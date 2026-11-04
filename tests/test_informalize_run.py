"""`cf run` and `cf harvest` carrying an informalize phase.

New code, for docs/proposals/informalize.md sections 8.1 and 8.5 (component 5b of its
section 11).  The existing commands do the work; what is new is what they have to know
about a phase:

* the transfer sends the output repository at the base commit into the entrypoint (with
  `tasks/` it is the entrypoint's first commit, so the run's `data/` stays out of it) and,
  with `--include-sources`, the library's source read-only to the one directory of this
  id and commit, under the same before/after tree digest as a companion repo;
* the verify stage runs the informalize gate's own `--self-check`, and the staged tend
  config is checked against `verify_informal.py`;
* the harvest brings the files the run's tasks own home beside its documents, under
  `corpus/<run>/tree/`, with the host's own sha256 as the expectation, and re-runs the
  run's staged gate with `--findings` over what landed.

The whole fleet, phase by phase, is tests/e2e/informalize_synthetic/driver.py, which
harvests each of its runs from the entrypoint it makes; here the pieces are held to their
rules one at a time, on the phases that need no TeX.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
for _entry in (str(HERE), str(SKILL / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import _informalize_doc as doc  # noqa: E402
from harvest import files as harvest_files  # noqa: E402
from harvest import harvest as harvest_mod  # noqa: E402
from harvest import informal_files  # noqa: E402
from informalize import stages  # noqa: E402
from run import manifest, remote  # noqa: E402

DRIVER = HERE / "e2e" / "informalize_synthetic" / "driver.py"
#: the host every run here is planned for; nothing is ever sent to it
HOSTS = ("\nhosts:\n  allowlist: [testbox]\n  testbox:\n    ssh: testbox.example\n"
         "    remote_root: /srv/cf\n    tend_version: \">=0.1\"\n")


def load_driver():
    spec = importlib.util.spec_from_file_location("informalize_synthetic_driver_run", DRIVER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-informalize-run-"))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path.resolve()


class PhaseCase(unittest.TestCase):
    """One synthetic informalize id with a phase planned for an allowlisted host."""

    phase = "items"
    #: what this phase needs on the `cf plan-informalize` command line beyond the caps
    plan_args: tuple[str, ...] = ()

    def setUp(self):
        self.driver = load_driver()
        self.pipe = self.driver.Pipeline(scratch(self))
        self.pipe.workspace()
        workspace_yaml = self.pipe.ws / "workspace.yaml"
        workspace_yaml.write_text(workspace_yaml.read_text(encoding="utf-8") + HOSTS,
                                  encoding="utf-8")
        self.prepare()
        self.pipe.plan(self.phase, "--workers", "1", "--reviewers", "1",
                       "--host", "testbox", "--i-have-consent", *self.plan_args)
        self.run_id = self.pipe.runs[self.phase]
        self.run_dir = self.pipe.run_dir(self.phase)
        self.data = self.read_yaml(self.run_dir / "run.yaml")

    def prepare(self) -> None:
        """What the output repository must hold before this phase is planned."""

    @staticmethod
    def read_yaml(path: Path) -> dict:
        from cflib.yamlio import read_yaml

        return read_yaml(path)

    def manifest_entries(self, *, include_sources: bool = True):
        return manifest.build_manifest(
            workspace_dir=Path(self.pipe.ws), skill_dir=SKILL, run_id=self.run_id,
            run=self.data, host=self.host(), include_sources=include_sources,
            include_texts=False, companions={})

    def host(self) -> dict:
        from cflib.workspace import Workspace

        return manifest.resolve_host(Workspace.load(self.pipe.ws), "testbox")

    def entry(self, entries, name):
        found = [e for e in entries if e["name"] == name]
        self.assertEqual(len(found), 1, f"{name} is not one entry of {[e['name'] for e in entries]}")
        return found[0]


class TestWhatTheTransferSends(PhaseCase):

    def test_the_entrypoint_is_the_repository_and_the_tasks(self):
        entries, _warnings = self.manifest_entries()
        repo = self.entry(entries, "repo")
        self.assertEqual(repo["dest"], f"/srv/cf/{self.run_id}/")
        self.assertEqual(repo["src"], str(self.run_dir / "repo"))
        self.assertTrue(repo["exists"])
        self.assertEqual(self.entry(entries, "tasks")["dest"], f"/srv/cf/{self.run_id}/tasks/")
        # `cf informalize apply` reads the entrypoint's first commit against the
        # base commit's tree, so nothing else goes into it
        self.assertEqual([e for e in entries if e["name"] == "data"], [])

    def test_the_library_source_travels_read_only_to_its_own_directory(self):
        entries, warnings = self.manifest_entries()
        library = self.entry(entries, "library-src")
        self.assertEqual(library["dest"],
                         f"/srv/cf/library-src/{self.driver.ID}-{doc.COMMIT[:12]}/")
        self.assertIn("--chmod=D0555,F0444", library["rsync_flags"])
        self.assertIn("--delete", library["rsync_flags"])
        self.assertEqual(library["tree_sha256"],
                         self.data["informalize"]["library"]["src_sha256"])
        self.assertEqual([w for w in warnings if "library" in w], [])

    def test_a_library_source_that_moved_since_the_plan_is_named(self):
        src = self.pipe.paths.informalize_library_src(self.driver.ID)
        (src / "ExampleLib" / "Topology" / "Glue" / "Basic.lean").write_text(
            "-- another tree\n", encoding="utf-8")
        _entries, warnings = self.manifest_entries()
        self.assertTrue(any("src_sha256" in w for w in warnings), warnings)

    def test_without_include_sources_it_says_the_workers_have_no_library(self):
        entries, warnings = self.manifest_entries(include_sources=False)
        self.assertEqual([e for e in entries if e["name"] == "library-src"], [])
        self.assertTrue(any("--include-sources" in w for w in warnings), warnings)

    def test_the_verify_stage_runs_the_gate_s_own_self_check(self):
        steps = remote.plan_commands(
            host=self.host(), run_id=self.run_id, run=self.data,
            entries=self.manifest_entries()[0], names=manifest.remote_names(self.data,
                                                                            self.run_id),
            budget={"max_cost": 300.0, "currency": "USD"},
            concurrency={"workers": 1, "reviewers": 1},
            informalize=self.data["informalize"])
        verify = [s for s in steps if s["stage"] == "verify"]
        self_check = [s for s in verify if "--self-check" in s["shell"]]
        self.assertEqual(len(self_check), 1, [s["title"] for s in verify])
        self.assertIn("verify_informal.py", self_check[0]["shell"])
        self.assertIn("python3 -I", self_check[0]["shell"])
        self.assertNotIn("verify_findings.py", " ".join(s["shell"] for s in steps))
        # the digest of the library's source, before the run and after it
        for stage in ("verify", "monitor"):
            self.assertTrue(any("library-src" in s["title"] for s in steps
                                if s["stage"] == stage), stage)

    def test_the_staged_config_calls_the_informalize_gate(self):
        self.assertEqual(manifest.gate_config_warnings(Path(self.pipe.ws), self.run_id,
                                                       self.host(), self.data), [])

    def test_cf_run_prints_the_plan_and_records_what_it_would_send(self):
        code, text = self.pipe.cf("run", self.run_id, "--host", "testbox",
                                  "--i-have-consent", "--include-sources")
        self.assertEqual(code, 0, text)
        self.assertIn("verify_informal.py", text)
        self.assertIn("dry run", text)
        recorded = self.read_yaml(self.run_dir / "run.yaml")["run"]
        sent = {row["name"]: row for row in recorded["transfer"]}
        self.assertIn("repo", sent)
        self.assertEqual(sent["library-src"]["tree_sha256"],
                         self.data["informalize"]["library"]["src_sha256"])
        self.assertTrue(recorded["include_sources"])

    def test_a_phase_planned_for_this_machine_is_refused(self):
        self.pipe.plan(self.phase, "--run-id", "local-one")
        code, text = self.pipe.cf("run", "local-one", "--i-have-consent", ok=False)
        self.assertNotEqual(code, 0)
        self.assertIn("allowlisted host", text)


class TestTheItemsHarvest(PhaseCase):
    """The phase whose fleet writes no findings document at all: its output is the table."""

    def harvest(self, *argv: str, ok: bool = True):
        wt = self.pipe.worktree(self.phase, "items")
        self.pipe.write_json(wt / "informal/items.json", self.pipe.provisional_table())
        self.pipe.write(wt / "informal/items-review.md", "| id | kind | title |\n|---|---|---|\n")
        self.pipe.complete(wt, "items")
        self.pipe.merge(self.phase, "items")
        entrypoint = self.pipe.entrypoint(self.phase)
        code, text = self.pipe.cf("harvest", self.run_id, "--from", str(entrypoint),
                                  *argv, ok=ok)
        corpus = self.pipe.paths.corpus_run_dir(self.run_id)
        receipt = json.loads((corpus / "RECEIPT.json").read_text(encoding="utf-8"))
        return code, text, corpus, receipt

    def test_the_files_the_phase_wrote_land_with_the_host_s_own_sha256(self):
        code, text, corpus, receipt = self.harvest()
        self.assertEqual(code, 0, text)
        block = receipt["informalize"]
        self.assertEqual(block["owned"], ["informal/items-review.md", "informal/items.json"])
        self.assertEqual(block["expected"], block["owned"])
        self.assertEqual(sorted(block["paths"]), block["owned"])
        self.assertEqual(block["missing"], [])
        self.assertEqual(block["sha_mismatched"], [])
        # neither is pinned by the base commit, so both count as written by the run
        self.assertEqual(sorted(block["changed"]), block["owned"])
        for rel in block["owned"]:
            self.assertTrue((corpus / "tree" / rel).is_file(), rel)
        self.assertTrue(receipt["complete"])
        self.assertTrue(receipt["gate"]["ok"], receipt["gate"]["problems"])
        self.assertEqual(receipt["gate"]["preset"], "informalize-items")

    def test_the_landed_table_is_no_finding_of_the_corpus(self):
        _code, _text, corpus, receipt = self.harvest()
        self.assertEqual(harvest_files.iter_finding_files(corpus), [])
        self.assertEqual(receipt["expected"]["count"], 0)
        self.assertEqual(receipt["extra"], [])
        self.assertFalse(harvest_files.is_finding_name("tree/informal/items.json"))

    def test_a_table_the_gate_refuses_fails_the_receipt(self):
        wt = self.pipe.worktree(self.phase, "items")
        table = self.pipe.provisional_table()
        table["items"][0]["declarations"][0]["line"] = 13      # not the source's keyword line
        self.pipe.write_json(wt / "informal/items.json", table)
        self.pipe.write(wt / "informal/items-review.md", "| id |\n|---|\n")
        self.pipe.complete(wt, "items")
        self.pipe.merge(self.phase, "items")
        code, text = self.pipe.cf("harvest", self.run_id, "--from",
                                  str(self.pipe.entrypoint(self.phase)), ok=False)
        self.assertNotEqual(code, 0)
        receipt = json.loads((self.pipe.paths.corpus_run_dir(self.run_id) / "RECEIPT.json")
                             .read_text(encoding="utf-8"))
        self.assertFalse(receipt["complete"])
        self.assertFalse(receipt["gate"]["ok"])
        self.assertTrue(any("[4]" in p for p in receipt["gate"]["problems"]),
                        receipt["gate"]["problems"])


class TestTheDraftHarvest(PhaseCase):
    """The phase that writes the text: no findings document, and none to re-gate at home.

    A draft task's output is its section's `.tex`, which the gate judges on the run host
    over the change set; `verify_informal.py --findings` has no mode that reads one.  An
    empty document list here is the phase's shape, so the gate block is a pass with a
    note -- the "empty corpus is not a clean one" refusal would have made every draft
    harvest exit 1 (the phase that needs TeX, which is why no test saw it).
    """

    phase = "draft"
    plan_args = ("--sections", "all")

    def prepare(self) -> None:
        pipe = self.pipe
        pipe.items()
        informal = pipe.repo / "informal"
        pipe.write_json(informal / "hypotheses.json", doc.HYPOTHESES)
        notation = informal / "notation.sty"
        marker = "% Standing hypotheses (one \\fcdefhyp per key of hypotheses.json).\n"
        notation.write_text(notation.read_text(encoding="utf-8").replace(
            marker, marker + doc.NOTATION_HYPOTHESES), encoding="utf-8")
        pipe.git(pipe.repo, "add", "-A")
        pipe.git(pipe.repo, "commit", "-qm", "the conventions")
        stages.write_receipt(pipe.paths, self.driver.ID, "I4", inputs={}, outputs={},
                             checks={"synthetic": True}, runs=["by hand"])

    def sections(self) -> dict[str, str]:
        """``{task: its one owned section file}``, the planner's own record."""
        return {task: paths[0]
                for task, paths in sorted(self.data["informalize"]["paths"].items())}

    def merged(self) -> Path:
        """The entrypoint with each draft task's section merged into main."""
        for text, (task, rel) in zip((doc.SECTION_1, doc.SECTION_2),
                                     sorted(self.sections().items())):
            wt = self.pipe.worktree(self.phase, task)
            self.pipe.write(wt / rel, text)
            self.pipe.complete(wt, task)
        self.pipe.merge(self.phase, *sorted(self.sections()))
        return self.pipe.entrypoint(self.phase)

    def receipt(self) -> dict:
        return json.loads((self.pipe.paths.corpus_run_dir(self.run_id) / "RECEIPT.json")
                          .read_text(encoding="utf-8"))

    def test_the_sections_land_and_the_gate_has_no_document_to_re_read(self):
        entrypoint = self.merged()
        code, text = self.pipe.cf("harvest", self.run_id, "--from", str(entrypoint))
        self.assertEqual(code, 0, text)
        receipt = self.receipt()
        self.assertTrue(receipt["complete"], receipt)
        block = receipt["informalize"]
        self.assertEqual(block["owned"], sorted(self.sections().values()))
        self.assertEqual(sorted(block["changed"]), block["owned"])
        for rel in block["owned"]:
            self.assertTrue((self.pipe.paths.corpus_run_dir(self.run_id) / "tree" / rel)
                            .is_file(), rel)
        gate = receipt["gate"]
        self.assertTrue(gate["ok"], gate)
        self.assertEqual(gate["files"], 0)
        self.assertEqual(gate["problems"], [])
        self.assertNotIn("broken", gate)
        self.assertIn("no document", gate["note"])

    def test_a_path_the_entrypoint_changed_that_no_task_owns_is_refused(self):
        entrypoint = self.merged()
        # a hand edit on the box, outside every task's manifest: the harvest fetches
        # only owned paths, so the entrypoint's own change set is what sees it
        self.pipe.write(entrypoint / "informal" / "preamble.tex", "% edited by hand\n")
        self.pipe.git(entrypoint, "add", "-A")
        self.pipe.git(entrypoint, "commit", "-qm", "a hand edit")
        code, text = self.pipe.cf("harvest", self.run_id, "--from", str(entrypoint), ok=False)
        self.assertNotEqual(code, 0)
        receipt = self.receipt()
        self.assertFalse(receipt["complete"])
        self.assertEqual(receipt["informalize"]["unowned"], ["informal/preamble.tex"])
        self.assertIn("informal/preamble.tex", receipt["informalize"]["host_changed"])
        self.assertIn("UNOWNED", text)

    def test_the_task_files_and_the_findings_are_not_unowned(self):
        self.merged()
        code, text = self.pipe.cf("harvest", self.run_id, "--from",
                                  str(self.pipe.entrypoint(self.phase)))
        self.assertEqual(code, 0, text)
        block = self.receipt()["informalize"]
        # `complete` set `status: complete` in each task file: tend's, not a task's output
        self.assertTrue(any(p.startswith("tasks/") for p in block["host_changed"]),
                        block["host_changed"])
        self.assertEqual(block["unowned"], [])

    def test_a_from_that_is_not_the_entrypoint_is_not_a_complete_harvest(self):
        self.merged()
        elsewhere = self.pipe.entrypoint(self.phase).parent
        code, text = self.pipe.cf("harvest", self.run_id, "--from", str(elsewhere), ok=False)
        self.assertNotEqual(code, 0)
        receipt = self.receipt()
        self.assertFalse(receipt["complete"])
        block = receipt["informalize"]
        self.assertEqual(block["expected"], [])
        self.assertEqual(block["absent"], block["owned"])
        self.assertFalse(block["ok"])
        self.assertIn("holds NONE of", text)


