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

Some steps here are checks rather than commands the operator ran by hand:
preflight compares the host's ``tend --version`` against the range recorded in
``workspace.yaml`` (``hosts.<host>.tend_version``) instead of printing a version
nobody reads, the verify/monitor stages recompute the digest of each
transferred companion tree, so a read-only copy an agent made writable again is
visible before its findings are harvested, and a locate run's verify stage
checks its databases and every source layer its gate reads against the shas
gate.json pins (references/locate-documents.md section 9), and an informalize
phase's runs its own gate's self-check, which reads the library's source and the
TeX the agents build with (docs/proposals/informalize.md section 8.3).  They record their
output into run.yaml through :func:`execute`, including when they REFUSE: a
step that exits non-zero raises, and the one outcome worth a durable record
must not be the one that writes nothing, so :func:`execute` appends into a
caller-owned ``results`` list rather than only returning it.

An informalize phase's smoke stage starts one more thing beside ``tend run``: the
spend watchdog (:func:`watchdog_steps`), because tend's own budget counts no
agentbox spend and the cap in run.yaml is otherwise enforced by nobody
(docs/proposals/informalize.md section 8.1).

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

from ._compat import CfError, hashing, iso_now


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


#: the agent CLI each bridge launches, for the preflight's version line
AGENT_CLIS = {"agentbox-claude": "claude", "claude-p": "claude", "agentbox-codex": "codex"}
#: the preflight step that prints ``<label>: <version>`` lines, and the run.yaml
#: ``versions`` key each label fills (references/locate-documents.md section 9)
VERSIONS_TITLE = "versions: tend, agentbox, the agent CLI (recorded in run.yaml)"
VERSION_LABELS = {"tend-version": "tend", "agentbox-version": "agentbox",
                  "agent-cli-version": "agent_cli"}


def _versions_step(tend: str, bridge: str) -> str:
    """Print one ``<label>: <version>`` line per pin; ``unknown`` when a probe finds none."""
    cli = AGENT_CLIS.get(bridge, "")
    cli_probe = (f"$( ({cli} --version || echo unknown) 2>/dev/null | head -1)" if cli
                 else "none (this bridge launches no agent CLI of its own)")
    return ("printf 'tend-version: %s\\n' \"$( { " + f"{tend} {TEND_VERSION_PROBE}"
            + "; } 2>/dev/null | tail -1)\"; "
            "printf 'agentbox-version: %s\\n' \"$( (agentbox --version || echo unknown) "
            "2>/dev/null | head -1)\"; "
            f"printf 'agent-cli-version: %s\\n' \"{cli_probe}\"")


def recorded_versions(results: Iterable[dict]) -> dict[str, str]:
    """``{tend, agentbox, agent_cli}`` parsed from the executed versions step, or ``{}``."""
    for record in results:
        if record.get("title") != VERSIONS_TITLE or not record.get("ran"):
            continue
        out = {}
        for line in str(record.get("stdout") or "").splitlines():
            label, _, value = line.partition(":")
            key = VERSION_LABELS.get(label.strip())
            if key and value.strip():
                out[key] = value.strip()
        return out
    return {}


def _tend_version_step(host: dict, tend: str) -> str:
    """The preflight fragment that checks -- or, unrecorded, only prints -- tend's version."""
    required = str(host.get("tend_version_required") or "")
    probe = f"{tend} {TEND_VERSION_PROBE}"
    if not required:
        return (f"{probe}; "
                "echo 'tend version NOT checked: record hosts."
                f"{host.get('name', '<host>')}.tend_version (e.g. \">=0.4,<0.6\") in "
                "workspace.yaml and cf run will refuse a mismatch'")
    # grouped: a pipeline binds tighter than the probe's own `||`, so ungrouped
    # the check read the probe's output only when the probe FAILED, so a host with tend
    # installed was never checked at all
    return ("{ " + probe + "; } 2>&1 | tail -1 | python3 -c "
            + shlex.quote(TEND_VERSION_CHECK) + " " + shlex.quote(required))


