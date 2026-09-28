"""Export a docstring-tagged companion and its literate site as an annotation document.

New code, ported from the prototype exporter of docs/annotations.md section 10
(the tao-measure-lean paragraph) into a repository command; the design is
docs/proposals/pdf-to-companion.md section 5.4.  It reuses the skill's readers:

* ``adapters.docstring_tags`` (driven by an adapter config, by default the
  shipped template ``templates/adapters/docstring_tags.tao-measure-lean.yaml``)
  for the tag rows: item, sub, kind, page anchor and sorry class;
* ``adapters.leanparse`` for every declaration, tagged or not, with its
  docstring, namespace, privacy and doc line;
* ``adapters.itemtag`` and ``cflib.items`` for tag spans and the item grammar;
* ``annotations.ids`` for statement-hash ids and signatures;
* ``annotations.validate`` for the canonical bytes.

Outside the checkout, all read-only:

* the anchor edition's PDF (named through the registry by its sha256), whose
  ``pdftotext`` layer locates numbered headings and verifies docstring quotes
  before a ``TextQuoteSelector`` is written (:mod:`.pdftext`);
* the literate site's cross-reference index (``xref.json``) and its page
  manifest, a pin file listing every HTML page with its sha256 (the site is
  git-ignored build output that cannot be rebuilt everywhere).  The site is one
  ``scope: site`` source pinned by the sha256 of that manifest; a location
  names its page with a ``SitePageSelector`` refined by an RFC 3236 fragment
  (named declarations) or a ``CssSelector`` on the keyword's ``data-binding``
  attribute (anonymous ones, which Verso gives no id).  With the rendered site
  on disk each anchor is checked to occur once on its page; without it the
  index is trusted and the provenance note says so.

Deliberate differences from the prototype, documented in tests/test_export.py:
the PDF's id, title and edition come from the registry; ``companion.generator``
names this command; the site's ``retrieved_at`` and source id come from the
page manifest instead of the build's file times.
"""
from __future__ import annotations

import collections
import json
import re
import unicodedata
from pathlib import Path

from adapters import itemtag, leanparse
from adapters.api import from_config
from annotations import ids
from annotations.validate import site_sha256
from cflib import items as cf_items
from cflib import vocab, yamlio

from . import common
from .common import ExportError, HeaderCutter, Report, load_json, read_bytes, sha256_bytes
from .pdftext import PdfText, quoted_runs

_SKILL = Path(__file__).resolve().parents[3]
TEMPLATE = _SKILL / "templates" / "adapters" / "docstring_tags.tao-measure-lean.yaml"
RFC8118 = vocab.CONFORMS_TO_PDF
RFC3236 = vocab.CONFORMS_TO_HTML
PROV_TAG = "prov-docstring-tag"
PROV_TEXT = "prov-pdf-text-match"
PROV_SITE = "prov-verso-site"
SITE_NOTE = ("_site is git-ignored build output; it is not provably built from companion.commit, so it is "
             "pinned by content, not by revision")

_ABBREV = re.compile(r"(?:\bpp?|\be\.g|\bi\.e|\bcf|\bvs|\bresp|\bfig|\bno|\bch|\bsec|\bEx|\bDef|\bThm)$", re.I)


# ---------------------------------------------------------------------------
# tags, titles and notes
# ---------------------------------------------------------------------------

def _tag_span(text: str, tag: itemtag.TagHit) -> tuple[int, int]:
    """The tag's span in the collapsed docstring, with its backticks when it has them (a bare tag has none)."""
    s, e = tag.start, tag.end
    if s > 0 and text[s - 1] == "`" and text[e:e + 1] == "`":
        s, e = s - 1, e + 1
    return s, e


def first_sentence(doc: str, tag: itemtag.TagHit | None) -> str:
    """A passage's title: the docstring's first sentence with the tag cut out."""
    text = itemtag.collapse(doc)
    if tag is not None:
        s, e = _tag_span(text, tag)
        text = text[:s] + text[e:]
        text = re.sub(r"\(\s*[;,:—-]?\s*\)", "", text)   # "( )" left behind
        text = re.sub(r"\(\s*[;,]\s*", "(", text)
    text = re.sub(r"\s+", " ", text).strip(" .;:,—-")
    for m in re.finditer(r"\.(?=\s|$)", text):
        before = text[:m.start()]
        if _ABBREV.search(before) or before.endswith(("book p", "book pp")):
            continue
        after = text[m.end():].lstrip()
        if after[:1].isdigit():
            continue
        text = before
        break
    text = re.sub(r"\(\s*[;,:—-]+\s*", "(", text).strip(" .;:,")
    if text.startswith("(") and text.endswith(")") and text.count("(") == text.count(")"):
        depth, wraps = 0, True
        for i, ch in enumerate(text):
            depth += (ch == "(") - (ch == ")")
            if depth == 0 and i < len(text) - 1:
                wraps = False
                break
        if wraps:
            text = text[1:-1].strip()
    if len(text) > 200:
        text = text[:197].rstrip() + "..."
    return text


def tag_parenthetical(doc: str, tag: itemtag.TagHit | None) -> str | None:
    """The balanced parenthetical that directly follows the tag's closing backtick, if any."""
    if tag is None:
        return None
    text = itemtag.collapse(doc)
    i = _tag_span(text, tag)[1]                        # past the closing backtick, if any
    while i < len(text) and text[i] == " ":
        i += 1
    if i >= len(text) or text[i] != "(":
        return None
    depth = 0
    for j in range(i, min(len(text), i + 400)):
        depth += (text[j] == "(") - (text[j] == ")")
        if depth == 0:
            return text[i + 1:j].strip()
    return None


def _fold(s: str) -> str:
    folded = re.sub(r"[^0-9a-z]+", " ", unicodedata.normalize("NFKC", s).casefold()).strip()
    return re.sub(r"^(?:the|a|an) ", "", folded)


