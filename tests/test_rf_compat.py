"""Review fixes for the compat shims: one implementation, not two.

One class per finding, named after what used to be wrong.

* six ``compat`` shims carried 1,607 lines, 453 of them a second
  implementation behind ``try: from cflib ... except Exception``.  Those guards
  are bare, and the modules are only importable with ``scripts/`` on
  ``sys.path`` -- where ``cflib`` always resolves -- so the fallback could only
  fire when cflib itself RAISED at import time, and it then answered an honest
  bug with a different algorithm: a second item grammar that kept only the
  first sub-part of ``Theorem 1.1.1 (i)(a)`` and truncated ``Theorem 2.3.4.1``
  to three levels, and a second ``WorkspacePaths`` missing 35 of the real one's
  64 accessors (architecture#4);
* no test could reach any of it (``grep -rn '_cf_yamlio|_FallbackPaths' tests/``
  was empty), and inside the unreachable half ``sources/compat.py`` had a bare
  ``import yaml`` where its sibling guarded the same import (tests#8);
* command modules reached into each other's underscore names, so a refactor of
  ``cf onboard`` silently changed ``cf sources refresh`` (architecture#8).

The guards here are structural on purpose: they assert the shapes that let the
defect exist (a swallowed cflib import, a second grammar, a sibling's private
name) rather than one symptom each, because the symptom moves and the shape does
not.  Every name a caller uses is asserted to still answer, so "delete the
fallback" cannot quietly become "delete the API".
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
if str(SKILL / "scripts") not in sys.path:
    sys.path.insert(0, str(SKILL / "scripts"))

from cflib import items as cf_items  # noqa: E402
from cflib import vocab  # noqa: E402
from cflib.hashing import finding_id as cflib_finding_id  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from ledger import compat as ledger_compat  # noqa: E402
from sources import compat as sources_compat  # noqa: E402

SCRIPTS = SKILL / "scripts"

#: every cflib adapter in the tree, by import path
SHIMS = {
    "sources.compat": SCRIPTS / "sources" / "compat.py",
    "ledger.compat": SCRIPTS / "ledger" / "compat.py",
    "pair.compat": SCRIPTS / "pair" / "compat.py",
    "detect._compat": SCRIPTS / "detect" / "_compat.py",
    "harvest._compat": SCRIPTS / "harvest" / "_compat.py",
    "run._compat": SCRIPTS / "run" / "_compat.py",
}

#: the two modules that really do run where cflib is absent: `cf run` transfers
#: them to the host (scripts/run/manifest.py, plan/make_tend_root.py), so they
#: inline what they need and are exempt from the no-second-implementation rule.
SHIPPED_TO_A_HOST = ("verify_findings.py", "check_changed_files.py")


def _cflib_imports_under_try(path: Path) -> list[int]:
    """Line numbers of ``import cflib...`` statements wrapped in a ``try``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.ImportFrom) and (inner.module or "").startswith("cflib"):
                bad.append(inner.lineno)
            elif isinstance(inner, ast.Import):
                bad += [inner.lineno for a in inner.names if a.name.startswith("cflib")]
    return sorted(bad)


class TestNoShimHidesABrokenCflib(unittest.TestCase):
    """architecture#4: the fallback halves are gone, so an ImportError arrives."""

    def test_no_shim_imports_cflib_under_a_try(self):
        for name, path in sorted(SHIMS.items()):
            with self.subTest(shim=name):
                self.assertEqual(
                    _cflib_imports_under_try(path), [],
                    f"{name} guards its cflib import; a genuine error inside cflib would be "
                    "swallowed and a second implementation would take over")

    def test_an_import_time_error_inside_cflib_reaches_the_caller(self):
        """The hazard, end to end: a raising cflib module must not degrade.

        Run in a subprocess because the assertion is about what ``import`` does,
        and a meta-path finder installed in-process would outlive the test.
        """
        program = textwrap.dedent(
            """
            import importlib.abc, importlib.machinery, sys, types
            sys.path.insert(0, %r)

            class Boom(importlib.abc.MetaPathFinder, importlib.abc.Loader):
                def find_spec(self, name, path=None, target=None):
                    if name == "cflib.items":
                        return importlib.machinery.ModuleSpec(name, self)
                    return None
                def create_module(self, spec):
                    return types.ModuleType(spec.name)
                def exec_module(self, module):
                    raise ValueError("an honest bug inside cflib.items")

            sys.meta_path.insert(0, Boom())
            try:
                import %s          # noqa: F401
            except ValueError as exc:
                print("raised:%%s" %% exc)
            else:
                print("swallowed")
            """
        )
        for name in ("sources.compat", "ledger.compat"):
            with self.subTest(shim=name):
                out = subprocess.run([sys.executable, "-c", program % (str(SCRIPTS), name)],
                                     capture_output=True, text=True, timeout=120)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertIn("raised:an honest bug inside cflib.items", out.stdout,
                              f"{name} swallowed a real error inside cflib")

    def test_only_the_modules_that_ship_to_a_host_carry_a_second_copy(self):
        """The one exemption, stated as a fact about the shipped file set.

        ``scripts/sources/`` was believed to need a bare-host fallback too; the
        run manifest ships the bridges plus these two gate files and nothing
        else, so the belief was wrong and the fallback was unreachable.
        """
        from plan import make_tend_root
        from run import manifest

        staged = Path(make_tend_root.__file__).read_text(encoding="utf-8")
        for name in SHIPPED_TO_A_HOST:
            self.assertIn(name, staged)
        for shipped in manifest.BRIDGE_FILES:
            if not shipped.endswith(".py"):
                continue
            source = (SCRIPTS / "run" / shipped).read_text(encoding="utf-8")
            self.assertNotIn("compat", source,
                             f"{shipped} travels to a host and must not need a shim")


