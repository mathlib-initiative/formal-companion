"""cf bench -- build, label, freeze and score the locate benchmark; compare two runs.

New code, implementing docs/proposals/wikipedia-locate.md sections 8 and 9 (the
formats are references/locate-bench.md).  ``cf score`` is a different
measurement and is untouched.

    cf bench build <id> --sourceset S --frame tier-a.txt --curated-qids 1000.yaml \
        --curated-qids 100.yaml --seed 7
    cf bench anchor <id> --universe u1 --curated-1000 1000.yaml --curated-100 100.yaml
    cf bench anchor <id> --emit-args anchor        # then run the labeler Workflow
    cf bench anchor <id> --transcribe result.json
    cf bench anchor <id> --g4 spot-checks.jsonl
    cf bench freeze <id> [--plant] [--supersede]
    cf bench score <id> --run <locate-run> --split eval --transcripts <dir>
    cf bench compare <id> <baseline-score> <candidate-score> --claim G3.fnf_rate --aa <replicate>

Labels never leave this machine: ``benches/<id>/host/`` is the only part a run
host may be given (the article lists of each split and the canary strings).
The labeler is a Workflow (``workflows/locate-label.workflow.js``), run in a
session; ``--emit-args`` writes its args and prints the invocation, and
``--transcribe`` turns its result into label files.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from cflib import vocab
from cflib.errors import CfError
from cflib.jsonio import atomic_write, read_json, write_json
from cflib.paths import skill_root
from cflib.workspace import from_args
from cflib.yamlio import read_yaml, write_yaml

from bench import compare as compare_mod
from bench import curated as curated_mod
from bench import draw as draw_mod
from bench import gold as gold_mod
from bench import inputs
from bench import score as score_mod
from bench.layout import (BENCH_SCHEMA, CLUSTERS_SCHEMA, DRAW_SCHEMA, FRAME_SCHEMA, G1_RESOLVED_SCHEMA,
                          GOLD_SCHEMA, HOST_SCHEMA, SPANS_SCHEMA, BenchLayout, now, sha_of)

NAME = "bench"


def register(subparsers) -> None:
    p = subparsers.add_parser(NAME, help="build, label, freeze and score the locate benchmark; "
                                          "compare two runs by the acceptance rule",
                              description=__doc__,
                              formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="bench_command", metavar="<subcommand>")

    b = sub.add_parser("build", help="the frame, the seeded draws, clusters, splits and the G3 "
                                     "span sample; label-free host manifests")
    b.add_argument("bench_id")
    b.add_argument("--sourceset", required=True, help="the sourceset the frame's articles are in")
    b.add_argument("--manifest-version", type=int, default=None,
                   help="the sourceset manifest version to pin (default: the latest)")
    b.add_argument("--frame", required=True, type=Path,
                   help="the tier list: page ids or source ids, one per line, or a JSON list")
    b.add_argument("--curated-qids", type=Path, action="append", default=[], metavar="FILE",
                   help="a curated list: Mathlib's 1000.yaml (keyed by QID, suffixed keys "
                        "included), its 100.yaml (numbered; resolved through a 1000.yaml given "
                        "with it, else matched by title), or a plain list of QIDs; repeatable")
    b.add_argument("--uniform", type=int, default=40, help="articles drawn uniformly (default 40)")
    b.add_argument("--curated", type=int, default=40,
                   help="articles drawn from the frame's curated QIDs (default 40)")
    b.add_argument("--spans-per-article", type=int, default=8,
                   help="G3 spans drawn per article (default 8)")
    b.add_argument("--dev", type=float, default=0.5, help="share of articles in dev (default 0.5)")
    b.add_argument("--eval", type=float, default=0.5, help="share in eval (default 0.5)")
    b.add_argument("--test", type=float, default=0.0, help="share in test (default 0: no test split)")
    b.add_argument("--seed", type=int, default=0, help="the seed every draw is a function of")
    b.add_argument("--exclude-unrendered", action="store_true",
                   help="bench the drawn articles that are rendered and record the others in "
                        "draw.json's excluded, with their inclusion probabilities (by default "
                        "the build refuses and lists the sources to fetch and render)")

    a = sub.add_parser("anchor", help="G1 resolution, the frozen labeler's emit and transcribe "
                                      "(G1 anchors, G3 blind labels, h, GX), and G4 spot checks")
    a.add_argument("bench_id")
    a.add_argument("--universe", default=None, help="the universe gold is labelled at")
    a.add_argument("--library", action="append", default=[],
                   help="the library the curated lists name (default: the universe's Mathlib)")
    a.add_argument("--curated-1000", type=Path, default=None, help="Mathlib's $MATHLIB/docs/1000.yaml")
    a.add_argument("--curated-100", type=Path, default=None, help="Mathlib's $MATHLIB/docs/100.yaml")
    a.add_argument("--rename-table", type=Path, default=None,
                   help="old -> new declaration names, for curated names the pin renamed")
    a.add_argument("--emit-args", default=None, choices=sorted(vocab.BENCH_LABEL_MODES),
                   help="write the labeler's args for one mode and print the invocation")
    a.add_argument("--transcribe", type=Path, default=None,
                   help="a labeler result (the Workflow's return value) to turn into labels")
    a.add_argument("--g4", type=Path, default=None, help="the maintainer's spot checks (JSON lines)")
    a.add_argument("--checkout", action="append", default=[], metavar="LIB=DIR",
                   help="a local checkout of a library at its pinned commit, for the hunters")
    a.add_argument("--type-search", default="", help="how the hunters run a type search at the pin")
    a.add_argument("--search-url", default="", help="a LeanSearch-class service the hunters may use")
    a.add_argument("--model", default="opus", help="the labeler's model (default opus)")
    a.add_argument("--hunters", type=int, default=2, help="independent hunters per task (default 2)")
    a.add_argument("--gx", type=int, default=8, help="articles inventoried exhaustively (default 8)")
    a.add_argument("--signed-by", default=None, help="with --transcribe of a GX result: the maintainer")
    a.add_argument("--transcripts", type=Path, default=None,
                   help="with --transcribe: the labeler's transcripts, priced into spend.json")
    a.add_argument("--supersede", action="store_true",
                   help="change the gold of a frozen bench (the start of a new gold version)")

    f = sub.add_parser("freeze", help="write the gold manifest: tiers, splits, label shas, the "
                                      "labeler's shas, h; irreversible")
    f.add_argument("bench_id")
    f.add_argument("--plant", action="store_true",
                   help="also write the module-ablated corpus.sqlite of the planted tier P")
    f.add_argument("--supersede", action="store_true",
                   help="write a new gold version after the gold changed")

    s = sub.add_parser("score", help="every section 8.2 metric of one run on one split, with "
                                     "article-clustered intervals and a measurement_id")
    s.add_argument("bench_id")
    s.add_argument("--run", required=True, help="the locate run whose passage outcomes are scored")
    s.add_argument("--split", required=True, choices=sorted(vocab.BENCH_SPLITS))
    s.add_argument("--arm", default=None, choices=sorted(vocab.BENCH_ARMS),
                   help="the G1 arm the run is (default: what its run.yaml says)")
    s.add_argument("--gold-version", type=int, default=None, help="default: the latest")
    s.add_argument("--gate-log", type=Path, default=None,
                   help="the harvested gate-log directory of the run's tend roots")
    s.add_argument("--transcripts", type=Path, default=None,
                   help="the run's transcripts, scanned for the label canary")
    s.add_argument("--canary-report", type=Path, default=None,
                   help="a host-side canary scan ({scanned, hits}) instead of --transcripts")
    s.add_argument("--no-canary-check", action="store_true",
                   help="score without a canary scan (recorded in the score)")
    s.add_argument("--article-costs", type=Path, default=None,
                   help="{source id: dollars} over every session of each article's task")
    s.add_argument("--audit-labels", type=Path, default=None,
                   help="labelled rows of this score's overclaim audit sample")
    s.add_argument("--pin", action="append", default=[], metavar="KEY=VALUE",
                   help="agent_cli, tend or agentbox version a run.yaml does not record")
    s.add_argument("--bootstrap", type=int, default=score_mod.DEFAULT_REPLICATES,
                   help="bootstrap replicates (default 2000)")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--checkpoint", action="store_true", help="required to score the test split")
    s.add_argument("--json", action="store_true", dest="as_json")

    c = sub.add_parser("compare", help="the section 9 acceptance rule over two score documents")
    c.add_argument("bench_id")
    c.add_argument("baseline", type=Path)
    c.add_argument("candidate", type=Path)
    c.add_argument("--claim", required=True, help="the pre-registered metric, e.g. G3.fnf_rate")
    c.add_argument("--aa", type=Path, default=None, help="the baseline's A/A replicate score")
    c.add_argument("--ledger", type=Path, default=None, help="default: benches/<id>/ledger.jsonl")
    c.add_argument("--alpha", type=float, default=compare_mod.DEFAULT_ALPHA)
    c.add_argument("--replication", action="store_true",
                   help="this comparison replicates a claim on a fresh draw (spends no alpha)")
    c.add_argument("--bootstrap", type=int, default=2000)
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--note", default="")
    c.add_argument("--json", action="store_true", dest="as_json")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _bench(layout: BenchLayout) -> dict:
    if not layout.bench_yaml.is_file():
        raise CfError(f"no bench {layout.bench_id!r} at {layout.root}",
                      hint=f"cf bench build {layout.bench_id} --sourceset <id> --frame <file>")
    doc = read_yaml(layout.bench_yaml)
    if not isinstance(doc, dict) or doc.get("schema") != BENCH_SCHEMA:
        raise CfError(f"{layout.bench_yaml} is not a {BENCH_SCHEMA} file")
    return doc


def _save_bench(layout: BenchLayout, doc: dict) -> None:
    write_yaml(layout.bench_yaml, doc, header=f"# bench.yaml -- {layout.bench_id} (cf bench)\n")


def _frozen(layout: BenchLayout) -> bool:
    return bool(layout.gold_manifests())


def _articles(layout: BenchLayout) -> dict[str, dict]:
    """The drawn articles, from the frame, with their inclusion probabilities."""
    frame = {a["source_id"]: a for a in read_json(layout.frame_json)["articles"]}
    out = {}
    for row in read_json(layout.draw_json)["articles"]:
        out[row["source_id"]] = {**frame[row["source_id"]], "pi": row["pi"]}
    return out


def _recluster(paths, layout: BenchLayout, bench: dict) -> dict:
    articles = _articles(layout)
    cluster_map = draw_mod.clusters(list(articles.values()), gold_mod.gold_declarations(layout))
    fractions = bench["fractions"]
    splits = draw_mod.split(cluster_map, salt=f"{layout.bench_id}:{bench['seed']}", fractions=fractions)
    doc = {"schema": CLUSTERS_SCHEMA, "bench": layout.bench_id, "clusters": cluster_map,
           "splits": dict(sorted(splits.items())),
           "articles": dict(sorted(draw_mod.article_splits(cluster_map, splits).items())),
           "basis": "shared article QIDs and shared gold accept-set declarations"}
    write_json(layout.clusters_json, doc, sort_keys=True)
    by_split = {s: sorted(a for a, v in doc["articles"].items() if v == s) for s in ("dev", "eval", "test")}
    write_json(layout.host_manifest, {
        "schema": HOST_SCHEMA, "bench": layout.bench_id, "sourceset": bench["sourceset"],
        "articles": by_split,
        "note": "label-free: article lists only. Labels, spans and gold stay on the operator's machine."},
        sort_keys=True)
    layout.host_canaries.write_text(bench["canary"] + "\n", encoding="utf-8")
    return doc


def _universe(paths, bench: dict, requested: str | None) -> tuple[str, dict]:
    uid = requested or (bench.get("universe") or {}).get("id")
    if not uid:
        raise CfError("no universe: gold is labelled at a universe's pins", hint="pass --universe <id>")
    return uid, inputs.load_universe(paths, uid)


def _library(universe: dict, requested: list[str]) -> str:
    libs = inputs.libraries(universe)
    if requested:
        for lib in requested:
            if lib not in libs:
                raise CfError(f"library {lib!r} is not in the universe")
        return requested[0]
    found = [lib for lib, e in libs.items() if e.get("brain_lib") == "Mathlib" or lib == "mathlib"]
    if len(found) != 1:
        raise CfError("the universe holds no single Mathlib library for the curated lists",
                      hint="name it with --library <id>")
    return found[0]


def _skill_root() -> str:
    return str(skill_root())


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def _print_curated(lists: dict) -> None:
    """What each curated list contributed, and what it could not place -- never silent."""
    if not lists["files"]:
        print("  curated  : no curated list (--curated-qids): the curated draw is empty")
        return
    for f in lists["files"]:
        line = f"  curated  : {f['name']} ({f['kind']}): {f['entries']} entr(ies), {f['qids']} QID(s)"
        if f["kind"] == curated_mod.LIST_100:
            line += f", {f['title_only']} matched by title only (no QID)"
        if f["rejected"]:
            line += (f"; {len(f['rejected'])} value(s) are not QIDs and were not read: "
                     + ", ".join(f["rejected"][:5]) + (" …" if len(f["rejected"]) > 5 else ""))
        print(line)
    if lists["unmatched_100"]:
        print(f"  100.yaml : {len(lists['unmatched_100'])} entr(ies) name no frame article "
              f"(frame.json curated_lists.unmatched_100); one the frame should hold is settled "
              f"by its QID in a plain list")


def _unrendered_step(sourceset: str, listed: Path) -> str:
    """The named step that makes the drawn articles benchable: fetch and render exactly them."""
    return (f"fetch and render exactly the sources listed in {listed} -- cf sourceset fetch "
            f"{sourceset}, then cf sourceset render {sourceset} --source <id> for each -- and "
            f"build again: the draw depends on the frame and the seed alone, so it does not change")


def _build(ws, args) -> int:
    paths = ws.paths
    layout = BenchLayout.of(paths, args.bench_id)
    previous = _bench(layout) if layout.bench_yaml.is_file() else None
    if _frozen(layout):
        raise CfError(f"bench {args.bench_id} is frozen; its draw is part of its gold",
                      hint="build a new bench id for a new draw")
    had_gold = layout.gold_dir.is_dir() and any(layout.gold_dir.iterdir())
    old_draw = layout.draw_json.read_bytes() if layout.draw_json.is_file() else None
    manifest = inputs.load_manifest(paths, args.sourceset, args.manifest_version)
    rows = draw_mod.read_frame_list(args.frame)
    curated = curated_mod.read_lists(args.curated_qids)
    frame = draw_mod.build_frame(rows, manifest, curated)
    frame_doc = {"schema": FRAME_SCHEMA, "bench": args.bench_id, "sourceset": manifest.pin,
                 "frame_file_sha256": inputs.sha256_file(args.frame), **frame}
    drawn = draw_mod.draw(frame, uniform=args.uniform, curated=args.curated, seed=args.seed)
    # the draw reads no layer; the drawn articles' spans do (section 8.5 step 3)
    unrendered = draw_mod.unrendered_drawn(frame, drawn)
    if unrendered:
        atomic_write(layout.unrendered_list, "".join(f"{sid}\n" for sid in unrendered))
        if not args.exclude_unrendered:
            raise CfError(
                f"{len(unrendered)} of the {len(drawn['articles'])} drawn article(s) are not rendered "
                f"in sourceset {args.sourceset} version {manifest.version}: "
                + ", ".join(unrendered[:10]) + (" …" if len(unrendered) > 10 else ""),
                hint=_unrendered_step(args.sourceset, layout.unrendered_list)
                + "; or pass --exclude-unrendered to bench the rest, each excluded article "
                  "recorded in draw.json with its inclusion probabilities")
        drawn = draw_mod.exclude(drawn, unrendered, reason=f"not rendered in sourceset "
                                 f"{args.sourceset} version {manifest.version}")
    elif layout.unrendered_list.is_file():
        layout.unrendered_list.unlink()
    draw_doc = {"schema": DRAW_SCHEMA, "bench": args.bench_id, **drawn}
    if had_gold and old_draw is not None and json.loads(old_draw) != draw_doc:
        raise CfError(f"bench {args.bench_id} already holds gold labelled for another draw",
                      hint="a new draw is a new bench: build it under a new id")
    write_json(layout.frame_json, frame_doc, sort_keys=True)
    write_json(layout.draw_json, draw_doc, sort_keys=True)
    bench = {"schema": BENCH_SCHEMA, "id": args.bench_id,
             "created": (previous or {}).get("created") or now(),
             "sourceset": manifest.pin, "seed": args.seed,
             "draw": {"uniform": args.uniform, "curated": args.curated},
             "spans_per_article": args.spans_per_article,
             "fractions": {"dev": args.dev, "eval": args.eval, "test": args.test},
             "canary": (previous or {}).get("canary") or gold_mod.new_canary(),
             "universe": (previous or {}).get("universe"), "state": "built"}
    _save_bench(layout, bench)
    articles = _articles(layout)
    spans = []
    for sid in sorted(articles):
        layer = inputs.read_layer(paths, args.sourceset, sid, articles[sid].get("layer_sha256"))
        paragraphs = inputs.read_source_json(paths, args.sourceset, sid, "paragraphs.json")
        sections = inputs.read_source_json(paths, args.sourceset, sid, "sections.json",
                                           required=False) or []
        frame_spans = draw_mod.span_frame(layer, paragraphs, sections)
        chosen = draw_mod.sample_spans(sid, frame_spans, args.spans_per_article, args.seed,
                                       articles[sid]["pi"])
        draw_mod.check_spans_disjoint(layer, chosen)
        spans.extend(chosen)
    write_json(layout.spans_json, {"schema": SPANS_SCHEMA, "bench": args.bench_id,
                                   "per_article": args.spans_per_article, "spans": spans,
                                   "drawn_from": "paragraphs.json blocks with at least four words, "
                                                 "sectioned by sections.json; no run"},
               sort_keys=True)
    clusters = _recluster(paths, layout, bench)
    counts = {s: sum(1 for v in clusters["articles"].values() if v == s) for s in ("dev", "eval", "test")}
    print(f"built bench {args.bench_id}: frame {frame['N']} article(s) ({frame['N_curated']} curated), "
          f"drawn {len(articles)} ({drawn['uniform']['n']} uniform, {drawn['curated']['n']} curated), "
          f"{len(clusters['clusters'])} cluster(s), {len(spans)} G3 span(s)")
    if frame["unrendered"]:
        print(f"  frame    : {frame['N_rendered']} rendered, {len(frame['unrendered'])} not yet "
              f"(frame.json unrendered); the draw is the whole frame's, so only drawn ones must be")
    if drawn.get("excluded"):
        print(f"  excluded : {len(drawn['excluded'])} drawn article(s) not rendered, benched without "
              f"them (draw.json excluded, each with its inclusion probabilities; "
              f"{layout.unrendered_list} lists them)")
    _print_curated(frame["curated_lists"])
    print(f"  splits   : dev {counts['dev']}, eval {counts['eval']}, test {counts['test']}")
    print(f"  host     : {layout.host_manifest} (label-free)")
    print(f"  next     : cf bench anchor {args.bench_id} --universe <id> --curated-1000 <file>")
    return 0


# --------------------------------------------------------------------------
# anchor
# --------------------------------------------------------------------------

def _anchor(ws, args) -> int:
    paths = ws.paths
    layout = BenchLayout.of(paths, args.bench_id)
    bench = _bench(layout)
    actions = [x for x in (args.emit_args, args.transcribe, args.g4) if x]
    resolving = bool(args.curated_1000 or args.curated_100)
    if len(actions) + resolving > 1:
        raise CfError("one action at a time: resolve the curated lists, --emit-args, --transcribe "
                      "or --g4")
    if _frozen(layout) and not args.supersede and (resolving or args.transcribe or args.g4):
        raise CfError(f"bench {args.bench_id} is frozen: its gold is never edited",
                      hint="pass --supersede to start a new gold version, then "
                           "cf bench freeze --supersede")
    uid, universe = _universe(paths, bench, args.universe)
    usha = str(universe.get("universe_sha256") or "")
    if bench.get("universe") and bench["universe"].get("id") != uid:
        raise CfError(f"the bench's gold is labelled at universe {bench['universe']['id']!r}, not {uid!r}")
    bench["universe"] = {"id": uid, "sha256": usha}
    _save_bench(layout, bench)
    articles = _articles(layout)
    conn = inputs.open_corpus(paths.corpus_db(uid))
    try:
        if resolving:
            library = _library(universe, args.library)
            commit = inputs.libraries(universe)[library]["commit"]
            lists = gold_mod.read_curated_lists(args.curated_1000, args.curated_100)
            resolved = gold_mod.resolve_g1(list(articles.values()), conn, library=library,
                                           commit=commit, lists=lists,
                                           renames=gold_mod.read_rename_table(args.rename_table),
                                           tag_libraries=args.library or [library])
            write_json(layout.g1_resolved, {"schema": G1_RESOLVED_SCHEMA, "bench": args.bench_id,
                                            "universe": bench["universe"], "library": library,
                                            "commit": commit, **resolved}, sort_keys=True)
            bench["library"] = library
            bench["state"] = "anchored"
            _save_bench(layout, bench)
            _recluster(paths, layout, bench)
            print(f"resolved G1 for bench {args.bench_id}: {len(resolved['entries'])} entr(ies) "
                  f"at {library}@{commit[:12]}, {len(resolved['dropped_entries'])} dropped "
                  f"(nothing at the pin), unmatched {resolved['unmatched'] or 'none'}")
            print(f"  next     : cf bench anchor {args.bench_id} --emit-args anchor")
            return 0
        if args.emit_args:
            checkouts = {}
            for spec in args.checkout:
                lib, sep, directory = spec.partition("=")
                if not sep or not lib or not directory:
                    raise CfError(f"--checkout {spec!r} is not LIB=DIR")
                checkouts[lib] = str(Path(directory).expanduser().resolve())
            gx = None
            if args.emit_args == "inventory":
                split_of = read_json(layout.clusters_json)["articles"]
                pool = sorted(s for s in articles if split_of.get(s) == "eval") or sorted(articles)
                import random

                rng = random.Random(f"{bench['seed']}:gx")
                gx = sorted(rng.sample(pool, min(args.gx, len(pool))))
                bench["gx_articles"] = gx
                _save_bench(layout, bench)
            payload = gold_mod.emit_args(
                args.emit_args, paths=paths, layout=layout, bench=bench, articles=articles,
                universe=universe, conn=conn, checkouts=checkouts,
                tools={"ripgrep": "rg over each pinned checkout",
                       "type_search": args.type_search, "search_url": args.search_url},
                model=args.model, hunters=args.hunters, gx_articles=gx)
            out = layout.label_args(args.emit_args)
            write_json(out, payload, sort_keys=True)
            script = Path(_skill_root()) / gold_mod.LABELER_WORKFLOW
            print(f"{args.bench_id}: {len(payload['tasks'])} {args.emit_args} task(s) -> {out}")
            print("  run it in a Claude Code session (a Workflow cannot be launched from this CLI):")
            print(f"    Workflow({{ scriptPath: \"{script}\", args: <the JSON in {out}> }})")
            print(f"  ~{len(payload['tasks']) * (args.hunters + 1)} subagents; the args file holds "
                  "source excerpts and stays on this machine.")
            print(f"  then: cf bench anchor {args.bench_id} --transcribe <result.json>")
            return 0
        if args.transcribe:
            result = read_json(args.transcribe)
            mode = (result or {}).get("mode") if isinstance(result, dict) else None
            if mode not in vocab.BENCH_LABEL_MODES:
                raise CfError(f"{args.transcribe} is not a labeler result (mode {mode!r})")
            emitted = layout.label_args(mode)
            if not emitted.is_file():
                raise CfError(f"no {mode} args were emitted for bench {args.bench_id}",
                              hint=f"cf bench anchor {args.bench_id} --emit-args {mode}")
            summary = gold_mod.transcribe(result, read_json(emitted), paths=paths, layout=layout,
                                          bench=bench, articles=articles, universe=universe,
                                          conn=conn, signed_by=args.signed_by,
                                          after_freeze=args.supersede)
            digest = inputs.sha256_file(args.transcribe)
            copy = layout.label_result_copy(mode, digest)
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.transcribe, copy)
            cost = None
            if args.transcripts:
                from cost import fleet, pricing

                table, source = pricing.from_workspace_data(ws.data)
                cost = fleet.cost_block(args.transcripts, table=table, table_source=source)
            gold_mod.record_spend(layout, mode, summary["labels"], cost)
            _recluster(paths, layout, bench)
            print(f"transcribed {mode} for bench {args.bench_id}: "
                  + ", ".join(f"{k} {v if not isinstance(v, list) else len(v)}"
                              for k, v in summary.items() if k != "mode"))
            return 0
        if args.g4:
            rows = [json.loads(line) for line in args.g4.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
            summary = gold_mod.import_g4(rows, paths=paths, layout=layout, bench=bench,
                                         articles=articles, universe=universe, conn=conn,
                                         after_freeze=args.supersede)
            _recluster(paths, layout, bench)
            print(f"G4 for bench {args.bench_id}: {summary['rows']} spot check(s), "
                  f"{summary['positives']} positive, {summary['negatives']} negative")
            return 0
    finally:
        conn.close()
    raise CfError("nothing to do", hint="pass --curated-1000/--curated-100, --emit-args, "
                                        "--transcribe or --g4")


# --------------------------------------------------------------------------
# freeze
# --------------------------------------------------------------------------

def _freeze(ws, args) -> int:
    paths = ws.paths
    layout = BenchLayout.of(paths, args.bench_id)
    bench = _bench(layout)
    if not bench.get("universe"):
        raise CfError("gold is labelled at a universe; the bench has none yet",
                      hint=f"cf bench anchor {args.bench_id} --universe <id> ...")
    gold_mod.check_canaries(layout, bench["canary"])
    clusters = _recluster(paths, layout, bench)
    articles_split = clusters["articles"]
    tiers = {}
    for tier in ("G1", "G3", "GX", "G4"):
        info = gold_mod.tier_summary(layout, tier)
        if info:
            tiers[tier] = info
    if "GX" in tiers and not tiers["GX"]["signed_by"]:
        raise CfError("the GX inventories carry no maintainer signature",
                      hint="transcribe them with --signed-by <name>")
    dev_decls: set[str] = set()
    rows_by_tier = {t: gold_mod.read_labels(layout, t, required=False) for t in ("G1", "G3", "GX", "G4")}

    def keys(row: dict) -> set[str]:
        found = {f"{a['library']}:{a['declaration']}" for a in row.get("accept") or []}
        found |= {f"{d['library']}:{d['declaration']}" for d in row.get("declarations") or []
                  if d.get("relation") in gold_mod.COUNTING}
        return found

    for rows in rows_by_tier.values():
        for row in rows:
            if articles_split.get(row["source_id"]) == "dev":
                dev_decls |= keys(row)
    seen = sorted(row["gold_id"] for rows in rows_by_tier.values() for row in rows
                  if articles_split.get(row["source_id"]) in ("eval", "test") and keys(row) & dev_decls)
    if args.plant:
        eval_g1 = [r for r in rows_by_tier["G1"] if articles_split.get(r["source_id"]) == "eval"]
        ablation = gold_mod.plant(layout, paths.corpus_db(bench["universe"]["id"]), eval_g1)
        tiers["P"] = {"ablation_sha256": inputs.sha256_file(layout.ablation_json),
                      "corpus_sha256": ablation["corpus_sha256"],
                      "removed_declarations": len(ablation["removed"]),
                      "modules": len(ablation["modules"])}
    elif layout.ablation_json.is_file():
        ablation = read_json(layout.ablation_json)
        tiers["P"] = {"ablation_sha256": inputs.sha256_file(layout.ablation_json),
                      "corpus_sha256": ablation["corpus_sha256"],
                      "removed_declarations": len(ablation["removed"]),
                      "modules": len(ablation["modules"])}
    calibration = read_json(layout.calibration_json) if layout.calibration_json.is_file() else None
    labeler = {"version": gold_mod.LABELER_VERSION, "skill_commit": score_mod.skill_commit(),
               "workflow_sha256": {}, "prompt_sha256": {}}
    for mode in sorted(vocab.BENCH_LABEL_MODES):
        emitted = layout.label_args(mode)
        if emitted.is_file():
            sha = read_json(emitted)["labeler"]["workflow_sha256"]
            labeler["workflow_sha256"][mode] = sha
            # the prompts are inline in the workflow, so its sha is theirs
            labeler["prompt_sha256"][mode] = sha
    doc = {
        "schema": GOLD_SCHEMA, "bench": args.bench_id,
        "sourceset": bench["sourceset"], "universe": bench["universe"],
        "library": bench.get("library"),
        "frame_sha256": inputs.sha256_file(layout.frame_json),
        "draw_sha256": inputs.sha256_file(layout.draw_json),
        "excluded": [{"source_id": r["source_id"], "reason": r["reason"], "pi": r["pi"]}
                     for r in read_json(layout.draw_json).get("excluded") or []],
        "spans_sha256": inputs.sha256_file(layout.spans_json),
        "clusters_sha256": inputs.sha256_file(layout.clusters_json),
        "clusters": clusters["clusters"],
        "splits": {s: sorted(a for a, v in articles_split.items() if v == s)
                   for s in ("dev", "eval", "test")},
        "tiers": tiers, "seen": seen,
        "h": ({"found": calibration["found"], "total": calibration["total"], "h": calibration["h"],
               "tag_blind": True} if calibration else None),
        "labeler": labeler,
        "canary_sha256": sha_of(bench["canary"]),
    }
    manifests = layout.gold_manifests()
    if manifests:
        last_version, last = gold_mod.latest_manifest(layout)
        if gold_mod.manifest_body(last) == gold_mod.manifest_body(gold_mod.seal(dict(doc))):
            print(f"bench {args.bench_id} is frozen at gold version {last_version} "
                  f"({last['manifest_sha256'][:12]}); nothing changed")
            return 0
        if not args.supersede:
            raise CfError(f"bench {args.bench_id} is frozen and its gold changed",
                          hint="gold is never edited: pass --supersede to write gold version "
                               f"{last_version + 1}")
        doc["version"] = last_version + 1
        doc["previous_sha256"] = last["manifest_sha256"]
    else:
        doc["version"] = 1
        doc["previous_sha256"] = None
    doc["created"] = now()
    gold_mod.seal(doc)
    write_json(layout.gold_manifest(doc["version"]), doc, sort_keys=True)
    bench["state"] = "frozen"
    bench["frozen_version"] = doc["version"]
    _save_bench(layout, bench)
    print(f"froze bench {args.bench_id} at gold version {doc['version']}: "
          f"{doc['manifest_sha256'][:12]}")
    for tier, info in sorted(tiers.items()):
        print(f"  {tier:<3}: " + ", ".join(f"{k} {v}" for k, v in sorted(info.items())
                                           if k not in ("file", "sha256", "ablation_sha256",
                                                        "corpus_sha256")))
    print(f"  h  : {doc['h']['h'] if doc['h'] else 'not calibrated'}")
    return 0


# --------------------------------------------------------------------------
# score and compare
# --------------------------------------------------------------------------

def _score(ws, args) -> int:
    paths = ws.paths
    layout = BenchLayout.of(paths, args.bench_id)
    bench = _bench(layout)
    overrides = {}
    for spec in args.pin:
        key, sep, value = spec.partition("=")
        if not sep or key not in score_mod.VERSION_PINS:
            raise CfError(f"--pin {spec!r}: a pin is one of {', '.join(score_mod.VERSION_PINS)}=<value>")
        overrides[key] = value
    doc = score_mod.score(paths, layout, bench, run_id=args.run, split=args.split, arm=args.arm,
                          gold_version=args.gold_version, gate_log=args.gate_log,
                          transcripts=args.transcripts, canary_report=args.canary_report,
                          no_canary_check=args.no_canary_check, article_costs=args.article_costs,
                          audit_labels=args.audit_labels, replicates=args.bootstrap,
                          seed=args.seed, pin_overrides=overrides, checkpoint=args.checkpoint)
    if args.as_json:
        print(json.dumps({k: v for k, v in doc.items() if k != "per_cluster"}, indent=1, sort_keys=True))
        return 0
    print(f"scored run {args.run} on bench {args.bench_id} {args.split} ({doc['arm']} arm): "
          f"measurement {doc['measurement_id'][:16]}")
    for tier, info in sorted(doc["tiers"].items()):
        m = info["metrics"]
        parts = []
        for name in ("fnf_rate", "overclaim_rate", "recall_at_1", "inventory_recall", "memory_rate"):
            if name in m and m[name]["value"] is not None:
                lo, hi = m[name]["lo"], m[name]["hi"]
                ci = f" [{lo:.3f}, {hi:.3f}]" if lo is not None else ""
                parts.append(f"{name} {m[name]['value']:.3f}{ci}")
        print(f"  {tier:<3}: {info['gold']} gold, {info['joined']} joined; " + "; ".join(parts))
    print(f"  cost : {doc['cost']['value'] if doc['cost']['value'] is not None else '?'} USD/article "
          f"({doc['cost']['basis']})")
    print(f"  misses: {len(doc['misses'])}, audit sample {doc['audit']['sampled']} -> {doc['audit']['file']}")
    print(f"  wrote {layout.score_json(score_mod.score_name(doc['split'], doc['measurement_id']))}")
    return 0


def _compare(ws, args) -> int:
    paths = ws.paths
    layout = BenchLayout.of(paths, args.bench_id)
    _bench(layout)
    entry = compare_mod.compare(layout, args.baseline, args.candidate, claim=args.claim,
                                aa_path=args.aa, ledger=args.ledger, alpha=args.alpha,
                                replication=args.replication, replicates=args.bootstrap,
                                seed=args.seed, note=args.note)
    if args.as_json:
        print(json.dumps(entry, indent=1, sort_keys=True))
        return 0
    verdict = entry["decision"].upper() + ("" if entry["claimed"] else " (recorded, not claimed)")
    print(f"{args.claim}: {verdict} at alpha {entry['alpha_j']:.4g}; ledger entry {entry['entry_id']}")
    for name, cond in sorted(entry["conditions"].items()):
        print(f"  {name:<13} {'ok' if cond.get('ok') else 'FAILS'}")
    if not entry["claimed"]:
        print(f"  {entry['claim_basis']}")
    for note in entry["differences"]:
        print(f"  note: {note}")
    return 0


def run(args) -> int:
    ws = from_args(args)
    command = getattr(args, "bench_command", None)
    handlers = {"build": _build, "anchor": _anchor, "freeze": _freeze, "score": _score,
                "compare": _compare}
    if command not in handlers:
        raise CfError("cf bench needs a subcommand", hint="build, anchor, freeze, score or compare")
    return handlers[command](ws, args)
