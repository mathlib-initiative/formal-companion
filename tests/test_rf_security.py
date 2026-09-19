"""Regression tests for the security hardening of the review pass.

One test per finding it closes, each written so that it fails against the code
as it was:

* ``WorkspacePaths`` joined ids with ``/`` and validated nothing, so an absolute
  or ``..`` id wrote outside the workspace (pathlib discards the root for an
  absolute operand);
* the errata fetch had no size, redirect or scheme cap, and the page it writes
  is handed to workers as the document that decides whether a deviation is a
  correction;
* the no-push story was stated backwards: the pre-push hook is a reminder,
  ``cf init``'s refusal of a remote is the control, and neither `cf init` nor
  `cf status` said when the reminder was missing or a remote had appeared;
* ``dump_deps.sh`` ran a bare ``git status`` in the companion tree -- the index
  refresh that executes ``core.fsmonitor`` from the repo's own config;
* the agentbox bridge launched both hosts with their permission and sandbox
  checks off, by default, on the box that holds the companion checkouts;
* preflight printed ``tend --version`` and compared it with nothing.

A second pass added the cases an independent review of that work found: a
non-string id coerced into a legal path, a nested workspace whose guard `cf
status` never looked at, a standing warning about a deliberate ``--no-git``, a
before-digest recomputed from a moving local checkout, a run.yaml record that
claimed to know whether the sandbox was bypassed, a worker allowlist that denied
the gate its own prompt orders it to run, and a refused remote step whose record
went out with the exception.

Stdlib unittest only, no network, and nothing outside a temp directory.
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

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib.errors import CfError  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


# ==========================================================================
# security-safety#3 -- WorkspacePaths is the boundary CLAUDE.md S5.3 says it is
# ==========================================================================
class WorkspacePathIdsTest(unittest.TestCase):
    ROOT = Path("/ws")

    #: every accessor that takes an id, with the id in the position that is
    #: interpolated into a directory name or a filename
    ACCESSORS = (
        ("source_dir", lambda p, bad: p.source_dir(bad)),
        ("source_text", lambda p, bad: p.source_text(bad)),
        ("source_meta", lambda p, bad: p.source_meta(bad)),
        ("source_items", lambda p, bad: p.source_items(bad)),
        ("source_pages_dir", lambda p, bad: p.source_pages_dir(bad)),
        ("source_images_dir", lambda p, bad: p.source_images_dir(bad)),
        ("source_errata_md", lambda p, bad: p.source_errata_md(bad)),
        ("source_errata_raw", lambda p, bad: p.source_errata_raw(bad)),
        ("source_errata_json", lambda p, bad: p.source_errata_json(bad)),
        ("companion_dir", lambda p, bad: p.companion_dir(bad)),
        ("adapter_yaml", lambda p, bad: p.adapter_yaml(bad)),
        ("statements_jsonl", lambda p, bad: p.statements_jsonl(bad)),
        ("state_json", lambda p, bad: p.state_json(bad)),
        ("pairs_dir", lambda p, bad: p.pairs_dir(bad)),
        ("pairs_jsonl", lambda p, bad: p.pairs_jsonl(bad)),
        ("queue_json", lambda p, bad: p.queue_json(bad)),
        ("targets_json/source", lambda p, bad: p.targets_json(bad, "ours")),
        ("targets_json/name", lambda p, bad: p.targets_json("src", bad)),
        ("def_coverage_json/name", lambda p, bad: p.def_coverage_json("src", bad)),
        ("uncovered_json/name", lambda p, bad: p.uncovered_json("src", bad)),
        ("run_dir", lambda p, bad: p.run_dir(bad)),
        ("run_yaml", lambda p, bad: p.run_yaml(bad)),
        ("gate_json", lambda p, bad: p.gate_json(bad)),
        ("tend_root", lambda p, bad: p.tend_root(bad)),
        ("corpus_run_dir", lambda p, bad: p.corpus_run_dir(bad)),
        ("receipt_json", lambda p, bad: p.receipt_json(bad)),
        ("accepted_jsonl", lambda p, bad: p.accepted_jsonl(bad)),
        ("refuted_jsonl", lambda p, bad: p.refuted_jsonl(bad)),
        ("adjudicated_md", lambda p, bad: p.adjudicated_md(bad)),
        ("routed_jsonl", lambda p, bad: p.routed_jsonl(bad)),
        ("detect_companion_dir", lambda p, bad: p.detect_companion_dir(bad)),
        ("tally_json", lambda p, bad: p.tally_json(bad)),
        ("union_json", lambda p, bad: p.union_json(bad)),
    )

    HOSTILE = ("../../../../tmp/pwned", "/etc/cron.d/evil", "..", ".", "", "   ",
               "a/b", "a\\b", "\x00evil")

    #: a non-string is a CALLER that lost its id. `str(value)` would have coerced
    #: these into the legal names `sources/None` and `sources/7`, where the
    #: unvalidated code raised TypeError: the hardening must not turn a loud
    #: programming error into a quietly wrong path.
    NOT_STRINGS = (None, 7, 0, b"bytes", Path("/etc"), ["a"])

    def test_no_accessor_can_be_walked_out_of_the_workspace(self):
        paths = WorkspacePaths(root=self.ROOT)
        for label, call in self.ACCESSORS:
            for bad in self.HOSTILE:
                with self.subTest(accessor=label, id=bad):
                    with self.assertRaises(CfError):
                        call(paths, bad)

    def test_a_non_string_id_is_refused_and_not_coerced(self):
        paths = WorkspacePaths(root=self.ROOT)
        for label, call in self.ACCESSORS:
            for bad in self.NOT_STRINGS:
                with self.subTest(accessor=label, id=bad):
                    with self.assertRaises(CfError) as caught:
                        call(paths, bad)
                    self.assertIn("not a string", caught.exception.message)

    def test_the_reviewers_three_one_liners(self):
        """The exact values the finding printed: /tmp/pwned, /etc, and a sibling."""
        paths = WorkspacePaths(root=self.ROOT)
        for call in (lambda: paths.source_text("../../../../tmp/pwned"),
                     lambda: paths.source_text("/etc/cron.d/evil"),
                     lambda: paths.accepted_jsonl("../../evil")):
            with self.assertRaises(CfError) as caught:
                call()
            self.assertIn("unsafe", caught.exception.message)

    def test_a_run_naming_no_source_still_gets_its_own_refusal(self):
        """The new check must not shadow a command's own, better, error.

        `cf run --local` builds `pairs/<source>/queue.json` before it refuses a
        run.yaml that names no source, so the empty id reached the path helpers
        first and the user was told "unsafe source_id ''" instead of which flag
        settles it.
        """
        root = scratch(self)
        (root / "workspace.yaml").write_text(
            "version: 2\nworkspace:\n  name: demo\ncompanions: {}\nsources: {}\n"
            "hosts:\n  allowlist: []\n", encoding="utf-8")
        run_dir = root / "runs" / "demo"
        run_dir.mkdir(parents=True)
        (run_dir / "run.yaml").write_text("run_id: demo\nmode: differential\n",
                                          encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("CF_WORKSPACE", None)
        res = subprocess.run([sys.executable, str(SCRIPTS / "cf.py"), "--workspace", str(root),
                              "run", "demo", "--local"],
                             capture_output=True, text=True, env=env, cwd=str(REPO))
        self.assertNotEqual(res.returncode, 0)
        output = res.stdout + res.stderr
        self.assertIn("names no source", output)
        self.assertIn("cf plan-run --source", output)
        self.assertNotIn("unsafe source_id", output)

    def test_real_ids_still_work_and_stay_inside(self):
        """An interior dot is a legal id: only a separator or `..` is a traversal."""
        paths = WorkspacePaths(root=self.ROOT)
        for good in ("tao-measure-gsm126-published", "smith-analysis-1.2", "ours",
                     "attack-vac-2026-09-19", "notes-a"):
            for label, call in self.ACCESSORS:
                with self.subTest(accessor=label, id=good):
                    result = call(paths, good)
                    self.assertEqual(os.path.normpath(result), str(result))
                    self.assertTrue(str(result).startswith(str(self.ROOT) + os.sep), result)


# ==========================================================================
# security-safety#4 -- the errata fetch is bounded (first half: the curl caps)
# ==========================================================================
class ErrataFetchCapsTest(unittest.TestCase):
    def setUp(self):
        from sources import fetch_errata

        self.mod = fetch_errata

    def test_curl_argv_caps_size_redirects_and_scheme(self):
        argv = self.mod.curl_argv("/usr/bin/curl", "https://example.invalid/errata",
                                  "/tmp/out.html", 60)
        self.assertIn("--max-filesize", argv)
        self.assertEqual(argv[argv.index("--max-filesize") + 1], self.mod.MAX_FILESIZE)
        self.assertIn("--max-redirs", argv)
        self.assertEqual(argv[argv.index("--max-redirs") + 1], self.mod.MAX_REDIRS)
        # https only on the request AND on every redirect: curl blocks file:// on
        # redirect by itself, but not ftp://
        self.assertEqual(argv[argv.index("--proto") + 1], "=https")
        self.assertEqual(argv[argv.index("--proto-redir") + 1], "=https")
        self.assertIn("--max-time", argv)
        # the effective URL is collected, so a redirect is in the audit trail
        self.assertIn("%{url_effective}", argv[argv.index("-w") + 1])

    def test_a_plaintext_url_is_refused_by_name(self):
        for url in ("http://169.254.169.254/latest/meta-data/",
                    "http://127.0.0.1:8000/errata.html"):
            with self.assertRaises(ValueError) as caught:
                self.mod.curl(url)
            self.assertIn("https", str(caught.exception))

    def test_a_non_http_url_is_still_refused(self):
        with self.assertRaises(ValueError):
            self.mod.curl("file:///etc/passwd")

    def _fake_curl(self, body: bytes, stdout: str):
        """Replace the module's own `subprocess`/`shutil` handles, nothing global."""
        import subprocess as sp
        import types
        from unittest import mock

        def fake_run(argv, capture_output=True, text=True):
            with open(argv[argv.index("-o") + 1], "wb") as fh:
                fh.write(body)
            return sp.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        patches = [
            mock.patch.object(self.mod, "subprocess", types.SimpleNamespace(run=fake_run)),
            mock.patch.object(self.mod, "shutil",
                              types.SimpleNamespace(which=lambda name: "/usr/bin/" + name)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_an_oversized_body_is_refused_after_the_transfer(self):
        """--max-filesize is a no-op when the response declares no length."""
        self._fake_curl(b"x" * (self.mod.MAX_BYTES + 1), "200\nhttps://example.invalid/e\n")
        with self.assertRaises(RuntimeError) as caught:
            self.mod.curl("https://example.invalid/e")
        self.assertIn("over the", str(caught.exception))

    def test_the_effective_url_is_recorded_and_a_redirect_is_visible(self):
        landed = "https://elsewhere.invalid/errata-page"
        self._fake_curl(b"<html><body><p>Errata</p><ul><li>page 71: fix</li></ul>"
                        b"</body></html>", "200\n%s\n" % landed)
        out_dir = scratch(self)
        meta = self.mod.fetch("https://example.invalid/errata", str(out_dir),
                             source_id="src", source_sha="a" * 64, provenance="registry")
        self.assertEqual(meta["url_effective"], landed)
        self.assertTrue(meta["url_redirected"])
        self.assertEqual(json.loads((out_dir / "errata.json").read_text())["url_effective"],
                         landed)
        self.assertIn("REDIRECTED to: %s" % landed,
                      (out_dir / "errata.md").read_text(encoding="utf-8"))
        # the raw HTML is still kept verbatim: that is why this fetcher exists
        self.assertIn("<li>", (out_dir / "errata.raw.html").read_text(encoding="utf-8"))


# ==========================================================================
# security-safety#5 -- the hook is a reminder, and its absence must be visible
# ==========================================================================
class NoPushGuardTest(unittest.TestCase):
    def _workspace(self) -> Path:
        root = scratch(self) / "ws"
        self._cf("init", str(root))
        return root

    def _cf(self, *args) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("CF_WORKSPACE", None)
        return subprocess.run([sys.executable, str(SCRIPTS / "cf.py"), *args],
                              capture_output=True, text=True, env=env, cwd=str(REPO))

    def test_a_foreign_pre_push_hook_is_reported_not_swallowed(self):
        root = scratch(self) / "ws"
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True,
                       capture_output=True, text=True)
        hook = root / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        res = self._cf("init", str(root))
        self.assertEqual(res.returncode, 0, res.stderr)
        # it used to be log.detail ("hook exists; left untouched"), i.e. invisible
        # at default verbosity, and cf init still exited 0
        self.assertIn("NOT the cf guard", res.stdout + res.stderr)
        self.assertEqual(hook.read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")

    def test_status_reports_no_remote_and_the_hook_every_run(self):
        root = self._workspace()
        res = self._cf("--workspace", str(root), "status")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("no remote", res.stdout)
        self.assertIn("cf pre-push reminder installed", res.stdout)

    def test_status_warns_about_a_remote_added_after_init(self):
        """The case nothing caught: `cf init` refused one, then one appeared."""
        root = self._workspace()
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin",
                        str(scratch(self) / "bare.git")], check=True,
                       capture_output=True, text=True)
        res = self._cf("--workspace", str(root), "status")
        self.assertIn("remote origin", res.stdout)
        self.assertIn("HAS a remote", res.stderr)
        self.assertIn("stays local", res.stderr)

    def test_status_warns_when_the_hook_has_gone(self):
        root = self._workspace()
        (root / ".git" / "hooks" / "pre-push").unlink()
        res = self._cf("--workspace", str(root), "status")
        self.assertIn("NO cf pre-push reminder", res.stdout + res.stderr)
        self.assertIn("--no-verify", res.stderr)

    def test_status_json_carries_both_facts(self):
        root = self._workspace()
        res = self._cf("--workspace", str(root), "status", "--json")
        git = json.loads(res.stdout)["workspace"]["git"]
        self.assertEqual(git["remotes"], [])
        self.assertEqual(git["pre_push_hook"], "cf")

    def _nested_workspace(self) -> tuple[Path, Path]:
        """`cf init --allow-nested`: no .git of its own, the guard in the outer repo."""
        outer = (scratch(self) / "outer").resolve()   # /var -> /private/var on macOS
        outer.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(outer)], check=True,
                       capture_output=True, text=True)
        root = outer / "ws"
        res = self._cf("init", str(root), "--allow-nested")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse((root / ".git").exists())
        self.assertIn("CF_ALLOW_PUSH",
                      (outer / ".git" / "hooks" / "pre-push").read_text(encoding="utf-8"))
        return outer, root

    def test_status_names_the_enclosing_repo_it_is_guarded_by(self):
        """A nested workspace was reported as "not a git repo" with all three
        clauses false: a reminder WAS installed, `cf init` would not create a repo,
        and the outer repo's remotes were never looked at."""
        outer, root = self._nested_workspace()
        res = self._cf("--workspace", str(root), "status")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(f"guarding {outer}", res.stdout)
        self.assertIn("nested inside it", res.stdout)
        self.assertIn("no remote (the workspace stays local)", res.stdout)
        self.assertIn("cf pre-push reminder installed", res.stdout)
        self.assertNotIn("is not a git repo", res.stdout + res.stderr)

    def test_status_warns_about_a_remote_on_the_enclosing_repo(self):
        """The authoritative case: a remote added after init, on the repo that
        actually holds the workspace's corpus and ledgers."""
        outer, root = self._nested_workspace()
        subprocess.run(["git", "-C", str(outer), "remote", "add", "origin",
                        str(scratch(self) / "bare.git")], check=True,
                       capture_output=True, text=True)
        res = self._cf("--workspace", str(root), "status")
        self.assertIn("remote origin", res.stdout)
        self.assertIn("HAS a remote", res.stderr)
        self.assertIn(f"git -C {outer} remote remove origin", res.stderr)

    def test_a_no_git_workspace_is_stated_not_warned_about(self):
        """`--no-git` is documented: `cf status` must not tell the operator on
        every run to undo a flag they passed deliberately."""
        root = scratch(self) / "ws"
        res = self._cf("init", str(root), "--no-git")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("git: false", (root / "workspace.yaml").read_text(encoding="utf-8"))
        res = self._cf("--workspace", str(root), "status")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("no git repo (created with --no-git)", res.stdout)
        self.assertNotIn("is not a git repo", res.stderr)

    def test_a_workspace_that_lost_its_repo_is_still_warned_about(self):
        """The warning stays for the case it was written for."""
        root = self._workspace()
        shutil.rmtree(root / ".git")
        res = self._cf("--workspace", str(root), "status")
        self.assertIn("is not a git repo", res.stderr)

    def test_the_hook_and_the_docs_say_which_one_is_the_control(self):
        from cfcmd import init as init_cmd

        self.assertIn("--no-verify", init_cmd.PRE_PUSH_HOOK)
        self.assertIn("REMINDER, NOT THE CONTROL", init_cmd.PRE_PUSH_HOOK)
        traps = (REPO / "references" / "traps.md").read_text(encoding="utf-8")
        self.assertNotIn('because "no remote" is a default and not a control', traps)
        self.assertIn("the workspace itself stays local", traps)

    def test_hook_state_recognises_an_older_cf_hook(self):
        """Byte equality would call every workspace made by an older cf 'foreign'."""
        from cfcmd import init as init_cmd

        root = self._workspace()
        hook = root / ".git" / "hooks" / "pre-push"
        hook.write_text('#!/bin/sh\nif [ "$CF_ALLOW_PUSH" != "1" ]; then exit 1; fi\n',
                        encoding="utf-8")
        self.assertEqual(init_cmd.hook_state(root), "cf")


