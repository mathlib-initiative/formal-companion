#!/usr/bin/env python3
"""Draw the boxes of markers.json over the pages of the PDF (proof sheets), and check them.

    python3 informal/tools/proof_sheets.py OUTDIR [--dest DIR] [--dpi N] [--tight]
                                                  [--list-unboxed]

Part of formal-companion's informalize runtime (docs/proposals/informalize.md, section 7.3,
in the formal-companion skill); ported from the pilot's proof_sheets.py, with its ink level
raised from 160 to informalize.json's `boxes.ink_level` (224, the level extract_markers.py
fits boxes at), so light grey pixels under a stroke count as ink.  Needs poppler
(pdftoppm, pdftotext, pdfinfo).

Reads OUTDIR/<job>.pdf, OUTDIR/<job>.aux and OUTDIR/markers.json (written by
extract_markers.py) and writes, for every page NN:

  DIR/render/page-NN.png   the page as pdftoppm renders it at N dpi;
  DIR/page-NN.png          the same image with every item box in red and every part box in
                           blue, each a 2-pixel stroke centred on the box edge (as the
                           overlay viewer draws it), with the item id in the left margin and
                           the part id in the right margin at the top of the box;
  DIR/page-NN.svg          the same boxes and ids as vector shapes over render/page-NN.png.

DIR defaults to OUTDIR/proof-sheets/ and N to 110 (a letter page is then 935 pixels
wide).  Then it checks, on the rendered pages and on the words of pdftotext -tsv:

  * ink under a stroke: no box edge runs through ink: the edge's own pixel row (or
    column) at res_dpi, the resolution extract_markers.py fits the boxes at, holds no pixel
    whose darkest channel is below ink_level.  Where the 2-pixel stroke of a top or bottom
    edge drawn at N dpi touches ink, as it may where \\lineskip leaves two lines 2pt apart,
    the edge is reported as a tight edge, not fatal;
  * cut words: no word lies across a box edge (between 25% and 75% of its pdftotext box
    inside);
  * stray item text: in an item with parts, no word of the item lies between two parts or
    after the last part;
  * ink outside its boxes: between an item's above marker and its below marker (or its
    end marker in vertical mode), read from the .aux, all ink lies in the item's boxes on
    that page, so no accent, denominator or limit of the item's first or last line is
    left out;
  * frames apart: boxes of different items on a page are at least frame_bp apart (less a
    pixel for rounding), and a part box either shares its top edge with the box above it or
    keeps that distance from it (reported, not fatal);
  * with --tight, also every horizontal edge with ink closer than CLEAR_PX pixels to its
    stroke;
  * with --list-unboxed, every line of text outside all item boxes, for reading.

The exit status is 1 when a box edge runs through ink, a word is cut, item text strays or
ink lies outside its boxes, and 2 when an input is missing.

Standard library only (zlib writes the PNG files).  Apache License 2.0.
"""
from __future__ import annotations

import argparse
import math
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fccommon as fc  # noqa: E402

ITEM_RGB = (214, 40, 40)
PART_RGB = (30, 90, 220)
CLEAR_PX = 2       # wanted clearance between a horizontal edge and ink

