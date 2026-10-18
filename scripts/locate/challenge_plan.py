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
* a **stratified sample** -- seeded, one stratum per scope a passage was
  demoted to, one for ``supporting`` demotions and one per weak relation
  (``relatedMatch``, ``narrowMatch``, ``hasPart``) on the remaining locations
  -- is drawn at ``sample_rate`` (at least one per non-empty stratum) and
  re-judged; what is not drawn is ``sampled_out``;
* everything else is ``not_required``.

The draw is a function of the seed and the record keys alone (each record's
rank is ``sha256(seed, key)``), so re-planning gives the same plan.  The
required records are batched about ``cflib.vocab.CHALLENGE_BATCH_RECORDS`` to a
task, one source's records kept together and in order, and a passage with a
coverage claim never split over two tasks: one challenger judges the claim and
the parts it names.  The whole plan --
including every record's planned challenge value -- is written to
``runs/<id>/challenge-plan.json``, which the outcomes read back: a record a
plan required and no verdict judged is owed, never silently ``not_required``.
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
DEFAULT_SAMPLE_RATE = 0.2
#: the rule each record falls under (``cflib.vocab.CHALLENGE_RULES``)
REFUTE = vocab.CHALLENGE_RULE_REFUTE
HUNT = vocab.CHALLENGE_RULE_HUNT
SAMPLE = vocab.CHALLENGE_RULE_SAMPLE
NONE = vocab.CHALLENGE_RULE_NONE


#: what a coverage record names of each of its parts, for the challenger
_PART_KEYS = ("location_key", "library", "declaration", "relation", "rank")


def _rank(seed: int, key: str) -> str:
    return hashing.sha256_text(f"{int(seed)}\n{key}")


def _record(row: dict, target: str, *, run_id: str, location: dict | None = None) -> dict:
    rec = {"target": target, "source_id": row["source_id"], "item": row["item"],
           "run_id": run_id, "prominence": row.get("prominence"),
           "scope": row.get("scope"), "passage_kind": row.get("passage_kind"),
           "outcome": row.get("outcome"), "span": row.get("span"),
           "section": row.get("section"), "quote": row.get("quote")}
    if location is not None:
        rec.update({"location_key": location["location_key"], "library": location["library"],
                    "declaration": location["declaration"], "relation": location["relation"],
                    "rank": location.get("rank")})
    rec["key"] = outcomes_mod.record_key(target, row["source_id"], row["item"],
                                         rec.get("location_key"))
    return rec


def classify(rows: Iterable[dict], *, run_id: str) -> list[dict]:
    """Every record of one run's outcomes, each with its rule and stratum."""
    out: list[dict] = []
    for row in rows:
        if row.get("missing"):
            continue
        hot = row.get("prominence") in vocab.REFUTE_BY_DEFAULT_PROMINENCE
        for loc in row.get("locations") or []:
            rec = _record(row, "location", run_id=run_id, location=loc)
            if hot:
                rec.update(rule=REFUTE, stratum=None)
            elif loc["relation"] in vocab.WEAK_RELATIONS:
                rec.update(rule=SAMPLE, stratum=f"relation:{loc['relation']}")
            else:
                rec.update(rule=NONE, stratum=None)
            out.append(rec)
        claim = row.get("coverage")
        if claim:
            rec = _record(row, vocab.COVERAGE_TARGET, run_id=run_id)
            by_key = {loc["location_key"]: loc for loc in row.get("locations") or []}
            rec["parts"] = [{k: by_key.get(key, {}).get(k) for k in _PART_KEYS}
                            | {"location_key": key} for key in claim["parts"]]
            rec["note"] = claim.get("note")
            rec.update(rule=REFUTE, stratum=None)
            out.append(rec)
        outcome = row.get("outcome")
        if outcome in (vocab.OUTCOME_ABSENT, vocab.OUTCOME_UNRESOLVED):
            target = "absence" if outcome == vocab.OUTCOME_ABSENT else "unresolved"
            rec = _record(row, target, run_id=run_id)
            rec.update(rule=HUNT if hot else NONE, stratum=None)
            out.append(rec)
        elif outcome is None:
            rec = _record(row, "scope", run_id=run_id)
            rec.update(rule=SAMPLE, stratum=f"scope:{row.get('scope')}")
            out.append(rec)
        if row.get("prominence") == vocab.PROMINENCE_SUPPORTING:
            rec = _record(row, "prominence", run_id=run_id)
            rec.update(rule=SAMPLE, stratum="prominence:supporting")
            out.append(rec)
    return out


