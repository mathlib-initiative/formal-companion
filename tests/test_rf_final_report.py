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


class ManuscriptFixture(Workspace):
    """A self-authored manuscript: its items.json is empty ON PURPOSE."""

    PAIRED = True

    def setUp(self):
        super().setUp()
        self.write_items([])
        self.write_meta(source_format="text", pagination="none", locator="section")
        if self.PAIRED:
            self.write_pair_index(MANUSCRIPT_ITEMS)
        self.write_corpus("hunt", {
            "definition-1-1-1.json": document("Definition 1.1.1"),
            "lemma-1-2-2.json": dict(document("Lemma 1.2.2"), disagreements=[]),
            "exercise-1-3-3.json": dict(document("Exercise 1.3.3"), disagreements=[]),
        }, gate={"ok": True, "checked": 3})


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
                               ("import-corpus", ("--run-id", "--from", "--preset"))):
            with self.subTest(command=command):
                rc, out, _err = cf(command, "--help")
                self.assertEqual(rc, 0, "`cf %s` does not exist" % command)
                for flag in flags:
                    self.assertIn(flag, out, "`cf %s` does not take %s" % (command, flag))

    def test_the_hint_is_a_spelling_not_one_machine_s_paths(self):
        """Every argument in it is an angle-bracketed placeholder (CLAUDE.md S1)."""
        self.assertNotRegex(tally_mod.REGATE_HINT, r"(^|\s)/\w")
        self.assertEqual(sorted(set(re.findall(r"<[a-z-]+>", tally_mod.REGATE_HINT))),
                         ["<dir>", "<file>", "<preset>", "<run-id>"])


class TestTheHintIsRunAndItSettlesIt(unittest.TestCase):
    """The import branch of the hint, executed against a real ungated corpus."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="cf-rf-final-cli-",
                                                 dir=TMPDIR)).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = self.tmp / "ws"
        self.source = self.tmp / "findings"
        self.source.mkdir(parents=True)
        (self.source / "exercise-1-1-1.json").write_text(json.dumps({
            "item": "Exercise 1.1.1", "book_located": True, "book_passage_quote": QUOTE,
            "matches": [], "disagreements": [], "coverage_notes": ""}), encoding="utf-8")
        self.assertEqual(cf("init", str(self.ws), "--no-git", "-q")[0], 0)
        (self.ws / "workspace.yaml").write_text(
            "version: 2\nworkspace:\n  name: ws\ncompanions: {}\nsources:\n"
            "  %s:\n" % SOURCE_ID
            + '    sha256: "%s"\n' % SOURCE_SHA
            + "    title: A Book\n    edition: \"first edition\"\n    offset: 16\n"
              "    chapters: [1]\n    role: audited\n",
            encoding="utf-8")
        text = self.ws / "sources" / SOURCE_ID / "text.txt"
        text.parent.mkdir(parents=True, exist_ok=True)
        text.write_text("Exercise 1.1.1 (Boolean closure). " + QUOTE + "\n", encoding="utf-8")

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
