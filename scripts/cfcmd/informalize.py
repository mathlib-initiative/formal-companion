"""cf informalize -- an informal companion written from a Lean library.

New code (CLAUDE.md section 5 is the contract for adding a command), implementing
components 3, 4, 5b's `apply`, 6, 7 and 9 of docs/proposals/informalize.md section 11;
the stages are its section 3:

    cf informalize init <id> --library URL --commit SHA --module M --theorem T --prefix P \\
        --text-license Apache-2.0 --title T --author A [--reference "<citation line>"]
    cf informalize sync <id> [--check]
    cf informalize status <id>
    cf informalize build-command <id> --host H [--min-free-gb 60] [--lean-threads 16]
    cf informalize fetch-library <id> --host H --execute --i-have-consent
    cf informalize verso-command <id> --host H [--verso-rev v4.36.0-rc2] [--min-free-gb 60]
        [--lean-threads 16]
    cf informalize scope <id>
    cf informalize apply <id> --run <run>
    cf informalize pdf-command <id> --host H
    cf informalize fetch-pdf <id> --host H --execute --i-have-consent
    cf informalize overlay <id> --pdf-url URL [--packages-src DIR]
    cf informalize check-site <id>
    cf informalize check-items <id>
    cf informalize approve <id> items --by <name> --in-v1 <ids|all>
    cf informalize reference-checklist <id>
    cf informalize approve <id> references --by <name>

`init` and `sync` write `informalize/<id>/informalize.yaml`, the output repository
`informalize/<id>/repo/` and the I0 receipt; `fetch-library` writes `informal/
informalize.json`'s `source_date_epoch`, `scope` writes `informal/declarations.json`,
`apply` writes the files a harvested phase wrote and its stage receipt,
`approve <id> items` writes the renumbered table, `sections.tex` and the empty section
files, `fetch-pdf` writes `informal/markers.json`, and `overlay` writes the overlay's
configuration, the annotations, the landing page and the two workflows -- each committed.
Those are the only writes into a repository, under the exception
docs/proposals/informalize.md section 1.3 words (SKILL.md rule 10).  `build-command` runs
nothing: it writes what the host needs into `library/to-host/` and prints the lines that
copy it there and start the library's build; `verso-command` is the same for the Verso
build the overlay's hovers are cut from (section 10), into `verso/to-host/`, and the file
it ends with is committed by hand, as docs/overlay.md section 5 has a companion commit
one; `pdf-command` is the same again for the canonical PDF's two host builds (section 9),
into `pdf/<commit12>/to-host/`.  `fetch-library --execute` and `fetch-pdf --execute` are
the mode's only uses of ssh and rsync (CLAUDE.md section 3); `overlay` and `check-site`
run wholly on this machine and reach no network, building the site with the companion's
own `serve.py --build-only` from the PDF `fetch-pdf` brought home.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from cflib import log
from cflib.errors import CfError
from cflib.vocab import APPROVAL_GATES
from cflib.workspace import from_args
from overlay.config import OPEN_LICENSES

from informalize import apply as apply_mod
from informalize import approve as gates
from informalize import bootstrap, config, library, status
from informalize import overlay as overlay_mod
from informalize import pdf as pdf_mod
from informalize import scope as scope_mod


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

    bc = sub.add_parser("build-command",
                        help="stage I1: write library/to-host/ (the drivers and the library list) "
                             "and print the lines that copy it to the host and start the build; "
                             "runs nothing")
    bc.add_argument("id")
    bc.add_argument("--host", required=True, help="a host of workspace.yaml hosts.allowlist")
    bc.add_argument("--min-free-gb", type=int, default=library.DEFAULT_MIN_FREE_GB,
                    metavar="GB",
                    help="the host's disk floor in GiB, which the build never goes below "
                         f"(default {library.DEFAULT_MIN_FREE_GB}; the driver's own 450 is "
                         "for whole universes)")
    bc.add_argument("--lean-threads", type=int, default=library.DEFAULT_LEAN_THREADS,
                    metavar="N", help="LEAN_NUM_THREADS of the build and the dump (default "
                                      f"{library.DEFAULT_LEAN_THREADS})")

    fl = sub.add_parser("fetch-library",
                        help="stage I1: copy the host's build outputs back into library/, check "
                             "them, unpack the library's source, write the I1 receipt")
    fl.add_argument("id")
    fl.add_argument("--host", required=True, help="the host build-command named")
    fl.add_argument("--execute", action="store_true",
                    help="run ssh and rsync; without it, print what would run")
    fl.add_argument("--i-have-consent", action="store_true",
                    help="say that the host's owner agreed to the build (recorded in the receipt)")

    vc = sub.add_parser("verso-command",
                        help="write verso/to-host/ and print the lines that build the "
                             "library's literate HTML with Verso on the host and cut the "
                             "overlay's pdf-overlay/verso.json from it; runs nothing")
    vc.add_argument("id")
    vc.add_argument("--host", required=True, help="a host of workspace.yaml hosts.allowlist")
    vc.add_argument("--verso-rev", metavar="REV",
                    help="the Verso revision to build against (default: the tag of the "
                         "library's own toolchain, e.g. v4.36.0-rc2)")
    vc.add_argument("--min-free-gb", type=int, default=library.DEFAULT_MIN_FREE_GB,
                    metavar="GB",
                    help="the host's disk floor in GiB, which the job never goes below "
                         f"(default {library.DEFAULT_MIN_FREE_GB}; it copies I1's built "
                         "checkout, a second whole library)")
    vc.add_argument("--lean-threads", type=int, default=library.DEFAULT_LEAN_THREADS,
                    metavar="N", help="LEAN_NUM_THREADS of the build (default "
                                      f"{library.DEFAULT_LEAN_THREADS})")

    sc = sub.add_parser("scope", help="stage I2: the theorem's dependency closure inside the "
                                      "library; writes scope.json and informal/declarations.json")
    sc.add_argument("id")

    ap = sub.add_parser("apply", help="put a harvested phase's files into the output "
                                      "repository, commit them and write its stage receipt")
    ap.add_argument("id")
    ap.add_argument("--run", required=True, metavar="RUN",
                    help="the harvested informalize run of this id to apply")
    ap.add_argument("--unpriced", action="store_true",
                    help="write the receipt although the harvest priced no fleet (its "
                         "cost_usd is then 0 and the receipt says so); by default a "
                         "harvest with no cost block is refused")

    pc = sub.add_parser("pdf-command",
                        help="stage I6: write pdf/<commit12>/to-host/ (the output repository "
                             "at HEAD) and print the lines that copy it to the host and build "
                             "the canonical PDF there twice; runs nothing")
    pc.add_argument("id")
    pc.add_argument("--host", required=True, help="a host of workspace.yaml hosts.allowlist")

    fp = sub.add_parser("fetch-pdf",
                        help="stage I6: bring the canonical PDF, its markers, its receipt and "
                             "the proof sheets home, commit markers.json, write the I6 receipt")
    fp.add_argument("id")
    fp.add_argument("--host", required=True, help="the host pdf-command named")
    fp.add_argument("--execute", action="store_true",
                    help="run ssh and rsync; without it, print what would run")
    fp.add_argument("--i-have-consent", action="store_true",
                    help="say that the host's owner agreed to the build (recorded in the "
                         "receipt)")

    ov = sub.add_parser("overlay",
                        help="stage I7: configure the overlay, write the landing page and the "
                             "workflows, export the annotations, sync and check them, and "
                             "seed the PDF cache")
    ov.add_argument("id")
    ov.add_argument("--pdf-url", required=True, metavar="URL",
                    help="the https URL the maintainer publishes the canonical PDF at (a "
                         "release asset); the viewer fetches it and verifies its sha256")
    ov.add_argument("--packages-src", type=Path, metavar="DIR",
                    help="a .lake/packages directory of the library's build, where a notion "
                         "of a dependency is read: without it such a card shows the "
                         "documentation page and no statement")

    cs = sub.add_parser("check-site",
                        help="stage I7: run the companion's check_generated.py and "
                             "serve.py --build-only, read the built site, write the I7 receipt")
    cs.add_argument("id")

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
                      hint="cf informalize init | sync | status | build-command | "
                           "fetch-library | verso-command | scope | apply | pdf-command | "
                           "fetch-pdf | overlay | check-site | check-items | approve | "
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
    log.info(f"next: cf informalize build-command {args.id} --host <host> (stage I1, the "
             "library's build on the host)")
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


def _build_command(args) -> int:
    workspace = from_args(args)
    built = library.build_command(workspace, args.id, args.host, min_free_gb=args.min_free_gb,
                                  lean_threads=args.lean_threads)
    for line in built.lines:
        print(line)
    log.ok(f"wrote {built.to_host}: {len(built.files) - 1} driver files, "
           f"{library.LIBRARY_LIST} and {library.COMMAND_FILE} (the lines above); nothing ran")
    return 0


def _verso_command(args) -> int:
    workspace = from_args(args)
    built = library.verso_command(workspace, args.id, args.host, verso_rev=args.verso_rev,
                                  min_free_gb=args.min_free_gb,
                                  lean_threads=args.lean_threads)
    for line in built.lines:
        print(line)
    log.ok(f"wrote {built.to_host}: {len(built.files) - 1} driver files, "
           f"{library.LIBRARY_LIST} and {library.VERSO_COMMAND_FILE} (the lines above); "
           "nothing ran")
    return 0


def _fetch_library(args) -> int:
    workspace = from_args(args)
    result = library.fetch_library(workspace, args.id, args.host, execute=args.execute,
                                   consent=args.i_have_consent)
    if not args.execute:
        log.info(f"dry run: fetch-library {args.id} would run, then check what came back:")
        for argv in result.commands:
            print("  " + " ".join(shlex.quote(a) for a in argv))
        log.info("pass --execute --i-have-consent to run them")
        return 0
    facts = result.facts
    log.ok(f"fetched {workspace.paths.informalize_library(args.id)}: HEAD "
           f"{facts['commit'][:12]}, {facts['index']['declarations']} declarations in "
           f"{facts['index']['modules']} modules, canary passed on Lean "
           f"{facts['canary']['lean_version']} ({facts['canary']['form']} form), "
           f"{facts['deps']['repo_constants']} constants dumped, "
           f"{facts['source']['files']} source files")
    theorem = facts["theorem"]
    log.info(f"  {theorem['name']}: non-standard axioms "
             f"{', '.join(theorem['nonstandard_axioms']) or 'none'}")
    log.info(f"  library license {facts['license']['spdx']} ({facts['license']['method']})"
             + ("; written into informalize.yaml" if result.license_written else ""))
    if result.commit:
        log.info(f"  committed {result.commit[:12]}: informal/informalize.json's source date")
    if facts["source"]["links_skipped"]:
        log.warn(f"{len(facts['source']['links_skipped'])} link(s) of the archive were not "
                 f"unpacked: {', '.join(facts['source']['links_skipped'][:5])}")
    log.info(f"next: cf informalize scope {args.id}")
    return 0


def _scope(args) -> int:
    workspace = from_args(args)
    result = scope_mod.scope(workspace, args.id)
    counts = result.counts
    log.ok(f"wrote {result.scope_path}: {counts['members']} declarations, "
           f"{counts['type_only']} of them reached through types alone, depth at most "
           f"{counts['max_depth']} ({counts['walked_through']} generated or private constants "
           "walked through)")
    log.info("  by kind: " + ", ".join(f"{k} {v}" for k, v in counts["by_kind"].items()))
    log.info("  informal/declarations.json "
             + (f"committed {result.commit[:12]}" if result.commit else "unchanged"))
    if counts["unsigned"]:
        log.warn(f"{len(counts['unsigned'])} declaration(s) have no printed signature: "
                 + ", ".join(counts["unsigned"][:5]))
    log.info(f"next: the items phase (stage I3), `cf plan-informalize {args.id} --phase items "
             "--host <host> --i-have-consent`")
    return 0


def _apply(args) -> int:
    workspace = from_args(args)
    done = apply_mod.apply_run(workspace, args.id, args.run, unpriced=args.unpriced)
    if done.already:
        log.ok(f"{done.run_id} is applied already: the output repository holds its "
               f"{len(done.files)} file(s) at {(done.commit or '')[:12]}")
        return 0
    if done.files:
        log.ok(f"applied {done.run_id} ({done.phase}): {len(done.files)} file(s) committed "
               f"{(done.commit or '')[:12]}")
        for relative in done.files:
            log.detail(f"  {relative}")
    else:
        log.ok(f"applied {done.run_id} ({done.phase}): it changed no file of informal/, so "
               "only its receipt is written")
    log.info(f"  wrote {done.receipt} (${done.cost_usd:.2f} over its fleet run(s))")
    for problem in done.check_items:
        log.error(problem)
    if done.check_items:
        log.error(f"{len(done.check_items)} problem(s) in informal/items.json: the items are "
                  f"in, and `cf informalize approve {args.id} items` will refuse them")
        return 1
    log.info(f"next: cf informalize status {args.id}")
    return 0


def _pdf_command(args) -> int:
    workspace = from_args(args)
    built = pdf_mod.pdf_command(workspace, args.id, args.host)
    for line in built.lines:
        print(line)
    log.ok(f"wrote {built.to_host}: {len(built.files)} file(s) of the output repository at "
           f"HEAD and {pdf_mod.COMMAND_FILE} (the lines above); nothing ran")
    return 0


def _fetch_pdf(args) -> int:
    workspace = from_args(args)
    result = pdf_mod.fetch_pdf(workspace, args.id, args.host, execute=args.execute,
                               consent=args.i_have_consent)
    if not args.execute:
        log.info(f"dry run: fetch-pdf {args.id} would run, then check what came back:")
        for argv in result.commands:
            print("  " + " ".join(shlex.quote(a) for a in argv))
        log.info("pass --execute --i-have-consent to run them")
        return 0
    facts = result.facts
    log.ok(f"fetched {result.directory}: {facts['job']}.pdf, {facts['pdf']['bytes']} bytes, "
           f"{facts['pdf']['pages']} pages, sha256 {facts['pdf']['sha256'][:12]}; "
           f"{len(facts['proof_sheets'])} proof sheet file(s)")
    log.info("  the host's checks: "
             + ", ".join(f"{k} {v}" for k, v in facts["checks"].items()))
    log.info(f"  built with {facts['pdftex']}, {facts['latexmk']}, {facts['poppler']} over "
             f"{facts['inputs']} recorded input(s)")
    if result.committed:
        log.info(f"  committed {result.committed[:12]}: informal/markers.json")
    log.info(f"next: read the proof sheets and the PDF, then `cf informalize approve "
             f"{args.id} proof-sheets --by <name>`")
    return 0


def _overlay(args) -> int:
    workspace = from_args(args)
    result = overlay_mod.overlay(workspace, args.id, pdf_url=args.pdf_url,
                                 packages_src=args.packages_src)
    log.ok(f"{result.counts.get('annotation_files', 0)} annotation files in "
           f"{result.counts.get('sections', 0)} section folder(s), the overlay synced and "
           f"checked, {len(result.commits)} commit(s) ({result.head[:12]})")
    for relative in result.written:
        log.detail(f"wrote {relative}")
    if result.cache:
        log.info(f"seeded {result.cache} with the canonical PDF, so the site builds "
                 "before the release URL is live")
    for warning in result.warnings:
        log.warn(warning)
    log.info(f"next: cf informalize check-site {args.id}")
    return 0


def _check_site(args) -> int:
    workspace = from_args(args)
    result = overlay_mod.check_site(workspace, args.id)
    checks = result.checks
    log.ok(f"the built site: {checks['site_files']} overlay file(s) and "
           f"{checks['annotation_files']} annotation file(s) at their sha256, the PDF "
           f"published as {checks['pdf']['published_as']} under {checks['license']}, "
           f"{checks['verso']} Verso hovers")
    log.info(f"  index {checks['index']['path']} links to {checks['index']['href']}")
    log.info(f"  wrote {result.receipt}")
    log.info(f"next: look at the site in a browser, then `cf informalize approve {args.id} "
             "publish --by <name>`")
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
    "build-command": _build_command,
    "fetch-library": _fetch_library,
    "verso-command": _verso_command,
    "scope": _scope,
    "apply": _apply,
    "pdf-command": _pdf_command,
    "fetch-pdf": _fetch_pdf,
    "overlay": _overlay,
    "check-site": _check_site,
    "check-items": _check_items,
    "approve": _approve,
    "reference-checklist": _reference_checklist,
}
