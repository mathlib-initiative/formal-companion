"""The ONE vocabulary. Documented in references/vocabulary.md.

New code, consolidating the five drifting vocabularies the design records
(match verdict -> finding kind -> challenge classification -> ledger verdict
-> viewer verdict; design/architecture.md S3).  Every stage imports its enums
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
#: pagination noise (design/architecture.md S5 "Kinds gate").
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

#: Per-kind evidence matrix (design/critique.json: "A per-kind gate matrix is
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

# --- sources (docs/design/multi-source-model.md S1, S2)
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
