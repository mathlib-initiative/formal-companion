"""ONE Lean 4 source parser for every docstring-tag companion.

Ported from the v1 campaign's two extractors (not in this repo, ``extract_ours``
and ``extract_teorth``) and hardened against the three parsers that live in the
companion repos themselves and disagree (1,248 vs 1,414 vs 1,413 declarations --
the measurement is in the design record, not in any document here):

  tao-measure-lean's ``review_audit.py``
      ``private``/``protected``/``noncomputable``/``@[...]`` prefixes and the
      ``code_lines`` guard against declaration keywords inside prose.
  tao-measure-lean's ``make_verso_tables.py``
      comment-depth tracking that survives ``/--`` and ``--``, the upward
      docstring attach that skips attribute lines, and the body rule.
  measure_theory's ``extract_declaration_snippets.py``
      namespace/section tracking for fully qualified names and def bodies.

Measured on tao-measure-lean HEAD this parser sees 1,544 declarations =
1,414 (make_verso_tables) + 130 ``private`` ones, and no others.

The parser is deliberately regex-and-state, not a Lean front end: it must run
in milliseconds over a repo with no Lean toolchain present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Decl",
    "DEFAULT_DECL_KINDS",
    "DEFINITION_KINDS",
    "strip_comments",
    "comment_depths",
    "parse_file",
    "signature",
]

#: Lean keywords that can open a declaration we care about.
DEFAULT_DECL_KINDS: tuple[str, ...] = (
    "theorem", "lemma", "def", "abbrev", "structure", "class",
    "inductive", "instance", "example", "opaque", "axiom",
)

#: Kinds whose *body* is content (the ``elementaryMeasure := volume`` proxy was
#: spotted from a body, adam.NOTES.md 16-19), so bodies are captured for these.
DEFINITION_KINDS: frozenset[str] = frozenset(
    {"def", "abbrev", "instance", "structure", "class", "inductive"}
)

_MODIFIERS = (
    "private", "protected", "noncomputable", "nonrec", "partial",
    "unsafe", "scoped", "local", "mutual",
)

_ATTR_LINE_RE = re.compile(r"^\s*@\[[^\]]*\]\s*$")
_OPEN_IN_RE = re.compile(r"^\s*(?:open|variable|set_option)\b.*\bin\s*$")
_NAMESPACE_RE = re.compile(r"^namespace\s+([A-Za-z_À-￿][\w'.À-￿]*)\s*$")
_SECTION_RE = re.compile(r"^(?:noncomputable\s+)?section(?:\s+[\w'.]+)?\s*$")
_END_RE = re.compile(r"^end(?:\s+[\w'.]+)?\s*$")
_SECTION_DOC_RE = re.compile(r"^\s*/-!")
_TOKEN_RE = re.compile(r"/-|-/|--")


def _decl_re(decl_kinds: tuple[str, ...] | list[str]) -> re.Pattern[str]:
    """Build the declaration-opening regex for a kind list.

    Handles any number of leading attribute groups and modifiers, so
    ``@[simp] private noncomputable def f`` parses like ``def f``.
    """
    kinds = "|".join(re.escape(k) for k in decl_kinds)
    mods = "|".join(_MODIFIERS)
    return re.compile(
        r"^(?:@\[[^\]]*\]\s*)*"
        rf"(?P<mods>(?:(?:{mods})\s+)*)"
        rf"(?P<kw>{kinds})\b"
        r"(?:[ \t]+(?P<name>[^\s({\[:`]+))?"
    )


@dataclass
class Decl:
    """One declaration in one file."""

    file: str                      # repo-relative path
    line: int                      # 1-based line of the declaration keyword
    kind: str                      # lean keyword: theorem | def | example | ...
    name: str | None               # None for an anonymous example/instance
    full_name: str                 # fully qualified, or the synthetic id
    namespace: str = ""            # enclosing namespace at the declaration
    doc: str | None = None         # docstring text, /-- and -/ removed
    doc_line: int | None = None    # 1-based line of the /-- opener
    body: str = ""                 # raw source from the keyword line to the next decl
    def_body: str | None = None    # comment-stripped, capped body; definitions only
    private: bool = False
    protected: bool = False
    anonymous: bool = False
    modifiers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_definition(self) -> bool:
        return self.kind in DEFINITION_KINDS


def strip_comments(src: str) -> str:
    """Remove nested ``/- -/`` blocks and ``--`` line comments.

    Byte-identical in behaviour to ``strip_comments`` in extract_ours.py and in
    make_verso_tables.py:103 (both were used to decide ``sorry``).
    """
    out: list[str] = []
    depth = 0
    i, n = 0, len(src)
    while i < n:
        two = src[i:i + 2]
        if two == "/-":
            depth += 1
            i += 2
            continue
        if two == "-/" and depth:
            depth -= 1
            i += 2
            continue
        if depth:
            i += 1
            continue
        if two == "--":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def comment_depths(lines: list[str]) -> list[int]:
    """Comment nesting depth at the *start* of each line.

    A declaration keyword only opens a declaration at depth 0; this is what
    keeps prose such as "``example`` and structures" out of the table
    (review_audit.py ``code_lines``, make_verso_tables.py:170-186).
    """
    depths: list[int] = []
    depth = 0
    for line in lines:
        depths.append(depth)
        for tok in _TOKEN_RE.findall(line):
            if tok == "--":
                if depth == 0:
                    break          # a line comment hides the rest of the line
            elif tok == "/-":
                depth += 1
            elif depth > 0:
                depth -= 1
    return depths


def _attach_doc(lines: list[str], decl_line: int) -> tuple[int | None, str | None]:
    """The ``/-- ... -/`` block ending immediately above ``decl_line``.

    Attribute lines and ``open ... in`` lines between the docstring and the
    declaration are skipped (make_verso_tables.py:198-201, extended with the
    ``open ... in`` form used in tao-measure-lean).
    """
    j = decl_line - 1
    while j >= 0 and (_ATTR_LINE_RE.match(lines[j]) or _OPEN_IN_RE.match(lines[j])
                      or lines[j].strip() == ""):
        j -= 1
    if j < 0 or not lines[j].rstrip().endswith("-/"):
        return None, None
    depth, k, opener = 0, j, None
    while k >= 0:
        for tok in reversed(re.findall(r"/-+!?-?|-/", lines[k])):
            if tok == "-/":
                depth += 1
            elif tok.startswith("/-"):
                depth -= 1
                if depth == 0:
                    opener = (k, tok)
                    break
        if opener:
            break
        k -= 1
    if not opener or not opener[1].startswith("/--"):
        return None, None
    start = opener[0]
    text = "\n".join(lines[start:j + 1])
    text = re.sub(r"^\s*/--", "", text)
    text = re.sub(r"-/\s*$", "", text)
    return start + 1, text.strip()


def signature(body: str) -> str:
    """The declaration's statement: everything before ``:=`` or ``by``.

    Verbatim rule from extract_ours.py ``sig()`` — the 1,157-row baseline was
    produced with it, so changing it would move every statement string.
    """
    s = strip_comments(body)
    s = re.split(r":=|\bby\b|\bwhere\b", s, maxsplit=1)[0]
    return re.sub(r"\s+", " ", s).strip()


def _body_text(body: str, max_lines: int) -> str:
    """Comment-stripped body, capped at ``max_lines`` lines."""
    stripped = strip_comments(body).rstrip()
    lines = [ln for ln in stripped.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines] + ["  …"]
    return "\n".join(lines)


def parse_file(
    path: str | Path,
    rel: str | None = None,
    decl_kinds: tuple[str, ...] | list[str] = DEFAULT_DECL_KINDS,
    max_body_lines: int = 40,
) -> list[Decl]:
    """Parse one Lean file into ``Decl`` records, in file order.

    ``rel`` is the repo-relative path recorded on every declaration (and used
    for the synthetic ``example@<file>:<line>`` ids).
    """
    p = Path(path)
    rel = rel if rel is not None else p.name
    text = p.read_text(encoding="utf-8")
    lines = text.split("\n")
    depths = comment_depths(lines)
    decl_re = _decl_re(tuple(decl_kinds))

    starts: list[tuple[int, re.Match[str]]] = []
    ns_stack: list[str] = []
    blocks: list[tuple[str, int]] = []
    ns_at_line: dict[int, str] = {}

    for i, line in enumerate(lines):
        if depths[i] != 0:
            continue
        ns = _NAMESPACE_RE.match(line)
        if ns:
            parts = ns.group(1).split(".")
            ns_stack.extend(parts)
            blocks.append(("namespace", len(parts)))
            continue
        if _SECTION_RE.match(line):
            blocks.append(("section", 0))
            continue
        if _END_RE.match(line):
            if blocks:
                kind_popped, count = blocks.pop()
                if kind_popped == "namespace" and count:
                    del ns_stack[len(ns_stack) - count:]
            continue
        m = decl_re.match(line)
        if m:
            starts.append((i, m))
            ns_at_line[i] = ".".join(ns_stack)

    decls: list[Decl] = []
    for k, (i, m) in enumerate(starts):
        doc_line, doc = _attach_doc(lines, i)
        # The body runs to the next declaration's docstring (or keyword line),
        # stopping early at a namespace/section boundary or a /-! section
        # header so that a following untagged declaration's `sorry` is never
        # attributed here.
        end = len(lines)
        if k + 1 < len(starts):
            nxt_i, _ = starts[k + 1]
            nxt_doc, _unused = _attach_doc(lines, nxt_i)
            end = (nxt_doc - 1) if nxt_doc is not None else nxt_i
        for j in range(i + 1, end):
            if depths[j] == 0 and (_END_RE.match(lines[j]) or _NAMESPACE_RE.match(lines[j])
                                   or _SECTION_DOC_RE.match(lines[j])):
                end = j
                break
        body = "\n".join(lines[i:end])

        mods = tuple(w for w in m.group("mods").split()) if m.group("mods") else ()
        name = m.group("name")
        namespace = ns_at_line.get(i, "")
        anonymous = name is None
        if anonymous:
            full = f"{m.group('kw')}@{rel}:{i + 1}"
        elif name.startswith("_root_."):
            full = name[len("_root_."):]
        else:
            full = ".".join([x for x in (namespace, name) if x])

        is_def = m.group("kw") in DEFINITION_KINDS
        decls.append(Decl(
            file=rel, line=i + 1, kind=m.group("kw"), name=name, full_name=full,
            namespace=namespace, doc=doc, doc_line=doc_line, body=body,
            def_body=_body_text(body, max_body_lines) if is_def else None,
            private="private" in mods, protected="protected" in mods,
            anonymous=anonymous, modifiers=mods,
        ))
    return decls
