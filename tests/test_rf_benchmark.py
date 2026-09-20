"""The benchmark: planted defects, `cf score`, and a default-on synthetic tier.

Three things the review found missing, all in one file because they share one
fixture:

* **a recall denominator.**  Nothing in the corpus, the ledger or the tally can
  show a defect the fleet failed to raise, so "the companion is faithful" and
  "nobody looked" produced identical artifacts (critic#4, prompts-workflows#3).
  ``tests/e2e/answer-key.json`` plants eight defects in the two synthetic
  companions and ``cf score`` joins findings to it, which makes precision and
  recall of the MODEL-FREE detector a number anyone can reproduce.
* **coverage of the code that fixes the task universe.**  ``prepare_source.
  prepare()``, ``pair/make_queue.py``, ``anchor_check.check()`` and
  ``scripts/cfcmd/pair.py`` ran only under the private-data tier (tests#2).
  Here they run by default, over synthetic form-feed TEXT: no PDF reader, no
  poppler, no copyrighted book.
* **a machine-readable record of the pinned figures** (tests#5), in
  ``tests/baseline/regression-figures.json``, checked against the tests that
  assert them -- and, where the private data is present, against the data.

Nothing here reads a companion repository or the network.  The two figures the
gated tier re-derives come from ``CF_BOOK_TXT`` and ``CF_BASELINE_CORPUS`` and
skip cleanly when those are unset.

Run:  python3 -m unittest tests.test_rf_benchmark -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
_E2E = os.path.join(_REPO, "tests", "e2e")
for _path in (_SCRIPTS, _E2E):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import make_notes_repos as fixture  # noqa: E402  (tests/e2e)
import make_pdf  # noqa: E402       (tests/e2e)
from cflib.errors import CfError  # noqa: E402
from score import answer_key as key_mod  # noqa: E402
from score import mutate, rows as rows_mod, score as score_mod  # noqa: E402
from sources import prepare_source, render_pages  # noqa: E402

ANSWER_KEY = pathlib.Path(_E2E) / "answer-key.json"
STAGE_TABLE = pathlib.Path(_E2E) / "stages.anonymous-elementary-sets.json"
FIGURES = pathlib.Path(_REPO) / "tests" / "baseline" / "regression-figures.json"
SOURCE_ID = "anonymous-elementary-sets"
#: the booklet is byte-deterministic; this is what make_pdf.build() hashes to
BOOKLET_SHA = "0de15de43744e8a5d2193833bcc7d7fe78a1cb9bc0d85b22c2e7ba700dda668e"


def tmp_dir(prefix: str) -> str:
    """A temp directory removed when the process exits."""
    import atexit

    path = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def booklet_text() -> str:
    """The booklet as ``pdftotext`` would hand it over: pages split by form feeds.

    Built from ``make_pdf.PAGES`` -- the same lines the PDF writer draws -- so
    the whole of stage 1 can run with no PDF reader on the machine.  The running
    heads are the last line of each body page, which is what the offset
    inference votes on.
    """
    return "\f".join("\n".join(page) + "\n" for page in make_pdf.PAGES)


def booklet_items() -> list[str]:
    """The item ids the booklet prints, from the generator's own table."""
    return ["%s %s" % (kind, number) for kind, number, _title, _body in make_pdf.ITEMS]


#: the docstring tag `cf onboard`'s docstring_tags adapter reads off a module
TAG_RE = re.compile(r"`(?P<item>[A-Z][A-Za-z]+ \d+(?:\.\d+)+)`")


def tagged_items(*names: str) -> set:
    """The book items these fixture companions tag, read off the fixture itself.

    The expectation for "which items are in the task universe" has to come from
    somewhere other than the document under test: two fields of one
    reconciliation report agreeing with each other says nothing about either.
    """
    out = set()
    for name in names:
        for _module, text, _record in fixture.modules_for(name):
            found = TAG_RE.search(text)
            if found:                       # an untagged helper, or a removed tag
                out.add(found.group("item"))
    return out


def run_cf(*argv) -> tuple[int, str]:
    """Run one cf command in-process; returns (exit code, stdout)."""
    import cf as cf_main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cf_main.main(list(argv))
    return rc, out.getvalue()


