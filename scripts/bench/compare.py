"""The section 9 acceptance rule, and the loop ledger it writes.

New code, implementing docs/proposals/wikipedia-locate.md section 9 (steps 2 to
4) and the refusals of section 8.3, in the shape references/locate-bench.md
section 9 fixes.

A comparison is between two score documents of ONE bench, gold manifest, split
and arm, scored by one scorer (``scorer``, the sha of ``scripts/bench/*.py``).
It is refused outright when the two runs' observed model sets, renderers or
tools databases (full, blind, planted) differ -- a model change is a new
baseline, never an improvement -- and when one was scored after challenge and
the other was not, or one still owes challenge verdicts the other does not.
It then pairs the two runs cluster by cluster (the score documents carry every
ratio per article cluster) and checks, at once:

1. the pre-registered metric improves, the lower bound of its paired interval
   above zero at the comparison's alpha -- and it holds on the unseen-declaration
   stratum too (section 8.3), which outside dev must exist for the metric;
2. the safety metrics (false-not-formalized and overclaim rates, in every tier
   both documents hold) do not regress: the upper bound of their increase is at
   most 0.01 -- a baseline with no such errors counts as a rate of zero, never
   as a pass;
3. no stratum with n >= 30 drops by more than three points on its interval, for
   any metric both documents stratify -- n the stratum's unit count, never its
   denominator, which G3 weights;
4. the cost per article rises by at most 20% on its paired interval -- the
   locate and challenge spend alone when the two runs share their inventory
   runs (a shared inventory's cost would dilute the rise), every stage otherwise;
   two price tables, two cost bases, or a zero-cost baseline the candidate
   exceeds, fail;
5. the A/A comparison -- the baseline against a distinct replicate run of its
   own configuration (the configuration shas each plan records, never the bytes
   it rendered for its own run id) -- does NOT accept;
6. multiplicity: the alpha of the j-th claimed comparison in the bench's ledger
   is ``alpha * 2**-j`` (alpha-spending), unless the comparison replicates an
   accepted claim of ANOTHER bench (a fresh draw), named by its ledger entry,
   which spends nothing; the bootstrap must resolve that alpha.

The ledger is ``benches/<id>/ledger.jsonl``, one per bench, read and appended
under an exclusive lock, so two comparisons never spend one alpha.  Only
G1-blind, G3 and P metrics may be pre-registered (section 8.1).  Below about
150 eval articles or 400 gold positives, or without a canary check, a decision
is RECORDED, not claimed (section 9, step 3).
"""
from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path
from typing import Any, Iterator

from cflib import vocab
from cflib.errors import CfError
from cflib.jsonio import read_json

from . import metrics
from .layout import (LEDGER_SCHEMA, UNRECORDED, BenchLayout, now, read_jsonl_rows, sha_of,
                     write_canonical_jsonl)

#: below both of these a decision is recorded, not claimed
CLAIM_MIN_ARTICLES = 150
CLAIM_MIN_POSITIVES = 400
SAFETY_MAX_INCREASE = 0.01
STRATUM_MIN_N = 30
STRATUM_MAX_DROP = 0.03
COST_MAX_INCREASE = 0.20
DEFAULT_ALPHA = 0.05
#: the fewest bootstrap draws that may lie below the bound an alpha asks for
MIN_TAIL_DRAWS = 10
#: the tiers a pre-registered metric may come from (G1 only in the blind arm), and every
#: tier in its reporting order: cflib.vocab's (CLAUDE.md section 4: one vocabulary)
CLAIM_TIERS = vocab.BENCH_CLAIM_TIERS
TIERS = vocab.BENCH_TIER_ORDER
#: the pins that say how a run was configured: noted when two scores differ, refused
#: between a baseline and its A/A replicate (corpus_db_sha256 is a recorded pin only:
#: a rebuild of one universe state is not byte-identical, references/locate-documents.md)
CONFIG_PINS = ("universe_sha256", "sourceset_manifest_sha256", "agent_cli", "tend", "agentbox",
               "skill_commit", "gate_files", "prompts", "agents", "planning",
               "price_table_sha256")
