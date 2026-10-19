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
import shlex
import shutil
import sqlite3
import time
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


_UNSET = object()


def write_probe(directory: Path, decls: list[dict], *, library: str = "alpha",
                commit: str | None = ALPHA, toolchain: str = "4.34.0",
                modules: tuple = (("Alpha.Widget", "Alpha/Widget.lean"),),
                schema: str = "formal-companion-library-index/v0",
                header: dict | None = None, manifest: dict | None | object = _UNSET,
                extra_lines: tuple = ()) -> Path:
    """A probe output directory as probe_merge.py writes it: index.jsonl + manifest.json.

    ``header`` and ``manifest`` override keys (a value of None deletes the key);
    ``manifest=None`` writes no manifest at all.  The manifest's sha, byte count
    and record counts are the index's own unless overridden.
    """
    directory.mkdir(parents=True, exist_ok=True)
    published = schema == "autoform-library-index/v0"
    head = ({"record": "header", "schema": schema, "lean_toolchain": f"leanprover/lean4:v{toolchain}"}
            if published else
            {"record": "header", "schema": schema, "library": library, "toolchain": toolchain,
             "probe_revision": 1, "commit": commit, "declaration_count": len(decls),
             "module_count": len(modules)})
    for key, value in (header or {}).items():
        if value is None:
            head.pop(key, None)
        else:
            head[key] = value
    lines = [head] + [{"record": "module", "name": n, "path": p, "module_doc": None,
                       "truncated": False} for n, p in modules] + decls
    text = "".join(json.dumps(r) + "\n" for r in lines) + "".join(extra_lines)
    index = directory / "index.jsonl"
    index.write_text(text, encoding="utf-8")
    if manifest is None:
        return directory
    body = {"library": library, "commit": commit, "toolchain": f"leanprover/lean4:v{toolchain}",
            "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
            "index_bytes": index.stat().st_size,
            "records": {"header": 1, "module": len(modules), "declaration": len(decls)},
            "dirty": False, "template_sha256": "0" * 64,
            "modules_without_path": sum(1 for _n, p in modules if p is None)}
    for key, value in ({} if manifest is _UNSET else manifest).items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value
    (directory / "manifest.json").write_text(json.dumps(body), encoding="utf-8")
    return directory


