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
    cf bench compare <id> <baseline> <candidate> --claim G3.fnf_rate --aa <r> --replication b0:loop-…
    cf bench compare <id> <baseline-score> --aa <replicate> --claim G3.fnf_rate   # the A/A alone

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
                          GOLD_SCHEMA, HOST_SCHEMA, LABEL_FILES, SPANS_SCHEMA, BenchLayout, now,
                          sha_of)
from locate import inputs as locate_inputs
from sources import render_html

NAME = "bench"


class _BenchParser(argparse.ArgumentParser):
    """The parser of a ``cf bench`` subcommand: it takes back an optional positional that
    came after an option.

    Python 3.11's argparse (the floor, CLAUDE.md section 3) matches an optional positional
    (``nargs="?"``) with the first run of positionals, empty when that run is short, so
    ``compare <id> <baseline> --claim X <candidate>`` left ``<candidate>`` unrecognised
    there -- an order that parsed while the candidate was required, and that 3.12 parses
    still.  A subcommand that names its optional positional in ``late_positional`` (a
    ``(dest, type)`` pair) gets one leftover non-option string back as that positional
    while it is unset; any other leftover stays unrecognised, as argparse reports it.
    """

    late_positional: tuple[str, type] | None = None

    def parse_known_args(self, args=None, namespace=None):
        parsed, extras = super().parse_known_args(args, namespace)
        if self.late_positional is not None:
            dest, kind = self.late_positional
            if (getattr(parsed, dest, None) is None and len(extras) == 1
                    and not extras[0].startswith("-")):
                setattr(parsed, dest, kind(extras.pop()))
        return parsed, extras


def register(subparsers) -> None:
    p = subparsers.add_parser(NAME, help="build, label, freeze and score the locate benchmark; "
                                          "compare two runs by the acceptance rule",
                              description=__doc__,
                              formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="bench_command", metavar="<subcommand>", parser_class=_BenchParser)

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
                   help="a gate log (a gate.jsonl, or a directory of them) for every run of the "
                        "score (default: each run's harvested runs/<run>/host/gate.jsonl)")
    s.add_argument("--transcripts", type=Path, default=None,
                   help="the run's transcripts, scanned for the label canary")
    s.add_argument("--canary-report", type=Path, default=None,
                   help="a host-side canary scan ({scanned, hits, canary_sha256}, of this "
                        "bench's host/canaries.txt) instead of --transcripts "
                        "(default: each run's harvested runs/<run>/host/canary.json)")
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
    s.add_argument("--subset", action="append", default=None, metavar="IDS|FILE",
                   help="score only these articles of the split, pre-registered: source ids "
                        "(comma-separated, repeatable) or a file of them (one per line, or a "
                        "JSON list); the score says subset: true and the acceptance rule "
                        "refuses it")
    s.add_argument("--json", action="store_true", dest="as_json")

    c = sub.add_parser("compare", help="the section 9 acceptance rule over two score documents; "
                                        "with --aa and no candidate, the A/A check alone")
    c.add_argument("bench_id")
    c.add_argument("baseline", type=Path)
    c.add_argument("candidate", type=Path, nargs="?", default=None,
                   help="the candidate's score (left out: the A/A check of the baseline and "
                        "--aa alone, which answers aa ok or aa FAILS and writes nothing)")
    c.late_positional = ("candidate", Path)
    c.add_argument("--claim", required=True, help="the pre-registered metric, e.g. G3.fnf_rate")
    c.add_argument("--aa", type=Path, default=None,
                   help="the score of a distinct replicate run of the baseline's configuration")
    c.add_argument("--alpha", type=float, default=compare_mod.DEFAULT_ALPHA,
                   help="the one-sided level, 0 < alpha < 0.5 (default 0.05)")
    c.add_argument("--replication", default=None, metavar="BENCH:ENTRY",
                   help="the accepted claim of another bench (a fresh draw) this comparison "
                        "replicates, by its ledger entry; it spends no alpha")
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


def _articles_of(frame_doc: dict, draw_doc: dict) -> dict[str, dict]:
    """The drawn articles, from the frame, with their inclusion probabilities."""
    frame = {a["source_id"]: a for a in frame_doc["articles"]}
    return {row["source_id"]: {**frame[row["source_id"]], "pi": row["pi"]}
            for row in draw_doc["articles"]}


