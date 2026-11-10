"""The item table, `informal/items.json`: its rules, `check-items`, renumbering and the
reference checklist.

New code, for docs/proposals/informalize.md section 5.4.  The rules themselves -- the
table's and `declarations.json`'s, the keyword check of each key declaration against the
library's source, and `check_table`, the port of the pilot's item verifier that
`check-items` runs -- live in the runtime's `tools/fcitems.py`
(templates/informalize/runtime/), because the gate (`scripts/gate/verify_informal.py`, section
8.3 rule 4) checks the items task's table with them on a host where `cflib` cannot be
imported; this module imports them through `informalize.runtime.fcitems`, so there is one
copy of the rules, and re-exports their names.

Two states of one table.  The items task drafts **provisional** ids (`<section>.<n>`,
numbered as it likes, gaps allowed); `cf informalize approve <id> items` renumbers them
(:func:`renumber`) while every section file is empty, and from then on the table is
**numbered**: sections run 1, 2, ... and items 1, 2, ... within a section, in reading
order, the gaps only where a withdrawn id is recorded in `decisions`.  Once a section file
is not empty the ids are frozen (:func:`frozen_problems`): a printed number is an id, and
an annotation's id derives from the kind and the id.

`templates/informalize/schema/items.schema.json` states the shape as a JSON Schema for a
reader; the rules are the implementation, and tests/test_informalize_cli.py holds the two
to one set of keys and enums.
"""
from __future__ import annotations

import json
import re
from typing import Any

from cflib.hashing import sha256_json
from cflib.items import is_informal_item_id, split_informal_item_id

from informalize.runtime import fcitems as _rules_module

_rules = _rules_module()

ITEMS_SCHEMA = _rules.ITEMS_SCHEMA
SCHEMA_VERSION = _rules.SCHEMA_VERSION
TOP_KEYS = _rules.TOP_KEYS
LIBRARY_KEYS = _rules.LIBRARY_KEYS
SECTION_KEYS = _rules.SECTION_KEYS
ITEM_KEYS = _rules.ITEM_KEYS
PART_KEYS = _rules.PART_KEYS
REFERENCE_KEYS = _rules.REFERENCE_KEYS
DECLARATION_KEYS = _rules.DECLARATION_KEYS
SLUG_PATTERN = _rules.SLUG_PATTERN
SECTION_NUMBER_PATTERN = _rules.SECTION_NUMBER_PATTERN
SHA256_PATTERN = _rules.SHA256_PATTERN
KEYWORDS = _rules.KEYWORDS
SourceTree = _rules.SourceTree
section_file = _rules.section_file
withdrawn_ids = _rules.withdrawn_ids
reference_problems = _rules.reference_problems
item_problems = _rules.item_problems
items_problems = _rules.items_problems
declarations_problems = _rules.declarations_problems
keyword_problem = _rules.keyword_problem
check_table = _rules.check_table


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


def renumber(doc: dict, chosen: set[str], *, at: str | None = None) -> tuple[dict, dict]:
    """The numbered table: the chosen items in v1 and numbered 1.. per section in reading
    order, the others moved to `deferred`, empty sections dropped and the rest numbered
    1..; and the decision that records it (empty when nothing moved).  A deferred item
    keeps the id and section it had, and `at` (the decision's time) as `x_deferred_at`,
    which names the numbering they belong to."""
    keep = [it for it in doc["items"] if it["id"] in chosen]
    stamp = {"x_deferred_at": at} if at else {}
    moved = [dict(it, in_v1=False, **stamp) for it in doc["items"] if it["id"] not in chosen]
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
            "sections": {s["number"]: s["slug"] for s in doc["sections"]},
            "withdrawn": sorted(withdrawn_ids(doc))}


def frozen_problems(doc: dict, previous: dict | None) -> list[str]:
    """After the first draft: every approved id is still there with its kind and section,
    or withdrawn; a withdrawn id stays withdrawn; a new id comes after every approved one of
    its section; an approved section keeps its number and slug, and a new one comes after
    them all."""
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
    for iid in sorted(set(previous.get("withdrawn") or []) - withdrawn):
        out.append(f"item {iid}: withdrawn when it was last approved; a withdrawal stays in "
                   "decisions, and the id is never reused")
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


#: a cell boundary of a checklist row: a `|` that `_cell` did not escape
_CELL_BOUNDARY = re.compile(r"(?<!\\)\|")


def ticked_rows(text: str) -> set[tuple[str, str]]:
    """The (item, number) cells of each row a person ticked in a checklist, as written."""
    out = set()
    for line in text.splitlines():
        cells = [c.strip() for c in _CELL_BOUNDARY.split(line)]
        if len(cells) == 9 and cells[1].lower() == "[x]":
            out.add((cells[2], cells[5]))
    return out


def checklist_markdown(informalize_id: str, work: str | None, rows: list[dict],
                       ticked: set[tuple[str, str]] = frozenset()) -> str:
    """`reference-checklist.md`, deterministic: the same list and the same `ticked`
    (:func:`ticked_rows` of the file it replaces) give the same bytes."""
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
        box = "[x]" if (_cell(label), _cell(row["number"])) in ticked else "[ ]"
        lines.append(f"| {box} | " + " | ".join(_cell(v) for v in (
            label, row["kind"], row["title"], row["number"], row["source"],
            row["declaration"])) + " |")
    return "\n".join(lines) + "\n"


def dumps(doc: dict) -> bytes:
    """items.json as `approve` writes it back."""
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
