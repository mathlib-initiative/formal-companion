"""Locate field fixes outside the sources stage: L2 terms (C1) and shared finder starts (I1).

New code: regression tests for defects found running the locate stages against
real data, each reduced to a synthetic page (``Widget lemma``; no article text).
docs/proposals/wikipedia-locate.md section 3 (L2, L3a) and
references/locate-documents.md sections 2, 5 and 7 are what they pin:

* C1 -- L2's lexical retrieval queried every bold term, labels and symbols
  included ("R", "1'", "Example:", "Proof.");
* I1 -- the lead and a bold definition often share a start, the inventory prompt
  asked for one passage per finder, the ids tool refuses two passages with one
  head, and the gate counts a finder as kept by its start alone.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from locate import candidates  # noqa: E402

import test_verify_locate as TV  # noqa: E402


class _View:
    def __init__(self, finders):
        self._finders = finders

    def finders(self):
        return self._finders


class TestC1LexicalTermsAreTerms(unittest.TestCase):

    def test_c1_labels_symbols_and_stop_words_are_no_query(self):
        for term in ("R", "M", "1'", "2'", "Example:", "Proof.", "Definition:", "Case 2:",
                     "if and only if", "the", "e"):
            with self.subTest(term=term):
                self.assertFalse(candidates.is_lexical_term(term))
        for term in ("widget group", "spanning gadget", "Gadget", "Cayley table"):
            with self.subTest(term=term):
                self.assertTrue(candidates.is_lexical_term(term))

    def test_c1_the_title_stays_and_only_terms_are_queried(self):
        view = _View([{"finder": "bold_definition", "term": t} for t in
                      ("R", "1'", "Example:", "Proof.", "widget group", "if and only if")]
                     + [{"finder": "theorem_box", "term": "Theorem"}])
        terms = candidates.source_terms({"title": "Pi"}, view)
        self.assertEqual(terms, [("Pi", "title"), ("widget group", "bold_term")])


class TestI1FindersThatShareAStart(TV.GateTestCase):

    def setUp(self):
        self.fx = TV.Fixture(self, "locate-inventory")
        doc = TV.inventory_manifest_doc(self.fx)
        doc["finders"].insert(0, {"finder": "bold_definition", "start": TV.LEAD_SPAN[0],
                                  "span": list(TV.LEAD_SPAN), "section": [],
                                  "term": "widget lemma"})
        self.fx.manifest(TV.TASK, doc)

    def test_i1_one_passage_at_a_shared_start_keeps_every_finder_there(self):
        for finder in ("lead", "bold_definition"):
            body = TV.inventory_body()
            body["passages"][0]["finder"] = finder
            with self.subTest(finder=finder):
                self.assertOk(self.fx.run(self.fx.document(TV.TASK, body)))

    def test_i1_dropping_the_shared_start_still_needs_a_skip(self):
        body = TV.inventory_body()
        del body["passages"][0]
        self.assertRule(self.fx.run(self.fx.document(TV.TASK, copy.deepcopy(body))), 8,
                        "bold_definition passage")

    def test_i1_the_inventory_prompt_says_so(self):
        text = (SKILL / "templates" / "prompts" / "locate-inventory.md.tmpl").read_text(
            encoding="utf-8")
        self.assertIn("Finders that share a start are one passage", text)
        self.assertIn("first of them in the manifest", text)


if __name__ == "__main__":
    unittest.main()