class TestTheShimsHoldNoSecondGrammar(unittest.TestCase):
    """architecture#4: the divergence that made the dead code dangerous."""

    #: label -> (id, sub) cflib produces.  The deleted fallback answered
    #: sub='(i)' with title='a' for the first three and invented
    #: 'Theorem 2.3.4' for the fourth, which the grammar refuses outright.
    MULTI_SUB = [
        ("Theorem 1.1.1 (i)(a)", "Theorem 1.1.1", "(i)(a)"),
        ("Exercise 1.1.6(i)(ii)", "Exercise 1.1.6", "(i)(ii)"),
        ("Lemma 1.2.5 (a)(b)", "Lemma 1.2.5", "(a)(b)"),
    ]

    def test_multi_sub_labels_keep_every_sub_part(self):
        for raw, key, sub in self.MULTI_SUB:
            with self.subTest(label=raw):
                parsed = sources_compat.parse_item(raw)
                self.assertIsNotNone(parsed, f"{raw} must parse")
                self.assertEqual((parsed.key, parsed.sub, parsed.title), (key, sub, ""))

    def test_a_fourth_numbering_level_is_refused_not_truncated(self):
        """``Theorem 2.3.4.1`` is not ``Theorem 2.3.4``: silence here re-keys a book."""
        parsed, reason = sources_compat.parse_item_reject("Theorem 2.3.4.1")
        self.assertIsNone(parsed)
        self.assertTrue(reason)

    def test_the_shim_defines_no_parser_of_its_own(self):
        tree = ast.parse(SHIMS["sources.compat"].read_text(encoding="utf-8"))
        defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertNotIn("_fallback_parse_item", defined)
        self.assertNotIn("_clean_item_fallback", defined)

    def test_parse_agrees_with_cflib_over_the_whole_sample(self):
        labels = [raw for raw, _k, _s in self.MULTI_SUB] + [
            "Definition 1.2.2 (Lebesgue measurability)", "Exercise    1.3.19",
            "In-text p.42", "Theorem 7", "Corollary 1.3.14.", "`Lemma 1.2.5`",
        ]
        for raw in labels:
            with self.subTest(label=raw):
                obj, _reject = cf_items.try_parse(raw, tuple(sources_compat.DEFAULT_KINDS))
                parsed = sources_compat.parse_item(raw)
                if obj is None:
                    self.assertIsNone(parsed)
                else:
                    self.assertEqual((parsed.key, parsed.sub), (obj.id, obj.sub))


