"""cf informalize -- an informal companion written from a Lean library.

New code (CLAUDE.md section 5 is the contract for adding a command), implementing
component 3 of docs/proposals/informalize.md section 11; the stages are its section 3:

    cf informalize init <id> --library URL --commit SHA --module M --theorem T --prefix P \\
        --text-license Apache-2.0 --title T --author A [--reference "<citation line>"]
    cf informalize sync <id> [--check]
    cf informalize status <id>
    cf informalize check-items <id>
    cf informalize approve <id> items --by <name> --in-v1 <ids|all>
    cf informalize reference-checklist <id>
    cf informalize approve <id> references --by <name>

`init` and `sync` write `informalize/<id>/informalize.yaml`, the output repository
`informalize/<id>/repo/` and the I0 receipt; `approve <id> items` writes the renumbered
table, `sections.tex` and the empty section files there and commits them.  Those are the
only writes into a repository, under the exception docs/proposals/informalize.md section
1.3 words (SKILL.md rule 10).  The other subcommands of section 3 (build-command,
fetch-library, scope, apply, pdf-command, fetch-pdf, overlay, check-site) are later
components' and not implemented yet.
"""
from __future__ import annotations

from cflib import log
from cflib.errors import CfError
from cflib.vocab import APPROVAL_GATES
from cflib.workspace import from_args
from overlay.config import OPEN_LICENSES

from informalize import approve as gates
from informalize import bootstrap, config, status


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "informalize", help="write an informal companion of a Lean theorem: settings, output "
                            "repository, runtime, item table and the maintainer's approvals")
    sub = p.add_subparsers(dest="informalize_command", metavar="<subcommand>")

    init = sub.add_parser("init", help="write informalize.yaml and create the output repository")
    init.add_argument("id", help="the informalize id: lowercase letters, digits and -, "
                                 "at most 40 characters")
    init.add_argument("--library", required=True, metavar="URL",
                      help="the library's https repository URL")
    init.add_argument("--commit", required=True, help="the library commit (40 hex)")
    init.add_argument("--module", required=True, help="the theorem's module")
    init.add_argument("--theorem", required=True, help="the theorem's full Lean name")
    init.add_argument("--prefix", required=True,
                      help="the module prefix of the library's own modules")
    init.add_argument("--text-license", required=True, choices=list(OPEN_LICENSES),
                      help="the open license of the text and the PDF")
    init.add_argument("--title", required=True, help="the text's title")
    init.add_argument("--author", required=True, help="the text's author (the maintainer)")
    init.add_argument("--reference", metavar="CITATION",
                      help="the reference work whose numbers the text cites: a citation line "
                           "(author, title, edition, publisher, year), never a sentence of it")
    init.add_argument("--library-license", metavar="SPDX",
                      help="the library's license, set by hand (stamped license_method: "
                           "manual); otherwise I1 reads it from the library's license file")
    init.add_argument("--library-cache", action="store_true",
                      help="let I1 run the library's own cache script, which may download and "
                           "run code (off by default)")
    init.add_argument("--workers", type=int, help=f"caps.workers (default "
                                                  f"{config.DEFAULT_CAPS['workers']})")
    init.add_argument("--reviewers", type=int, help=f"caps.reviewers (default "
                                                    f"{config.DEFAULT_CAPS['reviewers']})")
    init.add_argument("--max-cost-per-phase", type=float, metavar="USD",
                      help="caps.max_cost_per_phase (default: none, so each plan names its "
                           "own --max-cost)")
    init.add_argument("--agent-timeout", metavar="DURATION",
                      help=f"caps.agent_timeout (default {config.DEFAULT_CAPS['agent_timeout']})")
    init.add_argument("--force", action="store_true",
                      help="rewrite an informalize.yaml that holds other settings")

    syn = sub.add_parser("sync", help="copy the runtime into informal/ and write informalize.json "
                                      "and GENERATED.json, then commit")
    syn.add_argument("id")
    syn.add_argument("--check", action="store_true", dest="check_only",
                     help="write nothing; exit 1 naming every missing, changed or extra "
                          "runtime file")

    sta = sub.add_parser("status", help="settings, repository, receipts, items and approvals")
    sta.add_argument("id")

    chk = sub.add_parser("check-items", help="check informal/items.json against "
                                             "declarations.json and the library's source")
    chk.add_argument("id")

    app = sub.add_parser("approve", help="record the maintainer's approval of a gate")
    app.add_argument("id")
    app.add_argument("gate", choices=list(APPROVAL_GATES))
    app.add_argument("--by", required=True, help="who approves")
    app.add_argument("--in-v1", metavar="IDS",
                     help="items gate: the items of version 1, comma-separated, or all "
                          "(required at the first approval)")
    app.add_argument("--note", default="", help="a note kept in the approval")

    ref = sub.add_parser("reference-checklist",
                         help="write reference-checklist.md: the reference numbers to check "
                              "against the work")
    ref.add_argument("id")


