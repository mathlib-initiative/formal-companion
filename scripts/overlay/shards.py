"""Convert an annotation document into the overlay's Literature Links annotation files.

New code, implementing section 7 of the overlay contract (`docs/overlay.md`).
The exporters resolve a companion's curation into one annotation document
(`vendor/formal_companion_annotations`); the overlay reads Literature Links
1.0 (`vendor/literature_links`), one annotation per file, one folder per book
section.  :func:`convert` is the whole mapping:

* one record per (item, linked declaration), for every link role the viewer
  shows, and one record for an item that links nothing; the record's ``id``
  is the item's, so the records about one item share it;
* ``subject``: one ``pdf`` end per region of the item, in reading order: the
  PDF at its official URL with its ``sha256`` and a CSL ``citation``, the
  region's box as ``{page, rect}`` (``[x, y, width, height]`` fractions of the
  displayed page from the top left; a quad region becomes its bounding box,
  the quads kept in the extension) and the item's label as ``title``;
* ``object``: one ``lean`` end, the companion's repository at the export
  commit with the declaration (or, for an anonymous example, its module and
  the line it starts on) as ``location`` and the link's role as ``role``; an
  item that links nothing has ``"object": []``, or, when its coverage is
  ``not_formalized``, the repository at the commit with ``relation: noMatch``;
* ``relation`` from the link's relation token, ``kind``, ``status`` and
  ``note`` from the item and its coverage (a class that no recommended
  status names stays in the extension as ``coverage``);
* everything else the viewer shows under one extension, :data:`NAMESPACE`, on
  the end it describes or on the record.

:func:`layout` places the files, the same way on every OS: an item with
several records gets a folder; :func:`file_bytes` writes them with folder
defaults and end templates (`overlay.defaults`).
"""
from __future__ import annotations

import collections
import copy
import hashlib
import math
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from pathlib import Path, PurePosixPath

from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from literature_links import discovery

from overlay import defaults

__all__ = ["ANNOTATION_FILE", "DEFAULTS", "DIRECTORY", "ITEM_DIR", "NAMESPACE", "RELATIONS", "ROLES", "SECTION_DIR",
           "UNLOCATED_PROFILE", "Conversion", "annotation_bytes", "convert", "file_bytes", "file_names", "layout",
           "page_of", "scan_problems", "section_of", "verso_fragment", "walk"]

#: The one extension key every record carries (docs/overlay.md section 7).
NAMESPACE = "org.mathlib-initiative.formal-companion.overlay"
#: Where the annotation files live, relative to the companion root: one folder per
#: section, one ``<name>.json`` per annotation, one ``<item>/`` folder per item with
#: several, no name longer than :data:`MAX_NAME`; `_defaults.json` at the top and in item folders.
DIRECTORY = "annotations"
DEFAULTS = defaults.DEFAULTS
SECTION_DIR = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*\Z")
ITEM_DIR = re.compile(r"[0-9A-Za-z_-][0-9A-Za-z._-]{0,119}\Z")
ANNOTATION_FILE = re.compile(r"[0-9A-Za-z_-][0-9A-Za-z._-]{0,114}\.json\Z")
MAX_NAME = 120
#: How many hex digits of sha256 a hashed name ends with.
HASH_DIGITS = 10
#: Link roles in the order the viewer lists them.
ROLES = ("key", "attributed", "support", "example", "helper")
#: The page boxes whose normalized coordinates are the displayed page's.
DISPLAYED_BOXES = ("CropBox", "MediaBox")
#: The curation's coverage class as a Literature Links ``status``: only the format's recommended values.
#: ``example_only``, ``private_only`` and ``out_of_scope`` give no status. An item with no class is
#: ``formalized`` when it has a key link.
STATUS_OF_COVERAGE = {"formalized": "formalized", "partial_only": "partial", "not_formalized": "not_formalized",
                      "support_only": "not_formalized"}
#: The classes their status names alone; any other stays in the record's extension as ``coverage``.
NAMED_BY_STATUS = frozenset({"formalized", "partial_only", "not_formalized"})
#: A link's relation token as a Literature Links ``relation``.  ``uses`` and
#: ``supporting`` are tokens outside the format's core that companions write.
RELATIONS = {"exact": "exactMatch", "generalization": "broadMatch", "special_case": "narrowMatch",
             "part": "hasPart", "related": "relatedMatch", "invocation": "relatedMatch",
             "uses": "relatedMatch", "supporting": "relatedMatch"}
