"""Regression tests for the item grammar and the source model (w2).

Every case here failed before the change it guards:

* generality#1 -- the grammar demanded two or three numeric segments and
  matched the FIRST three of anything deeper, so ``Theorem 2.3.4.5`` parsed as
  ``Theorem 2.3.4`` with ``.5`` in ``trailing`` and nineteen distinct theorems
  collapsed onto one audit key with nothing on any reject list.  A source's
  numbering is now read off its own headings, a deeper label is a reject, and
  ``Theorem 7`` / ``Lemma 2.3.4.1`` are readable numbering rather than junk.
  ``infer_kinds`` was case-sensitive, so an AMS-style ``THEOREM 1.2`` head was
  invisible.
* generality#4 -- the only wrong-document guard was ``items == 0``.  A 404-page
  book whose numbering puts the kind after the number prepared fifteen items --
  a fifth of the slots its own sections number -- and was adopted in silence,
  with ``kind_counts`` reading ``Exercise: 16, Now: 5, Suppose: 4`` on disk.
* generality#6 -- ``prepare`` ran ``pdftotext`` unconditionally, so a
  manuscript that was already text (an arXiv ``.tex``, notes in Markdown, a
  ``pdftotext`` dump) had no path in at all, and there was no way to say "this
  source is located by section, not by printed page".

The baseline-backed cases prove the invariance the frozen contract needs on the
real corpus and SKIP cleanly without it:

    CF_BASELINE_CORPUS  the 2,672-finding v1 corpus
    CF_BOOK_TXT         the pinned edition's extracted text

Run:  python3 -m unittest tests.test_rf_items_sources -v
"""
from __future__ import annotations

# The first import, before anything reads HOME: it pins HOME at a temporary
# directory for `unittest discover`, which loads no conftest.py.  The second
# spelling is for `python3 -m unittest tests.<module>`.  See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                                   # noqa: E402
from cflib import inference, items as cf_items                         # noqa: E402
from cflib.errors import CfError, ItemGrammarError, SourceError        # noqa: E402
from cflib.workspace import Workspace                                  # noqa: E402
from cfcmd.onboard import _refuse_an_empty_source                      # noqa: E402
from cfcmd.sources import LIST_COLUMNS                                 # noqa: E402
from sources import compat, locator, prepare_source                    # noqa: E402

KINDS = ("Definition", "Lemma", "Theorem", "Proposition", "Corollary",
         "Exercise", "Example", "Remark", "Satz")


def _env(name: str):
    value = os.environ.get(name)
    return value if value and os.path.exists(value) else None


CORPUS = _env("CF_BASELINE_CORPUS")
BOOK_TXT = _env("CF_BOOK_TXT")


def _quiet(fn, *args, **kwargs):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


def _tmp(case: unittest.TestCase, prefix: str) -> Path:
    d = tempfile.mkdtemp(prefix=prefix)
    case.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
    return Path(d)


def _synthetic(fmt: str, count: int = 19, kind: str = "Theorem") -> str:
    """``count`` items numbered ``fmt % n``, each with a statement and a proof."""
    return "\n\n".join((fmt % n) + " a statement.\n\nProof. Something.\n"
                       for n in range(1, count + 1)) + "\n"


# ---------------------------------------------------------------------------
# generality#1 -- one numbering per source, and a deeper label is a reject
# ---------------------------------------------------------------------------


class TestADeeperLabelIsRefusedNotTruncated(unittest.TestCase):
    """The silent merge: the worst failure this tool had."""

    def test_try_parse_rejects_a_four_segment_label_under_a_three_segment_numbering(self):
        item, reject = cf_items.try_parse("Theorem 2.3.4.5", KINDS)
        self.assertIsNone(item, "a four-segment label must not parse as a three-segment one")
        self.assertEqual(reject.reason, cf_items.REJECT_TOO_MANY_SEGMENTS)
        self.assertEqual(reject.context["number"], "2.3.4.5")
        self.assertEqual(reject.context["max_segments"], 3)

    def test_the_truncation_that_used_to_happen_cannot_happen_again(self):
        """Before: id ``Theorem 2.3.4``, trailing ``.5``, reject None."""
        for raw in ("Theorem 2.3.4.5", "Lemma 1.2.3.4", "Exercise 1.2.3.4.5"):
            item, reject = cf_items.try_parse(raw, KINDS)
            self.assertIsNone(item, raw)
            self.assertIsNotNone(reject, raw)
            self.assertNotIn(".", (item.trailing if item else ""))

    def test_nineteen_deep_headings_are_nineteen_rejects_not_one_item(self):
        text = _synthetic("Theorem 2.3.4.%d")
        rows, rejects = prepare_source.find_headings(text, KINDS)
        self.assertEqual(rows, [], "before the fix this was ONE row with 19 occurrences")
        self.assertEqual(len(rejects), 19)
        self.assertEqual({r["reason"] for r in rejects}, {"too_many_segments"})
        self.assertEqual(rejects[0]["number"], "2.3.4.1")

    def test_prepare_reports_deep_headings_as_a_warning_and_in_items_json(self):
        """A book numbered ``1.2.3`` with a few deeper labels: the real shape."""
        out = _tmp(self, "cf-deep-")
        src = out / "deep.md"
        src.write_text("# Ch\n\n" + _synthetic("Theorem 2.3.%d", 40)
                       + _synthetic("Theorem 2.3.4.%d", 2), encoding="utf-8")
        payload = prepare_source.prepare(str(src), str(out / "prepared"), source_id="deep")
        self.assertEqual(payload["numbering"]["max_segments"], 3)
        self.assertEqual(payload["items"], 40)
        self.assertEqual(len(payload["item_rejects"]), 2)
        warning = next(w for w in payload["warnings"]
                       if w.startswith("headings_deeper_than_the_numbering: 2"))
        # a dropped heading names the ONE thing that can bring it back: refresh
        # would re-infer the same depth off the same text.
        self.assertIn("cf sources set deep numbering=3,4", warning)
        written = json.loads((out / "prepared" / "items.json").read_text(encoding="utf-8"))
        self.assertEqual([r["number"] for r in written["rejects"]], ["2.3.4.1", "2.3.4.2"])
        # the merge that used to happen: all five folded onto the ONE key 2.3.4
        self.assertEqual([r["item"] for r in written["items"] if r["number"] == "2.3.4"],
                         ["Theorem 2.3.4"])
        self.assertFalse([r for r in written["items"] if r["item"] == "Theorem 2.3.4"
                          and len(r["occurrences"]) > 1])

    def test_parse_names_the_command_that_settles_a_deep_label(self):
        """`cf sources refresh --force` re-infers the SAME depth; only the override settles it."""
        with self.assertRaises(ItemGrammarError) as caught:
            cf_items.parse("Theorem 2.3.4.5", KINDS)
        self.assertIn("cf sources set <id> numbering=2,4", caught.exception.hint)
        self.assertNotIn("refresh", caught.exception.hint)


