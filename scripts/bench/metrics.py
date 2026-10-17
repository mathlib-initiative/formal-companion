"""The span-overlap join, every section 8.2 metric, and the cluster bootstrap.

New code, implementing docs/proposals/wikipedia-locate.md section 8.2 and the
join rule of its last paragraph, in the definitions references/locate-bench.md
section 8 states.  Three rules shape everything here:

* **Runs join gold by span overlap on the layer, never by id**: a gold passage
  and a run passage of one source join when they share at least four whole
  layer words, one to one, greedily by overlap (ties broken by id, so the join
  is a function of the data).  An item id is the run's own key and a gold span
  is not a passage of any run, so an id join would be no join at all.
* **Never pooled**: every metric is computed per tier (and G1 per arm, which a
  score document already is), and every ratio is kept as a numerator and a
  denominator per article cluster, so an interval is a cluster bootstrap and a
  comparison can pair two runs cluster by cluster.
* **A miss is a miss**: a gold-formalized passage whose article's inventory was
  unresolved or never landed is counted, with that cause -- unresolved and
  gate-failed tasks are misses, not exclusions.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from cflib import vocab

from .layout import overlap_words

#: the join's floor: whole layer words two spans must share
MIN_OVERLAP_WORDS = 4
#: the k of locate recall@k
RECALL_K = (1, 3, 5)
#: the cause of a false-not-formalized miss -> its mechanical error code
CAUSE_CODES = {"inventory_miss": "E-INVENTORY-MISS", "scope_demotion": "E-WRONG-SCOPE",
               "unresolved": "E-UNRESOLVED", "gate_failed": "E-UNRESOLVED"}
#: lower is better for these; higher is better for every other ratio metric
LOWER_IS_BETTER = frozenset({"fnf_rate", "overclaim_rate", "scope_error_rate", "unresolved_rate",
                             "article_fnf_rate", "memory_rate", "usd_per_article"})
#: the safety metrics the acceptance rule holds (section 9)
SAFETY_METRICS = ("fnf_rate", "overclaim_rate")
#: a gate log reason, classified by the first pattern it matches
REASON_CLASSES = (
    ("phantom_declaration", re.compile(r"phantom|not in (the )?corpus|does not exist|no such declaration|exact table", re.I)),
    ("outside_universe", re.compile(r"universe|unknown library|not a library", re.I)),
    ("quote", re.compile(r"quote|prefix|suffix|layer", re.I)),
    ("passage_id", re.compile(r"In-text|passage id|head_words|hash", re.I)),
    ("schema", re.compile(r"schema|missing key|not an object|must be", re.I)),
    ("ownership", re.compile(r"ownership|changed path|task id|stem", re.I)),
    ("absence_receipt", re.compile(r"queries|receipt", re.I)),
    ("curated", re.compile(r"curated|rule 4", re.I)),
    ("coverage", re.compile(r"coverage|skip|finder|paragraph", re.I)),
)
_RULE = re.compile(r"\brule\s*([1-8])\b", re.I)


# --------------------------------------------------------------------------
# inputs, normalised
# --------------------------------------------------------------------------

@dataclass
class RunPassage:
    source_id: str
    item: str
    span: tuple[int, int] | None
    scope: str | None
    passage_kind: str | None
    prominence: str | None
    outcome: str | None
    status: str | None
    locations: list[dict] = field(default_factory=list)
    receipt: dict | None = None

    def located(self, k: int | None = None) -> list[tuple[str, str]]:
        out = []
        for loc in self.locations:
            if loc.get("relation") not in vocab.LOCATE_RELATIONS:
                continue
            rank = loc.get("rank")
            if k is not None and isinstance(rank, int) and rank > k:
                continue
            out.append((str(loc.get("library")), str(loc.get("declaration"))))
        return out

    def rank1(self) -> tuple[str, str] | None:
        firsts = [loc for loc in self.locations if loc.get("rank") == 1
                  and loc.get("relation") in vocab.LOCATE_RELATIONS]
        return (str(firsts[0]["library"]), str(firsts[0]["declaration"])) if firsts else None

    def rejected(self) -> set[tuple[str, str]]:
        out = set()
        for cand in ((self.receipt or {}).get("candidates") or []):
            if cand.get("verdict") == vocab.CANDIDATE_REJECTED:
                out.add((str(cand.get("library")), str(cand.get("declaration"))))
        return out


@dataclass
class GoldPassage:
    gold_id: str
    tier: str
    source_id: str
    span: tuple[int, int] | None
    level: str
    scope: str | None
    status: str | None
    related: bool
    passage_kind: str | None
    accept: set[tuple[str, str]]
    relations: dict[tuple[str, str], str]
    weight: float = 1.0
    seen: bool | None = None


def gold_passages(tier: str, rows: Iterable[dict]) -> list[GoldPassage]:
    out = []
    for row in rows:
        decls = row.get("declarations") or []
        if tier == "G1":
            accept = {(a["library"], a["declaration"]) for a in row.get("accept") or []}
        else:
            accept = {(d["library"], d["declaration"]) for d in decls
                      if d.get("relation") in vocab.LOCATE_RELATIONS - {vocab.RELATED_MATCH}}
        relations = {(d["library"], d["declaration"]): d["relation"] for d in decls
                     if d.get("relation") in vocab.LOCATE_RELATIONS}
        weight = 1.0
        if tier == "G3" and row.get("pi_span") and row.get("pi_article"):
            weight = 1.0 / (float(row["pi_span"]) * float(row["pi_article"]))
        span = tuple(row["span"]) if row.get("span") else None
        out.append(GoldPassage(gold_id=row["gold_id"], tier=tier, source_id=row["source_id"],
                               span=span, level=row.get("level") or "passage",
                               scope=row.get("scope"), status=row.get("status"),
                               related=bool(row.get("related")),
                               passage_kind=row.get("passage_kind"), accept=accept,
                               relations=relations, weight=weight))
    return out


# --------------------------------------------------------------------------
# the join
# --------------------------------------------------------------------------

def join(gold: list[GoldPassage], run: list[RunPassage],
         layers: dict[str, str]) -> dict[str, RunPassage]:
    """``{gold id: run passage}``: one to one, at least four shared words, greedy by overlap."""
    pairs: list[tuple[int, str, str, int]] = []
    by_source: dict[str, list[tuple[int, RunPassage]]] = {}
    for index, passage in enumerate(run):
        if passage.span is not None:
            by_source.setdefault(passage.source_id, []).append((index, passage))
    for g in gold:
        if g.span is None:
            continue
        layer = layers.get(g.source_id, "")
        for index, passage in by_source.get(g.source_id, []):
            words = overlap_words(layer, g.span, passage.span)
            if words >= MIN_OVERLAP_WORDS:
                pairs.append((words, g.gold_id, passage.item, index))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    taken_gold: set[str] = set()
    taken_run: set[int] = set()
    out: dict[str, RunPassage] = {}
    for _words, gid, _item, index in pairs:
        if gid in taken_gold or index in taken_run:
            continue
        taken_gold.add(gid)
        taken_run.add(index)
        out[gid] = run[index]
    return out


# --------------------------------------------------------------------------
# per passage
# --------------------------------------------------------------------------

def fnf_cause(article_state: str, run: RunPassage | None) -> str | None:
    """Why a gold-formalized passage did not end ``formalized``; None when it did."""
    if article_state in ("unresolved", "gate_failed"):
        return article_state
    if run is None:
        return "inventory_miss"
    if run.scope not in (None, vocab.SCOPE_FORMAL_MATH):
        return "scope_demotion"
    if run.status == vocab.STATUS_FORMALIZED:
        return None
    if run.outcome is None:
        return "gate_failed"
    if run.outcome == vocab.OUTCOME_UNRESOLVED or run.status is None:
        return "unresolved"
    if run.outcome == vocab.OUTCOME_ABSENT or run.status == vocab.STATUS_NOT_FORMALIZED:
        return "absence"
    return "weak_relation"


def error_code(cause: str, gold: GoldPassage, run: RunPassage | None) -> str:
    """The section 9 mechanical code of one miss."""
    if cause in CAUSE_CODES:
        return CAUSE_CODES[cause]
    found = set(run.located()) if run else set()
    rejected = run.rejected() if run else set()
    if cause == "absence":
        return "E-REJECTED-CORRECT" if gold.accept & rejected else "E-FALSE-ABSENCE"
    if gold.accept & found:
        return "E-WRONG-RELATION"
    if gold.accept & rejected:
        return "E-REJECTED-CORRECT"
    return "E-RETRIEVAL-MISS"


# --------------------------------------------------------------------------
# per-cluster ratios
# --------------------------------------------------------------------------

class Ratio:
    """A ratio kept as ``{cluster: [numerator, denominator]}``."""

    def __init__(self):
        self.by_cluster: dict[str, list[float]] = {}

    def add(self, cluster: str, num: float, den: float = 1.0) -> None:
        cell = self.by_cluster.setdefault(cluster, [0.0, 0.0])
        cell[0] += num
        cell[1] += den

    @property
    def num(self) -> float:
        return sum(c[0] for c in self.by_cluster.values())

    @property
    def den(self) -> float:
        return sum(c[1] for c in self.by_cluster.values())

    def value(self) -> float | None:
        return self.num / self.den if self.den else None


def bootstrap(by_cluster: dict[str, list[float]], clusters: list[str], *, replicates: int,
              seed: str, level: float = 0.95) -> dict:
    """A percentile interval over clusters resampled with replacement."""
    cells = [by_cluster.get(c, [0.0, 0.0]) for c in clusters]
    num = sum(c[0] for c in cells)
    den = sum(c[1] for c in cells)
    out = {"value": (num / den) if den else None, "num": num, "den": den,
           "lo": None, "hi": None, "clusters": len(clusters)}
    if not den or not clusters or replicates <= 0:
        return out
    rng = random.Random(seed)
    draws = []
    n = len(cells)
    for _ in range(replicates):
        s_num = s_den = 0.0
        for _j in range(n):
            cell = cells[rng.randrange(n)]
            s_num += cell[0]
            s_den += cell[1]
        if s_den:
            draws.append(s_num / s_den)
    if draws:
        draws.sort()
        tail = (1.0 - level) / 2.0
        out["lo"] = _quantile(draws, tail)
        out["hi"] = _quantile(draws, 1.0 - tail)
        out["replicates"] = len(draws)
    return out


def _quantile(sorted_values: list[float], q: float) -> float:
    """The nearest-rank quantile (no interpolation: the same draws give the same bound)."""
    if not sorted_values:
        raise ValueError("no values")
    index = min(len(sorted_values) - 1, max(0, int(q * len(sorted_values))))
    return sorted_values[index]


def paired(a: dict[str, list[float]], b: dict[str, list[float]], clusters: list[str], *,
           replicates: int, seed: str) -> list[float]:
    """Paired bootstrap draws of ``ratio(b) - ratio(a)``, the same clusters drawn for both."""
    cells_a = [a.get(c, [0.0, 0.0]) for c in clusters]
    cells_b = [b.get(c, [0.0, 0.0]) for c in clusters]
    rng = random.Random(seed)
    n = len(clusters)
    out = []
    for _ in range(replicates if n else 0):
        na = da = nb = db = 0.0
        for _j in range(n):
            i = rng.randrange(n)
            na += cells_a[i][0]
            da += cells_a[i][1]
            nb += cells_b[i][0]
            db += cells_b[i][1]
        if da and db:
            out.append(nb / db - na / da)
    out.sort()
    return out


def quantile(values: list[float], q: float) -> float | None:
    return _quantile(values, q) if values else None


# --------------------------------------------------------------------------
# a tier, evaluated
# --------------------------------------------------------------------------

@dataclass
class TierResult:
    ratios: dict[str, Ratio]
    confusion: dict[str, dict[str, int]]
    misses: list[dict]
    joined: int
    gold: int
    strata: dict[str, dict[str, Ratio]]


def evaluate_tier(tier: str, gold: list[GoldPassage], run: list[RunPassage], *,
                  layers: dict[str, str], cluster_of: dict[str, str],
                  article_state: dict[str, str], assessment: dict[str, dict],
                  ablated: set[tuple[str, str]] | None = None) -> TierResult:
    """Every metric one tier supports, per cluster."""
    ratios: dict[str, Ratio] = {}

    def ratio(name: str) -> Ratio:
        return ratios.setdefault(name, Ratio())

    strata: dict[str, dict[str, Ratio]] = {}

    def stratum(key: str, name: str) -> Ratio:
        return strata.setdefault(key, {}).setdefault(name, Ratio())

    confusion: dict[str, dict[str, int]] = {}
    misses: list[dict] = []
    passage_gold = [g for g in gold if g.level == "passage" and g.span is not None]
    joined = join(passage_gold, run, layers)
    for g in sorted(passage_gold, key=lambda x: x.gold_id):
        cluster = cluster_of.get(g.source_id, "?")
        r = joined.get(g.gold_id)
        state = article_state.get(g.source_id, "ran")
        keys = _strata_keys(g, assessment.get(g.source_id) or {})
        if tier == "P":
            # the planted arm: the gold declarations are gone from the index, so
            # the only question is whether the run cited one anyway
            _memory(ratio, misses, tier, g, r, cluster, ablated or set())
            continue
        if g.status == vocab.STATUS_FORMALIZED:
            cause = fnf_cause(state, r)
            ratio("fnf_rate").add(cluster, 1.0 if cause else 0.0)
            for key in keys:
                stratum(key, "fnf_rate").add(cluster, 1.0 if cause else 0.0)
            if cause:
                ratio(f"fnf_cause:{cause}").add(cluster, 1.0, 0.0)
                misses.append({"gold_id": g.gold_id, "tier": tier, "source_id": g.source_id,
                               "span": list(g.span), "kind": "false_not_formalized",
                               "cause": cause, "code": error_code(cause, g, r),
                               "run_item": r.item if r else None,
                               "run_status": r.status if r else None})
        if r is not None and r.status == vocab.STATUS_FORMALIZED and g.status is not None:
            over = g.status != vocab.STATUS_FORMALIZED
            ratio("overclaim_rate").add(cluster, 1.0 if over else 0.0)
            if over:
                misses.append({"gold_id": g.gold_id, "tier": tier, "source_id": g.source_id,
                               "span": list(g.span), "kind": "overclaim",
                               "cause": f"gold {g.status}", "code": "E-WRONG-RELATION",
                               "run_item": r.item, "run_status": r.status})
        if r is not None and g.scope is not None:
            ratio("scope_error_rate").add(cluster, 1.0 if r.scope != g.scope else 0.0)
        if r is not None and r.scope == vocab.SCOPE_FORMAL_MATH:
            unresolved = r.outcome == vocab.OUTCOME_UNRESOLVED or (r.outcome is not None and r.status is None)
            ratio("unresolved_rate").add(cluster, 1.0 if unresolved else 0.0)
        if tier == "GX":
            ratio("inventory_recall").add(cluster, 1.0 if r is not None else 0.0)
        if tier == "G3" and g.scope == vocab.SCOPE_FORMAL_MATH:
            ratio("inventory_recall").add(cluster, (1.0 if r is not None else 0.0) * g.weight, g.weight)
        if g.accept:
            for k in RECALL_K:
                hit = r is not None and bool(set(r.located(k)) & g.accept)
                ratio(f"recall_at_{k}").add(cluster, 1.0 if hit else 0.0)
                if k == 1:
                    for key in keys:
                        stratum(key, "recall_at_1").add(cluster, 1.0 if hit else 0.0)
            if r is not None and r.rank1() is not None:
                ratio("precision_at_1").add(cluster, 1.0 if r.rank1() in g.accept else 0.0)
        if tier == "G3" and g.accept:
            for library in sorted({lib for lib, _ in g.accept}):
                hit = r is not None and any(lib == library for lib, _ in r.located())
                ratio("library_recall").add(cluster, 1.0 if hit else 0.0)
        if r is not None and g.relations:
            for loc in r.locations:
                key = (str(loc.get("library")), str(loc.get("declaration")))
                if key in g.relations and loc.get("relation") in vocab.LOCATE_RELATIONS:
                    row = confusion.setdefault(g.relations[key], {})
                    row[loc["relation"]] = row.get(loc["relation"], 0) + 1
    # G1 entries no passage could be anchored for: scored at article level only
    if tier == "G1":
        by_article: dict[str, list[RunPassage]] = {}
        for passage in run:
            by_article.setdefault(passage.source_id, []).append(passage)
        for g in sorted((x for x in gold if x.level == "article"), key=lambda x: x.gold_id):
            cluster = cluster_of.get(g.source_id, "?")
            hit = any(p.status == vocab.STATUS_FORMALIZED and set(p.located()) & g.accept
                      for p in by_article.get(g.source_id, []))
            ratio("article_fnf_rate").add(cluster, 0.0 if hit else 1.0)
            if not hit:
                misses.append({"gold_id": g.gold_id, "tier": tier, "source_id": g.source_id,
                               "span": None, "kind": "false_not_formalized", "cause": "article_level",
                               "code": "E-RETRIEVAL-MISS", "run_item": None, "run_status": None})
    return TierResult(ratios=ratios, confusion=confusion, misses=misses, joined=len(joined),
                      gold=len(gold), strata=strata)


def _memory(ratio, misses: list[dict], tier: str, g: GoldPassage, r: RunPassage | None,
            cluster: str, ablated: set[tuple[str, str]]) -> None:
    """P: a location at a declaration the planted index removed is ``E-MEMORY``."""
    memory = [loc for loc in (r.locations if r else [])
              if (str(loc.get("library")), str(loc.get("declaration"))) in ablated]
    ratio("memory_rate").add(cluster, 1.0 if memory else 0.0)
    for loc in memory:
        misses.append({"gold_id": g.gold_id, "tier": tier, "source_id": g.source_id,
                       "span": list(g.span) if g.span else None, "kind": "memory",
                       "cause": "ablated declaration", "code": "E-MEMORY", "run_item": r.item,
                       "declaration": f"{loc.get('library')}:{loc.get('declaration')}"})


def _strata_keys(g: GoldPassage, assessment: dict) -> list[str]:
    keys = []
    if assessment.get("importance"):
        keys.append(f"importance:{assessment['importance']}")
    if assessment.get("class"):
        keys.append(f"class:{assessment['class']}")
    if g.passage_kind:
        keys.append(f"passage_kind:{g.passage_kind}")
    if g.seen is not None:
        keys.append("seen:" + ("yes" if g.seen else "no"))
    return keys


def classify_reason(reason: str) -> str:
    """One gate log reason's class: a rule number when it names one, else the first pattern."""
    for name, pattern in REASON_CLASSES:
        if pattern.search(reason or ""):
            return name
    found = _RULE.search(reason or "")
    return f"rule_{found.group(1)}" if found else "other"


