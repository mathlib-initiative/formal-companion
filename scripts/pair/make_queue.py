"""queue.json and targets.<name>.json: what gets a task, and how urgent.

Ported from: recovered/b174aa13_L5589_Bash.txt (the queue_args builder: page
hints from each repo's ``page_anchor``, ranges capped at +3 pages, ``+16`` to
reach PDF pages, first six pages kept).  Generalised per docs/design/stage_map.md
Stage 3-4:

* the offset and the printed-page bounds come from the SOURCE's ``meta.json``
  (``sources/<source_id>/``), not ``+16`` / ``1..260``, and every row carries
  the ``source_id`` it was built against;
* page anchors are parsed with a SEARCH, not a match-at-position-0: the original
  ``re.match(r'(\\d+)...')`` silently dropped every ``book p. 135`` anchor, i.e.
  the whole attribution-table convention (97 of 225 queue items had page hints;
  the same data yields 179 read tolerantly);
* each queue row also carries the authoritative page from that source's
  ``items.json`` (``book_pdf_page``), so a task is not limited to what the
  companions claim;
* ``targets.<name>.json`` (never committed anywhere before) lists every item a
  companion formalises with its peers, its single-source flag and a priority.
"""

from __future__ import annotations

import os
from typing import Mapping, Sequence

from .compat import DEFAULT_KINDS, item_sort_key, parse_item, parse_pages, utcnow, write_json

#: priority rule, recorded in the artifact so a reader can re-derive it
PRIORITY_RULE = (
    "max = single-source and not Exercise/Remark; "
    "high = shared Definition/Exercise in a late section; "
    "medium = any other numbered item; low = In-text and Remark"
)


def anchor_pages(rows: Sequence[Mapping], *, span: int = 3, lo: int = 1, hi: int = 10000) -> set:
    """Printed pages named by a set of statement rows' page anchors."""
    pages: set[int] = set()
    for row in rows:
        pages.update(parse_pages(row.get("page_anchor"), span=span, lo=lo, hi=hi))
    return pages


def build_queue(
    pairs: Sequence[Mapping],
    *,
    source_id: str = "",
    offset: int = 0,
    max_pages: int = 6,
    span: int = 3,
    printed_max: int = 10000,
    book_items: Mapping[str, dict] | None = None,
) -> list[dict]:
    """``[{source_id, item, repos, pdf_pages, book_pdf_page}]`` per paired item."""
    queue = []
    for pair in pairs:
        repos = list(pair.get("repos") or [k for k in pair if isinstance(pair.get(k), list)])
        pages: set[int] = set()
        for repo in repos:
            pages |= anchor_pages(pair.get(repo) or [], span=span, lo=1, hi=printed_max)
        entry = {
            "source_id": source_id or str(pair.get("source_id") or ""),
            "item": pair["item"],
            "repos": repos,
            "pdf_pages": sorted(p + offset for p in pages)[:max_pages],
        }
        book_row = (book_items or {}).get(pair["item"])
        entry["book_pdf_page"] = book_row["pdf_page"] if book_row else None
        entry["book_page"] = book_row["page"] if book_row else None
        queue.append(entry)
    queue.sort(key=lambda e: item_sort_key(e["item"]))
    return queue


def _priority(
    key: str,
    kind: str,
    *,
    single_source: bool,
    section: int | None,
    late_from: int | None,
) -> str:
    if kind == "In-text" or kind == "Remark":
        return "low"
    if single_source and kind not in ("Exercise", "Remark"):
        return "max"
    if (
        not single_source
        and kind in ("Definition", "Exercise")
        and late_from is not None
        and section is not None
        and section >= late_from
    ):
        return "high"
    return "medium"


def late_section_threshold(book_items: Mapping[str, dict] | None, chapters=None) -> int | None:
    """Sections in the last third of the chapter count as 'late' (tend-run S2)."""
    if not book_items:
        return None
    sections = {
        row.get("section")
        for row in book_items.values()
        if row.get("section") is not None and (not chapters or row.get("chapter") in set(chapters))
    }
    if not sections:
        return None
    return max(sections) - 2