#: Check every source layer a locate run's gate reads, on the run host, against
#: the host's own ``gate.json`` (argv[1]) -- the file that gate reads -- once
#: that file is shown to be the one the transfer sent (argv[2], its sha256;
#: empty skips that check).  It reads gate.json there rather than taking the
#: pins as arguments: one quoted ``<sha>  <path>`` line per source made one ssh
#: argument of about 136 bytes a source, over Linux's 128 KiB cap on a single
#: argument at about 960 sources, and an article-tier shard holds up to 4,000
#: (8 sources a task, 500 tasks a root).  Beside each layer it opens the two tables the
#: gate's ``SourceView`` reads there and fails closed without (``formulas.json`` and
#: ``paragraphs.json``): each must be a JSON list of objects and, when the source's pin
#: carries ``views`` (a run planned since the gate pins them), the sha256 the pin
#: names.  Whether the formulas fit the layer, and the gate's stricter JSON reader,
#: stay the gate's own checks.  A failure is printed as ``sha256sum -c`` prints one
#: (``<path>: FAILED``, ``FAILED open or read``), on stdout, which run.yaml keeps;
#: argv[3] is how many failure lines are named before the rest are counted.
LAYER_CHECK_PY = r'''
import hashlib, json, sys

config, want, named = sys.argv[1], sys.argv[2], int(sys.argv[3])
#: the tables the gate's SourceView opens beside each layer.txt (gate/verify_locate.py)
TABLES = ("formulas.json", "paragraphs.json")


def refuse(why):
    sys.stderr.write("REFUSED: %s\n" % why)
    raise SystemExit(1)


try:
    with open(config, "rb") as fh:
        raw = fh.read()
except OSError as exc:
    refuse("cannot read %s on the host (%s): the gate reads its pins there" % (config, exc))
if want and hashlib.sha256(raw).hexdigest() != want:
    refuse("%s on the host is not the gate.json this transfer sent; send the run again" % config)
try:
    gate = json.loads(raw.decode("utf-8"))
except ValueError as exc:
    refuse("%s on the host is not JSON (%s)" % (config, exc))
sourceset = gate.get("sourceset") if isinstance(gate.get("sourceset"), dict) else {}
sources_dir = str(sourceset.get("sources_dir") or "").rstrip("/")
sources = gate.get("sources") if isinstance(gate.get("sources"), dict) else {}
if not sources_dir:
    refuse("gate.json has no sourceset.sources_dir; the gate fails closed without one")
if not sources:
    refuse("gate.json pins no source layer; the gate fails closed without one")


def layer_failure(path, pin):
    """``sha256sum -c``'s failure line for ``path`` against ``pin``, or None."""
    if not pin:
        return "%s: FAILED (gate.json pins no layer_sha256)" % path
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return "%s: FAILED open or read" % path
    return None if digest.hexdigest() == pin else "%s: FAILED" % path


def table_failure(path, pin=None):
    """The failure line for a table the gate opens beside the layer, or None."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        data = json.loads(raw.decode("utf-8"))
    except OSError:
        return "%s: FAILED open or read" % path
    except ValueError:
        return "%s: FAILED (not JSON)" % path
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        return "%s: FAILED (not a JSON list of objects)" % path
    if pin is not None and hashlib.sha256(raw).hexdigest() != pin:
        return "%s: FAILED" % path
    return None


lines, bad = [], 0
for sid in sorted(sources):
    pins = sources[sid] if isinstance(sources[sid], dict) else {}
    base = "%s/%s/" % (sources_dir, sid)
    failed = [layer_failure(base + "layer.txt", str(pins.get("layer_sha256") or ""))]
    views = pins.get("views") if isinstance(pins.get("views"), dict) else None
    failed += [table_failure(base + name, None if views is None else str(views.get(name) or ""))
               for name in TABLES]
    failed = [line for line in failed if line]
    lines += failed
    bad += 1 if failed else 0
for line in lines[:named]:
    print(line)
if len(lines) > named:
    print("... and %d more" % (len(lines) - named))
if bad:
    refuse("%d of %d source(s) of this run are not in %s on the host as the gate reads them "
           "(a layer.txt missing or not the rendering gate.json pins, or a %s missing, "
           "unreadable or not its pinned view; the lines above name them); send them with "
           "`cf run --include-source-text`" % (bad, len(sources), sources_dir, " or ".join(TABLES)))
print("source layers: %d pinned sha256 verified, each with its %s"
      % (len(sources), " and ".join(TABLES)))
'''

#: how many failure lines the verify step names before it counts the rest
LAYERS_NAMED = 50


