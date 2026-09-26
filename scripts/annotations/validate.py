#!/usr/bin/env python3
"""Validate a lean-companion annotation document: schema, then invariants.

New code, implementing docs/annotations.md (sections 6 to 9).  Usage::

    python3 scripts/annotations/validate.py <document.json> [...] [--canonical]
    python3 scripts/annotations/validate.py <document.json> --print-canonical

The document's ``schema_version`` (``"<major>.<minor>"``) selects the JSON
Schema by its major version (schema/annotations-v<major>.schema.json).  The
schema is evaluated first, by the in-repo ``schemacheck`` because the repo is
standard library only.  When it passes, the semantic invariants run; they are
the rules a JSON Schema cannot state: unique and sorted ids, references that
resolve, the anchoring and coverage rules for items, the item and declaration
id rules, selector grammars against their ``conformsTo``, the changelog's
rules, and the URL rule.  Some findings are warnings, not problems: an unknown
``conformsTo`` IRI, a missing ``conformsTo``, a link ``relation`` outside the
documented core, and a minor version newer than this checker's.
``--canonical`` also requires the file's bytes to be the canonical
serialisation (section 7: RFC 8785 order and numbers, two-space indent).
Exit status: 0 valid, 1 invalid, 2 unreadable.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from annotations import ids                                          # noqa: E402
from annotations.schemacheck import Validator                       # noqa: E402
from cflib import vocab                                              # noqa: E402

SCHEMA_DIR = HERE.parent.parent / "schema"


def schema_path(major: int) -> Path:
    """The schema file for one major version."""
    return SCHEMA_DIR / f"annotations-v{major}.schema.json"


SCHEMA_PATH = schema_path(vocab.ANNOTATION_SCHEMA_MAJOR)
_KNOWN_MINOR = int(vocab.ANNOTATION_SCHEMA_VERSION.split(".")[1])

#: Root arrays, in the order the checks walk them.
RECORD_ARRAYS = ("sources", "provenance", "clusters", "items", "declarations", "artifacts",
                 "memberships", "locations", "links", "destinations", "snippets", "verdicts",
                 "unattributed", "changelog")

#: Lists of ids (or plain strings) that are sets: kept sorted and duplicate-free.
#: A dotted name reaches into a sub-object of the record.
SET_FIELDS = {
    "sources": ("alternate_urls",),
    "items": ("clusters", "legacy_ids", "relations.same_as", "relations.part_of", "relations.near"),
    "declarations": ("anchors", "legacy_ids"),
    "locations": ("items", "legacy_ids"),
    "links": ("locations",),
    "clusters": ("items", "related_to"),
    "provenance": ("agents",),
    "unattributed": ("signals",),
    "verdicts": ("finding_ids", "locations", "resolution.commits", "resolution.replaced_by"),
}
#: Lists of objects kept sorted by one key, one entry per key value.  A tuple of
#: keys means alternatives: entries with the first key come first, sorted by it,
#: then entries with the second (``provenance.inputs``: files by ``path``, then
#: sources by ``source``).
KEYED_LISTS = {
    "sources": (("page_sizes", "first"), ("page_hashes", "path")),
    "provenance": (("inputs", ("path", "source")),),
    "verdicts": (("declarations", "declaration"),),
}

#: Which rules a source follows, by kind or (for ``other``) by media type.
FAMILY_BY_KIND = {"pdf": "pdf", "web": "html", "tex": "text", "lean": "text"}
_STANDARD = {"FragmentSelector", "TextQuoteSelector", "TextPositionSelector",
             "DataPositionSelector", "CssSelector", "XPathSelector", "SvgSelector", "RangeSelector"}
#: Selector classes each family may use (after W3C Web Annotation Appendix A).
ALLOWED_SELECTORS = {
    "pdf": {"FragmentSelector", "TextQuoteSelector", "TextPositionSelector",
            "PdfRegionSelector", "RangeSelector"},
    "html": {"FragmentSelector", "TextQuoteSelector", "TextPositionSelector", "CssSelector",
             "XPathSelector", "SvgSelector", "RangeSelector", "SitePageSelector"},
    "text": {"FragmentSelector", "TextQuoteSelector", "TextPositionSelector",
             "DataPositionSelector", "RangeSelector"},
    "generic": set(_STANDARD),
}
#: Known conformsTo IRIs: the grammar their value follows and the families they fit.
CONFORMS = {
    vocab.CONFORMS_TO_PDF: ("pdf", {"pdf"}),
    vocab.CONFORMS_TO_PDF_RFC3778: ("pdf", {"pdf"}),
    vocab.CONFORMS_TO_TEXT: ("text", {"text"}),
    vocab.CONFORMS_TO_HTML: ("html_id", {"html"}),
    vocab.CONFORMS_TO_MEDIA: ("media", {"html", "generic"}),
}

_URL = re.compile(r"https?://", re.I)
_NUM = r"-?(?:\d+(?:\.\d*)?|\.\d+)"
_RFC5147 = re.compile(
    r"^(?P<scheme>char|line)=(?:(?P<pos>\d+)|(?P<a>\d+)?,(?P<b>\d+)?)"
    r"(?P<integrity>(?:;(?:length=\d+(?:,[A-Za-z0-9._-]+)?|md5=[0-9a-f]{32}))*)$")
_XYWH = re.compile(r"^(?:(?P<unit>pixel|percent):)?(?P<x>\d+),(?P<y>\d+),(?P<w>\d+),(?P<h>\d+)$")
_CLOCK = r"\d+:\d{2}:\d{2}(?:\.\d*)?|\d{2}:\d{2}(?:\.\d*)?|\d+(?:\.\d*)?"  # npt-hhmmss | npt-mmss | npt-sec
_MEDIA_T = re.compile(r"^(?:npt:)?(?P<a>" + _CLOCK + r")?(?:,(?P<b>" + _CLOCK + r"))?$")
_PDF_PARAMS = {  # name -> allowed counts of comma-separated numbers (None: free text)
    "page": (1,), "nameddest": None, "structelem": None, "comment": None, "ef": None,
    "zoom": (1, 3), "view": None, "viewrect": (4,), "highlight": (4,), "search": None, "fdf": None,
}
_NAMED_ID = re.compile(r"^(?!(?:private|example|instance):)[^\s:]\S*$")
_HASH_ID = ids.HASH_ID
_DECLARED_ID = ids.DECLARED_ID
_RESERVED_PASSAGE = re.compile(r"^(?:" + vocab.PASSAGE_QUOTE_ID_PATTERN + "|"
                               + vocab.PASSAGE_PAGE_ID_PATTERN + r")$")
_VERSION = re.compile(r"^(?P<major>[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)$")


# ---------------------------------------------------------------------------
# loading and canonical form
# ---------------------------------------------------------------------------

class LoadError(ValueError):
    """The file is not a JSON document this checker can read."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise LoadError(f"duplicate key {key!r} in one object")
        out[key] = value
    return out


def _no_constants(name: str) -> Any:
    raise LoadError(f"{name} is not JSON")


def loads(text: str) -> Any:
    """Parse strictly: no duplicate keys, no NaN or Infinity."""
    try:
        return json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constants)
    except json.JSONDecodeError as exc:
        raise LoadError(f"not JSON: {exc}") from exc


