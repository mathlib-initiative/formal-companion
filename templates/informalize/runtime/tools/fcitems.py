#!/usr/bin/env python3
"""The rules of items.json and declarations.json, and the keyword check against the library.

Part of formal-companion's informalize runtime (docs/proposals/informalize.md, sections 5.3,
5.4 and 8.3 rule 4, in the formal-companion skill); `cf informalize sync` copies it byte for
byte into an output repository's informal/tools/.  Moved from the skill's
`informalize.itemtable`, the port of the pilot's item verifier, so that the gate, which runs
on a host where the skill's own library cannot be imported, checks a table by the same rules
as `cf informalize check-items`; the skill imports them from here.

Two states of one table: a **provisional** one (the items task's: ids `<section>.<n>` with
gaps allowed) and a **numbered** one (after the first items approval: sections 1, 2, ... and
items 1, 2, ... within a section, in reading order, gaps only where a withdrawn id is recorded
in `decisions`).  `items_problems(doc, numbered=...)` holds a table to the rules of section
5.4; `declarations_problems(doc)` holds declarations.json to section 5.3; `check_table(...)`
adds every declaration of every item against declarations.json (name, module, keyword line)
and each key declaration's keyword line in the library's source (`keyword_problem`, read
through `SourceTree`, never through a link out of the tree).

The vocabulary (kinds, roles, relations, reference sources, the item id) is fccommon.py's;
the name, module and commit patterns are copies of the skill's `informalize.config` ones, and
tests/test_informalize_runtime.py pins each copy.  Standard library only.  Apache License 2.0.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fccommon as fc  # noqa: E402

ITEMS_SCHEMA = fc.ITEMS_SCHEMA
SCHEMA_VERSION = fc.SCHEMA_VERSION
EXACT_MATCH = "exactMatch"

TOP_KEYS = ("schema", "schema_version", "library", "reference_work", "sections", "items",
            "deferred", "decisions")
LIBRARY_KEYS = ("commit", "declarations_sha256")
SECTION_KEYS = ("number", "title", "slug")
ITEM_KEYS = ("id", "section", "kind", "title", "gist", "parts", "in_v1", "reference",
             "departures", "declarations")
PART_KEYS = ("letter", "gist", "reference")
REFERENCE_KEYS = ("number", "source", "declaration")
DECLARATION_KEYS = ("name", "module", "line", "part", "role", "relation", "junk", "hypotheses")
#: copies of the skill's informalize.config patterns
COMMIT_PATTERN = r"\A[0-9a-f]{40}\Z"
MODULE_PATTERN = r"\A[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\Z"
DECLARATION_PATTERN = r"\A[^\s.]+(?:\.[^\s.]+)*\Z"
#: a section's file is `sections/<NN>-<slug>.tex`, so a slug is a file-name word
SLUG_PATTERN = r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z"
SECTION_NUMBER_PATTERN = r"\A[1-9][0-9]*\Z"
SHA256_PATTERN = r"\A[0-9a-f]{64}\Z"
#: the keywords a key declaration's keyword line holds, for each kind the probe records
#: (templates/corpus/probe.lean.tmpl, `kindOf`): a `partial def` is an opaque constant, and
#: Mathlib's `irreducible_def` a def; an instance's line need not hold its name, since an
#: anonymous instance's generated name is not on it
KEYWORDS = {
    "theorem": ("theorem", "lemma"),
    "def": ("def", "irreducible_def"),
    "abbrev": ("abbrev", "def"),
    "opaque": ("opaque", "def"),
    "axiom": ("axiom",),
    "inductive": ("inductive",),
    "structure": ("structure",),
    "class": ("class",),
    "instance": ("instance",),
}
#: characters of a Lean identifier besides letters and digits, for a whole-word match
_IDENT = r"\w'!?₀-₉ₐ-ₜ"


def is_item_id(value: Any) -> bool:
    """Is ``value`` an item id (``1.2``, never ``01.2``, ``1.2(a)`` or ``1``)?"""
    return isinstance(value, str) and fc.ITEM_ID.match(value) is not None


def split_item_id(value: str) -> tuple[int, int]:
    """``"2.3"`` -> ``(2, 3)``; the caller has checked :func:`is_item_id`."""
    section, _, n = value.partition(".")
    return int(section), int(n)


def _extension(key: str) -> bool:
    return isinstance(key, str) and key.startswith("x_")


def _unknown(where: str, value: dict, allowed: Iterable[str]) -> list[str]:
    allowed = set(allowed)
    return [f"{where}: unknown key {k!r}" for k in value if k not in allowed and not _extension(k)]


def _line_text(value: Any, *, empty: bool = False) -> bool:
    return isinstance(value, str) and (empty or bool(value.strip())) and "\n" not in value


def section_file(section: dict) -> str:
    """`01-gluing-spaces`: the name of a section's file under informal/sections/."""
    return f"{int(section['number']):02d}-{section['slug']}"


