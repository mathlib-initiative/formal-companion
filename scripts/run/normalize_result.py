#!/usr/bin/env python3
"""Coerce an agent's self-reported result into tend's strict output schema.

Ported from the v1 campaign's tao-diff-tend/bin/normalize_result.py (the Claude
variant) merged with its tao-diff-tend-gpt/bin/normalize_result.py
(the codex variant, which added prose-envelope parsing, the observable-state
fallback and the reviewer-verdict scraper).  Both shipped only on the server.

WHY THIS EXISTS -- the silent-requeue trap.  tend's contribution models are
StrictModel: one unexpected key, or `validation` given as a sentence instead of
``list[ValidationEvidence]``, rejects the whole contribution and returns the
finished work to the queue with no error the operator ever sees.  The first
server task was lost exactly that way.  So this whitelists and coerces instead
of forwarding what the agent wrote, and prints ONE JSON object on stdout.

Second lesson, from the codex runs: do not make the contract hostage to
instruction-following.  codex wrote findings/ reliably and skipped the result
file, which silently failed every task, so when there is no result file we look
at what the agent ACTUALLY produced (findings written + the gate's exit code)
before deciding.

usage: normalize_result.py worker|reviewer RESULT_JSON RAW_AGENT_OUTPUT

environment:
  CF_GATE_CMD        gate command for the observable-state fallback
                     (default: "python3 scripts/verify_findings.py")
  CF_FINDINGS_GLOB   findings glob for that fallback (default: "findings/*.json");
                     $TEND_TASK_ID in it is the task's id, ** is recursive
"""
from __future__ import annotations

import glob
import json
import os
import re
import shlex
import subprocess
import sys

MAX_TEXT = 4000
GATE_CMD = os.environ.get("CF_GATE_CMD", "python3 scripts/verify_findings.py")
FINDINGS_GLOB = os.environ.get("CF_FINDINGS_GLOB", "findings/*.json")


def load(path: str):
    """Parse a JSON file, or None when it is missing or malformed."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def load_raw(path: str) -> dict:
    """The agent's own envelope. Claude emits JSON; codex emits prose."""
    data = load(path)
    if isinstance(data, dict):
        return data
    try:
        with open(path, errors="ignore") as fh:
            text = fh.read().strip()
    except Exception:
        return {}
    return {"result": text[-MAX_TEXT:], "subtype": "success" if text else "error"}


def s(value, default: str = "") -> str:
    """One-line, length-capped string (tend rejects control characters)."""
    value = value if isinstance(value, str) else default
    return " ".join(value.split())[:MAX_TEXT]


def slist(value) -> list:
    """A list of non-empty strings, capped."""
    if not isinstance(value, list):
        return []
    return [x for x in value if isinstance(x, str) and x.strip()][:200]


def observed() -> dict | None:
    """What the worktree shows, regardless of what the agent said.

    Returns ``{"files": [...], "gate_ok": bool, "gate_msg": str}`` or None when
    the agent produced no findings at all.
    """
    # A locate run's documents nest one level deeper, findings/<task-id>/<source>.json,
    # and its glob may name the task: CF_FINDINGS_GLOB=findings/$TEND_TASK_ID/*.json.
    pattern = FINDINGS_GLOB.replace("$TEND_TASK_ID", os.environ.get("TEND_TASK_ID", ""))
    files = [f for f in sorted(glob.glob(pattern, recursive=True))
             if not os.path.basename(f).startswith(".")]
    if not files:
        return None
    try:
        # Exactly the task's own documents, by path: the bare command validates
        # every document in findings/, and once any other task has merged its
        # documents are there too, each a rule-7 refusal of THIS contribution.
        argv = shlex.split(GATE_CMD) + [arg for f in files for arg in ("--findings", f)]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        gate_ok = proc.returncode == 0
        # A refusal is on stderr; stdout ends with the ownership line either way.
        gate_msg = ((proc.stderr + "\n" + proc.stdout) if not gate_ok
                    else (proc.stdout or proc.stderr)).strip()[:400]
    except Exception as exc:  # gate missing, timeout, unreadable
        gate_ok, gate_msg = False, f"gate could not run: {exc}"
    return {"files": files, "gate_ok": gate_ok, "gate_msg": gate_msg}


