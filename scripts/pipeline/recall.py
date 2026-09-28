"""Recall of an S1 inventory against a known annotation document.

New code for milestone M1 of docs/proposals/pdf-to-companion.md, section 5.2
(M1's acceptance tests): the numbered items of a reference document are matched
on ``(page, kind, number)``, its passages by page and neighbour, and every miss
is listed with its page and label, because a miss is a finding to adjudicate,
not a number to hide.  Pages are compared on the physical basis; a printed page
is converted with the reference source's ``printed_page_offset``.

A reference item whose kind is a structural word (``Equation``: a displayed
equation number, not a heading) or whose kind the producer supplied
(``kind_source: producer``: a label the book never prints) is not an item a
heading extractor can find; it is reported under ``excluded``, never silently
dropped.
"""
from __future__ import annotations

from typing import Any, Iterable

from cflib.inference import STRUCTURAL_KINDS


def _source(doc: dict, sha256: str) -> dict | None:
    return next((s for s in doc.get("sources") or [] if s.get("sha256") == sha256), None)


def _physical(pages: dict, offset: int) -> tuple[int, int]:
    first = int(pages["first"])
    last = int(pages.get("last", first))
    if pages.get("basis") == "printed":
        return first + offset, last + offset
    return first, last


def item_pages(doc: dict, sha256: str) -> dict[str, set[int]]:
    """Physical pages per item id, from ``item.pages`` and the item's PDF locations."""
    src = _source(doc, sha256)
    if src is None:
        return {}
    offset = int(src.get("printed_page_offset") or 0)
    out: dict[str, set[int]] = {}
    for item in doc.get("items") or []:
        if item.get("source") == src["id"] and item.get("pages"):
            first, _last = _physical(item["pages"], offset)
            out.setdefault(item["id"], set()).add(first)
    for loc in doc.get("locations") or []:
        if (loc.get("target") or {}).get("source") != src["id"] or not loc.get("pages"):
            continue
        first, _last = _physical(loc["pages"], offset)
        for item_id in loc.get("items") or []:
            out.setdefault(item_id, set()).add(first)
    return out


def _in_chapters(number: str, chapters: Iterable[int]) -> bool:
    head = str(number).split(".")[0]
    return head.isdigit() and int(head) in set(chapters)


def numbered_truth(doc: dict, sha256: str, chapters: Iterable[int]) -> tuple[dict, list[dict]]:
    """``({(kind, number): {id, label, pages}}, excluded)`` for the reference's numbered items."""
    src = _source(doc, sha256)
    pages = item_pages(doc, sha256)
    structural = {k.lower() for k in STRUCTURAL_KINDS}
    truth: dict[tuple[str, str], dict] = {}
    excluded: list[dict] = []
    for item in doc.get("items") or []:
        if src is None or item.get("source") != src["id"] or item.get("form") != "numbered":
            continue
        if not _in_chapters(item["number"], chapters):
            continue
        row = {"id": item["id"], "label": item.get("label") or f"{item['kind']} {item['number']}",
               "pages": sorted(pages.get(item["id"], set()))}
        if item["kind"].lower() in structural or item.get("kind_source") == "producer":
            excluded.append({**row, "why": "not a printed heading kind"})
            continue
        truth[(item["kind"], item["number"])] = row
    return truth, excluded


def numbered_recall(ours: dict, truth: dict) -> dict[str, Any]:
    """Match our numbered items on (kind, number) and first physical page; list every miss.

    The reference's page is the first page any of its locations shows (an item
    set across a page break has a region on each).
    """
    mine = {(i["kind"], i["number"]): i for i in ours.get("items") or []
            if i.get("form") == "numbered"}
    hits, misses = 0, []
    for key, ref in sorted(truth.items(), key=lambda kv: (kv[1]["pages"][:1], kv[0])):
        got = mine.get(key)
        if got is None:
            misses.append({**ref, "why": "no such item in the inventory"})
            continue
        page = got["pages"]["first"]
        if ref["pages"] and page != ref["pages"][0]:
            misses.append({**ref, "why": f"found on physical page {page}, the reference "
                                         f"starts it on {ref['pages'][0]}"})
            continue
        hits += 1
    total = len(truth)
    extra = sorted(f"{k} {n}" for (k, n) in mine if (k, n) not in truth)
    return {"total": total, "found": hits, "recall": round(hits / total, 4) if total else None,
            "misses": misses, "not_in_reference": extra}


def passage_truth(doc: dict, sha256: str, last_page: int) -> list[dict]:
    """The reference's passages on the source, up to physical page ``last_page``."""
    src = _source(doc, sha256)
    if src is None:
        return []
    offset = int(src.get("printed_page_offset") or 0)
    out = []
    for item in doc.get("items") or []:
        if item.get("source") != src["id"] or item.get("form") != "passage":
            continue
        if not item.get("pages"):
            continue
        first, _last = _physical(item["pages"], offset)
        if first > last_page:
            continue
        out.append({"id": item["id"], "page": first, "title": item.get("title", ""),
                    "near": sorted((item.get("relations") or {}).get("near") or [])})
    return sorted(out, key=lambda r: (r["page"], r["id"]))


def passage_recall(ours: dict, truth: list[dict]) -> dict[str, Any]:
    """A reference passage is recovered by one of ours on its page, or beside its item.

    "Beside its item": our passage names the same ``near`` item and sits within
    one page of it.  This is the proposal's "near the right item and page".
    """
    mine = [i for i in ours.get("items") or [] if i.get("form") == "passage"
            and i.get("pages")]
    by_page = {i["pages"]["first"] for i in mine}
    near = {}
    for i in mine:
        for n in (i.get("relations") or {}).get("near") or []:
            near.setdefault(n, set()).add(i["pages"]["first"])
    hits, misses = 0, []
    for ref in truth:
        ok = ref["page"] in by_page or any(
            abs(p - ref["page"]) <= 1 for n in ref["near"] for p in near.get(n, ()))
        if ok:
            hits += 1
        else:
            misses.append(ref)
    total = len(truth)
    return {"total": total, "found": hits, "recall": round(hits / total, 4) if total else None,
            "misses": misses, "ours": len(mine)}
