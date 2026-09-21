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

The independent review of that work is pinned here too: a bootstrapped table is
inert until a human edits it (it used to carry a live placeholder object and
invent findings against it), an unscoped run audits every chapter instead of
fabricating Chapter 1 and records the scope it used, a scan with no companion
vocabulary says its evidence will be empty, the one classifier answers a legacy
row with a number in it, and the shipped templates name no real construction.

Everything here is hermetic: a synthetic source and a synthetic companion in a
temp directory, no baseline, no network, no Lean.
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

    #: Rows only a hand-built or legacy statements table produces -- the same
    #: path the signature fallback is reachable on at all.  ``cf pair`` used to
    #: coerce both fields and the surviving classifier did not, so unifying the
    #: two turned a verdict into an AttributeError / TypeError from inside it.
    NON_STRING_ROWS = ({"kind": 3}, {"statement": 123}, {"kind": 3, "statement": 123})

    def test_a_numeric_kind_or_statement_gets_a_verdict_not_a_traceback(self):
        pattern = pair_cov.def_regex()
        for row in self.NON_STRING_ROWS:
            with self.subTest(row=row):
                self.assertFalse(detect_cov.is_definitional(row))
                self.assertFalse(pair_cov._is_definitional(row, pattern))

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


#: A Chapter-2 Definition and the one theorem tagged to it.  No table places it
#: and no workspace scope mentions it: it is the item a fabricated ``chapter: 1``
#: dropped out of the coverage split without saying so.
_CHAPTER_TWO_ITEM = {
    "item": "Definition 2.1.1", "kind": "Definition", "number": "2.1.1",
    "chapter": 2, "section": 1, "index": 1, "pdf_page": 41, "page": 25,
    "occurrences": [{"pdf_page": 41, "page": 25, "line": 4}],
}
_CHAPTER_TWO_STATEMENT = {
    "item": "Definition 2.1.1", "decl": "Booklet.about_baz", "repo": COMPANION,
    "statement": "theorem about_baz : True := trivial", "file": "Booklet/C.lean",
    "line": 7, "sorry": False, "source_id": SOURCE_ID, "source_sha": SOURCE_SHA,
}


