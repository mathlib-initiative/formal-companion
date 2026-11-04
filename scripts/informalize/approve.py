"""Stage I3's commands and the maintainer's gates: `check-items`, `reference-checklist` and
`approve <id> <gate>`.

New code, for docs/proposals/informalize.md sections 5.4, 5.7 and 13.

* `check-items` holds `informal/items.json` to section 5.4's rules and every declaration
  to `informal/declarations.json` and its keyword line in `library/src/`
  (`informalize.itemtable.check_table`).  It writes nothing.
* `approve <id> items --in-v1 <ids|all>` passes the same check first.  While every section
  file is empty it moves the items not chosen to `deferred`, drops empty sections,
  renumbers what is left, records the old-to-new map in `decisions`, writes `sections.tex`
  and an empty file per section, and commits; once a section file is not empty the ids
  are frozen, and an approval only pins (after setting `in_v1` when `--in-v1` is given),
  holding the table to the ids it approved before.  It pins `items.json`.
* `reference-checklist` writes `reference-checklist.md`, the reference numbers of the
  approved table for a person with the work in hand; `approve <id> references` pins that
  list (not the whole table), and only the list the checklist on disk shows.
* `approve <id> proof-sheets` and `approve <id> publish` pin what the I6 and I7 receipts
  list, once those stages have run (components 6 and 7 write them).

An approval is the maintainer's: it is refused inside a fleet task (an agent's wrapper
sets `CF_ROLE`, tend sets `TEND_TASK_ID`), and it prints what it pins.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from cflib.errors import CfError
from cflib.hashing import sha256_file
from cflib.jsonio import atomic_write
from cflib.state import now
from cflib.vocab import APPROVAL_GATES

from informalize import config, itemtable, stages
from informalize.outrepo import OutputRepo

ITEMS = "informal/items.json"
DECLARATIONS = "informal/declarations.json"
SECTIONS_TEX = "informal/sections.tex"
SECTIONS_DIR = "informal/sections"
CONVENTIONS_FILE = "00-conventions.tex"
#: the pin of the references gate: the list of reference numbers derived from items.json
REFERENCES_PIN = f"repo/{ITEMS}#references"
#: the stage whose receipt a gate pins from, for the two gates of components 6 and 7
RECEIPT_GATES = {"proof-sheets": ("I6", "cf informalize fetch-pdf"),
                 "publish": ("I7", "cf informalize overlay")}
#: what a fleet's wrappers and tend set in an agent's environment
AGENT_ENVIRONMENT = ("CF_ROLE", "TEND_TASK_ID")
_SECTION_FILE = re.compile(r"\A[0-9]{2,}-[a-z0-9-]+\.tex\Z")


@dataclass
class Table:
    """One informalize id's item table, with what it is checked against."""

    workspace: object
    id: str
    cfg: dict = field(init=False)
    repo: OutputRepo = field(init=False)

    def __post_init__(self):
        paths = self.workspace.paths
        self.cfg = config.load(paths, self.id, sessions=config.session_limit(self.workspace))
        self.repo = OutputRepo(paths, self.id)
        self.repo.check_location()

    @property
    def paths(self):
        return self.workspace.paths

    def items(self) -> dict:
        path = self.repo.root / ITEMS
        if path.is_symlink() or not path.is_file():
            raise CfError(f"no {path}: stage I3 has not run",
                          hint="the items phase writes informal/items.json "
                               "(docs/proposals/informalize.md section 8.2)")
        return _read_json(path)

    def declarations(self) -> tuple[dict, str]:
        path = self.repo.root / DECLARATIONS
        if path.is_symlink() or not path.is_file():
            raise CfError(f"no {path}: stage I2 has not run",
                          hint=f"`cf informalize scope {self.id}` writes it")
        doc = _read_json(path)
        found = itemtable.declarations_problems(doc)
        if found:
            raise CfError(f"{path}: {found[0]}", hint="; ".join(found[1:4]) or
                          f"`cf informalize scope {self.id}` writes it again")
        return doc, sha256_file(path)

    def source(self) -> itemtable.SourceTree:
        root = self.paths.informalize_library_src(self.id)
        if not root.is_dir():
            raise CfError(f"no {root}: stage I1 has not run",
                          hint=f"`cf informalize fetch-library {self.id}` unpacks the "
                               "library's source there")
        return itemtable.SourceTree(root)

    def numbered(self) -> bool:
        """Has an items approval renumbered this table once?  From then on it stays so."""
        return stages.read_approval(self.paths, self.id, "items") is not None

    def check(self, doc: dict, *, numbered: bool) -> list[str]:
        declarations, sha = self.declarations()
        return itemtable.check_table(doc, declarations=declarations, declarations_sha256=sha,
                                     source=self.source(),
                                     library_commit=self.cfg["library"]["commit"],
                                     numbered=numbered)

    def drafted(self) -> list[str]:
        """The section files that are not empty: once there is one, the ids are frozen."""
        directory = self.repo.root / SECTIONS_DIR
        if not directory.is_dir():
            return []
        return sorted(p.name for p in directory.iterdir()
                      if p.name != CONVENTIONS_FILE and _SECTION_FILE.match(p.name)
                      and p.is_file() and p.read_text(encoding="utf-8", errors="replace").strip())

    def references_sha(self) -> str | None:
        path = self.repo.root / ITEMS
        if not path.is_file():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            return itemtable.rows_sha256(itemtable.reference_rows(doc))
        except (ValueError, KeyError, TypeError, AttributeError):
            return None


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CfError(f"{path} is not JSON: {exc}") from None
    if not isinstance(value, dict):
        raise CfError(f"{path} is not a JSON object")
    return value


