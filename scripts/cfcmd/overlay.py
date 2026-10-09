"""cf overlay -- generate, verify and feed a companion's PDF overlay.

New code (CLAUDE.md S5 is the contract for adding a command), implementing
section 6 of `docs/overlay.md`:

    cf overlay init <companion> --source-id notes-pdf --title "Lecture notes"
    cf overlay sync <companion>
    cf overlay sync <companion> --check
    cf overlay check <companion>
    cf overlay export <companion> --convention attribution-table --commit <sha>
    cf overlay geometry --pdf notes.pdf --first 7 --last 9 --source-id notes-pdf \\
        --verifier-id agent:reviewer-1 --method agent_visual --out fragment.json
    cf overlay destinations --site <built site> --doc <document> --out fragment.json

It is the one command that writes into a companion repository, and it writes
only under `<companion>/pdf-overlay/` and, for `export`, the shard directory
`<companion>/annotations/` (contract rule 5, CLAUDE.md S4, CONTRIBUTING.md
invariant 3, SKILL.md rule 10).  `geometry` and `destinations`
write a document FRAGMENT wherever the user says and touch no companion at all.

No workspace is needed: a companion's overlay is generated from this checkout
and the companion's own data, so `cf overlay` runs in a clone with nothing
set up -- the same reason `cf sources validate-registry` takes no workspace.

`geometry` and `destinations` are the producers' own parsers mounted here with
``parents=``, so the flags, their defaults and their help have one definition,
in `scripts/overlay/geometry.py` and `scripts/overlay/destinations.py`; this
module rebuilds the command line from the parsed namespace and calls the
producer's own ``main``, so the behaviour has one definition too.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from cflib import log
from cflib.errors import CfError

from overlay import destinations as overlay_destinations
from overlay import export as overlay_export
from overlay import geometry as overlay_geometry
from overlay import sync as overlay_sync

#: Built once and reused: `register` mounts them as parents and `run` reads
#: their actions back to rebuild the producer's argv.
GEOMETRY_PARSER = overlay_geometry.build_parser()
DESTINATIONS_PARSER = overlay_destinations.build_parser()


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "overlay", help="generate and verify a companion's pdf-overlay/ directory")
    sub = p.add_subparsers(dest="overlay_command", metavar="<subcommand>")

    init = sub.add_parser("init", help="write pdf-overlay/overlay.config.json from the example")
    init.add_argument("companion", type=Path, help="the companion's checkout")
    init.add_argument("--source-id", required=True,
                      help="the id of the PDF source the overlay shows (the exporter's kind: pdf source)")
    init.add_argument("--title", required=True, help="the page title and heading")
    init.add_argument("--file-name", help="the cached PDF's file name "
                                          "(default: <source-id>.pdf)")
    init.add_argument("--force", action="store_true",
                      help="overwrite a config that is already there")

    syn = sub.add_parser("sync", help="copy the runtime into pdf-overlay/ and write "
                                      "GENERATED.json with the shard list")
    syn.add_argument("companion", type=Path, help="the companion's checkout")
    syn.add_argument("--check", action="store_true", dest="check_only",
                     help="write nothing; exit 1 naming every missing, changed or "
                          "extra file")

    chk = sub.add_parser("check", help="verify the generated directory, the config, the "
                                       "shards and the curation inputs they were exported from")
    chk.add_argument("companion", type=Path, help="the companion's checkout")

    exp = sub.add_parser("export", help="run a cf export convention and write its Literature "
                                        "Links shards into annotations/")
    exp.add_argument("companion", type=Path, help="the companion's checkout")
    exp.add_argument("--convention", required=True, choices=list(overlay_export.CONVENTIONS),
                     help="which cf export subcommand writes this companion's document")
    exp.add_argument("--commit", required=True, help="the commit the checkout must be at")
    exp.add_argument("--source-id",
                     help="the kind: pdf source to export (default: the config's source_id, else "
                          "the document's only pdf source)")
    exp.add_argument("exporter_args", nargs="*", metavar="ARG",
                     help="everything after a bare `--` is passed to `cf export` "
                          "unchanged (--data-dir, --ledger, --pdf, ...)")

    sub.add_parser("geometry", parents=[GEOMETRY_PARSER], add_help=False,
                   help="item headings and regions from a PDF text layer, as "
                        "annotation records")
    sub.add_parser("destinations", parents=[DESTINATIONS_PARSER], add_help=False,
                   help="declaration destinations from a built Verso literate site")


def producer_argv(parser: argparse.ArgumentParser, args) -> list[str]:
    """The command line *args* carries, for a parser mounted with ``parents=``.

    Only what differs from the parser's own default is emitted, so the
    producer applies its defaults itself and the two can never drift.
    """
    argv: list[str] = []
    for action in parser._actions:                           # noqa: SLF001 - no public API
        if not action.option_strings or isinstance(action, argparse._HelpAction):  # noqa: SLF001
            continue
        value = getattr(args, action.dest, None)
        if value is None or value == action.default:
            continue
        flag = action.option_strings[-1]
        if action.nargs == 0:                                # store_true and friends
            argv.append(flag)
        else:
            argv += [flag, str(value)]
    return argv


def run(args) -> int:
    command = getattr(args, "overlay_command", None)
    if not command:
        raise CfError("say which overlay subcommand to run",
                      hint="cf overlay init | sync | check | export | geometry | "
                           "destinations (`cf overlay --help`)")
    return _DISPATCH[command](args)


def _init(args) -> int:
    path = overlay_sync.init(args.companion, source_id=args.source_id, title=args.title,
                             file_name=args.file_name, force=args.force)
    log.ok(f"wrote {path}")
    log.info("next: edit it for this companion, then `cf overlay sync "
             f"{args.companion}`")
    return 0


def _sync(args) -> int:
    if args.check_only:
        problems = overlay_sync.sync_problems(args.companion)
        for problem in problems:
            log.error(problem)
        if problems:
            log.error(f"{len(problems)} problem(s); `cf overlay sync "
                      f"{args.companion}` rewrites the directory")
            return 1
        log.ok(f"{args.companion}/{overlay_sync.OVERLAY_DIRNAME} matches the templates")
        return 0
    result = overlay_sync.sync(args.companion)
    log.ok(f"{result.overlay}: {len(result.written)} written, "
           f"{len(result.unchanged)} unchanged, {len(result.deleted)} deleted")
    for relative in result.deleted:
        log.detail(f"deleted {relative}")
    return 0


def _check(args) -> int:
    problems = overlay_sync.check(args.companion)
    for problem in problems:
        log.error(problem)
    if problems:
        log.error(f"{len(problems)} problem(s) in "
                  f"{args.companion}/{overlay_sync.OVERLAY_DIRNAME}")
        return 1
    log.ok(f"{args.companion}/{overlay_sync.OVERLAY_DIRNAME} is generated and configured, and "
           f"its shards are valid and current")
    return 0


def _export(args) -> int:
    result = overlay_export.export(args.companion, convention=args.convention, commit=args.commit,
                                   source_id=args.source_id, extra=args.exporter_args)
    log.ok(f"wrote {len(result.shards)} shards, {sum(result.shards.values())} records, "
           f"into {result.directory}")
    for name in result.deleted:
        log.detail(f"deleted {name}")
    log.info(f"next: `cf overlay sync {args.companion}`, then `cf overlay check {args.companion}`")
    return 0


def _geometry(args) -> int:
    return overlay_geometry.main(producer_argv(GEOMETRY_PARSER, args))


def _destinations(args) -> int:
    return overlay_destinations.main(producer_argv(DESTINATIONS_PARSER, args))


_DISPATCH = {
    "init": _init,
    "sync": _sync,
    "check": _check,
    "export": _export,
    "geometry": _geometry,
    "destinations": _destinations,
}
