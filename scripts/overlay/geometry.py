#!/usr/bin/env python3
"""Item regions from a PDF text layer, as annotation records.

Generalised from the per-companion geometry generator the overlay contract
names in section 7 (`docs/overlay.md`, seeded as the overlay template's
SPEC.md): that script was written for one section of one book, with the kind
list, the numbering depth, the section prefix, the text column and the
paragraph indent as constants.  Here every one is an argument or a
measurement, and the
output is the annotation format's own `items`, `locations` and `provenance`
(docs/annotations.md sections 5.2, 5.6, 5.7 and 6.1-6.4) instead of a private
batch file.

What the original did, which is kept:

* an item begins at a line whose text opens with a numbered heading of one of
  the source's kinds, followed by a full stop or a parenthesised title;
* it ends at the line before the next numbered heading, the next section
  heading, the next paragraph set off by a vertical gap and a first-line indent
  that is not a sub-part marker, or -- for a kind given in ``--proof-kinds`` --
  the line beginning ``Proof.``, which is exact and is not subject to the
  paragraph rule;
* an item that runs off the page continues in a second region from the top of
  the next page's text, under the same end rules;
* a region is never cut off: it extends to the widest line inside it (one
  heading or display set past the column's right margin truncated a tenth of
  the regions the original wrote with a fixed right edge), capped at the page's
  own margin;
* the gap measured before a paragraph break is the gap from the nearest earlier
  fragment lying above the line, not from the previous fragment in sort order:
  ``pdftotext`` splits a line at inline mathematics, and a taller mathematics
  fragment of the *same* visual line can sort first;
* crops are evidence for the visual sign-off, are derived from a copyrighted
  PDF, and are written to a local directory only -- never committed, and only
  when ``pdftoppm`` is present.

What is new: the kinds, the numbering, the section prefix and the proof kinds
are arguments (the kinds default to the source's registry record); headings go
through ``cflib.items``' grammar and its reject list rather than a private
regex; ids come from ``formal_companion_annotations.ids`` where the format
defines them; the text column, the body band (a running head and a folio are
measured away, not assumed) and the paragraph indent are read off each page;
and the sign-off method is the one the caller names -- it is never
``manual_visual`` unless a person says so.

Run it by path::

    python3 scripts/overlay/geometry.py --pdf NOTES.pdf --first 7 --last 9 \\
        --kinds Definition,Theorem,Exercise --section 2.1 \\
        --source-id notes-pdf --verifier-id agent:reviewer-1 \\
        --method agent_visual --out fragment.json

With ``--bbox-xhtml`` a saved ``pdftotext -bbox-layout`` document is read
instead of running ``pdftotext``.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from formal_companion_annotations import ids                        # noqa: E402
from formal_companion_annotations import validate as V              # noqa: E402
from formal_companion_annotations import vocab                      # noqa: E402
from annotations.export.common import after_number, render_sub      # noqa: E402
from cflib import __version__                                       # noqa: E402
from cflib import items as cf_items                                 # noqa: E402
from cflib.errors import CfError                                    # noqa: E402
from cflib.jsonio import atomic_write                               # noqa: E402

#: The two sign-off methods a caller may name.  There is no default: a region
#: a person never looked at may not claim that a person did.
SIGNOFF_METHODS = ("agent_visual", "manual_visual")
#: Kinds whose statement ends at ``Proof.`` unless the caller says otherwise.
DEFAULT_PROOF_KINDS: tuple[str, ...] = ("Theorem", "Lemma", "Proposition", "Corollary")
#: The page box the normalised coordinates are fractions of.
DEFAULT_PAGE_BOX = "CropBox"
#: The id of the one provenance record a fragment carries.
PROVENANCE_ID = "prov-overlay-geometry"

#: Margin around a region, in page heights.
GAP = 0.004
#: A segment shorter than this is a degenerate continuation and is dropped.
MIN_HEIGHT = 0.012
#: Margin past the widest line of a region, in page widths.
XPAD = 0.004
#: Never past the page's own margin.
X_MAX = 0.96
#: The vertical gap, in page heights, that may open a new paragraph.
GAP_MIN = 0.008
#: How far a paragraph's first line may sit from the column's own edge, in page
#: widths: more than a hair (an item's own continuation lines are flush) and no
#: more than a first-line indent (a centred display is not a new paragraph).
INDENT_MIN = 0.01
INDENT_MAX = 0.06
#: A line separated from the rest of the page by more than this many typical
#: line pitches is a running head or a folio, not body text.
HEADER_GAP_FACTOR = 2.0
#: Below this many lines a page's pitch cannot be measured, so nothing is trimmed.
MIN_LINES_FOR_BAND = 5
#: Default resolution of an evidence crop.
RENDER_DPI = 100

_EPS = 1e-6
_SUBITEM = re.compile(r"^\(?([ivx]+|[a-z]|\d+)\)")
_PROOF = re.compile(r"^Proof\.")
#: A numbered heading with no kind word: the source's own section heading.
_SECTION = re.compile(r"^\d+(?:\.\d+)*\.\s+(?![a-z])\S")
_NUMBER = r"\d+(?:\.\d+)*"

_PAGE_EL = re.compile(r"<page\b([^>]*)>(.*?)</page>", re.S)
_LINE_EL = re.compile(r"<line\b([^>]*)>(.*?)</line>", re.S)
_WORD_EL = re.compile(r"<word\b[^>]*>(.*?)</word>", re.S)
_ATTR = re.compile(r'([A-Za-z_:][\w:.-]*)\s*=\s*"([^"]*)"')
_TAG = re.compile(r"<[^>]*>")


# ---------------------------------------------------------------------------
# the text layer
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Line:
    """One ``pdftotext -bbox-layout`` line box, as fractions of the page box."""

    y0: float
    y1: float
    x0: float
    x1: float
    text: str

    def sort_key(self) -> tuple:
        return (self.y0, self.y1, self.x0, self.x1, self.text)


@dataclass(frozen=True)
class Page:
    """One page of the layout: its box in points and its line boxes, in reading order."""

    width_pt: float
    height_pt: float
    lines: tuple[Line, ...]


def _attrs(text: str) -> dict[str, str]:
    return {k: v for k, v in _ATTR.findall(text)}


def _number_attr(attrs: Mapping[str, str], name: str, where: str) -> float:
    raw = attrs.get(name)
    try:
        return float(raw)                                   # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise CfError(f"{where} has no numeric {name}",
                      hint="the layout must be poppler pdftotext -bbox-layout output") from None


def _line_text(body: str) -> str:
    """The line's text: its words joined by one space (its characters when it has none)."""
    words = [html.unescape(w).strip() for w in _WORD_EL.findall(body)]
    words = [w for w in words if w]
    if not words:
        words = [html.unescape(_TAG.sub(" ", body)).strip()]
    return re.sub(r"\s+", " ", " ".join(words)).strip()


