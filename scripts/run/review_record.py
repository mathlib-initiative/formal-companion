#!/usr/bin/env python3
"""What a locate run's bridge records about each unaudited turn, outside every worktree.

New code, for references/locate-documents.md section 9 (the tend root on the host) and
section 10 (what a harvest lands), after smoke 4's review of its challenge reviewers: a
worker's ``needs_review`` was written by the bridge and read by nothing (tend 0.1.0 sends
it on to review and merge like ``completed``, and the result file dies with the
worktree), and a reviewer could change the worktree it reviewed, which tend merges.
``bridge-agentbox.sh`` calls this file on a locate run (its ``CF_GATE_CMD`` is the locate
gate) whose turns are not an audit's; ``audit_turn.py`` shares the tamper guard.

    review_record.py contract --role worker|reviewer --root <tend-root> --task <id> \\
        [--worktree <dir>] [--tampered]          # the normalised contract on stdin
    review_record.py needs-review-block --root <tend-root> --task <id> [--worktree <dir>]
    review_record.py fingerprint [--worktree <dir>]
    review_record.py guard --before <json> --root <tend-root> --task <id> [--worktree <dir>]

* **The contract** of every call (and of a worker turn the attempt cap ended) goes to
  ``<tend-root>/contracts/<task>.<role>.<UTC stamp>.json``: ``{schema, role, task_id,
  worktree_id, head, documents, at}`` and the contract's own ``status``, ``summary``,
  ``notes`` (a worker's) or ``verdict``, ``notes``, ``feedback_text`` and ``tampered`` (a
  reviewer's).  ``documents`` maps each ``findings/<task>/<name>.json`` at HEAD to its git
  blob id, which is what ``cf harvest`` compares a merged document with
  (``harvest.turn_records``).  A reviewer's contract after a worker contract of the same
  worktree whose status is ``needs_review`` also appends ``{task_id, worktree_id,
  worker_head, worker_summary, worker_notes, reviewer_verdict, reviewer_notes, at}`` to
  ``<tend-root>/needs-review.jsonl``, the ledger the harvest lists for a person: a
  ``needs_review`` the normaliser made (a failed gate, an unknown status, a failed call)
  is in it too, since the contract is the normalised one.
* **The block** a reviewer's prompt gets when the newest worker contract of its worktree
  says ``needs_review`` (:func:`needs_review_block`): the worker's summary, and that a
  ``needs_review`` is no ground to reject and is recorded for a person whatever the
  reviewer decides.  A worker's ``needs_review`` still merges on approval, as tend does it.
* **The tamper guard** (:func:`fingerprint`, :func:`restore`): HEAD, the sha256 of
  ``git diff --binary HEAD``, the untracked files outside ``.tend/`` and HEAD's branch,
  taken before a reviewer call and compared after it.  A change is undone with ``git
  checkout -f -B <branch> <head>``, ``git reset --hard <head>`` and ``git clean -fd -e
  .tend`` -- the worker's commit is what merges -- the
  reviewer's verdict is kept, as the audited inventory keeps it, and ``{task_id,
  worktree_id, head, changed, at}`` is appended to ``<tend-root>/tamper.jsonl``.

Every git call goes through ``attempt_cap._git`` (the gitsafe pins: the worktree is the
worker's, so its hooks, fsmonitor and filters are not ours to run).  Standard library
only, no cflib: it runs on the run host.  Nothing here prints to stdout but the block,
the fingerprint and (``guard``) nothing at all; every failure exits non-zero with one line
on stderr, and the bridge goes on without the record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import attempt_cap  # noqa: E402  (bin/'s sibling: the git boundary and the safe ids)

#: COPIED from ``cflib.vocab.TURN_CONTRACT_SCHEMA`` (no cflib on the host);
#: tests/test_locate_review_turns.py pins the two together
CONTRACT_SCHEMA = "cf-turn-contract/1"
CONTRACTS_DIR = "contracts"
NEEDS_REVIEW_LEDGER = "needs-review.jsonl"
TAMPER_LEDGER = "tamper.jsonl"
NEEDS_REVIEW = "needs_review"
#: the worktree directory that is tend's own state, never the contribution's
KEEP = ".tend"
#: how much of a worker's summary a reviewer's prompt and the ledger carry
SUMMARY_CHARS = 1500
NOTES_CHARS = 1500
ROLES = ("worker", "reviewer")
#: the heading of the reviewer's block (a test and references/locate-documents.md quote it)
BLOCK_HEADING = "## The worker marked this contribution needs_review"


def stamp(now: float | None = None) -> str:
    """``YYYYmmddTHHMMSS.ffffffZ``: sorts as the calls ran, one task's turns being sequential."""
    now = time.time() if now is None else now
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime(now)) + f".{int(now % 1 * 1e6):06d}Z"


