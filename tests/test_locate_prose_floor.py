"""The prose floor (gate rule 8 under ``prose_floor_rule`` 1): no paragraph left out silently.

New code, for references/locate-documents.md sections 2 and 9 and docs/design/stage_map.md
section 4.0 (the versioned keys).  The smoke-4 audit found two statements whose whole
paragraphs were formula-free (``math: false``), held by no passage and no skip, so the
coverage floor, which reads only math paragraphs, never asked for a record.  Under the
prose floor every other ``paragraphs.json`` block outside the back matter with at least
``PROSE_FLOOR_MIN_WORDS`` words is covered too, History included; a short block and the
back matter are not; a gate.json without the key gates as before; the receipt counts the
prose under skips; the planner writes the key into an inventory's gate.json and nowhere
else; the worker prompt says it.  The page is ``tests/_locate_fixtures.py``'s made-up
"Widget gaps".
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
for path in (SCRIPTS, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import _locate_fixtures as F  # noqa: E402
import test_locate_gate_statement_cut as SC  # noqa: E402
import test_verify_locate as G  # noqa: E402
from cflib import vocab  # noqa: E402
from harvest import gate_corpus  # noqa: E402

vl = G.vl
TASK = "t-inv-0001"
FLOOR = {**SC.CUT_3, "prose_floor_rule": vocab.PROSE_FLOOR_RULE}


class TheFloorsNamesAreCflibs(unittest.TestCase):

    def test_the_gate_inlines_cflibs_values(self):
        pairs = {"PROSE_FLOOR_FIRST": vocab.PROSE_FLOOR_FIRST,
                 "PROSE_FLOOR_RULE": vocab.PROSE_FLOOR_RULE,
                 "PROSE_FLOOR_MIN_WORDS": vocab.PROSE_FLOOR_MIN_WORDS,
                 "BACK_MATTER": vocab.AUDIT_BACK_MATTER}
        for name, theirs in pairs.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(vl, name), theirs)
        self.assertEqual(vl.KNOWN_PROSE_FLOOR_RULE, vocab.PROSE_FLOOR_RULE)

    def test_which_blocks_the_floor_reads(self):
        para = {"math": False, "section": ["History"]}
        eight = "one two three four five six seven eight"
        self.assertTrue(vl.prose_floor_applies(para, eight))
        self.assertFalse(vl.prose_floor_applies(para, "one two three four five six seven"))
        self.assertFalse(vl.prose_floor_applies({**para, "math": True}, eight))
        for heading in ("References", " see also ", "External links", "Notes and references"):
            with self.subTest(heading=heading):
                self.assertFalse(vl.prose_floor_applies({"math": False, "section": [heading]},
                                                        eight))
        # the lead and a section named like the back matter below its top are the body
        self.assertTrue(vl.prose_floor_applies({"math": False, "section": []}, eight))
        self.assertTrue(vl.prose_floor_applies({"math": False,
                                                "section": ["Proof", "References"]}, eight))


class FloorCase(G.GateTestCase):
    """The Widget-gaps page in the gate fixture, with no finder in the task."""

    def setUp(self):
        self.fx = SC.CutFixture(self, layer=F.GAP_LAYER, formulas=F.GAP_FORMULAS,
                                paragraphs=F.GAP_PARAGRAPHS)
        self.fx.manifest(TASK, {"source_id": G.SID, "source_sha": G.HTML_SHA,
                                "layer_sha256": self.fx.layer_sha, "renderer": "web-html/1",
                                "finders": []})

    def gate(self, body: dict, keys: dict = FLOOR) -> dict:
        self.fx.versions(**keys)
        return self.fx.run(self.fx.document(TASK, body))

    def stats(self, result: dict) -> dict:
        return next(s for s in result["stats"] if s.get("mode") == "locate-inventory")

    def prose_problems(self, result: dict) -> list[str]:
        return [p for p in result["problems"] if "(the prose floor)" in p]


class TestTheProseFloor(FloorCase):

    def test_a_body_every_paragraph_of_which_is_held_passes(self):
        for blocks in ("cover", "skip"):
            for history in ("cover", "skip"):
                with self.subTest(blocks=blocks, history=history):
                    self.assertOk(self.gate(F.gap_body(blocks=blocks, history=history)))

    def test_a_formula_free_paragraph_inside_a_long_gap_is_refused(self):
        """The CFSG shape: three formula-free paragraphs, the middle one a statement in
        words, left between two passages with neither a passage nor a skip."""
        result = self.gate(F.gap_body(blocks="silent"))
        self.assertRule(result, 8, "(the prose floor)")
        named = self.prose_problems(result)
        self.assertEqual(len(named), 3, named)
        for name in ("BB1", "BB2", "BB3"):
            with self.subTest(block=name):
                self.assertTrue(any(f"the paragraph at {F.GAP_SPANS[name]} (section "
                                    f"['Building blocks'])" in p for p in named), named)
        self.assertTrue(all("expository` when it states nothing" in p for p in named))
        # the math floor has nothing to say of them: none is a math paragraph
        self.assertFalse([p for p in result["problems"] if "(the coverage floor)" in p])

    def test_history_is_not_exempt(self):
        result = self.gate(F.gap_body(history="silent"))
        self.assertRule(result, 8, f"the paragraph at {F.GAP_SPANS['HIST']} (section "
                                   f"['History'])")
        self.assertEqual(len(self.prose_problems(result)), 1)

    def test_a_short_block_and_the_back_matter_are_exempt(self):
        body = F.gap_body()
        body["skips"] = [s for s in body["skips"] if s["reason"] != "references"]
        result = self.gate(body)
        self.assertOk(result)
        self.assertLess(len(F.GAP_LAYER[slice(*F.GAP_SPANS["SHORT"])].split()),
                        vocab.PROSE_FLOOR_MIN_WORDS)
        self.assertGreaterEqual(len(F.GAP_LAYER[slice(*F.GAP_SPANS["REF1"])].split()),
                                vocab.PROSE_FLOOR_MIN_WORDS)

    def test_a_partly_covered_paragraph_is_held(self):
        """MOD1's first sentence is a passage and its notation sentence none: the floor asks
        whether a passage or a skip overlaps the block, and the run left over is the gap
        audit's (audit rule 3), not the gate's."""
        body = F.gap_body()
        mod1 = next(p for p in body["passages"] if p["span"][0] == F.GAP_SPANS["MOD1"][0])
        self.assertLess(mod1["span"][1], F.GAP_SPANS["MOD1"][1])
        self.assertOk(self.gate(body))

    def test_a_gate_json_without_the_key_gates_as_before(self):
        for body in (F.gap_body(blocks="silent"), F.gap_body(history="silent")):
            result = self.gate(body, SC.CUT_3)
            self.assertOk(result)
            self.assertNotIn("prose_paragraphs", self.stats(result)["skipped"])
        self.assertEqual(vl.GateConfig(self.fx.gate).prose_floor_rule, 0)

    def test_the_receipt_counts_the_prose_under_skips(self):
        result = self.gate(F.gap_body(blocks="skip", history="skip"))
        self.assertOk(result)
        skipped = self.stats(result)["skipped"]
        self.assertEqual(skipped["prose_paragraphs"], 4)
        self.assertEqual(skipped["math_paragraphs"], 0)
        result = self.gate(F.gap_body())
        self.assertEqual(self.stats(result)["skipped"]["prose_paragraphs"], 1)

    def test_a_version_the_gate_does_not_know_is_a_broken_gate_json(self):
        self.fx.versions(**SC.CUT_3, prose_floor_rule=vocab.PROSE_FLOOR_RULE + 1)
        with self.assertRaises(vl.GateBroken):
            self.fx.gate_cfg()
        self.fx.versions(**SC.CUT_3, prose_floor_rule="1")
        with self.assertRaises(vl.GateBroken):
            self.fx.gate_cfg()


class TestTheReceiptsSkippedBlock(unittest.TestCase):

    def test_prose_paragraphs_are_summed_only_when_a_gate_counted_them(self):
        old = [{"source_id": "a", "skipped": {"skips": 1, "chars": 9, "math_paragraphs": 1,
                                             "finder_passages": 0}}]
        summary = gate_corpus.skipped_summary(old)
        self.assertNotIn("prose_paragraphs", summary)
        self.assertNotIn("prose_paragraphs", summary["by_source"]["a"])
        new = old + [{"source_id": "b", "skipped": {"skips": 2, "chars": 30,
                                                   "math_paragraphs": 0, "finder_passages": 0,
                                                   "prose_paragraphs": 3}}]
        summary = gate_corpus.skipped_summary(new)
        self.assertEqual(summary["prose_paragraphs"], 3)
        self.assertEqual(summary["by_source"]["b"]["prose_paragraphs"], 3)
        self.assertEqual(summary["by_source"]["a"]["prose_paragraphs"], 0)

    def test_the_summary_line(self):
        base = {"preset": "locate-inventory", "sources": ["a"], "files": 1, "ok": True,
                "problems": []}
        old = gate_corpus.render({**base, "skipped": {"skips": 1, "chars": 9,
                                                      "math_paragraphs": 1,
                                                      "finder_passages": 0}})
        self.assertIn("1 math paragraph(s) and 0 finder passage(s)", old)
        new = gate_corpus.render({**base, "skipped": {"skips": 1, "chars": 9,
                                                      "math_paragraphs": 1,
                                                      "finder_passages": 0,
                                                      "prose_paragraphs": 2}})
        self.assertIn("1 math paragraph(s), 2 other paragraph(s) and 0 finder passage(s)", new)


class TestThePlannerWritesTheKey(unittest.TestCase):

    def test_an_inventory_names_it_and_a_locate_run_does_not(self):
        from test_locate_planner import scratch

        root = scratch(self)
        paths = F.campaign(self, root)
        inventory = json.loads(paths.gate_json("inv-1").read_text(encoding="utf-8"))
        self.assertEqual(inventory["prose_floor_rule"], vocab.PROSE_FLOOR_RULE)
        F.plan_locate(self, root, run_id="loc-1")
        located = json.loads(paths.gate_json("loc-1").read_text(encoding="utf-8"))
        self.assertNotIn("prose_floor_rule", located)


if __name__ == "__main__":
    unittest.main()
