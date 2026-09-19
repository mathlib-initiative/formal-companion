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
  ``In-text Exercise 1.4.24`` -- the collapsed label is the key every join
  uses, so an in-text label goes through the grammar and NOT through the
  positional search (see :func:`collapse_to_book_item`);
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
from typing import Iterable, Mapping, Sequence

if __package__ in (None, ""):  # `python3 scripts/harvest/tally.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvest import harvest as harvest_mod
from harvest._compat import (SEVERITIES, SUBSTANTIVE_KINDS, CfError, cf_items,
                             hashing, iso_now, iter_finding_files, read_json,
                             read_yaml)

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
    """The source item a finding label belongs to -- the key every join uses.

    ``kinds`` is ONE SOURCE's kinds list.  It has to be, because the kinds decide
    which labels collapse at all: a source whose grammar has no ``Proposition``
    would silently keep ``Proposition 1.2.3`` as a raw label if it were handed a
    sibling edition's kinds, and the two would tally as two items.

    ``Exercise 1.6.27(iii)`` -> ``Exercise 1.6.27``;
    ``Corollary (Lemma 1.2.5 + Exercise 1.2.4)`` -> ``Lemma 1.2.5``;
    ``In-text, book p. 68`` -> ``In-text p.68``;
    ``In-text definition, Exercise 1.4.24`` -> ``In-text Exercise 1.4.24``.

    For a NUMBERED label the search is deliberately positional rather than a
    strict parse: worker labels wrap the item in prose, and the strict grammar
    rejects those, which would leave two rows for one book item.  Whitespace is
    normalised first, so ``Exercise    1.3.19`` collapses like any other.

    An IN-TEXT label never reaches that search.  It goes through
    ``cflib.items.join_key``, for two reasons:

    * the positional search hijacks it.  ``In-text, book p. 51 / Definition
      1.3.6`` collapsed onto ``Definition 1.3.6`` and ``In-text convention,
      book p. 29 (Exercise 1.2.5: ...)`` onto ``Exercise 1.2.5`` -- a page-keyed
      item filed under a numbered item it merely cites, which is both a lost
      join (the same page under another label lands elsewhere) and a false one
      (the numbered item collects findings that are not about it);
    * ``In-text Definition 1.4.35`` is not ``Definition 1.4.35``.  It is the
      unnumbered material next to it, so it keeps its own key.

    An in-text sub-part is NOT collapsed: ``In-text Lemma 1.2.13(ii)`` and
    ``In-text Lemma 1.2.13(iii)`` are two anchors, and both corpus generations
    spell them as two items.  ``Exercise 1.6.27(iii)`` still collapses, because
    that one is a part of an item the book itself numbers.
    """
    kinds = list(kinds or cf_items.DEFAULT_KINDS)
    cleaned = cf_items.clean(label or "")
    if cf_items.is_in_text(cleaned):
        key, _reject = cf_items.try_join_key(cleaned, kinds)
        return key  # an unparseable in-text label keeps itself; see unjoinable()
    match = _kind_alternation(kinds).search(cleaned)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    item, _reject = cf_items.try_parse(cleaned, kinds)
    if item is not None:
        return item.id
    return cleaned


def unjoinable(rows: Iterable[dict], kinds=None) -> list[dict]:
    """In-text labels the grammar could not canonicalise, with the reason.

    Nothing is dropped: such a label still tallies under itself.  But it cannot
    be joined to the same item under another spelling, so every caller that
    joins by item reports this list rather than letting the row look unique.
    """
    rows = list(rows)
    seen: dict[tuple[str, str], dict] = {}
    for row in rows:
        raw = str(row.get("item_raw") or "")
        source_id = str(row.get("source_id") or "")
        if (source_id, raw) in seen or not cf_items.is_in_text(raw):
            continue
        _key, reject = cf_items.try_join_key(raw, _kinds_for(kinds, source_id)
                                             or cf_items.DEFAULT_KINDS)
        if reject is None:
            continue
        seen[(source_id, raw)] = {
            "source_id": source_id,
            "item_raw": raw,
            "item": str(row.get("item") or ""),
            "reason": reject.reason,
            "runs": sorted({str(r.get("run") or "") for r in rows
                            if str(r.get("item_raw") or "") == raw}),
        }
    return [seen[k] for k in sorted(seen)]


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


def _kinds_for(kinds, source_id: str) -> Sequence[str] | None:
    """``kinds`` may be one list or ``{source_id: kinds}`` -- pick this source's."""
    if isinstance(kinds, Mapping):
        return kinds.get(source_id) or kinds.get("") or None
    return kinds


