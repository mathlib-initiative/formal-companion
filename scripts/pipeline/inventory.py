"""S1, inventory: ``doc.v0.json`` and the prepared page texts in, ``doc.v1.json`` out.

New code for milestone M1 of docs/proposals/pdf-to-companion.md, section 2.2
(output, gates) with section 4.6 (the cap on ``exact``).  What it adds to the
document:

* **numbered items** from the headings source preparation found
  (``sources/<id>/items.json``, the whitespace-tolerant heading regex of
  ``sources/prepare_source.py``), restricted to the pipeline's scope.  Each has
  a structured id from the item grammar (``ids.item_label_id``), ``pages
  {first, last, basis: physical, anchor: own}`` running to the page before the
  next heading, and never past the opening of an out-of-scope chapter or
  section, and a location ``<item>@pNNN`` whose TextQuoteSelector anchors the
  heading as printed (``Exercise 1.3.19``), with 32 characters of context on
  each side, all under the text-layer normalisation.  A heading preparation
  found more than once is placed on the occurrence that has a heading's shape
  (a parenthesised title or a period after the number), or reported unplaced;
* **erratum items** from the raw errata page kept by S0: every list entry that
  names a book item or a printed page becomes an item of the errata source,
  its quote capped, and an entry that names in-scope items is linked to them
  by ``relations.same_as`` through the item grammar (``cflib.items``);
* **passages**, the deterministic half only: items supplied in the document's
  item shape with ``--passages FILE`` (a later agent stage writes that file),
  accepted when the exact selector check finds their whole quote on their
  page (it is capped after that check), a second record with the same capped
  quote listed as a duplicate; and
  a heuristic finder for claims the text marks itself (a line that starts
  ``Proof.``, ``Proof of ...``, ``Remark.``, ``Claim.``, ``Example (...)``,
  unnumbered), whose locations cite the provenance record
  ``prov-s1-heuristic-passages`` and whose items carry ``x_finder:
  heuristic``.  Bold type does not survive ``pdftotext``, so an unnumbered bold
  heading with no marker word is not found here;
* ``relations.near`` on every passage: the numbered item it follows in reading
  order (the first one of the scope when it precedes them all).

Then the gates of ``pipeline/source_gates.py``,
``formal_companion_annotations.validate --canonical``, the document and the S1
receipt.  Nothing here reads a companion.
"""
from __future__ import annotations

import re
import time
from bisect import bisect_right
from pathlib import Path
from typing import Any, Mapping, Sequence

from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import ids
from formal_companion_annotations.validate import loads
from cflib import items as item_grammar
from cflib.errors import CfError
from cflib.hashing import sha256_file
from cflib.jsonio import read_json, write_json
from cflib.yamlio import read_yaml
from pipeline import document, pipeline_yaml, source_gates, stage_receipt
from sources import fetch_errata
from sources.prepare_source import load_items, split_pages

STAGE = "S1"
PROV_HEADINGS = "prov-s1-headings"
PROV_HEURISTIC = "prov-s1-heuristic-passages"
PROV_SUPPLIED = "prov-s1-supplied-passages"
PROV_ERRATA = "prov-s1-errata"
GATE_REPORT = "S1.gate.json"
SLOT_REASONS_FILE = "slot-reasons.yaml"

#: words that open an unnumbered, text-marked passage (the heuristic finder)
MARKER_WORDS: tuple[str, ...] = (
    "Proof", "Remark", "Remarks", "Note", "Claim", "Notation", "Convention", "Warning",
    "Heuristic", "Example", "Examples", "Definition", "Lemma", "Theorem", "Proposition",
    "Corollary", "Principle", "Conjecture", "Question", "Problem",
)
_MARKER = re.compile(
    r"(?m)^[ \t]*(?P<label>(?P<word>" + "|".join(MARKER_WORDS) + r")"
    r"(?:[ \t]+of[ \t]+[^\n]{1,80}?|[ \t]*\([^()\n]{1,80}\))?)[.:](?=\s|$)")
