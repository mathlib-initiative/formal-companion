"""Plan what `cf run` sends to the server, and refuse what it must not send.

New code.  The original was a hand-run sequence of rsyncs recorded in
scratchpad/ffmain/tao-diff-run/RUNBOOK.md ("Start it" / "Steer it") and the five
server roots ~/tao-{diff,attack}-* whose only differences were the entrypoint
path, the bridge path and the concurrency limits.

Three controls the original had no place for (design/critique.json, risks):

* **host allowlist** -- `cf run` automates rsync of private Lean repos and
  copyrighted source text to a machine the operator does not own, so the host
  must be named in ``workspace.yaml: hosts.allowlist`` or the command refuses;
* **consent** -- ``--i-have-consent`` is recorded in run.yaml with the operator
  and timestamp, so a run can always be traced to a person who agreed to it;
* **out-of-tree gate** -- the gate used to live at ``scripts/verify_findings.py``
  INSIDE the entrypoint the worker may edit and commit, so "every merged file
  passed the gate twice" proved nothing.  The gate is transferred to its own
  directory beside the entrypoint, never into it.

Companion repos and source text are excluded unless ``--include-sources`` /
``--include-source-text`` are given, and both facts are recorded in run.yaml.

A run transfers ONLY the sources it needs: ``source_shas(workspace, run)`` reads
``run.yaml``'s ``sources`` list, so a workspace holding two editions of one work
does not ship the edition this run never reads.  The companion repos go to
``<root>/repos/<name>/`` and the source texts to ``<root>/sources/<source_id>/``
-- two directories, because one ``sources/`` holding both would let a companion
named after a source overwrite it.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ._compat import CfError, human_bytes

#: rsync flags used for every component (archive, no device/special files).
BASE_RSYNC = ["-a", "--no-D", "--omit-dir-times"]
#: read-only transfer for companion sources: the run must not be able to edit them.
#: (design/critique.json: "'sources read-only' is prompt-enforced only".)
READONLY_CHMOD = ["--chmod=D0555,F0444"]
#: the opt-in that lets a run's agents run with approvals and the sandbox off
#: (scripts/run/bridge-agentbox.sh); absent, both hosts deny by default.
BYPASS_ENV = "CF_ALLOW_SANDBOX_BYPASS"
#: the only files of scripts/run/ that belong on the server
BRIDGE_FILES = ("bridge-agentbox.sh", "bridge-claude-p.sh", "bridge-tend-agent.sh",
                "normalize_result.py", "extract_final_json.py")
#: never ship build trees, git objects or editor droppings to the server.
SOURCE_EXCLUDES = [".git", ".lake", "build", ".DS_Store", "__pycache__", "*.olean"]
#: a tend worktree per ready task: 382 worktrees were 1.1 GB in 11 minutes.
DEFAULT_DISK_BUDGET_BYTES = 20 * 1024 ** 3


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def resolve_host(workspace, host: str) -> dict:
    """Resolve *host* against ``workspace.yaml: hosts`` or raise.

    ``Workspace.check_host`` is the enforcement point (it refuses anything not in
    ``hosts.allowlist``); this adds the connection facts the commands need:
    ``{name, ssh, remote_root, tend_cmd, agentbox_profile, disk_budget_bytes,
    tend_version_required}``.

    ``tend_version_required`` is a comma-separated version spec (``">=0.4,<0.6"``)
    from ``hosts.<host>.tend_version``, ``hosts.tend_version`` or
    ``defaults.tend_version``.  The whole of ``scripts/plan`` + ``scripts/run``
    exists to drive one external tool whose config schema nothing pins, so
    preflight compares the host's ``tend --version`` against this and refuses;
    with nothing recorded it can only print the version, and says so.
    """
    workspace.check_host(host)
    hosts = _as_dict(workspace.hosts)
    entry = _as_dict(hosts.get(host)) or _as_dict(_as_dict(hosts.get("entries")).get(host))
    ssh = entry.get("ssh") or entry.get("target") or host
    remote_root = str(entry.get("remote_root") or entry.get("root") or hosts.get("remote_root")
                      or f"/home/{os.environ.get('USER', 'jack')}").rstrip("/")
    budget_gb = workspace.default("worktree_budget_gb", None)
    return {
        "name": host,
        "ssh": str(ssh),
        "remote_root": remote_root,
        "tend_cmd": str(entry.get("tend_cmd") or hosts.get("tend_cmd")
                        or f"cd {remote_root}/tend && uv run"),
        "agentbox_profile": str(entry.get("agentbox_profile") or entry.get("profile")
                                or hosts.get("agentbox_profile") or ""),
        "disk_budget_bytes": int(entry.get("disk_budget_bytes")
                                 or (float(budget_gb) * 1024 ** 3 if budget_gb else 0)
                                 or DEFAULT_DISK_BUDGET_BYTES),
        "tend_version_required": str(entry.get("tend_version")
                                     or hosts.get("tend_version")
                                     or workspace.default("tend_version", "") or ""),
    }


def remote_names(run: dict, run_id: str) -> dict:
    """Remote directory names for this run (entrypoint, tend root, gate)."""
    remote = _as_dict(run.get("remote"))
    entry = remote.get("entrypoint") or run.get("entrypoint") or run_id
    tend_root = remote.get("tend_root") or run.get("tend_root") or f"{run_id}-tend"
    if isinstance(entry, dict):
        entry = entry.get("name") or run_id
    if isinstance(tend_root, dict):
        tend_root = tend_root.get("name") or f"{run_id}-tend"
    return {
        "entrypoint": str(entry),
        "tend_root": str(tend_root),
        "gate": str(remote.get("gate") or f"{run_id}-gate"),
        # Two distinct roots on purpose: `sources/<source_id>/` is COPYRIGHTED
        # source text, `repos/<companion>/` is the read-only Lean. One shared
        # directory would let a companion whose name matched a source_id land on
        # top of the text every quote is checked against.
        "sources": str(remote.get("sources") or "sources"),
        "repos": str(remote.get("repos") or "repos"),
    }


def _exclude_patterns(flags: list[str] | None) -> list[str]:
    """The ``--exclude=<pat>`` patterns out of a flag list."""
    out = []
    for flag in flags or []:
        if flag.startswith("--exclude="):
            out.append(flag[len("--exclude="):])
    return out


def _excluded(rel: Path, patterns: list[str]) -> bool:
    """rsync-ish match: a pattern hits any path component, with globbing."""
    if not patterns:
        return False
    parts = rel.parts
    for pat in patterns:
        if any(fnmatch.fnmatch(part, pat) for part in parts):
            return True
        if fnmatch.fnmatch(rel.as_posix(), pat):
            return True
    return False


def tree_digest(root: Path, excludes: list[str] | None = None) -> dict:
    """``{sha256, files, bytes}`` over exactly the files rsync would send.

    ``READONLY_CHMOD`` transfers a companion at mode 0444 inside 0555
    directories, which is a speed bump and not a barrier: the copy is owned by
    the run's user, so an agent running with approvals bypassed can ``chmod u+w``
    and edit it, and nothing else would ever notice.  This digest is walked once,
    pinned in run.yaml (``run.transfer[].tree_sha256``, see
    :func:`transfer_record`) and recomputed on the host
    (``REMOTE_TREE_DIGEST_PY``) after the transfer and after the run, so an edit
    is visible in the run's own record instead of being discovered in the
    findings.  It is PINNED rather than re-walked because the monitor stage runs
    in a later `cf run` invocation: comparing the host against whatever the local
    checkout hashes to by then would accuse an agent of editing a tree the
    operator had merely pulled.

    One sha256 over ``<relative posix path>\\0<file sha256>\\n`` lines sorted by
    path.  Symlinks are skipped: ``rsync -a`` ships the link, not its target.
    """
    root = Path(root)
    patterns = list(excludes or [])
    rows: list[tuple[str, str]] = []
    total = 0
    for dirpath, dirs, names in os.walk(root):
        rel_root = Path(dirpath).relative_to(root)
        dirs[:] = [d for d in dirs if not _excluded(rel_root / d, patterns)]
        for name in names:
            rel = rel_root / name
            if _excluded(rel, patterns):
                continue
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            inner = hashlib.sha256()
            try:
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        inner.update(chunk)
                        total += len(chunk)
            except OSError:
                rows.append((rel.as_posix(), "unreadable"))
                continue
            rows.append((rel.as_posix(), inner.hexdigest()))
    outer = hashlib.sha256()
    for rel_posix, digest in sorted(rows):
        outer.update(("%s\0%s\n" % (rel_posix, digest)).encode("utf-8"))
    return {"sha256": outer.hexdigest(), "files": len(rows), "bytes": total}


#: :func:`tree_digest`, for python3 on the run host (no cflib there).  The two
#: must agree byte for byte -- tests/test_rf_security.py runs this snippet against
#: the function on the same tree, which is the enforcement.
REMOTE_TREE_DIGEST_PY = r'''
import fnmatch, hashlib, os, sys

root, want = sys.argv[1], sys.argv[2]
patterns = sys.argv[3:]


def excluded(rel):
    for pat in patterns:
        if any(fnmatch.fnmatch(part, pat) for part in rel.split("/")):
            return True
        if fnmatch.fnmatch(rel, pat):
            return True
    return False


rows = []
for dirpath, dirs, names in os.walk(root):
    rel_root = os.path.relpath(dirpath, root)
    prefix = "" if rel_root == "." else rel_root + "/"
    dirs[:] = [d for d in dirs if not excluded(prefix + d)]
    for name in names:
        rel = prefix + name
        if excluded(rel):
            continue
        path = os.path.join(dirpath, name)
        if os.path.islink(path):
            continue
        inner = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    inner.update(chunk)
        except OSError:
            rows.append((rel, "unreadable"))
            continue
        rows.append((rel, inner.hexdigest()))
outer = hashlib.sha256()
for rel, digest in sorted(rows):
    outer.update(("%s\0%s\n" % (rel, digest)).encode("utf-8"))
got = outer.hexdigest()
print("tree %s files=%d sha256=%s" % (root, len(rows), got))
if want and want != got:
    sys.stderr.write("REFUSED: %s is not the tree that was transferred\n" % root)
    sys.stderr.write("  expected %s\n  got      %s\n" % (want, got))
    sys.stderr.write("  the companion copies go over read-only, but 0444 is reversible by their\n")
    sys.stderr.write("  owner: an agent may have edited the repo the findings cite. Re-transfer\n")
    sys.stderr.write("  and do not harvest this run.\n")
    raise SystemExit(1)
'''


def _measure(src: Path, patterns: list[str]) -> tuple[int, int]:
    """``(files, bytes)`` of *src*, skipping excluded directories and files."""
    files = 0
    total = 0
    for root, dirs, names in os.walk(src):
        rel_root = Path(root).relative_to(src)
        dirs[:] = [d for d in dirs if not _excluded(rel_root / d, patterns)]
        for name in names:
            rel = rel_root / name
            if _excluded(rel, patterns):
                continue
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
            files += 1
    return files, total


def _entry(name: str, src, dest: str, *, required: bool = True,
           flags: list[str] | None = None, note: str = "") -> dict:
    """One manifest row, with its measured size so the plan can be budgeted.

    ``src`` is a directory, a single file, or a list of files (the bridges are
    a handful of named files out of a package directory, and counting the whole
    directory would misreport what is actually sent).
    """
    if isinstance(src, (list, tuple)):
        srcs = [Path(s) for s in src]
        present = [s for s in srcs if s.exists()]
        return {
            "name": name, "src": str(srcs[0].parent if srcs else ""),
            "srcs": [str(s) for s in srcs], "dest": dest, "kind": "files",
            "exists": bool(present) and len(present) == len(srcs),
            "required": required, "files": len(present),
            "bytes": sum(s.stat().st_size for s in present),
            "rsync_flags": list(flags or []), "note": note,
            "missing_files": [str(s) for s in srcs if not s.exists()],
        }
    src = Path(src)
    is_dir = src.is_dir()
    if is_dir:
        # Measure what rsync will actually SEND: the same --exclude patterns are
        # assembled in `flags` and were never applied to the walk, so the
        # transfer table an operator reads before consenting overstated the two
        # companion repos by 30.3 GB / 399,704 files against 100.3 MB / 5,842.
        files, size = _measure(src, _exclude_patterns(flags))
    else:
        files = 1 if src.exists() else 0
        size = src.stat().st_size if src.exists() else 0
    return {
        "name": name,
        "src": str(src),
        "srcs": [str(src)],
        "dest": dest,
        "kind": "dir" if is_dir else "file",
        "exists": src.exists(),
        "required": required,
        "files": files,
        "bytes": size,
        "rsync_flags": list(flags or []),
        "note": note,
    }


def build_manifest(*, workspace_dir: Path, skill_dir: Path, run_id: str, run: dict,
                   host: dict, include_sources: bool = False, include_texts: bool = False,
                   companions: dict[str, str] | None = None) -> tuple[list[dict], list[str]]:
    """Build the rsync manifest for one run.

    The pieces are what ``cf plan-run`` staged under ``runs/<run-id>/``:
    ``tasks/`` and ``data/`` are the entrypoint repo, ``tend-root/`` is the
    orchestration root (config.yaml, .tend/, bin/ wrappers, prompts/), and
    ``tend-root/gate/`` is the gate -- which goes to its OWN remote directory,
    never inside the entrypoint the worker commits to.

    ``companions`` maps companion name -> repo path; it is only consulted when
    ``include_sources`` is set.  ``include_texts`` ships the COPYRIGHTED text of
    every source ``run.yaml`` names -- and only those: a workspace holding two
    editions of one work sends the one this run reads, never both.  Returns
    (entries, warnings); a missing REQUIRED component is a warning here and an
    error in ``cf run --execute``, so the dry run can still show the whole plan.
    """
    names = remote_names(run, run_id)
    root = host["remote_root"]
    rd = workspace_dir / "runs" / run_id
    tend_root = rd / "tend-root"
    entries: list[dict] = []
    warnings: list[str] = []

    entries.append(_entry("tasks", rd / "tasks", f"{root}/{names['entrypoint']}/tasks/",
                          note="tend task YAML, one per item"))
    if (rd / "data").exists():
        entries.append(_entry("data", rd / "data", f"{root}/{names['entrypoint']}/data/",
                              note="statement tables the workers read"))
    else:
        warnings.append(f"runs/{run_id}/data/ is absent: the workers will have no "
                        "statements.jsonl/pairs.jsonl to read")

    # The tend root, minus its gate/ copy (which belongs out of tree, below).
    entries.append(_entry("tend-root", tend_root, f"{root}/{names['tend_root']}/",
                          flags=["--exclude=gate/"],
                          note="config.yaml, .tend/*.yaml, bin/ wrappers, prompts/"))

    if not (tend_root / "prompts").is_dir():
        entries.append(_entry("prompts", rd / "prompts",
                              f"{root}/{names['tend_root']}/prompts/",
                              note="rendered worker/reviewer prompts"))

    # THE GATE GOES OUTSIDE THE ENTRYPOINT.  A worker holds Write and
    # Bash(git:*) in its worktree, so a gate committed inside that tree can be
    # edited and then passes both the validation and the pre-merge run --
    # "every merged file passed the gate twice" would prove nothing.
    gate_dir = tend_root / "gate"
    if gate_dir.is_dir():
        entries.append(_entry("gate", gate_dir, f"{root}/{names['gate']}/",
                              note="OUT OF TREE: verify_findings.py, check_changed_files.py, gate.json"))
    else:
        entries.append(_entry("gate", skill_dir / "scripts" / "gate" / "verify_findings.py",
                              f"{root}/{names['gate']}/verify_findings.py",
                              note="OUT OF TREE: the worktree must never contain the gate"))
        entries.append(_entry("gate-config", rd / "gate.json", f"{root}/{names['gate']}/gate.json",
                              note="kinds/verdicts/witness/sources for this run's mode"))

    if not _bridges_staged(tend_root):
        entries.append(_entry("bridges", [skill_dir / "scripts" / "run" / n for n in BRIDGE_FILES],
                              f"{root}/{names['tend_root']}/bin/",
                              note="agent bridges + the StrictModel coercion"))

    if include_sources:
        pinned = recorded_tree_digests(run)
        for name, repo in sorted((companions or {}).items()):
            entry = _entry(f"repo:{name}", Path(repo).expanduser(),
                           f"{root}/{names['repos']}/{name}/",
                           flags=READONLY_CHMOD + [f"--exclude={p}" for p in SOURCE_EXCLUDES],
                           note="companion Lean sources, transferred read-only")
            if entry["exists"]:
                # The BEFORE hash of what leaves this machine, so the same digest
                # can be demanded of the host after the transfer and after the run.
                # run.yaml's record wins: the first `cf run` walks the tree, every
                # later invocation (the monitor stage is one) compares the host
                # against the digest that was actually SENT, not against a local
                # checkout that may have been pulled or switched since.
                entry["tree_sha256"] = (pinned.get(entry["name"])
                                        or tree_digest(Path(repo).expanduser(),
                                                       SOURCE_EXCLUDES)["sha256"])
            entries.append(entry)
        if not companions:
            warnings.append("--include-sources was given but workspace.yaml lists no companion repos")
    wants_images = "image" in source_access(run).lower()
    if include_texts:
        source_ids = run_source_ids(run)
        if not source_ids:
            warnings.append("--include-source-text was given but run.yaml names no sources")
        for source_id in source_ids:
            sdir = workspace_dir / "sources" / source_id
            dest = f"{root}/{names['sources']}/{source_id}"
            entries.append(_entry(f"source-text:{source_id}", sdir / "text.txt",
                                  f"{dest}/text.txt",
                                  note="COPYRIGHTED: leaves this machine only with "
                                       "--include-source-text"))
            if (sdir / "errata.md").exists():
                if _errata_is_repo_supplied(sdir):
                    # The URL came out of the companion repo, and nobody has
                    # confirmed it. The page is handed to workers as authoritative
                    # errata, so an unconfirmed one does not leave this machine.
                    warnings.append(
                        "errata for %s came from the companion repo and is unconfirmed: not "
                        "transferred (`cf sources set %s errata_url=<url>` to confirm it)"
                        % (source_id, source_id))
                else:
                    entries.append(_entry(f"source-errata:{source_id}", sdir / "errata.md",
                                          f"{dest}/errata.md",
                                          note="live errata, as fetched (never a summary)"))
            if wants_images:
                entries.append(_entry(f"source-images:{source_id}", sdir / "images",
                                      f"{dest}/images/",
                                      note="COPYRIGHTED page images (the hunt run reads notation "
                                           "pdftotext drops)"))
    elif wants_images:
        warnings.append("this run's source access asks for page images but "
                        "--include-source-text was not given: the workers will have no source "
                        "text on the host")

    for entry in entries:
        if entry["required"] and not entry["exists"]:
            warnings.append("missing %s: %s" % (entry["name"], entry["src"]))
    return entries, warnings


#: the keys of a manifest entry that belong in run.yaml's ``run.transfer`` record.
#: ``tree_sha256`` is one of them: the monitor-stage integrity check compares the
#: host against it, so it has to survive the invocation that sent the tree.
TRANSFER_RECORD_KEYS = ("name", "src", "dest", "files", "bytes", "exists", "tree_sha256")


def transfer_record(entries: list[dict]) -> list[dict]:
    """The ``run.transfer`` rows for run.yaml -- what was sent, and its digest."""
    return [{key: entry[key] for key in TRANSFER_RECORD_KEYS if key in entry}
            for entry in entries]


def recorded_tree_digests(run: dict) -> dict[str, str]:
    """``{entry name: tree_sha256}`` a previous `cf run` recorded in run.yaml."""
    out: dict[str, str] = {}
    for row in _as_dict(run.get("run")).get("transfer") or []:
        if isinstance(row, dict) and row.get("name") and row.get("tree_sha256"):
            out[str(row["name"])] = str(row["tree_sha256"])
    return out


def include_sources_for(run: dict, *, requested: bool, stage: str) -> tuple[bool, list[str]]:
    """Whether this invocation covers the companion repos, and what to say about it.

    ``stages_upto("monitor")`` is ``["monitor"]`` alone, so the monitor stage is a
    SEPARATE `cf run` invocation, and the companion-tree integrity check hangs off
    the ``repo:<name>`` entries.  Reading ``--include-sources`` from the flag alone
    meant that forgetting to repeat it silently removed the one check that catches
    a chmod-and-edit on the host, and then rewrote ``include_sources: false`` over
    the earlier true, dropping the pinned digests with it.

    So the RUN's own record decides: once run.yaml says this run transferred the
    companion repos, every later invocation covers them too and says why.
    """
    recorded = bool(_as_dict(run.get("run")).get("include_sources"))
    if requested or not recorded:
        return requested, []
    return True, ["run.yaml records include_sources: true for this run, so the companion repos "
                  "are in this %s plan although --include-sources was not repeated -- the "
                  "before/after tree digests are the check that a read-only copy was not made "
                  "writable and edited on the host" % stage]


def _errata_is_repo_supplied(source_dir: Path) -> bool:
    """Did the errata URL come from the audited repository itself?

    ``sources/<id>/errata.json`` records the provenance of the URL it fetched
    (``fetch_errata.fetch(provenance=…)``).  An older file without the key
    predates the provenance record and is treated as confirmed, since it could
    only have been written when every fetch was allowed.
    """
    meta_path = source_dir / "errata.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):                       # pragma: no cover - unreadable meta
        return False
    return bool(meta.get("repo_supplied"))


def agent_permissions(run_dir: Path) -> dict:
    """What the staged wrappers will hand the agent hosts, for run.yaml's record.

    ``bridge-agentbox.sh`` denies by default (a role allowlist, and ``codex``
    sandboxed) and takes the approvals-and-sandbox bypass as an explicit opt-in,
    read from the environment.  This reads the wrappers `cf plan-run` staged,
    because that is the only channel `cf run` can see.

    It is therefore a THREE-state record, not a boolean.  ``cf plan-run
    --allow-sandbox-bypass`` is what stages the variable, so a wrapper that
    exports it says the opt-in was asked for deliberately and recorded in
    run.yaml.  Its absence is still not ``false``: the wrappers are generated
    (`cf plan-run` writes "Regenerate rather than edit" into them), and an
    operator who exports ``CF_ALLOW_SANDBOX_BYPASS=1`` in the shell that launches
    tend on the host gets a bypassed run the wrappers say nothing about.  So the
    two states this can distinguish are "requested here" and "not requested here,
    and the host can still set it".  What the host does record either way is the
    bridge's own ``bypass=`` line in ``<tend-root>/agentbox-<role>.err``.
    """
    bindir = Path(run_dir) / "tend-root" / "bin"
    wrappers = sorted(p for p in bindir.glob("*.sh")) if bindir.is_dir() else []
    wanted = (BYPASS_ENV, "CF_ALLOWED_TOOLS", "CF_WORKER_TOOLS", "CF_REVIEWER_TOOLS",
              "CF_PERMISSION_FLAGS", "CF_CODEX_SANDBOX")
    found: dict[str, str] = {}
    bypass_from: list[str] = []
    for path in wrappers:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:                                 # pragma: no cover - unreadable wrapper
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("export "):
                continue
            assignment = line[len("export "):]
            key, _, value = assignment.partition("=")
            key = key.strip()
            if key not in wanted:
                continue
            value = value.strip().strip('"').strip("'")
            found[key] = value
            if key == BYPASS_ENV and value == "1":
                bypass_from.append(path.name)
    return {
        "permission_bypass": (f"requested: `cf plan-run --allow-sandbox-bypass` staged a "
                              f"wrapper that exports {BYPASS_ENV}=1"
                              if bypass_from else
                              "not requested in the staged wrapper (the environment that "
                              "launches tend on the host can still set it; the bridge logs "
                              "bypass= to agentbox-<role>.err)"),
        "permission_bypass_in_wrapper": bool(bypass_from),
        "bypass_env": BYPASS_ENV,
        "bypass_exported_by": bypass_from,
        "wrappers": [p.name for p in wrappers],
        "env": found,
        "default": "deny by default: role allowlist for claude, --sandbox for codex "
                   "(to opt out, re-plan with `cf plan-run --allow-sandbox-bypass`, which "
                   f"stages {BYPASS_ENV}=1 into the wrappers and records it here; the "
                   "bin/*.sh wrappers are generated per run, so a hand edit to them is "
                   "overwritten by the next plan)",
    }


def _bridges_staged(tend_root: Path) -> bool:
    """True when `cf plan-run` already copied a bridge into tend-root/bin/."""
    bindir = tend_root / "bin"
    return bindir.is_dir() and any((bindir / name).exists() for name in BRIDGE_FILES)


def check_planned_host(run: dict, host: str) -> None:
    """Refuse to send a run to a host it was not planned for.

    ``config.yaml`` and ``gate.json`` carry the RUN HOST's absolute paths
    (``<remote_root>/<run-id>-tend``, ``<remote_root>/<run-id>-gate``), so
    transferring them to a different host silently points tend and the gate at
    paths that do not exist there.
    """
    planned = _as_dict(run.get("host"))
    name = planned.get("name")
    if name and name != host:
        raise CfError(
            f"run.yaml was planned for host {name!r}, not {host!r}",
            hint="config.yaml and gate.json embed that host's absolute paths (a run planned "
                 "`--host local` points at this machine); re-plan with "
                 f"`cf plan-run --host {host} --run-id <id>`")


def planned_consent(run: dict) -> dict:
    """What `cf plan-run` recorded about consent (it asks for the same flag)."""
    return _as_dict(run.get("consent"))


def source_access(run: dict) -> str:
    """'text' or 'text+images' -- what the workers may read of the source."""
    access = _as_dict(run.get("source_access"))
    return str(access.get("access") if access else None
               or run.get("source_access")
               or _as_dict(run.get("book")).get("access")
               or run.get("book_access")
               or "text")


def task_count(workspace_dir: Path, run_id: str, run: dict) -> int:
    """Number of task files actually staged (falls back to run.yaml's count)."""
    tasks = workspace_dir / "runs" / run_id / "tasks"
    if tasks.is_dir():
        return sum(1 for p in tasks.iterdir() if p.suffix in (".yaml", ".yml"))
    value = run.get("tasks")
    if isinstance(value, dict):
        value = value.get("count")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def disk_budget(entries: list[dict], tasks: int, host: dict) -> dict:
    """Estimate the server disk a run needs, because tend makes a worktree per task.

    tend creates the worktree for every ready task immediately: 382 tasks became
    382 worktrees and 1.1 GB in 11 minutes with a single worker allowed
    (design/critique.json).  N companions x 763 tasks needs `depends_on` gating or
    ``--copy-dir --cow``; this is the number that tells the operator which.
    """
    entrypoint_bytes = sum(e["bytes"] for e in entries
                           if e["name"] in ("tasks", "data") or e["name"].startswith("entrypoint"))
    transfer_bytes = sum(e["bytes"] for e in entries)
    estimated = entrypoint_bytes * max(tasks, 0)
    budget = int(host.get("disk_budget_bytes") or DEFAULT_DISK_BUDGET_BYTES)
    out = {
        "tasks": tasks,
        "entrypoint_bytes": entrypoint_bytes,
        "transfer_bytes": transfer_bytes,
        "estimated_worktree_bytes": estimated,
        "budget_bytes": budget,
        "over_budget": estimated > budget,
        "human": f"{human_bytes(estimated)} of worktrees for {tasks} tasks "
                 f"(budget {human_bytes(budget)})",
    }
    if out["over_budget"]:
        out["advice"] = ("raise hosts.<host>.disk_budget_bytes, or run tend with --copy-dir --cow, "
                         "or gate the tasks with depends_on so they do not all become ready at once")
    return out


def check_budget_cap(workspace, run: dict, max_cost: float | None,
                     no_budget_cap: bool) -> dict:
    """Resolve the spend ceiling, or refuse to plan a run without one.

    design/critique.json: "No budget gate ... run.yaml carries a billing-path
    field but no max_cost".  With agentbox the only enforceable cap is
    gateway-side, so the cap recorded here is what the operator must also set at
    the gateway; tend's own --max-cost counts tend-agent sessions only.
    """
    run_budget = _as_dict(run.get("budget"))
    budget = _as_dict(getattr(workspace, "budget", {}))
    currency = str(run_budget.get("currency") or budget.get("currency") or "USD")
    value = max_cost
    if value is None:
        for source in (run_budget, budget):
            for key in ("max_cost", "max_cost_usd", "ceiling"):
                if source.get(key) is not None:
                    value = float(source[key])
                    break
            if value is not None:
                break
    if value is None and not no_budget_cap:
        raise CfError(
            "no spend ceiling: pass --max-cost N, set budget.max_cost in workspace.yaml, "
            "or acknowledge the risk with --no-budget-cap",
            hint="agentbox hides token usage from tend (aggregate usage: input=0), so an "
                 "uncapped run's only limit is the subscription session limit",
        )
    return {
        "max_cost": value,
        "currency": currency,
        "enforced_by": "tend-control budget (tend-agent sessions only); set the gateway cap too",
        "billing_path": str(run_budget.get("billing_path") or budget.get("billing_path")
                            or "unrecorded"),
        "acknowledged_uncapped": bool(value is None and no_budget_cap),
    }


def companion_repos(workspace, names: Any = None) -> dict[str, str]:
    """{companion name: repo path} from workspace.yaml (all, or just ``names``)."""
    wanted = {str(n) for n in names} if names is not None else None
    out: dict[str, str] = {}
    for name, ref in workspace.companions.items():
        if wanted is not None and name not in wanted:
            continue
        if ref.repo:
            out[str(name)] = str(ref.repo)
    return out


def run_source_ids(run: dict) -> list[str]:
    """The source ids one run reads, in ``run.yaml`` order.

    ``sources`` is a LIST of ids (or a map of id -> body); a run plans against
    one source today, but the manifest must not assume it, because the thing
    that decides what leaves this machine has to be the run's own record.
    """
    value = run.get("sources")
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, (list, tuple)):
        out = []
        for entry in value:
            out.append(str(entry.get("id") or entry.get("source_id") or "")
                       if isinstance(entry, dict) else str(entry))
        return [v for v in out if v]
    if run.get("source_id"):
        return [str(run["source_id"])]
    return []


