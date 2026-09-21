"""Review fixes for the revision pin: what a finding was judged against.

One class per finding, named after what used to be wrong.

* nothing pinned a finding to a companion revision, so a claim about a Lean
  statement could not be re-checked, closed or expired: six months later a live
  finding and one already fixed upstream read identically (critic#2);
* the run-independent key was taken over the label a worker WROTE, so two runs
  that spelled one book item differently produced two keys and
  ``union --group-by finding-id --new-only`` still called the second sighting
  new -- the "exact" grouping was not even a refinement of the default one
  (gates-correctness#6, the half the earlier guard left).

Every assertion here is pessimistic: an unpinned row is reported as unpinned and
never fingerprinted against today's HEAD, a reformatted statement is not a
changed one, a changed one is never silently closed, and a re-check moves no
verdict.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
if str(SKILL / "scripts") not in sys.path:
    sys.path.insert(0, str(SKILL / "scripts"))

from cflib import hashing, vocab  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from cflib.workspace import Workspace  # noqa: E402
from cflib.yamlio import read_yaml, write_yaml  # noqa: E402
from harvest import tally as tally_mod  # noqa: E402
from harvest import union as union_mod  # noqa: E402
from ledger import findings as findings_mod  # noqa: E402
from ledger import transcribe as transcribe_mod  # noqa: E402
from ledger import validate_ledger as vl  # noqa: E402
from recheck import pin as pin_mod  # noqa: E402
from recheck import recheck as recheck_mod  # noqa: E402

SOURCE_ID = "a-book-first-edition"
SOURCE_SHA = "a1" * 32
BASELINE_CORPUS = os.environ.get("CF_BASELINE_CORPUS")

LEAN = """\
import Mathlib

namespace Demo

/-- `Lemma 1.2.5` (finite additivity). -/
theorem add_mono (a b : Nat) (h : a <= b) : True := trivial

/-- `Exercise 1.2.4` (union). -/
theorem union_elem (s : Nat) : True := trivial

end Demo
"""

MINI_BOOK = (
    "Exercise 1.2.4 (Boolean closure). Show that the union of two elementary "
    "sets is elementary.\n\x0c"
    "Lemma 1.2.5 (Finite additivity). Show that the measure of a union adds.\n"
)

CONVENTIONS = """# House conventions -- ours

confirmed_by: the repo owner
confirmed_at: 2026-01-01

## 1. Total functions and junk values

- the measure of a non-measurable set is `0` by junk value.

## 2. Standing type and structure conventions

- unsigned quantities live in `ℝ≥0∞`.

## 7. Known-and-accepted deltas

- the bundled subtype in `Foo` is deliberate; the owner accepted it in 2026.
"""

ADAPTER = """\
name: ours
repo: {repo}
adapter: docstring_tags
granularity: item
sources:
  default: {source_id}
  map: []
glob: "Src/**/*.lean"
tag:
  position: docstring_start
  require_backticks: true
