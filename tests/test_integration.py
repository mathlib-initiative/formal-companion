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
        # the command index of SKILL.md S"Command index". `book` is deleted: a
        # source is discovered by `cf onboard` and re-prepared by
        # `cf sources refresh` (docs/design/multi-source-model.md S7).
        for name in ("init", "onboard", "sources", "extract", "pair", "plan-run", "run",
                     "harvest", "import-corpus", "tally", "detect-proxies",
                     "adjudicate", "route", "report", "status"):
            self.assertIn(name, mapping)
        self.assertNotIn("book", mapping)


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

    def test_importing_another_edition_needs_the_override(self):
        """SKILL.md hard rule 3, at the one place a foreign edition enters."""
        args = ("import-corpus", "--run-id", "draft", "--from", str(self.source),
                "--campaign", str(self.campaign), "--no-tally", "-q", "--force",
                "--book-sha", "d" * 64)
        with self.assertRaises(CfError) as ctx:
            self._cf(*args)
        self.assertIn("another edition", getattr(ctx.exception, "hint", ""))
        self.assertEqual(self._cf(*args, "--allow-edition-mismatch"), 0)
        receipt = json.loads(
            (self.campaign / "corpus" / "draft" / "RECEIPT.json").read_text())
        self.assertEqual(receipt["book_sha"], "d" * 64)
        self.assertTrue(receipt["complete"])

    def test_import_gate_validates_what_landed(self):
        gate = self.tmp / "gate.local.json"
        book = self.tmp / "book.txt"
        book.write_text("nothing in here matches any quote\n", encoding="utf-8")
        gate.write_text(json.dumps({
            "schema_version": 2, "preset": "differential-text", "mode": "differential",
            "run_id": "g1", "book": {"text": str(book), "sha256": "a" * 64},
            "repos": ["ours"], "kinds": ["CONTENT"], "verdicts": ["AGREE"],
            "witness_required": False, "require_book_located": True,
            "require_book_sha": True, "findings_dir": ".", "kind_rules": {},
        }), encoding="utf-8")
        rc = self._cf("import-corpus", "--run-id", "g1", "--from", str(self.source),
                      "--campaign", str(self.campaign), "--no-tally", "--force",
                      "--gate", "--gate-config", str(gate))
        self.assertEqual(rc, 1, "a corpus that fails the gate must not be complete")
        receipt = json.loads((self.campaign / "corpus" / "g1" / "RECEIPT.json").read_text())
        self.assertFalse(receipt["complete"])
        self.assertFalse(receipt["gate"]["ok"])
        self.assertTrue(receipt["gate"]["problems"])

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


class TestHarvestSafety(unittest.TestCase):
    """`cf harvest --from host:/path` must obey the same rules as `cf run`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-harvest-safety-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.campaign = self.tmp / "campaign"
        self._cf("init", str(self.campaign), "--book", "b", "--offset", "16",
                 "--sha256", "a" * 64, "--no-git", "-q")

    def _cf(self, *argv) -> int:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cf_main.main(list(argv))
        self.out = buf.getvalue()
        return rc

    def _allow(self, alias: str, ssh: str) -> None:
        from cflib.yamlio import read_yaml, write_yaml

        path = self.campaign / "campaign.yaml"
        data = read_yaml(path)
        data.setdefault("hosts", {})["allowlist"] = [alias]
        data["hosts"][alias] = {"ssh": ssh, "remote_root": "/home/x"}
        write_yaml(path, data)

    def test_an_unlisted_host_is_refused_before_anything_runs(self):
        with self.assertRaises(CfError) as ctx:
            self._cf("harvest", "r1", "--from", "not-allowlisted.example.net:/findings",
                     "--execute", "--i-have-consent", "--campaign", str(self.campaign), "-q")
        self.assertIn("allowlist", str(ctx.exception))

    def test_an_allowlisted_host_still_needs_consent(self):
        self._allow("ffmain", "ffmain")
        with self.assertRaises(CfError) as ctx:
            self._cf("harvest", "r1", "--from", "ffmain:/findings", "--execute",
                     "--campaign", str(self.campaign), "-q")
        self.assertIn("consent", str(ctx.exception))

    def test_a_local_source_needs_neither(self):
        source = self.tmp / "findings"
        source.mkdir()
        (source / "exercise-1-1-1.json").write_text(json.dumps({
            "schema_version": 2, "run_id": "r1", "book_sha": "a" * 64,
            "item": "Exercise 1.1.1", "book_located": True, "book_passage_quote": "",
            "matches": [], "disagreements": [], "coverage_notes": "",
        }), encoding="utf-8")
        self.assertEqual(
            self._cf("harvest", "r1", "--from", str(source), "--no-tally",
                     "--campaign", str(self.campaign), "-q"), 0)


class TestExtractSurvivesOneBrokenCompanion(unittest.TestCase):
    """One unparseable adapter.yaml must not take the whole stage down."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-extract-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.campaign = self.tmp / "campaign"
        self.repo = self.tmp / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "A.lean").write_text(
            "/-- `Exercise 1.1.1` (a tagged declaration). -/\n"
            "theorem foo : True := trivial\n", encoding="utf-8")
        for argv in (["init"], ["add", "-A"],
                     ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "x"]):
            subprocess.run(["git", "-C", str(self.repo), *argv],
                           capture_output=True, check=False)
        self._cf("init", str(self.campaign), "--book", "b", "--offset", "16",
                 "--sha256", "a" * 64, "--no-git", "-q")

    def _cf(self, *argv) -> int:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cf_main.main(list(argv))
        self.out = buf.getvalue()
        return rc

    def _break(self, name: str) -> None:
        d = self.campaign / "companions" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "adapter.yaml").write_text(
            "name: %s\nrepo: %s\nadapter: ledger_nodes\nglob: \"*.json\"\n"
            "anchor: ledger\ngranularity: node\nwrite_policy: read_only\n"
            % (name, self.repo), encoding="utf-8")

    def test_the_healthy_companion_is_still_extracted_and_the_command_fails(self):
        self.assertEqual(self._cf("onboard", "ours", "--repo", str(self.repo),
                                  "--adapter", "docstring_tags", "--glob", "src/**/*.lean",
                                  "--campaign", str(self.campaign), "-q"), 0)
        self._break("broken")
        rc = self._cf("extract", "--campaign", str(self.campaign))
        self.assertEqual(rc, 1, self.out)
        self.assertIn("broken", self.out)
        table = self.campaign / "companions" / "ours" / "statements.jsonl"
        self.assertTrue(table.exists(), "a broken peer stopped the healthy companion")
        self.assertTrue(table.read_text().strip())


