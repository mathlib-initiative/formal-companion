"""Render (and, only on demand, execute) the remote commands of a server run.

New code, but every command here is one the operator ran by hand in the v1
campaign's runbook (references/runbook-server.md is the version that ships):

    uv run tend-control limits --root ... --workers 1 --reviewers 1   # smoke test
    uv run tend run --root ... --log-level INFO
    uv run tend-control budget --root ... --max-cost 50               # BEFORE raising
    uv run tend-control limits --root ... --workers 6 --reviewers 4
    uv run tend-control status --root ...

The order is the point.  "Smoke-test first -- do not launch 382 tasks blind": the
model ids in the first run had never been exercised from that machine, and a wrong
name fails every task immediately and repeatedly.  And the budget ceiling is set
BEFORE concurrency is raised, because `request_changes` sends a task back for
another worker turn, so the agent count is a lower bound, not a total.

Two steps here are checks rather than commands the operator ran by hand:
preflight compares the host's ``tend --version`` against the range recorded in
``workspace.yaml`` (``hosts.<host>.tend_version``) instead of printing a version
nobody reads, and the verify/monitor stages recompute the digest of each
transferred companion tree, so a read-only copy an agent made writable again is
visible before its findings are harvested.  Both record their output into
run.yaml through :func:`execute`, including when they REFUSE: a step that exits
non-zero raises, and the one outcome worth a durable record must not be the one
that writes nothing, so :func:`execute` appends into a caller-owned ``results``
list rather than only returning it.

Nothing in this module runs anything unless ``execute=True`` is passed, and
cf run only passes it when the operator gave --execute for a host on the
workspace's allowlist.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from ._compat import CfError, iso_now


#: Compare the host's ``tend --version`` (on stdin) with a spec like
#: ``">=0.4,<0.6"`` (argv[1]) and refuse a mismatch.  Preflight used to print the
#: version and move on: nothing in the repo says which tend writes the config
#: schema ``cf plan-run`` targets, so a host running a different one validated
#: locally and failed every task there with no diagnosis.
TEND_VERSION_CHECK = r'''
import re, sys

spec = sys.argv[1]
text = sys.stdin.read().strip()
found = re.search(r"([0-9]+(?:\.[0-9]+)*)", text)
if not found:
    sys.stderr.write("REFUSED: no version in tend's output %r (required %s)\n" % (text, spec))
    raise SystemExit(1)
got = found.group(1)


def order(left, right):
    a = [int(x) for x in left.split(".")]
    b = [int(x) for x in right.split(".")]
    width = max(len(a), len(b))
    a += [0] * (width - len(a))
    b += [0] * (width - len(b))
    return (a > b) - (a < b)


tests = {">=": lambda c: c >= 0, "<=": lambda c: c <= 0, "==": lambda c: c == 0,
         ">": lambda c: c > 0, "<": lambda c: c < 0, "=": lambda c: c == 0}
unmet = []
for clause in [c.strip() for c in spec.split(",") if c.strip()]:
    for op in (">=", "<=", "==", ">", "<", "="):
        if clause.startswith(op):
            if not tests[op](order(got, clause[len(op):].strip())):
                unmet.append(clause)
            break
    else:
        if order(got, clause) != 0:
            unmet.append("==" + clause)
print("tend %s (required %s)" % (got, spec))
if unmet:
    sys.stderr.write("REFUSED: tend %s on this host does not satisfy %s\n"
                     % (got, ", ".join(unmet)))
    sys.stderr.write("  the run's config.yaml was written for the recorded range; a schema\n")
    sys.stderr.write("  change fails every task on the host and reads as a model error.\n")
    sys.stderr.write("  Update tend there, or change hosts.<host>.tend_version in workspace.yaml.\n")
    raise SystemExit(1)
'''


def _ssh(host: dict, remote_cmd: str) -> dict:
    return {"argv": ["ssh", host["ssh"], remote_cmd], "shell": f"ssh {shlex.quote(host['ssh'])} {shlex.quote(remote_cmd)}"}


def _rsync(entry: dict, host: dict) -> dict:
    from .manifest import BASE_RSYNC

    if entry["kind"] == "files":
        sources = list(entry.get("srcs") or [])
    else:
        src = entry["src"]
        sources = [src + "/" if entry["kind"] == "dir" and not src.endswith("/") else src]
    argv = ["rsync", *BASE_RSYNC, *entry["rsync_flags"], *sources,
            f"{host['ssh']}:{entry['dest']}"]
    return {"argv": argv, "shell": " ".join(shlex.quote(a) for a in argv)}


def _tend_version_step(host: dict, tend: str) -> str:
    """The preflight fragment that checks -- or, unrecorded, only prints -- tend's version."""
    required = str(host.get("tend_version_required") or "")
    if not required:
        return (f"{tend} tend --version; "
                "echo 'tend version NOT checked: record hosts."
                f"{host.get('name', '<host>')}.tend_version (e.g. \">=0.4,<0.6\") in "
                "workspace.yaml and cf run will refuse a mismatch'")
    return (f"{tend} tend --version 2>&1 | tail -1 | python3 -c "
            + shlex.quote(TEND_VERSION_CHECK) + " " + shlex.quote(required))


