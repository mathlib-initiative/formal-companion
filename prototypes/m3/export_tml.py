#!/usr/bin/env python3
"""Prototype exporter: tao-measure-lean -> lean-companion/annotations, schema_version "1.0".

Reads the companion READ-ONLY and writes one annotation document plus a stats
file next to this script.  It reuses the skill's own readers rather than
re-implementing them (imported from the worktree, never modified):

* ``adapters.docstring_tags`` (driven by the shipped template
  ``templates/adapters/docstring_tags.tao-measure-lean.yaml``) for the tag rows:
  item, sub, kind, page anchor (page, raw text, own/crossref/none), sorry class;
* ``adapters.leanparse`` for every declaration (tagged or not), its docstring,
  namespace, private flag and doc line;
* ``adapters.itemtag`` / ``cflib.items`` for tag spans and the item grammar;
* ``annotations.ids`` for the statement-hash ids and signatures of anonymous
  declarations, the section 5.1 header cut for named ones, and the section 5.2
  item ids (label, quote_hash, page_title_hash);
* ``annotations.validate`` for the canonical (RFC 8785 based) output bytes.

Outside inputs, all read-only:

* the published PDF (sha256 64498329...), text layer via ``pdftotext``: numbered
  item headings are located by a line-start text match and docstring quotes are
  verified against the page before a TextQuoteSelector is written;
* the Verso literate site ``_site/`` (git-ignored build output, hosted nowhere):
  ``xref.json`` for destinations, each module page's HTML for the anchors.  The
  site is ONE source (``kind: web, scope: site``) pinned by ``sha256`` (section
  3.1: the sha256 of the manifest of every HTML page, listed in ``page_hashes``)
  and ``path``; a location names its page with a ``SitePageSelector`` refined by an
  RFC 3236 HTML-id fragment (named declarations) or a ``CssSelector`` on the
  keyword's ``data-binding`` attribute (anonymous ones, which Verso gives no id).

Usage: python3 export_tml.py [--out PATH] [--stats PATH]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

SKILL = Path("/Users/jack/Desktop/cf-wt/annotations")
sys.path.insert(0, str(SKILL / "scripts"))

from adapters import itemtag, leanparse          # noqa: E402
from adapters.api import from_config              # noqa: E402
from annotations import ids as anon_ids           # noqa: E402
from annotations.validate import canonical_dumps, canonicalize, site_sha256  # noqa: E402
from cflib import items as cf_items               # noqa: E402
from cflib import vocab                           # noqa: E402
from cflib import yamlio                          # noqa: E402

REPO = Path("/Users/jack/Desktop/tao-measure-lean")
TEMPLATE = SKILL / "templates/adapters/docstring_tags.tao-measure-lean.yaml"
PDF = Path("/Users/jack/Desktop/measure_theory/.lake/official-pdf/gsm-126-tao5-measure-book.pdf")
PDF_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
PDF_PAGE_PT = (612, 792)                           # pdfinfo -box: MediaBox = CropBox on all 265 pages
PAGE_OFFSET = 16                                   # physical = printed + 16 (CLAUDE.md)
SITE = REPO / "_site"
HERE = Path(__file__).resolve().parent

PDF_SOURCE = "tao-gsm126-published"
SITE_SOURCE = "tml-site"
RFC8118 = vocab.CONFORMS_TO_PDF
RFC3236 = vocab.CONFORMS_TO_HTML

PROV_TAG = "prov-docstring-tag"
PROV_TEXT = "prov-pdf-text-match"
PROV_SITE = "prov-verso-site"

QUOTE_MIN_CHARS = 12      # a prose run shorter than this is not a usable anchor
CONTEXT_CHARS = 32        # prefix / suffix length on a TextQuoteSelector

#: kinds whose header has no ``:=`` cut (fields or constructors follow ``where`` / ``|``)
NO_HEADER_CUT = {"structure", "class", "inductive"}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def module_of(rel: str) -> str:
    return rel[:-len(".lean")].replace("/", ".")


def utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SUB_RANGE = re.compile(r"^\(([^()]+)\)\s*[\u2013\u2014-]\s*\(([^()]+)\)")
_SUB_GROUPS = re.compile(r"^((?:\([^()]+\))+)")


def sub_from_label(rest: str) -> str:
    """The sub-part written right after an item number, without parentheses.

    ``(ii)`` -> ``ii``; ``(i)(a)`` -> ``i.a``; a range ``(iv)\u2013(vi)`` -> ``iv-vi``
    (the adapter keeps only the first group of a range).
    """
    rest = rest.strip()
    m = _SUB_RANGE.match(rest)
    if m:
        return f"{m.group(1).strip()}-{m.group(2).strip()}"
    m = _SUB_GROUPS.match(rest)
    if not m:
        return ""
    return ".".join(p.strip() for p in re.findall(r"\(([^()]*)\)", m.group(1)) if p.strip())


def after_number(raw: str, number: str) -> str:
    i = raw.find(number)
    return raw[i + len(number):] if i >= 0 else ""


# ---------------------------------------------------------------------------
# the PDF text layer
# ---------------------------------------------------------------------------

_QUOTE_FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "“": '"', "\u201d": '"',
                             "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u00a0": " "})


def normalise_with_map(text: str) -> tuple[str, list[int]]:
    """NFKC per character, whitespace collapsed, quotes folded; with an index map back."""
    out: list[str] = []
    back: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        norm = unicodedata.normalize("NFKC", ch).translate(_QUOTE_FOLD)
        for c in norm:
            if c.isspace():
                if prev_space:
                    continue
                out.append(" ")
                back.append(i)
                prev_space = True
            else:
                out.append(c)
                back.append(i)
                prev_space = False
    return "".join(out), back


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


class PdfText:
    def __init__(self, path: Path):
        raw = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], check=True,
                             capture_output=True).stdout.decode("utf-8")
        pages = raw.split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        self.pages = pages                               # index 0 = physical page 1
        self.norm = [normalise_with_map(p) for p in pages]
        ver = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True)
        m = re.search(r"pdftotext version (\S+)", ver.stdout + ver.stderr)
        self.version = m.group(1) if m else "unknown"
        self.last_miss: str | None = None

    def page_count(self) -> int:
        return len(self.pages)

    def headings(self, kind: str, number: str) -> list[tuple[int, int, int]]:
        """(physical page, offset, length) of every line that OPENS with ``<kind> <number>``
        followed by a title parenthesis or a full stop: the item's own heading shape."""
        pat = re.compile(rf"(?m)^{re.escape(kind)}\s+{re.escape(number)}(?=\s+\(|\.(?:\s|$))")
        return [(i + 1, m.start(), m.end() - m.start()) for i, page in enumerate(self.pages)
                for m in pat.finditer(page)]

    def bare_headings(self, number: str) -> list[tuple[int, int, int]]:
        """Lines opening with ``<number>. `` and no kind word (how the book prints
        its numbered strategies, section 2.1)."""
        pat = re.compile(rf"(?m)^{re.escape(number)}\.(?=\s)")
        return [(i + 1, m.start(), m.end() - m.start()) for i, page in enumerate(self.pages)
                for m in pat.finditer(page)]

    def heading_title(self, physical: int, end: int) -> str | None:
        """The parenthesised title that follows a heading label, if any (balanced, <= 160 chars)."""
        page = self.pages[physical - 1]
        i = end
        while i < len(page) and page[i] in " \t\n":
            i += 1
        if i >= len(page) or page[i] != "(":
            return None
        depth = 0
        for j in range(i, min(len(page), i + 200)):
            if page[j] == "(":
                depth += 1
            elif page[j] == ")":
                depth -= 1
                if depth == 0:
                    title = collapse_ws(page[i + 1:j])
                    return title if 0 < len(title) <= 160 else None
        return None

    def quote_at(self, physical: int, start: int, length: int) -> dict:
        """TextQuoteSelector keys for a raw span of one page, in the declared text layer
        (NFKC, whitespace collapsed)."""
        page = self.pages[physical - 1]
        exact = collapse_ws(page[start:start + length])
        prefix = collapse_ws(page[max(0, start - 3 * CONTEXT_CHARS):start])[-CONTEXT_CHARS:]
        suffix = collapse_ws(page[start + length:start + length + 3 * CONTEXT_CHARS])[:CONTEXT_CHARS]
        out = {"type": "TextQuoteSelector", "exact": exact}
        if prefix:
            out["prefix"] = prefix + (" " if page[start - 1:start].isspace() else "")
        if suffix:
            out["suffix"] = (" " if page[start + length:start + length + 1].isspace() else "") + suffix
        return out

    def find_prose(self, physical: int, candidate: str) -> dict | None:
        """A TextQuoteSelector when ``candidate`` occurs exactly once on the page
        (under the text layer's normalisation), else None."""
        if not (1 <= physical <= len(self.pages)):
            return None
        needle, _ = normalise_with_map(candidate)
        needle = needle.strip()
        if len(needle) < QUOTE_MIN_CHARS:
            return None
        hay, back = self.norm[physical - 1]
        first = hay.find(needle)
        if first < 0 or hay.find(needle, first + 1) >= 0:
            self.last_miss = "absent" if first < 0 else "ambiguous"
            return None
        raw_start = back[first]
        raw_end = back[first + len(needle) - 1] + 1
        return self.quote_at(physical, raw_start, raw_end - raw_start)

    def first_verified(self, physicals, runs) -> tuple[int, dict] | None:
        for p in physicals:
            for run in runs:
                q = self.find_prose(p, run)
                if q:
                    return p, q
        return None


