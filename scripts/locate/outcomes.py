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
   it ``survived`` and confirms its parts (each part no verdict judged,
   ``not_required`` or ``sampled_out``, is ``survived`` too), ``refute``
   removes it and leaves its parts ``hasPart``.  A record the challenge plan
   did not require keeps the value the plan recorded (``not_required``,
   ``sampled_out``), with two exceptions: a part of a confirmed claim is
   ``survived``, as just said, and a prominence overturn to ``subject`` or
   ``central`` that put a record under a rule requiring it makes it
   ``challenge_pending``.  The reopen wins: it is applied first, and a part it
   made pending stays pending, owed its own verdict under the stricter rule,
   while the claim stays ``survived``.  A record the plan required and no
   verdict judged has challenge ``None`` -- owed, which the export refuses
   (hard rule 5).
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
relation, a passage's outcome); a downgrade must name a weaker relation, and a
prominence overturn a prominence other than the passage's: anything else is
named in ``problems`` and left owed.  Every row carries the ``source_sha`` and
``layer_sha256`` of the document it came from, and a document whose pins are not
the run's manifest's puts a ``problem`` on each of its rows -- the export refuses
those, as it does a row whose location could not be joined.

**Status rule 2** (docs/proposals/statement-cut.md sections 1.5, 2.1, 2.5 and 5): a
locate run whose ``run.yaml`` names ``locate.status_rule: 2`` (:func:`status_rule_of`;
a run.yaml without it is status rule 1, and its rows, counts and statuses are
exactly the ones they always were) is derived by the bundle's rule.  Its rows carry
the passage's ``statement``, ``conjuncts`` and ``shape`` from the frozen inventory;
its coverage claim is a **bundle** whose ``relation`` is its weakest part's
(:func:`bundle_relation`: the weaker of each part's ``conjunct_relation`` and a
confirm's ``part_relations`` entry for it, since a challenge never raises one); a
challenge document's ``fills`` replace the last counting location a verdict removed
(:func:`_apply_fill`); a ``located`` passage the challenge leaves with no counting
location and no absence is **owed a hunt** -- challenge ``challenge_pending``,
``owed: "hunt"`` and a ``problem``, so the export refuses it -- and an unresolved
passage keeps its ``cut_flag``; the counts gain ``owed_hunt``, ``by_cut_flag`` and
``bundles``.  What judgment rules 3 add to a document is read on such a run only
(:data:`CUT_LOCATION_FIELDS`, :func:`_judged_fields`, :func:`_apply_coverage`): a
location's ``conjunct_relation`` and ``tests``, a verdict's own (a
``conjunct_relation`` never stronger than the location's), a coverage confirm's
``part_relations``, a refute's ``unstated`` and ``part_reasons`` and an uphold's
``special_case``; a run of status rule 1 derives the rows it always did whatever
keys its documents carry.  A verdict on a record its plan marked ``conditional:
"prominence"`` (``locate.challenge_plan``, the smoke-3 audit's anomaly 3) applies
only when the same challenge run overturned the passage's prominence to ``subject``
or ``central``; otherwise it is named and ignored, as a verdict on any record the
plan did not require is.  Only a plan made under judgment rules 3 marks a record so,
and its own ``challenge-plan.json`` says which, so this binds no earlier run.
"""
from __future__ import annotations

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
#: what a row of status rule 2 copies from its frozen inventory passage (the statement
#: cut's section 1.5): the passage's self-contained restatement and, on a compound
#: statement, its conjuncts and shape
STATEMENT_FIELDS = ("statement", "conjuncts", "shape")
#: the location fields of ``vocab.JUDGMENT_LOCATION_FIELDS`` that judgment rules 3 added
#: (the statement cut's J2 and fix 2a): kept from a document, and taken from a verdict,
#: only on a run of status rule 2 (:func:`_location_fields`), so a run of status rule 1
#: yields the rows it always did whatever keys its documents carry (the proposal's §7)
CUT_LOCATION_FIELDS = ("conjunct_relation", "tests", "pd6")
#: what a challenge downgrade to relatedMatch says of the record it leaves under judgment
#: rules 4 (the gate's ``check_bridge``): kept on the location, on a run of status rule 2
#: or 3, while it is a relatedMatch
BRIDGE_FIELDS = ("bridge", "incomparable")
#: the challenge target a conditional record waits on (``locate.challenge_plan``): its
#: verdict applies once the same challenge run overturns that passage's prominence
CONDITIONAL_PROMINENCE = "prominence"


def status_rule_of(run: dict) -> int:
    """The status rule a locate run's outcomes are derived under: ``locate.status_rule``.

    A run.yaml without the key -- every run planned before the statement cut's flip
    -- is status rule 1 (``cflib.vocab.STATUS_RULE_D1``); one naming a rule this code
    does not define is refused, never derived by another.
    """
    value = (run.get("locate") or {}).get("status_rule")
    if value is None:
        return vocab.STATUS_RULE_D1
    if type(value) is not int or value not in vocab.STATUS_RULES:
        raise CfError(f"run.yaml names status rule {value!r}, which this checkout does not "
                      "derive",
                      hint=f"status rules {', '.join(map(str, vocab.STATUS_RULES))} are defined "
                           "(docs/proposals/statement-cut.md section 2.2, references/"
                           "vocabulary.md section 1.11); tally the run with the checkout that "
                           "planned it")
    return int(value)


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
        # the receipt's bytes, so a reader of the outcomes (cf bench score) can tell
        # outcomes built from this harvest from outcomes of an earlier one
        gate = {"receipt": True, "complete": bool(got.get("complete")),
                "gate_ok": bool((got.get("gate") or {}).get("ok")) if got.get("gate") else None,
                "harvested_at": got.get("harvested_at"),
                "receipt_sha256": hashing.sha256_file(paths.receipt_json(run_id))}
        skipped = (got.get("gate") or {}).get("skipped")
        if isinstance(skipped, dict):
            # an inventory's skips, counted at harvest: what gate rule 8's coverage
            # floor let go unkept (references/locate-documents.md section 2)
            gate["skipped"] = skipped
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
                     require_gate: bool = True, conditional: dict | None = None,
                     fills: dict | None = None) -> tuple[dict, dict, list[str], dict]:
    """``(planned, verdicts, problems, gates)`` of the challenge runs of locate run ``run_id``.

    ``planned`` is ``{record key: plan row}`` -- the records of each run's
    ``challenge-plan.json`` that are ``run_id``'s; ``verdicts`` is ``{record key:
    verdict}`` from its harvested documents, kept only for a record the plan
    REQUIRED (a verdict on any other is named in ``problems`` and ignored);
    ``gates`` is each challenge run's receipt summary.  A challenge run that did
    not challenge ``run_id`` is refused; one whose plan requires nothing has no
    documents to read.  One record planned or judged twice is refused.

    Two optional dicts the caller passes are filled (the statement cut's section 5,
    anomalies A1-A3), so the four-value result stays what every reader takes:
    ``conditional`` gets ``{record key: verdict}`` for a record the plan marked
    ``conditional: "prominence"`` (not required, in the task of its passage's
    required prominence record), whose verdict applies only beside an overturn of
    that prominence to ``subject`` or ``central`` in the same run; ``fills`` gets
    ``{(source_id, join key): fill}`` from each document's ``fills``, what replaces
    the last counting location a verdict removed.  Without them, a verdict on a
    conditional record is named and ignored like any other unrequired one, and no
    document's fills are read.
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
                    if conditional is not None and \
                            plan_row.get("conditional") == CONDITIONAL_PROMINENCE:
                        # judged in the task of its passage's prominence record; it counts
                        # only beside that prominence's overturn (_apply_challenge)
                        if key in conditional:
                            raise CfError(f"{rel}: record {key[:12]} is judged twice")
                        conditional[key] = {**v, "source_id": sid,
                                            "challenge_run": challenge_run}
                        continue
                    problems.append(f"{rel}: {target} {v.get('item')} was planned "
                                    f"{plan_row.get('planned')}; the verdict is ignored")
                    continue
                if key in verdicts:
                    raise CfError(f"{rel}: record {key[:12]} is judged twice")
                verdicts[key] = {**v, "source_id": sid, "challenge_run": challenge_run}
                claims = doc.get("claims")
                if isinstance(claims, dict) and isinstance(claims.get(v.get("item")), list):
                    # the challenger's claim list of the passage, which its clauses name
                    verdicts[key]["claims"] = claims[v.get("item")]
            if fills is not None and isinstance(doc.get("fills"), dict):
                for item, fill in sorted(doc["fills"].items()):
                    pkey = (sid, cf_items.join_key(str(item)))
                    if pkey in fills:
                        problems.append(f"{rel}: {sid} {item} is filled twice; the second fill "
                                        "is ignored")
                        continue
                    fills[pkey] = {**(fill if isinstance(fill, dict) else {"outcome": fill}),
                                   "item": str(item), "source_id": sid,
                                   "challenge_run": challenge_run, "document": rel}
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


def _location_fields(status_rule: int) -> tuple[str, ...]:
    """The judgment fields a location of a run of ``status_rule`` keeps, where written."""
    if vocab.reads_bundles(status_rule):
        return vocab.JUDGMENT_LOCATION_FIELDS
    return tuple(k for k in vocab.JUDGMENT_LOCATION_FIELDS if k not in CUT_LOCATION_FIELDS)


def _location(source_id: str, item: str, loc: dict, universe: inputs.Universe,
              db: sqlite3.Connection | None, problems: list[str], *,
              status_rule: int = vocab.STATUS_RULE_D1) -> dict | None:
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
    # what the judgment rules have a location say of its passage, kept as written and
    # only where written, and judgment rules 3's two only on a run of status rule 2: a
    # run planned before them yields the rows it always did
    out.update({key: loc[key] for key in _location_fields(status_rule) if key in loc})
    if vocab.reads_bundles(status_rule):
        # a field the relation does not take is dropped here as after a verdict, so a
        # row's location is the same whether or not a challenge judged it (an optional
        # ``tests`` on a weaker relation, which rules 3's gate checks when given)
        _fit_fields(out)
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
                    db, problems: list[str], *,
                    status_rule: int = vocab.STATUS_RULE_D1) -> tuple[list[dict], list[str]]:
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
        got = _location(sid, item, loc, universe, db, problems, status_rule=status_rule)
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
    receipt, complete, gate_ok, harvested_at, receipt_sha256}}``, the sha of the
    ``RECEIPT.json`` read, and an inventory's ``skipped`` when its receipt's gate
    block counted what its skips removed).
    """
    run = run_yaml(paths, run_id)
    loc_block = run["locate"]
    mode = str(loc_block.get("mode") or run.get("mode"))
    status_rule = status_rule_of(run)
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
              "sourceset": loc_block.get("sourceset"), "universe": uni_pin,
              "status_rule": status_rule}
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
    c_conditional: dict = {}
    c_fills: dict = {}
    c_planned, c_verdicts, c_problems, c_gates = challenge_values(
        paths, run_id, challenge_runs, require_gate=require_gate, conditional=c_conditional,
        fills=c_fills)
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
                               task_id=_task_of(rel), inv_run=inv_run, status_rule=status_rule)
                for text in doc_problems:
                    _problem(row, problems, text)
                outcomes.append(row)
        for key, plan in sorted(planned.items()):
            if key in seen:
                continue
            base = frozen.get(key) or plan
            outcomes.append(_missing(run_id, key[0], plan["item"], base, plan,
                                     status_rule=status_rule))
        run_sources = set(loc_block.get("source_ids") or []) or {k[0] for k in planned}
        for key, base in sorted(frozen.items()):
            if key in seen or key in planned or base.get("scope") in vocab.LOCATED_SCOPES:
                continue
            if key[0] not in run_sources:
                continue
            outcomes.append(_scoped_out(run_id, key[0], base["item"], base, inv_run,
                                        status_rule=status_rule))
        layer_of = _Layers(paths, sourceset_id)
        conditional_of: dict[tuple[str, str], dict] = {}
        for key, verdict in c_conditional.items():
            conditional_of.setdefault(
                (verdict["source_id"], cf_items.join_key(str(verdict.get("item") or ""))),
                {})[key] = verdict
        rows_of = {(r["source_id"], cf_items.join_key(r["item"])) for r in outcomes}
        for pkey, fill in sorted(c_fills.items()):
            if pkey not in rows_of:
                problems.append(f"{fill['document']}: a fill for {fill['source_id']} "
                                f"{fill['item']}, which is no passage of run {run_id}; ignored")
        for row in outcomes:
            pkey = (row["source_id"], cf_items.join_key(row["item"]))
            _apply_challenge(row, c_planned, c_verdicts, universe, db, problems, layer_of,
                             status_rule=status_rule, conditional=conditional_of.get(pkey),
                             fill=c_fills.get(pkey))
            _derive(row, problems, status_rule=status_rule)
            for scratch in ("record_challenge", "prominence_challenge",
                            "passage_challenge_override", "coverage_lapsed", "counted_before"):
                row.pop(scratch, None)
        outcomes.sort(key=lambda r: (r["source_id"], r["span"][0] if r.get("span") else -1,
                                     r["item"]))
    finally:
        if db is not None:
            db.close()
    result["rows"] = finding_rows(run_id, docs, sourceset_id=sourceset_id, universe=universe,
                                  accepted=accepted)
    result["outcomes"] = outcomes
    result["counts"] = _locate_counts(outcomes, result["rows"], status_rule=status_rule)
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


def _statement_fields(base: dict, status_rule: int) -> dict:
    """What a row of status rule 2 copies from its frozen inventory passage, where written."""
    if not vocab.reads_bundles(status_rule):
        return {}
    return {key: base[key] for key in STATEMENT_FIELDS if base.get(key) is not None}


def _outcome(run_id: str, sid: str, item: str, p: dict, doc: dict, base: dict,
             universe: inputs.Universe, db, problems: list[str], *, task_id: str,
             inv_run: str, status_rule: int = vocab.STATUS_RULE_D1) -> dict:
    outcome = p.get("outcome")
    own: list[str] = []
    if outcome not in vocab.LOCATE_OUTCOMES:
        own.append(f"{sid} {item}: outcome {outcome!r} is not one of "
                   f"{', '.join(sorted(vocab.LOCATE_OUTCOMES))}")
        outcome = vocab.OUTCOME_UNRESOLVED
    locations, dropped = _join_locations(sid, item, p.get("locations") or [], universe, db,
                                         problems, status_rule=status_rule)
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
    if isinstance(p.get("claims"), list):
        # the passage's claim list (the judgment rules): what its locations' clauses name
        row["claims"] = p["claims"]
    row.update(_statement_fields(base, status_rule))
    if vocab.reads_bundles(status_rule) and outcome == vocab.OUTCOME_UNRESOLVED \
            and p.get("cut_flag") is not None:
        # what a worker who cannot re-cut the passage says of its cut (J4), counted in
        # the tally for the inventory audit
        if p["cut_flag"] in vocab.CUT_FLAGS:
            row["cut_flag"] = p["cut_flag"]
        else:
            own.append(f"{sid} {item}: cut_flag {p['cut_flag']!r} is not one of "
                       f"{', '.join(sorted(vocab.CUT_FLAGS))}")
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


def _missing(run_id: str, sid: str, item: str, base: dict, plan: dict, *,
             status_rule: int = vocab.STATUS_RULE_D1) -> dict:
    return {**_base_fields(sid, item, base), **_statement_fields(base, status_rule),
            "outcome": None, "locations": [],
            "coverage": None, "coverage_refuted": None,
            "absence": None, "receipt": {}, "reason": "no document landed for this passage",
            "unresolved_by": None, "missing": True, "refuted": [], "challenge": None,
            "status": None, "runs": {"inventory": None, "locate": run_id, "challenge": None},
            "task_id": plan.get("task_id")}


def _scoped_out(run_id: str, sid: str, item: str, base: dict, inv_run: str, *,
                status_rule: int = vocab.STATUS_RULE_D1) -> dict:
    return {**_base_fields(sid, item, base), **_statement_fields(base, status_rule),
            "outcome": None, "locations": [],
            "coverage": None, "coverage_refuted": None,
            "absence": None, "receipt": {}, "reason": f"scoped {base.get('scope')}",
            "unresolved_by": None, "missing": False, "refuted": [], "challenge": None,
            "status": None, "runs": {"inventory": inv_run, "locate": run_id, "challenge": None},
            "task_id": None}


def _conditional_verdicts(row: dict, planned: dict, verdicts: dict,
                          conditional: dict | None, problems: list[str]) -> dict:
    """``verdicts`` with the passage's conditional verdicts that apply (anomaly A3).

    A record the challenge plan marked ``conditional: "prominence"`` was put in the
    task of its passage's required prominence record with its planned value
    unchanged; its verdict applies only when the same challenge run overturned that
    prominence to ``subject`` or ``central`` (an overturn this function sees as the
    passage-level loop will apply it: planned for the outcome the passage has, to a
    prominence other than its own).  Otherwise each is named in ``problems`` and
    ignored, as a verdict on any record the plan did not require is.
    """
    if not conditional:
        return verdicts
    sid, item = row["source_id"], row["item"]
    pkey = record_key("prominence", sid, item)
    overturn = verdicts.get(pkey)
    plan = planned.get(pkey) or {}
    run = None
    if overturn is not None and overturn.get("action") == "overturn" \
            and overturn.get("prominence") in vocab.REFUTE_BY_DEFAULT_PROMINENCE \
            and overturn.get("prominence") != row.get("prominence") \
            and plan.get("outcome") == row.get("outcome"):
        run = overturn.get("challenge_run")
    out = dict(verdicts)
    for key, verdict in sorted(conditional.items()):
        if run is not None and verdict.get("challenge_run") == run:
            out[key] = verdict
        else:
            problems.append(f"{sid} {item}: a verdict on the conditional {verdict.get('target')} "
                            f"record {key[:12]} with no overturn of the passage's prominence to "
                            "subject or central in its challenge run; ignored")
    return out


def _apply_challenge(row: dict, planned: dict, verdicts: dict, universe, db,
                     problems: list[str], layer_of=None, *,
                     status_rule: int = vocab.STATUS_RULE_D1, conditional: dict | None = None,
                     fill: dict | None = None) -> None:
    sid, item = row["source_id"], row["item"]
    outcome_seen = row["outcome"]
    verdicts = _conditional_verdicts(row, planned, verdicts, conditional, problems)
    # whether the passage counted before the challenge: one it leaves without is owed a
    # hunt under status rule 2 (_derive)
    row["counted_before"] = any(l.get("relation") != vocab.RELATED_MATCH
                                for l in row["locations"])
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
            if isinstance(verdict.get("claims"), list):
                row["challenge_claims"] = verdict["claims"]
            if action in vocab.CHALLENGE_KEEP:
                _judged_fields(loc, verdict, row, problems, status_rule=status_rule)
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
                    _judged_fields(loc, verdict, row, problems, status_rule=status_rule)
        kept.append(loc)
    row["locations"] = _renumbered(kept)
    confirmed: list[dict] = []
    if row.get("coverage") is not None:
        confirmed = _apply_coverage(row, planned, verdicts, problems, status_rule=status_rule)
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
        if isinstance(verdict.get("claims"), list) and t != "prominence":
            row["challenge_claims"] = verdict["claims"]
        if action == "uphold" and t in ("absence", "unresolved") \
                and isinstance(verdict.get("queries"), list):
            # what the challenger searched before it upheld the record (the judgment
            # rules): its queries, tagged with its own claims
            row["challenge_receipt"] = {"queries": verdict["queries"],
                                        "query_clauses": verdict.get("query_clauses")}
            if vocab.reads_bundles(status_rule) and \
                    isinstance(verdict.get("special_case"), dict):
                # the nearest special case the challenger found and why it fails (the
                # smoke-3 audit's FM5 fix, judgment rules 3), beside its queries
                row["challenge_receipt"]["special_case"] = verdict["special_case"]
        if action == "overturn" and t in ("absence", "unresolved", "scope"):
            new, dropped = _join_locations(sid, item, verdict.get("locations") or [], universe,
                                           db, problems, status_rule=status_rule)
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
            if isinstance(verdict.get("claims"), list):
                # the overturn's locations name the challenger's claim list
                row["claims"] = verdict["claims"]
            row["outcome"] = vocab.OUTCOME_LOCATED
            row["scope"] = vocab.SCOPE_FORMAL_MATH
            row["absence"] = None
            row["unresolved_by"] = None
        elif t == "prominence" and action == "overturn":
            if verdict.get("prominence") == row["prominence"]:
                # an overturn to the passage's own prominence overturns nothing (the gate
                # refuses it): never a downgraded passage that kept its label and QID
                row["prominence_challenge"] = None
                _problem(row, problems, f"{sid} {item}: a prominence overturn to "
                                        f"{row['prominence']!r}, the passage's own, changes "
                                        "nothing; it is ignored and the record owed")
                continue
            if verdict.get("prominence") in vocab.PROMINENCE:
                row["prominence"] = verdict["prominence"]
            row["passage_challenge_override"] = vocab.CHALLENGE_DOWNGRADED
            if row["prominence"] in vocab.REFUTE_BY_DEFAULT_PROMINENCE:
                _reopen(row)
    # a confirmed claim confirms its parts only now, after any reopen: the reopen wins, so
    # a part a prominence overturn put under a rule requiring it is challenge_pending,
    # owed its own verdict under that rule, and the claim does not stand in for it
    confirm_parts(confirmed)
    if fill is not None:
        if vocab.reads_bundles(status_rule):
            _apply_fill(row, fill, universe, db, problems)
        else:
            problems.append(f"{sid} {item}: a fill from {fill.get('challenge_run')} on a run of "
                            f"status rule {status_rule}, which no fill binds; ignored")


def _counts(loc: dict) -> bool:
    return loc.get("relation") != vocab.RELATED_MATCH


def _apply_fill(row: dict, fill: dict, universe, db, problems: list[str]) -> None:
    """A challenge document's ``fills[item]``: what replaces the last counting location.

    The smoke-3 audit's anomalies A1 and A2, the statement cut's fix 6: a document
    whose verdicts leave a ``located`` passage with no counting location gives
    ``{outcome: absent, receipt}`` -- the challenger hunted and found nothing, so
    the passage is ``absent`` beside its ``relatedMatch`` locations, the absence
    the challenger's own (``survived``) -- or ``{outcome: located, locations}`` --
    the replacement locations it found, each its own witnessed finding
    (``survived``), first, then the kept ``relatedMatch`` ones, as an overturn's.
    A fill applies only when the passage counted before the challenge, its
    challenge run's verdicts left it with no counting location and no absence,
    and those verdicts are the fill's own run's; otherwise it is named on the row
    and ignored (the gate refuses it first).
    """
    sid, item = row["source_id"], row["item"]
    run = fill.get("challenge_run")
    if row["outcome"] != vocab.OUTCOME_LOCATED or row.get("absence") is not None \
            or not row.get("counted_before") or any(_counts(l) for l in row["locations"]) \
            or row["runs"].get("challenge") != run:
        _problem(row, problems, f"{sid} {item}: {run}'s fill names a passage its verdicts did "
                                "not leave without a location that counts; it is ignored")
        return
    outcome = fill.get("outcome")
    if outcome == vocab.OUTCOME_ABSENT and isinstance(fill.get("receipt"), dict):
        row["outcome"] = vocab.OUTCOME_ABSENT
        row["absence"] = {"receipt": fill["receipt"], "challenge": vocab.CHALLENGE_SURVIVED}
        row["fill"] = {"outcome": outcome, "challenge_run": run}
        return
    if outcome == vocab.OUTCOME_LOCATED and isinstance(fill.get("locations"), list):
        new, dropped = _join_locations(sid, item, fill["locations"], universe, db, problems,
                                       status_rule=vocab.STATUS_RULE_BUNDLE)
        for text in dropped:
            row["problem"] = "; ".join(filter(None, [row.get("problem"), text]))
        new = [loc for loc in new if _counts(loc)]
        if not new:
            _problem(row, problems, f"{sid} {item}: {run}'s fill names no location that exists "
                                    "and counts")
            return
        for loc in new:
            loc["challenge"] = vocab.CHALLENGE_SURVIVED
        row["locations"] = _overturned(new, row["locations"])
        row["fill"] = {"outcome": outcome, "challenge_run": run}
        return
    _problem(row, problems, f"{sid} {item}: a fill is {{outcome: absent, receipt}} or "
                            f"{{outcome: located, locations}}, not {fill.get('outcome')!r}; it is "
                            "ignored")


#: what a confirm or downgrade verdict may say of the record it leaves, which replaces
#: the worker's: what a hasPart or narrowMatch states (its step), the convention a
#: closeMatch differs by, and how the declaration is instantiated at the passage's
#: objects
_VERDICT_FIELDS = ("step", "convention", "instantiation")


def _judged_fields(loc: dict, verdict: dict, row: dict | None = None,
                   problems: list[str] | None = None, *,
                   status_rule: int = vocab.STATUS_RULE_D1) -> None:
    """What a challenger says the location it leaves states (the judgment rules).

    Called for a confirm, and for a downgrade once it is applied.  ``clauses``
    name the challenger's own claim list of the passage (the row's
    ``challenge_claims``), so the location keeps them as ``challenge_clauses``,
    its worker's ``clauses`` unchanged; a ``step``, ``convention`` or
    ``instantiation`` the verdict names is the record it leaves, and replaces the
    worker's.  Then a field that does not describe the relation the location now
    has is dropped (:func:`_fit_fields`): a ``convention`` belongs to a
    ``closeMatch`` and a ``step`` to a ``hasPart`` or ``narrowMatch``, so a
    downgrade from ``closeMatch`` to ``broadMatch`` leaves no convention behind.
    Nothing changes for a verdict that names none of them on a record that carries
    none (every verdict of a run planned before the rules).

    On a run of status rule 2, whose challenges are judged under judgment rules 3
    (the statement cut's fixes 2d and J2), a verdict that keeps or leaves a strong
    relation gives its own two-way ``tests``, which replace the worker's, and one
    on a ``hasPart`` part may give a ``conjunct_relation``, never stronger than the
    location's own: a stronger one, or one outside ``CONJUNCT_RELATIONS``, is named
    on the row and the location's kept (a challenge never raises a relation).  On a
    run of status rule 1 the verdict's ``tests`` and ``conjunct_relation`` are not
    read, as they never were.
    """
    if isinstance(verdict.get("clauses"), list):
        loc["challenge_clauses"] = verdict["clauses"]
    for key in _VERDICT_FIELDS:
        if isinstance(verdict.get(key), str) and verdict[key].strip():
            loc[key] = verdict[key]
    if vocab.reads_bundles(status_rule):
        _judged_cut_fields(loc, verdict, row, problems)
    _fit_fields(loc)


def _judged_cut_fields(loc: dict, verdict: dict, row: dict | None,
                       problems: list[str] | None) -> None:
    """A verdict's ``tests`` and ``conjunct_relation`` on the location it leaves (rules 3).

    And judgment rules 4's ``bridge`` or ``incomparable`` of a downgrade to
    ``relatedMatch`` (:data:`BRIDGE_FIELDS`), kept as the verdict gave it; a
    ``conjunct_relation`` may be ``hasPart`` there (``vocab.CONJUNCT_RELATIONS_PART``),
    which the gate admits under those rules only.
    """
    if isinstance(verdict.get("tests"), dict):
        loc["tests"] = verdict["tests"]
    for key in BRIDGE_FIELDS:
        if verdict.get(key) is not None:
            loc[key] = verdict[key]
    given = verdict.get("conjunct_relation")
    if given is None:
        return
    own = loc.get("conjunct_relation")
    why = None
    if given not in vocab.CONJUNCT_RELATIONS_PART:
        why = (f"{given!r}, which is none of "
               f"{', '.join(sorted(vocab.CONJUNCT_RELATIONS_PART))}")
    elif own in RELATION_STRENGTH and RELATION_STRENGTH[given] < RELATION_STRENGTH[own]:
        why = f"{given!r}, stronger than its own {own!r}"
    if why is None:
        loc["conjunct_relation"] = given
    elif row is not None and problems is not None:
        _problem(row, problems, f"{row['source_id']} {row['item']}: a verdict gives "
                                f"{loc.get('library')}:{loc.get('declaration')} the conjunct "
                                f"relation {why}; the location's is kept")


def held_relation(loc: dict) -> str | None:
    """The relation a location is held to: its own, or a ``hasPart``'s ``conjunct_relation``.

    Judgment rules 3 hold a part of a compound statement by the relation it bears to
    the conjuncts it states -- the relation a bundle takes from it (status rule 2,
    the weakest part's) -- so a part whose ``conjunct_relation`` is strong is a
    strong location, with a strong location's ``tests`` (the gate's ``held_label``).
    A ``hasPart`` with no conjunct relation is held to ``hasPart``.
    """
    relation = loc.get("relation")
    if relation == vocab.HAS_PART and loc.get("conjunct_relation") in \
            vocab.CONJUNCT_RELATIONS_PART:
        return loc["conjunct_relation"]
    return relation


def _fit_fields(loc: dict) -> None:
    """Drop a judgment field the location's relation does not take (see :func:`_judged_fields`).

    A ``convention`` belongs to a ``closeMatch`` (and, under judgment rules 3, to a
    ``hasPart`` whose ``conjunct_relation`` is a ``closeMatch``: J2), a ``step`` to a
    ``hasPart`` or ``narrowMatch``, and -- judgment rules 3's two, only on a run of
    status rule 2 -- a ``conjunct_relation`` to a ``hasPart`` and ``tests`` to a
    location held to a strong relation (:func:`held_relation`: its own, or a part's
    ``conjunct_relation``), so a confirm or downgrade that leaves a strong part keeps
    its tests and one that leaves it a ``narrowMatch`` drops them.
    """
    if loc.get("relation") != vocab.HAS_PART:
        loc.pop("conjunct_relation", None)
    # a closeMatch's convention, or (judgment rules 3, J2) a part's whose conjunct
    # relation is a closeMatch
    if loc.get("relation") != vocab.CLOSE_MATCH and \
            loc.get("conjunct_relation") != vocab.CLOSE_MATCH:
        loc.pop("convention", None)
    if loc.get("relation") not in vocab.STEP_RELATIONS:
        loc.pop("step", None)
    if held_relation(loc) not in vocab.STRONG_RELATIONS:
        loc.pop("tests", None)
    # judgment rules 4: a part's answer to PD6's shape tripwire, and a downgrade's bridge
    if loc.get("relation") != vocab.HAS_PART:
        loc.pop("pd6", None)
    if loc.get("relation") != vocab.RELATED_MATCH:
        for key in BRIDGE_FIELDS:
            loc.pop(key, None)


def _apply_coverage(row: dict, planned: dict, verdicts: dict,
                    problems: list[str], *, status_rule: int = vocab.STATUS_RULE_D1) -> list[dict]:
    """A coverage claim's challenge (the maintainer's decision D1); the parts it confirms.

    Every claim is refuted by default: ``confirm`` makes it ``survived``, and
    its parts are returned for :func:`confirm_parts`, which the caller runs
    once the passage-level records are applied -- after a prominence overturn's
    :func:`_reopen`, so a part the reopen made ``challenge_pending`` stays
    pending.  ``refute`` removes it (kept under ``coverage_refuted``,
    ``downgraded``) and leaves its parts ``hasPart`` locations.  A verdict
    applies only while the claim names the parts the plan saw, and an action
    outside ``cflib.vocab.COVERAGE_ACTIONS`` leaves it owed.  A claim whose part
    a location verdict refuted or downgraded no longer holds: it upgrades
    nothing, confirms no part, and the passage carries a problem, which the
    export refuses, until the claim is challenged again.  Anything but a
    standing survived claim returns no part.

    Under status rule 2 the claim is a bundle (the statement cut's sections 2.1
    and 1.4): a confirm's ``part_relations`` are kept beside it
    (``coverage_part_relations``), a refute's ``unstated`` and ``part_reasons`` on
    ``coverage_refuted``, and a standing claim gets its ``relation``, its weakest
    part's (:func:`bundle_relation`).  A bundle on a simple statement (no
    conjuncts), or one whose relation is no bundle's -- a part with no
    ``conjunct_relation``, or one that states only a special case of its conjuncts
    -- no longer holds: it upgrades nothing and the passage carries a problem.
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
            if isinstance(verdict.get("claims"), list):
                row["challenge_claims"] = verdict["claims"]
            if verdict.get("action") == "confirm" and isinstance(verdict.get("part_clauses"),
                                                                 dict):
                # the challenger's clauses of each part (the judgment rules), beside the
                # claim: the claim itself keeps exactly its record keys
                row["coverage_challenge_clauses"] = verdict["part_clauses"]
            if vocab.reads_bundles(status_rule) and verdict.get("action") == "confirm" \
                    and isinstance(verdict.get("part_relations"), dict):
                # the challenger's relation of each part to its conjuncts (judgment rules 3),
                # which counts in the bundle's relation where it is weaker than the part's own
                row["coverage_part_relations"] = verdict["part_relations"]
            for extra in ("part_conventions", "part_limits"):
                # the readings a confirm credits the parts with and its answers to PD6's
                # shape tripwire (judgment rules 4), kept beside the claim for the audit
                if vocab.reads_bundles(status_rule) and verdict.get("action") == "confirm" \
                        and isinstance(verdict.get(extra), dict):
                    row[f"coverage_{extra}"] = verdict[extra]
            if verdict.get("action") == "refute":
                row["coverage_refuted"] = {**claim, "challenge": vocab.CHALLENGE_DOWNGRADED,
                                           "witness": verdict.get("witness", "")}
                for extra in ("unstated", "part_reasons"):
                    # what a refute names under judgment rules 3 (the statement cut's fix 1a)
                    if vocab.reads_bundles(status_rule) and verdict.get(extra) is not None:
                        row["coverage_refuted"][extra] = verdict[extra]
                row["coverage"] = None
                row["passage_challenge_override"] = vocab.CHALLENGE_DOWNGRADED
                return []
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
        return []
    if vocab.reads_bundles(status_rule) and not _bundle_stands(row, claim, standing,
                                                                       problems):
        return []
    if claim["challenge"] == vocab.CHALLENGE_SURVIVED:
        return [standing[k] for k in claim["parts"]]
    return []


