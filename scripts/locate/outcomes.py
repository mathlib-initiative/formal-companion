"""Harvested locate documents -> finding rows and passage outcomes with a derived status.

New code, implementing docs/proposals/wikipedia-locate.md sections 2.6, 3 (L6)
and 5 in the shapes of references/locate-documents.md section 10.

Three steps, in this order, each a function of what landed:

1. **Finding rows.**  Every document becomes the rows section 10's table names
   -- one ``PASSAGE`` per inventory passage, one ``LOCATION`` per location, one
   ``ABSENCE`` per checked absence, one ``UNRESOLVED`` per unresolved passage
   or document -- keyed by ``cflib.hashing.finding_id`` unchanged: the library
   id is the ``repo`` and ``<library>:<declaration>@<commit>`` the declaration
   list, so the tally key and the receipts work as they do for a faithfulness
   run.
2. **Challenge.**  The verdicts of the challenge runs given are applied by
   section 4's semantics (``confirm``/``uphold`` keep a record and make it
   ``survived``; ``downgrade``, ``refute`` and ``overturn`` change it and make
   the affected records ``downgraded``, except that a location an overturn
   adds is the challenger's own witnessed finding, ``survived``; the
   locations a refute leaves are ranked 1..n again).  A passage's coverage
   claim (the maintainer's decision D1) is its own record: ``confirm`` makes
   it ``survived``, ``refute`` removes it and leaves its parts ``hasPart``.  A
   record the challenge plan did not require keeps the value the plan
   recorded (``not_required``, ``sampled_out``) -- unless a prominence
   overturn to ``subject`` or ``central`` put it under a rule that requires
   it, which makes it
   ``challenge_pending``; a record the plan required and no verdict judged has
   challenge ``None`` -- owed, which the export refuses (hard rule 5).
3. **Status.**  ``proof_status`` and the ``statement`` flag are joined from
   ``corpus.sqlite`` (never read from a document) with ``corpus.query.
   declaration``, and ``cflib.vocab.derive_status`` computes the passage's
   status over the surviving locations, the absence and the coverage claim.
   An unresolved passage has no status and is counted on its own, never as
   not formalized.  The database is the run's CHECK database, the full one, accepted when it was
   built for the universe the run pinned (``corpus.query.check_universe``):
   a rebuild of an unchanged universe is the same database, whatever its bytes.

A passage the run planned but no document carries (a task that never merged)
is kept with ``outcome: None`` and ``missing: true``: the safety metric counts
it as a miss, so dropping it here would hide one.  The planned passages are read
from ``runs/<run>/planned.json`` (the tend root, which a workspace does not
commit, only when that file is absent) and refused when they are not the count
``run.yaml`` recorded.

A challenge run is applied only to the locate run it challenged (its plan's
``challenged_runs`` and each record's ``run_id``), only to the records its plan
REQUIRED, and only while each record is what the plan saw (a location's
relation, a passage's outcome): anything else is named in ``problems`` and left
owed.  Every row carries the ``source_sha`` and ``layer_sha256`` of the
document it came from, and a document whose pins are not the run's manifest's
puts a ``problem`` on each of its rows -- the export refuses those, as it does a
row whose location could not be joined.
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from typing import Iterable

from cflib import hashing, items as cf_items, vocab
from cflib.errors import CfError
from cflib.jsonio import read_json
from cflib.paths import WorkspacePaths
from cflib.yamlio import read_yaml

from corpus import query

from . import inputs, records

CHALLENGE_PLAN_SCHEMA = "cf-locate-challenge-plan/1"
#: a relation's strength, strongest first: a ``downgrade`` names a WEAKER one
#: (references/locate-documents.md section 4); one order, the gate's too
RELATION_STRENGTH = vocab.RELATION_STRENGTH


def decl_token(library: str, declaration: str, commit: str) -> str:
    """``<library>:<declaration>@<commit>``: a location's entry in the finding's decl list."""
    return f"{library}:{declaration}@{commit}"


def record_key(target: str, source_id: str, item: str, location_key: str | None = None) -> str:
    """The key a challenge plan and its verdicts agree on.

    A location is its ``location_key``; any other target is the run-free hash of
    the target, the source and the cleaned item.
    """
    if target == "location":
        if not location_key:
            raise CfError("a location record needs its location_key")
        return location_key
    return hashing.sha256_text("\n".join([target, source_id, cf_items.clean(item)]))


# --------------------------------------------------------------------------
# reading a run
# --------------------------------------------------------------------------

def run_yaml(paths: WorkspacePaths, run_id: str) -> dict:
    path = paths.run_yaml(run_id)
    if not path.is_file():
        raise CfError(f"no run.yaml for run {run_id!r} ({path})",
                      hint="plan it with `cf plan-locate`")
    data = read_yaml(path)
    if not isinstance(data, dict) or not isinstance(data.get("locate"), dict):
        raise CfError(f"run {run_id!r} is not a locate run (run.yaml has no locate block)")
    return data


def task_manifests(paths: WorkspacePaths, run_id: str) -> list[dict]:
    """The run's task manifests, as planned.

    ``runs/<run>/planned.json`` (written by ``cf plan-locate`` beside run.yaml),
    else the tend root's ``gate/tasks/*.json``; [] when neither is there.
    """
    planned = paths.run_dir(run_id) / "planned.json"
    if planned.is_file():
        return list(read_json(planned).get("tasks") or [])
    directory = paths.tend_root(run_id) / "gate" / "tasks"
    if not directory.is_dir():
        return []
    return [read_json(p) for p in sorted(directory.glob("*.json"))]


class _Layers:
    """``layer_of(source_id)``: a source's rendered layer, read once, or None when the
    sourceset holds no rendering of it (a downgrade note is then chosen without it)."""

    def __init__(self, paths: WorkspacePaths, sourceset_id: str):
        self.paths, self.sourceset_id, self.read = paths, sourceset_id, {}

    def __call__(self, source_id: str) -> str | None:
        if source_id not in self.read:
            try:
                self.read[source_id] = (inputs.source_view(self.paths, self.sourceset_id,
                                                           source_id).layer()
                                        if self.sourceset_id else None)
            except (CfError, OSError):
                self.read[source_id] = None
        return self.read[source_id]


def _documents(paths: WorkspacePaths, run_id: str, *, require_gate: bool
               ) -> tuple[list[tuple[str, dict]], dict]:
    run_dir = paths.corpus_run_dir(run_id)
    gate = {"receipt": False, "complete": None, "gate_ok": None}
    got = inputs.receipt(run_dir)
    if got is not None:
        gate = {"receipt": True, "complete": bool(got.get("complete")),
                "gate_ok": bool((got.get("gate") or {}).get("ok")) if got.get("gate") else None,
                "harvested_at": got.get("harvested_at")}
    if require_gate:
        inputs.require_gated(run_dir, run_id, "run")
    return inputs.harvested_documents(run_dir), gate


def _source_of(rel: str, doc: dict) -> str:
    sid = str(doc.get("source_id") or "")
    if not sid:
        raise CfError(f"{rel}: a locate document names its source_id")
    if not rel.endswith(f"/{sid}.json"):
        raise CfError(f"{rel}: the document for {sid} lives at <task-id>/{sid}.json")
    return sid


def _task_of(rel: str) -> str:
    parts = rel.split("/")
    return parts[-2] if len(parts) >= 2 else ""


def _stamp(doc: dict) -> dict:
    return {"source_sha": doc.get("source_sha"), "layer_sha256": doc.get("layer_sha256")}


def inventory(paths: WorkspacePaths, run_id: str, *, require_gate: bool = True) -> dict:
    """The frozen passage table of an inventory run: ``{(source_id, item): passage}``.

    Returns ``{"passages": {...}, "unresolved": {source_id: reason}, "documents": n,
    "gate": {...}}``.  Two documents for one source, or one passage twice, are
    refused: a passage table is frozen once.  A run that was never harvested
    (no ``RECEIPT.json``) is refused, never read as an empty table.
    """
    if inputs.receipt(paths.corpus_run_dir(run_id)) is None:
        raise CfError(f"inventory run {run_id!r} has no RECEIPT.json under "
                      f"{paths.corpus_run_dir(run_id)}",
                      hint=f"harvest it first: cf harvest {run_id}")
    docs, gate = _documents(paths, run_id, require_gate=require_gate)
    passages: dict[tuple[str, str], dict] = {}
    unresolved: dict[str, dict] = {}
    seen_sources: dict[str, str] = {}
    for rel, doc in docs:
        if doc.get("mode") != vocab.LOCATE_INVENTORY:
            raise CfError(f"{rel} in inventory run {run_id} is a {doc.get('mode')!r} document")
        sid = _source_of(rel, doc)
        if sid in seen_sources:
            raise CfError(f"inventory run {run_id} holds two documents for {sid}: "
                          f"{seen_sources[sid]} and {rel}")
        seen_sources[sid] = rel
        if isinstance(doc.get("unresolved"), dict):
            unresolved[sid] = dict(doc["unresolved"])
            continue
        for row in doc.get("passages") or []:
            item = str(row.get("item") or "")
            key = (sid, cf_items.join_key(item))
            if key in passages:
                raise CfError(f"{rel}: passage {item} is listed twice")
            passages[key] = {**row, "source_id": sid, "item": item, **_stamp(doc),
                             "task_id": _task_of(rel)}
    return {"passages": passages, "unresolved": unresolved, "documents": len(docs),
            "gate": gate}


# --------------------------------------------------------------------------
# finding rows (section 10's table)
# --------------------------------------------------------------------------

def _row(run_id: str, source_id: str, item: str, kind: str, repo: str,
         decls: list[str], **extra) -> dict:
    return {"finding_id": hashing.finding_id(run_id, source_id, item, kind, decls),
            "run_id": run_id, "source_id": source_id, "item": item, "kind": kind,
            "repo": repo, "unfaithful_decls": decls, **extra}


def finding_rows(run_id: str, docs: Iterable[tuple[str, dict]], *, sourceset_id: str,
                 universe: inputs.Universe | None,
                 accepted: set[tuple[str, str]] | None = None) -> list[dict]:
    """Every document's finding rows, in document order.

    ``accepted`` -- ``{(document path, item)}`` -- keeps a locate document's rows
    to the passages the outcomes took from it, so a passage answered twice
    yields one set of rows.
    """
    rows: list[dict] = []
    for rel, doc in docs:
        mode = doc.get("mode")
        sid = _source_of(rel, doc)
        stamp = _stamp(doc)
        if mode == vocab.LOCATE_INVENTORY:
            if isinstance(doc.get("unresolved"), dict):
                rows.append(_row(run_id, sid, "", vocab.UNRESOLVED, sourceset_id, [],
                                 by=doc["unresolved"].get("by") or vocab.UNRESOLVED_BY_WORKER,
                                 **stamp))
                continue
            for p in doc.get("passages") or []:
                rows.append(_row(run_id, sid, str(p.get("item") or ""), vocab.PASSAGE,
                                 sourceset_id, [], passage_kind=p.get("passage_kind"),
                                 scope=p.get("scope"), **stamp))
            continue
        if mode != vocab.LOCATE:
            continue
        if universe is None:
            raise CfError("a locate document's rows need the run's universe")
        for p in doc.get("passages") or []:
            item = str(p.get("item") or "")
            if accepted is not None and (rel, item) not in accepted:
                continue
            outcome = p.get("outcome")
            for loc in p.get("locations") or []:
                lib, name = str(loc.get("library")), str(loc.get("declaration"))
                commit = universe.libraries.get(lib, {}).get("commit", "")
                rows.append(_row(run_id, sid, item, vocab.LOCATION, lib,
                                 [decl_token(lib, name, commit)],
                                 relation=loc.get("relation"), **stamp))
            if outcome == vocab.OUTCOME_ABSENT:
                rows.append(_row(run_id, sid, item, vocab.ABSENCE, universe.id, [], **stamp))
            elif outcome == vocab.OUTCOME_UNRESOLVED:
                rows.append(_row(run_id, sid, item, vocab.UNRESOLVED, universe.id, [],
                                 by=_unresolved_by(doc), **stamp))
    return rows


def _unresolved_by(doc: dict) -> str:
    """Who left a passage unresolved: the attempt cap's document says so, else the worker."""
    return str((doc.get("unresolved") or {}).get("by") or vocab.UNRESOLVED_BY_WORKER)


