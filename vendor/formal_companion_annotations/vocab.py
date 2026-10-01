"""The vocabulary of the annotation format: the enums, the id grammar, the IRIs.

Every closed set of values the format uses is defined here, once, and
documented value by value in docs/vocabulary.md.  The schema file repeats
every enum because it is data a non-Python consumer reads;
``tests/test_annotations_schema.py`` walks every ``enum`` in the schema and
fails when one has no set here or the two disagree, so this module stays the
one definition.

The format owns every value it uses.  A change to a set is a change to the
format: a new value is a new minor version at least, and a value removed or
given another meaning is a new major version (docs/annotations.md, section
12).  A producer that writes a value this module does not list writes an
invalid document.
"""
from __future__ import annotations

#: The stable public API of this module: exactly these names.  Anything else,
#: underscored or not, is internal and may change in any release.  Every
#: constant is listed: the vocabulary is the format's, and all of it is public.
__all__ = [
    "ANNOTATION_ARTIFACT_KINDS", "ANNOTATION_CHANGELOG_EVENTS", "ANNOTATION_CHANGELOG_REASONS",
    "ANNOTATION_CHANGELOG_SUBJECTS", "ANNOTATION_CONFORMS_TO", "ANNOTATION_COORDINATE_ORIGINS",
    "ANNOTATION_COORDINATE_UNITS", "ANNOTATION_COORDINATE_X_AXES",
    "ANNOTATION_COORDINATE_Y_AXES", "ANNOTATION_COVERAGE",
    "ANNOTATION_DESTINATION_RESOLUTIONS", "ANNOTATION_DESTINATION_SITES",
    "ANNOTATION_ID_SOURCES", "ANNOTATION_ITEM_FORMS", "ANNOTATION_ITEM_ID_SOURCES",
    "ANNOTATION_ITEM_RELATIONS", "ANNOTATION_LEAN_KINDS", "ANNOTATION_LINK_RELATIONS",
    "ANNOTATION_LINK_ROLES", "ANNOTATION_MEMBER_PARENT_KINDS", "ANNOTATION_MEMBERSHIP_ROLES",
    "ANNOTATION_ORIGIN_Y_AXIS", "ANNOTATION_PAGE_ANCHORS", "ANNOTATION_PAGE_BASES",
    "ANNOTATION_PAGE_BOXES", "ANNOTATION_PROFILE_VERSIONS", "ANNOTATION_PROFILES",
    "ANNOTATION_PROVENANCE_ACTORS", "ANNOTATION_PROVENANCE_METHODS",
    "ANNOTATION_RESOLUTION_STATES", "ANNOTATION_SCHEMA", "ANNOTATION_SCHEMA_ID_BASE",
    "ANNOTATION_SCHEMA_MAJOR", "ANNOTATION_SCHEMA_VERSION", "ANNOTATION_SELECTOR_TYPES",
    "ANNOTATION_SEVERITIES", "ANNOTATION_SORRY_CLASSES", "ANNOTATION_SOURCE_KINDS",
    "ANNOTATION_SOURCE_ROLES", "ANNOTATION_SOURCE_SCOPES", "ANNOTATION_TEXT_CASE",
    "ANNOTATION_TEXT_ORIGINS", "ANNOTATION_TEXT_UNICODE", "ANNOTATION_TEXT_WHITESPACE",
    "ANNOTATION_UNATTRIBUTED_CLAIMS", "ANNOTATION_VERDICT_STATUSES", "ANNOTATION_VERDICTS",
    "ANNOTATION_XREF_DATABASES", "ANONYMOUS_KEYWORDS", "CONFORMS_TO_HTML", "CONFORMS_TO_MEDIA",
    "CONFORMS_TO_PDF", "CONFORMS_TO_PDF_RFC3778", "CONFORMS_TO_TEXT", "DECLARED_ID_PATTERN",
    "DECLARED_SLUG_PATTERN", "HASH_ID_PATTERN", "PASSAGE_PAGE_ID_PATTERN",
    "PASSAGE_QUOTE_ID_PATTERN",
]

