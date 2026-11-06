"""`cf informalize build-command | fetch-library | scope`: stages I1 and I2.

New code, for docs/proposals/informalize.md section 11, component 4 (sections 6.1 and 6.2).
No Lean runs here, and no host: the library is a small invented one, `ExampleLib`, with a
fake probe index, a fake DumpDeps dump and a fake host folder written in a temporary
directory, and ssh and rsync are replaced by a local copy.  Four groups:

* the pieces without a workspace: the universe line and its `LIBRARY_CACHE` option, the
  host folder's spellings, the license table, the canary's form and files, and the host
  script's copies of the drivers' rules (its git pins, its license file names);
* the closure (`informalize.scope`) on a synthetic dump and index, and each refusal;
* the commands in a temporary workspace against a fake host folder: what build-command
  writes and prints, fetch-library's dry run, consent, checks and receipt, scope;
* the host script itself, run end to end with bash 4.3 or later and stub `lake`, `elan`
  and friends on the drivers build-command copied: the universe driver, the canary, the
  dump and the collection, then fetch-library and scope on what it wrote.
"""
from __future__ import annotations

# Pin HOME before anything builds a workspace: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
# See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
for entry in (str(HERE), str(SKILL / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import _informalize_doc as doc                                # noqa: E402
import cf as cf_main                                          # noqa: E402
from annotations.export import common                         # noqa: E402
from cflib import gitsafe                                     # noqa: E402
from cflib.errors import CfError                              # noqa: E402
from cflib.paths import WorkspacePaths                        # noqa: E402
from cflib.workspace import Workspace                         # noqa: E402
from cflib.yamlio import read_yaml                            # noqa: E402
from cfcmd import corpus as corpus_cmd                        # noqa: E402
from corpus import probe_merge, universe, universe_host       # noqa: E402
from informalize import itemtable, library, library_host      # noqa: E402
from informalize import scope as scope_mod                    # noqa: E402
from informalize.approve import AGENT_ENVIRONMENT             # noqa: E402
from informalize.outrepo import GIT_LOCATION_ENV              # noqa: E402
from test_locate_corpus import canary_records, decl, write_probe   # noqa: E402

ID = "example-thm"
LIB = f"{ID}-lib"
URL = doc.SLOTS["library_url"]
MODULE = "ExampleLib.Topology.Glue.Basic"
DEFS = "ExampleLib.Topology.Glue.Defs"
THEOREM = "ExampleLib.glue_theorem"
PREFIX = "ExampleLib"
TOOLCHAIN = "leanprover/lean4:v4.34.0"
HOST = "testbox"
SSH = "tester@testbox"
LICENSES = SKILL / "templates" / "informalize" / "licenses"
APACHE = (LICENSES / "Apache-2.0.txt").read_text(encoding="utf-8")
HOST_SCRIPT = SKILL / "scripts" / "informalize" / "library_host.sh"
DRIVER = SKILL / "scripts" / "corpus" / "build_universe_host.sh"


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-library-", dir=os.environ.get("CF_TEST_TMP")))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def cf(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cf_main.cli(list(argv))
        except SystemExit as exc:          # argparse
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# ExampleLib: two modules, five declarations, a dump of eight constants
# ---------------------------------------------------------------------------

def _file(lines: dict[int, str], *, header: str = "", length: int = 30) -> str:
    out = [f"-- line {n}" for n in range(1, length + 1)]
    for number, text in lines.items():
        out[number - 1] = text
    return header + "\n".join(out[header.count("\n"):]) + "\n"


def source_files(form: str = "plain") -> dict[str, str]:
    """The library's files at its commit: two modules, a license, a toolchain, a manifest."""
    opening = "/-\nCopyright (c) 2001 A. Author. All rights reserved.\n-/\n"
    if form == "module":
        opening += "module\n\npublic "
    basic = _file({20: "theorem glue_theorem {X : Type} (U V : Set X) (hU : IsOpen U) :"},
                  header=opening + "import ExampleLib.Topology.Glue.Defs\n")
    defs = _file({5: "structure GlueData (X : Type) where", 12: "def glue_map (X : Type) :",
                  16: "theorem cover_mono {X : Type} :", 20: "theorem unused : True := trivial",
                  23: "private def secret : Nat := 0"})
    return {"ExampleLib/Topology/Glue/Basic.lean": basic,
            "ExampleLib/Topology/Glue/Defs.lean": defs,
            "LICENSE": APACHE, "lean-toolchain": TOOLCHAIN + "\n",
            "lake-manifest.json": json.dumps({"version": "1.1.0", "packagesDir": ".lake/packages",
                                              "name": "examplelib", "packages": []}) + "\n",
            "scripts/lake-cache-get.sh": 'echo ran > "$1/.lake/library-cache-ran"\n',
            ".gitignore": ".lake/\n"}


MODULES = ((MODULE, "ExampleLib/Topology/Glue/Basic.lean"),
           (DEFS, "ExampleLib/Topology/Glue/Defs.lean"))


def records() -> list[dict]:
    """The probe's records of ExampleLib's declarations, as probe_merge.py sorts them."""
    basic, defs = (p for _m, p in MODULES)
    rows = [
        decl(THEOREM, "theorem", module=MODULE, path=basic, line=20, start_line=18, end_line=25,
             signature="∀ {X : Type} (U V : Set X), IsOpen U → ExampleLib.GlueData X"),
        decl("ExampleLib.GlueData", "structure", "definition", module=DEFS, path=defs, line=5,
             start_line=4, end_line=10, signature="Type → Type"),
        decl("ExampleLib.glue_map", "def", "definition", module=DEFS, path=defs, line=12,
             start_line=12, end_line=14, signature="(X : Type) → ExampleLib.GlueData X"),
        decl("ExampleLib.cover_mono", "theorem", module=DEFS, path=defs, line=16, start_line=16,
             end_line=18, signature="∀ {X : Type}, True"),
        decl("ExampleLib.unused", "theorem", module=DEFS, path=defs, line=20, start_line=20,
             end_line=21, signature="True"),
        decl("ExampleLib.secret", "def", "definition", module=DEFS, path=defs, line=23,
             start_line=23, end_line=23, signature="Nat", private=True),
    ]
    return sorted(rows, key=lambda r: r["name"])


SECRET = "_private.ExampleLib.Topology.Glue.Defs.0.ExampleLib.secret"


def deps_doc() -> dict:
    """DumpDeps' output: the declarations, and the generated and private constants between."""
    def row(name, kind, module, type_deps=(), value_deps=()):
        return {"name": name, "module": module, "kind": kind, "instance": False,
                "structure": kind == "inductive", "projection": False,
                "type_deps": list(type_deps), "value_deps": list(value_deps)}
    constants = [
        row(THEOREM, "theorem", MODULE, ["ExampleLib.GlueData", "ExampleLib.glue_map", "Set"],
            [f"{THEOREM}.proof_1", "ExampleLib.GlueData.mk"]),
        row(f"{THEOREM}.proof_1", "theorem", MODULE, ["True"], ["ExampleLib.cover_mono"]),
        row("ExampleLib.GlueData", "inductive", DEFS),
        row("ExampleLib.GlueData.mk", "ctor", DEFS, ["ExampleLib.GlueData"]),
        row("ExampleLib.glue_map", "def", DEFS, ["ExampleLib.GlueData"], [SECRET]),
        row(SECRET, "def", DEFS, ["Nat"], ["ExampleLib.cover_mono", "Nat.zero"]),
        row("ExampleLib.cover_mono", "theorem", DEFS,
            ["True", "OtherLib.Widget", "Mathlib.Nowhere", f"{SECRET}.aux"], ["trivial"]),
        row("ExampleLib.unused", "theorem", DEFS, ["True"], ["trivial"]),
    ]
    # the module table DumpDeps writes for every constant the library's own mention: the
    # only place the module of a constant the probe does not index is recorded
    referenced = [
        {"name": "Set", "module": "Mathlib.Data.Set.Defs", "kind": "def"},
        {"name": "True", "module": "Init.Prelude", "kind": "inductive"},
        {"name": "Nat.zero", "module": "Init.Prelude", "kind": "ctor"},
        {"name": "OtherLib.Widget", "module": "OtherLib.Basic", "kind": "inductive"},
        {"name": "Mathlib.Nowhere", "module": "<none>", "kind": "?"},
        {"name": f"{SECRET}.aux", "module": "Mathlib.Data.Set.Defs", "kind": "theorem"},
    ]
    return {"package": MODULE, "root_module": MODULE, "module_prefix": PREFIX,
            "repo_constants": constants, "referenced": referenced}


def settings(**library) -> dict:
    lib = {"url": URL, "commit": doc.COMMIT, "module": MODULE, "theorem": THEOREM,
           "prefix": PREFIX, "license": None, "library_cache": False, **library}
    return {"id": ID, "library": lib}


def source_tree(root: Path, files: dict[str, str] | None = None) -> itemtable.SourceTree:
    for rel, text in (files or source_files()).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return itemtable.SourceTree(root)


def write_tar(path: Path, files: dict[str, str], *, links: dict[str, str] | None = None,
              extra: tuple[str, ...] = ()) -> None:
    """A `git archive`-shaped tar: a pax global header, then the files."""
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT,
                      pax_headers={"comment": doc.COMMIT}) as tar:
        for name in [*sorted(files), *extra]:
            raw = files.get(name, "x").encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)


