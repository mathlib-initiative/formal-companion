"""Regression tests for the core CLI, the YAML writer, packaging and preflight.

Every case here failed before the change it guards:

* the hand-rolled YAML writer emitted scalars cf's own readers reject or alter
  (a tab in a harvested book title made ``registry.yaml`` unparseable);
* the one test that was supposed to notice a broken command module filtered on
  ``mod is None``, which ``discover_commands`` never stores, so the whole suite
  passed with a syntactically broken ``cfcmd`` module in the tree;
* ``main()`` raised CfError and only the ``__main__`` block rendered it, so any
  other entry point printed a traceback;
* the documented Python floor was enforced nowhere;
* ``cf init`` wrote two home-directory globs, which were both the search roots
  and the allowlist for absolute paths a companion repo names;
* ``cf onboard`` exited 0 then 2 for the identical command;
* there was no ``cf`` on PATH, no LICENSE, no packaging metadata and no way to
  find out that poppler was missing before a page render needed it.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                          # noqa: E402
from cflib import templates, yamlio                           # noqa: E402
from cflib.errors import CfError                              # noqa: E402
from cflib.registry import Registry, RegistryEntry            # noqa: E402
from cflib.workspace import DEFAULT_LIBRARY_PATHS, Workspace   # noqa: E402

PUBLISHED_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
DRAFT_SHA = "62d9565841b8593a7a3d12b3cbcea6ce8d3d3ae2f83b60ebf024d59714c0c6e8"
PUBLISHED_ID = "tao-measure-gsm126-published"


# ---------------------------------------------------------------------------
# architecture#1 -- the writer and the readers must agree
# ---------------------------------------------------------------------------

#: Scalars that have to survive dump -> load. The first four are the reported
#: defect (a harvested title with a control character, or one that arrives
#: already quoted); the rest are the shapes YAML types for you if they are not
#: quoted, and the block-scalar chomping cases.
NASTY_SCALARS = (
    "An Introduction to\tMeasure Theory",
    '"quoted"',
    "'single'",
    "a\rb",
    "a\x00b",
    "x\x7fy",
    "a\x85b",
    "a b",
    "﻿bom",
    "\x1b[31mred",
    "",
    " lead",
    "tail ",
    "-lead",
    "no",
    "yes",
    "Off",
    "null",
    "~",
    "0755",
    "0x10",
    "1_000",
    ".inf",
    "3.14",
    "1e5",
    "2026-09-16",
    "# hash",
    "a: b",
    "[1]",
    "{}",
    "*star",
    "&amp",
    "!bang",
    "|pipe",
    ">gt",
    "%pct",
    "@at",
    "`tick",
    "back\\slash",
    'q"mid',
    "a\nb",
    "a\nb\n",
    "a\n\nb\n",
    "a\n  indented\nb\n",
    "  leading\nb\n",
    "a\ntrailing  \n",
    "a\nb\n\n",
    "\na\n",
    "line\n",
    "tab\tin\tmiddle",
    "ünïcode",
)


class TestYamlRoundTrip(unittest.TestCase):
    """dump_yaml -> load_yaml AND load_yaml_minimal, byte for byte."""

    def test_every_nasty_scalar_survives_both_readers(self):
        for raw in NASTY_SCALARS:
            for label, wrap in (("mapping value", lambda v: {"t": v}),
                                ("sequence item", lambda v: [v]),
                                ("nested", lambda v: {"a": {"b": [v, {"c": v}]}})):
                obj = wrap(raw)
                text = yamlio.dump_yaml(obj)
                with self.subTest(scalar=raw, position=label):
                    self.assertEqual(yamlio.load_yaml(text), obj,
                                     f"PyYAML read {text!r} differently")
                    self.assertEqual(yamlio.load_yaml_minimal(text), obj,
                                     f"the fallback reader read {text!r} differently")

    def test_the_two_readers_never_disagree_with_each_other(self):
        for raw in NASTY_SCALARS:
            text = yamlio.dump_yaml({"t": raw})
            with self.subTest(scalar=raw):
                self.assertEqual(yamlio.load_yaml(text), yamlio.load_yaml_minimal(text))

    def test_the_bytes_do_not_depend_on_whether_pyyaml_is_installed(self):
        """workspace.yaml and registry.yaml are committed and hand-diffed."""
        source = (SCRIPTS / "cflib" / "yamlio.py").read_text(encoding="utf-8")
        writer = source.split("# writer", 1)[1]
        self.assertNotIn("HAVE_PYYAML", writer)
        self.assertNotIn("_pyyaml", writer)

    def test_a_harvested_title_with_a_tab_does_not_brick_the_overlay(self):
        """The end-to-end repro: append_overlay then Registry.load()."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ws"
            self.assertEqual(_quiet(cf_main.main, ["init", str(root), "--no-git", "-q"]), 0)
            title = "An Introduction to\tMeasure Theory"
            Registry.load(root).append_overlay(
                root, RegistryEntry(id="tao-meas-2011", sha256="a" * 64, title=title))
            reloaded = Registry.load(root)
            self.assertEqual(reloaded.entries["tao-meas-2011"].title, title)


