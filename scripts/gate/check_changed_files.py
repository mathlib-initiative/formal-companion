#!/usr/bin/env python3
"""Standalone CLI for the changed-files half of the gate.

The logic lives in ``verify_findings.py`` (``changed_entries`` / ``check_changed``)
because only that file and ``gate.json`` are transferred to a run host — the
tend config calls ``verify_findings.py --changed-files``. This wrapper exists
for local use and for a tend config that prefers two separate commands:

    python3 scripts/gate/check_changed_files.py --base main --root <worktree>

Ported from: nothing. It closes the hole the design critique named — the
original gate lived at ``scripts/verify_findings.py`` *inside* the worktree the
worker commits to, so a worker that committed an edited gate passed both the
validation and the pre-merge validation.

Exit status: 0 when the change set is clean, 1 when the contribution is refused,
3 when the check itself could not run (no such base ref, unreadable git output) —
a worker must answer a 3 by stopping and reporting blocked, never by deleting
findings until the check goes quiet.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from verify_findings import (EXIT_GATE_BROKEN, EXIT_OK, EXIT_REJECTED,  # noqa: E402
                            changed_entries, check_changed, ownership_line)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="main", help="branch the contribution is measured against")
    ap.add_argument("--root", default=".", help="worktree root (default: cwd)")
    ap.add_argument(
        "--task-id",
        default=os.environ.get("TEND_TASK_ID", ""),
        help="the worker's own task id: a contribution that touches another task's "
             "findings/<id>.json or tasks/<id>.yaml is refused (default: $TEND_TASK_ID). "
             "Without one only the single-stem rule runs: two stems are refused once some "
             "stem's findings file was already on --base (a batched pre-merge set, which "
             "adds one findings file per task, passes), but nothing can tell whose the one "
             "stem is",
    )
    ap.add_argument(
        "--allow-empty",
        action="store_true",
        help="tolerate a change set with no findings file (task-only merges)",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    entries, errs = changed_entries(root, args.base)
    problems = errs + check_changed(entries, allow_empty=args.allow_empty,
                                   task_id=args.task_id)
    if problems:
        print(f"CHANGED-FILES CHECK FAILED ({len(problems)} problem(s)):", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        print(f"  changed paths: {sorted(entries.items())}", file=sys.stderr)
        # `errs` means the check could not run (no such base ref, unparseable
        # output): an infrastructure failure a worker must not answer by editing
        # its findings, so it gets its own exit code.
        return EXIT_GATE_BROKEN if errs else EXIT_REJECTED
    if not args.quiet:
        print(f"OK: contribution touched only {len(entries)} allowed path(s) — "
              f"{ownership_line(args.task_id)}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
