"""The deterministic gates of S1 (inventory), fail-closed.

New code for milestone M1 of docs/proposals/pdf-to-companion.md, sections 2.2
and 3.3.  Each gate returns ``{ok, ...}`` with the evidence behind the answer,
so the stage receipt can carry it and a failure names what to fix.

* **Slot completeness** (:func:`slot_completeness`): at least
  ``MIN_ITEM_COMPLETENESS`` of the numbering's own slots, pooled across kinds
  (``cflib.inference.item_completeness``).  It catches catastrophic failure
  only.
* **Slot contiguity** (:func:`slot_contiguity`): per section and per counter,
  every slot from 1 to the highest number present, or listed with a reason.
  A book may run more than one counter in a section (Tao numbers exercises
  apart from everything else: ``Definition 1.1.1`` and ``Exercise 1.1.1`` both
  exist), and pooling the counters would let one kind's slot hide another's
  gap.  ``cflib.inference.infer_counters`` reads the counters off the numbers
  themselves: two kinds share a counter exactly when no number occurs under
  both.
* **Exact selector check** (:func:`exact_selectors`): every ``exact`` of a
  TextQuoteSelector that refines ``page=N`` occurs verbatim in page N's text
  under NFKC and whitespace collapse, with no length floor and no neighbouring
  page accepted.  ``scripts/gate/verify_findings.py`` is deliberately not used:
  it normalises lossily and binds pages within two.
* **Unique ids** (:func:`unique_ids`): no id twice in ``items`` or
  ``locations``, checked before the document is canonicalised (which would
  keep both silently).
* **Selector length cap** (:func:`exact_cap`): section 4.6.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable, Mapping, Sequence

from cflib.inference import MIN_ITEM_COMPLETENESS, infer_counters, item_completeness
from pipeline import document


def slot_completeness(numbers: Iterable[str]) -> dict:
    detail = item_completeness(list(numbers))
    return {"ok": detail.get("completeness", 0.0) >= MIN_ITEM_COMPLETENESS,
            "min": MIN_ITEM_COMPLETENESS, **detail}


def _levels(number: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in str(number).split("."))
    except ValueError:
        return None


def _reason_for(reasons: Mapping[str, str], kinds: Sequence[str],
                number: str) -> str | None:
    """The key of a reason for this slot: ``<number>`` (any counter) or ``<Kind> <number>``."""
    if number in reasons:
        return number
    for kind in kinds:
        if f"{kind} {number}" in reasons:
            return f"{kind} {number}"
    return None


def slot_contiguity(kind_numbers: Mapping[str, Iterable[str]],
                    reasons: Mapping[str, str] | None = None,
                    pages: Sequence[str] | None = None) -> dict:
    """Every slot 1..max per (counter, section) is present or has a reason.

    ``pages`` (the page texts) only improves the report: for a missing slot it
    looks for the number at the start of a line under a word the source's kind
    list does not have (``Principle 1.3.7``), which is usually the answer.
    """
    reasons = dict(reasons or {})
    counters = infer_counters(kind_numbers)
    missing: list[dict] = []
    explained: list[dict] = []
    groups = 0
    used: set[str] = set()
    for kinds in counters:
        slots: dict[tuple, set[int]] = defaultdict(set)
        for kind in kinds:
            for number in kind_numbers[kind]:
                levels = _levels(number)
                if levels:
                    slots[levels[:-1]].add(levels[-1])
        for prefix in sorted(slots):
            groups += 1
            present = slots[prefix]
            for n in range(1, max(present) + 1):
                if n in present:
                    continue
                number = ".".join(str(p) for p in prefix + (n,))
                row = {"counter": "/".join(kinds), "number": number}
                seen = _seen_as(pages, number) if pages else []
                if seen:
                    row["seen_as"] = seen
                key = _reason_for(reasons, kinds, number)
                if key is None:
                    missing.append(row)
                else:
                    row["reason"] = str(reasons[key])
                    explained.append(row)
                    used.add(key)
    unused = sorted(set(reasons) - used)
    return {"ok": not missing, "counters": ["/".join(k) for k in counters], "groups": groups,
            "missing": missing, "explained": explained, "unused_reasons": unused}


def _seen_as(pages: Sequence[str], number: str) -> list[dict]:
    pattern = re.compile(r"(?:^|\n)[ \t]*([A-Z][A-Za-z]{2,20})[ \t]+" + re.escape(number)
                         + r"(?![\d])")
    out = []
    for index, text in enumerate(pages, start=1):
        for m in pattern.finditer(text):
            out.append({"page": index, "label": f"{m.group(1)} {number}"})
    return out[:3]


def exact_selectors(doc: dict, page_texts: Mapping[str, Sequence[str]]) -> dict:
    """Every page-bound ``exact`` occurs verbatim on its own page (normalised)."""
    failures = []
    checked = 0
    for loc, source, page, quote in document.page_quotes(doc):
        pages = page_texts.get(source)
        if pages is None:
            failures.append({"location": loc["id"], "page": page,
                             "problem": f"no page text for source {source!r}"})
            continue
        checked += 1
        exact = document.normalize(quote.get("exact") or "")
        if not exact:
            failures.append({"location": loc["id"], "page": page, "problem": "empty exact"})
        elif not 1 <= page <= len(pages):
            failures.append({"location": loc["id"], "page": page,
                             "problem": f"page {page} is outside 1..{len(pages)}"})
        elif exact not in document.normalize(pages[page - 1]):
            failures.append({"location": loc["id"], "page": page,
                             "problem": "exact does not occur on its page",
                             "exact": exact})
    return {"ok": not failures, "checked": checked, "failures": failures}


def errata_quotes(doc: dict, errata_texts: Mapping[str, str]) -> dict:
    """Every erratum item's ``quote.exact`` occurs in the rendered errata page."""
    failures = []
    checked = 0
    for item in doc.get("items") or []:
        text = errata_texts.get(item.get("source"))
        if text is None or "quote" not in item:
            continue
        checked += 1
        if document.normalize(item["quote"]["exact"]) not in document.normalize(text):
            failures.append({"item": item["id"], "problem": "quote does not occur in the "
                                                            "rendered errata page"})
    return {"ok": not failures, "checked": checked, "failures": failures}


def unique_ids(doc: dict) -> dict:
    duplicates = {}
    for key in ("items", "locations"):
        counts = Counter(rec.get("id") for rec in doc.get(key) or [])
        dup = sorted(i for i, n in counts.items() if n > 1)
        if dup:
            duplicates[key] = dup
    return {"ok": not duplicates, "duplicates": duplicates}


def exact_cap(doc: dict) -> dict:
    problems = document.over_cap(doc)
    return {"ok": not problems, "cap_words": document.EXACT_WORD_CAP, "problems": problems}
