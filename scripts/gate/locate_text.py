"""The head text and the layer quote of a web source -- standard library only.

New code, for docs/proposals/wikipedia-locate.md sections 2.1, 2.2 and 4 (gate
rule 2), in the form references/locate-documents.md section 7 fixes.  Every
reader of a rendered web source needs the same three things, and one of them
-- the gate -- runs on the run host with no ``cflib`` on the path, so they live
here, stdlib-only, beside ``verify_findings.py``, and the planner copies this
file into the gate directory together with the vendored packages:

1. **The head text** of a source is its Literature Links text layer
   (``layer.txt``) with each formula's layer span replaced by its token
   ``⟦<normalized alttext>⟧`` (``formulas.json`` lists the spans).
   :class:`HeadMap` builds it and converts offsets both ways: a layer offset
   inside a formula has no head offset and is refused.
2. **The passage id** of a head start: the frozen rule of
   references/vocabulary.md section 4, here as :func:`hex_at` and
   :func:`assign_ids`.  ``cflib.passages`` is the definition the skill's own
   stages call; this file restates it for the host, in lockstep, the way
   ``verify_findings.finding_id`` restates ``cflib.hashing.finding_id``, and
   tests/test_locate_contract.py asserts the two agree.
3. **The quote**: :func:`find_quote` gives every layer offset where ``exact``
   occurs with ``prefix`` immediately before it and ``suffix`` immediately after.
   A gate accepts exactly one.  Matching is on the layer's own characters -- no
   normalisation at all -- because the layer is the one view quotes are cut
   from.
4. **The end** of a passage: :func:`passage_end`, the rule ``passage_ids.py``
   fills a span with and gate rule 2 re-computes (references/locate-documents.md
   section 2), so the extent a reader highlights is the tool's and never a
   hand-counted offset.

The vendored ``formal_companion_annotations.ids`` is imported from
``vendor/`` of the skill checkout, or from ``vendor/`` beside this file in a
gate directory.
"""
from __future__ import annotations

import re
import sys
from bisect import bisect_right
from pathlib import Path
from typing import Iterable, Sequence

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE / "vendor", _HERE.parent.parent / "vendor"):
    if (_candidate / "formal_companion_annotations").is_dir():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from formal_companion_annotations import ids as _ids  # noqa: E402

#: The formula token's delimiters (U+27E6, U+27E7); NFKC leaves them alone.
FORMULA_OPEN = "⟦"
FORMULA_CLOSE = "⟧"
#: the head starts at this many words and is extended up to this many
HEAD_WORDS = 12
MAX_HEAD_WORDS = 40
_DISPLAYSTYLE = re.compile(r"\\displaystyle(?![A-Za-z])")
_ID_PREFIX = "passage-"
_WINDOW = 1024


class HeadError(ValueError):
    """An offset, a formula list or a passage table the head rule cannot key."""


# --------------------------------------------------------------------------
# formulas (lockstep with cflib.passages.normalize_alttext / formula_token)
# --------------------------------------------------------------------------

def _single_group(text: str) -> bool:
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
    """Whitespace collapsed, ``\\displaystyle`` dropped, outer braces stripped, to a fixed point."""
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
# the head text and its offset map
# --------------------------------------------------------------------------

