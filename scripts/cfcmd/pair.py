"""``cf pair`` -- pair the companions' statement tables against the book.

    python3 scripts/cf.py pair [--only ours,adam] [--chapter 1]

Reads ``<campaign>/companions/*/statements.jsonl`` and ``<campaign>/book/<id>/items.json``
and writes into ``<campaign>/pairs/``:

    pairs.jsonl          items formalised by >= 2 companions (the differential queue)
    coverage.md          the per-item coverage table
    index.json           item -> repo -> decls (including In-text keys)
    reconciliation.json  items.json vs any-repo vs pairs (plan/make_tasks gates on this)
    rejected.json        labels that failed the strict item grammar
    queue.json           {item, repos, pdf_pages, book_pdf_page}
    targets.<name>.json  every item a companion formalises, with peers and priority
    def-coverage.<name>.json / uncovered.<name>.json
    anchor-check.json    mechanical PAGE_ANCHOR rows against the pinned edition

and advances ``companions/<name>/state.json.coverage``.  Nothing here calls a
model.

Ported from: /Users/jack/Desktop/tao-differential/compare.py and recovered cells
b174aa13_L5589 (queue), L6099 (def-coverage), L6145 (uncovered), L5762 (anchors).
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):  # direct execution
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from book.prepare_book import items_index, load_items
from pair import anchor_check, compare as compare_mod, def_coverage as defcov, make_queue
from pair.compat import campaign_paths, load_campaign, read_json, state_merge

NAME = "pair"
HELP = "compare companions item-by-item: pairs, queue, targets, coverage, anchors"


def register(subparsers):
    """cf.py discovery hook."""
    p = subparsers.add_parser(NAME, help=HELP, description=__doc__)
    p.add_argument("--campaign", help="campaign directory (default: $CF_CAMPAIGN or cwd upward)")
    p.add_argument("--book", help="book id (default: campaign.yaml book.id)")
    p.add_argument("--only", help="comma-separated companions to include")
    p.add_argument("--chapter", type=int, help="restrict def-coverage/uncovered to one chapter")
    p.add_argument("--run-id", default="anchor-check", help="run id stamped into PAGE_ANCHOR ids")
    p.add_argument("--tolerance", type=int, default=0, help="accepted page-anchor slack")
    p.add_argument("--no-anchors", action="store_true", help="skip the page-anchor check")
    p.add_argument("--no-coverage", action="store_true", help="skip def-coverage/uncovered")
    p.add_argument("--max-page-hints", type=int, default=6)
    p.set_defaults(func=run)
    return p


def run(args) -> int:
    campaign = load_campaign(args.campaign)
    book_cfg = campaign.book
    book_id = args.book or campaign.book_id
    if not book_id:
        raise SystemExit("error: no book id (pass --book or set book.id in campaign.yaml)")
    paths = campaign_paths(campaign.root, book_id)
    book_dir = str(paths.book_dir)
    items_json = load_items(book_dir)
    index_by_item = items_index(items_json)
    book_sha = items_json.get("book_sha", "")
    offset = int(items_json.get("offset") or 0)
    # grammar kinds = the book's heading kinds plus whatever campaign.yaml lists,
    # so a companion tag the book never prints (Strategy 2.1.1) is REJECTED with
    # reason unknown_kind rather than silently keyed.
    kinds = list(dict.fromkeys(list(items_json.get("kinds") or []) + list(campaign.kinds)))

    wanted = [n.strip() for n in (args.only or "").split(",") if n.strip()]
    names = [n for n in campaign.companion_names() if not wanted or n in wanted]
    if not names:
        names = sorted(
            d for d in os.listdir(str(paths.companions_dir))
            if os.path.exists(str(paths.statements_jsonl(d)))
        )
    tables = compare_mod.load_tables({n: str(paths.statements_jsonl(n)) for n in names})
    missing = [n for n in names if n not in tables]
    if missing:
        print("warning: no statements.jsonl for %s (run `cf extract`)" % ", ".join(missing))
    if not tables:
        raise SystemExit("error: no statements.jsonl found; run `cf extract` first")

    out_dir = str(paths.pairs_dir)
    chapters = book_cfg.get("chapters")
    result = compare_mod.compare(
        tables, out_dir, kinds=kinds, book_items=index_by_item, book_sha=book_sha,
        chapters=chapters,
    )
    recon = result["reconciliation"]
    print(
        "pairs: %d items formalised by >=2 of %s (from %d items in any repo; %d in the book, "
        "%d in scope, task universe %d)"
        % (recon["pairs"], ", ".join(result["repos"]), recon["items_in_any_repo"],
           recon.get("items_in_book", 0), recon.get("items_in_scope", 0),
           recon.get("task_universe", 0))
    )
    if recon.get("items_not_in_book"):
        print("    %d item ids are NOT headings in the pinned edition (see reconciliation.json): %s"
              % (len(recon["items_not_in_book"]),
                 ", ".join(recon["items_not_in_book"][:5])
                 + (" ..." if len(recon["items_not_in_book"]) > 5 else "")))
    if recon.get("items_out_of_scope"):
        print("    %d tagged items are book items outside the audited chapters"
              % len(recon["items_out_of_scope"]))
    if result["rejected"]:
        reasons = ", ".join(
            "%s %d" % (k or "?", v) for k, v in sorted(recon["rejected_by_reason"].items())
        )
        print("    %d rows failed the item grammar (%s; see rejected.json)"
              % (len(result["rejected"]), reasons))

    book_json = read_json(os.path.join(book_dir, "book.json"), default={}) or {}
    printed_max = max(1, int(book_json.get("pages") or 10000) - offset)
    queue = make_queue.build_queue(
        result["pairs"], offset=offset, max_pages=args.max_page_hints,
        book_items=index_by_item, printed_max=printed_max,
    )
    # the attack runs address every tagged item, In-text included (decisions.json
    # "item scope"); the differential queue above stays numbered-only.
    all_index = dict(result["index"])
    all_index.update(result["intext"])
    targets = {
        name: make_queue.build_targets(
            all_index, name, repos=result["repos"], offset=offset,
            max_pages=args.max_page_hints, book_items=index_by_item, kinds=kinds,
            printed_max=printed_max,
        )
        for name in tables
    }
    qsummary = make_queue.write_queue(out_dir, queue, targets, book_sha=book_sha, offset=offset)
    print("queue: %d items (%d with page hints from anchors)"
          % (qsummary["queue"], sum(1 for q in queue if q["pdf_pages"])))

    chapter = args.chapter
    if chapter is None and isinstance(chapters, list) and len(chapters) == 1:
        chapter = chapters[0]

    coverage: dict[str, dict] = {}
    if not args.no_coverage:
        for name, rows in tables.items():
            cov = defcov.def_coverage(items_json, rows, chapter=chapter, kinds=kinds)
            unc = defcov.uncovered(items_json, rows, kinds=kinds, chapter=chapter)
            coverage[name] = defcov.write_coverage(out_dir, name, cov, unc)
            print(
                "coverage %-8s definitions %d stated / %d bridge-only / %d missing; %d items uncovered"
                % (name, coverage[name]["def_stated"], coverage[name]["def_bridge_only"],
                   coverage[name]["def_missing"], coverage[name]["uncovered"])
            )

    anchors = None
    if not args.no_anchors:
        anchors = anchor_check.check(
            index_by_item, tables, book_sha=book_sha, offset=offset,
            run_id=args.run_id, kinds=kinds, tolerance=args.tolerance,
        )
        anchor_check.write_check(out_dir, anchors)
        print(
            "anchors: %d PAGE_ANCHOR rows (%d advisory +/-1 page) against sha %s"
            % (anchors["summary"]["findings"], anchors["summary"]["advisory"], book_sha[:12])
        )

    for name in tables:
        stats = qsummary["targets"].get(name, {})
        cov = coverage.get(name, {})
        arow = (anchors["summary"]["repos"].get(name, {}) if anchors else {})
        payload = {
            "book_sha": book_sha,
            "pairs": recon["pairs"],
            "targets": stats.get("targets", 0),
            "single_source": stats.get("single_source", 0),
            "shared": stats.get("shared", 0),
            "def_missing": cov.get("def_missing"),
            "def_bridge_only": cov.get("def_bridge_only"),
            "def_stated": cov.get("def_stated"),
            "uncovered": cov.get("uncovered"),
            "anchors_checked": arow.get("anchored"),
            "anchors_mismatched": arow.get("mismatched_items"),
        }
        state_merge(campaign, name, "coverage", payload)
    return 0


def main(argv=None) -> int:
    """Standalone entry point (until cf.py is wired up): ``python3 cfcmd/pair.py …``."""
    import argparse

    parser = argparse.ArgumentParser(prog="cf")
    sub = parser.add_subparsers(dest="command", required=True)
    register(sub)
    args = parser.parse_args([NAME] + list(sys.argv[1:] if argv is None else argv))
    return run(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
