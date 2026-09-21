"""Final-review fixes for the counting authority and the handoff (stdlib unittest).

Six findings, each reproduced against the code before it was changed:

* FR#1 -- a source the companion wrote ITSELF (``items.json`` empty on purpose)
  got a permanently zero coverage denominator: every figure in section 0 was an
  intersection with the empty set, so a fully audited manuscript read "0 of 0
  item(s) in scope examined (0 in the source)" under an opening paragraph
  claiming "Every item of every source under audit ... was examined", and the
  two scope cells named ``cf pair && cf harvest``, which provably cannot fill
  them.  The universe now comes from ``pairs/<id>/index.json``, and a zero or
  unknown whole can no longer reach the clean-bill sentence.
* FR#10 -- a corpus document carrying its rows under ``findings`` with no
  top-level ``item`` was counted by `cf tally` and invisible to `cf report`, and
  a run whose documents were all dropped was never named as ungated at all.
* FR#19 -- ``items_with_findings`` read ``disagreements`` alone while every
  sibling reader of the same corpus reads both defect keys.
* FR#20 -- an empty ``chapters_audited`` means "all", and the table rendered it
  as a TODO two lines above prose that rendered it as "all".
* FR#22 -- the ungated hints named ``--no-gate`` (never passed on the path that
  produces the warning) and a bare ``cf harvest <run-id>`` (which refuses an
  imported run).  Both branches of the replacement are run here.
* FR#26 -- a gate that RAN and rejected was indistinguishable from one that
  passed: `cf tally`, the authority every number comes from, said nothing.

and the four the confirmation pass found while attacking those fixes:

* FR#1 again, on the two nearest variants -- a manuscript whose labels need the
  source's OWN numbering to parse at all, and one whose workspace pins chapters.
  The first restored "0 of 0 item(s) in scope examined" on a fully examined
  source; the second dropped every planned item outside the pin, findings and
  all, although `cf plan-run` never narrows a manuscript's task universe.
* a corpus document `cf tally` REFUSES to read (exit 2, "every number a lower
  bound") was skipped here without a word, so `cf report` published the lower
  bound as a figure and called the item behind it never examined.
* an examined item outside the audited chapters vanished from every section-0
  figure INCLUDING ``items_with_findings``, with nothing in the document saying
  so, while section 3 counted its rows.
* the per-chapter "in task universe" cell printed a hard 0 for a figure nothing
  had computed, where the total row printed "—".

and four narrow shapes the review of those fixes reproduced, each one a place
where a sentence above was said wrongly or not at all: the line summarising
section 0 described both kinds of excluded document as one and claimed §3
counts a document `cf tally` refuses; the lower-bound sentence went unsaid in
every shape whose note ends early; two of the three name lists were uncapped
where the third caps at eight; and a reconciliation that names a task universe
it cannot itemise still printed 0 in every per-chapter row.

And two the second confirmation pass reproduced: a corpus run attributed to a
source the workspace no longer holds was dropped from every section-0 figure
and from coverage.json while `cf tally` counted it; and four of the five
caveats went unsaid on the three branches whose per-source note ends early --
which includes a run that names no single source, whose gate state had no
reader at all.

The book, the companion and the findings below are invented: no source text and
no companion's Lean lives in this repo (CLAUDE.md S1).  Every test here runs
without the private baselines.
"""

from __future__ import annotations

# Pin HOME before anything builds a workspace: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
# See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import io
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))

import cf as cf_main  # noqa: E402
from cflib import items as cf_items  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from harvest import harvest as harvest_mod  # noqa: E402
from harvest import tally as tally_mod  # noqa: E402
from ledger import compat  # noqa: E402
from report import make_handoff as handoff_mod  # noqa: E402

TMPDIR = os.environ.get("CF_TMPDIR") or None

SOURCE_ID = "a-book-1st-edition"
SOURCE_SHA = "a" * 64
#: an earlier printing the workspace keeps and no companion is bound to
OTHER_SOURCE_ID = "a-book-2nd-edition"
OTHER_SOURCE_SHA = "b" * 64
COMPANION = "ours"
#: a sentence a finding quotes; invented, not a line of any real book
QUOTE = ("Show that the union of two elementary sets is elementary, and that "
         "the intersection is elementary as well.")


def defect(**extra) -> dict:
    row = {"kind": "CONTENT", "severity": "high", "concept": "the concept",
           "unfaithful_decls": ["%s:Book.foo" % COMPANION], "repos_involved": [COMPANION],
           "faithful": [], "reasoning": "the hypothesis is dropped",
           "suggested_fix": "add it", "book_quote": QUOTE}
    row.update(extra)
    return row


def document(item: str = "", *, key: str = "disagreements", rows: int = 1) -> dict:
    """One corpus document, with its defect rows under *key*.

    ``item`` empty means a document that carries no top-level ``item`` at all --
    the shape `cf import-corpus` lands for a v1 file named after its task.
    """
    doc: dict = {"source_id": SOURCE_ID, "source_sha": SOURCE_SHA, "matches": [],
                 "coverage_notes": "", "book_located": True, "disagreements": []}
    if item:
        doc["item"] = item
    doc[key] = [defect(item=item) for _ in range(rows)]
    return doc


class Workspace(unittest.TestCase):
    """A one-source, one-companion workspace on disk. No CLI, no network."""

    #: "" narrows nothing, so the whole source is in scope
    CHAPTERS = ""
    #: extra ``sources:`` entries, for a row no companion is bound to
    EXTRA_SOURCES = ""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cf-rf-final-", dir=TMPDIR))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo, exist_ok=True)
        compat.write_text(os.path.join(self.root, "workspace.yaml"),
                          "version: 2\n"
                          "workspace:\n  name: rf\n"
                          "companions:\n"
                          "  %s:\n"
                          "    repo: %s\n"
                          "    adapter: attribution_table\n"
                          "    owner: examplebox/%s\n"
                          "    sources: [%s]\n"
                          "    anchor_source: %s\n"
                          "sources:\n"
                          "  %s:\n"
                          '    sha256: "%s"\n'
                          "    title: A Book\n"
                          '    edition: "first edition"\n'
                          "    offset: 16\n"
                          "%s"
                          "    role: audited\n"
                          "%s"
                          "hosts:\n  allowlist: []\n"
                          % (COMPANION, self.repo, COMPANION, SOURCE_ID, SOURCE_ID,
                             SOURCE_ID, SOURCE_SHA, self.CHAPTERS, self.EXTRA_SOURCES))
        self.paths = compat.workspace_paths(self.root)

    # -- fixture writers -------------------------------------------------
    def write_items(self, rows, source_id: str = SOURCE_ID, sha: str = SOURCE_SHA) -> None:
        compat.write_json(os.path.join(self.root, "sources", source_id, "items.json"),
                          {"source_id": source_id, "sha256": sha,
                           "count": len(rows), "items": list(rows)})

    def write_meta(self, **body) -> None:
        compat.write_json(os.path.join(self.root, "sources", SOURCE_ID, "meta.json"),
                          dict({"source_id": SOURCE_ID, "sha256": SOURCE_SHA}, **body))

    def write_pair_index(self, items) -> None:
        compat.write_json(os.path.join(self.root, "pairs", SOURCE_ID, "index.json"),
                          {"source_id": SOURCE_ID, "source_sha": SOURCE_SHA,
                           "repos": [COMPANION],
                           "items": {i: {COMPANION: ["%s:Book.d" % COMPANION]} for i in items},
                           "intext": {}})

    def write_reconciliation(self, **body) -> None:
        """``pairs/<id>/reconciliation.json`` in the shape `cf pair` writes.

        Every scope figure is computed from ``items.json`` (``pair/compare.py``
        takes ``in_scope`` from ``book_items``), so a manuscript's are all 0 --
        the defaults here.
        """
        compat.write_json(
            os.path.join(self.root, "pairs", SOURCE_ID, "reconciliation.json"),
            dict({"source_id": SOURCE_ID, "source_sha": SOURCE_SHA, "repos": [COMPANION],
                  "chapters": None, "items_in_book": 0, "items_in_scope": 0,
                  "items_in_scope_and_any_repo": 0, "items_in_scope_uncovered": [],
                  "task_universe": 0, "task_universe_items": []}, **body))

    def write_corpus(self, run_id: str, docs: dict, *, gate: dict | None = None) -> str:
        """``{file name: document}`` plus the RECEIPT the gate block decides."""
        directory = os.path.join(self.root, "corpus", run_id)
        for name, doc in docs.items():
            compat.write_json(os.path.join(directory, name), doc)
        compat.write_json(os.path.join(directory, "RECEIPT.json"),
                          {"run_id": run_id, "sources": {SOURCE_ID: SOURCE_SHA},
                           "actual": {"count": len(docs)}, "complete": True, "gate": gate})
        return directory

    # -- what the report computed ---------------------------------------
    def handoff(self) -> dict:
        return handoff_mod.make_handoff(self.root, date="2026-01-01")

    def coverage(self) -> dict:
        return self.handoff()["context"]["_coverage"]["sources"][0]


# ---------------------------------------------------------------------------
# FR#1 -- a manuscript's denominator
# ---------------------------------------------------------------------------

MANUSCRIPT_ITEMS = ["Definition 1.1.1", "Lemma 1.2.2", "Exercise 1.3.3"]


def slug(item: str) -> str:
    """The file name `cf plan-run` gives an item's task document."""
    return item.lower().replace(" ", "-").replace(".", "-") + ".json"


class ManuscriptFixture(Workspace):
    """A self-authored manuscript: its items.json is empty ON PURPOSE."""

    PAIRED = True
    #: the tagged environments the pairing index lists -- the universe itself
    ITEMS = MANUSCRIPT_ITEMS
    #: the one document that carries a defect row
    DEFECT_ON = MANUSCRIPT_ITEMS[0]
    #: what `sources/<id>/meta.json` records as this source's own numbering
    NUMBERING: dict | None = None

    def setUp(self):
        super().setUp()
        self.write_items([])
        meta = {"source_format": "text", "pagination": "none", "locator": "section"}
        if self.NUMBERING:
            meta["numbering"] = self.NUMBERING
        self.write_meta(**meta)
        if self.PAIRED:
            self.write_pair_index(self.ITEMS)
        self.write_corpus("hunt", {
            slug(item): (document(item) if item == self.DEFECT_ON
                         else dict(document(item), disagreements=[]))
            for item in self.ITEMS
        }, gate={"ok": True, "checked": len(self.ITEMS)})


