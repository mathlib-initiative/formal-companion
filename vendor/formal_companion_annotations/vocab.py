"""The vocabulary of the annotation format: the enums, the id grammar, the IRIs.

Extracted from the formal-companion skill's ``scripts/cflib/vocab.py``:
its "annotation documents" block, verbatim but for the paths its comments
cite, and the few sets from the skill's other sections that the format shares
with it.  Only the constants the format needs are here.  Documented in
docs/vocabulary.md, which defines every value and what it means.

The format owns every value it uses, the shared sets included: a change to
one is made here first, as a new version of the format, and producers (the
skill among them) conform.

The schema file repeats every enum because it is data a non-Python consumer
reads; ``tests/test_annotations_schema.py`` walks every ``enum`` in the schema
and fails when one has no set here or the two disagree, so this module stays
the one definition.
"""
from __future__ import annotations

#: The stable public API of this module (README.md, "Public API"): exactly these
#: names.  Anything else, underscored or not, is internal and may change in any release.
#: Every constant: the vocabulary is the format's, and all of it is public.
__all__ = [
    "ANNOTATION_ARTIFACT_KINDS", "ANNOTATION_CHANGELOG_EVENTS", "ANNOTATION_CHANGELOG_REASONS",
    "ANNOTATION_CHANGELOG_SUBJECTS", "ANNOTATION_CONFORMS_TO", "ANNOTATION_COORDINATE_ORIGINS",
    "ANNOTATION_COORDINATE_UNITS", "ANNOTATION_COORDINATE_X_AXES",
    "ANNOTATION_COORDINATE_Y_AXES", "ANNOTATION_COVERAGE",
    "ANNOTATION_DESTINATION_RESOLUTIONS", "ANNOTATION_DESTINATION_SITES",
    "ANNOTATION_ID_SOURCES", "ANNOTATION_ITEM_FORMS", "ANNOTATION_ITEM_ID_SOURCES",
    "ANNOTATION_ITEM_RELATIONS", "ANNOTATION_LEAN_KINDS", "ANNOTATION_LINK_RELATIONS",
    "ANNOTATION_LINK_ROLES", "ANNOTATION_MEMBERSHIP_ROLES", "ANNOTATION_ORIGIN_Y_AXIS",
    "ANNOTATION_PAGE_ANCHORS", "ANNOTATION_PAGE_BASES", "ANNOTATION_PAGE_BOXES",
    "ANNOTATION_PROVENANCE_ACTORS", "ANNOTATION_PROVENANCE_METHODS",
    "ANNOTATION_RESOLUTION_STATES", "ANNOTATION_SCHEMA", "ANNOTATION_SCHEMA_MAJOR",
    "ANNOTATION_SCHEMA_VERSION", "ANNOTATION_SELECTOR_TYPES", "ANNOTATION_SOURCE_KINDS",
    "ANNOTATION_SOURCE_SCOPES", "ANNOTATION_TEXT_CASE", "ANNOTATION_TEXT_ORIGINS",
    "ANNOTATION_TEXT_UNICODE", "ANNOTATION_TEXT_WHITESPACE", "ANNOTATION_UNATTRIBUTED_CLAIMS",
    "ANONYMOUS_KEYWORDS", "CONFORMS_TO_HTML", "CONFORMS_TO_MEDIA", "CONFORMS_TO_PDF",
    "CONFORMS_TO_PDF_RFC3778", "CONFORMS_TO_TEXT", "DECLARED_ID_PATTERN",
    "DECLARED_SLUG_PATTERN", "FINDING_KINDS", "HASH_ID_PATTERN", "LEDGER_VERDICTS",
    "PASSAGE_PAGE_ID_PATTERN", "PASSAGE_QUOTE_ID_PATTERN", "RESOLUTION_PREFIXES",
    "SORRY_CLASSES", "SOURCE_ROLES", "STATIC_RESOLUTIONS", "VIEWER_SEVERITIES",
    "VIEWER_STATUS", "VIEWER_VERDICTS",
]

# --- sets the format shares with the formal-companion vocabulary
#: First written for the skill's own stages (findings, the ledger, the viewer,
#: sources, sorry classes).  The format owns them: docs/vocabulary.md section 2
#: defines each value and its meaning, and a producer's copy (the skill keeps
#: one in its cflib.vocab) must equal these.  They are written out as literals
#: here so this package imports nothing from the skill.
#: A verdict record's ``finding_kind`` (the skill's vocabulary section 1.2).
FINDING_KINDS = frozenset({"CONTENT", "VACUITY", "PROXY", "SUSPECTED_ERRATA", "COVERAGE",
                           "PAGE_ANCHOR"})
