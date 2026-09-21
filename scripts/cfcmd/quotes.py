"""cf quotes hydrate -- put a redacted quote's text back, from your own source.

    cf quotes hydrate --bundle ../tao-share.tar.gz
    cf quotes hydrate --ledger --companion adam
    cf quotes hydrate --bundle ../tao-share --out sources/hydrated.adam.jsonl

A shared finding carries no book text: ``cf share export`` replaces every quote
with ``{page, length, sha256 of the normalised quote, first/last six words}``.
This command reverses that for somebody who holds the same edition, out of THEIR
OWN ``sources/<id>/text.txt``: it slides a window of the recorded length over the
recorded page (plus or minus the quote gate's ``PAGE_BIND_WINDOW``) and keeps the
passage whose sha256 matches.  The hash is the verification -- a recovered
sentence is the finding's sentence or the command says it could not find one.

Three outcomes, all counted: **hydrated**, **not-found** (this is not the edition
the finding was written against, or the quote was not copied out of the pinned
text) and **ambiguous** (two differently-spelled passages hash to the same quote).

``--out`` refuses any path ``cf share export`` would put in a bundle, so book
text recovered here cannot end up in the one artifact whose purpose is to travel.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from cflib import log
from cflib.errors import CfError
from cflib.jsonio import read_jsonl, write_jsonl
from cflib.log import format_table
from cflib.paths import skill_root
from cflib.workspace import from_args
from share import bundle as bundle_mod
from share import hydrate as hydrate_mod

NAME = "quotes"
HELP = "recover a redacted quote's text from this machine's own copy of the source"


def register(subparsers) -> None:
    p = subparsers.add_parser(
        NAME, help=HELP, description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="quotes_command", metavar="<subcommand>")

    hy = sub.add_parser("hydrate", help="recover redacted quotes (hash-verified)",
                        description=__doc__,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    hy.add_argument("--bundle", default="",
                    help="a bundle `cf share export` wrote (directory or .tar.gz): hydrate its "
                         "routed findings")
    hy.add_argument("--ledger", action="store_true",
                    help="hydrate the redacted quotes in THIS workspace's ledger/ instead "
                         "(the default when --bundle is not given)")
    hy.add_argument("--companion", action="append", default=[],
                    help="only this companion's rows (repeatable)")
    hy.add_argument("--out", default="",
                    help="write the hydrated rows as JSONL here (default: print them). "
                         "Refused for any path `cf share export` would include in a bundle")
    hy.add_argument("--no-text", action="store_true",
                    help="print the table and the counts only, not the recovered passages")


def run(args) -> int:
    command = getattr(args, "quotes_command", None)
    if not command:
        raise CfError("cf quotes needs a subcommand",
                      hint="`cf quotes hydrate --bundle <bundle>` recovers the text of a shared "
                           "campaign's quotes from this machine's own source")
    workspace = from_args(args)
    return _hydrate(workspace, args)


def _refuse_unsafe_out(out: Path, workspace) -> None:
    """``--out`` may not be a file a bundle carries, nor live in the skill repo."""
    root = Path(workspace.root).resolve()
    relative = ""
    if out == root or root in out.parents:
        relative = out.relative_to(root).as_posix()
    if relative and bundle_mod.would_export(relative):
        raise CfError(
            f"refusing to write hydrated book text to {out}: `cf share export` puts that path "
            "in a bundle",
            hint="write it somewhere the export does not read, e.g. --out "
                 f"{root / 'sources' / out.name} (sources/ is gitignored), and keep the "
                 "recovered text on the machine that holds the source")
    if out == skill_root() or skill_root() in out.parents:
        raise CfError(
            f"refusing to write hydrated book text inside the skill repo ({skill_root()})",
            hint="the skill repo holds code, templates and docs only (CLAUDE.md S1)")


def _rows_from_bundle(path: str, companions: list[str]) -> tuple[list[dict], str]:
    """Every routed/ledger row of a bundle, with the bundle's own description."""
    with tempfile.TemporaryDirectory(prefix="cf-quotes-") as tmp:
        bundle = bundle_mod.open_bundle(path, Path(tmp) / "bundle")
        manifest = bundle_mod.read_manifest(bundle)
        problems = bundle_mod.verify_bundle(bundle, manifest)
        if problems:
            raise CfError(
                f"{path} does not match its {bundle_mod.MANIFEST_NAME}: "
                + "; ".join(problems[:4]),
                hint="the bundle was altered or truncated in transit; ask for it again")
        rows: list[dict] = []
        for file in bundle_mod.bundle_routed_files(bundle) + bundle_mod.bundle_ledger_files(bundle):
            name = bundle_mod.companion_of(file)
            if companions and name not in companions:
                continue
            rows.extend(r for r in read_jsonl(file)
                        if not (isinstance(r, dict) and "_meta" in r))
        return rows, (f"bundle {manifest.get('bundle_id', '')[:12]} from workspace "
                      f"{manifest.get('workspace')!r}")


