"""The skip audit (audit rule 2): skips rendered, judged, repaired and counted.

New code, for references/locate-documents.md section 13.11 (``cf-inventory-audit/2``):
``scripts/gate/inventory_render.py --rule 2`` (the SKIP blocks in place, their ids in span
order, the header's Skips and ATTENTION lines, the index's ``skips`` and each part's,
ownership by the part that shows a skip, the 20 KB cap, ``--show <skip id>``, the diff's
SKIPS section and SKIP VERDICT lines), ``scripts/gate/inventory_audit.py --rule 2`` (every
skip judged once, the skip rows' rules, ``skip_ok``, the skeptic's skip hunt, strip, the
merge's evidence inside the skip, the skip rule and ``skip_seen``, a carried verdict mapped
to the next snapshot's ids, ``skip_edit`` collateral, ``decide``'s ``audit/2`` clause and
``skip_report``) and ``scripts/gate/inventory_edit.py --rule 2`` (``unskip``: drop, shrink,
split, and what it refuses), with the gate as the judge of a repair.  Rule 1 renders what
the base commit f523584 renders.  The fixtures are ``tests/_locate_fixtures.py``'s made-up
"Widget bounds" page: F1 a skipped proof, F2 legitimate skips, F3 a skipped display, F3b a
skip that starts inside a paragraph, F4 a hand edit of F1's skips.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
for path in (SCRIPTS, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import _locate_fixtures as F  # noqa: E402
import test_locate_gate_statement_cut as SC  # noqa: E402
import test_locate_inventory_audit as TL  # noqa: E402
import test_verify_locate as G  # noqa: E402
from cflib import vocab  # noqa: E402

ia, ie, ir = TL.ia, TL.ie, TL.ir
SID, TASK = TL.SID, TL.TASK
LAYER, PARAGRAPHS = F.SKIP_LAYER, F.SKIP_PARAGRAPHS
#: the base commit the skip audit's build branched from: rule 1 is its behaviour
BASE = "f523584"
SCHEMA2 = ia.AUDIT_SCHEMAS[2]


def doc_of(**kw) -> dict:
    """A skip document (F1 by default) with its header."""
    return {"schema": "cf-locate/1", "mode": "locate-inventory", "run_id": "r-1",
            "task_id": TASK, "source_id": SID, **F.skip_body(**kw)}


def facts(doc: dict, round_: int = 1) -> list[dict]:
    return ia.skip_facts(doc, PARAGRAPHS, round_)


def audit2(doc: dict, skips: dict | None = None, **passages) -> dict:
    """A rule-2 round-1 audit: every formal passage and every skip clean, but the rows
    given (``passages`` by item, ``skips`` by skip id)."""
    rows = []
    for item in TL.formal(doc):
        row = {"item": item, "origin": "statement_section", "verdict": "clean", "rule": 0,
               "merge_with": "", "reason": "", "action": ""}
        row.update(passages.get(item, {}))
        rows.append(row)
    skip_rows = []
    for fact in facts(doc):
        row = {"skip": fact["id"], "verdict": "clean", "rule": 0, "merge_with": "",
               "reason": "", "action": "", "statements": []}
        row.update((skips or {}).get(fact["id"], {}))
        skip_rows.append(row)
    return {"schema": SCHEMA2, "kind": "audit", "round": 1, "source_id": SID,
            "passages": rows, "expository_flags": [], "per_spec_formal_estimate": 8,
            "patterns": [], "subject_check": "one subject", "skips": skip_rows}


#: F1's proof skip as an auditor flags it: the two statements it hides
PROOF_FLAG = {"verdict": "skipped_statements", "rule": 6, "merge_with": "",
              "reason": "a proof skipped whole; a step asserts a fact",
              "action": "unskip it; add the statement; the narration becomes expository",
              "statements": [{"at": "Then every gadget of", "until": "e(W)\\leq n}",
                              "passage_kind": "theorem"}]}


def skeptic2(flags: dict, doc: dict, *, upheld=(), evidence: dict | None = None,
             examined_skips=None, missed_skips=(), round_: int = 1) -> dict:
    """A rule-2 skeptic: every flag ruled (``upheld`` by flag id, item or skip id), the
    passage hunt over every unflagged formal passage, the skip hunt over ``examined_skips``
    (default: every unflagged skip)."""
    verdicts = []
    for f in flags["flags"]:
        ok = f["flag_id"] in upheld or f.get("item") in upheld or f.get("skip") in upheld
        target = f.get("skip") or f["item"]
        verdicts.append({"flag_id": f["flag_id"], "upheld": ok, "verdict": f["verdict"],
                         "rule": f["rule"] if ok else 0,
                         "evidence": (evidence or {}).get(target, "") if ok else "",
                         "reason": "the spec says so"})
    flagged_items = {f["item"] for f in flags["flags"]}
    flagged_skips = {f.get("skip") for f in flags["flags"]}
    if examined_skips is None:
        examined_skips = [f["id"] for f in facts(doc, round_) if f["id"] not in flagged_skips]
    return {"schema": SCHEMA2, "kind": "skeptic", "round": round_, "source_id": SID,
            "verdicts": verdicts, "missed": [], "disputes": [],
            "examined": [i for i in TL.formal(doc) if i not in flagged_items],
            "per_spec_formal_estimate": 8, "summary": "fine",
            "examined_skips": list(examined_skips), "missed_skips": list(missed_skips)}


def merge1(doc: dict, audit: dict, skeptic: dict) -> dict:
    return ia.merge_round1(doc, [audit], skeptic, layer=LAYER, rule=2, paragraphs=PARAGRAPHS)


def verdicts1(doc: dict, merged: dict, review_rounds: int = 3) -> dict:
    return ia.verdicts_file(1, review_rounds, doc, "0" * 64, merged, None, rule=2)


class SkipCase(TL.HostCase):
    """The Widget-bounds layer in a staged gate directory, with F1 (or ``BODY``'s
    document) as the task's document."""

    LAYER = F.SKIP_LAYER
    FORMULAS = F.SKIP_FORMULAS
    PARAGRAPHS = F.SKIP_PARAGRAPHS
    BODY: dict = {}

    def setUp(self):
        super().setUp()
        self.fx.manifest(TASK, {"source_id": G.SID, "source_sha": G.HTML_SHA,
                                "layer_sha256": self.fx.layer_sha, "renderer": "web-html/1",
                                "finders": []})
        self.write_body(**self.BODY)

    def write_body(self, **kw) -> dict:
        doc = {**self.fx.header(TASK), **F.skip_body(**kw)}
        self.doc_path.write_text(json.dumps(doc), encoding="utf-8")
        return doc

    def render(self, *argv: str) -> subprocess.CompletedProcess:
        return self.tool("inventory_render.py", "--gate-config", self.gate_json, "--document",
                         self.doc_rel, *argv)

    def gate(self) -> subprocess.CompletedProcess:
        return self.tool("verify_locate.py", "--gate-config", self.gate_json, "--findings",
                         self.doc_rel)


# --------------------------------------------------------------------------- #
# the vocabulary and the fixtures
# --------------------------------------------------------------------------- #