class TestNumberingIsPerSource(unittest.TestCase):
    def test_a_four_level_label_parses_when_the_source_numbers_that_deep(self):
        item, reject = cf_items.try_parse("Lemma 2.3.4.1", KINDS, numbering=(3, 4))
        self.assertIsNone(reject)
        self.assertEqual(item.id, "Lemma 2.3.4.1")
        self.assertEqual(item.number_levels, (2, 3, 4, 1))

    def test_a_chapter_less_label_parses_when_the_source_numbers_that_flat(self):
        item, reject = cf_items.try_parse("Theorem 7", KINDS, numbering=(1, 1))
        self.assertIsNone(reject)
        self.assertEqual(item.id, "Theorem 7")
        self.assertEqual((item.chapter, item.section, item.number), (7, None, None))
        self.assertEqual(cf_items.slug(item), "theorem-7")

    def test_a_letter_sub_part_stays_a_sub_part_at_any_depth(self):
        for numbering, raw, expect in (((2, 2), "Exercise 3.2(a)", "Exercise 3.2"),
                                       ((1, 1), "Exercise 3(a)", "Exercise 3"),
                                       ((3, 4), "Exercise 1.2.3.4(iii)", "Exercise 1.2.3.4")):
            item, reject = cf_items.try_parse(raw, KINDS, numbering=numbering)
            self.assertIsNone(reject, raw)
            self.assertEqual(item.id, expect)
            self.assertEqual(item.sub, raw[len(expect):])

    def test_a_four_level_key_can_never_collide_with_a_two_level_one(self):
        keys = set()
        for raw in ("Theorem 2.3", "Theorem 2.3.4", "Theorem 2.3.4.5", "Theorem 2.3.45"):
            item, reject = cf_items.try_parse(raw, KINDS, numbering=(1, 4))
            self.assertIsNone(reject, raw)
            keys.add(item.id)
        self.assertEqual(len(keys), 4)

    def test_default_numbering_is_the_grammar_the_corpus_was_keyed_with(self):
        self.assertEqual(cf_items.DEFAULT_NUMBERING, (2, 3))
        self.assertEqual(cf_items.numbering_range(None), (2, 3))
        self.assertEqual(cf_items.numbering_range({"min_segments": 1, "max_segments": 4}), (1, 4))
        self.assertEqual(cf_items.numbering_range(7), (4, 4))          # clamped, never raised on
        self.assertEqual(cf_items.numbering_range((3, 1)), (3, 3))     # hi < lo is lo
        # `numbering: {}` is what a host with no cflib stores, and that run used
        # the DEFAULT -- reading it back as (2, 2) would reject every
        # three-segment label in the baseline corpus.
        self.assertEqual(cf_items.numbering_range({}), cf_items.DEFAULT_NUMBERING)
        self.assertEqual(prepare_source.segments_for(3, {}), cf_items.DEFAULT_NUMBERING)
        item, reject = cf_items.try_parse("Exercise 1.3.19", KINDS, numbering={})
        self.assertIsNone(reject)
        self.assertEqual(item.id, "Exercise 1.3.19")

    def test_a_hand_edited_numbering_falls_back_instead_of_raising(self):
        """The docstring promises tolerance of a value a human edited on disk."""
        for bad in ({"min_segments": "x"}, {"max_segments": None, "min_segments": "2.5"},
                    ("a", "b"), "3,4"):
            self.assertEqual(cf_items.numbering_range(bad), cf_items.DEFAULT_NUMBERING, bad)
            self.assertEqual(prepare_source.segments_for(3, bad),
                             prepare_source.DEFAULT_SEGMENTS, bad)

    def test_find_items_follows_the_sources_numbering(self):
        flat = _synthetic("Theorem %d")
        self.assertEqual(prepare_source.find_items(flat, KINDS), [])
        rows = prepare_source.find_items(flat, KINDS, segments=(1, 1))
        self.assertEqual(len(rows), 19)
        self.assertEqual(rows[0]["chapter"], 1)
        deep = prepare_source.find_items(_synthetic("Lemma 2.3.4.%d"), KINDS, segments=(3, 4))
        self.assertEqual(len(deep), 19)
        self.assertEqual(deep[0]["levels"], [2, 3, 4, 1])

    def test_segments_for_keeps_the_older_parts_spelling(self):
        self.assertEqual(prepare_source.segments_for(3), (2, 3))
        self.assertEqual(prepare_source.segments_for(2), (2, 2))
        self.assertEqual(prepare_source.segments_for(3, {"min_segments": 3,
                                                         "max_segments": 3}), (3, 3))


class TestTheNumberingOverride(unittest.TestCase):
    """The refusal named a command that could not settle it.

    ``numbering`` is inferred with a refusal attached, but unlike the offset,
    the kinds and the scope it had no override: ``cf sources refresh --force``
    re-reads the same text and reaches the same depth, so a book whose deeper
    labels are too few to clear ``MIN_DEPTH_SHARE`` dropped its real items on
    every run with no way to say "it really numbers this deep".
    """

    #: 20 three-level items plus 2 four-level ones: depth 4 is 2 headings, under
    #: `min_count`, so the inference says (3, 3) and the two are rejects.
    TEXT = _synthetic("Theorem 1.1.%d", 20) + _synthetic("Theorem 1.1.9.%d", 2)

    def test_the_inference_alone_drops_the_two_deep_items_on_every_run(self):
        guess = inference.infer_numbering(self.TEXT)
        self.assertEqual((guess.min_segments, guess.max_segments), (3, 3))
        out = _tmp(self, "cf-numov-")
        src = out / "book.tex"
        src.write_text(self.TEXT, encoding="utf-8")
        first = prepare_source.prepare(str(src), str(out / "p"), source_id="bk")
        again = prepare_source.prepare(str(src), str(out / "p"), source_id="bk", force=True)
        for payload in (first, again):
            self.assertEqual(payload["items"], 20)
            self.assertEqual(len(payload["item_rejects"]), 2)

    def test_the_override_takes_precedence_and_says_a_human_set_it(self):
        out = _tmp(self, "cf-numov2-")
        src = out / "book.tex"
        src.write_text(self.TEXT, encoding="utf-8")
        prepare_source.prepare(str(src), str(out / "p"), source_id="bk")
        payload = prepare_source.prepare(str(src), str(out / "p"), source_id="bk",
                                         segments=[3, 4])
        self.assertFalse(payload.get("skipped"), "a cached table must not answer a new numbering")
        self.assertEqual(payload["numbering"]["max_segments"], 4)
        self.assertEqual(payload["numbering"]["method"], "manual")
        self.assertEqual(payload["items"], 22)
        self.assertEqual(payload["item_rejects"], [])

    def test_the_override_is_settable_and_stamps_its_own_method(self):
        root = _tmp(self, "cf-numset-")
        self.assertEqual(_quiet(cf_main.main, ["init", str(root), "--no-git", "-q"]), 0)
        workspace = Workspace.load(root, validate=False)
        workspace.upsert_source("bk", sha256="c" * 64, title="t", edition="e")
        workspace.save()
        rc = _quiet(cf_main.cli, ["sources", "set", "bk", "numbering=3,4",
                                  "--workspace", str(root)])
        self.assertEqual(rc, 0)
        body = (Workspace.load(root, validate=False).data.get("sources") or {})["bk"]
        self.assertEqual(body["numbering"], [3, 4])
        self.assertEqual(body["numbering_method"], "manual")

    def test_a_numbering_that_is_not_a_range_is_refused_not_clamped(self):
        root = _tmp(self, "cf-numbad-")
        self.assertEqual(_quiet(cf_main.main, ["init", str(root), "--no-git", "-q"]), 0)
        workspace = Workspace.load(root, validate=False)
        workspace.upsert_source("bk", sha256="c" * 64, title="t", edition="e")
        workspace.save()
        for bad in ("numbering=deep", "numbering=0,2", "numbering=3,9", "numbering=4,3"):
            rc = _quiet(cf_main.cli, ["sources", "set", "bk", bad, "--workspace", str(root)])
            self.assertEqual(rc, 2, bad)
        body = (Workspace.load(root, validate=False).data.get("sources") or {})["bk"]
        self.assertNotIn("numbering", body)

    def test_the_workspace_row_reaches_prepare(self):
        """The override is inert unless `cf onboard`/`refresh` hands it to prepare."""
        from cfcmd.onboard import _prepare_source
        from cflib.hashing import sha256_file
        root = _tmp(self, "cf-numwire-")
        self.assertEqual(_quiet(cf_main.main, ["init", str(root), "--no-git", "-q"]), 0)
        src = root / "book.tex"
        src.write_text(self.TEXT, encoding="utf-8")
        workspace = Workspace.load(root, validate=False)
        workspace.upsert_source("bk", sha256=sha256_file(src), title="t", edition="e",
                                pdf=str(src))
        workspace.save()

        _quiet(_prepare_source, workspace, "bk", force=False, no_errata=True)
        items = json.loads((root / "sources" / "bk" / "items.json").read_text(encoding="utf-8"))
        self.assertEqual(items["count"], 20)

        self.assertEqual(_quiet(cf_main.cli, ["sources", "set", "bk", "numbering=3,4",
                                              "--workspace", str(root)]), 0)
        workspace = Workspace.load(root, validate=False)
        _quiet(_prepare_source, workspace, "bk", force=False, no_errata=True)
        items = json.loads((root / "sources" / "bk" / "items.json").read_text(encoding="utf-8"))
        self.assertEqual(items["count"], 22, "the override never reached prepare()")
        self.assertEqual(items["numbering"]["method"], "manual")
        self.assertEqual(items["rejects"], [])


