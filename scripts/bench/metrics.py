"""The span-overlap join, every section 8.2 metric, and the cluster bootstrap.

New code, implementing docs/proposals/wikipedia-locate.md section 8.2 and the
join rule of its last paragraph, in the definitions references/locate-bench.md
section 8 states.  Three rules shape everything here:

* **Runs join gold by span overlap on the layer, never by id**: a gold passage
  and a run passage of one source join when they share at least four whole
  layer words, one to one across distinct passages, greedily by overlap (ties
  broken by id, so the join is a function of the data).  Gold rows that are one
  passage of the text (their spans share four words) may all join the run
  passage that holds them.  An item id is the run's own key and a gold span is
  not a passage of any run, so an id join would be no join at all.
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

from .layout import MIN_OVERLAP_WORDS, overlap_words

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
#: a gate log line's reason is ``[rule N] <document>: <where>: <message>``
#: (scripts/gate/verify_locate.py ``Problems``); the rule is the class, and these
#: patterns, run over the MESSAGE alone, split a rule into sub-classes
REASON_SUBCLASSES = (
    ("phantom_declaration", re.compile(r"is not in corpus\.sqlite|does not exist|no such declaration", re.I)),
    ("outside_universe", re.compile(r"not in (?:the )?universe|outside the universe|not a library", re.I)),
    ("universe_pin", re.compile(r"^universe ", re.I)),
    ("proof_status_written", re.compile(r"not a worker's to write|proof_status", re.I)),
    ("curated", re.compile(r"curated", re.I)),
    ("absence_receipt", re.compile(r"distinct quer|\breceipt\b", re.I)),
    ("ranks", re.compile(r"\branks?\b|per passage", re.I)),
    ("note", re.compile(r"\bnote\b", re.I)),
    ("pin", re.compile(r"source_sha|layer_sha256|renderer|pinned", re.I)),
    ("quote", re.compile(r"\bquote\b|\bprefix\b|\bsuffix\b|matches the layer", re.I)),
    ("passage_id", re.compile(r"head_words|passage id|\bhash|cannot be keyed|cannot start"
                              r"|not one of this task's passages|is given twice", re.I)),
    ("coverage", re.compile(r"coverage|\bskip|finder|paragraph|have no outcome", re.I)),
    ("ownership", re.compile(r"ownership|changed path|belongs to task|not a task of", re.I)),
    ("schema", re.compile(r"schema|missing key|not an object|must be|is not one of", re.I)),
)
_RULE = re.compile(r"^\s*\[rule\s+([0-9A-Za-z_-]+)\]\s*")
_DOCUMENT = re.compile(r"^\S+\.json:\s*")
_WHERE_PART = re.compile(r"^(?:(?:passages|skips|verdicts|locations|candidates|receipt)"
                         r"(?:\[\d+\])?|In-text\s*#\s*[0-9A-Fa-f]+|\$[\w.\[\]]*)"
                         r"(?:\.[A-Za-z_]+(?:\[\d+\])?)*\s*:\s*")


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
    """``{gold id: run passage}``: at least four shared words, greedy by overlap.

    One to one across distinct passages: a run passage already holding a gold
    passage joins another only when the new gold span shares at least four words
    with every gold span it holds -- two G1 entries anchored to one theorem are
    one passage of the text, and a run passage that located both is no inventory
    miss for either -- so two disjoint gold passages never share one large run
    passage.
    """
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
    by_id = {g.gold_id: g for g in gold}
    held: dict[int, list[tuple[int, int]]] = {}
    out: dict[str, RunPassage] = {}
    for _words, gid, _item, index in pairs:
        if gid in out:
            continue
        g = by_id[gid]
        layer = layers.get(g.source_id, "")
        if any(overlap_words(layer, g.span, span) < MIN_OVERLAP_WORDS
               for span in held.get(index, [])):
            continue
        held.setdefault(index, []).append(g.span)
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
    """Every metric one tier supports, per cluster, and per stratum for every metric."""
    ratios: dict[str, Ratio] = {}
    strata: dict[str, dict[str, Ratio]] = {}

    def count(name: str, cluster: str, keys: list[str], num: float, den: float = 1.0) -> None:
        ratios.setdefault(name, Ratio()).add(cluster, num, den)
        for key in keys:
            strata.setdefault(key, {}).setdefault(name, Ratio()).add(cluster, num, den)

    confusion: dict[str, dict[str, int]] = {}
    misses: list[dict] = []
    passage_gold = [g for g in gold if g.level == "passage" and g.span is not None]
    joined = join(passage_gold, run, layers)
    by_article: dict[str, list[RunPassage]] = {}
    for passage in run:
        by_article.setdefault(passage.source_id, []).append(passage)
    if tier == "P":
        _memory(count, misses, gold, joined, by_article, cluster_of, assessment, ablated or set())
        return TierResult(ratios=ratios, confusion=confusion, misses=misses, joined=len(joined),
                          gold=len(gold), strata=strata)
    for g in sorted(passage_gold, key=lambda x: x.gold_id):
        cluster = cluster_of.get(g.source_id, "?")
        r = joined.get(g.gold_id)
        state = article_state.get(g.source_id, "ran")
        keys = _strata_keys(g, assessment.get(g.source_id) or {})
        if g.status == vocab.STATUS_FORMALIZED:
            cause = fnf_cause(state, r)
            count("fnf_rate", cluster, keys, 1.0 if cause else 0.0)
            if cause:
                ratios.setdefault(f"fnf_cause:{cause}", Ratio()).add(cluster, 1.0, 0.0)
                misses.append({"gold_id": g.gold_id, "tier": tier, "source_id": g.source_id,
                               "span": list(g.span), "kind": "false_not_formalized",
                               "cause": cause, "code": error_code(cause, g, r),
                               "run_item": r.item if r else None,
                               "run_status": r.status if r else None})
        if r is not None and r.status == vocab.STATUS_FORMALIZED and g.status is not None:
            over = g.status != vocab.STATUS_FORMALIZED
            count("overclaim_rate", cluster, keys, 1.0 if over else 0.0)
            if over:
                misses.append({"gold_id": g.gold_id, "tier": tier, "source_id": g.source_id,
                               "span": list(g.span), "kind": "overclaim",
                               "cause": f"gold {g.status}", "code": "E-WRONG-RELATION",
                               "run_item": r.item, "run_status": r.status})
        if r is not None and g.scope is not None:
            count("scope_error_rate", cluster, keys, 1.0 if r.scope != g.scope else 0.0)
        if r is not None and r.scope == vocab.SCOPE_FORMAL_MATH:
            # no locate result (a gate failure, ``missing``) leaves a passage as unresolved
            # as an unresolved outcome does: neither is ever counted resolved
            unresolved = (r.outcome is None or r.outcome == vocab.OUTCOME_UNRESOLVED
                          or r.status is None)
            count("unresolved_rate", cluster, keys, 1.0 if unresolved else 0.0)
        elif r is None and state in ("unresolved", "gate_failed") \
                and g.scope == vocab.SCOPE_FORMAL_MATH:
            # an unresolved or never-landed inventory is a miss, not an exclusion
            count("unresolved_rate", cluster, keys, 1.0)
        if tier == "GX":
            count("inventory_recall", cluster, keys, 1.0 if r is not None else 0.0)
        if tier == "G3" and g.scope == vocab.SCOPE_FORMAL_MATH:
            count("inventory_recall", cluster, keys, (1.0 if r is not None else 0.0) * g.weight,
                  g.weight)
        if g.accept:
            for k in RECALL_K:
                hit = r is not None and bool(set(r.located(k)) & g.accept)
                count(f"recall_at_{k}", cluster, keys, 1.0 if hit else 0.0)
            if r is not None and r.rank1() is not None:
                count("precision_at_1", cluster, keys, 1.0 if r.rank1() in g.accept else 0.0)
        if tier == "G3" and g.accept:
            for library in sorted({lib for lib, _ in g.accept}):
                hit = r is not None and any(lib == library for lib, _ in r.located())
                count("library_recall", cluster, keys, 1.0 if hit else 0.0)
        if r is not None and g.relations:
            for loc in r.locations:
                key = (str(loc.get("library")), str(loc.get("declaration")))
                if key in g.relations and loc.get("relation") in vocab.LOCATE_RELATIONS:
                    row = confusion.setdefault(g.relations[key], {})
                    row[loc["relation"]] = row.get(loc["relation"], 0) + 1
    # G1 entries no passage could be anchored for: scored at article level only, and
    # like a passage, a false-not-formalized only when gold says formalized
    if tier == "G1":
        for g in sorted((x for x in gold if x.level == "article"
                         and x.status == vocab.STATUS_FORMALIZED), key=lambda x: x.gold_id):
            cluster = cluster_of.get(g.source_id, "?")
            keys = _strata_keys(g, assessment.get(g.source_id) or {})
            passages = by_article.get(g.source_id, [])
            hit = any(p.status == vocab.STATUS_FORMALIZED and set(p.located()) & g.accept
                      for p in passages)
            count("article_fnf_rate", cluster, keys, 0.0 if hit else 1.0)
            if not hit:
                found = any(set(p.located()) & g.accept for p in passages)
                misses.append({"gold_id": g.gold_id, "tier": tier, "source_id": g.source_id,
                               "span": None, "kind": "false_not_formalized", "cause": "article_level",
                               "code": "E-WRONG-RELATION" if found else "E-RETRIEVAL-MISS",
                               "run_item": None, "run_status": None})
    return TierResult(ratios=ratios, confusion=confusion, misses=misses, joined=len(joined),
                      gold=len(gold), strata=strata)


def _memory(count, misses: list[dict], gold: list[GoldPassage], joined: dict[str, RunPassage],
            by_article: dict[str, list[RunPassage]], cluster_of: dict[str, str],
            assessment: dict[str, dict], ablated: set[tuple[str, str]]) -> None:
    """P: every location at a declaration the planted index removed is ``E-MEMORY``.

    The question is whether the run cited a removed declaration at all -- the
    planted index never showed it one -- so a citation counts wherever in the
    article it sits, not only on the passage joined to a gold row.  A P gold
    entry (passage or article level) is a memory hit when its joined passage
    cites any removed declaration, or any passage of its article cites one of
    its own accept set; every citation of a removed declaration is one miss,
    attributed to the gold row joined to its passage, else the article's first.
    """
    def cited(p: RunPassage) -> list[tuple[str, str]]:
        return [(str(loc.get("library")), str(loc.get("declaration"))) for loc in p.locations
                if (str(loc.get("library")), str(loc.get("declaration"))) in ablated]

    first_gold: dict[str, str] = {}
    for g in sorted(gold, key=lambda x: x.gold_id):
        first_gold.setdefault(g.source_id, g.gold_id)
        r = joined.get(g.gold_id)
        hit = bool(r is not None and cited(r)) or any(
            key in g.accept for p in by_article.get(g.source_id, []) for key in cited(p))
        count("memory_rate", cluster_of.get(g.source_id, "?"),
              _strata_keys(g, assessment.get(g.source_id) or {}), 1.0 if hit else 0.0)
    holder = {id(r): gid for gid, r in sorted(joined.items(), reverse=True)}
    for sid in sorted(by_article):
        for p in sorted(by_article[sid], key=lambda x: x.item):
            for lib, name in cited(p):
                misses.append({"gold_id": holder.get(id(p)) or first_gold.get(sid),
                               "tier": "P", "source_id": sid,
                               "span": list(p.span) if p.span else None, "kind": "memory",
                               "cause": "ablated declaration", "code": "E-MEMORY",
                               "run_item": p.item, "declaration": f"{lib}:{name}"})


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


def split_reason(reason: str) -> tuple[str | None, str]:
    """``(rule, message)`` of one gate reason: the ``[rule N]`` prefix and the text after the where.

    The where -- the document name, then ``passages[i]``, an ``In-text #<hex>``
    item, a ``locations[j]`` path -- names the passage, not the failure, so it is
    stripped before any pattern runs: an item id must not make every passage-level
    reason read as a passage-id failure.
    """
    text = str(reason or "")
    found = _RULE.match(text)
    rule = found.group(1) if found else None
    rest = text[found.end():] if found else text
    rest = _DOCUMENT.sub("", rest, count=1)
    while True:
        stripped = _WHERE_PART.sub("", rest, count=1)
        if stripped == rest:
            return rule, rest
        rest = stripped


def classify_reason(reason: str) -> str:
    """``rule_<N>`` (``:<sub-class>`` when the message says which), or ``unruled[:<sub>]``."""
    rule, message = split_reason(reason)
    sub = next((name for name, pattern in REASON_SUBCLASSES if pattern.search(message)), None)
    head = f"rule_{rule}" if rule else "unruled"
    return f"{head}:{sub}" if sub else head


#: the gate's exit status when it could not run (not a verdict on the worker)
EXIT_GATE_BROKEN = 3


def gate_rejections(lines: list[dict], run_ids: Iterable[str] | None = None) -> dict:
    """Pre-gate rejection rates by rule and reason over every worker attempt of these runs.

    A line with no ``task_id`` is the operator's own call (a harvest re-gate) and is
    dropped; a line of another run is dropped; a call that exited 3 (the gate
    could not run) is an infrastructure failure, counted apart and never as a
    rejection.  A rule is counted from the line's ``by_rule`` when it carries one,
    else from its reasons' ``[rule N]`` prefixes.
    """
    wanted = set(run_ids) if run_ids is not None else None
    calls = [l for l in lines if str(l.get("task_id") or "").strip()
             and (wanted is None or str(l.get("run_id") or "") in wanted)]
    broken = [l for l in calls if l.get("exit") == EXIT_GATE_BROKEN]
    attempts = [l for l in calls if l.get("exit") != EXIT_GATE_BROKEN]
    rejected = [l for l in attempts if not l.get("ok")]
    by_rule: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for line in rejected:
        rules = set(str(k) for k in (line.get("by_rule") or {}))
        reasons = [str(r) for r in (line.get("reasons") or [])]
        if not rules:
            rules = {split_reason(r)[0] for r in reasons if split_reason(r)[0]}
        for rule in rules or {"unruled"}:
            by_rule[rule] = by_rule.get(rule, 0) + 1
        for name in {classify_reason(r) for r in reasons} or {"unruled"}:
            by_reason[name] = by_reason.get(name, 0) + 1
    total = len(attempts)

    def rated(counts: dict[str, int]) -> dict:
        return {k: {"attempts": v, "rate": v / total if total else None}
                for k, v in sorted(counts.items())}

    return {"attempts": total, "rejected": len(rejected),
            "rejection_rate": (len(rejected) / total) if total else None,
            "broken": len(broken), "dropped_lines": len(lines) - len(calls),
            "by_rule": rated(by_rule), "by_reason": rated(by_reason)}


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
