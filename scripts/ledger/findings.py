"""Read findings out of a corpus, a routed .jsonl or a killed run's harvest.

Ported from: recovered/b174aa13_L5702_Bash.txt (which read
``harvest.partial.json['result']['findings']`` inline) and the corpus reader in
recovered/verify-audit-summary-wf_9da8dddd-e4b.js (the ``disagreements`` array
per item file).

This module normalises every shape the campaign can hold into ONE in-memory row
so that route/validate_ledger/adjudicate all speak the same language:

    {finding_id, run_id, item, kind, severity, concept, faithful (list),
     unfaithful_decls, repos_involved, book_quote, reasoning, suggested_fix,
     witness, book_sha, source}

It is tolerant of schema v1 (``faithful`` a single string, no ``finding_id``,
no ``book_sha``): v1 rows get their ``finding_id`` computed here with the same
formula ``scripts/gate/convert_v1.py`` uses, so ids agree whether a row was
converted on disk or read raw.  Nothing here writes to the baseline.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from ledger import compat

#: v1 wrote one repo name (or 'none'/'unsure'/'all_differ'); v2 wants a list
_V1_FAITHFUL_NONLIST = {"none": [], "unsure": [], "all_differ": []}


def _as_faithful_list(value: Any) -> list[str]:
    """Coerce a v1 ``faithful`` enum or a v2 list into a list of repo names."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    s = str(value).strip()
    if s in _V1_FAITHFUL_NONLIST:
        return list(_V1_FAITHFUL_NONLIST[s])
    return [s] if s else []


def normalise_finding(
    raw: dict,
    *,
    item: str = "",
    run_id: str = "",
    book_sha: str = "",
    source: str = "",
) -> dict:
    """Normalise one disagreement/finding row into the v2 in-memory shape."""
    it = compat.normalise_item(raw.get("item") or item)
    kind = str(raw.get("kind") or "").strip()
    decls = [compat.normalise_decl(d) for d in (raw.get("unfaithful_decls") or []) if d]
    rid = str(raw.get("run_id") or run_id or "")
    fid = raw.get("finding_id") or compat.finding_id(rid, it, kind, decls)
    row = {
        "finding_id": fid,
        "run_id": rid,
        "item": it,
        "kind": kind,
        "severity": str(raw.get("severity") or "").strip(),
        "concept": raw.get("concept") or raw.get("original_concept") or "",
        "faithful": _as_faithful_list(raw.get("faithful")),
        "unfaithful_decls": decls,
        "repos_involved": [str(r) for r in (raw.get("repos_involved") or []) if r],
        "book_quote": raw.get("book_quote") or "",
        "reasoning": raw.get("reasoning") or raw.get("reason") or "",
        "suggested_fix": raw.get("suggested_fix") or "",
        "witness": raw.get("witness"),
        "book_sha": str(raw.get("book_sha") or book_sha or ""),
        "source_file": source,
        # who produced the row: 'anchor_check' for mechanical PAGE_ANCHOR rows,
        # 'convert_v1' for converted baseline rows, else the worker/run
        "provenance": str(
            raw.get("provenance") or raw.get("source_kind") or raw.get("source") or ""
        ),
    }
    if raw.get("bridge") is not None:  # PROXY rows (stage 8)
        row["bridge"] = raw["bridge"]
    return row


def _rows_of_document(doc: Any) -> list[tuple[dict, str]]:
    """Pull (raw_row, item) pairs out of any corpus document shape."""
    out: list[tuple[dict, str]] = []
    if isinstance(doc, dict) and "result" in doc and isinstance(doc["result"], dict):
        doc = doc["result"]  # harvest.partial.json of a killed local fleet
    if isinstance(doc, list):
        for r in doc:
            if isinstance(r, dict) and not compat.is_meta_row(r):
                out.append((r, r.get("item", "")))
        return out
    if not isinstance(doc, dict):
        return out
    item = doc.get("item", "")
    for key in ("disagreements", "findings"):
        for r in doc.get(key) or []:
            if isinstance(r, dict) and not compat.is_meta_row(r):
                out.append((r, r.get("item", item)))
    return out


def load_findings(
    sources: Iterable[str],
    *,
    run_id: str = "",
    book_sha: str = "",
) -> list[dict]:
    """Load findings from files and/or directories.

    A directory is read as a corpus run: every ``*.json`` in it is one book item
    (``{"item": ..., "disagreements": [...]}``) and the directory's basename is
    the default ``run_id``.  A ``.jsonl`` file is read as routed/ledger-adjacent
    rows with an optional ``_meta`` header.  A ``.json`` file may hold a corpus
    item, a ``{"findings": [...]}`` bundle or a ``{"result": {...}}`` harvest.
    """
    rows: list[dict] = []
    for src in sources:
        if os.path.isdir(src):
            rid = run_id or os.path.basename(os.path.normpath(src))
            for path in compat.iter_json_files(src):
                doc = compat.read_json(path)
                sha = book_sha
                if isinstance(doc, dict):
                    sha = str(doc.get("book_sha") or book_sha or "")
                for raw, item in _rows_of_document(doc):
                    rows.append(
                        normalise_finding(
                            raw, item=item, run_id=rid, book_sha=sha, source=path
                        )
                    )
            continue
        if src.endswith(".jsonl"):
            raw_rows = compat.read_jsonl(src)
            meta = compat.meta_row(raw_rows)
            rid = run_id or str(meta.get("run_id") or "")
            sha = book_sha or str(meta.get("book_sha") or "")
            for raw in compat.data_rows(raw_rows):
                rows.append(
                    normalise_finding(raw, run_id=rid, book_sha=sha, source=src)
                )
            continue
        doc = compat.read_json(src)
        rid = run_id or (doc.get("run_id") if isinstance(doc, dict) else "") or ""
        for raw, item in _rows_of_document(doc):
            rows.append(
                normalise_finding(
                    raw, item=item, run_id=str(rid), book_sha=book_sha, source=src
                )
            )
    return rows


def index_by_id(rows: Iterable[dict]) -> dict[str, dict]:
    """finding_id -> row (first wins; the corpus is append-only per run)."""
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["finding_id"], r)
    return out


def rows_naming(rows: Iterable[dict], repo: str) -> list[dict]:
    """The Stage 9 routing rule: a row belongs to *repo* iff it NAMES a repo decl.

    The recovered routing cell (b174aa13_L5702) also admitted a row when the
    repo merely appeared in ``repos_involved`` and was not the ``faithful`` one.
    That fallback is what produced the "31 findings" scare, so it is gone: the
    prefix of ``unfaithful_decls`` is the whole rule.
    """
    prefix = "%s:" % repo
    return [
        r
        for r in rows
        if any(str(d).startswith(prefix) for d in (r.get("unfaithful_decls") or []))
    ]


def decls_of(row: dict, repo: str = "") -> list[str]:
    """The row's unfaithful decls, optionally restricted to one repo prefix."""
    decls = [compat.normalise_decl(d) for d in (row.get("unfaithful_decls") or []) if d]
    if not repo:
        return decls
    prefix = "%s:" % repo
    return [d for d in decls if d.startswith(prefix)]
