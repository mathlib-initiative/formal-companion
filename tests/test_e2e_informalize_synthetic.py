"""The informalize fleet end to end on a fake library, as one test of the suite.

New code, for docs/proposals/informalize.md sections 8.1 to 8.3 (component 5a of its section
11).  The driver is tests/e2e/informalize_synthetic/driver.py, which also runs on its own:
each phase planned with `cf plan-informalize`, its contributions written in worktrees of an
entrypoint made as `cf run` makes one, judged by the staged gate as the host runs it
(`python3 -I` from a copy of the planned `gate/`, a bare environment), good ones passed and
bad ones refused under the rule they break, the batched pre-merge check, a task at the
attempt cap, a gate whose pinned file moved, and each run harvested by `cf harvest` from
that entrypoint.  The phases that build need pdflatex, latexmk, kpsewhich and every package
of `tex_packages`, and are skipped without them; CF_REQUIRE_TEX=1 (CI's TeX job) makes that
skip a failure.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "e2e" / "informalize_synthetic" / "driver.py"

# Loaded by path under its own name, as the locate driver is.
_spec = importlib.util.spec_from_file_location("informalize_synthetic_driver", DRIVER)
pipeline = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pipeline
_spec.loader.exec_module(pipeline)

REQUIRE_TEX = os.environ.get("CF_REQUIRE_TEX") == "1"


@unittest.skipUnless(shutil.which("git"), "the fleet's change sets are git's")
class TestTheSyntheticInformalizeFleet(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="cf-informalize-e2e-"))
        cls.pipe = pipeline.Pipeline(cls.root)
        cls.done: list[str] = []
        names = pipeline.STAGES if pipeline.have_tex() else (
            "workspace", "items", "cap", "broken")
        for name in names:
            getattr(cls.pipe, name)()
            cls.done.append(name)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_the_phases_without_tex_ran(self):
        for name in ("workspace", "items", "cap", "broken"):
            self.assertIn(name, self.done)

    def test_the_phases_that_build_ran(self):
        if not pipeline.have_tex():
            if REQUIRE_TEX:
                self.fail("CF_REQUIRE_TEX=1 and pdflatex, latexmk, kpsewhich or a package of "
                          "tex_packages is missing")
            self.skipTest("no pdflatex, latexmk, kpsewhich or a package of tex_packages")
        self.assertEqual(tuple(self.done), pipeline.STAGES)

    def test_every_phase_was_planned_once(self):
        expected = {"items"} | ({"conventions", "draft", "attack", "judge", "edit"}
                                if pipeline.have_tex() else set())
        self.assertEqual(set(self.pipe.runs), expected)
        for phase, run_id in self.pipe.runs.items():
            self.assertEqual(run_id, f"{pipeline.ID}-{phase}-1")


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