def bundle_relation(parts: Iterable[dict], part_relations: dict | None = None) -> str | None:
    """A bundle's relation: its weakest part's (the maintainer's answer to question 9).

    docs/proposals/statement-cut.md section 2.1: for each part, its
    ``conjunct_relation`` as the locate document wrote it (or a verdict on the part
    weakened it), and, once a challenger confirmed the claim, that confirm's
    ``part_relations`` entry for it, the weaker of the two kept, since a challenge
    never raises a relation; then the weakest over the parts (greatest
    ``RELATION_STRENGTH``).  None when a part has no conjunct relation of
    ``CONJUNCT_RELATIONS``.  A bundle of an ``exactMatch`` and a ``broadMatch`` part is
    a ``broadMatch`` bundle.
    """
    weakest: str | None = None
    for loc in parts:
        own = loc.get("conjunct_relation")
        if own not in vocab.CONJUNCT_RELATIONS_PART:
            return None
        given = (part_relations or {}).get(loc.get("location_key"))
        relation = own
        if given in vocab.CONJUNCT_RELATIONS_PART and \
                RELATION_STRENGTH[given] > RELATION_STRENGTH[own]:
            relation = given
        if weakest is None or RELATION_STRENGTH[relation] > RELATION_STRENGTH[weakest]:
            weakest = relation
    return weakest