# --------------------------------------------------------------------------
# challenge
# --------------------------------------------------------------------------

def challenge_values(paths: WorkspacePaths, run_id: str, challenge_runs: Iterable[str], *,
                     require_gate: bool = True) -> tuple[dict, dict, list[str], dict]:
    """``(planned, verdicts, problems, gates)`` of the challenge runs of locate run ``run_id``.

    ``planned`` is ``{record key: plan row}`` -- the records of each run's
    ``challenge-plan.json`` that are ``run_id``'s; ``verdicts`` is ``{record key:
    verdict}`` from its harvested documents, kept only for a record the plan
    REQUIRED (a verdict on any other is named in ``problems`` and ignored);
    ``gates`` is each challenge run's receipt summary.  A challenge run that did
    not challenge ``run_id`` is refused; one whose plan requires nothing has no
    documents to read.  One record planned or judged twice is refused.
    """
    planned: dict[str, dict] = {}
    verdicts: dict[str, dict] = {}
    problems: list[str] = []
    gates: dict[str, dict] = {}
    for challenge_run in challenge_runs:
        plan_path = paths.run_dir(challenge_run) / "challenge-plan.json"
        if not plan_path.is_file():
            raise CfError(f"challenge run {challenge_run!r} has no challenge-plan.json",
                          hint="plan it with `cf plan-locate --preset locate-challenge`")
        plan = read_json(plan_path)
        if plan.get("schema") != CHALLENGE_PLAN_SCHEMA:
            raise CfError(f"{plan_path} is not a {CHALLENGE_PLAN_SCHEMA} document")
        if run_id not in (plan.get("challenged_runs") or []):
            raise CfError(f"challenge run {challenge_run!r} challenged "
                          f"{', '.join(plan.get('challenged_runs') or []) or 'nothing'}, "
                          f"not {run_id}",
                          hint=f"plan a challenge of it: cf plan-locate --preset "
                               f"locate-challenge --challenge-of {run_id}")
        required = 0
        for rec in plan.get("records") or []:
            if rec.get("run_id") != run_id:
                continue
            key = rec["key"]
            if key in planned:
                raise CfError(f"record {key[:12]} is planned by two challenge runs")
            planned[key] = {**rec, "challenge_run": challenge_run}
            required += rec.get("planned") == vocab.CHALLENGE_REQUIRED
        if not any(r.get("planned") == vocab.CHALLENGE_REQUIRED
                   for r in plan.get("records") or []):
            gates[challenge_run] = {"receipt": None, "complete": None, "gate_ok": None,
                                    "required": 0}
            continue
        docs, gate = _documents(paths, challenge_run, require_gate=require_gate)
        gates[challenge_run] = {**gate, "required": required}
        for rel, doc in docs:
            if doc.get("mode") != vocab.LOCATE_CHALLENGE:
                problems.append(f"{rel} in challenge run {challenge_run} is not a challenge "
                                "document")
                continue
            sid = _source_of(rel, doc)
            for v in doc.get("verdicts") or []:
                target = str(v.get("target") or "")
                try:
                    key = record_key(target, sid, str(v.get("item") or ""),
                                     v.get("location_key"))
                except CfError as exc:
                    problems.append(f"{rel}: {exc}")
                    continue
                plan_row = planned.get(key)
                if plan_row is None or plan_row["challenge_run"] != challenge_run:
                    problems.append(f"{rel}: a verdict on {target} {v.get('item')} is not a "
                                    f"record {challenge_run} planned for {run_id}; ignored")
                    continue
                if plan_row.get("planned") != vocab.CHALLENGE_REQUIRED:
                    problems.append(f"{rel}: {target} {v.get('item')} was planned "
                                    f"{plan_row.get('planned')}; the verdict is ignored")
                    continue
                if key in verdicts:
                    raise CfError(f"{rel}: record {key[:12]} is judged twice")
                verdicts[key] = {**v, "source_id": sid, "challenge_run": challenge_run}
    return planned, verdicts, problems, gates