# ---------------------------------------------------------------------------
# architecture#3 -- a broken command module must be loud
# ---------------------------------------------------------------------------

BROKEN_MODULE = "this is not python(\n"


class TestBrokenCommandIsVisible(unittest.TestCase):
    """The stub `discover_commands` registers must be findable by the test."""

    def _discover(self):
        import argparse

        parser = argparse.ArgumentParser()
        return cf_main.discover_commands(parser.add_subparsers(dest="command"))

    def test_a_broken_module_is_reported_as_a_broken_command(self):
        import cfcmd
        import importlib

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wobbly.py").write_text(BROKEN_MODULE, encoding="utf-8")
            cfcmd.__path__.append(tmp)
            importlib.invalidate_caches()
            try:
                mapping = self._discover()
            finally:
                cfcmd.__path__.remove(tmp)
                sys.modules.pop("cfcmd.wobbly", None)
                importlib.invalidate_caches()

        self.assertIn("wobbly", mapping)
        stub = mapping["wobbly"]
        self.assertIsInstance(stub, cf_main._BrokenCommand)
        # The predicate the suite uses has to see it. `mod is None` did not.
        self.assertEqual([n for n, m in mapping.items()
                          if isinstance(m, cf_main._BrokenCommand)], ["wobbly"])
        self.assertEqual([n for n, m in mapping.items() if m is None], [],
                         "discover_commands never stores None; a test that filters on it "
                         "cannot fail")
        with self.assertRaises(CfError) as ctx:
            stub.run(None)
        self.assertIn("failed to import", str(ctx.exception))
        self.assertIn("wobbly", str(ctx.exception))

    def test_a_healthy_tree_has_no_broken_command(self):
        mapping = self._discover()
        broken = {n: m.exc for n, m in mapping.items()
                  if isinstance(m, cf_main._BrokenCommand)}
        self.assertEqual(broken, {})


# ---------------------------------------------------------------------------
# first-run#5 / architecture#5 -- the floor, the error handler, the shim
# ---------------------------------------------------------------------------

class TestPythonFloor(unittest.TestCase):
    def test_an_older_interpreter_is_refused_in_one_sentence(self):
        msg = cf_main.python_too_old((3, 9, 6))
        self.assertTrue(msg.startswith("error: "), msg)
        self.assertIn("3.11", msg)
        self.assertIn("3.9", msg)
        self.assertEqual(len(msg.splitlines()), 1, "the refusal is one sentence")

    def test_the_floor_itself_passes(self):
        self.assertEqual(cf_main.python_too_old(cf_main.MIN_PYTHON + (0,)), "")
        self.assertEqual(cf_main.python_too_old(), "")

    def test_the_floor_is_the_same_number_everywhere(self):
        floor = "%d.%d" % cf_main.MIN_PYTHON
        pyproject = (SKILL / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'requires-python = ">={floor}"', pyproject)
        workflow = yamlio.read_yaml(SKILL / ".github" / "workflows" / "tests.yml")
        versions = workflow["jobs"]["pytest"]["strategy"]["matrix"]["python-version"]
        self.assertEqual(str(versions[0]), floor,
                         "CI must run the oldest version the CLI accepts")


