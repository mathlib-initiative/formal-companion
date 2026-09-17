"""The cflib names the run stage uses, plus the few helpers only it needs.

New code.  Everything shared lives in ``scripts/cflib`` (workspace.yaml,
WorkspacePaths, state.json, the vocabulary); this module re-exports what the run
stage uses so a reader of run/*.py can see its whole dependency surface in one
place, and adds three things cflib has no reason to own: a byte formatter and a
directory sizer for the disk budget, and the substantive-kind set used when a
run's findings are summarised.

Also fixes sys.path when a module here is executed directly
(``python3 scripts/run/status.py``) rather than through ``scripts/cf.py``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cflib import hashing, log, paths as cf_paths, state as cf_state, vocab  # noqa: E402
from cflib.workspace import Workspace, from_args  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_json, write_json  # noqa: E402
from cflib.yamlio import read_yaml, write_yaml  # noqa: E402

__all__ = [
    "Workspace", "CfError", "from_args", "hashing", "log", "cf_paths", "cf_state",
    "vocab", "read_json", "write_json", "read_yaml", "write_yaml",
    "iso_now", "dir_size", "human_bytes", "skill_root",
]

#: UTC timestamp, seconds resolution -- the same stamp state.json uses.
iso_now = cf_state.now


def skill_root() -> Path:
    """The companion-faithfulness checkout (never a workspace directory)."""
    return cf_paths.skill_root()


def dir_size(path: str | Path) -> int:
    """Total bytes of a directory tree (0 when it does not exist)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def human_bytes(n: int) -> str:
    """'1.1 GB' -- for the disk-budget warning in the run plan."""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"
