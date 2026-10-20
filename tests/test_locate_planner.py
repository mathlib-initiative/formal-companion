"""The locate planner: candidates, plan-locate, prompts, outcomes, records, export.

New code, for docs/proposals/wikipedia-locate.md sections 2.6, 2.7, 3, 5, 6,
7 and 11.1 in the formats of references/locate-documents.md.  The sourceset,
the universe and ``corpus.sqlite`` are made by their owners' own producers
(tests/_locate_fixtures.py); every page, library and declaration is made up.
What is pinned: L2's curated joins and their refusals, the three presets and
the plan's bounds and determinism, the two databases a run names, the
gate.json, task-manifest and run.yaml shapes of section 9, the staged gate
directory and ``bin/``, the wrapper variables, the prompts, the finding rows
of section 10 and their ids, the challenge rules and the seeded sample, the
derived status after challenge, the passage-id tool, the record builder's caps
and shapes against the vendored checker, and the sealed set.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _locate_fixtures as F  # noqa: E402
from cflib import hashing, items, vocab  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from cflib.yamlio import read_yaml  # noqa: E402
from locate import (candidates as cand_mod, challenge_plan, export as export_mod,  # noqa: E402
                    inputs, outcomes, passage_ids, plan as lplan, presets as lp, records)
from locate import tend_root as ltr  # noqa: E402
from plan import make_tend_root as mtr, presets as presets_mod  # noqa: E402


def scratch(case: unittest.TestCase) -> Path:
    """A fresh workspace path, resolved.

    ``cflib.workspace`` resolves the workspace root, so every path the planner
    writes is a resolved one.  On macOS ``tempfile`` hands out ``/var/folders/…``,
    a symlink to ``/private/var/folders/…``, and a test that built its expected
    paths from the unresolved name compared two spellings of one directory.
    """
    path = Path(tempfile.mkdtemp()).resolve()
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path / "ws"


def ws(root: Path, *argv: str) -> tuple[int, str, str]:
    return F.cf("--workspace", str(root), *argv)


def allow_host(paths) -> None:
    """Allowlist ``examplebox`` (remote root ``/srv/cf``) in the workspace."""
    from cflib.yamlio import write_yaml
    data = read_yaml(paths.workspace_yaml)
    data["hosts"] = {"allowlist": ["examplebox"], "examplebox": {"remote_root": "/srv/cf"}}
    write_yaml(paths.workspace_yaml, data)


#: the producer's layer sha and this renderer's, for a standing parity refusal
PRODUCER_SHA, OURS_SHA = "1" * 64, "2" * 64


def parity_refuse(paths, sid: str) -> None:
    """Make ``sid`` (unrendered) a standing parity refusal, as `cf sourceset render` leaves one:
    its pin carried the producer's layer sha, and the render measured another."""
    from sources import render_html
    from sources.sourceset import Sourceset
    ss = Sourceset.open(paths, F.SS)
    base = ss.latest()
    pinned = {**base.get(sid), "adopted_from": {"consumer": "wikilean", "pins_sha256": "3" * 64,
                                                "layer_sha256": PRODUCER_SHA}}
    ss.write_version(base, [pinned if e["source_id"] == sid else e for e in base])
    ss.record_parity_refusals({sid: {"layer_sha256": OURS_SHA, "renderer": render_html.RENDERER,
                                     "producer_layer_sha256": PRODUCER_SHA}})
    assert list(ss.parity_refusals()) == [sid]


def ablate(source: Path, planted: Path, *modules: tuple[str, str]) -> set[str]:
    """A copy of ``source`` without ``modules`` (``(library, module)``), the way ``cf bench
    freeze --plant`` makes the P tier's database; returns the removed declarations."""
    import sqlite3
    shutil.copy2(source, planted)
    conn = sqlite3.connect(str(planted))
    removed: set[str] = set()
    try:
        for library, module in modules:
            for decl_id, name in conn.execute(
                    "SELECT id, name FROM decls WHERE library = ? AND module = ?",
                    (library, module)).fetchall():
                removed.add(name)
                conn.execute("DELETE FROM tags WHERE decl_id = ?", (decl_id,))
                conn.execute("DELETE FROM decls_fts WHERE rowid = ?", (decl_id,))
            conn.execute("DELETE FROM decls WHERE library = ? AND module = ?", (library, module))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('planted', 'b0')")
        conn.commit()
    finally:
        conn.close()
    return removed


class Campaign(unittest.TestCase):
    """A workspace with a rendered sourceset, a universe, candidates and inventory ``inv-1``."""

    def setUp(self):
        self.root = scratch(self)
        self.paths = F.campaign(self, self.root)

    def locate(self, *extra: str, run_id: str = "loc-1"):
        F.plan_locate(self, self.root, *extra, run_id=run_id)
        F.write_harvest(self.paths, run_id, F.locate_documents(self.paths, run_id))

    def challenge(self, *extra: str, run_id: str = "chl-1", verdict=None):
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-challenge",
                            "--sourceset", F.SS, "--universe", F.U, "--challenge-of", "loc-1",
                            "--run-id", run_id, *extra)
        self.assertEqual(code, 0, out + err)
        F.write_harvest(self.paths, run_id,
                        F.challenge_documents(self.paths, run_id, verdict=verdict))


# --------------------------------------------------------------------------
# L2 candidates
# --------------------------------------------------------------------------

class TestCandidates(Campaign):

    def doc(self, sid=F.W1, kind=None):
        if kind is None:
            return read_json(self.paths.candidates_json(F.SS, F.U, sid))
        return read_json(self.paths.candidates_kind_dir(F.SS, F.U, kind) / f"{sid}.json")

    def test_the_curated_joins_and_the_lexical_hits(self):
        doc = self.doc()
        self.assertEqual(doc["schema"], "cf-locate-candidates/1")
        self.assertEqual(doc["universe_sha256"], F.universe_pin(self.paths)["sha256"])
        curated = {(h["library"], h["declaration"], h["via"]) for h in doc["curated"]}
        self.assertEqual(curated, {("alpha", "Widget.exists_spanning_gadget", "wikidata_tag"),
                                   ("beta", "Beta.Widget.gadget_of_finite", "docstring_url")})
        lexical = {(h["library"], h["declaration"]) for h in doc["lexical"]}
        self.assertFalse(lexical & {(l, d) for l, d, _ in curated}, "a curated hit repeated")
        self.assertTrue(all(h["matched"] in ("title", "bold_term") for h in doc["lexical"]))
        self.assertTrue(all(h["via"] in vocab.CANDIDATE_VIA for h in doc["curated"]))
        self.assertEqual(doc["corpus_db"], {"kind": "full", "sha256": hashing.sha256_file(
            self.paths.corpus_db(F.U))})

    def test_the_terms_are_the_title_and_the_leads_bold_terms(self):
        """Seam #4, #5: no alias field exists, and a bold word outside the lead (a
        theorem box's label, a term defined in passing) is not what the article is about."""
        w1 = self.doc()
        self.assertEqual(w1["terms"], ["Widget lemma"])     # the lead's bold term is the title
        self.assertNotIn("spanning", w1["terms"])
        self.assertNotIn("Theorem.", w1["terms"])
        self.assertEqual(self.doc(F.W2)["terms"], ["Gadget theorem", "gadget lattice theorem"])

    def test_the_lexical_hits_are_the_host_tools_own_search(self):
        """Seam #13, #81: one definition of the word search, ``corpus.query.search``."""
        from corpus import query
        conn = query.open_db(self.paths.corpus_db(F.U))
        self.addCleanup(conn.close)
        doc = self.doc(F.W2)
        found = {(r["library"], r["declaration"]) for term in doc["terms"]
                 for r in query.search(conn, term, limit=50)}
        self.assertTrue(doc["lexical"])
        self.assertLessEqual({(h["library"], h["declaration"]) for h in doc["lexical"]}, found)

    def test_the_curated_lists_are_joined_and_a_phantom_is_dropped_by_name(self):
        lists = self.root.parent / "lists"
        lists.mkdir()
        (lists / "1000.json").write_text(json.dumps({
            "Q99999002": {"title": "Gadget theorem", "decls": ["Beta.gadget_lattice_height",
                                                              "Beta.no_such_thing"]}}))
        (lists / "100.yaml").write_text("7:\n  title: The Widget Lemma\n  decl: Widget.card_dvd\n")
        (lists / "meta.json").write_text(json.dumps({"libraries": {"alpha": [
            {"title": "Gadget theorem", "declarations": ["Widget.spanning_conj"]}]}}))
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--curated-1000", str(lists / "1000.json"),
                            "--curated-100", str(lists / "100.yaml"), "--curated-library",
                            "beta", "--library-metadata", str(lists / "meta.json"))
        self.assertEqual(code, 0, out + err)
        w2 = self.doc(F.W2)
        vias = {(h["declaration"], h["via"]) for h in w2["curated"]}
        self.assertIn(("Beta.gadget_lattice_height", "mathlib_1000"), vias)
        self.assertIn(("Widget.spanning_conj", "library_metadata"), vias)
        self.assertEqual([(d["declaration"], d["reason"]) for d in w2["dropped"]],
                         [("Beta.no_such_thing", "no such declaration at the universe's commit")])
        # 100.yaml's title matches "Widget lemma" (case, a leading "The"), but names beta's
        # library, where Widget.card_dvd is not: dropped, never kept as a phantom
        w1 = self.doc(F.W1)
        self.assertIn(("beta", "Widget.card_dvd", "mathlib_100"),
                      {(d["library"], d["declaration"], d["via"]) for d in w1["dropped"]})

    def test_the_blind_arm_writes_no_curated_hit_and_says_so(self):
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--blind")
        self.assertEqual(code, 0, out + err)
        doc = self.doc(kind="blind")
        self.assertTrue(doc["blind"])
        self.assertEqual((doc["curated"], doc["dropped"], doc["article_qid"]), ([], [], None))
        self.assertTrue(doc["lexical"])
        self.assertEqual(doc["corpus_db"]["kind"], "blind")
        self.assertEqual(doc["corpus_db"]["sha256"],
                         hashing.sha256_file(self.paths.corpus_db_blind(F.U)))
        # review #38: the arms keep apart -- the assisted file is untouched
        self.assertFalse(self.doc()["blind"])
        self.assertTrue(self.doc()["curated"])
        # review #37: no join shaped the blind list; it is the plain lexical retrieval
        from corpus import query
        conn = query.open_db(self.paths.corpus_db_blind(F.U))
        self.addCleanup(conn.close)
        terms = [(t, "title" if i == 0 else "bold_term") for i, t in enumerate(doc["terms"])]
        plain = cand_mod.lexical_hits(terms, inputs.load_universe(self.paths, F.U), conn,
                                      limit=cand_mod.DEFAULT_LIMIT, exclude=set())
        self.assertEqual(doc["lexical"], plain)
        self.assertIn(("alpha", "Widget.exists_spanning_gadget"),
                      {(h["library"], h["declaration"]) for h in doc["lexical"]})

    def test_the_blind_arm_needs_the_blind_database(self):
        """D2: --blind reads corpus.blind.sqlite, and refuses when it was never built."""
        self.paths.corpus_db_blind(F.U).unlink()
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--blind")
        self.assertNotEqual(code, 0)
        self.assertIn(f"cf corpus build-db {F.U} --blind", out + err)

    def test_a_planted_database_is_searched_and_must_be_a_full_build(self):
        """D2 (P tier): --tools-corpus-db names the planted file; a blind one is refused."""
        planted = self.root.parent / "planted.sqlite"
        shutil.copy2(self.paths.corpus_db(F.U), planted)
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--tools-corpus-db", str(planted))
        self.assertEqual(code, 0, out + err)
        sha = hashing.sha256_file(planted)
        self.assertEqual(self.doc(kind=f"planted-{sha[:16]}")["corpus_db"],
                         {"kind": "planted", "sha256": sha})
        shutil.copy2(self.paths.corpus_db_blind(F.U), planted)
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--tools-corpus-db", str(planted))
        self.assertNotEqual(code, 0)
        self.assertIn("G1-blind", out + err)
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--tools-corpus-db", str(planted), "--blind")
        self.assertNotEqual(code, 0)
        self.assertIn("two different arms", out + err)

    def test_a_planted_file_names_no_declaration_its_database_lacks(self):
        """Review C-02: the P tier's file travels to its workers, so a hit the planted
        database lacks -- curated, dropped or refused for its QID -- is withheld from every
        field, and counted only on this machine; the joins and the reasons are the full
        corpus.sqlite's, where the ablated declarations still exist."""
        planted = self.root.parent / "planted.sqlite"
        removed = ablate(self.paths.corpus_db(F.U), planted, ("alpha", "Alpha.Widget"),
                         ("beta", "Beta.Widget"))
        self.assertIn("Widget.exists_spanning_gadget", removed)
        lists = self.root.parent / "lists"
        lists.mkdir()
        (lists / "1000.json").write_text(json.dumps({"Q99999001": {
            "decls": ["Widget.exists_spanning_gadget", "Widget.no_such_thing"]}}))
        (lists / "100.yaml").write_text(
            "7:\n  title: Gadget theorem\n  wikidata: Q99999001\n  decl: Widget.card_dvd\n")
        (lists / "meta.json").write_text(json.dumps({"libraries": {"alpha": [
            {"wikidata": "Q99999001", "declarations": ["Widget.spanning_conj"]}]}}))
        argv = ("locate", "candidates", "--sourceset", F.SS, "--universe", F.U,
                "--curated-1000", str(lists / "1000.json"), "--curated-100",
                str(lists / "100.yaml"), "--curated-library", "alpha",
                "--library-metadata", str(lists / "meta.json"))
        code, out, err = ws(self.root, *argv, "--tools-corpus-db", str(planted))
        self.assertEqual(code, 0, out + err)
        self.assertIn("7 withheld", out + err)
        kind = f"planted-{hashing.sha256_file(planted)[:16]}"
        for sid in (F.W1, F.W2):
            text = (self.paths.candidates_kind_dir(F.SS, F.U, kind) / f"{sid}.json").read_text()
            doc = json.loads(text)
            named = {h["declaration"] for field in ("curated", "dropped", "lexical")
                     for h in doc[field]}
            self.assertFalse(named & removed, f"{sid} names an ablated declaration")
            for name in sorted(removed | {"Widget.no_such_thing"}):
                self.assertNotRegex(text, rf"(?<![\w.]){re.escape(name)}(?![\w.])")
        w2 = self.doc(F.W2, kind=kind)
        self.assertEqual({(h["declaration"], h["via"]) for h in w2["curated"]},
                         {("Beta.gadget_lattice_height", "wikidata_tag")})
        st = read_json(self.paths.sourceset_state_json(F.SS))["candidates"][f"{F.U}/{kind}"]
        self.assertEqual(st["withheld"], 7)
        # the full arm's file names them all, each with a true reason
        code, out, err = ws(self.root, *argv)
        self.assertEqual(code, 0, out + err)
        w1 = self.doc(F.W1)
        self.assertIn(("Widget.exists_spanning_gadget", "mathlib_1000"),
                      {(h["declaration"], h["via"]) for h in w1["curated"]})
        self.assertEqual([(d["declaration"], d["reason"]) for d in w1["dropped"]],
                         [("Widget.no_such_thing", "no such declaration at the universe's commit")])
        self.assertEqual(read_json(self.paths.sourceset_state_json(F.SS))
                         ["candidates"][F.U]["withheld"], 0)

    def test_a_database_built_for_another_state_of_the_universe_is_refused(self):
        """Seam #20: meta.universe_sha256 must be the universe's, with no escape."""
        from corpus.universe import Universe
        u = Universe.load(self.paths.universe_yaml(F.U))
        index = self.paths.universe_dir(F.U) / "index" / "alpha" / "decls.jsonl"
        index.write_text(index.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        u.set_index("alpha", path="index/alpha/", sha256=hashing.sha256_file(index),
                    declarations=len(F.DECLS["alpha"]))
        u.save()
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U)
        self.assertNotEqual(code, 0)
        self.assertIn("another state of universe", out + err)

    def test_an_unrendered_named_source_is_refused(self):
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--source", F.W3)
        self.assertNotEqual(code, 0)
        self.assertIn("not rendered", out + err)

    def test_a_named_source_render_refused_for_parity_is_named_so_seam2(self):
        """Review SEAM-2: no render settles a standing parity refusal, so the refusal names
        it and the step that does (a corrected delivery in a new sourceset)."""
        parity_refuse(self.paths, F.W3)
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--source", F.W3)
        self.assertNotEqual(code, 0)
        self.assertIn("because the layer is not the producer's", out + err)
        self.assertIn(f"the producer recorded {PRODUCER_SHA[:12]}", out + err)
        self.assertIn("cf sourceset adopt <new id>", out + err)
        self.assertNotIn("render it:", out + err)

    def test_an_unnamed_source_render_refused_for_parity_is_reported_so_seam2(self):
        """Review SEAM-2, its residual: a run over every source reports a standing parity
        refusal apart, never as a source with "no candidates file yet"."""
        parity_refuse(self.paths, F.W3)
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U)
        self.assertEqual(code, 0, out + err)
        self.assertIn("1 source(s) have no candidates file because render refused them, the "
                      f"layer not the producer's: {F.W3}", out + err)
        self.assertIn(f"no render of {F.SS} settles that", out + err)
        self.assertNotIn("not rendered and have no candidates file yet", out + err)
        self.assertFalse(self.paths.candidates_json(F.SS, F.U, F.W3).exists())

    def test_the_state_file_records_the_run_through_its_one_writer(self):
        st = read_json(self.paths.sourceset_state_json(F.SS))
        self.assertEqual((st["schema"], st["id"]), ("cf-sourceset-state/1", F.SS))
        self.assertEqual(st["candidates"][F.U]["sources"], 2)
        self.assertEqual(st["candidates"][F.U]["skipped_unrendered"], 1)
        self.assertEqual(st["candidates"][F.U]["corpus_db"]["kind"], "full")
        self.assertIn("counts", st)                 # the sourceset's own section survives


# --------------------------------------------------------------------------
# presets and readers
# --------------------------------------------------------------------------

class TestPresetsAndReaders(unittest.TestCase):

    def test_the_three_presets_carry_their_modes_kinds(self):
        for mode in vocab.LOCATE_MODES:
            preset = lp.get(mode)
            self.assertEqual(preset.mode, mode)
            self.assertEqual(set(preset.kinds), vocab.kinds_for_mode(mode))
            self.assertLessEqual(preset.max_workers + preset.max_reviewers, 15)
        self.assertEqual(lp.shape(vocab.LOCATE).max_per_task, 8)

    def test_plan_run_refuses_them_and_names_plan_locate(self):
        for mode in vocab.LOCATE_MODES:
            with self.subTest(mode=mode), self.assertRaises(CfError) as caught:
                presets_mod.get(mode)
            self.assertIn("cf plan-locate", caught.exception.hint)
        for mode in vocab.LOCATE_MODES:
            self.assertNotIn(mode, presets_mod.RUNNABLE)

    def test_the_manifest_is_the_owners_and_refused_as_the_owner_refuses(self):
        """Seam #1, #82: ``Sourceset``'s validation, not a private reader's."""
        root = scratch(self)
        paths = F.make_workspace(root)
        path = F.write_sourceset(paths)
        manifest = inputs.manifest(paths, F.SS)
        self.assertEqual((manifest.sourceset_id, manifest.version), (F.SS, 2))
        self.assertEqual(inputs.manifest(paths, F.SS, 1).entries, {})
        self.assertEqual(inputs.pinned_manifest(paths, manifest.pin()).sha256, manifest.sha256)
        self.assertEqual(sorted(s for s, e in manifest.entries.items() if inputs.rendered(e)),
                         [F.W1, F.W2])
        lines = path.read_text().splitlines()
        path.write_text("\n".join([lines[0], lines[2], lines[1], *lines[3:]]) + "\n")
        with self.assertRaises(CfError):
            inputs.manifest(paths, F.SS)
        path.write_text("\n".join(lines[1:]) + "\n")
        with self.assertRaises(CfError):
            inputs.manifest(paths, F.SS)

    def test_a_missing_view_is_refused_not_read_as_empty(self):
        """Seam #2: a source with no finders.json is not a source with no finders."""
        root = scratch(self)
        paths = F.make_workspace(root)
        F.write_sourceset(paths)
        view = inputs.source_view(paths, F.SS, F.W1)
        self.assertTrue(view.finders())
        directory = paths.sourceset_source_dir(F.SS, F.W1)
        (directory / "finders.json").unlink()
        with self.assertRaises(CfError) as caught:
            view.finders()
        self.assertIn(f"cf sourceset render {F.SS} --source {F.W1}", caught.exception.hint)
        self.assertTrue(view.paragraphs())
        (directory / "formulas.json").unlink()
        with self.assertRaises(CfError):
            view.layer()

    def test_a_universe_whose_sha_is_not_its_libraries_is_refused(self):
        root = scratch(self)
        paths = F.make_workspace(root)
        F.write_universe(paths)
        text = paths.universe_yaml(F.U).read_text()
        paths.universe_yaml(F.U).write_text(text.replace(F.ALPHA, "c" * 40))
        with self.assertRaises(CfError) as caught:
            inputs.load_universe(paths, F.U)
        self.assertIn("universe_sha256", str(caught.exception))

    def test_the_universe_is_the_owners(self):
        """Seam #18: ``corpus.universe.Universe.load`` behind the planner's view."""
        root = scratch(self)
        paths = F.make_workspace(root)
        F.write_universe(paths)
        u = inputs.load_universe(paths, F.U)
        self.assertEqual(u.sha256, u.owner.sha256())
        self.assertEqual(sorted(u.libraries), ["alpha", "beta"])
        self.assertEqual(u.commit("beta"), F.BETA)
        self.assertEqual([x["id"] for x in u.excluded], ["gamma"])
        self.assertIsNone(u.mathlib_library())


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

