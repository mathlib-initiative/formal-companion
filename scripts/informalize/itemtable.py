"""The item table, `informal/items.json`: its rules, `check-items`, renumbering and the
reference checklist.

New code, for docs/proposals/informalize.md section 5.4; `check_table` is a port of the
pilot's item verifier (each key declaration's name, module and keyword line against the
library dump and its source), made generic: the declarations come from
`declarations.json` (section 5.3), the source from `library/src/`, and the keywords of
each probe kind from :data:`KEYWORDS`.

Two states of one table.  The items task drafts **provisional** ids (`<section>.<n>`,
numbered as it likes, gaps allowed); `cf informalize approve <id> items` renumbers them
(:func:`renumber`) while every section file is empty, and from then on the table is
**numbered**: sections run 1, 2, ... and items 1, 2, ... within a section, in reading
order, the gaps only where a withdrawn id is recorded in `decisions`.  Once a section file
is not empty the ids are frozen (:func:`frozen_problems`): a printed number is an id, and
an annotation's id derives from the kind and the id.

`templates/informalize/schema/items.schema.json` states the shape as a JSON Schema for a
reader; this module is the implementation, and tests/test_informalize_cli.py holds the two
to one set of keys and enums.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from cflib.hashing import sha256_json
from cflib.items import is_informal_item_id, split_informal_item_id
from cflib.vocab import (EXACT_MATCH, INFORMALIZE_KINDS, INFORMALIZE_RELATIONS,
                         INFORMALIZE_ROLES, REFERENCE_SOURCES)

from informalize.config import COMMIT_PATTERN, DECLARATION_PATTERN, MODULE_PATTERN

ITEMS_SCHEMA = "formal-companion/informalize-items"
SCHEMA_VERSION = 1

TOP_KEYS = ("schema", "schema_version", "library", "reference_work", "sections", "items",
            "deferred", "decisions")
LIBRARY_KEYS = ("commit", "declarations_sha256")
SECTION_KEYS = ("number", "title", "slug")
ITEM_KEYS = ("id", "section", "kind", "title", "gist", "parts", "in_v1", "reference",
             "departures", "declarations")
PART_KEYS = ("letter", "gist", "reference")
REFERENCE_KEYS = ("number", "source", "declaration")
DECLARATION_KEYS = ("name", "module", "line", "part", "role", "relation", "junk", "hypotheses")
#: a section's file is `sections/<NN>-<slug>.tex`, so a slug is a file-name word
SLUG_PATTERN = r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z"
SECTION_NUMBER_PATTERN = r"\A[1-9][0-9]*\Z"
SHA256_PATTERN = r"\A[0-9a-f]{64}\Z"
#: the keywords a declaration's keyword line holds, for each kind the probe records
#: (templates/corpus/probe.lean.tmpl, `kindOf`); an instance's line need not hold its
#: name, since an anonymous instance's generated name is not on it
KEYWORDS = {
    "theorem": ("theorem", "lemma"),
    "def": ("def",),
    "abbrev": ("abbrev", "def"),
    "opaque": ("opaque",),
    "axiom": ("axiom",),
    "inductive": ("inductive",),
    "structure": ("structure",),
    "class": ("class",),
    "instance": ("instance",),
}
#: characters of a Lean identifier besides letters and digits, for a whole-word match
_IDENT = r"\w'!?₀-₉ₐ-ₜ"


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
# ---------------------------------------------------------------------------

def reference_problems(where: str, ref: Any) -> list[str]:
    if ref is None:
        return []
    if not isinstance(ref, dict):
        return [f"{where}: reference is not an object or null"]
    out = _unknown(f"{where} reference", ref, REFERENCE_KEYS)
    if not _line_text(ref.get("number")):
        out.append(f"{where}: reference.number is not one line of text")
    if ref.get("source") not in REFERENCE_SOURCES:
        out.append(f"{where}: reference.source {ref.get('source')!r} is not one of "
                   f"{', '.join(REFERENCE_SOURCES)}")
    declaration = ref.get("declaration")
    if declaration is not None and (not isinstance(declaration, str)
                                    or not re.match(DECLARATION_PATTERN, declaration)):
        out.append(f"{where}: reference.declaration {declaration!r} is not a declaration name")
    if ref.get("source") == "docstring" and declaration is None:
        out.append(f"{where}: a number from a docstring names the declaration it is in")
    return out


def item_problems(item: Any, section_numbers: set[str]) -> list[str]:
    """Every rule of section 5.4 one item breaks, on its own."""
    if not isinstance(item, dict):
        return [f"an item that is not an object: {item!r}"]
    iid = item.get("id")
    where = f"item {iid}"
    out = _unknown(where, item, ITEM_KEYS)
    out += [f"{where}: no {k!r}" for k in ITEM_KEYS if k not in item]
    if not is_informal_item_id(iid):
        out.append(f"item {iid!r}: an id is <section>.<n>, both numbers from 1")
    elif item.get("section") != iid.split(".")[0]:
        out.append(f"{where}: its section is {item.get('section')!r}, not the one its id names")
    if item.get("section") not in section_numbers:
        out.append(f"{where}: section {item.get('section')!r} is not in `sections`")
    if item.get("kind") not in INFORMALIZE_KINDS:
        out.append(f"{where}: kind {item.get('kind')!r} is not one of {', '.join(INFORMALIZE_KINDS)}")
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
        if role not in INFORMALIZE_ROLES:
            out.append(f"{at}: role {role!r} is not one of {', '.join(INFORMALIZE_ROLES)}")
        if relation not in INFORMALIZE_RELATIONS:
            out.append(f"{at}: relation {relation!r} is not one of "
                       f"{', '.join(INFORMALIZE_RELATIONS)}")
        if part is not None and part not in letters:
            out.append(f"{at}: part {part!r} is not a part of the item")
        if letters and role == "key" and part is None:
            out.append(f"{at}: a key declaration of an item with parts names its part")
        if role == "key" and relation in INFORMALIZE_RELATIONS and relation != EXACT_MATCH \
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
        if not is_informal_item_id(iid):
            continue
        section, n = split_informal_item_id(iid)
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
# ---------------------------------------------------------------------------

def declarations_problems(doc: Any) -> list[str]:
    """Section 5.3's `declarations.json`: one entry per scope member, sorted by name."""
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
        if _source_path(row.get("path")) is None:
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
    if len(set(names)) != len(names):
        twice = sorted({n for n in names if names.count(n) > 1})
        out.append(f"declarations.json: listed twice: {', '.join(twice[:5])}")
    if names != sorted(names):
        out.append("declarations.json: the entries are not sorted by name")
    return out


