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
        [--tend "uv run"] [--interval 300 | --once] [--dry-run] [--parent <pid>]
    watchdog.py --root <tend-root> --stop-others

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
* **the gate-failure rate** -- the fraction of tend's validation calls in
  ``<root>/gate-log/gate.jsonl`` (the lines with ``changed_files: true`` and no
  ``pre_merge: true``) that did not pass; an agent's own ``--findings`` check
  is logged too, and counts for nothing here, and tend's pre-merge re-check of
  a batch (``pre_merge: true``) is counted apart, as ``pre_merge_calls`` and
  ``pre_merge_failed``, never as a second attempt;
* **merged tasks** -- the task directories under ``findings/`` on the
  entrypoint's merge target (``git ls-tree``, with the boundary pinned).

An envelope is named ``<task-id>.<role>.<UTC stamp>.<pid>.json``, or, for an
audited run's call, ``<task-id>.<role>.<step>.<UTC stamp>.<pid>.json``
(``agent_call.py --step``; references/locate-documents.md section 13.9);
:func:`envelope_name` reads both, and every one counts towards the spend.

Every record carries ``spend_usd`` (the priced floor plus the estimate, what
the rules judge), ``spend_floor_usd``, ``unpriced_calls``, ``unpriced_seconds``,
``estimated_unpriced_usd`` (null when there was nothing to estimate with),
``estimate_usd_per_second``, ``gate_calls`` and ``gate_failed`` (the validation
calls the rate is over) and ``pre_merge_calls`` and ``pre_merge_failed``; a root
with any stepped envelope also gets ``spend_by_step``.

Model-free and standard library only: it decides from numbers, runs on the
host where cflib is not installed, and its one action is ``tend-control
drain --root`` (finish what is in flight, start nothing).  Every check is
appended to ``<root>/watchdog.jsonl`` and the latest is ``<root>/watchdog.json``,
which the sequencer reads after the shard: a drain, a reason to drain that no
drain acted on, or a record it cannot read stops the campaign.