_ERRATUM_PAGE = re.compile(r"\b(?:page|pg\.?|p\.)\s*(\d{1,4})\b", re.I)
#: what follows a label that is a heading, not a cross-reference: a
#: parenthesised title, or the period that ends the label
# A heading's shape after the label: a parenthesised title, which may wrap onto
# the next line (``Exercise 1.2.11 (Monotone convergence theorem for measurable\nsets).``),
# or a period that closes the label itself. A period followed by ``)`` closes a
# parenthetical cross-reference (``..., Exercise 1.2.11.)``), not a heading.
_HEADING_SHAPE = re.compile(r"[ \t]*(?:\((?P<title>[^()]{1,200})\)|\.(?![\d)]))")
#: the opening of a chapter (``Chapter 2``) or a section (``2.1 Title``, or a
#: running head ``2.1. Title  47``); a short line, so a wrapped sentence that
#: happens to start with a number is not one
_CHAPTER_OPENING = re.compile(r"(?m)^[ \t]*(?i:chapter)[ \t]+(?P<number>\d+)\b(?P<rest>[^\n]*)$")
_SECTION_OPENING = re.compile(
    r"(?m)^[ \t]*(?:\d+[ \t]+)?(?P<number>\d+\.\d+)\.?[ \t]+(?P<rest>[A-Z][^\n]*)$")
_OPENING_MAX_WORDS = 10


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def load_page_texts(paths, source_id: str) -> list[str]:
    """The prepared source's pages, one string per physical page."""
    path = paths.source_text(source_id)
    if not Path(path).exists():
        raise CfError(f"no prepared text for {source_id} at {path}",
                      hint="run `cf ingest` first (S0 prepares the source)")
    return split_pages(Path(path).read_text(encoding="utf-8", errors="ignore"))


def in_scope(number: str, scope: Mapping[str, Any]) -> bool:
    levels = number.split(".")
    chapters = [int(c) for c in scope.get("chapters") or []]
    sections = [str(s) for s in scope.get("sections") or []]
    try:
        if int(levels[0]) in chapters:
            return True
    except ValueError:
        return False
    return len(levels) > 1 and ".".join(levels[:2]) in sections


def _heading_pattern(kind: str, number: str) -> re.Pattern:
    return re.compile(r"(?:^|\n)[ \t]*(?P<label>(?i:" + re.escape(kind) + r")[ \t]+"
                      + re.escape(number) + r")(?=[ \t.,:;()]|$)", re.M)


def _heading_on_page(page: str, kind: str, number: str) -> re.Match | None:
    return _heading_pattern(kind, number).search(page)


def _heading_shape(page: str, m: re.Match) -> re.Match | None:
    """The title-or-period that makes a line-start label a heading, or ``None``."""
    return _HEADING_SHAPE.match(page, m.end("label"))


def _place_ambiguous(row: dict, pages: Sequence[str]) -> tuple[int, re.Match, str] | None:
    """``(page, match, title)`` of the occurrence with a heading's shape, or ``None``.

    Preparation records every line-start occurrence of a label and takes the
    first inside its section's span, which can be a cross-reference that
    happens to start a line (``Lemma 1.1.3 is what the next item states``).
    Only an occurrence followed by a parenthesised title or a period counts;
    when several do, the one preparation chose wins, else the first.  With
    none, the caller reports the heading unplaced rather than guess.
    """
    candidates = sorted({o.get("pdf_page") for o in row.get("occurrences") or []
                         if isinstance(o.get("pdf_page"), int)} | {row.get("pdf_page")} - {None})
    pattern = _heading_pattern(row["kind"], row["number"])
    shaped = []
    for page in candidates:
        if not 1 <= page <= len(pages):
            continue
        for m in pattern.finditer(pages[page - 1]):
            shape = _heading_shape(pages[page - 1], m)
            if shape is not None:
                shaped.append((page, m, document.normalize(shape.group("title") or "")))
    if not shaped:
        return None
    return next((s for s in shaped if s[0] == row.get("pdf_page")), shaped[0])


