"""The ``audit`` block of an audited inventory run's RECEIPT.json.

New code, for references/locate-documents.md section 13.10 (what a harvest makes of
an audited run) and docs/design/stage_map.md's L6 row.  A run planned with
``cf plan-locate --audit-rounds`` (``run.yaml`` ``locate.audit``) reviews each
inventory document in rounds inside its tend run (section 13.9), and keeps every
round's record in ``<tend-root>/audit/<task-id>/``: the turn machine's
``state.json`` and, under ``<source-id>/``, each round's ``r<k>.verdicts.json``.
``cf harvest`` fetches that directory to ``runs/<run>/host/audit/``
(``harvest.host_artifacts``) and this module reads it beside the corpus that
landed, so the receipt says, for every harvested document, whether the audit
approved exactly those bytes:

* ``outcomes``: each document's :data:`cflib.vocab.AUDIT_OUTCOMES`, judged by its
  sha256 against the state's ``approved`` (the audit's approval), then its ``best``
  (the audited cap's restore); ``stale`` names a document the audit saw and did not
  approve as harvested, ``unaudited`` one the audit directory holds nothing of;
* ``rounds``: each round's verdicts files summed -- the decisions, the merge's
  ``counts`` (flagged, upheld, refuted, missed, the check statuses, collateral,
  regressions, the kind rule), ``by_verdict`` and the harm;
* ``residual``: the verdicts of the round that reviewed the harvested bytes (the
  last round, or the snapshot a revert or the cap restored), per formal passage,
  with the formal passages of kind ``other`` before (round 1) and after;
* ``turns``: the turn machine's counts summed over the tasks (rejections, precheck
  refusals, retries, audit_failed, reverts, gate resets, tampering);
* ``cost_usd``: the envelopes' dollars by step (``cost.fleet.envelope_by_step``),
  named by :data:`cflib.vocab.AUDIT_COST_STEPS`.

A run whose ``run.yaml`` names no audit gets no block, so its receipt is what it
was.  Nothing here judges a cut: the counts are the merge's, read back.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from cflib import vocab

#: the receipt key
AUDIT_KEY = "audit"
#: a round's verdicts file, ``<task>/<source>/r<k>.verdicts.json``
_VERDICTS = re.compile(r"r([0-9]+)\.verdicts\.json\Z")
#: the turn machine's state file in each task's directory
STATE = "state.json"
#: where the block's ``audit_dir`` came from: this harvest's fetch, or a copy already here
SOURCE_FETCHED = "fetched"
SOURCE_KEPT = "kept"


def plan_of(run_doc: Mapping[str, Any] | None) -> dict | None:
    """``{rule, review_rounds}`` of an audited run's ``run.yaml`` (``locate.audit``), else None."""
    block = run_doc.get("locate") if isinstance(run_doc, Mapping) else None
    audit = block.get("audit") if isinstance(block, Mapping) else None
    if not isinstance(audit, Mapping) or audit.get("rule") in (None, ""):
        return None
    return {"rule": audit.get("rule"), "review_rounds": audit.get("review_rounds")}


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def harvested_documents(corpus_dir: Path) -> dict[str, dict]:
    """``{source_id: {task_id, sha256, path}}`` of the documents a locate corpus landed.

    A locate corpus is ``<task-id>/<source-id>.json`` (section 10); the sha256 is of
    the file's bytes, which the harvest copied byte for byte from the merged branch.
    """
    out: dict[str, dict] = {}
    for path in sorted(Path(corpus_dir).glob("*/*.json")):
        if not path.is_file() or path.is_symlink():
            continue
        data = _load(path)
        if not isinstance(data, dict):
            continue
        sid = str(data.get("source_id") or path.stem)
        out[sid] = {"task_id": path.parent.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "path": f"{path.parent.name}/{path.name}"}
    return out