def _challenge_value(key: str, planned: dict, verdicts: dict) -> str | None:
    if key in verdicts:
        action = verdicts[key].get("action")
        return (vocab.CHALLENGE_SURVIVED if action in vocab.CHALLENGE_KEEP
                else vocab.CHALLENGE_DOWNGRADED)
    plan = planned.get(key)
    if plan is None:
        return None
    if plan.get("planned") == vocab.CHALLENGE_REQUIRED:
        return None
    return plan.get("planned")


def _passage_challenge(row: dict) -> str | None:
    """One value for a passage over all of its records.

    Its locations' (the refuted ones included: a refutation is a judged record),
    its absence's or unresolved outcome's, its coverage claim's (a refuted one
    included), and its prominence record's when a plan named one: owed
    (``None``) when any of them is owed, else ``challenge_pending`` when a rule now requires one nobody judged
    (:func:`_reopen`), else ``downgraded`` when any changed, else ``survived``
    when any was judged.
    """
    values = [loc.get("challenge") for loc in [*row["locations"], *(row.get("refuted") or [])]]
    for claim in (row.get("coverage"), row.get("coverage_refuted")):
        if claim is not None:
            values.append(claim.get("challenge"))
    if row.get("record_challenge", "unset") != "unset":
        values.append(row.get("record_challenge"))
    if row.get("prominence_challenge", "unset") != "unset":
        values.append(row.get("prominence_challenge"))
    if not values or any(v is None for v in values):
        return None
    if vocab.CHALLENGE_PENDING in values:
        return vocab.CHALLENGE_PENDING
    if row.get("passage_challenge_override"):
        return row["passage_challenge_override"]
    if vocab.CHALLENGE_DOWNGRADED in values:
        return vocab.CHALLENGE_DOWNGRADED
    if vocab.CHALLENGE_SURVIVED in values:
        return vocab.CHALLENGE_SURVIVED
    if vocab.CHALLENGE_SAMPLED_OUT in values:
        return vocab.CHALLENGE_SAMPLED_OUT
    return vocab.CHALLENGE_NOT_REQUIRED


# --------------------------------------------------------------------------
# outcomes
# --------------------------------------------------------------------------

def _problem(row: dict, problems: list[str], text: str) -> None:
    """A problem of one passage: listed, and put on its row so the export refuses it."""
    problems.append(text)
    row["problem"] = "; ".join(filter(None, [row.get("problem"), text]))


