#!/usr/bin/env python3
"""Turn the position markers of a build into boxes on the PDF's pages: markers.json.

    python3 informal/tools/extract_markers.py OUTDIR

Part of formal-companion's informalize runtime (docs/proposals/informalize.md, sections 5.6
and 7.3, in the formal-companion skill); ported from the pilot's extract_markers.py, whose
constants now come from informalize.json's `boxes`.  Needs poppler (pdfinfo, pdftoppm).

Reads OUTDIR/<job>.aux, OUTDIR/<job>.pdf and OUTDIR/build-receipt.json, as
tools/build_pdf.sh left them, and writes OUTDIR/markers.json.  For every item and every
part it records the id, the LaTeX label, the printed number, the declarations the build
recorded there and one or more boxes [page, x, y, w, h]: page is 1-based, and x, y, w, h
are fractions of the page width and height, measured from the top left corner.  An item or
part that crosses a page break gets one box per page.  For every `\\fcnotion` it records
the constant, the words as printed and the boxes around those words.

The markers decide which lines belong to an item or part; the box is fitted to the ink of
those lines.  Every page is rendered with pdftoppm at res_dpi (a pixel is ink when its gray
value is below ink_level), and on each page the region runs from its first line to its
last, or to the edge of the text block where a page break cuts it.  The first line is
found from the above marker, which fcmarkers.sty puts on the vertical list before that
line and its interline glue: the region starts at the first ink below the first blank
pixel row under it, so a line taller than a baselineskip keeps its top.  The last line is
found from the below marker, at the bottom of that line's box (or, after a closing display
or list, from the end marker): the region ends at the last ink above the first blank row
from there down, so a nested denominator or a limit below the line stays in.  The box is
the ink of the region padded by pad_bp above and below; at the
sides it is the text block padded by pad_bp, widened where ink comes within pad_bp / 2 of
that, so the boxes of a column line up.  Moreover

  * a box never reaches into the ink around it: where that ink is closer than 2 pad_bp, the
    edge goes halfway between;
  * two consecutive parts on a page share one edge, halfway between their ink, unless the
    ink is at least 2 pad_bp + frame_bp apart;
  * boxes of different items stay frame_bp apart where that leaves CLEAR_BP between each
    edge and its ink, so that a proof sheet's stroke misses the ink; else they share one
    edge, halfway between their ink;
  * an item's box encloses the boxes of its parts, and a part edge closer than frame_bp to
    the item's edge is moved onto it (no double lines).

A notion's box is fitted differently, because its words are a few on one line rather than
whole lines: its side edges are the two positions the markers recorded, and its top and
bottom are the ink of the line the words are on, padded as an item's is.  The left edge is
moved half a pad into the glue before the words, since a letter's ink may begin at the pen
(the f of "fundamental" does); the right edge stays at the pen after the last letter,
where a comma or a full stop may follow with no glue at all.  Where a notion is broken
over lines it gets one box per line, the first from its start marker to the text block's
right edge and the last from its left edge to the end marker, those two padded as an
item's sides are.

It assumes what the document template gives: one column, items that span the text block,
and lines at least 2pt apart (fcmarkers.sty sets \\lineskiplimit), so that an edge between
two parts never has to cross ink.

Apache License 2.0.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fccommon as fc  # noqa: E402

XHEIGHT_BP = 4.0    # every line has ink at least this far above its baseline
SLACK_BP = 3.0      # how far above the text block a page's first line may reach
CLEAR_BP = 2.0      # ink to an item's edge before frame_bp is kept (3 pixels at 110 dpi)


@dataclass(frozen=True)
class Params:
    """informalize.json's `boxes`."""

    res_dpi: int
    ink_level: int
    pad_bp: float
    frame_bp: float

    @classmethod
    def of(cls, boxes: dict) -> "Params":
        return cls(int(boxes["res_dpi"]), int(boxes["ink_level"]), float(boxes["pad_bp"]),
                   float(boxes["frame_bp"]))