# ==========================================================================
# security-safety#6 -- dump_deps.sh: gitsafe inline, and say what it executes
# ==========================================================================
class DumpDepsTrustTest(unittest.TestCase):
    SCRIPT = SCRIPTS / "detect" / "dump_deps.sh"

    def test_the_two_git_calls_pin_the_repos_config(self):
        text = self.SCRIPT.read_text(encoding="utf-8")
        from cflib import gitsafe

        for i in range(0, len(gitsafe.SAFE_CONFIG), 2):
            self.assertIn(gitsafe.SAFE_CONFIG[i + 1], text,
                          "dump_deps.sh must inline every gitsafe config key")
        for key, value in gitsafe.SAFE_ENV.items():
            self.assertIn(f"{key}={value}", text)
        # no bare `git -C` left: an index refresh executes core.fsmonitor from the
        # companion's own .git/config
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("git -C") or " git -C " in stripped:
                self.fail(f"bare git call in dump_deps.sh: {stripped}")

    def test_the_header_says_it_runs_the_companions_code_as_you(self):
        head = "\n".join(self.SCRIPT.read_text(encoding="utf-8").splitlines()[:30])
        self.assertIn("lake env lean --run", head)
        self.assertIn("invoking user", head)
        self.assertTrue("trust" in head and "sandbox" in head, head)

    def test_gitsafe_names_the_one_exception_to_builds_nothing(self):
        from cflib import gitsafe

        self.assertIn("dump_deps.sh", gitsafe.__doc__ or "")

    def test_the_script_is_valid_bash_and_help_still_shows_usage(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(self.SCRIPT)],
                                        capture_output=True, text=True).returncode, 0)
        res = subprocess.run(["bash", str(self.SCRIPT), "--help"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Usage:", res.stdout)
        self.assertIn("--module-prefix", res.stdout)

    def test_a_dry_run_still_checks_the_tree_with_the_safe_git(self):
        tmp = scratch(self)
        repo = tmp / "companion"
        (repo / ".lake").mkdir(parents=True)
        res = subprocess.run(
            ["bash", str(self.SCRIPT), "--repo", str(repo), "--out", str(tmp / "deps.json"),
             "--root-module", "M", "--module-prefix", "M.Tao", "--dry-run"],
            capture_output=True, text=True, cwd=str(tmp))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("lake env lean --run", res.stdout)


# ==========================================================================
# prompts-workflows#10 / security-safety#2 -- the bridge denies by default
# ==========================================================================
class AgentboxBridgePermissionsTest(unittest.TestCase):
    SCRIPT = SCRIPTS / "run" / "bridge-agentbox.sh"

    def setUp(self):
        self.text = self.SCRIPT.read_text(encoding="utf-8")

    def test_it_is_valid_bash(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(self.SCRIPT)],
                                        capture_output=True, text=True).returncode, 0)

    def _launch(self, *, role: str = "worker", host: str = "claude",
                bypass: str | None = None, env: dict | None = None) -> list[str]:
        """Run the bridge with a fake `agentbox` on PATH; return the host's argv."""
        from run.manifest import BYPASS_ENV

        tmp = scratch(self)
        prompts = tmp / "prompts"
        prompts.mkdir()
        (prompts / f"{role}-system.md").write_text("system\n", encoding="utf-8")
        (prompts / f"{role}.md").write_text("task\n", encoding="utf-8")
        bindir = tmp / "bin"
        bindir.mkdir()
        argv_file = tmp / "argv.txt"
        (bindir / "agentbox").write_text(
            '#!/bin/sh\nfor a in "$@"; do printf \'%s\\n\' "$a"; done > "$CF_TEST_ARGV"\n'
            'printf \'{}\\n\'\n', encoding="utf-8")
        (bindir / "agentbox").chmod(0o755)
        worktree = tmp / "worktree"
        worktree.mkdir()
        environ = dict(os.environ)
        environ.update({"PATH": f"{bindir}:{environ['PATH']}", "CF_TEST_ARGV": str(argv_file),
                        "CF_BRIDGE_ROOT": str(tmp), "CF_AGENT_HOST": host, "CF_ROLE": role,
                        "CF_AGENTBOX_PROFILE": "examplebox"})
        for key in (BYPASS_ENV, "CF_GATE_CMD", "CF_GATE_DIR", "CF_ALLOWED_TOOLS",
                    "CF_WORKER_TOOLS", "CF_REVIEWER_TOOLS"):
            environ.pop(key, None)
        environ.update(env or {})
        if bypass is not None:
            environ[BYPASS_ENV] = bypass
        env = environ
        subprocess.run(["bash", str(self.SCRIPT), role], capture_output=True, text=True,
                       cwd=str(worktree), env=env)
        self.assertTrue(argv_file.exists(), "the fake agentbox was never reached")
        return argv_file.read_text(encoding="utf-8").splitlines()

    def test_claude_denies_by_default_and_bypasses_only_on_the_opt_in(self):
        argv = self._launch(host="claude")
        self.assertFalse([a for a in argv if a.startswith("--dangerously")], argv)
        self.assertIn("--permission-mode", argv)
        self.assertIn("acceptEdits", argv)
        self.assertEqual(argv[argv.index("--allowedTools") + 1],
                         "Read,Glob,Grep,Edit,Write,Bash(git:*)")
        argv = self._launch(host="claude", bypass="1")
        self.assertIn("--dangerously-skip-permissions", argv)

    def test_codex_is_sandboxed_by_default_and_bypasses_only_on_the_opt_in(self):
        argv = self._launch(host="codex")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
        argv = self._launch(host="codex", bypass="1")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertNotIn("--sandbox", argv)

    def test_the_reviewer_gets_a_read_only_surface(self):
        argv = self._launch(role="reviewer", host="claude")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Glob,Grep,Bash(git:*)")

    def test_the_worker_role_has_an_allowlist_like_the_local_bridge(self):
        local = (SCRIPTS / "run" / "bridge-claude-p.sh").read_text(encoding="utf-8")
        for tools in ("Read,Glob,Grep,Edit,Write,Bash(git:*)", "Read,Glob,Grep,Bash(git:*)"):
            self.assertIn(tools, local)          # the reference posture
            self.assertIn(tools, self.text)      # now the agentbox bridge too

    def test_the_default_worker_allowlist_permits_its_own_gate_run(self):
        """worker.md orders the worker to run the gate before committing; an
        allowlist that denied it would cost a rework turn per task."""
        gate = "/home/tester/demo-gate"
        argv = self._launch(host="claude", env={
            "CF_GATE_CMD": f"python3 {gate}/verify_findings.py --gate-config {gate}/gate.json"})
        tools = argv[argv.index("--allowedTools") + 1].split(",")
        self.assertIn(f"Bash(python3 {gate}/verify_findings.py:*)", tools)
        # narrow on purpose: python3 in general stays denied, because the worker
        # must not be able to substitute its own gate
        self.assertNotIn("Bash(python3:*)", tools)
        # and the gate is derived, not baked in: no CF_GATE_CMD, no entry
        argv = self._launch(host="claude")
        self.assertEqual(argv[argv.index("--allowedTools") + 1],
                         "Read,Glob,Grep,Edit,Write,Bash(git:*)")

    def test_the_bypass_record_says_only_what_the_wrapper_can_tell_it(self):
        """Tri-state, not a boolean: nothing in the repo stages the opt-in, so a
        bare `false` claimed a fact `cf run` cannot know -- the environment that
        launches tend on the host can set it with no wrapper involved."""
        from run import manifest
        from run.manifest import BYPASS_ENV

        run_dir = scratch(self) / "runs" / "demo"
        bindir = run_dir / "tend-root" / "bin"
        bindir.mkdir(parents=True)
        (bindir / "worker-agent.sh").write_text(
            "#!/bin/sh\nexport CF_AGENT_HOST=claude\n"
            "export CF_ALLOWED_TOOLS=Read,Grep\n", encoding="utf-8")
        facts = manifest.agent_permissions(run_dir)
        self.assertFalse(facts["permission_bypass_in_wrapper"])
        self.assertIn("not requested in the staged wrapper", facts["permission_bypass"])
        self.assertIn("host can still set it", facts["permission_bypass"])
        self.assertEqual(facts["env"]["CF_ALLOWED_TOOLS"], "Read,Grep")
        # and it must not tell the operator to edit a file the repo generates
        self.assertNotIn("in the wrapper to opt out", facts["default"])

        (bindir / "worker-agent.sh").write_text(
            f"#!/bin/sh\nexport {BYPASS_ENV}=1\n", encoding="utf-8")
        facts = manifest.agent_permissions(run_dir)
        self.assertTrue(facts["permission_bypass_in_wrapper"])
        self.assertIn("requested", facts["permission_bypass"])
        self.assertEqual(facts["bypass_exported_by"], ["worker-agent.sh"])

    def test_the_bridge_header_does_not_promise_a_record_it_cannot_keep(self):
        self.assertNotIn("so a bypassed run is visible afterwards", self.text)
        self.assertIn("whether the STAGED WRAPPER", self.text)
        self.assertIn("agentbox-<role>.err is the record that is", self.text)


