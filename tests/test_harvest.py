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


def write_findings(directory: Path, item: str, defects: list[dict]) -> Path:
    """A minimal v1-shaped findings file, as the runs merged them."""
    directory.mkdir(parents=True, exist_ok=True)
    name = item.lower().replace(" ", "-").replace(".", "-") + ".json"
    path = directory / name
    path.write_text(json.dumps({
        "item": item, "book_located": True, "book_passage_quote": "q",
        "matches": [], "disagreements": defects, "coverage_notes": "",
    }), encoding="utf-8")
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
        # teorth's "In-text definition, Exercise 1.4.24" is a finding about 1.4.24
        self.assertEqual(tally_mod.collapse_to_book_item("In-text definition, Exercise 1.4.24"),
                         "Exercise 1.4.24")

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
        self.assertEqual(teorth["collapsed"]["substantive"], 40)
        self.assertEqual(teorth["collapsed"]["high"], 18)

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
        receipt = harvest_mod.harvest(run_id="run-1",
                                      source={"kind": "local", "path": str(self.source)},
                                      dest=self.dest, book_sha="abc", verify_sha=True)
        self.assertTrue(receipt["complete"])
        self.assertEqual(receipt["expected"]["count"], 8)
        self.assertEqual(receipt["actual"]["count"], 8)
        self.assertEqual(receipt["missing"], [])
        self.assertEqual(len(receipt["manifest"]), 8)
        self.assertEqual(receipt["book_sha"], "abc")
        self.assertTrue((self.dest / harvest_mod.RECEIPT_NAME).exists())

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

    def test_edition_audit_names_findings_from_another_edition(self):
        """Batch-1 findings were judged against the 2011 draft; anchors differ."""
        write_findings(self.source, "Exercise 1.1.99", [])
        harvest_mod.harvest(run_id="run-1", source={"kind": "local", "path": str(self.source)},
                            dest=self.dest, book_sha="published-sha")
        victim = self.dest / "exercise-1-1-99.json"
        payload = json.loads(victim.read_text())
        payload["book_sha"] = "draft-sha"
        victim.write_text(json.dumps(payload), encoding="utf-8")
        audit = harvest_mod.edition_audit(self.dest, "published-sha")
        self.assertEqual(audit["mismatched"], ["exercise-1-1-99.json"])
        self.assertEqual(len(audit["no_book_sha"]), 8)
        self.assertFalse(harvest_mod.edition_audit(self.dest, "")["checked"])

    def test_an_edition_mismatch_fails_the_receipt(self):
        """SKILL.md hard rule 3: a mismatch is refused, not warned about."""
        write_findings(self.source, "Exercise 1.1.99", [])
        harvest_mod.harvest(run_id="run-1", source={"kind": "local", "path": str(self.source)},
                            dest=self.dest, book_sha="published-sha")
        victim = self.dest / "exercise-1-1-99.json"
        payload = json.loads(victim.read_text())
        payload["book_sha"] = "draft-sha"
        victim.write_text(json.dumps(payload), encoding="utf-8")

        receipt = harvest_mod.build_receipt(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            expected=[p.name for p in self.source.iterdir()], dest=self.dest,
            book_sha="published-sha")
        self.assertFalse(receipt["complete"])
        self.assertFalse(receipt["edition"]["ok"])
        self.assertIn("REFUSED", harvest_mod.describe(receipt))

        allowed = harvest_mod.build_receipt(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            expected=[p.name for p in self.source.iterdir()], dest=self.dest,
            book_sha="published-sha", allow_edition_mismatch=True)
        self.assertTrue(allowed["complete"])
        self.assertTrue(allowed["edition"]["ok"])
        self.assertTrue(allowed["edition"]["allowed_mismatch"])

    def test_a_missing_book_sha_is_recorded_but_not_fatal(self):
        """v1 rows carry none until convert_v1 stamps them."""
        receipt = harvest_mod.harvest(
            run_id="run-1", source={"kind": "local", "path": str(self.source)},
            dest=self.dest, book_sha="published-sha")
        self.assertTrue(receipt["complete"])
        self.assertEqual(len(receipt["edition"]["no_book_sha"]), 8)

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

        book = self.root / "book.txt"
        book.write_text("Show that the union of two elementary sets is elementary "
                        "and that the intersection is elementary as well.\n", encoding="utf-8")
        cfg = self.root / "gate.local.json"
        cfg.write_text(json.dumps({
            "schema_version": 2, "preset": "differential-text", "mode": "differential",
            "run_id": "run-1", "book": {"text": str(book), "sha256": "abc"},
            "repos": ["ours"], "kinds": ["CONTENT", "COVERAGE"],
            "verdicts": ["AGREE", "CONTENT_DELTA"], "witness_required": False,
            "require_book_located": True, "require_book_sha": True,
            "findings_dir": ".", "kind_rules": {},
        }), encoding="utf-8")

        good = self.root / "corpus" / "gated"
        good.mkdir(parents=True)
        (good / "exercise-1-2-4.json").write_text(json.dumps({
            "schema_version": 2, "run_id": "run-1", "book_sha": "abc",
            "item": "Exercise 1.2.4", "book_located": True,
            "book_passage_quote": "Show that the union of two elementary sets is elementary",
            "matches": [], "disagreements": [], "coverage_notes": "",
        }), encoding="utf-8")
        result = gate_corpus.gate_corpus(good, cfg)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["files"], 1)

        # ... and the receipt it writes beside the findings is not gated as one
        (good / harvest_mod.RECEIPT_NAME).write_text(json.dumps({"run_id": "run-1"}),
                                                     encoding="utf-8")
        self.assertTrue(gate_corpus.gate_corpus(good, cfg)["ok"])

        (good / "fabricated.json").write_text(json.dumps({
            "schema_version": 2, "run_id": "run-1", "book_sha": "abc",
            "item": "Exercise 1.2.5", "book_located": True,
            "book_passage_quote": "Every bounded function on the unit interval is Riemann "
                                  "integrable whenever the measure is complete.",
            "matches": [], "disagreements": [], "coverage_notes": "",
        }), encoding="utf-8")
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


