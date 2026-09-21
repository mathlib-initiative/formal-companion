"""Make the suite hermetic under pytest: HOME is a temporary directory.

The pin itself lives in `tests/_hermetic.py`, which says why it exists and
which measurement it stops. It has to live there rather than here because this
file is a pytest hook and `python3 -m unittest discover -s tests` -- the gate
CLAUDE.md S7 documents -- never loads it; under that loader the suite used to
read whatever the developer's `~/Downloads` held. All this file adds is
pytest's end-of-session cleanup.
"""
from __future__ import annotations

import _hermetic  # noqa: F401 -- imported for its side effect: HOME is pinned


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest hook
    if _hermetic.HOME is not None:
        _hermetic.HOME.cleanup()