class TestAManuscriptIsCountedAgainstItsTaggedEnvironments(ManuscriptFixture):
    def test_the_universe_comes_from_the_pairing_index(self):
        """items.json is empty by design, so the pairing index is the denominator."""
        cov = self.coverage()
        self.assertTrue(cov["manuscript"])
        self.assertEqual(cov["items_in_source"], 3)
        self.assertEqual(cov["items_in_scope"], 3)
        self.assertEqual(cov["items_examined"], 3)
        self.assertEqual(cov["items_with_findings"], 1)
        self.assertEqual(cov["items_never_examined"], 0)

    def test_no_cell_names_a_command_that_cannot_fill_it(self):
        text = self.handoff()["text"]
        self.assertNotIn("TODO(coverage)", text)
        row = next(l for l in text.splitlines() if l.startswith("| `%s` | aaaa" % SOURCE_ID))
        self.assertIn("| all / 1 |", row)
        self.assertIn("| 3 / 3 |", row)

    def test_the_tldr_carries_the_real_figures(self):
        self.assertIn("`%s`: 3 of 3 item(s) in scope examined (3 in the source)" % SOURCE_ID,
                      self.handoff()["text"])

    def test_a_paginated_source_is_not_mistaken_for_one(self):
        """The detection is the meta.json shape, not "the table came back empty"."""
        self.write_meta(source_format="pdf", pagination="formfeed", locator="page")
        cov = self.coverage()
        self.assertFalse(cov["manuscript"])
        self.assertEqual(cov["items_in_source"], 0)

    def test_the_reconciliation_is_not_a_second_opinion_about_the_scope(self):
        """`cf pair` computes its scope from the table that is empty by design.

        So for a manuscript it writes 0, always, and taking that figure beside
        the recomputed one published "3 of 0 item(s) in scope", chapter rows
        summing to 3 under a total row reading 0, and a staleness line sending
        the reader to `cf pair` -- which leaves the file byte-identical.
        """
        self.write_reconciliation()
        cov = self.coverage()
        self.assertEqual(cov["items_in_scope"], 3)
        self.assertEqual(cov["items_in_source"], 3)
        self.assertEqual(cov["items_in_task_universe"], 3)
        text = self.handoff()["text"]
        self.assertNotIn("of 0 item(s) in scope", text)
        self.assertNotIn("re-run `cf pair`", text)
        self.assertNotIn("TODO(coverage)", text)

    def test_the_chapter_rows_add_up_to_the_total_row(self):
        """Section 0's per-chapter rows and its `**all audited**` row are one sum.

        Fixing the total alone would have left the table contradicting itself
        the other way round: the chapter rows read the task universe off the
        reconciliation too.
        """
        self.write_reconciliation()
        cov = self.coverage()
        for column in ("in_scope", "in_task_universe", "examined", "gated", "with_findings"):
            with self.subTest(column=column):
                self.assertEqual(sum(b[column] for b in cov["by_chapter"].values()),
                                 cov["items_" + column])


class TestAManuscriptIsParsedWithItsOwnNumbering(ManuscriptFixture):
    """FR#1, variant 1: the labels need this source's numbering to parse at all.

    ``sources/<id>/meta.json`` records the numbering `cf onboard` inferred, and a
    label DEEPER than it is a hard reject rather than a truncation.  Parsed
    against ``DEFAULT_NUMBERING`` instead, every label of a book numbered
    ``2.3.4.1`` rejects, every item loses its chapter, and under a workspace that
    pins chapters every one of them falls out of scope -- which put the headline
    figure FR#1 was filed over, "0 of 0 item(s) in scope examined", back on a
    fully examined source.
    """

    NUMBERING = {"min_segments": 4, "max_segments": 4, "method": "inferred",
                 "confidence": 0.93}
    ITEMS = ["Definition 2.3.1.1", "Lemma 2.3.4.1", "Theorem 2.4.1.1"]
    DEFECT_ON = "Lemma 2.3.4.1"
    #: what `cf onboard`'s `(\d+)\.(\d+)` scope inference writes from such a label
    CHAPTERS = "    chapters: [2]\n"

    def test_the_default_numbering_is_what_rejected_these_labels(self):
        """The reject the fix removes, shown against the grammar itself."""
        self.assertIsNone(cf_items.try_parse("Lemma 2.3.4.1", cf_items.DEFAULT_KINDS)[0])
        self.assertEqual(cf_items.try_parse("Lemma 2.3.4.1", cf_items.DEFAULT_KINDS)[1].reason,
                         "too_many_segments")
        parsed, reject = cf_items.try_parse("Lemma 2.3.4.1", cf_items.DEFAULT_KINDS,
                                            numbering=self.NUMBERING)
        self.assertIsNone(reject)
        self.assertEqual(parsed.chapter, 2)

    def test_the_universe_keeps_its_chapters_and_its_scope(self):
        cov = self.coverage()
        self.assertEqual(cov["items_in_source"], 3)
        self.assertEqual(cov["items_in_scope"], 3)
        self.assertEqual(cov["chapters_in_source"], [2])
        self.assertEqual(cov["items_examined"], 3)
        self.assertEqual(cov["items_with_findings"], 1)

    def test_the_headline_figure_is_not_zero_of_zero_again(self):
        text = self.handoff()["text"]
        self.assertNotIn("of 0 item(s) in scope examined", text)
        self.assertIn("`%s`: 3 of 3 item(s) in scope examined (3 in the source)" % SOURCE_ID,
                      text)


class TestAManuscriptNumberedDeeperThanItsLabels(ManuscriptFixture):
    """The shape a real leanblueprint companion has, and the reason for the union.

    A manuscript's ``numbering`` is inferred from its PROSE headings, while its
    items are the adapter's tagged environments, which the blueprint numbers
    separately: one real companion's prose numbers `1.2.3` and all 293 of its
    environments are `Definition 1.1`.  Taking the source's own range alone
    there rejects every label, empties the chapter table and puts all 293 items
    under one "unnumbered" row -- the opposite failure to the one the numbering
    was carried in to fix, on the very companion FR#1 was reproduced against.
    """

    NUMBERING = {"min_segments": 3, "max_segments": 3, "method": "headings",
                 "confidence": 1.0}
    ITEMS = ["Definition 1.1", "Lemma 1.2", "Theorem 2.1"]
    DEFECT_ON = "Theorem 2.1"

    def test_the_labels_keep_their_chapters(self):
        cov = self.coverage()
        self.assertEqual(cov["chapters_in_source"], [1, 2])
        self.assertEqual(sorted(cov["by_chapter"]), ["1", "2"])
        self.assertEqual(cov["items_in_scope"], 3)
        self.assertEqual(cov["items_examined"], 3)

    def test_the_sources_own_range_alone_would_reject_every_one_of_them(self):
        self.assertEqual(cf_items.try_parse("Definition 1.1", cf_items.DEFAULT_KINDS,
                                            numbering=self.NUMBERING)[1].reason, "no_number")
        self.assertEqual(handoff_mod._universe_numbering(self.NUMBERING), (2, 3),
                         "the parse admits what either range admits")
        self.assertEqual(handoff_mod._universe_numbering({"min_segments": 4,
                                                          "max_segments": 4}), (2, 4))
        self.assertEqual(handoff_mod._universe_numbering(None), cf_items.DEFAULT_NUMBERING)


class TestAPinnedChapterDoesNotNarrowAManuscript(ManuscriptFixture):
    """FR#1, variant 2: the pairing index IS the planned universe.

    `cf plan-run` never narrows a manuscript's task universe by chapter
    (``pair/compare.py`` needs a non-empty ``book_items``, and a manuscript's
    item table is empty by design), so every planned and examined item outside
    the pin was counted by `cf tally` and by no figure in section 0 -- 7 of 293
    on the companion this was reproduced against, one of them carrying the run's
    only CONTENT finding.
    """

    CHAPTERS = "    chapters: [1]\n"
    ITEMS = MANUSCRIPT_ITEMS + ["Lemma 2.1.1"]
    DEFECT_ON = "Lemma 2.1.1"

    def test_every_planned_item_is_counted(self):
        cov = self.coverage()
        self.assertEqual(cov["items_in_source"], 4)
        self.assertEqual(cov["items_examined"], 4)
        self.assertEqual(cov["items_with_findings"], 1)
        self.assertEqual(cov["examined_outside_scope"], [],
                         "the planned universe cannot be outside the reported scope")
        self.assertEqual(cov["chapters_audited"], [])
        self.assertEqual(cov["chapters_pinned"], [1], "the pin is published, not thrown away")

    def test_section_zero_cannot_say_none_while_section_three_counts_one(self):
        """The contradiction FR#10 was filed about, reached through the scope."""
        rows = tally_mod.rows_for_run("hunt", os.path.join(self.root, "corpus", "hunt"),
                                      kinds=None)
        self.assertEqual([r["item"] for r in rows], ["Lemma 2.1.1"])
        self.assertEqual(self.coverage()["items_with_findings"], len({r["item"] for r in rows}))

    def test_the_document_says_the_pin_did_not_apply(self):
        """Silently ignoring it would be the same false denominator the other way."""
        text = self.handoff()["text"]
        self.assertIn("`chapters: 1` does not narrow this source", text)
        self.assertIn("`pairs/%s/index.json`, which no chapter list narrows" % SOURCE_ID, text)
        self.assertIn("`%s`: 4 of 4 item(s) in scope examined (4 in the source)" % SOURCE_ID,
                      text)


class TestAManuscriptWithNoPairingHasNoDenominator(ManuscriptFixture):
    PAIRED = False          # `cf pair` never ran, so nothing lists the environments

    def test_the_report_cannot_open_with_a_clean_bill(self):
        """total == scope == never == 0 reached the "everything was examined" branch."""
        text = self.handoff()["text"]
        self.assertNotIn("Every item of every source under audit", text)
        self.assertIn("`%s` (no denominator computed)" % SOURCE_ID, text)

    def test_the_cells_say_what_the_items_are_instead_of_naming_a_command(self):
        text = self.handoff()["text"]
        self.assertIn(handoff_mod.MANUSCRIPT_CELL, text)
        self.assertNotIn("TODO(coverage)", text)
        self.assertIn("run `cf pair`", text)

    def test_the_figures_are_unknown_rather_than_zero(self):
        """3 documents landed and the whole is unknown -- not "0 of 0 examined"."""
        cov = self.coverage()
        self.assertIsNone(cov["items_in_source"])
        self.assertIsNone(cov["items_never_examined"])
        self.assertIn("`%s`: 3 of ? item(s) in scope examined (? in the source)" % SOURCE_ID,
                      self.handoff()["text"])

    def test_a_zero_denominator_is_never_a_full_audit(self):
        """The guard is on the number, not only on the manuscript path."""
        self.assertIn("(no denominator computed)", handoff_mod.scope_sentence(
            {"sources": [{"source_id": "s", "under_audit": True, "items_in_source": 0,
                          "items_in_scope": 0, "items_never_examined": 0}]}))


# ---------------------------------------------------------------------------
# FR#20 -- "chapters audited" is a figure, not a hole
# ---------------------------------------------------------------------------