"""


def scratch(case: unittest.TestCase) -> Path:
    """A temp directory that honours CF_TEST_TMP and is removed afterwards."""
    path = Path(tempfile.mkdtemp(dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def git(repo: Path, *args: str) -> None:
    """A git command in a FIXTURE repo (ours to write; companions never are)."""
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.invalid",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e.invalid",
               GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   capture_output=True, text=True)


def write_doc(directory: Path, item: str, decls: list[str], *, kind: str = "CONTENT",
              name: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or item.lower().replace(" ", "-").replace(".", "-") + ".json")
    path.write_text(json.dumps({
        "schema_version": 2, "item": item, "source_id": SOURCE_ID,
        "source_sha": SOURCE_SHA, "book_located": True, "book_passage_quote": "q",
        "matches": [], "coverage_notes": "",
        "disagreements": [{"kind": kind, "severity": "high", "concept": "the concept",
                           "reasoning": "because the hypothesis differs",
                           "book_quote": "Let E be a measurable set.",
                           "unfaithful_decls": list(decls)}],
    }), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# gates-correctness#6 -- the half the earlier guard left


class TestTheRunIndependentKeyJoinsTwoSpellingsOfOneItem(unittest.TestCase):
    """`--group-by finding-id` linked two runs only when they agreed on the label.

    The key hashed the label as WRITTEN, so one defect at ``Exercise 1.6.27``
    reported by one run as ``Exercise 1.6.27`` and by another as
    ``Exercise 1.6.27(iii)`` landed in two groups -- and ``--new-only`` called the
    second one new, which is the exact regression (re-found defects scored as
    misses) union.py exists to fix.  It is also the duplicate its own header
    records for ``Exercise 1.6.39``.
    """

    def setUp(self):
        self.root = scratch(self)
        self.runs = {}
        for run, label in (("run-a", "Exercise 1.6.27"), ("run-b", "Exercise 1.6.27(iii)")):
            directory = self.root / "corpus" / run
            write_doc(directory, label, ["ours:Foo.bar"], name="e.json")
            self.runs[run] = directory
        self.rows = tally_mod.collect(self.runs)

    def test_both_runs_collapse_onto_one_book_item(self):
        self.assertEqual(sorted({r["item"] for r in self.rows}), ["Exercise 1.6.27"])
        self.assertEqual(len({r["item_raw"] for r in self.rows}), 2, "two spellings")

    def test_one_defect_in_two_runs_is_one_key_and_one_entry(self):
        self.assertEqual(len({r["defect_key"] for r in self.rows}), 1)
        entries = union_mod.union(self.rows, group_by="finding-id")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["runs"], ["run-a", "run-b"])
        self.assertEqual(union_mod.summary(entries)["multi_run"], 1)
        self.assertEqual(len(entries[0]["finding_ids"]), 2, "one id per run, kept")

    def test_a_re_found_defect_is_not_new_under_either_grouping(self):
        for group_by in ("item-kind", "finding-id"):
            with self.subTest(group_by=group_by):
                self.assertEqual(
                    union_mod.union(self.rows, group_by=group_by,
                                    baseline_runs=["run-a"], new_only=True), [])

    def test_the_exact_grouping_is_a_refinement_of_the_default_one(self):
        """The property that makes `finding-id` safe: it may split, never re-split
        a defect the default key joined."""
        write_doc(self.root / "corpus" / "run-c", "Exercise 1.6.27",
                  ["ours:Other.decl"], name="e.json")
        rows = self.rows + tally_mod.collect({"run-c": self.root / "corpus" / "run-c"})
        groups: dict[tuple, set] = {}
        for row in rows:
            groups.setdefault((row["source_id"], row["repo"], row["defect_key"]), set()).add(
                (row["source_id"], row["repo"], row["item"], row["kind"]))
        spanning = {k: v for k, v in groups.items() if len(v) > 1}
        self.assertEqual(spanning, {})
        self.assertEqual(len(union_mod.union(rows, group_by="item-kind")), 1)
        self.assertEqual(len(union_mod.union(rows, group_by="finding-id")), 2,
                         "two declarations at one item are still two defects")

    def test_the_option_is_named_after_what_it_groups_on(self):
        """`--group-by finding-id` never grouped on the finding_id -- union.py's own
        header says so. The old spelling keeps working and says it is the old one."""
        self.assertEqual(union_mod.canonical_group_by("finding-id"), "defect-key")
        self.assertEqual(union_mod.union(self.rows, group_by="defect-key"),
                         union_mod.union(self.rows, group_by="finding-id"))
        doc = union_mod.build(self.runs, group_by="finding-id")
        self.assertEqual(doc["group_by"], "defect-key",
                         "the document records the name the grouping really has")
        self.assertIn("finding-id", union_mod.GROUP_BY_CHOICES)

    def test_there_is_one_definition_of_the_key(self):
        args = (SOURCE_ID, "Exercise 1.2.1", "CONTENT", ["ours:mono"])
        self.assertEqual(tally_mod.defect_key(*args), hashing.defect_key(*args))

    def test_the_key_is_the_frozen_id_with_no_run(self):
        """cflib.hashing is one file: the key must not drift from the id."""
        self.assertEqual(
            hashing.defect_key(SOURCE_ID, "Exercise  1.2.1", "CONTENT", ["b:two", "a:one"]),
            hashing.sha256_text("\n".join(
                ["", SOURCE_ID, "Exercise 1.2.1", "CONTENT", "a:one", "b:two"])))

    @unittest.skipUnless(BASELINE_CORPUS, "CF_BASELINE_CORPUS is not set")
    def test_the_real_corpus_has_defects_only_the_join_key_links(self):
        """Measured on the landed corpus, not argued from a fixture.

        Adding ``item_raw`` back to the key tuple reconstructs the partition the
        old key produced (it hashed the raw label, and one raw label maps to one
        collapsed item), so the two counts are the before and after.  Fewer keys
        means defects that two runs labelled differently now meet -- and each of
        them is a row ``--new-only`` used to call new.
        """
        base = Path(BASELINE_CORPUS)
        runs = {p.name: p for p in sorted(base.iterdir())
                if p.is_dir() and any(p.glob("*.json"))}
        rows = tally_mod.collect(runs)
        self.assertTrue(rows, f"{base} holds no findings")
        joined = {(r["source_id"], r["repo"], r["defect_key"]) for r in rows}
        by_raw_label = {(r["source_id"], r["repo"], r["item_raw"], r["defect_key"])
                        for r in rows}
        self.assertLess(len(joined), len(by_raw_label))
        # and the fix must not cost the refinement property: no key may span two
        # book items, or `finding-id` would re-split what `item-kind` joined
        spanning: dict[str, set] = {}
        for row in rows:
            spanning.setdefault(row["defect_key"], set()).add(
                (row["source_id"], row["item"], row["kind"]))
        self.assertEqual({k: sorted(v) for k, v in spanning.items() if len(v) > 1}, {})


# ---------------------------------------------------------------------------
# critic#2 (2) -- the per-declaration fingerprint


class TestADeclarationIsFingerprintedByItsStatement(unittest.TestCase):
    """A re-check has to be quiet about formatting and loud about content."""

    BASE = "theorem add_mono (a b : Nat) (h : a <= b) : True"

    def test_reindenting_does_not_move_the_fingerprint(self):
        wrapped = "theorem add_mono (a b : Nat)\n    (h : a <= b) :\n    True"
        self.assertEqual(pin_mod.decl_fingerprint(self.BASE),
                         pin_mod.decl_fingerprint(wrapped))

    def test_rewording_the_docstring_does_not_move_it(self):
        self.assertEqual(
            pin_mod.decl_fingerprint("/-- `Lemma 1.2.5` (additivity). -/ " + self.BASE),
            pin_mod.decl_fingerprint("/-- `Lemma 1.2.5` (additivity, reworded). -/ "
                                     + self.BASE))

    def test_a_changed_hypothesis_does_move_it(self):
        self.assertNotEqual(pin_mod.decl_fingerprint(self.BASE),
                            pin_mod.decl_fingerprint(self.BASE.replace("<=", "<")))

    def test_it_is_a_sha256_of_the_normalised_text(self):
        self.assertEqual(pin_mod.decl_fingerprint(self.BASE),
                         hashing.sha256_text(pin_mod.normalise_statement(self.BASE)))
        self.assertRegex(pin_mod.decl_fingerprint(self.BASE), r"^[0-9a-f]{64}$")

    def test_a_declaration_keeps_its_repo_prefix_out_of_the_lookup(self):
        self.assertEqual(pin_mod.split_decl("ours:Demo.add_mono"), ("ours", "Demo.add_mono"))
        self.assertEqual(pin_mod.split_decl("Demo.add_mono"), ("", "Demo.add_mono"))
        self.assertEqual(
            pin_mod.split_decl("teorth:example «Exercise 1.2.1 (union)» at A.lean:3"),
            ("teorth", "example «Exercise 1.2.1 (union)» at A.lean:3"),
            "the FIRST colon splits: a Lean name may carry later ones")

    def test_the_table_is_read_by_declaration(self):
        root = scratch(self)
        path = root / "statements.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in [
            {"_meta": {"file": "statements"}},
            {"decl": "Demo.add_mono", "statement": self.BASE},
            {"decl": "Demo.union_elem", "statement": "theorem union_elem : True"},
        ]), encoding="utf-8")
        prints = pin_mod.statement_fingerprints(path)
        self.assertEqual(sorted(prints), ["Demo.add_mono", "Demo.union_elem"])
        self.assertEqual(prints["Demo.add_mono"], pin_mod.decl_fingerprint(self.BASE))

    def test_a_missing_table_is_no_fingerprints_not_a_crash(self):
        self.assertEqual(pin_mod.statement_fingerprints(scratch(self) / "nope.jsonl"), {})


# ---------------------------------------------------------------------------
# critic#2 (1) -- the plan-time commit


class TestTheCheckoutIsPinnedThroughGitsafe(unittest.TestCase):
    """`dirty` is part of the pin: a tree that is not its commit is not pinned."""

    def setUp(self):
        self.repo = scratch(self) / "repo"
        (self.repo / "Src").mkdir(parents=True)
        (self.repo / "Src" / "A.lean").write_text(LEAN, encoding="utf-8")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "one")

    def test_a_clean_checkout_pins_its_head(self):
        pin = pin_mod.repo_pin(self.repo)
        self.assertRegex(pin["repo_commit"], r"^[0-9a-f]{40}$")
        self.assertIs(pin["dirty"], False)

    def test_an_edited_tree_is_dirty(self):
        (self.repo / "Src" / "A.lean").write_text(LEAN + "\n-- edit\n", encoding="utf-8")
        self.assertIs(pin_mod.repo_pin(self.repo)["dirty"], True)

    def test_an_untracked_lean_file_is_dirty_too(self):
        """It was audited and it is in no commit, so the commit does not hold it."""
        (self.repo / "Src" / "B.lean").write_text(LEAN, encoding="utf-8")
        self.assertIs(pin_mod.repo_pin(self.repo)["dirty"], True)

    def test_a_tree_that_is_not_a_checkout_pins_nothing_and_says_why(self):
        plain = scratch(self)
        pin = pin_mod.repo_pin(plain)
        self.assertIsNone(pin["repo_commit"])
        self.assertIn("not a git checkout", pin["method"])
        self.assertIsNone(pin_mod.repo_pin(None)["repo_commit"])

    def test_the_repo_cannot_name_a_program_for_the_pin_to_run(self):
        """The whole reason this goes through cflib.gitsafe: reading a tree must
        not execute what the tree chose."""
        marker = self.repo.parent / "pwned"
        git(self.repo, "config", "core.fsmonitor",
            f"sh -c 'touch {marker}'")
        git(self.repo, "config", "core.hooksPath", str(self.repo / "hooks"))
        pin = pin_mod.repo_pin(self.repo)
        self.assertRegex(str(pin["repo_commit"]), r"^[0-9a-f]{40}$")
        self.assertFalse(marker.exists(),
                         "the audited repository's own config was executed")


# ---------------------------------------------------------------------------
# a fixture workspace with one git-backed companion


class WorkspaceCase(unittest.TestCase):
    """One companion (a real git checkout), one source, one extracted table."""

    def setUp(self):
        self.tmp = scratch(self)
        self.root = self.tmp / "ws"
        self.root.mkdir(parents=True)
        (self.root / "workspace.yaml").write_text(
            "version: 2\nworkspace:\n  name: rf\ncompanions: {}\nsources: {}\n",
            encoding="utf-8")
        self.repo = self.tmp / "repos" / "ours"
        (self.repo / "Src").mkdir(parents=True)
        self.lean = self.repo / "Src" / "A.lean"
        self.lean.write_text(LEAN, encoding="utf-8")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "one")

        ws = Workspace.load(self.root)
        ws.upsert_source(SOURCE_ID, sha256=SOURCE_SHA, title="A Book", edition="first",
                         offset=0, offset_confidence=0.99, offset_method="declared",
                         kinds=["Definition", "Lemma", "Exercise"], chapters=[1],
                         role="audited", confirmed=True)
        ws.upsert_companion("ours", repo=str(self.repo), adapter="docstring_tags",
                            sources=[SOURCE_ID], anchor_source=SOURCE_ID,
                            write_policy="read_only")
        ws.save()
        self.ws = Workspace.load(self.root)
        d = self.ws.paths.companion_dir("ours")
        d.mkdir(parents=True, exist_ok=True)
        (d / "adapter.yaml").write_text(
            ADAPTER.format(repo=self.repo, source_id=SOURCE_ID), encoding="utf-8")
        self.extract()

    # -- stages ------------------------------------------------------------
    def extract(self):
        from cfcmd import extract as extract_cmd

        class A:
            workspace = str(self.root)
            companion = ["ours"]
            repo = None
            force = True
            dry_run = False
            allow_missing_source_sha = False

        self.assertEqual(extract_cmd.run(A()), 0)
        self.ws = Workspace.load(self.root)

    def plan_run_yaml(self, run_id: str = "r1", *, pin: dict | None = None) -> Path:
        """A run.yaml as `cf plan-run` writes it, carrying the revision pin."""
        p = self.ws.paths
        p.run_dir(run_id).mkdir(parents=True, exist_ok=True)
        doc = {"version": 1, "run_id": run_id, "companion": "ours", "repos": ["ours"]}
        if pin is not False:
            doc["companion_pins"] = {"ours": pin or pin_mod.repo_pin(self.repo)}
        write_yaml(p.run_yaml(run_id), doc, header=f"# run.yaml -- {run_id}\n")
        return Path(p.run_yaml(run_id))

    def corpus(self, run_id: str = "r1", decls=("ours:Demo.add_mono",),
               item: str = "Lemma 1.2.5", kind: str = "CONTENT") -> Path:
        directory = Path(self.ws.paths.corpus_run_dir(run_id))
        write_doc(directory, item, list(decls), kind=kind, name="f.json")
        return directory

    def harvest_pin(self, run_id: str = "r1") -> dict:
        from cfcmd import harvest as harvest_cmd

        run_yaml = Path(self.ws.paths.run_yaml(run_id))
        data = read_yaml(run_yaml) if run_yaml.exists() else {}
        return harvest_cmd._pin(self.ws, run_id, data if isinstance(data, dict) else {},
                               Path(self.ws.paths.corpus_run_dir(run_id)))

    def transcribe(self, run_id: str = "r1", verdict: str = "CONFIRMED_CONTENT") -> dict:
        rows = findings_mod.load_findings([str(self.ws.paths.corpus_run_dir(run_id))])
        index = findings_mod.index_by_id(rows)
        result = {"findings": [
            {"finding_id": row["finding_id"], "item": row["item"],
             "reconciled": {"verdict": verdict, "severity": "high", "confidence": 0.9,
                            "rationale": "the hypothesis differs from the book's, which "
                                         "is the whole finding"}}
            for row in rows]}
        path = self.tmp / f"result.{run_id}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return transcribe_mod.transcribe(str(path), workspace=str(self.root),
                                         companion="ours", source_id=SOURCE_ID,
                                         source_sha=SOURCE_SHA, index=index,
                                         kind="adjudicate", at="2026-09-20")

    def ledger_rows(self, name: str = "accepted") -> list[dict]:
        from cflib.jsonio import read_jsonl

        path = (self.ws.paths.accepted_jsonl("ours") if name == "accepted"
                else self.ws.paths.refuted_jsonl("ours"))
        return read_jsonl(path, skip_meta=True) if Path(path).exists() else []

    def recheck(self, **over) -> dict:
        from cfcmd import recheck as recheck_cmd

        class A:
            workspace = str(self.root)
            companion = ["ours"]
            from_stored = False
            no_write = False
            json = False

        for k, v in over.items():
            setattr(A, k, v)
        args = A()
        self.assertEqual(recheck_cmd.run(args), 0)
        return recheck_cmd._recheck_one(Workspace.load(self.root), "ours", args)

    def stage_a_plannable_run(self) -> None:
        """The pairing artifacts, conventions and host `cf plan-run` needs."""
        p = self.ws.paths
        p.source_dir(SOURCE_ID).mkdir(parents=True, exist_ok=True)
        p.source_text(SOURCE_ID).write_text(MINI_BOOK, encoding="utf-8")
        p.conventions_md("ours").write_text(CONVENTIONS, encoding="utf-8")
        p.pairs_dir(SOURCE_ID).mkdir(parents=True, exist_ok=True)
        p.queue_json(SOURCE_ID).write_text(json.dumps([
            {"item": "Lemma 1.2.5", "repos": ["ours"],
             "decls": {"ours": ["Demo.add_mono"]}, "book_page": 20}]), encoding="utf-8")
        p.targets_json(SOURCE_ID, "ours").write_text(json.dumps([
            {"item": "Lemma 1.2.5", "decls": ["Demo.add_mono"], "peers": [],
             "single_source": True, "book_page": 20, "section": "1.2"}]), encoding="utf-8")
        p.reconciliation_json(SOURCE_ID).write_text(json.dumps({
            "source_id": SOURCE_ID, "source_sha": SOURCE_SHA, "task_universe": 1,
            "task_universe_items": ["Lemma 1.2.5"]}), encoding="utf-8")
        ws = Workspace.load(self.root)
        ws.data["hosts"] = {"allowlist": ["examplebox"],
                            "examplebox": {"ssh": "someone@examplebox",
                                           "remote_root": "/srv/someone"}}
        ws.data["models"] = {"worker": "claude-sonnet-5", "reviewer": "claude-opus-5"}
        ws.save()
        self.ws = Workspace.load(self.root)

    def plan(self, run_id: str = "p1") -> dict:
        from cfcmd import plan_run

        class A:
            workspace = str(self.root)
            preset = "attack-hunt"
            host = "examplebox"
            model = None
            companion = "ours"
            source_id = None
            chapters = None
            max_open_tasks = None
            max_cost = None
            i_have_consent = False
            force = False
            conventions_unconfirmed = False
            allow_unreconciled = False
            allow_sandbox_bypass = False

        A.run_id = run_id
        self.assertEqual(plan_run.run(A()), 0)
        return read_yaml(Workspace.load(self.root).paths.run_yaml(run_id))

    def edit(self, old: str, new: str, *, commit: str = "") -> None:
        text = self.lean.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.lean.write_text(text.replace(old, new), encoding="utf-8")
        if commit:
            git(self.repo, "commit", "-aqm", commit)


# ---------------------------------------------------------------------------
# critic#2 (1) -- the plan records the revision


class TestThePlanRecordsTheRevisionItAudits(WorkspaceCase):
    """`run.yaml` recorded the source sha and never the companion's commit."""

    def setUp(self):
        super().setUp()
        self.stage_a_plannable_run()

    def test_run_yaml_carries_the_companion_commit_and_the_dirty_flag(self):
        doc = self.plan()
        pin = doc["companion_pins"]["ours"]
        self.assertEqual(pin["repo_commit"], pin_mod.repo_pin(self.repo)["repo_commit"])
        self.assertIs(pin["dirty"], False)
        self.assertIn("gitsafe", pin["method"])

    def test_a_dirty_checkout_is_a_warning_the_plan_prints(self):
        self.lean.write_text(LEAN + "\n-- uncommitted\n", encoding="utf-8")
        doc = self.plan()
        self.assertIs(doc["companion_pins"]["ours"]["dirty"], True)
        self.assertTrue([w for w in doc["warnings"] if "dirty" in w],
                        f"no dirty warning in {doc['warnings']}")

    def test_the_pin_is_what_harvest_stamps(self):
        doc = self.plan()
        self.corpus("p1")
        stamped = self.harvest_pin("p1")
        self.assertEqual(stamped["companions"]["ours"]["repo_commit"],
                         doc["companion_pins"]["ours"]["repo_commit"])
        self.assertTrue(stamped["decls"])


