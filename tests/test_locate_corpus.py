"""The locate corpus package: the host probe drivers, universes, ingest, corpus.sqlite.

New code, for docs/proposals/wikipedia-locate.md sections 2.3-2.5 and 6 and
references/locate-documents.md section 8.  Everything runs on synthetic
probe output written into temporary directories -- libraries ``alpha`` and
``beta``, made-up declarations -- because the suite has no Lean: what is
tested is what ingest decides from the records a probe writes, what the
database holds, and what the two host tools print.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main  # noqa: E402
from cflib import gitsafe, vocab  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from corpus import build_db, corpus_search, ingest, probe_merge, query, universe_host  # noqa: E402
from corpus.universe import Universe, lean_version, universe_sha256  # noqa: E402

TEMPLATE = SKILL / "templates" / "corpus" / "probe.lean.tmpl"
TOOLCHAIN = "leanprover/lean4:v4.34.0"
ALPHA = "a" * 40
BETA = "b" * 40


def _scratch(case) -> Path:
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def decl(name: str, kind: str = "theorem", status: str = "proved", **kw) -> dict:
    """One declaration record in the probe's shape (scripts/corpus/probe_merge.py)."""
    record = {"record": "declaration", "name": name, "module": "Alpha.Widget", "path": None,
              "kind": kind, "line": 10, "start_line": 9, "end_line": 12, "signature": "True",
              "docstring": None, "mentions": [], "private": False, "wrapper": None,
              "proof_status": status, "nonstandard_axioms": [], "deprecated": None,
              "truncated": False, "truncated_fields": [], "errors": []}
    record.update(kw)
    return record


def write_probe(directory: Path, decls: list[dict], *, library: str = "alpha",
                commit: str | None = ALPHA, toolchain: str = "4.34.0",
                modules: tuple = (("Alpha.Widget", "Alpha/Widget.lean"),),
                schema: str = "formal-companion-library-index/v0") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    header = {"record": "header", "schema": schema, "library": library,
              "toolchain": toolchain, "probe_revision": 1}
    lines = [header] + [{"record": "module", "name": n, "path": p, "module_doc": None,
                         "truncated": False} for n, p in modules] + decls
    (directory / "index.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines),
                                           encoding="utf-8")
    manifest = {"library": library, "template_sha256": "0" * 64}
    if commit:
        manifest["commit"] = commit
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def canary_records(*, hidden_sorry_seen: bool = True) -> list[dict]:
    return [
        decl("CfCanary.hiddenSorry", "theorem", "statement_only" if hidden_sorry_seen else "proved",
             nonstandard_axioms=["sorryAx"] if hidden_sorry_seen else [], module="CfCanary.Basic"),
        decl("CfCanary.wantedTheorem", "theorem", "wanted", wrapper="CfCanary.TheoremWanted",
             module="CfCanary.Basic"),
        decl("CfCanary.propDef", "def", "definition", signature="Prop", module="CfCanary.Basic"),
        decl("CfCanary.axiomTheorem", "theorem", "proved",
             nonstandard_axioms=["CfCanary.canaryAxiom"], module="CfCanary.Basic"),
        decl("CfCanary.canaryAxiom", "axiom", "axiom", nonstandard_axioms=["CfCanary.canaryAxiom"],
             module="CfCanary.Basic"),
    ]


def alpha_records() -> list[dict]:
    return [
        decl("Widget.exists_spanning_gadget", signature="∀ (w : Widget), ∃ g, g.Spans w",
             docstring="The widget lemma (https://en.wikipedia.org/wiki/Widget_lemma).",
             line=11, start_line=10),
        decl("Widget.Spans", "def", "definition", signature="Widget → Gadget → Prop",
             line=21, start_line=21),
        decl("Widget.sorried", status="statement_only", nonstandard_axioms=["sorryAx"],
             line=31, start_line=31),
        decl("Widget.bigAxiomThm", nonstandard_axioms=["Alpha.bigAxiom"], line=41, start_line=41),
        decl("Gadget", "structure", "definition", signature="Type", line=51, start_line=51),
    ]


def make_universe(root: Path, *, canary: bool = True, libs=("alpha",)) -> Universe:
    path = root / "corpora" / "u1" / "universe.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    universe = Universe.create(path, "u1", "2026-10-17")
    for lib in libs:
        universe.add_library(library_id=lib, repo=f"https://example.org/{lib}",
                             commit=ALPHA if lib == "alpha" else BETA, toolchain=TOOLCHAIN,
                             roots=[lib.capitalize()])
    if canary:
        universe.record_canary(toolchain="4.34.0", passed=True, cases={}, index_sha256="0" * 64,
                               checked="2026-10-17T00:00:00Z")
    universe.save()
    return Universe.load(path)