def git_checkout(root: Path, files: dict[str, str]) -> tuple[Path, str]:
    """A real git checkout holding ``files``, committed; returns (path, HEAD sha)."""
    root.mkdir(parents=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    for argv in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "pin"]):
        subprocess.run(["git", "-C", str(root), *argv], check=True, env=env,
                       capture_output=True)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()
    return root, head


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
                                     "corpus_db", "corpus_db_blind", "universe_sha256"})
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

    def test_r2_a_re_add_records_a_missing_licence_and_refuses_another(self):
        """The review's LicenceNull variant: beta added without --licence, then re-added
        with one, exited 0 and still said ``licence: null``."""
        universe = make_universe(self.root, libs=("alpha", "beta"))
        sha = universe.sha256()
        self.assertIsNone(universe.library("beta")["licence"])
        pin = dict(library_id="beta", repo="https://example.org/beta", commit=BETA,
                   toolchain=TOOLCHAIN, roots=["Beta"])
        row = universe.add_library(**pin, licence="Apache-2.0")
        self.assertEqual(row["licence"], {"spdx": "Apache-2.0"})
        universe.save()
        again = Universe.load(universe.path)
        self.assertEqual(again.library("beta")["licence"], {"spdx": "Apache-2.0"})
        self.assertEqual(again.sha256(), sha)                  # outside universe_sha256
        self.assertEqual(again.add_library(**pin)["licence"], {"spdx": "Apache-2.0"})
        self.assertEqual(again.add_library(**pin, licence="Apache-2.0")["licence"],
                         {"spdx": "Apache-2.0"})
        with self.assertRaises(CfError) as caught:
            again.add_library(**pin, licence="MIT")
        self.assertIn("already records licence 'Apache-2.0'", str(caught.exception))
        with self.assertRaises(CfError) as caught:
            again.add_library(**pin, brain_lib="Beta!")
        self.assertIn("already records brain_lib 'beta'", str(caught.exception))
        self.assertEqual(again.library("beta")["licence"], {"spdx": "Apache-2.0"})
        for bad in ("Apache-2.0;rm -rf", "<MIT>", "x" * 101):
            with self.subTest(licence=bad), self.assertRaises(CfError):
                universe.add_library(library_id="gamma", repo="https://example.org/gamma",
                                     commit="c" * 40, toolchain=TOOLCHAIN, roots=["G"],
                                     licence=bad)
        self.assertEqual(universe.add_library(
            library_id="delta", repo="https://example.org/delta", commit="d" * 40,
            toolchain=TOOLCHAIN, roots=["D"], licence=" MIT OR Apache-2.0 ")["licence"],
            {"spdx": "MIT OR Apache-2.0"})

    def test_r2_a_repository_the_consumer_cannot_link_is_refused_at_add(self):
        """The review's RepoTrailingSlash and RepoFiveSegments variants were pinned, exported
        with nothing refused, and refused by the consumer; a re-add cannot change a repo."""
        universe = make_universe(self.root)
        for bad in ("https://example.org/beta/", "https://gitlab.example.org/group/sub/team/lean/beta",
                    "https://example.org/a/../beta", "https://example.org/a//beta",
                    "https://example.org/./beta", "https://localhost/beta", "https://example.org",
                    "https://example.org:8443/beta", "https://example.org/be ta",
                    "https://example.org/" + "b" * 101):
            with self.subTest(repo=bad), self.assertRaises(CfError) as caught:
                universe.add_library(library_id="beta", repo=bad, commit=BETA,
                                     toolchain=TOOLCHAIN, roots=["Beta"])
            self.assertIn("no trailing slash", str(caught.exception))
        for good in ("https://github.com/leanprover-community/mathlib4",
                     "https://gitlab.example.org/group/sub/team/beta",
                     "https://example.org/beta.git", "https://example.org/~beta"):
            with self.subTest(repo=good):
                lid = f"lib{len(universe.libraries)}"
                universe.add_library(library_id=lid, repo=good, commit=BETA,
                                     toolchain=TOOLCHAIN, roots=["Beta"], brain_lib=lid)
        path = self.root / "trailing.yaml"
        text = universe.path.read_text(encoding="utf-8")
        path.write_text(text.replace("https://example.org/alpha", "https://example.org/alpha/"),
                        encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            Universe.load(path)
        self.assertIn("'https://example.org/alpha/' is not an https repository URL",
                      str(caught.exception))

    def test_r2_the_record_sets_length_caps_are_refused_where_the_value_is_chosen(self):
        """The review's LongExclusion variant: a reason of about 390 characters exported and
        was refused (with a message naming the wrong rule) by the consumer's cap, which it
        has since written down: 300 characters for brain_lib, toolchain and each root, 1000
        for an exclusion's reason."""
        universe = make_universe(self.root)
        pin = dict(library_id="gamma", repo="https://example.org/gamma", commit="c" * 40,
                   toolchain=TOOLCHAIN, roots=["G"])
        for bad, needle in (({"brain_lib": "B" * 301}, "brain_lib is 301 characters"),
                            ({"toolchain": "leanprover/lean4:v" + "4" * 290},
                             "toolchain is 308 characters"),
                            ({"roots": ["G", "A" * 301]}, "1 root(s) longer than 300")):
            with self.subTest(bad=bad), self.assertRaises(CfError) as caught:
                universe.add_library(**{**pin, **bad})
            self.assertIn(needle, str(caught.exception))
        # a new row's token is also a library token (TestBrainLibIsALibraryToken), at most 64
        universe.add_library(**{**pin, "brain_lib": "B" * 64, "roots": ["A" * 300]})
        with self.assertRaises(CfError) as caught:
            universe.exclude("delta", "x" * 1001)
        self.assertIn("1001 characters, longer than 1000", str(caught.exception))
        self.assertNotIn("delta", [r["id"] for r in universe.excluded])
        reason = " ".join(["the build failed at the pinned commit"] * 10)     # about 390
        self.assertEqual(universe.exclude("delta", reason)["reason"], reason)
        self.assertEqual(len(universe.exclude("epsilon", "y" * 1000)["reason"]), 1000)
        universe.save()
        path = self.root / "long.yaml"
        path.write_text(universe.path.read_text(encoding="utf-8").replace("y" * 1000, "y" * 1001),
                        encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            Universe.load(path)
        self.assertIn("a reason of 1001 characters, longer than 1000", str(caught.exception))

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


class TestBrainLibIsALibraryToken(unittest.TestCase):
    """B6: u1's ``FF.<Root>`` tokens passed (any non-empty name of at most 300 characters)
    and the consumer's Brain refuses them; a library added from now on carries a token of
    ``corpus.universe.BRAIN_LIB``, while a universe written before still loads."""

    def setUp(self):
        self.root = _scratch(self)

    def pin(self, library_id="gamma", **kw):
        return dict(library_id=library_id, repo=f"https://example.org/{library_id}",
                    commit="c" * 40, toolchain=TOOLCHAIN, roots=["G"], **kw)

    def test_a_new_row_outside_the_grammar_is_refused_naming_the_flag(self):
        universe = make_universe(self.root)
        for token, hint in (("FF.AdicModules", "FF_AdicModules"), ("Mathlib!", "Mathlib_"),
                            ("1Lib", "L1Lib"), ("Ma thlib", "Ma_thlib"), ("_Init", "L_Init"),
                            ("Ä", "L_"), ("A" * 65, "A" * 64)):
            with self.subTest(brain_lib=token), self.assertRaises(CfError) as caught:
                universe.add_library(**self.pin(brain_lib=token))
            self.assertIn(f"brain_lib {token!r} is not a library token", str(caught.exception))
            self.assertIn(f"--brain-lib {hint}", caught.exception.hint)
        self.assertEqual(universe.library_ids(), ["alpha"])

    def test_an_id_that_is_no_token_needs_the_flag(self):
        universe = make_universe(self.root)
        with self.assertRaises(CfError) as caught:
            universe.add_library(**self.pin("lean-core"))
        self.assertIn("brain_lib (the library id, as none was given) 'lean-core'",
                      str(caught.exception))
        self.assertIn("--brain-lib lean_core", caught.exception.hint)
        row = universe.add_library(**self.pin("lean-core", brain_lib="Init"))
        self.assertEqual(row["brain_lib"], "Init")

    def test_the_consumers_tokens_are_admitted(self):
        universe = make_universe(self.root)
        for i, token in enumerate(("Mathlib", "FF_AdicModules", "Init", "Std", "TauCeti",
                                   "A" * 64, "x")):
            with self.subTest(brain_lib=token):
                lid = f"lib{i}"
                row = universe.add_library(**self.pin(lid, brain_lib=token))
                self.assertEqual(row["brain_lib"], token)
        self.assertEqual(universe.library("alpha")["brain_lib"], "alpha")     # the id, a token
        universe.save()

    def test_a_universe_written_before_the_grammar_still_loads_and_re_adds(self):
        universe = make_universe(self.root)
        universe.data["libraries"].append({
            "id": "ff-adic", "brain_lib": "FF.AdicModules", "repo": "https://example.org/adic",
            "commit": "d" * 40, "toolchain": TOOLCHAIN, "roots": ["AdicModules"],
            "licence": None, "conventions": None, "index": None})
        universe.save()
        again = Universe.load(universe.path)
        self.assertEqual(again.validate(), [])
        self.assertEqual(again.library("ff-adic")["brain_lib"], "FF.AdicModules")
        pin = dict(library_id="ff-adic", repo="https://example.org/adic", commit="d" * 40,
                   toolchain=TOOLCHAIN, roots=["AdicModules"])
        self.assertEqual(again.add_library(**pin, licence="Apache-2.0")["licence"],
                         {"spdx": "Apache-2.0"})
        self.assertEqual(again.add_library(**pin, brain_lib="FF.AdicModules")["brain_lib"],
                         "FF.AdicModules")
        again.save()
        self.assertEqual(Universe.load(universe.path).library("ff-adic")["brain_lib"],
                         "FF.AdicModules")

    def test_cf_corpus_add_refuses_it_and_writes_nothing(self):
        make_universe(self.root)
        before = (self.root / "corpora" / "u1" / "universe.yaml").read_bytes()
        with self.assertRaises(CfError) as caught:
            cf_main.main(["corpus", "add", "u1", "--library", "gamma", "--repo",
                          "https://example.org/gamma", "--commit", "c" * 40, "--toolchain",
                          TOOLCHAIN, "--roots", "G", "--brain-lib", "FF.Gamma",
                          "--base", str(self.root / "corpora")])
        self.assertIn("--brain-lib FF_Gamma", caught.exception.hint)
        self.assertEqual((self.root / "corpora" / "u1" / "universe.yaml").read_bytes(), before)


class TestIngest(unittest.TestCase):

    def setUp(self):
        self.root = _scratch(self)
        self.universe = make_universe(self.root, libs=("alpha", "beta"))
        self.out = self.root / "corpora" / "u1" / "index" / "alpha"

    def run_ingest(self, records, checkout=None, accept_published=None, **kw):
        shutil.rmtree(self.root / "probe", ignore_errors=True)
        source = write_probe(self.root / "probe", records, **kw)
        return ingest.ingest(self.universe, "alpha", source, self.out, checkout=checkout,
                             accept_published=accept_published)

    def decls(self) -> list[dict]:
        lines = (self.out / "decls.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def refused(self, records, needle: str = "", **kw):
        with self.assertRaises(CfError) as caught:
            self.run_ingest(records, **kw)
        if needle:
            self.assertIn(needle, str(caught.exception))
        return caught.exception

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
                                        "deprecated", "private", "probe_proof_status", "flags"})

    def test_an_unsorted_input_is_sorted_through_chunks(self):
        records = [decl(f"N.d{i:03d}") for i in (5, 3, 9, 1, 7, 2, 8, 0, 6, 4)]
        with mock.patch.object(ingest, "CHUNK_RECORDS", 3):
            self.run_ingest(records)
        self.assertEqual([r["name"] for r in self.decls()], [f"N.d{i:03d}" for i in range(10)])
        # no chunk file is left behind
        self.assertEqual(sorted(p.name for p in self.out.parent.iterdir()), ["alpha"])

    def test_a_repeated_public_name_is_refused_sorted_or_not(self):
        for records in ([decl("A.x"), decl("A.x")], [decl("B.y"), decl("A.x"), decl("A.x")]):
            with self.subTest(records=[r["name"] for r in records]):
                self.refused(records, "two public declarations named A.x")
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
        self.refused(alpha_records(), "canary")
        self.universe.record_canary(toolchain="4.34.0", passed=False, cases={},
                                    index_sha256="0" * 64, checked="t")
        self.refused(alpha_records(), "canary")

    def test_a_record_ingest_cannot_decide_is_refused_with_its_line(self):
        error = self.refused([decl("A.x", proof_status="maybe")])
        self.assertIn("line 3", str(error))
        broken = self.root / "bad"
        write_probe(broken, [decl("A.x")], extra_lines=("{not json\n",))
        with self.assertRaises(CfError) as caught:
            ingest.ingest(self.universe, "alpha", broken, self.out)
        self.assertIn("not JSON", str(caught.exception))


class TestTheIndexMustBeThePinnedOneWhole(unittest.TestCase):
    """Review #32, #37, #38: identity from the index itself, and every byte accounted for."""

    def setUp(self):
        self.root = _scratch(self)
        self.universe = make_universe(self.root, libs=("alpha", "beta"))
        self.out = self.root / "corpora" / "u1" / "index" / "alpha"

    def ingest_dir(self, source):
        return ingest.ingest(self.universe, "alpha", source, self.out)

    def refused(self, needle, records=None, **kw):
        source = write_probe(self.root / f"p{len(list(self.root.iterdir()))}",
                             alpha_records() if records is None else records, **kw)
        with self.assertRaises(CfError) as caught:
            self.ingest_dir(source)
        self.assertIn(needle, str(caught.exception))

    def test_a_header_with_no_toolchain_is_refused(self):
        self.refused("names no toolchain", header={"toolchain": None})

    def test_the_header_and_the_manifest_may_not_disagree(self):
        self.refused("disagree", header={"library": "beta"})            # manifest says alpha
        self.refused("disagree", header={"commit": BETA})
        self.refused("disagree", manifest={"commit": BETA})

    def test_a_sibling_manifest_cannot_vouch_for_another_index(self):
        # beta's re-probed index beside alpha's manifest: the header is read too
        source = write_probe(self.root / "mixed", alpha_records(), library="beta", commit=BETA)
        alpha_manifest = write_probe(self.root / "alpha", alpha_records())
        shutil.copy2(alpha_manifest / "manifest.json", source / "manifest.json")
        with self.assertRaises(CfError):
            self.ingest_dir(source)

    def test_an_index_cut_short_is_refused(self):
        source = write_probe(self.root / "cut", alpha_records())
        index = source / "index.jsonl"
        lines = index.read_text(encoding="utf-8").splitlines(keepends=True)
        index.write_text("".join(lines[:-1]), encoding="utf-8")        # at a line boundary
        with self.assertRaises(CfError) as caught:
            self.ingest_dir(source)
        self.assertIn("sha256", str(caught.exception))

    def test_the_record_counts_must_be_the_records_read(self):
        self.refused("declaration_count", header={"declaration_count": 9})
        self.refused("records.declaration", manifest={"records": {"header": 1, "module": 1,
                                                                   "declaration": 9}})
        self.refused("bytes", manifest={"index_bytes": 3})

    def test_a_probe_index_needs_its_manifest_and_its_sha(self):
        self.refused("no manifest.json", manifest=None)
        self.refused("index_sha256", manifest={"index_sha256": None})

    def test_a_dirty_or_unknown_checkout_is_refused(self):
        self.refused("dirty", manifest={"dirty": True})
        source = write_probe(self.root / "unknown", alpha_records())
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        manifest["dirty"] = None
        (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            self.ingest_dir(source)
        self.assertIn("could not tell", str(caught.exception))

    def test_an_index_with_no_module_path_is_refused(self):
        """Review #46, the ingest side: a probe that never found the sources."""
        self.refused("no module", modules=(("Alpha.Widget", None),))


class TestTheProbesRecordMustAgreeWithItself(unittest.TestCase):
    """Review #31: a record missing its closure or contradicting itself is refused."""

    def check(self, **kw):
        record = decl("A.x", **kw)
        with self.assertRaises(CfError):
            ingest.check_consistent(record)

    def test_a_missing_or_bad_closure_is_refused(self):
        for value in (_UNSET, None, "sorryAx", [1]):
            record = decl("A.x", status="statement_only")
            if value is _UNSET:
                del record["nonstandard_axioms"]
            else:
                record["nonstandard_axioms"] = value
            with self.subTest(value=value), self.assertRaises(CfError):
                ingest.decide(record)

    def test_contradictions_are_refused(self):
        self.check(status="statement_only", nonstandard_axioms=[])
        self.check(status="proved", nonstandard_axioms=["sorryAx"])
        self.check(status="wanted")
        self.check(status="proved", wrapper="W.ProofWanted")
        self.check(kind="axiom", status="proved")
        self.check(kind="theorem", status="axiom")
        self.check(kind="theorem", status="definition")
        self.check(kind="structure", status="proved")

    def test_consistent_records_pass(self):
        for record in (*canary_records(), *alpha_records()):
            with self.subTest(name=record["name"]):
                ingest.check_consistent(record)

    def test_ingest_refuses_the_unsafe_move(self):
        root = _scratch(self)
        universe = make_universe(root)
        source = write_probe(root / "p", [decl("A.x", status="statement_only")])
        with self.assertRaises(CfError) as caught:
            ingest.ingest(universe, "alpha", source, root / "out")
        self.assertIn("contradicts itself", str(caught.exception))


class TestPublishedIndexes(unittest.TestCase):
    """Review #30, #39: no bypass of the identity checks, and acceptance only by name."""

    PUBLISHED = [{"record": "declaration", "name": "P.a", "module": "Alpha.Widget",
                  "kind": "theorem", "status": "sorry"},
                 {"record": "declaration", "name": "P.b", "module": "Alpha.Widget",
                  "kind": "def", "status": "definition", "signature": "Prop"}]

    def setUp(self):
        self.root = _scratch(self)
        self.universe = make_universe(self.root, canary=False)
        self.out = self.root / "corpora" / "u1" / "index" / "alpha"

    def source(self, name="pub", records=None, **kw):
        kw.setdefault("modules", (("Alpha.Widget", None),))
        return write_probe(self.root / name, records or self.PUBLISHED,
                           schema="autoform-library-index/v0", **kw)

    def test_it_is_refused_unless_accepted_by_name(self):
        with self.assertRaises(CfError) as caught:
            ingest.ingest(self.universe, "alpha", self.source(), self.out)
        self.assertIn("--accept-published", caught.exception.hint)
        with self.assertRaises(CfError):
            ingest.ingest(self.universe, "alpha", self.source("b"), self.out,
                          accept_published="   ")

    def test_the_identity_checks_apply_to_it(self):
        for name, kw in (("lib", {"manifest": {"library": "someone-else"}}),
                         ("nocommit", {"commit": None}),
                         ("commit", {"commit": BETA}),
                         ("toolchain", {"toolchain": "4.99.0"}),
                         ("nomanifest", {"manifest": None})):
            with self.subTest(case=name), self.assertRaises(CfError):
                ingest.ingest(self.universe, "alpha", self.source(name, **kw), self.out,
                              accept_published="the library's own CI publishes it")

    def test_an_accepted_one_is_recorded_and_flagged(self):
        manifest = ingest.ingest(self.universe, "alpha", self.source(), self.out,
                                 accept_published="the library's own CI publishes it")
        self.assertEqual(manifest["published"]["reason"], "the library's own CI publishes it")
        row = self.universe.library("alpha")["published_index"]
        self.assertEqual(row["reason"], "the library's own CI publishes it")
        rows = {r["name"]: r for r in (json.loads(line) for line in
                (self.out / "decls.jsonl").read_text(encoding="utf-8").splitlines())}
        self.assertEqual(rows["P.a"]["proof_status"], "statement_only")
        self.assertEqual(rows["P.a"]["flags"], ["published_status"])
        self.assertTrue(rows["P.b"]["statement"])
        self.universe.save()
        self.assertIn("published_index", Universe.load(self.universe.path).library("alpha"))
        # a probe index later replaces the acceptance
        self.universe.record_canary(toolchain="4.34.0", passed=True, cases={},
                                    index_sha256="0" * 64, checked="t")
        ingest.ingest(self.universe, "alpha", write_probe(self.root / "probe", alpha_records()),
                      self.out)
        self.assertNotIn("published_index", self.universe.library("alpha"))

    def test_an_unknown_status_word_is_refused(self):
        bad = [{"record": "declaration", "name": "P.c", "module": "Alpha.Widget",
                "kind": "theorem", "status": "mostly"}]
        with self.assertRaises(CfError):
            ingest.ingest(self.universe, "alpha", self.source(records=bad), self.out,
                          accept_published="x")

    def test_a_probe_index_cannot_carry_the_override(self):
        with self.assertRaises(CfError):
            ingest.ingest(self.universe, "alpha", write_probe(self.root / "p", alpha_records()),
                          self.out, accept_published="x")


class TestPrivateTwins(unittest.TestCase):
    """Review #34: the probe names a private declaration by its user name."""

    def setUp(self):
        self.root = _scratch(self)
        self.universe = make_universe(self.root)
        self.out = self.root / "out"

    def run_ingest(self, records):
        modules = (("Alpha.A", "Alpha/A.lean"), ("Alpha.B", "Alpha/B.lean"))
        return ingest.ingest(self.universe, "alpha",
                             write_probe(self.root / "p", records, modules=modules), self.out)

    def names(self):
        return [json.loads(l)["name"] for l in
                (self.out / "decls.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_private_twins_are_dropped_and_counted(self):
        manifest = self.run_ingest([decl("Alpha.aux", module="Alpha.A", private=True),
                                    decl("Alpha.aux", module="Alpha.B", private=True),
                                    decl("Alpha.main", module="Alpha.A")])
        self.assertEqual(self.names(), ["Alpha.main"])
        self.assertEqual(manifest["private_collisions_dropped"],
                         {"count": 2, "names": ["Alpha.aux"]})

    def test_a_public_one_keeps_its_name(self):
        manifest = self.run_ingest([decl("Alpha.aux", module="Alpha.A"),
                                    decl("Alpha.aux", module="Alpha.B", private=True)])
        self.assertEqual(self.names(), ["Alpha.aux"])
        self.assertEqual(manifest["private_collisions_dropped"]["count"], 1)

    def test_two_public_ones_are_still_refused(self):
        with self.assertRaises(CfError):
            self.run_ingest([decl("Alpha.aux", module="Alpha.A"),
                             decl("Alpha.aux", module="Alpha.B")])


class TestFieldsThatAreNotStrings(unittest.TestCase):
    """Review #43: a bad path is a refusal naming its line, never a traceback."""

    def test_bad_paths_are_refused_with_the_line(self):
        root = _scratch(self)
        universe = make_universe(root)
        for i, bad in enumerate((5, ["a"], "A/W\u0000.lean")):
            with self.subTest(path=bad):
                source = write_probe(root / f"p{i}", [decl("A.x", path=bad)])
                with self.assertRaises(CfError) as caught:
                    ingest.ingest(universe, "alpha", source, root / "out")
                self.assertIn("line 3", str(caught.exception))
        source = write_probe(root / "m", [decl("A.x")], modules=((7, "Alpha/W.lean"),))
        with self.assertRaises(CfError):
            ingest.ingest(universe, "alpha", source, root / "out")


class TestTagsFromACheckout(unittest.TestCase):
    """Review #44 (the checkout must be the pin's, clean) and the span rule."""

    def setUp(self):
        self.root = _scratch(self)
        lines = [""] * 60
        lines[9] = "@[wikidata Q99999001, stacks 0ABC]"
        lines[10] = "theorem Widget.exists_spanning_gadget"
        lines[21] = "  -- @[kerodon 0XYZ] after the name line is not this declaration's"
        lines[29] = "@[kerodon 01AB]"            # the line before Widget.sorried's span
        self.checkout, self.head = git_checkout(self.root / "checkout",
                                                {"Alpha/Widget.lean": "\n".join(lines)})
        self.universe = make_universe(self.root)
        self.universe.library("alpha")["commit"] = self.head
        self.out = self.root / "out"

    def run_ingest(self, records=None, checkout=None):
        source = write_probe(self.root / "p", records or alpha_records(), commit=self.head)
        return ingest.ingest(self.universe, "alpha", source, self.out,
                             checkout=checkout or self.checkout)

    def test_tags_come_from_the_span_and_the_docstring(self):
        manifest = self.run_ingest()
        tags = {json.loads(l)["name"]: json.loads(l)["tags"] for l in
                (self.out / "decls.jsonl").read_text(encoding="utf-8").splitlines()}
        self.assertEqual(tags["Widget.exists_spanning_gadget"], [
            {"tag": "stacks", "value": "0ABC"}, {"tag": "wikidata", "value": "Q99999001"},
            {"tag": "wikipedia", "value": "https://en.wikipedia.org/wiki/Widget_lemma"}])
        self.assertEqual(tags["Widget.sorried"], [])
        self.assertEqual(tags["Widget.Spans"], [])
        self.assertEqual(manifest["tags"], {"stacks": 1, "wikidata": 1, "wikipedia": 1})

    def test_a_checkout_at_another_commit_is_refused(self):
        self.universe.library("alpha")["commit"] = "c" * 40
        source = write_probe(self.root / "p", alpha_records(), commit="c" * 40)
        with self.assertRaises(CfError) as caught:
            ingest.ingest(self.universe, "alpha", source, self.out, checkout=self.checkout)
        self.assertIn("not at the pinned", str(caught.exception))

    def test_a_dirty_checkout_is_refused(self):
        (self.checkout / "Alpha" / "Widget.lean").write_text("@[wikidata Q1]\n", encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            self.run_ingest()
        self.assertIn("modified", str(caught.exception))

    def test_a_directory_that_is_not_a_checkout_is_refused(self):
        plain = self.root / "plain"
        plain.mkdir()
        with self.assertRaises(CfError):
            self.run_ingest(checkout=plain)

    def test_head_is_read_from_files(self):
        fake = self.root / "fake"
        (fake / ".git" / "refs" / "heads").mkdir(parents=True)
        (fake / ".git" / "HEAD").write_text("a" * 40 + "\n", encoding="utf-8")
        self.assertEqual(ingest.checkout_head(fake), "a" * 40)
        (fake / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        self.assertIsNone(ingest.checkout_head(fake))
        (fake / ".git" / "packed-refs").write_text(f"# pack-refs\n{'b' * 40} refs/heads/main\n",
                                                   encoding="utf-8")
        self.assertEqual(ingest.checkout_head(fake), "b" * 40)
        (fake / ".git" / "refs" / "heads" / "main").write_text("c" * 40, encoding="utf-8")
        self.assertEqual(ingest.checkout_head(fake), "c" * 40)
        (fake / ".git" / "HEAD").write_text("ref: refs/../../etc\n", encoding="utf-8")
        self.assertIsNone(ingest.checkout_head(fake))
        self.assertEqual(ingest.checkout_head(self.checkout), self.head)

    def test_a_path_that_leaves_the_checkout_is_not_read(self):
        outside = self.root / "Outside.lean"
        outside.write_text("@[wikidata Q1]\ntheorem x\n", encoding="utf-8")
        records = [decl("A.x", path="../Outside.lean", line=2, start_line=1)]
        manifest = self.run_ingest(records)
        self.assertEqual(json.loads((self.out / "decls.jsonl").read_text())["tags"], [])
        self.assertEqual(manifest["checkout_files_unreadable"], 1)


class TestWikipediaURLs(unittest.TestCase):
    """Review #35: apostrophes, balanced parentheses, percent-encoding, one canonical form."""

    def test_titles_and_their_canonical_form(self):
        cases = {
            "https://en.wikipedia.org/wiki/Wilson's_theorem": "Wilson's_theorem",
            "see https://en.wikipedia.org/wiki/Lagrange's_theorem_(group_theory).":
                "Lagrange's_theorem_(group_theory)",
            "<https://en.wikipedia.org/wiki/Ring_(mathematics)>": "Ring_(mathematics)",
            "[ring](https://en.wikipedia.org/wiki/Ring_(mathematics))": "Ring_(mathematics)",
            "(https://en.wikipedia.org/wiki/Widget_lemma)": "Widget_lemma",
            "https://en.wikipedia.org/wiki/Wilson%27s_theorem": "Wilson's_theorem",
            "https://en.wikipedia.org/wiki/Sylow_theorems#Theorems": "Sylow_theorems",
            "https://en.m.wikipedia.org/wiki/Sylow_theorems": "Sylow_theorems",
            "https://en.wikipedia.org/wiki/widget_lemma": "Widget_lemma",
            "'https://en.wikipedia.org/wiki/Widget_lemma'": "Widget_lemma",
            "https://en.wikipedia.org/wiki/G%C3%B6del%27s_incompleteness_theorems,":
                "Gödel's_incompleteness_theorems",
            "https://en.wikipedia.org/wiki/Ring_(mathematics": "Ring",
        }
        for text, title in cases.items():
            with self.subTest(text=text):
                self.assertEqual(ingest.wikipedia_urls(text),
                                 [f"https://en.wikipedia.org/wiki/{title}"])

    def test_the_seam_cases_join_the_desktop_page(self):
        """Wave 1 #14: a disambiguated title, a mobile link, a fragment, a markdown link."""
        for text, title in (
                ("https://en.wikipedia.org/wiki/Function_(mathematics).", "Function_(mathematics)"),
                ("[x](https://en.wikipedia.org/wiki/Ring_(mathematics))", "Ring_(mathematics)"),
                ("https://en.m.wikipedia.org/wiki/Widget_lemma", "Widget_lemma"),
                ("https://en.wikipedia.org/wiki/Widget_lemma#Proof", "Widget_lemma")):
            with self.subTest(text=text):
                self.assertEqual(ingest.wikipedia_urls(text),
                                 [f"https://en.wikipedia.org/wiki/{title}"])

    def test_stripping_removes_the_whole_link(self):
        self.assertEqual(ingest.strip_wikipedia_urls(
            "See https://en.m.wikipedia.org/wiki/Ring_(mathematics)#Def, then x."),
            "See , then x.")
        self.assertEqual(ingest.strip_wikipedia_urls("[r](https://en.wikipedia.org/wiki/R) ok"),
                         "[r]() ok")
        self.assertEqual(ingest.strip_wikipedia_urls("no link"), "no link")
        self.assertIsNone(ingest.strip_wikipedia_urls(None))

    def test_other_wikis_and_none(self):
        self.assertEqual(ingest.wikipedia_urls("https://de.wikipedia.org/wiki/Satz_von_Sylow"),
                         ["https://de.wikipedia.org/wiki/Satz_von_Sylow"])
        self.assertEqual(ingest.wikipedia_urls("https://example.org/wiki/X and no link"), [])
        self.assertEqual(ingest.wikipedia_urls("https://en.wikipedia.org/wiki/"), [])


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
                                 "declaration": "Widget.exists_spanning_gadget",
                                 "path": "Alpha/Widget.lean", "line": 11, "tag": "wikipedia",
                                 "value": "https://en.wikipedia.org/wiki/Widget_lemma"}])

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


class TestTheToolsReadOnlyThePinnedDatabase(unittest.TestCase):
    """A second or abbreviated --db never overrides the allowlisted one, and in a gate
    directory the tools read only the database its gate.json pins."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        universe = make_universe(cls.root)
        udir = cls.root / "corpora" / "u1"
        ingest.ingest(universe, "alpha", write_probe(cls.root / "pa", alpha_records()),
                      udir / "index" / "alpha")
        cls.full = udir / "corpus.sqlite"
        cls.blind = udir / "corpus.blind.sqlite"
        build_db.build(universe, udir, cls.full)
        build_db.build(universe, udir, cls.blind, blind=True)
        cls.loose = cls.root / "loose"
        cls.gate = cls.root / "gate"
        for directory in (cls.loose, cls.gate):
            directory.mkdir()
            for name in ("corpus_search.py", "corpus_show.py"):
                shutil.copy2(SCRIPTS / "corpus" / name, directory / name)
        (cls.gate / "gate.json").write_text(json.dumps(
            {"schema": "cf-gate-locate/1",
             "tools_corpus_db": {"path": str(cls.blind), "sha256": "0" * 64, "kind": "blind"}}),
            encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def tool(self, where, name, *args):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        return subprocess.run([sys.executable, "-I", str(where / name), *args],
                              capture_output=True, text=True, cwd=str(self.root), env=env)

    SHOW = ("alpha", "Widget.exists_spanning_gadget")

    def test_a_repeated_or_abbreviated_db_is_refused(self):
        for where in (self.loose, self.gate):
            for argv in (("--db", str(self.blind), "--db", str(self.full)),
                         (f"--db={self.blind}", f"--db={self.full}"),
                         ("--db", str(self.blind), "--d", str(self.full)),
                         ("--d", str(self.full))):
                with self.subTest(where=where.name, argv=argv):
                    proc = self.tool(where, "corpus_show.py", *argv, *self.SHOW)
                    self.assertEqual((proc.returncode, proc.stdout), (2, ""), proc.stderr)
                    proc = self.tool(where, "corpus_search.py", *argv, "widget")
                    self.assertEqual((proc.returncode, proc.stdout), (2, ""), proc.stderr)

    def test_in_a_gate_directory_only_the_pinned_database(self):
        proc = self.tool(self.gate, "corpus_show.py", "--db", str(self.full), *self.SHOW)
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertIn("not this run's tools database", proc.stderr)
        proc = self.tool(self.gate, "corpus_search.py", "--db", str(self.full), "widget")
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        proc = self.tool(self.gate, "corpus_show.py", "--db", str(self.blind), *self.SHOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["tags"], [])
        proc = self.tool(self.loose, "corpus_show.py", "--db", str(self.full), *self.SHOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["tags"])

    def test_a_gate_json_that_pins_nothing_is_the_hosts_problem(self):
        gate = self.root / "gate-unpinned"
        shutil.copytree(self.loose, gate)
        (gate / "gate.json").write_text(json.dumps({"schema": "cf-gate-locate/1"}),
                                        encoding="utf-8")
        proc = self.tool(gate, "corpus_show.py", "--db", str(self.blind), *self.SHOW)
        self.assertEqual((proc.returncode, proc.stdout), (3, ""))
        self.assertIn("pins no tools database", proc.stderr)


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

    def add_alpha(self, *extra):
        self.cf("corpus", "add", "u1", "--library", "alpha", "--repo", "https://example.org/alpha",
                "--commit", ALPHA, "--toolchain", TOOLCHAIN, "--roots", "Alpha", *extra)

    def test_probe_command_prints_the_pipeline_and_runs_nothing(self):
        self.cf("corpus", "init", "u1")
        self.add_alpha()
        with mock.patch("subprocess.run") as ran, mock.patch("subprocess.Popen") as popen:
            out = self.cf("corpus", "probe-command", "u1", "--library", "alpha")
            canary = self.cf("corpus", "probe-command", "u1", "--canary", "--toolchain",
                             TOOLCHAIN)
        ran.assert_not_called()
        popen.assert_not_called()
        for needle in ("MIN_FREE_GB=450", "65536", "build_universe_host.sh", "cf corpus ingest u1",
                       ALPHA):
            with self.subTest(needle=needle):
                self.assertIn(needle, out)
        self.assertIn('cp -R "$CF_DRIVERS/templates/corpus/canary/." "$ATLAS_ROOT/canary/4.34.0"/',
                      canary)
        self.assertIn("cf corpus ingest u1 --canary", canary)

    def test_atlas_root_is_set_once_so_the_cleanup_never_names_root(self):
        """Review #51: an unset ATLAS_ROOT made step 4 `rm -rf /libs/<id>/.lake`."""
        self.cf("corpus", "init", "u1")
        self.add_alpha()
        out = self.cf("corpus", "probe-command", "u1", "--library", "alpha")
        lines = out.splitlines()
        export = next(i for i, l in enumerate(lines) if l.startswith("export ATLAS_ROOT="))
        self.assertEqual(lines[export], 'export ATLAS_ROOT="${ATLAS_ROOT:-$HOME/atlas}"')
        first_use = next(i for i, l in enumerate(lines)
                         if "$ATLAS_ROOT" in l and not l.startswith("#") and i != export)
        self.assertLess(export, first_use)
        self.assertIn('[ -n "$ATLAS_ROOT" ] && rm -rf -- "$ATLAS_ROOT/libs/alpha/.lake"', out)
        # and the line does what it says in a shell with ATLAS_ROOT unset
        home = self.root / "home"
        (home / "atlas" / "libs" / "alpha" / ".lake").mkdir(parents=True)
        script = "\n".join([lines[export],
                             next(l for l in lines if "rm -rf" in l)])
        subprocess.run(["bash", "-c", script], check=True,
                       env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home)})
        self.assertFalse((home / "atlas" / "libs" / "alpha" / ".lake").exists())
        given = self.cf("corpus", "probe-command", "u1", "--library", "alpha",
                        "--atlas-root", "/srv/atlas root")
        self.assertIn("export ATLAS_ROOT='/srv/atlas root'", given)

    def test_a_toolchain_that_needs_quoting_is_refused(self):
        """Review #52: the toolchain reached a single-quoted sed expression raw."""
        self.cf("corpus", "init", "u1")
        evil = "leanprover/lean4:v4.34.0'; touch /tmp/PWNED; '"
        with self.assertRaises(CfError):
            cf_main.main(["corpus", "probe-command", "u1", "--canary", "--toolchain", evil,
                          "--workspace", str(self.ws)])
        with self.assertRaises(CfError):
            cf_main.main(["corpus", "add", "u1", "--library", "alpha", "--repo",
                          "https://example.org/alpha", "--commit", ALPHA, "--toolchain", evil,
                          "--roots", "Alpha", "--workspace", str(self.ws)])
        nightly = self.cf("corpus", "probe-command", "u1", "--canary", "--toolchain",
                          "leanprover/lean4-nightly:nightly-2026-10-01")
        self.assertIn('"$ATLAS_ROOT/canary/leanprover-lean4-nightly-nightly-2026-10-01"', nightly)
        self.assertNotIn("'$ATLAS_ROOT", nightly)
        sed = next(l for l in nightly.splitlines() if "sed -e" in l)
        quoted = sed.split("sed -e ", 1)[1].split(' "$f"', 1)[0]
        self.assertEqual(shlex.split(quoted), [
            "s#@@TOOLCHAIN@@#leanprover/lean4-nightly:nightly-2026-10-01#;"
            "s#@@MODULE_HEADER@@#module\\n\\npublic section#;s#@@MODULE_FOOTER@@#end#"])

    def test_the_canary_build_runs_clean_and_niced(self):
        """Review #54: a bare `lake build` in the operator's shell."""
        self.cf("corpus", "init", "u1")
        canary = self.cf("corpus", "probe-command", "u1", "--canary", "--toolchain", TOOLCHAIN)
        build = next(l for l in canary.splitlines() if l.rstrip().endswith("lake build"))
        self.assertTrue(build.startswith("env -i "), build)
        self.assertIn("nice -n", build)
        self.assertIn("ionice -c3", build)

    def test_a_refused_add_leaves_the_conventions_alone(self):
        """Review #40, #47: the copy came before the validation."""
        self.cf("corpus", "init", "u1")
        first = self.root / "first.md"
        first.write_text("confirmed_by: the maintainer\n0 ∈ ℕ\n", encoding="utf-8")
        self.add_alpha("--conventions", str(first))
        dest = self.ws / "corpora" / "u1" / "conventions" / "alpha.md"
        self.assertEqual(dest.read_text(encoding="utf-8"), first.read_text(encoding="utf-8"))
        draft = self.root / "draft.md"
        draft.write_text("a draft\n", encoding="utf-8")
        with self.assertRaises(CfError):          # a new pin: refused
            cf_main.main(["corpus", "add", "u1", "--library", "alpha", "--repo",
                          "https://example.org/alpha", "--commit", "c" * 40, "--toolchain",
                          TOOLCHAIN, "--roots", "Alpha", "--conventions", str(draft),
                          "--workspace", str(self.ws)])
        self.assertEqual(dest.read_text(encoding="utf-8"), first.read_text(encoding="utf-8"))
        with self.assertRaises(CfError):          # a valid re-add may not overwrite either
            cf_main.main(["corpus", "add", "u1", "--library", "alpha", "--repo",
                          "https://example.org/alpha", "--commit", ALPHA, "--toolchain",
                          TOOLCHAIN, "--roots", "Alpha", "--conventions", str(draft),
                          "--workspace", str(self.ws)])
        self.assertIn("confirmed", self.cf("corpus", "status", "u1"))
        with self.assertRaises(CfError):          # nor does an add that fails on its roots
            cf_main.main(["corpus", "add", "u1", "--library", "beta", "--repo",
                          "https://example.org/beta", "--commit", BETA, "--toolchain", TOOLCHAIN,
                          "--roots", "Beta Gamma", "--conventions", str(draft),
                          "--workspace", str(self.ws)])
        self.assertFalse((dest.parent / "beta.md").exists())

    def test_ingest_accepts_a_published_index_only_by_name(self):
        self.cf("corpus", "init", "u1")
        self.add_alpha()
        source = write_probe(self.root / "pub", TestPublishedIndexes.PUBLISHED,
                             schema="autoform-library-index/v0",
                             modules=(("Alpha.Widget", None),))
        with self.assertRaises(CfError):
            cf_main.main(["corpus", "ingest", "u1", "--library", "alpha", "--from", str(source),
                          "--workspace", str(self.ws)])
        out = self.cf("corpus", "ingest", "u1", "--library", "alpha", "--from", str(source),
                      "--accept-published", "the library's CI publishes it")
        self.assertIn("flagged published_status", out)
        self.assertIn("accepted on trust", out)
        self.assertNotIn("isProp failed", out)

    def test_r2_the_licence_a_first_add_left_out_is_recorded_by_a_re_add(self):
        self.cf("corpus", "init", "u1", "--frozen", "2026-10-17")
        pin = ["corpus", "add", "u1", "--library", "beta", "--repo", "https://example.org/beta",
               "--commit", BETA, "--toolchain", TOOLCHAIN, "--roots", "Beta"]
        out = self.cf(*pin)
        self.assertIn("records no licence", out)
        path = self.ws / "corpora" / "u1" / "universe.yaml"
        self.assertIsNone(Universe.load(path).library("beta")["licence"])
        out = self.cf(*pin, "--licence", "Apache-2.0")
        self.assertNotIn("records no licence", out)
        self.assertEqual(Universe.load(path).library("beta")["licence"], {"spdx": "Apache-2.0"})
        with self.assertRaises(CfError):
            cf_main.main([*pin, "--licence", "MIT", "--workspace", str(self.ws)])
        self.assertEqual(Universe.load(path).library("beta")["licence"], {"spdx": "Apache-2.0"})

    def test_add_without_its_pin_is_refused(self):
        self.cf("corpus", "init", "u1")
        with self.assertRaises(CfError):
            cf_main.main(["corpus", "add", "u1", "--library", "alpha",
                          "--workspace", str(self.ws)])


class TestOneWordRule(unittest.TestCase):
    """Review #36, #45: a name and a query are split by one rule, the one section 8 states."""

    def test_names_and_queries_split_alike(self):
        for name, words in (("Polynomial.natDegree", "polynomial nat degree"),
                            ("orderOf", "order of"), ("IsCompact", "is compact"),
                            ("T2Space", "t2 space"), ("IsPGroup.card_eq", "is pgroup card eq"),
                            ("foo'", "foo"), ("Nat.Prime.two_le", "nat prime two le")):
            with self.subTest(name=name):
                self.assertEqual(corpus_search.name_words(name), words)
                self.assertEqual(corpus_search.query_words(name), words.split())

    def test_section_8_states_the_rule_the_code_runs(self):
        documents = " ".join((SKILL / "references" / "locate-documents.md").read_text(
            encoding="utf-8").split())
        self.assertIn("a run of letters and digits", documents)
        self.assertIn("a lower-case letter or a digit is followed by an upper-case one", documents)
        self.assertIn("queries are split by the same rule", documents)

    def test_a_camel_case_query_finds_the_name(self):
        root = _scratch(self)
        universe = make_universe(root)
        udir = root / "corpora" / "u1"
        ingest.ingest(universe, "alpha", write_probe(root / "p", [
            decl("Polynomial.natDegree", "def", "definition", signature="ℕ"),
            decl("Monoid.orderOf", "def", "definition", signature="ℕ")]),
            udir / "index" / "alpha")
        build_db.build(universe, udir, udir / "corpus.sqlite")
        conn = query.open_db(udir / "corpus.sqlite")
        self.addCleanup(conn.close)
        self.assertEqual(query.search(conn, "natDegree")[0]["declaration"], "Polynomial.natDegree")
        self.assertEqual(query.search(conn, "orderOf")[0]["declaration"], "Monoid.orderOf")

    def test_a_pasted_paragraph_is_cheap(self):
        """Review #42: deduplication was quadratic in the query length."""
        text = " ".join(f"word{i}" for i in range(60000))
        started = time.monotonic()
        words = corpus_search.query_words(text)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(words), corpus_search.MAX_WORDS)