def _make_workspace(root: Path, *, stage_table: bool = True, chapters: bool = True,
                    extra_items=(), extra_statements=()) -> None:
    """A one-source, one-companion workspace on disk.

    ``chapters=False`` leaves ``sources.<id>.chapters`` out of workspace.yaml, the
    state a book nobody has scoped yet is in.
    """
    (root / "companions" / COMPANION).mkdir(parents=True)
    (root / "repos" / COMPANION).mkdir(parents=True)
    (root / "sources" / SOURCE_ID).mkdir(parents=True)
    statements = [*_STATEMENTS, *extra_statements]
    items = [*_items(), *extra_items]
    (root / "companions" / COMPANION / "statements.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in statements), encoding="utf-8")
    (root / "companions" / COMPANION / "state.json").write_text(
        json.dumps({"name": COMPANION}), encoding="utf-8")
    (root / "sources" / SOURCE_ID / "items.json").write_text(json.dumps({
        "source_id": SOURCE_ID, "source_sha": SOURCE_SHA, "offset": 16,
        "kinds": ["Definition", "Lemma", "Theorem", "Exercise", "Remark"],
        "count": len(items), "items": items,
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
        "    offset: 16\n"
        + ("    chapters: [1]\n" if chapters else "")
        + "    role: audited\n"
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
    CHAPTERS = True
    EXTRA_ITEMS: tuple = ()
    EXTRA_STATEMENTS: tuple = ()

    def setUp(self):
        from cflib import log

        log.set_level(0)
        self.addCleanup(log.set_level, 1)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # resolved: the commands record absolute paths, and on macOS /var is a
        # symlink to /private/var, so an unresolved root compares unequal
        self.root = Path(tmp.name).resolve()
        _make_workspace(self.root, stage_table=self.STAGE_TABLE, chapters=self.CHAPTERS,
                        extra_items=self.EXTRA_ITEMS,
                        extra_statements=self.EXTRA_STATEMENTS)

    def warnings(self) -> list:
        """Collect the command's `warn` lines (logging itself is muted)."""
        from cfcmd import detect_proxies

        messages: list[str] = []
        original = detect_proxies.warn
        detect_proxies.warn = messages.append
        self.addCleanup(setattr, detect_proxies, "warn", original)
        return messages

    def give_the_first_row_a_body(self, body: str) -> None:
        """Put a definition body on the row tagged to ``Definition 1.1.1``."""
        statements = self.root / "companions" / COMPANION / "statements.jsonl"
        rows = [json.loads(line) for line in
                statements.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows[0]["body"] = body
        statements.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def finding_rows(self) -> list:
        """``detect/<name>/findings.cheap.jsonl`` without its ``_meta`` head row."""
        path = self.root / "detect" / COMPANION / "findings.cheap.jsonl"
        rows = [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return rows[1:]

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
            "native_tokens": ["myOwnCoverCost"],
        })
        self.assertEqual(overlaid.stage_of_repo_module("Booklet.Early.Sizes"), 1)
        self.assertEqual(overlaid.native_tokens, ["myOwnCoverCost"])
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
        self.assertEqual([r["item"] for r in self.finding_rows()], ["Definition 1.2.3"])

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
        # nothing in it is live: an example object would be scanned for
        self.assertIs(table["_unedited"], True)
        self.assertEqual(table["module_stages"], [])
        self.assertEqual(table["mathlib_objects"], {})
        with self.assertRaises(CfError) as ctx:
            self.detect("--init-stage-table")
        self.assertIn(str(written), str(ctx.exception) + str(getattr(ctx.exception, "hint", "")))

    def test_the_written_table_then_loads(self):
        self.assertEqual(self.detect("--init-stage-table"), 0)
        table = StageTable.for_source(
            SOURCE_ID, extra_dirs=[self.root / "detect" / "mathlib_stages"])
        self.assertEqual(table.source_id, SOURCE_ID)
        self.assertTrue(table.available)
        self.assertTrue(table.unedited)

    # -- the skeleton is inert until a human edits it ----------------------
    #: An ordinary Lean body that happens to name a generic library identifier.
    #: The skeleton's example object was live and its label was `size`, which
    #: `_token_pattern` matches in any `*.size`, so bootstrapping a table and
    #: running the detector emitted a PROXY row citing a constant that exists in
    #: no library -- into corpus/ and the tally, by default.
    GENERIC_BODY = "def foo (xs : Array Nat) : Nat := xs.size"

    def test_an_unedited_skeleton_scans_nothing_and_invents_nothing(self):
        self.give_the_first_row_a_body(self.GENERIC_BODY)
        self.assertEqual(self.detect("--init-stage-table"), 0)
        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        cov = self.coverage()
        scan = cov["_meta"]["body_proxy_scan"]
        self.assertFalse(scan["ran"])
        self.assertIn("_unedited", scan["reason"])
        self.assertEqual(cov["proxy_candidates"], [])
        self.assertEqual([r["kind"] for r in self.finding_rows()], ["COVERAGE"])

    def test_deleting_the_marker_turns_the_table_on(self):
        """The marker gates the scan, not the emptiness: a filled-in table works."""
        self.give_the_first_row_a_body(self.GENERIC_BODY)
        self.assertEqual(self.detect("--init-stage-table"), 0)
        written = self.root / "detect" / "mathlib_stages" / f"{SOURCE_ID}.json"
        table = json.loads(written.read_text(encoding="utf-8"))
        del table["_unedited"]
        table["module_stages"] = [["Lib.Measure", 2]]
        table["mathlib_objects"] = {
            "Lib.Measure.size": {"label": "size", "module": "Lib.Measure", "stage": 2}}
        written.write_text(json.dumps(table), encoding="utf-8")

        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        cov = self.coverage()
        self.assertTrue(cov["_meta"]["body_proxy_scan"]["ran"])
        self.assertEqual([c["decl"] for c in cov["proxy_candidates"]], ["Booklet.foo"])

    def test_the_lean_tier_refuses_an_unedited_skeleton_too(self):
        self.assertEqual(self.detect("--init-stage-table"), 0)
        deps = self.root / "detect" / COMPANION / "deps.json"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.write_text(json.dumps({"repo_constants": [], "referenced": []}), encoding="utf-8")
        with self.assertRaises(CfError) as ctx:
            self.detect("--lean", "--no-emit-corpus", "--no-tally")
        self.assertIn("_unedited", str(ctx.exception))


class DefCoverageScopeIsNotFabricatedTest(_WorkspaceCase):
    """No table and no recorded chapters must not quietly audit Chapter 1.

    ``_audited_chapter`` fell back to the table's scalar ``chapter``, which
    defaults to 1, so the first run on a new book reported a complete coverage
    split for a scope nobody had chosen and the file recorded no scope at all.
    """

    STAGE_TABLE = False
    CHAPTERS = False
    EXTRA_ITEMS = (_CHAPTER_TWO_ITEM,)
    EXTRA_STATEMENTS = (_CHAPTER_TWO_STATEMENT,)

    def test_an_unscoped_run_audits_every_chapter_and_records_that(self):
        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        cov = self.coverage()
        self.assertEqual(sorted(cov["bridge_only"]),
                         ["Definition 1.2.3", "Definition 2.1.1"])
        self.assertIsNone(cov["_meta"]["chapter"])
        self.assertIn("Definition 2.1.1", [r["item"] for r in self.finding_rows()])


class DefCoverageScopeIsTheWorkspacesTest(_WorkspaceCase):
    """...and a scope the workspace DOES record still narrows the audit."""

    STAGE_TABLE = False
    EXTRA_ITEMS = (_CHAPTER_TWO_ITEM,)
    EXTRA_STATEMENTS = (_CHAPTER_TWO_STATEMENT,)

    def test_a_recorded_scope_is_honoured_and_recorded(self):
        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        cov = self.coverage()
        self.assertEqual(cov["bridge_only"], ["Definition 1.2.3"])
        self.assertEqual(cov["_meta"]["chapter"], 1)


class CompanionOverlayIsReadFromTheWorkspaceTest(_WorkspaceCase):
    """`companions/<name>/stage-overlay.json`, beside conventions.md."""

    def _overlaid_run(self) -> dict:
        (self.root / "companions" / COMPANION / "stage-overlay.json").write_text(
            json.dumps({"companion": COMPANION, "source_id": SOURCE_ID,
                        "repo_module_stages": [["Booklet.A", 1]],
                        "native_tokens": ["myOwnCoverCost"]}), encoding="utf-8")
        statements = self.root / "companions" / COMPANION / "statements.jsonl"
        rows = [json.loads(line) for line in
                statements.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows[0]["body"] = "def foo : X := Lib.Measure.size E"
        rows[0]["item"] = "Definition 1.2.3"
        statements.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        return self.coverage()

    def test_the_overlay_is_applied_to_the_body_scan(self):
        cov = self._overlaid_run()
        self.assertTrue(cov["_meta"]["body_proxy_scan"]["ran"])
        self.assertEqual(cov["_meta"]["companion_overlay"],
                         str(self.root / "companions" / COMPANION / "stage-overlay.json"))
        self.assertEqual([c["decl"] for c in cov["proxy_candidates"]], ["Booklet.foo"])

    def test_without_an_overlay_the_scan_still_runs(self):
        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        cov = self.coverage()
        self.assertTrue(cov["_meta"]["body_proxy_scan"]["ran"])
        self.assertIsNone(cov["_meta"]["companion_overlay"])

    def test_a_scan_with_no_companion_vocabulary_says_the_evidence_is_empty(self):
        """`native_tokens` left the shipped table; a run that relied on it is told.

        The scan still runs and still emits PROXY rows, so no count moves -- what
        is lost is `native_tokens_in_body`, which is how a reader tells "the body
        still builds the work's object" from "the body IS the library's object".
        """
        messages = self.warnings()
        self.assertEqual(self.detect("--no-emit-corpus", "--no-tally"), 0)
        self.assertTrue([m for m in messages if "native_tokens_in_body" in m], messages)
        self.assertTrue([m for m in messages if "stage-overlay.json" in m], messages)

    def test_an_overlay_that_maps_them_is_not_warned_about(self):
        messages = self.warnings()
        self._overlaid_run()
        self.assertEqual([m for m in messages if "native_tokens" in m], [])


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


class ShippedTemplatesCarryNoCompanionVocabularyTest(unittest.TestCase):
    """CLAUDE.md S1: examples in templates are synthetic.

    The whole point of splitting the stage table was that a published copy of
    this skill must not carry a private companion's own construction names --
    and the overlay template's ``native_tokens`` example was seeded from the very
    list the split deleted from the shipped table.  The recorded fixture is the
    one place those names may sit, and its ``_note`` says what it is; the real
    names are read from there so this test does not restate them.
    """

    def test_no_detect_template_names_a_real_construction(self):
        recorded = json.loads((REPO / "tests" / "data" / "stage-overlay.adam.json")
                              .read_text(encoding="utf-8"))
        tokens = [str(t) for t in recorded["native_tokens"]]
        self.assertTrue(tokens)
        templates = sorted((REPO / "templates" / "detect").glob("*.tmpl"))
        self.assertTrue(templates)
        for template in templates:
            text = template.read_text(encoding="utf-8")
            for token in tokens:
                with self.subTest(template=template.name, token=token):
                    self.assertNotIn(token, text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
