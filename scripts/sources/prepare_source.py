"""pdftotext -> text.txt + pages/ + meta.json + items.json, for ONE source.

Ported from: recovered/b174aa13_L5589_Bash.txt (``pdftotext -layout`` + the
``\\f`` page count), recovered/b174aa13_L6099_Bash.txt (the Definition heading
regex) and recovered/b174aa13_L6145_Bash.txt (the numbered-item regex over the
eight Chapter-1 kinds).  Generalised per design/stage_map.md Stage 1:

* the heading regex is whitespace-tolerant -- the originals used a literal
  single space and lost ``Exercise    1.3.19`` (410 items instead of 411);
* the printed<->PDF page offset and the kind list are INFERRED from the text
  when the caller does not know them (docs/design/multi-source-model.md S5), not
  the hard-coded +16 / Tao list, and never typed by a user;
* every item records where it was found (pdf page, printed page, line), so the
  mechanical anchor check in pair/anchor_check.py has something to check
  against, and every downstream artifact can carry ``source_id``/``source_sha``.

Run: ``cf onboard <repo>`` (which calls this), or ``cf sources refresh <id>``.
"""

from __future__ import annotations

import bisect
import os
import re
import shutil
import subprocess
from typing import Iterable, Sequence

from .compat import (
    item_sort_key,
    normalise_item,
    read_json,
    sha256_file,
    sha256_text,
    utcnow,
    write_json,
)