class TestPlanInventory(Campaign):

    def gate(self, run="inv-1", local=False):
        rd = self.paths.run_dir(run)
        return read_json(rd / ("gate.local.json" if local else "gate.json"))

    def test_one_task_per_rendered_source_with_the_finders_prefilled(self):
        tasks = sorted(p.name for p in self.paths.tasks_dir("inv-1").glob("*.yaml"))
        self.assertEqual(tasks, [f"inv-{F.W1}.yaml", f"inv-{F.W2}.yaml"])
        manifest = read_json(self.paths.tend_root("inv-1") / "gate" / "tasks" / f"inv-{F.W1}.json")
        self.assertEqual(manifest["mode"], "locate-inventory")
        (doc,) = manifest["documents"]
        self.assertEqual(set(doc), {"source_id", "source_sha", "layer_sha256", "renderer",
                                    "finders"})
        self.assertEqual({f["finder"] for f in doc["finders"]},
                         {"lead", "bold_definition", "theorem_box"})
        text = (self.paths.tasks_dir("inv-1") / f"inv-{F.W1}.yaml").read_text()
        self.assertIn("title: \"inventory: enwiki-1001-2002\"", text)
        self.assertIn(f"findings/inv-{F.W1}/{F.W1}.json", text)
        self.assertIn("priority: high", text)          # importance: high

    def test_one_passage_per_finder_start_is_what_is_counted(self):
        """Seam #0: the rows stay one per (finder, start); the worker writes one per start."""
        manifest = read_json(self.paths.tend_root("inv-1") / "gate" / "tasks" / f"inv-{F.W1}.json")
        finders = manifest["documents"][0]["finders"]
        # the lead is also a bold definition (two rows, one start); the theorem box's
        # bold "Theorem." only labels its paragraph, so it proposes no bold_definition
        # (web-html/2: cflib.vocab.is_bold_label) and the box start is one row
        self.assertEqual(len(finders), 4)
        self.assertEqual(len({f["start"] for f in finders}), 3)
        by_start: dict[int, set] = {}
        for f in finders:
            by_start.setdefault(f["start"], set()).add(f["finder"])
        self.assertIn({"lead", "bold_definition"}, by_start.values())
        self.assertIn({"theorem_box"}, by_start.values())
        text = (self.paths.tasks_dir("inv-1") / f"inv-{F.W1}.yaml").read_text()
        self.assertIn("(3 finder passage(s))", text)
        block = read_yaml(self.paths.run_yaml("inv-1"))["locate"]
        self.assertEqual(block["passages"], 3 + 1)
        worker = (self.paths.prompts_dir("inv-1") / "worker.md").read_text()
        self.assertIn("write ONE passage per start", worker)
        self.assertNotIn("Keep every one of them as a passage", worker)

    def test_a_source_whose_files_moved_is_refused_at_plan_time(self):
        """Seam #2: the files on disk must be the ones the entry pins."""
        body = self.paths.sourceset_source_dir(F.SS, F.W1) / "body.html"
        body.write_bytes(body.read_bytes() + b" ")
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-x")
        self.assertNotEqual(code, 0)
        self.assertIn("body.html has sha", out + err)
        self.assertIn(f"cf sourceset render {F.SS} --source {F.W1}", out + err)

    def test_a_missing_candidates_file_is_refused(self):
        """Seam #32: the gate's curated-hit rule cannot run without one."""
        self.paths.candidates_json(F.SS, F.U, F.W2).unlink()
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-x")
        self.assertNotEqual(code, 0)
        self.assertIn(F.W2, out + err)
        self.assertIn(f"cf locate candidates --sourceset {F.SS} --universe {F.U}", out + err)

    def test_a_source_render_refused_for_parity_is_skipped_as_such_seam2(self):
        """Review SEAM-2: locate.skipped says "parity refused", not "not rendered", and a
        --source naming it is refused with the step that settles it, not a render."""
        parity_refuse(self.paths, F.W3)
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-p")
        self.assertEqual(code, 0, out + err)
        block = read_yaml(self.paths.run_yaml("inv-p"))["locate"]
        self.assertEqual(block["skipped"], {F.W3: "parity refused"})
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-q",
                            "--source", F.W3)
        self.assertNotEqual(code, 0)
        self.assertIn("render refused", out + err)
        self.assertIn(f"{F.W3} (the layer is {OURS_SHA[:12]}", out + err)
        self.assertIn("adopt the producer's corrected delivery into a new sourceset", out + err)
        self.assertNotIn("render it:", out + err)

    def test_one_source_per_page_the_preferred_one(self):
        """``Manifest.preferred()``: an adopted revision of a page is planned instead."""
        from sources import websource
        from sources.sourceset import Sourceset
        ss = Sourceset.open(self.paths, F.SS)
        base = ss.latest()
        adopted = websource.entry(websource.family("enwiki"), 1001, 2003, title="Widget lemma",
                                  origin="adopted")
        ss.write_version(base, [*base, adopted])
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-p")
        self.assertEqual(code, 0, out + err)
        block = read_yaml(self.paths.run_yaml("inv-p"))["locate"]
        self.assertEqual(block["source_ids"], [F.W2])
        self.assertEqual(block["skipped"], {"enwiki-1001-2003": "not rendered",
                                            F.W3: "not rendered"})
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-q",
                            "--source", F.W1)
        self.assertNotEqual(code, 0)
        self.assertIn("page 1001 plans enwiki-1001-2003", out + err)

    def test_gate_json_is_the_section_9_shape(self):
        gate = self.gate()
        self.assertEqual(set(gate), {
            "schema", "mode", "preset", "run_id", "kinds", "universe", "corpus_db",
            "tools_corpus_db", "sourceset", "sources", "candidates_dir", "blind", "inventory",
            "challenged_runs", "findings_dir", "tasks_dir", "log_dir", "orchestrator_db",
            "max_locations", "min_absence_queries", "judgment_rules", "vendor",
            "candidates_sha256"})
        self.assertEqual(gate["schema"], "cf-gate-locate/1")
        self.assertEqual(gate["judgment_rules"], vocab.JUDGMENT_RULES)
        self.assertEqual(gate["kinds"], ["PASSAGE", "UNRESOLVED"])
        self.assertEqual(sorted(gate["sources"]), [F.W1, F.W2])
        self.assertEqual(gate["universe"]["libraries"]["alpha"]["commit"], F.ALPHA)
        db_sha = hashing.sha256_file(self.paths.corpus_db(F.U))
        self.assertEqual(gate["corpus_db"], {"path": str(self.paths.corpus_db(F.U)),
                                             "sha256": db_sha})
        self.assertEqual(gate["tools_corpus_db"], {"path": str(self.paths.corpus_db(F.U)),
                                                   "sha256": db_sha, "kind": "full"})
        manifest = inputs.manifest(self.paths, F.SS)
        self.assertEqual(gate["sourceset"]["manifest_sha256"], manifest.sha256)
        self.assertEqual(set(gate["vendor"]), {"literature_links", "formal_companion_annotations"})
        self.assertIsNone(gate["inventory"])

    def test_the_local_gate_logs_apart_from_the_workers(self):
        """Seam #68 (D3): a gate run on this machine never writes into the workers' log."""
        self.assertEqual(self.gate()["log_dir"], str(self.paths.tend_root("inv-1") / "gate-log"))
        self.assertEqual(self.gate(local=True)["log_dir"],
                         str(self.paths.run_dir("inv-1") / "gate-local-log"))

    def test_the_tend_root_stages_the_whole_locate_gate_directory(self):
        """Seam #9, #10, #22, #27, #39, #71 (D1): the gate, the findings gate it imports,
        both corpus tools, the records, the text rule, the vendored packages."""
        gate_dir = self.paths.tend_root("inv-1") / "gate"
        for name in ("gate.json", "verify_locate.py", "verify_findings.py", "locate_text.py",
                     "passage_ids.py", "locate_records.py", "corpus_search.py",
                     "corpus_show.py", "vendor/literature_links/companion.py",
                     "vendor/formal_companion_annotations/ids.py",
                     "vendor/literature_links.manifest.json",
                     "vendor/formal_companion_annotations.manifest.json",
                     f"tasks/inv-{F.W1}.json"):
            self.assertTrue((gate_dir / name).is_file(), name)
        self.assertEqual(
            (gate_dir / "verify_findings.py").read_bytes(),
            (SCRIPTS / "gate" / "verify_findings.py").read_bytes())
        self.assertFalse(list(gate_dir.rglob("__pycache__")))
        # the gate imports verify_findings' change-set reader (field defect P2)
        self.assertTrue((gate_dir / "verify_findings.py").is_file())
        cfg = (self.paths.tend_root("inv-1") / "config.yaml").read_text()
        self.assertIn(f"{gate_dir}/verify_locate.py, --gate-config", cfg)
        self.assertIn("--changed-files", cfg)

    def test_the_staged_gate_and_tools_run_from_outside_the_checkout(self):
        """Seam #71: the staged directory imports nothing from this checkout."""
        gate_dir = self.paths.tend_root("inv-1") / "gate"
        elsewhere = self.root.parent / "elsewhere"
        elsewhere.mkdir()
        for script in ("verify_locate.py", "corpus_search.py", "corpus_show.py",
                       "passage_ids.py"):
            proc = subprocess.run([sys.executable, "-I", str(gate_dir / script), "--help"],
                                  cwd=elsewhere, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"{script}: {proc.stderr}")

    def test_bin_holds_the_bridge_and_every_helper(self):
        """Seam #28 (D1): the bridge exits 78 without agent_call.py and attempt_cap.py."""
        bindir = self.paths.tend_root("inv-1") / "bin"
        for name in ("bridge-agentbox.sh", "normalize_result.py", "extract_final_json.py",
                     "agent_call.py", "attempt_cap.py", "watchdog.py"):
            self.assertTrue((bindir / name).is_file(), name)
            self.assertEqual((bindir / name).read_bytes(),
                             (SCRIPTS / "run" / name).read_bytes(), name)

    def test_a_gate_file_this_checkout_lacks_is_a_planning_error(self):
        """D1: a missing required file is refused, never a root that fails on the host."""
        absent = "/".join(("scripts", "gate", "no_such_gate_file.py"))   # not a citation
        rows = (*ltr.GATE_FILES, (absent, "no_such_gate_file.py"))
        with mock.patch.object(ltr, "GATE_FILES", rows):
            code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                                "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-x")
        self.assertNotEqual(code, 0)
        self.assertIn("gate file missing", out + err)
        self.assertIn("no_such_gate_file.py", out + err)

    def test_the_wrappers_export_the_locate_variables(self):
        bindir = self.paths.tend_root("inv-1") / "bin"
        worker = (bindir / "worker-agent.sh").read_text()
        reviewer = (bindir / "reviewer-agent.sh").read_text()
        gate_dir = self.paths.tend_root("inv-1") / "gate"
        db = self.paths.corpus_db(F.U)
        self.assertIn(f'CF_EXTRA_TOOLS="Bash(python3 {gate_dir}/corpus_search.py --db {db}:*),'
                      f'Bash(python3 {gate_dir}/corpus_show.py --db {db}:*),'
                      f'Bash(python3 {gate_dir}/passage_ids.py:*)"', worker)
        self.assertNotIn("CF_EXTRA_TOOLS", reviewer)
        for text in (worker, reviewer):
            self.assertIn(f'CF_TASK_MANIFESTS="{gate_dir}/tasks"', text)
            self.assertIn('CF_ATTEMPT_CAP="3"', text)
            self.assertIn('CF_FINDINGS_GLOB="findings/${TEND_TASK_ID:-*}/*.json"', text)
            self.assertIn(f'CF_GATE_CMD="python3 {gate_dir}/verify_locate.py --gate-config', text)

    def test_run_yaml_carries_the_locate_block(self):
        doc = read_yaml(self.paths.run_yaml("inv-1"))
        block = doc["locate"]
        self.assertEqual(block["mode"], "locate-inventory")
        self.assertEqual(block["sourceset"]["version"], 2)
        db_sha = hashing.sha256_file(self.paths.corpus_db(F.U))
        self.assertEqual(block["universe"]["corpus_db_sha256"], db_sha)
        self.assertEqual(block["universe"]["tools_corpus_db"], {"kind": "full", "sha256": db_sha})
        self.assertEqual(block["arm"], "assisted")
        self.assertNotIn("campaign", block)                # the campaign plan stamps it
        self.assertEqual(block["skipped"], {F.W3: "not rendered"})
        self.assertEqual(doc["tasks"]["count"], 2)
        self.assertEqual(doc["repos"], [])
        self.assertIn("worker.md", doc["prompts"])
        st = read_json(self.paths.sourceset_state_json(F.SS))
        self.assertEqual(st["runs"]["inv-1"]["tasks"], 2)
        self.assertEqual(st["runs"]["inv-1"]["arm"], "assisted")

    def test_run_yaml_pins_the_skill_commit_and_every_staged_file(self):
        """Seam #62 (D6): what ran is pinned by the planner, not guessed by the scorer."""
        block = read_yaml(self.paths.run_yaml("inv-1"))["locate"]
        self.assertEqual(block["skill_commit"], export_mod.skill_commit())
        root = self.paths.tend_root("inv-1")
        staged = sorted(p.relative_to(root).as_posix() for top in ("gate", "bin")
                        for p in (root / top).rglob("*")
                        if p.is_file() and "/tasks/" not in p.relative_to(root).as_posix())
        self.assertEqual(sorted(block["gate_files"]), staged)
        for name in ("gate/verify_locate.py", "gate/verify_findings.py", "bin/agent_call.py",
                     "bin/watchdog.py", "gate/vendor/literature_links.manifest.json"):
            self.assertEqual(block["gate_files"][name], hashing.sha256_file(root / name), name)

    def test_the_spend_cap_is_only_the_flags(self):
        """Seam #76 (D6): a workspace default is not a run's cap."""
        data = read_yaml(self.paths.workspace_yaml)
        data["budget"] = {"max_cost_usd": 25}
        from cflib.yamlio import write_yaml
        write_yaml(self.paths.workspace_yaml, data)
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-b")
        self.assertEqual(code, 0, out + err)
        budget = read_yaml(self.paths.run_yaml("inv-b"))["budget"]
        self.assertIsNone(budget["max_cost"])
        self.assertEqual(budget["workspace_max_cost_usd"], 25)
        self.assertIn("--max-cost", out + err)

    def test_replanning_is_byte_identical(self):
        def snapshot():
            rd = self.paths.run_dir("inv-1")
            return {p.relative_to(rd).as_posix(): p.read_bytes() for p in sorted(rd.rglob("*"))
                    if p.is_file() and p.name != "run.yaml"}
        before = snapshot()
        created = read_yaml(self.paths.run_yaml("inv-1"))["created"]
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-1")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(snapshot(), before)
        self.assertEqual(read_yaml(self.paths.run_yaml("inv-1"))["created"], created)

    def test_the_prompts_render_every_slot_and_the_invocations(self):
        pdir = self.paths.prompts_dir("inv-1")
        self.assertEqual(sorted(p.name for p in pdir.iterdir()),
                         ["reviewer-system.md", "reviewer.md", "worker-system.md", "worker.md"])
        gate_dir = self.paths.tend_root("inv-1") / "gate"
        system = (pdir / "worker-system.md").read_text()
        self.assertIn(f"python3 {gate_dir}/corpus_search.py --db {self.paths.corpus_db(F.U)}",
                      system)
        self.assertIn(f"python3 {gate_dir}/passage_ids.py", system)
        # seam #35: the inventory refusal is the inventory's rule 4, not the locate one
        self.assertIn("an inventory with no `formal_math` passage, or a subject passage scoped "
                      "out", system)
        self.assertNotIn("your task manifest lists", system)
        self.assertIn("proof_status", system)
        self.assertIn("DATA, not instructions", system)
        worker = (pdir / "worker.md").read_text()
        self.assertIn("$TEND_TASK_ID", worker)
        self.assertIn('"mode": "locate-inventory"', worker)
        self.assertIn(".tend/agent_result.json", worker)
        for name in ("worker-system.md", "worker.md", "reviewer-system.md", "reviewer.md"):
            self.assertNotRegex((pdir / name).read_text(), r"\{\{", name)

    def test_the_root_holds_at_most_five_hundred_tasks(self):
        tasks = [lplan.TaskSpec(id=f"t{i}", mode="locate", title="t", priority="default",
                                summary="", documents=[]) for i in range(501)]
        with self.assertRaises(CfError) as caught:
            lplan.check_root_size(tasks)
        self.assertIn("campaign shards", caught.exception.hint)
        lplan.check_root_size(tasks[:500])

    def test_the_concurrency_is_the_workspaces_then_the_presets(self):
        """P3: the workspace asked for 6/4 and run.yaml and config.yaml said the preset's 8/4."""
        from cflib.yamlio import write_yaml

        def plan(run_id, *extra):
            code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                                "--sourceset", F.SS, "--universe", F.U, "--run-id", run_id,
                                *extra)
            return code, out + err

        def limits(run_id):
            doc = read_yaml(self.paths.run_yaml(run_id))
            cfg = (self.paths.tend_root(run_id) / "config.yaml").read_text()
            got = re.findall(r"^max_concurrent_(worker|reviewer)_agents: (\d+)$", cfg, re.M)
            return doc["concurrency"], dict(got)

        data = read_yaml(self.paths.workspace_yaml)
        data.setdefault("defaults", {}).update(max_concurrent_workers=6,
                                               max_concurrent_reviewers=4)
        write_yaml(self.paths.workspace_yaml, data)
        code, out = plan("inv-c")
        self.assertEqual(code, 0, out)
        block, cfg = limits("inv-c")
        self.assertEqual((block["workers"], block["reviewers"]), (6, 4))
        self.assertEqual(block["source"]["workers"], "workspace.defaults.max_concurrent_workers")
        self.assertEqual(cfg, {"worker": "6", "reviewer": "4"})
        self.assertIn("6 workers", (self.paths.tend_root("inv-c") / "README.md").read_text())
        # a flag wins
        code, out = plan("inv-f", "--workers", "2", "--reviewers", "1")
        self.assertEqual(code, 0, out)
        block, cfg = limits("inv-f")
        self.assertEqual((block["workers"], block["reviewers"], block["source"]["workers"]),
                         (2, 1, "--workers"))
        self.assertEqual(cfg, {"worker": "2", "reviewer": "1"})
        # and the preset only when the workspace says nothing
        for key in ("max_concurrent_workers", "max_concurrent_reviewers"):
            data["defaults"].pop(key)
        write_yaml(self.paths.workspace_yaml, data)
        code, out = plan("inv-p")
        self.assertEqual(code, 0, out)
        preset = lp.get("locate-inventory")
        block, cfg = limits("inv-p")
        self.assertEqual((block["workers"], block["reviewers"]),
                         (preset.max_workers, preset.max_reviewers))
        self.assertEqual(block["source"]["reviewers"], "preset.locate-inventory")
        # a limit that is not a whole number is refused; one over the cap is said
        data["defaults"]["max_concurrent_workers"] = "lots"
        write_yaml(self.paths.workspace_yaml, data)
        code, out = plan("inv-x")
        self.assertNotEqual(code, 0)
        self.assertIn("max_concurrent_workers", out)
        data["defaults"]["max_concurrent_workers"] = 12
        data["defaults"]["max_concurrent_sessions"] = 16
        write_yaml(self.paths.workspace_yaml, data)
        code, out = plan("inv-o", "--reviewers", "6")
        self.assertEqual(code, 0, out)
        self.assertIn("over the account cap of 16", out)

    def test_a_remote_host_gets_host_paths_and_the_local_gate_keeps_local_ones(self):
        data = read_yaml(self.paths.workspace_yaml)
        data["hosts"] = {"allowlist": ["examplebox"], "examplebox": {"remote_root": "/srv/cf"}}
        from cflib.yamlio import write_yaml
        write_yaml(self.paths.workspace_yaml, data)
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-r",
                            "--host", "examplebox", "--max-cost", "50")
        self.assertEqual(code, 0, out + err)
        remote = self.gate("inv-r")
        local = self.gate("inv-r", local=True)
        self.assertEqual(remote["corpus_db"]["path"], f"/srv/cf/corpora/{F.U}/corpus.sqlite")
        self.assertEqual(remote["tools_corpus_db"]["path"], remote["corpus_db"]["path"])
        self.assertEqual(remote["sourceset"]["sources_dir"], f"/srv/cf/sourcesets/{F.SS}/sources")
        self.assertEqual(remote["tasks_dir"], "/srv/cf/inv-r-gate/tasks")
        self.assertEqual(local["corpus_db"]["path"], str(self.paths.corpus_db(F.U)))
        doc = read_yaml(self.paths.run_yaml("inv-r"))
        self.assertEqual(doc["budget"]["max_cost"], 50.0)
        self.assertFalse(doc["consent"]["given"])