def source_shas(workspace, run: dict) -> dict[str, str]:
    """``{source_id: sha256}`` for the sources this run reads (was ``book_sha``).

    run.yaml's own record wins, so a landed corpus is always checked against the
    edition the run was PLANNED with; the workspace fills a gap.  A source the
    workspace does not know is left out rather than guessed at -- the harvest
    audit then reports it as unexpected instead of silently accepting it.
    """
    declared = run.get("sources")
    out: dict[str, str] = {}
    if isinstance(declared, dict):
        for sid, body in declared.items():
            if isinstance(body, dict):
                sha = body.get("sha256") or body.get("sha") or body.get("source_sha")
            else:
                sha = body
            if sha:
                out[str(sid)] = str(sha)
    elif isinstance(declared, (list, tuple)):
        for entry in declared:
            if isinstance(entry, dict) and entry.get("id"):
                sha = entry.get("sha256") or entry.get("sha") or entry.get("source_sha")
                if sha:
                    out[str(entry["id"])] = str(sha)
    for sid in run_source_ids(run):
        if sid in out:
            continue
        try:
            ref = workspace.source(sid)
        except Exception:  # noqa: BLE001 - an unknown source is reported, not fatal here
            continue
        if ref.sha256:
            out[sid] = str(ref.sha256)
    return out


