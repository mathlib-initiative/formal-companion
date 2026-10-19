"""cf locate -- the deterministic steps of locate that call no model.

New code, for docs/proposals/wikipedia-locate.md section 3 (L2).  One
subcommand today:

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
"""
from __future__ import annotations

from pathlib import Path

from cflib import log, vocab
from cflib.errors import CfError
from cflib.workspace import from_args

from locate import candidates as cand_mod
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


def run(args) -> int:
    if getattr(args, "locate_command", None) != "candidates":
        raise CfError("cf locate needs a subcommand", hint="cf locate candidates --help")
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
        # a standing parity refusal is no render away (review SEAM-2)
        log.warn(f"{len(refused)} source(s) have no candidates file because render refused "
                 f"them, the layer not the producer's: " + ", ".join(refused[:5])
                 + (f" (+{len(refused) - 5} more)" if len(refused) > 5 else "")
                 + f"; no render of {args.sourceset} settles that (`cf sourceset status "
                   f"{args.sourceset} --verify` lists them): the producer's corrected delivery, "
                   f"adopted into a new sourceset, does")
    return 0
