"""The carry-over map between two cuts, its rows, and gold carried across a re-cut.

New code, for docs/proposals/statement-cut.md section 4 as references/locate-documents.md
section 12 (``cf-locate-carry/1``) and references/locate-bench.md section 7
(``cf-bench-carry/1``) fix it.  Every layer, page, item and declaration is synthetic:

* the map itself (``locate.carry.map_page``) on hand-written cuts: every fate, a new
  revision whose text is unchanged, a text the new revision edits or repeats, and the
  refusals; the shared cases of ``tests/data/locate/carry-parity.json``, which a
  consumer's port runs too;
* ``cf locate carry`` over two harvested inventory runs on a sourceset rendered by its
  owner: the document, its determinism, ``--source``, the stamps it refuses, and
  ``--rows`` (known answers keyed by ``passage_key``);
* ``cf bench carry`` over a frozen bench: a gold row carried where its passage's text is
  unchanged and marked for relabel otherwise, every row relabelled across a labeler
  bump, and no label file edited.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent / "scripts"), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import test_locate_bench as tlb  # noqa: E402
except ImportError:  # pragma: no cover
    from tests import test_locate_bench as tlb  # noqa: E402

import cf as cf_main  # noqa: E402
from bench import gold as gold_mod  # noqa: E402
from cflib import hashing, vocab  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.paths import WorkspacePaths, skill_root  # noqa: E402
from locate import carry  # noqa: E402
from sources import render_html, websource  # noqa: E402

fx = tlb.fx
PARITY = HERE / "data" / "locate" / "carry-parity.json"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def at(layer: str, text: str, nth: int = 0) -> list[int]:
    i = -1
    for _ in range(nth + 1):
        i = layer.find(text, i + 1)
        assert i != -1, text
    return [i, i + len(text)]


def cut(layer: str, passages: list[tuple], pageid: int = 1001, sid: str = "enwiki-1001-2002"
        ) -> carry.Cut:
    """A cut from ``(item, text[, scope[, extra]])`` rows placed on ``layer`` by text."""
    rows = []
    for row in passages:
        item, text = row[0], row[1]
        scope = row[2] if len(row) > 2 else vocab.SCOPE_FORMAL_MATH
        extra = row[3] if len(row) > 3 else {}
        rows.append({"item": item, "span": at(layer, text, extra.pop("nth", 0)), "scope": scope,
                     **extra})
    return carry.Cut(pageid, sid, layer, sha(layer), rows)


def fates(page: dict) -> dict[str, tuple[str, list[str]]]:
    return {e["item"]: (e["fate"], [i["item"] for i in e["into"]]) for e in page["passages"]}


# ---------------------------------------------------------------------------
# the map
# ---------------------------------------------------------------------------

LAYER = ("Every widget has a gadget. A gadget spans a widget since every face meets it. "
         "Gadgets of a finite widget are conjugate. The number of gadgets is finite. "
         "A widget is tame when it has one face. Tame widgets are rigid. "
         "Rigid widgets have a spanning gadget. A spanning gadget is unique. "
         "The gadget count is odd. Every rigid widget is tame.")


class TestTheMap(unittest.TestCase):
    """Section 4.2: every fate, from spans and span text alone."""

    def old(self) -> carry.Cut:
        return cut(LAYER, [
            ("In-text #0000a001", "Every widget has a gadget."),
            ("In-text #0000a002", "A gadget spans a widget since every face meets it."),
            ("In-text #0000a003", "Gadgets of a finite widget are conjugate. The number of "
                                  "gadgets is finite."),
            ("In-text #0000a004", "A widget is tame when it has one face."),
            ("In-text #0000a005", "Tame widgets are rigid."),
            ("In-text #0000a006", "Rigid widgets have a spanning gadget. A spanning gadget "
                                  "is unique."),
            ("In-text #0000a007", "The gadget count is odd.")])

    def new(self) -> carry.Cut:
        return cut(LAYER, [
            ("In-text #0000a001", "Every widget has a gadget."),
            ("In-text #0000a002", "A gadget spans a widget"),
            ("In-text #0000b002", "every face meets it."),
            ("In-text #0000a003", "Gadgets of a finite widget are conjugate."),
            ("In-text #0000a004", "A widget is tame when it has one face. Tame widgets are "
                                  "rigid."),
            ("In-text #0000b006", "A spanning gadget is unique."),
            ("In-text #0000b009", "Every rigid widget is tame.")])

    def test_every_fate_of_a_recut_of_one_revision(self):
        page = carry.map_page(self.old(), self.new())
        self.assertTrue(page["same_layer"])
        self.assertEqual(fates(page), {
            "In-text #0000a001": ("unchanged", ["In-text #0000a001"]),
            "In-text #0000a002": ("split", ["In-text #0000a002", "In-text #0000b002"]),
            "In-text #0000a003": ("end_moved", ["In-text #0000a003"]),
            "In-text #0000a004": ("end_moved", ["In-text #0000a004"]),
            "In-text #0000a005": ("merged", ["In-text #0000a004"]),
            "In-text #0000a006": ("reshaped", ["In-text #0000b006"]),
            "In-text #0000a007": ("removed", [])})
        self.assertEqual([p["item"] for p in page["new_passages"]], ["In-text #0000b009"])
        self.assertEqual(page["new_passages"][0]["fate"], vocab.CARRY_NEW)
        self.assertEqual(page["counts"], {"end_moved": 2, "merged": 1, "new": 1, "removed": 1,
                                          "reshaped": 1, "split": 1, "unchanged": 1})
        used = {e["fate"] for e in page["passages"]} | {vocab.CARRY_NEW}
        self.assertLessEqual(used - {vocab.CARRY_NEW}, vocab.CARRY_FATES)

    def test_the_keys_are_the_hashing_modules(self):
        page = carry.map_page(self.old(), self.new())
        first = page["passages"][0]
        self.assertEqual(first["passage_key"],
                         hashing.passage_key(1001, sha("Every widget has a gadget.")))
        self.assertEqual(first["into"][0]["passage_key"], first["passage_key"])
        self.assertEqual(page["old"]["cut_id"], hashing.cut_id(
            1001, sha(LAYER), [p["span"] for p in self.old().passages]))
        self.assertNotEqual(page["old"]["cut_id"], page["new"]["cut_id"])

    def test_an_identical_cut_is_unchanged_throughout_and_one_cut_id(self):
        page = carry.map_page(self.old(), self.old())
        self.assertEqual({e["fate"] for e in page["passages"]}, {"unchanged"})
        self.assertEqual(page["new_passages"], [])
        self.assertEqual(page["old"]["cut_id"], page["new"]["cut_id"])

    def test_the_order_of_the_rows_changes_nothing(self):
        old, new = self.old(), self.new()
        old.passages.reverse()
        new.passages.reverse()
        self.assertEqual(carry.map_page(old, new), carry.map_page(self.old(), self.new()))

    def test_a_new_revision_with_unchanged_text(self):
        """The text moved (a lead was added): the same key, so unchanged, at its new span."""
        b = "A lead sentence was added. " + LAYER
        new = cut(b, [("In-text #0000a001", "Every widget has a gadget."),
                      ("In-text #0000a007", "The gadget count is odd.")], sid="enwiki-1001-2003")
        old = cut(LAYER, [("In-text #0000a001", "Every widget has a gadget."),
                          ("In-text #0000a007", "The gadget count is odd.")])
        page = carry.map_page(old, new)
        self.assertFalse(page["same_layer"])
        self.assertEqual({e["fate"] for e in page["passages"]}, {"unchanged"})
        moved = page["passages"][0]
        self.assertEqual(moved["into"][0]["span"], [s + 27 for s in moved["span"]])
        self.assertEqual(page["new"]["source_id"], "enwiki-1001-2003")

    def test_a_new_revision_places_an_old_text_it_still_holds_once(self):
        a = "Every widget has a gadget. Gadgets are conjugate in pairs."
        b = "A new lead sentence. Every widget has a gadget. Gadgets are conjugate in pairs."
        page = carry.map_page(
            cut(a, [("In-text #0000e001", a)]),
            cut(b, [("In-text #0000e001", "Every widget has a gadget."),
                    ("In-text #0000f002", "Gadgets are conjugate in pairs.")]))
        self.assertEqual(fates(page)["In-text #0000e001"],
                         ("split", ["In-text #0000e001", "In-text #0000f002"]))

    def test_an_edited_or_repeated_text_is_changed_never_guessed(self):
        a = "Gadgets are conjugate. A widget is tame."
        b = "Gadgets of one widget are conjugate. A widget is tame. A widget is tame."
        page = carry.map_page(
            cut(a, [("In-text #0000c002", "Gadgets are conjugate."),
                    ("In-text #0000c003", "A widget is tame.")]),
            cut(b, [("In-text #0000d002", "Gadgets of one widget are conjugate."),
                    ("In-text #0000d003", "A widget is tame. A widget is tame.")]))
        self.assertEqual(fates(page), {"In-text #0000c002": ("changed", []),
                                       "In-text #0000c003": ("changed", [])})
        self.assertEqual(len(page["new_passages"]), 2)
        for entry in page["passages"]:
            self.assertIsNone(entry["scope_changed"])
            self.assertIsNone(entry["conjuncts_changed"])

    def test_overlapping_occurrences_are_two(self):
        self.assertEqual(carry._occurrences("aaa", "aa"), [0, 1])

    def test_two_passages_of_one_text_refuse_the_page(self):
        layer = "Gadgets are conjugate. Widgets are tame. Gadgets are conjugate."
        old = cut(layer, [("In-text #00002001", "Gadgets are conjugate."),
                          ("In-text #00002002", "Gadgets are conjugate.", "formal_math",
                           {"nth": 1})])
        with self.assertRaises(carry.CarryRefused) as ctx:
            carry.map_page(old, cut(layer, [("In-text #00002001", "Gadgets are conjugate.")]))
        self.assertIn("two passages of the old cut share one passage_key", str(ctx.exception))
        with self.assertRaises(carry.CarryRefused):
            carry.map_page(cut(layer, [("In-text #00002001", "Widgets are tame.")]), old)

    def test_a_span_outside_the_layer_refuses_the_page(self):
        old = cut(LAYER, [("In-text #0000a001", "Every widget has a gadget.")])
        old.passages[0]["span"] = [0, len(LAYER) + 4]
        with self.assertRaises(carry.CarryRefused):
            carry.map_page(old, self.new())

    def test_scope_and_conjunct_changes_are_flagged_and_do_not_carry(self):
        layer = "A widget is finite exactly when its gadget is. Gadgets are conjugate."
        conj = [{"id": "c1", "text": "a finite widget has a finite gadget"},
                {"id": "c2", "text": "a widget with a finite gadget is finite"}]
        old = cut(layer, [("In-text #00001001", "A widget is finite exactly when its gadget is."),
                          ("In-text #00001002", "Gadgets are conjugate.", "expository"),
                          ])
        new = cut(layer, [("In-text #00001001", "A widget is finite exactly when its gadget is.",
                           "formal_math", {"conjuncts": conj, "shape": "iff"}),
                          ("In-text #00001002", "Gadgets are conjugate.")])
        page = carry.map_page(old, new)
        by = {e["item"]: e for e in page["passages"]}
        self.assertEqual((by["In-text #00001001"]["scope_changed"],
                          by["In-text #00001001"]["conjuncts_changed"]), (False, True))
        self.assertEqual((by["In-text #00001002"]["scope_changed"],
                          by["In-text #00001002"]["conjuncts_changed"]), (True, False))
        self.assertFalse(any(carry.carries(e) for e in page["passages"]))
        self.assertEqual([carry.carries(e) for e in carry.map_page(new, new)["passages"]],
                         [True, True])
        # an expository passage carries no pair verdict, unchanged or not
        self.assertEqual([carry.carries(e) for e in carry.map_page(old, old)["passages"]],
                         [True, False])

    def test_the_conjunct_digest_reads_the_list_as_written(self):
        self.assertEqual(carry.conjuncts_sha256({}), hashing.sha256_json([]))
        self.assertNotEqual(carry.conjuncts_sha256({"conjuncts": [{"id": "c1", "text": "a"}]}),
                            carry.conjuncts_sha256({"conjuncts": [{"id": "c1", "text": "b"}]}))


class TestTheParityTable(unittest.TestCase):
    """``tests/data/locate/carry-parity.json``: the shared cases, written by hand."""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(PARITY.read_text(encoding="utf-8"))

    def test_its_shape(self):
        self.assertEqual(self.doc["schema"], "cf-locate-carry-parity/1")
        self.assertGreaterEqual(len(self.doc["cases"]), 5)
        seen = set()
        for case in self.doc["cases"]:
            seen |= {e["fate"] for e in case["expected"].get("passages", [])}
            if case["expected"].get("new_passages"):
                seen.add(vocab.CARRY_NEW)
        # every fate a page can give is held by some case
        self.assertEqual(seen, vocab.CARRY_FATES | {vocab.CARRY_NEW})
        self.assertTrue(any("refused" in c["expected"] for c in self.doc["cases"]))

    def test_its_key_vectors(self):
        for vector in self.doc["keys"]:
            with self.subTest(text=vector["text"]):
                self.assertEqual(sha(vector["text"]), vector["span_sha256"])
                self.assertEqual(hashing.passage_key(vector["pageid"], vector["span_sha256"]),
                                 vector["passage_key"])

    def test_every_case(self):
        for case in self.doc["cases"]:
            with self.subTest(case=case["name"]):
                sides = {}
                for side in ("old", "new"):
                    layer = case[side]["layer"]
                    for p in case[side]["passages"]:
                        self.assertEqual(layer[p["span"][0]:p["span"][1]], p["text"])
                    sides[side] = carry.Cut(case["pageid"], f"enwiki-{case['pageid']}-{side}",
                                            layer, sha(layer),
                                            [{k: v for k, v in p.items() if k != "text"}
                                             for p in case[side]["passages"]])
                expected = case["expected"]
                if "refused" in expected:
                    with self.assertRaises(carry.CarryRefused) as ctx:
                        carry.map_page(sides["old"], sides["new"])
                    self.assertTrue(str(ctx.exception).startswith(expected["refused"]))
                    continue
                page = carry.map_page(sides["old"], sides["new"])
                self.assertEqual(page["same_layer"], expected["same_layer"])
                self.assertEqual(page["counts"], expected["counts"])
                self.assertEqual([p["item"] for p in page["new_passages"]],
                                 expected["new_passages"])
                self.assertEqual(len(page["passages"]), len(expected["passages"]))
                for got, want in zip(page["passages"], expected["passages"]):
                    self.assertEqual(got["item"], want["item"])
                    self.assertEqual(got["fate"], want["fate"], want["item"])
                    self.assertEqual([i["item"] for i in got["into"]], want["into"])
                    for flag in ("scope_changed", "conjuncts_changed"):
                        if flag in want:
                            self.assertEqual(got[flag], want[flag], (want["item"], flag))
                    if "carries" in want:
                        self.assertEqual(carry.carries(got), want["carries"])


# ---------------------------------------------------------------------------
# cf locate carry over two harvested inventory runs
# ---------------------------------------------------------------------------

def cf(world, *argv) -> tuple[int, str, str]:
    """``(exit code, stdout, stderr)``; a refusal is exit 2 with its message on stderr."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cf_main.main(["--workspace", str(world.root), *[str(a) for a in argv]])
        except CfError as exc:
            rc = 2
            print(str(exc), file=err)
    return rc, out.getvalue(), err.getvalue()