class TestTheChapterCellAgreesWithTheProse(Workspace):
    def setUp(self):
        super().setUp()
        self.write_items([
            {"item": "Exercise 1.2.3", "page": 24, "chapter": 1, "kind": "Exercise"},
            {"item": "Exercise 2.1.1", "page": 58, "chapter": 2, "kind": "Exercise"},
        ])
        self.write_corpus("hunt", {"exercise-1-2-3.json": document("Exercise 1.2.3")},
                          gate={"ok": True, "checked": 1})

    def test_an_empty_chapters_audited_is_all_not_a_todo(self):
        """One document stated one fact two contradictory ways."""
        text = self.handoff()["text"]
        self.assertIn("chapters audited all of chapters 1, 2;", text)
        row = next(l for l in text.splitlines() if l.startswith("| `%s` | aaaa" % SOURCE_ID))
        self.assertIn("| all / 2 |", row)
        self.assertNotIn("TODO(coverage)", row)

    def test_a_narrowed_scope_still_prints_its_fraction(self):
        cov = dict(self.coverage(), chapters_audited=[1], chapters_in_source=[1, 2])
        self.assertEqual(handoff_mod._chapter_fraction(cov), "1 / 2")

    def test_only_an_unknown_source_is_a_hole_and_it_names_onboard(self):
        """Nothing in `cf pair` or `cf harvest` ever writes a chapter list."""
        cell = handoff_mod._chapter_fraction({"chapters_in_source": []})
        self.assertIn("TODO(coverage)", cell)
        self.assertIn("cf onboard", cell)
        self.assertNotIn("cf pair", cell)


class TestASourceNobodyIsBoundToClaimsNoChapters(Workspace):
    """A reference edition the workspace keeps: `role: reference`, no companion."""

    EXTRA_SOURCES = ("  %s:\n"
                     '    sha256: "%s"\n'
                     "    title: A Book\n"
                     '    edition: "second edition"\n'
                     "    offset: 16\n"
                     "    role: reference\n" % (OTHER_SOURCE_ID, OTHER_SOURCE_SHA))

    def setUp(self):
        super().setUp()
        self.write_items([{"item": "Exercise 1.1.1", "page": 10, "chapter": 1,
                           "kind": "Exercise"}])
        # prepared, because onboarding extracts a reference edition's items too
        self.write_items([{"item": "Exercise 1.1.1", "page": 9, "chapter": 1,
                           "kind": "Exercise"},
                          {"item": "Exercise 2.1.1", "page": 57, "chapter": 2,
                           "kind": "Exercise"}],
                         source_id=OTHER_SOURCE_ID, sha=OTHER_SOURCE_SHA)
        self.write_corpus("hunt", {"exercise-1-1-1.json": document("Exercise 1.1.1")},
                          gate={"ok": True, "checked": 1})

    def test_the_chapters_cell_does_not_claim_every_chapter_was_audited(self):
        """"empty means all" is the audited source's rule, not this one's.

        The prose two lines below says the source is not under audit at all, so
        `all / 2` there was one document stating one fact two contradictory ways.
        """
        row = next(l for l in self.handoff()["text"].splitlines()
                   if l.startswith("| `%s` | bbbb" % OTHER_SOURCE_ID))
        self.assertIn("| — | — |", row)
        self.assertNotIn("all /", row)
        self.assertNotIn("TODO(coverage)", row)

    def test_the_audited_source_still_gets_its_figures(self):
        row = next(l for l in self.handoff()["text"].splitlines()
                   if l.startswith("| `%s` | aaaa" % SOURCE_ID))
        self.assertIn("| all / 1 |", row)
        self.assertIn("| 1 / 1 |", row)

    def test_the_prose_says_it_owes_no_denominator(self):
        self.assertIn("`%s`: not under audit — no companion is bound to it "
                      "(`role: reference`)" % OTHER_SOURCE_ID, self.handoff()["text"])

    def test_section_zero_does_not_label_its_chapters_all_audited(self):
        """`cf onboard` prepares an items.json for every edition a repo ships.

        So a reference edition arrives with chapters, and section 0 gave them a
        `**all audited**` total row and counted them as never examined — the
        figures the first paragraph is specifically taught not to count.
        """
        section = self.handoff()["text"].split("## 0.")[1].split("## 1.")[0]
        mine = [l for l in section.splitlines()
                if l.startswith("| `%s` |" % OTHER_SOURCE_ID)]
        self.assertEqual(len(mine), 1, mine)
        self.assertNotIn("all audited", mine[0])
        self.assertEqual(mine[0],
                         "| `%s` | — | 2 | — | — | — | 0 | 0 | 0 | — |" % OTHER_SOURCE_ID)
        self.assertIn("| `%s` | **all audited** |" % SOURCE_ID, section,
                      "the audited source keeps its own total row")

    def test_neither_cell_names_a_command_nor_invents_a_figure(self):
        """`cf pair && cf harvest` cannot put a scope on a source nothing audits."""
        for part, whole in ((None, None), (2, 2)):
            with self.subTest(part=part):
                self.assertEqual(handoff_mod._fraction(part, whole, False, False),
                                 handoff_mod.NO_DENOMINATOR_OWED)
        self.assertIn("TODO(coverage)", handoff_mod._fraction(None, None, False, True))
        self.assertEqual(handoff_mod._fraction(2, 2, False, True), "2 / 2")


# ---------------------------------------------------------------------------
# FR#10, FR#19 -- one reader for the defect rows of a corpus document
# ---------------------------------------------------------------------------

class TestTheReportCountsWhatTheTallyCounts(Workspace):
    def setUp(self):
        super().setUp()
        self.write_items([
            {"item": "Exercise 1.1.1", "page": 10, "chapter": 1, "kind": "Exercise"},
            {"item": "Exercise 1.1.2", "page": 11, "chapter": 1, "kind": "Exercise"},
        ])
        # a.json carries its row under `findings` with an empty `disagreements`:
        # the shape the gate admits (verify_findings requires the KEY, iterates both)
        self.dest = self.write_corpus("gated", {
            "a.json": dict(document("Exercise 1.1.1", key="findings"), disagreements=[]),
            "b.json": document("Exercise 1.1.2"),
        }, gate={"ok": True, "checked": 2})

    def test_a_row_under_findings_counts_as_a_finding(self):
        cov = self.coverage()
        self.assertEqual(cov["items_examined"], 2)
        self.assertEqual(cov["items_with_findings"], 2)
        self.assertEqual(cov["runs"]["gated"]["with_findings"], 2,
                         "the per-run table must not disagree with the item set")

    def test_the_two_readers_are_one_reader(self):
        """A third opinion about which key holds the rows is how they drifted."""
        self.assertIs(handoff_mod.tally_mod.defect_rows, tally_mod.defect_rows)
        self.assertEqual(tally_mod.FINDING_LISTS, ("disagreements", "findings"))
        rows = tally_mod.rows_for_run("gated", self.dest, kinds=None)
        self.assertEqual({r["item"] for r in rows}, self.coverage_with_findings())

    def coverage_with_findings(self) -> set:
        examined = handoff_mod.load_examined(
            self.paths, {}, handoff_mod.load_receipts(self.paths))
        return set(examined[SOURCE_ID]["with_findings"])


class TestADocumentWithNoTopLevelItemIsNotDropped(Workspace):
    def setUp(self):
        super().setUp()
        self.write_items([{"item": "Exercise 1.1.1", "page": 10, "chapter": 1,
                           "kind": "Exercise"}])
        # a v1 file named after its task, rows under `findings`, no `item` key
        self.dest = self.write_corpus(
            "imp", {"task-0042.json": document(key="findings")}, gate=None)

    def test_it_is_keyed_the_way_the_tally_keys_it(self):
        """The tally files it under the file stem; deriving the item from the row
        would have produced the same contradiction in the other direction."""
        rows = tally_mod.rows_for_run("imp", self.dest, kinds=None)
        self.assertEqual([r["item_raw"] for r in rows], ["task-0042"])
        examined = handoff_mod.load_examined(
            self.paths, {}, handoff_mod.load_receipts(self.paths))
        block = examined[SOURCE_ID]
        self.assertIn("task-0042", block["examined"])
        self.assertIn("task-0042", block["with_findings"])

    def test_the_run_is_named_ungated_even_with_nothing_left_to_count(self):
        """The bookkeeping is read off the RECEIPT, not off a surviving document."""
        shutil.rmtree(os.path.join(self.root, "corpus", "imp"))
        self.write_corpus("empty", {}, gate=None)
        examined = handoff_mod.load_examined(
            self.paths, {}, handoff_mod.load_receipts(self.paths))
        self.assertEqual(examined[SOURCE_ID]["ungated_runs"], ["empty"])
        self.assertIn("ungated run(s) `empty`", self.handoff()["text"])

    def test_the_gate_config_left_beside_the_corpus_is_not_an_examined_item(self):
        """`cf harvest` writes these three into the corpus directory itself."""
        directory = os.path.join(self.root, "corpus", "imp")
        for name in ("gate.local.json", "queue_remaining.json", "journal-harvest.json"):
            compat.write_json(os.path.join(directory, name), {"not": "a finding"})
        examined = handoff_mod.load_examined(
            self.paths, {}, handoff_mod.load_receipts(self.paths))
        self.assertEqual(set(examined[SOURCE_ID]["examined"]), {"task-0042"})


class TestADocumentTheTallyRefusesIsNotDroppedInSilence(Workspace):
    """NEW#g-report-1: the counting authority fails closed, the report failed open.

    `cf tally` refuses a corpus holding a findings file that is valid JSON and
    not an object ("a corpus with one it cannot read makes every number a lower
    bound", exit 2).  The report skipped the same file at ``except Exception:
    continue`` and published the lower bound as a figure -- with the item behind
    it named as NEVER EXAMINED, although it has a gated corpus document.
    """

    def setUp(self):
        super().setUp()
        self.write_items([
            {"item": "Exercise 1.1.1", "page": 10, "chapter": 1, "kind": "Exercise"},
            {"item": "Exercise 1.1.2", "page": 11, "chapter": 1, "kind": "Exercise"},
        ])
        self.dest = self.write_corpus("hunt", {
            "exercise-1-1-1.json": document("Exercise 1.1.1"),
            "exercise-1-1-2.json": document("Exercise 1.1.2"),
        }, gate={"ok": True, "checked": 2})
        # a findings file that is valid JSON and not a findings object
        compat.write_text(os.path.join(self.dest, "exercise-1-1-2.json"), "[]\n")

    def test_the_tally_still_refuses_the_file(self):
        with self.assertRaises(CfError) as caught:
            tally_mod.rows_for_run("hunt", self.dest, kinds=None)
        self.assertIn("is not a findings object", str(caught.exception))

    def test_the_report_counts_the_file_it_could_not_read(self):
        self.assertEqual(self.coverage()["unreadable_documents"],
                         ["hunt/exercise-1-1-2.json"])

    def test_the_handoff_names_it_and_calls_the_figures_a_lower_bound(self):
        text = self.handoff()["text"]
        self.assertIn("could not be read", text)
        self.assertIn("`hunt/exercise-1-1-2.json`", text)
        self.assertIn("LOWER BOUND", text)

    def test_the_command_says_so_on_the_terminal_too(self):
        """`cf tally` exits 2 on this corpus and `cf report` exits 0 over it."""
        rc, out, err = cf_cli("report", "--workspace", self.root, "--no-verify-args")
        self.assertEqual(rc, 0, err)
        self.assertIn("could not be read", out)
        self.assertIn("hunt/exercise-1-1-2.json", out)
        self.assertIn("cf tally --run <run-id>", out)

    def test_the_item_behind_it_is_never_published_as_unexamined_unremarked(self):
        """It has a gated document; the figures cannot say otherwise in silence."""
        cov = self.coverage()
        self.assertIn("Exercise 1.1.2", cov["never_examined"])
        text = self.handoff()["text"]
        self.assertIn("may be counted as never examined", text)
        self.assertEqual(cf_cli("tally", "--workspace", self.root, "--no-write")[0], 2,
                         "the tally this sentence sends the reader to must still refuse")


