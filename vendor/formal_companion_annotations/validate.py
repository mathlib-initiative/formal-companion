#!/usr/bin/env python3
"""Validate a formal-companion annotation document: I-JSON, schema, then invariants.

Implements docs/annotations.md (sections 9 to 12).  Usage::

    formal-companion-annotations-validate <document.json> [...] [--canonical] [--lint NAME]
    formal-companion-annotations-validate <document.json> --print-canonical
    formal-companion-annotations-validate <document.json> [...] --format json
    python3 -m formal_companion_annotations.validate <document.json> [...]

A document is read as bytes and decoded as strict UTF-8; :func:`loads` then
refuses anything that is not I-JSON (RFC 7493) with the limits of section 9.1:
a byte-order mark, a duplicate key, ``NaN``/``Infinity``, a number that is not
finite, an integer-valued number outside +-(2^53 - 1), a numeric literal of
more than 400 characters, a lone surrogate in a string or key, or nesting
deeper than 64 levels.  :func:`check` runs the same tests on an in-memory
object first (code ``IJSON``), so nothing later can crash on such input.

The document's ``schema_version`` (``"<major>.<minor>"``) selects the JSON
Schema of the minor it declares (annotations-v<major>.<minor>.schema.json,
shipped in this package's ``schema/`` directory), so a document that says
``"1.0"`` and uses a key added in 1.1 fails.  A document that declares a core
or profile minor newer than this checker knows is checked in *forward mode*
against the newest schema of that major: an unknown key of a closed object
becomes a ``W-NEWER`` warning and the semantic checks run on the keys the
checker knows; an unknown major, core or profile, is a problem.  The schema is
evaluated first, by the package's own ``schemacheck`` because the package is
standard library only.  When it passes, the semantic invariants run; they are
the rules a JSON Schema cannot state: unique and sorted ids, references that
resolve, the anchoring and coverage rules for items, the item and declaration
id rules, selector grammars against their ``conformsTo``, the changelog's
rules and the profile gating.  Every problem and warning is a :class:`Problem`
with a stable code: ``IJSON``, ``SCHEMA``, ``I1``..``I9`` and ``I11``..``I14``
for the invariants of the specification's section 9.3 table (``I10`` is
retired), ``L1`` for the ``external-urls`` lint, and the warnings
``W-CONFORMS`` (an unknown or missing ``conformsTo`` IRI), ``W-MEDIA`` (a
Media Fragments name that is no dimension, or a repeated one), ``W-RELATION``
(a link ``relation`` outside the documented core), ``W-XREF`` (an xref
``database`` outside the documented core), ``W-NEWER`` (a newer minor; unknown
keys ignored) and ``W-SCHEMA-ID`` (a root ``$schema`` that is not the declared
minor's ``$id``).
``--canonical`` also requires the file's exact bytes to be the canonical
serialisation (section 10: RFC 8785 order and numbers, two-space indent, LF).
``--print-canonical`` sorts the document's arrays, validates the result, and
then writes the canonical form as UTF-8 bytes.  ``--version`` prints the package version.
Exit status: 0 every document valid, 1 at least one invalid, 2 at least one
unreadable (not I-JSON), a usage error, a broken install (the packaged schema
file is missing) or an internal error; 2 wins over 1.  Every document given is
checked.  An unexpected exception while checking a document is reported on
stderr as ``<file>: internal error: <type>: <message>`` and counts as 2; a
traceback never reaches the user.
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
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import __version__, ids, vocab
from .schemacheck import _NO_WHITESPACE, _UNKNOWN_KEY, _WHITESPACE, Validator, _child

#: The stable public API of this module: exactly these names.  Anything else,
#: underscored or not, is internal and may change in any release.
__all__ = [
    "LINTS", "LoadError", "PROFILES", "Problem", "RECORD_ARRAYS", "Report", "SCHEMA_DIR", "SCHEMA_PATH",
    "canonical_dumps", "canonicalize", "check", "loads", "main", "order_key", "schema_path",
    "site_sha256", "validate",
]

HERE = Path(__file__).resolve().parent
#: The schema files ship inside the package (package data), so an installed
#: copy finds them without the repository's top-level ``schema/`` directory.
SCHEMA_DIR = HERE / "schema"
_KNOWN_MAJOR = vocab.ANNOTATION_SCHEMA_MAJOR
_KNOWN_MINOR = int(vocab.ANNOTATION_SCHEMA_VERSION.split(".")[1])
_KNOWN_PROFILE_MINOR = {name: int(version.split(".")[1])
                        for name, version in vocab.ANNOTATION_PROFILE_VERSIONS.items()}


def schema_path(major: int, minor: int) -> Path:
    """The schema file for one format minor: ``annotations-v<major>.<minor>.schema.json``."""
    return SCHEMA_DIR / f"annotations-v{major}.{minor}.schema.json"


def schema_id(major: int, minor: int) -> str:
    """The ``$id`` of one format minor's schema: an identifier, never fetched."""
    return f"{vocab.ANNOTATION_SCHEMA_ID_BASE}/v{major}.{minor}/schema.json"


#: The newest schema this package knows: the one a newer document is checked against.
SCHEMA_PATH = schema_path(_KNOWN_MAJOR, _KNOWN_MINOR)

#: The opt-in lints ``check(lints=...)`` and ``--lint`` know.
LINTS: frozenset[str] = frozenset({"external-urls"})

#: Root arrays, in the order the checks walk them.
RECORD_ARRAYS = ("sources", "provenance", "clusters", "items", "declarations", "artifacts",
                 "memberships", "locations", "links", "destinations", "snippets", "verdicts",
                 "unattributed", "changelog")

#: What each profile owns (docs/annotations.md section 4.2), the complete list:
#: its root arrays, and the fields it owns on other records as ``<root
#: array>.<field>`` (or ``companion.<field>``).  Invariant I14: an owned array
#: or field appears only in a document whose ``profiles`` declares the profile.
#: A reader that does not implement a profile ignores exactly these.  A
#: source's ``role`` is core: a document with no verdicts may still say which
#: source it audits.
PROFILES: dict[str, dict[str, tuple[str, ...]]] = {
    "audit": {"arrays": ("verdicts",), "fields": ()},
    "graph": {"arrays": ("artifacts", "clusters", "memberships"),
              "fields": ("companion.graph", "declarations.unlocated", "destinations.artifact",
                         "items.clusters", "links.cluster")},
    "history": {"arrays": ("changelog",), "fields": ("declarations.replaced_by", "links.orphaned")},
    "site": {"arrays": ("destinations", "snippets"), "fields": ()},
}

#: Lists of ids (or plain strings) that are sets: kept sorted and duplicate-free.
#: A dotted name reaches into a sub-object of the record.
SET_FIELDS = {
    "sources": ("alternate_urls", "identifiers.isbn"),
    "items": ("clusters", "legacy_ids", "relations.same_as", "relations.part_of", "relations.near"),
    "declarations": ("anchors", "legacy_ids"),
    "locations": ("items", "legacy_ids"),
    "links": ("locations",),
    "clusters": ("items", "related_to"),
    "provenance": ("agents",),
    "unattributed": ("signals",),
    "verdicts": ("locations", "resolution.commits", "resolution.replaced_by"),
}
#: Lists of objects kept sorted by a key, one entry per key value.  A string is
#: one key; a tuple of keys means alternatives (entries with the first key come
#: first, sorted by it, then entries with the second: ``provenance.inputs``,
#: files by ``path``, then sources by ``source``); a list of keys is a composite
#: key, every field in order (``xrefs`` by ``(database, tag)``).
#: ``companion.packages`` (by ``name``) is not in a record array and is handled
#: on its own.
KEYED_LISTS = {
    "sources": (("page_sizes", "first"), ("page_hashes", "path")),
    "items": (("xrefs", ["database", "tag"]),),
    "provenance": (("inputs", ("path", "source")),),
    "verdicts": (("declarations", "declaration"),),
}

#: Which rules a source follows, by kind or (for ``other``) by media type.
FAMILY_BY_KIND = {"pdf": "pdf", "web": "html", "tex": "text", "lean": "text"}
#: Selector types each family may use (docs/annotations.md section 6.2).
ALLOWED_SELECTORS = {
    "pdf": {"FragmentSelector", "TextQuoteSelector", "PdfRegionSelector"},
    "html": {"FragmentSelector", "TextQuoteSelector", "CssSelector", "SitePageSelector"},
    "text": {"FragmentSelector", "TextQuoteSelector"},
    "generic": {"FragmentSelector", "TextQuoteSelector", "CssSelector"},
}
#: A ``refinedBy`` chain is at most this many selectors deep (section 6.6).
MAX_REFINEMENT_DEPTH = 8
#: Known conformsTo IRIs: the grammar their value follows and the families they fit.
CONFORMS = {
    vocab.CONFORMS_TO_PDF: ("pdf", {"pdf"}),
    vocab.CONFORMS_TO_PDF_RFC3778: ("pdf", {"pdf"}),
    vocab.CONFORMS_TO_TEXT: ("text", {"text"}),
    vocab.CONFORMS_TO_HTML: ("html_id", {"html"}),
    vocab.CONFORMS_TO_MEDIA: ("media", {"html", "generic"}),
}

# I-JSON limits (docs/annotations.md section 9.1).
_MAX_INT = 2 ** 53 - 1
_MAX_NUMBER_CHARS = 400
_MAX_DEPTH = 64
_SURROGATE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")
_BOM = chr(0xFEFF)

# The checker's own grammars: ASCII digits only, integers of at most 9 digits,
# every one used with ``fullmatch``.  "No whitespace" is schemacheck's explicit
# class, the one the schema's patterns use, so the two never disagree.
_WS_RE = re.compile("[" + _WHITESPACE + "]")
_NUM = r"-?(?:[0-9]{1,9}(?:\.[0-9]{0,9})?|\.[0-9]{1,9})"
_RFC5147 = re.compile(
    r"(?P<scheme>char|line)=(?:(?P<pos>[0-9]{1,9})|(?P<a>[0-9]{1,9})?,(?P<b>[0-9]{1,9})?)"
    r"(?P<integrity>(?:;(?:length=[0-9]{1,9}(?:,[A-Za-z0-9._-]+)?|md5=[0-9A-Fa-f]{32}))*)")
_XYWH = re.compile(r"(?:(?P<unit>pixel|percent):)?(?P<x>[0-9]{1,9}),(?P<y>[0-9]{1,9}),"
                   r"(?P<w>[0-9]{1,9}),(?P<h>[0-9]{1,9})")
