"""pdftotext -> book.txt + pages/ + book.json + items.json.

Ported from: recovered/b174aa13_L5589_Bash.txt (``pdftotext -layout`` + the
``\\f`` page count), recovered/b174aa13_L6099_Bash.txt (the Definition heading
regex) and recovered/b174aa13_L6145_Bash.txt (the numbered-item regex over the
eight Chapter-1 kinds).  Generalised per design/stage_map.md Stage 1:

* the heading regex is whitespace-tolerant -- the originals used a literal
  single space and lost ``Exercise    1.3.19`` (410 items instead of 411);
* the printed<->PDF page offset and the kind list are parameters, not the
  hard-coded +16 / Tao list;
* every item records where it was found (pdf page, printed page, line), so the
  mechanical anchor check in pair/anchor_check.py has something to check
  against, and every downstream artifact can carry ``book_sha``.

Run: ``python3 scripts/cf.py book <id> --pdf <path> --offset 16``.
"""

from __future__ import annotations

import bisect
import os
import re
import shutil
import subprocess
from typing import Iterable, Sequence

from .compat import (
    DEFAULT_KINDS,
    item_sort_key,
    normalise_item,
    read_json,
    sha256_file,
    sha256_text,
    utcnow,
    write_json,
)

#: kinds whose headings start a numbered book item.  Overridden by campaign.yaml
#: (``book.kinds``); the Tao Chapter-1 universe is the first eight.
BOOK_KINDS: tuple[str, ...] = (
    "Definition",
    "Lemma",
    "Theorem",
    "Proposition",
    "Corollary",
    "Exercise",
    "Example",
    "Remark",
)

#: a sub-part immediately after the number: ``Exercise 1.4.29(iii).``
_SUB_RE = r"(?:\((?P<sub>[ivxlcdm]{1,6}|\d{1,3}|[a-z])\))?"
#: a parenthetical title on the same line: ``Definition 1.1.1 (Piecewise ...)``
_TITLE_RE = re.compile(r"^[ \t]*\(([^)\n]{1,160})\)")


def heading_regex(kinds: Sequence[str] = BOOK_KINDS, parts: int = 3) -> re.Pattern:
    """The whitespace-tolerant numbered-heading regex.

    ``parts=3`` means ``1.2.3`` (Tao's shared per-section counter); ``parts=2``
    also accepts ``1.2`` for books that number per chapter.
    """
    alt = "|".join(re.escape(k) for k in kinds)
    num = r"\d+(?:\.\d+)" + ("{1,2}" if parts >= 3 else "")
    return re.compile(
        r"(?:^|\n)[ \t]*(?P<label>(?P<kind>" + alt + r")[ \t]+(?P<num>" + num + r"))"
        + _SUB_RE
        + r"(?=[ \t.,:;()]|$)"
    )


def split_pages(text: str) -> list[str]:
    """Split pdftotext output on form feeds (one entry per PDF page)."""
    pages = text.split("\f")
    if pages and pages[-1] == "":
        pages.pop()
    return pages


def _page_starts(pages: Iterable[str]) -> list[int]:
    """Byte offset in ``book.txt`` at which each page begins."""
    starts, off = [], 0
    for page in pages:
        starts.append(off)
        off += len(page) + 1  # + the \f that terminates it
    return starts


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    return starts