class HeadMap:
    """The head text of one source and the offset map between it and the layer.

    ``formulas`` is ``formulas.json``: ``[{"span": [a, b], "alttext": ...}]``,
    half-open code-point spans of the layer, sorted and disjoint, each
    non-empty.  Anything else is refused: a map built from overlapping spans
    would send two passages to one head offset.
    """

    def __init__(self, layer: str, formulas: Sequence[dict] = ()):
        self.layer = str(layer)
        spans: list[tuple[int, int, str]] = []
        previous_end = 0
        for index, formula in enumerate(formulas or ()):
            try:
                a, b = (int(x) for x in formula["span"])
            except (KeyError, TypeError, ValueError):
                raise HeadError(f"formula {index} has no [start, end] span") from None
            if not (previous_end <= a < b <= len(self.layer)):
                raise HeadError(f"formula {index} span [{a}, {b}] is empty, overlaps the one "
                                f"before it or leaves the layer (0..{len(self.layer)})")
            spans.append((a, b, formula_token(formula.get("alttext", ""))))
            previous_end = b
        pieces: list[str] = []
        #: (layer_start, head_start, layer_end, head_end) of each formula
        self._formulas: list[tuple[int, int, int, int]] = []
        cursor = 0
        head_len = 0
        for a, b, token in spans:
            pieces.append(self.layer[cursor:a])
            head_len += a - cursor
            self._formulas.append((a, head_len, b, head_len + len(token)))
            pieces.append(token)
            head_len += len(token)
            cursor = b
        pieces.append(self.layer[cursor:])
        self.head = "".join(pieces)
        self._layer_starts = [f[0] for f in self._formulas]
        self._head_starts = [f[1] for f in self._formulas]

    def to_head(self, offset: int) -> int:
        """The head offset of a layer offset; refused strictly inside a formula."""
        offset = int(offset)
        if not 0 <= offset <= len(self.layer):
            raise HeadError(f"layer offset {offset} is outside the layer (0..{len(self.layer)})")
        i = bisect_right(self._layer_starts, offset) - 1
        if i < 0:
            return offset
        a, h, b, he = self._formulas[i]
        if offset == a:
            return h
        if offset < b:
            raise HeadError(f"layer offset {offset} is inside the formula at [{a}, {b}]")
        return he + (offset - b)

    def to_layer(self, offset: int) -> int:
        """The layer offset of a head offset; refused strictly inside a formula token."""
        offset = int(offset)
        if not 0 <= offset <= len(self.head):
            raise HeadError(f"head offset {offset} is outside the head text (0..{len(self.head)})")
        i = bisect_right(self._head_starts, offset) - 1
        if i < 0:
            return offset
        a, h, b, he = self._formulas[i]
        if offset == h:
            return a
        if offset < he:
            raise HeadError(f"head offset {offset} is inside the formula token at [{h}, {he}]")
        return b + (offset - he)


# --------------------------------------------------------------------------
# passage ids (lockstep with cflib.passages)
# --------------------------------------------------------------------------

def _is_white(ch: str) -> bool:
    return _ids.normalize_text(ch) == ""


def check_start(head: str, start: int) -> int:
    """``start`` if it is the first character of a word of ``head``; else :class:`HeadError`."""
    if isinstance(start, bool) or not isinstance(start, int):
        raise HeadError(f"a passage start must be a character offset, not {start!r}")
    if not 0 <= start < len(head):
        raise HeadError(f"passage start {start} is outside the head text (0..{len(head) - 1})")
    if _is_white(head[start]):
        raise HeadError(f"passage start {start} is whitespace, not the start of a word")
    if start > 0 and not _is_white(head[start - 1]):
        raise HeadError(f"passage start {start} is inside a word, not the start of one")
    return start


def head_at(head: str, start: int, limit: int = MAX_HEAD_WORDS) -> list[str]:
    """Up to ``limit`` normalised words of ``head`` from ``start``."""
    check_start(head, start)
    if limit < 1:
        return []
    size = _WINDOW
    while True:
        end = start + size
        words = _ids.normalize_text(head[start:end]).split(" ")
        if end >= len(head):
            return [w for w in words if w][:limit]
        if len(words) > limit:
            return words[:limit]
        size *= 2


def head_hex(words: Sequence[str]) -> str:
    """The 8-hex vendored passage hash of a head."""
    return _ids.passage_quote_id(" ".join(words))[len(_ID_PREFIX):]


def hex_at(head: str, start: int, head_words: int) -> str:
    """Re-hash the first ``head_words`` words from ``start``: the gate's re-check."""
    if isinstance(head_words, bool) or not isinstance(head_words, int) or head_words < 1:
        raise HeadError(f"head_words must be a positive word count, not {head_words!r}")
    if head_words > MAX_HEAD_WORDS:
        raise HeadError(f"head_words {head_words} is over the cap of {MAX_HEAD_WORDS}")
    return head_hex(head_at(head, start, head_words))