class TestAnExaminedItemOutsideTheScopeIsNotDropped(Workspace):
    """NEW#g-report-2: ``examined_outside_scope`` had no reader anywhere in the tree.

    Every section-0 figure is an intersection with the items in scope, so a
    corpus document for an item outside the audited chapters is counted by
    `cf tally` and by none of them -- ``items_with_findings`` included.  It is
    reachable on an ordinary path: `cf import-corpus` of a legacy corpus wider
    than the current chapter scope is how the imported baseline arrives.
    """

    CHAPTERS = "    chapters: [1]\n"

    def setUp(self):
        super().setUp()
        self.write_items([
            {"item": "Definition 1.1.1", "page": 10, "chapter": 1, "kind": "Definition"},
            {"item": "Lemma 2.1.1", "page": 58, "chapter": 2, "kind": "Lemma"},
        ])
        self.dest = self.write_corpus("hunt", {
            "definition-1-1-1.json": dict(document("Definition 1.1.1"), disagreements=[]),
            "lemma-2-1-1.json": document("Lemma 2.1.1"),
        }, gate={"ok": True, "checked": 2})

    def test_the_finding_outside_the_scope_is_still_published(self):
        cov = self.coverage()
        rows = tally_mod.rows_for_run("hunt", self.dest, kinds=None)
        self.assertEqual([r["item"] for r in rows], ["Lemma 2.1.1"])
        self.assertEqual(cov["items_with_findings"], 0, "it is outside the audited chapters")
        self.assertEqual(cov["with_findings_outside_scope"], ["Lemma 2.1.1"],
                         "a counted finding may not be absent from every published figure")
        self.assertEqual(cov["examined_outside_scope"], ["Lemma 2.1.1"])
        self.assertEqual(cov["chapters_outside_scope"], ["2"])

    def test_the_document_names_the_items_and_where_they_are_counted(self):
        text = self.handoff()["text"]
        self.assertIn("lie OUTSIDE the audited chapter(s)", text)
        self.assertIn("`Lemma 2.1.1`", text)
        self.assertIn("1 of them with findings", text)
        self.assertIn("`cf tally` and §3 count them and no figure above does", text)

    def test_the_per_run_block_and_the_per_source_figures_do_not_disagree_in_silence(self):
        cov = self.coverage()
        self.assertEqual(cov["runs"]["hunt"], {"examined": 2, "with_findings": 1, "gated": True})
        self.assertEqual(cov["items_examined"] + len(cov["examined_outside_scope"]),
                         cov["runs"]["hunt"]["examined"])


def flat(text: str) -> str:
    """The rendered document with its line wrapping taken out.

    The template's prose is hard-wrapped, so a sentence assertion that does not
    do this passes or fails on where a word happened to land.
    """
    return " ".join(text.split())


class TestTheLineUnderTheTableSaysWhichIsCountedWhere(Workspace):
    """Two different exclusions, one sentence, and it was false about one of them.

    The line closing section 0 lumped an examined item outside the audited
    chapters together with a corpus document nothing could read and said both
    are "counted in §3" -- but `cf tally` REFUSES a corpus holding the second
    (exit 2, so no ``tally.*.json`` exists and §3 renders a TODO).  It claimed
    coverage the report does not have, two lines under per-source notes that
    correctly say the opposite things about the two.
    """

    CHAPTERS = "    chapters: [1]\n"

    def setUp(self):
        super().setUp()
        self.write_items([
            {"item": "Definition 1.1.1", "page": 10, "chapter": 1, "kind": "Definition"},
            {"item": "Lemma 2.1.1", "page": 58, "chapter": 2, "kind": "Lemma"},
        ])
        self.dest = self.write_corpus("hunt", {
            "definition-1-1-1.json": dict(document("Definition 1.1.1"), disagreements=[]),
            "lemma-2-1-1.json": document("Lemma 2.1.1"),
            "definition-1-1-2.json": document("Definition 1.1.2"),
        }, gate={"ok": True, "checked": 3})
        compat.write_text(os.path.join(self.dest, "definition-1-1-2.json"), "[]\n")

    def test_the_two_exclusions_are_not_described_as_one(self):
        text = flat(self.handoff()["text"])
        self.assertIn("an item examined outside the audited chapters is counted in §3 and in "
                      "no column of this table", text)
        self.assertIn("a corpus document that could not be read is counted nowhere", text)
        self.assertNotIn("those documents are counted in §3 and in no column of this table", text)

    def test_the_notes_it_summarises_still_say_the_two_things_it_now_says(self):
        text = flat(self.handoff()["text"])
        self.assertIn("`cf tally` and §3 count them and no figure above does", text)
        self.assertIn("LOWER BOUND", text)

    def test_the_tally_the_sentence_appeals_to_really_refuses_this_corpus(self):
        """"counted nowhere" is true only because `cf tally` will not read it."""
        self.assertEqual(cf_cli("tally", "--workspace", self.root, "--no-write")[0], 2)


class TestTheLowerBoundIsSaidWhereTheRestOfTheNoteIsATodo(Workspace):
    """The sentence sat after the branches that end a source's note early.

    A source with no ``items.json`` has no denominator, so its note is one TODO
    line and nothing else -- and a corpus document nothing could read was then
    named on `cf report`'s terminal and in no line of the handoff, which is the
    artifact that travels.
    """

    def setUp(self):
        super().setUp()
        self.dest = self.write_corpus("hunt", {"exercise-1-1-1.json": document("Exercise 1.1.1")},
                                      gate={"ok": True, "checked": 1})
        compat.write_text(os.path.join(self.dest, "exercise-1-1-1.json"), "[]\n")

    def test_the_source_has_no_denominator_at_all(self):
        cov = self.coverage()
        self.assertIsNone(cov["items_in_source"])
        self.assertEqual(cov["unreadable_documents"], ["hunt/exercise-1-1-1.json"])

    def test_the_handoff_says_it_and_not_only_the_terminal(self):
        rc, out, err = cf_cli("report", "--workspace", self.root, "--no-verify-args")
        self.assertEqual(rc, 0, err)
        self.assertIn("LOWER BOUND", out)
        text = self.handoff()["text"]
        self.assertIn("LOWER BOUND", text)
        self.assertIn("`hunt/exercise-1-1-1.json`", text)


class TestALongListOfNamesIsCappedWhereverItIsPrinted(Workspace):
    """One line of Markdown may not carry a whole malformed directory.

    The out-of-scope sentence has capped at ``OUTSIDE_SCOPE_NAMES`` since it was
    written, for exactly this reason; the two unreadable-document sentences
    joined the full list, and a legacy import whose whole directory is the wrong
    shape makes that list as long as the directory.
    """

    #: enough to pass the cap by 4
    COUNT = 12

    def setUp(self):
        super().setUp()
        self.write_items([{"item": "Exercise 1.1.1", "page": 10, "chapter": 1,
                           "kind": "Exercise"}])
        self.dest = self.write_corpus("hunt", {
            "exercise-1-1-%d.json" % n: document("Exercise 1.1.%d" % n)
            for n in range(1, self.COUNT + 1)
        }, gate={"ok": True, "checked": self.COUNT})
        for n in range(1, self.COUNT + 1):
            compat.write_text(os.path.join(self.dest, "exercise-1-1-%d.json" % n), "[]\n")

    def write_unattributed_corpus(self) -> list:
        """A second run whose receipt names TWO sources, so no source block owns it."""
        directory = os.path.join(self.root, "corpus", "wide")
        for n in range(1, self.COUNT + 1):
            compat.write_text(os.path.join(directory, "exercise-2-1-%d.json" % n), "[]\n")
        compat.write_json(os.path.join(directory, "RECEIPT.json"),
                          {"run_id": "wide",
                           "sources": {SOURCE_ID: SOURCE_SHA, OTHER_SOURCE_ID: OTHER_SOURCE_SHA},
                           "actual": {"count": self.COUNT}, "complete": True,
                           "gate": {"ok": True, "checked": self.COUNT}})
        return sorted("wide/exercise-2-1-%d.json" % n for n in range(1, self.COUNT + 1))

    def assert_capped(self, text: str, names: list) -> None:
        cap = handoff_mod.OUTSIDE_SCOPE_NAMES
        self.assertEqual(len(names), self.COUNT)
        self.assertIn("and %d more" % (self.COUNT - cap), text)
        for name in names[:cap]:
            self.assertIn("`%s`" % name, text)
        for name in names[cap:]:
            self.assertNotIn("`%s`" % name, text)

    def test_the_per_source_sentence_is_capped(self):
        self.assert_capped(self.handoff()["text"], self.coverage()["unreadable_documents"])

    def test_the_sentence_for_documents_no_source_owns_is_capped_too(self):
        expected = self.write_unattributed_corpus()
        handoff = self.handoff()
        self.assertEqual(handoff["context"]["_coverage"]["unreadable_documents"], expected)
        self.assert_capped(handoff["text"], expected)

    def test_the_full_list_is_in_the_artifact_both_sentences_point_at(self):
        self.write_unattributed_corpus()
        self.handoff()
        published = compat.read_json(os.path.join(self.root, "reports", "coverage.json"))
        self.assertEqual(len(published["sources"][0]["unreadable_documents"]), self.COUNT)
        self.assertEqual(len(published["unreadable_documents"]), self.COUNT)
        self.assertIn("`reports/coverage.json` (`unreadable_documents`)", self.handoff()["text"])


