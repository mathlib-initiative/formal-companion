"""The bridge hardening of docs/proposals/wikipedia-locate.md section 6.

New code.  ``scripts/run/agent_call.py`` (the 15-slot ``flock`` semaphore, the
45-minute timeout, 409 and empty envelopes as infrastructure failures, the
envelope kept under ``usage/``) and ``scripts/run/attempt_cap.py`` (the
counter of genuine reviewer rejections and the UNRESOLVED documents at the
cap), driven through ``scripts/run/bridge-agentbox.sh`` with a FAKE
``agentbox`` on PATH.  No agent is ever called.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    import test_verify_locate as tvl  # noqa: E402
except ImportError:  # pragma: no cover
    from tests import test_verify_locate as tvl  # noqa: E402

from cflib import gitsafe  # noqa: E402
from gate import verify_locate as vl  # noqa: E402
from run import attempt_cap  # noqa: E402

AGENT_CALL = SCRIPTS / "run" / "agent_call.py"
BRIDGE = SCRIPTS / "run" / "bridge-agentbox.sh"
SUCCESS = {"type": "result", "subtype": "success", "is_error": False, "result": "done",
           "total_cost_usd": 0.25}


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-bridge-"))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path.resolve()


def clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CF_", "TEND_", "GIT_CONFIG_"))}
    env.update(extra)
    return env


def fake(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


class TestAgentCall(unittest.TestCase):

    def setUp(self):
        self.tmp = scratch(self)
        self.env = clean_env(CF_SLOT_DIR=str(self.tmp / "slots"), CF_SLOT_POLL="0.05")

    def call(self, script: str, *, env=None, task="t-1") -> tuple[subprocess.CompletedProcess, Path]:
        agent = fake(self.tmp / f"agent-{time.monotonic_ns()}", script)
        raw = self.tmp / f"raw-{time.monotonic_ns()}"
        proc = subprocess.run([sys.executable, str(AGENT_CALL), "--role", "worker", "--raw", str(raw),
                               "--err", str(self.tmp / "agent.err"), "--usage-dir",
                               str(self.tmp / "usage"), "--task-id", task, "--", str(agent)],
                              capture_output=True, text=True, env={**self.env, **(env or {})},
                              timeout=120)
        return proc, raw

    def test_a_completed_call_keeps_its_envelope(self):
        proc, raw = self.call(f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        kept = sorted((self.tmp / "usage").iterdir())
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0].name.startswith("t-1.worker.") and kept[0].suffix == ".json")
        self.assertEqual(json.loads(kept[0].read_text())["total_cost_usd"], 0.25)
        self.assertEqual(json.loads(raw.read_text()), SUCCESS)

    def test_empty_and_409_envelopes_are_infrastructure(self):
        proc, _ = self.call("exit 0\n")
        self.assertEqual(proc.returncode, 75, proc.stderr)
        self.assertIn("EMPTY", proc.stderr)
        conflict = {"type": "result", "subtype": "error_during_execution", "is_error": True,
                    "result": "API Error: 409 too many concurrent sessions"}
        proc, _ = self.call(f"printf '%s' '{json.dumps(conflict)}'\n")
        self.assertEqual(proc.returncode, 75, proc.stderr)
        self.assertIn("409", proc.stderr)
        proc, _ = self.call("echo 'HTTP 409 Conflict' >&2\nprintf 'prose'\n")
        self.assertEqual(proc.returncode, 75, proc.stderr)
        # an earlier call's 409 in the shared err file does not taint this one
        proc, _ = self.call(f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_timeout_kills_the_group_and_exits_124(self):
        marker = self.tmp / "child-alive"
        started = time.monotonic()
        proc, _ = self.call(f"(sleep 30; touch {marker}) &\nsleep 30\n",
                            env={"CF_AGENT_TIMEOUT": "1s"})
        self.assertEqual(proc.returncode, 124, proc.stderr)
        self.assertLess(time.monotonic() - started, 25)
        self.assertIn("TIMED OUT", proc.stderr)
        self.assertIn("not a verdict", proc.stderr)

    def test_the_slots_bound_concurrency_and_wait_rather_than_fail(self):
        log = self.tmp / "concurrency.log"
        script = (f"echo start >> {log}\nsleep 0.6\necho end >> {log}\n"
                  f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        agent = fake(self.tmp / "agent", script)
        procs = []
        for i in range(5):
            procs.append(subprocess.Popen(
                [sys.executable, str(AGENT_CALL), "--role", "worker", "--raw", str(self.tmp / f"r{i}"),
                 "--task-id", f"t-{i}", "--", str(agent)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={**self.env, "CF_SLOTS": "2"}))
        codes = [p.wait(timeout=120) for p in procs]
        self.assertEqual(codes, [0] * 5)
        depth = peak = 0
        for line in log.read_text().split():
            depth += 1 if line == "start" else -1
            peak = max(peak, depth)
        self.assertEqual(peak, 2)
        self.assertEqual(sorted(p.name for p in (self.tmp / "slots").iterdir()),
                         ["slot-01.lock", "slot-02.lock"])

    def test_a_bad_setting_is_a_usage_error(self):
        proc, _ = self.call("true\n", env={"CF_AGENT_TIMEOUT": "soon"})
        self.assertEqual(proc.returncode, 64)

    def test_durations(self):
        from run import agent_call

        self.assertEqual(agent_call.parse_duration("45m"), 2700)
        self.assertEqual(agent_call.parse_duration("2h"), 7200)
        self.assertEqual(agent_call.parse_duration("90"), 90)
        with self.assertRaises(ValueError):
            agent_call.parse_duration("-5m")


class BridgeCase(unittest.TestCase):
    """bridge-agentbox.sh in a temp tend root with a fake agentbox."""

    def setUp(self):
        self.tmp = scratch(self)
        self.root = self.tmp / "tend-root"
        prompts = self.root / "prompts"
        prompts.mkdir(parents=True)
        for role in ("worker", "reviewer"):
            (prompts / f"{role}-system.md").write_text("system\n", encoding="utf-8")
            (prompts / f"{role}.md").write_text("task $TEND_TASK_ID\n", encoding="utf-8")
        self.bin = self.tmp / "fakebin"
        self.bin.mkdir()
        self.calls = self.tmp / "agentbox-calls"
        self.worktree = self.tmp / "worktree"
        self.worktree.mkdir()

    def agentbox(self, body: str) -> None:
        fake(self.bin / "agentbox", f'echo called >> "{self.calls}"\n'
                                    f'for a in "$@"; do printf \'%s\\n\' "$a"; done > "{self.tmp}/argv"\n'
                                    + body)

    def bridge(self, role="worker", **env) -> subprocess.CompletedProcess:
        environ = clean_env(PATH=f"{self.bin}:{os.environ['PATH']}", CF_BRIDGE_ROOT=str(self.root),
                            CF_AGENTBOX_PROFILE="examplebox", CF_SLOT_DIR=str(self.tmp / "slots"),
                            TEND_TASK_ID="t-loc-0001", **env)
        return subprocess.run(["bash", str(BRIDGE), role], capture_output=True, text=True,
                              cwd=str(self.worktree), env=environ, timeout=120)

    def called(self) -> int:
        return len(self.calls.read_text().split()) if self.calls.exists() else 0


class TestTheBridge(BridgeCase):

    def test_infrastructure_failures_print_no_contribution(self):
        self.agentbox("exit 0\n")
        proc = self.bridge()
        self.assertEqual(proc.returncode, 75, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.agentbox('printf \'{"is_error": true, "result": "API Error: 409"}\'\n')
        self.assertEqual(self.bridge().returncode, 75)
        self.agentbox("sleep 20\n")
        proc = self.bridge(CF_AGENT_TIMEOUT="1")
        self.assertEqual(proc.returncode, 124, proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_a_completed_call_reports_and_keeps_the_envelope(self):
        self.agentbox(f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        proc = self.bridge()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("schema_version", json.loads(proc.stdout.strip().splitlines()[-1]))
        kept = list((self.root / "usage").iterdir())
        self.assertEqual(len(kept), 1)
        self.assertIn("t-loc-0001.worker.", kept[0].name)

    def test_extra_tools_reach_the_worker_only(self):
        self.agentbox(f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        extra = "Bash(python3 /gate/corpus_search.py:*),Bash(python3 /gate/corpus_show.py:*)"
        self.bridge(CF_EXTRA_TOOLS=extra)
        argv = (self.tmp / "argv").read_text().splitlines()
        self.assertTrue(argv[argv.index("--allowedTools") + 1].endswith("," + extra))
        self.bridge("reviewer", CF_EXTRA_TOOLS=extra)
        argv = (self.tmp / "argv").read_text().splitlines()
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Glob,Grep,Bash(git:*)")

    def test_the_reviewer_may_run_the_gate_and_nothing_wider(self):
        """P1: the reviewer prompt renders the gate command; claude -p denies an unlisted one."""
        self.agentbox(f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        gate = "python3 /srv/cf/r-gate/verify_locate.py --gate-config /srv/cf/r-gate/gate.json"
        extra = "Bash(python3 /srv/cf/r-gate/corpus_search.py:*)"
        self.bridge("reviewer", CF_GATE_CMD=gate, CF_EXTRA_TOOLS=extra)
        argv = (self.tmp / "argv").read_text().splitlines()
        self.assertEqual(argv[argv.index("--allowedTools") + 1],
                         "Read,Glob,Grep,Bash(git:*),Bash(python3 /srv/cf/r-gate/verify_locate.py:*)")
        # an explicit list still wins, as for the worker
        self.bridge("reviewer", CF_GATE_CMD=gate, CF_REVIEWER_TOOLS="Read")
        argv = (self.tmp / "argv").read_text().splitlines()
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read")

    def test_a_missing_helper_is_refused_before_any_call(self):
        bindir = self.tmp / "staged-bin"
        bindir.mkdir()
        shutil.copy2(BRIDGE, bindir / "bridge-agentbox.sh")
        shutil.copy2(SCRIPTS / "run" / "normalize_result.py", bindir / "normalize_result.py")
        self.agentbox("true\n")
        environ = clean_env(PATH=f"{self.bin}:{os.environ['PATH']}", CF_BRIDGE_ROOT=str(self.root),
                            CF_AGENTBOX_PROFILE="examplebox")
        proc = subprocess.run(["bash", str(bindir / "bridge-agentbox.sh"), "worker"],
                              capture_output=True, text=True, cwd=str(self.worktree), env=environ)
        self.assertEqual(proc.returncode, 78)
        self.assertIn("agent_call.py", proc.stderr)
        self.assertEqual(self.called(), 0)


class TestTheAttemptCap(BridgeCase):
    """Three genuine rejections, then the bridge ends the task without an agent."""

    def setUp(self):
        super().setUp()
        self.fx = tvl.Fixture(self, "locate")
        self.task = "t-loc-0001"
        self.fx.manifest(self.task, tvl.locate_manifest_doc(self.fx))
        tvl.git(self.worktree, "init", "-q", "-b", "main")
        # tend keeps its per-worktree state out of the change set, as here
        (self.worktree / ".git" / "info" / "exclude").write_text(".tend/\n", encoding="utf-8")
        (self.worktree / "tasks").mkdir()
        (self.worktree / "tasks" / f"{self.task}.yaml").write_text(
            f"id: {self.task}\nstatus: open\n", encoding="utf-8")
        tvl.git(self.worktree, "add", "-A")
        tvl.git(self.worktree, "commit", "-qm", "tasks")
        tvl.git(self.worktree, "checkout", "-q", "-b", self.task)
        self.capenv = {"CF_ATTEMPT_CAP": "3", "CF_TASK_MANIFESTS": str(self.fx.tasks)}

    def reject(self) -> subprocess.CompletedProcess:
        self.agentbox(
            'mkdir -p .tend && printf \'{"verdict": "request_changes", "notes": "n", '
            '"feedback_text": "the receipt is thin"}\' > .tend/agent_result.json\n'
            f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        return self.bridge("reviewer", **self.capenv)

    def test_only_genuine_rejections_count(self):
        self.assertEqual(json.loads(self.reject().stdout)["verdict"], "request_changes")
        self.assertEqual(attempt_cap.read_count(self.root, self.task), 1)
        # a failed call forced to request_changes by the normaliser is not a verdict
        self.agentbox('printf \'{"subtype": "error_during_execution", "is_error": true, '
                      '"result": "crashed"}\'\n')
        self.assertEqual(json.loads(self.bridge("reviewer", **self.capenv).stdout)["verdict"],
                         "request_changes")
        self.assertEqual(attempt_cap.read_count(self.root, self.task), 1)
        # nor is a timeout, nor an approval
        self.agentbox("sleep 20\n")
        self.bridge("reviewer", CF_AGENT_TIMEOUT="1", **self.capenv)
        self.agentbox('mkdir -p .tend && printf \'{"verdict": "approve", "notes": "ok"}\' '
                      f"> .tend/agent_result.json\nprintf '%s' '{json.dumps(SUCCESS)}'\n")
        self.bridge("reviewer", **self.capenv)
        self.assertEqual(attempt_cap.read_count(self.root, self.task), 1)

    def test_the_third_rejection_ends_the_task_unresolved(self):
        for _ in range(3):
            self.reject()
        self.assertEqual(attempt_cap.read_count(self.root, self.task), 3)
        before = self.called()
        proc = self.bridge("worker", **self.capenv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.called(), before, "the capped worker called an agent")
        out = json.loads(proc.stdout)
        self.assertEqual(out["status"], "completed")
        doc = self.worktree / "findings" / self.task / f"{tvl.SID}.json"
        data = json.loads(doc.read_text())
        self.assertEqual(data["unresolved"]["by"], "attempt_cap")
        self.assertEqual({p["outcome"] for p in data["passages"]}, {"unresolved"})
        self.assertIn("status: complete", (self.worktree / "tasks" / f"{self.task}.yaml").read_text())
        self.assertIn("UNRESOLVED at the attempt cap", tvl.git(self.worktree, "log", "-1", "--format=%s"))
        # the documents pass the locate gate as this task's own contribution
        result = vl.run_gate(self.fx.gate_cfg(), root=self.worktree, changed_files=True,
                             base="main", task_id=self.task)
        self.assertEqual(result["exit"], 0, result["problems"] + result["broken"])
        # and the reviewer approves exactly those, mechanically
        proc = self.bridge("reviewer", **self.capenv)
        self.assertEqual(json.loads(proc.stdout)["verdict"], "approve")
        self.assertEqual(self.called(), before)

    def test_a_tampered_capped_contribution_goes_to_the_agent(self):
        for _ in range(3):
            self.reject()
        self.bridge("worker", **self.capenv)
        doc = self.worktree / "findings" / self.task / f"{tvl.SID}.json"
        data = json.loads(doc.read_text())
        data["passages"][0]["outcome"] = "absent"
        doc.write_text(json.dumps(data))
        before = self.called()
        self.reject()
        self.assertEqual(self.called(), before + 1)

    def test_the_inlined_git_pins_are_gitsafes(self):
        self.assertEqual(attempt_cap._GIT_SAFE_CONFIG, gitsafe.SAFE_CONFIG)
        self.assertEqual(attempt_cap._GIT_SAFE_ENV, gitsafe.SAFE_ENV)

    def test_the_cap_is_off_without_a_task_or_a_cap(self):
        self.assertEqual(attempt_cap.main(["worker", "--root", str(self.root), "--task", "",
                                           "--manifests", str(self.fx.tasks)]),
                         attempt_cap.EXIT_NOT_CAPPED)
        self.assertEqual(attempt_cap.main(["worker", "--root", str(self.root), "--task", self.task,
                                           "--manifests", str(self.fx.tasks), "--cap", "0"]),
                         attempt_cap.EXIT_NOT_CAPPED)


class TestTheInventoryAndChallengeShapes(unittest.TestCase):

    def test_each_mode_writes_a_gate_valid_document(self):
        for mode, manifest_doc in (("locate-inventory", tvl.inventory_manifest_doc),
                                   ("locate-challenge", tvl.inventory_manifest_doc)):
            with self.subTest(mode=mode):
                fx = tvl.Fixture(self, mode)
                task = "t-0009"
                doc = manifest_doc(fx)
                if mode == "locate-challenge":
                    doc = {k: v for k, v in doc.items() if k != "finders"}
                    doc["records"] = []
                fx.manifest(task, doc)
                docs = attempt_cap.unresolved_documents(fx.tasks, task, "attempt_cap: 3")
                ((rel, body),) = docs.items()
                path = fx.root / rel
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(body))
                result = fx.run(path)
                self.assertEqual(result["exit"], 0, result["problems"] + result["broken"])


if __name__ == "__main__":
    unittest.main()