def parse_bbox_layout(xhtml: str) -> list[Page]:
    """Every page of a ``pdftotext -bbox-layout`` document, in document order.

    Coordinates are divided by the page box, so a caller never sees points; the
    page box itself is kept for the crops and for the RFC 8118 fallback
    fragment, which are in points.
    """
    pages: list[Page] = []
    for index, (attrs_text, body) in enumerate(_PAGE_EL.findall(xhtml), start=1):
        attrs = _attrs(attrs_text)
        where = f"page {index} of the layout"
        width = _number_attr(attrs, "width", where)
        height = _number_attr(attrs, "height", where)
        if width <= 0 or height <= 0:
            raise CfError(f"{where} is {width} x {height} points",
                          hint="a page box has a positive width and height")
        lines = []
        for line_attrs, line_body in _LINE_EL.findall(body):
            box = _attrs(line_attrs)
            text = _line_text(line_body)
            if not text:
                continue
            lines.append(Line(
                y0=_number_attr(box, "yMin", where) / height,
                y1=_number_attr(box, "yMax", where) / height,
                x0=_number_attr(box, "xMin", where) / width,
                x1=_number_attr(box, "xMax", where) / width,
                text=text))
        pages.append(Page(width, height, tuple(sorted(lines, key=Line.sort_key))))
    if not pages:
        raise CfError("the layout holds no page",
                      hint="pass a pdftotext -bbox-layout document, not plain text")
    return pages


