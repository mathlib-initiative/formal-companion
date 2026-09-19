"""Review-fix regressions for the detect stage (work package w2-detect).

Two findings:

* **architecture#2** -- ``cf pair`` and ``cf detect-proxies`` write the same
  ``pairs/<source_id>/def-coverage.<name>.json`` and owned the same three keys
  with two different signature classifiers, so the published verdict
  ``bridge-only`` vs ``stated`` depended on which command ran last.  There is
  now ONE classifier, and the order test below runs the two commands in both
  orders over one fixture and demands identical files.
* **generality#8** -- the detector needed a hand-written per-source stage table
  to run at all, and the shipped table mixed source-level facts (which section
  of the WORK develops a concept) with companion-level ones (one repo's module
  layout and identifier vocabulary).

Everything here is hermetic: a synthetic two-item source and a synthetic
companion in a temp directory, no baseline, no network, no Lean.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from cflib.errors import CfError  # noqa: E402
from detect import def_coverage as detect_cov  # noqa: E402
from detect.stages import StageTable  # noqa: E402
from pair import def_coverage as pair_cov  # noqa: E402

SOURCE_ID = "synthetic-booklet"
SOURCE_SHA = "a" * 64
COMPANION = "ours"

#: signatures whose classification used to depend on which writer saw them.
#: The first three are the ones the two regexes disagreed about: pair's allowed
#: at most one attribute group and did not list ``local ``.
SIGNATURE_BATTERY = [
    ("local instance foo : Bar := x", True),
    ("@[simp] @[inline] def volume : X := y", True),
    ("private local def z : X := y", True),
    ("def foo : X := y", True),
    ("@[simp] def foo : X := y", True),
    ("noncomputable def foo : X := y", True),
    ("structure S where", True),
    ("scoped instance : Foo := x", True),
    ("theorem t : True := trivial", False),
    ("lemma l : True := trivial", False),
    ("@[simp] theorem t : True := trivial", False),
]


# ==========================================================================
# architecture#2: one declaration classifier
# ==========================================================================
class OneDeclarationClassifierTest(unittest.TestCase):
    """The signature test the existing pin (TestDefCoverageIsOneImplementation) missed."""

    def test_both_entry_points_agree_on_the_signature_battery(self):
        pattern = pair_cov.def_regex()
        for statement, definitional in SIGNATURE_BATTERY:
            row = {"statement": statement}
            with self.subTest(statement=statement):
                self.assertEqual(pair_cov._is_definitional(row, pattern), definitional)
                self.assertEqual(detect_cov.is_definitional(row), definitional)

    def test_there_is_exactly_one_signature_regex(self):
        """Not "two patterns that happen to agree": the same compiled object."""
        self.assertIs(pair_cov.def_regex(), detect_cov.def_signature())
        self.assertIs(pair_cov.def_regex(), detect_cov.DEF_SIGNATURE)

    def test_a_custom_keyword_list_still_reaches_the_one_classifier(self):
        """`pair.def_coverage(keywords=...)` writes `keywords` into the file."""
        narrow = pair_cov.def_regex(("structure",))
        self.assertIsNot(narrow, detect_cov.DEF_SIGNATURE)
        self.assertTrue(detect_cov.is_definitional({"statement": "structure S where"}, narrow))
        self.assertFalse(detect_cov.is_definitional({"statement": "def f : X := y"}, narrow))
        # an adapter `kind` is not a per-caller decision, so it still wins
        self.assertTrue(detect_cov.is_definitional({"kind": "instance", "statement": "(truncated)"},
                                                   narrow))


# ==========================================================================
# the fixture workspace both commands write into
# ==========================================================================
def _items() -> list[dict]:
    """Three Chapter-1 Definitions; pages are synthetic but shaped like Stage 1's."""
    rows = []
    for n, (section, index, pdf_page) in enumerate(((1, 1, 21), (1, 2, 22), (2, 3, 31))):
        rows.append({
            "item": f"Definition 1.{section}.{index}",
            "kind": "Definition",
            "number": f"1.{section}.{index}",
            "chapter": 1,
            "section": section,
            "index": index,
            "pdf_page": pdf_page,
            "page": pdf_page - 16,
            "occurrences": [{"pdf_page": pdf_page, "page": pdf_page - 16, "line": 10 * n + 1}],
        })
    return rows