class TestPathsAreCflibsPaths(unittest.TestCase):
    """architecture#4: the fallback WorkspacePaths was missing 35 accessors."""

    #: accessors ``_PlainPaths`` never had, each of which a stage writes through
    MISSING_FROM_THE_FALLBACK = ("pairs_jsonl", "queue_json", "gate_json", "tend_root",
                                 "receipt_json", "tally_json", "union_json", "lint_json",
                                 "adapter_yaml", "source_meta", "list_sources",
                                 "ensure_skeleton")
    #: accessors wave 3 added for `cf pin` / `cf recheck`, which must keep working
    WAVE_THREE = ("pin_json", "recheck_json")

    def test_ledger_paths_forward_to_the_real_workspace_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = ledger_compat.workspace_paths(tmp)
            for name in self.MISSING_FROM_THE_FALLBACK + self.WAVE_THREE:
                with self.subTest(accessor=name):
                    self.assertTrue(hasattr(paths, name), f"LedgerPaths lost {name}")
            self.assertEqual(Path(paths.pin_json("r1")).name, "pin.r1.json")
            self.assertEqual(Path(paths.recheck_json("ours")).name, "recheck.ours.json")

    def test_sources_paths_are_cflibs_own_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsInstance(sources_compat.workspace_paths(tmp), WorkspacePaths)

    def test_no_shim_defines_a_second_paths_class(self):
        for name in ("sources.compat", "ledger.compat"):
            tree = ast.parse(SHIMS[name].read_text(encoding="utf-8"))
            defined = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
            with self.subTest(shim=name):
                self.assertFalse(defined & {"_FallbackPaths", "_PlainPaths", "_FallbackWorkspace"},
                                 f"{name} still mirrors cflib's paths")


