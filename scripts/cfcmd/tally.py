"""cf tally -- recompute every number from the corpus.

New code, generalising the tally at
tao-measure-lean/docs/OPEN_FAITHFULNESS_FINDINGS.md:296-311.

    cf tally                          # every harvested run, every repo
    cf tally --run attack-hunt-claude # one run
    cf tally --repo ours --json
    cf tally --source tao-measure-gsm126-published   # one source's numbers
    cf tally --union --agreed         # also write union.<repo>.json and agreed_high.json
    cf tally --union --baseline diff-claude-text --new-only   # what a new run adds
    cf tally --locate --run loc-1 --challenge-run chl-1      # a locate run's outcomes

"Do not decide 'nothing is open' by re-reading the prose": the figure in that
file was wrong three times, and the third time the corpus itself was a file
short.  So this command reads corpus/, writes reports/, and writes the counts
into each companion's state.json -- and `cf status` prints only what was
written here.
"""
from __future__ import annotations

import json
from pathlib import Path

from cflib import log
from cflib import state as state_mod
from cflib.workspace import from_args
from cflib.errors import CfError
from cflib.jsonio import write_json

from harvest import agreed as agreed_mod
from harvest import tally as tally_mod
from harvest import union as union_mod


def register(subparsers) -> None:
    p = subparsers.add_parser("tally", help="recompute finding counts from corpus/")
    p.add_argument("--run", action="append", default=[],
                   help="a run id in corpus/ (repeatable; default: all)")
    p.add_argument("--repo", action="append", default=[], help="limit the table to these repos")
    p.add_argument("--source", dest="source_id", default=None,
                   help="count only findings that read this source (the table groups by "
                        "(source_id, repo) either way)")
    p.add_argument("--union", action="store_true", help="also write reports/union.<repo>.json")
    p.add_argument("--agreed", action="store_true", help="also write reports/agreed_high.json")
    p.add_argument("--baseline", action="append", default=[],
                   help="run ids that count as already known (for --new-only)")
    p.add_argument("--new-only", action="store_true", help="union keeps only what the baseline missed")
    p.add_argument("--min-runs", type=int, default=2, help="agreement threshold (default 2)")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.add_argument("--no-write", action="store_true", help="print only; write no reports")
    p.add_argument("--locate", action="store_true",
                   help="tally ONE locate run (--run): finding rows, passage outcomes and "
                        "derived statuses into reports/locate-tally.<run>.json and "
                        "reports/locate-outcomes.<run>.jsonl")
    p.add_argument("--challenge-run", action="append", default=[],
                   help="with --locate: a challenge run whose verdicts apply (repeatable)")


def run(args) -> int:
    workspace = from_args(args)
    paths = workspace.paths
    if getattr(args, "locate", False):
        return _locate(workspace, args)
    if getattr(args, "challenge_run", None):
        raise CfError("--challenge-run belongs to --locate")
    runs = _runs(workspace, args.run)
    if not runs:
        raise CfError("no harvested runs found in corpus/",
                      hint="run `cf harvest <run-id>` first")

    kinds = {sid: list(workspace.kinds(sid)) for sid in workspace.source_ids()}
    sources = [args.source_id] if args.source_id else None
    if args.source_id:
        workspace.source(args.source_id)  # raises when unknown
    result = tally_mod.tally(runs, kinds=kinds, sources=sources)
    if args.as_json:
        print(json.dumps(result, indent=1, sort_keys=True))
    else:
        log.info(tally_mod.render(result, args.repo or None))
    # The receipt's gate block is the only record that a corpus was validated
    # against the source text, and nothing used to read it: an ungated run's
    # numbers looked exactly like a gated one's.
    if result.get("ungated"):
        log.warn(f"ungated run(s): {', '.join(result['ungated'])} -- their RECEIPT.json carries "
                 "no gate block, so no quote in them was checked against the source text. "
                 f"The counts above include them. {tally_mod.REGATE_HINT}")
    # A gate that RAN and rejected is not the same state as no gate at all, and
    # only the second one was ever named here: a rejected run's rows -- a quote
    # the gate could not find in the source text -- were counted, and routed,
    # with nothing in the counting authority's own output saying so.
    if result.get("gate_failed"):
        log.warn(f"gate FAILED for run(s): {', '.join(result['gate_failed'])} -- their quotes "
                 "were checked against the source text and rejected (see "
                 "corpus/<run-id>/RECEIPT.json gate.problems). The counts above include them; "
                 f"re-run those tasks or drop that corpus. {tally_mod.REGATE_HINT}")
    if result.get("finding_id_mismatches"):
        log.warn(f"{len(result['finding_id_mismatches'])} row(s) carry a finding_id that is not "
                 "the id their content hashes to. The computed id is the one counted and "
                 "linked; see finding_id_mismatches in the tally file.")

    if not args.no_write:
        run_key = args.run[0] if len(args.run) == 1 else "all"
        out = Path(paths.tally_json(run_key))
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json(out, result, indent=1, sort_keys=True)
        log.detail(f"-> {out}")
        _record_state(workspace, result, runs)

    if args.union:
        _write_unions(workspace, args, runs, result)
    if args.agreed:
        _write_agreed(workspace, args, runs)
    return 0


def _runs(workspace, wanted: list[str]) -> dict[str, Path]:
    """{run id: corpus directory}, defaulting to every directory in corpus/."""
    paths = workspace.paths
    corpus_dir = Path(paths.corpus_dir)
    if wanted:
        out = {}
        for run_id in wanted:
            directory = Path(paths.corpus_run_dir(run_id))
            if not directory.is_dir():
                raise CfError(f"no corpus for run {run_id!r} ({directory})")
            out[run_id] = directory
        return out
    if not corpus_dir.is_dir():
        return {}
    return {p.name: p for p in sorted(corpus_dir.iterdir()) if p.is_dir()}