def _articles(layout: BenchLayout) -> dict[str, dict]:
    return _articles_of(read_json(layout.frame_json), read_json(layout.draw_json))


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


def _universe(paths, bench: dict, requested: str | None):
    """The ``corpus.universe.Universe`` gold is (or will be) labelled at."""
    uid = requested or (bench.get("universe") or {}).get("id")
    if not uid:
        raise CfError("no universe: gold is labelled at a universe's pins", hint="pass --universe <id>")
    return inputs.load_universe(paths, uid)


def _labelled(layout: BenchLayout) -> bool:
    """Does the bench hold anything labelled at its universe (resolved G1 or a label file)?"""
    return layout.g1_resolved.is_file() or any(layout.label_file(t).is_file() for t in LABEL_FILES)


def _stamp_universe(layout: BenchLayout, bench: dict, universe, supersede: bool) -> None:
    """Stamp the universe's id and RECOMPUTED sha; refuse a change under existing gold.

    Gold is labelled at one state of one universe: a different universe is
    refused outright, and a new state of the same one (an index re-ingested and
    rebuilt) is refused once anything is labelled, unless ``--supersede`` starts
    the gold over at it.
    """
    pin = inputs.universe_pin(universe)
    old = bench.get("universe") or {}
    if old.get("id") and old["id"] != pin["id"]:
        raise CfError(f"the bench's gold is labelled at universe {old['id']!r}, not {pin['id']!r}")
    if old.get("sha256") and old["sha256"] != pin["sha256"] and _labelled(layout) and not supersede:
        raise CfError(f"the bench's gold is labelled at universe {pin['id']} as it was "
                      f"({str(old['sha256'])[:12]}…); it is now {pin['sha256'][:12]}…",
                      hint="label at one state: pass --supersede to resolve and label again at "
                           "the new one (then cf bench freeze --supersede)")
    bench["universe"] = pin


def _library(universe, requested: list[str]) -> str:
    """The library the curated lists name: ``--library``, else the universe's Mathlib.

    ``corpus.universe.Universe.mathlib_library`` is the one rule, shared with
    ``cf locate candidates``.
    """
    libs = inputs.libraries(universe)
    if requested:
        for lib in requested:
            if lib not in libs:
                raise CfError(f"library {lib!r} is not in the universe")
        return requested[0]
    found = universe.mathlib_library()
    if found is None:
        raise CfError(f"universe {universe.id} holds no Mathlib library for the curated lists",
                      hint="name it with --library <id>")
    return str(found["id"])


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
            f"{sourceset} --select {listed} --execute, then cf sourceset render {sourceset} "
            f"--source <id> for each -- and build again: the draw depends on the frame and the "
            f"seed alone, so it does not change")


def _same_json(path: Path, doc: dict) -> bool:
    """Is ``path`` the JSON document ``doc`` (as ``write_json`` would write it)?"""
    return path.is_file() and json.loads(path.read_text(encoding="utf-8")) == json.loads(
        json.dumps(doc, sort_keys=True))


