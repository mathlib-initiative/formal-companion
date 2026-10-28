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

# The first import, before anything reads HOME: it pins HOME at a temporary
# directory for `unittest discover`, which loads no conftest.py.  The second
# spelling is for `python3 -m unittest tests.<module>`.  See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

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
    # A final line that is whitespace-only but NOT empty passed the old
    # `_block_safe`, was written as `|-`, and the fallback reader's trailing-blank
    # chomp then dropped it while PyYAML kept it.
    "a\n ",
    "a\n  \nb",
    # A leading quote mark AND ` #` AND a trailing backslash together: the minimal
    # reader's comment stripper read the writer's `\"` as the CLOSING quote and cut
    # the value at the ` #` that followed.
    '" #_b\\',
    # A flow indicator cannot OPEN a plain scalar even in block context, so a bare
    # leading comma made PyYAML refuse the whole file.
    ", and other notes",
    # `: &c` inside a double-quoted scalar is not an anchor; the fallback reader's
    # anchor check used to refuse a file the writer had just produced.
    "Measure Theory: &c",
)

#: The alphabet the randomised half of the round-trip battery draws from: every
#: character that means something to a YAML scanner, plus the control characters
#: and separators a harvested title can carry.
_FUZZ_ALPHABET = tuple("ab #:\"'\\|>-\n\t \r{}[]*&!%@`~,?0179.eE_x/") + (
    "\x00", "\x1b", "\x85", "\u2028", "\ufeff", "ü")


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

    def test_a_random_battery_survives_both_readers(self):
        """The property, not a list: whatever cf writes, both readers read back.

        Seeded, so a failure is reproducible and the suite's runtime is fixed. The
        hand-picked cases above are the ones this found; it stays because the next
        scanner character someone forgets will not be on that list.
        """
        import random

        rng = random.Random(20260919)
        for _ in range(1500):
            raw = "".join(rng.choice(_FUZZ_ALPHABET)
                          for _ in range(rng.randint(0, 12)))
            for wrap in (lambda v: {"t": v}, lambda v: [v],
                         lambda v: {"a": {"b": [v, {"c": v}]}}):
                obj = wrap(raw)
                text = yamlio.dump_yaml(obj)
                if yamlio.load_yaml(text) != obj or yamlio.load_yaml_minimal(text) != obj:
                    self.fail(f"{raw!r} dumped to {text!r} does not round-trip")

    def test_a_float_survives_both_readers_with_or_without_an_exponent(self):
        """Python spells 1e-05 and 1e+16 with no point, which YAML 1.1 and the fallback
        reader both read as a string: a campaign's ``--unpriced-usd-per-hour 1e-5`` came
        back as ``'1e-05'``.  Seeded, over every magnitude a float has."""
        import random

        rng = random.Random(20261019)
        floats = [1e-05, 1e16, -2e-07, 5e-324, 1.7976931348623157e308, 1.5e-05,
                  20.000002, 0.1, 0.0, -0.0, 1.0]
        floats += [rng.choice((-1, 1)) * rng.random() * 10.0 ** rng.randint(-320, 300)
                   for _ in range(500)]
        for value in floats:
            text = yamlio.dump_yaml({"t": value, "s": [value]})
            for read in (yamlio.load_yaml, yamlio.load_yaml_minimal):
                got = read(text)
                with self.subTest(value=value, reader=read.__name__):
                    self.assertIsInstance(got["t"], float, text)
                    self.assertEqual((got["t"], got["s"]), (value, [value]), text)
        self.assertEqual(yamlio.dump_yaml({"t": 1e-05}), "t: 1.0e-05\n")
        self.assertEqual(yamlio.dump_yaml({"t": 0.25}), "t: 0.25\n")

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
        self.assertIn("Copyright 2026 the formal-companion contributors", text)
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
    def test_home_points_at_a_temporary_directory_under_either_loader(self):
        # No `"pytest" not in sys.modules` escape any more: the pin moved out of
        # the pytest hook into tests/_hermetic.py, so this case is meant to run
        # under `unittest discover` -- the loader it used to skip on is the one
        # that read the developer's ~/Downloads.
        if os.environ.get("CF_TEST_KEEP_HOME") == "1":
            self.skipTest("CF_TEST_KEEP_HOME=1 asks for the real home")
        self.assertIn("cf-test-home-", os.environ.get("HOME", ""),
                      "tests/_hermetic.py should have redirected HOME")
        self.assertEqual(os.path.expanduser("~"), os.environ["HOME"])

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
# first-run#3 -- the search roots are derived, not one machine's layout
# ---------------------------------------------------------------------------

