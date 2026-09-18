"""Map v1 ledger rows (batch 1, hand-transcribed) onto the v2 ledger schema.

Ported from: the shapes written by hand in
/Users/jack/Desktop/tao-differential/accepted.adam.jsonl (item, adam_decls,
finding_summary, verdict, severity, resolution, reason, confidence) and
refuted.adam.jsonl (item, kind, unfaithful_decls, original_concept, verdict,
severity, confidence, reason, evidence, action, method).

Nothing here writes to the baseline: it reads those files and returns v2 rows,
so the 2026-09-04/05 adjudication can be validated, re-routed and re-reported by
the same code that handles new runs.  The companion converter for the FINDINGS
corpus is scripts/gate/convert_v1.py; this is its ledger-side twin.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable

from ledger import compat

#: legacy verdict spellings seen in batch-1 artifacts and challenge results
LEGACY_VERDICTS = {
    "CONTENT": "CONFIRMED_CONTENT",
    "CONFIRMED": "CONFIRMED_CONTENT",
    "ATTRIBUTION": "ATTRIBUTION_GAP",
    "ATTRIBUTION_GAP": "ATTRIBUTION_GAP",
    "CONVENTION": "CONVENTION",
    "ERRATA": "ERRATA",
    "REFUTED": "REFUTED",
    "UNSURE": "UNSURE",
    "NO_DEFECT_FOUND": "REFUTED",
    "NO_WEAKNESS_FOUND": "REFUTED",
}
_RESOLUTION_HEAD = re.compile(
    r"^\s*(fixed:PR#\d+|in_progress:[A-Za-z0-9._/#+-]+|refuted|argued_correct|out_of_scope)"
)


def map_verdict(value: Any) -> str:
    """Map a v1/challenge verdict token onto the v2 ledger vocabulary."""
    token = str(value or "").strip().upper()
    return LEGACY_VERDICTS.get(token, token)


def split_resolution(value: Any) -> tuple[str | None, str]:
    """Split ``"refuted; binder dropped in PR#10"`` into (token, note)."""
    if value in (None, ""):
        return None, ""
    s = str(value).strip()
    m = _RESOLUTION_HEAD.match(s)
    if not m:
        return None, s
    token = m.group(1)
    note = s[m.end():].lstrip(" ;,.").strip()
    return token, note


def _decls(row: dict, repo: str) -> list[str]:
    """Collect declarations from either v1 spelling and prefix bare names."""
    raw: list[str] = []
    for value in (row.get("unfaithful_decls") or []):
        raw.append(str(value))
    for key in ("%s_decls" % repo, "decls", "repo_decls"):
        for value in (row.get(key) or []):
            raw.append(str(value))
    out: list[str] = []
    for d in raw:
        d = compat.normalise_decl(d)
        if not d:
            continue
        if ":" not in d and repo:
            d = "%s:%s" % (repo, d)
        if d not in out:
            out.append(d)
    return out


def match_finding(
    findings: Iterable[dict], item: str, decls: Iterable[str], kind: str = "",
    *, source_id: str = ""
) -> dict | None:
    """Find the corpus finding a v1 ledger row answers.

    v1 rows carry no ``finding_id``; they are matched on
    ``(source_id, item, declarations)`` with the finding's ``kind`` as a
    tie-break.  The join is on the PAIR: with two sources in the corpus, the
    label alone would match an ``Exercise 1.3.19`` from the other edition and
    file a ledger row against a finding nobody raised.  Returns None when the
    row answers nothing in the given corpus -- the validator then reports it as
    an unlinked row rather than silently inventing an id.
    """
    item_n = compat.item_join_key(item)
    want = {compat.normalise_decl(d) for d in decls if d}
    best, best_score = None, 0
    for f in findings:
        if compat.item_join_key(f.get("item")) != item_n:
            continue
        if source_id and str(f.get("source_id") or "") not in ("", source_id):
            continue
        have = {compat.normalise_decl(d) for d in (f.get("unfaithful_decls") or []) if d}
        overlap = len(want & have)
        if want and not overlap:
            continue
        score = 4 * overlap
        if kind and str(f.get("kind") or "") == kind:
            score += 2
        if want and have == want:
            score += 1
        if score > best_score:
            best, best_score = f, score
    return best


def map_v1_row(
    row: dict,
    *,
    repo: str,
    source_id: str,
    source_sha: str = "",
    findings: Iterable[dict] | None = None,
    run_id: str = "",
    method: str = "manual-transcription",
    at: str = "",
) -> dict:
    """Map one v1 ledger row to the v2 schema (see references/vocabulary.md).

    A v1 row predates the multi-source model and names no source, so the
    caller supplies the run's ``source_id``; a matched finding's own source
    wins over it.  The ``finding_id`` is recomputed five-part from that source,
    which is the whole reason the migration has to know it.
    """
    findings = list(findings or [])
    item = compat.normalise_item(row.get("item"))
    decls = _decls(row, repo)
    kind = str(row.get("kind") or "").strip()
    finding = match_finding(findings, item, decls, kind, source_id=source_id) if findings else None
    if finding is not None:
        fid = finding["finding_id"]
        source_id = str(finding.get("source_id") or source_id)
        source_sha = str(finding.get("source_sha") or source_sha)
        kind = kind or str(finding.get("kind") or "")
        if not decls:
            decls = [
                d
                for d in (finding.get("unfaithful_decls") or [])
                if compat.decl_repo(d) == repo
            ]
    else:
        fid = compat.finding_id(run_id, str(row.get("source_id") or source_id), item, kind, decls)

    verdict = map_verdict(row.get("verdict"))
    resolution, note = split_resolution(row.get("resolution"))
    reason = str(row.get("reason") or "").strip()
    out = {
        "finding_id": fid,
        "run_id": str(finding.get("run_id") if finding else run_id) or run_id,
        "source_id": str(row.get("source_id") or source_id or ""),
        "source_sha": str(row.get("source_sha") or row.get("book_sha") or source_sha or ""),
        "item": item,
        "repo": repo,
        "kind": kind,
        "unfaithful_decls": decls,
        "verdict": verdict,
        "severity": str(row.get("severity") or "").strip(),
        "confidence": row.get("confidence"),
        "contested": bool(row.get("contested", False)),
        "dissent": list(row.get("dissent") or []),
        "summary": str(
            row.get("finding_summary") or row.get("original_concept") or row.get("summary") or ""
        ).strip(),
        "reason": reason,
        "method": str(row.get("method") or method),
        "schema": "ledger-v2",
    }
    if verdict == "CONVENTION":
        out["recovery_argument"] = str(row.get("recovery_argument") or reason)
    if resolution:
        out["resolution"] = resolution
    if note:
        out["resolution_note"] = note
    if row.get("evidence"):
        out["evidence"] = row["evidence"]
    if row.get("action"):
        out["action"] = row["action"]
    if at or row.get("at"):
        out["at"] = str(row.get("at") or at)
    if finding is None:
        out["linked"] = False
    return out


def map_v1_file(
    path: str,
    *,
    repo: str,
    source_id: str,
    source_sha: str = "",
    findings: Iterable[dict] | None = None,
    run_id: str = "",
    method: str = "manual-transcription",
) -> tuple[list[dict], dict]:
    """Map a whole v1 ledger file; returns (rows, meta).

    ``source_sha`` falls back to the file's own ``_meta.official_pdf.sha256``
    (refuted.adam.jsonl records the pinned edition that way).
    """
    raw = compat.read_jsonl(path)
    meta = compat.meta_row(raw)
    sha = source_sha or str(
        (meta.get("official_pdf") or {}).get("sha256") if isinstance(meta.get("official_pdf"), dict) else ""
    )
    at = str(meta.get("written") or "")
    findings = list(findings or [])
    rows = [
        map_v1_row(
            r,
            repo=repo,
            source_id=source_id,
            source_sha=sha,
            findings=findings,
            run_id=run_id,
            method=method,
            at=at,
        )
        for r in compat.data_rows(raw)
    ]
    for row in rows:
        row["_file"] = os.path.basename(path)
    return rows, meta


def merge_rows(rows: Iterable[dict]) -> list[dict]:
    """Merge rows that answer the same finding with the same verdict.

    accepted.adam.jsonl and refuted.adam.jsonl overlap by design (a CONVENTION
    ruling is recorded in both), so a straight concatenation double-counts.
    Rows that disagree on the verdict are NOT merged -- validate_rows reports
    them as a contradiction, which is the point.
    """
    out: list[dict] = []
    by_key: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row.get("finding_id") or ""), str(row.get("verdict") or ""))
        if key not in by_key:
            merged = dict(row)
            by_key[key] = merged
            out.append(merged)
            continue
        merged = by_key[key]
        for field, value in row.items():
            if field == "_file":
                merged["_file"] = "%s+%s" % (merged.get("_file", ""), value)
                continue
            if value in (None, "", [], {}):
                continue
            if merged.get(field) in (None, "", [], {}):
                merged[field] = value
            elif field == "reason" and value not in str(merged[field]):
                merged[field] = "%s\n\n%s" % (merged[field], value)
    return out