# ==========================================================================
# prompts-workflows#10 -- the transferred companion trees are hashed and checked
# ==========================================================================
class CompanionTreeIntegrityTest(unittest.TestCase):
    def _tree(self) -> Path:
        root = scratch(self) / "companion"
        (root / "Mod").mkdir(parents=True)
        (root / "Mod" / "A.lean").write_text("theorem a : True := trivial\n", encoding="utf-8")
        (root / "lakefile.toml").write_text("name = \"mod\"\n", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        (root / ".lake").mkdir()
        (root / ".lake" / "big.olean").write_bytes(b"\x00" * 16)
        return root

    def test_the_digest_covers_what_rsync_sends_and_nothing_else(self):
        from run import manifest

        root = self._tree()
        digest = manifest.tree_digest(root, manifest.SOURCE_EXCLUDES)
        self.assertEqual(digest["files"], 2)          # .git/ and .lake/ excluded
        before = digest["sha256"]
        (root / ".git" / "config").write_text("[core]\n\tfsmonitor = /bin/sh\n",
                                              encoding="utf-8")
        self.assertEqual(manifest.tree_digest(root, manifest.SOURCE_EXCLUDES)["sha256"],
                         before, "an excluded file must not move the digest")
        (root / "Mod" / "A.lean").write_text("theorem a : False := sorry\n", encoding="utf-8")
        self.assertNotEqual(manifest.tree_digest(root, manifest.SOURCE_EXCLUDES)["sha256"],
                            before, "an edited .lean file must move it")

    def test_the_host_side_snippet_computes_the_same_digest(self):
        """The two copies of the algorithm are pinned against each other."""
        from run import manifest

        root = self._tree()
        expected = manifest.tree_digest(root, manifest.SOURCE_EXCLUDES)
        res = subprocess.run([sys.executable, "-c", manifest.REMOTE_TREE_DIGEST_PY,
                              str(root), expected["sha256"], *manifest.SOURCE_EXCLUDES],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("sha256=%s" % expected["sha256"], res.stdout)
        self.assertIn("files=%d" % expected["files"], res.stdout)

    def test_the_host_side_snippet_refuses_a_tree_that_changed(self):
        from run import manifest

        root = self._tree()
        stale = manifest.tree_digest(root, manifest.SOURCE_EXCLUDES)["sha256"]
        # what a chmod-and-edit looks like: 0444 is reversible by its owner
        target = root / "Mod" / "A.lean"
        target.chmod(0o444)
        target.chmod(0o644)
        target.write_text("theorem a : True := by trivial\n", encoding="utf-8")
        res = subprocess.run([sys.executable, "-c", manifest.REMOTE_TREE_DIGEST_PY,
                              str(root), stale, *manifest.SOURCE_EXCLUDES],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("REFUSED", res.stderr)

    def test_the_plan_hashes_each_repo_before_and_after(self):
        from run import manifest, remote

        host = {"name": "examplebox", "ssh": "tester@examplebox",
                "remote_root": "/home/tester", "tend_cmd": "uv run",
                "agentbox_profile": "", "disk_budget_bytes": 10 ** 10,
                "tend_version_required": ""}
        workspace_dir = scratch(self)
        run_dir = workspace_dir / "runs" / "demo"
        (run_dir / "tasks").mkdir(parents=True)
        (run_dir / "tasks" / "t.yaml").write_text("id: t\n", encoding="utf-8")
        repo = self._tree()
        run = {"run_id": "demo", "sources": [], "remote": {}}
        entries, _ = manifest.build_manifest(
            workspace_dir=workspace_dir, skill_dir=REPO, run_id="demo", run=run,
            host=host, include_sources=True, include_texts=False,
            companions={"ours": str(repo)})
        entry = [e for e in entries if e["name"] == "repo:ours"][0]
        self.assertEqual(entry["tree_sha256"],
                         manifest.tree_digest(repo, manifest.SOURCE_EXCLUDES)["sha256"])
        steps = remote.plan_commands(
            host=host, run_id="demo", run=run, entries=entries,
            names=manifest.remote_names(run, "demo"),
            budget={"max_cost": 5.0, "currency": "USD"},
            concurrency={"workers": 1, "reviewers": 1})
        checks = [s for s in steps if s.get("expected") == entry["tree_sha256"]]
        self.assertEqual(sorted(s["stage"] for s in checks), ["monitor", "verify"])
        for step in checks:
            self.assertIn(entry["tree_sha256"], step["shell"])
            self.assertTrue(step["record_output"])
            self.assertTrue(step["safe"])

    def _cf_run(self, root: Path, *extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("CF_WORKSPACE", None)
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "cf.py"), "--workspace", str(root), "run", "demo",
             "--host", "examplebox", "--i-have-consent", "--max-cost", "5", *extra],
            capture_output=True, text=True, env=env, cwd=str(REPO))

    def _workspace(self) -> tuple[Path, Path, Path]:
        """A workspace with one companion repo and one planned run. (root, repo, run_dir)"""
        root = scratch(self)
        repo = self._tree()
        source_id = "smith-analysis-2e"
        (root / "workspace.yaml").write_text(
            "version: 2\nworkspace:\n  name: demo\n"
            "companions:\n  ours:\n"
            f"    repo: {repo}\n"
            "    adapter: docstring_tags\n"
            f"    sources: [{source_id}]\n"
            f"    anchor_source: {source_id}\n"
            "sources:\n"
            f"  {source_id}:\n"
            f'    sha256: "{"b" * 64}"\n'
            "    title: A Synthetic Analysis Text\n"
            '    edition: "2nd edition"\n'
            "    offset: 12\n"
            "    role: audited\n"
            "hosts:\n  allowlist: [examplebox]\n  remote_root: /home/tester\n"
            "  examplebox:\n    ssh: tester@examplebox\n    tend_version: \">=0.4\"\n",
            encoding="utf-8")
        run_dir = root / "runs" / "demo"
        (run_dir / "tasks").mkdir(parents=True)
        (run_dir / "tasks" / "t.yaml").write_text("id: t\n", encoding="utf-8")
        (run_dir / "run.yaml").write_text(
            "run_id: demo\nmode: differential\nrepos: [ours]\nsources: []\n", encoding="utf-8")
        bindir = run_dir / "tend-root" / "bin"
        bindir.mkdir(parents=True)
        (bindir / "worker-agent.sh").write_text("#!/bin/sh\nexport CF_AGENT_HOST=claude\n",
                                                encoding="utf-8")
        return root, repo, run_dir

    def test_a_monitor_only_run_still_plans_the_integrity_check(self):
        """`stages_upto("monitor")` is ["monitor"] alone, so the check runs in a
        LATER invocation. It used to exist only if that invocation repeated
        --include-sources: forgetting the flag removed the one check that catches a
        chmod-and-edit, and then rewrote include_sources: false over the true."""
        from run import manifest

        root, repo, run_dir = self._workspace()
        sent = manifest.tree_digest(repo, manifest.SOURCE_EXCLUDES)["sha256"]
        first = self._cf_run(root, "--include-sources", "--stage", "verify")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        recorded = (run_dir / "run.yaml").read_text(encoding="utf-8")
        self.assertIn(sent, recorded, "run.yaml must pin the digest that was sent")

        later = self._cf_run(root, "--stage", "monitor")      # no --include-sources
        self.assertEqual(later.returncode, 0, later.stdout + later.stderr)
        self.assertIn(sent, later.stdout, "the monitor plan must still demand the digest")
        self.assertIn("unchanged since the transfer", later.stdout)
        self.assertIn("include_sources: true", later.stdout + later.stderr)
        self.assertIn("include_sources: true",
                      (run_dir / "run.yaml").read_text(encoding="utf-8"))

    def test_the_pinned_digest_is_not_rewalked_from_the_local_checkout(self):
        """An operator who pulled the companion since the transfer must not be told
        an agent edited the repo the findings cite."""
        from run import manifest

        root, repo, run_dir = self._workspace()
        sent = manifest.tree_digest(repo, manifest.SOURCE_EXCLUDES)["sha256"]
        first = self._cf_run(root, "--include-sources", "--stage", "verify")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

        (repo / "Mod" / "B.lean").write_text("theorem b : True := trivial\n", encoding="utf-8")
        pulled = manifest.tree_digest(repo, manifest.SOURCE_EXCLUDES)["sha256"]
        self.assertNotEqual(sent, pulled)

        later = self._cf_run(root, "--stage", "monitor")
        self.assertIn(sent, later.stdout)
        self.assertNotIn(pulled, later.stdout)

    def test_a_dry_run_records_the_agent_posture_and_prints_the_checks(self):
        """End to end through `cf run`: run.yaml gains the record, no transfer happens."""
        root = scratch(self)
        repo = self._tree()
        source_id = "smith-analysis-2e"
        (root / "workspace.yaml").write_text(
            "version: 2\nworkspace:\n  name: demo\n"
            "companions:\n  ours:\n"
            f"    repo: {repo}\n"
            "    adapter: docstring_tags\n"
            f"    sources: [{source_id}]\n"
            f"    anchor_source: {source_id}\n"
            "sources:\n"
            f"  {source_id}:\n"
            f'    sha256: "{"b" * 64}"\n'
            "    title: A Synthetic Analysis Text\n"
            '    edition: "2nd edition"\n'
            "    offset: 12\n"
            "    role: audited\n"
            "hosts:\n  allowlist: [examplebox]\n  remote_root: /home/tester\n"
            "  examplebox:\n    ssh: tester@examplebox\n    tend_version: \">=0.4\"\n",
            encoding="utf-8")
        run_dir = root / "runs" / "demo"
        (run_dir / "tasks").mkdir(parents=True)
        (run_dir / "tasks" / "t.yaml").write_text("id: t\n", encoding="utf-8")
        (run_dir / "run.yaml").write_text(
            "run_id: demo\nmode: differential\nrepos: [ours]\nsources: []\n", encoding="utf-8")
        bindir = run_dir / "tend-root" / "bin"
        bindir.mkdir(parents=True)
        (bindir / "worker-agent.sh").write_text("#!/bin/sh\nexport CF_AGENT_HOST=claude\n",
                                                encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("CF_WORKSPACE", None)
        res = subprocess.run(
            [sys.executable, str(SCRIPTS / "cf.py"), "--workspace", str(root), "run", "demo",
             "--host", "examplebox", "--i-have-consent", "--include-sources", "--max-cost", "5",
             "--stage", "verify"],
            capture_output=True, text=True, env=env, cwd=str(REPO))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("nothing was transferred", res.stdout)
        from run import manifest

        digest = manifest.tree_digest(repo, manifest.SOURCE_EXCLUDES)["sha256"]
        self.assertIn(digest, res.stdout, "the plan must demand the digest it sent")
        self.assertIn(">=0.4", res.stdout, "preflight must check tend's version")
        recorded = (run_dir / "run.yaml").read_text(encoding="utf-8")
        self.assertIn("permission_bypass", recorded)
        self.assertIn(manifest.BYPASS_ENV, recorded)

    def test_execute_records_the_digest_output_for_run_yaml(self):
        import io

        from run import remote

        steps = [{"stage": "verify", "title": "repo:ours landed", "safe": True,
                  "record_output": True, "expected": "d" * 64,
                  "argv": [sys.executable, "-c", "print('tree sha256=%s' % ('d' * 64))"],
                  "shell": "python3 -c ..."}]
        out = remote.execute(steps, ["verify"], dry_run=False, stream=io.StringIO())
        self.assertEqual(out[0]["expected"], "d" * 64)
        self.assertIn("sha256=" + "d" * 64, out[0]["stdout"])

    def test_a_refused_step_is_the_one_record_that_must_survive(self):
        """`execute` raises on a non-zero step, so a caller reading only the return
        value discarded exactly the outcome worth recording -- a refused tree digest
        or a refused tend version wrote nothing into run.yaml."""
        import io

        from run import remote

        steps = [
            {"stage": "verify", "title": "host reachable", "safe": True,
             "argv": [sys.executable, "-c", "pass"], "shell": "true"},
            {"stage": "verify", "title": "repo:ours landed byte for byte", "safe": True,
             "record_output": True, "expected": "d" * 64,
             "argv": [sys.executable, "-c",
                      "import sys; sys.stderr.write('REFUSED: not the tree\\n'); "
                      "sys.stdout.write('tree sha256=%s\\n' % ('e' * 64)); sys.exit(1)"],
             "shell": "python3 -c ..."},
        ]
        executed: list[dict] = []
        with self.assertRaises(CfError):
            remote.execute(steps, ["verify"], dry_run=False, stream=io.StringIO(),
                           results=executed)
        self.assertEqual(len(executed), 2)
        self.assertTrue(executed[-1]["failed"])
        self.assertEqual(executed[-1]["returncode"], 1)
        self.assertEqual(executed[-1]["expected"], "d" * 64)
        self.assertIn("sha256=" + "e" * 64, executed[-1]["stdout"])


# ==========================================================================
# critic#6 (code half) -- preflight compares tend's version, it does not print it
# ==========================================================================
class TendVersionPreflightTest(unittest.TestCase):
    def _host(self, required: str) -> dict:
        return {"name": "examplebox", "ssh": "tester@examplebox",
                "remote_root": "/home/tester", "tend_cmd": "uv run",
                "agentbox_profile": "", "disk_budget_bytes": 10 ** 10,
                "tend_version_required": required}

    def _preflight(self, required: str) -> dict:
        from run import remote

        steps = remote.plan_commands(
            host=self._host(required), run_id="demo", run={"run_id": "demo"}, entries=[],
            names={"entrypoint": "demo", "tend_root": "demo-tend", "gate": "demo-gate",
                   "sources": "sources", "repos": "repos"},
            budget={"max_cost": 1.0, "currency": "USD"},
            concurrency={"workers": 1, "reviewers": 1})
        return [s for s in steps if s["stage"] == "preflight"][0]

    def test_the_required_range_is_read_from_the_workspace(self):
        from cflib.workspace import Workspace
        from run import manifest

        root = scratch(self)
        (root / "workspace.yaml").write_text(
            "version: 2\nworkspace:\n  name: demo\ncompanions: {}\nsources: {}\n"
            "hosts:\n  allowlist: [examplebox]\n  examplebox:\n    ssh: tester@examplebox\n"
            "    tend_version: \">=0.4,<0.6\"\n", encoding="utf-8")
        host = manifest.resolve_host(Workspace.load(root), "examplebox")
        self.assertEqual(host["tend_version_required"], ">=0.4,<0.6")

    def test_preflight_compares_instead_of_only_printing(self):
        step = self._preflight(">=0.4,<0.6")
        self.assertIn("tend --version", step["shell"])
        self.assertIn(">=0.4,<0.6", step["shell"])
        self.assertTrue(step["record_output"])

    def test_with_nothing_recorded_it_says_it_is_not_checking(self):
        step = self._preflight("")
        self.assertIn("NOT checked", step["shell"])
        self.assertIn("tend_version", step["shell"])

    def _check(self, version: str, spec: str) -> subprocess.CompletedProcess:
        from run.remote import TEND_VERSION_CHECK

        return subprocess.run([sys.executable, "-c", TEND_VERSION_CHECK, spec],
                              input=version, capture_output=True, text=True)

    def test_the_version_comparison_accepts_and_refuses(self):
        for version, spec in (("tend 0.4.2", ">=0.4"), ("tend 0.5.0", ">=0.4,<0.6"),
                              ("0.4", ">=0.4,<0.6"), ("tend version 1.2.3", "==1.2.3")):
            with self.subTest(version=version, spec=spec):
                self.assertEqual(self._check(version, spec).returncode, 0)
        for version, spec in (("tend 0.3.9", ">=0.4"), ("tend 0.6.0", ">=0.4,<0.6"),
                              ("tend 1.0", "==0.9"), ("tend (unknown)", ">=0.4")):
            with self.subTest(version=version, spec=spec):
                res = self._check(version, spec)
                self.assertEqual(res.returncode, 1)
                self.assertIn("REFUSED", res.stderr)

    def test_the_refusal_names_what_settles_it(self):
        res = self._check("tend 0.3.0", ">=0.4")
        self.assertIn("hosts.<host>.tend_version", res.stderr)


if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
