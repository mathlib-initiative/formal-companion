"""Build the Literature Links 1.0 records of locate -- standard library only.

New code, implementing docs/proposals/wikipedia-locate.md section 2.7 in the
form references/locate-documents.md section 6 fixes.  It imports nothing from
``cflib``: the export calls it inside the skill, and the gate can carry it to a
run host (with ``locate_text.py`` and the vendored ``literature_links``) to
build the same records a worker's document will become, and check them.

Every input is a plain mapping:

* ``source`` -- the sourceset entry (``source_id``, ``pageid``, ``revision``,
  ``title``, ``wikidata``, ``html_sha256``, ``layer_sha256``, ``renderer``) plus
  ``uri`` (the revision's permalink), ``container_title`` and ``licence`` (an
  SPDX id);
* ``passage`` -- the outcome row (``item``, ``passage_kind``, ``span``,
  ``status``, ``challenge``, ``section``, ``prominence``) of
  references/locate-documents.md section 10;
* ``location`` -- one of its locations, with ``proof_status`` joined from the
  index, and ``module``, ``path`` and ``line`` (required: :func:`location_problem`)
  and, when the index has one, ``end_line`` (a published index may record
  ``line`` only: the end then carries no ``end_line``, never a guessed one);
* ``library`` -- the universe's library (``id``, ``repo``, ``commit``);
* ``universe`` -- ``{id, sha256, libraries: [library, ...]}``.

Three record shapes, all with ``id = <source_id>::<item>``, the same subject
end -- the capped quote, and in its locate extension the passage's whole
``span`` (half-open code-point offsets on the layer, never its text) beside the
``layer_sha256`` they index and the ``span_sha256`` of those characters, so a
consumer holding that layer highlights the passage, not only its quote, and on
a ``subject`` passage the article's QID as ``identifiers.wikidata``, except on a
record nobody judged under that prominence (``challenge_pending``:
:func:`article_qid`) -- and the passage's ``section`` (its heading path) and
``prominence`` in the locate extension when the passage row carries them --
:func:`passage_records`, the export's entry, requires both -- and, for a
passage with a coverage claim (the maintainer's decision D1), ``coverage:
{complete, parts: [location_key, ...], challenge}`` on every one of its records
(:func:`coverage_problem`):

* :func:`location_record` -- one ``lean`` end at the declaration, ``role:
  key``, the relation and the passage's derived status; a ``closeMatch``
  names its convention difference in ``note`` (:func:`passage_records`
  refuses one with none);
* :func:`absence_record` -- ``relation: noMatch``, ``status: not_formalized``,
  one minimal ``lean`` end per repository the universe searched at its commit
  (``type``, ``uri``, ``version`` and nothing else: the spec's checked-absence
  form, at a few dozen bytes a library; :func:`absence_ends`), and the search
  receipt referenced by sha256 and count, never inline;
* :func:`unresolved_record` -- ``object: []`` and no status.

A passage of a run of status rule 2 (``STATUS_RULE_BUNDLE``; docs/proposals/
statement-cut.md section 2.4, ``cf-locate-recordset/2``) is built by
:func:`passage_records` with ``status_rule=2``: every record's locate extension
also carries the revision-free ``passage_key``, a location record's its
``pair_key`` too (:func:`passage_key`, :func:`pair_key`, :func:`bundle_key`:
``cflib.hashing``'s keys, restated here in lockstep because this module imports
no cflib, as ``verify_findings`` restates ``finding_id``), and a coverage claim
rides as a **bundle**, ``{complete, parts, challenge, relation, conjuncts: [{id,
text, part}], bundle_key}`` (:func:`bundle_ext`).  The three builders take those
as keywords, so every call of status rule 1 -- the gate's preview among them --
builds the records it always did.  A passage of a run of status rule 3
(``STATUS_RULE_BROAD``, R-BROAD) is built exactly so: only the status its outcome
row carries was derived differently.

The quote is capped (:func:`cap_quote`): at most ``QUOTE_WORDS`` words of
``exact`` from the passage start, at most ``CONTEXT_CHARS`` characters of
``prefix`` and ``suffix``, cut from the layer itself and widened only within
those caps until it occurs exactly once; a passage whose capped quote cannot
be made unique is refused (:class:`RecordError`), never exported ambiguous.
Free text is held to the same cap: :func:`check_note` for a location's note,
:func:`receipt_document` for an absence's receipt, and :func:`downgrade_note`
says which note a challenge downgrade to ``closeMatch`` gives its location.
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
#: ``cflib.vocab.CLOSE_MATCH``, ``HAS_PART``, ``MIN_COVERAGE_PARTS``,
#: ``COVERAGE_RECORD_KEYS``, ``CHALLENGE_PENDING``, ``CHALLENGE_VERDICTS`` and
#: ``PROMINENCE``
CLOSE_MATCH = "closeMatch"
HAS_PART = "hasPart"
MIN_COVERAGE_PARTS = 2
COVERAGE_KEYS = frozenset({"complete", "parts", "challenge"})
CHALLENGE_PENDING = "challenge_pending"
CHALLENGES = frozenset({"survived", "downgraded", "not_required", "sampled_out",
                        CHALLENGE_PENDING})
PROMINENCES = frozenset({"subject", "central", "supporting"})
#: the keys of a receipt candidate (references/locate-documents.md section 3), the only
#: ones its sidecar carries
RECEIPT_CANDIDATE_KEYS = ("library", "declaration", "verdict", "reason")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
#: ``sources.websource._QID``: the grammar of a source's ``wikidata``
_QID = re.compile(r"Q[1-9][0-9]*\Z")
#: ``cflib.vocab.PROMINENCE_SUBJECT``: the one prominence whose records carry the
#: article's QID
SUBJECT = "subject"
_SPACE = re.compile(r"\s")
#: ``cflib.vocab.STATUS_RULE_D1`` / ``STATUS_RULE_BUNDLE`` / ``STATUS_RULE_BROAD``: the
#: derivation a run's outcomes were derived under, which picks the record set's shapes
#: (``cf-locate-recordset/1`` for 1, ``/2`` for 2 and 3: status rule 3 changes the statuses
#: a record carries, never its shape)
STATUS_RULE_D1 = 1
STATUS_RULE_BUNDLE = 2
STATUS_RULE_BROAD = 3
STATUS_RULES = (STATUS_RULE_D1, STATUS_RULE_BUNDLE, STATUS_RULE_BROAD)
#: ``cflib.vocab.COVERAGE_RECORD_KEYS_BUNDLE`` and ``BUNDLE_RELATIONS``: a bundle as a record
#: of status rule 2 carries it, and the relations it may have (its weakest part's)
COVERAGE_KEYS_BUNDLE = COVERAGE_KEYS | {"relation", "conjuncts", "bundle_key"}
BUNDLE_RELATIONS = frozenset({"exactMatch", "closeMatch", "broadMatch"})


class RecordError(ValueError):
    """A record that cannot be built as the format and the caps require."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# the revision-free keys (cflib.hashing's, restated in lockstep: this module
