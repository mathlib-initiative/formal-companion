"""Final-review fixes for the suite itself: what the tests were not testing.

Each case here fails against the tree as it was:

* three ``cf doctor`` cases stubbed ``shutil.which`` only for the tools they
  named and let everything else fall through to the machine, so asserting
  ``exit 0`` silently required poppler -- and no CI runner has poppler;
* the documented gate, ``python3 -m unittest discover -s tests``, loads no
  ``conftest.py``, so the HOME pin never applied to it and every onboarding test
  walked, sha256'd and ``pdftotext``'d the developer's own ``~/Downloads``;
* ``tests/e2e/multisource.sh`` defaulted its workspace to a directory inside the
  checkout, which ``cf init`` refuses, so the invocation CLAUDE.md documents
  exited 2 on every machine;
* two of the three run bridges had no test of any kind -- not even ``bash -n``
  -- and nothing linted the shipped shell at all;
* two assertions compared a value with itself, so they held for every
  implementation including one that ignores its arguments.

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

import ast
import json
import os
import re
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


def scratch(case: unittest.TestCase) -> Path:
    tmp = tempfile.TemporaryDirectory(prefix="cf-rf-final-")
    case.addCleanup(tmp.cleanup)
    return Path(tmp.name).resolve()


def child_env(**extra: str) -> dict:
    """A minimal environment for a child interpreter: no CF_* from this shell."""
    env = {"PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C.UTF-8"}
    for key in ("SYSTEMROOT", "TMPDIR"):
        if key in os.environ:
            env[key] = os.environ[key]
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# FR#16 -- the doctor cases must assert about `cf doctor`, not about the machine
# ---------------------------------------------------------------------------
class TestDoctorCasesAssertAboutTheCommand(unittest.TestCase):
    def test_they_pass_with_nothing_at_all_on_path(self):
        """The three exit-0 cases required poppler; every CI runner lacks it.

        Run in a child with an empty PATH, which is the strongest form of "not
        installed": a partial ``shutil.which`` stub then reports every tool the
        case did not name as missing, ``cf doctor`` exits 2 on the required
        ones, and the three ``assertEqual(rc, 0)`` lines fail.  ``sys.executable``
        is absolute, so the child still starts.
        """
        empty = scratch(self) / "bin"
        empty.mkdir()
        res = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_rf_core.TestDoctor"],
            cwd=str(SKILL), capture_output=True, text=True, timeout=CHILD_TIMEOUT,
            env=child_env(PATH=str(empty), HOME=str(scratch(self))))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("Ran 6 tests", res.stderr)


# ---------------------------------------------------------------------------
# FR#17 -- the documented gate is hermetic too, not only the pytest one
# ---------------------------------------------------------------------------

#: a module that builds a workspace expands `library_paths`, whose shipped entry
#: is `~/Downloads`; the grep is the same heuristic the finding used
_BUILDS_A_WORKSPACE = re.compile(r"onboard|cf init|library_paths")


class TestBothLoadersAreHermetic(unittest.TestCase):
    def test_every_module_that_builds_a_workspace_imports_the_pin(self):
        offenders = []
        for path in sorted(HERE.glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            if _BUILDS_A_WORKSPACE.search(text) and "import _hermetic" not in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [],
                         "add `import _hermetic` as the first import: "
                         "conftest.py does not run under `unittest discover`")

    def test_the_documented_gate_runs_the_pin_check_instead_of_skipping_it(self):
        """`unittest discover` used to skip the one case that checks the pin."""
        res = subprocess.run(
            [sys.executable, "-m", "unittest", "-v",
             "tests.test_rf_core.TestSuiteIsHermetic"],
            cwd=str(SKILL), capture_output=True, text=True, timeout=CHILD_TIMEOUT,
            env=child_env(PATH=os.environ.get("PATH", ""), HOME=str(scratch(self))))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("skipped", res.stderr, res.stderr)

    def test_a_pdf_in_the_developers_downloads_is_not_opened(self):
        """The finding's own measurement, shrunk to one onboarding case.

        With a PDF in ``$HOME/Downloads`` the gate shelled ``pdftotext`` at it
        once per onboarding test -- 880 launches over 80 files in one module.
        The shim records every launch and needs no poppler, so this runs in CI.
        """
        tmp = scratch(self)
        home = tmp / "home"
        (home / "Downloads").mkdir(parents=True)
        sys.path.insert(0, str(HERE / "e2e"))
        try:
            import make_pdf
        finally:
            sys.path.remove(str(HERE / "e2e"))
        (home / "Downloads" / "private.pdf").write_bytes(make_pdf.build())

        bindir = tmp / "bin"
        bindir.mkdir()
        log = tmp / "pdftotext.log"
        shim = bindir / "pdftotext"
        shim.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$CF_TEST_PDFTOTEXT_LOG"\n',
                        encoding="utf-8")
        shim.chmod(0o755)
        log.write_text("", encoding="utf-8")

        res = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_cflib.TestOnboardIntegration"
             ".test_onboard_discovers_the_source_without_a_single_flag"],
            cwd=str(SKILL), capture_output=True, text=True, timeout=CHILD_TIMEOUT,
            env=child_env(PATH=f"{bindir}:{os.environ.get('PATH', '')}", HOME=str(home),
                          CF_TEST_PDFTOTEXT_LOG=str(log)))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        launched = [line for line in log.read_text(encoding="utf-8").splitlines()
                    if str(home) in line]
        self.assertEqual(launched, [],
                         "the suite read a file under the developer's HOME")


# ---------------------------------------------------------------------------
# FR#21 -- an e2e script may not default its workspace inside the checkout
# ---------------------------------------------------------------------------
class TestE2EWorkspaceDefaults(unittest.TestCase):
    def test_no_e2e_script_defaults_its_workspace_inside_the_skill_repo(self):
        """`cf init` refuses a workspace under skill_root(), so such a default
        makes the documented `bash tests/e2e/<script>.sh` invocation exit 2."""
        from cfcmd.init import _refuse_unsafe_location

        checked = 0
        for script in sorted((SKILL / "tests" / "e2e").glob("*.sh")):
            match = re.search(r'^WS="\$\{WS:-(.*)\}"$',
                              script.read_text(encoding="utf-8"), re.M)
            if not match:
                continue
            default = subprocess.run(
                ["bash", "-c", f'HERE={script.parent}; echo "{match.group(1)}"'],
                capture_output=True, text=True, check=True,
                timeout=CHILD_TIMEOUT).stdout.strip()
            with self.subTest(script=script.name):
                _refuse_unsafe_location(Path(default))   # raises CfError if inside
            checked += 1
        self.assertTrue(checked, "no e2e script declared a WS default: glob is wrong")

    def test_the_default_is_a_fresh_directory_each_run(self):
        """Two runs must not share a workspace; `rm -rf "$WS"` is step 2's only
        cleanup, and a shared default would let one run inherit the other."""
        script = SKILL / "tests" / "e2e" / "multisource.sh"
        match = re.search(r'^WS="\$\{WS:-(.*)\}"$', script.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(match, "multisource.sh no longer declares a WS default")
        seen = set()
        for _ in range(2):
            seen.add(subprocess.run(
                ["bash", "-c", f'HERE={script.parent}; echo "{match.group(1)}"'],
                capture_output=True, text=True, check=True,
                timeout=CHILD_TIMEOUT).stdout.strip())
        self.assertEqual(len(seen), 2, seen)


# ---------------------------------------------------------------------------
# FR#38 -- the shipped shell: parsed here, linted in CI
# ---------------------------------------------------------------------------
def shipped_shell_scripts() -> list[Path]:
    return sorted(list(SCRIPTS.rglob("*.sh")) + list((SKILL / "tests" / "e2e").glob("*.sh")))


class TestShippedShellScripts(unittest.TestCase):
    def test_every_shipped_shell_script_is_valid_bash(self):
        """Glob-driven, not a hard-coded list: two of the three bridges had no
        syntax check at all, and a fourth script would have arrived the same way."""
        scripts = shipped_shell_scripts()
        self.assertTrue(scripts, "no .sh files found: the glob is wrong")
        for path in scripts:
            with self.subTest(script=str(path.relative_to(SKILL))):
                res = subprocess.run(["bash", "-n", str(path)], capture_output=True,
                                     text=True, timeout=CHILD_TIMEOUT)
                self.assertEqual(res.returncode, 0,
                                 f"{path.relative_to(SKILL)}: {res.stderr}")

    def test_the_workflow_lints_them_too(self):
        """`bash -n` only proves it parses; shellcheck is the linter the scripts
        were written against (one already carries a `disable=` directive)."""
        text = (SKILL / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("shellcheck", text, "no shell-lint job in the workflow")
        lint = [line for line in text.splitlines() if "run: shellcheck" in line]
        self.assertEqual(len(lint), 1, lint)
        for glob in ("scripts/run/bridge-*.sh", "scripts/detect/dump_deps.sh",
                     "tests/e2e/*.sh"):
            self.assertIn(glob, lint[0])


class TestLocalBridgeArgumentContract(unittest.TestCase):
    """`scripts/run/bridge-claude-p.sh` -- the default local bridge, untested."""

    SCRIPT = SCRIPTS / "run" / "bridge-claude-p.sh"

    def _launch(self, *args: str, env: dict | None = None,
                session_id: str | None = None) -> tuple[subprocess.CompletedProcess, list[str]]:
        """Run the bridge with a fake `claude` on PATH; return it and the argv."""
        tmp = scratch(self)
        prompts = tmp / "prompts"
        prompts.mkdir()
        for role in ("worker", "reviewer"):
            (prompts / f"{role}.md").write_text(
                "task $TEND_TASK_ID in $TEND_WORKTREE_PATH\n", encoding="utf-8")
        session_dir = tmp / "session"
        session_dir.mkdir()
        if session_id is not None:
            (session_dir / "claude-session-id").write_text(session_id, encoding="utf-8")
        argv_file = tmp / "argv.txt"
        bindir = tmp / "bin"
        bindir.mkdir()
        fake = bindir / "claude"
        fake.write_text(
            '#!/bin/sh\nfor a in "$@"; do printf \'%s\\n\' "$a"; done > "$CF_TEST_ARGV"\n'
            '''printf '{"session_id":"s-1","result":"{\\\\"schema_version\\\\":1,'''
            '''\\\\"status\\\\":\\\\"done\\\\",\\\\"summary\\\\":\\\\"ok\\\\",'''
            '''\\\\"verdict\\\\":\\\\"approve\\\\",\\\\"notes\\\\":\\\\"n\\\\"}"}\\n'\n''',
            encoding="utf-8")
        fake.chmod(0o755)
        environ = child_env(PATH=f"{bindir}:{os.environ.get('PATH', '')}",
                            HOME=str(tmp), CF_TEST_ARGV=str(argv_file),
                            CF_BRIDGE_ROOT=str(tmp),
                            TEND_AGENT_SESSION_DIR=str(session_dir),
                            TEND_TASK_ID="t-1", TEND_WORKTREE_PATH=str(tmp))
        environ.update(env or {})
        res = subprocess.run(["bash", str(self.SCRIPT), *args], capture_output=True,
                             text=True, cwd=str(tmp), env=environ, timeout=CHILD_TIMEOUT)
        argv = (argv_file.read_text(encoding="utf-8").splitlines()
                if argv_file.exists() else [])
        return res, argv

    def test_the_positional_role_picks_the_reviewers_read_only_surface(self):
        res, argv = self._launch("reviewer")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(argv[argv.index("--allowedTools") + 1],
                         "Read,Glob,Grep,Bash(git:*)")
        self.assertNotIn("--permission-mode", argv)

    def test_the_default_role_is_the_worker_and_it_may_edit(self):
        res, argv = self._launch()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(argv[argv.index("--allowedTools") + 1],
                         "Read,Glob,Grep,Edit,Write,Bash(git:*)")
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")

    def test_cf_role_names_the_role_when_no_positional_does(self):
        _, argv = self._launch(env={"CF_ROLE": "reviewer"})
        self.assertEqual(argv[argv.index("--allowedTools") + 1],
                         "Read,Glob,Grep,Bash(git:*)")

    def test_resume_passes_the_stored_session_id_and_only_then(self):
        _, argv = self._launch("--resume", session_id="s-0")
        self.assertEqual(argv[argv.index("--resume") + 1], "s-0")
        _, argv = self._launch(session_id="s-0")
        self.assertNotIn("--resume", argv)

    def test_the_prompt_is_substituted_and_stdout_is_only_the_contract_json(self):
        res, argv = self._launch()
        self.assertIn("task t-1 in ", argv[-1])
        self.assertEqual(json.loads(res.stdout)["status"], "done")

    def test_the_model_flag_is_absent_unless_cf_model_names_one(self):
        _, argv = self._launch()
        self.assertNotIn("--model", argv)
        _, argv = self._launch(env={"CF_MODEL": "a-model-id"})
        self.assertEqual(argv[argv.index("--model") + 1], "a-model-id")


class TestTendAgentBridgeArgumentContract(unittest.TestCase):
    """`scripts/run/bridge-tend-agent.sh` -- the stub backend, untested."""

    SCRIPT = SCRIPTS / "run" / "bridge-tend-agent.sh"

    def _launch(self, *args: str, env: dict | None = None):
        tmp = scratch(self)
        prompts = tmp / "prompts"
        prompts.mkdir()
        for role in ("worker", "reviewer"):
            (prompts / f"{role}.md").write_text("task\n", encoding="utf-8")
        argv_file = tmp / "argv.txt"
        bindir = tmp / "bin"
        bindir.mkdir()
        fake = bindir / "tend-agent"
        fake.write_text('#!/bin/sh\nfor a in "$@"; do printf \'%s\\n\' "$a"; done '
                        '> "$CF_TEST_ARGV"\n', encoding="utf-8")
        fake.chmod(0o755)
        environ = child_env(PATH=f"{bindir}:{os.environ.get('PATH', '')}",
                            HOME=str(tmp), CF_TEST_ARGV=str(argv_file),
                            CF_BRIDGE_ROOT=str(tmp), ANTHROPIC_API_KEY="k")
        environ.update(env or {})
        res = subprocess.run(["bash", str(self.SCRIPT), *args], capture_output=True,
                             text=True, cwd=str(tmp), env=environ, timeout=CHILD_TIMEOUT)
        argv = (argv_file.read_text(encoding="utf-8").splitlines()
                if argv_file.exists() else [])
        return res, argv

    def test_no_api_key_is_a_refusal_that_names_the_two_other_bridges(self):
        res, argv = self._launch(env={"ANTHROPIC_API_KEY": ""})
        self.assertEqual(res.returncode, 78, res.stdout + res.stderr)
        self.assertIn("bridge-claude-p.sh", res.stderr)
        self.assertIn("bridge-agentbox.sh", res.stderr)
        self.assertEqual(argv, [], "it must refuse before launching anything")

    def test_the_role_reaches_the_agent_from_the_positional_or_cf_role(self):
        _, argv = self._launch()
        self.assertEqual(argv[argv.index("--role") + 1], "worker")
        _, argv = self._launch("reviewer")
        self.assertEqual(argv[argv.index("--role") + 1], "reviewer")
        _, argv = self._launch(env={"CF_ROLE": "reviewer"})
        self.assertEqual(argv[argv.index("--role") + 1], "reviewer")

    def test_the_prompt_file_follows_the_role_and_the_optional_flags_are_optional(self):
        _, argv = self._launch("reviewer")
        self.assertTrue(argv[argv.index("--prompt-file") + 1].endswith("/reviewer.md"))
        self.assertNotIn("--model", argv)
        self.assertNotIn("--reasoning-effort", argv)
        _, argv = self._launch(env={"CF_MODEL": "a model id",
                                    "CF_REASONING_EFFORT": "high"})
        # quoted inside ${CF_MODEL:+...}, so a value with a space stays one word
        self.assertEqual(argv[argv.index("--model") + 1], "a model id")
        self.assertEqual(argv[argv.index("--reasoning-effort") + 1], "high")


# ---------------------------------------------------------------------------
# FR#48 / FR#49 -- an assertion whose two operands are the same text
# ---------------------------------------------------------------------------
_SELF_COMPARING = {"assertEqual", "assertNotEqual", "assertIs", "assertIsNot"}

#: The one pre-existing case, kept by (file, operand) rather than by file so a
#: new one there still fails.  `test_finding_id_is_stable_and_content_addressed`
#: calls a helper twice to claim the rows are DETERMINISTIC, which is a real
#: property of a content-addressed id even though the line also holds for a
#: constant implementation; the sibling assertions kill that mutant.  It is on
#: master and outside this pass; strengthen or drop it in its own change.
_ALLOWED = {("test_detect.py", "rows()")}


class TestNoIdenticalOperandAssertions(unittest.TestCase):
    def test_no_assertion_compares_a_value_with_itself(self):
        """`assertEqual(f(x), f(x))` holds for every implementation, including
        one that ignores its arguments; it is a guard that guards nothing."""
        offenders = []
        for path in sorted(HERE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or len(node.args) < 2:
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr not in _SELF_COMPARING:
                    continue
                left = ast.unparse(node.args[0])
                if left != ast.unparse(node.args[1]):
                    continue
                if (path.name, left) in _ALLOWED:
                    continue
                offenders.append(f"{path.name}:{node.lineno}: {left}")
        self.assertEqual(sorted(offenders), [],
                         "these assertions hold for every implementation: "
                         + "; ".join(sorted(offenders)))

    def test_the_allowlist_is_still_needed(self):
        """A recorded escape that stopped applying is one nobody may rely on."""
        live = set()
        for path in sorted(HERE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and len(node.args) >= 2
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _SELF_COMPARING
                        and ast.unparse(node.args[0]) == ast.unparse(node.args[1])):
                    live.add((path.name, ast.unparse(node.args[0])))
        self.assertEqual(_ALLOWED - live, set(), "delete the stale entry")


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