def _write_unions(workspace, args, runs: dict[str, Path], result: dict) -> None:
    kinds = {sid: list(workspace.kinds(sid)) for sid in workspace.source_ids()}
    rows = tally_mod.collect(runs, kinds=kinds,
                             sources=[args.source_id] if args.source_id else None)
    for repo in sorted(result["repos"]):
        if args.repo and repo not in args.repo:
            continue
        entries = union_mod.union(rows, repo=repo, source_id=args.source_id,
                                  baseline_runs=args.baseline, new_only=args.new_only)
        doc = {
            "generated_at": tally_mod.iso_now(),
            "repo": repo,
            "source_id": args.source_id or "*",
            "runs": sorted(runs),
            "baseline_runs": sorted(args.baseline),
            "new_only": bool(args.new_only),
            "summary": union_mod.summary(entries),
            # the SAME kinds the rows were collapsed under, three lines up: a
            # source whose kinds list is narrower than the default (both live
            # ones are -- no Strategy, no Claim) has in-text labels that are
            # genuinely unjoinable under ITS grammar and joinable under the
            # default one, and reporting them under the default is the silent
            # drop this list exists to prevent.
            "unjoinable": tally_mod.unjoinable(rows, kinds),
            "entries": entries,
        }
        out = Path(workspace.paths.union_json(repo))
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json(out, doc, indent=1, sort_keys=True)
        log.info(f"union {repo}: {doc['summary']['entries']} entries "
                 f"({doc['summary']['new']} new, {doc['summary']['multi_run']} in >1 run) -> {out}")


def _write_agreed(workspace, args, runs: dict[str, Path]) -> None:
    doc = agreed_mod.build(runs, source_id=args.source_id, min_runs=args.min_runs)
    out = Path(workspace.paths.agreed_high_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, doc, indent=1, sort_keys=True)
    # "entries", not "loci": the key is (source_id, repo, item, kind), so one
    # locus faulted with two kinds is two entries and this count can rise where
    # the pre-kind file's fell. `locus_count` is the comparable figure.
    log.info(f"agreed (the same complaint in >= {args.min_runs} runs): {doc['count']} entries "
             f"over {doc['locus_count']} loci, "
             f"{doc['cross_family']} agreed across run families -> {out}")
    if doc["same_locus_count"]:
        # the weaker signal, named: several runs faulted the locus with different
        # complaints, which is a reason to look and not corroboration
        log.detail(f"  plus {doc['same_locus_count']} locus/loci several runs faulted with "
                   f"DIFFERENT complaints (same_locus in that file; not counted as agreement)")


def _record_state(workspace, result: dict, runs: dict[str, Path]) -> None:
    """Write each repo's counts into its companion state.json."""
    for name in workspace.companion_names():
        data = result["repos"].get(name)
        if not data:
            continue
        try:
            with state_mod.update(workspace, name) as state:
                state_mod.set_section(state, "tally", {
                    **tally_mod.state_counts(data),
                    "runs": sorted(runs),
                })
                state_mod.note(state, "tally", rows=data["rows"], runs=len(runs))
        except Exception as exc:
            log.detail(f"state.json for {name}: {exc}")


def _locate(workspace, args) -> int:
    """``--locate``: one locate run's rows, outcomes and counts (references/locate-documents.md
    section 10), written to the reports and the sourceset's state file."""
    from cflib.jsonio import write_jsonl
    from locate import outcomes as outcomes_mod, state as ss_state

    if len(args.run) != 1:
        raise CfError("--locate tallies one run", hint="pass exactly one --run <run-id>")
    if args.union or args.agreed or args.repo or args.source_id or args.baseline:
        raise CfError("--locate takes --run, --challenge-run, --json and --no-write only")
    run_id = args.run[0]
    paths = workspace.paths
    built = outcomes_mod.build(paths, run_id, challenge_runs=args.challenge_run)
    gate = built["gate"]
    result = {"run_id": run_id, "mode": built["mode"], "sourceset": built["sourceset"],
              "universe": built["universe"], "challenge_runs": built["challenge_runs"],
              "gate": gate, "counts": built["counts"], "problems": built["problems"],
              "rows": built["rows"]}
    if args.as_json:
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=1,
                         sort_keys=True))
    else:
        counts = built["counts"]
        log.info(f"locate tally {run_id} [{built['mode']}]: {len(built['rows'])} finding row(s)")
        for key in sorted(counts):
            log.info(f"  {key}: {counts[key]}")
    if not gate.get("receipt") or not gate.get("complete") or not gate.get("gate_ok"):
        log.warn(f"run {run_id} is not a complete, gated harvest (receipt={gate.get('receipt')}, "
                 f"complete={gate.get('complete')}, gate ok={gate.get('gate_ok')}): the counts "
                 "above are not ones the export will accept")
    for problem in built["problems"][:20]:
        log.warn(problem)
    if not args.no_write:
        write_json(paths.locate_tally_json(run_id), result, indent=1, sort_keys=True)
        if built["outcomes"]:
            write_jsonl(paths.locate_outcomes_jsonl(run_id),
                        [outcomes_mod.outcome_row(r) for r in built["outcomes"]])
        sourceset = (built["sourceset"] or {}).get("id")
        if sourceset:
            with ss_state.update(paths, sourceset) as st:
                ss_state.record(st, "tallies", run_id, {"mode": built["mode"],
                                                        "challenge_runs": built["challenge_runs"],
                                                        **built["counts"]})
    return 0