def new_revision(world: fx.World, i: int, revision: int, paragraphs: list[str]) -> str:
    """Another revision of article ``i``'s page with other text, rendered by its owner."""
    from sources.sourceset import Sourceset

    base = world.articles[world.sid(i)]["entry"]
    entry = websource.entry(websource.family("enwiki"), base["pageid"], revision,
                            title=base["title"], wikidata=base["wikidata"],
                            assessment=base["assessment"])
    sid = entry["source_id"]
    body = ("<div class=\"mw-parser-output\">\n"
            + "".join(f"<p>{p}</p>\n" for p in paragraphs) + "</div>\n")
    rendering = render_html.render(body)
    ss = Sourceset.open(world.paths, fx.SOURCESET)
    sdir = ss.source_dir(sid)
    render_html.write_rendering(sdir, rendering)
    render_html.write_bytes(sdir / "source.raw.html", body.encode("utf-8"))
    entry.update(fetch=websource.fetch_block(),
                 raw_sha256=fx.sha256_bytes((sdir / "source.raw.html").read_bytes()),
                 html_sha256=fx.sha256_bytes((sdir / "body.html").read_bytes()),
                 layer_sha256=fx.sha256_bytes((sdir / "layer.txt").read_bytes()),
                 renderer=render_html.RENDERER)
    world.entries[sid] = entry
    world.articles[sid] = {"entry": entry, "layer": rendering.layer,
                           "spans": [p["span"] for p in rendering.paragraphs if p["block"] == "p"],
                           "index": i}
    world.write_manifest()
    return sid


