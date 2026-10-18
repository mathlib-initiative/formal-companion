"""The score document: one locate run, one split, one arm, against frozen gold.

New code, implementing docs/proposals/wikipedia-locate.md sections 8.2, 8.3 and
8.4 in the shape references/locate-bench.md section 8 fixes.

**What it reads**, each through the package that owns it (``bench.inputs``): the
bench's frozen gold manifest and label files; the locate run's ``run.yaml``,
``reports/locate-outcomes.<run>.jsonl`` and ``reports/locate-tally.<run>.json``
(which challenge runs the outcomes were built with); the frozen inventory, task
manifests, documents and ``RECEIPT.json`` of every run involved (inventory,
locate, challenge); the bench articles' layers; and, when ``cf harvest`` brought
them home, each run's ``runs/<run>/host/gate.jsonl`` and ``canary.json``.

**The refusals**, each a ``CfError`` naming what to fix: a receipt that is not
complete, not gated, or carries no ``cost`` block; gold whose files changed after
freeze; a run of another universe (or another state of it) than gold was
labelled at; runs of mixed arms; a subject-only (article-tier) run; an inventory
that did not plan, or read another rendering of, an article of the split; a
``formal_math`` passage of the split the locate run has no outcome row for (it
was never planned: a ``--source`` or shard run is scored on the articles it
covers, never as gate failures of the rest); a planted run whose tools database
is not this bench's ablation, or scored off eval; the test split without
``--checkpoint``; and a transcript that holds the bench's canary, which fails the
run outright (section 8.4).

**``measurement_id``** is the sha of every pin section 8.3 lists -- each run's
own recorded commit and gate files, prompts, models, tool versions, universe,
sourceset, renderer, gold, receipts and prices -- so two score documents with one
id were measured the same way.  The scorer's own code is a separate ``scorer``
field, not a pin: re-scoring an unchanged run with unchanged scoring code
rewrites the same file, whatever else the checkout committed in between.
"""
from __future__ import annotations

import gzip
import math
import random
import zlib
from pathlib import Path
from typing import Any

from cflib import items as cf_items
from cflib import vocab
from cflib.errors import CfError
from cflib.jsonio import read_json, write_json
from cflib.paths import skill_root

from . import gold as gold_mod
from . import inputs, metrics
from .layout import SCORE_SCHEMA, BenchLayout, now, read_jsonl_rows, sha_of, write_canonical_jsonl

#: the version pins ``cf run`` records in run.yaml ``versions`` (or ``--pin`` gives)
VERSION_PINS = ("agent_cli", "tend", "agentbox")
UNRECORDED = "unrecorded"
#: the fixed audit of exact/broad claims outside gold, per arm (section 8.2)
AUDIT_SIZE = 30
AUDIT_RELATIONS = frozenset({vocab.EXACT_MATCH, vocab.BROAD_MATCH})
DEFAULT_REPLICATES = 2000
#: the tools database kinds of run.yaml locate.universe.tools_corpus_db (cflib.vocab)
TOOLS_FULL, TOOLS_BLIND, TOOLS_PLANTED = (vocab.TOOLS_DB_FULL, vocab.TOOLS_DB_BLIND,
                                          vocab.TOOLS_DB_PLANTED)
STAGES = ("inventory", "locate", "challenge")


def scorer() -> dict:
    """The scorer's own identity: a sha over ``scripts/bench/*.py``, names and bytes."""
    files = sorted((skill_root() / "scripts" / "bench").glob("*.py"))
    parts = [(p.name, inputs.sha256_file(p)) for p in files]
    return {"sha256": sha_of(parts), "files": len(parts)}


# --------------------------------------------------------------------------
# the runs a score reads
# --------------------------------------------------------------------------

def _locate_block(run: dict) -> dict:
    block = run.get("locate")
    return block if isinstance(block, dict) else {}


