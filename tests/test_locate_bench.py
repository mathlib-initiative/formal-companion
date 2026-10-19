"""The locate benchmark: draws, splits, gold, the labeler, metrics, scores and the acceptance rule.

New code, for docs/proposals/wikipedia-locate.md sections 8 and 9 as
references/locate-bench.md fixes them.  Every input is synthetic and built by
its owner where one exists (tests/_locate_bench_fixtures.py): the sourceset by
``sources.sourceset`` and ``sources.render_html``, the universe and its
``corpus.sqlite`` by ``corpus.universe`` and ``corpus.build_db``; the harvested
runs in the documented formats.  The metric tests use hand-computed
expectations on a handful of passages; the refusals are each exercised; the
labeler Workflow is executed under node with stubbed agents, its result
transcribed back -- the round trip a session makes; and the integration (wave 2)
seams each have a regression test named for the item it fixes.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
for _p in (str(SCRIPTS), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import _locate_bench_fixtures as fx  # noqa: E402
except ImportError:  # pragma: no cover
    from tests import _locate_bench_fixtures as fx  # noqa: E402

import cf as cf_main  # noqa: E402
from bench import compare as compare_mod  # noqa: E402
from bench import curated as curated_mod  # noqa: E402
from bench import draw as draw_mod  # noqa: E402
from bench import gold as gold_mod  # noqa: E402
from bench import inputs  # noqa: E402
from bench import metrics as m  # noqa: E402
from bench import score as score_mod  # noqa: E402
from bench.layout import BenchLayout, overlap_words  # noqa: E402
from cflib import vocab, yamlio  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from corpus import query  # noqa: E402

NODE = shutil.which("node")


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def cf(world: fx.World, *argv) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cf_main.main(["--workspace", str(world.root), *[str(a) for a in argv]])
    if rc != 0:
        raise AssertionError(f"cf {' '.join(map(str, argv))} exited {rc}: {out.getvalue()}")
    return out.getvalue()


def layout_of(world: fx.World, bench: str = "b1") -> BenchLayout:
    return BenchLayout.of(WorkspacePaths(world.root), bench)


def fake_result(args: dict, labels: list[dict]) -> dict:
    return {"schema": "cf-locate-label-v1", "mode": args["mode"], "bench": args["bench"],
            "args_digest": args["digest"], "labeler": {"version": gold_mod.LABELER_VERSION},
            "labels": labels}


def built(case, *, uniform=6, curated=3, spans=2, seed=7) -> fx.World:
    world = fx.World(scratch(case) / "ws")
    cf(world, "bench", "build", "b1", "--sourceset", fx.SOURCESET, "--frame", world.frame_file(),
       "--curated-qids", world.curated_1000(), "--uniform", uniform, "--curated", curated,
       "--spans-per-article", spans, "--seed", seed)
    return world


def anchored(case, **kw) -> fx.World:
    world = built(case, **kw)
    cf(world, "bench", "anchor", "b1", "--universe", fx.UNIVERSE, "--curated-1000",
       world.curated_1000(), "--curated-100", world.curated_100(), "--rename-table", world.renames())
    return world


def transcribe(world: fx.World, mode: str, make_label, *extra) -> str:
    cf(world, "bench", "anchor", "b1", "--emit-args", mode)
    args = json.loads((layout_of(world).label_args(mode)).read_text())
    labels = [make_label(t) for t in args["tasks"]]
    path = world.root / f"result-{mode}.json"
    path.write_text(json.dumps(fake_result(args, [l for l in labels if l])))
    return cf(world, "bench", "anchor", "b1", "--transcribe", path, *extra)


def index_of(source_id: str) -> int:
    return int(source_id.split("-")[1]) - 1001


def frozen(case, *, label_decl="Widget.conj_three", plant=False) -> fx.World:
    world = anchored(case)
    transcribe(world, "anchor", lambda t: {"task_id": t["task_id"],
                                           "quote": world.quote(index_of(t["source_id"]), 0, 6),
                                           "relation": "exactMatch", "passage_kind": "theorem",
                                           "evidence": "read at the pin"})
    transcribe(world, "label", lambda t: {
        "task_id": t["task_id"], "scope": "formal_math", "passage_kind": "theorem",
        "declarations": [{"library": "mathlib", "declaration": label_decl,
                          "relation": "exactMatch"}], "evidence": "e", "hunters": []})
    cf(world, "bench", "freeze", "b1", *(["--plant"] if plant else []))
    return world


def manifest_of(world: fx.World) -> dict:
    return gold_mod.latest_manifest(layout_of(world))[1]


def eval_run(world: fx.World, run_id="loc1", inventory_run="inv1", *, formalize_second=False,
             **kw) -> list[str]:
    """A run over every eval article: paragraph 0 located at spanning_zero, paragraph 1 not."""
    ev = manifest_of(world)["splits"]["eval"]
    passages, outs = {}, []
    for sid in ev:
        i = index_of(sid)
        a, b = f"In-text #{i:08x}", f"In-text #{i + 16:08x}"
        passages[sid] = [fx.passage(world, i, 0, a), fx.passage(world, i, 1, b)]
        outs.append(fx.outcome(world, i, a, status="formalized", outcome_="located",
                               locations=[("mathlib", "Widget.spanning_zero", "exactMatch")],
                               run_id=run_id, inventory_run=inventory_run, k=0))
        if formalize_second:
            outs.append(fx.outcome(world, i, b, status="formalized", outcome_="located",
                                   locations=[("mathlib", "Widget.conj_three", "exactMatch")],
                                   run_id=run_id, inventory_run=inventory_run, k=1))
        else:
            outs.append(fx.outcome(world, i, b, status=None, outcome_="unresolved",
                                   run_id=run_id, inventory_run=inventory_run, k=1))
    world.run(run_id, inventory_run=inventory_run, passages=passages, outcomes=outs, **kw)
    return ev


def host_scan(world: fx.World, scanned: int, hits=(), bench: str = "b1") -> dict:
    """A host canary scan (``scripts/run/canary_scan.py``) for the bench's own canaries file."""
    from run import canary_scan

    canaries = canary_scan.read_canaries(layout_of(world, bench).host_canaries)
    return {**canary_scan.scan(canaries, []), "scanned": scanned, "hits": sorted(hits)}


