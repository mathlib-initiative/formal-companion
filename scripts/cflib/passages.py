"""The renderer-independent passage head id: ``In-text #<hex>``.

New code, implementing docs/proposals/wikipedia-locate.md section 2.2 (the
hash of a passage's head) for sources with no numbering and no printed page.
The hex is the vendored format's passage hash
(``formal_companion_annotations.ids.passage_quote_id``, reused here, never
re-implemented) over the passage's **head**: its first twelve words, extended
word by word until no other passage of the same source starts with the same
words, up to forty.  Two passages that still agree on forty words are a
refusal (:class:`cflib.errors.PassageIdError`) naming both, never a silent
drop and never a guess.

**The head text.**  Every function here reads a source's *head text*: its
Literature Links text layer with each formula replaced by
:func:`formula_token` of its alttext, so the head does not depend on which
renderer drew the formula (MathML token text, an image, a TeX string).  The
renderer that produces the layer (not implemented) produces this text too,
and passage starts are character offsets into it.  The token is
``FORMULA_OPEN + normalize_alttext(alttext) + FORMULA_CLOSE`` -- for a
MediaWiki ``{\\displaystyle G}``, ``⟦G⟧``.

**Words.**  The head text from a passage start is put through the vendored
passage normaliser (``ids.normalize_text``: NFKC, then each run of Unicode
``White_Space`` to one space, ends stripped), and a word is a maximal run
between single spaces.  A formula is no special case: ``⟦a + b⟧`` is three
words.  That keeps the rule a function of the string alone -- no bracket is
ever parsed, so a literal ``⟦`` in the prose cannot unbalance anything -- and
makes the head, and so the id, blind to whitespace changes.

**The head.**  ``head_words`` words joined by one space: exactly the string
the vendored hash normalises, so two heads that compare equal here hash
equal there.  A passage near the end of the text with fewer words left than
the head wants has a shorter head; ``head_words`` is the count actually used,
and re-hashing the first ``head_words`` words from the start reproduces the
hex (:func:`hex_at`), which is what a gate re-checks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import ids as _ids

from . import items
from .errors import PassageIdError

#: The formula token's delimiters: U+27E6 / U+27E7, which NFKC leaves alone.
FORMULA_OPEN = "⟦"
FORMULA_CLOSE = "⟧"
#: The head starts at this many words ...
HEAD_WORDS = 12
#: ... and is extended word by word up to this many.
MAX_HEAD_WORDS = 40

_DISPLAYSTYLE = re.compile(r"\\displaystyle(?![A-Za-z])")
#: the vendored hash's id prefix (``passage-<8 hex>``)
_ID_PREFIX = "passage-"
#: how much head text to normalise first, in characters; doubled until enough
_WINDOW = 1024


# --------------------------------------------------------------------------
# formulas
# --------------------------------------------------------------------------

def _single_group(text: str) -> bool:
    """True when ``text`` is one ``{...}`` group: its first brace closes at its last character.

    ``\\{`` and ``\\}`` are TeX's literal braces and do not count, so ``{a\\}``
    is not a group and ``{a}{b}`` is two.
    """
    if len(text) < 2 or text[0] != "{" or text[-1] != "}":
        return False
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i == len(text) - 1
        i += 1
    return False


def normalize_alttext(alttext: str) -> str:
    """A formula's alttext in the one form the head text uses.

    Whitespace collapsed (the vendored ``normalize_text``, so NFKC first), every
    ``\\displaystyle`` dropped, and outer braces stripped while the whole string
    is one balanced group -- repeated until nothing changes, so
    ``{\\displaystyle {x}}``, `` x `` and ``x`` are one formula, while
    ``{\\mathcal {P}}(X)`` keeps its braces because they do not wrap it all.
    Anything else in the TeX is kept as written: two alttexts that differ in
    more than presentation are two formulas.
    """
    text = _ids.normalize_text(str(alttext or ""))
    while True:
        before = text
        text = _ids.normalize_text(_DISPLAYSTYLE.sub(" ", text))
        if _single_group(text):
            text = _ids.normalize_text(text[1:-1])
        if text == before:
            return text


def formula_token(alttext: str) -> str:
    """``⟦<normalized alttext>⟧``: what a formula is in the head text."""
    return FORMULA_OPEN + normalize_alttext(alttext) + FORMULA_CLOSE


# --------------------------------------------------------------------------
# heads
# --------------------------------------------------------------------------

def _is_white(ch: str) -> bool:
    """Is ``ch`` whitespace by the vendored normaliser's definition?"""
    return _ids.normalize_text(ch) == ""


def _check_start(head_text: str, start) -> int:
    if isinstance(start, bool) or not isinstance(start, int):
        raise PassageIdError(f"a passage start must be a character offset, not {start!r}",
                             starts=(start,))
    if not 0 <= start < len(head_text):
        raise PassageIdError(f"passage start {start} is outside the head text "
                             f"(0..{len(head_text) - 1})", starts=(start,))
    if _is_white(head_text[start]):
        raise PassageIdError(f"passage start {start} is whitespace, not the start of a word",
                             starts=(start,),
                             hint="the renderer gives each passage the offset of its first "
                                  "word in the head text")
    return start


