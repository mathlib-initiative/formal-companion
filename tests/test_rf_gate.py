"""Review-fix regressions for the findings gate (stdlib unittest).

One test per defect the code review found in ``scripts/gate/``:

* the operator pass was an independent window search, so a single flipped ``<=``,
  a DELETED operator and an unmapped glyph all passed as *verbatim*;
* nothing bound a quote to the item or the page it is supposed to evidence, so a
  section running head passed as an item's passage;
* the quote gate had a floor and no ceiling, against SKILL.md hard rule 6;
* defect rows under a ``findings`` key were never validated but were counted by
  ``harvest.tally`` and loaded by the ledger;
* the changed-files check could not see a deletion or a rename;
* the gate ran bare ``git`` inside the worktree it judges, so a worker could hand
  it a program to execute through ``.git/config``;
* 'the gate is broken' and 'your findings are rejected' shared exit code 1.

The synthetic book below is a public-domain paraphrase: no source text lives in
this repo (CLAUDE.md S1).  Nothing here needs the private baselines.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))

from gate import verify_findings as vf  # noqa: E402
from plan import presets as presets_mod  # noqa: E402

SOURCE_ID = "a-book-2nd-edition"
SOURCE_SHA = "b" * 64
TMPDIR = os.environ.get("CF_TMPDIR") or None

#: Seven pages, a running head repeated on every one of them, and two sentences
#: whose operators are the point.  ``\f`` is what pdftotext writes between pages,
#: and the running head is the shape that defeated the gate: >=25 normalised
#: characters, on every page of the section.
RUNNING_HEAD = "2.3. Outer measures, pre-measures, and product measures"
PAGE_1 = (
    "Exercise 2.3.4 (Monotonicity). Let E and F be elementary sets. "
    "Show that if E is contained in F then m(E) <= m(F), and that every "
    "open set is measurable.\n"
)
PAGE_2 = (
    "Lemma 2.3.5 (Countable additivity). Let the sets be disjoint. Then the "
    "measure of their union is the sum of their measures, and x ∈ E holds "
    "for every point of the union.\n"
)
PAGE_7 = (
    "Exercise 2.9.9 (A long way from the rest). Show that a bounded set of "
    "finite outer measure need not be measurable in the Jordan sense.\n"
)
#: pages 3 to 6 carry ordinary prose, so the running head recurs seven times
_FILLER = [
    "We now turn to the elementary properties of the construction, and record "
    "the consequences of the previous paragraph for later use in section %d.\n" % n
    for n in range(3, 7)
]
PAGES = [PAGE_1, PAGE_2, *_FILLER, PAGE_7]
BOOK = "".join(f"{RUNNING_HEAD}\n\n{body}\f" for body in PAGES)

QUOTE_1_PAGE = 1
QUOTE_2_PAGE = 2

QUOTE_1 = ("Show that if E is contained in F then m(E) <= m(F), and that every "
           "open set is measurable")
QUOTE_2 = ("the measure of their union is the sum of their measures, and "
           "x ∈ E holds for every point of the union")


def gate_cfg(preset_name: str, book: pathlib.Path, **over) -> vf.GateConfig:
    """A GateConfig for a preset with ``book`` as its single source's text."""
    data = presets_mod.gate_config(
        presets_mod.get(preset_name),
        run_id=over.pop("run_id", "test-run"),
        sources={SOURCE_ID: {"text": str(book), "sha256": SOURCE_SHA,
                             **over.pop("source", {})}},
        repos=over.pop("repos", ["ours"]),
    )
    data.update(over)
    return vf.GateConfig(data)


def doc_for(quote: str = QUOTE_1, *, item: str = "Exercise 2.3.4", **over) -> dict:
    doc = {
        "schema_version": 2,
        "run_id": "test-run",
        "source_id": SOURCE_ID,
        "source_sha": SOURCE_SHA,
        "item": item,
        "book_located": True,
        "book_passage_quote": quote,
        "matches": [],
        "disagreements": [
            {
                "concept": "monotonicity",
                "kind": "CONTENT",
                "repos_involved": ["ours"],
                "faithful": [],
                "unfaithful_decls": ["ours:mono"],
                "book_quote": quote,
                "witness": {"object": "E = [0,1), F = [0,2)", "why": "the bound is reversed"},
                "reasoning": "the companion states the reverse inequality",
                "severity": "high",
            }
        ],
        "coverage_notes": "",
    }
    doc.update(over)
    return doc


