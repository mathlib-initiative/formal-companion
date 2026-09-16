"""``cf route`` -- send each finding to the repository that owns it.

    python3 scripts/cf.py route
    python3 scripts/cf.py route --companion adam --emit-upstream ~/Desktop/outbox

Writes ``ledger/routed/findings.<name>.jsonl`` -- a ``_meta`` row carrying the
status, the routing rule, the book edition and the counts, then one row per
finding that NAMES a declaration of that repository.  That prefix is the whole
rule; the recovered routing cell also admitted rows where the repo merely
appeared in ``repos_involved``, which is what produced the "31 findings" scare.

It also writes ``ledger/routed/message.<name>.md``: a DRAFT for the repo owner.
Nothing here sends anything, and ``--emit-upstream`` REFUSES to export while a
file's status is UNCHALLENGED -- a first-pass finding is wrong about 40% of the
time, and 32 unchallenged findings were once held back for exactly that reason.
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):  # direct execution
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse

from route import route as route_mod

NAME = "route"
HELP = "route findings to the repo that owns them; refuse to export unchallenged rows"


def register(subparsers):
    """cf.py discovery hook."""
    p = subparsers.add_parser(
        NAME, help=HELP, description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--campaign", help="campaign directory (default: $CF_CAMPAIGN or cwd upward)")
    p.add_argument("--companion", action="append", default=[],
                   help="companion to route for (repeatable; default: all)")
    p.add_argument("--corpus", action="append", default=[],
                   help="corpus run directory or findings file (default: every corpus/<run>/)")
    p.add_argument("--book-sha", default="", help="override the campaign's pinned edition")
    p.add_argument("--anchor-check", default="",
                   help="pairs/anchor-check.json (default: the campaign's)")
    p.add_argument("--emit-upstream", default="",
                   help="directory to write the upstream export into (CHALLENGED files only)")
    p.add_argument("--redact-quotes", action="store_true",
                   help="replace book_quote with (length, hash) in the upstream export")
    p.add_argument("--no-messages", action="store_true", help="skip the owner-message drafts")
    p.set_defaults(func=run)
    return p


def run(args) -> int:
    reports = route_mod.route_campaign(
        args.campaign,
        companions=args.companion,
        corpus=args.corpus,
        book_sha=args.book_sha,
        anchor_check_path=args.anchor_check,
        write_messages=not args.no_messages,
    )
    if not reports:
        print("nothing routed: no companions found", file=sys.stderr)
        return 2
    rc = 0
    for rep in reports:
        meta = rep["routed"]["meta"]
        counts = meta["counts"]
        print("%s: %d row(s) / %d item(s) — %s%s"
              % (rep["companion"], counts["rows"], counts["items"], meta["status"],
                 (", %d withdrawn" % counts["withdrawn"]) if counts["withdrawn"] else ""))
        print("  %s" % rep["file"])
        if counts.get("unchallenged"):
            print("  %d row(s) unchallenged; challenge them before anything leaves the campaign dir:"
                  % counts["unchallenged"])
            print("    %s" % meta.get("challenge_command", ""))
        if args.emit_upstream:
            try:
                out = route_mod.emit_upstream(
                    rep["routed"], args.emit_upstream,
                    companion=rep["companion"],
                    campaign=os.path.abspath(args.campaign or "."),
                    redact_quotes=args.redact_quotes,
                )
                print("  upstream: %s" % out)
            except route_mod.UpstreamRefused as exc:
                print("  %s" % exc, file=sys.stderr)
                rc = 2
    return rc


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers()
    register(sub)
    raise SystemExit(run(parser.parse_args()))
