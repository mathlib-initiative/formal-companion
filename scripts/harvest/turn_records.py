"""What an unaudited locate run's turn records say about the corpus that landed.

New code, for references/locate-documents.md section 10 (what a harvest makes of a
run) and docs/design/stage_map.md's L6 row.  The bridge of a locate run keeps, in its
tend root, one contract per agent call (``scripts/run/review_record.py``:
``contracts/<task>.<role>.<stamp>.json``, each worker's carrying the git blob id of
each of its task's documents at HEAD), a ledger of every review of a contribution the
worker marked ``needs_review`` (``needs-review.jsonl``) and a ledger of every reviewer
call whose change the tamper guard undid (``tamper.jsonl``).  ``cf harvest`` fetches
them to ``runs/<run>/host/`` (``harvest.host_artifacts``) and this module reads them
beside the corpus, for four receipt blocks:

* ``needs_review``: ``{count, tasks, merged, entries}`` -- every task a worker marked
  ``needs_review`` (the ledger's tasks, and the tasks whose last worker contract says
  so), ``merged`` the tasks whose last worker contract, the contribution that merged,
  says so, and ``entries`` the ledger's lines.  Smoke 4's default: a ``needs_review``
  merges as before (tend 0.1.0 merges it like ``completed``), and is listed here and in
  ``cf harvest``'s output for a person to read before the run's tally is used.  It
  never makes the receipt incomplete.
* ``worker_documents``: ``{checked, matched, unrecorded}`` and ``merged_not_worker``:
  each landed ``<task>/<source>.json`` compared, by its git blob id, with the blob the
  task's last worker contract recorded for ``findings/<task>/<source>.json``.  A
  document that differs is ``{task_id, path, merged_blob, worker_blob}`` in
  ``merged_not_worker`` and makes the receipt **INCOMPLETE**: what merged is not what
  the worker committed (a reviewer's commit, say).  A task with no worker contract is
  ``unrecorded``, reported and never refusing.  ``checked`` is false when no contracts
  are here at all.
* ``reviewer_tampered``: ``{count, tasks, entries}``, the tamper ledger's lines: each
  was undone before the merge, so it is reported, never refusing.

A run with none of the three records (an audited run, a run planned before the bridge
kept them, a faithfulness run) gets no blocks, and its receipt is what it was.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

#: the receipt keys
NEEDS_REVIEW_KEY = "needs_review"
WORKER_DOCUMENTS_KEY = "worker_documents"
MERGED_NOT_WORKER_KEY = "merged_not_worker"
TAMPERED_KEY = "reviewer_tampered"
#: the local names in ``runs/<run>/host/`` (``harvest.host_artifacts``)
CONTRACTS = "contracts"
NEEDS_REVIEW_LEDGER = "needs-review.jsonl"
TAMPER_LEDGER = "tamper.jsonl"
NEEDS_REVIEW = "needs_review"
MAX_NAMED = 20


def git_blob_id(data: bytes, *, length: int = 40) -> str:
    """The id git gives a blob of ``data``: sha1 (40 hex), or sha256 for a 64-hex repo."""
    algo = hashlib.sha256 if length == 64 else hashlib.sha1
    return algo(b"blob %d\0" % len(data) + data).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def last_workers(contracts_dir: Path) -> dict[str, dict]:
    """``{task_id: its last worker contract}``, by the contracts' names (their stamps sort)."""
    out: dict[str, dict] = {}
    if not contracts_dir.is_dir() or contracts_dir.is_symlink():
        return out
    for path in sorted(contracts_dir.glob("*.worker.*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("role") == "worker" and data.get("task_id"):
            out[str(data["task_id"])] = data
    return out


def landed(corpus_dir: Path) -> dict[str, dict[str, Path]]:
    """``{task_id: {findings/<task>/<name>.json: landed path}}`` of a locate corpus."""
    out: dict[str, dict[str, Path]] = {}
    for path in sorted(Path(corpus_dir).glob("*/*.json")):
        if path.is_file() and not path.is_symlink():
            task = path.parent.name
            out.setdefault(task, {})[f"findings/{task}/{path.name}"] = path
    return out


def needs_review_block(ledger: list[dict], workers: dict[str, dict]) -> dict:
    merged = sorted(t for t, c in workers.items() if c.get("status") == NEEDS_REVIEW)
    tasks = sorted({str(r.get("task_id")) for r in ledger if r.get("task_id")} | set(merged))
    entries = [{k: row.get(k) for k in ("task_id", "worktree_id", "worker_head",
                                         "worker_summary", "reviewer_verdict",
                                         "reviewer_notes", "at")} for row in ledger]
    return {"count": len(tasks), "tasks": tasks, "merged": merged, "entries": entries}


def worker_documents(corpus_dir: Path, workers: dict[str, dict], checked: bool) -> tuple[dict, list]:
    """``(worker_documents block, merged_not_worker rows)`` for the landed corpus."""
    matched, unrecorded, wrong = 0, [], []
    if checked:
        for task, docs in sorted(landed(corpus_dir).items()):
            contract = workers.get(task)
            recorded = (contract or {}).get("documents") if contract else None
            if not isinstance(recorded, dict):
                unrecorded.append(task)
                continue
            for rel, path in sorted(docs.items()):
                want = str(recorded.get(rel) or "")
                have = git_blob_id(path.read_bytes(), length=len(want) or 40)
                if want == have:
                    matched += 1
                else:
                    wrong.append({"task_id": task, "path": rel, "merged_blob": have,
                                  "worker_blob": want or None})
    return {"checked": bool(checked), "matched": matched, "unrecorded": unrecorded}, wrong


def summarise(corpus_dir: Path, host_dir: Path) -> dict[str, Any] | None:
    """The receipt's turn-record blocks (the module docstring), or None with no records."""
    host_dir = Path(host_dir)
    contracts_dir = host_dir / CONTRACTS
    ledger_path, tamper_path = host_dir / NEEDS_REVIEW_LEDGER, host_dir / TAMPER_LEDGER
    have_contracts = contracts_dir.is_dir() and not contracts_dir.is_symlink()
    if not (have_contracts or ledger_path.is_file() or tamper_path.is_file()):
        return None
    workers = last_workers(contracts_dir) if have_contracts else {}
    documents, wrong = worker_documents(corpus_dir, workers, have_contracts)
    tampered = _jsonl(tamper_path)
    return {
        NEEDS_REVIEW_KEY: needs_review_block(_jsonl(ledger_path), workers),
        WORKER_DOCUMENTS_KEY: documents,
        MERGED_NOT_WORKER_KEY: wrong,
        TAMPERED_KEY: {"count": len(tampered),
                       "tasks": sorted({str(r.get("task_id")) for r in tampered
                                        if r.get("task_id")}),
                       "entries": [{k: r.get(k) for k in ("task_id", "worktree_id", "head",
                                                         "changed", "at")} for r in tampered]},
    }


def describe(receipt: dict) -> list[str]:
    """The harvest summary's lines for the four blocks (none when the receipt has none)."""
    lines: list[str] = []
    needs = receipt.get(NEEDS_REVIEW_KEY)
    if isinstance(needs, dict):
        if needs.get("count"):
            merged = set(needs.get("merged") or [])
            named = ", ".join(t + (" (merged so)" if t in merged else "")
                              for t in (needs.get("tasks") or [])[:MAX_NAMED])
            lines.append(f"  NEEDS REVIEW: {needs['count']} task(s) a worker marked needs_review"
                         f" -- {named}; each merged as before and is a person's to read")
        else:
            lines.append("  needs_review: none")
    docs = receipt.get(WORKER_DOCUMENTS_KEY)
    if isinstance(docs, dict):
        if not docs.get("checked"):
            lines.append("  worker documents: not checked (no worker contracts here)")
        else:
            lines.append(f"  worker documents: {docs.get('matched', 0)} merged document(s) are "
                         f"their worker's commit"
                         + (f"; {len(docs.get('unrecorded') or [])} task(s) left no worker "
                            f"contract" if docs.get("unrecorded") else ""))
    for row in (receipt.get(MERGED_NOT_WORKER_KEY) or [])[:MAX_NAMED]:
        lines.append(f"  MERGED NOT THE WORKER'S: {row['path']} is blob "
                     f"{str(row['merged_blob'])[:12]}, its worker committed "
                     f"{str(row.get('worker_blob') or 'none')[:12]}")
    tampered = receipt.get(TAMPERED_KEY)
    if isinstance(tampered, dict) and tampered.get("count"):
        lines.append(f"  reviewer_tampered: {tampered['count']} reviewer call(s) changed the "
                     f"worktree and were undone before the merge -- "
                     + ", ".join(tampered.get("tasks") or []))
    return lines