def _source_path(value: Any) -> PurePosixPath | None:
    if not isinstance(value, str) or "\\" in value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or str(pure) != value or pure.suffix != ".lean" \
            or any(part in ("", ".", "..") for part in pure.parts):
        return None
    return pure


class SourceTree:
    """`library/src/`, read a file at a time, never through a link out of it."""

    def __init__(self, root: Path):
        self.root = root
        self._lines: dict[str, list[str] | None] = {}

    def lines(self, relative: str) -> list[str] | None:
        if relative not in self._lines:
            self._lines[relative] = self._read(relative)
        return self._lines[relative]

    def _read(self, relative: str) -> list[str] | None:
        pure = _source_path(relative)
        if pure is None:
            return None
        path = self.root.joinpath(*pure.parts)
        real, top = os.path.realpath(path), os.path.realpath(self.root)
        if not real.startswith(top + os.sep) or not os.path.isfile(real):
            return None
        return Path(real).read_text(encoding="utf-8", errors="replace").splitlines()


def keyword_problem(entry: dict, source: SourceTree) -> str | None:
    """Why `entry`'s keyword line in the library's source does not hold its keyword and
    its name's last component, or None when it does."""
    name, kind, line = entry["name"], entry.get("kind"), entry.get("line")
    lines = source.lines(entry.get("path"))
    if lines is None:
        return f"{name}: {entry.get('path')} is not in library/src/"
    if not isinstance(line, int) or not 1 <= line <= len(lines):
        return f"{name}: {entry.get('path')} has no line {line}"
    text = lines[line - 1]
    words = KEYWORDS.get(kind)
    if words is None:
        return f"{name}: no keyword is known for kind {kind!r}"
    keyword = rf"(?<![{_IDENT}.])(?:{'|'.join(words)})(?![{_IDENT}])"
    if not re.search(keyword, text):
        return (f"{name}: line {line} of {entry['path']} holds no {' or '.join(words)} "
                f"keyword: {text.strip()[:80]!r}")
    if kind != "instance":
        last = name.rsplit(".", 1)[-1]
        if not re.search(rf"(?<![{_IDENT}]){re.escape(last)}(?![{_IDENT}])", text):
            return f"{name}: line {line} of {entry['path']} does not name {last}"
    return None


