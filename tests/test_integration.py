"""End-to-end integration tests: the parts have to fit, not just pass alone.

Each stage of this skill was written separately, so the failures that survive
unit testing are all *interface* failures -- one stage writing a shape the next
one does not read.  These tests pin the joins that actually broke during
integration:

* every ``cfcmd`` module imports and registers (a module that raises is
  advertised as ``(unavailable: ...)`` and silently does nothing);
* ``pairs/queue.json`` and ``pairs/targets.<name>.json`` are wrappers
  (``{created, count, ..., items: [...]}``), and ``plan`` iterated them as if
  they were bare lists -- "string indices must be integers";
* ``cf import-corpus`` lifts a v1 findings directory into corpus/<run-id> as v2
  with a receipt, and never writes to the source;
* the handoff's run table reads ``run.yaml``'s real nested shape
  (``host: {name: ...}``, ``tasks: {count: ...}``, ``book: {access: ...}``),
  not a flat string.

The baseline-backed cases skip cleanly when CF_BASELINE_CORPUS is unset.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                          # noqa: E402
from cfcmd import plan_run                                    # noqa: E402
from cflib.errors import CfError                              # noqa: E402
from plan import make_tasks as mt                             # noqa: E402
from cflib.paths import WorkspacePaths                        # noqa: E402
from report import make_handoff                               # noqa: E402

BASELINE_CORPUS = os.environ.get("CF_BASELINE_CORPUS")

#: the two Tao editions, by the ids registry/sources.yaml gives them
PUBLISHED_ID = "tao-measure-gsm126-published"
DRAFT_ID = "tao-measure-2011-draft"
PUBLISHED_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
DRAFT_SHA = "62d95658" + "0" * 56


def write_workspace(root: Path, *, companions: str = "", sources: str = "",
                    extra: str = "") -> Path:
    """A valid v2 workspace.yaml on disk, for fixtures `cf init` cannot build.

    `cf init` writes an EMPTY workspace by design (no flags: R2), so a test that
    needs a source already pinned writes one here rather than inventing flags
    the CLI does not have.
    """
    root.mkdir(parents=True, exist_ok=True)
    text = ("version: 2\n"
            f"workspace:\n  name: {root.name}\n"
            + ("companions:\n" + companions if companions else "companions: {}\n")
            + ("sources:\n" + sources if sources else "sources: {}\n")
            + extra)
    path = root / "workspace.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def tao_source(source_id: str = PUBLISHED_ID, sha: str = PUBLISHED_SHA,
               edition: str = "AMS GSM 126 (published pagination)",
               role: str = "audited") -> str:
    return (f"  {source_id}:\n"
            f'    sha256: "{sha}"\n'
            "    title: An Introduction to Measure Theory\n"
            f'    edition: "{edition}"\n'
            "    offset: 16\n"
            "    chapters: [1]\n"
            f"    role: {role}\n")


class TestCommandDiscovery(unittest.TestCase):
    """Every command module must import; a broken one is invisible, not loud."""

    def test_no_command_is_unavailable(self):
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        mapping = cf_main.discover_commands(subparsers)
        # `mod is None` could never be true: discover_commands registers a
        # _BrokenCommand STUB for a module that fails to import, so this test
        # passed unchanged with a syntactically broken cfcmd module in the tree.
        broken = {name: mod.exc for name, mod in mapping.items()
                  if isinstance(mod, cf_main._BrokenCommand)}
        self.assertEqual(broken, {}, f"command modules failed to register: {broken}")

    def test_the_documented_commands_are_all_registered(self):
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        mapping = cf_main.discover_commands(subparsers)
        # the command index of SKILL.md S"Command index". `book` is deleted: a
        # source is discovered by `cf onboard` and re-prepared by
        # `cf sources refresh` (docs/design/multi-source-model.md S7).
        for name in ("init", "onboard", "sources", "extract", "pair", "plan-run", "run",
                     "harvest", "import-corpus", "tally", "detect-proxies",
                     "adjudicate", "route", "report", "status"):
            self.assertIn(name, mapping)
        self.assertNotIn("book", mapping)

    def test_every_documented_sources_subcommand_exists(self):
        """SKILL.md's command index names `sources <sub>` rows; each must parse."""
        import argparse
        import re

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        cf_main.discover_commands(subparsers)
        documented = set(re.findall(r"^\| `sources (\w+)",
                                    (HERE.parent / "SKILL.md").read_text(), re.M))
        self.assertIn("remove", documented, "cf sources remove is undocumented")
        extra = {"show": ["x"], "remove": ["x"], "set": ["x", "y=1"],
                 "add": ["x.pdf", "--for", "ours"]}
        for sub in sorted(documented):
            with self.subTest(sub=sub):
                args = parser.parse_args(["sources", sub, *extra.get(sub, [])])
                self.assertEqual(getattr(args, "sources_command"), sub)


