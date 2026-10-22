"""The statement cut's gate: rule 10, span-end rule 2, judgment rules 3, the plan and the receipt.

New code, for docs/proposals/statement-cut.md (its "gate" package): section 3.1's
blocks and section 3.2's end rule as the gate reads them (``locate_text``), gate
rule 10 (section 3.3), rule 9 under judgment rules 3 (sections 1.3, 1.4 and the
gate rows of section 5), the receipt's ``cut`` block, and the planner's version
keys, manifests and refusals (sections 1.2 and 2.2).  Every new check binds only
a gate.json that names its version, so each test gates its document both ways:
under the statement cut's key, and as a run planned before it, which is gated as
it always was.  Every article, passage, declaration and phrase below is made up
(the "Widget theory" layer, libraries ``alpha`` and ``beta``).
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import hashlib
import json
import re
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
for path in (SCRIPTS, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import _locate_fixtures as F  # noqa: E402
import test_verify_locate as G  # noqa: E402
from cflib import vocab  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from cflib.yamlio import read_yaml  # noqa: E402
from gate import verify_locate as vl  # noqa: E402
from harvest import gate_corpus  # noqa: E402
from test_locate_planner import scratch, ws  # noqa: E402

lt = vl.lt

# --------------------------------------------------------------------------- #
# a layer with every kind of block section 3.1 reads
# --------------------------------------------------------------------------- #


def formula_text(tex: str) -> str:
    """A formula as the layer carries it: its fallback text, then its TeX annotation."""
    return f"{tex}{{\\displaystyle {tex}}}"


TITLE = "Widget theory"
DEF1 = "A widget is finite when it has finitely many sprockets."
THM1 = "Every finite widget has a spanning gadget."
P1 = f"{DEF1} {THM1}"
P2 = f"For every finite widget {formula_text('W')} the gadget count satisfies:"
D1 = formula_text("c(W)=2n")
P3 = f"Here {formula_text('n')} is the order of the widget."
L1 = "a gadget of a finite widget is unique up to conjugacy;"
L2 = "a gadget of an infinite widget need not exist."
BOX_A = f"Theorem. Every finite widget {formula_text('W')} has a spanning gadget."
BOX_B = "It is unique."
BOX = f"{BOX_A} {BOX_B}"
P4 = "It is unique."
HISTORY = "History The lemma was first stated in a synthetic note about widgets."
LAYER = "\n".join([TITLE, P1, P2, D1, P3, L1, L2, BOX, P4, HISTORY])
#: the same layer with P3 the words that finish P2's sentence after its display (a
#: lower-case first word of the same length, so every span and formula is the same)
P3_WHEN = "when" + P3[len("Here"):]
LAYER_WHEN = LAYER.replace(P3, P3_WHEN, 1)


def at(text: str, after: int = 0) -> list[int]:
    a = LAYER.index(text, after)
    return [a, a + len(text)]


SPANS = {"P1": at(P1), "P2": at(P2), "D1": at(D1), "P3": at(P3), "L1": at(L1), "L2": at(L2),
         "BOX": at(BOX), "P4": at(P4, at(BOX)[1]), "HISTORY": at(HISTORY)}
SPANS["DEF1"] = at(DEF1)
SPANS["THM1"] = at(THM1)
SPANS["BOX_B"] = at(BOX_B, SPANS["BOX"][0])


def formulas() -> list[dict]:
    out, cursor = [], 0
    for tex in ("W", "c(W)=2n", "n", "W"):
        text = formula_text(tex)
        a = LAYER.index(text, cursor)
        out.append({"span": [a, a + len(text)], "alttext": "{\\displaystyle " + tex + "}"})
        cursor = a + len(text)
    return out


FORMULAS = formulas()


def paragraphs() -> list[dict]:
    def block(name, kind, math):
        return {"span": SPANS[name], "section": [], "block": kind, "formulas": 0, "math": math}
    return [block("P1", "p", False), block("P2", "p", True), block("D1", "math", True),
            block("P3", "p", True), block("L1", "li", False), block("L2", "li", False),
            block("BOX", "p", True), block("BOX", "theorem_box", True),
            block("P4", "p", False), block("HISTORY", "p", False)]


PARAGRAPHS = sorted(paragraphs(), key=lambda p: (p["span"][0], -p["span"][1], p["block"]))

#: the passages of the good cut: (name, span, kind, prominence, scope, until, extra)
GOOD = [
    ("DEF1", SPANS["DEF1"], "definition", "supporting", "formal_math", "many sprockets.", {}),
    ("THM1", SPANS["THM1"], "theorem", "central", "formal_math", None, {}),
    ("P2", [SPANS["P2"][0], SPANS["D1"][1]], "theorem", "supporting", "formal_math",
     "{\\displaystyle c(W)=2n}", {}),
    ("P3", SPANS["P3"], "other", "supporting", "expository", None, {}),
    ("LIST", [SPANS["L1"][0], SPANS["L2"][1]], "theorem", "supporting", "formal_math",
     "need not exist.",
     {"conjuncts": [{"id": "c1", "text": "finite widgets have one gadget up to conjugacy"},
                    {"id": "c2", "text": "infinite widgets may lack one"}],
      "shape": "cases"}),
    ("BOX", SPANS["BOX"], "theorem", "central", "formal_math", None,
     {"conjuncts": [{"id": "c1", "text": "a finite widget has a spanning gadget"},
                    {"id": "c2", "text": "there is at most one such gadget"}],
      "shape": "existence_uniqueness"}),
    ("P4", SPANS["P4"], "other", "supporting", "expository", None, {}),
]


def head_ids(starts: list[int], layer: str = LAYER) -> dict[int, tuple[str, int]]:
    hm = lt.HeadMap(layer, FORMULAS)
    return {start: (f"In-text #{h}", w) for start, (_s, h, w)
            in zip(starts, lt.assign_ids(hm.head, [hm.to_head(s) for s in starts]))}


def passage(name: str, span, kind, prominence, scope, until, extra, ids,
            layer: str = LAYER) -> dict:
    item, words = ids[span[0]]
    row = {"item": item, "head_words": words, "span": list(span),
           "quote": {"exact": " ".join(layer[span[0]:span[1]].split(" ")[:4]),
                     "prefix": layer[max(0, span[0] - 12):span[0]], "suffix": ""},
           "section": [], "fragment": None, "passage_kind": kind, "prominence": prominence,
           "scope": scope, "statement": f"a synthetic restatement of the {name.lower()} passage",
           "concept": None, "finder": "theorem_box" if name == "BOX" else "model",
           **copy.deepcopy(extra)}
    if until is not None:
        row["until"] = until
    return row


def cut_body(rows=None, layer: str = LAYER) -> dict:
    rows = GOOD if rows is None else rows
    ids = head_ids([r[1][0] for r in rows], layer)
    return {"passages": [passage(*r, ids, layer) for r in rows],
            "skips": [{"span": SPANS["HISTORY"], "reason": "history"}], "notes": ""}


def replace(name: str, **changes) -> list:
    """GOOD with one passage's fields replaced (``span``, ``until``, ``extra``, ...)."""
    keys = ("name", "span", "kind", "prominence", "scope", "until", "extra")
    out = []
    for row in GOOD:
        if row[0] == name:
            values = dict(zip(keys, row))
            values.update(changes)
            row = tuple(values[k] for k in keys)
        out.append(row)
    return out


class CutFixture(G.Fixture):
    """test_verify_locate's gate fixture over the Widget theory layer above."""

    def __init__(self, case, mode="locate-inventory", layer: str = LAYER, **kwargs):
        super().__init__(case, mode, curated=False, **kwargs)
        sdir = Path(self.gate["sourceset"]["sources_dir"]) / G.SID
        (sdir / "layer.txt").write_text(layer, encoding="utf-8")
        (sdir / "formulas.json").write_text(json.dumps(FORMULAS), encoding="utf-8")
        (sdir / "paragraphs.json").write_text(json.dumps(PARAGRAPHS), encoding="utf-8")
        self.layer_sha = hashlib.sha256(layer.encode("utf-8")).hexdigest()
        self.gate["sources"][G.SID]["layer_sha256"] = self.layer_sha
        self.write_gate()

    def versions(self, **keys) -> None:
        for key in ("judgment_rules", "span_end_rule", "statement_cut_rule"):
            self.gate.pop(key, None)
        self.gate.update({k: v for k, v in keys.items() if v is not None})
        self.write_gate()


#: the version keys of a run planned under the statement cut's first versions (every run
#: planned from its flip on so far: inv-pilot-1's), of one planned under the continued
#: cut (span-end rule 3, statement-cut rule 2), and of one planned before the flip
#: (span-end rule 1, no rule 10).  Spelled out, so a test of one version never moves
#: with what the planner writes
CUT_1 = {"span_end_rule": 2, "statement_cut_rule": 1}
CUT_2 = {"span_end_rule": 3, "statement_cut_rule": 2}
BEFORE = {"span_end_rule": 1}


class InventoryCase(G.GateTestCase):

    TASK = "t-inv-0001"
    #: the layer the case's source carries
    LAYER = LAYER

    def setUp(self):
        self.fx = CutFixture(self, layer=self.LAYER)
        self.fx.manifest(self.TASK, {
            "source_id": G.SID, "source_sha": G.HTML_SHA, "layer_sha256": self.fx.layer_sha,
            "renderer": "web-html/1",
            "finders": [{"finder": "theorem_box", "start": SPANS["BOX"][0],
                         "span": SPANS["BOX"], "section": []}]})

    def gate(self, body: dict, keys: dict) -> dict:
        self.fx.versions(**keys)
        return self.fx.run(self.fx.document(self.TASK, body))

    def stats(self, result: dict) -> dict:
        return next(s for s in result["stats"] if s.get("mode") == "locate-inventory")

    def assertCutOnly(self, body: dict, needle: str, rule: int = 10):
        """Refused under the statement cut, and passed as a run planned before it."""
        self.assertRule(self.gate(body, CUT_1), rule, needle)
        self.assertOk(self.gate(body, BEFORE))