class TestTheHostToolsRefuseBadBytes(unittest.TestCase):
    """Review #41: a non-UTF-8 argument is exit 2, never a traceback with exit 1."""

    def test_surrogates_are_bad_arguments(self):
        root = _scratch(self)
        universe = make_universe(root)
        udir = root / "corpora" / "u1"
        ingest.ingest(universe, "alpha", write_probe(root / "p", alpha_records()),
                      udir / "index" / "alpha")
        build_db.build(universe, udir, udir / "corpus.sqlite")
        db = str(udir / "corpus.sqlite")
        for name, argv in (("corpus_show.py", ["--db", db, "alpha", b"\xff\xfe"]),
                           ("corpus_search.py", ["--db", db, "--library", b"\xff", "gadget"]),
                           ("corpus_search.py", ["--db", db, b"gad\xffget"])):
            with self.subTest(tool=name, argv=argv):
                proc = subprocess.run([sys.executable, "-I", str(SCRIPTS / "corpus" / name),
                                       *[a if isinstance(a, bytes) else a.encode() for a in argv]],
                                      capture_output=True)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertNotIn(b"Traceback", proc.stderr)


class TestTheHostDrivers(unittest.TestCase):
    """Review #46, #48, #49, #50 -- changes to the drivers as run."""

    DRIVER = SCRIPTS / "corpus" / "build_universe_host.sh"

    def test_src_dir_reaches_the_probe(self):
        text = self.DRIVER.read_text(encoding="utf-8")
        call = text[text.index('cenv SHARD_COUNT="$OPT_SHARD_COUNT"'):]
        call = call[:call.index('bash "$probe_host"')]
        self.assertIn('SRC_DIRS="$OPT_SRC_DIR"', call)
        self.assertIn('--src-dirs "${SRC_DIRS:-.}"',
                      (SCRIPTS / "corpus" / "probe_host.sh").read_text(encoding="utf-8"))

    def test_index_ok_re_probes_an_index_the_old_driver_left_pathless(self):
        root = _scratch(self)
        tsv = root / "u1.tsv"
        tsv.write_text(f"alpha\thttps://example.org/alpha\t{ALPHA}\tAlpha\tbuild\tSRC_DIR=src\n",
                       encoding="utf-8")
        entry = universe_host.get_entry(tsv, "alpha")
        out = root / "index"
        out.mkdir()
        (out / "index.jsonl").write_text("{}\n", encoding="utf-8")
        manifest = {"library": "alpha", "commit": ALPHA, "index_bytes": 3,
                    "records": {"header": 1, "module": 4, "declaration": 9},
                    "modules_without_path": 4, "roots": ["Alpha"], "module_prefixes": ["Alpha"]}
        (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        rc, why = universe_host.index_ok(entry, ALPHA, out, None, None)
        self.assertEqual(rc, 2, why)
        self.assertIn("SRC_DIR", why)
        manifest["modules_without_path"] = 0
        (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertNotIn("SRC_DIR", universe_host.index_ok(entry, ALPHA, out, None, None)[1])

    def run_avail(self, atlas: Path, home: Path, df_values: dict) -> str:
        """The driver's own avail_gb, extracted and run against a fake df."""
        text = self.DRIVER.read_text(encoding="utf-8")
        body = text[text.index("avail_gb() {"):]
        body = body[:body.index("\n}\n") + 3]
        bindir = atlas.parent / "bin"
        bindir.mkdir(exist_ok=True)
        cases = "".join(f'    {path}) echo " {gib}G";;\n' for path, gib in df_values.items())
        (bindir / "df").write_text("#!/bin/sh\necho ' Avail'\nfor a in \"$@\"; do\n"
                                   "  case \"$a\" in\n    -*) ;;\n" + cases +
                                   "    *) echo ' 9999G';;\n  esac\ndone\n", encoding="utf-8")
        (bindir / "df").chmod(0o755)
        env = {"PATH": f"{bindir}:/usr/bin:/bin", "ATLAS_ROOT": str(atlas), "HOME": str(home)}
        return subprocess.run(["bash", "-c", body + "\navail_gb"], capture_output=True,
                              text=True, env=env, check=True).stdout.strip()

    def test_the_floor_is_the_fullest_filesystem_a_step_writes(self):
        root = _scratch(self)
        atlas, home = root / "atlas", root / "home"
        atlas.mkdir()
        home.mkdir()
        self.assertEqual(self.run_avail(atlas, home, {str(atlas): 10, "/": 5000}), "10")
        self.assertEqual(self.run_avail(atlas, home, {str(atlas): 800, str(home): 30}), "30")

    def test_ensure_space_stops_when_df_cannot_say(self):
        text = self.DRIVER.read_text(encoding="utf-8")
        body = text[text.index("ensure_space() {"):]
        body = body[:body.index("\n  }\n")]
        self.assertIn("case $avail in ''|*[!0-9]*) stop", body)

    def test_a_floor_that_is_not_a_number_is_refused_before_anything_runs(self):
        root = _scratch(self)
        tsv = root / "u1.tsv"
        tsv.write_text(f"alpha\thttps://example.org/alpha\t{ALPHA}\tAlpha\tbuild\n",
                       encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root),
               "ATLAS_ROOT": str(root / "atlas"), "MIN_FREE_GB": "450G"}
        proc = subprocess.run(["bash", str(self.DRIVER), str(tsv)], capture_output=True,
                              text=True, env=env)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("MIN_FREE_GB must be a whole number", proc.stderr)
        self.assertFalse((root / "atlas").exists())

    def test_the_merge_runs_no_content_filter_of_the_checkout(self):
        """Review #49: `git status` in probe_merge compared content with the filters armed."""
        root = _scratch(self)
        repo, _head = git_checkout(root / "lib", {"a.lean": "theorem a : True := trivial\n"})
        proof = repo / ".git" / "proof.txt"
        script = repo / ".git" / "f.sh"
        script.write_text(f"#!/bin/sh\necho ran > {proof}\ncat\n", encoding="utf-8")
        script.chmod(0o755)
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "-C", str(repo), "config", "filter.x.clean", str(script)],
                       check=True, env=env)
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text("*.lean filter=x\n", encoding="utf-8")
        path = repo / "a.lean"
        path.write_bytes(path.read_bytes())
        stamp = os.stat(path).st_mtime + 5
        os.utime(path, (stamp, stamp))
        self.assertIs(probe_merge._dirty(repo), False)
        self.assertFalse(proof.exists(), "probe_merge ran the checkout's clean filter")
        bare = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                              capture_output=True, text=True, env=env)
        if not proof.exists():                               # pragma: no cover - git build
            self.skipTest("this git does not run a clean filter on `status`")
        self.assertEqual(bare.returncode, 0)

    def test_the_inlined_filter_blanks_are_gitsafes(self):
        names = ["filter.x.clean", "filter.a=b.process", "filter.lfs.smudge"]
        self.assertEqual(probe_merge._filter_pin_args(names), gitsafe.filter_pin_args(names))
        have = {"GIT_CONFIG_COUNT": "3"}
        self.assertEqual(probe_merge._filter_pairs(names, have),
                         gitsafe._filter_pairs(names, have))


