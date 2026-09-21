"""What every command that lands a corpus records once the files are home.

New code, moved out of ``scripts/cfcmd/harvest.py`` (docs/design/multi-source-
model.md S10).  Three commands land a corpus -- ``cf harvest``,
``cf import-corpus`` and ``cf detect-proxies`` -- and all three then owe the
workspace the same two artifacts: ``reports/tally.<run>.json`` and a run block
in every covered companion's ``state.json``.  The two helpers lived in the
harvest COMMAND module and the other two commands imported them by their
underscore names, so a change to `cf harvest`'s wiring silently changed
`cf import-corpus`; cfcmd is the thin argument-parsing layer over the stage
packages, so shared work belongs here instead.
"""
from __future__ import annotations

from pathlib import Path

from harvest import tally as tally_mod
from harvest._compat import cf_state, log, write_json


def run_companions(data: dict) -> list[str]:
    """The companions a run covers (plan-run writes `companion`, a string)."""
    for key in ("companions", "companion", "repos"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return [value]
        if isinstance(value, list) and value:
            return [str(v) for v in value]
    return []


def write_run_tally(workspace, run_id: str, dest: Path) -> dict:
    """Recompute this run's tally from the corpus and write reports/tally.<run>.json.

    The kinds map is per source: the grammar that decides which labels collapse
    onto one item belongs to the source, not to the workspace.
    """
    kinds = {sid: list(workspace.kinds(sid)) for sid in workspace.source_ids()}
    result = tally_mod.tally({run_id: dest}, kinds=kinds)
    out = Path(workspace.paths.tally_json(run_id))
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, result, indent=1, sort_keys=True)
    log.info("")
    log.info(tally_mod.render(result))
    log.info(f"-> {out}")
    return result


def record_run_state(workspace, run_id: str, data: dict, receipt: dict, counts: dict,
                     pin: dict | None = None) -> None:
    """Record findings/receipt counts against every companion the run covers."""
    names = run_companions(data) or workspace.companion_names()
    repos = (counts.get("repos") or {}) if counts else {}
    pinned = ((pin or {}).get("companions") or {})
    for name in names:
        try:
            with cf_state.update(workspace, str(name)) as state:
                block = pinned.get(str(name)) or {}
                values = {
                    "findings": receipt["actual"]["count"],
                    "expected": receipt["expected"]["count"],
                    "receipt_ok": receipt["complete"],
                    "harvested_at": receipt["harvested_at"],
                    # which revision this run's findings are claims about, so
                    # `cf status` can say it without re-reading the corpus
                    "repo_commit": block.get("repo_commit"),
                    "pinned": bool(block.get("fingerprinted")),
                }
                cf_state.record_run(state, run_id, values)
                if str(name) in repos:
                    cf_state.merge_section(state, "tally",
                                           tally_mod.state_counts(repos[str(name)]))
                cf_state.note(state, "harvest", run=run_id, complete=receipt["complete"])
        except Exception as exc:  # noqa: BLE001 - a bad state.json must not lose the corpus
            log.detail(f"state.json for {name}: {exc}")