def number_pages(pages: Sequence[Page], first: int, last: int) -> dict[int, Page]:
    """``{physical page: Page}`` for the requested range.

    A layout of exactly ``last - first + 1`` pages is the extract ``pdftotext -f
    first -l last`` writes, so its first page IS physical page ``first``; a
    longer one is the whole document, counted from physical page 1 and sliced.
    Anything else is refused rather than guessed at, because an off-by-one here
    puts every region on the wrong page.
    """
    if first < 1:
        raise CfError(f"--first {first} is not a physical page number (they start at 1)")
    if last < first:
        raise CfError(f"--last {last} is before --first {first}")
    want = last - first + 1
    if len(pages) == want:
        chosen = list(pages)
    elif len(pages) >= last:
        chosen = list(pages[first - 1:last])
    else:
        raise CfError(f"the layout holds {len(pages)} page(s); pages {first}-{last} need "
                      f"{want} (an extract) or at least {last} (the whole document)",
                      hint="extract the same range you pass to --first and --last")
    return {first + i: page for i, page in enumerate(chosen)}


def body_lines(page: Page) -> tuple[Line, ...]:
    """The page's body text: its lines less a running head and a folio.

    Both are measured, never assumed: a line at the top or the bottom that is
    separated from the rest by more than :data:`HEADER_GAP_FACTOR` typical line
    pitches is furniture.  A page whose pitch cannot be measured keeps every
    line, which is the safe direction -- a region may start a little high, where
    dropping a real first line would lose text.
    """
    lines = list(page.lines)
    if len(lines) < MIN_LINES_FOR_BAND:
        return tuple(lines)
    for _pass in range(2):
        gaps = [lines[i + 1].y0 - lines[i].y0 for i in range(len(lines) - 1)]
        if len(gaps) < 3:
            break
        threshold = HEADER_GAP_FACTOR * statistics.median(gaps)
        cut = False
        if gaps[-1] > threshold and len(lines) > 3:
            lines = lines[:-1]
            cut = True
        if gaps[0] > threshold and len(lines) > 3:
            lines = lines[1:]
            cut = True
        if not cut:
            break
    return tuple(lines)


def column(lines: Sequence[Line]) -> tuple[float, float, float, float]:
    """``(top, bottom, left, right)`` of a page's text column, in page fractions."""
    if not lines:
        raise CfError("a page of the layout holds no text line",
                      hint="the pages passed to --first/--last must carry a text layer")
    return (min(ln.y0 for ln in lines), max(ln.y1 for ln in lines),
            min(ln.x0 for ln in lines), max(ln.x1 for ln in lines))


# ---------------------------------------------------------------------------
# headings, marks and segments
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Heading:
    """A numbered item heading found on one page."""

    page: int
    y0: float
    kind: str
    number: str
    sub: str
    title: str
    label: str

    def sort_key(self) -> tuple:
        return (self.page, self.y0, self.kind, self.number, self.sub)


def heading_pattern(kinds: Sequence[str]) -> "re.Pattern[str]":
    """Where a heading of this source's kinds may open a line.

    It only LOCATES a candidate; ``cflib.items`` decides, so a line this
    pattern finds and the grammar refuses is a reject and never an item.
    """
    if not kinds:
        raise CfError("no item kinds to look for",
                      hint="pass --kinds, or name a --source-id the registry knows")
    alternatives = "|".join(re.escape(k) for k in kinds)
    return re.compile(r"^(" + alternatives + r")\s+(" + _NUMBER + r")(\.|\s*\()")