def inventory(world: fx.World, rid: str, docs: dict[str, list[dict]], *,
              gate: dict | None = None, unresolved: set[str] = frozenset(),
              layer_sha: dict[str, str] | None = None) -> None:
    """A harvested, gated inventory run whose documents hold ``docs``' passages."""
    world.run_record(rid, "locate-inventory", source_ids=sorted(docs), judgment_rules=None)
    if gate:
        world.write(f"runs/{rid}/gate.json",
                    json.dumps({"schema": "cf-gate-locate/1", "mode": "locate-inventory",
                                **gate}))
    world.receipt(rid)
    world.task_manifest(rid, "t-inv", "locate-inventory", sorted(docs))
    for sid, rows in sorted(docs.items()):
        doc = {"schema": "cf-locate/1", "mode": "locate-inventory", "run_id": rid,
               "task_id": "t-inv", "source_id": sid, "source_sha": "1" * 64,
               "layer_sha256": (layer_sha or {}).get(sid)
               or world.articles[sid]["entry"]["layer_sha256"],
               "renderer": render_html.RENDERER, "passages": [] if sid in unresolved else rows}
        if sid in unresolved:
            doc["unresolved"] = {"reason": "the article is a list", "by": "worker"}
        world.document(rid, "t-inv", sid, doc)


