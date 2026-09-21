"""Finding rows emitted by the detector (kinds ``PROXY`` and ``COVERAGE``).

New code. Stage 8 of docs/design/stage_map.md §4: "a detector hit counts only
once it is emitted as a finding row of kind ``PROXY`` (with ``bridge:
proved|fiat|none`` ...), so it joins the corpus and the ledger like any other
finding".

These rows are *mechanical*: they carry no ``book_quote`` and no ``witness``,
which is exactly why the design critique asks for a per-kind gate matrix
("PROXY: bridge field + closure path as evidence; COVERAGE: item + items.json
page"). The quote and witness keys are written as ``null`` so a reader can see
the absence is deliberate rather than a dropped field.

``run_id`` is deterministic (``detect-<companion>-<tier>`` by default) so
``finding_id`` -- sha256(run_id, source_id, item, kind, sorted(unfaithful_decls))
-- is stable across re-runs and the corpus stays idempotent.  Every row carries
``source_id`` and ``source_sha``: a detector reads ONE source's items.json, and
a row that did not say which would be gated against the wrong text.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from detect._compat import CfError, finding_id, vocab


def _checked(kind: str, severity: str, bridge: str | None) -> None:
    """Refuse a row the shared vocabulary does not accept (cflib.vocab)."""
    vocab.check(kind, vocab.FINDING_KINDS, "finding kind")
    vocab.check(severity, vocab.SEVERITY_SET, "severity")
    if bridge is not None:
        vocab.check(bridge, vocab.BRIDGE_KINDS, "bridge")


def default_run_id(companion: str, tier: str) -> str:
    """``detect-<companion>-<tier>`` -- deterministic, never a timestamp."""
    return f"detect-{companion}-{tier}"


def _decl_ids(companion: str, decls: Iterable[str]) -> list[str]:
    """``["ours:IsInterval"]`` -- the repo-prefixed ids the router keys on."""
    return [f"{companion}:{d}" for d in decls]


def proxy_row(
    *,
    run_id: str,
    companion: str,
    source_id: str,
    source_sha: str | None = None,
    item: str,
    decl: str,
    rule: str,
    severity: str,
    concept: str,
    reasoning: str,
    bridge: str,
    bridge_decls: Sequence[dict] | None = None,
    evidence: dict | None = None,
    tier: str = "cheap",
) -> dict:
    """One PROXY finding: a source concept carried by a foreign object.

    ``bridge`` is ``proved`` (a theorem ties the repo's object to the foreign
    one and is not a ``sorry``), ``fiat`` (only asserted) or ``none``.
    """
    _checked(vocab.PROXY, severity, bridge)
    unfaithful = _decl_ids(companion, [decl])
    if not unfaithful:
        raise CfError(f"PROXY finding for {item} names no declaration",
                      hint="vocab.EVIDENCE_BY_KIND requires unfaithful_decls on PROXY rows")
    return {
        "finding_id": finding_id(run_id, source_id, item, vocab.PROXY, unfaithful),
        "run_id": run_id,
        "source_id": source_id,
        "source_sha": source_sha,
        "item": item,
        "kind": vocab.PROXY,
        "severity": severity,
        "repo": companion,
        "faithful": [],
        "unfaithful_decls": unfaithful,
        "concept": concept,
        "reasoning": reasoning,
        "bridge": bridge,
        "bridge_decls": list(bridge_decls or []),
        "evidence": {"rule": rule, "tier": tier, **(evidence or {})},
        "book_quote": None,
        "witness": None,
        "detector": f"cf-detect-proxies/{tier}",
    }


def coverage_row(
    *,
    run_id: str,
    companion: str,
    source_id: str,
    source_sha: str | None = None,
    item: str,
    rule: str,
    severity: str,
    reasoning: str,
    decls: Sequence[str] = (),
    evidence: dict | None = None,
    tier: str = "cheap",
) -> dict:
    """One COVERAGE finding: the source states a concept the companion does not.

    ``decls`` is empty for a wholly missing item -- there is nothing to name --
    and Stage 9 routes only on ``unfaithful_decls``, so such rows stay in the
    workspace report rather than reaching an owner.
    """
    _checked(vocab.COVERAGE, severity, None)
    unfaithful = _decl_ids(companion, decls)
    return {
        "finding_id": finding_id(run_id, source_id, item, vocab.COVERAGE, unfaithful),
        "run_id": run_id,
        "source_id": source_id,
        "source_sha": source_sha,
        "item": item,
        "kind": vocab.COVERAGE,
        "severity": severity,
        "repo": companion,
        "faithful": [],
        "unfaithful_decls": unfaithful,
        "concept": f"{item} has no native definition in {companion}",
        "reasoning": reasoning,
        "bridge": None,
        "bridge_decls": [],
        "evidence": {"rule": rule, "tier": tier, **(evidence or {})},
        "book_quote": None,
        "witness": None,
        "detector": f"cf-detect-proxies/{tier}",
    }


def meta(*, run_id: str, companion: str, tier: str, source_id: str,
         source_sha: str | None, counts: dict, inputs: dict) -> dict:
    """The ``_meta`` row written at the head of a findings jsonl file."""
    return {
        "run_id": run_id,
        "repo": companion,
        "tier": tier,
        "source_id": source_id,
        "source_sha": source_sha,
        "kinds": [vocab.PROXY, vocab.COVERAGE],
        "status": vocab.UNCHALLENGED,
        "counts": counts,
        "inputs": inputs,
        "note": (
            "mechanical detector output: no book quote and no witness by construction; "
            "these rows are candidates for adjudication, not confirmed findings"
        ),
    }
