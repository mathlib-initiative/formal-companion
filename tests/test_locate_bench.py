"""The locate benchmark: draws, splits, gold, the labeler, metrics, scores and the acceptance rule.

New code, for docs/proposals/wikipedia-locate.md sections 8 and 9 as
references/locate-bench.md fixes them.  Every input is synthetic and built by
the documented formats (tests/_locate_bench_fixtures.py): the sourceset, the
universe and the harvested runs come from packages built beside this one.
The metric tests use hand-computed expectations on a handful of passages; the
refusals are each exercised; and the labeler Workflow is executed under node
with stubbed agents, its result transcribed back -- the round trip a session
makes.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import io
import json
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
                               run_id=run_id, inventory_run=inventory_run))
        if formalize_second:
            outs.append(fx.outcome(world, i, b, status="formalized", outcome_="located",
                                   locations=[("mathlib", "Widget.conj_three", "exactMatch")],
                                   run_id=run_id, inventory_run=inventory_run))
        else:
            outs.append(fx.outcome(world, i, b, status=None, outcome_="unresolved",
                                   run_id=run_id, inventory_run=inventory_run))
    world.run(run_id, inventory_run=inventory_run, passages=passages, outcomes=outs, **kw)
    return ev


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

    def test_an_article_the_manifest_lacks_or_holds_unrendered_is_refused_by_name(self):
        with self.assertRaises(CfError) as ctx:
            self.frame([{"pageid": 1001}, {"pageid": 4242}])
        self.assertIn("pageid 4242", str(ctx.exception))
        self.manifest.entries[self.world.sid(1)]["layer_sha256"] = None
        with self.assertRaises(CfError) as ctx:
            self.frame([{"pageid": 1002}])
        self.assertIn(self.world.sid(1), str(ctx.exception))

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

    def test_the_anchor_reads_the_same_lists(self):
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
        self.assertIn("mathlib_100 2 (by title)", evidence[w.sid(2)])
        self.assertEqual(doc["unmatched"], {"mathlib_100": 1})
        self.assertEqual(doc["listed_without_declarations"], {"mathlib_1000": 2, "mathlib_100": 1})

    def test_the_list_names_are_the_vocabularys(self):
        self.assertLessEqual({curated_mod.LIST_1000, curated_mod.LIST_100}, vocab.CANDIDATE_VIA)


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
        with self.assertRaises(CfError) as ctx:
            draw_mod.build_frame([{"pageid": 1001}], manifest, set())
        self.assertIn(self.pin0, str(ctx.exception))

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


class TestGateRejections(unittest.TestCase):

    def test_reasons_are_classified_and_rated_over_every_attempt(self):
        lines = [{"task": "t1", "mode": "locate", "ok": True, "reasons": []},
                 {"task": "t2", "mode": "locate", "ok": False,
                  "reasons": ["rule 3: alpha:W.ghost does not exist in corpus.sqlite at its commit"]},
                 {"task": "t3", "mode": "locate", "ok": False,
                  "reasons": ["library 'gamma' is outside the universe u1"]},
                 {"task": "t4", "mode": "locate", "ok": False, "reasons": ["rule 6: ranks repeat"]}]
        found = m.gate_rejections(lines)
        self.assertEqual((found["attempts"], found["rejected"]), (4, 3))
        self.assertEqual(found["by_reason"]["phantom_declaration"]["attempts"], 1)
        self.assertEqual(found["by_reason"]["outside_universe"]["attempts"], 1)
        self.assertEqual(found["by_reason"]["rule_6"]["rate"], 0.25)


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
        three = by_article[world.sid(3)][0]
        self.assertEqual([(a["declaration"], a["why"]) for a in three["accept"]],
                         [("Widget.new_name", "renamed")])
        self.assertEqual(by_article[world.sid(2)][0]["via"], ["wikidata_tag"])
        self.assertNotIn(world.sid(4), by_article)
        self.assertEqual(doc["dropped_entries"][0]["dropped"][0]["declaration"], "Widget.gone_forever")
        self.assertEqual(doc["unmatched"], {"mathlib_1000": 1})
        self.assertEqual(doc["listed_without_declarations"], {"mathlib_1000": 1})


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
        with self.assertRaises(CfError) as ctx:
            cf(self.world, "bench", "build", "b1", "--sourceset", fx.SOURCESET, "--frame",
               self.world.frame_file(), "--uniform", 2, "--seed", 8)
        self.assertIn("another draw", str(ctx.exception))

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
                           "declarations": [dict(hit, relation="exactMatch")], "evidence": "e"})
        self.run_transcribe(args, labels)
        cal = json.loads(self.lay.calibration_json.read_text())
        want = sum(1 for k in range(len(args["tasks"])) if k % 2 == 0)
        self.assertEqual((cal["found"], cal["total"]), (want, len(args["tasks"])))
        self.assertTrue(cal["tag_blind"])

    def test_gx_needs_a_signature_and_g4_its_caps(self):
        args = self.emit("inventory")
        labels = [{"task_id": t["task_id"], "source_id": t["source_id"],
                   "passages": [{"quote": self.world.quote(index_of(t["source_id"]), 1, 7),
                                 "passage_kind": "theorem", "scope": "formal_math",
                                 "from": ["A", "F"]}]} for t in args["tasks"]]
        with self.assertRaises(CfError):
            self.run_transcribe(args, labels)
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
        self.assertIn("unchanged", cf(world, "bench", "freeze", "b1") + "unchanged")
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
        conn = inputs.open_corpus(lay.planted_db)
        try:
            for name in ablation["removed"]:
                lib, decl = name.split(":", 1)
                self.assertIsNone(inputs.decl(conn, lib, fx.COMMIT_M, decl))
        finally:
            conn.close()
        original = inputs.open_corpus(WorkspacePaths(world.root).corpus_db(fx.UNIVERSE))
        try:
            lib, decl = ablation["removed"][0].split(":", 1)
            self.assertIsNotNone(inputs.decl(original, lib, fx.COMMIT_M, decl))
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
        self.assertLessEqual({"measurement_id", "pins", "tiers", "strata", "cost", "misses",
                              "audit", "coverage", "canary_check", "per_cluster"}, set(doc))
        self.assertEqual(doc["coverage"]["articles"], len(self.eval))
        self.assertEqual(doc["tiers"]["G1"]["arm"], "assisted")
        self.assertEqual(doc["canary_check"], {"ran": False, "basis": "--no-canary-check"})
        g3 = doc["tiers"]["G3"]["metrics"]
        # every eval article: G3 spans are paragraphs 0..3, two sampled; paragraph 1 unresolved
        self.assertIsNotNone(g3["fnf_rate"]["value"])
        self.assertTrue(all(miss["code"] in vocab.ERROR_CODES for miss in doc["misses"]))
        self.assertEqual(doc["pins"]["observed_models"], {"claude-opus-test": 300})
        self.assertEqual(doc["pins"]["agent_cli"], "2.0.0")

    def test_measurement_id_is_stable_and_moves_with_every_pin(self):
        first = score(self.world)["measurement_id"]
        self.assertEqual(score(self.world)["measurement_id"], first)
        changes = {
            "models": dict(models={"claude-other": {"input": 1}}),
            "prompts": dict(prompts={"worker.md": "9" * 64}),
            "corpus": dict(corpus_db_sha="7" * 64),
        }
        for name, kw in changes.items():
            with self.subTest(pin=name):
                eval_run(self.world, run_id=f"loc-{name}", inventory_run=f"inv-{name}", **kw)
                self.assertNotEqual(score(self.world, f"loc-{name}")["measurement_id"], first)
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
        path.write_text(json.dumps({"complete": True}))
        with self.assertRaises(CfError) as ctx:
            score(self.world, "loc3")
        self.assertIn("no cost block", str(ctx.exception))

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
        report = self.world.write("inputs/scan.json", json.dumps({"scanned": 3, "hits": ["x.jsonl"]}))
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
        self.world.run("loc-all", inventory_run="inv-all", passages=passages, outcomes=[])
        doc = score(self.world, "loc-all")
        cf(self.world, "bench", "score", "b1", "--run", "loc-all", "--split", "dev", "--bootstrap", 50,
           "--no-canary-check")
        names = sorted(p.name for p in layout_of(self.world).scores_dir.glob("*.json"))
        self.assertIn(f"eval-{doc['measurement_id'][:16]}.json", names)
        self.assertIn(f"dev-{doc['measurement_id'][:16]}.json", names)

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
                           inventory_run="invp") for sid in ev]
        self.world.run("locp", inventory_run="invp", passages=passages, outcomes=outs,
                       corpus_db_sha=ablation["corpus_sha256"])
        doc = score(self.world, "locp")
        self.assertIn("P", doc["tiers"])
        self.assertTrue(any(x["code"] == "E-MEMORY" for x in doc["misses"]))
        self.assertNotIn("P", score(self.world, "loc1")["tiers"])

    def test_costs_gate_log_and_the_audit_sample(self):
        costs = self.world.write("inputs/costs.json", json.dumps({s: 2.0 for s in self.eval}))
        log = self.world.write("inputs/gate-log/a.jsonl",
                               json.dumps({"task": "t", "mode": "locate", "ok": False,
                                           "reasons": ["rule 3: W.x does not exist in corpus"]}) + "\n")
        doc = score(self.world, "loc1", "--no-canary-check", "--article-costs", costs,
                    "--gate-log", log.parent)
        self.assertEqual(doc["cost"]["value"], 2.0)
        self.assertEqual(doc["gate_rejections"]["by_reason"]["phantom_declaration"]["attempts"], 1)
        audit = layout_of(self.world).audit_jsonl(score_mod.score_name("eval", doc["measurement_id"]))
        self.assertTrue(audit.is_file())


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

def score_doc(fnf_cells, *, models=("m1",), renderer=("web-html/1",), split="eval", arm="blind",
              cost=1.0, articles=10, positives=20, gold="g" * 64, overclaim=None, mid=None,
              strata=None):
    clusters = sorted(fnf_cells)
    per_cluster = {"G3": {"fnf_rate": fnf_cells,
                          "overclaim_rate": overclaim or {c: [0.0, 1.0] for c in clusters}},
                   "cost": {"usd_per_article": {c: [cost, 1.0] for c in clusters}}}
    return {"schema": "cf-bench-score/1", "bench": "b1", "split": split, "arm": arm,
            "measurement_id": mid or gold_mod.sha_of([fnf_cells, cost, models]),
            "pins": {"observed_models": {mm: 1 for mm in models}, "renderer": list(renderer),
                     "universe_sha256": "u"},
            "gold_manifest": {"version": 1, "sha256": gold},
            "coverage": {"articles": articles, "total_positives": positives},
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
                                   aa_path=aa, replicates=300, seed=1, **kw)

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
        self.assertEqual(self.compare(base, claimed, aa, replication=True)["alpha_j"], 0.05)

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
        good = {"den": 40, "by_cluster": {c: [0.0, 1.0] for c in self.base_cells}}
        bad = {"den": 40, "by_cluster": {c: [1.0, 1.0] for c in self.base_cells}}
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

    def test_a_reused_baseline_is_noted(self):
        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells))
        self.assertEqual(self.compare(base, cand)["differences"], [])
        self.assertIn("baseline reused: every pin matches an earlier entry's baseline",
                      self.compare(base, cand)["differences"])

    def test_the_cli_prints_the_decision(self):
        from cflib.yamlio import write_yaml

        write_yaml(self.lay.bench_yaml, {"schema": "cf-bench/1", "id": "b1"})
        base = self.write("base", score_doc(self.base_cells))
        cand = self.write("cand", score_doc(self.better_cells))
        out = cf(self.world, "bench", "compare", "b1", base, cand, "--claim", "G3.fnf_rate",
                 "--bootstrap", 100)
        self.assertIn("REJECT", out)          # no A/A replicate
        self.assertIn("aa", out)


if __name__ == "__main__":
    unittest.main()
