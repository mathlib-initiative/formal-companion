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
   the affected records ``downgraded``).  A record the challenge plan did not
   require keeps the value the plan recorded (``not_required``,
   ``sampled_out``); a record it required and no verdict judged has challenge
   ``None`` -- owed, which the export refuses (hard rule 5).
3. **Status.**  ``proof_status`` and the ``statement`` flag are joined from
   ``corpus.sqlite`` (never read from a document), and ``cflib.vocab.
   derive_status`` computes the passage's status over the surviving locations
   and the absence.  An unresolved passage has no status and is counted on its
   own, never as not formalized.

A passage the run planned but no document carries (a task that never merged)
is kept with ``outcome: None`` and ``missing: true``: the safety metric counts
it as a miss, so dropping it here would hide one.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from cflib import hashing, items as cf_items, vocab
from cflib.errors import CfError
from cflib.jsonio import read_json
from cflib.paths import WorkspacePaths
from cflib.yamlio import read_yaml

from . import inputs

CHALLENGE_PLAN_SCHEMA = "cf-locate-challenge-plan/1"
REQUIRED = "required"


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
    """The run's task manifests, as planned (``tend-root/gate/tasks/*.json``)."""
    directory = paths.tend_root(run_id) / "gate" / "tasks"
    if not directory.is_dir():
        return []
    return [read_json(p) for p in sorted(directory.glob("*.json"))]


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
        raise CfError(f"{rel}: the document for {sid} lives at findings/<task-id>/{sid}.json")
    return sid


def _task_of(rel: str) -> str:
    parts = rel.split("/")
    return parts[-2] if len(parts) >= 2 else ""


def inventory(paths: WorkspacePaths, run_id: str, *, require_gate: bool = True) -> dict:
    """The frozen passage table of an inventory run: ``{(source_id, item): passage}``.

    Returns ``{"passages": {...}, "unresolved": {source_id: reason}, "documents": n,
    "gate": {...}}``.  Two documents for one source, or one passage twice, are
    refused: a passage table is frozen once.
    """
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
            passages[key] = {**row, "source_id": sid, "item": item,
                             "source_sha": doc.get("source_sha"),
                             "layer_sha256": doc.get("layer_sha256"), "task_id": _task_of(rel)}
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
                 universe: inputs.Universe | None) -> list[dict]:
    """Every document's finding rows, in document order."""
    rows: list[dict] = []
    for rel, doc in docs:
        mode = doc.get("mode")
        sid = _source_of(rel, doc)
        if mode == vocab.LOCATE_INVENTORY:
            if isinstance(doc.get("unresolved"), dict):
                rows.append(_row(run_id, sid, "", vocab.UNRESOLVED, sourceset_id, [],
                                 by=doc["unresolved"].get("by")))
                continue
            for p in doc.get("passages") or []:
                rows.append(_row(run_id, sid, str(p.get("item") or ""), vocab.PASSAGE,
                                 sourceset_id, [], passage_kind=p.get("passage_kind"),
                                 scope=p.get("scope")))
            continue
        if mode != vocab.LOCATE:
            continue
        if universe is None:
            raise CfError("a locate document's rows need the run's universe")
        for p in doc.get("passages") or []:
            item = str(p.get("item") or "")
            outcome = p.get("outcome")
            for loc in p.get("locations") or []:
                lib, name = str(loc.get("library")), str(loc.get("declaration"))
                commit = universe.libraries.get(lib, {}).get("commit", "")
                rows.append(_row(run_id, sid, item, vocab.LOCATION, lib,
                                 [decl_token(lib, name, commit)],
                                 relation=loc.get("relation")))
            if outcome == vocab.OUTCOME_ABSENT:
                rows.append(_row(run_id, sid, item, vocab.ABSENCE, universe.id, []))
            elif outcome == vocab.OUTCOME_UNRESOLVED:
                rows.append(_row(run_id, sid, item, vocab.UNRESOLVED, universe.id, [],
                                 by=(doc.get("unresolved") or {}).get("by")))
    return rows


# --------------------------------------------------------------------------
# challenge
# --------------------------------------------------------------------------