def _build(ws, args) -> int:
    paths = ws.paths
    layout = BenchLayout.of(paths, args.bench_id)
    previous = _bench(layout) if layout.bench_yaml.is_file() else None
    if _frozen(layout):
        raise CfError(f"bench {args.bench_id} is frozen; its draw is part of its gold",
                      hint="build a new bench id for a new draw")
    had_gold = layout.gold_dir.is_dir() and any(layout.gold_dir.iterdir())
    manifest = inputs.load_manifest(paths, args.sourceset, args.manifest_version)
    rows = draw_mod.read_frame_list(args.frame)
    curated = curated_mod.read_lists(args.curated_qids)
    frame = draw_mod.build_frame(rows, manifest, curated)
    frame_doc = {"schema": FRAME_SCHEMA, "bench": args.bench_id, "sourceset": manifest.pin(),
                 "frame_file_sha256": inputs.sha256_file(args.frame), **frame}
    drawn = draw_mod.draw(frame, uniform=args.uniform, curated=args.curated, seed=args.seed)
    # the draw reads no layer; the drawn articles' spans do (section 8.5 step 3)
    unrendered = draw_mod.unrendered_drawn(frame, drawn)
    # a standing parity refusal is no fetch or render away (only a corrected delivery
    # settles it): named apart, and unrendered.txt lists only what a fetch and a render settle
    standing = locate_inputs.parity_refusals(paths, args.sourceset, manifest)
    refused = {sid: standing[sid] for sid in unrendered if sid in standing}
    fetchable = [sid for sid in unrendered if sid not in refused]
    if unrendered and args.exclude_unrendered:
        for ids, why in ((fetchable, vocab.LOCATE_SKIP_NOT_RENDERED),
                         (sorted(refused), f"{vocab.LOCATE_SKIP_PARITY_REFUSED} (the layer is "
                                           f"not the producer's)")):
            if ids:
                drawn = draw_mod.exclude(drawn, ids, reason=f"{why} in sourceset "
                                         f"{args.sourceset} version {manifest.version}")
    draw_doc = {"schema": DRAW_SCHEMA, "bench": args.bench_id, **drawn}
    fractions = {"dev": args.dev, "eval": args.eval, "test": args.test}
    older = draw_mod.older_renderer_drawn(frame, drawn, render_html.RENDERER)
    if older:
        # a run is planned only on the current renderer's layers, and a score refuses a run
        # that read another rendering than the bench pins: nothing of this bench would score
        raise CfError(
            f"{len(older)} of the {len(drawn['articles'])} drawn article(s) were rendered by "
            f"another renderer than {render_html.RENDERER}: "
            + ", ".join(f"{sid} ({r})" for sid, r in list(older.items())[:10])
            + (" …" if len(older) > 10 else "")
            + "; no run can be planned on those layers, so no run could be scored on this bench",
            hint=f"a recorded layer never changes: render the sources under "
                 f"{render_html.RENDERER} in a new sourceset seeded from this one's bytes -- "
                 f"`cf sourceset create <new id> --from {args.sourceset}`, then `cf sourceset "
                 f"render <new id>` -- and build the bench on <new id> (the draw depends on the "
                 f"frame and the seed alone, so it does not change)")

    def refuse_a_changed_draw(changed: list[str]) -> None:
        if changed:
            raise CfError(f"bench {args.bench_id} already holds gold labelled for another draw "
                          f"({', '.join(changed)} would change)",
                          hint="a new draw is a new bench: build it under a new id")

    if had_gold:
        # gold is labelled for one frame, draw, span sample and split: every refusal comes
        # before anything is written, so a refused rebuild changes nothing
        refuse_a_changed_draw([name for name, same in (
            ("frame.json", _same_json(layout.frame_json, frame_doc)),
            ("draw.json", _same_json(layout.draw_json, draw_doc)),
            ("the split fractions", (previous or {}).get("fractions") == fractions),
            ("the seed", (previous or {}).get("seed") == args.seed),
            ("the sourceset pin", (previous or {}).get("sourceset") == manifest.pin()))
            if not same])
    if fetchable:
        atomic_write(layout.unrendered_list, "".join(f"{sid}\n" for sid in fetchable))
    elif layout.unrendered_list.is_file():
        layout.unrendered_list.unlink()
    if unrendered and not args.exclude_unrendered:
        steps = [_unrendered_step(args.sourceset, layout.unrendered_list)] if fetchable else []
        if refused:
            steps.append(f"for the {len(refused)} render refused: " + locate_inputs.parity_remedy(
                manifest, then="build the bench on it"))
        raise CfError(
            f"{len(unrendered)} of the {len(drawn['articles'])} drawn article(s) are not rendered "
            f"in sourceset {args.sourceset} version {manifest.version}: "
            + ", ".join(unrendered[:10]) + (" …" if len(unrendered) > 10 else "")
            + (f"; render refused {len(refused)} of them because the layer is not the "
               f"producer's: " + locate_inputs.parity_refused_text(refused) if refused else ""),
            hint="; ".join(steps)
            + "; or pass --exclude-unrendered to bench the rest, each excluded article "
              "recorded in draw.json with its inclusion probabilities")
    articles = _articles_of(frame_doc, draw_doc)
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
    spans_doc = {"schema": SPANS_SCHEMA, "bench": args.bench_id,
                 "per_article": args.spans_per_article, "spans": spans,
                 "drawn_from": "the outermost paragraphs.json blocks with at least four words "
                               "(nested blocks one unit), sectioned by sections.json; no run"}
    if had_gold:
        refuse_a_changed_draw(["spans.json"] if not (layout.spans_json.is_file() and {
            k: v for k, v in read_json(layout.spans_json).items() if k in ("per_article", "spans")
        } == json.loads(json.dumps({"per_article": spans_doc["per_article"],
                                    "spans": spans_doc["spans"]}))) else [])
    write_json(layout.frame_json, frame_doc, sort_keys=True)
    write_json(layout.draw_json, draw_doc, sort_keys=True)
    write_json(layout.spans_json, spans_doc, sort_keys=True)
    # what anchoring recorded survives a same-input rebuild of a labelled bench
    carried = {k: v for k, v in (previous or {}).items() if k in ("library", "gx_articles")}
    bench = {**carried, "schema": BENCH_SCHEMA, "id": args.bench_id,
             "created": (previous or {}).get("created") or now(),
             "sourceset": manifest.pin(), "seed": args.seed,
             "draw": {"uniform": args.uniform, "curated": args.curated},
             "spans_per_article": args.spans_per_article, "fractions": fractions,
             "canary": (previous or {}).get("canary") or gold_mod.new_canary(),
             "universe": (previous or {}).get("universe"),
             "state": ((previous or {}).get("state") or "built") if had_gold else "built"}
    _save_bench(layout, bench)
    clusters = _recluster(paths, layout, bench)
    counts = {s: sum(1 for v in clusters["articles"].values() if v == s) for s in ("dev", "eval", "test")}
    print(f"built bench {args.bench_id}: frame {frame['N']} article(s) ({frame['N_curated']} curated), "
          f"drawn {len(articles)} ({drawn['uniform']['n']} uniform, {drawn['curated']['n']} curated), "
          f"{len(clusters['clusters'])} cluster(s), {len(spans)} G3 span(s)")
    if frame["unrendered"]:
        # a standing parity refusal is not "not yet": no render of this sourceset settles it
        frame_refused = sum(1 for sid in frame["unrendered"] if sid in standing)
        print(f"  frame    : {frame['N_rendered']} rendered, "
              f"{len(frame['unrendered']) - frame_refused} not yet"
              + (f", {frame_refused} render refused for parity (the layer is not the "
                 f"producer's; no render of {args.sourceset} settles it)" if frame_refused else "")
              + " (frame.json unrendered); the draw is the whole frame's, so only drawn ones "
                "must be")
    if drawn.get("excluded"):
        listed = (f"; {layout.unrendered_list} lists {'the rest' if refused else 'them'}"
                  if fetchable else "")
        print(f"  excluded : {len(drawn['excluded'])} drawn article(s) not rendered"
              + (f" ({len(refused)} of them render refused for parity, which no render of "
                 f"{args.sourceset} settles)" if refused else "")
              + f", benched without them (draw.json excluded, each with its inclusion "
                f"probabilities{listed})")
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
    universe = _universe(paths, bench, args.universe)
    # the database must describe the universe as it is now, before anything is stamped
    conn = inputs.open_checked(paths, universe)
    try:
        _stamp_universe(layout, bench, universe, args.supersede)
        _save_bench(layout, bench)
        articles = _articles(layout)
        if resolving:
            library = _library(universe, args.library)
            commit = str(inputs.libraries(universe)[library]["commit"])
            lists = []
            if args.curated_1000:
                lists.append(("mathlib_1000", gold_mod.read_curated_1000(args.curated_1000)))
            if args.curated_100:
                lists.append(("mathlib_100", gold_mod.read_curated_100(args.curated_100)))
            resolved = gold_mod.resolve_g1(list(articles.values()), conn, library=library,
                                           commit=commit, lists=lists,
                                           renames=gold_mod.read_rename_table(args.rename_table),
                                           tag_libraries=args.library or [library])
            write_json(layout.g1_resolved, {"schema": G1_RESOLVED_SCHEMA, "bench": args.bench_id,
                                            "universe": bench["universe"], "library": library,
                                            "commit": commit, **resolved}, sort_keys=True)
            # the G1 labels follow the resolution: a label of an entry no longer resolved goes
            if layout.label_file("G1").is_file():
                keep = {e["entry_id"] for e in resolved["entries"]}
                rows = [{k: v for k, v in r.items() if k not in ("canary", "tier")}
                        for r in gold_mod.read_labels(layout, "G1") if r["gold_id"] in keep]
                gold_mod.write_labels(layout, "G1", rows, bench["canary"], after_freeze=args.supersede)
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