def computed_finding_id(run_id: str, source_id: str, item: str, kind: str,
                        decls: Sequence[str]) -> str:
    """The defect's id, RECOMPUTED from its content -- never the worker's value.

    ``references/vocabulary.md`` says the id "is assigned at harvest ... a worker
    cannot be trusted to compute it".  It was not: the row's own ``finding_id``
    was copied straight through into the union and the ledger, so a worker that
    wrote ``"finding_id": "00" * 32`` on every row had it stored verbatim and
    every adjudication linked by it.  The id is content-addressed, so the content
    is what it is computed from, here, every time the corpus is read.

    ``decls`` is the defect's WHOLE ``unfaithful_decls`` list, not the per-repo
    slice the row is filed under: the id belongs to the defect, and one defect
    can name two repos.  Each declaration is hashed AS WRITTEN (``finding_id``
    strips it and nothing else); ``ledger/findings.py`` recomputes the same id
    from the same document and the two must agree byte for byte, so neither may
    hash a matching form.
    """
    return hashing.finding_id(run_id, source_id, item, kind, decls)


def defect_key(source_id: str, item: str, kind: str, decls: Sequence[str]) -> str:
    """:func:`computed_finding_id` with NO run id -- one key for one defect.

    ``run_id`` is the first part of ``finding_id``, so the same defect found by
    two runs gets two ids and nothing can link them: ``union --group-by
    finding-id --new-only`` answered "everything is new".  This key drops it and
    keeps every other part, so it is the id two runs of the same defect share.
    It is a KEY, never written into a finding: the stored id is unchanged.
    """
    return hashing.finding_id("", source_id, item, kind, decls)


def id_mismatches(rows: Iterable[dict]) -> list[dict]:
    """Rows whose stored ``finding_id`` is not the one their content hashes to.

    Recorded and reported rather than corrected in silence: a mismatch means
    either a worker computed the id wrongly or wrote one it invented, and both
    are facts about the corpus the receipt's reader needs.  The computed id is
    the one every number and every ledger link uses.
    """
    seen: dict[tuple, dict] = {}
    for row in rows:
        stored = str(row.get("finding_id_stored") or "")
        if not stored:
            continue
        key = (str(row.get("run") or ""), str(row.get("file") or ""), stored)
        seen.setdefault(key, {
            "run": key[0],
            "file": key[1],
            "item": str(row.get("item_raw") or ""),
            "kind": row.get("kind"),
            "stored": stored,
            "computed": str(row.get("finding_id") or ""),
        })
    return [seen[k] for k in sorted(seen)]