def runs_of(paths, run_id: str, rows: list[dict], locate: dict) -> dict[str, list[str]]:
    """The inventory, locate and challenge runs of one scored locate run.

    The inventory run is ``run.yaml`` ``locate.inventory_run`` (with any the rows
    name); the challenge runs are the ones the locate tally built the outcomes
    with (``reports/locate-tally.<run>.json`` ``challenge_runs``), so they are
    pinned and gated even when no verdict touched a row.
    """
    out: dict[str, set[str]] = {"inventory": set(), "locate": {run_id}, "challenge": set()}
    if locate.get("inventory_run"):
        out["inventory"].add(str(locate["inventory_run"]))
    for row in rows:
        for stage, rid in (row.get("runs") or {}).items():
            if rid and stage in out:
                out[stage].add(str(rid))
    out["challenge"] |= {str(r) for r in inputs.tally(paths, run_id).get("challenge_runs") or []}
    if not out["inventory"]:
        raise CfError(f"run {run_id} names no inventory run",
                      hint="its run.yaml locate.inventory_run says which frozen inventory it located")
    return {k: sorted(v) for k, v in out.items()}


def load_runs(paths, run_ids: list[str]) -> tuple[dict[str, dict], dict[str, dict], dict[str, str]]:
    """``(run.yaml, receipt, receipt sha)`` per run; refused unless complete, gated and priced."""
    yamls, receipts, shas = {}, {}, {}
    for rid in run_ids:
        receipt, sha = inputs.receipt(paths, rid)
        if not isinstance(receipt.get("cost"), dict):
            raise CfError(f"run {rid}'s receipt has no cost block",
                          hint="the observed model ids and the cost per article come from the "
                               "agents' transcripts: harvest again with --transcripts")
        dollars(receipt["cost"].get("usd"), f"run {rid}'s receipt cost.usd")
        yamls[rid] = inputs.run_yaml(paths, rid)
        receipts[rid] = receipt
        shas[rid] = sha
    return yamls, receipts, shas


def dollars(value, what: str) -> float:
    """A finite, non-negative number of dollars, or refuse: a NaN or a missing sum is no cost."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value < 0:
        raise CfError(f"{what} is {value!r}, not a finite non-negative number of dollars",
                      hint="a cost the score cannot read is never read as $0")
    return float(value)


def check_arms(yamls: dict[str, dict], run_id: str) -> str:
    """The locate run's arm, refused when any other run of the score has another."""
    arm = str(_locate_block(yamls[run_id]).get("arm") or vocab.BENCH_ARM_ASSISTED)
    vocab.check(arm, vocab.BENCH_ARMS, "run arm")
    other = {rid: str(_locate_block(y).get("arm") or vocab.BENCH_ARM_ASSISTED)
             for rid, y in yamls.items() if rid != run_id}
    mixed = sorted(rid for rid, a in other.items() if a != arm)
    if mixed:
        raise CfError(f"run {run_id} is the {arm} arm, but {', '.join(mixed)} "
                      f"{'is' if len(mixed) == 1 else 'are'} not",
                      hint="a G1-blind score rests on a blind inventory and blind challenges; "
                           "the arms are never mixed")
    return arm


def check_universe(run_locate: dict, manifest: dict) -> dict:
    """Refuse a run of another universe, or another state of it, than gold was labelled at."""
    run_u = run_locate.get("universe") or {}
    gold_u = manifest.get("universe") or {}
    if run_u.get("id") != gold_u.get("id") or run_u.get("sha256") != gold_u.get("sha256"):
        raise CfError(f"the run was planned on universe {run_u.get('id')!r} at "
                      f"{str(run_u.get('sha256'))[:12]}…, gold is labelled at {gold_u.get('id')!r} "
                      f"at {str(gold_u.get('sha256'))[:12]}…",
                      hint="score runs of the universe gold was labelled at, or re-anchor the "
                           "bench at the new one (cf bench anchor --supersede, then freeze)")
    return run_u


