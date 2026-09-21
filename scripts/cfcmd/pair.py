"""``cf pair`` -- pair the companions' statement tables, one SOURCE at a time.

    python3 scripts/cf.py pair [--source <id>] [--only ours,adam] [--chapter 1]

A workspace holds N companions and M sources, and a differential is possible
exactly where two companions' source lists overlap (docs/design/archive/multi-source-model.md
R1).  So this command does not pair "the campaign": it walks every source at
least one companion is bound to -- ``workspace.shared_sources()`` plus the
single-companion ones -- and writes one tree per source:

    pairs/<source_id>/pairs.jsonl          items formalised by >= 2 companions
    pairs/<source_id>/coverage.md          the per-item coverage table
    pairs/<source_id>/index.json           item -> repo -> decls (In-text included)
    pairs/<source_id>/reconciliation.json  items.json vs any-repo vs pairs
    pairs/<source_id>/rejected.json        labels that failed the item grammar
    pairs/<source_id>/queue.json           {item, repos, pdf_pages, book_pdf_page}
    pairs/<source_id>/targets.<name>.json  every item a companion formalises
    pairs/<source_id>/def-coverage.<name>.json / uncovered.<name>.json
    pairs/<source_id>/anchor-check.json    mechanical PAGE_ANCHOR rows

and advances ``companions/<name>/state.json``: the per-source counts under
``sources.<source_id>``, the roll-up under ``coverage``.  Nothing here calls a
model.

Two source gates are enforced before anything is compared:

* the bytes -- ``items.json`` must carry the sha ``workspace.yaml`` pins for that
  source, or the run is refused rather than warned about;
* the pagination -- a companion's anchors are checked only against its OWN
  ``anchor_source``.  tao-measure-lean bundles the 2011 draft and anchors to the
  published book; checking its anchors against the wrong edition of the same work
  is what made 22 of 40 batch-1 findings pagination noise.

Ported from: /Users/jack/Desktop/tao-differential/compare.py and recovered cells
b174aa13_L5589 (queue), L6099 (def-coverage), L6145 (uncovered), L5762 (anchors).
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):  # direct execution
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cflib import log, state as state_mod
from cflib.errors import CfError
from cflib.workspace import from_args
from pair import anchor_check, compare as compare_mod, def_coverage as defcov, make_queue
from sources.prepare_source import items_index, load_items, load_meta

NAME = "pair"
HELP = "compare companions item-by-item, per source: pairs, queue, targets, coverage, anchors"


def register(subparsers):
    """cf.py discovery hook."""
    p = subparsers.add_parser(NAME, help=HELP, description=__doc__)
    p.add_argument("--source", help="pair only this source_id (default: every source "
                                    "at least one companion is bound to)")
    p.add_argument("--only", help="comma-separated companions to include")
    p.add_argument("--chapter", type=int, help="restrict def-coverage/uncovered to one chapter")
    p.add_argument("--run-id", help="run id stamped into PAGE_ANCHOR ids "
                                    "(default: anchor-check-<source_id>)")
    p.add_argument("--tolerance", type=int, default=0, help="accepted page-anchor slack")
    p.add_argument("--no-anchors", action="store_true", help="skip the page-anchor check")
    p.add_argument("--no-emit-corpus", action="store_true",
                   help="do not publish the PAGE_ANCHOR rows into corpus/<run-id>/ "
                        "(they then reach no tally, route or ledger)")
    p.add_argument("--no-coverage", action="store_true", help="skip def-coverage/uncovered")
    p.add_argument("--max-page-hints", type=int, default=6)
    p.set_defaults(func=run)
    return p


def run(args) -> int:
    workspace = from_args(args)
    wanted = [n.strip() for n in (args.only or "").split(",") if n.strip()]
    source_ids = _sources_to_pair(workspace, args.source, wanted)

    shared = workspace.shared_sources()
    log.info("pairing %d source(s): %d shared by >=2 companions, %d audited by one"
             % (len(source_ids), sum(1 for s in source_ids if s in shared),
                sum(1 for s in source_ids if s not in shared)))

    # {companion: {source_id: payload}} -- state.json is written once per
    # companion at the end, not once per (companion, source).
    per_companion: dict[str, dict[str, dict]] = {}
    paired = 0
    for source_id in source_ids:
        if _pair_one(workspace, source_id, wanted, args, per_companion):
            paired += 1
    if not paired:
        raise CfError("no source could be paired",
                      hint="run `cf onboard <repo>` (or `cf sources refresh <id>`) so that "
                           "sources/<id>/items.json and companions/<name>/statements.jsonl exist")
    _record_state(workspace, per_companion)
    return 0


# ---------------------------------------------------------------------------
# which sources
# ---------------------------------------------------------------------------

def _sources_to_pair(workspace, only: str | None, wanted) -> list[str]:
    """Every source at least one (selected) companion is audited against.

    ``role: reference`` sources fall out for free: a reference edition is
    prepared for the page map and listed by nobody's ``sources``, so no
    companion is bound to it and there is nothing to pair.
    """
    if only:
        if only not in workspace.sources:
            raise CfError(f"unknown source {only!r}",
                          hint="`cf sources list` shows the workspace's sources")
        names = workspace.companions_of(only)
        if not names:
            raise CfError(f"no companion is audited against {only}",
                          hint=f"`cf onboard <repo>` binds one, or `cf sources set {only} "
                               f"role=reference` says it is audited by nobody")
        return [only]
    out: list[str] = []
    for source_id, names in workspace.shared_sources(min_companions=1).items():
        if wanted and not [n for n in names if n in wanted]:
            continue
        out.append(source_id)
    if not out:
        raise CfError("no source is audited by any companion",
                      hint="run `cf onboard <repo>`; `cf status` lists what the workspace holds")
    return out


# ---------------------------------------------------------------------------
# one source
# ---------------------------------------------------------------------------

def _pair_one(workspace, source_id: str, wanted, args, per_companion: dict) -> bool:
    """Pair one source; returns False when there was nothing to pair."""
    paths = workspace.paths
    ref = workspace.source(source_id)
    names = [n for n in workspace.companions_of(source_id) if not wanted or n in wanted]
    log.info("")
    log.info("source %s" % ref.short())
    if not names:
        log.warn("    no companion selected; skipped")
        return False
    if not paths.source_items(source_id).exists():
        log.warn(f"    sources/{source_id}/items.json is missing; skipped")
        log.info(f"    prepare it: cf sources refresh {source_id}")
        return False

    source_dir = str(paths.source_dir(source_id))
    items_json = load_items(source_dir)
    index_by_item = items_index(items_json)
    source_sha = str(items_json.get("source_sha") or "")
    # the SOURCE gate: the items were built from the bytes workspace.yaml pins,
    # or this run stops.  Batch-1 anchor findings were judged against the 2011
    # draft, and nothing said so.
    workspace.require_source_sha(source_id, source_sha, f"sources/{source_id}/items.json")

    offset = int(items_json.get("offset") if items_json.get("offset") is not None else ref.offset)
    # grammar kinds = this source's heading kinds plus whatever workspace.yaml
    # lists, so a companion tag the source never prints (Strategy 2.1.1) is
    # REJECTED with reason unknown_kind rather than silently keyed.
    kinds = list(dict.fromkeys(
        [str(k) for k in (items_json.get("kinds") or [])] + list(ref.kinds)))
    chapters = list(ref.chapters) or None

    tables = compare_mod.load_tables(
        {n: str(paths.statements_jsonl(n)) for n in names}, source_id=source_id)
    missing = [n for n in names if n not in tables]
    if missing:
        log.warn("    no statements.jsonl for %s (run `cf extract`)" % ", ".join(missing))
    empty = [n for n, rows in tables.items() if not rows]
    if empty:
        log.warn("    %s has no row tagged against this source" % ", ".join(sorted(empty)))
    tables = {n: rows for n, rows in tables.items() if rows}
    if not tables:
        log.warn("    nothing to pair; skipped")
        return False

    out_dir = str(paths.pairs_dir(source_id))
    result = compare_mod.compare(
        tables, out_dir, source_id=source_id, source_sha=source_sha, kinds=kinds,
        book_items=index_by_item, chapters=chapters,
    )
    recon = result["reconciliation"]
    log.info(
        "    pairs: %d items formalised by >=2 of %s (from %d items in any repo; %d in the "
        "source, %d in scope, task universe %d)"
        % (recon["pairs"], ", ".join(result["repos"]), recon["items_in_any_repo"],
           recon.get("items_in_book", 0), recon.get("items_in_scope", 0),
           recon.get("task_universe", 0))
    )
    if recon.get("items_not_in_book"):
        log.info("    %d item ids are NOT headings in %s (see reconciliation.json): %s"
                 % (len(recon["items_not_in_book"]), source_id,
                    ", ".join(recon["items_not_in_book"][:5])
                    + (" ..." if len(recon["items_not_in_book"]) > 5 else "")))
    if recon.get("items_out_of_scope"):
        log.info("    %d tagged items are source items outside the audited chapters"
                 % len(recon["items_out_of_scope"]))
    if result["rejected"]:
        reasons = ", ".join(
            "%s %d" % (k or "?", v) for k, v in sorted(recon["rejected_by_reason"].items())
        )
        log.info("    %d rows failed the item grammar (%s; see rejected.json)"
                 % (len(result["rejected"]), reasons))

    meta = load_meta(source_dir) if paths.source_meta(source_id).exists() else {}
    printed_max = max(1, int(meta.get("pages") or ref.pages or 10000) - offset)
    queue = make_queue.build_queue(
        result["pairs"], source_id=source_id, offset=offset, max_pages=args.max_page_hints,
        book_items=index_by_item, printed_max=printed_max,
    )
    # the attack runs address every tagged item, In-text included (the design decisions record,
    # "item scope"); the differential queue above stays numbered-only.
    all_index = dict(result["index"])
    all_index.update(result["intext"])
    targets = {
        name: make_queue.build_targets(
            all_index, name, source_id=source_id, repos=result["repos"], offset=offset,
            max_pages=args.max_page_hints, book_items=index_by_item, kinds=kinds,
            chapters=chapters, printed_max=printed_max,
        )
        for name in tables
    }
    qsummary = make_queue.write_queue(out_dir, queue, targets, source_id=source_id,
                                      source_sha=source_sha, offset=offset)
    log.info("    queue: %d items (%d with page hints from anchors)"
             % (qsummary["queue"], sum(1 for q in queue if q["pdf_pages"])))

    chapter = args.chapter
    if chapter is None and chapters and len(chapters) == 1:
        chapter = chapters[0]

    coverage: dict[str, dict] = {}
    if not args.no_coverage:
        for name, rows in tables.items():
            cov = defcov.def_coverage(items_json, rows, source_id=source_id,
                                      chapter=chapter, kinds=kinds)
            unc = defcov.uncovered(items_json, rows, source_id=source_id,
                                   kinds=kinds, chapter=chapter)
            coverage[name] = defcov.write_coverage(out_dir, name, cov, unc)
            log.info(
                "    coverage %-10s definitions %d stated / %d bridge-only / %d missing; "
                "%d items uncovered"
                % (name, coverage[name]["def_stated"], coverage[name]["def_bridge_only"],
                   coverage[name]["def_missing"], coverage[name]["uncovered"])
            )

    anchors = None
    run_id = args.run_id or ("anchor-check-%s" % source_id)
    if not args.no_anchors:
        anchored, elsewhere = _anchor_tables(workspace, tables, source_id)
        for name, other in sorted(elsewhere.items()):
            log.info("    %s anchors to %s, not to this source; its page anchors are checked "
                     "there" % (name, other or "(nothing)"))
        if anchored:
            anchors = anchor_check.check(
                index_by_item, anchored, source_id=source_id, source_sha=source_sha,
                offset=offset, run_id=run_id, kinds=kinds, tolerance=args.tolerance,
            )
            anchor_check.write_check(out_dir, anchors)
            log.info(
                "    anchors: %d PAGE_ANCHOR rows (%d advisory +/-1 page) against sha %s"
                % (anchors["summary"]["findings"], anchors["summary"]["advisory"],
                   source_sha[:12])
            )
            if anchors["rows"] and not args.no_emit_corpus:
                _emit_anchor_corpus(paths, anchors, source_id=source_id, run_id=run_id,
                                    source_sha=source_sha, offset=offset,
                                    index_by_item=index_by_item)

    for name in tables:
        stats = qsummary["targets"].get(name, {})
        cov = coverage.get(name, {})
        arow = (anchors["summary"]["repos"].get(name, {}) if anchors else {})
        per_companion.setdefault(name, {})[source_id] = {
            "sha": source_sha,
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
    return True


def _anchor_tables(workspace, tables, source_id: str):
    """Split the loaded tables into "anchors to this source" and "anchors elsewhere".

    A companion's ``anchor_source`` is the edition whose PAGINATION its docstring
    page anchors follow.  Checking a repo that anchors to another edition against
    this one turns every row into a mismatch and buries the real ones.
    """
    anchored, elsewhere = {}, {}
    for name, rows in tables.items():
        anchor = workspace.anchor_source(name)
        if anchor and anchor != source_id:
            elsewhere[name] = anchor
        else:
            anchored[name] = rows
    return anchored, elsewhere


def _record_state(workspace, per_companion: dict[str, dict[str, dict]]) -> None:
    """One state.json write per companion: per-source counts plus a roll-up."""
    for name, by_source in sorted(per_companion.items()):
        with state_mod.update(workspace, name) as st:
            for source_id, payload in sorted(by_source.items()):
                state_mod.source_section(st, source_id, payload)
            roll = {
                "sources": sorted(by_source),
                "by_source": {sid: dict(p) for sid, p in sorted(by_source.items())},
            }
            for key in ("pairs", "targets", "single_source", "shared", "def_missing",
                        "def_bridge_only", "def_stated", "uncovered",
                        "anchors_checked", "anchors_mismatched"):
                values = [p.get(key) for p in by_source.values() if p.get(key) is not None]
                roll[key] = sum(values) if values else None
            state_mod.merge_section(st, "coverage", roll)


# ---------------------------------------------------------------------------
# the mechanical corpus
# ---------------------------------------------------------------------------

def _emit_anchor_corpus(paths, anchors, *, source_id: str, run_id: str, source_sha: str,
                        offset: int, index_by_item) -> None:
    """Publish one source's mechanical PAGE_ANCHOR rows into ``corpus/<run-id>/``.

    the design critique, correction 2: "Route them as mechanical low-severity rows from
    anchor_check.py against the pinned edition."  They could not be routed while
    they lived only in ``pairs/<id>/anchor-check.json``, which ``route`` reads as
    a withdrawal oracle and never as a source (its sources are ``corpus/<run>/``).

    Each document is validated against the ``mechanical`` preset first -- the
    gate that refused anchor_check's own output until its rows carried
    ``source`` and ``page``.  The gate config names exactly ONE source, because
    the run id names one: ``anchor-check-<source_id>``.
    """
    from detect import corpus as mech_corpus
    from gate import verify_findings as vf
    from plan import presets as presets_mod

    rows = anchors["rows"]
    repos = sorted({str(r.get("repo") or "") for r in rows if r.get("repo")})
    pages = {item: (entry.get("page") if isinstance(entry, dict) else None)
             for item, entry in (index_by_item or {}).items()}
    docs = mech_corpus.documents(rows, companion=repos[0] if repos else "",
                                 run_id=run_id, source_id=source_id, source_sha=source_sha,
                                 items_pages={k: v for k, v in pages.items() if v})
    images = paths.source_images_dir(source_id)
    cfg = vf.GateConfig(presets_mod.gate_config(
        presets_mod.get("mechanical"), run_id=run_id, repos=repos,
        sources={source_id: {"text": str(paths.source_text(source_id)),
                             "sha256": source_sha,
                             "images_dir": str(images) if images.is_dir() else None,
                             "page_offset": offset,
                             # the PINNED-TEXT page per item, which is what the
                             # gate binds a quote to.  `pages` above is the
                             # PRINTED page, for detect's own printed_page field:
                             # the two differ by `offset` and swapping them puts
                             # every item that far from its own quotes.
                             "item_pages": presets_mod.item_pages(index_by_item)}}))
    books, problems = vf.load_sources(cfg)
    for doc in docs:
        problems += vf.validate_doc(doc, mech_corpus.slug(doc["item"]) + ".json", cfg, books)
    if problems:
        log.warn("the mechanical gate refused %s's anchor rows; corpus/%s not written"
                 % (source_id, run_id))
        for problem in problems[:8]:
            log.info("      - " + problem)
        return
    dest = paths.corpus_run_dir(run_id)
    receipt = mech_corpus.write_corpus(docs, dest, run_id=run_id,
                                       companion=",".join(repos), source_id=source_id,
                                       source_sha=source_sha,
                                       counts=anchors["summary"].get("repos", {}))
    for stale in mech_corpus.stale_files(dest, receipt["names"]):
        stale.unlink()
    receipt["source_id"] = source_id
    receipt["source_sha"] = source_sha
    receipt["complete"] = True
    receipt["gate"] = {"ok": True, "preset": "mechanical", "problems": []}
    receipt["expected"] = {"count": len(docs), "names": receipt["names"]}
    receipt["actual"] = {"count": len(receipt["names"])}
    receipt["expected_from"] = "cf pair: one document per mismatched item of one source"
    mech_corpus.write_json(dest / "RECEIPT.json", receipt, indent=1, sort_keys=True)
    log.info("    anchors: wrote corpus/%s (%d document(s))" % (run_id, len(docs)))


def main(argv=None) -> int:
    """Standalone entry point: ``python3 cfcmd/pair.py --source <id>``."""
    import argparse

    parser = argparse.ArgumentParser(prog="cf")
    sub = parser.add_subparsers(dest="command", required=True)
    register(sub)
    args = parser.parse_args([NAME] + list(sys.argv[1:] if argv is None else argv))
    args.workspace = os.environ.get("CF_WORKSPACE") or None
    return run(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
