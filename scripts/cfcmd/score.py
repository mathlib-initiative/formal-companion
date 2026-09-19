"""cf score -- measure a run against an answer key (precision, recall, F1).

New code.  Every other command counts what the fleet reported; this one counts
what it MISSED, which nothing in the corpus, the ledger or the tally can show:
a defect no worker raised leaves no artifact behind, so "the companion is
faithful" and "nobody looked" produce the same files.  Scoring a run over a
fixture whose defects were planted is the only measurement in the repo with a
recall denominator.

    cf score --run detect-notes-b-cheap --answer-key tests/e2e/answer-key.json
    cf score --run detect-notes-a-cheap --run anchor-check-<source> \
             --answer-key <key>              # several runs, one score
    cf score --corpus <dir> --answer-key <key> --json

It reads corpus documents only, so it works on the MECHANICAL rows `cf
detect-proxies` and `cf pair` write with no model in the loop -- which is what
makes the free detector's score reproducible by anyone.

Writes ``reports/score.<run>.json``; ``--no-write`` prints only.  Exit code 2 is
a user error; ``--fail-under <F1>`` turns a drop into exit code 1, for CI.
"""
from __future__ import annotations

import json
from pathlib import Path

from cflib import log
from cflib.errors import CfError
from cflib.jsonio import write_json
from cflib.workspace import from_args

from score import answer_key as key_mod
from score import rows as rows_mod
from score import score as score_mod


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "score",
        help="score findings against an answer key (precision / recall / F1)",
        description=__doc__)
    p.add_argument("--answer-key", required=True,
                   help="the answer key to score against (tests/e2e/answer-key.json ships one)")
    p.add_argument("--run", action="append", default=[],
                   help="a run id in corpus/ (repeatable)")
    p.add_argument("--corpus", action="append", default=[],
                   help="a corpus directory outside corpus/ (repeatable)")
    p.add_argument("--fail-under", type=float, default=None,
                   help="exit 1 when the overall F1 is below this")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="print the score document instead of the table")
    p.add_argument("--no-write", action="store_true", help="print only; write no report")


def run(args) -> int:
    workspace = from_args(args)
    paths = workspace.paths
    key = key_mod.load(args.answer_key)
    dirs = _dirs(workspace, args)
    kinds = {sid: list(workspace.kinds(sid)) for sid in workspace.source_ids()}
    _warn_about_key_sources(workspace, key)

    rows = rows_mod.rows_from_dirs(dirs, kinds=kinds)
    result = score_mod.score(rows, key, kinds=kinds)
    result["runs"] = {run_id: str(path) for run_id, path in sorted(dirs.items())}
    result["answer_key"]["path"] = str(Path(args.answer_key))

    if args.as_json:
        print(json.dumps(result, indent=1, sort_keys=True))
    else:
        log.info(score_mod.render(result))

    if not args.no_write:
        run_key = args.run[0] if (len(args.run) == 1 and not args.corpus) else "all"
        out = Path(paths.score_json(run_key))
        write_json(out, result, indent=1, sort_keys=True)
        log.detail(f"-> {out}")

    f1 = result["totals"]["f1"]
    if args.fail_under is not None and (f1 is None or f1 < args.fail_under):
        log.warn(f"F1 {'-' if f1 is None else f1} is below the floor {args.fail_under}")
        return 1
    return 0


def _dirs(workspace, args) -> dict[str, Path]:
    """{run id: corpus directory} from --run and --corpus.

    Neither defaults to "every run in corpus/": a score is only meaningful over
    the runs the key was written for, and silently pooling an unrelated run
    would report its findings as this fixture's false positives.
    """
    paths = workspace.paths
    out: dict[str, Path] = {}
    for run_id in args.run:
        directory = Path(paths.corpus_run_dir(run_id))
        if not directory.is_dir():
            raise CfError(f"no corpus for run {run_id!r} ({directory})",
                          hint="`cf status` lists the runs this workspace has harvested")
        out[run_id] = directory
    for raw in args.corpus:
        directory = Path(raw)
        if not directory.is_dir():
            raise CfError(f"no corpus directory at {directory}")
        out[directory.name] = directory
    if not out:
        raise CfError("nothing to score",
                      hint="pass --run <run-id> (a directory in corpus/) or --corpus <dir>")
    return out


def _warn_about_key_sources(workspace, key) -> None:
    """A key about a source or an edition this workspace does not hold.

    Named rather than refused: a key can legitimately carry entries for several
    sources, and a workspace that holds one of them still scores that one.  The
    sha is a warning for the same reason -- a fixture regenerated from the same
    generator has the same bytes, and a key that names an older edition still
    scores every item whose text did not move, which is a judgement for the
    person reading the table.
    """
    named = {str(e.get("source_id") or "") for e in key["entries"]}
    known = set(workspace.source_ids())
    unknown = sorted(named - known)
    if unknown and len(unknown) == len(named):
        log.warn(f"the answer key is about {', '.join(unknown)}, which this workspace does not "
                 "hold: every planted defect will count as missed. Score it in the workspace "
                 "the fixture was onboarded into.")
        return
    want = str(key.get("source_sha") or "")
    key_source = str(key.get("source_id") or "")
    if not want or key_source not in known:
        return
    have = str(workspace.source(key_source).sha256 or "")
    if have and have != want:
        log.warn(f"the answer key was written against {key_source} {want[:12]} and this "
                 f"workspace pins {have[:12]}: the items may have moved. Re-generate the "
                 "fixture, or check the key before quoting the numbers.")
