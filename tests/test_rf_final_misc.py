"""Confirmation-pass fixes: the statements a command or a document still got wrong.

Each case here fails against the tree as it was:

* `SKILL.md` described a shared bundle's redacted quote as ``(page, length,
  hash)`` while `POLICY.md` and ``references/traps.md`` described the record that
  is really written, so the three shipped documents disagreed about how much of a
  sentence leaves the workspace;
* ``references/onboarding-contract.md`` relabelled the dry run's ``files`` count
  as "the files that yielded rows" -- it is the files the adapter PARSES, and on
  the measured target 112 of them yield 33 files' worth of rows;
* `cf share export` printed a refusal `cf share import` does not make, and named
  three field names for a prose scan that walks every free-text field of a row;
* `cf onboard <repo> --source <the wrong file>` bound before preparing, so a
  refusal exited 2 with a source row, a registry overlay row and a prepared
  directory to its name;
* the one-spelling hints interpolated a workspace path raw, so a workspace whose
  directory name carries a space printed commands that cannot be pasted -- and
  `cf route` writes one of them into the ledger and into the owner's draft;
* `cf status` exited 2 on a companion directory it had printed as "not onboarded"
  one line earlier;
* ``tests/test_rf_docs.py``'s module docstring described a narrower flag rule
  than the one that runs;
* ``references/findings-schema-v2.md`` said priority is a function of the run
  mode; it is a function of the kind, the peers and the section.

Stdlib unittest only, no network, and nothing written outside a temporary
directory.
"""
from __future__ import annotations

# The first import, before anything reads HOME: it pins HOME at a temporary
# directory for `unittest discover`, which loads no conftest.py.  The second
# spelling is for `python3 -m unittest tests.<module>`.  See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import argparse
import ast
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

#: how long a child interpreter may take before the case is called broken
CHILD_TIMEOUT = 300

SOURCE_ID = "a-book-1st-edition"
SOURCE_SHA = "a" * 64
COMPANION = "ours"


def read(name: str) -> str:
    return (SKILL / name).read_text(encoding="utf-8")


def scratch(case: unittest.TestCase) -> Path:
    tmp = tempfile.TemporaryDirectory(prefix="cf-rf-misc-")
    case.addCleanup(tmp.cleanup)
    return Path(tmp.name).resolve()   # /var -> /private/var: cflib resolves its paths


