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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