# ---------------------------------------------------------------------------
# critic#2 (1)+(2) -- harvest stamps the pin


class TestHarvestStampsTheRevisionPin(WorkspaceCase):
    """Nothing said which revision a landed corpus was judged against."""

    def test_the_pin_names_the_commit_and_fingerprints_the_declaration(self):
        self.plan_run_yaml()
        self.corpus()
        doc = self.harvest_pin()
        head = pin_mod.repo_pin(self.repo)["repo_commit"]
        self.assertEqual(doc["companions"]["ours"]["repo_commit"], head)
        self.assertTrue(doc["companions"]["ours"]["fingerprinted"])
        self.assertEqual(doc["unpinned"], [])
        entry = doc["decls"]["ours:Demo.add_mono"]
        self.assertEqual(entry["repo_commit"], head)
        self.assertEqual(
            entry["decl_fingerprint"],
            pin_mod.decl_fingerprint("theorem add_mono (a b : Nat) (h : a <= b) : True"))

    def test_it_is_written_as_a_report_not_into_the_corpus(self):
        """A new file in corpus/<run>/ would be read as a finding by the gate."""
        self.plan_run_yaml()
        corpus = self.corpus()
        before = sorted(p.name for p in corpus.iterdir())
        self.harvest_pin()
        self.assertEqual(sorted(p.name for p in corpus.iterdir()), before)
        self.assertTrue(Path(self.ws.paths.pin_json("r1")).exists())

    def test_a_corpus_nobody_planned_is_unpinned_not_pinned_to_head(self):
        self.corpus("legacy")  # no run.yaml at all
        doc = self.harvest_pin("legacy")
        self.assertEqual(doc["decls"], {})
        self.assertEqual(doc["unpinned"], ["ours:Demo.add_mono"])

    def test_a_dirty_checkout_is_not_fingerprinted(self):
        pin = dict(pin_mod.repo_pin(self.repo))
        pin["dirty"] = True
        self.plan_run_yaml(pin=pin)
        self.corpus()
        doc = self.harvest_pin()
        self.assertFalse(doc["companions"]["ours"]["fingerprinted"])
        self.assertIn("dirty", doc["companions"]["ours"]["note"])
        self.assertEqual(doc["unpinned"], ["ours:Demo.add_mono"])

    def test_a_table_extracted_at_another_commit_is_not_used(self):
        """The one guess this must never make: fingerprinting against a table the
        companion has moved past would pin the finding to code it never saw."""
        self.plan_run_yaml(pin={"repo_commit": "b" * 40, "dirty": False, "method": "test"})
        self.corpus()
        doc = self.harvest_pin()
        self.assertFalse(doc["companions"]["ours"]["fingerprinted"])
        self.assertIn("extracted at", doc["companions"]["ours"]["note"])
        self.assertEqual(doc["unpinned"], ["ours:Demo.add_mono"])

    def test_a_declaration_no_table_holds_is_unpinned_not_invented(self):
        self.plan_run_yaml()
        self.corpus(decls=("ours:Demo.never_existed",))
        doc = self.harvest_pin()
        self.assertEqual(doc["unpinned"], ["ours:Demo.never_existed"])
        self.assertEqual(doc["decls"], {})