class BookTestCase(unittest.TestCase):
    """A written-out source text, so the gate loads it exactly as it does a run's."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMPDIR)
        self.root = pathlib.Path(self.tmp.name)
        self.book_path = self.root / "text.txt"
        self.book_path.write_text(BOOK, encoding="utf-8")
        self.cfg = gate_cfg("attack-vac", self.book_path)
        self.books, errs = vf.load_sources(self.cfg)
        self.assertEqual(errs, [])
        self.book = self.books[SOURCE_ID]

    def tearDown(self):
        self.tmp.cleanup()

    def errs(self, doc, cfg=None):
        return vf.validate_doc(doc, "x.json", cfg or self.cfg, self.books)


# --------------------------------------------------------------------------- #
# gates-correctness#1 — the operator pass
# --------------------------------------------------------------------------- #
class TestOperatorPassIsAnchored(BookTestCase):
    """A doctored operator must not be certified, whatever shape the doctoring is.

    Before the fix all three of these returned ``(True, 'verbatim')``: the
    operator check was an independent ``_window_match`` over the operator stream,
    so any six operator-free tokens satisfied it; a deleted operator skipped the
    check altogether; and the negated glyphs were not in the operator table.
    """

    def test_the_real_quote_still_passes_verbatim(self):
        ok, why = vf.quote_ok(self.book, QUOTE_1)
        self.assertTrue(ok, why)
        self.assertEqual(why, "verbatim")
        self.assertEqual(self.errs(doc_for()), [])

    def test_a_single_flipped_inequality_in_a_long_quote_is_refused(self):
        ok, why = vf.quote_ok(self.book, QUOTE_1.replace("<=", ">="))
        self.assertFalse(ok, "a flipped <= was certified")
        self.assertIn("OPERATOR", why)
        self.assertIn("CHANGED", why)
        self.assertIn("≤", why)  # the book's operator, in the error

    def test_a_deleted_operator_is_refused(self):
        ok, why = vf.quote_ok(self.book, QUOTE_1.replace("<=", ""))
        self.assertFalse(ok, "a deleted <= was certified")
        self.assertIn("DROPPED", why)

    def test_an_unmapped_negated_glyph_is_refused(self):
        ok, why = vf.quote_ok(self.book, QUOTE_2.replace("∈", "∉"))
        self.assertFalse(ok, "∈ -> ∉ was certified")
        self.assertIn("OPERATOR", why)

    def test_a_typographic_variant_of_the_same_operator_still_matches(self):
        """``⩽`` and ``≤`` are one relation: folding them is not a loophole."""
        ok, why = vf.quote_ok(self.book, QUOTE_1.replace("<=", "⩽"))
        self.assertTrue(ok, why)

    def test_the_flip_is_refused_inside_a_partial_match_too(self):
        """The window tolerance must not become an escape from the operator pass.

        A genuine page with one operator doctored and an invented tail matched a
        60% word window and the old operator search then found 50% of the
        operator stream elsewhere, so the gate passed it with a note about the
        tail and nothing about the flipped ``∈``.
        """
        doctored = PAGE_2.strip().replace("∈", "∉") + (
            " and therefore every bounded subset of the plane admits a unique "
            "invariant measure of total mass one in the sense described above")
        ok, why = vf.quote_ok(self.book, doctored)
        self.assertFalse(ok, f"a flipped ∈ passed as a partial match: {why}")
        self.assertIn("OPERATOR", why)

    def test_a_flip_in_a_disagreement_quote_fails_the_whole_document(self):
        doc = doc_for()
        doc["disagreements"][0]["book_quote"] = QUOTE_1.replace("<=", ">=")
        self.assertTrue(any("book_quote" in e and "OPERATOR" in e for e in self.errs(doc)))

    def test_the_operator_pass_runs_without_the_has_operators_gate(self):
        """``has_operators`` on the DOCTORED quote is what made the hole."""
        self.assertFalse(vf.has_operators(QUOTE_1.replace("<=", "")))
        self.assertFalse(vf.quote_ok(self.book, QUOTE_1.replace("<=", ""))[0])

    def test_the_negated_glyphs_are_in_the_operator_table(self):
        for glyph in "∉∌⊄⊅⊃⊇⊊⊋≰≱≢∀∃∅":
            with self.subTest(glyph=glyph):
                self.assertIn(glyph, vf._OPERATOR_TOKENS)
        for variant, plain in (("≦", "≤"), ("⩽", "≤"),
                               ("≧", "≥"), ("⩾", "≥")):
            with self.subTest(variant=variant):
                self.assertEqual(vf.norm_operators("a %s b" % variant),
                                 vf.norm_operators("a %s b" % plain))

    def test_words_and_operator_normalisations_agree_on_the_word_stream(self):
        """The anchoring rests on this: same words, operators interleaved."""
        for raw in (BOOK, QUOTE_1, QUOTE_2, "pre-measure x<=y f : R -> C, ∀x ∃y",
                    "a−b ≠ c/=d !=e"):
            with self.subTest(raw=raw[:30]):
                words, _gaps = vf._split_operators(vf.norm_operators(raw).split())
                self.assertEqual(words, vf.norm(raw).split())


# --------------------------------------------------------------------------- #
# gates-correctness#2 — a quote is bound to a page (the running-head class)
# --------------------------------------------------------------------------- #
class TestPageRecurrenceIsRefused(BookTestCase):
    """A running head repeated through a section is not an item's passage.

    Before the fix the gate's whole claim was "this string of >=25 normalised
    characters occurs somewhere in the pinned text", and the verifier passed a
    complete attack-preset document whose ``book_passage_quote`` was the running
    head.
    """

    def test_the_source_is_loaded_page_by_page(self):
        self.assertEqual(len(self.book.pages), len(PAGES) + 1)  # the trailing \f
        self.assertEqual(self.book.words, vf.norm(BOOK))
        self.assertEqual(self.book.pages_matching(vf.norm(QUOTE_2)), [QUOTE_2_PAGE])

    def test_a_quote_records_the_page_it_matched(self):
        m = vf.quote_match(self.book, QUOTE_1)
        self.assertTrue(m.ok, m.why)
        self.assertEqual(m.pages, [QUOTE_1_PAGE])

    def test_a_running_head_is_refused_and_says_how_often_it_recurs(self):
        self.assertGreaterEqual(len(vf.norm(RUNNING_HEAD)), vf.MIN_QUOTE_CHARS)
        ok, why = vf.quote_ok(self.book, RUNNING_HEAD)
        self.assertFalse(ok, "the running head passed as a passage")
        self.assertIn("running head", why)
        self.assertIn("%d pages" % len(PAGES), why)

    def test_the_whole_document_fails_on_a_running_head_passage(self):
        errs = self.errs(doc_for(RUNNING_HEAD))
        self.assertTrue(any("book_passage_quote" in e and "running head" in e for e in errs), errs)

    def test_a_flat_string_book_binds_nothing_and_does_not_pretend_to(self):
        """``pair/`` and ``detect/`` hand in a flat string; they must still work."""
        flat = vf.BookText(vf.norm(BOOK))
        self.assertIsNone(flat.pages)
        self.assertEqual(flat.pages_matching(vf.norm(QUOTE_1)), [])
        self.assertTrue(vf.quote_ok(flat, RUNNING_HEAD)[0])  # unbindable, so unrefused


class TestQuoteIsBoundToItsItem(BookTestCase):
    """A quote must be where the item it evidences is stated.

    Before the fix ``item`` was read for nothing but the finding id, so a
    document about ``Exercise 2.9.9`` passed on a sentence six pages away.
    """

    def test_the_item_page_comes_from_the_items_own_heading(self):
        bind = vf.PageBind(self.cfg, SOURCE_ID, self.book, "Exercise 2.3.4")
        self.assertEqual(bind.pages, [QUOTE_1_PAGE])
        self.assertIn("heading", bind.how)

    def test_a_quote_from_another_section_is_refused_with_the_delta(self):
        errs = self.errs(doc_for(QUOTE_1, item="Exercise 2.9.9"))
        self.assertTrue(any("not the item's passage" in e for e in errs), errs)
        self.assertTrue(any("6 pages from" in e for e in errs), errs)

    def test_the_bind_holds_for_a_disagreement_quote_too(self):
        doc = doc_for(item="Exercise 2.9.9")
        doc["book_passage_quote"] = PAGE_7.split(".", 1)[1].strip()
        errs = self.errs(doc)
        self.assertTrue(any("disagreement[0].book_quote" in e and "not the item's passage" in e
                            for e in errs), errs)

    def test_a_neighbouring_page_is_inside_the_window(self):
        """An item's statement runs over a page break; +-2 pages is not a defect."""
        bind = vf.PageBind(self.cfg, SOURCE_ID, self.book, "Exercise 2.3.4")
        near = vf.QuoteMatch(True, "verbatim", pages=[QUOTE_1_PAGE + vf.PAGE_BIND_WINDOW])
        far = vf.QuoteMatch(True, "verbatim", pages=[QUOTE_1_PAGE + vf.PAGE_BIND_WINDOW + 1])
        self.assertEqual(bind.problem("q", near), "")
        self.assertIn("not the item's passage", bind.problem("q", far))

    def test_an_unbindable_item_is_recorded_not_passed_over(self):
        doc = doc_for(item="In-text, first paragraph of the chapter")
        self.assertEqual(self.errs(doc), [])  # unbound is not a failure
        notes = vf.binding_notes(doc, "x.json", self.cfg, self.book)
        self.assertTrue(any(n.startswith("page unbound:") for n in notes), notes)
        self.assertTrue(any("neither a numbered label nor a printed page" in n for n in notes))

    def test_an_item_naming_a_printed_page_uses_the_sources_offset(self):
        """``In-text, book p. 71`` is bindable when the run said what the offset is."""
        cfg = gate_cfg("attack-vac", self.book_path, source={"page_offset": 2})
        bind = vf.PageBind(cfg, SOURCE_ID, self.book, "In-text, book p. 3")
        self.assertEqual(bind.pages, [5])
        self.assertIn("page_offset", bind.how)
        # `page_offset: 0` means the run does not know it, so the gate binds
        # nothing rather than pretending the printed page is the PDF page.
        self.assertEqual(self.cfg.source(SOURCE_ID)["page_offset"], 0)
        self.assertEqual(vf.PageBind(self.cfg, SOURCE_ID, self.book,
                                     "In-text, book p. 3").pages, [])

    def test_gate_json_item_pages_win_over_the_heading_search(self):
        """A caller holding the source's items.json can hand the map over."""
        cfg = gate_cfg("attack-vac", self.book_path)
        cfg.source(SOURCE_ID)["item_pages"] = {"Exercise  2.3.4": 4}
        bind = vf.PageBind(cfg, SOURCE_ID, self.book, "Exercise 2.3.4")
        self.assertEqual((bind.pages, bind.how), ([4], "gate.json item_pages"))
        # and it is read from gate.json, not only settable in process
        data = dict(cfg.data)
        data["sources"] = {SOURCE_ID: {**cfg.data["sources"][SOURCE_ID],
                                       "item_pages": {"Exercise 2.3.4": [4, 5]}}}
        reloaded = vf.GateConfig(data)
        self.assertEqual(vf.PageBind(reloaded, SOURCE_ID, self.book,
                                     "Exercise 2.3.4").pages, [4, 5])

    def test_the_legacy_presets_do_not_bind_and_say_so(self):
        """The imported corpus has landed rows the bind would reject; measured."""
        for name in ("attack-legacy", "differential-legacy"):
            with self.subTest(preset=name):
                self.assertFalse(presets_mod.gate_config(
                    presets_mod.get(name), run_id="r",
                    sources={SOURCE_ID: {"text": "t", "sha256": SOURCE_SHA}},
                    repos=["ours"])["bind_item_page"])
        for name in ("differential-text", "attack-vac", "attack-hunt", "mechanical"):
            with self.subTest(preset=name):
                self.assertTrue(presets_mod.gate_config(
                    presets_mod.get(name), run_id="r",
                    sources={SOURCE_ID: {"text": "t", "sha256": SOURCE_SHA}},
                    repos=["ours"])["bind_item_page"])
        legacy = gate_cfg("attack-legacy", self.book_path, repos=["ours"])
        self.assertEqual(vf.validate_doc(doc_for(QUOTE_1, item="Exercise 2.9.9"),
                                         "x.json", legacy, self.books), [])


