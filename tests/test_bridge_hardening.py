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

import contextlib
import io
import json
import os
import signal
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

    def records(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.tmp / "usage").glob("*.json"))]

    def test_t1_a_timed_out_call_leaves_a_record_with_what_is_known(self):
        proc, _ = self.call("sleep 30\n", env={"CF_AGENT_TIMEOUT": "1s"}, task="t-7")
        self.assertEqual(proc.returncode, 124, proc.stderr)
        (record,) = self.records()
        self.assertEqual((record["subtype"], record["is_error"], record["total_cost_usd"]),
                         ("error_timeout", True, None))
        call = record["cf_agent_call"]
        self.assertEqual((call["outcome"], call["role"], call["task_id"], call["exit"],
                          call["cost"]), ("timeout", "worker", "t-7", 124, "unknown"))
        self.assertGreaterEqual(call["elapsed_s"], 1.0)
        self.assertEqual(call["timeout_s"], 1.0)
        self.assertGreaterEqual(record["duration_ms"], 1000)
        self.assertIn("kept at", proc.stderr)
        # the watchdog counts it as an unpriced call
        from run import watchdog

        got = watchdog.ledger(self.tmp / "usage")
        self.assertEqual(got["unpriced_calls"], 1)

    def test_t1_an_empty_or_prose_stdout_leaves_a_record_too(self):
        proc, _ = self.call("exit 0\n", task="t-8")
        self.assertEqual(proc.returncode, 75, proc.stderr)
        proc, _ = self.call("printf 'not an envelope'\n", task="t-8")
        outcomes = sorted(r["cf_agent_call"]["outcome"] for r in self.records())
        self.assertEqual(outcomes, ["empty", "not_json"])
        # the prose itself is kept beside its record, as before
        self.assertEqual(len(list((self.tmp / "usage").glob("*.out"))), 1)

    def test_r2_json_that_is_no_object_is_kept_beside_its_record(self):
        """The review's t1-nonobj run: stdout ``["partial", 1]`` was kept as ``.json`` and
        the record, written under the same name in the same second, replaced it."""
        proc, _ = self.call("printf '[\"partial\", 1]'\n", task="t-3")
        kept = sorted(p.name for p in (self.tmp / "usage").iterdir())
        self.assertEqual(len(kept), 2, kept)
        (out,) = (self.tmp / "usage").glob("t-3.worker.*.out")
        self.assertEqual(out.read_bytes(), b'["partial", 1]')
        (record,) = self.records()
        self.assertEqual((record["cf_agent_call"]["outcome"],
                          record["cf_agent_call"]["stdout_bytes"]), ("not_json", 14))
        self.assertEqual(out.name[:-len(".out")], kept[0][:-len(".json")])

    def test_t1_a_priced_envelope_gets_no_record(self):
        proc, _ = self.call(f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.records(), [SUCCESS])

    def test_a_signal_reaches_the_agent_and_frees_the_slot_after_it(self):
        """Review #62: TERM to the caller's group stops the agent before the slot is free."""
        pidfile = self.tmp / "agent.pid"
        agent = fake(self.tmp / "agent-sleeps", f"echo $$ > {pidfile}\nexec sleep 30\n")
        caller = subprocess.Popen([sys.executable, str(AGENT_CALL), "--role", "worker", "--raw",
                                   str(self.tmp / "raw"), "--task-id", "t-1", "--", str(agent)],
                                  env={**self.env, "CF_SLOTS": "1"}, start_new_session=True,
                                  stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: caller.poll() is None and caller.kill())
        for _ in range(200):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.05)
        agent_pid = int(pidfile.read_text())
        os.killpg(caller.pid, signal.SIGTERM)          # what tend's stop does to the bridge
        code = caller.wait(timeout=60)
        self.assertEqual(code, 128 + signal.SIGTERM, caller.stderr.read())
        with self.assertRaises(ProcessLookupError):
            os.kill(agent_pid, 0)
        import fcntl

        fd = os.open(self.tmp / "slots" / "slot-01.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # the slot is free again
        finally:
            os.close(fd)

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
        self.tend_root(self.tmp / "tend-root")
        self.bin = self.tmp / "fakebin"
        self.bin.mkdir()
        self.calls = self.tmp / "agentbox-calls"
        self.worktree = self.tmp / "worktree"
        self.worktree.mkdir()

    def tend_root(self, root: Path) -> None:
        """The bridge's root (CF_BRIDGE_ROOT): its prompts; attempts/ and usage/ land here."""
        self.root = root
        prompts = self.root / "prompts"
        prompts.mkdir(parents=True, exist_ok=True)
        for role in ("worker", "reviewer"):
            (prompts / f"{role}-system.md").write_text("system\n", encoding="utf-8")
            (prompts / f"{role}.md").write_text("task $TEND_TASK_ID\n", encoding="utf-8")

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
        """P1: the reviewer prompt renders the gate command; the agent CLI denies an unlisted one."""
        self.agentbox(f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        gate = "python3 /srv/cf/r-gate/verify_locate.py --gate-config /srv/cf/r-gate/gate.json"
        extra = "Bash(python3 /srv/cf/r-gate/corpus_search.py:*)"
        self.bridge("reviewer", CF_GATE_CMD=gate, CF_EXTRA_TOOLS=extra)
        argv = (self.tmp / "argv").read_text().splitlines()
        # a locate reviewer's git is read-only by subcommand, as the audited reviewer's
        self.assertEqual(argv[argv.index("--allowedTools") + 1],
                         "Read,Glob,Grep,Bash(git diff:*),Bash(git show:*),Bash(git log:*),"
                         f"Bash(git status:*),Bash({gate} --findings:*)")
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
        # the fixture's root is the tend root, as on the host: gate/ (with
        # tasks/) beside attempts/, where the gate reads the bridge's counter
        self.tend_root(self.fx.root)
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

    def prose(self, text: str) -> subprocess.CompletedProcess:
        """A reviewer that writes NO result file and says ``text`` in its envelope."""
        envelope = dict(SUCCESS, result=text)
        (self.tmp / "envelope.json").write_text(json.dumps(envelope), encoding="utf-8")
        self.agentbox(f'cat "{self.tmp / "envelope.json"}"\n')
        return self.bridge("reviewer", **self.capenv)

    def test_a_verdict_coerced_from_prose_is_not_a_rejection(self):
        """Three approvals in prose, and no result file, used to cap a task."""
        for text in ("I checked the form and ran the gate: exit 0. I approve this "
                     "contribution; it is well formed.",
                     '{"schema_version": 1, "verdict": "approve", "notes": "fine"}', ""):
            for _ in range(3):
                self.prose(text)
        self.assertEqual(attempt_cap.read_count(self.root, self.task), 0)
        self.assertEqual(json.loads(self.prose('{"verdict": "approve"}').stdout)["verdict"],
                         "approve")
        # an explicit marker in prose is the reviewer's own verdict, and counts
        out = json.loads(self.prose("The receipt is thin. verdict: request_changes").stdout)
        self.assertEqual(out["verdict"], "request_changes")
        self.assertEqual(attempt_cap.read_count(self.root, self.task), 1)

    def test_at_the_cap_the_attempts_other_changes_are_undone(self):
        """A neighbour's task file, a stray file and a deletion left by the
        rejected attempts made the gate refuse the capped contribution forever."""
        tvl.git(self.worktree, "checkout", "-q", "main")
        (self.worktree / "tasks" / "t-loc-0002.yaml").write_text(
            "id: t-loc-0002\nstatus: open\npriority: high\n", encoding="utf-8")
        landed = self.worktree / "findings" / "t-loc-0002" / f"{tvl.SID}.json"
        landed.parent.mkdir(parents=True)
        landed.write_text("{}\n", encoding="utf-8")
        tvl.git(self.worktree, "add", "-A")
        tvl.git(self.worktree, "commit", "-qm", "a neighbour landed")
        tvl.git(self.worktree, "checkout", "-q", "-B", self.task)
        own = self.worktree / "tasks" / f"{self.task}.yaml"
        own.write_text(f"id: {self.task}\nstatus: open\npriority: 9\n", encoding="utf-8")
        (self.worktree / "tasks" / "t-loc-0002.yaml").write_text(
            "id: t-loc-0002\nstatus: open\npriority: 9\n", encoding="utf-8")
        landed.unlink()
        (self.worktree / "scripts").mkdir()
        (self.worktree / "scripts" / "stray.py").write_text("print(1)\n", encoding="utf-8")
        tvl.git(self.worktree, "add", "-A")
        tvl.git(self.worktree, "commit", "-qm", "a rejected attempt")
        (self.worktree / "notes.txt").write_text("untracked\n", encoding="utf-8")
        for _ in range(3):
            self.reject()
        before = self.called()
        # left alone, the reviewer would not approve this mechanically
        self.assertFalse(attempt_cap.is_capped_contribution(self.worktree, self.fx.tasks,
                                                            self.task))
        proc = self.bridge("worker", **self.capenv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = vl.run_gate(self.fx.gate_cfg(), root=self.worktree, changed_files=True,
                             base="main", task_id=self.task)
        self.assertEqual(result["exit"], 0, result["problems"] + result["broken"])
        self.assertEqual(landed.read_text(), "{}\n")
        self.assertIn("priority: high", (self.worktree / "tasks" / "t-loc-0002.yaml").read_text())
        self.assertEqual(own.read_text(), f"id: {self.task}\nstatus: complete\n")
        self.assertFalse((self.worktree / "scripts" / "stray.py").exists())
        self.assertFalse((self.worktree / "notes.txt").exists())
        self.assertEqual(json.loads(self.bridge("reviewer", **self.capenv).stdout)["verdict"],
                         "approve")
        self.assertEqual(self.called(), before)
        # a stray path after the cap is not approved mechanically
        (self.worktree / "late.txt").write_text("x\n", encoding="utf-8")
        self.assertFalse(attempt_cap.is_capped_contribution(self.worktree, self.fx.tasks,
                                                            self.task))

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

    def test_a_cap_under_the_gates_is_refused_before_any_agent_call(self):
        """The locate gate accepts a ``by: attempt_cap`` document only at a count of at
        least its LOCATE_ATTEMPT_CAP, so a bridge capping lower wrote documents that the
        gate refused on every later call, and the task looped.  A wrapper whose
        CF_ATTEMPT_CAP is under the gate's is refused on every call, from the first."""
        low = dict(self.capenv, CF_ATTEMPT_CAP=str(vl.LOCATE_ATTEMPT_CAP - 1))
        self.agentbox(f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        for rejected in (0, vl.LOCATE_ATTEMPT_CAP - 1):
            while attempt_cap.read_count(self.root, self.task) < rejected:
                self.reject()
            before = self.called()
            for role in ("worker", "reviewer"):
                with self.subTest(rejected=rejected, role=role):
                    proc = self.bridge(role, **low)
                    self.assertEqual(proc.returncode, attempt_cap.EXIT_CONFIG, proc.stderr)
                    self.assertIn(f"CF_ATTEMPT_CAP={vl.LOCATE_ATTEMPT_CAP - 1} is refused",
                                  proc.stderr)
                    self.assertEqual(proc.stdout, "")
            self.assertEqual(self.called(), before, "a refused cap called an agent")
            self.assertFalse((self.worktree / "findings").exists())
        self.assertIn("under the locate gate's", (self.root / "agentbox-worker.err").read_text())
        for cap in ("1", str(vl.LOCATE_ATTEMPT_CAP - 1)):
            said = io.StringIO()
            with contextlib.redirect_stderr(said):
                code = attempt_cap.main(["worker", "--root", str(self.root), "--task", self.task,
                                         "--manifests", str(self.fx.tasks), "--cap", cap])
            self.assertEqual(code, attempt_cap.EXIT_CONFIG)
            self.assertIn(f"set CF_ATTEMPT_CAP to 0 (off) or at least {vl.LOCATE_ATTEMPT_CAP}",
                          said.getvalue())
        # the gate's own cap, or one above it, is the bridge's to choose
        self.assertEqual(attempt_cap.main(["worker", "--root", str(self.root), "--task",
                                           self.task, "--manifests", str(self.fx.tasks),
                                           "--cap", str(vl.LOCATE_ATTEMPT_CAP + 1)]),
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
                docs = attempt_cap.unresolved_documents(fx.tasks, task, attempt_cap.cap_reason(3))
                ((rel, body),) = docs.items()
                path = fx.root / rel
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(body))
                result = fx.run(path)
                self.assertEqual(result["exit"], 0, result["problems"] + result["broken"])


if __name__ == "__main__":
    unittest.main()


class TestTheObservableStateFallback(unittest.TestCase):
    """#30/#40: with no result file the fallback gates exactly the task's own documents."""

    def setUp(self):
        self.tmp = scratch(self)
        self.wt = self.tmp / "worktree"
        (self.wt / "findings" / "t-1").mkdir(parents=True)
        (self.wt / "findings" / "t-0").mkdir(parents=True)
        (self.wt / "findings" / "t-1" / "a.json").write_text("{}")
        (self.wt / "findings" / "t-0" / "merged.json").write_text("{}")
        self.argv = self.tmp / "gate-argv"

    def fallback(self, *, ok: bool) -> dict:
        gate = self.tmp / "gate.py"
        gate.write_text(
            "import sys\n"
            f"open({str(self.argv)!r}, 'w').write('\\n'.join(sys.argv[1:]))\n"
            + ("print('OK: 1 document')\n" if ok else
               "print('VALIDATION FAILED (1 problem(s)):', file=sys.stderr)\n"
               "print('  - [rule 2] t-1/a.json: the quote matches 0 times', file=sys.stderr)\n"
               "print('  changed files: ownership checked against task t-1')\nsys.exit(1)\n"),
            encoding="utf-8")
        script = (f"import sys; sys.path.insert(0, {str(SCRIPTS / 'run')!r}); "
                  "import normalize_result as n, json; print(json.dumps(n.worker_result(None, '')))")
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              cwd=str(self.wt),
                              env=clean_env(CF_GATE_CMD=f"{sys.executable} {gate} --gate-config g.json",
                                            CF_FINDINGS_GLOB="findings/$TEND_TASK_ID/*.json",
                                            TEND_TASK_ID="t-1"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_only_the_tasks_documents_reach_the_gate(self):
        out = self.fallback(ok=True)
        self.assertEqual(out["status"], "completed")
        self.assertEqual(self.argv.read_text().splitlines(),
                         ["--gate-config", "g.json", "--findings", "findings/t-1/a.json"])

    def test_a_refusal_reports_the_reason_not_the_ownership_line(self):
        out = self.fallback(ok=False)
        self.assertEqual(out["status"], "needs_review")
        self.assertIn("[rule 2]", out["notes"])


class TestTheReviewerMayRunTheGate(BridgeCase):
    """#31: the reviewer's checklist has it confirm the gate passes."""

    def test_the_gate_is_on_the_reviewers_surface(self):
        self.agentbox(f"printf '%s' '{json.dumps(SUCCESS)}'\n")
        gate = "/srv/r-1-gate"
        self.bridge("reviewer", CF_GATE_CMD=f"python3 {gate}/verify_locate.py --gate-config "
                                            f"{gate}/gate.json")
        argv = (self.tmp / "argv").read_text().splitlines()
        tools = argv[argv.index("--allowedTools") + 1].split(",")
        self.assertIn(f"Bash(python3 {gate}/verify_locate.py --gate-config {gate}/gate.json "
                      f"--findings:*)", tools)
        self.assertNotIn("Write", tools)
        self.assertNotIn("Edit", tools)
