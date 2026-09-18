"""Regression tests for harvest, tally, union, agreed and the run planner.

The numbers asserted here are not invented: they are what the baseline corpus
at ``$CF_BASELINE_CORPUS`` (the five runs of the 2026-09 audit, 2,672 findings
files) actually contains, and they are the numbers
tao-measure-lean/docs/OPEN_FAITHFULNESS_FINDINGS.md publishes -- 72 rows over 40
items for ``ours``, of which 16 are CONTENT/VACUITY, 1 a suspected erratum, 1
coverage-only and 22 pagination.  If a change to tally.py moves any of them, it
has changed what the audit says, which is exactly the event these tests exist to
catch.

Every test that needs the baseline skips cleanly when its env var is unset:

    CF_BASELINE_CORPUS=~/Desktop/tao-diff-run python3 -m unittest discover tests

``CF_TEST_TMP`` (optional) puts the scratch directories somewhere other than the
system temp dir.
"""
from __future__ import annotations

import io
import re
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

from cflib import items as cf_items  # noqa: E402
from harvest import agreed as agreed_mod  # noqa: E402
from harvest import harvest as harvest_mod  # noqa: E402
from harvest import harvest_journal  # noqa: E402
from harvest import tally as tally_mod  # noqa: E402
from harvest import union as union_mod  # noqa: E402

BASELINE = os.environ.get("CF_BASELINE_CORPUS")
#: the five baseline runs, in the order OPEN_FAITHFULNESS_FINDINGS.md lists them
BASELINE_RUNS = ("findings", "findings-pdf", "findings-gpt", "findings-vac", "findings-hunt")


def baseline_runs() -> dict:
    return {name: Path(BASELINE) / name for name in BASELINE_RUNS}


