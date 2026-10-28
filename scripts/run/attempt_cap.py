#!/usr/bin/env python3
"""The attempt cap: the third genuine rejection ends a task as UNRESOLVED.

New code, implementing docs/proposals/wikipedia-locate.md section 6
(Convergence) for the bridges in this directory; the documents it writes are
references/locate-documents.md section 1's ``unresolved`` form.

A task a reviewer keeps rejecting used to loop until the run was stopped.  Now:

* ``record`` -- after a REVIEWER call, a counter keyed on the task id under
  ``<tend-root>/attempts/<task-id>.json`` (outside every worktree, where no
  worker can reset it) counts the rejection, but only a GENUINE one: a
  ``request_changes`` from an agent call that completed, which the reviewer
  itself stated -- in its result file (``--result``), or, with none, in an
  explicit ``verdict:`` marker of its prose (``normalize_result.
  explicit_verdict``).  A timeout, a 409, a crash, and a ``request_changes``
  the normaliser fell back to for want of a stated verdict never count;
* ``worker`` -- before a WORKER call, a task at the cap (``CF_ATTEMPT_CAP``,
  0 for off, else at least the locate gate's own :data:`LOCATE_ATTEMPT_CAP`:
  the gate accepts a ``by: attempt_cap`` document at no lower count, so a lower
  cap is refused with exit 78 on every call, before any agent is called, rather
  than writing documents every later gate call refuses)
  gets one schema-valid ``UNRESOLVED`` document per source of its task manifest
  (``unresolved.by: attempt_cap``), written and committed in the worktree with
  the git boundary pinned, and the bridge reports a finished contribution
  without calling an agent; exit 10 means "not capped, call the agent".  Every
  other path the attempts changed since the merge base with ``--base``
  (``CF_MERGE_TARGET``, default ``main``) is put back as it is there -- a
  neighbour's task file, a stray file, a deletion -- and the task file keeps the
  merge base's lines with ``status: complete``, so the change set is exactly what
  the locate gate's rule 7 accepts;
* ``reviewer`` -- a task at the cap whose change set is exactly those
  documents and its task file's status is approved mechanically, without an
  agent; exit 10 otherwise;
* ``count`` -- prints the task's count of genuine rejections.

**An audited run** (``CF_AUDIT_RULE`` set; references/locate-documents.md section
13.9) never ends a task UNRESOLVED at the cap, since that would throw a gate-clean
cut away: the worker side restores the task's best reviewed snapshot instead --
the bytes ``audit_turn.py`` recorded as ``best`` in ``<tend-root>/audit/<task-id>/
state.json``, checked against their sha256 -- into the document's path, puts every
other path back as above, commits, and the reviewer side approves exactly that
change set.  The turn machine keeps genuine rejections at or under its review
rounds less one, below the cap, so this is the backstop; a task whose state names
no snapshot (none was ever reviewed) is capped as before.

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

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import normalize_result  # noqa: E402  (bin/'s sibling: the one reading of a verdict)

EXIT_NOT_CAPPED = 10
#: a cap the locate gate would not honour (sysexits EX_CONFIG, as the bridge's own
#: configuration errors)
EXIT_CONFIG = 78
UNRESOLVED_ATTEMPT_CAP = "attempt_cap"
#: an audited run's turn state: ``<tend-root>/AUDIT_DIR/<task-id>/AUDIT_STATE``
#: (``audit_turn.py`` writes it; the cap reads its ``best`` snapshot)
AUDIT_DIR = "audit"
AUDIT_STATE = "state.json"
#: COPIED from ``cflib.vocab.LOCATE_ATTEMPT_CAP`` (no cflib on the host): the
#: count at or above which ``gate/verify_locate.py`` accepts a ``by: attempt_cap``
#: document; tests/test_locate_contract.py pins the three together
LOCATE_ATTEMPT_CAP = 3
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


def stated_rejection(result_path: Path | None, raw_path: Path) -> bool:
    """Did the reviewer itself say ``request_changes``?

    Its result file decides when it wrote one (only that file's own verdict
    counts, never a value the normaliser coerced); with none, an explicit
    ``verdict:`` marker in its prose does.  Approval prose with no marker, a
    printed object the marker reads as ``approve``, and no output at all are
    not rejections -- three of those used to overwrite a task's good documents.
    """
    if result_path is not None and result_path.is_file():
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            return data.get("verdict") == "request_changes"
    raw = normalize_result.load_raw(str(raw_path))
    return normalize_result.explicit_verdict(str(raw.get("result") or "")) == "request_changes"


def record(root: Path, task: str, raw_path: Path, result_text: str,
           result_path: Path | None = None) -> int:
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
        if verdict == "request_changes" and genuine(raw_path) \
                and stated_rejection(result_path, raw_path):
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


def audit_task_dir(root: Path, task: str) -> Path:
    """``<tend-root>/audit/<task-id>/``: an audited task's state and round files."""
    if not _safe(task):
        raise ValueError(f"unsafe task id {task!r}")
    return root / AUDIT_DIR / task