# ---------------------------------------------------------------------------
# the run planner: allowlist, budget, disk, out-of-tree gate


class TestRunPlanner(unittest.TestCase):
    """cf run's refusals, exercised without touching a network."""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        from cflib.campaign import Campaign

        self.root = scratch(self)
        (self.root / "campaign.yaml").write_text(
            "version: 1\n"
            "book:\n  id: tao-measure\n  sha256: abc123\n  offset: 16\n"
            "companions:\n  - name: ours\n    repo: /tmp/ours\n"
            "hosts:\n  allowlist: [testbox]\n  remote_root: /home/tester\n"
            "  testbox:\n    ssh: tester@testbox\n"
            "budget:\n  max_cost_usd: 25\n", encoding="utf-8")
        self.campaign = Campaign.load(self.root)
        self.run_dir = self.root / "runs" / "demo"
        (self.run_dir / "tasks").mkdir(parents=True)
        for i in (1, 2, 3):
            (self.run_dir / "tasks" / f"exercise-1-1-{i}.yaml").write_text(
                f"schema_version: 1\nid: exercise-1-1-{i}\ntitle: \"hunt: Exercise 1.1.{i}\"\n"
                f"description: |\n  probe\n", encoding="utf-8")
        self.run = {"run_id": "demo", "mode": "attack:hunt", "companions": ["ours"],
                    "book": {"id": "tao-measure", "sha256": "abc123", "access": "text"},
                    "remote": {"entrypoint": "hunt", "tend_root": "hunt-tend"}}

    def test_host_must_be_allowlisted(self):
        from run import manifest

        self.assertEqual(manifest.resolve_host(self.campaign, "testbox")["ssh"], "tester@testbox")
        with self.assertRaises(Exception):
            manifest.resolve_host(self.campaign, "ffmain")

    def test_sources_and_book_are_excluded_by_default(self):
        from run import manifest

        host = manifest.resolve_host(self.campaign, "testbox")
        entries, _ = manifest.build_manifest(
            campaign_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_book=False,
            companions=manifest.companion_repos(self.campaign))
        names = [e["name"] for e in entries]
        self.assertNotIn("source:ours", names)
        self.assertFalse([n for n in names if n.startswith("book-")])
        self.assertIn("gate", names)

    def test_sources_are_transferred_read_only(self):
        from run import manifest

        host = manifest.resolve_host(self.campaign, "testbox")
        entries, _ = manifest.build_manifest(
            campaign_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=True, include_book=False,
            companions={"ours": str(self.root)})
        source = [e for e in entries if e["name"] == "source:ours"][0]
        self.assertIn("--chmod=D0555,F0444", source["rsync_flags"])

    def test_gate_is_transferred_out_of_tree(self):
        from run import manifest

        host = manifest.resolve_host(self.campaign, "testbox")
        entries, _ = manifest.build_manifest(
            campaign_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_book=False, companions={})
        gate = [e for e in entries if e["name"] == "gate"][0]
        entrypoint = f"{host['remote_root']}/hunt/"
        self.assertFalse(gate["dest"].startswith(entrypoint))

    def test_in_tree_gate_config_is_reported(self):
        from run import manifest

        host = manifest.resolve_host(self.campaign, "testbox")
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

        host = manifest.resolve_host(self.campaign, "testbox")
        gate_dir = self.run_dir / "tend-root" / "gate"
        gate_dir.mkdir(parents=True)
        (gate_dir / "verify_findings.py").write_text("# gate\n", encoding="utf-8")
        (gate_dir / "gate.json").write_text("{}", encoding="utf-8")
        entries, _ = manifest.build_manifest(
            campaign_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_book=False, companions={})
        gate = [e for e in entries if e["name"] == "gate"][0]
        self.assertEqual(gate["src"], str(gate_dir))
        self.assertEqual(gate["dest"], f"{host['remote_root']}/demo-gate/")
        self.assertNotIn("gate-config", [e["name"] for e in entries])
        tend = [e for e in entries if e["name"] == "tend-root"][0]
        self.assertIn("--exclude=gate/", tend["rsync_flags"])

    def test_budget_cap_is_required(self):
        from run import manifest
        from cflib.campaign import Campaign

        budget = manifest.check_budget_cap(self.campaign, self.run, None, False)
        self.assertEqual(budget["max_cost"], 25.0)
        (self.root / "campaign.yaml").write_text(
            "version: 1\nbook:\n  id: b\n  offset: 16\nhosts:\n  allowlist: [testbox]\n",
            encoding="utf-8")
        bare = Campaign.load(self.root)
        with self.assertRaises(Exception):
            manifest.check_budget_cap(bare, self.run, None, False)
        self.assertTrue(manifest.check_budget_cap(bare, self.run, None, True)["acknowledged_uncapped"])

    def test_disk_budget_flags_a_worktree_blowout(self):
        from run import manifest

        host = manifest.resolve_host(self.campaign, "testbox")
        host["disk_budget_bytes"] = 1000
        entries, _ = manifest.build_manifest(
            campaign_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_book=False, companions={})
        disk = manifest.disk_budget(entries, 763, host)
        self.assertTrue(disk["over_budget"])
        self.assertIn("--copy-dir --cow", disk["advice"])

    def test_commands_smoke_before_raise(self):
        from run import manifest, remote

        host = manifest.resolve_host(self.campaign, "testbox")
        entries, _ = manifest.build_manifest(
            campaign_dir=self.root, skill_dir=SCRIPTS.parent, run_id="demo", run=self.run,
            host=host, include_sources=False, include_book=False, companions={})
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
            run_cmd._concurrency(self.campaign, {}, A())
        self.assertIn("account cap", str(ctx.exception))

        A.over_session_cap = True
        loud = run_cmd._concurrency(self.campaign, {}, A())
        self.assertEqual(loud["sessions"], 36)
        self.assertTrue(loud["over_session_cap"])

        class B:
            workers = 8
            reviewers = 5
            over_session_cap = False

        fine = run_cmd._concurrency(self.campaign, {}, B())
        self.assertEqual(fine["sessions"], 13)
        self.assertNotIn("over_session_cap", fine)

    def test_execute_is_opt_in(self):
        """A dry run must not spawn a process, whatever the step says."""
        from run import remote

        step = {"stage": "monitor", "title": "t", "argv": ["false"], "shell": "false"}
        results = remote.execute([step], ["monitor"], dry_run=True, stream=io.StringIO())
        self.assertEqual(results, [{"stage": "monitor", "title": "t", "ran": False}])

    FLEET_KW = dict(
        campaign={"book_id": "b", "book_title": "B", "book_sha": "0" * 64,
                  "book_layout_txt": "/b/book.txt", "book_pages_dir": "/b/pages",
                  "pdf_page_offset": 16, "errata": ""},
        repos=[{"name": "ours", "repo": "/r/ours",
                "statements": "/c/ours.jsonl", "conventions": "total functions"}],
        mode="differential",
        gate={"command": "python3 gate.py", "kinds": ["CONTENT"], "witness_required": False,
              "quote_min_chars": 25, "scratch_dir": "/tmp/sg"},
    )

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
            self.assertIn(key, args["campaign"], f"workflow reads args.campaign.{key}")
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
        items = fleet_local.item_args(self.run_dir, meta)
        self.assertTrue(items)
        for it in items:
            self.assertEqual(it["repos"], ["ours", "adam"])
            self.assertEqual(it["pdf_pages"], [21])
            self.assertEqual(it["note"], "")
        single = fleet_local.item_args(
            self.run_dir, {t["item"]: {"repos": ["ours"]} for t in items})
        self.assertIn("only ours formalises", single[0]["note"])


if __name__ == "__main__":
    unittest.main()