# A 5 x 7 bitmap font for box ids and page numbers.
FONT = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    "a": ["00000", "00000", "01110", "00001", "01111", "10001", "01111"],
    "b": ["10000", "10000", "10110", "11001", "10001", "10001", "11110"],
    "c": ["00000", "00000", "01110", "10000", "10000", "10001", "01110"],
    "d": ["00001", "00001", "01101", "10011", "10001", "10001", "01111"],
    "e": ["00000", "00000", "01110", "10001", "11111", "10000", "01110"],
    "f": ["00110", "01001", "01000", "11100", "01000", "01000", "01000"],
    "g": ["00000", "01111", "10001", "10001", "01111", "00001", "01110"],
    "h": ["10000", "10000", "10110", "11001", "10001", "10001", "10001"],
    "i": ["00100", "00000", "01100", "00100", "00100", "00100", "01110"],
    "j": ["00010", "00000", "00110", "00010", "00010", "10010", "01100"],
    "k": ["10000", "10000", "10010", "10100", "11000", "10100", "10010"],
    "l": ["01100", "00100", "00100", "00100", "00100", "00100", "01110"],
    "m": ["00000", "00000", "11010", "10101", "10101", "10001", "10001"],
    "n": ["00000", "00000", "10110", "11001", "10001", "10001", "10001"],
    "o": ["00000", "00000", "01110", "10001", "10001", "10001", "01110"],
    "p": ["00000", "00000", "11110", "10001", "11110", "10000", "10000"],
    "q": ["00000", "00000", "01101", "10011", "01111", "00001", "00001"],
    "r": ["00000", "00000", "10110", "11001", "10000", "10000", "10000"],
    "s": ["00000", "00000", "01110", "10000", "01110", "00001", "11110"],
    "t": ["01000", "01000", "11100", "01000", "01000", "01001", "00110"],
    "u": ["00000", "00000", "10001", "10001", "10001", "10011", "01101"],
    "v": ["00000", "00000", "10001", "10001", "10001", "01010", "00100"],
    "w": ["00000", "00000", "10001", "10001", "10101", "10101", "01010"],
    "x": ["00000", "00000", "10001", "01010", "00100", "01010", "10001"],
    "y": ["00000", "00000", "10001", "10001", "01111", "00001", "01110"],
    "z": ["00000", "00000", "11111", "00010", "00100", "01000", "11111"],
    " ": ["00000"] * 7,
}
GLYPH_W, GLYPH_H, SCALE = 6, 7, 2
_INK_TABLES: dict[int, bytes] = {}     # ink level -> byte table, 1 for an ink channel value