def jcs_number(value: int | float) -> str:
    """A number as RFC 8785 writes it: ECMAScript Number::toString of the IEEE double.

    The shortest digits that round-trip, integers without ``.0``, and plain
    decimal notation for 1e-7 <= |x| < 1e21 (so ``1.0`` is ``1`` and ``0.00001``
    stays ``0.00001``); exponent form ``1e+21`` / ``1e-7`` outside that range.
    An integer beyond 2**53 is written as the double it becomes.
    """
    if isinstance(value, bool):
        raise TypeError("a boolean is not a number")
    if isinstance(value, int) and abs(value) <= 2 ** 53:
        return str(value)
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("NaN and Infinity are not JSON")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    mantissa, _, exponent = repr(abs(x)).partition("e")
    whole, _, frac = mantissa.partition(".")
    digits = (whole + frac).lstrip("0")
    e10 = (int(exponent) if exponent else 0) - len(frac)
    stripped = digits.rstrip("0")
    e10 += len(digits) - len(stripped)
    s, k = stripped, len(stripped)
    n = e10 + k
    if k <= n <= 21:
        text = s + "0" * (n - k)
    elif 0 < n <= 21:
        text = s[:n] + "." + s[n:]
    elif -6 < n <= 0:
        text = "0." + "0" * (-n) + s
    else:
        e = n - 1
        mark = f"+{e}" if e >= 0 else f"-{-e}"
        text = (s if k == 1 else s[0] + "." + s[1:]) + "e" + mark
    return sign + text


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be", "surrogatepass")


def order_key(value: Any) -> Any:
    """The section 7 order: strings by UTF-16 code units (RFC 8785), numbers by value.

    One order for object keys, record ids, id lists and keyed lists, so a
    producer that sorts as JavaScript's default ``Array.prototype.sort`` does
    (by UTF-16 code units) writes the same bytes.  It differs from code point
    order only for a character above U+FFFF against one in U+E000..U+FFFF.
    """
    if isinstance(value, str):
        return _utf16_key(value)
    return value


def _entry_key(entry: Any, key: str | tuple[str, ...]) -> tuple:
    """Where an entry of a keyed list sorts: by its first present alternative key."""
    keys = (key,) if isinstance(key, str) else key
    if isinstance(entry, dict):
        for i, name in enumerate(keys):
            if name in entry:
                return i, order_key(entry[name])
    return len(keys), b""


def _key_label(key: str | tuple[str, ...]) -> str:
    return key if isinstance(key, str) else ", then ".join(key)


def _emit(value: Any, depth: int, out: list[str]) -> None:
    pad = "  " * (depth + 1)
    if isinstance(value, dict):
        if not value:
            out.append("{}")
            return
        out.append("{\n")
        keys = sorted(value, key=_utf16_key)
        for i, key in enumerate(keys):
            out.append(pad + json.dumps(key, ensure_ascii=False) + ": ")
            _emit(value[key], depth + 1, out)
            out.append(",\n" if i < len(keys) - 1 else "\n")
        out.append("  " * depth + "}")
    elif isinstance(value, list):
        if not value:
            out.append("[]")
            return
        out.append("[\n")
        for i, item in enumerate(value):
            out.append(pad)
            _emit(item, depth + 1, out)
            out.append(",\n" if i < len(value) - 1 else "\n")
        out.append("  " * depth + "]")
    elif value is None:
        out.append("null")
    elif isinstance(value, bool):
        out.append("true" if value else "false")
    elif isinstance(value, (int, float)):
        out.append(jcs_number(value))
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    else:
        raise TypeError(f"{type(value).__name__} is not JSON")


def canonical_dumps(doc: Any) -> str:
    """The canonical text (docs/annotations.md section 7).

    RFC 8785 (JCS) for key order (UTF-16 code units) and for numbers
    (:func:`jcs_number`); strings escaped as ECMAScript ``JSON.stringify``
    does, with non-ASCII written as UTF-8; laid out with a two-space indent,
    ``": "`` after a key, LF line ends and a final newline.
    """
    out: list[str] = []
    _emit(doc, 0, out)
    return "".join(out) + "\n"


def _get(rec: dict, dotted: str) -> Any:
    node: Any = rec
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _set(rec: dict, dotted: str, value: Any) -> None:
    *head, last = dotted.split(".")
    node = rec
    for part in head:
        node = node[part]
    node[last] = value


def canonicalize(doc: dict) -> dict:
    """A copy with every record array sorted by id and every set field sorted.

    Everything sorts in the section 7 order (:func:`order_key`): UTF-16 code
    units, the order RFC 8785 gives object keys.
    """
    out = copy.deepcopy(doc)
    for name in RECORD_ARRAYS:
        records = out.get(name)
        if not isinstance(records, list):
            continue
        records.sort(key=lambda r: order_key(str(r.get("id", ""))) if isinstance(r, dict) else b"")
        for rec in records:
            if not isinstance(rec, dict):
                continue
            for dotted in SET_FIELDS.get(name, ()):
                values = _get(rec, dotted)
                if isinstance(values, list):
                    _set(rec, dotted, sorted(set(values), key=lambda v: order_key(str(v))))
            for field_name, key in KEYED_LISTS.get(name, ()):
                values = rec.get(field_name)
                if isinstance(values, list):
                    values.sort(key=lambda d, k=key: _entry_key(d, k))
    return out


def site_sha256(page_hashes: list[dict]) -> str:
    """The sha256 of a ``scope: site`` source (section 3.1).

    The sha256 of the manifest with one line ``"<sha256 of the page's bytes>  <path>\\n"``
    per HTML page (two spaces, as ``sha256sum`` writes it), the lines ordered
    by path in the section 7 order, encoded as UTF-8.
    """
    lines = sorted(page_hashes, key=lambda p: order_key(p["path"]))
    manifest = "".join(f"{p['sha256']}  {p['path']}\n" for p in lines)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def site_page_file(path: str) -> str:
    """The page file a ``SitePageSelector`` path names: ``index.html`` of a directory path."""
    return path + "index.html" if path.endswith("/") else path


def _at_key(at: str) -> dt.datetime:
    """A changelog ``at`` as a UTC instant; a bare date is the start of its day (00:00Z).

    Raises ValueError for a string that matches the schema's pattern but is not a
    calendar date or instant (``2026-02-30``, ``2026-09-05T25:00:00Z``).
    """
    if "T" not in at:
        return dt.datetime.fromisoformat(at + "T00:00:00+00:00")
    return dt.datetime.fromisoformat(at.replace("Z", "+00:00"))


def _full_name(decl: dict) -> str | None:
    """A declaration's full Lean name: ``name`` when given, else read from a name or
    private id (``<full name>`` or ``private:<module>:<full name>``)."""
    if "name" in decl:
        return decl["name"]
    if decl.get("id_source") == "name":
        return decl["id"]
    if decl.get("id_source") == "private" and decl["id"].count(":") >= 2:
        return decl["id"].split(":", 2)[2]
    return None


def _parse_at(at: str) -> dt.datetime | None:
    """``_at_key`` that returns None instead of raising."""
    try:
        return _at_key(at)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# the checker