class TestTheStagedGateRunsAlone(Campaign):
    """P2: the gate directory plan-locate stages is the whole of what runs on the host.

    The field case: ``runs/<run>/tend-root/gate/`` had no ``verify_findings.py``,
    which the gate imports, so every call died with ``ModuleNotFoundError`` and
    Python's exit 1 -- the code the worker reads as "your document is wrong".
    The tests that ran the gate ran it from ``scripts/gate/``, where the missing
    module sat beside it.  Here the staged directory is copied to a fresh place
    outside this checkout (a stand-in for ``<remote_root>/<run>-gate``) and run
    with ``python3 -I``, a bare environment and a scratch working directory.
    """

    def setUp(self):
        super().setUp()
        self.staged = self.paths.tend_root("inv-1") / "gate"
        away = Path(tempfile.mkdtemp(prefix="cf-host-")).resolve()
        self.addCleanup(shutil.rmtree, away, ignore_errors=True)
        self.assertFalse(str(away).startswith(str(SKILL)))
        self.host = away / "inv-1-gate"
        shutil.copytree(self.staged, self.host)
        self.cwd = away / "worktree"
        docs = F.inventory_documents(self.paths)
        first = sorted(docs)[0]
        self.doc_rel = "findings/" + first          # a worktree's layout
        self.doc = docs[first]
        self.write(self.doc)

    def write(self, doc: dict) -> Path:
        path = self.cwd / self.doc_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def isolated(self, script: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", str(self.host / script), *argv],
                              capture_output=True, text=True, cwd=str(self.cwd),
                              env={"PATH": "/usr/bin:/bin", "HOME": str(self.cwd)}, timeout=120)

    def gate(self, *argv: str) -> subprocess.CompletedProcess:
        return self.isolated("verify_locate.py", "--gate-config", str(self.host / "gate.json"),
                             *argv)

    def test_every_import_resolves_inside_the_directory(self):
        import ast

        from gate import verify_locate as vl

        local = {p.stem for p in self.host.glob("*.py")}
        self.assertLessEqual({m[:-3] for m in vl.GATE_MODULES}, local)
        vendored = {p.name for p in (self.host / "vendor").iterdir() if p.is_dir()}
        self.assertEqual(vendored, {"literature_links", "formal_companion_annotations"})
        for path in sorted(self.host.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    with self.subTest(file=path.name, module=name):
                        self.assertTrue(top in sys.stdlib_module_names or top in local
                                        or top in vendored, f"{path.name} imports {name}")
                        self.assertNotEqual(top, "cflib")

    def test_the_gate_runs_with_python_dash_I_and_nothing_else(self):
        proc = self.gate("--self-check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK: gate self-check", proc.stdout)
        self.assertIn(str(self.host / "vendor"), proc.stdout)      # its own copy, not ours
        proc = self.gate("--findings", self.doc_rel)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK: 1 locate document(s) validated", proc.stdout)
        bad = json.loads(json.dumps(self.doc))
        bad["passages"][0]["quote"]["exact"] = "a sentence this layer never held"
        self.write(bad)
        proc = self.gate("--findings", self.doc_rel)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("VALIDATION FAILED", proc.stderr)

    def test_a_directory_missing_a_module_is_broken_not_a_verdict(self):
        for module in ("verify_findings.py", "locate_text.py"):
            with self.subTest(module=module):
                shutil.copy2(self.staged / module, self.host / module)
                (self.host / module).unlink()
                for argv in (("--findings", self.doc_rel), ("--self-check",)):
                    proc = self.gate(*argv)
                    self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
                    self.assertIn("the gate could not run", proc.stderr)
                    self.assertNotIn("Traceback", proc.stderr)
                    self.assertNotIn("VALIDATION FAILED", proc.stderr)
                    self.assertEqual(proc.stdout.splitlines()[-1],
                                     "ownership: not checked (the gate could not run)")
                shutil.copy2(self.staged / module, self.host / module)

    def test_a_crash_inside_the_gate_is_exit_3(self):
        cfg = json.loads((self.host / "gate.json").read_text(encoding="utf-8"))
        cfg["max_locations"] = "five"           # a malformed gate.json value: named, exit 3
        (self.host / "gate.json").write_text(json.dumps(cfg), encoding="utf-8")
        proc = self.gate("--findings", self.doc_rel)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("the gate could not run", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        shutil.copy2(self.staged / "gate.json", self.host / "gate.json")
        # an exception nothing foresaw (a sibling that raises mid-validation) is exit 3 too
        with (self.host / "locate_text.py").open("a", encoding="utf-8") as fh:
            fh.write("\n\nclass HeadMap:  # sabotaged by the test\n"
                     "    def __init__(self, *args, **kwargs):\n"
                     "        raise RuntimeError('a crash nothing foresaw')\n")
        proc = self.gate("--findings", self.doc_rel)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("the gate itself failed", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("VALIDATION FAILED", proc.stderr)

    def test_the_prompts_read_the_exit_codes_the_gate_writes(self):
        """The worker-prompt half of P2: exit 1 is a verdict, exit 3 is blocked, in every prompt."""
        from gate import verify_locate as vl
        from locate import prompts as lprompts

        self.assertEqual((lprompts.GATE_EXIT_REJECTED, lprompts.GATE_EXIT_BROKEN),
                         (vl.EXIT_REJECTED, vl.EXIT_GATE_BROKEN))
        bad = json.loads(json.dumps(self.doc))
        bad["passages"][0]["quote"]["exact"] = "a sentence this layer never held"
        self.write(bad)
        rejected = self.gate("--findings", self.doc_rel)
        self.assertEqual(rejected.returncode, lprompts.GATE_EXIT_REJECTED)
        self.assertIn(lprompts.GATE_REJECTED_MARK, rejected.stderr)
        (self.host / "verify_findings.py").unlink()
        broken = self.gate("--findings", self.doc_rel)
        self.assertEqual(broken.returncode, lprompts.GATE_EXIT_BROKEN)
        self.assertIn(lprompts.GATE_BROKEN_MARK, broken.stderr)
        self.assertNotIn(lprompts.GATE_REJECTED_MARK, broken.stderr)
        self.locate()
        self.challenge()
        for run_id, mode in (("inv-1", "locate-inventory"), ("loc-1", "locate"),
                             ("chl-1", "locate-challenge")):
            pdir = self.paths.prompts_dir(run_id)
            for name, role in (("worker-system.md", "worker"), ("worker.md", "worker"),
                               ("reviewer-system.md", "reviewer")):
                with self.subTest(mode=mode, prompt=name):
                    text = (pdir / name).read_text(encoding="utf-8")
                    self.assertIn(lprompts.gate_exits(role), text)
                    self.assertNotIn("Any other non-zero exit", text)
        worker = lprompts.gate_exits("worker")
        self.assertIn(f"**exit {vl.EXIT_GATE_BROKEN}**", worker)
        self.assertIn("`blocked`", worker)
        self.assertIn("not grounds for rejecting the worker", lprompts.gate_exits("reviewer"))

    def test_the_self_check_names_what_is_missing(self):
        shutil.rmtree(self.host / "vendor" / "literature_links")
        proc = self.gate("--self-check")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("literature_links", proc.stderr)

    def test_the_workers_tools_run_from_it_too(self):
        for script in ("passage_ids.py", "corpus_search.py", "corpus_show.py"):
            with self.subTest(script=script):
                proc = self.isolated(script, "--help")
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = subprocess.run([sys.executable, "-I", "-c",
                               "import sys; sys.path.insert(0, sys.argv[1]); import locate_records",
                               str(self.host)], capture_output=True, text=True,
                              cwd=str(self.cwd), env={"PATH": "/usr/bin:/bin"}, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_checkout_without_a_gate_file_refuses_to_plan(self):
        from locate import tend_root as ltr

        absent = "/".join(("scripts", "gate", "nope.py"))          # not a citation
        with unittest.mock.patch.object(ltr, "GATE_FILES",
                                        ltr.GATE_FILES + ((absent, "nope.py"),)):
            with self.assertRaises(CfError) as caught:
                ltr.stage(layout_host={}, layout_local={}, preset=lp.get("locate-inventory"),
                          run_id="x", gate_config={}, prompts={}, tasks=[], model=None,
                          agent=None, allow_sandbox_bypass=False, context={})
        self.assertIn("nope.py", str(caught.exception))

    def test_cf_runs_verify_stage_runs_the_self_check_isolated(self):
        from run import remote

        host = {"name": "examplebox", "ssh": "examplebox", "remote_root": "/srv/cf",
                "tend_cmd": "uv run"}
        names = {"entrypoint": "inv-1", "tend_root": "inv-1-tend", "gate": "inv-1-gate"}
        steps = remote.plan_commands(host=host, run_id="inv-1", run={}, entries=[], names=names,
                                     budget={}, concurrency={"workers": 1, "reviewers": 1},
                                     locate={})
        verify = [s["argv"][-1] for s in steps if s["stage"] == "verify"]
        self.assertIn("python3 -I /srv/cf/inv-1-gate/verify_locate.py --gate-config "
                      "/srv/cf/inv-1-gate/gate.json --self-check", verify)
        self.assertFalse([c for c in verify if c.startswith("test -f")])


class TestTheStagedRootDrivesTheBridge(Campaign):
    """The planned root's own wrappers, run through its own bin/, with a fake agentbox.

    P1: the locate reviewer's prompt renders the gate command and makes "the
    gate passes" a check, but the bridge allowlisted only Read, Glob, Grep and
    git for a reviewer, so the agent CLI would deny the call.  And the smoke
    plan found ``agent_call.py``, ``attempt_cap.py`` and ``watchdog.py`` absent
    from the staged bin/ (only cf run's transfer fallback shipped them), so a
    root used as planned had a bridge that exits 78 before any call.
    """

    SUCCESS = {"type": "result", "subtype": "success", "is_error": False,
               "result": "done", "total_cost_usd": 0.1}

    def setUp(self):
        super().setUp()
        self.troot = self.paths.tend_root("inv-1")
        self.gate_dir = self.troot / "gate"
        tmp = Path(tempfile.mkdtemp(prefix="cf-bridge-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.tmp = tmp
        fakebin = tmp / "fakebin"
        fakebin.mkdir()
        self.argv_file = tmp / "argv"
        agentbox = fakebin / "agentbox"
        agentbox.write_text("#!/bin/sh\n"
                            f'for a in "$@"; do printf \'%s\\n\' "$a"; done > "{self.argv_file}"\n'
                            f"printf '%s' '{json.dumps(self.SUCCESS)}'\n", encoding="utf-8")
        agentbox.chmod(0o755)
        self.worktree = tmp / "worktree"
        self.worktree.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CF_", "TEND_", "GIT_CONFIG_"))}
        self.env.update(PATH=f"{fakebin}:{self.env['PATH']}", USER="examplebox",
                        CF_SLOT_DIR=str(tmp / "slots"), TEND_TASK_ID=f"inv-{F.W1}")

    def launch(self, role: str) -> list[str]:
        if self.argv_file.exists():
            self.argv_file.unlink()
        proc = subprocess.run(["bash", str(self.troot / "bin" / f"{role}-agent.sh")],
                              capture_output=True, text=True, cwd=str(self.worktree),
                              env=self.env, timeout=120)
        self.assertTrue(self.argv_file.exists(),
                        f"the staged {role} bridge never reached the agent: {proc.stderr}")
        return self.argv_file.read_text(encoding="utf-8").splitlines()

    def tools(self, role: str) -> list[str]:
        argv = self.launch(role)
        return argv[argv.index("--allowedTools") + 1].split(",")

    def test_every_bridge_helper_is_staged_beside_the_bridge(self):
        from plan.make_tend_root import BRIDGE_HELPERS
        from run import manifest

        self.assertEqual(set(BRIDGE_HELPERS),
                         {n for n in manifest.BRIDGE_FILES if n.endswith(".py")})
        for helper in BRIDGE_HELPERS:
            with self.subTest(helper=helper):
                staged = self.troot / "bin" / helper
                self.assertEqual(staged.read_bytes(),
                                 (SCRIPTS / "run" / helper).read_bytes())
        def transfer():
            entries, _ = manifest.build_manifest(
                workspace_dir=self.root, skill_dir=SKILL, run_id="inv-1",
                run=read_yaml(self.paths.run_yaml("inv-1")),
                host={"name": "examplebox", "ssh": "examplebox", "remote_root": "/srv/cf"})
            return {e["name"]: e for e in entries}

        self.assertNotIn("bridge-helpers", transfer())
        # a root planned before a helper existed still gets that helper, and only it
        (self.troot / "bin" / "attempt_cap.py").unlink()
        fallback = transfer()["bridge-helpers"]
        self.assertEqual([Path(s).name for s in fallback["srcs"]], ["attempt_cap.py"])

    def test_the_reviewer_may_run_the_gate_its_prompt_renders(self):
        program = f"python3 {self.gate_dir}/verify_locate.py"
        # the --findings check, never tend's --changed-files call (the label hardening)
        check = f"{program} --gate-config {self.gate_dir}/gate.json --findings"
        entry = f"Bash({check}:*)"
        reviewer = self.tools("reviewer")
        self.assertIn(entry, reviewer)
        self.assertNotIn(f"Bash({program}:*)", reviewer)
        self.assertEqual(reviewer[:4], ["Read", "Glob", "Grep", "Bash(git:*)"])
        self.assertFalse([t for t in reviewer if t in ("Edit", "Write", "Bash(python3:*)")])
        self.assertFalse([t for t in reviewer if "corpus_" in t or "passage_ids" in t])
        # every gate invocation either reviewer prompt renders is under that entry
        pdir = self.paths.prompts_dir("inv-1")
        for name in ("reviewer.md", "reviewer-system.md"):
            text = (pdir / name).read_text(encoding="utf-8")
            for line in text.splitlines():
                if "verify_locate.py" in line and line.lstrip().startswith("python3"):
                    self.assertTrue(line.strip().startswith(check + " "), (name, line))
        self.assertIn(check + " ", (pdir / "reviewer.md").read_text())

    def test_the_worker_keeps_the_gate_and_its_tools(self):
        worker = self.tools("worker")
        program = f"python3 {self.gate_dir}/verify_locate.py"
        check = f"{program} --gate-config {self.gate_dir}/gate.json --findings"
        self.assertIn(f"Bash({check}:*)", worker)
        self.assertNotIn(f"Bash({program}:*)", worker)
        # every gate invocation the worker prompts render is under that entry
        pdir = self.paths.prompts_dir("inv-1")
        for name in ("worker.md", "worker-system.md"):
            for line in (pdir / name).read_text(encoding="utf-8").splitlines():
                if "verify_locate.py" in line and line.lstrip().startswith("python3"):
                    self.assertTrue(line.strip().startswith(check + " "), (name, line))
        self.assertIn(f"Bash(python3 {self.gate_dir}/passage_ids.py:*)", worker)
        db = self.paths.corpus_db(F.U)
        self.assertIn(f"Bash(python3 {self.gate_dir}/corpus_search.py --db {db}:*)", worker)


class TestTheStagedGateAcceptsWhatThePromptAsksFor(Campaign):
    """Seam #8: an inventory document written the way the prompt says, its ids filled
    by the staged passage-id tool, passes the staged gate -- every input made by its
    owner, every script run from the tend root's gate directory."""

    def test_passage_ids_then_verify_locate(self):
        gate_dir = self.paths.tend_root("inv-1") / "gate"
        worktree = self.root.parent / "worktree"
        task = f"inv-{F.W1}"
        doc = F.inventory_documents(self.paths)[f"{task}/{F.W1}.json"]
        for passage in doc["passages"]:
            passage.update(item="", head_words=0, span=None)
        path = worktree / "findings" / task / f"{F.W1}.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
        source = self.paths.sourceset_source_dir(F.SS, F.W1)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.run([sys.executable, "-I", str(gate_dir / "passage_ids.py"),
                               "--source-dir", str(source), "--document", str(path), "--write"],
                              cwd=worktree, capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        filled = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(all(p["item"].startswith("In-text #") for p in filled["passages"]))
        proc = subprocess.run([sys.executable, "-I", str(gate_dir / "verify_locate.py"),
                               "--gate-config", str(gate_dir / "gate.json"), "--root",
                               str(worktree), "--findings", f"findings/{task}/{F.W1}.json",
                               "--task-id", task],
                              cwd=worktree, capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK: 1 locate document(s)", proc.stdout)


class TestTheToolsDatabase(Campaign):
    """D2: the workers search the TOOLS database; the gate checks against the full one."""

    def plan(self, *extra, run_id="inv-t"):
        return ws(self.root, "plan-locate", "--preset", "locate-inventory", "--sourceset",
                  F.SS, "--universe", F.U, "--run-id", run_id, *extra)

    def candidates(self, *extra):
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, *extra)
        self.assertEqual(code, 0, out + err)

    def test_the_blind_arm_searches_the_blind_database(self):
        code, out, err = self.plan("--blind")
        self.assertNotEqual(code, 0)                         # the candidates are the full arm's
        self.assertIn("blind", out + err)
        self.candidates("--blind")
        code, out, err = self.plan("--blind")
        self.assertEqual(code, 0, out + err)
        gate = read_json(self.paths.gate_json("inv-t"))
        blind = self.paths.corpus_db_blind(F.U)
        self.assertEqual(gate["tools_corpus_db"], {"path": str(blind), "kind": "blind",
                                                   "sha256": hashing.sha256_file(blind)})
        self.assertEqual(gate["corpus_db"]["path"], str(self.paths.corpus_db(F.U)))
        self.assertTrue(gate["blind"])
        gate_dir = self.paths.tend_root("inv-t") / "gate"
        worker = (self.paths.tend_root("inv-t") / "bin" / "worker-agent.sh").read_text()
        self.assertIn(f"Bash(python3 {gate_dir}/corpus_search.py --db {blind}:*)", worker)
        system = (self.paths.prompts_dir("inv-t") / "worker-system.md").read_text()
        self.assertIn(f"corpus_search.py --db {blind}", system)
        block = read_yaml(self.paths.run_yaml("inv-t"))["locate"]
        self.assertEqual((block["arm"], block["universe"]["tools_corpus_db"]["kind"]),
                         ("blind", "blind"))

    def test_the_blind_arm_refuses_a_missing_blind_database(self):
        self.paths.corpus_db_blind(F.U).unlink()
        code, out, err = self.plan("--blind")
        self.assertNotEqual(code, 0)
        self.assertIn(f"cf corpus build-db {F.U} --blind", out + err)

    def test_a_planted_database_for_the_p_tier(self):
        """Seam #15, #78: the planted file is the tools database only, by content address."""
        planted = self.root.parent / "planted.sqlite"
        shutil.copy2(self.paths.corpus_db(F.U), planted)
        code, out, err = self.plan("--tools-corpus-db", str(planted))
        self.assertNotEqual(code, 0)                        # the candidates came from the full one
        self.assertIn("planted", out + err)
        self.candidates("--tools-corpus-db", str(planted))
        data = read_yaml(self.paths.workspace_yaml)
        data["hosts"] = {"allowlist": ["examplebox"], "examplebox": {"remote_root": "/srv/cf"}}
        from cflib.yamlio import write_yaml
        write_yaml(self.paths.workspace_yaml, data)
        code, out, err = self.plan("--tools-corpus-db", str(planted), "--host", "examplebox",
                                   "--max-cost", "5")
        self.assertEqual(code, 0, out + err)
        sha = hashing.sha256_file(planted)
        remote = read_json(self.paths.gate_json("inv-t"))
        local = read_json(self.paths.run_dir("inv-t") / "gate.local.json")
        self.assertEqual(remote["tools_corpus_db"],
                         {"path": f"/srv/cf/corpora/{F.U}/planted-{sha[:16]}.sqlite",
                          "sha256": sha, "kind": "planted"})
        self.assertEqual(local["tools_corpus_db"]["path"], str(planted.resolve()))
        self.assertEqual(remote["corpus_db"]["path"], f"/srv/cf/corpora/{F.U}/corpus.sqlite")
        block = read_yaml(self.paths.run_yaml("inv-t"))["locate"]
        self.assertEqual(block["universe"]["tools_corpus_db"], {"kind": "planted", "sha256": sha})
        code, out, err = self.plan("--tools-corpus-db", str(planted), "--blind")
        self.assertNotEqual(code, 0)
        self.assertIn("two different arms", out + err)

    def test_a_stale_database_is_refused_and_a_rebuilt_one_is_the_same_run(self):
        """Seam #11, #20 (D2): identity is the universe sha, never the file's bytes."""
        from corpus import build_db
        from corpus.universe import Universe
        u = Universe.load(self.paths.universe_yaml(F.U))
        build_db.build(u, self.paths.universe_dir(F.U), self.paths.corpus_db(F.U), force=True)
        u.save()
        code, out, err = self.plan()
        self.assertEqual(code, 0, out + err)                 # rebuilt, same universe: accepted
        index = self.paths.universe_dir(F.U) / "index" / "beta" / "decls.jsonl"
        index.write_text(index.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        u = Universe.load(self.paths.universe_yaml(F.U))
        u.set_index("beta", path="index/beta/", sha256=hashing.sha256_file(index),
                    declarations=len(F.DECLS["beta"]))
        u.save()
        code, out, err = self.plan(run_id="inv-u")
        self.assertNotEqual(code, 0)
        self.assertIn("another state of universe", out + err)


class TestTheHostLayout(unittest.TestCase):

    def test_no_checkouts_and_no_inventory_directory_on_the_host(self):
        """Seam #21, #38, #77, #80: nothing creates either, so nothing names them."""
        paths = F.make_workspace(scratch(self))
        db = {"path": str(paths.corpus_db(F.U)), "sha256": "1" * 64, "kind": "full"}
        layouts = lplan.host_layout(paths, host="examplebox", run_id="r1", sourceset_id=F.SS,
                                    universe_id=F.U, tools_db=db, remote_root="/srv/cf")
        for side in ("host", "local"):
            self.assertEqual(layouts[side]["checkouts_dir"], "")
            self.assertNotIn("inventory_dir", layouts[side])
        preset = lp.get(vocab.LOCATE_CHALLENGE)
        from locate import prompts as lprompts
        F.write_universe(paths)
        ctx = lprompts.build_context(preset, run_id="r1", host=layouts["host"],
                                     universe=inputs.load_universe(paths, F.U), per_task=8)
        worker = lprompts.render_prompts(preset, ctx)["worker.md"]
        self.assertNotIn("checkouts", worker)
        self.assertIn("**overturn** it: a scope with the locations of the passage it hid, a "
                      "prominence with the right `prominence`", worker)       # seam #33
        # every subject prominence is a record of the plan (F1): the challenger is told
        # how to judge one, which is no demotion
        self.assertIn("A `subject` prominence is refuted by default", worker)
        self.assertNotIn("downgraded", worker)


class TestEveryRootStagesTheBridgeHelpers(unittest.TestCase):
    """Seam #28 (D1): a faithfulness root too -- the bridges call the helpers on every run."""

    def plan(self, root: Path) -> mtr.TendRootPlan:
        return mtr.TendRootPlan(local=root / "tend-root", tend_root="/srv/r1-tend",
                                entrypoint="/srv/r1", gate_dir="/srv/r1-gate",
                                prompts_dir="/srv/r1-tend/prompts")

    def test_a_faithfulness_root(self):
        root = scratch(self)
        mtr.make_tend_root(self.plan(root), presets_mod.get("attack-hunt"), run_id="r1",
                           gate_config={"sources": {}})
        bindir = root / "tend-root" / "bin"
        for name in mtr.BIN_HELPERS:
            self.assertTrue((bindir / name).is_file(), name)
        self.assertFalse((bindir / "watchdog.py").exists())

    def test_a_missing_helper_or_gate_file_is_an_error(self):
        root = scratch(self)
        bridges = root / "bridges"
        bridges.mkdir(parents=True)
        shutil.copy2(SCRIPTS / "run" / "bridge-agentbox.sh", bridges / "bridge-agentbox.sh")
        with self.assertRaises(CfError) as caught:
            mtr.make_tend_root(self.plan(root), presets_mod.get("attack-hunt"), run_id="r1",
                               gate_config={"sources": {}}, bridges_src=bridges)
        self.assertIn("bridge helper missing", str(caught.exception))
        with self.assertRaises(CfError) as caught:
            mtr.make_tend_root(self.plan(root), presets_mod.get("attack-hunt"), run_id="r1",
                               gate_config={"sources": {}}, gate_src=root / "nowhere")
        self.assertIn("gate file missing", str(caught.exception))


class TestFieldS5TheInventoryPlansConceptArticles(unittest.TestCase):
    """S5: plan-locate read no flag, so every redirect stub, disambiguation page, list and
    biography of a rendered sourceset became a paid locate task.  Here W2's entry
    recorded no flags (as every entry of a sourceset enumerated before the flags
    existed does) and its assessment class says Redirect."""

    def setUp(self):
        self.root = scratch(self)
        self.paths = F.make_workspace(self.root)
        with mock.patch.dict(F.ARTICLES[F.W2], {"assessment": {"class": "Redirect",
                                                               "importance": "NA"}}):
            F.write_sourceset(self.paths)
        F.write_universe(self.paths)
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U)
        self.assertEqual(code, 0, out + err)
        self.assertIsNone(F.manifest_entries(self.paths)[F.W2].get("flags"))

    def plan(self, run_id: str, *extra: str) -> tuple[dict, str]:
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", run_id, *extra)
        self.assertEqual(code, 0, out + err)
        return read_yaml(self.paths.run_yaml(run_id)), out + err

    def test_a_page_its_class_flags_is_left_out_and_named(self):
        doc, out = self.plan("inv-1")
        self.assertEqual(doc["locate"]["source_ids"], [F.W1])
        self.assertEqual(doc["locate"]["skipped"][F.W2], "no concept article (redirect)")
        self.assertIn("--include-flagged plans them", out)
        # nobody asked Wikidata about W1: a biography would pass as a concept article
        self.assertTrue(any("no biography flag" in w and "flag-biographies wpm --execute" in w
                            for w in doc["warnings"]), doc["warnings"])

    def test_asked_for_or_named_it_is_planned(self):
        doc, _out = self.plan("inv-2", "--include-flagged")
        self.assertEqual(doc["locate"]["source_ids"], [F.W1, F.W2])
        doc, _out = self.plan("inv-3", "--source", F.W2)
        self.assertEqual(doc["locate"]["source_ids"], [F.W2])
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate", "--sourceset", F.SS,
                            "--universe", F.U, "--inventory-run", "inv-2", "--include-flagged")
        self.assertEqual(code, 2, out + err)
        self.assertIn("belongs to the locate-inventory preset", out + err)


class TestPlanLocate(Campaign):

    def test_tasks_carry_the_frozen_formal_math_passages(self):
        self.locate()
        manifests = {p.stem: read_json(p) for p in
                     (self.paths.tend_root("loc-1") / "gate" / "tasks").glob("*.json")}
        self.assertEqual(sorted(manifests), [f"loc-{F.W1}-1", f"loc-{F.W2}-1"])
        (w1_doc,) = manifests[f"loc-{F.W1}-1"]["documents"]
        self.assertEqual(set(w1_doc), {"source_id", "source_sha", "layer_sha256", "renderer",
                                       "passages", "curated"})
        w1 = w1_doc["passages"]
        self.assertEqual(len(w1), 3)                     # the expository paragraph is out
        self.assertEqual([p["span"][0] for p in w1], sorted(p["span"][0] for p in w1))
        self.assertEqual(set(w1[0]), {"item", "span", "head_words", "prominence", "passage_kind",
                                      "text_span", "section", "quote"})
        gate = read_json(self.paths.gate_json("loc-1"))
        self.assertEqual(gate["inventory"], {"run_id": "inv-1"})      # seam #80: no dir
        self.assertEqual(gate["kinds"], ["ABSENCE", "LOCATION", "UNRESOLVED"])
        self.assertNotIn("inventory", read_yaml(self.paths.run_yaml("loc-1"))["remote"])

    def curated(self, sid):
        return [{"library": h["library"], "declaration": h["declaration"]}
                for h in read_json(self.paths.candidates_json(F.SS, F.U, sid))["curated"]]

    def test_a_sources_curated_hits_go_to_exactly_one_of_its_tasks(self):
        """Seam #29 (D7): the task holding the subject passage, every other task []."""
        F.plan_locate(self, self.root, "--per-task", "2", run_id="loc-2")
        docs = {p.stem: read_json(p)["documents"][0] for p in
                (self.paths.tend_root("loc-2") / "gate" / "tasks").glob("*.json")}
        self.assertTrue(self.curated(F.W1))
        self.assertEqual(docs[f"loc-{F.W1}-1"]["curated"], self.curated(F.W1))
        self.assertEqual(docs[f"loc-{F.W1}-2"]["curated"], [])
        self.assertTrue(any(p["prominence"] == "subject"
                            for p in docs[f"loc-{F.W1}-1"]["passages"]))
        self.assertEqual(docs[f"loc-{F.W2}-1"]["curated"], self.curated(F.W2))
        worker = (self.paths.prompts_dir("loc-2") / "worker.md").read_text()
        self.assertIn("Every curated candidate your manifest lists", worker)
        self.assertIn("whose relation is not `relatedMatch`", worker)                 # seam #34
        self.assertIn("its receipt still lists each curated candidate it concerns", worker)

    def test_the_curated_hits_go_to_the_subjects_chunk_else_the_first(self):
        subject, other = {"prominence": "subject"}, {"prominence": "central"}
        self.assertEqual(lplan._curated_chunk([[other], [other, subject]]), 1)
        self.assertEqual(lplan._curated_chunk([[other], [other]]), 0)

    def test_the_subject_only_tier_carries_each_sources_curated_hits(self):
        F.plan_locate(self, self.root, "--subject-only", run_id="loc-s")
        (manifest,) = [read_json(p) for p in
                       (self.paths.tend_root("loc-s") / "gate" / "tasks").glob("*.json")]
        self.assertEqual({d["source_id"]: d["curated"] for d in manifest["documents"]},
                         {F.W1: self.curated(F.W1), F.W2: self.curated(F.W2)})

    def test_a_plan_of_another_arm_than_its_inventory_is_refused(self):
        """Seam #63 (D6): a blind locate run over an assisted inventory is half one arm."""
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--blind")
        self.assertEqual(code, 0, out + err)
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate", "--sourceset", F.SS,
                            "--universe", F.U, "--inventory-run", "inv-1", "--blind")
        self.assertNotEqual(code, 0)
        self.assertIn("assisted arm and this plan is the blind arm", out + err)

    def test_a_missing_candidates_file_is_refused(self):
        """Seam #32: in a locate plan too."""
        self.paths.candidates_json(F.SS, F.U, F.W1).unlink()
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate", "--sourceset", F.SS,
                            "--universe", F.U, "--inventory-run", "inv-1", "--run-id", "loc-x")
        self.assertNotEqual(code, 0)
        self.assertIn(F.W1, out + err)

    def test_per_task_bounds_the_passages(self):
        self.locate("--per-task", "2", run_id="loc-2")
        ids = sorted(p.stem for p in (self.paths.tend_root("loc-2") / "gate" / "tasks").glob("*"))
        self.assertEqual(ids, [f"loc-{F.W1}-1", f"loc-{F.W1}-2", f"loc-{F.W2}-1"])
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate", "--sourceset", F.SS,
                            "--universe", F.U, "--inventory-run", "inv-1", "--per-task", "9")
        self.assertNotEqual(code, 0)
        self.assertIn("--per-task", out + err)

    def test_the_subject_only_tier_bundles_one_passage_per_article(self):
        F.plan_locate(self, self.root, "--subject-only", run_id="loc-s")
        (manifest,) = [read_json(p) for p in
                       (self.paths.tend_root("loc-s") / "gate" / "tasks").glob("*.json")]
        self.assertEqual(manifest["task_id"], "loc-subject-0001")
        self.assertEqual([len(d["passages"]) for d in manifest["documents"]], [1, 1])
        self.assertTrue(all(d["passages"][0]["prominence"] == "subject"
                            for d in manifest["documents"]))

    def test_an_ungated_inventory_is_not_frozen(self):
        F.write_harvest(self.paths, "inv-1", F.inventory_documents(self.paths), gate_ok=None)
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate", "--sourceset", F.SS,
                            "--universe", F.U, "--inventory-run", "inv-1")
        self.assertNotEqual(code, 0)
        self.assertIn("not gated", out + err)

    def test_mode_flags_are_checked(self):
        for argv, needle in (((("--preset", "locate")), "--inventory-run"),
                             (("--preset", "locate-challenge"), "--challenge-of"),
                             (("--preset", "locate-inventory", "--inventory-run", "inv-1"),
                              "belong to the locate preset")):
            with self.subTest(argv=argv):
                code, out, err = ws(self.root, "plan-locate", *argv, "--sourceset", F.SS,
                                    "--universe", F.U)
                self.assertNotEqual(code, 0)
                self.assertIn(needle, out + err)

    def test_the_configuration_pins_do_not_name_the_run(self):
        """Review M-01: two plans of one configuration that differ only in run id record
        equal ``locate.config_shas`` -- the prompt templates and the staged gate and bin
        files that are not the run's own -- while the rendered shas still differ."""
        allow_host(self.paths)
        runs = {"local": ("loc-a", "loc-b"), "host": ("loc-c", "loc-d")}
        for side, run_ids in runs.items():
            extra = ("--host", "examplebox", "--max-cost", "5") if side == "host" else ()
            for run_id in run_ids:
                F.plan_locate(self, self.root, *extra, run_id=run_id)
            a, b = (read_yaml(self.paths.run_yaml(r)) for r in run_ids)
            with self.subTest(side=side):
                self.assertEqual(a["locate"]["config_shas"], b["locate"]["config_shas"])
                self.assertNotEqual(a["locate"]["gate_files"], b["locate"]["gate_files"])
                self.assertNotEqual(a["prompts"], b["prompts"])
                rendered, config = a["locate"]["gate_files"], a["locate"]["config_shas"]
                self.assertEqual(set(rendered) - set(config["gate_files"]),
                                 {"gate/gate.json", "bin/worker-agent.sh", "bin/reviewer-agent.sh"})
                self.assertEqual(config["gate_files"],
                                 {k: v for k, v in rendered.items() if k in config["gate_files"]})
        tdir = SKILL / "templates" / "prompts"
        self.assertEqual(config["prompts"], {name: hashing.sha256_file(tdir / name) for name in (
            "locate-reviewer-system.md.tmpl", "locate-reviewer.md.tmpl",
            "locate-worker-system.md.tmpl", "locate.md.tmpl")})
        self.assertIn("locate-inventory.md.tmpl",
                      read_yaml(self.paths.run_yaml("inv-1"))["locate"]["config_shas"]["prompts"])
        # every template a preset renders is one the pin hashes
        from locate import prompts as lprompts
        for name in lp.LOCATE_PRESETS:
            preset = lp.get(name)
            ctx = lprompts.build_context(preset, run_id="r", host=lplan.host_layout(
                self.paths, host=lplan.LOCAL, run_id="r", sourceset_id=F.SS, universe_id=F.U,
                tools_db=inputs.tools_database(self.paths, inputs.load_universe(
                    self.paths, F.U)))["host"], universe=inputs.load_universe(self.paths, F.U),
                per_task=4)
            only = self.root.parent / f"templates-{name}"
            only.mkdir()
            for template in lprompts.template_files(preset).values():
                shutil.copy2(tdir / template, only / template)
            self.assertEqual(set(lprompts.render_prompts(preset, ctx, tdir=only)),
                             set(lprompts.PROMPT_FILES))

    def test_the_planning_keys_the_aa_check_reads_are_recorded(self):
        """``--per-task`` changes the tasks a run is cut into and no template or staged
        file carries it, so ``config_shas`` is equal across task sizes; the bench's
        ``planning`` pin reads the run.yaml keys themselves, and the planner records each."""
        from bench import score as score_mod
        F.plan_locate(self, self.root, "--per-task", "1", run_id="loc-a")
        F.plan_locate(self, self.root, "--per-task", "8", run_id="loc-b")
        a, b = (read_yaml(self.paths.run_yaml(r))["locate"] for r in ("loc-a", "loc-b"))
        self.assertEqual(a["config_shas"], b["config_shas"])
        self.assertEqual((a["per_task"], b["per_task"]), (1, 8))
        for block in (a, b, read_yaml(self.paths.run_yaml("inv-1"))["locate"]):
            self.assertLessEqual(set(score_mod.PLANNING_KEYS) | {"seed"}, set(block))

    def test_the_configuration_records_what_the_agents_run_with_seam1(self):
        """Review SEAM-1: the wrappers are left out of ``config_shas.gate_files`` (they
        name the run's paths), and they were the only record of the agent host, the model
        and the sandbox bypass; ``config_shas.agents`` records those, the attempt cap and
        the merge target as the plan decided them, equal across run ids."""
        allow_host(self.paths)
        extra = ("--host", "examplebox", "--max-cost", "5")
        F.plan_locate(self, self.root, *extra, run_id="loc-a")
        F.plan_locate(self, self.root, *extra, run_id="loc-b")
        F.plan_locate(self, self.root, *extra, "--allow-sandbox-bypass", run_id="loc-c")
        F.plan_locate(self, self.root, *extra, "--model", "claude-opus-5", run_id="loc-d")
        a, b, c, d = (read_yaml(self.paths.run_yaml(r))["locate"]["config_shas"]
                      for r in ("loc-a", "loc-b", "loc-c", "loc-d"))
        self.assertEqual(a, b)
        run = read_yaml(self.paths.run_yaml("loc-a"))
        self.assertEqual(a["agents"], {"agent_host": "claude", "model": run["model"],
                                       "allow_sandbox_bypass": False,
                                       "agentbox_profile": None,
                                       "attempt_cap": vocab.LOCATE_ATTEMPT_CAP,
                                       "merge_target": "main"})
        wrapper = (self.paths.tend_root("loc-a") / "bin" / "worker-agent.sh").read_text()
        self.assertIn(f"export CF_MODEL={run['model']}\n", wrapper)
        self.assertIn("export CF_AGENT_HOST=claude\n", wrapper)
        # the bypass and the model are configuration: only `agents` differs
        for other, key, value in ((c, "allow_sandbox_bypass", True),
                                  (d, "model", "claude-opus-5")):
            with self.subTest(key=key):
                self.assertEqual({k: v for k, v in other.items() if k != "agents"},
                                 {k: v for k, v in a.items() if k != "agents"})
                self.assertEqual(other["agents"], {**a["agents"], key: value})
        self.assertIn("export CF_ALLOW_SANDBOX_BYPASS=1\n",
                      (self.paths.tend_root("loc-c") / "bin" / "worker-agent.sh").read_text())

    def test_the_agentbox_profile_the_wrappers_export_is_configuration_too_seam1(self):
        """Review SEAM-1, its residual: ``CF_AGENTBOX_PROFILE`` (the workspace's
        ``hosts.agentbox_profile``) picks the sandbox profile the agents run under, so
        two runs under two profiles are no A/A replicates; ``config_shas.agents``
        records it, and a plan that differs from another only in it differs only there."""
        from cflib.yamlio import write_yaml
        allow_host(self.paths)
        extra = ("--host", "examplebox", "--max-cost", "5")
        F.plan_locate(self, self.root, *extra, run_id="loc-a")
        data = read_yaml(self.paths.workspace_yaml)
        data["hosts"]["agentbox_profile"] = "example-profile"
        write_yaml(self.paths.workspace_yaml, data)
        F.plan_locate(self, self.root, *extra, run_id="loc-p")
        F.plan_locate(self, self.root, *extra, run_id="loc-q")
        a, p, q = (read_yaml(self.paths.run_yaml(r))["locate"]["config_shas"]
                   for r in ("loc-a", "loc-p", "loc-q"))
        self.assertEqual(p, q)
        self.assertIsNone(a["agents"]["agentbox_profile"])
        self.assertEqual(p["agents"], {**a["agents"], "agentbox_profile": "example-profile"})
        self.assertEqual({k: v for k, v in p.items() if k != "agents"},
                         {k: v for k, v in a.items() if k != "agents"})
        self.assertIn("export CF_AGENTBOX_PROFILE=example-profile\n",
                      (self.paths.tend_root("loc-p") / "bin" / "worker-agent.sh").read_text())

    def test_a_re_plan_keeps_the_campaign_stamp(self):
        """Review OPS-05: a shard re-planned (with another cap, as `cf run`'s refusal tells
        the operator to) keeps the stamp `cf campaign plan` gave it, so the export still
        seals it under its campaign, never as ``run:<id>``."""
        allow_host(self.paths)
        F.plan_locate(self, self.root, "--host", "examplebox", "--max-cost", "20",
                      run_id="loc-s")
        code, out, err = ws(self.root, "campaign", "plan", "c1", "--shard", "loc-s",
                            "--host", "examplebox")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_yaml(self.paths.run_yaml("loc-s"))["locate"]["campaign"], "c1")
        F.plan_locate(self, self.root, "--host", "examplebox", "--max-cost", "7",
                      run_id="loc-s")
        data = read_yaml(self.paths.run_yaml("loc-s"))
        self.assertEqual((data["locate"]["campaign"], data["budget"]["max_cost"]), ("c1", 7.0))
        self.assertEqual(export_mod.campaign_token(data, "loc-s", []), "c1")
        F.plan_locate(self, self.root, "--host", "examplebox", run_id="loc-t")
        data = read_yaml(self.paths.run_yaml("loc-t"))
        self.assertNotIn("campaign", data["locate"])
        self.assertEqual(export_mod.campaign_token(data, "loc-t", []), "run:loc-t")


# --------------------------------------------------------------------------
# outcomes and the tally
# --------------------------------------------------------------------------

class TestOutcomes(Campaign):

    def setUp(self):
        super().setUp()
        self.locate()

    def outcomes(self, run="loc-1"):
        return {r["item"]: r for r in map(json.loads,
                                          self.paths.locate_outcomes_jsonl(run).read_text()
                                          .splitlines())}

    def test_rows_follow_section_10_and_hash_with_finding_id(self):
        built = outcomes.build(self.paths, "loc-1")
        kinds = sorted(r["kind"] for r in built["rows"])
        self.assertEqual(kinds, ["ABSENCE"] + ["LOCATION"] * 5 + ["UNRESOLVED"])
        loc = next(r for r in built["rows"] if r["kind"] == "LOCATION" and r["repo"] == "beta")
        self.assertEqual(loc["unfaithful_decls"], [f"beta:Beta.Widget.gadget_of_finite@{F.BETA}"])
        self.assertEqual(loc["finding_id"], hashing.finding_id(
            "loc-1", F.W1, loc["item"], "LOCATION", loc["unfaithful_decls"]))
        absence = next(r for r in built["rows"] if r["kind"] == "ABSENCE")
        self.assertEqual((absence["repo"], absence["unfaithful_decls"]), (F.U, []))
        inv = outcomes.build(self.paths, "inv-1")
        self.assertEqual(inv["counts"]["passages"], 6)
        self.assertTrue(all(r["repo"] == F.SS for r in inv["rows"]))

    def test_the_tally_writes_outcomes_with_a_derived_status(self):
        code, out, err = ws(self.root, "tally", "--locate", "--run", "loc-1")
        self.assertEqual(code, 0, out + err)
        rows = self.outcomes()
        status = {r["passage_kind"] + "/" + (r["outcome"] or "-"): r["status"]
                  for r in rows.values()}
        self.assertEqual(sorted(r["status"] or "none" for r in rows.values()),
                         sorted(["partial", "partial", "formalized", "none", "not_formalized",
                                 "none"]), status)
        lead = next(r for r in rows.values() if r["prominence"] == "subject"
                    and r["source_id"] == F.W1)
        self.assertEqual([l["proof_status"] for l in lead["locations"]], ["proved", "proved"])
        self.assertTrue(all(l["location_key"] == hashing.location_key(
            F.W1, lead["item"], l["library"], l["commit"], l["declaration"])
            for l in lead["locations"]))
        self.assertIsNone(lead["challenge"])          # owed: nothing challenged yet
        tally = read_json(self.paths.locate_tally_json("loc-1"))
        self.assertEqual(tally["counts"]["unresolved"], 1)
        self.assertEqual(tally["counts"]["scoped_out"], 1)
        self.assertEqual(tally["counts"]["by_status"]["not_formalized"], 1)
        st = read_json(self.paths.sourceset_state_json(F.SS))
        self.assertEqual(st["tallies"]["loc-1"]["passages"], 6)

    def test_an_unresolved_passage_is_never_not_formalized(self):
        ws(self.root, "tally", "--locate", "--run", "loc-1")
        unresolved = [r for r in self.outcomes().values() if r["outcome"] == "unresolved"]
        self.assertEqual([r["status"] for r in unresolved], [None])

    def test_a_planned_passage_with_no_document_is_kept_as_missing(self):
        docs = F.locate_documents(self.paths)
        docs.pop(next(k for k in docs if F.W2 in k))
        shutil.rmtree(self.paths.corpus_run_dir("loc-1"))
        F.write_harvest(self.paths, "loc-1", docs)
        built = outcomes.build(self.paths, "loc-1")
        missing = [r for r in built["outcomes"] if r["missing"]]
        self.assertEqual(len(missing), 2)
        self.assertTrue(all(r["status"] is None and r["outcome"] is None for r in missing))

    def test_a_document_in_the_wrong_place_is_named_by_the_harvested_layout(self):
        """Seam #46 (D4): harvested documents are <task-id>/<source-id>.json."""
        docs = F.locate_documents(self.paths)
        key = next(k for k in docs if F.W1 in k)
        docs[key.replace(f"{F.W1}.json", "other.json")] = docs.pop(key)
        shutil.rmtree(self.paths.corpus_run_dir("loc-1"))
        F.write_harvest(self.paths, "loc-1", docs)
        with self.assertRaises(CfError) as caught:
            outcomes.build(self.paths, "loc-1")
        self.assertIn(f"lives at <task-id>/{F.W1}.json", str(caught.exception))
        self.assertNotIn("findings/", str(caught.exception))

    def test_a_rebuilt_database_still_joins_the_run(self):
        """Seam #11: a rebuild of an unchanged universe has other bytes and is the same."""
        from corpus import build_db
        from corpus.universe import Universe
        before = hashing.sha256_file(self.paths.corpus_db(F.U))
        u = Universe.load(self.paths.universe_yaml(F.U))
        with mock.patch("cflib.state.now", return_value="2030-01-01T00:00:00Z"):
            build_db.build(u, self.paths.universe_dir(F.U), self.paths.corpus_db(F.U),
                           force=True)
        u.save()
        self.assertNotEqual(hashing.sha256_file(self.paths.corpus_db(F.U)), before)
        built = outcomes.build(self.paths, "loc-1")
        lead = next(r for r in built["outcomes"] if r["prominence"] == "subject"
                    and r["source_id"] == F.W1)
        self.assertEqual([l["proof_status"] for l in lead["locations"]], ["proved", "proved"])

    def test_a_written_proof_status_is_ignored_and_named(self):
        docs = F.locate_documents(self.paths)
        key = next(k for k in docs if F.W1 in k)
        docs[key]["passages"][0]["locations"][0]["proof_status"] = "definition"
        F.write_harvest(self.paths, "loc-1", docs)
        built = outcomes.build(self.paths, "loc-1")
        self.assertTrue(any("proof_status" in p for p in built["problems"]))
        lead = next(r for r in built["outcomes"] if r["item"] ==
                    docs[key]["passages"][0]["item"])
        self.assertEqual(lead["locations"][0]["proof_status"], "proved")


class TestChallenge(Campaign):

    def setUp(self):
        super().setUp()
        self.locate()

    def plan(self, run="chl-1"):
        return read_json(self.paths.run_dir(run) / "challenge-plan.json")

    def test_the_section_5_rules(self):
        self.challenge()
        plan = self.plan()
        by = {}
        for rec in plan["records"]:
            by.setdefault((rec["target"], rec["rule"]), []).append(rec["planned"])
        # every location on a subject or central passage is refuted by default
        self.assertEqual(by[("location", "refute_by_default")], ["required"] * 3)
        # the subject absence is hunted; the supporting unresolved outcome is not
        self.assertEqual(by[("absence", "hunt")], ["required"])
        self.assertEqual(by[("unresolved", "none")], ["not_required"])
        # weak relations, scope demotions and supporting prominences are sampled
        self.assertIn(("location", "sample"), by)
        self.assertIn(("scope", "sample"), by)
        self.assertEqual(sorted(by[("prominence", "sample")]),
                         ["required", "sampled_out", "sampled_out"])
        # every subject prominence is refuted by default: the QID rides on it (F1)
        self.assertEqual(by[("prominence", "refute_by_default")], ["required"] * 2)
        required = [r for r in plan["records"] if r["planned"] == "required"]
        self.assertTrue(all(r["task_id"] == "chl-0001" for r in required))
        manifest = read_json(self.paths.tend_root("chl-1") / "gate" / "tasks" / "chl-0001.json")
        self.assertEqual(sum(len(d["records"]) for d in manifest["documents"]), len(required))
        self.assertEqual(read_json(self.paths.gate_json("chl-1"))["challenged_runs"], ["loc-1"])

    def test_every_subject_prominence_is_challenged_whatever_the_rates(self):
        """The B5 review's F1: the article's QID rides on every record of a subject
        passage, and only supporting prominences were challenged, so a passage
        mislabelled subject kept its QID with no verdict that could take it away."""
        self.challenge("--sample-rate", "0", "--strong-sample-rate", "0",
                       "--absence-sample-rate", "0")
        plan = self.plan()
        prominence = [r for r in plan["records"] if r["target"] == "prominence"]
        subject = sorted((r["source_id"], r["rule"], r["stratum"], r["planned"])
                         for r in prominence if r["prominence"] == "subject")
        self.assertEqual(subject, [(F.W1, "refute_by_default", "prominence:subject", "required"),
                                   (F.W2, "refute_by_default", "prominence:subject", "required")])
        # the supporting ones are still the sample's, and at rate 0 none is drawn
        self.assertEqual({(r["rule"], r["stratum"], r["planned"]) for r in prominence
                          if r["prominence"] == "supporting"},
                         {("sample", "prominence:supporting", "sampled_out")})
        # each is in the task that judges its passage's locations
        tasks = {}
        for task_id, manifest_path in ((p.stem, p) for p in sorted(
                (self.paths.tend_root("chl-1") / "gate" / "tasks").glob("*.json"))):
            for d in read_json(manifest_path)["documents"]:
                for rec in d["records"]:
                    tasks.setdefault((d["source_id"], rec["item"]), set()).add(task_id)
        for rec in prominence:
            if rec["prominence"] == "subject":
                self.assertEqual(tasks[(rec["source_id"], rec["item"])], {rec["task_id"]})

    def test_a_harvested_challenge_is_not_re_planned_into_another_plan(self):
        """The B5 review: re-planning a harvested challenge in place under a planner that
        requires more (every subject prominence) left those records owed, and the export
        refused every subject passage with exit 0.  The same plan re-plans; another is
        refused before anything is written, and plans under a new run id."""
        self.challenge()
        before = (self.paths.run_dir("chl-1") / "challenge-plan.json").read_bytes()
        argv = ("plan-locate", "--preset", "locate-challenge", "--sourceset", F.SS,
                "--universe", F.U, "--challenge-of", "loc-1")
        code, out, err = ws(self.root, *argv, "--run-id", "chl-1")
        self.assertEqual(code, 0, out + err)
        self.assertEqual((self.paths.run_dir("chl-1") / "challenge-plan.json").read_bytes(),
                         before)
        code, out, err = ws(self.root, *argv, "--run-id", "chl-1", "--sample-rate", "1")
        self.assertNotEqual(code, 0, out + err)
        self.assertIn("challenge run chl-1 is harvested", out + err)
        self.assertIn("newly required", out + err)
        self.assertIn("a new --run-id", out + err)
        self.assertEqual((self.paths.run_dir("chl-1") / "challenge-plan.json").read_bytes(),
                         before)
        code, out, err = ws(self.root, *argv, "--run-id", "chl-2", "--sample-rate", "1")
        self.assertEqual(code, 0, out + err)
        # a challenge planned but not harvested re-plans as it always did
        code, out, err = ws(self.root, *argv, "--run-id", "chl-2", "--sample-rate", "0")
        self.assertEqual(code, 0, out + err)

    def test_several_subject_passages_of_one_article_are_each_challenged(self):
        """Nothing limits an article to one subject passage: each one's label is judged."""
        rows = [{"source_id": F.W1, "item": item, "prominence": "subject",
                 "outcome": "located", "scope": "formal_math", "passage_kind": "definition",
                 "locations": [{"library": "alpha", "declaration": f"Widget.{n}",
                                "relation": "exactMatch", "rank": 1,
                                "location_key": str(n) * 64}]}
                for n, item in ((1, "In-text #3fa91c07"), (2, "In-text #77e01b2c"))]
        recs = challenge_plan.draw(challenge_plan.classify(rows, run_id="loc-1"), seed=0,
                                   sample_rate=0, strong_sample_rate=0, absence_sample_rate=0)
        self.assertEqual(sorted((r["item"], r["rule"], r["stratum"], r["planned"])
                                for r in recs if r["target"] == "prominence"),
                         [("In-text #3fa91c07", "refute_by_default", "prominence:subject",
                           "required"),
                          ("In-text #77e01b2c", "refute_by_default", "prominence:subject",
                           "required")])
        # a central passage's prominence is no record of the plan, as before
        central = challenge_plan.classify([{**rows[0], "prominence": "central"}],
                                          run_id="loc-1")
        self.assertEqual([r["target"] for r in central], ["location"])

    def test_the_sample_is_a_function_of_the_seed(self):
        built = outcomes.build(self.paths, "loc-1", require_gate=True)
        records = challenge_plan.classify(built["outcomes"], run_id="loc-1")
        draws = []
        for seed in (0, 0, 1, 2, 3, 4):
            copy = [dict(r) for r in records]
            challenge_plan.draw(copy, seed=seed, sample_rate=0.2)
            draws.append(tuple(r["planned"] for r in copy))
        self.assertEqual(draws[0], draws[1])
        self.assertGreater(len(set(draws)), 1, "no seed changed the draw")
        with self.assertRaises(CfError):
            challenge_plan.draw(records, seed=0, sample_rate=1.5)

    def test_verdicts_apply_and_the_status_is_derived_after_them(self):
        self.challenge()
        code, out, err = ws(self.root, "tally", "--locate", "--run", "loc-1",
                            "--challenge-run", "chl-1")
        self.assertEqual(code, 0, out + err)
        rows = list(map(json.loads, self.paths.locate_outcomes_jsonl("loc-1").read_text()
                        .splitlines()))
        overturned = next(r for r in rows if r["source_id"] == F.W2
                          and r["prominence"] == "subject")
        self.assertEqual((overturned["outcome"], overturned["status"], overturned["challenge"]),
                         ("located", "formalized", "downgraded"))
        self.assertEqual(overturned["runs"]["challenge"], "chl-1")
        located = [r for r in rows if r["outcome"] == "located"]
        self.assertTrue(all(r["challenge"] in vocab.CHALLENGE_VERDICTS for r in located))

    def test_a_refuted_location_leaves_and_a_downgrade_changes_the_relation(self):
        def verdict(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "location" and rec["declaration"] == "Widget.exists_spanning_gadget":
                v.update(action="refute")
            if rec["target"] == "location" and rec["declaration"] == "Widget.card_dvd":
                v.update(action="downgrade", relation="relatedMatch")
            return v
        self.challenge(verdict=verdict)
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        lead = next(r for r in built["outcomes"] if r["source_id"] == F.W1
                    and r["prominence"] == "subject")
        self.assertEqual([l["declaration"] for l in lead["locations"]],
                         ["Beta.Widget.gadget_of_finite"])
        self.assertEqual([l["declaration"] for l in lead["refuted"]],
                         ["Widget.exists_spanning_gadget"])
        self.assertEqual(lead["challenge"], "downgraded")
        theorem = next(r for r in built["outcomes"] if r["passage_kind"] == "theorem"
                       and r["source_id"] == F.W1)
        # its one location downgraded to relatedMatch and no absence: unresolved, no status
        self.assertEqual((theorem["locations"][0]["relation"], theorem["status"]),
                         ("relatedMatch", None))

    def test_a_prominence_changes_only_by_an_overturn(self):
        """Seam #33: section 4's prominence verdict is uphold or overturn."""
        def verdict(action):
            def judge(rec):
                v = F.default_verdict(rec)
                if rec["target"] == "prominence":
                    v.update(action=action, prominence="central")
                return v
            return judge
        self.challenge(verdict=verdict("overturn"))
        plan = self.plan()
        (item,) = [r["item"] for r in plan["records"] if r["target"] == "prominence"
                   and r["prominence"] == "supporting" and r["planned"] == "required"]
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        self.assertEqual(next(r for r in built["outcomes"] if r["item"] == item)["prominence"],
                         "central")
        shutil.rmtree(self.paths.corpus_run_dir("chl-1"))
        F.write_harvest(self.paths, "chl-1",
                        F.challenge_documents(self.paths, verdict=verdict("downgrade")))
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        self.assertEqual(next(r for r in built["outcomes"] if r["item"] == item)["prominence"],
                         "supporting")

    def test_an_overturn_ranks_the_challengers_locations_first(self):
        """Seam #65: rank 1 is never the relatedMatch location an absence sat beside."""
        def loc(name, relation, rank):
            return {"library": "alpha", "declaration": name, "relation": relation,
                    "rank": rank, "location_key": name}
        old = [loc("Old.related", "relatedMatch", 1), loc("Old.exact", "exactMatch", 2)]
        new = [loc("New.second", "broadMatch", 2), loc("New.first", "exactMatch", 1)]
        merged = outcomes._overturned(new, old)
        self.assertEqual([(l["declaration"], l["rank"]) for l in merged],
                         [("New.first", 1), ("New.second", 2), ("Old.related", 3)])
        many = [loc(f"New.n{i}", "exactMatch", i) for i in range(1, 6)]
        self.assertEqual(len(outcomes._overturned(many, old)), vocab.MAX_LOCATIONS_PER_PASSAGE)

    def test_a_challenge_hunts_in_the_database_its_runs_searched(self):
        """Review C-03: a challenge of a P run never silently searches the full database,
        whose hunts would overturn absences with the very declarations the ablation
        removed; nor is a full run challenged with a planted one."""
        planted = self.root.parent / "planted.sqlite"
        shutil.copy2(self.paths.corpus_db(F.U), planted)
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--tools-corpus-db", str(planted))
        self.assertEqual(code, 0, out + err)
        self.locate("--tools-corpus-db", str(planted), run_id="loc-p")
        base = ("plan-locate", "--preset", "locate-challenge", "--sourceset", F.SS,
                "--universe", F.U, "--run-id", "chl-p")
        code, out, err = ws(self.root, *base, "--challenge-of", "loc-p")
        self.assertNotEqual(code, 0)
        self.assertIn("run loc-p's workers searched the planted database", out + err)
        self.assertIn(f"--tools-corpus-db {planted.resolve()}", out + err)
        self.assertFalse(self.paths.run_dir("chl-p").exists())
        other = self.root.parent / "other.sqlite"
        ablate(planted, other, ("beta", "Beta.Gadget"))
        code, out, err = ws(self.root, *base, "--challenge-of", "loc-p",
                            "--tools-corpus-db", str(other))
        self.assertNotEqual(code, 0)
        self.assertIn(hashing.sha256_file(planted)[:12], out + err)
        code, out, err = ws(self.root, *base, "--challenge-of", "loc-p",
                            "--tools-corpus-db", str(planted))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_json(self.paths.gate_json("chl-p"))["tools_corpus_db"]["sha256"],
                         hashing.sha256_file(planted))
        for extra in (("--challenge-of", "loc-1", "--tools-corpus-db", str(planted)),
                      ("--challenge-of", "loc-1", "--challenge-of", "loc-p")):
            code, out, err = ws(self.root, "plan-locate", "--preset", "locate-challenge",
                                "--sourceset", F.SS, "--universe", F.U, "--run-id", "chl-q",
                                *extra)
            self.assertNotEqual(code, 0, extra)
            self.assertIn("database", out + err)
            self.assertFalse(self.paths.run_dir("chl-q").exists())

    def test_a_challenge_of_another_arm_is_refused(self):
        """Seam #63: a blind challenge of an assisted locate run."""
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-challenge",
                            "--sourceset", F.SS, "--universe", F.U, "--challenge-of", "loc-1",
                            "--run-id", "chl-b", "--blind")
        self.assertNotEqual(code, 0)
        self.assertIn("assisted arm", out + err)

    def test_a_required_record_nobody_judged_is_owed(self):
        def verdict(rec):
            return F.default_verdict(rec)
        self.challenge(verdict=verdict)
        docs = F.challenge_documents(self.paths, "chl-1")
        for doc in docs.values():
            doc["verdicts"] = [v for v in doc["verdicts"] if v["target"] != "absence"]
        shutil.rmtree(self.paths.corpus_run_dir("chl-1"))
        F.write_harvest(self.paths, "chl-1", docs)
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        absent = next(r for r in built["outcomes"] if r["outcome"] == "absent")
        self.assertIsNone(absent["challenge"])