#: One row per Definition.  The first two carry the signatures the two writers
#: used to disagree about, and NO adapter ``kind`` -- the documented v1 fallback
#: path, and the only path on which the divergence was reachable.
_STATEMENTS = [
    {"item": "Definition 1.1.1", "decl": "Booklet.foo", "repo": COMPANION,
     "statement": "local instance foo : Bar := x", "file": "Booklet/A.lean", "line": 3,
     "sorry": False, "source_id": SOURCE_ID, "source_sha": SOURCE_SHA},
    {"item": "Definition 1.1.2", "decl": "Booklet.volume", "repo": COMPANION,
     "statement": "@[simp] @[inline] def volume : X := y", "file": "Booklet/A.lean", "line": 9,
     "sorry": False, "source_id": SOURCE_ID, "source_sha": SOURCE_SHA},
    {"item": "Definition 1.2.3", "decl": "Booklet.about_bar", "repo": COMPANION,
     "statement": "theorem about_bar : Bar = Bar := rfl", "file": "Booklet/B.lean", "line": 4,
     "sorry": False, "source_id": SOURCE_ID, "source_sha": SOURCE_SHA},
]

#: A source-level stage table for the synthetic booklet: which SECTION of the
#: work develops a concept, plus the library objects a body may hide behind.
_STAGE_TABLE = {
    "source_id": SOURCE_ID,
    "chapter": 1,
    "default_stage": 0,
    "stage_beyond_book": 8,
    "module_stages": [["Lib.Late", 4], ["Lib.Measure", 2]],
    "mathlib_objects": {"Lib.Measure.size": {"label": "size", "module": "Lib.Measure",
                                             "stage": 2}},
}