def child_env(**extra: str) -> dict:
    """A minimal environment for a child `cf`: no CF_* from this shell."""
    env = {"PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C.UTF-8",
           "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    env.update(extra)
    return env


def cf(*argv: str, cwd: Path | None = None, env: dict | None = None):
    """Run the CLI the way a user does. Never touches the network."""
    return subprocess.run([sys.executable, str(SCRIPTS / "cf.py"), *argv],
                          capture_output=True, text=True, timeout=CHILD_TIMEOUT,
                          cwd=str(cwd) if cwd else None, env=env or child_env())


# ---------------------------------------------------------------------------
# FR#32 -- one redaction, described the same way by every document that has it
# ---------------------------------------------------------------------------
#: how the documents spell the record a redacted quote leaves behind, e.g.
#: ``(page, normalised length, sha256, …)``.  Anchored on the comma so that
#: ``(page images; …)`` elsewhere is not a record.
_QUOTE_RECORD = re.compile(r"\(page[,)][^)]*\)?", re.I | re.S)

REDACTION_DOCS = ("SKILL.md", "POLICY.md", "references/traps.md")


class TestEveryDocumentDescribesTheSameRedactedQuote(unittest.TestCase):
    """SKILL.md promised less of a redaction than `cf share export` performs.

    It said the record is ``(page, length, hash)`` in one place and
    ``(page, hash)`` in another, while POLICY.md and references/traps.md say the
    edge words travel too.  A reader deciding whether a bundle may go to a third
    party was reading whichever document they opened.
    """

    def records(self) -> list[tuple[str, str]]:
        found = []
        for name in REDACTION_DOCS:
            flat = " ".join(read(name).split())
            found += [(name, hit) for hit in _QUOTE_RECORD.findall(flat)]
        return found

    def test_every_document_states_all_four_parts_of_the_record(self):
        records = self.records()
        self.assertTrue(records, "no document spells the redacted-quote record: regex is wrong")
        for name, record in records:
            with self.subTest(doc=name, record=record):
                self.assertIn("sha256", record.lower(),
                              f"{name} names a hash the export does not write")
                self.assertIn("length", record.lower())
                self.assertIn("quarter", record.lower(),
                              f"{name} omits the words that travel with the record")
                self.assertIn("fourteen", record.lower(),
                              f"{name} omits the floor below which nothing travels")

    def test_the_bounds_the_documents_state_are_the_bounds_the_code_applies(self):
        """The documents are checked against `share.redact`, not against each other."""
        from share import redact as redact_mod

        self.assertEqual(redact_mod.MIN_WORDS_FOR_EDGES, 14,
                         "the documents say nothing travels below fourteen words")
        for count in range(14, 200):
            keep = redact_mod.edge_words(["word"] * count)
            with self.subTest(words=count):
                self.assertLessEqual(keep, count // 4,
                                     "the documents say at most a quarter from each end")


# ---------------------------------------------------------------------------
# FR#42 -- the onboarding contract's file counts, labelled by what they count
# ---------------------------------------------------------------------------
class TestTheFileCountsAreTwoDifferentCounts(unittest.TestCase):
    """The contract relabelled the dry run's `files` as the files that yielded rows.

    It is the files the adapter PARSES: the glob, minus ``exclude:``, minus what
    the repo git-ignores.  Measured on `tao-measure-lean` the two are 112 and 33.
    """

    def adapter(self, repo: Path):
        from adapters import api as adapters_api

        return adapters_api.from_config({
            "adapter": "docstring_tags", "name": "probe", "repo": str(repo),
            "glob": "**/*.lean", "granularity": "item",
            "exclude": [".lake/**/*.lean", "**/.lake/**/*.lean"],
            "sources": {"default": SOURCE_ID, "map": []},
        }, name="probe")

    def repo(self) -> Path:
        """Two parsed files, one of them tagged, and a vendored one that is neither."""
        repo = scratch(self) / "repo"
        (repo / "Src").mkdir(parents=True)
        (repo / "Src" / "Tagged.lean").write_text(
            "/-- `Exercise 1.2.4` Show that the union is elementary. -/\n"
            "theorem a : True := trivial\n", encoding="utf-8")
        (repo / "Src" / "Untagged.lean").write_text("theorem b : True := trivial\n",
                                                    encoding="utf-8")
        (repo / ".lake" / "packages" / "dep").mkdir(parents=True)
        (repo / ".lake" / "packages" / "dep" / "C.lean").write_text(
            "/-- `Exercise 9.9.9` vendored. -/\ntheorem c : True := trivial\n",
            encoding="utf-8")
        return repo

    def test_the_files_key_counts_what_the_adapter_parses_not_what_yields_rows(self):
        repo = self.repo()
        adapter = self.adapter(repo)
        shas = {SOURCE_ID: SOURCE_SHA}
        counts = adapter.dry_run(repo, sources=shas)
        rows = list(adapter.extract(repo, sources=shas))
        self.assertEqual(counts["files"], len(adapter.source_files(repo)))
        self.assertEqual(counts["files"], 2, "the vendored file is excluded, the untagged is not")
        self.assertEqual(len({row["file"] for row in rows}), 1)
        self.assertGreater(counts["files"], len({row["file"] for row in rows}),
                           "a file the adapter parses need not produce a row")

    def test_the_contract_does_not_call_the_files_key_the_row_producing_count(self):
        line = [ln for ln in read("references/onboarding-contract.md").splitlines()
                if "dry run's `files` key" in ln]
        self.assertEqual(len(line), 1, line)
        self.assertNotIn("the files that yielded rows", line[0])
        self.assertIn("parses", line[0])


# ---------------------------------------------------------------------------
# NEW#g-misc-1 / NEW#g-misc-2 -- what `cf share export` says it did and what is next
# ---------------------------------------------------------------------------
def share_workspace(case: unittest.TestCase, root: Path):
    """A workspace with one companion, one source and one routed row."""
    from cflib.workspace import Workspace

    repo = root.parent / "companion-repo"
    repo.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True)
    (root / "workspace.yaml").write_text(
        "version: 2\n"
        "workspace:\n  name: rf\n"
        "companions:\n"
        f"  {COMPANION}:\n"
        f"    repo: {repo}\n"
        "    adapter: docstring_tags\n"
        f"    sources: [{SOURCE_ID}]\n"
        f"    anchor_source: {SOURCE_ID}\n"
        "sources:\n"
        f"  {SOURCE_ID}:\n"
        f'    sha256: "{SOURCE_SHA}"\n'
        "    title: A Book\n"
        '    edition: "first edition"\n'
        "    offset: 16\n"
        "    role: audited\n"
        "hosts:\n  allowlist: []\n", encoding="utf-8")
    workspace = Workspace.load(root)
    workspace.paths.ensure_skeleton()
    workspace.paths.companion_dir(COMPANION).mkdir(parents=True, exist_ok=True)
    workspace.paths.source_dir(SOURCE_ID).mkdir(parents=True, exist_ok=True)
    workspace.paths.source_text(SOURCE_ID).write_text(
        "Let E be an elementary set. Then the outer measure of every subset of E "
        "is at most the outer measure of E itself.\n", encoding="utf-8")
    return workspace