def write_host(root: Path, *, commit: str = doc.COMMIT, done: str = "0", form: str = "plain",
               files: dict[str, str] | None = None, decls: list[dict] | None = None,
               deps: dict | None = None, canary: list[dict] | None = None,
               head: str | None = None, state_commit: str | None = None,
               to_host: Path | None = None) -> Path:
    """What library_host.sh leaves under `<root>/library/` when it has run the drivers and
    the library list of `to_host` (build-command's `library/to-host/`)."""
    files = source_files(form) if files is None else files
    lib = root / "library"
    host = lib / "host"
    (host / "state").mkdir(parents=True, exist_ok=True)
    (host / "license").mkdir(parents=True, exist_ok=True)
    (host / "logs").mkdir(parents=True, exist_ok=True)
    (host / "DONE").write_text(f"{done}\n", encoding="utf-8")
    if to_host is not None:
        (host / "drivers.json").write_text(json.dumps(library_host.digest(
            to_host / "drivers", to_host / library.LIBRARY_LIST)), encoding="utf-8")
    (host / "commit").write_text(f"{head or commit}\n", encoding="utf-8")
    (host / "commit-time").write_text("1700000000\n", encoding="utf-8")
    (host / "lean-toolchain").write_text(TOOLCHAIN + "\n", encoding="utf-8")
    (host / "lake-manifest.json").write_text(files.get("lake-manifest.json", "{}"),
                                             encoding="utf-8")
    if "LICENSE" in files:
        (host / "license" / "LICENSE").write_text(files["LICENSE"], encoding="utf-8")
    (host / "state" / f"{LIB}.json").write_text(json.dumps(
        {"stage": "indexed", "commit": state_commit or commit}), encoding="utf-8")
    (host / "logs" / f"{LIB}.log").write_text("built\n", encoding="utf-8")
    (host / "library_host.log").write_text("done\n", encoding="utf-8")
    (host / "canary-form").write_text(f"{form}\n", encoding="utf-8")
    write_probe(host / "canary", canary_records() if canary is None else canary,
                library="cf-canary", commit=None, modules=(("CfCanary.Basic",
                                                            "CfCanary/Basic.lean"),))
    write_tar(host / "src.tar", {k: v for k, v in files.items() if k != ".gitignore"},
              links={"README": "LICENSE"})
    index = lib / "index" / f"{LIB}@{commit[:12]}"
    write_probe(index, records() if decls is None else decls, library=LIB, commit=commit,
                modules=MODULES)
    (index / "work").mkdir()
    (index / "work" / "shard-0.log").write_text("probe log\n", encoding="utf-8")
    (lib / "deps.json").write_text(json.dumps(deps_doc() if deps is None else deps),
                                   encoding="utf-8")
    return root


class FakeHost:
    """ssh and rsync against a local folder: `test -f` asked of it, rsync run locally."""

    def __init__(self, ssh: str = SSH):
        self.ssh = ssh
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        if argv[0] == "ssh":
            path = argv[-1].split(" ", 2)[2]
            return subprocess.CompletedProcess(argv, 0 if Path(path).is_file() else 1, "", "")
        if argv[0] == "rsync":
            local = [a[len(self.ssh) + 1:] if a.startswith(self.ssh + ":") else a for a in argv]
            return subprocess.run(local, capture_output=True, text=True)
        raise AssertionError(f"unexpected command {argv}")


# ---------------------------------------------------------------------------
# the pieces, without a workspace
# ---------------------------------------------------------------------------

class TestTheLibraryLine(unittest.TestCase):

    def test_the_line_is_one_the_driver_reads(self):
        fields = library.universe_fields(settings())
        self.assertEqual(fields[:5], [LIB, URL, doc.COMMIT, MODULE, "build"])
        self.assertEqual(fields[5], f"BUILD_TARGETS={MODULE};MODULE_PREFIXES={PREFIX};"
                                    "LIBRARY_CACHE=0")
        entry = universe_host.Entry(1, fields)
        self.assertEqual(entry.options["LIBRARY_CACHE"], "0")
        self.assertEqual(entry.bases, [MODULE])
        opted = library.universe_fields(settings(library_cache=True))
        self.assertTrue(opted[5].endswith("LIBRARY_CACHE=1"))

    def test_a_line_the_driver_would_refuse_is_refused_here(self):
        with self.assertRaises(CfError) as caught:
            library.universe_fields(settings(url="https://example.org:8443/ExampleLib"))
        self.assertIn("would refuse", caught.exception.message)

    def test_library_cache_keeps_its_default_and_changes_no_index(self):
        self.assertEqual(universe_host.OPTION_DEFAULTS["LIBRARY_CACHE"], "1")
        base = [LIB, URL, doc.COMMIT, MODULE, "build"]
        off = universe_host.Entry(1, [*base, "LIBRARY_CACHE=0"])
        on = universe_host.Entry(1, base)
        self.assertEqual(on.options["LIBRARY_CACHE"], "1")
        self.assertEqual(off.spec(doc.COMMIT), on.spec(doc.COMMIT))
        with self.assertRaises(universe_host.UniverseError):
            universe_host.Entry(1, [*base, "LIBRARY_CACHE=yes"])

    def test_the_driver_skips_the_librarys_cache_script_when_told(self):
        text = DRIVER.read_text(encoding="utf-8")
        self.assertIn('[ -f "$dir/scripts/lake-cache-get.sh" ] && [ "$OPT_LIBRARY_CACHE" != 1 ]',
                      text)
        self.assertEqual(subprocess.run(["bash", "-n", str(DRIVER)]).returncode, 0)
        self.assertEqual(subprocess.run(["bash", "-n", str(HOST_SCRIPT)]).returncode, 0)


class TestTheHostFolder(unittest.TestCase):

    def test_an_absolute_folder_is_spelled_for_the_shell_and_for_rsync(self):
        absolute = library.RemoteRoot("/srv/runs/informalize-x")
        self.assertEqual(absolute.shell(), "/srv/runs/informalize-x")
        self.assertEqual(absolute.shell("library/host/DONE"),
                         "/srv/runs/informalize-x/library/host/DONE")
        self.assertEqual(absolute.rsync("library.tsv"), "/srv/runs/informalize-x/library.tsv")
        self.assertEqual(library.RemoteRoot("/srv/runs/informalize-x/").rsync(),
                         "/srv/runs/informalize-x")

    def test_what_cannot_be_named_safely_is_refused(self):
        # `~` and `$HOME` among them: every remote path of a run is quoted for the host's
        # shell, where a leading ~ is a directory of that name (run.manifest's own refusal)
        for root in ("~", "$HOME", "~/informalize-x", "$HOME/informalize-x/",
                     "informalize-x", "~/a b", "/srv/../etc", "~/x;rm",
                     "/srv/$(id)", "", "/"):
            with self.subTest(root=root), self.assertRaises(CfError):
                library.RemoteRoot(root)


