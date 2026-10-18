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
import shutil
import subprocess
import sys
import tempfile
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


class TestTheWatchdog(unittest.TestCase):

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
        (log / "gate.jsonl").write_text("".join(json.dumps({"ok": i % 2 == 0}) + "\n"
                                                for i in range(30)))
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

    def test_a_shell_prefix_runs_through_bash(self):
        envelope(self.root / "usage", "t.json", 11)
        code, record = self.run_wd("--tend", "export X=1; env")
        self.assertTrue(record["drained"], record)
        self.assertIn("export X=1; env tend-control drain", record["action"])

    def test_the_inlined_git_pins_are_gitsafes(self):
        self.assertEqual(watchdog._GIT_SAFE_CONFIG, gitsafe.SAFE_CONFIG)
        self.assertEqual(watchdog._GIT_SAFE_ENV, gitsafe.SAFE_ENV)


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

    def test_the_cli(self):
        self.shard("r-1")
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cf_main.main(["--workspace", str(self.ws_dir), "campaign", "plan", "c1",
                                 "--shard", "r-1", "--host", "examplebox"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("drains on the spend cap alone", out.getvalue())
        with redirect_stdout(io.StringIO()) as status, redirect_stderr(io.StringIO()):
            cf_main.main(["--workspace", str(self.ws_dir), "campaign", "status", "c1", "--json"])
        self.assertEqual(json.loads(status.getvalue())["totals"]["harvested"], 0)


class TestTheSequencer(WorkspaceCase):
    """Render the host side, then run it against fake tend binaries."""

    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "fakebin"
        self.bin.mkdir()
        self.log = self.tmp / "tend.log"
        # `tend run --root R ...`: spend what R/spend says, slowly enough for the
        # watchdog to look, then finish
        (self.bin / "tend").write_text(
            '#!/bin/sh\nroot=""; while [ $# -gt 0 ]; do [ "$1" = --root ] && root="$2"; shift; done\n'
            f'echo "run $root" >> "{self.log}"\n'
            'mkdir -p "$root/usage"\n'
            'printf \'{"subtype":"success","total_cost_usd":%s}\' "$(cat "$root/spend")" '
            '> "$root/usage/t.worker.x.1.json"\nsleep 2\n')
        (self.bin / "tend-control").write_text(f'#!/bin/sh\necho "control $*" >> "{self.log}"\n')
        for f in self.bin.iterdir():
            f.chmod(0o755)
        for run_id, spend in (("r-1", 1), ("r-2", 50), ("r-3", 1)):
            self.shard(run_id, cap=10)
            root = self.remote / f"{run_id}-tend"
            root.mkdir()
            (root / "spend").write_text(str(spend))

    def render(self):
        campaign_mod.plan(self.ws, "c1", ["r-1", "r-2", "r-3"], host="examplebox")
        return campaign_mod.render(self.ws, "c1")

    def test_the_render(self):
        out = self.render()
        host = self.ws_dir / "campaigns" / "c1" / "host"
        sequencer = host / "sequencer.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(sequencer)]).returncode, 0)
        self.assertTrue(os.access(sequencer, os.X_OK))
        text = sequencer.read_text()
        self.assertLess(text.index("run_shard 1 r-1"), text.index("run_shard 2 r-2"))
        self.assertTrue((host / "watchdog.py").is_file() and (host / "usage.py").is_file())
        for needle in ("from cflib", "import cflib", "cf.py"):
            self.assertNotIn(needle, text)
        doc = campaign_mod.load(self.ws, "c1")
        self.assertEqual(doc["sequencer"]["sha256"], out["sequencer"]["sha256"])
        self.assertIn("rsync -a", out["transfer"])

    def test_it_runs_in_order_and_stops_when_a_watchdog_drains(self):
        self.render()
        sequencer = self.ws_dir / "campaigns" / "c1" / "host" / "sequencer.sh"
        transcripts = self.tmp / "transcripts"
        transcripts.mkdir()
        proc = subprocess.run(["bash", str(sequencer)], capture_output=True, text=True,
                              timeout=180,
                              env=clean_env(PATH=f"{self.bin}:{os.environ['PATH']}",
                                            CF_WATCH_INTERVAL="0.3",
                                            CF_TRANSCRIPT_DIRS=str(transcripts)))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        runs = [line.split()[1] for line in self.log.read_text().splitlines()
                if line.startswith("run ")]
        self.assertEqual(runs, [str(self.remote / "r-1-tend"), str(self.remote / "r-2-tend")])
        self.assertIn(f"control drain --root {self.remote / 'r-2-tend'}", self.log.read_text())
        state = self.remote / "campaigns" / "c1"
        self.assertTrue((state / "shard-1.done").exists())
        self.assertFalse((state / "shard-2.done").exists())
        self.assertTrue((state / "STOPPED").exists())
        self.assertIn("DRAINED by the watchdog", (state / "sequencer.log").read_text())
        self.assertTrue((state / "usage-1.jsonl").exists())
        # a stopped campaign refuses to resume until the operator says so
        again = subprocess.run(["bash", str(sequencer)], capture_output=True, text=True,
                               env=clean_env(PATH=f"{self.bin}:{os.environ['PATH']}"))
        self.assertEqual(again.returncode, 2)
        self.assertIn("STOPPED", again.stderr)


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


if __name__ == "__main__":
    unittest.main()