def build_targets(
    index: Mapping[str, Mapping[str, list]],
    name: str,
    *,
    source_id: str = "",
    repos: Sequence[str],
    offset: int = 0,
    span: int = 3,
    max_pages: int = 6,
    printed_max: int = 10000,
    book_items: Mapping[str, dict] | None = None,
    kinds: Sequence[str] = DEFAULT_KINDS,
    chapters: Sequence[int] | None = None,
    late_from: int | None = None,
) -> list[dict]:
    """Every item ``name`` formalises OF THIS SOURCE: peers, single-source, priority.

    ``chapters`` is the source's audited scope (``sources.<id>.chapters``): the
    "late section" band that decides a `high` priority is computed inside it, so
    a workspace whose second source has ten chapters does not drag the first
    source's threshold with it.
    """
    if late_from is None:
        late_from = late_section_threshold(book_items, chapters)
    out = []
    for key in sorted(index, key=item_sort_key):
        rows = index[key].get(name)
        if not rows:
            continue
        peers = [r for r in repos if r != name and index[key].get(r)]
        parsed = parse_item(key, kinds)
        kind = parsed.kind if parsed else key.split()[0]
        section = parsed.section if parsed else None
        single = not peers
        book_row = (book_items or {}).get(key)
        if book_row is None and parsed is not None and parsed.page is not None:
            # In-text items are keyed BY their printed page ("In-text p.23")
            book_row = {"page": parsed.page, "pdf_page": parsed.page + offset}
        pages = sorted(
            p + offset for p in anchor_pages(rows, span=span, lo=1, hi=printed_max)
        )[:max_pages]
        out.append(
            {
                "source_id": source_id,
                "item": key,
                "kind": kind,
                "number": parsed.number if parsed else "",
                "decls": [r.get("decl") for r in rows],
                "raw_labels": sorted({r.get("item_raw") for r in rows if r.get("item_raw")}),
                "peers": peers,
                "single_source": single,
                "priority": _priority(
                    key, kind, single_source=single, section=section, late_from=late_from
                ),
                "sorry": any(bool(r.get("sorry")) for r in rows),
                "pdf_pages": pages,
                "book_pdf_page": book_row["pdf_page"] if book_row else None,
                "book_page": book_row["page"] if book_row else None,
                "in_book": book_row is not None,
            }
        )
    return out


def write_queue(
    out_dir: str,
    queue: Sequence[dict],
    targets: Mapping[str, Sequence[dict]],
    *,
    source_id: str = "",
    source_sha: str = "",
    offset: int = 0,
) -> dict:
    """Write ``pairs/<source_id>/queue.json`` + one ``targets.<name>.json`` each."""
    os.makedirs(out_dir, exist_ok=True)
    write_json(
        os.path.join(out_dir, "queue.json"),
        {
            "created": utcnow(),
            "source_id": source_id,
            "source_sha": source_sha,
            "offset": offset,
            "count": len(queue),
            "with_page_hints": sum(1 for q in queue if q["pdf_pages"]),
            "items": list(queue),
        },
    )
    summary = {}
    for name, rows in targets.items():
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["priority"]] = counts.get(row["priority"], 0) + 1
        write_json(
            os.path.join(out_dir, "targets.%s.json" % name),
            {
                "created": utcnow(),
                "companion": name,
                "source_id": source_id,
                "source_sha": source_sha,
                "priority_rule": PRIORITY_RULE,
                "count": len(rows),
                # distinct docstring tags behind those items: In-text tags collapse
                # onto one key per printed page, so a task per item is fewer tasks
                # than a task per tag (plan/make_tasks may expand by `decls`).
                "raw_labels": len({lbl for r in rows for lbl in r.get("raw_labels", [])}),
                "single_source": sum(1 for r in rows if r["single_source"]),
                "shared": sum(1 for r in rows if not r["single_source"]),
                "by_priority": counts,
                "items": list(rows),
            },
        )
        summary[name] = {
            "targets": len(rows),
            "raw_labels": len({lbl for r in rows for lbl in r.get("raw_labels", [])}),
            "single_source": sum(1 for r in rows if r["single_source"]),
            "shared": sum(1 for r in rows if not r["single_source"]),
            "by_priority": counts,
        }
    return {"queue": len(queue), "targets": summary}
