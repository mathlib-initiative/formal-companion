"""Where the line ranges of a TeX source fall on the pages of the PDF typeset from it.

New code, for `cf export roadmap-locators` (docs/exporters.md section 9).  A
companion's locators say which lines of a manuscript's TeX a statement comes
from, and the overlay draws boxes on the manuscript's PDF.  Nothing in a PDF
names a source line, and the build that could (position markers, as the
informalize runtime's `extract_markers.py` reads them) is not the companion's
to rerun, so a line range is found by what it prints:

* the prose words of the source (:class:`Source`: comments, mathematics and
  control sequences blanked, each word with its line) are aligned with the
  words of the PDF's own text layer (:class:`Layout`, from ``pdftotext
  -bbox-layout``), first by the four-word runs that occur once in each, which
  say where in the PDF to look, then word by word over that window;
* the first and the last word matched inside a range give its first and last
  typeset rows; rows that print no matched word (a displayed formula) are
  shared out between the ranges on either side by the source lines with
  content that each has there;
* a typeset row that holds the end of one range and the start of the next goes
  to exactly one of them, the one that has more of it, and both ranges decide
  that by the same rule (:meth:`Placer.place`);
* a box runs from its first row to its last on each page, its top and bottom
  edges in the white space between rows (:func:`boxes`), held against the
  rendered page where poppler's ``pdftoppm`` is on PATH (:class:`Ink`, over
  the runtime's own ``PageInk``); ranges with the same rows share one box, and
  a box that encloses another is drawn wider, so their edges do not coincide.

SyncTeX is deliberately not read.  Its forward search answers for a source
line with no node of its own, such as the last line of a paragraph, with the
first line of the next paragraph, so an extent taken from it reaches into its
neighbour.

Words are compared as ASCII letters after Unicode decomposition, lower-cased,
two letters or more.  A passage in another script, or one that is mathematics
alone between two others that are, is not placed, and is reported.
"""
from __future__ import annotations

import bisect
import difflib
import html
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["PROGRAM", "INK_PROGRAM", "MIN_WORD_AGREEMENT", "PlaceError", "Source", "Row", "Word",
           "Layout", "Placer", "Extent", "Ink", "Box", "boxes", "detex", "words_of"]

#: poppler's program the rows cannot be read without
PROGRAM = "pdftotext"
#: and the one that renders a page, without which no edge is held against the ink
INK_PROGRAM = "pdftoppm"
#: A run of this many words that occurs once in the source and once in the PDF is an anchor.
NGRAM = 4
#: Source lines read on either side of a range, for the words that bound it.
CONTEXT_LINES = 30
#: PDF words read on either side of the anchors.
CONTEXT_WORDS = 400
#: A matching block shorter than this is chance, unless it is one word of LONE_WORD letters.
MIN_BLOCK = 2
LONE_WORD = 7
#: Below this share of the ranges' words found in the PDF, the PDF is not this source's.
MIN_WORD_AGREEMENT = 0.5
#: Points an edge may move to reach a blank pixel row.
REACH_PT = 3.5
#: Points of white kept above a page's first row and below its last.
LEAD_PT = 2.0
#: Points a box stands out from the text block, and how much more for each box inside it.
PAD_PT = 6.0
NEST_PT = 3.0
#: Points a box that encloses another is moved off the inner one's top and bottom.
NUDGE_PT = 0.8
#: How deep a nesting is still drawn apart.
MAX_NEST = 7

_MATH_ENVIRONMENTS = (r"equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?|displaymath|"
                      r"alignat\*?|flalign\*?|math")
#: Control sequences whose braced argument is not printed as words.
_SILENT = (r"label|ref|eqref|autoref|cref|Cref|pageref|cite|citep|citet|nocite|begin|end|input|"
           r"include|bibitem|bibliography|bibliographystyle|url|href|usepackage|documentclass|"
           r"newcommand|renewcommand|providecommand|newtheorem|theoremstyle|includegraphics")
_ACCENT = re.compile(r"""\\(?:['"`^~=.]|[cvHuk](?![A-Za-z]))\s*\{?([A-Za-z])\}?""")
#: A page number alone: arabic, or a roman numeral that is well formed.
_FOLIO = re.compile(r"[0-9]{1,4}|(?=[ivxlcdm])m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})"
                    r"(?:ix|iv|v?i{0,3})", re.IGNORECASE)


