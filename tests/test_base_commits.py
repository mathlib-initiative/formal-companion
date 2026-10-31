"""A test that needs a frozen base commit may skip without it, but never in CI.

New code, for CLAUDE.md section 7 and ``tests/_base_commits.py``: what ``not_held`` does with
and without ``CF_REQUIRE_BASE_COMMITS``, and that every CI job that runs the suite takes the
whole history and sets it, so those tests run there rather than skip.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
for path in (SKILL / "scripts", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import _base_commits  # noqa: E402
from cflib import yamlio  # noqa: E402


class TestNotHeld(unittest.TestCase):

    def test_a_checkout_without_the_commit_skips(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("CF_REQUIRE_BASE_COMMITS", None)
            with self.assertRaisesRegex(unittest.SkipTest, "does not hold commit abc1234"):
                _base_commits.not_held(self, "abc1234")

    def test_it_fails_where_the_commit_is_required(self):
        with mock.patch.dict(os.environ, {"CF_REQUIRE_BASE_COMMITS": "1"}):
            with self.assertRaisesRegex(self.failureException, "does not hold commit abc1234"):
                _base_commits.not_held(self, "abc1234")


class TestCiRunsThem(unittest.TestCase):

    def test_every_job_that_runs_the_suite_holds_the_base_commits(self):
        workflow = yamlio.read_yaml(SKILL / ".github" / "workflows" / "tests.yml")
        suites = {name: [step for step in job["steps"]
                         if re.search(r"-m (pytest|unittest)\b", step.get("run", ""))]
                  for name, job in workflow["jobs"].items()}
        suites = {name: steps for name, steps in suites.items() if steps}
        self.assertEqual(sorted(suites), ["pytest", "pytest-no-pyyaml", "unittest"])
        for name, steps in suites.items():
            with self.subTest(job=name):
                checkout = [step for step in workflow["jobs"][name]["steps"]
                            if step.get("uses", "").startswith("actions/checkout")]
                self.assertEqual([step.get("with", {}).get("fetch-depth") for step in checkout],
                                 [0], "a shallow checkout holds no base commit")
                for step in steps:
                    self.assertEqual(step.get("env", {}).get("CF_REQUIRE_BASE_COMMITS"), "1",
                                     "without it a missing base commit reads as a pass")


if __name__ == "__main__":
    unittest.main()