#: The tokens their ``relation`` names alone; any other token stays in the record's extension.
INJECTIVE = frozenset({"exact", "generalization", "special_case", "part"})
#: The location of an anonymous example the export could not place on a line:
#: ``{module}`` alone would select the whole module.
UNLOCATED_PROFILE = "urn:x-formal-companion:unlocated-example:v1"
#: The CSL type of a ``kind: pdf`` source.
CSL_TYPE = "book"

_CLUSTER_SECTION = re.compile(r"\bSection (\d+)\.(\d+)", re.ASCII)
_DOTTED = re.compile(r"(\d+)\.(\d+)", re.ASCII)
_HTTPS = re.compile(r"https://[^\s#\\]+\Z")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
#: Device names Windows reserves whatever the extension (`nul.json` is `NUL`).
_RESERVED = re.compile(r"(?:con|prn|aux|nul|com[0-9]|lpt[0-9])\Z", re.IGNORECASE)


@dataclass
class Conversion:
    """The records by section, their files, and what the conversion could not carry."""

    sections: dict[str, list[dict]] = field(default_factory=dict)
    #: ``{"<section>/[<item>/]<name>.json": record}`` in file order, each record as the reader reads it there.
    files: dict[str, dict] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    counts: collections.Counter = field(default_factory=collections.Counter)

    @property
    def records(self) -> list[dict]:
        return list(self.files.values())


# ---------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------

def _by_id(rows, key: str = "id") -> dict:
    out: dict = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get(key) and row[key] not in out:
            out[row[key]] = row
    return out


def _site_path(href: object) -> bool:
    """The viewer's relative-href rule: no scheme, no `..`, no leading `/`, no whitespace."""
    return (isinstance(href, str) and bool(href) and not re.search(r"[\s#:\\]", href)
            and not href.startswith("/") and ".." not in PurePosixPath(href).parts)


def repo_url(repo_id: object) -> str | None:
    """``https://host/org/name`` for a ``companion.repo`` id; None for a local checkout."""
    if not isinstance(repo_id, str) or "/" not in repo_id or repo_id.startswith("local/"):
        return None
    return f"https://{repo_id}"


def item_label(item: dict) -> str:
    """The item's printed label: kind, number, sub-part and title, as the book prints them."""
    if item.get("label"):
        return item["label"]
    if item.get("form") == "numbered" and item.get("number"):
        label = f"{item['kind']} {item['number']}" + (f"({item['sub']})" if item.get("sub") else "")
        return label + (f" ({item['title']})" if item.get("title") else "")
    return item.get("title") or item["id"]


def section_of(item: dict, clusters: dict) -> str:
    """The section folder an item's records go to.

    A number with three or more parts names its section (``1.2.7`` is in
    ``1.2``); otherwise the item's own ``section`` or a cluster locator that
    says ``Section N.M``; otherwise ``other`` (a two-part number such as
    ``3.4`` may be an equation or a section, so it names no section).
    """
    parts = str(item.get("number") or "").split(".")
    if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}.{parts[1]}"
    match = _DOTTED.search(str(item.get("section") or ""))
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    for cluster in item.get("clusters") or []:
        match = _CLUSTER_SECTION.search(str((clusters.get(cluster) or {}).get("locator") or ""))
        if match:
            return f"{match.group(1)}.{match.group(2)}"
    return "other"


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{value!r} is not a finite coordinate")
    return Decimal(repr(value))


def _number(value: Decimal) -> float:
    return float(value)


def _extent(start: float, end: Decimal) -> float:
    """``end - start`` as a float whose decimal sum with ``start`` never passes ``end``."""
    extent = float(end - Decimal(repr(start)))
    while extent > 0 and Fraction(repr(start)) + Fraction(repr(extent)) > Fraction(str(end)):
        extent = math.nextafter(extent, 0.0)
    return extent


def page_size(source: dict, page: int) -> tuple[Decimal, Decimal] | None:
    for entry in source.get("page_sizes") or []:
        if entry.get("first", 0) <= page <= entry.get("last", 0):
            return _decimal(entry["width_pt"]), _decimal(entry["height_pt"])
    size = source.get("page_size")
    return (_decimal(size["width_pt"]), _decimal(size["height_pt"])) if size else None