def head_at(head_text: str, start: int, limit: int = MAX_HEAD_WORDS) -> list[str]:
    """Up to ``limit`` normalised words of ``head_text`` from offset ``start``.

    Fewer only when the text runs out.  The window normalised is grown until it
    holds a word past the limit, so the last word kept is never one the window
    cut in half.
    """
    _check_start(head_text, start)
    if limit < 1:
        return []
    size = _WINDOW
    while True:
        end = start + size
        words = _ids.normalize_text(head_text[start:end]).split(" ")
        if end >= len(head_text):
            return [w for w in words if w][:limit]
        if len(words) > limit:
            return words[:limit]
        size *= 2


def head_hex(words: Sequence[str]) -> str:
    """The 8-hex passage hash of a head: the vendored ``passage_quote_id``'s digits."""
    head = " ".join(words)
    return _ids.passage_quote_id(head)[len(_ID_PREFIX):]


def hex_at(head_text: str, start: int, head_words: int) -> str:
    """Re-hash the first ``head_words`` words from ``start`` -- the gate's re-check."""
    if isinstance(head_words, bool) or not isinstance(head_words, int) or head_words < 1:
        raise PassageIdError(f"head_words must be a positive word count, not {head_words!r}",
                             starts=(start,))
    if head_words > MAX_HEAD_WORDS:
        raise PassageIdError(f"head_words {head_words} is over the cap of {MAX_HEAD_WORDS}",
                             starts=(start,))
    return head_hex(head_at(head_text, start, head_words))


@dataclass(frozen=True)
class PassageHead:
    """One passage's id: where it starts, how many words its head took, and the hash."""

    start: int
    hex: str
    head_words: int
    head: str

    @property
    def item(self) -> str:
        """``In-text #<hex>``."""
        return items.passage_item(self.hex)


def _preview(words: Sequence[str], n: int = 8) -> str:
    text = " ".join(words[:n])
    return text + (" ..." if len(words) > n else "")


def assign_passage_ids(head_text: str, starts: Iterable[int]) -> list[PassageHead]:
    """The ids of ALL of one source's passages, in the order ``starts`` gives them.

    Each head is the shortest of 12..40 words that no other passage of the
    source starts with.  Uniqueness is checked against every other start at the
    same length, which is what makes the result independent of the order the
    starts are given in.  Refused, with :class:`PassageIdError` naming both
    passages:

    * an offset given twice, or a start that is whitespace or out of range;
    * two passages whose first forty words agree;
    * two different heads whose 8-hex hashes collide (about one source in
      ten million at a hundred passages; refused, never re-keyed silently).
    """
    head_text = str(head_text if head_text is not None else "")
    offsets = list(starts)
    seen: dict[int, int] = {}
    for index, start in enumerate(offsets):
        _check_start(head_text, start)
        if start in seen:
            raise PassageIdError(f"passage start {start} is given twice "
                                 f"(passages {seen[start]} and {index})",
                                 starts=(start, start))
        seen[start] = index
    words = [head_at(head_text, start) for start in offsets]
    chosen: list[int | None] = [None] * len(offsets)
    for n in range(HEAD_WORDS, MAX_HEAD_WORDS + 1):
        pending = [i for i, c in enumerate(chosen) if c is None]
        if not pending:
            break
        groups: dict[tuple[str, ...], list[int]] = {}
        for i, w in enumerate(words):
            groups.setdefault(tuple(w[:n]), []).append(i)
        for i in pending:
            if len(groups[tuple(words[i][:n])]) == 1:
                chosen[i] = len(words[i][:n])
    for i, count in enumerate(chosen):
        if count is None:
            twin = next(j for j, w in enumerate(words)
                        if j != i and w[:MAX_HEAD_WORDS] == words[i][:MAX_HEAD_WORDS])
            raise PassageIdError(
                f"passages at offsets {offsets[i]} and {offsets[twin]} share their first "
                f"{MAX_HEAD_WORDS} words ({_preview(words[i])!r}); neither has an id",
                starts=(offsets[i], offsets[twin]),
                hint="two passages with one head are one passage, or the renderer "
                     "split a passage twice; merge them or move a start")
    heads: list[PassageHead] = []
    by_hex: dict[str, int] = {}
    for i, start in enumerate(offsets):
        count = chosen[i]
        assert count is not None
        head_words = words[i][:count]
        digest = head_hex(head_words)
        if digest in by_hex:
            j = by_hex[digest]
            raise PassageIdError(
                f"passages at offsets {offsets[j]} and {start} have different heads that "
                f"hash to the same id {digest}",
                starts=(offsets[j], start),
                hint="an 8-hex collision; report it, the id rule cannot separate them")
        by_hex[digest] = i
        heads.append(PassageHead(start=start, hex=digest, head_words=count,
                                 head=" ".join(head_words)))
    return heads


def passage_id(head_text: str, start: int, starts: Iterable[int]) -> tuple[str, int]:
    """``(hex, head_words)`` of the passage at ``start``, among all of ``starts``.

    The one-passage view of :func:`assign_passage_ids`, which it calls over
    ``starts`` plus ``start`` -- so a collision anywhere in the source refuses
    here too, because a source whose passage table cannot be keyed is refused
    as a whole.  Keying a whole source, call :func:`assign_passage_ids` once.
    """
    offsets = list(dict.fromkeys([*starts, start]))
    for head in assign_passage_ids(head_text, offsets):
        if head.start == start:
            return head.hex, head.head_words
    raise AssertionError("unreachable: start is among the offsets")  # pragma: no cover