# --- the format and its versions (docs/annotations.md, sections 4 and 12)
#: The root ``schema`` value: the format identifier.  No other value is accepted.
ANNOTATION_SCHEMA = "formal-companion/annotations"
#: ``"<major>.<minor>"``.  Readers match the major version; a writer declares the
#: lowest minor whose schema admits the document.
ANNOTATION_SCHEMA_VERSION = "1.0"
ANNOTATION_SCHEMA_MAJOR = 1
#: The schema ``$id`` is ``<base>/v<major>.<minor>/schema.json``: one per format
#: minor, never changed after release, an identifier that is never fetched.
ANNOTATION_SCHEMA_ID_BASE = "https://w3id.org/formal-companion/annotations"
#: The four optional profiles and the version of each that this package knows
#: (docs/annotations.md, section 4.2).  Each profile is versioned on its own;
#: a document declares the ones it uses in its root ``profiles`` object.
ANNOTATION_PROFILE_VERSIONS = {"audit": "1.0", "graph": "1.0", "history": "1.0", "site": "1.0"}
ANNOTATION_PROFILES = frozenset(ANNOTATION_PROFILE_VERSIONS)

# --- sources (section 5.1)
#: ``other`` names any other medium by its IANA ``media_type`` (markdown, EPUB,
#: video); the four named kinds are presets for the common ones.
ANNOTATION_SOURCE_KINDS = frozenset({"pdf", "web", "tex", "lean", "other"})
#: ``site``: one web source for a whole rendered site, each location naming its
#: page with a ``SitePageSelector``.  Only a ``web`` source may be a site.
ANNOTATION_SOURCE_SCOPES = frozenset({"page", "site"})
#: A source's ``role``: ``audited`` (a companion is checked against it) or
#: ``reference`` (kept only so a page map to a sibling edition can be checked).
#: A core field: a document with no verdicts may still say which source it
#: audits (section 5.1).
ANNOTATION_SOURCE_ROLES = frozenset({"audited", "reference"})
#: A source's ``text_layer``: how quotes on it are matched (section 6.7).
ANNOTATION_TEXT_UNICODE = frozenset({"none", "nfc", "nfkc"})
ANNOTATION_TEXT_WHITESPACE = frozenset({"exact", "collapse", "ignore"})
ANNOTATION_TEXT_CASE = frozenset({"sensitive", "insensitive"})

# --- items (section 5.2)
#: The discriminator of an item record.
ANNOTATION_ITEM_FORMS = frozenset({"numbered", "passage"})
#: How an item id was formed (section 7.2).
ANNOTATION_ITEM_ID_SOURCES = frozenset({"label", "quote_hash", "page_title_hash", "declared"})
#: Whose words an item's ``kind`` or ``title`` are.
ANNOTATION_TEXT_ORIGINS = frozenset({"source", "producer"})
#: The curator's class for an item: what its links state (invariant I5 ties the
#: class to the links).  ``formalized``: a key link states the whole item;
#: ``partial_only``: the linked declarations state a special case or one part;
#: ``support_only``: only a neighbouring item is stated; ``example_only``: the
#: only declarations that state it are anonymous examples, linked with role
#: ``example`` (an anonymous example that states the whole item is a ``key``
#: link's target, and the item is ``formalized``); ``private_only``: only a
#: private declaration states it; ``not_formalized``: nothing states it;
#: ``out_of_scope``: the companion does not set out to formalise it.
ANNOTATION_COVERAGE = frozenset({"formalized", "partial_only", "support_only", "example_only",
                                 "private_only", "not_formalized", "out_of_scope"})