# --------------------------------------------------------------------------
# the mutation generator
# --------------------------------------------------------------------------
class MutationClassTests(unittest.TestCase):
    """Every defect class plants what it says it plants, deterministically."""

    DEF = ("/-- `Definition 1.3.2` (fixture).  Book p. 6.\n\n"
           "Formalised from the booklet. -/\ndef jordanOuter (n : Nat) : Nat := n\n")
    THM = ("/-- `Exercise 1.2.6` (fixture).  Book p. 4.\n\n"
           "Formalised from the booklet. -/\n"
           "theorem measure_translation (n k : Nat) : measure (n + k) = measure n + k := rfl\n")
    NEG = ("/-- `Exercise 1.3.7` (fixture).  Book p. 8.\n\n"
           "Formalised from the booklet. -/\n"
           "theorem not_jordan (n : Nat) (h : jordanInner n < jordanOuter n) :\n"
           "    Not (jordanMeasurable n) := fun hEq => absurd hEq (Nat.ne_of_lt h)\n")
    LEQ = ("/-- `Lemma 1.4.2` (fixture).  Book p. 9.\n\n"
           "Formalised from the booklet. -/\n"
           "theorem lebesgue_subadditive (m n : Nat) :\n"
           "    lebesgueOuter (m + n) <= lebesgueOuter m + lebesgueOuter n := Nat.le_refl _\n")

    def test_every_class_in_the_table_has_a_kind(self):
        """A class with no kind could not be scored, so there must not be one."""
        self.assertEqual(set(mutate.MUTATIONS), set(mutate.KIND_BY_CLASS))

    def test_strengthened_hypothesis_adds_a_binder(self):
        out, record = mutate.mutate_text(self.THM, "strengthened_hypothesis")
        self.assertIn("(hMut : 0 < n)", out)
        self.assertEqual(record["kind"], "CONTENT")
        # the conclusion is untouched: the defect is the extra assumption
        self.assertIn("measure (n + k) = measure n + k", out)

    def test_vacuity_drops_a_negation(self):
        out, record = mutate.mutate_text(self.NEG, "vacuity")
        self.assertNotIn("Not (jordanMeasurable n)", out)
        self.assertIn(": jordanMeasurable n := rfl", out)
        self.assertEqual(record["kind"], "VACUITY")

    def test_vacuity_of_a_positive_statement_is_True(self):
        out, _record = mutate.mutate_text(self.THM, "vacuity")
        self.assertIn(": True := trivial", out)

    def test_dropped_dimension_pins_the_last_variable(self):
        out, record = mutate.mutate_text(self.THM, "dropped_dimension")
        self.assertIn("theorem measure_translation (n : Nat) :", out)
        self.assertIn("measure (n + 1) = measure n + 1", out)
        self.assertEqual(record["pinned"], "k")

    def test_dropped_dimension_refuses_a_single_variable(self):
        with self.assertRaises(CfError) as caught:
            mutate.mutate_text(self.DEF, "dropped_dimension")
        self.assertIn("one variable", str(caught.exception))

    def test_reversed_inequality_swaps_the_sides(self):
        out, _record = mutate.mutate_text(self.LEQ, "reversed_inequality")
        self.assertIn("lebesgueOuter m + lebesgueOuter n <= lebesgueOuter (m + n)", out)

    def test_reversed_inequality_refuses_a_statement_without_one(self):
        with self.assertRaises(CfError):
            mutate.mutate_text(self.THM, "reversed_inequality")

    def test_proxy_definition_replaces_the_defining_term(self):
        out, record = mutate.mutate_text(self.DEF, "proxy_definition",
                                         library_object="Lib.Jordan.jordanContent")
        self.assertIn(":= Lib.Jordan.jordanContent n", out)
        self.assertEqual(record["kind"], "PROXY")

    def test_proxy_definition_needs_an_object(self):
        with self.assertRaises(CfError) as caught:
            mutate.mutate_text(self.DEF, "proxy_definition")
        self.assertIn("--object", str(caught.exception.hint))

    def test_missing_item_removes_the_book_tag(self):
        out, record = mutate.mutate_text(self.DEF, "missing_item")
        self.assertNotIn("Definition 1.3.2", out)
        self.assertIn("Book p. 6", out)          # the page survives; the TAG is gone
        self.assertEqual(record["removed_tag"], "Definition 1.3.2")

    def test_wrong_page_anchor_moves_the_page_off_the_printed_one(self):
        out, record = mutate.mutate_text(self.LEQ, "wrong_page_anchor")
        self.assertIn("Book p. 3", out)
        self.assertEqual((record["was_page"], record["now_page"]), (9, 3))

    def test_wrong_page_anchor_never_plants_an_adjacent_page(self):
        """pair/anchor_check.py calls +/-1 advisory, so a key must not plant one."""
        for seed in range(len(mutate._PAGE_DELTAS)):
            _out, record = mutate.mutate_text(self.LEQ, "wrong_page_anchor", seed=seed)
            self.assertGreater(abs(record["now_page"] - record["was_page"]), 1)

    def test_convention_rename_changes_the_name_and_nothing_else(self):
        out, record = mutate.mutate_text(self.THM, "convention_rename")
        self.assertIn("theorem measureTranslation", out)
        self.assertIn("measure (n + k) = measure n + k := rfl", out)
        self.assertIsNone(record["kind"])        # a decoy, not a defect

    def test_a_mutation_is_deterministic(self):
        first, _a = mutate.mutate_text(self.THM, "dropped_dimension", seed=1)
        second, _b = mutate.mutate_text(self.THM, "dropped_dimension", seed=1)
        self.assertEqual(first, second)
        other, _c = mutate.mutate_text(self.THM, "dropped_dimension", seed=0)
        self.assertNotEqual(first, other)        # the seed really selects

    def test_an_unknown_class_is_refused_by_name(self):
        with self.assertRaises(CfError) as caught:
            mutate.mutate_text(self.THM, "make_it_worse")
        self.assertIn("classes:", str(caught.exception.hint))

    def test_text_with_no_declaration_is_refused(self):
        with self.assertRaises(CfError):
            mutate.mutate_text("-- just a comment\n", "vacuity")


