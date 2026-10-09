"""cf overlay -- generate, verify and feed a companion's PDF overlay.

New code (CLAUDE.md S5 is the contract for adding a command), implementing
section 6 of `docs/overlay.md`:

    cf overlay init <companion> --source-id notes-pdf --title "Lecture notes"
    cf overlay sync <companion>
    cf overlay sync <companion> --check
    cf overlay check <companion>
    cf overlay export <companion> --convention attribution-table --commit <sha>

It is the one command that writes into a companion repository, and it writes
only under `<companion>/pdf-overlay/` and, for `export`, the annotation directory
`<companion>/annotations/` (contract rule 5, CLAUDE.md S4, CONTRIBUTING.md
invariant 3, SKILL.md rule 10).

No workspace is needed: a companion's overlay is generated from this checkout
and the companion's own data, so `cf overlay` runs in a clone with nothing
set up -- the same reason `cf sources validate-registry` takes no workspace.
"""
from __future__ import annotations

from pathlib import Path

from cflib import log
from cflib.errors import CfError

from overlay import export as overlay_export
from overlay import sync as overlay_sync


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
                                      "the annotation bundle and GENERATED.json")
    syn.add_argument("companion", type=Path, help="the companion's checkout")
    syn.add_argument("--check", action="store_true", dest="check_only",
                     help="write nothing; exit 1 naming every missing, changed or "
                          "extra file")

    chk = sub.add_parser("check", help="verify the generated directory, the config, the "
                                       "annotations and the curation inputs they were exported from")
    chk.add_argument("companion", type=Path, help="the companion's checkout")

    exp = sub.add_parser("export", help="run a cf export convention and write its Literature "
                                        "Links annotations into annotations/<section>/")
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


def run(args) -> int:
    command = getattr(args, "overlay_command", None)
    if not command:
        raise CfError("say which overlay subcommand to run",
                      hint="cf overlay init | sync | check | export (`cf overlay --help`)")
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
           f"its annotations are valid and current")
    return 0


def _export(args) -> int:
    result = overlay_export.export(args.companion, convention=args.convention, commit=args.commit,
                                   source_id=args.source_id, extra=args.exporter_args)
    log.ok(f"wrote {sum(result.sections.values())} annotation files in {len(result.sections)} "
           f"section folders into {result.directory}")
    for name in result.deleted:
        log.detail(f"deleted {name}")
    log.info(f"next: `cf overlay sync {args.companion}`, then `cf overlay check {args.companion}`")
    return 0


_DISPATCH = {
    "init": _init,
    "sync": _sync,
    "check": _check,
    "export": _export,
}
