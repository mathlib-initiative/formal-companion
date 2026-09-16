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
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                          # noqa: E402
from cfcmd import plan_run                                    # noqa: E402
from cflib.errors import CfError                              # noqa: E402
from plan import make_tasks as mt                             # noqa: E402
from report import make_handoff                               # noqa: E402

BASELINE_CORPUS = os.environ.get("CF_BASELINE_CORPUS")


class TestCommandDiscovery(unittest.TestCase):
    """Every command module must import; a broken one is invisible, not loud."""

    def test_no_command_is_unavailable(self):
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        mapping = cf_main.discover_commands(subparsers)
        broken = [name for name, mod in mapping.items() if mod is None]
        self.assertEqual(broken, [], f"command modules failed to register: {broken}")

    def test_the_documented_commands_are_all_registered(self):
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        mapping = cf_main.discover_commands(subparsers)
        for name in ("init", "book", "onboard", "extract", "pair", "plan-run", "run",
                     "harvest", "import-corpus", "tally", "detect-proxies",
                     "adjudicate", "route", "report", "status"):
            self.assertIn(name, mapping)


class TestPairsArtifactsAreWrappers(unittest.TestCase):
    """``cf pair`` writes {..., items: [...]}; planning must unwrap it."""

    QUEUE = {"created": "x", "book_sha": "a" * 64, "offset": 16, "count": 2,
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
            self.QUEUE, {"ours": self.TARGETS}, kinds=kinds)
        self.assertEqual([e.item for e in entries],
                         ["Definition 1.1.1", "Exercise 1.1.2"])
        targets = mt.target_entries(self.TARGETS, "ours")
        self.assertEqual([e.item for e in targets], ["Definition 1.1.1"])


class TestHandoffRunTable(unittest.TestCase):
    """The run table reads plan-run's nested run.yaml, not a flattened one."""

    RUN_YAML = {
        "mode": "attack", "model": "claude-sonnet-5",
        "host": {"name": "ffmain", "kind": "remote", "remote_root": "/home/x", "ssh": "x@y"},
        "book": {"id": "tao-measure", "access": "text+images"},
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
            {"r1": {"host": "ffmain", "book_access": "text", "tasks": 12}}, [])
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
        self.campaign = self.tmp / "campaign"
        self.source = self.tmp / "source"
        self.source.mkdir(parents=True)
        for name in ("exercise-1-1-1.json", "exercise-1-1-2.json"):
            (self.source / name).write_text(json.dumps(self.V1_DOC), encoding="utf-8")
        self._cf("init", str(self.campaign), "--book", "b", "--offset", "16",
                 "--sha256", "a" * 64, "--no-git", "-q")

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
                      "--campaign", str(self.campaign), "--no-tally", "-q")
        self.assertEqual(rc, 0)

        dest = self.campaign / "corpus" / "r1"
        receipt = json.loads((dest / "RECEIPT.json").read_text())
        self.assertTrue(receipt["complete"])
        self.assertEqual(receipt["expected"]["count"], 2)
        self.assertEqual(receipt["actual"]["count"], 2)
        self.assertEqual(receipt["missing"], [])
        self.assertEqual(receipt["source"]["kind"], "import")

        doc = json.loads((dest / "exercise-1-1-1.json").read_text())
        self.assertEqual(doc["schema_version"], 2)
        self.assertEqual(doc["run_id"], "r1")
        self.assertEqual(doc["book_sha"], "a" * 64)

        after = {p.name: (p.stat().st_mtime_ns, p.read_bytes())
                 for p in self.source.glob("*.json")}
        self.assertEqual(before, after, "the source corpus was modified")

    def test_import_is_idempotent(self):
        args = ("import-corpus", "--run-id", "r1", "--from", str(self.source),
                "--campaign", str(self.campaign), "--no-tally", "-q", "--force")
        self._cf(*args)
        first = (self.campaign / "corpus" / "r1" / "exercise-1-1-1.json").read_bytes()
        self._cf(*args)
        second = (self.campaign / "corpus" / "r1" / "exercise-1-1-1.json").read_bytes()
        self.assertEqual(first, second)

    def test_import_refuses_an_empty_or_missing_directory(self):
        # cf.main() RAISES for a library caller; the CLI wrapper in cf.py's
        # __main__ block is what turns a CfError into exit 2.
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaises(CfError):
            self._cf("import-corpus", "--run-id", "r2", "--from", str(empty),
                     "--campaign", str(self.campaign), "-q")
        with self.assertRaises(CfError):
            self._cf("import-corpus", "--run-id", "r3", "--from", str(self.tmp / "nope"),
                     "--campaign", str(self.campaign), "-q")
        self.assertFalse((self.campaign / "corpus" / "r2").exists())

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
                      "--repos", "ours,teorth,adam", "--campaign", str(self.campaign),
                      "--no-tally", "-q")
        self.assertEqual(rc, 0)
        receipt = json.loads(
            (self.campaign / "corpus" / "findings-hunt" / "RECEIPT.json").read_text())
        self.assertEqual(receipt["expected"]["count"], 763)
        self.assertEqual(receipt["actual"]["count"], 763)
        self.assertTrue(receipt["complete"])


if __name__ == "__main__":
    unittest.main()