class TestTheLicenseTable(unittest.TestCase):

    def test_each_open_license_text_is_named(self):
        for path in sorted(LICENSES.glob("*.txt")):
            with self.subTest(license=path.stem):
                self.assertEqual(library.license_of(path.read_text(encoding="utf-8")), path.stem)

    def test_bsd_and_short_notices(self):
        bsd = ("Redistribution and use in source and binary forms, with or without "
               "modification, are permitted provided that ...\n3. Neither the name of the "
               "copyright holder nor ...")
        self.assertEqual(library.license_of(bsd), "BSD-3-Clause")
        self.assertEqual(library.license_of(bsd.split("\n3.")[0]), "BSD-2-Clause")
        self.assertEqual(library.license_of("Licensed under the Apache License,\nVersion 2.0."),
                         "Apache-2.0")

    def test_nothing_or_two_licenses_name_none(self):
        mit = (LICENSES / "MIT.txt").read_text(encoding="utf-8")
        self.assertIsNone(library.license_of(""))
        self.assertIsNone(library.license_of("All rights reserved."))
        self.assertIsNone(library.license_of(mit + "\n" + APACHE))
        self.assertIsNone(library.license_of("GNU GENERAL PUBLIC LICENSE Version 3"))


class TestTheCanary(unittest.TestCase):

    def test_the_form_follows_the_first_command(self):
        cases = {
            "module\n\npublic import Foo\n": "module",
            "/- Copyright\n /- nested -/ still a comment\n-/\n-- a line\nmodule\nimport Foo\n":
                "module",
            "/-! module doc -/\nimport Foo\n": "plain",
            "-- module\nimport Foo\n": "plain",
            "import Foo\nmodule\n": "plain",
            "modules_are_not_it : Nat\n": "plain",
            "": "plain",
        }
        for text, form in cases.items():
            with self.subTest(text=text):
                self.assertEqual(library_host.module_form(text), form)

    def test_the_slug_and_the_header_are_probe_commands(self):
        for toolchain in ("leanprover/lean4:v4.36.0-rc2", "v4.34.0", "4.31.0",
                          "leanprover/lean4-nightly:nightly-2026-10-01"):
            with self.subTest(toolchain=toolchain):
                self.assertEqual(library_host.slug(toolchain),
                                 corpus_cmd._slug(universe.lean_version(toolchain)))  # noqa: SLF001
                self.assertEqual(library_host.lean_version(toolchain),
                                 universe.lean_version(toolchain))
        self.assertEqual(library_host.TOOLCHAIN.pattern, universe.TOOLCHAIN.pattern)
        printed = corpus_cmd._canary_pipeline(mock.Mock(id="u1"), TOOLCHAIN, None)  # noqa: SLF001
        self.assertIn(library_host.MODULE_HEADER.replace("\n", "\\n"), printed)
        self.assertIn(f"@@MODULE_FOOTER@@#{library_host.MODULE_FOOTER}#", printed)

    def test_both_forms_render_every_template(self):
        for form in library_host.FORMS:
            with self.subTest(form=form):
                work = scratch(self) / "canary"
                shutil.copytree(SKILL / "templates" / "corpus" / "canary", work)
                written = library_host.render_canary(work, TOOLCHAIN, form)
                self.assertIn("CfCanary/Basic.lean", written)
                self.assertEqual(list(work.rglob("*.tmpl")), [])
                basic = (work / "CfCanary" / "Basic.lean").read_text(encoding="utf-8")
                self.assertNotIn("@@", basic)
                self.assertEqual(library_host.module_form(basic), form)
                self.assertEqual((work / "lean-toolchain").read_text(encoding="utf-8").strip(),
                                 TOOLCHAIN)

    def test_the_helper_runs_alone_under_python_dash_i(self):
        root = scratch(self)
        index = scratch(self)
        write_probe(index, records(), library=LIB, commit=doc.COMMIT, modules=MODULES)
        tool = SKILL / "scripts" / "informalize" / "library_host.py"

        def form(files=None):
            if files is not None:
                source_tree(root, files)
            return subprocess.run([sys.executable, "-I", str(tool), "module-form", "--checkout",
                                   str(root), "--index", str(index / "index.jsonl")],
                                  capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        got = form(source_files("plain"))
        self.assertEqual((got.returncode, got.stdout.strip()), (0, "plain"), got.stderr)
        # the theorem's file is plain, another module of the index is not: the module form
        mixed = source_files("plain")
        mixed["ExampleLib/Topology/Glue/Defs.lean"] = "module\n\n" + mixed[
            "ExampleLib/Topology/Glue/Defs.lean"]
        got = form(mixed)
        self.assertEqual((got.returncode, got.stdout.strip()), (0, "module"), got.stderr)
        (root / "ExampleLib/Topology/Glue/Defs.lean").unlink()
        got = form()
        self.assertEqual(got.returncode, 2)
        self.assertIn("does not hold", got.stderr)
        got = subprocess.run([sys.executable, "-I", str(tool), "slug", "--toolchain", "x y"],
                             capture_output=True, text=True)
        self.assertEqual(got.returncode, 2)


def _shell_array(text: str, name: str) -> list[str]:
    body = re.search(rf"^{name}=\((.*?)\)", text, re.S | re.M).group(1)
    return body.split()


class TestTheHostScriptKeepsTheDriversRules(unittest.TestCase):

    def setUp(self):
        self.text = HOST_SCRIPT.read_text(encoding="utf-8")

    def test_its_git_pins_are_gitsafes(self):
        self.assertEqual(_shell_array(self.text, "GIT_SAFE_CONFIG"), list(gitsafe.SAFE_CONFIG))
        self.assertEqual(dict(p.split("=", 1) for p in _shell_array(self.text, "GIT_SAFE_ENV")),
                         gitsafe.SAFE_ENV)

    def test_its_git_and_clean_environment_are_the_drivers(self):
        driver = DRIVER.read_text(encoding="utf-8")

        def body(text: str, start: str, end: str) -> str:
            return text[text.index(start):text.index(end)]
        mine = body(self.text, "\ng() {", "\npy() {").replace("library_host:", "build_universe:")
        self.assertEqual(mine, body(driver, "\ng() {", "\npy() {"))
        def function(text: str, name: str) -> str:
            start = text.index(f"\n{name}() {{\n")
            return text[start:text.index("\n}\n", start) + 3]
        for name in ("cenv", "heavy"):
            with self.subTest(function=name):
                self.assertEqual(function(self.text, name), function(driver, name))

    def test_the_lines_the_pilot_learned_are_there(self):
        text = self.text
        self.assertIn('ulimit -n 65536 || fail 5 ', text)
        self.assertLess(text.index('ulimit -n 65536'), text.index('heavy bash "$dump"'))
        self.assertIn('--timeout "$DUMP_TIMEOUT"', text)
        # a stale DONE goes once the lock is held and before anything runs; what ran is
        # recorded under the trap that writes DONE
        lock, stale = text.index("flock -n 8 ||"), text.index('rm -f "$host_dir/DONE"')
        trap = text.index("trap 'rc=$?; printf")
        self.assertLess(lock, stale)
        self.assertLess(stale, trap)
        self.assertLess(trap, text.index('digest --drivers "$drivers" --list "$universe"'))
        self.assertLess(trap, text.index('bash "$driver" "$universe" "$lib"'))
        self.assertIn('archive --format=tar -o "$host_dir/src.tar.partial" "$commit" -- '
                      "'*.lean'", text)

    def test_it_copies_the_license_files_the_mac_reads(self):
        loop = re.search(r"for name in (.*?); do", self.text, re.S).group(1)
        self.assertEqual(tuple(loop.replace("\\\n", " ").split()), library.LICENSE_FILES)

    def test_every_file_it_runs_is_shipped(self):
        shipped = set(library.host_files())
        for rel in ("scripts/corpus/build_universe_host.sh", "scripts/corpus/universe_host.py",
                    "scripts/corpus/probe_host.sh", "scripts/corpus/probe_merge.py",
                    "templates/corpus/probe.lean.tmpl", "scripts/detect/dump_deps.sh",
                    "scripts/detect/DumpDeps.lean.tmpl", "scripts/informalize/library_host.sh",
                    "scripts/informalize/library_host.py",
                    "templates/corpus/canary/lakefile.toml.tmpl",
                    "templates/corpus/canary/CfCanary/Basic.lean.tmpl"):
            with self.subTest(file=rel):
                self.assertIn(rel, shipped)
        for rel in corpus_cmd.DRIVERS:
            self.assertIn(rel, shipped)


# ---------------------------------------------------------------------------
# the closure
# ---------------------------------------------------------------------------

class TestTheClosure(unittest.TestCase):

    def setUp(self):
        self.src = source_tree(scratch(self))
        self.records = {r["name"]: r for r in records() if not r["private"]}

    def compute(self, deps=None, records_=None, cfg=None, src=None):
        return scope_mod.compute(cfg or settings(), deps_doc() if deps is None else deps,
                                 self.records if records_ is None else records_,
                                 src or self.src)

    def test_members_depth_and_reach(self):
        members, entries, references, counts = self.compute()
        by_name = {m["name"]: m for m in members}
        self.assertEqual(sorted(by_name), ["ExampleLib.GlueData", "ExampleLib.cover_mono",
                                           "ExampleLib.glue_map", THEOREM])
        self.assertEqual({n: (m["depth"], m["reach"]) for n, m in by_name.items()}, {
            THEOREM: (0, "type"),
            "ExampleLib.GlueData": (1, "type"),
            "ExampleLib.glue_map": (1, "type"),
            # through an auxiliary lemma, which costs nothing, not through glue_map's private
            # helper, which would put it at 2
            "ExampleLib.cover_mono": (1, "value"),
        })
        self.assertEqual(by_name["ExampleLib.GlueData"]["proof_status"], "definition")
        self.assertEqual(by_name[THEOREM]["proof_status"], "proved")
        self.assertEqual([tuple(m) for m in members], [scope_mod.MEMBER_KEYS] * 4)
        self.assertEqual([tuple(e) for e in entries], [scope_mod.ENTRY_KEYS] * 4)
        self.assertEqual(counts["walked_through"], 3)
        self.assertEqual(counts["type_only"], 3)
        self.assertEqual(counts["max_depth"], 1)
        self.assertEqual(itemtable.declarations_problems({"declarations": entries,
                                                          "references": references}), [])

    def test_the_references_are_what_the_members_types_name_elsewhere(self):
        _members, entries, references, counts = self.compute()
        self.assertEqual([tuple(r) for r in references], [scope_mod.DOC_REFERENCE_KEYS] * 3)
        self.assertEqual({r["name"]: r["doc_url"] for r in references}, {
            # a member's type names it, and the doc site holds its library
            "Set": "https://leanprover-community.github.io/mathlib4_docs/"
                   "Mathlib/Data/Set/Defs.html#Set",
            "True": "https://leanprover-community.github.io/mathlib4_docs/"
                    "Init/Prelude.html#True",
            # another library: the module the dump found it in, and no URL
            "OtherLib.Widget": "",
        })
        # not Nat.zero, which no member's type names; not the constant no module holds; not
        # a private name
        self.assertEqual(counts["references"], 3)
        # with no manifest the dependency is unknown: the rows carry the keys, empty
        self.assertEqual({r["package"] for r in references}, {""})
        self.assertEqual([e["source_url"] for e in entries if e["name"] == THEOREM],
                         [f"{URL}/blob/{doc.COMMIT}/ExampleLib/Topology/Glue/Basic.lean#L20"])

    def test_a_reference_names_the_dependency_the_manifest_pins(self):
        """`lake-manifest.json` is the one place a dependency's commit is recorded, and the
        package's name matches the module's root but for its case."""
        manifest = {"version": "1.1.0", "packagesDir": ".lake/packages", "packages": [
            {"name": "mathlib", "type": "git", "rev": "a" * 40,
             "url": "https://github.com/leanprover-community/mathlib4"},
            {"name": "otherLib", "type": "git", "rev": "not a commit",
             "url": "https://example.org/lean-libs/OtherLib"}]}
        packages = scope_mod.dependency_packages(manifest)
        _members, _entries, references, _counts = self.compute()
        rows = {r["name"]: r for r in scope_mod.reference_table(
            scope_mod.constants(deps_doc()),
            [{"name": m["name"]} for m in self.compute()[0]],
            deps_doc().get("referenced"), packages)}
        self.assertEqual([r["name"] for r in references], sorted(rows))
        self.assertEqual({k: rows["Set"][k] for k in ("package", "repo", "commit")},
                         {"package": "mathlib",
                          "repo": "github.com/leanprover-community/mathlib4",
                          "commit": "a" * 40})
        # Init is the toolchain's: the manifest lists no package for it
        self.assertEqual(rows["True"]["package"], "")
        # a rev that is not 40 hex pins nothing, but the package is still named
        self.assertEqual({k: rows["OtherLib.Widget"][k] for k in ("package", "commit")},
                         {"package": "otherLib", "commit": ""})

    def test_a_library_that_is_no_web_url_gets_no_source_link(self):
        cfg = settings(url="git@example.org:lean-libs/ExampleLib.git")
        _members, entries, _references, _counts = self.compute(cfg=cfg)
        self.assertEqual({e["source_url"] for e in entries}, {""})

    def test_the_source_link_is_the_annotation_cards_destination(self):
        # one reader of a remote URL, the exporter's, so the link in declarations.json and
        # the card's destination are the same string for every URL, port and trailing .git
        # included -- and a URL with no repository to it gets no link rather than one to
        # nowhere
        for url in (URL, f"{URL}.git", f"{URL}/", "https://example.org:8443/lib/ExampleLib"):
            with self.subTest(url=url):
                self.assertEqual(
                    scope_mod.source_url(url, doc.COMMIT, "A.lean", 3),
                    f"https://{common.repo_id_from_url(url)}/blob/{doc.COMMIT}/A.lean#L3")
        self.assertEqual(scope_mod.source_url("https://example.org", doc.COMMIT, "A.lean", 3),
                         "")

    def test_a_broken_reference_table_is_refused(self):
        rows = [{"name": "Set", "module": "Mathlib.Data.Set.Defs", "kind": "def",
                 "doc_url": ""}]
        self.assertEqual(itemtable.declarations_problems({"declarations": [],
                                                          "references": rows}), [])
        for bad, needle in (({"references": "Set"}, "`references` is not a list"),
                            ({"references": [{"name": "Set", "module": "Set Defs"}]},
                             "is not a module name"),
                            ({"references": [rows[0], dict(rows[0], name="A")]},
                             "not sorted by name")):
            with self.subTest(needle=needle):
                found = itemtable.declarations_problems({"declarations": [], **bad})
                self.assertTrue(any(needle in p for p in found), found)

    def test_the_walk_takes_the_cheapest_path(self):
        graph = {n: {"type_deps": [], "value_deps": []} for n in "tabcd"}
        graph["t"]["value_deps"] = ["a", "x"]
        graph["a"]["value_deps"] = ["b"]
        graph["b"]["value_deps"] = ["c"]
        graph["t"]["type_deps"] = ["d"]
        graph["d"]["value_deps"] = ["c"]
        closure = scope_mod.walk(graph, "t", {"t", "b", "c", "d"})
        self.assertEqual(closure.depth, {"t": 0, "a": 0, "b": 1, "c": 2, "d": 1})
        self.assertEqual(closure.type_only, {"t", "d"})

    def test_what_it_refuses(self):
        deps = deps_doc()
        bad_prefix = dict(deps, module_prefix="Other")
        no_theorem = dict(deps, repo_constants=deps["repo_constants"][1:])
        no_bodies = copy.deepcopy(deps)
        no_bodies["repo_constants"][0]["value_deps"] = []
        none_at_all = copy.deepcopy(deps)
        for row in none_at_all["repo_constants"]:
            if row["kind"] == "theorem" and row["name"] != THEOREM:
                row["value_deps"] = []
        twice = copy.deepcopy(deps)
        twice["repo_constants"].append(dict(twice["repo_constants"][-1], module=MODULE,
                                            type_deps=["False"]))
        sorry = dict(self.records)
        sorry[THEOREM] = dict(sorry[THEOREM], nonstandard_axioms=["sorryAx"],
                              proof_status="statement_only")
        moved = dict(self.records)
        moved[THEOREM] = dict(moved[THEOREM], module=DEFS)
        missing = {k: v for k, v in self.records.items() if k != THEOREM}
        short = source_tree(scratch(self), {**source_files(),
                                            "ExampleLib/Topology/Glue/Defs.lean": "x\n"})
        cases = {
            "under prefix 'Other'": dict(deps=bad_prefix),
            "not among the constants": dict(deps=no_theorem),
            "proof's body was not loaded": dict(deps=no_bodies),
            "twice with different types": dict(deps=twice),
            "rests on sorryAx": dict(records_=sorry),
            "not in ExampleLib.Topology.Glue.Basic": dict(records_=moved),
            "not a public declaration": dict(records_=missing),
            "has 1 lines": dict(src=short),
        }
        for needle, kwargs in cases.items():
            with self.subTest(needle=needle), self.assertRaises(CfError) as caught:
                self.compute(**kwargs)
            self.assertIn(needle, caught.exception.message)
        # the theorem's own body is there, but no other theorem's: that is no refusal
        members, _entries, _refs, _counts = self.compute(deps=none_at_all)
        self.assertEqual(len(members), 4)
        # a declaration that is no theorem, in a library none of whose theorems has a body
        stated_as_def = copy.deepcopy(none_at_all)
        stated_as_def["repo_constants"][0]["kind"] = "def"
        with self.assertRaises(CfError) as caught:
            self.compute(deps=stated_as_def)
        self.assertIn("for any of the library's", caught.exception.message)

    def test_a_constant_two_modules_realized_is_one(self):
        deps = deps_doc()
        lemma = "ExampleLib.glue_map.congr_simp"
        deps["repo_constants"][0]["value_deps"].append(lemma)
        for module, used in ((DEFS, "ExampleLib.cover_mono"), (MODULE, "ExampleLib.unused")):
            deps["repo_constants"].append({
                "name": lemma, "module": module, "kind": "theorem", "instance": False,
                "structure": False, "projection": False, "type_deps": ["ExampleLib.glue_map"],
                "value_deps": [used]})
        members, _entries, _refs, counts = self.compute(deps=deps)
        by_name = {m["name"]: m for m in members}
        # the second copy's dependency is kept too, one level below the theorem
        self.assertEqual((by_name["ExampleLib.unused"]["depth"],
                          by_name["ExampleLib.unused"]["reach"]), (1, "value"))
        self.assertEqual(len(members), 5)
        self.assertEqual(counts["repeated_rows"], 1)
        self.assertEqual(scope_mod.constants(deps)[lemma]["value_deps"],
                         ["ExampleLib.cover_mono", "ExampleLib.unused"])

    def test_two_public_records_of_one_name_are_refused(self):
        root = scratch(self)
        write_probe(root, records() + [records()[0]], library=LIB, commit=doc.COMMIT,
                    modules=MODULES)
        with self.assertRaises(CfError) as caught:
            scope_mod.public_records(root / "index.jsonl")
        self.assertIn("two public records", caught.exception.message)

    def test_a_record_that_contradicts_itself_is_refused(self):
        bad = dict(self.records)
        bad["ExampleLib.cover_mono"] = dict(bad["ExampleLib.cover_mono"],
                                            proof_status="statement_only")
        with self.assertRaises(CfError) as caught:
            self.compute(records_=bad)
        self.assertIn("ExampleLib.cover_mono", caught.exception.message)


# ---------------------------------------------------------------------------
# the commands, against a fake host folder
# ---------------------------------------------------------------------------

class _Workspace(unittest.TestCase):

    #: the library's commit `init` names (the end-to-end test uses a real one)
    commit = doc.COMMIT

    def setUp(self):
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in AGENT_ENVIRONMENT + GIT_LOCATION_ENV:
            os.environ.pop(name, None)
        base = scratch(self)
        self.root = base / "ws"
        self.host = base / f"informalize-{ID}"
        self.host.mkdir()
        code, out, err = cf("init", str(self.root), "--no-git", "-q")
        self.assertEqual(code, 0, out + err)
        ws = Workspace.load(self.root, validate=False)
        ws.data["hosts"] = {"allowlist": [HOST],
                            HOST: {"ssh": SSH, "remote_root": str(self.host)}}
        ws.save()
        self.paths = WorkspacePaths(self.root)
        self.repo = self.paths.informalize_repo(ID)
        self.init()

    def cf(self, *argv: str, code: int = 0) -> str:
        got, out, err = cf("--workspace", str(self.root), *argv)
        self.assertEqual(got, code, out + err)
        return out + err

    def init(self, *extra: str, code: int = 0) -> str:
        return self.cf("informalize", "init", ID, "--library", URL, "--commit", self.commit,
                       "--module", MODULE, "--theorem", THEOREM, "--prefix", PREFIX,
                       "--text-license", "Apache-2.0", "--title", "The glue theorem",
                       "--author", "A. Maintainer", *extra, code=code)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True,
                              text=True, check=True).stdout

    def commits(self) -> int:
        return int(self.git("rev-list", "--count", "HEAD").strip())

    def write_host(self, **kwargs) -> Path:
        """The host folder after a job that ran what build-command wrote here."""
        to_host = self.paths.informalize_library_to_host(ID)
        if not (to_host / library.LIBRARY_LIST).is_file():
            self.cf("informalize", "build-command", ID, "--host", HOST)
        return write_host(self.host, to_host=to_host, **kwargs)

    def fetch(self, *extra: str, code: int = 0, fake: FakeHost | None = None) -> str:
        fake = fake or FakeHost()
        with mock.patch.object(library, "_run", fake):
            return self.cf("informalize", "fetch-library", ID, "--host", HOST, *extra,
                           code=code)

    def receipt(self, stage: str) -> dict:
        return json.loads(self.paths.informalize_receipt(ID, stage).read_text(encoding="utf-8"))