NOTED_PINS = CONFIG_PINS[:1] + ("corpus_db_sha256",) + CONFIG_PINS[1:]
#: the per-run configuration pins a plan records in run.yaml locate.config_shas: the
#: gate code and prompt templates without the bytes a plan renders per run (run id,
#: run paths), and the agent wrappers' settings (the sandbox bypass among them, review
#: SEAM-1), so a run planned before they were recorded cannot be told apart
CONFIG_SHA_PINS = ("gate_files", "prompts", "agents")


def load_score(path: Path) -> dict:
    doc = read_json(path)
    if not isinstance(doc, dict) or doc.get("schema") != "cf-bench-score/1":
        raise CfError(f"{path} is not a cf-bench-score/1 document")
    return doc


def refuse_subset(doc: dict, path: Path, role: str) -> None:
    """The acceptance rule reads whole-split scores only: a subset score is refused by name."""
    if doc.get("subset"):
        n = len(doc.get("subset_articles") or [])
        raise CfError(f"the {role} {path} is a subset score ({n} article(s) of the "
                      f"{doc.get('split')} split, subset sha {str(doc.get('subset_sha256'))[:12]}…)",
                      hint="a pre-registered subset is a measurement, never a claim: compare "
                           "scores of the whole split")


def _challenge_state(doc: dict) -> tuple[bool, bool]:
    """``(scored after a challenge run, rows still owed a verdict)`` of one score."""
    owed = ((doc.get("coverage") or {}).get("challenge_owed") or {}).get("total") or 0
    return bool((doc.get("pins") or {}).get("challenge_runs")), int(owed) > 0


def _values(pin) -> Any:
    """A per-run pin (``{run: value}``) as its sorted values; any other pin as itself."""
    if isinstance(pin, dict):
        return sorted({sha_of(v) if isinstance(v, (dict, list)) else str(v) for v in pin.values()})
    return pin


def _planted(doc: dict) -> bool:
    return ((doc.get("pins") or {}).get("tools_corpus_db") or {}).get("kind") == \
        vocab.TOOLS_DB_PLANTED \
        or "P" in (doc.get("tiers") or {})


def pin_differences(base: dict, cand: dict, pins=NOTED_PINS) -> list[str]:
    return [pin for pin in pins
            if _values((base.get("pins") or {}).get(pin)) != _values((cand.get("pins") or {}).get(pin))]


