"""cf locate -- the deterministic steps of locate that call no model.

New code, for docs/proposals/wikipedia-locate.md section 3 (L2) and
docs/proposals/statement-cut.md section 4 (the carry-over map).  Two subcommands:

    cf locate candidates --sourceset wpm --universe u1 \\
        [--curated-1000 docs-1000.yaml] [--curated-100 docs-100.yaml] \\
        [--library-metadata results.json] [--blind | --tools-corpus-db FILE] \\
        [--source enwiki-1001-2002]

writes ``sourcesets/<id>/candidates/<universe>/<source-id>.json`` for every
rendered source (references/locate-documents.md section 5), retrieved from the
run's TOOLS database (section 9): the full ``corpus.sqlite``; the G1-blind
``corpus.blind.sqlite`` under ``--blind``, into ``<universe>/blind/``; or a
planted one under ``--tools-corpus-db``, into ``<universe>/planted-<sha16>/``, which
names no curated hit the planted database lacks (only their count is printed and
recorded in the sourceset's ``state.json``).  The curated lists
are the operator's files -- Mathlib's own ``$MATHLIB/docs/1000.yaml`` and
``$MATHLIB/docs/100.yaml`` at the universe's commit -- read as data; nothing is fetched.

    cf locate carry --from inv-a --to inv-b [--source enwiki-1001-2002] \\
        [--rows known.jsonl --rows-out known.carried.jsonl]

writes the carry-over map from one inventory run's cut to another's,
``reports/locate-carry/<from>/<to>.json`` (``cf-locate-carry/1``,
references/locate-documents.md section 12): each old passage's fate from spans and
span text alone (``locate.carry``), for every page both runs inventoried or the
named sources' pages.  With ``--rows`` it copies a JSON-lines file of rows keyed by
``passage_key`` -- known answers, a verdict export -- adding each row's ``carry``
and ``carried``.  It reads the two runs' gated documents and their layers and writes
nothing else; a re-run writes the same bytes.
"""
from __future__ import annotations

from pathlib import Path

from cflib import log, vocab
from cflib.errors import CfError
from cflib.workspace import from_args

from locate import candidates as cand_mod
from locate import carry as carry_mod
from locate import state as ss_state


def register(subparsers) -> None:
    p = subparsers.add_parser("locate", help="the deterministic locate steps (L2 candidates)")
    sub = p.add_subparsers(dest="locate_command", metavar="<subcommand>")
    c = sub.add_parser("candidates",
                       help="L2: curated joins and lexical retrieval per source, for one universe")
    c.add_argument("--sourceset", required=True, help="the sourceset id")
    c.add_argument("--universe", required=True, help="the universe id")
    c.add_argument("--manifest-version", type=int, default=None,
                   help="the sourceset version to read (default: the newest)")
    c.add_argument("--source", dest="sources", action="append", default=[],
                   help="only this source (repeatable; default: every rendered source)")
    c.add_argument("--curated-1000", type=Path, default=None, metavar="FILE",
                   help="a 1000.yaml-shaped file: Wikidata QID -> {title, decl|decls}")
    c.add_argument("--curated-100", type=Path, default=None, metavar="FILE",
                   help="a 100.yaml-shaped file: number -> {title, decl|decls}, matched by title")
    c.add_argument("--curated-library", default=None,
                   help="the universe library the two curated lists name (default: the "
                        "universe's Mathlib library)")
    c.add_argument("--library-metadata", type=Path, default=None, metavar="FILE",
                   help="{\"libraries\": {<id>: [{wikidata, title, declarations}]}}: main results")
    c.add_argument("--blind", action="store_true",
                   help="the G1-blind arm: retrieve from corpus.blind.sqlite, write no curated "
                        "hit, and say so in each file")
    c.add_argument("--tools-corpus-db", type=Path, default=None, metavar="FILE",
                   help="retrieve from this planted database instead (built for the same "
                        "universe; the run it feeds must name the same file)")
    c.add_argument("--limit", type=int, default=cand_mod.DEFAULT_LIMIT,
                   help=f"lexical hits kept per source (default {cand_mod.DEFAULT_LIMIT})")
    k = sub.add_parser("carry",
                       help="the carry-over map between two inventory runs' cuts: each old "
                            "passage's fate, from spans and span text")
    k.add_argument("--from", dest="from_run", required=True, metavar="RUN",
                   help="the inventory run of the old cut")
    k.add_argument("--to", dest="to_run", required=True, metavar="RUN",
                   help="the inventory run of the new cut")
    k.add_argument("--source", dest="sources", action="append", default=[],
                   help="only this source's page (repeatable; default: every page both runs "
                        "inventoried)")
    k.add_argument("--rows", type=Path, default=None, metavar="FILE",
                   help="a JSON-lines file of rows keyed by passage_key (known answers, a "
                        "verdict export) to copy with each row's fate")
    k.add_argument("--rows-out", type=Path, default=None, metavar="FILE",
                   help="where --rows' rows are written, each with carry and carried")


