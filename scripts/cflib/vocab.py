"""The ONE vocabulary. Documented in references/vocabulary.md.

New code, consolidating the five drifting vocabularies the design records
(match verdict -> finding kind -> challenge classification -> ledger verdict
-> viewer verdict).  Every stage imports its enums
from here: a value that is not in these sets is a gate failure, never a new
convention.
"""
from __future__ import annotations

from typing import Iterable

from .errors import CfError

# --- match verdicts (what a differential/attack worker decides about an item)
AGREE = "AGREE"
CONVENTION_ONLY = "CONVENTION_ONLY"
CONTENT_DELTA = "CONTENT_DELTA"
SINGLE_SOURCE_OK = "SINGLE_SOURCE_OK"
SINGLE_SOURCE_DELTA = "SINGLE_SOURCE_DELTA"
NO_DEFECT_FOUND = "NO_DEFECT_FOUND"
MATCH_VERDICTS = frozenset({
    AGREE, CONVENTION_ONLY, CONTENT_DELTA,
    SINGLE_SOURCE_OK, SINGLE_SOURCE_DELTA, NO_DEFECT_FOUND,
})

#: v1 spellings seen in the 2,672-file baseline -> v2 (convert_v1.py).
#: The vac run wrote NO_WEAKNESS_FOUND where hunt wrote NO_DEFECT_FOUND.
MATCH_VERDICT_ALIASES = {
    "NO_WEAKNESS_FOUND": NO_DEFECT_FOUND,
    "NO_DEFECT": NO_DEFECT_FOUND,
    "NO_DEFECTS_FOUND": NO_DEFECT_FOUND,
    "MATCH": AGREE,
    "CONVENTION": CONVENTION_ONLY,
    "CONTENT": CONTENT_DELTA,
}

# --- finding kinds
CONTENT = "CONTENT"
VACUITY = "VACUITY"
PROXY = "PROXY"
SUSPECTED_ERRATA = "SUSPECTED_ERRATA"
COVERAGE = "COVERAGE"
PAGE_ANCHOR = "PAGE_ANCHOR"
FINDING_KINDS = frozenset({CONTENT, VACUITY, PROXY, SUSPECTED_ERRATA, COVERAGE, PAGE_ANCHOR})

#: PAGE_ANCHOR is produced by pair/anchor_check.py only. An LLM that emits it
#: is failing the kinds gate: 22 of 40 batch-1 differential findings were
#: pagination noise (the kinds gate; references/findings-schema-v2.md S7).
MECHANICAL_ONLY_KINDS = frozenset({PAGE_ANCHOR})

# --- run modes and the kinds each mode may emit
DIFFERENTIAL = "differential"
SINGLE_SOURCE = "single-source"
ATTACK_VAC = "attack:vac"
ATTACK_HUNT = "attack:hunt"
RUN_MODES = frozenset({DIFFERENTIAL, SINGLE_SOURCE, ATTACK_VAC, ATTACK_HUNT})
ATTACK_MODES = frozenset({ATTACK_VAC, ATTACK_HUNT})

KINDS_BY_MODE: dict[str, frozenset] = {
    DIFFERENTIAL: frozenset({CONTENT, VACUITY, SUSPECTED_ERRATA, COVERAGE}),
    SINGLE_SOURCE: frozenset({CONTENT, VACUITY, SUSPECTED_ERRATA, COVERAGE}),
    ATTACK_VAC: frozenset({CONTENT, VACUITY, SUSPECTED_ERRATA}),
    ATTACK_HUNT: frozenset({CONTENT, VACUITY, SUSPECTED_ERRATA}),
}

#: Per-kind evidence matrix (the design critique: "A per-kind gate matrix is
#: needed (PROXY: bridge field + closure path as evidence; COVERAGE: item +
#: items.json page; CONTENT/VACUITY: quote + witness)").  Values are the
#: finding fields the mechanical gate requires for that kind.
EVIDENCE_BY_KIND: dict[str, tuple[str, ...]] = {
    CONTENT: ("item", "book_quote", "unfaithful_decls"),
    VACUITY: ("item", "book_quote", "unfaithful_decls"),
    SUSPECTED_ERRATA: ("item", "book_quote"),
    COVERAGE: ("item",),
    PROXY: ("item", "unfaithful_decls", "bridge"),
    PAGE_ANCHOR: ("item", "page_anchor_found", "page_anchor_expected"),
}

#: Kinds whose findings must carry a structured witness in attack mode.
WITNESS_KINDS = frozenset({CONTENT, VACUITY})