class TestLibrarySearchRoots(unittest.TestCase):
    def test_the_shipped_default_names_no_absolute_layout(self):
        self.assertEqual(list(DEFAULT_LIBRARY_PATHS), ["~/Downloads"])
        text = templates.render_template("workspace.yaml.example", {
            "workspace_name": "t", "created": '"2026-01-01"'})
        data = yamlio.load_yaml_minimal(text)
        self.assertEqual(data["library_paths"], ["~/Downloads"])
        # the glob that walked the developer's Desktop, and made the allowlist a
        # home subtree every audited repo could point into
        self.assertNotIn("~/Desktop/**/sources", text.split("library_paths:")[1])

    def test_the_derived_roots_are_the_store_and_two_shapes_under_each_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ws = _workspace(root / "ws")
            self.assertEqual(ws.derived_library_paths(), [str(root / "ws" / "sources")])

            repos = root / "checkouts"
            (repos / "some-companion").mkdir(parents=True)
            ws.upsert_companion("some-companion", repo=str(repos / "some-companion"),
                                adapter="docstring_tags", sources=[])
            ws.save()
            ws = Workspace.load(root / "ws", validate=False)
            derived = ws.derived_library_paths()
            self.assertEqual(derived, [str(root / "ws" / "sources"),
                                       str(repos / "**" / "sources"),
                                       str(repos / "**" / ".lake" / "official-pdf")])
            # the BARE parent is the thing that must not appear: library_paths is
            # also the allowlist for an absolute path a repo names, and the parent
            # of a checkout kept on the desktop is the whole desktop.
            self.assertNotIn(str(repos), derived)
            for entry in derived[1:]:
                self.assertIn(entry, ws.library_paths)
            self.assertIn("~/Downloads", ws.library_paths)

    def test_a_derived_root_reaches_a_sibling_repos_pdf_but_not_a_loose_one(self):
        """The shapes have to keep resolving the PDF the old default resolved."""
        from cflib import discovery
        from cflib.workspace import derived_roots_for

        with tempfile.TemporaryDirectory() as tmp:
            repos = Path(tmp).resolve() / "checkouts"
            (repos / "companion").mkdir(parents=True)
            (repos / "sibling" / "sources").mkdir(parents=True)
            (repos / "sibling" / ".lake" / "official-pdf").mkdir(parents=True)
            wanted = repos / "sibling" / "sources" / "book.pdf"
            cached = repos / "sibling" / ".lake" / "official-pdf" / "official.pdf"
            loose = repos / "unrelated.pdf"
            for path in (wanted, cached, loose):
                path.write_bytes(b"%PDF-1.4\n")
            discovery._LIBRARY_CACHE.clear()
            try:
                found = set()
                for root in derived_roots_for(repos / "companion"):
                    found |= set(discovery._expand_library(root))
            finally:
                discovery._LIBRARY_CACHE.clear()
            self.assertIn(wanted, found)
            self.assertIn(cached, found)
            self.assertNotIn(loose, found, "a derived root is not the bare parent")

    def test_a_companion_whose_checkout_moved_is_skipped_not_walked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ws = _workspace(root / "ws")
            ws.upsert_companion("gone", repo=str(root / "not-here"),
                                adapter="docstring_tags", sources=[])
            ws.save()
            ws = Workspace.load(root / "ws", validate=False)
            self.assertEqual(ws.derived_library_paths(), [str(root / "ws" / "sources")])

    def test_cf_init_prints_the_effective_search_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ws"
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = cf_main.main(["init", str(root), "--no-git"])
            self.assertEqual(rc, 0)
            printed = out.getvalue()
            self.assertIn("pdf search roots", printed)
            self.assertIn("~/Downloads", printed)
            self.assertIn(str(root / "sources"), printed)


