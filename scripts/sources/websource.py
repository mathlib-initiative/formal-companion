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
  (:func:`entry_problems`), the page flags among them (:func:`page_flags`), and
  what an entry's flags say when it recorded none (:func:`effective_flags`);
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

#: the entry origins, defined in ``cflib.vocab`` (CLAUDE.md section 4: one vocabulary)
ORIGIN_ENUMERATED = vocab.SOURCE_ORIGIN_ENUMERATED
ORIGIN_ADOPTED = vocab.SOURCE_ORIGIN_ADOPTED
ORIGINS = vocab.SOURCE_ORIGINS

#: every field a manifest entry carries, in the contract's order; a value a
#: source does not have yet is null
ENTRY_FIELDS = ("source_id", "family", "pageid", "revision", "title", "wikidata",
                "assessment", "origin", "licence_class", "fetch", "raw_sha256",
                "html_sha256", "layer_sha256", "renderer")
#: optional fields an entry may also carry
OPTIONAL_FIELDS = ("adopted_from", "flags")
#: the fields a later manifest version may fill in (null -> value); every other
#: field is fixed when the entry is first written
SHA_FIELDS = ("raw_sha256", "html_sha256", "layer_sha256")

#: the assessment classes that make a page a redirect, a disambiguation page, a list
REDIRECT_CLASSES = frozenset({"redirect"})
DISAMBIGUATION_CLASSES = frozenset({"disambig", "disambiguation"})
LIST_CLASSES = frozenset({"list", "fl"})
#: a title that names a list
LIST_TITLE_PREFIXES = ("List of ", "Lists of ")

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


def family(family_id: str, workspace=None, *, network: bool = False
           ) -> registry_mod.SourceFamily:
    """The registry family ``family_id``, refused when unknown or structurally broken.

    Offline use (create, adopt, render, a replay of recorded responses) accepts
    any family ``SourceFamily.problems()`` passes, an overlay-only one with its
    own licence included, as registry/README.md says.  ``network=True`` -- the
    caller is about to fetch from the family's ``site`` -- also refuses a family
    nobody confirmed, and a confirmed one whose ``site`` the workspace overlay
    changed: the one module that may reach Wikipedia fetches only from the site a
    reviewed registry row names (CLAUDE.md section 3).
    """
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
    if network:
        if not fam.confirmed:
            raise CfError(f"family {family_id} is not confirmed, so nothing is fetched from "
                          f"its site {fam.site}",
                          hint="a family is fetched from only once its registry row is "
                               "reviewed (confirmed: true in registry/sources.yaml); "
                               "--fixtures replays recorded responses without it")
        shipped = registry_mod.Registry.load(None).family(family_id)
        if shipped is not None and shipped.site != fam.site:
            raise CfError(f"family {family_id}: the workspace overlay changes its site from "
                          f"{shipped.site} to {fam.site}; nothing is fetched",
                          hint=f"delete `site` from the {family_id} row of the workspace "
                               f"{registry_mod.OVERLAY_FILE}")
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
    if assessment is not None and not isinstance(assessment, dict):
        raise CfError(f"a web source's assessment is null or {{class, importance}}, not "
                      f"{assessment!r}")
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


def page_flags(*, title: str | None, assessment: dict | None, redirect: bool = False,
               disambiguation: bool = False) -> dict:
    """An enumerated page's ``flags`` (``cflib.vocab.PAGE_FLAGS``); ``biography`` undetermined.

    ``redirect`` and ``disambiguation`` are the page info's, or the project's own
    class for them; ``list`` is a List or Featured List class, or a title that
    starts "List of" / "Lists of".  Wikidata's own list and disambiguation
    classes are not used: they misclass real articles.
    """
    cls = str((assessment or {}).get("class") or "").casefold()
    return {vocab.PAGE_FLAG_REDIRECT: bool(redirect) or cls in REDIRECT_CLASSES,
            vocab.PAGE_FLAG_DISAMBIGUATION: bool(disambiguation) or cls in DISAMBIGUATION_CLASSES,
            vocab.PAGE_FLAG_LIST: cls in LIST_CLASSES
            or str(title or "").startswith(LIST_TITLE_PREFIXES),
            vocab.PAGE_FLAG_BIOGRAPHY: None}


def merged_flags(old: dict | None, new: dict) -> dict:
    """``old`` with each flag it has not determined taken from ``new`` (a flag never changes)."""
    out = dict(old or {})
    for name, value in new.items():
        if out.get(name) is None and value is not None:
            out[name] = value
        out.setdefault(name, value)
    return out


def effective_flags(entry: dict) -> dict:
    """The entry's ``flags``, each one it has not determined read off its own record.

    An entry carries no flags (``flags`` null) when it was enumerated before
    the flags were recorded, or seeded by ``create --from`` from such a
    sourceset; its recorded assessment class and title still say what
    :func:`page_flags` would: a Redirect or Disambig class, a List class or a
    "List of" title (field defect S5: a whole sourceset of null flags was
    fetched and planned stubs and all).  A determined flag is kept as recorded;
    ``biography`` stays undetermined until ``cf sourceset flag-biographies``.
    Nothing here is written back: the manifest keeps what was recorded.
    """
    derived = page_flags(title=entry.get("title"), assessment=entry.get("assessment"))
    return merged_flags(entry.get("flags"), derived)


def concept_page(entry: dict) -> bool:
    """No flag of the entry's page says it is not a concept article (an unknown one does not).

    The flags are :func:`effective_flags`: a null flag the entry's own
    assessment class settles is settled by it.
    """
    return not any(effective_flags(entry).values())


def no_article(entry: dict) -> bool:
    """Is the entry's page a redirect or a disambiguation page (:func:`effective_flags`)?

    What a plain ``cf sourceset fetch`` leaves out: such a page's bytes are a
    stub, no article.
    """
    flags = effective_flags(entry)
    return bool(flags.get(vocab.PAGE_FLAG_REDIRECT) or flags.get(vocab.PAGE_FLAG_DISAMBIGUATION))


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
    if not _encodable(record):
        out.append(f"{where}: a string holds a lone surrogate (it has no UTF-8 bytes)")
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
    flags = record.get("flags")
    if flags is not None and not (isinstance(flags, dict) and set(flags) <= vocab.PAGE_FLAGS
                                  and all(v is None or isinstance(v, bool)
                                          for v in flags.values())):
        out.append(f"{where}: flags is null or {{{', '.join(sorted(vocab.PAGE_FLAGS))}}} "
                   f"each true, false or null")
    return out


def _encodable(value: Any) -> bool:
    """Every string inside ``value`` has UTF-8 bytes (no lone surrogate)."""
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def revision_url(fam: registry_mod.SourceFamily, pageid: int, revision: int) -> str:
    """The permalink of one revision (the family's ``revision_url`` template)."""
    return fam.revision_link(pageid, revision)