def passage(world: fx.World, sid: str, item: str, span: list[int], scope="formal_math") -> dict:
    return {"item": item, "span": list(span), "scope": scope, "passage_kind": "theorem",
            "prominence": "central", "quote": {"exact": world.articles[sid]["layer"][
                span[0]:span[1]].split(".")[0]}}


def paragraphs_cut(world: fx.World, sid: str, tag: int) -> list[dict]:
    """One passage per paragraph of the source."""
    return [passage(world, sid, f"In-text #{tag:04x}{k:04x}", span)
            for k, span in enumerate(world.articles[sid]["spans"])]


class TestCfLocateCarry(unittest.TestCase):
    """Two inventory runs: one page untouched, one re-cut, one on a new revision."""

    def setUp(self):
        self.world = fx.World(tlb.scratch(self) / "ws", articles=4)
        w = self.world
        self.s0, self.s1, self.s2, self.s3 = (w.sid(i) for i in range(4))
        # article 2's next revision: paragraph 0 kept word for word, paragraph 1 edited
        paras = fx.article_paragraphs(2)
        self.r2 = new_revision(w, 2, 3004, [paras[0], paras[1].replace("conjugate", "similar"),
                                            paras[2], paras[3]])
        old = {sid: paragraphs_cut(w, sid, n) for n, sid in enumerate((self.s0, self.s1,
                                                                        self.s2, self.s3))}
        inventory(w, "inv-a", old)
        # the new cut: article 0 untouched; article 1's paragraph 1 cut at its semicolon;
        # article 2 on its new revision; article 3 not inventoried again
        new = {self.s0: paragraphs_cut(w, self.s0, 0)}
        layer1 = w.articles[self.s1]["layer"]
        a, b = w.articles[self.s1]["spans"][1]
        semi = layer1.index(";", a)
        new[self.s1] = [p for k, p in enumerate(paragraphs_cut(w, self.s1, 1)) if k != 1] + [
            passage(w, self.s1, "In-text #00010001", [a, semi]),
            passage(w, self.s1, "In-text #00019001", [semi + 2, b])]
        new[self.r2] = paragraphs_cut(w, self.r2, 2)
        inventory(w, "inv-b", new, gate={"span_end_rule": 2, "statement_cut_rule": 1})

    def doc(self) -> dict:
        path = self.world.paths.locate_carry_json("inv-a", "inv-b")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_document(self):
        rc, out, err = cf(self.world, "locate", "carry", "--from", "inv-a", "--to", "inv-b")
        self.assertEqual(rc, 0, err)
        doc = self.doc()
        self.assertEqual(doc["schema"], carry.CARRY_SCHEMA)
        self.assertEqual(doc["old"], {"inventory_run": "inv-a", "span_end_rule": 0,
                                      "statement_cut_rule": 0})
        self.assertEqual(doc["new"], {"inventory_run": "inv-b", "span_end_rule": 2,
                                      "statement_cut_rule": 1})
        self.assertIsNone(doc["sources"])
        self.assertEqual(doc["refused"], [])
        self.assertEqual(doc["unpaired"], {"old": [1004], "new": []})
        pages = {p["pageid"]: p for p in doc["pages"]}
        self.assertEqual(sorted(pages), [1001, 1002, 1003])
        self.assertEqual(pages[1001]["counts"], {"unchanged": 4})
        self.assertEqual(pages[1002]["counts"], {"split": 1, "unchanged": 3})
        self.assertEqual(fates(pages[1002])["In-text #00010001"],
                         ("split", ["In-text #00010001", "In-text #00019001"]))
        # the new revision: paragraph 0 and the untouched ones carry, the edited one does not
        page = pages[1003]
        self.assertFalse(page["same_layer"])
        self.assertEqual((page["old"]["source_id"], page["new"]["source_id"]), (self.s2, self.r2))
        self.assertEqual(page["counts"], {"changed": 1, "new": 1, "unchanged": 3})
        self.assertEqual(fates(page)["In-text #00020001"][0], "changed")
        self.assertEqual(doc["counts"], {"pages": 3, "changed": 1, "new": 1, "split": 1,
                                         "unchanged": 10})
        self.assertIn("3 page(s)", out)

    def test_a_rerun_writes_the_same_bytes(self):
        path = self.world.paths.locate_carry_json("inv-a", "inv-b")
        self.assertEqual(cf(self.world, "locate", "carry", "--from", "inv-a", "--to", "inv-b")[0],
                         0)
        first = path.read_bytes()
        self.assertEqual(cf(self.world, "locate", "carry", "--from", "inv-a", "--to", "inv-b")[0],
                         0)
        self.assertEqual(path.read_bytes(), first)
        self.assertEqual(carry.build(self.world.paths, "inv-a", "inv-b"), json.loads(first))

    def test_it_writes_nothing_else(self):
        before = {p: p.stat().st_mtime_ns for p in self.world.root.rglob("*") if p.is_file()}
        cf(self.world, "locate", "carry", "--from", "inv-a", "--to", "inv-b")
        after = {p: p.stat().st_mtime_ns for p in self.world.root.rglob("*") if p.is_file()}
        self.assertEqual(set(after) - set(before),
                         {self.world.paths.locate_carry_json("inv-a", "inv-b")})
        self.assertEqual({p for p in before if before[p] != after[p]}, set())

    def test_a_named_source_maps_its_page_only(self):
        doc = carry.build(self.world.paths, "inv-a", "inv-b", source_ids=[self.r2])
        self.assertEqual([p["pageid"] for p in doc["pages"]], [1003])
        self.assertEqual(doc["sources"], [self.r2])
        doc = carry.build(self.world.paths, "inv-a", "inv-b", source_ids=[self.s3])
        self.assertEqual(doc["pages"], [])
        self.assertEqual(doc["unpaired"], {"old": [1004], "new": []})
        with self.assertRaises(CfError):
            carry.build(self.world.paths, "inv-a", "inv-b", source_ids=["enwiki-9999-1"])

    def test_a_document_made_against_another_layer_refuses_its_page(self):
        inventory(self.world, "inv-c", {self.s0: paragraphs_cut(self.world, self.s0, 0)},
                  layer_sha={self.s0: "9" * 64})
        doc = carry.build(self.world.paths, "inv-a", "inv-c")
        self.assertEqual(doc["pages"], [])
        self.assertEqual([r["pageid"] for r in doc["refused"]], [1001])
        self.assertIn("was made against layer", doc["refused"][0]["reason"])

    def test_an_unresolved_document_refuses_its_page(self):
        inventory(self.world, "inv-c", {self.s0: []}, unresolved={self.s0})
        doc = carry.build(self.world.paths, "inv-a", "inv-c")
        self.assertIn("unresolved", doc["refused"][0]["reason"])

    def test_only_two_gated_inventory_runs(self):
        self.world.run_record("loc-x", "locate", inventory_run="inv-a", source_ids=[self.s0])
        with self.assertRaises(CfError) as ctx:
            carry.build(self.world.paths, "inv-a", "loc-x")
        self.assertIn("not an inventory", str(ctx.exception))
        inventory(self.world, "inv-u", {self.s0: paragraphs_cut(self.world, self.s0, 0)})
        self.world.receipt("inv-u", gated=False)
        with self.assertRaises(CfError):
            carry.build(self.world.paths, "inv-a", "inv-u")

    def test_rows_keyed_by_passage_key(self):
        w = self.world
        layer = w.articles[self.s1]["layer"]
        keys = [hashing.passage_key(1002, hashing.span_sha256(layer, span))
                for span in w.articles[self.s1]["spans"][:2]]
        rows = [{"passage_key": keys[0], "declaration": "Widget.spanning_one",
                 "relation": "exactMatch"},
                {"passage_key": keys[1], "declaration": "Widget.conj_three",
                 "relation": "exactMatch"},
                {"passage_key": "0" * 64, "declaration": "Widget.unrelated"}]
        src = w.write("inputs/known.jsonl", "".join(json.dumps(r) + "\n" for r in rows) + "\n")
        out = w.root / "inputs" / "known.carried.jsonl"
        rc, text, err = cf(w, "locate", "carry", "--from", "inv-a", "--to", "inv-b",
                           "--rows", src, "--rows-out", out)
        self.assertEqual(rc, 0, err)
        got = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["declaration"] for r in got], [r["declaration"] for r in rows])
        self.assertEqual([r["carried"] for r in got], [True, False, False])
        self.assertEqual(got[0]["carry"]["fate"], "unchanged")
        self.assertEqual(got[0]["carry"]["into"][0]["passage_key"], keys[0])
        self.assertEqual(got[1]["carry"]["fate"], "split")
        self.assertEqual(len(got[1]["carry"]["into"]), 2)
        self.assertIsNone(got[2]["carry"])
        self.assertIn("1 of 3 carried", text)
        # its own output carries the same way again: idempotent
        again = w.root / "inputs" / "known.carried.2.jsonl"
        cf(w, "locate", "carry", "--from", "inv-a", "--to", "inv-b", "--rows", out,
           "--rows-out", again)
        self.assertEqual(again.read_bytes(), out.read_bytes())

    def test_rows_refusals_come_before_any_write(self):
        w = self.world
        bad = w.write("inputs/bad.jsonl", json.dumps({"item": "In-text #00010001"}) + "\n")
        report = w.paths.locate_carry_json("inv-a", "inv-b")
        rc, _, err = cf(w, "locate", "carry", "--from", "inv-a", "--to", "inv-b", "--rows", bad,
                        "--rows-out", w.root / "out.jsonl")
        self.assertNotEqual(rc, 0)
        self.assertIn("passage_key", err)
        self.assertFalse(report.exists())
        good = w.write("inputs/good.jsonl", json.dumps({"passage_key": "0" * 64}) + "\n")
        for target in (good, skill_root() / "carried.jsonl"):
            with self.subTest(target=target):
                rc, _, err = cf(w, "locate", "carry", "--from", "inv-a", "--to", "inv-b",
                                "--rows", good, "--rows-out", target)
                self.assertNotEqual(rc, 0)
                self.assertFalse(report.exists())
        self.assertFalse((skill_root() / "carried.jsonl").exists())
        rc, _, err = cf(w, "locate", "carry", "--from", "inv-a", "--to", "inv-b", "--rows", good)
        self.assertNotEqual(rc, 0)
        self.assertIn("go together", err)

    def test_the_report_path(self):
        paths = WorkspacePaths(Path("/w"))
        self.assertEqual(paths.locate_carry_json("inv-a", "inv-b"),
                         Path("/w/reports/locate-carry/inv-a/inv-b.json"))
        with self.assertRaises(CfError):
            paths.locate_carry_json("../x", "inv-b")