def check_table(doc: Any, *, declarations: dict, declarations_sha256: str, source: SourceTree,
                library_commit: str, numbered: bool = True) -> list[str]:
    """`cf informalize check-items`: the rules of section 5.4, then every declaration of
    every item against `declarations.json` (name, module, keyword line) and its keyword line
    in the library's source."""
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
            if name not in checked:
                checked.add(name)
                found = keyword_problem(row, source)
                if found:
                    out.append(f"item {iid}: {found}")
    return out


# ---------------------------------------------------------------------------
# approval: renumbering, the frozen ids, sections.tex
# ---------------------------------------------------------------------------

def parse_in_v1(spec: str, doc: dict) -> set[str]:
    """`all`, or a comma list of the table's ids; anything else is refused by the caller."""
    ids = [it.get("id") for it in doc.get("items") or [] if isinstance(it, dict)]
    if spec.strip() == "all":
        return set(ids)
    chosen = {s.strip() for s in spec.split(",") if s.strip()}
    return chosen


def renumber(doc: dict, chosen: set[str]) -> tuple[dict, dict]:
    """The numbered table: the chosen items in v1 and numbered 1.. per section in reading
    order, the others moved to `deferred`, empty sections dropped and the rest numbered
    1..; and the decision that records it (empty when nothing moved)."""
    keep = [it for it in doc["items"] if it["id"] in chosen]
    moved = [dict(it, in_v1=False) for it in doc["items"] if it["id"] not in chosen]
    used = [s for s in doc["sections"] if any(it["section"] == s["number"] for it in keep)]
    section_of = {s["number"]: str(n) for n, s in enumerate(used, 1)}
    position = {id(it): i for i, it in enumerate(keep)}
    keep.sort(key=lambda it: (int(section_of[it["section"]]), position[id(it)]))
    mapping: dict[str, str] = {}
    counter: dict[str, int] = {}
    items = []
    for item in keep:
        section = section_of[item["section"]]
        counter[section] = counter.get(section, 0) + 1
        new = f"{section}.{counter[section]}"
        mapping[item["id"]] = new
        items.append(dict(item, id=new, section=section, in_v1=True))
    sections = [dict(s, number=section_of[s["number"]]) for s in used]
    dropped = [s["number"] for s in doc["sections"] if s["number"] not in section_of]
    decision: dict[str, Any] = {}
    renumbered = {old: new for old, new in mapping.items() if old != new}
    sections_moved = {old: new for old, new in section_of.items() if old != new}
    if renumbered or moved or dropped or sections_moved:
        decision = {"renumbered": renumbered, "sections": sections_moved,
                    "deferred": [it["id"] for it in moved], "dropped_sections": dropped}
    out = dict(doc, sections=sections, items=items,
               deferred=list(doc.get("deferred") or []) + moved)
    return out, decision


def snapshot(doc: dict) -> dict:
    """What an items approval records of the table, for :func:`frozen_problems`."""
    return {"items": {it["id"]: it["kind"] for it in doc["items"]},
            "sections": {s["number"]: s["slug"] for s in doc["sections"]}}


