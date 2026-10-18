#!/usr/bin/env python3
"""One agent call, inside a session slot, under a timeout, with its envelope kept.

New code, implementing docs/proposals/wikipedia-locate.md section 6 (the
session cap; cost and spend) for the bridges in this directory, which call it
instead of running the agent CLI themselves:

    agent_call.py --role worker --raw RAW --err ERR [--usage-dir DIR] [--task-id ID] \\
        -- agentbox host claude --profile P -- -p ... --output-format json

* **The session cap.**  The account allows 16 concurrent agent sessions per
  Unix user, across every root, and the 17th gets HTTP 409s.  Before the call
  this takes one of ``CF_SLOTS`` (15) slot files in ``CF_SLOT_DIR`` (per user,
  default ``~/.cache/cf-agent-slots``) with ``flock(2)``, holds it for the
  call, and WAITS for a free one rather than failing: a bridge that exited
  would only be retried by tend into the same wait.  The lock fd is not
  inherited by the agent, so a daemon the agent leaves behind cannot pin a
  slot.
* **The timeout.**  The call runs in its own process group under
  ``CF_AGENT_TIMEOUT`` (``45m``; ``s``/``m``/``h`` or seconds); expiry kills the
  group and exits 124.  An expiry is an infrastructure failure, never a
  verdict: the bridge exits non-zero and tend retries the same worktree.
* **Signals are forwarded.**  The agent runs in its own session, so a TERM,
  INT or HUP sent to the bridge's process group (tend stopping, a Ctrl-C, a
  hang-up) would otherwise leave it running with no timeout and its slot
  free: each is passed to the agent's group, which is then stopped as on a
  timeout, before the slot is let go; the call exits 128 + the signal.
* **409 and empty envelopes** exit 75 (EX_TEMPFAIL) for the same reason.
* **The envelope is kept.**  The agent's stdout -- Claude's JSON envelope, with
  its usage and ``total_cost_usd`` -- is copied to
  ``<usage-dir>/<task-id>.<role>.<UTC stamp>.<pid>.json`` (``.out`` when it is
  not JSON), outside the worktree, where the spend watchdog and the cost
  cross-check read it.  The bridges used to delete it.

Standard library only: it runs on the run host, where cflib is not installed.
Exit status: the agent's own on a completed call (the bridge normalises it),
124 on a timeout, 75 on an empty or 409 envelope, 128 + N when signal N
stopped it, 64 on a usage error.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

EXIT_TIMEOUT = 124
EXIT_TEMPFAIL = 75
EXIT_USAGE = 64
DEFAULT_SLOTS = 15
DEFAULT_TIMEOUT = "45m"
#: seconds between attempts to take a slot (plus jitter)
POLL_SECONDS = 5.0
#: seconds between the TERM and the KILL of a timed-out process group
KILL_GRACE = 30.0
#: what a caller's stop arrives as; each is forwarded to the agent's group
FORWARDED_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
_CONFLICT = re.compile(r"(?:\b(?:HTTP|status|API Error|error)\W{0,4}409\b|\b409\W{0,3}"
                       r"(?:conflict|too many))", re.I)


def parse_duration(text: str) -> float:
    """``45m`` / ``90s`` / ``2h`` / ``2700`` -> seconds; ValueError otherwise."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(text or ""))
    if not match:
        raise ValueError(f"not a duration: {text!r} (want e.g. 45m, 90s, 2h or seconds)")
    value = float(match.group(1))
    return value * {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2)]


def slot_dir() -> Path:
    configured = os.environ.get("CF_SLOT_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("HOME") or "~").expanduser() / ".cache" / "cf-agent-slots"


def acquire_slot(directory: Path, slots: int, *, poll: float = POLL_SECONDS,
                 log=None) -> tuple[int, int]:
    """``(fd, slot number)`` of the first free slot, waiting for one when all are taken."""
    directory.mkdir(parents=True, exist_ok=True)
    waited = 0.0
    said = False
    while True:
        for number in range(1, slots + 1):
            fd = os.open(directory / f"slot-{number:02d}.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            os.set_inheritable(fd, False)
            if said and log:
                log(f"agent_call: slot {number} free after {waited:.0f}s")
            return fd, number
        if not said and log:
            log(f"agent_call: all {slots} session slots in {directory} are taken; waiting")
            said = True
        pause = poll + random.uniform(0, poll)
        time.sleep(pause)
        waited += pause


def is_conflict(raw: str, stderr: str) -> bool:
    """Did the agent's host answer 409 (the session cap) instead of running?"""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict):
        if data.get("api_error_status") == 409 or str(data.get("status")) == "409":
            return True
        if data.get("is_error") or data.get("subtype") not in (None, "success"):
            text = " ".join(str(data.get(k) or "") for k in ("result", "error", "message"))
            if _CONFLICT.search(text) or re.search(r"\b409\b", text):
                return True
        return False
    return bool(_CONFLICT.search(raw[-4000:]) or _CONFLICT.search(stderr[-4000:]))


def keep_envelope(usage_dir: Path | None, raw: bytes, *, task_id: str, role: str) -> Path | None:
    """Copy the envelope outside the worktree; a failure to keep it never fails the call."""
    if usage_dir is None:
        return None
    try:
        json.loads(raw.decode("utf-8"))
        suffix = "json"
    except (UnicodeDecodeError, ValueError):
        suffix = "out"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    safe_task = re.sub(r"[^A-Za-z0-9._-]", "_", task_id or "no-task")[:120]
    try:
        usage_dir.mkdir(parents=True, exist_ok=True)
        path = usage_dir / f"{safe_task}.{role}.{stamp}.{os.getpid()}.{suffix}"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, path)
        return path
    except OSError:
        return None


class _Signalled(Exception):
    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