# ---------------------------------------------------------------------------
# gold across a re-cut
# ---------------------------------------------------------------------------

class TestBenchCarry(unittest.TestCase):
    """A frozen bench and cuts of its articles: a gold row carries only where the
    passages over its span did not move."""

    def setUp(self):
        self.world = tlb.frozen(self)
        w = self.world
        self.layout = tlb.layout_of(w)
        self.sids = sorted(w.articles)
        self.g1 = tlb.gold_mod.read_labels(self.layout, "G1")
        self.g3 = gold_mod.read_labels(self.layout, "G3")
        anchored = [r for r in self.g1 if r.get("span")]
        self.assertTrue(anchored and self.g3)
        # the G1 row: a quote at the head of paragraph 0 of its article
        self.g1_row = anchored[0]
        # a G3 row on another article: a whole paragraph block
        self.g3_row = next(r for r in self.g3 if r["source_id"] != self.g1_row["source_id"])
        self.shas = {t: fx.sha256_bytes(self.layout.label_file(t).read_bytes())
                     for t in ("G1", "G3")}

    def paragraphs(self) -> dict[str, list[dict]]:
        return {sid: paragraphs_cut(self.world, sid, n) for n, sid in enumerate(self.sids)}

    def g3_halves(self, *, shorten: bool = False) -> list[dict]:
        """The G3 row's paragraph as two passages, cut at a word in its middle."""
        w, row = self.world, self.g3_row
        a, b = row["span"]
        layer = w.articles[row["source_id"]]["layer"]
        mid = layer.index(" ", (a + b) // 2)
        return [passage(w, row["source_id"], "In-text #0000aa01", [a, mid]),
                passage(w, row["source_id"], "In-text #0000aa02", [mid + 1, b - 1 if shorten
                                                                     else b])]

    def with_g3(self, cut_: dict, pieces: list[dict] | None) -> dict:
        """``cut_`` with the G3 row's paragraph replaced by ``pieces`` (None: no passage)."""
        row = self.g3_row
        rows = [p for p in cut_[row["source_id"]] if p["span"] != row["span"]]
        cut_[row["source_id"]] = rows + (pieces or [])
        return cut_

    def carry(self, old: dict, new: dict) -> dict:
        inventory(self.world, "inv-a", old)
        inventory(self.world, "inv-b", new)
        out = tlb.cf(self.world, "bench", "carry", "b1", "--from", "inv-a", "--to", "inv-b")
        self.assertIn("no label file was edited", out)
        doc = json.loads(self.layout.gold_carry_json(1).read_text(encoding="utf-8"))
        for tier, sha_ in self.shas.items():
            self.assertEqual(fx.sha256_bytes(self.layout.label_file(tier).read_bytes()), sha_)
        return doc

    def row(self, doc: dict, gold: dict) -> dict:
        return next(r for r in doc["rows"] if r["gold_id"] == gold["gold_id"])

    def test_a_gold_file_carried_across_a_recut(self):
        """The G1 row's paragraph is split: it is relabelled with that fate, the rest carry."""
        w, sid = self.world, self.g1_row["source_id"]
        new = self.paragraphs()
        a, b = w.articles[sid]["spans"][0]
        cut_at = w.articles[sid]["layer"].index(" states", a)
        new[sid] = [p for p in new[sid] if p["span"] != [a, b]] + [
            passage(w, sid, "In-text #0000f001", [a, cut_at]),
            passage(w, sid, "In-text #0000f002", [cut_at + 1, b])]
        doc = self.carry(self.paragraphs(), new)
        self.assertEqual(doc["schema"], "cf-bench-carry/1")
        self.assertEqual(doc["gold"]["version"], 1)
        self.assertEqual(doc["labeler"], {"gold": gold_mod.LABELER_VERSION,
                                          "current": gold_mod.LABELER_VERSION, "bumped": False})
        self.assertIsNone(doc["note"])
        self.assertEqual((doc["from"]["inventory_run"], doc["to"]["inventory_run"]),
                         ("inv-a", "inv-b"))
        moved = [r for r in doc["rows"] if r["source_id"] == sid
                 and r["span"][0] < b and a < r["span"][1]]
        self.assertIn(self.g1_row["gold_id"], {r["gold_id"] for r in moved})
        for row in moved:
            self.assertFalse(row["carried"])
            self.assertEqual([p["fate"] for p in row["passages"]], ["split"])
            self.assertIn("split", row["reason"])
        rest = [r for r in doc["rows"] if r not in moved]
        self.assertTrue(rest)
        for row in rest:
            self.assertTrue(row["carried"], row)
            self.assertIsNone(row["reason"])
            self.assertEqual({p["fate"] for p in row["passages"]}, {"unchanged"})
            self.assertEqual([n["passage_key"] for n in row["into"]],
                             [p["passage_key"] for p in row["passages"]])
        total = doc["counts"]["total"]
        self.assertEqual((total["rows"], total["relabel"], total["carried"]),
                         (len(doc["rows"]), len(moved), len(rest)))
        self.assertEqual(total["no_span"], sum(1 for r in self.g1 if not r.get("span")))

    def test_an_unmoved_recut_carries_every_row_and_writes_the_same_bytes(self):
        doc = self.carry(self.paragraphs(), self.paragraphs())
        self.assertEqual(doc["counts"]["total"]["relabel"], 0)
        first = self.layout.gold_carry_json(1).read_bytes()
        tlb.cf(self.world, "bench", "carry", "b1", "--from", "inv-a", "--to", "inv-b")
        self.assertEqual(self.layout.gold_carry_json(1).read_bytes(), first)

    def test_a_span_over_several_passages_carries_when_none_moved(self):
        old = self.with_g3(self.paragraphs(), self.g3_halves())
        doc = self.carry(old, self.with_g3(self.paragraphs(), self.g3_halves()))
        row = self.row(doc, self.g3_row)
        self.assertTrue(row["carried"])
        self.assertEqual(len(row["passages"]), 2)

    def test_a_span_over_several_passages_is_relabelled_when_one_moved(self):
        old = self.with_g3(self.paragraphs(), self.g3_halves())
        doc = self.carry(old, self.with_g3(self.paragraphs(), self.g3_halves(shorten=True)))
        row = self.row(doc, self.g3_row)
        self.assertFalse(row["carried"])
        self.assertEqual([p["fate"] for p in row["passages"]], ["unchanged", "end_moved"])
        self.assertIn("In-text #0000aa02 over it is end_moved", row["reason"])

    def test_a_span_no_passage_covers_carries_until_one_does(self):
        doc = self.carry(self.with_g3(self.paragraphs(), None),
                         self.with_g3(self.paragraphs(), None))
        row = self.row(doc, self.g3_row)
        self.assertTrue(row["carried"])
        self.assertEqual((row["passages"], row["into"]), ([], []))
        doc = self.carry(self.with_g3(self.paragraphs(), None),
                         self.with_g3(self.paragraphs(), self.g3_halves()))
        row = self.row(doc, self.g3_row)
        self.assertFalse(row["carried"])
        self.assertIn("not one of the old cut's", row["reason"])

    def test_across_a_labeler_bump_every_row_is_relabelled(self):
        inventory(self.world, "inv-a", self.paragraphs())
        inventory(self.world, "inv-b", self.paragraphs())
        with mock.patch.object(gold_mod, "LABELER_VERSION", "locate-label/99"):
            out = tlb.cf(self.world, "bench", "carry", "b1", "--from", "inv-a", "--to", "inv-b")
        doc = json.loads(self.layout.gold_carry_json(1).read_text(encoding="utf-8"))
        self.assertIn("every row is relabelled", out)
        self.assertTrue(doc["labeler"]["bumped"])
        self.assertIn("every row is relabelled", doc["note"])
        self.assertEqual(doc["counts"]["total"]["carried"], 0)
        for row in doc["rows"]:
            self.assertFalse(row["carried"])
            self.assertIn("locate-label/99", row["reason"])

    def test_a_new_revision_of_a_gold_article_is_anchored_again_not_carried(self):
        w, sid = self.world, self.g3_row["source_id"]
        i = w.articles[sid]["index"]
        paras = fx.article_paragraphs(i)
        rev = new_revision(w, i, 4000 + i, ["A sentence opens this revision."] + paras)
        new = self.paragraphs()
        del new[sid]
        new[rev] = paragraphs_cut(w, rev, 9)
        doc = self.carry(self.paragraphs(), new)
        rows = [r for r in doc["rows"] if r["source_id"] == sid]
        self.assertTrue(rows)
        for row in rows:
            self.assertFalse(row["carried"])
            self.assertIn("anchored again, not carried", row["reason"])

    def test_gold_changed_after_freeze_is_refused(self):
        inventory(self.world, "inv-a", self.paragraphs())
        path = self.layout.label_file("G3")
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        rc, _, err = cf(self.world, "bench", "carry", "b1", "--from", "inv-a", "--to", "inv-a")
        self.assertEqual(rc, 2)
        self.assertIn("changed after freeze", err)
        self.assertFalse(self.layout.gold_carry_json(1).exists())

    def test_an_unfrozen_bench_is_refused(self):
        world = tlb.labelled(self)
        inventory(world, "inv-a", {})
        rc, _, err = cf(world, "bench", "carry", "b1", "--from", "inv-a", "--to", "inv-a")
        self.assertEqual(rc, 2)
        self.assertIn("not frozen", err)


if __name__ == "__main__":
    unittest.main()