class TestBuildCommand(_Workspace):

    def test_it_writes_what_the_host_needs_and_prints_the_lines(self):
        out = self.cf("informalize", "build-command", ID, "--host", HOST)
        to_host = self.paths.informalize_library_to_host(ID)
        for rel in library.host_files():
            with self.subTest(file=rel):
                self.assertEqual((to_host / "drivers" / rel).read_bytes(),
                                 (SKILL / rel).read_bytes())
        (entry,) = universe_host.load_universe(to_host / "library.tsv")
        self.assertEqual((entry.id, entry.ref, entry.roots_spec), (LIB, self.commit, MODULE))
        self.assertEqual(entry.options["LIBRARY_CACHE"], "0")
        self.assertEqual(entry.options["MODULE_PREFIXES"], PREFIX)
        self.assertIn(f"ssh -n {SSH} 'mkdir -p {self.host}/library/host'", out)
        self.assertIn(f"rsync -a --delete {os.path.realpath(to_host)}/drivers/ "
                      f"{SSH}:{self.host}/drivers/", out)
        start = [line for line in out.splitlines() if "setsid -f" in line]
        self.assertEqual(len(start), 1)
        self.assertIn(f"cd {self.host} && mkdir -p library/host && setsid -f env "
                      'ATLAS_ROOT="$PWD/library" MIN_FREE_GB=60 LEAN_THREADS=16 bash '
                      "drivers/scripts/informalize/library_host.sh library.tsv >> "
                      "library/host/library_host.log 2>&1 < /dev/null", start[0])
        script = (to_host / "build-command.sh").read_text(encoding="utf-8")
        self.assertIn(start[0], script)
        self.assertEqual(subprocess.run(["sh", "-n", str(to_host / "build-command.sh")]).returncode,
                         0)
        self.assertNotIn("lake-cache-get", out)
        self.assertIn("nothing ran", out)

    def test_the_floor_and_the_threads_are_flags_and_the_cache_an_opt_in(self):
        self.init("--library-cache", "--force")
        out = self.cf("informalize", "build-command", ID, "--host", HOST, "--min-free-gb", "120",
                      "--lean-threads", "8")
        self.assertIn("MIN_FREE_GB=120 LEAN_THREADS=8", out)
        self.assertIn("may download and run code", out)
        line = (self.paths.informalize_library_to_host(ID) / "library.tsv").read_text()
        self.assertIn("LIBRARY_CACHE=1", line)
        self.cf("informalize", "build-command", ID, "--host", HOST, "--lean-threads", "0", code=2)

    def test_a_host_off_the_allowlist_or_a_root_it_cannot_name_is_refused(self):
        out = self.cf("informalize", "build-command", ID, "--host", "otherbox", code=2)
        self.assertIn("allowlist", out)
        ws = Workspace.load(self.root, validate=False)
        ws.data["hosts"][HOST]["remote_root"] = "~"
        ws.save()
        self.assertIn("remote_root", self.cf("informalize", "build-command", ID, "--host", HOST,
                                             code=2))

    def test_the_host_folder_is_this_ids_own(self):
        def with_root(entry_root, shared=None):
            ws = Workspace.load(self.root, validate=False)
            ws.data["hosts"][HOST].pop("remote_root", None)
            ws.data["hosts"].pop("remote_root", None)
            if entry_root is not None:
                ws.data["hosts"][HOST]["remote_root"] = entry_root
            if shared is not None:
                ws.data["hosts"]["remote_root"] = shared
            ws.save()
        # the workspace-wide root, or none at all (the home), is never used
        for shared in ("/home/tester/locate-smoke", None):
            with self.subTest(shared=shared):
                with_root(None, shared)
                out = self.cf("informalize", "build-command", ID, "--host", HOST, code=2)
                self.assertIn(f"sets no remote_root for {HOST} itself", out)
                self.assertNotIn("rsync", out)
        # another run's root, the home, another id's folder
        for root in ("/home/tester/locate-smoke", "/home/tester",
                     "/home/tester/informalize-other-thm"):
            with self.subTest(root=root):
                with_root(root)
                out = self.cf("informalize", "build-command", ID, "--host", HOST, code=2)
                self.assertIn(f"its last part must be informalize-{ID}", out)
                self.assertIn(f"must be informalize-{ID}",
                              self.fetch(code=2))
        # this id's folder under ~: a leading ~ is quoted into a directory of that name
        with_root(f"~/informalize-{ID}")
        self.assertIn("absolute", self.cf("informalize", "build-command", ID, "--host", HOST,
                                          code=2))
        with_root(f"/home/tester/runs/informalize-{ID}")
        out = self.cf("informalize", "build-command", ID, "--host", HOST)
        self.assertIn(f"cd /home/tester/runs/informalize-{ID} && ", out)

    def test_status_names_the_next_step(self):
        self.assertIn(f"cf informalize build-command {ID} --host <host>",
                      self.cf("informalize", "status", ID))
        self.cf("informalize", "build-command", ID, "--host", HOST)
        self.assertIn(f"cf informalize fetch-library {ID} --host <host> --execute",
                      self.cf("informalize", "status", ID))