def rows_for_run(run_id: str, directory: str | Path,
                 kinds=None,
                 mode: str | None = None,
                 sources: Sequence[str] | None = None) -> list[dict]:
    """One row per (defect, repo it names) across every findings file in *directory*.

    Every row carries ``source_id`` and ``source_sha``.  ``kinds`` may be one
    list or a ``{source_id: kinds}`` map, because the item grammar is per source.
    ``sources`` filters to the named source ids -- a corpus directory can hold
    two sources' files, and a tally of one of them must not count the other's.

    Every row's ``finding_id`` is RECOMPUTED here (:func:`computed_finding_id`),
    with the stored value kept as ``finding_id_stored`` when the two disagree.
    The inputs to that hash are read from the same places ``ledger/findings.
    normalise_finding`` reads them -- a defect's own ``run_id`` and ``source_id``
    win over the harvest run's, the declarations go in as written -- because the
    two readers key the two halves of the pipeline (tally/union/gate against
    route/adjudicate/transcribe) and an id computed two ways links neither.
    """
    directory = Path(directory)
    mode = mode or run_mode(run_id, directory)
    wanted = {str(s) for s in sources} if sources else None
    rows: list[dict] = []
    for path in iter_finding_files(directory):
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            # A findings file that is valid JSON but not an object used to be
            # skipped without a word, so the corpus was a file short and every
            # number computed from it was quietly a lower bound -- the exact
            # failure the receipt exists to prevent.  The gate already calls it
            # an error; so does the count.
            raise CfError(
                f"{path.name} is not a findings object (it is "
                f"{'null' if payload is None else type(payload).__name__})",
                hint="a findings file is an object carrying `disagreements`; a corpus with "
                     "one it cannot read makes every number a lower bound. Fix or re-run "
                     f"that task, then `cf harvest {run_id}` again")
        source_id = str(payload.get("source_id") or "")
        if wanted is not None and source_id not in wanted:
            continue
        source_sha = str(payload.get("source_sha") or payload.get("book_sha") or "")
        item_raw = str(payload.get("item") or path.stem)
        item = collapse_to_book_item(item_raw, _kinds_for(kinds, source_id))
        for defect in _defects(payload):
            by_repo = _decl_repos(defect) or {UNATTRIBUTED: []}
            row_source = str(defect.get("source_id") or source_id)
            all_decls = [str(d).strip() for d in (defect.get("unfaithful_decls") or [])
                         if str(d).strip()]
            kind = defect.get("kind")
            # The id's run part is the run that FOUND the defect.  A defect that
            # carries its own ``run_id`` keeps it, because the ledger reader does
            # (``normalise_finding``: ``raw.get("run_id") or run_id``) and a
            # routed row read back has to hash to the id the corpus carries.  The
            # row's ``run`` below stays the run this corpus directory IS.
            row_run = str(defect.get("run_id") or run_id or "")
            computed = computed_finding_id(row_run, row_source, item_raw,
                                          str(kind or "").strip(), all_decls)
            stored = str(defect.get("finding_id") or "")
            for repo, decls in sorted(by_repo.items()):
                row = {
                    "run": run_id,
                    "mode": mode,
                    "file": path.name,
                    "repo": repo,
                    "source_id": row_source,
                    "source_sha": str(defect.get("source_sha") or source_sha),
                    "item_raw": item_raw,
                    "item": item,
                    "kind": kind,
                    "severity": defect.get("severity"),
                    "concept": defect.get("concept"),
                    "decls": sorted(decls),
                    "finding_id": computed,
                    "defect_key": defect_key(row_source, item_raw,
                                             str(kind or "").strip(), all_decls),
                    "faithful": defect.get("faithful"),
                }
                if stored and stored != computed:
                    row["finding_id_stored"] = stored
                rows.append(row)
    return rows


def collect(runs: dict[str, str | Path], kinds=None,
            modes: dict[str, str] | None = None,
            sources: Sequence[str] | None = None) -> list[dict]:
    """Rows for several runs; ``runs`` maps run id -> corpus directory."""
    out: list[dict] = []
    for run_id, directory in runs.items():
        out.extend(rows_for_run(run_id, directory, kinds,
                                (modes or {}).get(run_id), sources=sources))
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


#: how a group key is spelled in ``tally()["groups"]``
GROUP_SEP = "::"
#: the default grouping (docs/design/multi-source-model.md S0.2 rule 4)
DEFAULT_GROUP_BY: tuple[str, ...] = ("source_id", "repo")


def group_key(row: Mapping, group_by: Sequence[str] = DEFAULT_GROUP_BY) -> str:
    """``"<source_id>::<repo>"`` for one row."""
    return GROUP_SEP.join(str(row.get(field) or "") for field in group_by)


def _block(rows: Sequence[dict], runs: Mapping) -> dict:
    """The per-group tally block (counts, classes, per-run)."""
    per_run = {}
    for run_id in runs:
        run_rows = [r for r in rows if r["run"] == run_id]
        per_run[run_id] = {
            "rows": len(run_rows),
            "items": len({r["item_raw"] for r in run_rows}),
            "collapsed_items": len({r["item"] for r in run_rows}),
        }
    return {
        "rows": len(rows),
        "by_kind": dict(collections.Counter(r["kind"] for r in rows if r["kind"])),
        "by_severity": {s: sum(1 for r in rows if r["severity"] == s) for s in SEVERITIES},
        "raw": _classify_items(rows, "item_raw"),
        "collapsed": _classify_items(rows, "item"),
        "per_run": per_run,
    }


