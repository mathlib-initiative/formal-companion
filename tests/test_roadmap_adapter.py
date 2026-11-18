"""The `roadmap_locators` adapter: a roadmap's pages as rows of the manuscript's statements.

New code, testing scripts/adapters/roadmap_locators.py and what registers it
(references/onboarding-contract.md section 1).  The companion is
tests/_roadmap_fixture.py's synthetic one: a manuscript of twenty-four lines
with one lemma, three locator files and five pages.  Nothing here needs
poppler, TeX or a network; the last class runs `cf onboard` on it in a
temporary workspace.
"""
from __future__ import annotations

# The first import, before anything reads HOME (see tests/_hermetic.py).
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _roadmap_fixture as fx                                      # noqa: E402
import cf as cf_main                                               # noqa: E402
from adapters import api as adapters_api                           # noqa: E402
from adapters import roadmap_locators                              # noqa: E402
from adapters.base import validate_rows                            # noqa: E402
from cflib import vocab                                            # noqa: E402
from cflib.adapters_api import DRY_RUN_REQUIRED_KEYS               # noqa: E402
from cflib.errors import AdapterError                              # noqa: E402

SHA = "a" * 64
#: the manuscript with LaTeX's numbering recoverable: the lemma is Lemma 1.1
NUMBERED = fx.SOURCE.replace("\\documentclass{article}",
                             "\\documentclass{article}\\newtheorem{lemma}{Lemma}[section]", 1)
LINES = "../../sources/notes-locators.md"