class TestCliRendersErrorsInsteadOfTracebacks(unittest.TestCase):
    def test_cli_returns_the_exit_code_and_prints_error_and_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["status", "--workspace", str(Path(tmp) / "nope")]
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = cf_main.cli(argv)
            self.assertEqual(rc, 2)
            self.assertIn("error: ", err.getvalue())
            self.assertIn("hint:", err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())

    def test_main_still_raises_for_a_library_caller(self):
        """tests/test_integration.py documents this contract; keep it."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CfError):
                _quiet(cf_main.main, ["status", "--workspace", str(Path(tmp) / "nope")])


class TestCfOnPath(unittest.TestCase):
    """first-run#6: the hints print `cf ...`, so a `cf` has to exist."""

    SHIM = SKILL / "bin" / "cf"

    def test_the_shim_exists_and_is_executable(self):
        self.assertTrue(self.SHIM.is_file(), f"{self.SHIM} is missing")
        self.assertTrue(os.access(self.SHIM, os.X_OK), f"{self.SHIM} is not executable")

    def test_running_the_shim_prints_the_version(self):
        res = subprocess.run([str(self.SHIM), "--version"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(res.stdout.startswith("cf "), res.stdout)

    def test_a_symlinked_shim_finds_the_checkout_it_points_into(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "cf"
            link.symlink_to(self.SHIM)
            res = subprocess.run([str(link), "--version"], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(res.stdout.startswith("cf "), res.stdout)

    def test_the_shim_renders_a_refusal_as_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = subprocess.run([str(self.SHIM), "status", "--workspace",
                                  str(Path(tmp) / "nope")], capture_output=True, text=True)
            self.assertEqual(res.returncode, 2)
            self.assertIn("error: ", res.stderr)
            self.assertNotIn("Traceback", res.stderr)


class TestRepositoryFurniture(unittest.TestCase):
    """architecture#5 / docs-accuracy#7: the metadata an adopter needs."""

    def test_the_licence_is_apache_2_0_with_a_real_copyright_line(self):
        text = (SKILL / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0, January 2004", text)
        self.assertIn("Copyright 2026 the companion-faithfulness contributors", text)
        self.assertNotIn("[name of copyright owner]", text)

    def test_pyproject_declares_the_runtime_contract(self):
        text = (SKILL / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.11"', text)
        self.assertIn("dependencies = []", text)     # stdlib only (CLAUDE.md S3)
        self.assertIn("yaml = [", text)              # PyYAML is an extra, not a dependency
        self.assertIn("[project.optional-dependencies]", text)

    def test_nothing_shipped_names_a_developer_home(self):
        for rel in ("pyproject.toml", "LICENSE", "bin/cf",
                    ".github/workflows/tests.yml", "tests/conftest.py",
                    "scripts/cfcmd/doctor.py", "scripts/cf.py"):
            path = SKILL / rel
            if not path.exists():        # a file a later commit adds
                continue
            with self.subTest(file=rel):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("/Users/", text)
                self.assertNotIn("~/Desktop", text)


# ---------------------------------------------------------------------------
# tests#1 / tests#3 -- the suite itself
# ---------------------------------------------------------------------------

class TestSuiteIsHermetic(unittest.TestCase):
    def test_conftest_points_home_at_a_temporary_directory(self):
        if os.environ.get("CF_TEST_KEEP_HOME") == "1":
            self.skipTest("CF_TEST_KEEP_HOME=1 asks for the real home")
        if "pytest" not in sys.modules:
            self.skipTest("tests/conftest.py is a pytest hook")
        self.assertIn("cf-test-home-", os.environ.get("HOME", ""),
                      "tests/conftest.py should have redirected HOME")

    def test_no_test_module_imports_pyyaml_unguarded(self):
        """PyYAML is optional; one bare import failed five tests without it."""
        offenders = []
        for path in sorted(HERE.glob("test_*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for n, line in enumerate(lines, 1):
                if re.fullmatch(r"\s*import yaml\s*", line):
                    window = "\n".join(lines[max(0, n - 6):n + 4])
                    if "try:" not in window and "HAVE_PYYAML" not in window:
                        offenders.append(f"{path.name}:{n}")
        self.assertEqual(offenders, [], "use cflib.yamlio, or guard with a skipTest")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _quiet(fn, *args, **kwargs):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


def _workspace(root: Path) -> Workspace:
    """A minimal valid workspace on disk, with the shipped library_paths."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(
        "version: 2\nworkspace:\n  name: t\ncompanions: {}\nsources: {}\n"
        "hosts:\n  allowlist: []\nlibrary_paths:\n  - ~/Downloads\n", encoding="utf-8")
    return Workspace.load(root, validate=False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