# ---------------------------------------------------------------------------
# first-run#2 -- onboard twice, same answer
# ---------------------------------------------------------------------------

class _FakeAdapter:
    """The `adapters.load` seam, reduced to what `cf onboard` calls."""

    #: (item, page anchor) per extracted row. The anchor check scores these against
    #: each candidate edition's items.json, so this is how a fixture decides whether
    #: the anchor is mechanically decidable.
    ROWS: tuple[tuple[str, int], ...] = (("Definition 1.1.1", 5),)

    def __init__(self, workspace=None):
        self.default_source_id = None

    def bind_default_source(self, source_id):
        self.default_source_id = source_id

    def _bind(self, sources):
        bound = dict(sources or {})
        sid = self.default_source_id or (next(iter(bound)) if bound else None)
        return sid, bound.get(sid)

    def dry_run(self, repo=None, *, sources=None):
        sid, _ = self._bind(sources)
        return {"declarations": 25, "tagged": 25, "items": 25, "rejects": [],
                "sources": [], "rows_by_source": {sid: len(self.ROWS)}}

    def extract(self, repo=None, *, sources=None):
        sid, sha = self._bind(sources)
        for n, (item, page) in enumerate(self.ROWS):
            yield {"item": item, "item_raw": item, "sub": "",
                   "item_kind": item.split()[0], "decl": f"Foo{n}", "kind": "def",
                   "namespace": "", "file": "A.lean", "line": 3 + n,
                   "statement": "theorem foo", "body": None,
                   "sorry": False, "sorry_class": "none", "page_anchor": page,
                   "page_anchor_raw": f"book p. {page}", "anchor_source": "own",
                   "repo": "ours", "key": True, "anonymous": False, "private": False,
                   "granularity": "item", "source_id": sid, "source_sha": sha}

    def key_inputs(self):
        return {"adapter_kind": "docstring_tags"}


#: Six items with the pages the PUBLISHED edition prints them on. The draft's
#: items.json shifts every one, so the mechanical check has a clear winner.
AGREEING_ROWS: tuple[tuple[str, int], ...] = (
    ("Definition 1.1.1", 5), ("Definition 1.1.2", 7), ("Lemma 1.1.3", 9),
    ("Theorem 1.1.4", 12), ("Exercise 1.1.5", 14), ("Proposition 1.1.6", 18),
)


class _AgreeingAdapter(_FakeAdapter):
    ROWS = AGREEING_ROWS


