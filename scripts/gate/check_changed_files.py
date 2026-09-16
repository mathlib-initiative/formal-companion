#!/usr/bin/env python3
"""Standalone CLI for the changed-files half of the gate.

The logic lives in ``verify_findings.py`` (``changed_paths`` / ``check_changed``)
because only that file and ``gate.json`` are transferred to a run host — the
tend config calls ``verify_findings.py --changed-files``. This wrapper exists
for local use and for a tend config that prefers two separate commands:

    python3 scripts/gate/check_changed_files.py --base main --root <worktree>

Ported from: nothing. It closes the hole the design critique named — the
original gate lived at ``scripts/verify_findings.py`` *inside* the worktree the
worker commits to, so a worker that committed an edited gate passed both the
validation and the pre-merge validation.

Exit status: 0 when the change set is clean, 1 otherwise.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from verify_findings import changed_paths, check_changed  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="main", help="branch the contribution is measured against")
    ap.add_argument("--root", default=".", help="worktree root (default: cwd)")
    ap.add_argument(
        "--allow-empty",
        action="store_true",
        help="tolerate a change set with no findings file (task-only merges)",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    paths, errs = changed_paths(root, args.base)
    problems = errs + check_changed(paths, allow_empty=args.allow_empty)
    if problems:
        print(f"CHANGED-FILES CHECK FAILED ({len(problems)} problem(s)):", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        print(f"  changed paths: {sorted(paths)}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"OK: contribution touched only {len(paths)} allowed path(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