def routed_row() -> dict:
    return {
        "finding_id": "c3" * 32, "run_id": "run-1", "source_id": SOURCE_ID,
        "source_sha": SOURCE_SHA, "item": "Exercise 1.2.3", "kind": "CONTENT",
        "severity": "high", "repo": COMPANION, "verdict": "CONFIRMED_CONTENT",
        "unfaithful_decls": [f"{COMPANION}:Book.outerMeasure_mono"], "other_decls": [],
        "faithful": [], "method": "workflow:adjudicate",
        "reason": "the statement drops a hypothesis",
        "book_quote": "Let E be an elementary set.",
    }


def export(case: unittest.TestCase, workspace, out: Path) -> tuple[dict, str]:
    from cfcmd import share as share_cmd
    from share import bundle as bundle_mod

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        share_cmd.run(argparse.Namespace(
            share_command="export", workspace=str(workspace.root), out=str(out),
            companion=[], redact=None, include_statements=False,
            skip_unchallenged=True, force=False))
    return bundle_mod.read_manifest(out), buf.getvalue()


class TestTheZeroLedgerExportDescribesTheImportThatFollows(unittest.TestCase):
    """`cf adjudicate` writes accepted/refuted even when it adjudicates nothing.

    `share.merge` refuses on those FILES being absent, not on their row count, so
    the export's "`cf share import` needs at least one adjudicated ledger row and
    will refuse this bundle" was false for exactly the bundle that branch
    describes: the import accepts it and merges nothing.
    """

    def setUp(self):
        from cflib.jsonio import write_jsonl

        root = scratch(self) / "alice" / "ws"
        self.workspace = share_workspace(self, root)
        write_jsonl(self.workspace.paths.routed_jsonl(COMPANION), [routed_row()],
                    meta={"repo": COMPANION, "status": "CHALLENGED", "schema": "routed-v2"})
        # what `cf adjudicate --transcribe` leaves after adjudicating nothing:
        # both files, each carrying its `_meta` row and no data row
        for path in (self.workspace.paths.accepted_jsonl(COMPANION),
                     self.workspace.paths.refuted_jsonl(COMPANION)):
            write_jsonl(path, [], meta={"repo": COMPANION, "schema": "ledger-v2"})
        self.out = scratch(self) / "bundle"
        self.manifest, self.printed = export(self, self.workspace, self.out)

    def test_the_bundle_this_branch_describes_is_one_the_import_accepts(self):
        from share import bundle as bundle_mod
        from share import merge as merge_mod

        self.assertEqual(self.manifest["counts"]["ledger_rows"], 0)
        self.assertTrue(bundle_mod.bundle_ledger_files(self.out),
                        "the ledger files `cf adjudicate` wrote are in the bundle")
        other = share_workspace(self, scratch(self) / "bob" / "ws")
        report = merge_mod.merge_bundle(other, self.out, self.manifest)
        self.assertEqual(report["added"], 0)

    def test_the_export_does_not_promise_a_refusal(self):
        nxt = self.printed.split("next:")[-1]
        self.assertNotIn("will refuse this bundle", nxt)
        self.assertNotIn("needs at least one adjudicated ledger row", nxt)
        self.assertIn("merges adjudicated ledger rows", nxt)
        self.assertEqual([c for c in nxt.splitlines() if c.strip().startswith("cf share import")],
                         [], "the export named a command on a bundle with no rows to merge")

    def test_the_summary_names_the_scope_the_scan_really_has(self):
        """FR#3 widened the scan to every free-text field; two printed strings
        still named the three v1 spellings, and they are the only statement of
        the scan's scope an operator sees."""
        self.assertIn("free-text fields", self.printed)
        self.assertNotIn("reason/reasoning/concept", self.printed)

    def test_the_unchecked_prose_warning_names_it_too(self):
        """The same sentence on the other branch: no pinned text to compare against."""
        workspace = share_workspace(self, scratch(self) / "notext" / "ws")
        workspace.paths.source_text(SOURCE_ID).unlink()
        from cflib.jsonio import write_jsonl

        write_jsonl(workspace.paths.routed_jsonl(COMPANION), [routed_row()],
                    meta={"repo": COMPANION, "status": "CHALLENGED", "schema": "routed-v2"})
        _, printed = export(self, workspace, scratch(self) / "bundle2")
        self.assertIn("NOT checked for verbatim source passages", printed)
        self.assertNotIn("inside a reason or a reasoning", printed)
        self.assertIn("free-text fields", printed)