class TestFetchLibrary(_Workspace):

    def setUp(self):
        super().setUp()
        self.write_host()

    def test_a_dry_run_runs_nothing(self):
        fake = FakeHost()
        out = self.fetch(fake=fake)
        self.assertEqual(fake.calls, [])
        self.assertIn("dry run", out)
        self.assertIn(f"test -f {self.host}/library/host/DONE", out)
        self.assertIn(f"{SSH}:{self.host}/library/index/{LIB}@{self.commit[:12]}/", out)
        self.assertFalse(self.paths.informalize_library_src(ID).exists())

    def test_executing_needs_consent(self):
        fake = FakeHost()
        out = self.fetch("--execute", code=2, fake=fake)
        self.assertIn("--i-have-consent", out)
        self.assertEqual(fake.calls, [])

    def test_it_fetches_checks_and_records(self):
        before = self.commits()
        out = self.fetch("--execute", "--i-have-consent")
        self.assertIn("canary passed", out)
        lib = self.paths.informalize_library(ID)
        for rel in ("host/DONE", "host/src.tar", "index/index.jsonl", "index/manifest.json",
                    "deps.json", "src/ExampleLib/Topology/Glue/Basic.lean"):
            with self.subTest(file=rel):
                self.assertTrue((lib / rel).is_file())
        self.assertFalse((lib / "index" / "work").exists())
        self.assertFalse((lib / ".incoming").exists())
        self.assertFalse((lib / "src" / "README").exists())     # a link is never made
        self.assertEqual((lib / "src" / "LICENSE").stat().st_mode & 0o777, 0o444)
        settings_ = read_yaml(self.paths.informalize_yaml(ID))
        self.assertEqual(settings_["library"]["license"], "Apache-2.0")
        self.assertNotIn("license_method", settings_["library"])
        stamp = json.loads((self.repo / "informal/informalize.json").read_text(encoding="utf-8"))
        self.assertEqual(stamp["source_date_epoch"], 1700000000)
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.git("status", "--porcelain"), "")
        receipt = self.receipt("I1")
        checks = receipt["checks"]
        self.assertEqual(checks["commit"], self.commit)
        self.assertTrue(checks["canary"]["passed"])
        self.assertEqual(checks["canary"]["form"], "plain")
        self.assertEqual(checks["theorem"]["nonstandard_axioms"], [])
        self.assertEqual(checks["license"]["spdx"], "Apache-2.0")
        self.assertEqual(checks["source"]["links_skipped"], ["README"])
        self.assertEqual(checks["consent"]["host"], HOST)
        self.assertIn("library/index/index.jsonl", receipt["outputs"])
        ran = json.loads((lib / "host" / "drivers.json").read_text(encoding="utf-8"))
        self.assertIn(f"drivers/{library.HOST_SCRIPT}", ran)
        self.assertEqual(receipt["inputs"], {
            "informalize.yaml": receipt["inputs"]["informalize.yaml"],
            **{f"library/to-host/{name}": sha for name, sha in ran.items()}})
        self.cf("informalize", "sync", ID, "--check")
        # a second fetch of the same files changes nothing
        self.fetch("--execute", "--i-have-consent")
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.receipt("I1"), receipt)
        # and init with its first flags is still the same settings
        self.init()

    def test_a_job_that_has_not_finished_or_failed_is_refused(self):
        (self.host / "library" / "host" / "DONE").unlink()
        self.assertIn("DONE yet", self.fetch("--execute", "--i-have-consent", code=2))
        self.assertFalse(self.paths.informalize_library_src(ID).exists())
        (self.host / "library" / "host" / "DONE").write_text("3\n")
        out = self.fetch("--execute", "--i-have-consent", code=2)
        self.assertIn("status '3'", out)
        self.assertIn("library_host.log", out)

    def test_a_failed_job_is_named_before_anything_else_is_copied(self):
        shutil.rmtree(self.host / "library")
        self.write_host(done="1")
        (self.host / "library" / "deps.json").unlink()
        shutil.rmtree(self.host / "library" / "index")
        fake = FakeHost()
        out = self.fetch("--execute", "--i-have-consent", code=2, fake=fake)
        self.assertIn("status '1'", out)
        self.assertIn("library_host.log", out)
        self.assertEqual([argv[0] for argv in fake.calls], ["ssh", "rsync"])

    def test_drivers_other_than_the_ones_that_ran_are_refused(self):
        to_host = self.paths.informalize_library_to_host(ID)
        script = to_host / "drivers" / "scripts" / "informalize" / "library_host.sh"
        script.write_text(script.read_text(encoding="utf-8") + "# changed since\n")
        out = self.fetch("--execute", "--i-have-consent", code=2)
        self.assertIn("not what the host job ran: 1 file(s) differ "
                      "(drivers/scripts/informalize/library_host.sh)", out)
        self.assertFalse(self.paths.informalize_library_src(ID).exists())
        (self.host / "library" / "host" / "drivers.json").unlink()
        self.assertIn("no host/drivers.json", self.fetch("--execute", "--i-have-consent",
                                                         code=2))

    def test_a_dual_licensed_library_takes_one_of_its_licenses_by_hand(self):
        mit = (LICENSES / "MIT.txt").read_text(encoding="utf-8")
        shutil.rmtree(self.host / "library")
        self.write_host(files={k: v for k, v in source_files().items() if k != "LICENSE"})
        license_dir = self.host / "library" / "host" / "license"
        (license_dir / "LICENSE-APACHE").write_text(APACHE, encoding="utf-8")
        (license_dir / "LICENSE-MIT").write_text(mit, encoding="utf-8")
        out = self.fetch("--execute", "--i-have-consent", code=2)
        self.assertIn("name Apache-2.0, MIT, so pick the one", out)
        self.assertIn("--library-license", out)
        self.init("--library-license", "BSD-3-Clause", "--force")
        self.assertIn("name Apache-2.0, MIT, informalize.yaml says BSD-3-Clause",
                      self.fetch("--execute", "--i-have-consent", code=2))
        self.init("--library-license", "MIT", "--force")
        self.fetch("--execute", "--i-have-consent")
        block = self.receipt("I1")["checks"]["license"]
        self.assertEqual((block["spdx"], block["method"], block["named"]),
                         ("MIT", "manual", ["Apache-2.0", "MIT"]))
        self.assertEqual(read_yaml(self.paths.informalize_yaml(ID))["library"]["license"], "MIT")

    def test_a_failed_check_leaves_the_last_fetch_in_place(self):
        self.fetch("--execute", "--i-have-consent")
        src = self.paths.informalize_library_src(ID)
        receipt = self.receipt("I1")
        (self.host / "library" / "host" / "commit").write_text("f" * 40 + "\n")
        out = self.fetch("--execute", "--i-have-consent", code=2)
        self.assertIn("HEAD is 'ffffffffffff'", out)
        self.assertTrue((src / "ExampleLib/Topology/Glue/Basic.lean").is_file())
        self.assertEqual(self.receipt("I1"), receipt)
        self.assertTrue((self.paths.informalize_library(ID) / ".incoming" / "host").is_dir())

    def test_each_check(self):
        broken_canary = canary_records()
        broken_canary[0] = dict(broken_canary[0], proof_status="proved", nonstandard_axioms=[])
        mit = (LICENSES / "MIT.txt").read_text(encoding="utf-8")
        no_theorem = [r for r in records() if r["name"] != THEOREM]
        no_bodies = deps_doc()
        no_bodies["repo_constants"][0]["value_deps"] = []
        cases = {
            "the canary failed": dict(canary=broken_canary),
            "driver's state says": dict(state_commit="e" * 40),
            "dump of root module": dict(deps=dict(deps_doc(), root_module=DEFS)),
            "0 public records": dict(decls=no_theorem),
            "none of the 2 module files the index names opens": dict(
                files=source_files("plain"), form="module"),
            "1 of the 2 module files the index names open": dict(
                files=source_files("module"), form="plain"),
            "proof's body was not loaded": dict(deps=no_bodies),
            "license is not known": dict(files={**source_files(), "LICENSE": "All mine."}),
            "not in src.tar": dict(files={k: v for k, v in source_files().items()
                                          if not k.endswith("Defs.lean")}),
        }
        for needle, kwargs in cases.items():
            with self.subTest(needle=needle):
                shutil.rmtree(self.host / "library")
                self.write_host(**kwargs)
                out = self.fetch("--execute", "--i-have-consent", code=2)
                self.assertIn(needle, out)
        shutil.rmtree(self.host / "library")
        self.write_host(files={**source_files(), "LICENSE": mit})
        self.init("--library-license", "Apache-2.0", "--force")
        self.assertIn("name MIT, informalize.yaml says Apache-2.0",
                      self.fetch("--execute", "--i-have-consent", code=2))

    def test_a_manual_license_stands_when_the_file_names_none(self):
        shutil.rmtree(self.host / "library")
        self.write_host(files={**source_files(), "LICENSE": "All mine."})
        self.init("--library-license", "LicenseRef-Mine", "--force")
        self.fetch("--execute", "--i-have-consent")
        settings_ = read_yaml(self.paths.informalize_yaml(ID))
        self.assertEqual(settings_["library"]["license"], "LicenseRef-Mine")
        self.assertEqual(settings_["library"]["license_method"], "manual")
        self.assertEqual(self.receipt("I1")["checks"]["license"]["method"], "manual")

    def test_an_archive_that_leaves_the_folder_is_refused(self):
        host = self.host / "library" / "host"
        write_tar(host / "src.tar", source_files(), extra=("../escape.lean",))
        out = self.fetch("--execute", "--i-have-consent", code=2)
        self.assertIn("outside the archive", out)
        self.assertFalse((self.paths.informalize_library(ID).parent / "escape.lean").exists())


