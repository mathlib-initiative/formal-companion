"""``cf report`` -- render the handoff and the claims-verification args.

    cf report
    cf report --tally reports/tally.hunt.json --gh-json prs.json

Four outputs, all mechanical:

1. ``reports/handoff.<date>.md`` from ``templates/handoff.md``, filled ONLY from
   computed artifacts -- tally files, ledger rows, routed ``_meta``, state.json,
   run.yaml and saved ``gh`` output.  A figure with no artifact behind it is
   written as ``TODO(...)`` with the command that would produce it; this module
   never calls the network and never counts anything by reading prose.
2. ``reports/coverage.json`` -- every figure of the handoff's section 0, per
   source and per chapter: items in the source, items in the audited scope, items
   in the differential's task universe, items paired by two or more companions,
   items a run examined, of those the ones whose run passed the quote gate, items
   that carried a finding, and items in scope no run ever looked at.  Beside them
   the documents those figures do NOT cover, because a figure that silently
   excludes a counted document is the failure this section exists to prevent:
   items examined outside the audited chapters (``cf tally`` counts them and
   section 0 does not) and corpus documents that could not be read at all, which
   make every figure above a lower bound.
3. ``reports/unexamined.<source_id>.json``, one per source -- the items in scope
   that no run examined, BY NAME.  A count can say "381 of 411"; only the names
   say which item was missed, and the handoff points its reader at this file.
4. ``reports/verify-claims.args.json`` -- the args for
   ``workflows/verify-claims.workflow.js``, whose lenses re-derive every claim in
   the draft from the same sources, with an independent second vote on the
   numbers.  An earlier pass of that workflow found 19 wrong claims out of 173.

The report is a workspace-internal document: while any routed file says
UNCHALLENGED it is not an upstream report, and section 1 says so.
"""

from __future__ import annotations

import argparse
import os
import sys

if __package__ in (None, ""):  # direct execution
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ledger import compat
from report import claims as claims_mod
from report import make_handoff as handoff_mod

NAME = "report"
HELP = "render the handoff from computed artifacts and emit the verify-claims args"

VERIFY_WORKFLOW = "workflows/verify-claims.workflow.js"