class TestATaskUniverseWithNoItemsBehindItIsNotZeroPerChapter(Workspace):
    """NEW#g-report-4's neighbour: the same cell, an older reconciliation.

    ``task_universe`` without ``task_universe_items`` is a shape the tree
    already knows about -- `cf plan-run` warns about it and falls back to a
    count-only check -- and the per-chapter rows counted against the empty set
    it leaves behind, printing 0 in every row under a total row printing the
    real figure.
    """

    def setUp(self):
        super().setUp()
        self.write_items([
            {"item": "Exercise 1.1.1", "page": 10, "chapter": 1, "kind": "Exercise"},
            {"item": "Exercise 1.1.2", "page": 11, "chapter": 1, "kind": "Exercise"},
        ])
        # written by hand, not through write_reconciliation: the defect IS the
        # absent key, and an older `cf pair` wrote the file without it
        compat.write_json(
            os.path.join(self.root, "pairs", SOURCE_ID, "reconciliation.json"),
            {"source_id": SOURCE_ID, "source_sha": SOURCE_SHA, "repos": [COMPANION],
             "chapters": None, "items_in_book": 2, "items_in_scope": 2,
             "items_in_scope_and_any_repo": 2, "items_in_scope_uncovered": [],
             "task_universe": 2})
        self.write_corpus("hunt", {"exercise-1-1-1.json": document("Exercise 1.1.1")},
                          gate={"ok": True, "checked": 1})

    def test_the_per_chapter_figure_is_unknown_rather_than_zero(self):
        cov = self.coverage()
        self.assertEqual(cov["items_in_task_universe"], 2)
        self.assertIs(cov["task_universe_itemised"], False)
        self.assertEqual([b["in_task_universe"] for b in cov["by_chapter"].values()], [None])

    def test_the_document_says_which_of_the_two_was_never_computed(self):
        text = self.handoff()["text"]
        self.assertIn("names a task universe of 2 and does not list the items behind it", text)
        self.assertIn("`task_universe_items`", text)
        section = text.split("## 0.")[1].split("## 1.")[0]
        rows = [l for l in section.splitlines() if l.startswith("| `%s` |" % SOURCE_ID)]
        self.assertEqual(len(rows), 2, rows)
        self.assertEqual([c.strip() for c in rows[0].split("|")][5], "—")
        self.assertEqual([c.strip() for c in rows[1].split("|")][5], "2")

    def test_an_itemised_reconciliation_is_still_counted_per_chapter(self):
        """The guard narrows to the shape it is for, and no further."""
        self.write_reconciliation(items_in_scope=2, task_universe=1,
                                  task_universe_items=["Exercise 1.1.1"])
        cov = self.coverage()
        self.assertIs(cov["task_universe_itemised"], True)
        self.assertEqual([b["in_task_universe"] for b in cov["by_chapter"].values()], [1])
        self.assertNotIn("does not list the items behind it", self.handoff()["text"])


class TestAnUncomputedTaskUniverseIsNotZeroPerChapter(Workspace):
    """NEW#g-report-4: one figure, three renderings, in one document.

    With no ``reconciliation.json`` nothing has computed a task universe, so the
    total row prints "—" and the prose "?" -- while the per-chapter row counted
    against an empty set and printed a hard 0, which is the overclaim the
    neighbouring ``paired`` cell was already taught not to make.
    """

    def setUp(self):
        super().setUp()
        self.write_items([
            {"item": "Exercise 1.1.1", "page": 10, "chapter": 1, "kind": "Exercise"},
            {"item": "Exercise 1.1.2", "page": 11, "chapter": 1, "kind": "Exercise"},
        ])
        self.write_corpus("hunt", {"exercise-1-1-1.json": document("Exercise 1.1.1")},
                          gate={"ok": True, "checked": 1})

    def test_the_figure_nothing_computed_is_not_a_zero(self):
        cov = self.coverage()
        self.assertIsNone(cov["items_in_task_universe"])
        self.assertEqual([b["in_task_universe"] for b in cov["by_chapter"].values()], [None])

    def test_the_chapter_row_prints_what_the_total_row_prints(self):
        section = self.handoff()["text"].split("## 0.")[1].split("## 1.")[0]
        rows = [l for l in section.splitlines() if l.startswith("| `%s` |" % SOURCE_ID)]
        self.assertEqual(len(rows), 2, rows)
        self.assertEqual([c.strip() for c in rows[0].split("|")][5],
                         [c.strip() for c in rows[1].split("|")][5])
        self.assertEqual([c.strip() for c in rows[0].split("|")][5], "—")

    def test_the_chapter_rows_still_add_up_to_the_total_row(self):
        """The invariant, widened to the case where the figure is uncomputed."""
        cov = self.coverage()
        for column in ("in_scope", "in_task_universe", "examined", "gated", "with_findings"):
            with self.subTest(column=column):
                values = [b[column] for b in cov["by_chapter"].values()]
                total = cov["items_" + column]
                if total is None:
                    self.assertTrue(all(v is None for v in values), values)
                else:
                    self.assertEqual(sum(values), total)


# ---------------------------------------------------------------------------
# FR#26 -- a gate that ran and REJECTED is its own state
# ---------------------------------------------------------------------------

class TestAFailedGateIsDistinguishable(Workspace):
    def setUp(self):
        super().setUp()
        self.write_items([{"item": "Exercise 1.1.1", "page": 10, "chapter": 1,
                           "kind": "Exercise"}])
        self.dest = self.write_corpus(
            "rejected", {"exercise-1-1-1.json": document("Exercise 1.1.1")},
            gate={"ok": False, "preset": "differential-legacy", "files": 1,
                  "problems": ["exercise-1-1-1.json: book_quote NOT FOUND in the book text"]})

    def test_the_receipt_reader_tells_the_two_apart(self):
        runs = {"rejected": self.dest}
        self.assertEqual(harvest_mod.gate_failed_runs(runs), ["rejected"])
        self.assertEqual(harvest_mod.ungated_runs(runs), [],
                         "a gate block IS present: `ungated` must stay true of its own message")

    def test_the_tally_names_it_and_says_the_rows_are_counted(self):
        result = tally_mod.tally({"rejected": self.dest})
        self.assertEqual(result["gate_failed"], ["rejected"])
        self.assertEqual(result["ungated"], [])
        rendered = tally_mod.render(result)
        self.assertIn("GATE FAILED run(s): rejected", rendered)
        self.assertIn("gate.problems", rendered)

    def test_the_handoff_does_not_call_it_unchecked(self):
        cov = self.coverage()
        self.assertEqual(cov["gate_failed_runs"], ["rejected"])
        # not only in the prose: `reports/coverage.json` publishes both lists,
        # and a rejected run in BOTH is the same corpus described two ways by
        # two artifacts -- `cf tally`'s own `ungated` is [] for this receipt.
        self.assertEqual(cov["ungated_runs"], [])
        self.assertEqual(tally_mod.tally({"rejected": self.dest})["ungated"],
                         cov["ungated_runs"])
        text = self.handoff()["text"]
        self.assertIn("gate FAILED for run(s) `rejected`", text)
        self.assertNotIn("ungated run(s) `rejected`", text)
        self.assertEqual(cov["items_gated"], 0, "a rejected run is not a gated one")

    def test_a_run_with_no_gate_block_still_lands_in_ungated(self):
        """The two lists are disjoint, and the first one still has its own case."""
        self.write_corpus("imp", {"exercise-1-1-1.json": document("Exercise 1.1.1")}, gate=None)
        cov = self.coverage()
        self.assertEqual(cov["ungated_runs"], ["imp"])
        self.assertEqual(cov["gate_failed_runs"], ["rejected"])
        text = self.handoff()["text"]
        self.assertIn("ungated run(s) `imp`", text)
        self.assertIn("gate FAILED for run(s) `rejected`", text)

    def test_a_run_whose_gate_passed_is_named_by_neither(self):
        result = tally_mod.tally({"gated": self.write_corpus(
            "gated", {"exercise-1-1-1.json": document("Exercise 1.1.1")},
            gate={"ok": True, "checked": 1})})
        self.assertEqual(result["gate_failed"], [])
        self.assertEqual(result["ungated"], [])
        self.assertNotIn("GATE FAILED", tally_mod.render(result))


# ---------------------------------------------------------------------------
# FR#22 -- the remedy a warning prints has to be runnable
# ---------------------------------------------------------------------------

