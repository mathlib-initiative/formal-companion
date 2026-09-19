"""Review fixes for the harvest / union / agreed / ledger-link stage.

One class per finding, named after what used to be wrong.  The four findings
these cover all have the same shape -- a count or a join that failed toward
"cleaner" -- so every test here asserts the pessimistic answer: an id nobody
recomputed is not trusted, two claims at one id are two claims, two runs that
faulted the same page did not necessarily agree, and a corpus nobody gated says
so out loud.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib.errors import CfError  # noqa: E402
from cflib.hashing import finding_id  # noqa: E402
from harvest import agreed as agreed_mod  # noqa: E402
from harvest import harvest as harvest_mod  # noqa: E402
from harvest import tally as tally_mod  # noqa: E402
from harvest import union as union_mod  # noqa: E402
from ledger import findings as findings_mod  # noqa: E402
from ledger import validate_ledger as vl  # noqa: E402

SOURCE_ID = "booklet-1st"
SOURCE_SHA = "a1" * 32


def scratch(case: unittest.TestCase) -> Path:
    """A temp directory that honours CF_TEST_TMP and is removed afterwards."""
    path = Path(tempfile.mkdtemp(dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def write_doc(directory: Path, item: str, defects: list[dict], *,
              source_id: str = SOURCE_ID, source_sha: str = SOURCE_SHA,
              name: str = "") -> Path:
    """One findings document, as a run merges them."""
    directory.mkdir(parents=True, exist_ok=True)
    tail = name or (item.lower().replace(" ", "-").replace(".", "-") + ".json")
    path = directory / tail
    path.write_text(json.dumps({
        "schema_version": 2, "item": item, "source_id": source_id,
        "source_sha": source_sha, "book_located": True, "book_passage_quote": "q",
        "matches": [], "disagreements": defects, "coverage_notes": "",
    }), encoding="utf-8")
    return path


def defect(**over) -> dict:
    row = {"kind": "CONTENT", "severity": "high", "concept": "the concept",
           "reasoning": "because the hypothesis differs",
           "book_quote": "Let E be a measurable set.",
           "unfaithful_decls": ["ours:Foo.bar"]}
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# gates-correctness#4


class TestFindingIdIsRecomputedNotTrusted(unittest.TestCase):
    """references/vocabulary.md promises the id is assigned at harvest. Now it is."""

    def setUp(self):
        self.root = scratch(self)
        self.corpus = self.root / "corpus" / "run-a"
        write_doc(self.corpus, "Exercise 1.2.9",
                  [defect(finding_id="00" * 32)])

    def test_the_row_carries_the_computed_id(self):
        rows = tally_mod.rows_for_run("run-a", self.corpus)
        self.assertEqual(len(rows), 1)
        want = finding_id("run-a", SOURCE_ID, "Exercise 1.2.9", "CONTENT", ["ours:Foo.bar"])
        self.assertEqual(rows[0]["finding_id"], want)
        self.assertNotEqual(rows[0]["finding_id"], "00" * 32)

    def test_the_id_the_worker_wrote_is_recorded_not_discarded(self):
        rows = tally_mod.rows_for_run("run-a", self.corpus)
        self.assertEqual(rows[0]["finding_id_stored"], "00" * 32)
        mismatches = tally_mod.id_mismatches(rows)
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["stored"], "00" * 32)
        self.assertEqual(mismatches[0]["item"], "Exercise 1.2.9")

    def test_the_tally_document_and_the_table_both_say_so(self):
        result = tally_mod.tally({"run-a": self.corpus})
        self.assertEqual(len(result["finding_id_mismatches"]), 1)
        self.assertIn("FINDING_ID MISMATCH", tally_mod.render(result))

    def test_a_correct_id_is_not_reported(self):
        good = self.root / "corpus" / "run-b"
        write_doc(good, "Exercise 1.2.9", [defect(
            finding_id=finding_id("run-b", SOURCE_ID, "Exercise 1.2.9", "CONTENT",
                                  ["ours:Foo.bar"]))])
        rows = tally_mod.rows_for_run("run-b", good)
        self.assertNotIn("finding_id_stored", rows[0])
        self.assertEqual(tally_mod.id_mismatches(rows), [])

    def test_the_id_hashes_the_whole_defect_not_the_row_s_repo_slice(self):
        """One defect naming two repos is two rows and ONE id."""
        two = self.root / "corpus" / "run-c"
        write_doc(two, "Exercise 1.2.9",
                  [defect(unfaithful_decls=["ours:Foo.bar", "theirs:Baz.qux"])])
        rows = tally_mod.rows_for_run("run-c", two)
        self.assertEqual({r["repo"] for r in rows}, {"ours", "theirs"})
        self.assertEqual(len({r["finding_id"] for r in rows}), 1)
        self.assertEqual(rows[0]["finding_id"],
                         finding_id("run-c", SOURCE_ID, "Exercise 1.2.9", "CONTENT",
                                    ["ours:Foo.bar", "theirs:Baz.qux"]))

    def test_the_ledger_reader_recomputes_it_too(self):
        rows = findings_mod.load_findings([str(self.corpus)])
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["finding_id"], "00" * 32)
        self.assertEqual(rows[0]["finding_id_stored"], "00" * 32)


class TestTwoClaimsAtOneIdAreVisible(unittest.TestCase):
    """The id excludes prose, so two different defects on one decl share it."""

    def setUp(self):
        self.root = scratch(self)
        corpus = self.root / "corpus" / "run-a"
        write_doc(corpus, "Exercise 1.2.9",
                  [defect(concept="the hypothesis E measurable is missing"),
                   defect(concept="the conclusion is stated for open sets only")])
        self.findings = findings_mod.load_findings([str(corpus)])
        self.assertEqual(len(self.findings), 2)
        self.assertEqual(self.findings[0]["finding_id"], self.findings[1]["finding_id"])
        self.fid = self.findings[0]["finding_id"]

    def _ledger_row(self) -> dict:
        return {
            "finding_id": self.fid, "item": "Exercise 1.2.9", "repo": "ours",
            "kind": "CONTENT", "unfaithful_decls": ["ours:Foo.bar"],
            "verdict": "CONVENTION", "severity": "none",
            "reason": "the companion's standing convention covers it",
            "recovery_argument": "x" * (vl.MIN_RECOVERY_CHARS + 1),
            "resolution": "argued_correct",
            "source_id": SOURCE_ID, "source_sha": SOURCE_SHA,
        }

    def test_the_index_reports_the_collision_when_asked(self):
        collisions: list[dict] = []
        index = findings_mod.index_by_id(self.findings, collisions=collisions)
        self.assertEqual(len(index), 1, "first-wins is still the index")
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0]["claims"], 2)
        self.assertEqual(collisions[0]["finding_id"], self.fid)

    def test_two_identical_rows_are_not_a_collision(self):
        twice = [self.findings[0], dict(self.findings[0])]
        self.assertEqual(findings_mod.id_collisions(twice), [])

    def test_one_ledger_row_does_not_close_both(self):
        cov = vl.ledger_coverage([self._ledger_row()], self.findings)
        self.assertEqual(cov["findings"], 2)
        self.assertEqual(cov["covered"], 0, "neither claim can be attributed to the one row")
        self.assertEqual(cov["missing"], 2)
        self.assertEqual(len(cov["collisions"]), 1)
        self.assertTrue(cov["collisions"][0]["answered"])

    def test_an_uncontested_finding_is_still_covered(self):
        one = [self.findings[0]]
        cov = vl.ledger_coverage([self._ledger_row()], one)
        self.assertEqual((cov["findings"], cov["covered"], cov["missing"]), (1, 1, 0))
        self.assertEqual(cov["collisions"], [])


# ---------------------------------------------------------------------------
# gates-correctness#5


class TestAgreementMeansTheSameComplaint(unittest.TestCase):
    """Two runs that both looked hard at page 71 have not corroborated anything."""

    @staticmethod
    def row(run: str, item_raw: str, kind: str, decl: str, **over) -> dict:
        row = {"run": run, "mode": "differential", "repo": "ours",
               "source_id": SOURCE_ID, "source_sha": SOURCE_SHA,
               "item_raw": item_raw, "item": tally_mod.collapse_to_book_item(item_raw),
               "kind": kind, "severity": "high", "decls": [decl] if decl else []}
        row.update(over)
        return row

    def test_two_kinds_on_one_page_are_not_agreement(self):
        rows = [self.row("A", "In-text, book p. 71", "CONTENT", "ours:First"),
                self.row("B", "In-text remark, book p. 71", "VACUITY", "ours:Second")]
        self.assertEqual(agreed_mod.agreed(rows), [])
        weak = agreed_mod.same_locus(rows)
        self.assertEqual(len(weak), 1)
        self.assertEqual(weak[0]["item"], "In-text p.71")
        self.assertEqual(weak[0]["runs"], ["A", "B"])
        self.assertIn("different kinds", weak[0]["why_not_agreement"])

    def test_two_sub_parts_with_disjoint_declarations_are_not_agreement(self):
        rows = [self.row("A", "Exercise 1.1.2(i)", "CONTENT", "ours:First"),
                self.row("B", "Exercise 1.1.2(iii)", "CONTENT", "ours:Second")]
        self.assertEqual(agreed_mod.agreed(rows), [])
        weak = agreed_mod.same_locus(rows)
        self.assertEqual([e["item"] for e in weak], ["Exercise 1.1.2"])
        self.assertIn("no declaration named by every run",
                      weak[0]["why_not_agreement"])
        # and the reviewer can see which part each run hit
        self.assertEqual(weak[0]["by_run"]["A"]["items_raw"], ["Exercise 1.1.2(i)"])
        self.assertEqual(weak[0]["by_run"]["B"]["items_raw"], ["Exercise 1.1.2(iii)"])

    def test_the_same_complaint_about_one_declaration_still_agrees(self):
        rows = [self.row("A", "Exercise 1.1.2(i)", "CONTENT", "ours:First"),
                self.row("B", "Exercise 1.1.2", "CONTENT", "ours:First", mode="attack")]
        entries = agreed_mod.agreed(rows)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["item"], "Exercise 1.1.2")
        self.assertEqual(entries[0]["kind"], "CONTENT")
        self.assertEqual(entries[0]["shared_decls"], ["ours:First"])
        self.assertTrue(entries[0]["cross_family"])
        self.assertEqual(entries[0]["items_raw"],
                         ["Exercise 1.1.2", "Exercise 1.1.2(i)"])
        self.assertEqual(agreed_mod.same_locus(rows), [],
                         "a locus that agreed is not also reported as weaker evidence")

    def test_rows_that_name_no_declaration_agree_on_the_locus_alone(self):
        """There is nothing to intersect, so the ``_unattributed`` bucket keeps
        the behaviour it had -- COVERAGE findings name no decl by construction."""
        rows = [self.row("A", "Exercise 1.1.21", "COVERAGE", "", repo="_unattributed"),
                self.row("B", "Exercise 1.1.21", "COVERAGE", "", repo="_unattributed")]
        entries = agreed_mod.agreed(rows, substantive_only=False)
        self.assertEqual([e["item"] for e in entries], ["Exercise 1.1.21"])
        self.assertEqual(entries[0]["shared_decls"], [])

    def test_the_document_states_the_key_it_used(self):
        root = scratch(self)
        write_doc(root / "corpus" / "run-a", "Exercise 1.2.9", [defect()])
        write_doc(root / "corpus" / "run-b", "Exercise 1.2.9", [defect()])
        doc = agreed_mod.build({"run-a": root / "corpus" / "run-a",
                                "run-b": root / "corpus" / "run-b"})
        self.assertIn("kind", doc["criteria"]["grouped_by"])
        self.assertIn("declaration", doc["criteria"]["grouped_by"])
        self.assertEqual(doc["count"], 1)
        self.assertEqual(doc["same_locus_count"], 0)
        self.assertIn("same_locus", doc)


# ---------------------------------------------------------------------------
# gates-correctness#6


class TestGroupByFindingIdLinksAcrossRuns(unittest.TestCase):
    """`--group-by finding-id` is the flag an operator reaches for for precision.

    It had no test, and it answered "everything is new": the run id is the first
    part of the hash, so one defect found twice had two ids.
    """

    def setUp(self):
        self.root = scratch(self)
        self.runs = {}
        for run in ("run-a", "run-b"):
            directory = self.root / "corpus" / run
            write_doc(directory, "Exercise 1.2.9", [defect()])
            self.runs[run] = directory
        self.rows = tally_mod.collect(self.runs)

    def test_the_stored_ids_really_do_differ_by_run(self):
        self.assertEqual(len({r["finding_id"] for r in self.rows}), 2)
        self.assertEqual(len({r["defect_key"] for r in self.rows}), 1)

    def test_one_defect_in_two_runs_is_one_entry(self):
        entries = union_mod.union(self.rows, group_by="finding-id")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["runs"], ["run-a", "run-b"])
        self.assertEqual(union_mod.summary(entries)["multi_run"], 1)
        self.assertEqual(len(entries[0]["finding_ids"]), 2, "one id per run, kept")
        self.assertEqual(len(entries[0]["defect_keys"]), 1)

    def test_a_re_found_defect_is_not_new(self):
        for group_by in ("item-kind", "finding-id"):
            with self.subTest(group_by=group_by):
                self.assertEqual(
                    union_mod.union(self.rows, group_by=group_by,
                                    baseline_runs=["run-a"], new_only=True),
                    [])

    def test_it_still_separates_two_defects_the_default_key_merges(self):
        """This is what the exact grouping is FOR: same item, same kind, two decls."""
        other = self.root / "corpus" / "run-c"
        write_doc(other, "Exercise 1.2.9",
                  [defect(unfaithful_decls=["ours:Foo.bar"]),
                   defect(unfaithful_decls=["ours:Other.decl"])])
        rows = tally_mod.collect({"run-c": other})
        self.assertEqual(len(union_mod.union(rows, group_by="item-kind")), 1)
        self.assertEqual(len(union_mod.union(rows, group_by="finding-id")), 2)

    def test_rows_with_no_run_independent_key_are_refused_not_mis_grouped(self):
        stripped = [{k: v for k, v in row.items() if k != "defect_key"}
                    for row in self.rows]
        with self.assertRaises(CfError) as caught:
            union_mod.union(stripped, group_by="finding-id")
        self.assertIn("--group-by item-kind", str(caught.exception.hint))
        # one run needs no cross-run linking, so it is allowed
        one = [r for r in stripped if r["run"] == "run-a"]
        self.assertEqual(len(union_mod.union(one, group_by="finding-id")), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