def openings(pages: Sequence[str], scope: Mapping[str, Any]) -> list[tuple[int, int, str]]:
    """``(page, pos, label)`` of every out-of-scope chapter or section opening.

    Read by the heading grammar: a line ``Chapter N``, or a short line that
    starts with a section number and a capitalised title (a section heading,
    or the running head of a page inside that section).  A numbered item's
    span, and with it the scope span, stops before the first one that follows
    it.
    """
    out = []
    for page, text in enumerate(pages, start=1):
        for m in _CHAPTER_OPENING.finditer(text):
            words = len(m.group("rest").split()) + 2
            if words <= _OPENING_MAX_WORDS and not in_scope(m.group("number"), scope):
                out.append((page, m.start("number"), f"Chapter {m.group('number')}"))
        for m in _SECTION_OPENING.finditer(text):
            words = len(m.group("rest").split()) + 1
            if words <= _OPENING_MAX_WORDS and not in_scope(m.group("number"), scope):
                out.append((page, m.start("number"), f"Section {m.group('number')}"))
    return sorted(out)


def _running_head_only(before: str) -> bool:
    """True when the text above a heading on its page is at most one line (the head)."""
    lines = [ln for ln in before.splitlines() if ln.strip()]
    return len(lines) <= 1


def _nth(page: str, pos: int, exact: str) -> int:
    """How many times ``exact`` occurs in the normalised page before ``pos``."""
    return document.normalize(page[:pos]).count(exact)


# ---------------------------------------------------------------------------
# numbered items
# ---------------------------------------------------------------------------

def _last_page(pages: Sequence[str], page: int, end: tuple[int, int]) -> int:
    """The last page of an item on ``page`` that stops before ``end`` (page, pos)."""
    epage, epos = end
    last = epage - 1 if (epage > page and _running_head_only(pages[epage - 1][:epos])) \
        else epage
    return max(page, last)


def numbered_items(rows: Sequence[dict], pages: Sequence[str], scope: Mapping[str, Any],
                   source_id: str, stops: Sequence[tuple[int, int, str]] = ()
                   ) -> tuple[list[dict], list[dict], list[tuple], list[dict]]:
    """``(items, locations, anchors, unplaced)`` for the in-scope headings.

    ``anchors`` is ``(page, pos, item id)`` in reading order, the index the
    passages are placed against; ``unplaced`` lists a heading whose page text
    does not show it (a defect of the preparation), or an ambiguous heading
    none of whose occurrences has a heading's shape; the gate reports both.
    ``stops`` are the out-of-scope openings (:func:`openings`): an in-scope
    item's span ends before the first one after it, and an in-scope item
    followed by an out-of-scope heading with no opening between ends on its
    own page (the pending sign-off extends it).
    """
    placed = []
    unplaced = []
    for row in rows:
        page = row.get("pdf_page")
        title = None
        if row.get("ambiguous"):
            chosen = _place_ambiguous(row, pages)
            if chosen is None:
                unplaced.append({"item": row["item"], "page": page,
                                 "occurrences": [o.get("pdf_page") for o in
                                                 row.get("occurrences") or []],
                                 "reason": "ambiguous: no occurrence has a heading's shape "
                                           "(a parenthesised title or a period)"})
                continue
            page, m, title = chosen
        else:
            m = _heading_on_page(pages[page - 1], row["kind"], row["number"]) \
                if page and 1 <= page <= len(pages) else None
        if m is None:
            unplaced.append({"item": row["item"], "page": page,
                             "reason": "the heading is not on its page"})
            continue
        placed.append((page, m.start("label"), row, m.group("label"), title))
    placed.sort(key=lambda t: (t[0], t[1]))
    stops = sorted((s[0], s[1]) for s in stops)

    items, locations, anchors = [], [], []
    for index, (page, pos, row, raw, shaped_title) in enumerate(placed):
        if not in_scope(row["number"], scope):
            continue
        item_id = ids.item_label_id(row["kind"], row["number"])
        nxt = (placed[index + 1][0], placed[index + 1][1]) if index + 1 < len(placed) else None
        stop = next((s for s in stops if s > (page, pos) and (nxt is None or s < nxt)), None)
        if stop is not None:
            last = _last_page(pages, page, stop)
        elif nxt is None or not in_scope(placed[index + 1][2]["number"], scope):
            last = page
        else:
            last = _last_page(pages, page, nxt)
        exact = document.normalize(raw)
        context = document.quote_in_context(pages[page - 1], exact,
                                            _nth(pages[page - 1], pos, exact))
        prefix, suffix = context if context else ("", "")
        levels = row["number"].split(".")
        item: dict[str, Any] = {
            "id": item_id, "id_source": "label", "form": "numbered", "source": source_id,
            "kind": row["kind"], "number": row["number"], "label": exact,
            "pages": {"first": page, "basis": "physical", "anchor": "own"},
        }
        if last != page:
            item["pages"]["last"] = last
        if len(levels) > 1:
            item["section"] = ".".join(levels[:-1])
        title = shaped_title if shaped_title is not None else \
            document.normalize(row.get("title") or "")
        if title and document.word_count(title) <= document.EXACT_WORD_CAP:
            item["title"] = title
        items.append(item)
        locations.append(document.page_quote_location(item_id, source_id, page, exact,
                                                       prefix, suffix, PROV_HEADINGS))
        anchors.append((page, pos, item_id))
    return items, locations, anchors, unplaced