def region(selector: dict, source: dict) -> tuple[int, list[float], list[list[float]] | None]:
    """``(page, [x, y, width, height], quads or None)`` of one ``PdfRegionSelector``.

    Its coordinates must be of the displayed page box (CropBox, or MediaBox);
    ``pt`` coordinates are divided by the source's page size and a
    ``bottom_left`` origin is flipped, so the result is always top-left
    fractions.  The old selector carries no page rotation, so the page is
    taken as unrotated.
    """
    page = selector.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("a region names no physical page")
    space = selector.get("coordinate_space") or {}
    if space.get("page_box") not in DISPLAYED_BOXES:
        raise ValueError(f"page {page}: page box {space.get('page_box')!r} is not the displayed page")
    width = height = Decimal(1)
    if space.get("units") == "pt":
        size = page_size(source, page)
        if size is None:
            raise ValueError(f"page {page}: pt coordinates and no page size")
        width, height = size
    elif space.get("units") != "normalized":
        raise ValueError(f"page {page}: unknown units {space.get('units')!r}")
    flip = {"top_left": False, "bottom_left": True}.get(space.get("origin"))
    if flip is None:
        raise ValueError(f"page {page}: unknown origin {space.get('origin')!r}")

    def point(x: object, y: object) -> tuple[Decimal, Decimal]:
        fx, fy = _decimal(x) / width, _decimal(y) / height
        if width != 1 or height != 1:
            fx, fy = round(fx, 12), round(fy, 12)
        fy = 1 - fy if flip else fy
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            raise ValueError(f"page {page}: a corner leaves the page")
        return fx, fy

    quads = None
    if isinstance(selector.get("rect"), dict):
        r = selector["rect"]
        corners = [point(r["x0"], r["y0"]), point(r["x1"], r["y1"])]
    elif selector.get("quads"):
        quads, corners = [], []
        for quad in selector["quads"]:
            four = [point(quad[f"x{n}"], quad[f"y{n}"]) for n in range(4)]
            corners += four
            quads.append([_number(v) for corner in four for v in corner])
    else:
        raise ValueError(f"page {page}: a region with neither rect nor quads")
    x0, x1 = min(c[0] for c in corners), max(c[0] for c in corners)
    y0, y1 = min(c[1] for c in corners), max(c[1] for c in corners)
    x, y = _number(x0), _number(y0)
    rect = [x, y, _extent(x, x1), _extent(y, y1)]
    if rect[2] <= 0 or rect[3] <= 0:
        raise ValueError(f"page {page}: a region with no area")
    return page, rect, quads


def _regions(location: dict) -> list[dict]:
    """The ``PdfRegionSelector``s of the first selector alternative that has any."""
    def walk(node, found):
        if isinstance(node, dict):
            if node.get("type") == "PdfRegionSelector":
                found.append(node)
            for key in ("refinedBy", "startSelector", "endSelector"):
                walk(node.get(key), found)
        return found
    for alternative in (location.get("target") or {}).get("selector") or []:
        found = walk(alternative, [])
        if found:
            return found
    return []


# ---------------------------------------------------------------------------
# the records
# ---------------------------------------------------------------------------

def pdf_source(doc: dict, source_id: str | None) -> dict:
    """The document's ``kind: pdf`` source: the one named, or the only one."""
    sources = [s for s in doc.get("sources") or [] if isinstance(s, dict) and s.get("kind") == "pdf"]
    if source_id is not None:
        sources = [s for s in sources if s.get("id") == source_id]
    if len(sources) != 1:
        raise ValueError(f"the document has {len(sources)} pdf sources"
                         + (f" with id {source_id!r}" if source_id else "; name one with --source-id"))
    source = sources[0]
    for key in ("url", "sha256"):
        if not source.get(key):
            raise ValueError(f"source {source['id']!r} carries no {key}, which the overlay fetches and verifies by")
    return source