# --------------------------------------------------------------------------- #
# gates-correctness#3 — the union key
# --------------------------------------------------------------------------- #
BOGUS_ROW = {
    "concept": "invented",
    "kind": "NOT_A_KIND",
    "repos_involved": ["not-a-repo"],
    "faithful": "nope",
    "unfaithful_decls": ["not-a-repo:Nonexistent.decl"],
    "severity": "catastrophic",
}


class TestEveryCountedDefectKeyIsValidated(BookTestCase):
    """The validating path read one key; the counting paths read two.

    A row under ``findings`` was gated on ``disagreements`` alone and then counted
    by ``harvest.tally`` and loaded by the ledger: one key name bypassed the quote
    gate, the kind and verdict vocabularies, the witness rule and the repo
    allowlist at once.
    """

    def corpus_dir(self, doc) -> pathlib.Path:
        out = self.root / "corpus"
        out.mkdir(exist_ok=True)
        (out / "exercise-2-3-4.json").write_text(json.dumps(doc), encoding="utf-8")
        return out

    def test_the_gate_and_the_tally_read_the_same_keys(self):
        from harvest import tally

        self.assertEqual(list(vf.DEFECT_LISTS), list(tally.FINDING_LISTS))

    def test_a_row_under_each_counted_key_is_validated(self):
        for key in vf.DEFECT_LISTS:
            with self.subTest(key=key):
                doc = doc_for(**{"disagreements": [], key: [dict(BOGUS_ROW)]})
                errs = self.errs(doc)
                self.assertTrue(any("NOT_A_KIND" in e for e in errs), errs)

    def test_the_gate_refuses_no_fewer_rows_than_the_tally_counts(self):
        """The symmetry itself, over the real tally and the real ledger."""
        from harvest import tally
        from ledger import findings as ledger_findings

        doc = doc_for(disagreements=[], findings=[dict(BOGUS_ROW)])
        corpus = self.corpus_dir(doc)
        counted = tally.rows_for_run("run-A", corpus)
        loaded = ledger_findings.load_findings([str(corpus)])
        self.assertEqual(len(counted), 1, counted)
        self.assertEqual(len(loaded), 1, loaded)
        errs = self.errs(doc)
        self.assertTrue(errs, "the tally counted a row the gate validated nothing about")
        self.assertTrue(any("findings[0]" in e for e in errs), errs)

    def test_the_next_plausible_key_name_fails_loudly(self):
        doc = doc_for(defects=[dict(BOGUS_ROW)])
        errs = self.errs(doc)
        self.assertTrue(any("'defects'" in e and "does not validate" in e for e in errs), errs)

    def test_a_well_formed_row_under_the_other_key_still_passes(self):
        """Symmetry cuts both ways: validating the key is not banning it.

        ``disagreements`` stays a required key (schema v2), so a document that
        writes its rows under ``findings`` still declares the canonical one.
        """
        good = doc_for()
        good["findings"] = good["disagreements"]
        good["disagreements"] = []
        self.assertEqual(self.errs(good), [])

    def test_matches_are_not_mistaken_for_defect_rows(self):
        doc = doc_for()
        doc["matches"] = [{"concept": "c", "decls": [], "verdict": "NO_DEFECT_FOUND"}]
        self.assertEqual(self.errs(doc), [])


# --------------------------------------------------------------------------- #
# prompts-workflows#9 — the copyright ceiling
# --------------------------------------------------------------------------- #
class TestQuoteLengthCeiling(BookTestCase):
    def test_a_page_long_quote_is_refused(self):
        whole = BOOK.replace("\f", " ")
        pasted = whole * (vf.MAX_QUOTE_CHARS // len(whole) + 1)
        self.assertGreater(len(pasted), vf.MAX_QUOTE_CHARS)
        ok, why = vf.quote_ok(self.book, pasted)
        self.assertFalse(ok)
        self.assertIn("ceiling", why)

    def test_the_ceiling_is_configurable_per_run(self):
        m = vf.quote_match(self.book, QUOTE_1, max_chars=40)
        self.assertFalse(m.ok)
        self.assertIn("40-character ceiling", m.why)
        self.assertTrue(vf.quote_match(self.book, QUOTE_1, max_chars=0).ok)  # 0 = off

    def test_the_floor_still_bites(self):
        ok, why = vf.quote_ok(self.book, "Show that")
        self.assertFalse(ok)
        self.assertIn("too short", why)


if __name__ == "__main__":
    unittest.main()