def challenge_values(paths: WorkspacePaths, challenge_runs: Iterable[str], *,
                     require_gate: bool = True) -> tuple[dict, dict, list[str]]:
    """``(planned, verdicts, problems)`` over every challenge run given.

    ``planned`` is ``{record key: plan row}`` from each run's
    ``challenge-plan.json``; ``verdicts`` is ``{record key: verdict}`` from its
    harvested documents.  One record planned or judged twice is refused.
    """
    planned: dict[str, dict] = {}
    verdicts: dict[str, dict] = {}
    problems: list[str] = []
    for run_id in challenge_runs:
        plan_path = paths.run_dir(run_id) / "challenge-plan.json"
        if not plan_path.is_file():
            raise CfError(f"challenge run {run_id!r} has no challenge-plan.json",
                          hint="plan it with `cf plan-locate --preset locate-challenge`")
        plan = read_json(plan_path)
        if plan.get("schema") != CHALLENGE_PLAN_SCHEMA:
            raise CfError(f"{plan_path} is not a {CHALLENGE_PLAN_SCHEMA} document")
        for rec in plan.get("records") or []:
            key = rec["key"]
            if key in planned:
                raise CfError(f"record {key[:12]} is planned by two challenge runs")
            planned[key] = {**rec, "challenge_run": run_id}
        docs, _gate = _documents(paths, run_id, require_gate=require_gate)
        for rel, doc in docs:
            if doc.get("mode") != vocab.LOCATE_CHALLENGE:
                problems.append(f"{rel} in challenge run {run_id} is not a challenge document")
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
                if key in verdicts:
                    raise CfError(f"{rel}: record {key[:12]} is judged twice")
                verdicts[key] = {**v, "source_id": sid, "challenge_run": run_id}
    return planned, verdicts, problems


def _challenge_value(key: str, planned: dict, verdicts: dict) -> str | None:
    if key in verdicts:
        action = verdicts[key].get("action")
        return (vocab.CHALLENGE_SURVIVED if action in vocab.CHALLENGE_KEEP
                else vocab.CHALLENGE_DOWNGRADED)
    plan = planned.get(key)
    if plan is None:
        return None
    if plan.get("planned") == REQUIRED:
        return None
    return plan.get("planned")


def _passage_challenge(row: dict) -> str | None:
    """One value for a passage over all of its records.

    Its locations', its absence's or unresolved outcome's, and its prominence
    record's when a plan named one: owed (``None``) when any of them is owed,
    else ``downgraded`` when any changed, else ``survived`` when any was judged.
    """
    values = [loc.get("challenge") for loc in row["locations"]]
    if row.get("record_challenge", "unset") != "unset":
        values.append(row.get("record_challenge"))
    if row.get("prominence_challenge", "unset") != "unset":
        values.append(row.get("prominence_challenge"))
    if not values or any(v is None for v in values):
        return None
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

def _location(source_id: str, item: str, loc: dict, universe: inputs.Universe,
              db: inputs.CorpusDB | None, problems: list[str]) -> dict | None:
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
        row = db.decl(lib, commit, name)
        if row is None:
            problems.append(f"{source_id} {item}: {lib}:{name} is not in corpus.sqlite at "
                            f"{commit[:12]}")
            return None
        out.update({"proof_status": row["proof_status"], "statement": bool(row["statement"]),
                    "module": row.get("module"), "path": row.get("path"),
                    "line": row.get("line"), "end_line": row.get("end_line"),
                    "kind": row.get("kind")})
    return out