def read_headings(pages: Mapping[int, Sequence[Line]], kinds: Sequence[str], *,
                  numbering=None, rejects: "list[cf_items.Reject] | None" = None
                  ) -> tuple[list[Heading], list[tuple[int, float, str]]]:
    """Every item heading, and every mark that can end an item.

    A mark is an item heading, a section heading or a ``Proof.`` line.  Every
    accepted heading is a mark even when ``--section`` later keeps it out of the
    fragment: the next section's first item still ends this section's last one.
    """
    pattern = heading_pattern(kinds)
    headings: list[Heading] = []
    marks: list[tuple[int, float, str]] = []
    for page in sorted(pages):
        for line in pages[page]:
            text = line.text
            if pattern.match(text):
                item, reject = cf_items.try_parse(text, kinds, numbering=numbering)
                if item is None or not item.numbered:
                    if reject is not None and rejects is not None:
                        rejects.append(reject)
                    continue
                number = ".".join(str(n) for n in item.number_levels)
                sub, _rest, refused = render_sub(after_number(text, number))
                if refused:
                    if rejects is not None:
                        rejects.append(cf_items.Reject(
                            text[:80], "sub_part_not_carried", {"sub": refused}))
                    sub = ""
                label = f"{item.kind} {number}"
                if sub:
                    label += item.sub
                if item.title:
                    label += f" ({item.title})"
                headings.append(Heading(page, line.y0, item.kind, number, sub,
                                        item.title, label))
                marks.append((page, line.y0, "item"))
            elif _SECTION.match(text):
                marks.append((page, line.y0, "sect"))
            elif _PROOF.match(text):
                marks.append((page, line.y0, "proof"))
    headings.sort(key=Heading.sort_key)
    marks.sort()
    return headings, marks


def paragraph_trim(lines: Sequence[Line], top: float, bottom: float,
                   left: float) -> tuple[float, bool]:
    """Where an unnumbered paragraph cuts the span ``(top, bottom)`` short.

    The gap is measured from the nearest earlier fragment lying strictly above
    the line, not from the previous fragment in sort order: ``pdftotext`` splits
    a line at inline mathematics, and a taller mathematics fragment of the
    *same* visual line can sort first, which would hide the paragraph break.
    """
    span = [ln for ln in lines if ln.y0 >= top - _EPS and ln.y1 <= bottom + _EPS]
    seen: list[tuple[float, float]] = []
    for i, line in enumerate(span):
        above = [b for (_a, b) in seen if b <= line.y0 + _EPS]
        offset = abs(line.x0 - left)
        if (above and i > 0 and INDENT_MIN <= offset <= INDENT_MAX
                and (line.y0 - max(above)) >= GAP_MIN and not _SUBITEM.match(line.text)):
            return max(above) + GAP, True
        seen.append((line.y0, line.y1))
    return bottom, False


def segments(heading: Heading, marks: Sequence[tuple[int, float, str]],
             body: Mapping[int, Sequence[Line]], *, proof_kinds: Sequence[str]
             ) -> list[tuple[int, float, float]]:
    """``(page, y0, y1)`` for every region of one item, in reading order."""
    proved = heading.kind in proof_kinds
    after = [m for m in marks
             if (m[0], m[1]) > (heading.page, heading.y0) and (m[2] != "proof" or proved)]
    nxt = after[0] if after else None
    out: list[tuple[int, float, float]] = []
    page, cursor = heading.page, heading.y0 - GAP
    while True:
        lines = body[page]
        top, bottom, left, _right = column(lines)
        ya = max(cursor, top - GAP)
        ends_at_proof = bool(nxt and nxt[0] == page and nxt[2] == "proof")
        if nxt and nxt[0] == page:
            yb, last = nxt[1] - GAP, True
        else:
            yb, last = bottom + GAP, (nxt is None or (page + 1) not in body)
        yb, trimmed = (yb, False) if ends_at_proof else paragraph_trim(lines, ya, yb, left)
        out.append((page, ya, yb))
        if trimmed or last:
            break
        page += 1
        cursor = column(body[page])[0] - GAP
    return [s for s in out if s[2] - s[1] >= MIN_HEIGHT]


