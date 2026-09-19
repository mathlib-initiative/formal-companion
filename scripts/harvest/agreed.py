"""Loci two or more independent runs made the same complaint about.

Rewrite of the script behind a 2026-09 ``agreed_high_findings.json``.
Inter-run agreement is the cheapest evidence the pipeline has that a first-pass
complaint is real -- a first pass is wrong about 40% of the time (4 of 10 Adam
rows were refuted as house convention), and the runs are independent passes over
the same items, so two runs landing on the same defect is not nothing.

An entry is evidence about a LOCUS, not about a finding.  Sub-parts are
collapsed onto the book item they belong to (below), so ``Exercise 1.3.10`` is
one locus however many parts a run faulted; what an entry says is "these runs
each made a complaint of this kind about this declaration of this item".

Three corrections to the original file:

* it emitted rows with ``"repos": []`` -- findings that named no declaration at
  all.  Those are kept here under the ``_unattributed`` repo instead of being
  presented as if they were about some repo;
* agreement was counted per raw label, so ``Exercise 1.3.10`` and
  ``Exercise 1.3.10(ii)`` could not agree.  Agreement is counted per BOOK item;
* the key was ``(source_id, repo, item)`` alone, so two runs that faulted
  DIFFERENT declarations with DIFFERENT kinds on one printed page corroborated
  each other: a CONTENT complaint about ``ours:First`` and a VACUITY complaint
  about ``ours:Second``, both on ``In-text p.71``, came back as one agreed
  entry.  That inflates the signal in exactly the direction that makes the audit
  look better than it is, and ``agreed_high.json`` is what a reviewer triages
  from.  The kind is part of the key and the runs must share a declaration;
  the loci that only pass the weaker test are reported separately, under
  ``same_locus``, labelled with why they failed the stronger one.

This is evidence, not a verdict: agreement between two runs of the same family
(both attack, or both differential over the same prompt) is weaker than
agreement across families, so ``modes`` is recorded on every entry.
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):  # `python3 scripts/harvest/agreed.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvest._compat import SUBSTANTIVE_KINDS, iso_now
from harvest.tally import collect, parse_run_arg, unjoinable

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def _eligible(rows: Sequence[dict], min_severity: str, substantive_only: bool,
              source_id: str | None):
    """The rows agreement may be computed over, in the caller's filter terms."""
    floor = SEVERITY_RANK.get(min_severity, 0)
    for row in rows:
        if substantive_only and row["kind"] not in SUBSTANTIVE_KINDS:
            continue
        if SEVERITY_RANK.get(row["severity"], -1) < floor:
            continue
        if source_id is not None and str(row.get("source_id") or "") != source_id:
            continue
        yield row


def _decls_by_run(group: Sequence[dict]) -> dict[str, list[str]]:
    """{run: the declarations that run named at this locus}."""
    out: dict[str, set] = {}
    for row in group:
        out.setdefault(str(row["run"]), set()).update(row["decls"])
    return {run: sorted(decls) for run, decls in sorted(out.items())}


def _shared_decls(by_run: dict[str, list[str]]) -> list[str] | None:
    """Declarations every run named, or ``None`` when they share none.

    A group in which NO run named a declaration returns ``[]``: those are the
    ``_unattributed`` rows, there is nothing to intersect, and they agree on the
    locus alone.  A group in which some run named one and the runs share none is
    ``None`` -- two runs complaining about two different declarations are not
    corroborating each other.
    """
    sets = [set(d) for d in by_run.values()]
    if not any(sets):
        return []
    shared = set.intersection(*sets) if sets else set()
    return sorted(shared) or None


def _entry(src: str, repo: str, item: str, group: Sequence[dict]) -> dict:
    by_run = _decls_by_run(group)
    return {
        "source_id": src,
        "source_sha": str(group[0].get("source_sha") or ""),
        "repo": repo,
        "item": item,
        "runs": sorted(by_run),
        "modes": sorted({r["mode"] for r in group}),
        "cross_family": len({r["mode"] for r in group}) > 1,
        "kinds": sorted({r["kind"] for r in group if r["kind"]}),
        "rows": len(group),
        "decls": sorted({d for r in group for d in r["decls"]}),
        # the labels as the runs wrote them: a reviewer has to be able to see
        # that run A faulted part (i) and run B part (iii) of one exercise
        "items_raw": sorted({str(r.get("item_raw") or "") for r in group}),
        "by_run": {run: {"decls": decls,
                         "items_raw": sorted({str(r.get("item_raw") or "") for r in group
                                              if str(r["run"]) == run}),
                         "kinds": sorted({r["kind"] for r in group
                                          if str(r["run"]) == run and r["kind"]})}
                   for run, decls in by_run.items()},
    }