# --- severities
SEVERITIES = ("low", "medium", "high")
SEVERITY_SET = frozenset(SEVERITIES)

# --- ledger / challenge verdicts
CONFIRMED_CONTENT = "CONFIRMED_CONTENT"
ATTRIBUTION_GAP = "ATTRIBUTION_GAP"
CONVENTION = "CONVENTION"
ERRATA = "ERRATA"
REFUTED = "REFUTED"
UNSURE = "UNSURE"
LEDGER_VERDICTS = frozenset({CONFIRMED_CONTENT, ATTRIBUTION_GAP, CONVENTION, ERRATA, REFUTED, UNSURE})

#: A CONVENTION verdict must carry a written recovery argument
#: (refuted.adam.jsonl row 2); the ledger validator enforces it.
VERDICTS_REQUIRING_ARGUMENT = frozenset({CONVENTION, ERRATA})

# --- resolutions
RESOLUTION_REFUTED = "refuted"
RESOLUTION_ARGUED_CORRECT = "argued_correct"
RESOLUTION_OUT_OF_SCOPE = "out_of_scope"
STATIC_RESOLUTIONS = frozenset({RESOLUTION_REFUTED, RESOLUTION_ARGUED_CORRECT, RESOLUTION_OUT_OF_SCOPE})
#: prefixed resolutions: "fixed:PR#8", "in_progress:fix/differential-batch2"
RESOLUTION_PREFIXES = ("fixed:", "in_progress:")

# --- routing status (_meta.status on routed files)
UNCHALLENGED = "UNCHALLENGED"
CHALLENGED = "CHALLENGED"
ROUTE_STATUSES = frozenset({UNCHALLENGED, CHALLENGED})

# --- re-check status (`cf recheck`: a pinned finding against the current repo).
#: These are facts about the CODE, never verdicts: `changed` and `gone` say a
#: human must look again, not that a finding is closed.  `unpinned` is the honest
#: answer for an imported corpus and for every row written before the pin
#: existed -- it is reported rather than guessed at, so re-checking a legacy
#: ledger cannot move an adjudicated number.  Ordered worst-last for display.
RECHECK_UNCHANGED = "unchanged"
RECHECK_UNPINNED = "unpinned"
RECHECK_CHANGED = "changed"
RECHECK_GONE = "gone"
RECHECK_STATUSES = (RECHECK_UNCHANGED, RECHECK_UNPINNED, RECHECK_CHANGED, RECHECK_GONE)
RECHECK_STATUS_SET = frozenset(RECHECK_STATUSES)

# --- viewer layer (publish-verdicts; measure_theory only in v1)
VIEWER_VERDICTS = frozenset({"faithful", "convention", "attribution_gap", "proxy", "deviation"})
VIEWER_STATUS = frozenset({"unconfirmed", "contested", "human_confirmed", "fixed"})
LEDGER_TO_VIEWER = {
    CONFIRMED_CONTENT: "deviation",
    ATTRIBUTION_GAP: "attribution_gap",
    CONVENTION: "convention",
    ERRATA: "convention",
    REFUTED: "faithful",
    UNSURE: "unconfirmed",
}
KIND_TO_VIEWER = {PROXY: "proxy"}

# --- sources (docs/design/archive/multi-source-model.md S1, S2)
#: audited = a companion is checked against it; reference = prepared only so the
#: page map to a sibling edition can be checked, and audited by nobody.
SOURCE_AUDITED = "audited"
SOURCE_REFERENCE = "reference"
SOURCE_ROLES = frozenset({SOURCE_AUDITED, SOURCE_REFERENCE})

#: What a registry licence note permits. `none` means a download is refused even
#: with --download; nothing is EVER redistributed, whatever this says.
LICENCE_FETCH = frozenset({"none", "author_site", "open"})

#: The repo signals `cf onboard` reads (cflib.discovery, S4.3). All of them run
#: for every adapter kind: a repo may carry a signal that "belongs" to another.
DISCOVERY_SIGNALS = ("formalization_yaml", "ledger_source", "official_source_json",
                     "graph_json", "prose", "filesystem", "citation")

#: Where a located PDF was found, in search order (S4.4).
PDF_FOUND_IN = ("repo_declared", "repo_cache", "workspace_store", "library_path",
                "download", "none")

#: How a page offset was arrived at.
OFFSET_METHODS = frozenset({"running_head_mode", "declared", "manual", "registry", "unknown"})