class TestRootsAreTheDriversGlobs(unittest.TestCase):
    """Review #53: roots the host driver refuses are refused at `cf corpus add`."""

    def test_universe_and_driver_accept_the_same_roots(self):
        from corpus.universe import ROOT_GLOB

        for root in ("Alpha", "Alpha.+", "Alpha.*", "Mathlib.Tactic", "Foo'", "Beta Gamma",
                     "Alpha.", "", "Alpha.+.x", "1Alpha", "Al-pha"):
            with self.subTest(root=root):
                self.assertEqual(bool(ROOT_GLOB.match(root)),
                                 bool(universe_host._GLOB.fullmatch(root)))

    def test_add_refuses_one(self):
        universe = make_universe(_scratch(self))
        with self.assertRaises(CfError):
            universe.add_library(library_id="beta", repo="https://example.org/beta", commit=BETA,
                                 toolchain=TOOLCHAIN, roots=["Beta Gamma"])


class TestTheProofWantedScanIsMarkedUnbuilt(unittest.TestCase):
    """Review #33: section 2.4's source scan does not exist, and says so where it is read."""

    def test_the_manifest_and_section_8_say_it(self):
        root = _scratch(self)
        universe = make_universe(root)
        manifest = ingest.ingest(universe, "alpha", write_probe(root / "p", alpha_records()),
                                 root / "out")
        self.assertEqual(manifest["proof_wanted_scan"], "not implemented")
        self.assertIn("TODO", ingest.__doc__)
        documents = " ".join((SKILL / "references" / "locate-documents.md").read_text(
            encoding="utf-8").split())
        self.assertIn("source scan for a `proof_wanted` that adds no constant is not "
                      "implemented", documents)


