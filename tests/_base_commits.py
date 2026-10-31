"""What a test that reads a frozen base commit does in a checkout without that commit.

New code, for CLAUDE.md section 7.  Some tests compare this checkout with an older commit's
tree (``git archive``, ``git show``), so they cannot run in a checkout that does not hold it,
such as a shallow clone.  They skip there, and the skip reads as a pass: CI's depth-1 checkout
hid seven stale assertions behind a green run.  ``.github/workflows/tests.yml`` now takes the
whole history and sets ``CF_REQUIRE_BASE_COMMITS=1``, which makes the skip a failure, as
``CF_REQUIRE_POPPLER`` does for ``tests/e2e/synthetic.sh``.
"""
from __future__ import annotations

import os
import unittest


def not_held(case: unittest.TestCase, commit: str) -> None:
    """End ``case``, which cannot read ``commit``: a skip, a failure if the commit is required."""
    why = f"this checkout does not hold commit {commit}"
    if os.environ.get("CF_REQUIRE_BASE_COMMITS") == "1":
        case.fail(f"{why} and CF_REQUIRE_BASE_COMMITS=1 requires it: check out the whole history")
    case.skipTest(why)