def _location(source_id: str, item: str, loc: dict, universe: inputs.Universe,
              db: sqlite3.Connection | None, problems: list[str]) -> dict | None:
    lib = str(loc.get("library") or "")
    name = str(loc.get("declaration") or "")
    if lib not in universe.libraries:
        problems.append(f"{source_id} {item}: library {lib!r} is not in universe {universe.id}")
        return None
    commit = universe.commit(lib)
    out = {"library": lib, "commit": commit, "declaration": name,
           "relation": loc.get("relation"), "rank": loc.get("rank"),
           "confidence": loc.get("confidence"), "note": loc.get("note") or "",
           "location_key": hashing.location_key(source_id, item, lib, commit, name)}
    if "proof_status" in loc:
        problems.append(f"{source_id} {item}: a document wrote proof_status for {lib}:{name}; "
                        "it is joined from the index, and the written value was ignored")
    if db is not None:
        row = query.declaration(db, lib, name, commit=commit)
        if row is None:
            problems.append(f"{source_id} {item}: {lib}:{name} is not in corpus.sqlite at "
                            f"{commit[:12]}")
            return None
        out.update({"proof_status": row["proof_status"], "statement": row["statement"],
                    "module": row.get("module"), "path": row.get("path"),
                    "line": row.get("line"), "end_line": row.get("end_line"),
                    "kind": row.get("kind")})
    return out


def _join_locations(sid: str, item: str, given: Iterable[dict], universe: inputs.Universe,
                    db, problems: list[str]) -> tuple[list[dict], list[str]]:
    """``(joined locations by rank, why each dropped one was dropped)``.

    A declaration listed twice keeps its best-ranked copy (and says so in
    ``problems``); a location that cannot be joined -- a library outside the
    universe, a declaration missing from the index -- is dropped, and its reason
    returned so the caller can put it on the passage's row.
    """
    dropped: list[str] = []
    joined: dict[str, dict] = {}
    for loc in given:
        before = len(problems)
        got = _location(sid, item, loc, universe, db, problems)
        if got is None:
            dropped.extend(problems[before:])
            continue
        key = got["location_key"]
        if key in joined:
            problems.append(f"{sid} {item}: {got['library']}:{got['declaration']} is listed "
                            "twice; the best-ranked copy is kept")
            if (got.get("rank") or 99) >= (joined[key].get("rank") or 99):
                continue
        joined[key] = got
    ordered = sorted(joined.values(),
                     key=lambda l: (l.get("rank") or 99, l["library"], l["declaration"]))
    return ordered, dropped


def build(paths: WorkspacePaths, run_id: str, *, challenge_runs: Iterable[str] = (),
          require_gate: bool = False, with_db: bool = True) -> dict:
    """Everything a locate run's harvest yields: rows, passage outcomes, counts.

    Returns ``{"run": run.yaml, "mode", "rows", "outcomes", "counts", "problems",
    "gate", "gates", "challenge_runs"}``; ``outcomes`` is empty for an inventory
    run, and ``gates`` is the receipt summary of every run read (``{run_id: {role,
    receipt, complete, gate_ok}}``).
    """
    run = run_yaml(paths, run_id)
    loc_block = run["locate"]
    mode = str(loc_block.get("mode") or run.get("mode"))
    sourceset_id = str((loc_block.get("sourceset") or {}).get("id") or "")
    uni_pin = loc_block.get("universe") or {}
    universe = inputs.load_universe(paths, str(uni_pin.get("id"))) if uni_pin.get("id") else None
    if universe is not None and uni_pin.get("sha256") and universe.sha256 != uni_pin["sha256"]:
        raise CfError(f"universe {universe.id} changed since run {run_id} was planned "
                      f"({str(uni_pin['sha256'])[:12]} -> {universe.sha256[:12]})",
                      hint="a run is judged against the universe frozen into its gate.json")
    docs, gate = _documents(paths, run_id, require_gate=require_gate)
    gates = {run_id: {"role": mode, **gate}}
    problems: list[str] = []
    result = {"run": run, "mode": mode, "rows": [], "outcomes": [], "problems": problems,
              "gate": gate, "gates": gates, "challenge_runs": list(challenge_runs),
              "sourceset": loc_block.get("sourceset"), "universe": uni_pin}
    if mode == vocab.LOCATE_INVENTORY:
        result["rows"] = finding_rows(run_id, docs, sourceset_id=sourceset_id,
                                      universe=universe)
        result["counts"] = _inventory_counts(docs, result["rows"])
        return result
    if mode != vocab.LOCATE:
        raise CfError(f"run {run_id} is a {mode} run; outcomes are built from a locate run",
                      hint="pass the locate run, and its challenge runs with --challenge-run")
    pins = _source_pins(paths, loc_block)
    inv_run = str(loc_block.get("inventory_run") or "")
    frozen: dict = {}
    if inv_run:
        inv = inventory(paths, inv_run, require_gate=require_gate)
        frozen = inv["passages"]
        gates[inv_run] = {"role": vocab.LOCATE_INVENTORY, **inv["gate"]}
    planned = _planned(paths, run_id, loc_block)
    c_planned, c_verdicts, c_problems, c_gates = challenge_values(
        paths, run_id, challenge_runs, require_gate=require_gate)
    problems.extend(c_problems)
    gates.update({rid: {"role": vocab.LOCATE_CHALLENGE, **g} for rid, g in c_gates.items()})
    # the CHECK database, pinned to the universe the run was planned in (whose
    # sha was compared above); never compared by file bytes
    db = (inputs.open_corpus(paths.corpus_db(universe.id), universe, blind=False)
          if (with_db and universe) else None)
    accepted: set[tuple[str, str]] = set()
    try:
        seen: set[tuple[str, str]] = set()
        outcomes: list[dict] = []
        for rel, doc in docs:
            if doc.get("mode") != vocab.LOCATE:
                problems.append(f"{rel} in locate run {run_id} is a {doc.get('mode')!r} document")
                continue
            sid = _source_of(rel, doc)
            doc_problems = []
            if doc.get("universe") != universe.id or doc.get("universe_sha256") != universe.sha256:
                doc_problems.append(f"{rel}: names universe {doc.get('universe')!r}, not "
                                    f"{universe.id} at {universe.sha256[:12]}")
            pin = pins.get(sid)
            if pin is not None and (doc.get("source_sha"), doc.get("layer_sha256")) != pin:
                doc_problems.append(f"{rel}: source_sha/layer_sha256 are not the rendering the "
                                    f"run pinned for {sid}")
            for p in doc.get("passages") or []:
                item = str(p.get("item") or "")
                key = (sid, cf_items.join_key(item))
                want = (planned.get(key) or {}).get("task_id")
                if planned and want and want != _task_of(rel):
                    problems.append(f"{rel}: passage {item} belongs to task {want}, not "
                                    f"{_task_of(rel)}; ignored")
                    continue
                if key in seen:
                    problems.append(f"{rel}: passage {item} is located twice in the run")
                    continue
                seen.add(key)
                accepted.add((rel, item))
                if planned and key not in planned:
                    problems.append(f"{rel}: passage {item} is not one the run planned")
                base = frozen.get(key) or planned.get(key) or {}
                row = _outcome(run_id, sid, item, p, doc, base, universe, db, problems,
                               task_id=_task_of(rel), inv_run=inv_run)
                for text in doc_problems:
                    _problem(row, problems, text)
                outcomes.append(row)
        for key, plan in sorted(planned.items()):
            if key in seen:
                continue
            base = frozen.get(key) or plan
            outcomes.append(_missing(run_id, key[0], plan["item"], base, plan))
        run_sources = set(loc_block.get("source_ids") or []) or {k[0] for k in planned}
        for key, base in sorted(frozen.items()):
            if key in seen or key in planned or base.get("scope") in vocab.LOCATED_SCOPES:
                continue
            if key[0] not in run_sources:
                continue
            outcomes.append(_scoped_out(run_id, key[0], base["item"], base, inv_run))
        layer_of = _Layers(paths, sourceset_id)
        for row in outcomes:
            _apply_challenge(row, c_planned, c_verdicts, universe, db, problems, layer_of)
            _derive(row, problems)
            for scratch in ("record_challenge", "prominence_challenge",
                            "passage_challenge_override", "coverage_lapsed"):
                row.pop(scratch, None)
        outcomes.sort(key=lambda r: (r["source_id"], r["span"][0] if r.get("span") else -1,
                                     r["item"]))
    finally:
        if db is not None:
            db.close()
    result["rows"] = finding_rows(run_id, docs, sourceset_id=sourceset_id, universe=universe,
                                  accepted=accepted)
    result["outcomes"] = outcomes
    result["counts"] = _locate_counts(outcomes, result["rows"])
    return result