def agreed(rows: Sequence[dict], min_runs: int = 2, min_severity: str = "high",
           substantive_only: bool = True, source_id: str | None = None) -> list[dict]:
    """Loci at least *min_runs* runs made the SAME complaint about.

    The key is ``(source_id, repo, item, kind)`` and the runs must share at least
    one declaration, so an entry means "these runs each faulted this declaration
    of this item in this way".  ``min_severity`` filters the rows before grouping
    (the baseline file was "high in >= 2 runs"); pass "low" to count agreement at
    any severity.  The weaker groupings this rejects are :func:`same_locus`.

    AGREEMENT IS COMPUTED WITHIN A SOURCE, NEVER ACROSS ONE.  Two runs that
    faulted ``Exercise 1.3.19`` in two different editions do not agree about
    anything -- they are two items -- and counting them as one would manufacture
    exactly the cross-run corroboration this file exists to measure.
    """
    groups: dict[tuple[str, str, str, str], list[dict]] = collections.defaultdict(list)
    for row in _eligible(rows, min_severity, substantive_only, source_id):
        groups[(str(row.get("source_id") or ""), row["repo"], row["item"],
                str(row["kind"] or ""))].append(row)

    out = []
    for (src, repo, item, kind), group in sorted(groups.items()):
        if len({r["run"] for r in group}) < min_runs:
            continue
        shared = _shared_decls(_decls_by_run(group))
        if shared is None:
            continue
        entry = _entry(src, repo, item, group)
        entry["kind"] = kind
        entry["shared_decls"] = shared
        out.append(entry)
    return out


def same_locus(rows: Sequence[dict], min_runs: int = 2, min_severity: str = "high",
               substantive_only: bool = True, source_id: str | None = None) -> list[dict]:
    """The weaker signal: loci several runs faulted, but not with one complaint.

    These are what the old ``(source_id, repo, item)`` key called agreement: two
    runs both looked hard at one book item (an in-text label collapses to a
    printed page, so "one item" can be a whole page) and each found something,
    but they did not find the same thing.  That is worth a reviewer's attention
    and it is not corroboration, so it is a separate list and it says why it is
    not in :func:`agreed`.
    """
    strict = {(e["source_id"], e["repo"], e["item"]) for e in agreed(
        rows, min_runs=min_runs, min_severity=min_severity,
        substantive_only=substantive_only, source_id=source_id)}
    groups: dict[tuple[str, str, str], list[dict]] = collections.defaultdict(list)
    for row in _eligible(rows, min_severity, substantive_only, source_id):
        groups[(str(row.get("source_id") or ""), row["repo"], row["item"])].append(row)

    out = []
    for (src, repo, item), group in sorted(groups.items()):
        if len({r["run"] for r in group}) < min_runs or (src, repo, item) in strict:
            continue
        why = []
        if len({str(r["kind"] or "") for r in group}) > 1:
            why.append("different kinds")
        if _shared_decls(_decls_by_run(group)) is None:
            why.append("no declaration named by every run")
        entry = _entry(src, repo, item, group)
        entry["why_not_agreement"] = why or [
            f"fewer than {min_runs} runs made any one complaint"]
        out.append(entry)
    return out


def build(runs: dict[str, str | Path], source_id: str | None = None, **kwargs) -> dict:
    """``{generated_at, criteria, entries, same_locus}`` for reports/agreed_high.json.

    ``count`` counts ENTRIES and ``locus_count`` the distinct
    ``(source_id, repo, item)`` loci behind them; see the comment on both.
    """
    rows = collect(runs, sources=[source_id] if source_id else None)
    entries = agreed(rows, source_id=source_id, **kwargs)
    weaker = same_locus(rows, source_id=source_id, **kwargs)
    return {
        "generated_at": iso_now(),
        "runs": sorted(runs),
        "source_id": source_id or "*",
        "criteria": {
            "min_runs": kwargs.get("min_runs", 2),
            "min_severity": kwargs.get("min_severity", "high"),
            "substantive_only": kwargs.get("substantive_only", True),
            # the only machine-readable record of what agreement meant here
            "grouped_by": ("source_id + repo + item + kind (sub-parts collapsed), "
                           "with a declaration named by every run"),
            "same_locus_grouped_by": "source_id + repo + item (sub-parts collapsed)",
        },
        # ENTRIES, not loci: the key includes the kind, so one item faulted with
        # CONTENT by two runs and with VACUITY by two runs is two entries.  The
        # pre-kind file counted loci, so ``count`` is not comparable with it --
        # ``locus_count`` is, and it is the smaller number.
        "count": len(entries),
        "locus_count": len({(e["source_id"], e["repo"], e["item"]) for e in entries}),
        # an in-text label the grammar could not canonicalise cannot agree with
        # the same item under another spelling: reported, never dropped.
        "unjoinable": unjoinable(rows),
        "by_source": dict(collections.Counter(e["source_id"] for e in entries)),
        "cross_family": sum(1 for e in entries if e["cross_family"]),
        "entries": entries,
        # loci several runs faulted with DIFFERENT complaints: weaker evidence,
        # kept visible rather than counted as agreement
        "same_locus_count": len(weaker),
        "same_locus": weaker,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="append", default=[], metavar="ID=DIR", required=True)
    parser.add_argument("--min-runs", type=int, default=2)
    parser.add_argument("--min-severity", choices=("low", "medium", "high"), default="high")
    parser.add_argument("--any-kind", action="store_true",
                        help="count COVERAGE/PAGE_ANCHOR agreement too")
    parser.add_argument("--source", default=None, help="restrict to one source_id")
    args = parser.parse_args(argv)
    runs = dict(parse_run_arg(v) for v in args.run)
    doc = build(runs, source_id=args.source, min_runs=args.min_runs,
                min_severity=args.min_severity, substantive_only=not args.any_kind)
    import json
    print(json.dumps(doc, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