class PlaceError(Exception):
    """An input the placement cannot read; the message names it."""


# ---------------------------------------------------------------------------
# words
# ---------------------------------------------------------------------------

def _token(word: str) -> str:
    """A word as it is compared: decomposed, its marks dropped, lower-cased, ASCII letters only."""
    flat = unicodedata.normalize("NFKD", word)
    return re.sub(r"[^a-z]", "", flat.lower())


def words_of(text: str) -> list[str]:
    """The comparable words of a run of text, in order: two letters or more."""
    out = []
    for piece in re.findall(r"[^\W\d_]+", unicodedata.normalize("NFKC", text)):
        token = _token(piece)
        if len(token) >= 2:
            out.append(token)
    return out


def _blank(match: re.Match[str]) -> str:
    """The match as spaces, its line breaks kept, so that every later line keeps its number."""
    return re.sub(r"[^\n]", " ", match.group(0))


def detex(source: str) -> str:
    """The source with what does not print as prose blanked, line for line.

    Comments, displayed and inline mathematics, the arguments of the control
    sequences that print no words (a label, a citation key, an environment's
    name) and every other control sequence's own name go; an accent leaves its
    letter.  What remains is the words a reader of the page sees, on the lines
    the source has them, and nothing else is promised of it.
    """
    text = re.sub(r"(?<!\\)%[^\n]*", _blank, source)
    for escaped in ("\\$", "\\%", "\\&", "\\#", "\\_"):
        text = text.replace(escaped, "  ")
    text = re.sub(r"\\begin\{(" + _MATH_ENVIRONMENTS + r")\}.*?\\end\{\1\}", _blank, text, flags=re.S)
    text = re.sub(r"\\\[.*?\\\]", _blank, text, flags=re.S)
    text = re.sub(r"\$\$.*?\$\$", _blank, text, flags=re.S)
    text = re.sub(r"\\\(.*?\\\)", _blank, text, flags=re.S)
    text = re.sub(r"\$[^$]*\$", _blank, text, flags=re.S)
    text = re.sub(r"\\(?:" + _SILENT + r")\*?(?:\[[^\]\n]*\])?\{[^}\n]*\}", _blank, text)
    text = _ACCENT.sub(lambda m: m.group(1), text)
    text = text.replace("\\ss", "ss")
    return re.sub(r"\\[A-Za-z@]+\*?", _blank, text)


class Source:
    """A TeX file: its prose words by line, the lines that print anything, and its labels."""

    def __init__(self, text: str) -> None:
        self.lines = text.split("\n")
        self.tokens: list[str] = []
        self.token_lines: list[int] = []
        for number, line in enumerate(detex(text).split("\n"), 1):
            for token in words_of(line):
                self.tokens.append(token)
                self.token_lines.append(number)
        # content[n]: does line n put ink on the page?  A line that only opens or closes an
        # environment, or labels it, does not.
        self._content = [0]
        self._stripped: list[str] = []
        for line in self.lines:
            bare = re.sub(r"(?<!\\)%.*", "", line)
            self._stripped.append(bare)
            bare = re.sub(r"\\(?:begin|end|label)\*?(?:\[[^\]]*\])?\{[^}]*\}", "", bare)
            bare = bare.replace("\\[", "").replace("\\]", "")
            self._content.append(self._content[-1] + (1 if re.search(r"[A-Za-z0-9]", bare) else 0))

    def __len__(self) -> int:
        return len(self.lines)

    def first_token_at(self, line: int) -> int:
        """The index of the first word on ``line`` or after it."""
        return bisect.bisect_left(self.token_lines, line)

    def content(self, first: int, last: int) -> int:
        """How many of the lines ``first``..``last`` print anything."""
        first, last = max(1, first), min(len(self.lines), last)
        return self._content[last] - self._content[first - 1] if last >= first else 0

    def label_span(self, label: str) -> tuple[int, int] | None:
        """The lines of the innermost environment that holds ``\\label{label}``.

        A label outside every environment (a section's) is its own line.  None
        when the source has no such label, or has it twice.
        """
        needle = "\\label{" + label + "}"
        hits = [n for n, line in enumerate(self._stripped, 1) if needle in line]
        if len(hits) != 1:
            return None
        at = hits[0]
        stack: list[tuple[str, int]] = []
        for number, line in enumerate(self._stripped, 1):
            for match in re.finditer(r"\\(begin|end)\{([^}]*)\}", line):
                which, name = match.group(1), match.group(2)
                if name == "document":
                    continue
                if which == "begin":
                    stack.append((name, number))
                elif stack and stack[-1][0] == name:
                    _, opened = stack.pop()
                    if opened <= at <= number:
                        return opened, number
            if number == at and not stack:
                return at, at
        return at, at

    def section_at(self, line: int) -> str:
        """The number of the section ``line`` is in, or ``""`` before the first and in an appendix."""
        number, appendix = 0, False
        for bare in self._stripped[:line]:
            if re.match(r"\s*\\appendix\b", bare):
                appendix = True
            elif re.match(r"\s*\\section\s*(?:\[[^\]]*\])?\{", bare):
                number += 1
        return "" if appendix or not number else str(number)