#: The typed relations between items (``item.relations``).
ANNOTATION_ITEM_RELATIONS = frozenset({"same_as", "part_of", "near"})
#: The documented core of an xref's ``database``: the public databases whose
#: entries Lean cross-reference attributes name.  The field is an open
#: lower-case snake_case token; another token passes with a warning (W-XREF).
ANNOTATION_XREF_DATABASES = frozenset({"stacks", "kerodon", "wikidata", "lmfdb"})
ANNOTATION_PAGE_BASES = frozenset({"printed", "physical"})
#: Where a page came from: the thing's own anchor, a cross-reference to it, or
#: inferred from a neighbour.
ANNOTATION_PAGE_ANCHORS = frozenset({"own", "crossref", "inferred"})

# --- declarations (section 5.3)
#: The Lean keyword, or the member kind.  A member (``field``, ``projection``,
#: ``constructor``, ``recursor``) names the declaration it belongs to in
#: ``parent``; ANNOTATION_MEMBER_PARENT_KINDS says which kinds a parent may have.
ANNOTATION_LEAN_KINDS = frozenset({"theorem", "lemma", "def", "abbrev", "structure", "class",
                                   "inductive", "instance", "example", "opaque", "axiom", "alias",
                                   "irreducible_def", "field", "projection", "constructor",
                                   "recursor"})
#: For each member kind, the kinds its ``parent`` may have.  No other kind
#: carries a ``parent``.
ANNOTATION_MEMBER_PARENT_KINDS = {
    "field": frozenset({"structure", "class"}),
    "projection": frozenset({"structure", "class"}),
    "constructor": frozenset({"inductive", "structure", "class"}),
    "recursor": frozenset({"inductive", "structure", "class"}),
}
#: How a declaration id was formed (section 7.1).
ANNOTATION_ID_SOURCES = frozenset({"name", "private", "statement_hash", "declared"})
#: A declaration's ``sorry``: whether its proof holds a ``sorry``, and why.
ANNOTATION_SORRY_CLASSES = frozenset({"exercise", "gap", "none"})

# --- links (section 5.4)
ANNOTATION_LINK_ROLES = frozenset({"key", "attributed", "support", "example", "helper"})
#: The documented core of a link's ``relation``: how the declaration relates to
#: the item's text.  The field is an open lower-case snake_case token (the
#: schema's pattern); a token outside this set passes with a warning
#: (W-RELATION).  ``part``: the declaration states one part of the item.
ANNOTATION_LINK_RELATIONS = frozenset({"exact", "generalization", "special_case", "part",
                                       "invocation", "related"})

# --- provenance and unattributed (sections 5.6 and 5.8)
ANNOTATION_PROVENANCE_ACTORS = frozenset({"author", "agent", "human", "tool"})
ANNOTATION_PROVENANCE_METHODS = frozenset({"docstring_tag", "curation", "blueprint_macro",
                                           "lean_scan", "layout_extraction", "text_match",
                                           "manual", "agent_annotation", "pipeline",
                                           "adjudication", "site_index"})
ANNOTATION_UNATTRIBUTED_CLAIMS = frozenset({"unattributed", "api", "infrastructure"})

# --- the graph profile (section 8.1)
#: Formalization artifacts that are not declarations.
ANNOTATION_ARTIFACT_KINDS = frozenset({"module_doc", "check", "figure", "other"})
#: A cluster member's role, as the companion's own graph gives it.
ANNOTATION_MEMBERSHIP_ROLES = frozenset({"primary", "support", "consumer"})

# --- the site profile (section 8.2)
ANNOTATION_DESTINATION_SITES = frozenset({"verso_literate", "doc_gen4", "blueprint", "other"})
#: A destination's ``resolution``: ``exact`` needs a ``fragment``, ``fallback`` a
#: ``fallback_label``.
ANNOTATION_DESTINATION_RESOLUTIONS = frozenset({"exact", "fallback"})