class TestTheSkipVocabularyIsOneDefinition(unittest.TestCase):

    PAIRS = {"AUDIT_RULES": "INVENTORY_AUDIT_RULES", "AUDIT_SCHEMAS": "INVENTORY_AUDIT_SCHEMAS",
             "SKIP_VERDICT": "AUDIT_SKIP_VERDICT", "SKIP_VERDICTS": "AUDIT_SKIP_VERDICTS",
             "SKIP_ORIGINS": "AUDIT_SKIP_ORIGINS", "SKIP_OPS": "AUDIT_SKIP_OPS",
             "SKIP_COLLATERAL": "AUDIT_SKIP_COLLATERAL", "SKIP_HINTS": "AUDIT_SKIP_HINTS",
             "BACK_MATTER": "AUDIT_BACK_MATTER", "MOSTLY_SKIPPED_MIN": "AUDIT_MOSTLY_SKIPPED_MIN",
             "MOSTLY_SKIPPED_SHARE": "AUDIT_MOSTLY_SKIPPED_SHARE",
             "SKIP_STATEMENTS_MAX": "AUDIT_SKIP_STATEMENTS_MAX",
             "SKIP_AT_WORDS": "AUDIT_SKIP_AT_WORDS", "SKIP_UNTIL_WORDS": "AUDIT_SKIP_UNTIL_WORDS",
             "SKIP_RULE_RULE": "AUDIT_SKIP_RULE_RULE",
             "SKIP_RULE_ACTION": "AUDIT_SKIP_RULE_ACTION",
             "SKIP_BLOCK_BYTES": "AUDIT_SKIP_BLOCK_BYTES"}

    def test_the_host_copy_is_cflibs(self):
        for host, ours in self.PAIRS.items():
            with self.subTest(host=host):
                self.assertEqual(getattr(ia, host), getattr(vocab, ours))
        self.assertEqual(ia.DEFAULT_RULE, 1)
        self.assertEqual(ia.AUDIT_SCHEMA, vocab.INVENTORY_AUDIT_SCHEMA)
        self.assertIn(vocab.INVENTORY_AUDIT_RULE, vocab.INVENTORY_AUDIT_RULES)

    def test_rule_2_adds_and_rule_1_keeps(self):
        self.assertEqual(ia.edit_ops(1), vocab.AUDIT_EDIT_OPS)
        self.assertEqual(ia.edit_ops(2), vocab.AUDIT_EDIT_OPS + vocab.AUDIT_SKIP_OPS)
        self.assertEqual(set(ia.OP_KEYS_BY_RULE[2]), set(ia.edit_ops(2)))
        self.assertEqual(ia.OP_KEYS_BY_RULE[1], ia.OP_KEYS)
        self.assertIs(ia.RECORDS_BY_RULE[1], ia.RECORDS)
        self.assertNotIn("skips", ia.RECORDS["audit"][0])
        self.assertIn("skips", ia.RECORDS_BY_RULE[2]["audit"][0])
        self.assertNotIn(vocab.AUDIT_SKIP_VERDICT, vocab.AUDIT_VERDICTS)
        self.assertEqual(ia.collateral_changes(2)[-1], "skip_edit")
        self.assertEqual(ia.verdict_origins(2)[-1], "skip_rule")

    def test_every_value_is_documented(self):
        vocabulary = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")
        documents = (SKILL / "references" / "locate-documents.md").read_text(encoding="utf-8")
        values = ({vocab.AUDIT_SKIP_VERDICT, *vocab.AUDIT_SKIP_ORIGINS, *vocab.AUDIT_SKIP_OPS,
                   *vocab.AUDIT_SKIP_COLLATERAL, *vocab.AUDIT_SKIP_HINTS,
                   *vocab.INVENTORY_AUDIT_SCHEMAS.values()})
        for value in sorted(values):
            with self.subTest(value=value):
                self.assertIn(f"`{value}`", vocabulary)
                self.assertIn(f"`{value}`", documents)
        for name in ("INVENTORY_AUDIT_RULES", "INVENTORY_AUDIT_SCHEMAS", "AUDIT_SKIP_VERDICT",
                     "AUDIT_BACK_MATTER", "AUDIT_MOSTLY_SKIPPED_MIN", "AUDIT_SKIP_STATEMENTS_MAX",
                     "AUDIT_SKIP_BLOCK_BYTES", "AUDIT_SKIP_RULE_ACTION"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", vocabulary)


class TestTheFixturesAreGateClean(SkipCase):
    """F1-F3b pass the gate under the wide cut: what the audit judges is the cut, not form."""

    def test_each_fixture_passes(self):
        for kw in ({}, {"proof": False}, {"proof": False, "display": True},
                   {"proof": False, "remark": True}):
            with self.subTest(**kw):
                self.write_body(**kw)
                proc = self.gate()
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


# --------------------------------------------------------------------------- #
# the facts, the report and the soft threshold
# --------------------------------------------------------------------------- #

class TestTheFactsAndTheReport(unittest.TestCase):

    def test_f1_the_proof_skip_is_the_rules_and_the_furniture_is_not(self):
        got = {f["reason"]: f for f in facts(doc_of())}
        self.assertEqual([f["id"] for f in facts(doc_of())],
                         ["r1-s001", "r1-s002", "r1-s003", "r1-s004"])
        proof = got[F.PROOF_SKIP["reason"]]
        self.assertEqual((proof["math"], proof["unheld"], proof["unheld_outside"]), (4, 4, 4))
        self.assertTrue(proof["rule"] and proof["in_proof"] and proof["mostly"])
        self.assertEqual(proof["sections"], ["Proof"])
        self.assertFalse(proof["furniture"] or proof["partial"] or proof["inside"])
        side = got["sidebar of related formulas"]
        self.assertEqual((side["math"], side["unheld"], side["unheld_outside"]), (3, 3, 0))
        self.assertTrue(side["furniture"])
        self.assertFalse(side["rule"])
        for reason in ("infobox", "references"):
            self.assertTrue(got[reason]["furniture"])
            self.assertEqual(got[reason]["math"], 0)
        self.assertEqual(ia.skip_hints(proof), ["skip-rule", "in-proof", "mostly-skipped"])
        self.assertEqual(ia.skip_hints(side), ["furniture"])

    def test_the_report_counts_what_the_gate_counts(self):
        report = ia.skip_report(doc_of(), PARAGRAPHS)
        self.assertEqual(report["skips"], 4)
        self.assertEqual(report["math_paragraphs"], 7)
        self.assertEqual(report["furniture_math_paragraphs"], 3)
        self.assertEqual(ia.skipped_math_outside(report), 4)
        self.assertEqual(report["by_section"], {"(lead)": {"skipped": 3, "math": 3},
                                                "Proof": {"skipped": 4, "math": 4}})
        self.assertEqual(report["mostly_skipped"], ["Proof"])
        self.assertEqual(report["chars"], sum(b - a for a, b in
                                              (s["span"] for s in doc_of()["skips"])))
        two = ia.skip_report(doc_of(proof=False), PARAGRAPHS)
        self.assertEqual((two["math_paragraphs"], two["furniture_math_paragraphs"],
                          two["mostly_skipped"]), (3, 3, []))

    def test_the_soft_threshold(self):
        def counts(skipped, math, furniture=False):
            return {"S": {"skipped": skipped, "math": math, "furniture": furniture,
                          "path": ["S"], "first": 0}}
        # at least 2, and at least half (6 of 12 is mostly skipped, 2 of 5 is not)
        for (skipped, math, furniture), want in (((2, 3, False), ["S"]), ((2, 4, False), ["S"]),
                                                 ((2, 5, False), []), ((6, 12, False), ["S"]),
                                                 ((1, 1, False), []), ((1, 2, False), []),
                                                 ((3, 3, True), []), ((5, 9, False), ["S"])):
            with self.subTest(skipped=skipped, math=math, furniture=furniture):
                self.assertEqual(ia.mostly_skipped(counts(skipped, math, furniture)), want)

    def test_a_skip_of_no_block_is_furniture_in_the_lead_only(self):
        doc = doc_of(proof=False)
        # the title, ahead of the first paragraph (a short description or an infobox the
        # paragraphs omit), and the Proof heading, past the first block of a section
        title = [0, len(F.SKIP_TITLE)]
        heading = [F.SKIP_SPANS["H_PROOF"][0], F.SKIP_SPANS["H_PROOF"][1]]
        doc["skips"] = sorted(doc["skips"] + [{"span": title, "reason": "the title"},
                                              {"span": heading, "reason": "a heading"}],
                              key=lambda s: s["span"])
        got = {f["reason"]: f for f in facts(doc)}
        self.assertEqual((got["the title"]["blocks"], got["the title"]["furniture"]), ([], True))
        self.assertEqual(ia.skip_hints(got["the title"]), ["furniture"])
        self.assertEqual((got["a heading"]["blocks"], got["a heading"]["furniture"]),
                         ([], False))
        self.assertEqual(ia.skip_hints(got["a heading"]), [])
        text = ir.render(doc, LAYER, None, rule=2, paragraphs=PARAGRAPHS)
        block = text[text.index(f"SKIP {got['the title']['id']} "):]
        self.assertIn("0 block(s)", block.splitlines()[0])
        self.assertEqual(block.splitlines()[3], "  HINT: furniture")

    def test_partial_and_inside(self):
        f3b = {f["reason"]: f for f in facts(doc_of(proof=False, remark=True))}["a remark"]
        self.assertTrue(f3b["partial"])
        self.assertEqual((f3b["math"], f3b["rule"]), (0, False))
        doc = doc_of(proof=False)
        stmt = next(p for p in doc["passages"] if p["passage_kind"] == "theorem")
        a = stmt["span"][0]
        doc["skips"].append({"span": [a + 6, a + 19], "reason": "a nested finder"})
        inner = {f["reason"]: f for f in facts(doc)}["a nested finder"]
        self.assertTrue(inner["inside"])
        self.assertEqual(inner["unheld"], 0)


# --------------------------------------------------------------------------- #
# inventory_render.py --rule 2
# --------------------------------------------------------------------------- #

GOLDEN_F1 = """\
# enwiki-1001-2002: inventory, 5 passages (5 formal)

@EXPLAIN@
Worker notes: (none)
Skips: 4 (r1-s001 to r1-s004); 7 math paragraph(s) lie under them and no passage, 4 of them outside the lead and the back matter.
ATTENTION: mostly skipped sections: Proof 4 of 4

GAP: Widget bounds ⏎
[1] @LEAD@ · [14, 72] · formal_math · definition · subject · (lead)
  TEXT: A widget is a finite graph of sprockets joined by gadgets.
  STATEMENT: a synthetic restatement of the lead passage
SKIP r1-s001 · [73, 128] · 55 chars · 1 block(s), 0 math paragraph(s), 0 held by no passage · (lead)
  REASON: infobox
  ¶ [73, 128] p: Field Widget theory Introduced by a synthetic committee
  HINT: furniture
SKIP r1-s002 · [129, 233] · 104 chars · 3 block(s), 3 math paragraph(s), 3 held by no passage · (lead)
  REASON: sidebar of related formulas
  ¶ [129, 165] li · math · held by no passage: e(W)\\geq 0{\\displaystyle e(W)\\geq 0}
  ¶ [166, 196] li · math · held by no passage: c(W)=2n{\\displaystyle c(W)=2n}
  ¶ [197, 233] li · math · held by no passage: g(W)\\leq n{\\displaystyle g(W)\\leq n}
  HINT: furniture
GAP:  ⏎ Statement ⏎
[2] @STMT@ · [244, 348] · formal_math · theorem · central · Statement
  TEXT: Every finite widget W{\\displaystyle W} of order n{\\displaystyle n} has at most n{\\displaystyle n} edges.
  STATEMENT: a synthetic restatement of the stmt passage
GAP:  ⏎ Proof ⏎
SKIP r1-s003 · [355, 621] · 266 chars · 4 block(s), 4 math paragraph(s), 4 held by no passage · Proof
  REASON: proof narration; it argues the theorem above
  ¶ [355, 431] p · math · held by no passage: Suppose W{\\displaystyle W} is a finite widget with n{\\displaystyle n} nodes.
  ¶ [432, 512] p · math · held by no passage: Then every gadget of W{\\displaystyle W} has at most n{\\displaystyle n} edges, so
  ¶ [513, 549] dd · math · held by no passage: e(W)\\leq n{\\displaystyle e(W)\\leq n}
  ¶ [550, 621] p · math · held by no passage: This completes the proof, since n{\\displaystyle n} bounds every gadget.
  HINT: skip-rule in-proof mostly-skipped
GAP:  ⏎ Gadget bound ⏎
[3] @GB1@ · [635, 728] · formal_math · definition · supporting · Gadget bound
  TEXT: The gadget bound of a connected widget W{\\displaystyle W} is ⏎ g(W)=n-1{\\displaystyle g(W)=n-1}
  STATEMENT: a synthetic restatement of the gb1 passage
[4] @GB3A@ · [729, 783] · formal_math · definition · supporting · Gadget bound
  TEXT: A widget whose gadgets are all bounded is called tame,
  STATEMENT: a synthetic restatement of the gb3 passage
GAP:  and
[5] @GB3B@ · [788, 816] · formal_math · theorem · supporting · Gadget bound
  TEXT: tame widgets have no cycles.
  STATEMENT: a synthetic restatement of the gb3 passage
GAP:  ⏎ References ⏎
SKIP r1-s004 · [828, 944] · 116 chars · 3 block(s), 0 math paragraph(s), 0 held by no passage · References
  REASON: references
  … [828, 944] 3 list items, no math: A. Author, Widgets, a synthetic book. … C. Author, Sprockets, a synthetic note.
  HINT: furniture
"""


def golden(text: str, doc: dict) -> str:
    names = ["LEAD", "STMT", "GB1", "GB3A", "GB3B"]
    items = [p["item"] for p in ia.ordered(doc) if p["scope"] == "formal_math"]
    for name, item in zip(names, items):
        text = text.replace(f"@{name}@", item)
    return text.replace("@EXPLAIN@", "\n".join(ir.EXPLAIN + ir.EXPLAIN_SKIPS))


def _base_tools(case: unittest.TestCase) -> Path | None:
    """The base commit's inventory_render.py and inventory_audit.py beside this checkout's
    gate (``git show``), or None when this checkout does not hold the commit."""
    out = Path(tempfile.mkdtemp(prefix="cf-base-gate-")).resolve()
    case.addCleanup(shutil.rmtree, out, ignore_errors=True)
    for name in ("inventory_render.py", "inventory_audit.py", "inventory_edit.py"):
        got = subprocess.run(["git", "-C", str(SKILL), "show", f"{BASE}:scripts/gate/{name}"],
                             capture_output=True, text=True)
        if got.returncode != 0:
            return None
        (out / name).write_text(got.stdout, encoding="utf-8")
    return out


class TestTheRendering(SkipCase):

    def test_the_golden_rule_2_rendering_of_f1(self):
        proc = self.render("--rule", "2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, golden(GOLDEN_F1, self.doc()))

    def test_rule_1_is_the_base_commits_on_every_fixture(self):
        base = _base_tools(self)
        if base is None:
            self.skipTest(f"this checkout does not hold commit {BASE}")
        for name in ("inventory_render.py", "inventory_audit.py", "inventory_edit.py"):
            shutil.copy2(base / name, self.gate_dir / f"base_{name}")
        # the base tools import their siblings by their plain names: a directory of their own
        old = self.gate_dir.parent / "gate-base"
        shutil.copytree(self.gate_dir, old)
        for name in ("inventory_render.py", "inventory_audit.py", "inventory_edit.py"):
            shutil.copy2(base / name, old / name)
        for kw in ({}, {"proof": False}, {"proof": False, "display": True},
                   {"proof": False, "remark": True}):
            with self.subTest(**kw):
                self.write_body(**kw)
                now = self.render()
                then = subprocess.run([sys.executable, "-I", str(old / "inventory_render.py"),
                                       "--gate-config", self.gate_json, "--document",
                                       self.doc_rel], capture_output=True, text=True,
                                      cwd=str(self.fx.root),
                                      env={"PATH": "/usr/bin:/bin", "HOME": str(self.fx.root)})
                self.assertEqual(then.returncode, 0, then.stderr)
                self.assertEqual(now.stdout, then.stdout)
                self.assertEqual(self.render("--rule", "1").stdout, then.stdout)
                self.assertIn("SKIPS: [", now.stdout)
                self.assertNotIn("SKIP r1-s", now.stdout)

    def test_the_index_names_each_skip_and_the_part_that_shows_it(self):
        proc = self.render("--rule", "2", "--out-dir", ".tend/audit", "--label", "r1",
                           "--part-bytes", "1500")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        index = json.loads((self.fx.root / ".tend" / "audit" / "r1.render.json").read_text())
        self.assertEqual((index["schema"], index["rule"]), (SCHEMA2, 2))
        self.assertEqual([r["id"] for r in index["skips"]],
                         ["r1-s001", "r1-s002", "r1-s003", "r1-s004"])
        # a skip just before a part's first passage is that part's: it shows there
        owner = {r["id"]: r["part"] for r in index["skips"]}
        for part in index["parts"]:
            text = (self.fx.root / ".tend" / "audit" / part["file"]).read_text()
            for sid in part["skips"]:
                self.assertIn(f"SKIP {sid} ", text)
            for sid, n in owner.items():
                if part["file"].endswith(f"part{n}.md"):
                    self.assertIn(sid, part["skips"])
                else:
                    self.assertNotIn(f"SKIP {sid} ", text)
        self.assertEqual(sorted(s for p in index["parts"] for s in p["skips"]), sorted(owner))
        proof = next(r for r in index["skips"] if r["id"] == "r1-s003")
        self.assertEqual({k: proof[k] for k in ("blocks", "math", "unheld", "unheld_outside",
                                                "furniture", "rule", "sections", "inside")},
                         {"blocks": 4, "math": 4, "unheld": 4, "unheld_outside": 4,
                          "furniture": False, "rule": True, "sections": ["Proof"],
                          "inside": False})
        # every part carries the header's skip lines
        for part in index["parts"]:
            text = (self.fx.root / ".tend" / "audit" / part["file"]).read_text()
            self.assertIn("Skips: 4 (r1-s001 to r1-s004)", text)
            self.assertIn("ATTENTION: mostly skipped sections: Proof 4 of 4", text)

    def test_the_skips_after_the_last_passage_count_toward_its_part(self):
        # the whole end of the page skipped after the statement: the tail's SKIP block
        rows = [r for r in F.skip_rows() if r["section"] != ["Gadget bound"]]
        doc = placed(rows, [{"span": [F.SKIP_SPANS["GB1"][0], F.SKIP_SPANS["REF3"][1]],
                             "reason": "the rest of the page"}])
        tail = ir.entries2(doc, LAYER, None, facts(doc))[1]
        head = ir.header(doc, ir.skip_summary(doc, facts(doc), PARAGRAPHS))
        self.assertTrue(any(line.startswith("SKIP r1-s") for line in tail))
        for slack in (200, 400, 600, 900, 1200, 1600, 2400):
            budget = ir.nbytes(head) + slack
            with self.subTest(budget=budget):
                cut = ir.parts(doc, LAYER, None, budget, rule=2, paragraphs=PARAGRAPHS)
                for part in cut:
                    if len(part["items"]) < 2:
                        continue        # one passage over the budget is a part of its own
                    lines = part["text"].split("\n")
                    body = [x for x in lines if not x.startswith("Part ")]
                    self.assertLessEqual(ir.nbytes(body) - 2, budget, part["items"])
                self.assertIn("the rest of the page", cut[-1]["text"])
                self.assertEqual(cut[-1]["skips"][-1], facts(doc)[-1]["id"])
        # rule 1's parts are the base commit's: its tail is one capped line, not budgeted
        one = ir.parts(doc, LAYER, None, ir.nbytes(ir.header(doc)) + 600)
        self.assertIn("SKIPS: [", one[-1]["text"])

    def test_the_legend_only_when_the_document_skips(self):
        none = doc_of(proof=False, furniture=False)
        text = ir.render(none, LAYER, None, rule=2, paragraphs=PARAGRAPHS)
        self.assertIn("\nSkips: none.\n", text)
        for line in ir.EXPLAIN_SKIPS:
            self.assertNotIn(line, text)
            self.assertIn(line, ir.render(doc_of(), LAYER, None, rule=2, paragraphs=PARAGRAPHS))

    def test_a_math_block_of_the_back_matter_is_clipped_as_prose(self):
        layer = "x" * 100 + "".join(chr(ord("a") + i % 26) for i in range(1000))

        def lines(section):
            fact = {"id": "r1-s001", "span": [100, 1100], "reason": "the end",
                    "blocks": [{"span": [100, 1100], "block": "li", "math": True,
                                "held": False, "section": section}],
                    "math": 1, "unheld": 1, "sections": [" > ".join(section)], "rule": False,
                    "in_proof": False, "in_example": False, "mostly": False,
                    "partial": False, "furniture": ia.is_furniture(section)}
            return ir.skip_lines(fact, layer)
        whole = next(x for x in lines(["Proof"]) if x.startswith("  ¶"))
        self.assertIn(layer[100:1100], whole)
        for section in (["References"], ["See also"]):
            clipped = next(x for x in lines(section) if x.startswith("  ¶"))
            self.assertIn(layer[100:300] + " … " + layer[900:1100], clipped)
        lead = next(x for x in lines([]) if x.startswith("  ¶"))
        self.assertIn(layer[100:1100], lead)            # the lead's math stays whole

    def test_attention_only_on_a_mostly_skipped_section(self):
        self.write_body(proof=False)
        text = self.render("--rule", "2").stdout
        self.assertNotIn("ATTENTION", text)
        self.assertIn("Skips: 3 (r1-s001 to r1-s003); 3 math paragraph(s)", text)
        self.assertNotIn("skip-rule", text.split("Worker notes")[1])

    def test_partial_and_a_skip_inside_a_kept_passage(self):
        doc = self.write_body(proof=False, remark=True)
        text = self.render("--rule", "2").stdout
        block = text[text.index("REASON: a remark") - 120:]
        self.assertIn("· 1 block(s), 0 math paragraph(s), 0 held by no passage · Gadget bound",
                      block)
        self.assertIn("¶ [788, 816] (block [729, 816]) p: tame widgets have no cycles.", block)
        self.assertIn("HINT: partial", block)
        stmt = next(p for p in doc["passages"] if p["passage_kind"] == "theorem")
        doc["skips"].append({"span": [stmt["span"][0] + 6, stmt["span"][0] + 19],
                             "reason": "a nested finder"})
        doc["skips"].sort(key=lambda s: s["span"])
        self.doc_path.write_text(json.dumps(doc), encoding="utf-8")
        text = self.render("--rule", "2").stdout
        self.assertIn("SKIPS inside kept passages: r1-s003 [250, 263] a nested finder", text)
        self.assertNotIn("SKIP r1-s003 ·", text)

    def test_show_a_skip_whole_and_rule_1_refuses_its_id(self):
        proc = self.render("--rule", "2", "--show", "r1-s003", "--context", "20")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertTrue(lines[0].startswith("SKIP r1-s003 · [355, 621]"))
        self.assertEqual(lines[1], "  BEFORE: " + ir.flat(LAYER[335:355]))
        self.assertEqual(lines[-3], "  AFTER: " + ir.flat(LAYER[621:641]))
        self.assertEqual(lines[-2], "  HINT: skip-rule in-proof mostly-skipped")
        self.assertEqual(self.render("--rule", "2", "--show", "r1-s009").returncode, 2)
        refused = self.render("--show", "r1-s003")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("is no passage of the document", refused.stderr)

    def test_a_block_past_the_cap_shows_the_rest_clipped(self):
        doc = self.doc()
        fact = next(f for f in facts(doc) if f["id"] == "r1-s003")
        with mock.patch.object(ia, "SKIP_BLOCK_BYTES", 200):
            lines = ir.skip_lines(fact, LAYER)
            whole = ir.skip_lines(fact, LAYER, cap=False)
        self.assertEqual(lines[-2], "  … the rest in full: --show r1-s003")
        self.assertNotIn("the rest in full", "\n".join(whole))
        self.assertEqual(len(whole), len(lines) - 1)

    def test_a_rule_that_is_not_known_is_refused(self):
        proc = self.render("--rule", "3")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not an audit rule", proc.stderr)


class TestTheDiff(SkipCase):
    """Round 2's diff under rule 2: the skips that changed and each skip verdict's counts."""

    def test_the_skips_section_and_a_skip_verdict_line(self):
        audit_dir = self.fx.root / ".tend" / "audit"
        audit_dir.mkdir(parents=True)
        before = self.doc()
        (audit_dir / "r1.document.json").write_text(json.dumps(before), encoding="utf-8")
        verdicts = {"verdicts": [{"id": "r1-v002", "item": "", "skip": "r1-s003",
                                  "skips": ["r1-s003"], "span": F.PROOF_SKIP["span"],
                                  "verdict": "skipped_statements",
                                  "statements": PROOF_FLAG["statements"]}]}
        (audit_dir / "r1.verdicts.json").write_text(json.dumps(verdicts), encoding="utf-8")
        self.write_body(proof=False)
        proc = self.render("--rule", "2", "--out-dir", ".tend/audit", "--label", "r2",
                           "--previous", ".tend/audit/r1.document.json",
                           "--previous-verdicts", ".tend/audit/r1.verdicts.json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = (audit_dir / "r2.diff.md").read_text()
        self.assertIn("SKIPS: 4 before, 3 after", text)
        self.assertIn("REMOVED SKIP r1-s003: [355, 621] · proof narration", text)
        self.assertIn("SKIP VERDICT r1-v002 on r1-s003 [355, 621]: 4 math paragraph(s) unheld "
                      "before, 0 now; 1 of its 1 statement(s) now start inside a passage; the "
                      "skip is gone.", text)
        self.assertEqual(text.count("ADDED In-text #"), 3)
        # rule 1's diff is the base commit's: no skip lines
        self.render("--out-dir", ".tend/audit", "--label", "r2",
                    "--previous", ".tend/audit/r1.document.json")
        self.assertNotIn("SKIP", (audit_dir / "r2.diff.md").read_text())
        refused = self.render("--out-dir", ".tend/audit", "--label", "r2", "--previous",
                              ".tend/audit/r1.document.json", "--previous-verdicts",
                              ".tend/audit/r1.verdicts.json")
        self.assertEqual(refused.returncode, 2)


# --------------------------------------------------------------------------- #
# validation under rule 2
# --------------------------------------------------------------------------- #

class TestTheSkipRows(unittest.TestCase):

    def setUp(self):
        self.doc = doc_of()
        self.facts = facts(self.doc)

    def problems(self, data, **kw) -> list[str]:
        return ia.validate_audit(data, self.doc, rule=2, skips=self.facts, **kw)

    def assertInvalid(self, problems, needle):
        self.assertTrue(any(needle in p for p in problems), (needle, problems))

    def test_a_sound_audit(self):
        self.assertEqual(self.problems(audit2(self.doc)), [])
        self.assertEqual(self.problems(audit2(self.doc, {"r1-s003": PROOF_FLAG})), [])
        merge = {"verdict": "skipped_statements", "rule": 3, "reason": "a display",
                 "action": "set until", "merge_with": TL.formal(self.doc)[2], "statements": []}
        self.assertEqual(self.problems(audit2(self.doc, {"r1-s003": merge})), [])

    def test_every_skip_once_and_its_rules(self):
        data = audit2(self.doc)
        data["skips"].append(dict(data["skips"][0]))
        self.assertInvalid(self.problems(data), "skip r1-s001 is judged 2 times")
        data = audit2(self.doc)
        del data["skips"][1]
        self.assertInvalid(self.problems(data), "1 skip(s) not judged: r1-s002")
        data = audit2(self.doc)
        data["skips"][0]["skip"] = "r1-s009"
        self.assertInvalid(self.problems(data), "r1-s009 is no skip of the document")
        bad = [({"verdict": "over_cut"}, "not one of clean, skipped_statements"),
               ({"verdict": "clean", "rule": 6}, "a clean skip gives rule 0"),
               ({"verdict": "clean", "statements": PROOF_FLAG["statements"]},
                "a clean skip lists no statements"),
               ({**PROOF_FLAG, "rule": 0}, "names no rule"),
               ({**PROOF_FLAG, "statements": []}, "lists no statement and names no merge_with"),
               ({**PROOF_FLAG, "merge_with": "In-text #00000000"}, "is no passage"),
               ({**PROOF_FLAG, "statements": [{"at": "Then every", "until": "",
                                               "passage_kind": "theorem"}]},
                "has 2 words, not 3 to 12"),
               ({**PROOF_FLAG, "statements": [{"at": " ".join(["word"] * 13), "until": "",
                                               "passage_kind": "theorem"}]},
                "more than 12"),
               ({**PROOF_FLAG, "statements": [{"at": "Then every gadget", "until": "",
                                               "passage_kind": "lemma"}]},
                "passage_kind is 'lemma'"),
               ({**PROOF_FLAG, "statements": [dict(PROOF_FLAG["statements"][0])] * 41},
                "41 statements, more than 40"),
               ({"skip_ok": True}, "skip_ok needs a reason"),
               ({**PROOF_FLAG, "skip_ok": True}, "skip_ok goes on a clean row only")]
        for row, needle in bad:
            with self.subTest(needle=needle):
                self.assertInvalid(self.problems(audit2(self.doc, {"r1-s003": row})), needle)
        # skip_ok is the skip rule's exemption: only on a skip the rule judges
        ok = {"skip_ok": True, "reason": "a sidebar the page repeats"}
        self.assertInvalid(self.problems(audit2(self.doc, {"r1-s002": ok})),
                           "which the skip rule does not judge")
        self.assertEqual(self.problems(audit2(self.doc, {"r1-s003": ok})), [])

    def test_the_schema_is_the_rules(self):
        data = audit2(self.doc)
        self.assertInvalid(ia.validate_audit(data, self.doc), "not 'cf-inventory-audit/1'")
        data["schema"] = ia.AUDIT_SCHEMA
        self.assertInvalid(ia.validate_audit(data, self.doc),
                           "has the key 'skips', which audit does not take")
        del data["skips"]
        self.assertEqual(ia.validate_audit(data, self.doc), [])
        data["schema"] = SCHEMA2
        self.assertInvalid(self.problems(data), "has no 'skips'")

    def test_a_part_judges_the_skips_it_shows(self):
        data = audit2(self.doc)
        data["skips"] = [r for r in data["skips"] if r["skip"] in ("r1-s003",)]
        self.assertEqual(self.problems(data, expect=TL.formal(self.doc),
                                       expect_skips=["r1-s003"]), [])
        self.assertInvalid(self.problems(data, expect=TL.formal(self.doc),
                                         expect_skips=["r1-s003", "r1-s004"]),
                           "not judged: r1-s004")

    def test_the_skeptics_skip_hunt(self):
        audit = audit2(self.doc, {"r1-s003": PROOF_FLAG})
        flags = ia.strip(1, self.doc, audits=[audit], rule=2)
        good = skeptic2(flags, self.doc, upheld=["r1-s003"],
                        evidence={"r1-s003": "has at most"})
        check = lambda data, **kw: ia.validate_skeptic(data, self.doc, flags, round_=1, rule=2,
                                                       skips=self.facts, **kw)
        self.assertEqual(check(good), [])
        wrong = copy.deepcopy(good)
        flag = next(v for v in wrong["verdicts"] if v["flag_id"] == flags["flags"][0]["flag_id"])
        flag["verdict"] = "under_cut"
        self.assertInvalid(check(wrong), "a skip flag is upheld as skipped_statements")
        # every unflagged skip over math no passage holds outside the furniture: here none
        self.assertEqual(check(dict(good, examined_skips=[])), [])
        bare = audit2(self.doc)
        flags = ia.strip(1, self.doc, audits=[bare], rule=2)
        hunt = skeptic2(flags, self.doc, examined_skips=["r1-s001"])
        self.assertInvalid(ia.validate_skeptic(hunt, self.doc, flags, round_=1, rule=2,
                                               skips=self.facts),
                           "leaves out 1 unflagged skip(s) over math no passage holds: r1-s003")
        missed = dict(PROOF_FLAG, skip="r1-s003", evidence="")
        hunt = skeptic2(flags, self.doc, missed_skips=[missed])
        self.assertInvalid(ia.validate_skeptic(hunt, self.doc, flags, round_=1, rule=2,
                                               skips=self.facts), "gives no evidence")
        self.assertInvalid(ia.validate_skeptic(dict(hunt, examined_skips=["r1-s042"]), self.doc,
                                               flags, round_=1, rule=2, skips=self.facts),
                           "r1-s042 is no skip of the document")

    def test_a_later_skeptic_without_facts_names_the_rounds_skips(self):
        """With neither an index nor the source, a round-k skeptic file is held to the
        skips' ids in round k's numbering, as the rendering it read spells them."""
        flags = {"schema": SCHEMA2, "kind": "flags", "round": 2, "source_id": SID,
                 "flags": [], "disputes": []}
        data = skeptic2(flags, self.doc, examined_skips=["r2-s003"], round_=2)
        self.assertEqual(ia.validate_skeptic(data, self.doc, flags, round_=2, rule=2), [])
        self.assertInvalid(ia.validate_skeptic(dict(data, examined_skips=["r1-s003"]), self.doc,
                                               flags, round_=2, rule=2),
                           "r1-s003 is no skip of the document")
        missed = dict(PROOF_FLAG, skip="r2-s003", evidence="has at most n")
        self.assertEqual(ia.validate_skeptic(dict(data, examined_skips=[],
                                                  missed_skips=[missed]),
                                             self.doc, flags, round_=2, rule=2), [])


class TestTheValidatorReadsTheSkipsText(SkipCase):
    """``validate --gate-config`` (the line an auditor or a skeptic is given) holds each skip
    statement's ``at`` and each skip evidence to the skip's own text, which the merge drops
    or refutes silently; without the layer the file is valid, as the turn accepts it."""

    def validate(self, kind: str, data: dict, *extra: str) -> subprocess.CompletedProcess:
        path = self.fx.root / ".tend" / "audit" / f"r1.{kind}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return self.tool("inventory_audit.py", "validate", "--kind", kind, "--file",
                         str(path.relative_to(self.fx.root)), "--document", self.doc_rel,
                         "--rule", "2", *extra)

    def test_a_statement_anchored_before_its_skip(self):
        doc = self.doc()
        # the statement's own words, above the proof skip: on the layer, not in the skip
        outside = dict(PROOF_FLAG, statements=[{"at": "has at most n", "until": "",
                                                "passage_kind": "theorem"},
                                               {"at": "Every finite widget W", "until": "",
                                                "passage_kind": "theorem"}])
        data = audit2(doc, {"r1-s003": outside})
        self.assertEqual(self.validate("audit", data).returncode, 0)
        proc = self.validate("audit", data, "--gate-config", self.gate_json)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("INVALID: audit.skips[2].statements[1].at 'Every finite widget W' is not "
                      "in the text of r1-s003 [355, 621]", proc.stdout)
        self.assertNotIn("statements[0]", proc.stdout)          # "has at most n" is inside
        good = self.validate("audit", audit2(doc, {"r1-s003": PROOF_FLAG}), "--gate-config",
                             self.gate_json)
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)

    def test_skip_evidence_from_outside_the_skip(self):
        doc = self.doc()
        audit = audit2(doc, {"r1-s003": PROOF_FLAG})
        flags = ia.strip(1, doc, audits=[audit], rule=2)
        flags_path = self.fx.root / ".tend" / "audit" / "r1.flags.json"
        flags_path.parent.mkdir(parents=True, exist_ok=True)
        flags_path.write_text(json.dumps(flags), encoding="utf-8")
        rel = str(flags_path.relative_to(self.fx.root))
        missed = dict(PROOF_FLAG, skip="r1-s003", evidence="of order n")
        for evidence, ok in (("has at most n", True), ("of order n", False)):
            with self.subTest(evidence=evidence):
                data = skeptic2(flags, doc, upheld=["r1-s003"], evidence={"r1-s003": evidence})
                self.assertEqual(self.validate("skeptic", data, "--flags", rel).returncode, 0)
                proc = self.validate("skeptic", data, "--flags", rel, "--gate-config",
                                     self.gate_json)
                self.assertEqual(proc.returncode, 0 if ok else 1, proc.stdout + proc.stderr)
                if not ok:
                    self.assertIn("skeptic.verdicts[0].evidence is not in the text of r1-s003",
                                  proc.stdout)
        bare = audit2(doc)
        flags = ia.strip(1, doc, audits=[bare], rule=2)
        flags_path.write_text(json.dumps(flags), encoding="utf-8")
        data = skeptic2(flags, doc, examined_skips=["r1-s003"], missed_skips=[missed])
        proc = self.validate("skeptic", data, "--flags", rel, "--gate-config", self.gate_json)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("skeptic.missed_skips[0].evidence is not in the text of r1-s003",
                      proc.stdout)


# --------------------------------------------------------------------------- #
# strip and merge under rule 2
# --------------------------------------------------------------------------- #

class TestStripAndMerge(unittest.TestCase):

    def setUp(self):
        self.doc = doc_of()

    def test_strip_withholds_the_statements_and_the_action(self):
        audit = audit2(self.doc, {"r1-s003": PROOF_FLAG},
                       **{TL.formal(self.doc)[3]: {"verdict": "wrong_prominence", "rule": 9,
                                                   "reason": "too prominent",
                                                   "action": "set supporting"}})
        flags = ia.strip(1, self.doc, audits=[audit], rule=2)
        self.assertEqual(flags["schema"], SCHEMA2)
        self.assertEqual([(f["flag_id"], f.get("skip"), f["item"]) for f in flags["flags"]],
                         [("r1-f001", "r1-s003", ""),
                          ("r1-f002", None, TL.formal(self.doc)[3])])
        self.assertEqual(flags["flags"][0], {"flag_id": "r1-f001", "item": "", "skip": "r1-s003",
                                             "span": F.PROOF_SKIP["span"],
                                             "verdict": "skipped_statements", "rule": 6,
                                             "merge_with": "", "reason": PROOF_FLAG["reason"]})
        text = json.dumps(flags)
        for withheld in ("Then every gadget", PROOF_FLAG["action"]):
            self.assertNotIn(withheld, text)
        self.assertNotIn("statements", flags["flags"][0])

    def test_an_upheld_skip_flag_with_evidence_inside_the_skip(self):
        audit = audit2(self.doc, {"r1-s003": dict(PROOF_FLAG, statements=[
            *PROOF_FLAG["statements"],
            {"at": "words that are nowhere", "until": "", "passage_kind": "theorem"}])})
        flags = ia.strip(1, self.doc, audits=[audit], rule=2)
        merged = merge1(self.doc, audit, skeptic2(flags, self.doc, upheld=["r1-s003"],
                                                  evidence={"r1-s003": "has at most n"}))
        (v,) = merged["verdicts"]
        self.assertEqual((v["id"], v["item"], v["skip"], v["skips"], v["span"], v["origin"],
                          v["verdict"], v["unheld"]),
                         ("r1-v001", "", "r1-s003", ["r1-s003"], F.PROOF_SKIP["span"], "auditor",
                          "skipped_statements", 4))
        self.assertEqual(v["statements"], PROOF_FLAG["statements"])
        counts = merged["counts"]
        self.assertEqual({k: counts[k] for k in ("skips", "skips_judged", "skips_flagged",
                                                 "skips_upheld", "skips_refuted", "skip_rule",
                                                 "hidden_statements", "statements_dropped",
                                                 "flagged")},
                         {"skips": 4, "skips_judged": 4, "skips_flagged": 1, "skips_upheld": 1,
                          "skips_refuted": 0, "skip_rule": 0, "hidden_statements": 1,
                          "statements_dropped": 1, "flagged": 0})
        self.assertEqual(merged["skip_report"]["mostly_skipped"], ["Proof"])
        out = verdicts1(self.doc, merged)
        self.assertEqual((out["schema"], out["decision_rule"], out["decision"]),
                         (SCHEMA2, "audit/2", "request_changes"))

    def test_evidence_outside_the_skip_refutes_and_the_skip_rule_still_raises_it(self):
        audit = audit2(self.doc, {"r1-s003": PROOF_FLAG})
        flags = ia.strip(1, self.doc, audits=[audit], rule=2)
        # on the layer, but in the statement above the skip: not the skip's own text
        merged = merge1(self.doc, audit, skeptic2(flags, self.doc, upheld=["r1-s003"],
                                                  evidence={"r1-s003": "of order n"}))
        self.assertEqual(merged["refuted"][0]["why"], "no_evidence")
        self.assertEqual([(v["origin"], v["statements"], v["action"]) for v in merged["verdicts"]],
                         [("skip_rule", [], ia.SKIP_RULE_ACTION)])

    def test_the_skip_rule_fires_on_f1_not_on_f2_and_not_with_skip_ok(self):
        audit = audit2(self.doc)
        merged = merge1(self.doc, audit, skeptic2(ia.strip(1, self.doc, audits=[audit], rule=2),
                                                  self.doc))
        self.assertEqual([(v["skip"], v["origin"], v["rule"]) for v in merged["verdicts"]],
                         [("r1-s003", "skip_rule", 6)])
        self.assertEqual(merged["counts"]["skip_rule"], 1)
        ok = audit2(self.doc, {"r1-s003": {"skip_ok": True, "reason": "a synthetic exemption"}})
        merged = merge1(self.doc, ok, skeptic2(ia.strip(1, self.doc, audits=[ok], rule=2),
                                               self.doc))
        self.assertEqual(merged["verdicts"], [])
        self.assertEqual(merged["skip_ok"], [{"skip": "r1-s003", "span": F.PROOF_SKIP["span"],
                                              "reason": "a synthetic exemption"}])
        self.assertEqual(merged["skip_seen"], [F.PROOF_SKIP["span"]])
        self.assertEqual(verdicts1(self.doc, merged)["decision"], "approve")
        f2 = doc_of(proof=False)
        audit = audit2(f2)
        merged = merge1(f2, audit, skeptic2(ia.strip(1, f2, audits=[audit], rule=2), f2))
        self.assertEqual(merged["verdicts"], [])

    def test_a_missed_skip_needs_evidence_inside_it(self):
        f3b = doc_of(proof=False, remark=True)
        audit = audit2(f3b)
        flags = ia.strip(1, f3b, audits=[audit], rule=2)
        row = {"skip": "r1-s003", "verdict": "skipped_statements", "rule": 1,
               "merge_with": "", "reason": "a remark asserts a fact",
               "action": "unskip; add the statement",
               "statements": [{"at": "tame widgets have", "until": "no cycles.",
                               "passage_kind": "theorem"}]}
        for evidence, added in (("tame widgets have no cycles", 1), ("called tame", 0)):
            with self.subTest(evidence=evidence):
                merged = merge1(f3b, audit, skeptic2(flags, f3b, missed_skips=[
                    dict(row, evidence=evidence)]))
                self.assertEqual(merged["counts"]["skip_missed_added"], added)
                self.assertEqual([v["origin"] for v in merged["verdicts"]],
                                 ["skeptic_missed"] * added)

    def test_the_decision_table_audit_2(self):
        rounds = [{"round": 1, "actionable": 3, "harm": False},
                  {"round": 2, "actionable": 2, "harm": False}]
        decide = lambda **kw: ia.decide(2, 3, kw.pop("actionable", 2), kw.pop("before", 3),
                                        False, 100, rounds, rule=kw.pop("rule", 2), **kw)[0]
        # below T=5: audit/1 approves; audit/2 rejects for a skip verdict when it progressed
        self.assertEqual(decide(rule=1, skip_actionable=1), "approve")
        self.assertEqual(decide(skip_actionable=1), "request_changes")
        self.assertEqual(decide(skip_actionable=0), "approve")
        self.assertEqual(decide(skip_actionable=1, actionable=3), "approve")
        self.assertEqual(decide(skip_actionable=1, actionable=3, skip_math=1,
                                previous_skip_math=4), "request_changes")
        self.assertEqual(decide(skip_actionable=1, actionable=3, skip_math=4,
                                previous_skip_math=4), "approve")
        # the final round never rejects
        self.assertEqual(ia.decide(3, 3, 1, 3, False, 100, rounds, rule=2, skip_actionable=1,
                                   skip_math=0, previous_skip_math=4)[0], "approve")


def placed(rows: list[dict], skips: list[dict]) -> dict:
    """A skip document of ``rows`` and ``skips`` (the furniture's added), placed by
    ``passage_ids`` under span-end rule 3."""
    from locate import passage_ids
    doc = {**doc_of(), "passages": rows,
           "skips": sorted([dict(s) for s in F.FURNITURE_SKIPS] + skips,
                           key=lambda s: s["span"])}
    problems = passage_ids.place(doc, LAYER, F.SKIP_FORMULAS, PARAGRAPHS,
                                 rule=vocab.SPAN_END_CONTINUED)
    assert not problems, problems
    return doc


#: two skips that share the proof's second paragraph, and a skip nested in the proof's
OVERLAP_A = {"span": F.skip_span("PF1", "PF2"), "reason": "proof narration A"}
OVERLAP_B = {"span": F.skip_span("PF2", "PF4"), "reason": "proof narration B"}
NESTED = {"span": F.skip_span("PF3"), "reason": "the proof's display"}


class TestOverlappingAndNestedSkips(unittest.TestCase):
    """The gate admits overlapping and nested skips; each skip of a round is judged, and
    raised by the skip rule, as itself: two skips are two verdicts, each naming its own skip
    once, so the repair unskips each once and no shared paragraph settles the other."""

    def setUp(self):
        self.overlap = placed(F.skip_rows(), [OVERLAP_A, OVERLAP_B])
        self.nested = placed(F.skip_rows(), [dict(F.PROOF_SKIP), NESTED])

    def ids(self, doc: dict) -> dict[str, str]:
        return {f["reason"]: f["id"] for f in facts(doc)}

    def all_clean(self, doc: dict) -> dict:
        audit = audit2(doc)
        return merge1(doc, audit, skeptic2(ia.strip(1, doc, audits=[audit], rule=2), doc))

    def test_the_skip_rule_names_each_skip_alone(self):
        ids = self.ids(self.overlap)
        a, b = ids["proof narration A"], ids["proof narration B"]
        merged = self.all_clean(self.overlap)
        self.assertEqual([(v["skip"], v["skips"], v["origin"], v["unheld"])
                          for v in merged["verdicts"]],
                         [(a, [a], "skip_rule", 2), (b, [b], "skip_rule", 3)])
        self.assertEqual(merged["counts"]["skip_rule"], 2)
        ids = self.ids(self.nested)
        whole, display = ids[F.PROOF_SKIP["reason"]], ids["the proof's display"]
        merged = self.all_clean(self.nested)
        self.assertEqual([(v["skip"], v["skips"], v["span"]) for v in merged["verdicts"]],
                         [(whole, [whole], F.PROOF_SKIP["span"]),
                          (display, [display], NESTED["span"])])

    def test_an_upheld_flag_settles_its_own_skip_not_the_one_it_overlaps(self):
        ids = self.ids(self.overlap)
        a, b = ids["proof narration A"], ids["proof narration B"]
        audit = audit2(self.overlap, {b: PROOF_FLAG})
        flags = ia.strip(1, self.overlap, audits=[audit], rule=2)
        merged = merge1(self.overlap, audit, skeptic2(flags, self.overlap, upheld=[b],
                                                      evidence={b: "has at most n"}))
        self.assertEqual([(v["skip"], v["skips"], v["origin"]) for v in merged["verdicts"]],
                         [(a, [a], "skip_rule"), (b, [b], "auditor")])
        self.assertEqual((merged["counts"]["skips_upheld"], merged["counts"]["skip_rule"]),
                         (1, 1))
        # a missed skip of the skeptic's names its skip alone too
        audit = audit2(self.overlap, {a: {"skip_ok": True, "reason": "a synthetic exemption"},
                                      b: {"skip_ok": True, "reason": "a synthetic exemption"}})
        flags = ia.strip(1, self.overlap, audits=[audit], rule=2)
        missed = dict(PROOF_FLAG, skip=b, evidence="has at most n")
        merged = merge1(self.overlap, audit, skeptic2(flags, self.overlap,
                                                      missed_skips=[missed]))
        self.assertEqual([(v["skip"], v["skips"], v["origin"]) for v in merged["verdicts"]],
                         [(b, [b], "skeptic_missed")])

    def test_the_repair_unskips_each_named_skip_once(self):
        for doc in (self.overlap, self.nested):
            verdicts = self.all_clean(doc)["verdicts"]
            ops = [{"op": "unskip", "skip": sid, "verdict": v["id"]}
                   for v in verdicts for sid in v["skips"]]
            out, _log, problems = ie.apply(doc, ops, verdict_ids={v["id"] for v in verdicts},
                                           rule=2, round_=1)
            self.assertEqual(problems, [])
            self.assertEqual(len(out["skips"]), len(F.FURNITURE_SKIPS))

    def test_a_carried_verdict_names_the_snapshots_skips_its_span_overlaps(self):
        r1 = doc_of()
        audit = audit2(r1, {"r1-s003": PROOF_FLAG})
        flags = ia.strip(1, r1, audits=[audit], rule=2)
        previous = verdicts1(r1, merge1(r1, audit, skeptic2(
            flags, r1, upheld=["r1-s003"], evidence={"r1-s003": "has at most n"})))
        (v,) = previous["verdicts"]
        # the worker split the proof skip by hand into the two overlapping ones
        check = {"schema": SCHEMA2, "kind": "check", "round": 2, "source_id": SID,
                 "checked": [{"verdict_id": v["id"], "status": "partial",
                              "reason": "still skipped"}],
                 "collateral": [], "new": [], "regressions": []}
        skeptic = {"schema": SCHEMA2, "kind": "skeptic", "round": 2, "source_id": SID,
                   "verdicts": [], "missed": [], "disputes": [], "examined": [],
                   "per_spec_formal_estimate": 8, "summary": "", "examined_skips": [],
                   "missed_skips": []}
        merged = ia.merge_later(2, self.overlap, [check], skeptic, previous, None,
                                {"collateral": []}, layer=LAYER, rule=2,
                                paragraphs=PARAGRAPHS)
        ids = self.ids(self.overlap)
        both = sorted([ids["proof narration A"], ids["proof narration B"]])
        both = [i.replace("r1-", "r2-") for i in both]
        self.assertEqual([(x["origin"], x["skip"], x["skips"]) for x in merged["verdicts"]],
                         [("carried", "", both)])
        self.assertEqual(merged["counts"]["skip_rule"], 0)          # both settled by the carry


class TestLaterRounds(unittest.TestCase):
    """Round 2 under rule 2: a carried skip verdict keeps its span and names the new
    snapshot's skips there; one set aside joins ``skip_seen``; the skip rule spares what
    is settled."""

    def setUp(self):
        self.r1 = doc_of()
        audit = audit2(self.r1, {"r1-s003": PROOF_FLAG})
        flags = ia.strip(1, self.r1, audits=[audit], rule=2)
        merged = merge1(self.r1, audit, skeptic2(flags, self.r1, upheld=["r1-s003"],
                                                 evidence={"r1-s003": "has at most n"}))
        self.previous = verdicts1(self.r1, merged)
        (self.v,) = self.previous["verdicts"]

    def check(self, status: str) -> dict:
        return {"schema": SCHEMA2, "kind": "check", "round": 2, "source_id": SID,
                "checked": [{"verdict_id": self.v["id"], "status": status,
                             "reason": "" if status == "applied" else "still skipped"}],
                "collateral": [], "new": [], "regressions": []}

    def merge(self, doc: dict, status: str, **kw) -> dict:
        skeptic = {"schema": SCHEMA2, "kind": "skeptic", "round": 2, "source_id": SID,
                   "verdicts": [], "missed": [], "disputes": [], "examined": [],
                   "per_spec_formal_estimate": 8, "summary": "", "examined_skips": [],
                   "missed_skips": []}
        return ia.merge_later(2, doc, [self.check(status)], skeptic, self.previous, None,
                              {"collateral": []}, layer=LAYER, rule=2, paragraphs=PARAGRAPHS,
                              **kw)

    def test_a_partial_repair_carries_the_verdict_onto_the_new_ids(self):
        doc = doc_of()
        # the worker shrank the proof skip to its last paragraph and added nothing
        proof = next(s for s in doc["skips"] if s["reason"] == F.PROOF_SKIP["reason"])
        proof["span"] = F.skip_span("PF2", "PF4")
        merged = self.merge(doc, "partial")
        (v,) = merged["verdicts"]
        self.assertEqual((v["origin"], v["of"], v["span"], v["skips"], v["skip"]),
                         ("carried", "r1-v001", F.PROOF_SKIP["span"], ["r2-s003"], "r2-s003"))
        self.assertEqual(v["statements"], PROOF_FLAG["statements"])
        self.assertEqual(merged["counts"]["skip_carried"], 1)
        self.assertEqual(merged["counts"]["skip_rule"], 0)          # settled by the carry

    def test_an_applied_verdict_goes_to_history_and_a_remnant_is_raised_again(self):
        doc = doc_of()
        proof = next(s for s in doc["skips"] if s["reason"] == F.PROOF_SKIP["reason"])
        proof["span"] = F.skip_span("PF4")
        merged = self.merge(doc, "applied")
        self.assertEqual(merged["history"][0]["span"], F.PROOF_SKIP["span"])
        self.assertEqual([(v["origin"], v["skip"]) for v in merged["verdicts"]],
                         [("skip_rule", "r2-s003")])

    def test_a_verdict_set_aside_is_seen_and_spares_the_skip(self):
        merged = self.merge(doc_of(), "gate_refused_ok")
        self.assertEqual(merged["verdicts"], [])
        self.assertEqual(merged["skip_seen"], [F.PROOF_SKIP["span"]])
        self.assertEqual(merged["set_aside"][0]["span"], F.PROOF_SKIP["span"])


# --------------------------------------------------------------------------- #
# inventory_edit.py --rule 2: unskip
# --------------------------------------------------------------------------- #

class TestUnskip(unittest.TestCase):

    def setUp(self):
        self.doc = doc_of()

    def apply(self, *ops, round_=1):
        return ie.apply(self.doc, [dict(o, verdict="r1-v001") for o in ops], rule=2,
                        round_=round_)

    def spans(self, doc):
        return [(s["span"], s["reason"]) for s in doc["skips"]]

    def test_drop_shrink_and_split(self):
        a, b = F.PROOF_SKIP["span"]
        out, log, problems = self.apply({"op": "unskip", "skip": "r1-s003"})
        self.assertEqual(problems, [])
        self.assertNotIn((F.PROOF_SKIP["span"], F.PROOF_SKIP["reason"]), self.spans(out))
        self.assertEqual(len(out["skips"]), 3)
        self.assertIn("r1-s003 [355, 621] dropped", log[0])
        out, _log, problems = self.apply({"op": "unskip", "skip": "r1-s003",
                                          "keep": [{"span": [550, 621]}]})
        self.assertEqual(problems, [])
        self.assertIn(([550, 621], F.PROOF_SKIP["reason"]), self.spans(out))
        out, _log, problems = self.apply({"op": "unskip", "skip": "r1-s003",
                                          "keep": [{"span": [a, 431], "reason": "a context"},
                                                   {"span": [550, b]}]})
        self.assertEqual(problems, [])
        self.assertIn(([a, 431], "a context"), self.spans(out))
        self.assertIn(([550, b], F.PROOF_SKIP["reason"]), self.spans(out))
        self.assertEqual(len(out["skips"]), 5)
        self.assertEqual(self.doc["skips"], doc_of()["skips"])      # the base is untouched

    def test_what_it_refuses(self):
        a, b = F.PROOF_SKIP["span"]
        cases = [({"op": "unskip", "skip": "r1-s009"}, "is no skip of the base document"),
                 ({"op": "unskip", "skip": "r2-s003"}, "is round 2's numbering"),
                 ({"op": "unskip", "skip": "r1-s003", "keep": [{"span": [a - 5, a + 10]}]},
                  "is not inside the skip"),
                 ({"op": "unskip", "skip": "r1-s003", "keep": [{"span": [a, 500]},
                                                               {"span": [450, b]}]},
                  "overlaps or comes before"),
                 ({"op": "unskip", "skip": "r1-s003", "keep": [{"span": [500, 500]}]},
                  "start < end"),
                 ({"op": "unskip", "skip": "r1-s003", "keep": "In-text #00000000"},
                  "keep is a list of pieces"),
                 ({"op": "unskip", "skip": "r1-s003", "keep": [{"span": [a, b], "x": 1}]},
                  "is not a piece")]
        for op, needle in cases:
            with self.subTest(needle=needle):
                _out, _log, problems = self.apply(op)
                self.assertTrue(any(needle in p for p in problems), problems)
        _out, _log, problems = self.apply({"op": "unskip", "skip": "r1-s003"},
                                          {"op": "unskip", "skip": "r1-s003"})
        self.assertTrue(any("unskipped twice" in p for p in problems), problems)
        _out, _log, problems = ie.apply(self.doc, [{"op": "unskip", "skip": "r1-s003"}], rule=2)
        self.assertTrue(any("cites exactly one verdict" in p for p in problems), problems)
        # rule 1 knows no unskip
        _out, _log, problems = ie.apply(self.doc, [{"op": "unskip", "skip": "r1-s003",
                                                    "verdict": "r1-v001"}])
        self.assertTrue(any("'unskip', not one of merge" in p for p in problems), problems)


class TestTheRepairOnTheHost(SkipCase):
    """F1 and F3 repaired as a revision repairs them: the edit tool's ops, the id tool and
    the gate, whose rule 8 and coverage floor judge the result."""

    def ops(self, rows: list) -> str:
        path = self.fx.root / ".tend" / "audit" / "r1.ops.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": SCHEMA2, "kind": "ops", "round": 1,
                                    "source_id": SID, "ops": rows}), encoding="utf-8")
        return str(path.relative_to(self.fx.root))

    def edit(self, rows: list, *extra: str) -> subprocess.CompletedProcess:
        base = self.fx.root / ".tend" / "audit" / "r1.document.json"
        if not base.is_file():
            base.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.doc_path, base)
        return self.tool("inventory_edit.py", "--document", self.doc_rel, "--ops",
                         self.ops(rows), "--base", ".tend/audit/r1.document.json", "--rule", "2",
                         "--write", *extra)

    @staticmethod
    def add(after: str | None, name: str, kind: str, scope: str) -> dict:
        p = F.skip_passage(name, kind, scope)
        return {"op": "add", "after": after, "quote": p["quote"], "verdict": "r1-v001",
                "fields": {k: p[k] for k in ("statement", "passage_kind", "scope",
                                             "prominence", "section")}}

    def f1_ops(self, *, unskip: bool = True, narration: bool = True) -> list:
        stmt = TL.formal(self.doc())[1]
        rows = [{"op": "unskip", "skip": "r1-s003", "verdict": "r1-v001"}] if unskip else []
        rows.append(self.add(stmt, "PF1", "other", "expository"))
        rows.append(self.add(stmt, "PF2", "theorem", "formal_math"))
        if narration:
            rows.append(self.add(stmt, "PF4", "other", "expository"))
        return rows

    def rekey_and_gate(self) -> subprocess.CompletedProcess:
        self.rekey()
        return self.gate()

    def test_f1_unskip_and_the_adds_pass_the_gate(self):
        proc = self.edit(self.f1_ops())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        gate = self.rekey_and_gate()
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        doc = self.doc()
        self.assertEqual(len(doc["skips"]), 3)
        self.assertEqual(ia.skip_report(doc, PARAGRAPHS)["mostly_skipped"], [])
        self.assertEqual(ia.skipped_math_outside(ia.skip_report(doc, PARAGRAPHS)), 0)

    def test_the_gate_refuses_a_forgotten_unskip_and_a_narration_left_uncovered(self):
        self.edit(self.f1_ops(unskip=False))
        gate = self.rekey_and_gate()
        self.assertEqual(gate.returncode, 1)
        self.assertIn("a skip drops text no passage keeps", gate.stdout + gate.stderr)
        self.write_body()
        (self.fx.root / ".tend" / "audit" / "r1.document.json").unlink()
        self.edit(self.f1_ops(narration=False))
        gate = self.rekey_and_gate()
        self.assertEqual(gate.returncode, 1)
        self.assertIn("the coverage floor", gate.stdout + gate.stderr)

    def test_f3_unskip_and_a_set_of_until(self):
        doc = self.write_body(proof=False, display=True)
        gb1 = next(p["item"] for p in doc["passages"] if p.get("until"))
        rows = [{"op": "unskip", "skip": "r1-s003", "verdict": "r1-v001"},
                {"op": "set", "item": gb1, "fields": {"until": None}, "verdict": "r1-v001"}]
        proc = self.edit(rows)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        gate = self.rekey_and_gate()
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        held = next(p for p in self.doc()["passages"] if p["span"][0] == F.SKIP_SPANS["GB1"][0])
        self.assertEqual(held["span"][1], F.SKIP_SPANS["GB2"][1])

    def test_rule_1_refuses_unskip_and_writes_nothing(self):
        before = self.doc_path.read_bytes()
        base = self.fx.root / ".tend" / "audit" / "r1.document.json"
        base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.doc_path, base)
        proc = self.tool("inventory_edit.py", "--document", self.doc_rel, "--ops",
                         self.ops(self.f1_ops()), "--base", ".tend/audit/r1.document.json",
                         "--write")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.doc_path.read_bytes(), before)