#: kinds whose headings start a numbered item.  A LAST RESORT only: the kinds of
#: a source are inferred from its own headings (cflib.inference.infer_kinds) and
#: recorded in ``sources.<id>.kinds``; this list is what a text too short to
#: infer from falls back to.
SOURCE_KINDS: tuple[str, ...] = (
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


def heading_regex(kinds: Sequence[str] = SOURCE_KINDS, parts: int = 3) -> re.Pattern:
    """The whitespace-tolerant numbered-heading regex.

    ``parts=3`` means ``1.2.3`` (Tao's shared per-section counter); ``parts=2``
    also accepts ``1.2`` for books that number per chapter.
    """
    alt = "|".join(re.escape(k) for k in kinds)
    num = r"\d+(?:\.\d+)" + ("{1,2}" if parts >= 3 else "")
    # `\f` as well as `\n`: pdftotext puts the form feed BETWEEN the pages, so an
    # item that begins a page has `\f` and not a newline immediately before it and
    # was invisible to the index -- a heading nothing can pair, nothing can put in
    # a task universe and nothing reports as missing. Tao's text happens to start
    # no item that way (442 labels either way), so this changes no pinned count.
    return re.compile(
        r"(?:^|[\n\f])[ \t]*(?P<label>(?P<kind>" + alt + r")[ \t]+(?P<num>" + num + r"))"
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
    """Byte offset in ``text.txt`` at which each page begins."""
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
    kinds: Sequence[str] = SOURCE_KINDS,
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
    source_id: str,
    offset: int | None = None,
    kinds: Sequence[str] | None = None,
    edition: str = "",
    parts: int = 3,
    expect_sha: str = "",
    force: bool = False,
    write_pages: bool = True,
    infer: bool = True,
) -> dict:
    """Prepare one source directory; returns the ``meta.json`` payload.

    ``offset=None`` and ``kinds=None`` mean INFER (docs/design/multi-source-model.md
    S5): the offset is the mode of ``pdf_page - running_head`` and the kinds are
    the capitalised words the source's own numbered headings use.  The returned
    payload carries ``offset_confidence``, ``offset_method`` and ``kind_counts``
    so the caller can print what it inferred and refuse a weak inference --
    below 0.9 the caller asks rather than guesses, because an offset wrong by one
    is exactly the defect this audit exists to catch.

    Idempotent: a second run with the same PDF (sha), offset and kind list re-uses
    the existing outputs and reports ``skipped``.  ``expect_sha`` is the SOURCE
    GATE -- workspace.yaml pins the sha and a different PDF is refused rather than
    silently re-pinned (the batch-1 anchor findings were made against the 2011
    draft).
    """
    pdf = os.path.abspath(pdf)
    if not os.path.exists(pdf):
        raise FileNotFoundError("no such PDF: %s" % pdf)
    sha = sha256_file(pdf)
    if expect_sha and sha != expect_sha:
        raise ValueError(
            "source gate: %s has sha %s but workspace.yaml pins %s"
            % (pdf, sha[:12], expect_sha[:12])
        )
    os.makedirs(out_dir, exist_ok=True)
    meta_json = os.path.join(out_dir, "meta.json")
    txt_path = os.path.join(out_dir, "text.txt")
    items_path = os.path.join(out_dir, "items.json")
    previous = read_json(meta_json, default=None)
    # A cached run is only equivalent if it reached the offset the SAME WAY.
    # `prepare` writes meta.json before its caller decides whether to accept the
    # confidence, so a weak inference ("offset 1, 0.67") is on disk even when the
    # caller refused it.  Keying reuse on the value alone then made a human's
    # `cf sources set <id> offset=1` unreachable: the declaration agrees with the
    # guess it was meant to replace, the cached payload comes back with the
    # guess's 0.67 confidence, and the caller refuses it again.  A DECLARED offset
    # therefore never reuses an INFERRED one.
    declared_over_guess = (
        offset is not None
        and str(previous.get("offset_method") or "") not in ("declared", "manual")
        if previous else False
    )
    unchanged = (
        previous
        and not force
        and previous.get("sha256") == sha
        and (offset is None or previous.get("offset") == offset)
        and not declared_over_guess
        and (kinds is None or list(previous.get("kinds") or []) == list(kinds))
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

    warnings: list[str] = []
    kind_counts: dict[str, int] = {}
    structural_kinds: dict[str, int] = {}
    offset_confidence = 1.0
    offset_method = "declared" if offset is not None else "manual"
    histogram: dict = {}
    voting_pages = 0

    if infer:
        guess = _infer_offset(pages, declared=offset)
        if guess is not None:
            if offset is None:
                offset = guess.offset
                offset_method = "running_head_mode"
            else:
                offset_method = "declared"
            offset_confidence = guess.confidence
            histogram = {str(k): v for k, v in sorted(guess.histogram.items(),
                                                      key=lambda kv: -kv[1])[:6]}
            voting_pages = guess.voting_pages
        inferred_kinds, structural_kinds = _infer_kinds(text)
        kind_counts = inferred_kinds
        if kinds is None:
            kinds = tuple(inferred_kinds) or SOURCE_KINDS
            if not inferred_kinds:
                warnings.append("kinds_not_inferred")
        else:
            missing = [k for k in kinds if k not in inferred_kinds]
            if missing:
                warnings.append("kinds_not_in_text: " + ", ".join(missing))
    if offset is None:
        offset = 0
        offset_confidence = 0.0
    if kinds is None:
        kinds = SOURCE_KINDS

    items = find_items(text, kinds, offset=offset, parts=parts)
    by_kind: dict[str, int] = {}
    by_chapter: dict[str, int] = {}
    for row in items:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        by_chapter[str(row["chapter"])] = by_chapter.get(str(row["chapter"]), 0) + 1
    write_json(
        items_path,
        {
            "source_id": source_id,
            "source_sha": sha,
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
        "source_id": source_id,
        "sha256": sha,
        "edition": edition or os.path.basename(pdf),
        "source_pdf": pdf,
        "pages": len(pages),
        "offset": offset,
        "offset_confidence": round(float(offset_confidence), 4),
        "offset_method": offset_method,
        "offset_voting_pages": voting_pages,
        "offset_histogram": histogram,
        "printed_page_rule": "printed = pdf_page - %d" % offset,
        "kinds": list(kinds),
        "kind_counts": kind_counts,
        "structural_kinds": structural_kinds,
        "kind_warnings": [w for w in warnings if w.startswith("kinds")],
        "item_number_parts": parts,
        "text_sha256": sha256_text(text),
        "text_bytes": len(text.encode("utf-8")),
        "items": len(items),
        "page_files": "pdf" if write_pages else "none",
        "warnings": warnings,
        "created": utcnow(),
        "tool": "pdftotext -layout",
    }
    write_json(meta_json, payload)
    payload["skipped"] = False
    return payload


def _infer_offset(pages, declared=None):
    """cflib.inference.infer_offset when cflib is importable (it always is here)."""
    try:
        from cflib.inference import infer_offset
    except Exception:  # pragma: no cover - stand-alone use without cflib
        return None
    return infer_offset(pages, declared=declared)


def _infer_kinds(text):
    try:
        from cflib.inference import infer_kinds
    except Exception:  # pragma: no cover
        return {}, {}
    return infer_kinds(text)


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


def load_items(source_dir: str) -> dict:
    """Read ``items.json`` from a prepared source directory."""
    path = os.path.join(source_dir, "items.json")
    data = read_json(path, default=None)
    if data is None:
        raise FileNotFoundError(
            "no items.json in %s (run `cf sources refresh <id>`)" % source_dir)
    return data


def load_meta(source_dir: str) -> dict:
    """Read ``meta.json`` from a prepared source directory."""
    path = os.path.join(source_dir, "meta.json")
    data = read_json(path, default=None)
    if data is None:
        raise FileNotFoundError(
            "no meta.json in %s (run `cf sources refresh <id>`)" % source_dir)
    return data


def items_index(items_json: dict) -> dict[str, dict]:
    """``{item: row}`` for O(1) lookup by pair/ and plan/ stages."""
    return {row["item"]: row for row in items_json.get("items", [])}
