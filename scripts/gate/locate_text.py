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
   hand-counted offset.  It has two versions, the ``span_end_rule`` a run's
   ``gate.json`` names: version 1 ends a passage that gives no ``until`` at the
   end of its paragraph; version 2, the statement cut's
   (docs/proposals/statement-cut.md section 3.2), at the earlier of its
   *extent* and the next passage's start, less the separator before it.
5. **The blocks** the statement cut reads (the same proposal, section 3.1):
   :class:`Blocks` over ``paragraphs.json`` and ``formulas.json`` gives each
   offset its outermost block, a block the next one, and the definitions the
   end rule and gate rule 10 rest on -- a display block, a block whose last
   sentence introduces what follows, the displays a sentence introduces
   (:meth:`Blocks.chain`), a run of list items and the extent;
   :func:`units` counts a passage's sentences and displays for the harvest
   receipt.  Rule 1 reads none of it, so a run planned before the statement cut
   ends, gates and counts as it did.

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


def passage_end(layer: str, paragraphs: "Sequence[dict] | Blocks", start: int, exact: str,
                until: object = None, *, rule: int = 1, next_start: int | None = None,
                formulas: Sequence[dict] | None = None) -> int:
    """The layer offset a passage that starts at ``start`` with quote ``exact`` ends at.

    The one rule ``passage_ids.py`` writes and gate rule 2 re-computes, in the
    version ``rule`` names (a run's ``span_end_rule``).  With an ``until`` (the
    passage's last words), both end at the first occurrence of it at or after
    the start.  Without one,

    * **rule 1** ends it at the end of the paragraph the passage starts in
      (:func:`paragraph_end`), else at the end of the quote;
    * **rule 2** (``SPAN_END_STATEMENT_CUT``, docs/proposals/statement-cut.md
      section 3.2) ends it at the earlier of its extent
      (:meth:`Blocks.extent`, ``compound=False``: its block, and the displays
      its last sentence introduces; the quote's end when no block holds the
      start) and ``next_start``, the start of the document's next passage --
      so two passages of one paragraph never nest.  When the next start is
      the earlier, the separator before it is in neither passage
      (:meth:`Blocks.separator_start`: the whitespace, a connective of
      ``CUT_CONNECTIVES`` and a comma or semicolon), so a statement's span,
      and with it its ``passage_key``, is the same whether the next passage
      or an ``until`` at its last word ends it.

    Neither ever ends before the end of the quote.  Rule 1 reads neither
    ``next_start`` nor ``formulas``; rule 2 needs the source's ``formulas``
    (``formulas.json``, or ``()`` for a source that has none), and
    ``paragraphs`` may be a :class:`Blocks` built once for the source.  An empty
    or null ``until`` is none; one that is not a string, or is not on the layer
    after the start, is a :class:`HeadError`.  Offsets are code points of the
    layer, like every span (a UTF-16 count of an astral character is not one).
    """
    if rule not in SPAN_END_RULES:
        raise ValueError(f"span-end rule {rule!r} is not one of {SPAN_END_RULES}")
    end = start + len(exact)
    if until is None or until == "":
        if rule == 1:
            table = paragraphs.paragraphs if _is_blocks(paragraphs) else paragraphs
            return max(end, paragraph_end(table, start) or end)
        if next_start is not None and (isinstance(next_start, bool)
                                       or not isinstance(next_start, int)
                                       or next_start <= start):
            raise ValueError(f"next_start {next_start!r} is not a passage start after {start}")
        blocks = Blocks.of(layer, paragraphs, formulas)
        stop = blocks.extent(start, compound=False)
        stop = end if stop is None else stop
        if next_start is not None and next_start < stop:
            stop = blocks.separator_start(start, next_start)
        return max(end, stop)
    if not isinstance(until, str):
        raise HeadError(f"`until` is a {type(until).__name__}, not the passage's last words")
    at = layer.find(until, start)
    if at < 0:
        raise HeadError(f"`until` {until[:40]!r} is not on the layer after the passage's start")
    return max(end, at + len(until))