def _source_pins(paths: WorkspacePaths, loc_block: dict) -> dict[str, tuple]:
    """``{source_id: (html_sha256, layer_sha256)}`` of the manifest the run pinned."""
    pin = loc_block.get("sourceset") or {}
    if not pin.get("id"):
        return {}
    manifest = inputs.pinned_manifest(paths, pin)
    return {sid: (e.get("html_sha256"), e.get("layer_sha256"))
            for sid, e in manifest.entries.items()}


def _planned(paths: WorkspacePaths, run_id: str, loc_block: dict) -> dict:
    """``{(source_id, join key): planned passage}``, refused when it is not what was planned."""
    planned: dict[tuple[str, str], dict] = {}
    for manifest in task_manifests(paths, run_id):
        for doc in manifest.get("documents") or []:
            for p in doc.get("passages") or []:
                planned[(doc["source_id"], cf_items.join_key(p["item"]))] = {
                    **p, "task_id": manifest.get("task_id")}
    want = loc_block.get("passages")
    if want is not None and int(want) != len(planned):
        raise CfError(f"run {run_id} planned {want} passage(s) and its task manifests hold "
                      f"{len(planned)}",
                      hint=f"restore {paths.run_dir(run_id) / 'planned.json'} (or the tend "
                           "root), or re-plan the run")
    return planned


def _base_fields(sid: str, item: str, base: dict) -> dict:
    return {"source_id": sid, "item": item,
            "passage_kind": base.get("passage_kind"), "prominence": base.get("prominence"),
            "scope": base.get("scope") or vocab.SCOPE_FORMAL_MATH,
            "span": list(base["span"]) if base.get("span") else None,
            "head_words": base.get("head_words"), "section": base.get("section"),
            "quote": base.get("quote"), "source_sha": base.get("source_sha"),
            "layer_sha256": base.get("layer_sha256")}


def _outcome(run_id: str, sid: str, item: str, p: dict, doc: dict, base: dict,
             universe: inputs.Universe, db, problems: list[str], *, task_id: str,
             inv_run: str) -> dict:
    outcome = p.get("outcome")
    own: list[str] = []
    if outcome not in vocab.LOCATE_OUTCOMES:
        own.append(f"{sid} {item}: outcome {outcome!r} is not one of "
                   f"{', '.join(sorted(vocab.LOCATE_OUTCOMES))}")
        outcome = vocab.OUTCOME_UNRESOLVED
    locations, dropped = _join_locations(sid, item, p.get("locations") or [], universe, db,
                                         problems)
    if outcome == vocab.OUTCOME_UNRESOLVED and locations:
        own.append(f"{sid} {item}: an unresolved passage has no locations "
                   f"({len(locations)} given)")
    receipt = p.get("receipt") if isinstance(p.get("receipt"), dict) else {}
    coverage = None
    if p.get("coverage") is not None:
        coverage, why = coverage_claim(p["coverage"], outcome, locations)
        if why:
            own.append(f"{sid} {item}: {why}")
    row = {**_base_fields(sid, item, base), **_stamp(doc), "outcome": outcome,
           "locations": locations, "coverage": coverage, "coverage_refuted": None,
           "absence": ({"receipt": receipt, "challenge": None}
                       if outcome == vocab.OUTCOME_ABSENT else None),
           "receipt": receipt, "reason": p.get("reason") or "",
           "unresolved_by": _unresolved_by(doc) if outcome == vocab.OUTCOME_UNRESOLVED else None,
           "missing": False, "refuted": [], "challenge": None, "status": None,
           "runs": {"inventory": inv_run or None, "locate": run_id, "challenge": None},
           "task_id": task_id}
    for text in own:
        _problem(row, problems, text)
    for text in dropped:
        row["problem"] = "; ".join(filter(None, [row.get("problem"), text]))
    return row