def quoted_runs(doc: str) -> list[str]:
    """Prose runs of the double-quoted passages of a docstring, longest first.

    Backticked Lean inside a quote is the companion's rendering of the book's
    formula, never the book's text, so it splits the quote into runs.
    """
    runs: list[str] = []
    for q in re.findall(r"[\"“]([^\"\u201d]{%d,})[\"\u201d]" % QUOTE_MIN_CHARS, doc or ""):
        for part in re.split(r"`[^`]*`|…|\.\.\.", q):
            part = re.sub(r"\s+", " ", part).strip(" ,;:")
            if len(part) >= QUOTE_MIN_CHARS and len(re.findall(r"[A-Za-z]{2,}", part)) >= 2:
                runs.append(part)
    return sorted(set(runs), key=lambda s: (-len(s), s))


def quote_record(sel: dict) -> dict:
    """``item.quote`` from a TextQuoteSelector (same keys minus ``type``)."""
    return {k: sel[k] for k in ("exact", "prefix", "suffix") if k in sel}


# ---------------------------------------------------------------------------
# docstring titles and notes
# ---------------------------------------------------------------------------

_ABBREV = re.compile(r"(?:\bpp?|\be\.g|\bi\.e|\bcf|\bvs|\bresp|\bfig|\bno|\bch|\bsec|\bEx|\bDef|\bThm)$",
                     re.I)


def first_sentence(doc: str, tag: itemtag.TagHit | None) -> str:
    text = itemtag.collapse(doc)
    if tag is not None:
        s, e = tag.start - 1, tag.end + 1              # the backticks around the tag
        text = text[:s] + text[e:]
        text = re.sub(r"\(\s*[;,:\u2014-]?\s*\)", "", text)   # "( )" left behind
        text = re.sub(r"\(\s*[;,]\s*", "(", text)
    text = re.sub(r"\s+", " ", text).strip(" .;:,\u2014-")
    for m in re.finditer(r"\.(?=\s|$)", text):
        before = text[:m.start()]
        if _ABBREV.search(before) or before.endswith(("book p", "book pp")):
            continue
        after = text[m.end():].lstrip()
        if after[:1].isdigit():
            continue
        text = before
        break
    text = re.sub(r"\(\s*[;,:\u2014-]+\s*", "(", text).strip(" .;:,")
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
    i = tag.end + 1                                    # past the closing backtick
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