def frozen_problems(doc: dict, previous: dict | None) -> list[str]:
    """After the first draft: every approved id is still there with its kind and section,
    or withdrawn; a new id comes after every approved one of its section; an approved
    section keeps its number and slug, and a new one comes after them all."""
    if not isinstance(previous, dict):
        return []
    out: list[str] = []
    before_items = previous.get("items") or {}
    before_sections = previous.get("sections") or {}
    now_items = {it["id"]: it["kind"] for it in doc["items"]}
    now_sections = {s["number"]: s["slug"] for s in doc["sections"]}
    withdrawn = withdrawn_ids(doc)
    for iid, kind in sorted(before_items.items()):
        if iid in now_items:
            if now_items[iid] != kind:
                out.append(f"item {iid}: its kind was {kind} when its id froze; an annotation "
                           "id is the kind and the id, so the kind is frozen too")
        elif iid not in withdrawn:
            out.append(f"item {iid}: gone without a withdrawal; record "
                       f'{{"withdrawn": "{iid}", "why": "..."}} in decisions')
    top: dict[int, int] = {}
    for iid in filter(is_informal_item_id, before_items):
        section, n = split_informal_item_id(iid)
        top[section] = max(top.get(section, 0), n)
    for iid in sorted(filter(is_informal_item_id, set(now_items) - set(before_items))):
        section, n = split_informal_item_id(iid)
        if n <= top.get(section, 0):
            out.append(f"item {iid}: a new item comes after every approved item of its section")
    for number, slug in sorted(before_sections.items()):
        if now_sections.get(number) != slug:
            out.append(f"section {number}: its number and slug ({slug}) froze with its file")
    highest = max((int(n) for n in before_sections), default=0)
    for number in sorted(set(now_sections) - set(before_sections)):
        if int(number) <= highest:
            out.append(f"section {number}: a new section comes after every approved one")
    return out


def sections_tex(doc: dict) -> str:
    """`informal/sections.tex`: one `\\fcinputsection` line per section, in order."""
    return "".join(f"\\fcinputsection{{{s['number']}}}{{{section_file(s)}}}{{{s['title']}}}\n"
                   for s in doc["sections"])


# ---------------------------------------------------------------------------
# the reference checklist
# ---------------------------------------------------------------------------

def _given(ref: Any) -> bool:
    return isinstance(ref, dict) and bool(ref.get("number")) and ref.get("source") != "none"


def reference_rows(doc: dict) -> list[dict]:
    """Each `in_v1` item and part with a reference number, in reading order."""
    rows: list[dict] = []
    for item in doc.get("items") or []:
        if not item.get("in_v1"):
            continue
        refs = [(None, item.get("reference"))]
        refs += [(p.get("letter"), p.get("reference")) for p in item.get("parts") or []]
        for letter, ref in refs:
            if _given(ref):
                rows.append({"id": item["id"], "part": letter, "kind": item["kind"],
                             "title": item["title"], "number": ref["number"],
                             "source": ref["source"], "declaration": ref.get("declaration")})
    return rows


def rows_sha256(rows: list[dict]) -> str:
    return sha256_json(rows)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def checklist_markdown(informalize_id: str, work: str | None, rows: list[dict]) -> str:
    """`reference-checklist.md`, deterministic: the same list gives the same bytes."""
    lines = [f"# Reference numbers to check: {informalize_id}", ""]
    if work:
        lines += [f"Reference work: {work}.", ""]
    lines += [
        "Check each number against your own copy of the work before anything is drafted:",
        "the drafters print these numbers through `\\fcref`, and a number whose source is",
        "`inferred` was read off the work's order, not a docstring, and is printed with a",
        "dagger. A wrong number is fixed in `informal/items.json` by hand and approved again.",
        f"Then run `cf informalize approve {informalize_id} references --by <name>`.",
        "",
        f"List sha256: `{rows_sha256(rows)}` ({len(rows)} numbers)",
        "",
    ]
    if not rows:
        lines += ["No item or part of version 1 carries a reference number.", ""]
        return "\n".join(lines)
    lines += ["| checked | item | kind | title | number | source | stated in |",
              "|---|---|---|---|---|---|---|"]
    for row in rows:
        label = row["id"] + (f"({row['part']})" if row["part"] else "")
        lines.append("| [ ] | " + " | ".join(_cell(v) for v in (
            label, row["kind"], row["title"], row["number"], row["source"],
            row["declaration"])) + " |")
    return "\n".join(lines) + "\n"


def dumps(doc: dict) -> bytes:
    """items.json as `approve` writes it back."""
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

