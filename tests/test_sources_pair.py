"""Regression tests for the source and pair stages.

These pin the numbers the audit actually produced, so a refactor that changes
them is visible:

* the pinned Tao edition yields **411** Chapter-1 items and **Exercise 1.3.19**
  is one of them (the baseline's single-space regex found 410 and the task
  generator silently dropped it);
* the three baseline statement tables pair into **225** items;
* ``def-coverage.ours.json`` is reproduced exactly: 32 stated / 4 bridge-only /
  0 missing, and 29 uncovered numbered items.

They also pin the multi-source model's own promises
(docs/design/multi-source-model.md):

* ``prepare()`` INFERS the offset and the item kinds from the text -- nobody
  types ``16`` (R2);
* every pair, queue row, target, coverage file and PAGE_ANCHOR finding carries
  ``source_id`` and ``source_sha`` (R4);
* ``cf pair`` walks M sources and writes one ``pairs/<source_id>/`` tree each,
  never one directory that mixes two editions (R1).

Everything reads the local baseline through environment variables and SKIPS
cleanly when they are unset:

    CF_BOOK_TXT       = ~/Desktop/tao-differential/book.official.txt
    CF_BASELINE_DIFF  = ~/Desktop/tao-differential        (ours/teorth/adam.jsonl)
    CF_BASELINE_CORPUS= ~/Desktop/tao-diff-run            (data/*.jsonl)
    CF_MT             = ~/Desktop/measure_theory          (the pinned PDF)
    CF_TEST_TMP       = directory for scratch output (default: a temp dir)

Run:  python3 -m unittest tests.test_sources_pair -v
"""

from __future__ import annotations

import json
import atexit
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from sources import prepare_source  # noqa: E402
from sources.compat import finding_id, parse_item, parse_item_reject, parse_pages  # noqa: E402
from sources.fetch_errata import html_to_markdown, stable_text  # noqa: E402
from pair import anchor_check, compare as compare_mod  # noqa: E402
from pair import def_coverage as defcov, make_queue  # noqa: E402

#: the published AMS edition, the one both target companions anchor to
SOURCE_ID = "tao-measure-gsm126-published"
SOURCE_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
PDF_REL = ".lake/official-pdf/gsm-126-tao5-measure-book.pdf"
KINDS = prepare_source.SOURCE_KINDS
GRAMMAR_KINDS = tuple(KINDS) + ("Strategy", "Claim")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def book_text_path() -> str | None:
    p = os.environ.get("CF_BOOK_TXT")
    return p if p and os.path.exists(p) else None


def baseline_dir() -> str | None:
    for env, sub in (("CF_BASELINE_DIFF", ""), ("CF_BASELINE_CORPUS", "data")):
        root = os.environ.get(env)
        if root and os.path.exists(os.path.join(root, sub, "ours.jsonl")):
            return os.path.join(root, sub)
    return None


def pdf_path() -> str | None:
    root = os.environ.get("CF_MT")
    p = os.path.join(root, PDF_REL) if root else None
    return p if p and os.path.exists(p) else None


def tmp_dir(prefix: str) -> str:
    """A scratch directory removed when the interpreter exits.

    Without the atexit hook these accumulate; with CF_TEST_TMP pointing at a
    workspace directory they accumulated *inside the workspace*.
    """
    base = os.environ.get("CF_TEST_TMP")
    if base:
        os.makedirs(base, exist_ok=True)
        d = tempfile.mkdtemp(prefix=prefix, dir=base)
    else:
        d = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, d, True)
    return d


def load_baseline_tables(names=("ours", "teorth", "adam")) -> dict:
    d = baseline_dir()
    return compare_mod.load_tables({n: os.path.join(d, "%s.jsonl" % n) for n in names})


