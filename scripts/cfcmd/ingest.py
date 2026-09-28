"""cf ingest -- pipeline stage S0: pin one PDF and start a pipeline.

New code for milestone M1 of docs/proposals/pdf-to-companion.md (section 2.1);
the stage itself is ``pipeline/ingest.py`` and docs/pipeline.md says what it
does today and what is deferred.

    cf ingest --pdf book.pdf --scope chapters=1 --pipeline tao-ch1
    cf ingest --pdf book.pdf --scope chapters=1 --pipeline tao-ch1 --errata <url>
    cf ingest --pdf draft.pdf --scope chapters=1 --pipeline p --override-edition "<why>"

Writes ``pipelines/<id>/{pipeline.yaml, doc.v0.json, stages/S0.stage-receipt.json}``,
prepares ``sources/<source id>/`` (text, pages, items, meta, raw errata) and
creates the generated repository ``pipelines/<id>/repo/`` with one commit.
"""
from __future__ import annotations

from cflib import log
from cflib.workspace import from_args
from pipeline import ingest as stage


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "ingest", help="pipeline S0: pin a PDF (sha, pages, offset, edition), fetch its "
                       "errata raw, and write doc v0 and the S0 receipt")
    p.add_argument("--pdf", required=True, help="the PDF to ingest (stays outside git)")
    p.add_argument("--scope", required=True, metavar="chapters=N[,N]",
                   help="the explicit scope: chapters=1, sections=1.3,1.7, or both joined "
                        "with ';'")
    p.add_argument("--pipeline", required=True, metavar="ID",
                   help="the pipeline id: pipelines/<id>/ in the workspace")
    p.add_argument("--errata", default=None, metavar="URL",
                   help="the errata page, fetched raw with curl (default: the registry's "
                        "errata_url for a known edition)")
    p.add_argument("--no-errata", action="store_true",
                   help="fetch no errata; the receipt records that")
    p.add_argument("--licence", default="", metavar="NOTE",
                   help="a licence note recorded on the source (default: the registry's)")
    p.add_argument("--override-edition", default=None, metavar="REASON",
                   help="ingest an edition the registry marks draft or superseded; the "
                        "reason is recorded in the receipt")
    p.add_argument("--offset", type=int, default=None, metavar="N",
                   help="the printed page offset (physical = printed + N) when the running "
                        "heads cannot settle it; stamped offset_method: manual")
    p.add_argument("--source-id", default=None,
                   help="the source id of a book the registry does not know (default: "
                        "derived from the PDF's title)")
    p.add_argument("--companion", default=None, metavar="NAME",
                   help="the generated companion's name (default: the pipeline id)")


def run(args) -> int:
    workspace = from_args(args)
    result = stage.ingest(workspace, pdf=args.pdf, scope_spec=args.scope,
                          pipeline_id=args.pipeline, errata_url=args.errata,
                          no_errata=args.no_errata, licence=args.licence,
                          override_edition=args.override_edition, offset=args.offset,
                          source_id=args.source_id, companion=args.companion)
    edition = result["edition"]
    log.ok(f"S0 {result['pipeline_id']}: {result['source_id']} sha {result['sha256'][:12]}, "
           f"{result['pages']} pages, offset {result['offset']} "
           f"(confidence {result['offset_confidence']:.4f}, {result['offset_method']})")
    log.info(f"  edition   : {edition['status']}"
             + (f" ({edition['source_id']})" if edition.get("source_id") else "")
             + (f", override: {edition['override']}" if edition.get("override") else ""))
    errata = result["errata"]
    log.info("  errata    : " + (f"{errata.get('entries')} entries, raw sha "
                                  f"{str(errata.get('sha256'))[:12]}" if errata else "none"))
    log.info(f"  headings  : {result['items_found']} numbered headings in the whole PDF")
    log.info(f"  repository: {result['repo']} at {result['commit'][:12]}")
    log.info(f"  doc v0    : {result['doc']}")
    log.info(f"  receipt   : {result['receipt']}")
    log.info("  manual    : the page spot check is pending (not built)")
    log.info(f"next: cf inventory --pipeline {result['pipeline_id']}")
    return 0
