"""Regression tests for the corpus defects found running the drivers and ``cf corpus`` in the field.

New code, for docs/proposals/wikipedia-locate.md sections 2.3-2.5 and
references/locate-documents.md section 8.  Each class is one defect, named by
its field label (D1-D12), and reduces what went wrong to synthetic libraries
(``alpha``, ``beta``, ``esc``) and made-up declarations: no library's real
source, no host.  The helpers are ``tests/test_locate_corpus.py``'s.
"""
from __future__ import annotations

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
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import cf as cf_main  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from corpus import build_db, ingest, probe_merge, query, universe_host  # noqa: E402

from test_locate_corpus import (ALPHA, TOOLCHAIN, decl, git_checkout,  # noqa: E402
                                make_universe, write_probe)

TEMPLATE = SKILL / "templates" / "corpus" / "probe.lean.tmpl"


def _scratch(case) -> Path:
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def shard_files(work: Path, *, roots: list[str], prefixes: list[str],
                modules: list[tuple[str, str | None]], decls: list[dict]) -> None:
    """One probe shard (shard 0 of 1) as the template writes it."""
    work.mkdir(parents=True, exist_ok=True)
    header = {"record": "header", "schema": probe_merge.SCHEMA, "library": "esc",
              "toolchain": "4.34.0", "lean_githash": "f" * 40, "probe_revision": 2,
              "roots": roots, "module_prefixes": prefixes, "module_count": len(modules),
              "shard_index": 0, "shard_count": 1, "shard_hash": "fnv1a64(utf8(name)) mod shard_count"}
    lines = [header]
    lines += [{"record": "module", "name": n, "path": p, "module_doc": None, "truncated": False}
              for n, p in modules]
    for d in decls:
        lines.append({"record": "declaration", "kind": "theorem", "proof_status": "proved",
                      "nonstandard_axioms": [], "private": False, "deprecated": None,
                      "truncated": False, "errors": [], "path": None, **d})
    lines.append({"record": "shard_end", "shard_index": 0, "shard_count": 1, "constants_seen": 9,
                  "declarations": len(decls), "skipped": {}, "closure_nodes": 1, "ms_total": 1,
                  "ms_closure": 0, "ms_print": 0, "ms_other": 0})
    (work / "shard-0.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                                for r in lines), encoding="utf-8")
    (work / "probe.lean").write_text("-- rendered\n", encoding="utf-8")


class TestD3ModuleNamesThatNeedEscaping(unittest.TestCase):
    """D3: a module such as ``Esc.«1»`` was skipped with a warning, and the driver succeeded."""

    TREE = {"Esc.lean": "", "Esc/1.lean": "", "Esc/0805.3777/Tensor.lean": "",
            "Esc/Sub/Plain.lean": "", "Esc/a b.lean": ""}

    def test_expand_roots_escapes_every_component_lake_would_name(self):
        root = write_tree(_scratch(self), self.TREE)
        modules, prefixes = universe_host.expand_roots(root, ["Esc.+"], ".")
        self.assertEqual(modules, sorted(["Esc.«1»", "Esc.«0805.3777».Tensor", "Esc.Sub.Plain",
                                          "Esc.«a b»"]))
        self.assertEqual(prefixes, ["Esc"])
        self.assertEqual(universe_host.expand_roots(root, ["Esc.*"], ".")[0],
                         sorted(modules + ["Esc"]))

    def test_a_file_no_module_name_expresses_fails_the_expansion(self):
        root = write_tree(_scratch(self), {**self.TREE, "Esc/«odd».lean": ""})
        with self.assertRaises(universe_host.UniverseError) as caught:
            universe_host.expand_roots(root, ["Esc.+"], ".")
        self.assertIn("«odd».lean", str(caught.exception))
        out = root / "roots"
        proc = subprocess.run([sys.executable, "-I", str(SCRIPTS / "corpus" / "universe_host.py"),
                               "expand-roots", "--checkout", str(root), "--roots", "Esc.+",
                               "--out", str(out), "--prefixes-out", str(root / "prefixes")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertFalse(out.exists())

    def test_the_host_script_finds_its_sibling_under_python_dash_i(self):
        root = write_tree(_scratch(self), self.TREE)
        proc = subprocess.run([sys.executable, "-I", str(SCRIPTS / "corpus" / "universe_host.py"),
                               "expand-roots", "--checkout", str(root), "--roots", "Esc.+",
                               "--out", str(root / "roots"), "--prefixes-out",
                               str(root / "prefixes")], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Esc.«0805.3777».Tensor", (root / "roots").read_text(encoding="utf-8"))

    def test_render_writes_escaped_imports_and_refuses_what_would_break_out(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        text = probe_merge.render(template, "esc", ["Esc.«1»", "Esc.«0805.3777».Tensor"],
                                  ["Esc"], ["."])
        self.assertIn("import Esc.«1»\n", text)
        self.assertIn("import Esc.«0805.3777».Tensor\n", text)
        for bad in ("Esc.«1", "Esc.«a»b", "Esc.«x\n#eval IO.println 1»", "Esc..A", "Esc.",
                    "Esc.1", "«»"):
            with self.subTest(name=bad), self.assertRaises(probe_merge.ProbeError):
                probe_merge.render(template, "esc", [bad], ["Esc"], ["."])

    def test_components_round_trip(self):
        for name, parts in (("Esc.«0805.3777».Tensor", ("Esc", "0805.3777", "Tensor")),
                            ("Esc.«1»", ("Esc", "1")), ("Mathlib", ("Mathlib",)),
                            ("Foo.«ö»", ("Foo", "ö"))):
            with self.subTest(name=name):
                self.assertEqual(probe_merge.module_components(name), parts)
                self.assertEqual(probe_merge.module_components(probe_merge.module_name(parts)),
                                 parts)
        self.assertEqual(probe_merge.module_file("Esc.«0805.3777».Tensor"),
                         "Esc/0805.3777/Tensor.lean")
        # Lean prints `Foo.ö` plain and the driver writes `Foo.«ö»`: one module either way,
        # but only the escaped spelling is one the driver itself writes into an import
        self.assertEqual(probe_merge.module_components("Foo.ö"),
                         probe_merge.module_components("Foo.«ö»"))
        self.assertFalse(probe_merge.module_ok("Foo.ö"))
        self.assertTrue(probe_merge.module_ok("Foo.«ö»"))
        self.assertIsNone(probe_merge.module_components("Foo.a b"))

    def test_a_list_keeps_an_escaped_name_with_a_separator_whole(self):
        root = _scratch(self)
        listing = root / "roots"
        listing.write_text("Esc.«a, b»\nEsc.«c d».E\n", encoding="utf-8")
        self.assertEqual(probe_merge.split_list(f"@{listing}"), ["Esc.«a, b»", "Esc.«c d».E"])
        self.assertEqual(probe_merge.split_list("A, B.«x y»,C"), ["A", "B.«x y»", "C"])

    def test_the_merge_refuses_a_root_it_did_not_index(self):
        root = _scratch(self)
        checkout, _head = git_checkout(root / "esc", {"Esc/1.lean": ""})
        (checkout / "lean-toolchain").write_text("leanprover/lean4:v4.34.0\n", encoding="utf-8")
        args = dict(count=1, checkout=checkout, template=TEMPLATE, parallel=1,
                    started="2026-10-17T00:00:00Z")
        shard_files(root / "ok", roots=["Esc.«1»"], prefixes=["Esc"],
                    modules=[("Esc.«1»", "Esc/1.lean")],
                    decls=[{"name": "Esc.one", "module": "Esc.«1»"}])
        manifest = probe_merge.merge(root / "ok", out_dir=root / "out-ok",
                                     probe=root / "ok" / "probe.lean", **args)
        self.assertEqual(manifest["records"]["module"], 1)
        shard_files(root / "short", roots=["Esc.«1»", "Esc.«2»"], prefixes=["Esc"],
                    modules=[("Esc.«1»", "Esc/1.lean")],
                    decls=[{"name": "Esc.one", "module": "Esc.«1»"}])
        with self.assertRaises(probe_merge.ProbeError) as caught:
            probe_merge.merge(root / "short", out_dir=root / "out-short",
                              probe=root / "short" / "probe.lean", **args)
        self.assertIn("Esc.«2»", str(caught.exception))


class TestD4TheTemplateFindsEscapedModulesSources(unittest.TestCase):
    """D4: the template built a source path from escaped components (``Esc/«1».lean``)."""

    def test_the_path_is_built_from_unescaped_components(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("c.toString (escape := false)", text)
        self.assertIn("let rel := moduleFile m", text)
        self.assertNotIn("m.components.map toString", text)

    def test_prefixes_are_parsed_with_their_escapes(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        body = text[text.index("def parseName"):text.index("def moduleFile")]
        self.assertIn("'«'", body)
        self.assertIn("'»'", body)
        self.assertNotIn('splitOn "."', body)

    def test_the_revision_says_the_bytes_changed(self):
        self.assertIn("def probeRevision : Nat := 2", TEMPLATE.read_text(encoding="utf-8"))


def run_cf(*argv) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = cf_main.main(list(argv))
    return code, buf.getvalue()


class TestD1AUniverseOutsideAnyWorkspace(unittest.TestCase):
    """D1: a universe directory on the host (``<base>/u1/``) could not be addressed at all."""

    def setUp(self):
        self.root = _scratch(self)
        self.base = self.root / "corpus"
        self.base.mkdir()
        # a --workspace that does not exist: any subcommand that read a workspace would fail
        self.nowhere = ["--workspace", str(self.root / "no-workspace-here")]

    def cf(self, *argv) -> str:
        code, out = run_cf(*argv, "--base", str(self.base), *self.nowhere)
        self.assertEqual(code, 0, out)
        return out

    def test_every_subcommand_works_on_the_base_alone(self):
        from test_locate_corpus import alpha_records, canary_records
        self.cf("corpus", "init", "u1", "--frozen", "2026-10-17")
        self.assertTrue((self.base / "u1" / "universe.yaml").is_file())
        self.cf("corpus", "add", "u1", "--library", "alpha", "--repo", "https://example.org/alpha",
                "--commit", ALPHA, "--toolchain", TOOLCHAIN, "--roots", "Alpha")
        self.cf("corpus", "add", "u1", "--library", "gamma", "--exclude", "a private repository")
        self.assertIn("build_universe_host.sh",
                      self.cf("corpus", "probe-command", "u1", "--library", "alpha"))
        canary = write_probe(self.root / "canary", canary_records(), library="cf-canary")
        self.assertIn("PASSED", self.cf("corpus", "ingest", "u1", "--canary", "--from",
                                        str(canary)))
        self.cf("corpus", "ingest", "u1", "--library", "alpha", "--from",
                str(write_probe(self.root / "pa", alpha_records())))
        self.assertTrue((self.base / "u1" / "index" / "alpha" / "decls.jsonl").is_file())
        self.cf("corpus", "build-db", "u1")
        self.cf("corpus", "build-db", "u1", "--blind")
        self.assertTrue((self.base / "u1" / "corpus.sqlite").is_file())
        self.assertTrue((self.base / "u1" / "corpus.blind.sqlite").is_file())
        status = json.loads(self.cf("corpus", "status", "u1", "--json"))
        self.assertEqual(status["corpus_db_state"], "current")
        hits = [json.loads(l) for l in self.cf("corpus", "search", "u1", "spanning",
                                               "--json").splitlines()]
        self.assertEqual(hits[0]["declaration"], "Widget.exists_spanning_gadget")
        self.assertIn("axiom_dependent", self.cf("corpus", "show", "u1", "alpha",
                                                 "Widget.bigAxiomThm"))
        self.assertFalse((self.root / "no-workspace-here").exists())

    def test_one_layout_for_a_workspace_and_a_base(self):
        from cflib.paths import CorporaPaths, WorkspacePaths
        ws = WorkspacePaths(self.root / "ws")
        base = CorporaPaths(self.root / "ws" / "corpora")
        for method, args in (("universe_dir", ("u1",)), ("universe_yaml", ("u1",)),
                             ("universe_index_dir", ("u1", "alpha")),
                             ("universe_conventions_md", ("u1", "alpha")),
                             ("corpus_db", ("u1",)), ("corpus_db_blind", ("u1",))):
            with self.subTest(method=method):
                self.assertEqual(getattr(ws, method)(*args), getattr(base, method)(*args))
        with self.assertRaises(CfError):
            base.universe_dir("../escape")

    def test_a_base_in_the_skill_repo_or_missing_is_refused(self):
        for base in (SKILL, SKILL / "templates", self.root / "missing"):
            with self.subTest(base=base), self.assertRaises(CfError):
                cf_main.main(["corpus", "init", "u1", "--base", str(base), *self.nowhere])
        self.assertFalse((SKILL / "templates" / "u1").exists())


class TestD2AutoformsCompleteIsMappedByKind(unittest.TestCase):
    """D2: every record of a published index whose words are ``complete``/``wanted`` was refused."""

    RECORDS = [
        {"record": "declaration", "name": "P.lemma_one", "module": "Alpha.Widget",
         "kind": "lemma", "status": "complete", "signature": "1 = 1"},
        {"record": "declaration", "name": "P.thm", "module": "Alpha.Widget",
         "kind": "theorem", "status": "complete", "signature": "True"},
        {"record": "declaration", "name": "P.fn", "module": "Alpha.Widget",
         "kind": "def", "status": "complete", "signature": "ℕ → ℕ"},
        {"record": "declaration", "name": "P.IsGood", "module": "Alpha.Widget",
         "kind": "def", "status": "complete", "signature": "ℕ → Prop"},
        {"record": "declaration", "name": "P.inst", "module": "Alpha.Widget",
         "kind": "instance", "status": "complete", "signature": "Inhabited ℕ"},
        {"record": "declaration", "name": "P.Shape", "module": "Alpha.Widget",
         "kind": "structure", "status": "complete", "signature": "Type"},
        {"record": "declaration", "name": "P.wanted", "module": "Alpha.Widget",
         "kind": "theorem", "status": "wanted", "signature": "False"},
    ]

    def setUp(self):
        self.root = _scratch(self)
        self.universe = make_universe(self.root, canary=False)
        self.out = self.root / "corpora" / "u1" / "index" / "alpha"

    def ingest(self, records):
        source = write_probe(self.root / f"pub{len(list(self.root.iterdir()))}", records,
                             schema="autoform-library-index/v0",
                             modules=(("Alpha.Widget", None),))
        ingest.ingest(self.universe, "alpha", source, self.out,
                      accept_published="the library's CI publishes it")
        return {r["name"]: r for r in (json.loads(line) for line in
                (self.out / "decls.jsonl").read_text(encoding="utf-8").splitlines())}

    def test_complete_is_proved_for_a_theorem_and_a_definition_otherwise(self):
        rows = self.ingest(self.RECORDS)
        want = {"P.lemma_one": "proved", "P.thm": "proved", "P.fn": "definition",
                "P.IsGood": "definition", "P.inst": "definition", "P.Shape": "definition",
                "P.wanted": "wanted"}
        self.assertEqual({n: r["proof_status"] for n, r in rows.items()}, want)
        self.assertTrue(rows["P.IsGood"]["statement"])
        self.assertFalse(rows["P.fn"]["statement"])
        for row in rows.values():
            self.assertEqual(row["flags"], ["published_status"])
        self.assertEqual(rows["P.lemma_one"]["probe_proof_status"], "complete")

    def test_lemma_is_the_probes_theorem(self):
        rows = self.ingest(self.RECORDS)
        self.assertEqual(rows["P.lemma_one"]["kind"], "theorem")
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("lemma", manifest["by_kind"])

    def test_complete_on_a_kind_it_cannot_decide_is_refused(self):
        for kind in ("axiom", "", "example"):
            with self.subTest(kind=kind), self.assertRaises(CfError) as caught:
                self.ingest([{"record": "declaration", "name": "P.x", "module": "Alpha.Widget",
                              "kind": kind, "status": "complete"}])
            self.assertIn("complete", str(caught.exception))


DRIVER = SCRIPTS / "corpus" / "build_universe_host.sh"


def driver_functions(*, start: str = "cenv() {", end: str = "py() {") -> str:
    """The driver's own shell functions from ``start`` up to ``end``, to run alone."""
    text = DRIVER.read_text(encoding="utf-8")
    return text[text.index(start):text.index(end)]


class TestD11TheDriversGitCarriesGitsafesPins(unittest.TestCase):
    """D11: ``g()`` ran git on third-party checkouts without the gitsafe pins."""

    def run_g(self, *, filters: tuple = (), version: str = "2.43.0", args=("status",)):
        from cflib import gitsafe
        root = _scratch(self)
        home = root / "home"
        bindir = home / ".elan" / "bin"
        bindir.mkdir(parents=True)
        log = root / "git.log"
        checkout = root / "lib"
        (checkout / ".git").mkdir(parents=True)
        listing = "".join(f"{name}\n" for name in ("core.bare", *filters))
        (bindir / "git").write_text(
            "#!/bin/sh\n"
            'for a in "$@"; do case "$a" in --version) echo "git version ' + version + '"; exit 0;; '
            "config) printf '%s' '" + listing + "'; exit 0;; esac; done\n"
            f'{{ printf "ARG %s\\n" "$@"; env | sort | sed "s/^/ENV /"; }} > "{log}"\n',
            encoding="utf-8")
        (bindir / "ionice").write_text('#!/bin/sh\nshift\nexec "$@"\n', encoding="utf-8")
        for stub in ("git", "ionice"):
            (bindir / stub).chmod(0o755)
        script = ("USER_NAME=t\nNICE_LEVEL=10\n" + driver_functions()
                  + f'\ng -C "{checkout}" ' + " ".join(args) + "\n")
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        argv = [line[4:] for line in lines if line.startswith("ARG ")]
        env = dict(line[4:].split("=", 1) for line in lines if line.startswith("ENV "))
        return proc, argv, env, gitsafe

    def test_the_fixed_pins_and_the_environment_are_gitsafes(self):
        proc, argv, env, gitsafe = self.run_g()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(argv[:len(gitsafe.SAFE_CONFIG)], list(gitsafe.SAFE_CONFIG))
        for key, value in gitsafe.SAFE_ENV.items():
            self.assertEqual(env.get(key), value, key)
        self.assertEqual(argv[-3:], ["-C", argv[-2], "status"])

    def test_every_content_filter_of_the_checkout_is_blanked_both_ways(self):
        proc, argv, env, _gs = self.run_g(filters=("filter.x.clean", "filter.a=b.process"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("filter.x.clean=", argv)
        self.assertNotIn("filter.a=b.process=", argv)       # -c cannot carry that one
        self.assertEqual(env.get("GIT_CONFIG_COUNT"), "2")
        keys = {env.get("GIT_CONFIG_KEY_0"), env.get("GIT_CONFIG_KEY_1")}
        self.assertEqual(keys, {"filter.x.clean", "filter.a=b.process"})
        self.assertEqual(env.get("GIT_CONFIG_VALUE_0"), "")

    def test_an_unblankable_filter_on_an_old_git_stops_the_call(self):
        proc, argv, _env, _gs = self.run_g(filters=("filter.a=b.smudge",), version="2.30.2",
                                           args=("checkout", "--detach", ALPHA))
        self.assertEqual(proc.returncode, 126)
        self.assertIn("filter.a=b.smudge", proc.stderr)
        self.assertEqual(argv, [], "git ran although the filter could not be blanked")

    def test_the_published_index_is_hashed_without_filters(self):
        self.assertIn('g hash-object --no-filters "$stage/$idx"', DRIVER.read_text(encoding="utf-8"))


def modern_bash() -> str | None:
    """A bash that runs the drivers (``mapfile``, ``wait -n -p``: 5.1 or later), or None."""
    for candidate in (shutil.which("bash"), "/bin/bash", "/usr/bin/bash", "/opt/homebrew/bin/bash",
                      "/usr/local/bin/bash"):
        if not candidate or not os.path.exists(candidate):
            continue
        got = subprocess.run([candidate, "-c", "echo ${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"],
                             capture_output=True, text=True)
        try:
            major, minor = (int(x) for x in got.stdout.split())
        except ValueError:
            continue
        if (major, minor) >= (5, 1):
            return candidate
    return None


GITHASH = "c" * 40


class TestD5LibrariesProbedInsideAWorkspace(unittest.TestCase):
    """D5: the drivers knew only build and published-index; Init/Std or Batteries probed in
    Mathlib's workspace got Mathlib's commit and paths null or relative to the wrong tree."""

    def setUp(self):
        self.bash = modern_bash()
        if self.bash is None:
            self.skipTest("the host drivers need bash 5.1 or later")
        self.root = _scratch(self)
        self.home = self.root / "home"
        self.bin = self.home / ".elan" / "bin"
        self.bin.mkdir(parents=True)
        self.atlas = self.root / "atlas"
        self.ws, _ = git_checkout(self.atlas / "libs" / "ws", {"Ws.lean": "", ".gitignore": ".lake/\n"})
        (self.ws / "lean-toolchain").write_text("leanprover/lean4:v4.34.0\n", encoding="utf-8")
        (self.ws / ".lake" / "build").mkdir(parents=True)
        tc = self.home / ".elan" / "toolchains" / "fake"
        write_tree(tc, {"src/lean/Init.lean": "", "src/lean/Init/Core.lean": "",
                        "lib/lean/Init.olean": "", "lib/lean/Init/Core.olean": ""})
        self.stub("lean", f'[ "$1" = --githash ] && echo {GITHASH}', where=tc / "bin")
        self.stub("elan", f'[ "$1 $2" = "which lean" ] && echo "{tc}/bin/lean"')
        self.stub("lake", '[ "$1 $2" = "env true" ] && exit 0\n'
                          '[ "$1 $2" = "env lean" ] && exec cp "$HOME/shard.jsonl" "$PROBE_OUTPUT"\n'
                          "exit 9")
        self.stub("ionice", 'shift; exec "$@"')
        self.stub("flock", "exit 0")
        self.stub("timeout", 'while [ "${1#-}" != "$1" ]; do shift; done; shift; exec "$@"')
        self.stub("df", 'echo " Avail"; for a in "$@"; do case "$a" in -*) ;; *) echo " 9999G";; esac; done')
        # the clean PATH the drivers set starts here: the Linux host's own bash is 5.x
        for tool, target in (("python3", sys.executable), ("git", shutil.which("git")),
                             ("bash", self.bash)):
            (self.bin / tool).symlink_to(target)

    def stub(self, name: str, body: str, where: Path | None = None) -> None:
        where = where or self.bin
        where.mkdir(parents=True, exist_ok=True)
        (where / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (where / name).chmod(0o755)

    def shard(self, *, library: str, roots, prefixes, modules, decls, githash=GITHASH):
        shard_files(self.root / "fixture", roots=roots, prefixes=prefixes, modules=modules,
                    decls=decls)
        lines = (self.root / "fixture" / "shard-0.jsonl").read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        header.update({"library": library, "lean_githash": githash})
        (self.home / "shard.jsonl").write_text("\n".join([json.dumps(header)] + lines[1:]) + "\n",
                                               encoding="utf-8")

    def drive(self, line: str, lib_id: str) -> subprocess.CompletedProcess:
        tsv = self.root / "u1.tsv"
        tsv.write_text(line + "\n", encoding="utf-8")
        env = {"HOME": str(self.home), "USER": "t", "ATLAS_ROOT": str(self.atlas),
               "MIN_FREE_GB": "1", "PATH": f"{self.bin}:/usr/bin:/bin"}
        return subprocess.run([self.bash, str(DRIVER), str(tsv), lib_id], capture_output=True,
                              text=True, env=env, timeout=120)

    def index(self, lib_id: str, commit: str) -> tuple[dict, list[dict]]:
        out = self.atlas / "index" / f"{lib_id}@{commit[:12]}"
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        records = [json.loads(l) for l in (out / "index.jsonl").read_text(encoding="utf-8").splitlines()]
        return manifest, records

    def log_of(self, lib_id: str) -> str:
        path = self.atlas / "logs" / "u1" / f"{lib_id}.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_core_is_pinned_at_the_toolchains_githash_with_lean4_paths(self):
        self.shard(library="lean-core", roots=["Init", "Init.Core"], prefixes=["Init"],
                   modules=[("Init", None), ("Init.Core", None)],
                   decls=[{"name": "Nat.add_comm'", "module": "Init.Core"}])
        proc = self.drive(f"lean-core\thttps://example.org/lean4\t{GITHASH}\tInit.*\tcore\t"
                          "WORKSPACE=ws;SHARD_COUNT=1", "lean-core")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr + self.log_of("lean-core"))
        manifest, records = self.index("lean-core", GITHASH)
        self.assertEqual(manifest["commit"], GITHASH)
        self.assertEqual(records[0]["commit"], GITHASH)
        self.assertEqual(manifest["library_kind"], "core")
        self.assertIs(manifest["dirty"], False)
        paths = {r["name"]: r["path"] for r in records if r["record"] == "module"}
        self.assertEqual(paths, {"Init": "src/Init.lean", "Init.Core": "src/Init/Core.lean"})
        decl_path = next(r["path"] for r in records if r["record"] == "declaration")
        self.assertEqual(decl_path, "src/Init/Core.lean")
        sidecar = json.loads((self.atlas / "index" / f"lean-core@{GITHASH[:12]}" /
                              "universe-entry.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["spec"]["workspace"], "ws")
        # and the index is one ingest takes as the pinned lean-core, whole
        universe = make_universe(self.root / "ws-side", libs=())
        universe.add_library(library_id="lean-core", repo="https://example.org/lean4",
                             commit=GITHASH, toolchain="leanprover/lean4:v4.34.0",
                             roots=["Init.*"])
        got = ingest.ingest(universe, "lean-core", self.atlas / "index" / f"lean-core@{GITHASH[:12]}",
                            self.root / "ws-side" / "out")
        self.assertEqual(got["declarations"], 1)
        self.assertEqual(got["modules_without_path"], 0)

    def test_core_on_another_toolchain_is_refused(self):
        self.shard(library="lean-core", roots=["Init"], prefixes=["Init"],
                   modules=[("Init", None)], decls=[])
        proc = self.drive(f"lean-core\thttps://example.org/lean4\t{'d' * 40}\tInit\tcore\t"
                          "WORKSPACE=ws;SHARD_COUNT=1", "lean-core")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("but the Lean of ws", proc.stdout + proc.stderr)
        self.assertFalse((self.atlas / "index" / f"lean-core@{'d' * 12}" / "index.jsonl").exists())

    def package(self, *, rev: str | None = None, url: str = "https://example.org/batteries"):
        pkg, head = git_checkout(self.ws / ".lake" / "packages" / "batteries",
                                 {"Batteries.lean": "", "Batteries/Data.lean": "",
                                  ".gitignore": ".lake/\n"})
        write_tree(pkg, {".lake/build/lib/lean/Batteries.olean": "",
                         ".lake/build/lib/lean/Batteries/Data.olean": ""})
        (self.ws / "lake-manifest.json").write_text(json.dumps({"packages": [
            {"name": "batteries", "type": "git", "rev": rev or head, "url": url,
             "subDir": None}]}), encoding="utf-8")
        return head

    def test_a_package_is_pinned_at_the_manifest_rev_with_its_own_paths(self):
        head = self.package()
        self.shard(library="batteries", roots=["Batteries", "Batteries.Data"],
                   prefixes=["Batteries"],
                   modules=[("Batteries", ".lake/packages/batteries/Batteries.lean"),
                            ("Batteries.Data", None)],
                   decls=[{"name": "Batteries.thing", "module": "Batteries.Data"}])
        proc = self.drive(f"batteries\thttps://example.org/batteries\t{head}\tBatteries.*\t"
                          "package-in-workspace\tWORKSPACE=ws;PACKAGE=batteries;SHARD_COUNT=1",
                          "batteries")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr + self.log_of("batteries"))
        manifest, records = self.index("batteries", head)
        self.assertEqual(manifest["commit"], head)
        self.assertNotEqual(manifest["commit"],
                            manifest["library_source"]["workspace_commit"])
        self.assertIs(manifest["dirty"], False)
        paths = {r["name"]: r["path"] for r in records if r["record"] == "module"}
        self.assertEqual(paths, {"Batteries": "Batteries.lean",
                                 "Batteries.Data": "Batteries/Data.lean"})

    def test_a_package_at_another_rev_or_from_another_repository_is_refused(self):
        head = self.package(rev="e" * 40)
        self.shard(library="batteries", roots=["Batteries"], prefixes=["Batteries"],
                   modules=[("Batteries", None)], decls=[])
        line = (f"batteries\thttps://example.org/batteries\t{head}\tBatteries\t"
                "package-in-workspace\tWORKSPACE=ws;PACKAGE=batteries;SHARD_COUNT=1")
        proc = self.drive(line, "batteries")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("pins batteries at eeeeeeeeeeee", proc.stdout + proc.stderr)
        proc = self.drive(line.replace("example.org/batteries", "example.org/fork"), "batteries")
        self.assertEqual(proc.returncode, 1)

    def test_the_list_says_where_each_kind_is_probed(self):
        tsv = self.root / "u.tsv"
        good = (f"mathlib\thttps://example.org/mathlib\t{ALPHA}\tMathlib\tbuild\n"
                f"lean-core\thttps://example.org/lean4\t{GITHASH}\tInit.*\tcore\tWORKSPACE=mathlib\n"
                f"batteries\thttps://example.org/b\t{ALPHA}\tBatteries.*\tpackage-in-workspace\t"
                "WORKSPACE=mathlib;PACKAGE=batteries\n")
        tsv.write_text(good, encoding="utf-8")
        entries = universe_host.load_universe(tsv)
        self.assertEqual(entries[1].spec(GITHASH)["workspace"], "mathlib")
        self.assertEqual(entries[2].spec(ALPHA)["package"], "batteries")
        for bad in (good.splitlines()[1] + "\n" + good.splitlines()[0],      # workspace after
                    f"x\thttps://example.org/x\t{ALPHA}\tX\tcore\n",            # no WORKSPACE
                    f"x\thttps://example.org/x\t{ALPHA}\tX\tpackage-in-workspace\tWORKSPACE=w\n",
                    f"x\thttps://example.org/x\t{ALPHA}\tX\tbuild\tWORKSPACE=w\n",
                    f"x\thttps://example.org/x\t{ALPHA}\tX\tcore\tWORKSPACE=x\n",
                    f"x\thttps://example.org/x\t{ALPHA}\tX\tpackage-in-workspace\t"
                    "WORKSPACE=w;PACKAGE=..\n"):
            with self.subTest(line=bad):
                tsv.write_text(bad, encoding="utf-8")
                with self.assertRaises(universe_host.UniverseError):
                    universe_host.load_universe(tsv)


class TestD12AtomicWritesKeepTheModeAPlainWriteWould(unittest.TestCase):
    """D12: ``universe.yaml`` was 0600 (mkstemp's mode) beside 0644 indexes."""

    def with_umask(self, mask: int):
        old = os.umask(mask)
        self.addCleanup(os.umask, old)

    def test_a_new_file_gets_the_umask_mode(self):
        from cflib.jsonio import atomic_write
        root = _scratch(self)
        self.with_umask(0o022)
        self.assertEqual(atomic_write(root / "a.json", "{}\n").stat().st_mode & 0o777, 0o644)
        os.umask(0o077)
        self.assertEqual(atomic_write(root / "b.json", "{}\n").stat().st_mode & 0o777, 0o600)

    def test_a_rewrite_keeps_the_files_own_mode(self):
        from cflib.jsonio import atomic_write
        root = _scratch(self)
        self.with_umask(0o022)
        target = root / "kept.json"
        target.write_text("{}\n", encoding="utf-8")
        target.chmod(0o640)
        atomic_write(target, '{"a": 1}\n')
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(target.read_text(encoding="utf-8"), '{"a": 1}\n')
        self.assertEqual([p.name for p in root.iterdir()], ["kept.json"])

    def test_universe_yaml_is_readable_by_the_group_and_others(self):
        self.with_umask(0o022)
        root = _scratch(self)
        universe = make_universe(root)
        self.assertEqual(universe.path.stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()