class TestTheCompatViewCarriesEveryLevel(unittest.TestCase):
    """The sources/ and pair/ stages see the item through ``sources.compat``."""

    def test_the_item_id_view_keeps_the_fourth_level(self):
        view = compat._from_cf_item(
            "Lemma 2.3.4.1", cf_items.parse("Lemma 2.3.4.1", KINDS, numbering=(1, 4)))
        self.assertEqual(view.number, "2.3.4.1")
        self.assertEqual(view.parts, (2, 3, 4, 1))

    def test_a_deep_item_sorts_by_its_numbers_not_its_label(self):
        keys = sorted(("Theorem 2.3.4.10", "Theorem 2.3.4.9"), key=compat.item_sort_key)
        self.assertEqual(keys, ["Theorem 2.3.4.9", "Theorem 2.3.4.10"])

    def test_a_flat_numbered_item_sorts_by_its_number(self):
        keys = sorted(("Theorem 10", "Theorem 9", "Theorem 1"), key=compat.item_sort_key)
        self.assertEqual(keys, ["Theorem 1", "Theorem 9", "Theorem 10"])
        rows = prepare_source.find_items(_synthetic("Theorem %d", 12), KINDS, segments=(1, 1))
        self.assertEqual([r["item"] for r in rows][:3], ["Theorem 1", "Theorem 2", "Theorem 3"])
        self.assertEqual([r["item"] for r in rows][-1], "Theorem 12")

    def test_an_unnumbered_label_still_sorts_last(self):
        for label in ("In-text p.71", "In-text, book p. 71", "In-text p 71"):
            self.assertEqual(compat.item_sort_key(label)[0], (99, 99, 99), label)


class TestInferNumbering(unittest.TestCase):
    def test_the_depth_comes_off_the_sources_own_headings(self):
        for fmt, expect in (("Theorem %d", (1, 1)),
                            ("Theorem 3.%d", (2, 2)),
                            ("Definition 1.2.%d", (3, 3)),
                            ("Lemma 2.3.4.%d", (4, 4))):
            guess = inference.infer_numbering(_synthetic(fmt))
            self.assertEqual((guess.min_segments, guess.max_segments), expect, fmt)
            self.assertEqual(guess.method, "headings")

    def test_a_handful_of_stray_lines_does_not_widen_the_numbering(self):
        text = _synthetic("Definition 1.2.%d", 40) + "\nLet 2 be given.\nBibliography 1\n"
        guess = inference.infer_numbering(text)
        self.assertEqual((guess.min_segments, guess.max_segments), (3, 3))
        self.assertEqual(guess.counts[1], 2)          # counted and reported, never used
        self.assertLess(guess.confidence, 1.0)

    def test_a_text_with_no_numbered_heading_falls_back_and_says_so(self):
        guess = inference.infer_numbering("Some prose with no numbered heading at all.\n")
        self.assertEqual((guess.min_segments, guess.max_segments), inference.DEFAULT_NUMBERING)
        self.assertEqual(guess.method, "default")
        self.assertEqual(guess.confidence, 0.0)

    def test_structural_words_do_not_vote_on_the_numbering(self):
        text = "\n".join(["Chapter %d" % n for n in range(1, 9)]) + "\n" + \
            _synthetic("Theorem 1.2.%d")
        guess = inference.infer_numbering(text)
        self.assertEqual((guess.min_segments, guess.max_segments), (3, 3))