def run(args) -> int:
    command = getattr(args, "locate_command", None)
    if command == "carry":
        return _carry(args)
    if command != "candidates":
        raise CfError("cf locate needs a subcommand",
                      hint="cf locate candidates --help, or cf locate carry --help")
    if args.limit < 1:
        raise CfError("--limit must be at least 1")
    ws = from_args(args)
    lists = cand_mod.CuratedLists(list_1000=args.curated_1000, list_100=args.curated_100,
                                  library_metadata=args.library_metadata,
                                  curated_library=args.curated_library)
    result = cand_mod.run(ws.paths, args.sourceset, args.universe, source_ids=args.sources,
                          version=args.manifest_version, lists=lists, blind=args.blind,
                          planted=args.tools_corpus_db, limit=args.limit, workspace=ws)
    # one row per universe AND database kind, so the blind arm's run never overwrites the
    # assisted one's record; a --source run says it covered only those sources
    kind = result["corpus_db"]["kind"]
    key = args.universe if kind == vocab.TOOLS_DB_FULL else f"{args.universe}/{Path(result['dir']).name}"
    ss_state.record(ws.paths, args.sourceset, "candidates", key, {
        "manifest": result["sourceset"], "universe_sha256": result["universe"]["sha256"],
        "corpus_db": result["corpus_db"], "blind": result["blind"], "dir": result["dir"],
        "partial": bool(args.sources), "written": result["written"],
        "sources": len(result["written"]), "curated": result["curated"],
        "lexical": result["lexical"], "dropped": result["dropped"],
        "withheld": result["withheld"],
        "skipped_unrendered": len(result["skipped_unrendered"])})
    log.info(f"candidates: {len(result['written'])} source(s) of {args.sourceset} "
             f"v{result['sourceset']['version']} against {args.universe} "
             f"({result['corpus_db']['kind']} database)"
             f"{' (BLIND arm: no curated hits)' if args.blind else ''}: "
             f"{result['curated']} curated, {result['lexical']} lexical, "
             f"{result['dropped']} curated name(s) dropped (not in the universe)"
             + (f", {result['withheld']} withheld (not in the planted database; named in "
                f"no file a worker reads)" if kind == vocab.TOOLS_DB_PLANTED else ""))
    refused = result["parity_refused"]
    pending = len(result["skipped_unrendered"]) - len(refused)
    if pending:
        log.warn(f"{pending} source(s) are not rendered and have no candidates file yet")
    if refused:
        # a standing parity refusal is no render away: only a corrected delivery settles it
        log.warn(f"{len(refused)} source(s) have no candidates file because render refused "
                 f"them, the layer not the producer's: " + ", ".join(refused[:5])
                 + (f" (+{len(refused) - 5} more)" if len(refused) > 5 else "")
                 + f"; no render of {args.sourceset} settles that (`cf sourceset status "
                   f"{args.sourceset} --verify` lists them): the producer's corrected delivery, "
                   f"adopted into a new sourceset, does")
    return 0


def _carry(args) -> int:
    if (args.rows is None) != (args.rows_out is None):
        raise CfError("--rows and --rows-out go together",
                      hint="name the rows to carry and the file their copy is written to")
    ws = from_args(args)
    rows = None
    if args.rows is not None:
        # every refusal before anything is written
        carry_mod.check_rows_out(args.rows_out, source=args.rows)
        rows = carry_mod.read_rows(args.rows)
    doc = carry_mod.build(ws.paths, args.from_run, args.to_run, source_ids=args.sources)
    path = carry_mod.write(ws.paths, doc)
    counts = doc["counts"]
    fates = ", ".join(f"{n} {fate}" for fate, n in counts.items() if fate != "pages")
    log.info(f"carry {args.from_run} -> {args.to_run}: {counts['pages']} page(s)"
             + (f" ({fates})" if fates else "") + f"; {path}")
    for side, run_id in (("old", args.from_run), ("new", args.to_run)):
        if doc["unpaired"][side]:
            log.info(f"{len(doc['unpaired'][side])} page(s) only {run_id} inventoried: not mapped")
    if doc["refused"]:
        log.warn_block(f"{len(doc['refused'])} page(s) refused (named in the map's refused):",
                       [f"  {r['pageid']}: {r['reason']}" for r in doc["refused"][:5]]
                       + ([f"  … and {len(doc['refused']) - 5} more"]
                          if len(doc["refused"]) > 5 else []))
    if rows is not None:
        carried, rcounts = carry_mod.carry_rows(doc, rows)
        out = carry_mod.write_rows(args.rows_out, carried, source=args.rows)
        log.info(f"rows: {rcounts['carried']} of {rcounts['rows']} carried, "
                 f"{rcounts['asked_again']} to ask again ({rcounts['unmatched']} on no passage "
                 f"of the old cut); {out}")
    return 0