def tools_db(run_universe: dict) -> dict:
    """``{kind, sha256}`` of the database the workers searched (``full`` when unrecorded)."""
    tools = run_universe.get("tools_corpus_db")
    if not isinstance(tools, dict):
        return {"kind": TOOLS_FULL, "sha256": run_universe.get("corpus_db_sha256") or UNRECORDED}
    return {"kind": str(tools.get("kind") or TOOLS_FULL),
            "sha256": str(tools.get("sha256") or UNRECORDED)}


def pins(paths, *, yamls: dict, receipts: dict, receipt_shas: dict, locate_run: str,
         renderers: list[str], manifest: dict, tools: dict, challenge_runs: list[str],
         overrides: dict[str, str]) -> dict:
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
        recorded, given = ",".join(found), overrides.get(key)
        if given and recorded and given != recorded:
            raise CfError(f"--pin {key}={given}: the runs' run.yaml record {key} {recorded}",
                          hint="--pin fills a version a run.yaml does not record; it never "
                               "replaces one")
        versions[key] = recorded or given or UNRECORDED
    gate = {}
    for rid in sorted(yamls):
        path = paths.gate_json(rid)
        gate[rid] = inputs.sha256_file(path) if path.is_file() else UNRECORDED
    universe = locate.get("universe") or {}
    return {
        # each run's own commit and staged gate code, as its plan recorded them
        "skill_commit": {rid: str(_locate_block(y).get("skill_commit") or UNRECORDED)
                         for rid, y in sorted(yamls.items())},
        "gate_files": {rid: (sha_of(_locate_block(y)["gate_files"])
                             if isinstance(_locate_block(y).get("gate_files"), dict) else UNRECORDED)
                       for rid, y in sorted(yamls.items())},
        "prompts": {rid: dict(sorted((y.get("prompts") or {}).items())) for rid, y in sorted(yamls.items())},
        "gate": gate,
        "observed_models": dict(sorted(observed.items())),
        **versions,
        "universe_sha256": universe.get("sha256") or UNRECORDED,
        "gold_universe_sha256": (manifest.get("universe") or {}).get("sha256") or UNRECORDED,
        "corpus_db_sha256": universe.get("corpus_db_sha256") or UNRECORDED,
        "tools_corpus_db": tools,
        "sourceset_manifest_sha256": ((locate.get("sourceset") or {}).get("manifest_sha256")) or UNRECORDED,
        "renderer": sorted(set(renderers)),
        "gold_manifest_sha256": manifest["manifest_sha256"],
        "receipts": dict(sorted(receipt_shas.items())),
        "price_table_sha256": sorted(tables),
        "challenge_runs": list(challenge_runs),
    }


def measurement_id(pin_doc: dict) -> str:
    return sha_of(pin_doc)


def score_name(split: str, mid: str) -> str:
    """``<split>-<first 16 hex of measurement_id>``: one run scores once per split."""
    return f"{split}-{mid[:16]}"


# --------------------------------------------------------------------------
# the canary
# --------------------------------------------------------------------------

def _scan_report(doc: dict, where: str) -> dict:
    """A host scan that read something: ``scanned`` a positive count, ``hits`` a list."""
    scanned = doc.get("scanned")
    if isinstance(scanned, bool) or not isinstance(scanned, int) or scanned <= 0 \
            or not isinstance(doc.get("hits"), list):
        raise CfError(f"{where} is not a canary scan that read anything ({{scanned: >0, hits: [...]}}, "
                      f"got scanned {scanned!r})",
                      hint="a scan of nothing is no check: re-run the host's scan, or pass "
                           "--transcripts <dir> or --no-canary-check")
    return doc


def _read_transcript(path: Path) -> str:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as handle:
                return handle.read()
        return path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, EOFError, zlib.error) as exc:
        raise CfError(f"cannot read transcript {path} ({exc})",
                      hint="an unread transcript is never a clean one: re-harvest it, or pass "
                           "--no-canary-check") from None


