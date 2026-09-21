"""``cf route`` -- send each finding to the repository that owns it.

    cf route
    cf route --companion adam --emit-upstream <outbox dir>
    cf route --companion adam --github --repo owner/name

Writes ``ledger/routed/findings.<name>.jsonl`` -- a ``_meta`` row carrying the
status, the routing rule, the book edition and the counts, then one row per
finding that NAMES a declaration of that repository.  That prefix is the whole
rule; the recovered routing cell also admitted rows where the repo merely
appeared in ``repos_involved``, which is what produced the "31 findings" scare.

It also writes ``ledger/routed/message.<name>.md``: a DRAFT for the repo owner,
one section per finding carrying the printed page, the book sentence, the
declaration at ``file:line`` and the adjudication outcome.  ``--github`` writes
one draft issue body per accepted finding plus an ``index.json`` holding the
exact ``gh issue create --body-file`` command for each, into
``ledger/routed/github/<name>/`` -- one directory PER COMPANION, and ``--out``
names the parent of those directories, never a directory two companions share.

Nothing here sends anything: no network call, no ``gh``, no API.  ``--emit-upstream``
and ``--github`` both REFUSE while a file's status is UNCHALLENGED -- a first-pass
finding is wrong about 40% of the time, and 32 unchallenged findings were once
held back for exactly that reason.

How much of a quoted sentence an artifact carries is a property of its
DESTINATION, not of the workspace: ``--destination owner`` (the default for the
owner-message draft, which never leaves the workspace directory) carries the
sentence, ``--destination public`` reduces it to the printed page, the hash of
the whole and at most a quarter of its words at each end -- nothing at all below
fourteen words, where the ends would be the sentence.  It is never inferred from
a git remote.

A destination other than ``owner`` also scrubs the ROW'S OWN prose: the quote
policy governs ``book_quote``, and a worker quoting the book inside a
``reasoning`` or a witness is the same sentence by another route, so every field
is checked against this workspace's pinned ``sources/<id>/text.txt`` with the
scrubber ``cf share export`` uses.  A workspace with no prepared text cannot make
that check, and the draft is REFUSED rather than written (`cf sources refresh`).
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):  # direct execution
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse

from cflib.errors import CfError
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
    p.add_argument("--github", action="store_true",
                   help="write one draft issue body per accepted finding plus index.json "
                        "(writes files only -- it never calls gh or the network)")
    p.add_argument("--repo", default="",
                   help="owner/name the printed `gh issue create` should target "
                        "(default: the companion's `owner` if it is a slug, else gh's own "
                        "current-repository default)")
    p.add_argument("--out", default="",
                   help="parent directory for --github; each companion gets its own "
                        "<out>/<companion>/ inside it, never a shared one "
                        "(default: ledger/routed/github/<companion>/)")
    p.add_argument("--destination", default="",
                   choices=sorted(route_mod.DESTINATION_DEFAULTS),
                   help="who the drafts are addressed to; chooses the quote policy "
                        "(owner: the sentence; public: page + hash + up to a quarter of "
                        "the words at each end, and the row's prose scrubbed too). "
                        "Default: `%s` for the owner message, `%s` for --github"
                        % (route_mod.DEFAULT_DESTINATION, route_mod.GITHUB_DESTINATION))
    p.add_argument("--quotes", default="", choices=("",) + tuple(route_mod.QUOTE_POLICIES),
                   help="override the destination's quote policy for the drafts")
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


def _repo_slug(args, companion: str) -> str:
    """``--repo owner/name``, else the companion's own ``owner`` when it is a slug.

    A repo is never derived from a git remote: the remote says where a checkout
    pushes, not whether the tracker it belongs to is public, and this string ends
    up in a command that opens a thread on someone else's repository.
    """
    if args.repo:
        return str(args.repo)
    try:
        from cflib.workspace import Workspace

        owner = str((Workspace.load(args.workspace).companion(companion).owner or ""))
    except Exception:  # noqa: BLE001 - no workspace, no companion, no slug
        return ""
    return owner if owner.count("/") == 1 and " " not in owner else ""


def _emit_issues(args, rep) -> str:
    """``--github``: write the bodies and print the command for each. Calls nothing.

    Two companions NEVER share a destination directory.  ``--out DIR`` used to be
    taken verbatim for every report while the body's file name is the source and
    the item, so in a two-companion workspace -- the project's normal shape --
    both wrote into DIR: ``index.json`` held only the last companion, and the
    body for an item both companions formalise was overwritten.  The printed
    ``gh issue create`` for the first one then pointed at the second one's
    evidence, which files the wrong claim on someone else's public tracker.
    """
    dest = (os.path.join(args.out, route_mod.issue_slug(rep["companion"]))
            if args.out else route_mod.issues_dir(rep["routed_dir"], rep["companion"]))
    index = route_mod.write_github_issues(
        rep["routed"], dest,
        companion=rep["companion"],
        repo=_repo_slug(args, rep["companion"]),
        destination=args.destination or route_mod.GITHUB_DESTINATION,
        quotes=args.quotes,
        policy=rep.get("policy"),
        workspace=rep["workspace"],
        routed_file=rep["file"],
    )
    print("  github: %d issue body/bodies over %d finding(s) in %s (quotes: %s)"
          % (index["count"], index["findings"], index["dir"], index["quotes"]))
    print("  nothing was filed. File them yourself:")
    for command in index["commands"]:
        print("    %s" % command)
    if not index["commands"]:
        print("    (no accepted finding yet: only %s are reported)"
              % ", ".join(index["reportable_verdicts"]))
    return dest


def run(args) -> int:
    _check_publish_policy(args)
    redact = _redact(args)
    try:
        reports = route_mod.route_workspace(
            args.workspace,
            companions=args.companion,
            corpus=args.corpus,
            anchor_check_path=args.anchor_check,
            write_messages=not args.no_messages,
            destination=args.destination or route_mod.DEFAULT_DESTINATION,
            quotes=args.quotes,
        )
    except route_mod.PolicyRefused as exc:
        raise CfError(str(exc), hint="policy.destinations lives in workspace.yaml and "
                                     "`cf route --help` lists the destinations; the pinned "
                                     "text a non-owner destination is checked against comes "
                                     "from `cf sources refresh`")
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
        if counts.get("id_collision_rows"):
            print("  %d row(s) held back: they share a finding_id with another finding, and a "
                  "ledger row can only be addressed to the id. Give each further claim its own "
                  "declaration, item or kind, then re-harvest." % counts["id_collision_rows"])
        if counts.get("unchallenged"):
            print("  %d row(s) unchallenged; challenge them before anything leaves the workspace dir:"
                  % counts["unchallenged"])
            print("    %s" % meta.get("challenge_command", ""))
        if args.emit_upstream:
            try:
                out = route_mod.emit_upstream(
                    rep["routed"], args.emit_upstream,
                    companion=rep["companion"],
                    workspace=rep["workspace"],
                    redact_quotes=redact,
                )
                print("  upstream: %s%s" % (out, "" if redact else "  (quotes NOT redacted)"))
            except route_mod.UpstreamRefused as exc:
                print("  %s" % exc, file=sys.stderr)
                rc = 2
        if args.github:
            try:
                _emit_issues(args, rep)
            except route_mod.UpstreamRefused as exc:
                print("  %s" % exc, file=sys.stderr)
                rc = 2
            except route_mod.PolicyRefused as exc:
                raise CfError(str(exc),
                              hint="`cf route --help` lists the quote policies; the pinned "
                                   "text a public body is checked against comes from "
                                   "`cf sources refresh`")
    return rc


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers()
    register(sub)
    raise SystemExit(run(parser.parse_args()))
