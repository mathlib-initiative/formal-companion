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


def _item_note(repos: list[str], meta: dict, note_single_source: bool) -> str:
    """The per-item hint the workflow prints as ``ITEM NOTE``."""
    note = str(meta.get("note") or "")
    if note:
        return note
    # An attack run lists only the companion it audits, so a one-entry list
    # there says nothing about whether a peer exists -- do not claim it does.
    if note_single_source and len(repos) == 1:
        return (f"only {repos[0]} formalises this item -- there is nothing to diff against, "
                "so compare the Lean statement directly with the book passage")
    return ""


def item_args(run_dir: Path, item_meta: dict[str, dict] | None = None,
              note_single_source: bool = True) -> list[dict]:
    """The workflow's ``items``: the task rows plus the per-item context it reads.

    ``workflows/fleet-local.workflow.js`` dereferences ``t.item``, ``t.repos``,
    ``t.pdf_pages`` and ``t.note`` on every element, so every one is written
    here even when the value is empty.  ``item_meta`` maps an item label to the
    ``{repos, pdf_pages, note}`` row ``pairs/queue.json`` (differential) or
    ``pairs/targets.<companion>.json`` (attack) carries for it.
    """
    meta = item_meta or {}
    out: list[dict] = []
    for task in task_args(run_dir):
        row = meta.get(task["item"]) or {}
        repos = [str(r) for r in (row.get("repos") or row.get("peers_and_self") or [])]
        out.append({
            **task,
            "item": task["item"],
            "repos": repos,
            "pdf_pages": [int(x) for x in (row.get("pdf_pages") or []) if str(x).isdigit()],
            "note": _item_note(repos, row, note_single_source),
        })
    return out


def workflow_mode(run_mode: str | None, items: list[dict]) -> str:
    """One of the workflow's three MODE_BRIEF keys.

    ``run.yaml`` records ``differential`` or ``attack``; the workflow also has a
    ``single-source`` brief, which is the right one when no item in the run has
    a peer to diff against.
    """
    if str(run_mode or "").startswith("attack"):
        return "attack"
    if items and all(len(i.get("repos") or []) < 2 for i in items):
        return "single-source"
    return "differential"


def build(
    run_dir: Path,
    run_id: str,
    *,
    campaign: dict,
    repos: list[dict],
    mode: str | None,
    gate: dict,
    pairs: str | None = None,
    item_meta: dict[str, dict] | None = None,
    model: str = "opus",
    challenge: bool = True,
    challenge_model: str = "opus",
    max_items: int = DEFAULT_MAX_ITEMS,
    force: bool = False,
) -> dict:
    """The ``args`` object for ``workflows/fleet-local.workflow.js``.

    Every key here is one the shipped script dereferences; the previous shape
    (``{run_id, context, model, self_gate, items, count}``) shared not one key
    with it, so the workflow rendered an empty repository list, an undefined
    book path and — because ``GATE.command`` was absent — no self-gate at all.
    ``tests/test_run_harvest`` asserts the two stay in step.
    """
    attack = str(mode or "").startswith("attack")
    items = item_args(run_dir, item_meta, note_single_source=not attack)
    if len(items) > max_items and not force:
        raise CfError(
            f"{len(items)} items is too many for the local Workflow fleet (limit {max_items})",
            hint="run it on the server with `cf run`, or pass --force and expect to resume "
                 "after the session limit (batch 1 died at 129 of 225 agents)",
        )
    args = {
        "run_id": run_id,
        "campaign": dict(campaign),
        "repos": [dict(r) for r in repos],
        "mode": workflow_mode(mode, items),
        "gate": dict(gate),
        "model": model,                 # never Fable for subagents (2026-09-05 budget rule)
        "challenge": bool(challenge),
        "challenge_model": challenge_model,
        # "" is meaningful: the workflow's `A.pairs ? ... : ''` drops the line
        "pairs": str(pairs or ""),
        "items": items,
        "count": len(items),
    }
    return args


#: every key ``workflows/fleet-local.workflow.js`` reads off ``args``, ``args.campaign``,
#: ``args.gate``, ``args.repos[i]`` and ``args.items[i]`` -- the contract build() must meet
WORKFLOW_KEYS: dict[str, tuple[str, ...]] = {
    "args": ("run_id", "campaign", "repos", "mode", "gate", "model",
             "challenge", "challenge_model", "pairs", "items"),
    "campaign": ("book_id", "book_title", "book_sha", "book_layout_txt",
                 "book_pages_dir", "pdf_page_offset", "errata"),
    "repos": ("name", "repo", "statements", "conventions"),
    "gate": ("command", "kinds", "witness_required", "quote_min_chars", "scratch_dir"),
    "items": ("item", "repos", "pdf_pages", "note"),
}


def missing_keys(args: dict) -> list[str]:
    """Keys the workflow dereferences that ``args`` does not carry (for tests)."""
    out = [f"args.{k}" for k in WORKFLOW_KEYS["args"] if k not in args]
    for section in ("campaign", "gate"):
        block = args.get(section) or {}
        out += [f"args.{section}.{k}" for k in WORKFLOW_KEYS[section] if k not in block]
    for i, repo in enumerate(args.get("repos") or []):
        out += [f"args.repos[{i}].{k}" for k in WORKFLOW_KEYS["repos"] if k not in repo]
    for i, item in enumerate(args.get("items") or []):
        out += [f"args.items[{i}].{k}" for k in WORKFLOW_KEYS["items"] if k not in item]
    return out


def write(run_dir: Path, args: dict) -> Path:
    """Write ``fleet-local.args.json`` next to the run's tasks."""
    path = Path(run_dir) / "fleet-local.args.json"
    write_json(path, args, indent=1, sort_keys=False)
    return path


def instructions(args_path: Path, count: int, run_id: str = "<run-id>",
                 gate_config: str = "<run>/gate.local.json",
                 corpus_dir: str = "corpus/<run-id>") -> str:
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
        f"      python3 scripts/cf.py harvest {run_id} --journal <workflow-dir> --gate",
        f"      python3 scripts/gate/verify_findings.py --gate-config {gate_config} "
        f"--root {corpus_dir} --findings-dir .",
        "      re-queue whatever failed in a second Workflow run",
    ])