def cf(*argv) -> tuple:
    """Run the CLI in process; returns (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = cf_main.main(list(argv))
    except SystemExit as exc:            # --help
        rc = int(exc.code or 0)
    return rc, out.getvalue(), err.getvalue()


def cf_cli(*argv) -> tuple:
    """The same, through the process entry point: a refusal is an EXIT CODE.

    ``cf_main.main`` raises ``CfError`` for a library caller; ``cf_main.cli`` is
    what a shell runs, and what a hint printed for a user to paste reaches.  A
    hint is only settled if the command in it exits 0 there.
    """
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = cf_main.cli(list(argv))
    except SystemExit as exc:            # --help
        rc = int(exc.code or 0)
    return rc, out.getvalue(), err.getvalue()


class TestTheUngatedHintNamesNothingThatDoesNotWork(Workspace):
    """Every command and flag in the two rewritten hints, run."""

    def setUp(self):
        super().setUp()
        self.write_items([{"item": "Exercise 1.1.1", "page": 10, "chapter": 1,
                           "kind": "Exercise"}])
        self.write_corpus("imp", {"exercise-1-1-1.json": document("Exercise 1.1.1")},
                          gate=None)

    def printed(self) -> str:
        """Everything the two warnings put in front of a reader."""
        result = tally_mod.tally({"imp": os.path.join(self.root, "corpus", "imp")})
        return tally_mod.render(result) + "\n" + self.handoff()["text"]

    def test_neither_hint_names_a_flag_that_was_never_passed(self):
        """--no-gate is not the remedy for an import that could build no config."""
        self.assertNotIn("--no-gate", self.printed())

    def test_neither_hint_names_the_bare_invocation_that_refuses(self):
        """`cf harvest <run-id>` alone answers "run.yaml records no host"."""
        self.assertNotRegex(self.printed(), r"`cf harvest <run-id>`(?!\s*--)")

    def test_every_command_printed_exists_and_takes_the_flags_named(self):
        text = self.printed()
        self.assertIn("cf harvest <run-id> --gate-config", text)
        self.assertIn("cf import-corpus --run-id <run-id> --from <dir> --preset", text)
        for command, flags in (("harvest", ("--gate-config", "--from", "--journal",
                                            "--execute")),
                               ("import-corpus", ("--run-id", "--from", "--preset",
                                                  "--source"))):
            with self.subTest(command=command):
                rc, out, _err = cf(command, "--help")
                self.assertEqual(rc, 0, "`cf %s` does not exist" % command)
                for flag in flags:
                    self.assertIn(flag, out, "`cf %s` does not take %s" % (command, flag))

    def test_the_hint_is_a_spelling_not_one_machine_s_paths(self):
        """Every argument in it is an angle-bracketed placeholder (CLAUDE.md S1).

        ``<id>`` joined the list when the import branch gained the ``--source``
        a multi-source workspace refuses without; the assertion still pins the
        WHOLE set, so a real path in the hint fails here as before.
        """
        self.assertNotRegex(tally_mod.REGATE_HINT, r"(^|\s)/\w")
        self.assertEqual(sorted(set(re.findall(r"<[a-z-]+>", tally_mod.REGATE_HINT))),
                         ["<dir>", "<file>", "<id>", "<preset>", "<run-id>"])


def build_import_workspace(case: unittest.TestCase, *source_ids: str) -> None:
    """A workspace holding *source_ids*, plus one v1 findings file to import.

    Taken out of one test class's setUp because the hint it exercises has to be
    run in a workspace with MORE than one source too: that is the configuration
    `cf import-corpus` refuses without ``--source``, and the one the hint's own
    branch (an import that landed ``gate: null``) is most likely to be read in.
    """
    case.tmp = pathlib.Path(tempfile.mkdtemp(prefix="cf-rf-final-cli-", dir=TMPDIR)).resolve()
    case.addCleanup(shutil.rmtree, case.tmp, True)
    case.ws = case.tmp / "ws"
    case.source = case.tmp / "findings"
    case.source.mkdir(parents=True)
    (case.source / "exercise-1-1-1.json").write_text(json.dumps({
        "item": "Exercise 1.1.1", "book_located": True, "book_passage_quote": QUOTE,
        "matches": [], "disagreements": [], "coverage_notes": ""}), encoding="utf-8")
    case.assertEqual(cf("init", str(case.ws), "--no-git", "-q")[0], 0)
    shas = {SOURCE_ID: SOURCE_SHA, OTHER_SOURCE_ID: OTHER_SOURCE_SHA}
    blocks = "".join(
        "  %s:\n" % sid
        + '    sha256: "%s"\n' % shas[sid]
        + "    title: A Book\n    edition: \"%s\"\n    offset: 16\n"
          "    chapters: [1]\n    role: audited\n"
          % ("first edition" if sid == SOURCE_ID else "second edition")
        for sid in source_ids)
    (case.ws / "workspace.yaml").write_text(
        "version: 2\nworkspace:\n  name: ws\ncompanions: {}\nsources:\n" + blocks,
        encoding="utf-8")
    for sid in source_ids:
        text = case.ws / "sources" / sid / "text.txt"
        text.parent.mkdir(parents=True, exist_ok=True)
        text.write_text("Exercise 1.1.1 (Boolean closure). " + QUOTE + "\n", encoding="utf-8")


class TestTheHintIsRunAndItSettlesIt(unittest.TestCase):
    """The import branch of the hint, executed against a real ungated corpus."""

    def setUp(self):
        build_import_workspace(self, SOURCE_ID)

    def receipt(self, run_id: str) -> dict:
        return json.loads((self.ws / "corpus" / run_id / "RECEIPT.json").read_text())

    def test_the_import_branch_closes_the_ungated_state(self):
        rc, _out, err = cf("import-corpus", "--run-id", "imp", "--from", str(self.source),
                           "--workspace", str(self.ws), "--no-tally", "--preset", "")
        self.assertEqual(rc, 0, err)
        self.assertIsNone(self.receipt("imp")["gate"], "the fixture must start ungated")

        rc, tallied, _err = cf("tally", "--workspace", str(self.ws), "--no-write")
        self.assertEqual(rc, 0)
        self.assertIn("UNGATED run(s): imp", tallied)
        self.assertIn("cf import-corpus --run-id <run-id> --from <dir> --preset <preset>",
                      tallied)

        # the sentence that was printed, run verbatim (with this run's own id,
        # directory and preset substituted for the placeholders)
        rc, _out, err = cf("import-corpus", "--run-id", "imp", "--from", str(self.source),
                           "--workspace", str(self.ws), "--no-tally",
                           "--preset", "differential-legacy", "--repos", COMPANION, "--force")
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.receipt("imp")["gate"]["ok"], self.receipt("imp")["gate"])

        rc, tallied, _err = cf("tally", "--workspace", str(self.ws), "--no-write")
        self.assertEqual(rc, 0)
        self.assertNotIn("UNGATED", tallied)

    def test_the_harvest_branch_runs_the_gate_over_an_existing_corpus(self):
        """`cf harvest <run-id> --gate-config <file>` (plus the --from it needs)."""
        self.assertIn("cf harvest <run-id> --gate-config <file>", tally_mod.REGATE_HINT)
        rc, _out, err = cf("import-corpus", "--run-id", "imp", "--from", str(self.source),
                           "--workspace", str(self.ws), "--no-tally",
                           "--preset", "differential-legacy", "--repos", COMPANION)
        self.assertEqual(rc, 0, err)
        config = self.ws / "corpus" / "imp" / "gate.local.json"
        self.assertTrue(config.exists())

        rc, out, err = cf("harvest", "re-gated", "--from",
                          str(self.ws / "corpus" / "imp"), "--workspace", str(self.ws),
                          "--gate-config", str(config), "--no-tally")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(self.receipt("re-gated")["gate"]["ok"], out)

    def test_a_rejected_gate_is_named_by_cf_tally(self):
        """The counting authority's own output, on a corpus the gate refused."""
        (self.source / "fabricated.json").write_text(json.dumps({
            "item": "Exercise 1.1.2", "book_located": True,
            "book_passage_quote": "A sentence that is in no edition of any book.",
            "matches": [], "disagreements": [], "coverage_notes": ""}), encoding="utf-8")
        rc, out, _err = cf("import-corpus", "--run-id", "bad", "--from", str(self.source),
                           "--workspace", str(self.ws), "--no-tally",
                           "--preset", "differential-legacy", "--repos", COMPANION)
        self.assertEqual(rc, 1, out)
        self.assertFalse(self.receipt("bad")["gate"]["ok"])

        rc, tallied, warned = cf("tally", "--workspace", str(self.ws), "--no-write")
        self.assertEqual(rc, 0)
        self.assertIn("GATE FAILED run(s): bad", tallied)
        self.assertIn("gate FAILED for run(s): bad", warned)
        self.assertIn("RECEIPT.json gate.problems", warned)


class TestTheImportHintIsRunInAMultiSourceWorkspace(unittest.TestCase):
    """NEW#g-report-3: the same hint, in the workspace shape that refuses it.

    `cf import-corpus` refuses a workspace with more than one source unless it
    is told which one the findings read, and the remedy printed for the one
    state it exists for -- an import that landed ``gate: null`` -- left the flag
    out, so pasting it exited 2 and changed nothing.  The sibling class above
    exercises the same sentence in a ONE-source workspace, which is why it
    passed throughout.
    """

    def setUp(self):
        build_import_workspace(self, SOURCE_ID, OTHER_SOURCE_ID)

    def receipt(self, run_id: str) -> dict:
        return json.loads((self.ws / "corpus" / run_id / "RECEIPT.json").read_text())

    def test_the_workspace_refuses_the_import_without_the_flag(self):
        """The refusal the hint has to answer, named by the command itself."""
        rc, _out, err = cf_cli("import-corpus", "--run-id", "imp", "--from", str(self.source),
                               "--workspace", str(self.ws), "--no-tally",
                               "--preset", "differential-legacy")
        self.assertEqual(rc, 2, err)
        self.assertIn("this workspace has 2 sources", err)
        self.assertIn("--source", err)

    def test_the_printed_invocation_settles_the_ungated_state(self):
        rc, _out, err = cf("import-corpus", "--run-id", "imp", "--from", str(self.source),
                           "--workspace", str(self.ws), "--no-tally", "--preset", "",
                           "--source", SOURCE_ID)
        self.assertEqual(rc, 0, err)
        self.assertIsNone(self.receipt("imp")["gate"], "the fixture must start ungated")

        rc, tallied, _err = cf("tally", "--workspace", str(self.ws), "--no-write")
        self.assertEqual(rc, 0)
        self.assertIn("UNGATED run(s): imp", tallied)
        self.assertIn("cf import-corpus --run-id <run-id> --from <dir> --preset <preset> "
                      "--source <id>", tallied)

        # the sentence that was printed, run verbatim, with this run's own id,
        # directory, preset and source substituted for the placeholders
        rc, _out, err = cf_cli("import-corpus", "--run-id", "imp", "--from", str(self.source),
                               "--workspace", str(self.ws), "--no-tally",
                               "--preset", "differential-legacy", "--source", SOURCE_ID,
                               "--repos", COMPANION, "--force")
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.receipt("imp")["gate"]["ok"], self.receipt("imp")["gate"])

        rc, tallied, _err = cf("tally", "--workspace", str(self.ws), "--no-write")
        self.assertEqual(rc, 0)
        self.assertNotIn("UNGATED", tallied)

    def test_the_one_source_workspace_may_still_drop_the_flag(self):
        """The hint says so, so the sentence without it has to keep working."""
        self.assertIn("drop `--source` in a one-source workspace", tally_mod.REGATE_HINT)


class TestTheReportNamesNoPrivateAlias(unittest.TestCase):
    """The item grammar is reached by its own name, not through a shim's alias."""

    def test_no_underscore_name_of_another_package_is_reached_through(self):
        """`compat._cf_items.try_parse` is what the shim exists to prevent.

        The shim publishes only the canonicaliser, and its own deadline test says
        to move a call site it cannot serve onto cflib rather than to grow it, so
        this one goes to `cflib.items` directly.
        """
        source = (SKILL / "scripts" / "report" / "make_handoff.py").read_text(encoding="utf-8")
        self.assertNotIn("compat._cf_items", source)
        self.assertNotIn("compat._", source.replace("compat._cf_items", ""))

    def test_the_source_s_own_kinds_decide_what_parses(self):
        """A manuscript with an unusual kind list gets a reject, not a guess."""
        self.assertEqual(self.chapters(kinds=()), [], "no default kind list admits Widget")
        self.assertEqual(self.chapters(kinds=("Widget",)), [2])

    def chapters(self, *, kinds) -> list:
        universe = handoff_mod._manuscript_universe(
            {"path": "", "present": True, "count": 0, "by_item": {}, "chapters": [],
             "manuscript": True},
            {"Widget 2.3.4": {COMPANION}}, kinds)
        return universe["chapters"]


class TestEveryCommandTheseWarningsPrintExists(unittest.TestCase):
    def test_each_cf_command_in_the_shared_hint_resolves(self):
        printed = sorted(set(re.findall(r"\bcf ([a-z][a-z-]+)", tally_mod.REGATE_HINT)))
        self.assertTrue(printed, "the hint names no command at all?")
        for name in printed:
            with self.subTest(command=name):
                self.assertEqual(cf(name, "--help")[0], 0,
                                 "`cf %s` is printed but does not exist" % name)


# ---------------------------------------------------------------------------
# NEW#h-report-1 -- a corpus run attributed to a source the workspace does not hold
# ---------------------------------------------------------------------------

#: an id no ``sources:`` block below lists -- what a removed source, or a corpus
#: imported under another workspace's id, leaves behind in the receipts
GHOST_SOURCE_ID = "a-book-3rd-edition"
GHOST_SOURCE_SHA = "c" * 64