def _check_g1(layout: BenchLayout) -> None:
    """The frozen G1 tier, the resolution and the clusters describe the same entries."""
    resolved = ({e["entry_id"] for e in read_json(layout.g1_resolved).get("entries") or []}
                if layout.g1_resolved.is_file() else set())
    labelled = {r["gold_id"] for r in gold_mod.read_labels(layout, "G1", required=False)}
    stale, unlabelled = sorted(labelled - resolved), sorted(resolved - labelled)
    if stale or unlabelled:
        raise CfError(f"G1 does not match its resolution: {len(stale)} label(s) of entries no longer "
                      f"resolved, {len(unlabelled)} resolved entr(ies) with no label"
                      + (f" ({', '.join((stale + unlabelled)[:4])})" if stale or unlabelled else ""),
                      hint=f"cf bench anchor {layout.bench_id} --emit-args anchor, run the labeler, "
                           "then --transcribe its result")


def _check_calibration(layout: BenchLayout) -> dict | None:
    """h is over every anchored G1 passage, from one labeler run of them all, or not at all."""
    if not layout.calibration_json.is_file():
        return None
    calibration = read_json(layout.calibration_json)
    anchored = {r["gold_id"] for r in gold_mod.read_labels(layout, "G1", required=False)
                if r.get("level") == "passage"}
    rows = {r["gold_id"] for r in calibration.get("rows") or []}
    if rows != anchored or calibration.get("h") is None:
        raise CfError(f"h is not over the anchored G1 passages: calibration holds {len(rows)} of "
                      f"{len(anchored)}" + (f", missing {', '.join(sorted(anchored - rows)[:4])}"
                                             if anchored - rows else ""),
                      hint=f"cf bench anchor {layout.bench_id} --emit-args calibrate, run the "
                           "labeler on every task, then --transcribe")
    return calibration


