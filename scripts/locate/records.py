"""Build the Literature Links 2.0 records of locate -- standard library only.

New code, implementing docs/proposals/wikipedia-locate.md section 2.7 in the
form references/locate-documents.md section 6 fixes.  It imports nothing from
``cflib``: the export calls it inside the skill, and the gate can carry it to a
run host (with ``locate_text.py`` and the vendored ``literature_links``) to
build the same records a worker's document will become, and check them.

Every input is a plain mapping:

* ``source`` -- the sourceset entry (``source_id``, ``pageid``, ``revision``,
  ``title``, ``html_sha256``, ``renderer``) plus ``uri`` (the revision's
  permalink), ``container_title`` and ``licence`` (an SPDX id);
* ``passage`` -- the outcome row (``item``, ``passage_kind``, ``span``,
  ``status``, ``challenge``) of references/locate-documents.md section 10;
* ``location`` -- one of its locations, with ``proof_status`` joined from the
  index, and ``module``, ``path``, ``line``, ``end_line``;
* ``library`` -- the universe's library (``id``, ``repo``, ``commit``);
* ``universe`` -- ``{id, sha256, libraries: [library, ...]}``.

Three record shapes, all with ``id = <source_id>::<item>``:

* :func:`location_record` -- one ``lean`` end at the declaration, ``role:
  key``, the relation and the passage's derived status;
* :func:`absence_record` -- ``relation: noMatch``, ``status: not_formalized``,
  one ``lean`` end per universe library with no location (the spec's
  checked-absence form), and the search receipt referenced by sha256 and
  count, never inline;
* :func:`unresolved_record` -- ``object: []`` and no status.

The quote is capped (:func:`cap_quote`): at most ``QUOTE_WORDS`` words of
``exact`` from the passage start, at most ``CONTEXT_CHARS`` characters of
``prefix`` and ``suffix``, cut from the layer itself and widened only within
those caps until it occurs exactly once; a passage whose capped quote cannot
be made unique is refused (:class:`RecordError`), never exported ambiguous.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE / "vendor", _HERE.parent.parent / "vendor"):
    if (_candidate / "literature_links").is_dir():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break
for _candidate in (_HERE, _HERE.parent / "gate"):
    if (_candidate / "locate_text.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

import locate_text  # noqa: E402
from literature_links import companion as _ll  # noqa: E402

#: ``cflib.vocab.LOCATE_EXTENSION`` (restated: this module runs without cflib)
EXTENSION = "org.mathlib-initiative.formal-companion.locate"
#: ``cflib.vocab.EXPORT_QUOTE_WORDS`` / ``EXPORT_CONTEXT_CHARS``
QUOTE_WORDS = 12
CONTEXT_CHARS = 32
#: ``cflib.vocab.MAX_LOCATIONS_PER_PASSAGE``
MAX_LOCATIONS = 5
NO_MATCH = "noMatch"
NOT_FORMALIZED = "not_formalized"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SPACE = re.compile(r"\s")


class RecordError(ValueError):
    """A record that cannot be built as the format and the caps require."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(obj: Any) -> str:
    """The one JSON spelling of a record: sorted keys, no spaces, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------------
# the capped quote
# --------------------------------------------------------------------------

def _words_end(layer: str, start: int, words: int) -> int:
    """The end offset of the first ``words`` whitespace-separated words from ``start``."""
    i, n, count = start, len(layer), 0
    while i < n and count < words:
        while i < n and _SPACE.match(layer[i]):
            i += 1
        if i >= n:
            break
        while i < n and not _SPACE.match(layer[i]):
            i += 1
        count += 1
    return i


def _clip_left(text: str, chars: int) -> str:
    """The last ``chars`` characters of ``text``, cut at a word boundary when one fits."""
    if len(text) <= chars:
        return text
    clipped = text[-chars:]
    cut = clipped.find(" ")
    return clipped[cut + 1:] if 0 <= cut < len(clipped) - 1 else clipped


def _clip_right(text: str, chars: int) -> str:
    if len(text) <= chars:
        return text
    clipped = text[:chars]
    cut = clipped.rfind(" ")
    return clipped[:cut] if cut > 0 else clipped


def cap_quote(layer: str, start: int, end: int | None = None, *,
              words: int = QUOTE_WORDS, context: int = CONTEXT_CHARS) -> dict:
    """``{exact, prefix, suffix}`` for the passage starting at ``start``, within the caps.

    ``exact`` is the first ``words`` words of the passage (never past ``end``);
    ``prefix``/``suffix`` are empty when ``exact`` is already unique, else widened
    word by word up to ``context`` characters each.  The result occurs exactly
    once on the layer, at ``start``, or :class:`RecordError` says why not.
    """
    if not 0 <= start < len(layer):
        raise RecordError(f"passage start {start} is outside the layer")
    stop = _words_end(layer, start, words)
    if end is not None:
        stop = min(stop, max(int(end), start + 1))
    exact = layer[start:stop].rstrip()
    if not exact:
        raise RecordError(f"the passage at {start} has no text to quote")
    if locate_text.find_quote(layer, exact) == [start]:
        return {"exact": exact, "prefix": "", "suffix": ""}
    after = start + len(exact)
    for width in range(1, context + 1):
        prefix = _clip_left(layer[max(0, start - width):start], width)
        suffix = _clip_right(layer[after:after + width], width)
        for pre, suf in ((prefix, ""), ("", suffix), (prefix, suffix)):
            if locate_text.find_quote(layer, exact, pre, suf) == [start]:
                return {"exact": exact, "prefix": pre, "suffix": suf}
    raise RecordError(f"the quote at {start} ({exact[:40]!r}) occurs more than once on the "
                      f"layer even with {context} characters of context each side")


# --------------------------------------------------------------------------
# ends
# --------------------------------------------------------------------------

def subject_end(source: Mapping[str, Any], quote: Mapping[str, str], *,
                span_sha256: str) -> dict:
    """The ``html`` end: the revision's permalink, the quote, the CSL citation, the licence."""
    uri = str(source["uri"])
    revision = str(source["revision"])
    citation = {"id": str(source["source_id"]), "type": "webpage",
                "title": str(source.get("title") or source["source_id"]), "URL": uri}
    if source.get("container_title"):
        citation["container-title"] = str(source["container_title"])
    quote_out = {"exact": quote["exact"]}
    if quote.get("prefix"):
        quote_out["prefix"] = quote["prefix"]
    if quote.get("suffix"):
        quote_out["suffix"] = quote["suffix"]
    ext = {"pageid": int(source["pageid"]), "html_sha256": str(source["html_sha256"]),
           "renderer": str(source["renderer"]), "span_sha256": span_sha256,
           "licence": str(source.get("licence") or "")}
    return {"type": "html", "uri": uri, "version": revision,
            "location": {"quote": quote_out}, "citation": citation,
            "extensions": {EXTENSION: ext}}