_CLOCK = (r"[0-9]{1,9}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{0,9})?|[0-9]{2}:[0-9]{2}(?:\.[0-9]{0,9})?"
          r"|[0-9]{1,9}(?:\.[0-9]{0,9})?")                   # npt-hhmmss | npt-mmss | npt-sec
_MEDIA_T = re.compile(r"(?:npt:)?(?P<a>" + _CLOCK + r")?(?:,(?P<b>" + _CLOCK + r"))?")
_PAGE = re.compile(r"[1-9][0-9]{0,8}")
_HTML_ID = re.compile("[^" + _WHITESPACE + "#]+")
_PDF_PARAMS = {  # name -> allowed counts of comma-separated numbers (None: free text)
    "page": (1,), "nameddest": None, "structelem": None, "comment": None, "ef": None,
    "zoom": (1, 3), "view": None, "viewrect": (4,), "highlight": (4,), "search": None, "fdf": None,
}
_NAMED_ID = re.compile(r"(?!(?:private|example|instance):)[^" + _WHITESPACE + ":]" + _NO_WHITESPACE + "*")
_HASH_ID = ids.HASH_ID
_DECLARED_ID = ids.DECLARED_ID
_RESERVED_PASSAGE = re.compile(r"(?:" + vocab.PASSAGE_QUOTE_ID_PATTERN + "|"
                               + vocab.PASSAGE_PAGE_ID_PATTERN + r")")
_VERSION = re.compile(r"(?P<major>[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)")
_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})"
                   r"(?:T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?Z)?")
#: A schema ``pattern`` that starts like this is a date or timestamp field (I9 checks its value).
_DATE_PATTERN_PREFIX = "^[0-9]{4}-[0-9]{2}-[0-9]{2}"
#: The ``external-urls`` lint: ``<scheme>://`` for any RFC 3986 scheme anywhere
#: in the string; a protocol-relative ``//host`` or a ``javascript:``/``data:``/
#: ``mailto:``/``file:`` prefix only at the start of the string.
_URL_LIKE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]*://|^//|^(?:javascript|data|mailto|file):)" + _NO_WHITESPACE + "*",
    re.IGNORECASE)
_QUOTE_KEYS = ("exact", "prefix", "suffix")
_SELECTOR_CHILDREN = ("refinedBy",)


# ---------------------------------------------------------------------------
# problems and reports
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Problem:
    """One finding: a stable ``code`` (module docstring), a JSON path and a message.

    Codes, exit statuses and output line shapes are under semver; message
    wording is not.
    """
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class Report:
    """What :func:`check` found: problems make a document invalid, warnings do not."""
    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


# ---------------------------------------------------------------------------
# loading (I-JSON) and canonical form
# ---------------------------------------------------------------------------