# ---------------------------------------------------------------------------
# NEW#g-misc-3 -- a refused `cf onboard --source` leaves the workspace alone
# ---------------------------------------------------------------------------
class TestARefusedOnboardLeavesNothingBehind(unittest.TestCase):
    """`cf sources add` prepares first; `cf onboard` reached the same refusals
    with the old ordering, so exit 2 left a source row, a registry overlay row
    (with its `cf init` comment header re-dumped) and a prepared directory --
    recoverable only with `cf sources remove`, which nothing named."""

    def setUp(self):
        self.root = scratch(self)
        self.home = self.root / "home"
        self.home.mkdir()
        repo = self.root / "barerepo" / "Src"
        repo.mkdir(parents=True)
        (repo / "A.lean").write_text(
            "/-- `Exercise 1.2.4` Show that the union of two elementary sets is "
            "elementary. -/\ntheorem union_elementary : True := trivial\n", encoding="utf-8")
        # prose with no numbered item in it: the "this is not the document this
        # companion formalises" refusal, reached through `--source`
        self.appendix = self.root / "appendix.txt"
        self.appendix.write_text(
            "Appendix Manuscript\n\nProse only, with no numbered statements in it at all.\n",
            encoding="utf-8")
        self.ws = self.root / "ws"
        res = cf("init", str(self.ws), "--no-git", "-q", env=child_env(HOME=str(self.home)))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.before = {name: (self.ws / name).read_bytes()
                       for name in ("workspace.yaml", "registry.yaml")}

    def refuse(self):
        return cf("--workspace", str(self.ws), "onboard", str(self.root / "barerepo"),
                  "--adapter", "docstring_tags", "--source", str(self.appendix),
                  "--no-errata", env=child_env(HOME=str(self.home)))

    def test_the_two_yaml_files_are_byte_identical_afterwards(self):
        res = self.refuse()
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        for name, before in self.before.items():
            with self.subTest(file=name):
                self.assertEqual((self.ws / name).read_bytes(), before)

    def test_no_source_directory_is_left_prepared(self):
        self.refuse()
        self.assertEqual(sorted(p.name for p in (self.ws / "sources").iterdir()), [])

    def test_the_refusal_retracts_the_prepared_line_by_name(self):
        """`_prepare_source` prints `ok   prepared  sources/<id>/` first, and that
        line is true until the rollback deletes the directory it names."""
        res = self.refuse()
        said = res.stdout + res.stderr
        self.assertIn("ok   prepared  sources/appendix/", said)
        self.assertIn("appendix was not added; the workspace is unchanged", said)
        self.assertIn("sources/appendix/ was removed again", said)

    def test_the_workspace_still_works_afterwards(self):
        """The state a refusal used to leave was one `Workspace.load` rejects."""
        self.refuse()
        res = cf("--workspace", str(self.ws), "status", env=child_env(HOME=str(self.home)))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("appendix", res.stdout + res.stderr)


