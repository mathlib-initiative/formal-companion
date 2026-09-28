"""pipeline.yaml validation and the stage-receipt writer.

docs/proposals/pdf-to-companion.md section 4.1 gives the receipt's fields and
section 5.1 (M0a) asks for a writer that passes its tests; the writer fails
closed, so most of these are refusals.  Every workspace here is a temporary
directory.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import yamlio                                              # noqa: E402
from cflib.errors import CfError                                      # noqa: E402
from cflib.hashing import sha256_file                                 # noqa: E402
from cflib.paths import WorkspacePaths                                # noqa: E402
from pipeline import pipeline_yaml, stage_receipt                     # noqa: E402

SHA_A = "a1" * 32
SHA_B = "b2" * 32
SHA_C = "c3" * 32


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-pipe-", dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def valid_pipeline() -> dict:
    return {
        "version": 1,
        "pipeline": {"id": "tao-ch1", "source_id": "a-book-2e", "companion": "a-companion"},
        "exercise_policy": None,
        "scope": {"chapters": [1], "sections": ["1.7"]},
        "consent": {"include_source_text": True, "recorded": "2026-09-27", "note": ""},
        "fleets": {"S5": ["attack-vac-1", "attack-hunt-1"], "S9": ["diff-1"]},
        "notes": "",
    }


def valid_receipt() -> dict:
    return {
        "input_doc_sha256": SHA_A,
        "output_doc_sha256": SHA_B,
        "pipeline_yaml_sha256": SHA_C,
        "fleet_runs": [{"run_id": "attack-vac-1", "run_yaml_sha256": SHA_A,
                        "receipt_sha256": SHA_B}],
        "prompt_shas": {"worker": SHA_A, "reviewer": SHA_B},
        "model_ids": ["claude-opus-5"],
        "gate_result": {"ok": True, "problems": []},
        "consent": {"include_source_text": True, "recorded": "2026-09-27"},
        "cost_usd": 12.5,
        "tokens": {"claude-opus-5": {"input": 1, "output": 2, "cache_write": 3, "cache_read": 4}},
        "wall_clock_s": 3600,
    }


# ---------------------------------------------------------------------------
# pipeline.yaml
# ---------------------------------------------------------------------------

class TestPipelineYaml(unittest.TestCase):
    def setUp(self):
        self.root = scratch(self)
        self.paths = WorkspacePaths(root=self.root)

    def write(self, data: dict, pipeline_id: str = "tao-ch1") -> Path:
        # block YAML, so the fallback reader CI runs without PyYAML parses it
        path = self.paths.pipeline_yaml(pipeline_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yamlio.dump_yaml(data), encoding="utf-8")
        return path

    def test_a_valid_file_loads_and_lists_its_fleets(self):
        self.assertEqual(pipeline_yaml.validate(valid_pipeline()), [])
        self.write(valid_pipeline())
        data = pipeline_yaml.load_for(self.paths, "tao-ch1")
        self.assertEqual(pipeline_yaml.fleet_run_ids(data, "S5"), ["attack-vac-1", "attack-hunt-1"])
        self.assertEqual(pipeline_yaml.fleet_run_ids(data),
                         ["attack-vac-1", "attack-hunt-1", "diff-1"])

    def test_the_policy_may_be_unset_but_not_anything_else(self):
        for policy in ("stub", "prove", None):
            with self.subTest(policy=policy):
                self.assertEqual(pipeline_yaml.validate(dict(valid_pipeline(),
                                                             exercise_policy=policy)), [])
        problems = pipeline_yaml.validate(dict(valid_pipeline(), exercise_policy="skip"))
        self.assertTrue(any("exercise_policy" in p for p in problems), problems)

    def test_unknown_keys_are_refused_at_every_level(self):
        cases = {
            "top": dict(valid_pipeline(), budget=5),
            "pipeline": dict(valid_pipeline(),
                             pipeline=dict(valid_pipeline()["pipeline"], host="x")),
            "scope": dict(valid_pipeline(), scope={"chapters": [1], "pages": [3]}),
            "consent": dict(valid_pipeline(),
                            consent={"include_source_text": False, "signed": "yes"}),
        }
        for where, data in cases.items():
            with self.subTest(where=where):
                problems = pipeline_yaml.validate(data)
                self.assertTrue(any("unknown key" in p for p in problems), problems)

    def test_missing_required_keys_are_refused(self):
        for key in ("version", "pipeline", "exercise_policy", "scope", "consent", "fleets"):
            data = valid_pipeline()
            del data[key]
            with self.subTest(key=key):
                problems = pipeline_yaml.validate(data)
                self.assertTrue(any(f"missing required key {key!r}" in p for p in problems),
                                problems)
        data = valid_pipeline()
        del data["pipeline"]["companion"]
        self.assertTrue(any("companion" in p for p in pipeline_yaml.validate(data)))

    def test_the_scope_is_explicit(self):
        problems = pipeline_yaml.validate(dict(valid_pipeline(), scope={}))
        self.assertTrue(any("names no chapter" in p for p in problems), problems)
        problems = pipeline_yaml.validate(dict(valid_pipeline(),
                                               scope={"chapters": [0], "sections": ["x"]}))
        self.assertTrue(any("positive integers" in p for p in problems), problems)
        self.assertTrue(any("<chapter>.<section>" in p for p in problems), problems)

    def test_consent_to_send_source_text_needs_a_date(self):
        problems = pipeline_yaml.validate(dict(valid_pipeline(),
                                               consent={"include_source_text": True}))
        self.assertTrue(any("recorded" in p for p in problems), problems)
        self.assertEqual(pipeline_yaml.validate(dict(valid_pipeline(),
                                                     consent={"include_source_text": False})), [])
        problems = pipeline_yaml.validate(dict(valid_pipeline(),
                                               consent={"include_source_text": "yes"}))
        self.assertTrue(any("true or false" in p for p in problems), problems)

    def test_fleets_name_stages_and_each_run_once(self):
        problems = pipeline_yaml.validate(dict(valid_pipeline(), fleets={"S10": ["r"]}))
        self.assertTrue(any("not a stage" in p for p in problems), problems)
        problems = pipeline_yaml.validate(dict(valid_pipeline(),
                                               fleets={"S5": ["r"], "S9": ["r"]}))
        self.assertTrue(any("already listed" in p for p in problems), problems)
        problems = pipeline_yaml.validate(dict(valid_pipeline(), fleets={"S5": "r"}))
        self.assertTrue(any("list of run ids" in p for p in problems), problems)

    def test_the_id_must_match_its_directory(self):
        self.write(valid_pipeline(), pipeline_id="other")
        with self.assertRaises(CfError) as caught:
            pipeline_yaml.load_for(self.paths, "other")
        self.assertIn("not the directory", str(caught.exception))
        bad = valid_pipeline()
        bad["pipeline"]["id"] = "a/b"
        self.assertTrue(pipeline_yaml.validate(bad))

    def test_version_and_shape_are_checked(self):
        self.assertTrue(pipeline_yaml.validate([]))
        self.assertTrue(any("version must be 1" in p
                            for p in pipeline_yaml.validate(dict(valid_pipeline(), version=2))))
        self.write(dict(valid_pipeline(), version=2))
        with self.assertRaises(CfError):
            pipeline_yaml.load_for(self.paths, "tao-ch1")


# ---------------------------------------------------------------------------
# the stage receipt writer
# ---------------------------------------------------------------------------

class TestStageReceiptWriter(unittest.TestCase):
    def setUp(self):
        self.root = scratch(self)
        self.paths = WorkspacePaths(root=self.root)

    def test_a_valid_receipt_is_written_with_its_envelope_and_reads_back(self):
        out = stage_receipt.write(self.paths, "tao-ch1", "S5", valid_receipt())
        self.assertEqual(out, self.root / "pipelines" / "tao-ch1" / "stages"
                         / "S5.stage-receipt.json")
        doc = json.loads(out.read_text(encoding="utf-8"))
        for key in stage_receipt.RECEIPT_FIELDS + stage_receipt.ENVELOPE_FIELDS:
            self.assertIn(key, doc)
        self.assertEqual((doc["pipeline_id"], doc["stage"], doc["schema_version"]),
                         ("tao-ch1", "S5", 1))
        self.assertEqual(stage_receipt.read(out)["cost_usd"], 12.5)

    def refuse(self, receipt: dict, needle: str, stage: str = "S5") -> None:
        with self.assertRaises(CfError) as caught:
            stage_receipt.write(self.paths, "tao-ch1", stage, receipt)
        self.assertIn(needle, str(caught.exception))
        self.assertFalse(self.paths.stage_receipt_json("tao-ch1", stage).exists(),
                         "nothing may be written on a refusal")

    def test_a_missing_field_is_refused(self):
        for key in stage_receipt.RECEIPT_FIELDS:
            receipt = valid_receipt()
            del receipt[key]
            with self.subTest(missing=key):
                self.refuse(receipt, f"missing field {key!r}")

    def test_an_extra_field_is_refused(self):
        self.refuse(dict(valid_receipt(), notes="x"), "unknown field 'notes'")

    def test_a_sha_of_the_wrong_length_or_case_is_refused(self):
        for key in stage_receipt.SHA_FIELDS:
            with self.subTest(field=key):
                self.refuse(dict(valid_receipt(), **{key: SHA_A[:-1]}), f"{key} must be 64")
                self.refuse(dict(valid_receipt(), **{key: SHA_A.upper()}), f"{key} must be 64")
                self.refuse(dict(valid_receipt(), **{key: None}), f"{key} must be 64")
        receipt = valid_receipt()
        receipt["fleet_runs"][0]["receipt_sha256"] = "abc"
        self.refuse(receipt, "receipt_sha256 must be 64")
        receipt = valid_receipt()
        receipt["prompt_shas"]["worker"] = SHA_A + "0"
        self.refuse(receipt, "prompt_shas['worker'] must be 64")

    def test_a_fleet_run_entry_is_checked_whole(self):
        receipt = valid_receipt()
        del receipt["fleet_runs"][0]["run_yaml_sha256"]
        self.refuse(receipt, "fleet_runs[0]: missing field 'run_yaml_sha256'")
        receipt = valid_receipt()
        receipt["fleet_runs"][0]["host"] = "x"
        self.refuse(receipt, "fleet_runs[0]: unknown field 'host'")
        receipt = valid_receipt()
        receipt["fleet_runs"].append(dict(receipt["fleet_runs"][0]))
        self.refuse(receipt, "listed twice")

    def test_the_stage_name_is_checked(self):
        self.refuse(valid_receipt(), "not one of S0 to S9", stage="S10")
        with self.assertRaises(CfError):
            stage_receipt.write(self.paths, "tao-ch1", "../x", valid_receipt())

    def test_numbers_and_tokens_are_checked(self):
        self.refuse(dict(valid_receipt(), cost_usd=-1), "cost_usd")
        self.refuse(dict(valid_receipt(), cost_usd=True), "cost_usd")
        self.refuse(dict(valid_receipt(), cost_usd="12"), "cost_usd")
        self.refuse(dict(valid_receipt(), wall_clock_s=-5), "wall_clock_s")
        self.refuse(dict(valid_receipt(), tokens={"m": {"input": 1}}), "tokens['m']")
        self.refuse(dict(valid_receipt(), tokens={"m": {"input": 1.5, "output": 0,
                                                        "cache_write": 0, "cache_read": 0}}),
                    "tokens['m']")
        self.refuse(dict(valid_receipt(), gate_result={"ok": "yes"}), "gate_result")
        self.refuse(dict(valid_receipt(), consent={"recorded": "2026-09-27"}), "consent")
        self.refuse(dict(valid_receipt(), model_ids=["", "x"]), "model_ids")

    def test_a_written_file_that_was_edited_is_refused_on_read(self):
        out = stage_receipt.write(self.paths, "tao-ch1", "S5", valid_receipt())
        doc = json.loads(out.read_text(encoding="utf-8"))
        doc["output_doc_sha256"] = "short"
        out.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(CfError):
            stage_receipt.read(out)


class TestSumFleets(unittest.TestCase):
    """The stage receipt sums its fleets from their run.yaml and RECEIPT.json."""

    def setUp(self):
        self.root = scratch(self)
        self.paths = WorkspacePaths(root=self.root)

    def fleet(self, run_id: str, cost: dict | None, *, receipt=True) -> None:
        run_yaml = self.paths.run_yaml(run_id)
        run_yaml.parent.mkdir(parents=True, exist_ok=True)
        run_yaml.write_text(f"run_id: {run_id}\ntasks:\n  count: 3\n", encoding="utf-8")
        if receipt:
            path = self.paths.receipt_json(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"run_id": run_id, "complete": True, "cost": cost}),
                            encoding="utf-8")

    def block(self, usd: float, model: str = "claude-opus-5", unpriced=()) -> dict:
        return {"usd": usd, "priced": [model], "unpriced": list(unpriced),
                "tokens": {model: {"input": 10, "output": 20, "cache_write": 30,
                                   "cache_read": 40}}}

    def test_two_priced_fleets_sum(self):
        self.fleet("a", self.block(1.25))
        self.fleet("b", self.block(2.0, model="claude-sonnet-5"))
        summed = stage_receipt.sum_fleets(self.paths, ["a", "b"])
        self.assertEqual(summed["cost_usd"], 3.25)
        self.assertEqual(summed["model_ids"], ["claude-opus-5", "claude-sonnet-5"])
        self.assertEqual(summed["tokens"]["claude-opus-5"]["cache_read"], 40)
        self.assertEqual([f["run_id"] for f in summed["fleet_runs"]], ["a", "b"])
        self.assertEqual(summed["fleet_runs"][0]["run_yaml_sha256"],
                         sha256_file(self.paths.run_yaml("a")))
        self.assertEqual(summed["fleet_runs"][0]["receipt_sha256"],
                         sha256_file(self.paths.receipt_json("a")))
        # and the sum is a receipt body the writer accepts
        receipt = dict(valid_receipt(), **summed)
        self.assertTrue(stage_receipt.write(self.paths, "p", "S5", receipt).exists())

    def test_a_fleet_without_a_cost_block_is_refused(self):
        self.fleet("a", None)
        with self.assertRaises(CfError) as caught:
            stage_receipt.sum_fleets(self.paths, ["a"])
        self.assertIn("no cost block", str(caught.exception))

    def test_a_fleet_with_an_unpriced_model_is_refused(self):
        self.fleet("a", self.block(1.0, unpriced=["some-other-model"]))
        with self.assertRaises(CfError) as caught:
            stage_receipt.sum_fleets(self.paths, ["a"])
        self.assertIn("unpriced", str(caught.exception))

    def test_a_fleet_never_harvested_or_never_planned_is_refused(self):
        self.fleet("a", self.block(1.0), receipt=False)
        with self.assertRaises(CfError) as caught:
            stage_receipt.sum_fleets(self.paths, ["a"])
        self.assertIn("RECEIPT.json", str(caught.exception))
        with self.assertRaises(CfError) as caught:
            stage_receipt.sum_fleets(self.paths, ["never-planned"])
        self.assertIn("run.yaml", str(caught.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
