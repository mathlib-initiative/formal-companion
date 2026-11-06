"""What the canonical PDF prints inside a `markers.json` box, for the titles of section 10.

New code, for section 10 of `docs/proposals/informalize.md`: a notion's title is the text
the page shows, not the LaTeX its `\\fcnotion` was written with -- a reader of a card sees
``B(x, rho)``, as the page does, and never ``$\\ball{x}{\\rho}$``.  The crop itself is the
runtime's: `check_items.crop`, which rule 9 of the box check already runs over every box
(`templates/informalize/runtime/tools/`), so there is one cropper and the export reads the
boxes exactly as the check that passed them did.

This module joins a box group's crops in the order the markers list them, which is reading
order, and keeps every character poppler gave (Greek, subscripts, operators): only the
white space and the punctuation of :func:`printed` are touched.  :func:`strip_tex` is the
fallback for a title no crop can be had for, and :func:`has_tex` says which titles need
one at all.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

#: poppler's two programs: the crop needs pdftotext, the page sizes pdfinfo
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


def strip_tex(title: str) -> str:
    """A title with its TeX markup removed, and the title itself when nothing is left.

    A ligature is replaced by the character it prints rather than dropped: it is the one
    piece of markup this fallback can read as well as the page does.
    """
    plain = title
    for tex, char in LIGATURES:
        plain = plain.replace(tex, char)
    plain = re.sub(r"\s+", " ", _MARKUP.sub(" ", plain)).strip()
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


def cropper():
    """The one cropper: the runtime's `check_items.crop`, which rule 9 reads a box with.

    From THIS checkout's runtime, never an output repository's copy of it, as the rest
    of the convention reads it (`informalize.runtime`).
    """
    from informalize import runtime as informalize_runtime
    return informalize_runtime.check_items().crop


class BoxText:
    """The printed text of the box groups of one PDF, or nothing without poppler."""

    def __init__(self, pdf: Path, dpi: int) -> None:
        self.pdf = Path(pdf)
        self.dpi = int(dpi)
        #: the programs that are not on PATH; with any of them missing nothing is cropped
        self.missing = [name for name in PROGRAMS if shutil.which(name) is None]
        self._sizes: list[tuple[float, float]] | None = None
        self._crop = None

    @property
    def available(self) -> bool:
        return not self.missing

    @property
    def why(self) -> str:
        """Why no box of this PDF can be read, for the export's report."""
        return f"poppler is not on PATH: {', '.join(self.missing)}"

    def of(self, boxes: list) -> str:
        """The text the PDF prints inside ``boxes``, joined in the order they stand.

        Empty when poppler is absent, when the boxes are, or when the crop holds no text
        (an `\\fcnotion` around a figure, say): the caller falls back and reports it.  A
        box this PDF has no page for is read as nothing; the export refuses it by itself,
        with the page count it was measured against.
        """
        if not self.available or not boxes:
            return ""
        # the runtime is loaded (and the page sizes read) only where a box is read
        if self._crop is None:
            from informalize import runtime as informalize_runtime
            self._crop = cropper()
            self._sizes = informalize_runtime.fccommon().pdf_pages(self.pdf)
        return printed("\n".join(
            self._crop(self.pdf, list(box), self._sizes[int(box[0]) - 1], self.dpi)
            for box in boxes
            if isinstance(box, list) and len(box) == 5 and isinstance(box[0], int)
            and 1 <= box[0] <= len(self._sizes)))