# --------------------------------------------------------------------------
# the statement cut's blocks (docs/proposals/statement-cut.md section 3.1)
# --------------------------------------------------------------------------

#: the span-end rules this file knows (``cflib.vocab.SPAN_END_RULE``'s versions; a
#: test pins them): 1 ends a passage at its paragraph's end, 2 -- the statement cut's,
#: ``cflib.vocab.SPAN_END_STATEMENT_CUT`` -- at the earlier of its extent and the next
#: passage's start
SPAN_END_STATEMENT_CUT = 2
SPAN_END_RULES = (1, SPAN_END_STATEMENT_CUT)
#: ``paragraphs.json``'s ``block`` names the cut reads (references/locate-documents.md
#: section 7: ``sources.render_html`` writes them)
BLOCK_THEOREM_BOX = "theorem_box"
BLOCK_MATH = "math"
LIST_BLOCKS = ("li", "dd")
#: what ends a sentence, and the TeX spacing a formula's alttext may end with after
#: its last character (``\qquad`` before ``\quad``, so the longer one is read first);
#: ``\right.`` is a null delimiter, not a full stop; a line break ``\\``, an
#: environment's ``\end{…}`` and a closing brace (``{\text{.}}``) stand after the
#: last character too, and are set aside the same way
SENTENCE_STOPS = frozenset(".!?")
#: the stops a formula's last character ends a sentence with: TeX writes a factorial
#: with ``!`` (``n!``, ``x^{n!}``, ``\frac{1}{n!}``), so a formula's ``!`` is none.
#: Read as one it would end a chain early, and gate rule 10 (b) would refuse the
#: ``until`` that runs a passage over a display its sentence introduces
FORMULA_STOPS = SENTENCE_STOPS - {"!"}
TEX_SPACING = ("\\qquad", "\\quad", "\\,", "\\;", "\\:", "\\!", "~")
TEX_NULL_DELIMITER = "\\right."
TEX_LINE_BREAK = "\\\\"
_TEX_END_ENVIRONMENT = re.compile(r"\\end\s*\{[A-Za-z]+\*?\}\Z")
#: a closing bracket or quote a block may end with after its full stop ("(See the
#: notes.)"): read through to the stop when it stands after one
CLOSING_MARKS = frozenset(")]\"'\u2019\u201d\u00bb")
#: where the inventory cuts inside a sentence (``cflib.vocab.CUT_CONNECTIVES``,
#: restated for the gate host; a test pins the two).  The end rule's version 2 leaves
#: the one before the next passage's start out of both passages
CUT_CONNECTIVES = (";", "since", "because", "so", "hence", "then", "moreover", ", and")
#: what else separates two pieces of one sentence: the comma or semicolon before a
#: connective or the next piece
SEPARATOR_MARKS = frozenset(",;")
#: the words a full stop does not end a sentence after (``locate_records.
#: _ABBREVIATIONS``, restated: that module imports this one; a test pins the two)
ABBREVIATIONS = frozenset({"i.e.", "e.g.", "cf.", "resp.", "etc.", "viz.", "vs.", "w.r.t."})


