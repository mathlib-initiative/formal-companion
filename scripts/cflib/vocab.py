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
#: ``survived``: challenged and confirmed with a quoted witness;
#: ``downgraded``: challenged, and the relation (or outcome) was weakened;
#: ``not_required``: outside every challenge rule; ``sampled_out``: inside the
#: stratified sample's frame, not drawn.  Nothing leaves a workspace without one
#: (hard rule 5, per record).
CHALLENGE_VERDICTS = frozenset({CHALLENGE_SURVIVED, CHALLENGE_DOWNGRADED,
                                CHALLENGE_NOT_REQUIRED, CHALLENGE_SAMPLED_OUT})
#: the relations a stratified challenge sample re-judges as weak (section 5)
WEAK_RELATIONS = frozenset({RELATED_MATCH, NARROW_MATCH, HAS_PART})

# --- the locate documents (references/locate-documents.md): the values the
# stages exchange, defined once here so the renderer, the planner, the gate and
# the bench read one set
#: the ``schema`` of every inventory, locate and challenge document
LOCATE_DOCUMENT_SCHEMA = "cf-locate/1"
#: the renderer of every web source of the first corpus (section 2.1); a source's
#: ``renderer`` field, and a pin of every run, gate and score document.
#: ``web-html/1`` kept style and script text in the layer and collapsed ASCII
#: whitespace only; ``web-html/2`` is the Literature Links rule
#: (scripts/sources/render_html.py states it in full)
RENDERER_WEB_HTML = "web-html/2"
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
#: ... and these only with a label's punctuation or enumerator ("Solution:",
#: "Problem 3."), since bare they may be the term a sentence defines.
BOLD_LABELS_PUNCTUATED = frozenset({
    "solution", "answer", "problem", "question", "input", "output", "statement",
    "observation", "axiom", "axioms", "fact", "result", "construction", "algorithm",
    "property", "properties", "assumption", "assumptions", "special case", "idea"})
_LABEL_TRIM = " \t.:;,!?\u2013\u2014-"
#: an enumerator a label may carry after its word: 2, 2.1, 3a, iv, (b), (2)
_LABEL_ENUMERATOR = re.compile(r"(?:\s+(?:\(?(?:\d+(?:\.\d+)*[a-z]?|[ivxlc]+|[a-z])\)?))+$")
#: a name a label may carry in parentheses: "Theorem (Widget lemma)."
_LABEL_NAME = re.compile(r"\s*\([^()]*\)$")
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
#: ``overturn`` change it (``downgraded``).
CHALLENGE_TARGETS = frozenset({"location", "absence", "unresolved", "scope", "prominence"})
CHALLENGE_KEEP = frozenset({"confirm", "uphold"})
CHALLENGE_CHANGE = frozenset({"downgrade", "refute", "overturn"})
CHALLENGE_ACTIONS = CHALLENGE_KEEP | CHALLENGE_CHANGE
#: an absence's receipt lists at least this many distinct queries (gate rule 5)
MIN_ABSENCE_QUERIES = 3
#: a locate task carries at most this many formal_math passages (section 3, L3b)
MAX_PASSAGES_PER_LOCATE_TASK = 8
#: a challenge task judges about this many records (section 5)
CHALLENGE_BATCH_RECORDS = 20
#: the export's quote caps (section 2.7): words of ``exact``, characters of each
#: of ``prefix`` and ``suffix``
EXPORT_QUOTE_WORDS = 12
EXPORT_CONTEXT_CHARS = 32
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