def link_note(paren: str | None, book_title: str | None, page_raw: str | None) -> str | None:
    """A link note: what the tag's own parenthetical says beyond the book's title and the page.

    The parenthetical is split on ``;``; a segment that is the book title, or only the
    page anchor the link already carries in ``pages.raw``, is dropped; what is left, if
    anything, is the note (the companion's words about this declaration's share).
    """
    if not paren:
        return None
    keep = []
    for seg in (s.strip() for s in paren.split(";")):
        if not seg:
            continue
        if book_title and _fold(seg) == _fold(book_title):
            continue
        if page_raw and _fold(seg) == _fold(page_raw):
            continue
        if itemtag.PAGE_RE.fullmatch(seg):
            continue
        keep.append(seg)
    note = "; ".join(keep).strip()
    return note or None


# ---------------------------------------------------------------------------
# section 5.1 header cut for named declarations
# ---------------------------------------------------------------------------

class HeaderCutter:
    """``(signature, statement_sha256)`` for a declaration at (file, keyword line), by
    the section 5.1 rule (keyword up to the ``:=`` that ends the header, comments
    removed, whitespace collapsed), using ``annotations.ids``' own scanner."""

    def __init__(self):
        self.cache: dict[str, str] = {}
        self.skipped = collections.Counter()

    def code(self, rel: str) -> tuple[str, str]:
        if rel not in self.cache:
            code = anon_ids.strip_comments((REPO / rel).read_text(encoding="utf-8"))[0]
            self.cache[rel] = (code, anon_ids.blank_literals(code))
        return self.cache[rel]

    def cut(self, rel: str, line: int, kind: str) -> tuple[str, str] | None:
        if kind in NO_HEADER_CUT:
            self.skipped[f"kind_{kind}"] += 1
            return None
        code, scan = self.code(rel)
        pos = 0
        for _ in range(line - 1):
            pos = scan.index("\n", pos) + 1
        eol = scan.find("\n", pos)
        eol = len(scan) if eol < 0 else eol
        start = next((i for i in range(pos, eol) if anon_ids._token_at(scan, i, kind)), None)  # noqa: SLF001
        if start is None:
            self.skipped["keyword_not_on_line"] += 1
            return None
        # section 5.1's cut, the same as an anonymous declaration's; none when no := or where ends it
        sig = anon_ids.named_signature(code, scan, start, kind)
        if sig is None:
            self.skipped["no_header_end"] += 1
            return None
        return sig, anon_ids.statement_sha256(sig)


