"""What locates an item in a source: a printed page, or a heading path.

New code (docs/design/multi-source-model.md S5).  The source model was written
for a typeset PDF all the way down -- ``pdftotext``, running heads, ``printed =
pdf_page - offset``, ``In-text p.<n>`` -- and a manuscript that has no printed
page (an arXiv ``.tex``, lecture notes in Markdown, an HTML chapter, a
``pdftotext`` dump kept as a file) had no way in at all: :func:`prepare_source.
prepare` ran ``pdftotext`` unconditionally and failed on the bytes.

This module holds the small amount of knowledge that makes the second kind of
source possible, and the refusal that keeps it honest:

* :func:`source_format`      PDF or text, from the file's own suffix
* :func:`detect_pagination`  ``formfeed`` when the text carries page breaks,
                             ``none`` when it does not
* :func:`heading_paths`      the heading anchors a text markup exposes, so an
                             item in an unpaginated source is located by the
                             section it is in
* :func:`require_pagination` the CfError every page-dependent feature owes the
                             user instead of a crash or a plausible number

The rule is the repo's own (CLAUDE.md S4): a page-dependent feature run against
a source that has no pages REFUSES and names what settles it.  An offset of 0
that nothing supports would be exactly the "plausible default worse than no
answer" the offset rule already forbids.
"""

from __future__ import annotations

import os
import re

#: the text carries ``\f`` page breaks, so it has pages to bind quotes to
PAGINATION_FORMFEED = "formfeed"
#: the text has no pages at all: items are located by section and line
PAGINATION_NONE = "none"

#: ``items.json``/``meta.json`` ``locator``: what an item's position means
LOCATOR_PAGE = "page"
LOCATOR_SECTION = "section"

#: manuscript suffixes `cf sources add` accepts beside a PDF
TEXT_SUFFIXES: tuple[str, ...] = (".txt", ".md", ".markdown", ".tex", ".html", ".htm", ".rst")
PDF_SUFFIXES: tuple[str, ...] = (".pdf",)

FORMAT_PDF = "pdf"
FORMAT_TEXT = "text"

_MD_HEADING = re.compile(r"^[ \t]*(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_TEX_HEADING = re.compile(
    r"^[ \t]*\\(chapter|section|subsection|subsubsection)\*?\{([^}\n]{1,160})\}", re.MULTILINE)
_HTML_HEADING = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def source_format(path: str) -> str:
    """``"pdf"`` or ``"text"`` from the suffix; raises on anything else.

    The suffix decides, not a sniff of the bytes: a source is named by a human
    on the command line, and guessing wrong here would mean preparing a
    different document than the one they meant.
    """
    suffix = os.path.splitext(str(path))[1].lower()
    if suffix in PDF_SUFFIXES:
        return FORMAT_PDF
    if suffix in TEXT_SUFFIXES:
        return FORMAT_TEXT
    raise ValueError(
        "%s: not a source this skill can read (%s)"
        % (path, ", ".join(PDF_SUFFIXES + TEXT_SUFFIXES)))


def detect_pagination(text: str) -> str:
    """``formfeed`` when the text carries ``\\f`` page breaks, else ``none``.

    A ``pdftotext`` dump kept as a ``.txt`` still has its pages, and it is worth
    the check: with page breaks the offset inference, the quote gate and every
    page anchor work exactly as they do on the PDF it came from.
    """
    return PAGINATION_FORMFEED if "\f" in (text or "") else PAGINATION_NONE


def heading_paths(text: str, fmt_suffix: str) -> list[tuple[int, str]]:
    """``[(offset in text, "Chapter 3 > 3.2 Vector spaces"), ...]``, in order.

    The heading path is what replaces a printed page for an unpaginated source:
    it is stable under re-flowing, a reader can find it, and it is the only
    locator an HTML or TeX manuscript actually has.  A plain ``.txt`` exposes no
    markup, so it returns nothing and its items keep their line number.
    """
    suffix = (fmt_suffix or "").lower()
    if suffix in (".md", ".markdown"):
        found = [(m.start(), len(m.group(1)), m.group(2).strip())
                 for m in _MD_HEADING.finditer(text or "")]
    elif suffix == ".tex":
        levels = {"chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4}
        found = [(m.start(), levels[m.group(1)], m.group(2).strip())
                 for m in _TEX_HEADING.finditer(text or "")]
    elif suffix in (".html", ".htm"):
        found = [(m.start(), int(m.group(1)), _TAGS.sub("", m.group(2)).strip())
                 for m in _HTML_HEADING.finditer(text or "")]
    else:
        return []
    out: list[tuple[int, str]] = []
    stack: list[tuple[int, str]] = []
    for pos, level, title in found:
        if not title:
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        out.append((pos, " > ".join(t for _, t in stack)))
    return out


def locate(anchors: list[tuple[int, str]], pos: int) -> str:
    """The innermost heading path at or before ``pos`` (``""`` when none)."""
    best = ""
    for start, path in anchors:
        if start > pos:
            break
        best = path
    return best


def is_paginated(meta) -> bool:
    """Does this prepared source have pages? (``meta.json`` or a pagination str)."""
    if isinstance(meta, dict):
        value = meta.get("pagination") or PAGINATION_FORMFEED
    else:
        value = str(meta or PAGINATION_FORMFEED)
    return value != PAGINATION_NONE


def require_pagination(meta, feature: str, *, source_id: str = "", settle: str = "") -> None:
    """Refuse ``feature`` on an unpaginated source, naming what settles it.

    Every page-dependent stage owes the user this instead of a crash or a
    silently plausible page: an unpaginated source's items carry no
    ``pdf_page``, so an offset, a page image, a page bind or a printed-page
    anchor has nothing to be computed from.
    """
    if is_paginated(meta):
        return
    who = ("%s: " % source_id) if source_id else ""
    raise _cf_error(
        "%s%s is not applicable for an unpaginated source (pagination: none)" % (who, feature),
        settle or ("this source is located by section and line; prepare the paginated edition "
                   "instead: cf sources add <pdf> --for <companion>"))


def _cf_error(message: str, hint: str):
    """``cflib.errors.CfError`` when cflib is importable, else a ValueError.

    The sources/ stage runs on a bare host without cflib (see
    ``sources/compat.py``), and a refusal must still be a refusal there.
    """
    try:
        from cflib.errors import CfError  # noqa: PLC0415 - optional dependency by design
    except Exception:  # pragma: no cover - stand-alone use without cflib
        return ValueError("%s -- %s" % (message, hint))
    return CfError(message, hint=hint)
