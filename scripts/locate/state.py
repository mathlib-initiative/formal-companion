"""The planner's rows in the sourceset's state file, written through its one writer.

New code, for docs/proposals/wikipedia-locate.md section 3 ("a sourceset-level
state file replaces companions/<name>/state.json for locate").  The file is
``sources.sourceset.Sourceset``'s (references/locate-documents.md section 7):
``Sourceset.record_state`` is its only writer, merging one key's row into one
section and keeping every other.  The planner records ``runs.<run-id>``,
``candidates.<universe>``, ``tallies.<run-id>`` and ``exports.<set-id>`` here.
"""
from __future__ import annotations

from cflib.paths import WorkspacePaths

from sources.sourceset import Sourceset


def record(paths: WorkspacePaths, sourceset_id: str, section: str, key: str,
           values: dict) -> dict:
    """``state[section][key]`` merged with ``values`` (stamped), every sibling kept."""
    return Sourceset.open(paths, sourceset_id).record_state(section, dict(values), key=key)


def read(paths: WorkspacePaths, sourceset_id: str) -> dict:
    return Sourceset.open(paths, sourceset_id).read_state()