class TestInferKindsIsCaseInsensitive(unittest.TestCase):
    def test_an_all_caps_ams_head_is_a_kind_not_an_invisible_line(self):
        kinds, _structural = inference.infer_kinds(_synthetic("THEOREM 1.%d", 5))
        self.assertEqual(kinds, {"Theorem": 5}, "before the fix this was {}")

    def test_an_all_caps_head_becomes_a_ROW_not_just_a_counted_word(self):
        """Half a fix is worse than none: infer_kinds saw them, find_items did not.

        ``infer_kinds`` folds case, so an AMS book infers ``Theorem``; the item
        table's own alternation did not, so it matched nothing, ``kinds_not_in_text``
        no longer fired, and the refusal read "0 numbered items ... kinds Theorem"
        -- naming the kind as present.
        """
        text = _synthetic("THEOREM 1.2.%d", 19)
        rows = prepare_source.find_items(text, ("Theorem",))
        self.assertEqual(len(rows), 19, "before the fix this was 0")
        self.assertEqual(rows[0]["item"], "Theorem 1.2.1")
        self.assertEqual({r["kind"] for r in rows}, {"Theorem"})

    def test_the_canonical_id_is_the_sources_own_spelling_not_the_typesetters(self):
        mixed = _synthetic("THEOREM 1.2.%d", 3) + _synthetic("Theorem 1.3.%d", 3)
        rows = prepare_source.find_items(mixed, ("Theorem",))
        self.assertEqual([r["item"] for r in rows],
                         ["Theorem 1.2.1", "Theorem 1.2.2", "Theorem 1.2.3",
                          "Theorem 1.3.1", "Theorem 1.3.2", "Theorem 1.3.3"])
        self.assertEqual(prepare_source.canonical_kind("SATZ", KINDS), "Satz")
        self.assertEqual(prepare_source.canonical_kind("McShane", ("McShane",)), "McShane")

    def test_a_deep_all_caps_heading_is_rejected_under_its_canonical_name(self):
        _rows, rejects = prepare_source.find_headings(_synthetic("THEOREM 2.3.4.%d", 4),
                                                      ("Theorem",))
        self.assertEqual([r["kind"] for r in rejects], ["Theorem"] * 4)
        self.assertEqual(rejects[0]["raw"], "Theorem 2.3.4.1")

    def test_the_two_spellings_of_one_kind_are_one_kind(self):
        text = _synthetic("THEOREM 1.%d", 4) + _synthetic("Theorem 2.%d", 3)
        kinds, _structural = inference.infer_kinds(text)
        self.assertEqual(kinds, {"Theorem": 7})

    def test_a_lower_case_line_start_is_still_not_a_heading(self):
        """``-layout`` text wraps, so a body line can start with any word."""
        text = _synthetic("and 1.2.%d", 9)
        self.assertEqual(inference.infer_kinds(text)[0], {})

    def test_an_all_caps_structural_word_is_still_structural(self):
        kinds, structural = inference.infer_kinds(_synthetic("SECTION 1.%d", 4))
        self.assertEqual(kinds, {})
        self.assertEqual(structural, {"Section": 4})

    def test_a_flat_numbered_kind_needs_the_sources_own_depth(self):
        text = _synthetic("Problem %d", 6)
        self.assertEqual(inference.infer_kinds(text)[0], {})
        self.assertEqual(inference.infer_kinds(text, min_segments=1)[0], {"Problem": 6})

    def test_canonical_kind_word_leaves_a_mixed_case_word_alone(self):
        self.assertEqual(inference.canonical_kind_word("THEOREM"), "Theorem")
        self.assertEqual(inference.canonical_kind_word("McShane"), "McShane")
        self.assertEqual(inference.canonical_kind_word("Satz"), "Satz")


class TestTheFrozenNormalisersDidNotMove(unittest.TestCase):
    """``clean()`` is hashed into the corpus and ``join_key`` joins two generations."""

    #: pinned byte-for-byte: these are the shapes finding_id hashes
    CLEAN = {
        "Exercise 1.3.19": "Exercise 1.3.19",
        "Exercise    1.3.19": "Exercise 1.3.19",
        "Exercise\t1.3.19.": "Exercise 1.3.19",
        "`Exercise 1.3.19`": "Exercise 1.3.19",
        "Exercise\u00a01.3.19": "Exercise 1.3.19",
        "Theorem 2.3.4.5": "Theorem 2.3.4.5",
        "Theorem 7": "Theorem 7",
        "In-text, book p. 71": "In-text, book p. 71",
        "Exercise 1.1.6(i)": "Exercise 1.1.6(i)",
        "Lemma 1.2.13\u2013 x": "Lemma 1.2.13- x",
    }

    def test_clean_is_byte_for_byte_what_it_was(self):
        for raw, want in self.CLEAN.items():
            self.assertEqual(cf_items.clean(raw), want, raw)

    def test_a_numbered_label_joins_on_clean_whatever_the_numbering(self):
        for numbering in (None, (1, 1), (2, 3), (1, 4)):
            for raw in ("Exercise 1.3.19", "Theorem 2.3.4.5", "Theorem 7",
                        "Corollary (Lemma 1.2.5 + Exercise 1.2.4)"):
                self.assertEqual(cf_items.join_key(raw, KINDS, numbering=numbering),
                                 cf_items.clean(raw), (raw, numbering))

    def test_the_two_generations_in_text_spellings_still_meet(self):
        self.assertEqual(cf_items.join_key("In-text, book p. 71"), "In-text p.71")
        self.assertEqual(cf_items.join_key("In-text p.71"), "In-text p.71")
        self.assertEqual(cf_items.join_key("In-text, Definition 1.4.35"),
                         "In-text Definition 1.4.35")
        self.assertEqual(cf_items.join_key("In-text convention, book p. 29 (Exercise 1.2.5)"),
                         "In-text p.29")

    def test_a_synthetic_sweep_of_labels_round_trips_to_one_key(self):
        """The property: id + sub is the cleaned label, and depth never merges keys."""
        seen: dict[str, str] = {}
        for kind in ("Definition", "Lemma", "Theorem", "Exercise", "Satz"):
            for levels in ((7,), (3, 4), (1, 2, 3), (2, 3, 4, 5), (10, 20, 30, 40)):
                for sub in ("", "(i)", "(iv)", "(3)", "(a)"):
                    number = ".".join(str(n) for n in levels)
                    raw = f"{kind} {number}{sub}"
                    item, reject = cf_items.try_parse(raw, KINDS, numbering=(1, 4))
                    self.assertIsNone(reject, raw)
                    self.assertEqual(item.full, cf_items.clean(raw), raw)
                    self.assertEqual(cf_items.join_key(raw, KINDS, numbering=(1, 4)),
                                     cf_items.clean(raw), raw)
                    self.assertNotIn(item.full, seen, f"{raw} collided with {seen.get(item.full)}")
                    seen[item.full] = raw
        self.assertEqual(len(seen), 5 * 5 * 5)

    def test_whitespace_tolerance_survives_at_every_depth(self):
        for raw, want in (("Exercise    1.3.19", "Exercise 1.3.19"),
                          ("Theorem\t2.3.4.5", "Theorem 2.3.4.5"),
                          ("Theorem  7", "Theorem 7")):
            item, reject = cf_items.try_parse(raw, KINDS, numbering=(1, 4))
            self.assertIsNone(reject, raw)
            self.assertEqual(item.id, want)

    def test_sort_key_keeps_the_shape_make_tasks_relies_on(self):
        """plan/make_tasks.py supplies a literal (9, 0, 0, 0, (), item) beside these."""
        keys = [cf_items.sort_key(cf_items.parse(raw, KINDS, numbering=(1, 4)))
                for raw in ("Theorem 7", "Theorem 1.2.3", "Theorem 1.2.3.4",
                            "Theorem 1.2.3(i)")]
        keys.append((9, 0, 0, 0, (), "unparseable"))
        for key in keys:
            self.assertEqual(len(key), 6)
        sorted(keys)                                  # must not raise on mixed shapes


