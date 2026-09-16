"""Count findings from the corpus -- the only place numbers come from.

Generalised from the tally at tao-measure-lean/docs/OPEN_FAITHFULNESS_FINDINGS.md
lines 296-311, which walked the five baseline run directories, kept every
disagreement naming ``ours:``, and printed "72 rows over 40 distinct items".

That recipe is kept exactly (same row rule, same dedup) and generalised:

* any repo prefix, not just ``ours`` (teorth: 147 rows / 71 items; adam: 7 / 5);
* any set of runs, each tagged with its mode, so ATTACK-only and
  DIFFERENTIAL-only findings can be separated -- the attack gate rejects
  PAGE_ANCHOR and COVERAGE by construction, so mixing the two run families
  without the tag makes the attack runs look clean;
* both a RAW item count (the label the worker wrote) and a COLLAPSED one (the
  book item it belongs to), because ``Exercise 1.6.27(iii)`` and ``In-text
  definition, Exercise 1.4.24`` are findings about ``Exercise 1.6.27`` and
  ``Exercise 1.4.24``;
* findings that name no declaration at all are counted separately instead of
  being attributed to a repo -- routing on anything but the ``unfaithful_decls``
  prefix is what produced the "31 findings" scare (design/stage_map.md Stage 9).

The prose lesson that motivates the whole module is in the same file: the number
was wrong three times (once because the corpus itself was a file short), always
in the direction of "cleaner".  So: recompute, never re-read.
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path
from typing import Iterable, Sequence

if __package__ in (None, ""):  # `python3 scripts/harvest/tally.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvest._compat import (SEVERITIES, SUBSTANTIVE_KINDS, cf_items, iso_now,
                             iter_finding_files, read_json, read_yaml)

#: keys under which a findings file may carry its defect list (v1 and v2)
FINDING_LISTS = ("disagreements", "findings")
#: bucket for rows whose finding named no declaration
UNATTRIBUTED = "_unattributed"


def run_mode(run_id: str, corpus_dir: Path | None = None) -> str:
    """'attack' or 'differential' for a run.

    Prefers the run's own ``run.yaml`` (``mode: attack:hunt``); falls back to the
    run id, which is how the baseline's five directories are told apart
    (findings-vac / findings-hunt were the attack runs).
    """
    if corpus_dir is not None:
        for name in ("run.yaml", "run.yml"):
            path = Path(corpus_dir) / name
            if path.exists():
                try:
                    data = read_yaml(path)
                except Exception:
                    data = None
                mode = (data or {}).get("mode") if isinstance(data, dict) else None
                if isinstance(mode, str) and mode:
                    return "attack" if mode.strip().lower().startswith("attack") else "differential"
    low = run_id.lower()
    if any(tag in low for tag in ("attack", "vac", "hunt")):
        return "attack"
    return "differential"


def _kind_alternation(kinds: Sequence[str]) -> re.Pattern:
    alts = "|".join(sorted((re.escape(k) for k in kinds), key=len, reverse=True))
    return re.compile(r"\b(%s)\s+(\d+(?:\.\d+)+)" % alts)


def collapse_to_book_item(label: str, kinds: Sequence[str] | None = None) -> str:
    """The book item a finding label belongs to.

    ``Exercise 1.6.27(iii)`` -> ``Exercise 1.6.27``;
    ``In-text definition, Exercise 1.4.24`` -> ``Exercise 1.4.24``;
    ``In-text, book p. 68`` -> ``In-text p.68`` (no numbered item to collapse to).

    The search is deliberately positional rather than a strict parse: worker
    labels wrap the item in prose ("In-text definition, Exercise 1.4.24"), and
    the strict grammar in cflib.items rejects those, which would leave two rows
    for one book item.  Whitespace is normalised first, so ``Exercise    1.3.19``
    collapses like any other.
    """
    kinds = list(kinds or cf_items.DEFAULT_KINDS)
    cleaned = cf_items.clean(label or "")
    match = _kind_alternation(kinds).search(cleaned)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    item, _reject = cf_items.try_parse(cleaned, kinds)
    if item is not None:
        return item.id
    return cleaned


def _defects(payload: dict) -> list[dict]:
    """Every defect row in a findings file, whatever key it used."""
    out: list[dict] = []
    for key in FINDING_LISTS:
        value = payload.get(key)
        if isinstance(value, list):
            out.extend(d for d in value if isinstance(d, dict))
    return out


def _decl_repos(defect: dict) -> dict[str, list[str]]:
    """{repo prefix: [repo:decl, ...]} from ``unfaithful_decls``."""
    out: dict[str, list[str]] = collections.defaultdict(list)
    for decl in defect.get("unfaithful_decls") or []:
        text = str(decl).strip()
        if not text:
            continue
        repo = text.split(":", 1)[0] if ":" in text else UNATTRIBUTED
        out[repo].append(text)
    return dict(out)


def rows_for_run(run_id: str, directory: str | Path,
                 kinds: Sequence[str] | None = None,
                 mode: str | None = None) -> list[dict]:
    """One row per (defect, repo it names) across every findings file in *directory*."""
    directory = Path(directory)
    mode = mode or run_mode(run_id, directory)
    rows: list[dict] = []
    for path in iter_finding_files(directory):
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            continue
        item_raw = str(payload.get("item") or path.stem)
        for defect in _defects(payload):
            by_repo = _decl_repos(defect) or {UNATTRIBUTED: []}
            for repo, decls in sorted(by_repo.items()):
                rows.append({
                    "run": run_id,
                    "mode": mode,
                    "file": path.name,
                    "repo": repo,
                    "item_raw": item_raw,
                    "item": collapse_to_book_item(item_raw, kinds),
                    "kind": defect.get("kind"),
                    "severity": defect.get("severity"),
                    "concept": defect.get("concept"),
                    "decls": sorted(decls),
                    "finding_id": defect.get("finding_id"),
                    "faithful": defect.get("faithful"),
                })
    return rows


def collect(runs: dict[str, str | Path], kinds: Sequence[str] | None = None,
            modes: dict[str, str] | None = None) -> list[dict]:
    """Rows for several runs; ``runs`` maps run id -> corpus directory."""
    out: list[dict] = []
    for run_id, directory in runs.items():
        out.extend(rows_for_run(run_id, directory, kinds,
                                (modes or {}).get(run_id)))
    return out


def _classify_items(rows: Iterable[dict], key: str) -> dict:
    """Per-item kind/severity sets and the disposition classes of the baseline table."""
    kinds_by_item: dict[str, set] = collections.defaultdict(set)
    sev_by_item: dict[str, set] = collections.defaultdict(set)
    runs_by_item: dict[str, set] = collections.defaultdict(set)
    modes_by_item: dict[str, set] = collections.defaultdict(set)
    rows_by_item: dict[str, int] = collections.Counter()
    for row in rows:
        ident = row[key]
        kinds_by_item[ident].add(row["kind"])
        sev_by_item[ident].add(row["severity"])
        runs_by_item[ident].add(row["run"])
        modes_by_item[ident].add(row["mode"])
        rows_by_item[ident] += 1

    def has(ident: str, *wanted: str) -> bool:
        return bool(kinds_by_item[ident] & set(wanted))

    idents = sorted(kinds_by_item)
    classes = {
        # the four disposition classes of OPEN_FAITHFULNESS_FINDINGS.md's table
        "content_vac": sorted(i for i in idents if has(i, "CONTENT", "VACUITY")),
        "suspected_errata": sorted(i for i in idents
                                   if "SUSPECTED_ERRATA" in kinds_by_item[i]
                                   and not has(i, "CONTENT", "VACUITY")),
        "proxy": sorted(i for i in idents if "PROXY" in kinds_by_item[i]
                        and not has(i, "CONTENT", "VACUITY", "SUSPECTED_ERRATA")),
        "coverage": sorted(i for i in idents if kinds_by_item[i] == {"COVERAGE"}),
        "page_anchor": sorted(i for i in idents if kinds_by_item[i] == {"PAGE_ANCHOR"}),
    }
    substantive = sorted(i for i in idents if kinds_by_item[i] & SUBSTANTIVE_KINDS)
    high = sorted(i for i in idents if "high" in sev_by_item[i])
    attack = {i for i in idents if "attack" in modes_by_item[i]}
    differential = {i for i in idents if "differential" in modes_by_item[i]}
    return {
        "items": len(idents),
        "substantive": len(substantive),
        "high": len(high),
        "classes": {name: len(value) for name, value in classes.items()},
        "class_items": classes,
        "substantive_items": substantive,
        "high_items": high,
        "sets": {
            "attack_only": sorted(attack - differential),
            "differential_only": sorted(differential - attack),
            "both": sorted(attack & differential),
        },
        "detail": {
            i: {
                "rows": rows_by_item[i],
                "kinds": sorted(k for k in kinds_by_item[i] if k),
                "severities": sorted(s for s in sev_by_item[i] if s),
                "runs": sorted(runs_by_item[i]),
                "modes": sorted(modes_by_item[i]),
            } for i in idents
        },
    }


def tally(runs: dict[str, str | Path], kinds: Sequence[str] | None = None,
          modes: dict[str, str] | None = None, book_sha: str = "") -> dict:
    """The full tally over a set of runs, per repo prefix.

    Returns ``{generated_at, runs, repos: {repo: {...}}}`` where each repo has
    row counts by kind and severity, raw and collapsed item statistics, per-run
    counts, and the attack-only / differential-only item sets.
    """
    rows = collect(runs, kinds, modes)
    by_repo: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_repo[row["repo"]].append(row)

    out_repos = {}
    for repo, repo_rows in sorted(by_repo.items()):
        per_run = {}
        for run_id in runs:
            run_rows = [r for r in repo_rows if r["run"] == run_id]
            per_run[run_id] = {
                "rows": len(run_rows),
                "items": len({r["item_raw"] for r in run_rows}),
                "collapsed_items": len({r["item"] for r in run_rows}),
            }
        out_repos[repo] = {
            "rows": len(repo_rows),
            "by_kind": dict(collections.Counter(r["kind"] for r in repo_rows if r["kind"])),
            "by_severity": {s: sum(1 for r in repo_rows if r["severity"] == s) for s in SEVERITIES},
            "raw": _classify_items(repo_rows, "item_raw"),
            "collapsed": _classify_items(repo_rows, "item"),
            "per_run": per_run,
        }
    return {
        "generated_at": iso_now(),
        "book_sha": book_sha,
        "runs": {run_id: {"dir": str(directory),
                          "mode": (modes or {}).get(run_id) or run_mode(run_id, Path(directory)),
                          "files": len(iter_finding_files(directory))}
                 for run_id, directory in runs.items()},
        "rows": len(rows),
        "repos": out_repos,
    }


def state_counts(repo_tally: dict) -> dict:
    """The ``tally`` block of companions/<name>/state.json (architecture.md section 2)."""
    raw = repo_tally["raw"]
    return {
        "rows": repo_tally["rows"],
        "items": raw["items"],
        "collapsed_items": repo_tally["collapsed"]["items"],
        "substantive": raw["substantive"],
        "high": raw["high"],
        "content_vac": raw["classes"]["content_vac"],
        "suspected_errata": raw["classes"]["suspected_errata"],
        "coverage": raw["classes"]["coverage"],
        "page_anchor": raw["classes"]["page_anchor"],
    }


def render(result: dict, repos: Sequence[str] | None = None) -> str:
    """A short human table; every number in it is a key of *result*."""
    lines = [f"runs: " + ", ".join(f"{r} ({v['mode']}, {v['files']} files)"
                                   for r, v in result["runs"].items()),
             f"rows: {result['rows']}"]
    for repo, data in result["repos"].items():
        if repos and repo not in repos:
            continue
        raw, col = data["raw"], data["collapsed"]
        lines.append("")
        lines.append(f"[{repo}] {data['rows']} rows over {raw['items']} raw items "
                     f"({col['items']} book items)")
        lines.append("  kinds:      " + ", ".join(f"{k}={v}" for k, v in sorted(data["by_kind"].items())))
        lines.append("  severity:   " + ", ".join(f"{k}={v}" for k, v in data["by_severity"].items()))
        lines.append(f"  substantive: {raw['substantive']} raw / {col['substantive']} book items "
                     f"(CONTENT|VACUITY|SUSPECTED_ERRATA|PROXY)")
        lines.append(f"  high:        {raw['high']} raw / {col['high']} book items")
        lines.append("  classes:    " + ", ".join(f"{k}={v}" for k, v in raw["classes"].items()))
        lines.append("  per run:    " + ", ".join(f"{r}={v['items']}" for r, v in data["per_run"].items()))
        sets = col["sets"]
        lines.append(f"  attack-only={len(sets['attack_only'])} "
                     f"differential-only={len(sets['differential_only'])} "
                     f"both={len(sets['both'])}")
    return "\n".join(lines)


def parse_run_arg(value: str) -> tuple[str, str]:
    """``name=dir`` or a bare directory (whose basename becomes the run id)."""
    if "=" in value:
        name, _, directory = value.partition("=")
        return name, directory
    return Path(value).name, value


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone entry point: ``python3 scripts/harvest/tally.py --run a=dir ...``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="append", default=[], metavar="ID=DIR",
                        help="a corpus directory to count (repeatable)")
    parser.add_argument("--repo", action="append", default=[], help="limit the table to these repos")
    parser.add_argument("--json", action="store_true", help="print the full tally as JSON")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("at least one --run ID=DIR is required")
    runs = dict(parse_run_arg(v) for v in args.run)
    result = tally(runs)
    if args.json:
        import json
        print(json.dumps(result, indent=1, sort_keys=True))
    else:
        print(render(result, args.repo or None))
    return 0


if __name__ == "__main__":
    sys.exit(main())


#: kept for callers that imported the private spelling
_parse_run_arg = parse_run_arg
