"""The ONE vocabulary. Documented in references/vocabulary.md.

New code, consolidating the five drifting vocabularies the design records
(match verdict -> finding kind -> challenge classification -> ledger verdict
-> viewer verdict).  Every stage imports its enums
from here: a value that is not in these sets is a gate failure, never a new
convention.

The locate block at the end is new code too, for the inverse question --
where is a source formalized, in any library -- and implements the vocabulary
of docs/proposals/wikipedia-locate.md sections 2.2, 2.4, 2.6, 2.7, 5 and 10
(documented in references/vocabulary.md section 1.11), with
:func:`derive_status` and :func:`decide_proof_status`.  The locate stages --
scripts/sources, scripts/corpus, scripts/locate, scripts/gate/verify_locate.py
and scripts/bench -- import their values from here; the copies a host-side
script must inline are pinned to these by tests.
"""
from __future__ import annotations

import re
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

# --- locate finding kinds (docs/proposals/wikipedia-locate.md sections 2.6, 3, 6)
#: ``FINDING_KINDS`` above stays the set of FAITHFULNESS kinds: the ledger
#: validator, the answer key and ``harvest``'s substantive kinds all read it,
#: and a locate outcome is not a defect.  These are the kinds a locate document
#: row carries in ``kind`` -- the field ``harvest.tally`` hashes into
#: ``finding_id`` -- so a passage's own kind travels in ``passage_kind``
#: (``PASSAGE_KINDS``), never in ``kind``.
#:
#: ``PASSAGE``: one row per passage of an inventory (L3a), keyed by its
#: ``In-text #<hex>`` item.  ``LOCATION``: the passage is stated, wholly or in
#: part, by a declaration at a commit.  ``ABSENCE``: a checked statement that
#: the passage was searched for in a universe and nothing was found.
#: ``UNRESOLVED``: the worker, or the attempt cap of section 6, could not
#: decide -- counted on its own, never as not formalized.
PASSAGE = "PASSAGE"
LOCATION = "LOCATION"
ABSENCE = "ABSENCE"
UNRESOLVED = "UNRESOLVED"
#: the three outcomes a located passage can end with (section 2.6)
LOCATE_OUTCOME_KINDS = frozenset({LOCATION, ABSENCE, UNRESOLVED})
LOCATE_FINDING_KINDS = frozenset({PASSAGE}) | LOCATE_OUTCOME_KINDS
#: every kind any mode may emit, faithfulness and locate together
ALL_FINDING_KINDS = FINDING_KINDS | LOCATE_FINDING_KINDS

# --- run modes and the kinds each mode may emit
DIFFERENTIAL = "differential"
SINGLE_SOURCE = "single-source"
ATTACK_VAC = "attack:vac"
ATTACK_HUNT = "attack:hunt"
#: the three locate presets (L3a inventory, L3b locate, L5 challenge); planned
#: by `cf plan-locate` (scripts/locate/plan.py)
LOCATE_INVENTORY = "locate-inventory"
LOCATE = "locate"
LOCATE_CHALLENGE = "locate-challenge"
LOCATE_MODES = frozenset({LOCATE_INVENTORY, LOCATE, LOCATE_CHALLENGE})
RUN_MODES = frozenset({DIFFERENTIAL, SINGLE_SOURCE, ATTACK_VAC, ATTACK_HUNT}) | LOCATE_MODES
ATTACK_MODES = frozenset({ATTACK_VAC, ATTACK_HUNT})