def _two_edition_repo(root: Path) -> Path:
    """A companion whose discovered sources are exactly TWO editions of one work.

    This is the shape first-run#2 is about, and the cluster has to be the ANCHOR's
    own: an earlier fixture also matched the unversioned `tao-measure` id, which
    won its single-candidate cluster at confidence 1.0, so `_confirm_anchor`
    returned early on `len(cluster) < 2` and the post-extraction path the finding
    is about was never exercised at all. Naming a PDF on the published line is what
    joins the title-only `formalization.yaml` claim to that edition (the documented
    "one signal names the WORK, another names the FILE" join), leaving the two
    registry editions and nothing else.

    Neither PDF resolves, which is every machine that does not already keep a copy.
    """
    repo = root / "two-editions"
    (repo / "Companion").mkdir(parents=True, exist_ok=True)
    (repo / "formalization.yaml").write_text(
        "version: v0.3\n"
        "sources:\n"
        "- title: An Introduction to Measure Theory\n"
        "  authors:\n"
        "  - Terence Tao\n"
        "  type: textbook\n", encoding="utf-8")
    # The truncated shas carry the ellipsis `_SHA_PREFIX_RE` looks for: a prefix
    # is a CANDIDATE the registry resolves to an edition, never an identity.
    (repo / "CLAUDE.md").write_text(
        "- The source PDF is not committed (copyrighted). Two editions exist locally.\n"
        f"  An earlier *draft* (sha256 `{DRAFT_SHA[:8]}…`) and the published book\n"
        f"  (`sources/measure-theory.pdf`, sha256 `{PUBLISHED_SHA[:8]}…`).\n"
        "  Both satisfy PDF page = book page + 16.\n"
        "  **Book-page anchors in docstrings follow the PUBLISHED pagination.**\n",
        encoding="utf-8")
    for i in range(25):
        (repo / "Companion" / f"File{i}.lean").write_text(
            f'/-- `Definition 1.1.{i + 1}` (book p. {20 + i}) -/\n'
            f"theorem foo{i} : True := trivial\n", encoding="utf-8")
    return repo


def _rival_editions_beside_the_anchor_repo(root: Path) -> Path:
    """A companion whose ANCHOR cluster is a single candidate, beside a rival pair.

    Same repo as :func:`_two_edition_repo` without the PDF path, so the title-only
    `formalization.yaml` claim stays a source of its own: three discovered ids, and
    the one that wins the anchor (at `single_candidate`, confidence 1.0) is not in
    the cluster that holds the two rival editions. `_confirm_anchor` used to re-elect
    only the anchor's OWN cluster and returned early on `len(cluster) < 2`, so the
    undecidable pair was never scored after extraction at all -- run 1 exited 0 and
    run 2, which had rows at the pre-extraction site, exited 2.
    """
    repo = _two_edition_repo(root)
    (repo / "CLAUDE.md").write_text(
        "- The source PDF is not committed (copyrighted). Two editions exist locally.\n"
        f"  An earlier *draft* (sha256 `{DRAFT_SHA[:8]}…`) and the published book\n"
        f"  (sha256 `{PUBLISHED_SHA[:8]}…`). Both satisfy PDF page = book page + 16.\n"
        "  **Book-page anchors in docstrings follow the PUBLISHED pagination.**\n",
        encoding="utf-8")
    return repo


def _seed_items(workspace_root: Path, source_id: str, pages: dict[str, int]) -> None:
    """Write a prepared source's items.json without needing a PDF or poppler.

    `_prepare_source` returns early for a source with no PDF, leaving whatever is
    already in `sources/<id>/`, so this is how a hermetic test gives the anchor
    check something to score.
    """
    out = workspace_root / "sources" / source_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "items.json").write_text(json.dumps({
        "source_id": source_id,
        "kinds": ["Definition", "Lemma", "Theorem", "Proposition", "Exercise"],
        "count": len(pages),
        "items": [{"item": item, "page": page} for item, page in pages.items()],
    }), encoding="utf-8")


class _OnboardCase(unittest.TestCase):
    """A workspace, a two-edition companion, and `adapters.load` stubbed out."""

    ADAPTER = _FakeAdapter

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.ws = self.root / "ws"
        self.repo = _two_edition_repo(self.root)
        self.assertEqual(_quiet(cf_main.main, ["init", str(self.ws), "--no-git", "-q"]), 0)
        # library_paths: [] -- no machine can reach either edition's PDF, which is
        # every machine that does not already keep a copy.
        ws = Workspace.load(self.ws)
        ws.data["library_paths"] = []
        ws.save()
        fake = types.ModuleType("adapters")
        fake.load = lambda adapter_yaml, workspace=None: self.ADAPTER(workspace)
        self._saved = sys.modules.get("adapters")
        sys.modules["adapters"] = fake

    def tearDown(self):
        if self._saved is not None:
            sys.modules["adapters"] = self._saved
        else:
            sys.modules.pop("adapters", None)
        self.tmp.cleanup()

    def _onboard(self, *extra):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = cf_main.cli(["onboard", str(self.repo), "--workspace", str(self.ws),
                              "--no-errata", "-q", *extra])
        return rc, err.getvalue()

    def _discovered_sources(self):
        return sorted(Workspace.load(self.ws, validate=False).sources)