# ---------------------------------------------------------------------------
# the export
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "tao-measure-lean.annotations.json")
    ap.add_argument("--stats", type=Path, default=HERE / "tao-measure-lean.export-stats.json")
    args = ap.parse_args(argv)

    stats: dict = collections.OrderedDict()
    problems: list[str] = []
    commit = git("rev-parse", "HEAD")
    commit_time = git("log", "-1", "--format=%ct", commit)
    dirty = git("status", "--porcelain")
    if dirty:
        problems.append(f"working tree not clean: {dirty.splitlines()[:5]}")
    pdf_bytes = PDF.stat().st_size
    if sha256_file(PDF) != PDF_SHA:
        raise SystemExit(f"{PDF}: sha256 changed")

    # -- 1. the adapter's rows (tags) ---------------------------------------
    cfg = yamlio.read_yaml(TEMPLATE)
    cfg["repo"] = str(REPO)
    cfg["name"] = "tao-measure-lean"
    adapter = from_config(cfg, source="export_tml")
    binding = adapter.bind({PDF_SOURCE: PDF_SHA})
    rows, counts, rejects = adapter._rows(adapter.resolve_repo(), binding)   # noqa: SLF001
    stats["adapter_counts"] = dict(sorted(counts.items()))
    stats["adapter_rejects"] = rejects
    rows_at = {(r["file"], r["line"]): r for r in rows}
    reject_at = {(r["file"], r["line"]): r for r in rejects}
    page_re = adapter.page_pattern() or itemtag.PAGE_RE
    clause_window = int(adapter.get("tag.clause_window", 140))
    numbered_kinds = adapter.numbered_kinds(PDF_SOURCE)
    all_kinds = adapter.item_kinds(PDF_SOURCE)

    # -- 2. every declaration, via the same parser --------------------------
    decls: list[leanparse.Decl] = []
    files = adapter.source_files(REPO)
    for path in files:
        rel = str(path.relative_to(REPO))
        decls += list(leanparse.parse_file(path, rel=rel, decl_kinds=adapter.decl_kinds(),
                                           max_body_lines=int(adapter.get("max_body_lines", 40))))
    stats["declarations_parsed"] = len(decls)

    # -- 3. anonymous ids ----------------------------------------------------
    scanned: list[anon_ids.AnonymousDecl] = []
    for path in files:
        rel = str(path.relative_to(REPO))
        scanned += anon_ids.scan_file(path.read_text(encoding="utf-8"), rel)
    assigned = anon_ids.assign_ids(scanned)
    anon_at = {(a.file, a.line): (a, *assigned[a]) for a in scanned}
    anon_decls = [d for d in decls if d.anonymous]
    unmatched = [(d.file, d.line) for d in anon_decls if (d.file, d.line) not in anon_at]
    extra = [k for k in anon_at if k not in {(d.file, d.line) for d in anon_decls}]
    if unmatched or extra:
        problems.append(f"anonymous join: leanparse-only {unmatched}, ids.py-only {extra}")
    ordinals = anon_ids.ordinal_ids(assigned)
    if ordinals:
        problems.append(f"~k ordinals need ordinal_assigned changelog records: {[o[0] for o in ordinals]}")
    stats["anonymous"] = {"leanparse": len(anon_decls), "ids_py": len(scanned),
                          "by_keyword": dict(collections.Counter(a.keyword for a in scanned)),
                          "by_id_source": dict(collections.Counter(v[1] for v in assigned.values())),
                          "with_ordinal": len(ordinals),
                          "untyped": sum(1 for a in scanned if not a.typed),
                          "rejected_tags": [(a.file, a.line, a.rejected_tag) for a in scanned if a.rejected_tag]}

    gap_re = adapter.gap_marker()
    exercise_permanent = adapter.exercise_sorry_is_permanent()
    cutter = HeaderCutter()

    declarations: dict[str, dict] = {}
    decl_id_at: dict[tuple[str, int], str] = {}
    for d in decls:
        module = module_of(d.file)
        rec: dict = {"file": d.file, "line": d.line, "module": module, "kind": d.kind,
                     "namespace": d.namespace or "",
                     "private": bool(d.private), "anonymous": bool(d.anonymous)}
        if d.doc_line:
            rec["doc_line"] = d.doc_line
        if d.anonymous:
            a, rid, source = anon_at[(d.file, d.line)]
            rec.update(id=rid, id_source=source, statement_sha256=a.statement_sha256,
                       signature=a.signature)
        else:
            if d.private:
                rec.update(id=anon_ids.private_id(module, d.full_name), id_source="private",
                           name=d.full_name)
            else:
                rec.update(id=d.full_name, id_source="name", name=d.full_name)
            got = cutter.cut(d.file, d.line, d.kind)
            if got is not None:
                rec["signature"], rec["statement_sha256"] = got
        _has, sorry_class = adapter._classify_sorry(d.body, gap_re, exercise_permanent)  # noqa: SLF001
        row = rows_at.get((d.file, d.line))
        if row is not None and row["sorry_class"] != sorry_class:
            problems.append(f"sorry class disagrees for {rec['id']}: {row['sorry_class']} vs {sorry_class}")
        if row is not None and row["namespace"] != rec["namespace"]:
            problems.append(f"namespace disagrees for {rec['id']}: {row['namespace']!r} vs {rec['namespace']!r}")
        rec["sorry"] = sorry_class
        if rec["id"] in declarations:
            problems.append(f"duplicate declaration id {rec['id']} ({d.file}:{d.line})")
            continue
        declarations[rec["id"]] = rec
        decl_id_at[(d.file, d.line)] = rec["id"]
    stats["declarations"] = {
        "total": len(declarations),
        "by_id_source": dict(collections.Counter(r["id_source"] for r in declarations.values())),
        "sorry": dict(collections.Counter(r["sorry"] for r in declarations.values())),
        "with_signature": sum(1 for r in declarations.values() if "signature" in r),
        "named_with_signature": sum(1 for r in declarations.values()
                                    if "signature" in r and not r["anonymous"]),
        "signature_skipped": dict(cutter.skipped),
        "root_namespace": sum(1 for r in declarations.values() if r["namespace"] == ""),
    }

    # -- 4. the PDF ----------------------------------------------------------
    pdf = PdfText(PDF)
    if pdf.page_count() != 265:
        problems.append(f"pdftotext gave {pdf.page_count()} pages, pdfinfo says 265")

    # -- 5. items and links --------------------------------------------------
    items: dict[str, dict] = {}
    links: dict[str, dict] = {}
    locations: dict[str, dict] = {}
    passage_quote_stats = collections.Counter()
    passage_pages: collections.Counter = collections.Counter()
    passage_merges = []
    helper_rows = []
    quote_misses = []
    anchored_in_text = []
    split_tags = []
    sub_fixes = []
    link_quote_stats = collections.Counter()

    def numbered_item(kind: str, number: str) -> str:
        iid = anon_ids.item_label_id(kind, number)
        if iid not in items:
            items[iid] = {"id": iid, "id_source": "label", "form": "numbered", "source": PDF_SOURCE,
                          "kind": kind, "number": number, "label": f"{kind} {number}"}
        return iid

    def link_pages(page: int | None, last: int | None, raw: str | None, source: str) -> dict | None:
        if page is None:
            return None
        out = {"first": page, "basis": "printed", "anchor": source}
        if last and last != page:
            out["last"] = last
        if raw:
            out["raw"] = raw
        return out

    def add_link(item: str, role: str, decl: str, raw: str, sub: str = "", **extra) -> str:
        lid = f"{item}/{role}/{decl}" + (f"/{sub}" if sub else "")
        rec = {"id": lid, "item": item, "declaration": decl, "role": role,
               "provenance": PROV_TAG, "raw_label": raw}
        if sub:
            rec["sub"] = sub
        rec.update({k: v for k, v in extra.items() if v is not None})
        if lid in links:
            problems.append(f"duplicate link {lid}")
        links[lid] = rec
        return lid

    def page_of(text: str) -> tuple[int | None, int | None, str | None]:
        m = page_re.search(text)
        if not m:
            return None, None, None
        last = int(m.group(2)) if (m.lastindex or 0) >= 2 and m.group(2) else None
        return int(m.group(1)), last, m.group(0).strip()

    def add_passage(d, did: str, raw: str, half: str, page: int | None, last: int | None,
                    page_raw: str | None) -> None:
        """One passage per In-text tag (or tag half), keyed by the section 5.2 rule."""
        tag = itemtag.find_tag(d.doc, adapter.item_kinds())
        title = first_sentence(d.doc, tag) or f"In-text passage formalised by {did}"
        parsed = cf_items.try_parse(half, all_kinds)[0]
        qualifier = (parsed.qualifier or "").strip() if parsed else ""
        near_item = None
        kind, kind_source = "passage", None
        # the parsed id names a numbered neighbour ("In-text Exercise 1.2.13") or a page
        # ("In-text p.27"); the qualifier is the tag's own word ("definition") or, with no
        # word, the neighbour again ("In-text, Remark 1.4.33")
        ref_label = re.sub(r"^In-text\s+", "", parsed.id) if parsed else ""
        ref_parsed, _ = cf_items.try_parse(ref_label, numbered_kinds)
        if ref_parsed is not None:
            number = ".".join(str(n) for n in ref_parsed.number_levels)
            near_item = numbered_item(ref_parsed.kind, number)
        if qualifier and cf_items.try_parse(qualifier, numbered_kinds)[0] is None:
            kind, kind_source = qualifier, "producer"
        label = None
        disp = re.search(r",\s*display\s*(\([^()]+\))", half)
        if disp:
            label = disp.group(1)
        anchor_kind = "own"
        basis_note = None
        runs = quoted_runs(d.doc)
        if page is None:
            if near_item is None:
                problems.append(f"in-text passage with neither page nor reference: {half!r} {did}")
                return
            it = items[near_item]
            heads = pdf.headings(it["kind"], it["number"])
            if not heads:
                problems.append(f"no heading found for the reference of {raw!r}")
                return
            page = heads[0][0] - PAGE_OFFSET
            anchor_kind = "inferred"
            basis_note = f"heading of {it['kind']} {it['number']}"
            anchored_in_text.append({"decl": did, "raw": raw, "near": near_item,
                                     "heading_physical_pages": [h[0] for h in heads]})
            here = any(pdf.find_prose(page + PAGE_OFFSET, r) for r in runs)
            if not here and any(pdf.find_prose(page + PAGE_OFFSET + 1, r) for r in runs):
                page += 1
                basis_note += ", moved to the next page where the docstring's quote is verified"
        physical = page + PAGE_OFFSET
        candidates = [physical] if anchor_kind == "own" else [physical, physical + 1]
        hit = pdf.first_verified(candidates, runs)
        quote = None
        if hit:
            cand, quote = hit
            if cand != physical:
                basis_note = (basis_note or "") + f", quote verified on the next page ({cand})"
                physical = cand
        if runs:
            passage_quote_stats["docstring_quotes"] += 1
            if quote:
                passage_quote_stats["verified_on_anchor_page" if anchor_kind == "own"
                                    else "verified_near_referenced_heading"] += 1
            else:
                misses = []
                for run in runs:
                    pdf.last_miss = None
                    pdf.find_prose(physical, run)
                    misses.append(pdf.last_miss)
                near = [p for p in (physical - 1, physical + 1)
                        for run in runs if pdf.find_prose(p, run)]
                verdict = ("found_only_on_adjacent_page" if near
                           else "ambiguous_on_page" if "ambiguous" in misses else "not_found")
                passage_quote_stats[verdict] += 1
                quote_misses.append({"decl": did, "printed_page": page, "verdict": verdict, "runs": runs})
        pages = {"first": page, "basis": "printed", "anchor": anchor_kind}
        if anchor_kind == "own":
            if last and last != page:
                pages["last"] = last
            if page_raw:
                pages["raw"] = page_raw
        if quote:
            iid, id_source = anon_ids.passage_quote_id(quote["exact"]), "quote_hash"
        else:
            iid, id_source = anon_ids.passage_page_id(page, title), "page_title_hash"
        rec = {"id": iid, "id_source": id_source, "form": "passage", "source": PDF_SOURCE,
               "kind": kind, "title": title, "title_source": "producer", "pages": pages}
        if kind_source:
            rec["kind_source"] = kind_source
        if label:
            rec["label"] = label
        # the quote is verified within its page only, so it stays on the page's location
        # (section 5.2: the quote_hash comes from the item's first location's TextQuoteSelector)
        if near_item:
            rec["relations"] = {"near": [near_item]}
        if iid in items:
            passage_merges.append({"id": iid, "decl": did, "kept_title": items[iid]["title"],
                                   "dropped_title": title, "same_pages": items[iid]["pages"] == pages})
            if items[iid]["pages"] != pages or items[iid].get("relations") != rec.get("relations"):
                problems.append(f"passage {iid} merged with different pages or relations ({did})")
        else:
            items[iid] = rec
            passage_pages[page] += 1
        add_link(iid, "key", did, raw)
        selector = {"type": "FragmentSelector", "conformsTo": RFC8118, "value": f"page={physical}"}
        if quote:
            selector["refinedBy"] = quote
        lid = f"{iid}@p{physical:03d}"
        loc = locations.setdefault(lid, {
            "id": lid, "items": [iid],
            "target": {"source": PDF_SOURCE, "selector": [selector]},
            "pages": {"first": physical, "basis": "physical"},
            "provenance": PROV_TEXT if (quote or anchor_kind == "inferred") else PROV_TAG})
        if basis_note and "x_page_basis" not in loc:
            loc["x_page_basis"] = basis_note

    def link_location(lid: str, d, pages: dict | None) -> list[str] | None:
        """A per-link location when a docstring quote is verified on the link's own page."""
        if pages is None:
            return None
        runs = quoted_runs(d.doc)
        if not runs:
            return None
        link_quote_stats["links_with_docstring_quotes"] += 1
        phys = [p + PAGE_OFFSET for p in range(pages["first"], pages.get("last", pages["first"]) + 1)]
        hit = pdf.first_verified(phys, runs)
        if not hit:
            link_quote_stats["not_verified"] += 1
            return None
        p, quote = hit
        link_quote_stats["verified"] += 1
        locid = f"{lid}@p{p:03d}"
        locations[locid] = {
            "id": locid, "items": [],
            "target": {"source": PDF_SOURCE, "selector": [
                {"type": "FragmentSelector", "conformsTo": RFC8118, "value": f"page={p}",
                 "refinedBy": quote}]},
            "pages": {"first": p, "basis": "physical"}, "provenance": PROV_TEXT}
        return [locid]

    decl_by_loc = {(d.file, d.line): d for d in decls}
    for row in sorted(rows, key=lambda r: (r["file"], r["line"])):
        d = decl_by_loc[(row["file"], row["line"])]
        did = decl_id_at[(row["file"], row["line"])]
        raw = row["item_raw"]
        tag = itemtag.find_tag(d.doc, adapter.item_kinds())
        anchor = itemtag.find_page_anchor(d.doc, tag, clause_window=clause_window, page_re=page_re)
        if (anchor.page, anchor.raw, anchor.source) != (row["page_anchor"], row["page_anchor_raw"],
                                                        row["anchor_source"]):
            problems.append(f"anchor re-read disagrees for {did}")
        halves = [h.strip() for h in raw.split(" / ")] if " / " in raw else [raw]
        if len(halves) > 1:
            split_tags.append({"decl": did, "raw": raw, "halves": halves})
        row_page_half = None
        if anchor.page is not None and len(halves) > 1:
            row_page_half = next((i for i, h in enumerate(halves) if page_of(h)[0] is not None), None)
        for hi, half in enumerate(halves):
            # the row's page belongs to the half that names it (or to the whole tag)
            own_page = anchor.page is not None and (len(halves) == 1 or row_page_half in (None, hi))
            page = anchor.page if own_page else None
            last = anchor.last_page if own_page else None
            praw = anchor.raw if own_page else None
            psrc = anchor.source if own_page else None
            parsed, reject = cf_items.try_parse(half, all_kinds)
            if parsed is None:
                problems.append(f"tag half unparsed: {half!r} of {raw!r} ({reject.reason})")
                continue
            if parsed.kind != "In-text":
                number = ".".join(str(n) for n in parsed.number_levels)
                iid = numbered_item(parsed.kind, number)
                sub = sub_from_label(after_number(half, number))
                if len(halves) == 1 and sub != itemtag.collapse(row["sub"]).strip("()").replace(")(", "."):
                    sub_fixes.append({"decl": did, "raw": raw, "adapter_sub": row["sub"], "sub": sub})
                pages = link_pages(page, last, praw, psrc) if psrc in ("own", "crossref") else None
                lid = f"{iid}/key/{did}" + (f"/{sub}" if sub else "")
                add_link(iid, "key", did, raw, sub, pages=pages,
                         locations=link_location(lid, d, pages))
                continue
            # In-text: "proof of <item>" is a helper of that numbered item
            m = re.search(r"\bproof of\s+(?P<ref>.+?)(?:,\s*book\b.*)?$", half)
            if m:
                ref, rej = cf_items.try_parse(m.group("ref"), numbered_kinds)
                if ref is None:
                    problems.append(f"proof-of target unparsed: {half!r} ({rej.reason})")
                    continue
                number = ".".join(str(n) for n in ref.number_levels)
                iid = numbered_item(ref.kind, number)
                sub = sub_from_label(after_number(m.group("ref"), number))
                pages = link_pages(page, last, praw, psrc) if psrc in ("own", "crossref") else None
                lid = f"{iid}/helper/{did}" + (f"/{sub}" if sub else "")
                add_link(iid, "helper", did, raw, sub, pages=pages,
                         locations=link_location(lid, d, pages))
                helper_rows.append({"decl": did, "raw": raw, "item": iid})
                continue
            if page is None and len(halves) > 1:
                page, last, praw = page_of(half)
            add_passage(d, did, raw, half, page, last, praw)

    # numbered items: a location from the heading
    heading_stats = collections.Counter()
    no_heading = []
    link_pages_vs_heading = collections.Counter()
    disagreements = []
    for iid, it in items.items():
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
        selector = {"type": "FragmentSelector", "conformsTo": RFC8118,
                    "value": f"page={physical}", "refinedBy": quote}
        loc = {"id": f"{iid}@p{physical:03d}", "items": [iid],
               "target": {"source": PDF_SOURCE, "selector": [selector]},
               "pages": {"first": physical, "basis": "physical"},
               "provenance": PROV_TEXT}
        if len(heads) > 1:
            loc["x_heading_candidates"] = [h[0] for h in heads]
        locations[loc["id"]] = loc
        printed = physical - PAGE_OFFSET
        for ln in links.values():
            if ln["item"] != iid or "pages" not in ln:
                continue
            p = ln["pages"]
            if p["first"] <= printed <= p.get("last", p["first"]):
                link_pages_vs_heading["heading_within_link_pages"] += 1
            else:
                link_pages_vs_heading["heading_outside_link_pages"] += 1
                disagreements.append({"link": ln["id"], "link_pages": [p["first"], p.get("last", p["first"])],
                                      "heading_printed_page": printed})

    # link notes: the tag's own parenthetical, minus the book's title and the page
    note_count = 0
    decl_of_id = {decl_id_at[(x.file, x.line)]: x for x in decls if (x.file, x.line) in decl_id_at}
    for ln in links.values():
        d = decl_of_id[ln["declaration"]]
        if items[ln["item"]]["form"] != "numbered":
            continue
        paren = tag_parenthetical(d.doc, itemtag.find_tag(d.doc, adapter.item_kinds()))
        note = link_note(paren, items[ln["item"]].get("title"), ln.get("pages", {}).get("raw"))
        if note:
            ln["note"] = note
            note_count += 1

    # I5: an item whose links are all helpers needs coverage
    roles_by_item = collections.defaultdict(set)
    for ln in links.values():
        roles_by_item[ln["item"]].add(ln["role"])
    helper_only = sorted(i for i, r in roles_by_item.items() if "key" not in r)
    for iid in helper_only:
        items[iid]["coverage"] = "support_only"
    located = {i for loc in locations.values() for i in loc["items"]}
    unanchored = [i for i, it in items.items() if i not in located and "quote" not in it and "pages" not in it]
    unlinked = sorted(i for i in items if i not in roles_by_item)

    stats["items"] = {"total": len(items),
                      "numbered": sum(1 for i in items.values() if i["form"] == "numbered"),
                      "passages": sum(1 for i in items.values() if i["form"] == "passage"),
                      "by_kind": dict(collections.Counter(i["kind"] for i in items.values())),
                      "by_id_source": dict(collections.Counter(i["id_source"] for i in items.values())),
                      "passage_pages_anchor": dict(collections.Counter(
                          i["pages"]["anchor"] for i in items.values() if i["form"] == "passage")),
                      "with_relations_near": sum(1 for i in items.values() if "relations" in i),
                      "with_label_display": sum(1 for i in items.values()
                                                if i["form"] == "passage" and "label" in i),
                      "kind_source_producer": sum(1 for i in items.values()
                                                  if i.get("kind_source") == "producer"),
                      "helper_only_items_given_coverage_support_only": helper_only,
                      "items_without_links": unlinked,
                      "unanchored_items": unanchored}
    stats["headings"] = dict(heading_stats)
    stats["headings"]["no_heading_items"] = no_heading
    stats["headings"]["several_heading_items"] = {
        l["id"]: l["x_heading_candidates"] for l in locations.values() if "x_heading_candidates" in l}
    stats["link_pages_vs_heading"] = dict(link_pages_vs_heading)
    stats["link_pages_vs_heading_disagreements"] = disagreements
    stats["passages"] = {"pages_with_more_than_one_passage": sum(1 for n in passage_pages.values() if n > 1),
                         "passages_on_those_pages": sum(n for n in passage_pages.values() if n > 1),
                         "max_passages_on_one_page": max(passage_pages.values(), default=0),
                         "quote_check": dict(passage_quote_stats),
                         "quote_misses": quote_misses,
                         "merged_by_same_id": passage_merges,
                         "anchored_without_page": anchored_in_text}
    stats["split_tags"] = split_tags
    stats["sub_fixes_over_adapter"] = sub_fixes
    stats["helper_links"] = helper_rows
    stats["links"] = {"total": len(links),
                      "by_role": dict(collections.Counter(l["role"] for l in links.values())),
                      "with_sub": sum(1 for l in links.values() if "sub" in l),
                      "with_pages": dict(collections.Counter(l["pages"]["anchor"] for l in links.values()
                                                            if "pages" in l)),
                      "with_locations": sum(1 for l in links.values() if "locations" in l),
                      "with_note": note_count,
                      "with_relation": sum(1 for l in links.values() if "relation" in l),
                      "link_quote_check": dict(link_quote_stats)}

    # -- 6. the literate site ----------------------------------------------
    xref_path = SITE / "xref.json"
    xref_bytes = xref_path.read_bytes()
    xref = json.loads(xref_bytes.decode("utf-8"))
    constants = xref["VersoHtml.constant"]["contents"]
    by_user: dict[tuple[str, str], dict] = {}
    for key, entries in constants.items():
        for e in entries:
            data = e.get("data") or {}
            by_user[(data.get("module", ""), data.get("userName", key))] = e
    destinations: dict[str, dict] = {}
    page_cache: dict[str, str] = {}
    page_sha: dict[str, str] = {}
    page_mtime: list[float] = []
    site_stats = collections.Counter()
    missing_on_page = []

    def site_page(module: str) -> str | None:
        rel = module.replace(".", "/") + "/"
        html = SITE / rel / "index.html"
        if not html.is_file():
            return None
        if rel not in page_cache:
            data = html.read_bytes()
            page_cache[rel] = data.decode("utf-8")
            page_sha[rel] = sha256_bytes(data)
            page_mtime.append(html.stat().st_mtime)
        return rel

    for did, rec in sorted(declarations.items()):
        href = site_page(rec["module"])
        if href is None:
            site_stats["no_site_page"] += 1
            continue
        html = page_cache[href]
        if rec["anonymous"]:
            src = (REPO / rec["file"]).read_bytes()
            lines = src.split(b"\n")
            line_start = sum(len(l) + 1 for l in lines[:rec["line"] - 1])
            kw_off = src.index(rec["kind"].encode(), line_start)
            binding_attr = f"kw-occ-Lean.Parser.Command.{rec['kind']}-{kw_off}"
            n = html.count(f'data-binding="{binding_attr}"')
            if n != 1:
                site_stats[f"anonymous_binding_count_{n}"] += 1
                continue
            inner = {"type": "CssSelector", "value": f'[data-binding="{binding_attr}"]'}
            destinations[f"{did}@site"] = {
                "id": f"{did}@site", "declaration": did, "site": "verso_literate", "href": href,
                "resolution": "fallback",
                "fallback_label": "module page (Verso gives an example or anonymous instance no anchor)"}
            site_stats["anonymous_located_by_keyword_binding"] += 1
        else:
            entry = by_user.get((rec["module"], rec["name"]))
            if entry is None:
                site_stats["named_missing_from_xref"] += 1
                continue
            if entry["address"].strip("/") + "/" != href:
                site_stats["xref_address_mismatch"] += 1
                continue
            frag = entry["id"]
            if html.count(f'id="{frag}"') != 1:
                site_stats["xref_id_not_once_on_page"] += 1
                missing_on_page.append({"decl": did, "xref_id": frag,
                                        "count_on_page": html.count(f'id="{frag}"')})
                destinations[f"{did}@site"] = {
                    "id": f"{did}@site", "declaration": did, "site": "verso_literate",
                    "href": href, "resolution": "fallback",
                    "fallback_label": "module page (xref.json names an anchor the page lacks)"}
                continue
            if re.search(r"[\s#]", frag):
                problems.append(f"xref id {frag!r} is not an RFC 3236 id value")
                continue
            inner = {"type": "FragmentSelector", "conformsTo": RFC3236, "value": frag}
            destinations[f"{did}@site"] = {
                "id": f"{did}@site", "declaration": did, "site": "verso_literate", "href": href,
                "fragment": frag, "resolution": "exact"}
            site_stats["named_located_by_xref_id"] += 1
        lid = f"{did}@site"
        locations[lid] = {"id": lid, "items": [],
                          "target": {"source": SITE_SOURCE, "selector": [
                              {"type": "SitePageSelector", "path": href, "refinedBy": inner}]},
                          "provenance": PROV_SITE}
        rec["anchors"] = [lid]
    # section 3.1: the site's sha256 is the sha256 of the manifest of EVERY HTML page
    all_pages = sorted(SITE.rglob("*.html"))
    page_hashes = []
    for html in all_pages:
        page_hashes.append({"path": str(html.relative_to(SITE)), "sha256": sha256_bytes(html.read_bytes())})
        page_mtime.append(html.stat().st_mtime)
    for rel, sha in page_sha.items():
        assert {"path": rel + "index.html", "sha256": sha} in page_hashes, rel
    site_sha = site_sha256(page_hashes)
    stats["site"] = dict(site_stats)
    stats["site"]["module_pages_selected"] = len(page_sha)
    stats["site"]["html_pages_hashed"] = len(page_hashes)
    stats["site"]["sha256"] = site_sha
    stats["site"]["missing_on_page"] = missing_on_page

    # -- 7. unattributed -----------------------------------------------------
    unattributed = {}
    tagged_ids = {l["declaration"] for l in links.values()}
    for (f, ln), did in decl_id_at.items():
        if did in tagged_ids:
            continue
        d = decl_by_loc[(f, ln)]
        doc = (d.doc or "").lstrip()
        signals = []
        if (f, ln) in reject_at:
            signals.append(f"tag refused by the item grammar ({reject_at[(f, ln)]['reason']}): "
                           f"{reject_at[(f, ln)]['raw']}")
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
        unattributed[did] = rec
    stats["unattributed"] = dict(collections.Counter(u["claim"] for u in unattributed.values()))

    # -- 8. the document -----------------------------------------------------
    sources = {
        PDF_SOURCE: {
            "id": PDF_SOURCE, "kind": "pdf", "role": "audited",
            "title": "An Introduction to Measure Theory (Terence Tao, AMS GSM 126)",
            "edition": "published pagination (PDF created 2011-02-25)",
            "sha256": PDF_SHA, "bytes": pdf_bytes, "physical_page_count": pdf.page_count(),
            "printed_page_offset": PAGE_OFFSET,
            "page_size": {"width_pt": PDF_PAGE_PT[0], "height_pt": PDF_PAGE_PT[1]},
            "text_layer": {"extractor": "pdftotext", "version": pdf.version,
                           "unicode": "nfkc", "whitespace": "collapse", "case": "sensitive"},
        },
        SITE_SOURCE: {
            "id": SITE_SOURCE, "kind": "web", "scope": "site", "role": "reference",
            "title": "tao-measure-lean literate site (local Verso build, hosted nowhere)",
            "path": "_site", "sha256": site_sha, "page_hashes": page_hashes,
            "retrieved_at": utc(max(page_mtime)),
            "x_note": ("_site is git-ignored build output; it is not provably built from "
                       "companion.commit, so it is pinned by content, not by revision"),
        },
    }
    lean_inputs = []
    for path in files:
        data = path.read_bytes()
        lean_inputs.append({"path": str(path.relative_to(REPO)), "sha256": sha256_bytes(data),
                            "bytes": len(data), "commit": commit})
    provenance = [
        {"id": PROV_TAG, "actor": "author", "method": "docstring_tag", "confirmed_by_human": False,
         "tool": {"name": "companion-faithfulness adapters.docstring_tags",
                  "version": "template docstring_tags.tao-measure-lean.yaml"},
         "inputs": sorted(lean_inputs, key=lambda r: r["path"]),
         "note": f"Book tag read from the declaration's docstring at {commit[:7]}. "
                 "Whether a human reviewed each tag is not recorded in the repository."},
        {"id": PROV_TEXT, "actor": "tool", "method": "text_match", "confirmed_by_human": False,
         "tool": {"name": "pdftotext", "version": pdf.version},
         "inputs": [{"source": PDF_SOURCE}],
         "note": "Page found by matching the text layer: a numbered item's heading line, "
                 "a quote from the docstring found exactly once on the anchored page, or, "
                 "for a passage placed by a numbered neighbour, that neighbour's heading page."},
        {"id": PROV_SITE, "actor": "tool", "method": "site_index", "confirmed_by_human": False,
         "tool": {"name": "Verso literate site", "path": "_site/xref.json"},
         "inputs": [{"path": "_site/xref.json", "sha256": sha256_bytes(xref_bytes),
                     "bytes": len(xref_bytes)}],
         "note": "Anchor from the Verso cross-reference index (named declarations) or the "
                 "keyword's data-binding attribute (anonymous ones), checked to occur once "
                 "on the pinned page."},
    ]
    doc = {
        "schema": vocab.ANNOTATION_SCHEMA,
        "schema_version": vocab.ANNOTATION_SCHEMA_VERSION,
        "companion": {"name": "tao-measure-lean", "repo": "github.com/mathlib-initiative/tao-measure-lean",
                      "commit": commit, "generated_at": utc(int(commit_time)),
                      "generator": "export_tml.py prototype 1.0"},
        "sources": list(sources.values()),
        "items": list(items.values()),
        "declarations": list(declarations.values()),
        "links": list(links.values()),
        "provenance": provenance,
        "locations": list(locations.values()),
        "destinations": list(destinations.values()),
        "unattributed": list(unattributed.values()),
    }
    doc = canonicalize(doc)
    text = canonical_dumps(doc)
    args.out.write_text(text, encoding="utf-8")
    x_keys = collections.Counter(k for arr in doc.values() if isinstance(arr, list)
                                 for r in arr if isinstance(r, dict) for k in r if k.startswith("x_"))
    stats["problems"] = problems
    stats["output"] = {"path": str(args.out), "bytes": len(text.encode("utf-8")),
                       "counts": {k: len(v) for k, v in doc.items() if isinstance(v, list)},
                       "x_keys": dict(sorted(x_keys.items()))}
    args.stats.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(stats["output"], indent=2))
    if problems:
        print("PROBLEMS:", *problems, sep="\n  ", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