class TestScope(_Workspace):

    def setUp(self):
        super().setUp()
        self.write_host()

    def test_it_needs_a_current_i1(self):
        self.assertIn("stage I1 has not run", self.cf("informalize", "scope", ID, code=2))
        self.fetch("--execute", "--i-have-consent")
        (self.paths.informalize_library(ID) / "deps.json").write_text("{}")
        out = self.cf("informalize", "scope", ID, code=2)
        self.assertIn("changed since stage I1", out)

    def test_it_writes_the_scope_and_the_declarations(self):
        self.fetch("--execute", "--i-have-consent")
        before = self.commits()
        out = self.cf("informalize", "scope", ID)
        self.assertIn("4 declarations", out)
        scope = json.loads(self.paths.informalize_scope(ID).read_text(encoding="utf-8"))
        self.assertEqual(scope["schema"], scope_mod.SCOPE_SCHEMA)
        self.assertEqual(scope["theorem"], THEOREM)
        self.assertEqual([m["name"] for m in scope["members"]],
                         sorted(m["name"] for m in scope["members"]))
        declared = json.loads((self.repo / "informal/declarations.json").read_text("utf-8"))
        self.assertEqual(declared["schema"], scope_mod.DECLARATIONS_SCHEMA)
        self.assertEqual(itemtable.declarations_problems(declared), [])
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.git("status", "--porcelain"), "")
        receipt = self.receipt("I2")
        self.assertEqual(receipt["checks"]["members"], 4)
        self.assertEqual(receipt["checks"]["type_only"], 3)
        # the same inputs write the same bytes and no commit
        self.cf("informalize", "scope", ID)
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.receipt("I2"), receipt)
        self.assertIn("cf plan-informalize", self.cf("informalize", "status", ID))