It stops on ``<root>/watchdog.stop``, after a drain, or -- given ``--parent``,
the sequencer's pid -- once that process is gone and no ``tend run --root
<root>`` is live: a sequencer that is killed leaves its tend run watched to
the end, and no watchdog behind for ever.  Between checks it looks every
``POLL`` seconds, so it leaves within seconds of the stop file or of its tend
run's end, not an interval later.  ``--stop-others`` is what the sequencer runs
before it launches a shard: it stops every other watchdog on the root (one a
killed sequencer left, with that sequencer's cap and thresholds), so the
relaunch is watched by its own watchdog alone.  A progress line it cannot
print (a full disk) is lost, never the watchdog.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
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
#: seconds between looks, while it waits for the next check, at the stop file
#: and (given --parent) at the sequencer and the tend run
POLL = 2.0
#: seconds --stop-others gives a watchdog to leave on SIGTERM before SIGKILL
STOP_GRACE = 10.0
#: COPIED from ``cflib.vocab.AUDIT_STEPS`` (no cflib on the host): the step an audited
#: call's envelope names; tests/test_audit_turn.py pins the copy
AUDIT_STEPS = ("audit", "skeptic", "check", "revision")
ROLES = ("worker", "reviewer")
_ENVELOPE = re.compile(r"(?P<head>.+)\.(?P<stamp>\d{8}T\d{6}Z)\.(?P<pid>\d+)\.(?:json|out)\Z")


def _number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def envelope_name(name: str) -> dict | None:
    """``{task_id, role, step, stamp, pid}`` of a kept envelope's file name, or None.

    Both forms: ``<task>.<role>.<stamp>.<pid>.json`` (``step`` None) and an audited
    call's ``<task>.<role>.<step>.<stamp>.<pid>.json``.  The task id may itself hold
    dots, so the name is read from its right end.
    """
    match = _ENVELOPE.match(name)
    if not match:
        return None
    head = match.group("head")
    step = None
    task, _, last = head.rpartition(".")
    if last in AUDIT_STEPS:
        step = last
        task, _, last = task.rpartition(".")
    if last not in ROLES or not task:
        return None
    return {"task_id": task, "role": last, "step": step, "stamp": match.group("stamp"),
            "pid": int(match.group("pid"))}


def ledger(usage_dir: Path, *, fallback_usd_per_hour: float | None = None) -> dict:
    """What ``usage/`` says was spent: the priced floor and the estimate of the rest.

    ``{dollars, envelopes, unpriced_calls, unpriced_seconds, usd_per_second,
    usd_per_hour, estimated_unpriced_usd}``.  An envelope with a
    ``total_cost_usd`` is priced; one whose ``total_cost_usd`` is null and that
    records an elapsed time (``agent_call``'s record of a call that left no
    envelope) is an unpriced call, estimated at the highest dollars-per-second
    of the priced envelopes that carry a ``duration_ms`` -- or
    ``fallback_usd_per_hour`` before any does; ``estimated_unpriced_usd`` is
    None when neither exists.  ``usd_per_hour`` is that rate an hour, taken
    from the unrounded rate: ``usd_per_second`` is rounded to 9 places, an
    error an hour multiplies by 3600, and ``cf campaign plan`` records a
    pilot's $20 an hour from this key as 20.0.  When any envelope's name carries an
    audited call's step (:func:`envelope_name`), ``by_step`` is ``{step: {envelopes,
    dollars}}`` over the stepped envelopes (the priced floor of each); a root with
    none gets no such key.
    """
    out = {"dollars": 0.0, "envelopes": 0, "unpriced_calls": 0, "unpriced_seconds": 0.0,
           "usd_per_second": None, "usd_per_hour": None, "estimated_unpriced_usd": 0.0}
    if not usage_dir.is_dir():
        return out
    rate = None
    by_step: dict[str, dict] = {}
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
        named = envelope_name(path.name)
        if named and named["step"]:
            row = by_step.setdefault(named["step"], {"envelopes": 0, "dollars": 0.0})
            row["envelopes"] += 1
            row["dollars"] += cost if cost is not None and cost > 0 else 0.0
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
    out["usd_per_hour"] = round(rate * 3600.0, 6) if rate is not None else None
    if out["unpriced_calls"]:
        out["estimated_unpriced_usd"] = (round(out["unpriced_seconds"] * rate, 6)
                                         if rate is not None else None)
    out["dollars"] = round(out["dollars"], 6)
    out["unpriced_seconds"] = round(out["unpriced_seconds"], 1)
    if by_step:
        out["by_step"] = {step: {"envelopes": row["envelopes"],
                                 "dollars": round(row["dollars"], 6)}
                          for step, row in sorted(by_step.items())}
    return out


def spend(usage_dir: Path) -> tuple[float, int]:
    """``(dollars, envelopes)``: the priced floor over every kept JSON envelope."""
    got = ledger(usage_dir)
    return got["dollars"], got["envelopes"]


def gate_counts(log_path: Path) -> dict[str, int]:
    """``{calls, failed, pre_merge_calls, pre_merge_failed}`` over tend's calls in the gate log.

    The gate logs every call and says whether it judged a change set
    (``changed_files``) and whether it was tend's batched pre-merge check
    (``pre_merge``): tend's validation and pre-merge commands judge a change
    set, a worker's or a reviewer's ``--findings`` self-check does not.  Only a
    validation call (``changed_files: true``, ``pre_merge`` not true) is one of
    ``calls``, so an agent that re-runs the gate on its document neither drains
    the root nor dilutes its failure rate, and a contribution tend re-checks in
    its merge batch is not counted twice.  The pre-merge calls are counted
    apart: a batch conflict is a real event, but not a worker's attempt.  A line
    written before the ``pre_merge`` field has none and reads as false.
    """
    out = {"calls": 0, "failed": 0, "pre_merge_calls": 0, "pre_merge_failed": 0}
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("changed_files") is not True:
            continue
        prefix = "pre_merge_" if record.get("pre_merge") is True else ""
        out[f"{prefix}calls"] += 1
        out[f"{prefix}failed"] += 0 if record.get("ok") else 1
    return out


def gate_calls(log_path: Path) -> tuple[int, int]:
    """``(calls, failed)`` over tend's validation calls in the gate log (:func:`gate_counts`)."""
    counts = gate_counts(log_path)
    return counts["calls"], counts["failed"]


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


def pid_alive(pid: int) -> bool:
    """Is process *pid* running (a zombie is not)?"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def tend_alive(root: str) -> bool | None:
    """Is a ``tend run --root <root>`` live?  None when the process table cannot be read.

    The same match as the sequencer's ``pgrep -f``: the joined command line,
    whatever prefix runs tend (``uv run``, a path to it).
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    pattern = re.compile("tend run --root " + re.escape(root) + "( |$)")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        except OSError:
            continue
        if pattern.search(" ".join(a.decode("utf-8", "replace") for a in argv)):
            return True
    return False


def watchdogs_on(root: str) -> list[int]:
    """The pids of the other live ``watchdog.py`` processes given ``--root <root>``.

    Read from each process's argv (``--root R`` or ``--root=R``, the path as
    given or resolved), never matched as a pattern; this process is not one.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    want = {root, os.path.realpath(root)}
    out = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            raw = (entry / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        except OSError:
            continue
        argv = [a.decode("utf-8", "replace") for a in raw]
        if not any(os.path.basename(a) == "watchdog.py" for a in argv):
            continue
        roots = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--root"]
        roots += [a[len("--root="):] for a in argv if a.startswith("--root=")]
        if any(r in want or os.path.realpath(r) in want for r in roots) \
                and pid_alive(int(entry.name)):
            out.append(int(entry.name))
    return sorted(out)


def stop_others(root: str, grace: float = STOP_GRACE) -> tuple[list[int], list[int]]:
    """SIGTERM every other watchdog on *root*, SIGKILL what outlives *grace*.

    ``(stopped, survivors)``: the pids found, and those still alive after both.
    """
    pids = watchdogs_on(root)
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        alive = [p for p in pids if pid_alive(p)]
        for pid in alive:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        deadline = time.monotonic() + wait
        while alive and time.monotonic() < deadline:
            time.sleep(0.1)
            alive = [p for p in alive if pid_alive(p)]
        if not alive:
            return pids, []
    return pids, [p for p in pids if pid_alive(p)]


def orphaned(args) -> bool:
    """Given --parent: is the sequencer gone and no ``tend run --root <root>`` live?"""
    return (args.parent is not None and not pid_alive(args.parent)
            and tend_alive(str(args.root)) is False)


def wait_for_next_check(args, stop: Path) -> str | None:
    """Sleep one interval, looking every POLL seconds; why it must leave now, or None."""
    deadline = time.monotonic() + max(1.0, args.interval)
    while True:
        if stop.exists():
            return "stop"
        if orphaned(args):
            return "orphaned"
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        time.sleep(min(left, POLL))


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
    gate = gate_counts(root / "gate-log" / "gate.jsonl")
    calls, failed = gate["calls"], gate["failed"]
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
              "gate_calls": calls, "gate_failed": failed,
              "pre_merge_calls": gate["pre_merge_calls"],
              "pre_merge_failed": gate["pre_merge_failed"], "merged_tasks": merged,
              "reasons": reasons, "drained": bool(previous.get("drained")), "action": None}
    if "by_step" in spent:
        record["spend_by_step"] = spent["by_step"]
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
    ap.add_argument("--max-cost", type=float, default=None,
                    help="the shard's spend cap from its run.yaml, in USD (required)")
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
    ap.add_argument("--parent", type=int, default=None, metavar="PID",
                    help="the sequencer's pid: once it is gone, watch only while a "
                         "`tend run --root <root>` is live")
    ap.add_argument("--stop-others", action="store_true",
                    help="stop every other watchdog on --root, print their pids and exit "
                         "(1 when one would not stop); nothing is checked")
    args = ap.parse_args(argv)
    if args.stop_others:
        stopped, survivors = stop_others(str(args.root))
        print(" ".join(map(str, stopped)), flush=True)
        if survivors:
            print(f"watchdog: {' '.join(map(str, survivors))} on {args.root} would not stop",
                  file=sys.stderr)
            return 1
        return 0
    if args.max_cost is None:
        ap.error("the following arguments are required: --max-cost")
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
        try:
            print(json.dumps(record, sort_keys=True), flush=True)
        except OSError:
            pass        # a full disk loses this line; watchdog.jsonl is the record
        if args.once or record["drained"] or stop.exists():
            return 2 if record["drained"] else 0
        why = wait_for_next_check(args, stop)
        if why == "stop":
            return 0
        if why == "orphaned":
            try:
                print(f"watchdog: the sequencer (pid {args.parent}) is gone and no tend run is "
                      f"live on {args.root}; stopping", file=sys.stderr)
            except OSError:
                pass
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