# --- adapter kinds and policies (Stage 0 contract)
ADAPTER_KINDS = frozenset({"docstring_tags", "attribution_table", "blueprint_tex",
                           "ledger_nodes", "custom"})

#: Adapter kinds v1 actually IMPLEMENTS.  ``ADAPTER_KINDS`` is the vocabulary --
#: what an adapter.yaml may legally say -- and it is deliberately wider, because
#: ``references/onboarding-contract.md`` specifies ``ledger_nodes`` and ``custom``
#: without shipping them.  ``cf onboard`` refuses a kind that is not in this set
#: BEFORE it writes anything; otherwise it registered a companion nothing could
#: parse and the next ``cf extract`` failed for the whole workspace.
#: ``tests/test_cflib`` asserts this equals ``adapters.api.registry()``.
IMPLEMENTED_ADAPTER_KINDS = frozenset({"docstring_tags", "attribution_table", "blueprint_tex"})
GRANULARITIES = frozenset({"item", "node"})

#: Which artifact OF THE COMPANION a statement row was read from.  A companion
#: whose corpus is built from an exported attribution table has two, and they
#: do not agree: ``table`` is what the export says, ``lean_scan`` is a
#: declaration the Lean sources claim for a book item (a docstring naming one,
#: or a named section the convention files statements under) that the export
#: omits.  A finding must be able to say which, because "no declaration states
#: this" read off a ``table``-only corpus is a claim about the export and not
#: about the repository -- the false COVERAGE the cross-check exists to stop.
ROW_PROVENANCE = frozenset({"table", "lean_scan", "docstring", "blueprint"})

#: What the cross-check may do with a declaration the table omits.  There is no
#: value that passes quietly: ``union`` publishes the omission as a row and
#: warns, ``report`` warns without publishing, and ``off`` is a deviation a
#: companion states in its own adapter.yaml rather than one the code assumes --
#: it runs no scan, and warns on every extraction that none ran.  An unquoted
#: ``off`` is YAML false and is read as ``off``.
CROSS_CHECK_POLICIES = frozenset({"union", "report", "off"})
WRITE_POLICIES = ("read_only", "branch_only", "branch_and_pr")
WRITE_POLICY_SET = frozenset(WRITE_POLICIES)
ANCHOR_CONVENTIONS = frozenset({"docstring", "ledger", "none"})
SORRY_CLASSES = frozenset({"exercise", "gap", "none"})
BRIDGE_KINDS = frozenset({"proved", "fiat", "none"})

# --- annotation documents (schema/annotations-v1.schema.json, docs/annotations.md)
#: The enums of the lean-companion annotation format.  The schema file repeats
#: them because it is data a non-Python consumer reads; ``tests/
#: test_annotations_schema.py`` fails when the two disagree, so this block stays
#: the one definition.  ``VIEWER_VERDICTS`` / ``VIEWER_STATUS`` above are reused
#: for a verdict record, and ``SORRY_CLASSES`` for a declaration's ``sorry``.
ANNOTATION_SCHEMA = "lean-companion/annotations"
#: ``"<major>.<minor>"``.  Readers match the major version; a writer declares the
#: minor it used (docs/annotations.md, section 9).
ANNOTATION_SCHEMA_VERSION = "1.0"
ANNOTATION_SCHEMA_MAJOR = 1
#: ``other`` names any other medium by its IANA ``media_type`` (markdown, EPUB,
#: video); the four named kinds are presets for the common ones.
ANNOTATION_SOURCE_KINDS = frozenset({"pdf", "web", "tex", "lean", "other"})
#: ``site``: one web source for a whole rendered site, each location naming its
#: page with a ``SitePageSelector``.  Only a ``web`` source may be a site.
ANNOTATION_SOURCE_SCOPES = frozenset({"page", "site"})
ANNOTATION_LINK_ROLES = frozenset({"key", "attributed", "support", "example", "helper"})
#: The documented core of a link's ``relation``: how the declaration relates to
#: the item's text.  The field is an open string; a value outside this set
#: passes with a warning (WikiLean's ``match_kind`` uses these five).
ANNOTATION_LINK_RELATIONS = frozenset({"exact", "generalization", "special_case", "invocation",
                                       "related"})
#: What an item's links do NOT show on their own.  An item whose links carry no
#: ``key`` link must say which of these it is (docs/annotations.md, invariant I5).
#: Falling back to the memberships of the item's clusters is not a coverage
#: class: it is the separate boolean ``item.cluster_fallback``, so an item keeps
#: the curation's class (``example_only`` ...) and the fallback together.
ANNOTATION_COVERAGE = frozenset({"formalized", "partial", "not_formalized", "out_of_scope",
                                 "support_only", "example_only"})
