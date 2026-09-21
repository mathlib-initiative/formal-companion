"""Put a redacted quote's text back, from the reader's OWN copy of the source.

New code.  A redacted row carries the page, the normalised length and the sha256
of the quote (:mod:`share.redact`), so a person holding the same edition can
recover the sentence without it ever having been shared: slide a window of that
length over the recorded page (plus or minus the quote gate's
``PAGE_BIND_WINDOW``) and keep the window whose sha256 matches.

Three outcomes, all counted and none of them silent:

* **hydrated** -- exactly one passage of the holder's text hashes to the record;
* **not-found** -- none does, which means the holder's edition is not the one the
  finding was written against, or the quote was not copied verbatim out of the
  pinned text;
* **ambiguous** -- two passages that differ in their raw spelling both hash to it,
  so which one the finding meant cannot be decided mechanically.

MEASURED over the baseline corpus's 241 distinct quotes, against the published
edition's pinned text (those v1 rows carry no ``source_id`` of their own, so there
is one text to match them against): 214 hydrate, 25 are not found (a quote
whose mathematics the worker tidied -- ``∫_{R^d} f(x) dx`` for the extraction's
``Rd f (x) dx`` -- is the recurring shape), and 2 are ambiguous.  Only 25 of the
214 come back byte-identical to the quote as the worker wrote it: the pinned text
wraps the same sentence over two lines, which is exactly why the digest is over
the normalised form and why what comes back is the HOLDER's spelling.

The window is a CHARACTER length, not a word count, so a window may begin or end
inside one of the page's whitespace-separated tokens: a worker quoting "... as in
the Lebesgue case" stops one colon short of the book's ``case:``, and whole-token
windows hydrate 284 of the 347 quotes where character windows hydrate 312.  A
record written by ``cf route --redact-quotes`` carries only the RAW length, so for
those the search tries every length a whitespace collapse can account for
(:data:`MAX_COLLAPSE`) and says so when that would cost too much.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Sequence

from gate.verify_findings import PAGE_BIND_WINDOW, BookText, norm

from .redact import is_redacted, normalise_quote

#: how many characters a whitespace collapse may have removed from a quote whose
#: record carries no ``norm_chars``.  ``route._redact`` stores ``len(quote)`` of
#: the raw string: every run of whitespace it collapses shrinks the normalised
#: form, and a single newline per line shrinks it by nothing at all, so a quote of
#: a few lines is within a handful of characters of its raw length.  32 covers a
#: quote whose source spacing was badly mangled and keeps the search bounded.
MAX_COLLAPSE = 32
#: how many pages a MULTI-LENGTH sweep (a record with no ``norm_chars`` and no
#: first words, i.e. one ``route._redact`` wrote) may scan before it gives up and
#: names the command that records both.  A single-length sweep has no such cap.
MAX_SWEEP_PAGES = 8

_TOKEN = re.compile(r"\S+")

#: status values, so a caller counts the same three buckets this module reports
HYDRATED = "hydrated"
NOT_FOUND = "not-found"
AMBIGUOUS = "ambiguous"


def split_pages(raw: str) -> list[str]:
    """The pinned text page by page.

    ``pdftotext`` writes a form feed between pages, which is the only page
    boundary the workspace has; a source with none is one page, and the search
    then runs over the whole text.
    """
    return str(raw or "").split("\f")


def locate_pages(raw: str, quote: str) -> list[int]:
    """The 1-based PDF pages of *raw* carrying *quote*.

    Uses the quote gate's own lossy normalisation (:func:`gate.verify_findings.
    norm`), which is what binds a quote to a page everywhere else in the skill, so
    an exporter records the page the gate would have named.  The digest the
    hydration matches on is the stricter one -- a page recorded here is where to
    look, never evidence that the passage is the right one.
    """
    book = BookText.from_text(str(raw or ""), operators=False)
    return book.pages_matching(norm(quote))


class _Page:
    """One page of the pinned text, in the digest's normalisation and in its own.

    ``norm`` is the page as :func:`share.redact.normalise_quote` would spell it,
    and ``offsets[k]`` is the index in ``raw`` of ``norm[k]`` -- so a window that
    hashes to a record can be handed back as the holder's OWN bytes, and a window
    may begin or end inside one of the page's whitespace-separated tokens.  That
    last part is load-bearing: measured over the 347 quotes of the baseline
    corpus, matching whole tokens only hydrates 284 of them, because a worker
    quoting "... as in the Lebesgue case" stops one colon short of the book's
    "case:" token.
    """

    def __init__(self, number: int, raw: str):
        self.number = number
        self.raw = raw
        parts: list[str] = []
        offsets: list[int] = []
        for k, match in enumerate(_TOKEN.finditer(raw)):
            a, b = match.start(), match.end()
            if k:
                parts.append(" ")
                offsets.append(a - 1)          # inside the whitespace run before it
            lowered = raw[a:b].lower()
            parts.append(lowered)
            if len(lowered) == b - a:
                offsets.extend(range(a, b))
            else:
                # A character whose lowercase is a different LENGTH (Turkish dotted
                # I and friends).  The digest is still exact -- norm is built with
                # the same .lower() the record's normalisation uses -- and only the
                # raw slice's edge can move by a character inside this one token.
                offsets.extend([a] * len(lowered))
        self.norm = "".join(parts)
        self.offsets = offsets

    def _span(self, start: int, length: int) -> tuple[str, str]:
        return (self.norm[start:start + length],
                self.raw[self.offsets[start]:self.offsets[start + length - 1] + 1])

    def windows(self, length: int, first_words: str = "") -> list[tuple[str, str]]:
        """``(normalised, raw)`` for the candidate windows of *length* characters.

        ``first_words`` is the record's own first words, so a window can only start
        where they occur and the search is a substring scan.  A record that
        withheld its edges (a short quote) has no such anchor and every position of
        the page is a candidate.

        Falling back to every position when the first words occur NOWHERE was
        measured and dropped: over the baseline corpus's 241 distinct quotes it
        recovered not one more passage and cost five times the time, because a
        quote whose opening words are absent from the page is a quote of a
        different extraction, not a quote at a different offset.
        """
        if length <= 0 or length > len(self.norm):
            return []
        if not first_words:
            return [self._span(start, length)
                    for start in range(0, len(self.norm) - length + 1)]
        out: list[tuple[str, str]] = []
        start = self.norm.find(first_words)
        while start != -1:
            if start + length <= len(self.norm):
                out.append(self._span(start, length))
            start = self.norm.find(first_words, start + 1)
        return out


def target_lengths(record: dict) -> list[int]:
    """The normalised lengths to slide, longest first.

    One length for a ``quote-v1`` record.  For ``route._redact``'s record only the
    raw length is known, so every length a whitespace collapse could account for
    is tried -- the alternative is refusing to hydrate the export the skill has
    been writing all along.
    """
    if record.get("norm_chars"):
        return [int(record["norm_chars"])]
    chars = int(record.get("chars") or 0)
    if chars <= 0:
        return []
    return [n for n in range(chars, max(0, chars - MAX_COLLAPSE) - 1, -1) if n > 0]


def pages_to_search(record: dict, page_count: int, *, window: int = PAGE_BIND_WINDOW) -> list[int]:
    """The 1-based pages a record's quote may sit on.

    The recorded page plus or minus *window* -- the same tolerance the gate's
    ``PageBind`` allows, because the page was recorded by the same match and an
    item's statement runs over page breaks.  A record with no page searches
    everything rather than nothing.
    """
    pages = [int(p) for p in (record.get("pages") or ([record["page"]] if record.get("page") else []))]
    if not pages:
        return list(range(1, page_count + 1))
    wanted: list[int] = []
    for page in pages:
        for n in range(page - window, page + window + 1):
            if 1 <= n <= page_count and n not in wanted:
                wanted.append(n)
    return sorted(wanted)


def hydrate_record(record: dict, raw: str, *, window: int = PAGE_BIND_WINDOW) -> dict:
    """Recover one redacted quote's text out of *raw* (a source's pinned text).

    Returns ``{status, text, page, pages, searched_pages, why}``; ``text`` is the
    holder's own spelling of the passage, byte for byte as their ``text.txt``
    carries it, and is empty unless the status is ``hydrated``.
    """
    digest = str(record.get("sha256") or "")
    lengths = target_lengths(record)
    raw_pages = split_pages(raw)
    searched = pages_to_search(record, len(raw_pages), window=window)
    out: dict[str, Any] = {"status": NOT_FOUND, "text": "", "page": None, "pages": [],
                           "searched_pages": searched, "why": ""}
    if not digest:
        out["why"] = "the record carries no sha256, so nothing can be verified against it"
        return out
    if not lengths:
        out["why"] = ("the record carries neither norm_chars nor chars, so there is no "
                      "window length to slide")
        return out
    first_words = str(record.get("first_words") or "")
    # A record with first words is a substring scan, which is affordable over a
    # whole source.  Without them every position is a candidate, and MANY lengths
    # (a record with no ``norm_chars``) multiplies that by MAX_COLLAPSE -- so that
    # combination is swept over a bounded number of pages only, and past it the
    # command says what would settle it instead of hashing the book 32 times over.
    if len(lengths) > 1 and not first_words and len(searched) > MAX_SWEEP_PAGES:
        out["why"] = (
            f"this record carries neither a page nor its first words, so the search would "
            f"have to sweep {len(searched)} pages at {len(lengths)} lengths; re-export the "
            f"bundle with `cf share export`, which records both")
        return out
    hits: dict[str, list[int]] = {}
    for number in searched:
        page = _Page(number, raw_pages[number - 1])
        for length in lengths:
            for candidate, raw_span in page.windows(length, first_words):
                if hashlib.sha256(candidate.encode("utf-8")).hexdigest() != digest:
                    continue
                hits.setdefault(raw_span, [])
                if page.number not in hits[raw_span]:
                    hits[raw_span].append(page.number)
    if not hits:
        out["why"] = (f"no passage of {len(searched)} searched page(s) hashes to the record's "
                      f"sha256 (this is not the edition the finding was written against, or the "
                      f"quote was not copied out of the pinned text)")
        return out
    if len(hits) > 1:
        out["status"] = AMBIGUOUS
        out["pages"] = sorted({n for pages_of in hits.values() for n in pages_of})
        out["why"] = (f"{len(hits)} passages with different spellings hash to the same quote "
                      f"on page(s) {out['pages']}; which one the finding meant cannot be decided "
                      f"mechanically")
        return out
    text, found_on = next(iter(hits.items()))
    out["status"] = HYDRATED
    out["text"] = text
    out["pages"] = sorted(found_on)
    out["page"] = out["pages"][0]
    return out


def verify_text(record: dict, text: str) -> bool:
    """Does *text* really hash to *record*'s digest?  (The hydration's own check.)"""
    want = str(record.get("sha256") or "")
    if not want:
        return False
    return hashlib.sha256(normalise_quote(text).encode("utf-8")).hexdigest() == want


def hydrate_rows(rows: Iterable[dict], texts: dict[str, str],
                 *, window: int = PAGE_BIND_WINDOW) -> tuple[list[dict], dict[str, int]]:
    """Hydrate every redacted row of *rows* against ``{source_id: pinned text}``.

    Returns ``(reports, counts)``.  One report per redacted row --
    ``{source_id, item, kind, finding_id, status, text, page, why}`` -- and counts
    over ``hydrated`` / ``not-found`` / ``ambiguous`` / ``no-source`` plus the
    number of rows that carried nothing to hydrate.
    """
    reports: list[dict] = []
    counts = {HYDRATED: 0, NOT_FOUND: 0, AMBIGUOUS: 0, "no-source": 0,
              "rows": 0, "redacted": 0, "verbatim": 0}
    for row in rows:
        counts["rows"] += 1
        record = row.get("book_quote")
        if not is_redacted(record):
            if str(record or "").strip():
                counts["verbatim"] += 1
            continue
        counts["redacted"] += 1
        source_id = str(row.get("source_id") or "")
        report = {
            "source_id": source_id,
            "item": str(row.get("item") or ""),
            "kind": str(row.get("kind") or ""),
            "finding_id": str(row.get("finding_id") or ""),
            "page": record.get("page"),
        }
        raw = texts.get(source_id)
        if raw is None:
            report.update(status="no-source", text="", why=(
                f"this workspace has no pinned text for {source_id or '(no source_id)'}"))
            counts["no-source"] += 1
            reports.append(report)
            continue
        result = hydrate_record(record, raw, window=window)
        report.update(status=result["status"], text=result["text"], why=result["why"],
                      page=result["page"] if result["page"] else record.get("page"),
                      searched_pages=result["searched_pages"])
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        reports.append(report)
    return reports, counts


def redacted_rows(rows: Iterable[dict]) -> list[dict]:
    """Only the rows whose ``book_quote`` is a redaction record."""
    return [r for r in rows if is_redacted(r.get("book_quote"))]


def source_texts(paths: Any, source_ids: Sequence[str]) -> dict[str, str]:
    """``{source_id: pinned text}`` for the sources this workspace has prepared.

    A source with no ``sources/<id>/text.txt`` is left out rather than recorded as
    empty: "this workspace has no text for that edition" and "the quote is not in
    it" are different answers and the counts keep them apart.
    """
    out: dict[str, str] = {}
    for source_id in source_ids:
        if not source_id:
            continue
        path = paths.source_text(source_id)
        if path.exists():
            out[source_id] = path.read_text(encoding="utf-8", errors="replace")
    return out