def withdrawn_ids(doc: dict) -> set[str]:
    return {d["withdrawn"] for d in doc.get("decisions") or []
            if isinstance(d, dict) and isinstance(d.get("withdrawn"), str)}


# ---------------------------------------------------------------------------
# the rules of section 5.4

def reference_problems(where: str, ref: Any) -> list[str]:
    """An item's or a part's `reference`, the number the reference work prints it under --
    not declarations.json's `references` table, which is
    :func:`declaration_references_problems`."""
    if ref is None:
        return []
    if not isinstance(ref, dict):
        return [f"{where}: reference is not an object or null"]
    out = _unknown(f"{where} reference", ref, REFERENCE_KEYS)
    if not _line_text(ref.get("number")):
        out.append(f"{where}: reference.number is not one line of text")
    if ref.get("source") not in fc.REFERENCE_SOURCES:
        out.append(f"{where}: reference.source {ref.get('source')!r} is not one of "
                   f"{', '.join(fc.REFERENCE_SOURCES)}")
    declaration = ref.get("declaration")
    if declaration is not None and (not isinstance(declaration, str)
                                    or not re.match(DECLARATION_PATTERN, declaration)):
        out.append(f"{where}: reference.declaration {declaration!r} is not a declaration name")
    if ref.get("source") == "docstring" and declaration is None:
        out.append(f"{where}: a number from a docstring names the declaration it is in")
    return out


def item_problems(item: Any, section_numbers: set[str] | None) -> list[str]:
    """Every rule of section 5.4 one item breaks, on its own; `section_numbers=None` for a
    deferred item, whose section is the one of the numbering it was deferred from."""
    if not isinstance(item, dict):
        return [f"an item that is not an object: {item!r}"]
    iid = item.get("id")
    where = f"item {iid}"
    out = _unknown(where, item, ITEM_KEYS)
    out += [f"{where}: no {k!r}" for k in ITEM_KEYS if k not in item]
    if not is_item_id(iid):
        out.append(f"item {iid!r}: an id is <section>.<n>, both numbers from 1")
    elif item.get("section") != iid.split(".")[0]:
        out.append(f"{where}: its section is {item.get('section')!r}, not the one its id names")
    if section_numbers is not None and item.get("section") not in section_numbers:
        out.append(f"{where}: section {item.get('section')!r} is not in `sections`")
    if item.get("kind") not in fc.KIND_NAMES:
        out.append(f"{where}: kind {item.get('kind')!r} is not one of {', '.join(fc.KIND_NAMES)}")
    if not _line_text(item.get("title")):
        out.append(f"{where}: title is not one line of text")
    if not isinstance(item.get("gist"), str):
        out.append(f"{where}: gist is not text")
    if not isinstance(item.get("in_v1"), bool):
        out.append(f"{where}: in_v1 is not true or false")
    if not isinstance(item.get("departures"), str):
        out.append(f"{where}: departures is not text")
    out += reference_problems(where, item.get("reference"))
    parts = item.get("parts")
    letters: list[str] = []
    if not isinstance(parts, list):
        out.append(f"{where}: parts is not a list")
    else:
        for part in parts:
            if not isinstance(part, dict):
                out.append(f"{where}: a part that is not an object")
                continue
            out += _unknown(f"{where} part", part, PART_KEYS)
            letters.append(part.get("letter"))
            if not isinstance(part.get("gist"), str):
                out.append(f"{where}({part.get('letter')}): gist is not text")
            out += reference_problems(f"{where}({part.get('letter')})", part.get("reference"))
        if letters != [chr(ord("a") + n) for n in range(len(letters))]:
            out.append(f"{where}: parts are lettered a, b, ... in order, not {letters}")
    out += _declaration_problems(item, where, letters)
    return out


