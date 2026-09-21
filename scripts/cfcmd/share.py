"""cf share -- hand a campaign to somebody else without handing over the book.

    cf share export --out ../tao-share.tar.gz
    cf share export --out ../tao-share --companion adam --full-quotes
    cf share import ../tao-share.tar.gz

The workspace directory is a git repo with NO remote, because ``corpus/`` quotes
1-3 sentences of a copyrighted source per finding and ``companions/*/
statements.jsonl`` holds a private repo's Lean statements.  That rule is right,
and it was being paid for with everything collaboration needs: two people could
not work one campaign, findings could not be reviewed by anyone who had not run
the pipeline, and a lost laptop was a lost calibration baseline.

``export`` is the answer.  It writes the ledger rows, the routed findings, the
tallies, the run receipts and source METADATA into a bundle with a
``MANIFEST.json`` naming every file and its sha256 -- and by default every book
quote in it is REDACTED to ``{page, length, sha256 of the normalised quote,
first/last six words}``.  ``book_quote`` is not the only field that quotes the
book, so a verbatim run of 40 normalised characters or more inside a ``reason``,
``evidence``, ``finding_summary``, ``original_concept``, ``reasoning``,
``concept`` or ``suggested_fix`` is redacted in place the same way and recorded in
the row's ``redacted_spans``.  Nothing in a bundle reconstructs the source: there
is no text, no page image, no PDF, no findings document, and no statements table
unless ``--include-statements`` asks for one; a ``_meta`` row travels through an
allowlist, so the absolute paths and workflow ids ``cf adjudicate`` and ``cf
route`` write into one stay behind; and a companion whose routed ``_meta.status``
is not ``CHALLENGED`` is refused outright (SKILL.md hard rule 5) unless
``--skip-unchallenged`` leaves those rows behind.  A reader who holds the same
edition gets the sentences back with ``cf quotes hydrate --bundle <bundle>``.

``import`` merges a bundle's ledger rows into this workspace: the same finding
with the same verdict is a no-op, two different verdicts are BOTH kept and marked
``conflict``, and a source whose sha256 differs from this workspace's is refused
outright, naming both shas.  Importing the same bundle twice changes nothing.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from cflib import log
from cflib.errors import CfError
from cflib.log import format_table
from cflib.workspace import from_args
from share import bundle as bundle_mod
from share import merge as merge_mod

NAME = "share"
HELP = "export a campaign as a redacted bundle, or merge one somebody sent you"


def register(subparsers) -> None:
    p = subparsers.add_parser(
        NAME, help=HELP, description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="share_command", metavar="<subcommand>")

    exp = sub.add_parser("export", help="write a shareable bundle (quotes redacted)",
                         description=__doc__,
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    exp.add_argument("--out", required=True,
                     help="bundle to write: a directory, or a path ending .tar.gz/.tgz. "
                          "Refused inside the workspace repo, a companion repo or the skill "
                          "repo -- a bundle is meant to travel")
    exp.add_argument("--companion", action="append", default=[],
                     help="companion to export (repeatable; default: every one)")
    exp.add_argument("--redact-quotes", dest="redact", action="store_true", default=None,
                     help="replace every book quote -- and every verbatim run of the source "
                          "inside a reason or a reasoning -- with (page, length, hash, "
                          "first/last six words) (default: workspace.yaml "
                          "policy.redact_quotes_on_export, which is true)")
    exp.add_argument("--full-quotes", dest="redact", action="store_false",
                     help="carry the verbatim book sentences instead (overrides the workspace "
                          "policy); only for a recipient who may hold them, and recorded as "
                          "quotes: VERBATIM in the manifest")
    exp.add_argument("--include-statements", action="store_true",
                     help="also copy companions/<name>/statements.jsonl (a private repo's Lean "
                          "statements; off by default)")
    exp.add_argument("--skip-unchallenged", action="store_true",
                     help="leave a companion's routed findings OUT of the bundle when their "
                          "_meta.status is not CHALLENGED, instead of refusing the export. A "
                          "first-pass finding is wrong about 40%% of the time, so they are "
                          "never carried either way; the adjudicated ledger rows still travel")
    exp.add_argument("--force", action="store_true", help="overwrite an existing --out")

    imp = sub.add_parser("import", help="merge a bundle's ledger rows into this workspace",
                         description=__doc__,
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    imp.add_argument("bundle", help="the bundle directory or .tar.gz to read")
    imp.add_argument("--dry-run", action="store_true",
                     help="say what would be merged and write nothing")


def run(args) -> int:
    command = getattr(args, "share_command", None)
    if not command:
        raise CfError("cf share needs a subcommand",
                      hint="`cf share export --out <bundle>` writes one; "
                           "`cf share import <bundle>` merges one")
    workspace = from_args(args)
    return {"export": _export, "import": _import}[command](workspace, args)


def _redact(workspace, args) -> bool:
    """``--redact-quotes`` / ``--full-quotes``, else the workspace's own policy.

    Read exactly as ``cfcmd.route._redact`` reads it, and for the same reason that
    helper exists: ``policy.redact_quotes_on_export`` was written into every
    workspace by `cf init` and read by nothing, so an operator who read their
    workspace.yaml and believed quotes were redacted got verbatim book sentences
    in the file they handed to somebody else.  A flag overrides the policy; with
    no flag the policy is what applies.
    """
    if getattr(args, "redact", None) is not None:
        return bool(args.redact)
    try:
        return bool(dict(workspace.policy or {}).get("redact_quotes_on_export", True))
    except Exception:  # noqa: BLE001 - a workspace we cannot read gets the safe default
        return True


def _export(workspace, args) -> int:
    redact = _redact(workspace, args)
    manifest = bundle_mod.export_bundle(
        workspace, args.out, companions=args.companion, redact=redact,
        include_statements=args.include_statements, force=args.force,
        skip_unchallenged=args.skip_unchallenged)
    counts = manifest["counts"]
    log.ok(f"wrote {manifest['out']}")
    log.info(f"bundle    : {manifest['bundle_id'][:12]}  ({len(manifest['files'])} files)")
    log.info(f"companions: {', '.join(manifest['companions']) or 'none'}")
    log.info(format_table(
        ["ledger rows", "routed rows", "tallies", "receipts", "statements"],
        [[counts["ledger_rows"], counts["routed_rows"], counts["tallies"],
          counts["receipts"], counts["statements"]]]))
    for name, status in sorted(manifest.get("routed_status", {}).items()):
        left_out = name in (manifest.get("skipped_unchallenged") or [])
        log.info(f"routed    : {name} {status}"
                 + ("   -- LEFT OUT of the bundle" if left_out else ""))
    if redact:
        log.info(f"quotes    : {counts['quotes_redacted']} redacted, "
                 f"{manifest['counts']['quotes_with_page']} of them with a page")
        log.info(f"prose     : {counts['prose_spans']} verbatim run(s) of "
                 f"{manifest['prose_span_min']}+ characters redacted out of the rows' reason/"
                 "reasoning/concept fields")
        log.info("            a holder of the same edition reads them with "
                 "`cf quotes hydrate --bundle <bundle>`")
    else:
        log.warn(f"quotes    : VERBATIM -- {counts['quotes_verbatim']} book sentence(s) are in "
                 "this bundle, and the rows' prose is as written. Only hand it to somebody "
                 "who may hold them")
    if args.include_statements and counts["statements"]:
        log.warn(f"{counts['statements']} statements.jsonl file(s) are in this bundle: they are "
                 "a private repo's Lean statements")
    log.info("")
    log.info("next:")
    log.info(f"  cf share import {manifest['out']}        (in the other workspace)")
    return 0


def _import(workspace, args) -> int:
    with tempfile.TemporaryDirectory(prefix="cf-share-import-") as tmp:
        bundle = bundle_mod.open_bundle(args.bundle, Path(tmp) / "bundle")
        manifest = bundle_mod.read_manifest(bundle)
        problems = bundle_mod.verify_bundle(bundle, manifest)
        if problems:
            raise CfError(
                f"{args.bundle} does not match its {bundle_mod.MANIFEST_NAME}: "
                + "; ".join(problems[:4])
                + (f" (+{len(problems) - 4} more)" if len(problems) > 4 else ""),
                hint="the bundle was altered or truncated in transit; ask for it again "
                     "rather than importing part of it")
        log.ok(f"{bundle_mod.MANIFEST_NAME} verified: {len(manifest['files'])} file(s) from "
               f"workspace {manifest.get('workspace')!r}, quotes {manifest.get('quotes')}")
        report = merge_mod.merge_bundle(workspace, bundle, manifest, dry_run=args.dry_run)
    if report["files"]:
        log.info(format_table(
            ["file", "companion", "added", "unchanged", "conflicts"],
            [[Path(f["file"]).name, f["companion"], f["added"], f["unchanged"], f["conflicts"]]
             for f in report["files"]]))
    verb = "would merge" if args.dry_run else "merged"
    log.info(f"{verb} {report['added']} row(s); {report['unchanged']} already here")
    for conflict in report["conflicts"]:
        log.warn(f"conflict {conflict['item']}: {' vs '.join(conflict['verdicts'])} "
                 f"(joined by {conflict['joined_by']}) -- both rows kept in "
                 f"{conflict['file']}, marked conflict")
    if report["conflicts"]:
        log.info("nothing was overwritten: adjudicate the conflicts and keep the verdict you "
                 "can defend")
    if args.dry_run:
        log.info("--dry-run: nothing was written")
    # The routed findings, the tallies and the receipts are in the bundle to be
    # READ, not merged: this workspace recomputes its own with `cf route`/`cf tally`.
    log.info("the bundle's routed findings are not merged; read them with "
             "`cf quotes hydrate --bundle <bundle>`")
    return 0


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    register(parser.add_subparsers())
    raise SystemExit(run(parser.parse_args()))
