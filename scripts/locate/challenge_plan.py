"""Which records an L5 challenge judges, and what every other record says.

New code, implementing docs/proposals/wikipedia-locate.md section 5 over the
passage outcomes of harvested locate runs (``locate.outcomes``):

* every location on a ``subject`` or ``central`` passage is **refuted by
  default** -- required;
* every absence and every unresolved outcome on a ``subject`` or ``central``
  passage is **hunted** -- required;
* every **coverage claim** (the maintainer's decision D1), whatever the
  passage's prominence, is refuted by default -- required: it upgrades the
  passage's derived status, so no claim leaves unchallenged;
* every strong location whose own note admits a clause it does not state
  (``cflib.vocab.admits_missing_clause``) is refuted by default too, whatever
  its passage's prominence (the smoke-2 audit's FM1: three notes said so and
  the relation still counted);
* every ``subject`` passage's **prominence** is refuted by default, stratum
  ``prominence:subject`` (:data:`SUBJECT_STRATUM`), always and never sampled:
  the article's QID rides on every record of a subject passage
  (``locate.records.article_qid``), so a passage mislabelled ``subject`` would
  keep it with no verdict that could take it away.  An overturn away from
  ``subject`` removes it, an uphold keeps it, and several subject passages of
  one article are each judged on their own;
* on a ``supporting`` passage, every location that sets its derived status
  on its own -- its **bases** (:func:`bases`) -- is required whenever its
  relation is strong (``exactMatch``, ``closeMatch``, ``broadMatch``:
  ``cflib.vocab.STRONG_RELATIONS``): the passage keeps its status while any
  one of them stands, so a correct downgrade of the best-ranked one alone
  changes nothing, and the smoke-2 audit found unchallenged strong records on
  supporting passages right only about 45% of the time (references/
  vocabulary.md section 1.11);
* a **stratified sample** -- seeded, one stratum per scope a passage was
  demoted to, one for ``supporting`` demotions and one per weak relation
  (``relatedMatch``, ``narrowMatch``, ``hasPart``) on the remaining locations,
  drawn at ``sample_rate``; one per strong relation on the remaining locations
  of a ``supporting`` passage, drawn at ``strong_sample_rate``; and one of the
  ``supporting`` passages' absences, drawn at ``absence_sample_rate`` and
  hunted -- each at least one per non-empty stratum when its rate is above
  zero, and re-judged; what is not drawn is ``sampled_out``;
* everything else (a ``supporting`` passage's unresolved outcome) is
  ``not_required``.

The draw is a function of the seed and the record keys alone (each record's
rank is ``sha256(seed, key)``), so re-planning gives the same plan.  The
required records are batched about ``cflib.vocab.CHALLENGE_BATCH_RECORDS`` to a
task, one source's records kept together and in order, and a passage's records
never split over two tasks: one challenger judges a coverage claim and the
parts it names, and every location of one passage, so definitionally linked
declarations on it get one challenger's relations (the FM7 rule).  Each
location record also shows the passage's other locations, read-only, with
what the plan made of each (``passage_locations``).  The whole plan --
including every record's planned challenge value -- is written to
``runs/<id>/challenge-plan.json``, which the outcomes read back: a record a
plan required and no verdict judged is owed, never silently ``not_required``.

**Conditional records** (the smoke-3 audit's anomaly A3, the statement cut's fix 6,
docs/proposals/statement-cut.md section 5): a prominence sample that overturned a
``supporting`` passage to ``central`` reopened records no task held, which stayed
owed.  A plan made with ``conditional`` (the default from the statement cut's flip
on, since ``cflib.vocab.JUDGMENT_RULES`` is the statement cut's; a plan made before
the flip has none, and nothing changes for it) puts every location, absence and
unresolved record of a ``supporting`` passage whose prominence record is required,
and which the plan did not require itself, in that passage's task with
``conditional: "prominence"`` and its planned value unchanged
(:func:`mark_conditional`): the challenger judges
them only when it overturns the prominence to ``subject`` or ``central``, and the
outcomes apply such a verdict only then.  The records of a run of status rule 2
also carry the passage's ``statement``, ``conjuncts`` and ``shape``, and a coverage
record's parts each part's ``clauses`` and ``conjunct_relation``, where the
outcomes have them; a run of status rule 1 gets the records it always got, though
its parts carry ``clauses`` (the claim ids of judgment rules 1 and 2), so a
challenge planned over it now writes the tasks it wrote before (:func:`classify`).
"""
from __future__ import annotations

