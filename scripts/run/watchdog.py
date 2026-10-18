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
  the bridges keep in ``<root>/usage/`` (scripts/run/agent_call.py), plus an
  ESTIMATE for each call that ended with no priced envelope (a timeout, a
  signal, an empty stdout: ``agent_call`` keeps a record of it with
  ``total_cost_usd: null`` and its elapsed time): the call's seconds times the
  highest dollars-per-second any priced envelope of the root shows, or
  ``--unpriced-usd-per-hour`` before one exists (:func:`ledger`).  A 45-minute
  call that timed out used to add nothing at all (field defect T1).  It is
  still not the bill: the transcripts priced by ``scripts/cost`` at harvest
  stay the primary figure;
* **the gate-failure rate** -- the fraction of tend's calls in
  ``<root>/gate-log/gate.jsonl`` (the lines with ``changed_files: true``) that
  did not pass; an agent's own ``--findings`` check is logged too, and counts
  for nothing here;
* **merged tasks** -- the task directories under ``findings/`` on the
  entrypoint's merge target (``git ls-tree``, with the boundary pinned).

Every record carries ``spend_usd`` (the priced floor plus the estimate, what
the rules judge), ``spend_floor_usd``, ``unpriced_calls``, ``unpriced_seconds``,
``estimated_unpriced_usd`` (null when there was nothing to estimate with) and
``estimate_usd_per_second``.

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


def _number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def ledger(usage_dir: Path, *, fallback_usd_per_hour: float | None = None) -> dict:
    """What ``usage/`` says was spent: the priced floor and the estimate of the rest.

    ``{dollars, envelopes, unpriced_calls, unpriced_seconds, usd_per_second,
    estimated_unpriced_usd}``.  An envelope with a ``total_cost_usd`` is priced;
    one whose ``total_cost_usd`` is null and that records an elapsed time
    (``agent_call``'s record of a call that left no envelope) is an unpriced
    call, estimated at the highest dollars-per-second of the priced envelopes
    that carry a ``duration_ms`` -- or ``fallback_usd_per_hour`` before any
    does; ``estimated_unpriced_usd`` is None when neither exists.
    """
    out = {"dollars": 0.0, "envelopes": 0, "unpriced_calls": 0, "unpriced_seconds": 0.0,
           "usd_per_second": None, "estimated_unpriced_usd": 0.0}
    if not usage_dir.is_dir():
        return out
    rate = None
    for path in sorted(usage_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        out["envelopes"] += 1
        cost = _number(data.get("total_cost_usd"))
        millis = _number(data.get("duration_ms"))
        if cost is not None:
            if cost > 0:
                out["dollars"] += cost
                if millis and millis > 0:
                    here = cost / (millis / 1000.0)
                    rate = here if rate is None else max(rate, here)
            continue
        call = data.get("cf_agent_call") if isinstance(data.get("cf_agent_call"), dict) else {}
        seconds = _number(call.get("elapsed_s"))
        if seconds is None and millis is not None:
            seconds = millis / 1000.0
        if seconds is not None:
            out["unpriced_calls"] += 1
            out["unpriced_seconds"] += max(0.0, seconds)
    if rate is None and fallback_usd_per_hour is not None:
        rate = float(fallback_usd_per_hour) / 3600.0
    out["usd_per_second"] = round(rate, 9) if rate is not None else None
    if out["unpriced_calls"]:
        out["estimated_unpriced_usd"] = (round(out["unpriced_seconds"] * rate, 6)
                                         if rate is not None else None)
    out["dollars"] = round(out["dollars"], 6)
    out["unpriced_seconds"] = round(out["unpriced_seconds"], 1)
    return out


def spend(usage_dir: Path) -> tuple[float, int]:
    """``(dollars, envelopes)``: the priced floor over every kept JSON envelope."""
    got = ledger(usage_dir)
    return got["dollars"], got["envelopes"]


def gate_calls(log_path: Path) -> tuple[int, int]:
    """``(calls, failed)`` over tend's calls in the gate log.

    The gate logs every call and says whether it judged a change set
    (``changed_files``): tend's validation and pre-merge commands do, a worker's
    or a reviewer's ``--findings`` self-check does not.  Only the former count,
    so an agent that re-runs the gate on its document neither drains the root
    nor dilutes its failure rate.
    """
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
        if isinstance(record, dict) and record.get("changed_files") is True:
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
    spent = ledger(root / "usage", fallback_usd_per_hour=args.unpriced_usd_per_hour)
    dollars, envelopes = spent["dollars"], spent["envelopes"]
    estimate = spent["estimated_unpriced_usd"]
    calls, failed = gate_calls(root / "gate-log" / "gate.jsonl")
    merged = merged_tasks(Path(args.entrypoint) if args.entrypoint else None, args.branch)
    # the rules judge the floor plus the estimate of what the unpriced calls cost
    total = round(dollars + (estimate or 0.0), 6)
    reasons = decide(dollars=total, max_cost=args.max_cost, calls=calls, failed=failed,
                     merged=merged, p90_cost=args.p90_cost_per_task,
                     p90_gate=args.p90_gate_failure_rate, multiple=args.multiple,
                     min_tasks=args.min_tasks, min_calls=args.min_gate_calls)
    previous = {}
    try:
        previous = json.loads((root / "watchdog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "root": str(root),
              "spend_usd": total, "spend_floor_usd": dollars,
              "unpriced_calls": spent["unpriced_calls"],
              "unpriced_seconds": spent["unpriced_seconds"],
              "estimated_unpriced_usd": estimate,
              "estimate_usd_per_second": spent["usd_per_second"],
              "envelopes": envelopes, "max_cost": args.max_cost,
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
    ap.add_argument("--unpriced-usd-per-hour", type=float, default=None,
                    help="what an hour of a call that left no priced envelope is estimated at "
                         "before any priced envelope with a duration exists (default: none, "
                         "and such calls are recorded as unestimated)")
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