def formula_stop(alttext: str) -> str:
    """The last character of a formula's normalised alttext, what TeX writes after it
    set aside; '' when nothing is left.

    Set aside, from the end and again until nothing more is: TeX spacing, a
    ``\\right.`` null delimiter, a line break ``\\\\``, an environment's closer
    ``\\end{…}`` and a closing brace that is not an escaped ``\\}``.  It is what a
    formula that ends a block or a sentence ends it with: a display ``a = b.\\,``,
    ``\\begin{aligned}a&=b.\\end{aligned}`` or ``a = b{\\text{.}}`` ends its
    sentence with a full stop, one ending ``,`` runs on, and ``\\{x\\}`` ends with
    its brace.  ``x^{n!}`` ends with ``!``, a factorial, which ends no sentence
    (``FORMULA_STOPS``).
    """
    text = normalize_alttext(alttext)
    while True:
        before = text
        text = text.rstrip()
        closer = _TEX_END_ENVIRONMENT.search(text)
        if closer is not None:
            text = text[:closer.start()]
        elif text.endswith("}") and not _escaped(text, len(text) - 1):
            text = text[:-1]
        else:
            for tail in (TEX_NULL_DELIMITER, TEX_LINE_BREAK, *TEX_SPACING):
                if text.endswith(tail) and not _escaped(text, len(text) - len(tail)):
                    text = text[:-len(tail)]
                    break
        if text == before:
            return text[-1:] if text else ""


def _escaped(text: str, at: int) -> bool:
    """Is the character at ``at`` escaped: an odd run of backslashes before it?"""
    run = 0
    while at - run - 1 >= 0 and text[at - run - 1] == "\\":
        run += 1
    return run % 2 == 1


class Block:
    """One span of ``paragraphs.json``: every entry with exactly that span, in table order.

    ``kinds`` holds each entry's ``block`` (a theorem box and its one paragraph
    may share a span, and so may a ``dd`` and the display it holds); ``kind`` is
    the first one's.
    """

    __slots__ = ("start", "end", "kind", "kinds")

    def __init__(self, start: int, end: int, kind: str):
        self.start, self.end, self.kind = start, end, kind
        self.kinds = [kind]

    @property
    def span(self) -> tuple[int, int]:
        return self.start, self.end

    @property
    def theorem_box(self) -> bool:
        return BLOCK_THEOREM_BOX in self.kinds

    @property
    def list_kind(self) -> str | None:
        """``li`` or ``dd`` when the block is a list item, else None."""
        return next((k for k in self.kinds if k in LIST_BLOCKS), None)

    def __repr__(self) -> str:
        return f"Block({self.start}, {self.end}, {'/'.join(self.kinds)})"


