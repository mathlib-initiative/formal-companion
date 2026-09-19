"""Review-fix regressions for the findings gate (stdlib unittest).

One test per defect the code review found in ``scripts/gate/``:

* the operator pass was an independent window search, so a single flipped ``<=``,
  a DELETED operator and an unmapped glyph all passed as *verbatim*;
* nothing bound a quote to the item or the page it is supposed to evidence, so a
  section running head passed as an item's passage;
* the quote gate had a floor and no ceiling, against SKILL.md hard rule 6;
* defect rows under a ``findings`` key were never validated but were counted by
  ``harvest.tally`` and loaded by the ledger;
* the changed-files check could not see a deletion or a rename, and its ownership
  rule was silent about being off;
* the gate ran bare ``git`` inside the worktree it judges, so a worker could hand
  it a program to execute through ``.git/config``;
* 'the gate is broken' and 'your findings are rejected' shared exit code 1;
* the item-page map, the best of the three binding routes, could not reach the
  gate from any caller;
* three recorded measurements did not re-derive from the corpus they name.

The synthetic book below is a public-domain paraphrase: no source text lives in
this repo (CLAUDE.md S1).  Every test runs without the private baselines except
``TestTheRecordedBaselineFiguresReDerive``, which needs CF_BASELINE_CORPUS and
CF_BOOK_TXT and skips cleanly without them.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import statistics
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


def _env_dir(name: str) -> pathlib.Path | None:
    """A local baseline path from the environment, or None (the suite must pass
    on a machine that has none of them -- CLAUDE.md S7)."""
    value = os.environ.get(name)
    if not value:
        return None
    path = pathlib.Path(value).expanduser()
    return path if path.exists() else None


#: the 2,672-file imported corpus and the pinned text of the source it quotes
_CORPUS = _env_dir("CF_BASELINE_CORPUS")
_BOOK_TXT = _env_dir("CF_BOOK_TXT")

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

    def test_gate_json_carries_both_thresholds(self):
        """A source whose licence note permits more raises them per run."""
        tight = gate_cfg("attack-vac", self.book_path, max_quote_chars=40)
        self.assertTrue(any("40-character ceiling" in e
                            for e in vf.validate_doc(doc_for(), "x.json", tight, self.books)))
        warn = gate_cfg("attack-vac", self.book_path, long_quote_chars=30)
        notes = vf.binding_notes(doc_for(), "x.json", warn, self.book)
        self.assertTrue(any(n.startswith("over-long quote:") for n in notes), notes)
        self.assertEqual(vf.binding_notes(doc_for(), "x.json", self.cfg, self.book), [])


# --------------------------------------------------------------------------- #
# gates-correctness#7 + security-safety#1 — the changed-files half of the gate
# --------------------------------------------------------------------------- #
class WorktreeTestCase(unittest.TestCase):
    """A real git worktree with one worker's landed pair already on ``main``."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMPDIR)
        self.root = pathlib.Path(self.tmp.name)
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        self.git("init", "-q", "-b", "main")
        (self.root / "findings").mkdir()
        (self.root / "tasks").mkdir()
        (self.root / "findings" / "alice.json").write_text(
            json.dumps({"schema_version": 2, "disagreements": [{"kind": "CONTENT"}]}),
            encoding="utf-8")
        (self.root / "tasks" / "alice.yaml").write_text("id: alice\nstatus: complete\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "alice's finding lands")
        self.git("checkout", "-q", "-b", "carol")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, env=self.env,
                       capture_output=True, text=True)

    def write_own_pair(self):
        (self.root / "findings" / "carol.json").write_text("{}\n")
        (self.root / "tasks" / "carol.yaml").write_text("id: carol\n")

    def entries(self):
        got, errs = vf.changed_entries(self.root, "main")
        self.assertEqual(errs, [])
        return got


