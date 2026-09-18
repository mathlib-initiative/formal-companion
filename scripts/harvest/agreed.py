"""Findings two or more independent runs agree on.

Rewrite of the script behind /Users/jack/Desktop/tao-diff-run/agreed_high_findings.json.
Inter-run agreement is the cheapest evidence the pipeline has that a first-pass
finding is real -- a first pass is wrong about 40% of the time (4 of 10 Adam rows
were refuted as house convention), and the runs are independent passes over the
same items, so two runs landing on the same book item is not nothing.

Two corrections to the original file:

* it emitted rows with ``"repos": []`` -- findings that named no declaration at
  all.  Those are kept here under the ``_unattributed`` repo instead of being
  presented as if they were about some repo;
* agreement was counted per raw label, so ``Exercise 1.3.10`` and
  ``Exercise 1.3.10(ii)`` could not agree.  Agreement is counted per BOOK item.

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


def agreed(rows: Sequence[dict], min_runs: int = 2, min_severity: str = "high",
           substantive_only: bool = True, source_id: str | None = None) -> list[dict]:
    """Source items a repo was faulted on by at least *min_runs* runs.

    ``min_severity`` filters the rows before grouping (the baseline file was
    "high in >= 2 runs"); pass "low" to count agreement at any severity.

    AGREEMENT IS COMPUTED WITHIN A SOURCE, NEVER ACROSS ONE.  Two runs that
    faulted ``Exercise 1.3.19`` in two different editions do not agree about
    anything -- they are two items -- and counting them as one would manufacture
    exactly the cross-run corroboration this file exists to measure.
    """
    floor = SEVERITY_RANK.get(min_severity, 0)
    groups: dict[tuple[str, str, str], list[dict]] = collections.defaultdict(list)
    for row in rows:
        if substantive_only and row["kind"] not in SUBSTANTIVE_KINDS:
            continue
        if SEVERITY_RANK.get(row["severity"], -1) < floor:
            continue
        row_source = str(row.get("source_id") or "")
        if source_id is not None and row_source != source_id:
            continue
        groups[(row_source, row["repo"], row["item"])].append(row)

    out = []
    for (src, repo, item), group in sorted(groups.items()):
        runs = sorted({r["run"] for r in group})
        if len(runs) < min_runs:
            continue
        out.append({
            "source_id": src,
            "source_sha": str(group[0].get("source_sha") or ""),
            "repo": repo,
            "item": item,
            "runs": runs,
            "modes": sorted({r["mode"] for r in group}),
            "cross_family": len({r["mode"] for r in group}) > 1,
            "kinds": sorted({r["kind"] for r in group if r["kind"]}),
            "rows": len(group),
            "decls": sorted({d for r in group for d in r["decls"]}),
        })
    return out


def build(runs: dict[str, str | Path], source_id: str | None = None, **kwargs) -> dict:
    """``{generated_at, criteria, entries}`` for reports/agreed_high.json."""
    rows = collect(runs, sources=[source_id] if source_id else None)
    entries = agreed(rows, source_id=source_id, **kwargs)
    return {
        "generated_at": iso_now(),
        "runs": sorted(runs),
        "source_id": source_id or "*",
        "criteria": {
            "min_runs": kwargs.get("min_runs", 2),
            "min_severity": kwargs.get("min_severity", "high"),
            "substantive_only": kwargs.get("substantive_only", True),
            "grouped_by": "source_id + repo + item (sub-parts collapsed)",
        },
        "count": len(entries),
        # an in-text label the grammar could not canonicalise cannot agree with
        # the same item under another spelling: reported, never dropped.
        "unjoinable": unjoinable(rows),
        "by_source": dict(collections.Counter(e["source_id"] for e in entries)),
        "cross_family": sum(1 for e in entries if e["cross_family"]),
        "entries": entries,
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
