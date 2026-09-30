"""Recall of an S1 inventory against a known annotation document.

New code for milestone M1 of docs/proposals/pdf-to-companion.md, section 5.2
(M1's acceptance tests): the numbered items of a reference are matched on
``(page, kind, number)``, its passages one to one, and every miss is listed
with its page and label, because a miss is a finding to adjudicate, not a
number to hide.  Pages are compared on the physical basis; a printed page is
converted with the reference source's ``printed_page_offset``.

The reference is either an annotation document (:func:`numbered_truth`,
:func:`passage_truth`) or the derived truth table ``pipeline/derive_truth.py``
writes from one (:func:`table_truth`), which keeps only ``{kind, number,
page}`` and ``{page, near}`` and so carries no quote.  A reference passage
with a quote is recovered only by one of ours on its page whose quote overlaps
it (:func:`quotes_overlap`); one without a quote -- every passage of a derived
table -- by the one-to-one page rule, and the result says which rule it used.

A reference item whose kind is a structural word (``Equation``: a displayed
equation number, not a heading) or whose kind the producer supplied
(``kind_source: producer``: a label the book never prints) is not an item a
heading extractor can find; it is reported under ``excluded``, never silently
dropped.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import ids
from cflib.inference import STRUCTURAL_KINDS
from pipeline import document

#: the words two quotes must share in one run to overlap (fewer when a quote is shorter)
OVERLAP_WORDS = 4


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


def numbered_rows(doc: dict, sha256: str) -> list[dict]:
    """``{id, kind, number, label, pages, producer_kind}`` for every numbered item on the source."""
    src = _source(doc, sha256)
    if src is None:
        return []
    pages = item_pages(doc, sha256)
    out = []
    for item in doc.get("items") or []:
        if item.get("source") != src["id"] or item.get("form") != "numbered":
            continue
        out.append({"id": item["id"], "kind": item["kind"], "number": item["number"],
                    "label": item.get("label") or f"{item['kind']} {item['number']}",
                    "pages": sorted(pages.get(item["id"], set())),
                    "producer_kind": item.get("kind_source") == "producer"})
    return out


def _split_truth(rows: Iterable[dict], chapters: Iterable[int]) -> tuple[dict, list[dict]]:
    structural = {k.lower() for k in STRUCTURAL_KINDS}
    chapters = list(chapters)
    truth: dict[tuple[str, str], dict] = {}
    excluded: list[dict] = []
    for r in rows:
        if not _in_chapters(r["number"], chapters):
            continue
        row = {"id": r["id"], "label": r["label"], "pages": list(r["pages"])}
        if r["kind"].lower() in structural or r.get("producer_kind"):
            excluded.append({**row, "why": "not a printed heading kind"})
            continue
        truth[(r["kind"], r["number"])] = row
    return truth, excluded


def numbered_truth(doc: dict, sha256: str, chapters: Iterable[int]) -> tuple[dict, list[dict]]:
    """``({(kind, number): {id, label, pages}}, excluded)`` for the reference's numbered items."""
    return _split_truth(numbered_rows(doc, sha256), chapters)


def table_truth(table: Mapping[str, Any], chapters: Iterable[int]
                ) -> tuple[dict, list[dict], list[dict]]:
    """``(numbered truth, excluded, passages)`` from a derived truth table.

    The same shapes :func:`numbered_truth` and :func:`passage_truth` return,
    with the passages unbounded (the caller cuts them at the scope's last
    page) and no quote on any of them.
    """
    rows = [{"id": ids.item_label_id(r["kind"], r["number"]), "kind": r["kind"],
             "number": r["number"], "label": f"{r['kind']} {r['number']}",
             "pages": [r["page"]] if r.get("page") is not None else []}
            for r in table.get("numbered") or []]
    truth, excluded = _split_truth(rows, chapters)
    passages = [{"id": f"passage-{n}", "page": int(r["page"]), "title": "",
                 "near": sorted(r.get("near") or [])}
                for n, r in enumerate(table.get("passages") or [], start=1)]
    return truth, excluded, sorted(passages, key=lambda r: (r["page"], r["id"]))


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
    quotes = item_quotes(doc, src["id"])
    out = []
    for item in doc.get("items") or []:
        if item.get("source") != src["id"] or item.get("form") != "passage":
            continue
        if not item.get("pages"):
            continue
        first, _last = _physical(item["pages"], offset)
        if first > last_page:
            continue
        row = {"id": item["id"], "page": first, "title": item.get("title", ""),
               "near": sorted((item.get("relations") or {}).get("near") or [])}
        if quotes.get(item["id"]):
            row["quote"] = quotes[item["id"]]
        out.append(row)
    return sorted(out, key=lambda r: (r["page"], r["id"]))