class TestOnboardIsIdempotent(_OnboardCase):
    """The identical command must not exit 0 and then 2."""

    def test_the_fixture_really_is_two_editions_of_one_work(self):
        """Without this the class pins nothing: see `_two_edition_repo`."""
        rc, _ = self._onboard("--dry-run")
        self.assertEqual(rc, 0)
        rc, _ = self._onboard("--anchor-source", PUBLISHED_ID)
        self.assertEqual(rc, 0)
        self.assertEqual(self._discovered_sources(),
                         ["tao-measure-2011-draft", PUBLISHED_ID])

    def test_two_identical_onboards_give_the_same_exit_code(self):
        rc1, err1 = self._onboard()
        rc2, err2 = self._onboard()
        self.assertEqual(rc1, rc2,
                         f"first run exited {rc1}, second {rc2}\nfirst:\n{err1}\n"
                         f"second:\n{err2}")

    def test_an_undecidable_anchor_is_a_refusal_that_names_the_flag(self):
        """CLAUDE.md: below MIN_ANCHOR_AGREEMENT the command stops, both times."""
        for run in (1, 2):
            rc, err = self._onboard()
            with self.subTest(run=run):
                self.assertEqual(rc, 2, err)
                self.assertIn("cannot decide which edition", err)
                self.assertIn("--anchor-source", err)

    def test_the_healthy_path_is_still_idempotent_and_exits_zero(self):
        """The refusal must not swallow the case where the anchor IS decidable."""
        rc1, err1 = self._onboard("--anchor-source", PUBLISHED_ID)
        rc2, _ = self._onboard("--anchor-source", PUBLISHED_ID)
        self.assertEqual((rc1, rc2), (0, 0), err1)


class TestARivalPairOutsideTheAnchorsCluster(_OnboardCase):
    """The post-extraction check covers every multi-edition cluster, not just one."""

    def setUp(self):
        super().setUp()
        self.repo = _rival_editions_beside_the_anchor_repo(self.root)

    def test_the_undecidable_pair_is_refused_after_extraction_too(self):
        for run in (1, 2):
            rc, err = self._onboard()
            with self.subTest(run=run):
                self.assertEqual(rc, 2, err)
                self.assertIn("cannot decide which edition", err)


class TestAnAgreeingAnchorNeedsNoFlags(_OnboardCase):
    """A two-edition work whose anchors DO agree has a FLAGLESS onboard path.

    An anchor that is merely undetermined until this run has extracted is not an
    undecidable one. Refusing the provisional pre-extraction election refused every
    first onboard of any two-edition cluster, flags or not -- which is both of the
    target companions, and `tests/e2e/multisource.sh`'s stated invariant that they
    onboard with NO FLAGS.
    """

    ADAPTER = _AgreeingAdapter

    def setUp(self):
        super().setUp()
        _seed_items(self.ws, PUBLISHED_ID, dict(AGREEING_ROWS))
        # the same items, four pages earlier: the pre-publication pagination.
        _seed_items(self.ws, "tao-measure-2011-draft",
                    {item: page - 4 for item, page in AGREEING_ROWS})

    def test_no_flags_exits_zero_twice_and_pins_the_agreeing_edition(self):
        for run in (1, 2):
            rc, err = self._onboard()
            with self.subTest(run=run):
                self.assertEqual(rc, 0, err)
                companion = Workspace.load(self.ws).companion("two-editions")
                self.assertEqual(companion.anchor_source, PUBLISHED_ID)

    def test_the_two_runs_search_the_same_pdf_roots(self):
        """Discovery must not widen between run 1 and run 2 (CLAUDE.md S4).

        `derived_library_paths` reads `workspace.companions`, and `upsert_companion`
        for the repo being onboarded runs AFTER the PDFs are resolved -- so run 1
        searched without this repo's own roots and run 2 searched with them, and two
        identical commands differed in whether the ANCHOR edition had text at all.
        """
        from cflib import discovery

        seen: list[list[str]] = []
        real = discovery.resolve_claims

        def spy(claims, **kw):
            seen.append(sorted(kw.get("library_paths") or []))
            return real(claims, **kw)

        discovery.resolve_claims = spy
        try:
            self.assertEqual(self._onboard()[0], 0)
            self.assertEqual(self._onboard()[0], 0)
        finally:
            discovery.resolve_claims = real
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1], "run 1 and run 2 searched different roots")
        for shape in ("**/sources", "**/.lake/official-pdf"):
            self.assertIn(str(self.root / shape), seen[0])

    def test_two_onboards_prepare_the_same_source_ids(self):
        self.assertEqual(self._onboard()[0], 0)
        first = self._discovered_sources()
        self.assertEqual(self._onboard()[0], 0)
        self.assertEqual(first, self._discovered_sources())