def refuse_incomparable(base: dict, cand: dict) -> list[str]:
    """Raise on what makes two scores incomparable; return the softer differences."""
    for key, why in (("bench", "two benches"), ("split", "two splits"), ("arm", "two arms")):
        if base.get(key) != cand.get(key):
            raise CfError(f"the scores are of {why}: {base.get(key)!r} and {cand.get(key)!r}",
                          hint="a comparison is within one bench, split and arm; arms are never pooled")
    if base["gold_manifest"]["sha256"] != cand["gold_manifest"]["sha256"]:
        raise CfError("the scores were measured against two gold manifests",
                      hint="score both runs against the same frozen gold")
    b_scorer = (base.get("scorer") or {}).get("sha256")
    c_scorer = (cand.get("scorer") or {}).get("sha256")
    if b_scorer != c_scorer:
        raise CfError(f"the scores were computed by two scorers ({str(b_scorer)[:12]}… and "
                      f"{str(c_scorer)[:12]}…)",
                      hint="re-score both runs with this checkout's cf bench score")
    b_tools = ((base["pins"].get("tools_corpus_db") or {}).get("kind")) or vocab.TOOLS_DB_FULL
    c_tools = ((cand["pins"].get("tools_corpus_db") or {}).get("kind")) or vocab.TOOLS_DB_FULL
    if b_tools != c_tools or _planted(base) != _planted(cand):
        raise CfError(f"the workers searched a {b_tools} and a {c_tools} database",
                      hint="a planted (P) or blind tools database is its own measurement: "
                           "compare planted runs only with planted runs")
    b_chal, c_chal = _challenge_state(base), _challenge_state(cand)
    if b_chal[0] != c_chal[0]:
        raise CfError("one score was tallied after a challenge run and the other was not",
                      hint="challenge both runs (cf tally --locate --challenge-run) or neither, "
                           "then score them again")
    if b_chal[1] != c_chal[1]:
        raise CfError("one score still owes challenge verdicts (coverage.challenge_owed) and "
                      "the other does not",
                      hint="finish the challenge of the run that owes them, tally and score again")
    base_models = set(base["pins"].get("observed_models") or {})
    cand_models = set(cand["pins"].get("observed_models") or {})
    if base_models != cand_models:
        raise CfError(f"the observed model sets differ ({sorted(base_models)} vs {sorted(cand_models)}): "
                      f"a model change is a new baseline, never an improvement",
                      hint="score the new model as a baseline of its own, with its own A/A run")
    if base["pins"].get("renderer") != cand["pins"].get("renderer"):
        raise CfError(f"the renderers differ ({base['pins'].get('renderer')} vs "
                      f"{cand['pins'].get('renderer')})",
                      hint="a renderer change re-keys passages; it is a new baseline")
    notes = [f"{pin} differs" for pin in pin_differences(base, cand)]
    if b_chal[1] and c_chal[1]:
        notes.append("both scores owe challenge verdicts")
    return notes


def check_replicate(base: dict, replicate: dict) -> None:
    """Refuse an A/A ``replicate`` that is not a distinct run of the baseline's configuration."""
    refuse_incomparable(base, replicate)
    same_run = replicate.get("measurement_id") == base.get("measurement_id") \
        or replicate.get("run") == base.get("run")
    b_receipts, r_receipts = (base.get("pins") or {}).get("receipts") or {}, \
        (replicate.get("pins") or {}).get("receipts") or {}
    if same_run or (base.get("run") in b_receipts and
                    b_receipts.get(base.get("run")) == r_receipts.get(replicate.get("run"))):
        raise CfError("the A/A replicate is the baseline's own run, not a replicate of it",
                      hint="re-run the baseline's configuration as a fresh locate run and score it")
    unrecorded = sorted({f"{pin} of {rid}" for doc in (base, replicate)
                         for pin in CONFIG_SHA_PINS
                         for rid, value in _per_run((doc.get("pins") or {}).get(pin), doc)
                         if value == UNRECORDED})
    if unrecorded:
        raise CfError("the A/A check cannot tell the two configurations apart: run.yaml "
                      "records no locate.config_shas for " + ", ".join(unrecorded),
                      hint="re-plan those runs with this checkout's cf plan-locate (it records "
                           "the configuration's shas), then run, harvest, tally and score them")
    differ = pin_differences(base, replicate, CONFIG_PINS)
    if differ:
        raise CfError(f"the A/A replicate is not the baseline's configuration: "
                      f"{', '.join(differ)} differ",
                      hint="an A/A replicate re-runs the baseline's configuration unchanged")


def _per_run(pin, doc: dict) -> list[tuple[str, Any]]:
    """A per-run pin's ``(run, value)`` pairs; a missing one is the score's run, unrecorded."""
    if isinstance(pin, dict):
        return sorted(pin.items())
    return [(str(doc.get("run") or "?"), UNRECORDED if pin is None else pin)]


def _metric(name: str) -> tuple[str, str]:
    tier, _, metric = name.partition(".")
    if not metric:
        raise CfError(f"a claim names a tier and a metric, e.g. G3.fnf_rate, not {name!r}")
    return tier, metric


