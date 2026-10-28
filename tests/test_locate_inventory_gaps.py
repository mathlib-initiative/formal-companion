"""The gap audit (audit rule 3): gaps found, drawn block by block, judged, repaired and counted.

New code, for references/locate-documents.md section 13.12 (``cf-inventory-audit/3``):
``scripts/gate/inventory_audit.py --rule 3`` (each run of a block's own text of the body
that no passage and no skip holds, of eight words or more, a gap ``r<k>-g<nnn>``; every gap
judged once, ``clean`` or ``gap_statements``; the skeptic's gap hunt; strip, the merge's
evidence inside the gap, a carried verdict mapped to the next snapshot's gaps; the
``gap_report``; the kind rule over every formal ``other`` passage with its expository
answer) and ``scripts/gate/inventory_render.py --rule 3`` (the GAP lines block by block,
in full to 1,200 characters, the gaps' header line, the index's ``gaps`` and each part's,
``--show <gap id>``, the diff's GAPS and GAP VERDICT lines, the advisory ``commentary``
hint), with the edit tool's ``add`` and the gate as the repair.  The two shapes the smoke-4
audit found no record for are synthetic cases here: a formula-free paragraph inside a long
gap, which the first renderer clipped away, and a notation sentence left in a gap; so are
the seven narration patterns the ``commentary`` hint reads.  Rules 1 and 2 render, validate
and merge what 3b29518 did.  The page is ``tests/_locate_fixtures.py``'s made-up "Widget
gaps"; every text is synthetic.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import shutil
import subprocess
import sys
import tempfile
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
import test_locate_inventory_audit as TL  # noqa: E402
import test_locate_inventory_skips as TS  # noqa: E402
import test_verify_locate as G  # noqa: E402
from cflib import vocab  # noqa: E402
from gate import locate_text as lt  # noqa: E402

ia, ie, ir = TL.ia, TL.ie, TL.ir
SID, TASK = TL.SID, TL.TASK
LAYER, PARAGRAPHS, FORMULAS = F.GAP_LAYER, F.GAP_PARAGRAPHS, F.GAP_FORMULAS
SCHEMA3 = ia.AUDIT_SCHEMAS[3]
#: the commit whose rule-1 and rule-2 tools this branch must reproduce
BASE = "3b29518"
NOTATION_AT = "We write N for the product"


def doc_of(**kw) -> dict:
    """A gap document (the notation gap by default) with its header."""
    return {"schema": "cf-locate/1", "mode": "locate-inventory", "run_id": "r-1",
            "task_id": TASK, "source_id": SID, **F.gap_body(**kw)}


def gfacts(doc: dict, round_: int = 1) -> list[dict]:
    return ia.gap_facts(doc, PARAGRAPHS, LAYER, round_)


def blocks():
    return lt.Blocks(LAYER, PARAGRAPHS, FORMULAS)


def audit3(doc: dict, gaps: dict | None = None, **passages) -> dict:
    """A rule-3 round-1 audit: every formal passage, skip and gap clean, but the rows given
    (``passages`` by item, ``gaps`` by gap id)."""
    rows = []
    for item in TL.formal(doc):
        row = {"item": item, "origin": "statement_section", "verdict": "clean", "rule": 0,
               "merge_with": "", "reason": "", "action": ""}
        row.update(passages.get(item, {}))
        rows.append(row)
    skip_rows = [{"skip": f["id"], "verdict": "clean", "rule": 0, "merge_with": "",
                  "reason": "", "action": "", "statements": []}
                 for f in ia.skip_facts(doc, PARAGRAPHS, 1)]
    gap_rows = []
    for fact in gfacts(doc):
        row = {"gap": fact["id"], "verdict": "clean", "rule": 0, "merge_with": "",
               "reason": "", "action": "", "statements": []}
        row.update((gaps or {}).get(fact["id"], {}))
        gap_rows.append(row)
    return {"schema": SCHEMA3, "kind": "audit", "round": 1, "source_id": SID,
            "passages": rows, "expository_flags": [], "per_spec_formal_estimate": 6,
            "patterns": [], "subject_check": "one subject", "skips": skip_rows,
            "gaps": gap_rows}


#: the notation gap as an auditor flags it: the notation passage it holds (C7)
NOTATION_FLAG = {"verdict": "gap_statements", "rule": 7, "merge_with": "",
                 "reason": "a notation sentence the next statement uses",
                 "action": "add it as a notation passage",
                 "statements": [{"at": NOTATION_AT, "until": "", "passage_kind": "notation"}]}


def skeptic3(flags: dict, doc: dict, *, upheld=(), evidence: dict | None = None,
             examined_gaps=None, missed_gaps=(), round_: int = 1) -> dict:
    """A rule-3 skeptic: every flag ruled (``upheld`` by flag id, item or gap id), the
    passage hunt over every unflagged formal passage, the skip and gap hunts over every
    unflagged skip and gap (``examined_gaps`` to override)."""
    verdicts = []
    for f in flags["flags"]:
        target = f.get("gap") or f.get("skip") or f["item"]
        ok = f["flag_id"] in upheld or target in upheld
        verdicts.append({"flag_id": f["flag_id"], "upheld": ok, "verdict": f["verdict"],
                         "rule": f["rule"] if ok else 0,
                         "evidence": (evidence or {}).get(target, "") if ok else "",
                         "reason": "the spec says so"})
    flagged_items = {f["item"] for f in flags["flags"]}
    flagged_gaps = {f.get("gap") for f in flags["flags"]}
    if examined_gaps is None:
        examined_gaps = [f["id"] for f in gfacts(doc, round_) if f["id"] not in flagged_gaps]
    return {"schema": SCHEMA3, "kind": "skeptic", "round": round_, "source_id": SID,
            "verdicts": verdicts, "missed": [], "disputes": [],
            "examined": [i for i in TL.formal(doc) if i not in flagged_items],
            "per_spec_formal_estimate": 6, "summary": "fine", "examined_skips": [],
            "missed_skips": [], "examined_gaps": list(examined_gaps),
            "missed_gaps": list(missed_gaps)}


def merge1(doc: dict, audit: dict, skeptic: dict) -> dict:
    return ia.merge_round1(doc, [audit], skeptic, layer=LAYER, rule=3, paragraphs=PARAGRAPHS)


def verdicts1(doc: dict, merged: dict) -> dict:
    return ia.verdicts_file(1, 3, doc, "0" * 64, merged, None, rule=3)


class GapCase(TL.HostCase):
    """The Widget-gaps page in a staged gate directory under the prose floor, its document
    the task's."""

    LAYER = F.GAP_LAYER
    FORMULAS = F.GAP_FORMULAS
    PARAGRAPHS = F.GAP_PARAGRAPHS
    KEYS = {**SC.CUT_3, "prose_floor_rule": vocab.PROSE_FLOOR_RULE}
    BODY: dict = {}

    def setUp(self):
        super().setUp()
        self.fx.manifest(TASK, {"source_id": G.SID, "source_sha": G.HTML_SHA,
                                "layer_sha256": self.fx.layer_sha, "renderer": "web-html/1",
                                "finders": []})
        self.write_body(**self.BODY)

    def write_body(self, **kw) -> dict:
        doc = {**self.fx.header(TASK), **F.gap_body(**kw)}
        self.doc_path.write_text(json.dumps(doc), encoding="utf-8")
        return doc

    def render(self, *argv: str) -> subprocess.CompletedProcess:
        return self.tool("inventory_render.py", "--gate-config", self.gate_json, "--document",
                         self.doc_rel, *argv)

    def gate(self) -> subprocess.CompletedProcess:
        return self.tool("verify_locate.py", "--gate-config", self.gate_json, "--findings",
                         self.doc_rel)