class TestPairsArtifactsAreWrappers(unittest.TestCase):
    """``cf pair`` writes {..., items: [...]}; planning must unwrap it."""

    QUEUE = {"created": "x", "source_id": PUBLISHED_ID, "source_sha": PUBLISHED_SHA,
             "offset": 16, "count": 2,
             "items": [{"item": "Definition 1.1.1", "repos": ["ours"], "book_page": 5},
                       {"item": "Exercise 1.1.2", "repos": ["ours", "adam"], "book_page": 6}]}
    TARGETS = {"created": "x", "companion": "ours", "count": 1,
               "items": [{"item": "Definition 1.1.1", "decls": ["A.b"], "peers": [],
                          "single_source": True, "priority": "max", "in_book": True}]}

    def test_queue_rows_unwraps_both_shapes(self):
        self.assertEqual(len(plan_run._queue_rows(self.QUEUE)), 2)
        self.assertEqual(len(plan_run._queue_rows(self.QUEUE["items"])), 2)
        self.assertEqual(plan_run._queue_rows({}), [])
        self.assertEqual(plan_run._queue_rows(None), [])

    def test_len_of_the_wrapper_is_not_the_task_count(self):
        # the bug this guards: len({...10 keys...}) was used as `expected`
        self.assertNotEqual(len(self.TARGETS), len(plan_run._queue_rows(self.TARGETS)))
        self.assertEqual(len(plan_run._queue_rows(self.TARGETS)), 1)

    def test_entry_builders_accept_the_wrapper(self):
        kinds = ("Definition", "Exercise")
        entries = mt.differential_entries(
            self.QUEUE, {"ours": self.TARGETS}, source_id=PUBLISHED_ID, kinds=kinds)
        self.assertEqual([e.item for e in entries],
                         ["Definition 1.1.1", "Exercise 1.1.2"])
        self.assertEqual({e.source_id for e in entries}, {PUBLISHED_ID})
        targets = mt.target_entries(self.TARGETS, "ours", source_id=PUBLISHED_ID)
        self.assertEqual([e.item for e in targets], ["Definition 1.1.1"])
        self.assertEqual(targets[0].qualified, f"{PUBLISHED_ID}::Definition 1.1.1")