def layer_check(gate_config: str, want_sha256: str = "") -> str:
    """The verify command that checks each source layer of a locate run on the host.

    The gate reads ``<sources_dir>/<id>/layer.txt`` and fails closed (exit 3)
    when it is missing or is not gate.json's ``layer_sha256``, and so it does
    when the ``formulas.json`` or ``paragraphs.json`` beside it is missing,
    unreadable or not the pin's ``views`` sha; a run whose layers were neither sent (``--include-source-text``)
    nor already on the host would pass the other checks and fail every task,
    after the agents' cost was spent.  So each one is checked here, from the host's
    ``gate_config`` -- refused unless
    its sha256 is ``want_sha256``, the gate.json the transfer sent
    (:func:`sent_gate_json_sha256`) -- and the failures are named.  The command
    is the same size for any number of sources (:data:`LAYER_CHECK_PY`).
    """
    return " ".join(["python3 -c", shlex.quote(LAYER_CHECK_PY), shlex.quote(gate_config),
                     shlex.quote(want_sha256 or ""), str(LAYERS_NAMED)])


def sent_gate_json_sha256(entries: Iterable[dict], gate_dir: str) -> str:
    """The sha256 of the gate.json this transfer sends into ``gate_dir``; ``""`` for none.

    The staged ``gate/`` directory's when the root has one (a locate plan), else
    the run directory's own copy: the layer check refuses a host gate.json that
    is neither, so the pins it trusts are the ones this invocation sent.
    """
    gate_dir = gate_dir.rstrip("/")
    for entry in entries:
        dest = str(entry.get("dest") or "").rstrip("/")
        if entry.get("kind") == "dir" and dest == gate_dir:
            src = Path(entry["src"]) / "gate.json"
        elif entry.get("kind") == "file" and dest == f"{gate_dir}/gate.json":
            src = Path(entry["src"])
        else:
            continue
        return hashing.sha256_file(src) if entry.get("exists") and src.is_file() else ""
    return ""


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


#: seconds between the watchdog's checks on a run host (its own default; the campaign
#: sequencer's ``CF_WATCH_INTERVAL`` is the same number)
WATCHDOG_INTERVAL = 300


def watchdog_log(remote_root: str, run_id: str) -> str:
    """Where the watchdog's own output goes: beside the run root, one file per run."""
    return f"{remote_root.rstrip('/')}/watchdog-{run_id}.log"


def watchdog_steps(host: dict, *, run_id: str, tend_root: str, entrypoint: str,
                   max_cost: float) -> list[dict]:
    """Stop any watchdog left on this root, clear what it left, then start one (detached).

    ``scripts/run/watchdog.py``, which `cf plan-informalize` stages in the root's
    ``bin/``: it sums the agents' usage envelopes and runs ``tend-control drain`` when
    the spend passes ``max_cost``, with no pilot thresholds, so its gate-failure rate and
    its merged-task count decide nothing (docs/proposals/informalize.md section 8.1).
    ``</dev/null`` with both streams redirected, as the ``tend run`` step has it:
    otherwise ssh waits on the detached process's file descriptors and never returns.

    ``watchdog.stop`` and ``watchdog.json`` go with the old process, as the campaign
    sequencer removes them before each shard: the watchdog's loop leaves on the stop
    file, so one left by a launch that was stopped by hand would make the new watchdog
    write a single record and exit -- an unwatched fleet with no ceiling at all -- and a
    record left from before would be what ``cf run --status`` reports as the spend now.
    Nothing else removes them: the tend-root rsync has no ``--delete``.
    """
    script = f"{tend_root}/bin/watchdog.py"
    log_path = watchdog_log(host["remote_root"], run_id)
    command = (f"python3 {shlex.quote(script)} --root {shlex.quote(tend_root)} "
               f"--entrypoint {shlex.quote(entrypoint)} --max-cost {max_cost} "
               f"--interval {WATCHDOG_INTERVAL} --tend {shlex.quote(host['tend_cmd'])}")
    left = " ".join(shlex.quote(f"{tend_root}/{name}")
                    for name in ("watchdog.stop", "watchdog.json"))
    return [
        {"stage": "smoke", "safe": False,
         "title": "stop any spend watchdog left on this root by an earlier launch, and "
                  "remove its stop file and last record (a stop file left there stops the "
                  "new watchdog at its first check)",
         **_ssh(host, f"set -e; python3 {shlex.quote(script)} --root {shlex.quote(tend_root)} "
                      f"--stop-others; rm -f {left}")},
        {"stage": "smoke", "safe": False, "record_output": True,
         "title": f"start the spend watchdog (drains the root at {max_cost} USD; its log is "
                  f"{log_path})",
         **_ssh(host, f"nohup sh -c {shlex.quote(command)} </dev/null "
                      f">> {shlex.quote(log_path)} 2>&1 & echo started")},
    ]


