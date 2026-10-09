"""Convert an annotation document into the overlay's Literature Links shards.

New code, implementing section 7 of the overlay contract (`docs/overlay.md`).
The exporters resolve a companion's curation into one annotation document
(`vendor/formal_companion_annotations`); the overlay reads Literature Links
(`vendor/literature_links`), one link per annotation, stored as JSONL shards,
one file per book section.  :func:`convert` is the whole mapping:

* one record per (region, item, linked declaration), for every link role the
  viewer shows, and one target-less record per region of an item that links
  nothing;
* ``source``: the PDF, its official URL and a ``book`` pin;
* ``location``: ``{page, rect}``, the region's box as ``[x, y, width, height]``
  fractions of the displayed page from the top left (a quad region becomes
  its bounding box, the quads kept in the extension);
* ``target``: the rendered page (``href#fragment``, or the fallback page),
  relative to the shard, and a ``repository`` pin of the declaration;
* ``kind``, ``label``, ``status``, ``note`` and ``id`` from the item, its
  coverage and the link;
* everything else the viewer shows under one extension, :data:`NAMESPACE`.
"""
from __future__ import annotations

import collections
import json
import math
import re
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from pathlib import Path, PurePosixPath

from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from literature_links import discovery

__all__ = ["DIRECTORY", "NAMESPACE", "ROLES", "SHARD_FILE", "Conversion", "convert", "scan_problems",
           "section_of", "shard_bytes"]

#: The one extension key every record carries (docs/overlay.md section 7).
NAMESPACE = "org.mathlib-initiative.formal-companion.overlay"
#: Where the shards live, relative to the companion root, and what a shard is called.
DIRECTORY = "annotations"
SHARD_FILE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*\.jsonl\Z")
#: Link roles in the order the viewer lists them.
ROLES = ("key", "attributed", "support", "example", "helper")
#: The page boxes whose normalized coordinates are the displayed page's.
DISPLAYED_BOXES = ("CropBox", "MediaBox")
#: Statuses: the curation's class where it gives one, else ``formalized`` for a key link.
STATUS_OF_COVERAGE = {"partial_only": "partial"}
#: A fragment percent-encoded as `encodeURIComponent` does, so the viewer uses it as it is.
FRAGMENT_SAFE = "!'()*-._~"

_CLUSTER_SECTION = re.compile(r"\bSection (\d+)\.(\d+)")
_DOTTED = re.compile(r"(\d+)\.(\d+)")
_HTTPS = re.compile(r"https://[^\s#\\]+\Z")