def read_audit_dir(audit_dir: Path | None) -> tuple[dict[str, dict], list[str]]:
    """``({task_id: {state, sources: {source_id: {k: verdicts}}}}, unreadable)``.

    ``discarded/`` (a round whose document changed under it) and ``raw/`` (the
    envelopes) are not a round's record and are not read.
    """
    tasks: dict[str, dict] = {}
    unreadable: list[str] = []
    if audit_dir is None or not Path(audit_dir).is_dir():
        return tasks, unreadable
    root = Path(audit_dir)
    for task_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        entry = {"state": None, "sources": {}}
        state_path = task_dir / STATE
        if state_path.is_file():
            state = _load(state_path)
            if isinstance(state, dict) and state.get("schema") == vocab.AUDIT_TURN_SCHEMA:
                entry["state"] = state
            else:
                unreadable.append(state_path.relative_to(root).as_posix())
        for source_dir in sorted(p for p in task_dir.iterdir()
                                 if p.is_dir() and not p.is_symlink()):
            rounds = {}
            for path in sorted(source_dir.iterdir()):
                match = _VERDICTS.match(path.name)
                if not match or not path.is_file():
                    continue
                data = _load(path)
                if isinstance(data, dict) and data.get("kind") == "verdicts":
                    rounds[int(match.group(1))] = data
                else:
                    unreadable.append(path.relative_to(root).as_posix())
            if rounds:
                entry["sources"][source_dir.name] = dict(sorted(rounds.items()))
        tasks[task_dir.name] = entry
    return tasks, unreadable


def _outcome(sha: str, state: dict | None, rounds: dict[int, dict]) -> str:
    """The document's :data:`vocab.AUDIT_OUTCOMES` from its sha256, the state and its rounds."""
    if state is None and not rounds:
        return "unaudited"
    approved = (state or {}).get("approved") if isinstance((state or {}).get("approved"),
                                                          dict) else None
    if approved and approved.get("sha256") == sha:
        why = str(approved.get("why") or "")
        if why.startswith("not audited"):
            return "not_audited"
        if why.startswith("reverted to round"):
            return "reverted"
        if why.startswith("audit_failed"):
            return "audit_failed"
        return "approved"
    best = (state or {}).get("best") if isinstance((state or {}).get("best"), dict) else None
    if best and best.get("sha256") == sha:
        return "capped"
    if state is None and rounds:
        last = rounds[max(rounds)]
        if last.get("document_sha256") == sha and last.get("decision") == "approve":
            return "audit_failed" if last.get("audit_failed") else "approved"
    return "stale"


def _reviewed_round(sha: str, rounds: dict[int, dict]) -> int | None:
    """The latest round whose verdicts reviewed exactly these bytes."""
    hits = [k for k, v in rounds.items() if v.get("document_sha256") == sha]
    return max(hits) if hits else None


def _add_counts(into: dict, counts: Mapping[str, Any]) -> None:
    for key, value in counts.items():
        if key == "by_verdict" and isinstance(value, Mapping):
            merged = into.setdefault("by_verdict", {})
            for verdict, n in value.items():
                if _int(n):
                    merged[verdict] = merged.get(verdict, 0) + n
        elif _int(value):
            into[key] = into.get(key, 0) + value


