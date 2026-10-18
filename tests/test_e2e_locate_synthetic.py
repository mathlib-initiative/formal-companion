"""The synthetic locate campaign end to end, as one test of the suite.

New code, for the locate integration (docs/proposals/wikipedia-locate.md
sections 2-9; references/locate-documents.md).  The driver is
tests/e2e/locate_synthetic/driver.py, which also runs on its own; here it
runs every stage in a temporary directory -- the universe, the sourceset, L2,
the three presets with the staged out-of-tree gate over good and bad
documents, the harvests, the tally, the sealed export and the bench score --
and each stage's own checks read the artifacts it wrote.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "e2e" / "locate_synthetic" / "driver.py"

# Loaded by path under its own name: a bare ``import`` would need the driver's
# directory on sys.path, and a module name there shadows the stage packages.
_spec = importlib.util.spec_from_file_location("locate_synthetic_driver", DRIVER)
pipeline = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pipeline
_spec.loader.exec_module(pipeline)


@unittest.skipUnless(shutil.which("git"), "the ownership stage needs git")
class TestTheSyntheticLocateCampaign(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="cf-locate-e2e-"))
        cls.pipe = pipeline.Pipeline(cls.root)
        for name in pipeline.STAGES:
            getattr(cls.pipe, name)()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_every_stage_ran(self):
        commands = " ".join(self.pipe.log)
        for needle in ("corpus build-db", "sourceset render", "locate candidates",
                       "--preset locate-inventory", "--preset locate ", "--preset locate-challenge",
                       "tally --locate", "export literature-links", "bench score"):
            with self.subTest(stage=needle):
                self.assertIn(needle, commands)

    def test_the_score_is_pinned_and_measured(self):
        score = self.pipe.score
        self.assertRegex(score["measurement_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(score["tiers"]["G1"]["metrics"]["fnf_rate"]["value"], 0)
        self.assertEqual(score["split"], "eval")


if __name__ == "__main__":
    unittest.main()