# --- the audit profile (section 8.3): a verdict's vocabulary is the format's own
#: A verdict's ``verdict`` and each ``declarations[].verdict``.
ANNOTATION_VERDICTS = frozenset({"faithful", "convention", "attribution_gap", "proxy", "deviation"})
#: A verdict's ``severity``, ordered ``none`` < ``low`` < ``medium`` < ``high``.
ANNOTATION_SEVERITIES = frozenset({"none", "low", "medium", "high"})
#: A verdict's ``status``.
ANNOTATION_VERDICT_STATUSES = frozenset({"unconfirmed", "contested", "human_confirmed", "fixed"})
#: A verdict's ``resolution.state``.  ``resolution`` is optional; a verdict
#: without one is open.
ANNOTATION_RESOLUTION_STATES = frozenset({"open", "fixed", "in_progress", "refuted",
                                          "argued_correct", "out_of_scope"})

# --- the history profile (section 8.4)
#: ``ordinal_assigned`` records which declaration took a ``~k`` statement-hash
#: ordinal, by its file; without it a ``~k`` id is refused, and so is a live id
#: whose latest record names another file (section 7.1.3).
ANNOTATION_CHANGELOG_EVENTS = frozenset({"born", "retired", "restated", "renamed",
                                         "ordinal_assigned"})
ANNOTATION_CHANGELOG_SUBJECTS = frozenset({"declaration", "item", "location", "artifact"})
ANNOTATION_CHANGELOG_REASONS = frozenset({"statement_changed", "removed", "renamed", "moved",
                                          "other"})

# --- locations and selectors (section 6)
#: The five selector types of version 1.0: three W3C Web Annotation classes and
#: the two extensions (``PdfRegionSelector``, ``SitePageSelector``).
ANNOTATION_SELECTOR_TYPES = frozenset({"FragmentSelector", "TextQuoteSelector", "CssSelector",
                                       "PdfRegionSelector", "SitePageSelector"})
#: A ``PdfRegionSelector``'s ``coordinate_space``.  ``top_left`` goes with
#: ``y_axis`` ``down`` and ``bottom_left`` with ``up`` (ANNOTATION_ORIGIN_Y_AXIS);
#: ``normalized`` coordinates are fractions of the page box, ``pt`` are points.
ANNOTATION_COORDINATE_ORIGINS = frozenset({"top_left", "bottom_left"})
ANNOTATION_COORDINATE_UNITS = frozenset({"normalized", "pt"})
ANNOTATION_COORDINATE_X_AXES = frozenset({"right"})
ANNOTATION_COORDINATE_Y_AXES = frozenset({"down", "up"})
ANNOTATION_ORIGIN_Y_AXIS = {"top_left": "down", "bottom_left": "up"}
ANNOTATION_PAGE_BOXES = frozenset({"MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox"})
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

# --- the id grammar (section 7.1), used by ids.py (which writes ids) and
# validate.py (which checks them), both with full-match semantics.
#: A statement-hash id is ``<keyword>:<12 hex>`` with an optional ``~k``
#: (k >= 2).  A declared id is ``<keyword>:<slug>``: letters of either case,
#: digits, ``.``, ``_`` and ``-``, starting with a letter or digit, and never a
#: slug that is 12 lowercase hex digits (with or without ``~k``), which is
#: reserved for statement-hash ids.
ANONYMOUS_KEYWORDS = ("example", "instance")
HASH_ID_PATTERN = r"(?P<kw>example|instance):(?P<hex>[0-9a-f]{12})(?:~(?P<k>[2-9]|[1-9][0-9]+))?"
DECLARED_SLUG_PATTERN = r"(?![0-9a-f]{12}(?:~[0-9]+)?(?![A-Za-z0-9._~-]))[A-Za-z0-9][A-Za-z0-9._-]*"
DECLARED_ID_PATTERN = r"(?P<kw>example|instance):(?P<slug>" + DECLARED_SLUG_PATTERN + r")"
#: The two mechanical passage-id forms (section 7.2); a declared item id may
#: not take either shape.
PASSAGE_QUOTE_ID_PATTERN = r"passage-[0-9a-f]{8}"
PASSAGE_PAGE_ID_PATTERN = r"passage-p[1-9][0-9]*-[0-9a-f]{8}"