def _by_verdict(verdicts: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in verdicts:
        if isinstance(v, Mapping) and v.get("verdict"):
            out[str(v["verdict"])] = out.get(str(v["verdict"]), 0) + 1
    return dict(sorted(out.items()))


def _round_rows(per_round: dict[int, list[dict]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for k in sorted(per_round):
        row: dict[str, Any] = {"documents": 0, "decisions": {d: 0 for d in vocab.AUDIT_DECISIONS},
                               "audit_failed": 0, "harm": 0, "counts": {}}
        for verdicts in per_round[k]:
            row["documents"] += 1
            decision = str(verdicts.get("decision") or "")
            if decision in row["decisions"]:
                row["decisions"][decision] += 1
            row["audit_failed"] += bool(verdicts.get("audit_failed"))
            row["harm"] += bool(verdicts.get("harm"))
            if isinstance(verdicts.get("counts"), Mapping):
                _add_counts(row["counts"], verdicts["counts"])
        row["counts"] = dict(sorted(row["counts"].items()))
        if "by_verdict" in row["counts"]:
            row["counts"]["by_verdict"] = dict(sorted(row["counts"]["by_verdict"].items()))
        row["rejected"] = row["decisions"]["request_changes"]
        out[str(k)] = row
    return out


def _cost(envelopes: Path | None) -> tuple[dict | None, dict | None, int]:
    """``(cost_usd, cost_calls, unpriced_calls)`` by :data:`vocab.AUDIT_COST_STEPS`."""
    if envelopes is None or not Path(envelopes).exists():
        return None, None, 0
    from cost import fleet  # noqa: PLC0415 - cost.fleet imports the harvest package

    by_step = fleet.envelope_by_step(envelopes)
    usd, calls = {}, {}
    for name, key in vocab.AUDIT_COST_STEPS.items():
        row = by_step.get(key) or {}
        usd[name] = round(float(row.get("usd") or 0.0), 6)
        calls[name] = int(row.get("envelopes") or 0)
    for key, row in by_step.items():             # anything else: an unstepped reviewer call
        if key not in vocab.AUDIT_COST_STEPS.values():
            usd[key] = round(float(row.get("usd") or 0.0), 6)
            calls[key] = int(row.get("envelopes") or 0)
    unpriced = sum(int(row.get("unpriced_calls") or 0) for row in by_step.values())
    return usd, calls, unpriced


def summarise(corpus_dir: Path, plan: Mapping[str, Any], *, audit_dir: Path | None,
              source: str | None, envelopes: Path | None = None) -> dict:
    """The receipt's ``audit`` block for the corpus at *corpus_dir*.

    *audit_dir* is the fetched ``<tend-root>/audit/`` (None when none is here: every
    document is then ``unaudited``), *source* where it came from (:data:`SOURCE_FETCHED`
    or :data:`SOURCE_KEPT`, None without one), *envelopes* the fetched ``usage/``.
    """
    docs = harvested_documents(Path(corpus_dir))
    tasks, unreadable = read_audit_dir(audit_dir)
    by_source: dict[str, tuple[str, dict | None, dict[int, dict]]] = {}
    for task_id, entry in tasks.items():
        state = entry["state"]
        sources = set(entry["sources"])
        if state and state.get("source_id"):
            sources.add(str(state["source_id"]))
        for sid in sources:
            by_source[sid] = (task_id, state, entry["sources"].get(sid, {}))
    outcomes = {name: 0 for name in vocab.AUDIT_OUTCOMES}
    per_source: dict[str, dict] = {}
    per_round: dict[int, list[dict]] = {}
    residual: dict[str, Any] = {"documents": 0, "actionable": 0, "by_verdict": {}, "formal": 0}
    kind = {"kind_other_formal_before": 0, "kind_other_formal_after": 0,
            "kind_other_in_proof_or_example_before": 0,
            "kind_other_in_proof_or_example_after": 0}
    for sid, doc in sorted(docs.items()):
        task_id, state, rounds = by_source.get(sid, (doc["task_id"], None, {}))
        if task_id != doc["task_id"]:
            state, rounds = None, {}
        outcome = _outcome(doc["sha256"], state, rounds)
        outcomes[outcome] += 1
        reviewed = _reviewed_round(doc["sha256"], rounds)
        for k, verdicts in rounds.items():
            per_round.setdefault(k, []).append(verdicts)
        last = rounds[max(rounds)] if rounds else {}
        row = {"task_id": doc["task_id"], "outcome": outcome, "document_sha256": doc["sha256"],
               "rounds": len(rounds), "final_round": max(rounds) if rounds else None,
               "decision": last.get("decision"), "reviewed_round": reviewed,
               "actionable_by_round": [len(rounds[k].get("verdicts") or [])
                                       for k in sorted(rounds)]}
        if reviewed is not None:
            here = rounds[reviewed]
            verdicts = list(here.get("verdicts") or [])
            counts = here.get("counts") if isinstance(here.get("counts"), Mapping) else {}
            formal = counts.get("formal") if _int(counts.get("formal")) else 0
            row.update(residual=len(verdicts), formal=formal)
            residual["documents"] += 1
            residual["actionable"] += len(verdicts)
            residual["formal"] += formal
            for verdict, n in _by_verdict(verdicts).items():
                residual["by_verdict"][verdict] = residual["by_verdict"].get(verdict, 0) + n
            first = (rounds.get(1) or {}).get("counts")
            if isinstance(first, Mapping):
                for name in ("kind_other_formal", "kind_other_in_proof_or_example"):
                    before, after = first.get(name), counts.get(name)
                    if _int(before) and _int(after):
                        kind[f"{name}_before"] += before
                        kind[f"{name}_after"] += after
        per_source[sid] = row
    residual["by_verdict"] = dict(sorted(residual["by_verdict"].items()))
    residual["per_formal"] = (round(residual["actionable"] / residual["formal"], 4)
                              if residual["formal"] else None)
    residual.update(kind)
    turns: dict[str, int] = {"tasks": 0, "rejections": 0}
    for entry in tasks.values():
        state = entry["state"]
        if not state:
            continue
        turns["tasks"] += 1
        if _int(state.get("rejections")):
            turns["rejections"] += state["rejections"]
        if isinstance(state.get("counts"), Mapping):
            _add_counts(turns, state["counts"])
    harvested_tasks = {d["task_id"] for d in docs.values()}
    cost_usd, cost_calls, unpriced = _cost(envelopes)
    return {
        "rule": plan.get("rule"), "review_rounds": plan.get("review_rounds"),
        "source": source if audit_dir is not None else None,
        "documents": len(docs),
        "audited": sum(1 for row in per_source.values() if row["rounds"]),
        "outcomes": outcomes,
        "stale": sorted(s for s, r in per_source.items() if r["outcome"] == "stale"),
        "unaudited": sorted(s for s, r in per_source.items() if r["outcome"] == "unaudited"),
        "not_harvested": sorted(t for t in tasks if t not in harvested_tasks),
        "unreadable": sorted(unreadable),
        "rounds": _round_rows(per_round),
        "residual": residual,
        "turns": dict(sorted(turns.items())),
        "per_source": per_source,
        "cost_usd": cost_usd,
        "cost_calls": cost_calls,
        "cost_unpriced_calls": unpriced,
    }


def problems(block: Mapping[str, Any] | None) -> list[str]:
    """What the harvest warns about an audit block: no directory, stale, unaudited, unreadable."""
    if not isinstance(block, Mapping):
        return []
    out = []
    if block.get("source") is None:
        out.append("run.yaml names an audit, and no audit directory is here "
                   "(runs/<run>/host/audit/), so no document's audit is known")
    if block.get("stale"):
        out.append(f"{len(block['stale'])} document(s) are not the bytes the audit approved "
                   f"(stale): {', '.join(block['stale'][:10])}")
    if block.get("unaudited") and block.get("source") is not None:
        out.append(f"{len(block['unaudited'])} document(s) have no audit record (unaudited): "
                   f"{', '.join(block['unaudited'][:10])}")
    if block.get("unreadable"):
        out.append(f"{len(block['unreadable'])} audit file(s) could not be read: "
                   f"{', '.join(block['unreadable'][:10])}")
    return out


def describe(block: Mapping[str, Any], indent: str = "  ") -> list[str]:
    """The block as lines for the harvest summary; every number is a key of it."""
    named = ", ".join(f"{k} {n}" for k, n in (block.get("outcomes") or {}).items() if n)
    lines = [f"{indent}audit: rule {block.get('rule')}, {block.get('review_rounds')} review "
             f"round(s); {block.get('audited', 0)} of {block.get('documents', 0)} document(s) "
             f"audited ({named or 'none'}); the audit directory: "
             f"{block.get('source') or 'NONE'}"]
    for k, row in (block.get("rounds") or {}).items():
        decisions = ", ".join(f"{d} {n}" for d, n in (row.get("decisions") or {}).items() if n)
        counts = row.get("counts") or {}
        by = ", ".join(f"{v} {n}" for v, n in (counts.get("by_verdict") or {}).items())
        lines.append(f"{indent}  round {k}: {row.get('documents')} document(s) ({decisions}); "
                     f"{counts.get('actionable', 0)} actionable verdict(s)"
                     + (f" ({by})" if by else "")
                     + (f", {row['harm']} with harm" if row.get("harm") else "")
                     + (f", {row['audit_failed']} audit_failed" if row.get("audit_failed")
                        else ""))
    res = block.get("residual") or {}
    per = res.get("per_formal")
    lines.append(f"{indent}  residual: {res.get('actionable', 0)} actionable verdict(s) over "
                 f"{res.get('formal', 0)} formal passage(s)"
                 + (f" ({per} a passage)" if per is not None else "")
                 + f"; formal passages of kind other {res.get('kind_other_formal_before', 0)} "
                   f"before, {res.get('kind_other_formal_after', 0)} after")
    turns = block.get("turns") or {}
    lines.append(f"{indent}  turns: " + ", ".join(f"{k} {v}" for k, v in turns.items()))
    if block.get("cost_usd"):
        from cost import pricing  # noqa: PLC0415

        lines.append(f"{indent}  cost by step: " + ", ".join(
            f"{k} {pricing.format_usd(v)}" for k, v in block["cost_usd"].items()))
    for line in problems(block):
        lines.append(f"{indent}  AUDIT: {line}")
    return lines