def register(subparsers):
    """cf.py discovery hook."""
    p = subparsers.add_parser(
        NAME, help=HELP, description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--workspace", help="workspace directory (default: $CF_WORKSPACE or cwd upward)")
    p.add_argument("--tally", action="append", default=[],
                   help="tally JSON to read (default: every reports/tally.*.json)")
    p.add_argument("--gh-json", action="append", default=[],
                   help="saved `gh ... --json` output (this command never calls gh)")
    p.add_argument("--out", default="", help="output path (default: reports/handoff.<date>.md)")
    p.add_argument("--date", default="", help="date stamp (default: today)")
    p.add_argument("--tldr", default="", help="override the computed TL;DR sentence")
    p.add_argument("--wrong", default="",
                   help="file holding the 'what this pass got wrong' section")
    p.add_argument("--draft", default="",
                   help="verify this draft instead of the rendered handoff")
    p.add_argument("--no-verify-args", action="store_true",
                   help="skip reports/verify-claims.args.json")
    p.set_defaults(func=run)
    return p


def run(args) -> int:
    wrong = ""
    if args.wrong:
        with open(args.wrong, "r", encoding="utf-8") as fh:
            wrong = fh.read().strip()

    report = handoff_mod.make_handoff(
        args.workspace or os.getcwd(),
        out_path=args.out,
        tally_files=args.tally,
        gh_files=args.gh_json,
        date=args.date,
        tldr=args.tldr,
        wrong_section=wrong,
    )
    ctx = report["context"]
    paths = ctx["_paths"]
    print("wrote %s" % report["path"])
    # Named, not written silently: the handoff tells its reader to open
    # unexamined.<source_id>.json, and `cf report` used to print neither it nor
    # coverage.json, so the reader had to guess that the file existed at all.
    for path in report["coverage_files"]:
        print("wrote %s" % path)
    # Said on the terminal as well as in the document: `cf tally` REFUSES a
    # corpus holding one of these (exit 2, "makes every number a lower bound")
    # and this command exits 0, so without a line here the same corpus is
    # complete to one command and unreadable to the other.
    unreadable = _unreadable(ctx["_coverage"])
    if unreadable:
        print("  %d corpus document(s) could not be read, so every coverage figure is a "
              "LOWER BOUND:" % len(unreadable))
        for name in unreadable[:12]:
            print("    %s" % name)
        print("    `cf tally --run <run-id>` refuses them by name and says why")
    # The same set §0 carries, for the same reason: `cf tally` prints UNGATED and
    # GATE FAILED for this corpus and exits 2 over some of it, and this command
    # exits 0, so a caveat only the document carries is one the reader meets only
    # if they open it.
    for line in _caveats(ctx["_coverage"]):
        print("  %s" % line)
    if report["todos"]:
        print("  %d figure(s) have no artifact behind them yet:" % len(report["todos"]))
        for t in report["todos"][:12]:
            print("    %s" % t)
    else:
        print("  every figure came from a computed artifact")

    if args.no_verify_args:
        return 0

    draft = report["text"]
    if args.draft:
        with open(args.draft, "r", encoding="utf-8") as fh:
            draft = fh.read()
    payload = claims_mod.build_args(
        paths.root,
        draft=draft,
        companions=[f["name"] for f in ctx["_facts"]],
        workspace_cfg=ctx["_workspace"],
        verification_commands=ctx["verification_commands"],
    )
    out = os.path.join(paths.reports, "verify-claims.args.json")
    compat.write_json(out, payload)
    print("wrote %s (%d lens(es) + an independent numbers vote + a critic)"
          % (out, len(payload["lenses"])))
    print("  verify it in a Claude Code session:")
    print("    Workflow({ scriptPath: \"%s\", args: <the JSON in %s> })"
          % (os.path.join(_skill_root(), VERIFY_WORKFLOW), out))
    print("  then fix every WRONG claim before the report goes anywhere.")
    return 0


def _unreadable(coverage: dict) -> list:
    """Every corpus document `cf report` could not parse, per source and without one."""
    names = set(coverage.get("unreadable_documents") or ())
    for src in coverage.get("sources") or []:
        names.update(src.get("unreadable_documents") or ())
    return sorted(names)


def _caveats(coverage: dict) -> list:
    """The rest of what section 0 does not count, one line each.

    The unreadable documents have had their own block above since they were
    first published; these four had none at all -- an ungated or gate-FAILED
    run, an examined item outside the audited chapters and a document carrying
    no `source_id` -- so `cf tally` named them and `cf report` said nothing
    about the same corpus.
    """
    ungated, failed, outside, outside_found, unstamped = [], [], 0, 0, 0
    for src in coverage.get("sources") or []:
        ungated.extend(src.get("ungated_runs") or ())
        failed.extend(src.get("gate_failed_runs") or ())
        outside += len(src.get("examined_outside_scope") or ())
        outside_found += len(src.get("with_findings_outside_scope") or ())
        unstamped += int(src.get("unstamped_documents") or 0)
    lines = []
    if ungated:
        lines.append("ungated run(s) %s: no quote in them was checked against the source text"
                     % ", ".join(sorted(set(ungated))))
    if failed:
        lines.append("gate FAILED for run(s) %s: their quotes were checked and rejected, and "
                     "their rows are counted" % ", ".join(sorted(set(failed))))
    if outside:
        lines.append("%d examined item(s) lie outside the audited chapter(s) (%d with findings): "
                     "`cf tally` and §3 count them and no coverage figure does"
                     % (outside, outside_found))
    if unstamped:
        lines.append("%d corpus document(s) carry no `source_id` and were attributed from their "
                     "run's receipt" % unstamped)
    if lines:
        lines.append("every one of these is in §0 of the handoff and in reports/coverage.json")
    return lines


def _skill_root() -> str:
    """The skill checkout, from the ONE resolver (``cflib.paths.skill_root``).

    Every printed ``scriptPath``/``script:`` line must be absolute: ``cf``
    defaults its workspace to the current directory and a workspace has no
    ``workflows/`` directory, so a relative path resolves against the wrong
    root. Three sibling commands print one; they resolve it the same way.
    """
    from cflib.paths import skill_root

    return str(skill_root())


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers()
    register(sub)
    raise SystemExit(run(parser.parse_args()))