class MutationGuardTests(unittest.TestCase):
    """The generator refuses to plant a defect outside a fixture tree."""

    def setUp(self):
        self.root = pathlib.Path(tmp_dir("cf-mutate-"))
        self.file = self.root / "Thing.lean"
        self.file.write_text(MutationClassTests.DEF, encoding="utf-8")

    def test_a_temp_directory_is_allowed(self):
        record = mutate.mutate_file(self.file, "missing_item")
        self.assertEqual(record["removed_tag"], "Definition 1.3.2")
        self.assertNotIn("Definition 1.3.2", self.file.read_text(encoding="utf-8"))

    def test_a_path_outside_the_fixture_root_is_refused(self):
        outside = pathlib.Path(_REPO) / "SKILL.md"
        with self.assertRaises(CfError) as caught:
            mutate.check_writable(outside, fixture_root=self.root)
        message = caught.exception.render()
        self.assertIn("refusing to mutate", message)
        self.assertIn("--i-know", message)
        # and it really did not write
        self.assertIn("companion", outside.read_text(encoding="utf-8")[:4000].lower())

    def test_the_fixture_root_makes_a_path_writable(self):
        elsewhere = pathlib.Path(tmp_dir("cf-fixture-root-")) / "Notes"
        elsewhere.mkdir(parents=True)
        target = elsewhere / "Thing.lean"
        target.write_text(MutationClassTests.DEF, encoding="utf-8")
        self.assertEqual(mutate.check_writable(target, fixture_root=elsewhere), target.resolve())

    def test_i_know_overrides_the_guard(self):
        self.assertTrue(mutate.check_writable("/nowhere/Thing.lean", i_know=True))

    def test_the_cli_refuses_with_exit_code_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = mutate.main([str(pathlib.Path(_REPO) / "SKILL.md"),
                              "--class", "missing_item"])
        self.assertEqual(rc, 2)
        self.assertIn("--i-know", err.getvalue())

    def test_the_cli_prints_the_record_it_planted(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mutate.main([str(self.file), "--class", "proxy_definition",
                              "--object", "Lib.Jordan.jordanContent"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["class"], "proxy_definition")


# --------------------------------------------------------------------------
# the answer key
# --------------------------------------------------------------------------
class AnswerKeyTests(unittest.TestCase):
    """The key loads, validates, and refuses the shapes that would miscount."""

    def setUp(self):
        self.key = key_mod.load(ANSWER_KEY)

    def _write(self, entries, **top) -> str:
        path = pathlib.Path(tmp_dir("cf-key-")) / "key.json"
        path.write_text(json.dumps({"source_id": SOURCE_ID, "entries": entries, **top}),
                        encoding="utf-8")
        return str(path)

    def test_the_shipped_key_summarises_as_documented(self):
        summary = key_mod.summary(self.key)
        self.assertEqual(summary["defects"], 8)
        self.assertEqual(summary["decoys"], 3)
        self.assertEqual(summary["clean"], 28)
        self.assertEqual(summary["entries"], 36)
        self.assertEqual(summary["repos"], ["notes-a", "notes-b"])
        self.assertEqual(summary["defects_by_kind"],
                         {"CONTENT": 3, "COVERAGE": 1, "PAGE_ANCHOR": 1, "PROXY": 2,
                          "VACUITY": 1})

    def test_the_shipped_key_covers_the_documented_defect_shapes(self):
        classes = {e["class"] for e in key_mod.defects(self.key)}
        self.assertEqual(classes, {"strengthened_hypothesis", "vacuity", "dropped_dimension",
                                   "reversed_inequality", "proxy_definition", "missing_item",
                                   "wrong_page_anchor"})
        # every planted class is one the generator can plant
        self.assertTrue(classes <= set(mutate.MUTATIONS))

    def test_half_the_key_is_out_of_a_mechanical_detector_s_reach(self):
        """The asymmetry the score exists to measure, stated as an assertion."""
        mechanical = [e for e in key_mod.defects(self.key) if e["mechanical"]]
        self.assertEqual(len(mechanical), 4)
        self.assertEqual({e["kind"] for e in mechanical},
                         {"PROXY", "COVERAGE", "PAGE_ANCHOR"})
        model_only = [e for e in key_mod.defects(self.key) if not e["mechanical"]]
        self.assertEqual({e["kind"] for e in model_only}, {"CONTENT", "VACUITY"})

    def test_every_entry_explains_itself(self):
        for entry in self.key["entries"]:
            self.assertTrue(entry.get("why"), key_mod.entry_id(entry))

    def test_a_defect_with_no_kind_is_refused(self):
        path = self._write([{"repo": "notes-a", "item": "Definition 1.1.1", "expect": "defect"}])
        with self.assertRaises(CfError) as caught:
            key_mod.load(path)
        self.assertIn("names no kind", str(caught.exception))

    def test_a_clean_locus_with_a_kind_is_refused(self):
        path = self._write([{"repo": "notes-a", "item": "Definition 1.1.1",
                             "expect": "clean", "kind": "CONTENT"}])
        with self.assertRaises(CfError) as caught:
            key_mod.load(path)
        self.assertIn("must not be reported under ANY kind", str(caught.exception.hint))

    def test_two_entries_for_one_locus_are_refused(self):
        entry = {"repo": "notes-a", "item": "Definition 1.1.1", "expect": "defect",
                 "kind": "PROXY", "decls": ["notes-a:box"]}
        path = self._write([dict(entry), dict(entry)])
        with self.assertRaises(CfError) as caught:
            key_mod.load(path)
        self.assertIn("two entries", str(caught.exception))

    def test_an_unknown_kind_is_refused_by_the_shared_vocabulary(self):
        path = self._write([{"repo": "notes-a", "item": "Definition 1.1.1", "expect": "defect",
                             "kind": "WRONGNESS"}])
        with self.assertRaises(CfError):
            key_mod.load(path)

    def test_a_missing_source_id_is_refused(self):
        path = pathlib.Path(tmp_dir("cf-key-")) / "key.json"
        path.write_text(json.dumps({"entries": [
            {"repo": "notes-a", "item": "Definition 1.1.1", "expect": "clean"}]}),
            encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            key_mod.load(str(path))
        self.assertIn("source_id", str(caught.exception))

    def test_an_empty_key_is_refused(self):
        path = self._write([])
        with self.assertRaises(CfError):
            key_mod.load(path)


class AnswerKeyMatchesTheFixtureTests(unittest.TestCase):
    """The key and the fixture generator cannot drift apart unnoticed.

    A key that names an item the booklet does not print, or a declaration the
    companion does not carry, scores a fixture that no longer exists -- and the
    result looks exactly like a recall failure of whatever was being measured.
    """

    @classmethod
    def setUpClass(cls):
        cls.key = key_mod.load(ANSWER_KEY)
        cls.modules = {name: fixture.modules_for(name) for name in ("notes-a", "notes-b")}

    def _decls(self, name) -> list[str]:
        """Declarations the companion TAGS to a book item (post-mutation names)."""
        out = []
        for _module, text, _record in self.modules[name]:
            if not re.search(r"`[A-Z][A-Za-z]+ \d+(?:\.\d+)+`", text):
                continue                          # the tag was removed: nothing is tagged
            out.append(mutate.parse_declaration(text).name)
        return out

    def test_the_key_says_which_booklet_items_it_does_not_judge(self):
        """The boundary of the measurement, recorded rather than implied.

        The key judges the 18 items the companions formalise.  The booklet prints
        28, and a score quoted off this key is not a statement about the other
        10 -- so they are listed, and `cf score` flags a finding on one of them
        ``item_in_key: false`` instead of charging it against precision.
        """
        judged = {e["item"] for e in self.key["entries"]}
        self.assertEqual(judged, tagged_items("notes-a", "notes-b"))
        self.assertEqual(self.key["not_judged"]["items"],
                         [i for i in booklet_items() if i not in judged])
        self.assertTrue(self.key["not_judged"]["what"])

    def test_the_key_names_only_items_the_booklet_prints(self):
        problems = key_mod.check_against_items(
            self.key, booklet_items(),
            {name: self._decls(name) for name in self.modules})
        self.assertEqual(problems, [])

    def test_the_planted_records_are_exactly_the_key_s_defects(self):
        planted = {}
        for name, modules in self.modules.items():
            for _module, _text, record in modules:
                if record and record["kind"]:
                    planted[(name, record["item"], record["kind"])] = record["class"]
        keyed = {(e["repo"], e["item"], e["kind"]): e["class"]
                 for e in key_mod.defects(self.key)}
        self.assertEqual(planted, keyed)

    def test_the_decoys_are_planted_too_and_are_not_defects(self):
        renamed = [record for modules in self.modules.values()
                   for _m, _t, record in modules
                   if record and record["class"] == "convention_rename"]
        self.assertEqual(len(renamed), 1)
        self.assertEqual(renamed[0]["was_decl"], "measure_well_defined")
        decoys = {e["class"] for e in key_mod.decoys(self.key)}
        self.assertEqual(decoys, {"convention_rename", "convention_alias"})

    def test_every_clean_locus_really_is_tagged_in_that_repo(self):
        for entry in key_mod.clean(self.key):
            have = set(self._decls(entry["repo"]))
            for decl in entry["decls"]:
                self.assertIn(decl.split(":", 1)[1], have, key_mod.entry_id(entry))

    def test_the_key_and_the_fixture_agree_on_which_pages_are_wrong(self):
        """Exactly one anchor in the fixture disagrees with the booklet."""
        printed = {item: page for item, page, _d, _s in fixture.SHARED}
        printed.update({item: page for item, page, _d, _s in fixture.A_ONLY + fixture.B_ONLY})
        wrong = []
        for name, modules in self.modules.items():
            for _module, text, _record in modules:
                tag = re.search(r"`(?P<item>[A-Z][A-Za-z]+ \d+(?:\.\d+)+)`", text)
                page = re.search(r"Book p\.[ \t]*(\d+)", text)
                if not tag or not page:
                    continue
                if int(page.group(1)) != printed[tag.group("item")]:
                    wrong.append((name, tag.group("item")))
        self.assertEqual(wrong, [("notes-b", "Exercise 1.4.4")])

    def test_the_fixture_pages_are_the_pages_the_booklet_prints(self):
        """Read off make_pdf's own layout, so the two generators cannot drift."""
        pages = {}
        page_no = 0
        for lines in make_pdf.PAGES[2:]:
            page_no += 1
            for line in lines:
                found = re.match(r"^(?P<item>[A-Z][A-Za-z]+ \d+(?:\.\d+)+)", line)
                if found:
                    pages.setdefault(found.group("item"), page_no)
        for item, page, _decl, _statement in fixture.SHARED:
            self.assertEqual(page, pages[item], item)


# --------------------------------------------------------------------------
# the arithmetic of the score
# --------------------------------------------------------------------------
class ScoreArithmeticTests(unittest.TestCase):
    """Precision, recall and the join, on rows built by hand."""

    KEY = {
        "source_id": SOURCE_ID,
        "entries": [
            {"source_id": SOURCE_ID, "repo": "notes-b", "item": "Definition 1.3.2",
             "kind": "PROXY", "expect": "defect", "class": "proxy_definition",
             "decls": ["notes-b:jordanOuter"], "why": "x"},
            {"source_id": SOURCE_ID, "repo": "notes-b", "item": "Exercise 1.3.7",
             "kind": "VACUITY", "expect": "defect", "class": "vacuity",
             "decls": ["notes-b:not_jordan"], "why": "x"},
            {"source_id": SOURCE_ID, "repo": "notes-b", "item": "Lemma 1.2.2",
             "expect": "clean", "class": "convention_rename",
             "decls": ["notes-b:measureWellDefined"], "why": "a spelling"},
        ],
    }

    def _row(self, repo, item, kind, run="r1", decls=()):
        return {"run": run, "source_id": SOURCE_ID, "repo": repo, "item": item,
                "kind": kind, "decls": list(decls), "severity": "medium"}

    def test_a_perfect_run_scores_one(self):
        rows = [self._row("notes-b", "Definition 1.3.2", "PROXY"),
                self._row("notes-b", "Exercise 1.3.7", "VACUITY")]
        result = score_mod.score(rows, self.KEY)
        self.assertEqual(result["totals"]["precision"], 1.0)
        self.assertEqual(result["totals"]["recall"], 1.0)
        self.assertEqual(result["totals"]["f1"], 1.0)
        self.assertEqual(result["misses"], [])

    def test_a_miss_is_a_false_negative_and_is_named(self):
        rows = [self._row("notes-b", "Definition 1.3.2", "PROXY")]
        result = score_mod.score(rows, self.KEY)
        self.assertEqual(result["totals"]["recall"], 0.5)
        self.assertEqual(result["totals"]["precision"], 1.0)
        self.assertEqual([m["item"] for m in result["misses"]], ["Exercise 1.3.7"])
        self.assertEqual(result["by_kind"]["VACUITY"]["false_negatives"], 1)

    def test_a_finding_on_a_decoy_is_a_false_positive_and_says_which(self):
        rows = [self._row("notes-b", "Lemma 1.2.2", "CONTENT")]
        result = score_mod.score(rows, self.KEY)
        self.assertEqual(result["totals"]["precision"], 0.0)
        self.assertEqual(result["decoys_reported"], 1)
        fp = result["false_positives"][0]
        self.assertTrue(fp["on_decoy"])
        self.assertEqual(fp["decoy_class"], "convention_rename")
        self.assertEqual(fp["why_clean"], "a spelling")

    def test_the_right_item_under_the_wrong_kind_is_not_a_hit(self):
        """A PROXY defect reported as COVERAGE is a different claim."""
        rows = [self._row("notes-b", "Definition 1.3.2", "COVERAGE")]
        result = score_mod.score(rows, self.KEY)
        self.assertEqual(result["totals"]["true_positives"], 0)
        self.assertEqual(result["totals"]["false_positives"], 1)

    def test_a_repo_the_key_never_names_is_flagged_not_silently_charged(self):
        rows = [self._row("notes-a", "Definition 1.3.2", "PROXY")]
        result = score_mod.score(rows, self.KEY)
        self.assertFalse(result["false_positives"][0]["repo_in_key"])

    def test_an_item_the_key_never_judges_is_flagged_not_silently_charged(self):
        """A key covers the loci it lists; a number off it is about those loci.

        Here the repo IS in the key and the item is not, which is the case a
        partial key produces: a COVERAGE row about a book item no companion was
        ever expected to formalise.
        """
        rows = [self._row("notes-b", "Remark 1.1.7", "COVERAGE")]
        result = score_mod.score(rows, self.KEY)
        fp = result["false_positives"][0]
        self.assertTrue(fp["repo_in_key"])
        self.assertFalse(fp["item_in_key"])
        self.assertEqual(result["not_judged_reported"], 1)
        self.assertIn("the answer key does not judge this item", score_mod.render(result))
        # a locus the key does judge is not flagged
        judged = score_mod.score([self._row("notes-b", "Lemma 1.2.2", "CONTENT")], self.KEY)
        self.assertTrue(judged["false_positives"][0]["item_in_key"])
        self.assertEqual(judged["not_judged_reported"], 0)

    def test_two_runs_reporting_one_defect_are_one_true_positive(self):
        """The join has no run id in it, which is what makes a key reusable."""
        rows = [self._row("notes-b", "Definition 1.3.2", "PROXY", run="fleet-1"),
                self._row("notes-b", "Definition 1.3.2", "PROXY", run="fleet-2")]
        result = score_mod.score(rows, self.KEY)
        self.assertEqual(result["totals"]["true_positives"], 1)
        self.assertEqual(result["loci_reported"], 1)
        self.assertEqual(result["rows_read"], 2)
        self.assertEqual(result["hits"][0]["runs"], ["fleet-1", "fleet-2"])

    def test_a_sub_part_label_joins_the_item_it_belongs_to(self):
        rows = [self._row("notes-b", "Exercise 1.3.7(iii)", "VACUITY")]
        result = score_mod.score(rows, self.KEY)
        self.assertEqual(result["totals"]["true_positives"], 1)

    def test_a_hit_records_whether_the_declaration_agrees(self):
        right = score_mod.score([self._row("notes-b", "Definition 1.3.2", "PROXY",
                                           decls=["notes-b:jordanOuter"])], self.KEY)
        self.assertTrue(right["hits"][0]["decls_agree"])
        wrong = score_mod.score([self._row("notes-b", "Definition 1.3.2", "PROXY",
                                           decls=["notes-b:somethingElse"])], self.KEY)
        self.assertTrue(wrong["hits"][0])          # still a hit
        self.assertFalse(wrong["hits"][0]["decls_agree"])

    def test_precision_is_null_when_nothing_was_reported(self):
        result = score_mod.score([], self.KEY)
        self.assertIsNone(result["totals"]["precision"])
        self.assertEqual(result["totals"]["recall"], 0.0)
        self.assertIn("-", score_mod.render(result))

    def test_metrics_has_no_denominator_it_cannot_justify(self):
        self.assertEqual(score_mod.metrics(0, 0, 0),
                         {"true_positives": 0, "false_positives": 0, "false_negatives": 0,
                          "reported": 0, "planted": 0, "precision": None, "recall": None,
                          "f1": None})
        self.assertEqual(score_mod.metrics(1, 1, 2)["precision"], 0.5)
        self.assertEqual(score_mod.metrics(1, 1, 2)["recall"], 0.3333)

    def test_the_table_names_the_misses_and_the_decoys(self):
        rows = [self._row("notes-b", "Lemma 1.2.2", "CONTENT")]
        table = score_mod.render(score_mod.score(rows, self.KEY))
        self.assertIn("missed (planted, not reported)", table)
        self.assertIn("on the convention_rename decoy", table)

    def test_rows_take_the_repo_from_repos_involved_when_no_decl_names_one(self):
        """A COVERAGE row for an untagged definition names no declaration."""
        corpus = pathlib.Path(tmp_dir("cf-rows-"))
        (corpus / "item.json").write_text(json.dumps({
            "source_id": SOURCE_ID, "item": "Definition 1.4.1",
            "disagreements": [{"kind": "COVERAGE", "unfaithful_decls": [],
                               "repos_involved": ["notes-b"], "severity": "medium"}],
        }), encoding="utf-8")
        rows = rows_mod.rows_from_dir(corpus)
        self.assertEqual([(r["repo"], r["kind"]) for r in rows], [("notes-b", "COVERAGE")])

    def test_a_findings_file_that_is_not_an_object_is_refused(self):
        corpus = pathlib.Path(tmp_dir("cf-rows-"))
        (corpus / "broken.json").write_text("null", encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            rows_mod.rows_from_dir(corpus)
        self.assertIn("not a findings object", str(caught.exception))

    def test_a_receipt_is_not_counted_as_a_finding(self):
        corpus = pathlib.Path(tmp_dir("cf-rows-"))
        (corpus / "RECEIPT.json").write_text(json.dumps({"actual": {"count": 0}}),
                                             encoding="utf-8")
        self.assertEqual(rows_mod.rows_from_dir(corpus), [])


# --------------------------------------------------------------------------
# stage 1 over synthetic TEXT: no PDF, no poppler
# --------------------------------------------------------------------------
class SyntheticSourceTextTests(unittest.TestCase):
    """``prepare()`` and the item grammar, over form-feed separated text.

    ``prepare()`` shells out to ``pdftotext`` once and does everything else in
    Python; the stub stands in for that one call, so the orchestrator, the page
    split, the offset inference, the kind inference, ``pages/`` and
    ``items.json`` all run on a machine with no PDF reader.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = booklet_text()
        cls.root = pathlib.Path(tmp_dir("cf-prepare-"))
        cls.pdf = cls.root / "booklet.pdf"
        cls.pdf.write_bytes(make_pdf.build())
        cls.out = cls.root / "source"
        cls.inferred_out = cls.root / "source-inferred"
        with cls._stub():
            cls.payload = prepare_source.prepare(
                str(cls.pdf), str(cls.out), source_id=SOURCE_ID,
                kinds=prepare_source.SOURCE_KINDS)
            # the same text with the kind list INFERRED rather than supplied
            cls.inferred = prepare_source.prepare(
                str(cls.pdf), str(cls.inferred_out), source_id=SOURCE_ID)

    @classmethod
    def _stub(cls):
        def fake(pdf, out_txt, layout=True):  # noqa: ARG001 - the stub's signature
            pathlib.Path(out_txt).write_text(cls.text, encoding="utf-8")
            return cls.text
        return mock.patch.object(prepare_source, "pdftotext", fake)

    def test_the_booklet_generator_is_byte_deterministic(self):
        import hashlib

        self.assertEqual(hashlib.sha256(make_pdf.build()).hexdigest(), BOOKLET_SHA)
        self.assertEqual(hashlib.sha256(make_pdf.build()).hexdigest(),
                         hashlib.sha256(self.pdf.read_bytes()).hexdigest())

    def test_the_offset_is_inferred_from_the_running_heads(self):
        self.assertEqual(self.payload["offset"], 2)
        self.assertEqual(self.payload["offset_method"], "running_head_mode")
        self.assertEqual(self.payload["offset_confidence"], 1.0)
        self.assertEqual(self.payload["pages"], 12)

    def test_the_kind_inference_reports_only_the_kinds_the_text_supports(self):
        """A ten-page booklet has one Proposition, so inference does not see it.

        Not a defect: `infer_kinds` refuses to promote a kind on one heading, and
        a source too short to infer from is why `prepare` takes a kind list at
        all.  `cf onboard` supplies the wider grammar, which is why the workspace
        ends up with 28 items and this call with 18.
        """
        self.assertEqual(self.inferred["kinds"], ["Definition", "Exercise", "Lemma"])
        self.assertEqual(self.inferred["items"], 18)
        self.assertEqual(self.payload["items"], 28)

    def test_every_item_is_found_and_placed_on_its_printed_page(self):
        items = prepare_source.load_items(str(self.out))
        self.assertEqual(items["count"], 28)
        by_item = {r["item"]: r for r in items["items"]}
        self.assertEqual(sorted(by_item), sorted(booklet_items()))
        self.assertEqual(by_item["Exercise 1.4.4"]["page"], 10)
        self.assertEqual(by_item["Exercise 1.4.4"]["pdf_page"], 12)
        self.assertEqual(by_item["Definition 1.1.1"]["page"], 1)

    def test_pages_are_written_one_file_per_pdf_page(self):
        self.assertTrue((self.out / "pages" / "p001.txt").exists())
        self.assertEqual(len(list((self.out / "pages").iterdir())), 12)

    def test_a_second_run_is_a_no_op(self):
        with self._stub():
            again = prepare_source.prepare(str(self.pdf), str(self.out), source_id=SOURCE_ID,
                                           kinds=prepare_source.SOURCE_KINDS)
        self.assertTrue(again["skipped"])

    def test_the_source_gate_refuses_another_edition(self):
        with self._stub(), self.assertRaises(ValueError) as caught:
            prepare_source.prepare(str(self.pdf), str(self.out), source_id=SOURCE_ID,
                                   kinds=prepare_source.SOURCE_KINDS, expect_sha="f" * 64)
        self.assertIn("source gate", str(caught.exception))

    def test_the_28_items_survive_irregular_whitespace_and_a_tab(self):
        """The 411-vs-410 trap, on data anyone can generate.

        The synthetic twin of the pinned Chapter-1 count: what the private tier
        asserts as a magnitude, this asserts as the invariant behind it.
        """
        mangled = re.sub(r"(?m)^(Exercise|Definition|Lemma|Theorem) (\d)",
                         lambda m: "%s   \t %s" % (m.group(1), m.group(2)), self.text)
        found = prepare_source.find_items(mangled, offset=2)
        self.assertEqual(len({r["item"] for r in found}), 28)
        single_space = set()
        for kind in prepare_source.SOURCE_KINDS:   # the baseline's own regex
            single_space |= {m.group(1) for m in
                             re.finditer(r"(?:^|\n)\s*(%s 1\.\d+\.\d+)" % kind, mangled)}
        self.assertLess(len(single_space), 28)     # which is how 411 became 410

    def test_an_item_that_starts_a_page_is_still_found(self):
        """pdftotext writes a form feed BETWEEN pages, not a newline."""
        text = "Definition 1.1.1. A box.\n\f Lemma 1.1.2. Boxes are elementary.\n"
        self.assertEqual([r["item"] for r in prepare_source.find_items(text)],
                         ["Definition 1.1.1", "Lemma 1.1.2"])

    def test_pdftotext_says_which_package_provides_it(self):
        with mock.patch.object(prepare_source.shutil, "which", lambda _exe: None):
            with self.assertRaises(RuntimeError) as caught:
                prepare_source.pdftotext(str(self.pdf), str(self.root / "x.txt"))
        self.assertIn("poppler", str(caught.exception))

    def test_page_rendering_refuses_without_poppler_and_without_a_pdf(self):
        with mock.patch.object(render_pages.shutil, "which", lambda _exe: None):
            with self.assertRaises(RuntimeError) as caught:
                render_pages.render(str(self.pdf), str(self.out), [1])
        self.assertIn("pdftoppm", str(caught.exception))
        with mock.patch.object(render_pages.shutil, "which", lambda _exe: "/usr/bin/pdftoppm"):
            with self.assertRaises(FileNotFoundError):
                render_pages.render(str(self.root / "nope.pdf"), str(self.out), [1])

    def test_page_ranges_collapse_to_what_pdftoppm_is_asked_for(self):
        self.assertEqual(render_pages._ranges([3, 1, 2, 9]), [(1, 3), (9, 9)])


# --------------------------------------------------------------------------
# the whole flow, by default, on the synthetic fixture
# --------------------------------------------------------------------------
@unittest.skipUnless(shutil.which("git"), "the fixture companions are git repos")
class SyntheticPipelineTests(unittest.TestCase):
    """`cf onboard` -> `cf pair` -> `cf detect-proxies` -> `cf score`, with no PDF reader.

    This is the tier the review found missing: ``prepare_source.prepare()``,
    ``pair/make_queue.py``, ``anchor_check.check()`` and ``scripts/cfcmd/
    pair.py`` had no default coverage at all, and they are the code that decides
    which items exist and which page a worker opens.  Everything here runs on
    the booklet's synthetic text with ``pdftotext`` stubbed out, and the numbers
    it asserts are the ones ``tests/e2e/synthetic.sh`` gets from the real
    pdftotext on the real PDF.
    """

    @classmethod
    def setUpClass(cls):
        text = booklet_text()

        def fake_pdftotext(pdf, out_txt, layout=True):  # noqa: ARG001 - stub signature
            pathlib.Path(out_txt).write_text(text, encoding="utf-8")
            return text

        cls.root = pathlib.Path(tmp_dir("cf-synthetic-"))
        # `cf onboard` expands the workspace's home-relative library path and
        # fingerprints every PDF it finds there.  tests/conftest.py already
        # points HOME at a temp directory, but `python3 -m unittest discover`
        # does not load that hook, and this class is the one that onboards.
        cls._home = mock.patch.dict(os.environ, {"HOME": str(cls.root)})
        cls._home.start()
        pdf = cls.root / "elementary-notes.pdf"
        pdf.write_bytes(make_pdf.build())
        with contextlib.redirect_stdout(io.StringIO()):   # the generator's own report
            cls.planted = fixture.build(str(cls.root), str(pdf))
        cls.ws = cls.root / "ws"
        assert run_cf("init", str(cls.ws), "--no-git", "-q")[0] == 0
        with mock.patch.object(prepare_source, "pdftotext", fake_pdftotext):
            for name in ("notes-a", "notes-b"):
                rc, out = run_cf("onboard", str(cls.root / name), "--no-errata", "-q",
                                 "--workspace", str(cls.ws))
                assert rc == 0, out
            cls.pair_rc, cls.pair_out = run_cf("pair", "--workspace", str(cls.ws))
            for name in ("notes-a", "notes-b"):
                rc, out = run_cf("detect-proxies", name, "--stages", str(STAGE_TABLE),
                                 "-q", "--workspace", str(cls.ws))
                assert rc == 0, out
            cls.score_rc, cls.score_out = run_cf(
                "score", "--run", "detect-notes-a-cheap", "--run", "detect-notes-b-cheap",
                "--run", "anchor-check-%s" % SOURCE_ID,
                "--answer-key", str(ANSWER_KEY), "--workspace", str(cls.ws))

    @classmethod
    def tearDownClass(cls):
        cls._home.stop()

    def _json(self, *parts):
        return json.loads((self.ws.joinpath(*parts)).read_text(encoding="utf-8"))

    # -- discovery and stage 1 --------------------------------------------
    def test_nothing_about_the_source_was_typed_in(self):
        meta = self._json("sources", SOURCE_ID, "meta.json")
        self.assertEqual(meta["offset"], 2)
        self.assertEqual(meta["offset_method"], "running_head_mode")
        self.assertEqual(meta["sha256"], BOOKLET_SHA)

    def test_the_key_was_written_against_the_edition_this_workspace_pins(self):
        key = key_mod.load(ANSWER_KEY)
        self.assertEqual(key["source_sha"],
                         self._json("sources", SOURCE_ID, "meta.json")["sha256"])

    # -- pair --------------------------------------------------------------
    def test_pair_writes_one_tree_for_the_one_source(self):
        self.assertEqual(self.pair_rc, 0)
        recon = self._json("pairs", SOURCE_ID, "reconciliation.json")
        self.assertEqual(recon["items_in_book"], 28)
        self.assertEqual(recon["items_in_scope"], 28)
        self.assertEqual(recon["pairs"], 17)
        self.assertEqual(recon["repos"], ["notes-a", "notes-b"])
        self.assertEqual(recon["source_sha"], BOOKLET_SHA)

    def test_the_task_universe_is_the_items_at_least_one_repo_formalises(self):
        recon = self._json("pairs", SOURCE_ID, "reconciliation.json")
        self.assertEqual(recon["task_universe"], recon["items_in_scope_and_any_repo"])
        self.assertEqual(recon["task_universe"], 18)

    def test_the_queue_carries_a_page_hint_per_item(self):
        queue = self._json("pairs", SOURCE_ID, "queue.json")
        self.assertEqual(queue["count"], 17)
        self.assertEqual(queue["with_page_hints"], 17)
        self.assertEqual(queue["source_id"], SOURCE_ID)

    def test_targets_are_written_per_companion(self):
        # 18 against notes-a and 17 against notes-b: the planted missing_item is
        # a book Definition notes-b tags nothing to, so it has one target fewer.
        for name, count in (("notes-a", 18), ("notes-b", 17)):
            targets = self._json("pairs", SOURCE_ID, "targets.%s.json" % name)
            self.assertEqual(targets["companion"], name)
            self.assertEqual(targets["source_sha"], BOOKLET_SHA)
            self.assertEqual(targets["count"], count)

    def test_def_coverage_sees_the_definition_notes_b_never_states(self):
        a = self._json("pairs", SOURCE_ID, "def-coverage.notes-a.json")
        b = self._json("pairs", SOURCE_ID, "def-coverage.notes-b.json")
        self.assertEqual(len(a["stated"]), 7)
        self.assertEqual(a["missing"], [])
        self.assertEqual(len(b["stated"]), 6)
        self.assertEqual(b["missing"], ["Definition 1.4.1"])

    def test_exactly_the_planted_anchor_is_flagged(self):
        anchors = self._json("pairs", SOURCE_ID, "anchor-check.json")
        self.assertEqual(anchors["summary"]["findings"], 1)
        self.assertEqual(anchors["summary"]["advisory"], 0)
        row = anchors["rows"][0]
        self.assertEqual((row["repo"], row["item"], row["kind"]),
                         ("notes-b", "Exercise 1.4.4", "PAGE_ANCHOR"))
        self.assertEqual(row["page"], {"claimed": [4], "actual": 10, "actual_pdf_page": 12})
        self.assertEqual(row["source_sha"], BOOKLET_SHA)

    # -- score -------------------------------------------------------------
    def test_the_model_free_detector_scores_exactly_what_the_key_records(self):
        self.assertEqual(self.score_rc, 0)
        recorded = key_mod.load(ANSWER_KEY)["recorded_scores"]["mechanical"]
        totals = self._json("reports", "score.all.json")["totals"]
        self.assertEqual(totals["precision"], recorded["precision"])
        self.assertEqual(totals["recall"], recorded["recall"])
        self.assertEqual(totals["f1"], recorded["f1"])
        self.assertEqual((totals["true_positives"], totals["false_positives"],
                          totals["false_negatives"]), (4, 0, 4))

    def test_it_finds_every_defect_of_the_kinds_it_can_see(self):
        by_kind = self._json("reports", "score.all.json")["by_kind"]
        for kind in ("PROXY", "COVERAGE", "PAGE_ANCHOR"):
            self.assertEqual(by_kind[kind]["recall"], 1.0, kind)
            self.assertEqual(by_kind[kind]["precision"], 1.0, kind)

    def test_and_none_of_the_kinds_it_cannot(self):
        """The measurement the repo had no instrument for."""
        result = self._json("reports", "score.all.json")
        for kind in ("CONTENT", "VACUITY"):
            self.assertEqual(result["by_kind"][kind]["recall"], 0.0, kind)
            self.assertIsNone(result["by_kind"][kind]["precision"], kind)
        self.assertEqual({m["kind"] for m in result["misses"]}, {"CONTENT", "VACUITY"})

    def test_it_walks_into_none_of_the_decoys(self):
        result = self._json("reports", "score.all.json")
        self.assertEqual(result["decoys_reported"], 0)
        self.assertEqual(result["false_positives"], [])

    def test_the_score_is_reported_per_defect_class(self):
        by_class = self._json("reports", "score.all.json")["by_class"]
        self.assertEqual(by_class["proxy_definition"], {"planted": 2, "caught": 2,
                                                        "kind": "PROXY", "recall": 1.0})
        self.assertEqual(by_class["vacuity"]["caught"], 0)

    def test_the_generator_planted_what_the_key_says_it_planted(self):
        planted = {(r["repo"], r["item"], r["kind"]) for rows in self.planted.values()
                   for r in rows if r["kind"]}
        keyed = {(e["repo"], e["item"], e["kind"])
                 for e in key_mod.defects(key_mod.load(ANSWER_KEY))}
        self.assertEqual(planted, keyed)

    def test_scoring_nothing_is_refused_with_the_flag_that_fixes_it(self):
        with self.assertRaises(CfError) as caught:
            run_cf("score", "--answer-key", str(ANSWER_KEY), "--workspace", str(self.ws))
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn("--run", caught.exception.hint)

    def test_an_unknown_run_is_refused(self):
        with self.assertRaises(CfError) as caught:
            run_cf("score", "--run", "no-such-run", "--answer-key", str(ANSWER_KEY),
                   "--workspace", str(self.ws))
        self.assertIn("no-such-run", str(caught.exception))

    def test_a_corpus_scoring_is_named_after_the_corpus(self):
        """Two `--corpus` scorings in one workspace must not overwrite each other.

        The report used to be ``score.all.json`` for anything but a single
        ``--run``, so the second corpus silently replaced the first.
        """
        copy = pathlib.Path(tmp_dir("cf-corpus-")) / "notes-a-mechanical"
        shutil.copytree(self.ws / "corpus" / "detect-notes-a-cheap", copy)
        rc, _out = run_cf("score", "--corpus", str(copy), "--answer-key",
                          str(ANSWER_KEY), "--workspace", str(self.ws), "-q")
        self.assertEqual(rc, 0)
        named = self.ws / "reports" / "score.notes-a-mechanical.json"
        self.assertTrue(named.exists(), sorted(p.name for p in (self.ws / "reports").iterdir()))
        self.assertEqual(json.loads(named.read_text(encoding="utf-8"))["totals"]
                         ["true_positives"], 1)

    def test_fail_under_turns_a_drop_into_a_nonzero_exit(self):
        rc, _out = run_cf("score", "--run", "detect-notes-b-cheap", "--answer-key",
                          str(ANSWER_KEY), "--workspace", str(self.ws), "--no-write",
                          "--fail-under", "0.9")
        self.assertEqual(rc, 1)

    def test_one_companion_s_run_scores_only_what_it_could_have_found(self):
        """Scoring one detector run leaves the other repo's defects as misses."""
        rc, out = run_cf("score", "--run", "detect-notes-a-cheap", "--answer-key",
                         str(ANSWER_KEY), "--workspace", str(self.ws), "--json")
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertEqual(result["totals"]["true_positives"], 1)
        self.assertEqual(result["totals"]["false_positives"], 0)
        self.assertEqual(result["totals"]["planted"], 8)


# --------------------------------------------------------------------------
# the machine-readable record of the pinned figures
# --------------------------------------------------------------------------
class BaselineFiguresRecordTests(unittest.TestCase):
    """tests/baseline/regression-figures.json, and that it has not drifted.

    The figures are facts about one private corpus and one edition, so they can
    never be re-derived by a stranger.  Recording them makes them reviewable and
    diffable; checking each one against the test that asserts it is what stops
    the record and the suite disagreeing.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(FIGURES.read_text(encoding="utf-8"))
        cls.figures = cls.doc["figures"]

    def test_every_figure_says_what_it_is_and_where_it_is_asserted(self):
        for name, figure in self.figures.items():
            self.assertIsInstance(figure["value"], int, name)
            self.assertTrue(figure["what"], name)
            self.assertTrue(figure["needs"], name)
            self.assertTrue(figure["asserted_in"], name)
            for path in figure["asserted_in"]:
                self.assertTrue((pathlib.Path(_REPO) / path).exists(), path)

    def test_the_headline_figures_are_all_recorded(self):
        for name in ("chapter_1_items", "differential_task_universe", "corpus_findings",
                     "ours_rows", "ours_raw_items", "ours_raw_substantive", "ours_raw_high",
                     "teorth_rows", "adam_rows", "def_coverage_stated",
                     "def_coverage_bridge_only", "def_coverage_missing"):
            self.assertIn(name, self.figures)
        self.assertEqual(self.figures["chapter_1_items"]["value"], 411)
        self.assertEqual(self.figures["differential_task_universe"]["value"], 383)
        self.assertEqual(self.figures["corpus_findings"]["value"], 2672)

    def test_each_recorded_figure_still_appears_in_the_test_that_pins_it(self):
        """The record and the asserts move together or a test fails."""
        for name, figure in self.figures.items():
            pattern = re.compile(r"(?<![\d.])%d(?![\d.])" % figure["value"])
            for path in figure["asserted_in"]:
                text = (pathlib.Path(_REPO) / path).read_text(encoding="utf-8")
                self.assertRegex(text, pattern, "%s: %s no longer carries %d"
                                 % (name, path, figure["value"]))

    def test_the_two_traps_are_recorded_as_such(self):
        self.assertIn("410", self.doc["rule"])
        self.assertIn("382", self.doc["rule"])
        self.assertEqual(self.figures["chapter_1_items_single_space_regex"]["value"], 410)

    @unittest.skipUnless(os.environ.get("CF_BASELINE_CORPUS"), "CF_BASELINE_CORPUS unset")
    def test_the_corpus_size_re_derives_from_the_corpus(self):
        root = pathlib.Path(os.environ["CF_BASELINE_CORPUS"])
        total = sum(len(list((root / d).glob("*.json")))
                    for d in ("findings", "findings-gpt", "findings-pdf",
                              "findings-hunt", "findings-vac"))
        self.assertEqual(total, self.figures["corpus_findings"]["value"])

    @unittest.skipUnless(os.environ.get("CF_BOOK_TXT"), "CF_BOOK_TXT unset")
    def test_the_chapter_one_count_re_derives_from_the_book_text(self):
        text = pathlib.Path(os.environ["CF_BOOK_TXT"]).read_text(
            encoding="utf-8", errors="ignore")
        items = prepare_source.find_items(text, offset=16)
        chapter_one = {r["item"] for r in items if r["chapter"] == 1}
        self.assertEqual(len(chapter_one), self.figures["chapter_1_items"]["value"])
        self.assertIn("Exercise 1.3.19", chapter_one)


if __name__ == "__main__":  # pragma: no cover - module entry point
    unittest.main()