def region_rect(lines: Sequence[Line], ya: float, yb: float) -> dict:
    """The region's box: the column's left edge, widened to the widest line inside it.

    Every side is clamped into the page box: the margin this adds around a
    region (:data:`GAP`, :data:`XPAD`) must not take a coordinate outside
    ``[0, 1]``, which the format refuses for normalised units.
    """
    _top, _bottom, left, right = column(lines)
    inside = [ln.x1 for ln in lines if ln.y0 >= ya - _EPS and ln.y1 <= yb + _EPS]
    widest = max(inside) if inside else right
    rect = {"x0": max(0.0, round(left - XPAD, 6)),
            "y0": max(0.0, round(ya, 6)),
            "x1": min(X_MAX, round(widest + XPAD, 6)),
            "y1": min(1.0, round(yb, 6))}
    if rect["x1"] <= rect["x0"] or rect["y1"] <= rect["y0"]:
        raise CfError(f"a region of the layout is empty: {rect}",
                      hint="the page's text column could not be measured")
    return rect


# ---------------------------------------------------------------------------
# the crops that evidence a visual sign-off
# ---------------------------------------------------------------------------

def resolve_tool(name: str) -> str | None:
    """``name`` on PATH, resolved with realpath; None when it is not a regular executable.

    The overlay contract's section 1 rule 6: a symlinked executable is followed
    and the target must be a regular file that is executable, so a name on PATH
    cannot stand for a directory or a dangling link.
    """
    found = shutil.which(name)
    if found is None:
        return None
    real = os.path.realpath(found)
    if not (os.path.isfile(real) and os.access(real, os.X_OK)):
        return None
    return real