def find_items(
    text: str,
    kinds: Sequence[str] = BOOK_KINDS,
    *,
    offset: int = 0,
    parts: int = 3,
) -> list[dict]:
    """Every numbered heading in ``text``, one row per item.

    A label can occur at line start more than once (a cross-reference wrapped
    onto a new line, a running footer).  Occurrences are all recorded; the row's
    page is chosen by :func:`_choose_occurrence`, which prefers the occurrence
    inside the page span of the item's own section -- the "first hit" rule puts
    ``Exercise 1.4.29`` on PDF page 77 (a footer cross-reference in S1.3)
    instead of 111.
    """
    pages = split_pages(text)
    starts = _page_starts(pages)
    lines = _line_starts(text)
    seen: dict[str, dict] = {}
    for m in heading_regex(kinds, parts).finditer(text):
        label = normalise_item(m.group("label"))
        pos = m.start("label")
        pdf_page = bisect.bisect_right(starts, pos)  # 1-based
        line = bisect.bisect_right(lines, pos)  # 1-based
        occ = {
            "pdf_page": pdf_page,
            "page": pdf_page - offset,
            "line": line,
            "sub": "(%s)" % m.group("sub").lower() if m.group("sub") else "",
            "title": _title_at(text, m.end()),
        }
        row = seen.setdefault(
            label,
            {
                "item": label,
                "kind": m.group("kind"),
                "number": m.group("num"),
                "occurrences": [],
            },
        )
        row["occurrences"].append(occ)
    items = []
    for label, row in seen.items():
        nums = tuple(int(x) for x in row["number"].split("."))
        row["chapter"] = nums[0]
        row["section"] = nums[1] if len(nums) > 1 else None
        row["index"] = nums[2] if len(nums) > 2 else None
        items.append(row)
    items.sort(key=lambda r: item_sort_key(r["item"]))
    _resolve_occurrences(items)
    return items


def _title_at(text: str, pos: int) -> str:
    """The parenthetical title that follows a heading, if any (same line)."""
    tail = text[pos : pos + 200].split("\n", 1)[0]
    m = _TITLE_RE.match(tail)
    return m.group(1).strip() if m else ""


def _resolve_occurrences(items: list[dict]) -> None:
    """Pick one page/line per item and flag the ambiguous ones.

    Items with a single line-start occurrence fix the page span of their
    (chapter, section); an ambiguous item then takes the earliest occurrence
    inside that span, falling back to the earliest occurrence overall.
    """
    span: dict[tuple, list[int]] = {}
    for row in items:
        if len(row["occurrences"]) == 1:
            key = (row["chapter"], row["section"])
            span.setdefault(key, []).append(row["occurrences"][0]["pdf_page"])
    bounds = {k: (min(v), max(v)) for k, v in span.items()}
    for row in items:
        occ = sorted(row["occurrences"], key=lambda o: o["pdf_page"])
        chosen = occ[0]
        if len(occ) > 1:
            lo, hi = bounds.get((row["chapter"], row["section"]), (None, None))
            if lo is not None:
                inside = [o for o in occ if lo <= o["pdf_page"] <= hi]
                if inside:
                    chosen = inside[0]
        row["pdf_page"] = chosen["pdf_page"]
        row["page"] = chosen["page"]
        row["line"] = chosen["line"]
        row["sub"] = chosen["sub"]
        row["title"] = chosen["title"] or next((o["title"] for o in occ if o["title"]), "")
        row["ambiguous"] = len(occ) > 1
        row["occurrences"] = [
            {"pdf_page": o["pdf_page"], "page": o["page"], "line": o["line"]} for o in occ
        ]