def item_quotes(doc: dict, source_id: str | None = None) -> dict[str, str]:
    """An item's quote: its own ``quote.exact``, else its first location's TextQuoteSelector."""
    out: dict[str, str] = {}
    for item in doc.get("items") or []:
        exact = (item.get("quote") or {}).get("exact")
        if exact and (source_id is None or item.get("source") == source_id):
            out[item["id"]] = document.normalize(exact)
    for loc in doc.get("locations") or []:
        target = loc.get("target") or {}
        if source_id is not None and target.get("source") != source_id:
            continue
        for sel in target.get("selector") or []:
            quote = sel.get("refinedBy") if sel.get("type") != "TextQuoteSelector" else sel
            if isinstance(quote, dict) and quote.get("type") == "TextQuoteSelector" \
                    and quote.get("exact"):
                for item_id in loc.get("items") or []:
                    out.setdefault(item_id, document.normalize(quote["exact"]))
                break
    return out


def _words(text: str) -> list[str]:
    return document.normalize(text).casefold().split()


def quotes_overlap(a: str, b: str, min_words: int = OVERLAP_WORDS) -> bool:
    """True when the two quotes share a run of ``min_words`` words (fewer for a shorter quote).

    Compared after the text-layer normalisation and case folding; a quote of
    fewer words than ``min_words`` must occur whole in the other.
    """
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    n = min(min_words, len(wa), len(wb))
    grams = {tuple(wa[i:i + n]) for i in range(len(wa) - n + 1)}
    return any(tuple(wb[i:i + n]) in grams for i in range(len(wb) - n + 1))


def passage_recall(ours: dict, truth: list[dict]) -> dict[str, Any]:
    """Match the reference's passages one to one against ours, and list every miss.

    Each of our passages recovers at most one reference passage.

    * A reference passage **with a quote** is recovered only by one of ours on
      the same physical page whose quote overlaps it (:func:`quotes_overlap`,
      the proposal's page-plus-quote test).  One that only finds a free
      passage of ours on its page is a ``page_only`` match: listed apart, not
      counted as found, because page coincidence is not recovery.
    * A reference passage **without a quote** (every passage of a derived
      truth table) can only be matched by the page rule: one of ours on its
      page, else one beside its item (the same ``near`` item, within one
      page).  ``rule`` then says ``page``, and the figure is an upper bound on
      recovery, not a quote-verified recall.
    """
    mine = [i for i in ours.get("items") or [] if i.get("form") == "passage"
            and i.get("pages")]
    quotes = item_quotes(ours)
    free = list(range(len(mine)))

    def page_of(k: int) -> int:
        return int(mine[k]["pages"]["first"])

    def id_of(k: int) -> str:
        return str(mine[k].get("id") or f"ours[{k}]")

    def take(pred) -> int | None:
        for k in free:
            if pred(k):
                free.remove(k)
                return k
        return None

    quoted = [r for r in truth if r.get("quote")]
    unquoted = [r for r in truth if not r.get("quote")]
    by_quote, by_page, beside, page_only, misses = [], [], [], [], []
    # quote-verified first, so a page coincidence never takes the passage a quote needs
    unmatched_quoted = []
    for ref in quoted:
        k = take(lambda k: page_of(k) == ref["page"] and id_of(k) in quotes
                 and quotes_overlap(quotes[id_of(k)], ref["quote"]))
        if k is None:
            unmatched_quoted.append(ref)
        else:
            by_quote.append({"reference": ref["id"], "ours": id_of(k)})
    for ref in unquoted:
        k = take(lambda k: page_of(k) == ref["page"])
        if k is not None:
            by_page.append({"reference": ref["id"], "ours": id_of(k)})
    for ref in unquoted:
        if any(m["reference"] == ref["id"] for m in by_page):
            continue
        k = take(lambda k: abs(page_of(k) - ref["page"]) <= 1 and set(ref["near"]) & set(
            (mine[k].get("relations") or {}).get("near") or []))
        if k is None:
            misses.append(ref)
        else:
            beside.append({"reference": ref["id"], "ours": id_of(k)})
    for ref in unmatched_quoted:
        k = take(lambda k: page_of(k) == ref["page"])
        if k is not None:
            page_only.append({"reference": ref["id"], "ours": id_of(k),
                              "page": ref["page"]})
        misses.append(ref)
    found = len(by_quote) + len(by_page) + len(beside)
    total = len(truth)
    rule = "quote" if not unquoted else "page" if not quoted else "mixed"
    notes = {
        "quote": "one to one, page plus quote overlap",
        "page": "one to one by page or beside the item: the reference has no quotes, so "
                "this is an upper bound on recovery, not a quote-verified recall",
        "mixed": "one to one: quoted references by page plus quote overlap, the rest by "
                 "page or beside the item (an upper bound for those)",
    }
    return {"total": total, "found": found, "recall": round(found / total, 4) if total else None,
            "rule": rule, "note": notes[rule],
            "matched_by_quote": len(by_quote), "matched_by_page": len(by_page),
            "matched_beside_item": len(beside), "page_only": len(page_only),
            "page_only_matches": page_only, "misses": misses, "ours": len(mine)}