def scope_span(items: Sequence[dict]) -> tuple[int, int] | None:
    """The physical pages from the first in-scope heading to the last in-scope page."""
    if not items:
        return None
    return (min(i["pages"]["first"] for i in items),
            max(i["pages"].get("last", i["pages"]["first"]) for i in items))


def near_item(anchors: Sequence[tuple], page: int, pos: int) -> str | None:
    """The numbered item a passage at (page, pos) follows; the first one if none precedes it."""
    if not anchors:
        return None
    keys = [(a[0], a[1]) for a in anchors]
    at = bisect_right(keys, (page, pos)) - 1
    return anchors[max(at, 0)][2]


# ---------------------------------------------------------------------------
# passages
# ---------------------------------------------------------------------------

def _passage(item_id: str, id_source: str, source_id: str, kind: str, title: str,
             page: int, near: str | None, *, kind_source: str = "source",
             finder: str) -> dict:
    item: dict[str, Any] = {
        "id": item_id, "id_source": id_source, "form": "passage", "source": source_id,
        "kind": kind, "title": title, "title_source": "producer",
        "pages": {"first": page, "basis": "physical", "anchor": "own"},
        "x_finder": finder,
    }
    if kind_source != "source":
        item["kind_source"] = kind_source
    if near:
        item["relations"] = {"near": [near]}
    return item


def heuristic_passages(pages: Sequence[str], span: tuple[int, int] | None,
                       anchors: Sequence[tuple], source_id: str,
                       stops: Sequence[tuple[int, int, str]] = ()) -> list[tuple[dict, dict]]:
    """``(item, location)`` for every text-marked unnumbered passage in the span.

    A marked line with an out-of-scope opening (``stops``) between it and the
    item it follows is not in scope, even on a page the span covers.
    """
    if span is None:
        return []
    stops = sorted((s[0], s[1]) for s in stops)
    keys = [(a[0], a[1]) for a in anchors]
    out = []
    for page in range(span[0], span[1] + 1):
        text = pages[page - 1]
        for m in _MARKER.finditer(text):
            start = m.start("label")
            at = bisect_right(keys, (page, start)) - 1
            if at >= 0 and any(keys[at] < s <= (page, start) for s in stops):
                continue
            exact = document.cap_words(text[start:])
            if not exact:
                continue
            context = document.quote_in_context(text, exact, _nth(text, start, exact))
            if context is None:
                continue
            label = document.normalize(m.group("label"))
            near = near_item(anchors, page, start)
            title = f"{label} (heuristic: a line the text marks)"
            item = _passage(ids.passage_quote_id(exact), "quote_hash", source_id,
                            m.group("word"), title, page, near, finder="heuristic")
            loc = document.page_quote_location(item["id"], source_id, page, exact,
                                               context[0], context[1], PROV_HEURISTIC)
            out.append((item, loc))
    return out


def load_passages_file(path: str | Path) -> list[dict]:
    """The items of a ``--passages`` file: a JSON list, or ``{"items": [...]}``."""
    data = read_json(path)
    if isinstance(data, dict):
        data = data.get("items")
    if not isinstance(data, list):
        raise CfError(f"{path}: expected a JSON list of items or {{\"items\": [...]}}")
    return data