class TestChangedFilesSeesDeletions(WorktreeTestCase):
    """`git diff --name-only` erased A/M/D, so a destructive change set passed.

    All three of these exited 0 before the fix, and the pure deletion could not
    even be told apart from an empty contribution.
    """

    def test_a_pure_deletion_is_refused(self):
        self.git("rm", "-q", "findings/alice.json", "tasks/alice.yaml")
        self.git("commit", "-qm", "drop alice")
        entries = self.entries()
        self.assertEqual(entries.get("findings/alice.json"), "D")
        problems = vf.check_changed(entries)
        self.assertTrue(any("deleted" in p for p in problems), problems)
        self.assertTrue(any("no findings file was written" in p for p in problems), problems)

    def test_a_deletion_beside_the_workers_own_addition_is_refused(self):
        self.write_own_pair()
        self.git("rm", "-q", "findings/alice.json", "tasks/alice.yaml")
        self.git("add", "-A")
        self.git("commit", "-qm", "mine in, alice's out")
        problems = vf.check_changed(self.entries())
        self.assertTrue(any("findings/alice.json" in p and "deleted" in p for p in problems),
                        problems)

    def test_a_rename_away_is_refused(self):
        self.git("mv", "findings/alice.json", "findings/carol.json")
        self.git("mv", "tasks/alice.yaml", "tasks/carol.yaml")
        self.git("commit", "-qm", "rename alice to me")
        problems = vf.check_changed(self.entries())
        self.assertTrue(any("findings/alice.json" in p for p in problems), problems)

    def test_emptying_another_workers_finding_is_refused_by_the_task_id(self):
        (self.root / "findings" / "alice.json").write_text('{"disagreements": []}\n')
        (self.root / "tasks" / "alice.yaml").write_text(
            "id: alice\nstatus: complete\nnote: rebased\n")
        self.write_own_pair()
        self.git("add", "-A")
        self.git("commit", "-qm", "tidy up")
        entries = self.entries()
        self.assertEqual(vf.check_changed(entries), [],
                         "the pair rule alone cannot see this")
        problems = vf.check_changed(entries, task_id="carol")
        self.assertTrue(any("findings/alice.json" in p and "not to this worker's" in p
                            for p in problems), problems)

    def test_a_workers_own_pair_still_passes_with_its_task_id(self):
        self.write_own_pair()
        self.git("add", "-A")
        self.git("commit", "-qm", "mine")
        self.assertEqual(vf.check_changed(self.entries(), task_id="carol"), [])

    def test_the_task_id_comes_from_the_environment_by_default(self):
        self.write_own_pair()
        self.git("add", "-A")
        self.git("commit", "-qm", "mine")
        script = str(SKILL / "scripts" / "gate" / "check_changed_files.py")
        for task_id, code in (("carol", 0), ("alice", 1)):
            with self.subTest(task_id=task_id):
                proc = subprocess.run(
                    [sys.executable, script, "--base", "main", "--root", str(self.root)],
                    capture_output=True, text=True,
                    env={**self.env, "TEND_TASK_ID": task_id})
                self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)

    def test_a_missing_base_ref_still_fails_closed(self):
        entries, errs = vf.changed_entries(self.root, "no-such-branch")
        self.assertEqual(entries, {})
        self.assertTrue(errs and "fails closed" in errs[0])

    def test_changed_paths_keeps_its_old_shape_for_existing_callers(self):
        self.write_own_pair()
        paths, errs = vf.changed_paths(self.root, "main")
        self.assertEqual(errs, [])
        self.assertIsInstance(paths, set)
        self.assertEqual(paths, {"findings/carol.json", "tasks/carol.yaml"})
        self.assertEqual(vf.check_changed(paths), [])  # a bare set: no statuses