# ---------------------------------------------------------------------------
# critic#2 -- the pin reaches the ledger row


class TestTheLedgerRowCarriesThePin(WorkspaceCase):
    """The row outlives the corpus, so the pin has to live on the row."""

    def test_a_transcribed_row_carries_the_commit_and_the_fingerprints(self):
        self.plan_run_yaml()
        self.corpus()
        self.harvest_pin()
        report = self.transcribe()
        self.assertEqual(report["errors"], [])
        row = self.ledger_rows()[0]
        self.assertEqual(row["repo_commit"], pin_mod.repo_pin(self.repo)["repo_commit"])
        self.assertEqual(sorted(row["decl_fingerprints"]), ["ours:Demo.add_mono"])
        self.assertEqual(vl.validate_row(row, sources={SOURCE_ID: SOURCE_SHA}), [])

    def test_a_run_with_no_pin_file_yields_an_unpinned_row_not_a_wrong_one(self):
        self.corpus()
        report = self.transcribe()
        self.assertEqual(report["errors"], [])
        row = self.ledger_rows()[0]
        self.assertEqual(row["repo_commit"], "")
        self.assertEqual(row["decl_fingerprints"], {})

    def test_the_run_commit_is_kept_even_when_no_declaration_was_fingerprinted(self):
        pin = dict(pin_mod.repo_pin(self.repo))
        pin["dirty"] = True
        self.plan_run_yaml(pin=pin)
        self.corpus()
        self.harvest_pin()
        self.transcribe()
        row = self.ledger_rows()[0]
        self.assertEqual(row["repo_commit"], pin["repo_commit"])
        self.assertEqual(row["decl_fingerprints"], {})