class Image:
    """An RGB image held as a bytearray, read from a binary PPM."""

    def __init__(self, ppm: bytes, ink_level: int):
        self.ink_level = ink_level
        fields, pos = [], 0
        while len(fields) < 4:
            while ppm[pos:pos + 1].isspace():
                pos += 1
            if ppm[pos:pos + 1] == b"#":
                pos = ppm.index(b"\n", pos) + 1
                continue
            end = pos
            while not ppm[end:end + 1].isspace():
                end += 1
            fields.append(ppm[pos:end])
            pos = end
        if fields[0] != b"P6" or fields[3] != b"255":
            raise fc.ToolError("pdftoppm did not write an 8-bit P6 image")
        self.w, self.h = int(fields[1]), int(fields[2])
        self.px = bytearray(ppm[pos + 1:pos + 1 + 3 * self.w * self.h])

    def copy(self) -> "Image":
        other = object.__new__(Image)
        other.w, other.h, other.px = self.w, self.h, bytearray(self.px)
        other.ink_level = self.ink_level
        return other

    def ink(self, x: int, y: int) -> bool:
        if not (0 <= x < self.w and 0 <= y < self.h):
            return False
        i = 3 * (y * self.w + x)
        return min(self.px[i], self.px[i + 1], self.px[i + 2]) < self.ink_level

    def row_ink(self, y: int) -> tuple[int, int] | None:
        """The first and last ink column of row y, or None."""
        if not 0 <= y < self.h:
            return None
        table = _INK_TABLES.setdefault(
            self.ink_level, bytes(1 if v < self.ink_level else 0 for v in range(256)))
        row = bytes(self.px[3 * y * self.w:3 * (y + 1) * self.w]).translate(table)
        first = row.find(1)
        return None if first < 0 else (first // 3, row.rfind(1) // 3)

    def put(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = 3 * (y * self.w + x)
            self.px[i:i + 3] = bytes(rgb)

    def hline(self, x0: int, x1: int, y: int, rgb) -> None:
        for x in range(x0, x1 + 1):
            self.put(x, y, rgb)

    def vline(self, x: int, y0: int, y1: int, rgb) -> None:
        for y in range(y0, y1 + 1):
            self.put(x, y, rgb)

    def stroke(self, box: "Box", rgb) -> None:
        # 2 pixels centred on each edge: the rows/columns just before and at it.
        for y in (box.y0 - 1, box.y0, box.y1 - 1, box.y1):
            self.hline(box.x0 - 1, box.x1, y, rgb)
        for x in (box.x0 - 1, box.x0, box.x1 - 1, box.x1):
            self.vline(x, box.y0 - 1, box.y1, rgb)

    def text(self, x: int, y: int, s: str, rgb) -> None:
        for n, ch in enumerate(s):
            for r, row in enumerate(FONT.get(ch, FONT[" "])):
                for c, bit in enumerate(row):
                    if bit == "1":
                        for dy in range(SCALE):
                            for dx in range(SCALE):
                                self.put(x + SCALE * (GLYPH_W * n + c) + dx,
                                         y + SCALE * r + dy, rgb)

    def png(self) -> bytes:
        row = 3 * self.w
        raw = b"".join(b"\x00" + bytes(self.px[y * row:(y + 1) * row]) for y in range(self.h))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def text_width(s: str) -> int:
    return SCALE * GLYPH_W * len(s)


class Box:
    def __init__(self, entry: dict, box: list[float], w: int, h: int):
        self.id = entry["id"]
        self.item = entry.get("parent", entry["id"])
        self.is_part = "parent" in entry
        self.number = entry["number"]
        self.page = box[0]
        self.fx, self.fy, self.fw, self.fh = box[1:]
        self.x0, self.y0 = round(box[1] * w), round(box[2] * h)
        self.x1, self.y1 = round((box[1] + box[3]) * w), round((box[2] + box[4]) * h)

    def frac_of(self, wx0: float, wy0: float, wx1: float, wy1: float) -> float:
        """Fraction of the area of a rectangle (page fractions) inside the box."""
        dx = min(wx1, self.fx + self.fw) - max(wx0, self.fx)
        dy = min(wy1, self.fy + self.fh) - max(wy0, self.fy)
        area = max(1e-12, (wx1 - wx0) * (wy1 - wy0))
        return max(0.0, dx) * max(0.0, dy) / area


def render(pdf: Path, page: int, dpi: int, tmp: Path, ink_level: int) -> Image:
    stem = tmp / f"p{page}"
    subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page),
                    "-singlefile", str(pdf), str(stem)], check=True)
    ppm = tmp / f"p{page}.ppm"
    img = Image(ppm.read_bytes(), ink_level)
    ppm.unlink()
    return img


def words_of(pdf: Path) -> dict[int, list[dict]]:
    """The words of pdftotext -tsv by page, with boxes in page fractions."""
    out = subprocess.run(["pdftotext", "-tsv", "-enc", "UTF-8", str(pdf), "-"],
                         capture_output=True, text=True, check=True).stdout
    sizes = fc.pdf_pages(pdf)
    pages: dict[int, list[dict]] = {}
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 12 or f[0] != "5":
            continue
        page = int(f[1])
        pw, ph = sizes[page - 1]
        left, top, width, height = map(float, f[6:10])
        pages.setdefault(page, []).append({
            "line": (int(f[2]), int(f[3]), int(f[4])),
            "x0": left / pw, "y0": top / ph,
            "x1": (left + width) / pw, "y1": (top + height) / ph,
            "text": f[11],
        })
    return pages