class TestTheGateRunsNoProgramTheWorkerChose(WorktreeTestCase):
    """The gate runs git inside the worktree it is judging.

    An index refresh runs ``core.fsmonitor``, named in that worktree's own
    ``.git/config``, and the payload lives under ``.git/`` where the changed-files
    check never looks — so the gate passed the contribution clean while executing
    the worker's program.  Verified before the fix: the file below was written.
    """

    def arm(self) -> pathlib.Path:
        proof = self.root / ".git" / "proof.txt"
        hook = self.root / ".git" / "x.sh"
        hook.write_text("#!/bin/sh\necho ran > %s\n" % proof, encoding="utf-8")
        hook.chmod(0o755)
        self.git("config", "core.fsmonitor", str(hook))
        self.write_own_pair()
        return proof

    def test_no_hook_of_the_worktree_is_executed(self):
        proof = self.arm()
        paths, errs = vf.changed_paths(self.root, "main")
        self.assertEqual(errs, [])
        self.assertEqual(paths, {"findings/carol.json", "tasks/carol.yaml"})
        self.assertFalse(proof.exists(), "the gate executed a program the worker chose")

    def test_the_inlined_pins_are_gitsafes_own(self):
        from cflib import gitsafe

        self.assertEqual(vf._GIT_SAFE_CONFIG, gitsafe.SAFE_CONFIG)
        self.assertEqual(vf._GIT_SAFE_ENV, gitsafe.SAFE_ENV)

    def test_no_bare_git_invocation_in_the_tree(self):
        """Tree-wide, not one adapter attribute: the hole was in the gate.

        The allowlist is documented, not incidental: ``cflib/gitsafe.py`` IS the
        boundary, and ``cfcmd/init.py`` acts on the user's own workspace repo,
        never on a companion.
        """
        allowed = {"cflib/gitsafe.py", "cfcmd/init.py"}
        offenders = []
        for path in sorted((SKILL / "scripts").rglob("*.py")):
            rel = path.relative_to(SKILL / "scripts").as_posix()
            if rel in allowed:
                continue
            body = path.read_text(encoding="utf-8")
            for m in re.finditer(r"""\[\s*["']git["']""", body):
                window = body[m.start():m.start() + 120]
                if "SAFE_CONFIG" not in window:
                    line = body[:m.start()].count("\n") + 1
                    offenders.append(f"{rel}:{line}")
        self.assertEqual(offenders, [], "bare git invocation(s) outside gitsafe")


# --------------------------------------------------------------------------- #
# architecture#7 — 'your findings are bad' is not 'the gate is broken'
# --------------------------------------------------------------------------- #
class TestExitCodes(BookTestCase):
    """One code for both meant a mis-rsynced gate read to a worker as a verdict."""

    def gate_dir(self, doc) -> list[str]:
        (self.root / "findings").mkdir(exist_ok=True)
        (self.root / "findings" / "exercise-2-3-4.json").write_text(
            json.dumps(doc), encoding="utf-8")
        cfgp = self.root / "gate.json"
        cfgp.write_text(json.dumps(self.cfg.data), encoding="utf-8")
        return ["--gate-config", str(cfgp), "--root", str(self.root), "--quiet"]

    def test_a_clean_run_is_zero_and_a_rejected_finding_is_one(self):
        argv = self.gate_dir(doc_for())
        self.assertEqual(vf.main(argv), vf.EXIT_OK)
        self.assertEqual(vf.main(self.gate_dir(doc_for(RUNNING_HEAD))), vf.EXIT_REJECTED)

    def test_a_missing_gate_config_is_not_a_rejected_finding(self):
        argv = self.gate_dir(doc_for())
        argv[1] = str(self.root / "nope.json")
        self.assertEqual(vf.main(argv), vf.EXIT_GATE_BROKEN)

    def test_an_unreadable_source_text_is_not_a_rejected_finding(self):
        argv = self.gate_dir(doc_for())
        self.book_path.unlink()
        self.assertEqual(vf.main(argv), vf.EXIT_GATE_BROKEN)

    def test_a_source_text_override_naming_no_source_is_not_a_rejected_finding(self):
        argv = self.gate_dir(doc_for())
        self.assertEqual(vf.main(argv + ["--source-text", "no-such-source=/tmp/x.txt"]),
                         vf.EXIT_GATE_BROKEN)

    def test_a_missing_base_ref_is_not_a_rejected_finding(self):
        argv = self.gate_dir(doc_for())
        self.assertEqual(vf.main(argv + ["--changed-files", "--base", "no-such-branch"]),
                         vf.EXIT_GATE_BROKEN)

    def test_the_three_codes_are_distinct(self):
        self.assertEqual(sorted({vf.EXIT_OK, vf.EXIT_REJECTED, vf.EXIT_GATE_BROKEN}),
                         [0, 1, 3])


