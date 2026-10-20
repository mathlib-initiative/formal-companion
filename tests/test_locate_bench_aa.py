"""The A/A check of the acceptance rule: a replicate of the baseline's configuration
whose outcomes repeat the baseline's.

New code, for references/locate-bench.md section 9, rule 5 (docs/proposals/
wikipedia-locate.md section 9, step 3: "the A/A run rejects").  The rule's own
A/A condition is one-sided -- the rule applied to the baseline and its replicate
must not accept -- so a replicate that came out clearly WORSE, or that differs on
a metric the claim does not name, passed it.  These tests pin the agreement half
(every metric of every tier both scores hold, and the cost per article, within a
stated tolerance on the paired interval), the planning refusal by name, the
challenge seed that is recorded and compared by nothing, and
``cf bench compare <id> <baseline> --aa <replicate>``, the check alone, which
answers ``aa ok`` or ``aa FAILS`` and writes nothing.  The score documents are
synthetic (``test_locate_bench.score_doc``).
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent / "scripts"), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import test_locate_bench as tlb  # noqa: E402
except ImportError:  # pragma: no cover
    from tests import test_locate_bench as tlb  # noqa: E402

import cf as cf_main  # noqa: E402
from bench import compare as compare_mod  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.yamlio import write_yaml  # noqa: E402

fx = tlb.fx
score_doc = tlb.score_doc

#: a challenge run's planning keys as cf plan-locate records them
CHALLENGE_PLAN = {"per_task": None, "subject_only": False, "sample_rate": 0.2,
                  "strong_sample_rate": 0.5, "absence_sample_rate": 0.25}


class TestTheAACheck(unittest.TestCase):

    def setUp(self):
        self.world = fx.World(tlb.scratch(self) / "ws", articles=1)
        self.lay = tlb.layout_of(self.world)
        self.lay.root.mkdir(parents=True)
        n = 40
        self.base_cells = {f"c{i:02d}": [1.0 if i % 2 else 0.0, 1.0] for i in range(n)}
        self.better_cells = {f"c{i:02d}": [1.0 if i % 8 == 1 else 0.0, 1.0] for i in range(n)}
        self.worse_cells = {f"c{i:02d}": [1.0 if i % 8 else 0.0, 1.0] for i in range(n)}

    def write(self, name, doc) -> Path:
        path = self.lay.root / f"{name}.json"
        path.write_text(json.dumps(doc))
        return path

    def aa(self, base, rep, **kw) -> dict:
        return compare_mod.aa_check(self.lay, base, rep, claim=kw.pop("claim", "G3.fnf_rate"),
                                    replicates=kw.pop("replicates", 1000), seed=1, **kw)

    def compare(self, base, cand, aa) -> dict:
        return compare_mod.compare(self.lay, base, cand, claim="G3.fnf_rate", aa_path=aa,
                                   replicates=1000, seed=1)

    def challenged(self, mid, run, *, plan=None, seed=0, **kw) -> dict:
        """A score of one run challenged by ``run``, planned as ``plan`` says."""
        doc = score_doc(self.base_cells, mid=mid, challenge_runs=(run,), **kw)
        doc["pins"]["planning"] = {run: dict(CHALLENGE_PLAN if plan is None else plan)}
        doc["pins"]["challenge_seed"] = {run: seed}
        return doc

    def test_an_identical_replicate_answers_aa_ok_and_writes_nothing(self):
        base = self.write("base", score_doc(self.base_cells))
        rep = self.write("rep", score_doc(self.base_cells, mid="replicate"))
        doc = self.aa(base, rep)
        self.assertEqual((doc["answer"], doc["ok"], doc["failed"]), ("aa ok", True, []))
        self.assertEqual(doc["schema"], "cf-bench-aa/1")
        agree = doc["aa"]["agreement"]
        self.assertEqual(sorted(agree["metrics"]), ["G3.fnf_rate", "G3.overclaim_rate"])
        self.assertTrue(all(row["ok"] and row["lo"] == row["hi"] == 0.0
                            for row in agree["metrics"].values()))
        self.assertTrue(agree["cost"]["ok"])
        self.assertEqual((agree["tolerance"], agree["cost_tolerance"], agree["level"]),
                         (compare_mod.AA_TOLERANCE, compare_mod.AA_COST_TOLERANCE, 0.95))
        self.assertFalse(doc["aa"]["accepted"])
        self.assertEqual(doc["configuration"]["compared"], list(compare_mod.CONFIG_PINS))
        self.assertFalse(self.lay.ledger_jsonl.exists(), "the A/A check spent alpha")

    def test_a_clearly_worse_replicate_fails_where_the_rule_alone_let_it_pass(self):
        base = self.write("base", score_doc(self.base_cells))
        worse = self.write("worse", score_doc(self.worse_cells, mid="worse"))
        doc = self.aa(base, worse)
        self.assertFalse(doc["aa"]["accepted"])         # one-sided: the rule never accepts it
        self.assertEqual((doc["answer"], doc["failed"]), ("aa FAILS", ["G3.fnf_rate"]))
        row = doc["aa"]["agreement"]["metrics"]["G3.fnf_rate"]
        self.assertGreater(row["lo"], compare_mod.AA_TOLERANCE)
        # and in a comparison: a gain over a baseline whose replicate does not repeat it
        cand = self.write("cand", score_doc(self.better_cells))
        entry = self.compare(base, cand, worse)
        self.assertEqual(entry["decision"], "reject")
        self.assertIn("aa", entry["failed"])
        self.assertEqual(entry["conditions"]["aa"]["agreement"]["disagree"], ["G3.fnf_rate"])
        # the replicate that repeats it still lets the gain through
        rep = self.write("rep", score_doc(self.base_cells, mid="replicate"))
        self.assertEqual(self.compare(base, cand, rep)["decision"], "accept")

    def test_a_better_replicate_fails_both_halves(self):
        base = self.write("base", score_doc(self.base_cells))
        lucky = self.write("lucky", score_doc(self.better_cells, mid="lucky"))
        doc = self.aa(base, lucky)
        self.assertEqual(doc["failed"], ["rule", "G3.fnf_rate"])
        self.assertEqual(doc["answer"], "aa FAILS")

    def test_a_difference_inside_its_sampling_noise_agrees(self):
        # two clusters of forty flip: a point difference of 0.05, past the tolerance, but
        # an interval that reaches zero, so the runs do not disagree
        noisy = dict(self.base_cells, c00=[1.0, 1.0], c02=[1.0, 1.0])
        base = self.write("base", score_doc(self.base_cells))
        rep = self.write("rep", score_doc(noisy, mid="noisy"))
        doc = self.aa(base, rep)
        row = doc["aa"]["agreement"]["metrics"]["G3.fnf_rate"]
        self.assertAlmostEqual(row["replicate"] - row["baseline"], 0.05)
        self.assertLessEqual(row["lo"], compare_mod.AA_TOLERANCE)
        self.assertEqual(doc["answer"], "aa ok", doc["failed"])

    def test_a_cost_that_does_not_repeat_fails_the_agreement(self):
        base = self.write("base", score_doc(self.base_cells, cost=1.0))
        dear = self.write("dear", score_doc(self.base_cells, cost=1.5, mid="dear"))
        doc = self.aa(base, dear)
        self.assertEqual(doc["failed"], ["cost"])
        self.assertAlmostEqual(doc["aa"]["agreement"]["cost"]["relative_lo"], 0.5)
        near = self.write("near", score_doc(self.base_cells, cost=1.1, mid="near"))
        self.assertEqual(self.aa(base, near)["answer"], "aa ok")
        # two cost bases cannot be compared at all
        other = score_doc(self.base_cells, mid="basis")
        other["cost"] = {"basis": "receipt spread"}
        doc = self.aa(base, self.write("basis", other))
        self.assertEqual(doc["failed"], ["cost"])
        self.assertIn("different bases", doc["aa"]["agreement"]["cost"]["why"])

    def test_an_error_rate_one_run_never_risked_is_a_rate_of_zero_there(self):
        base_doc = score_doc(self.base_cells)
        del base_doc["per_cluster"]["G3"]["overclaim_rate"]
        base = self.write("base", base_doc)
        over = {c: [1.0, 1.0] for c in self.base_cells}
        doc = self.aa(base, self.write("over", score_doc(self.base_cells, overclaim=over,
                                                         mid="over")))
        self.assertEqual(doc["failed"], ["G3.overclaim_rate"])
        self.assertIn("never risked", doc["aa"]["agreement"]["metrics"]["G3.overclaim_rate"]["why"])
        clean = {c: [0.0, 1.0] for c in self.base_cells}
        self.assertEqual(self.aa(base, self.write("clean", score_doc(
            self.base_cells, overclaim=clean, mid="clean")))["answer"], "aa ok")
        # a rate that is no error rate, measured by one run alone, has nothing to compare
        rep = score_doc(self.base_cells, mid="p1")
        rep["per_cluster"]["G3"]["precision_at_1"] = {c: [1.0, 1.0] for c in self.base_cells}
        doc = self.aa(base, self.write("p1", rep))
        self.assertTrue(doc["aa"]["agreement"]["metrics"]["G3.precision_at_1"]["ok"])

    def test_another_planning_key_is_refused_by_name(self):
        base = self.write("base", self.challenged("base", "chl1"))
        rep = self.write("rep", self.challenged(
            "rep", "chl2", plan={**CHALLENGE_PLAN, "strong_sample_rate": 1.0}))
        for check in (lambda: self.aa(base, rep), lambda: compare_mod.check_replicate(
                compare_mod.load_score(base), compare_mod.load_score(rep))):
            with self.assertRaises(CfError) as ctx:
                check()
            self.assertIn("planning differ (challenge strong_sample_rate (--strong-sample-rate): "
                          "0.5 in the baseline, 1.0 in the replicate)", str(ctx.exception))
            self.assertIn("planning flags", ctx.exception.hint or "")
        # a run planned before a key existed records none: unrecorded, by name
        old = {k: v for k, v in CHALLENGE_PLAN.items() if k != "absence_sample_rate"}
        with self.assertRaises(CfError) as ctx:
            self.aa(self.write("old", self.challenged("old", "chl0", plan=old)), base)
        self.assertIn("challenge absence_sample_rate (--absence-sample-rate): unrecorded in the "
                      "baseline, 0.25 in the replicate", str(ctx.exception))

    def test_another_challenge_seed_is_recorded_and_compared_by_nothing(self):
        base = self.write("base", self.challenged("base", "chl1", seed=0))
        rep = self.write("rep", self.challenged("rep", "chl2", seed=7))
        doc = self.aa(base, rep)
        self.assertEqual(doc["answer"], "aa ok", doc["failed"])
        self.assertEqual(doc["recorded"]["challenge_seed"],
                         {"baseline": {"chl1": 0}, "replicate": {"chl2": 7}})

    def test_the_replicate_is_refused_as_every_comparison_and_replicate_is(self):
        base = self.write("base", score_doc(self.base_cells))
        with self.assertRaises(CfError) as ctx:
            self.aa(base, base)
        self.assertIn("own run", str(ctx.exception))
        for name, kw, needle in (("model", {"models": ("m2",)}, "new baseline"),
                                 ("prompts", {"pins": {"prompts": {"loc-prompts": {"x": "y"}}}},
                                  "prompts differ")):
            with self.subTest(name=name), self.assertRaises(CfError) as ctx:
                self.aa(base, self.write(name, score_doc(self.base_cells, mid=name, **kw)))
            self.assertIn(needle, str(ctx.exception))
        subset = score_doc(self.base_cells, mid="subset")
        subset.update(subset=True, subset_articles=["a"], subset_sha256="0" * 64)
        with self.assertRaises(CfError) as ctx:
            self.aa(base, self.write("subset", subset))
        self.assertIn("subset score", str(ctx.exception))
        with self.assertRaises(CfError):
            self.aa(base, self.write("rep", score_doc(self.base_cells, mid="r")), alpha=0.5)

    def run_cf(self, *argv) -> tuple[int, str]:
        """``cf`` in-process: its exit code and output, a refusal as ``cli()`` reports it."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = cf_main.main(["--workspace", str(self.world.root), *map(str, argv)])
            except CfError as exc:
                return exc.exit_code, out.getvalue() + f"error: {exc}\n"
        return code, out.getvalue()

    def test_the_cli_answers_aa_ok_or_aa_fails(self):
        write_yaml(self.lay.bench_yaml, {"schema": "cf-bench/1", "id": "b1"})
        base = self.write("base", score_doc(self.base_cells))
        rep = self.write("rep", score_doc(self.base_cells, mid="replicate"))
        worse = self.write("worse", score_doc(self.worse_cells, mid="worse"))
        aa = ("bench", "compare", "b1", base, "--claim", "G3.fnf_rate", "--bootstrap", 1000)
        code, out = self.run_cf(*aa, "--aa", rep)
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("aa ok: replicate "), out)
        self.assertIn("nothing written", out)
        code, out = self.run_cf(*aa, "--aa", worse)
        self.assertEqual(code, 1, out)
        self.assertTrue(out.startswith("aa FAILS"), out)
        self.assertIn("disagrees: G3.fnf_rate", out)
        code, out = self.run_cf(*aa, "--aa", rep, "--json")
        self.assertEqual((code, json.loads(out)["answer"]), (0, "aa ok"))
        self.assertFalse(self.lay.ledger_jsonl.exists())
        # no candidate and no replicate is no comparison; a replication claims something
        code, out = self.run_cf(*aa)
        self.assertEqual(code, 2)
        self.assertIn("needs a candidate score, or --aa", out)
        code, out = self.run_cf(*aa, "--aa", rep, "--replication", "b0:loop-0")
        self.assertEqual(code, 2)
        self.assertIn("claims nothing", out)
        # the full comparison names the metrics its replicate disagrees on
        cand = self.write("cand", score_doc(self.better_cells))
        code, out = self.run_cf("bench", "compare", "b1", base, cand, "--claim", "G3.fnf_rate",
                                "--aa", worse, "--bootstrap", 1000)
        self.assertEqual(code, 0, out)
        self.assertIn("REJECT", out)
        self.assertIn("aa: the replicate disagrees with the baseline on G3.fnf_rate", out)


if __name__ == "__main__":
    unittest.main()