class TestTheConsumerAPI(unittest.TestCase):
    """Wave 1 #13, #16, #19, #23, #24: what the planner and the bench call instead of a
    private reader."""

    def setUp(self):
        self.root = _scratch(self)
        self.universe = make_universe(self.root, libs=("alpha", "beta"))
        self.udir = self.root / "corpora" / "u1"
        records = alpha_records() + [
            decl("Widget.old_lemma", deprecated={"new_name": "Widget.exists_spanning_gadget",
                                                 "since": "2026-01-01"}, line=61, start_line=61)]
        ingest.ingest(self.universe, "alpha", write_probe(self.root / "pa", records),
                      self.udir / "index" / "alpha")
        beta = [decl("Beta.Widget.gadget_of_finite", module="Beta.Widget",
                     signature="∀ (w : Widget), Finite w → ∃ g, g.Spans w",
                     docstring="Finite case of https://en.wikipedia.org/wiki/Widget_lemma ."),
                decl("Beta.spanning", module="Beta.Widget")]
        ingest.ingest(self.universe, "beta",
                      write_probe(self.root / "pb", beta, library="beta", commit=BETA,
                                  modules=(("Beta.Widget", "Beta/Widget.lean"),)),
                      self.udir / "index" / "beta")
        self.db = self.udir / "corpus.sqlite"
        build_db.build(self.universe, self.udir, self.db)

    def connect(self, path=None):
        conn = query.open_db(path or self.db)
        self.addCleanup(conn.close)
        return conn

    def test_all_words_is_an_and_of_the_same_ranking(self):
        conn = self.connect()
        self.assertEqual(corpus_search.fts_match(["a", "b"], all_words=True), '"a" AND "b"')
        any_word = {h["declaration"] for h in query.search(conn, "gadget finite")}
        every = [h["declaration"] for h in query.search(conn, "gadget finite", all_words=True)]
        self.assertEqual(every, ["Beta.Widget.gadget_of_finite"])
        self.assertLess(set(every), any_word)

    def test_the_tag_rows_carry_path_line_and_value(self):
        conn = self.connect()
        url = "https://en.wikipedia.org/wiki/Widget_lemma"
        rows = query.tagged(conn, "wikipedia", url)
        self.assertEqual([(r["library"], r["declaration"]) for r in rows],
                         [("alpha", "Widget.exists_spanning_gadget"),
                          ("beta", "Beta.Widget.gadget_of_finite")])
        self.assertEqual(set(rows[0]), {"library", "commit", "declaration", "path", "line",
                                        "tag", "value"})
        self.assertEqual((rows[0]["path"], rows[0]["line"], rows[0]["value"], rows[0]["commit"]),
                         ("Alpha/Widget.lean", 11, url, ALPHA))
        self.assertEqual(query.tags_of_kind(conn, "wikipedia"), rows)
        self.assertEqual(query.tags_of_kind(conn, "kerodon"), [])

    def test_declaration_by_the_exact_key_and_its_rename(self):
        conn = self.connect()
        row = query.declaration(conn, "alpha", "Widget.old_lemma", commit=ALPHA)
        self.assertEqual(row["deprecated_to"], "Widget.exists_spanning_gadget")
        self.assertIsNone(query.declaration(conn, "alpha", "Widget.old_lemma", commit=BETA))
        self.assertIsNone(query.declaration(conn, "alpha", "Widget.Spans")["deprecated_to"])

    def test_check_universe_refuses_a_stale_or_foreign_database(self):
        conn = self.connect()
        self.assertEqual(query.check_universe(conn, self.universe)["schema"], "cf-corpus/1")
        self.universe.set_index("beta", path="index/beta/", sha256="9" * 64, declarations=2)
        with self.assertRaises(CfError) as caught:
            query.check_universe(conn, self.universe)
        self.assertIn("cf corpus build-db u1", caught.exception.hint)
        other = self.root / "other.sqlite"
        raw = sqlite3.connect(other)
        raw.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        raw.execute("INSERT INTO meta VALUES ('schema', 'something-else/1')")
        raw.commit()
        raw.row_factory = sqlite3.Row
        self.addCleanup(raw.close)
        with self.assertRaises(CfError):
            query.check_universe(raw, self.universe)

    def test_the_blind_database(self):
        blind_path = self.udir / "corpus.blind.sqlite"
        result = build_db.build(self.universe, self.udir, blind_path, blind=True)
        self.assertTrue(result["blind"])
        conn = self.connect(blind_path)
        self.assertTrue(query.is_blind(conn))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM decls").fetchone()[0], 8)
        self.assertNotIn("wikipedia.org",
                         query.declaration(conn, "beta", "Beta.Widget.gadget_of_finite")["docstring"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM decls WHERE docstring LIKE "
                                      "'%wikipedia.org%'").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM decls_fts WHERE docstring LIKE "
                                      "'%wikipedia.org%'").fetchone()[0], 0)
        self.assertEqual(query.tagged(conn, "wikipedia",
                                      "https://en.wikipedia.org/wiki/Widget_lemma"), [])
        # the host tool cannot reach the URL through the docstring column either
        self.assertEqual([h["declaration"] for h in
                          query.search(conn, "wikipedia org wiki", all_words=True)], [])
        query.check_universe(conn, self.universe, blind=True)
        with self.assertRaises(CfError):
            query.check_universe(conn, self.universe, blind=False)
        full = self.connect()
        self.assertFalse(query.is_blind(full))
        with self.assertRaises(CfError):
            query.check_universe(full, self.universe, blind=True)
        recorded = self.universe.corpus_db_blind
        self.assertEqual(recorded["path"], "corpus.blind.sqlite")
        self.assertEqual(recorded["sha256"], hashlib.sha256(blind_path.read_bytes()).hexdigest())
        self.assertEqual(self.universe.corpus_db["path"], "corpus.sqlite")
        self.assertTrue(build_db.current(self.universe, blind_path, blind=True))
        self.assertFalse(build_db.current(self.universe, blind_path, blind=False))
        self.assertFalse(build_db.build(self.universe, self.udir, blind_path, blind=True)["built"])
        self.universe.save()
        self.assertEqual(Universe.load(self.universe.path).corpus_db_blind, recorded)
        # an index change makes both stale
        self.universe.set_index("beta", path="index/beta/", sha256="9" * 64, declarations=2)
        self.assertIsNone(self.universe.corpus_db)
        self.assertIsNone(self.universe.corpus_db_blind)

    def test_the_mathlib_library(self):
        self.assertIsNone(self.universe.mathlib_library())
        self.universe.add_library(library_id="mathlib4", repo="https://example.org/mathlib4",
                                  commit="c" * 40, toolchain=TOOLCHAIN, roots=["Mathlib"],
                                  brain_lib="Mathlib")
        self.assertEqual(self.universe.mathlib_library()["id"], "mathlib4")
        other = make_universe(_scratch(self), libs=("alpha",))
        other.add_library(library_id="mathlib", repo="https://example.org/mathlib",
                          commit="d" * 40, toolchain=TOOLCHAIN, roots=["Mathlib"])
        self.assertEqual(other.mathlib_library()["id"], "mathlib")
        for lib in other.libraries:
            lib["brain_lib"] = "Mathlib"
        with self.assertRaises(CfError):
            other.mathlib_library()