class TestInitGitGuard(unittest.TestCase):
    """The "campaign repos have no remote" invariant must actually be established."""

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
        return ["init", str(target), "--book", "b", "--offset", "16",
                "--sha256", "a" * 64, "-q"]

    def test_a_fresh_directory_gets_its_own_repo_and_the_hook(self):
        target = self.tmp / "campaign"
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
        target = outer / "campaign"
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
        target = outer / "campaign"
        with self.assertRaises(CfError) as ctx:
            self._cf(*self._init_args(target))
        self.assertIn("--allow-nested", getattr(ctx.exception, "hint", ""))
        self.assertFalse(target.exists())

    def test_allow_nested_guards_the_enclosing_repo(self):
        outer = self.tmp / "inner"
        outer.mkdir()
        self._git("init", "-q", cwd=outer)
        target = outer / "campaign"
        self.assertEqual(self._cf(*self._init_args(target), "--allow-nested"), 0)
        self.assertTrue((outer / ".git" / "hooks" / "pre-push").exists())
        self.assertIn("/campaign/", (outer / ".gitignore").read_text())
        status = self._git("status", "--porcelain", "-uall", cwd=outer).stdout
        self.assertNotIn("campaign/campaign.yaml", status)

    def test_a_campaign_repo_with_a_remote_is_refused_by_the_policy(self):
        target = self.tmp / "campaign"
        target.mkdir()
        self._git("init", "-q", cwd=target)
        self._git("remote", "add", "origin", "git@github.com:example/public.git", cwd=target)
        with self.assertRaises(CfError) as ctx:
            self._cf(*self._init_args(target))
        self.assertIn("allow_remote", str(ctx.exception))


class TestExportPolicyIsRead(unittest.TestCase):
    """campaign.yaml's policy block was written by cf init and read by nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-policy-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.campaign = self.tmp / "campaign"
        buf = io.StringIO()
        with redirect_stdout(buf):
            cf_main.main(["init", str(self.campaign), "--book", "b", "--offset", "16",
                          "--sha256", "a" * 64, "--no-git", "-q"])

    def _policy(self, **over):
        from cflib.yamlio import read_yaml, write_yaml

        path = self.campaign / "campaign.yaml"
        data = read_yaml(path)
        data.setdefault("policy", {}).update(over)
        write_yaml(path, data)

    def test_redaction_defaults_to_the_campaign_policy(self):
        from cfcmd import route as route_cmd

        class A:
            campaign = str(self.campaign)
            redact_quotes = None

        self.assertTrue(route_cmd._redact(A), "policy.redact_quotes_on_export is true")
        A.redact_quotes = False
        self.assertFalse(route_cmd._redact(A))
        A.redact_quotes = None
        self._policy(redact_quotes_on_export=False)
        self.assertFalse(route_cmd._redact(A))

    def test_an_unreadable_campaign_still_redacts(self):
        from cfcmd import route as route_cmd

        class A:
            campaign = str(self.tmp / "nope")
            redact_quotes = None

        self.assertTrue(route_cmd._redact(A))

    def test_publish_unchallenged_true_is_refused(self):
        from cfcmd import route as route_cmd

        class A:
            campaign = str(self.campaign)

        route_cmd._check_publish_policy(A)      # false: fine
        self._policy(publish_unchallenged=True)
        with self.assertRaises(SystemExit):
            route_cmd._check_publish_policy(A)

    def test_the_template_keys_are_all_read_somewhere(self):
        from cflib.campaign import Campaign

        policy = Campaign.load(self.campaign).policy
        self.assertEqual(set(policy),
                         {"publish_unchallenged", "redact_quotes_on_export", "allow_remote"})


if __name__ == "__main__":
    unittest.main()