def draw(records: list[dict], *, seed: int, sample_rate: float) -> list[dict]:
    """Give every record its planned value: required, sampled_out or not_required."""
    if not 0 <= sample_rate <= 1:
        raise CfError(f"the sample rate is a fraction in [0, 1], not {sample_rate}")
    strata: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        if rec["rule"] in (REFUTE, HUNT):
            rec["planned"] = vocab.CHALLENGE_REQUIRED
        elif rec["rule"] == SAMPLE:
            strata[rec["stratum"]].append(rec)
        else:
            rec["planned"] = vocab.CHALLENGE_NOT_REQUIRED
    for stratum, members in sorted(strata.items()):
        members.sort(key=lambda r: _rank(seed, r["key"]))
        # exact arithmetic: 0.07 of 100 records is 7, never the 8 a float product rounds to
        take = math.ceil(Fraction(str(sample_rate)) * len(members)) if sample_rate > 0 else 0
        for i, rec in enumerate(members):
            rec["planned"] = vocab.CHALLENGE_REQUIRED if i < take else vocab.CHALLENGE_SAMPLED_OUT
    return records


def batches(records: list[dict], *, size: int = vocab.CHALLENGE_BATCH_RECORDS) -> list[list[dict]]:
    """The required records in tasks of at most ``size``, sources kept in order.

    A passage with a coverage claim is one unit: its records go to one task
    (alone in a task when they are more than ``size``), so the challenger who
    judges the claim judges the parts it names too.
    """
    required = sorted((r for r in records if r["planned"] == vocab.CHALLENGE_REQUIRED),
                      key=lambda r: (r["source_id"], (r.get("span") or [0])[0], r["item"],
                                     r["target"], r["key"]))
    together = {(r["source_id"], r["item"]) for r in required
                if r["target"] == vocab.COVERAGE_TARGET}
    units: list[list[dict]] = []
    for rec in required:
        passage = (rec["source_id"], rec["item"])
        if passage in together and units and (units[-1][0]["source_id"],
                                              units[-1][0]["item"]) == passage:
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
         batch_size: int = vocab.CHALLENGE_BATCH_RECORDS) -> dict:
    """The challenge plan over harvested locate runs: records, strata and task batches."""
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
        records.extend(classify(built["outcomes"], run_id=run_id))
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
    draw(records, seed=seed, sample_rate=sample_rate)
    groups = batches(records, size=batch_size)
    first = next(iter(inputs_seen.values()))
    return {"records": records, "batches": groups, "sourceset": first["sourceset"],
            "universe": first["universe"]}


def plan_document(run_id: str, built: dict, *, seed: int, sample_rate: float,
                  task_of: dict[str, str], challenged_runs: list[str]) -> dict:
    """``challenge-plan.json``: every record, its rule, stratum, planned value and task."""
    rows = []
    for rec in sorted(built["records"], key=lambda r: (r["source_id"], r["item"], r["target"],
                                                       r["key"])):
        rows.append({k: rec.get(k) for k in (
            "key", "target", "source_id", "item", "location_key", "library", "declaration",
            "relation", "outcome", "run_id", "prominence", "scope", "rule", "stratum",
            "planned")}
            | {"task_id": task_of.get(rec["key"])}
            | ({"parts": [part["location_key"] for part in rec["parts"]]}
               if rec["target"] == vocab.COVERAGE_TARGET else {}))
    counts: dict[str, int] = defaultdict(int)
    for rec in rows:
        counts[rec["planned"]] += 1
    return {"schema": SCHEMA, "run_id": run_id,
            "challenged_runs": sorted(challenged_runs),
            "seed": int(seed), "sample_rate": sample_rate, "counts": dict(sorted(counts.items())),
            "records": rows}