import math
from collections import defaultdict
from fractions import Fraction
from typing import Iterable

from cflib import hashing, vocab
from cflib.errors import CfError
from cflib.paths import WorkspacePaths

from . import outcomes as outcomes_mod

SCHEMA = outcomes_mod.CHALLENGE_PLAN_SCHEMA
DEFAULT_SAMPLE_RATE = vocab.CHALLENGE_SAMPLE_RATE
DEFAULT_STRONG_SAMPLE_RATE = vocab.CHALLENGE_STRONG_SAMPLE_RATE
DEFAULT_ABSENCE_SAMPLE_RATE = vocab.CHALLENGE_ABSENCE_SAMPLE_RATE
#: the rule each record falls under (``cflib.vocab.CHALLENGE_RULES``)
REFUTE = vocab.CHALLENGE_RULE_REFUTE
HUNT = vocab.CHALLENGE_RULE_HUNT
BASIS = vocab.CHALLENGE_RULE_BASIS
SAMPLE = vocab.CHALLENGE_RULE_SAMPLE
NONE = vocab.CHALLENGE_RULE_NONE
#: the stratum of the ``supporting`` passages' absences
ABSENCE_STRATUM = f"outcome:{vocab.OUTCOME_ABSENT}"
#: the stratum of the ``supporting`` passages' prominences, drawn at ``sample_rate``
SUPPORTING_STRATUM = f"prominence:{vocab.PROMINENCE_SUPPORTING}"
#: the stratum a ``subject`` passage's prominence record is named by: never drawn, since
#: every one is refuted by default (the article's QID rides on the passage's records)
SUBJECT_STRATUM = f"prominence:{vocab.PROMINENCE_SUBJECT}"
#: the plan's three sampling rates, as ``run.yaml`` and ``challenge-plan.json`` name them
RATE_KEYS = ("sample_rate", "strong_sample_rate", "absence_sample_rate")


#: what a coverage record names of each of its parts, for the challenger
_PART_KEYS = ("location_key", "library", "declaration", "relation", "rank")
#: what it names beside them under judgment rules 3 (a run of status rule 2), where the
#: part carries them: the conjuncts it states and its relation to them (the statement
#: cut's section 1.2).  Only there: a part of an earlier run carries ``clauses`` too, the
#: claim ids of its own claim list, which its coverage record never showed
_BUNDLE_PART_KEYS = ("clauses", "conjunct_relation", "pd6")
#: what a record copies from a status-rule-2 outcome row, where the row carries it
_STATEMENT_KEYS = ("statement", "conjuncts", "shape")
#: the challenge target a conditional record waits on (outcomes.CONDITIONAL_PROMINENCE)
CONDITIONAL = outcomes_mod.CONDITIONAL_PROMINENCE
#: the records a required prominence sample takes into its task as conditional ones
_CONDITIONAL_TARGETS = ("location", "absence", "unresolved")


def _rank(seed: int, key: str) -> str:
    return hashing.sha256_text(f"{int(seed)}\n{key}")


def _record(row: dict, target: str, *, run_id: str, location: dict | None = None,
            status_rule: int = vocab.STATUS_RULE_D1) -> dict:
    rec = {"target": target, "source_id": row["source_id"], "item": row["item"],
           "run_id": run_id, "prominence": row.get("prominence"),
           "scope": row.get("scope"), "passage_kind": row.get("passage_kind"),
           "outcome": row.get("outcome"), "span": row.get("span"),
           "section": row.get("section"), "quote": row.get("quote")}
    if row.get("claims") is not None:
        rec["claims"] = row["claims"]
    if vocab.reads_bundles(status_rule):
        rec.update({key: row[key] for key in _STATEMENT_KEYS if row.get(key) is not None})
    if location is not None:
        rec.update({"location_key": location["location_key"], "library": location["library"],
                    "declaration": location["declaration"], "relation": location["relation"],
                    "rank": location.get("rank"), "note": location.get("note") or None})
        rec.update({key: location[key] for key in vocab.JUDGMENT_LOCATION_FIELDS
                    if key in location})
    rec["key"] = outcomes_mod.record_key(target, row["source_id"], row["item"],
                                         rec.get("location_key"))
    return rec


