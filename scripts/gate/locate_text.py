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
   hand-counted offset.  It has three versions, the ``span_end_rule`` a run's
   ``gate.json`` names: version 1 ends a passage that gives no ``until`` at the
   end of its paragraph; version 2, the statement cut's
   (docs/proposals/statement-cut.md section 3.2), at the earlier of its
   *extent* and the next passage's start, less the separator before it;
   version 3 the same with the *continued* extent, which runs on over the
   paragraph that finishes a sentence after its display.
5. **The blocks** the statement cut reads (the same proposal, section 3.1):
   :class:`Blocks` over ``paragraphs.json`` and ``formulas.json`` gives each
   offset its outermost block, a block the next one, and the definitions the
   end rule and gate rule 10 rest on -- a display block, a block whose last
   sentence introduces what follows, the displays a sentence introduces
   (:meth:`Blocks.chain`), a run of list items and the extent;
   :func:`units` counts a passage's sentences and displays for the harvest
   receipt.  Rule 1 reads none of it, so a run planned before the statement cut
   ends, gates and counts as it did.  The same section defines the *continued*
   extent -- the paragraph that finishes a sentence after its display
   (:meth:`Blocks.continuation`), which ``chain`` and ``extent`` join only
   when asked (``continued=True``), and the looser bound gate rule 10 (b)
   holds an end to under statement-cut rule 2 (``loose=True``: any sentence
   that runs on into a paragraph that continues it, the display between
   seen or not) -- and the connectives inside a span
   (:meth:`Blocks.cut_points`).  Only span-end rule 3 and statement-cut rule
   2 read them, so every end and every gate verdict of an earlier version is
   the one it was.  The same section defines the *wide* bound beside them
   (``wide=True``: the loose bound read across footnote marks, ``FOOTNOTE_MARK``,
   and into a ``dd`` or a paragraph that finishes the sentence,
   :meth:`Blocks.finishes`, :meth:`Blocks.opens_with_formula`) and the tails only
   it reaches (:meth:`Blocks.wide_tails`); only statement-cut rule 3 reads it
   (gate rule 10 (b)), and ``continues``, ``continuation`` and every default end
   are untouched by it.

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
    * **rule 3** (``SPAN_END_CONTINUED``, the same section) is rule 2 with the
      *continued* extent (``continued=True``): the paragraph that finishes a
      sentence after its display (:meth:`Blocks.continuation`) is the
      sentence's, so "…such that [display] for all x." ends after "x.".

    None ever ends before the end of the quote.  Rule 1 reads neither
    ``next_start`` nor ``formulas``; rules 2 and 3 need the source's ``formulas``
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
        stop = blocks.extent(start, compound=False, continued=rule >= SPAN_END_CONTINUED)
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
#: passage's start, and 3 -- ``cflib.vocab.SPAN_END_CONTINUED`` -- the same with the
#: continued extent
SPAN_END_STATEMENT_CUT = 2
SPAN_END_CONTINUED = 3
SPAN_END_RULES = (1, SPAN_END_STATEMENT_CUT, SPAN_END_CONTINUED)
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
#: the one block a sentence continues into after its display: a plain paragraph, never a
#: list item, a ``dd``, a quotation or a theorem box (docs/proposals/statement-cut.md
#: section 3.1, continuation(D))
CONTINUATION_BLOCK = "p"
#: the blocks a continuation may leave, beside a display block: a paragraph, a ``dd``
#: and a display.  A container (a ``blockquote``, a theorem box, a list item) is a unit,
#: and its sentence never runs on out of it
CONTINUED_FROM = frozenset({"p", "dd", BLOCK_MATH})
#: the opening brackets and quotes read past to a paragraph's first word ("(that is, …");
#: never ``[``, which opens a footnote mark
OPENING_MARKS = frozenset("(\"'\u2018\u201c")
#: a word that labels an item rather than going on with a sentence: a run of one to
#: four letters closed by a bracket ("(ii)", "a)")
_ITEM_LABEL = re.compile(r"[^\W\d_]{1,4}\)")
_LETTERS = re.compile(r"[^\W\d_]*")
#: the words a full stop does not end a sentence after (``locate_records.
#: _ABBREVIATIONS``, restated: that module imports this one; a test pins the two)
ABBREVIATIONS = frozenset({"i.e.", "e.g.", "cf.", "resp.", "etc.", "viz.", "vs.", "w.r.t."})
#: what a ``sup.reference`` leaves in the layer between two blocks (the renderer drops it
#: from every block span): ``[12]``, ``[a]``, ``[nb 3]``, ``[note 3]``.  Only the wide
#: bound reads it (:meth:`Blocks.next` with ``marks=True``), so ``[0, 1]``, ``[edit]`` and
#: a heading's text are no mark and still end a run (docs/proposals/statement-cut.md
#: section 3.1)
FOOTNOTE_MARK = re.compile(r"\[(?:[0-9]{1,4}|[a-z]|(?:nb|note) [0-9]{1,4})\]")


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
    docs/proposals/statement-cut.md section 3.1, which the end rule (versions 2
    and 3) and gate rule 10 read, with the continued chain, the loose bound and
    the cut points the same section defines, which only span-end rule 3 and
    statement-cut rule 2 read, and the wide bound, which only statement-cut rule 3
    reads.

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
        #: the display formulas (``math`` blocks), by start: what ends_with_display() reads
        self._math = [b for b in self.blocks if BLOCK_MATH in b.kinds]
        self._math_starts = [b.start for b in self._math]
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

    def next(self, block: Block, *, marks: bool = False) -> Block | None:
        """next(B): the first block no other holds that starts at or after B's end
        with only whitespace between, else None.

        With ``marks`` (the wide bound's next across marks, docs/proposals/
        statement-cut.md section 3.1), the text between may also hold footnote
        marks: with every ``FOOTNOTE_MARK`` removed only whitespace is left, and no
        formula has a character between the two blocks.  ``[0, 1]``, ``[edit]`` and
        a heading's text are no mark, so no next block is found across them.
        """
        i = bisect_right(self._top_starts, block.end - 1)
        if i >= len(self.top):
            return None
        following = self.top[i]
        gap = self.layer[block.end:following.start]
        if not all(_is_white(ch) for ch in gap):
            if not marks or not all(_is_white(ch) for ch in FOOTNOTE_MARK.sub("", gap)) \
                    or self._formula_between(block.end, following.start):
                return None
        return following

    def _formula_between(self, start: int, end: int) -> bool:
        """Does a formula have a character in ``[start, end)``?"""
        i = bisect_right(self._formula_starts, start) - 1
        if i < 0:
            i = 0
        while i < len(self.formulas) and self.formulas[i][0] < end:
            if self.formulas[i][1] > start:
                return True
            i += 1
        return False

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

    def ends_with_display(self, block: Block) -> bool:
        """ends_with_display(D): D is a display block, or a paragraph or ``dd``
        (``CONTINUED_FROM``) whose last content is a display formula -- a ``math``
        block inside it with only whitespace after it to D's end.

        A container (a ``blockquote``, a theorem box, a list item) that ends with a
        display does not, nor one that holds only a display: it is a unit, and its
        sentence never runs on out of it.  So a block's kinds are read before it is
        read as a display, and the continued extent never runs past the loose
        bound, which stops at a theorem box (:meth:`runs_on`).  A footnote mark
        after a nested display makes this false too (its text is not whitespace),
        a known false negative.
        """
        if not set(block.kinds) <= CONTINUED_FROM:
            return False
        if self.is_display(block):
            return True
        i = bisect_right(self._math_starts, block.end - 1) - 1
        if i < 0:
            return False
        shown = self._math[i]
        if shown.start < block.start or shown.end > block.end or shown.span == block.span:
            return False
        return all(_is_white(ch) for ch in self.layer[shown.end:block.end])

    def continues(self, block: Block) -> bool:
        """continues(N): N's first word goes on with a sentence rather than starting one.

        The first word is the one at N's first non-space character, read past the
        opening marks ``OPENING_MARKS`` (never ``[``, a footnote mark's), with a
        trailing ``,``, ``;`` or ``:`` set aside.  It continues when it is one of
        ``ABBREVIATIONS`` as written, lower-case ("i.e."; "E.g." starts a new
        sentence with its capital), or when its leading run of letters starts with
        a lower-case letter and is two letters or more, or the word "a".  A lone
        lower-case letter is a variable written as text (``x is…``), and a digit,
        an upper-case letter or a formula starts a new sentence, so none of them
        continues; nor does an item label, a run of one to four letters closed by a
        bracket ("(ii)", "a)").  A name particle or a function name written as text
        ("von Neumann", "ln x") reads as a continuation: a known false positive
        (docs/proposals/statement-cut.md section 3.1).
        """
        layer = self.layer
        i = block.start
        while i < block.end and _is_white(layer[i]):
            i += 1
        while i < block.end and layer[i] in OPENING_MARKS and self.formula_at(i) is None:
            i += 1
        if i >= block.end or self.formula_at(i) is not None:
            return False
        j = i
        while j < block.end and not _is_white(layer[j]) and self.formula_at(j) is None:
            j += 1
        word = layer[i:j]
        if word.rstrip(",;:") in ABBREVIATIONS:
            return True
        if _ITEM_LABEL.match(word):
            return False
        letters = _LETTERS.match(word).group(0)
        return letters[:1].islower() and (len(letters) >= 2 or letters == "a")

    def continuation(self, block: Block) -> Block | None:
        """continuation(D): the paragraph that finishes D's last sentence after its
        display, else None.

        D is no list item, ends with a display (:meth:`ends_with_display`) and
        introduces what follows (:meth:`introduces`: the display has no full
        stop); N = next(D) is a plain paragraph (``CONTINUATION_BLOCK`` its only
        kind: never an ``li``, a ``dd``, a theorem box or a display, and no next
        block across a heading's text); and N continues (:meth:`continues`).
        Then N is the continuation: "…such that [display] for all x." is one
        sentence over three blocks.
        """
        if "li" in block.kinds or not self.ends_with_display(block) \
                or not self.introduces(block):
            return None
        following = self.next(block)
        if following is None or set(following.kinds) != {CONTINUATION_BLOCK}:
            return None
        return following if self.continues(following) else None

    def opens_with_formula(self, block: Block, before: Block) -> bool:
        """opens_with_formula(N, before): N goes on with the sentence ``before`` left
        open, with a formula as its first word (docs/proposals/statement-cut.md
        section 3.1; only the wide bound reads it).

        N is no display block; its first non-space character is inside a formula;
        the first word after its leading formulas, whitespace and ``,`` ``;`` ``:``
        set aside, is no item label, and its leading run of letters starts with a
        lower-case letter and is two letters or more, or is the word "a"; and
        ``before`` ends with a word: its last non-space character lies outside
        every formula.  So "…for an n by n matrix" and then "A requires…" is one
        sentence, while a paragraph that opens "x etc. are…" after one that ends
        with a formula, or "A Then…" with a capital after its formula, is not.
        There is no abbreviation clause.  Nothing here reads a full stop: the run
        that asks (:meth:`runs_on`) reaches N only from a sentence that
        :meth:`introduces` it.  A missing full stop before a sentence that opens
        with a formula ("…see the integers" and then "Z is a domain.") reads as one
        sentence: a known false positive, pinned by a test.
        """
        if self.is_display(block):
            return False
        layer = self.layer
        i = block.start
        while i < block.end and _is_white(layer[i]):
            i += 1
        if i >= block.end or self.formula_at(i) is None:
            return False
        while i < block.end:
            formula = self.formula_at(i)
            if formula is not None:
                i = formula[1]
            elif _is_white(layer[i]) or layer[i] in ",;:":
                i += 1
            else:
                break
        if i >= block.end:
            return False
        j = i
        while j < block.end and not _is_white(layer[j]) and self.formula_at(j) is None:
            j += 1
        word = layer[i:j]
        if _ITEM_LABEL.match(word):
            return False
        letters = _LETTERS.match(word).group(0)
        if not (letters[:1].islower() and (len(letters) >= 2 or letters == "a")):
            return False
        k = before.end - 1
        while k >= before.start and _is_white(layer[k]):
            k -= 1
        return k >= before.start and self.formula_at(k) is None

    def finishes(self, block: Block, before: Block) -> bool:
        """finishes(N, before): N's first word goes on with ``before``'s open sentence --
        it :meth:`continues`, or ``before`` is a paragraph, a ``dd`` or a display
        (``CONTINUED_FROM``) and N :meth:`opens_with_formula` after it.  The kinds
        guard keeps a ``dt`` term and a quotation from running on into a ``dd`` or a
        paragraph that opens with a formula."""
        if self.continues(block):
            return True
        return set(before.kinds) <= CONTINUED_FROM and self.opens_with_formula(block, before)

    def runs_on(self, block: Block, *, wide: bool = False) -> list[Block]:
        """The looser continuation gate rule 10 (b) reads under statement-cut rule 2
        (docs/proposals/statement-cut.md section 3.3, the maintainer's reading K1):
        the blocks D's last sentence runs on over to the paragraph that finishes it,
        else ``[]``.

        D is no list item and no theorem box (each a unit) and introduces what
        follows; then any ``dd`` or display blocks, each introducing what follows
        in turn -- a display the gate sees, or an indented line it cannot read as
        one (a ``texhtml`` display, a ``dd`` of text) -- and then a plain paragraph
        that :meth:`continues`.  It needs no display the gate can see, and leaves
        a container too, so it holds every :meth:`continuation` and more: a
        bound that only ever admits a longer ``until``.  The default end never
        reads it.

        With ``wide`` (the wide runs on, docs/proposals/statement-cut.md section
        3.1), the walk is the same with three differences: it reads :meth:`next`
        across footnote marks (``marks=True``); a plain paragraph ends the run when
        it :meth:`finishes` the sentence, not only when it continues it; and a
        ``dd`` that is no display block also ends it, included, when the block
        before is a paragraph, a ``dd`` or a display (``CONTINUED_FROM``) and the
        ``dd`` finishes it.  Any other ``dd`` or display block is passed over while
        it introduces, as without.  No end rule reads it.
        """
        out: list[Block] = []
        last = block
        while True:
            if "li" in last.kinds or last.theorem_box or not self.introduces(last):
                return []
            following = self.next(last, marks=wide)
            if following is None:
                return []
            if set(following.kinds) == {CONTINUATION_BLOCK}:
                done = self.finishes(following, last) if wide else self.continues(following)
                return out + [following] if done else []
            if "li" in following.kinds or not set(following.kinds) <= {"dd", BLOCK_MATH}:
                return []
            if wide and "dd" in following.kinds and not self.is_display(following) \
                    and set(last.kinds) <= CONTINUED_FROM and self.finishes(following, last):
                return out + [following]
            out.append(following)
            last = following

    def chain(self, block: Block, *, continued: bool = False,
              loose: bool = False, wide: bool = False) -> list[Block]:
        """chain(B): B, then the display blocks its last sentence introduces.

        A list item (``li``) is a unit of its own, never a display a sentence
        introduces: a list of display items is one passage per item (or a
        compound statement over them, :meth:`list_run`), so the chain stops at
        one.  A ``dd`` is how an article indents a display, and joins.

        With ``continued`` (the continued chain), when B is no list item: while
        the chain's last block has a :meth:`continuation`, that paragraph and the
        displays its own last sentence introduces join too, so a sentence that
        goes on after its display is one sentence to its end, over as many
        displays and continuations as it has (span-end rule 3).  Without it, the
        chain is the one span-end rules 1 and 2 and statement-cut rule 1 read.
        ``loose`` (gate rule 10 (b) under statement-cut rule 2) joins what
        :meth:`runs_on` names in place of the continuation, and implies
        ``continued``.  ``wide`` (the wide chain, gate rule 10 (b) under
        statement-cut rule 3) implies ``loose``, and both the display join and
        :meth:`runs_on` read across footnote marks and with ``wide=True``.
        """
        loose = loose or wide
        out = [block]
        while True:
            while self.introduces(out[-1]):
                following = self.next(out[-1], marks=wide)
                if following is None or "li" in following.kinds or not self.is_display(following):
                    break
                out.append(following)
            if not (continued or loose) or "li" in block.kinds:
                return out
            if loose:
                tail = self.runs_on(out[-1], wide=wide)
                if not tail:
                    return out
                out.extend(tail)
                continue
            tail = self.continuation(out[-1])
            if tail is None:
                return out
            out.append(tail)

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

    def extent(self, start: int, *, compound: bool = False, continued: bool = False,
               loose: bool = False, wide: bool = False) -> int | None:
        """extent(s, compound): the furthest a passage that starts at ``start`` may run.

        None when no block holds the start; a theorem box's end when the start's
        block is one; else the end of the last block of chain(L), L the last
        item of the start's list run when ``compound`` (a compound statement
        over the items of one list), and the start's own block otherwise.
        ``continued`` reads the continued chain (:meth:`chain`): the *continued
        extent*, which runs on over the paragraph that finishes a sentence after
        its display (an ``li`` L gets none; a ``dd`` run may continue).  Like
        the extent it is an upper bound, never a unit: a continuation that goes
        on into further sentences is included to its end.  ``loose`` reads the
        loose chain (:meth:`runs_on`), the bound gate rule 10 (b) holds an end to
        under statement-cut rule 2; it is never shorter than the continued
        extent.  ``wide`` reads the wide chain: the *wide extent*, never shorter
        than the loose one, which no end rule reads.
        """
        block = self.block_at(start)
        if block is None:
            return None
        if block.theorem_box:
            return block.end
        last = self.list_run(block)[-1] if compound and block.list_kind else block
        return self.chain(last, continued=continued, loose=loose, wide=wide)[-1].end

    def continuations(self) -> list[tuple[Block, Block]]:
        """``[(D, N)]``: every block no other holds whose :meth:`continuation` is N, in
        layer order (the receipt's counts under statement-cut rule 2)."""
        out = []
        for block in self.top:
            tail = self.continuation(block)
            if tail is not None:
                out.append((block, tail))
        return out

    def loose_tails(self) -> list[tuple[list[Block], Block]]:
        """``[(head, N)]``: every paragraph N that the loose bound alone reaches -- the
        last block of some :meth:`runs_on`, and no :meth:`continuation` -- with the
        blocks its sentence runs on over, from the first block no other holds whose
        :meth:`runs_on` reaches N, in layer order (the receipt's loose counts under
        statement-cut rule 2).  These are the tails the default end stops before:
        after a display the gate cannot see, a paragraph with no full stop, or a
        container's sentence."""
        strict = {tail.span for _block, tail in self.continuations()}
        seen: set[tuple[int, int]] = set()
        out = []
        for block in self.top:
            run = self.runs_on(block)
            if not run or run[-1].span in strict or run[-1].span in seen:
                continue
            seen.add(run[-1].span)
            out.append(([block] + run[:-1], run[-1]))
        return out

    def wide_tails(self) -> list[tuple[list[Block], Block]]:
        """``[(head, N)]``: every block N that the wide chain (:meth:`chain` with
        ``wide=True``) of an earlier block no other holds reaches and the loose chain
        of none reaches, in layer order, with ``head`` the blocks of the first such
        wide chain before N (docs/proposals/statement-cut.md section 3.1).  These are
        what the wide bound alone admits an ``until`` into: across a footnote mark,
        into a ``dd`` that finishes a sentence, into a paragraph that opens with a
        formula.  No tail is a :meth:`continuation` or one of :meth:`loose_tails`."""
        reached = {b.span for block in self.top for b in self.chain(block, loose=True)[1:]}
        seen: set[tuple[int, int]] = set()
        out = []
        for block in self.top:
            run = self.chain(block, wide=True)
            for k in range(1, len(run)):
                tail = run[k]
                if tail.span in reached or tail.span in seen:
                    continue
                seen.add(tail.span)
                out.append((run[:k], tail))
        return out

    def mid_sentence(self, start: int) -> bool:
        """Does a passage that starts at ``start`` start inside a sentence?

        Not at its block's first word, and the last character before it -- read
        back past whitespace and commas or semicolons, a trailing formula by its
        alttext (:meth:`last_character`) -- is no full stop.  "…a gadget, since
        gadgets…" starts "since" inside one; "…spans. Then…" starts "Then" a
        sentence of its own.
        """
        block = self.block_at(start)
        if block is None:
            return False
        layer = self.layer
        first = block.start
        while first < block.end and _is_white(layer[first]):
            first += 1
        if start <= first:
            return False
        i = start
        while i > block.start and self.formula_at(i - 1) is None and (
                _is_white(layer[i - 1]) or layer[i - 1] in SEPARATOR_MARKS):
            i -= 1
        last, stops = self._last(block.start, i)
        return bool(last) and not stops

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

    def cut_points(self, span: Sequence[int]) -> list[tuple[int, str]]:
        """``[(offset, connective)]``: where a connective of ``CUT_CONNECTIVES`` stands
        strictly inside the span ``[a, b)``, in layer order.

        A ``;``; a comma followed (across whitespace) by the whole word ``and`` (the
        connective ``, and``; a bare "and" is none); and a word connective
        ("since", "then", …) as a whole word, case aside: whitespace or a comma
        or semicolon before it, and whitespace or one after it.  Nothing inside a
        formula counts, nor one at the span's start, nor one after which no word
        of the span follows.  Each is a point :meth:`separator_start` sets aside
        whole, so a passage that started at the word after it would end the one
        before at or before the point.  It is an upper bound on where the cut
        rules cut ("if … then", "so that" and a sentence that starts "Then" are
        points too), which is how a reader is to count it (docs/proposals/
        statement-cut.md section 3.1).
        """
        a, b = int(span[0]), int(span[1])
        layer = self.layer
        words = [c for c in CUT_CONNECTIVES if c.lstrip(",; ") and not c.startswith(",")]
        points: list[tuple[int, str]] = []
        i = a + 1
        while i < b:
            formula = self.formula_at(i)
            if formula is not None:
                i = max(formula[1], i + 1)
                continue
            ch = layer[i]
            if ch == ";":
                if self._word_follows(i + 1, b):
                    points.append((i, ";"))
            elif ch == ",":
                m = i + 1
                while m < b and _is_white(layer[m]) and self.formula_at(m) is None:
                    m += 1
                if self._whole_word(m, "and", b) and self._word_follows(m + 3, b):
                    points.append((i, ", and"))
            elif _is_white(layer[i - 1]) or layer[i - 1] in SEPARATOR_MARKS:
                for word in words:
                    if self._whole_word(i, word, b) and self._word_follows(i + len(word), b):
                        points.append((i, word))
                        break
            i += 1
        return points

    def _whole_word(self, at: int, word: str, end: int) -> bool:
        """Is ``word`` at ``at`` (case aside), outside every formula, with whitespace or a
        comma or semicolon after it before ``end``?"""
        after = at + len(word)
        if after >= end or self.layer[at:after].lower() != word:
            return False
        if any(self.formula_at(k) is not None for k in range(at, after)):
            return False
        return _is_white(self.layer[after]) or self.layer[after] in SEPARATOR_MARKS

    def _word_follows(self, at: int, end: int) -> bool:
        """Does a word of ``[at, end)`` follow ``at``, across whitespace and commas or
        semicolons?"""
        while at < end and self.formula_at(at) is None and (
                _is_white(self.layer[at]) or self.layer[at] in SEPARATOR_MARKS):
            at += 1
        return at < end

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
           start: int, *, compound: bool = False, continued: bool = False,
           loose: bool = False, wide: bool = False) -> int | None:
    """:meth:`Blocks.extent` of one source: what gate rule 10 (b) holds an end to."""
    return Blocks.of(layer, paragraphs, formulas).extent(start, compound=compound,
                                                         continued=continued, loose=loose,
                                                         wide=wide)


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
