"""The synthetic locate campaign end to end, as one test of the suite.

New code, for the locate integration (docs/proposals/wikipedia-locate.md
sections 2-9; references/locate-documents.md).  The driver is
tests/e2e/locate_synthetic/driver.py, which also runs on its own; here it
runs every stage in a temporary directory -- the universe, the sourceset
adopted from a producer's delivery (references/locate-documents.md section 7),
L2, the three presets with the staged out-of-tree gate over good and bad
documents, the harvests, the tally, the sealed export, the bench score, and the
A/A stage (a replicate planned exactly as the baseline answers ``aa ok``; one
planned with another ``--strong-sample-rate`` is refused by name,
references/locate-bench.md section 9, rule 5) -- and each stage's own checks
read the artifacts it wrote.

One small test keeps ``cf sourceset enumerate-wikiproject`` and ``fetch``
honest offline for as long as the skill keeps its own fetcher: over the
recorded responses they build exactly the sources the delivery adopts.
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
WIKI_FIXTURES = HERE / "data" / "locate" / "wiki"

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
        for needle in ("corpus build-db", "sourceset adopt", "sourceset render",
                       "locate candidates",
                       "--preset locate-inventory", "--preset locate ", "--preset locate-challenge",
                       "tally --locate", "export literature-links", "bench score",
                       "--run-id loc-2", "bench compare"):
            with self.subTest(stage=needle):
                self.assertIn(needle, commands)

    def test_the_sourceset_is_a_producers_delivery_and_nothing_was_fetched(self):
        commands = " ".join(self.pipe.log)
        self.assertNotIn("sourceset fetch", commands)
        self.assertNotIn("enumerate-wikiproject", commands)
        self.assertNotIn("--fixtures", commands)
        from sources import websource
        from sources.sourceset import Sourceset
        manifest = Sourceset.open(pipeline.cf_main_paths(self.pipe.ws), pipeline.SS).latest()
        self.assertEqual(len(manifest), len(pipeline.PAGES))
        for entry in manifest:
            with self.subTest(source=entry["source_id"]):
                self.assertEqual(entry["origin"], "adopted")
                self.assertEqual(entry["fetch"], websource.fetch_block())
                self.assertEqual(set(entry["adopted_from"]),
                                 {"consumer", "pins_sha256", "layer_sha256"})
                self.assertEqual(entry["adopted_from"]["layer_sha256"], entry["layer_sha256"])
                self.assertEqual(entry["assessment"]["importance"],
                                 entry["assessment"]["importance"].lower())

    def test_a_delivery_whose_layer_is_not_this_renderers_is_refused_by_render(self):
        self.assertEqual(self.pipe.parity_refusal["not_the_producers_layer"], [pipeline.WIDGET])
        self.assertEqual(self.pipe.parity_refusal["rendered"], len(pipeline.PAGES) - 1)
        self.assertEqual(self.pipe.parity_status["producer_layer"],
                         {"pinned": len(pipeline.PAGES), "rendered": len(pipeline.PAGES) - 1,
                          "refused": [pipeline.WIDGET], "waiting": 0})

    def test_the_score_is_pinned_and_measured(self):
        score = self.pipe.score
        self.assertRegex(score["measurement_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(score["tiers"]["G1"]["metrics"]["fnf_rate"]["value"], 0)
        self.assertEqual(score["split"], "eval")

    def test_the_aa_replicate_repeats_the_baseline_and_a_changed_plan_is_refused(self):
        doc = self.pipe.aa_result
        self.assertEqual((doc["answer"], doc["failed"]), ("aa ok", []))
        self.assertEqual(doc["replicate"], self.pipe.aa_replicate["measurement_id"])
        self.assertEqual(self.pipe.aa_replicate["runs"]["locate"], ["loc-2"])
        # each side's challenge run and its seed, as recorded: whatever the seed is (it
        # comes from the run id once the planner derives it), the A/A compares none
        seeds = doc["recorded"]["challenge_seed"]
        self.assertEqual({side: sorted(runs) for side, runs in seeds.items()},
                         {"baseline": ["chl-1"], "replicate": ["chl-2"]})
        self.assertTrue(all(isinstance(seed, int) for runs in seeds.values()
                            for seed in runs.values()), seeds)
        # the synthetic worker answers both runs alike, so agreement is SHOWN on every row,
        # not merely left unrefuted
        agree = doc["aa"]["agreement"]
        self.assertEqual((agree["unresolved"], agree["reach"]), ([], 0.0))
        self.assertEqual(len(agree["within"]), agree["compared"])
        self.assertGreater(agree["compared"], 2)
        self.assertIn("strong_sample_rate (--strong-sample-rate): 0.5 in the baseline, 1.0 in "
                      "the replicate", self.pipe.aa_refusal_text)


class TestTheFetchPathStillWorksOffline(unittest.TestCase):
    """``enumerate-wikiproject`` and ``fetch`` still replay the recordings while they exist."""

    def test_enumerate_and_fetch_build_the_sources_the_delivery_adopts(self):
        root = Path(tempfile.mkdtemp(prefix="cf-locate-fetch-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        pipe = pipeline.Pipeline(root)
        pipe.workspace()
        replay = ("--fixtures", str(WIKI_FIXTURES), "--execute")
        pipe.cf("sourceset", "create", "fetched")
        pipe.cf("sourceset", "enumerate-wikiproject", "fetched", "--project", "Mathematics",
                *replay)
        pipe.cf("sourceset", "fetch", "fetched", *replay)
        pipe.cf("sourceset", "render", "fetched")
        pipe.cf("sourceset", "create", "adopted")
        pipe.cf("sourceset", "adopt", "adopted", "--from", str(pipe.pins(pipe.made / "pins")),
                "--consumer", pipeline.CONSUMER)
        pipe.cf("sourceset", "render", "adopted")
        from sources.sourceset import Sourceset
        paths = pipeline.cf_main_paths(pipe.ws)
        fetched = Sourceset.open(paths, "fetched").latest()
        adopted = Sourceset.open(paths, "adopted").latest()
        self.assertEqual(sorted(fetched.entries), sorted(adopted.entries))
        self.assertEqual(len(fetched), len(pipeline.PAGES))
        for sid, entry in fetched.entries.items():
            with self.subTest(source=sid):
                self.assertEqual(entry["origin"], "enumerated")
                self.assertIsNotNone(entry["layer_sha256"])
                ours = dict(entry)
                ours.pop("origin")
                ours.pop("flags", None)            # the page info's: a delivery carries none
                theirs = dict(adopted.get(sid))
                self.assertEqual(theirs.pop("origin"), "adopted")
                self.assertEqual(theirs.pop("adopted_from")["layer_sha256"],
                                 entry["layer_sha256"])
                theirs.pop("flags", None)
                self.assertEqual(theirs, ours)


if __name__ == "__main__":
    unittest.main()
