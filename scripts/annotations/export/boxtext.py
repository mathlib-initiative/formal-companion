"""What the canonical PDF prints inside a `markers.json` box, for the titles of section 10.

New code, for section 10 of `docs/proposals/informalize.md`: a notion's title is the text
the page shows, not the LaTeX its `\\fcnotion` was written with -- a reader of a card sees
``B(x, rho)``, as the page does, and never ``$\\ball{x}{\\rho}$``.  The crop itself is the
runtime's: `check_items.read_box`, which rule 9 of the box check already runs over every
box (`templates/informalize/runtime/tools/`), so there is one box reader and the export
reads the boxes exactly as the check that passed them did.  That reader holds pdftotext's
crop against the page's own glyphs (`pdftohtml -xml`), so what it gives is what a reader
of the page sees: ``ℕ`` and not ``N``, ``Π₁(f)`` and not ``Π1 (f )``, ``f ∘ γ`` and not
``f ◦γ``. Scripts without Unicode characters use braced notation such as
``ι_{S}``, which the viewer renders with HTML sub/sup elements. Where the glyphs
cannot be had it gives pdftotext's text, and says so.

This module joins a box group's crops in the order the markers list them, which is reading
order, and keeps every character that reader gave: only the white space and the
punctuation of :func:`printed` are touched.  :func:`strip_tex` is the fallback for a title
no crop can be had for, and :func:`has_tex` says which titles need one at all.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

#: poppler's two programs a crop cannot be had without: pdftotext crops, pdfinfo gives the
#: page sizes.  `pdftohtml`, which reads the glyphs, is not one of them: without it the
#: reader gives pdftotext's own text and the title is reported as read from a flattened
#: page, rather than not read at all
PROGRAMS = ("pdftotext", "pdfinfo")
#: what TeX prints for the ligatures a title is written with: a dash is typed as two or
#: three hyphens and a quotation mark as a pair of them, and the page prints one character
LIGATURES = (("---", "—"), ("--", "–"), ("``", "“"), ("''", "”"))
#: TeX markup in a title: a control word or symbol, a math shift, a group, an accent, or a
#: ligature -- `Seifert--van Kampen` is as much the source of what the page prints as
#: `$\ball{x}{\rho}$` is, and a title holding one is read from the page like any other
_MARKUP = re.compile(r"\\[A-Za-z@]+|\\.|[${}~^_&%#]|"
                     + "|".join(re.escape(tex) for tex, _ in LIGATURES))


def has_tex(title: str | None) -> bool:
    """Does this title hold TeX markup, so that the page prints something else?"""
    return bool(_MARKUP.search(title or ""))


def ligatures(text: str) -> str:
    """Plain text as TeX typesets it: each ligature the character the page prints for it.

    The whole of what TeX does to a line that holds no markup, so this is also what the
    text's own title prints as (`informalize.config.printed_title`), where the title is
    plain text `main.tex` escapes rather than LaTeX a drafter wrote.
    """
    for tex, char in LIGATURES:
        text = text.replace(tex, char)
    return text


def strip_tex(title: str) -> str:
    """A title with its TeX markup removed, and the title itself when nothing is left.

    A ligature is replaced by the character it prints rather than dropped: it is the one
    piece of markup this fallback can read as well as the page does.
    """
    plain = re.sub(r"\s+", " ", _MARKUP.sub(" ", ligatures(title))).strip()
    return plain or title.strip()


def printed(text: str) -> str:
    """Poppler's text as a title: the line breaks closed up, white space collapsed, and
    every other character as poppler gave it.

    A line that ends in a hyphen keeps it and takes the next line against it
    (`path-\\nconnected` is `path-connected`): nothing here can tell a hyphen the page
    prints from one TeX broke a word at, and the one the page prints is the one a reader
    sees.  A box is padded by `boxes.pad_bp` on each side, so the crop of a notion that
    a sentence goes on past catches its comma or its full stop; a title ends at the last
    character of the notion itself.
    """
    joined = re.sub(r"\s+", " ", re.sub(r"-\s*\n\s*", "-", text)).strip()
    return re.sub(r"[\s.,;:]+\Z", "", joined) or joined


def parenthesis_after(text: str, head: str) -> str | None:
    """The balanced parenthesis the page prints after ``head``, as an item's head prints
    its title: ``Theorem 1.2 (the title).``  None when there is none."""
    start = text.find(head)
    if start < 0:
        return None
    rest = text[start + len(head):].lstrip(" ")
    if not rest.startswith("("):
        return None
    depth = 0
    for n, char in enumerate(rest):
        depth += (char == "(") - (char == ")")
        if depth == 0:
            return rest[1:n].strip() or None
    return None


def reader():
    """The one box reader: the runtime's `check_items.read_box`, which rule 9 reads a box
    with.

    From THIS checkout's runtime, never an output repository's copy of it, as the rest
    of the convention reads it (`informalize.runtime`).
    """
    from informalize import runtime as informalize_runtime
    return informalize_runtime.check_items().read_box


@dataclass
class BoxGroup:
    """What one entry's boxes print, and what the page gave up on the way."""
    text: str
    #: the script runs the reader left as poppler read them, because Unicode has no
    #: character for one of theirs (``ιS``, ``γ[r,s]``)
    kept: list[str] = field(default_factory=list)
    #: were the page's own glyphs read for every box?  Where they were not, the text is
    #: pdftotext's, which flattens a double-struck letter and a subscript
    glyphs: bool = True


class BoxText:
    """The printed text of the box groups of one PDF, or nothing without poppler."""

    def __init__(self, pdf: Path, dpi: int) -> None:
        self.pdf = Path(pdf)
        self.dpi = int(dpi)
        #: the programs that are not on PATH; with any of them missing nothing is cropped
        self.missing = [name for name in PROGRAMS if shutil.which(name) is None]
        self._sizes: list[tuple[float, float]] | None = None
        self._read = None

    @property
    def available(self) -> bool:
        return not self.missing

    @property
    def why(self) -> str:
        """Why no box of this PDF can be read, for the export's report."""
        return f"poppler is not on PATH: {', '.join(self.missing)}"

    def of(self, boxes: list) -> BoxGroup:
        """The text the PDF prints inside ``boxes``, joined in the order they stand, with
        what the page gave up on the way.

        The text is empty when poppler is absent, when the boxes are, or when the crop
        holds no text
        (an `\\fcnotion` around a figure, say): the caller falls back and reports it.  A
        box this PDF has no page for is read as nothing; the export refuses it by itself,
        with the page count it was measured against.
        """
        if not self.available or not boxes:
            return BoxGroup("")
        # the runtime is loaded (and the page sizes read) only where a box is read
        if self._read is None:
            from informalize import runtime as informalize_runtime
            self._read = reader()
            self._sizes = informalize_runtime.fccommon().pdf_pages(self.pdf)
        reads = [self._read(self.pdf, list(box), self._sizes[int(box[0]) - 1], self.dpi,
                            script_markup=True)
                 for box in boxes
                 if isinstance(box, list) and len(box) == 5 and isinstance(box[0], int)
                 and 1 <= box[0] <= len(self._sizes)]
        return BoxGroup(printed("\n".join(read.text for read in reads)),
                        kept=[run for read in reads for run in read.kept],
                        glyphs=all(read.glyphs for read in reads))