def _commit(library: Mapping[str, Any]) -> str:
    commit = str(library.get("commit") or "")
    if not _COMMIT.match(commit):
        raise RecordError(f"library {library.get('id')!r} has no 40-hex commit")
    return commit


def lean_end(library: Mapping[str, Any], location: Mapping[str, Any] | None = None) -> dict:
    """A ``lean`` end: at a declaration (``role: key``), or the bare library (an absence)."""
    end = {"type": "lean", "uri": str(library["repo"]), "version": _commit(library)}
    if location is None:
        end["extensions"] = {EXTENSION: {"library": str(library["id"])}}
        return end
    loc = {"declaration": str(location["declaration"])}
    if location.get("module"):
        loc["module"] = str(location["module"])
    ext = {"library": str(library["id"]), "proof_status": str(location["proof_status"])}
    for key in ("path", "line", "end_line"):
        if location.get(key) is not None:
            ext[key] = location[key]
    if location.get("statement"):
        ext["statement"] = True
    end.update(role="key", location=loc, extensions={EXTENSION: ext})
    return end


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

def record_id(source_id: str, item: str) -> str:
    return f"{source_id}::{item}"


def _record_ext(passage: Mapping[str, Any], universe: Mapping[str, Any], **extra) -> dict:
    ext = {"item": str(passage["item"]), "universe": str(universe["id"]),
           "universe_sha256": str(universe["sha256"]), "challenge": passage_challenge(passage)}
    ext.update({k: v for k, v in extra.items() if v is not None})
    return ext


def passage_challenge(passage: Mapping[str, Any]) -> str:
    value = passage.get("challenge")
    if not value:
        raise RecordError(f"{passage.get('source_id')} {passage.get('item')}: no challenge value "
                          "(hard rule 5: nothing leaves the workspace without one)")
    return str(value)


def location_record(source: Mapping[str, Any], passage: Mapping[str, Any],
                    location: Mapping[str, Any], library: Mapping[str, Any],
                    universe: Mapping[str, Any], quote: Mapping[str, str], *,
                    span_sha256: str) -> dict:
    challenge = location.get("challenge")
    if not challenge:
        raise RecordError(f"{passage.get('item')}: location {location.get('declaration')} has no "
                          "challenge value")
    record = {
        "id": record_id(str(source["source_id"]), str(passage["item"])),
        "subject": [subject_end(source, quote, span_sha256=span_sha256)],
        "object": [lean_end(library, location)],
        "relation": str(location["relation"]),
        "kind": str(passage["passage_kind"]),
        "extensions": {EXTENSION: _record_ext(
            passage, universe, location_key=str(location["location_key"]),
            rank=int(location["rank"]) if location.get("rank") is not None else None,
            confidence=location.get("confidence") or None)},
    }
    record["extensions"][EXTENSION]["challenge"] = str(challenge)
    if passage.get("status"):
        record["status"] = str(passage["status"])
    if location.get("note"):
        record["note"] = str(location["note"])
    return record