class TestARegistrationRetractsOnlyWhatItCreated(unittest.TestCase):
    """The undo is shared with `cf sources add`, so it is pinned on its own."""

    def workspace(self):
        from cflib.workspace import Workspace

        root = scratch(self) / "ws"
        root.mkdir(parents=True)
        (root / "workspace.yaml").write_text(
            "version: 2\nworkspace:\n  name: rf\ncompanions: {}\nsources: {}\n"
            "hosts:\n  allowlist: []\n", encoding="utf-8")
        workspace = Workspace.load(root, validate=False)
        workspace.paths.ensure_skeleton()
        return workspace

    def test_a_source_the_workspace_already_held_is_not_this_calls_to_remove(self):
        from cfcmd.onboard import SourceRegistration

        workspace = self.workspace()
        workspace.upsert_source("held", title="Held", edition="1e", role="audited")
        workspace.save()
        kept_dir = workspace.paths.source_dir("held")
        kept_dir.mkdir(parents=True)
        (kept_dir / "text.txt").write_text("held\n", encoding="utf-8")

        registration = SourceRegistration(workspace)
        workspace.upsert_source("new", title="New", edition="1e", role="audited")
        new_dir = workspace.paths.source_dir("new")
        new_dir.mkdir(parents=True)
        (new_dir / "text.txt").write_text("new\n", encoding="utf-8")

        dropped = registration.retract(["held", "new"])
        self.assertEqual(dropped, {"new": True})
        self.assertIn("held", workspace.sources)
        self.assertNotIn("new", workspace.sources)
        self.assertTrue(kept_dir.is_dir())
        self.assertFalse(new_dir.exists())

    def test_the_overlay_is_restored_from_its_bytes_not_re_dumped(self):
        from cfcmd.onboard import SourceRegistration

        workspace = self.workspace()
        overlay = workspace.root / "registry.yaml"
        before = b"# a comment header cf init wrote\nversion: 1\nsources: {}\n"
        overlay.write_bytes(before)

        registration = SourceRegistration(workspace)
        overlay.write_bytes(b"version: 1\nsources:\n  new:\n    title: New\n")
        workspace.upsert_source("new", title="New", edition="1e", role="audited")
        registration.retract(["new"])
        self.assertEqual(overlay.read_bytes(), before)