def _row_version(mode: str, rows: list[dict], sha: str, last: dict | None) -> str:
    """The labeler version one mode's rows were labelled by.

    A row transcribed from locate-label/5 on names it (``labeler_version``, the
    version its args were emitted for).  Rows from before the field name none:
    they take this checkout's version only when they came from this checkout's
    labeler (the workflow's sha), the last frozen manifest's when that manifest
    froze the same labeler for the mode (so re-freezing unchanged gold changes
    nothing), and are refused otherwise -- gold labelled before a policy change
    is relabelled, never frozen as if labelled after it.
    """
    raw = [r.get("labeler_version") for r in rows]
    if any(v is not None and not isinstance(v, str) for v in raw):
        raise CfError(f"the {mode} labels name a labeler version that is no version string",
                      hint="label rows are the transcriber's: re-run that mode on every task "
                           "and transcribe the full result")
    named = set(raw)
    if named == {None}:
        if sha == gold_mod.workflow_sha256():
            return gold_mod.LABELER_VERSION
        frozen = (last or {}).get("labeler") or {}
        if (frozen.get("workflow_sha256") or {}).get(mode) == sha and frozen.get("version"):
            return str(frozen["version"])
        raise CfError(f"the {mode} labels name no labeler version, and their labeler "
                      f"({str(sha)[:12]}…) is neither this checkout's nor one a frozen manifest "
                      f"records: they were labelled before {gold_mod.LABELER_VERSION}",
                      hint=f"a policy change relabels the gold: re-emit the {mode} args, run the "
                           f"labeler on every task and transcribe the full result")
    if None in named or len(named) != 1:
        raise CfError(f"the {mode} labels name {len(named - {None})} labeler version(s)"
                      + (" and some name none" if None in named else ""),
                      hint="one tier, one labeler: re-run that mode on every task and "
                           "transcribe the full result")
    return named.pop()


