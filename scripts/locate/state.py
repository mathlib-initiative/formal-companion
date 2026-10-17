"""The sourceset-level state file locate writes instead of companions/<name>/state.json.

New code, for docs/proposals/wikipedia-locate.md section 3 ("a sourceset-level
state file replaces companions/<name>/state.json for locate") at the path
references/locate-documents.md section 7 gives (``sourcesets/<id>/state.json``).
Like ``cflib.state`` it is one atomic read-modify-write per command, keeps every
section it does not know, and stamps what it wrote; the planner, the outcomes
and the export each write their own section here.
"""
from __future__ import annotations

import contextlib
from typing import Iterator

from cflib import state as cf_state
from cflib.errors import StateError
from cflib.jsonio import read_json, write_json
from cflib.paths import WorkspacePaths

STATE_VERSION = 1


def read(paths: WorkspacePaths, sourceset_id: str) -> dict:
    path = paths.sourceset_state_json(sourceset_id)
    if not path.exists():
        return {"version": STATE_VERSION, "sourceset": sourceset_id}
    data = read_json(path)
    if not isinstance(data, dict):
        raise StateError(f"{path} is not a JSON object")
    return data


@contextlib.contextmanager
def update(paths: WorkspacePaths, sourceset_id: str) -> Iterator[dict]:
    """Read, let the caller change, then write ``sourcesets/<id>/state.json`` atomically."""
    data = read(paths, sourceset_id)
    yield data
    data["updated_at"] = cf_state.now()
    paths.sourceset_dir(sourceset_id).mkdir(parents=True, exist_ok=True)
    write_json(paths.sourceset_state_json(sourceset_id), data, sort_keys=True)


def record(data: dict, section: str, key: str, values: dict) -> dict:
    """``data[section][key] = values`` with a timestamp, leaving every other key alone."""
    block = data.setdefault(section, {})
    if not isinstance(block, dict):
        raise StateError(f"sourceset state section {section!r} is not a mapping")
    block[key] = {**values, "at": cf_state.now()}
    return block[key]