# ---------------------------------------------------------------------------
# NEW#g-misc-5 -- a hint is pasteable from a workspace whose path has a space
# ---------------------------------------------------------------------------
class TestAHintSurvivesASpaceInTheWorkspacePath(unittest.TestCase):
    """Both of the wave's one-spelling hints interpolated the path raw.

    `cf adjudicate`'s is not only printed: `cf route` writes it into
    ``ledger/routed/findings.<name>.jsonl`` and into the owner-message draft, so
    the broken command shipped to the repository's owner.
    """

    def test_the_adjudicate_invocation_is_one_shell_word(self):
        import shlex

        from cflib import invocation

        command = invocation.adjudicate_command("/private/tmp/my audit/ws", "ours",
                                                "--emit-args")
        self.assertEqual(shlex.split(command),
                         ["cf", "adjudicate", "--workspace", "/private/tmp/my audit/ws",
                          "--companion", "ours", "--emit-args"])

    def test_a_space_free_path_is_left_exactly_as_it_was(self):
        from cflib import invocation

        self.assertEqual(
            invocation.adjudicate_command("/tmp/ws", "ours", "--emit-args"),
            "cf adjudicate --workspace /tmp/ws --companion ours --emit-args")

    def test_the_cd_cf_init_prints_runs(self):
        """`cd /tmp/my audit/ws` is `cd: too many arguments`, and the whole point
        of that line is that the operator pastes it."""
        outside = scratch(self) / "my audit"
        outside.mkdir(parents=True)
        home = scratch(self) / "home"
        home.mkdir()
        target = outside / "ws"
        res = cf("init", str(target), "--no-git", cwd=outside, env=child_env(HOME=str(home)))
        self.assertEqual(res.returncode, 0, res.stderr)
        printed = [line.strip() for line in (res.stdout + res.stderr).splitlines()
                   if line.strip().startswith("cd ")]
        self.assertEqual(len(printed), 1, res.stdout)
        replayed = subprocess.run(["bash", "-c", f"{printed[0]} && pwd"], capture_output=True,
                                  text=True, cwd=str(outside), timeout=CHILD_TIMEOUT)
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        self.assertEqual(replayed.stdout.strip(), str(target))


# ---------------------------------------------------------------------------
# NEW#g-misc-6 -- `cf status` on a companion directory workspace.yaml has no row for
# ---------------------------------------------------------------------------
class TestStatusSurvivesADirectoryOnlyCompanion(unittest.TestCase):
    """`companion_names()` is "workspace.yaml, plus any directory with an adapter".

    That is the state a half-finished onboard or a copied companion directory
    leaves, and `cf status` is where an operator would see it: it printed the row
    as `not onboarded` and then exited 2 on `sources_of()`.
    """

    def setUp(self):
        from cflib import state as state_mod

        root = scratch(self) / "ws"
        self.workspace = share_workspace(self, root)
        with state_mod.update(self.workspace, COMPANION) as st:
            st["name"] = COMPANION
            st["repo"] = str(root.parent / "companion-repo")
        ghost = self.workspace.paths.companion_dir("ghost")
        ghost.mkdir(parents=True)
        (ghost / "adapter.yaml").write_text(
            f"name: ghost\nadapter: docstring_tags\nrepo: {root.parent}\n"
            'glob: "**/*.lean"\n', encoding="utf-8")

    def status(self) -> tuple[int, str]:
        from cfcmd import status as status_cmd

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = status_cmd.run(argparse.Namespace(
                workspace=str(self.workspace.root), companion=None, source=None,
                as_json=False, runs=False, verbose=False))
        return rc, buf.getvalue()

    def test_it_prints_the_directory_and_exits_zero(self):
        rc, said = self.status()
        self.assertEqual(rc, 0)
        self.assertIn("ghost", said)
        self.assertIn("not onboarded", said)
        self.assertNotIn("unknown companion", said)

    def test_the_companion_with_a_row_is_still_counted_against_its_sources(self):
        """The guard must not turn every companion's bound-source count into 0."""
        _, said = self.status()
        row = [line for line in said.splitlines() if line.startswith(COMPANION)]
        self.assertEqual(len(row), 1, said)
        self.assertIn(f"bound to {SOURCE_ID}", said)