class TestTheAttackHarvest(PhaseCase):
    """A phase whose documents land as findings: the gate reads them against the base text."""

    phase = "attack"

    def prepare(self) -> None:
        pipe = self.pipe
        pipe.items()
        informal = pipe.repo / "informal"
        pipe.write_json(informal / "hypotheses.json", doc.HYPOTHESES)
        notation = informal / "notation.sty"
        marker = "% Standing hypotheses (one \\fcdefhyp per key of hypotheses.json).\n"
        notation.write_text(notation.read_text(encoding="utf-8").replace(
            marker, marker + doc.NOTATION_HYPOTHESES), encoding="utf-8")
        pipe.write(informal / "sections/01-gluing-spaces.tex", doc.SECTION_1)
        pipe.write(informal / "sections/02-covers.tex", doc.SECTION_2)
        pipe.git(pipe.repo, "add", "-A")
        pipe.git(pipe.repo, "commit", "-qm", "the conventions and the drafts")
        stages.write_receipt(pipe.paths, self.driver.ID, "I4", inputs={}, outputs={},
                             checks={"synthetic": True}, runs=["by hand"])

    def documents(self, quote: str) -> dict:
        finding = {"item": "1.2", "part": "b", "severity": "imprecise", "quote": quote,
                   "declarations": ["ExampleLib.glue_surjective"],
                   "what": "The text does not say that U and V are open in part (b).",
                   "lean_evidence": "glue_surjective binds hU : IsOpen U and hV : IsOpen V.",
                   "fix": "Name both hypotheses in part (b)."}
        return {"attack-lean-01": ("lean-first", "1", ["1.1", "1.2"], [finding]),
                "attack-tex-01": ("tex-first", "1", ["1.1", "1.2"], []),
                "attack-lean-02": ("lean-first", "2", ["2.1", "2.2", "2.3"], []),
                "attack-tex-02": ("tex-first", "2", ["2.1", "2.2", "2.3"], [])}

    def merged(self, quote: str) -> Path:
        """An entrypoint holding the documents the fleet would have merged."""
        entrypoint = self.pipe.made / "merged"
        for task, (direction, section, items, findings) in self.documents(quote).items():
            self.pipe.write_json(entrypoint / "findings" / task / "attack.json",
                                 self.pipe.attack_document(task, direction, section, items,
                                                           findings))
        return entrypoint

    def test_the_documents_land_and_the_run_s_own_gate_reads_them_at_home(self):
        code, text = self.pipe.cf("harvest", self.run_id, "--from",
                                  str(self.merged("the map induced by gluing is surjective")
                                      / "findings"))
        self.assertEqual(code, 0, text)
        corpus = self.pipe.paths.corpus_run_dir(self.run_id)
        receipt = json.loads((corpus / "RECEIPT.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["expected"]["count"], 4)
        self.assertTrue(receipt["complete"], receipt)
        self.assertTrue(receipt["gate"]["ok"], receipt["gate"]["problems"])
        self.assertEqual(receipt["gate"]["files"], 4)
        # the attack tasks own no file of the output repository: nothing but the
        # documents comes home, and an attack run changes no text
        self.assertEqual(receipt["informalize"]["owned"], [])
        self.assertEqual(receipt["gate"]["root"], str(self.run_dir / "repo"))
        self.assertEqual(receipt["gate"]["gate_code"]["script"], "verify_informal.py")

    def test_a_quote_that_is_not_the_base_text_s_fails_the_receipt(self):
        code, _text = self.pipe.cf("harvest", self.run_id, "--from",
                                   str(self.merged("the induced map is onto") / "findings"),
                                   ok=False)
        self.assertNotEqual(code, 0)
        receipt = json.loads((self.pipe.paths.corpus_run_dir(self.run_id) / "RECEIPT.json")
                             .read_text(encoding="utf-8"))
        self.assertFalse(receipt["complete"])
        self.assertTrue(any("[6]" in p for p in receipt["gate"]["problems"]),
                        receipt["gate"]["problems"])


class TestTheOwnedPaths(unittest.TestCase):
    """`informalize.paths` read without a workspace: what the harvest brings home."""

    def block(self, phase: str, paths: dict) -> dict:
        return {"informalize": {"id": "x", "phase": phase, "paths": paths}}

    def test_the_findings_are_left_to_the_findings_rsync(self):
        run = self.block("judge", {"judge-01": ["informal/sections/01-a.tex",
                                                "findings/judge-01/ruling.json"]})
        self.assertEqual(informal_files.owned_paths(run), ["informal/sections/01-a.tex"])
        self.assertTrue(informal_files.writes_findings(run))

    def test_a_path_that_leaves_the_entrypoint_is_not_fetched(self):
        run = self.block("draft", {"draft-01": ["informal/sections/01-a.tex",
                                                "../../etc/passwd", "/etc/passwd",
                                                "informal/../../x"]})
        self.assertEqual(informal_files.owned_paths(run), ["informal/sections/01-a.tex"])

    def test_a_phase_that_writes_no_document_is_harvested_from_the_entrypoint(self):
        run = self.block("draft", {"draft-01": ["informal/sections/01-a.tex"]})
        self.assertFalse(informal_files.writes_findings(run))
        source = {"kind": "remote", "host": "h", "path": "/srv/cf/run-1"}
        self.assertEqual(informal_files.entrypoint_of(source, run), "/srv/cf/run-1")
        # and from the entrypoint beside the findings directory, however it is named
        self.assertEqual(
            informal_files.entrypoint_of({**source, "path": "/srv/cf/run-1/findings"}, run),
            "/srv/cf/run-1")
        self.assertEqual(informal_files.entrypoint_of(
            {**source, "path": "/srv/cf/run-1/findings"}, self.block("attack", {})),
            "/srv/cf/run-1")

    def test_the_fetch_is_a_change_set_a_listing_and_one_rsync(self):
        run = self.block("draft", {"draft-01": ["informal/sections/01-a.tex",
                                                "informal/sections/02-b.tex"]})
        steps = informal_files.list_commands({"kind": "remote", "host": "h"}, "/srv/cf/r",
                                             informal_files.owned_paths(run), Path("/tmp/t"))
        self.assertEqual(len(steps), 3, [s["title"] for s in steps])
        self.assertIn("git diff --name-only", steps[0]["shell"])
        self.assertIn("shasum -a 256", steps[1]["shell"])
        # one transfer for the whole owned set, its names on stdin: not one ssh per path
        self.assertEqual(steps[2]["shell"].count("rsync"), 1)
        self.assertIn("--files-from=-", steps[2]["shell"])
        self.assertEqual(steps[2]["stdin"],
                         "informal/sections/01-a.tex\ninformal/sections/02-b.tex\n")

    def test_a_change_set_is_asked_for_even_when_the_tasks_own_no_file(self):
        steps = informal_files.list_commands({"kind": "remote", "host": "h"}, "/srv/cf/r",
                                             [], Path("/tmp/t"))
        self.assertEqual(len(steps), 1)
        self.assertIn("git diff --name-only", steps[0]["shell"])

    def test_only_findings_at_the_end_of_a_from_is_stripped(self):
        run = self.block("judge", {"judge-01": ["findings/judge-01/ruling.json"]})
        source = {"kind": "remote", "host": "h", "path": "/srv/cf/run-1/findings"}
        self.assertEqual(informal_files.entrypoint_of(source, run), "/srv/cf/run-1")
        # a judge run whose fleet merged no document has no findings/ to name, and
        # --from <entrypoint> means the entrypoint, not its parent
        self.assertEqual(informal_files.entrypoint_of({**source, "path": "/srv/cf/run-1"}, run),
                         "/srv/cf/run-1")

    def test_what_the_entrypoint_changed_outside_a_task_is_unowned(self):
        owned = ["informal/items.json"]
        changed = ["findings/attack-01/attack.json", "informal/items.json",
                   "informal/preamble.tex", "tasks/items.yaml"]
        self.assertEqual(informal_files.unowned(changed, owned), ["informal/preamble.tex"])

    def test_a_file_that_did_not_land_as_the_host_holds_it_is_not_complete(self):
        receipt = harvest_mod.build_receipt(
            run_id="r", source={"kind": "local", "path": "/x"}, expected=[], dest=Path("/x"),
            informalize={"ok": False, "missing": ["informal/items.json"], "owned": [],
                         "paths": {}, "changed": []})
        self.assertFalse(receipt["complete"])
        self.assertEqual(receipt["informalize"]["missing"], ["informal/items.json"])


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