def audit_state_path(root: Path, task: str) -> Path:
    return audit_task_dir(root, task) / AUDIT_STATE


def audited() -> bool:
    """Is this an audited run's turn (the wrappers export ``CF_AUDIT_RULE``)?"""
    return bool(os.environ.get("CF_AUDIT_RULE"))


def _sha256(data: bytes) -> str:
    import hashlib  # noqa: PLC0415 - only the audited cap hashes
    return hashlib.sha256(data).hexdigest()


def best_snapshot(root: Path, task: str) -> dict | None:
    """``{round, document, bytes}`` of the audited task's best reviewed snapshot, or None.

    The state names the snapshot by a path under the task's audit directory and its
    sha256; a path outside that directory, a missing file or bytes of another sha are
    no snapshot.
    """
    try:
        state = json.loads(audit_state_path(root, task).read_text(encoding="utf-8"))
        best = state.get("best")
        document = str(state.get("document") or "")
        base = audit_task_dir(root, task).resolve()
        path = (root / str(best["snapshot"])).resolve()
        if base not in path.parents or not path.is_file() or path.is_symlink():
            return None
        data = path.read_bytes()
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    if _sha256(data) != best.get("sha256") or not document:
        return None
    return {"round": best.get("round"), "document": document, "bytes": data}


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
    # every path this module names is a literal path, never a glob pathspec
    env["GIT_LITERAL_PATHSPECS"] = "1"
    env.setdefault("GIT_AUTHOR_NAME", "attempt-cap bridge")
    env.setdefault("GIT_AUTHOR_EMAIL", "attempt-cap@invalid")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    blanks = _filter_blanks(worktree, env)
    return subprocess.run(["git", *_GIT_SAFE_CONFIG, *blanks, *args], cwd=str(worktree),
                          capture_output=True, text=True, env=env, check=False)


def cap_reason(count: int) -> str:
    """The ``unresolved.reason`` of a document the bridge writes at the cap.

    The locate gate accepts ``by: attempt_cap`` only with this text
    (``verify_locate.ATTEMPT_CAP_REASON``; tests/test_bridge_hardening.py pins
    the two together).
    """
    return (f"{UNRESOLVED_ATTEMPT_CAP}: {count} genuine reviewer rejections; the bridge "
            f"ended the task without another agent call")


def merge_base(worktree: Path, base: str) -> str:
    """The one merge base of ``base`` and HEAD (what the gate's change set is read from)."""
    got = _git(worktree, "merge-base", "--all", base, "HEAD")
    bases = got.stdout.split()
    if got.returncode != 0 or len(bases) != 1:
        raise RuntimeError(f"no single merge base between {base!r} and HEAD "
                           f"({got.stderr.strip()[:200] or f'{len(bases)} found'})")
    return bases[0]


def change_set(worktree: Path, base_commit: str) -> dict[str, str]:
    """``{path: status}`` as the locate gate reads it: the merge base against the
    working tree, without renames, plus untracked files git does not ignore."""
    got = _git(worktree, "diff", "--no-renames", "--ignore-submodules=none", "--name-status",
               "-z", base_commit)
    if got.returncode != 0:
        raise RuntimeError(f"git diff failed: {got.stderr.strip()[:300]}")
    fields = got.stdout.split("\0")
    out: dict[str, str] = {}
    for status, path in zip(fields[0::2], fields[1::2]):
        if path:
            out[path] = status.strip()
    got = _git(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    for path in got.stdout.split("\0"):
        if path:
            out.setdefault(path, "A")
    return out


def _at(worktree: Path, base_commit: str, path: str) -> str | None:
    got = _git(worktree, "cat-file", "blob", f"{base_commit}:{path}")
    return got.stdout if got.returncode == 0 else None


def restore_outside(worktree: Path, base_commit: str, keep: set[str]) -> list[str]:
    """Put every changed path but ``keep`` back as it is at the merge base; returns them.

    A path the merge base holds is checked out from it (index and working tree);
    one it does not hold is removed from both.  Git does the writing, so no
    path is followed through a symlink the worktree holds.
    """
    restored: list[str] = []
    for path in sorted(change_set(worktree, base_commit)):
        if path in keep or path.startswith(".tend/"):
            continue                  # .tend/ is tend's own state, never the attempt's
        if _at(worktree, base_commit, path) is not None:
            done = _git(worktree, "checkout", base_commit, "--", path)
        else:
            done = _git(worktree, "rm", "-q", "-r", "-f", "--cached", "--ignore-unmatch", "--",
                        path)
            if done.returncode == 0:
                done = _git(worktree, "clean", "-q", "-f", "-x", "--", path)
        if done.returncode != 0:
            raise RuntimeError(f"could not restore {path}: {done.stderr.strip()[:300]}")
        restored.append(path)
    return restored


_STATUS = re.compile(r"(?m)^status:.*$")


def complete_task_file(text: str) -> str:
    """The task file's text with ``status: complete`` and every other line as given."""
    done, n = _STATUS.subn("status: complete", text, count=1)
    if n:
        return done
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "status: complete\n"


def write_unresolved(worktree: Path, manifests: Path, task: str, count: int,
                     base: str = "main") -> list[str]:
    """Write and commit the task's UNRESOLVED documents; returns the paths written.

    Everything else the rejected attempts changed is put back as it is at the
    merge base first (:func:`restore_outside`): left in the branch, a
    neighbour's task file or a stray path made the gate refuse the capped
    contribution on every later call, which is the loop the cap exists to end.
    """
    reason = cap_reason(count)
    docs = unresolved_documents(manifests, task, reason)
    base_commit = merge_base(worktree, base)
    task_rel = f"tasks/{task}.yaml"
    restore_outside(worktree, base_commit, set(docs))
    for rel, doc in docs.items():
        path = worktree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dumps(doc), encoding="utf-8")
    task_file = worktree / task_rel
    if task_file.is_file():
        task_file.write_text(complete_task_file(task_file.read_text(encoding="utf-8")),
                             encoding="utf-8")
    paths = sorted(docs) + ([task_rel] if task_file.is_file() else [])
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