def pdftotext(pdf: str, out_txt: str, *, layout: bool = True) -> str:
    """Run ``pdftotext -layout`` and return the extracted text."""
    exe = shutil.which("pdftotext")
    if exe is None:
        raise RuntimeError("pdftotext not found (brew install poppler)")
    cmd = [exe] + (["-layout"] if layout else []) + [pdf, out_txt]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(out_txt):
        raise RuntimeError("pdftotext failed (%s): %s" % (proc.returncode, proc.stderr.strip()))
    with open(out_txt, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.read()


def prepare(
    pdf: str,
    out_dir: str,
    *,
    book_id: str,
    offset: int = 0,
    kinds: Sequence[str] = BOOK_KINDS,
    edition: str = "",
    parts: int = 3,
    expect_sha: str = "",
    force: bool = False,
    write_pages: bool = True,
) -> dict:
    """Prepare one book directory; returns the ``book.json`` payload.

    Idempotent: a second run with the same PDF (sha), offset and kind list
    re-uses the existing outputs and reports ``skipped``.  ``expect_sha`` is the
    edition gate -- campaign.yaml pins the sha and a different PDF is refused
    rather than silently re-pinned (the batch-1 anchor findings were made
    against the 2011 draft).
    """
    pdf = os.path.abspath(pdf)
    if not os.path.exists(pdf):
        raise FileNotFoundError("no such PDF: %s" % pdf)
    sha = sha256_file(pdf)
    if expect_sha and sha != expect_sha:
        raise ValueError(
            "edition gate: %s has sha %s but campaign.yaml pins %s" % (pdf, sha[:12], expect_sha[:12])
        )
    os.makedirs(out_dir, exist_ok=True)
    book_json = os.path.join(out_dir, "book.json")
    txt_path = os.path.join(out_dir, "book.txt")
    items_path = os.path.join(out_dir, "items.json")
    previous = read_json(book_json, default=None)
    unchanged = (
        previous
        and not force
        and previous.get("sha256") == sha
        and previous.get("offset") == offset
        and list(previous.get("kinds") or []) == list(kinds)
        and os.path.exists(txt_path)
        and os.path.exists(items_path)
    )
    if unchanged:
        payload = dict(previous)
        payload["skipped"] = True
        return payload

    text = pdftotext(pdf, txt_path)
    pages = split_pages(text)
    if write_pages:
        _write_pages(out_dir, pages)
    items = find_items(text, kinds, offset=offset, parts=parts)
    by_kind: dict[str, int] = {}
    by_chapter: dict[str, int] = {}
    for row in items:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        by_chapter[str(row["chapter"])] = by_chapter.get(str(row["chapter"]), 0) + 1
    write_json(
        items_path,
        {
            "book_id": book_id,
            "book_sha": sha,
            "offset": offset,
            "kinds": list(kinds),
            "count": len(items),
            "by_kind": by_kind,
            "by_chapter": by_chapter,
            "ambiguous": [r["item"] for r in items if r.get("ambiguous")],
            "created": utcnow(),
            "items": items,
        },
    )
    payload = {
        "book_id": book_id,
        "sha256": sha,
        "edition": edition or os.path.basename(pdf),
        "source_pdf": pdf,
        "pages": len(pages),
        "offset": offset,
        "printed_page_rule": "printed = pdf_page - %d" % offset,
        "kinds": list(kinds),
        "item_number_parts": parts,
        "text_sha256": sha256_text(text),
        "text_bytes": len(text.encode("utf-8")),
        "items": len(items),
        "page_files": "pdf" if write_pages else "none",
        "created": utcnow(),
        "tool": "pdftotext -layout",
    }
    write_json(book_json, payload)
    payload["skipped"] = False
    return payload


def _write_pages(out_dir: str, pages: Sequence[str]) -> None:
    """Write ``pages/pNNN.txt``, one file per PDF page (never committed)."""
    pdir = os.path.join(out_dir, "pages")
    os.makedirs(pdir, exist_ok=True)
    keep = set()
    for i, page in enumerate(pages, 1):
        name = "p%03d.txt" % i
        keep.add(name)
        path = os.path.join(pdir, name)
        body = page.lstrip("\n")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                if fh.read() == body:
                    continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    for name in os.listdir(pdir):  # a shorter PDF must not leave stale pages
        if name.endswith(".txt") and name not in keep:
            os.remove(os.path.join(pdir, name))


def load_items(book_dir: str) -> dict:
    """Read ``items.json`` from a prepared book directory."""
    path = os.path.join(book_dir, "items.json")
    data = read_json(path, default=None)
    if data is None:
        raise FileNotFoundError("no items.json in %s (run `cf book <id> --pdf ...`)" % book_dir)
    return data


def items_index(items_json: dict) -> dict[str, dict]:
    """``{item: row}`` for O(1) lookup by pair/ and plan/ stages."""
    return {row["item"]: row for row in items_json.get("items", [])}