def csl_date(text: str) -> dict:
    """A CSL date: ``date-parts`` from a leading ISO date, and the text itself as ``raw`` when it says more."""
    match = re.match(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", text)
    out: dict = {}
    if match:
        out["date-parts"] = [[int(part) for part in match.groups() if part]]
    if not match or match.group(0) != text:
        out["raw"] = text
    return out


def pdf_end(source: dict) -> dict:
    """A subject end for the PDF, without a location: its official URL, sha256 and CSL citation.

    The extension carries what serve.py and the viewer need beside them.
    """
    citation = {"id": source["id"], "type": CSL_TYPE}
    for ours, theirs in (("title", "title"), ("edition", "edition"), ("url", "URL")):
        if source.get(ours):
            citation[theirs] = source[ours]
    if source.get("retrieved_at"):
        citation["accessed"] = csl_date(source["retrieved_at"])
    identifiers = dict(source.get("identifiers") or {})
    isbns = identifiers.pop("isbn", None) or []
    if isbns:
        citation["ISBN"] = isbns[0]
    if isinstance(identifiers.get("doi"), str):
        citation["DOI"] = identifiers.pop("doi")
    extra = {f"isbn-{n}": isbn for n, isbn in enumerate(isbns[1:], 2)}
    extra.update({k: v for k, v in identifiers.items() if isinstance(v, str)})
    end = {"type": "pdf", "uri": source["url"], "sha256": source["sha256"], "citation": citation}
    if extra:
        end["identifiers"] = dict(sorted(extra.items()))
    ext = {"source_id": source["id"]}
    for key in ("bytes", "physical_page_count", "alternate_urls"):
        if source.get(key):
            ext[key] = source[key]
    end["extensions"] = {NAMESPACE: ext}
    return end


def page_of(module: object) -> str | None:
    """A module's rendered page relative to the site root: ``A/B/C/index.html`` for ``A.B.C``."""
    return module.replace(".", "/") + "/index.html" if isinstance(module, str) and module else None


def verso_fragment(declaration: object) -> str | None:
    """The anchor a rendered page gives a declaration: its name with each ``.`` as ``___``."""
    return declaration.replace(".", "___") if isinstance(declaration, str) and declaration else None


def lean_location(decl: dict) -> dict:
    """The declaration by name; an anonymous one by its module and the line it starts on, else a profile."""
    module, line = decl.get("module"), decl.get("line")
    if decl.get("name"):
        out = {"declaration": decl["name"]}
        if module:
            out["module"] = module
        return out
    if module and isinstance(line, int) and not isinstance(line, bool) and line >= 1:
        return {"module": module, "lines": [line]}
    out = {"profile": UNLOCATED_PROFILE}
    if module:
        out["module"] = module
    out["id"] = decl["id"]
    return out


def _status(item: dict, has_key: bool) -> str | None:
    coverage = item.get("coverage")
    if coverage:
        return STATUS_OF_COVERAGE.get(coverage)
    return "formalized" if has_key else None


def provenance_of(record: object, *, note: bool = False) -> dict | None:
    """``{actor, method, confirmed_by_human}`` of a provenance record (and its ``note``), or None."""
    if not isinstance(record, dict):
        return None
    out = {k: record[k] for k in ("actor", "method", "confirmed_by_human") if k in record}
    if note and record.get("note"):
        out["note"] = record["note"]
    return out or None


def item_order(location: dict) -> list[str]:
    """A location's items in the order its curation names them.

    The format keeps ``items`` sorted; an exporter that knows the order the
    region's own label names them in records it as ``x_item_order``, used
    when it lists exactly the same items.
    """
    items = [i for i in location.get("items") or [] if isinstance(i, str)]
    order = location.get("x_item_order")
    if isinstance(order, list) and all(isinstance(i, str) for i in order) \
            and len(set(order)) == len(order) and sorted(order) == sorted(items):
        return list(order)
    return items


@dataclass
class _Region:
    """One region of the source, placed: what each item drawn in it needs for its subject end."""

    location: dict
    id: str
    page: int
    rect: list[float]
    quads: list | None
    order: list[str]


class _Converter:
    def __init__(self, doc: dict, source_id: str | None, directory: str) -> None:
        self.doc = doc
        self.out = Conversion()
        self.source = pdf_source(doc, source_id)
        self.pdf_end = pdf_end(self.source)
        companion = doc.get("companion") or {}
        self.commit = companion.get("commit")
        # Records are made for a file in <directory>/<section>/, this far below the companion root; then moved.
        self.up = "../" * (len(PurePosixPath(directory).parts) + 1)
        self.project = {"type": "lean", "uri": repo_url(companion.get("repo")) or self.up}
        if self.commit:
            self.project["version"] = self.commit
        self.declarations = _by_id(doc.get("declarations"))
        self.snippets = _by_id(doc.get("snippets"), "declaration")
        self.destinations = _by_id(doc.get("destinations"), "declaration")
        self.clusters = _by_id(doc.get("clusters"))
        self.provenance = _by_id(doc.get("provenance"))
        self.items = {i["id"]: i for i in doc.get("items") or [] if i.get("source") == self.source["id"]}
        self.links: dict[str, list[dict]] = collections.defaultdict(list)
        for link in doc.get("links") or []:
            if link.get("orphaned"):
                continue
            if not link.get("declaration"):
                self.out.counts["links_to_a_cluster_not_shown"] += 1
                continue
            self.links[link["item"]].append(link)
        for links in self.links.values():
            links.sort(key=lambda l: (ROLES.index(l["role"]) if l["role"] in ROLES else len(ROLES), l["id"]))
        self.verdicts: dict[str, list[dict]] = collections.defaultdict(list)
        for verdict in doc.get("verdicts") or []:
            self.verdicts[verdict["item"]].append(verdict)

    def destination(self, decl: dict, location: dict) -> dict | None:
        """The rendered page, as far as the end's location does not already give it, or None.

        A page is ``<site root>/<module as a path>/index.html``, and an exact
        one adds the declaration's anchor (:func:`page_of`, :func:`verso_fragment`):
        a destination they give keeps only its ``resolution`` (and
        ``fallback_label``), and one they do not give keeps its ``href``
        (relative to the site root, or an ``https:`` URL) and its ``fragment``.
        """
        dest = self.destinations.get(decl["id"])
        if dest is None:
            self.out.counts["declarations_without_a_rendered_page"] += 1
            return None
        href = dest.get("href")
        if not (isinstance(href, str) and _HTTPS.fullmatch(href) or _site_path(href)):
            self.out.problems.append(f"{decl['id']}: destination href {href!r} is neither a site path "
                                     "nor an https: URL; the card is unlinked")
            return None
        out = {"resolution": dest.get("resolution")}
        if dest.get("fallback_label"):
            out["fallback_label"] = dest["fallback_label"]
        if href != page_of(location.get("module")):
            out["href"] = href
            self.out.counts["destinations_with_their_own_page"] += 1
        if dest.get("resolution") == "exact" and dest.get("fragment") \
                and dest["fragment"] != verso_fragment(location.get("declaration")):
            out["fragment"] = dest["fragment"]
            self.out.counts["destinations_with_their_own_anchor"] += 1
        return out

    def lean_end(self, decl: dict, link: dict, verdicts: list[dict]) -> dict:
        """The object end of one link: the repository at the commit, the declaration in it, and its card."""
        end = dict(self.project, location=lean_location(decl), role=link["role"])
        if "profile" in end["location"]:
            self.out.counts["unlocated_anonymous_declarations"] += 1
        ext: dict = {"declaration": {"id": decl["id"]}}
        if decl.get("kind"):
            ext["declaration"]["kind"] = decl["kind"]
        for flag in ("private", "anonymous"):
            if decl.get(flag):
                ext["declaration"][flag] = True
        if decl.get("sorry") and decl["sorry"] != "none":
            ext["declaration"]["sorry"] = decl["sorry"]
        snippet = self.snippets.get(decl["id"])
        if snippet and snippet.get("text"):
            ext["snippet"] = {k: snippet[k] for k in ("text", "line", "truncated") if k in snippet}
        destination = self.destination(decl, end["location"])
        if destination is not None:
            ext["destination"] = destination
        for verdict in verdicts:
            for entry in verdict.get("declarations") or []:
                if entry.get("declaration") == decl["id"] and "declaration_verdict" not in ext:
                    ext["declaration_verdict"] = {"verdict": entry["verdict"], "status": verdict["status"]}
                    if entry.get("note"):
                        ext["declaration_verdict"]["note"] = entry["note"]
        end["extensions"] = {NAMESPACE: ext}
        return end

    def subject_end(self, item: dict, region: _Region) -> dict:
        end = copy.deepcopy(self.pdf_end)
        end["location"] = {"page": region.page, "rect": region.rect}
        end["title"] = item_label(item)
        ext = end["extensions"][NAMESPACE]
        ext["region"] = region.id
        if len(region.order) > 1:
            ext["region_items"] = list(region.order)
        if region.quads:
            ext["quads"] = region.quads
        if region.location.get("signoff"):
            ext["signoff"] = region.location["signoff"]
        return end

    def verdict(self, v: dict) -> dict:
        out = {k: v[k] for k in ("verdict", "severity", "status", "summary", "evidence", "resolution", "sub") if k in v}
        found = provenance_of(self.provenance.get(v.get("provenance")), note=True)
        if found:
            out["provenance"] = found
        return out

    def locator(self, item: dict, pages: set[int]) -> dict:
        """The item's cluster locators, and the physical and printed pages its regions are on."""
        out: dict = {}
        texts: list[str] = []
        for cluster in item.get("clusters") or []:
            text = (self.clusters.get(cluster) or {}).get("locator")
            if isinstance(text, str) and text.strip() and text not in texts:
                texts.append(text)
        if texts:
            out["text"] = "; ".join(texts)
        out["physical"] = sorted(pages)
        offset = self.source.get("printed_page_offset")
        if isinstance(offset, int) and not isinstance(offset, bool):
            printed = [page - offset for page in out["physical"] if page - offset >= 1]
            if printed:
                out["printed"] = printed
        return out

    def records(self) -> Conversion:
        regions: dict[str, list[_Region]] = collections.defaultdict(list)
        for location in sorted(self.doc.get("locations") or [], key=lambda l: l["id"]):
            if (location.get("target") or {}).get("source") != self.source["id"]:
                continue
            selectors = _regions(location)
            if not selectors:
                self.out.counts["locations_without_a_region"] += 1
                continue
            for n, selector in enumerate(selectors):
                region_id = location["id"] if len(selectors) == 1 else f"{location['id']}#{n}"
                try:
                    page, rect, quads = region(selector, self.source)
                except (ValueError, KeyError, TypeError) as exc:
                    self.out.problems.append(f"{location['id']}: {exc}; the region is not exported")
                    continue
                count = self.source.get("physical_page_count")
                if isinstance(count, int) and page > count:
                    self.out.problems.append(f"{location['id']}: page {page} is past the source's {count} pages")
                    continue
                self.out.counts["regions"] += 1
                self.out.counts["quad_regions"] += quads is not None
                placed = _Region(location, region_id, page, rect, quads, item_order(location))
                for item_id in placed.order:
                    if item_id not in self.items:
                        self.out.problems.append(f"{location['id']}: item {item_id!r} is not an item of the source")
                        continue
                    regions[item_id].append(placed)
        for item_id, placed in sorted(regions.items()):
            placed.sort(key=lambda r: (r.page, r.rect[1], r.rect[0], r.id))
            self.add(self.items[item_id], placed)
        for section, records in sorted(self.out.sections.items()):
            records.sort(key=_order)
            try:
                paths = layout(records, self.out.counts)
            except ValueError as exc:
                self.out.problems.append(f"{section}: {exc}")
                continue
            for path, record in zip(paths, records):
                self.out.files[f"{section}/{path}"] = defaults.moved(record, f"{section}/{DEFAULTS}",
                                                                     f"{section}/{path}")
        self.out.files = dict(sorted(self.out.files.items()))
        self.out.counts["records"] = sum(len(r) for r in self.out.sections.values())
        self.out.counts["sections"] = len(self.out.sections)
        return self.out

    def add(self, item: dict, regions: list[_Region]) -> None:
        """The item's records: one per link, or one that links nothing; each names every region of the item."""
        links = self.links.get(item["id"], [])
        status = _status(item, any(l["role"] == "key" for l in links))
        subject = [self.subject_end(item, r) for r in regions]
        base: dict = {"id": item["id"], "kind": item["kind"].lower()}
        if status:
            base["status"] = status
        if item.get("coverage_note"):
            base["note"] = item["coverage_note"]
        common = {"locator": self.locator(item, {r.page for r in regions})}
        if item.get("coverage") and item["coverage"] not in NAMED_BY_STATUS:
            common["coverage"] = item["coverage"]
        verdicts = self.verdicts.get(item["id"], [])
        if verdicts:
            common["verdicts"] = [self.verdict(v) for v in verdicts]
        section = self.out.sections.setdefault(section_of(item, self.clusters), [])
        if not links:
            record = dict(base, subject=subject, object=[])
            if item.get("coverage") == "not_formalized" and "version" in self.project:
                # The item was searched for in the repository at the commit and is not there.
                record.update(relation="noMatch", object=[dict(self.project)])
                self.out.counts["checked_absences"] += 1
            if item.get("clusters"):
                common["clusters"] = list(item["clusters"])
            record["extensions"] = {NAMESPACE: common}
            section.append(record)
            self.out.counts["records_linking_nothing"] += 1
            return
        for link in links:
            decl = self.declarations.get(link["declaration"])
            if decl is None:
                self.out.problems.append(f"{link['id']}: declaration {link['declaration']!r} is not in the document")
                continue
            ext = dict(common)
            token = link.get("relation")
            if token and token not in INJECTIVE:
                ext["relation"] = token
            found = provenance_of(self.provenance.get(link.get("provenance")))
            if found:
                ext["provenance"] = found
            record = dict(base, subject=copy.deepcopy(subject), object=[self.lean_end(decl, link, verdicts)])
            if token in RELATIONS:
                record["relation"] = RELATIONS[token]
            record["extensions"] = {NAMESPACE: ext}
            section.append(record)
            self.out.counts[f"records.{link['role']}"] += 1


def _link(record: dict) -> tuple[str, str] | None:
    """``(role, declaration id)`` of a record's link, or None for a record that links nothing."""
    ends = record.get("object") or []
    if record.get("relation") == "noMatch" or not ends:
        return None
    ext = (ends[0].get("extensions") or {}).get(NAMESPACE) or {}
    return ends[0]["role"], ext["declaration"]["id"]


def _order(record: dict) -> tuple:
    first = record["subject"][0]
    ext = first["extensions"][NAMESPACE]
    link = _link(record)
    rank = -1 if link is None else (ROLES.index(link[0]) if link[0] in ROLES else len(ROLES))
    x, y = first["location"]["rect"][:2]
    items = ext.get("region_items") or [record["id"]]
    position = items.index(record["id"]) if record["id"] in items else len(items)
    return (first["location"]["page"], y, x, ext["region"], position, record["id"], rank, link[1] if link else "")


def convert(doc: dict, *, source_id: str | None = None, directory: str = DIRECTORY) -> Conversion:
    """Every Literature Links record the overlay needs, by book section, each with its file name."""
    return _Converter(doc, source_id, directory).records()


# ---------------------------------------------------------------------------
# the files
# ---------------------------------------------------------------------------

def name_key(record: dict, *, in_item: bool = False) -> str:
    """``<item>__<role>__<declaration>``, or ``<item>`` for a record that links nothing, unsanitised.

    In its item's folder a record with a link drops the ``<item>__``.
    """
    link = _link(record)
    if link is None:
        return record["id"]
    rest = f"{link[0]}__{link[1]}"
    return rest if in_item else f"{record['id']}__{rest}"


def _hashed(stem: str, key: str, suffix: str) -> str:
    tail = "-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:HASH_DIGITS]
    return stem[:MAX_NAME - len(suffix) - len(tail)] + tail + suffix


def _names(entries: list[tuple[str, str, str]], counts: collections.Counter | None) -> list[str]:
    """One name per ``(key, full key, suffix)`` entry of one folder; see :func:`file_names`."""
    stems = []
    for key, _full, _suffix in entries:
        stem = _UNSAFE.sub("-", key)
        stems.append("-" + stem[1:] if stem.startswith(".") else stem)
    long = [len(stem) + len(suffix) > MAX_NAME for stem, (_, _, suffix) in zip(stems, entries)]
    hashed = [too_long or bool(_RESERVED.fullmatch(stem.split(".", 1)[0])) or (stem + suffix).lower() == DEFAULTS
              for stem, (_, _, suffix), too_long in zip(stems, entries, long)]
    while True:
        names = [_hashed(stem, full, suffix) if h else stem + suffix
                 for stem, (_, full, suffix), h in zip(stems, entries, hashed)]
        groups: dict[str, list[int]] = collections.defaultdict(list)
        for index, name in enumerate(names):
            groups[name.lower()].append(index)
        clashing = [index for group in groups.values() if len(group) > 1 for index in group if not hashed[index]]
        if not clashing:
            break
        for index in clashing:
            hashed[index] = True
    for group in groups.values():
        if len(group) > 1:
            raise ValueError(f"{len(group)} records hash to the file name {names[group[0]]!r}")
    if counts is not None:
        counts["files_truncated"] += sum(long)
        counts["files_hashed"] += sum(hashed)
    return names


def file_names(records: list[dict], counts: collections.Counter | None = None, *, in_item: bool = False) -> list[str]:
    """One file name per record of one folder, in the records' order.

    The name is :func:`name_key` with every character outside
    ``[A-Za-z0-9._-]`` (and a leading ``.``) replaced by ``-``, then ``.json``.
    A name longer than :data:`MAX_NAME`, a Windows device name, `_defaults.json`,
    and every name that would equal another one in the folder ignoring case end
    instead in ``-`` and the first :data:`HASH_DIGITS` hex digits of the sha256
    of the full, unsanitised key, truncated to fit.
    """
    keys = [name_key(record, in_item=in_item) for record in records]
    if len(set(keys)) != len(keys):
        raise ValueError(f"two records share the key {collections.Counter(keys).most_common(1)[0][0]!r}")
    return _names([(key, key, ".json") for key in keys], counts)


def layout(records: list[dict], counts: collections.Counter | None = None) -> list[str]:
    """Each record's path in its section folder, in the records' order.

    An item with one record is ``<name>.json`` (:func:`file_names`); an item
    with several is a folder named like a file without ``.json``, its records
    named in it without the item.  Folders and files share the folder's names.
    """
    items: dict[str, list[int]] = collections.defaultdict(list)
    for index, record in enumerate(records):
        items[record["id"]].append(index)
    single = [found[0] for found in items.values() if len(found) == 1]
    folders = [item for item, found in items.items() if len(found) > 1]
    keys = [name_key(records[index]) for index in single]
    names = _names([(key, key, ".json") for key in keys] + [(item, item, "") for item in folders], counts)
    paths = [""] * len(records)
    for index, name in zip(single, names):
        paths[index] = name
    for item, folder in zip(folders, names[len(single):]):
        inside = items[item]
        for index, name in zip(inside, file_names([records[i] for i in inside], counts, in_item=True)):
            paths[index] = f"{folder}/{name}"
    return paths


def walk(directory: Path) -> tuple[dict[str, Path], list[str], list[str]]:
    """``(files, folders, odd)`` of an annotation directory, by path relative to it.

    The layout is `_defaults.json` and section folders at the top, annotation
    files and item folders in a section, `_defaults.json` and annotation files
    in an item folder.  Anything else, a link included, is odd.  Folders come
    deepest first.
    """
    files: dict[str, Path] = {}
    folders: list[str] = []
    odd: list[str] = []

    def visit(path: Path, prefix: str, depth: int) -> None:
        for entry in sorted(os.scandir(path), key=lambda e: e.name):
            relative = prefix + entry.name
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode) and depth < 2 and (SECTION_DIR, ITEM_DIR)[depth].fullmatch(entry.name):
                visit(Path(entry.path), relative + "/", depth + 1)
                folders.append(relative)
            elif stat.S_ISREG(mode) and (depth != 1 if entry.name == DEFAULTS
                                         else depth > 0 and ANNOTATION_FILE.fullmatch(entry.name)):
                files[relative] = Path(entry.path)
            else:
                odd.append(relative)

    if directory.is_dir():
        visit(directory, "", 0)
    return dict(sorted(files.items())), folders, odd