class TestTheValidatorRefusesAPinThatIsNotOne(unittest.TestCase):
    """A truncated pin would report a live finding as changed, which is the one
    failure that stops anyone running a re-check twice."""

    def row(self, **over) -> dict:
        row = {"finding_id": "f" * 64, "item": "Lemma 1.2.5", "source_id": SOURCE_ID,
               "source_sha": SOURCE_SHA, "repo": "ours", "verdict": "CONFIRMED_CONTENT",
               "reason": "a reason long enough to be a reason", "severity": "high",
               "unfaithful_decls": ["ours:Demo.add_mono"]}
        row.update(over)
        return row

    def codes(self, **over) -> list[str]:
        return sorted(e["code"] for e in
                      vl.validate_row(self.row(**over), sources={SOURCE_ID: SOURCE_SHA}))

    def test_an_unpinned_row_is_legal(self):
        self.assertEqual(self.codes(), [])
        self.assertEqual(self.codes(repo_commit="", decl_fingerprints={}), [])

    def test_a_full_pin_is_legal(self):
        self.assertEqual(self.codes(repo_commit="c" * 40,
                                    decl_fingerprints={"ours:Demo.add_mono": "d" * 64}), [])

    def test_a_truncated_commit_is_refused(self):
        self.assertEqual(self.codes(repo_commit="c" * 8), ["repo_commit"])

    def test_a_fingerprint_that_is_not_a_digest_is_refused(self):
        self.assertEqual(
            self.codes(repo_commit="c" * 40,
                       decl_fingerprints={"ours:Demo.add_mono": "nope"}),
            ["decl_fingerprints"])

    def test_a_fingerprint_for_a_declaration_the_row_does_not_name_is_refused(self):
        self.assertEqual(
            self.codes(repo_commit="c" * 40, decl_fingerprints={"ours:Other": "d" * 64}),
            ["decl_fingerprints"])

    def test_fingerprints_without_a_commit_are_refused(self):
        self.assertEqual(
            self.codes(decl_fingerprints={"ours:Demo.add_mono": "d" * 64}), ["repo_commit"])

    def test_a_recheck_block_may_not_adjudicate(self):
        self.assertEqual(self.codes(recheck={"status": "changed", "verdict": "REFUTED"}),
                         ["recheck"])
        self.assertEqual(self.codes(recheck={"status": "changed",
                                             "resolution": "fixed:PR#8"}), ["recheck"])

    def test_a_recheck_status_outside_the_vocabulary_is_refused(self):
        self.assertEqual(self.codes(recheck={"status": "probably-fine"}), ["recheck"])
        self.assertEqual(self.codes(recheck={"status": vocab.RECHECK_UNCHANGED}), [])