# --------------------------------------------------------------------------- #
# section 3.1: the blocks, as locate_text reads them
# --------------------------------------------------------------------------- #

class TestTheFixturesBlocks(unittest.TestCase):
    """The Widget theory layer reads as these tests say (the cut package's
    locate_text.Blocks; its own tests are tests/test_locate_statement_cut.py)."""

    def setUp(self):
        self.blocks = lt.Blocks(LAYER, PARAGRAPHS, FORMULAS)

    def test_a_box_is_a_box_and_the_title_is_in_no_block(self):
        blk = self.blocks.block_at(SPANS["BOX_B"][0])
        self.assertEqual(blk.span, tuple(SPANS["BOX"]))
        self.assertEqual(set(blk.kinds), {"p", "theorem_box"})
        self.assertIsNone(self.blocks.block_at(0))

    def test_the_extents_the_gate_tests_rest_on(self):
        self.assertEqual(self.blocks.extent(SPANS["P2"][0]), SPANS["D1"][1])
        self.assertEqual(self.blocks.extent(SPANS["DEF1"][0]), SPANS["P1"][1])
        self.assertEqual(self.blocks.extent(SPANS["BOX_B"][0]), SPANS["BOX"][1])
        self.assertEqual(self.blocks.extent(SPANS["L1"][0]), SPANS["L1"][1])
        self.assertEqual(self.blocks.extent(SPANS["L1"][0], compound=True), SPANS["L2"][1])
        self.assertIsNone(self.blocks.extent(0))

    def test_the_ends_under_rule_2(self):
        # the next start is the earlier, so the space before it is in neither passage
        self.assertEqual(lt.passage_end(LAYER, self.blocks, SPANS["DEF1"][0], "A widget is",
                                        rule=2, next_start=SPANS["THM1"][0]),
                         SPANS["DEF1"][1])
        self.assertEqual(LAYER[SPANS["DEF1"][1]:SPANS["THM1"][0]], " ")
        self.assertEqual(lt.passage_end(LAYER, PARAGRAPHS, SPANS["DEF1"][0], "A widget is"),
                         SPANS["P1"][1])
        self.assertEqual(lt.passage_end(LAYER, self.blocks, SPANS["P2"][0], "For every",
                                        rule=2), SPANS["D1"][1])

    def test_the_sentences_counted(self):
        count = lambda span: len(self.blocks.units(span)["sentences"])  # noqa: E731
        self.assertEqual(count(SPANS["P1"]), 2)
        self.assertEqual(count(SPANS["BOX"]), 3)
        self.assertEqual(count([SPANS["L1"][0], SPANS["L2"][1]]), 1)
        self.assertEqual(self.blocks.units([SPANS["P2"][0], SPANS["D1"][1]])["displays"],
                         [SPANS["D1"]])


# --------------------------------------------------------------------------- #
# gate.json: the keys and their dependence
# --------------------------------------------------------------------------- #

class TestTheVersionKeys(InventoryCase):

    def test_the_gate_knows_the_cut_and_binds_only_what_gate_json_names(self):
        self.fx.versions(**CUT_1)
        cfg = self.fx.gate_cfg()
        self.assertEqual((cfg.span_end_rule, cfg.statement_cut_rule), (2, 1))
        self.fx.versions()
        cfg = self.fx.gate_cfg()
        self.assertEqual((cfg.judgment_rules, cfg.span_end_rule, cfg.statement_cut_rule),
                         (0, 0, 0))

    def test_the_cut_without_its_end_rule_cannot_run(self):
        for end in (None, 1):
            self.fx.versions(span_end_rule=end, statement_cut_rule=1)
            with self.subTest(end=end), self.assertRaises(vl.GateBroken) as caught:
                self.fx.gate_cfg()
            self.assertIn("needs span-end rule 2", str(caught.exception))

    def test_a_newer_or_malformed_cut_rule_cannot_run(self):
        for value, needle in ((vl.KNOWN_STATEMENT_CUT_RULE + 1, "this gate knows"),
                              ("1", "not a version number"), (True, "not a version number")):
            self.fx.versions(span_end_rule=2, statement_cut_rule=value)
            with self.subTest(value=value), self.assertRaises(vl.GateBroken) as caught:
                self.fx.gate_cfg()
            self.assertIn(needle, str(caught.exception))

    def test_the_known_versions_follow_cflib(self):
        # the inlined names themselves are pinned in test_verify_locate
        self.assertEqual(vl.KNOWN_STATEMENT_CUT_RULE,
                         max(vocab.STATEMENT_CUT_RULE, vocab.STATEMENT_CUT_CONTINUED))
        self.assertEqual(vl.KNOWN_JUDGMENT_RULES,
                         max(vocab.JUDGMENT_RULES, vocab.RULES_STATEMENT_CUT))
        self.assertEqual(vl.KNOWN_SPAN_END_RULE,
                         max(vocab.SPAN_END_RULE, vocab.SPAN_END_CONTINUED))
        # the gate never knows a span-end rule the end it recomputes would refuse
        self.assertEqual(vl.KNOWN_SPAN_END_RULE, max(lt.SPAN_END_RULES))
        self.assertEqual(sorted(vl.STATEMENT_CUT_SPAN_END),
                         list(range(1, vl.KNOWN_STATEMENT_CUT_RULE + 1)))

    def test_each_statement_cut_rule_reads_one_span_end_rule(self):
        """The pairs runs planned so far name stay admitted; every other is exit 3."""
        for keys in ({}, {"span_end_rule": 1}, {"span_end_rule": 2}, CUT_1, CUT_2,
                     {"judgment_rules": 3, **CUT_2}):
            self.fx.versions(**keys)
            with self.subTest(keys=keys):
                cfg = self.fx.gate_cfg()
                self.assertEqual((cfg.span_end_rule, cfg.statement_cut_rule),
                                 (keys.get("span_end_rule", 0),
                                  keys.get("statement_cut_rule", 0)))
        for end, cut, needle in (
                (3, 1, "statement-cut rule 1 reads span-end rule 2"),
                (2, 2, "statement-cut rule 2 reads span-end rule 3"),
                (1, 2, "statement-cut rule 2 reads span-end rule 3"),
                (None, 2, "statement-cut rule 2 reads span-end rule 3"),
                (3, None, "span_end_rule 3 without statement_cut_rule 2"),
                (None, 1, "needs span-end rule 2"),
                (2, vl.KNOWN_STATEMENT_CUT_RULE + 1, "this gate knows"),
                (vl.KNOWN_SPAN_END_RULE + 1, 2, "this gate knows")):
            self.fx.versions(span_end_rule=end, statement_cut_rule=cut)
            with self.subTest(end=end, cut=cut), self.assertRaises(vl.GateBroken) as caught:
                self.fx.gate_cfg()
            self.assertIn(needle, str(caught.exception))


# --------------------------------------------------------------------------- #
# rule 2 under span-end rule 2 (section 3.2)
# --------------------------------------------------------------------------- #

class TestTheEndRule2(InventoryCase):

    def test_the_good_cut_passes_both_ways(self):
        self.assertOk(self.gate(cut_body(), CUT_1))
        self.assertOk(self.gate(cut_body(), BEFORE))
        self.assertOk(self.gate(cut_body(), {}))

    def test_a_passage_without_until_ends_at_the_next_ones_start(self):
        # ... less the separator before that start, which is in neither passage
        body = cut_body(replace("DEF1", span=SPANS["DEF1"], until=None))
        self.assertOk(self.gate(body, CUT_1))
        result = self.gate(body, BEFORE)           # rule 1 ends it at the paragraph's end
        self.assertRule(result, 2, "the end of the paragraph it starts in")
        glued = cut_body(replace("DEF1", span=[SPANS["DEF1"][0], SPANS["THM1"][0]], until=None))
        self.assertRule(self.gate(glued, CUT_1), 2, "the separator before the next passage")

    def test_a_passage_without_until_runs_into_the_display_it_introduces(self):
        body = cut_body(replace("P2", until=None))
        self.assertOk(self.gate(body, CUT_1))
        self.assertRule(self.gate(body, BEFORE), 2, "the end of the paragraph it starts in")
        short = cut_body(replace("P2", span=SPANS["P2"], until=None))
        short["skips"].append({"span": SPANS["D1"], "reason": "a display kept by no passage"})
        self.assertRule(self.gate(short, CUT_1), 2, "the displays its last sentence introduces")
        self.assertOk(self.gate(short, BEFORE))

    def test_the_paragraph_end_over_the_next_passage_is_refused_under_the_cut(self):
        body = cut_body(replace("DEF1", span=SPANS["P1"], until=None))
        body["passages"] = [p for p in body["passages"]
                            if p["span"][0] != SPANS["THM1"][0]]
        self.assertOk(self.gate(body, BEFORE))
        body = cut_body(replace("DEF1", span=SPANS["P1"], until=None))
        result = self.gate(body, CUT_1)
        self.assertRule(result, 2, "the separator before the next passage")
        self.assertRule(result, 10, "shares text with")


# --------------------------------------------------------------------------- #
# rule 10 (section 3.3)
# --------------------------------------------------------------------------- #