def canary_check(paths, canary: str, run_ids: list[str], *, transcripts: Path | None,
                 report: Path | None, skip: bool) -> dict:
    """Refuse when any transcript holds the bench's canary (section 8.4).

    ``--transcripts`` scans a directory (or one file) here; ``--canary-report``
    reads one host scan; with neither, every run's harvested
    ``runs/<run>/host/canary.json`` is read, and a run without one leaves the
    check undone, which is refused unless ``--no-canary-check`` says so.  A check
    that read nothing is refused, never passed: a missing path, an empty
    directory, an unreadable transcript, a scan of zero files.
    """
    if transcripts is not None:
        if not transcripts.exists():
            raise CfError(f"--transcripts {transcripts}: no such file or directory")
        files = [transcripts] if transcripts.is_file() else sorted(
            p for p in transcripts.rglob("*") if p.is_file())
        if not files:
            raise CfError(f"the canary scan read no transcript under {transcripts}",
                          hint="point --transcripts at the harvested transcripts, or pass "
                               "--no-canary-check")
        hits = [path.name for path in files if canary in _read_transcript(path)]
        if hits:
            raise CfError(f"{len(hits)} transcript(s) hold the bench's label canary: "
                          + ", ".join(hits[:5]),
                          hint="a label reached an agent: the run fails, and the bench's labels "
                               "must be treated as seen")
        return {"ran": True, "basis": "transcripts", "scanned": len(files), "hits": 0}
    reports = {}
    if report is not None:
        reports["--canary-report"] = _scan_report(inputs.canary_report(report), str(report))
    else:
        for rid in run_ids:
            path = inputs.run_host_dir(paths, rid) / inputs.HOST_CANARY
            if path.is_file():
                reports[rid] = _scan_report(inputs.canary_report(path), str(path))
    if reports and (report is not None or len(reports) == len(run_ids)):
        hits = sorted(str(h) for doc in reports.values() for h in doc.get("hits") or [])
        if hits:
            raise CfError(f"the host's canary scan found {len(hits)} transcript(s) holding a "
                          "label canary: " + ", ".join(hits[:5]),
                          hint="a label reached an agent: the run fails")
        return {"ran": True, "basis": "host reports" if report is None else "host report",
                "reports": sorted(reports), "hits": 0,
                "scanned": sum(int(d["scanned"]) for d in reports.values())}
    if skip:
        return {"ran": False, "basis": "--no-canary-check"}
    missing = sorted(set(run_ids) - set(reports))
    raise CfError("no canary check" + (f" for run(s) {', '.join(missing)}" if reports else "")
                  + ": pass --transcripts <dir>, --canary-report <file> or --no-canary-check",
                  hint="cf harvest brings a host's scan home as runs/<run>/host/canary.json; a "
                       "transcript holding a label canary fails the run (section 8.4)")


# --------------------------------------------------------------------------
# run passages and coverage
# --------------------------------------------------------------------------