# ---------------------------------------------------------------------------
# the PDF's rows
# ---------------------------------------------------------------------------

@dataclass
class Row:
    """One typeset row of one page: its place in points from the top left, and its words."""
    page: int                 # 1-based
    index: int                # among the page's rows, from 0
    top: float
    bottom: float
    left: float
    right: float
    text: str = ""


@dataclass(frozen=True)
class Word:
    """One comparable word of the PDF: the row it is on, among all rows, and where it starts."""
    token: str
    row: int
    x: float


class Layout:
    """The rows of a PDF's pages in reading order, and the words on them.

    ``pages`` is one ``(width, height, lines)`` per physical page, a line being
    ``(top, bottom, [(x0, x1, text), ...])`` in points from the top left: what
    ``pdftotext -bbox-layout`` gives (:meth:`extract`), or what a test writes.
    Lines that overlap vertically are one row (a displayed formula and its
    number).  A page's folio and a running head are not rows: a first or last
    row that is a page number alone, or whose words recur at that edge of a
    fifth of the pages (three at least), is dropped.
    """

    def __init__(self, pages: list[tuple[float, float, list]]) -> None:
        self.sizes = [(float(w), float(h)) for w, h, _ in pages]
        per_page = [self._rows(lines) for _, _, lines in pages]
        self._drop_furniture(per_page)
        self.rows: list[Row] = []
        self.words: list[Word] = []
        self.page_rows: list[list[int]] = []
        for number, rows in enumerate(per_page, 1):
            ordinals = []
            for index, (top, bottom, words) in enumerate(rows):
                ordinal = len(self.rows)
                ordinals.append(ordinal)
                self.rows.append(Row(number, index, top, bottom, min(w[0] for w in words),
                                     max(w[1] for w in words), " ".join(w[2] for w in words)))
                for x0, _x1, text in words:
                    for token in words_of(text):
                        self.words.append(Word(token, ordinal, x0))
            self.page_rows.append(ordinals)
        lefts = sorted(row.left for row in self.rows)
        rights = sorted(row.right for row in self.rows)
        if lefts:
            self.left = lefts[len(lefts) // 10]
            self.right = rights[(len(rights) * 9) // 10]
        else:
            self.left, self.right = 0.0, (self.sizes[0][0] if self.sizes else 0.0)
        self.mid = (self.left + self.right) / 2

    @staticmethod
    def _rows(lines: list) -> list[tuple[float, float, list]]:
        """A page's lines as rows, top to bottom, each ``(top, bottom, words left to right)``."""
        rows: list[list] = []
        for top, bottom, words in sorted((line for line in lines if line[2]),
                                         key=lambda line: (line[0] + line[1]) / 2):
            if rows:
                last = rows[-1]
                overlap = min(last[1], bottom) - max(last[0], top)
                if overlap > 0.6 * min(last[1] - last[0], bottom - top):
                    last[0], last[1] = min(last[0], top), max(last[1], bottom)
                    last[2] += list(words)
                    continue
            rows.append([top, bottom, list(words)])
        return [(top, bottom, sorted(words)) for top, bottom, words in rows]

    @staticmethod
    def _drop_furniture(per_page: list[list]) -> None:
        """Remove folios and running heads from the first and last rows of the pages, in place."""
        def text(row) -> str:
            return " ".join(w[2] for w in row[2]).strip()

        def key(row) -> str:
            return re.sub(r"[\W\d_]+", " ", text(row)).strip().casefold()

        need = max(3, -(-len(per_page) // 5))
        for edge in (0, -1):
            seen: dict[str, int] = {}
            for rows in per_page:
                if rows and key(rows[edge]):
                    seen[key(rows[edge])] = seen.get(key(rows[edge]), 0) + 1
            for rows in per_page:
                if not rows:
                    continue
                bare = re.sub(r"[\s\-–—.]+", "", text(rows[edge]))
                if _FOLIO.fullmatch(bare) or seen.get(key(rows[edge]), 0) >= need:
                    del rows[edge]

    @classmethod
    def from_bbox(cls, xml: str) -> "Layout":
        """The layout ``pdftotext -bbox-layout`` describes."""
        pages: list[tuple[float, float, list]] = []
        number = r'"(-?[0-9.]+)"'
        for raw in xml.split("\n"):
            page = re.search(r"<page width=" + number + " height=" + number, raw)
            if page:
                pages.append((float(page.group(1)), float(page.group(2)), []))
                continue
            line = re.search(r"<line xMin=" + number + " yMin=" + number + " xMax=" + number
                             + " yMax=" + number, raw)
            if line and pages:
                pages[-1][2].append((float(line.group(2)), float(line.group(4)), []))
                continue
            word = re.search(r"<word xMin=" + number + " yMin=" + number + " xMax=" + number
                             + " yMax=" + number + r">(.*?)</word>", raw)
            if word and pages and pages[-1][2]:
                pages[-1][2][-1][2].append((float(word.group(1)), float(word.group(3)),
                                            html.unescape(word.group(5))))
        return cls(pages)

    @classmethod
    def extract(cls, pdf: Path) -> "Layout":
        """Run ``pdftotext -bbox-layout`` over ``pdf``."""
        if shutil.which(PROGRAM) is None:
            raise PlaceError(f"{PROGRAM} is not on PATH (install poppler)")
        proc = subprocess.run([PROGRAM, "-enc", "UTF-8", "-bbox-layout", str(pdf), "-"],
                              capture_output=True)
        if proc.returncode != 0:
            raise PlaceError(f"{PROGRAM} could not read {pdf}: "
                             f"{proc.stderr.decode('utf-8', 'replace').strip()[:200]}")
        layout = cls.from_bbox(proc.stdout.decode("utf-8", "replace"))
        if not layout.rows:
            raise PlaceError(f"{pdf} has no text layer to match the source's words against")
        return layout

    def rows_of(self, first: int, last: int) -> list[tuple[int, int, int]]:
        """``(page, first row, last row)`` for each page the rows ``first``..``last`` are on."""
        out = []
        start, end = self.rows[first], self.rows[last]
        for page in range(start.page, end.page + 1):
            count = len(self.page_rows[page - 1])
            if count:
                out.append((page, start.index if page == start.page else 0,
                            end.index if page == end.page else count - 1))
        return out


# ---------------------------------------------------------------------------
# a range's rows
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Extent:
    """The rows a line range prints on, among all rows, and how they were found.

    ``how`` is ``words`` when words of the range itself were matched, and
    ``neighbours`` when none was and the range sits between the matched words
    on either side of it.
    """
    first: int
    last: int
    how: str


class Placer:
    """Finds line ranges of one source on one layout, and keeps count of what it matched."""

    def __init__(self, source: Source, layout: Layout) -> None:
        self.source, self.layout = source, layout
        self.words_inside = 0
        self.words_matched = 0
        self._once: dict[tuple, int] = {}
        for at in range(len(layout.words) - NGRAM + 1):
            gram = tuple(w.token for w in layout.words[at:at + NGRAM])
            self._once[gram] = -1 if gram in self._once else at
        self._source_count: dict[tuple, int] = {}
        for at in range(len(source.tokens) - NGRAM + 1):
            gram = tuple(source.tokens[at:at + NGRAM])
            self._source_count[gram] = self._source_count.get(gram, 0) + 1

    @property
    def agreement(self) -> float | None:
        """The share of the placed ranges' words that the PDF prints where they were looked for."""
        return self.words_matched / self.words_inside if self.words_inside else None

    def _window(self, first: int, last: int) -> tuple[int, int, int, int] | None:
        """``(source words from, to, PDF words from, to)`` to align for the lines ``first``..``last``."""
        for widen in (1, 3):
            lo = self.source.first_token_at(first - CONTEXT_LINES * widen)
            hi = self.source.first_token_at(last + CONTEXT_LINES * widen + 1)
            hits = []
            for at in range(lo, hi - NGRAM + 1):
                gram = tuple(self.source.tokens[at:at + NGRAM])
                if self._source_count.get(gram) == 1 and self._once.get(gram, -1) >= 0:
                    hits.append(self._once[gram])
            if not hits:
                continue
            hits.sort()
            median = hits[len(hits) // 2]
            spread = 3 * (hi - lo) + 2 * CONTEXT_WORDS
            near = [h for h in hits if abs(h - median) <= spread]
            return (lo, hi, max(0, near[0] - CONTEXT_WORDS),
                    min(len(self.layout.words), near[-1] + NGRAM + CONTEXT_WORDS))
        return None

    def place(self, first: int, last: int) -> Extent | None:
        """The rows the source lines ``first``..``last`` print on, or None when they cannot be found.

        Three matched words bound a range: ``s`` and ``e``, the first and last
        inside it, ``u``, the last before it, and ``n``, the first after it.
        Where ``u`` and ``s`` are on one row, the range starts on that row when
        ``s`` is in its left half and on the next when not; where ``e`` and
        ``n`` are, it ends there when ``n`` is in the right half and on the row
        before when not.  The range after this one reads the same two words as
        its ``u`` and ``s``, so exactly one of the two keeps the row (a range of
        one row keeps it whatever the other does).  Rows strictly between ``u``
        and ``s``, or ``e`` and ``n``, print no matched word; they are divided
        in the proportion of the source lines with content on each side of the
        range's edge, by one rounding that both ranges compute alike.
        """
        if not (1 <= first <= last <= len(self.source)):
            return None
        self.words_inside += self.source.first_token_at(last + 1) - self.source.first_token_at(first)
        window = self._window(first, last)
        if window is None:
            return None
        lo, hi, pdf_lo, pdf_hi = window
        tokens = self.source.tokens[lo:hi]
        lines = self.source.token_lines[lo:hi]
        pdf = self.layout.words[pdf_lo:pdf_hi]
        matcher = difflib.SequenceMatcher(None, tokens, [w.token for w in pdf], autojunk=False)
        pairs: dict[int, Word] = {}
        for block in matcher.get_matching_blocks():
            if block.size >= MIN_BLOCK or (block.size == 1 and len(tokens[block.a]) >= LONE_WORD):
                for k in range(block.size):
                    pairs[block.a + k] = pdf[block.b + k]
        order = sorted(pairs)
        inside = [i for i in order if first <= lines[i] <= last]
        before = [i for i in order if lines[i] < first]
        after = [i for i in order if lines[i] > last]
        u, u_line = (pairs[before[-1]], lines[before[-1]]) if before else (None, 0)
        n, n_line = (pairs[after[0]], lines[after[0]]) if after else (None, 0)
        content, mid = self.source.content, self.layout.mid
        self.words_matched += len(inside)
        if inside:
            s, s_line = pairs[inside[0]], lines[inside[0]]
            e, e_line = pairs[inside[-1]], lines[inside[-1]]
            start, end = s.row, e.row
            if u is not None and u.row == start:
                if s.x >= mid and start < end:
                    start += 1
            elif u is not None and u.row < start:
                between = start - u.row - 1
                ours = content(first, s_line - 1)
                if between and ours:
                    theirs = content(u_line + 1, first - 1)
                    start = u.row + 1 + int(theirs * between / (ours + theirs) + 0.5)
            if n is not None and n.row == end:
                if n.x < mid and end > start:
                    end -= 1
            elif n is not None and n.row > end:
                between = n.row - end - 1
                ours = content(e_line + 1, last)
                if between and ours:
                    theirs = content(last + 1, n_line - 1)
                    end += int(ours * between / (ours + theirs) + 0.5)
            return Extent(start, max(start, end), "words")
        if u is None or n is None or n.row < u.row:
            return None
        between = n.row - u.row - 1
        if between > 0:
            ahead = content(u_line + 1, first - 1)
            ours = content(first, last)
            total = max(1, ahead + ours + content(last + 1, n_line - 1))
            skip = int(ahead * between / total + 0.5)
            take = max(skip, int((ahead + ours) * between / total + 0.5) - 1)
            return Extent(u.row + 1 + min(skip, between - 1), u.row + 1 + min(take, between - 1),
                          "neighbours")
        row = n.row if (n.x >= mid or n.row == u.row) else u.row
        return Extent(row, row, "neighbours")


# ---------------------------------------------------------------------------
# the rendered page
# ---------------------------------------------------------------------------

class Ink:
    """Which pixel rows of the rendered pages hold ink between the text block's sides.

    The reader is the informalize runtime's own ``PageInk``
    (`templates/informalize/runtime/tools/extract_markers.py`), at the
    resolution and the gray level its ``DEFAULT_BOXES`` give, so a box here and
    a box there are held against the same picture of the page.  Pages are
    rendered as they are asked for.
    """

    def __init__(self, pdf: Path, layout: Layout) -> None:
        if shutil.which(INK_PROGRAM) is None:
            raise PlaceError(f"{INK_PROGRAM} is not on PATH (install poppler)")
        from informalize import runtime as informalize_runtime
        boxes_default = informalize_runtime.fccommon().DEFAULT_BOXES
        self._page_ink = informalize_runtime.extract_markers().PageInk
        self.res_dpi, self.ink_level = int(boxes_default["res_dpi"]), int(boxes_default["ink_level"])
        self.pdf, self.layout = pdf, layout
        self.left, self.right = layout.left - 2 * PAD_PT, layout.right + 2 * PAD_PT
        self._pages: dict[int, object] = {}

    def _page(self, page: int):
        if page not in self._pages:
            with tempfile.TemporaryDirectory(prefix="cf-texplace-") as tmp:
                stem = Path(tmp) / "page"
                subprocess.run([INK_PROGRAM, "-gray", "-r", str(self.res_dpi), "-f", str(page),
                                "-l", str(page), "-singlefile", str(self.pdf), str(stem)],
                               check=True, capture_output=True)
                self._pages[page] = self._page_ink(stem.with_suffix(".pgm").read_bytes(),
                                                   self.layout.sizes[page - 1], self.ink_level)
        return self._pages[page]

    def weight(self, page: int, y: float) -> int:
        """Ink pixels in the row at ``y`` points from the top, 0 when the text block has none there."""
        rendered = self._page(page)
        cell = rendered.rows[rendered.row(y)]
        if cell is None or cell[1] / rendered.sx < self.left or cell[0] / rendered.sx > self.right:
            return 0
        return cell[2]

    def step(self, page: int) -> float:
        """One pixel row, in points."""
        return 1.0 / self._page(page).sy


# ---------------------------------------------------------------------------
# boxes
# ---------------------------------------------------------------------------

@dataclass
class Box:
    """One box on one page: the rows it runs over and its place as fractions of the page."""
    page: int
    first: int
    last: int
    rect: list[float] = field(default_factory=list)       # [x, y, width, height]
    on_ink: int = 0                                        # how many of its two edges cross ink

    @property
    def key(self) -> str:
        """The box's name: the page and the rows, which two ranges with one extent share."""
        return f"p{self.page:03d}-r{self.first:03d}-{self.last:03d}"


def _settle(ink, page: int, y: float) -> tuple[float, bool]:
    """``y`` moved to the nearest blank pixel row within reach, and whether it still crosses ink.

    Where no row within reach is blank, the edge goes to the one with the least ink.
    """
    if ink is None or not ink.weight(page, y):
        return y, False
    step = ink.step(page)
    best, least = y, ink.weight(page, y)
    k = 1
    while k * step <= REACH_PT:
        for candidate in (y - k * step, y + k * step):
            weight = ink.weight(page, candidate)
            if not weight:
                return candidate, False
            if weight < least:
                best, least = candidate, weight
        k += 1
    return best, True


def boxes(layout: Layout, extents: dict[str, list[Extent]], ink=None) -> tuple[dict[str, list[Box]], dict]:
    """A box per page for every extent, by the name its ranges were given, and what to report.

    An edge between two rows is halfway between them, and at a page's first or
    last row :data:`LEAD_PT` beyond it; with ``ink`` (an :class:`Ink`, or
    anything with its ``weight`` and ``step``) each is then moved onto a blank
    pixel row.  Extents with the same rows on a page are one :class:`Box`,
    which every one of their names lists.  A box that encloses another stands
    :data:`NEST_PT` further out at the sides for each level, and is moved off
    the inner one's top and bottom where the page is blank there.

    The report counts the boxes, names each edge that still crosses ink
    (``edges_on_ink``), says whether the edges were held against a rendered
    page at all (``edges_checked``), and names each pair of boxes on a page
    that overlap without one enclosing the other (``crossing``), which is what
    two ranges that share source lines give.
    """
    wanted: dict[tuple[int, int, int], Box] = {}
    parts: dict[str, list[tuple[int, int, int]]] = {}
    for name, found in extents.items():
        parts[name] = []
        for extent in found:
            for part in layout.rows_of(extent.first, extent.last):
                wanted.setdefault(part, Box(*part))
                if part not in parts[name]:
                    parts[name].append(part)
    by_page: dict[int, list[tuple[int, int]]] = {}
    for page, first, last in wanted:
        by_page.setdefault(page, []).append((first, last))
    depth: dict[tuple[int, int, int], int] = {}
    crossing = []
    for page, spans in by_page.items():
        spans.sort(key=lambda span: (span[1] - span[0], span))
        for span in spans:
            inner = [depth[(page, *other)] for other in spans
                     if other != span and (page, *other) in depth
                     and span[0] <= other[0] and other[1] <= span[1]]
            depth[(page, *span)] = 1 + max(inner) if inner else 0
        ordered = sorted(spans)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                nested = (a[0] <= b[0] and b[1] <= a[1]) or (b[0] <= a[0] and a[1] <= b[1])
                if a[1] >= b[0] and not nested:
                    crossing.append(f"{Box(page, *a).key} and {Box(page, *b).key}")
    on_ink = []
    for (page, first, last), box in sorted(wanted.items()):
        rows = [layout.rows[g] for g in layout.page_rows[page - 1]]
        width, height = layout.sizes[page - 1]
        top = rows[first].top - LEAD_PT if first == 0 else (rows[first - 1].bottom + rows[first].top) / 2
        bottom = (rows[last].bottom + LEAD_PT if last == len(rows) - 1
                  else (rows[last].bottom + rows[last + 1].top) / 2)
        top, top_inked = _settle(ink, page, top)
        bottom, bottom_inked = _settle(ink, page, bottom)
        level = min(depth[(page, first, last)], MAX_NEST)
        if ink is not None:
            for _ in range(level):
                if not top_inked and not ink.weight(page, top - NUDGE_PT):
                    top -= NUDGE_PT
                if not bottom_inked and not ink.weight(page, bottom + NUDGE_PT):
                    bottom += NUDGE_PT
        top, bottom = max(0.0, top), min(height, bottom)
        x0 = max(0.0, layout.left - PAD_PT - NEST_PT * level)
        x1 = min(width, layout.right + PAD_PT + NEST_PT * level)
        box.rect = [round(x0 / width, 6), round(top / height, 6),
                    round((x1 - x0) / width, 6), round((bottom - top) / height, 6)]
        box.on_ink = int(top_inked) + int(bottom_inked)
        if box.on_ink:
            on_ink.append(box.key)
    report = {"boxes": len(wanted), "edges_checked": ink is not None, "edges_on_ink": on_ink,
              "crossing": crossing}
    return {name: [wanted[part] for part in found] for name, found in parts.items()}, report