def coverage_claim(given, outcome, locations: list[dict]) -> tuple[dict | None, str]:
    """A locate document's coverage claim, its parts turned from ranks into location keys.

    ``(claim, "")`` -- ``{complete: True, parts: [location_key, ...], note,
    challenge: None}``, the parts in the order the document names them -- or
    ``(None, why)`` when the claim is not one the record set can carry: not on a
    located passage, not ``complete: true``, fewer than
    ``cflib.vocab.MIN_COVERAGE_PARTS`` distinct parts, a part that is the rank of
    no joined ``hasPart`` location, or no note (references/locate-documents.md
    section 3; the gate refuses each of these first).
    """
    if outcome != vocab.OUTCOME_LOCATED:
        return None, f"a coverage claim on an {outcome} passage is not one"
    if not isinstance(given, dict) or given.get("complete") is not True:
        return None, "a coverage claim says complete: true"
    ranks = given.get("parts")
    if not isinstance(ranks, list) or not all(isinstance(r, int) and not isinstance(r, bool)
                                              for r in ranks) \
            or len(set(ranks)) != len(ranks) or len(ranks) < vocab.MIN_COVERAGE_PARTS:
        return None, (f"a coverage claim names at least {vocab.MIN_COVERAGE_PARTS} distinct "
                      f"ranks, not {ranks!r}")
    by_rank = {loc.get("rank"): loc for loc in locations}
    keys = []
    for rank in ranks:
        loc = by_rank.get(rank)
        if loc is None or loc.get("relation") != vocab.HAS_PART:
            return None, (f"coverage part {rank} is not the rank of one of the passage's "
                          "joined hasPart locations")
        keys.append(loc["location_key"])
    note = " ".join(str(given.get("note") or "").split())
    if not note:
        return None, "a coverage claim says in its note how the parts state every clause"
    return {"complete": True, "parts": keys, "note": note, "challenge": None}, ""


def _missing(run_id: str, sid: str, item: str, base: dict, plan: dict) -> dict:
    return {**_base_fields(sid, item, base), "outcome": None, "locations": [],
            "coverage": None, "coverage_refuted": None,
            "absence": None, "receipt": {}, "reason": "no document landed for this passage",
            "unresolved_by": None, "missing": True, "refuted": [], "challenge": None,
            "status": None, "runs": {"inventory": None, "locate": run_id, "challenge": None},
            "task_id": plan.get("task_id")}


def _scoped_out(run_id: str, sid: str, item: str, base: dict, inv_run: str) -> dict:
    return {**_base_fields(sid, item, base), "outcome": None, "locations": [],
            "coverage": None, "coverage_refuted": None,
            "absence": None, "receipt": {}, "reason": f"scoped {base.get('scope')}",
            "unresolved_by": None, "missing": False, "refuted": [], "challenge": None,
            "status": None, "runs": {"inventory": inv_run, "locate": run_id, "challenge": None},
            "task_id": None}


def _apply_challenge(row: dict, planned: dict, verdicts: dict, universe, db,
                     problems: list[str], layer_of=None) -> None:
    sid, item = row["source_id"], row["item"]
    outcome_seen = row["outcome"]
    # locations first: their own records
    kept = []
    for loc in row["locations"]:
        key = loc["location_key"]
        plan = planned.get(key)
        if plan is not None and plan.get("relation") != loc["relation"]:
            loc["challenge"] = None
            _problem(row, problems, f"{sid} {item}: {loc['library']}:{loc['declaration']} is "
                                    f"{loc['relation']} and the challenge planned it as "
                                    f"{plan.get('relation')}; it is owed again")
            kept.append(loc)
            continue
        verdict = verdicts.get(key)
        loc["challenge"] = _challenge_value(key, planned, verdicts)
        if verdict is not None:
            row["runs"]["challenge"] = verdict["challenge_run"]
            action = verdict.get("action")
            if action == "refute":
                row["refuted"].append({**loc, "witness": verdict.get("witness", "")})
                row["passage_challenge_override"] = vocab.CHALLENGE_DOWNGRADED
                continue
            if action == "downgrade":
                relation = verdict.get("relation")
                if relation not in vocab.LOCATE_RELATIONS or \
                        RELATION_STRENGTH[relation] <= RELATION_STRENGTH[loc["relation"]]:
                    loc["challenge"] = None
                    _problem(row, problems, f"{sid} {item}: a downgrade of "
                                            f"{loc['library']}:{loc['declaration']} from "
                                            f"{loc['relation']} to {relation!r} is not weaker; "
                                            "it is ignored and the record owed")
                else:
                    loc["relation"] = relation
                    if relation == vocab.CLOSE_MATCH and not str(loc.get("note") or "").strip():
                        loc["note"] = downgrade_note(verdict, layer_of(sid) if layer_of else None)
        kept.append(loc)
    row["locations"] = _renumbered(kept)
    if row.get("coverage") is not None:
        _apply_coverage(row, planned, verdicts, problems)
    # the passage-level record: an absence, an unresolved outcome, a scope or a prominence
    target = {vocab.OUTCOME_ABSENT: "absence", vocab.OUTCOME_UNRESOLVED: "unresolved"}.get(
        row["outcome"])
    if target is None and row["outcome"] is None and not row["missing"]:
        target = "scope"
    targets = [t for t in (target, "prominence") if t]
    for t in targets:
        key = record_key(t, sid, item)
        plan = planned.get(key)
        if plan is not None and plan.get("outcome") != outcome_seen:
            value = None
            _problem(row, problems, f"{sid} {item}: the {t} record was planned for outcome "
                                    f"{plan.get('outcome')!r} and the passage is "
                                    f"{outcome_seen!r}; it is owed again")
            if t == "prominence":
                row["prominence_challenge"] = None
            else:
                row["record_challenge"] = None
                if row["absence"] is not None:
                    row["absence"]["challenge"] = None
            continue
        value = _challenge_value(key, planned, verdicts)
        if t in ("absence", "unresolved", "scope"):
            row["record_challenge"] = value
            if row["absence"] is not None:
                row["absence"]["challenge"] = value
        elif key in planned or key in verdicts:
            row["prominence_challenge"] = value
        verdict = verdicts.get(key)
        if verdict is None:
            continue
        row["runs"]["challenge"] = verdict["challenge_run"]
        action = verdict.get("action")
        if action == "overturn" and t in ("absence", "unresolved", "scope"):
            new, dropped = _join_locations(sid, item, verdict.get("locations") or [], universe,
                                           db, problems)
            for text in dropped:
                row["problem"] = "; ".join(filter(None, [row.get("problem"), text]))
            # the overturned record is the absence (or outcome, or scope), and it is
            # gone; each location the challenger added is its own witnessed, gated
            # finding -- challenged and standing, never a weakened one (field defect X5)
            for loc in new:
                loc["challenge"] = vocab.CHALLENGE_SURVIVED
            row["record_challenge"] = vocab.CHALLENGE_DOWNGRADED
            row["passage_challenge_override"] = vocab.CHALLENGE_DOWNGRADED
            if t == "scope":
                row["scope"] = vocab.SCOPE_FORMAL_MATH
                if not new:
                    _problem(row, problems, f"{sid} {item}: scope overturned to formal_math "
                                            "with no location; the passage needs a locate pass")
                    continue
            elif not new:
                _problem(row, problems, f"{sid} {item}: an overturn with no location that exists")
                continue
            row["locations"] = _overturned(new, row["locations"])
            row["outcome"] = vocab.OUTCOME_LOCATED
            row["scope"] = vocab.SCOPE_FORMAL_MATH
            row["absence"] = None
            row["unresolved_by"] = None
        elif t == "prominence" and action == "overturn":
            if verdict.get("prominence") in vocab.PROMINENCE:
                row["prominence"] = verdict["prominence"]
            row["passage_challenge_override"] = vocab.CHALLENGE_DOWNGRADED
            if row["prominence"] in vocab.REFUTE_BY_DEFAULT_PROMINENCE:
                _reopen(row)