def supplied_passages(records: Sequence[dict], pages: Sequence[str], offset: int,
                      anchors: Sequence[tuple],
                      source_id: str
                      ) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """``(accepted (item, location) pairs, rejects, duplicates)`` for ``--passages`` items.

    A record is in the document's item shape: ``form: passage``, a ``kind``, a
    ``title``, ``pages {first, basis}`` and ``quote {exact}``.  Its whole
    normalised quote must occur on its page; only then is it capped for the
    selector and the id, so a true opening with an invented tail is rejected.
    Its id is then the quote hash (or its own, when it says ``id_source:
    declared``) and its ``relations.near`` is kept when given, else computed.
    A record whose id an earlier accepted record already has (the same capped
    quote) is listed under ``duplicates``, never dropped silently.
    """
    accepted, rejects, duplicates = [], [], []
    taken: dict[str, str] = {}
    for index, rec in enumerate(records):
        where = f"passages[{index}]"
        if not isinstance(rec, dict):
            rejects.append({"record": where, "reason": "not an object"})
            continue
        problems = []
        if rec.get("form", "passage") != "passage":
            problems.append("form must be passage")
        if rec.get("source", source_id) != source_id:
            problems.append(f"source must be {source_id}")
        for key in ("kind", "title"):
            if not (isinstance(rec.get(key), str) and rec[key].strip()):
                problems.append(f"{key} is required")
        pages_rec = rec.get("pages") if isinstance(rec.get("pages"), dict) else {}
        first = pages_rec.get("first")
        basis = pages_rec.get("basis", "physical")
        full = document.normalize(str((rec.get("quote") or {}).get("exact") or ""))
        exact = document.cap_words(full)
        if not isinstance(first, int) or basis not in ("physical", "printed"):
            problems.append("pages {first, basis} is required")
        if not exact:
            problems.append("quote.exact is required")
        if problems:
            rejects.append({"record": where, "reason": "; ".join(problems)})
            continue
        page = first + offset if basis == "printed" else first
        if not 1 <= page <= len(pages):
            rejects.append({"record": where, "reason": f"physical page {page} is outside the PDF"})
            continue
        text = pages[page - 1]
        norm = document.normalize(text)
        at = norm.find(full)
        if at < 0:
            capped_only = exact != full and exact in norm
            rejects.append({"record": where, "page": page,
                            "reason": "the quote does not occur on its page (exact selector "
                                      "check)" + ("; its first "
                                                  f"{document.EXACT_WORD_CAP} words do, the "
                                                  "rest does not" if capped_only else "")})
            continue
        prefix, suffix = document.quote_in_context(text, exact,
                                                   norm[:at].count(exact)) or ("", "")
        if rec.get("id_source") == "declared" and rec.get("id"):
            item_id, id_source = str(rec["id"]), "declared"
        else:
            item_id, id_source = ids.passage_quote_id(exact), "quote_hash"
        near = None
        given = (rec.get("relations") or {}).get("near")
        if not given:
            # place it by where the quote sits: map the normalised offset back
            # onto the raw page through the first word of the quote
            raw = text.find(exact.split(" ")[0])
            near = near_item(anchors, page, raw if raw >= 0 else 0)
        item = _passage(item_id, id_source, source_id, rec["kind"].strip(), rec["title"].strip(),
                        page, near, kind_source=str(rec.get("kind_source") or "source"),
                        finder="supplied")
        if given:
            item["relations"] = {"near": document.sorted_ids(given)}
        if item_id in taken:
            duplicates.append({"record": where, "id": item_id, "page": page,
                               "duplicate_of": taken[item_id]})
            continue
        taken[item_id] = where
        loc = document.page_quote_location(item_id, source_id, page, exact, prefix, suffix,
                                           PROV_SUPPLIED)
        accepted.append((item, loc))
    return accepted, rejects, duplicates


# ---------------------------------------------------------------------------
# errata
# ---------------------------------------------------------------------------

def errata_entries(raw_html: str) -> list[str]:
    """The list entries of the rendered errata page, in page order."""
    body, _audit = fetch_errata.render(raw_html)
    return [line.strip()[2:].strip() for line in body.splitlines()
            if line.strip().startswith("- ") and line.strip()[2:].strip()]