def _declaration_problems(item: dict, where: str, letters: list[str]) -> list[str]:
    declarations = item.get("declarations")
    if not isinstance(declarations, list):
        return [f"{where}: declarations is not a list"]
    out: list[str] = []
    names: set[str] = set()
    keys: list[dict] = []
    for decl in declarations:
        if not isinstance(decl, dict):
            out.append(f"{where}: a declaration that is not an object")
            continue
        name = decl.get("name")
        at = f"{where} {name}"
        out += _unknown(at, decl, DECLARATION_KEYS)
        out += [f"{at}: no {k!r}" for k in DECLARATION_KEYS if k not in decl]
        if not isinstance(name, str) or not re.match(DECLARATION_PATTERN, name):
            out.append(f"{where}: declaration name {name!r} is not a full name")
        elif name in names:
            out.append(f"{at}: listed twice in one item")
        names.add(name if isinstance(name, str) else "")
        if not isinstance(decl.get("module"), str) or not re.match(MODULE_PATTERN, decl["module"]):
            out.append(f"{at}: module {decl.get('module')!r} is not a module name")
        line = decl.get("line")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            out.append(f"{at}: line {line!r} is not a line number")
        role, relation, part = decl.get("role"), decl.get("relation"), decl.get("part")
        if role not in fc.ROLES:
            out.append(f"{at}: role {role!r} is not one of {', '.join(fc.ROLES)}")
        if relation not in fc.RELATIONS:
            out.append(f"{at}: relation {relation!r} is not one of {', '.join(fc.RELATIONS)}")
        if part is not None and part not in letters:
            out.append(f"{at}: part {part!r} is not a part of the item")
        if letters and role == "key" and part is None:
            out.append(f"{at}: a key declaration of an item with parts names its part")
        if role == "key" and relation in fc.RELATIONS and relation != EXACT_MATCH \
                and not str(item.get("departures") or "").strip():
            out.append(f"{at}: a key that is not {EXACT_MATCH} says why in departures")
        for field in ("junk", "hypotheses"):
            value = decl.get(field)
            if not isinstance(value, list) or not all(_line_text(v) for v in value):
                out.append(f"{at}: {field} is not a list of lines of text")
        if role == "key":
            keys.append(decl)
    if item.get("in_v1") is True:
        if not keys:
            out.append(f"{where}: no key declaration")
        for letter in letters:
            if not any(d.get("part") == letter for d in keys):
                out.append(f"{where}({letter}): no key declaration")
    return out


def items_problems(doc: Any, *, numbered: bool = True) -> list[str]:
    """Every rule of section 5.4 the table breaks; `numbered=False` for a provisional
    table, whose ids need not yet run from 1 without gaps."""
    if not isinstance(doc, dict):
        return ["items.json is not an object"]
    out = _unknown("items.json", doc, TOP_KEYS)
    out += [f"items.json: no {k!r}" for k in TOP_KEYS if k not in doc]
    if doc.get("schema") != ITEMS_SCHEMA:
        out.append(f"schema is not {ITEMS_SCHEMA}")
    if doc.get("schema_version") != SCHEMA_VERSION:
        out.append(f"schema_version is not {SCHEMA_VERSION}")
    library = doc.get("library")
    if not isinstance(library, dict):
        out.append("library is not an object")
    else:
        out += _unknown("library", library, LIBRARY_KEYS)
        if not isinstance(library.get("commit"), str) or not re.match(COMMIT_PATTERN, library["commit"]):
            out.append(f"library.commit {library.get('commit')!r} is not 40 hex")
        sha = library.get("declarations_sha256")
        if not isinstance(sha, str) or not re.match(SHA256_PATTERN, sha):
            out.append(f"library.declarations_sha256 {sha!r} is not a sha256")
    work = doc.get("reference_work")
    if work is not None and not _line_text(work):
        out.append("reference_work is not one line of text or null")
    for key in ("deferred", "decisions"):
        if not isinstance(doc.get(key), list):
            out.append(f"{key} is not a list")
    for entry in doc.get("deferred") if isinstance(doc.get("deferred"), list) else []:
        out += [f"deferred {p}" for p in item_problems(entry, None)]
    out += _section_problems(doc.get("sections"), numbered)
    numbers = {s.get("number") for s in doc.get("sections") or [] if isinstance(s, dict)}
    items = doc.get("items")
    if not isinstance(items, list):
        return out + ["items is not a list"]
    seen: set[str] = set()
    for item in items:
        out += item_problems(item, numbers)
        iid = item.get("id") if isinstance(item, dict) else None
        if isinstance(iid, str):
            if iid in seen:
                out.append(f"item {iid}: listed twice")
            seen.add(iid)
    out += _order_problems(doc, numbered)
    return out