class TestRule10(InventoryCase):

    def test_overlap_and_containment(self):
        # DEF1 ended by `until` past THM1's start: two spans share text
        body = cut_body(replace("DEF1", span=SPANS["P1"], until="spanning gadget."))
        self.assertCutOnly(body, "shares text with")

    def test_a_span_runs_into_the_next_block_only_where_the_cut_allows(self):
        # a simple statement over two list items
        body = cut_body(replace("LIST", extra={}))
        self.assertCutOnly(body, "only inside a theorem box")
        # an expository sentence run into the list
        rows = [r for r in GOOD if r[0] != "LIST"]
        rows = [(*r[:5], "need not exist.", r[6]) if r[0] == "P3" else r for r in rows]
        rows = [(r[0], [SPANS["P3"][0], SPANS["L2"][1]], *r[2:]) if r[0] == "P3" else r
                for r in rows]
        self.assertCutOnly(cut_body(rows), "only inside a theorem box")

    def test_a_display_is_never_cut(self):
        inside = LAYER.index("{\\displaystyle c(W)")
        body = cut_body(replace("P2", span=[SPANS["P2"][0], inside + len("{\\displaystyle c(W)")],
                                until="{\\displaystyle c(W)"))
        self.assertCutOnly(body, "inside the formula")

    def test_conjuncts_are_none_or_two_to_five_of_a_closed_shape(self):
        two = copy.deepcopy(GOOD[4][6]["conjuncts"])
        six = [{"id": f"c{k}", "text": f"a widget fact number {k}"} for k in range(1, 7)]
        cases = {
            "never [] or null": {"conjuncts": [], "shape": "cases"},
            "never [] or null ": {"conjuncts": None, "shape": "cases"},
            "a list of 2 to 5": {"conjuncts": six, "shape": "cases"},
            "a list of 2 to 5 ": {"conjuncts": two[:1], "shape": "cases"},
            "nothing else": {"conjuncts": [{**two[0], "note": "x"}, two[1]], "shape": "cases"},
            "is not c2": {"conjuncts": [two[0], {**two[1], "id": "c3"}], "shape": "cases"},
            "names its shape": {"conjuncts": two},
            "the closed list": {"conjuncts": two, "shape": "lemma_and_corollary"},
            "exactly 2": {"conjuncts": two + [{"id": "c3", "text": "a third widget fact"}],
                          "shape": "existence_uniqueness"},
            "a shape belongs to a compound": {"shape": "cases"},
            "one line": {"conjuncts": [two[0], {"id": "c2", "text": "two\nlines"}],
                         "shape": "cases"},
        }
        for needle, extra in cases.items():
            with self.subTest(needle=needle):
                self.assertCutOnly(cut_body(replace("LIST", extra=extra)), needle.strip())

    def test_a_conjunct_is_in_the_workers_own_words(self):
        copied = [{"id": "c1", "text": " ".join(P1.split()[:14])},
                  {"id": "c2", "text": "infinite widgets may lack one"}]
        body = cut_body(replace("LIST", extra={"conjuncts": copied, "shape": "cases"}))
        self.assertCutOnly(body, "own words", rule=1)

    def test_conjuncts_belong_to_a_formal_passage(self):
        body = cut_body(replace("P3", extra={"conjuncts": copy.deepcopy(GOOD[4][6]["conjuncts"]),
                                             "shape": "cases"}))
        self.assertCutOnly(body, "belong to a formal_math passage")

    def test_a_passage_scoped_out_is_supporting(self):
        for prominence in ("central", "subject"):
            body = cut_body(replace("P3", prominence=prominence))
            with self.subTest(prominence=prominence):
                self.assertCutOnly(body, "the subject marker goes")

    def test_one_span_text_is_one_passage_key(self):
        rows = [r for r in GOOD if r[0] != "BOX"]
        rows += [("BOX", [SPANS["BOX"][0], SPANS["BOX_B"][0] - 1], "theorem", "central",
                  "formal_math", "spanning gadget.", {}),
                 ("BOX_B", SPANS["BOX_B"], "other", "supporting", "formal_math", None, {})]
        rows = [(*r[:4], "formal_math", *r[5:]) if r[0] == "P4" else r for r in rows]
        rows.sort(key=lambda r: r[1][0])
        self.assertCutOnly(cut_body(rows), "the same text as")

    def test_the_counts_are_reported_and_refuse_nothing(self):
        result = self.gate(cut_body(), CUT_1)
        self.assertOk(result)
        self.assertEqual(self.stats(result)["cut"], {
            "passages": 7, "formal": 5, "compound": 2, "conjuncts": 4,
            "by_shape": {"cases": 1, "existence_uniqueness": 1},
            "sentences": 7, "displays": 1, "multi_sentence": 1, "multi_display": 0})
        self.assertNotIn("cut", self.stats(self.gate(cut_body(), BEFORE)))

    def test_the_receipts_cut_block(self):
        result = self.gate(cut_body(), CUT_1)
        block = gate_corpus.cut_summary(result["stats"])
        self.assertEqual(block["formal"], 5)
        self.assertEqual(block["by_source"][G.SID]["by_shape"],
                         {"cases": 1, "existence_uniqueness": 1})
        # the pilot's clauses a passage: conjuncts, and one for each simple statement
        self.assertEqual((block["conjuncts"], block["by_source"][G.SID]["conjuncts"]), (4, 4))
        self.assertEqual(block["conjuncts"] + block["formal"] - block["compound"], 7)
        self.assertIn("cut: 5 formal passage(s) of 7, 2 compound of 4 conjunct(s)",
                      gate_corpus.render({"preset": "x", "sources": [], "files": 1, "ok": True,
                                          "problems": [], "cut": block}))

    def test_the_harvest_re_gate_carries_the_block_under_the_cut_only(self):
        corpus = self.fx.root / "corpus"
        self.fx.document(self.TASK, cut_body(), directory=corpus)
        for keys, present in ((CUT_1, True), (BEFORE, False)):
            self.fx.versions(**keys)
            out = gate_corpus._gate_locate_with(vl, corpus, self.fx.root / "gate" / "gate.json",
                                                {"from": "checkout"})
            with self.subTest(keys=keys):
                self.assertTrue(out["ok"], out["problems"])
                self.assertEqual("cut" in out, present)


# --------------------------------------------------------------------------- #
# the continued cut: span-end rule 3 and statement-cut rule 2 (inv-pilot-1's fixes)
# --------------------------------------------------------------------------- #

def without(*names: str, rows=None) -> list:
    return [r for r in (GOOD if rows is None else rows) if r[0] not in names]


def changed(name: str, rows=None, **changes) -> list:
    """``rows`` (GOOD) with one passage's fields replaced, as :func:`replace` does."""
    keys = ("name", "span", "kind", "prominence", "scope", "until", "extra")
    out = []
    for row in GOOD if rows is None else rows:
        if row[0] == name:
            values = dict(zip(keys, row))
            values.update(changes)
            row = tuple(values[k] for k in keys)
        out.append(row)
    return out


#: P2 over its display and P3, the paragraph that (on LAYER_WHEN) finishes its sentence
P2_ON = [SPANS["P2"][0], SPANS["P3"][1]]


class TestTheContinuedCut(InventoryCase):
    """On LAYER_WHEN, P3 ("when n is the order of the widget.") finishes the sentence P2
    and its display leave open: span-end rule 3 ends P2 after it, and rule 10 (b) admits
    it under statement-cut rule 2 only."""

    LAYER = LAYER_WHEN

    def body(self, rows) -> dict:
        return cut_body(rows, layer=LAYER_WHEN)

    def test_the_good_cut_passes_every_way(self):
        for keys in (CUT_2, CUT_1, BEFORE, {}):
            with self.subTest(keys=keys):
                self.assertOk(self.gate(self.body(GOOD), keys))

    def test_rule_10_b_admits_the_words_after_the_display_under_rule_2_only(self):
        body = self.body(changed("P2", rows=without("P3"), span=P2_ON, until="of the widget."))
        self.assertOk(self.gate(body, CUT_2))
        self.assertRule(self.gate(body, CUT_1), 10, "only inside a theorem box")
        self.assertOk(self.gate(body, BEFORE))

    def test_past_the_continuation_is_refused_in_rule_2_s_words(self):
        rows = without("P3", "LIST")
        rows = changed("P2", rows=rows, span=[SPANS["P2"][0], SPANS["L2"][1]],
                       until="need not exist.")
        result = self.gate(self.body(rows), CUT_2)
        self.assertRule(result, 10, "the displays its last sentence introduces and the "
                                    "paragraph that finishes that sentence after them")
        # rule 1's words are the ones they were
        result = self.gate(self.body(rows), CUT_1)
        self.assertRule(result, 10, "into the displays its last sentence introduces, or -- a "
                                    "compound statement")

    def test_span_end_rule_3_ends_a_passage_after_the_words(self):
        body = self.body(changed("P2", rows=without("P3"), span=P2_ON, until=None))
        self.assertOk(self.gate(body, CUT_2))
        self.assertRule(self.gate(body, CUT_1), 2, "the displays its last sentence introduces")
        short = self.body(changed("P2", until=None))
        short["passages"] = [p for p in short["passages"]
                             if p["span"][0] != SPANS["P3"][0]]
        short["skips"].append({"span": SPANS["P3"], "reason": "the end of a sentence"})
        self.assertRule(self.gate(short, CUT_2), 2, "and of the paragraph that finishes that "
                                                    "sentence after them")
        self.assertOk(self.gate(short, CUT_1))
        # the next passage's start still ends it first, less the separator
        self.assertOk(self.gate(self.body(changed("P2", until=None)), CUT_2))

    def test_the_counts(self):
        def cut(rows, skips=()):
            body = self.body(rows)
            body["skips"].extend({"span": list(s), "reason": "x"} for s in skips)
            result = self.gate(body, CUT_2)
            self.assertOk(result)
            return self.stats(result)["cut"]

        extra = ("subjects", "continued", "continuation_starts", "continuations_unheld",
                 "connective_points", "formal_with_connective", "connective_starts")
        got = cut(GOOD)
        # rule 1's counts are the ones they were, and the rest follow
        self.assertEqual({k: got[k] for k in got if k not in extra + ("by_connective",)}, {
            "passages": 7, "formal": 5, "compound": 2, "conjuncts": 4,
            "by_shape": {"cases": 1, "existence_uniqueness": 1},
            "sentences": 7, "displays": 1, "multi_sentence": 1, "multi_display": 0})
        self.assertEqual({k: got[k] for k in extra + ("by_connective",)}, {
            "subjects": 0, "continued": 0, "continuation_starts": 1, "continuations_unheld": 0,
            "connective_points": 1, "formal_with_connective": 1, "connective_starts": 0,
            "by_connective": {";": 1}})
        got = cut(changed("P2", rows=without("P3"), span=P2_ON, until="of the widget."))
        self.assertEqual((got["continued"], got["continuation_starts"],
                          got["continuations_unheld"]), (1, 0, 0))
        got = cut(without("P3"), skips=[SPANS["P3"]])
        self.assertEqual((got["continued"], got["continuation_starts"],
                          got["continuations_unheld"]), (0, 0, 1))
        got = cut(changed("THM1", prominence="subject"))
        self.assertEqual(got["subjects"], 1)