#: A verdict record's ``ledger_verdict`` (section 1.4).
LEDGER_VERDICTS = frozenset({"CONFIRMED_CONTENT", "ATTRIBUTION_GAP", "CONVENTION", "ERRATA",
                             "REFUTED", "UNSURE"})
#: The skill's resolutions (section 1.5), from which ``ANNOTATION_RESOLUTION_STATES``
#: below is derived: the static ones, and the prefixes of ``fixed:PR#8`` and
#: ``in_progress:<branch>``.
STATIC_RESOLUTIONS = frozenset({"refuted", "argued_correct", "out_of_scope"})
RESOLUTION_PREFIXES = ("fixed:", "in_progress:")
#: A source's ``role`` (section 1.6).
SOURCE_ROLES = frozenset({"audited", "reference"})
#: A verdict record's ``verdict``, each ``declarations[].verdict``, its
#: ``severity`` and its ``status`` (the viewer layer, section 1.7).
VIEWER_VERDICTS = frozenset({"faithful", "convention", "attribution_gap", "proxy", "deviation"})
VIEWER_STATUS = frozenset({"unconfirmed", "contested", "human_confirmed", "fixed"})
VIEWER_SEVERITIES = frozenset({"none", "low", "medium", "high"})
#: A declaration's ``sorry``.
SORRY_CLASSES = frozenset({"exercise", "gap", "none"})

# --- annotation documents (schema/annotations-v1.schema.json, docs/annotations.md)
#: The enums of the formal-companion annotation format.  The schema file repeats
#: them because it is data a non-Python consumer reads; ``tests/
#: test_annotations_schema.py`` walks every ``enum`` in the schema and fails when
#: one has no set here or the two disagree, so this block stays the one
#: definition.  Sets from other sections are reused: ``VIEWER_VERDICTS`` (a
#: verdict's ``verdict`` and each ``declarations[].verdict``), ``VIEWER_SEVERITIES``
#: and ``VIEWER_STATUS`` for a verdict record, ``LEDGER_VERDICTS`` and
#: ``FINDING_KINDS`` for its ``ledger_verdict`` and ``finding_kind``,
#: ``SOURCE_ROLES`` for a source's ``role`` and ``SORRY_CLASSES`` for a
#: declaration's ``sorry``.
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
#: the item's text.  The field is an open lower-case snake_case token (the
#: schema's pattern); a token outside this set passes with a warning
#: (WikiLean's ``match_kind`` uses these five).
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
#: ``resolution.state`` of a verdict: the structured form of ``check_resolution``'s
#: strings (section 1.5, derived here so a new resolution reaches the format),
#: plus ``open`` for a verdict nobody has resolved.  ``resolution`` is optional; a
#: verdict without one is open.
ANNOTATION_RESOLUTION_STATES = frozenset({"open"} | STATIC_RESOLUTIONS
                                         | {p.rstrip(":") for p in RESOLUTION_PREFIXES})
ANNOTATION_DESTINATION_SITES = frozenset({"verso_literate", "doc_gen4", "blueprint", "other"})
#: A destination's ``resolution``: ``exact`` needs a ``fragment``, ``fallback`` a
#: ``fallback_label``.
ANNOTATION_DESTINATION_RESOLUTIONS = frozenset({"exact", "fallback"})
#: A ``PdfRegionSelector``'s ``coordinate_space``.  ``top_left`` goes with
#: ``y_axis`` ``down`` and ``bottom_left`` with ``up`` (ANNOTATION_ORIGIN_Y_AXIS);
#: ``normalized`` coordinates are fractions of the page box, ``pt`` are points.
ANNOTATION_COORDINATE_ORIGINS = frozenset({"top_left", "bottom_left"})
ANNOTATION_COORDINATE_UNITS = frozenset({"normalized", "pt"})
ANNOTATION_COORDINATE_X_AXES = frozenset({"right"})
ANNOTATION_COORDINATE_Y_AXES = frozenset({"down", "up"})
ANNOTATION_ORIGIN_Y_AXIS = {"top_left": "down", "bottom_left": "up"}
ANNOTATION_PAGE_BOXES = frozenset({"MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox"})
#: A source's ``text_layer``: how quotes and offsets on it are matched
#: (docs/annotations.md, section 4.6).
ANNOTATION_TEXT_UNICODE = frozenset({"none", "nfc", "nfkc"})
ANNOTATION_TEXT_WHITESPACE = frozenset({"exact", "collapse", "ignore"})
ANNOTATION_TEXT_CASE = frozenset({"sensitive", "insensitive"})
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

#: The id grammar for anonymous declarations, used by ids.py (which writes ids)
#: and validate.py (which checks them).
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