def link_note(paren: str | None, book_title: str | None, page_raw: str | None,
              page_re: re.Pattern | None = None) -> str | None:
    """A link note: what the tag's own parenthetical says beyond the book's title and the page.

    The parenthetical is split on ``;``; a segment that is the book title, or
    only the page anchor the link already carries in ``pages.raw``, is dropped;
    what is left, if anything, is the note.
    """
    if not paren:
        return None
    page_re = page_re or itemtag.PAGE_RE
    keep = []
    for seg in (s.strip() for s in paren.split(";")):
        if not seg:
            continue
        if book_title and _fold(seg) == _fold(book_title):
            continue
        if page_raw and _fold(seg) == _fold(page_raw):
            continue
        if page_re.fullmatch(seg):
            continue
        keep.append(seg)
    note = "; ".join(keep).strip()
    return note or None


# ---------------------------------------------------------------------------
# the export
# ---------------------------------------------------------------------------

class DocstringTagsExport:
    """One export: the tag rows, every declaration, the PDF text layer and the site pin."""

    def __init__(self, repo: Path, commit: str, *, pdf: Path, xref: Path, site_pages: Path,
                 site: Path | None = None, adapter_config: Path | None = None,
                 name: str | None = None, repo_id: str | None = None, pdf_text: PdfText | None = None,
                 site_name: str = "verso_literate", source_id: str | None = None,
                 source_title: str | None = None, source_edition: str | None = None,
                 page_offset: int | None = None):
        self.repo = repo.resolve()
        self.head, self.head_date = common.pinned_checkout(self.repo, commit)
        self.name, self.repo_id = common.companion_identity(self.repo, name, repo_id)
        self.report = Report()
        self.site_name = site_name
        # the PDF
        self.pdf_path = pdf
        self.pdf_bytes = pdf.stat().st_size
        self.pdf_sha = common.sha256_bytes(pdf.read_bytes())
        fields = common.pdf_source_fields(self.pdf_sha, source_id=source_id, title=source_title,
                                          edition=source_edition, offset=page_offset)
        self.source_id, self.source_fields = fields["id"], fields
        self.offset = fields["printed_page_offset"]
        self.pdf = pdf_text if pdf_text is not None else PdfText.extract(pdf)
        # the adapter and its rows
        config_path = adapter_config or TEMPLATE
        cfg = yamlio.read_yaml(config_path)
        if not isinstance(cfg, dict):
            raise ExportError(f"{config_path} is not an adapter config")
        cfg["repo"] = str(self.repo)
        cfg["name"] = self.name
        cfg["sources"] = {"default": self.source_id, "map": []}
        self.adapter = from_config(cfg, source=str(config_path))
        self.config_name = Path(config_path).name
        self.config_is_template = Path(config_path).resolve() == TEMPLATE.resolve()
        # the site pin
        self.pin = load_json(site_pages)
        self.xref_bytes = read_bytes(xref)
        self.site_dir = site.resolve() if site is not None else None
        # state
        self.items: dict[str, dict] = {}
        self.links: dict[str, dict] = {}
        self.locations: dict[str, dict] = {}
        self.declarations: dict[str, dict] = {}
        self.destinations: dict[str, dict] = {}
        self.unattributed: dict[str, dict] = {}
        self.changelog: dict[str, dict] = {}
        self.decl_id_at: dict[tuple[str, int], str] = {}
        self.decl_by_loc: dict[tuple[str, int], leanparse.Decl] = {}
        self.passage_quote_stats: collections.Counter = collections.Counter()
        self.passage_pages: collections.Counter = collections.Counter()
        self.link_quote_stats: collections.Counter = collections.Counter()
        self.passage_merges: list[dict] = []
        self.helper_rows: list[dict] = []
        self.quote_misses: list[dict] = []
        self.anchored_in_text: list[dict] = []
        self.split_tags: list[dict] = []
        self.sub_fixes: list[dict] = []

    # -- rows and declarations -------------------------------------------------------
    def read_rows(self) -> None:
        rows, counts, rejects = self.adapter.tagged_rows(self.repo, {self.source_id: self.pdf_sha})
        self.rows = rows
        self.rejects = rejects
        self.report.counts["adapter_counts"] = dict(sorted(counts.items()))
        self.report.counts["adapter_rejects"] = rejects
        self.rows_at = {(r["file"], r["line"]): r for r in rows}
        self.reject_at = {(r["file"], r["line"]): r for r in rejects}
        self.page_re = self.adapter.page_pattern() or itemtag.PAGE_RE
        self.clause_window = int(self.adapter.get("tag.clause_window", 140))
        self.numbered_kinds = self.adapter.numbered_kinds(self.source_id)
        self.all_kinds = self.adapter.item_kinds(self.source_id)
        self.files = self.adapter.source_files(self.repo)

    def read_declarations(self) -> None:
        decls: list[leanparse.Decl] = []
        scanned: list[ids.AnonymousDecl] = []
        for path in self.files:
            rel = str(path.relative_to(self.repo))
            decls += list(leanparse.parse_file(path, rel=rel, decl_kinds=self.adapter.decl_kinds(),
                                               max_body_lines=int(self.adapter.get("max_body_lines", 40))))
            scanned += ids.scan_file(path.read_text(encoding="utf-8"), rel)
        self.decls = decls
        self.decl_by_loc = {(d.file, d.line): d for d in decls}
        assigned = ids.assign_ids(scanned)
        anon_at = {(a.file, a.line): (a, *assigned[a]) for a in scanned}
        anon_decls = [d for d in decls if d.anonymous]
        unmatched = [(d.file, d.line) for d in anon_decls if (d.file, d.line) not in anon_at]
        extra = [k for k in anon_at if k not in {(d.file, d.line) for d in anon_decls}]
        if unmatched or extra:
            self.report.problem(f"anonymous join: leanparse-only {unmatched}, ids.py-only {extra}")
        for rid, a in ids.ordinal_ids(assigned):
            cid = f"ordinal/{rid}"
            self.changelog[cid] = {"id": cid, "at": self.head_date, "event": "ordinal_assigned", "subject": rid,
                                   "subject_kind": "declaration", "commit": self.head, "file": a.file,
                                   "line": a.line}
        self.report.counts["anonymous"] = {
            "leanparse": len(anon_decls), "ids_py": len(scanned),
            "by_keyword": dict(collections.Counter(a.keyword for a in scanned)),
            "by_id_source": dict(collections.Counter(v[1] for v in assigned.values())),
            "with_ordinal": len(self.changelog), "untyped": sum(1 for a in scanned if not a.typed),
            "rejected_tags": [(a.file, a.line, a.rejected_tag) for a in scanned if a.rejected_tag]}
        gap_re = self.adapter.gap_marker()
        exercise_permanent = self.adapter.exercise_sorry_is_permanent()
        cutter = HeaderCutter(self.repo)
        for d in decls:
            module = common.module_of(d.file)
            rec: dict = {"file": d.file, "line": d.line, "module": module, "kind": d.kind,
                         "namespace": d.namespace or "", "private": bool(d.private),
                         "anonymous": bool(d.anonymous)}
            if d.doc_line:
                rec["doc_line"] = d.doc_line
            if d.anonymous:
                if (d.file, d.line) not in anon_at:
                    continue                                 # reported above as a leanparse-only declaration
                a, rid, source = anon_at[(d.file, d.line)]
                rec.update(id=rid, id_source=source, statement_sha256=a.statement_sha256, signature=a.signature)
            else:
                if d.private:
                    rec.update(id=ids.private_id(module, d.full_name), id_source="private", name=d.full_name)
                else:
                    rec.update(id=d.full_name, id_source="name", name=d.full_name)
                got = cutter.cut(d.file, d.line, d.kind)
                if got is not None:
                    rec["signature"], rec["statement_sha256"] = got
            _has, sorry_class = self.adapter.classify_sorry(d.body, gap_re, exercise_permanent)
            row = self.rows_at.get((d.file, d.line))
            if row is not None and row["sorry_class"] != sorry_class:
                self.report.problem(f"sorry class disagrees for {rec['id']}: {row['sorry_class']} vs {sorry_class}")
            if row is not None and row["namespace"] != rec["namespace"]:
                self.report.problem(f"namespace disagrees for {rec['id']}: {row['namespace']!r} vs "
                                    f"{rec['namespace']!r}")
            rec["sorry"] = sorry_class
            if rec["id"] in self.declarations:
                self.report.problem(f"duplicate declaration id {rec['id']} ({d.file}:{d.line})")
                continue
            self.declarations[rec["id"]] = rec
            self.decl_id_at[(d.file, d.line)] = rec["id"]
        self.report.counts["declarations"] = {
            "total": len(self.declarations),
            "by_id_source": dict(collections.Counter(r["id_source"] for r in self.declarations.values())),
            "sorry": dict(collections.Counter(r["sorry"] for r in self.declarations.values())),
            "with_signature": sum(1 for r in self.declarations.values() if "signature" in r),
            "named_with_signature": sum(1 for r in self.declarations.values()
                                        if "signature" in r and not r["anonymous"]),
            "signature_skipped": dict(sorted(cutter.skipped.items())),
            "root_namespace": sum(1 for r in self.declarations.values() if r["namespace"] == "")}

    # -- items and links ---------------------------------------------------------------
    def numbered_item(self, kind: str, number: str) -> str:
        iid = ids.item_label_id(kind, number)
        if iid not in self.items:
            self.items[iid] = {"id": iid, "id_source": "label", "form": "numbered", "source": self.source_id,
                               "kind": kind, "number": number, "label": f"{kind} {number}"}
        return iid

    @staticmethod
    def link_pages(page, last, raw, source) -> dict | None:
        if page is None:
            return None
        out = {"first": page, "basis": "printed", "anchor": source}
        if last and last != page:
            out["last"] = last
        if raw:
            out["raw"] = raw
        return out

    def add_link(self, item: str, role: str, decl: str, raw: str, sub: str = "", **extra) -> str:
        lid = f"{item}/{role}/{decl}" + (f"/{sub}" if sub else "")
        rec = {"id": lid, "item": item, "declaration": decl, "role": role, "provenance": PROV_TAG}
        if common.has_url(raw):
            self.report.problem(f"{decl}: the tag {raw!r} holds a URL, which I10 keeps out of raw_label; "
                                "the link was written without it")
        else:
            rec["raw_label"] = raw
        if sub:
            rec["sub"] = sub
        rec.update({k: v for k, v in extra.items() if v is not None})
        if lid in self.links:
            self.report.problem(f"duplicate link {lid}")
        self.links[lid] = rec
        return lid

    def page_of(self, text: str) -> tuple[int | None, int | None, str | None]:
        m = self.page_re.search(text)
        if not m:
            return None, None, None
        last = int(m.group(2)) if (m.lastindex or 0) >= 2 and m.group(2) else None
        return int(m.group(1)), last, m.group(0).strip()

    def pages_in_range(self, did: str, raw: str, page, last) -> bool:
        """Whether a tag's printed page (and range end) lie in the PDF; a problem naming the declaration if not."""
        if page is None:
            return True
        count = self.pdf.page_count()
        bad = page < 1 or page + self.offset > count
        if last is not None and last != page:
            bad = bad or last < page or last + self.offset > count
        if bad:
            span = f"{page}-{last}" if last is not None and last != page else f"{page}"
            self.report.problem(f"{did}: the tag {raw!r} gives printed page {span}, outside the PDF's "
                                f"{count} physical pages at offset {self.offset} (or reversed); the page was dropped")
        return not bad

    def add_passage(self, d: leanparse.Decl, did: str, raw: str, half: str, page, last, page_raw) -> None:
        """One passage per In-text tag (or tag half), keyed by the section 5.2 rule."""
        tag = self.adapter.tag_of(d.doc, self.adapter.item_kinds())
        title = first_sentence(d.doc, tag)
        if common.has_url(title):
            self.report.problem(f"{did}: the passage title from its docstring holds a URL, which I10 keeps out "
                                "of every non-source field; the fallback title was written instead")
            title = ""
        title = title or f"In-text passage formalised by {did}"
        parsed = cf_items.try_parse(half, self.all_kinds)[0]
        qualifier = (parsed.qualifier or "").strip() if parsed else ""
        near_item = None
        kind, kind_source = "passage", None
        # a numbered neighbour is the tag's first item mention ("In-text, Remark 1.4.33",
        # "In-text definition, Exercise 1.2.13", "In-text, near Exercise 1.1.3, book p. 12"),
        # found by the grammar whether or not the tag also gives a page
        refs = cf_items.find_items(half, self.numbered_kinds, limit=1)
        if refs:
            number = ".".join(str(n) for n in refs[0].number_levels)
            near_item = self.numbered_item(refs[0].kind, number)
        # the kind is the tag's own single word ("definition", "convention"); positional prose is not a kind
        if re.fullmatch(r"[A-Za-z][A-Za-z-]*", qualifier):
            kind, kind_source = qualifier, "producer"
        label = None
        disp = re.search(r",\s*display\s*(\([^()]+\))", half)
        if disp and not common.has_url(disp.group(1)):
            label = disp.group(1)
        anchor_kind = "own"
        basis_note = None
        runs = quoted_runs(d.doc)
        pdf = self.pdf
        if page is None:
            if near_item is None:
                self.report.problem(f"in-text passage with neither page nor reference: {half!r} {did}")
                return
            it = self.items[near_item]
            heads = pdf.headings(it["kind"], it["number"])
            if not heads:
                self.report.problem(f"no heading found for the reference of {raw!r}")
                return
            page = heads[0][0] - self.offset
            last = None
            anchor_kind = "inferred"
            basis_note = f"heading of {it['kind']} {it['number']}"
            self.anchored_in_text.append({"decl": did, "raw": raw, "near": near_item,
                                          "heading_physical_pages": [h[0] for h in heads]})
            here = any(pdf.find_prose(page + self.offset, r) for r in runs)
            if not here and any(pdf.find_prose(page + self.offset + 1, r) for r in runs):
                page += 1
                basis_note += ", moved to the next page where the docstring's quote is verified"
        physical = page + self.offset
        # an own anchor is verified over its whole page range; an inferred one was placed on its page above
        span = range(physical, (last if last and last > page else page) + self.offset + 1)
        hit = pdf.first_verified(list(span) if anchor_kind == "own" else [physical], runs)
        quote = None
        if hit:
            physical, quote = hit
        if runs:
            self.passage_quote_stats["docstring_quotes"] += 1
            if quote:
                self.passage_quote_stats["verified_on_anchor_page" if anchor_kind == "own"
                                         else "verified_near_referenced_heading"] += 1
            else:
                misses = []
                for run in runs:
                    pdf.last_miss = None
                    pdf.find_prose(physical, run)
                    misses.append(pdf.last_miss)
                near = [p for p in (physical - 1, physical + 1) for run in runs if pdf.find_prose(p, run)]
                verdict = ("found_only_on_adjacent_page" if near
                           else "ambiguous_on_page" if "ambiguous" in misses else "not_found")
                self.passage_quote_stats[verdict] += 1
                self.quote_misses.append({"decl": did, "printed_page": page, "verdict": verdict, "runs": runs})
        pages = {"first": page, "basis": "printed", "anchor": anchor_kind}
        if anchor_kind == "own":
            if last and last != page:
                pages["last"] = last
            if page_raw:
                pages["raw"] = page_raw
        if quote:
            iid, id_source = ids.passage_quote_id(quote["exact"]), "quote_hash"
        else:
            iid, id_source = ids.passage_page_id(page, title), "page_title_hash"
        rec = {"id": iid, "id_source": id_source, "form": "passage", "source": self.source_id,
               "kind": kind, "title": title, "title_source": "producer", "pages": pages}
        if kind_source:
            rec["kind_source"] = kind_source
        if label:
            rec["label"] = label
        # the quote is verified within its page only, so it stays on the page's location
        # (section 5.2: the quote_hash comes from the item's first location's TextQuoteSelector)
        if near_item:
            rec["relations"] = {"near": [near_item]}
        if iid in self.items:
            self.passage_merges.append({"id": iid, "decl": did, "kept_title": self.items[iid]["title"],
                                        "dropped_title": title, "same_pages": self.items[iid]["pages"] == pages})
            if self.items[iid]["pages"] != pages or self.items[iid].get("relations") != rec.get("relations"):
                self.report.problem(f"passage {iid} merged with different pages or relations ({did})")
        else:
            self.items[iid] = rec
            self.passage_pages[page] += 1
        self.add_link(iid, "key", did, raw)
        selector = {"type": "FragmentSelector", "conformsTo": RFC8118, "value": f"page={physical}"}
        if quote:
            selector["refinedBy"] = quote
        lid = f"{iid}@p{physical:03d}"
        loc = self.locations.setdefault(lid, {
            "id": lid, "items": [iid],
            "target": {"source": self.source_id, "selector": [selector]},
            "pages": {"first": physical, "basis": "physical"},
            "provenance": PROV_TEXT if (quote or anchor_kind == "inferred") else PROV_TAG})
        if basis_note and "x_page_basis" not in loc:
            loc["x_page_basis"] = basis_note

    def link_location(self, lid: str, d: leanparse.Decl, pages: dict | None) -> list[str] | None:
        """A per-link location when a docstring quote is verified on the link's own page."""
        if pages is None:
            return None
        runs = quoted_runs(d.doc)
        if not runs:
            return None
        self.link_quote_stats["links_with_docstring_quotes"] += 1
        phys = [p + self.offset for p in range(pages["first"], pages.get("last", pages["first"]) + 1)]
        hit = self.pdf.first_verified(phys, runs)
        if not hit:
            self.link_quote_stats["not_verified"] += 1
            return None
        p, quote = hit
        self.link_quote_stats["verified"] += 1
        locid = f"{lid}@p{p:03d}"
        self.locations[locid] = {
            "id": locid, "items": [],
            "target": {"source": self.source_id, "selector": [
                {"type": "FragmentSelector", "conformsTo": RFC8118, "value": f"page={p}", "refinedBy": quote}]},
            "pages": {"first": p, "basis": "physical"}, "provenance": PROV_TEXT}
        return [locid]

    def build_links(self) -> None:
        for row in sorted(self.rows, key=lambda r: (r["file"], r["line"])):
            d = self.decl_by_loc[(row["file"], row["line"])]
            did = self.decl_id_at.get((row["file"], row["line"]))
            raw = row["item_raw"]
            if did is None:
                # a duplicate declaration id (reported by read_declarations) or an unjoined anonymous one
                self.report.problem(f"the tag {raw!r} at {row['file']}:{row['line']} names a declaration the "
                                    "document does not carry; no link was written")
                continue
            tag = self.adapter.tag_of(d.doc, self.adapter.item_kinds())
            anchor = itemtag.find_page_anchor(d.doc, tag, clause_window=self.clause_window, page_re=self.page_re)
            if (anchor.page, anchor.raw, anchor.source) != (row["page_anchor"], row["page_anchor_raw"],
                                                            row["anchor_source"]):
                self.report.problem(f"anchor re-read disagrees for {did}")
            halves = [h.strip() for h in raw.split(" / ")] if " / " in raw else [raw]
            if len(halves) > 1:
                self.split_tags.append({"decl": did, "raw": raw, "halves": halves})
            row_page_half = None
            if anchor.page is not None and len(halves) > 1:
                row_page_half = next((i for i, h in enumerate(halves) if self.page_of(h)[0] is not None), None)
            for hi, half in enumerate(halves):
                self.link_half(d, did, row, raw, half, halves, hi, anchor, row_page_half)

    def link_half(self, d, did, row, raw, half, halves, hi, anchor, row_page_half) -> None:
        # the row's page belongs to the half that names it (or to the whole tag)
        own_page = anchor.page is not None and (len(halves) == 1 or row_page_half in (None, hi))
        page = anchor.page if own_page else None
        last = anchor.last_page if own_page else None
        praw = anchor.raw if own_page else None
        psrc = anchor.source if own_page else None
        if not self.pages_in_range(did, raw, page, last):
            page = last = praw = psrc = None
        parsed, reject = cf_items.try_parse(half, self.all_kinds)
        if parsed is None:
            self.report.problem(f"tag half unparsed: {half!r} of {raw!r} ({reject.reason})")
            return
        if parsed.kind != cf_items.IN_TEXT:
            number = ".".join(str(n) for n in parsed.number_levels)
            iid = self.numbered_item(parsed.kind, number)
            sub = self.sub_of(did, raw, common.after_number(half, number))
            if len(halves) == 1 and sub != itemtag.collapse(row["sub"]).strip("()").replace(")(", "."):
                self.sub_fixes.append({"decl": did, "raw": raw, "adapter_sub": row["sub"], "sub": sub})
            pages = self.link_pages(page, last, praw, psrc) if psrc in ("own", "crossref") else None
            lid = f"{iid}/key/{did}" + (f"/{sub}" if sub else "")
            self.add_link(iid, "key", did, raw, sub, pages=pages, locations=self.link_location(lid, d, pages))
            return
        # In-text: "proof of <item>" is a helper of that numbered item
        m = re.search(r"\bproof of\s+(?P<ref>.+?)(?:,\s*book\b.*)?$", half)
        if m:
            ref, rej = cf_items.try_parse(m.group("ref"), self.numbered_kinds)
            if ref is None:
                self.report.problem(f"proof-of target unparsed: {half!r} ({rej.reason})")
                return
            number = ".".join(str(n) for n in ref.number_levels)
            iid = self.numbered_item(ref.kind, number)
            sub = self.sub_of(did, raw, common.after_number(m.group("ref"), number))
            pages = self.link_pages(page, last, praw, psrc) if psrc in ("own", "crossref") else None
            lid = f"{iid}/helper/{did}" + (f"/{sub}" if sub else "")
            self.add_link(iid, "helper", did, raw, sub, pages=pages,
                          locations=self.link_location(lid, d, pages))
            self.helper_rows.append({"decl": did, "raw": raw, "item": iid})
            return
        if page is None and len(halves) > 1:
            page, last, praw = self.page_of(half)
            if not self.pages_in_range(did, raw, page, last):
                page = last = praw = None
        self.add_passage(d, did, raw, half, page, last, praw)

    def sub_of(self, did: str, raw: str, after: str) -> str:
        """The link's sub-part, by the renderer both exporters share; a refused one is dropped and reported."""
        sub, _rest, refused = common.render_sub(after)
        if refused:
            self.report.problem(f"{did}: the sub-part {refused!r} of the tag {raw!r} is not one the format can "
                                "carry (docs/annotations.md section 3.2); the link was written without a sub")
        return sub

    def locate_headings(self) -> None:
        """A numbered item's location: its heading line in the text layer."""
        heading_stats: collections.Counter = collections.Counter()
        no_heading: list[str] = []
        from_links: list[str] = []
        vs: collections.Counter = collections.Counter()
        disagreements: list[dict] = []
        pdf = self.pdf
        for iid, it in self.items.items():
            if it["form"] != "numbered":
                continue
            heads = pdf.headings(it["kind"], it["number"])
            bare = False
            if not heads:
                heads = pdf.bare_headings(it["number"])
                bare = bool(heads)
            if not heads:
                heading_stats["no_heading"] += 1
                no_heading.append(iid)
                self.pages_from_key_link(iid, it, from_links)
                continue
            if bare:
                heading_stats["bare_number_heading"] += 1
                it["kind_source"] = "producer"               # the book prints no kind word
            heading_stats["one_heading" if len(heads) == 1 else "several_headings"] += 1
            physical, offset, length = heads[0]
            quote = pdf.quote_at(physical, offset, length)
            it["label"] = quote["exact"]
            book_title = pdf.heading_title(physical, offset + length)
            if book_title:
                it["title"] = book_title
                heading_stats["book_title_from_heading"] += 1
            selector = {"type": "FragmentSelector", "conformsTo": RFC8118, "value": f"page={physical}",
                        "refinedBy": quote}
            loc = {"id": f"{iid}@p{physical:03d}", "items": [iid],
                   "target": {"source": self.source_id, "selector": [selector]},
                   "pages": {"first": physical, "basis": "physical"}, "provenance": PROV_TEXT}
            if len(heads) > 1:
                loc["x_heading_candidates"] = [h[0] for h in heads]
            self.locations[loc["id"]] = loc
            printed = physical - self.offset
            for ln in self.links.values():
                if ln["item"] != iid or "pages" not in ln:
                    continue
                p = ln["pages"]
                if p["first"] <= printed <= p.get("last", p["first"]):
                    vs["heading_within_link_pages"] += 1
                else:
                    vs["heading_outside_link_pages"] += 1
                    disagreements.append({"link": ln["id"], "link_pages": [p["first"], p.get("last", p["first"])],
                                          "heading_printed_page": printed})
        self.report.counts["headings"] = dict(heading_stats)
        self.report.counts["headings"]["no_heading_items"] = no_heading
        if from_links:
            self.report.counts["headings"]["pages_from_key_link_items"] = from_links
        self.report.counts["headings"]["several_heading_items"] = {
            loc["id"]: loc["x_heading_candidates"] for loc in self.locations.values()
            if "x_heading_candidates" in loc}
        self.report.counts["link_pages_vs_heading"] = dict(vs)
        self.report.counts["link_pages_vs_heading_disagreements"] = disagreements

    def pages_from_key_link(self, iid: str, it: dict, from_links: list[str]) -> None:
        """A numbered item the text layer shows no heading for takes its pages from its earliest paged key link."""
        paged = sorted((ln for ln in self.links.values()
                        if ln["item"] == iid and ln["role"] == "key" and "pages" in ln),
                       key=lambda ln: (ln["pages"]["first"], ln["id"]))
        if not paged:
            return
        it["pages"] = dict(paged[0]["pages"])
        from_links.append(iid)

    def annotate_links(self) -> None:
        """Link notes from the tag's parenthetical; coverage for helper-only items; the item counts."""
        note_count = 0
        decl_of_id = {self.decl_id_at[(x.file, x.line)]: x for x in self.decls
                      if (x.file, x.line) in self.decl_id_at}
        for ln in self.links.values():
            d = decl_of_id[ln["declaration"]]
            if self.items[ln["item"]]["form"] != "numbered":
                continue
            paren = tag_parenthetical(d.doc, self.adapter.tag_of(d.doc, self.adapter.item_kinds()))
            note = link_note(paren, self.items[ln["item"]].get("title"), ln.get("pages", {}).get("raw"),
                             self.page_re)
            if common.has_url(note):
                self.report.problem(f"{ln['declaration']}: the note of {ln['id']} holds a URL, which I10 keeps "
                                    "out of every non-source field; the note was dropped")
                note = None
            if note:
                ln["note"] = note
                note_count += 1
        roles_by_item: dict[str, set[str]] = collections.defaultdict(set)
        for ln in self.links.values():
            roles_by_item[ln["item"]].add(ln["role"])
        helper_only = sorted(i for i, r in roles_by_item.items() if "key" not in r)
        for iid in helper_only:
            self.items[iid]["coverage"] = "support_only"
        located = {i for loc in self.locations.values() for i in loc["items"]}
        unanchored = [i for i, it in self.items.items()
                      if i not in located and "quote" not in it and "pages" not in it]
        unlinked = sorted(i for i in self.items if i not in roles_by_item)
        items = list(self.items.values())
        self.report.counts["items"] = {
            "total": len(items), "numbered": sum(1 for i in items if i["form"] == "numbered"),
            "passages": sum(1 for i in items if i["form"] == "passage"),
            "by_kind": dict(collections.Counter(i["kind"] for i in items)),
            "by_id_source": dict(collections.Counter(i["id_source"] for i in items)),
            "passage_pages_anchor": dict(collections.Counter(i["pages"]["anchor"] for i in items
                                                             if i["form"] == "passage")),
            "with_relations_near": sum(1 for i in items if "relations" in i),
            "with_label_display": sum(1 for i in items if i["form"] == "passage" and "label" in i),
            "kind_source_producer": sum(1 for i in items if i.get("kind_source") == "producer"),
            "helper_only_items_given_coverage_support_only": helper_only,
            "items_without_links": unlinked, "unanchored_items": unanchored}
        self.report.counts["passages"] = {
            "pages_with_more_than_one_passage": sum(1 for n in self.passage_pages.values() if n > 1),
            "passages_on_those_pages": sum(n for n in self.passage_pages.values() if n > 1),
            "max_passages_on_one_page": max(self.passage_pages.values(), default=0),
            "quote_check": dict(self.passage_quote_stats), "quote_misses": self.quote_misses,
            "merged_by_same_id": self.passage_merges, "anchored_without_page": self.anchored_in_text}
        self.report.counts["split_tags"] = self.split_tags
        self.report.counts["sub_fixes_over_adapter"] = self.sub_fixes
        self.report.counts["helper_links"] = self.helper_rows
        links = list(self.links.values())
        self.report.counts["links"] = {
            "total": len(links), "by_role": dict(collections.Counter(ln["role"] for ln in links)),
            "with_sub": sum(1 for ln in links if "sub" in ln),
            "with_pages": dict(collections.Counter(ln["pages"]["anchor"] for ln in links if "pages" in ln)),
            "with_locations": sum(1 for ln in links if "locations" in ln),
            "with_note": note_count, "with_relation": sum(1 for ln in links if "relation" in ln),
            "link_quote_check": dict(self.link_quote_stats)}
        if unanchored:
            self.report.problem(f"items anchored nowhere: {unanchored}")

    # -- the literate site -------------------------------------------------------------------
    def read_site_pin(self) -> None:
        pages = self.pin.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ExportError("the site page manifest lists no pages", hint="see tests/data/m3/README.md")
        self.page_hashes = sorted(({"path": p["path"], "sha256": p["sha256"]} for p in pages),
                                  key=lambda p: p["path"])
        self.site_sha = site_sha256(self.page_hashes)
        pinned = self.pin.get("site_sha256")
        if pinned and pinned != self.site_sha:
            raise ExportError(f"the page manifest's site_sha256 is {pinned[:12]}..., its pages give "
                              f"{self.site_sha[:12]}...")
        self.page_set = {p["path"] for p in self.page_hashes}
        self.site_path = str(self.pin.get("path") or "_site")
        self.page_cache: dict[str, str] = {}
        if self.site_dir is not None:
            by_path = {p["path"]: p["sha256"] for p in self.page_hashes}
            for rel, sha in by_path.items():
                f = self.site_dir / rel
                if not f.is_file():
                    raise ExportError(f"{self.site_dir} has no {rel}, which the page manifest pins")
                if sha256_bytes(f.read_bytes()) != sha:
                    raise ExportError(f"{self.site_dir}/{rel} is not the pinned page",
                                      hint="render the site from the pinned commit, or export without --site")
            extra = sorted(str(q.relative_to(self.site_dir)) for q in self.site_dir.rglob("*.html")
                           if str(q.relative_to(self.site_dir)) not in by_path)
            if extra:
                raise ExportError(f"{self.site_dir} holds pages the manifest does not pin: {extra[:3]}")

    def page_text(self, rel_dir: str) -> str | None:
        """The HTML of one module page when the site is on disk, else None."""
        if self.site_dir is None:
            return None
        if rel_dir not in self.page_cache:
            self.page_cache[rel_dir] = (self.site_dir / rel_dir / "index.html").read_text(encoding="utf-8")
        return self.page_cache[rel_dir]

    def build_site(self) -> None:
        self.read_site_pin()
        xref = json.loads(self.xref_bytes.decode("utf-8"))
        constants = xref["VersoHtml.constant"]["contents"]
        by_user: dict[tuple[str, str], dict] = {}
        for key, entries in constants.items():
            for e in entries:
                data = e.get("data") or {}
                by_user[(data.get("module", ""), data.get("userName", key))] = e
        stats: collections.Counter = collections.Counter()
        missing_on_page: list[dict] = []
        verified = self.site_dir is not None
        for did, rec in sorted(self.declarations.items()):
            href = rec["module"].replace(".", "/") + "/"
            if href + "index.html" not in self.page_set:
                stats["no_site_page"] += 1
                continue
            html = self.page_text(href)
            if rec["anonymous"]:
                src = (self.repo / rec["file"]).read_bytes()
                lines = src.split(b"\n")
                line_start = sum(len(ln) + 1 for ln in lines[:rec["line"] - 1])
                kw_off = src.index(rec["kind"].encode(), line_start)
                binding = f"kw-occ-Lean.Parser.Command.{rec['kind']}-{kw_off}"
                if html is not None:
                    n = html.count(f'data-binding="{binding}"')
                    if n != 1:
                        stats[f"anonymous_binding_count_{n}"] += 1
                        continue
                inner = {"type": "CssSelector", "value": f'[data-binding="{binding}"]'}
                self.destinations[f"{did}@site"] = {
                    "id": f"{did}@site", "declaration": did, "site": self.site_name, "href": href,
                    "resolution": "fallback",
                    "fallback_label": "module page (Verso gives an example or anonymous instance no anchor)"}
                stats["anonymous_located_by_keyword_binding" if verified
                      else "anonymous_located_unverified"] += 1
            else:
                entry = by_user.get((rec["module"], rec["name"]))
                if entry is None:
                    stats["named_missing_from_xref"] += 1
                    continue
                if entry["address"].strip("/") + "/" != href:
                    stats["xref_address_mismatch"] += 1
                    continue
                frag = entry["id"]
                if html is not None and html.count(f'id="{frag}"') != 1:
                    stats["xref_id_not_once_on_page"] += 1
                    missing_on_page.append({"decl": did, "xref_id": frag,
                                            "count_on_page": html.count(f'id="{frag}"')})
                    self.destinations[f"{did}@site"] = {
                        "id": f"{did}@site", "declaration": did, "site": self.site_name, "href": href,
                        "resolution": "fallback",
                        "fallback_label": "module page (xref.json names an anchor the page lacks)"}
                    continue
                if re.search(r"[\s#]", frag):
                    self.report.problem(f"xref id {frag!r} is not an RFC 3236 id value")
                    continue
                inner = {"type": "FragmentSelector", "conformsTo": RFC3236, "value": frag}
                self.destinations[f"{did}@site"] = {
                    "id": f"{did}@site", "declaration": did, "site": self.site_name, "href": href,
                    "fragment": frag, "resolution": "exact"}
                stats["named_located_by_xref_id" if verified else "named_located_unverified"] += 1
            lid = f"{did}@site"
            self.locations[lid] = {"id": lid, "items": [],
                                   "target": {"source": self.pin.get("source_id") or f"{self.name}-site",
                                              "selector": [{"type": "SitePageSelector", "path": href,
                                                            "refinedBy": inner}]},
                                   "provenance": PROV_SITE}
            rec["anchors"] = [lid]
        stats["module_pages_selected"] = len({d["href"] for d in self.destinations.values()})
        stats["html_pages_hashed"] = len(self.page_hashes)
        self.report.counts["site"] = dict(stats)
        self.report.counts["site"]["sha256"] = self.site_sha
        self.report.counts["site"]["missing_on_page"] = missing_on_page
        self.report.counts["site"]["checked_against_pages"] = verified
        if not verified:
            self.report.lose("site.anchors_unverified", len(self.destinations),
                             "the rendered site was not on disk, so no anchor was checked to occur once on its "
                             "page; the index and the page manifest were trusted (pass --site to check)")

    # -- unattributed ---------------------------------------------------------------------------
    def build_unattributed(self) -> None:
        tagged_ids = {ln["declaration"] for ln in self.links.values()}
        for (f, ln), did in self.decl_id_at.items():
            if did in tagged_ids:
                continue
            d = self.decl_by_loc[(f, ln)]
            doc = (d.doc or "").lstrip()
            signals = []
            if (f, ln) in self.reject_at:
                signals.append(f"tag refused by the item grammar ({self.reject_at[(f, ln)]['reason']}): "
                               f"{self.reject_at[(f, ln)]['raw']}")
            if not d.doc:
                signals.append("no docstring")
            claim = "unattributed"
            if re.search(r"\(infrastructure\b", doc):
                claim = "infrastructure"
                signals.append("docstring marker (infrastructure...)")
            elif re.search(r"\(API\)", doc):
                claim = "api"
                signals.append("docstring marker (API)")
            rec = {"id": did, "declaration": did, "claim": claim}
            if signals:
                rec["signals"] = signals
            self.unattributed[did] = rec
        self.report.counts["unattributed"] = dict(collections.Counter(u["claim"] for u in self.unattributed.values()))

    # -- the document ------------------------------------------------------------------------------
    def run(self) -> dict:
        self.read_rows()
        self.read_declarations()
        pages_expected = common.registry_source(self.pdf_sha)
        if pages_expected is not None and pages_expected.pages and self.pdf.page_count() != pages_expected.pages:
            self.report.problem(f"pdftotext gave {self.pdf.page_count()} pages, the registry says "
                                f"{pages_expected.pages}")
        self.build_links()
        self.locate_headings()
        self.annotate_links()
        self.build_site()
        self.build_unattributed()
        pdf_source = {
            "id": self.source_id, "kind": "pdf", "role": "audited", "title": self.source_fields["title"],
            "sha256": self.pdf_sha, "bytes": self.pdf_bytes, "physical_page_count": self.pdf.page_count(),
            "printed_page_offset": self.offset,
            "text_layer": {"extractor": "pdftotext", "version": self.pdf.version,
                           "unicode": "nfkc", "whitespace": "collapse", "case": "sensitive"}}
        if "edition" in self.source_fields:
            pdf_source["edition"] = self.source_fields["edition"]
        if self.pdf.page_size_pt:
            pdf_source["page_size"] = {"width_pt": self.pdf.page_size_pt[0], "height_pt": self.pdf.page_size_pt[1]}
        else:
            self.report.lose("source.page_size", 1, "pdfinfo was not available, so the page box is not recorded")
        site_source = {
            "id": self.pin.get("source_id") or f"{self.name}-site", "kind": "web", "scope": "site",
            "role": "reference",
            "title": self.pin.get("title") or f"{self.name} literate site (local build, hosted nowhere)",
            "path": self.site_path, "sha256": self.site_sha, "page_hashes": self.page_hashes,
            "x_note": SITE_NOTE}
        if self.pin.get("rendered_at"):
            site_source["retrieved_at"] = self.pin["rendered_at"]
        self.report.bend("source.x_note", 1, "why the site is pinned by content and not by revision; a note, "
                         "not a field")
        lean_inputs = []
        for path in self.files:
            data = path.read_bytes()
            lean_inputs.append({"path": str(path.relative_to(self.repo)), "sha256": sha256_bytes(data),
                                "bytes": len(data), "commit": self.head})
        site_note = ("Anchor from the Verso cross-reference index (named declarations) or the keyword's "
                     "data-binding attribute (anonymous ones), checked to occur once on the pinned page."
                     if self.site_dir is not None else
                     "Anchor from the Verso cross-reference index (named declarations) or the keyword's "
                     "data-binding attribute (anonymous ones); the rendered pages were not on disk, so no "
                     "anchor was checked against its page.")
        provenance = [
            {"id": PROV_TAG, "actor": "author", "method": "docstring_tag", "confirmed_by_human": False,
             "tool": {"name": "companion-faithfulness adapters.docstring_tags",
                      "version": f"{'template' if self.config_is_template else 'config'} {self.config_name}"},
             "inputs": sorted(lean_inputs, key=lambda r: r["path"]),
             "note": f"Book tag read from the declaration's docstring at {self.head[:7]}. "
                     "Whether a human reviewed each tag is not recorded in the repository."},
            {"id": PROV_TEXT, "actor": "tool", "method": "text_match", "confirmed_by_human": False,
             "tool": {"name": "pdftotext", "version": self.pdf.version},
             "inputs": [{"source": self.source_id}],
             "note": "Page found by matching the text layer: a numbered item's heading line, "
                     "a quote from the docstring found exactly once on the anchored page, or, "
                     "for a passage placed by a numbered neighbour, that neighbour's heading page."},
            {"id": PROV_SITE, "actor": "tool", "method": "site_index", "confirmed_by_human": False,
             "tool": {"name": "Verso literate site", "path": f"{self.site_path}/xref.json"},
             "inputs": [{"path": f"{self.site_path}/xref.json", "sha256": sha256_bytes(self.xref_bytes),
                         "bytes": len(self.xref_bytes)}],
             "note": site_note},
        ]
        doc = {
            "schema": vocab.ANNOTATION_SCHEMA, "schema_version": vocab.ANNOTATION_SCHEMA_VERSION,
            "companion": {"name": self.name, "repo": self.repo_id, "commit": self.head,
                          "generated_at": self.head_date, "generator": common.generator("docstring-tags")},
            "sources": [pdf_source, site_source],
            "items": list(self.items.values()), "declarations": list(self.declarations.values()),
            "links": list(self.links.values()), "provenance": provenance,
            "locations": list(self.locations.values()), "destinations": list(self.destinations.values()),
            "unattributed": list(self.unattributed.values()),
        }
        if self.changelog:
            doc["changelog"] = list(self.changelog.values())
        x_keys = collections.Counter(k for arr in doc.values() if isinstance(arr, list)
                                     for r in arr if isinstance(r, dict) for k in r if k.startswith("x_"))
        self.report.counts["output"] = {"counts": {k: len(v) for k, v in doc.items() if isinstance(v, list)},
                                        "x_keys": dict(sorted(x_keys.items()))}
        if x_keys.get("x_page_basis"):
            self.report.bend("location.x_page_basis", x_keys["x_page_basis"],
                             "how a passage's page was placed (the heading of the numbered neighbour it names, "
                             "moved to the next page where its docstring quote verifies) has no location field; "
                             "kept as x_page_basis")
        if x_keys.get("x_heading_candidates"):
            self.report.bend("location.x_heading_candidates", x_keys["x_heading_candidates"],
                             "a numbered item whose heading line the text layer shows on several pages is located "
                             "on the first; the physical pages of every candidate are kept as x_heading_candidates")
        self.report.counts["repo_head"] = self.head
        return doc


def export(repo: Path, commit: str, **options) -> tuple[dict, Report]:
    """The document and the report for one docstring-tagged companion checkout."""
    job = DocstringTagsExport(repo, commit, **options)
    doc = job.run()
    return doc, job.report