class TestTheChallengePlanReachesStrongRecords(Campaign):
    """The smoke-2 audit's FM2: the strong relations on a supporting passage were never
    challenged, and they decided every supporting passage's formalized status.

    The record that sets a supporting passage's status (its basis) is required when its
    relation is strong; the other strong records of a supporting passage are sampled per
    relation at ``--strong-sample-rate``; a supporting passage's absence is sampled at
    ``--absence-sample-rate`` and hunted.  The fixture's supporting definition passage of
    Widget lemma is an ``exactMatch`` to a definition (formalized); this test adds a
    ``closeMatch`` to a proved theorem beside it (``partial`` on a definition passage, so
    not its basis) and makes Gadget theorem's supporting passage an absence."""

    def setUp(self):
        super().setUp()
        F.plan_locate(self, self.root, run_id="loc-1")
        self.harvest()

    def harvest(self, *, second_basis: bool = False) -> None:
        docs = F.locate_documents(self.paths, "loc-1")
        for rel, doc in docs.items():
            for p in doc["passages"]:
                names = [l["declaration"] for l in p["locations"]]
                if "Widget.Spanning" in names:
                    p["locations"].append(F._loc("alpha", "Widget.card_dvd", "closeMatch", 3,
                                                 note="the order is written as a cardinal"))
                    if second_basis:
                        # a closeMatch to a definition formalizes the definition passage on
                        # its own too: a second basis
                        p["locations"].append(F._loc("beta", "Beta.GadgetLattice",
                                                     "closeMatch", 4,
                                                     note="the gadget is bundled with its "
                                                          "lattice"))
                if p["outcome"] == "unresolved":
                    p.update(outcome="absent", reason="",
                             receipt=F._receipt("gadget induction", "lattice induction",
                                                "induction on the order"))
        F.write_harvest(self.paths, "loc-1", docs)

    def plan(self, *extra: str, run_id: str = "chl-1") -> dict:
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-challenge",
                            "--sourceset", F.SS, "--universe", F.U, "--challenge-of", "loc-1",
                            "--run-id", run_id, *extra)
        self.assertEqual(code, 0, out + err)
        return read_json(self.paths.run_dir(run_id) / "challenge-plan.json")

    @staticmethod
    def by_declaration(plan: dict) -> dict[tuple[str, str], dict]:
        """The location records by (declaration, relation): Widget.card_dvd is located on
        two passages, a hasPart of the central theorem and the added closeMatch."""
        return {(r["declaration"], r["relation"]): r for r in plan["records"]
                if r["target"] == "location"}

    def test_the_basis_of_a_supporting_passage_is_required(self):
        plan = self.plan("--strong-sample-rate", "0", "--sample-rate", "0")
        spanning = self.by_declaration(plan)[("Widget.Spanning", "exactMatch")]
        self.assertEqual((spanning["prominence"], spanning["rule"], spanning["planned"]),
                         ("supporting", "basis", "required"))
        self.assertIsNotNone(spanning["task_id"])
        # the closeMatch beside it does not set the definition passage's status
        close = self.by_declaration(plan)[("Widget.card_dvd", "closeMatch")]
        self.assertEqual((close["rule"], close["stratum"], close["planned"]),
                         ("sample", "relation:closeMatch", "sampled_out"))
        # a central passage's locations are refuted by default, as before
        part = self.by_declaration(plan)[("Widget.card_dvd", "hasPart")]
        self.assertEqual((part["prominence"], part["rule"]), ("central", "refute_by_default"))

    def test_every_strong_record_that_sets_the_status_on_its_own_is_required(self):
        """The passage keeps its status while any one of them stands: requiring the
        best-ranked one alone left the others to a 50% draw, and a correct downgrade of
        it changed nothing."""
        self.harvest(second_basis=True)
        built = outcomes.build(self.paths, "loc-1", require_gate=True)
        row = next(r for r in built["outcomes"] if any(
            l["declaration"] == "Widget.Spanning" for l in r["locations"]))
        self.assertEqual([l["declaration"] for l in challenge_plan.bases(row)],
                         ["Widget.Spanning", "Beta.GadgetLattice"])
        plan = self.plan("--strong-sample-rate", "0", "--sample-rate", "0")
        drawn = self.by_declaration(plan)
        for decl in ("Widget.Spanning", "Beta.GadgetLattice"):
            relation = "exactMatch" if decl == "Widget.Spanning" else "closeMatch"
            self.assertEqual((drawn[(decl, relation)]["rule"], drawn[(decl, relation)]["planned"]),
                             ("basis", "required"), decl)
        self.assertEqual(drawn[("Widget.card_dvd", "closeMatch")]["planned"], "sampled_out")
        # one challenger judges both, and each record shows the passage's other locations
        tasks = {drawn[(d, r)]["task_id"] for d, r in (("Widget.Spanning", "exactMatch"),
                                                       ("Beta.GadgetLattice", "closeMatch"))}
        self.assertEqual(len(tasks), 1)
        manifest = read_json(self.paths.tend_root("chl-1") / "gate" / "tasks"
                             / f"{tasks.pop()}.json")
        spanning = next(r for d in manifest["documents"] for r in d["records"]
                        if r.get("declaration") == "Widget.Spanning")
        siblings = {(o["declaration"], o["planned"]) for o in spanning["passage_locations"]}
        self.assertEqual(siblings, {("Beta.GadgetLattice", "required"),
                                    ("Widget.card_dvd", "sampled_out"),
                                    ("Widget.spanning_conj", "sampled_out")})

    def test_the_basis_is_the_location_whose_own_status_is_the_passages(self):
        built = outcomes.build(self.paths, "loc-1", require_gate=True)
        row = next(r for r in built["outcomes"] if any(
            l["declaration"] == "Widget.Spanning" for l in r["locations"]))
        self.assertEqual(row["status"], "formalized")
        self.assertEqual(challenge_plan.basis(row)["declaration"], "Widget.Spanning")
        # a passage whose best location is a hasPart rests on it, and it is weak: sampled
        theorem = next(r for r in built["outcomes"] if r["passage_kind"] == "theorem"
                       and r["source_id"] == F.W1)
        self.assertEqual(challenge_plan.basis(theorem)["relation"], "hasPart")
        self.assertIsNone(challenge_plan.basis({**row, "status": None}))

    def test_the_other_strong_records_are_sampled_at_the_strong_rate(self):
        drawn = self.by_declaration(self.plan("--strong-sample-rate", "1", "--sample-rate", "0"))
        self.assertEqual(drawn[("Widget.card_dvd", "closeMatch")]["planned"], "required")
        default = self.plan(run_id="chl-2")
        self.assertEqual((default["strong_sample_rate"], default["sample_rate"],
                          default["absence_sample_rate"]), (0.5, 0.2, 0.25))
        # 0.5 of a one-record stratum is one record: at least one per non-empty stratum
        self.assertEqual(self.by_declaration(default)[("Widget.card_dvd", "closeMatch")]
                         ["planned"], "required")
        # the draw is stratified by relation: one stratum per strong relation
        records = [{"rule": "sample", "stratum": f"relation:{rel}", "key": f"{rel}{i}"}
                   for rel in ("exactMatch", "broadMatch") for i in range(4)]
        challenge_plan.draw(records, seed=0, sample_rate=0, strong_sample_rate=0.5)
        for rel in ("exactMatch", "broadMatch"):
            self.assertEqual(sum(r["planned"] == "required" for r in records
                                 if r["stratum"] == f"relation:{rel}"), 2, rel)

    def test_a_supporting_absence_is_sampled_at_the_absence_rate_and_hunted(self):
        none = self.plan("--absence-sample-rate", "0")
        (absence,) = [r for r in none["records"] if r["target"] == "absence"
                      and r["prominence"] == "supporting"]
        self.assertEqual((absence["rule"], absence["stratum"], absence["planned"]),
                         ("sample", "outcome:absent", "sampled_out"))
        every = self.plan("--absence-sample-rate", "1", run_id="chl-2")
        (absence,) = [r for r in every["records"] if r["target"] == "absence"
                      and r["prominence"] == "supporting"]
        self.assertEqual(absence["planned"], "required")
        manifest = read_json(self.paths.tend_root("chl-2") / "gate" / "tasks"
                             / f"{absence['task_id']}.json")
        self.assertIn(absence["key"], [r["key"] for d in manifest["documents"]
                                       for r in d["records"]])
        # the subject's absence is hunted, as before, whatever the rate
        subject = [r for r in none["records"] if r["target"] == "absence"
                   and r["prominence"] == "subject"]
        self.assertEqual([(r["rule"], r["planned"]) for r in subject], [("hunt", "required")])

    def test_the_rates_are_recorded_in_run_yaml_and_the_bench_planning_pin(self):
        from bench import score as score_mod
        self.plan("--strong-sample-rate", "0.75", "--absence-sample-rate", "0.5")
        block = read_yaml(self.paths.run_yaml("chl-1"))["locate"]
        self.assertEqual((block["sample_rate"], block["strong_sample_rate"],
                          block["absence_sample_rate"]), (0.2, 0.75, 0.5))
        self.assertEqual(score_mod.planning({"locate": block}),
                         {"per_task": None, "subject_only": False, "sample_rate": 0.2,
                          "strong_sample_rate": 0.75, "absence_sample_rate": 0.5})
        located = read_yaml(self.paths.run_yaml("loc-1"))["locate"]
        self.assertEqual((located["strong_sample_rate"], located["absence_sample_rate"]),
                         (None, None))
        # a run.yaml from before the two rates keeps the pin it had
        older = {k: v for k, v in block.items() if k not in score_mod.LATER_PLANNING_KEYS}
        self.assertEqual(score_mod.planning({"locate": older}),
                         {"per_task": None, "subject_only": False, "sample_rate": 0.2})

    def test_a_rate_outside_zero_to_one_is_refused(self):
        for flag in ("--strong-sample-rate", "--absence-sample-rate", "--sample-rate"):
            code, out, err = ws(self.root, "plan-locate", "--preset", "locate-challenge",
                                "--sourceset", F.SS, "--universe", F.U, "--challenge-of",
                                "loc-1", "--run-id", "chl-x", flag, "1.5")
            self.assertNotEqual(code, 0, flag)
            self.assertIn("fraction in [0, 1]", out + err)
            self.assertFalse(self.paths.run_dir("chl-x").exists())