class TestTheContinuedCutOnANewSentence(InventoryCase):
    """On LAYER, P3 ("Here n is …") starts a sentence of its own: nothing continues P2."""

    def test_rule_10_b_refuses_the_run_on_under_rule_2_too(self):
        body = cut_body(changed("P2", rows=without("P3"), span=P2_ON, until="of the widget."))
        self.assertRule(self.gate(body, CUT_2), 10, "and the paragraph that finishes that "
                                                    "sentence after them")
        self.assertRule(self.gate(body, CUT_1), 10, "only inside a theorem box")

    def test_the_good_cut_counts_no_continuation(self):
        result = self.gate(cut_body(), CUT_2)
        self.assertOk(result)
        got = self.stats(result)["cut"]
        self.assertEqual((got["continued"], got["continuation_starts"],
                          got["continuations_unheld"]), (0, 0, 0))


class TestOneSubject(InventoryCase):
    """Rule 10 (h), statement-cut rule 2: two or more formal passages marked subject are
    refused, all named and none chosen (the maintainer's reading K3)."""

    def test_two_subjects_are_refused_under_rule_2_only(self):
        rows = changed("BOX", rows=changed("THM1", prominence="subject"), prominence="subject")
        body = cut_body(rows)
        result = self.gate(body, CUT_2)
        self.assertRule(result, 10, "2 passages carry `prominence: subject`")
        line = next(p for p in result["problems"] if "carry `prominence" in p)
        ids = {p["span"][0]: p["item"] for p in body["passages"]}
        for name in ("THM1", "BOX"):
            self.assertIn(f"{ids[SPANS[name][0]]} {SPANS[name]}", line)
        self.assertIn("a lead that only describes it is `expository`", line)
        self.assertNotIn("first", line)
        self.assertOk(self.gate(body, CUT_1))
        self.assertOk(self.gate(body, BEFORE))
        three = cut_body(changed("DEF1", rows=rows, prominence="subject"))
        self.assertRule(self.gate(three, CUT_2), 10, "3 passages carry `prominence: subject`")

    def test_one_subject_passes(self):
        self.assertOk(self.gate(cut_body(changed("BOX", prominence="subject")), CUT_2))

    def test_a_scoped_out_subject_is_rule_f_s_alone(self):
        body = cut_body(changed("P3", rows=changed("THM1", prominence="subject"),
                                prominence="subject"))
        result = self.gate(body, CUT_2)
        self.assertRule(result, 10, "the subject marker goes")
        self.assertFalse([p for p in result["problems"] if "carry `prominence" in p])


class TestTheReceiptUnderRule2(InventoryCase):

    def test_the_block_s_keys_follow_the_rule(self):
        one = gate_corpus.cut_summary(self.gate(cut_body(), CUT_1)["stats"])
        self.assertEqual(set(one), set(gate_corpus.CUT_COUNTS) | {"by_shape", "by_source"})
        self.assertEqual(one, gate_corpus.cut_summary(self.gate(cut_body(), CUT_1)["stats"],
                                                      rule=1))
        stats = self.gate(cut_body(changed("BOX", prominence="subject")), CUT_2)["stats"]
        two = gate_corpus.cut_summary(stats, rule=2)
        self.assertEqual(set(two), set(gate_corpus.CUT_COUNTS_2)
                         | {"by_shape", "by_connective", "by_source"})
        self.assertEqual(set(two["by_source"][G.SID]), set(two) - {"by_source"})
        self.assertEqual((two["subjects"], two["by_connective"]), (1, {";": 1}))
        self.assertEqual({k: two[k] for k in one if k != "by_source"},
                         {k: one[k] for k in one if k != "by_source"})
        text = gate_corpus.render({"preset": "x", "sources": [], "files": 1, "ok": True,
                                   "problems": [], "cut": two})
        self.assertIn("cut, continued: 1 subject(s)", text)
        self.assertNotIn("cut, continued", gate_corpus.render(
            {"preset": "x", "sources": [], "files": 1, "ok": True, "problems": [], "cut": one}))

    def test_the_harvest_re_gate_passes_the_rule(self):
        corpus = self.fx.root / "corpus"
        self.fx.document(self.TASK, cut_body(), directory=corpus)
        for keys, rule in ((CUT_2, 2), (CUT_1, 1)):
            self.fx.versions(**keys)
            out = gate_corpus._gate_locate_with(vl, corpus, self.fx.root / "gate" / "gate.json",
                                                {"from": "checkout"})
            with self.subTest(keys=keys):
                self.assertTrue(out["ok"], out["problems"])
                self.assertEqual("subjects" in out["cut"], rule == 2)


# --------------------------------------------------------------------------- #
# judgment rules 3 on a locate document (sections 1.3 and 5)
# --------------------------------------------------------------------------- #

THM_CONJUNCTS = [{"id": "c1", "text": "a finite widget has a spanning gadget"},
                 {"id": "c2", "text": "that gadget's size divides the order"}]
SIGNED = "∀ (w : Widget), w.Finite → ∃ g, g.Spans w"
NAT_ONLY = "∀ (n : ℕ) (w : Widget), w.order = n → ∃ g, g.Spans w"
TESTS = {"r2": "the passage is the declaration read at its widget",
         "r3": "a finite widget meets every hypothesis the declaration has"}
SPECIAL = {"reason": "no library states the lemma for any special widget either"}


def sign(fx, name: str, signature: str | None) -> None:
    con = sqlite3.connect(fx.db)
    con.execute("UPDATE decls SET signature = ? WHERE name = ?", (signature, name))
    con.commit()
    con.close()
    fx.repin_corpus()


def strong(lib="alpha", decl="Widget.exists_spanning_gadget", relation="exactMatch", rank=1,
           clauses=("c1", "c2"), **extra) -> dict:
    return {**G.location(lib, decl, relation, rank), "clauses": list(clauses),
            "instantiation": "at the passage's finite widget, no instance needed",
            "tests": copy.deepcopy(TESTS), **extra}


def part(lib, decl, rank, clause, relation="exactMatch", **extra) -> dict:
    """A hasPart of the compound theorem; a strong one carries what a strong location does."""
    held = ({"instantiation": "at the passage's finite widget, no instance needed",
             "tests": copy.deepcopy(TESTS)} if relation in vocab.STRONG_RELATIONS else {})
    return {**G.location(lib, decl, "hasPart", rank), "clauses": [clause],
            "conjunct_relation": relation, "step": f"the {clause} conjunct as the library states it",
            **held, **extra}


def cut_locate_body() -> dict:
    """Under judgment rules 3: the theorem (compound) located whole; the lead (simple) absent."""
    return {"passages": [
        {"item": G.THM_ITEM, "outcome": "located", "locations": [strong()],
         "receipt": {"queries": ["widget spanning gadget"],
                     "candidates": [{"library": "alpha",
                                     "declaration": "Widget.exists_spanning_gadget",
                                     "verdict": "accepted", "reason": ""}]}},
        {"item": G.LEAD_ITEM, "outcome": "absent", "locations": [],
         "receipt": {"queries": ["widget lemma", "finite widget spanning", "Widget.spanning"],
                     "candidates": [],
                     "query_clauses": {"widget lemma": ["c1"], "finite widget spanning": ["c1"],
                                       "Widget.spanning": ["c1"]},
                     "special_case": copy.deepcopy(SPECIAL)}}]}


def bundle_body() -> dict:
    body = cut_locate_body()
    body["passages"][0]["locations"] = [
        part("alpha", "Widget.exists_spanning_gadget", 1, "c1"),
        part("beta", "Beta.Widget.gadget_of_finite", 2, "c2", "broadMatch")]
    body["passages"][0]["coverage"] = {"complete": True, "parts": [1, 2],
                                       "note": "existence and the size bound together"}
    return body