class Geometry:
    def __init__(self, g: dict[str, int], pages: list[tuple[float, float]]):
        need = ("textleft", "texttop", "textwidth", "textheight", "strutheight",
                "strutdepth", "baselineskip")
        missing = [k for k in need if k not in g]
        if missing:
            raise fc.ToolError(f"no geometry ({', '.join(missing)}) in the .aux; is this a "
                               "build of a document that loads fcmarkers.sty?")
        bp = {k: v / fc.SP_PER_BP for k, v in g.items()}
        self.left = bp["textleft"]
        self.top = bp["texttop"]
        self.width = bp["textwidth"]
        self.bottom = bp["texttop"] + bp["textheight"]
        self.ht = bp["strutheight"]
        self.dp = bp["strutdepth"]
        self.bskip = bp["baselineskip"]
        self.pages = pages
        if "pdfpagewidth" in bp:
            w, h = pages[0]
            if abs(w - bp["pdfpagewidth"]) > 1 or abs(h - bp["pdfpageheight"]) > 1:
                raise fc.ToolError("pdfinfo's page size disagrees with the .aux")

    def size(self, page: int) -> tuple[float, float]:
        return self.pages[page - 1]

    def from_top(self, pos: dict[str, int]) -> float:
        """Distance in bp from the top of the page to a marker."""
        return self.size(pos["abspage"])[1] - pos["posy"] / fc.SP_PER_BP

    @property
    def floor(self) -> float:
        """Lowest point (bp from the top) the ink of a page's last line may reach."""
        return self.bottom + 0.6 * self.bskip


# ---------------------------------------------------------------------------
# Ink of the rendered pages

class PageInk:
    """The pixel rows of a rendered page that hold ink, and where in the row."""

    def __init__(self, pgm: bytes, size: tuple[float, float], ink_level: int):
        fields, pos = [], 0
        while len(fields) < 4:
            while pgm[pos:pos + 1].isspace():
                pos += 1
            if pgm[pos:pos + 1] == b"#":
                pos = pgm.index(b"\n", pos) + 1
                continue
            end = pos
            while not pgm[end:end + 1].isspace():
                end += 1
            fields.append(pgm[pos:end])
            pos = end
        if fields[0] != b"P5" or fields[3] != b"255":
            raise fc.ToolError("pdftoppm did not write an 8-bit P5 image")
        w, h = int(fields[1]), int(fields[2])
        data = pgm[pos + 1:pos + 1 + w * h]
        self.h = h
        self.sx, self.sy = w / size[0], h / size[1]
        table = bytes(1 if v < ink_level else 0 for v in range(256))
        # rows[r] = (first ink column, last ink column, ink pixels), or None.
        self.rows: list[tuple[int, int, int] | None] = []
        for r in range(h):
            seg = data[r * w:(r + 1) * w].translate(table)
            n = seg.count(1)
            self.rows.append((seg.find(1), seg.rfind(1), n) if n else None)

    def row(self, y: float) -> int:
        """The pixel row at y bp from the top."""
        return min(self.h - 1, max(0, int(y * self.sy)))

    def first_ink(self, r0: int, r1: int) -> int | None:
        for r in range(max(0, r0), min(self.h - 1, r1) + 1):
            if self.rows[r]:
                return r
        return None

    def last_ink(self, r0: int, r1: int) -> int | None:
        for r in range(min(self.h - 1, r1), max(0, r0) - 1, -1):
            if self.rows[r]:
                return r
        return None

    def cut(self, upper: float, lower: float) -> int:
        """The row that parts what ends at `upper` (a baseline, or the bottom of a display
        or list) from the line whose baseline is `lower`: the first blank row from `upper`
        down, or else the row with the least ink.  The row itself goes with what is above
        it."""
        r0, r1 = self.row(upper), self.row(lower - XHEIGHT_BP)
        r1 = max(r0, r1)
        for r in range(r0, r1 + 1):
            if not self.rows[r]:
                return r
        return min(range(r0, r1 + 1), key=lambda r: self.rows[r][2])  # type: ignore[index]

    def xspan(self, r0: int, r1: int) -> tuple[float, float] | None:
        """Leftmost and rightmost ink, in bp, of the rows r0..r1."""
        got = [self.rows[r] for r in range(max(0, r0), min(self.h - 1, r1) + 1) if self.rows[r]]
        if not got:
            return None
        return min(g[0] for g in got) / self.sx, (max(g[1] for g in got) + 1) / self.sx


