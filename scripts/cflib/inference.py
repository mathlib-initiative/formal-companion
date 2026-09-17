"""Inference: the numbers a user used to have to type.

docs/design/multi-source-model.md R2 / S5.  Everything here is PURE -- text in,
numbers out -- so it is unit-testable without a PDF and its refusals are
reproducible:

* :func:`infer_offset`   printed page = PDF page - offset, from running heads
* :func:`infer_kinds`    the item kinds a source's own headings actually use
* :func:`infer_scope`    the chapters the COMPANIONS tag (scope is derived,
                         never typed)
* :func:`infer_errata_url` / :func:`confirm_errata`

The rule that makes this safe is that low confidence is a REFUSAL, not a
default: ``OffsetGuess.ok()`` is false below 0.9 and the caller must ask.  An
offset that is wrong by one is exactly the defect the whole audit exists to
catch (batch-1's anchor findings were judged against the wrong edition), so a
guessed 16 would be worse than no answer at all.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .errors import SourceError

#: below this, `cf onboard` / `cf sources refresh` refuse and name the flag
MIN_OFFSET_CONFIDENCE = 0.9

#: capitalised words that head a STRUCTURE, not an item; reported, never used
STRUCTURAL_KINDS = frozenset({
    "Chapter", "Section", "Subsection", "Part", "Figure", "Table", "Page",
    "Appendix", "Volume", "Step", "Case", "Equation",
})

#: ``Definition 1.2.3`` / ``Exercise 1.4.29.`` at the start of a line
HEADING = re.compile(r"^[ \t]*([A-Z][a-z]{2,15})\s+(\d+)\.(\d+)(?:\.(\d+))?\.?(?=[\s.(])",
                     re.MULTILINE)


@dataclass(frozen=True)
class OffsetGuess:
    """What the running heads say about ``printed = pdf_page - offset``."""

    offset: int
    confidence: float
    voting_pages: int
    total_pages: int
    histogram: dict[int, int] = field(default_factory=dict)
    declared: int | None = None

    def ok(self, threshold: float = MIN_OFFSET_CONFIDENCE) -> bool:
        return self.voting_pages > 0 and self.confidence >= threshold

    def describe(self) -> str:
        return (f"offset {self.offset} confidence {self.confidence:.4f} "
                f"({round(self.confidence * self.voting_pages)}/{self.voting_pages} running heads "
                f"of {self.total_pages} pages)")


def running_head_candidates(page_text: str, total_pages: int) -> set[int]:
    """Page numbers a page's first and last line could be announcing.

    Only the outermost token of the first and last non-empty line is considered:
    a running head is ``23   1.2. Lebesgue measure`` or ``... measure   23``, and
    anything further in is body text (an equation number, a year, a citation).
    """
    lines = [line.rstrip() for line in (page_text or "").splitlines() if line.strip()]
    if not lines:
        return set()
    out: set[int] = set()
    for line in (lines[0], lines[-1]):
        toks = line.split()
        if not toks:
            continue
        for tok in (toks[0], toks[-1]):
            tok = tok.strip(".,")
            if tok.isdigit():
                n = int(tok)
                if 0 < n <= max(total_pages, 1):
                    out.add(n)
    return out


def infer_offset(pages: Sequence[str], *, declared: int | None = None,
                 min_confidence: float = MIN_OFFSET_CONFIDENCE) -> OffsetGuess:
    """Mode of ``pdf_page - running_head`` over every page that carries one.

    One page votes at most once per delta, so a page whose header and footer
    both show ``23`` cannot outvote a page that shows it once.  ``declared`` (an
    ``offset_hint`` parsed out of repo prose, e.g. "PDF page = book page + 16")
    is a CROSS-CHECK, never the answer: agreement raises the confidence floor to
    0.95, disagreement is refused with both numbers quoted.
    """
    total = len(pages)
    deltas: list[set[int]] = []
    for i, page in enumerate(pages, 1):
        cands = running_head_candidates(page, total)
        deltas.append({i - c for c in cands})
    histogram = Counter(d for page_deltas in deltas for d in page_deltas)
    voting = sum(1 for d in deltas if d)
    if not histogram:
        return OffsetGuess(offset=declared or 0, confidence=0.0, voting_pages=0,
                           total_pages=total, histogram={}, declared=declared)
    # most votes wins; ties go to the smaller absolute offset, then the smaller value
    offset = sorted(histogram.items(), key=lambda kv: (-kv[1], abs(kv[0]), kv[0]))[0][0]
    agreeing = sum(1 for d in deltas if offset in d)
    confidence = agreeing / voting if voting else 0.0
    if declared is not None and declared != offset:
        raise SourceError(
            f"the text says the page offset is {offset} "
            f"({agreeing}/{voting} running heads) but the repo declares {declared}",
            hint="one of the two is about a different edition; check which PDF was "
                 "fingerprinted, then settle it with `cf sources set <id> offset=<n>`")
    if declared is not None and declared == offset:
        confidence = max(confidence, 0.95)
    return OffsetGuess(offset=offset, confidence=round(confidence, 4), voting_pages=voting,
                       total_pages=total, histogram=dict(histogram), declared=declared)


def infer_kinds(text: str, *, min_count: int = 3) -> tuple[dict[str, int], dict[str, int]]:
    """``(item kind counts, structural kind counts)`` from numbered headings.

    The item kinds of a source are what its own text numbers -- a book with
    ``Problem 2.3.1`` gets ``Problem`` without anyone editing a list -- and the
    structural words are reported separately so a caller can see that they were
    considered and dropped.
    """
    counts: Counter[str] = Counter()
    structural: Counter[str] = Counter()
    for m in HEADING.finditer(text or ""):
        word = m.group(1)
        (structural if word in STRUCTURAL_KINDS else counts)[word] += 1
    kinds = {k: n for k, n in counts.most_common() if n >= min_count}
    return kinds, dict(structural.most_common())


def infer_scope(rows: Iterable[Mapping], items_json: Mapping, *, source_id: str,
                min_items: int = 5, min_ratio: float = 0.2) -> dict:
    """Which chapters the COMPANIONS actually tag -- the audited scope.

    Scope is not read off the book and is not a flag: a chapter is in scope when
    the companions bound to this source tag at least ``min_items`` of it, or at
    least ``min_ratio`` of the items the book has in it.  This is the number
    every later stage narrows to, so `cf pair`, `cf plan-run` and
    `cf detect-proxies` cannot disagree about what "covered" means.
    """
    in_book: Counter[int] = Counter()
    for row in (items_json.get("items") or []):
        chapter = row.get("chapter")
        if chapter is not None:
            in_book[int(chapter)] += 1
    tagged: dict[int, set[str]] = {}
    for row in rows or ():
        if row.get("source_id") not in (None, "", source_id):
            continue
        item = str(row.get("item") or "")
        m = re.search(r"(\d+)\.(\d+)", item)
        if not m:
            continue
        tagged.setdefault(int(m.group(1)), set()).add(item)
    by_chapter: dict[str, dict] = {}
    chapters: list[int] = []
    for chapter in sorted(set(tagged) | set(in_book)):
        n_tagged = len(tagged.get(chapter, ()))
        n_book = in_book.get(chapter, 0)
        ratio = (n_tagged / n_book) if n_book else 0.0
        by_chapter[str(chapter)] = {"tagged": n_tagged, "in_book": n_book,
                                    "ratio": round(ratio, 4)}
        if n_tagged >= min_items or (n_book and ratio >= min_ratio):
            chapters.append(chapter)
    return {"chapters": chapters, "by_chapter": by_chapter, "method": "companion_tags",
            "min_items": min_items, "min_ratio": min_ratio}


def infer_errata_url(claims: Sequence, entry=None) -> tuple[str | None, float, str]:
    """``(url, confidence, method)`` for the live errata page.

    Ranked: the registry's errata_url for the resolved sha (1.0), a book page the
    repo itself names (0.8), the download URL's parent page (0.5).  The fetch
    itself is curl + raw HTML (never a summarising fetch: one of those already
    dropped Definition 1.5.11 from a hand-written errata file).
    """
    if entry is not None and getattr(entry, "errata_url", None):
        return str(entry.errata_url), 1.0, "registry"
    for claim in claims or ():
        url = getattr(claim, "book_page_url", "")
        if url:
            return str(url), 0.8, "repo_book_page"
    download = None
    if entry is not None:
        download = getattr(entry, "download_url", None) or getattr(entry, "book_page_url", None)
    if not download:
        for claim in claims or ():
            for url in getattr(claim, "urls", ()) or ():
                if str(url).lower().endswith(".pdf"):
                    download = str(url)
                    break
            if download:
                break
    if download:
        parent = str(download).rsplit("/", 1)[0] + "/"
        return parent, 0.5, "download_url_parent"
    return None, 0.0, "none"


def confirm_errata(raw_html: str) -> bool:
    """Does the fetched page actually say ``errata``?

    A page that does not is kept (it may still be the book page) but its
    confidence is halved and ``meta.json.warnings`` records
    ``errata_url_unconfirmed``.
    """
    return "errata" in (raw_html or "").lower()