def _bundle_stands(row: dict, claim: dict, standing: dict, problems: list[str]) -> bool:
    """Put the bundle's relation on a standing claim of status rule 2, or lapse the claim."""
    sid, item = row["source_id"], row["item"]
    why = ""
    if not row.get("conjuncts"):
        why = ("a bundle stands only on a compound statement, and the inventory gives this "
               "passage no conjuncts")
    else:
        relation = bundle_relation([standing[k] for k in claim["parts"]],
                                   row.get("coverage_part_relations")
                                   if claim.get("challenge") == vocab.CHALLENGE_SURVIVED else None)
        if relation is None:
            why = "a part of the bundle names no conjunct_relation"
        elif relation == vocab.HAS_PART:
            why = ("its weakest part states only part of its conjuncts (a hasPart of them), "
                   "which leaves them unstated")
        elif relation not in vocab.BUNDLE_RELATIONS:
            why = (f"its weakest part is a {relation} of its conjuncts, a special case that "
                   "leaves them unstated")
        else:
            claim["relation"] = relation
            return True
    row["coverage_lapsed"] = True
    _problem(row, problems, f"{sid} {item}: the bundle no longer holds: {why}; it upgrades "
                            "nothing and is to be challenged again")
    return False


def confirm_parts(parts: Iterable[dict]) -> None:
    """A confirmed coverage claim confirms its parts (decision D1, parity round 3).

    The challenger matched every clause of the passage to a part, so each part
    the plan never required was judged with the claim: one whose own record is
    ``not_required`` or ``sampled_out`` is ``survived``.  A part a verdict
    judged keeps that verdict, and an owed one stays owed.  So does a
    ``challenge_pending`` part: a prominence overturn in the same run put it
    under a rule that requires it (:func:`_reopen`, which runs first), and it
    needs its own verdict under that stricter rule -- the claim stands in only
    for parts no rule required.  A part refuted or downgraded out of
    ``hasPart`` is no part any more, and lapses the claim before this is
    reached.
    """
    for loc in parts:
        if loc.get("challenge") in (vocab.CHALLENGE_NOT_REQUIRED, vocab.CHALLENGE_SAMPLED_OUT):
            loc["challenge"] = vocab.CHALLENGE_SURVIVED


