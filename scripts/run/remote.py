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
import re
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


def rsync_supports_chmod(rsync: str = "rsync") -> bool:
    """Does this rsync accept ``--chmod``?

    macOS still ships rsync 2.6.9 (2006), which does not: it exits 1 with
    "Invalid argument passed to --chmod" and takes the transfer with it.  The
    flag arrived in rsync 3.0, so the read-only companion transfer -- a control
    the security review added -- cannot be expressed on a stock Mac.  Rather than
    drop the control or demand a Homebrew rsync, the caller falls back to
    chmod-ing on the host after the transfer (see :func:`_rsync`).
    """
    try:
        out = subprocess.run([rsync, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    first = (out.stdout or "").splitlines()[:1]
    match = re.search(r"version\s+(\d+)\.(\d+)", first[0] if first else "")
    return bool(match) and (int(match.group(1)), int(match.group(2))) >= (3, 0)


def _rsync(entry: dict, host: dict) -> dict:
    from .manifest import BASE_RSYNC

    if entry["kind"] == "files":
        sources = list(entry.get("srcs") or [])
    else:
        src = entry["src"]
        sources = [src + "/" if entry["kind"] == "dir" and not src.endswith("/") else src]
    flags = [f for f in entry["rsync_flags"]
             if not (f.startswith("--chmod=") and not rsync_supports_chmod())]
    argv = ["rsync", *BASE_RSYNC, *flags, *sources, f"{host['ssh']}:{entry['dest']}"]
    return {"argv": argv, "shell": " ".join(shlex.quote(a) for a in argv)}


def _chmod_fallback_modes(entry: dict) -> tuple[str, str] | None:
    """``(dir_mode, file_mode)`` when the read-only transfer must be applied on the host.

    ``None`` when this rsync can do it in flight, which is the normal path.  The
    property is the same either way -- the copy ends up 0444 inside 0555 -- but
    with an rsync that lacks ``--chmod`` it is applied a moment after the files
    land rather than as they land.  That window is on the host, before any agent
    starts, and the control was always owner-reversible defence in depth rather
    than a barrier (manifest.tree_digest): the digest recomputed after the
    transfer is what actually notices an edit.
    """
    chmod = [f for f in entry.get("rsync_flags", []) if f.startswith("--chmod=")]
    if not chmod or rsync_supports_chmod():
        return None
    modes = chmod[0].split("=", 1)[1]
    dirs = next((m[1:] for m in modes.split(",") if m.startswith("D")), "0555")
    files = next((m[1:] for m in modes.split(",") if m.startswith("F")), "0444")
    return dirs, files


#: How to make a tend report its version.  NOT ``tend --version``: the CLI that
#: shipped as tend 0.1.0 has no such flag and its top-level parser requires
#: ``--root``, so the probe exited 2 ("the following arguments are required:
#: --root") and took the whole preflight with it.  Installed package metadata is
#: there whatever the CLI chooses to expose, and prints nothing else.
TEND_VERSION_PROBE = (
    "python -c \"import importlib.metadata as m;"
    "print(m.version('tend'))\" 2>/dev/null || echo unknown"
)


def _tend_version_step(host: dict, tend: str) -> str:
    """The preflight fragment that checks -- or, unrecorded, only prints -- tend's version."""
    required = str(host.get("tend_version_required") or "")
    probe = f"{tend} {TEND_VERSION_PROBE}"
    if not required:
        return (f"{probe}; "
                "echo 'tend version NOT checked: record hosts."
                f"{host.get('name', '<host>')}.tend_version (e.g. \">=0.4,<0.6\") in "
                "workspace.yaml and cf run will refuse a mismatch'")
    return (f"{probe} 2>&1 | tail -1 | python3 -c "
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
                  names: dict, budget: dict, concurrency: dict,
                  force: bool = False) -> list[dict]:
    """The ordered command plan: preflight, transfer, verify, smoke, raise, monitor.

    Each step is ``{stage, title, argv, shell, safe}``; ``safe`` marks the
    read-only steps (`cf run --execute` will run any stage up to the one asked
    for, but never the 'raise' stage without --smoke-verified).
    """
    root = host["remote_root"]
    tend = host["tend_cmd"]
    tend_root = f"{root}/{names['tend_root']}"
    entrypoint = f"{root}/{names['entrypoint']}"
    tasks_commit = shlex.quote("tasks for " + run_id)
    steps: list[dict] = []

    def add(stage: str, title: str, cmd: dict, safe: bool = False, **extra) -> None:
        steps.append({"stage": stage, "title": title, "safe": safe, **cmd, **extra})

    add("preflight", "host reachable, tend at the required version, disk free",
        _ssh(host, f"set -e; uname -sm; df -h {shlex.quote(root)} | tail -1; "
                   f"command -v agentbox >/dev/null && echo 'agentbox: ok' || echo 'agentbox: MISSING'; "
                   + _tend_version_step(host, tend)), safe=True, record_output=True)

    if not force:
        # Every transfer commits tasks/ over main, so once tend has merged into
        # it a re-send reopens each finished task and the fleet runs it again.
        add("transfer", f"refuse to re-send tasks/ once main has moved past 'tasks for {run_id}' "
                        "(--force sends anyway)",
            _ssh(host, f"head=$(git --git-dir={shlex.quote(entrypoint + '/.git')} log -1 "
                       "--format=%s 2>/dev/null); "
                       f"test -z \"$head\" || test \"$head\" = {tasks_commit} || "
                       "{ printf 'REFUSED: the entrypoint is at \"%s\", past %s; re-sending "
                       f"tasks/ would reopen every finished task\\n' \"$head\" {tasks_commit} >&2; "
                       "exit 1; }"), safe=True)

    dirs = sorted({str(Path(e["dest"]).parent) if e["kind"] == "file" else e["dest"].rstrip("/")
                   for e in entries})
    add("transfer", "create remote directories",
        _ssh(host, "mkdir -p " + " ".join(shlex.quote(d) for d in dirs)))
    for entry in entries:
        if not entry["exists"]:
            continue
        if "--delete" in entry["rsync_flags"]:
            # An earlier copy is read-only, and rsync cannot unlink a stale file
            # inside a directory without u+w.
            dest = shlex.quote(entry["dest"])
            add("transfer", f"make the earlier copy of {entry['name']} writable so --delete "
                            f"can remove what was deleted since",
                _ssh(host, f"test ! -e {dest} || chmod -R u+w {dest}"))
        add("transfer", f"rsync {entry['name']} -> {entry['dest']}", _rsync(entry, host))
        modes = _chmod_fallback_modes(entry)
        if modes:
            # This rsync cannot express --chmod (macOS ships 2.6.9), so the
            # read-only property is applied on the host instead of in flight.
            # A separate step, not a shell suffix: `execute` runs argv, so a
            # suffix on `shell` would print and never run.
            dirs_mode, files_mode = modes
            add("transfer", f"make {entry['name']} read-only on the host (rsync lacks --chmod)",
                _ssh(host, f"find {shlex.quote(entry['dest'])} -type d -exec chmod {dirs_mode} {{}} + ; "
                           f"find {shlex.quote(entry['dest'])} -type f -exec chmod {files_mode} {{}} +"))
    # Commit on EVERY transfer, not only when the repo is created.  The old
    # `git rev-parse --git-dir || (git init && git add -A && git commit)` committed
    # exactly once: a re-plan that rsynced a different task set into an existing
    # entrypoint left those files untracked, and tend's batched merge checks the
    # entrypoint is clean before it fast-forwards -- a dirty tree bounces the
    # WHOLE batch back to pending.  That is what silently stopped the first
    # server run: 383 task files on disk, 163 tracked, 220 untracked, every
    # reviewer-approved worktree bounced, nothing ever reached main.  The final
    # check fails the stage loudly rather than leaving a run that can never merge.
    add("transfer", "commit the transferred tasks on the entrypoint's main (tend bounces a dirty tree)",
        _ssh(host, f"cd {shlex.quote(entrypoint)} && "
                   f"(git rev-parse --git-dir >/dev/null 2>&1 || git init -q -b main .) && "
                   f"git add -A && "
                   f"(git diff --cached --quiet || git commit -qm {tasks_commit}) && "
                   f"test -z \"$(git status --porcelain)\" || "
                   f"{{ echo 'REFUSED: entrypoint still dirty after commit; tend will bounce every merge batch' >&2; exit 1; }}"))

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

    # The limits go on `tend run` ITSELF, not through tend-control first.
    # tend-control writes into orchestrator.sqlite, which does not exist until a
    # run has started, so the old "hold the fleet at 1/1, then start it" order
    # applied nothing: the smoke run came up at the config's 8 workers and
    # launched eight agents at once -- the exact thing a smoke test exists to
    # prevent, on the first real run, at Opus prices.  `tend run` takes the caps
    # as flags and applies them from the first scheduling pass.
    #
    # `</dev/null` and both streams redirected: otherwise ssh waits on the detached
    # run's file descriptors and the command never returns.
    add("smoke", "start the run at 1 worker / 1 reviewer (detached; watch ONE task worker -> gate -> reviewer -> merge)",
        _ssh(host, f"nohup sh -c {shlex.quote(f'{tend} tend run --root {tend_root} --log-level INFO --max-concurrent-worker-agents 1 --max-concurrent-reviewer-agents 1')} "
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
    """Stage names up to and including *stage* (raise/monitor/stop act on the running fleet alone)."""
    if stage in ("raise", "monitor", "stop"):
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
