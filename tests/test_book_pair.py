"""Regression tests for the book and pair stages.

These pin the numbers the audit actually produced, so a refactor that changes
them is visible:

* the pinned Tao edition yields **411** Chapter-1 items and **Exercise 1.3.19**
  is one of them (the baseline's single-space regex found 410 and the task
  generator silently dropped it);
* the three baseline statement tables pair into **225** items;
* ``def-coverage.ours.json`` is reproduced exactly: 32 stated / 4 bridge-only /
  0 missing, and 29 uncovered numbered items.

Everything reads the local baseline through environment variables and SKIPS
cleanly when they are unset:

    CF_BOOK_TXT       = ~/Desktop/tao-differential/book.official.txt
    CF_BASELINE_DIFF  = ~/Desktop/tao-differential        (ours/teorth/adam.jsonl)
    CF_BASELINE_CORPUS= ~/Desktop/tao-diff-run            (data/*.jsonl)
    CF_MT             = ~/Desktop/measure_theory          (the pinned PDF)
    CF_TEST_TMP       = directory for scratch output (default: a temp dir)

Run:  python3 -m unittest tests.test_book_pair -v
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

from book import prepare_book  # noqa: E402
from book.compat import finding_id, parse_item, parse_item_reject, parse_pages  # noqa: E402
from book.fetch_errata import html_to_markdown, stable_text  # noqa: E402
from pair import anchor_check, compare as compare_mod, def_coverage as defcov, make_queue  # noqa: E402

BOOK_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
PDF_REL = ".lake/official-pdf/gsm-126-tao5-measure-book.pdf"
KINDS = prepare_book.BOOK_KINDS
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
    campaign directory they accumulated *inside the campaign*.
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


def items_from_book_text() -> dict:
    with open(book_text_path(), encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    items = prepare_book.find_items(text, KINDS, offset=16)
    return {
        "book_id": "tao-measure", "book_sha": BOOK_SHA, "offset": 16,
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
# book items
# --------------------------------------------------------------------------


@unittest.skipUnless(book_text_path(), "set CF_BOOK_TXT to the pinned edition's text")
class BookItemsTests(unittest.TestCase):
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
class PrepareBookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tmp_dir("cf-book-")
        cls.payload = prepare_book.prepare(
            pdf_path(), cls.out, book_id="tao-measure", offset=16, kinds=KINDS,
            edition="GSM 126", expect_sha=BOOK_SHA,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_book_json_pins_the_edition(self):
        self.assertEqual(self.payload["sha256"], BOOK_SHA)
        self.assertEqual(self.payload["pages"], 265)
        self.assertEqual(self.payload["offset"], 16)
        self.assertEqual(self.payload["items"], 442)

    def test_pages_and_items_written(self):
        self.assertTrue(os.path.exists(os.path.join(self.out, "pages", "p001.txt")))
        self.assertEqual(len(os.listdir(os.path.join(self.out, "pages"))), 265)
        items = prepare_book.load_items(self.out)
        self.assertEqual(items["book_sha"], BOOK_SHA)
        self.assertEqual(len([r for r in items["items"] if r["chapter"] == 1]), 411)

    def test_second_run_is_a_no_op(self):
        again = prepare_book.prepare(
            pdf_path(), self.out, book_id="tao-measure", offset=16, kinds=KINDS,
            expect_sha=BOOK_SHA,
        )
        self.assertTrue(again["skipped"])

    def test_wrong_edition_is_refused(self):
        with self.assertRaises(ValueError):
            prepare_book.prepare(
                pdf_path(), self.out, book_id="tao-measure", offset=16, kinds=KINDS,
                expect_sha="deadbeef", force=True,
            )


@unittest.skipUnless(pdf_path() and shutil.which("pdftoppm"), "needs CF_MT and pdftoppm")
class RenderPagesTests(unittest.TestCase):
    def test_one_page_renders_to_a_stable_name(self):
        from book import render_pages

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
            cls.tables, tmp_dir("cf-pair-"), kinds=GRAMMAR_KINDS,
            book_items=cls.index, book_sha=BOOK_SHA, chapters=[1], write=False,
        )

    def test_225_pairs(self):
        self.assertEqual(len(self.result["pairs"]), 225)
        self.assertEqual(self.result["reconciliation"]["pairs"], 225)

    def test_only_the_known_junk_label_is_rejected(self):
        rejected = self.result["rejected"]
        self.assertEqual(len(rejected), 1, [r["item_raw"] for r in rejected])
        self.assertIn("Corollary (Lemma 1.2.5", rejected[0]["item_raw"])

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
            self.tables, tmp_dir("cf-pair-"), kinds=GRAMMAR_KINDS, book_items=broken,
            book_sha=BOOK_SHA, chapters=[1], write=False,
        )["reconciliation"]
        self.assertEqual(recon_410["items_in_scope"], 410)
        self.assertEqual(recon_410["task_universe"], 382)

    @unittest.skipUnless(book_text_path(), "needs CF_BOOK_TXT")
    def test_tags_that_are_not_book_items_are_reported(self):
        recon = self.result["reconciliation"]
        self.assertIn("Definition 1.1.15", recon["items_not_in_book"])
        self.assertTrue(recon["items_out_of_scope"])  # Chapter-2 tags are in scope terms, not junk

    def test_writes_every_artifact(self):
        out = tmp_dir("cf-pair-write-")
        try:
            compare_mod.compare(
                self.tables, out, kinds=GRAMMAR_KINDS, book_items=self.index,
                book_sha=BOOK_SHA, chapters=[1],
            )
            for name in ("pairs.jsonl", "coverage.md", "reconciliation.json",
                         "rejected.json", "index.json"):
                self.assertTrue(os.path.exists(os.path.join(out, name)), name)
            with open(os.path.join(out, "pairs.jsonl"), encoding="utf-8") as fh:
                first = json.loads(fh.readline())
            self.assertEqual(first["_meta"]["book_sha"], BOOK_SHA)
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
            cls.tables, tmp_dir("cf-q-"), kinds=GRAMMAR_KINDS, book_items=cls.index,
            book_sha=BOOK_SHA, chapters=[1], write=False,
        )
        cls.queue = make_queue.build_queue(
            cls.result["pairs"], offset=16, book_items=cls.index, printed_max=249
        )

    def test_queue_has_one_entry_per_pair(self):
        self.assertEqual(len(self.queue), 225)

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
            all_index, "ours", repos=self.result["repos"], offset=16,
            book_items=self.index, kinds=GRAMMAR_KINDS, printed_max=249,
        )
        raw = {lbl for t in targets for lbl in t["raw_labels"]}
        tagged = {r["item"] for r in self.tables["ours"]}
        rejected = {r["item_raw"] for r in self.result["rejected"] if r["repo"] == "ours"}
        self.assertEqual(len(tagged), 763)
        self.assertEqual(len(raw) + len(rejected), 763)
        in_text = [t for t in targets if t["item"].startswith("In-text")]
        self.assertTrue(in_text)
        self.assertTrue(all(t["priority"] == "low" for t in in_text))
        page_keyed = next(t for t in in_text if t["item"].startswith("In-text p."))
        self.assertEqual(page_keyed["book_pdf_page"], page_keyed["book_page"] + 16)

    def test_targets_split_single_source_from_shared(self):
        targets = make_queue.build_targets(
            self.result["index"], "ours", repos=self.result["repos"], offset=16,
            book_items=self.index, kinds=GRAMMAR_KINDS, printed_max=249,
        )
        shared = [t for t in targets if not t["single_source"]]
        single = [t for t in targets if t["single_source"]]
        self.assertEqual(len(shared) + len(single), len(targets))
        self.assertGreater(len(single), 0)
        self.assertTrue(all(t["priority"] in ("max", "high", "medium", "low") for t in targets))
        # a single-source Definition is the highest-value target
        self.assertTrue(any(t["priority"] == "max" for t in targets))


@unittest.skipUnless(baseline_dir() and book_text_path(), "needs the baseline and CF_BOOK_TXT")
class DefCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items_json = items_from_book_text()
        cls.rows = load_baseline_tables(("ours",))["ours"]

    def test_reproduces_def_coverage_ours(self):
        cov = defcov.def_coverage(self.items_json, self.rows, chapter=1, kinds=GRAMMAR_KINDS)
        self.assertEqual(len(cov["stated"]), 32)
        self.assertEqual(len(cov["bridge_only"]), 4)
        self.assertEqual(cov["missing"], [])
        baseline = os.path.join(baseline_dir(), "..", "def-coverage.ours.json")
        baseline = os.path.normpath(baseline)
        if os.path.exists(baseline):
            with open(baseline, encoding="utf-8") as fh:
                want = json.load(fh)
            self.assertEqual(cov["stated"], want["stated"])
            self.assertEqual(cov["bridge_only"], want["bridge_only"])

    def test_reproduces_uncovered_ours(self):
        unc = defcov.uncovered(self.items_json, self.rows, kinds=KINDS, chapter=1)
        self.assertEqual(unc["count"], 29)
        self.assertEqual(unc["by_kind"], {"Exercise": 1, "Remark": 28})
        self.assertIn("Exercise 1.6.18", unc["items"])


@unittest.skipUnless(baseline_dir() and book_text_path(), "needs the baseline and CF_BOOK_TXT")
class AnchorCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = {r["item"]: r for r in items_from_book_text()["items"]}
        cls.tables = load_baseline_tables()
        cls.result = anchor_check.check(
            cls.index, cls.tables, book_sha=BOOK_SHA, offset=16,
            run_id="test-anchor", kinds=GRAMMAR_KINDS,
        )

    def test_rows_are_mechanical_page_anchor_findings(self):
        for row in self.result["rows"]:
            self.assertEqual(row["kind"], "PAGE_ANCHOR")
            self.assertEqual(row["severity"], "low")
            self.assertTrue(row["mechanical"])
            self.assertEqual(row["book_sha"], BOOK_SHA)
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
            finding_id("test-anchor", row["item"], "PAGE_ANCHOR", row["unfaithful_decls"]),
        )
        # a different run id gives a different id; the same inputs give the same one
        again = anchor_check.check(
            self.index, self.tables, book_sha=BOOK_SHA, offset=16,
            run_id="test-anchor", kinds=GRAMMAR_KINDS,
        )
        self.assertEqual([r["finding_id"] for r in again["rows"]],
                         [r["finding_id"] for r in self.result["rows"]])

    def test_synthetic_pdf_page_confusion_is_named(self):
        index = {"Exercise 1.1.1": {"item": "Exercise 1.1.1", "page": 10, "pdf_page": 26, "line": 1}}
        tables = {"x": [{"item": "Exercise 1.1.1", "decl": "foo", "page_anchor": "26"}]}
        res = anchor_check.check(index, tables, book_sha=BOOK_SHA, offset=16, run_id="r")
        self.assertEqual(len(res["rows"]), 1)
        self.assertEqual(res["rows"][0]["detail"]["pattern"], "pdf_page_not_printed_page")


@unittest.skipUnless(
    baseline_dir() and pdf_path() and shutil.which("pdftotext"),
    "needs the baseline, CF_MT and pdftotext",
)
class CommandTests(unittest.TestCase):
    """`cf book` then `cf pair` over a throw-away campaign directory."""

    @classmethod
    def setUpClass(cls):
        import argparse

        from cfcmd import book as book_cmd, pair as pair_cmd

        cls.book_cmd, cls.pair_cmd = book_cmd, pair_cmd
        cls.root = tmp_dir("cf-campaign-")
        for name in ("ours", "teorth", "adam"):
            os.makedirs(os.path.join(cls.root, "companions", name), exist_ok=True)
            shutil.copy(
                os.path.join(baseline_dir(), "%s.jsonl" % name),
                os.path.join(cls.root, "companions", name, "statements.jsonl"),
            )
        with open(os.path.join(cls.root, "campaign.yaml"), "w", encoding="utf-8") as fh:
            fh.write(
                "book:\n"
                "  id: tao-measure\n"
                "  pdf: %s\n"
                "  sha256: %s\n"
                "  offset: 16\n"
                "  chapters: [1]\n"
                "  kinds: [%s]\n"
                "companions:\n" % (pdf_path(), BOOK_SHA, ", ".join(GRAMMAR_KINDS))
                + "".join("  - name: %s\n    adapter: docstring_tags\n" % n
                          for n in ("ours", "teorth", "adam"))
            )
        cls.parser = argparse.ArgumentParser()
        sub = cls.parser.add_subparsers(dest="command")
        book_cmd.register(sub)
        pair_cmd.register(sub)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _run(self, argv):
        """Run a cf command, swallowing its human output."""
        import contextlib
        import io

        args = self.parser.parse_args(argv)
        with contextlib.redirect_stdout(io.StringIO()):
            return args.func(args)

    def test_book_then_pair(self):
        self.assertEqual(
            self._run(["book", "tao-measure", "--campaign", self.root, "--no-errata", "--no-pages"]), 0
        )
        self.assertEqual(self._run(["pair", "--campaign", self.root]), 0)
        pairs = os.path.join(self.root, "pairs")
        with open(os.path.join(pairs, "queue.json"), encoding="utf-8") as fh:
            queue = json.load(fh)
        self.assertEqual(queue["count"], 225)
        self.assertEqual(queue["with_page_hints"], 179)
        with open(os.path.join(pairs, "anchor-check.json"), encoding="utf-8") as fh:
            anchors = json.load(fh)
        self.assertEqual(anchors["summary"]["repos"]["adam"]["mismatched_items"], 0)
        with open(os.path.join(self.root, "companions", "ours", "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(state["coverage"]["def_bridge_only"], 4)
        self.assertEqual(state["coverage"]["uncovered"], 29)
        self.assertEqual(state["book"]["items"], 442)
        # idempotence: a second pass changes nothing it did not have to
        self.assertEqual(self._run(["pair", "--campaign", self.root]), 0)


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
