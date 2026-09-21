"""What a run is doing: tend-control status remotely, the journal locally.

New code; the remote half is the monitor block of the v1 campaign's runbook
("Steer it", now references/runbook-server.md), the local half counts a
Workflow run's ``journal.jsonl`` because a local fleet has no control plane at
all.

The local corpus half is always printed, needs no network, and is the number
that actually matters: how many findings are home and whether the last harvest
receipt said the corpus was whole.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

if __package__ in (None, ""):  # `python3 scripts/run/status.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run._compat import read_json, read_yaml


def remote_steps(host: dict, run_id: str, names: dict) -> list[dict]:
    """The read-only commands that report a server run's progress."""
    tend_root = f"{host['remote_root']}/{names['tend_root']}"
    entrypoint = f"{host['remote_root']}/{names['entrypoint']}"

    def ssh(cmd: str, title: str) -> dict:
        return {"title": title, "argv": ["ssh", host["ssh"], cmd],
                "shell": f"ssh {shlex.quote(host['ssh'])} {shlex.quote(cmd)}"}

    return [
        ssh(f"{host['tend_cmd']} tend-control status --root {shlex.quote(tend_root)}",
            "tend-control status (progress, limits, spend)"),
        ssh(f"ls -1 {shlex.quote(entrypoint + '/findings')} 2>/dev/null | wc -l",
            "findings merged on the entrypoint"),
        ssh(f"tail -n 20 {shlex.quote(tend_root + '/run.log')}", "last 20 log lines"),
        ssh(f"tail -n 5 {shlex.quote(tend_root + '/agentbox-worker.err')} 2>/dev/null || true",
            "bridge stderr (agentbox failures land here, not in tend)"),
    ]


def journal_counts(journal: Path) -> dict:
    """Result/error counts from a local Workflow run's journal.jsonl."""
    from harvest.harvest_journal import read_journal, link_challenges, summarise

    path = Path(journal)
    workflow_dir = path.parent if path.name == "journal.jsonl" else path
    items, rows = read_journal(workflow_dir)
    # `--status` is a read-only progress query, so the unlinked rulings are
    # COUNTED here (challenge_rows_seen / challenges_unlinked / unmatched_labels
    # in the printed line), never refused: the operator asked what the run did,
    # and a status command that exits 2 answers nothing.  The refusal belongs to
    # `cf harvest --journal`, which writes a corpus from the same rows.
    return summarise(items, link_challenges(items, rows, strict=False), rows)


def corpus_status(paths, run_id: str) -> dict:
    """Findings already harvested for this run, and what the receipt said."""
    corpus = Path(paths.corpus_run_dir(run_id))
    receipt_path = Path(paths.receipt_json(run_id))
    receipt = read_json(receipt_path, default=None) if receipt_path.exists() else None
    files = [p for p in sorted(corpus.glob("*.json")) if p.name != "RECEIPT.json"] if corpus.is_dir() else []
    return {
        "corpus": str(corpus),
        "files": len(files),
        "receipt": None if not receipt else {
            "harvested_at": receipt.get("harvested_at"),
            "expected": (receipt.get("expected") or {}).get("count"),
            "actual": (receipt.get("actual") or {}).get("count"),
            "complete": receipt.get("complete"),
            "missing": len(receipt.get("missing") or []),
        },
    }


def planned_status(paths, run_id: str) -> dict:
    """What run.yaml says was planned, so status can report progress as a fraction."""
    run_yaml = Path(paths.run_yaml(run_id))
    if not run_yaml.exists():
        return {}
    data = read_yaml(run_yaml)
    if not isinstance(data, dict):
        return {}
    tasks = data.get("tasks")
    if isinstance(tasks, dict):
        tasks = tasks.get("count")
    block = data.get("run") if isinstance(data.get("run"), dict) else {}
    planned_host = data.get("host") if isinstance(data.get("host"), dict) else {}
    return {
        "mode": data.get("preset") or data.get("mode"),
        "tasks": tasks,
        "host": block.get("host") or planned_host.get("name"),
        "planned_at": block.get("planned_at"),
        "executed_stages": [s.get("stage") for s in (block.get("executed") or [])],
        "smoke_verified": block.get("smoke_verified"),
    }


def render(run_id: str, planned: dict, corpus: dict, journal: dict | None) -> str:
    """One-screen local summary (the remote commands are printed separately)."""
    lines = [f"run {run_id}"]
    if planned:
        lines.append("  planned: " + ", ".join(f"{k}={v}" for k, v in planned.items() if v not in (None, [])))
    done, total = corpus["files"], planned.get("tasks")
    frac = f" of {total}" if total else ""
    lines.append(f"  corpus:  {done}{frac} findings at {corpus['corpus']}")
    receipt = corpus["receipt"]
    if receipt:
        lines.append(f"  receipt: {receipt['actual']}/{receipt['expected']} "
                     + ("COMPLETE" if receipt["complete"] else f"INCOMPLETE ({receipt['missing']} missing)")
                     + f" at {receipt['harvested_at']}")
    else:
        lines.append("  receipt: none yet (run `cf harvest`)")
    if journal:
        lines.append("  journal: " + ", ".join(f"{k}={v}" for k, v in journal.items()))
    return "\n".join(lines)