def absence_record(source: Mapping[str, Any], passage: Mapping[str, Any],
                   universe: Mapping[str, Any], quote: Mapping[str, str], *,
                   span_sha256: str, receipt: Mapping[str, Any]) -> dict:
    """The checked absence; ``receipt`` is ``{sha256, queries, candidates}`` of the sidecar."""
    libraries = sorted(universe["libraries"], key=lambda lib: str(lib["id"]))
    if not libraries:
        raise RecordError("an absence names the universe's libraries, and it has none")
    absence = passage.get("absence") or {}
    challenge = absence.get("challenge") or passage.get("challenge")
    record = {
        "id": record_id(str(source["source_id"]), str(passage["item"])),
        "subject": [subject_end(source, quote, span_sha256=span_sha256)],
        "object": [lean_end(lib) for lib in libraries],
        "relation": NO_MATCH,
        "status": NOT_FORMALIZED,
        "kind": str(passage["passage_kind"]),
        "extensions": {EXTENSION: _record_ext(passage, universe, receipt=dict(receipt))},
    }
    if not challenge:
        raise RecordError(f"{passage.get('item')}: the absence has no challenge value")
    record["extensions"][EXTENSION]["challenge"] = str(challenge)
    return record


def unresolved_record(source: Mapping[str, Any], passage: Mapping[str, Any],
                      universe: Mapping[str, Any], quote: Mapping[str, str], *,
                      span_sha256: str) -> dict:
    """An unresolved outcome: ``object: []`` and no status; counted, never not formalized."""
    return {
        "id": record_id(str(source["source_id"]), str(passage["item"])),
        "subject": [subject_end(source, quote, span_sha256=span_sha256)],
        "object": [],
        "kind": str(passage["passage_kind"]),
        "extensions": {EXTENSION: _record_ext(passage, universe)},
    }


def receipt_document(source_id: str, item: str, receipt: Mapping[str, Any],
                     universe: Mapping[str, Any]) -> tuple[str, dict, dict]:
    """``(sha256, the sidecar, the reference)`` for an absence's search receipt."""
    queries = [str(q) for q in receipt.get("queries") or []]
    candidates = [dict(c) for c in receipt.get("candidates") or [] if isinstance(c, dict)]
    body = {"source_id": source_id, "item": item, "universe": str(universe["id"]),
            "universe_sha256": str(universe["sha256"]), "queries": queries,
            "candidates": candidates}
    digest = sha256_text(canonical(body))
    return digest, body, {"sha256": digest, "queries": len(set(queries)),
                          "candidates": len(candidates)}


def passage_records(source: Mapping[str, Any], passage: Mapping[str, Any], layer: str,
                    universe: Mapping[str, Any]) -> tuple[list[dict], list[tuple[str, dict]]]:
    """Every record of one passage outcome, and the receipt sidecars they reference."""
    span = passage.get("span")
    if not span:
        raise RecordError(f"{passage.get('item')}: no span to quote")
    start, end = int(span[0]), int(span[1])
    quote = cap_quote(layer, start, end)
    span_sha = sha256_text(layer[start:end])
    libs = {str(lib["id"]): lib for lib in universe["libraries"]}
    records: list[dict] = []
    sidecars: list[tuple[str, dict]] = []
    outcome = passage.get("outcome")
    locations = sorted(passage.get("locations") or [],
                       key=lambda l: (l.get("rank") or 99, l["library"], l["declaration"]))
    if len(locations) > MAX_LOCATIONS:
        raise RecordError(f"{passage['item']}: {len(locations)} locations, over the cap of "
                          f"{MAX_LOCATIONS}")
    ranks = [l.get("rank") for l in locations]
    if len(set(ranks)) != len(ranks):
        raise RecordError(f"{passage['item']}: two locations share a rank")
    passage_challenge(passage)
    for loc in locations:
        if loc["library"] not in libs:
            raise RecordError(f"{passage['item']}: library {loc['library']} is not in the universe")
        records.append(location_record(source, passage, loc, libs[loc["library"]], universe,
                                       quote, span_sha256=span_sha))
    if outcome == "absent":
        digest, body, ref = receipt_document(str(source["source_id"]), str(passage["item"]),
                                             (passage.get("absence") or {}).get("receipt")
                                             or passage.get("receipt") or {}, universe)
        sidecars.append((digest, body))
        records.append(absence_record(source, passage, universe, quote, span_sha256=span_sha,
                                      receipt=ref))
    elif not locations:
        records.append(unresolved_record(source, passage, universe, quote,
                                         span_sha256=span_sha))
    return records, sidecars


def check(record: Mapping[str, Any]) -> list[str]:
    """The vendored Literature Links checker's problems for one record; empty when valid."""
    return _ll.validate(dict(record))


def check_all(records: Iterable[Mapping[str, Any]]) -> list[str]:
    out = []
    for record in records:
        out.extend(f"{record.get('id')}: {p}" for p in check(record))
    return out