# --------------------------------------------------------------------------- #
# the gate's own prose: references that name something, figures that re-derive
# --------------------------------------------------------------------------- #
class TestTheDocstringsPointAtRealThings(unittest.TestCase):
    """A refactor left ``:class:`ItemBinding``` behind; the class is ``PageBind``.

    Cheap to check and worth checking: these docstrings are how the next reader
    finds the code that implements the rule.
    """

    def test_every_cross_reference_names_a_module_attribute(self):
        for module in (vf, presets_mod):
            source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
            # a dotted name (``cflib.hashing.finding_id``) points outside this
            # module on purpose; a bare one claims to be defined in it
            for role, name in re.findall(r":(class|func|data):`~?([A-Za-z_]\w*)`", source):
                with self.subTest(module=module.__name__, ref=f"{role}:{name}"):
                    self.assertTrue(hasattr(module, name),
                                    f"{module.__name__} docstring names {name!r}, "
                                    f"which is not defined there")


@unittest.skipIf(_CORPUS is None or _BOOK_TXT is None,
                 "set CF_BASELINE_CORPUS and CF_BOOK_TXT to re-derive the figures")
class TestTheRecordedBaselineFiguresReDerive(unittest.TestCase):
    """Every figure the gate's comments cite, measured again from the corpus.

    These numbers are the recorded justification for turning ``bind_item_page``
    and ``check_operators`` off on the two ``*-legacy`` presets -- for accepting
    landed rows a new run would refuse -- so they have to be re-derivable, and
    three of them were not: the window comment claimed 2,925 of 2,942 bindable
    quotes with 17 outside, and the PageBind docstring 851 unplaceable, none of
    which any configuration of this code produces.  The page figures HALVE without
    the source's own page offset, so the offset is part of every assertion here.
    """

    OFFSET = 16          # registry/sources.yaml: pdf_page = printed_page + 16

    @classmethod
    def setUpClass(cls):
        from gate import convert_v1

        raw = _BOOK_TXT.read_text(errors="ignore")
        # operators OFF is the legacy configuration this corpus is gated under;
        # the operator pass is measured separately below against the same text
        plain = vf.BookText.from_text(raw, operators=False)
        with_ops = vf.BookText.from_text(raw, operators=True)
        cfg = {n: cls._cfg(n) for n in (0, cls.OFFSET)}
        f = dict(files=0, quotes=0, operator_failures=0, other_failures=0, partial=0,
                 over_long=0, max_pages=0, stray_defect_arrays=0,
                 unbound={0: 0, cls.OFFSET: 0}, straddle={0: 0, cls.OFFSET: 0},
                 bindable={0: 0, cls.OFFSET: 0}, beyond={0: 0, cls.OFFSET: 0},
                 unbound_why={}, beyond_files=set(), lengths=[])
        for run, mode in (("findings", "differential"), ("findings-pdf", "differential"),
                          ("findings-gpt", "differential"), ("findings-vac", "attack"),
                          ("findings-hunt", "attack")):
            for path in sorted((_CORPUS / run).glob("*.json")):
                v1 = json.loads(path.read_text())
                f["files"] += 1
                f["stray_defect_arrays"] += sum(
                    1 for key, value in v1.items()
                    if key not in vf.DEFECT_LISTS and key != "matches"
                    and isinstance(value, list)
                    and any(isinstance(x, dict) and "kind" in x for x in value))
                doc = convert_v1.convert_doc(v1, run_id=f"baseline-{run}",
                                             source_id=SOURCE_ID, source_sha=SOURCE_SHA,
                                             mode=mode, repos=["ours", "teorth", "adam"])
                binds = {n: vf.PageBind(cfg[n], SOURCE_ID, plain, doc.get("item"))
                         for n in cfg}
                for where, quote in vf._quotes_of(doc):
                    f["quotes"] += 1
                    f["lengths"].append(len(quote))
                    f["over_long"] += len(quote) > vf.LONG_QUOTE_CHARS
                    if not vf.quote_match(with_ops, quote, max_chars=0).ok:
                        f["operator_failures"] += 1
                    m = vf.quote_match(plain, quote, max_chars=0)
                    if not m.ok:
                        f["other_failures"] += 1
                        continue
                    f["partial"] += m.why.startswith("partial")
                    f["max_pages"] = max(f["max_pages"], len(m.pages))
                    for n, bind in binds.items():
                        if not bind.pages:
                            f["unbound"][n] += 1
                            if n == 0:
                                f["unbound_why"][bind.how] = f["unbound_why"].get(bind.how, 0) + 1
                        elif not m.pages:
                            f["straddle"][n] += 1
                        else:
                            f["bindable"][n] += 1
                            delta = min(abs(p - k) for p in m.pages for k in bind.pages)
                            if delta > vf.PAGE_BIND_WINDOW:
                                f["beyond"][n] += 1
                                if n == cls.OFFSET:
                                    f["beyond_files"].add(f"{run}/{path.name}")
        cls.f = f

    @staticmethod
    def _cfg(offset: int) -> vf.GateConfig:
        data = presets_mod.gate_config(
            presets_mod.get("differential-legacy"), run_id="baseline",
            sources={SOURCE_ID: {"text": str(_BOOK_TXT), "sha256": SOURCE_SHA,
                                 "page_offset": offset}},
            repos=["ours", "teorth", "adam"])
        data["bind_item_page"] = True     # the legacy presets turn it off
        return vf.GateConfig(data)

    def test_the_corpus_is_the_one_the_figures_were_taken_over(self):
        self.assertEqual(self.f["files"], 2672)
        self.assertEqual(self.f["quotes"], 3019)

    def test_the_page_bind_figures_with_the_sources_own_offset(self):
        """PAGE_BIND_WINDOW's comment and gate_config()'s bind_item_page comment."""
        f, n = self.f, self.OFFSET
        self.assertEqual(f["unbound"][n], 26)
        self.assertEqual(f["straddle"][n], 8)
        self.assertEqual(f["bindable"][n], 2985)
        self.assertEqual(f["beyond"][n], 16)
        self.assertEqual(f["bindable"][n] - f["beyond"][n], 2969)
        self.assertEqual(len(f["beyond_files"]), 16)

    def test_the_same_figures_without_the_offset(self):
        """PageBind's docstring: the count of unplaceable quotes, and why."""
        f = self.f
        self.assertEqual(f["unbound"][0], 262)
        self.assertEqual(f["bindable"][0], 2750)
        self.assertEqual(f["beyond"][0], 16)
        printed = sum(c for why, c in f["unbound_why"].items()
                      if "neither a numbered label nor a printed page" in why)
        missing = sum(c for why, c in f["unbound_why"].items()
                      if "occurs nowhere in the pinned text" in why)
        self.assertEqual((printed, missing), (230, 32))

    def test_the_operator_pass_and_the_recurrence_ceiling(self):
        """_operator_problem's docstring, and quote_match's page pass."""
        self.assertEqual(self.f["operator_failures"], 5)
        self.assertEqual(self.f["other_failures"], 0)
        # nothing in the corpus recurs past the ceiling, so it costs no baseline row
        self.assertEqual(self.f["max_pages"], 5)
        self.assertLessEqual(self.f["max_pages"], vf.MAX_MATCH_PAGES)

    def test_the_quote_length_figures_and_the_partial_window_count(self):
        """LONG_QUOTE_CHARS's comment and partial_quotes' docstring."""
        lengths = sorted(self.f["lengths"])
        self.assertEqual(statistics.median(lengths), 176)
        self.assertEqual(lengths[int(0.9 * len(lengths))], 329)
        self.assertEqual(lengths[-1], 2620)
        self.assertEqual(self.f["over_long"], 36)
        self.assertEqual(max(lengths), 2620)
        self.assertLess(max(lengths), vf.MAX_QUOTE_CHARS)  # the ceiling rejects none
        self.assertEqual(self.f["partial"], 287)

    def test_no_baseline_file_carries_a_stray_top_level_defect_array(self):
        """validate_doc's refusal of the next plausible key name, measured."""
        self.assertEqual(self.f["stray_defect_arrays"], 0)


if __name__ == "__main__":
    unittest.main()
