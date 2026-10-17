"""The identity of a web source: page id and revision, the fixed fetch, the record.

New code, implementing docs/proposals/wikipedia-locate.md section 2.1 in the
manifest-entry shape of references/locate-documents.md section 7.  A web source
is one canonical rendering of one page revision, identified by
``<family>-<pageid>-<revision>`` (``cflib.registry.web_source_id``) and NEVER by
title: titles carry ``/`` and move, so a title is metadata here and nothing
keys on it.

Three things are fixed and live only here:

* the parse parameter set every fetch uses (:data:`PARSE_PARAMS`, plus
  ``oldid``), whose canonical-JSON sha (:func:`params_sha256`) every fetched
  entry records, so two fetches of one revision with different parameters can
  never pass for one source;
* the manifest entry's fields (:data:`ENTRY_FIELDS`) and what each may hold
  (:func:`entry_problems`);
* the revision permalink, from the family's ``revision_url`` template in the
  registry -- built from the page id and revision, so it survives a page move.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from cflib import registry as registry_mod
from cflib import vocab
from cflib.errors import CfError

#: the fixed parse parameter set (references/locate-documents.md section 7);
#: ``oldid`` is added per revision and is not part of the hashed set
PARSE_PARAMS: dict[str, str] = {
    "action": "parse",
    "prop": "text",
    "formatversion": "2",
    "disablelimitreport": "1",
    "disableeditsection": "1",
    "format": "json",
}
FETCH_API = "action=parse"

ORIGIN_ENUMERATED = "enumerated"
ORIGIN_ADOPTED = "adopted"
ORIGINS = frozenset({ORIGIN_ENUMERATED, ORIGIN_ADOPTED})

#: every field a manifest entry carries, in the contract's order; a value a
#: source does not have yet is null
ENTRY_FIELDS = ("source_id", "family", "pageid", "revision", "title", "wikidata",
                "assessment", "origin", "licence_class", "fetch", "raw_sha256",
                "html_sha256", "layer_sha256", "renderer")
#: optional fields an entry may also carry
OPTIONAL_FIELDS = ("adopted_from",)
#: the fields a later manifest version may fill in (null -> value); every other
#: field is fixed when the entry is first written
SHA_FIELDS = ("raw_sha256", "html_sha256", "layer_sha256")

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_QID = re.compile(r"Q[1-9][0-9]*\Z")


def params_sha256() -> str:
    """sha256 of the canonical JSON of :data:`PARSE_PARAMS` (sorted keys, no spaces)."""
    blob = json.dumps(PARSE_PARAMS, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def parse_params(revision: int) -> dict[str, str]:
    """The full request parameters for one revision."""
    _positive(revision, "revision")
    return {**PARSE_PARAMS, "oldid": str(int(revision))}


def fetch_block() -> dict:
    """The ``fetch`` value of an entry fetched with the fixed parameter set."""
    return {"api": FETCH_API, "params_sha256": params_sha256()}


def _positive(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CfError(f"a web source's {name} is a positive integer, not {value!r}")
    return value


def family(family_id: str, workspace=None) -> registry_mod.SourceFamily:
    """The registry family ``family_id``, refused when unknown or not confirmed."""
    reg = registry_mod.Registry.load(workspace)
    fam = reg.family(family_id)
    if fam is None:
        known = ", ".join(sorted(reg.families)) or "none"
        raise CfError(f"no web-source family {family_id!r} in the registry",
                      hint=f"families the registry knows: {known} (registry/sources.yaml "
                           f"`families:`)")
    problems = fam.problems()
    if problems:
        raise CfError(f"family {family_id}: " + "; ".join(problems))
    return fam


def api_url(fam: registry_mod.SourceFamily) -> str:
    """The MediaWiki API endpoint of a family's site."""
    return fam.site.rstrip("/") + "/w/api.php"