def _reopen(row: dict) -> None:
    """A prominence overturned to ``subject``/``central`` reopens the passage's records.

    The challenge plan judged the passage's records under ``supporting``: its
    locations were sampled or not required, its absence or unresolved outcome
    was not hunted.  Under the new prominence section 5 refutes every location
    by default and hunts the absence, so each record no verdict judged
    (``not_required``, ``sampled_out``) becomes ``challenge_pending`` -- never
    left ``not_required``, which a consumer would read as "no rule applied"
    (field defect CH2: two central absences exported that nobody hunted).  A
    record a verdict judged keeps its value.  It runs before a confirmed
    coverage claim confirms its parts (:func:`confirm_parts`), so a part it
    makes pending is not confirmed with the claim.
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


#: a downgrade's note is computed in ``records`` (the export's free-text rule), so
#: the gate, which stages that module as ``locate_records``, checks the very note
#: these outcomes write; the names stay importable from here
NOTE_FROM_WITNESS_CHARS = records.NOTE_FROM_WITNESS_CHARS
witness_sentences = records.witness_sentences
downgrade_note = records.downgrade_note


def _renumbered(locations: list[dict]) -> list[dict]:
    """The locations by relation strength, then rank, ranked 1..n again.

    A refuted location leaves the passage, and the ones after it move up: the
    export's ranks are the order a reader sees, never a gap where a refuted
    declaration stood (field defect X4: two passages exported ranks [1, 3]).

    R-SPECIFIC ranks by relation strength first (``RELATION_STRENGTH``,
    references/vocabulary.md section 1.11), so a downgrade moves its card below
    every card it is now weaker than: rank 1 after a challenge is the passage's
    strongest surviving card, never the ``closeMatch`` an ``exactMatch`` was
    downgraded into above one that survived.  **Among cards of equal strength
    the worker's order stands**: which declaration is stated for the passage's
    own objects is nowhere in a record, so nothing here can re-apply the
    specificity half of the rule, and the challenger reports a general card
    ranked above a specific one as a recall miss instead.
    """
    ordered = sorted(locations, key=lambda l: (RELATION_STRENGTH.get(l.get("relation"), 9),
                                               l.get("rank") or 99, l["library"],
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


def _derive(row: dict, problems: list[str], *,
            status_rule: int = vocab.STATUS_RULE_D1) -> None:
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
        coverage = records.coverage_ext(claim, status_rule=status_rule)
    try:
        row["status"] = vocab.derive_status(row["passage_kind"] or vocab.PASSAGE_OTHER,
                                            joined, row["absence"] is not None, coverage,
                                            status_rule=status_rule)
    except CfError as exc:
        _problem(row, problems, f"{row['source_id']} {row['item']}: {exc}")
        row["status"] = None
    if vocab.reads_bundles(status_rule):
        _owed_hunt(row, problems)


def _owed_hunt(row: dict, problems: list[str]) -> None:
    """A located passage the challenge left with nothing that counts is owed a hunt.

    The statement cut's section 2.5 (the smoke-3 audit's anomalies A1 and A2), under
    status rule 2 only: a ``located`` passage that counted before the challenge and
    is left with no counting location and no absence has no status, and nobody
    checked that it is absent.  Its challenge is ``challenge_pending`` (one record it
    owes is still owed, as a reopened one is) unless a record is owed outright, it
    carries ``owed: "hunt"``, and a problem names it, so the export refuses it by
    name and the tally counts it as ``owed_hunt``.  Under status rule 1 the same
    passage stays what it always was: no status, exported.  A challenge document of
    judgment rules 3 makes the case unreachable with its ``fills``; this is the net
    under them.
    """
    if row["outcome"] != vocab.OUTCOME_LOCATED or row.get("absence") is not None \
            or not row.get("counted_before") or any(_counts(l) for l in row["locations"]):
        return
    if row["challenge"] is not None:
        row["challenge"] = vocab.CHALLENGE_PENDING
    row["owed"] = vocab.CHALLENGE_RULE_HUNT
    _problem(row, problems, f"{row['source_id']} {row['item']}: the challenge left this located "
                            "passage with no location that counts and no absence: it is owed a "
                            "hunt, and is not exported until one is recorded")


def _inventory_counts(docs, rows) -> dict:
    kinds = Counter(r["kind"] for r in rows)
    return {"documents": len(docs), "passages": kinds.get(vocab.PASSAGE, 0),
            "unresolved_documents": kinds.get(vocab.UNRESOLVED, 0),
            "attempt_capped": sum(1 for r in rows if r.get("by") == vocab.UNRESOLVED_ATTEMPT_CAP),
            "by_scope": dict(sorted(Counter(r.get("scope") for r in rows
                                            if r["kind"] == vocab.PASSAGE).items())),
            "by_passage_kind": dict(sorted(Counter(r.get("passage_kind") for r in rows
                                                   if r["kind"] == vocab.PASSAGE).items()))}


def _locate_counts(outcomes: list[dict], rows: list[dict], *,
                   status_rule: int = vocab.STATUS_RULE_D1) -> dict:
    located = [o for o in outcomes if o["outcome"] is not None]
    by_library = Counter(l["library"] for o in outcomes for l in o["locations"])
    counts = {
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
    if vocab.reads_bundles(status_rule):
        counts.update(_bundle_counts(outcomes))
    return counts


def _bundle_counts(outcomes: list[dict]) -> dict:
    """The counts status rule 2 adds (every other count is status rule 1's, unchanged).

    ``owed_hunt``: located passages the challenge left with nothing that counts
    (:func:`_owed_hunt`); ``by_cut_flag``: the ``cut_flag`` of each unresolved
    passage that carries one, for the inventory audit; ``bundles``: the coverage
    claims locate wrote (standing or refuted), those whose challenge is
    ``survived`` and that hold, and those by relation.
    """
    written = [o for o in outcomes if o.get("coverage") or o.get("coverage_refuted")]
    # a claim gets its relation only while it holds (_bundle_stands): a lapsed one has none
    survived = [o for o in outcomes if (o.get("coverage") or {}).get("challenge")
                == vocab.CHALLENGE_SURVIVED and (o.get("coverage") or {}).get("relation")]
    return {
        "owed_hunt": sum(1 for o in outcomes if o.get("owed") == vocab.CHALLENGE_RULE_HUNT),
        "by_cut_flag": dict(sorted(Counter(o["cut_flag"] for o in outcomes
                                           if o.get("cut_flag")).items())),
        "bundles": {"written": len(written), "survived": len(survived),
                    "by_relation": dict(sorted(Counter(o["coverage"]["relation"]
                                                       for o in survived).items()))},
    }


def outcome_row(row: dict) -> dict:
    """The section 10 row as written to ``reports/locate-outcomes.<run>.jsonl``."""
    keep = ("source_id", "item", "passage_kind", "prominence", "scope", "outcome", "locations",
            "coverage", "absence", "challenge", "status", "runs", "span", "head_words",
            "section", "source_sha", "layer_sha256", "reason", "unresolved_by", "missing",
            "refuted", "coverage_refuted", "task_id")
    out = {k: row.get(k) for k in keep}
    # the judgment rules' claim lists, only where a document wrote one: a run planned
    # before them writes the rows it always did; and status rule 2's fields (the
    # statement cut's section 1.5), only on a run of that rule
    for key in ("claims", "challenge_claims", "challenge_receipt",
                "coverage_challenge_clauses", "coverage_part_relations",
                "coverage_part_conventions", "coverage_part_limits", *STATEMENT_FIELDS,
                "cut_flag", "owed", "fill"):
        if row.get(key) is not None:
            out[key] = row[key]
    if row.get("problem"):
        out["problem"] = row["problem"]
    return out
