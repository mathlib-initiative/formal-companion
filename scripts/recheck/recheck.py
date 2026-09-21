"""Classify every open finding against the companion's CURRENT statements.

New code (docs/design/archive/multi-source-model.md S6.2 / the pin this package records in
``pin.py``).  This is the half that makes the pin worth recording: a finding
whose declaration still hashes to the fingerprint it was judged against is still
a live claim, one whose declaration has changed needs a human to look again, and
one whose declaration is gone is a claim about code that no longer exists.

Four statuses and nothing else (``cflib.vocab.RECHECK_STATUSES``):

``unchanged``  every pinned declaration is byte-identical after normalisation.
``changed``    a fingerprint differs -- the finding is NOT closed, it is marked
               ``needs_rereview``, because a statement that moved may have been
               fixed or may have been made worse and only a reader can say.
``gone``       a pinned declaration is not in the current table at all.
``unpinned``   the row carries no fingerprint and its run recorded none: every
               imported legacy corpus, and every row written before the pin
               existed.  It is reported, never guessed at, which is why the
               pinned regression figures cannot move when this lands.

No verdict is touched.  ``cf recheck`` states what the code did; whether that
closes a finding is an adjudication, and adjudications are written by the stage
that argues them.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from cflib import vocab

from recheck import pin as pin_mod

#: worst-first, so a row's status is the worst of its declarations'
_RANK = {vocab.RECHECK_UNCHANGED: 0, vocab.RECHECK_CHANGED: 2, vocab.RECHECK_GONE: 3,
         vocab.RECHECK_UNPINNED: 1}

#: how much of a statement a report prints, so a reader can see WHAT moved
HEAD_CHARS = 160


def _head(text: object) -> str:
    """The first :data:`HEAD_CHARS` of a normalised statement, ellipsised."""
    normalised = pin_mod.normalise_statement(text)
    return normalised if len(normalised) <= HEAD_CHARS else normalised[:HEAD_CHARS - 1] + "…"


def classify_decl(written: str, pinned: str, current: Mapping[str, str]) -> dict:
    """One declaration's re-check: ``{decl, status, pinned, current, …}``.

    ``current`` maps a bare declaration name to its statement text as the
    companion holds it NOW.  ``written`` is the ``repo:Decl`` spelling a finding
    used; the repo prefix is stripped to look the declaration up, because the
    statement table is per companion and does not repeat its own name.
    """
    _repo, decl = pin_mod.split_decl(written)
    out: dict = {"decl": written, "pinned_fingerprint": pinned or ""}
    if not pinned:
        out["status"] = vocab.RECHECK_UNPINNED
        return out
    if decl not in current:
        out["status"] = vocab.RECHECK_GONE
        return out
    now = pin_mod.decl_fingerprint(current[decl])
    out["current_fingerprint"] = now
    out["status"] = (vocab.RECHECK_UNCHANGED if now == pinned else vocab.RECHECK_CHANGED)
    if out["status"] == vocab.RECHECK_CHANGED:
        out["current_statement_head"] = _head(current[decl])
    return out


def classify_row(row: Mapping, *, current: Mapping[str, str],
                 pin_doc: Mapping | None = None, companion: str = "",
                 pinned_statements: Mapping[str, str] | None = None) -> dict:
    """One ledger/routed row's re-check block.

    ``pinned_statements`` is optional: when the caller can still show the text
    the fingerprint was taken over (it has the old table), the ``changed`` rows
    carry both heads, which is what makes the report readable rather than a list
    of hashes.  When it cannot, the new head alone is still printed.
    """
    pin = pin_mod.row_pin(row, pin_doc, companion=companion)
    fingerprints = pin["decl_fingerprints"]
    decls = pin["decls"]
    per_decl = [classify_decl(d, fingerprints.get(d, ""), current) for d in decls]
    if not per_decl:
        # A COVERAGE finding names no declaration: there is nothing to fingerprint,
        # so it can never be re-checked mechanically and must not be reported as
        # unchanged (which would read as "still verified").
        status = vocab.RECHECK_UNPINNED
    else:
        status = max((d["status"] for d in per_decl), key=lambda s: _RANK.get(s, 0))
    for entry in per_decl:
        if entry["status"] == vocab.RECHECK_CHANGED and pinned_statements:
            was = pinned_statements.get(pin_mod.split_decl(entry["decl"])[1])
            if was is not None:
                entry["pinned_statement_head"] = _head(was)
    return {
        "finding_id": str(row.get("finding_id") or ""),
        "defect_key": str(row.get("defect_key") or ""),
        "run_id": str(row.get("run_id") or ""),
        "item": str(row.get("item") or ""),
        "kind": str(row.get("kind") or ""),
        "verdict": str(row.get("verdict") or ""),
        "status": status,
        "needs_rereview": status in (vocab.RECHECK_CHANGED, vocab.RECHECK_GONE),
        "pinned_commit": pin["repo_commit"],
        "decls": per_decl,
    }


def counts(entries: Iterable[Mapping]) -> dict:
    """``{status: n}`` over every status in the vocabulary, zeros included.

    Zeros are written out on purpose: a report that omits ``gone`` reads as "none
    were checked" exactly like one that omits it because there were none.
    """
    out = {status: 0 for status in vocab.RECHECK_STATUSES}
    total = 0
    for entry in entries:
        out[str(entry.get("status") or vocab.RECHECK_UNPINNED)] = out.get(
            str(entry.get("status") or vocab.RECHECK_UNPINNED), 0) + 1
        total += 1
    out["rows"] = total
    return out


def block(entry: Mapping, *, at: str, current_commit: str) -> dict:
    """The ``recheck`` block written back onto a ledger row.

    Deliberately small and self-describing: the status, when it was taken, the
    two commits it compared and the per-declaration detail.  It never carries a
    verdict or a resolution, so a re-check can be re-run over a ledger a hundred
    times without moving a single adjudicated number.
    """
    return {
        "at": at,
        "status": str(entry.get("status") or ""),
        "needs_rereview": bool(entry.get("needs_rereview")),
        "pinned_commit": str(entry.get("pinned_commit") or ""),
        "checked_commit": current_commit or "",
        "decls": [
            {k: v for k, v in decl.items()
             if k in ("decl", "status", "pinned_fingerprint", "current_fingerprint")}
            for decl in entry.get("decls") or []
        ],
    }


def render(report: Mapping) -> str:
    """A short human summary; every number in it is a key of *report*."""
    c = report.get("counts") or {}
    lines = [
        "companion %s: %s -> %s%s" % (
            report.get("companion", ""),
            _short(report.get("pinned_commits") or []) or "(unpinned)",
            _short([report.get("current_commit")]) or "(no checkout)",
            " DIRTY" if report.get("dirty") else ""),
        "  rows: %d (%s)" % (
            c.get("rows", 0),
            ", ".join("%s=%d" % (s, c.get(s, 0)) for s in vocab.RECHECK_STATUSES)),
    ]
    if report.get("declarations") is not None:
        lines.append("  current statement table: %d declaration(s)"
                     % report["declarations"])
    for entry in report.get("entries") or []:
        if not entry.get("needs_rereview"):
            continue
        lines.append("  %s [%s] %s -- %s" % (entry.get("item", ""), entry.get("kind", ""),
                                             entry.get("status", ""),
                                             entry.get("finding_id", "")[:12]))
        for decl in entry.get("decls") or []:
            if decl.get("status") == vocab.RECHECK_UNCHANGED:
                continue
            lines.append("      %s: %s" % (decl.get("decl", ""), decl.get("status", "")))
            if decl.get("pinned_statement_head"):
                lines.append("        was: %s" % decl["pinned_statement_head"])
            if decl.get("current_statement_head"):
                lines.append("        now: %s" % decl["current_statement_head"])
    if c.get(vocab.RECHECK_UNPINNED):
        lines.append("  %d row(s) carry no fingerprint (imported or pre-pin): re-harvest "
                     "them from a planned run to pin them."
                     % c[vocab.RECHECK_UNPINNED])
    return "\n".join(lines)


def _short(values: Sequence) -> str:
    return ", ".join(str(v)[:12] for v in values if v)
