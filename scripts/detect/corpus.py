"""Publish MECHANICAL rows into ``corpus/<run-id>/`` as gate-shaped documents.

Used by ``cf detect-proxies`` (PROXY, COVERAGE) and by ``cf pair``
(PAGE_ANCHOR, from ``pair/anchor_check.py``).

New code.  design/stage_map.md Stage 8's gate is that each hit is "emitted as a
finding row of kind ``PROXY`` ... so it joins the corpus and the ledger like any
other finding".  It did not: ``cf detect-proxies`` wrote ``detect/<name>/
findings.*.jsonl`` and every downstream reader (``cf tally``, ``route``,
``adjudicate``) reads ``corpus/`` only, so the model-free stage the SKILL calls
step 5 of the default program contributed zero rows to the report -- and the
``mechanical`` gate preset, which exists for exactly these rows, was applied to
nothing.

This module is the join.  A detector row is FLAT (one defect, one declaration);
a corpus document is per book ITEM and carries ``disagreements``.  So the rows
are grouped by item and each becomes one disagreement, with the per-kind
evidence the gate demands: PROXY needs ``bridge`` and a ``closure`` path,
COVERAGE needs neither.

These documents deliberately carry no ``book_passage_quote``: a mechanical
detector never read the book.  The ``mechanical`` preset sets
``require_passage_quote: false`` for that reason, and no other preset does.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from detect._compat import read_json, vocab, write_json

SCHEMA_VERSION = 2

#: keys of a detector row that become disagreement keys unchanged
_CARRIED = (
    "finding_id", "concept", "kind", "reasoning", "severity",
    "unfaithful_decls", "bridge", "bridge_decls", "book_sha", "detector",
    # PAGE_ANCHOR rows from pair/anchor_check.py travel the same road
    "source", "page", "mechanical", "advisory", "detail",
)


def _closure(row: dict) -> list[str]:
    """The dependency path a PROXY row offers as evidence.

    ``analyze_stages`` rows carry ``evidence.closure_path``; the body-proxy rows
    from ``def_coverage`` carry the library objects the defining term names.
    Either way the gate wants something non-empty that a human can follow.
    """
    evidence = row.get("evidence") or {}
    path = evidence.get("closure_path")
    if path:
        return [str(p) for p in path]
    decl = (row.get("unfaithful_decls") or [""])[0]
    labels = [str(o.get("label") or o.get("constant") or "")
              for o in (evidence.get("objects") or evidence.get("hits") or [])
              if isinstance(o, dict)]
    labels = [x for x in labels if x]
    if labels:
        return [f"{decl} -> {label}" for label in labels[:6]]
    return [str(b.get("decl") or b) for b in (row.get("bridge_decls") or [])] or [decl]


def disagreement(row: dict, companion: str) -> dict:
    """One detector row as a schema-v2 disagreement."""
    out = {k: row[k] for k in _CARRIED if k in row}
    out["repos_involved"] = [companion]
    out["faithful"] = list(row.get("faithful") or [])
    out["evidence"] = row.get("evidence") or {}
    out["suggested_fix"] = row.get("suggested_fix") or ""
    # A mechanical row has neither by construction; write them as null so a
    # reader sees the absence is deliberate rather than a dropped field.
    out["book_quote"] = row.get("book_quote")
    out["witness"] = row.get("witness")
    if row.get("kind") == vocab.PROXY:
        out["bridge"] = row.get("bridge") or "none"
        out["closure"] = _closure(row)
    if row.get("repos_involved"):
        out["repos_involved"] = [str(r) for r in row["repos_involved"]]
    return out


def documents(rows: Iterable[dict], *, companion: str, run_id: str,
              book_sha: str | None, items_pages: dict[str, int] | None = None) -> list[dict]:
    """Group flat detector rows into one gate-shaped document per book item."""
    pages = items_pages or {}
    by_item: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("item"):
            continue
        by_item.setdefault(str(row["item"]), []).append(row)
    out: list[dict] = []
    for item in sorted(by_item):
        out.append({
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "book_sha": book_sha,
            "item": item,
            # The detector located the item in items.json, not in the prose: it
            # knows the item exists and on which page, and never read the text.
            "book_located": True,
            "book_passage_quote": None,
            "printed_page": pages.get(item),
            "matches": [],
            "disagreements": [disagreement(r, companion) for r in by_item[item]],
            "coverage_notes": "",
            "produced_by": "cf detect-proxies",
        })
    return out


def slug(item: str) -> str:
    """``Definition 1.2.2`` -> ``definition-1-2-2`` (the corpus filename)."""
    keep = [c.lower() if c.isalnum() else "-" for c in item]
    name = "".join(keep)
    while "--" in name:
        name = name.replace("--", "-")
    return name.strip("-") or "item"


def write_corpus(docs: Sequence[dict], dest: str | Path, *, run_id: str,
                 companion: str, book_sha: str | None, counts: dict | None = None) -> dict:
    """Write one JSON per item into *dest* and return the names written.

    Idempotent: the same detector input rewrites the same bytes, so re-running
    ``cf detect-proxies --emit-corpus`` does not churn the corpus.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for doc in docs:
        name = slug(str(doc["item"])) + ".json"
        write_json(dest / name, doc, indent=1, sort_keys=True)
        written.append(name)
    return {
        "run_id": run_id,
        "repo": companion,
        "book_sha": book_sha,
        "destination": str(dest),
        "documents": len(written),
        "names": sorted(written),
        "counts": dict(counts or {}),
    }


def stale_files(dest: str | Path, keep: Sequence[str]) -> list[Path]:
    """Documents in *dest* this emission did not write (a shrunken detector run)."""
    from harvest._compat import NON_FINDING_FILES  # local: harvest is not a detect dep

    dest = Path(dest)
    if not dest.is_dir():
        return []
    keeping = set(keep)
    return [p for p in sorted(dest.glob("*.json"))
            if p.name not in keeping and p.name not in NON_FINDING_FILES]


__all__ = ["documents", "disagreement", "slug", "write_corpus", "stale_files", "read_json"]
