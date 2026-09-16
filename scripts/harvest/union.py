"""Union a repo's findings across runs, with run provenance.

Rewrite of the ad hoc 2026-09-06 scripts that produced
/Users/jack/Desktop/tao-diff-run/union.{ours,adam}.json.  Those grouped by
``(item, kind, concept[:50])``, so the SAME defect found by two runs became two
rows whenever the two agents phrased the concept differently -- visible in
union.adam.json, where Exercise 1.6.39 appears twice with runs ``["c-text"]``
and ``["c-pdf"]``.  design/critique.json names that defect: "concept is
LLM-written text, so the same defect in two runs gets different ids and
cross-run linking cannot use it".

Here the key is ``(repo, book item, kind)`` -- all three mechanical -- with the
v2 ``finding_id`` (sha256 of run-id, item, kind, sorted unfaithful_decls) kept
alongside when the corpus carries it.  ``--new-only`` answers the question a new
run is actually run to answer: what did it find that the baseline did not?
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path
from typing import Iterable, Sequence

if __package__ in (None, ""):  # `python3 scripts/harvest/union.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvest._compat import SUBSTANTIVE_KINDS, iso_now
from harvest.tally import UNATTRIBUTED, collect, parse_run_arg

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def _worst(severities: Iterable[str]) -> str:
    """The highest severity seen for a group ('' when none was recorded)."""
    known = [s for s in severities if s in SEVERITY_RANK]
    return max(known, key=lambda s: SEVERITY_RANK[s]) if known else ""


def union(rows: Sequence[dict], repo: str | None = None,
          baseline_runs: Iterable[str] = (), new_only: bool = False,
          substantive_only: bool = False, group_by: str = "item-kind") -> list[dict]:
    """Group rows into one entry per defect, tagged with the runs that found it.

    ``group_by`` is ``item-kind`` (mechanical, the default) or ``finding-id``
    (exact, but only usable on a v2 corpus where every row carries one).
    """
    baseline = set(baseline_runs)
    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for row in rows:
        if repo is not None and row["repo"] != repo:
            continue
        if substantive_only and row["kind"] not in SUBSTANTIVE_KINDS:
            continue
        if group_by == "finding-id" and row.get("finding_id"):
            key = (row["repo"], str(row["finding_id"]))
        else:
            key = (row["repo"], row["item"], row["kind"])
        groups[key].append(row)

    out: list[dict] = []
    for key, group in groups.items():
        runs = sorted({r["run"] for r in group})
        entry = {
            "repo": key[0],
            "item": group[0]["item"],
            "items_raw": sorted({r["item_raw"] for r in group}),
            "kind": group[0]["kind"],
            "severity": _worst(r["severity"] for r in group),
            "runs": runs,
            "modes": sorted({r["mode"] for r in group}),
            "rows": len(group),
            "decls": sorted({d for r in group for d in r["decls"]}),
            "finding_ids": sorted({str(r["finding_id"]) for r in group if r.get("finding_id")}),
            "concepts": [{"run": r["run"], "concept": r.get("concept")} for r in group],
            "files": sorted({r["file"] for r in group}),
            "in_baseline": bool(baseline & set(runs)),
            "new": bool(baseline) and not (baseline & set(runs)),
        }
        if new_only and not entry["new"]:
            continue
        out.append(entry)
    out.sort(key=lambda e: (e["repo"], e["item"], e["kind"] or "", e["severity"]))
    return out


def summary(entries: Sequence[dict]) -> dict:
    """Counts a report can quote: entries, items, by kind, by severity, new."""
    return {
        "entries": len(entries),
        "items": len({e["item"] for e in entries}),
        "by_kind": dict(collections.Counter(e["kind"] for e in entries if e["kind"])),
        "by_severity": dict(collections.Counter(e["severity"] for e in entries if e["severity"])),
        "multi_run": sum(1 for e in entries if len(e["runs"]) > 1),
        "new": sum(1 for e in entries if e["new"]),
        "unattributed": sum(1 for e in entries if e["repo"] == UNATTRIBUTED),
    }


def build(runs: dict[str, str | Path], repo: str | None = None, **kwargs) -> dict:
    """Union document for one repo: ``{generated_at, repo, runs, summary, entries}``."""
    rows = collect(runs)
    entries = union(rows, repo=repo, **kwargs)
    return {
        "generated_at": iso_now(),
        "repo": repo or "*",
        "runs": sorted(runs),
        "baseline_runs": sorted(kwargs.get("baseline_runs") or []),
        "group_by": kwargs.get("group_by", "item-kind"),
        "summary": summary(entries),
        "entries": entries,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="append", default=[], metavar="ID=DIR", required=True)
    parser.add_argument("--repo", help="repo prefix to union (default: all)")
    parser.add_argument("--baseline", action="append", default=[], metavar="RUN_ID",
                        help="run ids that count as already known (repeatable)")
    parser.add_argument("--new-only", action="store_true",
                        help="keep only defects no baseline run found")
    parser.add_argument("--substantive-only", action="store_true")
    parser.add_argument("--group-by", choices=("item-kind", "finding-id"), default="item-kind")
    args = parser.parse_args(argv)
    runs = dict(parse_run_arg(v) for v in args.run)
    doc = build(runs, repo=args.repo, baseline_runs=args.baseline, new_only=args.new_only,
                substantive_only=args.substantive_only, group_by=args.group_by)
    import json
    print(json.dumps(doc, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
