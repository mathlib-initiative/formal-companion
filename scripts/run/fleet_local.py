"""Build the args for the local Workflow fleet (the <100-item fallback).

Ported from the queue builder in scratchpad/recovered/b174aa13_L5589_Bash.txt
(``queue_args.json``: item, repos, pdf_pages) and the fleet script it fed,
recovered/b174aa13_L5600_Workflow.txt.

The local path exists because the server path needs ssh, an agentbox profile and
the box owner's consent.  It is a FALLBACK, and the record says why: the local
Workflow tool is capped at min(16, CPUs-2) concurrency and dies on the
subscription session limit mid-run (129 of 225 agents in batch 1; 70 of 114
elsewhere).  So this refuses to build a fleet larger than --max-items (100 by
default) unless forced.

Two things a Workflow script cannot do, which the caller must therefore do
(design/critique.json, corrections): it has no filesystem or Node API, so the
quote gate CANNOT run inside the script after each agent -- the worker must
self-gate in its prompt, and the run is reconciled afterwards with
``harvest_journal.py`` -> ``verify_findings.py`` -> a second Workflow run for
whatever failed.  And the script must take every input through ``args``: a
script that computes anything from ``Date.now()`` cannot be resumed.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # `python3 scripts/run/fleet_local.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run._compat import CfError, read_yaml, write_json

DEFAULT_MAX_ITEMS = 100


def task_args(run_dir: Path) -> list[dict]:
    """One arg object per task file: ``{id, item, title, prompt}``."""
    tasks_dir = Path(run_dir) / "tasks"
    if not tasks_dir.is_dir():
        raise CfError(f"no tasks directory at {tasks_dir}", hint="run `cf plan-run` first")
    out: list[dict] = []
    for path in sorted(tasks_dir.iterdir()):
        if path.suffix not in (".yaml", ".yml"):
            continue
        data = read_yaml(path)
        if not isinstance(data, dict):
            continue
        title = str(data.get("title") or path.stem)
        item = data.get("item") or title.split(":", 1)[-1].strip()
        out.append({
            "id": str(data.get("id") or path.stem),
            "item": str(item),
            "title": title,
            "priority": data.get("priority"),
            "prompt": str(data.get("description") or ""),
        })
    return out


def build(run_dir: Path, run_id: str, context: dict,
          max_items: int = DEFAULT_MAX_ITEMS, force: bool = False) -> dict:
    """The ``args`` object for workflows/fleet-local.workflow.js."""
    items = task_args(run_dir)
    if len(items) > max_items and not force:
        raise CfError(
            f"{len(items)} items is too many for the local Workflow fleet (limit {max_items})",
            hint="run it on the server with `cf run`, or pass --force and expect to resume "
                 "after the session limit (batch 1 died at 129 of 225 agents)",
        )
    return {
        "run_id": run_id,
        "context": context,
        "model": "opus",           # never Fable for subagents (2026-09-05 budget rule)
        "self_gate": True,          # the script cannot run the gate; the worker must
        "items": items,
        "count": len(items),
    }


def write(run_dir: Path, args: dict) -> Path:
    """Write ``fleet-local.args.json`` next to the run's tasks."""
    path = Path(run_dir) / "fleet-local.args.json"
    write_json(path, args, indent=1, sort_keys=False)
    return path


def instructions(args_path: Path, count: int) -> str:
    """What the operator must do next -- cf.py cannot launch a Workflow itself."""
    return "\n".join([
        f"{count} items staged for the local fleet.",
        "",
        "The Workflow tool cannot be launched from cf.py; run it from a Claude Code session:",
        "  script: workflows/fleet-local.workflow.js",
        f"  args:   {args_path}",
        "",
        "  - subagents must be model 'opus' (Fable-default runs lost agents to the session limit)",
        "  - expect to resume: pass resumeFromRunId and edit only the failed agent() opts",
        "  - the script CANNOT run the quote gate; after the run:",
        "      python3 scripts/cf.py harvest <run-id> --journal <workflow-dir>",
        "      python3 scripts/gate/verify_findings.py --corpus corpus/<run-id>",
        "      re-queue whatever failed in a second Workflow run",
    ])
