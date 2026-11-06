"""The host, played locally: `ssh` and `rsync` against a folder of this machine.

New code, for the tests of docs/proposals/informalize.md sections 6.1 and 9: every
command that copies anything to or from a host -- `cf informalize fetch-library` (stage
I1) and `fetch-pdf` (I6) -- takes a `runner`, so a test can play the host itself.  This
is the one such runner, shared by tests/test_informalize_library.py,
tests/test_informalize_pdf.py and tests/e2e/informalize_synthetic/driver.py, which had a
copy each.  It reaches no network: `test -f` is asked of a local path and `rsync` is run
with the alias stripped off its arguments.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


class FakeHost:
    """ssh and rsync against a local folder: `test -f` asked of it, rsync run locally."""

    def __init__(self, ssh: str):
        self.ssh = ssh
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        if argv[0] == "ssh":
            path = argv[-1].split(" ", 2)[2]
            return subprocess.CompletedProcess(argv, 0 if Path(path).is_file() else 1, "", "")
        if argv[0] == "rsync":
            local = [a[len(self.ssh) + 1:] if a.startswith(self.ssh + ":") else a for a in argv]
            return subprocess.run(local, capture_output=True, text=True)
        raise AssertionError(f"unexpected command {argv}")    # pragma: no cover
