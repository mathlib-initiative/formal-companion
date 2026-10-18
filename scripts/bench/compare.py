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
   stratum too (section 8.3);
2. the safety metrics (false-not-formalized and overclaim rates, in every tier
   both documents hold) do not regress: the upper bound of their increase is at
   most 0.01;
3. no stratum with n >= 30 drops by more than three points on its interval;
4. the cost per article rises by at most 20% on its paired interval -- the
   locate and challenge spend alone when the two runs share their inventory
   runs (a shared inventory's cost would dilute the rise), every stage otherwise;
5. the A/A comparison (the baseline against its own replicate) does NOT accept;
6. multiplicity: the alpha of the j-th claimed comparison in the ledger is
   ``alpha * 2**-j`` (alpha-spending), unless the comparison is a replication on
   a fresh draw, which spends nothing.

Only G1-blind, G3 and P metrics may be pre-registered (section 8.1).  Below
about 150 eval articles or 400 gold positives a decision is RECORDED, not
claimed (section 9, step 3).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cflib import vocab
from cflib.errors import CfError
from cflib.jsonio import read_json

from . import metrics
from .layout import LEDGER_SCHEMA, BenchLayout, now, read_jsonl_rows, sha_of, write_canonical_jsonl

#: below both of these a decision is recorded, not claimed
CLAIM_MIN_ARTICLES = 150
CLAIM_MIN_POSITIVES = 400
SAFETY_MAX_INCREASE = 0.01
STRATUM_MIN_N = 30
STRATUM_MAX_DROP = 0.03
COST_MAX_INCREASE = 0.20
DEFAULT_ALPHA = 0.05
#: the tiers a pre-registered metric may come from (G1 only in the blind arm)
CLAIM_TIERS = ("G1", "G3", "P")


def load_score(path: Path) -> dict:
    doc = read_json(path)
    if not isinstance(doc, dict) or doc.get("schema") != "cf-bench-score/1":
        raise CfError(f"{path} is not a cf-bench-score/1 document")
    return doc


def _challenge_state(doc: dict) -> tuple[bool, bool]:
    """``(scored after a challenge run, rows still owed a verdict)`` of one score."""
    owed = ((doc.get("coverage") or {}).get("challenge_owed") or {}).get("total") or 0
    return bool((doc.get("pins") or {}).get("challenge_runs")), int(owed) > 0


def _values(pin) -> list[str]:
    """A per-run pin (``{run: value}``) as its sorted values; any other pin as itself."""
    if isinstance(pin, dict):
        return sorted({str(v) for v in pin.values()})
    return pin


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
    b_tools = ((base["pins"].get("tools_corpus_db") or {}).get("kind")) or "full"
    c_tools = ((cand["pins"].get("tools_corpus_db") or {}).get("kind")) or "full"
    if b_tools != c_tools:
        raise CfError(f"the workers searched a {b_tools} and a {c_tools} database",
                      hint="a planted (P) or blind tools database is its own measurement")
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
    notes = []
    for pin in ("universe_sha256", "corpus_db_sha256", "sourceset_manifest_sha256", "agent_cli",
                "tend", "agentbox", "skill_commit", "gate_files", "prompts"):
        if _values(base["pins"].get(pin)) != _values(cand["pins"].get(pin)):
            notes.append(f"{pin} differs")
    if b_chal[1] and c_chal[1]:
        notes.append("both scores owe challenge verdicts")
    return notes


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
    lower = metrics.quantile(draws, alpha_j)
    conditions["claim"] = {"metric": claim, "alpha": alpha_j, "improvement_lower": lower,
                           "ok": lower is not None and lower > 0}
    unseen = _stratum_draws(base, cand, tier, "seen:no", metric, replicates, seed)
    if unseen is not None:
        low = metrics.quantile(_improvement(unseen, metric), alpha_j)
        conditions["claim_unseen"] = {"improvement_lower": low, "ok": low is not None and low > 0}
    safety = {}
    for t in sorted(set(base.get("per_cluster") or {}) & set(cand.get("per_cluster") or {})):
        for m in metrics.SAFETY_METRICS:
            if m in base["per_cluster"][t] and m in cand["per_cluster"][t]:
                d = metrics.paired(base["per_cluster"][t][m], cand["per_cluster"][t][m], clusters,
                                   replicates=replicates, seed=f"{seed}:safety:{t}:{m}")
                upper = metrics.quantile(d, 0.95)
                safety[f"{t}.{m}"] = {"increase_upper": upper,
                                      "ok": upper is None or upper <= SAFETY_MAX_INCREASE}
    conditions["safety"] = {"metrics": safety, "ok": all(v["ok"] for v in safety.values())}
    strata = {}
    for key in sorted(set((base.get("strata") or {}).get(tier, {}))
                      & set((cand.get("strata") or {}).get(tier, {}))):
        b = base["strata"][tier][key].get(metric)
        c = cand["strata"][tier][key].get(metric)
        if not b or not c or min(b.get("den") or 0, c.get("den") or 0) < STRATUM_MIN_N:
            continue
        d = _improvement(metrics.paired(b["by_cluster"], c["by_cluster"], clusters,
                                        replicates=replicates, seed=f"{seed}:stratum:{key}"), metric)
        low = metrics.quantile(d, 0.025)
        strata[key] = {"n": min(b["den"], c["den"]), "improvement_lower": low,
                       "ok": low is None or low >= -STRATUM_MAX_DROP}
    conditions["strata"] = {"checked": strata, "ok": all(v["ok"] for v in strata.values())}
    conditions["cost"] = _cost(base, cand, clusters, replicates, seed)
    return conditions


def _stratum_draws(base, cand, tier, key, metric, replicates, seed):
    try:
        b = base["strata"][tier][key][metric]["by_cluster"]
        c = cand["strata"][tier][key][metric]["by_cluster"]
    except KeyError:
        return None
    return metrics.paired(b, c, _clusters(base, cand), replicates=replicates,
                          seed=f"{seed}:unseen")


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
        return {"ok": False, "why": "a score has no cost per article"}
    base_value = sum(x[0] for x in b.values()) / max(1.0, sum(x[1] for x in b.values()))
    if base_value <= 0:
        return {"ok": True, "why": "the baseline cost nothing", "relative_upper": None}
    draws = metrics.paired(b, c, clusters, replicates=replicates, seed=f"{seed}:cost")
    upper = metrics.quantile(draws, 0.95)
    relative = None if upper is None else upper / base_value
    return {"baseline_usd_per_article": base_value, "relative_upper": relative, "cells": cells,
            "basis": [base.get("cost", {}).get("basis"), cand.get("cost", {}).get("basis")],
            "ok": relative is None or relative <= COST_MAX_INCREASE}


def accepted(conditions: dict) -> bool:
    keys = ("claim", "safety", "strata", "cost")
    return all(conditions[k]["ok"] for k in keys) and conditions.get("claim_unseen", {"ok": True})["ok"]


def claimable(cand: dict) -> tuple[bool, str]:
    coverage = cand.get("coverage") or {}
    articles = int(coverage.get("articles") or 0)
    positives = int(coverage.get("total_positives") or 0)
    if cand.get("split") == "eval" and (articles >= CLAIM_MIN_ARTICLES or positives >= CLAIM_MIN_POSITIVES):
        return True, f"{articles} eval articles, {positives} gold positives"
    return False, (f"{articles} {cand.get('split')} articles and {positives} gold positives: below "
                   f"{CLAIM_MIN_ARTICLES} eval articles or {CLAIM_MIN_POSITIVES} positives a "
                   f"decision is recorded, not claimed")


def compare(layout: BenchLayout, base_path: Path, cand_path: Path, *, claim: str,
            aa_path: Path | None = None, ledger: Path | None = None, alpha: float = DEFAULT_ALPHA,
            replication: bool = False, replicates: int = 2000, seed: int = 0,
            note: str = "") -> dict:
    base, cand = load_score(base_path), load_score(cand_path)
    if base.get("bench") != layout.bench_id:
        raise CfError(f"{base_path} is a score of bench {base.get('bench')!r}, not {layout.bench_id!r}")
    differences = refuse_incomparable(base, cand)
    ledger_path = ledger or layout.ledger_jsonl
    history = read_jsonl_rows(ledger_path) if Path(ledger_path).is_file() else []
    spent = sum(1 for e in history if e.get("claimed") and not e.get("replication"))
    if any(e.get("baseline") == base["measurement_id"] for e in history):
        differences.append("baseline reused: every pin matches an earlier entry's baseline")
    alpha_j = alpha if replication else alpha * (0.5 ** (spent + 1))
    conditions = evaluate(base, cand, claim=claim, alpha_j=alpha_j, replicates=replicates, seed=seed)
    if aa_path is None:
        conditions["aa"] = {"ok": False, "why": "no A/A replicate: the rule needs one to reject"}
    else:
        replicate = load_score(aa_path)
        refuse_incomparable(base, replicate)
        aa = evaluate(base, replicate, claim=claim, alpha_j=alpha_j, replicates=replicates,
                      seed=seed + 1)
        conditions["aa"] = {"accepted": accepted(aa), "ok": not accepted(aa),
                            "claim_improvement_lower": aa["claim"]["improvement_lower"],
                            "replicate": replicate["measurement_id"]}
    decision = "accept" if accepted(conditions) and conditions["aa"]["ok"] else "reject"
    claimed, why = claimable(cand)
    entry = {
        "schema": LEDGER_SCHEMA, "bench": layout.bench_id, "at": now(), "claim": claim,
        "split": cand.get("split"), "arm": cand.get("arm"),
        "baseline": base["measurement_id"], "candidate": cand["measurement_id"],
        "gold_manifest_sha256": cand["gold_manifest"]["sha256"],
        "alpha": alpha, "alpha_j": alpha_j, "replication": bool(replication),
        "conditions": conditions, "decision": decision, "claimed": claimed, "claim_basis": why,
        "differences": differences, "note": note,
        "failed": sorted(k for k, v in conditions.items() if isinstance(v, dict) and not v.get("ok")),
    }
    entry["entry_id"] = "loop-" + sha_of({k: v for k, v in entry.items() if k != "at"})[:12]
    write_canonical_jsonl(Path(ledger_path), [*history, entry])
    return entry