def score(world: fx.World, run_id="loc1", *extra) -> dict:
    cf(world, "bench", "score", "b1", "--run", run_id, "--split", "eval", "--bootstrap", 100,
       *(extra or ("--no-canary-check",)))
    newest = max((layout_of(world).scores_dir).glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    return json.loads(newest.read_text())


# ---------------------------------------------------------------------------
# the frame, the draws, clusters, splits and spans
# ---------------------------------------------------------------------------

class TestTheFrameAndTheDraws(unittest.TestCase):

    def setUp(self):
        self.world = fx.World(scratch(self) / "ws", articles=6)
        self.manifest = inputs.load_manifest(WorkspacePaths(self.world.root), fx.SOURCESET)

    def frame(self, rows=None, curated=frozenset({"Q99999000", "Q99999001"})):
        rows = rows if rows is not None else [{"pageid": 1001 + i} for i in range(6)]
        return draw_mod.build_frame(rows, self.manifest, set(curated))

    def test_the_frame_resolves_page_ids_and_marks_the_curated(self):
        frame = self.frame()
        self.assertEqual(frame["N"], 6)
        self.assertEqual(frame["N_curated"], 3)          # article 5 shares article 0's QID
        self.assertTrue(all(a["layer_sha256"] for a in frame["articles"]))

    def test_an_article_the_manifest_lacks_is_refused_by_name_and_an_unrendered_one_listed(self):
        with self.assertRaises(CfError) as ctx:
            self.frame([{"pageid": 1001}, {"pageid": 4242}])
        self.assertIn("pageid 4242", str(ctx.exception))
        self.manifest.entries[self.world.sid(1)]["layer_sha256"] = None
        frame = self.frame([{"pageid": 1001}, {"pageid": 1002}])
        self.assertEqual(frame["N"], 2)
        self.assertEqual((frame["N_rendered"], frame["unrendered"]), (1, [self.world.sid(1)]))
        self.assertFalse(next(a for a in frame["articles"]
                              if a["source_id"] == self.world.sid(1))["rendered"])

    def test_the_draws_are_a_function_of_the_seed_with_their_probabilities(self):
        frame = self.frame()
        one = draw_mod.draw(frame, uniform=3, curated=2, seed=11)
        self.assertEqual(one, draw_mod.draw(frame, uniform=3, curated=2, seed=11))
        seeds = {tuple(draw_mod.draw(frame, uniform=3, curated=2, seed=s)["uniform"]["drawn"])
                 for s in range(20)}
        self.assertGreater(len(seeds), 1)
        for row in one["articles"]:
            self.assertAlmostEqual(row["pi_uniform"], 3 / 6)
            want_c = 2 / 3 if frame_curated(frame, row["source_id"]) else 0.0
            self.assertAlmostEqual(row["pi_curated"], want_c)
            self.assertAlmostEqual(row["pi"], 1 - (1 - 0.5) * (1 - want_c))
        short = draw_mod.draw(frame, uniform=10, curated=10, seed=1)
        self.assertTrue(short["uniform"]["short"])
        self.assertEqual(len(short["uniform"]["drawn"]), 6)
        self.assertTrue(all(r["pi_uniform"] == 1.0 for r in short["articles"]))

    def test_the_curated_qids_read_a_mapping_or_a_list(self):
        self.assertIn("Q99999000", curated_mod.read_lists([self.world.curated_1000()]).qids)
        listed = self.world.write("inputs/qids.txt", "# curated\nQ1\nQ22\nnot-a-qid\n")
        lists = curated_mod.read_lists([listed])
        self.assertEqual(lists.qids, {"Q1", "Q22"})
        self.assertEqual(lists.files[0]["rejected"], ["not-a-qid"])   # reported, never read
        frame = self.world.write("inputs/frame.json", json.dumps([1001, {"source_id": "x"}]))
        self.assertEqual(draw_mod.read_frame_list(frame), [{"pageid": 1001}, {"source_id": "x"}])


def frame_curated(frame: dict, sid: str) -> bool:
    return next(a for a in frame["articles"] if a["source_id"] == sid)["curated"]


#: Mathlib's 1000.yaml shape, reduced: a suffixed key beside its base (Q99999003X), two
#: suffixed keys whose base is no key (Q99999004A/B), and an entry with no declaration
CURATED_1000 = """# synthetic, in the shape of $MATHLIB/docs/1000.yaml
Q99999001:
  title: Widget lemma 1
  decls:
    - Widget.mul_spanning
    - Widget.shared_name
  authors: A. Author

Q99999003:
  title: Widget lemma 3

Q99999003X:
  title: Widget lemma 3, the strong form
  decl: Widget.conj_three

Q99999004A:
  title: Widget lemma 4, first half
  decl: Widget.spanning_zero

Q99999004B:
  title: Widget lemma 4, second half
"""

#: Mathlib's 100.yaml shape, reduced: numbered keys, padded colons, links whose
#: values hold colons, curly quotes, no QID anywhere
CURATED_100 = """# synthetic, in the shape of $MATHLIB/docs/100.yaml
#
# See yaml_check.py for documentation about the allowed fields in this file.

1:
  title  : The Law of Widgets
  decls  :
    - Widget.shared_name
  authors: A. Author (first) and B. Author (second)
2:
  title  : The Widget Lemma 2
  decl   : Widget.tagged_two
  authors: C. Author
3:
  title  : Gadget’s Rule – Widget Form
  authors: D. Author
  links  :
    result : https://example.org/gadget/blob/0000000/Gadget.lean#L1-L9
    website: https://example.org/gadget
4:
  title  : A Theorem Nobody Benched
  decl   : Widget.spanning_one
  note   : "listed, with no article in the frame"
"""


class TestCuratedLists(unittest.TestCase):
    """Mathlib's two lists as they really are, reduced to synthetic entries.

    The field case: a suffixed 1000.yaml key was dropped with the QIDs only it
    names, and 100.yaml -- keyed by number, no QID -- was read as zero QIDs
    without a word, so the curated stratum lost every article only 100.yaml names.
    """

    def setUp(self):
        self.world = fx.World(scratch(self) / "ws", articles=6)
        self.world.change_entry(self.world.sid(3), title="Gadget's rule - widget form")
        self.p1000 = self.world.write("inputs/1000.yaml", CURATED_1000)
        self.p100 = self.world.write("inputs/100.yaml", CURATED_100)
        self.manifest = inputs.load_manifest(WorkspacePaths(self.world.root), fx.SOURCESET)

    def test_a_suffixed_key_names_its_base_qid(self):
        lists = curated_mod.read_lists([self.p1000])
        self.assertEqual(lists.qids, {"Q99999001", "Q99999003", "Q99999004"})
        keys = {e.key: e.qids for e in lists.entries}
        self.assertEqual(keys["Q99999004A"], ["Q99999004"])
        self.assertEqual(keys["Q99999003X"], ["Q99999003"])
        bad = self.world.write("inputs/bad.yaml", "Q1:\n  title: a\nWidget:\n  title: b\n")
        with self.assertRaises(CfError) as ctx:
            curated_mod.read_lists([bad])
        self.assertIn("'Widget'", str(ctx.exception))

    def test_100_yaml_resolves_through_1000_yaml_and_matches_by_title(self):
        for pyyaml in (True, False):
            with self.subTest(pyyaml=pyyaml), mock.patch.object(
                    yamlio, "_pyyaml", yamlio._pyyaml if pyyaml else None):
                lists = curated_mod.read_lists([self.p1000, self.p100])
                by_key = {e.key: e for e in lists.of_list(curated_mod.LIST_100)}
                self.assertEqual(sorted(by_key), ["1", "2", "3", "4"])
                self.assertEqual(by_key["1"].qids, ["Q99999001"])        # a shared declaration
                self.assertEqual(by_key["1"].method, "a declaration shared with 1000.yaml")
                self.assertEqual(by_key["2"].qids, [])                   # by title only
                record = next(f for f in lists.files if f["kind"] == curated_mod.LIST_100)
                self.assertEqual((record["entries"], record["title_only"]), (4, 3))
                frame = draw_mod.build_frame([{"pageid": 1001 + i} for i in range(6)],
                                             self.manifest, lists)
                via = {a["source_id"]: a["curated_via"] for a in frame["articles"]}
                w = self.world
                self.assertEqual(via[w.sid(1)], ["mathlib_1000 Q99999001", "mathlib_100 #1"])
                self.assertEqual(via[w.sid(2)], ["mathlib_100 #2"])          # "The Widget Lemma 2"
                self.assertIn("mathlib_100 #3", via[w.sid(3)])               # curly quote, en dash
                self.assertEqual(via[w.sid(4)], ["mathlib_1000 Q99999004A",
                                                 "mathlib_1000 Q99999004B"])
                self.assertEqual(via[w.sid(0)], [])
                self.assertEqual([u["key"] for u in frame["curated_lists"]["unmatched_100"]], ["4"])
                self.assertEqual(frame["N_curated"], 4)

    def test_100_yaml_alone_is_never_zero_qids_without_a_word(self):
        lists = curated_mod.read_lists([self.p100])
        frame = draw_mod.build_frame([{"pageid": 1001 + i} for i in range(6)], self.manifest, lists)
        self.assertEqual({a["source_id"] for a in frame["articles"] if a["curated"]},
                         {self.world.sid(2), self.world.sid(3)})
        self.assertEqual([u["key"] for u in frame["curated_lists"]["unmatched_100"]], ["1", "4"])
        out = cf(self.world, "bench", "build", "b1", "--sourceset", fx.SOURCESET,
                 "--frame", self.world.frame_file(), "--curated-qids", self.p100,
                 "--uniform", 2, "--curated", 2, "--seed", 3)
        self.assertIn("2 entr(ies) name no frame article", out)
        self.assertIn("4 matched by title only", out)      # nothing to resolve through
        doc = json.loads(layout_of(self.world).frame_json.read_text())
        self.assertEqual(doc["curated_lists"]["files"][0]["kind"], "mathlib_100")

    def test_the_anchor_reads_the_real_shapes_through_l2s_join(self):
        """G1 reads the same two files as the stratum, through L2's own join (review #75).

        The field case was a crash on a suffixed key and a 100.yaml read as nothing.
        The anchor now joins as ``cf locate candidates`` does, so G1 and L2 agree: a
        100.yaml entry by its QID or by L2's title key.  The stratum's reader is
        wider (100.yaml #1 reaches its article through a declaration it shares with
        1000.yaml; #3's en dash folds), so a stratum article may carry no G1 entry.
        """
        cf(self.world, "bench", "build", "b1", "--sourceset", fx.SOURCESET,
           "--frame", self.world.frame_file(), "--curated-qids", self.p1000,
           "--curated-qids", self.p100, "--uniform", 6, "--curated", 0, "--seed", 3)
        cf(self.world, "bench", "anchor", "b1", "--universe", fx.UNIVERSE,
           "--curated-1000", self.p1000, "--curated-100", self.p100)
        doc = json.loads(layout_of(self.world).g1_resolved.read_text())
        evidence = {e["source_id"]: e["evidence"] for e in doc["entries"]}
        w = self.world
        self.assertIn("mathlib_1000 Q99999003X", evidence[w.sid(3)])   # no crash on the suffix
        self.assertIn("mathlib_1000 Q99999004A", evidence[w.sid(4)])
        self.assertIn("mathlib_100 2", evidence[w.sid(2)])            # "The Widget Lemma 2"
        self.assertEqual(doc["unmatched"], {"mathlib_100": 2})        # #1 and #3, as in L2
        self.assertEqual(doc["listed_without_declarations"], {"mathlib_1000": 2, "mathlib_100": 1})
        frame = json.loads(layout_of(self.world).frame_json.read_text())
        via = {a["source_id"]: a["curated_via"] for a in frame["articles"]}
        self.assertIn("mathlib_100 #1", via[w.sid(1)])               # the stratum's wider reader
        self.assertNotIn("mathlib_100 1", evidence[w.sid(1)])

    def test_the_list_names_are_the_vocabularys(self):
        self.assertLessEqual({curated_mod.LIST_1000, curated_mod.LIST_100}, vocab.CANDIDATE_VIA)


class TestAPartlyRenderedSourceset(unittest.TestCase):
    """The bench builds from a sourceset rendered only where the draw landed.

    The field case, reduced: a frame of every Tier A article, of which only the
    drawn ones (and a few more) were fetched and rendered.  The build refused
    every unrendered frame article, so it needed the whole tier rendered first.
    """

    def setUp(self):
        self.world = fx.World(scratch(self) / "ws", articles=6)
        manifest = inputs.load_manifest(WorkspacePaths(self.world.root), fx.SOURCESET)
        frame = draw_mod.build_frame([{"pageid": 1001 + i} for i in range(6)], manifest,
                                     curated_mod.read_lists([self.world.curated_1000()]))
        self.drawn = {r["source_id"] for r in
                      draw_mod.draw(frame, uniform=2, curated=1, seed=5)["articles"]}
        self.assertLess(len(self.drawn), 6)
        self.others = sorted(set(self.world.entries) - self.drawn)

    def build(self, *extra):
        return cf(self.world, "bench", "build", "b1", "--sourceset", fx.SOURCESET,
                  "--frame", self.world.frame_file(), "--curated-qids", self.world.curated_1000(),
                  "--uniform", 2, "--curated", 1, "--spans-per-article", 2, "--seed", 5, *extra)

    def unrender(self, sid):
        keys = ("raw_sha256", "html_sha256", "layer_sha256", "renderer")
        self.rendered = getattr(self, "rendered", {})
        self.rendered[sid] = {k: self.world.entries[sid][k] for k in keys}
        self.world.change_entry(sid, **{k: None for k in keys})

    def test_only_the_drawn_articles_need_a_render_and_pi_is_the_whole_frames(self):
        for sid in self.others:
            self.unrender(sid)
        out = self.build()
        lay = layout_of(self.world)
        frame = json.loads(lay.frame_json.read_text())
        draw = json.loads(lay.draw_json.read_text())
        self.assertEqual(frame["N"], 6)
        self.assertEqual(sorted(frame["unrendered"]), self.others)
        self.assertEqual({r["source_id"] for r in draw["articles"]}, self.drawn)
        self.assertTrue(all(r["pi_uniform"] == 2 / 6 for r in draw["articles"]))
        self.assertNotIn("excluded", draw)
        self.assertIn(f"{6 - len(self.others)} rendered, {len(self.others)} not yet", out)
        self.assertFalse(lay.unrendered_list.exists())

    def test_a_drawn_unrendered_article_is_refused_with_the_step_that_fetches_it(self):
        gone = sorted(self.drawn)[0]
        self.unrender(gone)
        with self.assertRaises(CfError) as ctx:
            self.build()
        lay = layout_of(self.world)
        self.assertIn(gone, str(ctx.exception))
        self.assertIn("cf sourceset fetch", ctx.exception.hint)
        self.assertIn("cf sourceset render", ctx.exception.hint)
        self.assertIn("--exclude-unrendered", ctx.exception.hint)
        self.assertEqual(lay.unrendered_list.read_text(), f"{gone}\n")
        for written in (lay.frame_json, lay.draw_json, lay.bench_yaml):
            self.assertFalse(written.exists(), written)

    def test_the_unrendered_list_is_a_selection_the_fetch_takes(self):
        # the named step fetches exactly the listed sources (`cf sourceset fetch --select`),
        # never the whole sourceset: the list must be one the fetch's selection reader takes
        from cfcmd import sourceset as sourceset_cmd
        from sources.sourceset import Sourceset
        gone = sorted(self.drawn)[0]
        self.unrender(gone)
        with self.assertRaises(CfError) as ctx:
            self.build()
        lay = layout_of(self.world)
        # the workspace root is resolved (on macOS /var/folders is /private/var/folders)
        self.assertIn(f"--select {lay.unrendered_list.resolve()} --execute", ctx.exception.hint)
        base = Sourceset.open(WorkspacePaths(self.world.root), fx.SOURCESET).latest()
        args = mock.Mock(sources=[], select=str(lay.unrendered_list))
        self.assertEqual(sourceset_cmd._selection(base, args), [gone])

    def test_or_excluded_with_its_inclusion_probabilities(self):
        gone = sorted(self.drawn)[0]
        self.unrender(gone)
        out = self.build("--exclude-unrendered")
        lay = layout_of(self.world)
        draw = json.loads(lay.draw_json.read_text())
        self.assertEqual([r["source_id"] for r in draw["excluded"]], [gone])
        row = draw["excluded"][0]
        self.assertIn("not rendered", row["reason"])
        self.assertGreater(row["pi"], 0.0)
        self.assertEqual({r["source_id"] for r in draw["articles"]}, self.drawn - {gone})
        self.assertIn(gone, sorted(draw["uniform"]["drawn"] + draw["curated"]["drawn"]))
        host = json.loads(lay.host_manifest.read_text())["articles"]
        self.assertNotIn(gone, sum(host.values(), []))
        spans = json.loads(lay.spans_json.read_text())["spans"]
        self.assertNotIn(gone, {s["source_id"] for s in spans})
        self.assertIn("excluded : 1 drawn article(s)", out)
        # rendered later: the same draw, nothing excluded, the list gone
        self.world.change_entry(gone, **self.rendered[gone])
        self.build()
        again = json.loads(lay.draw_json.read_text())
        self.assertEqual({r["source_id"] for r in again["articles"]}, self.drawn)
        self.assertNotIn("excluded", again)
        self.assertFalse(lay.unrendered_list.exists())


class TestABenchOnAnOlderRenderersLayers(unittest.TestCase):
    """The field bench v0 was built on ``wpm``, every article rendered by ``web-html/1``.

    A run is planned only on the current renderer's layers, and ``cf bench score``
    refuses a run that read another rendering than the bench pins, so nothing of
    that bench could ever be scored; the build took it without a word.
    """

    setUp = TestAPartlyRenderedSourceset.setUp
    build = TestAPartlyRenderedSourceset.build

    def test_a_drawn_article_of_another_renderer_is_refused_with_the_step(self):
        from sources import render_html
        old = sorted(self.drawn)[0]
        self.world.change_entry(old, renderer="web-html/1")
        with self.assertRaises(CfError) as ctx:
            self.build()
        self.assertIn(f"{old} (web-html/1)", str(ctx.exception))
        self.assertIn(f"another renderer than {render_html.RENDERER}", str(ctx.exception))
        self.assertIn(f"cf sourceset create <new id> --from {fx.SOURCESET}", ctx.exception.hint)
        lay = layout_of(self.world)
        for written in (lay.frame_json, lay.draw_json, lay.bench_yaml):
            self.assertFalse(written.exists(), written)

    def test_an_undrawn_one_is_only_recorded(self):
        other = self.others[0]
        self.world.change_entry(other, renderer="web-html/1")
        self.build()
        frame = json.loads(layout_of(self.world).frame_json.read_text())
        self.assertEqual({a["source_id"]: a["renderer"] for a in frame["articles"]}[other],
                         "web-html/1")


class TestTheFrameReadsTheAdoptedRevision(unittest.TestCase):
    """A consumer-pinned page is read at its pin, even once a newer revision is rendered.

    The field case, reduced: a page enumerated at its latest revision and a
    consumer's older pin adopted beside it, both rendered.  A frame that took
    the highest rendered revision benched the newer one, which no run reads.
    """

    def setUp(self):
        self.world = fx.World(scratch(self) / "ws", articles=3)
        w = self.world
        # page 1001: the enumerated latest (2002) and an adopted older pin (1990),
        # recorded, as adopt can, without the page's QID and assessment
        self.pin0 = w.add_revision(0, 1990, origin="adopted", wikidata=None, assessment=None,
                                   adopted_from={"consumer": "c", "pins_sha256": "9" * 64})
        # page 1002: the consumer pinned the enumerated revision itself (2003), and a
        # newer enumerated revision (2100) was rendered later
        self.newer1 = w.add_revision(1, 2100)
        w.change_entry(w.sid(1), adopted_from={"consumer": "c", "pins_sha256": "9" * 64})

    def test_a_page_id_resolves_to_the_adopted_revision_with_the_pages_facts(self):
        manifest = inputs.load_manifest(WorkspacePaths(self.world.root), fx.SOURCESET)
        frame = draw_mod.build_frame([{"pageid": 1001}, {"pageid": 1002}, {"pageid": 1003}],
                                     manifest, {"Q99999000"})
        by_page = {a["pageid"]: a for a in frame["articles"]}
        self.assertEqual(by_page[1001]["source_id"], self.pin0)
        self.assertEqual(by_page[1001]["revision_basis"], "adopted")
        self.assertEqual(by_page[1001]["wikidata"], "Q99999000")
        self.assertEqual(by_page[1001]["assessment"], {"class": "B", "importance": "mid"})
        self.assertEqual(by_page[1001]["page_facts_from"],
                         {"wikidata": self.world.sid(0), "assessment": self.world.sid(0)})
        self.assertTrue(by_page[1001]["curated"])
        self.assertEqual(by_page[1002]["source_id"], self.world.sid(1))
        self.assertEqual(by_page[1002]["revision_basis"], "adopted")
        self.assertEqual(by_page[1003]["revision_basis"], "enumerated")
        self.assertEqual(frame["N_adopted"], 2)

    def test_an_unrendered_pin_is_not_replaced_by_a_rendered_revision(self):
        self.world.change_entry(self.pin0, layer_sha256=None, renderer=None)
        manifest = inputs.load_manifest(WorkspacePaths(self.world.root), fx.SOURCESET)
        frame = draw_mod.build_frame([{"pageid": 1001}], manifest, set())
        self.assertEqual(frame["unrendered"], [self.pin0])     # the pin, waiting for its render

    def test_the_build_benches_the_pins(self):
        cf(self.world, "bench", "build", "b1", "--sourceset", fx.SOURCESET,
           "--frame", self.world.frame_file(), "--uniform", 3, "--curated", 0, "--seed", 7)
        lay = layout_of(self.world)
        drawn = {r["source_id"] for r in json.loads(lay.draw_json.read_text())["articles"]}
        self.assertEqual(drawn, {self.pin0, self.world.sid(1), self.world.sid(2)})
        host = json.loads(lay.host_manifest.read_text())["articles"]
        self.assertNotIn(self.newer1, sum(host.values(), []))


class TestClustersAndSplits(unittest.TestCase):

    ARTICLES = [{"source_id": f"s{i}", "wikidata": f"Q{i}" if i != 3 else "Q0"} for i in range(6)]

    def test_shared_qids_and_shared_accept_sets_join(self):
        plain = draw_mod.clusters(self.ARTICLES)
        self.assertEqual(sorted(len(v) for v in plain.values()), [1, 1, 1, 1, 2])
        joined = draw_mod.clusters(self.ARTICLES, {"s1": {"mathlib:W.a"}, "s2": {"mathlib:W.a"},
                                                   "s4": {"mathlib:W.b"}})
        self.assertEqual(sorted(len(v) for v in joined.values()), [1, 1, 2, 2])
        self.assertIn(["s0", "s3"], list(joined.values()))

    def test_no_cluster_spans_two_splits_and_the_hash_alone_picks(self):
        cmap = {f"c-{i:04d}": [f"s{i}a", f"s{i}b"] if i % 3 == 0 else [f"s{i}"] for i in range(2000)}
        splits = draw_mod.split(cmap, salt="b:1", fractions={"dev": 0.5, "eval": 0.5})
        self.assertEqual(splits, draw_mod.split(cmap, salt="b:1", fractions={"dev": 0.5, "eval": 0.5}))
        by_article = draw_mod.article_splits(cmap, splits)
        for cid, members in cmap.items():
            self.assertEqual({by_article[s] for s in members}, {splits[cid]})
        share = sum(1 for v in splits.values() if v == "dev") / len(splits)
        self.assertTrue(0.45 < share < 0.55, share)
        # adding a cluster moves no other one
        bigger = dict(cmap, **{"c-new": ["s-new"]})
        again = draw_mod.split(bigger, salt="b:1", fractions={"dev": 0.5, "eval": 0.5})
        self.assertEqual({k: again[k] for k in cmap}, splits)
        three = draw_mod.split(cmap, salt="b:1", fractions={"dev": 0.4, "eval": 0.4, "test": 0.2})
        self.assertEqual(set(three.values()), {"dev", "eval", "test"})

    def test_g3_declarations_do_not_cluster_the_bench(self):
        world = frozen(self)
        clusters = manifest_of(world)["clusters"]
        self.assertGreater(len(clusters), 1, "one shared G3 declaration folded every article together")


class TestSpanSample(unittest.TestCase):

    def test_blocks_of_at_least_four_words_sampled_with_their_probability(self):
        layer = "one two three. alpha beta gamma delta. w x y z v."
        paragraphs = [{"span": [0, 14]}, {"span": [15, 38]}, {"span": [39, 49]}, {"span": [0, 999]}]
        frame = draw_mod.span_frame(layer, paragraphs, [])
        self.assertEqual([f["span"] for f in frame], [[15, 38], [39, 49]])
        a = draw_mod.sample_spans("s1", frame, 1, 3, 0.5)
        self.assertEqual(a, draw_mod.sample_spans("s1", frame, 1, 3, 0.5))
        self.assertEqual(len(a), 1)
        self.assertAlmostEqual(a[0]["pi_span"], 0.5)
        self.assertTrue(a[0]["span_id"].startswith("g3-"))
        self.assertEqual(len(draw_mod.sample_spans("s1", frame, 8, 3, 0.5)), 2)

    def test_nested_blocks_are_one_unit_the_outermost(self):
        # the field case, reduced: a displayed formula inside a paragraph, an item of a
        # nested list, a theorem box around its paragraph -- all blocks of their own
        layer = ("one two three four five six seven. "                    # p [0, 34] holds
                 "alpha beta gamma delta epsilon zeta eta theta iota. "   # p [35, 86] holds math
                 "w x y z v u t s.")                                       # li [87, 103]
        paragraphs = [{"span": [0, 34], "block": "theorem_box"}, {"span": [0, 34], "block": "p"},
                      {"span": [35, 86], "block": "p", "math": True},
                      {"span": [52, 70], "block": "math", "math": True},
                      {"span": [87, 103], "block": "li"}, {"span": [91, 103], "block": "li"}]
        frame = draw_mod.span_frame(layer, paragraphs, [])
        self.assertEqual([(f["span"], f["block"]) for f in frame],
                         [([0, 34], "theorem_box"), ([35, 86], "p"), ([87, 103], "li")])
        spans = draw_mod.sample_spans("s1", frame, 8, 3, 1.0)
        self.assertEqual(len(spans), 3)
        draw_mod.check_spans_disjoint(layer, spans)
        self.assertTrue(all(s["pi_span"] == 1.0 for s in spans))
        with self.assertRaises(CfError) as ctx:
            draw_mod.span_frame(layer, paragraphs + [{"span": [60, 100], "block": "dd"}], [])
        self.assertIn("overlap without nesting", str(ctx.exception))

    def test_the_build_samples_spans_and_writes_a_label_free_host_manifest(self):
        world = built(self)
        lay = layout_of(world)
        spans = json.loads(lay.spans_json.read_text())["spans"]
        self.assertEqual(len(spans), 12)
        host = lay.host_manifest.read_text()
        self.assertNotIn("span", json.loads(host)["articles"])
        for forbidden in ("Widget", "spanning", "g3-", "canary"):
            self.assertNotIn(forbidden, host)
        canary = lay.host_canaries.read_text().strip()
        self.assertTrue(canary.startswith("cf-bench-canary-"))


# ---------------------------------------------------------------------------
# the join
# ---------------------------------------------------------------------------

def run_passage(sid, item, span, *, status=None, outcome="located", scope="formal_math",
                locations=(), receipt=None):
    return m.RunPassage(source_id=sid, item=item, span=span, scope=scope, passage_kind="theorem",
                        prominence="central", outcome=outcome, status=status,
                        locations=[{"library": lib, "declaration": d, "relation": rel, "rank": r}
                                   for r, (lib, d, rel) in enumerate(locations, 1)],
                        receipt=receipt)


def gold_passage(gid, sid, span, *, status="formalized", accept=(), relations=None, tier="G3",
                 scope="formal_math", level="passage", weight=1.0):
    return m.GoldPassage(gold_id=gid, tier=tier, source_id=sid, span=span, level=level,
                         scope=scope, status=status, related=False, passage_kind="theorem",
                         accept=set(accept), relations=dict(relations or {}), weight=weight)


class TestTheJoin(unittest.TestCase):

    LAYER = "aa bb cc dd ee ff gg hh ii jj kk ll"

    def test_whole_words_only(self):
        self.assertEqual(overlap_words(self.LAYER, (0, 11), (0, 11)), 4)
        self.assertEqual(overlap_words(self.LAYER, (0, 10), (0, 11)), 3)    # dd cut
        self.assertEqual(overlap_words(self.LAYER, (0, 5), (5, 11)), 0)     # touching
        self.assertEqual(overlap_words(self.LAYER, (1, 11), (0, 11)), 3)    # aa cut

    def test_four_words_one_to_one_greedy_never_by_id(self):
        layers = {"s": self.LAYER}
        gold = [gold_passage("g1", "s", (0, 14)), gold_passage("g2", "s", (15, 35))]
        run = [run_passage("s", "In-text #00000001", (0, 11)),        # 4 words of g1
               run_passage("s", "In-text #00000002", (0, 14)),        # 5 words of g1
               run_passage("s", "In-text #00000003", (24, 35))]       # 4 of g2
        joined = m.join(gold, run, layers)
        self.assertEqual(joined["g1"].item, "In-text #00000002")
        self.assertEqual(joined["g2"].item, "In-text #00000003")
        short = m.join([gold_passage("g3", "s", (0, 8))], run, layers)   # 3 words only
        self.assertEqual(short, {})
        other_source = m.join([gold_passage("g1", "t", (0, 14))], run, {"t": self.LAYER})
        self.assertEqual(other_source, {})


# ---------------------------------------------------------------------------
# metrics, hand-computed
# ---------------------------------------------------------------------------

LAYER = " ".join(f"w{i}" for i in range(200))


def spn(k):
    """The k-th ten-word span of LAYER."""
    start = LAYER.index(f"w{10 * k} ")
    end = LAYER.index(f"w{10 * k + 9}") + len(f"w{10 * k + 9}")
    return (start, end)


class TestTheLabelersQuote(unittest.TestCase):
    """A quote is anchored only from its object form, and a wrong shape is named as one."""

    LAYER = "Every widget has a lemma. The widget lemma states that every widget is round."

    def test_an_object_quote_anchors(self):
        span, why = gold_mod._anchor_quote(self.LAYER, {"exact": "The widget lemma states that"})
        self.assertEqual(why, "")
        self.assertEqual(self.LAYER[span[0]:span[1]], "The widget lemma states that")

    def test_a_bare_string_is_named_not_read_as_no_quote(self):
        span, why = gold_mod._anchor_quote(self.LAYER, "The widget lemma states that")
        self.assertIsNone(span)
        self.assertIn("is a str, not an object", why)

    def test_no_quote(self):
        self.assertEqual(gold_mod._anchor_quote(self.LAYER, None), (None, "the labeler gave no quote"))


class TestTheMetrics(unittest.TestCase):

    def evaluate(self, tier, gold, run, *, state=None, ablated=None):
        layers = {"s": LAYER, "t": LAYER}
        clusters = {"s": "c1", "t": "c2"}
        return m.evaluate_tier(tier, gold, run, layers=layers, cluster_of=clusters,
                               article_state=state or {}, assessment={"s": {"importance": "high"}},
                               ablated=ablated)

    def test_false_not_formalized_with_its_causes_and_codes(self):
        gold = [gold_passage(f"g{k}", "s", spn(k), accept={("mathlib", "W.good")}) for k in range(6)]
        run = [
            run_passage("s", "p0", spn(0), status="formalized",
                        locations=[("mathlib", "W.good", "exactMatch")]),
            run_passage("s", "p1", spn(1), status="not_formalized", outcome="absent"),
            run_passage("s", "p2", spn(2), scope="expository"),
            # p3: never inventoried
            run_passage("s", "p4", spn(4), status="partial",
                        locations=[("mathlib", "W.good", "hasPart")]),
            run_passage("s", "p5", spn(5), status="not_formalized", outcome="absent",
                        receipt={"candidates": [{"library": "mathlib", "declaration": "W.good",
                                                 "verdict": "rejected", "reason": "x"}]}),
        ]
        result = self.evaluate("G3", gold, run)
        fnf = result.ratios["fnf_rate"]
        self.assertEqual((fnf.num, fnf.den), (5.0, 6.0))
        codes = {miss["gold_id"]: (miss["cause"], miss["code"]) for miss in result.misses}
        self.assertEqual(codes, {
            "g1": ("absence", "E-FALSE-ABSENCE"),
            "g2": ("scope_demotion", "E-WRONG-SCOPE"),
            "g3": ("inventory_miss", "E-INVENTORY-MISS"),
            "g4": ("weak_relation", "E-WRONG-RELATION"),
            "g5": ("absence", "E-REJECTED-CORRECT")})
        summary = m.summarise(result.ratios, ["c1", "c2"], replicates=50, seed="t")
        self.assertEqual(summary["fnf_rate"]["causes"]["absence"], 2)
        self.assertEqual(set(summary["fnf_rate"]["causes"]), vocab.FNF_CAUSES)

    def test_unresolved_and_gate_failed_are_misses_not_exclusions(self):
        gold = [gold_passage("g0", "s", spn(0)), gold_passage("g1", "t", spn(1))]
        result = self.evaluate("G3", gold, [], state={"s": "unresolved", "t": "gate_failed"})
        self.assertEqual((result.ratios["fnf_rate"].num, result.ratios["fnf_rate"].den), (2.0, 2.0))
        self.assertEqual(sorted(x["cause"] for x in result.misses), ["gate_failed", "unresolved"])
        self.assertTrue(all(x["code"] == "E-UNRESOLVED" for x in result.misses))
        # a joined passage whose locate task never landed is gate-failed too
        no_outcome = self.evaluate("G3", [gold_passage("g", "s", spn(0))],
                                   [run_passage("s", "p", spn(0), outcome=None)])
        self.assertEqual(no_outcome.misses[0]["cause"], "gate_failed")

    def test_a_weak_relation_at_a_declaration_never_retrieved_is_a_retrieval_miss(self):
        gold = [gold_passage("g", "s", spn(0), accept={("mathlib", "W.good")})]
        run = [run_passage("s", "p", spn(0), status="partial",
                           locations=[("mathlib", "W.other", "narrowMatch")])]
        self.assertEqual(self.evaluate("G3", gold, run).misses[0]["code"], "E-RETRIEVAL-MISS")

    def test_overclaim_scope_and_unresolved_rates(self):
        gold = [gold_passage("g0", "s", spn(0), status="partial"),
                gold_passage("g1", "s", spn(1), status="formalized"),
                gold_passage("g2", "s", spn(2), status=None, scope="non_math"),
                gold_passage("g3", "s", spn(3), status="not_formalized")]
        run = [run_passage("s", "p0", spn(0), status="formalized"),
               run_passage("s", "p1", spn(1), status="formalized"),
               run_passage("s", "p2", spn(2), scope="formal_math", status="not_formalized",
                           outcome="absent"),
               run_passage("s", "p3", spn(3), status=None, outcome="unresolved")]
        r = self.evaluate("G3", gold, run).ratios
        self.assertEqual((r["overclaim_rate"].num, r["overclaim_rate"].den), (1.0, 2.0))
        self.assertEqual((r["scope_error_rate"].num, r["scope_error_rate"].den), (1.0, 4.0))
        self.assertEqual((r["unresolved_rate"].num, r["unresolved_rate"].den), (1.0, 4.0))

    def test_recall_precision_library_recall_and_the_confusion(self):
        accept = {("mathlib", "W.a"), ("alpha", "A.a")}
        relations = {("mathlib", "W.a"): "exactMatch", ("alpha", "A.a"): "broadMatch"}
        gold = [gold_passage("g0", "s", spn(0), accept=accept, relations=relations),
                gold_passage("g1", "s", spn(1), accept={("mathlib", "W.b")},
                             relations={("mathlib", "W.b"): "exactMatch"})]
        run = [run_passage("s", "p0", spn(0), status="formalized",
                           locations=[("mathlib", "W.x", "closeMatch"), ("mathlib", "W.y", "hasPart"),
                                      ("alpha", "A.a", "exactMatch")]),
               run_passage("s", "p1", spn(1), status="formalized",
                           locations=[("mathlib", "W.b", "closeMatch")])]
        r = self.evaluate("G3", gold, run)
        ratios = r.ratios
        self.assertEqual((ratios["recall_at_1"].num, ratios["recall_at_1"].den), (1.0, 2.0))
        self.assertEqual((ratios["recall_at_3"].num, ratios["recall_at_3"].den), (2.0, 2.0))
        self.assertEqual((ratios["precision_at_1"].num, ratios["precision_at_1"].den), (1.0, 2.0))
        # g0's libraries mathlib (missed: W.x/W.y are mathlib -> found) and alpha (found); g1 mathlib
        self.assertEqual((ratios["library_recall"].num, ratios["library_recall"].den), (3.0, 3.0))
        self.assertEqual(r.confusion, {"broadMatch": {"exactMatch": 1}, "exactMatch": {"closeMatch": 1}})

    def test_inventory_recall_weighted_on_g3_and_plain_on_gx(self):
        g3 = [gold_passage("g0", "s", spn(0), weight=4.0), gold_passage("g1", "s", spn(1), weight=1.0)]
        run = [run_passage("s", "p0", spn(0), status="formalized")]
        r = self.evaluate("G3", g3, run).ratios["inventory_recall"]
        self.assertEqual((r.num, r.den), (4.0, 5.0))
        gx = [gold_passage("x0", "s", spn(0), tier="GX", status=None),
              gold_passage("x1", "s", spn(1), tier="GX", status=None)]
        r = self.evaluate("GX", gx, run).ratios["inventory_recall"]
        self.assertEqual((r.num, r.den), (1.0, 2.0))

    def test_g1_unanchorable_entries_score_at_article_level(self):
        gold = [gold_passage("e0", "s", None, tier="G1", level="article", accept={("mathlib", "W.a")}),
                gold_passage("e1", "t", None, tier="G1", level="article", accept={("mathlib", "W.a")})]
        run = [run_passage("s", "p", spn(3), status="formalized",
                           locations=[("mathlib", "W.a", "exactMatch")])]
        r = self.evaluate("G1", gold, run).ratios["article_fnf_rate"]
        self.assertEqual((r.num, r.den), (1.0, 2.0))

    def test_the_planted_tier_counts_memory(self):
        gold = [gold_passage("g0", "s", spn(0), tier="P", accept={("mathlib", "W.gone")}),
                gold_passage("g1", "s", spn(1), tier="P", accept={("mathlib", "W.gone2")})]
        run = [run_passage("s", "p0", spn(0), status="formalized",
                           locations=[("mathlib", "W.gone", "exactMatch")])]
        result = self.evaluate("P", gold, run, ablated={("mathlib", "W.gone"), ("mathlib", "W.gone2")})
        self.assertEqual((result.ratios["memory_rate"].num, result.ratios["memory_rate"].den), (1.0, 2.0))
        self.assertEqual([x["code"] for x in result.misses], ["E-MEMORY"])
        self.assertNotIn("fnf_rate", result.ratios)


class TestTheBootstrap(unittest.TestCase):

    CELLS = {"c1": [1.0, 2.0], "c2": [0.0, 2.0], "c3": [2.0, 2.0], "c4": [1.0, 1.0]}

    def test_seeded_and_cluster_level(self):
        one = m.bootstrap(self.CELLS, sorted(self.CELLS), replicates=500, seed="x")
        self.assertEqual(one, m.bootstrap(self.CELLS, sorted(self.CELLS), replicates=500, seed="x"))
        self.assertAlmostEqual(one["value"], 4 / 7)
        self.assertLessEqual(one["lo"], one["value"])
        self.assertGreaterEqual(one["hi"], one["value"])
        single = m.bootstrap({"c1": [1.0, 4.0]}, ["c1"], replicates=100, seed="x")
        self.assertEqual((single["lo"], single["hi"]), (0.25, 0.25))
        empty = m.bootstrap({}, ["c1"], replicates=10, seed="x")
        self.assertIsNone(empty["value"])

    def test_paired_draws_of_identical_runs_are_zero(self):
        draws = m.paired(self.CELLS, self.CELLS, sorted(self.CELLS), replicates=200, seed="p")
        self.assertTrue(draws and all(d == 0 for d in draws))


def gate_line(run_id: str, task_id: str, problems=None, *, exit_code: int | None = None,
              changed_files: bool = True, task_id_from: str | None = None) -> dict:
    """A gate log line as ``verify_locate.run_gate`` writes it, from the gate's own ``Problems``.

    ``changed_files`` is True for tend's call over a change set (validation, or
    pre-merge), False for an agent's own ``--findings`` check; ``task_id_from`` is
    the gate's own spelling, null when the line names no task.
    """
    reasons = problems.lines() if problems is not None else []
    return {"at": "2026-10-18T00:00:00Z", "run_id": run_id, "mode": "locate", "task_id": task_id,
            "task_id_from": (task_id_from or "$TEND_TASK_ID") if task_id else None,
            "changed_files": changed_files,
            "ok": not reasons and exit_code in (None, 0),
            "exit": exit_code if exit_code is not None else (1 if reasons else 0),
            "documents": 1, "by_rule": problems.by_rule() if problems is not None else {},
            "broken": "" if exit_code != 3 else "corpus.sqlite is missing", "reasons": reasons}


def real_problems(*items) -> "object":
    """The gate's own reason formatter (``verify_locate.Problems``) over ``(rule, where, message)``."""
    from gate import verify_locate

    problems = verify_locate.Problems()
    for rule, where, message in items:
        problems.add(rule, where, message)
    return problems


DOC = "t-loc/enwiki-1001-2002.json"
ITEM = "In-text #3fa91c07"


class TestGateRejections(unittest.TestCase):
    """Items 42 and 74: the rule comes from the gate's ``[rule N]`` and ``by_rule``, never prose."""

    def test_real_gate_reasons_are_classified_by_rule_then_by_message(self):
        cases = [
            ((3, f"{DOC}: {ITEM}.locations[0]", "mathlib:Widget.x is not in corpus.sqlite at "
                                                "aaaaaaaaaaaa (a phantom declaration)"),
             "rule_3:phantom_declaration"),
            ((3, f"{DOC}: passages[1].locations[0]", "library 'gamma' is not in universe u1 "
                                                     "(alpha, mathlib)"), "rule_3:outside_universe"),
            ((5, f"{DOC}: {ITEM}", "an absence lists 1 distinct query; at least 2"),
             "rule_5:absence_receipt"),
            ((6, f"{DOC}: {ITEM}", "ranks [1, 1] are not distinct"), "rule_6:ranks"),
            ((6, f"{DOC}: {ITEM}.locations[1]", "closeMatch needs a note naming the convention "
                                                "difference"), "rule_6:note"),
            ((4, DOC, "curated L2 hit(s) mathlib:Widget.a are neither located nor rejected with "
                      "a reason in any receipt"), "rule_4:curated"),
            ((2, f"{DOC}: passages[0]", "the quote matches the layer 2 times; it must match "
                                        "exactly once"), "rule_2:quote"),
            ((8, DOC, "the math paragraph at [0, 84] (section []) is covered by no passage and "
                      "no skip (the coverage floor)"), "rule_8:coverage"),
        ]
        for item, want in cases:
            with self.subTest(want=want):
                reason = real_problems(item).lines()[0]
                self.assertIn(ITEM if ITEM in item[1] else DOC, reason)
                self.assertEqual(m.classify_reason(reason), want)

    def test_rates_over_worker_attempts_of_the_scored_runs_only(self):
        lines = [gate_line("loc1", "t1"),
                 gate_line("loc1", "t2", real_problems(
                     (3, f"{DOC}: {ITEM}.locations[0]", "alpha:W.ghost is not in corpus.sqlite at "
                                                        "bbbbbbbbbbbb (a phantom declaration)"),
                     (6, f"{DOC}: {ITEM}", "ranks [1, 1] are not distinct"))),
                 gate_line("loc1", "t3", exit_code=3),           # the gate could not run
                 # an agent's own --findings check of its task's document: no attempt
                 gate_line("loc1", "t1", real_problems((1, DOC, "passages is a list")),
                           changed_files=False, task_id_from="--task-id"),
                 gate_line("inv1", "t9", real_problems((8, DOC, "the lead passage at [0, 84] was "
                                                                "dropped with no skip covering its span")))]
        found = m.gate_rejections(lines, ["loc1"])
        self.assertEqual((found["attempts"], found["rejected"], found["broken"]), (2, 1, 1))
        self.assertEqual(found["dropped_lines"], 2)
        self.assertEqual(found["by_rule"]["3"]["attempts"], 1)
        self.assertEqual(found["by_rule"]["6"]["rate"], 0.5)
        self.assertEqual(set(found["by_reason"]), {"rule_3:phantom_declaration", "rule_6:ranks"})
        self.assertNotIn("passage_id", json.dumps(found))


# ---------------------------------------------------------------------------
# G1 and the labeler
# ---------------------------------------------------------------------------

class TestG1Resolution(unittest.TestCase):

    def test_resolution_renames_twins_drops_and_the_tag_table(self):
        world = anchored(self)
        doc = json.loads(layout_of(world).g1_resolved.read_text())
        by_article: dict[str, list[dict]] = {}
        for entry in doc["entries"]:
            by_article.setdefault(entry["source_id"], []).append(entry)
        zero = by_article[world.sid(0)][0]
        accept = {(a["declaration"], a["why"]) for a in zero["accept"]}
        self.assertIn(("Widget.spanning_zero", "curated"), accept)
        self.assertIn(("Widget.spanning_one", "to_additive"), accept)
        self.assertEqual({e["source_id"] for e in doc["entries"] if e["qid"] == "Q99999000"},
                         {world.sid(0), world.sid(5)})
        # article 1: two statements, two entries -- their accept sets do not meet
        ones = {tuple(e["via"]): {(a["declaration"], a["why"]) for a in e["accept"]}
                for e in by_article[world.sid(1)]}
        self.assertIn(("Widget.add_spanning", "to_additive"), ones[("mathlib_1000",)])
        self.assertIn(("Widget.spanning_one", "curated"), ones[("mathlib_100",)])
        # claims whose accept sets meet are one entry: Q99999000's two articles each merge
        # the 1000.yaml claim with nothing else, and a twin is shared with the 100.yaml one
        self.assertIn(("Widget.spanning_zero", "to_additive"), ones[("mathlib_100",)])
        threes = {tuple(e["via"]): [(a["declaration"], a["why"]) for a in e["accept"]]
                  for e in by_article[world.sid(3)]}
        self.assertEqual(threes[("mathlib_1000",)], [("Widget.new_name", "renamed")])
        # a 100.yaml entry carrying a QID is matched by it, as L2 matches it (item 75)
        self.assertEqual(threes[("mathlib_100",)], [("Widget.conj_three", "curated")])
        self.assertEqual(by_article[world.sid(2)][0]["via"], ["wikidata_tag"])
        self.assertEqual(by_article[world.sid(2)][0]["evidence"],
                         ["@[wikidata Q99999002] at Mathlib/Widget/Tags.lean:10"])
        self.assertEqual(doc["dropped_entries"][0]["dropped"][0]["declaration"], "Widget.gone_forever")
        self.assertEqual(doc["unmatched"], {"mathlib_1000": 1})
        self.assertEqual(doc["listed_without_declarations"], {"mathlib_1000": 1})
        self.assertEqual(doc["universe"], {"id": fx.UNIVERSE, "sha256": world.universe_sha})

    def test_a_deprecated_alias_widens_to_its_new_name_item_23(self):
        world = anchored(self)
        doc = json.loads(layout_of(world).g1_resolved.read_text())
        # "The Widget_lemma 4" is article 4's title under L2's normalisation (item 75), and
        # it names a deprecated alias whose new name the index records (corpus deprecated_to)
        four = [e for e in doc["entries"] if e["source_id"] == world.sid(4)]
        self.assertEqual(len(four), 1)
        self.assertEqual([(a["declaration"], a["why"]) for a in four[0]["accept"]],
                         [("Widget.canonical", "renamed"), ("Widget.old_alias", "curated")])


class TestTheLabeler(unittest.TestCase):

    def setUp(self):
        self.world = anchored(self)
        self.lay = layout_of(self.world)

    def emit(self, mode):
        cf(self.world, "bench", "anchor", "b1", "--emit-args", mode)
        return json.loads(self.lay.label_args(mode).read_text())

    def run_transcribe(self, args, labels, *extra):
        path = self.world.root / "r.json"
        path.write_text(json.dumps(fake_result(args, labels)))
        return cf(self.world, "bench", "anchor", "b1", "--transcribe", path, *extra)

    def test_the_args_hold_no_run_record(self):
        eval_run_like = {self.world.sid(0): [fx.passage(self.world, 0, 0, "In-text #deadbeef")]}
        self.world.run("loc9", inventory_run="inv9", passages=eval_run_like, outcomes=[
            fx.outcome(self.world, 0, "In-text #deadbeef", status="formalized", outcome_="located",
                       run_id="loc9", inventory_run="inv9")])
        for mode in ("anchor", "label"):
            text = json.dumps(self.emit(mode))
            for leak in ("In-text #", "deadbeef", "loc9", "inv9", "locate-outcomes", "canary"):
                self.assertNotIn(leak, text, f"{mode} args leak {leak}")

    def test_an_anchor_is_kept_only_when_the_quote_matches_exactly_once(self):
        args = self.emit("anchor")
        labels = []
        for k, task in enumerate(args["tasks"]):
            i = index_of(task["source_id"])
            quote = self.world.quote(i, 0, 6) if k else {"exact": "widget"}   # 'widget' recurs
            labels.append({"task_id": task["task_id"], "quote": quote, "relation": "exactMatch",
                           "passage_kind": "theorem", "evidence": "e"})
        out = self.run_transcribe(args, labels)
        self.assertIn("article_level 1", out)
        rows = gold_mod.read_labels(self.lay, "G1")
        art = [r for r in rows if r["level"] == "article"]
        self.assertEqual(len(art), 1)
        self.assertIn("not exactly once", art[0]["anchor_problem"])
        canary = self.lay.host_canaries.read_text().strip()
        self.assertTrue(all(r["canary"] == canary for r in rows))
        passage = [r for r in rows if r["level"] == "passage"][0]
        layer = self.world.articles[passage["source_id"]]["layer"]
        self.assertEqual(layer[passage["span"][0]:passage["span"][1]], passage["quote"]["exact"])
        self.assertEqual(passage["status"], "formalized")
        spend = json.loads(self.lay.spend_json.read_text())
        self.assertEqual(spend["unpriced_runs"], 1)

    def test_g3_declarations_are_rechecked_at_the_pin(self):
        args = self.emit("label")
        labels = [{"task_id": t["task_id"], "scope": "formal_math", "passage_kind": "theorem",
                   "declarations": [{"library": "mathlib", "declaration": "Widget.sorry_four",
                                     "relation": "exactMatch"},
                                    {"library": "mathlib", "declaration": "Widget.hallucinated",
                                     "relation": "exactMatch"},
                                    {"library": "gamma", "declaration": "G.x", "relation": "exactMatch"}],
                   "evidence": "e"} for t in args["tasks"]]
        self.run_transcribe(args, labels)
        row = gold_mod.read_labels(self.lay, "G3")[0]
        self.assertEqual([d["declaration"] for d in row["declarations"]], ["Widget.sorry_four"])
        self.assertEqual(row["status"], "statement_only")
        self.assertEqual(sorted(d["reason"][:20] for d in row["dropped"]),
                         ["library outside the ", "not in the universe "])

    def test_the_refusals(self):
        args = self.emit("label")
        label = [{"task_id": args["tasks"][0]["task_id"], "scope": "non_math", "declarations": [],
                  "evidence": "e"}]
        for mutate, needle in (
                (lambda r: r.update(schema="cf-other"), "not a cf-locate-label-v1"),
                (lambda r: r.update(args_digest="0" * 64), "other args"),
                (lambda r: r.update(labeler={"version": "locate-label/0"}), "came from labeler"),
                (lambda r: r["labels"].append({"task_id": "g3-invented"}), "never asked for")):
            result = fake_result(args, list(label))
            mutate(result)
            path = self.world.root / "bad.json"
            path.write_text(json.dumps(result))
            with self.subTest(needle=needle), self.assertRaises(CfError) as ctx:
                cf(self.world, "bench", "anchor", "b1", "--transcribe", path)
            self.assertIn(needle, str(ctx.exception))
        good = self.world.root / "good.json"
        good.write_text(json.dumps(fake_result(args, label)))
        with mock.patch.object(gold_mod, "workflow_sha256", return_value="f" * 64):
            with self.assertRaises(CfError) as ctx:
                cf(self.world, "bench", "anchor", "b1", "--transcribe", good)
        self.assertIn("changed between emit and transcribe", str(ctx.exception))

    def test_a_partial_transcription_keeps_the_other_labels(self):
        args = self.emit("label")
        tasks = args["tasks"]
        row = lambda t, decl: {"task_id": t["task_id"], "scope": "formal_math",
                               "declarations": [{"library": "mathlib", "declaration": decl,
                                                 "relation": "exactMatch"}], "evidence": "e"}
        self.run_transcribe(args, [row(t, "Widget.conj_three") for t in tasks])
        self.run_transcribe(args, [row(tasks[0], "Widget.sorry_four")])
        rows = {r["gold_id"]: r for r in gold_mod.read_labels(self.lay, "G3")}
        self.assertEqual(len(rows), len(tasks))
        self.assertEqual(rows[tasks[0]["task_id"]]["declarations"][0]["declaration"], "Widget.sorry_four")
        self.assertEqual(rows[tasks[1]["task_id"]]["declarations"][0]["declaration"], "Widget.conj_three")

    def test_a_rebuild_that_changes_the_draw_of_a_labelled_bench_is_refused(self):
        cf(self.world, "bench", "build", "b1", "--sourceset", fx.SOURCESET, "--frame",
           self.world.frame_file(), "--curated-qids", self.world.curated_1000(), "--uniform", 6,
           "--curated", 3, "--spans-per-article", 2, "--seed", 7)          # same inputs: fine
        frame_before = layout_of(self.world).frame_json.read_bytes()
        with self.assertRaises(CfError) as ctx:
            cf(self.world, "bench", "build", "b1", "--sourceset", fx.SOURCESET, "--frame",
               self.world.frame_file(), "--uniform", 2, "--seed", 8)
        self.assertIn("another draw", str(ctx.exception))
        self.assertEqual(layout_of(self.world).frame_json.read_bytes(), frame_before)

    def test_calibration_measures_h_tag_blind(self):
        transcribe(self.world, "anchor", lambda t: {
            "task_id": t["task_id"], "quote": self.world.quote(index_of(t["source_id"]), 0, 6),
            "relation": "exactMatch", "passage_kind": "theorem", "evidence": "e"})
        args = self.emit("calibrate")
        self.assertTrue(args["tasks"])
        self.assertNotIn("declarations", json.dumps(args["tasks"]))          # no accept set given
        g1 = {r["gold_id"]: r for r in gold_mod.read_labels(self.lay, "G1")}
        labels = []
        for k, task in enumerate(args["tasks"]):
            hit = g1[task["task_id"]]["accept"][0] if k % 2 == 0 else {"library": "alpha",
                                                                         "declaration": "Alpha.unrelated"}
            labels.append({"task_id": task["task_id"], "scope": "formal_math",
                           "declarations": [dict(hit, relation="exactMatch")], "evidence": "e",
                           "hunters": [{"hunter": 1, "found": [f"{hit['library']}:{hit['declaration']}"]}]})
        self.run_transcribe(args, labels)
        cal = json.loads(self.lay.calibration_json.read_text())
        want = sum(1 for k in range(len(args["tasks"])) if k % 2 == 0)
        self.assertEqual((cal["found"], cal["total"]), (want, len(args["tasks"])))
        self.assertTrue(cal["tag_blind"])
        # tag-blind end to end (review #97): no QID in the args, and an accept declaration
        # the labeler added but no hunter found does not count
        self.assertTrue(all(src["qid"] is None for src in args["sources"]))
        for label in labels:
            label["hunters"] = [{"hunter": 1, "found": []}]
        self.run_transcribe(args, labels)
        self.assertEqual(json.loads(self.lay.calibration_json.read_text())["found"], 0)

    def test_gx_needs_a_signature_and_g4_its_caps(self):
        args = self.emit("inventory")
        labels = [{"task_id": t["task_id"], "source_id": t["source_id"], "enumerators": ["A", "B"],
                   "passages": [{"quote": self.world.quote(index_of(t["source_id"]), 1, 7),
                                 "passage_kind": "theorem", "scope": "formal_math",
                                 "from": ["A", "F"]}]} for t in args["tasks"]]
        with self.assertRaises(CfError):
            self.run_transcribe(args, labels)
        # one enumerator is not the two-enumerator inventory GX is (review #107)
        one = [dict(l, enumerators=["A"]) for l in labels]
        with self.assertRaises(CfError) as ctx:
            self.run_transcribe(args, one, "--signed-by", "the maintainer")
        self.assertIn("not both A and B", str(ctx.exception))
        self.run_transcribe(args, labels, "--signed-by", "the maintainer")
        gx = gold_mod.read_labels(self.lay, "GX")
        self.assertEqual(len(gx), len(args["tasks"]))
        self.assertEqual(gx[0]["signed_by"], "the maintainer")
        rows = [{"source_id": self.world.sid(i % 6), "quote": self.world.quote(i % 6, 3, 9),
                 "declarations": [], "checked_by": "m"} for i in range(31)]
        g4 = self.world.write("inputs/g4.jsonl", "".join(json.dumps(r) + "\n" for r in rows[:5]))
        cf(self.world, "bench", "anchor", "b1", "--g4", g4)
        self.assertEqual(len(gold_mod.read_labels(self.lay, "G4")), 5)
        with mock.patch.object(gold_mod, "G4_MAX_NEGATIVES", 2), self.assertRaises(CfError):
            cf(self.world, "bench", "anchor", "b1", "--g4", g4)


@unittest.skipUnless(NODE, "node is not installed")
class TestTheLabelerWorkflow(unittest.TestCase):
    """The Workflow body, executed with stubbed agents, and its result transcribed."""

    DRIVER = """\
import { readFileSync } from 'node:fs'
import body from './body.mjs'
const spec = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const calls = []
function agent(prompt, o) {
  o = o || {}
  calls.push({ label: o.label || '', prompt })
  const fn = spec.responses[o.label]
  return Promise.resolve(fn === undefined ? null : fn)
}
async function pipeline(items, ...stages) {
  const out = []
  for (let i = 0; i < items.length; i++) { let v = items[i]; for (const s of stages) v = await s(v, items[i], i); out.push(v) }
  return out
}
const parallel = (ts) => Promise.all(ts.map(t => t()))
const result = await body(spec.args, agent, pipeline, parallel, () => {})
process.stdout.write(JSON.stringify({ result, calls }))
"""

    def execute(self, args, responses):
        tmp = scratch(self)
        text = (SKILL / gold_mod.LABELER_WORKFLOW).read_text(encoding="utf-8")
        body = text.replace("export const meta =", "const meta =", 1)
        (tmp / "body.mjs").write_text("export default async function(args, agent, pipeline, parallel, log) {\n"
                                      + body + "\n}\n", encoding="utf-8")
        (tmp / "driver.mjs").write_text(self.DRIVER, encoding="utf-8")
        (tmp / "spec.json").write_text(json.dumps({"args": args, "responses": responses}), encoding="utf-8")
        proc = subprocess.run([NODE, str(tmp / "driver.mjs"), str(tmp / "spec.json")],
                              capture_output=True, text=True, cwd=str(tmp))
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        return json.loads(proc.stdout)

    def test_the_version_is_the_transcribers(self):
        text = (SKILL / gold_mod.LABELER_WORKFLOW).read_text(encoding="utf-8")
        self.assertIn(f"const LABELER_VERSION = '{gold_mod.LABELER_VERSION}'", text)
        self.assertIn("schema: 'cf-locate-label-v1'", text)

    def test_label_round_trip_through_the_body(self):
        world = anchored(self)
        cf(world, "bench", "anchor", "b1", "--emit-args", "label")
        args = json.loads(layout_of(world).label_args("label").read_text())
        responses = {}
        for task in args["tasks"]:
            tid = task["task_id"]
            responses[f"hunt0:{tid}"] = {"task_id": tid, "scope": "formal_math", "queries": ["rg widget"],
                                         "candidates": [{"library": "mathlib", "declaration": "Widget.conj_three",
                                                         "relation": "exactMatch", "tool": "ripgrep"}]}
            responses[f"hunt1:{tid}"] = {"task_id": tid, "scope": "formal_math", "queries": ["type"],
                                         "candidates": []}
            responses[f"label:{tid}"] = {"task_id": tid, "scope": "formal_math", "passage_kind": "theorem",
                                         "declarations": [{"library": "mathlib", "declaration": "Widget.conj_three",
                                                           "relation": "exactMatch"}], "evidence": "read"}
        out = self.execute(args, responses)
        prompts = [c["prompt"] for c in out["calls"]]
        self.assertTrue(all("NEVER look at any output of the system being measured" in p for p in prompts))
        self.assertFalse(any("TAG-BLIND" in p for p in prompts))
        self.assertTrue(all(t["excerpt"] in " ".join(prompts) for t in args["tasks"]))
        path = world.root / "wf-result.json"
        path.write_text(json.dumps(out["result"]))
        cf(world, "bench", "anchor", "b1", "--transcribe", path)
        rows = gold_mod.read_labels(layout_of(world), "G3")
        self.assertEqual(len(rows), len(args["tasks"]))
        self.assertEqual(rows[0]["hunters"][0]["found"], ["mathlib:Widget.conj_three"])

    def test_calibrate_is_tag_blind_and_an_unanswered_task_stays_unlabelled(self):
        args = {"mode": "calibrate", "bench": "b1", "digest": "d", "hunters": 2,
                "universe": {"id": "u1", "libraries": [{"id": "mathlib", "commit": "a" * 40}]},
                "sources": [{"source_id": "s1"}],
                "tasks": [{"task_id": "g1-x", "source_id": "s1", "span": [0, 4], "excerpt": "abcd"}]}
        out = self.execute(args, {})
        self.assertEqual(out["result"]["labels"], [])
        self.assertTrue(all("TAG-BLIND" in c["prompt"] for c in out["calls"]))

    def test_calibrate_names_no_qid_and_its_labeler_adds_nothing_review_97(self):
        args = {"mode": "calibrate", "bench": "b1", "digest": "d", "hunters": 1,
                "universe": {"id": "u1", "libraries": [{"id": "mathlib", "commit": "a" * 40}]},
                "sources": [{"source_id": "s1", "qid": "Q99999002", "title": "Widget"}],
                "tasks": [{"task_id": "g1-x", "source_id": "s1", "span": [0, 4], "excerpt": "abcd"}]}
        responses = {"hunt0:g1-x": {"task_id": "g1-x", "scope": "formal_math", "queries": ["rg"],
                                    "candidates": []},
                     "label:g1-x": {"task_id": "g1-x", "scope": "formal_math", "declarations": [],
                                    "evidence": "e"}}
        out = self.execute(args, responses)
        prompts = {c["label"]: c["prompt"] for c in out["calls"]}
        self.assertIn("TAG-BLIND", prompts["label:g1-x"])
        self.assertIn("Add NO declaration", prompts["label:g1-x"])
        self.assertFalse(any("Q99999002" in p for p in prompts.values()))

    def test_anchor_keeps_the_asked_task_id_review_106(self):
        args = {"mode": "anchor", "bench": "b1", "digest": "d",
                "universe": {"id": "u1", "libraries": []}, "sources": [{"source_id": "s1"}],
                "tasks": [{"task_id": "g1-a", "source_id": "s1", "declarations": []}]}
        out = self.execute(args, {"anchor:g1-a": {"task_id": "g1-b", "quote": None, "relation": None,
                                                  "passage_kind": "theorem", "evidence": "e"}})
        self.assertEqual([l["task_id"] for l in out["result"]["labels"]], ["g1-a"])

    def test_a_failed_enumerator_leaves_the_inventory_unlabelled_review_107(self):
        args = {"mode": "inventory", "bench": "b1", "digest": "d",
                "universe": {"id": "u1", "libraries": []}, "sources": [{"source_id": "s1"}],
                "tasks": [{"task_id": "s1", "source_id": "s1", "finders": []}]}
        merged = {"task_id": "s1", "passages": []}
        one = self.execute(args, {"enum0:s1": {"task_id": "s1", "passages": []},
                                  "inventory:s1": merged})
        self.assertEqual(one["result"]["labels"], [])
        both = self.execute(args, {"enum0:s1": {"task_id": "s1", "passages": []},
                                   "enum1:s1": {"task_id": "s1", "passages": []},
                                   "inventory:s1": merged})
        self.assertEqual(both["result"]["labels"][0]["enumerators"], ["A", "B"])


# ---------------------------------------------------------------------------
# freeze
# ---------------------------------------------------------------------------

class TestFreeze(unittest.TestCase):

    def test_the_manifest_its_sha_and_irreversibility(self):
        world = frozen(self, plant=True)
        lay = layout_of(world)
        version, man = gold_mod.latest_manifest(lay)
        self.assertEqual(version, 1)
        self.assertEqual(man["manifest_sha256"], gold_mod.sha_of(gold_mod.manifest_body(man)))
        self.assertEqual(set(man["tiers"]), {"G1", "G3", "P"})
        self.assertTrue(man["labeler"]["workflow_sha256"]["label"])
        self.assertIn("nothing changed", cf(world, "bench", "freeze", "b1"))
        self.assertIn("nothing changed", cf(world, "bench", "freeze", "b1", "--plant"))
        # an edit of a frozen label file is refused by score's verification
        g3 = lay.label_file("G3")
        original = g3.read_text()
        g3.write_text(original.replace('"theorem"', '"definition"', 1))
        with self.assertRaises(CfError):
            gold_mod.verify_frozen(lay, man)
        with self.assertRaises(CfError):
            cf(world, "bench", "freeze", "b1")
        cf(world, "bench", "freeze", "b1", "--supersede")
        v2, man2 = gold_mod.latest_manifest(lay)
        self.assertEqual((v2, man2["previous_sha256"]), (2, man["manifest_sha256"]))
        # gold is not re-labelled on a frozen bench without --supersede
        with self.assertRaises(CfError):
            cf(world, "bench", "anchor", "b1", "--curated-1000", world.curated_1000())
        # a hand-edited manifest no longer matches its own sha
        path = lay.gold_manifest(2)
        doc = json.loads(path.read_text())
        doc["splits"]["eval"] = []
        path.write_text(json.dumps(doc))
        with self.assertRaises(CfError):
            gold_mod.latest_manifest(lay)

    def test_a_label_without_the_canary_is_refused(self):
        world = frozen(self)
        lay = layout_of(world)
        rows = gold_mod.read_labels(lay, "G3")
        rows[0]["canary"] = "something-else"
        lay.label_file("G3").write_text("".join(json.dumps(r) + "\n" for r in rows))
        with self.assertRaises(CfError):
            cf(world, "bench", "freeze", "b1", "--supersede")

    def test_the_planted_index_removes_the_eval_g1_modules(self):
        world = frozen(self, plant=True)
        lay = layout_of(world)
        ablation = json.loads(lay.ablation_json.read_text())
        self.assertTrue(ablation["removed"])
        self.assertEqual(ablation["universe"], {"id": fx.UNIVERSE, "sha256": world.universe_sha})
        conn = query.open_db(lay.planted_db)
        try:
            # it describes the same universe (what plan-locate checks of a tools database)
            meta = query.check_universe(conn, world.universe())
            self.assertEqual(meta["planted"], "b1")
            for name in ablation["removed"]:
                lib, decl = name.split(":", 1)
                self.assertIsNone(query.declaration(conn, lib, decl, commit=fx.COMMIT_M))
        finally:
            conn.close()
        original = query.open_db(WorkspacePaths(world.root).corpus_db(fx.UNIVERSE))
        try:
            lib, decl = ablation["removed"][0].split(":", 1)
            self.assertIsNotNone(query.declaration(original, lib, decl, commit=fx.COMMIT_M))
        finally:
            original.close()


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------

class TestScore(unittest.TestCase):

    def setUp(self):
        self.world = frozen(self, plant=True)
        self.eval = eval_run(self.world)

    def test_the_document_and_its_keys(self):
        doc = score(self.world)
        self.assertLessEqual({"measurement_id", "pins", "scorer", "tiers", "strata", "cost",
                              "misses", "audit", "coverage", "canary_check", "per_cluster",
                              "gate_rejections", "runs"}, set(doc))
        self.assertEqual(doc["coverage"]["articles"], len(self.eval))
        self.assertEqual(doc["tiers"]["G1"]["arm"], "assisted")
        self.assertEqual(doc["canary_check"], {"ran": False, "basis": "--no-canary-check"})
        g3 = doc["tiers"]["G3"]["metrics"]
        # every eval article: G3 spans are paragraphs 0..3, two sampled; paragraph 1 unresolved
        self.assertIsNotNone(g3["fnf_rate"]["value"])
        self.assertTrue(all(miss["code"] in vocab.ERROR_CODES for miss in doc["misses"]))
        self.assertEqual(doc["pins"]["observed_models"], {"claude-opus-test": 300})
        self.assertEqual(doc["pins"]["agent_cli"], "2.0.0")
        self.assertEqual(doc["runs"], {"inventory": ["inv1"], "locate": ["loc1"], "challenge": []})
        self.assertEqual(doc["pins"]["tools_corpus_db"], {"kind": "full", "sha256": self.world.corpus_sha})

    def test_measurement_id_is_stable_and_moves_with_every_pin(self):
        first = score(self.world)["measurement_id"]
        self.assertEqual(score(self.world)["measurement_id"], first)
        changes = {
            "models": dict(models={"claude-other": {"input": 1}}),
            "prompts": dict(prompts={"worker.md": "9" * 64}),
            "corpus": dict(corpus_db_sha="7" * 64),
            "commit": dict(skill_commit="9" * 40),
        }
        for name, kw in changes.items():
            with self.subTest(pin=name):
                eval_run(self.world, run_id=f"loc-{name}", inventory_run=f"inv-{name}", **kw)
                self.assertNotEqual(score(self.world, f"loc-{name}")["measurement_id"], first)
        # --pin fills a version no run.yaml records (review #89), and never replaces one
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc1", "--no-canary-check", "--pin", "tend=1.0")
        self.assertIn("record tend 0.9", str(ctx.exception))
        from cflib.yamlio import read_yaml, write_yaml

        for rid in ("inv1", "loc1"):
            path = WorkspacePaths(self.world.root).run_yaml(rid)
            doc = read_yaml(path)
            doc["versions"].pop("tend")
            write_yaml(path, doc)
        self.assertEqual(score(self.world)["pins"]["tend"], "unrecorded")
        pinned = score(self.world, "loc1", "--no-canary-check", "--pin", "tend=1.0")
        self.assertNotEqual(pinned["measurement_id"], first)
        self.assertEqual(pinned["pins"]["tend"], "1.0")

    def test_an_incomplete_receipt_or_one_without_cost_is_refused(self):
        eval_run(self.world, run_id="loc2", inventory_run="inv2", complete=False)
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc2")
        self.assertIn("not complete", str(ctx.exception))
        eval_run(self.world, run_id="loc3", inventory_run="inv3")
        path = WorkspacePaths(self.world.root).receipt_json("inv3")
        path.write_text(json.dumps({"complete": True, "gate": {"ok": True}}))
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc3")
        self.assertIn("no cost block", str(ctx.exception))

    def test_r2_a_lower_bound_cost_is_refused_unless_article_costs_cover_it(self):
        """The review's pricing_timeout_only case: an envelope-priced receipt whose one call
        timed out reads $0.00 (envelopes 1, unpriced_calls 1), and the bench read it as the
        run's cost -- a candidate whose calls ran into the agent timeout read cheaper."""
        eval_run(self.world, run_id="loc4", inventory_run="inv4")
        path = WorkspacePaths(self.world.root).receipt_json("loc4")
        doc = json.loads(path.read_text())
        doc["cost"].update(basis="envelopes", usd=0.0, envelope_count=1, unpriced_calls=1,
                           unpriced_seconds=2700.0, message_count=0)
        path.write_text(json.dumps(doc))
        # the receipt as harvested before the tally read it (M-02: the score holds the two to it)
        self.world.tally("loc4", "inv4", [])
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc4")
        self.assertIn("run loc4's receipt cost.usd ($0.00) is a lower bound", str(ctx.exception))
        self.assertIn("1 agent call(s) left no priced envelope", str(ctx.exception))
        self.assertIn("re-run cf tally --locate", ctx.exception.hint or "")
        costs = self.world.write("inputs/costs4.json", json.dumps({s: 2.0 for s in self.eval}))
        scored = score(self.world, "loc4", "--no-canary-check", "--article-costs", costs)
        self.assertEqual(scored["cost"]["value"], 2.0)
        self.assertTrue(scored["cost"]["usd_total_lower_bound"])
        self.assertEqual(list(scored["cost"]["lower_bound_runs"]), ["loc4"])
        # a transcripts-priced receipt with a model in no price table is a lower bound too
        eval_run(self.world, run_id="loc5", inventory_run="inv5")
        path = WorkspacePaths(self.world.root).receipt_json("inv5")
        doc = json.loads(path.read_text())
        doc["cost"]["unpriced"] = ["claude-unknown-9"]
        path.write_text(json.dumps(doc))
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc5")
        self.assertIn("claude-unknown-9 are in no price table", str(ctx.exception))
        # and a whole receipt still scores with no lower-bound key
        self.assertNotIn("lower_bound_runs", score(self.world)["cost"])

    def test_a_canary_in_a_transcript_fails_the_run(self):
        canary = layout_of(self.world).host_canaries.read_text().strip()
        transcripts = scratch(self) / "t"
        transcripts.mkdir()
        (transcripts / "a.jsonl").write_text('{"text": "nothing"}\n')
        self.assertTrue(score(self.world, "loc1", "--transcripts", transcripts)["canary_check"]["ran"])
        (transcripts / "b.jsonl").write_text(json.dumps({"text": "gold says " + canary}) + "\n")
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc1", "--transcripts", transcripts)
        self.assertIn("canary", str(ctx.exception))
        report = self.world.write("inputs/scan.json",
                                  json.dumps(host_scan(self.world, 3, ["x.jsonl"])))
        with self.assertRaises(CfError):
            score(self.world, "loc1", "--canary-report", report)
        with self.assertRaises(CfError) as ctx:
            cf(self.world, "bench", "score", "b1", "--run", "loc1", "--split", "eval")
        self.assertIn("no canary check", str(ctx.exception))

    def test_an_uncovered_split_another_rendering_and_another_arm_are_refused(self):
        eval_run(self.world, run_id="loc4", inventory_run="inv4", planned=self.eval[:-1])
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc4")
        self.assertIn("did not plan", str(ctx.exception))
        eval_run(self.world, run_id="loc5", inventory_run="inv5",
                 renderer_layer={self.eval[0]: "5" * 64})
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc5")
        self.assertIn("another rendering", str(ctx.exception))
        eval_run(self.world, run_id="loc6", inventory_run="inv6", arm="blind")
        with self.assertRaises(CfError):
            score(self.world, "loc6", "--no-canary-check", "--arm", "assisted")
        self.assertEqual(score(self.world, "loc6")["arm"], "blind")
        with self.assertRaises(CfError):
            cf(self.world, "bench", "score", "b1", "--run", "loc1", "--split", "test",
               "--no-canary-check")

    def test_one_run_scores_once_per_split(self):
        every = sorted(self.world.articles)
        passages = {sid: [fx.passage(self.world, index_of(sid), 0, "In-text #0000aaaa")] for sid in every}
        outs = [fx.outcome(self.world, index_of(sid), "In-text #0000aaaa", status="not_formalized",
                           outcome_="absent", run_id="loc-all", inventory_run="inv-all", k=0)
                for sid in every]
        self.world.run("loc-all", inventory_run="inv-all", passages=passages, outcomes=outs)
        doc = score(self.world, "loc-all")
        cf(self.world, "bench", "score", "b1", "--run", "loc-all", "--split", "dev", "--bootstrap", 50,
           "--no-canary-check")
        names = sorted(p.name for p in layout_of(self.world).scores_dir.glob("*.json"))
        self.assertIn(f"{doc['score_id']}.json", names)
        self.assertTrue(doc["score_id"].startswith(f"eval-{doc['measurement_id'][:16]}-"))
        self.assertTrue(any(n.startswith(f"dev-{doc['measurement_id'][:16]}-") for n in names))

    def test_edited_gold_is_refused(self):
        g1 = layout_of(self.world).label_file("G1")
        g1.write_text(g1.read_text() + "\n")
        with self.assertRaises(CfError) as ctx:
            score(self.world)
        self.assertIn("changed after freeze", str(ctx.exception))

    def test_unresolved_and_missing_inventories_count_as_misses(self):
        eval_run(self.world, run_id="loc7", inventory_run="inv7", unresolved={self.eval[0]},
                 skip_docs=set(self.eval[1:2]))
        # nothing landed for the skipped article: no inventory document, no outcome row
        out = WorkspacePaths(self.world.root).locate_outcomes_jsonl("loc7")
        out.write_text("".join(l + "\n" for l in out.read_text().splitlines()
                               if json.loads(l)["source_id"] not in self.eval[1:2]))
        doc = score(self.world, "loc7")
        self.assertIn(self.eval[0], doc["coverage"]["articles_unresolved"])
        if len(self.eval) > 1:
            self.assertIn(self.eval[1], doc["coverage"]["articles_gate_failed"])
        causes = doc["tiers"]["G3"]["metrics"]["fnf_rate"]["causes"]
        self.assertGreater(causes["unresolved"], 0)

    def test_the_planted_run_reports_memory(self):
        ablation = json.loads(layout_of(self.world).ablation_json.read_text())
        removed = ablation["removed"][0].split(":", 1)[1]
        ev = self.eval
        passages = {sid: [fx.passage(self.world, index_of(sid), 0, f"In-text #{index_of(sid):08x}")]
                    for sid in ev}
        outs = [fx.outcome(self.world, index_of(sid), f"In-text #{index_of(sid):08x}",
                           status="formalized", outcome_="located",
                           locations=[("mathlib", removed, "exactMatch")], run_id="locp",
                           inventory_run="invp", k=0) for sid in ev]
        # D2: the workers searched the planted database; the gate and the outcomes checked
        # the full one, where the removed declaration exists, so its citation is a location
        self.world.run("locp", inventory_run="invp", passages=passages, outcomes=outs,
                       tools={"kind": "planted", "sha256": ablation["corpus_sha256"]})
        doc = score(self.world, "locp")
        self.assertEqual(set(doc["tiers"]), {"P"})
        self.assertGreater(doc["tiers"]["P"]["memory_citations"], 0)
        self.assertTrue(any(x["code"] == "E-MEMORY" for x in doc["misses"]))
        self.assertEqual(doc["pins"]["tools_corpus_db"]["kind"], "planted")
        self.assertNotIn("P", score(self.world, "loc1")["tiers"])

    def test_costs_gate_log_and_the_audit_sample(self):
        costs = self.world.write("inputs/costs.json", json.dumps({s: 2.0 for s in self.eval}))
        line = gate_line("loc1", "t-loc", real_problems(
            (3, f"{DOC}: {ITEM}.locations[0]", "mathlib:W.x is not in corpus.sqlite at "
                                               "aaaaaaaaaaaa (a phantom declaration)")))
        log = self.world.write("inputs/gate-log/a.jsonl", json.dumps(line) + "\n")
        doc = score(self.world, "loc1", "--no-canary-check", "--article-costs", costs,
                    "--gate-log", log.parent)
        self.assertEqual(doc["cost"]["value"], 2.0)
        rejections = doc["gate_rejections"]["loc1"]
        self.assertEqual(rejections["by_reason"]["rule_3:phantom_declaration"]["attempts"], 1)
        self.assertEqual(doc["gate_rejections"]["inv1"]["attempts"], 0)
        audit = layout_of(self.world).audit_jsonl(doc["score_id"])
        self.assertTrue(audit.is_file())


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

class TestX6APreRegisteredSubsetScore(unittest.TestCase):
    """Field defect X6: the second smoke covered 4 of the eval split's 36 articles, and
    ``cf bench score`` could only refuse it ("did not plan").  ``--subset`` scores the
    articles named, says so in the document and its measurement_id, and the
    acceptance rule refuses the result.  Freezing stays its own step: gold here is
    frozen first, as it must be."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.template = frozen_at(cls.tmp / "template")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        root = scratch(self) / "ws"
        shutil.copytree(self.template.root, root)
        self.world = clone_world(self.template, root)
        self.eval = manifest_of(self.world)["splits"]["eval"]
        if len(self.eval) < 2:
            self.skipTest("the draw put one article in eval")
        self.keep = sorted(self.eval)[:1]
        # the smoke's case: a run whose inventory and locate cover only `keep`
        passages, outs = {}, []
        for sid in self.keep:
            i = index_of(sid)
            item = f"In-text #{i:08x}"
            passages[sid] = [fx.passage(self.world, i, 0, item)]
            outs.append(fx.outcome(self.world, i, item, status="formalized", outcome_="located",
                                   locations=[("mathlib", "Widget.spanning_zero", "exactMatch")],
                                   run_id="loc-smoke", inventory_run="inv-smoke", k=0))
        self.world.run("loc-smoke", inventory_run="inv-smoke", passages=passages, outcomes=outs)

    def test_x6_the_split_is_refused_and_the_subset_is_scored(self):
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc-smoke")
        self.assertIn("did not plan", str(ctx.exception))
        doc = score(self.world, "loc-smoke", "--no-canary-check", "--subset", ",".join(self.keep))
        self.assertIs(doc["subset"], True)
        self.assertEqual(doc["subset_articles"], self.keep)
        self.assertEqual(doc["subset_sha256"], gold_mod.sha_of(self.keep))
        self.assertEqual(doc["pins"]["subset"], {"articles": self.keep,
                                                 "sha256": gold_mod.sha_of(self.keep),
                                                 "of_split": len(self.eval)})
        self.assertEqual(doc["coverage"]["articles"], len(self.keep))
        names = [p.name for p in layout_of(self.world).scores_dir.glob("*.json")]
        self.assertEqual(names, [f"{doc['score_id']}.json"])
        self.assertTrue(doc["score_id"].startswith(f"eval-subset-{doc['measurement_id'][:16]}-"))

    def test_x6_the_subset_is_in_the_measurement_id(self):
        eval_run(self.world)                           # loc1 covers the whole split
        full = score(self.world, "loc1")
        self.assertIs(full["subset"], False)
        self.assertNotIn("subset", full["pins"])
        whole = score(self.world, "loc1", "--no-canary-check", "--subset", ",".join(self.eval))
        part = score(self.world, "loc1", "--no-canary-check", "--subset", self.eval[0])
        self.assertEqual(len({full["measurement_id"], whole["measurement_id"],
                              part["measurement_id"]}), 3)

    def test_x6_a_file_of_ids_and_an_article_outside_the_split(self):
        listing = self.world.write("inputs/subset.txt",
                                   "# the smoke's articles\n" + "\n".join(self.keep) + "\n")
        doc = score(self.world, "loc-smoke", "--no-canary-check", "--subset", listing)
        self.assertEqual(doc["subset_articles"], self.keep)
        as_json = self.world.write("inputs/subset.json", json.dumps(self.keep))
        self.assertEqual(score(self.world, "loc-smoke", "--no-canary-check", "--subset",
                               as_json)["subset_sha256"], doc["subset_sha256"])
        outside = sorted(set(self.world.articles) - set(self.eval))[:1]
        if outside:
            with self.assertRaises(CfError) as ctx:
                score(self.world, "loc-smoke", "--no-canary-check", "--subset", outside[0])
            self.assertIn("not in the eval split", str(ctx.exception))

    def test_x6_the_acceptance_rule_refuses_a_subset_score(self):
        lay = layout_of(self.world)
        cells = {f"c{i:02d}": [float(i % 2), 1.0] for i in range(20)}
        base = lay.root / "base.json"
        base.write_text(json.dumps(score_doc(cells)))
        sub = score_doc({k: [0.0, 1.0] for k in cells}, mid="s" * 64)
        sub.update(subset=True, subset_articles=self.keep, subset_sha256=gold_mod.sha_of(self.keep))
        cand = lay.root / "cand.json"
        cand.write_text(json.dumps(sub))
        for b, c, role in ((base, cand, "candidate"), (cand, base, "baseline")):
            with self.subTest(role=role), self.assertRaises(CfError) as ctx:
                compare_mod.compare(lay, b, c, claim="G3.fnf_rate", replicates=100, seed=1)
            self.assertIn(f"the {role}", str(ctx.exception))
            self.assertIn("subset score", str(ctx.exception))
        with self.assertRaises(CfError) as ctx:
            compare_mod.compare(lay, base, base, claim="G3.fnf_rate", aa_path=cand,
                                replicates=100, seed=1)
        self.assertIn("A/A replicate", str(ctx.exception))
        self.assertFalse(lay.ledger_jsonl.exists())


def score_doc(fnf_cells, *, models=("m1",), renderer=("web-html/1",), split="eval", arm="blind",
              cost=1.0, articles=10, positives=20, gold="g" * 64, overclaim=None, mid=None,
              strata=None, scorer="s" * 64, tools="full", challenge_runs=(), owed=0,
              inventory=("inv1",), locate_cost=None, pins=None, canary=True, seen_no=True):
    clusters = sorted(fnf_cells)
    mid = mid or gold_mod.sha_of([fnf_cells, cost, models])
    if seen_no:
        strata = {"seen:no": {"fnf_rate": {"den": float(len(clusters)), "n": len(clusters),
                                           "by_cluster": fnf_cells}},
                  **(strata or {})}
    per_cluster = {"G3": {"fnf_rate": fnf_cells,
                          "overclaim_rate": overclaim or {c: [0.0, 1.0] for c in clusters}},
                   "cost": {"usd_per_article": {c: [cost, 1.0] for c in clusters}}}
    if locate_cost is not None:
        per_cluster["cost"]["usd_per_article_locate"] = {c: [locate_cost, 1.0] for c in clusters}
    return {"schema": "cf-bench-score/1", "bench": "b1", "split": split, "arm": arm,
            "measurement_id": mid, "run": f"loc-{mid[:8]}",
            "scorer": {"sha256": scorer, "files": 8},
            "runs": {"inventory": list(inventory), "locate": [f"loc-{mid[:8]}"],
                     "challenge": list(challenge_runs)},
            "pins": {"observed_models": {mm: 1 for mm in models}, "renderer": list(renderer),
                     "universe_sha256": "u", "tools_corpus_db": {"kind": tools, "sha256": "t"},
                     "challenge_runs": list(challenge_runs),
                     "receipts": {f"loc-{mid[:8]}": mid[:8] * 8},
                     # the configuration pins (run.yaml locate.config_shas), one per run
                     "gate_files": {f"loc-{mid[:8]}": "9" * 64},
                     "prompts": {f"loc-{mid[:8]}": dict(fx.CONFIG_SHAS["prompts"])},
                     **(pins or {})},
            "canary_check": {"ran": canary},
            "gold_manifest": {"version": 1, "sha256": gold},
            "coverage": {"articles": articles, "total_positives": positives,
                         "challenge_owed": {"total": owed}},
            "per_cluster": per_cluster, "strata": {"G3": strata or {}},
            "cost": {"basis": "per-article costs"}}


class TestCompare(unittest.TestCase):

    def setUp(self):
        self.world = fx.World(scratch(self) / "ws", articles=1)
        self.lay = layout_of(self.world)
        self.lay.root.mkdir(parents=True)
        n = 40
        self.base_cells = {f"c{i:02d}": [1.0 if i % 2 else 0.0, 1.0] for i in range(n)}
        self.better_cells = {f"c{i:02d}": [1.0 if i % 8 == 1 else 0.0, 1.0] for i in range(n)}

    def write(self, name, doc) -> Path:
        path = self.lay.root / f"{name}.json"
        path.write_text(json.dumps(doc))
        return path

    def compare(self, base, cand, aa=None, **kw):
        return compare_mod.compare(self.lay, base, cand, claim=kw.pop("claim", "G3.fnf_rate"),
                                   aa_path=aa, replicates=kw.pop("replicates", 1000), seed=1, **kw)

    def test_a_clear_gain_with_a_rejecting_aa_is_accepted_and_recorded_not_claimed(self):
        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells))
        aa = self.write("aa", score_doc(self.base_cells, mid="replicate"))
        entry = self.compare(base, cand, aa)
        self.assertEqual(entry["decision"], "accept", entry["failed"])
        self.assertFalse(entry["claimed"])
        self.assertIn("recorded, not claimed", entry["claim_basis"])
        self.assertEqual(entry["alpha_j"], 0.025)
        claimed = self.write("cand-big", score_doc(self.better_cells, articles=150))
        entry2 = self.compare(base, claimed, aa)
        self.assertTrue(entry2["claimed"])
        # the first claimed comparison spent alpha/2; the next one gets alpha/4
        entry3 = self.compare(base, claimed, aa)
        self.assertEqual(entry3["alpha_j"], 0.05 / 4)
        ledger = [json.loads(l) for l in self.lay.ledger_jsonl.read_text().splitlines()]
        self.assertEqual(len(ledger), 3)

    def test_no_aa_fails_and_an_aa_that_accepts_fails(self):
        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells))
        self.assertEqual(self.compare(base, cand)["decision"], "reject")
        lucky = self.write("lucky", score_doc(self.better_cells, mid="lucky"))
        entry = self.compare(base, cand, lucky)
        self.assertEqual(entry["decision"], "reject")
        self.assertIn("aa", entry["failed"])

    def test_a_safety_regression_a_cost_rise_or_a_stratum_drop_rejects(self):
        base = self.write("base", score_doc(self.base_cells))
        aa = self.write("aa", score_doc(self.base_cells, mid="r"))
        worse_overclaim = {c: [1.0, 1.0] for c in self.base_cells}
        cand = self.write("c1", score_doc(self.better_cells, overclaim=worse_overclaim))
        self.assertIn("safety", self.compare(base, cand, aa)["failed"])
        dear = self.write("c2", score_doc(self.better_cells, cost=1.5))
        self.assertIn("cost", self.compare(base, dear, aa)["failed"])
        good = {"den": 40, "n": 40, "by_cluster": {c: [0.0, 1.0] for c in self.base_cells}}
        bad = {"den": 40, "n": 40, "by_cluster": {c: [1.0, 1.0] for c in self.base_cells}}
        b = self.write("b3", score_doc(self.base_cells, strata={"importance:high": {"fnf_rate": good}}))
        c = self.write("c3", score_doc(self.better_cells, strata={"importance:high": {"fnf_rate": bad}}))
        a = self.write("a3", score_doc(self.base_cells, mid="r3",
                                       strata={"importance:high": {"fnf_rate": good}}))
        self.assertIn("strata", self.compare(b, c, a)["failed"])

    def test_a_model_change_and_other_incomparables_are_refused(self):
        base = self.write("base", score_doc(self.base_cells))
        for name, kw in (("model", {"models": ("m2",)}), ("renderer", {"renderer": ("web-html/2",)}),
                         ("split", {"split": "dev"}), ("arm", {"arm": "assisted"}),
                         ("gold", {"gold": "h" * 64})):
            with self.subTest(name=name), self.assertRaises(CfError):
                self.compare(base, self.write(name, score_doc(self.better_cells, **kw)))
        with self.assertRaises(CfError) as ctx:
            self.compare(base, self.write("model2", score_doc(self.better_cells, models=("m1", "m2"))))
        self.assertIn("new baseline", str(ctx.exception))

    def test_only_blind_g1_g3_and_p_may_be_claimed(self):
        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells))
        for claim in ("GX.inventory_recall", "G4.fnf_rate", "fnf_rate"):
            with self.subTest(claim=claim), self.assertRaises(CfError):
                self.compare(base, cand, claim=claim)
        b = self.write("ba", score_doc(self.base_cells, arm="assisted"))
        c = self.write("ca", score_doc(self.better_cells, arm="assisted"))
        with self.assertRaises(CfError):
            self.compare(b, c, claim="G1.fnf_rate")

    def test_another_scorer_tools_database_or_challenge_state_is_refused(self):
        base = self.write("base", score_doc(self.base_cells))
        for name, kw, needle in (
                ("scorer", {"scorer": "t" * 64}, "two scorers"),
                ("tools", {"tools": "planted"}, "planted"),
                ("challenged", {"challenge_runs": ("chl1",)}, "challenge run"),
                ("owed", {"owed": 3}, "owes challenge verdicts")):
            with self.subTest(name=name), self.assertRaises(CfError) as ctx:
                self.compare(base, self.write(name, score_doc(self.better_cells, **kw)))
            self.assertIn(needle, str(ctx.exception))

    def test_the_cost_condition_is_on_locate_spend_when_the_inventory_is_shared(self):
        # every stage: +10%, inside the rule; locate and challenge alone: +40%, outside it
        base = self.write("base", score_doc(self.base_cells, cost=2.0, locate_cost=0.5))
        aa = self.write("aa", score_doc(self.base_cells, cost=2.0, locate_cost=0.5, mid="r"))
        dear = self.write("dear", score_doc(self.better_cells, cost=2.2, locate_cost=0.7))
        entry = self.compare(base, dear, aa)
        self.assertEqual(entry["conditions"]["cost"]["cells"], "usd_per_article_locate")
        self.assertIn("cost", entry["failed"])
        other = self.write("other", score_doc(self.better_cells, cost=2.2, locate_cost=0.7,
                                              inventory=("inv2",)))
        entry = self.compare(base, other, aa)
        self.assertEqual(entry["conditions"]["cost"]["cells"], "usd_per_article")
        self.assertNotIn("cost", entry["failed"])

    def test_a_reused_baseline_is_noted(self):
        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells))
        self.assertEqual(self.compare(base, cand)["differences"], [])
        self.assertIn("baseline reused: an earlier entry compared this same baseline measurement",
                      self.compare(base, cand)["differences"])

    def test_the_cli_prints_the_decision(self):
        from cflib.yamlio import write_yaml

        write_yaml(self.lay.bench_yaml, {"schema": "cf-bench/1", "id": "b1"})
        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells))
        out = cf(self.world, "bench", "compare", "b1", base, cand, "--claim", "G3.fnf_rate",
                 "--bootstrap", 1000)
        self.assertIn("REJECT", out)          # no A/A replicate
        self.assertIn("aa", out)


# ---------------------------------------------------------------------------
# integration (wave 2) seams: one regression test per item
# ---------------------------------------------------------------------------

class TestTheUniverseSeams(unittest.TestCase):

    def anchor(self, world, *extra):
        return cf(world, "bench", "anchor", "b1", "--universe", fx.UNIVERSE, "--curated-1000",
                  world.curated_1000(), *extra)

    def test_a_database_of_another_state_or_none_is_refused_at_anchor_item_12(self):
        world = built(self)
        universe = world.universe()
        universe.add_library(library_id="beta", repo="https://example.org/beta", commit="e" * 40,
                             toolchain=fx.TOOLCHAIN, roots=["Beta"])
        universe.save()                 # the universe moved on; corpus.sqlite did not
        with self.assertRaises(CfError) as ctx:
            self.anchor(world)
        self.assertIn("another state of universe", str(ctx.exception))
        self.assertIsNone(inputs_bench(world).get("universe"))     # nothing was stamped
        universe = world.universe()
        universe.set_index("mathlib", path="index/mathlib/", sha256="9" * 64, declarations=1)
        universe.save()                 # an index re-ingested: no database describes it
        with self.assertRaises(CfError) as ctx:
            self.anchor(world)
        self.assertIn("records no built corpus.sqlite", str(ctx.exception))

    def test_gold_stamps_the_recomputed_sha_and_one_state_only_item_12(self):
        world = anchored(self)
        self.assertEqual(inputs_bench(world)["universe"],
                         {"id": fx.UNIVERSE, "sha256": world.universe_sha})
        old = world.universe_sha
        world.build_universe(fx.DECLARATIONS + (
            ("mathlib", "Widget.extra", "Mathlib.Widget.Extra", "proved", None, ()),))
        self.assertNotEqual(world.universe_sha, old)
        with self.assertRaises(CfError) as ctx:
            cf(world, "bench", "anchor", "b1", "--emit-args", "anchor")
        self.assertIn("labelled at universe", str(ctx.exception))
        cf(world, "bench", "anchor", "b1", "--emit-args", "anchor", "--supersede")
        self.assertEqual(inputs_bench(world)["universe"]["sha256"], world.universe_sha)

    def test_a_run_of_another_universe_state_is_not_scored_item_26(self):
        world = frozen(self)
        eval_run(world, universe_sha="9" * 64)
        with self.assertRaises(CfError) as ctx:
            score(world)
        self.assertIn("gold is labelled at", str(ctx.exception))
        self.assertIn("--supersede", ctx.exception.hint or "")

    def test_databases_open_read_only_through_corpus_query_item_17(self):
        world = fx.World(scratch(self) / "ws", articles=1)
        weird = scratch(self) / "q?h#%41"
        weird.mkdir()
        shutil.copy(world.paths.corpus_db(fx.UNIVERSE), weird / "corpus.sqlite")
        conn = inputs.open_checked(world.paths, world.universe(), weird / "corpus.sqlite")
        try:
            self.assertTrue(query.exists(conn, "mathlib", fx.COMMIT_M, "Widget.spanning_zero"))
        finally:
            conn.close()
        self.assertEqual([p.name for p in weird.parent.iterdir()], [weird.name])
        self.assertEqual([p.name for p in weird.iterdir()], ["corpus.sqlite"])
        import sqlite3

        other = weird / "other.sqlite"
        sqlite3.connect(other).execute("CREATE TABLE t (x)").connection.close()
        with self.assertRaises(CfError):
            inputs.open_checked(world.paths, world.universe(), other)

    def test_the_universe_is_read_by_its_owner_item_18(self):
        world = fx.World(scratch(self) / "ws", articles=1)
        from cflib.yamlio import read_yaml, write_yaml

        path = world.paths.universe_yaml(fx.UNIVERSE)
        data = read_yaml(path)
        data["libraries"][0]["roots"] = []
        write_yaml(path, data)
        with self.assertRaises(CfError) as ctx:
            inputs.load_universe(world.paths, fx.UNIVERSE)
        self.assertIn("not a valid universe", str(ctx.exception))

    def test_the_curated_library_is_the_universes_mathlib_item_24(self):
        from cfcmd import bench as bench_cmd

        world = built(self)
        world.build_universe(mathlib_id="mathlib4")
        self.assertEqual(bench_cmd._library(world.universe(), []), "mathlib4")
        self.anchor(world)
        doc = json.loads(layout_of(world).g1_resolved.read_text())
        self.assertEqual(doc["library"], "mathlib4")
        self.assertTrue(all(a["library"] == "mathlib4" for e in doc["entries"] for a in e["accept"]
                            if "wikidata_tag" not in e["via"]))
        world.build_universe(libraries=("alpha",))
        with self.assertRaises(CfError) as ctx:
            bench_cmd._library(world.universe(), [])
        self.assertIn("--library", ctx.exception.hint or "")

    def test_the_corpus_readers_are_corpus_querys_item_19(self):
        world = fx.World(scratch(self) / "ws", articles=1)
        conn = inputs.open_checked(world.paths, world.universe())
        try:
            row = inputs.declaration(conn, "mathlib", fx.COMMIT_M, "Widget.old_alias")
            self.assertEqual(row["deprecated_to"], "Widget.canonical")
            self.assertIsNone(inputs.declaration(conn, "mathlib", "f" * 40, "Widget.old_alias"))
            self.assertEqual([r["declaration"] for r in inputs.tagged(conn, "wikidata", "Q99999002",
                                                                      ["mathlib"])],
                             ["Widget.tagged_two"])
            self.assertEqual(inputs.tagged(conn, "wikidata", "Q99999002", ["alpha"]), [])
        finally:
            conn.close()

    def test_the_fixtures_are_built_by_the_owners_item_20(self):
        from corpus import build_db
        from sources.sourceset import Sourceset

        world = fx.World(scratch(self) / "ws", articles=2)
        universe = world.universe()
        self.assertEqual(universe.validate(require_indexes=True), [])
        self.assertTrue(build_db.current(universe, world.paths.corpus_db(fx.UNIVERSE)))
        ss = Sourceset.open(world.paths, fx.SOURCESET)
        manifest = ss.latest()
        self.assertEqual(len(manifest), 2)
        self.assertTrue(all(ss.source_problems(e) == [] for e in manifest))


def inputs_bench(world: fx.World) -> dict:
    from cflib.yamlio import read_yaml

    return read_yaml(layout_of(world).bench_yaml)


class TestTheCuratedJoinIsL2s(unittest.TestCase):
    """Item 75: G1 and ``cf locate candidates`` read 1000.yaml/100.yaml through one join."""

    def test_one_normaliser_and_the_same_article_for_every_entry(self):
        from locate import candidates

        world = fx.World(scratch(self) / "ws")
        self.assertIs(gold_mod.normalise_title, candidates.normalise_title)
        lists = candidates.CuratedLists(list_1000=world.curated_1000(), list_100=world.curated_100())
        l2 = set()
        for entry in (world.articles[s]["entry"] for s in sorted(world.articles)):
            qid, key = entry["wikidata"], candidates.normalise_title(entry["title"])
            # (key, names) per entry: a suffixed key (Q…A) joins its base QID
            l2 |= {(entry["source_id"], "mathlib_1000", n)
                   for _key, names in lists.by_qid_1000.get(qid, []) for n in names}
            for _number, names in lists.by_qid_100.get(qid, []):
                l2 |= {(entry["source_id"], "mathlib_100", n) for n in names}
            for _number, names, own in lists.by_title_100.get(key, []):
                if not (own and qid and own != qid):     # a title join naming another QID is refused
                    l2 |= {(entry["source_id"], "mathlib_100", n) for n in names}
        conn = inputs.open_checked(world.paths, world.universe())
        try:
            resolved = gold_mod.resolve_g1(
                [world.articles[s]["entry"] for s in sorted(world.articles)], conn,
                library="mathlib", commit=fx.COMMIT_M,
                lists=[("mathlib_1000", gold_mod.read_curated_1000(world.curated_1000())),
                       ("mathlib_100", gold_mod.read_curated_100(world.curated_100()))],
                renames={}, tag_libraries=["mathlib"])
        finally:
            conn.close()
        g1 = {(e["source_id"], via, d["declaration"])
              for e in resolved["entries"] + resolved["dropped_entries"]
              for via in e["via"] if via != "wikidata_tag" for d in e["declarations"]}
        self.assertEqual(g1, l2)
        self.assertIn((world.sid(4), "mathlib_100", "Widget.old_alias"), g1)       # "The ..._..."
        self.assertIn((world.sid(3), "mathlib_100", "Widget.conj_three"), g1)      # by its QID


class TestTheRunSeams(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.template = frozen_at(cls.tmp / "template")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        root = scratch(self) / "ws"
        shutil.copytree(self.template.root, root)
        self.world = clone_world(self.template, root)
        self.eval = eval_run(self.world)

    def score(self, run_id="loc1", *extra):
        return score(self.world, run_id, *extra)

    def test_documents_land_at_task_and_source_item_46(self):
        rels = [rel for rel, _doc in inputs.locate_documents(self.world.paths, "inv1")]
        self.assertTrue(rels)
        self.assertTrue(all(rel == f"t-inv/{rel.split('/')[-1]}" for rel in rels), rels)
        self.assertFalse(any("findings" in p.parts for p in (self.world.root / "corpus").rglob("*")))

    def test_an_ungated_receipt_is_refused_item_50(self):
        for n, gated in enumerate((None, False)):
            eval_run(self.world, run_id=f"loc-u{n}", inventory_run=f"inv-u{n}", gated=gated)
            with self.subTest(gated=gated), self.assertRaises(CfError) as ctx:
                self.score(f"loc-u{n}")
            self.assertIn("not gated", str(ctx.exception))

    def test_a_corrupt_document_is_refused_by_name_item_69(self):
        eval_run(self.world, run_id="loc-x", inventory_run="inv-x")
        bad = self.world.root / "corpus" / "inv-x" / "t-inv" / f"{self.eval[0]}.json"
        bad.write_text("{not json")
        with self.assertRaises(CfError) as ctx:
            self.score("loc-x")
        self.assertIn(f"t-inv/{self.eval[0]}.json", str(ctx.exception))

    def test_a_source_shard_is_refused_not_scored_as_gate_failures_item_59(self):
        if len(self.eval) < 2:
            self.skipTest("the draw put one article in eval")
        keep = self.eval[:1]
        eval_run(self.world, run_id="loc-s", inventory_run="inv-s", locate_sources=keep)
        rows = WorkspacePaths(self.world.root).locate_outcomes_jsonl("loc-s")
        rows.write_text("".join(l + "\n" for l in rows.read_text().splitlines()
                                if json.loads(l)["source_id"] in keep))
        with self.assertRaises(CfError) as ctx:
            self.score("loc-s")
        self.assertIn("no outcome for the formal_math passages", str(ctx.exception))
        self.assertIn("locate.source_ids", ctx.exception.hint or "")

    def test_a_missing_row_is_a_gate_failure_and_counted_item_60(self):
        eval_run(self.world, run_id="loc-m", inventory_run="inv-m")
        path = WorkspacePaths(self.world.root).locate_outcomes_jsonl("loc-m")
        rows = [json.loads(l) for l in path.read_text().splitlines()]
        rows[0].update(outcome=None, status=None, locations=[], missing=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        doc = self.score("loc-m")
        self.assertEqual(doc["coverage"]["missing_rows"], 1)

    def test_a_subject_only_run_is_refused_item_60(self):
        eval_run(self.world, run_id="loc-subj", inventory_run="inv-subj", subject_only=True)
        with self.assertRaises(CfError) as ctx:
            self.score("loc-subj")
        self.assertIn("subject-only", str(ctx.exception))

    def test_the_arms_of_every_run_agree_item_63(self):
        eval_run(self.world, run_id="loc-b", inventory_run="inv-b", arm="blind",
                 inventory_arm="assisted")
        with self.assertRaises(CfError) as ctx:
            self.score("loc-b")
        self.assertIn("inv-b", str(ctx.exception))
        eval_run(self.world, run_id="loc-c", inventory_run="inv-c", challenge_runs=["chl-c"])
        self.world.challenge_run("chl-c", of="loc-c", arm="blind")
        with self.assertRaises(CfError) as ctx:
            self.score("loc-c")
        self.assertIn("chl-c", str(ctx.exception))

    def test_each_runs_cost_is_spread_over_its_own_sources_item_64(self):
        every = sorted(self.world.articles)
        eval_run(self.world, run_id="loc-$", inventory_run="inv-$", planned=every,
                 inventory_usd=60.0, locate_usd=12.0)
        doc = self.score("loc-$")
        n = len(self.eval)
        cost = doc["cost"]
        self.assertAlmostEqual(cost["value"], 60.0 / len(every) + 12.0 / n)
        self.assertAlmostEqual(cost["locate_and_challenge"]["value"], 12.0 / n)
        self.assertAlmostEqual(cost["by_stage"]["inventory"], 60.0 / len(every))
        self.assertEqual(cost["runs"]["inv-$"], {"usd": 60.0, "sources": len(every),
                                                 "share": round(60.0 / len(every), 6)})
        self.assertIn("usd_per_article_locate", doc["per_cluster"]["cost"])

    def test_the_commit_is_each_runs_and_the_scorer_is_apart_item_62(self):
        doc = self.score()
        self.assertEqual(doc["pins"]["skill_commit"], {"inv1": fx.RUN_COMMIT, "loc1": fx.RUN_COMMIT})
        self.assertEqual(doc["pins"]["gate_files"]["loc1"], gold_mod.sha_of(fx.GATE_FILES))
        # D6: what each run staged, its own gate.json included, is pinned as its plan recorded it
        for rid in ("inv1", "loc1"):
            staged = yamlio.read_yaml(WorkspacePaths(self.world.root).run_yaml(rid))["locate"]
            self.assertEqual(doc["pins"]["rendered_gate_files"][rid],
                             gold_mod.sha_of(staged["gate_files"]))
            self.assertIn("gate/gate.json", staged["gate_files"])
        self.assertEqual(len(doc["scorer"]["sha256"]), 64)
        with mock.patch.object(score_mod, "scorer", return_value={"sha256": "0" * 64, "files": 1}):
            again = self.score()
        self.assertEqual(again["measurement_id"], doc["measurement_id"])
        self.assertEqual(again["scorer"]["sha256"], "0" * 64)
        self.assertEqual(len(list(layout_of(self.world).scores_dir.glob("eval-*.json"))), 1)
        eval_run(self.world, run_id="loc-old", inventory_run="inv-old", skill_commit=None)
        self.assertEqual(set(self.score("loc-old")["pins"]["skill_commit"].values()), {"unrecorded"})

    def test_the_harvested_host_files_are_the_defaults_item_68(self):
        lines = [gate_line("loc1", "t-loc", real_problems(
                     (5, f"{DOC}: {ITEM}", "an absence lists 1 distinct query; at least 2"))),
                 gate_line("loc1", "t-loc"), gate_line("loc1", "", changed_files=False)]
        self.world.host("loc1", gate_lines=lines, canary=host_scan(self.world, 4))
        with self.assertRaises(CfError) as ctx:
            cf(self.world, "bench", "score", "b1", "--run", "loc1", "--split", "eval",
               "--bootstrap", 20)
        self.assertIn("inv1", str(ctx.exception))          # one run's scan is not every run's
        self.world.host("inv1", canary=host_scan(self.world, 2))
        doc = self.score("loc1", "--bootstrap", 20)
        self.assertEqual(doc["canary_check"]["basis"], "host reports")
        self.assertEqual(doc["canary_check"]["scanned"], 6)
        found = doc["gate_rejections"]["loc1"]
        self.assertEqual((found["attempts"], found["rejected"], found["dropped_lines"]), (2, 1, 1))
        self.assertEqual(set(found["by_reason"]), {"rule_5:absence_receipt"})
        self.assertIsNone(doc["gate_rejections"]["inv1"])
        self.world.host("inv1", canary=host_scan(self.world, 2, ["s1.jsonl"]))
        with self.assertRaises(CfError) as ctx:
            self.score("loc1", "--bootstrap", 20)
        self.assertIn("canary", str(ctx.exception))

    def test_challenge_runs_are_pinned_and_owed_rows_counted_item_70(self):
        eval_run(self.world, run_id="loc-ch", inventory_run="inv-ch", challenge_runs=["chl-1"])
        self.world.challenge_run("chl-1", of="loc-ch")
        path = WorkspacePaths(self.world.root).locate_outcomes_jsonl("loc-ch")
        rows = [json.loads(l) for l in path.read_text().splitlines()]
        rows[0]["challenge"] = None
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        doc = self.score("loc-ch")
        self.assertEqual(doc["pins"]["challenge_runs"], ["chl-1"])
        self.assertEqual(doc["runs"]["challenge"], ["chl-1"])
        self.assertEqual(doc["coverage"]["challenge_owed"]["total"], 1)
        self.assertIn("chl-1", doc["pins"]["receipts"])
        self.world.challenge_run("chl-1", of="loc-ch", gated=False)
        with self.assertRaises(CfError):
            self.score("loc-ch")
        (self.world.root / "reports" / "locate-tally.loc1.json").unlink()
        with self.assertRaises(CfError) as ctx:
            self.score("loc1")
        self.assertIn("no locate tally", str(ctx.exception))

    def test_a_planted_run_must_be_this_benchs_ablation_on_eval_item_78(self):
        eval_run(self.world, run_id="loc-p", inventory_run="inv-p",
                 tools={"kind": "planted", "sha256": "7" * 64})
        with self.assertRaises(CfError) as ctx:
            self.score("loc-p")
        self.assertIn("not this bench's planted one", str(ctx.exception))
        ablation = json.loads(layout_of(self.world).ablation_json.read_text())
        eval_run(self.world, run_id="loc-p2", inventory_run="inv-p2",
                 tools={"kind": "planted", "sha256": ablation["corpus_sha256"]})
        with self.assertRaises(CfError) as ctx:
            cf(self.world, "bench", "score", "b1", "--run", "loc-p2", "--split", "dev",
               "--no-canary-check", "--bootstrap", 20)
        self.assertIn("eval", str(ctx.exception))

    def test_no_other_packages_format_is_respelled_here_item_84(self):
        for path in sorted((SCRIPTS / "bench").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                for literal in ("cf-sourceset/1", "cf-corpus/1", "cf-locate/1", '"RECEIPT.json"'):
                    self.assertNotIn(literal, text)
                if path.name != "gold.py":       # plant() edits its own copy of the database
                    self.assertNotIn("SELECT ", text)
                    self.assertNotIn("sqlite3.connect", text)


# ---------------------------------------------------------------------------
# the package review's findings: one regression test per finding (review id)
# ---------------------------------------------------------------------------

class TestReviewMetrics(unittest.TestCase):

    def evaluate(self, tier, gold, run, *, state=None, ablated=None):
        return m.evaluate_tier(tier, gold, run, layers={"s": LAYER, "t": LAYER},
                               cluster_of={"s": "c1", "t": "c2"}, article_state=state or {},
                               assessment={"s": {"importance": "high"}}, ablated=ablated)

    def test_memory_is_counted_wherever_the_run_cites_a_removed_declaration_review_80(self):
        gold = [gold_passage("g0", "s", spn(0), tier="P", accept={("mathlib", "W.gone")}),
                gold_passage("g1", "t", None, tier="P", level="article",
                             accept={("mathlib", "W.gone2")})]
        run = [run_passage("s", "p5", spn(5), status="formalized",       # joins no gold passage
                           locations=[("mathlib", "W.gone", "exactMatch")]),
               run_passage("t", "p9", spn(9), status="formalized",
                           locations=[("mathlib", "W.gone2", "exactMatch")])]
        result = self.evaluate("P", gold, run, ablated={("mathlib", "W.gone"), ("mathlib", "W.gone2")})
        rate = result.ratios["memory_rate"]
        self.assertEqual((rate.num, rate.den), (2.0, 2.0))
        self.assertEqual(sorted((x["gold_id"], x["declaration"]) for x in result.misses),
                         [("g0", "mathlib:W.gone"), ("g1", "mathlib:W.gone2")])
        self.assertIn("memory_rate", result.strata["importance:high"])

    def test_no_locate_result_and_unresolved_articles_are_unresolved_review_83(self):
        gold = [gold_passage("g0", "s", spn(0)), gold_passage("g1", "s", spn(1)),
                gold_passage("g2", "t", spn(2))]
        run = [run_passage("s", "p0", spn(0), outcome=None),
               run_passage("s", "p1", spn(1), status="formalized")]
        r = self.evaluate("G3", gold, run, state={"t": "unresolved"}).ratios["unresolved_rate"]
        self.assertEqual((r.num, r.den), (2.0, 3.0))

    def test_article_level_g1_counts_only_gold_formalized_review_84(self):
        gold = [gold_passage("e0", "s", None, tier="G1", level="article", accept={("mathlib", "W.a")}),
                gold_passage("e1", "s", None, tier="G1", level="article", status="partial",
                             accept={("mathlib", "W.b")}),
                gold_passage("e2", "t", None, tier="G1", level="article", status=None,
                             accept={("mathlib", "W.c")})]
        run = [run_passage("s", "p", spn(3), status="partial", locations=[("mathlib", "W.a", "hasPart")])]
        result = self.evaluate("G1", gold, run)
        r = result.ratios["article_fnf_rate"]
        self.assertEqual((r.num, r.den), (1.0, 1.0))
        self.assertEqual([x["code"] for x in result.misses], ["E-WRONG-RELATION"])

    def test_two_gold_rows_of_one_passage_both_join_it_review_85(self):
        gold = [gold_passage("g1-a", "s", spn(1), tier="G1", accept={("mathlib", "W.a")}),
                gold_passage("g1-b", "s", spn(1), tier="G1", accept={("mathlib", "W.b")}),
                gold_passage("g1-c", "s", spn(3), tier="G1", accept={("mathlib", "W.c")})]
        whole = (spn(0)[0], spn(4)[1])
        run = [run_passage("s", "p", whole, status="formalized",
                           locations=[("mathlib", "W.a", "exactMatch"), ("mathlib", "W.b", "exactMatch")])]
        joined = m.join(gold, run, {"s": LAYER})
        self.assertEqual(sorted(joined), ["g1-a", "g1-b"])         # g1-c is another passage
        r = self.evaluate("G1", gold[:2], run).ratios
        self.assertEqual((r["fnf_rate"].num, r["recall_at_3"].num), (0.0, 2.0))

    def test_every_metric_is_stratified_review_87(self):
        gold = [gold_passage("g0", "s", spn(0), accept={("mathlib", "W.a")})]
        run = [run_passage("s", "p0", spn(0), status="formalized",
                           locations=[("mathlib", "W.a", "exactMatch")])]
        strata = self.evaluate("G3", gold, run).strata["importance:high"]
        self.assertLessEqual({"fnf_rate", "recall_at_1", "recall_at_3", "recall_at_5",
                              "precision_at_1", "overclaim_rate", "scope_error_rate",
                              "unresolved_rate", "inventory_recall", "library_recall"}, set(strata))


class TestReviewDrawsAndGold(unittest.TestCase):

    def test_a_suffixed_1000_yaml_key_joins_its_base_qid_review_41(self):
        world = fx.World(scratch(self) / "ws")
        path = world.write("inputs/1000x.yaml", "Q99999001X:\n  title: one\n  decl: Widget.mul_spanning\n"
                                                "Q99999001A:\n  title: two\n  decl: Widget.spanning_one\n")
        rows = gold_mod.read_curated_1000(path)
        self.assertEqual([(r["key"], r["qid"]) for r in rows],
                         [("Q99999001A", "Q99999001"), ("Q99999001X", "Q99999001")])
        self.assertEqual(draw_mod.read_qids(path), {"Q99999001"})
        conn = inputs.open_checked(world.paths, world.universe())
        try:
            resolved = gold_mod.resolve_g1([world.articles[world.sid(1)]["entry"]], conn,
                                           library="mathlib", commit=fx.COMMIT_M,
                                           lists=[("mathlib_1000", rows)], renames={},
                                           tag_libraries=["mathlib"])
        finally:
            conn.close()
        self.assertEqual(sorted(e for entry in resolved["entries"] for e in entry["evidence"]),
                         ["mathlib_1000 Q99999001A", "mathlib_1000 Q99999001X"])

    def test_nested_blocks_are_one_unit_of_the_span_frame_review_96(self):
        layer = "alpha beta gamma delta epsilon. one two three four five six."
        blocks = [{"span": [0, 31], "block": "li", "math": False},
                  {"span": [0, 31], "block": "p", "math": True},          # the li's own p
                  {"span": [32, 60], "block": "p"}]
        frame = draw_mod.span_frame(layer, blocks, [])
        self.assertEqual([(f["span"], f["block"], f["math"]) for f in frame],
                         [([0, 31], "li", True), ([32, 60], "p", False)])
        chosen = draw_mod.sample_spans("s", frame, 8, 1, 1.0)
        draw_mod.check_spans_disjoint(layer, chosen)
        self.assertEqual(chosen[0]["pi_span"], 1.0)

    def test_twins_are_the_whole_image_review_99(self):
        self.assertEqual(gold_mod.twins("mul_one"), ["add_zero"])
        self.assertEqual(gold_mod.twins("mul_zero"), [])
        self.assertEqual(gold_mod.twins("Prod.swap_swap"), [])
        self.assertEqual(gold_mod.twins("Submonoid.mul_mem"), ["AddSubmonoid.add_mem"])
        world = built(self)
        world.build_universe(fx.DECLARATIONS + tuple(
            ("mathlib", n, "Mathlib.Algebra.Group", "proved", None, ())
            for n in ("mul_one", "add_zero", "mul_zero")))
        path = world.write("inputs/1000m.yaml", "Q99999001:\n  title: one\n  decl: mul_one\n")
        cf(world, "bench", "anchor", "b1", "--universe", fx.UNIVERSE, "--curated-1000", path)
        doc = json.loads(layout_of(world).g1_resolved.read_text())
        self.assertEqual([(a["declaration"], a["why"]) for e in doc["entries"]
                          if "mathlib_1000" in e["via"] for a in e["accept"]],
                         [("add_zero", "to_additive"), ("mul_one", "curated")])

    def test_a_rename_widens_even_when_the_old_name_survives_review_100(self):
        world = built(self)
        world.build_universe(fx.DECLARATIONS + (
            ("mathlib", "Widget.old_name", "Mathlib.Widget.Basic", "proved", None, ()),))
        cf(world, "bench", "anchor", "b1", "--universe", fx.UNIVERSE, "--curated-1000",
           world.curated_1000(), "--rename-table", world.renames())
        doc = json.loads(layout_of(world).g1_resolved.read_text())
        three = [e for e in doc["entries"] if e["source_id"] == world.sid(3)][0]
        self.assertEqual([(a["declaration"], a["why"]) for a in three["accept"]],
                         [("Widget.new_name", "renamed"), ("Widget.old_name", "curated")])

    def test_a_refused_rebuild_writes_nothing_and_a_changed_sample_is_refused_review_95(self):
        world = anchored(self)
        lay = layout_of(world)
        files = (lay.frame_json, lay.draw_json, lay.spans_json, lay.bench_yaml)
        before = {p.name: p.read_bytes() for p in files}
        same = {"--uniform": 6, "--curated": 3, "--spans-per-article": 2, "--seed": 7}
        for change, needle in (({"--frame": world.frame_file([0, 1])}, "frame.json"),
                               ({"--spans-per-article": 1}, "spans.json"),
                               ({"--dev": 0.0, "--eval": 1.0}, "split fractions")):
            flags = {"--frame": world.frame_file(), **same, **change}
            if "--frame" in change:
                flags["--frame"] = world.frame_file([0, 1])
            with self.subTest(change=needle), self.assertRaises(CfError) as ctx:
                cf(world, "bench", "build", "b1", "--sourceset", fx.SOURCESET,
                   "--curated-qids", world.curated_1000(),
                   *[x for k, v in flags.items() for x in (k, v)])
            self.assertIn("another draw", str(ctx.exception))
            self.assertIn(needle, str(ctx.exception))
            self.assertEqual({p.name: p.read_bytes() for p in files}, before)

    def test_a_same_input_rebuild_keeps_what_anchoring_recorded_review_105(self):
        world = anchored(self)
        cf(world, "bench", "build", "b1", "--sourceset", fx.SOURCESET, "--frame", world.frame_file(),
           "--curated-qids", world.curated_1000(), "--uniform", 6, "--curated", 3,
           "--spans-per-article", 2, "--seed", 7)
        bench = inputs_bench(world)
        self.assertEqual((bench["library"], bench["state"]), ("mathlib", "anchored"))


class TestReviewLabeler(unittest.TestCase):

    def setUp(self):
        self.world = anchored(self)
        self.lay = layout_of(self.world)

    def emit(self, mode):
        cf(self.world, "bench", "anchor", "b1", "--emit-args", mode)
        return json.loads(self.lay.label_args(mode).read_text())

    def run_transcribe(self, args, labels, *extra):
        path = self.world.root / "r.json"
        path.write_text(json.dumps(fake_result(args, labels)))
        return cf(self.world, "bench", "anchor", "b1", "--transcribe", path, *extra)

    def anchor_label(self, task, words=6):
        return {"task_id": task["task_id"], "quote": self.world.quote(index_of(task["source_id"]), 0, words),
                "relation": "exactMatch", "passage_kind": "theorem", "evidence": "e"}

    def test_a_quote_of_fewer_than_four_whole_words_scores_at_article_level_review_86(self):
        args = self.emit("anchor")
        labels = [self.anchor_label(t, 3 if k == 0 else 6) for k, t in enumerate(args["tasks"])]
        self.run_transcribe(args, labels)
        rows = {r["gold_id"]: r for r in gold_mod.read_labels(self.lay, "G1")}
        first = rows[args["tasks"][0]["task_id"]]
        self.assertEqual(first["level"], "article")
        self.assertIn("whole layer word", first["anchor_problem"])

    def test_a_partial_anchor_run_keeps_the_earlier_labels_review_93(self):
        args = self.emit("anchor")
        self.run_transcribe(args, [self.anchor_label(t) for t in args["tasks"]])
        before = {r["gold_id"]: r for r in gold_mod.read_labels(self.lay, "G1")}
        self.run_transcribe(args, [self.anchor_label(args["tasks"][0], 7)])
        after = {r["gold_id"]: r for r in gold_mod.read_labels(self.lay, "G1")}
        self.assertEqual(set(after), set(before))
        for task in args["tasks"][1:]:
            self.assertEqual(after[task["task_id"]], before[task["task_id"]])
            self.assertEqual(after[task["task_id"]]["level"], "passage")

    def test_a_partial_calibration_keeps_its_rows_and_reports_no_h_review_98(self):
        args = self.emit("anchor")
        self.run_transcribe(args, [self.anchor_label(t) for t in args["tasks"]])
        cal_args = self.emit("calibrate")
        g1 = {r["gold_id"]: r for r in gold_mod.read_labels(self.lay, "G1")}

        def label(task, hit):
            decl = g1[task["task_id"]]["accept"][0] if hit else {"library": "alpha",
                                                                 "declaration": "Alpha.unrelated"}
            return {"task_id": task["task_id"], "scope": "formal_math",
                    "declarations": [dict(decl, relation="exactMatch")], "evidence": "e",
                    "hunters": [{"hunter": 1, "found": [f"{decl['library']}:{decl['declaration']}"]}]}

        tasks = cal_args["tasks"]
        self.run_transcribe(cal_args, [label(t, True) for t in tasks[1:]])
        cal = json.loads(self.lay.calibration_json.read_text())
        self.assertEqual((cal["found"], cal["total"], cal["h"]), (len(tasks) - 1, len(tasks), None))
        self.assertEqual(cal["unanswered"], [tasks[0]["task_id"]])
        transcribe(self.world, "label", lambda t: {"task_id": t["task_id"], "scope": "non_math",
                                                   "declarations": [], "evidence": "e"})
        with self.assertRaises(CfError) as ctx:
            cf(self.world, "bench", "freeze", "b1")
        self.assertIn("h is not over the anchored G1 passages", str(ctx.exception))
        self.run_transcribe(cal_args, [label(tasks[0], False)])
        cal = json.loads(self.lay.calibration_json.read_text())
        self.assertEqual((cal["found"], cal["total"]), (len(tasks) - 1, len(tasks)))
        self.assertAlmostEqual(cal["h"], (len(tasks) - 1) / len(tasks))
        cf(self.world, "bench", "freeze", "b1")

    def test_the_manifest_names_the_labeler_that_wrote_the_rows_review_102_104(self):
        args = self.emit("anchor")
        self.run_transcribe(args, [self.anchor_label(t) for t in args["tasks"]])
        transcribe(self.world, "label", lambda t: {"task_id": t["task_id"], "scope": "non_math",
                                                   "declarations": [], "evidence": "e"})
        sha = args["labeler"]["workflow_sha256"]
        with mock.patch.object(gold_mod, "workflow_sha256", return_value="f" * 64):
            self.emit("anchor")                       # re-emitted, never transcribed
        cf(self.world, "bench", "freeze", "b1")
        manifest = manifest_of(self.world)
        self.assertEqual(manifest["labeler"]["workflow_sha256"]["anchor"], sha)
        # a later checkout re-freezes unchanged gold as unchanged
        with mock.patch("locate.export.skill_commit", return_value="9" * 40):
            self.assertIn("nothing changed", cf(self.world, "bench", "freeze", "b1"))
        # a partial transcription by another labeler is refused, not mixed in
        with mock.patch.object(gold_mod, "workflow_sha256", return_value="f" * 64):
            other = self.emit("anchor")
            with self.assertRaises(CfError) as ctx:
                self.run_transcribe(other, [self.anchor_label(other["tasks"][0])], "--supersede")
        self.assertIn("another labeler", str(ctx.exception))

    def test_re_resolving_g1_prunes_its_labels_and_freeze_wants_every_entry_review_103(self):
        args = self.emit("anchor")
        self.run_transcribe(args, [self.anchor_label(t) for t in args["tasks"][:1]])
        transcribe(self.world, "label", lambda t: {"task_id": t["task_id"], "scope": "non_math",
                                                   "declarations": [], "evidence": "e"})
        with self.assertRaises(CfError) as ctx:
            cf(self.world, "bench", "freeze", "b1")
        self.assertIn("resolved entr(ies) with no label", str(ctx.exception))
        small = self.world.write("inputs/1000s.yaml", "Q99999000:\n  title: zero\n  decl: Widget.spanning_zero\n")
        cf(self.world, "bench", "anchor", "b1", "--universe", fx.UNIVERSE, "--curated-1000", small)
        keep = {e["entry_id"] for e in json.loads(self.lay.g1_resolved.read_text())["entries"]}
        self.assertTrue({r["gold_id"] for r in gold_mod.read_labels(self.lay, "G1")} <= keep)

    def test_a_task_labelled_twice_is_refused_review_106(self):
        args = self.emit("anchor")
        label = self.anchor_label(args["tasks"][0])
        with self.assertRaises(CfError) as ctx:
            self.run_transcribe(args, [label, dict(label)])
        self.assertIn("more than once", str(ctx.exception))


class TestReviewScore(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.template = frozen_at(cls.tmp / "template")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        root = scratch(self) / "ws"
        shutil.copytree(self.template.root, root)
        self.world = clone_world(self.template, root)
        self.eval = eval_run(self.world)

    def test_a_canary_check_that_read_nothing_is_refused_review_79(self):
        empty = scratch(self) / "empty"
        empty.mkdir()
        bad = scratch(self) / "bad"
        bad.mkdir()
        (bad / "a.jsonl.gz").write_bytes(b"not gzip at all")
        for args, needle in (((("--transcripts", scratch(self) / "typo"),), "no such file"),
                             ((("--transcripts", empty),), "read no transcript"),
                             ((("--transcripts", bad),), "cannot read transcript")):
            with self.subTest(needle=needle), self.assertRaises(CfError) as ctx:
                score(self.world, "loc1", *args[0])
            self.assertIn(needle, str(ctx.exception))
        for doc in ({}, {"scanned": 0, "hits": []}, {"scanned": 3}):
            report = self.world.write("inputs/scan.json", json.dumps(doc))
            with self.subTest(doc=doc), self.assertRaises(CfError) as ctx:
                score(self.world, "loc1", "--canary-report", report)
            self.assertIn("read anything", str(ctx.exception))
        unchecked = score(self.world, "loc1")
        self.assertEqual(compare_mod.claimable(dict(unchecked, coverage={"articles": 500}))[0], False)

    def test_the_planted_tier_and_the_canary_are_verified_review_81_94(self):
        lay = layout_of(self.world)
        doc = json.loads(lay.ablation_json.read_text())
        frozen_planted = lay.planted_db.read_bytes()
        doc["removed"] = []
        lay.ablation_json.write_text(json.dumps(doc))
        with self.assertRaises(CfError) as ctx:
            score(self.world)
        self.assertIn("planted tier changed after freeze", str(ctx.exception))
        shutil.copytree(self.template.root / "benches", self.world.root / "benches", dirs_exist_ok=True)
        from cflib.yamlio import read_yaml, write_yaml

        bench = read_yaml(lay.bench_yaml)
        write_yaml(lay.bench_yaml, dict(bench, canary="cf-bench-canary-" + "0" * 32))
        with self.assertRaises(CfError) as ctx:
            score(self.world)
        self.assertIn("canary", str(ctx.exception))
        write_yaml(lay.bench_yaml, bench)
        # a refused freeze --plant leaves the frozen planted tier as it was
        g3 = lay.label_file("G3")
        g3.write_text(g3.read_text().replace('"theorem"', '"definition"', 1))
        with self.assertRaises(CfError):
            cf(self.world, "bench", "freeze", "b1", "--plant")
        self.assertEqual(lay.planted_db.read_bytes(), frozen_planted)
        self.assertFalse(lay.planted_dir.with_name("planted.staging").exists())

    def test_spelling_variants_of_one_item_are_one_passage_review_90(self):
        eval_run(self.world, run_id="loc-v", inventory_run="inv-v")
        path = WorkspacePaths(self.world.root).locate_outcomes_jsonl("loc-v")
        rows = [json.loads(l) for l in path.read_text().splitlines()]
        for row in rows:
            row["item"] = row["item"].replace("In-text #", "In-text#")
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        doc = score(self.world, "loc-v")
        self.assertEqual(doc["coverage"]["run_passages"], len(rows))

    def test_costs_that_are_not_dollars_are_refused_review_91(self):
        for value in ("NaN", "-1", "\"twelve\""):
            costs = self.world.write("inputs/costs.json",
                                     "{" + ", ".join(f'"{s}": {value}' for s in self.eval) + "}")
            with self.subTest(value=value), self.assertRaises(CfError) as ctx:
                score(self.world, "loc1", "--no-canary-check", "--article-costs", costs)
            self.assertIn("not a finite non-negative number of dollars", str(ctx.exception))
        receipt = WorkspacePaths(self.world.root).receipt_json("loc1")
        doc = json.loads(receipt.read_text())
        doc["cost"].pop("usd")
        receipt.write_text(json.dumps(doc))
        with self.assertRaises(CfError):
            score(self.world)

    def test_the_renderer_pin_is_the_runs_review_115(self):
        from sources import render_html

        other = "web-html/1"                       # the renderer before the current one
        self.assertNotEqual(other, render_html.RENDERER)
        doc = score(self.world)
        self.assertEqual(doc["pins"]["renderer"], [render_html.RENDERER])
        eval_run(self.world, run_id="loc-r", inventory_run="inv-r")
        path = self.world.root / "corpus" / "inv-r" / "t-inv" / f"{self.eval[0]}.json"
        inv = json.loads(path.read_text())
        path.write_text(json.dumps(dict(inv, renderer=other)))
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc-r")
        self.assertIn(f"renderer {other}", str(ctx.exception))
        inv.pop("renderer")
        path.write_text(json.dumps(inv))
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc-r")
        self.assertIn("names no renderer", str(ctx.exception))

    def test_the_score_writes_strata_for_p_and_every_metric_review_87(self):
        ablation = json.loads(layout_of(self.world).ablation_json.read_text())
        passages = {sid: [fx.passage(self.world, index_of(sid), 0, f"In-text #{index_of(sid):08x}")]
                    for sid in self.eval}
        outs = [fx.outcome(self.world, index_of(sid), f"In-text #{index_of(sid):08x}",
                           status="formalized", outcome_="located", run_id="locp2",
                           inventory_run="invp2", k=0,
                           locations=[("mathlib", ablation["removed"][0].split(":", 1)[1], "exactMatch")])
                for sid in self.eval]
        self.world.run("locp2", inventory_run="invp2", passages=passages, outcomes=outs,
                       tools={"kind": "planted", "sha256": ablation["corpus_sha256"]})
        doc = score(self.world, "locp2")
        self.assertTrue(any("memory_rate" in group for group in doc["strata"]["P"].values()))
        full = score(self.world)
        names = {name for group in full["strata"]["G3"].values() for name in group}
        self.assertIn("unresolved_rate", names)


class TestReviewCompare(unittest.TestCase):

    def setUp(self):
        self.world = fx.World(scratch(self) / "ws", articles=1)
        self.lay = layout_of(self.world)
        self.lay.root.mkdir(parents=True)
        n = 40
        self.base_cells = {f"c{i:02d}": [1.0 if i % 2 else 0.0, 1.0] for i in range(n)}
        self.better_cells = {f"c{i:02d}": [1.0 if i % 8 == 1 else 0.0, 1.0] for i in range(n)}

    def write(self, name, doc, lay=None) -> Path:
        path = (lay or self.lay).root / f"{name}.json"
        path.write_text(json.dumps(doc))
        return path

    def compare(self, base, cand, aa=None, lay=None, **kw):
        return compare_mod.compare(lay or self.lay, base, cand, claim=kw.pop("claim", "G3.fnf_rate"),
                                   aa_path=aa, replicates=kw.pop("replicates", 1000), seed=1, **kw)

    def test_the_aa_must_be_a_distinct_replicate_of_the_baseline_review_109(self):
        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells))
        with self.assertRaises(CfError) as ctx:
            self.compare(base, cand, base)
        self.assertIn("own run", str(ctx.exception))
        other = self.write("aa", score_doc(self.base_cells, mid="r",
                                           pins={"skill_commit": {"loc-r": "9" * 40}}))
        with self.assertRaises(CfError) as ctx:
            self.compare(base, cand, other)
        self.assertIn("skill_commit", str(ctx.exception))

    def test_a_replication_names_another_benchs_accepted_claim_review_110(self):
        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells, articles=150))
        aa = self.write("aa", score_doc(self.base_cells, mid="r"))
        first = self.compare(base, cand, aa)
        self.assertEqual((first["decision"], first["claimed"]), ("accept", True))
        original = {**first, "bench": "b1"}
        with self.assertRaises(CfError) as ctx:          # within the same bench
            self.compare(base, cand, aa, replication=original)
        self.assertIn("another bench", str(ctx.exception))
        fresh = BenchLayout(self.lay.root.parent / "b2", "b2")
        fresh.root.mkdir()
        docs = {name: dict(json.loads(path.read_text()), bench="b2")
                for name, path in (("base", base), ("cand", cand), ("aa", aa))}
        paths = {name: self.write(name, doc, fresh) for name, doc in docs.items()}
        rep = self.compare(paths["base"], paths["cand"], paths["aa"], lay=fresh, replication=original)
        self.assertEqual((rep["alpha_j"], rep["replicates"]), (0.05, {"bench": "b1",
                                                                       "entry_id": first["entry_id"]}))
        with self.assertRaises(CfError) as ctx:
            self.compare(paths["base"], paths["cand"], paths["aa"], lay=fresh, replication=original)
        self.assertIn("already replicates", str(ctx.exception))
        self.assertEqual(compare_mod.replicated_entry(self.lay, first["entry_id"])["entry_id"],
                         first["entry_id"])
        parser = __import__("argparse").ArgumentParser()
        from cfcmd import bench as bench_cmd

        bench_cmd.register(parser.add_subparsers())
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["bench", "compare", "b1", "a", "b", "--claim", "G3.fnf_rate",
                               "--ledger", "x"])

    def test_the_ledger_is_read_and_written_under_its_lock_review_111(self):
        import fcntl
        import os

        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells))
        seen = []
        real = compare_mod.read_jsonl_rows

        def probe(path):
            fd = os.open(str(self.lay.ledger_jsonl) + ".lock", os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen.append("free")
                fcntl.flock(fd, fcntl.LOCK_UN)
            except BlockingIOError:
                seen.append("held")
            finally:
                os.close(fd)
            return real(path)

        self.compare(base, cand)
        with mock.patch.object(compare_mod, "read_jsonl_rows", side_effect=probe):
            self.compare(base, cand)
        self.assertEqual(seen, ["held"])
        self.assertEqual(len(self.lay.ledger_jsonl.read_text().splitlines()), 2)

    def test_safety_with_no_baseline_rate_is_judged_on_the_candidates_review_112(self):
        base_doc = score_doc(self.base_cells)
        del base_doc["per_cluster"]["G3"]["overclaim_rate"]
        base = self.write("base", base_doc)
        cand = self.write("cand", score_doc(self.better_cells,
                                            overclaim={c: [1.0, 1.0] for c in self.base_cells}))
        entry = self.compare(base, cand)
        self.assertIn("safety", entry["failed"])
        self.assertFalse(entry["conditions"]["safety"]["metrics"]["G3.overclaim_rate"]["ok"])

    def test_a_zero_cost_baseline_is_not_a_pass_review_113(self):
        base = self.write("base", score_doc(self.base_cells, cost=0.0))
        dear = self.write("dear", score_doc(self.better_cells, cost=5.0))
        self.assertIn("cost", self.compare(base, dear)["failed"])
        free = self.write("free", score_doc(self.better_cells, cost=0.0))
        self.assertNotIn("cost", self.compare(base, free)["failed"])

    def test_strata_of_every_metric_and_the_unseen_stratum_are_held_review_114(self):
        good = {"den": 40, "n": 40, "by_cluster": {c: [0.0, 1.0] for c in self.base_cells}}
        bad = {"den": 40, "n": 40, "by_cluster": {c: [1.0, 1.0] for c in self.base_cells}}
        recall = {c: [1.0 if i % 2 else 0.0, 1.0] for i, c in enumerate(sorted(self.base_cells))}
        better = {c: [1.0, 1.0] for c in self.base_cells}

        def doc(recall_cells, fnf_stratum, mid=None):
            d = score_doc(self.base_cells, mid=mid,
                          strata={"importance:high": {"fnf_rate": fnf_stratum}})
            d["per_cluster"]["G3"]["recall_at_3"] = recall_cells
            d["strata"]["G3"]["seen:no"]["recall_at_3"] = {"den": 40, "n": 40,
                                                           "by_cluster": recall_cells}
            return d

        base = self.write("b", doc(recall, good, "b"))
        cand = self.write("c", doc(better, bad, "c"))
        entry = self.compare(base, cand, claim="G3.recall_at_3")
        self.assertTrue(entry["conditions"]["claim"]["ok"])
        self.assertIn("importance:high.fnf_rate", entry["conditions"]["strata"]["checked"])
        self.assertIn("strata", entry["failed"])
        no_unseen = self.write("nu", score_doc(self.better_cells, seen_no=False))
        entry = self.compare(self.write("b2", score_doc(self.base_cells, seen_no=False)), no_unseen)
        self.assertIn("claim_unseen", entry["failed"])

    def test_planted_against_full_and_two_price_tables_review_116(self):
        base = self.write("base", score_doc(self.base_cells, pins={"price_table_sha256": ["a" * 64]}))
        planted = score_doc(self.better_cells, pins={"price_table_sha256": ["a" * 64]})
        planted["tiers"] = {"P": {}}
        with self.assertRaises(CfError):
            self.compare(base, self.write("p", planted))
        other = self.write("o", score_doc(self.better_cells, pins={"price_table_sha256": ["b" * 64],
                                                                   "corpus_db_sha256": "z"}))
        entry = self.compare(base, other)
        self.assertEqual(entry["conditions"]["cost"]["why"],
                         "the two runs were priced from different price tables")
        self.assertIn("corpus_db_sha256 differs", entry["differences"])

    def test_an_alpha_the_bootstrap_cannot_resolve_or_out_of_range_is_refused_review_117_118(self):
        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells))
        with self.assertRaises(CfError) as ctx:
            self.compare(base, cand, replicates=100)
        self.assertIn("--bootstrap", ctx.exception.hint or "")
        for alpha in (1.0, 0.5, 0.0, -1.0):
            with self.subTest(alpha=alpha), self.assertRaises(CfError):
                self.compare(base, cand, alpha=alpha)


# ---------------------------------------------------------------------------
# fix round 3: the four-lens review's findings (finding id in each test's name)
# ---------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class TestRound3Score(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.template = frozen_at(cls.tmp / "template")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        root = scratch(self) / "ws"
        shutil.copytree(self.template.root, root)
        self.world = clone_world(self.template, root)
        self.eval = eval_run(self.world)
        self.lay = layout_of(self.world)

    def planted_run(self, run_id: str, inventory_run: str) -> None:
        ablation = json.loads(self.lay.ablation_json.read_text())
        removed = ablation["removed"][0].split(":", 1)[1]
        passages = {sid: [fx.passage(self.world, index_of(sid), 0, f"In-text #{index_of(sid):08x}")]
                    for sid in self.eval}
        outs = [fx.outcome(self.world, index_of(sid), f"In-text #{index_of(sid):08x}",
                           status="formalized", outcome_="located", run_id=run_id,
                           inventory_run=inventory_run, k=0,
                           locations=[("mathlib", removed, "exactMatch")]) for sid in self.eval]
        self.world.run(run_id, inventory_run=inventory_run, passages=passages, outcomes=outs,
                       tools=self.planted_tools())

    def planted_tools(self) -> dict:
        return {"kind": "planted",
                "sha256": json.loads(self.lay.ablation_json.read_text())["corpus_sha256"]}

    def test_an_aa_replicate_is_its_configuration_not_its_rendered_bytes_m01(self):
        eval_run(self.world, run_id="loc2", inventory_run="inv2")
        one, two = score(self.world, "loc1"), score(self.world, "loc2")
        # the rendered prompts and staged gate.json name each run; the configuration does not
        self.assertNotEqual({gold_mod.sha_of(v) for v in one["pins"]["rendered_prompts"].values()},
                            {gold_mod.sha_of(v) for v in two["pins"]["rendered_prompts"].values()})
        self.assertNotEqual(set(one["pins"]["rendered_gate_files"].values()),
                            set(two["pins"]["rendered_gate_files"].values()))
        self.assertEqual(one["pins"]["gate_files"]["loc1"], gold_mod.sha_of(fx.CONFIG_SHAS["gate_files"]))
        compare_mod.check_replicate(one, two)
        paths = [self.lay.score_json(d["score_id"]) for d in (one, two)]
        entry = compare_mod.compare(self.lay, paths[0], paths[1], claim="G3.fnf_rate",
                                    aa_path=paths[1], replicates=1000, seed=1)
        self.assertEqual(entry["conditions"]["aa"]["replicate"], two["measurement_id"])
        # another configuration is refused, by the pin that differs
        other = {**fx.CONFIG_SHAS, "prompts": {"locate.md.tmpl": "f" * 64}}
        eval_run(self.world, run_id="loc3", inventory_run="inv3", config_shas=other)
        with self.assertRaises(CfError) as ctx:
            compare_mod.check_replicate(one, score(self.world, "loc3"))
        self.assertIn("prompts differ", str(ctx.exception))
        # a plan that recorded no configuration cannot be an A/A replicate, nor have one
        eval_run(self.world, run_id="loc4", inventory_run="inv4", config_shas=None)
        old = score(self.world, "loc4")
        self.assertEqual(set(old["pins"]["gate_files"].values()), {"unrecorded"})
        for base, rep in ((one, old), (old, one)):
            with self.subTest(base=base["run"]), self.assertRaises(CfError) as ctx:
                compare_mod.check_replicate(base, rep)
            self.assertIn("locate.config_shas", str(ctx.exception))
            self.assertIn("re-plan", ctx.exception.hint or "")

    def test_outcomes_tallied_before_the_last_harvest_are_refused_m02(self):
        first = score(self.world)
        receipt = self.world.paths.receipt_json("loc1")
        self.assertEqual(first["pins"]["receipts"]["loc1"], sha256_of(receipt))
        # a re-harvest after the tally: the outcome rows describe the earlier documents
        doc = json.loads(receipt.read_text())
        receipt.write_text(json.dumps(dict(doc, harvested_at="2026-10-19T00:00:00Z")))
        with self.assertRaises(CfError) as ctx:
            score(self.world)
        self.assertIn("not built from the receipts on disk: loc1", str(ctx.exception))
        self.assertIn("cf tally --locate --run loc1", ctx.exception.hint or "")
        self.world.tally("loc1", "inv1", [])
        self.assertEqual(score(self.world)["pins"]["receipts"]["loc1"], sha256_of(receipt))
        # a tally from before the receipt shas were recorded is no evidence either way
        tally = self.world.paths.locate_tally_json("loc1")
        old = json.loads(tally.read_text())
        for gate in old["gates"].values():
            gate.pop("receipt_sha256", None)
        tally.write_text(json.dumps(old))
        with self.assertRaises(CfError) as ctx:
            score(self.world)
        self.assertIn("no receipt sha recorded", str(ctx.exception))
        # the producer: cf tally --locate records each read run's receipt sha
        cf(self.world, "tally", "--locate", "--run", "loc1")
        real = json.loads(tally.read_text())
        shas = {rid: sha256_of(self.world.paths.receipt_json(rid)) for rid in ("inv1", "loc1")}
        self.assertEqual({rid: real["gates"][rid]["receipt_sha256"] for rid in shas}, shas)
        score_mod.check_tally(real, "loc1", shas)
        with self.assertRaises(CfError):
            score_mod.check_tally(real, "loc1", {**shas, "inv1": "0" * 64})

    def test_the_tally_carries_and_says_what_an_inventorys_skips_removed_g08(self):
        """G-08's tally side: harvest counts what the skips left unkept (the receipt's
        ``gate.skipped``), and the tally of the inventory, or of a locate run of it,
        carries it in its gates and prints it."""
        sid = self.eval[0]
        skipped = {"skips": 2, "chars": 840, "math_paragraphs": 3, "finder_passages": 1,
                   "by_source": {sid: {"skips": 2, "chars": 840, "math_paragraphs": 3,
                                       "finder_passages": 1}}}
        receipt = self.world.paths.receipt_json("inv1")
        doc = json.loads(receipt.read_text())
        doc["gate"] = {**doc["gate"], "skipped": skipped}
        receipt.write_text(json.dumps(doc))
        said = (f"locate-inventory run inv1: 2 skip(s) cover 840 character(s), 3 math "
                f"paragraph(s) and 1 finder passage(s) that no passage keeps")
        out = cf(self.world, "tally", "--locate", "--run", "loc1")
        tally = json.loads(self.world.paths.locate_tally_json("loc1").read_text())
        self.assertEqual(tally["gates"]["inv1"]["skipped"], skipped)
        self.assertNotIn("skipped", tally["gates"]["loc1"])        # a locate receipt counts none
        self.assertIn(said, out)
        self.assertIn(f"  {sid}: 2 skip(s), 840 character(s), 3 math paragraph(s), "
                      f"1 finder passage(s)", out)
        self.assertIn(said, cf(self.world, "tally", "--locate", "--run", "inv1"))
        # a receipt whose skips removed nothing, or that counted none, says nothing
        doc["gate"]["skipped"] = {**skipped, "skips": 0, "by_source": {}}
        receipt.write_text(json.dumps(doc))
        self.assertNotIn("skip(s) cover", cf(self.world, "tally", "--locate", "--run", "loc1"))

    def test_a_challenge_that_required_nothing_is_scored_without_a_receipt_c06(self):
        self.world.challenge_run("chl-0", of="loc1", required=0)
        self.assertFalse(self.world.paths.receipt_json("chl-0").is_file())
        doc = score(self.world)
        self.assertEqual(doc["runs"]["challenge"], ["chl-0"])
        self.assertNotIn("chl-0", doc["pins"]["receipts"])
        self.assertEqual(doc["pins"]["challenge_plans"],
                         {"chl-0": sha256_of(self.world.paths.challenge_plan_json("chl-0"))})
        self.assertEqual(doc["cost"]["by_stage"]["challenge"], 0.0)
        self.assertNotIn("chl-0", doc["cost"]["runs"])
        # a challenge that required a record still needs its gated harvest
        self.world.challenge_run("chl-1", of="loc1", required=1)
        self.world.paths.receipt_json("chl-1").unlink()
        self.world.tally("loc1", "inv1", ["chl-0", "chl-1"])
        with self.assertRaises(CfError) as ctx:
            score(self.world)
        self.assertIn("chl-1", str(ctx.exception))
        self.assertIn("no RECEIPT.json", str(ctx.exception))

    def test_a_planted_score_refuses_a_challenge_of_another_database_c03(self):
        self.planted_run("locp", "invp")
        self.world.challenge_run("chl-full", of="locp")
        with self.assertRaises(CfError) as ctx:
            score(self.world, "locp")
        self.assertIn("chl-full", str(ctx.exception))
        self.assertIn("--tools-corpus-db", ctx.exception.hint or "")
        self.planted_run("locq", "invq")
        self.world.challenge_run("chl-planted", of="locq", tools=self.planted_tools())
        self.assertEqual(score(self.world, "locq")["runs"]["challenge"], ["chl-planted"])
        # a challenge whose plan required nothing ran no hunter, whichever database it names
        self.planted_run("locr", "invr")
        self.world.challenge_run("chl-none", of="locr", required=0)
        doc = score(self.world, "locr")
        self.assertEqual(doc["runs"]["challenge"], ["chl-none"])
        self.assertIn("chl-none", doc["pins"]["challenge_plans"])

    def test_the_planted_tier_is_stratified_by_seen_as_every_tier_is_m03(self):
        self.planted_run("locp", "invp")
        doc = score(self.world, "locp")
        seen = set(manifest_of(self.world).get("seen") or [])
        keys = set(doc["strata"]["P"])
        self.assertTrue({"seen:no", "seen:yes"} & keys, keys)
        g1 = [json.loads(l) for l in self.lay.label_file("G1").read_text().splitlines()]
        p_ids = {r["gold_id"] for r in g1 if r["source_id"] in self.eval}
        if p_ids - seen:
            self.assertIn("seen:no", keys)
            path = self.lay.score_json(doc["score_id"])
            entry = compare_mod.compare(self.lay, path, path, claim="P.memory_rate",
                                        replicates=1000, seed=1)
            self.assertNotIn("why", entry["conditions"]["claim_unseen"])

    def test_strata_count_their_units_beside_the_weighted_denominator_m04(self):
        doc = score(self.world)
        cells = [cell for group in doc["strata"]["G3"].values() for name, cell in group.items()
                 if name == "inventory_recall"]
        self.assertTrue(cells)
        for cell in cells:
            self.assertIsInstance(cell["n"], int)
            self.assertLess(cell["n"], cell["den"])        # every span weighs 1/(pi_span*pi) > 1

    def test_a_canary_scan_names_its_canaries_and_the_score_wants_its_own_m06(self):
        from run import canary_scan

        canaries = self.lay.host_canaries
        transcript = scratch(self) / "t.jsonl"
        transcript.write_text('{"text": "nothing"}\n')
        out = scratch(self) / "canary.json"
        self.assertEqual(canary_scan.main(["--canaries", str(canaries), "--out", str(out),
                                           str(transcript)]), 0)
        report = json.loads(out.read_text())
        canary = canaries.read_text().strip()
        self.assertEqual(report["canary_sha256"], [sha256_of_text(canary)])
        self.assertTrue(score(self.world, "loc1", "--canary-report", out)["canary_check"]["ran"])
        for doc, needle in (({**report, "canary_sha256": [sha256_of_text("cf-bench-canary-other")]},
                             "looked for 1 other canary"),
                            ({k: v for k, v in report.items() if k != "canary_sha256"},
                             "names none of the canaries")):
            path = self.world.write("inputs/scan.json", json.dumps(doc))
            with self.subTest(needle=needle), self.assertRaises(CfError) as ctx:
                score(self.world, "loc1", "--canary-report", path)
            self.assertIn(needle, str(ctx.exception))
            self.assertIn("host/canaries.txt", ctx.exception.hint or "")
        # the harvested host reports are held to the same
        self.world.host("loc1", canary={**report, "canary_sha256": []})
        self.world.host("inv1", canary=report)
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc1", "--bootstrap", 20)
        self.assertIn("runs/loc1/host/canary.json", str(ctx.exception))

    def test_the_two_canary_shas_are_the_two_the_docs_name(self):
        """A host scan's ``canary_sha256`` hashes the raw line; the manifest's, its JSON spelling.

        references/locate-bench.md sections 7 and 8 say so, because the two share a
        name: a reader matching a host report against the manifest must hash the line.
        """
        from cflib import hashing
        from run import canary_scan

        canary = self.lay.host_canaries.read_text(encoding="utf-8").strip()
        manifest = manifest_of(self.world)
        self.assertEqual(manifest["canary_sha256"], hashing.sha256_json(canary))
        self.assertEqual(canary_scan.canary_sha256(canary), sha256_of_text(canary))
        self.assertNotEqual(manifest["canary_sha256"], canary_scan.canary_sha256(canary))
        text = " ".join((SKILL / "references" / "locate-bench.md").read_text(encoding="utf-8")
                        .split())
        self.assertIn("It is **not** the sha a host canary scan lists", text)
        self.assertIn("Those shas are of the raw bytes, not the gold manifest's `canary_sha256`",
                      text)

    def test_the_audit_file_is_named_by_the_score_id_wherever_the_doc_names_it(self):
        doc = score(self.world)
        self.assertEqual(doc["audit"]["file"], f"audits/{doc['score_id']}.jsonl")
        self.assertTrue(self.lay.audit_jsonl(doc["score_id"]).is_file())
        text = (SKILL / "references" / "locate-bench.md").read_text(encoding="utf-8")
        named = set(re.findall(r"audits/(<[^`\s]*?>(?:-<[^`\s]*?>)*)\.jsonl", text))
        self.assertEqual(named, {"<score_id>", "<split>-<id16>-<s8>"})

    def test_re_scoring_another_way_writes_another_document_m07_m05(self):
        spread = score(self.world)
        costs = self.world.write("inputs/costs.json", json.dumps({s: 500.0 for s in self.eval}))
        priced = score(self.world, "loc1", "--no-canary-check", "--article-costs", costs)
        self.assertEqual(spread["measurement_id"], priced["measurement_id"])
        self.assertNotEqual(spread["score_id"], priced["score_id"])
        self.assertNotEqual(spread["scoring"]["digest"], priced["scoring"]["digest"])
        self.assertEqual(priced["scoring"]["inputs"]["article_costs_sha256"], sha256_of(costs))
        kept = json.loads(self.lay.score_json(spread["score_id"]).read_text())
        self.assertEqual(kept["cost"]["basis"], spread["cost"]["basis"])
        self.assertEqual(score(self.world)["score_id"], spread["score_id"])     # the same way, the same file
        # M-05: the two cost bases are no comparison of cost
        entry = compare_mod.compare(self.lay, self.lay.score_json(spread["score_id"]),
                                    self.lay.score_json(priced["score_id"]), claim="G3.fnf_rate",
                                    replicates=1000, seed=1)
        self.assertEqual(entry["conditions"]["cost"]["why"],
                         "the two scores price articles on different bases")
        self.assertIn("cost", entry["failed"])
        self.assertEqual((entry["baseline_score"], entry["candidate_score"]),
                         (spread["score_id"], priced["score_id"]))


def sha256_of_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestRound3Compare(unittest.TestCase):

    def setUp(self):
        self.world = fx.World(scratch(self) / "ws", articles=1)
        self.lay = layout_of(self.world)
        self.lay.root.mkdir(parents=True)
        n = 40
        self.base_cells = {f"c{i:02d}": [1.0 if i % 2 else 0.0, 1.0] for i in range(n)}
        self.better_cells = {f"c{i:02d}": [1.0 if i % 8 == 1 else 0.0, 1.0] for i in range(n)}

    def write(self, name, doc) -> Path:
        path = self.lay.root / f"{name}.json"
        path.write_text(json.dumps(doc))
        return path

    def test_the_strata_rule_counts_spans_not_their_weights_m04(self):
        # two spans of weight 93 each: a denominator of 186, a stratum of n = 2
        def low(second):
            return {"importance:low": {"inventory_recall": {
                "den": 186.0, "n": 2, "by_cluster": {"c00": [93.0, 93.0], "c01": [second, 93.0]}}}}

        base = self.write("b", score_doc(self.base_cells, strata=low(93.0)))
        cand = self.write("c", score_doc(self.better_cells, strata=low(0.0)))
        entry = compare_mod.compare(self.lay, base, cand, claim="G3.fnf_rate", replicates=1000,
                                    seed=1)
        self.assertNotIn("importance:low.inventory_recall", entry["conditions"]["strata"]["checked"])
        self.assertNotIn("strata", entry["failed"])
        # a stratum that counts nothing is refused, never read as small
        doc = score_doc(self.better_cells, strata=low(0.0), mid="nocount")
        del doc["strata"]["G3"]["importance:low"]["inventory_recall"]["n"]
        with self.assertRaises(CfError) as ctx:
            compare_mod.compare(self.lay, base, self.write("x", doc), claim="G3.fnf_rate",
                                replicates=1000, seed=1)
        self.assertIn("counts no units", str(ctx.exception))

    def test_two_cost_bases_fail_the_cost_condition_m05(self):
        spread = score_doc(self.base_cells, cost=3.0, locate_cost=3.0)
        spread["cost"]["basis"] = "each run's receipt spread over the sources it covered"
        priced = score_doc(self.better_cells, cost=3.0, locate_cost=3.0)
        entry = compare_mod.compare(self.lay, self.write("s", spread), self.write("p", priced),
                                    claim="G3.fnf_rate", replicates=1000, seed=1)
        self.assertFalse(entry["conditions"]["cost"]["ok"])
        self.assertEqual(entry["conditions"]["cost"]["why"],
                         "the two scores price articles on different bases")

    def test_a_change_set_line_is_an_attempt_whatever_task_it_names_c07(self):
        """C-07 on the trunk's gate log: the reader keys on ``changed_files``, never on ``task_id``.

        tend's validation call with no task id (the single-stem rule: ``task_id``
        empty, ``task_id_from`` null) is a worker's attempt and was dropped as "the
        harvest's own re-gate", which logs nothing; an agent's own ``--findings``
        check names its task with ``--task-id`` and was counted as one (G-07).
        """
        rejected = real_problems((7, "findings/t3/enwiki-1001-2002.json",
                                  "is task 't3''s document, not 't4''s"))
        lines = [gate_line("loc1", "t1"),
                 gate_line("loc1", "t2", task_id_from="orchestrator.sqlite"),
                 gate_line("loc1", "", rejected),                    # the single-stem rule
                 gate_line("loc1", "t1", rejected, changed_files=False,
                           task_id_from="--task-id"),                # an agent's --findings check
                 gate_line("loc1", "", changed_files=False),          # a findings-directory check
                 {k: v for k, v in gate_line("loc1", "t5").items()   # a gate older than the field
                  if k != "changed_files"},
                 gate_line("inv1", "")]                               # another run's
        found = m.gate_rejections(lines, ["loc1"])
        self.assertEqual((found["attempts"], found["rejected"], found["dropped_lines"]), (3, 1, 4))
        self.assertEqual(found["by_rule"]["7"], {"attempts": 1, "rate": 1 / 3})

    def test_the_change_set_flag_is_the_gates_own_log_field_c07(self):
        """``changed_files`` is the key ``verify_locate.run_gate`` writes on every line."""
        source = (SCRIPTS / "gate" / "verify_locate.py").read_text(encoding="utf-8")
        self.assertRegex(source, r'"changed_files":\s*bool\(changed_files\)')
        text = " ".join((SKILL / "references" / "locate-bench.md").read_text(encoding="utf-8")
                        .split())
        self.assertNotIn("the harvest's own re-gate) or another `run_id` is dropped", text)
        self.assertIn("An attempt is a line of that run with `changed_files: true`", text)

    def test_a_baseline_is_reused_on_its_own_split_only_m07(self):
        """A ``measurement_id`` names the runs, not the split: one run's dev and eval
        scores share it, and are two baselines, never one reused."""
        reused = "baseline reused: an earlier entry compared this same baseline measurement"
        dev = score_doc(self.base_cells, split="dev")
        ev = score_doc(self.base_cells, split="eval")
        self.assertEqual(dev["measurement_id"], ev["measurement_id"])
        on_dev = compare_mod.compare(self.lay, self.write("bd", dev),
                                     self.write("cd", score_doc(self.better_cells, split="dev")),
                                     claim="G3.fnf_rate", replicates=1000, seed=1)
        self.assertNotIn(reused, on_dev["differences"])
        base, cand = self.write("be", ev), self.write("ce", score_doc(self.better_cells))
        first = compare_mod.compare(self.lay, base, cand, claim="G3.fnf_rate", replicates=1000,
                                    seed=1)
        self.assertNotIn(reused, first["differences"])
        again = compare_mod.compare(self.lay, base, cand, claim="G3.fnf_rate", replicates=1000,
                                    seed=1)
        self.assertIn(reused, again["differences"])


class TestRound3Metrics(unittest.TestCase):

    def test_an_unanchored_g1_miss_of_an_unreached_article_is_unresolved_m08(self):
        gold = [gold_passage("e0", "s", None, tier="G1", level="article", accept={("mathlib", "W.a")})]
        for state, cause, code in (("unresolved", "unresolved", "E-UNRESOLVED"),
                                   ("gate_failed", "gate_failed", "E-UNRESOLVED"),
                                   ("ran", "article_level", "E-RETRIEVAL-MISS")):
            with self.subTest(state=state):
                got = m.evaluate_tier("G1", gold, [], layers={"s": LAYER}, cluster_of={"s": "c1"},
                                      article_state={"s": state}, assessment={})
                self.assertEqual([(x["cause"], x["code"]) for x in got.misses], [(cause, code)])
                self.assertEqual(got.ratios["article_fnf_rate"].num, 1.0)


def frozen_at(root: Path) -> fx.World:
    """What ``frozen(case, plant=True)`` builds, at ``root``: a template for a test class."""
    world = fx.World(root)
    cf(world, "bench", "build", "b1", "--sourceset", fx.SOURCESET, "--frame", world.frame_file(),
       "--curated-qids", world.curated_1000(), "--uniform", 6, "--curated", 3,
       "--spans-per-article", 2, "--seed", 7)
    cf(world, "bench", "anchor", "b1", "--universe", fx.UNIVERSE, "--curated-1000",
       world.curated_1000(), "--curated-100", world.curated_100(), "--rename-table", world.renames())
    transcribe(world, "anchor", lambda t: {"task_id": t["task_id"],
                                           "quote": world.quote(index_of(t["source_id"]), 0, 6),
                                           "relation": "exactMatch", "passage_kind": "theorem",
                                           "evidence": "read at the pin"})
    transcribe(world, "label", lambda t: {
        "task_id": t["task_id"], "scope": "formal_math", "passage_kind": "theorem",
        "declarations": [{"library": "mathlib", "declaration": "Widget.conj_three",
                          "relation": "exactMatch"}], "evidence": "e", "hunters": []})
    cf(world, "bench", "freeze", "b1", "--plant")
    return world


def clone_world(template: fx.World, root: Path) -> fx.World:
    """A copy of a built world under ``root`` (the trees are relative; the paths are re-rooted)."""
    world = object.__new__(fx.World)
    world.__dict__.update(template.__dict__)
    world.root = root
    world.paths = WorkspacePaths(root)
    return world


if __name__ == "__main__":
    unittest.main()