def scratch(case: unittest.TestCase) -> Path:
    """A temp directory that honours CF_TEST_TMP and is removed afterwards."""
    path = Path(tempfile.mkdtemp(dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


#: the two Tao editions, by the ids registry/sources.yaml gives them
PUBLISHED_ID = "tao-measure-gsm126-published"
DRAFT_ID = "tao-measure-2011-draft"
PUBLISHED_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
DRAFT_SHA = "62d95658" + "0" * 56


def write_findings(directory: Path, item: str, defects: list[dict],
                   source_id: str | None = None, source_sha: str | None = None) -> Path:
    """A minimal findings file, as the runs merged them.

    With no ``source_id``/``source_sha`` it is v1-shaped: a document that does
    not say which source it read, which is what the corpus looked like before
    ``convert_v1`` stamped it.  The name is qualified by the source when one is
    given, because ``exercise-1-1-1.json`` names two different items in two
    sources and one directory holds both.
    """
    directory.mkdir(parents=True, exist_ok=True)
    tail = item.lower().replace(" ", "-").replace(".", "-")
    name = (f"{source_id}--{tail}.json" if source_id else f"{tail}.json")
    path = directory / name
    payload = {"item": item, "book_located": True, "book_passage_quote": "q",
               "matches": [], "disagreements": defects, "coverage_notes": ""}
    if source_id:
        payload["source_id"] = source_id
    if source_sha:
        payload["source_sha"] = source_sha
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# the item grammar used to collapse findings onto book items


class TestCollapse(unittest.TestCase):
    """collapse_to_book_item is what turns 40 raw labels into 39 book items."""

    def test_sub_part_collapses(self):
        self.assertEqual(tally_mod.collapse_to_book_item("Exercise 1.6.27(iii)"),
                         "Exercise 1.6.27")

    def test_irregular_whitespace(self):
        # the baseline lost "Exercise    1.3.19" to a single-space regex
        self.assertEqual(tally_mod.collapse_to_book_item("Exercise    1.3.19"),
                         "Exercise 1.3.19")
        self.assertEqual(tally_mod.collapse_to_book_item("Exercise\t1.3.19"),
                         "Exercise 1.3.19")

    def test_prose_wrapped_label(self):
        # teorth's "In-text definition, Exercise 1.4.24" is a finding about the
        # in-text definition Exercise 1.4.24 uses, which the extraction lists as
        # its own item ("In-text Exercise 1.4.24").  It collapses onto THAT item,
        # not onto the numbered exercise: they are two items, and merging them
        # let a finding about one hide a finding about the other.
        self.assertEqual(tally_mod.collapse_to_book_item("In-text definition, Exercise 1.4.24"),
                         "In-text Exercise 1.4.24")
        self.assertEqual(tally_mod.collapse_to_book_item("In-text Exercise 1.4.24"),
                         "In-text Exercise 1.4.24")

    def test_prose_wrapped_numbered_label(self):
        # a NUMBERED label still collapses positionally, exactly as before
        self.assertEqual(tally_mod.collapse_to_book_item("Corollary (Lemma 1.2.5 + Exercise 1.2.4)"),
                         "Lemma 1.2.5")

    def test_in_text_page_label_has_no_book_item(self):
        self.assertEqual(tally_mod.collapse_to_book_item("In-text, book p. 68"), "In-text p.68")

    def test_two_level_number(self):
        self.assertEqual(tally_mod.collapse_to_book_item("Theorem 1.7"), "Theorem 1.7")


class TestRunMode(unittest.TestCase):
    def test_mode_from_run_id(self):
        self.assertEqual(tally_mod.run_mode("findings-vac"), "attack")
        self.assertEqual(tally_mod.run_mode("attack-hunt-claude"), "attack")
        self.assertEqual(tally_mod.run_mode("diff-claude-text"), "differential")

    def test_mode_from_run_yaml_wins(self):
        directory = scratch(self)
        (directory / "run.yaml").write_text("mode: attack:hunt\n", encoding="utf-8")
        self.assertEqual(tally_mod.run_mode("anything", directory), "attack")


# ---------------------------------------------------------------------------
# the baseline regression


@unittest.skipUnless(BASELINE, "set CF_BASELINE_CORPUS to the tao-diff-run checkout")
class TestBaselineTally(unittest.TestCase):
    """Reproduce the published figures from the corpus, not from the prose."""

    @classmethod
    def setUpClass(cls):
        cls.result = tally_mod.tally(baseline_runs())

    def test_ours_rows_and_items(self):
        ours = self.result["repos"]["ours"]
        self.assertEqual(ours["rows"], 72)
        self.assertEqual(ours["raw"]["items"], 40)

    def test_ours_disposition_classes(self):
        classes = self.result["repos"]["ours"]["raw"]["classes"]
        self.assertEqual(classes["content_vac"], 16)
        self.assertEqual(classes["suspected_errata"], 1)
        self.assertEqual(classes["coverage"], 1)
        self.assertEqual(classes["page_anchor"], 22)
        self.assertEqual(sum(classes.values()), 40)

    def test_ours_kind_breakdown(self):
        self.assertEqual(self.result["repos"]["ours"]["by_kind"],
                         {"PAGE_ANCHOR": 41, "CONTENT": 24, "COVERAGE": 3,
                          "SUSPECTED_ERRATA": 2, "VACUITY": 2})

    def test_ours_collapsed(self):
        ours = self.result["repos"]["ours"]
        self.assertEqual(ours["collapsed"]["items"], 39)
        self.assertEqual(ours["collapsed"]["substantive"], 17)
        self.assertEqual(ours["collapsed"]["high"], 3)
        self.assertEqual(ours["raw"]["substantive"], 17)
        self.assertEqual(ours["raw"]["high"], 3)

    def test_ours_per_run_items(self):
        per_run = self.result["repos"]["ours"]["per_run"]
        self.assertEqual([per_run[name]["items"] for name in BASELINE_RUNS],
                         [21, 16, 15, 11, 6])

    def test_teorth(self):
        teorth = self.result["repos"]["teorth"]
        self.assertEqual(teorth["rows"], 147)
        self.assertEqual(teorth["raw"]["items"], 71)
        self.assertEqual(teorth["raw"]["substantive"], 62)
        self.assertEqual(teorth["raw"]["high"], 25)
        # 41/19, not the 40/18 of the runs before 2026-09-18.  ONE row moved:
        # findings-hunt's "In-text definition, Exercise 1.4.24" used to collapse
        # onto the numbered "Exercise 1.4.24" and is now the separate item
        # "In-text Exercise 1.4.24" -- a de-conflation, not a new finding.  The
        # published `ours` numbers (72 rows / 40 items / 16 / 1 / 1 / 22) are
        # unchanged, and every other repo's counts are unchanged.
        self.assertEqual(teorth["collapsed"]["substantive"], 41)
        self.assertEqual(teorth["collapsed"]["high"], 19)

    def test_adam(self):
        adam = self.result["repos"]["adam"]
        self.assertEqual(adam["rows"], 7)
        self.assertEqual(adam["raw"]["items"], 5)
        self.assertEqual(adam["raw"]["high"], 1)

    def test_unattributed_rows_are_not_charged_to_a_repo(self):
        """Findings naming no declaration must not be routed to anyone.

        The "31 findings" scare was exactly this: rows without an
        unfaithful_decls prefix attributed to a repo by a fallback branch.
        """
        rows = self.result["repos"][tally_mod.UNATTRIBUTED]
        self.assertEqual(rows["rows"], 126)
        self.assertNotIn("CONTENT", rows["by_kind"])

    def test_attack_and_differential_sets_partition_the_items(self):
        for repo in ("ours", "teorth", "adam"):
            sets = self.result["repos"][repo]["collapsed"]["sets"]
            union = set(sets["attack_only"]) | set(sets["differential_only"]) | set(sets["both"])
            self.assertEqual(len(union), self.result["repos"][repo]["collapsed"]["items"], repo)
            self.assertFalse(set(sets["attack_only"]) & set(sets["differential_only"]), repo)

    def test_state_counts_match_the_architecture_block(self):
        counts = tally_mod.state_counts(self.result["repos"]["ours"])
        self.assertEqual(counts["rows"], 72)
        self.assertEqual(counts["items"], 40)
        self.assertEqual(counts["content_vac"], 16)
        self.assertEqual(counts["page_anchor"], 22)


@unittest.skipUnless(BASELINE, "set CF_BASELINE_CORPUS to the tao-diff-run checkout")
class TestBaselineUnionAgreed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = tally_mod.collect(baseline_runs())

    def test_union_collapses_the_same_defect_across_runs(self):
        """The ad hoc union split one defect per phrasing; this must not."""
        entries = union_mod.union(self.rows, repo="adam")
        exercise = [e for e in entries if e["item"] == "Exercise 1.6.39" and e["kind"] == "CONTENT"]
        self.assertEqual(len(exercise), 1)
        self.assertGreaterEqual(len(exercise[0]["runs"]), 2)

    def test_union_new_only_against_a_baseline(self):
        all_runs = {r["run"] for r in self.rows}
        baseline = {"findings", "findings-pdf", "findings-gpt"}
        entries = union_mod.union(self.rows, repo="ours", baseline_runs=baseline, new_only=True)
        self.assertTrue(entries)
        for entry in entries:
            self.assertFalse(baseline & set(entry["runs"]))
            self.assertTrue(set(entry["runs"]) <= all_runs)

    def test_agreed_requires_two_runs(self):
        entries = agreed_mod.agreed(self.rows, min_runs=2, min_severity="high")
        self.assertTrue(entries)
        for entry in entries:
            self.assertGreaterEqual(len(entry["runs"]), 2)
        # teorth Exercise 1.1.11 was found high by both families
        match = [e for e in entries if e["repo"] == "teorth" and e["item"] == "Exercise 1.1.11"]
        self.assertEqual(len(match), 1)
        self.assertTrue(match[0]["cross_family"])

    def test_agreed_excludes_coverage_by_default(self):
        """agreed_high_findings.json listed Exercise 1.1.21 with repos: []."""
        entries = agreed_mod.agreed(self.rows, min_runs=2, min_severity="high")
        self.assertFalse([e for e in entries if e["item"] == "Exercise 1.1.21"])
        with_coverage = agreed_mod.agreed(self.rows, min_runs=2, min_severity="high",
                                          substantive_only=False)
        self.assertTrue([e for e in with_coverage if e["item"] == "Exercise 1.1.21"])


# ---------------------------------------------------------------------------
# harvest receipts


class TestReceipt(unittest.TestCase):
    """The receipt is the only reason to believe a corpus is the whole corpus."""

    def setUp(self):
        self.root = scratch(self)
        self.source = self.root / "server-findings"
        self.dest = self.root / "corpus" / "run-1"
        for i in range(1, 9):
            write_findings(self.source, f"Exercise 1.1.{i}", [])

    def test_local_harvest_is_complete(self):
        receipt = harvest_mod.harvest(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            dest=self.dest, sources={PUBLISHED_ID: PUBLISHED_SHA}, verify_sha=True)
        self.assertTrue(receipt["complete"])
        self.assertEqual(receipt["expected"]["count"], 8)
        self.assertEqual(receipt["actual"]["count"], 8)
        self.assertEqual(receipt["missing"], [])
        self.assertEqual(len(receipt["manifest"]), 8)
        # The RECEIPT records the sha of EVERY source the run read, so a later
        # reader can say which editions the corpus was judged against without
        # re-opening 2,672 files.
        self.assertEqual(receipt["sources"], {PUBLISHED_ID: PUBLISHED_SHA})
        self.assertNotIn("book_sha", receipt)
        self.assertTrue((self.dest / harvest_mod.RECEIPT_NAME).exists())

    def test_the_receipt_counts_the_files_of_each_source_separately(self):
        """A two-source harvest must say how many files came from each."""
        for i in (1, 2, 3):
            write_findings(self.source, f"Exercise 1.1.{i}", [], PUBLISHED_ID, PUBLISHED_SHA)
        for i in (1, 2):
            write_findings(self.source, f"Exercise 1.1.{i}", [], DRAFT_ID, DRAFT_SHA)
        receipt = harvest_mod.harvest(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            dest=self.dest, sources={PUBLISHED_ID: PUBLISHED_SHA, DRAFT_ID: DRAFT_SHA})
        self.assertTrue(receipt["complete"])
        self.assertEqual(receipt["by_source"], {DRAFT_ID: 2, PUBLISHED_ID: 3})
        # the eight unstamped v1 files are recorded, not counted against a source
        self.assertEqual(len(receipt["edition"]["no_source"]), 8)
        self.assertEqual(receipt["actual"]["count"], 13)

    def test_one_file_short_is_not_complete(self):
        """findings-hunt held 762 where the server had 763; the missing file was real."""
        harvest_mod.harvest(run_id="run-1", source={"kind": "local", "path": str(self.source)},
                            dest=self.dest)
        expected = sorted([p.name for p in self.source.iterdir()] + ["exercise-1-1-17.json"])
        receipt = harvest_mod.build_receipt(
            run_id="run-1", source={"kind": "remote", "host": "h", "path": "/p"},
            expected=expected, dest=self.dest)
        self.assertFalse(receipt["complete"])
        self.assertEqual(receipt["missing"], ["exercise-1-1-17.json"])
        self.assertEqual(receipt["expected"]["count"] - receipt["actual"]["count"], 1)
        self.assertIn("exercise-1-1-17.json", harvest_mod.describe(receipt))

    def test_corrupted_file_fails_the_sha_check(self):
        harvest_mod.harvest(run_id="run-1", source={"kind": "local", "path": str(self.source)},
                            dest=self.dest)
        victim = sorted(self.dest.glob("exercise-*.json"))[0]
        source_shas = harvest_mod.source_shas({"kind": "local", "path": str(self.source)})
        victim.write_text('{"item": "truncated"}', encoding="utf-8")
        receipt = harvest_mod.build_receipt(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            expected=list(source_shas), dest=self.dest, source_manifest=source_shas)
        self.assertFalse(receipt["complete"])
        self.assertEqual(receipt["sha_mismatched"], [victim.name])

    def test_receipt_is_not_counted_as_a_finding(self):
        harvest_mod.harvest(run_id="run-1", source={"kind": "local", "path": str(self.source)},
                            dest=self.dest)
        result = tally_mod.tally({"run-1": self.dest})
        self.assertEqual(result["runs"]["run-1"]["files"], 8)

    def test_reharvest_records_the_previous_receipt(self):
        harvest_mod.harvest(run_id="run-1", source={"kind": "local", "path": str(self.source)},
                            dest=self.dest)
        write_findings(self.source, "Exercise 1.1.9", [])
        receipt = harvest_mod.harvest(run_id="run-1",
                                      source={"kind": "local", "path": str(self.source)},
                                      dest=self.dest)
        self.assertTrue(receipt["complete"])
        self.assertEqual(receipt["previous"]["actual"], 8)
        self.assertEqual(receipt["previous"]["gained"], ["exercise-1-1-9.json"])

    def _mismatched_corpus(self) -> Path:
        """Harvest a corpus in which one file was judged against the draft."""
        write_findings(self.source, "Exercise 1.1.99", [], PUBLISHED_ID, PUBLISHED_SHA)
        harvest_mod.harvest(run_id="run-1",
                            source={"kind": "local", "path": str(self.source)},
                            dest=self.dest, sources={PUBLISHED_ID: PUBLISHED_SHA})
        victim = self.dest / f"{PUBLISHED_ID}--exercise-1-1-99.json"
        payload = json.loads(victim.read_text())
        payload["source_sha"] = DRAFT_SHA
        victim.write_text(json.dumps(payload), encoding="utf-8")
        return victim

    def test_source_audit_names_findings_from_another_edition(self):
        """Batch-1 findings were judged against the 2011 draft; anchors differ."""
        victim = self._mismatched_corpus()
        audit = harvest_mod.source_audit(self.dest, {PUBLISHED_ID: PUBLISHED_SHA})
        self.assertEqual(audit["mismatched"], [victim.name])
        self.assertEqual(len(audit["no_source"]), 8)
        self.assertFalse(harvest_mod.source_audit(self.dest, {})["checked"])

    def test_a_file_naming_a_source_this_run_does_not_carry_is_its_own_class(self):
        """Wrong edition and wrong RUN need different fixes, so they are separate.

        A file stamped with a source_id the run never read was judged somewhere
        else entirely; re-pinning the edition would not make it belong here.
        """
        write_findings(self.source, "Exercise 2.1.1", [], "some-other-book", "c" * 64)
        harvest_mod.harvest(run_id="run-1",
                            source={"kind": "local", "path": str(self.source)},
                            dest=self.dest, sources={PUBLISHED_ID: PUBLISHED_SHA},
                            allow_edition_mismatch=True)
        audit = harvest_mod.source_audit(self.dest, {PUBLISHED_ID: PUBLISHED_SHA})
        self.assertEqual(audit["unknown_source"], ["some-other-book--exercise-2-1-1.json"])
        self.assertEqual(audit["mismatched"], [])

    def test_an_edition_mismatch_fails_the_receipt(self):
        """SKILL.md hard rule 3: a mismatch is refused, not warned about."""
        self._mismatched_corpus()
        expected = [p.name for p in self.source.iterdir()]

        receipt = harvest_mod.build_receipt(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            expected=expected, dest=self.dest, sources={PUBLISHED_ID: PUBLISHED_SHA})
        self.assertFalse(receipt["complete"])
        self.assertFalse(receipt["edition"]["ok"])
        self.assertIn("REFUSED", harvest_mod.describe(receipt))

        allowed = harvest_mod.build_receipt(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            expected=expected, dest=self.dest, sources={PUBLISHED_ID: PUBLISHED_SHA},
            allow_edition_mismatch=True)
        self.assertTrue(allowed["complete"])
        self.assertTrue(allowed["edition"]["ok"])
        self.assertTrue(allowed["edition"]["allowed_mismatch"])

    def test_the_other_edition_passes_when_the_run_carries_both(self):
        """The same file is a mismatch or not depending on what the run read."""
        self._mismatched_corpus()
        receipt = harvest_mod.build_receipt(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            expected=[p.name for p in self.source.iterdir()], dest=self.dest,
            sources={PUBLISHED_ID: PUBLISHED_SHA, DRAFT_ID: DRAFT_SHA})
        # It still names the published source, so its own sha decides it: the
        # sha does not belong to the id it claims.
        self.assertEqual(receipt["edition"]["mismatched"],
                         [f"{PUBLISHED_ID}--exercise-1-1-99.json"])

    def test_a_missing_source_sha_is_recorded_but_not_fatal(self):
        """v1 rows carry none until convert_v1 stamps them."""
        receipt = harvest_mod.harvest(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            dest=self.dest, sources={PUBLISHED_ID: PUBLISHED_SHA})
        self.assertTrue(receipt["complete"])
        self.assertEqual(len(receipt["edition"]["no_source"]), 8)

    def test_a_failing_gate_fails_the_receipt(self):
        gate_result = {"ok": False, "preset": "p", "problems": ["x.json: missing item"]}
        receipt = harvest_mod.harvest(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            dest=self.dest, gate_runner=lambda _dest: gate_result)
        self.assertFalse(receipt["complete"])
        self.assertEqual(receipt["gate"], gate_result)
        self.assertIn("gate: FAILED", harvest_mod.describe(receipt))

    def test_gate_corpus_validates_what_landed(self):
        """`cf harvest --gate` is the only path on which the gate actually runs."""
        from harvest import gate_corpus

        book = self.root / "text.txt"
        book.write_text("Show that the union of two elementary sets is elementary "
                        "and that the intersection is elementary as well.\n", encoding="utf-8")
        draft = self.root / "draft.txt"
        draft.write_text("The 2011 draft says something else entirely here.\n",
                         encoding="utf-8")
        cfg = self.root / "gate.local.json"
        cfg.write_text(json.dumps({
            "schema_version": 2, "preset": "differential-text", "mode": "differential",
            "run_id": "run-1",
            "sources": {PUBLISHED_ID: {"text": str(book), "sha256": PUBLISHED_SHA,
                                       "page_offset": 16},
                        DRAFT_ID: {"text": str(draft), "sha256": DRAFT_SHA,
                                   "page_offset": 16}},
            "repos": ["ours"], "kinds": ["CONTENT", "COVERAGE"],
            "verdicts": ["AGREE", "CONTENT_DELTA"], "witness_required": False,
            "require_book_located": True, "require_source_sha": True,
            "findings_dir": ".", "kind_rules": {},
        }), encoding="utf-8")

        def doc(**over):
            payload = {
                "schema_version": 2, "run_id": "run-1", "source_id": PUBLISHED_ID,
                "source_sha": PUBLISHED_SHA, "item": "Exercise 1.2.4",
                "book_located": True,
                "book_passage_quote":
                    "Show that the union of two elementary sets is elementary",
                "matches": [], "disagreements": [], "coverage_notes": "",
            }
            payload.update(over)
            return json.dumps(payload)

        good = self.root / "corpus" / "gated"
        good.mkdir(parents=True)
        (good / f"{PUBLISHED_ID}--exercise-1-2-4.json").write_text(doc(), encoding="utf-8")
        result = gate_corpus.gate_corpus(good, cfg)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["files"], 1)

        # ... and the receipt it writes beside the findings is not gated as one
        (good / harvest_mod.RECEIPT_NAME).write_text(json.dumps({"run_id": "run-1"}),
                                                     encoding="utf-8")
        self.assertTrue(gate_corpus.gate_corpus(good, cfg)["ok"])

        # The same quote, moved to the draft: both texts are loaded, and it is
        # checked against the one its own document names.
        (good / f"{DRAFT_ID}--exercise-1-2-4.json").write_text(
            doc(source_id=DRAFT_ID, source_sha=DRAFT_SHA), encoding="utf-8")
        moved = gate_corpus.gate_corpus(good, cfg)
        self.assertFalse(moved["ok"])
        self.assertTrue(any("NOT FOUND" in p and DRAFT_ID in p for p in moved["problems"]),
                        moved["problems"])
        (good / f"{DRAFT_ID}--exercise-1-2-4.json").unlink()

        (good / "fabricated.json").write_text(doc(
            item="Exercise 1.2.5",
            book_passage_quote="Every bounded function on the unit interval is Riemann "
                               "integrable whenever the measure is complete."),
            encoding="utf-8")
        bad = gate_corpus.gate_corpus(good, cfg)
        self.assertFalse(bad["ok"])
        self.assertTrue(any("NOT FOUND" in p for p in bad["problems"]))

    def test_remote_source_refuses_without_execute(self):
        source = harvest_mod.parse_source("ffmain:/home/jack/tao-attack-hunt/findings")
        self.assertEqual(source["kind"], "remote")
        with self.assertRaises(Exception):
            harvest_mod.harvest(run_id="run-1", source=source, dest=self.dest, execute=False)
        commands = harvest_mod.list_commands(source, self.dest)
        self.assertTrue(any("ls -1" in c["shell"] for c in commands))
        self.assertTrue(any(c["shell"].startswith("rsync") for c in commands))

    def test_local_path_with_a_colon_is_not_a_host(self):
        odd = self.root / "weird:name"
        odd.mkdir()
        self.assertEqual(harvest_mod.parse_source(str(odd))["kind"], "local")


# ---------------------------------------------------------------------------
# journal recovery


class TestHarvestJournal(unittest.TestCase):
    def setUp(self):
        self.root = scratch(self)
        self.wf = self.root / "wf_abc"
        self.wf.mkdir()
        (self.wf / "agent-a1.meta.json").write_text(json.dumps({"label": "adj:Exercise 1.2.4"}))
        (self.wf / "agent-c1.meta.json").write_text(json.dumps({"label": "chal0:Exercise_1.2.4"}))
        rows = [
            {"type": "result", "agentId": "a1", "value": {
                "item": "Exercise 1.2.4", "book_located": True, "matches": [],
                "disagreements": [{"concept": "c", "kind": "CONTENT", "severity": "high",
                                   "unfaithful_decls": ["ours:foo"]}],
                "coverage_notes": ""}},
            {"type": "result", "agentId": "c1",
             "result": json.dumps({"ruling_survives": False, "evidence": "e",
                                   "corrected_ruling": "CONVENTION"})},
            {"type": "log", "agentId": "a1", "value": {"noise": True}},
            {"not": "json"},
        ]
        (self.wf / "journal.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows[:-1]) + "\nnot json at all\n")
        (self.root / "queue.json").write_text(json.dumps(
            [{"item": "Exercise 1.2.4"}, {"item": "Exercise 1.3.1"}]))

    def test_recovers_items_and_links_challenges(self):
        items, challenges = harvest_journal.read_journal(self.wf)
        self.assertEqual(list(items), ["Exercise 1.2.4"])
        linked = harvest_journal.link_challenges(items, challenges)
        self.assertEqual(len(linked["Exercise 1.2.4"]), 1)
        self.assertEqual(linked["Exercise 1.2.4"][0]["link_method"], "label-item")
        summary = harvest_journal.summarise(items, linked)
        self.assertEqual(summary["items_refuted_by_any"], 1)
        self.assertEqual(summary["defects"], 1)

    def test_recovered_corpus_is_readable_by_tally(self):
        items, challenges = harvest_journal.read_journal(self.wf)
        dest = self.root / "corpus" / "local-recovered"
        harvest_journal.write_corpus(items, harvest_journal.link_challenges(items, challenges),
                                     dest, "local-recovered")
        self.assertTrue((dest / "exercise-1-2-4.json").exists())
        result = tally_mod.tally({"local-recovered": dest})
        self.assertEqual(result["repos"]["ours"]["rows"], 1)

    def test_remaining_queue(self):
        items, _ = harvest_journal.read_journal(self.wf)
        todo = harvest_journal.remaining(self.root / "queue.json", items)
        self.assertEqual([t["item"] for t in todo], ["Exercise 1.3.1"])


# ---------------------------------------------------------------------------
# union / agreed on a synthetic corpus (no baseline needed)


class TestUnionSynthetic(unittest.TestCase):
    def setUp(self):
        self.root = scratch(self)
        defect = lambda concept: [{"concept": concept, "kind": "CONTENT", "severity": "high",
                                   "repos_involved": ["ours"], "faithful": "teorth",
                                   "unfaithful_decls": ["ours:Foo.bar"], "book_quote": "q",
                                   "reasoning": "r"}]
        # the same defect, described differently, in two runs
        write_findings(self.root / "run-a", "Exercise 1.1.1", defect("hypothesis d >= 1 dropped"))
        write_findings(self.root / "run-b", "Exercise 1.1.1(ii)", defect("the d >= 1 hypothesis is missing"))
        write_findings(self.root / "run-b", "Exercise 1.2.2", defect("wrong endpoint"))
        self.rows = tally_mod.collect({"run-a": self.root / "run-a", "run-b": self.root / "run-b"})

    def test_one_entry_per_defect_not_per_phrasing(self):
        entries = union_mod.union(self.rows, repo="ours")
        by_item = {e["item"]: e for e in entries}
        self.assertEqual(sorted(by_item), ["Exercise 1.1.1", "Exercise 1.2.2"])
        self.assertEqual(by_item["Exercise 1.1.1"]["runs"], ["run-a", "run-b"])
        self.assertEqual(by_item["Exercise 1.1.1"]["items_raw"],
                         ["Exercise 1.1.1", "Exercise 1.1.1(ii)"])
        self.assertEqual(len(by_item["Exercise 1.1.1"]["concepts"]), 2)

    def test_new_only(self):
        entries = union_mod.union(self.rows, repo="ours", baseline_runs=["run-a"], new_only=True)
        self.assertEqual([e["item"] for e in entries], ["Exercise 1.2.2"])

    def test_agreed_counts_book_items_not_labels(self):
        entries = agreed_mod.agreed(self.rows, min_runs=2)
        self.assertEqual([e["item"] for e in entries], ["Exercise 1.1.1"])
        self.assertEqual(union_mod.summary(union_mod.union(self.rows, repo="ours"))["multi_run"], 1)


class TestInTextItemsJoinAcrossCorpusGenerations(unittest.TestCase):
    """`union --new-only` and `agreed` must join the two spellings of one item.

    The v1 corpus writes ``"In-text, book p. 71"``; the extraction writes
    ``"In-text p.71"``.  Before the fix these were two keys, so a run that
    re-found a known in-text defect was reported as finding something new and
    agreeing with nobody.
    """

    def setUp(self):
        self.root = scratch(self)
        decl = "ours:RiemannIntegrableOn.complexLebesgueIntegralOn_Icc"
        defect = lambda concept: [{"concept": concept, "kind": "CONTENT", "severity": "high",
                                   "repos_involved": ["ours"], "faithful": "teorth",
                                   "unfaithful_decls": [decl], "book_quote": "q",
                                   "reasoning": "r"}]
        # v1 spelling (the baseline) and today's spelling (the new run)
        write_findings(self.root / "v1", "In-text definition, book p. 71",
                       defect("Riemann integrability is stated for the wrong class"))
        write_findings(self.root / "v2", "In-text p.71",
                       defect("the p.71 definition admits non-integrable functions"))
        write_findings(self.root / "v2", "In-text p.241", defect("unrelated, new"))
        self.rows = tally_mod.collect({"v1": self.root / "v1", "v2": self.root / "v2"})

    def test_the_two_spellings_are_one_key(self):
        self.assertEqual({r["item"] for r in self.rows}, {"In-text p.71", "In-text p.241"})

    def test_union_makes_one_entry_found_by_both_runs(self):
        entries = {e["item"]: e for e in union_mod.union(self.rows, repo="ours")}
        self.assertEqual(sorted(entries), ["In-text p.241", "In-text p.71"])
        self.assertEqual(entries["In-text p.71"]["runs"], ["v1", "v2"])
        self.assertEqual(entries["In-text p.71"]["items_raw"],
                         ["In-text definition, book p. 71", "In-text p.71"])

    def test_new_only_does_not_call_a_re_found_defect_new(self):
        entries = union_mod.union(self.rows, repo="ours", baseline_runs=["v1"], new_only=True)
        self.assertEqual([e["item"] for e in entries], ["In-text p.241"])

    def test_the_two_runs_agree(self):
        entries = agreed_mod.agreed(self.rows, min_runs=2)
        self.assertEqual([e["item"] for e in entries], ["In-text p.71"])

    def test_an_in_text_item_does_not_swallow_the_numbered_item(self):
        """"In-text Exercise 1.4.24" and "Exercise 1.4.24" are two items."""
        decl = ["teorth:Measure.measurable_of_lim"]
        defect = [{"concept": "c", "kind": "CONTENT", "severity": "high",
                   "repos_involved": ["teorth"], "faithful": "ours",
                   "unfaithful_decls": decl, "book_quote": "q", "reasoning": "r"}]
        write_findings(self.root / "v3", "Exercise 1.4.24", defect)
        write_findings(self.root / "v3", "In-text definition, Exercise 1.4.24", defect)
        rows = tally_mod.collect({"v3": self.root / "v3"})
        self.assertEqual(sorted({r["item"] for r in rows}),
                         ["Exercise 1.4.24", "In-text Exercise 1.4.24"])

    def test_an_unjoinable_in_text_label_is_reported(self):
        write_findings(self.root / "v4", "In-text, somewhere",
                       [{"concept": "c", "kind": "CONTENT", "severity": "low",
                         "repos_involved": ["ours"], "faithful": "teorth",
                         "unfaithful_decls": ["ours:Foo.bar"], "book_quote": "q",
                         "reasoning": "r"}])
        result = tally_mod.tally({"v4": self.root / "v4"})
        self.assertEqual([u["item_raw"] for u in result["unjoinable"]], ["In-text, somewhere"])
        self.assertEqual(result["unjoinable"][0]["reason"], "in_text_no_target")
        # reported, NOT dropped: the row still tallies, under itself
        self.assertEqual(result["rows"], 1)
        self.assertIn("In-text, somewhere", tally_mod.render(result))

    def test_a_clean_corpus_reports_nothing(self):
        self.assertEqual(tally_mod.tally({"v2": self.root / "v2"})["unjoinable"], [])

    def test_union_and_agreed_carry_the_report(self):
        runs = {"v1": self.root / "v1", "v2": self.root / "v2"}
        self.assertEqual(union_mod.build(runs, repo="ours")["unjoinable"], [])
        self.assertEqual(agreed_mod.build(runs)["unjoinable"], [])
        write_findings(self.root / "v1", "In-text, somewhere",
                       [{"concept": "c", "kind": "CONTENT", "severity": "high",
                         "repos_involved": ["ours"], "faithful": "teorth",
                         "unfaithful_decls": ["ours:Foo.bar"], "book_quote": "q",
                         "reasoning": "r"}])
        self.assertEqual([u["item_raw"] for u in union_mod.build(runs, repo="ours")["unjoinable"]],
                         ["In-text, somewhere"])
        self.assertEqual([u["item_raw"] for u in agreed_mod.build(runs)["unjoinable"]],
                         ["In-text, somewhere"])


@unittest.skipUnless(BASELINE, "set CF_BASELINE_CORPUS to the tao-diff-run checkout")
class TestRealCorpusGenerationsJoin(unittest.TestCase):
    """The live regression: the real v1 corpus against the real new extraction.

    ``tests/data/in_text_labels.tml-prefix.json`` pins the in-text item labels of
    the 2026-09-18 ground-truth extraction (105 of them); the v1 labels are read
    out of ``$CF_BASELINE_CORPUS`` itself (140 of them).  Exact-match overlap is
    zero, which is the bug; under the canonical key 139 of the 140 join.
    """

    NEW_LABELS = Path(__file__).resolve().parent / "data" / "in_text_labels.tml-prefix.json"
    #: the only v1 in-text item the extraction has no counterpart for -- p.156 is
    #: an errata note, and the companion formalises nothing anchored to that page
    #: (the extraction has p.155 and p.157).  It canonicalises fine; there is
    #: simply no such item on the other side.
    KNOWN_UNMATCHED = {"In-text record (book p. 156; author's errata)"}

    @classmethod
    def setUpClass(cls):
        cls.old = set()
        for path in sorted(Path(BASELINE).glob("findings*/*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            item = payload.get("item")
            if isinstance(item, str) and cf_items.is_in_text(item):
                cls.old.add(item)
        cls.new = set(json.loads(cls.NEW_LABELS.read_text(encoding="utf-8"))["labels"])

    def test_the_two_label_sets_are_the_sizes_the_bug_report_names(self):
        self.assertEqual(len(self.old), 140)
        self.assertEqual(len(self.new), 105)

    def test_exact_match_overlap_is_zero(self):
        """The bug, asserted on the real data."""
        self.assertEqual({cf_items.clean(x) for x in self.old}
                         & {cf_items.clean(x) for x in self.new}, set())

    def test_every_real_in_text_label_canonicalises(self):
        for labels in (self.old, self.new):
            _keys, rejects = cf_items.join_keys(sorted(labels))
            self.assertEqual([r.raw for r in rejects], [])

    def test_the_canonical_key_joins_them(self):
        old_keys, _ = cf_items.join_keys(sorted(self.old))
        new_keys = set(cf_items.join_keys(sorted(self.new))[0].values())
        joined = {raw for raw, key in old_keys.items() if key in new_keys}
        self.assertGreater(len(joined), 100, "the join must be non-trivial")
        self.assertEqual(len(joined), 139)
        self.assertEqual(set(self.old) - joined, self.KNOWN_UNMATCHED)
        # and every one of today's labels is reachable from the v1 vocabulary
        self.assertEqual(new_keys - set(old_keys.values()), set())

    def test_the_p71_defect_joins(self):
        """The live proof: the item the ground-truth run re-found and lost."""
        self.assertIn("In-text definition, book p. 71", self.old)
        self.assertIn("In-text p.71", self.new)
        self.assertEqual(cf_items.join_key("In-text definition, book p. 71"), "In-text p.71")
        self.assertEqual(tally_mod.collapse_to_book_item("In-text definition, book p. 71"),
                         tally_mod.collapse_to_book_item("In-text p.71"))

    def test_the_whole_baseline_corpus_has_nothing_unjoinable(self):
        rows = tally_mod.collect(baseline_runs())
        self.assertEqual(tally_mod.unjoinable(rows), [])


class TestTwoSourcesDoNotMerge(unittest.TestCase):
    """The same item label in two sources is two items, everywhere downstream.

    ``Exercise 1.1.1`` of the published edition and ``Exercise 1.1.1`` of the
    2011 draft are different items (S6.1).  A stage that keyed on the bare label
    would report one defect where there are two, and -- worse -- would report
    two runs "agreeing" when each faulted a different edition.
    """

    DEFECT = [{"concept": "hypothesis d >= 1 dropped", "kind": "CONTENT",
               "severity": "high", "repos_involved": ["ours"], "faithful": [],
               "unfaithful_decls": ["ours:Foo.bar"], "book_quote": "q", "reasoning": "r"}]

    def setUp(self):
        self.root = scratch(self)
        for run, sid, sha in (("run-a", PUBLISHED_ID, PUBLISHED_SHA),
                              ("run-b", DRAFT_ID, DRAFT_SHA)):
            write_findings(self.root / run, "Exercise 1.1.1", self.DEFECT, sid, sha)
        self.runs = {"run-a": self.root / "run-a", "run-b": self.root / "run-b"}
        self.rows = tally_mod.collect(self.runs)

    def test_every_row_says_which_source_it_is_about(self):
        self.assertEqual(sorted(r["source_id"] for r in self.rows),
                         [DRAFT_ID, PUBLISHED_ID])
        self.assertEqual({r["source_id"]: r["source_sha"] for r in self.rows},
                         {PUBLISHED_ID: PUBLISHED_SHA, DRAFT_ID: DRAFT_SHA})

    def test_tally_groups_by_source_and_repo(self):
        result = tally_mod.tally(self.runs)
        self.assertEqual(sorted(result["groups"]),
                         sorted([tally_mod.group_key({"source_id": PUBLISHED_ID, "repo": "ours"}),
                                 tally_mod.group_key({"source_id": DRAFT_ID, "repo": "ours"})]))
        for block in result["groups"].values():
            self.assertEqual(block["rows"], 1)
        # the per-repo rollup is still there, and says which sources it spans
        self.assertEqual(result["repos"]["ours"]["rows"], 2)
        self.assertEqual(sorted(result["repos"]["ours"]["sources"]),
                         sorted([PUBLISHED_ID, DRAFT_ID]))
        self.assertEqual(result["sources"],
                         {DRAFT_ID: DRAFT_SHA, PUBLISHED_ID: PUBLISHED_SHA})

    def test_union_keys_on_the_source_so_one_defect_stays_two(self):
        entries = union_mod.union(self.rows, repo="ours")
        self.assertEqual(len(entries), 2)
        self.assertEqual(sorted(e["source_id"] for e in entries),
                         sorted([PUBLISHED_ID, DRAFT_ID]))
        for entry in entries:
            self.assertEqual(entry["item"], "Exercise 1.1.1")
            self.assertEqual(len(entry["runs"]), 1)     # never "found by both runs"
        one = union_mod.union(self.rows, repo="ours", source_id=PUBLISHED_ID)
        self.assertEqual([e["source_id"] for e in one], [PUBLISHED_ID])
        summary = union_mod.summary(entries)
        self.assertEqual(summary["multi_run"], 0)
        self.assertEqual(summary["items"], 2)
        self.assertEqual(summary["by_source"], {PUBLISHED_ID: 1, DRAFT_ID: 1})

    def test_agreement_is_never_computed_across_two_sources(self):
        """Two runs faulting one label in two editions agree about nothing."""
        self.assertEqual(agreed_mod.agreed(self.rows, min_runs=2), [])
        # ... and the same two runs over ONE source do agree
        both = scratch(self)
        for run in ("run-a", "run-b"):
            write_findings(both / run, "Exercise 1.1.1", self.DEFECT,
                           PUBLISHED_ID, PUBLISHED_SHA)
        rows = tally_mod.collect({"run-a": both / "run-a", "run-b": both / "run-b"})
        entries = agreed_mod.agreed(rows, min_runs=2)
        self.assertEqual([(e["source_id"], e["item"]) for e in entries],
                         [(PUBLISHED_ID, "Exercise 1.1.1")])

    def test_a_tally_can_be_restricted_to_one_source(self):
        """One corpus directory can hold two sources' files."""
        rows = tally_mod.collect(self.runs, sources=[DRAFT_ID])
        self.assertEqual([r["source_id"] for r in rows], [DRAFT_ID])
        self.assertEqual(tally_mod.tally(self.runs, sources=[DRAFT_ID])["rows"], 1)


# ---------------------------------------------------------------------------
# the run planner: allowlist, budget, disk, out-of-tree gate


class TestRunPlanner(unittest.TestCase):
    """cf run's refusals, exercised without touching a network."""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        from cflib.workspace import Workspace

        self.root = scratch(self)
        self.ours = self.root / "repos" / "ours"
        self.ours.mkdir(parents=True)
        (self.root / "workspace.yaml").write_text(
            "version: 2\n"
            "workspace:\n  name: tao\n"
            "companions:\n"
            "  ours:\n"
            f"    repo: {self.ours}\n"
            "    adapter: docstring_tags\n"
            f"    sources: [{PUBLISHED_ID}]\n"
            f"    anchor_source: {PUBLISHED_ID}\n"
            "sources:\n"
            f"  {PUBLISHED_ID}:\n"
            f'    sha256: "{PUBLISHED_SHA}"\n'
            "    title: An Introduction to Measure Theory\n"
            '    edition: "AMS GSM 126 (published pagination)"\n'
            "    offset: 16\n"
            "    role: audited\n"
            f"  {DRAFT_ID}:\n"
            f'    sha256: "{DRAFT_SHA}"\n'
            "    title: An Introduction to Measure Theory\n"
            '    edition: "2011-01-24 draft"\n'
            "    offset: 16\n"
            "    role: reference\n"
            "hosts:\n  allowlist: [testbox]\n  remote_root: /home/tester\n"
            "  testbox:\n    ssh: tester@testbox\n"
            "budget:\n  max_cost_usd: 25\n", encoding="utf-8")
        self.workspace = Workspace.load(self.root)
        # Both editions are PREPARED on this machine; the run reads one.
        for sid in (PUBLISHED_ID, DRAFT_ID):
            sdir = self.root / "sources" / sid
            (sdir / "images").mkdir(parents=True)
            (sdir / "text.txt").write_text(f"text of {sid}\n", encoding="utf-8")
            (sdir / "errata.md").write_text("# errata\n", encoding="utf-8")
            (sdir / "images" / "p-080.png").write_bytes(b"\x89PNG")
        self.run_dir = self.root / "runs" / "demo"
        (self.run_dir / "tasks").mkdir(parents=True)
        for i in (1, 2, 3):
            name = f"{PUBLISHED_ID}--exercise-1-1-{i}"
            (self.run_dir / "tasks" / f"{name}.yaml").write_text(
                f"schema_version: 1\nid: {name}\ntitle: \"hunt: Exercise 1.1.{i}\"\n"
                f"description: |\n  probe\n", encoding="utf-8")
        self.run = {"run_id": "demo", "mode": "attack:hunt", "companions": ["ours"],
                    "sources": [{"id": PUBLISHED_ID, "sha256": PUBLISHED_SHA,
                                 "offset": 16}],
                    "source_access": "text",
                    "remote": {"entrypoint": "hunt", "tend_root": "hunt-tend"}}

    def test_host_must_be_allowlisted(self):
        from run import manifest

        self.assertEqual(manifest.resolve_host(self.workspace, "testbox")["ssh"], "tester@testbox")
        with self.assertRaises(Exception):
            manifest.resolve_host(self.workspace, "ffmain")

    def test_sources_and_book_are_excluded_by_default(self):
        from run import manifest

        host = manifest.resolve_host(self.workspace, "testbox")
        entries, _ = manifest.build_manifest(
            workspace_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_texts=False,
            companions=manifest.companion_repos(self.workspace))
        names = [e["name"] for e in entries]
        self.assertNotIn("repo:ours", names)
        self.assertFalse([n for n in names if n.startswith("source-")])
        self.assertIn("gate", names)

    def test_sources_are_transferred_read_only(self):
        from run import manifest

        host = manifest.resolve_host(self.workspace, "testbox")
        entries, _ = manifest.build_manifest(
            workspace_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=True, include_texts=False,
            companions={"ours": str(self.ours)})
        repo = [e for e in entries if e["name"] == "repo:ours"][0]
        self.assertIn("--chmod=D0555,F0444", repo["rsync_flags"])
        # Lean under repos/, source text under sources/: a companion named after
        # a source cannot land on top of the text the gate checks quotes against.
        self.assertTrue(repo["dest"].endswith("/repos/ours/"), repo["dest"])

    def test_only_the_sources_this_run_reads_leave_the_machine(self):
        """Both editions are prepared here; the run reads one, so one travels."""
        from run import manifest

        host = manifest.resolve_host(self.workspace, "testbox")
        entries, warnings = manifest.build_manifest(
            workspace_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_texts=True, companions={})
        names = [e["name"] for e in entries]
        self.assertIn(f"source-text:{PUBLISHED_ID}", names)
        self.assertIn(f"source-errata:{PUBLISHED_ID}", names)
        self.assertFalse([n for n in names if DRAFT_ID in n],
                         "the draft is on this machine but not in this run")
        text = [e for e in entries if e["name"] == f"source-text:{PUBLISHED_ID}"][0]
        self.assertEqual(text["src"], str(self.root / "sources" / PUBLISHED_ID / "text.txt"))
        self.assertTrue(text["dest"].endswith(f"/sources/{PUBLISHED_ID}/text.txt"))
        # a text-only run does not ship the page images
        self.assertNotIn(f"source-images:{PUBLISHED_ID}", names)
        self.assertEqual([w for w in warnings if "image" in w], [])

    def test_a_two_source_run_transfers_both_texts_and_only_those(self):
        from run import manifest

        host = manifest.resolve_host(self.workspace, "testbox")
        run = dict(self.run, sources=[
            {"id": PUBLISHED_ID, "sha256": PUBLISHED_SHA},
            {"id": DRAFT_ID, "sha256": DRAFT_SHA}], source_access="text+images")
        entries, _ = manifest.build_manifest(
            workspace_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=run,
            host=host, include_sources=False, include_texts=True, companions={})
        names = [e["name"] for e in entries]
        for sid in (PUBLISHED_ID, DRAFT_ID):
            self.assertIn(f"source-text:{sid}", names)
            self.assertIn(f"source-images:{sid}", names)   # the hunt run reads notation
        self.assertEqual(manifest.source_shas(self.workspace, run),
                         {PUBLISHED_ID: PUBLISHED_SHA, DRAFT_ID: DRAFT_SHA})

    def test_gate_is_transferred_out_of_tree(self):
        from run import manifest

        host = manifest.resolve_host(self.workspace, "testbox")
        entries, _ = manifest.build_manifest(
            workspace_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_texts=False, companions={})
        gate = [e for e in entries if e["name"] == "gate"][0]
        entrypoint = f"{host['remote_root']}/hunt/"
        self.assertFalse(gate["dest"].startswith(entrypoint))

    def test_in_tree_gate_config_is_reported(self):
        from run import manifest

        host = manifest.resolve_host(self.workspace, "testbox")
        tend_root = self.run_dir / "tend-root"
        tend_root.mkdir(parents=True)
        (tend_root / "config.yaml").write_text(
            "validation_commands:\n  - argv: [python3, scripts/verify_findings.py]\n",
            encoding="utf-8")
        warnings = manifest.gate_config_warnings(self.root, "demo", host, self.run)
        self.assertTrue(any("out-of-tree" in w for w in warnings))
        self.assertTrue(any("--changed-files" in w for w in warnings))

    def test_planned_host_mismatch_is_refused(self):
        """config.yaml and gate.json embed one host's absolute paths."""
        from run import manifest

        manifest.check_planned_host({"host": {"name": "testbox"}}, "testbox")
        with self.assertRaises(Exception):
            manifest.check_planned_host({"host": {"name": "local"}}, "testbox")
        with self.assertRaises(Exception):
            manifest.check_planned_host({"host": {"name": "ffmain"}}, "testbox")

    def test_staged_gate_beats_the_skill_copy(self):
        """`cf plan-run` stages the gate in tend-root/gate/ with the run's gate.json."""
        from run import manifest

        host = manifest.resolve_host(self.workspace, "testbox")
        gate_dir = self.run_dir / "tend-root" / "gate"
        gate_dir.mkdir(parents=True)
        (gate_dir / "verify_findings.py").write_text("# gate\n", encoding="utf-8")
        (gate_dir / "gate.json").write_text("{}", encoding="utf-8")
        entries, _ = manifest.build_manifest(
            workspace_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_texts=False, companions={})
        gate = [e for e in entries if e["name"] == "gate"][0]
        self.assertEqual(gate["src"], str(gate_dir))
        self.assertEqual(gate["dest"], f"{host['remote_root']}/demo-gate/")
        self.assertNotIn("gate-config", [e["name"] for e in entries])
        tend = [e for e in entries if e["name"] == "tend-root"][0]
        self.assertIn("--exclude=gate/", tend["rsync_flags"])

    def test_budget_cap_is_required(self):
        from run import manifest
        from cflib.workspace import Workspace

        budget = manifest.check_budget_cap(self.workspace, self.run, None, False)
        self.assertEqual(budget["max_cost"], 25.0)
        bare_root = scratch(self)
        (bare_root / "workspace.yaml").write_text(
            "version: 2\nworkspace:\n  name: bare\ncompanions: {}\nsources: {}\n"
            "hosts:\n  allowlist: [testbox]\n", encoding="utf-8")
        bare = Workspace.load(bare_root)
        with self.assertRaises(Exception):
            manifest.check_budget_cap(bare, self.run, None, False)
        self.assertTrue(manifest.check_budget_cap(bare, self.run, None, True)["acknowledged_uncapped"])

    def test_disk_budget_flags_a_worktree_blowout(self):
        from run import manifest

        host = manifest.resolve_host(self.workspace, "testbox")
        host["disk_budget_bytes"] = 1000
        entries, _ = manifest.build_manifest(
            workspace_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_texts=False, companions={})
        disk = manifest.disk_budget(entries, 763, host)
        self.assertTrue(disk["over_budget"])
        self.assertIn("--copy-dir --cow", disk["advice"])

    def test_commands_smoke_before_raise(self):
        from run import manifest, remote

        host = manifest.resolve_host(self.workspace, "testbox")
        entries, _ = manifest.build_manifest(
            workspace_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_texts=False, companions={})
        steps = remote.plan_commands(
            host=host, run_id="demo", run=self.run, entries=entries,
            names=manifest.remote_names(self.run, "demo"),
            budget={"max_cost": 25.0, "currency": "USD"},
            concurrency={"workers": 5, "reviewers": 3})
        stages = [s["stage"] for s in steps]
        self.assertLess(stages.index("smoke"), stages.index("raise"))
        smoke = [s for s in steps if s["stage"] == "smoke"]
        self.assertIn("--workers 1 --reviewers 1", smoke[0]["shell"])
        raise_steps = [s for s in steps if s["stage"] == "raise"]
        self.assertIn("budget", raise_steps[0]["shell"])          # ceiling before concurrency
        self.assertIn("--workers 5 --reviewers 3", raise_steps[-1]["shell"])

    def test_the_transfer_table_measures_what_rsync_will_send(self):
        """It walked the whole tree while rsync excludes .git/.lake/build/*.olean."""
        from run import manifest

        repo = self.root / "companion"
        (repo / "Src").mkdir(parents=True)
        (repo / "Src" / "A.lean").write_text("x" * 1000, encoding="utf-8")
        for skip in (".git", ".lake", "build", "__pycache__"):
            d = repo / skip / "deep"
            d.mkdir(parents=True)
            (d / "junk.bin").write_text("y" * 100000, encoding="utf-8")
        (repo / "Src" / "A.olean").write_text("z" * 50000, encoding="utf-8")

        flags = [f"--exclude={p}" for p in manifest.SOURCE_EXCLUDES]
        entry = manifest._entry("source:x", repo, "/dest/", flags=flags)
        self.assertEqual(entry["files"], 1, "only Src/A.lean is actually sent")
        self.assertEqual(entry["bytes"], 1000)

        unfiltered = manifest._entry("source:x", repo, "/dest/")
        self.assertGreater(unfiltered["files"], entry["files"])

    def test_the_session_cap_is_enforced_not_documented(self):
        """traps.md C3: past 16 concurrent sessions the API returns 409s."""
        from cfcmd import run as run_cmd
        from cflib.errors import CfError

        class A:
            workers = 24
            reviewers = 12
            over_session_cap = False

        with self.assertRaises(CfError) as ctx:
            run_cmd._concurrency(self.workspace, {}, A())
        self.assertIn("account cap", str(ctx.exception))

        A.over_session_cap = True
        loud = run_cmd._concurrency(self.workspace, {}, A())
        self.assertEqual(loud["sessions"], 36)
        self.assertTrue(loud["over_session_cap"])

        class B:
            workers = 8
            reviewers = 5
            over_session_cap = False

        fine = run_cmd._concurrency(self.workspace, {}, B())
        self.assertEqual(fine["sessions"], 13)
        self.assertNotIn("over_session_cap", fine)

    def test_execute_is_opt_in(self):
        """A dry run must not spawn a process, whatever the step says."""
        from run import remote

        step = {"stage": "monitor", "title": "t", "argv": ["false"], "shell": "false"}
        results = remote.execute([step], ["monitor"], dry_run=True, stream=io.StringIO())
        self.assertEqual(results, [{"stage": "monitor", "title": "t", "ran": False}])

    FLEET_KW = dict(
        source={"source_id": PUBLISHED_ID, "title": "An Introduction to Measure Theory",
                "source_sha": PUBLISHED_SHA, "text": f"/s/{PUBLISHED_ID}/text.txt",
                "pages_dir": f"/s/{PUBLISHED_ID}/pages", "page_offset": 16, "errata": ""},
        repos=[{"name": "ours", "repo": "/r/ours",
                "statements": "/c/ours.jsonl", "conventions": "total functions"}],
        mode="differential",
        gate={"command": "python3 gate.py", "config": "/c/gate.local.json",
              "kinds": ["CONTENT"], "witness_required": False,
              "quote_min_chars": 25, "scratch_dir": "/tmp/sg"},
    )

    def test_local_fleet_repos_come_from_the_workspace(self):
        """`Workspace.companion` returns a CompanionRef, not a mapping.

        This built `args.repos` with `entry.get("repo")`, so `cf run --local`
        died with AttributeError before writing anything -- and a companion that
        run.yaml names but workspace.yaml does not must still get a row, with an
        empty repo, rather than taking the command down.
        """
        from cfcmd import run as run_cmd

        (self.workspace.paths.companion_dir("ours")).mkdir(parents=True, exist_ok=True)
        self.workspace.paths.conventions_md("ours").write_text(
            "## 1. Total functions\n\n- junk values are 0\n", encoding="utf-8")
        rows = run_cmd._local_repos(self.workspace, self.run)
        self.assertEqual([r["name"] for r in rows], ["ours"])
        self.assertEqual(rows[0]["repo"], str(self.ours))
        self.assertIn("junk values", rows[0]["conventions"])
        gone = run_cmd._local_repos(self.workspace, dict(self.run, repos=["ghost"]))
        self.assertEqual(gone[0]["repo"], "")

    def test_the_local_fleet_source_block_is_the_run_s_own(self):
        """run.yaml is the record of which edition a planned run reads."""
        from cfcmd import run as run_cmd

        self.assertEqual(run_cmd._run_source(self.workspace, self.run), PUBLISHED_ID)
        self.assertEqual(
            run_cmd._run_source(self.workspace,
                                dict(self.run, sources=[{"id": DRAFT_ID}])), DRAFT_ID)
        # a run.yaml naming none, in a workspace holding two, is not guessed at
        self.assertEqual(run_cmd._run_source(self.workspace, {}), "")

    def test_local_fleet_refuses_a_big_run(self):
        from run import fleet_local

        args = fleet_local.build(self.run_dir, "demo", max_items=100, **self.FLEET_KW)
        self.assertEqual(args["count"], 3)
        self.assertEqual(args["model"], "opus")
        with self.assertRaises(Exception):
            fleet_local.build(self.run_dir, "demo", max_items=2, **self.FLEET_KW)
        self.assertEqual(
            fleet_local.build(self.run_dir, "demo", max_items=2, force=True,
                              **self.FLEET_KW)["count"], 3)

    def test_local_fleet_args_carry_every_key_the_workflow_reads(self):
        """The shipped .js dereferences these; the old builder shared none of them."""
        from run import fleet_local

        args = fleet_local.build(self.run_dir, "demo", max_items=100, **self.FLEET_KW)
        self.assertEqual(fleet_local.missing_keys(args), [])
        # and every key the script actually touches is one of ours
        js = (SCRIPTS.parent / "workflows" / "fleet-local.workflow.js").read_text()
        for key in re.findall(r"\bA\.([a-z_]+)", js):
            if key not in ("items",):
                self.assertIn(key, args, f"workflow reads args.{key}")
        for key in re.findall(r"\bC\.([a-z_]+)", js):
            self.assertIn(key, args["source"], f"workflow reads args.source.{key}")
        for key in re.findall(r"\bGATE\.([a-z_]+)", js):
            self.assertIn(key, args["gate"], f"workflow reads args.gate.{key}")

    def test_local_fleet_mode_maps_to_a_workflow_brief(self):
        from run import fleet_local

        js = (SCRIPTS.parent / "workflows" / "fleet-local.workflow.js").read_text()
        for mode in ("differential", "single-source", "attack"):
            self.assertIn(mode, js)
        self.assertEqual(fleet_local.workflow_mode("attack", []), "attack")
        self.assertEqual(
            fleet_local.workflow_mode("differential", [{"repos": ["ours"]}]), "single-source")
        self.assertEqual(
            fleet_local.workflow_mode("differential", [{"repos": ["ours", "adam"]}]),
            "differential")

    def test_local_fleet_items_carry_repos_pages_and_a_note(self):
        from run import fleet_local

        meta = {t["item"]: {"repos": ["ours", "adam"], "pdf_pages": [21]}
                for t in fleet_local.task_args(self.run_dir)}
        items = fleet_local.item_args(self.run_dir, meta, source_id=PUBLISHED_ID)
        self.assertTrue(items)
        for it in items:
            self.assertEqual(it["repos"], ["ours", "adam"])
            self.assertEqual(it["pdf_pages"], [21])
            self.assertEqual(it["note"], "")
            self.assertEqual(it["source_id"], PUBLISHED_ID)
        single = fleet_local.item_args(
            self.run_dir, {t["item"]: {"repos": ["ours"]} for t in items})
        self.assertIn("only ours formalises", single[0]["note"])

    def test_an_item_carries_its_own_source_over_the_run_s(self):
        """The workflow keys its per-item source map on exactly this field.

        `workflows/fleet-local.workflow.js` builds SRC_OF from `t.source_id` and
        stamps every finding with it, so an item whose pairs row names the other
        edition must carry that edition, not the run's default.
        """
        from run import fleet_local

        rows = fleet_local.task_args(self.run_dir)
        meta = {t["item"]: {"repos": ["ours"]} for t in rows}
        meta[rows[0]["item"]]["source_id"] = DRAFT_ID
        items = fleet_local.item_args(self.run_dir, meta, source_id=PUBLISHED_ID)
        self.assertEqual(items[0]["source_id"], DRAFT_ID)
        self.assertEqual([i["source_id"] for i in items[1:]],
                         [PUBLISHED_ID] * (len(items) - 1))

    def test_a_run_spanning_two_sources_names_the_source_in_every_item_note(self):
        """"Exercise 1.3.19" does not tell a worker whose page to open."""
        from run import fleet_local

        rows = fleet_local.task_args(self.run_dir)
        meta = {t["item"]: {"repos": ["ours", "adam"]} for t in rows}
        meta[rows[0]["item"]]["source_id"] = DRAFT_ID
        args = fleet_local.build(self.run_dir, "demo", max_items=100,
                                 item_meta=meta,
                                 **dict(self.FLEET_KW, mode="differential"))
        self.assertEqual({i["source_id"] for i in args["items"]},
                         {DRAFT_ID, PUBLISHED_ID})
        for item in args["items"]:
            self.assertIn(f"source `{item['source_id']}`", item["note"])
        # ... and a single-source run does not carry that noise
        one = fleet_local.build(self.run_dir, "demo", max_items=100,
                                item_meta={t["item"]: {"repos": ["ours", "adam"]}
                                           for t in rows}, **self.FLEET_KW)
        self.assertEqual([i["note"] for i in one["items"]], [""] * len(one["items"]))


if __name__ == "__main__":
    unittest.main()
