"""The score document: one run, one split, one arm, against frozen gold.

New code, implementing docs/proposals/wikipedia-locate.md sections 8.2, 8.3 and
8.4 in the shape references/locate-bench.md section 8 fixes.

**What it reads** (and nothing else): the bench's frozen gold manifest and label
files; ``reports/locate-outcomes.<run>.jsonl``; the documents, ``run.yaml``,
task manifests and ``RECEIPT.json`` of every run those rows name (inventory,
locate, challenge); the bench articles' layers; optionally a gate log, a
transcript directory (for the canary), per-article costs and audit labels.

**The refusals**, each a ``CfError`` naming what to fix: a receipt that is not
``complete`` or carries no ``cost`` block (the observed model ids come from it);
gold whose files changed after freeze; a run that did not plan every article of
the split, or read another rendering of one; a split's arm that is not the run's;
the test split without ``--checkpoint``; and a transcript that holds the
bench's canary, which fails the run outright (section 8.4).

**``measurement_id``** is the sha of every pin section 8.3 lists, so two score
documents with one id were measured the same way, and ``cf bench compare``
reads the pins, not the id, to say which one differs.
"""
from __future__ import annotations

import gzip
import json
import random
from pathlib import Path
from typing import Any

from cflib import vocab
from cflib.errors import CfError
from cflib.jsonio import read_json, write_json
from cflib.paths import skill_root

from . import gold as gold_mod
from . import inputs, metrics
from .layout import SCORE_SCHEMA, BenchLayout, now, read_jsonl_rows, sha_of, write_canonical_jsonl

#: the version pins a run.yaml may carry under ``versions`` (or ``--pin`` may give)
VERSION_PINS = ("agent_cli", "tend", "agentbox")
UNRECORDED = "unrecorded"
#: the fixed audit of exact/broad claims outside gold, per arm (section 8.2)
AUDIT_SIZE = 30
AUDIT_RELATIONS = frozenset({vocab.EXACT_MATCH, vocab.BROAD_MATCH})
DEFAULT_REPLICATES = 2000