def _make_workspace(root: Path, *, stage_table: bool = True) -> None:
    """A one-source, one-companion workspace on disk."""
    (root / "companions" / COMPANION).mkdir(parents=True)
    (root / "repos" / COMPANION).mkdir(parents=True)
    (root / "sources" / SOURCE_ID).mkdir(parents=True)
    (root / "companions" / COMPANION / "statements.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in _STATEMENTS), encoding="utf-8")
    (root / "companions" / COMPANION / "state.json").write_text(
        json.dumps({"name": COMPANION}), encoding="utf-8")
    (root / "sources" / SOURCE_ID / "items.json").write_text(json.dumps({
        "source_id": SOURCE_ID, "source_sha": SOURCE_SHA, "offset": 16,
        "kinds": ["Definition", "Lemma", "Theorem", "Exercise", "Remark"],
        "count": len(_items()), "items": _items(),
    }), encoding="utf-8")
    # the mechanical gate fails closed without the pinned text
    (root / "sources" / SOURCE_ID / "text.txt").write_text(
        "\f".join(f"page {n}\n" for n in range(1, 40)), encoding="utf-8")
    (root / "workspace.yaml").write_text(
        "version: 2\n"
        "workspace:\n  name: synthetic\n"
        "companions:\n"
        f"  {COMPANION}:\n    repo: {root / 'repos' / COMPANION}\n"
        "    adapter: docstring_tags\n"
        f"    sources: [{SOURCE_ID}]\n"
        f"    anchor_source: {SOURCE_ID}\n"
        "sources:\n"
        f"  {SOURCE_ID}:\n"
        f'    sha256: "{SOURCE_SHA}"\n'
        "    title: A Synthetic Booklet\n"
        '    edition: "first"\n'
        "    offset: 16\n    chapters: [1]\n    role: audited\n"
        "    kinds: [Definition, Lemma, Theorem, Exercise, Remark]\n",
        encoding="utf-8")
    if stage_table:
        table = root / "detect" / "mathlib_stages"
        table.mkdir(parents=True)
        (table / f"{SOURCE_ID}.json").write_text(json.dumps(_STAGE_TABLE), encoding="utf-8")


def _run_command(module, root: Path, *argv: str) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace")
    sub = ap.add_subparsers()
    module.register(sub)
    args = ap.parse_args(["--workspace", str(root), *argv])
    return args.func(args)


class _WorkspaceCase(unittest.TestCase):
    """A fresh fixture workspace per test, with the command's logging muted."""

    STAGE_TABLE = True

    def setUp(self):
        from cflib import log

        log.set_level(0)
        self.addCleanup(log.set_level, 1)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # resolved: the commands record absolute paths, and on macOS /var is a
        # symlink to /private/var, so an unresolved root compares unequal
        self.root = Path(tmp.name).resolve()
        _make_workspace(self.root, stage_table=self.STAGE_TABLE)

    def detect(self, *extra) -> int:
        from cfcmd import detect_proxies

        return _run_command(detect_proxies, self.root, "detect-proxies", COMPANION,
                            "--source", SOURCE_ID, *extra)

    def pair(self, *extra) -> int:
        from cfcmd import pair as pair_cmd

        return _run_command(pair_cmd, self.root, "pair", "--source", SOURCE_ID, *extra)

    def coverage(self) -> dict:
        path = self.root / "pairs" / SOURCE_ID / f"def-coverage.{COMPANION}.json"
        return json.loads(path.read_text(encoding="utf-8"))


class DefCoverageIsOrderIndependentTest(_WorkspaceCase):
    """The published verdict must not depend on which command ran last."""

    #: written by whichever command wrote most recently, and a timestamp: these
    #: two keys are allowed to differ between the orders, nothing else is.
    VOLATILE = ("written_by", "created")

    def _both_orders(self) -> tuple[dict, dict]:
        """The same workspace, written twice, with nothing carried between."""
        self.assertEqual(self.pair("--no-anchors", "--no-emit-corpus"), 0)
        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        pair_then_detect = self.coverage()

        # one root, so the paths the commands record are identical; the shared
        # artifact is removed so the second order starts from nothing
        shutil.rmtree(self.root / "pairs")
        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        self.assertEqual(self.pair("--no-anchors", "--no-emit-corpus"), 0)
        return pair_then_detect, self.coverage()

    def test_the_two_orders_write_the_same_file(self):
        first, second = self._both_orders()
        self.assertEqual({k: v for k, v in first.items() if k not in self.VOLATILE},
                         {k: v for k, v in second.items() if k not in self.VOLATILE})
        self.assertEqual(first["written_by"], "cf detect-proxies")
        self.assertEqual(second["written_by"], "cf pair")

    def test_the_verdict_is_the_one_the_baseline_classification_gives(self):
        """`local instance` and a two-attribute `def` STATE their notion.

        The pinned def-coverage figures (32 stated / 4 bridge-only / 0 missing
        over the real corpus) are unmoved by this: every baseline row carries no
        adapter ``kind``, so every one of them goes through the signature test,
        and the two patterns classify all of them the same way.  They differ
        only on the three shapes in ``SIGNATURE_BATTERY``.
        """
        first, second = self._both_orders()
        for cov in (first, second):
            self.assertEqual(cov["stated"], ["Definition 1.1.1", "Definition 1.1.2"])
            self.assertEqual(cov["bridge_only"], ["Definition 1.2.3"])
            self.assertEqual(cov["missing"], [])


# ==========================================================================
# generality#8: the stage table
# ==========================================================================
class StageTableSplitTest(unittest.TestCase):
    """Source-level facts ship; companion-level facts come from the workspace."""

    def test_the_shipped_table_carries_no_companion_facts(self):
        table = StageTable.for_source("tao-measure-gsm126-published")
        self.assertTrue(table.module_stages)
        self.assertTrue(table.objects)
        self.assertEqual(table.repo_module_stages, [])
        self.assertEqual(table.native_tokens, [])

    def test_an_overlay_supplies_the_companion_facts(self):
        table = StageTable.for_source("tao-measure-gsm126-published")
        overlaid = table.with_companion_overlay({
            "repo_module_stages": [["Booklet.Early", 1]],
            "native_tokens": ["boxCoverCost"],
        })
        self.assertEqual(overlaid.stage_of_repo_module("Booklet.Early.Sizes"), 1)
        self.assertEqual(overlaid.native_tokens, ["boxCoverCost"])
        # the source-level half is untouched, and the shipped table is not mutated
        self.assertEqual(overlaid.module_stages, table.module_stages)
        self.assertEqual(table.repo_module_stages, [])

    def test_an_overlay_that_claims_source_level_keys_is_refused(self):
        table = StageTable.for_source("tao-measure-gsm126-published")
        with self.assertRaises(CfError) as ctx:
            table.with_companion_overlay({"module_stages": [["Mathlib.Order", 0]]})
        self.assertIn("module_stages", str(ctx.exception))

    def test_the_chapter_scope_comes_from_the_workspace(self):
        table = StageTable.for_source("tao-measure-gsm126-published")
        self.assertEqual(table.stage_of_item("Definition 1.2.2"), 2)
        self.assertIsNone(table.stage_of_item("Definition 2.1.4"))
        wider = table.scoped_to_chapters([1, 2])
        self.assertEqual(wider.stage_of_item("Definition 1.2.2"), 2)
        self.assertEqual(wider.stage_of_item("Definition 2.1.4"), 1)
        # an empty scope means "do not narrow", not "nothing is in scope"
        self.assertEqual(table.scoped_to_chapters([]).stage_of_item("Definition 2.1.4"), 1)

    def test_an_absent_table_is_a_value_not_an_exception(self):
        absent = StageTable.for_source("no-such-source", required=False)
        self.assertFalse(absent.available)
        self.assertIn("no stage table", absent.unavailable_reason)
        self.assertEqual(absent.objects_by_label(), {})
        self.assertIsNone(absent.path)
        with self.assertRaises(CfError):
            StageTable.for_source("no-such-source")


class DetectWithoutAStageTableTest(_WorkspaceCase):
    """`cf detect-proxies` must still answer the coverage question."""

    STAGE_TABLE = False

    def test_coverage_runs_and_the_body_scan_says_it_was_skipped(self):
        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        cov = self.coverage()
        self.assertEqual(cov["stated"], ["Definition 1.1.1", "Definition 1.1.2"])
        self.assertEqual(cov["bridge_only"], ["Definition 1.2.3"])
        scan = cov["_meta"]["body_proxy_scan"]
        self.assertFalse(scan["ran"])
        self.assertIn("no stage table", scan["reason"])
        self.assertIn("--init-stage-table", scan["reason"])
        self.assertEqual(cov["proxy_candidates"], [])

    def test_the_findings_are_still_written(self):
        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        rows = [json.loads(line) for line in
                (self.root / "detect" / COMPANION / "findings.cheap.jsonl")
                .read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual([r["item"] for r in rows[1:]], ["Definition 1.2.3"])

    def test_the_lean_tier_refuses_and_names_the_flag(self):
        deps = self.root / "detect" / COMPANION / "deps.json"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.write_text(json.dumps({"repo_constants": [], "referenced": []}), encoding="utf-8")
        with self.assertRaises(CfError) as ctx:
            self.detect("--lean", "--no-emit-corpus", "--no-tally")
        hint = str(getattr(ctx.exception, "hint", ""))
        self.assertIn("--init-stage-table", hint)
        self.assertIn("--stages", hint)

    def test_init_stage_table_writes_one_and_refuses_to_clobber_it(self):
        self.assertEqual(self.detect("--init-stage-table"), 0)
        written = self.root / "detect" / "mathlib_stages" / f"{SOURCE_ID}.json"
        self.assertTrue(written.exists())
        table = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(table["source_id"], SOURCE_ID)
        self.assertEqual(table["chapters"], [1])
        self.assertNotIn("repo_module_stages", table)   # companion-level: not here
        self.assertNotIn("native_tokens", table)
        # it is a candidate a human edits, and it says so
        self.assertTrue(any(k.startswith("_") for k in table))
        with self.assertRaises(CfError) as ctx:
            self.detect("--init-stage-table")
        self.assertIn(str(written), str(ctx.exception) + str(getattr(ctx.exception, "hint", "")))

    def test_the_written_table_then_loads(self):
        self.assertEqual(self.detect("--init-stage-table"), 0)
        table = StageTable.for_source(
            SOURCE_ID, extra_dirs=[self.root / "detect" / "mathlib_stages"])
        self.assertEqual(table.source_id, SOURCE_ID)
        self.assertTrue(table.available)


class CompanionOverlayIsReadFromTheWorkspaceTest(_WorkspaceCase):
    """`companions/<name>/stage-overlay.json`, beside conventions.md."""

    def test_the_overlay_is_applied_to_the_body_scan(self):
        (self.root / "companions" / COMPANION / "stage-overlay.json").write_text(
            json.dumps({"companion": COMPANION, "source_id": SOURCE_ID,
                        "repo_module_stages": [["Booklet.A", 1]],
                        "native_tokens": ["boxCoverCost"]}), encoding="utf-8")
        statements = self.root / "companions" / COMPANION / "statements.jsonl"
        rows = [json.loads(line) for line in
                statements.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows[0]["body"] = "def foo : X := Lib.Measure.size E"
        rows[0]["item"] = "Definition 1.2.3"
        statements.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        cov = self.coverage()
        self.assertTrue(cov["_meta"]["body_proxy_scan"]["ran"])
        self.assertEqual(cov["_meta"]["companion_overlay"],
                         str(self.root / "companions" / COMPANION / "stage-overlay.json"))
        self.assertEqual([c["decl"] for c in cov["proxy_candidates"]], ["Booklet.foo"])

    def test_without_an_overlay_the_scan_still_runs(self):
        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        cov = self.coverage()
        self.assertTrue(cov["_meta"]["body_proxy_scan"]["ran"])
        self.assertIsNone(cov["_meta"]["companion_overlay"])


# ==========================================================================
# the gate source block (hand-off from wave 1)
# ==========================================================================
class GateSourceBlockCarriesPinnedTextPagesTest(_WorkspaceCase):
    """`item_pages` is the PDF page of the pinned text, not the printed page."""

    def test_the_detector_passes_pdf_pages_to_the_gate(self):
        from cfcmd import detect_proxies

        pages = detect_proxies._item_pdf_pages(
            self.root / "sources" / SOURCE_ID / "items.json",
            ["Definition", "Lemma", "Theorem", "Exercise", "Remark"])
        self.assertEqual(pages, {"Definition 1.1.1": 21, "Definition 1.1.2": 22,
                                 "Definition 1.2.3": 31})
        printed = detect_proxies._items_pages(
            self.root / "sources" / SOURCE_ID / "items.json",
            ["Definition", "Lemma", "Theorem", "Exercise", "Remark"])
        self.assertEqual(printed, {"Definition 1.1.1": 5, "Definition 1.1.2": 6,
                                   "Definition 1.2.3": 15})

    def test_the_gate_config_the_detector_builds_carries_them(self):
        from cfcmd import detect_proxies
        from plan import presets as presets_mod

        seen: dict = {}
        original = presets_mod.gate_config

        def spy(preset, **kw):
            seen.update(kw)
            return original(preset, **kw)

        presets_mod.gate_config = spy
        self.addCleanup(setattr, presets_mod, "gate_config", original)
        self.assertEqual(self.detect("--run-id", "d-gate", "--no-tally"), 0)
        block = seen["sources"][SOURCE_ID]
        self.assertEqual(block["item_pages"]["Definition 1.2.3"], 31)
        self.assertEqual(block["page_offset"], 16)
        # and it survives source_block()'s key filter
        self.assertEqual(presets_mod.source_block(block)["item_pages"],
                         block["item_pages"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