class GhostCorpus(Workspace):
    """Helpers for a corpus whose receipt names an id ``workspace.yaml`` lacks."""

    def write_ghost_corpus(self, run_id: str, docs: dict, *, gate: dict | None,
                           source_id: str = GHOST_SOURCE_ID,
                           sha: str = GHOST_SOURCE_SHA) -> str:
        """Like :meth:`Workspace.write_corpus`, but stamped with *source_id*."""
        directory = os.path.join(self.root, "corpus", run_id)
        for name, doc in docs.items():
            if isinstance(doc, str):
                compat.write_text(os.path.join(directory, name), doc)
            else:
                compat.write_json(os.path.join(directory, name), doc)
        compat.write_json(os.path.join(directory, "RECEIPT.json"),
                          {"run_id": run_id, "sources": {source_id: sha},
                           "actual": {"count": len(docs)}, "complete": True, "gate": gate})
        return directory

    def unknown(self) -> list:
        return self.handoff()["context"]["_coverage"]["unknown_source_documents"]


class TestACorpusNamingASourceTheWorkspaceDoesNotHoldIsNotDropped(GhostCorpus):
    """NEW#h-report-1: ``load_examined``'s key space is wider than the workspace's.

    A block is keyed by the document's own ``source_id``, falling back to the
    run's receipt, and ``build_coverage`` read exactly two kinds of key -- the
    ids ``cfg.source_ids()`` lists and ``""``.  A block under any other id had no
    reader anywhere in the tree, so its documents, its findings, its unreadable
    files and its ungated runs left section 0 and ``coverage.json`` without a
    word, while `cf tally` counted the same rows and section 2 printed the run by
    name.  `cf sources remove <id>` after a harvest reaches it with no
    hand-editing at all.
    """

    def setUp(self):
        super().setUp()
        self.write_items([
            {"item": "Definition 1.1.1", "page": 10, "chapter": 1, "kind": "Definition"},
            {"item": "Lemma 1.2.2", "page": 12, "chapter": 1, "kind": "Lemma"},
        ])
        self.dest = self.write_ghost_corpus("second-run", {
            "definition-1-1-1.json": dict(document("Definition 1.1.1"),
                                          source_id=GHOST_SOURCE_ID,
                                          source_sha=GHOST_SOURCE_SHA),
        }, gate={"ok": True, "checked": 1})

    def test_the_block_is_published_with_everything_it_carries(self):
        self.assertEqual(self.unknown(), [{
            "source_id": GHOST_SOURCE_ID, "documents": 1, "items_examined": 1,
            "items_with_findings": 1, "runs": ["second-run"],
            "unreadable_documents": [], "ungated_runs": [], "gate_failed_runs": [],
        }])

    def test_the_authority_counts_the_row_no_figure_counts(self):
        """The disagreement this fix exists to end: one row, two artifacts."""
        rows = tally_mod.rows_for_run("second-run", self.dest, kinds=None)
        self.assertEqual([r["item"] for r in rows], ["Definition 1.1.1"])
        self.assertEqual(self.coverage()["items_with_findings"], 0)

    def test_the_item_behind_it_is_never_published_as_unexamined_unremarked(self):
        """It is still counted as never examined; the document may not be silent."""
        text = self.handoff()["text"]
        published = compat.read_json(
            os.path.join(self.root, "reports", "unexamined.%s.json" % SOURCE_ID))
        self.assertIn("Definition 1.1.1", published["items"])
        self.assertIn("may be counted as never examined above", text)

    def test_the_handoff_names_the_run_the_source_and_a_remedy_that_exists(self):
        text = flat(self.handoff()["text"])
        self.assertIn("name source(s) `%s` that this workspace does not hold" % GHOST_SOURCE_ID,
                      text)
        self.assertIn("in run(s) `second-run`", text)
        self.assertIn("`reports/coverage.json` (`unknown_source_documents`)", text)
        for command, flags in (("sources add", ("--for",)),
                               ("import-corpus", ("--run-id", "--from", "--source"))):
            with self.subTest(command=command):
                self.assertIn("cf %s" % command, text)
                rc, out, _err = cf(*command.split(), "--help")
                self.assertEqual(rc, 0, "`cf %s` does not exist" % command)
                for flag in flags:
                    self.assertIn(flag, out, "`cf %s` does not take %s" % (command, flag))

    def test_the_command_says_so_on_the_terminal_too(self):
        rc, out, err = cf_cli("report", "--workspace", self.root, "--no-verify-args")
        self.assertEqual(rc, 0, err)
        self.assertIn("second-run", out)
        self.assertIn("this workspace does not hold", out)


class TestTheGhostBlockKeepsItsUnreadableAndUngatedLists(GhostCorpus):
    """The stronger shape: the new warnings were swallowed with the block.

    NEW#g-report-1's ``_record_unreadable`` and the ungated backstop both write
    into the block ``load_examined`` keys by the run's receipt, so a receipt
    naming an id this workspace does not hold took both of them out of the
    document with it -- `grep -i ungated` over the handoff then found only the
    template's standing sentence.
    """

    def setUp(self):
        super().setUp()
        self.write_items([
            {"item": "Definition 1.1.1", "page": 10, "chapter": 1, "kind": "Definition"},
            {"item": "Lemma 1.2.2", "page": 12, "chapter": 1, "kind": "Lemma"},
        ])
        # unstamped, so both are attributed from the receipt; one will not parse
        self.dest = self.write_ghost_corpus("imp", {
            "definition-1-1-1.json": {k: v for k, v in document("Definition 1.1.1").items()
                                      if k != "source_id"},
            "lemma-1-2-2.json": "[]\n",
        }, gate=None)

    def test_both_lists_survive_with_the_block(self):
        block = self.unknown()[0]
        self.assertEqual(block["unreadable_documents"], ["imp/lemma-1-2-2.json"])
        self.assertEqual(block["ungated_runs"], ["imp"])
        self.assertEqual(block["runs"], ["imp"], "a run with no per-run count is still named")

    def test_the_handoff_says_ungated_and_lower_bound(self):
        text = flat(self.handoff()["text"])
        self.assertIn("ungated run(s) `imp`: no quote in them was checked", text)
        self.assertIn("`imp/lemma-1-2-2.json`", text)
        self.assertIn("LOWER BOUND", text)

    def test_the_terminal_says_the_same_two_things(self):
        rc, out, err = cf_cli("report", "--workspace", self.root, "--no-verify-args")
        self.assertEqual(rc, 0, err)
        self.assertIn("ungated run(s) imp", out)
        self.assertIn("imp/lemma-1-2-2.json", out)


class TestEveryKeyOfTheExaminedMapHasAReader(GhostCorpus):
    """Nothing ``load_examined`` builds may be unreachable again.

    The three readers in ``build_coverage`` have to partition its key space, so
    this fixture carries one key of every kind there is: a source the workspace
    holds, the ``""`` of a run whose receipt names two sources, a ghost id a
    document stamped itself, and a ghost id only a receipt names.
    """

    def setUp(self):
        super().setUp()
        self.write_items([{"item": "Definition 1.1.1", "page": 10, "chapter": 1,
                           "kind": "Definition"}])
        self.write_corpus("owned", {"definition-1-1-1.json": document("Definition 1.1.1")},
                          gate={"ok": True, "checked": 1})
        self.write_ghost_corpus("ghost-stamped", {
            "lemma-1-2-2.json": dict(document("Lemma 1.2.2"), source_id=GHOST_SOURCE_ID,
                                     source_sha=GHOST_SOURCE_SHA)},
            gate={"ok": True, "checked": 1})
        self.write_ghost_corpus("ghost-receipt", {
            "lemma-1-3-3.json": {k: v for k, v in document("Lemma 1.3.3").items()
                                 if k != "source_id"}},
            gate={"ok": True, "checked": 1}, source_id="a-book-4th-edition")
        # two sources in one receipt: `_run_source` refuses to guess, so the ""
        # key is the one that catches these
        directory = os.path.join(self.root, "corpus", "wide")
        compat.write_json(os.path.join(directory, "lemma-1-4-4.json"),
                          {k: v for k, v in document("Lemma 1.4.4").items()
                           if k != "source_id"})
        compat.write_json(os.path.join(directory, "RECEIPT.json"),
                          {"run_id": "wide",
                           "sources": {GHOST_SOURCE_ID: GHOST_SOURCE_SHA,
                                       "a-book-4th-edition": GHOST_SOURCE_SHA},
                           "actual": {"count": 1}, "complete": True,
                           "gate": {"ok": True, "checked": 1}})

    def examined_map(self) -> dict:
        runs = handoff_mod.load_runs(self.paths)
        return handoff_mod.load_examined(self.paths, runs,
                                         handoff_mod.load_receipts(self.paths))

    def test_the_fixture_really_holds_one_key_of_every_kind(self):
        self.assertEqual(sorted(self.examined_map()),
                         ["", SOURCE_ID, GHOST_SOURCE_ID, "a-book-4th-edition"])

    def test_no_key_is_left_without_one(self):
        coverage = self.handoff()["context"]["_coverage"]
        read = {s["source_id"] for s in coverage["sources"]}
        read |= {b["source_id"] for b in coverage["unknown_source_documents"]}
        # the unattributed reader is the third, and its key is the empty one
        read.add("")
        unread = sorted(set(self.examined_map()) - read)
        self.assertEqual(unread, [], "these blocks have no reader anywhere in the tree")

    def test_an_ungated_run_under_the_empty_key_is_said_too(self):
        """Every FIELD of the unattributed block has a reader, not only its items.

        A run whose receipt names two sources lands under ``""``; when no gate
        checked it, `cf tally` said so and the handoff -- the artifact that
        travels -- did not.
        """
        receipt = os.path.join(self.root, "corpus", "wide", "RECEIPT.json")
        doc = compat.read_json(receipt)
        doc["gate"] = None
        compat.write_json(receipt, doc)
        made = self.handoff()
        self.assertEqual(made["context"]["_coverage"]["unattributed_ungated_runs"], ["wide"])
        text = flat(made["text"])
        self.assertRegex(text, r"ungated run\(s\) [^.]*wide[^.]*: no quote in them was checked")

    def test_each_of_the_three_readers_says_its_own_share(self):
        text = flat(self.handoff()["text"])
        self.assertIn("1 corpus document(s) in run(s) `wide` name no source", text)
        self.assertIn("2 corpus document(s) in run(s) `ghost-receipt`, `ghost-stamped` name "
                      "source(s) `%s`, `a-book-4th-edition`" % GHOST_SOURCE_ID, text)
        self.assertIn("1 examined (1 of them gated)", text)