def stop_group(proc: subprocess.Popen) -> None:
    """TERM the agent's process group, then KILL it after :data:`KILL_GRACE`."""
    for sig, grace in ((signal.SIGTERM, KILL_GRACE), (signal.SIGKILL, None)):
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            pass
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def _forward_signals(proc: subprocess.Popen) -> dict:
    """Pass TERM/INT/HUP on to the agent's group; the first one also ends the wait."""
    caught: list[int] = []

    def forward(signum, _frame):
        try:
            os.killpg(proc.pid, signum)
        except OSError:
            pass
        if not caught:
            caught.append(signum)
            raise _Signalled(signum)

    previous = {}
    for sig in FORWARDED_SIGNALS:
        try:
            previous[sig] = signal.signal(sig, forward)
        except (ValueError, OSError):      # not the main thread: nothing to forward from
            pass
    return previous


def run(argv: list[str], *, role: str, raw_path: Path, err_path: Path | None,
        usage_dir: Path | None, task_id: str, slots: int, timeout: float,
        poll: float = POLL_SECONDS) -> int:
    def log(line: str) -> None:
        print(line, file=sys.stderr)
        if err_path is not None:
            try:
                with open(err_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    fd, number = acquire_slot(slot_dir(), slots, poll=poll, log=log)
    started = time.time()
    # This call's stderr on its own, so a 409 an EARLIER call logged to the shared
    # err file cannot make this one read as a conflict; appended there afterwards.
    err_tmp = raw_path.with_name(raw_path.name + ".stderr")
    signalled = 0
    previous: dict = {}
    try:
        with open(raw_path, "wb") as out, open(err_tmp, "wb") as err_fh:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err_fh,
                                    start_new_session=True, close_fds=True)
            previous = _forward_signals(proc)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                stop_group(proc)
                code = None
            except _Signalled as exc:
                # the agent's group got the signal; make sure it is gone before
                # the slot is let go, or a 16th session could start beside it
                stop_group(proc)
                code, signalled = None, exc.signum
    except OSError as exc:
        log(f"agent_call: could not run {argv[0]!r}: {exc}")
        return EXIT_TEMPFAIL
    finally:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        os.close(fd)
    stderr_bytes = err_tmp.read_bytes() if err_tmp.exists() else b""
    try:
        err_tmp.unlink()
    except OSError:
        pass
    if stderr_bytes:
        try:
            if err_path is not None:
                with open(err_path, "ab") as fh:
                    fh.write(stderr_bytes)
            else:
                sys.stderr.buffer.write(stderr_bytes)
                sys.stderr.flush()
        except OSError:
            pass
    raw = raw_path.read_bytes() if raw_path.exists() else b""
    kept = keep_envelope(usage_dir, raw, task_id=task_id, role=role)
    seconds = time.time() - started
    where = f" (kept at {kept})" if kept else ""
    if signalled:
        log(f"agent_call: role={role} task={task_id or '-'} slot={number} STOPPED by signal "
            f"{signalled} after {seconds:.0f}s; the agent's process group was stopped with it"
            f"{where}")
        return 128 + signalled
    if code is None:
        log(f"agent_call: role={role} task={task_id or '-'} slot={number} TIMED OUT after "
            f"{seconds:.0f}s: an infrastructure failure, not a verdict{where}")
        return EXIT_TIMEOUT
    text = raw.decode("utf-8", errors="replace")
    stderr_tail = stderr_bytes.decode("utf-8", errors="replace")[-4000:]
    if not text.strip():
        log(f"agent_call: role={role} task={task_id or '-'} slot={number} exit={code}: EMPTY "
            f"envelope after {seconds:.0f}s; tend retries{where}")
        return EXIT_TEMPFAIL
    if is_conflict(text, stderr_tail):
        log(f"agent_call: role={role} task={task_id or '-'} slot={number}: HTTP 409 (the "
            f"session cap) after {seconds:.0f}s; tend retries{where}")
        return EXIT_TEMPFAIL
    log(f"agent_call: role={role} task={task_id or '-'} slot={number} exit={code} "
        f"{seconds:.0f}s{where}")
    return int(code)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("agent_call: usage: agent_call.py [options] -- <agent command ...>", file=sys.stderr)
        return EXIT_USAGE
    split = argv.index("--")
    ap = argparse.ArgumentParser(prog="agent_call.py")
    ap.add_argument("--role", required=True, choices=("worker", "reviewer"))
    ap.add_argument("--raw", required=True, help="where the agent's stdout goes")
    ap.add_argument("--err", default=None, help="append the agent's stderr here")
    ap.add_argument("--usage-dir", default=None, help="keep a copy of the envelope here")
    ap.add_argument("--task-id", default=os.environ.get("TEND_TASK_ID", ""))
    args = ap.parse_args(argv[:split])
    command = argv[split + 1:]
    if not command:
        print("agent_call: no agent command after --", file=sys.stderr)
        return EXIT_USAGE
    try:
        timeout = parse_duration(os.environ.get("CF_AGENT_TIMEOUT") or DEFAULT_TIMEOUT)
        slots = int(os.environ.get("CF_SLOTS") or DEFAULT_SLOTS)
        poll = float(os.environ.get("CF_SLOT_POLL") or POLL_SECONDS)
        if slots < 1 or timeout <= 0:
            raise ValueError("CF_SLOTS and CF_AGENT_TIMEOUT must be positive")
    except ValueError as exc:
        print(f"agent_call: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return run(command, role=args.role, raw_path=Path(args.raw),
               err_path=Path(args.err) if args.err else None,
               usage_dir=Path(args.usage_dir) if args.usage_dir else None,
               task_id=args.task_id, slots=slots, timeout=timeout, poll=poll)


if __name__ == "__main__":
    raise SystemExit(main())