def erratum_items(entries: Sequence[str], errata_source: str, kinds: Sequence[str],
                  numbering, book_items: Mapping[str, dict], offset: int,
                  span: tuple[int, int] | None) -> tuple[list[dict], dict]:
    """Erratum items for the entries that concern the scope, and the counts."""
    counts = {"entries": len(entries), "linked": 0, "page_only": 0, "out_of_scope": 0,
              "no_item_no_page": 0, "rejects": 0}
    out: list[dict] = []
    for entry in entries:
        rejects: list = []
        named = item_grammar.find_items(entry, kinds, numbering=numbering, rejects=rejects)
        counts["rejects"] += len(rejects)
        targets = sorted({ids.item_label_id(i.kind, ".".join(map(str, i.number_levels)))
                          for i in named} & set(book_items))
        pages_named = [int(p) for p in _ERRATUM_PAGE.findall(entry)]
        if named and not targets:
            counts["out_of_scope"] += 1
            continue
        if not named:
            if not pages_named:
                counts["no_item_no_page"] += 1
                continue
            physical = pages_named[0] + offset
            if span is None or not span[0] <= physical <= span[1]:
                counts["out_of_scope"] += 1
                continue
        exact = document.cap_words(entry)
        if targets:
            counts["linked"] += 1
            title = "Erratum naming " + ", ".join(book_items[t]["label"] for t in targets)
        else:
            counts["page_only"] += 1
            title = f"Erratum on printed page {pages_named[0]}, naming no item"
        item: dict[str, Any] = {
            "id": ids.passage_quote_id(exact), "id_source": "quote_hash", "form": "passage",
            "source": errata_source, "kind": "erratum", "kind_source": "producer",
            "title": title, "title_source": "producer", "quote": {"exact": exact},
            "x_finder": "errata",
        }
        if targets:
            item["relations"] = {"same_as": targets}
        out.append(item)
    return out, counts


# ---------------------------------------------------------------------------
# the stage
# ---------------------------------------------------------------------------

def _dedupe(pairs: Sequence[tuple[dict, dict]]) -> list[tuple[dict, dict]]:
    """One passage per id: the same normalised quote on one source is one passage."""
    seen: dict[str, tuple[dict, dict]] = {}
    for item, loc in pairs:
        seen.setdefault(item["id"], (item, loc))
    return list(seen.values())


def load_reasons(path: str | Path | None) -> dict[str, str]:
    if path is None or not Path(path).exists():
        return {}
    data = read_yaml(path) if str(path).endswith((".yaml", ".yml")) else read_json(path)
    if not isinstance(data, dict):
        raise CfError(f"{path}: expected a mapping of slot -> reason")
    return {str(k): str(v) for k, v in data.items()}