# ---------------------------------------------------------------------------
# the host script, end to end with stub tools
# ---------------------------------------------------------------------------

#: the oldest bash the host drivers run on (tests/test_locate_corpus_field.py DRIVER_BASH)
DRIVER_BASH = (4, 3)


def modern_bash() -> str | None:
    for candidate in (shutil.which("bash"), "/bin/bash", "/usr/bin/bash",
                      "/opt/homebrew/bin/bash", "/usr/local/bin/bash"):
        if not candidate or not os.path.exists(candidate):
            continue
        got = subprocess.run([candidate, "-c", "echo ${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"],
                             capture_output=True, text=True)
        try:
            version = tuple(int(x) for x in got.stdout.split())
        except ValueError:
            continue
        if version >= DRIVER_BASH:
            return candidate
    return None


def shard_files(directory: Path, stem: str, *, library_: str, roots: list[str],
                prefixes: list[str], modules: tuple, decls: list[dict], count: int = 8) -> None:
    """The probe template's `count` shard files, as `lake env lean` would write them:
    `<stem>-<i>.jsonl` holds shard i's header, modules (shard 0 only), declarations and end."""
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        mine = [d for d in decls if probe_merge.shard_of(d["name"], count) == i]
        lines = [{"record": "header", "schema": probe_merge.SCHEMA, "library": library_,
                  "toolchain": "4.34.0", "lean_githash": "f" * 40, "probe_revision": 2,
                  "roots": roots, "module_prefixes": prefixes, "module_count": len(modules),
                  "shard_index": i, "shard_count": count,
                  "shard_hash": "fnv1a64(utf8(name)) mod shard_count"}]
        if i == 0:
            lines += [{"record": "module", "name": n, "path": p, "module_doc": None,
                       "truncated": False} for n, p in modules]
        lines += mine
        lines.append({"record": "shard_end", "shard_index": i, "shard_count": count,
                      "constants_seen": 40, "declarations": len(mine), "skipped": {},
                      "closure_nodes": 1, "ms_total": 1, "ms_closure": 0, "ms_print": 0,
                      "ms_other": 0})
        (directory / f"{stem}-{i}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines), encoding="utf-8")


#: the stub `lake`: a build makes `.lake/build`, a probe shard copies its fixture, the dump
#: copies the fake deps.json to where the rendered DumpDeps.lean writes, and `build
#: --no-build` fails, so that the driver reaches the cache step
LAKE = r"""#!/bin/sh
echo "lake $*" >> "$HOME/lake.log"
case "$1 $2" in
  "env true") exit 0 ;;
  "build --no-build") exit 1 ;;
  "build "*) mkdir -p .lake/build; exit 0 ;;
esac
if [ "$1 $2 $3" = "env lean --run" ]; then
  echo "nofile $(ulimit -n)" >> "$HOME/lake.log"
  out=$(sed -n 's/.*IO.FS.writeFile "\([^"]*\)".*/\1/p' "$4" | head -n 1)
  exec cp "$HOME/deps.json" "$out"
fi
if [ "$1 $2" = "env lean" ]; then
  case "$PWD" in */canary/*) stem=canary ;; *) stem=library ;; esac
  exec cp "$HOME/shards/$stem-$SHARD_INDEX.jsonl" "$PROBE_OUTPUT"
fi
exit 9
"""


class TestTheHostScriptEndToEnd(_Workspace):
    """build-command's copies, run as the host runs them, then fetch-library and scope."""

    def setUp(self):
        self.bash = modern_bash()
        if self.bash is None:
            self.skipTest("the host drivers need bash 4.3 or later")
        # the library, a real git repository at the commit init names
        lib_root = scratch(self) / "ExampleLib"
        lib_root.mkdir()
        for rel, text in source_files().items():
            (lib_root / rel).parent.mkdir(parents=True, exist_ok=True)
            (lib_root / rel).write_text(text, encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
               # two dates, so that the committer time is the one recorded
               "GIT_AUTHOR_DATE": "@1600000000 +0000", "GIT_COMMITTER_DATE": "@1700000000 +0000",
               "GIT_CONFIG_NOSYSTEM": "1"}
        for argv in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "pin"],
                     ["remote", "add", "origin", URL]):
            subprocess.run(["git", "-C", str(lib_root), *argv], check=True, env=env,
                           capture_output=True)
        self.commit = subprocess.run(["git", "-C", str(lib_root), "rev-parse", "HEAD"],
                                     check=True, capture_output=True, text=True).stdout.strip()
        self.lib_root = lib_root
        super().setUp()

    def stub(self, bindir: Path, name: str, body: str) -> None:
        (bindir / name).write_text(body if body.startswith("#!") else f"#!/bin/sh\n{body}\n",
                                   encoding="utf-8")
        (bindir / name).chmod(0o755)

    def test_the_host_script_and_the_mac_agree(self):
        self.cf("informalize", "build-command", ID, "--host", HOST)
        to_host = self.paths.informalize_library_to_host(ID)
        # the two rsyncs of the printed lines, as a local copy
        shutil.copytree(to_host / "drivers", self.host / "drivers")
        shutil.copy(to_host / "library.tsv", self.host / "library.tsv")
        atlas = self.host / "library"
        # the driver finds the library already cloned at the commit (no network here)
        shutil.copytree(self.lib_root, atlas / "libs" / LIB, symlinks=True)
        home = self.host / "home"
        bindir = home / ".elan" / "bin"
        bindir.mkdir(parents=True)
        self.stub(bindir, "lake", LAKE)
        self.stub(bindir, "elan", "exit 0")
        self.stub(bindir, "ionice", 'shift; exec "$@"')
        self.stub(bindir, "flock", "exit 0")
        self.stub(bindir, "timeout", 'echo "timeout $*" >> "$HOME/timeout.log"\n'
                                     'while [ "${1#-}" != "$1" ]; do shift; done; shift; exec "$@"')
        self.stub(bindir, "df", 'echo " Avail"; for a in "$@"; do case "$a" in -*) ;; '
                                '*) echo " 9999G";; esac; done')
        for tool, target in (("python3", sys.executable), ("git", shutil.which("git")),
                             ("bash", self.bash)):
            (bindir / tool).symlink_to(target)
        shard_files(home / "shards", "library", library_=LIB, roots=[MODULE], prefixes=[PREFIX],
                    modules=MODULES, decls=records())
        shard_files(home / "shards", "canary", library_="cf-canary", roots=["CfCanary"],
                    prefixes=["CfCanary"], modules=(("CfCanary", "CfCanary.lean"),
                                                    ("CfCanary.Basic", "CfCanary/Basic.lean")),
                    decls=canary_records())
        (home / "deps.json").write_text(json.dumps(deps_doc()), encoding="utf-8")
        env = {"HOME": str(home), "USER": "tester", "PATH": f"{bindir}:/usr/bin:/bin",
               "ATLAS_ROOT": str(atlas), "MIN_FREE_GB": "60", "LEAN_THREADS": "16",
               "DUMP_TIMEOUT": "777"}
        proc = subprocess.run([self.bash, "drivers/scripts/informalize/library_host.sh",
                               "library.tsv"], cwd=self.host, env=env, capture_output=True,
                              text=True, timeout=300)
        logs = proc.stdout + proc.stderr
        for path in sorted((atlas / "host").glob("*.log")) + sorted(
                (atlas / "logs").rglob("*.log")):
            logs += f"\n--- {path}\n" + path.read_text(encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, logs)
        host = atlas / "host"
        self.assertEqual((host / "DONE").read_text(), "0\n")
        self.assertEqual((host / "commit").read_text().strip(), self.commit)
        self.assertEqual((host / "commit-time").read_text().strip(), "1700000000")
        self.assertEqual((host / "canary-form").read_text().strip(), "plain")
        self.assertTrue((host / "license" / "LICENSE").is_file())
        # LIBRARY_CACHE=0: the library's own cache script never ran, and the log says so
        self.assertFalse((atlas / "libs" / LIB / ".lake" / "library-cache-ran").exists())
        self.assertIn("LIBRARY_CACHE=0", (host / "logs" / f"{LIB}.log").read_text())
        with tarfile.open(host / "src.tar") as tar:
            files = [m.name for m in tar.getmembers() if m.isfile()]
        # the .lean files only: no license, lakefile, script or agent instructions
        self.assertEqual(sorted(files), sorted(p for _m, p in MODULES))
        self.assertEqual(json.loads((host / "drivers.json").read_text()),
                         library_host.digest(to_host / "drivers", to_host / "library.tsv"))
        lake = (home / "lake.log").read_text()
        self.assertIn(f"lake build {MODULE}", lake)
        self.assertIn("lake env lean --run", lake)
        # the dump ran with the open-file limit raised and under its own timeout
        nofile = re.search(r"^nofile (\S+)$", lake, re.M).group(1)
        self.assertTrue(nofile == "unlimited" or int(nofile) >= 65536, nofile)
        # dump_deps.sh prints the command it runs; the `timeout` in it is the one of the
        # PATH it sets for lake (`PATH=/usr/bin:$PATH`), which is the host's own on a box
        # that has one, so the stub's log is not where this is read.
        self.assertIn("timeout 777 lake env lean --run",
                      (host / "dump_deps.log").read_text(encoding="utf-8"))

        out = self.fetch("--execute", "--i-have-consent")
        self.assertIn("canary passed on Lean 4.34.0 (plain form)", out)
        out = self.cf("informalize", "scope", ID)
        self.assertIn("4 declarations", out)
        self.assertEqual(self.git("status", "--porcelain"), "")


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