@dataclass
class Conversion:
    """The shards, by section, and what the conversion could not carry."""

    shards: dict[str, list[dict]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    counts: collections.Counter = field(default_factory=collections.Counter)

    @property
    def records(self) -> list[dict]:
        return [record for name in sorted(self.shards) for record in self.shards[name]]


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
    """The shard an item's records go to.

    A number with three or more parts names its section (``1.2.7`` is in
    ``1.2``); otherwise the item's own ``section`` or a cluster locator that
    says ``Section N.M``; otherwise a two-part number's first part (``3.4`` is
    in chapter ``3``); otherwise ``other``.
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
    if len(parts) == 2 and parts[0].isdigit():
        return parts[0]
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


def ll_source(source: dict) -> dict:
    """``source``: the PDF at its official URL, a ``book`` pin, and what serve.py and the viewer need."""
    pin = {"type": "book"}
    for ours, theirs in (("title", "title"), ("edition", "edition"), ("url", "url"),
                         ("retrieved_at", "retrieved"), ("sha256", "sha256")):
        if source.get(ours):
            pin[theirs] = source[ours]
    identifiers = dict(source.get("identifiers") or {})
    isbns = identifiers.pop("isbn", None) or []
    if isbns:
        pin["isbn"] = isbns[0]
    extra = {f"isbn-{n}": isbn for n, isbn in enumerate(isbns[1:], 2)}
    extra.update({k: v for k, v in identifiers.items() if isinstance(v, str)})
    if extra:
        pin["identifiers"] = dict(sorted(extra.items()))
    ext = {"source_id": source["id"]}
    for key in ("bytes", "physical_page_count", "alternate_urls"):
        if source.get(key):
            ext[key] = source[key]
    return {"type": "pdf", "uri": source["url"], "pin": pin, "extensions": {NAMESPACE: ext}}


def _status(item: dict, has_key: bool) -> str | None:
    coverage = item.get("coverage")
    if coverage:
        return STATUS_OF_COVERAGE.get(coverage, coverage)
    return "formalized" if has_key else None


def _verdict(v: dict) -> dict:
    out = {k: v[k] for k in ("verdict", "severity", "status", "summary", "evidence", "resolution", "sub") if k in v}
    return out


class _Converter:
    def __init__(self, doc: dict, source_id: str | None, directory: str) -> None:
        self.doc = doc
        self.out = Conversion()
        self.source = pdf_source(doc, source_id)
        self.ll_source = ll_source(self.source)
        companion = doc.get("companion") or {}
        self.commit = companion.get("commit")
        self.repo_url = repo_url(companion.get("repo"))
        self.up = "../" * len(PurePosixPath(directory).parts)
        self.declarations = _by_id(doc.get("declarations"))
        self.snippets = _by_id(doc.get("snippets"), "declaration")
        self.destinations = _by_id(doc.get("destinations"), "declaration")
        self.clusters = _by_id(doc.get("clusters"))
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

    def repository_pin(self, decl: dict | None = None) -> dict:
        pin = {"type": "repository"}
        if self.repo_url:
            pin["url"] = self.repo_url
        if self.commit:
            pin["commit"] = self.commit
        if decl is None:
            return pin
        if decl.get("file"):
            pin["path"] = decl["file"]
        if isinstance(decl.get("line"), int) and decl["line"] >= 1:
            pin["line"] = decl["line"]
        if decl.get("module"):
            pin["module"] = decl["module"]
        if decl.get("name"):
            pin["declaration"] = decl["name"]
        return pin

    def target(self, decl: dict, ext: dict) -> dict:
        """The rendered page relative to the shard, else the source file (an unlinked card)."""
        dest = self.destinations.get(decl["id"])
        uri = None
        if dest is not None:
            href = dest.get("href")
            base = href if isinstance(href, str) and _HTTPS.fullmatch(href) else (
                self.up + href if _site_path(href) else None)
            if base is None:
                self.out.problems.append(f"{decl['id']}: destination href {href!r} is neither a site path "
                                         "nor an https: URL; the card is unlinked")
            else:
                uri = base
                if dest.get("resolution") == "exact" and dest.get("fragment"):
                    uri += "#" + urllib.parse.quote(dest["fragment"], safe=FRAGMENT_SAFE)
                ext["destination"] = {"resolution": dest.get("resolution")}
                if dest.get("fallback_label"):
                    ext["destination"]["fallback_label"] = dest["fallback_label"]
        if uri is None:
            self.out.counts["targets_without_a_rendered_page"] += 1
            uri = self.up + urllib.parse.quote(decl["file"]) if decl.get("file") else \
                urllib.parse.quote(decl.get("name") or decl["id"], safe=".")
        return {"uri": uri, "pin": self.repository_pin(decl)}

    def records(self) -> Conversion:
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
                for item_id in location.get("items") or []:
                    item = self.items.get(item_id)
                    if item is None:
                        self.out.problems.append(f"{location['id']}: item {item_id!r} is not an item of the source")
                        continue
                    self.add(item, location, region_id, page, rect, quads)
        for records in self.out.shards.values():
            records.sort(key=_order)
        self.out.counts["records"] = sum(len(r) for r in self.out.shards.values())
        self.out.counts["shards"] = len(self.out.shards)
        return self.out

    def add(self, item: dict, location: dict, region_id: str, page: int, rect: list[float], quads) -> None:
        links = self.links.get(item["id"], [])
        status = _status(item, any(l["role"] == "key" for l in links))
        base = {"source": self.ll_source, "location": {"page": page, "rect": rect},
                "kind": item["kind"].lower(), "label": item_label(item)}
        if item.get("coverage_note"):
            base["note"] = item["coverage_note"]
        common = {"item": item["id"], "region": region_id}
        if quads:
            common["quads"] = quads
        if location.get("signoff"):
            common["signoff"] = location["signoff"]
        if item.get("pages"):
            common["pages"] = item["pages"]
        verdicts = self.verdicts.get(item["id"], [])
        if verdicts:
            common["verdicts"] = [_verdict(v) for v in verdicts]
        shard = self.out.shards.setdefault(section_of(item, self.clusters), [])
        if not links:
            record = dict(base, id=item["id"])
            if status == "not_formalized":
                record["status"] = {"value": status, "pin": self.repository_pin()}
            elif status:
                record["status"] = status
            if item.get("clusters"):
                common["clusters"] = list(item["clusters"])
            record["extensions"] = {NAMESPACE: common}
            shard.append(record)
            self.out.counts["target_less_records"] += 1
            return
        for link in links:
            decl = self.declarations.get(link["declaration"])
            if decl is None:
                self.out.problems.append(f"{link['id']}: declaration {link['declaration']!r} is not in the document")
                continue
            ext = dict(common, role=link["role"])
            if link.get("relation"):
                ext["relation"] = link["relation"]
            ext["declaration"] = {"id": decl["id"], "kind": decl.get("kind")}
            for flag in ("private", "anonymous"):
                if decl.get(flag):
                    ext["declaration"][flag] = True
            if decl.get("sorry") and decl["sorry"] != "none":
                ext["declaration"]["sorry"] = decl["sorry"]
            snippet = self.snippets.get(decl["id"])
            if snippet and snippet.get("text"):
                ext["snippet"] = {k: snippet[k] for k in ("text", "line", "truncated") if k in snippet}
            for verdict in verdicts:
                for entry in verdict.get("declarations") or []:
                    if entry.get("declaration") == decl["id"] and "declaration_verdict" not in ext:
                        ext["declaration_verdict"] = {"verdict": entry["verdict"], "status": verdict["status"]}
                        if entry.get("note"):
                            ext["declaration_verdict"]["note"] = entry["note"]
            record = dict(base, id=link["id"], target=self.target(decl, ext))
            if status:
                record["status"] = status
            record["extensions"] = {NAMESPACE: ext}
            shard.append(record)
            self.out.counts[f"records.{link['role']}"] += 1


def _order(record: dict) -> tuple:
    ext = record["extensions"][NAMESPACE]
    role = ext.get("role")
    rank = -1 if role is None else (ROLES.index(role) if role in ROLES else len(ROLES))
    x, y = record["location"]["rect"][:2]
    return (record["location"]["page"], y, x, ext["region"], ext["item"], rank, record["id"])


def convert(doc: dict, *, source_id: str | None = None, directory: str = DIRECTORY) -> Conversion:
    """Every Literature Links record the overlay needs, grouped into shards by book section."""
    return _Converter(doc, source_id, directory).records()


# ---------------------------------------------------------------------------
# the files
# ---------------------------------------------------------------------------

def line(record: dict) -> str:
    """One canonical JSONL line: sorted keys, no spaces, UTF-8 as written."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def shard_bytes(conversion: Conversion) -> dict[str, bytes]:
    """``{"<section>.jsonl": bytes}``: one canonical line per record, a final newline."""
    return {f"{name}.jsonl": "".join(line(r) + "\n" for r in records).encode("utf-8")
            for name, records in sorted(conversion.shards.items())}


def scan_problems(files: dict[str, bytes]) -> list[str]:
    """What the vendored ``literature_links`` scan says about these shards, one line each."""
    with tempfile.TemporaryDirectory(prefix="cf-overlay-shards-") as tmp:
        root = Path(tmp).resolve()
        for name, raw in files.items():
            (root / name).write_bytes(raw)
        found = discovery.collect([root])
        out = [f"{p.path.replace(str(root) + '/', '')}: {p.message} [{p.code}]" for p in found.problems]
        expected = sum(raw.count(b"\n") for raw in files.values())
        if not out and len(found.records) != expected:
            out.append(f"the scan read {len(found.records)} annotations from {expected} lines")
    return out