def item_bands(aux: fc.Aux, sizes: list[tuple[float, float]]) -> dict[int, dict[str, tuple]]:
    """``{page: {item id: (top, bottom)}}``, in bp from the top of the page: the band in which
    all ink is the item's.  It runs from just below the item's above marker to its below
    marker (or its end marker in vertical mode), and to the edges of the text block where a
    page break cuts the item."""
    g = aux.geometry
    if not {"texttop", "textheight", "baselineskip"} <= set(g):
        return {}
    text_top = g["texttop"] / fc.SP_PER_BP
    floor = text_top + (g["textheight"] + 0.6 * g["baselineskip"]) / fc.SP_PER_BP

    def from_top(mark: dict) -> float:
        return sizes[mark["abspage"] - 1][1] - mark["posy"] / fc.SP_PER_BP

    out: dict[int, dict[str, tuple]] = {}
    for item in aux.items:
        name = f"fc:{item['id']}"
        above, end = aux.positions.get(name + ":above"), aux.positions.get(name + ":end")
        if not above or not end:
            raise fc.ToolError(f"marker {name}:{'end' if above else 'above'} missing from the "
                               ".aux; rebuild")
        below = aux.positions.get(name + ":below")
        last = below if below and below["abspage"] == end["abspage"] else end
        for page in range(above["abspage"], last["abspage"] + 1):
            # the first line's ink is at least \lineskip (2pt) below the above marker
            top = from_top(above) + 1.0 if page == above["abspage"] else text_top - 3.0
            bottom = from_top(last) if page == last["abspage"] else floor
            if bottom > top:
                out.setdefault(page, {})[item["id"]] = (top, bottom)
    return out


def check_page(img: Image, boxes: list[Box], words: list[dict], page: int,
               out: dict[str, list[str]], list_unboxed: bool, tight: bool,
               frame_px: int, bands: dict[str, tuple] | None = None,
               page_h: float | None = None, fine: Image | None = None) -> None:
    """Check one page.  `img` is the page at the sheet's dpi, `fine` the same page at res_dpi
    (default `img`)."""
    def note(kind: str, msg: str) -> None:
        out.setdefault(kind, []).append(f"p.{page} {msg}")

    # Ink under a stroke: the edge's own row or column at res_dpi.  Tight edges: the stroke
    # at the sheet's dpi touches ink, or (--tight) comes within CLEAR_PX pixels of it.
    fine = fine or img
    for b in boxes:
        x0, x1 = math.floor(b.fx * fine.w), math.floor((b.fx + b.fw) * fine.w)
        y0, y1 = math.floor(b.fy * fine.h), math.floor((b.fy + b.fh) * fine.h)
        for name, y, stroke in (("top", y0, b.y0), ("bottom", y1, b.y1)):
            hits = sum(1 for x in range(x0, x1 + 1) if fine.ink(x, y))
            if hits:
                note("ink under a stroke", f"{b.id}: {name} edge (row {y} at res_dpi) runs "
                                           f"through {hits} ink pixels")
                continue
            near = []
            under = sum(1 for yy in (stroke - 1, stroke) for x in range(b.x0, b.x1 + 1)
                        if img.ink(x, yy))
            if under:
                near.append(f"{under} ink pixels under the stroke")
            for d in range(1, CLEAR_PX + 1 if tight else 1):
                for side, yy in (("above", stroke - 1 - d), ("below", stroke + d)):
                    if any(img.ink(x, yy) for x in range(b.x0, b.x1 + 1)):
                        near.append(f"{side} at {d}px")
            if near:
                note("tight edge", f"{b.id}: ink near the {name} edge ({', '.join(near)})")
        for name, x in (("left", x0), ("right", x1)):
            hits = sum(1 for y in range(y0, y1 + 1) if fine.ink(x, y))
            if hits:
                note("ink under a stroke", f"{b.id}: {name} edge (column {x} at res_dpi) runs "
                                           f"through {hits} ink pixels")

    items = [b for b in boxes if not b.is_part]
    parts = [b for b in boxes if b.is_part]

    # Ink outside its boxes: between the item's markers, every ink row lies in its boxes.
    for iid, (top, bottom) in sorted((bands or {}).items()):
        mine = [b for b in items if b.id == iid]
        sy = img.h / page_h if page_h else 0.0
        rows = []
        for y in range(math.ceil(top * sy), min(img.h - 1, math.floor(bottom * sy)) + 1):
            span = img.row_ink(y)
            if span and not any(b.y0 - 1 <= y <= b.y1 and b.x0 - 1 <= span[0]
                                and span[1] <= b.x1 for b in mine):
                rows.append(y)
        if rows:
            note("ink outside its boxes", f"{iid}: {len(rows)} rows of ink between y={rows[0]}px "
                                          f"and y={rows[-1]}px lie outside the item's boxes")

    # Cut words, stray item text, unboxed lines.
    unboxed: dict[tuple, list[dict]] = {}
    for wd in words:
        r = (wd["x0"], wd["y0"], wd["x1"], wd["y1"])
        in_item = None
        for b in items:
            f = b.frac_of(*r)
            if 0.25 < f < 0.75:
                note("cut word", f"{wd['text']!r} lies across the box of {b.id} ({f:.0%} inside)")
            if f >= 0.9:
                in_item = b
        for b in parts:
            f = b.frac_of(*r)
            if 0.25 < f < 0.75:
                note("cut word", f"{wd['text']!r} lies across the box of {b.id} ({f:.0%} inside)")
        if in_item is None:
            unboxed.setdefault(wd["line"], []).append(wd)
            continue
        mine = [b for b in parts if b.item == in_item.id]
        if mine and not any(b.frac_of(*r) > 0.25 for b in mine):
            cy = (wd["y0"] + wd["y1"]) / 2
            if cy > min(b.fy for b in mine):
                note("stray item text", f"{wd['text']!r} is in item {in_item.id} "
                                        "between or after its parts")
    if list_unboxed:
        for key in sorted(unboxed, key=lambda k: (unboxed[k][0]["y0"], unboxed[k][0]["x0"])):
            line = " ".join(w["text"] for w in unboxed[key])
            note("unboxed line", f"y={unboxed[key][0]['y0']:.3f}: {line[:110]}")

    # Frames apart.
    for a in items:
        for b in items:
            if a is b or b.y0 < a.y0:
                continue
            gap = b.y0 - a.y1
            if gap < frame_px:
                note("frames too close", f"{a.id} bottom and {b.id} top are {gap}px apart")
    for b in parts:
        above = [o for o in boxes if o.item == b.item and o is not b and o.y0 < b.y0]
        if not above:
            continue
        nearest = max(above, key=lambda o: o.y1 if o.is_part else o.y0)
        ref = nearest.y1 if nearest.is_part else nearest.y0
        gap = b.y0 - ref
        if 0 < gap < frame_px:
            note("frames too close", f"{b.id} top is {gap}px below the "
                                     f"{'bottom' if nearest.is_part else 'top'} of {nearest.id}")


