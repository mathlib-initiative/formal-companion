#!/usr/bin/env python3
"""The attempt cap: the third genuine rejection ends a task as UNRESOLVED.

New code, implementing docs/proposals/wikipedia-locate.md section 6
(Convergence) for the bridges in this directory; the documents it writes are
references/locate-documents.md section 1's ``unresolved`` form.

A task a reviewer keeps rejecting used to loop until the run was stopped.  Now:

* ``record`` -- after a REVIEWER call, a counter keyed on the task id under
  ``<tend-root>/attempts/<task-id>.json`` (outside every worktree, where no
  worker can reset it) counts the rejection, but only a GENUINE one: a
  ``request_changes`` from an agent call that completed.  A timeout, a 409 or
  a crash is not a verdict and never counts;
* ``worker`` -- before a WORKER call, a task at the cap (``CF_ATTEMPT_CAP``)
  gets one schema-valid ``UNRESOLVED`` document per source of its task manifest
  (``unresolved.by: attempt_cap``), written and committed in the worktree with
  the git boundary pinned, and the bridge reports a finished contribution
  without calling an agent; exit 10 means "not capped, call the agent";
* ``reviewer`` -- a task at the cap whose worktree holds exactly those
  documents is approved mechanically, without an agent; exit 10 otherwise.

The harvest receipt counts these documents apart from the worker's own
``unresolved`` ones.  Standard library only: it runs on the run host.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

EXIT_NOT_CAPPED = 10
UNRESOLVED_ATTEMPT_CAP = "attempt_cap"
DOCUMENT_SCHEMA = "cf-locate/1"
LOCATE_INVENTORY = "locate-inventory"

#: COPIED from ``cflib.gitsafe`` (SAFE_CONFIG / SAFE_ENV) for the reason
#: ``gate/verify_findings.py`` copies them: no cflib on the host.  The worktree
#: is the worker's, so its hooks, fsmonitor and pager are not ours to run.
#: tests/test_bridge_hardening.py asserts these equal gitsafe's.
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
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")


def _safe(value: str) -> bool:
    return bool(_SAFE_ID.match(str(value or ""))) and value not in (".", "..")


# --------------------------------------------------------------------------- #
# the counter
# --------------------------------------------------------------------------- #

def counter_path(root: Path, task: str) -> Path:
    if not _safe(task):
        raise ValueError(f"unsafe task id {task!r}")
    return root / "attempts" / f"{task}.json"


def read_count(root: Path, task: str) -> int:
    try:
        data = json.loads(counter_path(root, task).read_text(encoding="utf-8"))
        return int(data.get("rejections") or 0)
    except (OSError, ValueError, AttributeError):
        return 0


def genuine(raw_path: Path) -> bool:
    """Did the reviewer's agent call complete (so its verdict is a verdict)?"""
    try:
        text = raw_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not text.strip():
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return True                        # a prose envelope (codex) that said something
    if not isinstance(data, dict):
        return False
    return data.get("subtype") == "success" and not data.get("is_error")


