"""Book tags and page anchors inside a Lean docstring.

Ported from the v1 campaign's two extraction scripts (not in this repo) --
``extract_ours.py`` (the ITEM/PAGE regexes) and ``extract_teorth.py`` (the
leading-label form) -- and from the tag normalisation in tao-measure-lean's own
``make_verso_tables.py`` (``extract_tag``: whitespace collapsed so a tag broken
over a line wrap groups with its single-line spelling; leading tag preferred; a
backtick-wrapped tag near the start as the fallback).

Two defects the design records are fixed here:

* **cross-reference anchors.** ``extract_ours.py`` searched the whole
  docstring for ``book p. N``, so ``Exercise 1.2.17`` took its anchor from
  "(4) is the full Caratheodory condition of S1.7 (previewed on book p. 20)"
  (docs/design/stage_map.md Stage 2).  A page counts as the item's *own* anchor
  only when it sits inside the tag span or in the clause that immediately
  follows it; anything further away is reported as ``anchor_source:
  "crossref"`` with ``page_anchor: null``.
* **trailing code leak.** teorth's ITEM regex ran to the end of the line and
  captured Lean code; the tag is cut at the first backtick / newline, and the
  strict grammar in ``cflib.items`` reports whatever is left as ``trailing``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["TagHit", "AnchorHit", "find_tag", "find_page_anchor", "DEFAULT_KEYWORDS",
           "TAG_KIND_RE", "kinds_from_labels"]

DEFAULT_KEYWORDS: tuple[str, ...] = (
    "Definition", "Lemma", "Theorem", "Proposition", "Corollary",
    "Exercise", "Example", "Remark", "Claim", "Strategy", "In-text",
)

#: The kind half of a tag: a capitalised word immediately followed by a
#: ``c.s.n``-style number, or the literal ``In-text``.  This is how a repo's item
#: GRAMMAR is read rather than declared (R2): a companion that tags
#: ``Problem 2.3.1`` reveals the kind ``Problem`` without anyone typing a list,
#: and the word is never invented -- it is the one the repo wrote.
TAG_KIND_RE = re.compile(r"`?\s*(In-text|[A-Z][A-Za-z-]{2,15})(?=\s*[,.`]?\s*(?:\d+\.\d+|book\s+pp?\.))")


def kinds_from_labels(labels, min_count: int = 2) -> tuple[str, ...]:
    """Item kinds a repo's own tags use, most frequent first.

    ``min_count`` is the noise floor: one stray capitalised word before a
    section number is not a kind.  Nothing here consults a keyword list, which
    is the point -- a list would only ever return the kinds we already knew.
    """
    from collections import Counter
    counts: Counter = Counter()
    for label in labels:
        m = TAG_KIND_RE.match(str(label or "").strip())
        if m:
            counts[m.group(1)] += 1
    return tuple(k for k, n in counts.most_common() if n >= min_count)


#: "book p. 24", "book pp. 25-26", "pages 70-71", "p. 156".
PAGE_RE = re.compile(
    r"(?:\bbook\s+)?\b(?:pp?\.|pages?)\s*(\d{1,4})(?:\s*[-‐-―−]\s*(\d{1,4}))?",
    re.I,
)

#: Words after which a "." is an abbreviation, not the end of a sentence.
_ABBREV_RE = re.compile(r"(?:\bpp?|\be\.g|\bi\.e|\bcf|\bvs|\bresp|\bfig|\bno|\bch|\bsec)$", re.I)
_SENTENCE_END_RE = re.compile(r"\.(?=\s|$)")

#: A page mention is a *cross-reference* when some other book location is
#: named between the tag and the page: "the full Caratheodory condition of
#: S1.7 (previewed on book p. 20)" is what gave Exercise 1.2.17 the anchor 20.
_REFERENCE_RE = re.compile(
    r"\u00a7|\bSection\s+\d|\bChapter\s+\d|\bsee\b|\bcf\.|\bcompare\b|"
    r"\bpreviewed\b|\bproved in\b|\bstated in\b|"
    r"\b(?:Definition|Lemma|Theorem|Proposition|Corollary|Exercise|Example|Remark)\s+\d+\.\d+",
    re.I,
)

#: Past this many characters from the tag a page is never the item's own.
DEFAULT_ANCHOR_REACH = 400

@dataclass(frozen=True)
class TagHit:
    """A book tag found in a docstring."""

    tag: str            # the raw tag text, whitespace collapsed
    start: int          # offset into the collapsed docstring
    end: int
    position: str       # "leading" | "inline"


@dataclass(frozen=True)
class AnchorHit:
    """A printed-page anchor found (or deliberately not taken) in a docstring."""

    page: int | None
    raw: str | None
    source: str         # "own" | "crossref" | "none"
    last_page: int | None = None   # second page of a range, if any


def collapse(text: str) -> str:
    """Collapse runs of whitespace, so a tag broken over a line wrap parses."""
    return re.sub(r"\s+", " ", (text or "").strip())


def _keyword_alt(keywords) -> str:
    return "|".join(re.escape(k) for k in keywords)


def find_tag(
    doc: str,
    keywords=DEFAULT_KEYWORDS,
    require_backticks: bool = True,
    inline_window: int = 0,
    pattern: "re.Pattern[str] | None" = None,
) -> TagHit | None:
    """Find the book tag of a docstring.

    ``require_backticks`` (the tao-measure-lean convention, CLAUDE.md:32-34)
    only accepts a backtick-wrapped tag; that is what ``extract_ours.py`` did
    and it is what keeps prose mentions of "Lemma 1.2.5" out of the table.
    Set it false for repos that write the label bare at the start of the
    docstring (teorth/analysis).

    The leading tag wins over any later one: "previewed in `Lemma 1.2.5`" in
    the second sentence must not retag a declaration that opens with its own
    tag.  ``inline_window`` bounds the fallback search used when there is no
    leading tag; ``0`` means the whole docstring (extract_ours.py's rule).
    """
    text = collapse(doc)
    if not text:
        return None
    if pattern is not None:
        m = pattern.search(text)
        if not m:
            return None
        group = "tag" if "tag" in (m.groupdict() or {}) else (1 if m.lastindex else 0)
        return TagHit(collapse(m.group(group)), m.start(group), m.end(group),
                      "leading" if m.start(group) <= 2 else "inline")
    alt = _keyword_alt(keywords)

    lead = re.match(rf"^`(?P<tag>(?:{alt})(?![A-Za-z])[^`]*)`", text)
    if lead:
        return TagHit(collapse(lead.group("tag")), lead.start("tag"), lead.end("tag"), "leading")

    if not require_backticks:
        # Bare leading label, cut at the first backtick, colon or newline —
        # this is the teorth form and the cut is what stops the code leak.
        bare = re.match(rf"^(?P<tag>(?:{alt})(?![A-Za-z])[^`:\n]*)", text)
        if bare:
            tag = collapse(bare.group("tag")).rstrip(".")
            return TagHit(tag, bare.start("tag"), bare.start("tag") + len(tag), "leading")

    window = text if inline_window <= 0 else text[:inline_window]
    inline = re.search(rf"`(?P<tag>(?:{alt})(?![A-Za-z])[^`]*)`", window)
    if inline:
        return TagHit(collapse(inline.group("tag")), inline.start("tag"), inline.end("tag"), "inline")
    return None


def _clause_after(text: str, start: int, window: int) -> str:
    """The clause following ``start``: to the first sentence end, or ``window``."""
    chunk = text[start:start + window]
    for m in _SENTENCE_END_RE.finditer(chunk):
        before = chunk[:m.start()]
        if _ABBREV_RE.search(before):
            continue                      # "book p. 24" — not a sentence end
        after = chunk[m.end():].lstrip()
        if after[:1].isdigit():
            continue                      # inside a number such as "1.1.1"
        return chunk[:m.start()]
    return chunk


def find_page_anchor(
    doc: str,
    tag: TagHit | None,
    clause_window: int = 140,
    reach: int = DEFAULT_ANCHOR_REACH,
    page_re: "re.Pattern[str] | None" = None,
) -> AnchorHit:
    """The printed page this docstring claims for *its own* item.

    Three tiers, in order:

    1. a page inside the tag span (``In-text, book p. 24``) is the own anchor;
    2. a page in the clause that immediately follows the tag
       (``(`Lemma 1.2.5`, book p. 23)``) is the own anchor;
    3. a page further on is the own anchor only when no other book location is
       named between the tag and it, and it is within ``reach`` characters.

    Anything else is reported as ``crossref`` with ``page_anchor: null``:
    a wrong anchor is worse than a missing one, because ``pair/anchor_check``
    turns anchors into mechanical PAGE_ANCHOR findings.
    """
    text = collapse(doc)
    if not text:
        return AnchorHit(None, None, "none")
    page_re = page_re or PAGE_RE

    if tag is not None:
        inside = page_re.search(text[tag.start:tag.end])
        if inside:
            return _hit(inside, "own")
        clause = _clause_after(text, tag.end, clause_window)
        near = page_re.search(clause)
        if near:
            return _hit(near, "own")
        after = text[tag.end:tag.end + reach]
        far = page_re.search(after)
        if far and not _REFERENCE_RE.search(after[:far.start()]):
            return _hit(far, "own")

    anywhere = page_re.search(text)
    if anywhere:
        return AnchorHit(None, anywhere.group(0).strip(), "crossref")
    return AnchorHit(None, None, "none")


def _hit(match: re.Match[str], source: str) -> AnchorHit:
    """Build an AnchorHit; group 1 is the page, an optional group 2 its range end."""
    first = int(match.group(1))
    last = None
    if (match.lastindex or 0) >= 2 and match.group(2):
        last = int(match.group(2))
    return AnchorHit(first, match.group(0).strip(), source, last)