def _restored_path(manifests: Path, task: str, best: dict) -> str:
    """The one document an audited task holds, as its manifest and the state name it."""
    manifest = load_manifest(manifests, task)
    sources = [str(d.get("source_id") or "") for d in manifest.get("documents") or []]
    if len(sources) != 1 or not _safe(sources[0]):
        raise ValueError(f"task {task}'s manifest names {len(sources)} documents, not one")
    rel = f"findings/{task}/{sources[0]}.json"
    if best["document"] != rel:
        raise ValueError(f"the audit state names {best['document']!r}, not {rel!r}")
    return rel


def write_best(worktree: Path, manifests: Path, task: str, best: dict, count: int,
               base: str = "main") -> list[str]:
    """Restore and commit an audited task's best reviewed snapshot; returns the paths.

    As :func:`write_unresolved`, but the document is the snapshot's bytes."""
    rel = _restored_path(manifests, task, best)
    base_commit = merge_base(worktree, base)
    task_rel = f"tasks/{task}.yaml"
    restore_outside(worktree, base_commit, {rel})
    path = worktree / rel
    for part in (worktree / "findings", worktree / "findings" / task, path):
        if part.is_symlink():
            raise RuntimeError(f"{part} is a symlink; the snapshot is not written through it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(best["bytes"])
    task_file = worktree / task_rel
    if task_file.is_file():
        task_file.write_text(complete_task_file(task_file.read_text(encoding="utf-8")),
                             encoding="utf-8")
    paths = [rel] + ([task_rel] if task_file.is_file() else [])
    added = _git(worktree, "add", "-A", "--", f"findings/{task}", *paths[1:])
    if added.returncode != 0:
        raise RuntimeError(f"git add failed: {added.stderr.strip()[:300]}")
    staged = _git(worktree, "diff", "--cached", "--quiet")
    if staged.returncode != 0:
        done = _git(worktree, "commit", "-q", "-m",
                    f"The attempt cap restores round {best['round']}'s reviewed snapshot "
                    f"({count} rejections), task {task}")
        if done.returncode != 0:
            raise RuntimeError(f"git commit failed: {done.stderr.strip()[:300]}")
    return paths


def is_restored_contribution(worktree: Path, manifests: Path, task: str, best: dict,
                             base: str = "main") -> bool:
    """Does the worktree hold exactly the best snapshot's bytes as its document, and change
    nothing else since the merge base but its task file's status?"""
    try:
        rel = _restored_path(manifests, task, best)
        base_commit = merge_base(worktree, base)
        changed = change_set(worktree, base_commit)
        have = (worktree / rel).read_bytes()
    except (OSError, ValueError, RuntimeError):
        return False
    task_rel = f"tasks/{task}.yaml"
    if not set(changed) <= {rel, task_rel} or any(s == "D" for s in changed.values()):
        return False
    if task_rel in changed:
        before = _at(worktree, base_commit, task_rel)
        try:
            after = (worktree / task_rel).read_text(encoding="utf-8")
        except OSError:
            return False
        if before is None or complete_task_file(before) != after:
            return False
    present = sorted(p.name for p in (worktree / "findings" / task).iterdir() if p.is_file())
    return present == [Path(rel).name] and have == best["bytes"]


def is_capped_contribution(worktree: Path, manifests: Path, task: str,
                           base: str = "main") -> bool:
    """Does the worktree hold exactly the attempt-cap documents of this task, and change
    nothing else since the merge base but its task file's status?"""
    try:
        docs = unresolved_documents(manifests, task, "")
        base_commit = merge_base(worktree, base)
        changed = change_set(worktree, base_commit)
    except (OSError, ValueError, RuntimeError):
        return False
    task_rel = f"tasks/{task}.yaml"
    if not set(changed) <= set(docs) | {task_rel} or any(s == "D" for s in changed.values()):
        return False
    if task_rel in changed:
        before = _at(worktree, base_commit, task_rel)
        try:
            after = (worktree / task_rel).read_text(encoding="utf-8")
        except OSError:
            return False
        if before is None or complete_task_file(before) != after:
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
    for name in ("worker", "reviewer", "record", "count"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True, help="the tend root (attempts/ lives here)")
        p.add_argument("--task", default=os.environ.get("TEND_TASK_ID", ""))
        if name == "count":
            continue
        if name != "record":
            p.add_argument("--manifests", default=os.environ.get("CF_TASK_MANIFESTS", ""))
            p.add_argument("--worktree", default=os.getcwd())
            p.add_argument("--cap", type=int, default=cap_from_env())
            p.add_argument("--base", default=os.environ.get("CF_MERGE_TARGET") or "main",
                           help="the merge target the change set is read against "
                                "(default: $CF_MERGE_TARGET, else main)")
        else:
            p.add_argument("--raw", required=True, help="the reviewer's raw envelope")
            p.add_argument("--result", required=True,
                           help="the reviewer's result file (.tend/agent_result.json), which "
                                "may be missing")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if args.cmd == "count":
        if not _safe(args.task):
            print(f"attempt_cap: no usable task id ({args.task!r})", file=sys.stderr)
            return 64
        print(read_count(root, args.task))
        return 0
    if args.cmd != "record" and 0 < args.cap < LOCATE_ATTEMPT_CAP:
        print(f"attempt_cap: a cap of {args.cap} (CF_ATTEMPT_CAP) is under the locate gate's "
              f"{LOCATE_ATTEMPT_CAP}: the gate refuses a by: attempt_cap document at a lower "
              f"count, so the capped task would loop; set CF_ATTEMPT_CAP to 0 (off) or at "
              f"least {LOCATE_ATTEMPT_CAP} in the wrapper", file=sys.stderr)
        return EXIT_CONFIG
    if not _safe(args.task):
        print(f"attempt_cap: no usable task id ({args.task!r}); the cap is off", file=sys.stderr)
        return EXIT_NOT_CAPPED
    if args.cmd == "record":
        count = record(root, args.task, Path(args.raw), sys.stdin.read(), Path(args.result))
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
    best = best_snapshot(root, args.task) if audited() else None
    if best is not None:
        return _audited_cap(args, root, manifests, worktree, best, count)
    if args.cmd == "worker":
        try:
            paths = write_unresolved(worktree, manifests, args.task, count, args.base)
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
    if is_capped_contribution(worktree, manifests, args.task, args.base):
        print(json.dumps({"schema_version": 1, "verdict": "approve",
                          "notes": f"approved mechanically: task {args.task} is at the attempt cap "
                                   f"({count} genuine rejections) and holds exactly its "
                                   f"attempt-cap UNRESOLVED documents"}))
        return 0
    return EXIT_NOT_CAPPED


def _audited_cap(args, root: Path, manifests: Path, worktree: Path, best: dict,
                 count: int) -> int:
    """The cap of an audited run: the best reviewed snapshot, never UNRESOLVED."""
    if args.cmd == "worker":
        try:
            paths = write_best(worktree, manifests, args.task, best, count, args.base)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"attempt_cap: could not restore task {args.task}'s best snapshot: {exc}",
                  file=sys.stderr)
            return 1
        print(json.dumps({"schema_version": 1, "status": "completed",
                          "summary": f"attempt cap: {count} genuine reviewer rejections; the "
                                     f"bridge restored round {best['round']}'s reviewed "
                                     f"snapshot (no agent call)",
                          "files_changed": paths[:50],
                          "notes": "audited run: the cap restores the best reviewed snapshot, "
                                   "never UNRESOLVED"}))
        return 0
    if is_restored_contribution(worktree, manifests, args.task, best, args.base):
        print(json.dumps({"schema_version": 1, "verdict": "approve",
                          "notes": f"approved mechanically: task {args.task} is at the attempt "
                                   f"cap ({count} genuine rejections) and holds exactly round "
                                   f"{best['round']}'s reviewed snapshot"}))
        return 0
    return EXIT_NOT_CAPPED


if __name__ == "__main__":
    raise SystemExit(main())