def _improvement(draws: list[float], metric: str) -> list[float]:
    """Paired draws of (cand - base) turned into improvement: positive is better."""
    if metric in metrics.LOWER_IS_BETTER:
        return sorted(-d for d in draws)
    return sorted(draws)


def _resolvable(draws: list[float], alpha_j: float, what: str) -> None:
    """Refuse a bound the bootstrap cannot resolve: fewer than ten draws below it."""
    if draws and len(draws) * alpha_j < MIN_TAIL_DRAWS:
        need = int(-(-MIN_TAIL_DRAWS // alpha_j))
        raise CfError(f"{what}: alpha {alpha_j:.3g} needs at least {need} bootstrap draws, "
                      f"there are {len(draws)}",
                      hint=f"re-run with --bootstrap {need} (alpha-spending tightens with every "
                           "claimed comparison)")


def _cells(doc: dict, tier: str, metric: str) -> dict[str, list[float]]:
    try:
        return doc["per_cluster"][tier][metric]
    except KeyError:
        raise CfError(f"{doc.get('measurement_id', '?')[:12]} has no {tier}.{metric}") from None


def _clusters(base: dict, cand: dict) -> list[str]:
    found: set[str] = set()
    for doc in (base, cand):
        for tier in doc.get("per_cluster") or {}:
            for cells in doc["per_cluster"][tier].values():
                found |= set(cells)
    return sorted(found)


def _stratum(doc: dict, tier: str, key: str, metric: str) -> dict | None:
    return (((doc.get("strata") or {}).get(tier) or {}).get(key) or {}).get(metric)


def _stratum_n(cell: dict, doc: dict, where: str) -> int:
    """A stratum's unit count (``n``), never its denominator: a G3 denominator is weighted."""
    n = cell.get("n")
    if isinstance(n, bool) or not isinstance(n, int):
        raise CfError(f"{doc.get('measurement_id', '?')[:12]}: stratum {where} counts no units (n)",
                      hint="re-score both runs with this checkout's cf bench score")
    return n


def evaluate(base: dict, cand: dict, *, claim: str, alpha_j: float, replicates: int,
             seed: int) -> dict[str, Any]:
    """Every condition of the acceptance rule but the A/A and the claim threshold."""
    tier, metric = _metric(claim)
    if tier not in CLAIM_TIERS:
        raise CfError(f"{claim}: only G1-blind, G3 and P metrics may be pre-registered")
    if tier == "G1" and cand.get("arm") != vocab.BENCH_ARM_BLIND:
        raise CfError(f"{claim}: a G1 metric is pre-registered only in the blind arm")
    clusters = _clusters(base, cand)
    conditions: dict[str, Any] = {}
    draws = _improvement(metrics.paired(_cells(base, tier, metric), _cells(cand, tier, metric),
                                        clusters, replicates=replicates, seed=f"{seed}:claim"),
                         metric)
    _resolvable(draws, alpha_j, claim)
    lower = metrics.quantile(draws, alpha_j)
    conditions["claim"] = {"metric": claim, "alpha": alpha_j, "improvement_lower": lower,
                           "ok": lower is not None and lower > 0}
    if cand.get("split") != "dev":
        b, c = _stratum(base, tier, "seen:no", metric), _stratum(cand, tier, "seen:no", metric)
        if not b or not c:
            conditions["claim_unseen"] = {"improvement_lower": None, "ok": False,
                                          "why": f"no unseen-declaration stratum for {claim}"}
        else:
            unseen = _improvement(metrics.paired(b["by_cluster"], c["by_cluster"], clusters,
                                                 replicates=replicates, seed=f"{seed}:unseen"),
                                  metric)
            _resolvable(unseen, alpha_j, f"{claim} on seen:no")
            low = metrics.quantile(unseen, alpha_j)
            conditions["claim_unseen"] = {"improvement_lower": low, "ok": low is not None and low > 0}
    conditions["safety"] = _safety(base, cand, clusters, replicates, seed)
    strata = {}
    for key in sorted(set((base.get("strata") or {}).get(tier, {}))
                      & set((cand.get("strata") or {}).get(tier, {}))):
        for name in sorted(set(base["strata"][tier][key]) & set(cand["strata"][tier][key])):
            b, c = base["strata"][tier][key][name], cand["strata"][tier][key][name]
            n = min(_stratum_n(b, base, f"{tier}.{key}.{name}"),
                    _stratum_n(c, cand, f"{tier}.{key}.{name}"))
            if n < STRATUM_MIN_N:
                continue
            d = _improvement(metrics.paired(b["by_cluster"], c["by_cluster"], clusters,
                                            replicates=replicates,
                                            seed=f"{seed}:stratum:{key}:{name}"), name)
            low = metrics.quantile(d, 0.025)
            strata[f"{key}.{name}"] = {"n": n, "improvement_lower": low,
                                       "ok": low is not None and low >= -STRATUM_MAX_DROP}
    conditions["strata"] = {"checked": strata, "ok": all(v["ok"] for v in strata.values())}
    conditions["cost"] = _cost(base, cand, clusters, replicates, seed)
    return conditions


def _safety(base: dict, cand: dict, clusters: list[str], replicates: int, seed: int) -> dict:
    """Rule 2 in every tier both hold; a missing or empty baseline rate is a rate of zero."""
    safety = {}
    b_tiers = set(base.get("per_cluster") or {}) & set(TIERS)
    for t in sorted(b_tiers & set(cand.get("per_cluster") or {})):
        for m in metrics.SAFETY_METRICS:
            b = base["per_cluster"][t].get(m)
            c = cand["per_cluster"][t].get(m)
            if not c or not any(cell[0] for cell in c.values()):
                safety[f"{t}.{m}"] = {"increase_upper": None, "ok": True,
                                      "why": "the candidate makes no such error"}
                continue
            if not b or not any(cell[1] for cell in b.values()):
                # the baseline never risked the error: the increase is the candidate's own rate
                upper = metrics.bootstrap(c, clusters, replicates=replicates,
                                          seed=f"{seed}:safety:{t}:{m}")["hi"]
                why = "the baseline has no such rate; the candidate's own is the increase"
            else:
                upper = metrics.quantile(metrics.paired(b, c, clusters, replicates=replicates,
                                                        seed=f"{seed}:safety:{t}:{m}"), 0.95)
                why = ""
            safety[f"{t}.{m}"] = {"increase_upper": upper,
                                  "ok": upper is not None and upper <= SAFETY_MAX_INCREASE,
                                  **({"why": why} if why else {})}
    return {"metrics": safety, "ok": all(v["ok"] for v in safety.values())}


def cost_cells(base: dict, cand: dict) -> str:
    """Which cost the rule judges: locate and challenge spend when the inventories are shared."""
    shared = (base.get("runs") or {}).get("inventory") == (cand.get("runs") or {}).get("inventory")
    both = all("usd_per_article_locate" in ((d.get("per_cluster") or {}).get("cost") or {})
               for d in (base, cand))
    return "usd_per_article_locate" if shared and both else "usd_per_article"


def _cost(base: dict, cand: dict, clusters: list[str], replicates: int, seed: int) -> dict:
    cells = cost_cells(base, cand)
    b = (base.get("per_cluster") or {}).get("cost", {}).get(cells)
    c = (cand.get("per_cluster") or {}).get("cost", {}).get(cells)
    if not b or not c:
        return {"ok": False, "why": "a score has no cost per article", "cells": cells}
    tables = [sorted((d.get("pins") or {}).get("price_table_sha256") or []) for d in (base, cand)]
    if tables[0] != tables[1]:
        return {"ok": False, "why": "the two runs were priced from different price tables",
                "cells": cells}
    basis = [base.get("cost", {}).get("basis"), cand.get("cost", {}).get("basis")]
    if basis[0] != basis[1]:
        # per-article costs price every stage, the receipt spread splits them by stage:
        # one against the other reads a stage's spend as a rise or hides one
        return {"ok": False, "why": "the two scores price articles on different bases",
                "cells": cells, "basis": basis}
    base_value = sum(x[0] for x in b.values()) / max(1.0, sum(x[1] for x in b.values()))
    draws = metrics.paired(b, c, clusters, replicates=replicates, seed=f"{seed}:cost")
    upper = metrics.quantile(draws, 0.95)
    if base_value <= 0:
        # 20% of nothing is nothing: any rise over a zero-cost baseline exceeds it
        return {"baseline_usd_per_article": base_value, "increase_upper": upper,
                "relative_upper": None, "cells": cells, "basis": basis,
                "why": "the baseline cost nothing: any increase exceeds 20% of it",
                "ok": upper is not None and upper <= 0}
    relative = None if upper is None else upper / base_value
    return {"baseline_usd_per_article": base_value, "relative_upper": relative, "cells": cells,
            "basis": basis, "ok": relative is not None and relative <= COST_MAX_INCREASE}


def accepted(conditions: dict) -> bool:
    keys = ("claim", "safety", "strata", "cost")
    return all(conditions[k]["ok"] for k in keys) and conditions.get("claim_unseen", {"ok": True})["ok"]


def claimable(cand: dict) -> tuple[bool, str]:
    coverage = cand.get("coverage") or {}
    articles = int(coverage.get("articles") or 0)
    positives = int(coverage.get("total_positives") or 0)
    if not (cand.get("canary_check") or {}).get("ran"):
        return False, "the candidate was scored without a canary check: recorded, not claimed"
    if cand.get("split") == "eval" and (articles >= CLAIM_MIN_ARTICLES or positives >= CLAIM_MIN_POSITIVES):
        return True, f"{articles} eval articles, {positives} gold positives"
    return False, (f"{articles} {cand.get('split')} articles and {positives} gold positives: below "
                   f"{CLAIM_MIN_ARTICLES} eval articles or {CLAIM_MIN_POSITIVES} positives a "
                   f"decision is recorded, not claimed")


@contextlib.contextmanager
def ledger_lock(path: Path) -> Iterator[None]:
    """An exclusive lock on ``<ledger>.lock``, held from reading the ledger to appending to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def replicated_entry(other: BenchLayout, entry_id: str) -> dict:
    """The ledger entry of another bench a ``--replication`` replicates (it must exist)."""
    rows = read_jsonl_rows(other.ledger_jsonl) if other.ledger_jsonl.is_file() else []
    found = [r for r in rows if r.get("entry_id") == entry_id]
    if not found:
        raise CfError(f"bench {other.bench_id}'s ledger has no entry {entry_id}",
                      hint="--replication <bench>:<entry id> names the claim this comparison "
                           "replicates on a fresh draw")
    return {**found[-1], "bench": other.bench_id}


def _check_replication(layout: BenchLayout, original: dict, claim: str, history: list[dict]) -> dict:
    """A replication on a fresh draw replicates an accepted claim of ANOTHER bench, once."""
    if original.get("bench") == layout.bench_id:
        raise CfError("a replication is on a fresh draw, which is another bench",
                      hint="a comparison within this bench spends alpha")
    if original.get("decision") != "accept" or not original.get("claimed"):
        raise CfError(f"entry {original.get('entry_id')} of bench {original.get('bench')} is no "
                      "accepted claim, so there is nothing to replicate")
    if original.get("claim") != claim:
        raise CfError(f"entry {original.get('entry_id')} claimed {original.get('claim')}, not {claim}")
    pin = {"bench": original["bench"], "entry_id": original["entry_id"]}
    if any(e.get("replicates") == pin for e in history):
        raise CfError(f"this bench's ledger already replicates {pin['bench']}:{pin['entry_id']}",
                      hint="a claim is replicated once per fresh draw")
    return pin


def compare(layout: BenchLayout, base_path: Path, cand_path: Path, *, claim: str,
            aa_path: Path | None = None, alpha: float = DEFAULT_ALPHA,
            replication: dict | None = None, replicates: int = 2000, seed: int = 0,
            note: str = "") -> dict:
    """One comparison, appended to the bench's ledger; ``replication`` is the original entry."""
    if not (isinstance(alpha, (int, float)) and 0.0 < float(alpha) < 0.5):
        raise CfError(f"--alpha {alpha} is not a one-sided significance level",
                      hint="pass 0 < alpha < 0.5 (default 0.05)")
    base, cand = load_score(base_path), load_score(cand_path)
    refuse_subset(base, base_path, "baseline")
    refuse_subset(cand, cand_path, "candidate")
    if base.get("bench") != layout.bench_id:
        raise CfError(f"{base_path} is a score of bench {base.get('bench')!r}, not {layout.bench_id!r}")
    differences = refuse_incomparable(base, cand)
    replicate = None
    if aa_path is not None:
        replicate = load_score(aa_path)
        refuse_subset(replicate, aa_path, "A/A replicate")
        check_replicate(base, replicate)
    ledger_path = layout.ledger_jsonl
    with ledger_lock(ledger_path):
        history = read_jsonl_rows(ledger_path) if ledger_path.is_file() else []
        replicates_pin = (_check_replication(layout, replication, claim, history)
                          if replication is not None else None)
        spent = sum(1 for e in history if e.get("claimed") and not e.get("replication"))
        # a measurement_id names the runs, not the split they were scored on: the dev and
        # the eval score of one run are two baselines
        if any(e.get("baseline") == base["measurement_id"] and e.get("split") == base.get("split")
               for e in history):
            differences.append("baseline reused: an earlier entry compared this same baseline "
                               "measurement")
        alpha_j = float(alpha) if replicates_pin else float(alpha) * (0.5 ** (spent + 1))
        conditions = evaluate(base, cand, claim=claim, alpha_j=alpha_j, replicates=replicates,
                              seed=seed)
        if replicate is None:
            conditions["aa"] = {"ok": False, "why": "no A/A replicate: the rule needs one to reject"}
        else:
            aa = evaluate(base, replicate, claim=claim, alpha_j=alpha_j, replicates=replicates,
                          seed=seed + 1)
            conditions["aa"] = {"accepted": accepted(aa), "ok": not accepted(aa),
                                "claim_improvement_lower": aa["claim"]["improvement_lower"],
                                "replicate": replicate["measurement_id"],
                                "replicate_score": replicate.get("score_id")}
        decision = "accept" if accepted(conditions) and conditions["aa"]["ok"] else "reject"
        claimed, why = claimable(cand)
        entry = {
            "schema": LEDGER_SCHEMA, "bench": layout.bench_id, "at": now(), "claim": claim,
            "split": cand.get("split"), "arm": cand.get("arm"),
            "baseline": base["measurement_id"], "candidate": cand["measurement_id"],
            # which score document of each measurement: one run scored two ways is two
            "baseline_score": base.get("score_id"), "candidate_score": cand.get("score_id"),
            "gold_manifest_sha256": cand["gold_manifest"]["sha256"],
            "alpha": alpha, "alpha_j": alpha_j, "replication": bool(replicates_pin),
            "replicates": replicates_pin, "bootstrap": replicates,
            "conditions": conditions, "decision": decision, "claimed": claimed, "claim_basis": why,
            "differences": differences, "note": note,
            "failed": sorted(k for k, v in conditions.items() if isinstance(v, dict) and not v.get("ok")),
        }
        entry["entry_id"] = "loop-" + sha_of({k: v for k, v in entry.items() if k != "at"})[:12]
        write_canonical_jsonl(Path(ledger_path), [*history, entry])
    return entry