def record(root: Path, task: str, raw_path: Path, result_text: str) -> int:
    """Count one rejection when it is genuine; returns the count after this call."""
    try:
        verdict = json.loads(result_text).get("verdict")
    except (ValueError, AttributeError):
        verdict = None
    path = counter_path(root, task)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        count = read_count(root, task)
        if verdict == "request_changes" and genuine(raw_path):
            try:
                history = json.loads(path.read_text(encoding="utf-8")).get("history") or []
            except (OSError, ValueError, AttributeError):
                history = []
            count += 1
            history.append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps({"task_id": task, "rejections": count,
                                       "history": history}, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        return count
    finally:
        os.close(fd)


def cap_from_env() -> int:
    try:
        return max(0, int(os.environ.get("CF_ATTEMPT_CAP") or 0))
    except ValueError:
        return 0


# --------------------------------------------------------------------------- #
# the documents
# --------------------------------------------------------------------------- #

def load_manifest(manifests: Path, task: str) -> dict:
    data = json.loads((manifests / f"{task}.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("task_id") != task:
        raise ValueError(f"{manifests / (task + '.json')} is not task {task}'s manifest")
    return data


def _universe(manifests: Path, manifest: dict) -> tuple[str, str]:
    universe = manifest.get("universe")
    if not isinstance(universe, dict):
        try:
            gate = json.loads((manifests.parent / "gate.json").read_text(encoding="utf-8"))
            universe = gate.get("universe") or {}
        except (OSError, ValueError):
            universe = {}
    return str(universe.get("id") or ""), str(universe.get("sha256") or "")


def unresolved_documents(manifests: Path, task: str, reason: str) -> dict[str, dict]:
    """``{findings/<task>/<source>.json: document}`` for every document of the task."""
    manifest = load_manifest(manifests, task)
    mode = str(manifest.get("mode") or "")
    universe, universe_sha = _universe(manifests, manifest)
    out: dict[str, dict] = {}
    for doc in manifest.get("documents") or []:
        source = str(doc.get("source_id") or "")
        if not _safe(source):
            raise ValueError(f"task {task} names an unsafe source id {source!r}")
        body: dict = {"schema": DOCUMENT_SCHEMA, "mode": mode, "run_id": manifest.get("run_id"),
                      "task_id": task, "source_id": source, "source_sha": doc.get("source_sha"),
                      "layer_sha256": doc.get("layer_sha256"), "renderer": doc.get("renderer"),
                      "unresolved": {"reason": reason, "by": UNRESOLVED_ATTEMPT_CAP}}
        if mode == LOCATE_INVENTORY:
            body.update(passages=[], skips=[])
        else:
            body.update(universe=universe, universe_sha256=universe_sha)
            if mode == "locate":
                body["passages"] = [{"item": p.get("item"), "outcome": "unresolved",
                                     "locations": [], "reason": reason,
                                     "receipt": {"queries": [], "candidates": []}}
                                    for p in doc.get("passages") or [] if isinstance(p, dict)]
            else:
                body["verdicts"] = []
        out[f"findings/{task}/{source}.json"] = body
    if not out:
        raise ValueError(f"task {task}'s manifest names no documents")
    return out


def _dumps(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _filter_blanks(worktree: Path, env: dict) -> list[str]:
    """``-c filter.<n>.<hook>=`` for every content filter the worktree names.

    ``git add`` runs a clean filter over the files it stages, and the filter is
    the worker's to define; the names are read with ``git config --list``, which
    executes nothing.  A name with ``=`` gets the GIT_CONFIG_* pair as well.
    """
    got = subprocess.run(["git", *_GIT_SAFE_CONFIG, "config", "--list", "--name-only"],
                         cwd=str(worktree), capture_output=True, text=True, env=env, check=False)
    names = sorted({k.strip() for k in (got.stdout or "").splitlines()
                    if k.startswith("filter.") and k.rsplit(".", 1)[-1] in ("clean", "process", "smudge")})
    start = int(env.get("GIT_CONFIG_COUNT") or 0)
    for i, key in enumerate(names, start):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = ""
    if names:
        env["GIT_CONFIG_COUNT"] = str(start + len(names))
    return [a for k in names if "=" not in k for a in ("-c", k + "=")]


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, **_GIT_SAFE_ENV}
    env.setdefault("GIT_AUTHOR_NAME", "attempt-cap bridge")
    env.setdefault("GIT_AUTHOR_EMAIL", "attempt-cap@invalid")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    blanks = _filter_blanks(worktree, env)
    return subprocess.run(["git", *_GIT_SAFE_CONFIG, *blanks, *args], cwd=str(worktree),
                          capture_output=True, text=True, env=env, check=False)


def write_unresolved(worktree: Path, manifests: Path, task: str, count: int) -> list[str]:
    """Write and commit the task's UNRESOLVED documents; returns the paths written."""
    reason = (f"{UNRESOLVED_ATTEMPT_CAP}: {count} genuine reviewer rejections; the bridge "
              f"ended the task without another agent call")
    docs = unresolved_documents(manifests, task, reason)
    directory = worktree / "findings" / task
    if directory.is_dir():
        for stale in directory.iterdir():
            if stale.is_file() and f"findings/{task}/{stale.name}" not in docs:
                stale.unlink()
    for rel, doc in docs.items():
        path = worktree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dumps(doc), encoding="utf-8")
    task_file = worktree / "tasks" / f"{task}.yaml"
    if task_file.is_file():
        text = task_file.read_text(encoding="utf-8")
        text, n = re.subn(r"(?m)^status:\s*\S+\s*$", "status: complete", text, count=1)
        task_file.write_text(text if n else text + "status: complete\n", encoding="utf-8")
    paths = sorted(docs) + ([f"tasks/{task}.yaml"] if task_file.is_file() else [])
    added = _git(worktree, "add", "-A", "--", f"findings/{task}", *paths[len(docs):])
    if added.returncode != 0:
        raise RuntimeError(f"git add failed: {added.stderr.strip()[:300]}")
    staged = _git(worktree, "diff", "--cached", "--quiet")
    if staged.returncode != 0:
        done = _git(worktree, "commit", "-q", "-m",
                    f"UNRESOLVED at the attempt cap ({count} rejections), task {task}")
        if done.returncode != 0:
            raise RuntimeError(f"git commit failed: {done.stderr.strip()[:300]}")
    return paths


def is_capped_contribution(worktree: Path, manifests: Path, task: str) -> bool:
    """Does the worktree hold exactly the attempt-cap documents of this task?"""
    try:
        docs = unresolved_documents(manifests, task, "")
    except (OSError, ValueError):
        return False
    directory = worktree / "findings" / task
    if not directory.is_dir():
        return False
    present = sorted(f"findings/{task}/{p.name}" for p in directory.iterdir() if p.is_file())
    if present != sorted(docs):
        return False
    for rel, want in docs.items():
        try:
            have = json.loads((worktree / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        block = have.get("unresolved") if isinstance(have, dict) else None
        if not isinstance(block, dict) or block.get("by") != UNRESOLVED_ATTEMPT_CAP \
                or not str(block.get("reason") or "").startswith(UNRESOLVED_ATTEMPT_CAP):
            return False
        strip = dict(have, unresolved=None)
        expected = dict(want, unresolved=None)
        if strip.get("mode") == "locate":
            for row in strip.get("passages") or []:
                if isinstance(row, dict):
                    row["reason"] = ""
        if strip != expected:
            return False
    return True


# --------------------------------------------------------------------------- #
# the CLI the bridges call
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="attempt_cap.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("worker", "reviewer", "record"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True, help="the tend root (attempts/ lives here)")
        p.add_argument("--task", default=os.environ.get("TEND_TASK_ID", ""))
        if name != "record":
            p.add_argument("--manifests", default=os.environ.get("CF_TASK_MANIFESTS", ""))
            p.add_argument("--worktree", default=os.getcwd())
            p.add_argument("--cap", type=int, default=cap_from_env())
        else:
            p.add_argument("--raw", required=True, help="the reviewer's raw envelope")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if not _safe(args.task):
        print(f"attempt_cap: no usable task id ({args.task!r}); the cap is off", file=sys.stderr)
        return EXIT_NOT_CAPPED
    if args.cmd == "record":
        count = record(root, args.task, Path(args.raw), sys.stdin.read())
        print(f"attempt_cap: task {args.task}: {count} genuine rejection(s)", file=sys.stderr)
        return 0
    count = read_count(root, args.task)
    if args.cap < 1 or count < args.cap:
        return EXIT_NOT_CAPPED
    if not args.manifests:
        print("attempt_cap: CF_TASK_MANIFESTS is unset; cannot write the UNRESOLVED documents",
              file=sys.stderr)
        return 1
    manifests, worktree = Path(args.manifests), Path(args.worktree)
    if args.cmd == "worker":
        try:
            paths = write_unresolved(worktree, manifests, args.task, count)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"attempt_cap: could not end task {args.task} as UNRESOLVED: {exc}",
                  file=sys.stderr)
            return 1
        print(json.dumps({"schema_version": 1, "status": "completed",
                          "summary": f"attempt cap: {count} genuine reviewer rejections; the task "
                                     f"ends UNRESOLVED (written by the bridge, no agent call)",
                          "files_changed": paths[:50],
                          "notes": "unresolved.by = attempt_cap; counted separately at harvest"}))
        return 0
    if is_capped_contribution(worktree, manifests, args.task):
        print(json.dumps({"schema_version": 1, "verdict": "approve",
                          "notes": f"approved mechanically: task {args.task} is at the attempt cap "
                                   f"({count} genuine rejections) and holds exactly its "
                                   f"attempt-cap UNRESOLVED documents"}))
        return 0
    return EXIT_NOT_CAPPED


if __name__ == "__main__":
    raise SystemExit(main())