def render_pages(pdf: Path, geo: Geometry, params: Params) -> list[PageInk]:
    out = []
    with tempfile.TemporaryDirectory() as t:
        for page in range(1, len(geo.pages) + 1):
            stem = Path(t) / f"p{page}"
            subprocess.run(["pdftoppm", "-gray", "-r", str(params.res_dpi), "-f", str(page),
                            "-l", str(page), "-singlefile", str(pdf), str(stem)], check=True)
            pgm = stem.with_suffix(".pgm")
            out.append(PageInk(pgm.read_bytes(), geo.size(page), params.ink_level))
            pgm.unlink()
    return out


# ---------------------------------------------------------------------------
# Regions and boxes

@dataclass
class Span:
    """The part of an item or part on one page: its ink and its box, in bp."""
    page: int
    ink_top: float
    ink_bottom: float
    ink_left: float
    ink_right: float
    above: float | None       # bottom of other ink less than 2 pad_bp above
    below: float | None       # top of other ink less than 2 pad_bp below
    top: float = 0.0
    bottom: float = 0.0
    left: float = 0.0
    right: float = 0.0

    def pad(self, geo: Geometry, params: Params) -> None:
        pad = params.pad_bp
        self.top = self.ink_top - pad
        if self.above is not None:
            self.top = max(self.top, (self.above + self.ink_top) / 2)
        self.bottom = self.ink_bottom + pad
        if self.below is not None:
            self.bottom = min(self.bottom, (self.ink_bottom + self.below) / 2)
        self.left = min(geo.left - pad, self.ink_left - pad / 2)
        self.right = max(geo.left + geo.width + pad, self.ink_right + pad / 2)

    def box(self) -> list[float]:
        return [self.page, self.left, self.top, self.right - self.left, self.bottom - self.top]


@dataclass
class End:
    page: int
    y: float | None    # None: the region runs to the bottom of the page


def effective_end(geo: Geometry, start: dict, end: dict, fallback: dict | None) -> End:
    """Where a region ends.  `end` is the region's below marker, or its end marker when that
    is in vertical mode.  An end marker alone at the top of a later page belongs to the
    page before (to the end of the last part, if there is one)."""
    p1, p2 = start["abspage"], end["abspage"]
    ye = geo.from_top(end)
    if p2 > p1 and ye - geo.top < 0.5 * geo.bskip:
        if fallback and p1 <= fallback["abspage"] < p2:
            return End(fallback["abspage"], geo.from_top(fallback))
        return End(p2 - 1, None)
    return End(p2, ye)


def region_spans(geo: Geometry, ink: list[PageInk], params: Params, start: dict,
                 upper: float | None, end: End, lower: float | None, name: str) -> list[Span]:
    """The spans of a region from a start marker to an end.  `upper` is the region's above
    marker (default one baselineskip above the first baseline), `lower` the baseline of the
    line after the last line (default one baselineskip down)."""
    p1 = start["abspage"]
    ys = geo.from_top(start)
    out = []
    for p in range(p1, end.page + 1):
        pg = ink[p - 1]
        if p == p1 and upper is not None and upper - geo.top < 1.0:
            # the region starts the page: nothing of the text block is above it
            t = pg.first_ink(pg.row(geo.top - SLACK_BP), pg.row(ys))
        elif p == p1:
            c = pg.cut(ys - geo.bskip if upper is None else upper, ys)
            t = pg.first_ink(c + 1, pg.row(ys))
        else:
            t = pg.first_ink(pg.row(geo.top - SLACK_BP), pg.row(geo.floor))
        if p == end.page and end.y is not None:
            c = pg.cut(end.y, end.y + geo.bskip if lower is None else lower)
            b = pg.last_ink(-1 if t is None else t, c)
        else:
            b = pg.last_ink(-1 if t is None else t, pg.row(geo.floor))
        if t is None or b is None or b < t:
            # No ink found: fall back to the struts of the marked lines.  A box always
            # keeps at least its marker line, even on a forced overfull page.
            print(f"extract_markers: no ink for {name} on page {p}; using struts",
                  file=sys.stderr)
            top = ys - geo.ht if p == p1 else geo.top
            bottom = (end.y + geo.dp if p == end.page and end.y is not None
                      else geo.bottom + geo.dp)
            t, b = pg.row(top), max(pg.row(top), pg.row(bottom) - 1)
        reach = 2 * params.pad_bp * pg.sy
        above = pg.last_ink(t - int(reach) - 1, t - 1)
        below = pg.first_ink(b + 1, b + int(reach) + 1)
        xs = pg.xspan(t, b) or (geo.left, geo.left + geo.width)
        out.append(Span(p, t / pg.sy, (b + 1) / pg.sy, xs[0], xs[1],
                        None if above is None else (above + 1) / pg.sy,
                        None if below is None else below / pg.sy))
    for s in out:
        s.pad(geo, params)
    return out