def gate_rejections(lines: list[dict], mode: str | None = None) -> dict:
    """Pre-gate rejection rates by reason over every attempt the gate log records."""
    attempts = [l for l in lines if mode is None or l.get("mode") in (None, mode)]
    rejected = [l for l in attempts if not l.get("ok")]
    by_reason: dict[str, int] = {}
    for line in rejected:
        classes = {classify_reason(str(r)) for r in (line.get("reasons") or [])} or {"other"}
        for name in classes:
            by_reason[name] = by_reason.get(name, 0) + 1
    total = len(attempts)
    return {"attempts": total, "rejected": len(rejected),
            "rejection_rate": (len(rejected) / total) if total else None,
            "by_reason": {k: {"attempts": v, "rate": v / total if total else None}
                          for k, v in sorted(by_reason.items())}}


def summarise(ratios: dict[str, Ratio], clusters: list[str], *, replicates: int,
              seed: str) -> dict[str, Any]:
    """Each ratio's value and cluster-bootstrap interval; cause counts as plain counts."""
    out: dict[str, Any] = {}
    causes: dict[str, int] = {}
    for name in sorted(ratios):
        if name.startswith("fnf_cause:"):
            causes[name.split(":", 1)[1]] = int(ratios[name].num)
            continue
        out[name] = bootstrap(ratios[name].by_cluster, clusters, replicates=replicates,
                              seed=f"{seed}:{name}")
    if "fnf_rate" in out:
        out["fnf_rate"]["causes"] = {c: causes.get(c, 0) for c in sorted(vocab.FNF_CAUSES)}
    return out