def _section_problems(sections: Any, numbered: bool) -> list[str]:
    if not isinstance(sections, list):
        return ["sections is not a list"]
    out: list[str] = []
    numbers: list[int] = []
    slugs: set[str] = set()
    for sec in sections:
        if not isinstance(sec, dict):
            out.append(f"a section that is not an object: {sec!r}")
            continue
        out += _unknown(f"section {sec.get('number')}", sec, SECTION_KEYS)
        number = sec.get("number")
        if not isinstance(number, str) or not re.match(SECTION_NUMBER_PATTERN, number):
            out.append(f"section {number!r}: a number is a positive integer, as a string")
            continue
        numbers.append(int(number))
        if not _line_text(sec.get("title")):
            out.append(f"section {number}: title is not one line of text")
        slug = sec.get("slug")
        if not isinstance(slug, str) or not re.match(SLUG_PATTERN, slug):
            out.append(f"section {number}: slug {slug!r} is not lowercase words joined by -")
        elif slug in slugs:
            out.append(f"section {number}: slug {slug} is used twice")
        else:
            slugs.add(slug)
    if numbers != sorted(set(numbers)):
        out.append(f"sections are not in increasing order, each once: {numbers}")
    elif numbered and numbers != list(range(1, len(numbers) + 1)):
        out.append(f"sections are not numbered 1.. without gaps: {numbers}")
    return out


def _order_problems(doc: dict, numbered: bool) -> list[str]:
    """No withdrawn id is in use; numbered, the ids of a section rise in reading order and
    run from 1 without a gap but a withdrawn id (a provisional table's order is its list's)."""
    out: list[str] = []
    withdrawn = withdrawn_ids(doc)
    last: dict[int, int] = {}
    for item in doc.get("items") or []:
        iid = item.get("id") if isinstance(item, dict) else None
        if not is_item_id(iid):
            continue
        section, n = split_item_id(iid)
        if iid in withdrawn:
            out.append(f"item {iid}: a withdrawn id is never reused")
        previous = last.get(section, 0)
        if not numbered:
            continue
        if n <= previous:
            out.append(f"item {iid}: ids rise in reading order within a section")
        else:
            expected = previous + 1
            while f"{section}.{expected}" in withdrawn:
                expected += 1
            if n != expected:
                out.append(f"item {iid}: ids run from 1 without gaps within a section "
                           f"(expected {section}.{expected})")
        last[section] = max(previous, n)
    return out


# ---------------------------------------------------------------------------
# declarations.json, and the table against it and the library's source