# ---------------------------------------------------------------------------
# NEW#g-misc-7 -- the docs suite's stated scope is the scope it walks
# ---------------------------------------------------------------------------
class TestTheDocsModuleSaysWhatItChecks(unittest.TestCase):
    """The integration commit widened the flag rule to `scripts/` and fixed the
    stale spellings, and left the module docstring saying neither had happened --
    so the next reader widens a glob that is already wide and hunts spellings
    that are gone."""

    FLAG_TEST = "test_every_flag_in_a_code_literal_belongs_to_the_command_it_follows"

    def roots_walked(self) -> set[str]:
        """The glob roots the flag rule's own file set names."""
        text = read("tests/test_rf_docs.py")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == self.FLAG_TEST:
                body = ast.get_source_segment(text, node) or ""
                return set(re.findall(r'SKILL\.glob\("([A-Za-z_]+)/', body))
        self.fail(f"tests/test_rf_docs.py no longer defines {self.FLAG_TEST}")
        return set()

    def test_the_docstring_scopes_the_flag_rule_the_way_the_flag_rule_is_scoped(self):
        """The cheapest honest guard: the scope words are the glob roots."""
        roots = self.roots_walked()
        self.assertEqual(roots, {"scripts", "workflows", "templates"}, roots)
        doc = " ".join((ast.get_docstring(
            ast.parse(read("tests/test_rf_docs.py")), clean=False) or "").split())
        self.assertIn("flag rule", doc)
        clause = doc[doc.index("flag rule"):].split(":")[0]
        named = set(re.findall(r"``([A-Za-z_]+)/``", clause))
        self.assertEqual(named, roots,
                         f"the flag rule walks {sorted(roots)}; the module docstring scopes "
                         f"it to {sorted(named)}")

    def test_the_docstring_claims_no_stale_spelling_that_is_still_there(self):
        doc = " ".join((ast.get_docstring(
            ast.parse(read("tests/test_rf_docs.py")), clean=False) or "").split())
        self.assertNotIn("are not fixed yet", doc)


# ---------------------------------------------------------------------------
# NEW#g-misc-8 -- the task schema's priority comment states the rule
# ---------------------------------------------------------------------------
class TestTheSchemaPriorityCommentIsThePlannersRule(unittest.TestCase):
    """`# attack runs: max; differential: default` described 200 of 763 tasks.

    Priority is not a function of the run mode: `priority_for()` reads the item's
    KIND, whether a peer companion shares the source, and the section -- so on an
    exercise-heavy companion most attack tasks are `default`.
    """

    def comment(self) -> str:
        text = read("references/findings-schema-v2.md")
        section = text[text.index("\n## 2. "):text.index("\n## 3. ")]
        block = re.search(r"```yaml\n(.*?)```", section, re.S).group(1)
        return re.search(r"(?m)^priority: (\S+)\s*#(.*)$", block).group(2)

    def rendered(self, preset_name: str, item: str, single: bool, section: str | None) -> str:
        from plan import make_tasks as mt
        from plan import presets as presets_mod

        entry = mt.Entry(item=item, repos=["ours", "theirs"], decls={"ours": ["Widget.lemma"]},
                         source_id="widget-theory-2e", printed_page=71,
                         single_source=single, section=section)
        tasks = mt.make_tasks([entry], presets_mod.get(preset_name),
                              {"run_id": "probe", "source_id": "widget-theory-2e",
                               "source_sha": "ab" * 32, "gate_command": "python3 gate.py",
                               "companion": "ours"})
        return re.search(r"(?m)^priority: (\S+)", tasks[0].text).group(1)

    def test_an_attack_run_does_not_write_max_for_every_task(self):
        self.assertEqual(self.rendered("attack-vac", "Lemma 2.3.4", True, "2.3"), "max")
        self.assertEqual(self.rendered("attack-vac", "Exercise 1.3.19", True, "1.3"), "default")
        self.assertEqual(self.rendered("attack-vac", "Lemma 2.3.4", False, "2.3"), "high")

    def test_the_comment_does_not_claim_a_priority_the_planner_does_not_produce(self):
        comment = " ".join(self.comment().split())
        self.assertNotIn("attack runs: max", comment)
        self.assertIn("priority_for", comment)
        for word in ("max", "high", "default"):
            with self.subTest(priority=word):
                self.assertIn(word, comment)

    def test_a_differential_task_is_not_prioritised_at_all(self):
        from plan import presets as presets_mod

        for name in presets_mod.RUNNABLE:
            preset = presets_mod.get(name)
            if preset.mode != "differential":
                continue
            with self.subTest(preset=name):
                self.assertFalse(preset.prioritise)
                self.assertEqual(self.rendered(name, "Lemma 2.3.4", True, "2.3"), "default")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