def _by_rank(location: dict) -> tuple:
    return (location.get("rank") or 99, str(location.get("library")),
            str(location.get("declaration")))


def bases(row: dict) -> list[dict]:
    """Every location that sets a passage's derived status on its own, best first.

    Each location whose own status -- ``cflib.vocab.derive_status`` over it
    alone -- is the passage's (pre-challenge) status: the passage keeps that
    status while any one of them stands.  A passage with no status, or whose
    locations were not joined to the index (no ``proof_status``), has none.  No
    coverage claim is ever a basis: an unchallenged claim upgrades nothing, and
    every claim is refuted by default anyway.
    """
    status = row.get("status")
    if status is None:
        return []
    kind = row.get("passage_kind") or vocab.PASSAGE_OTHER
    out = []
    for loc in sorted(row.get("locations") or [], key=_by_rank):
        if loc.get("relation") == vocab.RELATED_MATCH or not loc.get("proof_status"):
            continue
        alone = {"relation": loc["relation"], "proof_status": loc["proof_status"],
                 "statement": bool(loc.get("statement"))}
        try:
            own = vocab.derive_status(kind, [alone], False)
        except CfError:
            continue
        if own == status:
            out.append(loc)
    return out


def basis(row: dict) -> dict | None:
    """The best-ranked of :func:`bases`, or None."""
    found = bases(row)
    return found[0] if found else None


def _location_rule(loc: dict, *, hot: bool, based: list[dict]) -> tuple[str, str | None]:
    """``(rule, stratum)`` of one location record."""
    relation = loc["relation"]
    if hot:
        return REFUTE, None
    if relation in vocab.STRONG_RELATIONS and vocab.admits_missing_clause(loc.get("note")):
        # its own note says it leaves a clause unstated: a strong relation that says the
        # opposite of itself is refuted by default, whatever the passage's prominence
        return REFUTE, None
    if relation in vocab.STRONG_RELATIONS and any(loc is b for b in based):
        return BASIS, None
    if relation in vocab.WEAK_RELATIONS or relation in vocab.STRONG_RELATIONS:
        return SAMPLE, f"relation:{relation}"
    return NONE, None


def classify(rows: Iterable[dict], *, run_id: str,
             status_rule: int = vocab.STATUS_RULE_D1) -> list[dict]:
    """Every record of one run's outcomes, each with its rule and stratum.

    ``status_rule`` is the locate run's (``outcomes.build``'s ``status_rule``): only
    under status rules 2 and 3 does a record copy the passage's ``statement``,
    ``conjuncts`` and ``shape`` and a coverage record's part its ``clauses`` and
    ``conjunct_relation`` (the statement cut's section 1.2), so the records of a
    run of status rule 1 are the ones they always were.
    """
    bundle = vocab.reads_bundles(status_rule)
    out: list[dict] = []
    for row in rows:
        if row.get("missing"):
            continue
        hot = row.get("prominence") in vocab.REFUTE_BY_DEFAULT_PROMINENCE
        based = [] if hot else bases(row)
        for loc in row.get("locations") or []:
            rec = _record(row, "location", run_id=run_id, location=loc,
                          status_rule=status_rule)
            rule, stratum = _location_rule(loc, hot=hot, based=based)
            rec.update(rule=rule, stratum=stratum)
            out.append(rec)
        claim = row.get("coverage")
        if claim:
            rec = _record(row, vocab.COVERAGE_TARGET, run_id=run_id, status_rule=status_rule)
            by_key = {loc["location_key"]: loc for loc in row.get("locations") or []}
            rec["parts"] = [{k: by_key.get(key, {}).get(k) for k in _PART_KEYS}
                            | ({k: by_key[key][k] for k in _BUNDLE_PART_KEYS
                                if by_key.get(key, {}).get(k) is not None} if bundle else {})
                            | {"location_key": key} for key in claim["parts"]]
            rec["note"] = claim.get("note")
            rec.update(rule=REFUTE, stratum=None)
            out.append(rec)
        outcome = row.get("outcome")
        if outcome in (vocab.OUTCOME_ABSENT, vocab.OUTCOME_UNRESOLVED):
            target = "absence" if outcome == vocab.OUTCOME_ABSENT else "unresolved"
            rec = _record(row, target, run_id=run_id, status_rule=status_rule)
            if hot:
                rec.update(rule=HUNT, stratum=None)
            elif outcome == vocab.OUTCOME_ABSENT:
                rec.update(rule=SAMPLE, stratum=ABSENCE_STRATUM)
            else:
                rec.update(rule=NONE, stratum=None)
            out.append(rec)
        elif outcome is None:
            rec = _record(row, "scope", run_id=run_id, status_rule=status_rule)
            rec.update(rule=SAMPLE, stratum=f"scope:{row.get('scope')}")
            out.append(rec)
        if row.get("prominence") == vocab.PROMINENCE_SUPPORTING:
            rec = _record(row, "prominence", run_id=run_id, status_rule=status_rule)
            rec.update(rule=SAMPLE, stratum=SUPPORTING_STRATUM)
            out.append(rec)
        elif row.get("prominence") == vocab.PROMINENCE_SUBJECT:
            # the QID its records carry rests on this label: required, whatever the rates
            rec = _record(row, "prominence", run_id=run_id, status_rule=status_rule)
            rec.update(rule=REFUTE, stratum=SUBJECT_STRATUM)
            out.append(rec)
    return out


