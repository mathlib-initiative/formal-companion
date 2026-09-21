"""Turn a book quote into something shareable, and recognise one again.

New code.  The redacted form is the hash ``route._redact`` already computed, plus
the three facts a reader and :mod:`share.hydrate` need that it dropped:

    {"redacted": true, "redaction": "quote-v1",
     "chars": 176, "norm_chars": 174,
     "sha256": "<sha256 of the normalised quote>",
     "page": 34, "pages": [34],
     "first_words": "let a b be an interval",
     "last_words": "which is what we wanted"}

``sha256`` is over the NORMALISED quote -- whitespace collapsed, lowercased, and
nothing else -- the same normalisation ``scripts/route/route.py`` hashes, so a
row redacted by ``cf route --redact-quotes`` and a row redacted here carry the
same digest for the same sentence.  It is deliberately NOT the quote gate's
:func:`gate.verify_findings.norm`, which deletes every operator: the defect class
this audit exists to find is a flipped ``≤``, so a hash that cannot tell
``m(E) ≤ m(F)`` from ``m(E) ≥ m(F)`` would let :mod:`share.hydrate`
reinstate the wrong sentence and call it verified.

``first_words``/``last_words`` are withheld for a short quote: six words from
each end of a twelve-word sentence is the whole sentence, which is the one thing
a redaction may not hand over.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Sequence

#: the shape of the record this module writes, so a reader can tell it from
#: ``route._redact``'s (which carries no page and no normalised length)
REDACTION = "quote-v1"
#: how many words of each end travel with a redacted quote
EDGE_WORDS = 6
#: below this many words the edges ARE the quote, so they are withheld
MIN_WORDS_FOR_EDGES = 2 * EDGE_WORDS + 2


def normalise_quote(text: Any) -> str:
    """Collapse whitespace and lowercase -- the form the digest is taken over.

    Identical to the normalisation inlined in ``route._redact``
    (``" ".join(quote.split()).lower()``).  The two must stay identical or a row
    shared through ``cf route --redact-quotes`` and a row shared through
    ``cf share export`` stop being the same quote; ``tests/test_rf_share.py``
    pins them against each other.
    """
    return " ".join(str(text or "").split()).lower()


def quote_digest(text: Any) -> str:
    """sha256 of :func:`normalise_quote` of *text*."""
    return hashlib.sha256(normalise_quote(text).encode("utf-8")).hexdigest()


def redact_quote(quote: Any, *, pages: Sequence[int] = ()) -> dict:
    """The shareable record for one book quote.

    ``pages`` are the 1-based PDF pages of the pinned text the quote was located
    on (see :func:`share.hydrate.locate_pages`); an empty list is recorded as
    ``page: None`` rather than guessed at, and ``cf quotes hydrate`` then has to
    search every page instead of a window.
    """
    raw = str(quote or "")
    norm = normalise_quote(raw)
    words = norm.split()
    page_list = [int(p) for p in pages]
    out: dict[str, Any] = {
        "redacted": True,
        "redaction": REDACTION,
        "chars": len(raw),
        "norm_chars": len(norm),
        "words": len(words),
        "sha256": hashlib.sha256(norm.encode("utf-8")).hexdigest(),
        "page": page_list[0] if page_list else None,
        "pages": page_list,
    }
    if len(words) >= MIN_WORDS_FOR_EDGES:
        out["first_words"] = " ".join(words[:EDGE_WORDS])
        out["last_words"] = " ".join(words[-EDGE_WORDS:])
    else:
        out["edges_withheld"] = (
            f"{len(words)} words: {EDGE_WORDS} from each end would be the whole quote"
        )
    return out


def is_redacted(value: Any) -> bool:
    """True for a redacted-quote record -- ours or ``route._redact``'s."""
    return isinstance(value, dict) and bool(value.get("redacted")) and bool(value.get("sha256"))


def redact_row(row: dict, *, pages: Sequence[int] = ()) -> dict:
    """A copy of *row* whose ``book_quote`` is a redacted record.

    A row that carries no quote, or one already redacted, is returned unchanged:
    re-redacting a record would hash the record itself.
    """
    quote = row.get("book_quote")
    if is_redacted(quote) or not str(quote or "").strip():
        return dict(row)
    out = dict(row)
    out["book_quote"] = redact_quote(quote, pages=pages)
    return out


def quote_of(row: dict) -> str:
    """The row's verbatim quote, or ``""`` when it carries none or a record."""
    quote = row.get("book_quote")
    return "" if is_redacted(quote) else str(quote or "")


def count_quotes(rows: Iterable[dict]) -> dict[str, int]:
    """``{verbatim, redacted, none}`` over *rows* -- what a manifest reports."""
    counts = {"verbatim": 0, "redacted": 0, "none": 0}
    for row in rows:
        quote = row.get("book_quote")
        if is_redacted(quote):
            counts["redacted"] += 1
        elif str(quote or "").strip():
            counts["verbatim"] += 1
        else:
            counts["none"] += 1
    return counts
