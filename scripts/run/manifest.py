"""Plan what `cf run` sends to the server, and refuse what it must not send.

New code.  The original was a hand-run sequence of rsyncs recorded in
scratchpad/ffmain/tao-diff-run/RUNBOOK.md ("Start it" / "Steer it") and the five
server roots ~/tao-{diff,attack}-* whose only differences were the entrypoint
path, the bridge path and the concurrency limits.

Three controls the original had no place for (design/critique.json, risks):

* **host allowlist** -- `cf run` automates rsync of private sources and
  copyrighted book text to a machine the operator does not own, so the host must
  be named in ``campaign.yaml: hosts.allowlist`` or the command refuses;
* **consent** -- ``--i-have-consent`` is recorded in run.yaml with the operator
  and timestamp, so a run can always be traced to a person who agreed to it;
* **out-of-tree gate** -- the gate used to live at ``scripts/verify_findings.py``
  INSIDE the entrypoint the worker may edit and commit, so "every merged file
  passed the gate twice" proved nothing.  The gate is transferred to its own
  directory beside the entrypoint, never into it.

Sources and the book are excluded unless ``--include-sources`` /
``--include-book`` are given, and both facts are recorded in run.yaml.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

from ._compat import CfError, human_bytes

#: rsync flags used for every component (archive, no device/special files).
BASE_RSYNC = ["-a", "--no-D", "--omit-dir-times"]
#: read-only transfer for companion sources: the run must not be able to edit them.
#: (design/critique.json: "'sources read-only' is prompt-enforced only".)
READONLY_CHMOD = ["--chmod=D0555,F0444"]
#: the only files of scripts/run/ that belong on the server
BRIDGE_FILES = ("bridge-agentbox.sh", "bridge-claude-p.sh", "bridge-tend-agent.sh",
                "normalize_result.py", "extract_final_json.py")
#: never ship build trees, git objects or editor droppings to the server.
SOURCE_EXCLUDES = [".git", ".lake", "build", ".DS_Store", "__pycache__", "*.olean"]
#: a tend worktree per ready task: 382 worktrees were 1.1 GB in 11 minutes.
DEFAULT_DISK_BUDGET_BYTES = 20 * 1024 ** 3


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def resolve_host(campaign, host: str) -> dict:
    """Resolve *host* against ``campaign.yaml: hosts`` or raise.

    ``Campaign.check_host`` is the enforcement point (it refuses anything not in
    ``hosts.allowlist``); this adds the connection facts the commands need:
    ``{name, ssh, remote_root, tend_cmd, agentbox_profile, disk_budget_bytes}``.
    """
    campaign.check_host(host)
    hosts = _as_dict(campaign.hosts)
    entry = _as_dict(hosts.get(host)) or _as_dict(_as_dict(hosts.get("entries")).get(host))
    ssh = entry.get("ssh") or entry.get("target") or host
    remote_root = str(entry.get("remote_root") or entry.get("root") or hosts.get("remote_root")
                      or f"/home/{os.environ.get('USER', 'jack')}").rstrip("/")
    budget_gb = campaign.default("worktree_budget_gb", None)
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
        "book": str(remote.get("book") or "book"),
        "sources": str(remote.get("sources") or "sources"),
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


def build_manifest(*, campaign_dir: Path, skill_dir: Path, run_id: str, run: dict,
                   host: dict, include_sources: bool, include_book: bool,
                   companions: dict[str, str] | None = None) -> tuple[list[dict], list[str]]:
    """Build the rsync manifest for one run.

    The sources are what ``cf plan-run`` staged under ``runs/<run-id>/``:
    ``tasks/`` and ``data/`` are the entrypoint repo, ``tend-root/`` is the
    orchestration root (config.yaml, .tend/, bin/ wrappers, prompts/), and
    ``tend-root/gate/`` is the gate -- which goes to its OWN remote directory,
    never inside the entrypoint the worker commits to.

    ``companions`` maps companion name -> repo path; it is only consulted when
    ``include_sources`` is set.  Returns (entries, warnings); a missing REQUIRED
    component is a warning here and an error in ``cf run --execute``, so the dry
    run can still show the whole plan.
    """
    names = remote_names(run, run_id)
    root = host["remote_root"]
    rd = campaign_dir / "runs" / run_id
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
                              note="kinds/verdicts/witness/book_sha for this run's mode"))

    if not _bridges_staged(tend_root):
        entries.append(_entry("bridges", [skill_dir / "scripts" / "run" / n for n in BRIDGE_FILES],
                              f"{root}/{names['tend_root']}/bin/",
                              note="agent bridges + the StrictModel coercion"))

    if include_sources:
        for name, repo in sorted((companions or {}).items()):
            entries.append(_entry(f"source:{name}", Path(repo).expanduser(),
                                  f"{root}/{names['sources']}/{name}/",
                                  flags=READONLY_CHMOD + [f"--exclude={p}" for p in SOURCE_EXCLUDES],
                                  note="companion sources, transferred read-only"))
        if not companions:
            warnings.append("--include-sources was given but campaign.yaml lists no companion repos")
    if include_book:
        book_id = str(_as_dict(run.get("book")).get("id") or run.get("book_id") or "")
        if not book_id:
            warnings.append("--include-book was given but run.yaml names no book id")
        else:
            book_dir = campaign_dir / "book" / book_id
            dest = f"{root}/{names['book']}/{book_id}"
            entries.append(_entry("book-text", book_dir / "book.txt", f"{dest}/book.txt",
                                  note="COPYRIGHTED: leaves this machine only with --include-book"))
            if (book_dir / "errata.md").exists():
                entries.append(_entry("book-errata", book_dir / "errata.md", f"{dest}/errata.md",
                                      note="live errata, as fetched (never a summary)"))
            if "image" in book_access(run).lower():
                entries.append(_entry("book-images", book_dir / "images", f"{dest}/images/",
                                      note="COPYRIGHTED page images (the hunt run reads notation "
                                           "pdftotext drops)"))
    elif "image" in book_access(run).lower():
        warnings.append("this run's book_access asks for page images but --include-book was not "
                        "given: the workers will have no book on the host")

    for entry in entries:
        if entry["required"] and not entry["exists"]:
            warnings.append("missing %s: %s" % (entry["name"], entry["src"]))
    return entries, warnings


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


def book_access(run: dict) -> str:
    """'text' or 'text+images' -- what the workers may read of the book."""
    book = _as_dict(run.get("book"))
    return str(book.get("access") or run.get("book_access") or "text")


def task_count(campaign_dir: Path, run_id: str, run: dict) -> int:
    """Number of task files actually staged (falls back to run.yaml's count)."""
    tasks = campaign_dir / "runs" / run_id / "tasks"
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


def check_budget_cap(campaign, run: dict, max_cost: float | None,
                     no_budget_cap: bool) -> dict:
    """Resolve the spend ceiling, or refuse to plan a run without one.

    design/critique.json: "No budget gate ... run.yaml carries a billing-path
    field but no max_cost".  With agentbox the only enforceable cap is
    gateway-side, so the cap recorded here is what the operator must also set at
    the gateway; tend's own --max-cost counts tend-agent sessions only.
    """
    run_budget = _as_dict(run.get("budget"))
    budget = _as_dict(getattr(campaign, "budget", {}))
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
            "no spend ceiling: pass --max-cost N, set budget.max_cost in campaign.yaml, "
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


def companion_repos(campaign) -> dict[str, str]:
    """{companion name: repo path} from campaign.yaml."""
    out: dict[str, str] = {}
    for entry in campaign.companions:
        if isinstance(entry, dict) and entry.get("name") and entry.get("repo"):
            out[str(entry["name"])] = str(entry["repo"])
    return out


def book_sha(campaign, run: dict) -> str:
    """The pinned edition sha -- run.yaml's when present, else the campaign's."""
    book = _as_dict(run.get("book"))
    for key in ("sha256", "sha", "book_sha"):
        if book.get(key):
            return str(book[key])
    if run.get("book_sha"):
        return str(run["book_sha"])
    return str(campaign.book_sha or "")


def gate_config_warnings(campaign_dir: Path, run_id: str, host: dict, run: dict) -> list[str]:
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
    config = campaign_dir / "runs" / run_id / "tend-root" / "config.yaml"
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