def line_runs(pg: PageInk, r0: int, r1: int) -> list[tuple[int, int]]:
    """The maximal runs of ink rows between r0 and r1: one per line of text there."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for r in range(max(0, r0), min(pg.h - 1, r1) + 1):
        if pg.rows[r]:
            if start is None:
                start = r
        elif start is not None:
            out.append((start, r - 1))
            start = None
    if start is not None:
        out.append((start, min(pg.h - 1, r1)))
    return out


def notion_spans(geo: Geometry, ink: list[PageInk], params: Params, start: dict, end: dict,
                 name: str) -> list[Span]:
    """The spans of a notion's words, from its start marker to its end marker.

    One span per line the words lie on: the sides are the recorded positions where the
    words begin and end, and the text block's edge where a line break cuts them.
    """
    pad = params.pad_bp
    xs, xe = start["posx"] / fc.SP_PER_BP, end["posx"] / fc.SP_PER_BP
    ys, ye = geo.from_top(start), geo.from_top(end)
    out: list[Span] = []
    for p in range(start["abspage"], end["abspage"] + 1):
        pg = ink[p - 1]
        first_page, last_page = p == start["abspage"], p == end["abspage"]
        top = pg.cut(ys - geo.bskip, ys) + 1 if first_page else pg.row(geo.top - SLACK_BP)
        bottom = pg.cut(ye, ye + geo.bskip) if last_page else pg.row(geo.floor)
        # Only the lines the markers are on.  Where the line above reaches into the
        # interline gap (a nested fraction, an underbrace label), the cut lands inside its
        # ink and leaves a run of that line in the band; that run is not the notion's, and
        # taking it would move the words' own run off k == 0 and widen its box to the
        # margin.  A run that reaches into the struts of the marked lines is kept, so a
        # descender or a limit of the notion's own line stays.
        runs = [(r0, r1) for r0, r1 in line_runs(pg, top, bottom)
                if (not first_page or r1 >= pg.row(ys - geo.ht))
                and (not last_page or r0 <= pg.row(ye + geo.dp))]
        if not runs:
            print(f"extract_markers: no ink for {name} on page {p}; using the strut",
                  file=sys.stderr)
            runs = [(pg.row(ys - geo.ht), max(pg.row(ys - geo.ht), pg.row(ys + geo.dp)))]
        for k, (r0, r1) in enumerate(runs):
            left = xs - pad / 2 if first_page and k == 0 else geo.left - pad
            right = xe if last_page and k == len(runs) - 1 else geo.left + geo.width + pad
            if right <= left:
                continue
            reach = int(2 * pad * pg.sy)
            above = pg.last_ink(r0 - reach - 1, r0 - 1)
            below = pg.first_ink(r1 + 1, r1 + reach + 1)
            span = Span(p, r0 / pg.sy, (r1 + 1) / pg.sy, left, right,
                        None if above is None else (above + 1) / pg.sy,
                        None if below is None else below / pg.sy)
            span.pad(geo, params)            # the top and the bottom of an item's span
            span.left, span.right = left, right   # the sides are the marked positions
            out.append(span)
    return out


def share_edge(a: Span, b: Span, params: Params) -> None:
    """Consecutive parts a above b on one page: one edge, or frame_bp apart."""
    if b.ink_top - a.ink_bottom < 2 * params.pad_bp + params.frame_bp:
        a.bottom = b.top = (a.ink_bottom + b.ink_top) / 2


def separate_items(spans: list[Span], params: Params) -> None:
    """Item spans on one page, top to bottom: keep them frame_bp apart if the ink leaves
    CLEAR_BP (at most pad_bp) between each edge and its ink, else give them one edge halfway
    between their ink; never let them overlap."""
    frame, clear = params.frame_bp, min(CLEAR_BP, params.pad_bp)
    spans = sorted(spans, key=lambda s: s.ink_top)
    for a, b in zip(spans, spans[1:]):
        if b.top - a.bottom >= frame:
            continue
        pad = min(params.pad_bp, (b.ink_top - a.ink_bottom - frame) / 2)
        if pad >= clear:
            a.bottom, b.top = a.ink_bottom + pad, b.ink_top - pad
        else:
            a.bottom = b.top = (a.ink_bottom + b.ink_top) / 2


def enclose(item: list[Span], part: Span, params: Params) -> None:
    """Fit a part span inside the item's span on its page, without double lines."""
    frame = params.frame_bp
    for s in item:
        if s.page != part.page:
            continue
        part.top = max(part.top, s.top)
        part.bottom = min(part.bottom, s.bottom)
        part.left = max(part.left, s.left)
        part.right = min(part.right, s.right)
        if part.top - s.top < frame:
            part.top = s.top
        if s.bottom - part.bottom < frame:
            part.bottom = s.bottom
        if part.left - s.left < frame:
            part.left = s.left
        if s.right - part.right < frame:
            part.right = s.right


