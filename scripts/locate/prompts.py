"""Render a locate run's prompts: one system prompt per role, one task prompt per mode.

New code, for docs/proposals/wikipedia-locate.md sections 3, 5 and 6 (the host
tools "with the exact invocation rendered into the prompts").  The bridge
reads ``<role>-system.md`` and ``<role>.md`` from the prompt directory, so a
locate run writes those four names from ``templates/prompts/locate-*.md.tmpl``:

* ``worker-system.md`` -- ``locate-worker-system.md.tmpl``: the universe, the
  tools by absolute path, what the gate refuses, quotes and data hygiene;
* ``worker.md`` -- the mode's own task prompt (``locate-inventory.md.tmpl``,
  ``locate.md.tmpl`` or ``locate-challenge.md.tmpl``) with the document shape
  of references/locate-documents.md sections 2-4;
* ``reviewer-system.md`` and ``reviewer.md`` -- form and gate output only:
  judgment lives in the L5 challenge, not in the tend reviewer.

Every invocation is rendered from the run's own host paths, and the renderer
(``plan.render``) refuses a template with an unfilled slot.
"""
from __future__ import annotations

import pathlib
from typing import Any, Mapping

from cflib import vocab
from cflib.paths import templates_dir

from plan.presets import Preset
from plan.render import render_file
from plan.render_prompts import finish_sentence

from . import presets as locate_presets

PROMPT_FILES = ("worker-system.md", "worker.md", "reviewer-system.md", "reviewer.md")


def finish_block(preset: Preset) -> str:
    """The worker's one way to finish, on this preset's bridge."""
    files = ('["findings/<task id>/<source-id>.json", "tasks/<task id>.yaml"]')
    status = ("`status` is `completed` (documents written and the gate passes), "
              "`needs_review` (written, but you are unsure), or `blocked` (you could not do "
              "the work at all, or the gate itself was broken: say why in `summary`).")
    channel = preset.finish_channel
    if channel == "final_result":
        return ("## How to finish (required)\n"
                "Call the `final_result` tool **exactly once**, with schema_version=1, status "
                "(`completed`, `blocked` or `needs_review`), a non-empty summary, and the files "
                "you changed. This is the only channel that is read.\n")
    shape = ('{"schema_version": 1, "status": "completed", "summary": "one or two sentences", '
             f'"files_changed": {files}}}')
    if channel == "stdout":
        return ("## How to finish (required)\n"
                "There is no `final_result` tool on this bridge and no result file is read. "
                "Finish by printing **one JSON object as the last thing in your output**, after "
                f"committing:\n\n{shape}\n\n{status}\n")
    return ("## How to finish (required)\n"
            "Your stdout is not read, and there is no `final_result` tool on this bridge. "
            "Finish by writing **`.tend/agent_result.json`** in the worktree, last, after "
            f"committing:\n\n{shape}\n\n{status}\n")


def build_context(preset: Preset, *, run_id: str, host: Mapping[str, str],
                  universe, per_task: int, merge_target_branch: str = "main") -> dict[str, Any]:
    """Every slot the locate templates read, from the run's host paths and its universe."""
    gate_dir = host["gate_dir"]
    # the TOOLS database (references/locate-documents.md section 9): the one the
    # allowlist pins the two tools to, never the gate's check database
    db = host["tools_corpus_db"]
    lines = []
    for lid, lib in sorted(universe.libraries.items()):
        lines.append(f"- `{lid}` — {lib.get('repo') or '(no repo url)'} at "
                     f"`{str(lib['commit'])[:12]}`")
    channel = preset.finish_channel
    return {
        "run_id": run_id,
        "mode": preset.mode,
        "schema": vocab.LOCATE_DOCUMENT_SCHEMA,
        "merge_target_branch": merge_target_branch,
        "universe_id": universe.id,
        "universe_sha256": universe.sha256,
        "universe_sha8": universe.sha256[:8],
        "universe_lines": "\n".join(lines),
        "gate_command": host["gate_command"],
        "search_command": f"python3 {gate_dir}/corpus_search.py --db {db}",
        "show_command": f"python3 {gate_dir}/corpus_show.py --db {db}",
        "ids_command": f"python3 {gate_dir}/passage_ids.py",
        "sources_dir": host["sources_dir"],
        "candidates_dir": host["candidates_dir"],
        "tasks_dir": host["tasks_dir"],
        "checkouts_dir": host.get("checkouts_dir") or "",
        "max_locations": vocab.MAX_LOCATIONS_PER_PASSAGE,
        "min_queries": vocab.MIN_ABSENCE_QUERIES,
        "per_task": per_task,
        "batch": vocab.CHALLENGE_BATCH_RECORDS,
        "is_inventory": preset.mode == vocab.LOCATE_INVENTORY,
        "finish_sentence": finish_sentence(preset),
        "finish_block": finish_block(preset),
        "finish_via_tool": channel == "final_result",
        "finish_via_file": channel == "agent_result",
        "finish_via_stdout": channel == "stdout",
    }


def render_prompts(preset: Preset, ctx: Mapping[str, Any], out_dir: pathlib.Path | None = None,
                   *, tdir: pathlib.Path | None = None) -> dict[str, str]:
    """The four prompts of one locate run; written to ``out_dir`` when given."""
    tdir = pathlib.Path(tdir or (templates_dir() / "prompts"))
    shape = locate_presets.shape(preset.name)
    out = {
        "worker-system.md": render_file(tdir / "locate-worker-system.md.tmpl", ctx),
        "worker.md": render_file(tdir / shape.worker_template, ctx),
        "reviewer-system.md": render_file(tdir / "locate-reviewer-system.md.tmpl", ctx),
        "reviewer.md": render_file(tdir / "locate-reviewer.md.tmpl", ctx),
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, text in out.items():
            (out_dir / name).write_text(text, encoding="utf-8")
    return out
