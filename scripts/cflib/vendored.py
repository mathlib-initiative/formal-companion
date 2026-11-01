"""Put ``vendor/`` on ``sys.path``, so the vendored annotation format imports.

New code, for the switchover to formal-companion-annotations (CLAUDE.md
section 2).  ``vendor/formal_companion_annotations/`` is one tagged release of
the format's own repository, copied byte for byte and sealed by
``vendor/formal_companion_annotations.manifest.json``
(``scripts/annotations/sync_format.py`` wrote both; never edit them by hand, but
for the one line that script's docstring names).
A module that imports the format imports this first::

    from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
    from formal_companion_annotations import ids

``vendor/`` goes FIRST on the path, so the vendored release is the one that
runs even where some other copy of the package is installed: the skill's
behaviour is pinned by its tree, not by the machine.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: The skill checkout's ``vendor/`` directory.
VENDOR = Path(__file__).resolve().parents[2] / "vendor"

if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))