def _tree_check(host: dict, entry: dict) -> dict:
    """Recompute a transferred companion tree's digest on the host and compare.

    The transfer is read-only (``--chmod=D0555,F0444``) but owner-reversible, and
    the workers hold shell on that box, so this is the cheap check that the tree
    the findings cite is still the tree that was sent.
    """
    from .manifest import REMOTE_TREE_DIGEST_PY, SOURCE_EXCLUDES

    dest = entry["dest"].rstrip("/")
    want = str(entry.get("tree_sha256") or "")
    cmd = " ".join(["python3 -c", shlex.quote(REMOTE_TREE_DIGEST_PY), shlex.quote(dest),
                    shlex.quote(want)] + [shlex.quote(p) for p in SOURCE_EXCLUDES])
    return _ssh(host, cmd)


def plan_commands(*, host: dict, run_id: str, run: dict, entries: list[dict],
                  names: dict, budget: dict, concurrency: dict) -> list[dict]:
    """The ordered command plan: preflight, transfer, verify, smoke, raise, monitor.

    Each step is ``{stage, title, argv, shell, safe}``; ``safe`` marks the
    read-only steps (`cf run --execute` will run any stage up to the one asked
    for, but never the 'raise' stage without --smoke-verified).
    """
    root = host["remote_root"]
    tend = host["tend_cmd"]
    tend_root = f"{root}/{names['tend_root']}"
    entrypoint = f"{root}/{names['entrypoint']}"
    steps: list[dict] = []

    def add(stage: str, title: str, cmd: dict, safe: bool = False, **extra) -> None:
        steps.append({"stage": stage, "title": title, "safe": safe, **cmd, **extra})

    add("preflight", "host reachable, tend at the required version, disk free",
        _ssh(host, f"set -e; uname -sm; df -h {shlex.quote(root)} | tail -1; "
                   f"command -v agentbox >/dev/null && echo 'agentbox: ok' || echo 'agentbox: MISSING'; "
                   + _tend_version_step(host, tend)), safe=True, record_output=True)

    dirs = sorted({str(Path(e["dest"]).parent) if e["kind"] == "file" else e["dest"].rstrip("/")
                   for e in entries})
    add("transfer", "create remote directories",
        _ssh(host, "mkdir -p " + " ".join(shlex.quote(d) for d in dirs)))
    for entry in entries:
        if not entry["exists"]:
            continue
        add("transfer", f"rsync {entry['name']} -> {entry['dest']}", _rsync(entry, host))
    add("transfer", "make the entrypoint a git repo on main (tend merges into it)",
        _ssh(host, f"cd {shlex.quote(entrypoint)} && git rev-parse --git-dir >/dev/null 2>&1 || "
                   f"(git init -b main . && git add -A && git commit -qm 'tasks for {run_id}')"))

    add("verify", "task files and dependency graph",
        _ssh(host, f"{tend} tend-task verify {shlex.quote(entrypoint + '/tasks')}"), safe=True)
    add("verify", "orchestrator config",
        _ssh(host, f"{tend} tend validate-config --root {shlex.quote(tend_root)}"), safe=True)
    add("verify", "gate runs out of tree against one staged finding (fails closed)",
        _ssh(host, f"test -f {shlex.quote(root + '/' + names['gate'] + '/verify_findings.py')} "
                   f"&& echo 'gate present (out of tree)'"), safe=True)
    # BEFORE: the companion trees as they landed, hashed on the host. Recorded in
    # run.yaml (run.executed) with the digest they were sent with.
    for entry in entries:
        if not entry.get("tree_sha256"):
            continue
        add("verify", f"{entry['name']} landed byte for byte (read-only transfer, "
                      f"owner-reversible)",
            _tree_check(host, entry), safe=True, record_output=True,
            expected=entry["tree_sha256"])

    add("smoke", "hold the fleet at 1 worker / 1 reviewer",
        _ssh(host, f"{tend} tend-control limits --root {shlex.quote(tend_root)} "
                   f"--workers 1 --reviewers 1"))
    # `</dev/null` and both streams redirected: otherwise ssh waits on the detached
    # run's file descriptors and the command never returns.
    add("smoke", "start the run (detached; watch ONE task worker -> gate -> reviewer -> merge)",
        _ssh(host, f"nohup sh -c {shlex.quote(f'{tend} tend run --root {tend_root} --log-level INFO')} "
                   f"</dev/null >> {shlex.quote(tend_root + '/run.log')} 2>&1 & echo started"))
    add("smoke", "watch it",
        _ssh(host, f"{tend} tend-control status --root {shlex.quote(tend_root)}"), safe=True)

    if budget.get("max_cost") is not None:
        add("raise", f"set the spend ceiling BEFORE raising concurrency "
                     f"({budget['max_cost']} {budget['currency']})",
            _ssh(host, f"{tend} tend-control budget --root {shlex.quote(tend_root)} "
                       f"--max-cost {budget['max_cost']}"))
    add("raise", f"raise limits to {concurrency['workers']} workers / {concurrency['reviewers']} reviewers",
        _ssh(host, f"{tend} tend-control limits --root {shlex.quote(tend_root)} "
                   f"--workers {concurrency['workers']} --reviewers {concurrency['reviewers']}"))

    add("monitor", "progress and spend",
        _ssh(host, f"{tend} tend-control status --root {shlex.quote(tend_root)}"), safe=True)
    add("monitor", "merged findings so far",
        _ssh(host, f"ls -1 {shlex.quote(entrypoint + '/findings')} | wc -l"), safe=True)
    # AFTER: the same digests, so an agent that chmod'ed a companion copy and
    # edited it is caught before the findings that cite it are harvested.
    for entry in entries:
        if not entry.get("tree_sha256"):
            continue
        add("monitor", f"{entry['name']} unchanged since the transfer",
            _tree_check(host, entry), safe=True, record_output=True,
            expected=entry["tree_sha256"])
    add("stop", "pause (keeps state) / drain / stop",
        _ssh(host, f"{tend} tend-control pause --root {shlex.quote(tend_root)}"))
    return steps


