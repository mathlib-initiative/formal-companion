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

    def test_no_accessor_can_be_walked_out_of_the_workspace(self):
        paths = WorkspacePaths(root=self.ROOT)
        for label, call in self.ACCESSORS:
            for bad in self.HOSTILE:
                with self.subTest(accessor=label, id=bad):
                    with self.assertRaises(CfError):
                        call(paths, bad)

    def test_the_reviewers_three_one_liners(self):
        """The exact values the finding printed: /tmp/pwned, /etc, and a sibling."""
        paths = WorkspacePaths(root=self.ROOT)
        for call in (lambda: paths.source_text("../../../../tmp/pwned"),
                     lambda: paths.source_text("/etc/cron.d/evil"),
                     lambda: paths.accepted_jsonl("../../evil")):
            with self.assertRaises(CfError) as caught:
                call()
            self.assertIn("unsafe", caught.exception.message)

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

if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
