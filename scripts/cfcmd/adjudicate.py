"""``cf adjudicate`` -- prepare, transcribe and validate the adjudication stage.

    python3 scripts/cf.py adjudicate --companion adam --emit-args
    python3 scripts/cf.py adjudicate --companion adam --challenge-only --emit-args
    python3 scripts/cf.py adjudicate --companion adam --transcribe result.json
    python3 scripts/cf.py adjudicate --validate

Stage 7 runs in a Claude Code session, not in this process: the lenses,
reconciler and skeptics are a Workflow script (``workflows/adjudicate.workflow.js``),
and a Workflow cannot be launched from a Python CLI.  So this command does the
three things that ARE mechanical:

1. ``--emit-args`` builds the workflow's ``args`` from the routed findings, the
   statement tables and the campaign paths, and prints the invocation to paste
   into the session (replacing the hand-assembled workflow_inputs.json of
   recovered/c4082627_L225_Bash.txt);
2. ``--transcribe`` turns the saved result into ledger rows, the two .jsonl
   files and ``adjudicated.<name>.md``, and advances ``state.json.adjudication``;
3. ``--validate`` machine-checks the ledger against the corpus.

With no flags it reports what still needs adjudicating, which is the number
``route`` uses to decide CHALLENGED vs UNCHALLENGED.
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):  # direct execution
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse

from ledger import compat, transcribe as transcribe_mod, workflow_args
from ledger import findings as findings_mod
from ledger import validate_ledger as vl
from route import route as route_mod

NAME = "adjudicate"
HELP = "prepare the adjudication/challenge workflows, transcribe their results, validate the ledger"

ADJUDICATE_WORKFLOW = "workflows/adjudicate.workflow.js"
CHALLENGE_WORKFLOW = "workflows/challenge.workflow.js"


def register(subparsers):
    """cf.py discovery hook."""
    p = subparsers.add_parser(
        NAME, help=HELP, description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--campaign", help="campaign directory (default: $CF_CAMPAIGN or cwd upward)")
    p.add_argument("--companion", action="append", default=[],
                   help="companion to act on (repeatable; default: all)")
    p.add_argument("--corpus", action="append", default=[],
                   help="corpus run directory or findings file (default: every corpus/<run>/)")
    p.add_argument("--emit-args", action="store_true",
                   help="write the workflow args JSON and print the invocation")
    p.add_argument("--challenge-only", action="store_true",
                   help="with --emit-args: only rows with no ledger verdict, for challenge.workflow.js")
    p.add_argument("--refuters", type=int, default=2, help="refuters per finding (challenge)")
    p.add_argument("--transcribe", default="", help="JSON file holding a workflow's return value")
    p.add_argument("--source", choices=("adjudicate", "challenge"), default="adjudicate",
                   help="which workflow produced --transcribe")
    p.add_argument("--at", default="", help="date stamp for transcribed rows (default: file mtime)")
    p.add_argument("--definitions", default="", help="JSON file of definition items (group D)")
    p.add_argument("--uncovered", default="", help="JSON file of uncovered definitions (group B3)")
    p.add_argument("--contested-threshold", type=float, default=0.7)
    p.add_argument("--validate", action="store_true", help="validate the ledger against the corpus")
    p.set_defaults(func=run)
    return p


def _corpus_sources(paths, explicit: list[str]) -> list[str]:
    if explicit:
        return explicit
    root = paths.corpus()
    if not os.path.isdir(root):
        return []
    return [
        os.path.join(root, d) for d in sorted(os.listdir(root))
        if os.path.isdir(os.path.join(root, d))
    ]


def _routed_or_route(paths, companion, all_findings, book_sha, ledger_rows, campaign_root):
    """Prefer the routed file; fall back to routing in memory."""
    path = paths.routed_findings(companion)
    if os.path.exists(path):
        return route_mod.read_routed(path)
    return route_mod.route_repo(
        all_findings, companion, book_sha=book_sha, ledger_rows=ledger_rows,
        campaign=campaign_root,
    )


def _load_list(path: str) -> list[dict]:
    if not path:
        return []
    doc = compat.read_json(path)
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for key in ("items", "definitions", "rows", "entries"):
            if isinstance(doc.get(key), list):
                return doc[key]
    return []


def run(args) -> int:
    cfg = compat.load_campaign(args.campaign)
    paths = cfg.paths
    book_sha = cfg.book_sha
    names = args.companion or cfg.companion_names()
    if not names:
        print("no companions: add them to campaign.yaml or run `cf onboard`", file=sys.stderr)
        return 2

    sources = _corpus_sources(paths, args.corpus)
    all_findings = findings_mod.load_findings(sources, book_sha=book_sha) if sources else []
    index = findings_mod.index_by_id(all_findings)

    if args.transcribe:
        report = transcribe_mod.transcribe(
            args.transcribe,
            campaign=paths.root,
            companion=names[0],
            book_sha=book_sha,
            index=index,
            source=args.source,
            at=args.at,
        )
        for err in report.get("errors") or []:
            print(str(err), file=sys.stderr)
        ledger = vl.load_ledger_dir(paths, names[0])
        counts: dict[str, int] = {}
        for row in ledger["rows"]["accepted"] + ledger["rows"]["refuted"]:
            key = str(row.get("verdict") or "")
            counts[key] = counts.get(key, 0) + 1
        compat.update_state(
            paths, names[0],
            lambda st, _r=report, _c=counts: compat.state_section(st, "adjudication", {
                "accepted": _r["accepted"],
                "refuted": _r["refuted"],
                "contested": _r["contested"],
                # the names `cf status` reads (cflib.state.summary_row)
                "confirmed": _c.get("CONFIRMED_CONTENT", 0),
                "attribution_gap": _c.get("ATTRIBUTION_GAP", 0),
                "convention": _c.get("CONVENTION", 0),
                "errata": _c.get("ERRATA", 0),
                "unsure": _c.get("UNSURE", 0),
                "by_verdict": _c,
                "method": "workflow:%s" % args.source,
                "errors": len(_r.get("errors") or []),
            }),
        )
        print("%s: %d accepted, %d refuted, %d contested"
              % (names[0], report["accepted"], report["refuted"], report["contested"]))
        for f in report["files"]:
            print("  %s" % f)
        return 0 if not report.get("errors") else 1

    rc = 0
    for name in names:
        ledger = vl.load_ledger_dir(paths, name)
        ledger_rows = ledger["rows"]["accepted"] + ledger["rows"]["refuted"]

        if args.validate:
            report = vl.validate_ledger_dir(paths, name, index=index or None, book_sha=book_sha)
            for err in report["errors"]:
                print(str(err), file=sys.stderr)
            print("%s: %d accepted + %d refuted rows, %d error(s)"
                  % (name, report["accepted"], report["refuted"], len(report["errors"])))
            rc = rc or (0 if report["ok"] else 1)
            continue

        routed = _routed_or_route(paths, name, all_findings, book_sha, ledger_rows, paths.root)
        rows = [r for r in routed["rows"] if not r.get("withdrawn")]
        unchallenged = [r for r in rows if not r.get("challenged")]

        if not args.emit_args:
            counts = routed["meta"].get("counts") or {}
            print("%s: %d routed row(s) over %d item(s); %d challenged, %d unchallenged (%s)"
                  % (name, counts.get("rows", len(rows)), counts.get("items", 0),
                     counts.get("challenged", 0), len(unchallenged), routed["meta"].get("status", "")))
            if unchallenged:
                print("  next: python3 scripts/cf.py adjudicate --campaign %s --companion %s --emit-args"
                      % (paths.root, name))
            continue

        selected = unchallenged if args.challenge_only else rows
        if not selected:
            print("%s: nothing to %s" % (name, "challenge" if args.challenge_only else "adjudicate"))
            continue
        if args.challenge_only:
            payload = workflow_args.build_challenge_args(
                cfg, name, selected, refuters=args.refuters)
            script = CHALLENGE_WORKFLOW
        else:
            payload = workflow_args.build_adjudicate_args(
                cfg, name, selected,
                definitions=_load_list(args.definitions),
                uncovered=_load_list(args.uncovered),
                contested_threshold=args.contested_threshold,
            )
            script = ADJUDICATE_WORKFLOW
        kind = "challenge" if args.challenge_only else "adjudicate"
        out = paths.args_file(kind, name)
        compat.write_json(out, payload)
        agents = workflow_args.agent_estimate(payload)
        print("%s: %d finding(s) -> %s" % (name, len(selected), out))
        print("  run it in a Claude Code session (a Workflow cannot be launched from this CLI):")
        print("    Workflow({ scriptPath: \"%s\", args: <the JSON in %s> })"
              % (os.path.join(_skill_root(), script), out))
        print("  ~%d subagents; resume with resumeFromRunId if the session limit stops it." % agents)
        print("  then: python3 scripts/cf.py adjudicate --campaign %s --companion %s "
              "--transcribe <result.json> --source %s" % (paths.root, name, kind))
    return rc


def _skill_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers()
    register(sub)
    ns = parser.parse_args()
    raise SystemExit(run(ns))