# imports no cflib; tests/test_locate_bundle_status.py holds the two together)
# --------------------------------------------------------------------------

def _key_part(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\n" in value:
        raise RecordError(f"a key's {name} is a non-empty one-line string with no edge "
                          f"whitespace, not {value!r}")
    return value


def _sha_part(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise RecordError(f"a key's {name} is a 64-hex sha256, not {value!r}")
    return value


def passage_key(pageid: Any, span_sha: str) -> str:
    """``sha256("<pageid>\\n<span_sha256>")`` (``cflib.hashing.passage_key``)."""
    if isinstance(pageid, bool) or not isinstance(pageid, int) or pageid < 1:
        raise RecordError(f"a pageid is a positive integer, not {pageid!r}")
    return sha256_text(f"{pageid}\n{_sha_part(span_sha, 'span_sha256')}")


def pair_key(passage: str, repository: str, declaration: str) -> str:
    """``sha256("<passage_key>\\n<repository>\\n<declaration>")`` (``cflib.hashing.pair_key``)."""
    return sha256_text("\n".join([_sha_part(passage, "passage_key"),
                                  _key_part(repository, "repository"),
                                  _key_part(declaration, "declaration")]))


def bundle_key(passage: str, pair_keys: Iterable[str]) -> str:
    """The passage key and the parts' pair keys, sorted, one per line (``cflib.hashing``)."""
    keys = [_sha_part(k, "pair_key") for k in pair_keys]
    if len(keys) < 2 or len(set(keys)) != len(keys):
        raise RecordError(f"a bundle has at least two distinct parts, not {len(set(keys))}")
    return sha256_text("\n".join([_sha_part(passage, "passage_key"), *sorted(keys)]))


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

def passage_span(passage: Mapping[str, Any]) -> list[int]:
    """The passage's half-open layer span, two ints ``0 <= start < end``, or :class:`RecordError`."""
    span = passage.get("span")
    if (not isinstance(span, (list, tuple)) or len(span) != 2
            or not all(type(offset) is int for offset in span)):
        raise RecordError(f"{passage.get('item')}: no [start, end] layer span ({span!r})")
    start, end = span
    if not 0 <= start < end:
        raise RecordError(f"{passage.get('item')}: the span [{start}, {end}] is not "
                          "0 <= start < end")
    return [start, end]


def article_qid(source: Mapping[str, Any], passage: Mapping[str, Any], *,
                challenge: str | None) -> str | None:
    """The article's QID one record of a passage carries, or None.

    The maintainer's decision B5 (docs/proposals/wikipedia-locate.md sections 2.2
    and 11.3): only a ``subject`` passage -- its prominence after the challenge --
    carries its source's ``wikidata``, the page's own QID, as the subject end's
    ``identifiers.wikidata``; no other passage does, since the article's QID on
    every passage welds the wrong concepts.  ``challenge`` is the record's own
    value, the one its extension carries: a ``challenge_pending`` record carries
    none.  It is a record a prominence overturn to ``subject`` reopened, which no
    challenger judged under that prominence's rule, so the QID would rest on a
    location (or an absence) nobody checked (the B5 review's F1); the
    passage's other records carry it, so the rule is per record, never per
    passage.  An article may have several subject passages: an inventory marks
    at most one (docs/proposals/statement-cut.md section 3.4 rule 9, and gate
    rule 10 (h) under statement-cut rule 2), but an older run or a prominence
    overturn to ``subject`` can give it more.  The skill does
    not check that the QID's enwiki sitelink is this article (no network in
    library code): its consumer does, before any concept-level edge.  A source
    with no QID gives None; one whose ``wikidata`` is no QID is refused, never
    guessed -- on every record of a subject passage, a ``challenge_pending`` one
    too, so whether a subject passage exports never turns on its challenge.
    """
    if passage.get("prominence") != SUBJECT:
        return None
    qid = source.get("wikidata")
    if qid is None:
        return None
    if not isinstance(qid, str) or not _QID.match(qid):
        raise RecordError(f"{source.get('source_id')}: wikidata {qid!r} is not a Wikidata QID, "
                          "and a subject passage's records carry it")
    return None if challenge == CHALLENGE_PENDING else qid


def subject_end(source: Mapping[str, Any], quote: Mapping[str, str], *,
                span_sha256: str, span: Iterable[int], wikidata: str | None = None) -> dict:
    """The ``html`` end: the revision's permalink, the quote, the CSL citation, the licence.

    Its locate extension carries the passage's ``span`` on the layer whose sha is
    the source's ``layer_sha256`` (offsets only: a consumer highlights its own
    copy of that layer), beside ``span_sha256``, the sha of those characters.  A
    span is offsets on one layer, so a source with no layer sha is refused.
    ``wikidata`` (:func:`article_qid`) is written as ``identifiers.wikidata``,
    outside the extension, where the format carries an end's identifiers.
    """
    layer_sha = str(source.get("layer_sha256") or "")
    if not _HEX64.match(layer_sha):
        raise RecordError(f"{source.get('source_id')}: no layer_sha256 for the subject end's span")
    start, end = span
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
           "layer_sha256": layer_sha, "renderer": str(source["renderer"]),
           "span": [int(start), int(end)], "span_sha256": span_sha256,
           "licence": str(source.get("licence") or "")}
    html_end = {"type": "html", "uri": uri, "version": revision,
                "location": {"quote": quote_out}, "citation": citation,
                "extensions": {EXTENSION: ext}}
    if wikidata is not None:
        html_end["identifiers"] = {"wikidata": wikidata}
    return html_end


def _commit(library: Mapping[str, Any]) -> str:
    commit = str(library.get("commit") or "")
    if not _COMMIT.match(commit):
        raise RecordError(f"library {library.get('id')!r} has no 40-hex commit")
    return commit


def lean_end(library: Mapping[str, Any], location: Mapping[str, Any] | None = None) -> dict:
    """A ``lean`` end: at a declaration (``role: key``), or the bare library (an absence).

    An absence's end is ``{type, uri, version}`` and nothing else: the record
    names every repository of the universe at its commit (:func:`absence_ends`),
    and the universe file already says which libraries each repository is.  A
    declaration's end carries ``line`` and
    ``end_line`` only as the index has them.
    """
    end = {"type": "lean", "uri": str(library["repo"]), "version": _commit(library)}
    if location is None:
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


def _record_ext(passage: Mapping[str, Any], universe: Mapping[str, Any], *,
                bundle: Mapping[str, Any] | None = None, **extra) -> dict:
    ext = {"item": str(passage["item"]), "universe": str(universe["id"]),
           "universe_sha256": str(universe["sha256"]), "challenge": passage_challenge(passage)}
    if isinstance(passage.get("section"), (list, tuple)):
        ext["section"] = [str(heading) for heading in passage["section"]]
    if passage.get("prominence"):
        ext["prominence"] = str(passage["prominence"])
    claim = passage.get("coverage")
    if claim:
        # status rule 2's bundle (bundle_ext) when the caller built one, else D1's claim
        ext["coverage"] = dict(bundle) if bundle is not None else coverage_ext(claim)
    ext.update({k: v for k, v in extra.items() if v is not None})
    return ext


def coverage_ext(claim: Mapping[str, Any], *, status_rule: int = STATUS_RULE_D1) -> dict:
    """A coverage claim as a record carries it: ``{complete, parts, challenge}``.

    Exactly the keys ``COVERAGE_KEYS`` -- the claim's ``note`` is not exported --
    with the parts' location keys in the claim's order.  The outcomes derive the
    passage's status from this same projection (``cflib.vocab.derive_status``), so
    the status a record carries is the one its claim gives.  Under status rule 2 the
    projection also carries the bundle's ``relation`` (its weakest part's, which the
    outcomes computed): the one more key the derivation reads, under status rule 3
    too.  The exported bundle, which adds the ``conjuncts`` and the ``bundle_key``, is
    :func:`bundle_ext`.
    """
    out = {"complete": True, "parts": [str(k) for k in claim["parts"]],
           "challenge": str(claim.get("challenge"))}
    if status_rule >= STATUS_RULE_BUNDLE:
        out["relation"] = claim.get("relation")
    return out


def bundle_conjuncts(passage: Mapping[str, Any], claim: Mapping[str, Any]) -> list[dict]:
    """``[{id, text, part}]``: each conjunct of the passage beside the part that states it.

    docs/proposals/statement-cut.md section 2.4: the passage's conjuncts (from the
    frozen inventory) in id order, each with the ``location_key`` of the one part of
    the claim whose ``clauses`` name it.  A bundle stands only on a compound
    statement whose conjuncts its parts partition (J3), so a passage with no
    conjuncts, a conjunct no part names or one two parts name is refused.
    """
    conjuncts = passage.get("conjuncts")
    if not isinstance(conjuncts, (list, tuple)) or not conjuncts:
        raise RecordError(f"{passage.get('item')}: a bundle on a passage with no conjuncts "
                          "(a coverage claim stands only on a compound statement)")
    by_key = {str(loc.get("location_key")): loc for loc in passage.get("locations") or []}
    out = []
    for conjunct in conjuncts:
        cid = conjunct.get("id") if isinstance(conjunct, Mapping) else None
        stating = [str(k) for k in claim["parts"]
                   if cid in ((by_key.get(str(k)) or {}).get("clauses") or [])]
        if len(stating) != 1:
            raise RecordError(f"{passage.get('item')}: conjunct {cid!r} is stated by "
                              f"{len(stating)} part(s) of the bundle, not exactly one")
        out.append({"id": str(cid), "text": str(conjunct.get("text") or ""),
                    "part": stating[0]})
    named = {c["part"] for c in out}
    idle = [str(k) for k in claim["parts"] if str(k) not in named]
    if idle:
        raise RecordError(f"{passage.get('item')}: bundle part {idle[0][:12]} states no "
                          "conjunct")
    return out


def bundle_ext(passage: Mapping[str, Any], claim: Mapping[str, Any], *, passage_key: str,
               pair_keys: Mapping[str, str]) -> dict:
    """A bundle as a record of status rule 2 carries it (``COVERAGE_KEYS_BUNDLE``).

    ``{complete, parts, challenge, relation, conjuncts, bundle_key}``: the D1 keys of
    :func:`coverage_ext`, the bundle's ``relation``, its conjuncts beside their parts
    (:func:`bundle_conjuncts`) and its ``bundle_key`` over ``pair_keys`` (``{part's
    location_key: its pair key}``).  A consumer shows a bundle's declarations each
    beside its conjunct from the record set alone.
    """
    if claim.get("relation") not in BUNDLE_RELATIONS:
        raise RecordError(f"{passage.get('item')}: a bundle's relation is one of "
                          f"{', '.join(sorted(BUNDLE_RELATIONS))}, not "
                          f"{claim.get('relation')!r}")
    out = coverage_ext(claim, status_rule=STATUS_RULE_BUNDLE)
    out["conjuncts"] = bundle_conjuncts(passage, claim)
    out["bundle_key"] = bundle_key(passage_key, [pair_keys[str(k)] for k in claim["parts"]])
    return out


def coverage_problem(passage: Mapping[str, Any], *,
                     status_rule: int = STATUS_RULE_D1) -> str | None:
    """Why a passage's coverage claim cannot ride on its records, or None.

    A claim (the maintainer's decision D1) names at least ``MIN_COVERAGE_PARTS``
    distinct parts, each the ``location_key`` of one of the passage's ``hasPart``
    locations, and carries its challenge value (hard rule 5), one of
    ``CHALLENGES``: a refuted claim is removed, never exported as ``refuted``.
    Under status rules 2 and 3 it is a bundle and carries its ``relation`` too, one of
    ``BUNDLE_RELATIONS``.
    """
    claim = passage.get("coverage")
    if claim and status_rule >= STATUS_RULE_BUNDLE and isinstance(claim, Mapping) \
            and claim.get("relation") not in BUNDLE_RELATIONS:
        return (f"{passage.get('item')}: the bundle's relation {claim.get('relation')!r} is not "
                f"one of {', '.join(sorted(BUNDLE_RELATIONS))}")
    return _d1_coverage_problem(passage)


def _d1_coverage_problem(passage: Mapping[str, Any]) -> str | None:
    """:func:`coverage_problem`'s checks of decision D1, every status rule's."""
    claim = passage.get("coverage")
    if not claim:
        return None
    parts = claim.get("parts") if isinstance(claim, Mapping) else None
    if not isinstance(parts, (list, tuple)) or len(set(map(str, parts))) != len(parts) \
            or len(parts) < MIN_COVERAGE_PARTS:
        return (f"{passage.get('item')}: a coverage claim names at least {MIN_COVERAGE_PARTS} "
                f"distinct parts, not {parts!r}")
    has_part = {str(loc.get("location_key")) for loc in passage.get("locations") or []
                if loc.get("relation") == HAS_PART}
    stray = [str(k) for k in parts if str(k) not in has_part]
    if stray:
        return (f"{passage.get('item')}: coverage part(s) {', '.join(k[:12] for k in stray)} "
                "are not hasPart locations of the passage")
    if not claim.get("challenge"):
        return (f"{passage.get('item')}: the coverage claim has no challenge value (hard rule 5: "
                "nothing leaves the workspace without one)")
    if claim.get("challenge") not in CHALLENGES:
        return (f"{passage.get('item')}: the coverage claim's challenge "
                f"{claim.get('challenge')!r} is not one of {', '.join(sorted(CHALLENGES))}")
    return None


def passage_problem(passage: Mapping[str, Any]) -> str | None:
    """Why a passage row cannot be exported as the record set requires, or None.

    Every record names its passage's ``section`` (the heading path, a list of
    non-empty strings; ``[]`` is the lead) and ``prominence``, so a consumer can
    single out the article's subject passage without a passage table.
    """
    section = passage.get("section")
    if not isinstance(section, (list, tuple)) or not all(isinstance(h, str) and h.strip()
                                                         for h in section):
        return f"{passage.get('item')}: no section heading path to export ({section!r})"
    if passage.get("prominence") not in PROMINENCES:
        return (f"{passage.get('item')}: prominence {passage.get('prominence')!r} is not one of "
                f"{', '.join(sorted(PROMINENCES))}")
    return None


def location_problem(location: Mapping[str, Any]) -> str | None:
    """Why a location's joined index row cannot be a record's lean end, or None.

    A lean end names its declaration's ``module``, its ``path`` (a safe relative
    ``.lean`` path from the repository root) and its ``line`` (>= 1) -- a consumer
    links the source from them; only ``end_line`` is optional.  The index admits a
    module with no path, so a location joined to one is refused here, by name, per
    passage, rather than in the consumer, which refuses the whole set.
    """
    missing = []
    if not isinstance(location.get("module"), str) or not location["module"].strip():
        missing.append("module")
    path = location.get("path")
    parts = str(path).split("/") if isinstance(path, str) else []
    if (not isinstance(path, str) or not path.endswith(".lean") or path.startswith("/")
            or "\\" in path or any(part in ("", ".", "..") for part in parts)):
        missing.append("path (a relative .lean path)")
    line = location.get("line")
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        missing.append("line")
    if not missing:
        return None
    return (f"{location.get('library')}:{location.get('declaration')} has no "
            f"{', '.join(missing)} in the index, and a lean end names its source by them")


def passage_challenge(passage: Mapping[str, Any]) -> str:
    value = passage.get("challenge")
    if not value:
        raise RecordError(f"{passage.get('source_id')} {passage.get('item')}: no challenge value "
                          "(hard rule 5: nothing leaves the workspace without one)")
    return str(value)


def location_record(source: Mapping[str, Any], passage: Mapping[str, Any],
                    location: Mapping[str, Any], library: Mapping[str, Any],
                    universe: Mapping[str, Any], quote: Mapping[str, str], *,
                    span_sha256: str, passage_key: str | None = None,
                    pair_key: str | None = None,
                    bundle: Mapping[str, Any] | None = None) -> dict:
    """One location's record.  ``passage_key``, ``pair_key`` and ``bundle`` are status
    rule 2's (:func:`passage_records`); without them the record is the one it always was."""
    challenge = location.get("challenge")
    if not challenge:
        raise RecordError(f"{passage.get('item')}: location {location.get('declaration')} has no "
                          "challenge value")
    record = {
        "id": record_id(str(source["source_id"]), str(passage["item"])),
        "subject": [subject_end(source, quote, span_sha256=span_sha256,
                                span=passage_span(passage),
                                wikidata=article_qid(source, passage,
                                                     challenge=str(challenge)))],
        "object": [lean_end(library, location)],
        "relation": str(location["relation"]),
        "kind": str(passage["passage_kind"]),
        "extensions": {EXTENSION: _record_ext(
            passage, universe, bundle=bundle, location_key=str(location["location_key"]),
            rank=int(location["rank"]) if location.get("rank") is not None else None,
            confidence=location.get("confidence") or None, passage_key=passage_key,
            pair_key=pair_key)},
    }
    record["extensions"][EXTENSION]["challenge"] = str(challenge)
    if passage.get("status"):
        record["status"] = str(passage["status"])
    if location.get("note"):
        record["note"] = str(location["note"])
    return record


def absence_ends(universe: Mapping[str, Any]) -> list[dict]:
    """The lean ends of a checked absence: one per distinct ``(repo, commit)``, by library id.

    An absence names what was searched, and two universe libraries split from
    one repository at one commit (lean-core's ``Init`` and ``Std``, one root
    each: proposal section 2.3) are one search target, so they are one end; the
    consumer checks the ends against the universe's ``(repo, commit)`` set, each
    pair once.  The first library by id of each pair places it.
    """
    ends: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for lib in sorted(universe["libraries"], key=lambda lib: str(lib["id"])):
        end = lean_end(lib)
        key = (end["uri"], end["version"])
        if key not in seen:
            seen.add(key)
            ends.append(end)
    return ends


def absence_record(source: Mapping[str, Any], passage: Mapping[str, Any],
                   universe: Mapping[str, Any], quote: Mapping[str, str], *,
                   span_sha256: str, receipt: Mapping[str, Any],
                   passage_key: str | None = None) -> dict:
    """The checked absence; ``receipt`` is ``{sha256, queries, candidates}`` of the sidecar."""
    ends = absence_ends(universe)
    if not ends:
        raise RecordError("an absence names the universe's libraries, and it has none")
    absence = passage.get("absence") or {}
    challenge = absence.get("challenge") or passage.get("challenge")
    if not challenge:
        raise RecordError(f"{passage.get('item')}: the absence has no challenge value")
    record = {
        "id": record_id(str(source["source_id"]), str(passage["item"])),
        "subject": [subject_end(source, quote, span_sha256=span_sha256,
                                span=passage_span(passage),
                                wikidata=article_qid(source, passage,
                                                     challenge=str(challenge)))],
        "object": ends,
        "relation": NO_MATCH,
        "status": NOT_FORMALIZED,
        "kind": str(passage["passage_kind"]),
        "extensions": {EXTENSION: _record_ext(passage, universe, receipt=dict(receipt),
                                              passage_key=passage_key)},
    }
    record["extensions"][EXTENSION]["challenge"] = str(challenge)
    return record


def unresolved_record(source: Mapping[str, Any], passage: Mapping[str, Any],
                      universe: Mapping[str, Any], quote: Mapping[str, str], *,
                      span_sha256: str, passage_key: str | None = None) -> dict:
    """An unresolved outcome: ``object: []`` and no status; counted, never not formalized."""
    return {
        "id": record_id(str(source["source_id"]), str(passage["item"])),
        "subject": [subject_end(source, quote, span_sha256=span_sha256,
                                span=passage_span(passage),
                                wikidata=article_qid(source, passage,
                                                     challenge=passage_challenge(passage)))],
        "object": [],
        "kind": str(passage["passage_kind"]),
        "extensions": {EXTENSION: _record_ext(passage, universe, passage_key=passage_key)},
    }


def verbatim_run(text: str, layer: str, words: int = QUOTE_WORDS) -> str | None:
    """The first run of more than ``words`` consecutive words of ``text`` that occurs,
    whitespace collapsed, on the layer -- source text a free-text field would carry
    past the quote cap -- or None."""
    tokens = str(text or "").split()
    if len(tokens) <= words:
        return None
    flat = " ".join(layer.split())
    for i in range(len(tokens) - words):
        run = " ".join(tokens[i:i + words + 1])
        if run in flat:
            return run
    return None


def _free_text(item: str, what: str, text: str, layer: str) -> None:
    run = verbatim_run(text, layer)
    if run is not None:
        raise RecordError(f"{item}: {what} carries {len(run.split())}+ words of the source "
                          f"verbatim ({run[:40]!r}...), past the {QUOTE_WORDS}-word quote cap")


def check_note(item: str, note: Any, layer: str) -> None:
    """Refuse (:class:`RecordError`) a location's note that carries the source past the cap.

    The one definition of the note half of the export's free-text rule: the
    export's :func:`passage_records` and the gate's rule 1 both call it, and the
    receipt half is :func:`receipt_document`, which both call too.
    """
    _free_text(item, "a location's note", str(note or ""), layer)


#: the longest note a downgrade's witness lends a closeMatch, in characters
NOTE_FROM_WITNESS_CHARS = 300
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


#: abbreviations whose full stop ends no sentence of a witness ("i.e. F(x) = ...")
_ABBREVIATIONS = frozenset({"i.e.", "e.g.", "cf.", "resp.", "etc.", "viz.", "vs.", "w.r.t."})
#: the mark of a challenger's recall miss under R-SPECIFIC (references/vocabulary.md section
#: 1.11): the last sentence of its witness on a general card, ``R-SPECIFIC miss: <library>
#: <declaration>.``, which reports a declaration the passage's locations lack and is never a
#: note (:func:`downgrade_note`)
SPECIFIC_MISS_MARK = "R-SPECIFIC miss:"


def is_specific_miss(sentence: str) -> bool:
    """Does a witness sentence carry the R-SPECIFIC recall-miss mark (in any case)?"""
    return SPECIFIC_MISS_MARK.casefold() in str(sentence).casefold()


def witness_sentences(witness: str) -> list[str]:
    """A witness's sentences, whitespace collapsed, each cut at a word at
    :data:`NOTE_FROM_WITNESS_CHARS`.

    A sentence ends at ``.``, ``!`` or ``?`` before whitespace, except after an
    abbreviation (:data:`_ABBREVIATIONS`): the field witness's "-- i.e. F(x) =
    ..." is one sentence, not two.
    """
    text = " ".join(str(witness or "").split())
    sentences: list[str] = []
    start = 0
    for brk in _SENTENCE_END.finditer(text):
        last = text[start:brk.start()].rsplit(" ", 1)[-1].lstrip("([{'\"")
        if last.lower() in _ABBREVIATIONS:
            continue
        sentences.append(text[start:brk.start()])
        start = brk.end()
    if text[start:]:
        sentences.append(text[start:])
    out = []
    for sentence in sentences:
        if len(sentence) > NOTE_FROM_WITNESS_CHARS:
            cut = sentence.rfind(" ", 0, NOTE_FROM_WITNESS_CHARS)
            sentence = (sentence[:cut if cut > 0 else NOTE_FROM_WITNESS_CHARS].rstrip(" ,;:")
                        + "\u2026")
        out.append(sentence)
    return out


def downgrade_note(verdict: dict, layer: str | None = None) -> str:
    """The note of a location a challenge downgraded to ``closeMatch``.

    A ``closeMatch`` names its convention difference in ``note`` (proposal
    section 2.6), and a downgrade from ``exactMatch`` is exactly the challenger
    saying there is one: the verdict's own ``note`` when it gives one, else a
    sentence of its ``witness`` (:func:`witness_sentences`) -- the first one that
    carries no more of the source verbatim than the export's quote cap
    (:func:`verbatim_run` against the passage's ``layer``).  A witness opens
    by quoting the passage as often as not (field case FTC #19a718e1: "The
    passage says only that '<19 words of the article>'"), and a note is exported,
    so that sentence is passed over for the next.  A sentence that carries the
    R-SPECIFIC recall-miss mark (:func:`is_specific_miss`) is never taken: it names
    a declaration the passage lacks, not the convention the record differs by.
    Without a layer, or when every sentence left quotes past the cap, the first of
    them, which the export refuses by name.  Empty only when the verdict says
    nothing, or its witness says only recall misses: the gate refuses both, and the
    export would refuse the record by name.  It lives here, beside
    :func:`check_note`, so the gate's rule 1 computes the same note from the staged
    copy and refuses the one the export would.
    """
    note = " ".join(str(verdict.get("note") or "").split())
    if note:
        return note
    sentences = [s for s in witness_sentences(str(verdict.get("witness") or ""))
                 if not is_specific_miss(s)]
    if not sentences:
        return ""
    if layer:
        for sentence in sentences:
            if verbatim_run(sentence, layer) is None:
                return sentence
    return sentences[0]


def receipt_document(source_id: str, item: str, receipt: Mapping[str, Any],
                     universe: Mapping[str, Any], layer: str = "") -> tuple[str, str, dict]:
    """``(sha256, the sidecar's text, the reference)`` for an absence's search receipt.

    The sidecar keeps each candidate's documented keys only, refuses a query or a
    reason that carries the source past the quote cap, and its sha256 is the sha
    of the exact bytes written -- the name of the file, the record's reference
    and the seal's entry are one value.  The reference's ``queries`` and
    ``candidates`` count the sidecar's two lists, which a consumer checks: a query
    repeated exactly is listed once, in its first place (the gate admits a
    repetition; it counts distinct queries), so the count and the list agree.
    """
    queries = list(dict.fromkeys(str(q) for q in receipt.get("queries") or []))
    candidates = [{k: c[k] for k in RECEIPT_CANDIDATE_KEYS if k in c}
                  for c in receipt.get("candidates") or [] if isinstance(c, dict)]
    for q in queries:
        _free_text(item, "a receipt query", q, layer)
    for c in candidates:
        _free_text(item, "a candidate's reason", str(c.get("reason") or ""), layer)
    body = {"source_id": source_id, "item": item, "universe": str(universe["id"]),
            "universe_sha256": str(universe["sha256"]), "queries": queries,
            "candidates": candidates}
    text = canonical(body) + "\n"
    digest = sha256_text(text)
    return digest, text, {"sha256": digest, "queries": len(queries),
                          "candidates": len(candidates)}


def passage_records(source: Mapping[str, Any], passage: Mapping[str, Any], layer: str,
                    universe: Mapping[str, Any], *, status_rule: int = STATUS_RULE_D1
                    ) -> tuple[list[dict], list[tuple[str, str]]]:
    """Every record of one passage outcome, and the receipt sidecars they reference
    (``(sha256, text)``); a location note that carries the source past the cap is refused.

    ``status_rule`` is the run's (``cf-locate-recordset/1`` for 1, ``/2`` for 2 and 3):
    under status rules 2 and 3 every record carries the ``passage_key``, a location
    record its ``pair_key``, and a coverage claim rides as its bundle
    (:func:`bundle_ext`), each conjunct's text held to the quote cap a note is.  The
    status every record carries is the passage row's, derived by the outcomes under
    the run's rule, so status rule 3 builds the shapes status rule 2 does.
    """
    if type(status_rule) is not int or status_rule not in STATUS_RULES:
        raise RecordError(f"status rule {status_rule!r} names no record-set format")
    start, end = passage_span(passage)
    if end > len(layer):
        raise RecordError(f"{passage.get('item')}: the span [{start}, {end}] leaves the layer "
                          f"(0..{len(layer)})")
    quote = cap_quote(layer, start, end)
    span_sha = sha256_text(layer[start:end])
    libs = {str(lib["id"]): lib for lib in universe["libraries"]}
    records: list[dict] = []
    sidecars: list[tuple[str, str]] = []
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
    why = passage_problem(passage) or coverage_problem(passage, status_rule=status_rule)
    if why:
        raise RecordError(why)
    # status rule 2's revision-free keys and bundle (docs/proposals/statement-cut.md 2.4),
    # each pair key needing its library's repository before the first record is built
    pk: str | None = None
    pairs: dict[str, str] = {}
    bundle = None
    if status_rule >= STATUS_RULE_BUNDLE:
        for loc in locations:
            if loc["library"] not in libs:
                raise RecordError(f"{passage['item']}: library {loc['library']} is not in the "
                                  "universe")
        pk = passage_key(source.get("pageid"), span_sha)
        pairs = {str(loc["location_key"]): pair_key(pk, str(libs[loc["library"]]["repo"]),
                                                    str(loc["declaration"]))
                 for loc in locations}
        claim = passage.get("coverage")
        if claim:
            bundle = bundle_ext(passage, claim, passage_key=pk, pair_keys=pairs)
            for conjunct in bundle["conjuncts"]:
                _free_text(str(passage["item"]), f"conjunct {conjunct['id']}'s text",
                           conjunct["text"], layer)
    for loc in locations:
        if loc["library"] not in libs:
            raise RecordError(f"{passage['item']}: library {loc['library']} is not in the universe")
        if loc.get("relation") == CLOSE_MATCH and not str(loc.get("note") or "").strip():
            raise RecordError(f"{passage['item']}: the closeMatch {loc['library']}:"
                              f"{loc['declaration']} names no convention difference in its note")
        check_note(str(passage["item"]), loc.get("note"), layer)
        why = location_problem(loc)
        if why:
            raise RecordError(f"{passage['item']}: {why}")
        records.append(location_record(source, passage, loc, libs[loc["library"]], universe,
                                       quote, span_sha256=span_sha, passage_key=pk,
                                       pair_key=pairs.get(str(loc["location_key"])),
                                       bundle=bundle))
    if outcome == "absent":
        digest, text, ref = receipt_document(str(source["source_id"]), str(passage["item"]),
                                             (passage.get("absence") or {}).get("receipt")
                                             or passage.get("receipt") or {}, universe, layer)
        sidecars.append((digest, text))
        records.append(absence_record(source, passage, universe, quote, span_sha256=span_sha,
                                      receipt=ref, passage_key=pk))
    elif not locations:
        records.append(unresolved_record(source, passage, universe, quote,
                                         span_sha256=span_sha, passage_key=pk))
    return records, sidecars


def check(record: Mapping[str, Any]) -> list[str]:
    """The vendored Literature Links checker's problems for one record; empty when valid."""
    return _ll.validate(dict(record))


def check_all(records: Iterable[Mapping[str, Any]]) -> list[str]:
    out = []
    for record in records:
        out.extend(f"{record.get('id')}: {p}" for p in check(record))
    return out