def run_passages(paths, rows: list[dict], runs: dict[str, list[str]], articles: set[str],
                 layers_sha: dict[str, str], renderers_pinned: dict[str, str]
                 ) -> tuple[list[metrics.RunPassage], dict[str, str], dict, list[str]]:
    """The scored run's passages in the split's articles, from its outcome rows.

    The outcome rows (``locate.outcomes``) carry one row per passage the locate
    run planned -- landed, or ``missing: true`` when no document landed (which
    ends as gate-failed) -- and a scoped-out row for each non-located passage of
    its sources.  The frozen inventory sets the inventory side: which articles it
    planned, landed, left unresolved, and from which rendering; and every
    ``formal_math`` passage of a covered article must have an outcome row, or the
    locate run never planned it and the score is refused.  Each inventory document
    of a split article must name the layer and renderer it read, and they must be
    the ones the bench pins; the renderers it names are the score's renderer pin.
    """
    landed: dict[str, str] = {}
    rendered: dict[str, str] = {}
    unresolved: set[str] = set()
    frozen: dict[tuple[str, str], dict] = {}
    planned: set[str] = set()
    manifests_seen = False
    for inv_run in runs["inventory"]:
        got = inputs.inventory(paths, inv_run)
        frozen.update(got["passages"])
        unresolved |= set(got["unresolved"])
        for rel, doc in inputs.locate_documents(paths, inv_run):
            sid = str(doc.get("source_id") or "")
            if sid in articles:
                for key in ("layer_sha256", "renderer"):
                    if not doc.get(key):
                        raise CfError(f"{rel} of inventory run {inv_run} names no {key}",
                                      hint="an inventory document says which rendering it read")
                landed[sid] = str(doc["layer_sha256"])
                rendered[sid] = str(doc["renderer"])
        for manifest in inputs.task_manifests(paths, inv_run):
            manifests_seen = True
            planned |= {str(d.get("source_id")) for d in manifest.get("documents") or []}
    for sid, layer in sorted(landed.items()):
        if layers_sha.get(sid) and layer != layers_sha[sid]:
            raise CfError(f"the run read another rendering of {sid} (layer {layer[:12]}…, the "
                          f"bench pins {layers_sha[sid][:12]}…)",
                          hint="score a run planned from the bench's sourceset pin")
        if renderers_pinned.get(sid) and rendered[sid] != renderers_pinned[sid]:
            raise CfError(f"the run read another rendering of {sid} (renderer {rendered[sid]}, "
                          f"the bench pins {renderers_pinned[sid]})",
                          hint="a renderer change re-keys passages: score a run planned from the "
                               "bench's sourceset pin")
    if manifests_seen:
        missing = sorted(articles - planned)
        if missing:
            raise CfError(f"the inventory did not plan {len(missing)} article(s) of the split: "
                          + ", ".join(missing[:8]),
                          hint="a bench run covers every article of the split it is scored on")
    state: dict[str, str] = {}
    for sid in articles:
        if sid in unresolved:
            state[sid] = "unresolved"
        elif sid in landed:
            state[sid] = "ran"
        else:
            state[sid] = "gate_failed"
    by_key: dict[tuple[str, str], dict] = {}
    for row in rows:
        sid = str(row.get("source_id"))
        if sid in articles:
            by_key[(sid, cf_items.join_key(str(row.get("item"))))] = row
    uncovered = sorted({sid for (sid, key), p in frozen.items()
                        if sid in articles and state.get(sid) == "ran"
                        and p.get("scope") in vocab.LOCATED_SCOPES and (sid, key) not in by_key})
    if uncovered:
        raise CfError(f"the locate run has no outcome for the formal_math passages of "
                      f"{len(uncovered)} article(s) of the split: " + ", ".join(uncovered[:8]),
                      hint="it never planned them (a --source or shard run: see its run.yaml "
                           "locate.source_ids); score it on a split it covers")
    receipts = {}
    for rid in runs["locate"]:
        for _rel, doc in inputs.locate_documents(paths, rid):
            for p in doc.get("passages") or []:
                receipts[(str(doc.get("source_id")), cf_items.join_key(str(p.get("item"))))] = \
                    p.get("receipt")
    out = []
    for key, row in sorted(by_key.items()):
        base = frozen.get(key, {})
        span = row.get("span") or base.get("span")
        out.append(metrics.RunPassage(
            source_id=key[0], item=str(row.get("item")), span=tuple(span) if span else None,
            scope=row.get("scope") or base.get("scope"),
            passage_kind=row.get("passage_kind") or base.get("passage_kind"),
            prominence=row.get("prominence") or base.get("prominence"),
            outcome=row.get("outcome"), status=row.get("status"),
            locations=list(row.get("locations") or []), receipt=receipts.get(key)))
    owed = {"located": 0, "absent": 0, "unresolved": 0}
    pending = 0
    for row in by_key.values():
        if row.get("outcome") in owed and row.get("challenge") is None:
            owed[row["outcome"]] += 1
        pending += row.get("challenge") == vocab.CHALLENGE_PENDING
    coverage = {"basis": ("inventory task manifests" if manifests_seen
                          else "assumed: the inventory has no task manifest"),
                "inventory_planned": len(planned) if manifests_seen else None,
                "outcome_rows": len(by_key),
                "missing_rows": sum(1 for r in by_key.values() if r.get("missing")),
                "challenge_owed": {**owed, "total": sum(owed.values())},
                # a passage a prominence overturn left unjudged under its new rule
                "challenge_pending": pending}
    return out, state, coverage, sorted(set(rendered.values()))