def tally(runs: dict[str, str | Path], kinds=None,
          modes: dict[str, str] | None = None,
          sources: Sequence[str] | None = None,
          group_by: Sequence[str] = DEFAULT_GROUP_BY) -> dict:
    """The full tally over a set of runs, grouped by ``(source_id, repo)``.

    Returns ``{generated_at, runs, groups: {...}, repos: {repo: {...}}}``.
    ``groups`` is the primary output -- an item id means a different item in a
    different source, so collapsing two sources' rows onto one repo total is a
    count nobody can act on.  ``repos`` is the per-repo rollup the state block
    and the human table read, and each repo entry names its sources so the
    rollup is never mistaken for a single-source number.
    """
    group_by = tuple(group_by) or DEFAULT_GROUP_BY
    rows = collect(runs, kinds, modes, sources=sources)
    by_group: dict[str, list[dict]] = collections.defaultdict(list)
    by_repo: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_group[group_key(row, group_by)].append(row)
        by_repo[row["repo"]].append(row)

    groups = {}
    for key, group_rows in sorted(by_group.items()):
        block = _block(group_rows, runs)
        for field in group_by:
            block[field] = str(group_rows[0].get(field) or "")
        groups[key] = block

    out_repos = {}
    for repo, repo_rows in sorted(by_repo.items()):
        block = _block(repo_rows, runs)
        block["sources"] = {}
        for sid in sorted({str(r.get("source_id") or "") for r in repo_rows}):
            sid_rows = [r for r in repo_rows if str(r.get("source_id") or "") == sid]
            block["sources"][sid] = _block(sid_rows, runs)
        out_repos[repo] = block
    return {
        "generated_at": iso_now(),
        "group_by": list(group_by),
        "sources": {sid: sha for sid, sha in sorted(
            {str(r.get("source_id") or ""): str(r.get("source_sha") or "") for r in rows}.items())},
        "runs": {run_id: {"dir": str(directory),
                          "mode": (modes or {}).get(run_id) or run_mode(run_id, Path(directory)),
                          "files": len(iter_finding_files(directory))}
                 for run_id, directory in runs.items()},
        "rows": len(rows),
        "groups": groups,
        "repos": out_repos,
        # in-text labels that could not be canonicalised: they tally under
        # themselves and therefore cannot join the same item under another
        # spelling.  Reported, never dropped.
        "unjoinable": unjoinable(rows, kinds),
        # a worker-written finding_id that is not the id the row's content
        # hashes to.  The computed one is used; the disagreement is recorded.
        "finding_id_mismatches": id_mismatches(rows),
        # runs whose RECEIPT.json carries no gate block: their quotes were never
        # checked against the source text, and every number below includes them.
        "ungated": harvest_mod.ungated_runs(runs),
    }


def state_counts(repo_tally: dict) -> dict:
    """The ``tally`` block of companions/<name>/state.json (architecture.md section 2).

    Accepts either a repo rollup or one ``(source_id, repo)`` group; a rollup
    additionally carries ``by_source`` so `cf status` can say which source a
    number came from instead of printing one figure over two editions.
    """
    raw = repo_tally["raw"]
    out = {
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
    if repo_tally.get("sources"):
        out["by_source"] = {sid: {"rows": blk["rows"], "items": blk["raw"]["items"],
                                  "substantive": blk["raw"]["substantive"],
                                  "high": blk["raw"]["high"]}
                            for sid, blk in repo_tally["sources"].items()}
    if repo_tally.get("source_id"):
        out["source_id"] = repo_tally["source_id"]
    return out


def render(result: dict, repos: Sequence[str] | None = None) -> str:
    """A short human table; every number in it is a key of *result*."""
    lines = [f"runs: " + ", ".join(f"{r} ({v['mode']}, {v['files']} files)"
                                   for r, v in result["runs"].items()),
             f"rows: {result['rows']}"]
    if result.get("unjoinable"):
        lines.append("UNJOINABLE in-text labels (reported, not dropped): "
                     + ", ".join("%s (%s)" % (u["item_raw"], u["reason"])
                                 for u in result["unjoinable"]))
    if result.get("finding_id_mismatches"):
        bad = result["finding_id_mismatches"]
        lines.append(f"FINDING_ID MISMATCH in {len(bad)} row(s): the id the worker wrote is not "
                     "the id the row's content hashes to. The computed id is the one every "
                     "number and every ledger link below uses: "
                     + ", ".join(f"{m['file']} ({m['stored'][:12]} != {m['computed'][:12]})"
                                 for m in bad[:6]))
    if result.get("ungated"):
        lines.append(f"UNGATED run(s): {', '.join(result['ungated'])} -- no gate block in "
                     "RECEIPT.json, so no quote in them was checked against the source text. "
                     "Re-harvest without --no-gate.")
    for key, data in result.get("groups", {}).items():
        repo = data.get("repo", key)
        if repos and repo not in repos:
            continue
        source_id = data.get("source_id") or "(unstamped)"
        raw, col = data["raw"], data["collapsed"]
        lines.append("")
        lines.append(f"[{source_id} / {repo}] {data['rows']} rows over {raw['items']} raw items "
                     f"({col['items']} source items)")
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