# ---------------------------------------------------------------------------
# critic#2 (4) -- cf recheck


class TestRecheckClassifiesEveryOpenFinding(WorkspaceCase):
    """There was no way to ask "is this finding still about today's code?"."""

    def setUp(self):
        super().setUp()
        self.plan_run_yaml()
        self.corpus()
        self.harvest_pin()
        self.assertEqual(self.transcribe()["errors"], [])

    def test_an_untouched_statement_is_unchanged(self):
        report = self.recheck()
        self.assertEqual(report["counts"]["rows"], 1)
        self.assertEqual(report["counts"][vocab.RECHECK_UNCHANGED], 1)
        self.assertFalse(report["entries"][0]["needs_rereview"])

    def test_reformatting_and_rewording_the_docstring_is_still_unchanged(self):
        self.edit("/-- `Lemma 1.2.5` (finite additivity). -/",
                  "/-- `Lemma 1.2.5` (finite additivity, reworded). -/")
        self.edit("theorem add_mono (a b : Nat) (h : a <= b) : True := trivial",
                  "theorem add_mono (a b : Nat)\n    (h : a <= b) :\n    True := trivial",
                  commit="reformat")
        report = self.recheck()
        self.assertEqual(report["counts"][vocab.RECHECK_UNCHANGED], 1)

    def test_a_changed_statement_is_changed_and_shows_both_heads(self):
        self.edit("(h : a <= b)", "(h : a < b)", commit="tighten")
        report = self.recheck()
        self.assertEqual(report["counts"][vocab.RECHECK_CHANGED], 1)
        entry = report["entries"][0]
        self.assertTrue(entry["needs_rereview"])
        decl = entry["decls"][0]
        self.assertEqual(decl["status"], vocab.RECHECK_CHANGED)
        self.assertIn("a <= b", decl["pinned_statement_head"])
        self.assertIn("a < b", decl["current_statement_head"])
        text = recheck_mod.render(report)
        self.assertIn("was:", text)
        self.assertIn("now:", text)

    def test_a_deleted_declaration_is_gone(self):
        self.edit("add_mono", "added_mono", commit="rename")
        report = self.recheck()
        self.assertEqual(report["counts"][vocab.RECHECK_GONE], 1)
        self.assertTrue(report["entries"][0]["needs_rereview"])

    def test_no_verdict_severity_or_resolution_is_altered(self):
        before = self.ledger_rows()[0]
        self.edit("(h : a <= b)", "(h : a < b)", commit="tighten")
        self.recheck()
        after = self.ledger_rows()[0]
        self.assertEqual({k: v for k, v in after.items() if k != "recheck"}, before)
        self.assertEqual(after["recheck"]["status"], vocab.RECHECK_CHANGED)

    def test_the_recheck_block_names_both_commits(self):
        self.edit("(h : a <= b)", "(h : a < b)", commit="tighten")
        self.recheck()
        block = self.ledger_rows()[0]["recheck"]
        self.assertEqual(block["pinned_commit"], self.ledger_rows()[0]["repo_commit"])
        self.assertRegex(block["checked_commit"], r"^[0-9a-f]{40}$")
        self.assertNotEqual(block["pinned_commit"], block["checked_commit"])

    def test_a_second_recheck_rewrites_nothing(self):
        self.edit("(h : a <= b)", "(h : a < b)", commit="tighten")
        self.recheck()
        path = Path(self.ws.paths.accepted_jsonl("ours"))
        first = path.read_bytes()
        self.recheck()
        self.assertEqual(path.read_bytes(), first)

    def test_the_companion_repository_is_not_written_to(self):
        before = {p.relative_to(self.repo): p.stat().st_mtime_ns
                  for p in sorted(self.repo.rglob("*")) if p.is_file()
                  and ".git" not in p.parts}
        self.recheck()
        after = {p.relative_to(self.repo): p.stat().st_mtime_ns
                 for p in sorted(self.repo.rglob("*")) if p.is_file()
                 and ".git" not in p.parts}
        self.assertEqual(after, before)

    def test_a_closed_row_is_not_re_checked(self):
        """A refutation does not expire, so re-checking it would only add noise."""
        self.assertEqual(self.transcribe(verdict="REFUTED")["errors"], [])
        self.assertEqual(self.ledger_rows("accepted"), [])
        self.assertEqual(self.recheck()["counts"]["rows"], 0)

    def test_no_write_leaves_the_ledger_and_the_report_alone(self):
        path = Path(self.ws.paths.accepted_jsonl("ours"))
        before = path.read_bytes()
        report = self.recheck(no_write=True)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(Path(self.ws.paths.recheck_json("ours")).exists())
        self.assertNotIn("files", report)

    def test_the_report_lands_where_paths_says(self):
        report = self.recheck()
        out = Path(self.ws.paths.recheck_json("ours"))
        self.assertTrue(out.exists())
        self.assertEqual(json.loads(out.read_text())["counts"], report["counts"])

    def test_it_says_what_it_compared_against(self):
        self.assertIn("extraction", self.recheck()["compared_against"])
        self.assertEqual(self.recheck(from_stored=True)["compared_against"],
                         "statements.jsonl")