def _apply_coverage(row: dict, planned: dict, verdicts: dict, problems: list[str]) -> None:
    """A coverage claim's challenge (the maintainer's decision D1).

    Every claim is refuted by default: ``confirm`` makes it ``survived``;
    ``refute`` removes it (kept under ``coverage_refuted``, ``downgraded``) and
    leaves its parts ``hasPart`` locations.  A verdict applies only while the
    claim names the parts the plan saw, and an action outside
    ``cflib.vocab.COVERAGE_ACTIONS`` leaves it owed.  A claim whose part a
    location verdict refuted or downgraded no longer holds: it upgrades nothing
    and the passage carries a problem, which the export refuses, until the claim
    is challenged again.
    """
    sid, item, claim = row["source_id"], row["item"], row["coverage"]
    key = record_key(vocab.COVERAGE_TARGET, sid, item)
    plan = planned.get(key)
    verdict = verdicts.get(key)
    if plan is not None and sorted(plan.get("parts") or []) != sorted(claim["parts"]):
        claim["challenge"] = None
        _problem(row, problems, f"{sid} {item}: the coverage claim was planned with other "
                                "parts; it is owed again")
    elif verdict is not None and verdict.get("action") not in vocab.COVERAGE_ACTIONS:
        claim["challenge"] = None
        _problem(row, problems, f"{sid} {item}: a coverage verdict is confirm or refute, not "
                                f"{verdict.get('action')!r}; the claim is owed")
    else:
        claim["challenge"] = _challenge_value(key, planned, verdicts)
        if verdict is not None:
            row["runs"]["challenge"] = verdict["challenge_run"]
            if verdict.get("action") == "refute":
                row["coverage_refuted"] = {**claim, "challenge": vocab.CHALLENGE_DOWNGRADED,
                                           "witness": verdict.get("witness", "")}
                row["coverage"] = None
                row["passage_challenge_override"] = vocab.CHALLENGE_DOWNGRADED
                return
    standing = {loc["location_key"]: loc for loc in row["locations"]
                if loc["relation"] == vocab.HAS_PART}
    gone = [k for k in claim["parts"] if k not in standing]
    if gone:
        named = {loc["location_key"]: f"{loc['library']}:{loc['declaration']}"
                 for loc in [*row["locations"], *row["refuted"]]}
        row["coverage_lapsed"] = True
        _problem(row, problems, f"{sid} {item}: the coverage claim names "
                                f"{', '.join(named.get(k, k[:12]) for k in gone)}, which the "
                                "challenge refuted or downgraded; the claim no longer holds "
                                "and is to be challenged again")


def _reopen(row: dict) -> None:
    """A prominence overturned to ``subject``/``central`` reopens the passage's records.

    The challenge plan judged the passage's records under ``supporting``: its
    locations were sampled or not required, its absence or unresolved outcome
    was not hunted.  Under the new prominence section 5 refutes every location
    by default and hunts the absence, so each record no verdict judged
    (``not_required``, ``sampled_out``) becomes ``challenge_pending`` -- never
    left ``not_required``, which a consumer would read as "no rule applied"
    (field defect CH2: two central absences exported that nobody hunted).  A
    record a verdict judged keeps its value.
    """
    for loc in row["locations"]:
        if loc.get("challenge") in vocab.CHALLENGE_UNJUDGED:
            loc["challenge"] = vocab.CHALLENGE_PENDING
    if row.get("record_challenge", "unset") in vocab.CHALLENGE_UNJUDGED:
        row["record_challenge"] = vocab.CHALLENGE_PENDING
    if row.get("absence") is not None and \
            row["absence"].get("challenge") in vocab.CHALLENGE_UNJUDGED:
        row["absence"]["challenge"] = vocab.CHALLENGE_PENDING
    if row.get("coverage") is not None and \
            row["coverage"].get("challenge") in vocab.CHALLENGE_UNJUDGED:
        row["coverage"]["challenge"] = vocab.CHALLENGE_PENDING


#: the longest note a downgrade's witness lends a closeMatch, in characters
NOTE_FROM_WITNESS_CHARS = 300
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


#: abbreviations whose full stop ends no sentence of a witness ("i.e. F(x) = ...")
_ABBREVIATIONS = frozenset({"i.e.", "e.g.", "cf.", "resp.", "etc.", "viz.", "vs.", "w.r.t."})


def witness_sentences(witness: str) -> list[str]:
    """A witness's sentences, whitespace collapsed, each cut at a word at
    :data:`NOTE_FROM_WITNESS_CHARS`.

    A sentence ends at ``.``, ``!`` or ``?`` before whitespace, except after an
    abbreviation (:data:`_ABBREVIATIONS`): the field witness's "-- i.e. F(x) =
    ..." is one sentence, not two.
    """
    text = " ".join(str(witness or "").split())
    sentences: list[str] = []
    start = 0
    for brk in _SENTENCE_END.finditer(text):
        last = text[start:brk.start()].rsplit(" ", 1)[-1].lstrip("([{'\"")
        if last.lower() in _ABBREVIATIONS:
            continue
        sentences.append(text[start:brk.start()])
        start = brk.end()
    if text[start:]:
        sentences.append(text[start:])
    out = []
    for sentence in sentences:
        if len(sentence) > NOTE_FROM_WITNESS_CHARS:
            cut = sentence.rfind(" ", 0, NOTE_FROM_WITNESS_CHARS)
            sentence = (sentence[:cut if cut > 0 else NOTE_FROM_WITNESS_CHARS].rstrip(" ,;:")
                        + "\u2026")
        out.append(sentence)
    return out