class TestBuildDbBlindCommand(unittest.TestCase):

    def test_the_command_writes_beside_the_full_one(self):
        root = _scratch(self)
        ws = root / "ws"
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            cf_main.main(["init", str(ws), "--no-git", "-q"])

        def cf(*argv):
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                self.assertEqual(cf_main.main([*argv, "--workspace", str(ws)]), 0)
            return out.getvalue()

        cf("corpus", "init", "u1")
        cf("corpus", "add", "u1", "--library", "alpha", "--repo", "https://example.org/alpha",
           "--commit", ALPHA, "--toolchain", TOOLCHAIN, "--roots", "Alpha")
        cf("corpus", "ingest", "u1", "--canary", "--from",
           str(write_probe(root / "canary", canary_records(), library="cf-canary")))
        cf("corpus", "ingest", "u1", "--library", "alpha", "--from",
           str(write_probe(root / "pa", alpha_records())))
        self.assertIn("G1-blind", cf("corpus", "build-db", "u1", "--blind"))
        from cflib.paths import WorkspacePaths
        paths = WorkspacePaths(ws)
        self.assertTrue(paths.corpus_db_blind("u1").is_file())
        self.assertFalse(paths.corpus_db("u1").exists())
        status = cf("corpus", "status", "u1")
        self.assertIn("corpus.blind.sqlite: 5 declarations", status)
        self.assertIn("corpus.sqlite: not built", status)
        cf("corpus", "build-db", "u1")
        data = json.loads(cf("corpus", "status", "u1", "--json"))
        self.assertEqual((data["corpus_db_state"], data["corpus_db_blind_state"]),
                         ("current", "current"))
        hits = [json.loads(line) for line in cf("corpus", "search", "u1", "spanning", "gadget",
                                                "--all-words", "--json").splitlines()]
        self.assertEqual([h["declaration"] for h in hits], ["Widget.exists_spanning_gadget"])


if __name__ == "__main__":
    unittest.main()