class TestRemovingASourceNamesTheCorpusThatStillPointsAtIt(GhostCorpus):
    """`cf sources remove` is how an ordinary workspace reaches the shape above.

    It dropped the id and said nothing about the runs judged against it, so the
    next `cf report` counted their documents under no figure at all while
    `cf tally` went on counting their rows.
    """

    EXTRA_SOURCES = ("  %s:\n"
                     '    sha256: "%s"\n'
                     "    title: A Book\n"
                     '    edition: "second edition"\n'
                     "    offset: 16\n"
                     "    role: reference\n" % (OTHER_SOURCE_ID, OTHER_SOURCE_SHA))

    def setUp(self):
        super().setUp()
        self.write_items([{"item": "Definition 1.1.1", "page": 10, "chapter": 1,
                           "kind": "Definition"}])
        self.write_ghost_corpus("second-run", {
            "definition-1-1-1.json": dict(document("Definition 1.1.1"),
                                          source_id=OTHER_SOURCE_ID,
                                          source_sha=OTHER_SOURCE_SHA)},
            gate={"ok": True, "checked": 1},
            source_id=OTHER_SOURCE_ID, sha=OTHER_SOURCE_SHA)

    def test_the_removal_names_the_run_and_a_command_that_exists(self):
        rc, out, err = cf_cli("sources", "remove", "--workspace", self.root, OTHER_SOURCE_ID)
        self.assertEqual(rc, 0, err)
        said = out + err
        self.assertIn("second-run", said)
        self.assertIn("corpus run(s) were judged against %s" % OTHER_SOURCE_ID, said)
        self.assertIn("cf import-corpus --run-id <run-id> --from <dir> --source <id>", said)

    def test_a_run_only_its_plan_attributes_is_named_too(self):
        """`cf report` attributes from the receipt OR run.yaml, so the warning reads both."""
        receipt = os.path.join(self.root, "corpus", "second-run", "RECEIPT.json")
        doc = compat.read_json(receipt)
        doc.pop("sources")
        compat.write_json(receipt, doc)
        compat.write_text(os.path.join(self.root, "runs", "second-run", "run.yaml"),
                          "run_id: second-run\nsources:\n  %s:\n    sha256: %s\n"
                          % (OTHER_SOURCE_ID, OTHER_SOURCE_SHA))
        rc, out, err = cf_cli("sources", "remove", "--workspace", self.root, OTHER_SOURCE_ID)
        self.assertEqual(rc, 0, err)
        self.assertIn("second-run", out + err)

    def test_a_source_no_corpus_names_is_removed_without_the_warning(self):
        """It is a warning about a real corpus, not a line every removal prints."""
        rc, out, err = cf_cli("sources", "remove", "--workspace", self.root, SOURCE_ID)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("were judged against", out + err)

    def test_the_report_after_the_removal_still_accounts_for_the_run(self):
        self.assertEqual(cf_cli("sources", "remove", "--workspace", self.root,
                                OTHER_SOURCE_ID)[0], 0)
        self.assertEqual([b["source_id"] for b in self.unknown()], [OTHER_SOURCE_ID])
        self.assertIn("that this workspace does not hold", flat(self.handoff()["text"]))


# ---------------------------------------------------------------------------
# NEW#h-report-2 -- the other four caveats, on the branches that end early
# ---------------------------------------------------------------------------

class TestTheCaveatsAreSaidWhereTheRestOfTheNoteIsATodo(Workspace):
    """Branch 3: no ``items.json``, so the note is one TODO line -- and a run no gate saw.

    Commit 25cbda3 fixed this hole for the unreadable sentence alone; the
    ungated, gate-FAILED, out-of-scope and unstamped ones stayed below the
    ``continue``s, and unlike the unreadable one they were not echoed on
    `cf report`'s terminal either.  "No quote in this corpus was checked against
    the source text" is the safety claim the whole gate exists for.
    """

    def setUp(self):
        super().setUp()
        self.write_corpus("legacy", {"definition-1-1-1.json": document("Definition 1.1.1")},
                          gate=None)

    def test_the_source_has_no_denominator_at_all(self):
        cov = self.coverage()
        self.assertIsNone(cov["items_in_source"])
        self.assertEqual(cov["ungated_runs"], ["legacy"])

    def test_the_handoff_names_the_ungated_run_not_only_cf_tally(self):
        text = flat(self.handoff()["text"])
        self.assertIn("- `%s`: no `items.json` yet" % SOURCE_ID, text)
        self.assertIn("ungated run(s) `legacy`: no quote in them was checked against the "
                      "source text", text)

    def test_the_authority_that_does_say_it_still_says_it(self):
        """`cf tally` printed this for the same corpus all along."""
        result = tally_mod.tally({"legacy": os.path.join(self.root, "corpus", "legacy")})
        self.assertIn("legacy", tally_mod.render(result))

    def test_the_terminal_echoes_the_same_caveat(self):
        rc, out, err = cf_cli("report", "--workspace", self.root, "--no-verify-args")
        self.assertEqual(rc, 0, err)
        self.assertIn("ungated run(s) legacy", out)
        self.assertIn("reports/coverage.json", out)


class TestAFailedGateIsSaidOnTheManuscriptBranchToo(Workspace):
    """Branch 2: an unpaired manuscript, whose note is one "run `cf pair`" line."""

    def setUp(self):
        super().setUp()
        self.write_items([])
        self.write_meta(source_format="text", pagination="none")
        self.write_corpus("hunt", {"definition-1-1-1.json": document("Definition 1.1.1")},
                          gate={"ok": False, "checked": 1, "problems": ["a quote is not in the text"]})

    def test_the_note_really_is_the_manuscript_branch(self):
        cov = self.coverage()
        self.assertTrue(cov["manuscript"])
        self.assertIsNone(cov["items_in_source"])
        self.assertEqual(cov["gate_failed_runs"], ["hunt"])

    def test_the_handoff_does_not_call_the_rejected_quotes_unchecked(self):
        text = flat(self.handoff()["text"])
        self.assertIn("a manuscript — its items are the tagged environments", text)
        self.assertIn("gate FAILED for run(s) `hunt`", text)
        self.assertNotIn("ungated run(s) `hunt`", text)


class TestTheOutOfScopeSentenceIsSaidForASourceNobodyIsBoundTo(Workspace):
    """Branch 1: not under audit, a chapter pin, and the only finding outside it.

    Section 0's row prints a hard 0 under "with findings" for this source while
    §3 counts a CONTENT row on the item, and the whole note was two sentences
    that say nothing about either.
    """

    EXTRA_SOURCES = ("  %s:\n"
                     '    sha256: "%s"\n'
                     "    title: A Book\n"
                     '    edition: "second edition"\n'
                     "    offset: 16\n"
                     "    chapters: [1]\n"
                     "    role: reference\n" % (OTHER_SOURCE_ID, OTHER_SOURCE_SHA))

    def setUp(self):
        super().setUp()
        self.write_items([{"item": "Exercise 1.1.1", "page": 10, "chapter": 1,
                           "kind": "Exercise"}])
        self.write_items([{"item": "Exercise 1.1.1", "page": 9, "chapter": 1,
                           "kind": "Exercise"}],
                         source_id=OTHER_SOURCE_ID, sha=OTHER_SOURCE_SHA)
        directory = os.path.join(self.root, "corpus", "legacy")
        compat.write_json(os.path.join(directory, "lemma-2-2-2.json"),
                          dict(document("Lemma 2.2.2"), source_id=OTHER_SOURCE_ID,
                               source_sha=OTHER_SOURCE_SHA))
        compat.write_text(os.path.join(directory, "broken.json"), "[]\n")
        compat.write_json(os.path.join(directory, "RECEIPT.json"),
                          {"run_id": "legacy", "sources": {OTHER_SOURCE_ID: OTHER_SOURCE_SHA},
                           "actual": {"count": 2}, "complete": True, "gate": None})

    def unbound(self) -> dict:
        return next(s for s in self.handoff()["context"]["_coverage"]["sources"]
                    if s["source_id"] == OTHER_SOURCE_ID)

    def test_the_figures_do_carry_what_the_note_left_unsaid(self):
        cov = self.unbound()
        self.assertFalse(cov["under_audit"])
        self.assertEqual(cov["with_findings_outside_scope"], ["Lemma 2.2.2"])
        self.assertEqual(cov["ungated_runs"], ["legacy"])
        self.assertEqual(cov["unreadable_documents"], ["legacy/broken.json"])

    def test_the_row_prints_zero_and_the_note_now_says_why(self):
        text = self.handoff()["text"]
        row = next(l for l in text.split("## 0.")[1].split("## 1.")[0].splitlines()
                   if l.startswith("| `%s` |" % OTHER_SOURCE_ID))
        self.assertIn("| — | 1 | — | — | — | 0 | 0 | 0 | — |", row)
        flattened = flat(text)
        self.assertIn("1 examined item(s) lie OUTSIDE the audited chapter(s)", flattened)
        self.assertIn("`Lemma 2.2.2`", flattened)
        self.assertIn("ungated run(s) `legacy`", flattened)
        self.assertIn("LOWER BOUND", flattened)

    def test_the_terminal_echoes_all_of_them(self):
        rc, out, err = cf_cli("report", "--workspace", self.root, "--no-verify-args")
        self.assertEqual(rc, 0, err)
        self.assertIn("ungated run(s) legacy", out)
        self.assertIn("lie outside the audited chapter(s)", out)
        self.assertIn("legacy/broken.json", out)


class TestTheCaveatsAreOneHelperOnEveryBranch(unittest.TestCase):
    """The five sentences are one list, so no branch can carry a subset again."""

    SRC = {"source_id": SOURCE_ID, "role": "audited", "unreadable_documents": ["r/a.json"],
           "examined_outside_scope": ["Lemma 2.1.1"],
           "with_findings_outside_scope": ["Lemma 2.1.1"], "chapters_outside_scope": ["2"],
           "ungated_runs": ["r"], "gate_failed_runs": ["s"], "unstamped_documents": 3}

    def test_every_caveat_is_in_the_helper(self):
        lines = handoff_mod._caveat_lines(self.SRC)
        self.assertEqual(len(lines), 5, lines)
        for fragment in ("could not be read", "lie OUTSIDE the audited chapter(s)",
                         "ungated run(s)", "gate FAILED for run(s)",
                         "carry no `source_id`"):
            with self.subTest(fragment=fragment):
                self.assertTrue(any(fragment in l for l in lines), lines)

    def test_every_line_is_nested_under_the_source_bullet(self):
        for line in handoff_mod._caveat_lines(self.SRC):
            self.assertTrue(line.startswith("  - "), line)

    def test_each_branch_of_the_note_calls_it(self):
        """Three early `continue`s plus the main path, read off the source."""
        body = (SKILL / "scripts" / "report" / "make_handoff.py").read_text(encoding="utf-8")
        note = body.split("def coverage_note(")[1].split("\ndef ")[0]
        self.assertEqual(note.count("_caveat_lines(src)"), 4, note)
        self.assertNotIn("_unreadable_lines(src)", note,
                         "the helper is the only caller left")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