class TestTheSkipEditCollateral(unittest.TestCase):
    """F4: a revision that changes skips with no operation is collateral ``skip_edit``."""

    def setUp(self):
        self.snapshot = doc_of()

    def collateral(self, doc: dict, ops: list | None = None) -> list[dict]:
        ops_doc = None if ops is None else {"schema": SCHEMA2, "kind": "ops", "round": 1,
                                            "source_id": SID, "ops": ops}
        verdicts = {"verdicts": [{"id": "r1-v001"}]}
        out = ia.collateral(2, self.snapshot, doc, ops_doc, verdicts, rule=2)
        self.assertEqual(out["schema"], SCHEMA2)
        return [e for e in out["collateral"] if e["change"] == "skip_edit"]

    def test_a_cited_unskip_is_no_collateral(self):
        doc = copy.deepcopy(self.snapshot)
        doc["skips"] = [s for s in doc["skips"] if s["reason"] != F.PROOF_SKIP["reason"]]
        self.assertEqual(self.collateral(doc, [{"op": "unskip", "skip": "r1-s003",
                                               "verdict": "r1-v001"}]), [])

    def test_removed_added_and_widened_by_hand(self):
        doc = copy.deepcopy(self.snapshot)
        doc["skips"] = [s for s in doc["skips"] if s["reason"] != F.PROOF_SKIP["reason"]]
        (entry,) = self.collateral(doc)
        self.assertEqual((entry["id"], entry["skip"], entry["before"], entry["after"]),
                         ("r2-x001", "r1-s003", {"span": F.PROOF_SKIP["span"],
                                                 "reason": F.PROOF_SKIP["reason"]}, None))
        doc = copy.deepcopy(self.snapshot)
        doc["skips"].append({"span": F.skip_span("GB2"), "reason": "a display"})
        (entry,) = self.collateral(doc)
        self.assertEqual((entry["before"], entry["after"]["span"]), (None, F.skip_span("GB2")))
        doc = copy.deepcopy(self.snapshot)
        infobox = next(s for s in doc["skips"] if s["reason"] == "infobox")
        infobox["span"] = [infobox["span"][0] - 3, infobox["span"][1]]
        (entry,) = self.collateral(doc)
        self.assertEqual((entry["skip"], entry["before"]["span"], entry["after"]["span"]),
                         ("r1-s001", F.skip_span("INFOBOX"), infobox["span"]))
        # rule 1 compares passages only, as it always did
        self.assertEqual(ia.collateral(2, self.snapshot, doc, None, None)["collateral"], [])

    def test_a_harmful_skip_edit_reverts_by_the_next_bases_id(self):
        """The entry's ``skip`` is the reviewed snapshot's id (round 1's); the ``revert``
        verdict names the skip as the next revision's base numbers it (round 2's), with the
        ``unskip`` that puts it back: drop one added, shrink one widened, none for a skip
        removed by hand."""
        audit = audit2(self.snapshot)
        previous = verdicts1(self.snapshot, merge1(self.snapshot, audit, skeptic2(
            ia.strip(1, self.snapshot, audits=[audit], rule=2), self.snapshot)))
        (v,) = previous["verdicts"]
        doc = copy.deepcopy(self.snapshot)
        doc["skips"].append({"span": F.skip_span("GB2"), "reason": "a display"})
        infobox = next(s for s in doc["skips"] if s["reason"] == "infobox")
        infobox["span"] = [infobox["span"][0] - 3, infobox["span"][1]]
        doc["skips"] = [s for s in doc["skips"] if s["reason"] != "references"]
        coll = ia.collateral(2, self.snapshot, doc, None, previous, rule=2)
        entries = [e for e in coll["collateral"] if e["change"] == "skip_edit"]
        self.assertEqual(len(entries), 3)
        check = {"schema": SCHEMA2, "kind": "check", "round": 2, "source_id": SID,
                 "checked": [{"verdict_id": v["id"], "status": "missing", "reason": "untouched"}],
                 "collateral": [{"id": e["id"], "harmful": True, "reason": "hides text"}
                                for e in entries], "new": [], "regressions": []}
        skeptic = {"schema": SCHEMA2, "kind": "skeptic", "round": 2, "source_id": SID,
                   "verdicts": [], "missed": [], "disputes": [], "examined": [],
                   "per_spec_formal_estimate": 8, "summary": "", "examined_skips": [],
                   "missed_skips": []}
        merged = ia.merge_later(2, doc, [check], skeptic, previous, None, coll, layer=LAYER,
                                rule=2, paragraphs=PARAGRAPHS)
        now = {f["reason"]: f for f in facts(doc, 2)}
        actions = sorted(x["action"] for x in merged["verdicts"] if x["verdict"] == "revert")
        added, widened = now["a display"], now["infobox"]
        self.assertEqual(len(actions), 3)
        self.assertTrue(any(a.endswith(
            f"; the skip is {added['id']} [{added['span'][0]}, {added['span'][1]}] in round "
            f"2's numbering (unskip {added['id']})") for a in actions), actions)
        before = F.skip_span("INFOBOX")
        self.assertTrue(any(a.endswith(
            f"(unskip {widened['id']} keeping [{before[0]}, {before[1]}])") for a in actions),
            actions)
        # the references skip removed by hand: no id to name, no operation to put it back
        self.assertEqual(sum("numbering" not in a for a in actions), 1, actions)
        self.assertTrue(all(x["id"].startswith("r2-") for x in facts(doc, 2)))


if __name__ == "__main__":
    unittest.main()