@unittest.skipIf(CORPUS is None, "CF_BASELINE_CORPUS unset")
class TestTheRealCorpusIsUnmoved(unittest.TestCase):
    """The tightened check must reject nothing that is already in the corpus."""

    @classmethod
    def setUpClass(cls):
        cls.labels: set[str] = set()
        for path in sorted(Path(CORPUS).glob("findings*/*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:                          # noqa: BLE001 - a corpus file may be junk
                continue
            if isinstance(payload.get("item"), str):
                cls.labels.add(payload["item"])
        for path in sorted(Path(CORPUS).glob("data/*.jsonl")):
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:                      # noqa: BLE001
                    continue
                if isinstance(row, dict) and isinstance(row.get("item"), str):
                    cls.labels.add(row["item"])
        if not cls.labels:
            raise unittest.SkipTest("no item labels under CF_BASELINE_CORPUS")

    def test_the_corpus_is_the_size_it_was(self):
        self.assertEqual(len(self.labels), 986)

    def test_not_one_corpus_label_is_deeper_than_the_default_numbering(self):
        """MEASURED: 0 of 986. The new reject cannot fire on the baseline."""
        deep = [label for label in self.labels
                if cf_items.try_parse(label, cf_items.DEFAULT_KINDS)[1] is not None
                and cf_items.try_parse(label, cf_items.DEFAULT_KINDS)[1].reason
                == cf_items.REJECT_TOO_MANY_SEGMENTS]
        self.assertEqual(deep, [])

    def test_every_corpus_label_joins_on_the_same_key_as_before(self):
        for label in self.labels:
            key, _reject = cf_items.try_join_key(label, cf_items.DEFAULT_KINDS)
            if cf_items.is_in_text(label):
                self.assertTrue(key.startswith(cf_items.IN_TEXT), label)
            else:
                self.assertEqual(key, cf_items.clean(label), label)

    def test_clean_is_idempotent_on_every_corpus_label(self):
        for label in self.labels:
            once = cf_items.clean(label)
            self.assertEqual(cf_items.clean(once), once, label)


@unittest.skipIf(BOOK_TXT is None, "CF_BOOK_TXT unset")
class TestThePinnedEditionParsesExactlyAsBefore(unittest.TestCase):
    """411 items, 442 labels -- with the numbering now READ rather than assumed."""

    @classmethod
    def setUpClass(cls):
        cls.text = Path(BOOK_TXT).read_text(encoding="utf-8", errors="ignore")

    def test_the_numbering_the_text_reports_is_three_segments(self):
        guess = inference.infer_numbering(self.text)
        self.assertEqual((guess.min_segments, guess.max_segments), (3, 3))
        self.assertGreater(guess.confidence, 0.95)

    def test_the_counts_do_not_move_under_the_inferred_numbering(self):
        rows, rejects = prepare_source.find_headings(
            self.text, prepare_source.SOURCE_KINDS, offset=16, segments=(3, 3))
        self.assertEqual(len(rows), 442)
        self.assertEqual(len([r for r in rows if r["chapter"] == 1]), 411)
        self.assertEqual(rejects, [])

    def test_the_inferred_numbering_and_the_default_find_the_same_rows(self):
        default = prepare_source.find_items(self.text, prepare_source.SOURCE_KINDS, offset=16)
        inferred = prepare_source.find_items(self.text, prepare_source.SOURCE_KINDS,
                                             offset=16, segments=(3, 3))
        self.assertEqual(default, inferred)


# ---------------------------------------------------------------------------
# generality#4 -- a source table the numbering says is mostly missing
# ---------------------------------------------------------------------------


class TestItemCompleteness(unittest.TestCase):
    def test_the_numbering_is_its_own_denominator(self):
        full = inference.item_completeness(["1.1.%d" % n for n in range(1, 21)])
        self.assertEqual((full["found"], full["expected"]), (20, 20))
        self.assertEqual(full["completeness"], 1.0)

    def test_a_table_with_two_of_thirty_slots_scores_accordingly(self):
        thin = inference.item_completeness(["2.8", "5.26"])
        self.assertEqual((thin["found"], thin["expected"]), (2, 34))
        self.assertLess(thin["completeness"], inference.MIN_ITEM_COMPLETENESS)
        self.assertEqual(thin["groups"], 2)

    def test_slots_are_pooled_across_kinds_so_a_shared_counter_scores_full(self):
        """Tao numbers Definition 1.3.8 then Lemma 1.3.9 on one counter."""
        pooled = inference.item_completeness(["1.3.%d" % n for n in range(1, 22)])
        self.assertEqual(pooled["completeness"], 1.0)

    def test_an_empty_table_is_zero_not_a_division_by_zero(self):
        self.assertEqual(inference.item_completeness([])["completeness"], 0.0)

    def test_the_worst_groups_are_named_for_the_message(self):
        detail = inference.item_completeness(["1.1", "1.2", "1.3", "9.40"])
        self.assertEqual(detail["worst"][0]["prefix"], "9")
        self.assertEqual(detail["worst"][0]["expected"], 40)

    def test_a_flat_numbering_has_a_no_chapter_group(self):
        detail = inference.item_completeness(["7", "8", "30"])
        self.assertEqual(detail["worst"][0]["prefix"], "(no chapter)")


class _Ref:
    """The two SourceRef fields the refusal reads."""

    def __init__(self, pdf="/x/book.pdf"):
        self.pdf = pdf


class TestANearlyEmptySourceIsRefused(unittest.TestCase):
    """CLAUDE.md S4: low confidence is a refusal, and it names the settlement."""

    def _payload(self, **over):
        payload = {
            "items": 15, "pages": 404, "kinds": ["Exercise", "Now", "Suppose"],
            "kind_counts": {"Exercise": 16, "Now": 5, "Suppose": 4},
            "kind_warnings": [], "warnings": [],
            "item_completeness": 0.217,
            "item_completeness_detail": {"found": 15, "expected": 69},
        }
        payload.update(over)
        return payload

    def test_an_empty_source_is_still_refused(self):
        with self.assertRaises(SourceError) as caught:
            _refuse_an_empty_source("ladr4e", _Ref(), self._payload(items=0))
        self.assertIn("0 numbered items", str(caught.exception))

    def test_a_table_below_the_floor_is_refused_and_says_which_signal_fired(self):
        with self.assertRaises(SourceError) as caught:
            _refuse_an_empty_source("ladr4e", _Ref(), self._payload())
        message = str(caught.exception)
        self.assertIn("0.217", message)
        self.assertIn("69 slots", message)
        self.assertIn("Exercise: 16", message)        # the user sees what it DID read

    def test_the_refusal_names_a_command_that_exists(self):
        with self.assertRaises(SourceError) as caught:
            _refuse_an_empty_source("ladr4e", _Ref(), self._payload())
        hint = getattr(caught.exception, "hint", "") or ""
        self.assertIn("cf sources set ladr4e completeness_ok=true", hint)
        self.assertIn("kinds=", hint)

    def test_a_table_above_the_floor_is_adopted(self):
        _refuse_an_empty_source("tao", _Ref(), self._payload(items=442,
                                                            item_completeness=0.9961))

    def test_the_override_in_the_workspace_settles_it(self):
        root = _tmp(self, "cf-comp-")
        (root / "workspace.yaml").write_text(
            "version: 2\nworkspace:\n  name: t\ncompanions: {}\n"
            "sources:\n  ladr4e:\n    completeness_ok: true\n"
            "hosts:\n  allowlist: []\n", encoding="utf-8")
        workspace = Workspace.load(root, validate=False)
        _refuse_an_empty_source("ladr4e", _Ref(), self._payload(), workspace)

    def test_every_declared_kind_absent_from_the_text_is_refused(self):
        payload = self._payload(
            items=2, kinds=["Definition", "Lemma", "Theorem"],
            kind_counts={"Now": 5, "See": 3},
            kind_warnings=["kinds_not_in_text: Definition, Lemma, Theorem"],
            item_completeness=0.9)
        with self.assertRaises(SourceError) as caught:
            _refuse_an_empty_source("ladr4e", _Ref(), payload)
        self.assertIn("not one of the 3 kinds", str(caught.exception))
        self.assertIn("Now: 5", str(caught.exception))

    def test_kinds_not_inferred_at_all_is_refused(self):
        payload = self._payload(kind_counts={}, warnings=["kinds_not_inferred"],
                                item_completeness=0.95)
        with self.assertRaises(SourceError):
            _refuse_an_empty_source("ladr4e", _Ref(), payload)

    def test_a_source_prepared_before_the_check_existed_is_not_re_judged(self):
        payload = self._payload()
        payload.pop("item_completeness")
        _refuse_an_empty_source("legacy", _Ref(), payload)

    def test_the_floor_is_the_measured_one(self):
        self.assertEqual(inference.MIN_ITEM_COMPLETENESS, 0.6)


class TestCompletenessOverrideStampsItself(unittest.TestCase):
    def test_cf_sources_set_records_who_vouched_for_the_table(self):
        root = _tmp(self, "cf-set-")
        self.assertEqual(_quiet(cf_main.main, ["init", str(root), "--no-git", "-q"]), 0)
        workspace = Workspace.load(root, validate=False)
        workspace.upsert_source("s1", sha256="a" * 64, title="t", edition="e")
        workspace.save()
        rc = _quiet(cf_main.main, ["sources", "set", "s1", "completeness_ok=true",
                                   "--workspace", str(root)])
        self.assertEqual(rc, 0)
        body = (Workspace.load(root, validate=False).data.get("sources") or {})["s1"]
        self.assertIs(body["completeness_ok"], True)
        self.assertEqual(body["completeness_method"], "manual")


@unittest.skipIf(BOOK_TXT is None, "CF_BOOK_TXT unset")
class TestThePinnedEditionPassesTheFloorWithMargin(unittest.TestCase):
    def test_the_measured_completeness_is_the_recorded_one(self):
        text = Path(BOOK_TXT).read_text(encoding="utf-8", errors="ignore")
        rows = prepare_source.find_items(text, prepare_source.SOURCE_KINDS, offset=16)
        detail = inference.item_completeness([r["number"] for r in rows])
        self.assertEqual(detail["completeness"], 0.9961)
        self.assertGreater(detail["completeness"] - inference.MIN_ITEM_COMPLETENESS, 0.3)


# ---------------------------------------------------------------------------
# generality#6 -- a source that is not a paginated PDF
# ---------------------------------------------------------------------------


MANUSCRIPT = """# 3 Vector spaces

Some prose.

## 3.2 Bases

Definition 3.2.1 (basis). A list is a basis if ...

Theorem 3.2.2. Every finite-dimensional space has a basis.

Theorem 3.2.3. Bases have equal length.

Definition 3.2.4 (dimension). The dimension is ...

## 3.7 Duality

Theorem 3.7.1. The dual is a vector space.

Definition 3.7.2 (dual basis). The dual basis is ...

Theorem 3.7.3. Duality is involutive on finite dimensions.

Exercise 3.7.4. Prove it.

Exercise 3.7.5. Prove the converse.

Exercise 3.7.6. Find a counterexample.
"""


class TestATextManuscriptIsASource(unittest.TestCase):
    def setUp(self):
        self.root = _tmp(self, "cf-text-")
        self.src = self.root / "notes.md"
        self.src.write_text(MANUSCRIPT, encoding="utf-8")

    def _prepare(self, **over):
        return prepare_source.prepare(str(self.src), str(self.root / "prepared"),
                                      source_id="notes", **over)

    def test_prepare_reads_a_markdown_manuscript_without_poppler(self):
        payload = self._prepare()
        self.assertEqual(payload["source_format"], "text")
        self.assertEqual(payload["tool"], "read (.md)")
        self.assertEqual(payload["items"], 10)
        self.assertEqual((self.root / "prepared" / "text.txt").read_text(encoding="utf-8"),
                         MANUSCRIPT)

    def test_an_unpaginated_source_says_so_everywhere_it_matters(self):
        payload = self._prepare()
        self.assertEqual(payload["pagination"], locator.PAGINATION_NONE)
        self.assertEqual(payload["locator"], locator.LOCATOR_SECTION)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(payload["offset_method"], "none")
        self.assertEqual(payload["pages"], 0)
        self.assertEqual(payload["printed_page_rule"],
                         "not applicable for an unpaginated source")
        self.assertEqual(payload["page_files"], "none")
        self.assertFalse((self.root / "prepared" / "pages").exists())

    def test_an_item_of_an_unpaginated_source_is_located_by_its_heading_path(self):
        self._prepare()
        written = json.loads((self.root / "prepared" / "items.json").read_text(encoding="utf-8"))
        self.assertEqual(written["locator"], locator.LOCATOR_SECTION)
        rows = {row["item"]: row for row in written["items"]}
        self.assertEqual(rows["Theorem 3.2.2"]["locator"], "3 Vector spaces > 3.2 Bases")
        self.assertEqual(rows["Exercise 3.7.4"]["locator"], "3 Vector spaces > 3.7 Duality")
        # no page was invented: a page-dependent stage must refuse, not read a 1
        self.assertIsNone(rows["Theorem 3.2.2"]["pdf_page"])
        self.assertIsNone(rows["Theorem 3.2.2"]["page"])
        self.assertGreater(rows["Theorem 3.2.2"]["line"], 0)

    def test_a_page_offset_is_refused_for_an_unpaginated_source(self):
        with self.assertRaises(ValueError) as caught:
            self._prepare(declared=16)
        self.assertIn("not applicable for an unpaginated source", str(caught.exception))

    def test_a_second_prepare_of_the_same_manuscript_is_idempotent(self):
        first = self._prepare()
        second = self._prepare()
        self.assertTrue(second["skipped"])
        self.assertEqual(first["text_sha256"], second["text_sha256"])


class TestAFormfeedTextKeepsItsPages(unittest.TestCase):
    """A ``pdftotext`` dump kept as a file still has pages, and they still work."""

    def _dump(self) -> str:
        pages = ["Front matter\n", "Contents\n"]
        for printed in range(1, 7):
            body = [str(printed) + "   1.2. Bases"]
            body.append("Theorem 1.2.%d. A statement." % printed)
            body.append("Definition 1.2.%d (a name). A definition." % (printed + 10))
            body.append("Exercise 1.2.%d. Prove it." % (printed + 20))
            pages.append("\n".join(body) + "\n")
        return "\f".join(pages)

    def test_the_offset_is_inferred_from_the_running_heads_of_a_text_file(self):
        root = _tmp(self, "cf-ff-")
        src = root / "dump.txt"
        src.write_text(self._dump(), encoding="utf-8")
        payload = prepare_source.prepare(str(src), str(root / "prepared"), source_id="dump",
                                         write_pages=False)
        self.assertEqual(payload["pagination"], locator.PAGINATION_FORMFEED)
        self.assertEqual(payload["locator"], locator.LOCATOR_PAGE)
        self.assertEqual(payload["pages"], 8)
        self.assertEqual(payload["offset"], 2)
        self.assertEqual(payload["offset_method"], "running_head_mode")
        self.assertEqual(payload["printed_page_rule"], "printed = pdf_page - 2")
        written = json.loads((root / "prepared" / "items.json").read_text(encoding="utf-8"))
        rows = {row["item"]: row for row in written["items"]}
        self.assertEqual(rows["Theorem 1.2.1"]["pdf_page"], 3)
        self.assertEqual(rows["Theorem 1.2.1"]["page"], 1)


@unittest.skipIf(BOOK_TXT is None, "CF_BOOK_TXT unset")
class TestTheRealDumpPreparesAsAText(unittest.TestCase):
    def test_the_pinned_edition_prepares_from_its_text_with_the_same_figures(self):
        root = _tmp(self, "cf-realtxt-")
        payload = prepare_source.prepare(BOOK_TXT, str(root / "prepared"),
                                         source_id="tao-txt", write_pages=False)
        self.assertEqual(payload["source_format"], "text")
        self.assertEqual(payload["pagination"], locator.PAGINATION_FORMFEED)
        self.assertEqual(payload["pages"], 265)
        self.assertEqual(payload["offset"], 16)
        self.assertEqual(payload["items"], 442)
        self.assertEqual(payload["item_completeness"], 0.9961)
        self.assertEqual(payload["item_rejects"], [])


class TestTheLocator(unittest.TestCase):
    def test_source_format_accepts_both_kinds_and_refuses_the_rest(self):
        self.assertEqual(locator.source_format("book.PDF"), locator.FORMAT_PDF)
        for name in ("notes.md", "ms.tex", "chapter.html", "dump.txt"):
            self.assertEqual(locator.source_format(name), locator.FORMAT_TEXT)
        with self.assertRaises(ValueError):
            locator.source_format("book.djvu")

    def test_pagination_is_detected_from_the_page_breaks(self):
        self.assertEqual(locator.detect_pagination("a\fb"), locator.PAGINATION_FORMFEED)
        self.assertEqual(locator.detect_pagination("a b"), locator.PAGINATION_NONE)

    def test_every_accepted_suffix_has_a_locator_or_is_plain_text(self):
        """An accepted markup with no branch gives every row an empty locator."""
        bodies = {
            ".md": "# One\n\ntext\n", ".markdown": "# One\n\ntext\n",
            ".tex": "\\section{One}\ntext\n", ".html": "<h1>One</h1>", ".htm": "<h1>One</h1>",
            ".rst": "One\n===\n\ntext\n",
        }
        for suffix in locator.TEXT_SUFFIXES:
            if suffix == ".txt":
                continue                       # no markup to read: line numbers locate it
            self.assertIn(suffix, bodies, f"{suffix} is accepted but has no heading pattern")
            self.assertTrue(locator.heading_paths(bodies[suffix], suffix), suffix)

    def test_rst_underline_headings_nest_by_the_rule_character(self):
        text = ("Chapter one\n===========\n\nprose\n\nA section\n---------\n\n"
                "more\n\nChapter two\n===========\n")
        self.assertEqual([path for _pos, path in locator.heading_paths(text, ".rst")],
                         ["Chapter one", "Chapter one > A section", "Chapter two"])

    def test_an_rst_rule_shorter_than_its_line_is_not_a_heading(self):
        self.assertEqual(locator.heading_paths("a long line of prose\n---\n", ".rst"), [])

    def test_heading_paths_are_read_per_markup(self):
        md = locator.heading_paths("# One\ntext\n## Two\ntext\n# Three\n", ".md")
        self.assertEqual([path for _pos, path in md], ["One", "One > Two", "Three"])
        tex = locator.heading_paths("\\chapter{A}\n\\section{B}\n\\subsection{C}\n", ".tex")
        self.assertEqual([path for _pos, path in tex], ["A", "A > B", "A > B > C"])
        html = locator.heading_paths("<h1>A</h1><p>x</p><h2>B <em>b</em></h2>", ".html")
        self.assertEqual([path for _pos, path in html], ["A", "A > B b"])
        self.assertEqual(locator.heading_paths("plain text\n", ".txt"), [])

    def test_locate_takes_the_innermost_heading_at_or_before_a_position(self):
        anchors = [(0, "A"), (10, "A > B"), (40, "C")]
        self.assertEqual(locator.locate(anchors, 5), "A")
        self.assertEqual(locator.locate(anchors, 39), "A > B")
        self.assertEqual(locator.locate(anchors, 99), "C")
        self.assertEqual(locator.locate([], 3), "")

    def test_require_pagination_refuses_by_name_and_says_what_settles_it(self):
        locator.require_pagination({"pagination": locator.PAGINATION_FORMFEED}, "page images")
        locator.require_pagination({}, "page images")          # unknown: assume paginated
        with self.assertRaises(CfError) as caught:
            locator.require_pagination({"pagination": locator.PAGINATION_NONE},
                                       "page images (--images)", source_id="notes")
        self.assertIn("not applicable for an unpaginated source", str(caught.exception))
        self.assertIn("notes", str(caught.exception))
        self.assertIn("cf sources add", getattr(caught.exception, "hint", "") or "")

    def test_require_pdf_refuses_a_manuscript_that_happens_to_have_pages(self):
        """Pagination is not the same question: a pdftotext dump has form feeds."""
        locator.require_pdf("book.pdf", "page images (--images)")
        locator.require_pdf({"source_format": locator.FORMAT_PDF}, "page images (--images)")
        locator.require_pdf("", "page images (--images)")       # unknown: not this guard's call
        with self.assertRaises(CfError) as caught:
            locator.require_pdf({"source_format": locator.FORMAT_TEXT,
                                 "pagination": locator.PAGINATION_FORMFEED},
                                "page images (--images)", source_id="dump")
        self.assertIn("needs a PDF", str(caught.exception))
        self.assertIn("dump", str(caught.exception))
        self.assertIn("cf sources add <pdf>", getattr(caught.exception, "hint", "") or "")
        with self.assertRaises(CfError):
            locator.require_pdf("dump.txt", "page images (--images)")


class TestTheSourcesCommandSpeaksOfSourcesNotPdfs(unittest.TestCase):
    def test_the_add_positional_is_named_source_and_names_both_kinds(self):
        action = _find_action(cf_main.build_parser()[0], ["sources", "add"], "source")
        self.assertIsNotNone(action, "`cf sources add` must take a <source>, not a <pdf>")
        self.assertIn(".tex", action.help)
        self.assertIn("PDF", action.help)

    def test_the_add_help_lists_exactly_what_is_accepted(self):
        action = _find_action(cf_main.build_parser()[0], ["sources", "add"], "source")
        for suffix in locator.TEXT_SUFFIXES:
            self.assertIn(suffix, action.help, suffix)

    def test_completeness_ok_is_settable(self):
        action = _find_action(cf_main.build_parser()[0], ["sources", "set"], "assignments")
        self.assertIn("completeness_ok=true", action.help)

    def test_numbering_is_settable(self):
        action = _find_action(cf_main.build_parser()[0], ["sources", "set"], "assignments")
        self.assertIn("numbering=3,4", action.help)

    def test_images_refuse_on_an_unpaginated_source(self):
        root = self._unpaginated_workspace("cf-img-")
        rc = _quiet(cf_main.cli, ["sources", "refresh", "notes", "--images",
                                  "--workspace", str(root)])
        self.assertEqual(rc, 2)

    def test_images_refuse_on_a_text_manuscript_that_carries_page_breaks(self):
        """`--images` shelled pdftoppm at the manuscript: an unhandled traceback."""
        root = _tmp(self, "cf-imgtxt-")
        self.assertEqual(_quiet(cf_main.main, ["init", str(root), "--no-git", "-q"]), 0)
        dump = root / "dump.txt"
        dump.write_text("\f".join("Theorem 1.%d.1 x.\n" % n for n in range(1, 7)),
                        encoding="utf-8")
        workspace = Workspace.load(root, validate=False)
        workspace.upsert_source("dumpsrc", sha256="d" * 64, title="t", edition="e",
                                pdf=str(dump), source_format=locator.FORMAT_TEXT,
                                pagination=locator.PAGINATION_FORMFEED)
        workspace.save()
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = cf_main.cli(["sources", "refresh", "dumpsrc", "--images",
                              "--workspace", str(root)])
        self.assertEqual(rc, 2, "a refusal, not a pdftoppm traceback")
        self.assertIn("needs a PDF", err.getvalue())

    def test_the_list_row_of_an_unpaginated_source_prints_no_offset(self):
        """`offset 0  conf 1.00` is the relation `cf sources show` refuses to print."""
        root = self._unpaginated_workspace("cf-list-")
        workspace = Workspace.load(root, validate=False)
        # exactly what an unpaginated source records: offset 0, method none,
        # confidence 1.0 -- certainty about a number that means nothing here.
        workspace.upsert_source("notes", offset=0, offset_confidence=1.0,
                                offset_method="none")
        workspace.save()
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            rc = cf_main.cli(["sources", "list", "--workspace", str(root)])
        self.assertEqual(rc, 0)
        row = next(line for line in out.getvalue().splitlines() if line.startswith("notes"))
        columns = row.split()
        offset_at = LIST_COLUMNS.index("offset")
        self.assertEqual(columns[offset_at], "-", row)
        self.assertEqual(columns[LIST_COLUMNS.index("conf")], "-", row)
        self.assertNotIn("1.00", row)

    def test_an_offset_override_refuses_on_an_unpaginated_source(self):
        root = self._unpaginated_workspace("cf-off-")
        rc = _quiet(cf_main.cli, ["sources", "set", "notes", "offset=16",
                                  "--workspace", str(root)])
        self.assertEqual(rc, 2)
        body = (Workspace.load(root, validate=False).data.get("sources") or {})["notes"]
        self.assertNotIn("offset_method", body)

    def _unpaginated_workspace(self, prefix: str) -> Path:
        root = _tmp(self, prefix)
        self.assertEqual(_quiet(cf_main.main, ["init", str(root), "--no-git", "-q"]), 0)
        workspace = Workspace.load(root, validate=False)
        workspace.upsert_source("notes", sha256="b" * 64, title="t", edition="e",
                                pagination=locator.PAGINATION_NONE)
        workspace.save()
        return root


class TestTheUserFacingStringsSpeakOfSourcesNotPdfs(unittest.TestCase):
    """A manuscript is a source now; the prose that still said PDF was wrong."""

    def test_a_missing_source_file_is_not_called_a_missing_pdf(self):
        from cfcmd.onboard import _prepare_source
        root = _tmp(self, "cf-nopdf-")
        self.assertEqual(_quiet(cf_main.main, ["init", str(root), "--no-git", "-q"]), 0)
        workspace = Workspace.load(root, validate=False)
        workspace.upsert_source("bk", sha256="e" * 64, title="t", edition="e")
        workspace.save()
        from cflib import log
        log.set_level(1)                       # `cf init -q` above silenced the module
        self.addCleanup(log.set_level, 1)
        said = io.StringIO()
        with redirect_stdout(said), redirect_stderr(said):
            _prepare_source(workspace, "bk", force=False, no_errata=True)
        said = said.getvalue()
        self.assertIn("no source file on this machine", said)
        self.assertIn("cf sources add <source> --for <companion>", said)
        self.assertNotIn("<pdf>", said)

    def test_the_top_level_epilogue_says_source(self):
        _parser, epilogue = cf_main.build_parser()[0], cf_main.build_parser()[0].epilog
        self.assertIn("fingerprints the source", epilogue)
        self.assertNotIn("fingerprints the PDF", epilogue)


class TestTheDocsStateTheGrammarThatIsImplemented(unittest.TestCase):
    """SKILL.md is the passage most agents read; it stated the old grammar.

    Not a style check: the sentence names the key every stage of the audit is
    built on, and it said two-or-three fixed levels long after the depth became
    a per-source inference.
    """

    DOCS = ("SKILL.md", "README.md", "docs/design/archive/multi-source-model.md",
            "references/vocabulary.md")

    def _read(self, name: str) -> str:
        return (SKILL / name).read_text(encoding="utf-8")

    def test_the_grammar_is_stated_the_same_way_everywhere_it_is_stated(self):
        for name in ("SKILL.md", "docs/design/archive/multi-source-model.md",
                     "references/vocabulary.md"):
            body = self._read(name)
            self.assertIn("<Kind> <n>[.<n>", body, name)
            self.assertNotIn("<Kind> <c>.<s>.<n>", body, name)

    def test_no_shipped_doc_still_asks_for_a_pdf_positional(self):
        for name in self.DOCS:
            self.assertNotIn("sources add <pdf", self._read(name), name)

    def test_the_numbering_and_completeness_refusals_are_in_the_current_spec(self):
        """These are two live refusals, so they are pinned in the live document.

        `docs/design/archive/multi-source-model.md` also describes them, and its
        banner says it is historical: a test that asserted current behaviour from
        the archive would keep passing after the code and the current spec moved
        on together.  `docs/design/stage_map.md` §3 is where both now live.
        """
        spec = self._read("docs/design/stage_map.md")
        self.assertIn("**The numbering depth**", spec)
        self.assertIn("**Item completeness**", spec)
        self.assertIn("MIN_ITEM_COMPLETENESS", spec)
        self.assertIn("cflib.inference.infer_numbering", spec)
        self.assertIn("MIN_DEPTH_SHARE", spec)
        for field in ("source_format", "pagination", "completeness_ok", "completeness_method",
                      "numbering_method"):
            self.assertIn(field, spec, field)

    def test_the_skill_inference_table_lists_the_new_inferences(self):
        table = self._read("SKILL.md")
        self.assertIn("MIN_ITEM_COMPLETENESS", table)
        self.assertIn("infer_numbering", table)


def _find_action(parser, path: list[str], dest: str):
    """The argparse action ``dest`` of the subcommand at ``path``."""
    current = parser
    for name in path:
        subparsers = [a for a in current._actions if hasattr(a, "choices") and a.choices]
        for action in subparsers:
            if name in (action.choices or {}):
                current = action.choices[name]
                break
        else:                                          # pragma: no cover - a typo in the test
            raise AssertionError(f"no subcommand {name!r}")
    for action in current._actions:
        if action.dest == dest:
            return action
    return None


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