class TestNoShimNeedsPyYAML(unittest.TestCase):
    """tests#8: the bare ``import yaml`` lived in the unreachable half."""

    def test_no_shim_imports_yaml(self):
        for name, path in sorted(SHIMS.items()):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            with self.subTest(shim=name):
                self.assertNotIn("yaml", imported,
                                 f"{name} imports PyYAML directly; cflib.yamlio is the reader, "
                                 "and it falls back to its own subset parser")

    def test_load_yaml_reads_through_cflib(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workspace.yaml"
            path.write_text("name: demo\nsources:\n  s1:\n    offset: 16\n", encoding="utf-8")
            for shim in (sources_compat, ledger_compat):
                with self.subTest(shim=shim.__name__):
                    data = shim.load_yaml(str(path))
                    self.assertEqual(data["name"], "demo")
                    self.assertEqual(data["sources"]["s1"]["offset"], 16)


class TestEveryPublicNameStillAnswers(unittest.TestCase):
    """The deletion must not take the API with it: these all have callers."""

    SOURCES_API = ("DEFAULT_KINDS", "DEFINITIONAL_KEYWORDS", "DEFINITIONAL_KINDS", "PAGE_ANCHOR",
                   "ItemId", "finding_id", "item_sort_key", "load_workspace", "load_yaml",
                   "normalise_item", "parse_item", "parse_item_reject", "parse_pages",
                   "read_json", "read_jsonl", "sha256_file", "sha256_text", "state_merge",
                   "utcnow", "workspace_paths", "write_json", "write_jsonl", "write_text")
    LEDGER_API = ("CLOSED_VERDICTS", "FINDING_KINDS", "LEDGER_VERDICTS", "MECHANICAL_KINDS",
                  "OPEN_VERDICTS", "RECHECK_STATUSES", "SEVERITIES", "STATUS_CHALLENGED",
                  "STATUS_UNCHALLENGED", "VERDICTS_REQUIRING_ARGUMENT", "VIEWER_VERDICT",
                  "data_rows", "decl_repo", "finding_id", "is_meta_row", "item_join_key",
                  "iter_json_files", "load_workspace", "load_yaml", "meta_row",
                  "normalise_decl", "normalise_item", "read_json", "read_jsonl", "read_state",
                  "resolution_ok", "sha256_text", "state_section", "update_state",
                  "workspace_paths", "write_json", "write_jsonl", "write_text")

    def test_sources_compat_exports_what_its_callers_import(self):
        for name in self.SOURCES_API:
            with self.subTest(name=name):
                self.assertTrue(hasattr(sources_compat, name))

    def test_ledger_compat_exports_what_its_callers_import(self):
        for name in self.LEDGER_API:
            with self.subTest(name=name):
                self.assertTrue(hasattr(ledger_compat, name))

    def test_pair_compat_still_re_exports_the_source_adapter(self):
        from pair import compat as pair_compat

        for name in ("DEFAULT_KINDS", "PAGE_ANCHOR", "finding_id", "parse_item", "parse_pages",
                     "item_sort_key", "workspace_paths", "write_json", "utcnow"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(pair_compat, name))

    def test_the_vocabulary_values_are_cflibs(self):
        self.assertEqual(ledger_compat.FINDING_KINDS, frozenset(vocab.FINDING_KINDS))
        self.assertEqual(ledger_compat.SEVERITIES, frozenset(vocab.SEVERITY_SET))
        self.assertEqual(ledger_compat.RECHECK_STATUSES, tuple(sorted(vocab.RECHECK_STATUS_SET)))
        self.assertEqual(ledger_compat.STATUS_UNCHALLENGED, vocab.UNCHALLENGED)
        self.assertEqual(ledger_compat.VIEWER_VERDICT, dict(vocab.LEDGER_TO_VIEWER))
        self.assertEqual(sources_compat.PAGE_ANCHOR, vocab.PAGE_ANCHOR)
        self.assertEqual(tuple(sources_compat.DEFAULT_KINDS), tuple(cf_items.DEFAULT_KINDS))

    def test_resolution_ok_still_judges_both_ways(self):
        self.assertTrue(ledger_compat.resolution_ok(None))
        self.assertTrue(ledger_compat.resolution_ok("fixed:PR#12"))
        self.assertFalse(ledger_compat.resolution_ok("probably fixed"))

    def test_the_writers_keep_their_shapes(self):
        """indent=1 and the ``_meta`` header are what the corpus files look like."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "a.json"
            sources_compat.write_json(out, {"b": 1, "a": 2})
            self.assertEqual(out.read_text(encoding="utf-8"), '{\n "b": 1,\n "a": 2\n}\n')

            rows = Path(tmp) / "rows.jsonl"
            self.assertEqual(sources_compat.write_jsonl(rows, [{"x": 1}], meta={"run": "r"}), 1)
            self.assertEqual(sources_compat.read_jsonl(rows), [{"x": 1}])
            self.assertEqual(len(sources_compat.read_jsonl(rows, skip_meta=False)), 2)
            self.assertEqual(sources_compat.read_jsonl(Path(tmp) / "absent.jsonl"), [])
            self.assertIsNone(sources_compat.read_json(Path(tmp) / "absent.json"))

            led = Path(tmp) / "led.json"
            ledger_compat.write_json(str(led), {"b": 1, "a": 2})
            self.assertEqual(led.read_text(encoding="utf-8"), '{\n "a": 2,\n "b": 1\n}\n')

    def test_finding_id_still_matches_the_one_definition(self):
        """The frozen contract: these shims are call sites, never a second hash."""
        cases = [
            ("r1", "s1", "Definition 1.2.2", "PROXY", ["ours:Foo", "ours:Bar"]),
            ("r", "s2", "`Lemma 1.2.5`", "VACUITY", ["adam:X", "adam:X", " adam:Y "]),
            ("", "", "", "", []),
        ]
        for args in cases:
            with self.subTest(item=args[2]):
                want = cflib_finding_id(*args)
                self.assertEqual(sources_compat.finding_id(*args), want)
                self.assertEqual(ledger_compat.finding_id(*args), want)


class TestTheShimsHaveADeadline(unittest.TestCase):
    """tests#8: a number that has to come down, so the shims cannot re-grow.

    Two comments (scripts/pair/compat.py, scripts/ledger/compat.py) say to delete
    these files once the call sites use cflib directly.  Nothing measured whether
    that was happening, so they grew instead.  Lower the ceilings when you move a
    caller across; never raise them.
    """

    #: total NON-BLANK lines across every shim; 1,292 before the fallback halves
    #: went (1,607 physical lines).  This is today's measured total with no
    #: margin at all: a ceiling with slack in it is not a ratchet, because the
    #: deleted second implementation could be pasted back under it and the suite
    #: would stay green.  Adding a line to a shim is meant to fail here.
    MAX_SHIM_LINES = 896
    #: modules that still import a shim rather than cflib directly.  Also today's
    #: measured count with no margin.
    MAX_CALLERS = 40

    def _callers(self) -> set[str]:
        out: set[str] = set()
        for path in sorted(SCRIPTS.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "compat" not in text:
                continue
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    names = {a.name for a in node.names}
                    if module.endswith("compat") or "compat" in names or "_compat" in names:
                        out.add(str(path.relative_to(SKILL)))
        return out - {str(p.relative_to(SKILL)) for p in SHIMS.values()}

    def test_the_shims_fit_under_the_ceiling(self):
        total = sum(len([ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()])
                    for p in SHIMS.values())
        self.assertLessEqual(
            total, self.MAX_SHIM_LINES,
            f"the compat shims are {total} non-blank lines, over the "
            f"{self.MAX_SHIM_LINES}-line ceiling: move the caller onto cflib instead, "
            "and lower MAX_SHIM_LINES when one moves")

    def test_the_caller_count_does_not_grow(self):
        callers = self._callers()
        self.assertLessEqual(
            len(callers), self.MAX_CALLERS,
            f"a new module imports a compat shim instead of cflib: {sorted(callers)}")


class TestCommandsDoNotReachIntoEachOther(unittest.TestCase):
    """architecture#8: cfcmd is argument parsing over the stage packages."""

    #: the one that is left, and why.  ``_prepare_source`` is 113 lines whose
    #: closure is another 107 of REFUSAL prose (``_refuse_an_empty_source``,
    #: ``_teach_the_overlay``, ``_errata_provenance``) naming the `cf onboard`
    #: flags that settle each stop -- CLI-layer text, not stage logic.  Moving
    #: it is a 220-line change with no user-visible payoff; it goes when
    #: cfcmd/onboard.py is next opened for its own reasons.
    ALLOWED = {("cfcmd/sources.py", "cfcmd.onboard", "_prepare_source")}

    def _private_cross_imports(self) -> set[tuple[str, str, str]]:
        found: set[tuple[str, str, str]] = set()
        for path in sorted((SCRIPTS / "cfcmd").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                module = node.module or ""
                if not module.startswith("cfcmd."):
                    continue
                if module == f"cfcmd.{path.stem}":
                    continue
                for alias in node.names:
                    if alias.name.startswith("_"):
                        found.add((f"cfcmd/{path.name}", module, alias.name))
        return found

    def test_no_command_imports_a_siblings_private_name(self):
        self.assertEqual(
            self._private_cross_imports(), self.ALLOWED,
            "a command module reaches into another's private name: promote the helper into the "
            "stage package that owns it (scripts/sources/, scripts/harvest/) and import it there")

    def test_the_promoted_helpers_live_in_their_stage_packages(self):
        from harvest import record as record_mod
        from sources.locator import TEXT_SUFFIXES, text_suffixes_help

        self.assertEqual(text_suffixes_help(), " ".join(TEXT_SUFFIXES))
        for name in ("write_run_tally", "record_run_state", "run_companions"):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(record_mod, name)))

    def test_the_help_still_advertises_every_accepted_suffix(self):
        """The reason the helper is shared: two commands print the same set."""
        from sources.locator import TEXT_SUFFIXES

        out = subprocess.run([sys.executable, str(SCRIPTS / "cf.py"), "sources", "add", "--help"],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        flat = " ".join(out.stdout.split())
        for suffix in TEXT_SUFFIXES:
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, flat)

    def test_run_companions_reads_every_spelling_a_plan_writes(self):
        from harvest import record as record_mod

        self.assertEqual(record_mod.run_companions({"companion": "ours"}), ["ours"])
        self.assertEqual(record_mod.run_companions({"companions": ["a", "b"]}), ["a", "b"])
        self.assertEqual(record_mod.run_companions({"repos": ["r"]}), ["r"])
        self.assertEqual(record_mod.run_companions({}), [])