class TestHandoffRunTable(unittest.TestCase):
    """The run table reads plan-run's nested run.yaml, not a flattened one."""

    RUN_YAML = {
        "mode": "attack", "model": "claude-sonnet-5",
        "host": {"name": "ffmain", "kind": "remote", "remote_root": "/home/x", "ssh": "x@y"},
        "sources": [{"id": PUBLISHED_ID, "sha256": PUBLISHED_SHA, "offset": 16}],
        "source_access": "text+images",
        "tasks": {"count": 519, "universe": "targets.ours.json", "expected": 519},
    }

    def test_nested_fields_render_as_values(self):
        table = make_handoff.render_runs_table({"r1": self.RUN_YAML}, [])
        self.assertIn("ffmain", table)
        self.assertIn("text+images", table)
        self.assertIn("519", table)
        self.assertNotIn("{'name'", table)      # a dict repr in the report
        self.assertNotIn("'count':", table)

    def test_a_flat_run_yaml_still_works(self):
        table = make_handoff.render_runs_table(
            {"r1": {"host": "ffmain", "source_access": "text", "tasks": 12}}, [])
        self.assertIn("ffmain", table)
        self.assertIn("text", table)
        self.assertIn("12", table)

    def test_an_imported_run_has_no_run_yaml_and_says_so(self):
        table = make_handoff.render_runs_table({}, [{
            "file": "tally.all.json",
            "runs": {"findings-hunt": {"mode": "attack", "files": 763}}}])
        self.assertIn("findings-hunt", table)
        self.assertIn("763", table)
        self.assertIn("TODO(", table)           # never invent a model or a host

    # -- the receipt join ---------------------------------------------------
    # An imported corpus has no run.yaml, but its RECEIPT.json records the
    # edition every document was judged against.  The table printed
    # TODO(source) next to a fact sitting two directories away.
    IMPORT_RECEIPT = {
        "run_id": "batch1",
        "sources": {PUBLISHED_ID: PUBLISHED_SHA},
        "source": {"kind": "import", "mode": "differential", "preset": "differential-text"},
        "actual": {"count": 382},
    }

    def test_an_imported_run_names_the_source_its_receipt_records(self):
        table = make_handoff.render_runs_table(
            {}, [{"file": "t.json", "runs": {"batch1": {"mode": "differential",
                                                        "files": 382}}}],
            {"batch1": self.IMPORT_RECEIPT})
        self.assertIn("`%s`" % PUBLISHED_ID, table)
        self.assertNotIn("TODO(source)", table)

    def test_an_unplanned_run_does_not_point_at_a_command_that_cannot_help(self):
        """`cf plan-run` will never fill in a historical run's model."""
        table = make_handoff.render_runs_table(
            {}, [], {"batch1": self.IMPORT_RECEIPT})
        self.assertNotIn("TODO(model)", table)
        self.assertNotIn("TODO(tasks)", table)
        self.assertIn("--", table)
        # the count still comes off the receipt, not out of the air
        self.assertIn("382", table)

    def test_a_planned_run_still_shows_its_holes(self):
        """The `--` is for unplanned runs only; a planned run keeps its TODOs."""
        table = make_handoff.render_runs_table({"r1": {"mode": "attack"}}, [])
        self.assertIn("TODO(model)", table)
        self.assertIn("TODO(tasks)", table)

    def test_a_legacy_list_shaped_receipt_is_still_read(self):
        """`cf pair` wrote `sources` as a bare list of ids before it was a map."""
        table = make_handoff.render_runs_table(
            {}, [], {"anchor-check": {"run_id": "anchor-check",
                                      "sources": [PUBLISHED_ID],
                                      "actual": {"count": 12}}})
        self.assertIn("`%s`" % PUBLISHED_ID, table)
        self.assertNotIn("TODO(source)", table)

    def test_load_receipts_reads_the_corpus_directory(self):
        tmp = Path(tempfile.mkdtemp(prefix="cf-receipts-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        run = tmp / "corpus" / "batch1"
        run.mkdir(parents=True)
        (run / "RECEIPT.json").write_text(json.dumps(self.IMPORT_RECEIPT))
        (tmp / "corpus" / "no-receipt").mkdir()
        found = make_handoff.load_receipts(WorkspacePaths(tmp))
        self.assertEqual(sorted(found), ["batch1"])
        self.assertEqual(found["batch1"]["sources"], {PUBLISHED_ID: PUBLISHED_SHA})


class TestMechanicalReceiptShape(unittest.TestCase):
    """`cf pair` and `cf detect-proxies` write the same receipt shape as import.

    A bare list of source ids loses the sha, which is the half that says which
    EDITION the documents were judged against.
    """

    def test_write_corpus_records_the_sha_per_source(self):
        from detect import corpus as detect_corpus

        tmp = Path(tempfile.mkdtemp(prefix="cf-mech-receipt-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        docs = [
            {"item": "Definition 1.1.1", "source_id": PUBLISHED_ID,
             "source_sha": PUBLISHED_SHA, "run_id": "r"},
            {"item": "Definition 1.1.1", "source_id": DRAFT_ID,
             "source_sha": DRAFT_SHA, "run_id": "r"},
        ]
        receipt = detect_corpus.write_corpus(docs, tmp, run_id="r", companion="ours")
        self.assertEqual(receipt["sources"],
                         {PUBLISHED_ID: PUBLISHED_SHA, DRAFT_ID: DRAFT_SHA})

    def test_an_empty_emission_still_names_the_source_it_was_asked_for(self):
        from detect import corpus as detect_corpus

        tmp = Path(tempfile.mkdtemp(prefix="cf-mech-empty-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        receipt = detect_corpus.write_corpus(
            [], tmp, run_id="r", companion="ours",
            source_id=PUBLISHED_ID, source_sha=PUBLISHED_SHA)
        self.assertEqual(receipt["sources"], {PUBLISHED_ID: PUBLISHED_SHA})


class TestStatusSourceColumn(unittest.TestCase):
    """`cf status` must not contradict its own source table.

    The companion row counted the sources the last EXTRACT touched, so a source
    just bound by `cf sources add` was listed in the table above (companions:
    this one) and absent from the row below (sources: 1). The row now says what
    the companion is BOUND to, and names the gap.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-status-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = self.tmp / "ws"
        repo = self.tmp / "repo"
        repo.mkdir(parents=True)
        write_workspace(
            self.ws,
            companions=(f"  ours:\n    repo: {repo}\n    adapter: docstring_tags\n"
                        f"    sources: [{PUBLISHED_ID}, {DRAFT_ID}]\n"
                        f"    anchor_source: {PUBLISHED_ID}\n"),
            sources=tao_source() + tao_source(DRAFT_ID, DRAFT_SHA, "2011 draft"))
        # state.json remembers only the source the last extract read
        (self.ws / "companions" / "ours").mkdir(parents=True)
        (self.ws / "companions" / "ours" / "state.json").write_text(json.dumps({
            "name": "ours", "anchor_source": PUBLISHED_ID,
            "sources": {PUBLISHED_ID: {"sha": PUBLISHED_SHA, "rows": 3, "items": 3}},
            "extract": {"rows": 3, "items": 3},
        }), encoding="utf-8")

    def _status(self) -> str:
        """stdout AND stderr: the table is printed, the warnings are logged."""
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            cf_main.main(["status", "--workspace", str(self.ws)])
        return buf.getvalue()

    def test_the_row_counts_what_the_companion_is_bound_to(self):
        out = self._status()
        self.assertIn("2 (1 extracted)", out)
        self.assertIn(f"bound to {DRAFT_ID} but no rows extracted", out)

    def test_no_annotation_when_the_two_agree(self):
        state = self.ws / "companions" / "ours" / "state.json"
        data = json.loads(state.read_text())
        data["sources"][DRAFT_ID] = {"sha": DRAFT_SHA, "rows": 1, "items": 1}
        state.write_text(json.dumps(data), encoding="utf-8")
        out = self._status()
        self.assertNotIn("extracted)", out)
        self.assertNotIn("no rows extracted", out)


class TestImportCorpus(unittest.TestCase):
    """`cf import-corpus` adopts a foreign findings directory, read-only."""

    V1_DOC = {
        "item": "Exercise 1.1.1",
        "book_located": True,
        "book_passage_quote": "",
        "matches": [],
        "disagreements": [],
        "coverage_notes": "",
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-integration-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "ws"
        self.source = self.tmp / "v1-findings"
        self.source.mkdir(parents=True)
        for name in ("exercise-1-1-1.json", "exercise-1-1-2.json"):
            (self.source / name).write_text(json.dumps(self.V1_DOC), encoding="utf-8")
        # `cf init` takes no flags (R2), so the source is pinned by writing the
        # workspace the way `cf onboard` would have.
        self._cf("init", str(self.workspace), "--no-git", "-q")
        write_workspace(self.workspace, sources=tao_source())

    def _cf(self, *argv) -> int:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cf_main.main(list(argv))
        self.out = buf.getvalue()
        return rc

    def test_import_converts_receipts_and_leaves_the_source_alone(self):
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes())
                  for p in self.source.glob("*.json")}
        rc = self._cf("import-corpus", "--run-id", "r1", "--from", str(self.source),
                      "--workspace", str(self.workspace), "--no-tally", "-q")
        self.assertEqual(rc, 0)

        dest = self.workspace / "corpus" / "r1"
        receipt = json.loads((dest / "RECEIPT.json").read_text())
        self.assertTrue(receipt["complete"])
        self.assertEqual(receipt["expected"]["count"], 2)
        self.assertEqual(receipt["actual"]["count"], 2)
        self.assertEqual(receipt["missing"], [])
        self.assertEqual(receipt["source"]["kind"], "import")
        # the receipt records which source the adopted corpus is about
        self.assertEqual(receipt["sources"], {PUBLISHED_ID: PUBLISHED_SHA})

        # A v1 document records no source at all; the importer stamps the
        # workspace's, and recomputes the five-part ids from it.
        doc = json.loads((dest / "exercise-1-1-1.json").read_text())
        self.assertEqual(doc["schema_version"], 2)
        self.assertEqual(doc["run_id"], "r1")
        self.assertEqual(doc["source_id"], PUBLISHED_ID)
        self.assertEqual(doc["source_sha"], PUBLISHED_SHA)
        self.assertNotIn("book_sha", doc)

        after = {p.name: (p.stat().st_mtime_ns, p.read_bytes())
                 for p in self.source.glob("*.json")}
        self.assertEqual(before, after, "the source corpus was modified")

    def test_a_two_source_workspace_makes_the_importer_ask_which(self):
        """A v1 corpus says nothing about its edition, so nobody may guess."""
        write_workspace(self.workspace,
                        sources=tao_source() + tao_source(
                            DRAFT_ID, DRAFT_SHA, "2011-01-24 draft", "reference"))
        with self.assertRaises(CfError) as ctx:
            self._cf("import-corpus", "--run-id", "amb", "--from", str(self.source),
                     "--workspace", str(self.workspace), "--no-tally", "-q")
        self.assertIn("--source", str(ctx.exception) +
                      str(getattr(ctx.exception, "hint", "")))
        self.assertEqual(
            self._cf("import-corpus", "--run-id", "amb", "--from", str(self.source),
                     "--source", DRAFT_ID, "--workspace", str(self.workspace),
                     "--no-tally", "-q"), 0)
        doc = json.loads(
            (self.workspace / "corpus" / "amb" / "exercise-1-1-1.json").read_text())
        self.assertEqual(doc["source_id"], DRAFT_ID)
        self.assertEqual(doc["source_sha"], DRAFT_SHA)

    def test_import_is_idempotent(self):
        args = ("import-corpus", "--run-id", "r1", "--from", str(self.source),
                "--workspace", str(self.workspace), "--no-tally", "-q", "--force")
        self._cf(*args)
        first = (self.workspace / "corpus" / "r1" / "exercise-1-1-1.json").read_bytes()
        self._cf(*args)
        second = (self.workspace / "corpus" / "r1" / "exercise-1-1-1.json").read_bytes()
        self.assertEqual(first, second)

    def test_import_refuses_an_empty_or_missing_directory(self):
        # cf.main() RAISES for a library caller; the CLI wrapper in cf.py's
        # __main__ block is what turns a CfError into exit 2.
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaises(CfError):
            self._cf("import-corpus", "--run-id", "r2", "--from", str(empty),
                     "--workspace", str(self.workspace), "-q")
        with self.assertRaises(CfError):
            self._cf("import-corpus", "--run-id", "r3", "--from", str(self.tmp / "nope"),
                     "--workspace", str(self.workspace), "-q")
        self.assertFalse((self.workspace / "corpus" / "r2").exists())

    def test_importing_another_edition_needs_the_override(self):
        """SKILL.md hard rule 3, at the one place a foreign edition enters."""
        args = ("import-corpus", "--run-id", "draft", "--from", str(self.source),
                "--workspace", str(self.workspace), "--no-tally", "-q", "--force",
                "--source-sha", DRAFT_SHA)
        with self.assertRaises(CfError) as ctx:
            self._cf(*args)
        self.assertIn("another edition", getattr(ctx.exception, "hint", ""))
        self.assertEqual(self._cf(*args, "--allow-edition-mismatch"), 0)
        receipt = json.loads(
            (self.workspace / "corpus" / "draft" / "RECEIPT.json").read_text())
        self.assertEqual(receipt["sources"], {PUBLISHED_ID: DRAFT_SHA})
        self.assertTrue(receipt["edition"]["allowed_mismatch"])
        self.assertTrue(receipt["complete"])

    def test_import_gate_validates_what_landed(self):
        gate = self.tmp / "gate.local.json"
        book = self.tmp / "book.txt"
        book.write_text("nothing in here matches any quote\n", encoding="utf-8")
        gate.write_text(json.dumps({
            "schema_version": 2, "preset": "differential-text", "mode": "differential",
            "run_id": "g1",
            "sources": {PUBLISHED_ID: {"text": str(book), "sha256": PUBLISHED_SHA,
                                       "page_offset": 16}},
            "repos": ["ours"], "kinds": ["CONTENT"], "verdicts": ["AGREE"],
            "witness_required": False, "require_book_located": True,
            "require_source_sha": True, "findings_dir": ".", "kind_rules": {},
        }), encoding="utf-8")
        rc = self._cf("import-corpus", "--run-id", "g1", "--from", str(self.source),
                      "--workspace", str(self.workspace), "--no-tally", "--force",
                      "--gate", "--gate-config", str(gate))
        self.assertEqual(rc, 1, "a corpus that fails the gate must not be complete")
        receipt = json.loads((self.workspace / "corpus" / "g1" / "RECEIPT.json").read_text())
        self.assertFalse(receipt["complete"])
        self.assertFalse(receipt["gate"]["ok"])
        self.assertTrue(receipt["gate"]["problems"])

    def test_the_gate_needs_no_planned_run(self):
        """`--gate` used to be unreachable for the corpora it exists to check.

        The two presets written for imported v1 corpora are gate-only, so
        `cf plan-run` refuses to write a gate.json for them, and `--gate`
        defaulted to `runs/<id>/gate.local.json` -- a file an imported run never
        has.  The importer knows the preset, the source and the repos, so it
        builds the config and leaves it next to the corpus it judged.
        """
        sentence = ("Show that the union of two elementary sets is elementary, "
                    "and that the intersection is elementary as well.")
        text = self.workspace / "sources" / PUBLISHED_ID / "text.txt"
        text.parent.mkdir(parents=True, exist_ok=True)
        text.write_text("Exercise 1.1.1 (Boolean closure). " + sentence + "\n",
                        encoding="utf-8")
        for name in ("exercise-1-1-1.json", "exercise-1-1-2.json"):
            (self.source / name).write_text(
                json.dumps(dict(self.V1_DOC, book_passage_quote=sentence)),
                encoding="utf-8")
        rc = self._cf("import-corpus", "--run-id", "leg", "--from", str(self.source),
                      "--workspace", str(self.workspace), "--no-tally",
                      "--preset", "differential-legacy", "--repos", "ours,teorth",
                      "--gate")
        self.assertEqual(rc, 0, self.out)
        dest = self.workspace / "corpus" / "leg"
        receipt = json.loads((dest / "RECEIPT.json").read_text())
        self.assertTrue(receipt["gate"]["ok"], receipt["gate"]["problems"])
        self.assertEqual(receipt["gate"]["preset"], "differential-legacy")
        self.assertTrue(receipt["complete"])

        # the config it used is on disk, named by the receipt, and re-runnable
        built = json.loads((dest / "gate.local.json").read_text())
        self.assertEqual(receipt["gate"]["config"], str(dest / "gate.local.json"))
        self.assertEqual(built["preset"], "differential-legacy")
        self.assertEqual(built["sources"][PUBLISHED_ID]["text"], str(text))
        self.assertEqual(built["sources"][PUBLISHED_ID]["sha256"], PUBLISHED_SHA)
        # the repos of the CORPUS, not of the workspace: a baseline run's
        # ours/teorth/adam are not this workspace's companions, and gating
        # against the workspace's names would reject every row.
        self.assertEqual(built["repos"], ["ours", "teorth"])
        # and it is not mistaken for a finding
        self.assertEqual(receipt["actual"]["count"], 2)

    def test_the_gate_says_what_it_needs_when_it_cannot_build_one(self):
        """No preset and no config: name both ways out, do not gate nothing."""
        with self.assertRaises(CfError) as ctx:
            self._cf("import-corpus", "--run-id", "nop", "--from", str(self.source),
                     "--workspace", str(self.workspace), "--no-tally",
                     "--preset", "", "--gate")
        said = str(ctx.exception) + str(getattr(ctx.exception, "hint", ""))
        self.assertIn("--preset", said)
        self.assertIn("--gate-config", said)

    V1_DEFECT_DOC = {
        "item": "In-text, book p. 71",
        "book_located": True,
        "book_passage_quote": "",
        "matches": [],
        "disagreements": [{"concept": "c", "kind": "CONTENT", "severity": "medium",
                           "repos_involved": ["ours"], "faithful": "none",
                           "unfaithful_decls": ["ours:Riemann.foo"],
                           "book_quote": "q", "reasoning": "r"}],
        "coverage_notes": "",
    }

    def _with_a_companion(self) -> Path:
        """A workspace that has a companion, and a v1 file naming `ours`."""
        write_workspace(
            self.workspace, sources=tao_source(),
            companions=("  tml-prefix:\n"
                        f"    repo: {self.tmp}\n"
                        "    adapter: docstring_tags\n"
                        f"    sources: [{PUBLISHED_ID}]\n"
                        f"    anchor_source: {PUBLISHED_ID}\n"))
        src = self.tmp / "v1-defect"
        src.mkdir(exist_ok=True)
        (src / "in-text-book-p-71.json").write_text(
            json.dumps(self.V1_DEFECT_DOC), encoding="utf-8")
        return src

    def test_a_foreign_repo_name_is_warned_about_not_silently_adopted(self):
        """The union key holds the repo: an unmapped import joins nothing here."""
        src = self._with_a_companion()
        err = io.StringIO()
        with redirect_stderr(err):                      # log.warn is a diagnostic
            rc = self._cf("import-corpus", "--run-id", "v1", "--from", str(src),
                          "--workspace", str(self.workspace), "--no-tally")
        self.assertEqual(rc, 0)
        self.assertIn("this workspace does not have: ours", err.getvalue())
        self.assertIn("--repo-map", err.getvalue())
        receipt = json.loads(
            (self.workspace / "corpus" / "v1" / "RECEIPT.json").read_text())
        self.assertEqual(receipt["source"]["repos"], ["ours"])
        self.assertEqual(receipt["source"]["repos_not_in_workspace"], ["ours"])
        row = json.loads((self.workspace / "corpus" / "v1"
                          / "in-text-book-p-71.json").read_text())["disagreements"][0]
        self.assertEqual(row["unfaithful_decls"], ["ours:Riemann.foo"])

    def test_the_repo_map_makes_an_imported_run_this_workspace_s_own(self):
        src = self._with_a_companion()
        err = io.StringIO()
        with redirect_stderr(err):
            rc = self._cf("import-corpus", "--run-id", "v1", "--from", str(src),
                          "--workspace", str(self.workspace), "--no-tally",
                          "--repo-map", "ours=tml-prefix")
        self.assertEqual(rc, 0)
        self.assertNotIn("this workspace does not have", err.getvalue())
        receipt = json.loads(
            (self.workspace / "corpus" / "v1" / "RECEIPT.json").read_text())
        self.assertEqual(receipt["source"]["repos"], ["tml-prefix"])
        self.assertEqual(receipt["source"]["repo_map"], {"ours": "tml-prefix"})
        self.assertEqual(receipt["source"]["repos_not_in_workspace"], [])
        doc = json.loads((self.workspace / "corpus" / "v1"
                          / "in-text-book-p-71.json").read_text())
        self.assertEqual(doc["repo_map"], {"ours": "tml-prefix"})
        self.assertEqual(doc["disagreements"][0]["unfaithful_decls"],
                         ["tml-prefix:Riemann.foo"])
        self.assertEqual(doc["disagreements"][0]["unfaithful_decls_v1"],
                         ["ours:Riemann.foo"])

    def test_a_map_onto_a_companion_nobody_has_is_refused(self):
        src = self._with_a_companion()
        with self.assertRaises(CfError) as ctx:
            self._cf("import-corpus", "--run-id", "v1", "--from", str(src),
                     "--workspace", str(self.workspace), "--no-tally",
                     "--repo-map", "ours=nobody")
        self.assertIn("nobody", str(ctx.exception))
        with self.assertRaises(CfError):
            self._cf("import-corpus", "--run-id", "v1", "--from", str(src),
                     "--workspace", str(self.workspace), "--no-tally",
                     "--repo-map", "ours")          # not <old>=<new>
        # and renaming a repo inside a verbatim copy would invalidate its id
        with self.assertRaises(CfError) as ctx:
            self._cf("import-corpus", "--run-id", "v1", "--from", str(src),
                     "--workspace", str(self.workspace), "--no-tally",
                     "--no-convert", "--repo-map", "ours=tml-prefix")
        self.assertIn("--no-convert", str(ctx.exception))

    def test_the_mode_is_guessed_from_the_baseline_directory_names(self):
        from cfcmd import import_corpus

        class A:
            mode = None
            preset = None
        self.assertEqual(import_corpus._mode_and_preset(A, Path("findings-hunt")),
                         ("attack", "attack-hunt"))
        self.assertEqual(import_corpus._mode_and_preset(A, Path("findings-pdf")),
                         ("differential", "differential-pdf"))
        self.assertEqual(import_corpus._mode_and_preset(A, Path("whatever"))[0],
                         "differential")

    @unittest.skipUnless(BASELINE_CORPUS, "CF_BASELINE_CORPUS unset")
    def test_the_real_baseline_run_imports_whole(self):
        src = Path(BASELINE_CORPUS) / "findings-hunt"
        if not src.is_dir():
            self.skipTest(f"{src} missing")
        rc = self._cf("import-corpus", "--run-id", "findings-hunt", "--from", str(src),
                      "--repos", "ours,teorth,adam", "--workspace", str(self.workspace),
                      "--no-tally", "-q")
        self.assertEqual(rc, 0)
        receipt = json.loads(
            (self.workspace / "corpus" / "findings-hunt" / "RECEIPT.json").read_text())
        self.assertEqual(receipt["expected"]["count"], 763)
        self.assertEqual(receipt["actual"]["count"], 763)
        self.assertTrue(receipt["complete"])


class TestHarvestSafety(unittest.TestCase):
    """`cf harvest --from host:/path` must obey the same rules as `cf run`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-harvest-safety-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "ws"
        self._cf("init", str(self.workspace), "--no-git", "-q")

    def _cf(self, *argv) -> int:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cf_main.main(list(argv))
        self.out = buf.getvalue()
        return rc

    def _allow(self, alias: str, ssh: str) -> None:
        from cflib.yamlio import read_yaml, write_yaml

        path = self.workspace / "workspace.yaml"
        data = read_yaml(path)
        data.setdefault("hosts", {})["allowlist"] = [alias]
        data["hosts"][alias] = {"ssh": ssh, "remote_root": "/home/x"}
        write_yaml(path, data)

    def test_an_unlisted_host_is_refused_before_anything_runs(self):
        with self.assertRaises(CfError) as ctx:
            self._cf("harvest", "r1", "--from", "not-allowlisted.example.net:/findings",
                     "--execute", "--i-have-consent", "--workspace", str(self.workspace), "-q")
        self.assertIn("allowlist", str(ctx.exception))

    def test_an_allowlisted_host_still_needs_consent(self):
        self._allow("ffmain", "ffmain")
        with self.assertRaises(CfError) as ctx:
            self._cf("harvest", "r1", "--from", "ffmain:/findings", "--execute",
                     "--workspace", str(self.workspace), "-q")
        self.assertIn("consent", str(ctx.exception))

    def test_a_local_source_needs_neither(self):
        source = self.tmp / "findings"
        source.mkdir()
        (source / "exercise-1-1-1.json").write_text(json.dumps({
            "schema_version": 2, "run_id": "r1", "source_id": PUBLISHED_ID,
            "source_sha": PUBLISHED_SHA,
            "item": "Exercise 1.1.1", "book_located": True, "book_passage_quote": "",
            "matches": [], "disagreements": [], "coverage_notes": "",
        }), encoding="utf-8")
        self.assertEqual(
            self._cf("harvest", "r1", "--from", str(source), "--no-tally",
                     "--workspace", str(self.workspace), "-q"), 0)


class TestExtractSurvivesOneBrokenCompanion(unittest.TestCase):
    """One unparseable adapter.yaml must not take the whole stage down."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-extract-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "ws"
        self.repo = self.tmp / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "A.lean").write_text(
            "/-- `Exercise 1.1.1` (a tagged declaration). -/\n"
            "theorem foo : True := trivial\n", encoding="utf-8")
        for argv in (["init"], ["add", "-A"],
                     ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "x"]):
            subprocess.run(["git", "-C", str(self.repo), *argv],
                           capture_output=True, check=False)
        self._cf("init", str(self.workspace), "--no-git", "-q")

    def _cf(self, *argv) -> int:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cf_main.main(list(argv))
        self.out = buf.getvalue()
        return rc

    def _adapter(self, name: str, *, kind: str = "docstring_tags",
                 glob: str = "src/**/*.lean", anchor: str = "docstring",
                 granularity: str = "item") -> None:
        """The adapter.yaml `cf onboard` writes, without running discovery.

        This test is about `cf extract` surviving one broken companion, so the
        workspace is built directly: onboarding the toy repo would exercise
        discovery, which is a different package's subject.
        """
        d = self.workspace / "companions" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "adapter.yaml").write_text(
            f"name: {name}\nrepo: {self.repo}\nadapter: {kind}\n"
            f"glob: \"{glob}\"\nanchor: {anchor}\ngranularity: {granularity}\n"
            f"write_policy: read_only\nsources:\n  default: {PUBLISHED_ID}\n  map: []\n",
            encoding="utf-8")

    def _break(self, name: str) -> None:
        """An adapter.yaml `load_adapter` cannot make sense of.

        workspace.yaml's own validation refuses an unimplemented adapter kind
        outright, so the broken companion is declared correctly THERE and its
        adapter.yaml names a kind nothing implements -- which is what a
        hand-edited file actually looks like when it goes wrong.
        """
        self._adapter(name, kind="no_such_adapter_kind")

    def test_the_healthy_companion_is_still_extracted_and_the_command_fails(self):
        write_workspace(
            self.workspace,
            companions=(f"  ours:\n    repo: {self.repo}\n    adapter: docstring_tags\n"
                        f"    sources: [{PUBLISHED_ID}]\n"
                        f"    anchor_source: {PUBLISHED_ID}\n"
                        f"  broken:\n    repo: {self.repo}\n    adapter: docstring_tags\n"
                        f"    sources: [{PUBLISHED_ID}]\n"
                        f"    anchor_source: {PUBLISHED_ID}\n"),
            sources=tao_source())
        self._adapter("ours")
        self._break("broken")
        rc = self._cf("extract", "--workspace", str(self.workspace))
        self.assertEqual(rc, 1, self.out)
        self.assertIn("broken", self.out)
        table = self.workspace / "companions" / "ours" / "statements.jsonl"
        self.assertTrue(table.exists(), "a broken peer stopped the healthy companion")
        rows = [json.loads(l) for l in table.read_text().splitlines()
                if l.strip() and not l.startswith('{"_meta"')]
        self.assertTrue(rows)
        # and the rows the healthy companion wrote say which source they are about
        for row in rows:
            self.assertEqual(row.get("source_id"), PUBLISHED_ID)
            self.assertEqual(row.get("source_sha"), PUBLISHED_SHA)


class TestInitGitGuard(unittest.TestCase):
    """The "workspace repos have no remote" invariant must actually be established."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-init-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _git(self, *argv, cwd=None):
        return subprocess.run(["git", *argv], cwd=str(cwd or self.tmp),
                              capture_output=True, text=True, check=False)

    def _cf(self, *argv) -> int:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cf_main.main(list(argv))
        self.out = buf.getvalue()
        return rc

    def _init_args(self, target):
        return ["init", str(target), "-q"]

    def test_a_fresh_directory_gets_its_own_repo_and_the_hook(self):
        target = self.tmp / "ws"
        self.assertEqual(self._cf(*self._init_args(target)), 0)
        self.assertTrue((target / ".git").is_dir())
        hook = target / ".git" / "hooks" / "pre-push"
        self.assertTrue(hook.exists())
        self.assertIn("CF_ALLOW_PUSH", hook.read_text())
        self.assertEqual(self._git("remote", cwd=target).stdout.strip(), "")

    def test_nested_inside_a_repo_with_a_remote_is_refused_outright(self):
        outer = self.tmp / "outer"
        outer.mkdir()
        self._git("init", "-q", cwd=outer)
        self._git("remote", "add", "origin", "git@github.com:example/public.git", cwd=outer)
        target = outer / "ws"
        with self.assertRaises(CfError) as ctx:
            self._cf(*self._init_args(target))
        self.assertIn("has a remote", str(ctx.exception))
        self.assertFalse(target.exists(), "nothing may be written before the refusal")
        # --allow-nested does NOT override a remote
        with self.assertRaises(CfError):
            self._cf(*self._init_args(target), "--allow-nested")
        self.assertFalse(target.exists())

    def test_nested_inside_a_remoteless_repo_is_refused_without_allow_nested(self):
        outer = self.tmp / "inner"
        outer.mkdir()
        self._git("init", "-q", cwd=outer)
        target = outer / "ws"
        with self.assertRaises(CfError) as ctx:
            self._cf(*self._init_args(target))
        self.assertIn("--allow-nested", getattr(ctx.exception, "hint", ""))
        self.assertFalse(target.exists())

    def test_allow_nested_guards_the_enclosing_repo(self):
        outer = self.tmp / "inner"
        outer.mkdir()
        self._git("init", "-q", cwd=outer)
        target = outer / "ws"
        self.assertEqual(self._cf(*self._init_args(target), "--allow-nested"), 0)
        self.assertTrue((outer / ".git" / "hooks" / "pre-push").exists())
        self.assertIn("/ws/", (outer / ".gitignore").read_text())
        status = self._git("status", "--porcelain", "-uall", cwd=outer).stdout
        self.assertNotIn("ws/workspace.yaml", status)

    def test_a_workspace_repo_with_a_remote_is_refused_by_the_policy(self):
        target = self.tmp / "ws"
        target.mkdir()
        self._git("init", "-q", cwd=target)
        self._git("remote", "add", "origin", "git@github.com:example/public.git", cwd=target)
        with self.assertRaises(CfError) as ctx:
            self._cf(*self._init_args(target))
        self.assertIn("allow_remote", str(ctx.exception))


class TestExportPolicyIsRead(unittest.TestCase):
    """workspace.yaml's policy block was written by cf init and read by nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-policy-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "ws"
        buf = io.StringIO()
        with redirect_stdout(buf):
            cf_main.main(["init", str(self.workspace), "--no-git", "-q"])

    def _policy(self, **over):
        from cflib.yamlio import read_yaml, write_yaml

        path = self.workspace / "workspace.yaml"
        data = read_yaml(path)
        data.setdefault("policy", {}).update(over)
        write_yaml(path, data)

    def test_redaction_defaults_to_the_workspace_policy(self):
        from cfcmd import route as route_cmd

        class A:
            workspace = str(self.workspace)
            redact_quotes = None

        self.assertTrue(route_cmd._redact(A), "policy.redact_quotes_on_export is true")
        A.redact_quotes = False
        self.assertFalse(route_cmd._redact(A))
        A.redact_quotes = None
        self._policy(redact_quotes_on_export=False)
        self.assertFalse(route_cmd._redact(A))

    def test_an_unreadable_workspace_still_redacts(self):
        from cfcmd import route as route_cmd

        class A:
            workspace = str(self.tmp / "nope")
            redact_quotes = None

        self.assertTrue(route_cmd._redact(A))

    def test_publish_unchallenged_true_is_refused(self):
        from cfcmd import route as route_cmd

        class A:
            workspace = str(self.workspace)

        route_cmd._check_publish_policy(A)      # false: fine
        self._policy(publish_unchallenged=True)
        with self.assertRaises(SystemExit):
            route_cmd._check_publish_policy(A)

    def test_the_template_keys_are_all_read_somewhere(self):
        from cflib.workspace import Workspace

        policy = Workspace.load(self.workspace).policy
        self.assertEqual(set(policy),
                         {"publish_unchallenged", "redact_quotes_on_export", "allow_remote",
                          "allow_download", "require_confirmed_sources", "allow_errata_fetch"})

    def test_require_confirmed_sources_is_enforced_by_plan_run(self):
        """Discovery appends a new source with confirmed: false (R3).

        A workspace that sets this key is saying no run may be planned against a
        source nobody has signed off, and the one place to enforce it is where a
        run picks its source -- everything downstream is stamped with it.
        """
        from cfcmd import plan_run as plan_cmd
        from cflib.workspace import Workspace

        write_workspace(self.workspace,
                        sources=tao_source(),
                        extra="policy:\n  require_confirmed_sources: true\n")
        ws = Workspace.load(self.workspace)
        source = ws.source(PUBLISHED_ID)
        self.assertFalse(source.confirmed)
        with self.assertRaises(CfError) as ctx:
            plan_cmd._check_confirmed(ws, source)
        self.assertIn("not confirmed", str(ctx.exception))
        self.assertIn("cf sources confirm", getattr(ctx.exception, "hint", ""))

        # confirmed, or the policy off, and it plans
        ws.upsert_source(PUBLISHED_ID, confirmed=True)
        ws.save()
        reloaded = Workspace.load(self.workspace)
        plan_cmd._check_confirmed(reloaded, reloaded.source(PUBLISHED_ID))
        write_workspace(self.workspace, sources=tao_source())
        off = Workspace.load(self.workspace)
        plan_cmd._check_confirmed(off, off.source(PUBLISHED_ID))


if __name__ == "__main__":
    unittest.main()