def _labeler(layout: BenchLayout, calibration: dict | None, last: dict | None = None) -> dict:
    """The labeler of each mode, from the rows it wrote (never from args on disk).

    Its version too (:func:`_row_version`): every mode's rows name one, and the
    gold's version is the one they share -- a tier labelled under another version
    than the rest is refused, since the score holds the gold to the judgment rules
    its version labels by (references/locate-bench.md sections 7 and 8).
    """
    by_mode = {"anchor": gold_mod.read_labels(layout, "G1", required=False),
               "label": gold_mod.read_labels(layout, "G3", required=False),
               "inventory": gold_mod.read_labels(layout, "GX", required=False),
               "calibrate": (calibration or {}).get("rows") or []}
    labeler = {"version": gold_mod.LABELER_VERSION, "skill_commit": {}, "workflow_sha256": {},
               "prompt_sha256": {}}
    versions: dict[str, str] = {}
    for mode, rows in sorted(by_mode.items()):
        if not rows:
            continue
        shas = {r.get("labeler_sha256") for r in rows}
        if None in shas or len(shas) != 1:
            raise CfError(f"the {mode} labels came from {len(shas - {None})} labeler(s)"
                          + (" and some carry none" if None in shas else ""),
                          hint="one tier, one labeler: re-run that mode on every task and "
                               "transcribe the full result")
        sha = shas.pop()
        versions[mode] = _row_version(mode, rows, sha, last)
        labeler["workflow_sha256"][mode] = sha
        # the prompts are inline in the workflow, so its sha is theirs
        labeler["prompt_sha256"][mode] = sha
        labeler["skill_commit"][mode] = ",".join(sorted({str(r.get("labeler_commit") or
                                                             score_mod.UNRECORDED) for r in rows}))
    if len(set(versions.values())) > 1:
        raise CfError("the gold's tiers were labelled by "
                      + ", ".join(f"{mode} {v}" for mode, v in sorted(versions.items()))
                      + ": one gold, one labeler version",
                      hint=f"a policy change relabels every tier: re-run the labeler "
                           f"({gold_mod.LABELER_VERSION}) on each mode, transcribe, then freeze")
    if versions:
        labeler["version"] = next(iter(versions.values()))
    return labeler


def _p_tier(ablation_path: Path, planted_db: Path, ablation: dict) -> dict:
    return {"ablation_sha256": inputs.sha256_file(ablation_path),
            "corpus_sha256": inputs.sha256_file(planted_db),
            "removed_declarations": len(ablation["removed"]), "modules": len(ablation["modules"])}


def _freeze(ws, args) -> int:
    paths = ws.paths
    layout = BenchLayout.of(paths, args.bench_id)
    bench = _bench(layout)
    if not bench.get("universe"):
        raise CfError("gold is labelled at a universe; the bench has none yet",
                      hint=f"cf bench anchor {args.bench_id} --universe <id> ...")
    gold_mod.check_canaries(layout, bench["canary"])
    _check_g1(layout)
    calibration = _check_calibration(layout)
    manifests = layout.gold_manifests()
    last_version, last = gold_mod.latest_manifest(layout) if manifests else (0, None)
    labeler = _labeler(layout, calibration, last)
    clusters = _recluster(paths, layout, bench)
    articles_split = clusters["articles"]
    tiers = {}
    for tier in vocab.BENCH_LABELLED_TIERS:
        info = gold_mod.tier_summary(layout, tier)
        if info:
            tiers[tier] = info
    if "GX" in tiers and not tiers["GX"]["signed_by"]:
        raise CfError("the GX inventories carry no maintainer signature",
                      hint="transcribe them with --signed-by <name>")
    dev_decls: set[str] = set()
    rows_by_tier = {t: gold_mod.read_labels(layout, t, required=False)
                    for t in vocab.BENCH_LABELLED_TIERS}

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
    staging = layout.planted_dir.with_name("planted.staging")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        if args.plant:
            eval_g1 = [r for r in rows_by_tier["G1"] if articles_split.get(r["source_id"]) == "eval"]
            universe = inputs.load_universe(paths, bench["universe"]["id"])
            if universe.sha256() != bench["universe"]["sha256"]:
                raise CfError(f"gold is labelled at universe {universe.id} as it was "
                              f"({str(bench['universe']['sha256'])[:12]}…), which is now "
                              f"{universe.sha256()[:12]}…: its planted index would be of another state",
                              hint="re-anchor with --supersede at the new state, or plant at the old one")
            # planted into a staging directory, moved into gold/planted/ only with its manifest
            ablation = gold_mod.plant(layout, paths, universe, eval_g1, staging)
            tiers["P"] = _p_tier(staging / layout.ablation_json.name,
                                 staging / layout.planted_db.name, ablation)
        elif last is not None and (last.get("tiers") or {}).get("P"):
            # the frozen planted tier carries over, as frozen (verify_frozen checks its files)
            gold_mod.verify_planted(layout, last["tiers"]["P"])
            tiers["P"] = dict(last["tiers"]["P"])
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
            "h": ({"found": calibration["found"], "total": calibration["total"],
                   "h": calibration["h"], "tag_blind": True} if calibration else None),
            "labeler": labeler,
            "canary_sha256": sha_of(bench["canary"]),
        }
        if last is not None:
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
        if args.plant:
            shutil.rmtree(layout.planted_dir, ignore_errors=True)
            staging.replace(layout.planted_dir)
        write_json(layout.gold_manifest(doc["version"]), doc, sort_keys=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
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

def subset_ids(values: list[str] | None) -> list[str] | None:
    """``--subset`` values -> source ids: each an existing file of ids, or comma-separated ids."""
    if values is None:
        return None
    out: list[str] = []
    for value in values:
        path = Path(value).expanduser()
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if isinstance(data, list):
                out.extend(str(x) for x in data)
                continue
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                out.extend(part for part in line.replace(",", " ").split() if part)
            continue
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


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
                          no_canary_check=args.no_canary_check,
                          article_costs_path=args.article_costs,
                          audit_labels=args.audit_labels, replicates=args.bootstrap,
                          seed=args.seed, pin_overrides=overrides, checkpoint=args.checkpoint,
                          subset=subset_ids(getattr(args, "subset", None)))
    if args.as_json:
        print(json.dumps({k: v for k, v in doc.items() if k != "per_cluster"}, indent=1, sort_keys=True))
        return 0
    print(f"scored run {args.run} on bench {args.bench_id} {args.split} ({doc['arm']} arm): "
          f"measurement {doc['measurement_id'][:16]}")
    if doc.get("subset"):
        print(f"  SUBSET: {len(doc['subset_articles'])} pre-registered article(s) of the split "
              f"(sha {doc['subset_sha256'][:12]}); not for the acceptance rule")
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
    print(f"  wrote {layout.score_json(doc['score_id'])}")
    return 0