KINDS_BY_MODE: dict[str, frozenset] = {
    DIFFERENTIAL: frozenset({CONTENT, VACUITY, SUSPECTED_ERRATA, COVERAGE}),
    SINGLE_SOURCE: frozenset({CONTENT, VACUITY, SUSPECTED_ERRATA, COVERAGE}),
    ATTACK_VAC: frozenset({CONTENT, VACUITY, SUSPECTED_ERRATA}),
    ATTACK_HUNT: frozenset({CONTENT, VACUITY, SUSPECTED_ERRATA}),
    # an inventory task ends with its passage table, or -- the attempt cap's
    # document -- UNRESOLVED; it locates nothing
    LOCATE_INVENTORY: frozenset({PASSAGE, UNRESOLVED}),
    # every formal_math passage of the task ends located, absent or unresolved
    LOCATE: LOCATE_OUTCOME_KINDS,
    # a challenger re-judges those records and writes the same three kinds,
    # each with a CHALLENGE_VERDICTS value; a hunted absence may become a LOCATION
    LOCATE_CHALLENGE: LOCATE_OUTCOME_KINDS,
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
VIEWER_SEVERITIES = frozenset({"none", "low", "medium", "high"})
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

#: What a source's licence lets an EXPORTED record quote (POLICY.md section 4.1,
#: docs/proposals/wikipedia-locate.md section 10).  ``closed`` is the default
#: for every source whose registry entry says nothing: all rights reserved, so
#: an export redacts its quotes.  ``open_attribution`` (CC BY and the like) and
#: ``open_share_alike`` (CC BY-SA, Wikipedia's text) may be quoted in capped form
#: with a citation and the licence named; ``public_domain`` needs neither, and
#: is capped all the same.  It is orthogonal to ``LICENCE_FETCH``: a class says
#: what may be QUOTED, a fetch value what may be DOWNLOADED, and neither lets
#: the skill redistribute a source or put its text in this repository.
LICENCE_CLOSED = "closed"
LICENCE_OPEN_ATTRIBUTION = "open_attribution"
LICENCE_OPEN_SHARE_ALIKE = "open_share_alike"
LICENCE_PUBLIC_DOMAIN = "public_domain"
LICENCE_CLASSES = frozenset({LICENCE_CLOSED, LICENCE_OPEN_ATTRIBUTION,
                             LICENCE_OPEN_SHARE_ALIKE, LICENCE_PUBLIC_DOMAIN})
DEFAULT_LICENCE_CLASS = LICENCE_CLOSED
#: the classes whose text an exported record may quote, capped
OPEN_LICENCE_CLASSES = frozenset({LICENCE_OPEN_ATTRIBUTION, LICENCE_OPEN_SHARE_ALIKE,
                                  LICENCE_PUBLIC_DOMAIN})
#: the classes whose quotes must travel with a citation and the licence named
ATTRIBUTION_LICENCE_CLASSES = frozenset({LICENCE_OPEN_ATTRIBUTION, LICENCE_OPEN_SHARE_ALIKE})

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


# ==========================================================================
# locate: where a source is formalized (docs/proposals/wikipedia-locate.md)
# ==========================================================================

#: The extension namespace root of every locate record (section 2.7), written
#: ``‹locate›`` in the proposal: a record's, a subject end's and an object end's
#: ``extensions`` key.
LOCATE_EXTENSION = "org.mathlib-initiative.formal-companion.locate"

# --- passages (section 2.2)
#: What a passage states.  Travels as ``passage_kind`` in a locate document row
#: and as the record's ``kind`` in an exported Literature Links record.
PASSAGE_DEFINITION = "definition"
PASSAGE_THEOREM = "theorem"
PASSAGE_CONJECTURE = "conjecture"
PASSAGE_EXAMPLE = "example"
PASSAGE_CONSTRUCTION = "construction"
PASSAGE_NOTATION = "notation"
PASSAGE_OTHER = "other"
PASSAGE_KINDS = frozenset({PASSAGE_DEFINITION, PASSAGE_THEOREM, PASSAGE_CONJECTURE,
                           PASSAGE_EXAMPLE, PASSAGE_CONSTRUCTION, PASSAGE_NOTATION,
                           PASSAGE_OTHER})
#: Which row of the section 2.6 table a passage kind is judged by.  The table
#: names theorem/conjecture passages and definition passages only; the rest is
#: decided here (references/vocabulary.md section 1.11): a construction or a
#: notation introduces an object or a symbol, so it is a definition passage,
#: and an example or an ``other`` passage is judged by BOTH rows, the better
#: answer winning -- "the Klein four-group" is formalized by a definition, "x^2
#: is continuous" by a theorem.
THEOREM_PASSAGE_KINDS = frozenset({PASSAGE_THEOREM, PASSAGE_CONJECTURE})
DEFINITION_PASSAGE_KINDS = frozenset({PASSAGE_DEFINITION, PASSAGE_CONSTRUCTION,
                                      PASSAGE_NOTATION})
EITHER_PASSAGE_KINDS = frozenset({PASSAGE_EXAMPLE, PASSAGE_OTHER})

#: How central a passage is to its source.  Not "role", which Literature Links
#: uses for an end.  ``subject``: what the article is about; ``central``: a
#: named result or definition the article states; ``supporting``: anything else
#: formal.
PROMINENCE_SUBJECT = "subject"
PROMINENCE_CENTRAL = "central"
PROMINENCE_SUPPORTING = "supporting"
PROMINENCE = frozenset({PROMINENCE_SUBJECT, PROMINENCE_CENTRAL, PROMINENCE_SUPPORTING})
#: the prominences whose locations are refuted by default and whose absences
#: and unresolved outcomes are hunted (section 5)
REFUTE_BY_DEFAULT_PROMINENCE = frozenset({PROMINENCE_SUBJECT, PROMINENCE_CENTRAL})

#: Whether a passage is mathematics to locate.  Only ``formal_math`` passages
#: are located; a passage may not be scoped ``expository`` or ``non_math``
#: while the deterministic candidates (L2) hold a tag or curated hit for it.
SCOPE_FORMAL_MATH = "formal_math"
SCOPE_EXPOSITORY = "expository"
SCOPE_NON_MATH = "non_math"
SCOPES = frozenset({SCOPE_FORMAL_MATH, SCOPE_EXPOSITORY, SCOPE_NON_MATH})
LOCATED_SCOPES = frozenset({SCOPE_FORMAL_MATH})

# --- declarations (section 2.4)
#: A declaration's ``proof_status``, in the ORDER it is decided: the first that
#: applies wins, independent of declaration kind (:func:`decide_proof_status`).
#: Computed mechanically from the axiom closure and the type, never by a model
#: and never by a worker, whose document may not carry the field at all.
PROOF_WANTED = "wanted"
PROOF_STATEMENT_ONLY = "statement_only"
PROOF_AXIOM_DEPENDENT = "axiom_dependent"
PROOF_PROVED = "proved"
PROOF_DEFINITION = "definition"
PROOF_STATUSES: tuple[str, ...] = (PROOF_WANTED, PROOF_STATEMENT_ONLY,
                                   PROOF_AXIOM_DEPENDENT, PROOF_PROVED, PROOF_DEFINITION)
PROOF_STATUS_SET = frozenset(PROOF_STATUSES)
#: the axioms a ``proved`` declaration may depend on; any other is ``axiom_dependent``
STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
#: the axiom ``sorry`` elaborates to; in a closure it means ``statement_only``
SORRY_AXIOM = "sorryAx"

# --- relations (section 2.6): Literature Links relation words with SKOS
# direction, subject = passage, object = declaration
EXACT_MATCH = "exactMatch"
CLOSE_MATCH = "closeMatch"
BROAD_MATCH = "broadMatch"
NARROW_MATCH = "narrowMatch"
HAS_PART = "hasPart"
RELATED_MATCH = "relatedMatch"
#: A location's relation: model-judged, then challenged.  ``exactMatch``: the
#: declaration states the passage; ``closeMatch``: the same up to a convention
#: difference named in the record's ``note``; ``broadMatch``: the declaration is
#: more general; ``narrowMatch``: it is a special case; ``hasPart``: it states
#: one part of a passage that says several things; ``relatedMatch``: relevant,
#: but it states something else.
LOCATE_RELATIONS = frozenset({EXACT_MATCH, CLOSE_MATCH, BROAD_MATCH, NARROW_MATCH,
                              HAS_PART, RELATED_MATCH})
#: how strong a relation is, strongest first (a lower number is stronger): a
#: challenge's ``downgrade`` names a relation of a strictly higher number than the
#: record's.  ``broadMatch`` stands above ``narrowMatch`` and ``hasPart`` because
#: :func:`derive_status` counts it for a theorem and them only as ``partial``
#: (references/locate-documents.md section 4).  The gate inlines a copy, pinned by
#: tests/test_locate_contract.py.
RELATION_STRENGTH = {EXACT_MATCH: 0, CLOSE_MATCH: 1, BROAD_MATCH: 2, NARROW_MATCH: 3,
                     HAS_PART: 3, RELATED_MATCH: 4}
#: The relation of an ABSENCE record, the spec's checked-absence form: one
#: ``lean`` end per universe library and no ``location``.  Never a location's.
NO_MATCH = "noMatch"
#: at most this many locations per passage, ranked 1..K (section 2.7)
MAX_LOCATIONS_PER_PASSAGE = 5

# --- the derived status (section 2.6): the Literature Links ``status``, computed
# once per passage after challenge and stamped on all of its records
STATUS_FORMALIZED = "formalized"
STATUS_STATEMENT_ONLY = "statement_only"
STATUS_PARTIAL = "partial"
STATUS_NOT_FORMALIZED = "not_formalized"
#: best first; an unresolved passage has no status at all (``None``)
DERIVED_STATUSES: tuple[str, ...] = (STATUS_FORMALIZED, STATUS_STATEMENT_ONLY,
                                     STATUS_PARTIAL, STATUS_NOT_FORMALIZED)
DERIVED_STATUS_SET = frozenset(DERIVED_STATUSES)

# --- challenge (section 5): every exported record carries one of these
CHALLENGE_SURVIVED = "survived"
CHALLENGE_DOWNGRADED = "downgraded"
CHALLENGE_NOT_REQUIRED = "not_required"
CHALLENGE_SAMPLED_OUT = "sampled_out"
CHALLENGE_PENDING = "challenge_pending"
#: ``survived``: challenged and confirmed with a quoted witness;
#: ``downgraded``: challenged, and the relation (or outcome) was weakened;
#: ``not_required``: outside every challenge rule; ``sampled_out``: inside the
#: stratified sample's frame, not drawn; ``challenge_pending``: a rule now
#: requires it and no challenge judged it -- the passage's prominence was
#: overturned to ``subject`` or ``central`` after its records were planned
#: under ``supporting`` (a consumer reads it as unchallenged).  Nothing leaves a
#: workspace without one (hard rule 5, per record).
CHALLENGE_VERDICTS = frozenset({CHALLENGE_SURVIVED, CHALLENGE_DOWNGRADED,
                                CHALLENGE_NOT_REQUIRED, CHALLENGE_SAMPLED_OUT,
                                CHALLENGE_PENDING})
#: the values of a record no challenge judged
CHALLENGE_UNJUDGED = frozenset({CHALLENGE_NOT_REQUIRED, CHALLENGE_SAMPLED_OUT,
                                CHALLENGE_PENDING})
#: the weak relations: on a ``supporting`` passage the stratified challenge
#: sample re-judges them at the plan's ``sample_rate`` (section 5)
WEAK_RELATIONS = frozenset({RELATED_MATCH, NARROW_MATCH, HAS_PART})
#: the strong relations: the three that formalize a theorem passage against a
#: proved declaration (:func:`derive_status`).  On a ``supporting`` passage the
#: one that sets its status (its basis) is required, and every other is
#: re-judged in a stratified sample at the plan's ``strong_sample_rate``; the
#: smoke-2 audit found them right about 45% of the time when nobody challenged
#: them (references/vocabulary.md section 1.11)
STRONG_RELATIONS = frozenset({EXACT_MATCH, CLOSE_MATCH, BROAD_MATCH})

# --- the locate documents (references/locate-documents.md): the values the
# stages exchange, defined once here so the renderer, the planner, the gate and
# the bench read one set
#: the ``schema`` of every inventory, locate and challenge document
LOCATE_DOCUMENT_SCHEMA = "cf-locate/1"
#: the renderer of every web source of the first corpus (section 2.1); a source's
#: ``renderer`` field, and a pin of every run, gate and score document.
#: ``web-html/1`` kept style and script text in the layer and collapsed ASCII
#: whitespace only; ``web-html/2`` is the Literature Links rule; ``web-html/3``
#: keeps the bold of a named concept or statement that ``/2``'s label rule took
#: for a label ("property (T)", "Axiom A", "Theorem (Widget lemma).") and records
#: every view's sha in ``render.json`` (scripts/sources/render_html.py states it
#: in full)
RENDERER_WEB_HTML = "web-html/3"
#: Where a sourceset entry came from (references/locate-documents.md section 7):
#: ``enumerated`` -- the PageAssessments listing, at the page's latest revision;
#: ``adopted`` -- a consumer's pinned revision and bytes, recorded in
#: ``adopted_from``, which a run plans instead of an enumerated revision of the
#: same page (the proposal's section 2.1).
SOURCE_ORIGIN_ENUMERATED = "enumerated"
SOURCE_ORIGIN_ADOPTED = "adopted"
SOURCE_ORIGINS = frozenset({SOURCE_ORIGIN_ENUMERATED, SOURCE_ORIGIN_ADOPTED})
#: What a sourceset entry's ``flags`` say of its page, each true, false, or null
#: for not determined (references/locate-documents.md section 7): ``redirect``
#: and ``disambiguation`` (the page info and the assessment class), ``list``
#: (the class, or a "List of" title), all set at enumeration, and
#: ``biography`` (Wikidata P31 = Q5), set by the optional ``flag-biographies``.
#: A page with any of them true is no concept article.
PAGE_FLAG_REDIRECT = "redirect"
PAGE_FLAG_DISAMBIGUATION = "disambiguation"
PAGE_FLAG_LIST = "list"
PAGE_FLAG_BIOGRAPHY = "biography"
PAGE_FLAGS = frozenset({PAGE_FLAG_REDIRECT, PAGE_FLAG_DISAMBIGUATION, PAGE_FLAG_LIST,
                        PAGE_FLAG_BIOGRAPHY})
#: Why an inventory plan left a source out (``run.yaml`` ``locate.skipped``, and the
#: reason of a bench draw's excluded article; references/locate-documents.md section
#: 9): ``not rendered`` -- no layer yet, which a render settles; ``parity refused``
#: -- its render stands refused because its layer is not the producer's, which no
#: render of the sourceset settles, only the producer's corrected delivery adopted
#: into a new one.  A page that is no concept article is skipped too, its reason
#: ``LOCATE_SKIP_NO_CONCEPT`` followed by its true flags in parentheses
#: ("no concept article (redirect)").
LOCATE_SKIP_NOT_RENDERED = "not rendered"
LOCATE_SKIP_PARITY_REFUSED = "parity refused"
LOCATE_SKIP_NO_CONCEPT = "no concept article"
LOCATE_SKIP_REASONS = frozenset({LOCATE_SKIP_NOT_RENDERED, LOCATE_SKIP_PARITY_REFUSED,
                                 LOCATE_SKIP_NO_CONCEPT})
#: What one formal_math passage of a locate document ends with (section 2.6), and
#: the finding kind each one is tallied under.
OUTCOME_LOCATED = "located"
OUTCOME_ABSENT = "absent"
OUTCOME_UNRESOLVED = "unresolved"
LOCATE_OUTCOMES = frozenset({OUTCOME_LOCATED, OUTCOME_ABSENT, OUTCOME_UNRESOLVED})
OUTCOME_KIND = {OUTCOME_LOCATED: LOCATION, OUTCOME_ABSENT: ABSENCE,
                OUTCOME_UNRESOLVED: UNRESOLVED}
#: Which finder proposed an inventory passage.  The three deterministic finders'
#: passages are pre-filled in an inventory task, and dropping one needs a skip
#: naming its span (gate rule 8); ``model`` is the enumerator's own.
FINDER_THEOREM_BOX = "theorem_box"
FINDER_BOLD_DEFINITION = "bold_definition"
FINDER_LEAD = "lead"
FINDER_MODEL = "model"
DETERMINISTIC_FINDERS = frozenset({FINDER_THEOREM_BOX, FINDER_BOLD_DEFINITION, FINDER_LEAD})
FINDERS = DETERMINISTIC_FINDERS | {FINDER_MODEL}
#: The words a bold run that only LABELS a paragraph is made of ("Proof.",
#: "Example 2:", "Definition:"): such a bold names no term, so it makes no
#: ``bold_definition`` passage and is no L2 lexical term (``is_bold_label``).
#: These are labels however they are written ...
BOLD_LABELS = frozenset({
    "proof", "proofs", "proof sketch", "sketch of proof", "proof idea", "example", "examples",
    "counterexample", "counterexamples", "non-example", "definition", "definitions",
    "theorem", "theorems", "lemma", "lemmas", "corollary", "corollaries", "proposition",
    "propositions", "remark", "remarks", "notation", "exercise", "exercises", "claim",
    "conjecture", "case", "cases", "step", "base case", "inductive step", "induction step",
    "hint", "note", "notes", "warning", "caution"})
#: ... and these only with a label's punctuation ("Solution:", "Problem 3."),
#: since without it they may be the term a sentence defines -- an enumerator
#: alone is no such mark: "Axiom A", "Problem P", "property (T)" name concepts.
BOLD_LABELS_PUNCTUATED = frozenset({
    "solution", "answer", "problem", "question", "input", "output", "statement",
    "observation", "axiom", "axioms", "fact", "result", "construction", "algorithm",
    "property", "properties", "assumption", "assumptions", "special case", "idea"})
_LABEL_TRIM = " \t.:;,!?\u2013\u2014-"
#: an enumerator a label may carry after its word: 2, 2.1, 3a, iv, (b), (2)
_LABEL_ENUMERATOR = re.compile(r"(?:\s+(?:\(?(?:\d+(?:\.\d+)*[a-z]?|[ivxlc]+|[a-z])\)?))+$")
#: a location's ``confidence``: the worker's own, recorded, never a status input
CONFIDENCES = frozenset({"high", "medium", "low"})
#: a receipt candidate's ``verdict``: every curated candidate handed to a task is
#: accepted or rejected with a reason (gate rule 4)
CANDIDATE_ACCEPTED = "accepted"
CANDIDATE_REJECTED = "rejected"
CANDIDATE_VERDICTS = frozenset({CANDIDATE_ACCEPTED, CANDIDATE_REJECTED})
#: How an L2 candidate was found deterministically (section 3, L2): a library's
#: ``@[wikidata]`` tag on the article's own QID, Mathlib's two curated lists, a
#: Wikipedia URL in a docstring, and a library's own metadata (FormalFrontier
#: ``formalization.yaml`` main results).  Every one is a ``curated`` hit, which
#: gate rule 4 reads; lexical retrieval is not.
CANDIDATE_VIA = frozenset({"wikidata_tag", "mathlib_1000", "mathlib_100", "docstring_url",
                           "library_metadata"})
#: the tags the library index parses from a declaration's source span and docstring
TAG_KINDS = frozenset({"wikidata", "stacks", "kerodon", "wikipedia"})
#: What a challenge verdict judges, and what it does (section 5).  ``confirm`` and
#: ``uphold`` keep the record (``survived``); ``downgrade``, ``refute`` and
#: ``overturn`` change it (``downgraded``).  ``coverage`` is a passage's coverage
#: claim (:data:`COVERAGE_TARGET`, the maintainer's decision D1).
COVERAGE_TARGET = "coverage"
CHALLENGE_TARGETS = frozenset({"location", "absence", "unresolved", "scope", "prominence",
                               COVERAGE_TARGET})
CHALLENGE_KEEP = frozenset({"confirm", "uphold"})
CHALLENGE_CHANGE = frozenset({"downgrade", "refute", "overturn"})
CHALLENGE_ACTIONS = CHALLENGE_KEEP | CHALLENGE_CHANGE
#: The coverage claim (the maintainer's decision D1, 2026-10-18; references/
#: vocabulary.md section 1.11): a located passage that says several things may
#: claim that its ``hasPart`` locations TOGETHER state every clause, with no
#: hypothesis stronger than the passage's.  ``{"complete": true, "parts": [ranks],
#: "note": "<one sentence>"}`` in a locate document, at least
#: ``MIN_COVERAGE_PARTS`` parts, every one a ``hasPart`` location of the passage.
#: Every claim is challenged whatever the passage's prominence -- refuted by
#: default, ``confirm`` or ``refute`` (``COVERAGE_ACTIONS``) -- because it upgrades
#: a status; a refute removes the claim and leaves its parts ``hasPart``, and a
#: confirm confirms its parts too (each one no other verdict judged is
#: ``survived``, except one a prominence overturn in the same run reopened, which
#: stays ``CHALLENGE_PENDING``: the reopen wins).  Only a claim whose challenge is ``survived`` counts in
#: :func:`derive_status`, as one more ``exactMatch`` location.
MIN_COVERAGE_PARTS = 2
COVERAGE_ACTIONS = frozenset({"confirm", "refute"})
#: the keys of a coverage claim as a record carries it (``extensions[locate].coverage``,
#: its parts the ``location_key`` of each and its challenge one of ``CHALLENGE_VERDICTS``)
#: and :func:`derive_status` reads it -- these and no others: the claim's note is not
#: exported, and an owed claim, with no challenge value yet, is no record's claim
COVERAGE_RECORD_KEYS = frozenset({"complete", "parts", "challenge"})
#: an absence's receipt lists at least this many distinct queries (gate rule 5)
MIN_ABSENCE_QUERIES = 3
#: The judgment rules (gate rule 9; references/vocabulary.md section 1.11, rules
#: R1-R7 and the policy defaults, and references/locate-documents.md sections 3
#: and 4): the version a run's ``gate.json`` names as ``judgment_rules``.  ``cf
#: plan-locate`` writes it for every run it plans, so the rules bind the runs
#: planned with this code; a ``gate.json`` without the key -- a run planned before
#: them -- is gated, harvested, tallied and exported as it always was, and one
#: naming an older version is gated by that version.  Version 1 is R1-R7 (the
#: smoke-2 audit); version 2 (``RULES_PD5_PD9``) adds the maintainer's PD5-PD9 of
#: 2026-10-20, whose mechanical part is two checks: a coverage claim one of whose
#: parts alone names every claim of the passage is refused (PD6), and a junk value
#: is no ``closeMatch`` convention (PD7: ``CONVENTION_JUNK_VALUE`` leaves
#: ``CLOSE_MATCH_CONVENTIONS``).
JUDGMENT_RULES = 2
#: the first judgment rules version that holds PD5-PD9 (see ``JUDGMENT_RULES``)
RULES_PD5_PD9 = 2
#: The span-end rule (gate rule 2's end check; references/locate-documents.md
#: sections 2 and 9): the version a run's ``gate.json`` names as
#: ``span_end_rule``.  Under it an inventory passage ends where ``passage_ids.py``
#: ends it (``locate_text.passage_end``: its ``until``, else its paragraph, else
#: its quote), and the gate refuses any other end.  ``cf plan-locate`` writes it
#: into every gate.json it plans, as it does ``JUDGMENT_RULES``, so the check binds
#: the inventory documents of a run planned with this code; a ``gate.json``
#: without the key -- a run planned before it, whose ``passage_ids.py`` dropped
#: ``until`` after placing the end -- is gated, also at a harvest's re-gate, with
#: the end held only inside the layer and after the quote, as it always was.  A
#: locate or challenge document carries no span of its own (the gate takes each
#: passage's span from the task manifest), so in those modes the key binds nothing.
SPAN_END_RULE = 1
#: A passage's claim list (rule R4; the smoke-2 audit's FM1): before it judges any
#: declaration, the worker writes the passage's formal clauses as short
#: paraphrases, each with an id ``c1``, ``c2``, ... (``CLAIM_ID``), at most
#: ``MAX_PASSAGE_CLAIMS`` of them, each one line of at most ``MAX_CLAIM_CHARS``
#: characters and never more than ``EXPORT_QUOTE_WORDS`` consecutive words of the
#: source; every location whose relation counts names the ids it states
#: (``clauses``), and an ``exactMatch``, ``closeMatch`` or ``broadMatch`` names
#: every one.
CLAIM_ID = re.compile(r"c[1-9][0-9]?\Z")
MAX_PASSAGE_CLAIMS = 12
MAX_CLAIM_CHARS = 300
#: The search receipt under the judgment rules (the smoke-2 audit's FM5: the
#: queries covered one clause and skipped the formal one): ``query_clauses`` maps
#: each query of ``queries`` to the claim ids it targets.  An absence tags every
#: query, and each claim -- and, for a worked example, the general theorem it is
#: an instance of, tagged ``GENERAL_QUERY_TAG`` -- has a distinct query of its own
#: among the tags (a matching of distinct queries to claims): one query tagged with
#: every claim searched for one of them.  A challenger's uphold of an absence or an
#: unresolved outcome lists its own queries, tagged the same way.
GENERAL_QUERY_TAG = "general"
#: A location's ``instantiation`` (the smoke-2 audit's FM6: a finite-dimensional
#: lemma credited to an operator on an infinite-dimensional space, an abstract
#: lattice theorem to subgroups it has no instance for): when the declaration's
#: signature takes an instance argument at a binder position, the location says,
#: in one line, which of the passage's objects the declaration is instantiated at
#: and which instance or hypothesis makes it apply there.  A setting that neither
#: contains nor is contained in the passage's makes the relation ``relatedMatch``
#: (rule R5); a special case of it is ``narrowMatch`` (R3).
#: A location's ``step`` (the smoke-2 audit's FM3: a lemma used inside a proof
#: credited as a part of it): a ``hasPart`` or ``narrowMatch`` (``STEP_RELATIONS``)
#: says, in one line, what the declaration alone entails of the passage -- on a
#: proof, the displayed step it states; on any other passage, the clause, as the
#: declaration states it at the objects it applies to.
STEP_RELATIONS = frozenset({HAS_PART, NARROW_MATCH})
#: The conventions a ``closeMatch`` may differ from its passage by (rule R1; the
#: smoke-2 audit's FM4: closeMatch for a strict derivative, a Banach codomain, a
#: weaker statement): the location names one as its ``convention`` and says how in
#: its ``note``.  A strengthening, a generalisation or a weakening is none of them,
#: and since PD7 (judgment rules 2) neither is a junk value: a declaration that
#: agrees with the passage on the passage's domain is an ``exactMatch`` when every
#: case outside it is trivial and a ``broadMatch`` when one has real content, and a
#: ``closeMatch`` differs where both sides are defined.
CONVENTION_NOTATION = "notation"
CONVENTION_ENCODING = "encoding"
CONVENTION_JUNK_VALUE = "junk_value"
CONVENTION_NORMALISATION = "normalisation"
CONVENTION_RENAMING = "renaming"
CONVENTION_UNFOLDING = "unfolding"
CONVENTION_BUNDLING = "bundling"
CLOSE_MATCH_CONVENTIONS = frozenset({CONVENTION_NOTATION, CONVENTION_ENCODING,
                                     CONVENTION_NORMALISATION, CONVENTION_RENAMING,
                                     CONVENTION_UNFOLDING, CONVENTION_BUNDLING})
#: the conventions under judgment rules 1, ``junk_value`` among them: a run planned
#: under them is gated by this list, and a challenger's confirm that keeps such a
#: record's own ``junk_value`` stands (a challenge never raises a relation, and PD7
#: would make that record an ``exactMatch``)
LEGACY_CLOSE_MATCH_CONVENTIONS = CLOSE_MATCH_CONVENTIONS | {CONVENTION_JUNK_VALUE}
#: The words an ``exactMatch`` or ``closeMatch`` note may not say: each describes
#: generality or strength, which is ``broadMatch`` (R2), ``narrowMatch`` (R3) or a
#: weakening (R5), never a convention (R1) and never an exact match
#: (:func:`says_generality`).  A phrase is matched as whole words, case-folded; a
#: trailing ``*`` matches any ending of its last word ("strengthen*" is
#: "strengthens", "strengthening"; "generali*" is "generalises", "generalization",
#: "generality").  "strict" and "strictly" alone are not on it: they name a strict
#: inequality or a strictly monotone object as often as a strengthening, so the
#: strengthening is named by the phrase ("strict derivative", "strict
#: differentiability", "strictly differentiable").
CLOSE_MATCH_GENERALITY_PHRASES = ("stronger", "strengthen*", "weaker", "weaken*",
                                  "more general*", "generali*", "special case",
                                  "particular case", "broader", "narrower",
                                  "strict derivative*", "strict differentiab*",
                                  "strictly differentiab*")
#: the fields the judgment rules add to a location of a locate document (and to an
#: overturn's): the outcomes keep each one a document wrote, and a challenge task's
#: manifest shows them to the challenger; none is exported (the record set's format
#: is unchanged, references/locate-documents.md section 6).  ``conjunct_relation``
#: and ``tests`` are judgment rules 3's (``RULES_STATEMENT_CUT``, the statement cut
#: below); a document of an earlier version carries neither, so its rows are the ones
#: they always were
JUDGMENT_LOCATION_FIELDS = ("clauses", "instantiation", "step", "convention",
                            "conjunct_relation", "tests")
#: the longest one-line field a judgment rule asks of a location (``instantiation``,
#: ``step``), and the fewest words with a letter in it: "n/a", "-" or "none" is
#: no instantiation and no step
MAX_JUDGMENT_LINE_CHARS = 300
MIN_JUDGMENT_LINE_WORDS = 3
#: The closed list of phrases by which a strong record's own note (or its
#: ``instantiation``) admits a clause its declaration does not state -- synthetic
#: examples: "it does not state the size bound", "only proves existence", "the
#: converse is not included".  The gate refuses such a record under the judgment
#: rules, and the challenge plan refutes one by default whatever the run
#: (:func:`admits_missing_clause`).  Matched case-folded, as whole words, with the
#: apostrophe folded and a contraction spelled out ("doesn't" is "does not"); a
#: phrase is not counted when it is negated ("not only the existence"), when the
#: passage is its subject ("the passage does not cover n = 0", "not part of the
#: passage": a junk-value or normalisation note says what the passage leaves out),
#: or in a relative clause on what an "unlike ..." clause names.  It is a
#: tripwire, not a parser: a note it misreads is reworded to say what the
#: declaration does.
CLAUSE_GAP_PHRASES = ("does not state", "do not state", "not stated", "not part of",
                      "not a part of", "rather than asserting", "rather than stating",
                      "rather than proving", "only the existence", "only existence",
                      "only proves", "only states", "proves only", "states only",
                      "only one direction", "one direction only", "not the converse",
                      "converse is not", "without the converse", "without uniqueness",
                      "uniqueness is not", "does not cover", "do not cover", "not covered",
                      "not asserted", "not included", "omits", "omitted", "leaves out",
                      "left out", "does not prove", "does not assert", "does not establish")
#: The phrases by which an absent passage's rejected candidate is said to state
#: part of the passage, a case of it or a generalisation of it (the smoke-2 audit:
#: two of its three false supporting absences rejected the declaration that states
#: a clause, as "not the worked example" or "not the whole passage").  Such a
#: declaration is a ``hasPart``, ``narrowMatch`` or ``broadMatch`` location, so the
#: gate refuses the rejection on an absence under the judgment rules
#: (:func:`rejects_a_part`; matched as :data:`CLOSE_MATCH_GENERALITY_PHRASES` are).
CANDIDATE_PART_PHRASES = ("only part", "part of the passage", "one clause",
                          "one of the clauses", "not the whole", "not the full",
                          "special case", "not the worked example", "not the example",
                          "partially", "more general*", "generali*")

# --- the statement cut (docs/proposals/statement-cut.md): one passage per statement,
# a compound statement's conjuncts written by the inventory and cited by locate and the
# challenge, a coverage claim only as a bundle on a compound statement, and a versioned
# cut with a carry-over map.  These are the names the proposal's four packages build
# against.  Until its flip (the proposal's section 7) nothing binds a run: the gate and
# the planner read them, but each rule binds only a run whose gate.json or run.yaml
# names its version, and no planner writes one before the flip, so every run is planned,
# gated, tallied and exported as it was before them.
#: The first judgment rules version of the statement cut (gate rule 9; the proposal's
#: sections 1, 2 and 5): a passage's clause ids are the inventory's conjunct ids (or
#: ``SIMPLE_STATEMENT_CLAUSE`` for a simple statement) and no document writes its own
#: claim list; a coverage claim is a bundle; and the smoke-3 audit's failure-mode gate
#: rules bind.  ``JUDGMENT_RULES`` becomes this at the flip.
RULES_STATEMENT_CUT = 3
#: The first span-end rule version of the statement cut (gate rule 2's end check; the
#: proposal's section 3): a passage that gives no ``until`` ends at the earlier of its
#: extent (its block, and the displays its last sentence introduces) and the next
#: passage's start.  ``SPAN_END_RULE`` becomes this at the flip.
SPAN_END_STATEMENT_CUT = 2
#: The statement-cut rule (gate rule 10, the inventory's cut checks; the proposal's
#: section 3): the version a gate.json names as ``statement_cut_rule``.  ``cf
#: plan-locate`` writes it into every gate.json from the flip on; a gate.json without
#: the key is gated without rule 10, as every run planned before it is.
STATEMENT_CUT_RULE = 1
#: The status rule (the proposal's section 2): the version of the derivation a locate
#: run's outcomes are derived under, recorded in its ``run.yaml`` (``locate.status_rule``)
#: and in the seal of its record set.  Version 1 (``STATUS_RULE_D1``) is decision D1 as
#: parity round 3 reads it: a survived coverage claim acts as one more ``exactMatch``
#: location.  Version 2 (``STATUS_RULE_BUNDLE``) is the maintainer's answer of
#: 2026-10-21 to question 9: a survived bundle acts as one location of its weakest
#: part's relation, and a passage the challenge leaves with no counting location and no
#: absence is owed a hunt.  A run.yaml without the key is version 1, and
#: :func:`derive_status` defaults to it; ``cf plan-locate`` writes ``STATUS_RULE`` from
#: the flip on.
STATUS_RULE_D1 = 1
STATUS_RULE_BUNDLE = 2
STATUS_RULE = STATUS_RULE_BUNDLE
#: The closed list of compound shapes (the maintainer's answer of 2026-10-21 to question
#: 2): one statement whose conclusion is a conjunction the text does not separate.
#: Anything else that holds two results is two statements, and the inventory cuts it.
#: ``existence_uniqueness`` -- existence and uniqueness; ``iff`` -- an equivalence, or
#: the conditions of one equivalence; ``cases`` -- the cases of one law or one
#: definition, in one sentence, one display or one list; ``conclusions`` -- several
#: conclusions about one subject under one hypothesis; ``equations`` -- one display
#: that holds several equations (an equation chain is no compound: it is one statement,
#: its end-to-end equation).
SHAPE_EXISTENCE_UNIQUENESS = "existence_uniqueness"
SHAPE_IFF = "iff"
SHAPE_CASES = "cases"
SHAPE_CONCLUSIONS = "conclusions"
SHAPE_EQUATIONS = "equations"
#: the shapes in the maintainer's order, as a prompt lists them
COMPOUND_SHAPE_ORDER = (SHAPE_EXISTENCE_UNIQUENESS, SHAPE_IFF, SHAPE_CASES,
                        SHAPE_CONCLUSIONS, SHAPE_EQUATIONS)
COMPOUND_SHAPES = frozenset(COMPOUND_SHAPE_ORDER)
#: A compound statement's conjuncts (an inventory passage's ``conjuncts``): at least
#: ``MIN_CONJUNCTS`` and at most ``MAX_CONJUNCTS``, ids ``c1`` .. ``cN`` in order
#: (``CLAIM_ID``'s grammar), each one line of at most ``MAX_CLAIM_CHARS`` characters in
#: the inventory worker's own words; a simple statement has none.  A shape named in
#: ``SHAPE_CONJUNCT_COUNTS`` has exactly that many.
MIN_CONJUNCTS = 2
MAX_CONJUNCTS = 5
SHAPE_CONJUNCT_COUNTS = {SHAPE_EXISTENCE_UNIQUENESS: 2}
#: the one clause id of a simple statement: the statement itself, what a location's
#: ``clauses`` and a receipt's ``query_clauses`` name on a passage with no conjuncts
SIMPLE_STATEMENT_CLAUSE = "c1"
#: what a ``hasPart`` location of a compound statement bears to the conjunct(s) it names
#: (its ``conjunct_relation``, judged by R1-R3 and PD1-PD10 against the conjunct, not the
#: passage): the relations that state a conjunct, or a special case of it
CONJUNCT_RELATIONS = frozenset({EXACT_MATCH, CLOSE_MATCH, BROAD_MATCH, NARROW_MATCH})
#: the relations a bundle may have: its weakest part's (question 9), and every part of a
#: bundle states its conjuncts strongly, so a bundle's relation is a strong one
BUNDLE_RELATIONS = STRONG_RELATIONS
#: A strong location's two instantiation tests under judgment rules 3 (the smoke-3
#: audit's FM1/FM4 fix), its ``tests``: ``r2`` -- how the passage follows from the
#: declaration; ``r3`` -- why the declaration, at the passage's objects, needs nothing
#: the passage does not give.  Each is a one-line judgment field.
TWO_WAY_TEST_KEYS = ("r2", "r3")
#: Where the inventory cuts inside a sentence (the maintainer's answer to question 3):
#: after a semicolon, at one of these words, or at a comma and "and" followed by a new
#: subject -- never between a hypothesis and its conclusion, inside an iff, or between
#: conjuncts about one subject.  Prompt rules, rendered into the inventory prompt and
#: the labeler; no gate parses a sentence.
CUT_CONNECTIVES = (";", "since", "because", "so", "hence", "then", "moreover", ", and")
#: Why a locate worker ends a passage ``unresolved`` because of its cut (``cut_flag``),
#: under judgment rules 3: it states nothing a library could state, or it holds several
#: statements the inventory did not cut.  ``not_a_statement`` is also the token of the
#: consumer's "not a statement" ballot flag (the maintainer's answer to question 7),
#: which no stage here writes.
CUT_FLAG_NOT_A_STATEMENT = "not_a_statement"
CUT_FLAG_SEVERAL_STATEMENTS = "several_statements"
CUT_FLAGS = frozenset({CUT_FLAG_NOT_A_STATEMENT, CUT_FLAG_SEVERAL_STATEMENTS})
#: The coverage claim as a record of status rule 2 carries it: the three keys of
#: ``COVERAGE_RECORD_KEYS``, the bundle's ``relation`` (one of ``BUNDLE_RELATIONS``), its
#: ``conjuncts`` (``[{id, text, part}]``, each conjunct beside the location key of the
#: part that states it) and its ``bundle_key`` (``cflib.hashing.bundle_key``)
COVERAGE_RECORD_KEYS_BUNDLE = COVERAGE_RECORD_KEYS | {"relation", "conjuncts", "bundle_key"}
#: The words an ``exactMatch`` or ``closeMatch`` note may not say under judgment rules 3:
#: ``CLOSE_MATCH_GENERALITY_PHRASES`` and the four the smoke-3 audit's FM1/FM4 fix adds
#: (a closeMatch for a law that covers more than a finite table, for every prime or every
#: point, or for a differentiability hypothesis is a generalisation or a special case,
#: never a convention).  A run of an earlier version is gated by the earlier list.
CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT = CLOSE_MATCH_GENERALITY_PHRASES + (
    "table", "for all primes", "every point", "differentiable at")
#: The naturals-for-integers tripwire under judgment rules 3 (the smoke-3 audit's fix 2b):
#: a strong relation is refused when the passage's ``statement`` or a conjunct says one of
#: ``NAT_FOR_INT_INTEGER_WORDS`` and every number-typed binder of the declaration is ℕ,
#: unless a note says one of ``NAT_FOR_INT_NATURAL_WORDS``.  A word is matched as
#: ``CLOSE_MATCH_GENERALITY_PHRASES`` are (whole words, case-folded, a trailing ``*`` any
#: ending); a symbol (``ℤ``, ``ℕ``) anywhere, so "nℤ" and "ℕ-valued" are hits.
NAT_FOR_INT_INTEGER_WORDS = ("integer", "integers", "ℤ")
NAT_FOR_INT_NATURAL_WORDS = ("ℕ", "natural*")
#: What a re-cut does to one passage of the old cut, by the carry-over map (the
#: proposal's section 4; computed from spans and span text only): ``unchanged`` -- a
#: passage of the new cut has the same ``passage_key`` (the same text on the same page);
#: ``end_moved`` -- one new passage overlaps it and starts where it starts; ``split`` --
#: two or more new passages overlap it; ``merged`` -- one new passage overlaps it, starts
#: before it and holds the whole of it; ``reshaped`` -- one new passage overlaps it,
#: starting elsewhere and not holding it; ``removed`` -- no new passage overlaps it
#: (its text is still on the page, kept by no passage); ``changed`` -- the new
#: revision does not hold its text exactly once, so it cannot be placed there.  Only
#: ``unchanged`` carries a verdict, a gold label or a known answer; every other fate asks
#: again.  ``CARRY_NEW`` marks a new passage that overlaps no old one.
CARRY_UNCHANGED = "unchanged"
CARRY_END_MOVED = "end_moved"
CARRY_SPLIT = "split"
CARRY_MERGED = "merged"
CARRY_RESHAPED = "reshaped"
CARRY_REMOVED = "removed"
CARRY_CHANGED = "changed"
CARRY_FATES = frozenset({CARRY_UNCHANGED, CARRY_END_MOVED, CARRY_SPLIT, CARRY_MERGED,
                         CARRY_RESHAPED, CARRY_REMOVED, CARRY_CHANGED})
CARRY_NEW = "new"
#: What a re-cut does to one frozen gold row (``cf bench carry``, the proposal's
#: section 4.4, over the carry-over map, the old cut on the layer the gold is
#: labelled on): ``carried`` -- the new cut is on that layer too and the passages over
#: the row's span are the old ones, unchanged and at the same spans, so its labels
#: stand as they are; ``reanchor`` -- the same holds on another layer (a new revision
#: or rendering, the text over the span unchanged), so its labels stand and only its
#: span moves, to the report's ``anchor``; ``relabel`` -- anything else, and every row
#: across a labeler bump.  A gold row without a span is counted apart.
GOLD_CARRIED = "carried"
GOLD_REANCHOR = "reanchor"
GOLD_RELABEL = "relabel"
GOLD_CARRY_STATES = frozenset({GOLD_CARRIED, GOLD_REANCHOR, GOLD_RELABEL})
#: a locate task carries at most this many formal_math passages (section 3, L3b)
MAX_PASSAGES_PER_LOCATE_TASK = 8
#: a challenge task judges about this many records (section 5)
CHALLENGE_BATCH_RECORDS = 20
#: the export's quote caps (section 2.7): words of ``exact``, characters of each
#: of ``prefix`` and ``suffix``
EXPORT_QUOTE_WORDS = 12
EXPORT_CONTEXT_CHARS = 32
#: the record set's tokens (references/locate-documents.md section 6), the grammar its
#: consumer checks a seal against: ``set_id``, ``sourceset.id`` and a campaign id are
#: letters, digits and ``._:-``, at most 128 (a campaign id never starts
#: ``RUN_TOKEN_PREFIX``); a run in no campaign is sealed under ``run:<locate run>``
#: [``+<challenge run>``], whose run ids are letters, digits and ``._-``, at most 64 --
#: so a planner, ``cf sourceset create`` and the export refuse one id by one rule
RECORDSET_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
RUN_TOKEN_PREFIX = "run:"
RUN_TOKEN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
#: the reason an attempt-capped task's document is UNRESOLVED (section 6,
#: Convergence): written by the bridge, never by a worker
UNRESOLVED_ATTEMPT_CAP = "attempt_cap"
#: who wrote an UNRESOLVED document's ``unresolved.by``: the worker that could not
#: decide, or the bridge at the attempt cap
UNRESOLVED_BY_WORKER = "worker"
UNRESOLVED_BY = frozenset({UNRESOLVED_BY_WORKER, UNRESOLVED_ATTEMPT_CAP})

# --- the run machinery's bounds (sections 6 and 7); the host-side scripts that
# cannot import cflib (the bridge's agent call, the attempt cap, the gate) keep
# copies a test pins to these
#: one tend root holds at most this many tasks; a larger plan is campaign shards
MAX_TASKS_PER_ROOT = 500
#: the reviewer rejection whose count reaches this writes the UNRESOLVED document
LOCATE_ATTEMPT_CAP = 3
#: the bridge's flock semaphore: one slot below the account's 16 sessions
SESSION_SLOTS = 15
#: every agent call runs under ``timeout`` this long; expiry is infrastructure
AGENT_TIMEOUT = "45m"
#: which call a locate gate log line was (its ``caller``): tend's validation command
#: (``--changed-files``, the only call the log's readers count as an attempt), its
#: pre-merge command (``--changed-files --pre-merge``), an agent's or the bridge's own
#: ``--findings`` check, and a directory of documents (a harvest, a hand check)
GATE_CALLER_VALIDATION = "validation"
GATE_CALLER_PRE_MERGE = "pre_merge"
GATE_CALLER_FINDINGS = "findings_check"
GATE_CALLER_DIRECTORY = "directory"
GATE_CALLERS = (GATE_CALLER_VALIDATION, GATE_CALLER_PRE_MERGE, GATE_CALLER_FINDINGS,
                GATE_CALLER_DIRECTORY)

# --- the challenge plan (section 5): why a record is in a challenge task, and
# what the plan says it is owed
CHALLENGE_RULE_REFUTE = "refute_by_default"
CHALLENGE_RULE_HUNT = "hunt"
#: the location that sets a ``supporting`` passage's derived status, when its
#: relation is one of :data:`STRONG_RELATIONS`: required (the passage's status
#: rests on it alone)
CHALLENGE_RULE_BASIS = "basis"
CHALLENGE_RULE_SAMPLE = "sample"
CHALLENGE_RULE_NONE = "none"
CHALLENGE_RULES = frozenset({CHALLENGE_RULE_REFUTE, CHALLENGE_RULE_HUNT, CHALLENGE_RULE_BASIS,
                             CHALLENGE_RULE_SAMPLE, CHALLENGE_RULE_NONE})
#: the challenge plan's default sampling rates, each a fraction of its strata
#: (``cf plan-locate --sample-rate``, ``--strong-sample-rate``,
#: ``--absence-sample-rate``; run.yaml records the ones a plan used): the scope
#: and ``supporting`` demotions and the weak relations; the strong relations of
#: a ``supporting`` passage other than its basis; a ``supporting`` passage's
#: absence, which the challenger hunts
CHALLENGE_SAMPLE_RATE = 0.2
CHALLENGE_STRONG_SAMPLE_RATE = 0.5
CHALLENGE_ABSENCE_SAMPLE_RATE = 0.25
#: a record the plan put in a challenge task: its verdict is owed
CHALLENGE_REQUIRED = "required"
CHALLENGE_PLANNED = frozenset({CHALLENGE_REQUIRED, CHALLENGE_SAMPLED_OUT,
                               CHALLENGE_NOT_REQUIRED})

# --- the library index (references/locate-documents.md section 8): a normalised
# declaration's ``flags``
#: the probe's isProp failed, so the status is the conservative statement_only
INDEX_FLAG_IS_PROP_UNKNOWN = "is_prop_unknown"
#: the status is a published index's own word, accepted with --accept-published
INDEX_FLAG_PUBLISHED_STATUS = "published_status"
INDEX_FLAGS = frozenset({INDEX_FLAG_IS_PROP_UNKNOWN, INDEX_FLAG_PUBLISHED_STATUS})

# --- the benchmark (sections 8 and 9)
#: the gold tiers, never pooled, in the order a score and a comparison report them
BENCH_TIER_ORDER = ("G1", "G3", "GX", "G4", "P")
BENCH_TIERS = frozenset(BENCH_TIER_ORDER)
#: the tiers whose gold is labelled (a label file each); P's gold is the planted ablation
BENCH_LABELLED_TIERS = ("G1", "G3", "GX", "G4")
#: the tiers a pre-registered claim may come from (G1 only in the blind arm)
BENCH_CLAIM_TIERS = ("G1", "G3", "P")
#: the kinds of database a locate run's workers search (references/locate-documents.md
#: section 9): the full ``corpus.sqlite``, the G1-blind ``corpus.blind.sqlite``, a
#: planted (module-ablated) one for the P tier; written into candidates files
#: (``corpus_db.kind``), gate.json and run.yaml (``tools_corpus_db.kind``) and score pins
TOOLS_DB_FULL = "full"
TOOLS_DB_BLIND = "blind"
TOOLS_DB_PLANTED = "planted"
TOOLS_DB_KINDS = (TOOLS_DB_FULL, TOOLS_DB_BLIND, TOOLS_DB_PLANTED)
#: G1 is scored in two arms that are never pooled (section 8.1)
BENCH_ARM_ASSISTED = "assisted"
BENCH_ARM_BLIND = "blind"
BENCH_ARMS = frozenset({BENCH_ARM_ASSISTED, BENCH_ARM_BLIND})
BENCH_SPLITS = frozenset({"dev", "eval", "test"})
#: what the A/A's agreement says of one metric, on the paired interval of (replicate -
#: baseline) against its tolerance (references/locate-bench.md section 9, rule 5): the
#: whole interval lies past it (``differs``, the runs disagree), inside it (``within``,
#: agreement shown), or reaches past it without lying wholly beyond (``unresolved``: no
#: difference shown, and no agreement either -- the burden of proof is on the difference)
BENCH_AA_DIFFERS = "differs"
BENCH_AA_WITHIN = "within"
BENCH_AA_UNRESOLVED = "unresolved"
BENCH_AA_VERDICTS = frozenset({BENCH_AA_DIFFERS, BENCH_AA_WITHIN, BENCH_AA_UNRESOLVED})
#: why a gold-formalized passage did not end ``formalized`` (the safety metric's
#: cause breakdown, section 8.2); unresolved and gate-failed are misses
FNF_CAUSES = frozenset({"inventory_miss", "scope_demotion", "absence", "weak_relation",
                        "unresolved", "gate_failed"})
#: the mechanical error code of every miss (section 9, step 1)
ERROR_CODES = frozenset({"E-INVENTORY-MISS", "E-RETRIEVAL-MISS", "E-REJECTED-CORRECT",
                         "E-WRONG-RELATION", "E-FALSE-ABSENCE", "E-WRONG-SCOPE",
                         "E-UNRESOLVED", "E-MEMORY"})
#: What one call of the frozen labeler does (workflows/locate-label.workflow.js,
#: references/locate-bench.md): ``anchor`` a G1 entry to a passage and adjudicate
#: its relation, ``label`` a G3 span blind, ``calibrate`` the hunters' recall
#: tag-blind on anchored G1 passages, ``inventory`` an article exhaustively (GX).
BENCH_LABEL_MODES = frozenset({"anchor", "label", "calibrate", "inventory"})


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


def is_bold_label(text: str) -> bool:
    """Is a bold run's text only a paragraph label, in :data:`BOLD_LABELS`?

    Case-folded, with its punctuation stripped and an enumerator after the label
    word dropped ("Case 2:", "Step 1a.", "Example (iii)"); a word of
    :data:`BOLD_LABELS_PUNCTUATED` counts only when a label's punctuation was
    there ("Solution:", "Problem 3."), never for an enumerator alone, which is
    how a concept is named ("Axiom A", "Problem P", "property (T)"); and a proof
    of something ("Proof of the lemma:", "Proof (sketch).") is a label too.  A
    label word followed by a parenthesised NAME ("Theorem (Widget lemma).") is
    no label: it names a statement, and its paragraph is a passage.

    The renderer's finders read this rule (``sources.render_html``), so any
    change to it, or to the two label sets, is a new renderer id:
    ``tests/test_locate_sources.py`` pins the rule's fingerprint to the id.
    """
    spaced = " ".join(str(text or "").split()).casefold()
    bare = spaced.strip(_LABEL_TRIM)
    punctuated = bare != spaced
    core = _LABEL_ENUMERATOR.sub("", bare).strip(_LABEL_TRIM)
    if core in BOLD_LABELS or (punctuated and core in BOLD_LABELS_PUNCTUATED):
        return True
    return core.startswith("proof of ") or core.startswith("proof (")


def fold_words(text) -> str:
    """``text`` as the judgment rules' phrase lists read it.

    Case-folded, the typographic apostrophe folded, a contraction spelled out
    ("doesn't" is "does not", "can't" is "can not") and whitespace collapsed.
    ``gate/verify_locate.py`` inlines the same function.
    """
    folded = str(text or "").replace("’", "'").casefold()
    folded = re.sub(r"(?<![\w'])can't(?![\w'])", "can not", folded)
    folded = re.sub(r"(?<![\w'])won't(?![\w'])", "will not", folded)
    folded = re.sub(r"(?<![\w'])cannot(?![\w'])", "can not", folded)
    folded = re.sub(r"(?<=\w)n't(?![\w'])", " not", folded)
    return " ".join(folded.split())


def phrase_pattern(phrase: str) -> re.Pattern:
    """A phrase of a judgment-rule list as a pattern: whole words, a trailing ``*`` any ending."""
    wild = phrase.endswith("*")
    words = phrase.rstrip("*").split()
    body = r"\s".join(re.escape(word) for word in words)
    return re.compile(rf"(?<![\w']){body}" + (r"[\w']*" if wild else r"(?![\w'])"))


def _phrase_in(text: str, phrases: Iterable[str]) -> str | None:
    """The first of ``phrases`` in ``text`` (:func:`fold_words`, :func:`phrase_pattern`)."""
    folded = fold_words(text)
    for phrase in phrases:
        if phrase_pattern(phrase).search(folded):
            return phrase
    return None


#: what ends a clause, for :func:`admits_missing_clause`'s subject test
_CLAUSE_END = re.compile(r"[.;:!?,()\[\]]")
_PASSAGE_WORD = re.compile(r"(?<![\w'])(?:passage|article)(?![\w'])")
_PASSAGE_AFTER = re.compile(r"\s*(?:by |in |of |from |for )?(?:the |this )(?:passage|article)"
                            r"(?![\w'])")
_NEGATED = re.compile(r"(?<![\w'])not\s*\Z")
_RELATIVE = re.compile(r"\s*(?:which|that|whose|where)(?![\w'])")


def _gap_exempt(folded: str, start: int, end: int, phrase: str) -> bool:
    """A :data:`CLAUSE_GAP_PHRASES` hit that admits no gap of the declaration's.

    Negated ("not only the existence"); the passage its subject, earlier in its
    clause ("the passage does not cover n = 0") or its object right after it
    ("not part of the passage"); or a relative clause on what an "unlike ..."
    clause names ("unlike its corollary, which does not state the bound").
    """
    before, after = folded[:start], folded[end:]
    if phrase.startswith("only") and _NEGATED.search(before):
        return True
    cuts = [m.end() for m in _CLAUSE_END.finditer(before)]
    clause_start = cuts[-1] if cuts else 0
    clause = before[clause_start:]
    stop = _CLAUSE_END.search(after)
    if _PASSAGE_WORD.search(clause) or _PASSAGE_AFTER.match(after[:stop.start() if stop
                                                                    else len(after)]):
        return True
    if len(cuts) >= 1 and _RELATIVE.match(clause):
        previous = before[cuts[-2] if len(cuts) >= 2 else 0:clause_start - 1]
        if previous.strip().startswith("unlike"):
            return True
    return False


def admits_missing_clause(note) -> str | None:
    """The :data:`CLAUSE_GAP_PHRASES` phrase a record's note admits a missing clause by.

    A strong relation (``exactMatch``, ``closeMatch``, ``broadMatch``) says the
    declaration states every clause of its passage (rule R4); a note that says
    the declaration "does not state" one, or "only proves existence", says the
    opposite, and the record is ``hasPart`` at most.  Returns the first phrase of
    the list with a hit :func:`_gap_exempt` does not set aside, or None.
    """
    folded = fold_words(note)
    for phrase in CLAUSE_GAP_PHRASES:
        for hit in phrase_pattern(phrase).finditer(folded):
            if not _gap_exempt(folded, hit.start(), hit.end(), phrase):
                return phrase
    return None


def says_generality(note) -> str | None:
    """The :data:`CLOSE_MATCH_GENERALITY_PHRASES` phrase an exact or close note says, or None.

    An ``exactMatch`` states its passage and a ``closeMatch`` differs from it by a
    convention only (rule R1): a note that calls the declaration "stronger",
    "more general" or "a special case" describes a ``broadMatch``, a
    ``narrowMatch`` or a weakening (R2, R3, R5), and the record is refused.
    """
    return _phrase_in(note, CLOSE_MATCH_GENERALITY_PHRASES)


def rejects_a_part(reason) -> str | None:
    """The :data:`CANDIDATE_PART_PHRASES` phrase a candidate's rejection reason says, or None."""
    return _phrase_in(reason, CANDIDATE_PART_PHRASES)


def locate_mode(mode: str) -> bool:
    """True for the three locate presets' modes (they plan from a sourceset, not a companion)."""
    return mode in LOCATE_MODES


def decide_proof_status(*, wanted: bool, axioms: Iterable[str], is_prop: bool) -> str:
    """A declaration's ``proof_status``, decided in the ``PROOF_STATUSES`` order.

    docs/proposals/wikipedia-locate.md section 2.4: the first rule that applies
    wins, whatever the declaration's kind.

    1. ``wanted`` -- the type is a ``ProofWanted``/``DefWanted``-style wrapper
       (``wanted`` is the probe's finding, or a source scan's on a toolchain
       whose ``proof_wanted`` adds no constant);
    2. ``statement_only`` -- the axiom closure contains ``sorryAx``;
    3. ``axiom_dependent`` -- it contains an axiom outside ``STANDARD_AXIOMS``;
    4. ``proved`` -- the type is a ``Prop``;
    5. ``definition`` -- data: types, functions, structures, classes, instances.

    A ``Prop``-VALUED definition (``def RiemannHypothesis : Prop``) has a type
    that is not itself a ``Prop`` -- its type is ``Prop`` -- so it is a
    ``definition``; the index records its ``statement`` flag beside it, which
    is what :func:`derive_status` reads.
    """
    closure = {str(a) for a in axioms}
    if wanted:
        return PROOF_WANTED
    if SORRY_AXIOM in closure:
        return PROOF_STATEMENT_ONLY
    if closure - STANDARD_AXIOMS:
        return PROOF_AXIOM_DEPENDENT
    if is_prop:
        return PROOF_PROVED
    return PROOF_DEFINITION


#: The rank of a derived status, best first, for "the best such location".
_STATUS_RANK = {status: rank for rank, status in enumerate(DERIVED_STATUSES)}
_PART = frozenset({HAS_PART, NARROW_MATCH})
_UNFINISHED = frozenset({PROOF_WANTED, PROOF_STATEMENT_ONLY, PROOF_AXIOM_DEPENDENT})
#: The proof statuses a ``Prop``-valued definition (the ``statement`` flag) can
#: have: ``decide_proof_status`` gives a definition every status but ``proved``,
#: which is a declaration whose type IS a ``Prop`` -- a theorem, not a definition.
_STATEMENT_PROOF_STATUSES = frozenset({PROOF_DEFINITION}) | _UNFINISHED
#: the relations that say a declaration states the passage itself
_STATES = frozenset({EXACT_MATCH, CLOSE_MATCH})
#: what a survived coverage claim acts as (decision D1, read in parity round 3):
#: ONE more exactMatch location, finished when every part is (``proved``, or data:
#: a ``definition`` without the statement flag) -- a proved declaration to the
#: theorem row and a definition to the definition row, so formalized on either --
#: and otherwise a statement_only one, statement_only on both rows.  Each pair is
#: the ``(proof_status, statement)`` the row reads, theorem row first.
_COVERAGE_FINISHED = ((PROOF_PROVED, False), (PROOF_DEFINITION, False))
_COVERAGE_UNFINISHED = ((PROOF_STATEMENT_ONLY, False), (PROOF_STATEMENT_ONLY, False))


def _theorem_row(relation: str, proof_status: str, statement: bool) -> str:
    """One location of a theorem or conjecture passage, by the section 2.6 table."""
    if relation in _PART:
        return STATUS_PARTIAL
    # exactMatch, closeMatch, broadMatch
    if statement:                       # a Prop-valued definition never formalizes it
        return STATUS_STATEMENT_ONLY
    if proof_status == PROOF_PROVED:
        return STATUS_FORMALIZED
    if proof_status in _UNFINISHED:
        return STATUS_STATEMENT_ONLY
    # DATA (a definition without the statement flag): an exact or close match is
    # an equivalence, isomorphism, bijection or structure-valued construction that
    # states the theorem -- two quotient rings shown isomorphic, as a RingEquiv -- and
    # formalizes it (the maintainer's decision D1); the table's "broadMatch to a
    # definition" stays partial
    if relation in _STATES:
        return STATUS_FORMALIZED
    return STATUS_PARTIAL


def _definition_row(relation: str, proof_status: str) -> str:
    """One location of a definition passage, by the section 2.6 table."""
    if relation in _PART or relation == BROAD_MATCH:
        return STATUS_PARTIAL
    # exactMatch, closeMatch
    if proof_status == PROOF_DEFINITION:
        return STATUS_FORMALIZED
    if proof_status in _UNFINISHED:
        return STATUS_STATEMENT_ONLY
    # a proved theorem stating a definition passage: not in the table, partial
    return STATUS_PARTIAL


def _field(location, name: str, default=None):
    if isinstance(location, dict):
        return location.get(name, default)
    return getattr(location, name, default)


_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _coverage_parts(coverage, by_key: dict, *,
                    status_rule: int = STATUS_RULE_D1) -> list[tuple[str, bool]]:
    """A coverage claim's parts as ``(proof_status, statement)``, or refuse the claim.

    The claim is the one a record carries: a mapping with exactly the keys
    ``COVERAGE_RECORD_KEYS`` (or an object with those attributes) -- ``complete``
    (``True``), ``parts`` (the ``location_key`` of each part: at least
    ``MIN_COVERAGE_PARTS``, distinct) and ``challenge`` (one of
    ``CHALLENGE_VERDICTS``).  A claim no challenge has judged yet (owed) has no
    challenge value and is no record's claim: its holder passes none, since it
    upgrades nothing.  Every part must be one of the passage's locations, and a
    ``hasPart`` one: a claim naming anything else says nothing a record can carry.
    Under status rule 2 the claim is a bundle (:func:`_bundle_fields`): its keys lie
    within ``COVERAGE_RECORD_KEYS_BUNDLE`` and it carries its ``relation``.
    """
    if isinstance(coverage, (list, tuple, set, str, bytes)):
        raise CfError(f"a passage's coverage claim is one {{complete, parts, challenge}} "
                      f"object, not a {type(coverage).__name__}",
                      hint="a passage carries at most one claim")
    allowed = COVERAGE_RECORD_KEYS_BUNDLE if status_rule == STATUS_RULE_BUNDLE \
        else COVERAGE_RECORD_KEYS
    if isinstance(coverage, dict):
        extra = sorted(set(coverage) - allowed)
        if extra and status_rule == STATUS_RULE_BUNDLE:
            raise CfError(f"a bundle is {{complete, parts, challenge, relation, conjuncts, "
                          f"bundle_key}}; unexpected {', '.join(map(str, extra))}",
                          hint="a record of status rule 2 carries its claim's six keys and no "
                               "others (references/locate-documents.md section 6)")
        if extra:
            raise CfError(f"a coverage claim is {{complete, parts, challenge}}; unexpected "
                          f"{', '.join(map(str, extra))}",
                          hint="the claim's note is not exported; a record carries the "
                               "three keys and no others (a bundle's relation is status rule "
                               "2's: pass status_rule=2 for a record set of that rule)")
    if _field(coverage, "complete") is not True:
        raise CfError(f"a coverage claim says complete: true, not "
                      f"{_field(coverage, 'complete')!r}",
                      hint="a passage claims coverage only when its parts state every clause; "
                           "otherwise it carries no claim")
    challenge = _field(coverage, "challenge")
    if challenge is None or not isinstance(challenge, str):
        raise CfError(f"a coverage claim carries its challenge value, not {challenge!r}",
                      hint="an owed claim (no challenge judged it yet) upgrades nothing and is "
                           "never exported: pass no claim until it has one")
    check(challenge, CHALLENGE_VERDICTS, "coverage challenge")
    parts = _field(coverage, "parts")
    if not isinstance(parts, (list, tuple)) or not all(isinstance(p, str) and p
                                                       for p in parts):
        raise CfError(f"a coverage claim's parts are the location keys of its parts, not "
                      f"{parts!r}")
    if len(set(parts)) != len(parts) or len(parts) < MIN_COVERAGE_PARTS:
        raise CfError(f"a coverage claim names at least {MIN_COVERAGE_PARTS} distinct parts, "
                      f"not {len(set(parts))}",
                      hint="one hasPart location stating the whole passage is an exactMatch")
    out = []
    for key in parts:
        location = by_key.get(key)
        if location is None or _field(location, "relation") != HAS_PART:
            raise CfError(f"coverage part {key[:12]} is not a hasPart location of the passage",
                          hint="a coverage claim names its passage's hasPart locations only; "
                               "a challenge that refutes or downgrades a part leaves the claim "
                               "to be challenged again")
        out.append((str(_field(location, "proof_status") or ""),
                    bool(_field(location, "statement", False))))
    if status_rule == STATUS_RULE_BUNDLE:
        _bundle_fields(coverage, list(parts))
    return out


def _bundle_fields(coverage, parts: list[str]) -> str:
    """A bundle's ``relation``, once its status-rule-2 fields are checked; refuse otherwise.

    docs/proposals/statement-cut.md sections 2.1 and 2.3: ``relation`` is required and
    one of ``BUNDLE_RELATIONS`` (the weakest part's, computed by the outcomes, never
    written by a document).  ``conjuncts`` and ``bundle_key`` are what the record set
    carries beside it (section 2.4), each checked when given: the conjuncts are
    ``MIN_CONJUNCTS``..``MAX_CONJUNCTS`` objects ``{id, text, part}`` in id order
    ``c1``..``cN``, each part one of the claim's ``parts``, and every part stating at
    least one (so, with two parts or more, none states them all: PD6); the bundle key
    is 64 lowercase hex (``cflib.hashing.bundle_key``).
    """
    relation = _field(coverage, "relation")
    if relation is None:
        raise CfError("a bundle carries its relation, its weakest part's",
                      hint="under status rule 2 the outcomes put the bundle's relation on the "
                           "claim (docs/proposals/statement-cut.md section 2.1)")
    if relation not in BUNDLE_RELATIONS:
        raise CfError(f"a bundle's relation is one of {', '.join(sorted(BUNDLE_RELATIONS))}, "
                      f"not {relation!r}",
                      hint="every part of a bundle states its conjuncts strongly, so the "
                           "weakest part's relation is a strong one")
    conjuncts = _field(coverage, "conjuncts")
    if conjuncts is not None:
        if not isinstance(conjuncts, (list, tuple)) or \
                not MIN_CONJUNCTS <= len(conjuncts) <= MAX_CONJUNCTS:
            raise CfError(f"a bundle's conjuncts are {MIN_CONJUNCTS} to {MAX_CONJUNCTS} "
                          f"{{id, text, part}} objects, not {conjuncts!r}")
        named = set()
        for n, conjunct in enumerate(conjuncts, 1):
            if not isinstance(conjunct, dict) or set(conjunct) != {"id", "text", "part"}:
                raise CfError(f"a bundle's conjunct is {{id, text, part}}, not {conjunct!r}")
            if conjunct["id"] != f"c{n}":
                raise CfError(f"a bundle's conjuncts are c1..c{len(conjuncts)} in order; "
                              f"conjunct {n} is {conjunct['id']!r}")
            if not isinstance(conjunct["text"], str) or not conjunct["text"].strip():
                raise CfError(f"conjunct {conjunct['id']} has no text")
            if conjunct["part"] not in parts:
                raise CfError(f"conjunct {conjunct['id']} names part "
                              f"{str(conjunct['part'])[:12]}, which is not one of the bundle's")
            named.add(conjunct["part"])
        idle = [p for p in parts if p not in named]
        if idle:
            raise CfError(f"bundle part {idle[0][:12]} states no conjunct",
                          hint="a bundle's parts partition its conjuncts, each stating at "
                               "least one, so no part states them all (PD6)")
    key = _field(coverage, "bundle_key")
    if key is not None and (not isinstance(key, str) or not _HEX64.match(key)):
        raise CfError(f"a bundle_key is 64 lowercase hex, not {key!r}")
    return relation


def _part_finished(proof_status: str, statement: bool) -> bool:
    """A coverage part that leaves its clause finished: ``proved``, or data.

    Data is a ``definition`` without the ``statement`` flag (decision D1's
    second half: data that states a theorem formalizes it).  Unfinished is
    everything else: ``statement_only``, ``wanted``, ``axiom_dependent``, and a
    ``Prop``-valued definition, which states its clause and proves nothing.
    """
    return proof_status == PROOF_PROVED or (proof_status == PROOF_DEFINITION and not statement)


def _coverage_location(parts: list[tuple[str, bool]]) -> tuple[tuple[str, bool], ...]:
    """The one ``exactMatch`` location a SURVIVED coverage claim acts as.

    The maintainer's decision D1 as parity round 3 reads it (references/
    vocabulary.md section 1.11, questions Q1-Q3 resolved): the claim is
    finished when every part is (:func:`_part_finished`) -- then it is a
    ``proved`` declaration to the theorem row and a ``definition`` to the
    definition row, so it formalizes the passage on either -- and otherwise it
    is a ``statement_only`` one, which gives ``statement_only`` on both rows.
    Returns the ``(proof_status, statement)`` each row reads, theorem row first.
    """
    if all(_part_finished(status, statement) for status, statement in parts):
        return _COVERAGE_FINISHED
    return _COVERAGE_UNFINISHED


def _location_status(passage_kind: str, relation: str, theorem_reads: tuple[str, bool],
                     definition_reads: tuple[str, bool]) -> str:
    """One location's status: its passage kind's row, or the better of both rows.

    ``theorem_reads`` and ``definition_reads`` are the ``(proof_status,
    statement)`` each row reads -- the declaration's own, or what a survived
    coverage claim acts as (:func:`_coverage_location`).
    """
    rows = []
    if passage_kind in THEOREM_PASSAGE_KINDS | EITHER_PASSAGE_KINDS:
        rows.append(_theorem_row(relation, *theorem_reads))
    if passage_kind in DEFINITION_PASSAGE_KINDS | EITHER_PASSAGE_KINDS:
        rows.append(_definition_row(relation, definition_reads[0]))
    return min(rows, key=_STATUS_RANK.__getitem__)


def derive_status(passage_kind: str, locations: Iterable, absent: bool,
                  coverage=None, *, status_rule: int = STATUS_RULE_D1) -> str | None:
    """A passage's derived status: the Literature Links ``status`` of its records.

    docs/proposals/wikipedia-locate.md section 2.6, computed once per passage
    after challenge.  ``locations`` are the passage's surviving locations, each
    a mapping (or an object) with ``relation`` (``LOCATE_RELATIONS``),
    ``proof_status`` (``PROOF_STATUSES``, joined from the library index), an
    optional ``statement`` flag (the declaration is a ``Prop``-valued
    definition) and, for a location a coverage claim names, its
    ``location_key``.  ``absent`` says the passage has a checked absence.
    ``coverage`` is the passage's coverage claim, or ``None`` (the default: the
    three-argument call is unchanged) -- exactly the claim a record carries,
    ``{complete: true, parts: [location key, ...], challenge}``, its parts
    ``hasPart`` locations of ``locations`` and its challenge one of
    ``CHALLENGE_VERDICTS``.  A claim no challenge has judged yet (owed) is not
    passed: it upgrades nothing, and no record carries it.

    * ``formalized`` -- a theorem or conjecture passage with an ``exactMatch``,
      ``closeMatch`` or ``broadMatch`` to a ``proved`` declaration, or an
      ``exactMatch`` or ``closeMatch`` to DATA (a ``definition`` without the
      ``statement`` flag: an equivalence, an isomorphism, a structure-valued
      construction that states the theorem); a definition passage with an
      ``exactMatch`` or ``closeMatch`` to a ``definition``; and any passage
      with a coverage claim whose challenge is ``survived`` and whose every part
      is finished -- ``proved``, or data;
    * ``statement_only`` -- the best such location is ``statement_only``,
      ``wanted`` or ``axiom_dependent``, or is a ``Prop``-valued definition
      stating a theorem passage; or any passage's survived coverage claim with
      an unfinished part (``statement_only``, ``wanted``, ``axiom_dependent``,
      or a ``Prop``-valued definition);
    * ``partial`` -- only ``hasPart`` or ``narrowMatch``, or ``broadMatch`` to a
      definition, with no coverage claim that upgrades them;
    * ``not_formalized`` -- a checked absence, possibly beside ``relatedMatch``
      locations;
    * ``None`` -- unresolved: no location that counts and no absence.  It is
      never ``not_formalized``.

    A survived claim is ONE more ``exactMatch`` location, judged by the
    passage's row (both rows for an example or ``other`` passage, the better
    winning) like any other: a ``proved`` declaration to the theorem row and a
    ``definition`` to the definition row when every part is finished, a
    ``statement_only`` declaration otherwise (:func:`_coverage_location`; the
    maintainer's decision D1 as parity round 3 reads it).

    The best status over all the evidence wins.  ``relatedMatch`` never counts
    towards a status, and a coverage claim that is not ``survived`` (pending,
    not required, sampled out, or downgraded; an owed claim is not passed and a
    refute removes the claim) adds nothing.  The cases neither table names
    are decided here, each conservatively (references/vocabulary.md section
    1.11): a definition passage matched exactly to a proved theorem is
    ``partial``; an example or ``other`` passage takes the better of the two
    rows; and a passage whose only locations are ``relatedMatch``, with no
    checked absence, is ``None``
    -- the state a challenge leaves when it downgrades a passage's one
    location to ``relatedMatch``: nothing says it is formalized and nobody
    checked that it is absent, so it counts as unresolved (a miss for the
    safety metric, never ``not_formalized``).

    Refused (``CfError``), because no consistent record can say them: a checked
    absence beside a location that counts; a ``statement`` flag on a ``proved``
    declaration (``decide_proof_status`` gives a ``Prop``-valued definition every
    status but ``proved``); a ``statement`` that is not a bool; one
    ``location_key`` on two locations; a coverage claim that is not ``complete:
    true``, names fewer than two distinct parts, names a part that is not one of
    the passage's ``hasPart`` locations, has no challenge value (``None``: owed)
    or a key outside ``COVERAGE_RECORD_KEYS`` (a ``note``: the record format has
    none); and any value outside the vocabulary -- a ``refuted`` challenge
    among them, since a refute removes the claim.

    ``status_rule`` (keyword only) is the version of this derivation a locate
    run's outcomes are derived under (``STATUS_RULE_D1``, the default, or
    ``STATUS_RULE_BUNDLE``; docs/proposals/statement-cut.md section 2.3).  Status
    rule 1 is everything above, unchanged in every case of
    ``tests/data/locate/status-parity.json``, and still refuses a claim with any
    key outside ``COVERAGE_RECORD_KEYS``, a ``relation`` among them.  Under status
    rule 2 the claim is a **bundle** as a record of that rule carries it: keys
    within ``COVERAGE_RECORD_KEYS_BUNDLE``, its ``relation`` required and one of
    ``BUNDLE_RELATIONS`` (its weakest part's), and its optional ``conjuncts`` and
    ``bundle_key`` checked (:func:`_bundle_fields`).  A survived bundle acts as
    one more location of THAT relation -- where status rule 1 makes it one more
    ``exactMatch`` -- with the same fixed stand-in for its proof status
    (:func:`_coverage_location`), so a bundle of broader parts on a definition,
    construction or notation passage is ``partial``, as one broader location is
    (PD1).  Everything else is status rule 1; the cases are
    ``tests/data/locate/status-parity-2.json``.
    """
    if type(status_rule) is not int or status_rule not in (STATUS_RULE_D1,
                                                            STATUS_RULE_BUNDLE):
        raise CfError(f"status rule {status_rule!r} is not one this code derives by",
                      hint=f"status rules {STATUS_RULE_D1} (decision D1) and "
                           f"{STATUS_RULE_BUNDLE} (the bundle's relation) are defined; a "
                           "run.yaml without locate.status_rule is 1")
    check(passage_kind, PASSAGE_KINDS, "passage kind")
    best: str | None = None
    counted: list[str] = []
    by_key: dict = {}
    for location in locations:
        relation = check(str(_field(location, "relation") or ""), LOCATE_RELATIONS,
                         "location relation")
        proof_status = check(str(_field(location, "proof_status") or ""), PROOF_STATUS_SET,
                             "location proof_status")
        statement = _field(location, "statement", False)
        if statement is None:
            statement = False
        if not isinstance(statement, bool):
            raise CfError(f"a location's statement flag must be true or false, not "
                          f"{statement!r}",
                          hint="the library index sets it on a Prop-valued definition")
        if statement and proof_status not in _STATEMENT_PROOF_STATUSES:
            raise CfError(f"a {proof_status} declaration with the statement flag set",
                          hint="the flag marks a Prop-valued DEFINITION, whose proof_status "
                               "is definition, wanted, statement_only or axiom_dependent; "
                               "re-join proof_status and the flag from the library index")
        key = _field(location, "location_key")
        if isinstance(key, str) and key:
            if key in by_key:
                raise CfError(f"location_key {key[:12]} appears twice in the passage's locations",
                              hint="a location_key names one declaration at one commit, and a "
                                   "passage has one location per declaration (gate rule 6)")
            by_key[key] = location
        if relation == RELATED_MATCH:
            continue
        status = _location_status(passage_kind, relation, (proof_status, statement),
                                  (proof_status, statement))
        counted.append(relation)
        if best is None or _STATUS_RANK[status] < _STATUS_RANK[best]:
            best = status
    if absent and counted:
        raise CfError(f"a checked absence beside a {counted[0]} location",
                      hint="an absence may sit beside relatedMatch locations only; "
                           "drop the absence or downgrade the location")
    if coverage is not None:
        parts = _coverage_parts(coverage, by_key, status_rule=status_rule)
        if _field(coverage, "challenge") == CHALLENGE_SURVIVED:
            # status rule 1: one more exactMatch (decision D1); status rule 2: one more
            # location of the bundle's own relation, its weakest part's
            relation = (str(_field(coverage, "relation")) if status_rule == STATUS_RULE_BUNDLE
                        else EXACT_MATCH)
            status = _location_status(passage_kind, relation, *_coverage_location(parts))
            if best is None or _STATUS_RANK[status] < _STATUS_RANK[best]:
                best = status
    if best is not None:
        return best
    return STATUS_NOT_FORMALIZED if absent else None
