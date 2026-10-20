"""Campaigns, the spend watchdog and ``cf run`` for locate shards.

New code, for docs/proposals/wikipedia-locate.md section 6 (Roots; Cost and
spend; ``cf campaign``) and references/locate-documents.md sections 9 and 11.
The host is simulated: a temp directory stands in for the remote root, and
FAKE ``tend`` and ``tend-control`` scripts on PATH record what the sequencer
and the watchdog ask of them.  No agent, no host, no network.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import io
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main  # noqa: E402
from cflib import gitsafe  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.workspace import Workspace  # noqa: E402
from run import campaign as campaign_mod  # noqa: E402
from run import manifest, remote, watchdog  # noqa: E402

WATCHDOG = SCRIPTS / "run" / "watchdog.py"

#: the process table the watchdog reads (``--parent``, ``--stop-others``): the
#: Linux run host's /proc, which macOS does not have
HAS_PROC = Path("/proc/self/stat").exists()
NEEDS_PROC = unittest.skipUnless(HAS_PROC, "no /proc here; the watchdog reads the Linux run "
                                           "host's process table")


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-campaign-"))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path.resolve()


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CF_", "TEND_", "GIT_CONFIG_"))}
    env.update(extra)
    return env


def git(cwd: Path, *args: str) -> None:
    env = clean_env(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t",
                    GIT_COMMITTER_EMAIL="t@x", GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, env=env)


def envelope(directory: Path, name: str, usd: float) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps({"subtype": "success", "total_cost_usd": usd}))


class _WatchdogRoot(unittest.TestCase):
    """A tend root with a fake ``tend-control`` that records each drain."""

    def setUp(self):
        self.tmp = scratch(self)
        self.root = self.tmp / "r-1-tend"
        self.root.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.drains = self.tmp / "drains"
        tc = self.bin / "tend-control"
        tc.write_text(f'#!/bin/sh\necho "$@" >> "{self.drains}"\n')
        tc.chmod(0o755)

    def run_wd(self, *extra) -> tuple[int, dict]:
        proc = subprocess.run([sys.executable, str(WATCHDOG), "--root", str(self.root),
                               "--max-cost", "10", "--once", *extra],
                              capture_output=True, text=True,
                              env=clean_env(PATH=f"{self.bin}:{os.environ['PATH']}"))
        return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])


class TestTheWatchdog(_WatchdogRoot):

    def test_the_rules(self):
        args = dict(max_cost=10, calls=0, failed=0, merged=None, p90_cost=None, p90_gate=None,
                    multiple=2)
        self.assertEqual(watchdog.decide(dollars=9.99, **args), [])
        self.assertIn("cap", watchdog.decide(dollars=10, **args)[0])
        args.update(calls=40, failed=12, p90_gate=0.1)
        self.assertIn("gate-failure rate", watchdog.decide(dollars=1, **args)[0])
        args.update(calls=10)                       # too few calls to judge
        self.assertEqual(watchdog.decide(dollars=1, **args), [])
        args.update(merged=6, p90_cost=0.5)
        self.assertIn("cost per merged task", watchdog.decide(dollars=6.5, **args)[0])
        args.update(merged=4)                       # too few merged tasks to judge
        self.assertEqual(watchdog.decide(dollars=6.5, **args), [])
        # no gate call yet and a zero minimum: nothing to divide by (review #74)
        self.assertEqual(watchdog.decide(dollars=1, max_cost=10, calls=0, failed=0, merged=None,
                                         p90_cost=None, p90_gate=0.1, multiple=2, min_calls=0), [])

    def test_a_zero_minimum_is_refused(self):
        for flag in ("--min-gate-calls", "--min-tasks"):
            proc = subprocess.run([sys.executable, str(WATCHDOG), "--root", str(self.root),
                                   "--max-cost", "10", "--p90-gate-failure-rate", "0.1",
                                   flag, "0", "--once", "--dry-run"],
                                  capture_output=True, text=True, env=clean_env())
            with self.subTest(flag=flag):
                self.assertEqual(proc.returncode, 64, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    @unittest.skipUnless(os.path.exists("/dev/full"), "no /dev/full here (a Linux device)")
    def test_a_full_disk_does_not_kill_it(self):
        """OPS-11: the progress print raised ENOSPC and the watchdog died."""
        with open("/dev/full", "w") as full:
            proc = subprocess.Popen([sys.executable, str(WATCHDOG), "--root", str(self.root),
                                     "--max-cost", "10", "--interval", "0.1", "--dry-run"],
                                    stdout=full, stderr=subprocess.PIPE, env=clean_env())
        self.addCleanup(proc.kill)
        time.sleep(2.5)
        self.assertIsNone(proc.poll(), proc.stderr.read() if proc.poll() is not None else "")
        (self.root / "watchdog.stop").write_text("")
        self.assertEqual(proc.wait(timeout=10), 0)
        proc.stderr.close()

    @NEEDS_PROC
    def test_an_orphan_watches_the_tend_run_to_its_end_then_goes(self):
        """OPS-06: a watchdog whose sequencer died looped forever.

        And it leaves within seconds of the tend run's end, not a watch interval
        (300 s by default) later, when a restart would already be running beside it.
        """
        root = str(self.root)
        tend = subprocess.Popen(["bash", "-c", f'exec -a "tend run --root {root}" sleep 3'])
        self.addCleanup(tend.kill)
        # a parent that leaves at once: the watchdog is re-parented
        parent = subprocess.run(
            ["bash", "-c", f'{shlex.quote(sys.executable)} {shlex.quote(str(WATCHDOG))} --root '
                           f'{shlex.quote(root)} --max-cost 10 --interval 300 --dry-run --parent $$ '
                           f'>/dev/null 2>&1 & echo $!'],
            capture_output=True, text=True, env=clean_env())
        pid = int(parent.stdout.strip())
        def alive() -> bool:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except OSError:
                return False
            return state != "Z"
        time.sleep(1.5)
        self.assertTrue(alive(), "the watchdog left while its tend run was live")
        tend.wait()
        deadline = time.monotonic() + 3 * watchdog.POLL + 2
        while alive() and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertFalse(alive(), "the orphaned watchdog outlived its tend run")

    @NEEDS_PROC
    def test_stop_others_stops_every_other_watchdog_on_the_root_alone(self):
        other = self.tmp / "r-1-tend-b"
        other.mkdir()
        def start(root: Path) -> subprocess.Popen:
            proc = subprocess.Popen([sys.executable, str(WATCHDOG), "--root", str(root),
                                     "--max-cost", "10", "--interval", "300", "--dry-run"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    env=clean_env())
            self.addCleanup(lambda: (proc.kill(), proc.wait()))
            return proc
        mine, theirs = start(self.root), start(other)
        deadline = time.monotonic() + 10
        while not (self.root / "watchdog.json").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        proc = subprocess.run([sys.executable, str(WATCHDOG), "--root", str(self.root),
                               "--stop-others"], capture_output=True, text=True, env=clean_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), [str(mine.pid)])
        self.assertEqual(mine.wait(timeout=10), -signal.SIGTERM)
        self.assertIsNone(theirs.poll(), "a watchdog on another root was stopped")
        # nothing left: an empty line, and still no cap needed
        proc = subprocess.run([sys.executable, str(WATCHDOG), "--root", str(self.root),
                               "--stop-others"], capture_output=True, text=True, env=clean_env())
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, ""), proc.stderr)
        # a check still needs its cap
        proc = subprocess.run([sys.executable, str(WATCHDOG), "--root", str(self.root), "--once"],
                              capture_output=True, text=True, env=clean_env())
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--max-cost", proc.stderr)

    def test_it_drains_once_at_the_cap_and_records_why(self):
        envelope(self.root / "usage", "t-1.worker.a.1.json", 4.0)
        envelope(self.root / "usage", "t-2.worker.b.2.json", 3.0)
        (self.root / "usage" / "t-3.worker.c.3.out").write_text("prose, no cost")
        code, record = self.run_wd()
        self.assertEqual((code, record["spend_usd"], record["drained"]), (0, 7.0, False))
        envelope(self.root / "usage", "t-4.reviewer.d.4.json", 3.5)
        code, record = self.run_wd()
        self.assertEqual(code, 2)
        self.assertTrue(record["drained"])
        self.assertIn("reached the shard's cap", record["reasons"][0])
        self.assertEqual(self.drains.read_text().split(), ["drain", "--root", str(self.root)])
        code, record = self.run_wd()
        self.assertEqual(len(self.drains.read_text().splitlines()), 1, "drained twice")
        self.assertTrue(json.loads((self.root / "watchdog.json").read_text())["drained"])
        self.assertEqual(len((self.root / "watchdog.jsonl").read_text().splitlines()), 3)

    def test_the_gate_log_and_merged_tasks_feed_the_rules(self):
        log = self.root / "gate-log"
        log.mkdir()
        # thirty of tend's calls (changed_files), and ten of an agent's own --findings
        # checks, which the rate leaves out
        lines = ([{"ok": i % 2 == 0, "changed_files": True} for i in range(30)]
                 + [{"ok": False, "changed_files": False}] * 10)
        (log / "gate.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
        entry = self.tmp / "entry"
        entry.mkdir()
        git(entry, "init", "-q", "-b", "main")
        for task in ("t-1", "t-2"):
            (entry / "findings" / task).mkdir(parents=True)
            (entry / "findings" / task / "s.json").write_text("{}")
        git(entry, "add", "-A")
        git(entry, "commit", "-qm", "merged")
        self.assertEqual(watchdog.merged_tasks(entry, "main"), 2)
        code, record = self.run_wd("--entrypoint", str(entry), "--p90-gate-failure-rate", "0.1",
                                   "--dry-run")
        self.assertEqual((record["gate_calls"], record["gate_failed"], record["merged_tasks"]),
                         (30, 15, 2))
        self.assertTrue(record["reasons"])
        self.assertFalse(record["drained"])
        self.assertFalse(self.drains.exists(), "--dry-run drained")

    def test_pre_merge_calls_are_counted_apart_never_as_attempts(self):
        """Port3 section 5 item 1: tend's pre-merge command re-runs the gate over a batch of
        contributions it already validated, and its line said nothing of it, so every merged
        contribution counted twice and a batch conflict raised the failure rate.  The line
        now says ``pre_merge: true``; the rate is over the validation calls alone, a line
        from a gate older than the field reads as one, and the pre-merge calls are counted
        apart in the record."""
        log = self.root / "gate-log"
        log.mkdir()
        lines = ([{"ok": i % 10 != 0, "changed_files": True, "pre_merge": False}
                  for i in range(17)]
                 + [{"ok": True, "changed_files": True}] * 3       # a gate older than the field
                 + [{"ok": i % 2 == 0, "changed_files": True, "pre_merge": True}
                    for i in range(20)]
                 + [{"ok": False, "changed_files": False, "pre_merge": False}] * 5)
        path = log / "gate.jsonl"
        path.write_text("".join(json.dumps(l) + "\n" for l in lines))
        self.assertEqual(watchdog.gate_calls(path), (20, 2))
        self.assertEqual(watchdog.gate_counts(path), {"calls": 20, "failed": 2,
                                                      "pre_merge_calls": 20,
                                                      "pre_merge_failed": 10})
        # 2 of 20 validations is 0.1, under 2 x 0.1; counted with the pre-merge calls
        # it was 12 of 40, which drained the root
        code, record = self.run_wd("--p90-gate-failure-rate", "0.1", "--dry-run")
        self.assertEqual((record["gate_calls"], record["gate_failed"]), (20, 2))
        self.assertEqual((record["pre_merge_calls"], record["pre_merge_failed"]), (20, 10))
        self.assertEqual(record["reasons"], [])

    def test_a_shell_prefix_runs_through_bash(self):
        envelope(self.root / "usage", "t.json", 11)
        code, record = self.run_wd("--tend", "export X=1; env")
        self.assertTrue(record["drained"], record)
        self.assertIn("export X=1; env tend-control drain", record["action"])

    def test_the_inlined_git_pins_are_gitsafes(self):
        self.assertEqual(watchdog._GIT_SAFE_CONFIG, gitsafe.SAFE_CONFIG)
        self.assertEqual(watchdog._GIT_SAFE_ENV, gitsafe.SAFE_ENV)


class TestT1TheWatchdogCountsCallsThatLeftNoEnvelope(_WatchdogRoot):
    """Field defect T1: a call that timed out left no priced envelope, so the watchdog's
    floor never saw its spend.  agent_call now keeps a record of such a call
    (``total_cost_usd: null``, its elapsed time), and the watchdog estimates it."""

    def timed_out(self, name: str, seconds: float) -> None:
        from run import agent_call

        record = agent_call.call_record(outcome="timeout", role="worker", task_id="t-9",
                                        slot=1, seconds=seconds, timeout=2700.0, code=124)
        (self.root / "usage").mkdir(parents=True, exist_ok=True)
        (self.root / "usage" / name).write_text(json.dumps(record))

    def test_t1_a_timed_out_call_is_estimated_at_the_roots_highest_rate(self):
        usage = self.root / "usage"
        usage.mkdir(parents=True)
        (usage / "t-1.worker.a.1.json").write_text(json.dumps(
            {"subtype": "success", "total_cost_usd": 3.0, "duration_ms": 1_000_000}))
        (usage / "t-2.worker.b.2.json").write_text(json.dumps(
            {"subtype": "success", "total_cost_usd": 1.0, "duration_ms": 1_000_000}))
        self.timed_out("t-9.worker.c.3.json", 2700.0)
        got = watchdog.ledger(usage)
        self.assertEqual((got["dollars"], got["unpriced_calls"], got["unpriced_seconds"]),
                         (4.0, 1, 2700.0))
        self.assertAlmostEqual(got["estimated_unpriced_usd"], 8.1)    # 2700 s x $0.003/s
        code, record = self.run_wd()
        # the floor alone ($4) is under the $10 cap; with the timed-out call it is not
        self.assertEqual(code, 2)
        self.assertTrue(record["drained"])
        self.assertEqual((record["spend_floor_usd"], record["unpriced_calls"]), (4.0, 1))
        self.assertAlmostEqual(record["spend_usd"], 12.1)
        self.assertIn("reached the shard's cap", record["reasons"][0])

    def test_t1_before_any_priced_envelope_the_fallback_rate_or_no_estimate(self):
        self.timed_out("t-9.worker.c.3.json", 1800.0)
        code, record = self.run_wd("--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(record["unpriced_calls"], 1)
        self.assertIsNone(record["estimated_unpriced_usd"])
        code, record = self.run_wd("--dry-run", "--unpriced-usd-per-hour", "30")
        self.assertAlmostEqual(record["estimated_unpriced_usd"], 15.0)
        self.assertIn("reached the shard's cap", record["reasons"][0])


class WorkspaceCase(unittest.TestCase):
    """A workspace with an allowlisted host whose remote root is a local temp dir."""

    def setUp(self):
        self.tmp = scratch(self)
        self.ws_dir = self.tmp / "ws"
        self.remote = self.tmp / "remote"
        self.remote.mkdir()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cf_main.main(["init", str(self.ws_dir), "--no-git", "-q"])
        ws = Workspace.load(self.ws_dir, validate=False)
        ws.data["hosts"] = {"allowlist": ["examplebox"],
                            "examplebox": {"remote_root": str(self.remote), "tend_cmd": "env",
                                           "ssh": "examplebox"}}
        ws.save()
        self.ws = Workspace.load(self.ws_dir, validate=False)

    def shard(self, run_id: str, *, tasks: int = 3, cap: float | None = 10.0,
              locate: bool = True, host: str = "examplebox") -> None:
        rd = self.ws_dir / "runs" / run_id
        (rd / "tasks").mkdir(parents=True)
        for i in range(tasks):
            (rd / "tasks" / f"t-{i}.yaml").write_text(f"id: t-{i}\n")
        lines = [f"version: 1", f"run_id: {run_id}", "mode: locate",
                 f"host:\n  name: {host}\n  kind: ssh\n  remote_root: {self.remote}",
                 "concurrency:\n  workers: 3\n  reviewers: 2"]
        if locate:
            lines.append("locate:\n  mode: locate")
        if cap is not None:
            lines.append(f"budget:\n  currency: USD\n  max_cost: {cap}")
        (rd / "run.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def pilot(self, run_id: str = "p-1", *, verified: bool = True,
              envelopes: tuple = ((3.0, 1_000_000),)) -> str:
        """A smoke-tested run, with the agent envelopes ``cf harvest`` brought home from it:
        ``(total_cost_usd, duration_ms)`` each, by default one at $10.80 an hour."""
        rd = self.ws_dir / "runs" / run_id
        rd.mkdir(parents=True)
        (rd / "run.yaml").write_text(f"version: 1\nrun_id: {run_id}\nmode: locate\n"
                                     f"locate:\n  mode: locate\n"
                                     f"run:\n  smoke_verified: {str(verified).lower()}\n")
        self.pilot_envelopes(run_id, envelopes)
        return run_id

    def pilot_envelopes(self, run_id: str, envelopes) -> Path:
        home = self.ws_dir / "runs" / run_id / "host" / "envelopes"
        for i, (usd, millis) in enumerate(envelopes):
            home.mkdir(parents=True, exist_ok=True)
            data = {"subtype": "success", "total_cost_usd": usd}
            if millis is not None:
                data["duration_ms"] = millis
            (home / f"t-{i}.worker.a.{i}.json").write_text(json.dumps(data))
        return home


class TestCampaignPlan(WorkspaceCase):

    def test_a_plan_orders_the_shards_and_records_the_thresholds(self):
        self.shard("r-1")
        self.shard("r-2", tasks=5, cap=20)
        path, doc = campaign_mod.plan(self.ws, "c1", ["r-1", "r-2"], host="examplebox",
                                      p90_cost_per_task=4.0, p90_gate_failure_rate=0.1)
        self.assertEqual([s["run_id"] for s in doc["shards"]], ["r-1", "r-2"])
        self.assertEqual((doc["tasks"], doc["max_cost"]), (8, 30.0))
        self.assertEqual(doc["watchdog"]["multiple"], 2.0)
        created = doc["created"]
        _path, again = campaign_mod.plan(self.ws, "c1", ["r-1", "r-2"], host="examplebox",
                                         p90_cost_per_task=4.0, p90_gate_failure_rate=0.1)
        self.assertEqual(again["created"], created)

    def test_the_refusals(self):
        self.shard("big", tasks=501)
        self.shard("nocap", cap=None)
        self.shard("faith", locate=False)
        self.shard("elsewhere", host="otherbox")
        self.shard("ok")
        cases = {"big": "at most 500", "nocap": "spend cap", "faith": "not a locate run",
                 "elsewhere": "planned for host"}
        for run_id, needle in cases.items():
            with self.subTest(run=run_id), self.assertRaises(CfError) as caught:
                campaign_mod.plan(self.ws, "c1", [run_id], host="examplebox")
            self.assertIn(needle, str(caught.exception))
        with self.assertRaises(CfError):
            campaign_mod.plan(self.ws, "c1", ["ok", "ok"], host="examplebox")
        with self.assertRaises(CfError):
            campaign_mod.plan(self.ws, "c1", ["ok"], host="nothere")
        with self.assertRaises(CfError):
            campaign_mod.plan(self.ws, "c1", ["ok"], host="examplebox", multiple=1)

    def test_the_shards_are_stamped_and_belong_to_one_campaign(self):
        from cflib.yamlio import read_yaml

        self.shard("r-1")
        self.shard("r-2")
        campaign_mod.plan(self.ws, "c1", ["r-1"], host="examplebox")
        self.assertEqual(read_yaml(self.ws_dir / "runs" / "r-1" / "run.yaml")["locate"]["campaign"],
                         "c1")
        with self.assertRaises(CfError) as caught:
            campaign_mod.plan(self.ws, "c2", ["r-2", "r-1"], host="examplebox")
        self.assertIn("belongs to campaign 'c1'", str(caught.exception))
        self.assertNotIn("campaign", read_yaml(self.ws_dir / "runs" / "r-2" / "run.yaml")["locate"])

    def test_an_id_a_shell_would_expand_is_refused(self):
        self.shard("r-1")
        for bad in ("c$(touch${IFS}PWNED)", "c`id`", "c 1", "-c1"):
            with self.subTest(id=bad), self.assertRaises(CfError):
                campaign_mod.plan(self.ws, bad, ["r-1"], host="examplebox")

    def test_a_rendered_campaign_may_only_append(self):
        for run_id in ("r-0", "r-1", "r-2", "r-3"):
            self.shard(run_id)
        campaign_mod.plan(self.ws, "c1", ["r-1", "r-2"], host="examplebox", pilot=self.pilot())
        campaign_mod.render(self.ws, "c1")
        with self.assertRaises(CfError) as caught:
            campaign_mod.plan(self.ws, "c1", ["r-0", "r-1", "r-2"], host="examplebox")
        self.assertIn("may only append", str(caught.exception))
        _path, doc = campaign_mod.plan(self.ws, "c1", ["r-1", "r-2", "r-3"], host="examplebox")
        self.assertEqual(doc["pilot"], "p-1")

    def test_a_host_path_is_absolute_or_under_the_hosts_home(self):
        """OPS-04: a relative path, or one only a shell would expand, is refused at plan time."""
        self.shard("r-1")
        for kwargs in ({"transcript_dirs": "tx"}, {"transcript_dirs": "/t/a:rel"},
                       {"transcript_dirs": "$HOME/tx"}, {"canaries": "canaries.txt"},
                       {"canaries": "~other/canaries.txt"}):
            with self.subTest(**kwargs), self.assertRaises(CfError) as caught:
                campaign_mod.plan(self.ws, "c1", ["r-1"], host="examplebox", **kwargs)
            self.assertIn("~/", caught.exception.hint)
        _path, doc = campaign_mod.plan(self.ws, "c1", ["r-1"], host="examplebox",
                                       transcript_dirs="~/tx:/srv/tx", canaries="~/c.txt")
        self.assertEqual((doc["transcript_dirs"], doc["canaries"]), ("~/tx:/srv/tx", "~/c.txt"))
        # the host's own default is held to the same rule at render
        self.ws.data["hosts"]["examplebox"]["transcript_dirs"] = "projects"
        self.ws.save()
        self.ws = Workspace.load(self.ws_dir, validate=False)
        campaign_mod.plan(self.ws, "c2", [self.shard_named("r-2")], host="examplebox",
                          pilot=self.pilot())
        with self.assertRaises(CfError) as caught:
            campaign_mod.render(self.ws, "c2")
        self.assertIn("transcript_dirs", str(caught.exception))

    def shard_named(self, run_id: str) -> str:
        self.shard(run_id)
        return run_id

    def test_render_refuses_a_shard_re_planned_since_the_campaign(self):
        """OPS-05: render kept campaign.yaml's stale cap and never read the shard again."""
        from cflib.yamlio import read_yaml, write_yaml

        self.shard("r-1")
        self.shard("r-2")
        campaign_mod.plan(self.ws, "c1", ["r-1", "r-2"], host="examplebox", pilot=self.pilot(),
                          p90_cost_per_task=4.0, p90_gate_failure_rate=0.1, multiple=3,
                          min_tasks=7)
        path = self.ws_dir / "runs" / "r-1" / "run.yaml"
        planned = read_yaml(path)
        cases = {"max_cost": lambda d: d["budget"].update(max_cost=7.0),
                 "workers": lambda d: d["concurrency"].update(workers=5),
                 "campaign": lambda d: d["locate"].pop("campaign")}
        for needle, change in cases.items():
            doc = read_yaml(path)
            change(doc)
            write_yaml(path, doc)
            with self.subTest(change=needle), self.assertRaises(CfError) as caught:
                campaign_mod.render(self.ws, "c1")
            self.assertIn(needle, str(caught.exception))
            self.assertIn("cf campaign plan c1", caught.exception.hint)
            write_yaml(path, planned)
        (self.ws_dir / "runs" / "r-2" / "tasks" / "t-9.yaml").write_text("id: t-9\n")
        with self.assertRaises(CfError) as caught:
            campaign_mod.render(self.ws, "c1")
        self.assertIn("tasks", str(caught.exception))
        # re-running the plan reads the shards again and keeps what it is not given
        doc = read_yaml(path)
        doc["budget"]["max_cost"] = 7.0
        doc["locate"].pop("campaign")
        write_yaml(path, doc)
        _path, again = campaign_mod.plan(self.ws, "c1", ["r-1", "r-2"], host="examplebox")
        self.assertEqual(again["watchdog"], {"p90_cost_per_task": 4.0, "p90_gate_failure_rate": 0.1,
                                             "multiple": 3, "min_tasks": 7,
                                             "unpriced_usd_per_hour": 10.8,
                                             "unpriced_usd_per_hour_pilot": "p-1"})
        self.assertEqual(read_yaml(path)["locate"]["campaign"], "c1")
        campaign_mod.render(self.ws, "c1")
        text = (self.ws_dir / "campaigns" / "c1" / "host" / "sequencer.sh").read_text()
        line = next(l for l in text.splitlines() if l.startswith("run_shard 1 "))
        self.assertIn("7", shlex.split(line))
        self.assertIn("--multiple 3", text)

    def test_render_needs_a_smoke_tested_pilot(self):
        self.shard("r-1")
        campaign_mod.plan(self.ws, "c1", ["r-1"], host="examplebox")
        with self.assertRaises(CfError) as caught:
            campaign_mod.render(self.ws, "c1")
        self.assertIn("no smoke-tested pilot", str(caught.exception))
        with self.assertRaises(CfError) as caught:
            campaign_mod.plan(self.ws, "c1", ["r-1"], host="examplebox",
                              pilot=self.pilot("p-0", verified=False))
        self.assertIn("records no passed smoke test", str(caught.exception))

    def test_status_says_ungated_not_failed(self):
        self.shard("r-1")
        self.shard("r-2")
        campaign_mod.plan(self.ws, "c1", ["r-1", "r-2"], host="examplebox")
        for run_id, gate in (("r-1", None), ("r-2", {"ok": False, "problems": ["x"]})):
            corpus = self.ws_dir / "corpus" / run_id
            corpus.mkdir(parents=True)
            (corpus / "RECEIPT.json").write_text(json.dumps({"complete": gate is None,
                                                             "gate": gate}))
        rows = {r["run_id"]: r for r in campaign_mod.status(self.ws, "c1")["shards"]}
        self.assertEqual((rows["r-1"]["gated"], rows["r-1"]["gate_ok"]), (False, False))
        self.assertEqual((rows["r-2"]["gated"], rows["r-2"]["gate_ok"]), (True, False))
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            cf_main.main(["--workspace", str(self.ws_dir), "campaign", "status", "c1"])
        table = out.getvalue()
        self.assertIn("UNGATED", table)
        self.assertIn("FAILED", table)

    def test_the_cli(self):
        self.shard("r-1")
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cf_main.main(["--workspace", str(self.ws_dir), "campaign", "plan", "c1",
                                 "--shard", "r-1", "--host", "examplebox",
                                 "--pilot", self.pilot()])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("drains on the spend cap alone", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cf_main.main(["--workspace", str(self.ws_dir), "campaign", "render", "c1"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("--stage verify", out.getvalue())
        self.assertNotIn("--stage smoke", out.getvalue())
        self.assertIn("no transcript directories are known", out.getvalue())
        with redirect_stdout(io.StringIO()) as status, redirect_stderr(io.StringIO()):
            cf_main.main(["--workspace", str(self.ws_dir), "campaign", "status", "c1", "--json"])
        self.assertEqual(json.loads(status.getvalue())["totals"]["harvested"], 0)


class TestTheUnpricedRate(WorkspaceCase):
    """Port3 open item 3: the watchdog read a call that left no priced envelope as $0 until
    its shard's first priced envelope.  campaign.yaml now carries the rate it is estimated
    at before then (``watchdog.unpriced_usd_per_hour``): the operator's, kept on a re-plan,
    else the pilot's highest priced dollars per hour; render refuses a campaign with neither."""

    def setUp(self):
        super().setUp()
        self.shard("r-1")

    def plan(self, **kwargs) -> dict:
        return campaign_mod.plan(self.ws, "c1", ["r-1"], host="examplebox", **kwargs)[1]

    def rate(self, doc: dict) -> tuple:
        return (doc["watchdog"]["unpriced_usd_per_hour"],
                doc["watchdog"]["unpriced_usd_per_hour_pilot"])

    def test_the_default_is_the_pilots_highest_priced_rate_with_a_duration(self):
        from run import agent_call

        pilot = self.pilot(envelopes=((3.0, 1_000_000),     # $10.80 an hour
                                      (1.0, 100_000),       # $36 an hour: the highest
                                      (50.0, None),         # priced, but no duration
                                      (0.0, 1)))            # a duration, but no dollars
        home = self.ws_dir / "runs" / pilot / "host" / "envelopes"
        # a call that left no envelope carries a duration and no price: no rate either
        (home / "t-9.worker.c.9.json").write_text(json.dumps(agent_call.call_record(
            outcome="timeout", role="worker", task_id="t-9", slot=1, seconds=10.0,
            timeout=2700.0, code=124)))
        self.assertEqual(campaign_mod.pilot_usd_per_hour(self.ws, pilot), 36.0)
        self.assertEqual(self.rate(self.plan(pilot=pilot)), (36.0, "p-1"))
        self.assertIsNone(campaign_mod.pilot_usd_per_hour(
            self.ws, self.pilot("p-2", envelopes=((5.0, None),))))
        self.assertIsNone(campaign_mod.pilot_usd_per_hour(self.ws, "never-harvested"))

    def test_the_flag_wins_and_is_kept_on_a_re_plan(self):
        self.assertEqual(self.rate(self.plan(pilot=self.pilot(), unpriced_usd_per_hour=25)),
                         (25.0, None))
        self.assertEqual(self.rate(self.plan()), (25.0, None))
        # a pilot given later measures another rate, and the operator's is still kept
        faster = self.pilot("p-2", envelopes=((1.0, 100_000),))
        doc = self.plan(pilot=faster)
        self.assertEqual((self.rate(doc), doc["pilot"]), ((25.0, None), "p-2"))
        self.assertEqual(self.rate(self.plan(unpriced_usd_per_hour=30.5)), (30.5, None))

    def test_a_measured_rate_follows_the_pilot(self):
        self.assertEqual(self.rate(self.plan(pilot=self.pilot())), (10.8, "p-1"))
        # a re-plan with no flag measures the kept pilot again
        self.pilot_envelopes("p-1", ((3.0, 1_000_000), (2.0, 100_000)))
        self.assertEqual(self.rate(self.plan()), (72.0, "p-1"))
        self.assertEqual(self.rate(self.plan(pilot=self.pilot("p-2", envelopes=(
            (1.0, 100_000),)))), (36.0, "p-2"))
        # the same pilot's rate is kept when its envelopes are no longer here
        shutil.rmtree(self.ws_dir / "runs" / "p-2" / "host")
        self.assertEqual(self.rate(self.plan()), (36.0, "p-2"))
        # another pilot with nothing to measure leaves no rate, never the last pilot's
        self.assertEqual(self.rate(self.plan(pilot=self.pilot("p-3", envelopes=()))),
                         (None, None))

    def test_render_refuses_a_campaign_with_neither(self):
        self.assertEqual(self.rate(self.plan(pilot=self.pilot(envelopes=()))), (None, None))
        with self.assertRaises(CfError) as caught:
            campaign_mod.render(self.ws, "c1")
        self.assertIn("watchdog.unpriced_usd_per_hour", str(caught.exception))
        self.assertIn("cf harvest p-1 --execute", caught.exception.hint)
        self.assertIn("--unpriced-usd-per-hour", caught.exception.hint)
        self.assertFalse((self.ws_dir / "campaigns" / "c1" / "host").exists())
        # the pilot harvested, a re-plan with no flag measures it and render goes on
        self.pilot_envelopes("p-1", ((3.0, 1_000_000),))
        self.assertEqual(self.rate(self.plan()), (10.8, "p-1"))
        campaign_mod.render(self.ws, "c1")

    def test_a_rate_that_is_no_rate_is_refused(self):
        from cflib.yamlio import read_yaml, write_yaml

        pilot = self.pilot()
        for bad in (0, -2.0, float("nan"), float("inf")):
            with self.subTest(rate=bad), self.assertRaises(CfError) as caught:
                self.plan(pilot=pilot, unpriced_usd_per_hour=bad)
            self.assertIn("not a positive dollars-per-hour", str(caught.exception))
        self.plan(pilot=pilot)
        path = self.ws_dir / "campaigns" / "c1" / "campaign.yaml"
        for bad in (0, "ten", True):
            doc = read_yaml(path)
            doc["watchdog"]["unpriced_usd_per_hour"] = bad
            write_yaml(path, doc)
            with self.subTest(edited=bad), self.assertRaises(CfError) as caught:
                campaign_mod.render(self.ws, "c1")
            self.assertIn("campaign.yaml watchdog.unpriced_usd_per_hour", str(caught.exception))

    def test_the_sequencers_watchdog_is_given_the_rate(self):
        """Through ``_watchdog_flags``: the rendered watchdog estimates a timed-out call at
        the campaign's rate before its root has a priced envelope."""
        from run import agent_call

        self.plan(pilot=self.pilot())
        campaign_mod.render(self.ws, "c1")
        host = self.ws_dir / "campaigns" / "c1" / "host"
        line = next(l for l in (host / "sequencer.sh").read_text().splitlines()
                    if l.startswith("WATCHDOG_FLAGS=("))
        flags = shlex.split(line[len("WATCHDOG_FLAGS=("):-1])
        self.assertEqual(flags[flags.index("--unpriced-usd-per-hour") + 1], "10.8")
        self.assertNotIn("p-1", flags)
        root = self.tmp / "wd-root"
        (root / "usage").mkdir(parents=True)
        (root / "usage" / "t-9.worker.c.9.json").write_text(json.dumps(agent_call.call_record(
            outcome="timeout", role="worker", task_id="t-9", slot=1, seconds=1800.0,
            timeout=2700.0, code=124)))
        proc = subprocess.run([sys.executable, str(host / "watchdog.py"), *flags, "--root",
                               str(root), "--max-cost", "100", "--once", "--dry-run"],
                              capture_output=True, text=True, env=clean_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertAlmostEqual(record["estimated_unpriced_usd"], 5.4)    # 1800 s at $10.80/h

    def test_the_cli(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cf_main.main(["--workspace", str(self.ws_dir), "campaign", "plan", "c1",
                                 "--shard", "r-1", "--host", "examplebox",
                                 "--pilot", self.pilot(envelopes=())])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("render refuses the campaign", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cf_main.main(["--workspace", str(self.ws_dir), "campaign", "plan", "c1",
                                 "--shard", "r-1", "--host", "examplebox",
                                 "--unpriced-usd-per-hour", "12.5"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("$12.5/hour (--unpriced-usd-per-hour)", out.getvalue())
        self.assertEqual(campaign_mod.load(self.ws, "c1")["watchdog"]["unpriced_usd_per_hour"],
                         12.5)


class TestTheSequencer(WorkspaceCase):
    """Render the host side, then run it against fake tend binaries."""

    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "fakebin"
        self.bin.mkdir()
        self.log = self.tmp / "tend.log"
        self.transcripts = self.tmp / "transcripts"
        self.transcripts.mkdir()
        # `tend run --root R ...`: spend what R/spend says, write one transcript
        # naming a worktree under R, as an agent's does (R/words is any text it
        # should hold, R/tname its name), and, when R/foreign exists, a foreign
        # session's transcript holding that text; then sleep R/sleep seconds
        # (default 2: long enough for the watchdog to look) and finish.
        # FAKE_TX overrides the transcript directory.
        (self.bin / "tend").write_text(
            '#!/bin/sh\nroot=""; while [ $# -gt 0 ]; do [ "$1" = --root ] && root="$2"; shift; done\n'
            f'echo "run $root" >> "{self.log}"\n'
            'mkdir -p "$root/usage"\n'
            'printf \'{"subtype":"success","total_cost_usd":%s}\' "$(cat "$root/spend")" '
            '> "$root/usage/t.worker.x.1.json"\n'
            f'tx="${{FAKE_TX:-{self.transcripts}}}"\n'
            'name="$(cat "$root/tname" 2>/dev/null || basename "$root")"\n'
            'printf \'{"model":"claude-opus-5","cwd":"%s/worktrees/w-1","usage":{"input":1,'
            '"output":2,"cache_write":0,"cache_read":0},"x":"%s"}\\n\' "$root" '
            '"$(cat "$root/words" 2>/dev/null)" > "$tx/$name.jsonl"\n'
            'if [ -e "$root/foreign" ]; then mkdir -p "$tx/other"; '
            'cat "$root/foreign" > "$tx/other/session.jsonl"; fi\n'
            'sleep "$(cat "$root/sleep" 2>/dev/null || echo 2)"\n')
        (self.bin / "tend-control").write_text(
            f'#!/bin/sh\necho "control $*" >> "{self.log}"\nexit "${{FAKE_DRAIN_EXIT:-0}}"\n')
        for f in self.bin.iterdir():
            f.chmod(0o755)
        for run_id, spend in (("r-1", 1), ("r-2", 50), ("r-3", 1)):
            self.shard(run_id, cap=10)
            root = self.remote / f"{run_id}-tend"
            root.mkdir()
            (root / "spend").write_text(str(spend))

    def render(self, **plan_extra):
        campaign_mod.plan(self.ws, "c1", ["r-1", "r-2", "r-3"], host="examplebox",
                          pilot=self.pilot(), **plan_extra)
        return campaign_mod.render(self.ws, "c1")

    def sequencer_env(self, **env) -> dict:
        env.setdefault("CF_TRANSCRIPT_DIRS", str(self.transcripts))
        if env["CF_TRANSCRIPT_DIRS"] is None:          # the rendered default
            del env["CF_TRANSCRIPT_DIRS"]
        return clean_env(PATH=f"{self.bin}:{os.environ['PATH']}", CF_WATCH_INTERVAL="0.3", **env)

    def sequencer(self) -> Path:
        """The rendered sequencer, or a skip where it cannot run."""
        if shutil.which("flock") is None:
            # the sequencer is the Linux run host's: it holds its campaign with
            # util-linux flock, which macOS does not ship
            self.skipTest("flock is not installed here; the sequencer runs on the Linux run host")
        return self.ws_dir / "campaigns" / "c1" / "host" / "sequencer.sh"

    def sequence(self, **env) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(self.sequencer())], capture_output=True, text=True,
                              timeout=180, env=self.sequencer_env(**env))

    def start(self, **env) -> subprocess.Popen:
        """The sequencer in the background, in its own session (so a test can kill it all)."""
        proc = subprocess.Popen(["bash", str(self.sequencer())], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True,
                                env=self.sequencer_env(**env))

        def reap():
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                pass
            proc.wait()
        self.addCleanup(reap)
        return proc

    def wait_for(self, predicate, timeout: float = 20.0, what: str = "") -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail(f"timed out waiting for {what or predicate}")
            time.sleep(0.1)

    def watchdogs(self, root: Path) -> list[int]:
        """The pids of live watchdog.py processes watching *root*."""
        out = []
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                argv = (proc / "cmdline").read_bytes().split(b"\0")
                state = (proc / "stat").read_text().rsplit(")", 1)[1].split()[0]
            except OSError:
                continue
            if state != "Z" and any(a.endswith(b"watchdog.py") for a in argv) \
                    and str(root).encode() in argv:
                out.append(int(proc.name))
        return out

    def state(self) -> Path:
        return self.remote / "campaigns" / "c1"

    def runs(self) -> list[str]:
        return [line.split()[1] for line in self.log.read_text().splitlines()
                if line.startswith("run ")]

    def test_the_render(self):
        out = self.render()
        host = self.ws_dir / "campaigns" / "c1" / "host"
        sequencer = host / "sequencer.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(sequencer)]).returncode, 0)
        self.assertTrue(os.access(sequencer, os.X_OK))
        text = sequencer.read_text()
        self.assertLess(text.index("run_shard 1 r-1"), text.index("run_shard 2 r-2"))
        self.assertTrue((host / "watchdog.py").is_file() and (host / "usage.py").is_file())
        self.assertTrue((host / "canary_scan.py").is_file())
        for needle in ("from cflib", "import cflib", "cf.py"):
            self.assertNotIn(needle, text)
        doc = campaign_mod.load(self.ws, "c1")
        self.assertEqual(doc["sequencer"]["sha256"], out["sequencer"]["sha256"])
        self.assertIn("rsync -a", out["transfer"])
        # OPS-12: rsync makes only the last directory of its destination, and
        # nothing else makes <remote root>/campaigns on a fresh host
        words = shlex.split(out["transfer"])
        self.assertEqual(words[:2], ["ssh", "examplebox"])
        self.assertEqual(shlex.split(words[2]), ["mkdir", "-p", f"{self.remote}/campaigns/c1"])
        self.assertEqual(words[3:5], ["&&", "rsync"])

    def test_it_runs_in_order_and_stops_when_a_watchdog_drains(self):
        self.render()
        # a transcript older than the campaign is neither exported nor pruned, though
        # it names r-1's root (an earlier run there): the baseline alone leaves it out
        old = self.transcripts / "operator.jsonl"
        old.write_text(json.dumps({"model": "claude-opus-5", "usage": {"input": 7},
                                   "cwd": f"{self.remote}/r-1-tend/worktrees/w-0"}) + "\n")
        proc = self.sequence()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self.runs(), [str(self.remote / "r-1-tend"), str(self.remote / "r-2-tend")])
        self.assertIn(f"control drain --root {self.remote / 'r-2-tend'}", self.log.read_text())
        state = self.remote / "campaigns" / "c1"
        self.assertTrue((state / "shard-r-1.done").exists())
        self.assertFalse((state / "shard-r-2.done").exists())
        self.assertTrue((state / "STOPPED").exists())
        self.assertIn("DRAINED by the watchdog", (state / "sequencer.log").read_text())
        # each shard's export holds its own transcript only, which was pruned
        for run_id in ("r-1", "r-2"):
            rows = (state / f"usage-{run_id}.jsonl").read_text().splitlines()
            self.assertEqual(len(rows), 1, rows)
            self.assertFalse((self.transcripts / f"{run_id}-tend.jsonl").exists())
        self.assertTrue(old.exists())
        # a stopped campaign refuses to resume until the operator says so
        again = self.sequence()
        self.assertEqual(again.returncode, 2)
        self.assertIn("STOPPED", again.stderr)

    def test_a_resumed_shard_keeps_its_first_attempts_usage(self):
        self.render()
        (self.remote / "r-2-tend" / "spend").write_text("1")
        fake = (self.bin / "tend").read_text()
        self.assertIn('|| echo 2)"\n', fake)
        (self.bin / "tend").write_text(fake.replace(
            '|| echo 2)"\n', '|| echo 2)"\ncase "$root" in *r-2-tend) exit 3;; esac\n'))
        first = self.sequence()
        self.assertEqual(first.returncode, 3, first.stderr)
        second = self.sequence()
        self.assertEqual(second.returncode, 3, second.stderr)
        rows = (self.remote / "campaigns" / "c1" / "usage-r-2.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 2, "the second attempt overwrote the first's usage")

    def test_a_failed_drain_or_a_late_cap_still_stops_the_campaign(self):
        self.render()
        proc = self.sequence(FAKE_DRAIN_EXIT="1")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self.runs(), [str(self.remote / "r-1-tend"), str(self.remote / "r-2-tend")])
        state = self.remote / "campaigns" / "c1"
        self.assertTrue((state / "STOPPED").exists())
        self.assertIn("the drain FAILED", (state / "sequencer.log").read_text())

    def test_a_label_canary_stops_the_campaign_and_keeps_the_transcripts(self):
        canaries = self.tmp / "canaries.txt"
        canaries.write_text("cf-bench-canary-" + "0" * 32 + "\n")
        self.render(canaries=str(canaries))
        (self.remote / "r-1-tend" / "words").write_text("cf-bench-canary-" + "0" * 32)
        proc = self.sequence()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self.runs(), [str(self.remote / "r-1-tend")])
        state = self.remote / "campaigns" / "c1"
        report = json.loads((state / "canary-r-1.json").read_text())
        self.assertEqual((report["scanned"], report["hits"]), (1, ["r-1-tend.jsonl"]))
        self.assertTrue((self.transcripts / "r-1-tend.jsonl").exists())
        self.assertIn("CANARY", (state / "sequencer.log").read_text())

    CANARY = "cf-bench-canary-" + "0" * 32

    def canaries(self) -> Path:
        path = self.tmp / "canaries.txt"
        path.write_text(self.CANARY + "\n")
        return path

    def transcript(self, name: str, root: Path, **extra) -> Path:
        """A transcript an agent of *root* wrote (its cwd is a worktree under the root)."""
        path = self.transcripts / name
        path.write_text(json.dumps({"model": "claude-opus-5", "cwd": f"{root}/worktrees/w-9",
                                    "usage": {"input": 1, "output": 1}, **extra}) + "\n")
        return path

    def test_an_interrupted_shard_keeps_its_baseline_and_scans_what_it_left(self):
        """OPS-01: a restart re-took the baseline, so the first attempt went unscanned."""
        self.render(canaries=str(self.canaries()))
        r1 = self.remote / "r-1-tend"
        (r1 / "words").write_text(self.CANARY)
        (r1 / "sleep").write_text("30")
        first = self.start()
        leaked = self.transcripts / "r-1-tend.jsonl"
        self.wait_for(leaked.exists, what="the first attempt's transcript")
        os.killpg(first.pid, signal.SIGKILL)          # the host reboots mid-shard
        first.wait()
        (r1 / "words").unlink()
        (r1 / "sleep").write_text("0")
        (r1 / "tname").write_text("r-1-tend-2")
        proc = self.sequence()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        report = json.loads((self.state() / "canary-r-1.json").read_text())
        self.assertEqual(report["hits"], ["r-1-tend.jsonl"])
        rows = (self.state() / "usage-r-1.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1, "the interrupted attempt's usage was not exported")
        self.assertTrue(leaked.exists())
        # what the interrupted attempt left is settled before anything is relaunched,
        # and a shard with a canary on record is not relaunched at all
        self.assertEqual(self.runs(), [str(r1)])
        self.assertFalse((self.state() / "shard-r-1.done").exists())
        self.assertTrue((self.state() / "STOPPED").exists())

    def test_a_clean_interrupted_attempt_is_exported_once_and_its_scan_kept(self):
        self.render(canaries=str(self.canaries()))
        r1 = self.remote / "r-1-tend"
        (r1 / "sleep").write_text("30")
        (self.remote / "r-2-tend" / "spend").write_text("1")
        first = self.start()
        self.wait_for((self.transcripts / "r-1-tend.jsonl").exists, what="a transcript")
        os.killpg(first.pid, signal.SIGKILL)
        first.wait()
        (r1 / "sleep").write_text("0")
        (r1 / "tname").write_text("r-1-tend-2")
        proc = self.sequence()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.runs(), [str(r1), str(r1), str(self.remote / "r-2-tend"),
                                       str(self.remote / "r-3-tend")])
        report = json.loads((self.state() / "canary-r-1.json").read_text())
        self.assertEqual((report["scanned"], report["hits"]), (2, []))
        rows = (self.state() / "usage-r-1.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 2, rows)
        self.assertEqual(sorted(p.name for p in self.transcripts.glob("r-1-*")), [])

    def test_a_canary_report_with_a_hit_is_never_replaced(self):
        """OPS-02: a resume after a canary stop overwrote the hit with a clean report."""
        self.render(canaries=str(self.canaries()))
        r1 = self.remote / "r-1-tend"
        (r1 / "words").write_text(self.CANARY)
        self.assertEqual(self.sequence().returncode, 2)
        report = self.state() / "canary-r-1.json"
        held = report.read_text()
        self.assertIn("r-1-tend.jsonl", held)
        # the operator removes STOPPED, the one resume the log names, and a late
        # transcript of the root's appears
        (self.state() / "STOPPED").unlink()
        (r1 / "words").unlink()
        (r1 / "tname").write_text("r-1-tend-2")
        self.transcript("late.jsonl", r1)
        proc = self.sequence()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(report.read_text(), held)
        aside = sorted((self.state() / "canary-r-1.attempts").glob("*.json"))
        self.assertEqual(len(aside), 1, aside)
        self.assertEqual(json.loads(aside[0].read_text())["hits"], [])
        self.assertEqual(self.runs(), [str(r1)], "a shard with a canary hit was relaunched")
        self.assertIn("CF_ACCEPT=r-1", proc.stderr)
        self.assertFalse((self.state() / "shard-r-1.done").exists())
        # accepting it moves the campaign on, and the hit stays for the harvest
        (self.state() / "STOPPED").unlink()
        proc = self.sequence(CF_ACCEPT="r-1")
        self.assertEqual(proc.returncode, 2, proc.stderr)        # r-2 drains
        self.assertEqual(self.runs(), [str(r1), str(self.remote / "r-2-tend")])
        self.assertIn("accepted", (self.state() / "shard-r-1.done").read_text())
        self.assertIn("canary", (self.state() / "shard-r-1.done").read_text())
        self.assertEqual(report.read_text(), held)

    def test_a_transcript_that_names_none_of_the_shards_paths_is_left_alone(self):
        """OPS-03: every new file in the directory was taken, exported and deleted."""
        self.render()
        r1 = self.remote / "r-1-tend"
        # another session of the same user, in a run whose root's name extends this one's
        (r1 / "foreign").write_text(json.dumps({
            "model": "claude-opus-5", "cwd": f"{self.remote}/r-1-tend-b/worktrees/w-1",
            "usage": {"input": 5, "output": 5}}) + "\n")
        proc = self.sequence()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        foreign = self.transcripts / "other" / "session.jsonl"
        self.assertTrue(foreign.exists(), "another run's transcript was pruned")
        rows = (self.state() / "usage-r-1.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1, rows)
        self.assertFalse((self.transcripts / "r-1-tend.jsonl").exists())
        self.assertIn("left alone", (self.state() / "sequencer.log").read_text())

    def test_a_tilde_in_a_host_path_is_the_hosts_home(self):
        """OPS-04: '~/…' arrived quoted, so nothing was scanned or exported."""
        home = self.tmp / "home"
        (home / "tx").mkdir(parents=True)
        (home / "canaries.txt").write_text(self.CANARY + "\n")
        self.render(transcript_dirs="~/tx", canaries="~/canaries.txt")
        (self.remote / "r-1-tend" / "words").write_text(self.CANARY)
        proc = self.sequence(CF_TRANSCRIPT_DIRS=None, HOME=str(home), FAKE_TX=str(home / "tx"))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        report = json.loads((self.state() / "canary-r-1.json").read_text())
        self.assertEqual(report["hits"], ["r-1-tend.jsonl"])
        self.assertEqual(len((self.state() / "usage-r-1.jsonl").read_text().splitlines()), 1)

    def test_a_host_path_that_is_not_there_stops_the_sequencer_before_any_shard(self):
        self.render(canaries=str(self.canaries()))
        for env, needle in (({"CF_TRANSCRIPT_DIRS": str(self.tmp / "nowhere")}, "nowhere"),
                            ({"CF_CANARIES": str(self.tmp / "none.txt")}, "none.txt")):
            with self.subTest(env=env):
                proc = self.sequence(**env)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn(needle, proc.stderr)
                self.assertFalse(self.log.exists(), "a shard was launched")

    @NEEDS_PROC
    def test_a_killed_sequencer_leaves_no_watchdog_holding_the_lock(self):
        """OPS-06: the orphaned watchdog held sequencer.lock, so every restart exited 3."""
        self.render()
        r1 = self.remote / "r-1-tend"
        (r1 / "sleep").write_text("4")
        first = self.start()
        self.wait_for(lambda: self.log.exists() and self.runs(), what="r-1's tend run")
        self.wait_for(lambda: self.watchdogs(r1), what="r-1's watchdog")
        os.kill(first.pid, signal.SIGTERM)                  # the sequencer alone
        first.wait(timeout=10)
        # its tend run lives on: a restart takes the lock and refuses the live root
        again = self.sequence()
        self.assertEqual(again.returncode, 3, again.stderr)
        self.assertNotIn("another sequencer holds", again.stderr)
        self.assertIn("already live", again.stderr)
        # the watchdog watches that tend run to its end, then goes
        self.wait_for(lambda: not self.watchdogs(r1), timeout=30,
                      what="the orphaned watchdog to exit")
        (r1 / "sleep").write_text("0")
        proc = self.sequence()
        self.assertEqual(proc.returncode, 2, proc.stderr)        # r-2 drains
        self.assertTrue((self.state() / "shard-r-1.done").exists())
        # what the first tend run wrote is settled first, then the relaunch's
        self.assertIn("settling what its earlier attempt left", proc.stderr)
        self.assertEqual(len((self.state() / "usage-r-1.jsonl").read_text().splitlines()), 2)

    @NEEDS_PROC
    def test_a_watchdog_left_on_the_root_is_stopped_before_the_relaunch(self):
        """A killed sequencer's watchdog, sleeping out its interval, watched the relaunch too.

        This one's parent (the test) stays alive and its interval is 1 s, so it
        would never leave on its own, and with its lower cap it would drain the
        relaunch of r-1 at $1 of its $0.5: the sequencer stops it first.
        """
        self.render()
        r1 = self.remote / "r-1-tend"
        stray = subprocess.Popen([sys.executable, str(WATCHDOG), "--root", str(r1),
                                  "--max-cost", "0.5", "--interval", "1", "--parent",
                                  str(os.getpid())],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=self.sequencer_env())
        self.addCleanup(lambda: (stray.kill(), stray.wait()))
        self.wait_for((r1 / "watchdog.json").exists, what="the stray watchdog's first check")
        proc = self.sequence()
        self.assertEqual(proc.returncode, 2, proc.stderr)        # r-2 drains at $50 of $10
        self.assertEqual(stray.wait(timeout=10), -signal.SIGTERM)
        self.assertIn(f"stopped the watchdog an earlier sequencer left on {r1} (pid {stray.pid})",
                      proc.stderr)
        self.assertNotIn(f"control drain --root {r1}", self.log.read_text())
        self.assertTrue((self.state() / "shard-r-1.done").exists())
        self.assertEqual(self.watchdogs(r1), [])

    def test_cf_accept_names_only_a_shard_that_has_started(self):
        """A CF_ACCEPT for a shard that never ran (a typo, a stale variable) skipped it unrun."""
        self.render()
        (self.remote / "r-2-tend" / "spend").write_text("1")
        proc = self.sequence(CF_ACCEPT="r-2, r-9")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.runs(), [str(self.remote / f"r-{k}-tend") for k in (1, 2, 3)])
        self.assertIn("shard 2 (r-2): CF_ACCEPT names it, but it has never started", proc.stderr)
        self.assertIn("CF_ACCEPT names r-9, which is no shard of campaign c1; ignored", proc.stderr)
        self.assertNotIn("accepted", (self.state() / "shard-r-2.done").read_text())
        self.assertNotIn("accepted as it stands", proc.stderr)

    def test_an_interrupted_shard_can_be_accepted_as_it_stands(self):
        self.render()
        r1 = self.remote / "r-1-tend"
        (r1 / "sleep").write_text("30")
        first = self.start()
        self.wait_for((self.transcripts / "r-1-tend.jsonl").exists, what="r-1's transcript")
        os.killpg(first.pid, signal.SIGKILL)
        first.wait()
        proc = self.sequence(CF_ACCEPT="r-1")
        self.assertEqual(proc.returncode, 2, proc.stderr)        # r-2 drains
        self.assertEqual(self.runs(), [str(r1), str(self.remote / "r-2-tend")])
        done = (self.state() / "shard-r-1.done").read_text()
        self.assertIn("accepted as it stands", done)
        self.assertIn("never (it was interrupted)", done)
        # what the interrupted attempt left is still settled: exported and pruned
        self.assertEqual(len((self.state() / "usage-r-1.jsonl").read_text().splitlines()), 1)
        self.assertFalse((self.transcripts / "r-1-tend.jsonl").exists())

    def test_an_accept_is_refused_while_what_the_shard_left_cannot_be_scanned(self):
        """CF_ACCEPT marked a shard done though the scan of what it left could not run.

        Its transcripts were then neither scanned nor exported, and every later
        shard's baseline took them in, so no harvest would have priced them.
        """
        self.render(canaries=str(self.canaries()))
        r1 = self.remote / "r-1-tend"
        (r1 / "sleep").write_text("30")
        first = self.start()
        leaked = self.transcripts / "r-1-tend.jsonl"
        self.wait_for(leaked.exists, what="r-1's transcript")
        os.killpg(first.pid, signal.SIGKILL)
        first.wait()
        host = self.ws_dir / "campaigns" / "c1" / "host"
        scan = host / "canary_scan.py"
        real = scan.read_text()
        scan.write_text("import sys\nsys.exit(3)\n")
        proc = self.sequence(CF_ACCEPT="r-1")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("shard 1 (r-1): CF_ACCEPT names it, but the canary scan of what it left "
                      "could not run, so it is not accepted", proc.stderr)
        self.assertFalse((self.state() / "shard-r-1.done").exists())
        self.assertTrue((self.state() / "STOPPED").exists())
        self.assertTrue(leaked.exists())
        self.assertFalse((self.state() / "usage-r-1.jsonl").exists())
        self.assertEqual(self.runs(), [str(r1)])
        # once the scan works, the same accept settles it and the campaign moves on
        scan.write_text(real)
        (self.state() / "STOPPED").unlink()
        proc = self.sequence(CF_ACCEPT="r-1")
        self.assertEqual(proc.returncode, 2, proc.stderr)        # r-2 drains
        self.assertIn("accepted as it stands", (self.state() / "shard-r-1.done").read_text())
        self.assertEqual(json.loads((self.state() / "canary-r-1.json").read_text())["hits"], [])
        self.assertEqual(len((self.state() / "usage-r-1.jsonl").read_text().splitlines()), 1)
        self.assertFalse(leaked.exists())
        self.assertEqual(self.runs(), [str(r1), str(self.remote / "r-2-tend")])

    def test_a_baseline_an_older_sequencer_wrote_in_the_locales_order_still_holds(self):
        """The trunk's sequencer sorted the baseline in the host's locale; comm now runs in C.

        An interrupted shard resumed under the new template compared that file as
        it stood, and judged a file that was already there new: exported and deleted.
        """
        self.render()
        r1 = self.remote / "r-1-tend"
        # two transcripts of an earlier run in r-1's root, there before the shard started
        old = [self.transcript("alpha.jsonl", r1), self.transcript("Zeta.jsonl", r1)]
        self.assertLess(b"Zeta", b"alpha")                 # C order, which en_US reverses
        self.state().mkdir(parents=True, exist_ok=True)
        (self.state() / "shard-r-1.before").write_bytes(
            b"".join(os.fsencode(p) + b"\0" for p in old))
        (self.state() / "shard-r-1.started").write_text("")
        proc = self.sequence()
        self.assertEqual(proc.returncode, 2, proc.stderr)        # r-2 drains
        self.assertIn("settling what its earlier attempt left", proc.stderr)
        for path in old:
            self.assertTrue(path.exists(), f"{path.name}, there before the shard, was pruned")
        # the relaunch's own transcript, and nothing else, is r-1's
        self.assertEqual(len((self.state() / "usage-r-1.jsonl").read_text().splitlines()), 1)
        self.assertFalse((self.transcripts / "r-1-tend.jsonl").exists())

    def test_a_shard_at_a_reason_to_drain_is_not_relaunched_and_can_be_accepted(self):
        """OPS-07: each resume relaunched tend over the cap, drained and stopped again."""
        self.render()
        r2 = str(self.remote / "r-2-tend")
        self.assertEqual(self.sequence().returncode, 2)          # r-2 drains at $50 of $10
        (self.state() / "STOPPED").unlink()
        proc = self.sequence()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self.runs(), [str(self.remote / "r-1-tend"), r2])
        self.assertIn("CF_ACCEPT=r-2", proc.stderr)
        self.assertIn("reached the shard's cap", proc.stderr)
        self.assertTrue((self.state() / "STOPPED").exists())
        (self.state() / "STOPPED").unlink()
        proc = self.sequence(CF_ACCEPT="r-2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.runs(), [str(self.remote / "r-1-tend"), r2,
                                       str(self.remote / "r-3-tend")])
        self.assertIn("reached the shard's cap", (self.state() / "shard-r-2.done").read_text())
        self.assertIn("every shard done", proc.stderr)

    def test_a_watchdog_that_dies_during_a_shard_stops_the_campaign(self):
        """OPS-11: the shard ran on with no spend control and was marked done."""
        self.render()
        host = self.ws_dir / "campaigns" / "c1" / "host"
        (host / "watchdog.py").rename(host / "watchdog_real.py")
        (host / "watchdog.py").write_text(
            "import os, sys\nif '--once' not in sys.argv and '--stop-others' not in sys.argv:\n"
            "    sys.exit(1)\n"
            "real = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watchdog_real.py')\n"
            "os.execv(sys.executable, [sys.executable, real] + sys.argv[1:])\n")
        proc = self.sequence()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("the watchdog exited 1", proc.stderr)
        self.assertEqual(self.runs(), [str(self.remote / "r-1-tend")])
        self.assertFalse((self.state() / "shard-r-1.done").exists())

    def test_a_live_tend_run_or_a_second_sequencer_is_refused(self):
        self.render()
        root = self.remote / "r-1-tend"
        smoke = subprocess.Popen(["bash", "-c", f'exec -a "tend run --root {root}" sleep 30'])
        self.addCleanup(smoke.kill)
        time.sleep(0.3)
        proc = self.sequence()
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("already live", proc.stderr)
        self.assertEqual(self.runs() if self.log.exists() else [], [])
        smoke.kill()
        smoke.wait()
        import fcntl

        state = self.remote / "campaigns" / "c1"
        with open(state / "sequencer.lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            proc = self.sequence()
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("another sequencer holds", proc.stderr)

    def test_every_rendered_path_is_quoted(self):
        odd = self.tmp / "re mote"
        odd.mkdir()
        self.ws.data["hosts"]["examplebox"]["remote_root"] = str(odd)
        self.ws.save()
        from cflib.workspace import Workspace

        self.ws = Workspace.load(self.ws_dir, validate=False)
        for run_id in ("r-1", "r-2", "r-3"):
            path = self.ws_dir / "runs" / run_id / "run.yaml"
            path.write_text(path.read_text().replace(str(self.remote), str(odd)))
        out = self.render(transcript_dirs="/t/a b:/t/c")
        text = (self.ws_dir / "campaigns" / "c1" / "host" / "sequencer.sh").read_text()
        self.assertEqual(subprocess.run(["bash", "-n", "-c", text]).returncode, 0)
        self.assertIn(f"STATE='{odd}/campaigns/c1'", text)
        self.assertIn("TRANSCRIPTS_DEFAULT='/t/a b:/t/c'", text)
        remote_cmd = shlex.split(out["start"])[2]
        self.assertEqual(shlex.split(remote_cmd)[:2], ["nohup", f"{odd}/campaigns/c1/sequencer.sh"])
        mkdir = shlex.split(out["transfer"])[2]
        self.assertEqual(shlex.split(mkdir), ["mkdir", "-p", f"{odd}/campaigns/c1"])
        self.assertEqual(shlex.split(out["transfer"])[-1], f"examplebox:{odd}/campaigns/c1/")


class TestCfRunForLocate(WorkspaceCase):

    def setUp(self):
        super().setUp()
        self.shard("r-1", cap=None)
        rd = self.ws_dir / "runs" / "r-1"
        local_sources = self.ws_dir / "sourcesets" / "wpm" / "sources"
        (local_sources / "enwiki-1001-2002").mkdir(parents=True)
        (local_sources / "enwiki-1001-2002" / "layer.txt").write_text("layer")
        db = self.ws_dir / "corpora" / "u1" / "corpus.sqlite"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"sqlite")
        cands = self.ws_dir / "sourcesets" / "wpm" / "candidates" / "u1"
        cands.mkdir(parents=True)
        (cands / "enwiki-1001-2002.json").write_text("{}")
        gate = {"schema": "cf-gate-locate/1", "mode": "locate",
                "sourceset": {"id": "wpm", "sources_dir": f"{self.remote}/locate/wpm/sources"},
                "sources": {"enwiki-1001-2002": {"html_sha256": "a" * 64}},
                "corpus_db": {"path": f"{self.remote}/locate/u1/corpus.sqlite", "sha256": "b" * 64},
                "candidates_dir": f"{self.remote}/locate/candidates/u1",
                "inventory": {"run_id": "r-0", "dir": f"{self.remote}/locate/inventory"}}
        local = dict(gate, sourceset={"id": "wpm", "sources_dir": str(local_sources)},
                     corpus_db={"path": str(db), "sha256": "b" * 64}, candidates_dir=str(cands),
                     inventory={"run_id": "r-0", "dir": str(self.ws_dir / "corpus" / "r-0")})
        (rd / "gate.json").write_text(json.dumps(gate))
        (rd / "gate.local.json").write_text(json.dumps(local))
        from cflib.yamlio import read_yaml

        self.run_doc = read_yaml(rd / "run.yaml")

    def test_a_recorded_cap_is_a_precondition(self):
        with self.assertRaises(CfError) as caught:
            manifest.check_budget_cap(self.ws, self.run_doc, None, False, locate=True)
        self.assertIn("spend cap", str(caught.exception))
        with self.assertRaises(CfError):
            manifest.check_budget_cap(self.ws, self.run_doc, None, True, locate=True)
        # a workspace-wide ceiling is not a recorded one
        self.ws.data["budget"] = {"max_cost_usd": 100}
        with self.assertRaises(CfError):
            manifest.check_budget_cap(self.ws, self.run_doc, None, False, locate=True)
        budget = manifest.check_budget_cap(self.ws, self.run_doc, 25.0, False, locate=True)
        self.assertEqual(budget["max_cost"], 25.0)
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out), self.assertRaises(CfError) as caught:
            cf_main.main(["--workspace", str(self.ws_dir), "run", "r-1", "--host", "examplebox",
                          "--i-have-consent", "--no-budget-cap"])
        self.assertIn("does not apply to a locate run", str(caught.exception))

    def test_only_this_runs_sources_travel_and_only_with_consent_to_text(self):
        host = manifest.resolve_host(self.ws, "examplebox")
        entries, warnings = manifest.build_manifest(
            workspace_dir=self.ws_dir, skill_dir=SKILL, run_id="r-1", run=self.run_doc, host=host)
        names = {e["name"] for e in entries}
        self.assertIn("corpus.sqlite", names)
        self.assertIn("candidates", names)
        self.assertFalse({n for n in names if n.startswith("source-layer:")})
        self.assertTrue(any("fails closed" in w for w in warnings))
        self.assertFalse(any("data/ is absent" in w for w in warnings))
        entries, _ = manifest.build_manifest(
            workspace_dir=self.ws_dir, skill_dir=SKILL, run_id="r-1", run=self.run_doc, host=host,
            include_texts=True)
        layer = next(e for e in entries if e["name"] == "source-layer:enwiki-1001-2002")
        self.assertEqual(layer["dest"], f"{self.remote}/locate/wpm/sources/enwiki-1001-2002/")
        # nothing on the host reads the frozen inventory: its passages travel in
        # the task manifests (#54, #80)
        self.assertNotIn("inventory", {e["name"] for e in entries})
        self.assertFalse(any("names no sources" in w for w in warnings))

    def test_only_this_runs_candidates_travel(self):
        """Review #67: the candidates directory is the sourceset's, shared by every run."""
        cands = self.ws_dir / "sourcesets" / "wpm" / "candidates" / "u1"
        for sid in ("enwiki-1001-2002", "enwiki-9-9"):
            (cands / f"{sid}.json").write_text("{}")
        host = manifest.resolve_host(self.ws, "examplebox")
        entries, _ = manifest.build_manifest(
            workspace_dir=self.ws_dir, skill_dir=SKILL, run_id="r-1", run=self.run_doc, host=host)
        entry = next(e for e in entries if e["name"] == "candidates")
        self.assertEqual(entry["srcs"], [str(cands / "enwiki-1001-2002.json")])
        self.assertNotIn("--delete", entry["rsync_flags"])
        steps = remote.plan_commands(host=host, run_id="r-1", run=self.run_doc, entries=[entry],
                                     names=manifest.remote_names(self.run_doc, "r-1"),
                                     budget={"max_cost": 10, "currency": "USD"},
                                     concurrency={"workers": 3, "reviewers": 2})
        titles = [s["title"] for s in steps if s["stage"] == "transfer"]
        rsync = titles.index(f"rsync candidates -> {entry['dest']}")
        self.assertTrue(titles[rsync - 1].startswith("open ") and titles[rsync + 1].startswith("close "))
        # the blind arm's files are sent too: its prompts name them (review C-04)
        rd = self.ws_dir / "runs" / "r-1"
        for name in ("gate.json", "gate.local.json"):
            doc = json.loads((rd / name).read_text())
            doc["blind"] = True
            (rd / name).write_text(json.dumps(doc))
        entries, _ = manifest.build_manifest(
            workspace_dir=self.ws_dir, skill_dir=SKILL, run_id="r-1", run=self.run_doc, host=host)
        self.assertIn("candidates", {e["name"] for e in entries})

    def test_the_verify_stage_checks_the_corpus_once_and_the_locate_gate(self):
        host = manifest.resolve_host(self.ws, "examplebox")
        gate, _local = manifest.locate_gate(self.ws_dir / "runs" / "r-1")
        steps = remote.plan_commands(host=host, run_id="r-1", run=self.run_doc, entries=[],
                                     names=manifest.remote_names(self.run_doc, "r-1"),
                                     budget={"max_cost": 10, "currency": "USD"},
                                     concurrency={"workers": 3, "reviewers": 2}, locate=gate)
        verify = [s for s in steps if s["stage"] == "verify"]
        corpus = next(s for s in verify if "corpus.sqlite" in s["title"])
        self.assertEqual(corpus["expected"], "b" * 64)
        self.assertIn("sha256sum -c", corpus["shell"])
        self.assertTrue(any("verify_locate.py" in s["shell"] for s in verify))

    def test_the_tools_database_travels_and_is_verified_when_it_is_another_file(self):
        rd = self.ws_dir / "runs" / "r-1"
        blind = self.ws_dir / "corpora" / "u1" / "corpus.blind.sqlite"
        blind.write_bytes(b"blind")
        gate = json.loads((rd / "gate.json").read_text())
        local = json.loads((rd / "gate.local.json").read_text())
        gate["tools_corpus_db"] = {"path": f"{self.remote}/locate/u1/corpus.blind.sqlite",
                                   "sha256": "c" * 64, "kind": "blind"}
        local["tools_corpus_db"] = {"path": str(blind), "sha256": "c" * 64, "kind": "blind"}
        (rd / "gate.json").write_text(json.dumps(gate))
        (rd / "gate.local.json").write_text(json.dumps(local))
        host = manifest.resolve_host(self.ws, "examplebox")
        entries, _ = manifest.build_manifest(
            workspace_dir=self.ws_dir, skill_dir=SKILL, run_id="r-1", run=self.run_doc, host=host)
        tools = next(e for e in entries if e["name"] == "tools-corpus.sqlite")
        self.assertEqual((tools["src"], tools["dest"], tools["sha256"]),
                         (str(blind), f"{self.remote}/locate/u1/corpus.blind.sqlite", "c" * 64))
        steps = remote.plan_commands(host=host, run_id="r-1", run=self.run_doc, entries=[],
                                     names=manifest.remote_names(self.run_doc, "r-1"),
                                     budget={"max_cost": 10, "currency": "USD"},
                                     concurrency={"workers": 3, "reviewers": 2}, locate=gate)
        checked = [s for s in steps if s["stage"] == "verify" and s.get("expected")]
        self.assertEqual(sorted(s["expected"] for s in checked), ["b" * 64, "c" * 64])
        self.assertIn("blind database", next(s for s in checked if s["expected"] == "c" * 64)
                      ["title"])
        # kind full: the same file as corpus_db, so nothing more travels or is checked
        for doc, path in ((gate, gate["corpus_db"]["path"]), (local, local["corpus_db"]["path"])):
            doc["tools_corpus_db"] = {"path": path, "sha256": "b" * 64, "kind": "full"}
        (rd / "gate.json").write_text(json.dumps(gate))
        (rd / "gate.local.json").write_text(json.dumps(local))
        entries, _ = manifest.build_manifest(
            workspace_dir=self.ws_dir, skill_dir=SKILL, run_id="r-1", run=self.run_doc, host=host)
        self.assertNotIn("tools-corpus.sqlite", {e["name"] for e in entries})
        steps = remote.plan_commands(host=host, run_id="r-1", run=self.run_doc, entries=[],
                                     names=manifest.remote_names(self.run_doc, "r-1"),
                                     budget={"max_cost": 10, "currency": "USD"},
                                     concurrency={"workers": 3, "reviewers": 2}, locate=gate)
        self.assertEqual(len([s for s in steps if s["stage"] == "verify" and s.get("expected")]), 1)

    def test_a_given_cap_must_agree_with_the_recorded_one(self):
        doc = dict(self.run_doc, budget={"currency": "USD", "max_cost": 10})
        with self.assertRaises(CfError) as caught:
            manifest.check_budget_cap(self.ws, doc, 25.0, False, locate=True)
        self.assertIn("--max-cost 25 differs", str(caught.exception))
        self.assertIn("budget.max_cost 10", str(caught.exception))
        self.assertEqual(manifest.check_budget_cap(self.ws, doc, 10.0, False,
                                                   locate=True)["max_cost"], 10.0)
        self.assertEqual(manifest.check_budget_cap(self.ws, doc, None, False,
                                                   locate=True)["max_cost"], 10.0)

    def cf_run(self, *extra) -> str:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cf_main.main(["--workspace", str(self.ws_dir), "run", "r-1", "--host",
                                 "examplebox", "--i-have-consent", *extra])
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue()

    def test_the_cap_given_to_cf_run_is_the_one_the_campaign_reads(self):
        from cflib.yamlio import read_yaml

        self.cf_run("--max-cost", "25", "--include-source-text")
        doc = read_yaml(self.ws_dir / "runs" / "r-1" / "run.yaml")
        self.assertEqual(doc["budget"]["max_cost"], 25.0)
        self.assertEqual(campaign_mod.shard_record(self.ws, "r-1", 1, "examplebox")["max_cost"],
                         25.0)
        with self.assertRaises(CfError):
            self.cf_run("--max-cost", "50")

    def test_the_consent_record_names_the_layers_that_left(self):
        from cflib.yamlio import read_yaml

        out = self.cf_run("--max-cost", "10", "--include-source-text")
        self.assertNotIn("names no sources", out)
        block = read_yaml(self.ws_dir / "runs" / "r-1" / "run.yaml")["run"]
        self.assertEqual(block["sources_transferred"], ["enwiki-1001-2002"])
        self.cf_run("--max-cost", "10")
        block = read_yaml(self.ws_dir / "runs" / "r-1" / "run.yaml")["run"]
        self.assertEqual(block["sources_transferred"], [])

    def test_a_locate_run_planned_for_this_machine_is_refused_first(self):
        from cflib.yamlio import read_yaml, write_yaml

        path = self.ws_dir / "runs" / "r-1" / "run.yaml"
        doc = read_yaml(path)
        doc["host"] = {"name": "local", "kind": "local", "remote_root": ""}
        write_yaml(path, doc)
        for extra in ([], ["--local"], ["--host", "examplebox", "--i-have-consent"]):
            with self.subTest(extra=extra), redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()), self.assertRaises(CfError) as caught:
                cf_main.main(["--workspace", str(self.ws_dir), "run", "r-1", *extra])
            self.assertIn("allowlisted host through tend", str(caught.exception))
            self.assertIn("cf plan-locate", caught.exception.hint)
        self.assertFalse((self.ws_dir / "runs" / "r-1" / "self-gate").exists())

    def test_the_preflight_records_the_versions_the_bench_pins(self):
        from cflib.yamlio import read_yaml, write_yaml

        rd = self.ws_dir / "runs" / "r-1"
        (rd / "tend-root" / "prompts").mkdir(parents=True)
        (rd / "tend-root" / "gate").mkdir()
        path = rd / "run.yaml"
        doc = read_yaml(path)
        doc["bridge"] = "agentbox-claude"
        write_yaml(path, doc)
        fake = self.tmp / "fakebin"
        fake.mkdir()
        scripts = {"ssh": '#!/bin/sh\nshift\nexec /bin/bash -c "$*"\n',
                   "agentbox": "#!/bin/sh\necho 'agentbox 9.9.9'\n",
                   "claude": "#!/bin/sh\necho '1.2.3 (Claude Code)'\n"}
        for name, text in scripts.items():
            (fake / name).write_text(text)
            (fake / name).chmod(0o755)
        env = {"PATH": f"{fake}{os.pathsep}{os.environ.get('PATH', '')}"}
        with mock.patch.dict(os.environ, env):
            self.cf_run("--max-cost", "10", "--stage", "preflight", "--execute")
        doc = read_yaml(path)
        self.assertEqual(doc["versions"]["agentbox"], "agentbox 9.9.9")
        self.assertEqual(doc["versions"]["agent_cli"], "1.2.3 (Claude Code)")
        self.assertIn("tend", doc["versions"])
        self.assertEqual(remote.recorded_versions([{"title": remote.VERSIONS_TITLE, "ran": False,
                                                    "stdout": "tend-version: 1"}]), {})
        # a bridge with no CLI of its own says so instead of probing one
        steps = remote.plan_commands(host=manifest.resolve_host(self.ws, "examplebox"),
                                     run_id="r-1", run={"bridge": "tend-agent"}, entries=[],
                                     names=manifest.remote_names(self.run_doc, "r-1"),
                                     budget={"max_cost": 10, "currency": "USD"},
                                     concurrency={"workers": 3, "reviewers": 2})
        probe = next(s for s in steps if s["title"] == remote.VERSIONS_TITLE)
        self.assertIn("launches no agent CLI", probe["shell"])

    def test_the_tend_config_must_call_the_locate_gate(self):
        host = manifest.resolve_host(self.ws, "examplebox")
        tend = self.ws_dir / "runs" / "r-1" / "tend-root"
        tend.mkdir()
        (tend / "config.yaml").write_text("validation_commands:\n  - argv: [python3, "
                                          f"{self.remote}/r-1-gate/verify_findings.py, --changed-files]\n")
        warnings = manifest.gate_config_warnings(self.ws_dir, "r-1", host, self.run_doc)
        self.assertTrue(any("verify_locate.py" in w for w in warnings))


class TestTheRunbook(unittest.TestCase):
    """references/runbook-server.md section 9, read as the operator follows it."""

    def test_the_pilot_and_shard_commands_send_the_layers_to_a_named_host(self):
        """OPS-08: without --include-source-text no layer travels, and every gate call exits 3."""
        import re

        text = (SKILL / "references" / "runbook-server.md").read_text(encoding="utf-8")
        section = text[text.index("## 9. A locate root"):text.index("## 10.")]
        # the commands an operator runs name the run (`cf run <pilot> ...`)
        commands = [" ".join(c.split()) for c in re.findall(r"`(cf run <[^`]*--execute[^`]*)`",
                                                            section)]
        self.assertGreaterEqual(len(commands), 2, commands)
        for command in commands:
            with self.subTest(command=command):
                self.assertIn("--host ", command)
                self.assertIn("--include-source-text", command)


class TestTheCanaryScan(unittest.TestCase):
    """scripts/run/canary_scan.py: the host-side report `cf bench score --canary-report` reads."""

    def test_hits_plain_and_gzipped_and_the_exit_codes(self):
        import gzip
        import hashlib

        tmp = scratch(self)
        canaries = tmp / "canaries.txt"
        canaries.write_text("cf-bench-canary-abc\n\n")
        clean = tmp / "a.jsonl"
        clean.write_text('{"x": "nothing"}\n')
        dirty = tmp / "b.jsonl.gz"
        with gzip.open(dirty, "wt") as fh:
            fh.write('{"x": "a cf-bench-canary-abc leak"}\n')
        out = tmp / "canary.json"
        scan = [sys.executable, str(SCRIPTS / "run" / "canary_scan.py"), "--canaries",
                str(canaries), "--out", str(out), "--"]
        proc = subprocess.run(scan + [str(clean)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(out.read_text()), {
            "scanned": 1, "hits": [], "canaries": 1,
            "canary_sha256": [hashlib.sha256(b"cf-bench-canary-abc").hexdigest()]})
        proc = subprocess.run(scan + [str(clean), str(dirty)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json.loads(out.read_text())["hits"], ["b.jsonl.gz"])
        proc = subprocess.run(scan + [str(tmp / "missing.jsonl")], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
