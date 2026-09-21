"""source text -> text.txt + pages/ + meta.json + items.json, for ONE source.

Ported from: the v1 campaign's three source-preparation cells (``pdftotext
-layout`` + the ``\\f`` page count; the Definition heading regex; the
numbered-item regex over the eight Chapter-1 kinds), none of them in this repo.
Generalised per docs/design/stage_map.md Stage 1:

* the heading regex is whitespace-tolerant -- the originals used a literal
  single space and lost ``Exercise    1.3.19`` (410 items instead of 411);
* the printed<->PDF page offset and the kind list are INFERRED from the text
  when the caller does not know them (docs/design/archive/multi-source-model.md S5), not
  the hard-coded +16 / Tao list, and never typed by a user;
* every item records where it was found (pdf page, printed page, line), so the
  mechanical anchor check in pair/anchor_check.py has something to check
  against, and every downstream artifact can carry ``source_id``/``source_sha``;
* the NUMBERING is inferred too (``1.2.3`` / ``3.4`` / ``7`` / ``2.3.4.1`` are
  four real book conventions), and a label deeper than it is reported as a
  reject rather than truncated onto a shorter item's key.  ``cf sources set
  <id> numbering=<min>,<max>`` is its override, stamped ``method: manual``:
  re-running the inference reads the same headings and reaches the same depth,
  so a book whose deeper labels are too few to be inferred from needs a human;
* a source that is already text (``.tex``, ``.md``, ``.html``, a ``pdftotext``
  dump) skips poppler entirely; when it has no page breaks it is prepared as an
  unpaginated source, located by section and line (``sources/locator.py``).

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
from .locator import (
    FORMAT_PDF,
    LOCATOR_PAGE,
    LOCATOR_SECTION,
    PAGINATION_FORMFEED,
    PAGINATION_NONE,
    detect_pagination,
    heading_paths,
    locate,
    source_format,
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

#: what a source whose numbering was not inferred falls back to: ``1.2`` or
#: ``1.2.3``, the grammar every existing corpus was keyed with
DEFAULT_SEGMENTS: tuple[int, int] = (2, 3)


def segments_for(parts: int = 3, segments=None) -> tuple[int, int]:
    """``(min, max)`` numeric segments, from the source's numbering or ``parts``.

    ``segments`` is what ``cflib.inference.infer_numbering`` found (also carried
    in ``items.json``/``meta.json`` as ``numbering``); ``parts`` is the older
    spelling and still means what it meant -- 3 is ``1.2`` or ``1.2.3``, 2 is
    ``1.2`` only.

    An EMPTY mapping means "nothing was inferred", which is what ``meta.json``
    stores on a host with no ``cflib`` -- and that is exactly the run that used
    :data:`DEFAULT_SEGMENTS`.  Reading it back as ``(2, 2)`` would say the
    source is numbered one level shallower than the table on disk was built
    with.  A value that cannot be read as a number falls back the same way,
    because this reads a number a human may have hand-edited.
    """
    if not segments:
        return DEFAULT_SEGMENTS if parts >= 3 else (2, 2)
    try:
        if isinstance(segments, dict):
            low = int(segments.get("min_segments") or DEFAULT_SEGMENTS[0])
            high = int(segments.get("max_segments") or low)
        elif isinstance(segments, int):
            low = high = segments
        else:
            seq = tuple(segments)
            low = int(seq[0])
            high = int(seq[1]) if len(seq) > 1 else low
        low = max(1, min(low, 4))
        high = max(low, min(high, 4))
    except (TypeError, ValueError):
        return DEFAULT_SEGMENTS if parts >= 3 else (2, 2)
    return low, high


def heading_regex(kinds: Sequence[str] = SOURCE_KINDS, parts: int = 3,
                  *, segments=None) -> re.Pattern:
    """The whitespace-tolerant numbered-heading regex for ONE numbering.

    ``segments=(min, max)`` is the source's own numbering: ``(2, 3)`` is Tao's
    ``1.2.3``, ``(1, 1)`` a book numbering ``Theorem 7``, ``(4, 4)`` one
    numbering ``Lemma 2.3.4.1``.  ``parts`` is the older spelling and still
    works.

    The ``deep`` group is the point of the whole thing: it matches the segments
    a label carries BEYOND the numbering, so a deeper label is caught and
    reported instead of being matched down to its first three segments.  Without
    it ``Theorem 2.3.4.5`` still matched -- as ``Theorem 2.3.4`` -- and every
    item of that section collapsed onto one key with no reject anywhere.

    The KIND word matches case-insensitively, because AMS-style books set their
    heads ``THEOREM 1.2``.  ``cflib.inference.infer_kinds`` already folds case,
    so such a book infers ``Theorem`` and then matched nothing here: 19 real
    headings, 0 rows, and a refusal that listed ``Theorem`` among the kinds as
    though it were present.  The matched word is canonicalised against this
    source's own kind list at the match site (:func:`find_headings`), so the id
    stays ``Theorem 1.2.1`` and the frozen normaliser is not involved.
    """
    low, high = segments_for(parts, segments)
    alt = "(?i:" + "|".join(re.escape(k) for k in kinds) + ")"
    if high <= 1:
        num = r"\d+"
    else:
        num = r"\d+(?:\.\d+){%d,%d}" % (low - 1, high - 1)
    # `\f` as well as `\n`: pdftotext puts the form feed BETWEEN the pages, so an
    # item that begins a page has `\f` and not a newline immediately before it and
    # was invisible to the index -- a heading nothing can pair, nothing can put in
    # a task universe and nothing reports as missing. Tao's text happens to start
    # no item that way (442 labels either way), so this changes no pinned count.
    return re.compile(
        r"(?:^|[\n\f])[ \t]*(?P<label>(?P<kind>" + alt + r")[ \t]+(?P<num>" + num + r"))"
        + r"(?P<deep>(?:\.\d+)+)?"
        + _SUB_RE
        + r"(?=[ \t.,:;()]|$)"
    )


def canonical_kind(word: str, kinds: Sequence[str] = SOURCE_KINDS) -> str:
    """The spelling of ``word`` this source declares (``THEOREM`` -> ``Theorem``).

    The heading regex matches the kind word case-insensitively, so the matched
    text may be in any case the typesetter chose; the id must not be.  The kind
    list is the authority -- it is what ``infer_kinds`` read off this very text,
    already folded onto one spelling per word -- so no re-casing rule is
    invented here and a kind that is genuinely mixed-case (``McShane``) keeps
    its capitals.  A word that matches no declared kind is returned as written,
    which cannot happen through :func:`heading_regex` and is the honest answer
    for a hand-built call.
    """
    low = str(word or "").lower()
    for kind in kinds or ():
        if str(kind).lower() == low:
            return str(kind)
    return str(word or "")


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
    segments=None,
    pagination: str = PAGINATION_FORMFEED,
    suffix: str = "",
) -> list[dict]:
    """Every numbered heading in ``text``, one row per item (see :func:`find_headings`)."""
    return find_headings(text, kinds, offset=offset, parts=parts, segments=segments,
                         pagination=pagination, suffix=suffix)[0]


def find_headings(
    text: str,
    kinds: Sequence[str] = SOURCE_KINDS,
    *,
    offset: int = 0,
    parts: int = 3,
    segments=None,
    pagination: str = PAGINATION_FORMFEED,
    suffix: str = "",
) -> tuple[list[dict], list[dict]]:
    """``(rows, rejects)``: every numbered heading, and every one refused.

    A label can occur at line start more than once (a cross-reference wrapped
    onto a new line, a running footer).  Occurrences are all recorded; the row's
    page is chosen by :func:`_resolve_occurrences`, which prefers the occurrence
    inside the page span of the item's own section -- the "first hit" rule puts
    ``Exercise 1.4.29`` on PDF page 77 (a footer cross-reference in S1.3)
    instead of 111.

    A heading whose number is DEEPER than the source's numbering goes on the
    reject list (``too_many_segments``) and becomes no row at all: the caller
    reports it, exactly as the item grammar's reject list is reported, because
    the alternative -- matching its first segments -- merges distinct book items
    onto one audit key.

    When ``pagination`` is ``none`` the source has no pages: ``pdf_page`` and
    ``page`` are ``None``, and each row carries the heading path it sits under
    (``locator``) plus its line.  Every page-dependent stage must ask
    ``locator.require_pagination`` before using a page from such a source.
    """
    paginated = pagination != PAGINATION_NONE
    pages = split_pages(text)
    starts = _page_starts(pages)
    lines = _line_starts(text)
    anchors = [] if paginated else heading_paths(text, suffix)
    seen: dict[str, dict] = {}
    rejects: list[dict] = []
    high = segments_for(parts, segments)[1]
    for m in heading_regex(kinds, parts, segments=segments).finditer(text):
        pos = m.start("label")
        line = bisect.bisect_right(lines, pos)  # 1-based
        # the kind word is matched in whatever case the book sets it in; the id
        # carries the source's own spelling, never the typesetter's.
        kind = canonical_kind(m.group("kind"), kinds)
        if m.group("deep"):
            rejects.append({
                "raw": normalise_item(kind + " " + m.group("num") + m.group("deep")),
                "reason": "too_many_segments",
                "kind": kind,
                "number": m.group("num") + m.group("deep"),
                "max_segments": high,
                "line": line,
            })
            continue
        label = normalise_item(kind + " " + m.group("num"))
        pdf_page = bisect.bisect_right(starts, pos) if paginated else None
        occ = {
            "pdf_page": pdf_page,
            "page": (pdf_page - offset) if paginated else None,
            "line": line,
            "sub": "(%s)" % m.group("sub").lower() if m.group("sub") else "",
            "title": _title_at(text, m.end()),
        }
        if not paginated:
            occ["locator"] = locate(anchors, pos)
        row = seen.setdefault(
            label,
            {
                "item": label,
                "kind": kind,
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
        if len(nums) > 3:
            row["levels"] = list(nums)
        items.append(row)
    items.sort(key=lambda r: item_sort_key(r["item"]))
    _resolve_occurrences(items, paginated=paginated)
    return items, rejects


def _title_at(text: str, pos: int) -> str:
    """The parenthetical title that follows a heading, if any (same line)."""
    tail = text[pos : pos + 200].split("\n", 1)[0]
    m = _TITLE_RE.match(tail)
    return m.group(1).strip() if m else ""


def _resolve_occurrences(items: list[dict], *, paginated: bool = True) -> None:
    """Pick one page/line per item and flag the ambiguous ones.

    Items with a single line-start occurrence fix the page span of their
    (chapter, section); an ambiguous item then takes the earliest occurrence
    inside that span, falling back to the earliest occurrence overall.

    An unpaginated source has no page to span, so the same rule runs on the LINE
    number instead -- the ordering it needs is "further into the text", which a
    line gives just as well as a page.
    """
    where = "pdf_page" if paginated else "line"
    span: dict[tuple, list[int]] = {}
    for row in items:
        if len(row["occurrences"]) == 1:
            key = (row["chapter"], row["section"])
            span.setdefault(key, []).append(row["occurrences"][0][where])
    bounds = {k: (min(v), max(v)) for k, v in span.items()}
    for row in items:
        occ = sorted(row["occurrences"], key=lambda o: o[where])
        chosen = occ[0]
        if len(occ) > 1:
            lo, hi = bounds.get((row["chapter"], row["section"]), (None, None))
            if lo is not None:
                inside = [o for o in occ if lo <= o[where] <= hi]
                if inside:
                    chosen = inside[0]
        row["pdf_page"] = chosen["pdf_page"]
        row["page"] = chosen["page"]
        row["line"] = chosen["line"]
        row["sub"] = chosen["sub"]
        row["title"] = chosen["title"] or next((o["title"] for o in occ if o["title"]), "")
        row["ambiguous"] = len(occ) > 1
        keep = ["pdf_page", "page", "line"] + ([] if paginated else ["locator"])
        if not paginated:
            row["locator"] = chosen.get("locator", "")
        row["occurrences"] = [{k: o.get(k) for k in keep} for o in occ]


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


def read_source_text(path: str, out_txt: str) -> str:
    """The source's text, whatever the source is: PDF through poppler, else read.

    A manuscript that is already text is COPIED to ``text.txt`` rather than
    converted, so ``text_sha256`` still fingerprints exactly the bytes every
    later stage quotes from -- and a source with no text layer and no poppler
    fails here, where the message can say so, instead of producing an empty
    table two stages later.
    """
    if source_format(path) == FORMAT_PDF:
        return pdftotext(path, out_txt)
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    os.makedirs(os.path.dirname(os.path.abspath(out_txt)) or ".", exist_ok=True)
    with open(out_txt, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def prepare(
    pdf: str,
    out_dir: str,
    *,
    source_id: str,
    offset: int | None = None,
    declared: int | None = None,
    kinds: Sequence[str] | None = None,
    edition: str = "",
    parts: int = 3,
    segments=None,
    expect_sha: str = "",
    force: bool = False,
    write_pages: bool = True,
    infer: bool = True,
) -> dict:
    """Prepare one source directory; returns the ``meta.json`` payload.

    ``offset=None`` and ``kinds=None`` mean INFER (docs/design/archive/multi-source-model.md
    S5): the offset is the mode of ``pdf_page - running_head`` and the kinds are
    the capitalised words the source's own numbered headings use.

    ``segments`` is the NUMBERING override (a human's ``cf sources set <id>
    numbering=3,4``), and it takes precedence over the inference for the same
    reason the offset override does: a book whose four-level labels are too few
    to clear ``MIN_DEPTH_SHARE`` infers a three-level numbering and puts its
    real items on the reject list, and re-running the inference cannot settle
    that -- only a human who has the book can.  It is stored as ``numbering``
    with ``method: "manual"``, so a depth somebody typed is never later read as
    one the headings supported.

    ``offset`` is an OVERRIDE (a human's ``cf sources set offset=…``); ``declared``
    is the number the companion repo's own prose claims, and it is a CROSS-CHECK,
    never the answer: the running heads still decide, agreement raises the
    confidence floor, and a contradiction is refused with both numbers quoted.
    That is why an agreeing declaration leaves ``offset_method`` at
    ``running_head_mode`` -- the text is what supports the number, and saying
    "declared" would credit the repo for an inference the pages carried.  The returned
    payload carries ``offset_confidence``, ``offset_method`` and ``kind_counts``
    so the caller can print what it inferred and refuse a weak inference --
    below 0.9 the caller asks rather than guesses, because an offset wrong by one
    is exactly the defect this audit exists to catch.

    ``pdf`` may also be a manuscript that is already text (``.txt``, ``.md``,
    ``.tex``, ``.html``): poppler is skipped, and a text with no ``\\f`` page
    breaks is prepared as an UNPAGINATED source -- no offset inference, items
    located by heading path and line, ``pagination: none`` in the payload.  The
    page-dependent stages refuse on that flag rather than compute a page nothing
    supports (``sources/locator.py``).

    Idempotent: a second run with the same PDF (sha), offset, kind list and
    numbering re-uses the existing outputs and reports ``skipped``.  ``expect_sha`` is the SOURCE
    GATE -- workspace.yaml pins the sha and a different PDF is refused rather than
    silently re-pinned (the batch-1 anchor findings were made against the 2011
    draft).
    """
    pdf = os.path.abspath(pdf)
    if not os.path.exists(pdf):
        raise FileNotFoundError("no such source file: %s" % pdf)
    fmt = source_format(pdf)
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
    # the same trap one field over: a numbering somebody just typed must not be
    # answered with the cached table the inference built without it.
    numbering_over_guess = (
        segments is not None
        and segments_for(parts, previous.get("numbering")) != segments_for(parts, segments)
        if previous else False
    )
    unchanged = (
        previous
        and not force
        and previous.get("sha256") == sha
        and (offset is None or previous.get("offset") == offset)
        and not declared_over_guess
        and not numbering_over_guess
        # a cached run that never saw this declaration never ran the cross-check
        and previous.get("offset_declared") == declared
        and (kinds is None or list(previous.get("kinds") or []) == list(kinds))
        and os.path.exists(txt_path)
        and os.path.exists(items_path)
    )
    if unchanged:
        payload = dict(previous)
        payload["skipped"] = True
        return payload

    text = read_source_text(pdf, txt_path)
    pages = split_pages(text)
    pagination = PAGINATION_FORMFEED if fmt == FORMAT_PDF else detect_pagination(text)
    paginated = pagination != PAGINATION_NONE
    suffix = os.path.splitext(pdf)[1].lower()
    if write_pages and paginated:
        _write_pages(out_dir, pages)

    warnings: list[str] = []
    kind_counts: dict[str, int] = {}
    structural_kinds: dict[str, int] = {}
    offset_confidence = 1.0
    offset_method = "declared" if offset is not None else "manual"
    histogram: dict = {}
    voting_pages = 0
    numbering: dict = {}

    if not paginated:
        # There is no printed page here, so there is no offset to be confident
        # about -- and no reason to make the caller refuse a source for failing a
        # page check it cannot take.  The doubt this source DOES carry is
        # recorded where it belongs: `pagination: none`, which every
        # page-dependent feature must refuse on (locator.require_pagination).
        if declared is not None or (offset not in (None, 0)):
            raise ValueError(
                "%s: a page offset is not applicable for an unpaginated source "
                "(pagination: none); it has no printed pages to shift" % source_id)
        offset = 0
        offset_method = "none"
        offset_confidence = 1.0

    if infer:
        if paginated:
            # a human's override supersedes the repo's prose: only one of the two is
            # ever cross-checked against the pages, and it is whichever was given.
            guess = _infer_offset(pages, declared=(offset if offset is not None else declared))
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
        numbering = _infer_numbering(text)
        if segments is not None:
            # the override decides the depth the kinds are then read at too: a
            # book numbered `Theorem 7` has no two-segment head to infer from.
            numbering = _manual_numbering(segments, numbering)
        min_segments = int(numbering.get("min_segments") or 2) if numbering else 2
        inferred_kinds, structural_kinds = _infer_kinds(text, min_segments=min_segments)
        kind_counts = inferred_kinds
        if kinds is None:
            kinds = tuple(inferred_kinds) or SOURCE_KINDS
            if not inferred_kinds:
                warnings.append("kinds_not_inferred")
        else:
            missing = [k for k in kinds if k not in inferred_kinds]
            if missing:
                warnings.append("kinds_not_in_text: " + ", ".join(missing))
    elif segments is not None:
        numbering = _manual_numbering(segments, numbering)
    if offset is None:
        offset = 0
        offset_confidence = 0.0
    if kinds is None:
        kinds = SOURCE_KINDS

    items, rejects = find_headings(text, kinds, offset=offset, parts=parts,
                                   segments=numbering or None, pagination=pagination,
                                   suffix=suffix)
    if rejects:
        # a dropped heading names its own escape: re-running the inference would
        # read the same text and reach the same depth, so only the override can
        # say that the book really numbers this deep.
        deepest = min(4, max(r["number"].count(".") + 1 for r in rejects))
        warnings.append(
            "headings_deeper_than_the_numbering: %d (if the book really numbers this deep: "
            "cf sources set %s numbering=%d,%d)"
            % (len(rejects), source_id, segments_for(parts, numbering or None)[0], deepest))
    by_kind: dict[str, int] = {}
    by_chapter: dict[str, int] = {}
    for row in items:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        by_chapter[str(row["chapter"])] = by_chapter.get(str(row["chapter"]), 0) + 1
    completeness = _item_completeness([r["number"] for r in items])
    write_json(
        items_path,
        {
            "source_id": source_id,
            "source_sha": sha,
            "offset": offset,
            "kinds": list(kinds),
            "numbering": numbering,
            "pagination": pagination,
            "locator": LOCATOR_PAGE if paginated else LOCATOR_SECTION,
            "count": len(items),
            "by_kind": by_kind,
            "by_chapter": by_chapter,
            "ambiguous": [r["item"] for r in items if r.get("ambiguous")],
            "rejects": rejects,
            "created": utcnow(),
            "items": items,
        },
    )
    payload = {
        "source_id": source_id,
        "sha256": sha,
        "edition": edition or os.path.basename(pdf),
        "source_pdf": pdf,
        "source_format": fmt,
        "pagination": pagination,
        "locator": LOCATOR_PAGE if paginated else LOCATOR_SECTION,
        "pages": len(pages) if paginated else 0,
        "offset": offset,
        "offset_confidence": round(float(offset_confidence), 4),
        "offset_method": offset_method,
        "offset_declared": declared,
        "offset_voting_pages": voting_pages,
        "offset_histogram": histogram,
        "printed_page_rule": ("printed = pdf_page - %d" % offset if paginated
                              else "not applicable for an unpaginated source"),
        "kinds": list(kinds),
        "kind_counts": kind_counts,
        "structural_kinds": structural_kinds,
        "kind_warnings": [w for w in warnings if w.startswith("kinds")],
        "numbering": numbering,
        "item_number_parts": parts,
        "item_completeness": completeness.get("completeness", 0.0),
        "item_completeness_detail": completeness,
        "item_rejects": rejects,
        "text_sha256": sha256_text(text),
        "text_bytes": len(text.encode("utf-8")),
        "items": len(items),
        "page_files": "pdf" if (write_pages and paginated) else "none",
        "warnings": warnings,
        "created": utcnow(),
        "tool": "pdftotext -layout" if fmt == FORMAT_PDF else "read (%s)" % (suffix or "text"),
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


def _infer_kinds(text, *, min_segments=2):
    try:
        from cflib.inference import infer_kinds
    except Exception:  # pragma: no cover
        return {}, {}
    return infer_kinds(text, min_segments=min_segments)


def _infer_numbering(text) -> dict:
    """``cflib.inference.infer_numbering`` as the dict items.json stores.

    ``{}`` when cflib is not importable, which leaves every caller on
    :data:`DEFAULT_SEGMENTS` -- the grammar this stage has always applied.
    """
    try:
        from cflib.inference import infer_numbering
    except Exception:  # pragma: no cover - stand-alone use without cflib
        return {}
    return infer_numbering(text).to_dict()


def _manual_numbering(segments, inferred: dict | None = None) -> dict:
    """The stored ``numbering`` dict for a depth a human set.

    It keeps the inference's own counts beside the manual range, so the record
    still shows what the headings said -- the override is the answer, not an
    erasure of the evidence it overrode.
    """
    low, high = segments_for(3, segments)
    guess = dict(inferred or {})
    return {"min_segments": low, "max_segments": high, "confidence": 1.0,
            "headings": int(guess.get("headings") or 0),
            "counts": dict(guess.get("counts") or {}), "method": "manual"}


def _item_completeness(numbers) -> dict:
    """``cflib.inference.item_completeness``; ``{}`` without cflib."""
    try:
        from cflib.inference import item_completeness
    except Exception:  # pragma: no cover - stand-alone use without cflib
        return {}
    return item_completeness(numbers)


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