#: what a location record shows of each other location on its passage (read-only)
_SIBLING_KEYS = ("location_key", "library", "declaration", "relation", "rank", "planned")


def show_siblings(records: list[dict]) -> None:
    """Give each location record the passage's other locations, read-only (the FM7 rule).

    ``passage_locations``: each other location of the same passage of the same
    run, with the plan's value for it (``required``, ``sampled_out``,
    ``not_required``), best rank first.  A challenger keeps definitionally linked
    declarations at one relation; the ones its task does not judge are context,
    never records it gives a verdict for.
    """
    by_passage: dict[tuple, list[dict]] = defaultdict(list)
    for rec in records:
        if rec["target"] == "location":
            by_passage[(rec["run_id"], rec["source_id"], rec["item"])].append(rec)
    for members in by_passage.values():
        members.sort(key=lambda r: (r.get("rank") or 99, str(r.get("library")),
                                    str(r.get("declaration"))))
        for rec in members:
            rec["passage_locations"] = [{k: other.get(k) for k in _SIBLING_KEYS}
                                        for other in members if other is not rec]


def rates_of(sample_rate: float = DEFAULT_SAMPLE_RATE,
             strong_sample_rate: float = DEFAULT_STRONG_SAMPLE_RATE,
             absence_sample_rate: float = DEFAULT_ABSENCE_SAMPLE_RATE) -> dict[str, float]:
    """The plan's three rates by their ``RATE_KEYS`` names, each checked to be in [0, 1]."""
    rates = {"sample_rate": sample_rate, "strong_sample_rate": strong_sample_rate,
             "absence_sample_rate": absence_sample_rate}
    for name, rate in rates.items():
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
            raise CfError(f"the {name.replace('_', ' ')} is a fraction in [0, 1], not {rate}",
                          hint="--sample-rate, --strong-sample-rate and --absence-sample-rate "
                               "each take a fraction of its strata")
    return rates


def stratum_rate(stratum: str, rates: dict[str, float]) -> float:
    """The rate one stratum is drawn at: the strong relations', the absences', else the plan's."""
    if stratum == ABSENCE_STRATUM:
        return rates["absence_sample_rate"]
    if stratum.startswith("relation:") and stratum.split(":", 1)[1] in vocab.STRONG_RELATIONS:
        return rates["strong_sample_rate"]
    return rates["sample_rate"]


