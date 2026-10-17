"""Statement snippets of the declarations an attribution table names, cut from the companion's Lean.

Ported from ``extract_declaration_snippets.py`` at measure_theory ef0a424, the
last commit that has it (``index_file`` and ``build``), which wrote them to
``declaration-snippets.json``; that file is no longer read.  The tag of an
``example:<slug>`` id is found with the reader of ``source_formalization_ledger.py``
at measure_theory ef0a424 (``code_line_starts``, ``declaration_body_end``,
``comment_ending_at``, ``example_tag`` and the example branch of
``DeclarationIndex``).  Both are ported rule for rule, except where a snippet
ends: a theorem's is still its statement through the proof's ``:=``, now the
first one outside brackets, comments and strings that closes no ``let`` or
``have`` (:func:`proof_start`), but a definition (:data:`DEFINITION_KEYWORDS`)
is shown whole, body, fields and constructors included, up to the next command
(:func:`declaration_end`, which bounds a theorem's statement too), a field's
snippet keeps the lines that continue its type, and a declaration is read only
outside comments and with any attributes or instance priority on its line
(docs/exporters.md section 1); ``code_line_starts`` also reads the escaped
character ``'\\"'`` as one character, not as a string's start.
``adapters.leanparse`` ends a body elsewhere, so it gives other text and lines.
Consumers: ``adapters.attribution_table`` (docs/design/stage_map.md Stage 2)
and ``annotations.export.attribution_table`` (docs/exporters.md section 1).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Callable, Collection

__all__ = ["SnippetError", "MAX_LINES", "DEFINITION_MAX_LINES", "MARKER", "DEFINITION_KEYWORDS",
           "RETIRED_SNIPPETS", "declaration_end", "definition_text", "proof_start", "statement_text", "index_text",
           "wanted", "cut", "reader"]

#: a statement longer than this is cut to its first MAX_LINES lines plus "\n  " + MARKER
MAX_LINES = 14
#: a definition longer than this is cut the same way, at this many lines
DEFINITION_MAX_LINES = 60
MARKER = "…"
#: The keywords whose declaration is its own content: its snippet is the whole declaration.
DEFINITION_KEYWORDS = frozenset({"def", "abbrev", "instance", "structure", "class", "inductive", "opaque"})
#: The file the companion once shipped the snippets in; not read, and named when a checkout still has it.
RETIRED_SNIPPETS = "declaration-snippets.json"

# -- extract_declaration_snippets.py at measure_theory ef0a424 ---------------
# (it read only the three modifiers in this order, no attribute, no `opaque` and no instance priority)
DECL_RE = re.compile(
    r"^(?:@\[(?:[^\[\]\"\n]|\[[^\[\]\n]*\]|\"(?:[^\"\\\n]|\\.)*\")*\]\s*)*"
    r"(?:(?:private|protected|public|noncomputable|nonrec|unsafe|partial|local|scoped|meta)\s+)*"
    r"(?P<kw>class\s+inductive|theorem|lemma|def|abbrev|structure|class|instance|inductive|opaque)"
    r"(?:\s+\(priority\s*:=[^)\n]*\))?"
    r"\s+(?P<name>[A-Za-z0-9_'.]+)"
)
MUTUAL_RE = re.compile(r"^mutual\s*$")
NAMESPACE_RE = re.compile(r"^namespace\s+([A-Za-z0-9_'.]+)\s*$")
SECTION_RE = re.compile(r"^(noncomputable\s+)?section(\s+[A-Za-z0-9_'.]+)?\s*$")
END_RE = re.compile(r"^end(\s+[A-Za-z0-9_'.]+)?\s*$")
FIELD_RE = re.compile(r"^\s{2,}([A-Za-z0-9_']+)\s*[:({\[]")
#: brackets: a ':=' inside them is a named argument's, a structure instance's or an attribute's
OPENING, CLOSING = "([{⟨⦃⟦", ")]}⟩⦄⟧"
#: words that bring a ':=' of their own into a statement
LOCAL_BINDERS = frozenset({"let", "have", "haveI", "letI"})
#: what an identifier is made of, after its first letter
NAME_CHARS = "_'!?"
#: what a command's first line starts with, once its comments are left out: a word, `@[` or `#`
COMMAND_START_RE = re.compile(r"[A-Za-z_@#]")
#: what surely starts a command, even inside a body at the declaration's indentation: an attribute,
#: a `#` command, or a command keyword after any modifiers
COMMAND_RE = re.compile(
    r"@\[|#[a-z]|(?:(?:private|protected|public|noncomputable|nonrec|unsafe|partial|local|scoped(?:\[[^\]\n]*\])?"
    r"|meta)\s+)*"
    r"(?:theorem|lemma|def|abbrev|structure|class|instance|inductive|opaque|axiom|example|mutual|end|namespace|"
    r"section|open|export|variable|universe|include|omit|set_option|attribute|deriving\s+instance|notation|"
    r"infix[lr]?|prefix|postfix|macro(?:_rules)?|syntax|elab(?:_rules)?|initialize|alias)\b"
)
#: what continues a declaration at its own indentation
CONTINUATION_RE = re.compile(r"(?:where|termination_by|decreasing_by|deriving(?!\s+instance))\b")
#: what a code line ends in when the body after it may start at the declaration's indentation
OPENER_RE = re.compile(r"(?::=|=>|\b(?:do|by|where))$")

# -- source_formalization_ledger.py at measure_theory ef0a424: the tag reader ----
EXAMPLE_HEADER_RE = re.compile(
    r"(?:@\[[^\]\n]*\]\s*)?(?:(?:private|protected|noncomputable|nonrec|unsafe|partial)\s+)*"
    r"example(?![^\s:({\[⦃])"
)
HEAD_PREFIX_RE = re.compile(
    r"(?:@\[.*\]|(?:open|set_option|attribute|include|omit|variable)\b.*\bin)\s*$"
)
EXAMPLE_TAG_RE = re.compile(
    r"`(example:(?![0-9a-f]{12}(?:~[0-9]+)?(?![A-Za-z0-9._~-]))[A-Za-z0-9][A-Za-z0-9._-]*)`"
)
BODY_CONTINUATION_RE = re.compile(r"(?:\||where\b|termination_by\b|decreasing_by\b|deriving\b)")


class SnippetError(Exception):
    """A file the ledger names that cannot be read from the checkout."""


def code_line_starts(lines: list[str]) -> list[bool]:
    """For each line, whether it starts outside block comments and string literals."""
    result: list[bool] = []
    depth = 0
    in_string = False
    for line in lines:
        result.append(depth == 0 and not in_string)
        index = 0
        while index < len(line):
            pair = line[index : index + 2]
            if in_string:
                if line[index] == "\\":
                    index += 2
                    continue
                if line[index] == '"':
                    in_string = False
                index += 1
            elif depth > 0:
                if pair == "/-":
                    depth += 1
                    index += 2
                elif pair == "-/":
                    depth -= 1
                    index += 2
                else:
                    index += 1
            elif pair == "--":
                break
            elif pair == "/-":
                depth += 1
                index += 2
            elif line[index : index + 3] == "'\"'" or line[index : index + 4] == "'\\\"'":
                index += 3 if line[index + 1] == '"' else 4
            else:
                if line[index] == '"':
                    in_string = True
                index += 1
    return result


def declaration_body_end(lines: list[str], start: int) -> int:
    """Exclusive end of the declaration whose header is lines[start]."""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and line[0] not in " \t" and not BODY_CONTINUATION_RE.match(line):
            break
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def comment_ending_at(lines: list[str], code: list[bool], index: int) -> tuple[int, str] | None:
    """(first line, body) of the comment that starts its own line and ends lines[index]."""
    stripped = lines[index].strip()
    if code[index] and stripped.startswith("--"):
        return index, stripped[2:].strip()
    if not stripped.endswith("-/"):
        return None
    first = index
    while first > 0 and not code[first]:
        first -= 1
    text = "\n".join(lines[first : index + 1]).strip()
    if not text.startswith("/-"):
        return None
    body = text[2:-2]
    return first, (body[1:] if body[:1] in ("-", "!") else body).strip()


def example_tag(lines: list[str], code: list[bool], start: int) -> str | None:
    """The declared id of the example whose keyword line is lines[start], or None.

    The head starts at its first attribute or `... in` prefix line. A tag comment starts its own
    line above the head (blank lines and other comments may come between) and its body is exactly
    one backticked `example:<slug>`; two distinct tags declare nothing.
    """
    head, index = start, start - 1
    while index >= 0:
        if code[index] and HEAD_PREFIX_RE.match(lines[index]):
            head = index
        elif lines[index].strip():
            comment = comment_ending_at(lines, code, index)
            if comment is None:
                break
            index = comment[0]
        index -= 1
    tags: set[str] = set()
    index = head - 1
    while index >= 0:
        if not lines[index].strip():
            index -= 1
            continue
        comment = comment_ending_at(lines, code, index)
        if comment is None:
            break
        match = EXAMPLE_TAG_RE.fullmatch(comment[1])
        if match:
            tags.add(match.group(1))
        index = comment[0] - 1
    return next(iter(tags)) if len(tags) == 1 else None


def tagged_examples(lines: list[str]) -> dict[str, list[tuple[int, str]]]:
    """``{example:<slug>: [(0-based keyword line, digest text)]}``: DeclarationIndex's example branch.

    Its namespace, section, end and named-declaration branches are left out: none of them
    matches a line EXAMPLE_HEADER_RE matches, so they never decide a tag.
    """
    code = code_line_starts(lines)
    found: dict[str, list[tuple[int, str]]] = {}
    for index, line in enumerate(lines):
        if not code[index] or not EXAMPLE_HEADER_RE.match(line):
            continue
        tag = example_tag(lines, code, index)
        if tag is not None:
            end = declaration_body_end(lines, index)
            found.setdefault(tag, []).append((index, "\n".join(lines[index:end])))
    return found


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _code(line: str) -> str:
    """``line``, which starts outside comments and strings, without its comments, and each string
    or ``'"'`` in it cut to its quotes, so that a ``--``, ``/-`` or ``:=`` in one is not read."""
    out: list[str] = []
    depth = at = 0
    while at < len(line):
        pair = line[at:at + 2]
        if depth:
            depth += 1 if pair == "/-" else -1 if pair == "-/" else 0
            at += 2 if pair in ("/-", "-/") else 1
        elif pair == "--":
            break
        elif pair == "/-":
            depth, at = 1, at + 2
        elif line[at:at + 3] == "'\"'" or line[at:at + 4] == "'\\\"'":
            out.append("''")
            at += 3 if line[at + 1] == '"' else 4
        elif line[at] == '"':
            close = at + 1
            while close < len(line) and line[close] != '"':
                close += 2 if line[close] == "\\" else 1
            out.append('""')
            at = close + 1
        else:
            out.append(line[at])
            at += 1
    return "".join(out)


def declaration_end(lines: list[str], start: int, code: list[bool] | None = None) -> int:
    """Exclusive end of the declaration whose keyword line is lines[start]: where the next command starts.

    That is the first later line that starts outside comments and strings, no deeper than
    lines[start], and, its comments left out, opens a command: it starts with what
    :data:`COMMAND_RE` reads, or with any other word (a library's own command) that is not a
    ``where``, ``deriving``, ``termination_by`` or ``decreasing_by`` of this one while the body
    is not open at that depth. The body opens there when such a word line comes right after a
    code line that ends in ``:=``, ``=>``, ``do``, ``by`` or ``where``, with comments but no blank
    line between, as a ``do`` block, a term or a constructor ``mk ::`` at column 0 does, and it
    stays open. Comment lines decide nothing, so a comment at the declaration's indentation is
    inside it when the next code line is; the comments no deeper than lines[start] just above
    the next command, and the blank lines before it, are not the declaration's. ``code`` is
    :func:`code_line_starts` of ``lines``.
    """
    code = code_line_starts(lines) if code is None else code
    indent = _indent(lines[start])
    end, cut, last, flat = start + 1, None, _code(lines[start]).rstrip(), False
    while end < len(lines):
        line = lines[end]
        head = _code(line).strip() if code[end] else ""
        word = _indent(line) <= indent and COMMAND_START_RE.match(head) and not CONTINUATION_RE.match(head)
        if not line.strip():
            last = ""
        elif not head:
            if cut is None and code[end] and _indent(line) <= indent:
                cut = end
        elif word and (COMMAND_RE.match(head) or not (flat or OPENER_RE.search(last))):
            break
        else:
            flat, cut, last = flat or bool(word), None, head
        end += 1
    end = end if cut is None else cut
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _capped(block: list[str], limit: int) -> str:
    while block and not block[-1].strip():
        block = block[:-1]
    if len(block) > limit:
        return "\n".join(block[:limit]) + "\n  " + MARKER
    return "\n".join(block)


def definition_text(lines: list[str], start: int, code: list[bool] | None = None) -> str:
    """The definition whose keyword line is lines[start], whole: through :func:`declaration_end`,
    cut past DEFINITION_MAX_LINES lines and marked."""
    return _capped(lines[start:declaration_end(lines, start, code)], DEFINITION_MAX_LINES)


def proof_start(lines: list[str], start: int, end: int) -> tuple[int, int] | None:
    """(line, column) of the ``:=`` that starts the proof of the declaration in lines[start:end], or None.

    That is the first ``:=`` outside comments, strings and brackets (a named argument
    ``(n := 1)``, a structure instance ``{ x := 1 }`` and an attribute ``@[simp (since := ...)]``
    each have their own) that does not close a ``let`` or ``have`` of the statement; a ``←``
    (``let x ← e`` in a ``do``) or the first ``=>`` outside brackets on a line that starts with
    ``|`` (``let rec f : T`` given by alternatives, not an absolute value) closes one too.
    """
    depth = comment = pending = 0
    in_string = False
    for row in range(start, min(end, len(lines))):
        line = lines[row]
        alternative = line.lstrip().startswith("|")
        at = 0
        while at < len(line):
            char, pair = line[at], line[at:at + 2]
            if in_string:
                if char == "\\":
                    at += 2
                    continue
                in_string = char != '"'
                at += 1
            elif comment:
                comment += 1 if pair == "/-" else -1 if pair == "-/" else 0
                at += 2 if pair in ("/-", "-/") else 1
            elif pair == "--":
                break
            elif pair == "/-":
                comment, at = 1, at + 2
            elif char == "'" and (at == 0 or not (line[at - 1].isalnum() or line[at - 1] in NAME_CHARS)):
                # a character literal: 'x' or '\n'
                at += 4 if line[at + 1:at + 2] == "\\" and line[at + 3:at + 4] == "'" \
                    else 3 if line[at + 2:at + 3] == "'" else 1
            elif char == '"':
                in_string, at = True, at + 1
            elif char in OPENING:
                depth, at = depth + 1, at + 1
            elif char in CLOSING:
                depth, at = max(0, depth - 1), at + 1
            elif pair == ":=":
                if depth == 0 and not pending:
                    return row, at
                pending -= 1 if depth == 0 else 0
                at += 2
            elif depth == 0 and pending and (char == "←" or alternative and pair == "=>"):
                # a binder closed without a `:=`: `let x ← e`, or `let rec f : T` and its `| ... =>` lines
                pending, at = pending - 1, at + (2 if pair == "=>" else 1)
            elif char.isalpha() or char == "_":
                stop = at + 1
                while stop < len(line) and (line[stop].isalnum() or line[stop] in NAME_CHARS):
                    stop += 1
                pending += 1 if depth == 0 and line[at:stop] in LOCAL_BINDERS else 0
                at = stop
            else:
                at += 1
    return None


def statement_text(lines: list[str], start: int, end: int) -> str:
    """A theorem's statement: its lines through the one that starts its proof (:func:`proof_start`), or
    that ends in `` where``, within lines[start:end], cut past MAX_LINES lines and marked."""
    proof = proof_start(lines, start, end)
    block: list[str] = []
    for line in lines[start:proof[0] + 1 if proof else end]:
        block.append(line)
        if line.rstrip().endswith(" where"):
            break
    return _capped(block, MAX_LINES)


def field_text(structure: str, lines: list[str], at: int, end: int) -> str:
    """A field's snippet: its structure's name, a marker, and the field's lines, the deeper indented
    lines that continue its type included, within lines[:end]; cut like a definition."""
    stop = at + 1
    while stop < end and lines[stop].strip() and _indent(lines[stop]) > _indent(lines[at]):
        stop += 1
    return _capped([f"structure {structure} where", f"  {MARKER}", *lines[at:stop]], DEFINITION_MAX_LINES)


def index_text(text: str) -> dict[str, tuple[int, str]]:
    """``{id: (1-based line, snippet)}`` for one Lean file: declarations, structure fields, tagged examples.

    A definition's snippet is :func:`definition_text`, a theorem's :func:`statement_text`, and a
    tagged example's its statement through the ``:=`` as the tag reader ends its body.
    """
    lines = text.split("\n")
    code = code_line_starts(lines)
    blocks: list[tuple[str, int]] = []
    stack: list[str] = []
    found: dict[str, tuple[int, str]] = {}
    for name, declarations in tagged_examples(lines).items():
        if len(declarations) == 1:
            start, body = declarations[0]
            body_lines = body.split("\n")
            found[name] = (start + 1, statement_text(body_lines, 0, len(body_lines)))
    i = 0
    while i < len(lines):
        line = lines[i]
        if not code[i]:
            i += 1
            continue
        ns = NAMESPACE_RE.match(line)
        if ns:
            components = ns.group(1).split(".")
            stack.extend(components)
            blocks.append(("namespace", len(components)))
            i += 1
            continue
        if SECTION_RE.match(line):
            blocks.append(("section", 0))
            i += 1
            continue
        if MUTUAL_RE.match(line):
            blocks.append(("mutual", 0))
            i += 1
            continue
        if END_RE.match(line):
            if blocks:
                kind_popped, count = blocks.pop()
                if kind_popped == "namespace":
                    del stack[len(stack) - count :]
            i += 1
            continue
        decl = DECL_RE.match(line)
        if decl:
            keyword, name = decl.group("kw").split()[0], decl.group("name")
            if name.startswith("_root_."):
                full = name.removeprefix("_root_.")
            else:
                full = ".".join([*stack, *name.split(".")])
            end = declaration_end(lines, i, code)
            if keyword in DEFINITION_KEYWORDS:
                snippet = definition_text(lines, i, code)
            else:
                snippet = statement_text(lines, i, end)
            found.setdefault(full, (i + 1, snippet))
            if keyword in {"structure", "class", "inductive"}:
                # an auto-generated projection sits at its field's line
                for k in range(i + 1, min(end, i + 200)):
                    field = FIELD_RE.match(lines[k])
                    if field and code[k] and "/--" not in lines[k]:
                        found.setdefault(f"{full}.{field.group(1)}", (k + 1, field_text(name, lines, k, end)))
            i = end
            continue
        i += 1
    return found


def wanted(refinement: dict, ledger: dict) -> list[tuple[str, str | None]]:
    """``(id, file)`` for every id the refinement lists, in id order; the file is the ledger node's."""
    files = {n["id"]: n.get("file") for n in ledger.get("formalization_nodes") or [] if n.get("id")}
    ids = {i for e in refinement.get("entries") or [] for i in e.get("formalization_ids") or []}
    return [(i, files.get(i)) for i in sorted(ids)]


def cut(refinement: dict, ledger: dict, read: Callable[[str], str]) -> tuple[dict[str, dict], list[str]]:
    """``({id: {file, line, text}}, sorted ids not located)`` for every id the refinement lists.

    An id is looked for in the file the ledger's formalization node names; an id with no
    node, no file, or no declaration there is not located, and has no snippet.
    """
    indexed: dict[str, dict[str, tuple[int, str]]] = {}
    snippets: dict[str, dict] = {}
    missing: list[str] = []
    for ident, file in wanted(refinement, ledger):
        if file is None:
            missing.append(ident)
            continue
        if file not in indexed:
            indexed[file] = index_text(read(file))
        hit = indexed[file].get(ident)
        if hit is None:
            missing.append(ident)
            continue
        line, text = hit
        snippets[ident] = {"file": file, "line": line, "text": text}
    return snippets, sorted(missing)


def reader(root: Path, files: Collection[str] | None = None) -> Callable[[str], str]:
    """Read a repo-relative file of the checkout at ``root``; anything outside it is a SnippetError.

    With ``files`` (the checkout's tracked Lean files, repo-relative), a path not in it, or a
    symlink to one not in it, is a SnippetError too.
    """
    base = root.resolve()
    allowed = None if files is None else frozenset(files)

    def read(rel: str) -> str:
        pure = PurePosixPath(str(rel))
        path = base.joinpath(*pure.parts)
        target = path.resolve()
        if pure.is_absolute() or ".." in pure.parts or not target.is_relative_to(base):
            raise SnippetError(f"the ledger names {rel!r}, which is not a file inside the checkout")
        if allowed is not None and not {str(pure), target.relative_to(base).as_posix()} <= allowed:
            raise SnippetError(f"the ledger names {rel!r}, which is not a tracked Lean file of the checkout")
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SnippetError(f"the ledger names {rel}, which cannot be read: {exc}") from exc
    return read
