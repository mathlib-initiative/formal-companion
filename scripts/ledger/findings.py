"""Read findings out of a corpus, a routed .jsonl or a killed run's harvest.

Ported from: the v1 campaign's routing cell (which read
``harvest.partial.json['result']['findings']`` inline) and the corpus reader of
its verify-audit-summary workflow (the ``disagreements`` array per item file).
Neither is in this repo.

This module normalises every shape the workspace can hold into ONE in-memory row
so that route/validate_ledger/adjudicate all speak the same language:

    {finding_id, run_id, source_id, source_sha, item, kind, severity, concept,
     faithful (list), unfaithful_decls, repos_involved, book_quote, reasoning,
     suggested_fix, witness, source_file}

``source_id`` is REQUIRED in the sense that matters: a row without one cannot be
given a correct ``finding_id``, because the id is five-part, so the caller
supplies the run's source and the row's own value wins when it has one.  It is
tolerant of schema v1 (``faithful`` a single string, no ``finding_id``, no
``source_sha``, and the sha in a slot called ``book_sha``): v1 rows get their
``finding_id`` computed here with the same formula ``scripts/gate/convert_v1.py``
uses, so ids agree whether a row was converted on disk or read raw.  Nothing
here writes to the baseline.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Iterable

from ledger import compat

#: v1 wrote one repo name (or 'none'/'unsure'/'all_differ'); v2 wants a list
_V1_FAITHFUL_NONLIST = {"none": [], "unsure": [], "all_differ": []}

#: The fields ``finding_id`` does NOT hash.  The id is
#: sha256(run_id, source_id, item, kind, sorted decls) -- deliberately no prose,
#: so that the same defect in two runs gets one id -- which means two genuinely
#: different CLAIMS about one declaration share it.  These are what tells them
#: apart, and :func:`claim_key` is how the collision is detected.
CLAIM_FIELDS = ("concept", "severity", "book_quote", "reasoning", "suggested_fix")


def claim_key(row: dict) -> str:
    """A digest of the claim a finding makes, i.e. of what its id does not hash."""
    parts = "\n".join(str(row.get(f) or "").strip() for f in CLAIM_FIELDS)
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


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
    source_id: str = "",
    source_sha: str = "",
    source: str = "",
) -> dict:
    """Normalise one disagreement/finding row into the v2 in-memory shape.

    ``source_id``/``source_sha`` are the fallbacks for a row that does not carry
    its own (a v1 row never does); the row's own values always win.  Both are
    propagated onto the result, because everything downstream -- the ledger
    gate, the tally's grouping, the routed file -- keys on ``(source_id,
    finding_id)`` and a row that lost its source cannot be joined back.

    ``finding_id`` is RECOMPUTED, never read.  It used to be
    ``raw.get("finding_id") or compat.finding_id(...)``, so a worker that wrote
    ``"finding_id": "00" * 32`` had it stored verbatim and every adjudication
    linked by it; ``references/vocabulary.md`` states the opposite ("a worker
    cannot be trusted to compute it").  A stored id that disagrees with the
    content is kept as ``finding_id_stored`` so the disagreement is recorded
    rather than lost.

    The id hashes the declarations AS WRITTEN, which is why ``raw_decls`` and not
    ``decls`` goes into it.  ``compat.normalise_decl`` strips INTERIOR whitespace
    (it is a matching form), while the corpus, ``convert_v1``, the gate and
    ``harvest.tally`` all hash each declaration with nothing but ``.strip()``.
    Hashing the normalised form re-keyed every finding whose declaration carries
    a space -- ``teorth:example «Exercise 1.2.1 (countable union)» at ...`` -- on
    the ledger side only, so an adjudication row addressed to the corpus id
    stopped matching its finding and ``cf tally`` reported nothing, because it
    reads its own rows.  The normalised form is still what the payload carries:
    it is what routing and matching compare.
    """
    it = compat.normalise_item(raw.get("item") or item)
    kind = str(raw.get("kind") or "").strip()
    raw_decls = [str(d) for d in (raw.get("unfaithful_decls") or []) if d]
    decls = [compat.normalise_decl(d) for d in raw_decls]
    rid = str(raw.get("run_id") or run_id or "")
    sid = str(raw.get("source_id") or source_id or "")
    ssha = str(raw.get("source_sha") or raw.get("book_sha") or source_sha or "")
    fid = compat.finding_id(rid, sid, it, kind, raw_decls)
    stored = str(raw.get("finding_id") or "")
    row = {
        "finding_id": fid,
        "run_id": rid,
        "source_id": sid,
        "source_sha": ssha,
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
        "source_file": source,
        # who produced the row: 'anchor_check' for mechanical PAGE_ANCHOR rows,
        # 'convert_v1' for converted baseline rows, else the worker/run
        "provenance": str(
            raw.get("provenance") or raw.get("source_kind") or raw.get("source") or ""
        ),
    }
    if stored and stored != fid:
        row["finding_id_stored"] = stored
    if raw.get("bridge") is not None:  # PROXY rows (stage 8)
        row["bridge"] = raw["bridge"]
    return row


def _rows_of_document(doc: Any, path: str = "") -> list[tuple[dict, str]]:
    """Pull (raw_row, item) pairs out of any corpus document shape.

    ``path`` is the file the document came from, because the ``{"result": {...}}``
    unwrap below is read only under the ONE name that shape has.  Applied to every
    corpus document it was a bypass: the gate validates the top-level
    ``disagreements``/``findings`` of an item file (``gate/verify_findings.py``
    DEFECT_LISTS) and would have counted rows out of a place it never validated.
    """
    out: list[tuple[dict, str]] = []
    if (os.path.basename(path) == "harvest.partial.json"
            and isinstance(doc, dict) and isinstance(doc.get("result"), dict)):
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
    paths_in: Iterable[str],
    *,
    run_id: str = "",
    source_id: str = "",
    source_sha: str = "",
) -> list[dict]:
    """Load findings from files and/or directories.

    ``source_id``/``source_sha`` are the defaults for rows that carry none; a
    document's own ``source_id`` always wins, so a corpus holding two sources'
    files reads correctly without a per-file flag.

    A directory is read as a corpus run: every ``*.json`` in it is one item
    (``{"item": ..., "disagreements": [...]}``) and the directory's basename is
    the default ``run_id``.  A ``.jsonl`` file is read as routed/ledger-adjacent
    rows with an optional ``_meta`` header.  A ``.json`` file may hold a corpus
    item or a ``{"findings": [...]}`` bundle; the ``{"result": {...}}`` harvest of
    a killed local fleet is unwrapped only under its own name,
    ``harvest.partial.json``, so that no corpus item can carry counted rows in a
    key the gate does not validate.
    """
    rows: list[dict] = []
    for src in paths_in:
        if os.path.isdir(src):
            rid = run_id or os.path.basename(os.path.normpath(src))
            for path in compat.iter_json_files(src):
                doc = compat.read_json(path)
                sid, sha = source_id, source_sha
                if isinstance(doc, dict):
                    sid = str(doc.get("source_id") or source_id or "")
                    sha = str(doc.get("source_sha") or doc.get("book_sha") or source_sha or "")
                for raw, item in _rows_of_document(doc, path):
                    rows.append(
                        normalise_finding(raw, item=item, run_id=rid, source_id=sid,
                                          source_sha=sha, source=path)
                    )
            continue
        if src.endswith(".jsonl"):
            raw_rows = compat.read_jsonl(src)
            meta = compat.meta_row(raw_rows)
            rid = run_id or str(meta.get("run_id") or "")
            sid = source_id or str(meta.get("source_id") or "")
            sha = source_sha or str(meta.get("source_sha") or meta.get("book_sha") or "")
            for raw in compat.data_rows(raw_rows):
                rows.append(
                    normalise_finding(raw, run_id=rid, source_id=sid, source_sha=sha, source=src)
                )
            continue
        doc = compat.read_json(src)
        rid = run_id or (doc.get("run_id") if isinstance(doc, dict) else "") or ""
        sid = source_id or (doc.get("source_id") if isinstance(doc, dict) else "") or ""
        sha = source_sha or (
            (doc.get("source_sha") or doc.get("book_sha")) if isinstance(doc, dict) else "") or ""
        for raw, item in _rows_of_document(doc, src):
            rows.append(
                normalise_finding(raw, item=item, run_id=str(rid), source_id=str(sid),
                                  source_sha=str(sha), source=src)
            )
    return rows


def id_collisions(rows: Iterable[dict]) -> list[dict]:
    """Ids carrying more than one distinct claim -- two findings, one id.

    ``concept`` is excluded from the id on purpose (it is LLM prose, and the id
    has to be stable across runs), so two different CONTENT defects about the
    same declaration of the same item hash identically.  That is not a bug in
    the id; it is a fact about it, and it has to be visible, because a single
    adjudication row addressed to the id would otherwise close both -- which
    ``ledger_coverage`` reported as 2 findings, 2 covered, 0 missing.
    """
    by_id: dict[str, list[dict]] = {}
    for row in rows:
        by_id.setdefault(str(row.get("finding_id") or ""), []).append(row)
    out: list[dict] = []
    for fid, group in sorted(by_id.items()):
        claims: dict[str, dict] = {}
        for row in group:
            claims.setdefault(claim_key(row), row)
        if len(claims) < 2:
            continue
        out.append({
            "finding_id": fid,
            "source_id": str(group[0].get("source_id") or ""),
            "item": str(group[0].get("item") or ""),
            "kind": str(group[0].get("kind") or ""),
            "claims": len(claims),
            "concepts": [str(r.get("concept") or "")[:80] for r in claims.values()],
            "files": sorted({str(r.get("source_file") or "") for r in group}),
        })
    return out


def index_by_id(rows: Iterable[dict], *, collisions: list | None = None) -> dict[str, dict]:
    """finding_id -> row (first wins; the corpus is append-only per run).

    The id is already five-part, so it is unique across sources on its own;
    keying on it needs no extra qualification.

    First-wins is NOT harmless when two findings share an id: the second claim
    disappears from the index, and a ledger row addressed to that id validates
    against the first.  Pass ``collisions`` (a list) to be told about every such
    id -- the entries :func:`id_collisions` returns are appended to it -- and
    report them; ``ledger_coverage`` refuses to count a colliding id as covered.
    """
    rows = list(rows)
    if collisions is not None:
        collisions.extend(id_collisions(rows))
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