def build(paths, pipeline_id: str, *, passages: str | Path | None = None,
          heuristic: bool = True, slot_reasons: str | Path | None = None) -> dict:
    """Build doc v1 and run every gate; writes nothing.

    Returns ``{doc, gate, source_id, stats, ...}``; :func:`run_stage` writes.
    """
    settings = pipeline_yaml.load_for(paths, pipeline_id)
    scope = settings["scope"]
    v0_path = paths.pipeline_doc(pipeline_id, 0)
    if not Path(v0_path).exists():
        raise CfError(f"no doc v0 for pipeline {pipeline_id}", hint="run `cf ingest` first")
    doc = loads(Path(v0_path).read_text(encoding="utf-8"))
    sid = settings["pipeline"]["source_id"]
    pdf_source = next((s for s in doc["sources"] if s["id"] == sid), None)
    if pdf_source is None:
        raise CfError(f"doc v0 has no source {sid}")
    table = load_items(str(paths.source_dir(sid)))
    if table.get("source_sha") != pdf_source["sha256"]:
        raise CfError(f"sources/{sid}/items.json was prepared from sha "
                      f"{str(table.get('source_sha'))[:12]}, doc v0 pins "
                      f"{pdf_source['sha256'][:12]}",
                      hint="re-run `cf ingest` for this pipeline")
    pages = load_page_texts(paths, sid)
    if len(pages) != pdf_source["physical_page_count"]:
        raise CfError(f"the prepared text has {len(pages)} pages, the PDF "
                      f"{pdf_source['physical_page_count']}")
    offset = int(pdf_source["printed_page_offset"])

    stops = openings(pages, scope)
    items, locations, anchors, unplaced = numbered_items(table["items"], pages, scope, sid,
                                                         stops)
    span = scope_span(items)
    by_id = {i["id"]: i for i in items}
    provenance = [p for p in doc["provenance"]
                  if p["id"] not in (PROV_HEADINGS, PROV_HEURISTIC, PROV_SUPPLIED, PROV_ERRATA)]
    provenance.append({"id": PROV_HEADINGS, "actor": "tool", "method": "text_match",
                       "confirmed_by_human": False,
                       "tool": {"name": "cf inventory", "version": "1"},
                       "inputs": [{"source": sid}],
                       "note": "numbered headings found by the heading regex of source "
                               "preparation, anchored on their page"})

    supplied, rejected, duplicates = [], [], []
    if passages is not None:
        supplied, rejected, duplicates = supplied_passages(load_passages_file(passages), pages, offset,
                                               anchors, sid)
        provenance.append({"id": PROV_SUPPLIED, "actor": "agent",
                           "method": "agent_annotation", "confirmed_by_human": False,
                           "inputs": [{"source": sid}],
                           "note": "passages supplied with --passages, each accepted by the "
                                   "exact selector check on its own page"})
    found = []
    if heuristic:
        found = heuristic_passages(pages, span, anchors, sid, stops)
        provenance.append({"id": PROV_HEURISTIC, "actor": "tool", "method": "text_match",
                           "confirmed_by_human": False,
                           "tool": {"name": "cf inventory heuristic passage finder",
                                    "version": "1"},
                           "inputs": [{"source": sid}],
                           "note": "HEURISTIC: lines the text itself marks (Proof., Remark., "
                                   "Claim., Example (...).) with no number; not agent-reviewed, "
                                   "not a completeness claim"})
    passages_all = _dedupe(supplied + found)
    items += [p[0] for p in passages_all]
    locations += [p[1] for p in passages_all]

    errata_counts = None
    errata_source = next((s for s in doc["sources"] if s["id"] == f"{sid}-errata"), None)
    errata_text = None
    if errata_source is not None:
        raw_path = paths.source_errata_raw(sid)
        if not Path(raw_path).exists():
            raise CfError(f"doc v0 names an errata source but {raw_path} is missing",
                          hint="re-run `cf ingest`")
        raw = Path(raw_path).read_text(encoding="utf-8", errors="ignore")
        entries = errata_entries(raw)
        errata_text = "\n".join(entries)
        extra, errata_counts = erratum_items(entries, errata_source["id"], table["kinds"],
                                             table.get("numbering") or None, by_id, offset,
                                             span)
        items += extra
        provenance.append({"id": PROV_ERRATA, "actor": "tool", "method": "pipeline",
                           "confirmed_by_human": False,
                           "tool": {"name": "cf inventory", "version": "1"},
                           "inputs": [{"source": errata_source["id"]}, {"source": sid}],
                           "note": "one item per errata entry that names an in-scope item "
                                   "(linked by same_as) or an in-scope printed page"})

    doc["items"] = items
    doc["locations"] = locations
    doc["provenance"] = provenance

    kind_numbers: dict[str, list[str]] = {}
    for item in items:
        if item["form"] == "numbered":
            kind_numbers.setdefault(item["kind"], []).append(item["number"])
    reasons_path = slot_reasons if slot_reasons is not None else \
        paths.pipeline_dir(pipeline_id) / SLOT_REASONS_FILE
    reasons = load_reasons(reasons_path)
    text, problems, warnings = document.check(doc)
    checks = {
        "slot_completeness": source_gates.slot_completeness(
            [n for ns in kind_numbers.values() for n in ns]),
        "slot_contiguity": source_gates.slot_contiguity(kind_numbers, reasons,
                                                        pages[span[0] - 1:span[1]]
                                                        if span else None),
        "exact_selectors": source_gates.exact_selectors(doc, {sid: pages}),
        "unique_ids": source_gates.unique_ids(doc),
        "exact_cap": source_gates.exact_cap(doc),
        "headings_placed": {"ok": not [u for u in unplaced
                                       if in_scope(u["item"].split(" ")[-1], scope)],
                            "unplaced": unplaced},
        "validate_canonical": {"ok": not problems and not warnings, "problems": problems[:20],
                               "warnings": warnings[:20]},
    }
    if span is not None:
        # a missing slot's `seen_as` page is relative to the span; make it physical
        for row in checks["slot_contiguity"]["missing"] + checks["slot_contiguity"]["explained"]:
            for seen in row.get("seen_as", []):
                seen["page"] += span[0] - 1
    if errata_source is not None:
        checks["errata_quotes"] = source_gates.errata_quotes(
            doc, {errata_source["id"]: errata_text or ""})
    if not items:
        checks["items_found"] = {"ok": False, "note": "no numbered item in scope"}
    stats = {
        "numbered": sum(1 for i in items if i["form"] == "numbered"),
        "passages_heuristic": sum(1 for i, _ in passages_all
                                  if i["x_finder"] == "heuristic"),
        "passages_supplied": len(supplied),
        "passages_rejected": len(rejected),
        "passages_duplicate": len(duplicates),
        "errata_items": sum(1 for i in items if i.get("x_finder") == "errata"),
        "scope_pages": list(span) if span else None,
    }
    gate = {"ok": all(c["ok"] for c in checks.values()), "checks": checks,
            "rejected_passages": rejected, "duplicate_passages": duplicates,
            "errata": errata_counts, "stats": stats,
            "scope_stops": [{"page": p, "label": label} for p, _pos, label in stops
                            if span and p >= span[0]][:1],
            "slot_reasons": reasons,
            "manual": [{"gate": "maintainer_signoff", "status": "pending", "method": "manual",
                        "note": "the maintainer confirms the scope, the edition and the item "
                                "list, and signs every slot reason"},
                       {"gate": "recall_calibration", "status": "not_built",
                        "method": "manual",
                        "note": "planted-deletion recall needs the enumerator and hunter "
                                "agents, which are not built"}]}
    return {"doc": doc, "text": text, "gate": gate, "source_id": sid, "stats": stats,
            "settings": settings, "v0_path": v0_path}