# --- the challenge plan (section 5): why a record is in a challenge task, and
# what the plan says it is owed
CHALLENGE_RULE_REFUTE = "refute_by_default"
CHALLENGE_RULE_HUNT = "hunt"
CHALLENGE_RULE_SAMPLE = "sample"
CHALLENGE_RULE_NONE = "none"
CHALLENGE_RULES = frozenset({CHALLENGE_RULE_REFUTE, CHALLENGE_RULE_HUNT, CHALLENGE_RULE_SAMPLE,
                             CHALLENGE_RULE_NONE})
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
#: the gold tiers, never pooled
BENCH_TIERS = frozenset({"G1", "G3", "GX", "G4", "P"})
#: G1 is scored in two arms that are never pooled (section 8.1)
BENCH_ARM_ASSISTED = "assisted"
BENCH_ARM_BLIND = "blind"
BENCH_ARMS = frozenset({BENCH_ARM_ASSISTED, BENCH_ARM_BLIND})
BENCH_SPLITS = frozenset({"dev", "eval", "test"})
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

    Case-folded, with its punctuation stripped and an enumerator or a
    parenthesised name after the label word dropped ("Case 2:", "Step 1a.",
    "Example (iii)", "Theorem (Widget lemma)."); a word of
    :data:`BOLD_LABELS_PUNCTUATED` counts only when one of those marks was there,
    and a proof of something ("Proof of the lemma:") is a label too.
    """
    spaced = " ".join(str(text or "").split()).casefold()
    bare = spaced.strip(_LABEL_TRIM)
    core = _LABEL_ENUMERATOR.sub("", bare).strip(_LABEL_TRIM)
    named = _LABEL_NAME.sub("", core).strip(_LABEL_TRIM)
    marked = bare != spaced.strip() or core != bare or named != core
    for word in {core, named}:
        if word in BOLD_LABELS or (marked and word in BOLD_LABELS_PUNCTUATED):
            return True
    return core.startswith("proof of ") or core.startswith("proof (")


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
    # data: the table's "broadMatch to a definition" is partial; an exact or
    # close match to data is not in the table and is decided the same way
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


def derive_status(passage_kind: str, locations: Iterable, absent: bool) -> str | None:
    """A passage's derived status: the Literature Links ``status`` of its records.

    docs/proposals/wikipedia-locate.md section 2.6, computed once per passage
    after challenge.  ``locations`` are the passage's surviving locations, each
    a mapping (or an object) with ``relation`` (``LOCATE_RELATIONS``),
    ``proof_status`` (``PROOF_STATUSES``, joined from the library index) and
    an optional ``statement`` flag (the declaration is a ``Prop``-valued
    definition).  ``absent`` says the passage has a checked absence.

    * ``formalized`` -- a theorem or conjecture passage with an ``exactMatch``,
      ``closeMatch`` or ``broadMatch`` to a ``proved`` declaration; a
      definition passage with an ``exactMatch`` or ``closeMatch`` to a
      ``definition``;
    * ``statement_only`` -- the best such location is ``statement_only``,
      ``wanted`` or ``axiom_dependent``, or is a ``Prop``-valued definition
      stating a theorem passage;
    * ``partial`` -- only ``hasPart`` or ``narrowMatch``, or ``broadMatch`` to a
      definition;
    * ``not_formalized`` -- a checked absence, possibly beside ``relatedMatch``
      locations;
    * ``None`` -- unresolved: no location that counts and no absence.  It is
      never ``not_formalized``.

    ``relatedMatch`` never counts towards a status.  Four cases the table does
    not name are decided here, each conservatively (references/vocabulary.md
    section 1.11): a theorem passage matched exactly to DATA is ``partial``, as
    its ``broadMatch`` is; a definition passage matched exactly to a proved
    theorem is ``partial``; an example or ``other`` passage takes the better of
    the two rows; and a passage whose only locations are ``relatedMatch``, with
    no checked absence, is ``None`` -- the state a challenge leaves when it
    downgrades a passage's one location to ``relatedMatch``: nothing says it is
    formalized and nobody checked that it is absent, so it counts as
    unresolved (a miss for the safety metric, never ``not_formalized``).

    Refused (``CfError``), because no consistent record can say them: a checked
    absence beside a location that counts; a ``statement`` flag on a ``proved``
    declaration (``decide_proof_status`` gives a ``Prop``-valued definition every
    status but ``proved``); a ``statement`` that is not a bool; and any value
    outside the vocabulary.
    """
    check(passage_kind, PASSAGE_KINDS, "passage kind")
    best: str | None = None
    counted: list[str] = []
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
        if relation == RELATED_MATCH:
            continue
        rows = []
        if passage_kind in THEOREM_PASSAGE_KINDS | EITHER_PASSAGE_KINDS:
            rows.append(_theorem_row(relation, proof_status, statement))
        if passage_kind in DEFINITION_PASSAGE_KINDS | EITHER_PASSAGE_KINDS:
            rows.append(_definition_row(relation, proof_status))
        status = min(rows, key=_STATUS_RANK.__getitem__)
        counted.append(relation)
        if best is None or _STATUS_RANK[status] < _STATUS_RANK[best]:
            best = status
    if absent and counted:
        raise CfError(f"a checked absence beside a {counted[0]} location",
                      hint="an absence may sit beside relatedMatch locations only; "
                           "drop the absence or downgrade the location")
    if best is not None:
        return best
    return STATUS_NOT_FORMALIZED if absent else None