def entry(fam: registry_mod.SourceFamily, pageid: int, revision: int, *,
          title: str | None = None, wikidata: str | None = None,
          assessment: dict | None = None, origin: str = ORIGIN_ENUMERATED,
          **extra) -> dict:
    """A new manifest entry: identity and metadata set, every sha null."""
    _positive(pageid, "page id")
    _positive(revision, "revision")
    record = {
        "source_id": fam.source_id(pageid, revision),
        "family": fam.id,
        "pageid": pageid,
        "revision": revision,
        "title": title or None,
        "wikidata": wikidata or None,
        "assessment": dict(assessment) if assessment else None,
        "origin": origin,
        "licence_class": fam.licence_class,
        "fetch": None,
        "raw_sha256": None,
        "html_sha256": None,
        "layer_sha256": None,
        "renderer": None,
    }
    for key, value in extra.items():
        if key not in OPTIONAL_FIELDS:
            raise CfError(f"a manifest entry has no field {key!r}")
        record[key] = value
    problems = entry_problems(record)
    if problems:
        raise CfError("; ".join(problems))
    return record


def entry_problems(record: Any) -> list[str]:
    """What is wrong with one manifest entry; empty when it is well formed."""
    if not isinstance(record, dict):
        return ["a manifest entry is a JSON object"]
    sid = record.get("source_id")
    where = f"entry {sid!r}"
    out: list[str] = []
    unknown = sorted(set(record) - set(ENTRY_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        out.append(f"{where}: unknown field(s) {', '.join(unknown)}")
    missing = [f for f in ENTRY_FIELDS if f not in record]
    if missing:
        out.append(f"{where}: missing field(s) {', '.join(missing)}")
        return out
    parsed = registry_mod.parse_web_source_id(str(sid or ""))
    if parsed is None:
        out.append(f"{where}: not a web source id (<family>-<pageid>-<revision>)")
    elif parsed != (record["family"], record["pageid"], record["revision"]):
        out.append(f"{where}: the id does not say family {record['family']!r}, page "
                   f"{record['pageid']!r}, revision {record['revision']!r}")
    for name in ("title", "wikidata", "renderer"):
        if record[name] is not None and not isinstance(record[name], str):
            out.append(f"{where}: {name} is a string or null")
    if record["wikidata"] is not None and not _QID.match(str(record["wikidata"])):
        out.append(f"{where}: wikidata {record['wikidata']!r} is not a QID")
    assessment = record["assessment"]
    if assessment is not None and not (
            isinstance(assessment, dict)
            and set(assessment) <= {"class", "importance"}
            and all(v is None or isinstance(v, str) for v in assessment.values())):
        out.append(f"{where}: assessment is null or {{class, importance}} strings")
    if record["origin"] not in ORIGINS:
        out.append(f"{where}: origin must be {' or '.join(sorted(ORIGINS))}")
    if record["licence_class"] not in vocab.LICENCE_CLASSES:
        out.append(f"{where}: licence_class must be one of "
                   f"{', '.join(sorted(vocab.LICENCE_CLASSES))}")
    fetch = record["fetch"]
    if fetch is not None and not (isinstance(fetch, dict) and set(fetch) == {"api", "params_sha256"}
                                  and fetch.get("api") == FETCH_API
                                  and _SHA.match(str(fetch.get("params_sha256")))):
        out.append(f"{where}: fetch is null or {{api: {FETCH_API}, params_sha256}}")
    for name in SHA_FIELDS:
        value = record[name]
        if value is not None and not _SHA.match(str(value)):
            out.append(f"{where}: {name} is null or 64 lowercase hex digits")
    if record["renderer"] is not None and record["layer_sha256"] is None:
        out.append(f"{where}: a renderer with no layer_sha256")
    if record["layer_sha256"] is not None and record["html_sha256"] is None:
        out.append(f"{where}: a layer_sha256 with no html_sha256")
    adopted = record.get("adopted_from")
    if adopted is not None and not isinstance(adopted, dict):
        out.append(f"{where}: adopted_from is an object")
    return out


def revision_url(fam: registry_mod.SourceFamily, pageid: int, revision: int) -> str:
    """The permalink of one revision (the family's ``revision_url`` template)."""
    return fam.revision_link(pageid, revision)