class TestTheHostDriversPinGit(unittest.TestCase):

    def test_the_inlined_pins_are_gitsafes_own(self):
        for module in (probe_merge, universe_host):
            with self.subTest(module=module.__name__):
                self.assertEqual(module._GIT_SAFE_CONFIG, gitsafe.SAFE_CONFIG)
                self.assertEqual(module._GIT_SAFE_ENV, gitsafe.SAFE_ENV)

    def test_the_host_side_files_import_no_cflib(self):
        for name in ("probe_merge.py", "universe_host.py", "corpus_search.py", "corpus_show.py"):
            with self.subTest(file=name):
                text = (SCRIPTS / "corpus" / name).read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"^\s*(from|import)\s+cflib", text, re.M))


class TestTheTemplateEmitsWhatIngestReads(unittest.TestCase):
    """Ingest re-decides from the probe's fields; the template must keep emitting them."""

    def test_every_field_ingest_reads_is_emitted(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        for field in ("name", "module", "path", "kind", "line", "start_line", "end_line",
                      "signature", "docstring", "wrapper", "proof_status", "nonstandard_axioms",
                      "deprecated", "truncated_fields", "errors"):
            with self.subTest(field=field):
                self.assertIn(f'("{field}",', text)
        self.assertIn('def schema : String := "formal-companion-library-index/v0"', text)

    def test_the_closure_keeps_sorry_and_drops_only_the_standard_three(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        standard = re.search(r"def isStandardAxiom.*?:=\s*(.*?)\n\n", text, re.S).group(1)
        self.assertEqual(set(re.findall(r"``([\w.]+)", standard)), set(vocab.STANDARD_AXIOMS))
        self.assertIn("usesSorry := axioms.contains ``sorryAx", text)
        # an isProp failure is recorded, which is what lets ingest refuse to call it proved
        self.assertIn('attempt "isProp" errs', text)

    def test_the_probe_decides_in_another_order_than_section_2_4(self):
        """The reason ingest re-decides: the template's order, as it stands."""
        text = TEMPLATE.read_text(encoding="utf-8")
        block = text[text.index("let proofStatus :="):text.index("let tc ←")]
        order = re.findall(r'"(axiom|wanted|statement_only|definition|proved)"', block)
        self.assertEqual(order, ["axiom", "axiom", "wanted", "statement_only", "definition",
                                 "proved"])
        self.assertNotIn("axiom_dependent", block)


class TestProofStatusIsReDecided(unittest.TestCase):

    def decide(self, *args, **kw):
        return ingest.decide(decl(*args, **kw))

    def test_the_canarys_four_cases(self):
        for record in canary_records():
            want = ingest.CANARY.get(record["name"])
            if want is None:
                continue
            status, statement, flags = ingest.decide(record)
            with self.subTest(name=record["name"]):
                self.assertEqual((status, statement), (want["proof_status"], want["statement"]))
                self.assertEqual(flags, [])

    def test_an_axiom_is_in_its_own_closure(self):
        self.assertEqual(self.decide("Alpha.bigAxiom", "axiom", "axiom")[0],
                         vocab.PROOF_AXIOM_DEPENDENT)
        self.assertEqual(self.decide("propext", "axiom", "axiom")[0], vocab.PROOF_PROVED)
        self.assertEqual(self.decide("Quot.sound", "axiom", "axiom")[0], vocab.PROOF_PROVED)
        self.assertEqual(self.decide("Classical.choice", "axiom", "axiom")[0],
                         vocab.PROOF_DEFINITION)

    def test_the_section_2_4_order(self):
        # wanted before sorry, sorry before another axiom, both before the type
        self.assertEqual(self.decide("x", wrapper="W.ProofWanted", status="wanted",
                                     nonstandard_axioms=["sorryAx"])[0], vocab.PROOF_WANTED)
        self.assertEqual(self.decide("x", status="statement_only",
                                     nonstandard_axioms=["sorryAx", "Alpha.bigAxiom"])[0],
                         vocab.PROOF_STATEMENT_ONLY)
        self.assertEqual(self.decide("x", nonstandard_axioms=["Alpha.bigAxiom"])[0],
                         vocab.PROOF_AXIOM_DEPENDENT)
        self.assertEqual(self.decide("x", "def", "definition", signature="ℕ",
                                     nonstandard_axioms=["Alpha.bigAxiom"])[0],
                         vocab.PROOF_AXIOM_DEPENDENT)
        self.assertEqual(self.decide("x")[0], vocab.PROOF_PROVED)
        self.assertEqual(self.decide("x", "def", "definition", signature="ℕ")[0],
                         vocab.PROOF_DEFINITION)
        self.assertEqual(self.decide("x", "inductive", "definition")[0], vocab.PROOF_DEFINITION)

    def test_a_failed_is_prop_is_never_proved(self):
        status, statement, flags = self.decide("x", "def", "proved",
                                               errors=["isProp: maximum recursion depth"])
        self.assertEqual(status, vocab.PROOF_STATEMENT_ONLY)
        self.assertEqual(flags, ["is_prop_unknown"])
        self.assertFalse(statement)
        # when the closure decides first the flag is not needed
        status, _s, flags = self.decide("x", "def", "statement_only", nonstandard_axioms=["sorryAx"],
                                        errors=["isProp: timeout"])
        self.assertEqual((status, flags), (vocab.PROOF_STATEMENT_ONLY, []))
        # a theorem's type is a proposition whatever the probe's isProp said
        self.assertEqual(self.decide("x", errors=["isProp: timeout"])[0], vocab.PROOF_PROVED)

    def test_the_statement_flag_is_a_definition_ending_in_prop(self):
        cases = {("def", "Prop"): True, ("def", "ℕ → Prop"): True,
                 ("abbrev", "(n : ℕ) → Fin n → Prop"): True,
                 ("opaque", "{α : Type} → α → Prop"): True,
                 ("def", "Set ℕ"): False, ("def", "(Prop → ℕ)"): False,
                 ("def", "ℕ → (ℕ → Prop) → ℕ"): False, ("theorem", "Prop"): False,
                 ("instance", "Prop"): False, ("def", None): False}
        for (kind, signature), want in cases.items():
            with self.subTest(kind=kind, signature=signature):
                self.assertEqual(ingest.statement_flag(kind, signature), want)

    def test_the_flag_never_sits_on_a_proved_declaration(self):
        status, statement, _f = self.decide("x", "def", "proved", signature="Prop")
        self.assertEqual(status, vocab.PROOF_PROVED)
        self.assertFalse(statement)
        # and what it decides is a pair derive_status accepts
        vocab.derive_status("theorem", [{"relation": "exactMatch", "proof_status": status,
                                         "statement": statement}], absent=False)


class TestTheUniverse(unittest.TestCase):

    def setUp(self):
        self.root = _scratch(self)

    def test_the_sha_is_the_section_8_definition(self):
        libs = [{"id": "beta", "commit": BETA, "index": {"sha256": "2" * 64}},
                {"id": "alpha", "commit": ALPHA, "index": {"sha256": "1" * 64}}]
        lines = f"alpha\t{ALPHA}\t{'1' * 64}\nbeta\t{BETA}\t{'2' * 64}"
        self.assertEqual(universe_sha256(libs), hashlib.sha256(lines.encode()).hexdigest())
        self.assertNotEqual(universe_sha256(libs[:1]), universe_sha256(libs))

    def test_round_trip_and_the_written_keys(self):
        universe = make_universe(self.root, libs=("alpha", "beta"))
        data = universe.data
        self.assertEqual(set(data), {"id", "frozen", "libraries", "excluded", "canaries",
                                     "corpus_db", "universe_sha256"})
        self.assertEqual([lib["id"] for lib in data["libraries"]], ["alpha", "beta"])
        self.assertEqual(data["universe_sha256"], universe.sha256())
        self.assertEqual(set(data["libraries"][0]), {
            "id", "brain_lib", "repo", "commit", "toolchain", "roots", "licence", "conventions",
            "index"})

    def test_add_is_idempotent_and_a_new_pin_is_refused(self):
        universe = make_universe(self.root)
        again = universe.add_library(library_id="alpha", repo="https://example.org/alpha",
                                     commit=ALPHA, toolchain=TOOLCHAIN, roots=["Alpha"])
        self.assertEqual(again["commit"], ALPHA)
        self.assertEqual(len(universe.libraries), 1)
        with self.assertRaises(CfError):
            universe.add_library(library_id="alpha", repo="https://example.org/alpha",
                                 commit="c" * 40, toolchain=TOOLCHAIN, roots=["Alpha"])
        for bad in ({"commit": "abc"}, {"repo": "http://x"}, {"roots": []},
                    {"library_id": "Alpha!"}):
            fields = dict(library_id="gamma", repo="https://example.org/g", commit="c" * 40,
                          toolchain=TOOLCHAIN, roots=["G"])
            fields.update(bad)
            with self.subTest(bad=bad), self.assertRaises(CfError):
                universe.add_library(**fields)

    def test_an_exclusion_names_its_reason_and_drops_the_row(self):
        universe = make_universe(self.root, libs=("alpha", "beta"))
        universe.exclude("beta", "the build failed at the pin")
        self.assertEqual(universe.library_ids(), ["alpha"])
        self.assertEqual(universe.excluded,
                         [{"id": "beta", "reason": "the build failed at the pin"}])
        with self.assertRaises(CfError):
            universe.library("beta")
        with self.assertRaises(CfError):
            universe.exclude("beta", "  ")
        with self.assertRaises(CfError):
            universe.add_library(library_id="beta", repo="https://example.org/beta", commit=BETA,
                                 toolchain=TOOLCHAIN, roots=["Beta"])

    def test_brain_lib_tokens_are_unique_per_repository(self):
        universe = make_universe(self.root)
        with self.assertRaises(CfError):
            universe.add_library(library_id="beta", repo="https://example.org/beta", commit=BETA,
                                 toolchain=TOOLCHAIN, roots=["Beta"], brain_lib="alpha")

    def test_canaries_match_every_spelling_of_a_toolchain(self):
        self.assertEqual(lean_version("leanprover/lean4:v4.34.0-rc2"), "4.34.0-rc2")
        self.assertEqual(lean_version("v4.34.0"), "4.34.0")
        universe = make_universe(self.root)
        self.assertTrue(universe.canary_passed("leanprover/lean4:v4.34.0"))
        self.assertFalse(universe.canary_passed("leanprover/lean4:v4.36.0"))

    def test_a_new_index_makes_the_database_stale(self):
        universe = make_universe(self.root)
        universe.set_index("alpha", path="index/alpha/", sha256="1" * 64, declarations=3)
        universe.set_corpus_db(sha256="2" * 64, built="t", declarations=3)
        universe.set_index("alpha", path="index/alpha/", sha256="1" * 64, declarations=3)
        self.assertIsNotNone(universe.corpus_db)
        universe.set_index("alpha", path="index/alpha/", sha256="3" * 64, declarations=3)
        self.assertIsNone(universe.corpus_db)

    def test_an_invalid_file_is_refused_on_load(self):
        path = self.root / "universe.yaml"
        path.write_text("id: u1\nlibraries:\n  - id: alpha\n    commit: nope\n", encoding="utf-8")
        with self.assertRaises(CfError):
            Universe.load(path)


class TestIngest(unittest.TestCase):

    def setUp(self):
        self.root = _scratch(self)
        self.universe = make_universe(self.root, libs=("alpha", "beta"))
        self.out = self.root / "corpora" / "u1" / "index" / "alpha"

    def run_ingest(self, records, checkout=None, **kw):
        source = write_probe(self.root / "probe", records, **kw)
        return ingest.ingest(self.universe, "alpha", source, self.out, checkout=checkout)

    def decls(self) -> list[dict]:
        lines = (self.out / "decls.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def test_the_normalised_index_and_its_manifest(self):
        manifest = self.run_ingest(alpha_records())
        rows = self.decls()
        self.assertEqual([r["name"] for r in rows], sorted(r["name"] for r in rows))
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["Widget.bigAxiomThm"]["proof_status"], "axiom_dependent")
        self.assertEqual(by_name["Widget.bigAxiomThm"]["probe_proof_status"], "proved")
        self.assertTrue(by_name["Widget.Spans"]["statement"])
        self.assertEqual(by_name["Widget.Spans"]["path"], "Alpha/Widget.lean")
        self.assertEqual(manifest["schema"], "cf-library-index/1")
        self.assertEqual(manifest["declarations"], 5)
        self.assertEqual(manifest["sha256"],
                         hashlib.sha256((self.out / "decls.jsonl").read_bytes()).hexdigest())
        self.assertEqual(manifest["by_proof_status"], {"axiom_dependent": 1, "definition": 2,
                                                       "proved": 1, "statement_only": 1})
        on_disk = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["sha256"], manifest["sha256"])
        lib = self.universe.library("alpha")
        self.assertEqual(lib["index"], {"path": "index/alpha/", "sha256": manifest["sha256"],
                                        "declarations": 5})
        for row in rows:
            self.assertEqual(set(row), {"name", "module", "path", "line", "start_line",
                                        "end_line", "kind", "proof_status", "statement",
                                        "printable", "signature", "docstring", "axioms", "tags",
                                        "deprecated", "probe_proof_status", "flags"})

    def test_an_unsorted_input_is_sorted_through_chunks(self):
        records = [decl(f"N.d{i:03d}") for i in (5, 3, 9, 1, 7, 2, 8, 0, 6, 4)]
        with mock.patch.object(ingest, "CHUNK_RECORDS", 3):
            self.run_ingest(records)
        self.assertEqual([r["name"] for r in self.decls()], [f"N.d{i:03d}" for i in range(10)])
        # no chunk file is left behind
        self.assertEqual(sorted(p.name for p in self.out.parent.iterdir()), ["alpha"])

    def test_a_repeated_name_is_refused_sorted_or_not(self):
        for records in ([decl("A.x"), decl("A.x")], [decl("B.y"), decl("A.x"), decl("A.x")]):
            with self.subTest(records=[r["name"] for r in records]):
                with self.assertRaises(CfError) as caught:
                    self.run_ingest(records)
                self.assertIn("two declarations named A.x", str(caught.exception))
        self.assertFalse(self.out.exists())

    def test_the_wrong_library_commit_or_toolchain_is_refused(self):
        for kw in ({"commit": "c" * 40}, {"commit": None}, {"library": "beta"},
                   {"toolchain": "4.36.0"}):
            with self.subTest(kw=kw), self.assertRaises(CfError):
                self.run_ingest(alpha_records(), **kw)
        with self.assertRaises(CfError):
            ingest.ingest(self.universe, "gamma", self.root, self.out)

    def test_no_library_is_ingested_before_its_toolchain_canary_passed(self):
        self.universe.data["canaries"] = []
        with self.assertRaises(CfError) as caught:
            self.run_ingest(alpha_records())
        self.assertIn("canary", str(caught.exception))
        self.universe.record_canary(toolchain="4.34.0", passed=False, cases={},
                                    index_sha256="0" * 64, checked="t")
        with self.assertRaises(CfError):
            self.run_ingest(alpha_records())

    def test_a_record_ingest_cannot_decide_is_refused_with_its_line(self):
        with self.assertRaises(CfError) as caught:
            self.run_ingest([decl("A.x", status="maybe")])
        self.assertIn("line 3", str(caught.exception))
        source = write_probe(self.root / "bad", [decl("A.x")])
        with open(source / "index.jsonl", "a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        with self.assertRaises(CfError):
            ingest.ingest(self.universe, "alpha", source, self.out)

    def test_tags_come_from_the_span_in_the_checkout_and_the_docstring(self):
        checkout = self.root / "checkout"
        (checkout / "Alpha").mkdir(parents=True)
        lines = [""] * 60
        lines[9] = "@[wikidata Q99999001, stacks 0ABC]"
        lines[10] = "theorem Widget.exists_spanning_gadget"
        lines[21] = "  -- @[kerodon 0XYZ] after the name line is not this declaration's"
        lines[29] = "@[kerodon 01AB]"            # the line before Widget.sorried's span
        (checkout / "Alpha" / "Widget.lean").write_text("\n".join(lines), encoding="utf-8")
        manifest = self.run_ingest(alpha_records(), checkout=checkout)
        tags = {r["name"]: r["tags"] for r in self.decls()}
        self.assertEqual(tags["Widget.exists_spanning_gadget"], [
            {"tag": "stacks", "value": "0ABC"}, {"tag": "wikidata", "value": "Q99999001"},
            {"tag": "wikipedia", "value": "https://en.wikipedia.org/wiki/Widget_lemma"}])
        self.assertEqual(tags["Widget.sorried"], [])
        self.assertEqual(tags["Widget.Spans"], [])
        self.assertEqual(manifest["tags"], {"stacks": 1, "wikidata": 1, "wikipedia": 1})
        self.assertTrue(manifest["tags_read_from_checkout"])

    def test_a_path_that_leaves_the_checkout_is_not_read(self):
        checkout = self.root / "checkout"
        checkout.mkdir()
        outside = self.root / "Outside.lean"
        outside.write_text("@[wikidata Q1]\ntheorem x\n", encoding="utf-8")
        (checkout / "Link.lean").symlink_to(outside)
        records = [decl("A.x", path="../Outside.lean", line=2, start_line=1),
                   decl("A.y", path="Link.lean", line=2, start_line=1)]
        manifest = self.run_ingest(records, checkout=checkout)
        self.assertEqual([r["tags"] for r in self.decls()], [[], []])
        self.assertEqual(manifest["checkout_files_unreadable"], 2)

    def test_a_published_index_maps_its_words_and_refuses_others(self):
        published = [{"record": "declaration", "name": "P.a", "module": "Alpha.Widget",
                      "kind": "theorem", "status": "sorry"},
                     {"record": "declaration", "name": "P.b", "module": "Alpha.Widget",
                      "kind": "def", "status": "definition", "signature": "Prop"}]
        self.run_ingest(published, schema="autoform-library-index/v0")
        rows = {r["name"]: r for r in self.decls()}
        self.assertEqual(rows["P.a"]["proof_status"], "statement_only")
        self.assertEqual(rows["P.a"]["flags"], ["published_status"])
        self.assertTrue(rows["P.b"]["statement"])
        with self.assertRaises(CfError):
            self.run_ingest([{"record": "declaration", "name": "P.c", "kind": "theorem",
                              "status": "mostly"}], schema="autoform-library-index/v0")


class TestTheCanary(unittest.TestCase):

    def setUp(self):
        self.root = _scratch(self)
        self.universe = make_universe(self.root, canary=False)

    def test_a_probe_that_classifies_the_four_passes(self):
        source = write_probe(self.root / "canary", canary_records(), library="cf-canary",
                             commit=None)
        result = ingest.check_canary(self.universe, source)
        self.assertTrue(result["passed"])
        self.assertTrue(self.universe.canary_passed(TOOLCHAIN))
        self.assertEqual(set(result["cases"]), set(ingest.CANARY))

    def test_a_probe_that_misses_the_hidden_sorry_fails(self):
        source = write_probe(self.root / "canary",
                             canary_records(hidden_sorry_seen=False), library="cf-canary")
        result = ingest.check_canary(self.universe, source)
        self.assertFalse(result["passed"])
        self.assertFalse(result["cases"]["CfCanary.hiddenSorry"]["ok"])
        self.assertFalse(self.universe.canary_passed(TOOLCHAIN))

    def test_a_missing_declaration_fails(self):
        records = [r for r in canary_records() if r["name"] != "CfCanary.propDef"]
        source = write_probe(self.root / "canary", records, library="cf-canary")
        result = ingest.check_canary(self.universe, source)
        self.assertFalse(result["passed"])
        self.assertIsNone(result["cases"]["CfCanary.propDef"]["got"])

    def test_the_canary_template_has_the_four_declarations(self):
        text = (SKILL / "templates/corpus/canary/CfCanary/Basic.lean.tmpl").read_text(
            encoding="utf-8")
        for name in ingest.CANARY:
            self.assertRegex(text, rf"\b(theorem|def) {name.split('.')[-1]}\b")
        self.assertIn("sorry", text)
        self.assertIn("axiom canaryAxiom", text)
        self.assertTrue(text.startswith("@@MODULE_HEADER@@"))


class TestTheDatabase(unittest.TestCase):

    def setUp(self):
        self.root = _scratch(self)
        self.universe = make_universe(self.root, libs=("alpha", "beta"))
        self.udir = self.root / "corpora" / "u1"
        ingest.ingest(self.universe, "alpha", write_probe(self.root / "pa", alpha_records()),
                      self.udir / "index" / "alpha")
        beta = [decl("Beta.Widget.gadget_of_finite", module="Beta.Widget",
                     signature="∀ (w : Widget), Finite w → ∃ g, g.Spans w"),
                decl("Widget.exists_spanning_gadget", module="Beta.Widget")]
        ingest.ingest(self.universe, "beta",
                      write_probe(self.root / "pb", beta, library="beta", commit=BETA,
                                  modules=(("Beta.Widget", "Beta/Widget.lean"),)),
                      self.udir / "index" / "beta")
        self.db = self.udir / "corpus.sqlite"
        self.result = build_db.build(self.universe, self.udir, self.db)

    def connect(self):
        conn = query.open_db(self.db)
        self.addCleanup(conn.close)
        return conn

    def test_the_schema_is_section_8_exactly(self):
        conn = self.connect()
        tables = {r["name"]: r["sql"] for r in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'index') "
            "AND name NOT LIKE 'decls_fts_%' AND name NOT LIKE 'sqlite_%'")}
        for statement in build_db.DDL:
            name = re.search(r"(?:TABLE|INDEX) (\w+)", statement).group(1)
            with self.subTest(table=name):
                self.assertEqual(" ".join(tables[name].split()), " ".join(statement.split()))
        documents = (SKILL / "references" / "locate-documents.md").read_text(encoding="utf-8")
        flat = " ".join(documents.split())
        for statement in build_db.DDL:
            with self.subTest(documented=statement[:40]):
                self.assertIn(" ".join(statement.split()).rstrip(), flat.replace(" ;", ";"))

    def test_meta_libraries_and_rows(self):
        conn = self.connect()
        meta = query.meta(conn)
        self.assertEqual(meta["schema"], "cf-corpus/1")
        self.assertEqual(meta["universe_sha256"], self.universe.sha256())
        self.assertEqual([lib["id"] for lib in query.libraries(conn)], ["alpha", "beta"])
        self.assertEqual(self.result["declarations"], 7)
        # a name in two libraries is two declarations
        self.assertTrue(query.exists(conn, "alpha", ALPHA, "Widget.exists_spanning_gadget"))
        self.assertTrue(query.exists(conn, "beta", BETA, "Widget.exists_spanning_gadget"))
        self.assertFalse(query.exists(conn, "alpha", BETA, "Widget.exists_spanning_gadget"))
        self.assertFalse(query.exists(conn, "alpha", ALPHA, "Widget.phantom"))
        row = query.declaration(conn, "alpha", "Widget.Spans")
        self.assertEqual((row["proof_status"], row["statement"]), ("definition", True))
        self.assertEqual(self.universe.corpus_db["sha256"],
                         hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_journal_mode_delete_and_no_side_files(self):
        self.assertEqual(sorted(p.name for p in self.udir.iterdir()),
                         ["corpus.sqlite", "index", "universe.yaml"])
        copy = self.root / "copy.sqlite"
        shutil.copyfile(self.db, copy)
        conn = sqlite3.connect(copy)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_the_read_only_connection_cannot_write(self):
        conn = self.connect()
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM decls")

    def test_search_by_name_words_and_signature(self):
        conn = self.connect()
        hits = query.search(conn, "spanning gadget")
        self.assertEqual(hits[0]["declaration"], "Widget.exists_spanning_gadget")
        self.assertEqual({h["library"] for h in hits[:2]}, {"alpha", "beta"})
        only = query.search(conn, ["finite"], library="beta")
        self.assertEqual([h["declaration"] for h in only], ["Beta.Widget.gadget_of_finite"])
        self.assertEqual(query.search(conn, "finite", library="alpha"), [])
        self.assertEqual(len(query.search(conn, "widget", limit=2)), 2)
        self.assertEqual(set(hits[0]), {"library", "declaration", "module", "path", "line",
                                        "end_line", "kind", "signature", "score"})

    def test_no_query_reaches_fts5_syntax(self):
        conn = self.connect()
        for text in ('gadget OR NEAR(', '"', 'name_words:widget', 'widget AND', '*', '-', '^x',
                     ')', "'; DROP TABLE decls; --"):
            with self.subTest(text=text):
                query.search(conn, text)
        self.assertEqual(query.search(conn, "!!! ---"), [])
        self.assertEqual(corpus_search.fts_match(['a"b']), '"a""b"')

    def test_name_words(self):
        for name, words in (("Widget.exists_spanning_gadget", "widget exists spanning gadget"),
                            ("IsPGroup.card_eq", "is pgroup card eq"),
                            ("Nat.Prime.two_le", "nat prime two le"), ("x", "x"), ("", "")):
            with self.subTest(name=name):
                self.assertEqual(query.name_words(name), words)

    def test_the_tag_join(self):
        conn = self.connect()
        hits = query.tagged(conn, "wikipedia", "https://en.wikipedia.org/wiki/Widget_lemma")
        self.assertEqual(hits, [{"library": "alpha", "commit": ALPHA,
                                 "declaration": "Widget.exists_spanning_gadget"}])

    def test_a_second_build_is_a_no_op_and_a_forced_one_has_the_same_rows(self):
        again = build_db.build(self.universe, self.udir, self.db)
        self.assertFalse(again["built"])
        before = self._rows()
        forced = build_db.build(self.universe, self.udir, self.db, force=True)
        self.assertTrue(forced["built"])
        self.assertEqual(self._rows(), before)

    def _rows(self):
        conn = sqlite3.connect(self.db)
        try:
            return (conn.execute("SELECT * FROM decls ORDER BY id").fetchall(),
                    conn.execute("SELECT rowid, * FROM decls_fts ORDER BY rowid").fetchall(),
                    conn.execute("SELECT * FROM tags ORDER BY decl_id, tag").fetchall())
        finally:
            conn.close()

    def test_a_changed_or_missing_index_is_refused(self):
        decls = self.udir / "index" / "alpha" / "decls.jsonl"
        decls.write_text(decls.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(CfError):
            build_db.build(self.universe, self.udir, self.db, force=True)
        self.universe.library("beta")["index"] = None
        with self.assertRaises(CfError):
            build_db.build(self.universe, self.udir, self.db, force=True)

    def test_a_file_that_is_not_a_corpus_is_refused(self):
        other = self.root / "other.sqlite"
        sqlite3.connect(other).close()
        with self.assertRaises(CfError):
            query.open_db(other)
        with self.assertRaises(CfError):
            query.open_db(self.root / "missing.sqlite")


class TestTheHostTools(unittest.TestCase):
    """Run as the worker runs them: copied alone into a gate directory, by absolute path."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        universe = make_universe(cls.root)
        udir = cls.root / "corpora" / "u1"
        ingest.ingest(universe, "alpha", write_probe(cls.root / "pa", alpha_records()),
                      udir / "index" / "alpha")
        cls.db = udir / "corpus.sqlite"
        build_db.build(universe, udir, cls.db)
        cls.gate = cls.root / "gate"
        cls.gate.mkdir()
        for name in ("corpus_search.py", "corpus_show.py"):
            shutil.copy2(SCRIPTS / "corpus" / name, cls.gate / name)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def tool(self, name, *args):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        return subprocess.run([sys.executable, "-I", str(self.gate / name), *args],
                              capture_output=True, text=True, cwd=str(self.root), env=env)

    def test_search_prints_json_lines(self):
        proc = self.tool("corpus_search.py", "--db", str(self.db), "spanning", "gadget")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(rows[0]["declaration"], "Widget.exists_spanning_gadget")
        self.assertEqual(rows[0]["library"], "alpha")
        limited = self.tool("corpus_search.py", "--db", str(self.db), "--limit", "1", "widget")
        self.assertEqual(len(limited.stdout.splitlines()), 1)
        nothing = self.tool("corpus_search.py", "--db", str(self.db), "zzzz")
        self.assertEqual((nothing.returncode, nothing.stdout), (0, ""))

    def test_show_prints_one_declaration_and_never_its_proof_status(self):
        proc = self.tool("corpus_show.py", "--db", str(self.db), "alpha",
                         "Widget.exists_spanning_gadget")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = json.loads(proc.stdout)
        self.assertEqual(record["tags"], [{"tag": "wikipedia",
                                           "value": "https://en.wikipedia.org/wiki/Widget_lemma"}])
        self.assertNotIn("proof_status", record)
        self.assertIn("docstring", record)

    def test_the_exit_codes(self):
        missing = self.tool("corpus_show.py", "--db", str(self.db), "alpha", "Widget.phantom")
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(self.tool("corpus_search.py", "--db", str(self.db)).returncode, 2)
        self.assertEqual(self.tool("corpus_search.py", "--db", str(self.db), "--limit", "0",
                                   "x").returncode, 2)
        absent = self.tool("corpus_search.py", "--db", str(self.root / "nope.sqlite"), "x")
        self.assertEqual(absent.returncode, 3)
        self.assertIn("not of your query", absent.stderr)
        self.assertEqual(self.tool("corpus_show.py", "--db",
                                   str(self.root / "pa" / "index.jsonl"), "alpha",
                                   "x").returncode, 3)


class TestTheCommand(unittest.TestCase):

    def setUp(self):
        self.root = _scratch(self)
        self.ws = self.root / "ws"
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            cf_main.main(["init", str(self.ws), "--no-git", "-q"])

    def cf(self, *argv) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = cf_main.main([*argv, "--workspace", str(self.ws)])
        self.assertEqual(code, 0, buf.getvalue())
        return buf.getvalue()

    def test_the_whole_program(self):
        self.cf("corpus", "init", "u1", "--frozen", "2026-10-17")
        self.cf("corpus", "add", "u1", "--library", "alpha", "--repo", "https://example.org/alpha",
                "--commit", ALPHA, "--toolchain", TOOLCHAIN, "--roots", "Alpha")
        self.cf("corpus", "add", "u1", "--library", "gamma", "--exclude", "licence forbids it")
        with self.assertRaises(CfError):
            cf_main.main(["corpus", "ingest", "u1", "--library", "alpha", "--from",
                          str(write_probe(self.root / "pa", alpha_records())),
                          "--workspace", str(self.ws)])
        canary = write_probe(self.root / "canary", canary_records(), library="cf-canary")
        self.assertIn("PASSED", self.cf("corpus", "ingest", "u1", "--canary", "--from",
                                        str(canary)))
        out = self.cf("corpus", "ingest", "u1", "--library", "alpha", "--from",
                      str(self.root / "pa"))
        self.assertIn("5 declarations", out)
        self.assertIn("built", self.cf("corpus", "build-db", "u1"))
        status = self.cf("corpus", "status", "u1")
        self.assertIn("excluded gamma: licence forbids it", status)
        self.assertIn("current", status)
        data = json.loads(self.cf("corpus", "status", "u1", "--json"))
        self.assertEqual(data["libraries"][0]["canary"], "passed")
        hits = [json.loads(line) for line in
                self.cf("corpus", "search", "u1", "spanning", "--json").splitlines()]
        self.assertEqual(hits[0]["declaration"], "Widget.exists_spanning_gadget")
        self.assertIn("axiom_dependent", self.cf("corpus", "show", "u1", "alpha",
                                                 "Widget.bigAxiomThm"))

    def test_probe_command_prints_the_pipeline_and_runs_nothing(self):
        self.cf("corpus", "init", "u1")
        self.cf("corpus", "add", "u1", "--library", "alpha", "--repo", "https://example.org/alpha",
                "--commit", ALPHA, "--toolchain", TOOLCHAIN, "--roots", "Alpha")
        with mock.patch("subprocess.run") as ran, mock.patch("subprocess.Popen") as popen:
            out = self.cf("corpus", "probe-command", "u1", "--library", "alpha")
            canary = self.cf("corpus", "probe-command", "u1", "--canary", "--toolchain",
                             TOOLCHAIN)
        ran.assert_not_called()
        popen.assert_not_called()
        for needle in ("MIN_FREE_GB=450", "65536", "build_universe_host.sh", "cf corpus ingest u1",
                       ALPHA, 'rm -rf "$ATLAS_ROOT"/libs/alpha/.lake'):
            with self.subTest(needle=needle):
                self.assertIn(needle, out)
        self.assertIn('cp -R templates/corpus/canary/. "$ATLAS_ROOT/canary/4.34.0"/', canary)
        self.assertIn("cf corpus ingest u1 --canary", canary)

    def test_add_without_its_pin_is_refused(self):
        self.cf("corpus", "init", "u1")
        with self.assertRaises(CfError):
            cf_main.main(["corpus", "add", "u1", "--library", "alpha",
                          "--workspace", str(self.ws)])


if __name__ == "__main__":
    unittest.main()