# --------------------------------------------------------------------------
# the passage-id tool
# --------------------------------------------------------------------------

class TestPassageIds(Campaign):

    def test_it_fills_the_ids_the_head_rule_gives(self):
        docs = F.inventory_documents(self.paths)
        doc = next(d for k, d in docs.items() if F.W1 in k)
        expected = [(p["item"], p["span"], p["head_words"]) for p in doc["passages"]]
        for p in doc["passages"]:
            p.update(item="", span=None, head_words=0)
        source = self.paths.sourceset_source_dir(F.SS, F.W1)
        path = self.root.parent / "doc.json"
        path.write_text(json.dumps(doc))
        script = SCRIPTS / "locate" / "passage_ids.py"
        proc = subprocess.run([sys.executable, str(script), "--source-dir", str(source),
                               "--document", str(path), "--write"], capture_output=True,
                              text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        filled = json.loads(path.read_text())
        self.assertEqual([(p["item"], p["span"], p["head_words"]) for p in filled["passages"]],
                         expected)

    def test_it_refuses_an_ambiguous_quote_and_a_start_inside_a_formula(self):
        source = self.paths.sourceset_source_dir(F.SS, F.W1)
        layer = (source / "layer.txt").read_text()
        formulas = json.loads((source / "formulas.json").read_text())
        a = formulas[0]["span"][0]
        doc = {"passages": [{"quote": {"exact": "widget"}},
                            {"quote": {"exact": layer[a + 1:a + 30]}}]}
        problems = passage_ids.place(doc, layer, formulas, [])
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("times on the layer", problems[0])

    def test_it_refuses_a_source_with_no_paragraphs(self):
        """Seam #2: a missing paragraphs.json would end every passage at its quote."""
        source = self.root.parent / "copy"
        shutil.copytree(self.paths.sourceset_source_dir(F.SS, F.W1), source)
        (source / "paragraphs.json").unlink()
        path = self.root.parent / "doc.json"
        path.write_text(json.dumps({"passages": [{"quote": {"exact": "In widget theory"}}]}))
        proc = subprocess.run([sys.executable, str(SCRIPTS / "locate" / "passage_ids.py"),
                               "--source-dir", str(source), "--document", str(path)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("paragraphs.json", proc.stderr)


# --------------------------------------------------------------------------
# records and the export
# --------------------------------------------------------------------------

class TestRecords(unittest.TestCase):

    LAYER = ("a widget meets every gadget once and a widget meets every gadget twice "
             "while sprockets turn and the widget lemma holds in every finite case.")

    def test_the_capped_quote_is_twelve_words_and_unique(self):
        quote = records.cap_quote(self.LAYER, 0)
        self.assertEqual(len(quote["exact"].split()), 12)
        self.assertLessEqual(len(quote["prefix"]), 32)
        start = self.LAYER.index("widget meets every gadget twice")
        quote = records.cap_quote(self.LAYER, start, start + 25)
        self.assertEqual(quote["exact"], "widget meets every gadget")
        from gate import locate_text
        self.assertEqual(locate_text.find_quote(self.LAYER, quote["exact"], quote["prefix"],
                                                quote["suffix"]), [start])

    def test_a_quote_no_context_can_make_unique_is_refused(self):
        layer = "x " * 40 + "x"
        with self.assertRaises(records.RecordError):
            records.cap_quote(layer, 20)

    def source(self):
        return {"source_id": F.W1, "pageid": 1001, "revision": 2002, "title": "Widget lemma",
                "html_sha256": "1" * 64, "layer_sha256": records.sha256_text(self.LAYER),
                "renderer": "web-html/1",
                "uri": "https://en.wikipedia.org/w/index.php?curid=1001&oldid=2002",
                "container_title": "Wikipedia", "licence": "CC-BY-SA-4.0"}

    def universe(self):
        return {"id": "u1", "sha256": "2" * 64, "libraries": [
            {"id": "alpha", "repo": "https://example.org/alpha", "commit": F.ALPHA},
            {"id": "beta", "repo": "https://example.org/beta", "commit": F.BETA}]}

    def test_every_shape_passes_the_vendored_checker(self):
        loc = {"library": "alpha", "commit": F.ALPHA, "declaration": "Widget.x",
               "relation": "closeMatch", "rank": 1, "confidence": "high", "note": "0 ∈ ℕ",
               "location_key": "3" * 64, "proof_status": "proved", "module": "Alpha.W",
               "path": "Alpha/W.lean", "line": 3, "end_line": 9, "challenge": "survived"}
        base = {"source_id": F.W1, "item": "In-text #3fa91c07", "passage_kind": "theorem",
                "span": [0, 40], "section": ["Statement"], "prominence": "central"}
        located = {**base, "outcome": "located", "status": "formalized",
                   "challenge": "survived", "locations": [loc]}
        absent = {**base, "outcome": "absent", "status": "not_formalized",
                  "challenge": "survived", "locations": [],
                  "absence": {"receipt": {"queries": ["a", "b", "c"], "candidates": []},
                              "challenge": "survived"}}
        unresolved = {**base, "outcome": "unresolved", "status": None,
                      "challenge": "not_required", "locations": []}
        for row in (located, absent, unresolved):
            recs, cars = records.passage_records(self.source(), row, self.LAYER, self.universe())
            with self.subTest(outcome=row["outcome"]):
                self.assertEqual(records.check_all(recs), [])
                rec = recs[0]
                self.assertEqual(rec["id"], f"{F.W1}::In-text #3fa91c07")
                self.assertEqual(rec["subject"][0]["citation"]["id"], F.W1)
                self.assertNotIn("lines", json.dumps(rec["object"]))
        recs, cars = records.passage_records(self.source(), absent, self.LAYER, self.universe())
        (absence,) = recs
        self.assertEqual((absence["relation"], absence["status"]), ("noMatch", "not_formalized"))
        self.assertEqual([e["version"] for e in absence["object"]], [F.ALPHA, F.BETA])
        self.assertTrue(all("location" not in e for e in absence["object"]))
        ext = absence["extensions"][records.EXTENSION]
        self.assertEqual(ext["receipt"], {"sha256": cars[0][0], "queries": 3, "candidates": 0})
        recs, _ = records.passage_records(self.source(), unresolved, self.LAYER, self.universe())
        self.assertEqual(recs[0]["object"], [])
        self.assertNotIn("status", recs[0])
        recs, _ = records.passage_records(self.source(), located, self.LAYER, self.universe())
        self.assertEqual(recs[0]["note"], "0 ∈ ℕ")
        self.assertEqual(recs[0]["object"][0]["extensions"][records.EXTENSION]["proof_status"],
                         "proved")

    def test_no_challenge_value_is_refused(self):
        row = {"source_id": F.W1, "item": "In-text #3fa91c07", "passage_kind": "theorem",
               "span": [0, 40], "outcome": "unresolved", "challenge": None, "locations": [],
               "section": [], "prominence": "subject"}
        with self.assertRaises(records.RecordError) as caught:
            records.passage_records(self.source(), row, self.LAYER, self.universe())
        self.assertIn("hard rule 5", str(caught.exception))

    def test_the_host_side_constants_are_the_vocabularys(self):
        """Seam #84: records.py runs host-side with no cflib; its copies are pinned here."""
        self.assertEqual(
            (records.QUOTE_WORDS, records.CONTEXT_CHARS, records.EXTENSION,
             records.MAX_LOCATIONS, records.NO_MATCH, records.NOT_FORMALIZED),
            (vocab.EXPORT_QUOTE_WORDS, vocab.EXPORT_CONTEXT_CHARS, vocab.LOCATE_EXTENSION,
             vocab.MAX_LOCATIONS_PER_PASSAGE, vocab.NO_MATCH, vocab.STATUS_NOT_FORMALIZED))
        from gate import verify_locate
        self.assertEqual(lplan.GATE_SCHEMA, verify_locate.GATE_SCHEMA)
        self.assertEqual(lplan.GATE_SCRIPT, "verify_locate.py")

    def test_the_planner_reads_the_shared_values(self):
        """Seam #85: one definition of the root bound, the cap and the challenge rules."""
        self.assertFalse(hasattr(lp, "MAX_TASKS_PER_ROOT"))
        self.assertEqual((challenge_plan.REFUTE, challenge_plan.HUNT, challenge_plan.SAMPLE,
                          challenge_plan.NONE),
                         (vocab.CHALLENGE_RULE_REFUTE, vocab.CHALLENGE_RULE_HUNT,
                          vocab.CHALLENGE_RULE_SAMPLE, vocab.CHALLENGE_RULE_NONE))
        env = ltr.wrapper_env(lp.get(vocab.LOCATE), "/g", "/db")
        self.assertEqual(env["worker"]["CF_ATTEMPT_CAP"], str(vocab.LOCATE_ATTEMPT_CAP))

    def test_the_module_needs_no_cflib(self):
        text = (SCRIPTS / "locate" / "records.py").read_text()
        self.assertNotRegex(text, r"(?m)^\s*(from|import) cflib")
        code = ("import sys; sys.path.insert(0, %r); import records; "
                "print(records.cap_quote('a b c', 0)['exact'])" % str(SCRIPTS / "locate"))
        proc = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "a b c")


class TestExport(Campaign):

    def setUp(self):
        super().setUp()
        self.locate()
        self.challenge()

    def export(self, *extra, set_id="set-1"):
        return ws(self.root, "export", "literature-links", "--run", "loc-1", "--challenge-run",
                  "chl-1", "--set-id", set_id, *extra)

    def test_the_sealed_set(self):
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        out_dir = self.paths.export_dir("set-1")
        self.assertEqual(export_mod.verify_seal(out_dir), [])
        seal = read_json(out_dir / "SEAL.json")
        self.assertEqual(seal["schema"], "cf-locate-recordset/1")
        pin = F.universe_pin(self.paths)
        self.assertEqual(seal["universe"], {"id": pin["id"], "universe_sha256": pin["sha256"]})
        self.assertEqual(seal["sourceset"]["manifest_sha256"],
                         inputs.manifest(self.paths, F.SS).sha256)
        self.assertEqual(sorted(seal["files"]), ["records/enwiki-1001-2002.jsonl",
                                                 "records/enwiki-1003-2004.jsonl",
                                                 "sources.jsonl", "universe.json"])
        recs = [json.loads(line) for p in sorted((out_dir / "records").glob("*.jsonl"))
                for line in p.read_text().splitlines()]
        self.assertEqual(records.check_all(recs), [])
        self.assertTrue(all(r["extensions"][records.EXTENSION]["challenge"]
                            in vocab.CHALLENGE_VERDICTS for r in recs))
        quotes = [r["subject"][0]["location"]["quote"] for r in recs]
        self.assertTrue(all(len(q["exact"].split()) <= 12 for q in quotes))
        self.assertTrue(all(len(q.get("prefix", "")) <= 32 and len(q.get("suffix", "")) <= 32
                            for q in quotes))
        by_id: dict[str, int] = {}
        for r in recs:
            by_id[r["id"]] = by_id.get(r["id"], 0) + 1
        self.assertLessEqual(max(by_id.values()), 5)
        self.assertNotIn("proof_status", json.dumps([r["subject"] for r in recs]))
        sources = [json.loads(l) for l in (out_dir / "sources.jsonl").read_text().splitlines()]
        self.assertEqual([s["source_id"] for s in sources], [F.W1, F.W2])
        # seam #6: the citation is the sources stage's
        from sources import websource
        fam = websource.family("enwiki")
        cite = recs[0]["subject"][0]["citation"]
        self.assertIn(websource.revision_url(fam, 1001, 2002), json.dumps(cite))
        # seam #25: the universe file carries what the universe defines, nothing else
        uni = read_json(out_dir / "universe.json")
        libs = {lib["id"]: lib for lib in uni["libraries"]}
        self.assertEqual(libs["alpha"]["licence"], {"spdx": "Apache-2.0"})
        # display_name is the library's brain_lib, the one name the universe gives it
        self.assertEqual([lib["display_name"] for lib in uni["libraries"]], ["Alpha", "Beta"])

    def test_an_absence_record_and_its_receipt(self):
        def uphold(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "absence":
                v = {"item": rec["item"], "target": "absence", "action": "uphold",
                     "witness": "searched"}
            return v
        shutil.rmtree(self.paths.corpus_run_dir("chl-1"))
        F.write_harvest(self.paths, "chl-1", F.challenge_documents(self.paths, verdict=uphold))
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        out_dir = self.paths.export_dir("set-1")
        recs = [json.loads(l) for l in (out_dir / "records" / f"{F.W2}.jsonl").read_text()
                .splitlines()]
        absence = next(r for r in recs if r.get("relation") == "noMatch")
        ref = absence["extensions"][records.EXTENSION]["receipt"]
        sidecar = read_json(out_dir / "receipts" / f"{ref['sha256']}.json")
        self.assertEqual(len(sidecar["queries"]), ref["queries"])
        self.assertEqual(export_mod.verify_seal(out_dir), [])

    def test_re_exporting_is_byte_identical(self):
        self.assertEqual(self.export()[0], 0)
        out_dir = self.paths.export_dir("set-1")
        first = {p.relative_to(out_dir).as_posix(): p.read_bytes()
                 for p in sorted(out_dir.rglob("*")) if p.is_file()}
        # a file the previous seal named and this set does not carry is removed
        seal = read_json(out_dir / "SEAL.json")
        (out_dir / "records" / "stale.jsonl").write_text("{}\n")
        seal["files"]["records/stale.jsonl"] = hashing.sha256_text("{}\n")
        (out_dir / "SEAL.json").write_text(records.canonical(seal) + "\n")
        self.assertEqual(self.export()[0], 0)
        second = {p.relative_to(out_dir).as_posix(): p.read_bytes()
                  for p in sorted(out_dir.rglob("*")) if p.is_file()}
        self.assertEqual(first, second)

    def test_the_target_is_guarded(self):
        """Review #31, #35: never the checkout, never a directory that is not a set, and
        nothing but what the previous seal named is ever removed."""
        code, out, err = self.export("--out", str(SKILL / "exports-here"))
        self.assertNotEqual(code, 0)
        self.assertIn("inside the skill repo", out + err)
        self.assertFalse((SKILL / "exports-here").exists())
        other = self.root.parent / "other"
        (other / "records").mkdir(parents=True)
        (other / "records" / "notes.txt").write_text("mine")
        code, out, err = self.export("--out", str(other))
        self.assertNotEqual(code, 0)
        self.assertIn("holds no cf-locate-recordset/1 seal", out + err)
        self.assertEqual((other / "records" / "notes.txt").read_text(), "mine")
        self.assertEqual(self.export()[0], 0)
        out_dir = self.paths.export_dir("set-1")
        (out_dir / "records" / "notes.txt").write_text("mine")
        code, out, err = self.export()
        self.assertNotEqual(code, 0)
        self.assertIn("its seal does not name: records/notes.txt", out + err)
        self.assertEqual((out_dir / "records" / "notes.txt").read_text(), "mine")

    def test_a_passage_whose_challenge_is_owed_is_refused_by_name(self):
        docs = F.challenge_documents(self.paths, "chl-1")
        for doc in docs.values():
            doc["verdicts"] = [v for v in doc["verdicts"] if v["target"] != "absence"]
        shutil.rmtree(self.paths.corpus_run_dir("chl-1"))
        F.write_harvest(self.paths, "chl-1", docs)
        code, out, err = self.export()
        self.assertEqual(code, 1, out + err)
        report = read_json(self.paths.export_report_json("set-1"))
        (refusal,) = [r for r in report["refused"] if r["item"]]
        self.assertIn("hard rule 5", refusal["reason"])
        self.assertEqual(export_mod.verify_seal(self.paths.export_dir("set-1")), [])

    def test_a_source_over_the_byte_budget_is_refused_not_truncated(self):
        code, out, err = self.export("--byte-budget", "200")
        self.assertEqual(code, 1, out + err)
        report = read_json(self.paths.export_report_json("set-1"))
        self.assertEqual(sorted(r["source_id"] for r in report["refused"]), [F.W1, F.W2])
        self.assertFalse(list((self.paths.export_dir("set-1") / "records").glob("*.jsonl")))

    def test_an_ungated_locate_run_is_not_exported(self):
        F.write_harvest(self.paths, "loc-1", F.locate_documents(self.paths), gate_ok=False)
        code, out, err = self.export()
        self.assertNotEqual(code, 0)
        self.assertIn("gate", out + err)

    def test_a_moved_layer_is_refused(self):
        layer = self.paths.sourceset_source_dir(F.SS, F.W1) / "layer.txt"
        layer.write_text(layer.read_text() + " ")
        code, out, err = self.export()
        self.assertEqual(code, 1)
        report = read_json(self.paths.export_report_json("set-1"))
        self.assertIn("layer.txt has sha", report["refused"][0]["reason"])

    def test_a_source_whose_licence_does_not_allow_it_is_refused(self):
        """Seam #72: the licence class is the registry's, open, and named with its SPDX id."""
        from cflib import registry as registry_mod
        real = registry_mod.Registry.licence_class
        with mock.patch.object(registry_mod.Registry, "licence_class",
                               lambda self, sid: "closed" if sid == F.W1 else real(self, sid)):
            code, out, err = self.export()
        self.assertEqual(code, 1, out + err)
        report = read_json(self.paths.export_report_json("set-1"))
        (refusal,) = report["refused"]
        self.assertEqual(refusal["source_id"], F.W1)
        self.assertIn("the registry gives 'closed'", refusal["reason"])
        self.assertFalse((self.paths.export_dir("set-1") / "records" / f"{F.W1}.jsonl").exists())

    def test_the_licence_rule(self):
        from types import SimpleNamespace
        fam = SimpleNamespace(id="testwiki", spdx="CC-BY-4.0")
        def reg(cls):
            return SimpleNamespace(licence_class=lambda sid: cls)
        entry = {"source_id": "testwiki-1-2", "licence_class": "closed"}
        self.assertIn("only", export_mod.licence_problem(entry, fam, reg("closed")))
        entry["licence_class"] = "open_attribution"
        self.assertIsNone(export_mod.licence_problem(entry, fam, reg("open_attribution")))
        self.assertIn("no SPDX id", export_mod.licence_problem(
            entry, SimpleNamespace(id="testwiki", spdx=""), reg("open_attribution")))
        entry["licence_class"] = "public_domain"
        self.assertIsNone(export_mod.licence_problem(
            entry, SimpleNamespace(id="testwiki", spdx=""), reg("public_domain")))
        self.assertIn("the manifest records", export_mod.licence_problem(
            entry, fam, reg("open_attribution")))

    def test_a_closed_overlay_family_is_refused(self):
        """Seam #72: a family the workspace adds with a closed licence never exports."""
        from cflib import registry as registry_mod
        from cflib.workspace import Workspace
        from sources import websource
        (self.root / "registry.yaml").write_text(
            "families:\n"
            "  closedwiki:\n"
            "    title: Closed Wiki\n"
            "    site: https://closed.example.org\n"
            "    container_title: Closed Wiki\n"
            "    licence_class: closed\n"
            "    licence: {spdx: LicenseRef-closed, note: all rights reserved, "
            "redistribute: false}\n"
            "    attribution: '\"{title}\", Closed Wiki, {url}, {licence}'\n"
            "    revision_url: \"https://closed.example.org/?oldid={revision}\"\n",
            encoding="utf-8")
        workspace = Workspace.load(self.root)
        fam = websource.family("closedwiki", workspace)
        entry = websource.entry(fam, 7, 8, title="Widget")
        why = export_mod.licence_problem(entry, fam, registry_mod.Registry.load(workspace))
        self.assertIn("licence class 'closed'", why)


class TestTheCuratedJoins(unittest.TestCase):
    """Review #39, #40, #42, #45, #46, #47: what joins, and on what key."""

    def setUp(self):
        self.root = scratch(self)
        self.paths = F.make_workspace(self.root)
        F.write_sourceset(self.paths)
        self.lists = self.root.parent / "lists"
        self.lists.mkdir()

    def candidates(self, *extra):
        if not self.paths.universe_yaml(F.U).exists():
            F.write_universe(self.paths)
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--curated-library", "alpha", *extra)
        self.assertEqual(code, 0, out + err)
        return {sid: read_json(self.paths.candidates_json(F.SS, F.U, sid)) for sid in (F.W1, F.W2)
                if self.paths.candidates_json(F.SS, F.U, sid).is_file()}

    def vias(self, doc):
        return {(h["declaration"], h["via"]) for h in doc["curated"]}

    def test_a_suffixed_1000_key_is_a_further_theorem_of_its_qid(self):
        (self.lists / "1000.json").write_text(json.dumps({
            "Q99999001": {"title": "Widget lemma", "decl": "Widget.card_dvd"},
            "Q99999001X": {"title": "Widget lemma (II)", "decl": "Widget.Spanning"},
            "Q99999002A": {"title": "Gadget theorem", "decl": "Widget.spanning_conj"}}))
        docs = self.candidates("--curated-1000", str(self.lists / "1000.json"))
        self.assertLessEqual({("Widget.card_dvd", "mathlib_1000"),
                              ("Widget.Spanning", "mathlib_1000")}, self.vias(docs[F.W1]))
        self.assertIn(("Widget.spanning_conj", "mathlib_1000"), self.vias(docs[F.W2]))
        self.assertTrue(any(h["evidence"] == "1000.yaml Q99999001X" for h in docs[F.W1]["curated"]))
        (self.lists / "bad.json").write_text(json.dumps({"Widget": {"decl": "x"}}))
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--curated-library", "alpha",
                            "--curated-1000", str(self.lists / "bad.json"))
        self.assertNotEqual(code, 0)
        self.assertIn("is not a QID", out + err)

    def test_a_title_join_keys_on_the_title_only_and_never_on_another_qid(self):
        (self.lists / "100.yaml").write_text(
            "1:\n  title: spanning\n  decl: Widget.Spanning\n"
            "2:\n  title: gadget lattice theorem\n  decl: Widget.card_dvd\n"
            "3:\n  title: The Widget Lemma\n  decl: Widget.spanning_conj\n  wikidata: Q99999002\n")
        (self.lists / "meta.json").write_text(json.dumps({"libraries": {"alpha": [
            {"title": "Widget lemma", "wikidata": "Q99999002",
             "declarations": ["Widget.card_dvd"]}]}}))
        docs = self.candidates("--curated-100", str(self.lists / "100.yaml"),
                               "--library-metadata", str(self.lists / "meta.json"))
        w1 = docs[F.W1]
        self.assertNotIn(("Widget.Spanning", "mathlib_100"), self.vias(w1))   # a body bold term
        self.assertNotIn(("Widget.card_dvd", "mathlib_100"), self.vias(docs[F.W2]))  # lead bold
        self.assertNotIn(("Widget.spanning_conj", "mathlib_100"), self.vias(w1))
        self.assertNotIn(("Widget.card_dvd", "library_metadata"), self.vias(w1))
        reasons = {(d["declaration"], d["via"]): d["reason"] for d in w1["dropped"]}
        self.assertEqual(reasons[("Widget.spanning_conj", "mathlib_100")],
                         "the entry names another QID (Q99999002)")
        self.assertEqual(reasons[("Widget.card_dvd", "library_metadata")],
                         "the entry names another QID (Q99999002)")
        # its own QID still joins it, by QID
        self.assertIn(("Widget.spanning_conj", "mathlib_100"), self.vias(docs[F.W2]))

    def test_curly_apostrophes_and_dashes_fold(self):
        self.assertEqual(cand_mod.normalise_title("Ptolemy’s Theorem"),
                         cand_mod.normalise_title("Ptolemy's theorem"))
        self.assertEqual(cand_mod.normalise_title("Cauchy–Schwarz inequality"),
                         cand_mod.normalise_title("Cauchy-Schwarz_inequality"))

    def test_a_mobile_url_in_a_docstring_joins(self):
        decls = json.loads(json.dumps(F.DECLS))
        mobile = F._decl("Beta.Widget.gadget_of_finite", "Beta.Widget", "Beta/Widget.lean", 40,
                         52, "theorem", "proved", "∀ (w : Widget), Finite w → Gadget w",
                         "See https://en.m.wikipedia.org/wiki/Widget_lemma.")
        decls["beta"] = [mobile if d["name"] == mobile["name"] else d for d in decls["beta"]]
        F.write_universe(self.paths, decls=decls)
        docs = self.candidates()
        self.assertIn(("Beta.Widget.gadget_of_finite", "docstring_url"), self.vias(docs[F.W1]))

    def test_the_article_urls_are_the_canonical_forms_of_its_exact_title(self):
        """Review C-10: a '?', a quote mark, a '#' or a '%' in a title is part of the
        page's name, and the docstring side decodes ``%3F`` back into it; a title
        ending in '!' joins both the link MediaWiki writes (the '!' literal, which
        the docstring side strips as punctuation) and its ``%21`` spelling."""
        from corpus.ingest import wikipedia_urls
        site = "https://en.wikipedia.org"
        for title, written in (("What Is Mathematics?", "What_Is_Mathematics%3F"),
                               ('"Hello, World!" program', "%22Hello,_World!%22_program"),
                               ("C# (programming language)", "C%23_(programming_language)"),
                               ("100% rule", "100%25_rule"),
                               ("Lagrange's theorem (group theory)",
                                "Lagrange's_theorem_(group_theory)"),
                               ("Lagrange's theorem (group theory)",
                                "Lagrange%27s_theorem_(group_theory)"),
                               ("Erdős–Ko–Rado theorem", "Erd%C5%91s%E2%80%93Ko%E2%80%93Rado_theorem"),
                               ("widget lemma", "Widget_lemma"),
                               ("Widget lemma!", "Widget_lemma!"),
                               ("Widget lemma!", "Widget_lemma%21"),
                               ("Gadget, widget", "Gadget,_widget"),
                               ("Widget lemma*", "Widget_lemma*"),
                               ("Widget lemma*", "Widget_lemma%2A"),
                               ("Widget.", "Widget.")):
            with self.subTest(title=title, written=written):
                (want,) = wikipedia_urls(f"See {site}/wiki/{written} for more.")
                self.assertIn(want, cand_mod.article_urls(site, title))
        # verifier, round 4: a link typed by hand with the trailing '?' raw is stored up to
        # it, as d1's article side read the title; a '?' inside a title leaves another
        # article's whole name, which is not read as this one's
        self.assertEqual(cand_mod.article_urls(site, "What Is Mathematics?"),
                         [f"{site}/wiki/What_Is_Mathematics", f"{site}/wiki/What_Is_Mathematics?"])
        (raw,) = wikipedia_urls(f"See {site}/wiki/What_Is_Mathematics? for more.")
        self.assertIn(raw, cand_mod.article_urls(site, "What Is Mathematics?"))
        (raw,) = wikipedia_urls(f"See {site}/wiki/Who?_(film) for more.")
        self.assertEqual(raw, f"{site}/wiki/Who")
        self.assertEqual(cand_mod.article_urls(site, "Who? (film)"), [f"{site}/wiki/Who?_(film)"])
        self.assertEqual(cand_mod.article_urls(site, "?"), [f"{site}/wiki/?"])
        self.assertEqual(cand_mod.article_urls(site, "Widget lemma!"),
                         [f"{site}/wiki/Widget_lemma", f"{site}/wiki/Widget_lemma!"])
        self.assertEqual(cand_mod.article_urls(site, ""), [])

    def test_a_link_to_a_title_ending_in_a_bang_joins_in_either_spelling(self):
        """Review C-10's trade-off, through the join itself: the docstring links MediaWiki
        writes (``Widget_lemma!``) and its percent-encoded spelling both reach the
        article's curated hits."""
        entry = {"title": "Widget lemma!", "wikidata": ""}
        site = "https://en.wikipedia.org"
        rows = {f"{site}/wiki/Widget_lemma": [
                    {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
                     "value": f"{site}/wiki/Widget_lemma", "path": "Alpha/Widget.lean",
                     "line": 1}],
                f"{site}/wiki/Widget_lemma!": [
                    {"library": "beta", "declaration": "Beta.Widget.gadget_of_finite",
                     "value": f"{site}/wiki/Widget_lemma!", "path": "Beta/Widget.lean",
                     "line": 2}]}
        F.write_universe(self.paths)
        universe = inputs.load_universe(self.paths, F.U)
        conn = inputs.open_corpus(self.paths.corpus_db(F.U), universe)
        self.addCleanup(conn.close)
        curated, _dropped = cand_mod.curated_hits(entry, universe, conn, cand_mod.CuratedLists(),
                                                  site=site, urls=rows)
        joined = {(h["library"], h["declaration"]) for h in curated if h["via"] == "docstring_url"}
        self.assertEqual(joined, {("alpha", "Widget.exists_spanning_gadget"),
                                  ("beta", "Beta.Widget.gadget_of_finite")})

    def test_a_title_with_a_question_mark_or_a_quote_joins_its_docstring_link(self):
        """Review C-10, through the join itself: the docstring side stores
        ``What_Is_Mathematics%3F`` as ``.../What_Is_Mathematics?`` (the reader decodes it),
        and the article's own side used to read the raw ``?`` as a query and a ``"`` as
        the end of its URL, so neither article joined.  A link typed with the trailing
        ``?`` raw, which the docstring side stores without it, still joins (round 4)."""
        from corpus.ingest import wikipedia_urls
        site = "https://en.wikipedia.org"
        F.write_universe(self.paths)
        universe = inputs.load_universe(self.paths, F.U)
        conn = inputs.open_corpus(self.paths.corpus_db(F.U), universe)
        self.addCleanup(conn.close)
        for title, written in (("What Is Mathematics?", "What_Is_Mathematics%3F"),
                               ("What Is Mathematics?", "What_Is_Mathematics?"),
                               ('"Hello, World!" program', "%22Hello,_World!%22_program")):
            with self.subTest(title=title, written=written):
                (stored,) = wikipedia_urls(f"See {site}/wiki/{written} for more.")
                rows = {stored: [{"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
                                  "value": stored, "path": "Alpha/Widget.lean", "line": 1}]}
                curated, _dropped = cand_mod.curated_hits(
                    {"title": title, "wikidata": ""}, universe, conn, cand_mod.CuratedLists(),
                    site=site, urls=rows)
                self.assertEqual({(h["library"], h["declaration"], h["via"]) for h in curated},
                                 {("alpha", "Widget.exists_spanning_gadget", "docstring_url")})

    def test_a_repeated_source_is_one_and_a_partial_run_says_so(self):
        self.candidates("--source", F.W1, "--source", F.W1)
        row = read_json(self.paths.sourceset_state_json(F.SS))["candidates"][F.U]
        self.assertEqual((row["sources"], row["written"], row["partial"]), (1, [F.W1], True))


class TestTheReviewOfThePlan(Campaign):
    """Review #1, #2, #3, #9, #10, #11, #12, #15: the plan's own refusals and records."""

    def test_a_wholly_scoped_out_source_is_kept_and_skipped(self):
        docs = F.inventory_documents(self.paths)
        for p in docs[f"inv-{F.W2}/{F.W2}.json"]["passages"]:
            p["scope"] = "expository"
        shutil.rmtree(self.paths.corpus_run_dir("inv-1"))
        F.write_harvest(self.paths, "inv-1", docs)
        F.plan_locate(self, self.root)
        block = read_yaml(self.paths.run_yaml("loc-1"))["locate"]
        self.assertIn(F.W2, block["source_ids"])
        self.assertEqual(block["skipped"][F.W2], "no formal_math passage in the inventory")
        F.write_harvest(self.paths, "loc-1", F.locate_documents(self.paths, "loc-1"))
        built = outcomes.build(self.paths, "loc-1")
        self.assertEqual(len([r for r in built["outcomes"] if r["source_id"] == F.W2]), 2)
        records = challenge_plan.classify(built["outcomes"], run_id="loc-1")
        self.assertTrue(any(r["target"] == "scope" and r["source_id"] == F.W2 for r in records))

    def test_a_refused_re_plan_writes_nothing(self):
        gate = self.paths.gate_json("inv-1").read_bytes()
        local = (self.paths.run_dir("inv-1") / "gate.local.json").read_bytes()
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-1",
                            "--source", F.W1)
        self.assertNotEqual(code, 0)
        self.assertIn("stale task file", out + err)
        self.assertEqual(self.paths.gate_json("inv-1").read_bytes(), gate)
        self.assertEqual((self.paths.run_dir("inv-1") / "gate.local.json").read_bytes(), local)

    def test_a_run_id_of_a_run_read_or_of_another_preset_is_refused(self):
        for argv, needle in ((("--inventory-run", "inv-1", "--run-id", "inv-1"),
                              "names a run this plan reads"),
                             (("--inventory-run", "inv-1", "--run-id", "inv-1x"), None)):
            code, out, err = ws(self.root, "plan-locate", "--preset", "locate", "--sourceset",
                                F.SS, "--universe", F.U, *argv)
            if needle:
                self.assertNotEqual(code, 0)
                self.assertIn(needle, out + err)
            else:
                self.assertEqual(code, 0, out + err)
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-1x")
        self.assertNotEqual(code, 0)
        self.assertIn("planned as 'locate'", out + err)

    def test_an_assisted_plan_refuses_a_blind_candidates_file(self):
        path = self.paths.candidates_json(F.SS, F.U, F.W1)
        doc = read_json(path)
        doc.update(blind=True, curated=[])
        path.write_text(json.dumps(doc))
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-x")
        self.assertNotEqual(code, 0)
        self.assertIn("is the blind arm's", out + err)

    def test_mode_and_bound_flags(self):
        for argv, needle in ((("--preset", "locate-challenge", "--challenge-of", "inv-1",
                               "--source", F.W1), "does not narrow a challenge"),
                             (("--preset", "locate-inventory", "--max-open-tasks", "0"),
                              "at least 1")):
            with self.subTest(argv=argv):
                code, out, err = ws(self.root, "plan-locate", *argv, "--sourceset", F.SS,
                                    "--universe", F.U)
                self.assertNotEqual(code, 0)
                self.assertIn(needle, out + err)
        hint = None
        try:
            lplan.check_root_size([lplan.TaskSpec(id=f"t{i}", mode="locate-challenge", title="",
                                                  priority="", summary="", documents=[])
                                   for i in range(501)], vocab.LOCATE_CHALLENGE)
        except CfError as exc:
            hint = exc.hint
        self.assertIn("--challenge-of", hint)
        self.assertNotIn("--source", hint)

    def test_a_locate_task_says_where_to_read_each_passage(self):
        F.plan_locate(self, self.root)
        manifest = read_json(self.paths.tend_root("loc-1") / "gate" / "tasks"
                             / f"loc-{F.W1}-1.json")
        text = (self.paths.sourceset_source_dir(F.SS, F.W1) / "text.txt").read_text()
        layer = (self.paths.sourceset_source_dir(F.SS, F.W1) / "layer.txt").read_text()
        self.assertNotEqual(text, layer)
        theorem = manifest["documents"][0]["passages"][1]
        a, b = theorem["text_span"]
        self.assertTrue(text[a:b].startswith("Theorem. Let $G$ be a finite widget"), text[a:b])
        self.assertTrue(text[a:b].endswith("no smaller gadget spans it."), text[a:b])
        self.assertEqual(theorem["section"], ["Statement"])
        self.assertTrue(theorem["quote"]["exact"].startswith("Theorem. Let"))
        planned = read_json(self.paths.run_dir("loc-1") / "planned.json")
        self.assertEqual(planned["schema"], "cf-locate-planned/1")
        self.assertIn(manifest, planned["tasks"])

    def test_the_sample_count_is_exact(self):
        records = [{"rule": "sample", "stratum": "s", "key": f"k{i}"} for i in range(100)]
        challenge_plan.draw(records, seed=0, sample_rate=0.07)
        self.assertEqual(sum(r["planned"] == "required" for r in records), 7)

    def test_a_challenge_that_requires_nothing_is_still_planned(self):
        """Review #15: its plan gives every record its value, and needs no harvest."""
        F.plan_locate(self, self.root)
        F.write_harvest(self.paths, "loc-1", F.locate_documents(self.paths, "loc-1"))
        real = lplan.challenge_build

        def none_required(*a, **k):
            built = real(*a, **k)
            for rec in built["records"]:
                rec["planned"] = "not_required"
            built["batches"] = []
            return built
        with mock.patch.object(lplan, "challenge_build", none_required):
            code, out, err = ws(self.root, "plan-locate", "--preset", "locate-challenge",
                                "--sourceset", F.SS, "--universe", F.U, "--challenge-of",
                                "loc-1", "--run-id", "chl-0")
        self.assertEqual(code, 0, out + err)
        self.assertIn("no challenge task to run", out + err)
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-0"])
        located = [r for r in built["outcomes"] if r["outcome"] == "located"]
        self.assertTrue(located)
        self.assertTrue(all(r["challenge"] == "not_required" for r in located))


class TestTheReviewOfTheOutcomes(Campaign):
    """Review #7, #16-#20, #22-#28, #30, #33, #36: outcomes refuse what is not theirs."""

    def setUp(self):
        super().setUp()
        F.plan_locate(self, self.root)
        self.docs = F.locate_documents(self.paths, "loc-1")
        F.write_harvest(self.paths, "loc-1", self.docs)

    def reharvest(self, run_id, docs):
        shutil.rmtree(self.paths.corpus_run_dir(run_id))
        F.write_harvest(self.paths, run_id, docs)

    def challenge(self, verdict=None, run_id="chl-1", of="loc-1"):
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-challenge",
                            "--sourceset", F.SS, "--universe", F.U, "--challenge-of", of,
                            "--run-id", run_id)
        self.assertEqual(code, 0, out + err)
        docs = F.challenge_documents(self.paths, run_id, verdict=verdict)
        F.write_harvest(self.paths, run_id, docs)
        return docs

    def row(self, built, sid, kind):
        return next(r for r in built["outcomes"] if r["source_id"] == sid
                    and r["passage_kind"] == kind)

    def w1(self):
        return self.docs[f"loc-{F.W1}-1/{F.W1}.json"]

    def test_a_challenge_of_another_locate_run_is_refused(self):
        self.challenge()
        F.plan_locate(self, self.root, run_id="loc-2")
        F.write_harvest(self.paths, "loc-2", F.locate_documents(self.paths, "loc-2"))
        with self.assertRaises(CfError) as caught:
            outcomes.build(self.paths, "loc-2", challenge_runs=["chl-1"])
        self.assertIn("challenged loc-1, not loc-2", str(caught.exception))
        code, out, err = ws(self.root, "export", "literature-links", "--run", "loc-2",
                            "--challenge-run", "chl-1", "--set-id", "s2")
        self.assertNotEqual(code, 0)

    def test_refuting_every_location_judges_the_passage(self):
        def verdict(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "location" and rec["declaration"] == "Widget.card_dvd":
                v.update(action="refute")
            return v
        self.challenge(verdict)
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        theorem = self.row(built, F.W1, "theorem")
        self.assertEqual((theorem["locations"], theorem["challenge"], theorem["status"]),
                         ([], "downgraded", None))
        self.assertEqual(built["counts"]["unresolved"], 2)     # review #28
        code, out, err = ws(self.root, "export", "literature-links", "--run", "loc-1",
                            "--challenge-run", "chl-1", "--set-id", "s1")
        self.assertEqual(code, 0, out + err)

    def test_a_verdict_on_a_record_not_required_is_ignored_and_named(self):
        plan_docs = self.challenge()
        plan = read_json(self.paths.run_dir("chl-1") / "challenge-plan.json")
        (spare,) = [r for r in plan["records"] if r["target"] == "prominence"
                    and r["planned"] == "sampled_out"][:1]
        doc = next(d for d in plan_docs.values() if d["source_id"] == spare["source_id"])
        doc["verdicts"].append({"item": spare["item"], "target": "prominence",
                                "action": "overturn", "prominence": "subject",
                                "witness": "unplanned"})
        self.reharvest("chl-1", plan_docs)
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        self.assertTrue(any("was planned sampled_out" in p for p in built["problems"]))
        row = next(r for r in built["outcomes"] if r["item"] == spare["item"])
        self.assertEqual(row["prominence"], spare["prominence"])
        self.assertEqual(built["gates"]["chl-1"]["role"], "locate-challenge")
        self.assertEqual(built["gates"]["inv-1"]["role"], "locate-inventory")

    def test_a_downgrade_names_a_weaker_relation(self):
        def verdict(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "location" and rec["declaration"] == "Widget.card_dvd":
                v.update(action="downgrade", relation="exactMatch")
            return v
        self.challenge(verdict)
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        theorem = self.row(built, F.W1, "theorem")
        self.assertEqual(theorem["locations"][0]["relation"], "hasPart")
        self.assertIsNone(theorem["challenge"])
        self.assertIn("is not weaker", theorem["problem"])

    def test_a_scope_overturned_with_no_location_is_refused_by_the_export(self):
        def verdict(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "scope":
                v.update(action="overturn", locations=[])
            return v
        self.challenge(verdict)
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        history = next(r for r in built["outcomes"] if r["passage_kind"] == "other"
                       and r["source_id"] == F.W1)
        self.assertEqual((history["scope"], history["outcome"]), ("formal_math", None))
        code, out, err = ws(self.root, "export", "literature-links", "--run", "loc-1",
                            "--challenge-run", "chl-1", "--set-id", "s1")
        self.assertEqual(code, 1, out + err)
        report = read_json(self.paths.export_report_json("s1"))
        self.assertTrue(any("scope overturned" in r["reason"] for r in report["refused"]))

    def test_a_record_changed_since_the_plan_is_owed_again(self):
        self.challenge()
        self.w1()["passages"][1]["locations"][0]["relation"] = "narrowMatch"
        self.reharvest("loc-1", self.docs)
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        theorem = self.row(built, F.W1, "theorem")
        self.assertIsNone(theorem["challenge"])
        self.assertIn("it is owed again", theorem["problem"])

    def test_a_declaration_listed_twice_keeps_its_best_rank(self):
        lead = self.w1()["passages"][0]
        lead["locations"].append(F._loc("alpha", "Widget.exists_spanning_gadget", rank=3))
        self.reharvest("loc-1", self.docs)
        built = outcomes.build(self.paths, "loc-1")
        row = next(r for r in built["outcomes"] if r["item"] == lead["item"])
        self.assertEqual([(l["declaration"], l["rank"]) for l in row["locations"]],
                         [("Widget.exists_spanning_gadget", 1),
                          ("Beta.Widget.gadget_of_finite", 2)])
        self.assertTrue(any("listed twice" in p for p in built["problems"]))
        challenge_plan.plan(self.paths, ["loc-1"], seed=0, sample_rate=0.2)

    def test_a_document_off_the_pinned_rendering_is_refused_per_row(self):
        """Review #24: stamp the source, and refuse a mismatch."""
        w2 = self.docs[f"loc-{F.W2}-1/{F.W2}.json"]
        w2["source_sha"] = "0" * 64
        self.reharvest("loc-1", self.docs)
        built = outcomes.build(self.paths, "loc-1")
        rows = [r for r in built["outcomes"] if r["source_id"] == F.W2]
        self.assertTrue(all("not the rendering the run pinned" in r["problem"] for r in rows))
        located = self.row(built, F.W1, "theorem")
        self.assertEqual(located["source_sha"], self.w1()["source_sha"])
        self.assertTrue(all(r["source_sha"] for r in built["rows"]))

    def test_a_phantom_declaration_refuses_its_passage(self):
        """Review #36."""
        self.w1()["passages"][1]["locations"].append(_phantom())
        self.reharvest("loc-1", self.docs)
        self.challenge()
        code, out, err = ws(self.root, "export", "literature-links", "--run", "loc-1",
                            "--challenge-run", "chl-1", "--set-id", "s1")
        self.assertEqual(code, 1, out + err)
        report = read_json(self.paths.export_report_json("s1"))
        self.assertTrue(any("Widget.no_such_thing" in r["reason"] for r in report["refused"]))

    def test_unresolved_passages(self):
        """Review #26, #27: no status, and the worker named."""
        unresolved = self.docs[f"loc-{F.W2}-1/{F.W2}.json"]["passages"][1]
        built = outcomes.build(self.paths, "loc-1")
        row = next(r for r in built["outcomes"] if r["item"] == unresolved["item"])
        self.assertEqual((row["status"], row["unresolved_by"]), (None, "worker"))
        unresolved["locations"] = [F._loc("beta", "Beta.GadgetLattice")]
        self.reharvest("loc-1", self.docs)
        built = outcomes.build(self.paths, "loc-1")
        row = next(r for r in built["outcomes"] if r["item"] == unresolved["item"])
        self.assertIsNone(row["status"])
        self.assertIn("an unresolved passage has no locations", row["problem"])

    def test_a_passage_answered_by_another_task_is_ignored(self):
        """Review #25."""
        F.plan_locate(self, self.root, "--per-task", "2", run_id="loc-2")
        docs = F.locate_documents(self.paths, "loc-2")
        first, second = docs[f"loc-{F.W1}-1/{F.W1}.json"], docs[f"loc-{F.W1}-2/{F.W1}.json"]
        first["passages"].append(dict(second["passages"][0]))
        F.write_harvest(self.paths, "loc-2", docs)
        built = outcomes.build(self.paths, "loc-2")
        self.assertTrue(any(f"belongs to task loc-{F.W1}-2" in p for p in built["problems"]))
        self.assertEqual(sum(r["kind"] == "LOCATION" and r["item"] == second["passages"][0]["item"]
                             for r in built["rows"]), 2)   # Widget.Spanning + spanning_conj, once

    def test_the_planned_passages_are_not_guessed(self):
        """Review #23: no manifests, or an unharvested inventory, is a refusal."""
        (self.paths.run_dir("loc-1") / "planned.json").unlink()
        shutil.rmtree(self.paths.tend_root("loc-1") / "gate" / "tasks")
        with self.assertRaises(CfError) as caught:
            outcomes.build(self.paths, "loc-1")
        self.assertIn("task manifests hold 0", str(caught.exception))
        with self.assertRaises(CfError) as caught:
            outcomes.inventory(self.paths, "inv-nope", require_gate=False)
        self.assertIn("RECEIPT.json", str(caught.exception))

    def test_a_receipt_keeps_its_documented_keys_and_no_source_text(self):
        """Review #32, #34."""
        def uphold(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "absence":
                v = {"item": rec["item"], "target": "absence", "action": "uphold",
                     "witness": "searched"}
            return v
        w2 = self.docs[f"loc-{F.W2}-1/{F.W2}.json"]
        w2["passages"][0]["receipt"]["candidates"] = [
            {"library": "beta", "declaration": "Beta.GadgetLattice", "verdict": "rejected",
             "reason": "a conjecture", "secret": "dropped"}]
        self.reharvest("loc-1", self.docs)
        self.challenge(uphold)
        code, out, err = ws(self.root, "export", "literature-links", "--run", "loc-1",
                            "--challenge-run", "chl-1", "--set-id", "s1")
        self.assertEqual(code, 0, out + err)
        out_dir = self.paths.export_dir("s1")
        seal = read_json(out_dir / "SEAL.json")
        (receipt,) = list((out_dir / "receipts").glob("*.json"))
        digest = hashing.sha256_file(receipt)
        self.assertEqual(receipt.stem, digest)
        self.assertEqual(seal["files"][f"receipts/{digest}.json"], digest)
        self.assertEqual(set(read_json(receipt)["candidates"][0]),
                         {"library", "declaration", "verdict", "reason"})
        layer = (self.paths.sourceset_source_dir(F.SS, F.W2) / "layer.txt").read_text()
        w2["passages"][0]["receipt"]["queries"].append(" ".join(layer.split()[:14]))
        self.reharvest("loc-1", self.docs)
        code, out, err = ws(self.root, "export", "literature-links", "--run", "loc-1",
                            "--challenge-run", "chl-1", "--set-id", "s1")
        self.assertEqual(code, 1, out + err)
        report = read_json(self.paths.export_report_json("s1"))
        self.assertTrue(any("past the 12-word quote cap" in r["reason"]
                            for r in report["refused"]))


def _phantom() -> dict:
    return F._loc("alpha", "Widget.no_such_thing", "relatedMatch", 2)


class TestTheDocumentsNameWhatIsBuilt(unittest.TestCase):

    def test_the_layout_and_the_index_rows(self):
        claude = (SKILL / "CLAUDE.md").read_text()
        self.assertTrue("scripts/locate/" in claude, "CLAUDE.md section 2 omits scripts/locate/")
        skill = (SKILL / "SKILL.md").read_text()
        for row in ("`locate candidates", "`plan-locate", "`export literature-links",
                    "--locate"):
            self.assertTrue(row in skill, f"SKILL.md does not name {row}")
        presets_md = (SKILL / "templates" / "PRESETS.md").read_text()
        for mode in vocab.LOCATE_MODES:
            self.assertTrue(f"`{mode}`" in presets_md, f"templates/PRESETS.md omits {mode}")


if __name__ == "__main__":
    unittest.main()