class Blocks:
    """The blocks of one source, with its layer and formulas: the definitions of
    docs/proposals/statement-cut.md section 3.1, which the end rule (version 2)
    and gate rule 10 read.

    ``paragraphs`` is ``paragraphs.json``: an entry without a readable non-empty
    span is passed over, as :func:`paragraph_end` passes it over.  The table is
    read in its own order with an outer block before the ones it holds
    (``sources.render_html`` sorts it by start, then the longer first), so the
    first block holding an offset is the outermost one, :func:`paragraph_end`'s
    choice.  ``formulas`` is ``formulas.json``.
    """

    #: what :func:`_is_blocks` reads: this file is imported under two names (``gate.
    #: locate_text`` in the skill, ``locate_text`` beside the gate), so a class check
    #: would tell one module's Blocks from the other's
    _locate_text_blocks = True

    def __init__(self, layer: str, paragraphs: Sequence[dict], formulas: Sequence[dict]):
        self.layer = str(layer)
        #: the table as given: what rule 1 reads (:func:`paragraph_end`)
        self.paragraphs = list(paragraphs or ())
        rows: list[tuple[int, int, int, str]] = []
        for index, para in enumerate(self.paragraphs):
            try:
                a, b = (int(x) for x in para["span"])
            except (KeyError, TypeError, ValueError):
                continue
            if a < b:
                rows.append((a, -b, index, str(para.get("block") or "")))
        rows.sort()
        self.blocks: list[Block] = []
        for a, negative_b, _index, kind in rows:
            if self.blocks and self.blocks[-1].span == (a, -negative_b):
                self.blocks[-1].kinds.append(kind)
            else:
                self.blocks.append(Block(a, -negative_b, kind))
        #: the blocks no other block holds, in layer order: what next() returns
        self.top: list[Block] = []
        reach = -1
        for block in self.blocks:
            if block.end > reach:
                self.top.append(block)
                reach = block.end
        self._top_starts = [b.start for b in self.top]
        spans: list[tuple[int, int, str]] = []
        for formula in formulas or ():
            try:
                fa, fb = (int(x) for x in formula["span"])
            except (KeyError, TypeError, ValueError):
                continue
            spans.append((fa, fb, str(formula.get("alttext") or "")))
        spans.sort()
        self.formulas = spans
        self._formula_starts = [f[0] for f in spans]

    @classmethod
    def of(cls, layer: str, paragraphs: "Sequence[dict] | Blocks",
           formulas: Sequence[dict] | None = None) -> "Blocks":
        """``paragraphs`` itself when it is already a :class:`Blocks`, else one built.

        The cut reads the formulas (a display block, a sentence a display ends),
        so building one takes them: ``()`` for a source that has none, never a
        forgotten argument.
        """
        if _is_blocks(paragraphs):
            return paragraphs
        if formulas is None:
            raise TypeError("the statement cut reads the source's formulas: pass formulas.json")
        return cls(layer, paragraphs, formulas)

    # -- formulas ----------------------------------------------------------

    def formula_at(self, offset: int) -> tuple[int, int, str] | None:
        """The formula whose layer span holds ``offset``, else None."""
        i = bisect_right(self._formula_starts, offset) - 1
        if i >= 0 and self.formulas[i][0] <= offset < self.formulas[i][1]:
            return self.formulas[i]
        return None

    def formulas_in(self, start: int, end: int) -> list[tuple[int, int, str]]:
        """The formulas whose whole layer span lies in ``[start, end)``."""
        i = bisect_right(self._formula_starts, start - 1)
        out = []
        while i < len(self.formulas) and self.formulas[i][0] < end:
            if self.formulas[i][1] <= end:
                out.append(self.formulas[i])
            i += 1
        return out

    # -- the definitions of section 3.1 -------------------------------------

    def block_at(self, offset: int) -> Block | None:
        """block(x): the outermost block holding ``offset``, else None."""
        for block in self.top:
            if block.start > offset:
                break
            if offset < block.end:
                return block
        return None

    def next(self, block: Block) -> Block | None:
        """next(B): the first block no other holds that starts at or after B's end
        with only whitespace between, else None."""
        i = bisect_right(self._top_starts, block.end - 1)
        if i >= len(self.top):
            return None
        following = self.top[i]
        if not all(_is_white(ch) for ch in self.layer[block.end:following.start]):
            return None
        return following

    def is_display(self, block: Block) -> bool:
        """A display block: it holds a formula, and its text outside its formulas
        holds no letter (a display, or a ``dd`` holding only one)."""
        inside = self.formulas_in(block.start, block.end)
        if not inside:
            return False
        cursor = block.start
        for fa, fb, _alt in inside:
            if any(ch.isalpha() for ch in self.layer[cursor:fa]):
                return False
            cursor = fb
        return not any(ch.isalpha() for ch in self.layer[cursor:block.end])

    def last_character(self, start: int, end: int) -> str:
        """The last non-space character of ``[start, end)``, a trailing formula read as
        :func:`formula_stop` of its alttext; '' when there is none.

        Closing brackets and quotes (``CLOSING_MARKS``) after a full stop are read
        through to it: ``(See the notes.)`` ends with its full stop, and ``(x > 0)``
        with its bracket.
        """
        return self._last(start, end)[0]

    def _last(self, start: int, end: int) -> tuple[str, bool]:
        """:meth:`last_character`, and whether it is a full stop: one of
        ``SENTENCE_STOPS`` in the text, one of ``FORMULA_STOPS`` read from a formula
        (so ``(n!)`` ends with its bracket, the factorial being no stop)."""
        i = end - 1
        closing = ""
        while i >= start:
            if _is_white(self.layer[i]):
                i -= 1
                continue
            formula = self.formula_at(i)
            if formula is None:
                ch = self.layer[i]
                if ch in CLOSING_MARKS:
                    closing = closing or ch
                    i -= 1
                    continue
                stops = ch in SENTENCE_STOPS
            else:
                ch = formula_stop(formula[2])
                if not ch:
                    i = formula[0] - 1
                    continue
                stops = ch in FORMULA_STOPS
            return (ch, True) if stops else (closing or ch, False)
        return closing, False

    def introduces(self, block: Block) -> bool:
        """introduces(B): B's last non-space character is no full stop, so its last
        sentence runs on into what follows."""
        last, stops = self._last(block.start, block.end)
        return bool(last) and not stops

    def chain(self, block: Block) -> list[Block]:
        """chain(B): B, then the display blocks its last sentence introduces.

        A list item (``li``) is a unit of its own, never a display a sentence
        introduces: a list of display items is one passage per item (or a
        compound statement over them, :meth:`list_run`), so the chain stops at
        one.  A ``dd`` is how an article indents a display, and joins.
        """
        out = [block]
        while self.introduces(out[-1]):
            following = self.next(out[-1])
            if following is None or "li" in following.kinds or not self.is_display(following):
                break
            out.append(following)
        return out

    def list_run(self, block: Block) -> list[Block]:
        """list run(B): an ``li`` or ``dd`` and the next() blocks of its kind after it."""
        kind = block.list_kind
        out = [block]
        if kind is None:
            return out
        while True:
            following = self.next(out[-1])
            if following is None or kind not in following.kinds:
                return out
            out.append(following)

    def extent(self, start: int, *, compound: bool = False) -> int | None:
        """extent(s, compound): the furthest a passage that starts at ``start`` may run.

        None when no block holds the start; a theorem box's end when the start's
        block is one; else the end of the last block of chain(L), L the last
        item of the start's list run when ``compound`` (a compound statement
        over the items of one list), and the start's own block otherwise.
        """
        block = self.block_at(start)
        if block is None:
            return None
        if block.theorem_box:
            return block.end
        last = self.list_run(block)[-1] if compound and block.list_kind else block
        return self.chain(last)[-1].end

    def separator_start(self, start: int, at: int) -> int:
        """Where the separator before ``at`` (the next passage's start) begins.

        Back from ``at``, and again while anything is set aside: whitespace, one
        connective of ``CUT_CONNECTIVES`` as a whole word (``, and`` only after its
        comma), and a comma or semicolon -- never a character inside a formula and
        never back to ``start``.  So ``…sprockets; every…`` ends its first piece
        after ``sprockets``, and ``…gadget, since gadgets…`` after ``gadget``: the
        span of the passage before is what an ``until`` at its last word gives,
        whatever the next passage is.
        """
        layer = self.layer
        i = at
        while i > start:
            j = i
            while j > start and _is_white(layer[j - 1]) and self.formula_at(j - 1) is None:
                j -= 1
            j = self._before_connective(start, j)
            if j == i:
                break
            i = j
        return i

    def _before_connective(self, start: int, j: int) -> int:
        """``j`` less one connective word or one separating mark ending at it."""
        layer = self.layer
        if j - 1 > start and layer[j - 1] in SEPARATOR_MARKS and self.formula_at(j - 1) is None:
            return j - 1
        for connective in CUT_CONNECTIVES:
            word = connective.lstrip(",; ")
            if not word:
                continue
            k = j - len(word)
            if k <= start or layer[k:j].lower() != word or self.formula_at(k) is not None \
                    or self.formula_at(j - 1) is not None:
                continue
            before = layer[k - 1]
            if not (_is_white(before) or before in SEPARATOR_MARKS):
                continue
            if connective.startswith(","):
                # ", and": the word counts only after its comma
                m = k
                while m > start and _is_white(layer[m - 1]):
                    m -= 1
                if not (m > start and layer[m - 1] == ","):
                    continue
            return k
        return j

    # -- units (section 3.3, the receipt's counts) ---------------------------

    def units(self, span: Sequence[int]) -> dict[str, list[list[int]]]:
        """The sentences and displays of the passage at ``span``: :func:`units`."""
        start, end = int(span[0]), int(span[1])
        layer = self.layer
        stops: list[int] = []          # offsets just past a sentence's last character
        i = start
        while i < end:
            formula = self.formula_at(i)
            if formula is not None:
                if formula_stop(formula[2]) in FORMULA_STOPS and formula[1] <= end:
                    stops.append(formula[1])
                i = max(formula[1], i + 1)
                continue
            if layer[i] in SENTENCE_STOPS and not _abbreviation(layer, start, i):
                stops.append(i + 1)
            i += 1
        sentences: list[list[int]] = []
        cursor = start
        block_starts = {b.start for b in self.blocks}
        for stop in stops:
            j = stop
            if j >= end:
                continue
            if _is_white(layer[j]):
                while j < end and _is_white(layer[j]):
                    j += 1
                if j >= end:
                    continue
            elif j not in block_starts:
                # glued to the next word: no sentence ends here, unless a block does
                continue
            if not (layer[j].isupper() or self.formula_starts_at(j)):
                continue
            sentences.append([cursor, stop])
            cursor = j
        tail = end
        while tail > cursor and _is_white(layer[tail - 1]):
            tail -= 1
        if tail > cursor:
            sentences.append([cursor, tail])
        displays = [list(b.span) for b in self.blocks
                    if BLOCK_MATH in b.kinds and start <= b.start and b.end <= end]
        return {"sentences": sentences, "displays": displays}

    def formula_starts_at(self, offset: int) -> bool:
        i = bisect_right(self._formula_starts, offset) - 1
        return i >= 0 and self.formulas[i][0] == offset