def to_fractions(geo: Geometry, boxes: list[list[float]]) -> list[list[float]]:
    out = []
    for p, x, y, w, h in boxes:
        pw, ph = geo.size(p)
        x0, y0 = max(0.0, x), max(0.0, y)
        x1, y1 = min(pw, x + w), min(ph, y + h)
        if x1 <= x0 or y1 <= y0:
            continue
        out.append([p, round(x0 / pw, 6), round(y0 / ph, 6),
                    round((x1 - x0) / pw, 6), round((y1 - y0) / ph, 6)])
    return out


def entry_skeletons(aux: fc.Aux) -> list[dict]:
    """The entries of markers.json without their boxes, from the .aux alone.

    check_items.py compares a markers.json with these to tell that it is of this build.
    """
    out = []
    for item in aux.items:
        iid = item["id"]
        parts = aux.parts_of(iid)
        out.append({"id": iid, "kind": item["kind"], "label": f"item:{iid}", "number": iid,
                    "section": item["section"], "title": item["title"],
                    "parts": [f"{iid}({p})" for p in parts], "decls": aux.decls_of(iid, "")})
        for p in parts:
            out.append({"id": f"{iid}({p})", "parent": iid, "part": p,
                        "label": f"item:{iid}:{p}", "number": f"{iid}({p})",
                        "decls": aux.decls_of(iid, p)})
    for n in aux.notions:
        out.append({"id": f"notion:{n['name']}", "notion": n["name"], "words": n["words"],
                    "label": f"notion:{n['index']}", "number": f"n{n['index']}",
                    **({"title": n["title"]} if n.get("title") else {})})
    return out