def cut_manifest_doc(fx, *, thm=None, lead=None) -> dict:
    doc = G.locate_manifest_doc(fx)
    rows = {row["item"]: row for row in doc["passages"]}
    thm_row, lead_row = rows[G.THM_ITEM], rows[G.LEAD_ITEM]
    for row, values in (
            (thm_row, {"statement": "for a finite widget of order n, a spanning gadget exists "
                                    "and its size divides n",
                       "conjuncts": copy.deepcopy(THM_CONJUNCTS), "shape": "conclusions",
                       **(thm or {})}),
            (lead_row, {"statement": "every finite widget has a spanning gadget",
                        **(lead or {})})):
        for key, value in values.items():
            if value is None:
                row.pop(key, None)
            else:
                row[key] = value
    return doc


class LocateCase(G.GateTestCase):

    TASK = "t-loc-0001"

    def setUp(self):
        self.fx = G.Fixture(self, "locate")
        self.fx.manifest(self.TASK, cut_manifest_doc(self.fx))

    def gate(self, body: dict, rules: int | None) -> dict:
        self.fx.gate.pop("judgment_rules", None)
        if rules is not None:
            self.fx.gate["judgment_rules"] = rules
        self.fx.write_gate()
        return self.fx.run(self.fx.document(self.TASK, body))

    def assertCutOnly(self, body: dict, needle: str, rule: int = 9):
        """Refused under judgment rules 3 for ``needle``; rules 2 never says it; no rules pass."""
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), rule, needle)
        self.assertFalse([p for p in self.gate(body, vocab.RULES_PD5_PD9)["problems"]
                          if needle in p])
        self.assertOk(self.gate(body, None))


class TestRules3OnALocateDocument(LocateCase):

    def test_a_good_document_passes_and_rules_2_would_want_its_claims(self):
        self.assertOk(self.gate(cut_locate_body(), vocab.RULES_STATEMENT_CUT))
        self.assertOk(self.gate(bundle_body(), vocab.RULES_STATEMENT_CUT))
        self.assertRule(self.gate(cut_locate_body(), vocab.RULES_PD5_PD9), 9,
                        "claim list first")
        self.assertOk(self.gate(cut_locate_body(), None))

    def test_j1_no_claim_list(self):
        body = cut_locate_body()
        body["passages"][0]["claims"] = copy.deepcopy(THM_CONJUNCTS)
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 9, "claims is the inventory's")
        self.assertOk(self.gate(body, None))

    def test_the_clause_ids_are_the_inventorys(self):
        body = cut_locate_body()
        body["passages"][0]["locations"][0]["clauses"] = ["c1"]
        self.assertCutOnly(body, "does not state c2")
        body["passages"][0]["locations"][0]["clauses"] = ["c1", "c3"]
        self.assertCutOnly(body, "c3 are not ids")
        # a simple statement's one clause is c1
        body = cut_locate_body()
        body["passages"][1]["receipt"]["query_clauses"] = {"widget lemma": ["c2"]}
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 9, "c2 are neither")

    def test_j2_a_part_of_a_compound_statement_bears_a_conjunct_relation(self):
        body = bundle_body()
        del body["passages"][0]["locations"][0]["conjunct_relation"]
        self.assertCutOnly(body, "says what it bears to the conjunct")
        body = bundle_body()
        body["passages"][0]["locations"][0]["conjunct_relation"] = "hasPart"
        self.assertCutOnly(body, "says what it bears to the conjunct")
        body = cut_locate_body()
        body["passages"][0]["locations"][0]["conjunct_relation"] = "exactMatch"
        self.assertCutOnly(body, "a exactMatch is none")

    def test_j2_a_part_of_a_simple_statement_names_c1_and_no_conjunct_relation(self):
        self.fx.manifest(self.TASK, cut_manifest_doc(self.fx, thm={"conjuncts": None,
                                                                   "shape": None}))
        body = cut_locate_body()
        body["passages"][0]["locations"] = [part("alpha", "Widget.exists_spanning_gadget", 1,
                                                 "c1")]
        self.assertCutOnly(body, "a hasPart of a simple one names c1")
        del body["passages"][0]["locations"][0]["conjunct_relation"]
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))

    def test_j2_a_close_part_names_its_convention(self):
        body = bundle_body()
        body["passages"][0]["locations"][0]["conjunct_relation"] = "closeMatch"
        self.assertCutOnly(body, "names the convention it differs by")
        body["passages"][0]["locations"][0]["convention"] = "encoding"
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        body["passages"][0]["locations"][0]["conjunct_relation"] = "exactMatch"
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 9,
                        "convention belongs to a closeMatch; a exactMatch")

    def test_j3_a_bundle_only_on_a_compound_statement(self):
        self.fx.manifest(self.TASK, cut_manifest_doc(self.fx, thm={"conjuncts": None,
                                                                   "shape": None}))
        body = bundle_body()
        for loc in body["passages"][0]["locations"]:
            loc.pop("conjunct_relation")
            loc["clauses"] = ["c1"]
        self.assertCutOnly(body, "on a compound statement only")

    def test_j3_one_part_per_conjunct_and_none_stating_them_all(self):
        body = bundle_body()
        body["passages"][0]["locations"][1]["clauses"] = ["c1"]
        result = self.gate(body, vocab.RULES_STATEMENT_CUT)
        self.assertRule(result, 9, "both name c1")
        self.assertRule(result, 9, "no part states c2")
        body = bundle_body()
        body["passages"][0]["locations"][0]["clauses"] = ["c1", "c2"]
        body["passages"][0]["locations"][1]["clauses"] = ["c2"]
        self.assertCutOnly(body, "names every conjunct")

    def test_j3_a_bundles_parts_state_their_conjuncts_strongly(self):
        body = bundle_body()
        body["passages"][0]["locations"][1]["conjunct_relation"] = "narrowMatch"
        self.assertCutOnly(body, "a bundle's part states them strongly")

    def test_fix_4a_strong_parts_of_every_conjunct_write_the_bundle(self):
        body = bundle_body()
        del body["passages"][0]["coverage"]
        self.assertCutOnly(body, "write the bundle")
        body["passages"][0]["locations"][1]["conjunct_relation"] = "narrowMatch"
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))

    def test_fix_2a_a_strong_location_writes_both_tests(self):
        body = cut_locate_body()
        del body["passages"][0]["locations"][0]["tests"]
        self.assertCutOnly(body, "writes both instantiation tests")
        for tests, needle in (({"r2": "a line of words here"}, "nothing else"),
                              ({"r2": "a line of words here", "r3": "n/a"}, "says nothing"),
                              ({"r2": "a line of words here", "r3": " "}, "tests.r3 is one line")):
            body = cut_locate_body()
            body["passages"][0]["locations"][0]["tests"] = tests
            with self.subTest(tests=tests):
                self.assertCutOnly(body, needle)

    def test_fix_2b_instantiation_on_every_strong_location(self):
        body = cut_locate_body()
        del body["passages"][0]["locations"][0]["instantiation"]
        self.assertCutOnly(body, "whatever its binders")

    def test_fix_2b_the_naturals_for_integers_tripwire(self):
        sign(self.fx, "Widget.exists_spanning_gadget", NAT_ONLY)
        self.fx.manifest(self.TASK, cut_manifest_doc(
            self.fx, thm={"statement": "for every integer order a spanning gadget exists"}))
        self.assertCutOnly(cut_locate_body(), "every number-typed binder")
        body = cut_locate_body()
        body["passages"][0]["locations"][0]["note"] = "the orders are natural numbers here"
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        # an integer binder, or no number-typed binder at all, trips nothing
        for signature in (SIGNED, NAT_ONLY.replace("(n : ℕ)", "(n : ℤ)")):
            sign(self.fx, "Widget.exists_spanning_gadget", signature)
            with self.subTest(signature=signature):
                self.assertOk(self.gate(cut_locate_body(), vocab.RULES_STATEMENT_CUT))

    def test_the_tripwires_words_are_cflibs_and_read_as_they_did(self):
        """vocab.NAT_FOR_INT_*_WORDS, read as the gate's two patterns read before the move."""
        before = (r"(?<![\w'])integers?(?![\w'])|ℤ", r"ℕ|(?<![\w'])natural")
        texts = ("an integer order", "the integers", "n in nℤ", "a ℤ-module", "integerness",
                 "orders are natural numbers", "naturally", "an ℕ-valued map", "supernatural",
                 "Integer orders", "no number at all")
        for pattern, old in zip((vl._INTEGER_WORDS, vl._NATURAL_WORDS), before):
            for text in texts:
                with self.subTest(pattern=old, text=text):
                    folded = vl.fold_words(text)
                    self.assertEqual(bool(pattern.search(folded)),
                                     bool(re.search(old, folded)))

    def test_fix_2c_the_longer_generality_list(self):
        body = cut_locate_body()
        body["passages"][0]["locations"][0]["note"] = "the lemma holds at every point"
        self.assertCutOnly(body, "every point")

    def test_fix_5b_an_absence_names_its_special_case(self):
        body = cut_locate_body()
        del body["passages"][1]["receipt"]["special_case"]
        self.assertCutOnly(body, "nearest special case")
        for value, rule, needle in (
                ({"reason": "x"}, 9, "says nothing"),
                ({"declaration": "Widget.Spanning", "reason": "a reason of words"}, 9,
                 "nothing else"),
                ({"library": "alpha", "declaration": "Widget.Phantom",
                  "reason": "the special case is too weak"}, 3, "phantom"),
                ({"library": "gamma", "declaration": "Widget.Spanning",
                  "reason": "the special case is too weak"}, 3, "not in universe")):
            body = cut_locate_body()
            body["passages"][1]["receipt"]["special_case"] = value
            with self.subTest(value=value):
                self.assertCutOnly(body, needle, rule)
        body = cut_locate_body()
        body["passages"][1]["receipt"]["special_case"] = {
            "library": "alpha", "declaration": "Widget.Spanning",
            "reason": "it defines spanning and states no gadget exists"}
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))

    def test_j4_a_cut_flag_on_an_unresolved_passage_only(self):
        body = cut_locate_body()
        body["passages"][1] = {"item": G.LEAD_ITEM, "outcome": "unresolved", "locations": [],
                               "reason": "the lead holds two statements",
                               "cut_flag": "several_statements",
                               "receipt": {"queries": [], "candidates": []}}
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        body["passages"][1]["cut_flag"] = "too_long"
        self.assertCutOnly(body, "cut_flag is one of")
        body = cut_locate_body()
        body["passages"][0]["cut_flag"] = "not_a_statement"
        self.assertCutOnly(body, "belongs to an unresolved passage")

    def test_an_inconsistent_manifest_cannot_be_gated(self):
        self.fx.manifest(self.TASK, cut_manifest_doc(
            self.fx, thm={"conjuncts": [{"id": "c2", "text": "x"}, {"id": "c1", "text": "y"}]}))
        self.assertBroken(self.gate(cut_locate_body(), vocab.RULES_STATEMENT_CUT),
                          "the frozen inventory is inconsistent")


