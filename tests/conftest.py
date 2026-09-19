"""Make the suite hermetic: HOME is a temporary directory for the whole session.

`cf init` writes a `library_paths` list with one home-relative entry, and
`cf onboard` expands it and fingerprints every PDF it finds.  With the
developer's real HOME the onboarding tests therefore walked and sha256'd the
machine's own PDFs: the suite took 101 s instead of 7 s, one test took 74 s of
that, and what it read depended on whose machine it was.  (No verdict could flip
-- every fixture pins a sha and `locate_pdf` refuses a mismatch -- but a test
that reads the user's Downloads is not a test.)

Set `CF_TEST_KEEP_HOME=1` to run against the real HOME deliberately, which is
the only way to reproduce that measurement.

This is a pytest hook.  `python3 -m unittest discover -s tests` does not load it,
so under unittest the suite still reads whatever `~/Downloads` holds; the CI
workflow runs pytest for that reason.
"""
from __future__ import annotations

import os
import tempfile

_KEEP = os.environ.get("CF_TEST_KEEP_HOME") == "1"
_HOME: "tempfile.TemporaryDirectory | None" = None

if not _KEEP:
    _HOME = tempfile.TemporaryDirectory(prefix="cf-test-home-")
    for _var in ("HOME", "USERPROFILE"):
        os.environ[_var] = _HOME.name
    # Path.home() and os.path.expanduser() read HOME on POSIX, but a stale
    # CF_WORKSPACE from the developer's shell would still point every command
    # that takes no --workspace at a real workspace.
    os.environ.pop("CF_WORKSPACE", None)


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest hook
    if _HOME is not None:
        _HOME.cleanup()