def declarations_problems(doc: Any) -> list[str]:
    """Section 5.3's `declarations.json`: one entry per scope member, sorted by name, and the
    `references` the conventions mark their notions with
    (:func:`declaration_references_problems`)."""
    rows = doc.get("declarations") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return ["declarations.json: no `declarations` list"]
    out: list[str] = []
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(f"declarations.json: an entry that is not an object: {row!r}")
            continue
        name = row.get("name")
        at = f"declarations.json {name}"
        if not isinstance(name, str) or not re.match(DECLARATION_PATTERN, name):
            out.append(f"declarations.json: name {name!r} is not a full name")
            continue
        names.append(name)
        if not isinstance(row.get("module"), str) or not re.match(MODULE_PATTERN, row["module"]):
            out.append(f"{at}: module {row.get('module')!r} is not a module name")
        if source_path(row.get("path")) is None:
            out.append(f"{at}: path {row.get('path')!r} is not a relative .lean path")
        lines = [row.get(k) for k in ("start_line", "line", "end_line")]
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in lines):
            out.append(f"{at}: start_line, line and end_line are not line numbers")
        elif not lines[0] <= lines[1] <= lines[2]:
            out.append(f"{at}: line {lines[1]} is not within {lines[0]}..{lines[2]}")
        if row.get("kind") not in KEYWORDS:
            out.append(f"{at}: kind {row.get('kind')!r} is not one the probe records "
                       f"({', '.join(KEYWORDS)})")
        if not isinstance(row.get("signature"), str):
            out.append(f"{at}: signature is not text")
        if not isinstance(row.get("source_url", ""), str):
            out.append(f"{at}: source_url is not text")
    if len(set(names)) != len(names):
        twice = sorted({n for n in names if names.count(n) > 1})
        out.append(f"declarations.json: listed twice: {', '.join(twice[:5])}")
    if names != sorted(names):
        out.append("declarations.json: the entries are not sorted by name")
    return out + declaration_references_problems(doc)


def declaration_references_problems(doc: Any) -> list[str]:
    """Section 5.3's `references`, which an older `declarations.json` may not carry: the
    constants from outside the library that a member's type names, sorted by name, each with
    the module the dependency dump found it in, the URL of its documentation (empty for a
    library the Lean doc site does not hold) and, where the skill's own scope step wrote
    them, the dependency that holds it.  Only the name, the module and the URL are
    checked here: the rest is the exporter's (docs/proposals/informalize.md section 10)."""
    rows = doc.get("references") if isinstance(doc, dict) else None
    if rows is None:
        return []
    if not isinstance(rows, list):
        return ["declarations.json: `references` is not a list"]
    out: list[str] = []
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(f"declarations.json: a reference that is not an object: {row!r}")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not re.match(DECLARATION_PATTERN, name):
            out.append(f"declarations.json: reference name {name!r} is not a full name")
            continue
        names.append(name)
        at = f"declarations.json reference {name}"
        if not isinstance(row.get("module"), str) or not re.match(MODULE_PATTERN, row["module"]):
            out.append(f"{at}: module {row.get('module')!r} is not a module name")
        if not isinstance(row.get("doc_url", ""), str):
            out.append(f"{at}: doc_url is not text")
    if len(set(names)) != len(names):
        twice = sorted({n for n in names if names.count(n) > 1})
        out.append(f"declarations.json: a reference listed twice: {', '.join(twice[:5])}")
    if names != sorted(names):
        out.append("declarations.json: the references are not sorted by name")
    return out


def source_path(value: Any) -> PurePosixPath | None:
    """A declaration's `path` as a relative .lean path inside the library, or None."""
    if not isinstance(value, str) or "\\" in value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or str(pure) != value or pure.suffix != ".lean" \
            or any(part in ("", ".", "..") for part in pure.parts):
        return None
    return pure