def run(args) -> int:
    command = getattr(args, "informalize_command", None)
    if not command:
        raise CfError("say which informalize subcommand to run",
                      hint="cf informalize init | sync | status | check-items | approve | "
                           "reference-checklist (`cf informalize --help`)")
    return _DISPATCH[command](args)


def _init(args) -> int:
    workspace = from_args(args)
    settings = config.build(
        iid=args.id, url=args.library, commit=args.commit, module=args.module,
        theorem=args.theorem, prefix=args.prefix, text_license=args.text_license,
        title=args.title, author=args.author, reference=args.reference,
        library_license=args.library_license, library_cache=args.library_cache,
        workers=args.workers, reviewers=args.reviewers,
        max_cost_per_phase=args.max_cost_per_phase, agent_timeout=args.agent_timeout)
    result = bootstrap.init(workspace, settings, force=args.force)
    if result.yaml_written:
        log.ok(f"wrote {workspace.paths.informalize_yaml(args.id)}")
    bootstrap.report(result)
    log.info(f"next: cf informalize status {args.id}; then stage I1, the library build on the "
             "host (component 4)")
    return 0


def _sync(args) -> int:
    workspace = from_args(args)
    if args.check_only:
        problems = bootstrap.sync_problems(workspace, args.id)
        for problem in problems:
            log.error(problem)
        if problems:
            log.error(f"{len(problems)} problem(s); `cf informalize sync {args.id}` rewrites "
                      "the runtime")
            return 1
        log.ok(f"{args.id}: informal/ matches the runtime templates and informalize.yaml")
        return 0
    result = bootstrap.sync(workspace, args.id)
    bootstrap.report(result)
    for relative in result.deleted:
        log.detail(f"deleted {relative}")
    return 0


def _status(args) -> int:
    workspace = from_args(args)
    rows = status.report(workspace, args.id)
    log.info(f"informalize {args.id}")
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        log.info(f"  {label.ljust(width)} : {value}")
    return 0


def _check_items(args) -> int:
    workspace = from_args(args)
    problems, doc = gates.check_items(workspace, args.id)
    for problem in problems:
        log.error(problem)
    items = doc.get("items") or []
    if problems:
        log.error(f"{len(problems)} problem(s) in informal/items.json")
        return 1
    log.ok(f"informal/items.json: {len(items)} items in {len(doc.get('sections') or [])} "
           "sections keep section 5.4's rules, and every declaration is in declarations.json "
           "at its keyword line")
    return 0


def _approve(args) -> int:
    workspace = from_args(args)
    approval = gates.approve(workspace, args.id, args.gate, by=args.by, in_v1=args.in_v1,
                             note=args.note)
    log.ok(f"approved {args.gate} for {args.id}, by {approval['by']} at {approval['at']}")
    for key, sha in approval["pins"].items():
        log.info(f"  pins {key}  {sha}")
    if args.gate == "items":
        snapshot = approval["snapshot"]
        log.info(f"  {len(snapshot['items'])} items in {len(snapshot['sections'])} sections"
                 + ("; the ids are frozen (a section was drafted)" if approval["frozen"] else ""))
        log.info(f"next: cf informalize reference-checklist {args.id}")
    elif args.gate == "references":
        log.info(f"  {len(approval['references'])} reference numbers")
    return 0


def _reference_checklist(args) -> int:
    workspace = from_args(args)
    path, rows = gates.reference_checklist(workspace, args.id)
    log.ok(f"wrote {path}: {len(rows)} reference numbers")
    log.info(f"next: check them against the work, then `cf informalize approve {args.id} "
             "references --by <name>`")
    return 0


_DISPATCH = {
    "init": _init,
    "sync": _sync,
    "status": _status,
    "check-items": _check_items,
    "approve": _approve,
    "reference-checklist": _reference_checklist,
}