def watchdog_stop_step(host: dict, *, tend_root: str) -> dict:
    """The 'stop' stage's step: stop the spend watchdog this run's smoke stage started.

    It is started without ``--parent``, so it polls until the root drains or its stop
    file appears; a phase that is paused or finished by hand would otherwise leave it
    on the host, one per phase (docs/proposals/informalize.md section 8.1).
    """
    script = f"{tend_root}/bin/watchdog.py"
    return {"stage": "stop", "safe": False,
            "title": "stop this phase's spend watchdog (it polls on until the root drains)",
            **_ssh(host, f"python3 {shlex.quote(script)} --root {shlex.quote(tend_root)} "
                         "--stop-others")}


def plan_commands(*, host: dict, run_id: str, run: dict, entries: list[dict],
                  names: dict, budget: dict, concurrency: dict,
                  force: bool = False, locate: dict | None = None,
                  informalize: dict | None = None) -> list[dict]:
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
    add("preflight", VERSIONS_TITLE, _ssh(host, _versions_step(tend, str(run.get("bridge") or ""))),
        safe=True, record_output=True)

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

    # An entry that locks its directories (``lock_dirs``, outermost first) creates
    # them inside its own open/close bracket: an earlier transfer left them
    # read-only, so a `mkdir -p` here, before the bracket opens them, fails on a
    # kind directory inside a closed per-universe one.  And an entry
    # whose rsync creates its own directory (``rsync_creates_dest``: rsync makes
    # the last component of a destination) needs only its parent here: a locate
    # run's source layers are one directory each, and naming them all made this
    # one ssh argument too long for Linux at about 2,800 sources.
    dirs = sorted({str(Path(e["lock_dirs"][0]).parent) if e.get("lock_dirs")
                   else str(Path(e["dest"].rstrip("/")).parent)
                   if e["kind"] == "file" or e.get("rsync_creates_dest")
                   else e["dest"].rstrip("/") for e in entries})
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
        locked = [shlex.quote(str(d).rstrip("/")) for d in entry.get("lock_dirs") or []]
        if locked:
            add("transfer", f"open {entry['dest']} for the files of this run (creating it, "
                            f"and opening every locked directory around it)",
                _ssh(host, f"set -e; for d in {' '.join(locked)}; do "
                           "test -d \"$d\" || mkdir \"$d\"; chmod u+w \"$d\"; done"))
        add("transfer", f"rsync {entry['name']} -> {entry['dest']}", _rsync(entry, host))
        if locked:
            add("transfer", f"close {entry['dest']} again (read-only, owner-reversible)",
                _ssh(host, f"chmod a-w {' '.join(locked)}"))
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
    gate_script = ("verify_informal.py" if informalize is not None else
                   "verify_locate.py" if locate is not None else "verify_findings.py")
    gate_dir = f"{root}/{names['gate']}"
    if informalize is not None:
        # The same reason as the locate gate below, and two more of its own: the
        # gate builds the document with its own copy of the runtime, so a TeX
        # installation without a package of `tex_packages` fails every task that
        # builds, and the workers read the library's source at a path only this
        # transfer puts there (docs/proposals/informalize.md section 8.3).
        add("verify", "the informalize gate runs out of tree from its own directory (python3 "
                      "-I --self-check: modules, the pinned sha256s, the task manifests, the "
                      "library's source and, in a phase that builds, pdflatex, latexmk and "
                      "every package of tex_packages)",
            _ssh(host, f"python3 -I {shlex.quote(gate_dir + '/' + gate_script)} --gate-config "
                       f"{shlex.quote(gate_dir + '/gate.json')} --self-check"),
            safe=True, record_output=True)
    elif locate is not None:
        # Not just present: the locate gate must RUN from its own directory, with
        # nothing else on the path (-I).  A gate directory staged without one of
        # its modules passed a `test -f` and then failed every worker's first
        # validation (field defect P2); --self-check imports everything, checks
        # the vendored pins, the task manifests, the sources and corpus.sqlite,
        # and exits 3 on anything missing, before any agent starts.
        add("verify", "the locate gate runs out of tree from its own directory (python3 -I "
                      "--self-check: modules, vendored pins, manifests, sources, corpus)",
            _ssh(host, f"python3 -I {shlex.quote(gate_dir + '/' + gate_script)} --gate-config "
                       f"{shlex.quote(gate_dir + '/gate.json')} --self-check"),
            safe=True, record_output=True)
    else:
        add("verify", "gate runs out of tree against one staged finding (fails closed)",
            _ssh(host, f"test -f {shlex.quote(gate_dir + '/' + gate_script)} "
                       f"&& echo 'gate present (out of tree)'"), safe=True)
    corpus = (locate or {}).get("corpus_db") or {}
    if corpus.get("path") and corpus.get("sha256"):
        # Verified here, before any agent starts, so a wrong database stops the run at
        # verify rather than at every task's first gate call; the gate itself also
        # compares it when it first opens it (mode=ro&immutable=1), since a gate run
        # anywhere else -- a contributor's machine, a harvest -- has no verify stage.
        line = shlex.quote(f"{corpus['sha256']}  {corpus['path']}")
        add("verify", "corpus.sqlite is the pinned database (sha256, once per run)",
            _ssh(host, f"echo {line} | sha256sum -c --status - && echo 'corpus.sqlite: pinned "
                       f"sha256 verified' || {{ echo 'REFUSED: corpus.sqlite on the host is not "
                       f"the database gate.json pins' >&2; exit 1; }}"),
            safe=True, record_output=True, expected=corpus["sha256"])
    if locate is not None:
        sent = sent_gate_json_sha256(entries, gate_dir)
        add("verify", "every source layer this run reads is on the host as pinned "
                      "(sha256 of each layer.txt, read from the host's gate.json, and the "
                      "formulas.json and paragraphs.json beside it)",
            _ssh(host, layer_check(f"{gate_dir}/gate.json", sent)),
            safe=True, record_output=True, **({"expected": sent} if sent else {}))
    tools = (locate or {}).get("tools_corpus_db") or {}
    if isinstance(tools, dict) and tools.get("path") and tools.get("sha256") \
            and tools["path"] != corpus.get("path"):
        # the blind or planted copy the worker's search tools read (gate.json
        # tools_corpus_db): pinned the same way, so a worker searches the
        # database the run recorded
        line = shlex.quote(f"{tools['sha256']}  {tools['path']}")
        add("verify", f"the {tools.get('kind') or 'tools'} database the search tools read is the "
                      f"pinned one (sha256)",
            _ssh(host, f"echo {line} | sha256sum -c --status - && echo 'tools database: pinned "
                       f"sha256 verified' || {{ echo 'REFUSED: the tools database on the host is "
                       f"not the one gate.json pins' >&2; exit 1; }}"),
            safe=True, record_output=True, expected=tools["sha256"])
    # BEFORE: the companion trees as they landed, hashed on the host. Recorded in
    # run.yaml (run.executed) with the digest they were sent with.
    for entry in entries:
        if not entry.get("tree_sha256"):
            continue
        add("verify", f"{entry['name']} landed byte for byte (read-only transfer, "
                      f"owner-reversible)",
            _tree_check(host, entry), safe=True, record_output=True,
            expected=entry["tree_sha256"])

    if informalize is not None and budget.get("max_cost") is not None:
        # The spend cap is enforced here, not by tend: with agentbox tend's own budget
        # counts no agent's spend (manifest.check_budget_cap), so an informalize phase's
        # ceiling is the watchdog the planner staged in the root's bin/.  It starts
        # BEFORE `tend run`, so the first task is already watched, and with no pilot
        # threshold: the cap is the whole rule here (section 8.1).  Two of them on one
        # root would each drain it with its own figures, so a watchdog an earlier
        # `--stage smoke` left is stopped first, as the campaign sequencer does.
        steps.extend(watchdog_steps(host, run_id=run_id, tend_root=tend_root,
                                    entrypoint=entrypoint, max_cost=budget["max_cost"]))

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
        _ssh(host, f"find {shlex.quote(entrypoint + '/findings')} -type f -name '*.json' "
                   f"| wc -l"), safe=True)
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
    if informalize is not None and budget.get("max_cost") is not None:
        steps.append(watchdog_stop_step(host, tend_root=tend_root))
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