def _agreement_lines(agree: dict) -> list[str]:
    """The A/A's agreement as ``cf bench compare --aa`` prints it: what it showed, then
    every row it holds with its verdict and interval (vocab.BENCH_AA_VERDICTS).

    No equivalence test: ``ok`` says no row was SHOWN to differ past its tolerance, and
    the second line says for how many rows agreement within it was shown and how large
    a difference the intervals leave possible, so a small A/A cannot read as a tight one.
    """
    tol, ctol, level = agree["tolerance"], agree["cost_tolerance"], agree["level"]
    if agree["ok"]:
        head = (f"no metric shown to differ by more than {tol:g}, nor the cost per article "
                f"by more than {ctol:.0%}")
    else:
        head = (f"shown to differ: {', '.join(agree['disagree'])} (a metric past {tol:g}, "
                f"the cost per article past {ctol:.0%})")
    lines = [f"  agreement     {'ok   ' if agree['ok'] else 'FAILS'} {head} "
             f"({level:.0%} paired intervals, {agree['compared']} compared)"]
    reach = agree.get("reach")
    lines.append(f"                      agreement within the tolerance shown for "
                 f"{len(agree['within'])} of {agree['compared']}"
                 + (f", unresolved for {len(agree['unresolved'])}" if agree["unresolved"] else "")
                 + ("" if reach is None else
                    f"; the intervals leave a difference of up to {reach:.3g} possible"))

    def num(value, *, sign=False, share=False) -> str:
        if value is None:
            return "?"
        return f"{value:+.1%}" if share else (f"{value:+.4g}" if sign else f"{value:.4g}")

    rows = [(name, row, False) for name, row in agree["metrics"].items()]
    rows.append((f"cost ({agree['cost'].get('cells')})", agree["cost"], True))

    def rank(row: dict) -> int:
        """What failed first, then what is unresolved, what agrees, and what was not compared."""
        if not row["ok"]:
            return 0
        return {vocab.BENCH_AA_UNRESOLVED: 1, vocab.BENCH_AA_WITHIN: 2}.get(row.get("verdict"), 3)

    width = max(len(name) for name, _row, _cost in rows)
    for name, row, cost in sorted(rows, key=lambda r: (rank(r[1]), r[0])):
        verdict = row.get("verdict") or ("FAILS" if not row["ok"] else "not compared")
        relative = cost and "relative_lo" in row
        lo = row.get("relative_lo" if relative else "lo")
        hi = row.get("relative_hi" if relative else "hi")
        diff = row.get("relative_difference" if relative else "difference")
        said = (f"{num(row.get('baseline'))} -> {num(row.get('replicate'))}"
                + ("" if diff is None else f", difference {num(diff, sign=True, share=relative)}")
                + ("" if lo is None else f" in [{num(lo, sign=True, share=relative)}, "
                                         f"{num(hi, sign=True, share=relative)}]"))
        why = f" ({row['why']})" if row.get("why") else ""
        lines.append(f"    {verdict:<12} {name:<{width}}  {said}{why}")
    return lines