def defaults_folders(paths) -> list[str]:
    """The folders that may hold a `_defaults.json`: the directory (``""``) and each item folder."""
    return [""] + sorted({path.rsplit("/", 1)[0] for path in paths if path.count("/") == 2})


def annotation_bytes(record: dict) -> bytes:
    """One file on its own, with no defaults: `overlay.defaults.dumps`."""
    return defaults.dumps(record)


def file_bytes(conversion: Conversion) -> dict[str, bytes]:
    """``{path: bytes}`` of every file in file order: the leaves and their `_defaults.json` files.

    Raises ValueError when the files would not read back as the records.
    """
    return defaults.files(conversion.files, defaults_folders(conversion.files))


def scan_problems(files: dict[str, bytes]) -> list[str]:
    """What the vendored ``literature_links`` scan and the layout (:func:`walk`) say about these files, line by line."""
    with tempfile.TemporaryDirectory(prefix="cf-overlay-annotations-") as tmp:
        root = Path(tmp).resolve()
        for name, raw in files.items():
            path = root.joinpath(*PurePosixPath(name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        found = discovery.collect([root])
        out = [f"{p.path.replace(str(root) + '/', '')}: {p.message} [{p.code}]" for p in found.problems]
        out += [f"{relative}: not a path of the annotation layout" for relative in walk(root)[2]]
    return out
