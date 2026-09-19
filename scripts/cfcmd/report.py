"""``cf report`` -- render the handoff and the claims-verification args.

    python3 scripts/cf.py report
    python3 scripts/cf.py report --tally reports/tally.hunt.json --gh-json prs.json

Two outputs, both mechanical:

1. ``reports/handoff.<date>.md`` from ``templates/handoff.md``, filled ONLY from
   computed artifacts -- tally files, ledger rows, routed ``_meta``, state.json,
   run.yaml and saved ``gh`` output.  A figure with no artifact behind it is
   written as ``TODO(...)`` with the command that would produce it; this module
   never calls the network and never counts anything by reading prose.
2. ``reports/verify-claims.args.json`` -- the args for
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
