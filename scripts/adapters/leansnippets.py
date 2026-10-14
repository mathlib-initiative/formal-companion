"""Statement snippets of the declarations an attribution table names, cut from the companion's Lean.

Ported from measure_theory's ``extract_declaration_snippets.py`` (``index_file``
and ``build``), which wrote them to ``declaration-snippets.json``; that file is
no longer read.  The tag of an ``example:<slug>`` id is found with the reader of
measure_theory's ``source_formalization_ledger.py`` (``code_line_starts``,
``declaration_body_end``, ``comment_ending_at``, ``example_tag`` and the example
branch of ``DeclarationIndex``).  Both are ported rule for rule, because the
snippets must stay byte-identical: ``adapters.leanparse`` tracks comment depth,
reads attributes and ends a body elsewhere, so it gives other text and lines.
Consumers: ``adapters.attribution_table`` (docs/design/stage_map.md Stage 2)
and ``annotations.export.attribution_table`` (docs/exporters.md section 1).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Callable

__all__ = ["SnippetError", "MAX_LINES", "MARKER", "index_text", "wanted", "cut", "reader"]

#: a snippet longer than this is cut to its first MAX_LINES lines plus "\n  " + MARKER
MAX_LINES = 14
MARKER = "…"

# -- extract_declaration_snippets.py ------------------------------------------
DECL_RE = re.compile(
    r"^(private\s+)?(protected\s+)?(noncomputable\s+)?"
    r"(theorem|lemma|def|abbrev|structure|class|instance|inductive)\s+([A-Za-z0-9_'.]+)"
)
NAMESPACE_RE = re.compile(r"^namespace\s+([A-Za-z0-9_'.]+)\s*$")
SECTION_RE = re.compile(r"^(noncomputable\s+)?section(\s+[A-Za-z0-9_'.]+)?\s*$")
END_RE = re.compile(r"^end(\s+[A-Za-z0-9_'.]+)?\s*$")
FIELD_RE = re.compile(r"^\s{2,}([A-Za-z0-9_']+)\s*[:({\[]")
STOP_RE = re.compile(r":=")
EXAMPLE_START_RE = re.compile(r"(?:noncomputable\s+)?example\b|/- `example:")

# -- source_formalization_ledger.py: the tag reader ------------------------------
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
            elif line[index : index + 3] == "'\"'":
                index += 3
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


def index_text(text: str) -> dict[str, tuple[int, str]]:
    """``{id: (1-based line, snippet)}`` for one Lean file: declarations, structure fields, tagged examples."""
    lines = text.split("\n")
    blocks: list[tuple[str, int]] = []
    stack: list[str] = []
    found: dict[str, tuple[int, str]] = {}
    # A tagged example: its statement, through the `:=`.
    for name, declarations in tagged_examples(lines).items():
        if len(declarations) == 1:
            start, body = declarations[0]
            block = []
            for line in body.split("\n"):
                block.append(line)
                if STOP_RE.search(line) or line.rstrip().endswith(" where"):
                    break
            snippet = "\n".join(block[:MAX_LINES]) + ("\n  " + MARKER if len(block) > MAX_LINES else "")
            found[name] = (start + 1, snippet)
    i = 0
    while i < len(lines):
        line = lines[i]
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
        if END_RE.match(line):
            if blocks:
                kind_popped, count = blocks.pop()
                if kind_popped == "namespace":
                    del stack[len(stack) - count :]
            i += 1
            continue
        decl = DECL_RE.match(line)
        if decl:
            keyword, name = decl.group(4), decl.group(5)
            if name.startswith("_root_."):
                full = name.removeprefix("_root_.")
            else:
                full = ".".join([*stack, *name.split(".")])
            block = [line]
            j = i + 1
            structural = keyword in {"structure", "class", "inductive"}
            while j < len(lines):
                last = block[-1]
                # A theorem's snippet is its statement: stop at the proof's `:=`. A definition's
                # body is the content, so it is kept (the breaks below and MAX_LINES bound it).
                if not structural and keyword in ("theorem", "lemma") and (
                    STOP_RE.search(last) or last.rstrip().endswith(" where")
                ):
                    break
                nxt = lines[j]
                if (
                    DECL_RE.match(nxt)
                    or NAMESPACE_RE.match(nxt)
                    or END_RE.match(nxt)
                    or SECTION_RE.match(nxt)
                    or nxt.startswith("/--")
                    or EXAMPLE_START_RE.match(nxt)
                ):
                    break
                if structural and nxt == "" and j + 1 < len(lines) and lines[j + 1] == "":
                    break
                block.append(nxt)
                j += 1
            while block and block[-1].strip() == "":
                block.pop()
            snippet = "\n".join(block)
            if len(block) > MAX_LINES:
                snippet = "\n".join(block[:MAX_LINES]) + "\n  " + MARKER
            found.setdefault(full, (i + 1, snippet))
            if structural:
                # an auto-generated projection sits at its field's line
                for k in range(i + 1, min(j, i + 200)):
                    field = FIELD_RE.match(lines[k])
                    if field and "/--" not in lines[k]:
                        field_snippet = f"structure {name} where\n  {MARKER}\n{lines[k]}"
                        found.setdefault(f"{full}.{field.group(1)}", (k + 1, field_snippet))
            i = j
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


def reader(root: Path) -> Callable[[str], str]:
    """Read a repo-relative file of the checkout at ``root``; anything outside it is a SnippetError."""
    base = root.resolve()

    def read(rel: str) -> str:
        pure = PurePosixPath(str(rel))
        path = base.joinpath(*pure.parts)
        if pure.is_absolute() or ".." in pure.parts or not path.resolve().is_relative_to(base):
            raise SnippetError(f"the ledger names {rel!r}, which is not a file inside the checkout")
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SnippetError(f"the ledger names {rel}, which cannot be read: {exc}") from exc
    return read