class CropRenderer:
    """Rasterises each region to a local PNG and returns its sha256: the sign-off evidence.

    Crops are derived from a copyrighted PDF: they are written to the caller's
    own directory and never anywhere else, and no crop is rendered unless
    ``pdftoppm`` is on PATH.
    """

    def __init__(self, pdf: Path, evidence_dir: Path, *, dpi: int = RENDER_DPI,
                 pdftoppm: str | None = None):
        self.pdf = Path(pdf)
        self.dir = Path(evidence_dir)
        self.dpi = dpi
        self.pdftoppm = pdftoppm or resolve_tool("pdftoppm")

    @property
    def available(self) -> bool:
        return self.pdftoppm is not None

    def render(self, page: int, rect: Mapping[str, float], size_pt: tuple[float, float],
               stem: str) -> str | None:
        """The sha256 of the crop, or None when no crop could be rendered."""
        if self.pdftoppm is None:
            return None
        width_px = size_pt[0] / 72.0 * self.dpi
        height_px = size_pt[1] / 72.0 * self.dpi
        x = int(rect["x0"] * width_px)
        y = int(rect["y0"] * height_px)
        w = max(1, int((rect["x1"] - rect["x0"]) * width_px))
        h = max(1, int((rect["y1"] - rect["y0"]) * height_px))
        self.dir.mkdir(parents=True, exist_ok=True)
        prefix = self.dir / stem
        try:
            subprocess.run([self.pdftoppm, "-r", str(self.dpi), "-f", str(page),
                            "-l", str(page), "-x", str(x), "-y", str(y), "-W", str(w),
                            "-H", str(h), "-png", "-singlefile", str(self.pdf), str(prefix)],
                           check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            raise CfError(f"pdftoppm could not render the crop for page {page}: "
                          f"{(exc.stderr or b'').decode('utf-8', 'replace').strip()[:200]}",
                          hint="--evidence-dir needs the same PDF the regions were "
                               "measured from") from exc
        out = Path(f"{prefix}.png")
        if not out.is_file():
            return None
        return hashlib.sha256(out.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# the fragment
# ---------------------------------------------------------------------------

def _highlight(rect: Mapping[str, float], size_pt: tuple[float, float]) -> str:
    """The RFC 8118 ``highlight=`` fallback, in points: left,right,top,bottom."""
    width, height = size_pt
    return (f"{math.floor(rect['x0'] * width)},{math.ceil(rect['x1'] * width)},"
            f"{math.floor(rect['y0'] * height)},{math.ceil(rect['y1'] * height)}")


def build_fragment(xhtml: str, *, first: int, last: int, source_id: str, kinds: Sequence[str],
                   verifier_id: str, method: str, section: str | None = None,
                   numbering=None, proof_kinds: Sequence[str] = DEFAULT_PROOF_KINDS,
                   page_box: str = DEFAULT_PAGE_BOX,
                   evidence: "CropRenderer | None" = None,
                   extractor_version: str | None = None,
                   provenance_id: str = PROVENANCE_ID,
                   rejects: "list[cf_items.Reject] | None" = None) -> dict:
    """``{"items": [...], "locations": [...], "provenance": [...]}`` for one page range.

    ``xhtml`` is a ``pdftotext -bbox-layout`` document covering physical pages
    ``first`` to ``last``.  ``method`` is the sign-off the caller performed; it
    has no default, and ``manual_visual`` is written only when the caller names
    it.  A heading the item grammar refuses is appended to ``rejects`` and is
    never guessed onto an item.

    The records come back in reading order, which is the order a person checks
    them in; :func:`main` writes them canonicalised (the format's section 7),
    and a caller that merges a fragment into a document canonicalises the
    result.
    """
    if method not in SIGNOFF_METHODS:
        raise CfError(f"{method!r} is not a sign-off method",
                      hint=f"--method {' or '.join(SIGNOFF_METHODS)}; there is no default")
    if not str(verifier_id or "").strip():
        raise CfError("a sign-off names its verifier",
                      hint="--verifier-id <the agent or person who looked at the crops>")
    if not str(source_id or "").strip():
        raise CfError("every region is stamped with the source it is on",
                      hint="--source-id <the id of the kind: pdf source in the document>")
    if page_box not in vocab.ANNOTATION_PAGE_BOXES:
        raise CfError(f"{page_box!r} is not a PDF page box",
                      hint="--page-box " + "|".join(sorted(vocab.ANNOTATION_PAGE_BOXES)))

    pages = number_pages(parse_bbox_layout(xhtml), first, last)
    body = {page: body_lines(p) for page, p in pages.items()}
    for page, lines in body.items():
        if not lines:
            raise CfError(f"physical page {page} carries no text line",
                          hint="a scanned page has no text layer; narrow --first/--last")
    headings, marks = read_headings(body, kinds, numbering=numbering, rejects=rejects)

    space = {"origin": "top_left", "units": "normalized", "x_axis": "right",
             "y_axis": "down", "page_box": page_box}
    items: list[dict] = []
    locations: list[dict] = []
    seen_items: dict[str, Heading] = {}
    for heading in headings:
        if section is not None and not (heading.number == section
                                        or heading.number.startswith(section + ".")):
            continue
        item_id = ids.item_label_id(heading.kind, heading.number, heading.sub or None)
        if item_id in seen_items:
            raise CfError(f"two headings give the item id {item_id!r}: "
                          f"{seen_items[item_id].label!r} and {heading.label!r}",
                          hint="the same item is printed twice in this page range; "
                               "narrow --first/--last")
        seen_items[item_id] = heading
        record = {"id": item_id, "id_source": "label", "form": "numbered",
                  "kind": heading.kind, "number": heading.number, "label": heading.label,
                  "source": source_id}
        if heading.sub:
            record["sub"] = heading.sub
        if heading.title:
            record["title"] = heading.title
        if section is not None:
            record["section"] = section
        items.append(record)

        parts = segments(heading, marks, body, proof_kinds=proof_kinds)
        if not parts:
            raise CfError(f"{heading.label!r} on physical page {heading.page} has no region "
                          f"taller than {MIN_HEIGHT} of the page",
                          hint="check --first/--last: the heading may be the last line of the range")
        per_page: dict[int, int] = {}
        for index, (page, ya, yb) in enumerate(parts, start=1):
            per_page[page] = per_page.get(page, 0) + 1
            suffix = f"-{per_page[page]}" if per_page[page] > 1 else ""
            location_id = f"{item_id}@p{page:03d}{suffix}"
            rect = region_rect(body[page], ya, yb)
            size_pt = (pages[page].width_pt, pages[page].height_pt)
            signoff = {"method": method, "verifier": verifier_id}
            if evidence is not None:
                digest = evidence.render(page, rect, size_pt,
                                         f"{page:03d}-{item_id.replace('.', '_')}-{index:02d}")
                if digest is not None:
                    signoff["evidence_id"] = f"crop:{location_id}"
                    signoff["evidence_sha256"] = digest
            region = {"type": "PdfRegionSelector", "page": page,
                      "coordinate_space": dict(space), "rect": rect}
            locations.append({
                "id": location_id, "items": [item_id], "provenance": provenance_id,
                "pages": {"first": page, "basis": "physical"},
                "signoff": signoff,
                "target": {"source": source_id, "selector": [
                    {"type": "FragmentSelector", "conformsTo": vocab.CONFORMS_TO_PDF,
                     "value": f"page={page}", "refinedBy": region},
                    {"type": "FragmentSelector", "conformsTo": vocab.CONFORMS_TO_PDF,
                     "value": f"page={page}&highlight={_highlight(rect, size_pt)}"}]}})

    note = (f"Item headings and regions from pdftotext -bbox-layout line boxes over physical "
            f"pages {first}-{last}, then a visual sign-off per region "
            f"(signoff.method is {method}).")
    if extractor_version:
        note += f" Text layer: pdftotext {extractor_version}."
    provenance = {"id": provenance_id, "actor": "tool", "method": "layout_extraction",
                  "confirmed_by_human": method == "manual_visual",
                  "agents": [verifier_id],
                  "tool": {"name": "formal-companion overlay geometry", "version": __version__},
                  "inputs": [{"source": source_id}],
                  "note": note}
    return {"items": items, "locations": locations, "provenance": [provenance]}


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

def run_pdftotext(pdf: Path, first: int, last: int) -> tuple[str, str]:
    """``(the -bbox-layout document, the pdftotext version)`` for one page range."""
    tool = resolve_tool("pdftotext")
    if tool is None:
        raise CfError("pdftotext is not on PATH, so the PDF's text layer cannot be read",
                      hint="install poppler, or pass a saved document with --bbox-xhtml")
    if not Path(pdf).is_file():
        raise CfError(f"{pdf} is not a file", hint="--pdf names the PDF to read")
    try:
        out = subprocess.run([tool, "-f", str(first), "-l", str(last), "-bbox-layout",
                              str(pdf), "-"], check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise CfError(f"pdftotext could not read pages {first}-{last} of {pdf}: "
                      f"{(exc.stderr or b'').decode('utf-8', 'replace').strip()[:200]}",
                      hint="check the page range against the PDF's own page count") from exc
    probe = subprocess.run([tool, "-v"], capture_output=True, text=True)
    found = re.search(r"pdftotext version (\S+)", probe.stdout + probe.stderr)
    return out.stdout.decode("utf-8", "replace"), (found.group(1) if found else "unknown")


def registry_kinds(source_id: str) -> tuple[str, ...]:
    """The kinds the registry records for this source, or ``()`` when it knows none."""
    from cflib.registry import Registry
    entry = Registry.load(None).get(source_id)
    return tuple(entry.kinds) if entry is not None else ()


def _split(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


def _numbering(value: str | None):
    if not value:
        return None
    parts = _split(value)
    try:
        return (int(parts[0]), int(parts[-1]))
    except (IndexError, ValueError):
        raise CfError(f"--numbering {value!r} is not <min>,<max>",
                      hint="e.g. --numbering 2,3 for a book numbered 1.2 to 1.2.3") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geometry.py",
        description="Item headings and regions from a PDF text layer, as annotation records.")
    parser.add_argument("--pdf", type=Path, help="the PDF to read the text layer of")
    parser.add_argument("--bbox-xhtml", type=Path,
                        help="a saved pdftotext -bbox-layout document to read instead")
    parser.add_argument("--first", type=int, required=True, help="first physical page")
    parser.add_argument("--last", type=int, required=True, help="last physical page")
    parser.add_argument("--kinds", help="the source's item kinds, comma separated "
                                        "(default: the registry's for --source-id)")
    parser.add_argument("--section", help="keep only items whose number sits in this section")
    parser.add_argument("--numbering", help="<min>,<max> numeric segments a label may carry")
    parser.add_argument("--proof-kinds", default=",".join(DEFAULT_PROOF_KINDS),
                        help="kinds whose statement ends at a line beginning 'Proof.'")
    parser.add_argument("--source-id", required=True, help="the id of the PDF source")
    parser.add_argument("--verifier-id", required=True, help="who signed the regions off")
    parser.add_argument("--method", required=True, choices=list(SIGNOFF_METHODS),
                        help="how the regions were signed off; there is no default")
    parser.add_argument("--page-box", default=DEFAULT_PAGE_BOX,
                        choices=sorted(vocab.ANNOTATION_PAGE_BOXES),
                        help="the page box the coordinates are fractions of")
    parser.add_argument("--evidence-dir", type=Path,
                        help="where to write the crops (local only; never commit them)")
    parser.add_argument("--dpi", type=int, default=RENDER_DPI, help="crop resolution")
    parser.add_argument("--out", type=Path, required=True, help="the fragment to write")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.bbox_xhtml is not None:
            if not args.bbox_xhtml.is_file():
                raise CfError(f"{args.bbox_xhtml} is not a file",
                              hint="--bbox-xhtml names a saved pdftotext -bbox-layout document")
            xhtml = args.bbox_xhtml.read_text(encoding="utf-8", errors="replace")
            version = None
        elif args.pdf is not None:
            xhtml, version = run_pdftotext(args.pdf, args.first, args.last)
        else:
            raise CfError("no text layer to read",
                          hint="pass --pdf, or a saved document with --bbox-xhtml")
        kinds = _split(args.kinds) or registry_kinds(args.source_id)
        if not kinds:
            raise CfError(f"the registry records no item kinds for {args.source_id!r}",
                          hint="pass --kinds Definition,Theorem,... for this source")
        evidence = None
        if args.evidence_dir is not None:
            if args.pdf is None:
                raise CfError("crops are rendered from the PDF, and none was named",
                              hint="pass --pdf with --evidence-dir, or drop --evidence-dir")
            evidence = CropRenderer(args.pdf, args.evidence_dir, dpi=args.dpi)
            if not evidence.available:
                print("note: pdftoppm is not on PATH, so no crop is rendered and the "
                      "sign-off records no evidence")
                evidence = None
        rejects: list[cf_items.Reject] = []
        fragment = build_fragment(
            xhtml, first=args.first, last=args.last, source_id=args.source_id, kinds=kinds,
            verifier_id=args.verifier_id, method=args.method, section=args.section,
            numbering=_numbering(args.numbering), proof_kinds=_split(args.proof_kinds),
            page_box=args.page_box, evidence=evidence, extractor_version=version,
            rejects=rejects)
    except CfError as exc:
        print(exc.render(), file=sys.stderr)
        return exc.exit_code
    atomic_write(args.out, V.canonical_dumps(V.canonicalize(fragment)))
    print(f"wrote {args.out} ({len(fragment['items'])} items, "
          f"{len(fragment['locations'])} regions)")
    for reject in rejects:
        print(f"reject: {reject.raw!r} ({reject.reason})")
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
