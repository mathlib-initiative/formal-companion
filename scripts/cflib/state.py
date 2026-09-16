"""companions/<name>/state.json: the durable per-companion state.

New code (design/architecture.md S2).  Before the skill, this state lived in
prose handoffs and was re-derived by reading files, which is how counts drifted
("every counting error so far ran toward cleaner").  Every command writes its
counts back here, atomically, and `cf status` prints nothing it did not read
from these files.

Layout (sections are created on demand; unknown sections are preserved):

    {"version": 1, "name": "ours", "repo": "...", "commit": "7dccce1",
     "adapter": {"kind": "docstring_tags", "key": "sha256:...", "sha": "..."},
     "book_sha": "64498329...", "ledger_sha": null, "stale_since_commit": null,
     "extract": {...}, "coverage": {...}, "lint": {...},
     "runs": {"<run-id>": {...}}, "tally": {...}, "adjudication": {...},
     "routed": {...}, "detect": {...}, "fixes": {...},
     "verdict_layer": null, "updated_at": "...", "history": [...]}
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import StateError
from .jsonio import read_json, write_json

STATE_VERSION = 1

#: Sections `cf status` knows how to summarise, in pipeline order.
SECTIONS = ("extract", "lint", "coverage", "runs", "tally", "adjudication",
            "routed", "detect", "fixes")

_HISTORY_CAP = 100


def now() -> str:
    """UTC timestamp, seconds resolution, Z suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_state(name: str, repo: str | None = None, commit: str | None = None,
              adapter: dict | None = None, book_sha: str | None = None) -> dict:
    """A fresh state document (written by `cf onboard`)."""
    return {
        "version": STATE_VERSION,
        "name": name,
        "repo": repo,
        "commit": commit,
        "adapter": adapter or {},
        "book_sha": book_sha,
        "ledger_sha": None,          # ledger_nodes repos: their export's sha
        "stale_since_commit": None,  # set when the repo moved past the export
        "extract": {},
        "lint": {},
        "coverage": {},
        "runs": {},
        "tally": {},
        "adjudication": {},
        "routed": {},
        "detect": {},
        "fixes": {},
        "verdict_layer": None,
        "created_at": now(),
        "updated_at": now(),
        "history": [],
    }


def state_path(campaign, name: str) -> Path:
    """companions/<name>/state.json for a Campaign (or a CampaignPaths)."""
    paths = getattr(campaign, "paths", campaign)
    return paths.state_json(name)


def read_state(campaign, name: str, missing_ok: bool = False) -> dict:
    """Read state.json. With ``missing_ok`` an absent file yields a skeleton."""
    p = state_path(campaign, name)
    if not p.exists():
        if missing_ok:
            return new_state(name)
        raise StateError(f"{p} does not exist",
                         hint=f"run `cf onboard {name} --repo <path> --adapter <kind>` first")
    data = read_json(p)
    if not isinstance(data, dict):
        raise StateError(f"{p}: expected a JSON object")
    data.setdefault("version", STATE_VERSION)
    data.setdefault("name", name)
    return data


def write_state(campaign, name: str, state: dict) -> Path:
    """Write state.json atomically, stamping updated_at."""
    state["updated_at"] = now()
    return write_json(state_path(campaign, name), state)


@contextlib.contextmanager
def update(campaign, name: str, missing_ok: bool = True) -> Iterator[dict]:
    """Read-modify-write state.json; nothing is written if the body raises.

        with state.update(campaign, "ours") as st:
            state.set_section(st, "extract", {"rows": 1157, "items": 763})
    """
    st = read_state(campaign, name, missing_ok=missing_ok)
    yield st
    write_state(campaign, name, st)


def set_section(state: dict, section: str, values: dict, stamp: bool = True) -> dict:
    """Replace a section's contents (adding ``at``) and return the section."""
    body = dict(values)
    if stamp:
        body["at"] = now()
    state[section] = body
    return body


def merge_section(state: dict, section: str, values: dict, stamp: bool = True) -> dict:
    """Merge into a section instead of replacing it."""
    body = dict(state.get(section) or {})
    body.update(values)
    if stamp:
        body["at"] = now()
    state[section] = body
    return body


def record_run(state: dict, run_id: str, values: dict) -> dict:
    """Record one run's counts under runs.<run-id>."""
    runs = state.setdefault("runs", {})
    body = dict(runs.get(run_id) or {})
    body.update(values)
    body["at"] = now()
    runs[run_id] = body
    return body


def note(state: dict, event: str, **fields: Any) -> None:
    """Append a short history entry (capped), so `cf status` can show order."""
    hist = state.setdefault("history", [])
    hist.append({"at": now(), "event": event, **fields})
    if len(hist) > _HISTORY_CAP:
        del hist[:-_HISTORY_CAP]


def summary_row(state: dict) -> dict:
    """The one-line summary `cf status` prints per companion."""
    extract = state.get("extract") or {}
    coverage = state.get("coverage") or {}
    tally = state.get("tally") or {}
    adj = state.get("adjudication") or {}
    routed = state.get("routed") or {}
    runs = state.get("runs") or {}
    return {
        "companion": state.get("name"),
        "commit": (state.get("commit") or "")[:7] or None,
        "adapter": (state.get("adapter") or {}).get("kind"),
        "rows": extract.get("rows"),
        "items": extract.get("items"),
        "single_source": coverage.get("single_source"),
        "runs": len(runs) or None,
        "findings": tally.get("rows"),
        "confirmed": adj.get("confirmed"),
        "refuted": adj.get("refuted"),
        "routed": routed.get("status"),
        "stale": "yes" if state.get("stale_since_commit") else None,
        "updated": (state.get("updated_at") or "")[:10] or None,
    }