def extract(build: fc.Build) -> dict:
    build.need(build.aux, build.pdf)
    receipt = build.receipt()
    params = Params.of(build.config.boxes)
    aux = fc.read_aux(build.aux)
    geo = Geometry(aux.geometry, fc.pdf_pages(build.pdf))
    ink = render_pages(build.pdf, geo, params)

    def pos(name: str) -> dict:
        if name not in aux.positions:
            raise fc.ToolError(f"marker {name} missing from the .aux; rebuild")
        return aux.positions[name]

    def above(name: str, start: dict) -> float | None:
        """The above marker of a region, on its first page."""
        mark = pos(name + ":above")
        return geo.from_top(mark) if mark["abspage"] == start["abspage"] else None

    def bottom(name: str) -> dict:
        """The below marker of a region, or its end marker when that is in vertical mode."""
        end = pos(name + ":end")
        below = aux.positions.get(name + ":below")
        return below if below and below["abspage"] == end["abspage"] else end

    items = []   # (item, parts, item spans, part spans)
    for item in aux.items:
        iid = item["id"]
        parts = aux.parts_of(iid)
        names = [f"fc:{iid}:{p}" for p in parts]
        starts = [pos(n + ":start") for n in names]
        ends = [effective_end(geo, s, bottom(n), None) for n, s in zip(names, starts)]
        part_spans = []
        for k, n in enumerate(names):
            # Above the part's first line: its above marker, else the end of the part
            # before it on that page.
            prev = ends[k - 1] if k else None
            upper = above(n, starts[k])
            if upper is None and prev and prev.y is not None \
                    and prev.page == starts[k]["abspage"]:
                upper = prev.y
            # The line after a part: the start of the next part on that page.
            nxt = starts[k + 1] if k + 1 < len(starts) else None
            lower = (geo.from_top(nxt) if nxt and nxt["abspage"] == ends[k].page else None)
            part_spans.append(region_spans(geo, ink, params, starts[k], upper, ends[k], lower,
                                           f"{iid}({parts[k]})"))
        for a, b in zip(part_spans, part_spans[1:]):
            if a[-1].page == b[0].page:
                share_edge(a[-1], b[0], params)
        fallback = bottom(names[-1]) if parts else None
        start = pos(f"fc:{iid}:start")
        end = effective_end(geo, start, bottom(f"fc:{iid}"), fallback)
        item_spans = region_spans(geo, ink, params, start, above(f"fc:{iid}", start), end,
                                  None, iid)
        # The item's span on a page reaches at least as far as its parts' ink.
        for ps in (s for spans in part_spans for s in spans):
            for s in item_spans:
                if s.page != ps.page:
                    continue
                if ps.ink_top < s.ink_top:
                    s.ink_top, s.above = ps.ink_top, ps.above
                if ps.ink_bottom > s.ink_bottom:
                    s.ink_bottom, s.below = ps.ink_bottom, ps.below
                s.ink_left = min(s.ink_left, ps.ink_left)
                s.ink_right = max(s.ink_right, ps.ink_right)
                s.pad(geo, params)
        items.append((item, parts, item_spans, part_spans))

    pages: dict[int, list[Span]] = {}
    for _, _, item_spans, _ in items:
        for s in item_spans:
            pages.setdefault(s.page, []).append(s)
    for spans in pages.values():
        separate_items(spans, params)
    for _, _, item_spans, part_spans in items:
        for spans in part_spans:
            for s in spans:
                enclose(item_spans, s, params)

    boxes = {}
    for item, parts, item_spans, part_spans in items:
        boxes[item["id"]] = to_fractions(geo, [s.box() for s in item_spans])
        for p, spans in zip(parts, part_spans):
            boxes[f"{item['id']}({p})"] = to_fractions(geo, [s.box() for s in spans])
    for n in aux.notions:
        marker = f"fc:notion:{n['index']}"
        spans = notion_spans(geo, ink, params, pos(marker + ":start"), pos(marker + ":end"),
                             f"notion {n['name']}")
        boxes[f"notion:{n['name']}"] = to_fractions(geo, [s.box() for s in spans])
    entries = [dict(e, boxes=boxes[e["id"]]) for e in entry_skeletons(aux)]
    return {
        "schema": fc.MARKERS_SCHEMA,
        "schema_version": fc.SCHEMA_VERSION,
        "job": build.config.job,
        "source_commit": receipt.get("source_commit"),
        "pdf_sha256": fc.sha256_file(build.pdf),
        "pages": len(geo.pages),
        "page_size_pt": list(geo.pages[0]),
        "boxes_config": build.config.boxes,
        "box_format": "[page, x, y, w, h]; page 1-based; fractions of the page from the top left",
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir", help="the build directory build_pdf.sh wrote")
    args = ap.parse_args(argv)
    try:
        fc.require_programs("pdfinfo", "pdftoppm")
        build = fc.Build(args.outdir)
        result = extract(build)
    except fc.ToolError as exc:
        print(f"extract_markers: {exc}", file=sys.stderr)
        return 2
    fc.write_json(build.markers, result)
    n_notions = sum(1 for e in result["entries"] if "notion" in e)
    n_parts = sum(1 for e in result["entries"] if "parent" in e)
    n_items = len(result["entries"]) - n_parts - n_notions
    n_boxes = sum(len(e["boxes"]) for e in result["entries"])
    print(f"extract_markers: {n_items} items, {n_parts} parts, {n_notions} notions, "
          f"{n_boxes} boxes -> {build.markers}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