def scratch(case: unittest.TestCase) -> Path:
    root = Path(tempfile.mkdtemp(prefix="cf-roadmap-adapter-", dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, root, ignore_errors=True)
    return root


def repository(case: unittest.TestCase, files: dict | None = None) -> Path:
    """The companion's files in a directory: an adapter reads a working tree."""
    repo = scratch(case) / "pebbles"
    written = fx.repository_files(None)
    written.update(files or {})
    fx.write_repository(repo, written)
    return repo


def adapter(repo: Path, **config):
    return adapters_api.from_config({"adapter": "roadmap_locators", "name": "pebbles", "repo": str(repo),
                                     "sources": {"default": "s"}, **config})


class TestDetection(unittest.TestCase):
    def test_the_kind_is_registered_and_implemented(self):
        self.assertIs(adapters_api.registry()["roadmap_locators"], roadmap_locators.RoadmapLocatorsAdapter)
        self.assertIn("roadmap_locators", vocab.IMPLEMENTED_ADAPTER_KINDS)
        self.assertIn("roadmap_locators", vocab.ADAPTER_KINDS)

    def test_it_scores_the_pages_that_cite_the_manuscript(self):
        score, evidence = roadmap_locators.RoadmapLocatorsAdapter.detect(repository(self))
        self.assertEqual(score, 0.95)
        self.assertEqual({k: evidence[k] for k in ("pages", "pages_with_lean", "pages_citing_manuscript",
                                                   "tex", "locator_files")},
                         {"pages": 5, "pages_with_lean": 4, "pages_citing_manuscript": 4,
                          "tex": fx.TEX_NAME, "locator_files": 2})
        self.assertIn("4 of 4 roadmap pages", evidence["reason"])

    def test_a_roadmap_that_mostly_cites_notes_scores_low(self):
        notes = "../../sources/project-notes.md#sketch"
        files = {f"blueprint/roadmap/notes/n{i}.md": fx.page(f"Note {i}", lean=f"Pebbles.n{i}", origin="bridged",
                                                             sources=[notes]) for i in range(12)}
        score, evidence = roadmap_locators.RoadmapLocatorsAdapter.detect(repository(self, files))
        self.assertEqual((evidence["pages_with_lean"], evidence["pages_citing_manuscript"]), (16, 4))
        self.assertEqual(score, round(0.6 + 0.35 * 4 / 16, 4))

    def test_a_repo_without_the_convention_scores_nothing_and_says_what_was_looked_for(self):
        empty = scratch(self)
        self.assertEqual(roadmap_locators.RoadmapLocatorsAdapter.detect(empty), (0.0, {}))
        self.assertIn("no blueprint/roadmap/**/*.md", roadmap_locators.RoadmapLocatorsAdapter.explain(empty)[0])
        notes_only = repository(self, {"blueprint/sources/notes-locators.md": fx.LOCATOR_NOTES,
                                       "blueprint/sources/labels.md": None})
        self.assertEqual(roadmap_locators.RoadmapLocatorsAdapter.detect(notes_only), (0.0, {}))
        self.assertIn("0 of them citing", roadmap_locators.RoadmapLocatorsAdapter.explain(notes_only)[0])

    def test_locators_of_two_manuscripts_are_not_one_companion(self):
        two = repository(self, {"blueprint/sources/labels.md": fx.LOCATOR_LABELS.replace(fx.TEX_NAME, "other.tex")})
        self.assertEqual(roadmap_locators.RoadmapLocatorsAdapter.detect(two), (0.0, {}))
        self.assertIn("read 2 TeX files", roadmap_locators.RoadmapLocatorsAdapter.explain(two)[0])
        with self.assertRaises(AdapterError):
            list(adapter(two).extract(sources={"s": SHA}))


class TestRows(unittest.TestCase):
    def rows(self, files: dict | None = None, **config) -> list[dict]:
        return list(adapter(repository(self, files), **config).extract(sources={"s": SHA}))

    def test_a_cited_page_gives_its_declarations_to_the_statement_it_cites(self):
        rows = self.rows()
        self.assertEqual([(r["item"], r["decl"]) for r in rows],
                         [("lem:heaps", "Pebbles.heaviest_exists"), ("lem:heaps", "Pebbles.Heap")])
        row = rows[0]
        self.assertEqual(row["statement"], "A nonempty heap of pebbles has a heaviest member.")
        self.assertEqual((row["file"], row["line"], row["item_kind"]), (fx.TEX_NAME, 8, "Lemma"))
        self.assertEqual((row["source_id"], row["source_sha"], row["repo"]), ("s", SHA, "pebbles"))
        self.assertEqual((row["page_anchor"], row["anchor_source"], row["provenance"]), (None, "none", "blueprint"))
        self.assertEqual(validate_rows(rows), [])

    def test_the_item_is_latexs_number_when_the_preamble_gives_it(self):
        rows = self.rows({fx.TEX_NAME: NUMBERED})
        self.assertEqual({r["item"] for r in rows}, {"Lemma 1.1"})
        self.assertEqual({r["item_raw"] for r in rows}, {"lem:heaps"})

    def test_a_proof_the_roadmap_does_not_claim_is_a_gap(self):
        self.assertEqual({(r["sorry"], r["sorry_class"]) for r in self.rows()}, {(True, "gap")})
        proved = fx.page("A heaviest member", lean="Pebbles.heaviest_exists", origin="cited",
                         sources=[f"{LINES}#heaps-lemma"]).replace("statement: formalized",
                                                                    "statement: formalized\nproof: formalized")
        rows = self.rows({"blueprint/roadmap/heaps/heaviest.md": proved})
        self.assertEqual({(r["sorry"], r["sorry_class"]) for r in rows}, {(False, "none")})

    def test_only_a_page_that_states_the_source_gives_rows(self):
        # the bridge and the retracted page cite the manuscript too, and state something else
        bridge = fx.page("A bridge", lean="Pebbles.weigh_le", origin="bridged", sources=[f"{LINES}#heaps-lemma"])
        unstated = fx.page("Unstated", lean="Pebbles.Colour", origin="cited", statement=None,
                           sources=[f"{LINES}#heaps-lemma"])
        rows = self.rows({"blueprint/roadmap/heaps/bridge.md": bridge,
                          "blueprint/roadmap/heaps/unstated.md": unstated})
        self.assertEqual({r["decl"] for r in rows}, {"Pebbles.heaviest_exists", "Pebbles.Heap"})

    def test_a_passage_inside_the_statement_counts_and_one_that_only_touches_it_does_not(self):
        def locator(lines: str) -> str:
            return fx.locator_lines(None).replace("`lem:heaps`, lines 8-11:", f"`lem:heaps`, lines {lines}:")

        only = {"blueprint/roadmap/heaps/heaviest.md": fx.page(
            "A heaviest member", lean="Pebbles.heaviest_exists", origin="cited", sources=[f"{LINES}#heaps-lemma"])}
        inside = self.rows({**only, "blueprint/sources/notes-locators.md": locator("9-10")})
        self.assertEqual([r["item"] for r in inside], ["lem:heaps"])
        touching = self.rows({**only, "blueprint/sources/notes-locators.md": locator("10-13")})
        self.assertEqual(touching, [])
        around = self.rows({**only, "blueprint/sources/notes-locators.md": locator("4-19")})
        self.assertEqual([r["item"] for r in around], ["lem:heaps"])

    def test_a_statement_of_the_appendix_is_keyed_on_its_label_and_so_is_the_table(self):
        tex = "\n".join([
            "\\documentclass{article}\\newtheorem{lemma}{Lemma}[section]",       # 1
            "\\begin{document}",                                                 # 2
            "\\section{Heaps}",                                                  # 3
            "\\begin{lemma}\\label{lem:first}",                                  # 4
            "Every heap has a top.",                                             # 5
            "\\end{lemma}",                                                      # 6
            "\\appendix",                                                        # 7
            "\\section{Sand}",                                                   # 8
            "\\begin{lemma}\\label{lem:sand}",                                   # 9
            "Sand has no heaviest grain.",                                       # 10
            "\\end{lemma}",                                                      # 11
            "\\end{document}", ""])
        locators = "\n".join(["# Locators", "", f"Line ranges refer to `{fx.TEX_NAME}`.", "",
                              "## First", "", "lines 4-6: the top.", "", "## Sand", "", "lines 9-11: the sand.", ""])
        base = {fx.TEX_NAME: tex, "blueprint/sources/notes-locators.md": locators,
                "blueprint/sources/labels.md": None, "blueprint/roadmap/heaps/weighing.md": None,
                "blueprint/roadmap/heaps/colours.md": None, "blueprint/roadmap/heaps/unnamed.md": None,
                "blueprint/roadmap/heaps/withdrawn.md": None,
                "blueprint/roadmap/heaps/heaviest.md": fx.page("First", lean="Pebbles.Heap", origin="cited",
                                                               sources=[f"{LINES}#first"])}
        self.assertEqual({r["item"] for r in self.rows(base)}, {"Lemma 1.1"})
        sand = fx.page("Sand", lean="Pebbles.Colour", origin="cited", sources=[f"{LINES}#sand"])
        both = self.rows({**base, "blueprint/roadmap/heaps/sand.md": sand})
        self.assertEqual({r["item"] for r in both}, {"lem:first", "lem:sand"})

    def test_a_name_that_is_no_declaration_is_rejected_not_written(self):
        garbled = fx.page("Garbled", lean="Pebbles.Heap, not a name", origin="cited",
                          sources=[f"{LINES}#heaps-lemma"])
        made = adapter(repository(self, {"blueprint/roadmap/heaps/heaviest.md": garbled}))
        report = made.dry_run(sources={"s": SHA})
        self.assertEqual((report["rejected"], report["rejects"][0]["raw"]), (1, "not a name"))
        self.assertEqual({r["decl"] for r in made.extract(sources={"s": SHA})}, {"Pebbles.Heap"})


class TestTheReportAndTheSource(unittest.TestCase):
    def test_the_dry_run_counts_statements_pages_and_passages(self):
        report = adapter(repository(self)).dry_run(sources={"s": SHA})
        for key in DRY_RUN_REQUIRED_KEYS:
            self.assertIn(key, report)
        self.assertEqual({k: report[k] for k in ("adapter", "statements", "tagged", "untagged", "rows", "items",
                                                 "declarations", "locator", "rejected")},
                         {"adapter": "roadmap_locators", "statements": 1, "tagged": 1, "untagged": 0, "rows": 2,
                          "items": 1, "declarations": 2, "locator": "label", "rejected": 0})
        self.assertEqual({k: report[k] for k in ("pages", "pages_with_lean", "pages_cited", "pages_not_cited",
                                                 "passages", "passages_outside_statements")},
                         {"pages": 5, "pages_with_lean": 4, "pages_cited": 2, "pages_not_cited": 2,
                          "passages": 3, "passages_outside_statements": 1})

    def test_the_manuscript_is_the_source_it_claims(self):
        titled = fx.SOURCE.replace("\\begin{document}", "\\title{Pebbles in $\\mathbb{R}$ and heaps}\\begin{document}", 1)
        repo = repository(self, {fx.TEX_NAME: titled})
        claim = adapter(repo).manuscript_claim()
        self.assertEqual((claim.title, claim.edition, claim.path), ("Pebbles in R and heaps", "manuscript", fx.TEX_NAME))
        self.assertEqual(claim.files, (str(repo / fx.TEX_NAME),))
        self.assertEqual(claim.kinds, ("Lemma",))
        self.assertEqual(roadmap_locators.manuscript_title("\\title{Unclosed"), "")

    def test_the_manuscript_may_be_named_in_adapter_yaml(self):
        moved = repository(self, {fx.TEX_NAME: None, "paper/notes.tex": fx.SOURCE})
        with self.assertRaises(AdapterError) as caught:
            list(adapter(moved).extract(sources={"s": SHA}))
        self.assertIn("roadmap: {tex:", caught.exception.hint)
        rows = list(adapter(moved, roadmap={"tex": "paper/notes.tex"}).extract(sources={"s": SHA}))
        self.assertEqual({r["file"] for r in rows}, {"paper/notes.tex"})
        with self.assertRaises(AdapterError):
            list(adapter(moved, roadmap={"tex": "../notes.tex"}).extract(sources={"s": SHA}))

    def test_the_key_moves_when_a_page_or_the_manuscript_does(self):
        repo = repository(self)
        before = adapter(repo).key_inputs()
        self.assertEqual(before["roadmap"], 10)                # 4 files under sources/, 6 under roadmap/
        (repo / "blueprint/roadmap/heaps/heaviest.md").write_text(
            fx.page("A heaviest member", lean="Pebbles.Heap", origin="cited", sources=[f"{LINES}#heaps-lemma"]),
            encoding="utf-8")
        after_page = adapter(repo).key_inputs()
        self.assertNotEqual(before["roadmap_sha"], after_page["roadmap_sha"])
        self.assertEqual(before["sources_sha"], after_page["sources_sha"])
        (repo / fx.TEX_NAME).write_text(NUMBERED, encoding="utf-8")
        self.assertNotEqual(after_page["sources_sha"], adapter(repo).key_inputs()["sources_sha"])


class TestOnboarding(unittest.TestCase):
    """`cf onboard` finds the convention and extracts it, with nothing typed in."""

    def cli(self, *args: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cf_main.cli(list(args))
        return code, out.getvalue() + err.getvalue()

    def test_it_detects_the_roadmap_and_writes_the_statement_table(self):
        repo = repository(self, {fx.TEX_NAME: NUMBERED})
        env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.invalid", GIT_COMMITTER_NAME="T",
                   GIT_COMMITTER_EMAIL="t@e.invalid", GIT_CONFIG_NOSYSTEM="1")
        for argv in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "the companion"]):
            subprocess.run(["git", "-C", str(repo), *argv], check=True, env=env, capture_output=True)
        workspace = scratch(self) / "ws"
        code, text = self.cli("init", str(workspace))
        self.assertEqual(code, 0, text)
        code, text = self.cli("--workspace", str(workspace), "onboard", str(repo), "--no-errata")
        self.assertEqual(code, 0, text)
        self.assertIn("roadmap_locators", text)
        table = workspace / "companions" / "pebbles" / "statements.jsonl"
        rows = [json.loads(line) for line in table.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = [row for row in rows if "item" in row]
        self.assertEqual([(r["item"], r["decl"]) for r in rows],
                         [("Lemma 1.1", "Pebbles.heaviest_exists"), ("Lemma 1.1", "Pebbles.Heap")])
        config = (workspace / "companions" / "pebbles" / "adapter.yaml").read_text(encoding="utf-8")
        self.assertIn("adapter: roadmap_locators", config)
        self.assertIn("blueprint/roadmap/**/*.md", config)


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main()