class TestRecheckIsHonestAboutWhatItCannotCheck(WorkspaceCase):
    """`unpinned` is why re-checking a legacy ledger moves no adjudicated number."""

    def test_a_legacy_row_with_no_fingerprint_is_unpinned(self):
        self.corpus()  # no run.yaml, so no pin anywhere
        self.transcribe()
        report = self.recheck()
        self.assertEqual(report["counts"][vocab.RECHECK_UNPINNED], 1)
        self.assertEqual(report["counts"][vocab.RECHECK_UNCHANGED], 0,
                         "an unpinned row must never read as 'still verified'")
        self.assertFalse(report["entries"][0]["needs_rereview"],
                         "unpinned is a gap in the record, not a change in the code")
        self.assertIn("no fingerprint", recheck_mod.render(report))

    def test_a_coverage_finding_names_no_declaration_so_it_is_unpinned(self):
        self.plan_run_yaml()
        self.corpus(decls=(), kind="COVERAGE")
        self.harvest_pin()
        self.transcribe()
        report = self.recheck()
        self.assertEqual(report["counts"][vocab.RECHECK_UNPINNED], 1)

    def test_a_row_whose_pin_file_was_deleted_falls_back_to_its_own_fields(self):
        self.plan_run_yaml()
        self.corpus()
        self.harvest_pin()
        self.transcribe()
        Path(self.ws.paths.pin_json("r1")).unlink()
        self.assertEqual(self.recheck()["counts"][vocab.RECHECK_UNCHANGED], 1)

    def test_from_stored_refuses_when_there_is_no_table(self):
        Path(self.ws.paths.statements_jsonl("ours")).unlink()
        with self.assertRaises(CfError) as caught:
            self.recheck(from_stored=True)
        self.assertIn("cf extract", str(caught.exception.hint))


