"""The MECHANICAL page-anchor differential: companion anchors vs items.json.

Ported from: recovered/b174aa13_L5762_Bash.txt part (C), which located every
numbered item in two editions and flagged the items whose anchor in
tao-measure-lean matched the 2011 draft page rather than the published page.

Generalised per design/critique.json:

* PAGE_ANCHOR is a MECHANICAL kind -- never an LLM finding.  22 of the 40
  differential-flagged items in batch 1 were pagination, which is exactly the
  work a regex does for free (HANDOFF section 2);
* the comparison is against the pinned edition's ``items.json`` (one edition,
  stamped with ``book_sha``), not draft-vs-official;
* rows are emitted in the findings v2 shape with a stable ``finding_id`` so the
  corpus, the ledger and ``route`` treat them like any other finding -- they are
  routed as low-severity mechanical rows, not silently withdrawn (32 anchors
  were genuinely corrected in PR #5).
"""

from __future__ import annotations

import os
from typing import Mapping, Sequence

from .compat import DEFAULT_KINDS, finding_id, item_sort_key, parse_item, parse_pages, utcnow, write_json
from .compare import row_item

KIND = "PAGE_ANCHOR"
SEVERITY = "low"


def claimed_pages(anchor) -> list[int]:
    """Printed pages a page anchor claims ("23", "23-25", "book p. 135")."""
    return parse_pages(anchor, span=10)


def classify(delta: int, offset: int) -> str:
    """Name the shape of a mismatch so a reader can fix a whole class at once."""
    if delta == offset:
        return "pdf_page_not_printed_page"
    if delta == -offset:
        return "printed_page_where_pdf_expected"
    if abs(delta) == 1:
        return "adjacent_page"
    return "other_edition_or_typo"


def check(
    items_index: Mapping[str, Mapping],
    tables: Mapping[str, Sequence[Mapping]],
    *,
    book_sha: str,
    offset: int = 0,
    run_id: str = "anchor-check",
    kinds: Sequence[str] = DEFAULT_KINDS,
    tolerance: int = 0,
) -> dict:
    """Check every anchored row of every companion; returns rows + summary.

    ``tolerance`` widens the accepted window (an item can straddle a page
    break); mismatches inside +/-1 are emitted with ``advisory: true`` so the
    routing stage can hold them back without losing them.
    """
    rows: list[dict] = []
    summary = {
        "created": utcnow(),
        "run_id": run_id,
        "book_sha": book_sha,
        "offset": offset,
        "tolerance": tolerance,
        "repos": {},
    }
    for repo, statements in tables.items():
        checked = anchored = ok = unknown = intext = 0
        grouped: dict[str, list[dict]] = {}
        for row in statements:
            checked += 1
            anchor = row.get("page_anchor")
            pages = claimed_pages(anchor)
            if not pages:
                continue
            anchored += 1
            parsed = parse_item(row_item(row), kinds)
            if parsed is not None and parsed.is_intext:
                # an In-text row's anchor IS its key: there is nothing to check it against
                intext += 1
                continue
            if parsed is None or parsed.key not in items_index:
                unknown += 1
                continue
            official = items_index[parsed.key].get("page")
            if official is None:
                unknown += 1
                continue
            if any(abs(p - official) <= tolerance for p in pages):
                ok += 1
                continue
            grouped.setdefault(parsed.key, []).append(
                {
                    "decl": row.get("decl"),
                    "file": row.get("file"),
                    "line": row.get("line"),
                    "anchor_raw": anchor,
                    "claimed_pages": pages,
                }
            )
        for item in sorted(grouped, key=item_sort_key):
            hits = grouped[item]
            entry = items_index[item]
            official = entry["page"]
            deltas = sorted({p - official for h in hits for p in h["claimed_pages"]})
            decls = sorted({"%s:%s" % (repo, h["decl"]) for h in hits if h.get("decl")})
            pattern = classify(deltas[0], offset) if deltas else "unknown"
            advisory = all(abs(d) <= 1 for d in deltas)
            rows.append(
                {
                    "finding_id": finding_id(run_id, item, KIND, decls),
                    "run_id": run_id,
                    "item": item,
                    "kind": KIND,
                    "severity": SEVERITY,
                    "repo": repo,
                    "faithful": [],
                    "unfaithful_decls": decls,
                    "mechanical": True,
                    "advisory": advisory,
                    "concept": "page anchor",
                    "reasoning": (
                        "%s anchors %s at printed page(s) %s; the pinned edition prints it on "
                        "page %d (PDF page %d). Pattern: %s."
                        % (
                            repo,
                            item,
                            ", ".join(str(p) for h in hits for p in h["claimed_pages"]),
                            official,
                            entry["pdf_page"],
                            pattern,
                        )
                    ),
                    "book_quote": None,
                    "witness": None,
                    "book_sha": book_sha,
                    "detail": {
                        "official_page": official,
                        "official_pdf_page": entry["pdf_page"],
                        "book_line": entry.get("line"),
                        "deltas": deltas,
                        "pattern": pattern,
                        "occurrences": entry.get("occurrences", []),
                        "anchors": hits,
                    },
                }
            )
        summary["repos"][repo] = {
            "rows": checked,
            "anchored": anchored,
            "ok": ok,
            "intext_skipped": intext,
            "unknown_item": unknown,
            "mismatched_items": len(grouped),
            "mismatched_rows": sum(len(v) for v in grouped.values()),
        }
    summary["findings"] = len(rows)
    summary["advisory"] = sum(1 for r in rows if r["advisory"])
    return {"summary": summary, "rows": rows}


def write_check(out_dir: str, result: Mapping) -> str:
    """Write ``pairs/anchor-check.json`` (summary + PAGE_ANCHOR rows)."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "anchor-check.json")
    write_json(path, {"summary": result["summary"], "rows": result["rows"]})
    return path