#: The discriminator of an item record.
ANNOTATION_ITEM_FORMS = frozenset({"numbered", "passage"})
#: How an item id was formed (docs/annotations.md, section 5.2).
ANNOTATION_ITEM_ID_SOURCES = frozenset({"label", "quote_hash", "page_title_hash", "declared"})
#: Whose words an item's ``kind`` or ``title`` are.
ANNOTATION_TEXT_ORIGINS = frozenset({"source", "producer"})
ANNOTATION_PAGE_BASES = frozenset({"printed", "physical"})
#: Where a page came from: the thing's own anchor, a cross-reference to it, or
#: inferred from a neighbour (statements.jsonl ``anchor_source``, less ``none``).
ANNOTATION_PAGE_ANCHORS = frozenset({"own", "crossref", "inferred"})
#: ``field`` and ``projection`` belong to a ``structure`` or ``class`` named by
#: the declaration's ``parent``.
ANNOTATION_LEAN_KINDS = frozenset({"theorem", "lemma", "def", "abbrev", "structure", "class",
                                   "inductive", "instance", "example", "opaque", "axiom",
                                   "field", "projection"})
#: How a declaration id was formed (docs/annotations.md, section 5.1).
ANNOTATION_ID_SOURCES = frozenset({"name", "private", "statement_hash", "declared"})
#: Formalization artifacts that are not declarations.
ANNOTATION_ARTIFACT_KINDS = frozenset({"module_doc", "check", "figure", "other"})
#: A cluster member's role, as a companion's graph (a ledger) records it.
ANNOTATION_MEMBERSHIP_ROLES = frozenset({"primary", "support", "consumer"})
ANNOTATION_UNATTRIBUTED_CLAIMS = frozenset({"unattributed", "api", "infrastructure"})
ANNOTATION_PROVENANCE_ACTORS = frozenset({"author", "agent", "human", "tool"})
ANNOTATION_PROVENANCE_METHODS = frozenset({"docstring_tag", "curation", "blueprint_macro",
                                           "lean_scan", "layout_extraction", "text_match",
                                           "manual", "agent_annotation", "pipeline",
                                           "adjudication", "site_index"})
VIEWER_SEVERITIES = frozenset({"none", "low", "medium", "high"})
#: ``resolution.state`` of a verdict: the structured form of ``check_resolution``'s
#: strings, plus ``open`` for a verdict nobody has resolved.  ``resolution`` is
#: optional; a verdict without one is open.
ANNOTATION_RESOLUTION_STATES = frozenset({"open", "fixed", "in_progress", "refuted",
                                          "argued_correct", "out_of_scope"})
ANNOTATION_DESTINATION_SITES = frozenset({"verso_literate", "doc_gen4", "blueprint", "other"})
#: ``ordinal_assigned`` records which declaration took a ``~k`` statement-hash
#: ordinal, by its file; without it a ``~k`` id is refused, and so is a live id
#: whose latest record names another file (docs/annotations.md, section 5.1).
ANNOTATION_CHANGELOG_EVENTS = frozenset({"born", "retired", "restated", "renamed",
                                         "ordinal_assigned"})
ANNOTATION_CHANGELOG_SUBJECTS = frozenset({"declaration", "item", "location", "artifact"})
ANNOTATION_CHANGELOG_REASONS = frozenset({"statement_changed", "removed", "renamed", "moved",
                                          "other"})
#: The typed relations between items (``item.relations``).
ANNOTATION_ITEM_RELATIONS = frozenset({"same_as", "part_of", "near"})
#: W3C Web Annotation selector classes, plus the two lean-companion extensions
#: (``PdfRegionSelector``, ``SitePageSelector``).
ANNOTATION_SELECTOR_TYPES = frozenset({"FragmentSelector", "TextQuoteSelector",
                                       "TextPositionSelector", "DataPositionSelector",
                                       "CssSelector", "XPathSelector", "SvgSelector",
                                       "RangeSelector", "PdfRegionSelector",
                                       "SitePageSelector"})