def _is_blocks(value: object) -> bool:
    """Is ``value`` a :class:`Blocks`, of this module or of its twin under another name?"""
    return getattr(type(value), "_locate_text_blocks", False) is True


def _abbreviation(layer: str, start: int, stop: int) -> bool:
    """Is the full stop at ``stop`` the end of an abbreviation (``ABBREVIATIONS``)?"""
    i = stop
    while i > start and not _is_white(layer[i - 1]):
        i -= 1
    return layer[i:stop + 1].lstrip("([{'\"").lower() in ABBREVIATIONS


def extent(layer: str, paragraphs: "Sequence[dict] | Blocks", formulas: Sequence[dict] | None,
           start: int, *, compound: bool = False) -> int | None:
    """:meth:`Blocks.extent` of one source: what gate rule 10 (b) holds an end to."""
    return Blocks.of(layer, paragraphs, formulas).extent(start, compound=compound)


def units(layer: str, paragraphs: "Sequence[dict] | Blocks", formulas: Sequence[dict] | None,
          span: Sequence[int]) -> dict[str, list[list[int]]]:
    """``{"sentences": [[a, b], …], "displays": [[a, b], …]}`` of the passage at ``span``.

    A sentence ends at a ``.``, ``!`` or ``?`` before whitespace and an
    upper-case letter or a formula, not after one of ``ABBREVIATIONS`` and
    never inside a formula; a formula whose alttext ends with ``.`` or ``?``
    (:func:`formula_stop`: ``a = b.``; ``FORMULA_STOPS``, a formula's ``!``
    being a factorial) ends a sentence too, and so does one
    that ends a block when the next block starts with no whitespace between
    (two paragraphs of a theorem box are glued in the layer).  Each sentence is
    its span, trailing whitespace left out.  The displays are the ``math``
    blocks of ``paragraphs.json`` inside the span.  The statement cut's harvest
    receipt counts both per passage (docs/proposals/statement-cut.md section
    3.3), and refuses nothing on them.
    """
    return Blocks.of(layer, paragraphs, formulas).units(span)