def _refuse(problems: list[str], what: str) -> None:
    if problems:
        raise CfError(f"{what}: {len(problems)} problem(s); the first: {problems[0]}",
                      hint="; ".join(problems[1:4]) or None, exit_code=1)


# ---------------------------------------------------------------------------
# check-items
# ---------------------------------------------------------------------------

def check_items(workspace, informalize_id: str) -> tuple[list[str], dict]:
    """`cf informalize check-items`: (problems, the table) -- read-only."""
    table = Table(workspace, informalize_id)
    doc = table.items()
    return table.check(doc, numbered=table.numbered()), doc


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------

def gate_state(workspace, informalize_id: str, gate: str) -> tuple[str, list[str]]:
    """``("absent" | "current" | "stale", why)`` of one gate's approval."""
    paths = workspace.paths
    approval = stages.read_approval(paths, informalize_id, gate)
    if approval is None:
        return "absent", []
    table = Table(workspace, informalize_id)
    stale = stages.stale_pins(paths.informalize_dir(informalize_id), approval["pins"],
                              {REFERENCES_PIN: table.references_sha})
    return ("stale" if stale else "current"), stale


def _require_current(workspace, informalize_id: str, gate: str, why: str) -> dict:
    state, stale = gate_state(workspace, informalize_id, gate)
    if state != "current":
        raise CfError(f"the {gate} approval is {state}" + (f" ({stale[0]})" if stale else ""),
                      hint=why)
    return stages.read_approval(workspace.paths, informalize_id, gate)


def approve(workspace, informalize_id: str, gate: str, *, by: str, in_v1: str | None = None,
            note: str = "") -> dict:
    """`cf informalize approve <id> <gate>`: write and return the approval."""
    agents = [name for name in AGENT_ENVIRONMENT if os.environ.get(name)]
    if agents:
        raise CfError(f"approve is the maintainer's, and this is a fleet task ({agents[0]} is set)",
                      hint="an agent never signs a gate (docs/proposals/informalize.md section 13)")
    if gate not in APPROVAL_GATES:
        raise CfError(f"no gate {gate!r}", hint=f"one of {', '.join(APPROVAL_GATES)}")
    if not by or not by.strip() or "\n" in by:
        raise CfError("--by names who approves, on one line")
    if in_v1 is not None and gate != "items":
        raise CfError("--in-v1 is for the items gate only")
    if gate == "items":
        return approve_items(workspace, informalize_id, by=by, in_v1=in_v1, note=note)
    if gate == "references":
        return approve_references(workspace, informalize_id, by=by, note=note)
    return approve_from_receipt(workspace, informalize_id, gate, by=by, note=note)