# --------------------------------------------------------------------------- #
# the vocabulary
# --------------------------------------------------------------------------- #

class TestTheGapVocabularyIsOneDefinition(unittest.TestCase):

    PAIRS = {"GAP_VERDICT": "AUDIT_GAP_VERDICT", "GAP_VERDICTS": "AUDIT_GAP_VERDICTS",
             "GAP_MIN_WORDS": "AUDIT_GAP_MIN_WORDS", "GAP_BLOCK_MAX": "AUDIT_GAP_BLOCK_MAX",
             "GAP_BLOCK_SIDE": "AUDIT_GAP_BLOCK_SIDE",
             "KIND_RULE_ACTION_3": "AUDIT_KIND_RULE_ACTION_3",
             "COMMENTARY_EFFICIENCY": "AUDIT_COMMENTARY_EFFICIENCY",
             "COMMENTARY_COMPLEXITY": "AUDIT_COMMENTARY_COMPLEXITY",
             "COMMENTARY_STARTS": "AUDIT_COMMENTARY_STARTS",
             "COMMENTARY_PHRASES": "AUDIT_COMMENTARY_PHRASES",
             "AUDIT_RULES": "INVENTORY_AUDIT_RULES", "AUDIT_SCHEMAS": "INVENTORY_AUDIT_SCHEMAS"}

    def test_the_host_copy_is_cflibs(self):
        for host, ours in self.PAIRS.items():
            with self.subTest(host=host):
                self.assertEqual(getattr(ia, host), getattr(vocab, ours))
        self.assertEqual(vocab.AUDIT_GAP_MIN_WORDS, vocab.PROSE_FLOOR_MIN_WORDS)
        self.assertTrue(vocab.AUDIT_KIND_RULE_ACTION_3.startswith(ia.KIND_RULE_ACTION))

    def test_rule_3_adds_and_rules_1_and_2_keep(self):
        self.assertNotIn("gaps", ia.RECORDS_BY_RULE[2]["audit"][0])
        self.assertIn("gaps", ia.RECORDS_BY_RULE[3]["audit"][0])
        self.assertIn("examined_gaps", ia.RECORDS_BY_RULE[3]["skeptic"][0])
        self.assertNotIn("examined_gaps", ia.RECORDS_BY_RULE[2]["skeptic"][0])
        self.assertEqual(ia.OP_KEYS_BY_RULE[3], ia.OP_KEYS_BY_RULE[2])
        self.assertEqual(ia.edit_ops(3), ia.edit_ops(2))
        self.assertEqual(ia.verdict_origins(3), ia.verdict_origins(2))
        self.assertNotIn(vocab.AUDIT_GAP_VERDICT, vocab.AUDIT_VERDICTS)
        self.assertEqual(ia.kind_rule_action(2), ia.KIND_RULE_ACTION)
        self.assertEqual(ia.kind_rule_action(3), ia.KIND_RULE_ACTION_3)

    def test_every_value_is_documented(self):
        vocabulary = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")
        documents = (SKILL / "references" / "locate-documents.md").read_text(encoding="utf-8")
        for value in (vocab.AUDIT_GAP_VERDICT, vocab.INVENTORY_AUDIT_SCHEMAS[3], "commentary",
                      "examined_gaps", "missed_gaps", "gap_report"):
            with self.subTest(value=value):
                self.assertIn(f"`{value}`", vocabulary)
                self.assertIn(f"`{value}`", documents)
        for name in ("AUDIT_GAP_VERDICT", "AUDIT_GAP_MIN_WORDS", "AUDIT_GAP_BLOCK_MAX",
                     "AUDIT_KIND_RULE_ACTION_3", "AUDIT_COMMENTARY_STARTS"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", vocabulary)


# --------------------------------------------------------------------------- #
# the facts
# --------------------------------------------------------------------------- #

class TestTheGaps(unittest.TestCase):

    def test_a_notation_sentence_left_in_a_gap(self):
        """The CRT shape: the notation sentence of a paragraph whose set-up sentence alone
        is a passage is a run no passage and no skip holds."""
        (fact,) = gfacts(doc_of())
        a, b = fact["span"]
        self.assertEqual(LAYER[a:b], F.GAP_NOTATION)
        self.assertEqual((fact["id"], fact["block"], fact["section"], fact["whole"]),
                         ("r1-g001", "p", ["Moduli"], False))
        self.assertEqual(fact["words"], len(F.GAP_NOTATION.split()))
        self.assertEqual(gfacts(doc_of(notation=True)), [])

    def test_formula_free_paragraphs_left_whole_are_gaps(self):
        facts = gfacts(doc_of(blocks="silent"))
        self.assertEqual([f["id"] for f in facts], ["r1-g001", "r1-g002", "r1-g003", "r1-g004"])
        self.assertEqual([f["span"] for f in facts[:3]],
                         [F.GAP_SPANS[n] for n in ("BB1", "BB2", "BB3")])
        self.assertTrue(all(f["whole"] for f in facts[:3]))
        self.assertEqual(LAYER[slice(*facts[1]["span"])], F.GAP_STATEMENT)
        # a skip holds them as a passage does
        self.assertEqual(len(gfacts(doc_of(blocks="skip"))), 1)

    def test_a_short_run_and_the_back_matter_are_no_gaps(self):
        doc = doc_of()
        doc["skips"] = [s for s in doc["skips"] if s["reason"] != "references"]
        spans = [f["span"] for f in gfacts(doc)]
        self.assertNotIn(F.GAP_SPANS["SHORT"], spans)
        self.assertNotIn(F.GAP_SPANS["REF1"], spans)
        self.assertEqual(len(spans), 1)
        doc = doc_of(history="silent")
        self.assertIn(F.GAP_SPANS["HIST"], [f["span"] for f in gfacts(doc)])

    def test_a_blocks_own_text_less_its_nested_blocks(self):
        rows = [{"span": [0, 100], "block": "li", "section": ["S"], "math": False},
                {"span": [20, 40], "block": "p", "section": ["S"], "math": True},
                {"span": [20, 40], "block": "theorem_box", "section": ["S"], "math": True},
                {"span": [60, 70], "block": "dd", "section": ["S"], "math": False}]
        pieces = [(tuple(p["span"]), p["block"]) for p in ia.own_text(rows)]
        self.assertEqual(pieces, [((0, 20), "li"), ((20, 40), "p"), ((40, 60), "li"),
                                  ((60, 70), "dd"), ((70, 100), "li")])
        self.assertEqual(ia.subtract((0, 10), [(2, 3), (1, 4), (8, 12)]),
                         [(0, 1), (4, 8)])

    def test_the_report(self):
        report = ia.gap_report(gfacts(doc_of(blocks="silent")))
        words = [len(LAYER[slice(*F.GAP_SPANS[n])].split()) for n in ("BB1", "BB2", "BB3")]
        self.assertEqual(report["gaps"], 4)
        self.assertEqual(report["whole"], 3)
        self.assertEqual(report["by_section"]["Building blocks"],
                         {"gaps": 3, "words": sum(words)})
        self.assertEqual(report["by_section"]["Moduli"]["gaps"], 1)
        self.assertEqual(ia.gap_report([]), {"gaps": 0, "words": 0, "whole": 0,
                                             "by_section": {}})


# --------------------------------------------------------------------------- #
# the rendering
# --------------------------------------------------------------------------- #

class TestTheRendering(unittest.TestCase):

    def render(self, doc: dict, rule: int) -> str:
        return ir.render(doc, LAYER, blocks(), rule=rule, paragraphs=PARAGRAPHS)

    def test_a_formula_free_paragraph_inside_a_long_gap_is_drawn_whole(self):
        """The CFSG shape: three formula-free paragraphs between two passages.  Rule 2 draws
        them as one GAP line clipped to its first and last 200 characters, so the statement
        in the middle one is never on screen; rule 3 draws each block whole, the statement a
        gap line of its own."""
        doc = doc_of(blocks="silent")
        two, three = self.render(doc, 2), self.render(doc, 3)
        self.assertNotIn(F.GAP_STATEMENT, two)
        self.assertIn(f"GAP r1-g002 · [532, 607] · p · 15 word(s) · Building blocks: "
                      f"{F.GAP_STATEMENT}", three)
        for name in ("BB1", "BB3"):
            self.assertIn(LAYER[slice(*F.GAP_SPANS[name])], three)
        self.assertIn("Gaps: 4 (r1-g001 to r1-g004)", three)
        self.assertIn(ir.EXPLAIN_GAPS[0], three)

    def test_the_notation_gap_is_a_line_of_its_own(self):
        three = self.render(doc_of(), 3)
        self.assertIn(f"GAP r1-g001 · [979, 1048] · p · 15 word(s) · Moduli: {F.GAP_NOTATION}",
                      three)
        self.assertIn("Gaps: 1 (r1-g001), 15 word(s)", three)
        self.assertIn("GAP [1233, 1254] p: See the gadgets page.", three)
        self.assertNotIn("Gaps:", self.render(doc_of(), 2))
        self.assertEqual(ir.gap_summary([]), ["Gaps: none."])

    def test_a_long_block_is_clipped_past_its_cap(self):
        fact = {"id": "r1-g001", "span": [0, 2000], "block": "p", "section": ["S"],
                "words": 300}
        text = "w " * 1000
        line = ir.gap_line3(fact, text)
        body = line.split(": ", 1)[1]
        self.assertIn(" … ", body)
        self.assertLess(len(body), ia.GAP_BLOCK_MAX)
        self.assertNotIn(" … ", ir.gap_line3(fact, text, cap=False))

    def test_the_parts_and_the_index_name_each_gap(self):
        doc = doc_of(blocks="silent")
        cut = ir.parts(doc, LAYER, blocks(), 1000, rule=3, paragraphs=PARAGRAPHS)
        self.assertGreater(len(cut), 1)
        shown = [g for part in cut for g in part["gaps"]]
        self.assertEqual(sorted(shown), [f["id"] for f in gfacts(doc)])
        for part in cut:
            for gid in part["gaps"]:
                self.assertIn(f"GAP {gid} ·", part["text"])
        self.assertNotIn("gaps", ir.parts(doc, LAYER, blocks(), rule=2,
                                          paragraphs=PARAGRAPHS)[0])


class TestTheCommentaryHint(unittest.TestCase):
    """Rule 3's advisory ``commentary`` hint, on the seven narration patterns smoke 4 kept
    formal (synthetic sentences each), and on what it must not mark."""

    SEVEN = (
        # a "need not" clause cut from its condition
        "the widget need not be finite",
        # which method may be used
        "every general counting method may be used for the gadget search",
        # an efficiency claim
        "sieving the counts makes the search dramatically faster",
        # an ordering that is faster
        "the search is faster with the counts in decreasing order",
        # an instruction of a computation
        "compute the remainder of the gadget count by 7",
        # an instruction whose number is a product
        "proceed by adding 6 = 2 × 3 to the count",
        # the set-up of an illustration
        "to illustrate, let W have 6 nodes and g = 2",
    )
    NOT = ("every finite widget has a spanning gadget",
           "the search runs in polynomial time and is faster than the naive one",
           "a faster gadget has at most 3 edges",
           "computes nothing",
           "the gadget count is computed as 6 = 2 × 3")

    def hint(self, text: str, rule: int, scope: str = "formal_math") -> list[str]:
        layer = text + " "
        passage = {"item": "In-text #00000001", "span": [0, len(text)], "scope": scope,
                   "passage_kind": "theorem", "section": ["Computation"]}
        return ir.hints(passage, layer, None, rule)

    def test_the_seven_are_marked_under_rule_3_only(self):
        for text in self.SEVEN:
            with self.subTest(text=text):
                self.assertTrue(ia.commentary_hint(text))
                self.assertIn("commentary", self.hint(text, 3))
                self.assertNotIn("commentary", self.hint(text, 2))
                self.assertNotIn("commentary", self.hint(text, 3, scope="expository"))

    def test_what_it_does_not_mark(self):
        for text in self.NOT:
            with self.subTest(text=text):
                self.assertFalse(ia.commentary_hint(text))


class TestTheRenderingOnTheHost(GapCase):

    def test_the_index_and_a_part_judge_their_gaps(self):
        self.write_body(blocks="skip", history="silent")
        proc = self.render("--rule", "3", "--out-dir", ".tend/audit", "--label", "r1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        index = json.loads((self.fx.root / ".tend" / "audit" / "r1.render.json").read_text())
        self.assertEqual((index["rule"], index["schema"]), (3, SCHEMA3))
        self.assertEqual([g["id"] for g in index["gaps"]], ["r1-g001", "r1-g002"])
        self.assertEqual(index["gaps"][1]["section"], "History")
        self.assertEqual(index["gaps"][1]["part"], 1)
        self.assertEqual(index["parts"][0]["gaps"], ["r1-g001", "r1-g002"])
        self.assertIn("skips", index)

    def test_show_a_gap_and_rule_2_refuses_its_id(self):
        proc = self.render("--rule", "3", "--show", "r1-g001", "--context", "40")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertTrue(lines[0].startswith("GAP r1-g001 · [979, 1048]"), lines)
        self.assertTrue(lines[1].startswith("  BEFORE: "))
        self.assertIn("pairwise coprime throughout.", lines[1])
        self.assertTrue(lines[2].startswith("  AFTER: "))
        self.assertIn("Then a widget", lines[2])
        self.assertEqual(self.render("--rule", "2", "--show", "r1-g001").returncode, 2)
        self.assertEqual(self.render("--rule", "3", "--show", "r1-g009").returncode, 2)


class TestRulesOneAndTwoAreTheBaseCommits(unittest.TestCase):
    """3b29518's renderer and this one, at rules 1 and 2, on the skip and the gap pages."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        for rel, name in (("scripts/gate/inventory_render.py", "inventory_render.py"),
                          ("scripts/gate/inventory_audit.py", "inventory_audit.py"),
                          ("scripts/gate/locate_text.py", "locate_text.py"),
                          ("scripts/gate/verify_locate.py", "verify_locate.py"),
                          ("scripts/gate/verify_findings.py", "verify_findings.py"),
                          ("scripts/locate/records.py", "locate_records.py")):
            out = subprocess.run(["git", "-C", str(SKILL), "show", f"{BASE}:{rel}"],
                                 capture_output=True)
            if out.returncode != 0:
                self.skipTest(f"this checkout does not hold commit {BASE}")
            (self.base / name).write_bytes(out.stdout)
        shutil.copytree(SKILL / "vendor", self.base / "vendor",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    def base_render(self, doc: dict, layer: str, paragraphs, formulas, rule: int) -> str:
        script = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(self.base)!r})\n"
            "import inventory_render as ir, locate_text as lt\n"
            "doc, layer, paragraphs, formulas, rule = json.load(sys.stdin)\n"
            "blocks = lt.Blocks(layer, paragraphs, formulas)\n"
            "sys.stdout.write(ir.render(doc, layer, blocks, rule=rule, paragraphs=paragraphs))\n")
        proc = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True,
                              text=True, input=json.dumps([doc, layer, paragraphs, formulas,
                                                           rule]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_byte_for_byte(self):
        pages = [(doc_of(**kw), LAYER, PARAGRAPHS, FORMULAS)
                 for kw in ({}, {"blocks": "silent"}, {"blocks": "skip", "notation": True})]
        pages += [(TS.doc_of(**kw), F.SKIP_LAYER, F.SKIP_PARAGRAPHS, F.SKIP_FORMULAS)
                  for kw in ({}, {"proof": False, "display": True})]
        for n, (doc, layer, paragraphs, formulas) in enumerate(pages):
            for rule in (1, 2):
                with self.subTest(page=n, rule=rule):
                    now = ir.render(doc, layer, lt.Blocks(layer, paragraphs, formulas),
                                    rule=rule, paragraphs=paragraphs)
                    self.assertEqual(now, self.base_render(doc, layer, paragraphs, formulas,
                                                           rule))


# --------------------------------------------------------------------------- #
# the gap rows, the skeptic's hunt and the text checks
# --------------------------------------------------------------------------- #

class TestTheGapRows(unittest.TestCase):

    def setUp(self):
        self.doc = doc_of()
        self.facts = gfacts(self.doc)

    def problems(self, audit: dict, **kw) -> list[str]:
        return ia.validate_audit(audit, self.doc, rule=3,
                                 skips=ia.skip_facts(self.doc, PARAGRAPHS, 1),
                                 gaps=self.facts, **kw)

    def test_a_sound_audit(self):
        self.assertEqual(self.problems(audit3(self.doc)), [])
        self.assertEqual(self.problems(audit3(self.doc, {"r1-g001": NOTATION_FLAG})), [])

    def test_every_gap_once_and_its_rules(self):
        audit = audit3(self.doc)
        audit["gaps"] = []
        self.assertIn("audit.gaps: 1 gap(s) not judged: r1-g001", self.problems(audit))
        audit = audit3(self.doc)
        audit["gaps"].append(dict(audit["gaps"][0]))
        self.assertIn("audit.gaps: gap r1-g001 is judged 2 times, not once",
                      self.problems(audit))
        audit = audit3(self.doc)
        audit["gaps"][0]["gap"] = "r1-g002"
        self.assertIn("audit.gaps[0]: r1-g002 is no gap of the document", self.problems(audit))
        for row, needle in (
                ({"statements": NOTATION_FLAG["statements"]}, "a clean gap lists no statements"),
                ({"verdict": "gap_statements", "rule": 7, "reason": "x y z", "action": "a b c"},
                 "lists no statement and names no merge_with (the kept passage the gap's "
                 "text belongs to)"),
                ({**NOTATION_FLAG, "rule": 0}, "names no rule"),
                ({"verdict": "skipped_statements"}, "not one of clean, gap_statements")):
            with self.subTest(needle=needle):
                audit = audit3(self.doc, {"r1-g001": row})
                self.assertTrue(any(needle in p for p in self.problems(audit)),
                                self.problems(audit))

    def test_the_schema_is_the_rules(self):
        audit = audit3(self.doc)
        del audit["gaps"]
        self.assertIn("audit has no 'gaps'", self.problems(audit))
        two = audit3(self.doc)
        two["schema"] = ia.AUDIT_SCHEMAS[2]
        del two["gaps"]
        self.assertEqual(ia.validate_audit(two, self.doc, rule=2,
                                           skips=ia.skip_facts(self.doc, PARAGRAPHS, 1)), [])
        two["gaps"] = []
        self.assertIn("audit has the key 'gaps', which audit does not take",
                      ia.validate_audit(two, self.doc, rule=2))

    def test_a_part_judges_the_gaps_it_shows(self):
        audit = audit3(self.doc)
        self.assertEqual(self.problems(audit, expect_gaps=["r1-g001"]), [])
        audit["gaps"] = []
        self.assertEqual(self.problems(audit, expect_gaps=[]), [])
        self.assertIn("audit: the part names r1-g004, which is no gap of the document",
                      self.problems(audit, expect_gaps=["r1-g004"]))

    def test_the_skeptics_gap_hunt(self):
        audit = audit3(self.doc)
        flags = ia.strip(1, self.doc, audits=[audit], rule=3, gaps=self.facts)
        self.assertEqual(flags["flags"], [])
        ok = skeptic3(flags, self.doc)
        self.assertEqual(ia.validate_skeptic(ok, self.doc, flags, round_=1, rule=3,
                                             gaps=self.facts), [])
        lazy = skeptic3(flags, self.doc, examined_gaps=[])
        self.assertIn("skeptic.examined_gaps leaves out 1 unflagged gap(s): r1-g001",
                      ia.validate_skeptic(lazy, self.doc, flags, round_=1, rule=3,
                                          gaps=self.facts))
        flagged = audit3(self.doc, {"r1-g001": NOTATION_FLAG})
        flags = ia.strip(1, self.doc, audits=[flagged], rule=3, gaps=self.facts)
        upheld = skeptic3(flags, self.doc, upheld=["r1-g001"],
                          evidence={"r1-g001": NOTATION_AT})
        self.assertEqual(ia.validate_skeptic(upheld, self.doc, flags, round_=1, rule=3,
                                             gaps=self.facts), [])
        upheld["verdicts"][0]["verdict"] = "should_be_expository"
        self.assertTrue(any("a gap flag is upheld as gap_statements" in p for p in
                            ia.validate_skeptic(upheld, self.doc, flags, round_=1, rule=3,
                                                gaps=self.facts)))
        # a passage's flag is never upheld as the gap's verdict
        passage = audit3(self.doc, **{TL.formal(self.doc)[0]: {
            "verdict": "statement_misstates", "rule": 7, "reason": "a b c", "action": "d e f"}})
        flags = ia.strip(1, self.doc, audits=[passage], rule=3, gaps=self.facts)
        bad = skeptic3(flags, self.doc, upheld=[flags["flags"][0]["flag_id"]])
        bad["verdicts"][0]["verdict"] = "gap_statements"
        self.assertTrue(any("is a gap's verdict, not a passage's" in p for p in
                            ia.validate_skeptic(bad, self.doc, flags, round_=1, rule=3,
                                                gaps=self.facts)))

    def test_the_text_checks(self):
        far = {**NOTATION_FLAG, "statements": [{"at": "The sprocket counts c1",
                                                "until": "", "passage_kind": "notation"}]}
        problems = ia.gap_text_problems(audit3(self.doc, {"r1-g001": far}), "audit",
                                        self.facts, LAYER)
        self.assertTrue(problems and "is not in the text of r1-g001" in problems[0], problems)
        self.assertEqual(ia.gap_text_problems(audit3(self.doc, {"r1-g001": NOTATION_FLAG}),
                                              "audit", self.facts, LAYER), [])
        flags = ia.strip(1, self.doc, audits=[audit3(self.doc, {"r1-g001": NOTATION_FLAG})],
                         rule=3, gaps=self.facts)
        outside = skeptic3(flags, self.doc, upheld=["r1-g001"],
                           evidence={"r1-g001": "Then a widget of order"})
        self.assertTrue(any("an upheld gap flag quotes words inside the gap" in p for p in
                            ia.gap_text_problems(outside, "skeptic", self.facts, LAYER,
                                                 flags)))


class TestTheValidatorOnTheHost(GapCase):

    def test_validate_reads_the_parts_gaps_from_the_index(self):
        proc = self.render("--rule", "3", "--out-dir", ".tend/audit", "--label", "r1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.doc()
        path = self.fx.root / ".tend" / "audit" / "r1.audit.part1.json"
        audit = audit3(doc)
        path.write_text(json.dumps(audit), encoding="utf-8")
        argv = ("validate", "--kind", "audit", "--file", ".tend/audit/r1.audit.part1.json",
                "--document", self.doc_rel, "--render-index", ".tend/audit/r1.render.json",
                "--part", "1", "--rule", "3")
        ok = self.tool("inventory_audit.py", *argv)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        audit["gaps"] = []
        path.write_text(json.dumps(audit), encoding="utf-8")
        bad = self.tool("inventory_audit.py", *argv)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("1 gap(s) not judged: r1-g001", bad.stdout)

    def test_strip_at_rule_3_reads_the_layer(self):
        path = self.fx.root / ".tend" / "audit" / "r1.audit.part1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(audit3(self.doc(), {"r1-g001": NOTATION_FLAG})),
                        encoding="utf-8")
        argv = ("strip", "--round", "1", "--document", self.doc_rel, "--audit",
                ".tend/audit/r1.audit.part1.json", "--out", ".tend/audit/r1.flags.json",
                "--rule", "3")
        refused = self.tool("inventory_audit.py", *argv)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("--gate-config", refused.stderr)
        done = self.tool("inventory_audit.py", *argv, "--gate-config", self.gate_json)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        (flag,) = json.loads((self.fx.root / ".tend" / "audit" / "r1.flags.json")
                             .read_text())["flags"]
        self.assertEqual((flag["gap"], flag["span"], flag["item"]), ("r1-g001", [979, 1048], ""))
        self.assertNotIn("statements", flag)
        self.assertNotIn("action", flag)


# --------------------------------------------------------------------------- #
# the merge
# --------------------------------------------------------------------------- #

class TestStripAndMerge(unittest.TestCase):

    def setUp(self):
        self.doc = doc_of()
        self.audit = audit3(self.doc, {"r1-g001": NOTATION_FLAG})
        self.flags = ia.strip(1, self.doc, audits=[self.audit], rule=3, gaps=gfacts(self.doc))

    def test_an_upheld_gap_flag_with_evidence_inside_the_gap(self):
        merged = merge1(self.doc, self.audit, skeptic3(self.flags, self.doc, upheld=["r1-g001"],
                                                       evidence={"r1-g001": NOTATION_AT}))
        (v,) = merged["verdicts"]
        mod1 = next(p["item"] for p in self.doc["passages"]
                    if p["span"][0] == F.GAP_SPANS["MOD1"][0])
        self.assertEqual((v["id"], v["gap"], v["gaps"], v["span"], v["verdict"], v["origin"]),
                         ("r1-v001", "r1-g001", ["r1-g001"], [979, 1048], "gap_statements",
                          "auditor"))
        self.assertEqual((v["statements"], v["after"], v["words"], v["item"]),
                         (NOTATION_FLAG["statements"], mod1, 15, ""))
        counts = merged["counts"]
        self.assertEqual((counts["gaps"], counts["gaps_judged"], counts["gaps_flagged"],
                          counts["gaps_upheld"], counts["gap_statements"], counts["flagged"]),
                         (1, 1, 1, 1, 1, 0))
        self.assertEqual(merged["gap_report"]["gaps"], 1)
        out = verdicts1(self.doc, merged)
        self.assertEqual((out["decision"], out["decision_rule"], out["schema"]),
                         ("request_changes", "audit/3", SCHEMA3))

    def test_evidence_outside_the_gap_refutes_it(self):
        merged = merge1(self.doc, self.audit, skeptic3(
            self.flags, self.doc, upheld=["r1-g001"],
            evidence={"r1-g001": "pairwise coprime throughout"}))
        self.assertEqual(merged["verdicts"], [])
        self.assertEqual((merged["refuted"][0]["gap"], merged["refuted"][0]["why"]),
                         ("r1-g001", "no_evidence"))
        self.assertEqual(merged["counts"]["gaps_refuted"], 1)

    def test_a_missed_gap_needs_evidence_inside_it(self):
        clean = audit3(self.doc)
        flags = ia.strip(1, self.doc, audits=[clean], rule=3, gaps=gfacts(self.doc))
        missed = {**NOTATION_FLAG, "gap": "r1-g001", "evidence": NOTATION_AT}
        merged = merge1(self.doc, clean, skeptic3(flags, self.doc, examined_gaps=["r1-g001"],
                                                  missed_gaps=[missed]))
        (v,) = merged["verdicts"]
        self.assertEqual((v["origin"], v["gap"]), ("skeptic_missed", "r1-g001"))
        merged = merge1(self.doc, clean, skeptic3(flags, self.doc, examined_gaps=["r1-g001"],
                                                  missed_gaps=[{**missed,
                                                                "evidence": "Then a widget"}]))
        self.assertEqual(merged["verdicts"], [])
        self.assertEqual(merged["counts"]["gap_missed_dropped"], 1)

    def test_a_statement_anchored_outside_its_gap_is_dropped(self):
        row = {**NOTATION_FLAG, "statements": [*NOTATION_FLAG["statements"],
                                               {"at": "The sprocket counts c1", "until": "",
                                                "passage_kind": "other"}]}
        audit = audit3(self.doc, {"r1-g001": row})
        flags = ia.strip(1, self.doc, audits=[audit], rule=3, gaps=gfacts(self.doc))
        merged = merge1(self.doc, audit, skeptic3(flags, self.doc, upheld=["r1-g001"],
                                                  evidence={"r1-g001": NOTATION_AT}))
        (v,) = merged["verdicts"]
        self.assertEqual(v["statements"], NOTATION_FLAG["statements"])
        self.assertEqual(merged["counts"]["gap_statements_dropped"], 1)

    def test_no_gap_no_verdict_and_rule_2_merges_as_before(self):
        doc = doc_of(notation=True)
        audit = audit3(doc)
        flags = ia.strip(1, doc, audits=[audit], rule=3, gaps=gfacts(doc))
        merged = merge1(doc, audit, skeptic3(flags, doc))
        self.assertEqual(merged["verdicts"], [])
        self.assertEqual(verdicts1(doc, merged)["decision"], "approve")
        two = {k: v for k, v in audit.items() if k != "gaps"}
        two["schema"] = ia.AUDIT_SCHEMAS[2]
        merged2 = ia.merge_round1(doc, [two], {
            **{k: v for k, v in skeptic3(flags, doc).items()
               if k not in ("examined_gaps", "missed_gaps")}, "schema": ia.AUDIT_SCHEMAS[2]},
            layer=LAYER, rule=2, paragraphs=PARAGRAPHS)
        self.assertNotIn("gap_report", merged2)
        self.assertNotIn("gaps", merged2["counts"])


class TestTheKindRuleUnderRule3(unittest.TestCase):
    """A formal ``other`` passage outside a proof or an example section: the kind rule
    passes it under rule 2 and judges it under rule 3, whose action names the expository
    answer; ``should_be_expository`` settles it and ``kind_other_ok`` exempts it."""

    def setUp(self):
        self.doc = doc_of()
        mod2 = next(p for p in self.doc["passages"] if p["span"][0] == F.GAP_SPANS["MOD2"][0])
        mod2.update(passage_kind="other", section=["Computation"])
        self.item = mod2["item"]

    def merge(self, audit_rule: int, **row) -> dict:
        rule = audit_rule
        audit = audit3(self.doc, **({self.item: row} if row else {}))
        schema = ia.AUDIT_SCHEMAS[rule]
        if rule < 3:
            audit = {k: v for k, v in audit.items() if k != "gaps"}
        audit["schema"] = schema
        facts = gfacts(self.doc) if rule >= 3 else None
        flags = ia.strip(1, self.doc, audits=[audit], rule=rule, gaps=facts)
        skeptic = skeptic3(flags, self.doc, upheld=[f["flag_id"] for f in flags["flags"]],
                           evidence={self.item: "Then a widget of order"})
        if rule < 3:
            skeptic = {k: v for k, v in skeptic.items()
                       if k not in ("examined_gaps", "missed_gaps")}
        skeptic["schema"] = schema
        return ia.merge_round1(self.doc, [audit], skeptic, layer=LAYER, rule=rule,
                               paragraphs=PARAGRAPHS)

    def test_rule_2_passes_it_and_rule_3_judges_it(self):
        self.assertEqual([v["origin"] for v in self.merge(2)["verdicts"]], [])
        kinds = [v for v in self.merge(3)["verdicts"] if v["origin"] == "kind_rule"]
        self.assertEqual([(v["item"], v["action"]) for v in kinds],
                         [(self.item, ia.KIND_RULE_ACTION_3)])
        passage = next(p for p in self.doc["passages"] if p["item"] == self.item)
        self.assertTrue(ia.kind_rule_applies(passage, 3))
        self.assertFalse(ia.kind_rule_applies(passage, 2))
        self.assertIn("kind-rule", ir.hints(passage, LAYER, None, 3))
        self.assertNotIn("kind-rule", ir.hints(passage, LAYER, None, 2))

    def test_the_expository_answer_settles_it_and_kind_other_ok_exempts_it(self):
        expository = self.merge(3, verdict="should_be_expository", rule=6,
                                reason="commentary on a method", action="scope expository")
        self.assertEqual([(v["origin"], v["verdict"]) for v in expository["verdicts"]],
                         [("auditor", "should_be_expository")])
        exempt = self.merge(3, kind_other_ok=True, reason="a formal statement of no kind")
        self.assertEqual(exempt["verdicts"], [])
        self.assertEqual(exempt["counts"]["kind_other_ok"], 1)


class TestLaterRounds(unittest.TestCase):

    def setUp(self):
        self.r1 = doc_of()
        audit = audit3(self.r1, {"r1-g001": NOTATION_FLAG})
        flags = ia.strip(1, self.r1, audits=[audit], rule=3, gaps=gfacts(self.r1))
        merged = merge1(self.r1, audit, skeptic3(flags, self.r1, upheld=["r1-g001"],
                                                 evidence={"r1-g001": NOTATION_AT}))
        self.previous = verdicts1(self.r1, merged)
        (self.v,) = self.previous["verdicts"]

    def merge(self, doc: dict, status: str) -> dict:
        check = {"schema": SCHEMA3, "kind": "check", "round": 2, "source_id": SID,
                 "checked": [{"verdict_id": self.v["id"], "status": status,
                              "reason": "" if status == "applied" else "still a gap"}],
                 "collateral": [], "new": [], "regressions": []}
        skeptic = {"schema": SCHEMA3, "kind": "skeptic", "round": 2, "source_id": SID,
                   "verdicts": [], "missed": [], "disputes": [], "examined": [],
                   "per_spec_formal_estimate": 6, "summary": "", "examined_skips": [],
                   "missed_skips": [], "examined_gaps": [], "missed_gaps": []}
        return ia.merge_later(2, doc, [check], skeptic, self.previous, None,
                              {"collateral": []}, layer=LAYER, rule=3, paragraphs=PARAGRAPHS)

    def test_a_missing_repair_carries_the_verdict_onto_the_new_ids(self):
        merged = self.merge(doc_of(), "missing")
        (v,) = merged["verdicts"]
        self.assertEqual((v["origin"], v["of"], v["span"], v["gaps"], v["gap"]),
                         ("carried", "r1-v001", [979, 1048], ["r2-g001"], "r2-g001"))
        self.assertEqual(v["statements"], NOTATION_FLAG["statements"])
        self.assertEqual(merged["counts"]["gap_carried"], 1)
        self.assertEqual(merged["gap_report"]["gaps"], 1)

    def test_an_applied_verdict_goes_to_history(self):
        merged = self.merge(doc_of(notation=True), "applied")
        self.assertEqual(merged["verdicts"], [])
        self.assertEqual(merged["history"][0]["span"], [979, 1048])
        self.assertEqual(merged["gap_report"], {"gaps": 0, "words": 0, "whole": 0,
                                                "by_section": {}})


class TestTheDiff(unittest.TestCase):

    def test_the_gaps_line_and_a_gap_verdict_line(self):
        r1 = doc_of()
        audit = audit3(r1, {"r1-g001": NOTATION_FLAG})
        flags = ia.strip(1, r1, audits=[audit], rule=3, gaps=gfacts(r1))
        verdicts = verdicts1(r1, merge1(r1, audit, skeptic3(
            flags, r1, upheld=["r1-g001"], evidence={"r1-g001": NOTATION_AT})))
        lines = ir.gap_diff(r1, doc_of(notation=True), LAYER, PARAGRAPHS, before_round=1,
                            after_round=2, verdicts=verdicts)
        self.assertEqual(lines[0], "GAPS: 1 before (15 word(s)), 0 after (0 word(s))")
        self.assertEqual(lines[1], "GAP VERDICT r1-v001 on r1-g001 [979, 1048]: 15 word(s) held "
                                   "by no passage and no skip before, 0 now; 1 of its 1 "
                                   "statement(s) now start inside a passage.")


# --------------------------------------------------------------------------- #
# the repair, through the edit tool, the ids and the gate
# --------------------------------------------------------------------------- #

class TestTheRepairOnTheHost(GapCase):

    def test_the_notation_gap_repaired_by_an_add(self):
        self.assertEqual(self.gate().returncode, 0)
        doc = self.doc()
        mod1 = next(p["item"] for p in doc["passages"] if p["span"][0] == F.GAP_SPANS["MOD1"][0])
        base = self.fx.root / ".tend" / "audit" / "r1.document.json"
        base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.doc_path, base)
        ops = self.fx.root / ".tend" / "audit" / "r1.ops.json"
        ops.write_text(json.dumps({"schema": SCHEMA3, "kind": "ops", "round": 1,
                                   "source_id": SID, "ops": [
                                       {"op": "add", "after": mod1, "verdict": "r1-v001",
                                        "quote": {"exact": NOTATION_AT},
                                        "fields": {"statement": "N is the product of the "
                                                                "sprocket counts",
                                                   "passage_kind": "notation",
                                                   "scope": "formal_math",
                                                   "prominence": "supporting"}}]}),
                       encoding="utf-8")
        proc = self.tool("inventory_edit.py", "--document", self.doc_rel, "--ops",
                         ".tend/audit/r1.ops.json", "--base", ".tend/audit/r1.document.json",
                         "--rule", "3", "--write")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.rekey()
        gate = self.gate()
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        self.assertEqual(gfacts(self.doc()), [])
        notation = next(p for p in self.doc()["passages"] if p["passage_kind"] == "notation")
        self.assertEqual(LAYER[slice(*notation["span"])], F.GAP_NOTATION)


if __name__ == "__main__":
    unittest.main()