def worker_result(d, raw_text: str) -> dict:
    """Build a tend worker contribution from the agent's file (or the artifacts)."""
    if not isinstance(d, dict):
        obs = observed()
        if obs and obs["gate_ok"]:
            return {"schema_version": 1, "status": "completed",
                    "summary": s(raw_text)[:2000] or "findings written; validation gate passed",
                    "files_changed": obs["files"][:50],
                    "notes": "status derived from artifacts: the agent wrote no result file, but "
                             + obs["gate_msg"]}
        if obs:
            return {"schema_version": 1, "status": "needs_review",
                    "summary": s(raw_text)[:2000] or "findings written but the validation gate failed",
                    "files_changed": obs["files"][:50],
                    "notes": obs["gate_msg"] or "the validation gate exited non-zero with no output"}
        return {"schema_version": 1,
                "status": "needs_review" if raw_text else "blocked",
                "summary": s(raw_text) or "agent produced no result file, no findings and no output"}

    status = d.get("status")
    out = {"schema_version": 1,
           "status": status if status in ("completed", "blocked", "needs_review") else "needs_review",
           "summary": s(d.get("summary")) or s(raw_text) or "agent reported no summary"}
    files_changed = slist(d.get("files_changed"))
    if files_changed:
        out["files_changed"] = files_changed
    tasks_created = slist(d.get("tasks_created"))
    if tasks_created:
        out["tasks_created"] = tasks_created
    # `validation` must be list[ValidationEvidence]; agents write prose. If it is not
    # already well formed, fold it into notes rather than dropping it (or failing).
    notes = s(d.get("notes"))
    validation = d.get("validation")
    if isinstance(validation, str) and validation.strip():
        notes = (notes + " | validation: " + s(validation)).strip(" |")
    elif isinstance(validation, list) and all(isinstance(x, dict) for x in validation):
        out["validation"] = validation
    if notes:
        out["notes"] = notes[:MAX_TEXT]
    return out


_VERDICT_MARK = re.compile(r"verdict:\s*(approve|request[_ ]changes)")
_APPROVE_PROSE = re.compile(r"\b(approved|looks good|no changes needed)\b")


def scrape_verdict(raw_text: str) -> bool:
    """Read a reviewer verdict out of prose (codex writes no result file).

    Prefers an explicit ``verdict: ...`` marker and takes the LAST one, so a
    reviewer that merely DISCUSSES "request_changes" is not read as voting for it.
    """
    low = (raw_text or "").lower()
    marks = _VERDICT_MARK.findall(low)
    if marks:
        return marks[-1].replace(" ", "_") == "approve"
    return bool(_APPROVE_PROSE.search(low)) and not re.search(r"\brequest[_ ]changes\b", low)


def reviewer_result(d, raw_text: str) -> dict:
    """Build a tend reviewer contribution."""
    if not isinstance(d, dict):
        approve = scrape_verdict(raw_text)
        out = {"schema_version": 1,
               "verdict": "approve" if approve else "request_changes",
               "notes": (s(raw_text) or "reviewer produced no result file")
                        + " | verdict parsed from reviewer prose (no result file written)"}
        if not approve:
            out["feedback_text"] = s(raw_text) or "Reviewer produced no output; re-run the review."
        return out

    verdict = d.get("verdict") if d.get("verdict") in ("approve", "request_changes") else "request_changes"
    notes = s(d.get("notes")) or s(raw_text) or "reviewer reported no notes"
    out = {"schema_version": 1, "verdict": verdict, "notes": notes}
    if verdict == "request_changes":
        out["feedback_text"] = s(d.get("feedback_text")) or notes
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__.strip().splitlines()[-6], file=sys.stderr)
        return 64
    role, result_path, raw_path = argv[1], argv[2], argv[3]
    if role not in ("worker", "reviewer"):
        print(f"normalize_result: unknown role {role!r}", file=sys.stderr)
        return 64

    d = load(result_path)
    raw = load_raw(raw_path)
    raw_text = (raw.get("result") or "").strip()
    raw_failed = raw.get("subtype") != "success" or bool(raw.get("is_error"))

    if role == "worker":
        out = worker_result(d, raw_text)
        # A crashed or cut-off agent call must never look like a clean pass.
        if raw_failed and out["status"] == "completed":
            out["status"] = "needs_review"
    else:
        out = reviewer_result(d, raw_text)
        if raw_failed and out["verdict"] == "approve":
            out["verdict"] = "request_changes"
            out["feedback_text"] = "Underlying agent call did not complete successfully; re-run."

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