STAGE_ORDER = ["preflight", "transfer", "verify", "smoke", "raise", "monitor", "stop"]


def stages_upto(stage: str) -> list[str]:
    """Stage names up to and including *stage* (monitor/stop stand alone)."""
    if stage in ("monitor", "stop"):
        return [stage]
    if stage not in STAGE_ORDER:
        raise CfError(f"unknown stage {stage!r} (want {', '.join(STAGE_ORDER)})")
    return STAGE_ORDER[:STAGE_ORDER.index(stage) + 1]


def render(steps: Iterable[dict], stages: Iterable[str]) -> str:
    """The copy-pasteable command block for the chosen stages."""
    wanted = list(stages)
    out: list[str] = []
    current = None
    for step in steps:
        if step["stage"] not in wanted:
            continue
        if step["stage"] != current:
            current = step["stage"]
            out.append(f"\n# ---- {current.upper()} " + "-" * (58 - len(current)))
        out.append(f"# {step['title']}")
        out.append(step["shell"])
    return "\n".join(out).strip()


def execute(steps: Iterable[dict], stages: Iterable[str], *, dry_run: bool = True,
            stream: Any = sys.stdout, results: list[dict] | None = None) -> list[dict]:
    """Run the chosen stages in order, stopping at the first failure.

    Returns one record per attempted step.  With ``dry_run`` (the default of every
    caller) nothing is executed and the records say so.

    Pass ``results`` to keep the records of a run that FAILED: a non-zero step
    raises ``CfError``, and a caller that only reads the return value discards
    exactly the record it most needs -- a refused companion-tree digest, or a
    refused ``tend --version``.  `cf run` passes its own list and writes it into
    run.yaml before re-raising.
    """
    wanted = list(stages)
    results = results if results is not None else []
    for step in steps:
        if step["stage"] not in wanted:
            continue
        print(f"$ {step['shell']}", file=stream)
        if dry_run:
            results.append({"stage": step["stage"], "title": step["title"], "ran": False})
            continue
        proc = subprocess.run(step["argv"], text=True, capture_output=True)
        if proc.stdout:
            print(proc.stdout.rstrip(), file=stream)
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        record = {"stage": step["stage"], "title": step["title"], "ran": True,
                  "at": iso_now(), "returncode": proc.returncode}
        if step.get("record_output"):
            # The tend version and the companion-tree digests are facts about the
            # run, not chatter: `cf run` writes these records into run.yaml, whose
            # sha the harvest receipt pins.
            record["stdout"] = (proc.stdout or "").strip()[-2000:]
            if step.get("expected"):
                record["expected"] = step["expected"]
        results.append(record)
        if proc.returncode != 0:
            record["failed"] = True
            raise CfError(f"remote step failed ({proc.returncode}): {step['title']}",
                          hint=f"command was: {step['shell']}")
    return results