def draw(records: list[dict], *, seed: int, sample_rate: float,
         strong_sample_rate: float = DEFAULT_STRONG_SAMPLE_RATE,
         absence_sample_rate: float = DEFAULT_ABSENCE_SAMPLE_RATE) -> list[dict]:
    """Give every record its planned value: required, sampled_out or not_required."""
    rates = rates_of(sample_rate, strong_sample_rate, absence_sample_rate)
    strata: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        if rec["rule"] in (REFUTE, HUNT, BASIS):
            rec["planned"] = vocab.CHALLENGE_REQUIRED
        elif rec["rule"] == SAMPLE:
            strata[rec["stratum"]].append(rec)
        else:
            rec["planned"] = vocab.CHALLENGE_NOT_REQUIRED
    for stratum, members in sorted(strata.items()):
        members.sort(key=lambda r: _rank(seed, r["key"]))
        rate = stratum_rate(stratum, rates)
        # exact arithmetic: 0.07 of 100 records is 7, never the 8 a float product rounds to
        take = math.ceil(Fraction(str(rate)) * len(members)) if rate > 0 else 0
        for i, rec in enumerate(members):
            rec["planned"] = vocab.CHALLENGE_REQUIRED if i < take else vocab.CHALLENGE_SAMPLED_OUT
    return records


def mark_conditional(records: list[dict]) -> list[dict]:
    """Mark each record a required prominence sample takes into its task (anomaly A3).

    For a ``supporting`` passage whose prominence record the draw required, each of
    its location, absence and unresolved records the plan did not require gets
    ``conditional: "prominence"``, its planned value unchanged: :func:`batches` puts
    it in the passage's task, beside the prominence record, so a challenger who
    overturns the prominence to ``subject`` or ``central`` judges it there, as a
    refuted-by-default or hunted record, and one who does not leaves it alone.
    Returns the records marked.
    """
    sampled = {(r["run_id"], r["source_id"], r["item"]) for r in records
               if r["target"] == "prominence" and r["planned"] == vocab.CHALLENGE_REQUIRED
               and r.get("prominence") == vocab.PROMINENCE_SUPPORTING}
    marked = []
    for rec in records:
        if rec["target"] in _CONDITIONAL_TARGETS and rec["planned"] != vocab.CHALLENGE_REQUIRED \
                and (rec["run_id"], rec["source_id"], rec["item"]) in sampled:
            rec["conditional"] = CONDITIONAL
            marked.append(rec)
    return marked


def _in_task(rec: dict) -> bool:
    """A record a challenge task holds: a required one, or a conditional one."""
    return rec["planned"] == vocab.CHALLENGE_REQUIRED or rec.get("conditional") == CONDITIONAL


def batches(records: list[dict], *, size: int = vocab.CHALLENGE_BATCH_RECORDS) -> list[list[dict]]:
    """The required records in tasks of at most ``size``, sources kept in order.

    A passage is one unit: its records go to one task (alone in a task when they
    are more than ``size``), so the challenger who judges a coverage claim judges
    the parts it names too, and one challenger judges every required location of
    a passage -- two encodings of one object, or a definition and the declaration
    defined from it, get one challenger's relations (the FM7 rule).  A conditional
    record (:func:`mark_conditional`) joins its passage's unit, so it is in the task
    of the prominence record whose overturn it waits on.
    """
    required = sorted((r for r in records if _in_task(r)),
                      key=lambda r: (r["source_id"], (r.get("span") or [0])[0], r["item"],
                                     r["target"], r["key"]))
    units: list[list[dict]] = []
    for rec in required:
        passage = (rec["source_id"], rec["item"])
        if units and (units[-1][0]["source_id"], units[-1][0]["item"]) == passage:
            units[-1].append(rec)
        else:
            units.append([rec])
    out: list[list[dict]] = []
    for unit in units:
        if not out or len(out[-1]) + len(unit) > size:
            out.append([])
        out[-1].extend(unit)
    return out