class SourceTree:
    """The library's source tree (`library/src/`), read a file at a time, never through a
    link out of it."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lines: dict[str, list[str] | None] = {}

    def lines(self, relative: str) -> list[str] | None:
        if relative not in self._lines:
            self._lines[relative] = self._read(relative)
        return self._lines[relative]

    def _read(self, relative: str) -> list[str] | None:
        pure = source_path(relative)
        if pure is None:
            return None
        path = self.root.joinpath(*pure.parts)
        real, top = os.path.realpath(path), os.path.realpath(self.root)
        if not real.startswith(top + os.sep) or not os.path.isfile(real):
            return None
        return Path(real).read_text(encoding="utf-8", errors="replace").splitlines()


def _without_comments(text: str) -> str:
    """A source line with its comments blanked: the end of one opened on an earlier line,
    each `/- ... -/` and `/-- ... -/` on it, one left open, and a `--` comment."""
    if "-/" in text and ("/-" not in text or text.index("-/") < text.index("/-")):
        text = " " + text[text.index("-/") + 2:]
    text = re.sub(r"/-.*?-/", " ", text)
    text = re.sub(r"/-.*\Z", " ", text)
    return re.sub(r"--.*\Z", " ", text)


def keyword_problem(entry: dict, source: SourceTree) -> str | None:
    """Why `entry`'s keyword line in the library's source does not hold its keyword and
    its name's last component outside a comment, or None when it does."""
    name, kind, line = entry["name"], entry.get("kind"), entry.get("line")
    lines = source.lines(entry.get("path"))
    if lines is None:
        return f"{name}: {entry.get('path')} is not in library/src/"
    if not isinstance(line, int) or not 1 <= line <= len(lines):
        return f"{name}: {entry.get('path')} has no line {line}"
    text = lines[line - 1]
    code = _without_comments(text)
    words = KEYWORDS.get(kind)
    if words is None:
        return f"{name}: no keyword is known for kind {kind!r}"
    keyword = rf"(?<![{_IDENT}.])(?:{'|'.join(words)})(?![{_IDENT}])"
    if not re.search(keyword, code):
        return (f"{name}: line {line} of {entry['path']} holds no {' or '.join(words)} "
                f"keyword outside a comment: {text.strip()[:80]!r}")
    if kind != "instance":
        last = name.rsplit(".", 1)[-1]
        if not re.search(rf"(?<![{_IDENT}]){re.escape(last)}(?![{_IDENT}])", code):
            return f"{name}: line {line} of {entry['path']} does not name {last}"
    return None


def check_table(doc: Any, *, declarations: dict, declarations_sha256: str, source: SourceTree,
                library_commit: str, numbered: bool = True) -> list[str]:
    """`cf informalize check-items` and the gate's rule 4: the rules of section 5.4, then
    every declaration of every item against `declarations.json` (name, module, keyword
    line), and each key declaration's keyword line in the library's source (a helper may be
    one a command such as `@[simps]` generated, whose line is the generating declaration's)."""
    out = items_problems(doc, numbered=numbered)
    if not isinstance(doc, dict):
        return out
    rows = {d["name"]: d for d in declarations.get("declarations") or []
            if isinstance(d, dict) and isinstance(d.get("name"), str)}
    library = doc.get("library") if isinstance(doc.get("library"), dict) else {}
    if library.get("commit") != library_commit:
        out.append(f"library.commit {library.get('commit')!r} is not the library commit "
                   f"{library_commit} of informalize.yaml")
    if library.get("declarations_sha256") != declarations_sha256:
        out.append("library.declarations_sha256 is not the sha256 of declarations.json "
                   f"({declarations_sha256[:12]}): the table was drafted against another scope")
    checked: set[str] = set()
    for item in doc.get("items") or []:
        if not isinstance(item, dict):
            continue
        iid = item.get("id")
        refs = [item.get("reference")] + [p.get("reference") for p in item.get("parts") or []
                                          if isinstance(p, dict)]
        for ref in refs:
            named = ref.get("declaration") if isinstance(ref, dict) else None
            if isinstance(named, str) and named not in rows:
                out.append(f"item {iid}: reference.declaration {named} is not in declarations.json")
        for decl in item.get("declarations") or []:
            if not isinstance(decl, dict) or not isinstance(decl.get("name"), str):
                continue
            name = decl["name"]
            row = rows.get(name)
            if row is None:
                out.append(f"item {iid}: {name} is not in declarations.json")
                continue
            if decl.get("module") != row.get("module"):
                out.append(f"item {iid}: {name} is in module {row.get('module')}, not "
                           f"{decl.get('module')}")
            if decl.get("line") != row.get("line"):
                out.append(f"item {iid}: {name}'s keyword line is {row.get('line')}, not "
                           f"{decl.get('line')}")
            if decl.get("role") == "key" and name not in checked:
                checked.add(name)
                found = keyword_problem(row, source)
                if found:
                    out.append(f"item {iid}: {found}")
    return out