def gate_config_warnings(workspace_dir: Path, run_id: str, host: dict, run: dict) -> list[str]:
    """Check the staged tend config actually calls the OUT-OF-TREE gate.

    The defect this catches (design/critique.json, gaps): both
    ``validation_commands`` and ``pre_merge_validation_commands`` ran
    ``python3 scripts/verify_findings.py`` from the worktree the worker may edit
    and commit, so a worker that rewrote the gate passed both runs.  The staged
    config must instead invoke the gate from the transferred gate directory and
    must pass a changed-files check.
    """
    names = remote_names(run, run_id)
    gate_path = f"{host['remote_root']}/{names['gate']}/verify_findings.py"
    config = workspace_dir / "runs" / run_id / "tend-root" / "config.yaml"
    if not config.exists():
        return [f"runs/{run_id}/tend-root/config.yaml is missing; `cf plan-run` writes it"]
    text = config.read_text(encoding="utf-8", errors="replace")
    out: list[str] = []
    if gate_path not in text:
        out.append(
            "tend-root/config.yaml does not invoke the out-of-tree gate %s -- a worker with "
            "Write and Bash(git:*) can edit an in-tree gate and pass it twice" % gate_path)
    if "--changed-files" not in text:
        out.append(
            "tend-root/config.yaml has no --changed-files check: pre-merge must refuse a "
            "contribution that touches anything but findings/<id>.json and tasks/<id>.yaml")
    return out