class TestRecheckVocabularyAndDocumentation(unittest.TestCase):
    """CLAUDE.md S4: one vocabulary, documented in references/vocabulary.md."""

    def test_the_statuses_are_defined_once(self):
        from ledger import compat as ledger_compat

        self.assertEqual(sorted(vocab.RECHECK_STATUSES),
                         sorted(ledger_compat.RECHECK_STATUSES))
        self.assertEqual(set(vocab.RECHECK_STATUSES), set(vocab.RECHECK_STATUS_SET))

    def test_every_status_is_documented(self):
        text = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")
        for status in vocab.RECHECK_STATUSES:
            with self.subTest(status=status):
                self.assertIn("`%s`" % status, text)

    def test_the_command_is_in_the_index(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("`recheck [--companion X]`", text)

    def test_the_new_report_paths_validate_their_id(self):
        paths = WorkspacePaths(Path("/tmp/does-not-matter"))
        self.assertTrue(str(paths.pin_json("r1")).endswith("reports/pin.r1.json"))
        self.assertTrue(
            str(paths.recheck_json("ours")).endswith("reports/recheck.ours.json"))
        for bad in ("../../etc", "/etc/passwd", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(CfError):
                    paths.pin_json(bad)
                with self.assertRaises(CfError):
                    paths.recheck_json(bad)


# ---------------------------------------------------------------------------
# gates-correctness#6 -- the passages named callers that never read the key


class TestThePassagesNameTheCallerThatLinksOnTheKey(unittest.TestCase):
    """Vocabulary §3.1 and `finding_id`'s docstring listed `agreed`,
    adjudication and `cf recheck` as linking on `defect_key`.  None of them
    reads it: `agreed` groups on `(source_id, repo, collapsed item, kind)` plus
    a shared declaration, and everything that addresses ONE observation joins on
    `finding_id`.  Left standing, the passage is licence for a later change to
    re-key `agreed` on the defect key, which would move the pinned agreement
    figures without anyone re-adjudicating a thing.
    """

    #: the callers the retracted sentences named as linking on the key
    NAMED = ("harvest/agreed.py", "cfcmd/adjudicate.py", "ledger/transcribe.py")

    #: the sentences that described behaviour the code does not have
    RETRACTED = (
        "`agreed`, re-adjudication and `cf recheck` all link on it",
        "it is what union, agreed, adjudication and ``cf recheck`` match across runs",
    )

    def flat(self, text: str) -> str:
        """*text* with every run of whitespace collapsed, so a claim can be
        matched whatever line it happens to wrap on."""
        return " ".join(str(text or "").split())

    def read(self, rel: str) -> str:
        return (SKILL / rel).read_text(encoding="utf-8")

    def section(self) -> str:
        body = self.read("references/vocabulary.md").split("### 3.1 ", 1)[1]
        return self.flat(body.split("\n### ", 1)[0])

    def test_none_of_the_callers_it_named_reads_the_key(self):
        for rel in self.NAMED:
            with self.subTest(rel=rel):
                self.assertNotIn("defect_key", self.read("scripts/" + rel))

    def test_union_is_the_one_that_groups_on_it(self):
        self.assertIn('key = (row_source, row["repo"], str(row["defect_key"]))',
                      self.read("scripts/harvest/union.py"))

    def test_the_vocabulary_assigns_each_caller_to_the_key_it_uses(self):
        section = self.section()
        self.assertIn("`union --group-by defect-key` groups on it", section)
        self.assertIn("join on `finding_id`", section)
        for claim in self.RETRACTED:
            self.assertNotIn(claim, section)

    def test_the_docstrings_say_the_same_thing(self):
        for doc in (hashing.finding_id.__doc__, hashing.defect_key.__doc__):
            flat = self.flat(doc)
            for claim in self.RETRACTED:
                self.assertNotIn(claim, flat)
        self.assertIn("``harvest/union.py --group-by defect-key`` is what matches on it "
                      "across runs", self.flat(hashing.finding_id.__doc__))
        self.assertIn("Callers that LINK a defect across runs use this",
                      self.flat(hashing.defect_key.__doc__))
        self.assertIn("``agreed`` groups on the collapsed item and the kind plus a "
                      "shared declaration and reads neither key",
                      self.flat(hashing.defect_key.__doc__))


class TestARowWithNoRunIndependentKeyIsStillReChecked(WorkspaceCase):
    """`cf recheck` joins a row to its pin on `finding_id` and `run_id`, which
    is why it works on a ledger at all: `ledger/transcribe.py` stamps no
    `defect_key`, so a re-check keyed on that would find nothing to check."""

    def setUp(self):
        super().setUp()
        self.plan_run_yaml()
        self.corpus()
        self.harvest_pin()
        self.assertEqual(self.transcribe()["errors"], [])

    def test_the_transcribed_row_carries_no_defect_key(self):
        row = self.ledger_rows()[0]
        self.assertEqual(str(row.get("defect_key") or ""), "")
        self.assertTrue(row["finding_id"])

    def test_the_re_check_still_classifies_it_against_its_pin(self):
        report = self.recheck()
        entry = report["entries"][0]
        self.assertEqual(entry["defect_key"], "")
        self.assertEqual(entry["finding_id"], self.ledger_rows()[0]["finding_id"])
        self.assertEqual(entry["status"], vocab.RECHECK_UNCHANGED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