def assign_ids(head: str, starts: Iterable[int]) -> list[tuple[int, str, int]]:
    """``[(start, hex, head_words)]`` for ALL of one source's passages, in the order given.

    The rule of ``cflib.passages.assign_passage_ids``: each head is the shortest
    of 12..40 words no other passage starts with; a start given twice, forty
    shared words, or two heads with one hash is a :class:`HeadError` naming both.
    """
    offsets = list(starts)
    seen: dict[int, int] = {}
    for index, start in enumerate(offsets):
        check_start(head, start)
        if start in seen:
            raise HeadError(f"passage start {start} is given twice (passages {seen[start]} "
                            f"and {index})")
        seen[start] = index
    words = [head_at(head, start) for start in offsets]
    chosen: list[int | None] = [None] * len(offsets)
    for n in range(HEAD_WORDS, MAX_HEAD_WORDS + 1):
        pending = [i for i, c in enumerate(chosen) if c is None]
        if not pending:
            break
        groups: dict[tuple[str, ...], int] = {}
        for w in words:
            key = tuple(w[:n])
            groups[key] = groups.get(key, 0) + 1
        for i in pending:
            if groups[tuple(words[i][:n])] == 1:
                chosen[i] = len(words[i][:n])
    out: list[tuple[int, str, int]] = []
    by_hex: dict[str, int] = {}
    for i, start in enumerate(offsets):
        count = chosen[i]
        if count is None:
            twin = next(j for j, w in enumerate(words)
                        if j != i and w[:MAX_HEAD_WORDS] == words[i][:MAX_HEAD_WORDS])
            raise HeadError(f"passages at offsets {offsets[i]} and {offsets[twin]} share their "
                            f"first {MAX_HEAD_WORDS} words; neither has an id")
        digest = head_hex(words[i][:count])
        if digest in by_hex:
            raise HeadError(f"passages at offsets {offsets[by_hex[digest]]} and {start} have "
                            f"different heads that hash to the same id {digest}")
        by_hex[digest] = i
        out.append((start, digest, count))
    return out


# --------------------------------------------------------------------------
# quotes on the layer
# --------------------------------------------------------------------------

def find_quote(layer: str, exact: str, prefix: str = "", suffix: str = "") -> list[int]:
    """Every layer offset at which ``exact`` starts with ``prefix``/``suffix`` around it.

    Exact character matching on the layer, overlapping occurrences included; an
    empty ``exact`` matches nowhere.  The caller decides what a count means: a
    gate accepts exactly one.
    """
    exact = str(exact or "")
    if not exact:
        return []
    prefix = str(prefix or "")
    needle = prefix + exact + str(suffix or "")
    found: list[int] = []
    at = layer.find(needle)
    while at >= 0:
        found.append(at + len(prefix))
        at = layer.find(needle, at + 1)
    return found


# --------------------------------------------------------------------------
# where a passage ends
# --------------------------------------------------------------------------

def paragraph_end(paragraphs: Sequence[dict], start: int) -> int | None:
    """The end of the first ``paragraphs.json`` block that holds the offset ``start``.

    The table is sorted by start with an outer block before the blocks it holds
    (``sources.render_html``), so the first block holding ``start`` is the
    outermost one: a passage that opens a theorem box ends where the box does.
    An entry without a readable span is passed over.
    """
    for para in paragraphs or ():
        try:
            a, b = (int(x) for x in para["span"])
        except (KeyError, TypeError, ValueError):
            continue
        if a <= start < b:
            return b
    return None


def passage_end(layer: str, paragraphs: Sequence[dict], start: int, exact: str,
                until: object = None) -> int:
    """The layer offset a passage that starts at ``start`` with quote ``exact`` ends at.

    The one rule ``passage_ids.py`` writes and gate rule 2 re-computes: with an
    ``until`` (the passage's last words), the end of the first occurrence of it
    at or after the start; else the end of the paragraph the passage starts in;
    else the end of the quote -- and never before the end of the quote.  An
    empty or null ``until`` is none; one that is not a string, or is not on the
    layer after the start, is a :class:`HeadError`.  Offsets are code points of
    the layer, like every span (a UTF-16 count of an astral character is not
    one).
    """
    end = start + len(exact)
    if until is None or until == "":
        return max(end, paragraph_end(paragraphs, start) or end)
    if not isinstance(until, str):
        raise HeadError(f"`until` is a {type(until).__name__}, not the passage's last words")
    at = layer.find(until, start)
    if at < 0:
        raise HeadError(f"`until` {until[:40]!r} is not on the layer after the passage's start")
    return max(end, at + len(until))