def _aa_only(layout: BenchLayout, args) -> int:
    """``cf bench compare <id> <baseline> --aa <replicate>``: rule 5's condition alone."""
    if args.aa is None:
        raise CfError("cf bench compare needs a candidate score, or --aa for the A/A check alone",
                      hint="cf bench compare <id> <baseline> <candidate> --claim <tier.metric> "
                           "--aa <replicate>, or cf bench compare <id> <baseline> --aa "
                           "<replicate> --claim <tier.metric>")
    if args.replication:
        raise CfError("--replication names the claim a comparison replicates; the A/A check "
                      "claims nothing", hint="give the candidate's score, or leave out --replication")
    doc = compare_mod.aa_check(layout, args.baseline, args.aa, claim=args.claim, alpha=args.alpha,
                               replicates=args.bootstrap, seed=args.seed)
    if args.as_json:
        print(json.dumps(doc, indent=1, sort_keys=True))
        return 0 if doc["ok"] else 1
    cond = doc["aa"]
    agree = cond["agreement"]
    print(f"{doc['answer']}: replicate {doc['replicate'][:16]} of baseline "
          f"{doc['baseline'][:16]} ({doc['claim']}, alpha {doc['alpha']:.4g}; nothing written)")
    print(f"  configuration ok    {', '.join(doc['configuration']['compared'])}: the same")
    lower = cond["claim_improvement_lower"]
    print(f"  rule          {'FAILS' if cond['accepted'] else 'ok   '} the acceptance rule "
          f"{'accepts' if cond['accepted'] else 'does not accept'} the replicate (claim lower "
          f"bound {'?' if lower is None else f'{lower:.4g}'})")
    for line in _agreement_lines(agree):
        print(line)
    seeds = doc["recorded"]["challenge_seed"]
    said = {side: ", ".join(f"{run} {seed}" for run, seed in seeds[side].items()) or "none"
            for side in ("baseline", "replicate")}
    print(f"  recorded      challenge seeds: {said['baseline']} (baseline), {said['replicate']} "
          f"(replicate); compared by nothing")
    for note in doc["differences"]:
        print(f"  note: {note}")
    return 0 if doc["ok"] else 1


def _compare(ws, args) -> int:
    paths = ws.paths
    layout = BenchLayout.of(paths, args.bench_id)
    _bench(layout)
    if args.candidate is None:
        return _aa_only(layout, args)
    replication = None
    if args.replication:
        other, sep, entry_id = args.replication.partition(":")
        if not sep or not other or not entry_id:
            raise CfError(f"--replication {args.replication!r} is not BENCH:ENTRY",
                          hint="name the replicated claim as <bench id>:<ledger entry id>")
        replication = compare_mod.replicated_entry(BenchLayout.of(paths, other), entry_id)
    entry = compare_mod.compare(layout, args.baseline, args.candidate, claim=args.claim,
                                aa_path=args.aa, alpha=args.alpha, replication=replication,
                                replicates=args.bootstrap, seed=args.seed, note=args.note)
    if args.as_json:
        print(json.dumps(entry, indent=1, sort_keys=True))
        return 0
    verdict = entry["decision"].upper() + ("" if entry["claimed"] else " (recorded, not claimed)")
    print(f"{args.claim}: {verdict} at alpha {entry['alpha_j']:.4g}; ledger entry {entry['entry_id']}")
    for name, cond in sorted(entry["conditions"].items()):
        print(f"  {name:<13} {'ok' if cond.get('ok') else 'FAILS'}")
    disagree = ((entry["conditions"].get("aa") or {}).get("agreement") or {}).get("disagree")
    if disagree:
        print(f"  aa: the replicate disagrees with the baseline on {', '.join(disagree)}")
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