class LoadError(ValueError):
    """The file is not an I-JSON document this checker can read."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise LoadError(f"duplicate key {key!r} in one object")
        out[key] = value
    return out


def _no_constants(name: str) -> Any:
    raise LoadError(f"{name} is not JSON")


def _short(text: str, limit: int = 80) -> str:
    """``text`` for a message, cut (with an ellipsis) when it is long."""
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _short_int(value: int) -> str:
    """An integer for a message; a huge one only by its size (``str`` of it may be refused)."""
    if value.bit_length() <= 256:
        return str(value)
    return f"an integer of about {int(value.bit_length() * 0.30103) + 1} digits"


def _parse_int(literal: str) -> int:
    if len(literal) > _MAX_NUMBER_CHARS:
        raise LoadError(f"a number of {len(literal)} characters is longer than {_MAX_NUMBER_CHARS}")
    value = int(literal)
    if abs(value) > _MAX_INT:
        raise LoadError(f"integer {literal} is outside +-(2^53 - 1), the I-JSON range")
    return value


def _parse_float(literal: str) -> int | float:
    if len(literal) > _MAX_NUMBER_CHARS:
        raise LoadError(f"a number of {len(literal)} characters is longer than {_MAX_NUMBER_CHARS}")
    value = float(literal)
    if not math.isfinite(value):
        raise LoadError(f"{literal} is not a finite number")
    if value.is_integer():
        if abs(value) > _MAX_INT:
            raise LoadError(f"integer-valued number {literal} is outside +-(2^53 - 1), the I-JSON range")
        return int(value)
    return value


def _trail_path(trail: tuple | None) -> str:
    keys: list[Any] = []
    while trail is not None:
        trail, key = trail
        keys.append(key)
    path = "$"
    for key in reversed(keys):
        path = _child(path, key)
    return path


def _ijson_scan(value: Any) -> tuple[list[Problem], bool]:
    """Problems (code ``IJSON``) with an in-memory value, and whether it holds an
    integer-valued float (``6.0``) that :func:`_integral_to_int` should turn into an int.

    Iterative, so a value nested too deep is reported, not a RecursionError.
    """
    problems: list[Problem] = []
    integral = False
    stack: list[tuple[Any, int, tuple | None]] = [(value, 1, None)]
    while stack:
        node, depth, trail = stack.pop()
        if isinstance(node, dict):
            if depth > _MAX_DEPTH:
                problems.append(Problem("IJSON", _trail_path(trail), f"nested deeper than {_MAX_DEPTH} levels"))
                continue
            for key in reversed(list(node)):
                if not isinstance(key, str):
                    problems.append(Problem("IJSON", _trail_path(trail), f"key {key!r} is not a string"))
                    continue
                if _SURROGATE.search(key):
                    problems.append(Problem("IJSON", _trail_path(trail),
                                            f"key {ascii(key)} contains a lone surrogate (not UTF-8 text)"))
                stack.append((node[key], depth + 1, (trail, key)))
        elif isinstance(node, list):
            if depth > _MAX_DEPTH:
                problems.append(Problem("IJSON", _trail_path(trail), f"nested deeper than {_MAX_DEPTH} levels"))
                continue
            for i in range(len(node) - 1, -1, -1):
                stack.append((node[i], depth + 1, (trail, i)))
        elif isinstance(node, str):
            if _SURROGATE.search(node):
                problems.append(Problem("IJSON", _trail_path(trail), "contains a lone surrogate (not UTF-8 text)"))
        elif isinstance(node, bool) or node is None:
            pass
        elif isinstance(node, int):
            if abs(node) > _MAX_INT:
                problems.append(Problem("IJSON", _trail_path(trail),
                                        f"{_short_int(node)} is outside +-(2^53 - 1), the I-JSON integer range"))
        elif isinstance(node, float):
            if not math.isfinite(node):
                problems.append(Problem("IJSON", _trail_path(trail), f"{node!r} is not a finite number"))
            elif node.is_integer():
                integral = True
        else:
            problems.append(Problem("IJSON", _trail_path(trail), f"a {type(node).__name__} is not a JSON value"))
    return problems, integral


def _integral_to_int(value: Any) -> Any:
    """A copy with every integer-valued float written as an int (``6.0`` -> ``6``)."""
    if isinstance(value, dict):
        return {k: _integral_to_int(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_integral_to_int(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def loads(text: str | bytes) -> Any:
    """Parse strictly as I-JSON (section 9.1).

    ``bytes`` are decoded as strict UTF-8.  Refused with :class:`LoadError`: a
    byte-order mark, a duplicate key, ``NaN``/``Infinity``, a number that is not
    finite or an integer-valued one outside +-(2^53 - 1), a numeric literal
    longer than 400 characters, a lone surrogate in a string or key, and
    nesting deeper than 64 levels.  Integer-valued numbers come back as
    ``int`` whatever their spelling (``6.0``, ``1e3`` -> ``6``, ``1000``).
    """
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LoadError(f"not UTF-8: {exc}") from exc
    elif not isinstance(text, str):
        raise TypeError("loads() takes str or bytes")
    if text.startswith(_BOM):
        raise LoadError("starts with a byte-order mark, which I-JSON forbids")
    try:
        doc = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constants,
                         parse_int=_parse_int, parse_float=_parse_float)
    except json.JSONDecodeError as exc:
        raise LoadError(f"not JSON: {exc}") from exc
    except RecursionError:
        raise LoadError(f"nested deeper than {_MAX_DEPTH} levels") from None
    problems, _integral = _ijson_scan(doc)
    if problems:
        raise LoadError(f"{problems[0].path}: {problems[0].message}")
    return doc


def jcs_number(value: int | float) -> str:
    """A number as RFC 8785 writes it: ECMAScript Number::toString of the IEEE double.

    The shortest digits that round-trip, integers without ``.0``, and plain
    decimal notation for 1e-6 <= |x| < 1e21 (so ``1.0`` is ``1`` and ``0.00001``
    stays ``0.00001``); exponent form ``1e+21`` / ``1e-7`` outside that range.
    An integer beyond 2**53 is written as the double it becomes; one beyond
    the range of a double raises ``ValueError`` (a valid document has neither:
    section 9.1 keeps integers within 2^53 - 1).
    """
    if isinstance(value, bool):
        raise TypeError("a boolean is not a number")
    if isinstance(value, int) and abs(value) <= 2 ** 53:
        return str(value)
    try:
        x = float(value)
    except OverflowError:
        raise ValueError(f"{_short_int(value)} is too large for a double") from None
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
    """The section 10 order: strings by UTF-16 code units (RFC 8785), numbers by value.

    One order for object keys, record ids, id lists and keyed lists, so a
    producer that sorts as JavaScript's default ``Array.prototype.sort`` does
    (by UTF-16 code units) writes the same bytes.  It differs from code point
    order only for a character above U+FFFF against one in U+E000..U+FFFF.
    """
    if isinstance(value, str):
        return _utf16_key(value)
    return value


def _sort_key(value: Any) -> tuple:
    """A total order for the sorts of :func:`canonicalize`: strings in the section 10
    order, then numbers by value, then anything else by its ``repr``.

    A valid document has only strings where these sorts look (ids, paths,
    names); the rest is here so that a document the schema will refuse still
    canonicalises instead of raising a ``TypeError`` from inside ``sort``.
    """
    if isinstance(value, str):
        return 0, _utf16_key(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 1, value
    return 2, repr(value)


def _entry_key(entry: Any, key: str | tuple[str, ...] | list[str]) -> tuple:
    """Where an entry of a keyed list sorts: by its composite key (a list of
    fields), or by its first present alternative key (a string, or a tuple)."""
    if isinstance(key, list):
        if isinstance(entry, dict):
            return 0, tuple(_sort_key(entry.get(name, "")) for name in key)
        return 1, ()
    keys = (key,) if isinstance(key, str) else key
    if isinstance(entry, dict):
        for i, name in enumerate(keys):
            if name in entry:
                return i, _sort_key(entry[name])
    return len(keys), (0, b"")


def _key_label(key: str | tuple[str, ...] | list[str]) -> str:
    if isinstance(key, list):
        return "(" + ", ".join(key) + ")"
    return key if isinstance(key, str) else ", then ".join(key)


def _emit(value: Any, depth: int, out: list[str]) -> None:
    pad = "  " * (depth + 1)
    if isinstance(value, dict):
        if not value:
            out.append("{}")
            return
        for key in value:
            if not isinstance(key, str):
                raise TypeError(f"an object key must be a string, not {type(key).__name__}: {key!r}")
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
    """The canonical text (docs/annotations.md section 10).

    RFC 8785 (JCS) for key order (UTF-16 code units) and for numbers
    (:func:`jcs_number`); strings escaped as ECMAScript ``JSON.stringify``
    does, with non-ASCII written as UTF-8; laid out with a two-space indent,
    ``": "`` after a key, LF line ends and a final newline.

    ``doc`` is a value :func:`loads` would return.  Anything else raises
    ``TypeError`` or ``ValueError`` naming the offending value: a non-string
    key, an integer too large for a double, an object that is not JSON.
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

    Everything sorts in the section 10 order (:func:`order_key`): UTF-16 code
    units, the order RFC 8785 gives object keys.  ``doc`` is a value
    :func:`loads` would return; a set field whose elements are not all strings
    is left as it is, for the schema to report.
    """
    out = copy.deepcopy(doc)
    companion = out.get("companion")
    if isinstance(companion, dict) and isinstance(companion.get("packages"), list):
        companion["packages"].sort(key=lambda p: _entry_key(p, "name"))
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
                if isinstance(values, list) and all(isinstance(v, str) for v in values):
                    _set(rec, dotted, sorted(set(values), key=order_key))
            for field_name, key in KEYED_LISTS.get(name, ()):
                values = rec.get(field_name)
                if isinstance(values, list):
                    values.sort(key=lambda d, k=key: _entry_key(d, k))
    return out


def site_sha256(page_hashes: list[dict]) -> str:
    """The sha256 of a ``scope: site`` source (section 5.1).

    The sha256 of the manifest with one line ``"<sha256 of the page's bytes>  <path>\\n"``
    per listed page (two spaces, as ``sha256sum`` writes it), the lines ordered
    by path in the section 10 order, encoded as UTF-8.
    """
    lines = sorted(page_hashes, key=lambda p: order_key(p["path"]))
    manifest = "".join(f"{p['sha256']}  {p['path']}\n" for p in lines)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def site_page_file(path: str) -> str:
    """The page file a ``SitePageSelector`` path names: ``index.html`` of a directory path."""
    return path + "index.html" if path.endswith("/") else path


def _parse_at(at: str) -> tuple | None:
    """A date or UTC instant as a sortable key, or None when it is not one.

    ``(year, month, day, hour, minute, second, fraction)``: a bare date is the
    start of its day, and the fraction is its digit string without trailing
    zeros, so instants compare with their full precision (no truncation to
    microseconds).  A calendar check refuses ``2026-02-30`` and ``T25:00:00Z``;
    a leap second ``T23:59:60Z`` is accepted.
    """
    m = _DATE.fullmatch(at)
    if m is None:
        return None
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    try:
        dt.date(year, month, day)
    except ValueError:
        return None
    if m[4] is None:
        return (year, month, day, 0, 0, 0, "")
    hour, minute, second = int(m[4]), int(m[5]), int(m[6])
    if hour > 23 or minute > 59 or second > 60 or (second == 60 and (hour, minute) != (23, 59)):
        return None
    return (year, month, day, hour, minute, second, (m[7] or "").rstrip("0"))


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


def _collinear(points: list[tuple[Any, Any]]) -> bool:
    """Are all the points on one line?  Exact (rationals), so no rounding decides it."""
    x0, y0 = Fraction(points[0][0]), Fraction(points[0][1])
    base = next(((Fraction(x), Fraction(y)) for x, y in points if (Fraction(x), Fraction(y)) != (x0, y0)),
                None)
    if base is None:
        return True
    dx, dy = base[0] - x0, base[1] - y0
    return all((Fraction(x) - x0) * dy == (Fraction(y) - y0) * dx for x, y in points)


# ---------------------------------------------------------------------------
# the checker
# ---------------------------------------------------------------------------

_SCHEMA_CACHE: dict[Path, dict] = {}
_DATE_CHAINS: dict[int, tuple[dict, list[tuple[str, ...]]]] = {}


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    """The schema at ``path``, parsed once per process (a shared object: do not mutate it)."""
    path = Path(path)
    schema = _SCHEMA_CACHE.get(path)
    if schema is None:
        schema = _SCHEMA_CACHE[path] = json.loads(path.read_text(encoding="utf-8"))
    return schema


def _date_chains(schema: dict) -> list[tuple[str, ...]]:
    """Every property chain in ``schema`` whose value is a date or timestamp.

    Found by the shape of the ``pattern`` (:data:`_DATE_PATTERN_PREFIX`), so a
    field added to the schema is checked without a change here.  ``"*"``
    stands for an array element.
    """
    cached = _DATE_CHAINS.get(id(schema))
    if cached is not None and cached[0] is schema:
        return cached[1]
    found: set[tuple[str, ...]] = set()

    def resolve(ref: str) -> Any:
        node: Any = schema
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node

    def walk(node: Any, chain: tuple[str, ...], active: frozenset[str]) -> None:
        if not isinstance(node, dict):
            return
        pattern = node.get("pattern")
        if isinstance(pattern, str) and pattern.startswith(_DATE_PATTERN_PREFIX):
            found.add(chain)
            return
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/") and ref not in active:
            walk(resolve(ref), chain, active | {ref})
        for key, sub in node.get("properties", {}).items():
            walk(sub, chain + (key,), active)
        if "items" in node:
            walk(node["items"], chain + ("*",), active)
        for key in ("allOf", "anyOf", "oneOf"):
            for sub in node.get(key, ()):
                walk(sub, chain, active)
        for key in ("then", "else", "additionalProperties"):
            walk(node.get(key), chain, active)

    walk(schema, (), frozenset())
    chains = sorted(found)
    if len(_DATE_CHAINS) > 8:
        _DATE_CHAINS.clear()
    _DATE_CHAINS[id(schema)] = (schema, chains)
    return chains


def _values_at(node: Any, chain: tuple[str, ...], path: str = "$") -> Iterator[tuple[str, Any]]:
    """``(path, value)`` for every value of ``node`` at a property chain (``"*"``: each element)."""
    if not chain:
        yield path, node
        return
    key, rest = chain[0], chain[1:]
    if key == "*":
        if isinstance(node, list):
            for i, child in enumerate(node):
                yield from _values_at(child, rest, f"{path}[{i}]")
    elif isinstance(node, dict) and key in node:
        yield from _values_at(node[key], rest, _child(path, key))


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


def page_size(source: dict, page: int) -> dict | None:
    """The size of one physical page: its ``page_sizes`` range when one lists it, else ``page_size``."""
    for rng in source.get("page_sizes", []):
        if rng["first"] <= page <= rng["last"]:
            return rng
    return source.get("page_size")


def printed_to_physical(page: int, basis: str, source: dict) -> int | None:
    """A page on the physical basis, or None when the offset is unknown."""
    if basis == "physical":
        return page
    offset = source.get("printed_page_offset")
    return None if offset is None else page + offset


#: A ``_Ctx.parent`` value: the parent is a PDF fragment that names its ``page=``.
PAGE_FRAGMENT = "page="


@dataclass
class _Ctx:
    """Where a selector sits (section 6.6).

    ``page`` is the page the nearest enclosing ``page=`` fragment chose, at any
    depth.  ``parent`` is the structural parent: None for a top-level
    alternative, :data:`PAGE_FRAGMENT` for a PDF fragment that names its page,
    else the parent's ``type``.  ``depth`` counts the selectors of the
    ``refinedBy`` chain, the top-level alternative being 1.
    """
    page: int | None = None
    parent: str | None = None
    depth: int = 1


def _a_kind_list(kinds: Iterable[str]) -> str:
    """``("structure", "class")`` -> ``"a structure or class"``, with the article of the first."""
    order = ("inductive", "structure", "class")
    names = [k for k in order if k in set(kinds)] + sorted(set(kinds) - set(order))
    article = "an" if names[0][0] in "aeiou" else "a"
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
    return f"{article} {joined}"


class Checker:
    """Collects the :class:`Problem` records of one document that the schema accepted."""

    def __init__(self, doc: dict, schema: dict) -> None:
        self.doc = doc
        self.problems: list[Problem] = []
        self.warnings: list[Problem] = []
        self.date_chains = _date_chains(schema)
        #: the profiles the document declares (any key of its ``profiles`` object)
        self.profiles: set[str] = set(doc.get("profiles", {}) or {})
        self.by_id: dict[str, dict[str, dict]] = {}
        for name in RECORD_ARRAYS:
            self.by_id[name] = {}
            for rec in doc.get(name, []) or []:
                self.by_id[name].setdefault(rec["id"], rec)
        #: each item's first location (document order, which I2 makes id order)
        self.first_location: dict[str, dict] = {}
        for _path, loc in self.records("locations"):
            for item_id in loc["items"]:
                self.first_location.setdefault(item_id, loc)
        changes = doc.get("changelog", []) or []
        #: declaration ids a changelog entry retires (what an orphaned link may name)
        self.retired = {c["subject"] for c in changes
                        if c["event"] == "retired" and c["subject_kind"] == "declaration"}
        #: every ordinal_assigned record, per subject
        self.ordinal_records: dict[str, list[dict]] = {}
        #: when each declaration id was retired or renamed (the latest such entry)
        self.gone_at: dict[str, tuple] = {}
        for c in changes:
            when = _parse_at(c["at"])
            if when is None:            # I9 reports it (check_dates); it orders nothing here
                continue
            if c["event"] == "ordinal_assigned":
                self.ordinal_records.setdefault(c["subject"], []).append(c)
            elif c["event"] in ("retired", "renamed") and c["subject_kind"] == "declaration":
                if c["subject"] not in self.gone_at or when > self.gone_at[c["subject"]]:
                    self.gone_at[c["subject"]] = when

    def fail(self, code: str, path: str, message: str) -> None:
        self.problems.append(Problem(code, path, message))

    def warn(self, code: str, path: str, message: str) -> None:
        self.warnings.append(Problem(code, path, message))

    def records(self, name: str) -> Iterator[tuple[str, dict]]:
        for i, rec in enumerate(self.doc.get(name, []) or []):
            yield f"$.{name}[{i}]", rec

    def ref(self, path: str, value: str | None, array: str) -> dict | None:
        """Resolve one reference into ``array``; report it (I3) when it does not."""
        if value is None:
            return None
        rec = self.by_id[array].get(value)
        if rec is None:
            if array not in self.doc:
                self.fail("I3", path, f"{value!r} names a record in {array}[], and the document has no {array}[]")
            else:
                self.fail("I3", path, f"{value!r} is not an id in {array}[]")
        return rec

    # -- I1, I2 ----------------------------------------------------------
    def check_ids_and_order(self) -> None:
        for name in RECORD_ARRAYS:
            ids_ = [rec["id"] for rec in self.doc.get(name, []) or []]
            seen: set[str] = set()
            for i, rid in enumerate(ids_):
                if rid in seen:
                    self.fail("I1", f"$.{name}[{i}].id", f"duplicate id {rid!r} in {name}[]")
                seen.add(rid)
            for i in range(1, len(ids_)):
                if order_key(ids_[i - 1]) > order_key(ids_[i]):
                    self.fail("I2", f"$.{name}[{i}].id",
                              f"{name}[] is not sorted by id (UTF-16 code units): {ids_[i]!r} comes "
                              f"after {ids_[i - 1]!r}")
                    break
            for path, rec in self.records(name):
                for dotted in SET_FIELDS.get(name, ()):
                    values = _get(rec, dotted)
                    if isinstance(values, list) and values != sorted(values, key=order_key):
                        self.fail("I2", f"{path}.{dotted}", "must be sorted (UTF-16 code units)")
                for field_name, key in KEYED_LISTS.get(name, ()):
                    keys = [_entry_key(d, key) for d in rec.get(field_name, [])]
                    if keys != sorted(keys) or len(set(keys)) != len(keys):
                        self.fail("I2", f"{path}.{field_name}",
                                  f"must be sorted by {_key_label(key)}, one entry each")
        keys = [_entry_key(p, "name") for p in self.doc["companion"].get("packages", [])]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            self.fail("I2", "$.companion.packages", "must be sorted by name, one entry each")

    # -- I14 -------------------------------------------------------------------
    def check_profiles(self) -> None:
        """A root array or field a profile owns appears only when the profile is declared."""
        for name, owned in PROFILES.items():
            if name in self.profiles:
                continue
            why = f"belongs to the {name} profile, which the document does not declare (profiles.{name})"
            for array in owned["arrays"]:
                if array in self.doc:
                    self.fail("I14", f"$.{array}", f"{array}[] {why}")
            for dotted in owned["fields"]:
                head, _, field_name = dotted.partition(".")
                if head == "companion":
                    if field_name in self.doc["companion"]:
                        self.fail("I14", f"$.companion.{field_name}", f"companion.{field_name} {why}")
                    continue
                for path, rec in self.records(head):
                    if field_name in rec:
                        self.fail("I14", f"{path}.{field_name}", f"{head}[].{field_name} {why}")

    # -- I3 ------------------------------------------------------------------
    def check_references(self) -> None:
        """References resolve, plus the other I3 rules: orphaned links, unique link
        and membership keys, one snippet each, parents, and a fixed verdict's
        resolution."""
        for path, item in self.records("items"):
            self.ref(f"{path}.source", item["source"], "sources")
            for i, c in enumerate(item.get("clusters", [])):
                self.ref(f"{path}.clusters[{i}]", c, "clusters")
            for rel, others in sorted(item.get("relations", {}).items()):
                for i, other in enumerate(others):
                    if other == item["id"]:
                        self.fail("I3", f"{path}.relations.{rel}[{i}]", "an item is not related to itself")
                    else:
                        self.ref(f"{path}.relations.{rel}[{i}]", other, "items")
        for path, decl in self.records("declarations"):
            for i, loc in enumerate(decl.get("anchors", [])):
                self.ref(f"{path}.anchors[{i}]", loc, "locations")
            if "replaced_by" in decl:
                self.ref(f"{path}.replaced_by", decl["replaced_by"], "declarations")
            if "parent" in decl:
                parent = self.ref(f"{path}.parent", decl["parent"], "declarations")
                allowed = vocab.ANNOTATION_MEMBER_PARENT_KINDS.get(decl["kind"])
                if parent is not None and allowed is not None and parent["kind"] not in allowed:
                    self.fail("I3", f"{path}.parent", f"a {decl['kind']}'s parent is {_a_kind_list(allowed)}, "
                                                      f"not {_a_kind_list((parent['kind'],))}")
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
                self.fail("I3", path, f"repeats the link at {prior} (same item, target, role and sub)")
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
                        self.fail("I3", f"{path}.orphaned",
                                  f"{target!r} is live; an orphaned link names a retired id")
                    elif target not in self.retired:
                        self.fail("I3", f"{path}.declaration",
                                  f"{target!r} is orphaned but no changelog entry retires it")
                else:
                    self.ref(f"{path}.declaration", target, "declarations")
            elif link.get("orphaned"):
                self.fail("I3", f"{path}.orphaned", "only a link to a declaration can be orphaned")
            relation = link.get("relation")
            if relation is not None and relation not in vocab.ANNOTATION_LINK_RELATIONS:
                self.warn("W-RELATION", f"{path}.relation",
                          f"{relation!r} is outside the documented core "
                          f"({', '.join(sorted(vocab.ANNOTATION_LINK_RELATIONS))})")
        member_keys: dict[tuple, str] = {}
        for path, member in self.records("memberships"):
            key = (member["cluster"], member.get("declaration"), member.get("artifact"), member["role"])
            prior = member_keys.setdefault(key, path)
            if prior != path:
                self.fail("I3", path, f"repeats the membership at {prior} (same cluster, target and role)")
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
                    self.fail("I3", f"{path}.locations[{i}]",
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
                self.fail("I3", where, "status is fixed, so the resolution must be fixed "
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
                self.fail("I3", f"{path}.declaration",
                          f"{snip['declaration']!r} already has a snippet at {prior}")

    # -- I6 (sources) ------------------------------------------------------------
    def check_sources(self) -> None:
        for path, source in self.records("sources"):
            pages = source.get("page_hashes")
            if pages is not None:
                for i, page in enumerate(pages):
                    if page["path"].endswith("/"):
                        self.fail("I6", f"{path}.page_hashes[{i}].path",
                                  f"{page['path']!r} names a directory; a page_hashes entry names a file "
                                  f"(write its index.html)")
                if "sha256" in source and site_sha256(pages) != source["sha256"]:
                    self.fail("I6", f"{path}.sha256", f"a site's sha256 is the sha256 of its page manifest; "
                                                      f"page_hashes give {site_sha256(pages)}")
            fields = [f for f in ("physical_page_count", "printed_page_offset", "page_size", "page_sizes")
                      if f in source]
            if fields and source_family(source) != "pdf":
                self.fail("I6", path, f"{', '.join(fields)} describe PDF pages; {source['id']!r} is not a PDF")
                continue
            count, last_end = source.get("physical_page_count"), 0
            for i, rng in enumerate(source.get("page_sizes", [])):
                if rng["last"] < rng["first"]:
                    self.fail("I6", f"{path}.page_sizes[{i}]",
                              f"last ({rng['last']}) is before first ({rng['first']})")
                if rng["first"] <= last_end:
                    self.fail("I6", f"{path}.page_sizes[{i}]", "page_sizes ranges overlap")
                if count is not None and rng["last"] > count:
                    self.fail("I6", f"{path}.page_sizes[{i}]", f"page {rng['last']} is past the source's "
                                                               f"{count} physical pages")
                last_end = max(last_end, rng["last"])

    # -- I4, I5, I6 (items and pages), I12 (item ids) ----------------------------
    def check_items(self) -> None:
        links_by_item: dict[str, list[dict]] = {}
        for _path, link in self.records("links"):
            if not link.get("orphaned"):
                links_by_item.setdefault(link["item"], []).append(link)
        identities: dict[tuple, str] = {}
        for path, item in self.records("items"):
            rid = item["id"]
            numbered = item["form"] == "numbered"
            # I4 is for passages: a numbered item is located by its number alone.
            if not numbered and not (rid in self.first_location or "quote" in item or "pages" in item):
                self.fail("I4", path, f"passage {rid!r} is anchored nowhere: it needs a location, a quote or pages")
            self.check_coverage(path, item, links_by_item.get(rid, []))
            for i, xref in enumerate(item.get("xrefs", [])):
                if xref["database"] not in vocab.ANNOTATION_XREF_DATABASES:
                    self.warn("W-XREF", f"{path}.xrefs[{i}].database",
                              f"{xref['database']!r} is outside the documented core "
                              f"({', '.join(sorted(vocab.ANNOTATION_XREF_DATABASES))})")
            if numbered:
                key = (item["source"], item["kind"], item["number"], item.get("sub"))
                prior = identities.setdefault(key, path)
                if prior != path:
                    self.fail("I6", path, f"the same numbered item as {prior} "
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

    def check_coverage(self, path: str, item: dict, links: list[dict]) -> None:
        """I5 (section 5.4): the coverage class and the item's non-orphaned links agree.

        1. A key link says the declaration states the whole item.
        2. An item with links and no key link carries ``coverage``.
        3. An item with an attributed link has a key link, unless it is
           ``partial_only`` (then the attributed links are the partial statements).
        4. ``formalized`` needs a key link; ``partial_only``, ``support_only``,
           ``example_only``, ``not_formalized`` and ``out_of_scope`` exclude one;
           ``private_only`` allows key links only to private declarations, and
           needs at least one link.
        5. ``not_formalized`` and ``out_of_scope`` have no key or attributed link.
        6. ``coverage_note`` needs ``coverage``.
        """
        rid = item["id"]
        roles = {link["role"] for link in links}
        coverage = item.get("coverage")
        if links and "key" not in roles and coverage is None:
            self.fail("I5", path, f"item {rid!r} has links but no key link, so it must carry coverage "
                                  f"(partial_only, support_only, example_only, private_only, not_formalized "
                                  f"or out_of_scope)")
        if "attributed" in roles and "key" not in roles and coverage != "partial_only":
            self.fail("I5", path, f"item {rid!r} has attributed links and no key link (only a partial_only "
                                  f"item may: its attributed links are the partial statements)")
        if coverage == "formalized" and "key" not in roles:
            self.fail("I5", f"{path}.coverage", f"item {rid!r} is formalized but has no key link")
        elif coverage in ("partial_only", "support_only", "example_only", "not_formalized", "out_of_scope") \
                and "key" in roles:
            self.fail("I5", f"{path}.coverage", f"item {rid!r} is {coverage} but has a key link")
        elif coverage == "private_only":
            if not links:
                self.fail("I5", f"{path}.coverage", f"item {rid!r} is private_only but has no link")
            for link in links:
                if link["role"] != "key":
                    continue
                if "cluster" in link:
                    self.fail("I5", f"{path}.coverage", f"item {rid!r} is private_only, so a key link names a "
                                                        f"private declaration; {link['id']!r} names a cluster")
                    continue
                decl = self.by_id["declarations"].get(link.get("declaration"))   # dangling: I3 reports it
                if decl is not None and not decl["private"]:
                    self.fail("I5", f"{path}.coverage", f"item {rid!r} is private_only, so a key link names a "
                                                        f"private declaration; {decl['id']!r} is not private")
        if coverage in ("not_formalized", "out_of_scope") and "attributed" in roles:
            self.fail("I5", f"{path}.coverage", f"item {rid!r} is {coverage} but has an attributed link")
        if "coverage_note" in item and coverage is None:
            self.fail("I5", f"{path}.coverage_note", "coverage_note needs coverage")

    def check_item_id(self, path: str, item: dict, source: dict | None) -> None:
        """I12: an item id follows its id_source (section 7.2)."""
        rid, how = item["id"], item["id_source"]
        if how == "label":
            try:
                want = ids.item_label_id(item["kind"], item["number"], item.get("sub"))
            except ValueError:
                want = None
            if want is None:
                self.fail("I12", f"{path}.id_source", f"kind {item['kind']!r} gives no label slug; declare the id")
            elif rid != want:
                self.fail("I12", f"{path}.id", f"a label id is <kind>-<number>[-<sub>], here {want!r}")
        elif how == "quote_hash":
            quote, where = self.passage_quote(item)
            if quote is None:
                self.fail("I12", f"{path}.id_source", "a quote_hash id needs item.quote, or a TextQuoteSelector "
                                                      "on the item's first location")
            elif not ids.normalize_text(quote):
                self.fail("I12", f"{path}.id_source", f"a quote_hash id hashes a quote with text in it; {where} "
                                                      f"is only whitespace")
            else:
                want = ids.passage_quote_id(quote)
                if rid != want:
                    self.fail("I12", f"{path}.id", f"a quote_hash id is passage-<8 hex of the normalised quote> "
                                                   f"({where}), here {want!r}")
        elif how == "page_title_hash":
            quote, where = self.passage_quote(item)
            if quote is not None:
                self.fail("I12", f"{path}.id_source", f"a passage with a quote ({where}) takes the quote_hash id")
            printed = None
            if source is not None:
                pages = item["pages"]
                if pages["basis"] == "printed":
                    printed = pages["first"]
                elif source.get("printed_page_offset") is not None:
                    printed = pages["first"] - source["printed_page_offset"]
            if printed is None:
                self.fail("I12", f"{path}.id_source", "page_title_hash needs a printed page: pages on the printed "
                                                      "basis, or a source with printed_page_offset")
            elif printed < 1:
                self.fail("I12", f"{path}.pages", f"page_title_hash needs a printed page of at least 1; physical "
                                                  f"page {pages['first']} less the source's printed_page_offset "
                                                  f"is {printed}")
            elif not ids.normalize_text(item["title"]):
                self.fail("I12", f"{path}.title", "a page_title_hash id hashes a title with text in it; this one "
                                                  "is only whitespace")
            else:
                want = ids.passage_page_id(printed, item["title"])
                if rid != want:
                    self.fail("I12", f"{path}.id", f"a page_title_hash id is passage-p<printed page>-<8 hex of "
                                                   f"the normalised title>, here {want!r}")
        elif _RESERVED_PASSAGE.fullmatch(rid):
            self.fail("I12", f"{path}.id", f"{rid!r} has the shape of a mechanical passage id; "
                                           f"a declared id may not take it")

    def passage_quote(self, item: dict) -> tuple[str | None, str]:
        """The quote a quote_hash id hashes, and where it came from (section 7.2).

        ``item.quote.exact`` when the item has one; otherwise the first
        TextQuoteSelector of the item's first location (locations in id order;
        alternatives in order, each followed by its ``refinedBy`` chain).
        """
        if "quote" in item:
            return item["quote"]["exact"], "item.quote"
        loc = self.first_location.get(item["id"])
        if loc is None:
            return None, ""
        for sel in loc["target"]["selector"]:
            node = sel
            while isinstance(node, dict):
                if node.get("type") == "TextQuoteSelector":
                    return node["exact"], f"the quote of location {loc['id']!r}"
                node = node.get("refinedBy")
        return None, ""

    def check_pages(self, path: str, pages: dict, source: dict | None) -> None:
        """I6: a page range is ordered, on a PDF, and its physical pages lie in 1..physical_page_count."""
        first, last = pages["first"], pages.get("last", pages["first"])
        if last < first:
            self.fail("I6", path, f"last ({last}) is before first ({first})")
        if source is None:
            return
        if source_family(source) != "pdf":
            self.fail("I6", path, f"pages are for a pdf source; {source['id']!r} is {source['kind']}")
            return
        count = source.get("physical_page_count")
        lo = printed_to_physical(first, pages["basis"], source)
        hi = printed_to_physical(last, pages["basis"], source)
        if lo is not None and lo < 1:
            self.fail("I6", path, f"page {first} ({pages['basis']}) is physical page {lo}; with the source's "
                                  f"printed_page_offset every physical page is at least 1")
        if count is not None and hi is not None and hi > count:
            self.fail("I6", path, f"page {last} ({pages['basis']}) is past the source's "
                                  f"{count} physical pages")

    # -- I7 --------------------------------------------------------------------
    def check_locations(self) -> None:
        for path, link in self.records("links"):
            item = self.by_id["items"].get(link["item"])
            for i, loc_id in enumerate(link.get("locations", [])):
                loc = self.by_id["locations"].get(loc_id)
                if loc is not None and item is not None and loc["target"]["source"] != item["source"]:
                    self.fail("I7", f"{path}.locations[{i}]", f"location {loc_id!r} is on "
                                                              f"{loc['target']['source']!r}, not on the item's "
                                                              f"source {item['source']!r}")
        anchored = {a for _p, d in self.records("declarations") for a in d.get("anchors", [])}
        anchored |= {a for _p, link in self.records("links") for a in link.get("locations", [])}
        for path, decl in self.records("declarations"):
            for i, loc_id in enumerate(decl.get("anchors", [])):
                loc = self.by_id["locations"].get(loc_id)
                src = self.by_id["sources"].get(loc["target"]["source"]) if loc else None
                if src is not None and (src["kind"] == "tex" or source_family(src) in ("pdf", "generic")):
                    what = src["kind"] if src["kind"] != "other" else f"other ({src.get('media_type')})"
                    self.fail("I7", f"{path}.anchors[{i}]", f"an anchor shows the declaration's own text, on a "
                                                            f"lean, web, or other text/html source; {src['id']!r} "
                                                            f"is {what}")
        for path, loc in self.records("locations"):
            if not loc["items"] and loc["id"] not in anchored:
                self.fail("I7", f"{path}.items", "a location shows at least one item, anchors a declaration, "
                                                 "or is named by a link")
            target = loc["target"]
            source = self.by_id["sources"].get(target["source"])
            if source is None:
                continue
            for i, item_id in enumerate(loc["items"]):
                item = self.by_id["items"].get(item_id)
                if item is not None and item["source"] != source["id"]:
                    self.fail("I7", f"{path}.items[{i}]", f"item {item_id!r} is on {item['source']!r}; a location "
                                                          f"shows items of its own source {source['id']!r}")
            site = source.get("scope") == "site"
            named: set[int] = set()
            for i, sel in enumerate(target["selector"]):
                spath = f"{path}.target.selector[{i}]"
                if site and sel["type"] != "SitePageSelector":
                    self.fail("I7", spath, f"{source['id']!r} is a site: each alternative names its page "
                                           f"with a SitePageSelector")
                    continue
                named |= self.check_selector(spath, sel, source, _Ctx())
            if "pages" in loc:
                self.check_pages(f"{path}.pages", loc["pages"], source)
                pages = loc["pages"]
                lo = printed_to_physical(pages["first"], pages["basis"], source)
                hi = printed_to_physical(pages.get("last", pages["first"]), pages["basis"], source)
                if lo is not None and hi is not None and lo >= 1:
                    outside = sorted(p for p in named if not lo <= p <= hi)
                    if outside:
                        self.fail("I7", f"{path}.pages", f"the selectors name physical page(s) {outside}, "
                                                         f"outside {lo}..{hi}")

    def check_selector(self, path: str, sel: dict, source: dict, ctx: _Ctx) -> set[int]:
        """Check one selector and what refines it; return the physical pages it names."""
        family, stype = source_family(source), sel["type"]
        pages: set[int] = set()
        if ctx.depth > MAX_REFINEMENT_DEPTH:
            self.fail("I7", path, f"a refinedBy chain is at most {MAX_REFINEMENT_DEPTH} selectors deep")
            return pages
        if stype not in ALLOWED_SELECTORS[family]:
            self.fail("I7", path, f"{stype} does not apply to a {source['kind']} source")
            return pages
        child_ctx = _Ctx(page=ctx.page, parent=stype, depth=ctx.depth + 1)
        if stype == "SitePageSelector":
            if source.get("scope") != "site":
                self.fail("I7", path, f"a SitePageSelector names a page of a site; {source['id']!r} is one page")
            elif ctx.parent is not None:
                self.fail("I7", path, "a SitePageSelector is a top-level alternative, never a refinement")
            elif "page_hashes" in source:
                page = site_page_file(sel["path"])
                if page not in {p["path"] for p in source["page_hashes"]}:
                    self.fail("I7", f"{path}.path", f"{page!r} is not a page in {source['id']!r}'s page_hashes")
        elif stype == "FragmentSelector":
            page = self.check_fragment(path, sel, source, ctx)
            if page is not None:
                pages.add(page)
                child_ctx.page = page
                child_ctx.parent = PAGE_FRAGMENT
        elif stype == "PdfRegionSelector":
            pages.add(sel["page"])
            self.check_region(path, sel, source, ctx)
        child = sel.get("refinedBy")
        if child is not None:
            pages |= self.check_selector(f"{path}.refinedBy", child, source, child_ctx)
        return pages

    def check_fragment(self, path: str, sel: dict, source: dict, ctx: _Ctx) -> int | None:
        """Check a FragmentSelector's value against its conformsTo; return the PDF page it names."""
        family, value = source_family(source), sel["value"]
        conforms = sel.get("conformsTo")
        if value.startswith("#"):
            self.fail("I7", f"{path}.value", "write the fragment without its leading #")
            return None
        if conforms is None:
            self.warn("W-CONFORMS", f"{path}.conformsTo", "absent (W3C: SHOULD have one); the value is not checked")
            return None
        known = CONFORMS.get(conforms)
        if known is None:
            self.warn("W-CONFORMS", f"{path}.conformsTo", f"{conforms!r} is not an IRI this checker knows; "
                                                          f"the value is not checked")
            return None
        grammar, families = known
        if family not in families:
            fits = sorted(iri for iri, (_g, fams) in CONFORMS.items() if family in fams)
            self.fail("I7", f"{path}.conformsTo", f"a {source['kind']} source takes {' or '.join(fits)}")
            return None
        if grammar == "pdf":
            params = self.parse_pdf_fragment(f"{path}.value", value, source)
            if params is None:
                return None
            if sel.get("refinedBy") is not None and "page" not in params:
                self.fail("I7", f"{path}.value", "a PDF fragment that is refined must name its page=")
            if "page" not in params:
                return None
            page = int(params["page"])
            if ctx.page is not None and page != ctx.page:
                self.fail("I7", f"{path}.value", f"page={page} inside page={ctx.page}")
            return page
        if grammar == "text":
            m = _RFC5147.fullmatch(value)
            if not m:
                self.fail("I7", f"{path}.value", f"{_short(value)!r} is not an RFC 5147 fragment "
                                                 f"(char=|line= position or range, then ;length= or ;md5=; "
                                                 f"numbers are ASCII digits, at most 9)")
                return None
            a, b = m.group("a"), m.group("b")
            if m.group("pos") is None and a is None and b is None:
                self.fail("I7", f"{path}.value", "an RFC 5147 range needs at least one end")
            elif a is not None and b is not None and int(a) >= int(b):
                self.fail("I7", f"{path}.value", f"RFC 5147 range {a},{b} is empty or out of order; "
                                                 f"lines L1..L2 are written line=(L1-1),L2")
        elif grammar == "html_id":
            if not _HTML_ID.fullmatch(value):
                self.fail("I7", f"{path}.value", f"{_short(value)!r} is not an HTML element id (RFC 3236: the fragment "
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
                self.warn("W-MEDIA", path, f"{_short(part)!r} is not a Media Fragments dimension (xywh=, t=, track=, "
                                           f"id=); a user agent ignores it")
                continue
            if name in seen:
                self.warn("W-MEDIA", path, f"{name}= appears more than once; the last one is used")
            seen.add(name)
            if name == "xywh":
                m = _XYWH.fullmatch(arg)
                if not m:
                    self.fail("I7", path, f"{_short(arg)!r} is not a Media Fragments xywh= value "
                                          f"([pixel:|percent:]x,y,w,h in ASCII digits, at most 9 each)")
                elif int(m.group("w")) == 0 or int(m.group("h")) == 0:
                    self.fail("I7", path, "xywh width and height must be positive")
                elif m.group("unit") == "percent" and (int(m.group("x")) + int(m.group("w")) > 100
                                                       or int(m.group("y")) + int(m.group("h")) > 100):
                    self.fail("I7", path, "a percent: region must stay within 100")
            elif name == "t":
                m = _MEDIA_T.fullmatch(arg)
                if not m or (m.group("a") is None and m.group("b") is None):
                    self.fail("I7", path, f"{_short(arg)!r} is not a Media Fragments t= value ([npt:]start[,end])")
                elif not all(_clock_ok(c) for c in (m.group("a"), m.group("b")) if c is not None):
                    self.fail("I7", path, f"t={arg}: minutes and seconds run from 00 to 59")
                elif m.group("a") is not None and m.group("b") is not None and \
                        _seconds(m.group("a")) >= _seconds(m.group("b")):
                    self.fail("I7", path, f"t={arg}: the start must be before the end")
            elif _WS_RE.search(arg):
                self.fail("I7", path, f"{name}= takes a percent-encoded name, got {_short(arg)!r}")

    def parse_pdf_fragment(self, path: str, value: str, source: dict) -> dict | None:
        params: dict[str, str] = {}
        for part in re.split(r"[&#]", value):
            name, eq, arg = part.partition("=")
            if not eq or name not in _PDF_PARAMS or not arg:
                self.fail("I7", path, f"{_short(part)!r} is not an RFC 8118 parameter (name=value, one of "
                                      f"{', '.join(sorted(_PDF_PARAMS))})")
                return None
            if name in params:
                self.fail("I7", path, f"{name}= is given twice; a PDF fragment names each parameter once")
                return None
            counts = _PDF_PARAMS[name]
            if counts is not None:
                args = arg.split(",")
                if name == "page":
                    if not _PAGE.fullmatch(arg):
                        self.fail("I7", path, f"page={_short(arg)} is not a positive integer (ASCII digits, at most 9)")
                        return None
                elif len(args) not in counts or not all(re.fullmatch(_NUM, a) for a in args):
                    want = " or ".join(str(c) for c in counts)
                    self.fail("I7", path, f"{name}= takes {want} numbers, got {_short(arg)!r}")
                    return None
                if name == "highlight":
                    left, right, top, bottom = (float(a) for a in args)
                    if left > right or top > bottom:
                        self.fail("I7", path, "highlight= is left,right,top,bottom with left <= right and "
                                              "top <= bottom")
            params[name] = arg
        if "page" in params:
            count = source.get("physical_page_count")
            if count is not None and int(params["page"]) > count:
                self.fail("I7", path, f"page={params['page']} is past the source's {count} physical pages")
        return params

    def check_region(self, path: str, sel: dict, source: dict, ctx: _Ctx) -> None:
        if "sha256" not in source and "git_blob" not in source:
            # Geometry is measured on the bytes.  A source pinned by identifiers
            # (or a revision) alone still has pages, so page= fragments and
            # pages are fine on it; a region is not.
            self.fail("I7", path, f"a PdfRegionSelector needs the source's bytes; {source['id']!r} names none "
                                  f"(no sha256 or git_blob): locate by page= or pages instead")
        count = source.get("physical_page_count")
        if count is not None and sel["page"] > count:
            self.fail("I7", f"{path}.page", f"page {sel['page']} is past the source's {count} physical pages")
        if ctx.parent not in (None, PAGE_FRAGMENT):
            self.fail("I7", path, "a PdfRegionSelector is a top-level alternative or the direct refinement of a "
                                  "PDF page= fragment")
        elif ctx.page is not None and ctx.page != sel["page"]:
            self.fail("I7", f"{path}.page", f"page {sel['page']} disagrees with the refined page={ctx.page}")
        space = sel["coordinate_space"]
        if vocab.ANNOTATION_ORIGIN_Y_AXIS[space["origin"]] != space["y_axis"]:
            self.fail("I7", f"{path}.coordinate_space", "top_left goes with y_axis down, bottom_left with up")
        boxes: list[tuple[str, str, dict]] = [(path, "rect", sel["rect"])] if "rect" in sel else []
        for i, quad in enumerate(sel.get("quads", [])):
            qpath = f"{path}.quads[{i}]"
            points = [(quad[f"x{k}"], quad[f"y{k}"]) for k in range(4)]
            if len(set(points)) < 3:
                self.fail("I7", qpath, "a quad needs at least three distinct corners")
            elif _collinear(points):
                self.fail("I7", qpath, "a quad's corners must not all lie on one line (it would enclose no area)")
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            boxes.append((qpath, "quad", {"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}))
        size = page_size(source, sel["page"])
        for bpath, what, box in boxes:
            values = [box["x0"], box["y0"], box["x1"], box["y1"]]
            if not all(math.isfinite(v) for v in values):
                self.fail("I7", bpath, "coordinates must be finite")
                continue
            if what == "rect" and (box["x0"] >= box["x1"] or box["y0"] >= box["y1"]):
                self.fail("I7", bpath, "a rect needs x0 < x1 and y0 < y1")
            normalized = space["units"] == "normalized"
            if min(values) < 0 or (normalized and max(values) > 1):
                self.fail("I7", bpath, "coordinates must lie in [0, 1] for units normalized" if normalized
                          else f"coordinates must be >= 0 for units {space['units']}")
            elif space["units"] == "pt" and size is not None and (
                    box["x1"] > size["width_pt"] or box["y1"] > size["height_pt"]):
                self.fail("I7", bpath, f"the {what} leaves the {jcs_number(size['width_pt'])} x "
                                       f"{jcs_number(size['height_pt'])} pt page")

    # -- I8 --------------------------------------------------------------------
    def check_declarations(self) -> None:
        for path, d in self.records("declarations"):
            rid, source, kind = d["id"], d["id_source"], d["kind"]
            if "signature" in d:
                sig = d["signature"]
                if ids.normalize_statement(sig) != sig:
                    self.fail("I8", f"{path}.signature", "must be normalised: comments removed, each run of "
                                                         "whitespace one space, ends stripped (the display form)")
                elif "statement_sha256" in d and ids.statement_sha256(sig) != d["statement_sha256"]:
                    self.fail("I8", f"{path}.signature", "its sha256 is not statement_sha256 (the hash of the "
                                                         "signature's hash form)")
            if kind == "example" and not d["anonymous"]:
                self.fail("I8", f"{path}.anonymous", "an example is always anonymous (a named example is refused)")
            if d.get("parent") == rid:
                self.fail("I8", f"{path}.parent", "a declaration is not its own parent")
            parent = self.by_id["declarations"].get(d.get("parent"))
            parent_name = _full_name(parent) if parent is not None else None
            if "namespace" in d and parent_name is not None and d["namespace"] != parent_name:
                self.fail("I8", f"{path}.namespace", f"a {kind}'s namespace is its parent's full name (its "
                                                     f"name), {parent_name!r}")
            full = _full_name(d)
            if kind in vocab.ANNOTATION_MEMBER_PARENT_KINDS and full is not None:
                where = f"{path}.name" if "name" in d else f"{path}.id"
                # "its own name" is one component: nothing after the namespace's dot holds a dot.  A
                # namespace that disagrees with the parent is reported above; the name is then held
                # against the parent's full name, not against it.
                if "namespace" in d and (parent_name is None or d["namespace"] == parent_name):
                    prefix = d["namespace"] + "." if d["namespace"] else ""
                    if not full.startswith(prefix) or "." in full[len(prefix):]:
                        self.fail("I8", where, f"a {kind}'s full name is its namespace, a dot and its own name")
                elif parent_name is not None and (not full.startswith(parent_name + ".")
                                                  or "." in full[len(parent_name) + 1:]):
                    self.fail("I8", where, f"a {kind}'s full name is its parent's full name ({parent_name!r}), "
                                           f"a dot and its own name")
            if source == "name":
                if d["private"] or d["anonymous"]:
                    self.fail("I8", f"{path}.id_source",
                              "a private or anonymous declaration is not identified by name")
                if not _NAMED_ID.fullmatch(rid):
                    self.fail("I8", f"{path}.id", f"{rid!r} is not a fully qualified Lean name")
                if "name" in d and d["name"] != rid:
                    self.fail("I8", f"{path}.name", f"a name-identified declaration's id is its name ({rid!r})")
            elif source == "private":
                if not d["private"] or d["anonymous"]:
                    self.fail("I8", f"{path}.id_source", "id_source private needs private true and anonymous false")
                want = f"private:{d['module']}:{d.get('name', '')}"
                if rid != want:
                    self.fail("I8", f"{path}.id",
                              f"a private declaration's id is private:<module>:<name>, here {want!r}")
            else:
                if not d["anonymous"]:
                    self.fail("I8", f"{path}.id_source", f"id_source {source} is for anonymous declarations")
                pattern = _HASH_ID if source == "statement_hash" else _DECLARED_ID
                m = pattern.fullmatch(rid)
                if not m:
                    form = "<keyword>:<12 hex>[~k]" if source == "statement_hash" else \
                        "<keyword>:<slug> (a slug of 12 hex digits is reserved for statement hashes)"
                    self.fail("I8", f"{path}.id", f"{rid!r} is not of the form {form}")
                else:
                    if m.group("kw") != kind:
                        self.fail("I8", f"{path}.id", f"the id says {m.group('kw')} but kind is {kind}")
                    if source == "statement_hash":
                        if "statement_sha256" in d and m.group("hex") != d["statement_sha256"][:12]:
                            self.fail("I8", f"{path}.id",
                                      "the id's hash is not the first 12 digits of statement_sha256")
                        if m.group("k") is not None:
                            self.check_ordinal(path, d)
                if "name" in d:
                    self.fail("I8", f"{path}.name", "an anonymous declaration has no name")

    def check_ordinal(self, path: str, d: dict) -> None:
        """A live ``~k`` id: its latest ``ordinal_assigned`` record names the declaration's file."""
        rid = d["id"]
        records = self.ordinal_records.get(rid, [])
        if not records:
            self.fail("I8", f"{path}.id", f"{rid!r} carries an ordinal, which is positional: record the "
                                          f"assignment in the changelog (event ordinal_assigned, with the file; "
                                          f"the history profile) or declare an id")
            return
        latest = max(_parse_at(c["at"]) for c in records)
        files = sorted({c["file"] for c in records if _parse_at(c["at"]) == latest})
        if len(files) > 1:
            self.fail("I8", f"{path}.id", f"the latest ordinal_assigned records for {rid!r} name different files "
                                          f"{files}")
        elif files[0] != d["file"]:
            self.fail("I8", f"{path}.id", f"the latest ordinal_assigned record gives {rid!r} to a declaration in "
                                          f"{files[0]!r}, and it is in {d['file']!r}: an added or deleted copy "
                                          f"moved the ordinal; record the new assignment, or declare an id")

    # -- I9 --------------------------------------------------------------------
    def check_dates(self) -> None:
        """Every date or timestamp in the document (found through the schema) is real."""
        for chain in self.date_chains:
            for path, value in _values_at(self.doc, chain):
                if isinstance(value, str) and _parse_at(value) is None:
                    self.fail("I9", path, f"{_short(value)!r} is not a calendar date or UTC instant")

    def check_changelog(self) -> None:
        live = {"declaration": self.by_id["declarations"], "item": self.by_id["items"],
                "location": self.by_id["locations"], "artifact": self.by_id["artifacts"]}
        for path, change in self.records("changelog"):
            pool = live[change["subject_kind"]]
            subject, event = change["subject"], change["event"]
            if event in ("retired", "renamed") and subject in pool:
                self.fail("I9", f"{path}.subject", f"{subject!r} is {event}, so it may not be used again")
            for name in ("successor", "possible_successor"):
                if name not in change:
                    continue
                if change[name] == subject:
                    self.fail("I9", f"{path}.{name}", f"the {name} differs from the subject")
                elif change[name] not in pool:
                    self.fail("I9", f"{path}.{name}", f"{change[name]!r} is not a live {change['subject_kind']} id")
            if event == "restated":
                self.check_restated(path, change, pool)
            if event == "ordinal_assigned":
                decl = pool.get(subject)
                m = _HASH_ID.fullmatch(subject)
                if change["subject_kind"] != "declaration" or m is None or m.group("k") is None:
                    self.fail("I9", f"{path}.subject",
                              "ordinal_assigned records a declaration's <keyword>:<12 hex>~k id")
                elif decl is None:
                    gone = self.gone_at.get(subject)
                    when = _parse_at(change["at"])
                    if when is not None and (gone is None or gone < when):
                        self.fail("I9", f"{path}.subject", f"{subject!r} is not a live declaration id, and no "
                                                           f"later changelog entry retires or renames it")

    def check_restated(self, path: str, change: dict, pool: dict[str, dict]) -> None:
        """``restated`` names a declared, named or private declaration id: live, or
        retired or renamed by a later entry (then the record is history)."""
        subject = change["subject"]
        decl = pool.get(subject)
        if change["subject_kind"] != "declaration":
            self.fail("I9", f"{path}.subject_kind", "restated records a declaration whose statement changed")
        elif _HASH_ID.fullmatch(subject) or (decl is not None and decl["id_source"] == "statement_hash"):
            self.fail("I9", f"{path}.subject", f"{subject!r} is a statement-hash id, which changes with its "
                                               f"statement: record retired (and the new id), not restated")
        elif decl is None:
            gone, when = self.gone_at.get(subject), _parse_at(change["at"])
            # "by a later entry" (8.4): the same instant is not later.  The ordinal_assigned
            # rule above says "no earlier" instead, and so accepts the same instant.
            if when is not None and (gone is None or gone <= when):
                self.fail("I9", f"{path}.subject", f"{subject!r} is not a live declaration id, and no later "
                                                   f"changelog entry retires or renames it")

    # -- I13 -------------------------------------------------------------------
    def check_attribution(self) -> None:
        linked = {link["declaration"]: path for path, link in self.records("links")
                  if "declaration" in link and not link.get("orphaned")}
        for path, row in self.records("unattributed"):
            if row["declaration"] in linked:
                self.fail("I13", f"{path}.declaration", f"{row['declaration']!r} is linked at "
                                                        f"{linked[row['declaration']]}, so it is not unattributed")

    def run(self) -> list[Problem]:
        self.check_ids_and_order()
        self.check_profiles()
        self.check_references()
        self.check_sources()
        self.check_items()
        self.check_locations()
        self.check_declarations()
        self.check_dates()
        self.check_changelog()
        self.check_attribution()
        return self.problems


def _seconds(clock: str) -> float:
    total = 0.0
    for part in clock.split(":"):
        total = total * 60 + float(part)
    return total


def _clock_ok(clock: str) -> bool:
    """A Media Fragments clock's minutes and seconds (when it has them) are 00..59."""
    parts = clock.split(":")
    return len(parts) == 1 or (int(parts[-2]) <= 59 and float(parts[-1]) < 60)


# -- L1: the external-urls lint ---------------------------------------------------

_L1_RULE = ("the external-urls lint allows a URL only in sources[].url, sources[].alternate_urls[], "
            "destinations[].href, the root $schema, a selector's conformsTo and quoted source text")


def lint_external_urls(doc: Any) -> list[Problem]:
    """``L1``: no URL-like string value or key anywhere but at the exempt paths (section 9.5).

    URL-like: ``<scheme>://`` for any RFC 3986 scheme anywhere in the string;
    a protocol-relative ``//host`` or a ``javascript:``/``data:``/``mailto:``/
    ``file:`` prefix at the start of the string.
    Exempt, by path and never by key name alone: ``sources[*].url``,
    ``sources[*].alternate_urls[*]``, ``destinations[*].href``, the root
    ``$schema``, a selector's ``conformsTo`` and the ``exact``/``prefix``/
    ``suffix`` of a ``TextQuoteSelector`` inside a location's ``target.selector``
    tree, and ``items[*].quote.*``.
    """
    found: list[Problem] = []

    def report(path: str, text: str, what: str) -> None:
        m = _URL_LIKE.search(text)
        if m:
            hit = m.group(0)
            hit = hit if len(hit) <= 60 else hit[:57] + "..."
            found.append(Problem("L1", path, f"{what}{hit!r}, a URL-like reference; {_L1_RULE}"))

    def walk(value: Any, path: str, chain: tuple, selector: bool, exempt: bool) -> None:
        if isinstance(value, str):
            if not exempt:
                report(path, value, "contains ")
        elif isinstance(value, list):
            urls = len(chain) == 3 and chain[0] == "sources" and chain[2] == "alternate_urls"
            selectors = len(chain) == 4 and chain[0] == "locations" and chain[2:] == ("target", "selector")
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]", chain + (i,), selectors, urls)
        elif isinstance(value, dict):
            quote = selector and value.get("type") == "TextQuoteSelector"
            for key, child in value.items():
                kpath = _child(path, key)
                report(kpath, key, f"the key {key!r} contains ")
                ex = ((not chain and key == "$schema")
                      or (len(chain) == 2 and chain[0] == "sources" and key == "url")
                      or (len(chain) == 2 and chain[0] == "destinations" and key == "href")
                      or (len(chain) == 3 and chain[0] == "items" and chain[2] == "quote" and key in _QUOTE_KEYS)
                      or (selector and key == "conformsTo")
                      or (quote and key in _QUOTE_KEYS))
                walk(child, kpath, chain + (key,), selector and key in _SELECTOR_CHILDREN, ex)

    walk(doc, "$", (), False, False)
    return found


_LINTS = {"external-urls": lint_external_urls}


# ---------------------------------------------------------------------------
# check() and the CLI
# ---------------------------------------------------------------------------

@dataclass
class _Selection:
    """What :func:`select_schema` decided: the schema file (None: an unknown major),
    the problems and warnings of the decision, and whether forward mode applies."""
    path: Path | None
    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    forward: bool = False


def select_schema(doc: Any) -> _Selection:
    """The schema of the minor ``doc`` declares (section 12.3), and how to apply it.

    A document whose ``schema_version`` cannot be read is checked against the
    newest schema, so the schema reports the malformed value.  An unknown major,
    core or profile, is a problem (``SCHEMA``).  A core or profile minor newer
    than this checker knows selects the newest schema of the major in forward
    mode (``W-NEWER``): unknown keys are ignored.  A root ``$schema`` that is
    not the declared minor's ``$id`` is a warning (``W-SCHEMA-ID``).
    """
    out = _Selection(SCHEMA_PATH)
    version = doc.get("schema_version") if isinstance(doc, dict) else None
    m = _VERSION.fullmatch(version) if isinstance(version, str) else None
    if m is None:
        return out
    major, minor = int(m.group("major")), int(m.group("minor"))
    if major != _KNOWN_MAJOR:
        out.path = None
        out.problems.append(Problem("SCHEMA", "$.schema_version",
                                    f"no schema for major version {major} (this checker has {_KNOWN_MAJOR})"))
        return out
    if minor > _KNOWN_MINOR:
        out.forward = True
        out.warnings.append(Problem("W-NEWER", "$.schema_version",
                                    f"{version} is newer than this checker ({vocab.ANNOTATION_SCHEMA_VERSION}); "
                                    f"checked against {vocab.ANNOTATION_SCHEMA_VERSION}, keys added since are "
                                    f"ignored"))
    else:
        out.path = schema_path(major, minor)
    profiles = doc.get("profiles")
    if isinstance(profiles, dict):
        for name in sorted(profiles):
            known = _KNOWN_PROFILE_MINOR.get(name)
            pm = _VERSION.fullmatch(profiles[name]) if isinstance(profiles[name], str) else None
            if known is None or pm is None:
                continue                            # the schema reports an unknown profile or a bad version
            if int(pm.group("major")) != _KNOWN_MAJOR:
                out.problems.append(Problem("SCHEMA", f"$.profiles.{name}",
                                            f"no schema for {name} profile major version {pm.group('major')} "
                                            f"(this checker has {vocab.ANNOTATION_PROFILE_VERSIONS[name]})"))
            elif int(pm.group("minor")) > known:
                out.forward = True
                out.warnings.append(Problem("W-NEWER", f"$.profiles.{name}",
                                            f"{profiles[name]} is newer than this checker's {name} profile "
                                            f"({vocab.ANNOTATION_PROFILE_VERSIONS[name]}); keys added since "
                                            f"are ignored"))
    if out.problems:
        out.path = None
        return out
    declared = doc.get("$schema")
    if isinstance(declared, str) and declared != schema_id(major, minor):
        out.warnings.append(Problem("W-SCHEMA-ID", "$.$schema",
                                    f"is not the $id of the declared schema {version}, "
                                    f"{schema_id(major, minor)}"))
    return out


def check(doc: Any, *, text: str | bytes | None = None, canonical: bool = False,
          lints: Iterable[str] = (), schema: dict | None = None) -> Report:
    """Problems and warnings for ``doc``.

    The I-JSON limits are checked first (code ``IJSON``); nothing else runs on
    a value that breaks them.  ``text`` is the file's content (``str`` or
    ``bytes``), needed only for ``canonical`` (I11: the bytes are the canonical
    form).  ``lints`` names opt-in lints from :data:`LINTS`; they run whatever
    the schema and invariants found.  ``schema`` overrides the choice by the
    declared minor (and forward mode).  Semantic checks run only on a document
    the schema accepts: they rely on its shape.
    """
    lints = tuple(lints)
    unknown = [name for name in lints if name not in LINTS]
    if unknown:
        raise ValueError(f"unknown lint(s) {unknown}; known: {sorted(LINTS)}")
    report = Report()
    problems, integral = _ijson_scan(doc)
    if problems:
        report.problems += problems
        return report
    if integral:
        doc = _integral_to_int(doc)
    forward = False
    if schema is None:
        selection = select_schema(doc)
        report.warnings += selection.warnings
        if selection.path is None:
            report.problems += selection.problems
            return report
        schema, forward = load_schema(selection.path), selection.forward
    checker = Validator(schema)
    for path, message in checker.errors(doc):
        if forward and message == _UNKNOWN_KEY:
            report.warnings.append(Problem("W-NEWER", path, "is not a key this checker knows; ignored, as the "
                                                            "document declares a newer minor"))
        else:
            report.problems.append(Problem("SCHEMA", path, message))
    if not report.problems:
        semantic = Checker(doc, schema)
        report.problems += semantic.run()
        report.warnings += semantic.warnings
    if canonical and text is not None and not report.problems:           # I11
        want = canonical_dumps(canonicalize(doc))
        same = text == want.encode("utf-8") if isinstance(text, (bytes, bytearray)) else text == want
        if not same:
            report.problems.append(Problem("I11", "$", "the file is not in canonical form (RFC 8785 key order and "
                                                       "numbers, 2-space indent, LF line ends, a final newline, "
                                                       "arrays sorted by id); --print-canonical writes it"))
    for name in lints:
        report.problems += _LINTS[name](doc)
    return report


def validate(doc: Any, *, text: str | bytes | None = None, canonical: bool = False,
             lints: Iterable[str] = (), schema: dict | None = None) -> list[str]:
    """Every problem with ``doc`` as ``path: message``; empty when it is valid."""
    return [str(p) for p in check(doc, text=text, canonical=canonical, lints=lints, schema=schema).problems]


def _lenient(stream: Any) -> None:
    """Never let a message the terminal's encoding cannot write become a crash."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass


def _print_report(path: Path, doc: Any, report: Report, as_json: bool, stream: Any) -> None:
    """One document's report on ``stream``: the JSON record, or the text lines (warnings first)."""
    if as_json:
        record = {"file": str(path), "ok": report.ok,
                  "problems": [{"code": p.code, "path": p.path, "message": p.message} for p in report.problems],
                  "warnings": [{"code": p.code, "path": p.path, "message": p.message} for p in report.warnings]}
        encoding = (getattr(stream, "encoding", None) or "utf-8").lower().replace("-", "")
        print(json.dumps(record, ensure_ascii=encoding != "utf8"), file=stream)   # valid JSON, whatever the locale
        return
    for warning in report.warnings:
        print(f"{path}: warning: {warning} [{warning.code}]", file=stream)
    if report.problems:
        for problem in report.problems:
            print(f"{path}: {problem} [{problem.code}]", file=stream)
    else:
        counts = ", ".join(f"{len(doc.get(k, []))} {k}" for k in RECORD_ARRAYS if isinstance(doc, dict) and k in doc)
        print(f"{path}: ok ({counts})", file=stream)


def _check_file(path: Path, args: argparse.Namespace, schema: dict | None) -> int:
    """Check one document as the CLI does; return its exit status (0, 1 or 2)."""
    try:
        data = path.read_bytes()
        doc = loads(data)
    except (OSError, LoadError) as exc:
        if args.format == "json":       # the record goes with the other records: standard output
            _print_report(path, None, Report([Problem("IJSON", "$", f"cannot read: {exc}")]), True,
                          sys.stderr if args.print_canonical else sys.stdout)
        else:
            print(f"{path}: cannot read: {exc}", file=sys.stderr)
        return 2
    if args.print_canonical:            # stdout carries the canonical bytes and nothing else (11.6)
        # Order first, then validate: the record arrays, id lists and keyed
        # lists are sorted here, so an unsorted document is repaired, not
        # refused (section 10: a producer never has to hand-sort). Everything
        # else must be valid before anything is written.
        try:
            ordered = canonicalize(doc) if isinstance(doc, dict) else doc
        except (TypeError, ValueError):     # a set field with unhashable or mixed elements: let the schema say so
            ordered = doc
        report = check(ordered, lints=args.lint, schema=schema)
        if not report.ok:
            _print_report(path, ordered, report, args.format == "json", sys.stderr)
            return 1
        for warning in report.warnings:
            print(f"{path}: warning: {warning} [{warning.code}]", file=sys.stderr)
        text = canonical_dumps(ordered)
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            sys.stdout.flush()
            buffer.write(text.encode("utf-8"))
            buffer.flush()
        else:
            sys.stdout.write(text)
        return 0
    report = check(doc, text=data, canonical=args.canonical, lints=args.lint, schema=schema)
    _print_report(path, doc, report, args.format == "json", sys.stdout)
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="formal-companion-annotations-validate",
        description="Validate formal-companion annotation documents (format 1.x, each against the schema "
                    "of the minor it declares).")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("documents", nargs="+", type=Path, help="annotation JSON files")
    parser.add_argument("--canonical", action="store_true",
                        help="also require the canonical serialisation, byte for byte")
    parser.add_argument("--print-canonical", action="store_true",
                        help="sort the (single) document, validate the result, then write its canonical form as "
                             "UTF-8 and exit")
    parser.add_argument("--lint", action="append", default=[], choices=sorted(LINTS), metavar="NAME",
                        help="also run an opt-in lint (repeatable): " + ", ".join(sorted(LINTS)))
    parser.add_argument("--format", choices=("text", "json"), default="text",
                        help="text lines (default), or one JSON object per document: "
                             '{"file", "ok", "problems": [{"code", "path", "message"}], "warnings": [...]}')
    # Hidden on purpose: it lets the tests evaluate a document against a schema
    # file other than the packaged one (a synthetic 1.1, say). A user never
    # needs it; the packaged schema of the declared minor is the only one that
    # counts for validity.
    parser.add_argument("--schema", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.print_canonical and len(args.documents) != 1:
        parser.error("--print-canonical takes exactly one document")
    _lenient(sys.stdout)
    _lenient(sys.stderr)
    schema = None
    if args.schema is not None:
        try:
            schema = load_schema(args.schema)
        except (OSError, ValueError) as exc:
            print(f"{parser.prog}: cannot read the schema {args.schema}: {exc}", file=sys.stderr)
            return 2
    if schema is None and not SCHEMA_PATH.is_file():
        # A broken install, not a problem with any document: the schema ships as
        # package data, and without it no document could be checked.
        print(f"{parser.prog}: schema file not found: {SCHEMA_PATH} (it ships inside the package as "
              f"package data; reinstall formal-companion-annotations)", file=sys.stderr)
        return 2
    status = 0
    for path in args.documents:
        try:
            status = max(status, _check_file(path, args, schema))
        except Exception as exc:                                # noqa: BLE001 - the CLI's last resort
            print(f"{path}: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
            status = 2
    return status


if __name__ == "__main__":
    sys.exit(main())
