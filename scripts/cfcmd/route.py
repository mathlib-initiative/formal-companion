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
    p.add_argument("--workspace", help="workspace directory (default: $CF_WORKSPACE or cwd upward)")
    p.add_argument("--companion", action="append", default=[],
                   help="companion to route for (repeatable; default: all)")
    p.add_argument("--corpus", action="append", default=[],
                   help="corpus run directory or findings file (default: every corpus/<run>/)")
    p.add_argument("--anchor-check", default="",
                   help="one pairs/<source_id>/anchor-check.json to use for EVERY source "
                        "(default: each source's own, which is the only one that examined "
                        "its items)")
    p.add_argument("--emit-upstream", default="",
                   help="directory to write the upstream export into (CHALLENGED files only)")
    p.add_argument("--redact-quotes", dest="redact_quotes", action="store_true", default=None,
                   help="replace book_quote with (length, hash) in the upstream export "
                        "(default: workspace.yaml policy.redact_quotes_on_export, which is true)")
    p.add_argument("--no-redact-quotes", dest="redact_quotes", action="store_false",
                   help="export the verbatim book sentence (overrides the workspace policy)")
    p.add_argument("--no-messages", action="store_true", help="skip the owner-message drafts")
    p.set_defaults(func=run)
    return p


def _redact(args) -> bool:
    """``--redact-quotes`` / ``--no-redact-quotes``, else the workspace's policy.

    ``policy.redact_quotes_on_export: true`` is written into every workspace by
    ``cf init`` and was read by nothing: the flag defaulted to False, so an
    operator who read their workspace.yaml and believed quotes were redacted got
    verbatim book sentences in the file they handed to a repo owner.
    """
    if args.redact_quotes is not None:
        return bool(args.redact_quotes)
    try:
        from cflib.workspace import Workspace

        policy = Workspace.load(args.workspace).policy
    except Exception:  # noqa: BLE001 - a workspace we cannot read gets the safe default
        return True
    return bool(policy.get("redact_quotes_on_export", True))


def _check_publish_policy(args) -> None:
    """``policy.publish_unchallenged`` is read, and only ``false`` is accepted.

    SKILL.md hard rule 5 is not negotiable: nothing leaves the workspace
    directory while ``_meta.status`` is UNCHALLENGED.  The key exists so the
    workspace states the rule, not so it can be turned off.
    """
    try:
        from cflib.workspace import Workspace

        policy = Workspace.load(args.workspace).policy
    except Exception:  # noqa: BLE001
        return
    if policy.get("publish_unchallenged"):
        raise SystemExit(
            "error: workspace.yaml sets policy.publish_unchallenged: true, which this skill "
            "does not implement.\nhint:  a first-pass finding is wrong about 40% of the time; "
            "run `cf adjudicate` and set it back to false."
        )


def run(args) -> int:
    _check_publish_policy(args)
    redact = _redact(args)
    reports = route_mod.route_workspace(
        args.workspace,
        companions=args.companion,
        corpus=args.corpus,
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
            print("  %d row(s) unchallenged; challenge them before anything leaves the workspace dir:"
                  % counts["unchallenged"])
            print("    %s" % meta.get("challenge_command", ""))
        if args.emit_upstream:
            try:
                out = route_mod.emit_upstream(
                    rep["routed"], args.emit_upstream,
                    companion=rep["companion"],
                    workspace=os.path.abspath(args.workspace or "."),
                    redact_quotes=redact,
                )
                print("  upstream: %s%s" % (out, "" if redact else "  (quotes NOT redacted)"))
            except route_mod.UpstreamRefused as exc:
                print("  %s" % exc, file=sys.stderr)
                rc = 2
    return rc


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers()
    register(sub)
    raise SystemExit(run(parser.parse_args()))
