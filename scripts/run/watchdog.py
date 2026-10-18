#!/usr/bin/env python3
"""The per-shard spend watchdog: drain a tend root before it overspends.

New code, implementing docs/proposals/wikipedia-locate.md section 6 (Cost and
spend): "a host-side, model-free watchdog per shard drains the root when its
spend passes the cap recorded in run.yaml, or when the gate-failure rate or
cost per merged task exceeds a multiple of the pilot's p90".  The campaign
sequencer (``templates/campaign/sequencer.sh.tmpl``) runs it beside each
shard's ``tend run``; ``cf run`` ships it in the root's ``bin/``.

    watchdog.py --root <tend-root> --entrypoint <entrypoint repo> --max-cost 120 \\
        [--p90-cost-per-task 4.1] [--p90-gate-failure-rate 0.12] [--multiple 2] \\
        [--tend "uv run"] [--interval 300 | --once] [--dry-run]

What it reads, all of it outside every worktree:

* **spend** -- the sum of ``total_cost_usd`` over the agents' JSON envelopes
  the bridges keep in ``<root>/usage/`` (scripts/run/agent_call.py).  It is a
  floor, not the bill: a call whose envelope carries no cost adds nothing, and
  the transcripts priced by ``scripts/cost`` at harvest stay the primary
  figure;
* **the gate-failure rate** -- the fraction of calls in
  ``<root>/gate-log/gate.jsonl`` that did not pass;
* **merged tasks** -- the task directories under ``findings/`` on the
  entrypoint's merge target (``git ls-tree``, with the boundary pinned).

Model-free and standard library only: it decides from numbers, runs on the
host where cflib is not installed, and its one action is ``tend-control
drain --root`` (finish what is in flight, start nothing).  Every check is
appended to ``<root>/watchdog.jsonl`` and the latest is ``<root>/watchdog.json``,
which the sequencer reads after the shard: a drain, a reason to drain that no
drain acted on, or a record it cannot read stops the campaign.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

#: COPIED from ``cflib.gitsafe`` (no cflib on the host);
#: tests/test_campaign.py asserts they equal gitsafe's.
_GIT_SAFE_CONFIG: tuple[str, ...] = (
    "-c", "core.fsmonitor=",
    "-c", "core.hooksPath=/dev/null",
    "-c", "diff.external=",
    "-c", "core.pager=cat",
    "-c", "core.sshCommand=",
    "-c", "core.askPass=",
)
_GIT_SAFE_ENV: dict[str, str] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}
#: below this many merged tasks the cost-per-task rule does not fire (noise)
MIN_TASKS = 5
#: below this many gate calls the failure-rate rule does not fire
MIN_GATE_CALLS = 20


def spend(usage_dir: Path) -> tuple[float, int]:
    """``(dollars, envelopes)`` over every kept JSON envelope."""
    total, count = 0.0, 0
    if not usage_dir.is_dir():
        return 0.0, 0
    for path in sorted(usage_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            count += 1
            value = data.get("total_cost_usd")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                total += float(value)
    return round(total, 6), count


def gate_calls(log_path: Path) -> tuple[int, int]:
    """``(calls, failed)`` from the gate log."""
    calls = failed = 0
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, 0
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            calls += 1
            failed += 0 if record.get("ok") else 1
    return calls, failed


def merged_tasks(entrypoint: Path | None, branch: str) -> int | None:
    """Task directories under ``findings/`` on the merge target; None when unreadable."""
    if entrypoint is None:
        return None
    env = {**os.environ, **_GIT_SAFE_ENV}
    try:
        proc = subprocess.run(["git", *_GIT_SAFE_CONFIG, "-C", str(entrypoint), "ls-tree",
                               "--name-only", f"{branch}:findings"],
                              capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return 0 if "Not a valid object name" in proc.stderr or "not a tree" in proc.stderr else None
    return sum(1 for line in proc.stdout.splitlines() if line.strip())


def decide(*, dollars: float, max_cost: float, calls: int, failed: int, merged: int | None,
           p90_cost: float | None, p90_gate: float | None, multiple: float,
           min_tasks: int = MIN_TASKS, min_calls: int = MIN_GATE_CALLS) -> list[str]:
    """The reasons to drain now; empty means keep going."""
    reasons = []
    if max_cost > 0 and dollars >= max_cost:
        reasons.append(f"spend ${dollars:.2f} reached the shard's cap ${max_cost:.2f}")
    if p90_gate is not None and calls > 0 and calls >= min_calls:
        rate = failed / calls
        if rate > multiple * p90_gate:
            reasons.append(f"gate-failure rate {rate:.3f} over {calls} calls is above "
                           f"{multiple:g} x the pilot's p90 {p90_gate:.3f}")
    if p90_cost is not None and merged and merged >= min_tasks:
        per_task = dollars / merged
        if per_task > multiple * p90_cost:
            reasons.append(f"cost per merged task ${per_task:.2f} over {merged} tasks is above "
                           f"{multiple:g} x the pilot's p90 ${p90_cost:.2f}")
    return reasons


def check(args) -> dict:
    root = Path(args.root)
    dollars, envelopes = spend(root / "usage")
    calls, failed = gate_calls(root / "gate-log" / "gate.jsonl")
    merged = merged_tasks(Path(args.entrypoint) if args.entrypoint else None, args.branch)
    reasons = decide(dollars=dollars, max_cost=args.max_cost, calls=calls, failed=failed,
                     merged=merged, p90_cost=args.p90_cost_per_task,
                     p90_gate=args.p90_gate_failure_rate, multiple=args.multiple,
                     min_tasks=args.min_tasks, min_calls=args.min_gate_calls)
    previous = {}
    try:
        previous = json.loads((root / "watchdog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "root": str(root),
              "spend_usd": dollars, "envelopes": envelopes, "max_cost": args.max_cost,
              "gate_calls": calls, "gate_failed": failed, "merged_tasks": merged,
              "reasons": reasons, "drained": bool(previous.get("drained")), "action": None}
    if reasons and not record["drained"]:
        # tend's prefix is shell (the host's tend_cmd exports PATH and cd's), so a
        # prefixed drain runs through bash -c; a bare one is an argv
        command = f"tend-control drain --root {shlex.quote(str(root))}"
        argv = (["bash", "-c", f"{args.tend} {command}"] if args.tend.strip()
                else shlex.split(command))
        record["action"] = f"{args.tend} {command}".strip()
        if not args.dry_run:
            try:
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
                record["drain_exit"] = proc.returncode
                record["drain_output"] = (proc.stdout + proc.stderr).strip()[-500:]
                record["drained"] = proc.returncode == 0
            except (OSError, subprocess.SubprocessError) as exc:
                record["drain_exit"] = None
                record["drain_output"] = str(exc)
    try:
        with open(root / "watchdog.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        tmp = root / "watchdog.json.tmp"
        tmp.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, root / "watchdog.json")
    except OSError:
        pass
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="watchdog.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, help="the shard's tend root")
    ap.add_argument("--entrypoint", default="", help="the shard's entrypoint repository")
    ap.add_argument("--branch", default="main", help="the merge target (default main)")
    ap.add_argument("--max-cost", type=float, required=True,
                    help="the shard's spend cap from its run.yaml, in USD")
    ap.add_argument("--p90-cost-per-task", type=float, default=None)
    ap.add_argument("--p90-gate-failure-rate", type=float, default=None)
    ap.add_argument("--multiple", type=float, default=2.0)
    ap.add_argument("--min-tasks", type=int, default=MIN_TASKS)
    ap.add_argument("--min-gate-calls", type=int, default=MIN_GATE_CALLS)
    ap.add_argument("--tend", default=os.environ.get("CF_TEND_CMD", ""),
                    help="the prefix tend's commands run under (e.g. 'uv run')")
    ap.add_argument("--interval", type=float, default=300.0, help="seconds between checks")
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--dry-run", action="store_true", help="never drain; record what it would do")
    args = ap.parse_args(argv)
    if args.max_cost <= 0:
        print("watchdog: --max-cost must be positive (a locate shard needs a recorded cap)",
              file=sys.stderr)
        return 64
    if args.min_gate_calls < 1 or args.min_tasks < 1:
        print("watchdog: --min-gate-calls and --min-tasks must be at least 1", file=sys.stderr)
        return 64
    stop = Path(args.root) / "watchdog.stop"
    while True:
        record = check(args)
        print(json.dumps(record, sort_keys=True), flush=True)
        if args.once or record["drained"] or stop.exists():
            return 2 if record["drained"] else 0
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