def _rows_from_ledger(workspace, companions: list[str]) -> tuple[list[dict], str]:
    paths = workspace.paths
    names = companions or list(workspace.companion_names())
    rows: list[dict] = []
    for name in names:
        for path in (paths.routed_jsonl(name), paths.accepted_jsonl(name),
                     paths.refuted_jsonl(name)):
            if path.exists():
                rows.extend(r for r in read_jsonl(path)
                            if not (isinstance(r, dict) and "_meta" in r))
    return rows, f"this workspace's ledger ({', '.join(names) or 'no companions'})"


def _hydrate(workspace, args) -> int:
    if args.bundle and args.ledger:
        raise CfError("--bundle and --ledger name two different sets of rows",
                      hint="run the command twice, once with each")
    rows, where = (_rows_from_bundle(args.bundle, args.companion) if args.bundle
                   else _rows_from_ledger(workspace, args.companion))
    redacted = hydrate_mod.redacted_rows(rows)
    log.info(f"source    : {where}")
    if not redacted:
        log.info(f"{len(rows)} row(s) read, none with a redacted quote: nothing to hydrate")
        if not args.bundle:
            log.info("this workspace's own rows carry their quotes verbatim; a bundle somebody "
                     "sent you is the redacted case (`cf quotes hydrate --bundle <bundle>`)")
        return 0
    source_ids = sorted({str(r.get("source_id") or "") for r in redacted})
    texts = hydrate_mod.source_texts(workspace.paths, source_ids)
    reports, counts = hydrate_mod.hydrate_rows(redacted, texts)
    log.info(format_table(
        ["item", "kind", "page", "status"],
        [[r["item"], r["kind"], r["page"], r["status"]] for r in reports]))
    log.info(f"hydrated {counts[hydrate_mod.HYDRATED]}, "
             f"not-found {counts[hydrate_mod.NOT_FOUND]}, "
             f"ambiguous {counts[hydrate_mod.AMBIGUOUS]}"
             + (f", no source text {counts['no-source']}" if counts["no-source"] else "")
             + f"  (of {counts['redacted']} redacted quote(s))")
    for report in reports:
        if report["status"] != hydrate_mod.HYDRATED:
            log.warn(f"{report['item']}: {report['status']} -- {report['why']}")
    if not args.no_text:
        for report in reports:
            if report["status"] == hydrate_mod.HYDRATED:
                log.info("")
                log.info(f"{report['item']} ({report['kind']}), page {report['page']}:")
                log.info(f"  {report['text']}")
    if args.out:
        out = Path(args.out).expanduser().resolve()
        _refuse_unsafe_out(out, workspace)
        write_jsonl(out, reports, meta={
            "read_from": where,
            "counts": counts,
            "note": ("hydrated book text: recovered from this machine's own sources/<id>/"
                     "text.txt and verified by sha256. It is book text, so it stays here -- "
                     "`cf share export` never reads this file"),
        })
        log.ok(f"wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    register(parser.add_subparsers())
    raise SystemExit(run(parser.parse_args()))