def approve_items(workspace, informalize_id: str, *, by: str, in_v1: str | None,
                  note: str = "") -> dict:
    table = Table(workspace, informalize_id)
    table.repo.require_stamp()
    doc = table.items()
    previous = stages.read_approval(table.paths, informalize_id, "items")
    drafted = table.drafted()
    if not drafted:
        new = _renumbered(table, doc, in_v1, by, first=previous is None)
    else:
        new = _frozen(table, doc, in_v1, previous, drafted)
    touched = _write_table(table, new, renumber=not drafted)
    count = sum(1 for it in new["items"] if it["in_v1"])
    commit = table.repo.commit(
        touched, f"Approve the item table: {count} items in version 1, in "
                 f"{len(new['sections'])} sections")
    pins = {f"repo/{ITEMS}": sha256_file(table.repo.root / ITEMS)}
    return stages.write_approval(
        table.paths, informalize_id, "items", by=by, pins=pins, note=note,
        extra={"snapshot": itemtable.snapshot(new), "frozen": bool(drafted),
               "commit": commit or table.repo.head()})


def _parse_choice(spec: str, doc: dict) -> set[str]:
    chosen = itemtable.parse_in_v1(spec, doc)
    ids = {it.get("id") for it in doc.get("items") or [] if isinstance(it, dict)}
    unknown = sorted(chosen - ids)
    if unknown:
        raise CfError(f"--in-v1 names an id the table does not have: {', '.join(unknown[:5])}",
                      hint="name the ids items.json gives, comma-separated, or all")
    if not chosen:
        raise CfError("--in-v1 chooses no item", hint="name the items of version 1, or all")
    return chosen


def _renumbered(table: Table, doc: dict, in_v1: str | None, by: str, *, first: bool) -> dict:
    """Before the first draft.  The first approval names the items of version 1; a later
    one may, and otherwise keeps those the table marks `in_v1`."""
    if in_v1 is None and first:
        raise CfError("--in-v1 is required at the first approval",
                      hint="name the items of version 1 (the provisional ids of items.json, "
                           "comma-separated), or all; the others move to `deferred`")
    if in_v1 is None:
        chosen = {it.get("id") for it in doc.get("items") or []
                  if isinstance(it, dict) and it.get("in_v1") is True}
        if not chosen:
            raise CfError("no item of the table is in version 1", hint="pass --in-v1")
    else:
        chosen = _parse_choice(in_v1, doc)
    # the choice first: an item left out needs no key declaration
    doc = dict(doc, items=[dict(it, in_v1=it.get("id") in chosen) if isinstance(it, dict) else it
                           for it in doc.get("items") or []])
    _refuse(table.check(doc, numbered=False), "items.json")
    new, decision = itemtable.renumber(doc, chosen)
    if decision:
        new["decisions"] = list(doc.get("decisions") or []) + [
            dict(decision, approved_by=by.strip(), at=now())]
    _refuse(table.check(new, numbered=True), "the renumbered table")    # a bug if it fires
    return new


def _frozen(table: Table, doc: dict, in_v1: str | None, previous: dict | None,
            drafted: list[str]) -> dict:
    new = doc
    if in_v1 is not None:
        chosen = _parse_choice(in_v1, doc)
        new = dict(doc, items=[dict(it, in_v1=it["id"] in chosen) for it in doc["items"]])
    problems = table.check(new, numbered=True)
    if not problems:
        problems = itemtable.frozen_problems(new, (previous or {}).get("snapshot"))
    _refuse(problems, f"items.json (ids frozen since {drafted[0]} was drafted)")
    return new