# ---------------------------------------------------------------------------
# skill-design#3 -- cf doctor
# ---------------------------------------------------------------------------

class TestDoctor(unittest.TestCase):
    def test_it_checks_every_dependency_the_code_gates_on(self):
        from cfcmd import doctor

        names = [row["name"] for row in doctor.check()]
        for expected in ("python", "git", "pdftotext", "pdftoppm", "curl", "node", "PyYAML"):
            self.assertIn(expected, names)
        required = {row["name"] for row in doctor.check() if row["required"]}
        self.assertEqual(required, {"python", "git", "pdftotext", "pdftoppm", "curl"})

    def test_a_missing_required_tool_is_one_actionable_line_and_exit_2(self):
        rc, out, err = self._run(missing={"pdftoppm"})
        self.assertEqual(rc, 2)
        self.assertIn("pdftoppm is missing", err)
        self.assertIn("poppler", err)
        self.assertIn("MISSING", out)

    def test_only_optional_things_missing_is_exit_0(self):
        rc, out, err = self._run(missing={"node"})
        self.assertEqual(rc, 0)
        self.assertIn("node is missing", out + err)

    def test_json_output_carries_the_same_verdict(self):
        rc, out, _ = self._run(missing={"curl"}, extra=["--json"])
        self.assertEqual(rc, 2)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("curl", [c["name"] for c in payload["checks"] if not c["ok"]])

    def test_it_works_with_no_workspace_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = self._run(missing=set(), workspace=str(Path(tmp) / "nope"))
            self.assertEqual(rc, 0)
            self.assertNotIn("pdf search roots", out)

    def test_it_prints_the_search_roots_when_there_is_a_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ws"
            self.assertEqual(_quiet(cf_main.main, ["init", str(root), "--no-git", "-q"]), 0)
            rc, out, _ = self._run(missing=set(), workspace=str(root))
            self.assertEqual(rc, 0)
            self.assertIn("pdf search roots", out)
            self.assertIn("~/Downloads", out)

    def _run(self, *, missing: set, extra=(), workspace=None):
        real_which = shutil.which

        def fake_which(name, *a, **kw):
            # Total, not partial: these cases assert about `cf doctor`, not about
            # what happens to be installed.  Falling through to the real
            # shutil.which made the three exit-0 assertions fail on any machine
            # without poppler -- which is every CI runner the suite runs on.
            return None if name in missing else "/usr/bin/" + name

        argv = ["doctor", *extra]
        if workspace:
            argv += ["--workspace", workspace]
        out, err = io.StringIO(), io.StringIO()
        shutil.which = fake_which
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = cf_main.cli(argv)
        finally:
            shutil.which = real_which
        return rc, out.getvalue(), err.getvalue()


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