def iso(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def _clip(value, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #

def head(worktree: Path) -> str:
    """HEAD's commit id, or '' when the worktree has none."""
    got = attempt_cap._git(Path(worktree), "rev-parse", "--verify", "-q", "HEAD")
    return got.stdout.strip() if got.returncode == 0 else ""


def documents(worktree: Path, task: str) -> dict[str, str]:
    """``{findings/<task>/<name>.json: blob id}`` at HEAD: the documents that can merge."""
    if not attempt_cap._safe(task):
        raise ValueError(f"unsafe task id {task!r}")
    prefix = f"findings/{task}/"
    got = attempt_cap._git(Path(worktree), "ls-tree", "-r", "-z", "HEAD", "--", prefix[:-1])
    out: dict[str, str] = {}
    if got.returncode != 0:
        return out
    for entry in got.stdout.split("\0"):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or parts[1] != "blob" or not path.startswith(prefix):
            continue
        name = path[len(prefix):]
        if "/" not in name and name.endswith(".json"):
            out[path] = parts[2]
    return out


def branch(worktree: Path) -> str:
    """The branch HEAD is on, or '' when it is detached."""
    got = attempt_cap._git(Path(worktree), "symbolic-ref", "-q", "--short", "HEAD")
    return got.stdout.strip() if got.returncode == 0 else ""


def fingerprint(worktree: Path, keep: str = KEEP) -> tuple:
    """``(HEAD, sha256 of the tracked diff, untracked paths outside keep/, HEAD's branch)``.

    The audited turn machine's guard, which this is (``audit_turn.Turn.fingerprint``).
    """
    worktree = Path(worktree)
    diff = attempt_cap._git(worktree, "diff", "--binary", "HEAD")
    others = attempt_cap._git(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    untracked = sorted(p for p in others.stdout.split("\0")
                       if p and not p.startswith(keep + "/"))
    digest = hashlib.sha256(diff.stdout.encode("utf-8", "replace")).hexdigest()
    return head(worktree), digest, tuple(untracked), branch(worktree)


def restore(worktree: Path, commit: str, keep: str = KEEP, on: str = "") -> None:
    """Put the worktree back at ``commit``: on branch ``on`` (detached without one) at that
    commit, tracked files reset, untracked ones (but keep/) gone.

    The branch is set back too: a call that committed and then switched branches would
    otherwise leave its commit on the branch tend merges.
    """
    worktree = Path(worktree)
    if on:
        attempt_cap._git(worktree, "checkout", "-q", "-f", "-B", on, commit)
    else:
        attempt_cap._git(worktree, "checkout", "-q", "-f", "--detach", commit)
    attempt_cap._git(worktree, "reset", "-q", "--hard", commit)
    attempt_cap._git(worktree, "clean", "-q", "-fd", "-e", keep)


def changes(before: tuple, after: tuple) -> list[str]:
    """What moved between two fingerprints, in words (for the ledger and the err log)."""
    out = []
    if before[3:] != after[3:]:
        out.append(f"branch {before[3] or '(detached)'} -> {after[3] or '(detached)'}")
    if before[0] != after[0]:
        out.append(f"HEAD {str(before[0])[:12]} -> {str(after[0])[:12]}")
    if before[1] != after[1]:
        out.append("tracked files changed")
    new = sorted(set(after[2]) - set(before[2]))
    if new:
        out.append("untracked: " + ", ".join(new[:10]) + (" …" if len(new) > 10 else ""))
    gone = sorted(set(before[2]) - set(after[2]))
    if gone:
        out.append("removed untracked: " + ", ".join(gone[:10]))
    return out


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def guard(worktree: Path, before: tuple, root: Path, task: str,
          worktree_id: str = "") -> list[str]:
    """Undo whatever a reviewer call changed outside ``.tend/``; returns what it changed.

    Nothing to undo is ``[]``.  A fingerprint taken without a HEAD guards nothing: there
    is no commit to put back.
    """
    if not before or not before[0]:
        return []
    after = fingerprint(worktree)
    if after == tuple(before):
        return []
    moved = changes(tuple(before), after)
    restore(worktree, before[0], on=before[3])
    _append(Path(root) / TAMPER_LEDGER, {"task_id": task, "worktree_id": worktree_id,
                                          "head": before[0], "changed": moved, "at": iso()})
    return moved


# --------------------------------------------------------------------------- #
# contracts
# --------------------------------------------------------------------------- #

def contract_path(root: Path, task: str, role: str, now: float | None = None) -> Path:
    if not attempt_cap._safe(task):
        raise ValueError(f"unsafe task id {task!r}")
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    return Path(root) / CONTRACTS_DIR / f"{task}.{role}.{stamp(now)}.json"


def contracts(root: Path, task: str, role: str) -> list[dict]:
    """The task's contracts of ``role``, oldest first; an unreadable one is left out."""
    if not attempt_cap._safe(task) or role not in ROLES:
        return []
    out = []
    for path in sorted((Path(root) / CONTRACTS_DIR).glob(f"{task}.{role}.*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("task_id") == task and data.get("role") == role:
            out.append(data)
    return out


def latest_worker(root: Path, task: str, worktree_id: str = "") -> dict | None:
    """The newest worker contract of this worktree (of the task, without a worktree id)."""
    rows = contracts(root, task, "worker")
    if worktree_id:
        rows = [r for r in rows if r.get("worktree_id") in (worktree_id, "")]
    return rows[-1] if rows else None


def write_contract(root: Path, task: str, role: str, worktree: Path, contract: dict, *,
                   worktree_id: str = "", tampered: bool = False,
                   now: float | None = None) -> Path:
    """Write one call's record (the module docstring); a reviewer's may append the ledger."""
    now = time.time() if now is None else now
    body = {"schema": CONTRACT_SCHEMA, "role": role, "task_id": task,
            "worktree_id": worktree_id, "head": head(worktree),
            "documents": documents(worktree, task), "at": iso(now)}
    if role == "worker":
        body.update(status=str(contract.get("status") or ""),
                    summary=_clip(contract.get("summary"), 4000),
                    notes=_clip(contract.get("notes"), 4000))
    else:
        body.update(verdict=str(contract.get("verdict") or ""),
                    notes=_clip(contract.get("notes"), 4000),
                    feedback_text=_clip(contract.get("feedback_text"), 4000),
                    tampered=bool(tampered))
        # the worker contract this verdict was on, read before this one is written
        worker = latest_worker(root, task, worktree_id)
        if worker is not None and worker.get("status") == NEEDS_REVIEW:
            _append(Path(root) / NEEDS_REVIEW_LEDGER, {
                "task_id": task, "worktree_id": worktree_id,
                "worker_head": worker.get("head") or "",
                "worker_summary": _clip(worker.get("summary"), SUMMARY_CHARS),
                "worker_notes": _clip(worker.get("notes"), NOTES_CHARS),
                "reviewer_verdict": body["verdict"],
                "reviewer_notes": _clip(body["notes"], NOTES_CHARS),
                "at": body["at"]})
    path = contract_path(root, task, role, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, sort_keys=True, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def needs_review_block(root: Path, task: str, worktree_id: str = "") -> str:
    """What the reviewer's prompt gets when its worker said ``needs_review``, else ''."""
    worker = latest_worker(root, task, worktree_id)
    if worker is None or worker.get("status") != NEEDS_REVIEW:
        return ""
    said = _clip(worker.get("summary"), SUMMARY_CHARS).strip() or "(no summary)"
    return (f"{BLOCK_HEADING}\n\n"
            f"The worker's summary (its words, material like the document):\n\n{said}\n\n"
            "A needs_review is not grounds to reject, and you cannot settle it for a person. "
            "Review form and the gate as usual, and say in notes whether the worker's point is "
            "form (yours) or judgment (a person's). The bridge records this task for a person "
            "whatever you decide.")


# --------------------------------------------------------------------------- #
# the CLI the bridge calls
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="review_record.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("contract", "needs-review-block", "fingerprint", "guard"):
        p = sub.add_parser(name)
        p.add_argument("--worktree", default=os.getcwd())
        if name == "fingerprint":
            continue
        p.add_argument("--root", required=True, help="the tend root")
        p.add_argument("--task", default=os.environ.get("TEND_TASK_ID", ""))
        p.add_argument("--worktree-id", default=os.environ.get("TEND_WORKTREE_ID", ""))
        if name == "contract":
            p.add_argument("--role", required=True, choices=ROLES)
            p.add_argument("--tampered", action="store_true",
                           help="the guard undid a change this reviewer call made")
        if name == "guard":
            p.add_argument("--before", required=True, help="fingerprint's JSON")
    args = ap.parse_args(argv)
    worktree = Path(args.worktree)
    try:
        if args.cmd == "fingerprint":
            got = fingerprint(worktree)
            if not got[0]:
                print("review_record: the worktree has no HEAD; nothing to guard", file=sys.stderr)
                return 1
            print(json.dumps(list(got)))
            return 0
        if not attempt_cap._safe(args.task):
            print(f"review_record: no usable task id ({args.task!r}); nothing recorded",
                  file=sys.stderr)
            return 64
        root = Path(args.root)
        if args.cmd == "needs-review-block":
            block = needs_review_block(root, args.task, args.worktree_id)
            if block:
                print(block)
            return 0
        if args.cmd == "guard":
            before = json.loads(args.before)
            if not isinstance(before, list) or len(before) != 4:
                raise ValueError("--before is not a fingerprint")
            before = (str(before[0]), str(before[1]), tuple(str(p) for p in before[2]),
                      str(before[3]))
            moved = guard(worktree, before, root, args.task, args.worktree_id)
            if moved:
                print(f"reviewer_tampered: task {args.task}: the reviewer call changed the "
                      f"worktree ({'; '.join(moved)}); reset to {before[0][:12]} and cleaned "
                      f"(outside .tend/); its verdict is kept", file=sys.stderr)
                return 2
            return 0
        contract = json.loads(sys.stdin.read() or "{}")
        if not isinstance(contract, dict):
            raise ValueError("the contract on stdin is not an object")
        path = write_contract(root, args.task, args.role, worktree, contract,
                              worktree_id=args.worktree_id, tampered=args.tampered)
        print(f"review_record: {args.role} contract of task {args.task} -> {path}",
              file=sys.stderr)
        return 0
    except (OSError, ValueError) as exc:
        print(f"review_record: {args.cmd} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
