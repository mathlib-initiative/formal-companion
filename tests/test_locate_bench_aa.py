"""The A/A check of the acceptance rule: a replicate of the baseline's configuration
whose outcomes repeat the baseline's.

New code, for references/locate-bench.md section 9, rule 5 (docs/proposals/
wikipedia-locate.md section 9, step 3: "the A/A run rejects").  The rule's own
A/A condition is one-sided -- the rule applied to the baseline and its replicate
must not accept -- so a replicate that came out clearly WORSE, or that differs on
a metric the claim does not name, passed it.  These tests pin the agreement half
(every metric of every tier both scores hold, and the cost per article, not
shown to differ past a stated tolerance on the paired interval), what each row's
verdict says (vocab.BENCH_AA_VERDICTS: the burden of proof is on the difference,
so a small A/A passes a large difference ``unresolved`` and says so), the
planning refusal by name, the challenge seed that is recorded and compared by
nothing, and ``cf bench compare <id> <baseline> --aa <replicate>``, the check
alone, which answers ``aa ok`` or ``aa FAILS`` and writes nothing, its candidate
given after an option still parsing on Python 3.11.  The score documents are
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
from bench import layout as layout_mod  # noqa: E402
from cflib import vocab  # noqa: E402
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
        self.assertEqual(doc["schema"], layout_mod.AA_SCHEMA)
        self.assertEqual(layout_mod.AA_SCHEMA, "cf-bench-aa/1")
        self.assertFalse(hasattr(compare_mod, "AA_SCHEMA") and compare_mod.AA_SCHEMA
                         is not layout_mod.AA_SCHEMA, "a second definition of the schema id")
        agree = doc["aa"]["agreement"]
        self.assertEqual(sorted(agree["metrics"]), ["G3.fnf_rate", "G3.overclaim_rate"])
        self.assertTrue(all(row["ok"] and row["lo"] == row["hi"] == row["difference"] == 0.0
                            and row["verdict"] == vocab.BENCH_AA_WITHIN
                            for row in agree["metrics"].values()))
        self.assertTrue(agree["cost"]["ok"])
        # identical outcomes: agreement within the tolerance is SHOWN, on every row
        self.assertEqual((agree["compared"], agree["within"], agree["unresolved"], agree["reach"]),
                         (3, ["G3.fnf_rate", "G3.overclaim_rate", "cost"], [], 0.0))
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
        self.assertEqual(row["verdict"], vocab.BENCH_AA_DIFFERS)
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

    def test_a_difference_inside_its_sampling_noise_passes_unresolved_not_within(self):
        # two clusters of forty flip: a point difference of 0.05, past the tolerance, but
        # an interval that reaches zero -- no difference shown, so the runs do not
        # disagree; and none ruled out, so agreement is not shown either
        noisy = dict(self.base_cells, c00=[1.0, 1.0], c02=[1.0, 1.0])
        base = self.write("base", score_doc(self.base_cells))
        rep = self.write("rep", score_doc(noisy, mid="noisy"))
        doc = self.aa(base, rep)
        agree = doc["aa"]["agreement"]
        row = agree["metrics"]["G3.fnf_rate"]
        self.assertAlmostEqual(row["difference"], 0.05)
        self.assertAlmostEqual(row["replicate"] - row["baseline"], 0.05)
        self.assertLessEqual(row["lo"], compare_mod.AA_TOLERANCE)
        self.assertGreater(row["hi"], compare_mod.AA_TOLERANCE)
        self.assertEqual((row["verdict"], row["ok"]), (vocab.BENCH_AA_UNRESOLVED, True))
        self.assertEqual(doc["answer"], "aa ok", doc["failed"])
        self.assertEqual(agree["unresolved"], ["G3.fnf_rate"])
        self.assertNotIn("G3.fnf_rate", agree["within"])

    def test_a_small_aa_passes_a_large_difference_and_says_it_resolved_nothing(self):
        """The burden of proof is on the difference: ten clusters whose fnf_rate goes from
        0.2 to 0.5 are not SHOWN to differ by more than 0.03, so the A/A answers aa ok --
        and its report says the row is unresolved, how far the interval reaches, and never
        that the metric is within 0.03."""
        base_cells = {f"c{i}": [1.0 if i < 2 else 0.0, 1.0] for i in range(10)}
        rep_cells = {f"c{i}": [1.0 if i < 5 else 0.0, 1.0] for i in range(10)}
        base = self.write("base", score_doc(base_cells))
        rep = self.write("rep", score_doc(rep_cells, mid="replicate"))
        doc = self.aa(base, rep)
        agree = doc["aa"]["agreement"]
        row = agree["metrics"]["G3.fnf_rate"]
        self.assertAlmostEqual(row["difference"], 0.3)
        self.assertEqual((doc["answer"], row["verdict"]), ("aa ok", vocab.BENCH_AA_UNRESOLVED))
        self.assertLessEqual(row["lo"], compare_mod.AA_TOLERANCE)
        self.assertGreaterEqual(agree["reach"], 0.3)
        self.assertEqual(agree["reach"], max(abs(row["lo"]), abs(row["hi"])))
        self.assertEqual(agree["unresolved"], ["G3.fnf_rate"])
        write_yaml(self.lay.bench_yaml, {"schema": "cf-bench/1", "id": "b1"})
        code, out = self.run_cf("bench", "compare", "b1", base, "--aa", rep, "--claim",
                                "G3.fnf_rate", "--bootstrap", 1000)
        self.assertEqual(code, 0, out)
        self.assertNotIn("within 0.03", out)
        self.assertIn("no metric shown to differ by more than 0.03", out)
        self.assertIn("agreement within the tolerance shown for 2 of 3, unresolved for 1", out)
        self.assertIn(f"up to {agree['reach']:.3g} possible", out)
        line = next(x for x in out.splitlines() if "G3.fnf_rate" in x and "difference" in x)
        self.assertEqual(line.split()[0], vocab.BENCH_AA_UNRESOLVED, out)
        self.assertIn(f"difference +0.3 in [{row['lo']:+.4g}, {row['hi']:+.4g}]", line)

    def test_a_cost_that_does_not_repeat_fails_the_agreement(self):
        base = self.write("base", score_doc(self.base_cells, cost=1.0))
        dear = self.write("dear", score_doc(self.base_cells, cost=1.5, mid="dear"))
        doc = self.aa(base, dear)
        self.assertEqual(doc["failed"], ["cost"])
        cost = doc["aa"]["agreement"]["cost"]
        self.assertAlmostEqual(cost["relative_lo"], 0.5)
        self.assertAlmostEqual(cost["relative_difference"], 0.5)
        self.assertEqual(cost["verdict"], vocab.BENCH_AA_DIFFERS)
        near = self.write("near", score_doc(self.base_cells, cost=1.1, mid="near"))
        doc = self.aa(base, near)
        self.assertEqual(doc["answer"], "aa ok")
        self.assertEqual(doc["aa"]["agreement"]["cost"]["verdict"], vocab.BENCH_AA_WITHIN)
        # 20% of nothing is nothing: over a baseline that cost nothing, any cost differs
        free = self.write("free", score_doc(self.base_cells, cost=0.0, mid="free"))
        doc = self.aa(free, self.write("paid", score_doc(self.base_cells, cost=0.1, mid="paid")))
        self.assertEqual((doc["failed"], doc["aa"]["agreement"]["cost"]["verdict"]),
                         (["cost"], vocab.BENCH_AA_DIFFERS))
        doc = self.aa(free, self.write("free2", score_doc(self.base_cells, cost=0.0, mid="f2")))
        self.assertEqual(doc["answer"], "aa ok")
        # two cost bases cannot be compared at all
        other = score_doc(self.base_cells, mid="basis")
        other["cost"] = {"basis": "receipt spread"}
        doc = self.aa(base, self.write("basis", other))
        self.assertEqual(doc["failed"], ["cost"])
        self.assertIn("different bases", doc["aa"]["agreement"]["cost"]["why"])
        self.assertIsNone(doc["aa"]["agreement"]["cost"]["verdict"])

    def test_an_error_rate_one_run_never_risked_is_a_rate_of_zero_there(self):
        base_doc = score_doc(self.base_cells)
        del base_doc["per_cluster"]["G3"]["overclaim_rate"]
        base = self.write("base", base_doc)
        over = {c: [1.0, 1.0] for c in self.base_cells}
        doc = self.aa(base, self.write("over", score_doc(self.base_cells, overclaim=over,
                                                         mid="over")))
        self.assertEqual(doc["failed"], ["G3.overclaim_rate"])
        row = doc["aa"]["agreement"]["metrics"]["G3.overclaim_rate"]
        self.assertIn("never risked", row["why"])
        self.assertEqual((row["difference"], row["verdict"]), (1.0, vocab.BENCH_AA_DIFFERS))
        clean = {c: [0.0, 1.0] for c in self.base_cells}
        self.assertEqual(self.aa(base, self.write("clean", score_doc(
            self.base_cells, overclaim=clean, mid="clean")))["answer"], "aa ok")
        # a rate that is no error rate, measured by one run alone, has nothing to compare
        rep = score_doc(self.base_cells, mid="p1")
        rep["per_cluster"]["G3"]["precision_at_1"] = {c: [1.0, 1.0] for c in self.base_cells}
        doc = self.aa(base, self.write("p1", rep))
        row = doc["aa"]["agreement"]["metrics"]["G3.precision_at_1"]
        self.assertTrue(row["ok"])
        self.assertIsNone(row["verdict"])
        self.assertNotIn("G3.precision_at_1", doc["aa"]["agreement"]["within"]
                         + doc["aa"]["agreement"]["unresolved"])

    def test_every_verdict_written_is_one_of_the_vocabulary(self):
        """CLAUDE.md S4: the verdicts are vocab.BENCH_AA_VERDICTS, and nothing else."""
        self.assertEqual(vocab.BENCH_AA_VERDICTS, {"differs", "within", "unresolved"})
        base = self.write("base", score_doc(self.base_cells))
        seen = set()
        for name, cells in (("same", self.base_cells), ("worse", self.worse_cells),
                            ("noisy", dict(self.base_cells, c00=[1.0, 1.0], c02=[1.0, 1.0]))):
            agree = self.aa(base, self.write(name, score_doc(cells, mid=name)))["aa"]["agreement"]
            seen |= {row["verdict"] for row in [*agree["metrics"].values(), agree["cost"]]}
        self.assertEqual(seen, vocab.BENCH_AA_VERDICTS)

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
        self.assertIn("FAILS shown to differ: G3.fnf_rate", out)
        line = next(x for x in out.splitlines() if "G3.fnf_rate" in x and "difference" in x)
        self.assertEqual(line.split()[0], vocab.BENCH_AA_DIFFERS, out)
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

    def parsed(self, *argv):
        parser, _mapping = cf_main.build_parser()
        return parser.parse_args(list(map(str, argv)))

    def test_a_candidate_given_after_an_option_still_parses(self):
        """An optional candidate must not cost the order that parsed while it was required:
        Python 3.11's argparse matched it, empty, with the first run of positionals, so a
        candidate after ``--claim`` was unrecognised there (3.12 parses it)."""
        for argv, candidate in (
                (("b1", "base", "cand", "--claim", "X", "--aa", "rep"), "cand"),
                (("b1", "base", "--claim", "X", "cand", "--aa", "rep"), "cand"),
                (("b1", "base", "--claim", "X", "--aa", "rep", "cand"), "cand"),
                (("b1", "base", "--aa", "rep", "--claim", "X"), None)):
            with self.subTest(argv=argv):
                args = self.parsed("bench", "compare", *argv)
                self.assertEqual(args.candidate, None if candidate is None else Path(candidate))
                self.assertEqual((args.baseline, args.aa), (Path("base"), Path("rep")))
        # a second candidate, or an option nobody declared, is still refused
        for argv in (("b1", "base", "cand", "--claim", "X", "more"),
                     ("b1", "base", "--claim", "X", "--frobnicate", "cand")):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                self.parsed("bench", "compare", *argv)
        # and the other bench subcommands take no positional back
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parsed("bench", "freeze", "b1", "--plant", "stray")


if __name__ == "__main__":
    unittest.main()