# ---------------------------------------------------------------------------

def load_schema(path: Path = SCHEMA_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def source_family(source: dict) -> str:
    """``pdf``, ``html``, ``text`` or ``generic``: the selector rules a source follows."""
    kind = source["kind"]
    if kind in FAMILY_BY_KIND:
        return FAMILY_BY_KIND[kind]
    media = source.get("media_type", "")
    if media == "application/pdf":
        return "pdf"
    if media in ("text/html", "application/xhtml+xml"):
        return "html"
    if media.startswith("text/"):
        return "text"
    return "generic"


def printed_to_physical(page: int, basis: str, source: dict) -> int | None:
    """A page on the physical basis, or None when the offset is unknown."""
    if basis == "physical":
        return page
    offset = source.get("printed_page_offset")
    return None if offset is None else page + offset


@dataclass
class _Ctx:
    """Where a selector sits: the page an enclosing fragment chose, what it refines."""
    page: int | None = None
    refines: str | None = None
    top: bool = True


@dataclass
class Report:
    """What :func:`check` found: problems make a document invalid, warnings do not."""
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Checker:
    """Collects ``path: message`` strings for one document."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.problems: list[str] = []
        self.warnings: list[str] = []
        self.by_id: dict[str, dict[str, dict]] = {}
        for name in RECORD_ARRAYS:
            self.by_id[name] = {}
            for rec in doc.get(name, []) or []:
                self.by_id[name].setdefault(rec["id"], rec)
        changes = doc.get("changelog", []) or []
        self.retired = {c["subject"] for c in changes if c["event"] == "retired"}
        #: every ordinal_assigned record, per subject
        self.ordinal_records: dict[str, list[dict]] = {}
        #: when each declaration id was retired or renamed (the latest such entry)
        self.gone_at: dict[str, dt.datetime] = {}
        for i, c in enumerate(changes):
            if _parse_at(c["at"]) is None:
                self.fail(f"$.changelog[{i}].at", f"{c['at']!r} is not a calendar date or UTC instant")
                continue
            if c["event"] == "ordinal_assigned":
                self.ordinal_records.setdefault(c["subject"], []).append(c)
            elif c["event"] in ("retired", "renamed") and c["subject_kind"] == "declaration":
                when = _at_key(c["at"])
                if c["subject"] not in self.gone_at or when > self.gone_at[c["subject"]]:
                    self.gone_at[c["subject"]] = when

    def fail(self, path: str, message: str) -> None:
        self.problems.append(f"{path}: {message}")

    def warn(self, path: str, message: str) -> None:
        self.warnings.append(f"{path}: {message}")

    def records(self, name: str) -> Iterator[tuple[str, dict]]:
        for i, rec in enumerate(self.doc.get(name, []) or []):
            yield f"$.{name}[{i}]", rec

    def ref(self, path: str, value: str | None, array: str) -> dict | None:
        """Resolve one reference into ``array``; report it when it does not."""
        if value is None:
            return None
        rec = self.by_id[array].get(value)
        if rec is None:
            if array not in self.doc:
                self.fail(path, f"{value!r} names a record in {array}[], and the document has no {array}[]")
            else:
                self.fail(path, f"{value!r} is not an id in {array}[]")
        return rec

    # -- I1, I2 ----------------------------------------------------------
    def check_ids_and_order(self) -> None:
        for name in RECORD_ARRAYS:
            ids_ = [rec["id"] for rec in self.doc.get(name, []) or []]
            seen: set[str] = set()
            for i, rid in enumerate(ids_):
                if rid in seen:
                    self.fail(f"$.{name}[{i}].id", f"duplicate id {rid!r} in {name}[]")
                seen.add(rid)
            for i in range(1, len(ids_)):
                if order_key(ids_[i - 1]) > order_key(ids_[i]):
                    self.fail(f"$.{name}[{i}].id",
                              f"{name}[] is not sorted by id (UTF-16 code units): {ids_[i]!r} comes "
                              f"after {ids_[i - 1]!r}")
                    break
            for path, rec in self.records(name):
                for dotted in SET_FIELDS.get(name, ()):
                    values = _get(rec, dotted)
                    if isinstance(values, list) and values != sorted(values, key=order_key):
                        self.fail(f"{path}.{dotted}", "must be sorted (UTF-16 code units)")
                for field_name, key in KEYED_LISTS.get(name, ()):
                    keys = [_entry_key(d, key) for d in rec.get(field_name, [])]
                    if keys != sorted(keys) or len(set(keys)) != len(keys):
                        self.fail(f"{path}.{field_name}", f"must be sorted by {_key_label(key)}, one entry each")

    # -- I3 ------------------------------------------------------------------
    def check_references(self) -> None:
        for path, item in self.records("items"):
            self.ref(f"{path}.source", item["source"], "sources")
            for i, c in enumerate(item.get("clusters", [])):
                self.ref(f"{path}.clusters[{i}]", c, "clusters")
            for rel, others in sorted(item.get("relations", {}).items()):
                for i, other in enumerate(others):
                    if other == item["id"]:
                        self.fail(f"{path}.relations.{rel}[{i}]", "an item is not related to itself")
                    else:
                        self.ref(f"{path}.relations.{rel}[{i}]", other, "items")
        for path, decl in self.records("declarations"):
            for i, loc in enumerate(decl.get("anchors", [])):
                self.ref(f"{path}.anchors[{i}]", loc, "locations")
            if "replaced_by" in decl:
                self.ref(f"{path}.replaced_by", decl["replaced_by"], "declarations")
            if "parent" in decl:
                parent = self.ref(f"{path}.parent", decl["parent"], "declarations")
                if parent is not None and parent["kind"] not in ("structure", "class"):
                    self.fail(f"{path}.parent", f"a {decl['kind']}'s parent is a structure or class, "
                                                f"not a {parent['kind']}")
        for path, loc in self.records("locations"):
            self.ref(f"{path}.target.source", loc["target"]["source"], "sources")
            for i, item in enumerate(loc["items"]):
                self.ref(f"{path}.items[{i}]", item, "items")
            if "provenance" in loc:
                self.ref(f"{path}.provenance", loc["provenance"], "provenance")
        link_keys: dict[tuple, str] = {}
        for path, link in self.records("links"):
            key = (link["item"], link.get("declaration"), link.get("cluster"), link["role"], link.get("sub"))
            prior = link_keys.setdefault(key, path)
            if prior != path:
                self.fail(path, f"repeats the link at {prior} (same item, target, role and sub)")
            self.ref(f"{path}.item", link["item"], "items")
            self.ref(f"{path}.provenance", link["provenance"], "provenance")
            for i, loc in enumerate(link.get("locations", [])):
                self.ref(f"{path}.locations[{i}]", loc, "locations")
            if "cluster" in link:
                self.ref(f"{path}.cluster", link["cluster"], "clusters")
            if "declaration" in link:
                target = link["declaration"]
                if link.get("orphaned"):
                    if target in self.by_id["declarations"]:
                        self.fail(f"{path}.orphaned", f"{target!r} is live; an orphaned link names a retired id")
                    elif target not in self.retired:
                        self.fail(f"{path}.declaration",
                                  f"{target!r} is orphaned but no changelog entry retires it")
                else:
                    self.ref(f"{path}.declaration", target, "declarations")
            elif link.get("orphaned"):
                self.fail(f"{path}.orphaned", "only a link to a declaration can be orphaned")
            relation = link.get("relation")
            if relation is not None and relation not in vocab.ANNOTATION_LINK_RELATIONS:
                self.warn(f"{path}.relation", f"{relation!r} is outside the documented core "
                                              f"({', '.join(sorted(vocab.ANNOTATION_LINK_RELATIONS))})")
        member_keys: dict[tuple, str] = {}
        for path, member in self.records("memberships"):
            key = (member["cluster"], member.get("declaration"), member.get("artifact"), member["role"])
            prior = member_keys.setdefault(key, path)
            if prior != path:
                self.fail(path, f"repeats the membership at {prior} (same cluster, target and role)")
            self.ref(f"{path}.cluster", member["cluster"], "clusters")
            self.ref(f"{path}.provenance", member["provenance"], "provenance")
            if "declaration" in member:
                self.ref(f"{path}.declaration", member["declaration"], "declarations")
            else:
                self.ref(f"{path}.artifact", member["artifact"], "artifacts")
        for name in ("snippets", "unattributed"):
            for path, rec in self.records(name):
                self.ref(f"{path}.declaration", rec["declaration"], "declarations")
        for path, dest in self.records("destinations"):
            if "declaration" in dest:
                self.ref(f"{path}.declaration", dest["declaration"], "declarations")
            else:
                self.ref(f"{path}.artifact", dest["artifact"], "artifacts")
        for path, verdict in self.records("verdicts"):
            item = self.ref(f"{path}.item", verdict["item"], "items")
            for i, loc_id in enumerate(verdict.get("locations", [])):
                loc = self.ref(f"{path}.locations[{i}]", loc_id, "locations")
                if loc is not None and item is not None and verdict["item"] not in loc["items"]:
                    self.fail(f"{path}.locations[{i}]",
                              f"location {loc['id']!r} does not show item {verdict['item']!r}")
            for i, d in enumerate(verdict.get("declarations", [])):
                self.ref(f"{path}.declarations[{i}].declaration", d["declaration"], "declarations")
            res = verdict.get("resolution", {"state": "open"})      # no resolution: the verdict is open
            for i, other in enumerate(res.get("replaced_by", [])):
                self.ref(f"{path}.resolution.replaced_by[{i}]", other, "declarations")
            if "provenance" in verdict:
                self.ref(f"{path}.provenance", verdict["provenance"], "provenance")
            if verdict["status"] == "fixed" and res["state"] != "fixed":
                where = f"{path}.resolution.state" if "resolution" in verdict else f"{path}.status"
                self.fail(where, "status is fixed, so the resolution must be fixed "
                                 "(a verdict without a resolution is open)")
        for path, cluster in self.records("clusters"):
            for i, item in enumerate(cluster.get("items", [])):
                self.ref(f"{path}.items[{i}]", item, "items")
            for i, other in enumerate(cluster.get("related_to", [])):
                self.ref(f"{path}.related_to[{i}]", other, "clusters")
        for path, prov in self.records("provenance"):
            for i, entry in enumerate(prov.get("inputs", [])):
                if "source" in entry:
                    self.ref(f"{path}.inputs[{i}].source", entry["source"], "sources")
        seen_snippet: dict[str, str] = {}
        for path, snip in self.records("snippets"):
            prior = seen_snippet.setdefault(snip["declaration"], path)
            if prior != path:
                self.fail(f"{path}.declaration", f"{snip['declaration']!r} already has a snippet at {prior}")

    # -- I6 (sources) ------------------------------------------------------------
    def check_sources(self) -> None:
        for path, source in self.records("sources"):
            pages = source.get("page_hashes")
            if pages is not None and "sha256" in source and site_sha256(pages) != source["sha256"]:
                self.fail(f"{path}.sha256", f"a site's sha256 is the sha256 of its page manifest; "
                                            f"page_hashes give {site_sha256(pages)}")
            fields = [f for f in ("physical_page_count", "printed_page_offset", "page_size", "page_sizes")
                      if f in source]
            if fields and source_family(source) != "pdf":
                self.fail(path, f"{', '.join(fields)} describe PDF pages; {source['id']!r} is not a PDF")
                continue
            count, last_end = source.get("physical_page_count"), 0
            for i, rng in enumerate(source.get("page_sizes", [])):
                if rng["last"] < rng["first"]:
                    self.fail(f"{path}.page_sizes[{i}]", f"last ({rng['last']}) is before first ({rng['first']})")
                if rng["first"] <= last_end:
                    self.fail(f"{path}.page_sizes[{i}]", "page_sizes ranges overlap")
                if count is not None and rng["last"] > count:
                    self.fail(f"{path}.page_sizes[{i}]", f"page {rng['last']} is past the source's "
                                                         f"{count} physical pages")
                last_end = max(last_end, rng["last"])

    # -- I4, I5, I6 ------------------------------------------------------------
    def check_items(self) -> None:
        located: set[str] = set()
        for _path, loc in self.records("locations"):
            located.update(loc["items"])
        links_by_item: dict[str, list[dict]] = {}
        for _path, link in self.records("links"):
            if not link.get("orphaned"):
                links_by_item.setdefault(link["item"], []).append(link)
        populated = {m["cluster"] for _p, m in self.records("memberships")}
        identities: dict[tuple, str] = {}
        for path, item in self.records("items"):
            rid = item["id"]
            numbered = item["form"] == "numbered"
            if not (rid in located or "quote" in item or "pages" in item):
                self.fail(path, f"item {rid!r} is anchored nowhere: it needs a location, a quote or pages")
            links = links_by_item.get(rid, [])
            roles = {link["role"] for link in links}
            coverage = item.get("coverage")
            if links and "key" not in roles and coverage is None:
                self.fail(path, f"item {rid!r} has links but no key link, so it must carry coverage "
                                f"(support_only, example_only, partial, ...)")
            if "attributed" in roles and "key" not in roles:
                self.fail(path, f"item {rid!r} has attributed links and no key link")
            if coverage in ("not_formalized", "out_of_scope") and "key" in roles:
                self.fail(f"{path}.coverage", f"item {rid!r} is {coverage} but has a key link")
            if item.get("cluster_fallback"):
                clusters = item.get("clusters", [])
                if not clusters:
                    self.fail(f"{path}.cluster_fallback", f"item {rid!r} falls back to its clusters but "
                                                          f"names no cluster")
                elif "memberships" in self.doc and not populated.intersection(clusters):
                    self.fail(f"{path}.cluster_fallback", f"item {rid!r} falls back to its clusters, and "
                                                          f"none of {clusters} has a membership")
                if "key" in roles:
                    self.fail(f"{path}.cluster_fallback", f"item {rid!r} falls back to its clusters but "
                                                          f"has a key link")
            if numbered:
                key = (item["source"], item["kind"], item["number"], item.get("sub"))
                prior = identities.setdefault(key, path)
                if prior != path:
                    self.fail(path, f"the same numbered item as {prior} "
                                    f"(source, kind, number, sub) = {list(key)!r}")
            source = self.by_id["sources"].get(item["source"])
            if "pages" in item:
                self.check_pages(f"{path}.pages", item["pages"], source)
            self.check_item_id(path, item, source)
        for path, link in self.records("links"):
            if "pages" in link:
                item = self.by_id["items"].get(link["item"])
                source = self.by_id["sources"].get(item["source"]) if item else None
                self.check_pages(f"{path}.pages", link["pages"], source)
            item = self.by_id["items"].get(link["item"])
            for i, loc_id in enumerate(link.get("locations", [])):
                loc = self.by_id["locations"].get(loc_id)
                if loc is not None and item is not None and loc["target"]["source"] != item["source"]:
                    self.fail(f"{path}.locations[{i}]", f"location {loc_id!r} is on "
                                                        f"{loc['target']['source']!r}, not on the item's "
                                                        f"source {item['source']!r}")

    def check_item_id(self, path: str, item: dict, source: dict | None) -> None:
        """I12: an item id follows its id_source (section 5.2)."""
        rid, how = item["id"], item["id_source"]
        if how == "label":
            try:
                want = ids.item_label_id(item["kind"], item["number"], item.get("sub"))
            except ValueError:
                want = None
            if want is None:
                self.fail(f"{path}.id_source", f"kind {item['kind']!r} gives no label slug; declare the id")
            elif rid != want:
                self.fail(f"{path}.id", f"a label id is <kind>-<number>[-<sub>], here {want!r}")
        elif how == "quote_hash":
            quote, where = self.passage_quote(item)
            if quote is None:
                self.fail(f"{path}.id_source", "a quote_hash id needs item.quote, or a TextQuoteSelector on "
                                               "the item's first location")
            else:
                want = ids.passage_quote_id(quote)
                if rid != want:
                    self.fail(f"{path}.id", f"a quote_hash id is passage-<8 hex of the normalised quote> "
                                            f"({where}), here {want!r}")
        elif how == "page_title_hash":
            quote, where = self.passage_quote(item)
            if quote is not None:
                self.fail(f"{path}.id_source", f"a passage with a quote ({where}) takes the quote_hash id")
            printed = None
            if source is not None:
                pages = item["pages"]
                if pages["basis"] == "printed":
                    printed = pages["first"]
                elif source.get("printed_page_offset") is not None:
                    printed = pages["first"] - source["printed_page_offset"]
            if printed is None or printed < 1:
                self.fail(f"{path}.id_source", "page_title_hash needs a printed page: pages on the printed "
                                               "basis, or a source with printed_page_offset")
            else:
                want = ids.passage_page_id(printed, item["title"])
                if rid != want:
                    self.fail(f"{path}.id", f"a page_title_hash id is passage-p<printed page>-<8 hex of "
                                            f"the normalised title>, here {want!r}")
        elif _RESERVED_PASSAGE.match(rid):
            self.fail(f"{path}.id", f"{rid!r} has the shape of a mechanical passage id; "
                                    f"a declared id may not take it")

    def passage_quote(self, item: dict) -> tuple[str | None, str]:
        """The quote a quote_hash id hashes, and where it came from (section 5.2).

        ``item.quote.exact`` when the item has one; otherwise the first
        TextQuoteSelector of the item's first location (locations in id order;
        alternatives in order, each followed by its ``refinedBy`` chain).
        """
        if "quote" in item:
            return item["quote"]["exact"], "item.quote"
        for _path, loc in self.records("locations"):
            if item["id"] not in loc["items"]:
                continue
            for sel in loc["target"]["selector"]:
                node = sel
                while isinstance(node, dict):
                    if node.get("type") == "TextQuoteSelector":
                        return node["exact"], f"the quote of location {loc['id']!r}"
                    node = node.get("refinedBy")
            return None, ""
        return None, ""

    def check_pages(self, path: str, pages: dict, source: dict | None) -> None:
        first, last = pages["first"], pages.get("last", pages["first"])
        if last < first:
            self.fail(path, f"last ({last}) is before first ({first})")
        if source is None:
            return
        if source_family(source) != "pdf":
            self.fail(path, f"pages are for a pdf source; {source['id']!r} is {source['kind']}")
            return
        count = source.get("physical_page_count")
        physical = printed_to_physical(last, pages["basis"], source)
        if count is not None and physical is not None and physical > count:
            self.fail(path, f"page {last} ({pages['basis']}) is past the source's "
                            f"{count} physical pages")

    # -- I7 --------------------------------------------------------------------
    def check_locations(self) -> None:
        anchored = {a for _p, d in self.records("declarations") for a in d.get("anchors", [])}
        anchored |= {a for _p, link in self.records("links") for a in link.get("locations", [])}
        for path, decl in self.records("declarations"):
            for i, loc_id in enumerate(decl.get("anchors", [])):
                loc = self.by_id["locations"].get(loc_id)
                src = self.by_id["sources"].get(loc["target"]["source"]) if loc else None
                if src is not None and (src["kind"] == "tex" or source_family(src) in ("pdf", "generic")):
                    what = src["kind"] if src["kind"] != "other" else f"other ({src.get('media_type')})"
                    self.fail(f"{path}.anchors[{i}]", f"an anchor shows the declaration's own text, on a lean, "
                                                      f"web, or other text/html source; {src['id']!r} is {what}")
        for path, loc in self.records("locations"):
            if not loc["items"] and loc["id"] not in anchored:
                self.fail(f"{path}.items", "a location shows at least one item, anchors a declaration, "
                                           "or is named by a link")
            target = loc["target"]
            source = self.by_id["sources"].get(target["source"])
            if source is None:
                continue
            for i, item_id in enumerate(loc["items"]):
                item = self.by_id["items"].get(item_id)
                if item is not None and item["source"] != source["id"]:
                    self.fail(f"{path}.items[{i}]", f"item {item_id!r} is on {item['source']!r}; a location "
                                                    f"shows items of its own source {source['id']!r}")
            site = source.get("scope") == "site"
            named: set[int] = set()
            for i, sel in enumerate(target["selector"]):
                spath = f"{path}.target.selector[{i}]"
                if site and sel["type"] != "SitePageSelector":
                    self.fail(spath, f"{source['id']!r} is a site: each alternative names its page "
                                     f"with a SitePageSelector")
                    continue
                named |= self.check_selector(spath, sel, source, _Ctx())
            if "pages" in loc:
                self.check_pages(f"{path}.pages", loc["pages"], source)
                pages = loc["pages"]
                lo = printed_to_physical(pages["first"], pages["basis"], source)
                hi = printed_to_physical(pages.get("last", pages["first"]), pages["basis"], source)
                if lo is not None and hi is not None:
                    outside = sorted(p for p in named if not lo <= p <= hi)
                    if outside:
                        self.fail(f"{path}.pages", f"the selectors name physical page(s) {outside}, "
                                                   f"outside {lo}..{hi}")

    def check_selector(self, path: str, sel: dict, source: dict, ctx: _Ctx) -> set[int]:
        """Check one selector and what refines it; return the physical pages it names."""
        family, stype = source_family(source), sel["type"]
        pages: set[int] = set()
        if stype not in ALLOWED_SELECTORS[family]:
            self.fail(path, f"{stype} does not apply to a {source['kind']} source")
            return pages
        child_ctx = _Ctx(page=ctx.page, refines=stype, top=False)
        if stype == "SitePageSelector":
            if source.get("scope") != "site":
                self.fail(path, f"a SitePageSelector names a page of a site; {source['id']!r} is one page")
            elif not ctx.top:
                self.fail(path, "a SitePageSelector is a top-level alternative, never a refinement")
            elif "page_hashes" in source:
                page = site_page_file(sel["path"])
                if page not in {p["path"] for p in source["page_hashes"]}:
                    self.fail(f"{path}.path", f"{page!r} is not a page in {source['id']!r}'s page_hashes")
        elif stype == "FragmentSelector":
            page = self.check_fragment(path, sel, source, ctx)
            if page is not None:
                pages.add(page)
                child_ctx.page = page
        elif stype in ("TextPositionSelector", "DataPositionSelector"):
            if sel["start"] >= sel["end"]:
                self.fail(path, f"start ({sel['start']}) must be before end ({sel['end']}); the range is half-open")
            if stype == "TextPositionSelector" and family == "pdf":
                if "extractor" not in source.get("text_layer", {}):
                    self.fail(path, f"offsets into a PDF need the source's text_layer.extractor "
                                    f"({source['id']!r} declares none)")
                if ctx.page is None:
                    self.fail(path, "offsets into a PDF count within one page: refine a page= fragment")
        elif stype == "PdfRegionSelector":
            pages.add(sel["page"])
            self.check_region(path, sel, source, ctx)
        elif stype == "RangeSelector":
            a, b = sel["startSelector"], sel["endSelector"]
            if a["type"] != b["type"]:
                self.fail(path, f"startSelector is {a['type']} and endSelector is {b['type']}; use one class")
            end_ctx = _Ctx(page=ctx.page, refines=ctx.refines, top=ctx.top)
            pages |= self.check_selector(f"{path}.startSelector", a, source, end_ctx)
            pages |= self.check_selector(f"{path}.endSelector", b, source, end_ctx)
        child = sel.get("refinedBy")
        if child is not None:
            pages |= self.check_selector(f"{path}.refinedBy", child, source, child_ctx)
        return pages

    def check_fragment(self, path: str, sel: dict, source: dict, ctx: _Ctx) -> int | None:
        """Check a FragmentSelector's value against its conformsTo; return the PDF page it names."""
        family, value = source_family(source), sel["value"]
        conforms = sel.get("conformsTo")
        if value.startswith("#"):
            self.fail(f"{path}.value", "write the fragment without its leading #")
            return None
        if conforms is None:
            self.warn(f"{path}.conformsTo", "absent (W3C: SHOULD have one); the value is not checked")
            return None
        known = CONFORMS.get(conforms)
        if known is None:
            self.warn(f"{path}.conformsTo", f"{conforms!r} is not an IRI this checker knows; "
                                            f"the value is not checked")
            return None
        grammar, families = known
        if family not in families:
            fits = sorted(iri for iri, (_g, fams) in CONFORMS.items() if family in fams)
            self.fail(f"{path}.conformsTo", f"a {source['kind']} source takes {' or '.join(fits)}")
            return None
        if grammar == "pdf":
            params = self.parse_pdf_fragment(f"{path}.value", value, source)
            if params is None:
                return None
            if sel.get("refinedBy") is not None and "page" not in params:
                self.fail(f"{path}.value", "a PDF fragment that is refined must name its page=")
            if "page" not in params:
                return None
            page = int(params["page"])
            if ctx.page is not None and page != ctx.page:
                self.fail(f"{path}.value", f"page={page} inside page={ctx.page}")
            return page
        if grammar == "text":
            m = _RFC5147.match(value)
            if not m:
                self.fail(f"{path}.value", f"{value!r} is not an RFC 5147 fragment "
                                           f"(char=|line= position or range, then ;length= or ;md5=)")
                return None
            a, b = m.group("a"), m.group("b")
            if m.group("pos") is None and a is None and b is None:
                self.fail(f"{path}.value", "an RFC 5147 range needs at least one end")
            elif a is not None and b is not None and int(a) >= int(b):
                self.fail(f"{path}.value", f"RFC 5147 range {a},{b} is empty or out of order; "
                                           f"lines L1..L2 are written line=(L1-1),L2")
        elif grammar == "html_id":
            if not re.fullmatch(r"[^\s#]+", value):
                self.fail(f"{path}.value", f"{value!r} is not an HTML element id (RFC 3236: the fragment "
                                           f"is an id, no whitespace)")
        else:
            self.check_media_fragment(f"{path}.value", value)
        return None

    def check_media_fragment(self, path: str, value: str) -> None:
        """W3C Media Fragments URI 1.0: xywh=, t=, track=, id=, joined by &.

        As the Recommendation's section 5.1.2 says, a name that is no dimension
        and a repeated dimension (the last one wins) are warnings, not errors.
        """
        seen: set[str] = set()
        for part in value.split("&"):
            name, eq, arg = part.partition("=")
            if not eq or name not in ("xywh", "t", "track", "id"):
                self.warn(path, f"{part!r} is not a Media Fragments dimension (xywh=, t=, track=, id=); "
                                f"a user agent ignores it")
                continue
            if name in seen:
                self.warn(path, f"{name}= appears more than once; the last one is used")
            seen.add(name)
            if name == "xywh":
                m = _XYWH.match(arg)
                if not m:
                    self.fail(path, f"{arg!r} is not a Media Fragments xywh= value")
                elif int(m.group("w")) == 0 or int(m.group("h")) == 0:
                    self.fail(path, "xywh width and height must be positive")
                elif m.group("unit") == "percent" and (int(m.group("x")) + int(m.group("w")) > 100
                                                       or int(m.group("y")) + int(m.group("h")) > 100):
                    self.fail(path, "a percent: region must stay within 100")
            elif name == "t":
                m = _MEDIA_T.match(arg)
                if not m or (m.group("a") is None and m.group("b") is None):
                    self.fail(path, f"{arg!r} is not a Media Fragments t= value ([npt:]start[,end])")
                elif m.group("a") is not None and m.group("b") is not None and \
                        _seconds(m.group("a")) >= _seconds(m.group("b")):
                    self.fail(path, f"t={arg}: the start must be before the end")
            elif re.search(r"\s", arg):
                self.fail(path, f"{name}= takes a percent-encoded name, got {arg!r}")

    def parse_pdf_fragment(self, path: str, value: str, source: dict) -> dict | None:
        params: dict[str, str] = {}
        for part in re.split(r"[&#]", value):
            name, eq, arg = part.partition("=")
            if not eq or name not in _PDF_PARAMS or not arg:
                self.fail(path, f"{part!r} is not an RFC 8118 parameter (name=value, one of "
                                f"{', '.join(sorted(_PDF_PARAMS))})")
                return None
            counts = _PDF_PARAMS[name]
            if counts is not None:
                args = arg.split(",")
                if name == "page":
                    if not re.fullmatch(r"[1-9]\d*", arg):
                        self.fail(path, f"page={arg} is not a positive integer")
                        return None
                elif len(args) not in counts or not all(re.fullmatch(_NUM, a) for a in args):
                    want = " or ".join(str(c) for c in counts)
                    self.fail(path, f"{name}= takes {want} numbers, got {arg!r}")
                    return None
                if name == "highlight":
                    left, right, top, bottom = (float(a) for a in args)
                    if left > right or top > bottom:
                        self.fail(path, "highlight= is left,right,top,bottom with left <= right and top <= bottom")
            params[name] = arg
        if "page" in params:
            count = source.get("physical_page_count")
            if count is not None and int(params["page"]) > count:
                self.fail(path, f"page={params['page']} is past the source's {count} physical pages")
        return params

    def check_region(self, path: str, sel: dict, source: dict, ctx: _Ctx) -> None:
        count = source.get("physical_page_count")
        if count is not None and sel["page"] > count:
            self.fail(f"{path}.page", f"page {sel['page']} is past the source's {count} physical pages")
        if ctx.refines is not None:
            if ctx.page is None:
                self.fail(path, "a PdfRegionSelector refines only a PDF page= FragmentSelector")
            elif ctx.page != sel["page"]:
                self.fail(f"{path}.page", f"page {sel['page']} disagrees with the refined page={ctx.page}")
        space = sel["coordinate_space"]
        if (space["origin"] == "top_left") != (space["y_axis"] == "down"):
            self.fail(f"{path}.coordinate_space", "top_left goes with y_axis down, bottom_left with up")
        boxes = [sel["rect"]] if "rect" in sel else []
        for i, quad in enumerate(sel.get("quads", [])):
            xs = [quad[f"x{k}"] for k in range(4)]
            ys = [quad[f"y{k}"] for k in range(4)]
            if len(set(zip(xs, ys))) < 3:
                self.fail(f"{path}.quads[{i}]", "a quad needs at least three distinct corners")
            boxes.append({"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)})
        size = source.get("page_size")
        for box in boxes:
            values = [box["x0"], box["y0"], box["x1"], box["y1"]]
            if not all(math.isfinite(v) for v in values):
                self.fail(path, "coordinates must be finite")
                continue
            if box["x0"] >= box["x1"] or box["y0"] >= box["y1"]:
                self.fail(path, "a rect needs x0 < x1 and y0 < y1")
            high = 1.0 if space["units"] == "normalized" else math.inf
            if min(values) < 0 or max(values) > high:
                self.fail(path, f"coordinates must lie in [0, {'1' if high == 1.0 else 'inf'}) "
                                f"for units {space['units']}")
            elif space["units"] == "pt" and size is not None and (
                    box["x1"] > size["width_pt"] or box["y1"] > size["height_pt"]):
                self.fail(path, f"the rect leaves the {jcs_number(size['width_pt'])} x "
                                f"{jcs_number(size['height_pt'])} pt page")

    # -- I8 --------------------------------------------------------------------
    def check_declarations(self) -> None:
        for path, d in self.records("declarations"):
            rid, source, kind = d["id"], d["id_source"], d["kind"]
            if "signature" in d:
                sig = d["signature"]
                if ids.normalize_statement(sig) != sig:
                    self.fail(f"{path}.signature", "must be normalised: comments removed, each run of "
                                                   "whitespace one space")
                elif "statement_sha256" in d and ids.statement_sha256(sig) != d["statement_sha256"]:
                    self.fail(f"{path}.signature", "its sha256 is not statement_sha256")
            if d.get("parent") == rid:
                self.fail(f"{path}.parent", "a declaration is not its own parent")
            parent = self.by_id["declarations"].get(d.get("parent"))
            parent_name = _full_name(parent) if parent is not None else None
            if "namespace" in d and parent_name is not None and d["namespace"] != parent_name:
                self.fail(f"{path}.namespace", f"a {kind}'s namespace is its parent's namespace plus the "
                                               f"parent's name, {parent_name!r}")
            if "namespace" in d and "name" in d and kind in ("field", "projection") and \
                    not d["name"].startswith(d["namespace"] + "." if d["namespace"] else ""):
                self.fail(f"{path}.name", f"a {kind}'s full name is its namespace, a dot and its own name")
            if source == "name":
                if d["private"] or d["anonymous"]:
                    self.fail(f"{path}.id_source", "a private or anonymous declaration is not identified by name")
                if not _NAMED_ID.match(rid):
                    self.fail(f"{path}.id", f"{rid!r} is not a fully qualified Lean name")
                if "name" in d and d["name"] != rid:
                    self.fail(f"{path}.name", f"a name-identified declaration's id is its name ({rid!r})")
            elif source == "private":
                if not d["private"] or d["anonymous"]:
                    self.fail(f"{path}.id_source", "id_source private needs private true and anonymous false")
                want = f"private:{d['module']}:{d.get('name', '')}"
                if rid != want:
                    self.fail(f"{path}.id", f"a private declaration's id is private:<module>:<name>, here {want!r}")
            else:
                if not d["anonymous"]:
                    self.fail(f"{path}.id_source", f"id_source {source} is for anonymous declarations")
                pattern = _HASH_ID if source == "statement_hash" else _DECLARED_ID
                m = pattern.match(rid)
                if not m:
                    form = "<keyword>:<12 hex>[~k]" if source == "statement_hash" else \
                        "<keyword>:<slug> (a slug of 12 hex digits is reserved for statement hashes)"
                    self.fail(f"{path}.id", f"{rid!r} is not of the form {form}")
                else:
                    if m.group("kw") != kind:
                        self.fail(f"{path}.id", f"the id says {m.group('kw')} but kind is {kind}")
                    if source == "statement_hash":
                        if "statement_sha256" in d and m.group("hex") != d["statement_sha256"][:12]:
                            self.fail(f"{path}.id", "the id's hash is not the first 12 digits of statement_sha256")
                        if m.group("k") is not None:
                            self.check_ordinal(path, d)
                if "name" in d:
                    self.fail(f"{path}.name", "an anonymous declaration has no name")

    def check_ordinal(self, path: str, d: dict) -> None:
        """A live ``~k`` id: its latest ``ordinal_assigned`` record names the declaration's file."""
        rid = d["id"]
        records = self.ordinal_records.get(rid, [])
        if not records:
            self.fail(f"{path}.id", f"{rid!r} carries an ordinal, which is positional: record the assignment "
                                    f"in the changelog (event ordinal_assigned, with the file) or declare an id")
            return
        latest = max(_at_key(c["at"]) for c in records)
        files = sorted({c["file"] for c in records if _at_key(c["at"]) == latest})
        if len(files) > 1:
            self.fail(f"{path}.id", f"the latest ordinal_assigned records for {rid!r} name different files "
                                    f"{files}")
        elif files[0] != d["file"]:
            self.fail(f"{path}.id", f"the latest ordinal_assigned record gives {rid!r} to a declaration in "
                                    f"{files[0]!r}, and it is in {d['file']!r}: an added or deleted copy moved "
                                    f"the ordinal; record the new assignment, or declare an id")

    # -- I9 --------------------------------------------------------------------
    def check_changelog(self) -> None:
        live = {"declaration": self.by_id["declarations"], "item": self.by_id["items"],
                "location": self.by_id["locations"], "artifact": self.by_id["artifacts"]}
        for path, change in self.records("changelog"):
            pool = live[change["subject_kind"]]
            subject, event = change["subject"], change["event"]
            if event in ("retired", "renamed") and subject in pool:
                self.fail(f"{path}.subject", f"{subject!r} is {event}, so it may not be used again")
            for name in ("successor", "possible_successor"):
                if name not in change:
                    continue
                if change[name] == subject:
                    self.fail(f"{path}.{name}", f"the {name} differs from the subject")
                elif change[name] not in pool:
                    self.fail(f"{path}.{name}", f"{change[name]!r} is not a live {change['subject_kind']} id")
            if event == "ordinal_assigned":
                decl = pool.get(subject)
                m = _HASH_ID.match(subject)
                if change["subject_kind"] != "declaration" or m is None or m.group("k") is None:
                    self.fail(f"{path}.subject", "ordinal_assigned records a declaration's <keyword>:<12 hex>~k id")
                elif decl is None:
                    gone = self.gone_at.get(subject)
                    when = _parse_at(change["at"])
                    if when is not None and (gone is None or gone < when):
                        self.fail(f"{path}.subject", f"{subject!r} is not a live declaration id, and no later "
                                                     f"changelog entry retires or renames it")

    # -- I13 -------------------------------------------------------------------
    def check_attribution(self) -> None:
        linked = {link["declaration"]: path for path, link in self.records("links")
                  if "declaration" in link and not link.get("orphaned")}
        for path, row in self.records("unattributed"):
            if row["declaration"] in linked:
                self.fail(f"{path}.declaration", f"{row['declaration']!r} is linked at "
                                                 f"{linked[row['declaration']]}, so it is not unattributed")

    # -- I10 -------------------------------------------------------------------
    def check_urls(self) -> None:
        def walk(value: Any, path: str) -> None:
            if isinstance(value, str):
                if _URL.search(value):
                    self.fail(path, "http(s) URLs belong in sources[] and destinations[] only")
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    walk(v, f"{path}[{i}]")
            elif isinstance(value, dict):
                quote = value.get("type") == "TextQuoteSelector"
                for k, v in value.items():
                    if k in ("conformsTo", "$schema"):
                        continue          # identifiers, never fetched
                    if quote and k in ("exact", "prefix", "suffix"):
                        continue          # source text, an anchor and not a link
                    walk(v, f"{path}.{k}")
        for key, value in self.doc.items():
            if key in ("sources", "destinations"):
                continue
            if key == "items" and isinstance(value, list):
                for i, item in enumerate(value):
                    walk({k: v for k, v in item.items() if k != "quote"}, f"$.items[{i}]")
                continue
            walk(value, f"$.{key}")

    def run(self) -> list[str]:
        self.check_ids_and_order()
        self.check_references()
        self.check_sources()
        self.check_items()
        self.check_locations()
        self.check_declarations()
        self.check_changelog()
        self.check_attribution()
        self.check_urls()
        return self.problems


def _seconds(clock: str) -> float:
    total = 0.0
    for part in clock.split(":"):
        total = total * 60 + float(part)
    return total


def select_schema(doc: Any) -> tuple[Path | None, list[str], list[str]]:
    """The schema file for ``doc``'s major version, plus problems and warnings.

    A document whose ``schema_version`` cannot be read is checked against this
    checker's own major version, so the schema reports the malformed value.
    """
    version = doc.get("schema_version") if isinstance(doc, dict) else None
    m = _VERSION.match(version) if isinstance(version, str) else None
    if m is None:
        return SCHEMA_PATH, [], []
    major, minor = int(m.group("major")), int(m.group("minor"))
    path = schema_path(major)
    if not path.exists():
        return None, [f"$.schema_version: no schema for major version {major} "
                      f"(this checker has {vocab.ANNOTATION_SCHEMA_MAJOR})"], []
    warnings = []
    if major == vocab.ANNOTATION_SCHEMA_MAJOR and minor > _KNOWN_MINOR:
        warnings.append(f"$.schema_version: {version} is newer than this checker "
                        f"({vocab.ANNOTATION_SCHEMA_VERSION}); keys added since are refused")
    return path, [], warnings


def check(doc: Any, *, text: str | None = None, canonical: bool = False,
          schema: dict | None = None) -> Report:
    """Problems and warnings for ``doc``.

    ``text`` is the file's content, needed only for ``canonical``.  ``schema``
    overrides the choice by major version.  Semantic checks run only on a
    document the schema accepts: they rely on its shape.
    """
    report = Report()
    if schema is None:
        path, problems, warnings = select_schema(doc)
        report.warnings += warnings
        if path is None:
            report.problems += problems
            return report
        schema = load_schema(path)
    checker = Validator(schema)
    report.problems += [f"{path}: {message}" for path, message in checker.errors(doc)]
    if report.problems:
        return report
    semantic = Checker(doc)
    report.problems += semantic.run()
    report.warnings += semantic.warnings
    if canonical and text is not None and not report.problems:
        if text != canonical_dumps(canonicalize(doc)):
            report.problems.append("$: the file is not in canonical form (RFC 8785 key order and numbers, "
                                   "2-space indent, LF, arrays sorted by id); --print-canonical writes it")
    return report


def validate(doc: Any, *, text: str | None = None, canonical: bool = False,
             schema: dict | None = None) -> list[str]:
    """Every problem with ``doc`` as ``path: message``; empty when it is valid."""
    return check(doc, text=text, canonical=canonical, schema=schema).problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate.py",
        description="Validate lean-companion annotation documents (schema v1).")
    parser.add_argument("documents", nargs="+", type=Path, help="annotation JSON files")
    parser.add_argument("--canonical", action="store_true",
                        help="also require the canonical serialisation, byte for byte")
    parser.add_argument("--print-canonical", action="store_true",
                        help="print the canonical form of the (single) document and exit")
    parser.add_argument("--schema", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.print_canonical and len(args.documents) != 1:
        parser.error("--print-canonical takes exactly one document")
    schema = load_schema(args.schema) if args.schema is not None else None
    status = 0
    for path in args.documents:
        try:
            text = path.read_text(encoding="utf-8")
            doc = loads(text)
        except (OSError, UnicodeDecodeError, LoadError) as exc:
            print(f"{path}: cannot read: {exc}", file=sys.stderr)
            return 2
        if args.print_canonical:
            if not isinstance(doc, dict):
                print(f"{path}: the document must be a JSON object", file=sys.stderr)
                return 1
            sys.stdout.write(canonical_dumps(canonicalize(doc)))
            return 0
        report = check(doc, text=text, canonical=args.canonical, schema=schema)
        for warning in report.warnings:
            print(f"{path}: warning: {warning}")
        if report.problems:
            status = 1
            for problem in report.problems:
                print(f"{path}: {problem}")
        else:
            counts = ", ".join(f"{len(doc.get(k, []))} {k}" for k in RECORD_ARRAYS if k in doc)
            print(f"{path}: ok ({counts})")
    return status


if __name__ == "__main__":
    sys.exit(main())