def plan(paths: WorkspacePaths, locate_runs: list[str], *, seed: int, sample_rate: float,
         strong_sample_rate: float = DEFAULT_STRONG_SAMPLE_RATE,
         absence_sample_rate: float = DEFAULT_ABSENCE_SAMPLE_RATE,
         batch_size: int = vocab.CHALLENGE_BATCH_RECORDS,
         conditional: bool | None = None) -> dict:
    """The challenge plan over harvested locate runs: records, strata, rates and task batches.

    ``conditional`` marks the conditional records of anomaly A3
    (:func:`mark_conditional`); by default it is on exactly when the judgment rules
    the plan's gate will name are the statement cut's (``cflib.vocab.JUDGMENT_RULES``
    at ``RULES_STATEMENT_CUT`` or later), whose challenge gate judges them.
    """
    rates = rates_of(sample_rate, strong_sample_rate, absence_sample_rate)
    if conditional is None:
        conditional = vocab.JUDGMENT_RULES >= vocab.RULES_STATEMENT_CUT
    if not locate_runs:
        raise CfError("a challenge plan judges harvested locate runs",
                      hint="name them with --challenge-of <run-id> (repeatable)")
    records: list[dict] = []
    inputs_seen: dict[str, dict] = {}
    for run_id in locate_runs:
        built = outcomes_mod.build(paths, run_id, require_gate=True)
        if built["mode"] != vocab.LOCATE:
            raise CfError(f"run {run_id} is a {built['mode']} run; a challenge judges locate runs")
        inputs_seen[run_id] = {"sourceset": built["sourceset"], "universe": built["universe"]}
        records.extend(classify(built["outcomes"], run_id=run_id,
                                status_rule=built["status_rule"]))
    pins = {(str(v["sourceset"]), str(v["universe"])) for v in inputs_seen.values()}
    if len(pins) > 1:
        raise CfError("the locate runs of one challenge pin different sourcesets or universes",
                      hint="challenge each universe's runs separately")
    seen: dict[str, str] = {}
    for rec in records:
        other = seen.get(rec["key"])
        if other is not None and other != rec["run_id"]:
            raise CfError(f"record {rec['key'][:12]} ({rec['source_id']} {rec['item']}, "
                          f"{rec['target']}) appears in two locate runs, {other} and "
                          f"{rec['run_id']}",
                          hint="a passage is located by one run; challenge one at a time")
        if other is not None:
            raise CfError(f"run {rec['run_id']}: {rec['source_id']} {rec['item']} lists "
                          f"{rec.get('library')}:{rec.get('declaration')} twice",
                          hint="a passage names a declaration once; the outcomes keep its best "
                               "rank and say so")
        seen[rec["key"]] = rec["run_id"]
    draw(records, seed=seed, **rates)
    if conditional:
        mark_conditional(records)
    show_siblings(records)
    groups = batches(records, size=batch_size)
    first = next(iter(inputs_seen.values()))
    return {"records": records, "batches": groups, "sourceset": first["sourceset"],
            "universe": first["universe"], "rates": rates}


def plan_document(run_id: str, built: dict, *, seed: int, sample_rate: float,
                  task_of: dict[str, str], challenged_runs: list[str],
                  strong_sample_rate: float = DEFAULT_STRONG_SAMPLE_RATE,
                  absence_sample_rate: float = DEFAULT_ABSENCE_SAMPLE_RATE) -> dict:
    """``challenge-plan.json``: every record, its rule, stratum, planned value and task,
    and the three rates the plan drew at."""
    rates = rates_of(sample_rate, strong_sample_rate, absence_sample_rate)
    rows = []
    for rec in sorted(built["records"], key=lambda r: (r["source_id"], r["item"], r["target"],
                                                       r["key"])):
        rows.append({k: rec.get(k) for k in (
            "key", "target", "source_id", "item", "location_key", "library", "declaration",
            "relation", "outcome", "run_id", "prominence", "scope", "rule", "stratum",
            "planned")}
            | {"task_id": task_of.get(rec["key"])}
            | ({"parts": [part["location_key"] for part in rec["parts"]]}
               if rec["target"] == vocab.COVERAGE_TARGET else {})
            | ({"conditional": rec["conditional"]} if rec.get("conditional") else {}))
    counts: dict[str, int] = defaultdict(int)
    for rec in rows:
        counts[rec["planned"]] += 1
    return {"schema": SCHEMA, "run_id": run_id,
            "challenged_runs": sorted(challenged_runs),
            "seed": int(seed), **rates, "counts": dict(sorted(counts.items())),
            "records": rows}