# --------------------------------------------------------------------------
# cost
# --------------------------------------------------------------------------

def _sources_of(paths, rid: str, y: dict) -> list[str]:
    ids = _locate_block(y).get("source_ids")
    if isinstance(ids, list) and ids:
        return [str(s) for s in ids]
    planned = {str(d.get("source_id")) for m in inputs.task_manifests(paths, rid)
               for d in m.get("documents") or []}
    return sorted(planned)


def article_costs(paths, path: Path | None, runs: dict[str, list[str]], yamls: dict,
                  receipts: dict, articles: list[str], cluster_of: dict[str, str]
                  ) -> tuple[metrics.Ratio, metrics.Ratio, dict]:
    """Dollars per article: ``(every stage, locate + challenge, the cost block)``.

    Each run's receipt prices the whole run, and each run covers its own sources
    (an inventory is usually shared by every run of the bench), so a run's
    dollars are spread over ``run.yaml`` ``locate.source_ids`` (else its planned
    sources) and an article is charged the share of every run that covered it.
    ``--article-costs`` (``{source id: dollars}`` over every session of each
    article's task) replaces the spread for both figures.
    """
    total, locate_only = metrics.Ratio(), metrics.Ratio()
    stage_of = {rid: stage for stage, ids in runs.items() for rid in ids}
    by_stage = {stage: 0.0 for stage in STAGES}
    if path is not None:
        per = read_json(path)
        if not isinstance(per, dict):
            raise CfError(f"{path} is not a mapping of source id to dollars")
        missing = [a for a in articles if a not in per]
        if missing:
            raise CfError(f"{path} has no cost for {len(missing)} article(s): " + ", ".join(missing[:5]))
        for a in articles:
            usd = dollars(per[a], f"{path}: the cost of {a}")
            total.add(cluster_of.get(a, "?"), usd, 1.0)
            locate_only.add(cluster_of.get(a, "?"), usd, 1.0)
        basis = "per-article costs (one figure for every stage)"
        by_stage = None
    else:
        charges = {a: {stage: 0.0 for stage in STAGES} for a in articles}
        shares = {}
        for rid, receipt in sorted(receipts.items()):
            covered = _sources_of(paths, rid, yamls[rid]) or list(articles)
            usd = dollars(receipt["cost"].get("usd"), f"run {rid}'s receipt cost.usd")
            share = usd / len(covered)
            shares[rid] = {"usd": usd, "sources": len(covered), "share": round(share, 6)}
            for a in set(covered) & set(articles):
                charges[a][stage_of.get(rid, "locate")] += share
        for a in articles:
            cluster = cluster_of.get(a, "?")
            total.add(cluster, sum(charges[a].values()), 1.0)
            locate_only.add(cluster, charges[a]["locate"] + charges[a]["challenge"], 1.0)
            for stage in STAGES:
                by_stage[stage] += charges[a][stage]
        by_stage = {s: round(v / len(articles), 6) if articles else None for s, v in by_stage.items()}
        basis = "each run's receipt spread over the sources it covered"
    info = {"usd_total": round(sum(float(r["cost"]["usd"]) for r in receipts.values()), 6),
            "articles": len(articles), "basis": basis, "by_stage": by_stage}
    if path is None:
        info["runs"] = shares
    return total, locate_only, info


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------

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


def _strata(result: metrics.TierResult, clusters: list[str], replicates: int, seed: str) -> dict:
    """``{stratum: {metric: bootstrap + by_cluster}}`` for every metric the tier stratified."""
    return {key: {name: {**metrics.bootstrap(r.by_cluster, clusters, replicates=replicates,
                                             seed=f"{seed}:{key}:{name}"),
                         "by_cluster": r.by_cluster}
                  for name, r in sorted(group.items())}
            for key, group in sorted(result.strata.items())}


