"""The locate planner: candidates, plan-locate, prompts, outcomes, records, export.

New code, for docs/proposals/wikipedia-locate.md sections 2.6, 2.7, 3, 5, 6,
7 and 11.1 in the formats of references/locate-documents.md.  The sourceset,
the universe and ``corpus.sqlite`` are built synthetically by the documented
format (tests/_locate_fixtures.py); every page, library and declaration is
made up.  What is pinned: L2's curated joins and their refusals, the three
presets and the plan's bounds and determinism, the gate.json, task-manifest
and run.yaml shapes of section 9, the wrapper variables, the prompts, the
finding rows of section 10 and their ids, the challenge rules and the seeded
sample, the derived status after challenge, the passage-id tool, the record
builder's caps and shapes against the vendored checker, and the sealed set.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
from plan import presets as presets_mod  # noqa: E402


def scratch(case: unittest.TestCase) -> Path:
    # resolved: the planner writes resolved paths, and on macOS the temp
    # directory is a symlink (/var/folders -> /private/var/folders)
    path = Path(tempfile.mkdtemp()).resolve()
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path / "ws"


def ws(root: Path, *argv: str) -> tuple[int, str, str]:
    return F.cf("--workspace", str(root), *argv)


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

    def doc(self, sid=F.W1):
        return read_json(self.paths.candidates_json(F.SS, F.U, sid))

    def test_the_curated_joins_and_the_lexical_hits(self):
        doc = self.doc()
        self.assertEqual(doc["schema"], "cf-locate-candidates/1")
        self.assertEqual(doc["universe_sha256"], F.universe_pin(self.paths)["sha256"])
        curated = {(h["library"], h["declaration"], h["via"]) for h in doc["curated"]}
        self.assertEqual(curated, {("alpha", "Widget.exists_spanning_gadget", "wikidata_tag"),
                                   ("beta", "Beta.Widget.gadget_of_finite", "docstring_url")})
        self.assertIn("spanning", doc["terms"])           # the lead's bold term
        lexical = {(h["library"], h["declaration"]) for h in doc["lexical"]}
        self.assertFalse(lexical & {(l, d) for l, d, _ in curated}, "a curated hit repeated")
        self.assertTrue(all(h["matched"] in ("title", "alias", "bold_term")
                            for h in doc["lexical"]))
        self.assertTrue(all(h["via"] in vocab.CANDIDATE_VIA for h in doc["curated"]))

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
        doc = self.doc()
        self.assertTrue(doc["blind"])
        self.assertEqual(doc["curated"], [])
        self.assertTrue(doc["lexical"])

    def test_an_unrendered_named_source_is_refused(self):
        code, out, err = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U, "--source", F.W3)
        self.assertNotEqual(code, 0)
        self.assertIn("not rendered", out + err)

    def test_the_state_file_records_the_run(self):
        st = read_json(self.paths.sourceset_state_json(F.SS))
        self.assertEqual(st["candidates"][F.U]["sources"], 2)
        self.assertEqual(st["candidates"][F.U]["skipped_unrendered"], 1)

    def test_name_words_and_the_query(self):
        self.assertEqual(inputs.name_words("Widget.exists_spanning_gadget"),
                         "widget exists spanning gadget")
        self.assertEqual(inputs.name_words("Beta.GadgetLattice"), "beta gadget lattice")
        self.assertEqual(inputs.fts_query('a "b" a'), '"a" "b"')
        self.assertEqual(inputs.fts_query("x y", any_word=True), '"x" OR "y"')


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

    def test_a_manifest_with_a_bad_header_or_order_is_refused(self):
        root = scratch(self)
        paths = F.make_workspace(root)
        path = F.write_sourceset(paths)
        lines = path.read_text().splitlines()
        path.write_text("\n".join([lines[0], lines[2], lines[1], *lines[3:]]) + "\n")
        with self.assertRaises(CfError):
            inputs.load_manifest(paths, F.SS)
        path.write_text("\n".join(lines[1:]) + "\n")
        with self.assertRaises(CfError):
            inputs.load_manifest(paths, F.SS)

    def test_a_universe_whose_sha_is_not_its_libraries_is_refused(self):
        root = scratch(self)
        paths = F.make_workspace(root)
        F.write_universe(paths)
        text = paths.universe_yaml(F.U).read_text()
        paths.universe_yaml(F.U).write_text(text.replace(F.ALPHA, "c" * 40))
        with self.assertRaises(CfError) as caught:
            inputs.load_universe(paths, F.U)
        self.assertIn("universe_sha256", str(caught.exception))


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

    def test_gate_json_is_the_section_9_shape(self):
        gate = self.gate()
        self.assertEqual(set(gate), {
            "schema", "mode", "preset", "run_id", "kinds", "universe", "corpus_db", "sourceset",
            "sources", "candidates_dir", "blind", "inventory", "challenged_runs", "findings_dir",
            "tasks_dir", "log_dir", "orchestrator_db", "max_locations", "min_absence_queries",
            "vendor"})
        self.assertEqual(gate["schema"], "cf-gate-locate/1")
        self.assertEqual(gate["kinds"], ["PASSAGE", "UNRESOLVED"])
        self.assertEqual(sorted(gate["sources"]), [F.W1, F.W2])
        self.assertEqual(gate["universe"]["libraries"]["alpha"]["commit"], F.ALPHA)
        self.assertEqual(gate["corpus_db"]["sha256"],
                         hashing.sha256_file(self.paths.corpus_db(F.U)))
        manifest = inputs.load_manifest(self.paths, F.SS)
        self.assertEqual(gate["sourceset"]["manifest_sha256"], manifest.sha256)
        self.assertEqual(set(gate["vendor"]), {"literature_links", "formal_companion_annotations"})
        self.assertIsNone(gate["inventory"])

    def test_the_tend_root_stages_the_locate_gate_directory(self):
        gate_dir = self.paths.tend_root("inv-1") / "gate"
        for name in ("gate.json", "locate_text.py", "passage_ids.py", "locate_records.py",
                     "vendor/literature_links/companion.py",
                     "vendor/formal_companion_annotations/ids.py",
                     "vendor/literature_links.manifest.json"):
            self.assertTrue((gate_dir / name).is_file(), name)
        self.assertFalse(list(gate_dir.rglob("__pycache__")))
        self.assertFalse((gate_dir / "verify_findings.py").exists())
        cfg = (self.paths.tend_root("inv-1") / "config.yaml").read_text()
        self.assertIn(f"{gate_dir}/verify_locate.py, --gate-config", cfg)
        self.assertIn("--changed-files", cfg)

    def test_the_wrappers_export_the_locate_variables(self):
        bindir = self.paths.tend_root("inv-1") / "bin"
        worker = (bindir / "worker-agent.sh").read_text()
        reviewer = (bindir / "reviewer-agent.sh").read_text()
        gate_dir = self.paths.tend_root("inv-1") / "gate"
        self.assertIn(f'CF_EXTRA_TOOLS="Bash(python3 {gate_dir}/corpus_search.py:*),'
                      f'Bash(python3 {gate_dir}/corpus_show.py:*),'
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
        self.assertEqual(block["sourceset"]["version"], 1)
        self.assertEqual(block["universe"]["corpus_db_sha256"],
                         hashing.sha256_file(self.paths.corpus_db(F.U)))
        self.assertEqual(block["skipped"], {F.W3: "not rendered"})
        self.assertEqual(doc["tasks"]["count"], 2)
        self.assertEqual(doc["repos"], [])
        self.assertIn("worker.md", doc["prompts"])
        st = read_json(self.paths.sourceset_state_json(F.SS))
        self.assertEqual(st["runs"]["inv-1"]["tasks"], 2)

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
        self.assertEqual(remote["sourceset"]["sources_dir"], f"/srv/cf/sourcesets/{F.SS}/sources")
        self.assertEqual(remote["tasks_dir"], "/srv/cf/inv-r-gate/tasks")
        self.assertEqual(local["corpus_db"]["path"], str(self.paths.corpus_db(F.U)))
        doc = read_yaml(self.paths.run_yaml("inv-r"))
        self.assertEqual(doc["budget"]["max_cost"], 50.0)
        self.assertFalse(doc["consent"]["given"])


class TestPlanLocate(Campaign):

    def test_tasks_carry_the_frozen_formal_math_passages(self):
        self.locate()
        manifests = {p.stem: read_json(p) for p in
                     (self.paths.tend_root("loc-1") / "gate" / "tasks").glob("*.json")}
        self.assertEqual(sorted(manifests), [f"loc-{F.W1}-1", f"loc-{F.W2}-1"])
        w1 = manifests[f"loc-{F.W1}-1"]["documents"][0]["passages"]
        self.assertEqual(len(w1), 3)                     # the expository paragraph is out
        self.assertEqual([p["span"][0] for p in w1], sorted(p["span"][0] for p in w1))
        self.assertEqual(set(w1[0]), {"item", "span", "head_words", "prominence", "passage_kind"})
        gate = read_json(self.paths.gate_json("loc-1"))
        self.assertEqual(gate["inventory"]["run_id"], "inv-1")
        self.assertEqual(gate["kinds"], ["ABSENCE", "LOCATION", "UNRESOLVED"])

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
        F.write_harvest(self.paths, "inv-1", F.inventory_documents(F.manifest_entries(self.paths)),
                        gate_ok=None)
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
        required = [r for r in plan["records"] if r["planned"] == "required"]
        self.assertTrue(all(r["task_id"] == "chl-0001" for r in required))
        manifest = read_json(self.paths.tend_root("chl-1") / "gate" / "tasks" / "chl-0001.json")
        self.assertEqual(sum(len(d["records"]) for d in manifest["documents"]), len(required))
        self.assertEqual(read_json(self.paths.gate_json("chl-1"))["challenged_runs"], ["loc-1"])

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


# --------------------------------------------------------------------------
# the passage-id tool
# --------------------------------------------------------------------------

class TestPassageIds(Campaign):

    def test_it_fills_the_ids_the_head_rule_gives(self):
        docs = F.inventory_documents(F.manifest_entries(self.paths))
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
                "html_sha256": "1" * 64, "renderer": "web-html/1",
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
                "span": [0, 40]}
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
               "span": [0, 40], "outcome": "unresolved", "challenge": None, "locations": []}
        with self.assertRaises(records.RecordError) as caught:
            records.passage_records(self.source(), row, self.LAYER, self.universe())
        self.assertIn("hard rule 5", str(caught.exception))

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
        self.assertEqual(seal["universe"], F.universe_pin(self.paths))
        self.assertEqual(seal["sourceset"]["manifest_sha256"],
                         inputs.load_manifest(self.paths, F.SS).sha256)
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
        (out_dir / "records" / "stale.jsonl").write_text("{}\n")
        self.assertEqual(self.export()[0], 0)
        second = {p.relative_to(out_dir).as_posix(): p.read_bytes()
                  for p in sorted(out_dir.rglob("*")) if p.is_file()}
        self.assertEqual(first, second)

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
        self.assertIn("layer.txt is not the layer", report["refused"][0]["reason"])


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