def draw(img: Image, boxes: list[Box], page: int) -> Image:
    sheet = img.copy()
    for b in sorted(boxes, key=lambda b: b.is_part):
        rgb = PART_RGB if b.is_part else ITEM_RGB
        sheet.stroke(b, rgb)
        if b.is_part:
            sheet.text(b.x1 + 6, b.y0 + 2, b.id, rgb)
        else:
            sheet.text(b.x0 - 6 - text_width(b.id), b.y0 + 2, b.id, rgb)
    label = f"p{page}"
    sheet.text(sheet.w - 12 - text_width(label), 12, label, (0, 0, 0))
    return sheet


def svg(img: Image, boxes: list[Box], page: int) -> str:
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{img.w}" height="{img.h}" '
           f'viewBox="0 0 {img.w} {img.h}" font-family="monospace" font-size="12">',
           f'<image href="render/page-{page:02d}.png" x="0" y="0" '
           f'width="{img.w}" height="{img.h}"/>']
    for b in sorted(boxes, key=lambda b: b.is_part):
        col = "#%02x%02x%02x" % (PART_RGB if b.is_part else ITEM_RGB)
        out.append(f'<rect x="{b.x0}" y="{b.y0}" width="{b.x1 - b.x0}" height="{b.y1 - b.y0}" '
                   f'fill="none" stroke="{col}" stroke-width="2">'
                   f'<title>{"part" if b.is_part else "item"} {b.id}</title></rect>')
        if b.is_part:
            out.append(f'<text x="{b.x1 + 6}" y="{b.y0 + 12}" fill="{col}">{b.id}</text>')
        else:
            out.append(f'<text x="{b.x0 - 6}" y="{b.y0 + 12}" fill="{col}" '
                       f'text-anchor="end">{b.id}</text>')
    out.append(f'<text x="{img.w - 12}" y="24" text-anchor="end">p{page}</text>')
    out.append('<text x="12" y="24" fill="#555">red: item, blue: part</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def frame_px(frame_bp: float, dpi: int) -> int:
    """frame_bp in pixels at dpi, less one pixel for the rounding of two edges."""
    return max(1, int(frame_bp * dpi / 72) - 1)


def sheets(build: fc.Build, dest: Path, dpi: int, list_unboxed: bool = False,
           tight: bool = False) -> tuple[dict[str, list[str]], int, int]:
    """Write the sheets of a build and return (findings by kind, pages, boxes)."""
    build.need(build.pdf, build.aux)
    if not build.markers.is_file():
        raise fc.ToolError(f"{build.markers} not found; run extract_markers.py first")
    markers = fc.read_json(build.markers, "markers.json")
    if markers.get("pdf_sha256") != fc.sha256_file(build.pdf):
        raise fc.ToolError(f"{build.markers} is stale; rerun extract_markers.py")
    ink_level = int(build.config.boxes["ink_level"])
    res_dpi = int(build.config.boxes["res_dpi"])
    frame = frame_px(float(build.config.boxes["frame_bp"]), dpi)
    (dest / "render").mkdir(parents=True, exist_ok=True)
    words = words_of(build.pdf)
    sizes = fc.pdf_pages(build.pdf)
    bands = item_bands(fc.read_aux(build.aux), sizes)
    findings: dict[str, list[str]] = {}
    n_boxes = 0
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for page in range(1, markers["pages"] + 1):
            img = render(build.pdf, page, dpi, tmp, ink_level)
            fine = img if res_dpi == dpi else render(build.pdf, page, res_dpi, tmp, ink_level)
            boxes = [Box(e, b, img.w, img.h) for e in markers["entries"]
                     for b in e["boxes"] if b[0] == page]
            n_boxes += len(boxes)
            check_page(img, boxes, words.get(page, []), page, findings, list_unboxed, tight,
                       frame, bands.get(page, {}), sizes[page - 1][1], fine)
            (dest / "render" / f"page-{page:02d}.png").write_bytes(img.png())
            (dest / f"page-{page:02d}.png").write_bytes(draw(img, boxes, page).png())
            (dest / f"page-{page:02d}.svg").write_text(svg(img, boxes, page), encoding="utf-8")
    return findings, markers["pages"], n_boxes


KINDS = ["ink under a stroke", "cut word", "stray item text", "ink outside its boxes",
         "frames too close", "tight edge", "unboxed line"]
#: the kinds that fail the run
FATAL = KINDS[:4]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir", help="the build directory build_pdf.sh wrote")
    ap.add_argument("--dest", help="where to write the sheets (default OUTDIR/proof-sheets/)")
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--list-unboxed", action="store_true",
                    help="also list every line of text outside all item boxes")
    ap.add_argument("--tight", action="store_true",
                    help=f"also list horizontal edges with ink closer than {CLEAR_PX} pixels "
                         "to their stroke")
    args = ap.parse_args(argv)
    try:
        fc.require_programs("pdftoppm", "pdftotext", "pdfinfo")
        build = fc.Build(args.outdir)
        dest = Path(args.dest).resolve() if args.dest else build.outdir / "proof-sheets"
        findings, pages, n_boxes = sheets(build, dest, args.dpi, args.list_unboxed, args.tight)
    except fc.ToolError as exc:
        print(f"proof_sheets: {exc}", file=sys.stderr)
        return 2
    for kind in KINDS:
        for line in findings.get(kind, []):
            print(f"{kind.upper()}: {line}")
    counts = ", ".join(f"{len(findings.get(k, []))} {k}" for k in KINDS[:5])
    print(f"proof_sheets: {pages} pages, {n_boxes} boxes drawn -> {dest}; {counts}")
    return 1 if any(findings.get(k) for k in FATAL) else 0


if __name__ == "__main__":
    sys.exit(main())