#: ``conformsTo`` IRIs with a grammar check.  The field is open: any other IRI
#: passes with a warning.  They are identifiers, not links: nothing fetches them.
#: RFC 8118 obsoletes RFC 3778, which the W3C Recommendation's table names, so
#: the two are synonyms here.
CONFORMS_TO_PDF = "http://tools.ietf.org/rfc/rfc8118"
CONFORMS_TO_PDF_RFC3778 = "http://tools.ietf.org/rfc/rfc3778"
CONFORMS_TO_TEXT = "http://tools.ietf.org/rfc/rfc5147"
CONFORMS_TO_HTML = "http://tools.ietf.org/rfc/rfc3236"
CONFORMS_TO_MEDIA = "http://www.w3.org/TR/media-frags/"
ANNOTATION_CONFORMS_TO = frozenset({CONFORMS_TO_PDF, CONFORMS_TO_PDF_RFC3778, CONFORMS_TO_TEXT,
                                    CONFORMS_TO_HTML, CONFORMS_TO_MEDIA})

#: The id grammar for anonymous declarations, used by scripts/annotations/ids.py
#: (which writes ids) and scripts/annotations/validate.py (which checks them).
#: A statement-hash id is ``<keyword>:<12 hex>`` with an optional ``~k``
#: (k >= 2).  A declared id is ``<keyword>:<slug>``: letters of either case,
#: digits, ``.``, ``_`` and ``-``, starting with a letter or digit, and never a
#: slug that is 12 lowercase hex digits (with or without ``~k``), which is
#: reserved for statement-hash ids.
ANONYMOUS_KEYWORDS = ("example", "instance")
HASH_ID_PATTERN = r"(?P<kw>example|instance):(?P<hex>[0-9a-f]{12})(?:~(?P<k>[2-9]|[1-9][0-9]+))?"
DECLARED_SLUG_PATTERN = r"(?![0-9a-f]{12}(?:~[0-9]+)?(?![A-Za-z0-9._~-]))[A-Za-z0-9][A-Za-z0-9._-]*"
DECLARED_ID_PATTERN = r"(?P<kw>example|instance):(?P<slug>" + DECLARED_SLUG_PATTERN + r")"
#: The two mechanical passage-id forms (docs/annotations.md, section 5.2); a
#: declared item id may not take either shape.
PASSAGE_QUOTE_ID_PATTERN = r"passage-[0-9a-f]{8}"
PASSAGE_PAGE_ID_PATTERN = r"passage-p[1-9][0-9]*-[0-9a-f]{8}"

#: Lean keywords whose declaration STATES a notion, as opposed to asserting
#: something about one.  The definition-coverage split (stated / bridge-only /
#: missing) turns on this and nothing else, and it is written down ONCE here
#: because ``pair/def_coverage.py`` and ``detect/def_coverage.py`` both write
#: ``pairs/<source_id>/def-coverage.<name>.json``: they used different sets
#: (``instance`` was in one and not the other), so the two agreed only by luck.
DEFINITIONAL_KEYWORDS: tuple[str, ...] = (
    "def", "abbrev", "structure", "class", "inductive", "instance",
)

#: ``kind`` values an adapter may put on a statement row that mean the same thing
DEFINITIONAL_KINDS = frozenset(set(DEFINITIONAL_KEYWORDS) | {"opaque"})


def check(value: str, allowed: Iterable[str], field: str) -> str:
    """Return ``value`` if it is in ``allowed``; otherwise raise CfError."""
    allowed = set(allowed)
    if value not in allowed:
        raise CfError(f"invalid {field}: {value!r}",
                      hint=f"allowed: {', '.join(sorted(allowed))}")
    return value


def canonical_match_verdict(value: str) -> str:
    """Map a v1/alias spelling onto the v2 vocabulary."""
    v = (value or "").strip().upper()
    v = MATCH_VERDICT_ALIASES.get(v, v)
    return check(v, MATCH_VERDICTS, "match verdict")


def check_resolution(value: str) -> str:
    """``fixed:PR#8`` | ``in_progress:<branch>`` | refuted | argued_correct | out_of_scope."""
    if value in STATIC_RESOLUTIONS:
        return value
    for prefix in RESOLUTION_PREFIXES:
        if value.startswith(prefix) and len(value) > len(prefix):
            return value
    raise CfError(f"invalid resolution: {value!r}",
                  hint="one of fixed:PR#N, in_progress:<branch>, refuted, argued_correct, out_of_scope")


def kinds_for_mode(mode: str) -> frozenset:
    """The finding kinds a run in ``mode`` may emit (the kinds gate)."""
    check(mode, RUN_MODES, "run mode")
    return KINDS_BY_MODE[mode]


def witness_required(mode: str, kind: str) -> bool:
    """The witness gate: attack modes require a witness on CONTENT/VACUITY."""
    return mode in ATTACK_MODES and kind in WITNESS_KINDS