def _gate_rejections(paths, all_runs: list[str], explicit: Path | None) -> dict:
    """Per run: the harvested ``runs/<run>/host/gate.jsonl`` (or ``--gate-log``), filtered to it."""
    out: dict[str, Any] = {}
    lines = inputs.gate_log(explicit) if explicit is not None else None
    for rid in all_runs:
        if lines is not None:
            out[rid] = metrics.gate_rejections(lines, [rid])
            continue
        path = inputs.run_host_dir(paths, rid) / inputs.HOST_GATE_LOG
        out[rid] = (metrics.gate_rejections(inputs.gate_log(path), [rid]) if path.is_file()
                    else None)
    return out


def score(paths, layout: BenchLayout, bench: dict, *, run_id: str, split: str,
          arm: str | None = None, gold_version: int | None = None, gate_log: Path | None = None,
          transcripts: Path | None = None, canary_report: Path | None = None,
          no_canary_check: bool = False, article_costs_path: Path | None = None,
          audit_labels: Path | None = None, replicates: int = DEFAULT_REPLICATES,
          seed: int = 0, pin_overrides: dict[str, str] | None = None,
          checkpoint: bool = False) -> dict:
    vocab.check(split, vocab.BENCH_SPLITS, "split")
    if split == "test" and not checkpoint:
        raise CfError("the test split is scored only at checkpoints",
                      hint="pass --checkpoint when this is one (section 9, step 4)")
    version, manifest = gold_mod.latest_manifest(layout, gold_version)
    gold_mod.verify_frozen(layout, manifest, canary=bench["canary"])
    articles_in = sorted((manifest.get("splits") or {}).get(split) or [])
    if not articles_in:
        raise CfError(f"bench {layout.bench_id} has no {split} article")
    locate_yaml = inputs.run_yaml(paths, run_id)
    locate = _locate_block(locate_yaml)
    if locate.get("mode") != vocab.LOCATE:
        raise CfError(f"run {run_id} is a {locate.get('mode')!r} run; a score is of a locate run")
    if locate.get("subject_only"):
        raise CfError(f"run {run_id} is an article-tier (subject-only) run, which is not scored "
                      "passage by passage",
                      hint="it located one subject passage per article; score a passage-level run")
    run_universe = check_universe(locate, manifest)
    rows = inputs.outcomes(paths, run_id)
    runs = runs_of(paths, run_id, rows, locate)
    all_runs = sorted({r for v in runs.values() for r in v})
    yamls, receipts, receipt_shas = load_runs(paths, all_runs)
    run_arm = check_arms(yamls, run_id)
    if arm is not None and arm != run_arm:
        raise CfError(f"run {run_id} is the {run_arm} arm, not {arm}",
                      hint="the two G1 arms are never pooled: score each run as the arm it ran")
    arm = run_arm
    tools = tools_db(run_universe)
    # the planted tier as the frozen manifest records it (verify_frozen checked its files)
    p_tier = (manifest.get("tiers") or {}).get("P")
    planted = tools["kind"] == TOOLS_PLANTED
    if planted:
        if not p_tier:
            raise CfError(f"run {run_id} searched a planted database, but bench "
                          f"{layout.bench_id}'s gold has no planted tier",
                          hint=f"cf bench freeze {layout.bench_id} --plant (--supersede) writes one")
        if tools["sha256"] != p_tier.get("corpus_sha256"):
            raise CfError(f"run {run_id}'s tools database is not this bench's planted one "
                          f"({tools['sha256'][:12]}… vs {str(p_tier.get('corpus_sha256'))[:12]}…)",
                          hint="plan the P run with this bench's gold/planted/corpus.sqlite")
        if split != "eval":
            raise CfError("a planted run is scored on eval, the split its ablation removed")
    elif p_tier and tools["sha256"] == p_tier.get("corpus_sha256"):
        raise CfError(f"run {run_id} searched this bench's planted database but records its "
                      f"tools database as {tools['kind']!r}",
                      hint="its run.yaml locate.universe.tools_corpus_db.kind must say planted")
    ablation = read_json(layout.ablation_json) if planted else None
    canary = canary_check(paths, bench["canary"], all_runs, transcripts=transcripts,
                          report=canary_report, skip=no_canary_check)
    frame = {a["source_id"]: a for a in read_json(layout.frame_json)["articles"]}
    layers_sha = {sid: frame[sid].get("layer_sha256") for sid in articles_in}
    run, state, coverage, renderers = run_passages(
        paths, rows, runs, set(articles_in), layers_sha,
        {sid: frame[sid].get("renderer") for sid in articles_in})
    pin_doc = pins(paths, yamls=yamls, receipts=receipts, receipt_shas=receipt_shas,
                   locate_run=run_id, renderers=renderers or [UNRECORDED],
                   manifest=manifest, tools=tools, challenge_runs=runs["challenge"],
                   overrides=pin_overrides or {})
    mid = measurement_id(pin_doc)
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
    if planted:
        ablated = {tuple(r.split(":", 1)) for r in ablation.get("removed") or []}
        info = (manifest.get("tiers") or {}).get("G1")
        g1_rows = ([r for r in read_jsonl_rows(layout.root / info["file"]) if r.get("source_id") in layers]
                   if info else [])
        p_gold = [g for g in metrics.gold_passages("G1", g1_rows) if g.accept & ablated]
        p_result = metrics.evaluate_tier("P", p_gold, run, layers=layers, cluster_of=cluster_of,
                                         article_state=state, assessment=assessment, ablated=ablated)
        citations = sum(1 for p in run for loc in p.locations
                        if (str(loc.get("library")), str(loc.get("declaration"))) in ablated)
        tiers["P"] = {"gold": p_result.gold, "joined": p_result.joined,
                      "metrics": metrics.summarise(p_result.ratios, clusters, replicates=replicates,
                                                   seed=f"{seed}:P"),
                      "memory_citations": citations,
                      "ablation_sha256": p_tier.get("ablation_sha256"),
                      "note": "a planted run is scored on P alone: its workers searched an index "
                              "without the eval G1 modules"}
        per_cluster["P"] = {name: r.by_cluster for name, r in p_result.ratios.items()
                            if not name.startswith("fnf_cause:")}
        strata_out["P"] = _strata(p_result, clusters, replicates, f"{seed}:P")
        positives["P"] = len(p_gold)
        all_misses.extend(p_result.misses)
    for tier in (() if planted else vocab.BENCH_LABELLED_TIERS):
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
        strata_out[tier] = _strata(result, clusters, replicates, f"{seed}:{tier}")
        all_misses.extend(result.misses)
    cost_total, cost_locate, cost_info = article_costs(paths, article_costs_path, runs, yamls,
                                                       receipts, articles_in, cluster_of)
    cost_info.update(metrics.bootstrap(cost_total.by_cluster, clusters, replicates=replicates,
                                       seed=f"{seed}:cost"))
    cost_info["locate_and_challenge"] = metrics.bootstrap(
        cost_locate.by_cluster, clusters, replicates=replicates, seed=f"{seed}:cost:locate")
    per_cluster["cost"] = {"usd_per_article": cost_total.by_cluster,
                           "usd_per_article_locate": cost_locate.by_cluster}
    gate_rej = _gate_rejections(paths, all_runs, gate_log)
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
        "measurement_id": mid, "pins": pin_doc, "scorer": scorer(),
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
        "misses": sorted(all_misses, key=lambda m: (m["tier"], m["gold_id"] or "", m["kind"],
                                                     str(m.get("run_item") or ""),
                                                     str(m.get("declaration") or ""))),
        "audit": audit_summary,
        "bootstrap": {"replicates": replicates, "seed": seed, "unit": "article cluster",
                      "interval": "95% percentile, nearest rank"},
        "per_cluster": per_cluster,
        "created": now(),
    }
    write_json(layout.score_json(score_id), doc, sort_keys=True)
    return doc