def _write_table(table: Table, doc: dict, *, renumber: bool) -> list[str]:
    """Write items.json, sections.tex and the empty section files; the paths to commit."""
    repo = table.repo
    touched = []
    if repo.write(ITEMS, itemtable.dumps(doc)):
        touched.append(ITEMS)
    if repo.write(SECTIONS_TEX, itemtable.sections_tex(doc).encode("utf-8")):
        touched.append(SECTIONS_TEX)
    wanted = {f"{itemtable.section_file(s)}.tex" for s in doc["sections"]}
    for name in sorted(wanted):
        relative = f"{SECTIONS_DIR}/{name}"
        if not (repo.root / relative).exists() and repo.write(relative, b""):
            touched.append(relative)
    if renumber:                        # every section file is empty: drop the old numbering's
        directory = repo.root / SECTIONS_DIR
        for path in sorted(directory.iterdir()) if directory.is_dir() else []:
            if path.name not in wanted and path.name != CONVENTIONS_FILE \
                    and _SECTION_FILE.match(path.name) and path.is_file() \
                    and not path.read_text(encoding="utf-8", errors="replace").strip():
                repo.remove(f"{SECTIONS_DIR}/{path.name}")
                touched.append(f"{SECTIONS_DIR}/{path.name}")
    return touched


def approve_references(workspace, informalize_id: str, *, by: str, note: str = "") -> dict:
    _require_current(workspace, informalize_id, "items",
                     f"`cf informalize approve {informalize_id} items` first: the reference "
                     "numbers are checked on the approved, renumbered table")
    table = Table(workspace, informalize_id)
    rows = itemtable.reference_rows(table.items())
    sha = itemtable.rows_sha256(rows)
    checklist = table.paths.informalize_reference_checklist(informalize_id)
    shown = checklist.read_text(encoding="utf-8") if checklist.is_file() else ""
    if f"List sha256: `{sha}`" not in shown:
        raise CfError(f"{checklist} does not show the current list of reference numbers",
                      hint=f"`cf informalize reference-checklist {informalize_id}` writes it; "
                           "check it against the work, then approve")
    return stages.write_approval(table.paths, informalize_id, "references", by=by,
                                 pins={REFERENCES_PIN: sha}, note=note,
                                 extra={"references": rows})


def approve_from_receipt(workspace, informalize_id: str, gate: str, *, by: str,
                         note: str = "") -> dict:
    """The proof-sheets and publish gates: the files their stage's receipt lists.

    TODO(components 6 and 7): the I6 and I7 receipts are written by `fetch-pdf` and
    `overlay`, which are not built; this pins the receipt and every output it lists
    (section 13: the PDF and markers.json; the output repository's HEAD and the PDF), each
    checked on disk first.
    """
    paths = workspace.paths
    stage, writer = RECEIPT_GATES[gate]
    receipt = stages.read_receipt(paths, informalize_id, stage)
    if receipt is None:
        raise CfError(f"stage {stage} has not run (no stages/{stage}.json)",
                      hint=f"`{writer} {informalize_id}` writes it")
    home = paths.informalize_dir(informalize_id)
    stale = stages.stale_pins(home, receipt["outputs"])
    if stale:
        raise CfError(f"the {stage} receipt no longer describes the files: {stale[0]}",
                      hint=f"re-run `{writer} {informalize_id}`")
    pins = {f"stages/{stage}.json": sha256_file(paths.informalize_receipt(informalize_id, stage)),
            **receipt["outputs"]}
    extra = {}
    if gate == "publish":
        _require_current(workspace, informalize_id, "proof-sheets",
                         f"`cf informalize approve {informalize_id} proof-sheets` first")
        repo = OutputRepo(paths, informalize_id)
        repo.require_stamp()
        if repo.changes():
            raise CfError(f"{repo.root} has uncommitted changes",
                          hint="publish approves a commit; commit or discard them first")
        extra["head"] = repo.head()
    return stages.write_approval(paths, informalize_id, gate, by=by, pins=pins, note=note,
                                 extra=extra)


# ---------------------------------------------------------------------------
# the reference checklist
# ---------------------------------------------------------------------------

def reference_checklist(workspace, informalize_id: str) -> tuple[Path, list[dict]]:
    """`cf informalize reference-checklist`: write it, for the approved table."""
    _require_current(workspace, informalize_id, "items",
                     f"`cf informalize approve {informalize_id} items` first: its renumbering "
                     "changes the ids this list names")
    table = Table(workspace, informalize_id)
    doc = table.items()
    rows = itemtable.reference_rows(doc)
    work = doc.get("reference_work") or (table.cfg.get("reference") or {}).get("work")
    path = table.paths.informalize_reference_checklist(informalize_id)
    atomic_write(path, itemtable.checklist_markdown(informalize_id, work, rows))
    return path, rows