def run_stage(paths, pipeline_id: str, **kwargs) -> dict:
    """Build, gate and (when every gate passes) write doc v1 and the S1 receipt."""
    started = time.monotonic()
    result = build(paths, pipeline_id, **kwargs)
    gate = result["gate"]
    report_path = paths.stage_receipts_dir(pipeline_id) / GATE_REPORT
    write_json(report_path, gate, indent=1, sort_keys=True)
    result["gate_report"] = report_path
    if not gate["ok"]:
        # a failed re-run must not leave the last run's checkpoint and passing
        # receipt behind for a later stage to proceed on
        for stale in (paths.pipeline_doc(pipeline_id, 1),
                      paths.stage_receipt_json(pipeline_id, STAGE)):
            Path(stale).unlink(missing_ok=True)
        failed = [k for k, v in gate["checks"].items() if not v["ok"]]
        raise CfError(f"S1 gate failed: {', '.join(failed)}; doc v1 was not written, and "
                      f"any earlier doc v1 and S1 receipt were removed",
                      hint=f"{report_path} lists every failure; a missing slot the book "
                           f"really skips is listed with a reason in "
                           f"{paths.pipeline_dir(pipeline_id) / SLOT_REASONS_FILE}")
    doc_path = paths.pipeline_doc(pipeline_id, 1)
    doc_sha = document.write_checkpoint(doc_path, result["doc"])
    receipt = {
        "input_doc_sha256": sha256_file(result["v0_path"]),
        "output_doc_sha256": doc_sha,
        "pipeline_yaml_sha256": sha256_file(paths.pipeline_yaml(pipeline_id)),
        "fleet_runs": [], "prompt_shas": {}, "model_ids": [],
        "gate_result": gate,
        "consent": dict(result["settings"].get("consent") or {"include_source_text": False}),
        "cost_usd": 0, "tokens": {},
        "wall_clock_s": round(time.monotonic() - started, 3),
    }
    result["receipt"] = stage_receipt.write(paths, pipeline_id, STAGE, receipt)
    result["doc_path"] = doc_path
    result["doc_sha256"] = doc_sha
    return result