def downgrade_note(verdict: dict, layer: str | None = None) -> str:
    """The note of a location a challenge downgraded to ``closeMatch``.

    A ``closeMatch`` names its convention difference in ``note`` (proposal
    section 2.6), and a downgrade from ``exactMatch`` is exactly the challenger
    saying there is one: the verdict's own ``note`` when it gives one, else a
    sentence of its ``witness`` (:func:`witness_sentences`) -- the first one that
    carries no more of the source verbatim than the export's quote cap
    (``records.verbatim_run`` against the passage's ``layer``).  A witness opens
    by quoting the passage as often as not (field case FTC #19a718e1: "The
    passage says only that '<19 words of the article>'"), and a note is exported,
    so that sentence is passed over for the next.  Without a layer, or when every
    sentence quotes past the cap, the first sentence, which the export refuses by
    name.  Empty only when the verdict says nothing, which the gate refuses and
    the export would refuse by name.
    """
    note = " ".join(str(verdict.get("note") or "").split())
    if note:
        return note
    sentences = witness_sentences(str(verdict.get("witness") or ""))
    if not sentences:
        return ""
    if layer:
        for sentence in sentences:
            if records.verbatim_run(sentence, layer) is None:
                return sentence
    return sentences[0]


def _renumbered(locations: list[dict]) -> list[dict]:
    """The locations in rank order, ranked 1..n again.

    A refuted location leaves the passage, and the ones after it move up: the
    export's ranks are the order a reader sees, never a gap where a refuted
    declaration stood (field defect X4: two passages exported ranks [1, 3]).
    """
    ordered = sorted(locations, key=lambda l: (l.get("rank") or 99, l["library"],
                                               l["declaration"]))
    for rank, loc in enumerate(ordered, 1):
        loc["rank"] = rank
    return ordered


def _overturned(new: list[dict], old: list[dict]) -> list[dict]:
    """An overturn's locations: the challenger's in its own rank order, then the kept
    ``relatedMatch`` ones, renumbered 1..n and capped at
    ``cflib.vocab.MAX_LOCATIONS_PER_PASSAGE`` -- so rank 1 is the challenger's best,
    never a related location the absence sat beside.
    """
    ordered = sorted(new, key=lambda l: (l.get("rank") or 99, l["library"], l["declaration"]))
    keys = {l["location_key"] for l in ordered}
    ordered += [l for l in sorted(old, key=lambda l: (l.get("rank") or 99, l["library"],
                                                      l["declaration"]))
                if l["relation"] == vocab.RELATED_MATCH and l["location_key"] not in keys]
    ordered = ordered[:vocab.MAX_LOCATIONS_PER_PASSAGE]
    for rank, loc in enumerate(ordered, 1):
        loc["rank"] = rank
    return ordered


def _derive(row: dict, problems: list[str]) -> None:
    row["challenge"] = None if row["missing"] else _passage_challenge(row)
    if row["outcome"] in (None, vocab.OUTCOME_UNRESOLVED) or row["missing"]:
        row["status"] = None
        return
    joined = [l for l in row["locations"] if l.get("proof_status")]
    # the claim as its records will carry it (records.coverage_ext), so the status is the
    # one the exported claim gives; an owed claim (no challenge value) is no record's
    # claim and upgrades nothing, and a lapsed one is named as a problem already
    claim = row.get("coverage")
    coverage = None
    if claim is not None and claim.get("challenge") is not None \
            and not row.get("coverage_lapsed") and len(joined) == len(row["locations"]):
        coverage = records.coverage_ext(claim)
    try:
        row["status"] = vocab.derive_status(row["passage_kind"] or vocab.PASSAGE_OTHER,
                                            joined, row["absence"] is not None, coverage)
    except CfError as exc:
        _problem(row, problems, f"{row['source_id']} {row['item']}: {exc}")
        row["status"] = None


def _inventory_counts(docs, rows) -> dict:
    kinds = Counter(r["kind"] for r in rows)
    return {"documents": len(docs), "passages": kinds.get(vocab.PASSAGE, 0),
            "unresolved_documents": kinds.get(vocab.UNRESOLVED, 0),
            "attempt_capped": sum(1 for r in rows if r.get("by") == vocab.UNRESOLVED_ATTEMPT_CAP),
            "by_scope": dict(sorted(Counter(r.get("scope") for r in rows
                                            if r["kind"] == vocab.PASSAGE).items())),
            "by_passage_kind": dict(sorted(Counter(r.get("passage_kind") for r in rows
                                                   if r["kind"] == vocab.PASSAGE).items()))}


def _locate_counts(outcomes: list[dict], rows: list[dict]) -> dict:
    located = [o for o in outcomes if o["outcome"] is not None]
    by_library = Counter(l["library"] for o in outcomes for l in o["locations"])
    return {
        "passages": len(outcomes),
        "by_outcome": dict(sorted(Counter(o["outcome"] or "none" for o in outcomes).items())),
        "by_status": dict(sorted(Counter(o["status"] or "none" for o in located).items())),
        "by_library": dict(sorted(by_library.items())),
        "by_challenge": dict(sorted(Counter(o["challenge"] or "owed" for o in located).items())),
        # an answered passage with no status counts as unresolved (references/vocabulary.md
        # section 1.11): the worker's, and one a challenge left without a location that counts
        "unresolved": sum(1 for o in located if o["status"] is None),
        "attempt_capped": sum(1 for o in outcomes
                              if o.get("unresolved_by") == vocab.UNRESOLVED_ATTEMPT_CAP),
        "missing": sum(1 for o in outcomes if o["missing"]),
        "scoped_out": sum(1 for o in outcomes if o["outcome"] is None and not o["missing"]),
        "problems": sum(1 for o in outcomes if o.get("problem")),
        "rows": dict(sorted(Counter(r["kind"] for r in rows).items())),
    }


def outcome_row(row: dict) -> dict:
    """The section 10 row as written to ``reports/locate-outcomes.<run>.jsonl``."""
    keep = ("source_id", "item", "passage_kind", "prominence", "scope", "outcome", "locations",
            "coverage", "absence", "challenge", "status", "runs", "span", "head_words",
            "section", "source_sha", "layer_sha256", "reason", "unresolved_by", "missing",
            "refuted", "coverage_refuted", "task_id")
    out = {k: row.get(k) for k in keep}
    if row.get("problem"):
        out["problem"] = row["problem"]
    return out