#: how the gate names a bundle part it holds by its conjunct_relation
STRONG_PART = "part whose conjunct_relation is exactMatch"


class TestABundlePartIsHeldByItsConjunctRelation(LocateCase):
    """Rules 3: a part of a compound statement is held by its ``conjunct_relation``.

    Under status rule 2 a bundle takes its weakest part's relation, so a part whose
    conjunct_relation is strong is a strong claim and passes every check a strong
    location does: fixes 2a-2c and R1's generality words.  Before this, the checks
    read the location's own relation (``hasPart``), so the FM1/FM4 over-claims the
    fixes target passed by being written as a bundle.
    """

    def test_a_good_bundle_passes(self):
        self.assertOk(self.gate(bundle_body(), vocab.RULES_STATEMENT_CUT))

    def test_fix_2a_a_strong_part_writes_both_tests(self):
        body = bundle_body()
        del body["passages"][0]["locations"][0]["tests"]
        self.assertCutOnly(body, f"a {STRONG_PART} writes both instantiation tests")
        body = bundle_body()
        del body["passages"][0]["locations"][1]["tests"]
        self.assertCutOnly(body, "a part whose conjunct_relation is broadMatch writes both")

    def test_fix_2b_a_strong_part_says_its_instantiation(self):
        body = bundle_body()
        del body["passages"][0]["locations"][0]["instantiation"]
        self.assertCutOnly(body, f"a {STRONG_PART} says how the declaration is instantiated")

    def test_fix_2b_the_naturals_for_integers_tripwire_on_a_part(self):
        sign(self.fx, "Widget.exists_spanning_gadget", NAT_ONLY)
        self.fx.manifest(self.TASK, cut_manifest_doc(
            self.fx, thm={"statement": "for every integer order a spanning gadget exists"}))
        self.assertCutOnly(bundle_body(), "every number-typed binder")
        body = bundle_body()
        body["passages"][0]["locations"][0]["note"] = "the orders are natural numbers here"
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))

    def test_r1_and_fix_2c_the_generality_words_on_a_part(self):
        for note, word in (("a stronger statement than the conjunct", "stronger"),
                           ("the lemma holds at every point", "every point")):
            body = bundle_body()
            body["passages"][0]["locations"][0]["note"] = note
            with self.subTest(note=note):
                self.assertCutOnly(body, f"the note of a {STRONG_PART} says {word!r}")
        # a broader part may say it is more general: that is what broadMatch is (R2)
        body = bundle_body()
        body["passages"][0]["locations"][1]["note"] = "a stronger statement than the conjunct"
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))

    def test_a_close_part_is_held_as_a_close_location(self):
        body = bundle_body()
        body["passages"][0]["locations"][0].update(conjunct_relation="closeMatch",
                                                   convention="encoding")
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        del body["passages"][0]["locations"][0]["tests"]
        self.assertCutOnly(body, "a part whose conjunct_relation is closeMatch writes both")

    def test_a_special_case_part_owes_none_of_it(self):
        """A narrowMatch part states a special case of its conjuncts: no strong claim."""
        sign(self.fx, "Widget.exists_spanning_gadget", NAT_ONLY)
        self.fx.manifest(self.TASK, cut_manifest_doc(
            self.fx, thm={"statement": "for every integer order a spanning gadget exists"}))
        body = bundle_body()
        del body["passages"][0]["coverage"]
        first = body["passages"][0]["locations"][0]
        first["conjunct_relation"] = "narrowMatch"
        first["note"] = "a stronger hypothesis, at every point"
        for key in ("tests", "instantiation"):
            first.pop(key)
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))


# --------------------------------------------------------------------------- #
# judgment rules 3 on a challenge document (section 1.4, anomalies A1-A3)
# --------------------------------------------------------------------------- #

KEY_A, KEY_B, KEY_C = "a" * 64, "b" * 64, "c" * 64


class ChallengeCase(G.GateTestCase):

    TASK = "t-chl-0001"

    def setUp(self):
        self.fx = G.Fixture(self, "locate-challenge")
        self.records()

    def thm(self, **extra) -> dict:
        return {"item": G.THM_ITEM, "span": G.THEOREM_SPAN, "passage_kind": "theorem",
                "outcome": "located", "prominence": "central",
                "statement": "for a finite widget a spanning gadget exists and divides n",
                "conjuncts": copy.deepcopy(THM_CONJUNCTS), "shape": "conclusions", **extra}

    def records(self, *extra: dict) -> None:
        loc = lambda key, decl, lib, relation, rank, **more: {  # noqa: E731
            **self.thm(), "target": "location", "location_key": key, "library": lib,
            "declaration": decl, "relation": relation, "rank": rank, **more}
        parts = [{"location_key": KEY_A, "library": "alpha",
                  "declaration": "Widget.exists_spanning_gadget", "relation": "hasPart",
                  "rank": 1, "clauses": ["c1"], "conjunct_relation": "exactMatch"},
                 {"location_key": KEY_B, "library": "beta",
                  "declaration": "Beta.Widget.gadget_of_finite", "relation": "hasPart",
                  "rank": 2, "clauses": ["c2"], "conjunct_relation": "broadMatch"}]
        doc = G.locate_manifest_doc(self.fx)
        doc.pop("passages")
        doc["records"] = [
            loc(KEY_A, "Widget.exists_spanning_gadget", "alpha", "hasPart", 1, clauses=["c1"],
                conjunct_relation="exactMatch", step="the existence of the gadget",
                passage_locations=[{"location_key": KEY_B, "relation": "hasPart"}]),
            loc(KEY_B, "Beta.Widget.gadget_of_finite", "beta", "hasPart", 2, clauses=["c2"],
                conjunct_relation="broadMatch", step="the size bound of the gadget",
                passage_locations=[{"location_key": KEY_A, "relation": "hasPart"}]),
            {**self.thm(), "target": "coverage", "parts": parts,
             "note": "existence and the bound together"},
            {"item": G.LEAD_ITEM, "target": "absence", "span": G.LEAD_SPAN,
             "passage_kind": "theorem", "outcome": "absent", "prominence": "subject",
             "statement": "every finite widget has a spanning gadget"},
            *extra]
        self.fx.manifest(self.TASK, doc)

    def body(self) -> dict:
        return {"verdicts": [
            {"item": G.THM_ITEM, "target": "location", "location_key": KEY_A, "action": "confirm",
             "clauses": ["c1"], "tests": copy.deepcopy(TESTS), "witness": "states existence"},
            {"item": G.THM_ITEM, "target": "location", "location_key": KEY_B, "action": "confirm",
             "clauses": ["c2"], "tests": copy.deepcopy(TESTS), "witness": "states the bound"},
            {"item": G.THM_ITEM, "target": "coverage", "action": "confirm",
             "part_relations": {KEY_A: "exactMatch", KEY_B: "broadMatch"},
             "witness": "the parts state both conjuncts"},
            {"item": G.LEAD_ITEM, "target": "absence", "action": "uphold",
             "queries": ["gadget lemma", "spanning widget gadget", "Widget.span"],
             "query_clauses": {"gadget lemma": ["c1"], "spanning widget gadget": ["c1"],
                               "Widget.span": ["c1"]},
             "special_case": copy.deepcopy(SPECIAL), "witness": "searched again"}]}

    def gate(self, body: dict, rules: int | None) -> dict:
        self.fx.gate.pop("judgment_rules", None)
        if rules is not None:
            self.fx.gate["judgment_rules"] = rules
        self.fx.write_gate()
        return self.fx.run(self.fx.document(self.TASK, body))

    def assertCutOnly(self, body: dict, needle: str, rule: int = 9):
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), rule, needle)
        self.assertFalse([p for p in self.gate(body, vocab.RULES_PD5_PD9)["problems"]
                          if needle in p])
        self.assertOk(self.gate(body, None))