def skill_commit() -> str:
    """This checkout's HEAD through ``cflib.gitsafe``, or ``unrecorded``."""
    import subprocess

    from cflib import gitsafe

    try:
        done = gitsafe.run(skill_root(), ["rev-parse", "HEAD"], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return UNRECORDED
    found = done.stdout.strip() if done.returncode == 0 else ""
    return found if len(found) == 40 else UNRECORDED


# --------------------------------------------------------------------------
# the runs a score reads
# --------------------------------------------------------------------------

def _runs_of(rows: list[dict], run_id: str) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {"inventory": set(), "locate": {run_id}, "challenge": set()}
    for row in rows:
        for stage, rid in (row.get("runs") or {}).items():
            if rid and stage in out:
                out[stage].add(str(rid))
    return {k: sorted(v) for k, v in out.items()}


def _locate_block(run: dict) -> dict:
    block = run.get("locate")
    return block if isinstance(block, dict) else {}


def load_runs(paths, run_ids: list[str]) -> tuple[dict[str, dict], dict[str, dict], dict[str, str]]:
    """``(run.yaml, receipt, receipt sha)`` per run; refused when a receipt is incomplete."""
    yamls, receipts, shas = {}, {}, {}
    for rid in run_ids:
        receipt, sha = inputs.receipt(paths, rid)
        if receipt.get("complete") is not True:
            raise CfError(f"run {rid}'s receipt is not complete",
                          hint="a score over an incomplete corpus is a lower bound on nothing; "
                               "re-run the missing tasks and harvest again")
        if not isinstance(receipt.get("cost"), dict):
            raise CfError(f"run {rid}'s receipt has no cost block",
                          hint="the observed model ids and the cost per article come from the "
                               "agents' transcripts: harvest again with --transcripts")
        yamls[rid] = inputs.run_yaml(paths, rid)
        receipts[rid] = receipt
        shas[rid] = sha
    return yamls, receipts, shas


def pins(paths, *, yamls: dict, receipts: dict, receipt_shas: dict, locate_run: str,
         renderers: list[str], gold_sha: str, overrides: dict[str, str]) -> dict:
    """Every pin of section 8.3, from the runs' own records."""
    locate = _locate_block(yamls[locate_run])
    observed: dict[str, int] = {}
    tables: set[str] = set()
    for receipt in receipts.values():
        cost = receipt["cost"]
        by_model = cost.get("messages_by_model")
        for model, tokens in sorted((cost.get("tokens") or {}).items()):
            count = (by_model or {}).get(model)
            if count is None:
                count = sum(int(v) for v in (tokens or {}).values() if isinstance(v, (int, float)))
            observed[model] = observed.get(model, 0) + int(count)
        if cost.get("price_table_sha256"):
            tables.add(str(cost["price_table_sha256"]))
    versions: dict[str, str] = {}
    for key in VERSION_PINS:
        found = sorted({str((y.get("versions") or {}).get(key)) for y in yamls.values()
                        if isinstance(y.get("versions"), dict) and (y.get("versions") or {}).get(key)})
        versions[key] = overrides.get(key) or (",".join(found) if found else UNRECORDED)
    gate = {}
    for rid in sorted(yamls):
        path = paths.gate_json(rid)
        gate[rid] = inputs.sha256_file(path) if path.is_file() else UNRECORDED
    return {
        "skill_commit": skill_commit(),
        "prompts": {rid: dict(sorted((y.get("prompts") or {}).items())) for rid, y in sorted(yamls.items())},
        "gate": gate,
        "observed_models": dict(sorted(observed.items())),
        **versions,
        "universe_sha256": ((locate.get("universe") or {}).get("sha256")) or UNRECORDED,
        "corpus_db_sha256": ((locate.get("universe") or {}).get("corpus_db_sha256")) or UNRECORDED,
        "sourceset_manifest_sha256": ((locate.get("sourceset") or {}).get("manifest_sha256")) or UNRECORDED,
        "renderer": sorted(set(renderers)),
        "gold_manifest_sha256": gold_sha,
        "receipts": dict(sorted(receipt_shas.items())),
        "price_table_sha256": sorted(tables),
    }


def measurement_id(pin_doc: dict) -> str:
    return sha_of(pin_doc)


def score_name(split: str, mid: str) -> str:
    """``<split>-<first 16 hex of measurement_id>``: one run scores once per split."""
    return f"{split}-{mid[:16]}"


# --------------------------------------------------------------------------
# the canary
# --------------------------------------------------------------------------

def canary_check(canary: str, *, transcripts: Path | None, report: Path | None,
                 skip: bool) -> dict:
    """Refuse when any transcript holds the bench's canary (section 8.4)."""
    if transcripts is not None:
        files = [transcripts] if transcripts.is_file() else sorted(
            p for p in transcripts.rglob("*") if p.is_file())
        hits = []
        for path in files:
            try:
                if path.suffix == ".gz":
                    with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as handle:
                        text = handle.read()
                else:
                    text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if canary in text:
                hits.append(path.name)
        if hits:
            raise CfError(f"{len(hits)} transcript(s) hold the bench's label canary: "
                          + ", ".join(hits[:5]),
                          hint="a label reached an agent: the run fails, and the bench's labels "
                               "must be treated as seen")
        return {"ran": True, "basis": "transcripts", "scanned": len(files), "hits": 0}
    if report is not None:
        doc = read_json(report)
        hits = list(doc.get("hits") or [])
        if hits:
            raise CfError(f"the host's canary scan found {len(hits)} transcript(s) holding a "
                          "label canary", hint="a label reached an agent: the run fails")
        return {"ran": True, "basis": "host report", "scanned": int(doc.get("scanned") or 0), "hits": 0}
    if skip:
        return {"ran": False, "basis": "--no-canary-check"}
    raise CfError("no canary check: pass --transcripts <dir>, --canary-report <file> or "
                  "--no-canary-check",
                  hint="a transcript holding a label canary fails the run (section 8.4); the "
                       "bench's host/canaries.txt is what a host-side scan greps for")


# --------------------------------------------------------------------------
# run passages
# --------------------------------------------------------------------------

def run_passages(paths, rows: list[dict], runs: dict[str, list[str]],
                 articles: set[str], layers_sha: dict[str, str]) -> tuple[list[metrics.RunPassage], dict[str, str], dict]:
    """Every passage the run inventoried in the split's articles, with its outcome.

    The frozen inventory supplies spans, scopes and kinds; the outcome rows supply
    outcome, status and locations.  A passage with no outcome row has outcome
    ``None``: its locate task never landed.
    """
    inventory_docs = [d for rid in runs["inventory"] for d in inputs.documents(paths, rid)
                      if d.get("mode") == vocab.LOCATE_INVENTORY]
    locate_docs = [d for rid in runs["locate"] for d in inputs.documents(paths, rid)
                   if d.get("mode") == vocab.LOCATE]
    planned: set[str] = set()
    manifests_seen = False
    for rid in runs["inventory"]:
        for manifest in inputs.task_manifests(paths, rid):
            manifests_seen = True
            for doc in manifest.get("documents") or []:
                planned.add(str(doc.get("source_id")))
    passages: dict[tuple[str, str], dict] = {}
    state: dict[str, str] = {}
    for doc in inventory_docs:
        sid = str(doc.get("source_id"))
        if sid not in articles:
            continue
        if layers_sha.get(sid) and doc.get("layer_sha256") and doc["layer_sha256"] != layers_sha[sid]:
            raise CfError(f"the run read another rendering of {sid} (layer "
                          f"{str(doc['layer_sha256'])[:12]}…, the bench pins {layers_sha[sid][:12]}…)",
                          hint="score a run planned from the bench's sourceset pin")
        if doc.get("unresolved"):
            state.setdefault(sid, "unresolved")
            continue
        state[sid] = "ran"
        for p in doc.get("passages") or []:
            passages[(sid, str(p.get("item")))] = dict(p, source_id=sid)
    for row in rows:
        sid = str(row.get("source_id"))
        if sid not in articles:
            continue
        key = (sid, str(row.get("item")))
        merged = dict(passages.get(key, {}))
        merged.update({k: v for k, v in row.items() if v is not None or k not in merged})
        passages[key] = merged
        state.setdefault(sid, "ran")
    receipts = {}
    for doc in locate_docs:
        for p in doc.get("passages") or []:
            receipts[(str(doc.get("source_id")), str(p.get("item")))] = p.get("receipt")
    coverage_basis = "task manifests" if manifests_seen else "assumed: no task manifest found"
    if manifests_seen:
        missing = sorted(articles - planned)
        if missing:
            raise CfError(f"the run did not plan {len(missing)} article(s) of the split: "
                          + ", ".join(missing[:8]),
                          hint="a bench run covers every article of the split it is scored on")
    for sid in sorted(articles):
        state.setdefault(sid, "gate_failed")
    out = []
    for (sid, item), p in sorted(passages.items()):
        span = p.get("span")
        out.append(metrics.RunPassage(
            source_id=sid, item=item, span=tuple(span) if span else None,
            scope=p.get("scope"), passage_kind=p.get("passage_kind"),
            prominence=p.get("prominence"), outcome=p.get("outcome"), status=p.get("status"),
            locations=list(p.get("locations") or []), receipt=receipts.get((sid, item))))
    return out, state, {"basis": coverage_basis, "planned": len(planned) if manifests_seen else None}


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------

def _article_costs(path: Path | None, receipts: dict, articles: list[str],
                   cluster_of: dict[str, str]) -> tuple[metrics.Ratio, dict]:
    ratio = metrics.Ratio()
    total = sum(float(r["cost"].get("usd") or 0.0) for r in receipts.values())
    if path is not None:
        per = read_json(path)
        if not isinstance(per, dict):
            raise CfError(f"{path} is not a mapping of source id to dollars")
        missing = [a for a in articles if a not in per]
        if missing:
            raise CfError(f"{path} has no cost for {len(missing)} article(s): " + ", ".join(missing[:5]))
        for a in articles:
            ratio.add(cluster_of.get(a, "?"), float(per[a]), 1.0)
        basis = "per-article costs"
    else:
        share = total / len(articles) if articles else 0.0
        for a in articles:
            ratio.add(cluster_of.get(a, "?"), share, 1.0)
        basis = "the runs' total split evenly over the articles (no per-article costs)"
    return ratio, {"usd_total": round(total, 6), "articles": len(articles), "basis": basis}


def _audit(run: list[metrics.RunPassage], joined_items: set[tuple[str, str]], seed: int,
           arm: str) -> list[dict]:
    pool = []
    for p in run:
        if (p.source_id, p.item) in joined_items:
            continue
        for loc in p.locations:
            if loc.get("relation") in AUDIT_RELATIONS:
                pool.append({"source_id": p.source_id, "item": p.item,
                             "span": list(p.span) if p.span else None,
                             "library": loc.get("library"), "declaration": loc.get("declaration"),
                             "commit": loc.get("commit"), "relation": loc.get("relation")})
    pool.sort(key=lambda r: (r["source_id"], r["item"], str(r["library"]), str(r["declaration"])))
    rng = random.Random(f"{seed}:audit:{arm}")
    chosen = sorted(rng.sample(range(len(pool)), min(AUDIT_SIZE, len(pool))))
    out = []
    for i in chosen:
        row = pool[i]
        row["audit_id"] = "audit-" + sha_of(row)[:12]
        row["verdict"] = None
        out.append(row)
    return out


def score(paths, layout: BenchLayout, bench: dict, *, run_id: str, split: str,
          arm: str | None = None, gold_version: int | None = None, gate_log: Path | None = None,
          transcripts: Path | None = None, canary_report: Path | None = None,
          no_canary_check: bool = False, article_costs: Path | None = None,
          audit_labels: Path | None = None, replicates: int = DEFAULT_REPLICATES,
          seed: int = 0, pin_overrides: dict[str, str] | None = None,
          checkpoint: bool = False) -> dict:
    vocab.check(split, vocab.BENCH_SPLITS, "split")
    if split == "test" and not checkpoint:
        raise CfError("the test split is scored only at checkpoints",
                      hint="pass --checkpoint when this is one (section 9, step 4)")
    version, manifest = gold_mod.latest_manifest(layout, gold_version)
    gold_mod.verify_frozen(layout, manifest)
    articles_in = sorted((manifest.get("splits") or {}).get(split) or [])
    if not articles_in:
        raise CfError(f"bench {layout.bench_id} has no {split} article")
    rows = inputs.outcomes(paths, run_id)
    runs = _runs_of(rows, run_id)
    inventory_run = _locate_block(inputs.run_yaml(paths, run_id)).get("inventory_run")
    if inventory_run and inventory_run not in runs["inventory"]:
        runs["inventory"] = sorted(set(runs["inventory"]) | {str(inventory_run)})
    if not runs["inventory"]:
        raise CfError(f"run {run_id} names no inventory run",
                      hint="its run.yaml locate.inventory_run (or its outcome rows' runs.inventory) "
                           "says which frozen inventory it located")
    all_runs = sorted({r for v in runs.values() for r in v})
    yamls, receipts, receipt_shas = load_runs(paths, all_runs)
    run_arm = str(_locate_block(yamls[run_id]).get("arm") or vocab.BENCH_ARM_ASSISTED)
    vocab.check(run_arm, vocab.BENCH_ARMS, "run arm")
    if arm is not None and arm != run_arm:
        raise CfError(f"run {run_id} is the {run_arm} arm, not {arm}",
                      hint="the two G1 arms are never pooled: score each run as the arm it ran")
    arm = run_arm
    canary = canary_check(bench["canary"], transcripts=transcripts, report=canary_report,
                          skip=no_canary_check)
    frame = {a["source_id"]: a for a in read_json(layout.frame_json)["articles"]}
    layers_sha = {sid: frame[sid].get("layer_sha256") for sid in articles_in}
    pin_doc = pins(paths, yamls=yamls, receipts=receipts, receipt_shas=receipt_shas,
                   locate_run=run_id, renderers=[frame[s].get("renderer") or UNRECORDED
                                                 for s in articles_in],
                   gold_sha=manifest["manifest_sha256"], overrides=pin_overrides or {})
    mid = measurement_id(pin_doc)
    run, state, coverage = run_passages(paths, rows, runs, set(articles_in), layers_sha)
    sourceset = bench["sourceset"]["id"]
    layers = {sid: inputs.read_layer(paths, sourceset, sid, layers_sha[sid]) for sid in articles_in}
    cluster_of = {sid: cid for cid, members in (manifest.get("clusters") or {}).items()
                  for sid in members}
    clusters = sorted({cluster_of[s] for s in articles_in if s in cluster_of})
    assessment = {sid: frame[sid].get("assessment") or {} for sid in articles_in}
    seen_ids = set(manifest.get("seen") or [])
    tiers: dict[str, Any] = {}
    all_misses: list[dict] = []
    joined_items: set[tuple[str, str]] = set()
    positives: dict[str, int] = {}
    strata_out: dict[str, Any] = {}
    per_cluster: dict[str, Any] = {}
    ablated = None
    ablation = read_json(layout.ablation_json) if layout.ablation_json.is_file() else None
    planted_run = bool(ablation and pin_doc["corpus_db_sha256"] == ablation.get("corpus_sha256"))
    if planted_run:
        ablated = {tuple(r.split(":", 1)) for r in ablation.get("removed") or []}
    for tier in ("G1", "G3", "GX", "G4"):
        info = (manifest.get("tiers") or {}).get(tier)
        if not info:
            continue
        label_rows = [r for r in read_jsonl_rows(layout.root / info["file"])
                      if r.get("source_id") in layers]
        gold = metrics.gold_passages(tier, label_rows)
        for g in gold:
            if split != "dev":
                g.seen = g.gold_id in seen_ids
        result = metrics.evaluate_tier(tier, gold, run, layers=layers, cluster_of=cluster_of,
                                       article_state=state, assessment=assessment)
        joined = metrics.join([g for g in gold if g.span is not None and g.level == "passage"],
                              run, layers)
        joined_items |= {(r.source_id, r.item) for r in joined.values()}
        summary = metrics.summarise(result.ratios, clusters, replicates=replicates,
                                    seed=f"{seed}:{tier}")
        tiers[tier] = {"gold": result.gold, "joined": result.joined, "metrics": summary,
                       "relation_confusion": result.confusion,
                       **({"arm": arm} if tier == "G1" else {})}
        positives[tier] = sum(1 for g in gold if g.status == vocab.STATUS_FORMALIZED)
        per_cluster[tier] = {name: r.by_cluster for name, r in result.ratios.items()
                             if not name.startswith("fnf_cause:")}
        strata_out[tier] = {key: {name: {**metrics.bootstrap(r.by_cluster, clusters,
                                                             replicates=replicates,
                                                             seed=f"{seed}:{tier}:{key}:{name}"),
                                         "by_cluster": r.by_cluster}
                                  for name, r in sorted(group.items())}
                            for key, group in sorted(result.strata.items())}
        all_misses.extend(result.misses)
        if tier == "G1" and planted_run and split != "dev":
            p_gold = [g for g in gold if g.accept & ablated]
            p_result = metrics.evaluate_tier("P", p_gold, run, layers=layers, cluster_of=cluster_of,
                                             article_state=state, assessment=assessment,
                                             ablated=ablated)
            tiers["P"] = {"gold": p_result.gold, "joined": p_result.joined,
                          "metrics": metrics.summarise(p_result.ratios, clusters,
                                                       replicates=replicates, seed=f"{seed}:P"),
                          "ablation_sha256": manifest.get("tiers", {}).get("P", {}).get("ablation_sha256")}
            per_cluster["P"] = {name: r.by_cluster for name, r in p_result.ratios.items()
                                if not name.startswith("fnf_cause:")}
            all_misses.extend(m for m in p_result.misses if m["code"] == "E-MEMORY")
    cost_ratio, cost_info = _article_costs(article_costs, receipts, articles_in, cluster_of)
    cost_info.update(metrics.bootstrap(cost_ratio.by_cluster, clusters, replicates=replicates,
                                       seed=f"{seed}:cost"))
    per_cluster["cost"] = {"usd_per_article": cost_ratio.by_cluster}
    gate_rej = None
    if gate_log is not None:
        gate_rej = metrics.gate_rejections(inputs.gate_log(gate_log))
    audit = _audit(run, joined_items, seed, arm)
    score_id = score_name(split, mid)
    write_canonical_jsonl(layout.audit_jsonl(score_id), audit)
    audit_summary: dict[str, Any] = {"sampled": len(audit), "file": f"audits/{score_id}.jsonl",
                                     "labeled": 0, "overclaims": 0}
    if audit_labels is not None:
        labels = {r.get("audit_id"): r.get("verdict") for r in read_jsonl_rows(audit_labels)}
        known = {a["audit_id"] for a in audit}
        labeled = {k: v for k, v in labels.items() if k in known and v in ("correct", "overclaim")}
        audit_summary.update(labeled=len(labeled),
                             overclaims=sum(1 for v in labeled.values() if v == "overclaim"))
    doc = {
        "schema": SCORE_SCHEMA, "bench": layout.bench_id, "split": split, "arm": arm,
        "checkpoint": bool(checkpoint), "run": run_id, "runs": runs,
        "measurement_id": mid, "pins": pin_doc,
        "gold_manifest": {"version": version, "sha256": manifest["manifest_sha256"]},
        "coverage": {"articles": len(articles_in), "clusters": len(clusters),
                     "gold_positives": positives, "total_positives": sum(positives.values()),
                     "articles_unresolved": sorted(s for s, v in state.items() if v == "unresolved"),
                     "articles_gate_failed": sorted(s for s, v in state.items() if v == "gate_failed"),
                     "run_passages": len(run), **coverage},
        "canary_check": canary,
        "tiers": tiers,
        "strata": strata_out,
        "cost": cost_info,
        "gate_rejections": gate_rej,
        "misses": sorted(all_misses, key=lambda m: (m["tier"], m["gold_id"], m["kind"])),
        "audit": audit_summary,
        "bootstrap": {"replicates": replicates, "seed": seed, "unit": "article cluster",
                      "interval": "95% percentile, nearest rank"},
        "per_cluster": per_cluster,
        "created": now(),
    }
    write_json(layout.score_json(score_id), doc, sort_keys=True)
    return doc
