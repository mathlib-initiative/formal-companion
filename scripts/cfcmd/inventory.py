"""cf inventory -- pipeline stage S1: the numbered items, errata and passages.

New code for milestone M1 of docs/proposals/pdf-to-companion.md (section 2.2);
the stage is ``pipeline/inventory.py``, its gates ``pipeline/source_gates.py``.

    cf inventory --pipeline tao-ch1
    cf inventory --pipeline tao-ch1 --passages passages.json
    cf inventory --pipeline tao-ch1 --slot-reasons reasons.yaml

Writes ``stages/S1.gate.json`` always, and ``doc.v1.json`` and
``stages/S1.stage-receipt.json`` only when every gate passes.
"""
from __future__ import annotations

from cflib import log
from cflib.errors import CfError
from cflib.workspace import from_args
from pipeline import inventory as stage


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "inventory", help="pipeline S1: numbered items, erratum items and passages with "
                          "exact page quotes; slot, selector and id gates; doc v1")
    p.add_argument("--pipeline", required=True, metavar="ID", help="the pipeline id")
    p.add_argument("--passages", default=None, metavar="FILE",
                   help="passage items in the document's item shape (JSON), each accepted "
                        "when its quote occurs on its page")
    p.add_argument("--no-heuristic", action="store_true",
                   help="skip the heuristic finder for text-marked passages")
    p.add_argument("--slot-reasons", default=None, metavar="FILE",
                   help="YAML or JSON map of missing slot -> reason (default: "
                        "pipelines/<id>/slot-reasons.yaml when it exists)")


def run(args) -> int:
    workspace = from_args(args)
    try:
        result = stage.run_stage(workspace.paths, args.pipeline, passages=args.passages,
                                 heuristic=not args.no_heuristic,
                                 slot_reasons=args.slot_reasons)
    except CfError:
        report = workspace.paths.stage_receipts_dir(args.pipeline) / stage.GATE_REPORT
        if report.exists():
            _explain(report)
        raise
    stats = result["stats"]
    gate = result["gate"]
    checks = gate["checks"]
    log.ok(f"S1 {args.pipeline}: {stats['numbered']} numbered items, "
           f"{stats['passages_heuristic']} heuristic and {stats['passages_supplied']} "
           f"supplied passages, {stats['errata_items']} erratum items")
    log.info(f"  completeness : {checks['slot_completeness']['completeness']}")
    log.info(f"  contiguity   : counters {', '.join(checks['slot_contiguity']['counters'])}; "
             f"{len(checks['slot_contiguity']['explained'])} slot(s) explained")
    log.info(f"  selectors    : {checks['exact_selectors']['checked']} checked on their page")
    if gate["rejected_passages"]:
        log.warn_block(f"{len(gate['rejected_passages'])} supplied passage(s) not accepted",
                       [f"{r['record']}: {r['reason']}" for r in gate["rejected_passages"]])
    if gate.get("duplicate_passages"):
        log.warn_block(f"{len(gate['duplicate_passages'])} supplied passage(s) repeat an "
                       f"accepted one (same capped quote, same id)",
                       [f"{r['record']}: {r['id']} on p.{r['page']}, as {r['duplicate_of']}"
                        for r in gate["duplicate_passages"]])
    log.info(f"  doc v1       : {result['doc_path']}")
    log.info(f"  receipt      : {result['receipt']}")
    return 0


def _explain(report) -> None:
    from cflib.jsonio import read_json
    gate = read_json(report)
    for name, check in sorted((gate.get("checks") or {}).items()):
        if check.get("ok"):
            continue
        lines = []
        for row in check.get("missing", []):
            seen = "; seen as " + ", ".join(f"{s['label']} p.{s['page']}"
                                            for s in row.get("seen_as", [])) \
                if row.get("seen_as") else ""
            lines.append(f"missing slot {row['number']} ({row['counter']}){seen}")
        for row in check.get("failures", []):
            lines.append(f"{row.get('location') or row.get('item')}: {row['problem']}")
        for key, dup in (check.get("duplicates") or {}).items():
            lines.append(f"duplicate {key} ids: {', '.join(dup)}")
        lines += [str(p) for p in check.get("problems", [])]
        for row in check.get("unplaced", []):
            lines.append(f"{row['item']} (page {row.get('page')}): "
                         f"{row.get('reason', 'not placed')}")
        log.warn_block(f"gate {name} failed", lines[:30])