class TestRules3OnAChallenge(ChallengeCase):

    def test_a_good_challenge_passes(self):
        self.assertOk(self.gate(self.body(), vocab.RULES_STATEMENT_CUT))
        self.assertOk(self.gate(self.body(), None))

    def test_no_claims(self):
        body = self.body()
        body["claims"] = {G.THM_ITEM: copy.deepcopy(THM_CONJUNCTS)}
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 9, "claims is the inventory's")

    def test_a_bundle_confirm_gives_part_relations_never_stronger(self):
        body = self.body()
        body["verdicts"][2]["part_relations"][KEY_B] = "exactMatch"
        self.assertCutOnly(body, "stronger than the part's own broadMatch")
        body["verdicts"][2]["part_relations"][KEY_B] = "narrowMatch"
        self.assertCutOnly(body, "a bundle's part states its conjuncts strongly")
        body["verdicts"][2]["part_relations"] = {KEY_A: "exactMatch"}
        self.assertCutOnly(body, "gives part bbbbbbbbbbbb no relation")
        body["verdicts"][2]["part_relations"] = {KEY_A: "exactMatch", KEY_B: "broadMatch",
                                                 KEY_C: "exactMatch"}
        self.assertCutOnly(body, "which is no part of the claim")
        del body["verdicts"][2]["part_relations"]
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 9, "part_relations: {")
        body["verdicts"][2]["part_clauses"] = {KEY_A: ["c1"], KEY_B: ["c2"]}
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 9,
                        "part_clauses is refused under the statement cut")

    def test_fix_1a_a_bundle_refute_names_what_is_unstated_and_why(self):
        body = self.body()
        body["verdicts"][2] = {"item": G.THM_ITEM, "target": "coverage", "action": "refute",
                               "unstated": ["c2"],
                               "part_reasons": {KEY_A: "it states existence and nothing more",
                                                KEY_B: "it bounds the order, not the size"},
                               "witness": "the bound is another"}
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        for change, needle in (({"unstated": []}, "names the conjuncts no part states"),
                               ({"unstated": ["c3"]}, "not the passage's clause ids"),
                               ({"part_reasons": {KEY_A: "it states existence only here"}},
                                "gives part bbbbbbbbbbbb no reason"),
                               ({"part_reasons": {KEY_A: "it states existence only here",
                                                  KEY_B: "-"}}, "says nothing")):
            refute = {**body["verdicts"][2], **change}
            case = copy.deepcopy(body)
            case["verdicts"][2] = refute
            with self.subTest(change=change):
                self.assertCutOnly(case, needle)

    def test_fix_2d_a_verdict_that_keeps_a_strong_relation_runs_its_own_tests(self):
        self.records({**self.thm(item=G.THM_ITEM), "target": "location", "location_key": KEY_C,
                      "library": "alpha", "declaration": "Widget.Spanning",
                      "relation": "exactMatch", "rank": 3, "clauses": ["c1", "c2"],
                      "instantiation": "at the widget itself", "tests": copy.deepcopy(TESTS)})
        body = self.body()
        body["verdicts"].append({"item": G.THM_ITEM, "target": "location", "location_key": KEY_C,
                                 "action": "confirm", "clauses": ["c1", "c2"],
                                 "witness": "states it"})
        self.assertCutOnly(body, "a verdict that keeps or leaves a exactMatch writes both")
        body["verdicts"][-1]["tests"] = copy.deepcopy(TESTS)
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))

    def test_a_verdict_on_a_part_may_weaken_its_conjunct_relation_never_raise_it(self):
        body = self.body()
        body["verdicts"][1]["conjunct_relation"] = "narrowMatch"
        body["verdicts"][2] = {"item": G.THM_ITEM, "target": "coverage", "action": "refute",
                               "unstated": ["c2"],
                               "part_reasons": {KEY_A: "it states existence and nothing more",
                                                KEY_B: "a special case of the bound only"},
                               "witness": "a special case"}
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        body["verdicts"][1]["conjunct_relation"] = "exactMatch"
        self.assertCutOnly(body, "stronger than the record's broadMatch")
        # a confirm of the bundle that keeps the part stronger than its own verdict says
        body = self.body()
        body["verdicts"][1]["conjunct_relation"] = "narrowMatch"
        self.assertCutOnly(body, "this document gives that part's record")

    def test_a_verdict_on_a_part_is_held_by_the_conjunct_relation_it_leaves(self):
        """Fix 2d, 2b and the generality words on a part, by the relation the verdict leaves it."""
        body = self.body()
        del body["verdicts"][0]["tests"]
        self.assertCutOnly(body, f"a verdict that keeps or leaves a {STRONG_PART} writes both")
        body = self.body()
        body["verdicts"][0]["note"] = "a stronger statement than the conjunct"
        self.assertCutOnly(body, f"the note of a {STRONG_PART} says 'stronger'")
        body = self.body()
        body["verdicts"][0]["note"] = "the lemma holds at every point"
        self.assertCutOnly(body, f"the note of a {STRONG_PART} says 'every point'")
        sign(self.fx, "Widget.exists_spanning_gadget", NAT_ONLY)
        self.records()
        doc = json.loads((self.fx.tasks / f"{self.TASK}.json").read_text(encoding="utf-8"))
        for rec in doc["documents"][0]["records"]:
            if rec["item"] == G.THM_ITEM:
                rec["statement"] = "for every integer order a spanning gadget exists"
        (self.fx.tasks / f"{self.TASK}.json").write_text(json.dumps(doc), encoding="utf-8")
        self.assertCutOnly(self.body(), "every number-typed binder")
        # a verdict that weakens the part to a special case keeps no strong relation
        body = self.body()
        body["verdicts"][0].update(conjunct_relation="narrowMatch")
        del body["verdicts"][0]["tests"]
        body["verdicts"][2] = {"item": G.THM_ITEM, "target": "coverage", "action": "refute",
                               "unstated": ["c1"],
                               "part_reasons": {KEY_A: "it states existence for naturals only",
                                                KEY_B: "it bounds the size and nothing more"},
                               "witness": "a special case"}
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))

    def test_a_fills_parts_are_held_by_their_conjunct_relation(self):
        body = self.body()
        for v in body["verdicts"][:2]:
            v.update(action="refute")
            for key in ("clauses", "tests"):
                v.pop(key)
        body["verdicts"][2] = {"item": G.THM_ITEM, "target": "coverage", "action": "refute",
                               "unstated": ["c1", "c2"],
                               "part_reasons": {KEY_A: "the declaration is about sprockets",
                                                KEY_B: "the declaration is about sprockets"},
                               "witness": "no part states the passage"}
        body["fills"] = {G.THM_ITEM: {"outcome": "located", "locations": [
            part("alpha", "Widget.Spanning", 1, "c1"),
            part("beta", "Beta.Widget.gadget_of_finite", 2, "c2", "narrowMatch")]}}
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        del body["fills"][G.THM_ITEM]["locations"][0]["tests"]
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 9,
                        f"a {STRONG_PART} writes both")

    def test_fix_5b_an_uphold_names_its_special_case(self):
        body = self.body()
        del body["verdicts"][3]["special_case"]
        self.assertCutOnly(body, "nearest special case")

    def test_a1_a2_a_verdict_that_empties_a_located_passage_brings_a_fill(self):
        body = self.body()
        for v in body["verdicts"][:2]:
            v.update(action="refute")
            v.pop("clauses")
        body["verdicts"][2] = {"item": G.THM_ITEM, "target": "coverage", "action": "refute",
                               "unstated": ["c1", "c2"],
                               "part_reasons": {KEY_A: "the declaration is about sprockets",
                                                KEY_B: "the declaration is about sprockets"},
                               "witness": "no part states the passage"}
        self.assertCutOnly(body, "give fills[")
        body["fills"] = {G.THM_ITEM: {"outcome": "absent", "receipt": {
            "queries": ["gadget divides order", "spanning gadget size", "Widget.card"],
            "candidates": [],
            "query_clauses": {"gadget divides order": ["c2"], "spanning gadget size": ["c1"],
                              "Widget.card": ["c1", "c2"]},
            "special_case": copy.deepcopy(SPECIAL)}}}
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        body["fills"][G.THM_ITEM]["receipt"]["query_clauses"] = {"Widget.card": ["c1"]}
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 9, "no query targets c2")
        body["fills"] = {G.THM_ITEM: {"outcome": "located", "locations": [
            strong("alpha", "Widget.Spanning")]}}
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        del body["fills"][G.THM_ITEM]["locations"][0]["tests"]
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 9, "writes both")

    def test_an_absent_fill_accepts_no_candidate(self):
        """An absent fill is held to a locate absence's rule 4: accepted means located."""
        body = self.body()
        for v in body["verdicts"][:2]:
            v.update(action="refute")
            for key in ("clauses", "tests"):
                v.pop(key)
        body["verdicts"][2] = {"item": G.THM_ITEM, "target": "coverage", "action": "refute",
                               "unstated": ["c1", "c2"],
                               "part_reasons": {KEY_A: "the declaration is about sprockets",
                                                KEY_B: "the declaration is about sprockets"},
                               "witness": "no part states the passage"}
        body["fills"] = {G.THM_ITEM: {"outcome": "absent", "receipt": {
            "queries": ["gadget divides order", "spanning gadget size", "Widget.card"],
            "candidates": [{"library": "alpha", "declaration": "Widget.Spanning",
                            "verdict": "accepted", "reason": ""}],
            "query_clauses": {"gadget divides order": ["c2"], "spanning gadget size": ["c1"],
                              "Widget.card": ["c1", "c2"]},
            "special_case": copy.deepcopy(SPECIAL)}}}
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 1,
                        "accepted candidate alpha:Widget.Spanning is not one of this passage's "
                        "locations")
        body["fills"][G.THM_ITEM]["receipt"]["candidates"][0].update(
            verdict="rejected", reason="it defines spanning and states no gadget")
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))

    def test_pd6_a_bundle_confirm_beside_a_part_said_to_state_every_conjunct(self):
        """Rules 3 dropped part_clauses; the same document's part verdict holds PD6 instead."""
        body = self.body()
        body["verdicts"][0]["clauses"] = ["c1", "c2"]
        self.assertCutOnly(body, "your verdict on part aaaaaaaaaaaa names every conjunct")
        # without the bundle confirm beside it, a part's clauses are only that verdict's
        body["verdicts"][2] = {"item": G.THM_ITEM, "target": "coverage", "action": "refute",
                               "unstated": ["c2"],
                               "part_reasons": {KEY_A: "it states the whole, so it is no part",
                                                KEY_B: "it bounds the order, not the size"},
                               "witness": "one part is the whole"}
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))

    def test_a_fill_on_a_passage_left_a_location_is_refused(self):
        body = self.body()
        body["fills"] = {G.THM_ITEM: {"outcome": "located", "locations": [
            strong("alpha", "Widget.Spanning")]}}
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 9, "drop it")
        self.assertOk(self.gate(body, None))

    def test_a3_a_prominence_overturn_judges_its_conditional_records(self):
        conditional = {"item": G.LEAD_ITEM, "target": "prominence", "span": G.LEAD_SPAN,
                       "passage_kind": "theorem", "outcome": "absent", "prominence": "subject"}
        self.records({**conditional})
        doc = json.loads((self.fx.tasks / f"{self.TASK}.json").read_text(encoding="utf-8"))
        records = doc["documents"][0]["records"]
        records[3]["conditional"] = "prominence"            # the absence rides on the sample
        records[4]["prominence"] = "supporting"
        (self.fx.tasks / f"{self.TASK}.json").write_text(json.dumps(doc), encoding="utf-8")
        body = self.body()
        uphold = body["verdicts"].pop(3)
        body["verdicts"].append({"item": G.LEAD_ITEM, "target": "prominence",
                                 "action": "uphold", "witness": "supporting it is"})
        # no overturn: the conditional absence is owed nothing, and a verdict on it is refused
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        with_it = copy.deepcopy(body)
        with_it["verdicts"].append(uphold)
        self.assertCutOnly(with_it, "is a conditional record", rule=1)
        # an overturn to central owes it, as a hunted record
        body["verdicts"][-1] = {"item": G.LEAD_ITEM, "target": "prominence", "action": "overturn",
                                "prominence": "central", "witness": "the article's centre"}
        self.assertRule(self.gate(body, vocab.RULES_STATEMENT_CUT), 1, "have no verdict")
        body["verdicts"].append(uphold)
        self.assertOk(self.gate(body, vocab.RULES_STATEMENT_CUT))
        # an earlier run's gate owes every record of its task, as it always did
        body["verdicts"][-2]["action"] = "uphold"
        body["verdicts"][-2].pop("prominence")
        body["verdicts"].pop()
        self.assertRule(self.gate(body, None), 1, "have no verdict")