class TestTheTallyStillLandsWhereItDid(unittest.TestCase):
    """The promotion must be a move, not a change: same file, same content."""

    def test_write_run_tally_writes_reports_tally_run_json(self):
        from cflib.workspace import Workspace
        from harvest import record as record_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace.yaml").write_text(
                "version: 2\n"
                "workspace:\n  name: ws\n"
                "companions:\n"
                "  ours:\n"
                "    adapter: docstring_tags\n"
                "    sources: [s1]\n"
                "    anchor_source: s1\n"
                "sources:\n"
                "  s1:\n"
                f'    sha256: "{"a" * 64}"\n'
                "    title: A Little Book\n"
                '    edition: "first"\n'
                "    offset: 16\n"
                "    role: audited\n",
                encoding="utf-8")
            corpus = root / "corpus" / "r1"
            corpus.mkdir(parents=True)
            (corpus / "f.json").write_text(json.dumps({
                "run_id": "r1", "companion": "ours", "source_id": "s1",
                "source_sha": "a" * 64, "item": "Exercise 1.3.19", "kind": "CONTENT",
                "severity": "medium", "unfaithful_decls": ["ours:Foo"],
            }), encoding="utf-8")

            workspace = Workspace.load(str(root))
            result = record_mod.write_run_tally(workspace, "r1", corpus)

            out = Path(workspace.paths.tally_json("r1"))
            self.assertTrue(out.exists(), "reports/tally.r1.json was not written")
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), result)
            self.assertEqual(out.read_text(encoding="utf-8")[:2], "{\n",
                             "the tally is still written with indent=1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