def items_from_book_text(offset: int = 16) -> dict:
    """An ``items.json`` payload built from the pinned edition's extracted text."""
    with open(book_text_path(), encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    items = prepare_source.find_items(text, KINDS, offset=offset)
    return {
        "source_id": SOURCE_ID, "source_sha": SOURCE_SHA, "offset": offset,
        "kinds": list(KINDS), "count": len(items), "items": items,
    }


# --------------------------------------------------------------------------
# item grammar (no fixtures needed)
# --------------------------------------------------------------------------


class ItemGrammarTests(unittest.TestCase):
    def test_whitespace_tolerance(self):
        """The 411-vs-410 trap: irregular whitespace must not lose an item."""
        for raw in ("Exercise    1.3.19", "Exercise\t1.3.19", "Exercise 1.3.19.", "`Exercise 1.3.19`"):
            parsed = parse_item(raw, GRAMMAR_KINDS)
            self.assertIsNotNone(parsed, raw)
            self.assertEqual(parsed.key, "Exercise 1.3.19")

    def test_sub_part_and_title_are_separate(self):
        self.assertEqual(parse_item("Exercise 1.4.29(iii)", GRAMMAR_KINDS).sub, "(iii)")
        self.assertEqual(parse_item("Definition 1.1.1 (boxes)", GRAMMAR_KINDS).key, "Definition 1.1.1")
        self.assertEqual(parse_item("Definition 1.1.1 (boxes)", GRAMMAR_KINDS).sub, "")

    def test_in_text_items_key_on_page_or_reference(self):
        self.assertEqual(parse_item("In-text, book p. 23", GRAMMAR_KINDS).key, "In-text p.23")
        self.assertTrue(parse_item("In-text, book p. 23", GRAMMAR_KINDS).is_intext)
        ref = parse_item("In-text definition, Lemma 1.2.13(iii)", GRAMMAR_KINDS)
        self.assertTrue(ref.key.startswith("In-text Lemma 1.2.13"))

    def test_junk_is_rejected_not_keyed(self):
        """compare.norm() fell through and made this a first-class key."""
        parsed, reason = parse_item_reject("Corollary (Lemma 1.2.5 + Exercise 1.2.4)", GRAMMAR_KINDS)
        self.assertIsNone(parsed)
        self.assertTrue(reason)

    def test_unknown_kind_is_actionable(self):
        parsed, reason = parse_item_reject("Strategy 2.1.1", KINDS)
        self.assertIsNone(parsed)
        self.assertEqual(reason, "unknown_kind")
        self.assertIsNotNone(parse_item("Strategy 2.1.1", GRAMMAR_KINDS))

    def test_page_anchor_parsing_is_tolerant(self):
        """The recovered queue builder used re.match and lost every 'book p. N'."""
        self.assertEqual(parse_pages("23"), [23])
        self.assertEqual(parse_pages("23-25"), [23, 24, 25])
        self.assertEqual(parse_pages("book p. 135"), [135])
        self.assertEqual(parse_pages(None), [])
        self.assertEqual(parse_pages("23-999", span=3), [23, 24, 25, 26])


class ErrataStabilityTests(unittest.TestCase):
    def test_volatile_cache_footer_does_not_count_as_a_change(self):
        """WordPress appends 'generated 17 seconds ago' to every response."""
        page = "<html><body><ul><li>p. 23: fix</li></ul></body></html>\n<!--\n\tgenerated %d seconds ago\n-->"
        self.assertNotEqual(page % 17, page % 41)
        self.assertEqual(stable_text(page % 17), stable_text(page % 41))


class ErrataRenderTests(unittest.TestCase):
    def test_html_to_markdown_keeps_every_entry(self):
        """No summarising fetch: every list item survives verbatim."""
        html = (
            "<html><head><style>x{}</style></head><body><h2>Errata</h2>"
            "<ul><li>p. 23: <em>Definition 1.5.11</em> should read X</li>"
            "<li>p. 40: typo</li></ul><p>End.</p></body></html>"
        )
        md = html_to_markdown(html)
        self.assertIn("## Errata", md)
        self.assertIn("Definition 1.5.11", md)
        self.assertIn("- p. 40: typo", md)
        self.assertNotIn("x{}", md)


# --------------------------------------------------------------------------
# source items
# --------------------------------------------------------------------------


@unittest.skipUnless(book_text_path(), "set CF_BOOK_TXT to the pinned edition's text")
class SourceItemsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items_json = items_from_book_text()
        cls.items = cls.items_json["items"]
        cls.by_item = {r["item"]: r for r in cls.items}

    def test_chapter_one_has_411_items_including_exercise_1_3_19(self):
        ch1 = [r for r in self.items if r["chapter"] == 1]
        self.assertEqual(len(ch1), 411)
        self.assertIn("Exercise 1.3.19", self.by_item)

    def test_single_space_regex_finds_only_410(self):
        """Documents the defect this stage fixes (design/stage_map.md Stage 1)."""
        with open(book_text_path(), encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        found = set()
        for kind in KINDS:  # the baseline's literal-single-space regex
            found |= {m.group(1) for m in re.finditer(r"(?:^|\n)\s*(%s 1\.\d+\.\d+)" % kind, text)}
        self.assertEqual(len(found), 410)
        self.assertNotIn("Exercise 1.3.19", found)

    def test_printed_page_map(self):
        row = self.by_item["Definition 1.1.1"]
        self.assertEqual(row["pdf_page"] - row["page"], 16)
        self.assertEqual(row["page"], 5)
        self.assertIn("Intervals", row["title"])

    def test_ambiguous_label_resolves_to_its_own_section(self):
        """'first line-start hit' puts Exercise 1.4.29 on a S1.3 footer (PDF 77)."""
        row = self.by_item["Exercise 1.4.29"]
        self.assertTrue(row["ambiguous"])
        self.assertEqual(row["pdf_page"], 111)
        self.assertIn(77, [o["pdf_page"] for o in row["occurrences"]])

    def test_definition_universe_matches_def_coverage(self):
        defs = [r for r in self.items if r["kind"] == "Definition" and r["chapter"] == 1]
        self.assertEqual(len(defs), 36)  # 32 stated + 4 bridge-only + 0 missing


@unittest.skipUnless(pdf_path() and shutil.which("pdftotext"), "needs CF_MT and pdftotext")
class PrepareSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tmp_dir("cf-source-")
        cls.payload = prepare_source.prepare(
            pdf_path(), cls.out, source_id=SOURCE_ID, offset=16, kinds=KINDS,
            edition="GSM 126", expect_sha=SOURCE_SHA,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_meta_json_pins_the_source(self):
        self.assertEqual(self.payload["source_id"], SOURCE_ID)
        self.assertEqual(self.payload["sha256"], SOURCE_SHA)
        self.assertEqual(self.payload["pages"], 265)
        self.assertEqual(self.payload["offset"], 16)
        self.assertEqual(self.payload["items"], 442)
        self.assertEqual(prepare_source.load_meta(self.out)["sha256"], SOURCE_SHA)

    def test_pages_and_items_written(self):
        self.assertTrue(os.path.exists(os.path.join(self.out, "pages", "p001.txt")))
        self.assertEqual(len(os.listdir(os.path.join(self.out, "pages"))), 265)
        items = prepare_source.load_items(self.out)
        self.assertEqual(items["source_id"], SOURCE_ID)
        self.assertEqual(items["source_sha"], SOURCE_SHA)
        self.assertEqual(len([r for r in items["items"] if r["chapter"] == 1]), 411)

    def test_second_run_is_a_no_op(self):
        again = prepare_source.prepare(
            pdf_path(), self.out, source_id=SOURCE_ID, offset=16, kinds=KINDS,
            expect_sha=SOURCE_SHA,
        )
        self.assertTrue(again["skipped"])

    def test_wrong_source_is_refused(self):
        with self.assertRaises(ValueError):
            prepare_source.prepare(
                pdf_path(), self.out, source_id=SOURCE_ID, offset=16, kinds=KINDS,
                expect_sha="deadbeef", force=True,
            )


@unittest.skipUnless(pdf_path() and shutil.which("pdftotext"), "needs CF_MT and pdftotext")
class PrepareSourceInfersTests(unittest.TestCase):
    """R2: nobody types the offset or the kind list -- the text is read for them."""

    @classmethod
    def setUpClass(cls):
        cls.out = tmp_dir("cf-infer-")
        cls.payload = prepare_source.prepare(
            pdf_path(), cls.out, source_id=SOURCE_ID, expect_sha=SOURCE_SHA,
            write_pages=False,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_the_offset_is_inferred_from_the_running_heads(self):
        self.assertEqual(self.payload["offset"], 16)
        self.assertEqual(self.payload["offset_method"], "running_head_mode")
        self.assertGreaterEqual(self.payload["offset_confidence"], 0.9)
        self.assertGreater(self.payload["offset_voting_pages"], 200)
        self.assertEqual(self.payload["printed_page_rule"], "printed = pdf_page - 16")

    def test_the_kinds_are_inferred_from_the_headings(self):
        for kind in ("Definition", "Exercise", "Lemma", "Theorem", "Remark"):
            self.assertIn(kind, self.payload["kinds"])
            self.assertGreaterEqual(self.payload["kind_counts"][kind], 3)
        self.assertEqual(self.payload["warnings"], [])

    def test_inference_finds_the_same_411_items_as_the_typed_numbers(self):
        items = prepare_source.load_items(self.out)
        self.assertEqual(len([r for r in items["items"] if r["chapter"] == 1]), 411)
        self.assertIn("Exercise 1.3.19", prepare_source.items_index(items))


class HeadingRegexTests(unittest.TestCase):
    """An item that BEGINS a page is still an item.

    pdftotext puts the form feed between the pages, so the character before the
    first heading of a page is ``\f`` and not a newline.  The index anchored on
    ``^|\n`` alone, which made such an item invisible: it could not be paired,
    could not enter a task universe, and was reported by nothing -- the exact
    defect class this audit exists to find, inside the audit's own index.
    """

    PAGE_BREAK = (
        "Chapter 1. Sets\n\nDefinition 1.1.1 (Box). A box is a product.\n"
        "\fTheorem 1.1.4 (Additivity). If E and F are disjoint,\n"
        "then m(E) + m(F).\n\nRemark 1.1.5. By refinement.\n"
    )

    def _labels(self, text, **kw):
        return [m.group("label")
                for m in prepare_source.heading_regex(**kw).finditer(text)]

    def test_an_item_at_a_page_boundary_is_found(self):
        labels = self._labels(self.PAGE_BREAK)
        self.assertIn("Theorem 1.1.4", labels)
        self.assertEqual(labels,
                         ["Definition 1.1.1", "Theorem 1.1.4", "Remark 1.1.5"])

    def test_find_items_pages_it_to_the_page_it_starts(self):
        items = {r["item"]: r for r in prepare_source.find_items(self.PAGE_BREAK, offset=0)}
        self.assertEqual(items["Definition 1.1.1"]["pdf_page"], 1)
        self.assertEqual(items["Theorem 1.1.4"]["pdf_page"], 2)
        self.assertEqual(items["Remark 1.1.5"]["pdf_page"], 2)

    def test_a_mid_line_mention_is_still_not_a_heading(self):
        """The fix widens the anchor by one character, not by dropping it."""
        self.assertEqual(
            self._labels("as shown in Theorem 1.1.4 above, the sum is finite.\n"), [])


@unittest.skipUnless(pdf_path() and shutil.which("pdftotext"), "needs CF_MT and pdftotext")
class PrepareDeclaredOffsetTests(unittest.TestCase):
    """A human's offset must survive the guess it was written to replace.

    ``prepare`` writes meta.json before its caller judges the confidence, so a
    refused weak inference is on disk anyway.  Reuse keyed on the offset VALUE
    then made ``cf sources set <id> offset=N`` unreachable whenever the human
    agreed with the low-confidence guess: the cached payload came back carrying
    the guess's confidence and the caller refused it again, for ever.
    """

    def setUp(self):
        # a fresh directory per test: these cases are ABOUT what is cached on
        # disk, so sharing one would make them depend on their own order
        self.out = tmp_dir("cf-declared-")
        self.addCleanup(shutil.rmtree, self.out, True)
        # first: infer, and accept whatever confidence the text supports
        self.first = prepare_source.prepare(
            pdf_path(), self.out, source_id=SOURCE_ID, expect_sha=SOURCE_SHA,
            write_pages=False)

    def test_declaring_the_same_offset_re_runs_and_reports_it_as_declared(self):
        self.assertEqual(self.first["offset_method"], "running_head_mode")
        again = prepare_source.prepare(
            pdf_path(), self.out, source_id=SOURCE_ID, offset=self.first["offset"],
            expect_sha=SOURCE_SHA, write_pages=False)
        self.assertFalse(again.get("skipped"))
        self.assertEqual(again["offset_method"], "declared")
        self.assertGreaterEqual(again["offset_confidence"], 0.95)

    def test_a_second_declared_run_is_a_no_op(self):
        kw = dict(source_id=SOURCE_ID, offset=16, expect_sha=SOURCE_SHA, write_pages=False)
        prepare_source.prepare(pdf_path(), self.out, **kw)
        self.assertTrue(prepare_source.prepare(pdf_path(), self.out, **kw).get("skipped"))


@unittest.skipUnless(pdf_path() and shutil.which("pdftoppm"), "needs CF_MT and pdftoppm")
class RenderPagesTests(unittest.TestCase):
    def test_one_page_renders_to_a_stable_name(self):
        from sources import render_pages

        out = tmp_dir("cf-img-")
        try:
            manifest = render_pages.render(pdf_path(), out, [21], dpi=50)
            self.assertEqual(manifest["written"], ["p-021.png"])
            self.assertTrue(os.path.exists(os.path.join(out, "images", "p-021.png")))
            again = render_pages.render(pdf_path(), out, [21], dpi=50)
            self.assertEqual(again["written"], [])  # idempotent
        finally:
            shutil.rmtree(out, ignore_errors=True)


# --------------------------------------------------------------------------
# rows belong to ONE source
# --------------------------------------------------------------------------


class RowsForSourceTests(unittest.TestCase):
    """R4: a companion audited against two sources contributes to two differentials."""

    MIXED = [
        {"item": "Definition 1.1.1", "decl": "a", "source_id": "src-a"},
        {"item": "Definition 2.1.1", "decl": "b", "source_id": "src-b"},
        {"item": "Definition 3.1.1", "decl": "c"},           # stamped by nobody
    ]
    LEGACY = [{"item": "Definition 1.1.1", "decl": "a"}, {"item": "Lemma 1.1.2", "decl": "b"}]

    def test_a_mixed_table_is_split_strictly(self):
        a = compare_mod.rows_for_source(self.MIXED, "src-a")
        b = compare_mod.rows_for_source(self.MIXED, "src-b")
        self.assertEqual([r["decl"] for r in a], ["a"])
        self.assertEqual([r["decl"] for r in b], ["b"])
        # the unstamped row belongs to no known source and is in neither
        self.assertNotIn("c", [r["decl"] for r in a + b])

    def test_a_legacy_table_passes_through_whole(self):
        """Filtering it to nothing would be a silently empty differential."""
        rows = compare_mod.rows_for_source(self.LEGACY, "anything")
        self.assertEqual(len(rows), 2)

    def test_load_tables_filters_by_source(self):
        d = tmp_dir("cf-rows-")
        path = os.path.join(d, "statements.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for row in self.MIXED:
                fh.write(json.dumps(row) + "\n")
        self.assertEqual(len(compare_mod.load_tables({"x": path}, source_id="src-a")["x"]), 1)
        self.assertEqual(len(compare_mod.load_tables({"x": path})["x"]), 3)


# --------------------------------------------------------------------------
# pairing over the baseline statement tables
# --------------------------------------------------------------------------


@unittest.skipUnless(baseline_dir(), "set CF_BASELINE_DIFF or CF_BASELINE_CORPUS")
class CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = load_baseline_tables()
        cls.items_json = items_from_book_text() if book_text_path() else None
        cls.index = (
            {r["item"]: r for r in cls.items_json["items"]} if cls.items_json else None
        )
        cls.result = compare_mod.compare(
            cls.tables, tmp_dir("cf-pair-"), source_id=SOURCE_ID, source_sha=SOURCE_SHA,
            kinds=GRAMMAR_KINDS, book_items=cls.index, chapters=[1], write=False,
        )

    def test_225_pairs(self):
        self.assertEqual(len(self.result["pairs"]), 225)
        self.assertEqual(self.result["reconciliation"]["pairs"], 225)

    def test_every_pair_names_its_source(self):
        for row in self.result["pairs"]:
            self.assertEqual(row["source_id"], SOURCE_ID)
            self.assertEqual(row["source_sha"], SOURCE_SHA)
        self.assertEqual(self.result["reconciliation"]["source_id"], SOURCE_ID)
        self.assertEqual(self.result["reconciliation"]["source_sha"], SOURCE_SHA)

    def test_only_the_known_junk_label_is_rejected(self):
        rejected = self.result["rejected"]
        self.assertEqual(len(rejected), 1, [r["item_raw"] for r in rejected])
        self.assertIn("Corollary (Lemma 1.2.5", rejected[0]["item_raw"])
        self.assertEqual(rejected[0]["source_id"], SOURCE_ID)

    def test_exercise_1_3_19_pairs_ours_and_teorth(self):
        row = next(p for p in self.result["pairs"] if p["item"] == "Exercise 1.3.19")
        self.assertEqual(row["repos"], ["ours", "teorth"])

    @unittest.skipUnless(book_text_path(), "needs CF_BOOK_TXT")
    def test_reconciliation_reports_the_1_3_19_fix(self):
        """The task universe is 383; the baseline's 410-item book universe gave 382."""
        recon = self.result["reconciliation"]
        self.assertEqual(recon["items_in_scope"], 411)
        self.assertEqual(recon["task_universe"], 383)
        broken = {k: v for k, v in self.index.items() if k != "Exercise 1.3.19"}
        recon_410 = compare_mod.compare(
            self.tables, tmp_dir("cf-pair-"), source_id=SOURCE_ID, source_sha=SOURCE_SHA,
            kinds=GRAMMAR_KINDS, book_items=broken, chapters=[1], write=False,
        )["reconciliation"]
        self.assertEqual(recon_410["items_in_scope"], 410)
        self.assertEqual(recon_410["task_universe"], 382)

    @unittest.skipUnless(book_text_path(), "needs CF_BOOK_TXT")
    def test_tags_that_are_not_source_items_are_reported(self):
        recon = self.result["reconciliation"]
        self.assertIn("Definition 1.1.15", recon["items_not_in_book"])
        self.assertTrue(recon["items_out_of_scope"])  # Chapter-2 tags are in scope terms, not junk

    def test_writes_every_artifact(self):
        out = tmp_dir("cf-pair-write-")
        try:
            compare_mod.compare(
                self.tables, out, source_id=SOURCE_ID, source_sha=SOURCE_SHA,
                kinds=GRAMMAR_KINDS, book_items=self.index, chapters=[1],
            )
            for name in ("pairs.jsonl", "coverage.md", "reconciliation.json",
                         "rejected.json", "index.json"):
                self.assertTrue(os.path.exists(os.path.join(out, name)), name)
            with open(os.path.join(out, "pairs.jsonl"), encoding="utf-8") as fh:
                first = json.loads(fh.readline())
            self.assertEqual(first["_meta"]["source_sha"], SOURCE_SHA)
            self.assertEqual(first["_meta"]["source_id"], SOURCE_ID)
            with open(os.path.join(out, "coverage.md"), encoding="utf-8") as fh:
                self.assertIn(SOURCE_ID, fh.read())
        finally:
            shutil.rmtree(out, ignore_errors=True)


@unittest.skipUnless(baseline_dir() and book_text_path(), "needs the baseline and CF_BOOK_TXT")
class QueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = load_baseline_tables()
        cls.items_json = items_from_book_text()
        cls.index = {r["item"]: r for r in cls.items_json["items"]}
        cls.result = compare_mod.compare(
            cls.tables, tmp_dir("cf-q-"), source_id=SOURCE_ID, source_sha=SOURCE_SHA,
            kinds=GRAMMAR_KINDS, book_items=cls.index, chapters=[1], write=False,
        )
        cls.queue = make_queue.build_queue(
            cls.result["pairs"], source_id=SOURCE_ID, offset=16, book_items=cls.index,
            printed_max=249,
        )

    def test_queue_has_one_entry_per_pair(self):
        self.assertEqual(len(self.queue), 225)
        self.assertTrue(all(q["source_id"] == SOURCE_ID for q in self.queue))

    def test_page_hints_beat_the_baseline(self):
        """97 of 225 had hints when 'book p. N' anchors were dropped; 179 now."""
        with_hints = sum(1 for q in self.queue if q["pdf_pages"])
        self.assertEqual(with_hints, 179)
        self.assertGreater(with_hints, 97)

    def test_every_queue_item_carries_the_authoritative_page(self):
        missing = [q["item"] for q in self.queue if not q["book_pdf_page"]]
        self.assertEqual(missing, [])
        row = next(q for q in self.queue if q["item"] == "Exercise 1.3.19")
        self.assertEqual(row["book_pdf_page"], 86)
        self.assertEqual(row["repos"], ["ours", "teorth"])

    def test_targets_cover_in_text_items_and_reconcile_with_the_tags(self):
        """Attack runs address every tagged item; 763 tags = 762 keyed + 1 rejected."""
        all_index = dict(self.result["index"])
        all_index.update(self.result["intext"])
        targets = make_queue.build_targets(
            all_index, "ours", source_id=SOURCE_ID, repos=self.result["repos"], offset=16,
            book_items=self.index, kinds=GRAMMAR_KINDS, chapters=[1], printed_max=249,
        )
        raw = {lbl for t in targets for lbl in t["raw_labels"]}
        tagged = {r["item"] for r in self.tables["ours"]}
        rejected = {r["item_raw"] for r in self.result["rejected"] if r["repo"] == "ours"}
        self.assertEqual(len(tagged), 763)
        self.assertEqual(len(raw) + len(rejected), 763)
        self.assertTrue(all(t["source_id"] == SOURCE_ID for t in targets))
        in_text = [t for t in targets if t["item"].startswith("In-text")]
        self.assertTrue(in_text)
        self.assertTrue(all(t["priority"] == "low" for t in in_text))
        page_keyed = next(t for t in in_text if t["item"].startswith("In-text p."))
        self.assertEqual(page_keyed["book_pdf_page"], page_keyed["book_page"] + 16)

    def test_targets_split_single_source_from_shared(self):
        targets = make_queue.build_targets(
            self.result["index"], "ours", source_id=SOURCE_ID, repos=self.result["repos"],
            offset=16, book_items=self.index, kinds=GRAMMAR_KINDS, chapters=[1],
            printed_max=249,
        )
        shared = [t for t in targets if not t["single_source"]]
        single = [t for t in targets if t["single_source"]]
        self.assertEqual(len(shared) + len(single), len(targets))
        self.assertGreater(len(single), 0)
        self.assertTrue(all(t["priority"] in ("max", "high", "medium", "low") for t in targets))
        # a single-source Definition is the highest-value target
        self.assertTrue(any(t["priority"] == "max" for t in targets))

    def test_write_queue_stamps_the_source_on_both_artifacts(self):
        out = tmp_dir("cf-qw-")
        targets = make_queue.build_targets(
            self.result["index"], "ours", source_id=SOURCE_ID, repos=self.result["repos"],
            offset=16, book_items=self.index, kinds=GRAMMAR_KINDS, chapters=[1],
            printed_max=249,
        )
        make_queue.write_queue(out, self.queue, {"ours": targets},
                               source_id=SOURCE_ID, source_sha=SOURCE_SHA, offset=16)
        with open(os.path.join(out, "queue.json"), encoding="utf-8") as fh:
            queue = json.load(fh)
        with open(os.path.join(out, "targets.ours.json"), encoding="utf-8") as fh:
            tgt = json.load(fh)
        self.assertEqual(queue["source_id"], SOURCE_ID)
        self.assertEqual(queue["source_sha"], SOURCE_SHA)
        self.assertEqual(tgt["source_id"], SOURCE_ID)
        self.assertEqual(tgt["source_sha"], SOURCE_SHA)


@unittest.skipUnless(baseline_dir() and book_text_path(), "needs the baseline and CF_BOOK_TXT")
class DefCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items_json = items_from_book_text()
        cls.rows = load_baseline_tables(("ours",))["ours"]

    def test_reproduces_def_coverage_ours(self):
        cov = defcov.def_coverage(self.items_json, self.rows, source_id=SOURCE_ID,
                                  chapter=1, kinds=GRAMMAR_KINDS)
        self.assertEqual(len(cov["stated"]), 32)
        self.assertEqual(len(cov["bridge_only"]), 4)
        self.assertEqual(cov["missing"], [])
        self.assertEqual(cov["source_id"], SOURCE_ID)
        self.assertEqual(cov["source_sha"], SOURCE_SHA)
        baseline = os.path.join(baseline_dir(), "..", "def-coverage.ours.json")
        baseline = os.path.normpath(baseline)
        if os.path.exists(baseline):
            with open(baseline, encoding="utf-8") as fh:
                want = json.load(fh)
            self.assertEqual(cov["stated"], want["stated"])
            self.assertEqual(cov["bridge_only"], want["bridge_only"])

    def test_reproduces_uncovered_ours(self):
        unc = defcov.uncovered(self.items_json, self.rows, source_id=SOURCE_ID,
                               kinds=KINDS, chapter=1)
        self.assertEqual(unc["count"], 29)
        self.assertEqual(unc["by_kind"], {"Exercise": 1, "Remark": 28})
        self.assertIn("Exercise 1.6.18", unc["items"])
        self.assertEqual(unc["source_id"], SOURCE_ID)

    def test_the_source_id_defaults_to_the_items_json(self):
        cov = defcov.def_coverage(self.items_json, self.rows, chapter=1, kinds=GRAMMAR_KINDS)
        self.assertEqual(cov["source_id"], SOURCE_ID)


@unittest.skipUnless(baseline_dir() and book_text_path(), "needs the baseline and CF_BOOK_TXT")
class AnchorCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = {r["item"]: r for r in items_from_book_text()["items"]}
        cls.tables = load_baseline_tables()
        cls.result = anchor_check.check(
            cls.index, cls.tables, source_id=SOURCE_ID, source_sha=SOURCE_SHA, offset=16,
            run_id="test-anchor", kinds=GRAMMAR_KINDS,
        )

    def test_rows_are_mechanical_page_anchor_findings(self):
        for row in self.result["rows"]:
            self.assertEqual(row["kind"], "PAGE_ANCHOR")
            self.assertEqual(row["severity"], "low")
            self.assertTrue(row["mechanical"])
            self.assertEqual(row["source_id"], SOURCE_ID)
            self.assertEqual(row["source_sha"], SOURCE_SHA)
            self.assertNotIn("book_sha", row)
            self.assertIsNone(row["book_quote"])
            self.assertTrue(row["unfaithful_decls"])

    def test_attribution_table_anchors_match_the_published_edition(self):
        """adam re-anchored to the official book; the check must agree."""
        self.assertEqual(self.result["summary"]["repos"]["adam"]["mismatched_items"], 0)
        self.assertGreater(self.result["summary"]["repos"]["adam"]["ok"], 400)

    def test_finds_the_known_cross_reference_anchor_bug(self):
        """Exercise 1.2.17 took its page from 'previewed on book p. 20'."""
        rows = {r["item"]: r for r in self.result["rows"] if r["repo"] == "ours"}
        self.assertIn("Exercise 1.2.17", rows)
        self.assertLess(min(rows["Exercise 1.2.17"]["detail"]["deltas"]), -5)
        self.assertFalse(rows["Exercise 1.2.17"]["advisory"])

    def test_finding_ids_are_stable_and_content_addressed(self):
        row = self.result["rows"][0]
        self.assertEqual(
            row["finding_id"],
            finding_id("test-anchor", SOURCE_ID, row["item"], "PAGE_ANCHOR",
                       row["unfaithful_decls"]),
        )
        # the SOURCE is part of the address: the same defect against another
        # edition is another finding, which is the whole point of R4.
        self.assertNotEqual(
            row["finding_id"],
            finding_id("test-anchor", "tao-measure-2011-draft", row["item"], "PAGE_ANCHOR",
                       row["unfaithful_decls"]),
        )
        again = anchor_check.check(
            self.index, self.tables, source_id=SOURCE_ID, source_sha=SOURCE_SHA, offset=16,
            run_id="test-anchor", kinds=GRAMMAR_KINDS,
        )
        self.assertEqual([r["finding_id"] for r in again["rows"]],
                         [r["finding_id"] for r in self.result["rows"]])

    def test_synthetic_pdf_page_confusion_is_named(self):
        index = {"Exercise 1.1.1": {"item": "Exercise 1.1.1", "page": 10, "pdf_page": 26,
                                    "line": 1}}
        tables = {"x": [{"item": "Exercise 1.1.1", "decl": "foo", "page_anchor": "26"}]}
        res = anchor_check.check(index, tables, source_id=SOURCE_ID, source_sha=SOURCE_SHA,
                                 offset=16, run_id="r")
        self.assertEqual(len(res["rows"]), 1)
        self.assertEqual(res["rows"][0]["detail"]["pattern"], "pdf_page_not_printed_page")


class _Stub:
    """The one thing ``discovery._anchor_agreement`` asks of a workspace."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.paths = self

    def source_items(self, source_id: str):
        return self.root / source_id / "items.json"


class CheckEditionTests(unittest.TestCase):
    """S4.6: which edition a companion's anchors follow is decided by the PAGES."""

    DRAFT = {
        "source_id": "draft", "kinds": ["Exercise", "Definition"],
        "items": [
            {"item": "Exercise 1.1.1", "page": 10, "pdf_page": 26, "kind": "Exercise"},
            {"item": "Exercise 1.1.2", "page": 12, "pdf_page": 28, "kind": "Exercise"},
            {"item": "Definition 1.2.1", "page": 30, "pdf_page": 46, "kind": "Definition"},
        ],
    }
    PUBLISHED = {
        "source_id": "published", "kinds": ["Exercise", "Definition"],
        "items": [
            {"item": "Exercise 1.1.1", "page": 10, "pdf_page": 26, "kind": "Exercise"},
            {"item": "Exercise 1.1.2", "page": 12, "pdf_page": 28, "kind": "Exercise"},
            # the one-printed-page shift that separates the two editions
            {"item": "Definition 1.2.1", "page": 29, "pdf_page": 45, "kind": "Definition"},
        ],
    }
    ROWS = [
        {"item": "Exercise 1.1.1", "decl": "a", "page_anchor": "10"},
        {"item": "Exercise 1.1.2", "decl": "b", "page_anchor": "12"},
        {"item": "Definition 1.2.1", "decl": "c", "page_anchor": "book p. 29"},
    ]

    def table(self, rows=None):
        return anchor_check.check_edition(
            {"draft": self.DRAFT, "published": self.PUBLISHED}, rows or self.ROWS)

    def test_the_shifted_item_decides_the_edition(self):
        table = self.table()
        self.assertEqual(table["published"], {"ok": 3, "mismatched": 0, "agreement": 1.0})
        self.assertEqual(table["draft"]["ok"], 2)
        self.assertEqual(table["draft"]["mismatched"], 1)
        self.assertAlmostEqual(table["draft"]["agreement"], 0.6667, places=3)
        self.assertGreater(table["published"]["agreement"] - table["draft"]["agreement"], 0.05)

    def test_a_book_p_anchor_is_scored_not_dropped(self):
        """A bare int() read loses every 'book p. N' -- the whole adam convention."""
        only_prose = [r for r in self.ROWS if str(r["page_anchor"]).startswith("book p.")]
        table = self.table(only_prose)
        self.assertEqual(table["published"]["ok"] + table["published"]["mismatched"], 1)

    def test_rows_with_nothing_to_score_leave_the_table_empty(self):
        table = self.table([{"item": "Exercise 9.9.9", "decl": "z", "page_anchor": "1"},
                            {"item": "Exercise 1.1.1", "decl": "y"}])
        self.assertEqual(table["published"], {"ok": 0, "mismatched": 0, "agreement": 0.0})

    def test_discovery_scores_editions_through_this_module(self):
        """S4.6 is decided HERE; ``cflib.discovery`` only ranks the table.

        Verified against the real data on 2026-09-16: over tao-measure-lean's
        1,464 extracted rows the published edition scores 271 ok / 35 mismatched
        (0.886) and the 2011 draft 202 / 103 (0.662), and the inline copy
        ``cflib.discovery`` still keeps agrees digit for digit -- so that copy
        can go.
        """
        from cflib import discovery

        books = {"draft": self.DRAFT, "published": self.PUBLISHED}
        want = anchor_check.check_edition(books, self.ROWS)

        fallback = getattr(discovery, "_check_edition_fallback", None)
        if fallback is not None:
            # the inline copy is scored on the anchors it can read: a bare
            # `int(anchor)` drops "book p. N", which this module keeps.
            plain = [r for r in self.ROWS if str(r["page_anchor"]).isdigit()]
            self.assertEqual(anchor_check.check_edition(books, plain), fallback(books, plain))

        out = pathlib.Path(tmp_dir("cf-editions-"))
        for source_id, items_json in books.items():
            (out / source_id).mkdir(parents=True, exist_ok=True)
            (out / source_id / "items.json").write_text(json.dumps(items_json), encoding="utf-8")
        self.assertEqual(
            discovery._anchor_agreement(list(books), self.ROWS, workspace=_Stub(out)), want)


# --------------------------------------------------------------------------
# the command, over a workspace with TWO sources
# --------------------------------------------------------------------------


@unittest.skipUnless(
    baseline_dir() and pdf_path() and shutil.which("pdftotext"),
    "needs the baseline, CF_MT and pdftotext",
)
class CommandTests(unittest.TestCase):
    """``cf pair`` over a workspace holding two sources and five companions.

    Source A is the real pinned Tao edition audited by ours/teorth/adam; source B
    is a two-item stand-in audited by two other companions.  The point of the
    fixture is that ONE command produces TWO trees and never mixes them (R1).
    """

    SECOND_ID = "demo-notes-1e"
    SECOND_SHA = "b" * 64

    @classmethod
    def setUpClass(cls):
        import cf as cf_main

        from cflib.workspace import Workspace

        cls.cf = cf_main
        cls.root = tmp_dir("cf-workspace-")
        shutil.rmtree(cls.root, ignore_errors=True)
        assert cf_main.main(["init", cls.root, "--no-git", "-q"]) == 0

        # --- source A: the real edition, prepared from the pinned PDF --------
        ws_paths = Workspace.load(cls.root).paths
        payload = prepare_source.prepare(
            pdf_path(), str(ws_paths.source_dir(SOURCE_ID)), source_id=SOURCE_ID,
            expect_sha=SOURCE_SHA, write_pages=False,
        )
        cls.prepared = payload

        # --- source B: a hand-written stand-in -------------------------------
        cls._write_second_source(ws_paths.source_dir(cls.SECOND_ID))

        # --- companions -------------------------------------------------------
        for name in ("ours", "teorth", "adam"):
            os.makedirs(os.path.join(cls.root, "companions", name), exist_ok=True)
            shutil.copy(
                os.path.join(baseline_dir(), "%s.jsonl" % name),
                os.path.join(cls.root, "companions", name, "statements.jsonl"),
            )
        for name in ("notes-a", "notes-b"):
            os.makedirs(os.path.join(cls.root, "companions", name), exist_ok=True)
            with open(os.path.join(cls.root, "companions", name, "statements.jsonl"),
                      "w", encoding="utf-8") as fh:
                for row in cls._second_rows(name):
                    fh.write(json.dumps(row) + "\n")

        ws = Workspace.load(cls.root)
        ws.upsert_source(
            SOURCE_ID, title="An Introduction to Measure Theory",
            authors=["Terence Tao"], edition="AMS GSM 126 (published)",
            sha256=SOURCE_SHA, offset=payload["offset"],
            offset_confidence=payload["offset_confidence"],
            offset_method=payload["offset_method"], pages=payload["pages"],
            kinds=list(payload["kinds"]), chapters=[1], role="audited", confirmed=True)
        ws.upsert_source(
            cls.SECOND_ID, title="Notes on Something Else", authors=["A N Other"],
            edition="1st", sha256=cls.SECOND_SHA, offset=0, offset_confidence=1.0,
            offset_method="manual", pages=40, kinds=["Definition", "Lemma"],
            chapters=[1], role="audited", confirmed=False)
        for name in ("ours", "teorth", "adam"):
            ws.upsert_companion(name, repo=baseline_dir(), adapter="docstring_tags",
                                sources=[SOURCE_ID], anchor_source=SOURCE_ID)
        for name in ("notes-a", "notes-b"):
            ws.upsert_companion(name, repo=baseline_dir(), adapter="docstring_tags",
                                sources=[cls.SECOND_ID], anchor_source=cls.SECOND_ID)
        ws.save()
        cls.problems = Workspace.load(cls.root).validate()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def _write_second_source(cls, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        items = [
            {"item": "Definition 1.1.1", "kind": "Definition", "number": "1.1.1",
             "chapter": 1, "section": 1, "index": 1, "page": 3, "pdf_page": 3, "line": 40,
             "sub": "", "title": "", "ambiguous": False, "occurrences": []},
            {"item": "Lemma 1.1.2", "kind": "Lemma", "number": "1.1.2",
             "chapter": 1, "section": 1, "index": 2, "page": 4, "pdf_page": 4, "line": 80,
             "sub": "", "title": "", "ambiguous": False, "occurrences": []},
        ]
        (out_dir / "items.json").write_text(json.dumps({
            "source_id": cls.SECOND_ID, "source_sha": cls.SECOND_SHA, "offset": 0,
            "kinds": ["Definition", "Lemma"], "count": len(items),
            "by_kind": {"Definition": 1, "Lemma": 1}, "by_chapter": {"1": 2},
            "ambiguous": [], "created": "2026-09-16T00:00:00Z", "items": items,
        }), encoding="utf-8")
        (out_dir / "meta.json").write_text(json.dumps({
            "source_id": cls.SECOND_ID, "sha256": cls.SECOND_SHA, "edition": "1st",
            "pages": 40, "offset": 0, "offset_confidence": 1.0, "offset_method": "manual",
            "kinds": ["Definition", "Lemma"], "items": len(items),
        }), encoding="utf-8")
        (out_dir / "text.txt").write_text("Definition 1.1.1. A thing.\nLemma 1.1.2. Another.\n",
                                          encoding="utf-8")

    @classmethod
    def _second_rows(cls, name):
        rows = [
            {"item": "Definition 1.1.1", "item_raw": "Definition 1.1.1", "decl": "%s.d1" % name,
             "file": "X.lean", "line": 1, "statement": "def d1 : Nat := 0",
             "kind": "def", "page_anchor": "3",
             "source_id": cls.SECOND_ID, "source_sha": cls.SECOND_SHA},
            {"item": "Lemma 1.1.2", "item_raw": "Lemma 1.1.2", "decl": "%s.l2" % name,
             "file": "X.lean", "line": 9, "statement": "theorem l2 : True",
             "kind": "theorem", "page_anchor": "4",
             "source_id": cls.SECOND_ID, "source_sha": cls.SECOND_SHA},
        ]
        if name == "notes-b":  # one single-source item, so the two are not identical
            rows.append({"item": "Definition 1.9.9", "item_raw": "Definition 1.9.9",
                         "decl": "notes-b.d9", "file": "Y.lean", "line": 3,
                         "statement": "def d9 : Nat := 1", "kind": "def",
                         "source_id": cls.SECOND_ID, "source_sha": cls.SECOND_SHA})
        return rows

    def _json(self, *parts):
        with open(os.path.join(self.root, *parts), encoding="utf-8") as fh:
            return json.load(fh)

    def _run(self, argv):
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = self.cf.main(list(argv))
        return rc, out.getvalue()

    def test_the_fixture_workspace_is_valid(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(self.prepared["offset"], 16)   # inferred, not typed

    def test_pair_writes_one_tree_per_source(self):
        rc, _ = self._run(["pair", "--workspace", self.root])
        self.assertEqual(rc, 0)

        queue = self._json("pairs", SOURCE_ID, "queue.json")
        self.assertEqual(queue["count"], 225)
        self.assertEqual(queue["with_page_hints"], 179)
        self.assertEqual(queue["source_id"], SOURCE_ID)
        self.assertEqual(queue["source_sha"], SOURCE_SHA)

        second = self._json("pairs", self.SECOND_ID, "queue.json")
        self.assertEqual(second["count"], 2)            # both items are shared
        self.assertEqual(second["source_id"], self.SECOND_ID)
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "pairs", self.SECOND_ID, "targets.notes-b.json")))

        # the two trees never see each other's items
        first = self._json("pairs", SOURCE_ID, "queue.json")["items"]
        self.assertNotIn("Definition 1.9.9", [q["item"] for q in first])
        recon = self._json("pairs", self.SECOND_ID, "reconciliation.json")
        self.assertEqual(recon["repos"], ["notes-a", "notes-b"])
        self.assertEqual(recon["items_not_in_book"], ["Definition 1.9.9"])

    def test_anchor_rows_and_coverage_reproduce_the_baseline(self):
        self.assertEqual(self._run(["pair", "--workspace", self.root])[0], 0)
        anchors = self._json("pairs", SOURCE_ID, "anchor-check.json")
        self.assertEqual(anchors["summary"]["repos"]["adam"]["mismatched_items"], 0)
        self.assertEqual(anchors["summary"]["source_id"], SOURCE_ID)
        self.assertEqual(anchors["summary"]["run_id"], "anchor-check-%s" % SOURCE_ID)
        self.assertTrue(os.path.isdir(
            os.path.join(self.root, "corpus", "anchor-check-%s" % SOURCE_ID)))

        cov = self._json("pairs", SOURCE_ID, "def-coverage.ours.json")
        self.assertEqual(len(cov["stated"]), 32)
        self.assertEqual(len(cov["bridge_only"]), 4)
        self.assertEqual(cov["missing"], [])

    def test_state_records_the_counts_per_source(self):
        self.assertEqual(self._run(["pair", "--workspace", self.root])[0], 0)
        state = self._json("companions", "ours", "state.json")
        row = state["sources"][SOURCE_ID]
        self.assertEqual(row["sha"], SOURCE_SHA)
        self.assertEqual(row["def_bridge_only"], 4)
        self.assertEqual(row["uncovered"], 29)
        self.assertEqual(state["coverage"]["sources"], [SOURCE_ID])
        self.assertEqual(state["coverage"]["by_source"][SOURCE_ID]["pairs"], 225)
        self.assertNotIn(self.SECOND_ID, state["sources"])

    def test_one_source_can_be_paired_alone(self):
        rc, _ = self._run(["pair", "--workspace", self.root, "--source", self.SECOND_ID])
        self.assertEqual(rc, 0)
        self.assertEqual(self._json("pairs", self.SECOND_ID, "queue.json")["count"], 2)

    def test_an_unknown_source_is_refused_by_name(self):
        from cflib.errors import CfError

        with self.assertRaises(CfError) as ctx:
            self._run(["pair", "--workspace", self.root, "--source", "no-such-source"])
        self.assertIn("no-such-source", str(ctx.exception))

    def test_a_second_pass_reproduces_every_row(self):
        """Only the ``created`` stamp may move; a pair that changes is a defect."""
        path = pathlib.Path(self.root, "pairs", SOURCE_ID, "pairs.jsonl")

        def rows():
            return [ln for ln in path.read_text(encoding="utf-8").splitlines()
                    if not ln.startswith('{"_meta"')]

        self.assertEqual(self._run(["pair", "--workspace", self.root])[0], 0)
        before = rows()
        self.assertEqual(self._run(["pair", "--workspace", self.root])[0], 0)
        self.assertEqual(before, rows())
        self.assertEqual(len(before), 225)

    def test_the_source_gate_refuses_another_edition(self):
        """workspace.yaml pins the bytes; items.json built from others is refused."""
        from cflib.errors import CfError
        from cflib.workspace import Workspace

        ws = Workspace.load(self.root)
        ws.upsert_source(self.SECOND_ID, sha256="c" * 64)
        ws.save()
        try:
            with self.assertRaises(CfError) as ctx:
                self._run(["pair", "--workspace", self.root, "--source", self.SECOND_ID])
            self.assertIn("source_sha", str(ctx.exception))
        finally:
            ws.upsert_source(self.SECOND_ID, sha256=self.SECOND_SHA)
            ws.save()


class TestDefCoverageIsOneImplementation(unittest.TestCase):
    """`cf pair` and `cf detect-proxies` write the same file; they must agree."""

    def test_the_keyword_set_has_one_definition(self):
        from cflib import vocab
        from detect import def_coverage as detect_cov
        from pair import def_coverage as pair_cov

        self.assertEqual(tuple(pair_cov.DEF_KEYWORDS), tuple(vocab.DEFINITIONAL_KEYWORDS))
        self.assertEqual(tuple(detect_cov.DEFINITIONAL_KEYWORDS),
                         tuple(vocab.DEFINITIONAL_KEYWORDS))
        self.assertIn("instance", pair_cov.DEF_KEYWORDS)   # the one that differed

    def test_an_adapter_kind_counts_as_definitional_in_both(self):
        from detect import def_coverage as detect_cov
        from pair import def_coverage as pair_cov

        row = {"kind": "instance", "statement": "(truncated)"}
        pattern = pair_cov.def_regex()
        self.assertTrue(pair_cov._is_definitional(row, pattern))
        self.assertTrue(detect_cov.is_definitional(row))
        theorem = {"kind": "theorem", "statement": "theorem foo : True"}
        self.assertFalse(pair_cov._is_definitional(theorem, pattern))
        self.assertFalse(detect_cov.is_definitional(theorem))

    def test_pair_preserves_the_keys_detect_owns(self):
        """Re-running cf pair used to delete 26 real proxy candidates."""
        from pair import def_coverage as pair_cov

        out = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        detector_file = out / "def-coverage.adam.json"
        detector_file.write_text(json.dumps({
            "stated": ["Definition 1.1.1"], "bridge_only": [], "missing": [],
            "keywords": ["def", "instance"],
            "proxy_candidates": [{"item": "Definition 1.3.12", "decl": "Tao.upperIntegral"}],
            "bridge_only_detail": {"Definition 1.2.2": ["X"]},
            "_meta": {"generated_by": "cf detect-proxies (cheap tier)"},
            "written_by": "cf detect-proxies",
        }), encoding="utf-8")

        pair_cov.write_coverage(str(out), "adam",
                                {"stated": ["Definition 1.1.1"], "bridge_only": [], "missing": [],
                                 "keywords": list(pair_cov.DEF_KEYWORDS)},
                                {"count": 0, "items": []})
        after = json.loads(detector_file.read_text())
        self.assertEqual(len(after["proxy_candidates"]), 1)
        self.assertIn("_meta", after)
        self.assertEqual(after["bridge_only_detail"], {"Definition 1.2.2": ["X"]})
        self.assertEqual(after["written_by"], "cf pair")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
