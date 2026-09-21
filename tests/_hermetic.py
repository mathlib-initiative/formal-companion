"""Pin HOME at a temporary directory, for BOTH loaders the repo documents.

`cf init` writes a `library_paths` list with one home-relative entry
(`~/Downloads`), and `cf onboard` expands it and fingerprints every PDF it
finds. With the developer's real HOME the onboarding tests therefore walked and
sha256'd the machine's own PDFs: the suite took 101 s instead of 7 s, one test
took 74 s of that, and what it read depended on whose machine it was. Measured
again against a synthetic home holding 80 PDFs, the documented gate shelled
`pdftotext` 896 times, 880 of them at those files -- eleven per file, once per
onboarding test -- against 16 times and none of them once this module is
imported. (No verdict could flip -- every fixture pins a sha and `locate_pdf`
refuses a mismatch -- but a test that reads the user's Downloads is not a test.)

This module does the pinning **on import** rather than through a pytest hook,
because `python3 -m unittest discover -s tests` -- the gate CLAUDE.md S7
documents -- loads no `tests/conftest.py`, and no package `__init__` either:
with no `-t` it never imports one, which is why `tests/` deliberately has none.
Under that loader the suite read whatever `~/Downloads` held; `tests/conftest.py`
now imports this module, and so does every test module that builds a workspace.
`tests/test_rf_final_tests.py::TestBothLoadersAreHermetic` fails on one that
forgets.

`tests/` is on `sys.path` under `unittest discover -s tests`, under pytest's
prepend import mode and under `python3 tests/test_<name>.py`, so a bare
`import _hermetic` resolves in all three. `python3 -m unittest tests.<module>`
-- the spelling two of those modules document -- puts the checkout root there
instead, which is why each import site falls back to `from tests import
_hermetic`. The prefix guard below makes the two spellings share one home
rather than stack two.

Set `CF_TEST_KEEP_HOME=1` to run against the real HOME deliberately, which is
the only way to reproduce that measurement.
"""
from __future__ import annotations

import atexit
import os
import tempfile

#: the temporary home, kept alive for the process; None when HOME was left alone
HOME: "tempfile.TemporaryDirectory | None" = None

_KEEP = os.environ.get("CF_TEST_KEEP_HOME") == "1"

# The prefix check keeps a re-import (or a test process this suite spawns) from
# stacking a second temporary home on top of the one already in force.
if not _KEEP and "cf-test-home-" not in os.environ.get("HOME", ""):
    HOME = tempfile.TemporaryDirectory(prefix="cf-test-home-")
    for _var in ("HOME", "USERPROFILE"):
        os.environ[_var] = HOME.name
    # Path.home() and os.path.expanduser() read HOME on POSIX, but a stale
    # CF_WORKSPACE from the developer's shell would still point every command
    # that takes no --workspace at a real workspace.
    os.environ.pop("CF_WORKSPACE", None)
    atexit.register(HOME.cleanup)