def build(paths: WorkspacePaths, run_id: str, *, challenge_runs: Iterable[str] = (),
          require_gate: bool = False, with_db: bool = True) -> dict:
    """Everything a locate run's harvest yields: rows, passage outcomes, counts.

    Returns ``{"run": run.yaml, "mode", "rows", "outcomes", "counts", "problems",
    "gate", "challenge_runs"}``; ``outcomes`` is empty for an inventory run.
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
    rows = finding_rows(run_id, docs, sourceset_id=sourceset_id, universe=universe)
    problems: list[str] = []
    result = {"run": run, "mode": mode, "rows": rows, "outcomes": [], "problems": problems,
              "gate": gate, "challenge_runs": list(challenge_runs),
              "sourceset": loc_block.get("sourceset"), "universe": uni_pin}
    if mode == vocab.LOCATE_INVENTORY:
        result["counts"] = _inventory_counts(docs, rows)
        return result
    if mode != vocab.LOCATE:
        raise CfError(f"run {run_id} is a {mode} run; outcomes are built from a locate run",
                      hint="pass the locate run, and its challenge runs with --challenge-run")
    inv_run = str(loc_block.get("inventory_run") or "")
    frozen = inventory(paths, inv_run, require_gate=require_gate)["passages"] if inv_run else {}
    planned: dict[tuple[str, str], dict] = {}
    for manifest in task_manifests(paths, run_id):
        for doc in manifest.get("documents") or []:
            for p in doc.get("passages") or []:
                planned[(doc["source_id"], cf_items.join_key(p["item"]))] = {
                    **p, "task_id": manifest.get("task_id")}
    c_planned, c_verdicts, c_problems = challenge_values(paths, challenge_runs,
                                                        require_gate=require_gate)
    problems.extend(c_problems)
    db = inputs.CorpusDB(paths.corpus_db(universe.id)) if (with_db and universe) else None
    try:
        if db is not None:
            want = str(uni_pin.get("corpus_db_sha256") or "")
            if want and db.sha256() != want:
                raise CfError(f"corpus.sqlite of {universe.id} is not the one run {run_id} was "
                              "gated against",
                              hint="proof_status is joined from the database the gate read")
        seen: set[tuple[str, str]] = set()
        outcomes: list[dict] = []
        for rel, doc in docs:
            if doc.get("mode") != vocab.LOCATE:
                problems.append(f"{rel} in locate run {run_id} is a {doc.get('mode')!r} document")
                continue
            sid = _source_of(rel, doc)
            if doc.get("universe") != universe.id or doc.get("universe_sha256") != universe.sha256:
                problems.append(f"{rel}: names universe {doc.get('universe')!r}, not "
                                f"{universe.id} at {universe.sha256[:12]}")
            for p in doc.get("passages") or []:
                item = str(p.get("item") or "")
                key = (sid, cf_items.join_key(item))
                if key in seen:
                    problems.append(f"{rel}: passage {item} is located twice in the run")
                    continue
                seen.add(key)
                if planned and key not in planned:
                    problems.append(f"{rel}: passage {item} is not one the run planned")
                base = frozen.get(key) or planned.get(key) or {}
                outcomes.append(_outcome(run_id, sid, item, p, doc, base, universe, db,
                                         problems, task_id=_task_of(rel), inv_run=inv_run))
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
        for row in outcomes:
            _apply_challenge(row, c_planned, c_verdicts, universe, db, problems)
            _derive(row, problems)
            for scratch in ("record_challenge", "prominence_challenge",
                            "passage_challenge_override"):
                row.pop(scratch, None)
        outcomes.sort(key=lambda r: (r["source_id"], r["span"][0] if r.get("span") else -1,
                                     r["item"]))
    finally:
        if db is not None:
            db.close()
    result["outcomes"] = outcomes
    result["counts"] = _locate_counts(outcomes, rows)
    return result


def _base_fields(sid: str, item: str, base: dict) -> dict:
    return {"source_id": sid, "item": item,
            "passage_kind": base.get("passage_kind"), "prominence": base.get("prominence"),
            "scope": base.get("scope") or vocab.SCOPE_FORMAL_MATH,
            "span": list(base["span"]) if base.get("span") else None,
            "head_words": base.get("head_words"), "section": base.get("section"),
            "quote": base.get("quote")}


def _outcome(run_id: str, sid: str, item: str, p: dict, doc: dict, base: dict,
             universe: inputs.Universe, db, problems: list[str], *, task_id: str,
             inv_run: str) -> dict:
    outcome = p.get("outcome")
    if outcome not in vocab.LOCATE_OUTCOMES:
        problems.append(f"{sid} {item}: outcome {outcome!r} is not one of "
                        f"{', '.join(sorted(vocab.LOCATE_OUTCOMES))}")
        outcome = vocab.OUTCOME_UNRESOLVED
    locations = []
    for loc in p.get("locations") or []:
        joined = _location(sid, item, loc, universe, db, problems)
        if joined is not None:
            locations.append(joined)
    locations.sort(key=lambda l: (l.get("rank") or 99, l["library"], l["declaration"]))
    receipt = p.get("receipt") if isinstance(p.get("receipt"), dict) else {}
    row = {**_base_fields(sid, item, base), "outcome": outcome, "locations": locations,
           "absence": ({"receipt": receipt, "challenge": None}
                       if outcome == vocab.OUTCOME_ABSENT else None),
           "receipt": receipt, "reason": p.get("reason") or "",
           "unresolved_by": (doc.get("unresolved") or {}).get("by")
           if outcome == vocab.OUTCOME_UNRESOLVED else None,
           "missing": False, "refuted": [], "challenge": None, "status": None,
           "runs": {"inventory": inv_run or None, "locate": run_id, "challenge": None},
           "task_id": task_id}
    return row


def _missing(run_id: str, sid: str, item: str, base: dict, plan: dict) -> dict:
    return {**_base_fields(sid, item, base), "outcome": None, "locations": [],
            "absence": None, "receipt": {}, "reason": "no document landed for this passage",
            "unresolved_by": None, "missing": True, "refuted": [], "challenge": None,
            "status": None, "runs": {"inventory": None, "locate": run_id, "challenge": None},
            "task_id": plan.get("task_id")}


def _scoped_out(run_id: str, sid: str, item: str, base: dict, inv_run: str) -> dict:
    return {**_base_fields(sid, item, base), "outcome": None, "locations": [],
            "absence": None, "receipt": {}, "reason": f"scoped {base.get('scope')}",
            "unresolved_by": None, "missing": False, "refuted": [], "challenge": None,
            "status": None, "runs": {"inventory": inv_run, "locate": run_id, "challenge": None},
            "task_id": None}


def _apply_challenge(row: dict, planned: dict, verdicts: dict, universe, db,
                     problems: list[str]) -> None:
    sid, item = row["source_id"], row["item"]
    # locations first: their own records
    kept = []
    for loc in row["locations"]:
        key = loc["location_key"]
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
                if relation not in vocab.LOCATE_RELATIONS:
                    problems.append(f"{sid} {item}: a downgrade to {relation!r}")
                else:
                    loc["relation"] = relation
        kept.append(loc)
    row["locations"] = kept
    # the passage-level record: an absence, an unresolved outcome, a scope or a prominence
    target = {vocab.OUTCOME_ABSENT: "absence", vocab.OUTCOME_UNRESOLVED: "unresolved"}.get(
        row["outcome"])
    if target is None and row["outcome"] is None and not row["missing"]:
        target = "scope"
    targets = [t for t in (target, "prominence") if t]
    for t in targets:
        key = record_key(t, sid, item)
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
            new = []
            for loc in verdict.get("locations") or []:
                joined = _location(sid, item, loc, universe, db, problems)
                if joined is not None:
                    joined["challenge"] = vocab.CHALLENGE_DOWNGRADED
                    new.append(joined)
            if not new:
                problems.append(f"{sid} {item}: an overturn with no location that exists")
                continue
            row["locations"] = [l for l in row["locations"]
                                if l["relation"] == vocab.RELATED_MATCH] + new
            row["outcome"] = vocab.OUTCOME_LOCATED
            row["scope"] = vocab.SCOPE_FORMAL_MATH
            row["absence"] = None
            row["record_challenge"] = vocab.CHALLENGE_DOWNGRADED
            row["passage_challenge_override"] = vocab.CHALLENGE_DOWNGRADED
        elif t == "prominence" and action in vocab.CHALLENGE_CHANGE:
            if verdict.get("prominence") in vocab.PROMINENCE:
                row["prominence"] = verdict["prominence"]
            row["passage_challenge_override"] = vocab.CHALLENGE_DOWNGRADED


def _derive(row: dict, problems: list[str]) -> None:
    row["challenge"] = None if row["missing"] else _passage_challenge(row)
    if row["outcome"] is None or row["missing"]:
        row["status"] = None
        return
    try:
        row["status"] = vocab.derive_status(row["passage_kind"] or vocab.PASSAGE_OTHER,
                                            [l for l in row["locations"]
                                             if l.get("proof_status")],
                                            row["absence"] is not None)
    except CfError as exc:
        problems.append(f"{row['source_id']} {row['item']}: {exc}")
        row["status"] = None
        row["problem"] = str(exc)


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
        "unresolved": sum(1 for o in outcomes if o["outcome"] == vocab.OUTCOME_UNRESOLVED),
        "attempt_capped": sum(1 for o in outcomes
                              if o.get("unresolved_by") == vocab.UNRESOLVED_ATTEMPT_CAP),
        "missing": sum(1 for o in outcomes if o["missing"]),
        "scoped_out": sum(1 for o in outcomes if o["outcome"] is None and not o["missing"]),
        "rows": dict(sorted(Counter(r["kind"] for r in rows).items())),
    }


def outcome_row(row: dict) -> dict:
    """The section 10 row as written to ``reports/locate-outcomes.<run>.jsonl``."""
    keep = ("source_id", "item", "passage_kind", "prominence", "scope", "outcome", "locations",
            "absence", "challenge", "status", "runs", "span", "head_words", "section",
            "reason", "unresolved_by", "missing", "refuted", "task_id")
    out = {k: row.get(k) for k in keep}
    if row.get("problem"):
        out["problem"] = row["problem"]
    return out