# --------------------------------------------------------------------------- #
# the planner (sections 1.2 and 2.2): the flip's keys, and the earlier flow refused
# --------------------------------------------------------------------------- #

class TestThePlanner(unittest.TestCase):
    """A run planned now names the statement cut's versions; one planned before the flip
    (``F.before_the_flip``, as every run of an operator's workspace was) named none."""

    def setUp(self):
        self.root = scratch(self)
        with F.before_the_flip():
            self.paths = F.campaign(self, self.root)

    def plan(self, *argv: str) -> tuple[int, str, str]:
        return ws(self.root, "plan-locate", "--sourceset", F.SS, "--universe", F.U, *argv)

    def test_before_the_flip_nothing_new_was_written(self):
        with F.before_the_flip():
            F.plan_locate(self, self.root)
        gate = read_json(self.paths.gate_json("loc-1"))
        self.assertNotIn("statement_cut_rule", gate)
        self.assertEqual((gate["judgment_rules"], gate["span_end_rule"]), (2, 1))
        self.assertNotIn("status_rule", read_yaml(self.paths.run_yaml("loc-1"))["locate"])
        planned = read_json(self.paths.run_dir("loc-1") / "planned.json")
        rows = [p for t in planned["tasks"] for d in t["documents"] for p in d["passages"]]
        self.assertTrue(rows)
        self.assertFalse([r for r in rows if "statement" in r or "conjuncts" in r])

    def test_the_flip_writes_the_keys_and_refuses_the_old_flow(self):
        self.assertEqual((vocab.JUDGMENT_RULES, vocab.SPAN_END_RULE, vocab.STATEMENT_CUT_RULE),
                         (vocab.RULES_STATEMENT_CUT, vocab.SPAN_END_CONTINUED,
                          vocab.STATEMENT_CUT_CONTINUED))
        code, out, err = self.plan("--preset", "locate", "--inventory-run", "inv-1",
                                   "--run-id", "loc-9")
        self.assertNotEqual(code, 0)
        self.assertIn("planned before the statement cut", out + err)
        self.assertIn("inventory the sources again", out + err)
        code, out, err = self.plan("--preset", "locate-inventory", "--run-id", "inv-2")
        self.assertEqual(code, 0, out + err)
        gate = read_json(self.paths.gate_json("inv-2"))
        self.assertEqual((gate["judgment_rules"], gate["span_end_rule"],
                          gate["statement_cut_rule"]), (3, 3, 2))
        self.assertEqual(read_json(self.paths.run_dir("inv-2") / "gate.local.json")
                         ["statement_cut_rule"], vocab.STATEMENT_CUT_CONTINUED)
        docs = F.inventory_documents(self.paths, "inv-2")
        doc = docs[f"inv-{F.W1}/{F.W1}.json"]
        theorem = doc["passages"][1]
        theorem.update(conjuncts=[{"id": "c1", "text": "the gadget exists"},
                                  {"id": "c2", "text": "its size divides the order"}],
                       shape="conclusions")
        F.write_harvest(self.paths, "inv-2", docs)
        code, out, err = self.plan("--preset", "locate", "--inventory-run", "inv-2",
                                   "--run-id", "loc-2")
        self.assertEqual(code, 0, out + err)
        F.write_harvest(self.paths, "loc-2", F.locate_documents(self.paths, "loc-2"))
        code, out, err = self.plan("--preset", "locate-challenge", "--challenge-of",
                                   "loc-2", "--run-id", "chl-2", "--sample-rate", "1")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_yaml(self.paths.run_yaml("loc-2"))["locate"]["status_rule"],
                         vocab.STATUS_RULE)
        self.assertNotIn("status_rule", read_yaml(self.paths.run_yaml("chl-2"))["locate"])
        self.assertEqual(read_json(self.paths.gate_json("chl-2"))["judgment_rules"],
                         vocab.RULES_STATEMENT_CUT)
        self.assertEqual(read_yaml(self.paths.run_yaml("chl-2"))["locate"]
                         ["absence_sample_rate"], vocab.CHALLENGE_ABSENCE_SAMPLE_RATE)
        planned = read_json(self.paths.run_dir("loc-2") / "planned.json")
        rows = {p["item"]: p for t in planned["tasks"] for d in t["documents"]
                for p in d["passages"]}
        self.assertEqual(rows[theorem["item"]]["conjuncts"], theorem["conjuncts"])
        self.assertEqual(rows[theorem["item"]]["shape"], "conclusions")
        self.assertTrue(all("statement" in r for r in rows.values()))
        self.assertFalse([r for i, r in rows.items() if i != theorem["item"] and "conjuncts" in r])
        # a challenge planned now of a locate run judged under rules 2 is refused
        with F.before_the_flip():
            F.plan_locate(self, self.root)
        code, out, err = self.plan("--preset", "locate-challenge", "--challenge-of",
                                   "loc-1", "--run-id", "chl-8")
        self.assertNotEqual(code, 0)
        self.assertIn("judged under judgment rules 2", out + err)

    def test_a_locate_plan_over_an_inventory_of_statement_cut_rule_1(self):
        """An inventory planned before the flip of inv-pilot-1's fixes names (2, 1), as
        inv-pilot-1 does; a locate run planned now over it is admitted (it reads the
        inventory's conjuncts, which rule 1 cut too) and names the continued cut's
        versions, which bind nothing in a locate document."""
        with F.before_the_continuation():
            code, out, err = self.plan("--preset", "locate-inventory", "--run-id", "inv-3")
        self.assertEqual(code, 0, out + err)
        gate = read_json(self.paths.gate_json("inv-3"))
        self.assertEqual((gate["judgment_rules"], gate["span_end_rule"],
                          gate["statement_cut_rule"]), (3, 2, 1))
        F.write_harvest(self.paths, "inv-3", F.inventory_documents(self.paths, "inv-3"))
        code, out, err = self.plan("--preset", "locate", "--inventory-run", "inv-3",
                                   "--run-id", "loc-3")
        self.assertEqual(code, 0, out + err)
        gate = read_json(self.paths.gate_json("loc-3"))
        self.assertEqual((gate["span_end_rule"], gate["statement_cut_rule"]), (3, 2))

    def test_the_challenge_manifest_carries_the_records_cut_fields_after_the_flip(self):
        from locate import plan as lplan

        built = {"batches": [[{"key": "k", "target": "absence", "item": "In-text #00000001",
                               "source_id": F.W1, "statement": "a restatement",
                               "conjuncts": [{"id": "c1", "text": "a"}, {"id": "c2", "text": "b"}],
                               "shape": "iff", "conditional": "prominence"}]]}
        manifest = __import__("locate.inputs", fromlist=["manifest"]).manifest(self.paths, F.SS)
        with F.before_the_flip():
            before = lplan.challenge_tasks(manifest, built)[0].documents[0]["records"][0]
        self.assertFalse({"statement", "conjuncts", "shape", "conditional"} & set(before))
        after = lplan.challenge_tasks(manifest, built)[0].documents[0]["records"][0]
        self.assertEqual(after["shape"], "iff")
        self.assertEqual(after["conditional"], "prominence")


if __name__ == "__main__":
    unittest.main()
