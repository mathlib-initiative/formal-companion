"""Tests for the proxy detector (scripts/detect, cf detect-proxies).

Two kinds of test:

* **unit** tests over small fixtures, which always run;
* **regression** tests against the on-disk baseline, which read their inputs
  through environment variables and skip cleanly when those are unset::

      CF_BASELINE_DIFF=<the v1 differential directory> \\
      CF_BOOK_TXT=<the book's extracted text> \\
          python3 -m unittest discover -s tests

  The two anchors are the ones docs/design/stage_map.md Stage 8 names: the cheap
  tier must reproduce ``def-coverage.ours.json`` (32 stated / 4 bridge-only /
  0 missing), and ``analyze_stages`` over the v1 detector's ``deps.json`` must
  reproduce the Definition 1.2.2 hit (``OpenExcessLebesgueMeasurable`` via
  ``volume``) and the ``elementaryMeasure`` history that detector's README
  records.

Nothing here runs lake, ssh or tend, and nothing writes outside a temp dir.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from detect import analyze_stages, def_coverage  # noqa: E402
from detect import findings as find_rows  # noqa: E402
from detect._compat import vocab  # noqa: E402
from detect.deps import DepsIndex  # noqa: E402
from detect.stages import StageTable  # noqa: E402

BASELINE_DIFF = os.environ.get("CF_BASELINE_DIFF")
BOOK_TXT = os.environ.get("CF_BOOK_TXT")

#: the two Tao editions, by the ids registry/sources.yaml gives them
PUBLISHED_ID = "tao-measure-gsm126-published"
DRAFT_ID = "tao-measure-2011-draft"
PUBLISHED_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
DRAFT_SHA = "62d95658" + "0" * 56

SOURCE_KINDS = ("Definition", "Lemma", "Theorem", "Proposition", "Corollary",
                "Exercise", "Example", "Remark")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(errors="ignore").splitlines() if line.strip()]


def _items_from_book(book_text: str) -> list[dict]:
    """The items.json the book stage produces, rebuilt here so the regression
    tests do not depend on another stage having run.

    Whitespace-tolerant on purpose: the single-space regex of the baseline lost
    ``Exercise    1.3.19``.
    """
    items: list[dict] = []
    seen: set[str] = set()
    for kind in SOURCE_KINDS:
        for m in re.finditer(rf"(?:^|\n)[ \t]*({kind}[ \t]+1\.\d+\.\d+)", book_text):
            ident = re.sub(r"\s+", " ", m.group(1))
            if ident in seen:
                continue
            seen.add(ident)
            items.append({"item": ident, "printed_page": book_text.count("\f", 0, m.start()) + 1 - 16})
    return items


# ==========================================================================
# unit tests: stage table
# ==========================================================================
class StageTableTest(unittest.TestCase):
    def setUp(self):
        self.stages = StageTable.for_source(PUBLISHED_ID)

    def test_shipped_table_loads(self):
        self.assertEqual(self.stages.source_id, PUBLISHED_ID)
        self.assertEqual(self.stages.chapter, 1)
        self.assertTrue(self.stages.module_stages)
        self.assertIn("MeasureTheory.MeasureSpace.volume", self.stages.objects)

    def test_one_table_serves_every_edition_of_the_work(self):
        """A stage is which SECTION develops a concept, not which page.

        Two editions of one work share their section structure even when their
        pagination differs, so the draft resolves to the same shipped file.
        """
        self.assertEqual(sorted(self.stages.source_ids), sorted([PUBLISHED_ID, DRAFT_ID]))
        draft = StageTable.for_source(DRAFT_ID)
        self.assertEqual(draft.path, self.stages.path)
        self.assertEqual(draft.stage_of_item("Definition 1.2.2"), 2)

    def test_module_stage_is_first_match_wins(self):
        # Lebesgue measure is developed in S1.2 ...
        self.assertEqual(self.stages.stage_of_module("Mathlib.MeasureTheory.Measure.Lebesgue.Basic"), 2)
        # ... abstract measure spaces in S1.4 ...
        self.assertEqual(self.stages.stage_of_module("Mathlib.MeasureTheory.Measure.Restrict"), 4)
        # ... and anything outside the subject is foundations.
        self.assertEqual(self.stages.stage_of_module("Mathlib.Order.Basic"), 0)

    def test_item_stage_is_the_section_number(self):
        self.assertEqual(self.stages.stage_of_item("Definition 1.2.2"), 2)
        self.assertEqual(self.stages.stage_of_item("Definition 1.2.2 (Lebesgue measurability)"), 2)
        self.assertEqual(self.stages.stage_of_item("Exercise    1.3.19"), 3)
        # another chapter has no stage in this table
        self.assertIsNone(self.stages.stage_of_item("Exercise 2.1.1"))
        self.assertIsNone(self.stages.stage_of_item("In-text p.7"))

    def test_an_unknown_source_names_the_shipped_tables(self):
        with self.assertRaises(Exception) as ctx:
            StageTable.for_source("no-such-source")
        message = str(getattr(ctx.exception, "hint", "")) + str(ctx.exception)
        self.assertIn(PUBLISHED_ID, message)


# ==========================================================================
# unit tests: cheap tier
# ==========================================================================
def _row(item, decl, statement, **kw):
    row = {"item": item, "decl": decl, "statement": statement, "file": "F.lean", "line": 1,
           "sorry": False, "repo": "ours"}
    row.update(kw)
    return row


class DefCoverageTest(unittest.TestCase):
    def test_classifies_stated_bridge_only_and_missing(self):
        rows = [
            _row("Definition 1.1.1", "IsInterval", "def IsInterval (I : Set ℝ) : Prop"),
            _row("Definition 1.1.4", "elem_union", "theorem elem_union : True"),
        ]
        cov = def_coverage.compute_def_coverage(
            ["Definition 1.1.1", "Definition 1.1.4", "Definition 1.1.9"], rows)
        self.assertEqual(cov["stated"], ["Definition 1.1.1"])
        self.assertEqual(cov["bridge_only"], ["Definition 1.1.4"])
        self.assertEqual(cov["missing"], ["Definition 1.1.9"])
        self.assertEqual(cov["bridge_only_detail"]["Definition 1.1.4"], ["elem_union"])

    def test_definitional_test_handles_modifiers_and_kind_field(self):
        self.assertTrue(def_coverage.is_definitional(
            _row("Definition 1.1.1", "d", "@[simp] private noncomputable def d : Nat")))
        self.assertTrue(def_coverage.is_definitional(
            _row("Definition 1.1.1", "d", "structure Foo where")))
        self.assertTrue(def_coverage.is_definitional(
            _row("Definition 1.1.1", "d", "instance : Foo Bar")))
        self.assertFalse(def_coverage.is_definitional(
            _row("Definition 1.1.1", "d", "theorem t : True")))
        # an explicit kind wins over the signature text
        self.assertTrue(def_coverage.is_definitional(
            _row("Definition 1.1.1", "d", "theorem t : True", kind="def")))

    def test_sub_parts_and_titles_collapse_onto_the_base_item(self):
        rows = [_row("Definition 1.3.8 (Unsigned measurable function)", "UnsignedMeasurable",
                     "def UnsignedMeasurable : Prop"),
                _row("Definition 1.3.8(ii)", "helper", "theorem helper : True")]
        cov = def_coverage.compute_def_coverage(["Definition 1.3.8"], rows)
        self.assertEqual(cov["stated"], ["Definition 1.3.8"])

    def test_real_items_json_rows_are_read(self):
        """The shape book/prepare_book.py writes: kind + occurrences[]."""
        rows = [{"item": "Definition 1.2.2", "kind": "Definition", "chapter": 1, "section": 2,
                 "occurrences": [{"pdf_page": 34, "page": 18, "sub": "", "title": "Lebesgue measurability"}]}]
        self.assertEqual(def_coverage.load_items({"items": rows}, kind="Definition", chapter=1),
                         ["Definition 1.2.2"])
        from cfcmd import detect_proxies

        self.assertEqual(detect_proxies._items_pages({"items": rows}, ()), {"Definition 1.2.2": 18})

    def test_items_json_shapes_are_tolerated(self):
        wanted = ["Definition 1.1.1", "Definition 1.2.2"]
        raw = ["Definition 1.1.1", "Definition 1.2.2", "Lemma 1.1.2", "junk"]
        self.assertEqual(def_coverage.load_items(raw, kind="Definition"), wanted)
        objs = [{"item": "Definition 1.2.2", "printed_page": 18}, {"id": "Definition 1.1.1"}]
        self.assertEqual(def_coverage.load_items(objs, kind="Definition"), wanted)
        self.assertEqual(def_coverage.load_items({"items": raw}, kind="Definition"), wanted)
        self.assertEqual(def_coverage.load_items(raw, kind="Definition", chapter=2), [])

    def test_definitional_kinds_agree_with_the_extract_adapters(self):
        """Drift guard: whatever the adapters call a definition, so do we.

        The adapters capture ``body`` only for their DEFINITION_KINDS, and the
        body-proxy scan needs a body, so a kind they treat as definitional and
        we do not would be silently unscanned.
        """
        from adapters.leanparse import DEFINITION_KINDS

        self.assertTrue(
            DEFINITION_KINDS <= set(def_coverage.DEFINITIONAL_KINDS),
            f"adapters treat {sorted(DEFINITION_KINDS - set(def_coverage.DEFINITIONAL_KINDS))} "
            "as definitional but detect does not",
        )

    def test_uncovered_items(self):
        rows = [_row("Exercise 1.1.1", "e", "theorem e : True")]
        self.assertEqual(
            def_coverage.uncovered_items(["Exercise 1.1.1", "Remark 1.2.3"], rows),
            ["Remark 1.2.3"])


class BodyProxyTest(unittest.TestCase):
    """The ``elementaryMeasure := (volume E).toNNReal`` shape, from bodies alone."""

    def setUp(self):
        self.stages = StageTable.for_source(PUBLISHED_ID)

    def _candidates(self, rows):
        return def_coverage.body_proxy_candidates(rows, self.stages)

    def test_flags_a_definition_whose_body_is_the_library_object(self):
        rows = [
            _row("Lemma 1.1.2", "elementaryMeasure",
                 "noncomputable def elementaryMeasure (E : ElementarySet d) : NNReal",
                 body="noncomputable def elementaryMeasure (E : ElementarySet d) : NNReal :=\n"
                      "  (volume (E : Set (EuclideanSpace ℝ (Fin d)))).toNNReal"),
        ]
        cands = self._candidates(rows)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["item"], "Lemma 1.1.2")
        self.assertEqual([h["label"] for h in cands[0]["hits"]], ["volume"])
        self.assertEqual(cands[0]["bridge"], "none")

    def test_intrinsic_body_is_not_flagged(self):
        rows = [
            _row("Lemma 1.1.2", "elementaryMeasure",
                 "noncomputable def elementaryMeasure (E : ElementarySet d) : NNReal",
                 body="noncomputable def elementaryMeasure (E : ElementarySet d) : NNReal :=\n"
                      "  elementaryContent E"),
        ]
        self.assertEqual(self._candidates(rows), [])

    def test_a_docstring_mention_is_not_a_dependency(self):
        rows = [
            _row("Lemma 1.1.2", "elementaryMeasure",
                 "noncomputable def elementaryMeasure : NNReal",
                 body="/-- agrees with `volume` (see the bridge lemma) -/\n"
                      "noncomputable def elementaryMeasure : NNReal := elementaryContent E "
                      "-- not volume"),
        ]
        self.assertEqual(self._candidates(rows), [])

    def test_earlier_stage_objects_are_ordinary_mathematics(self):
        # a S1.4 definition using S1.2's volume reaches backwards: not a proxy
        rows = [
            _row("Definition 1.4.31", "someDef", "def someDef : Prop",
                 body="def someDef : Prop := volume s = 0"),
        ]
        self.assertEqual(self._candidates(rows), [])

    def test_a_name_the_companion_defines_itself_is_not_the_library_object(self):
        """tao-measure-lean has its own `volume` (the book's box volume, S1.1)."""
        rows = [
            _row("Definition 1.1.1", "volume", "noncomputable def volume : ℝ",
                 body="noncomputable def volume (B : Set E) : ℝ := ∏ i, (b i - a i)"),
            _row("Lemma 1.1.2", "elemMeasure", "noncomputable def elemMeasure : ℝ",
                 body="noncomputable def elemMeasure (E : Set F) : ℝ := ∑ i, volume (pieces i)"),
        ]
        self.assertEqual(def_coverage.shadowed_object_labels(rows, self.stages), ["volume"])
        self.assertEqual(self._candidates(rows), [])

    def test_a_qualified_occurrence_still_counts_when_the_name_is_shadowed(self):
        rows = [
            _row("Definition 1.1.1", "volume", "noncomputable def volume : ℝ",
                 body="noncomputable def volume (B : Set E) : ℝ := ∏ i, (b i - a i)"),
            _row("Lemma 1.1.2", "elemMeasure", "noncomputable def elemMeasure : ℝ",
                 body="noncomputable def elemMeasure (E : Set F) : ℝ := "
                      "(MeasureTheory.MeasureSpace.volume E).toReal"),
        ]
        cands = self._candidates(rows)
        self.assertEqual([c["decl"] for c in cands], ["elemMeasure"])

    def test_bridge_is_classified_from_the_rows(self):
        proxy = _row("Definition 1.3.12", "upperIntegral", "def upperIntegral : ℝ≥0∞",
                     body="def upperIntegral : ℝ≥0∞ := lintegral f")
        proved = _row("Definition 1.3.12", "upperIntegral_eq_lintegral",
                      "theorem upperIntegral_eq_lintegral : upperIntegral f = lintegral f")
        fiat = _row("Definition 1.3.12", "upperIntegral_eq_lintegral",
                    "theorem upperIntegral_eq_lintegral : upperIntegral f = lintegral f", sorry=True)
        self.assertEqual(self._candidates([proxy, proved])[0]["bridge"], "proved")
        self.assertEqual(self._candidates([proxy, fiat])[0]["bridge"], "fiat")
        self.assertEqual(self._candidates([proxy])[0]["bridge"], "none")


class FindingRowTest(unittest.TestCase):
    def setUp(self):
        self.stages = StageTable.for_source(PUBLISHED_ID)
        self.cov = {"missing": ["Definition 1.1.9"], "bridge_only": ["Definition 1.4.1"],
                    "stated": [], "bridge_only_detail": {"Definition 1.4.1": ["isSetAlgebra_iff"]}}

    def test_rows_use_the_shared_vocabulary(self):
        rows = def_coverage.build_finding_rows(
            self.cov, [], companion="ours", run_id="detect-ours-cheap",
            source_id=PUBLISHED_ID, source_sha=PUBLISHED_SHA)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn(row["kind"], vocab.FINDING_KINDS)
            self.assertIn(row["severity"], vocab.SEVERITY_SET)
            self.assertEqual(row["source_id"], PUBLISHED_ID)
            self.assertEqual(row["source_sha"], PUBLISHED_SHA)
            self.assertNotIn("book_sha", row)
            self.assertIsNone(row["book_quote"])
            self.assertIsNone(row["witness"])
            self.assertIsInstance(row["faithful"], list)
            for field in vocab.EVIDENCE_BY_KIND[row["kind"]]:
                self.assertIn(field, row)
        bridge_only = [r for r in rows if r["item"] == "Definition 1.4.1"][0]
        self.assertEqual(bridge_only["unfaithful_decls"], ["ours:isSetAlgebra_iff"])
        missing = [r for r in rows if r["item"] == "Definition 1.1.9"][0]
        self.assertEqual(missing["unfaithful_decls"], [])  # nothing to route on

    def test_finding_id_is_stable_and_content_addressed(self):
        def rows(**over):
            kw = dict(companion="ours", run_id="r1", source_id=PUBLISHED_ID)
            kw.update(over)
            return [r["finding_id"]
                    for r in def_coverage.build_finding_rows(self.cov, [], **kw)]

        self.assertEqual(rows(), rows())
        self.assertNotEqual(rows(), rows(run_id="r2"))
        # ... and the SOURCE is part of the id: the same item of the same repo
        # in two editions is two findings, not one found twice.
        self.assertNotEqual(rows(), rows(source_id=DRAFT_ID))

    def test_invalid_severity_is_refused(self):
        with self.assertRaises(Exception):
            find_rows.proxy_row(run_id="r", companion="ours", item="Definition 1.1.1", decl="d",
                                rule="BODY_PROXY", severity="catastrophic", concept="c",
                                reasoning="r", bridge="none")

    def test_invalid_bridge_is_refused(self):
        with self.assertRaises(Exception):
            find_rows.proxy_row(run_id="r", companion="ours", item="Definition 1.1.1", decl="d",
                                rule="BODY_PROXY", severity="low", concept="c",
                                reasoning="r", bridge="probably")


# ==========================================================================
# unit tests: deps index
# ==========================================================================
def _const(name, kind="def", module="Repo.A", type_deps=(), value_deps=()):
    return {"name": name, "module": module, "kind": kind,
            "type_deps": list(type_deps), "value_deps": list(value_deps)}


class DepsIndexTest(unittest.TestCase):
    def setUp(self):
        self.deps = DepsIndex({
            "repo_constants": [
                _const("Repo.d", value_deps=["Repo.helper", "Lib.volume"]),
                _const("Repo.helper", value_deps=["Lib.deep"]),
                _const("Repo.t", kind="theorem", type_deps=["Repo.d", "Lib.volume"]),
                _const("Repo.t_sorry", kind="theorem", type_deps=["Repo.d", "Lib.volume"],
                       value_deps=["sorryAx"]),
            ],
            "referenced": [
                {"name": "Lib.volume", "module": "Lib.Measure", "kind": "def"},
                {"name": "Lib.deep", "module": "Lib.Deep", "kind": "def"},
            ],
        })

    def test_closure_follows_definitions_transitively(self):
        ext, seen, parent = self.deps.closure("Repo.d")
        self.assertEqual(set(ext), {"Lib.volume", "Lib.deep"})
        self.assertIn("Repo.helper", seen)
        self.assertEqual(self.deps.path_to("Repo.helper", parent, "Repo.d"),
                         ["Repo.d", "Repo.helper"])

    def test_theorems_are_followed_through_their_statement_only(self):
        ext, _seen, _parent = self.deps.closure("Repo.t")
        self.assertIn("Lib.volume", ext)
        self.assertIn("Lib.deep", ext)  # reached by unfolding the repo definition

    def test_bridge_classification(self):
        bridges = self.deps.bridge_theorems("Repo.d", ["Lib.volume"])
        self.assertEqual([b["decl"] for b in bridges], ["Repo.t", "Repo.t_sorry"])
        self.assertEqual(DepsIndex.classify_bridge(bridges), "proved")
        only_sorry = [b for b in bridges if b["sorry"]]
        self.assertEqual(DepsIndex.classify_bridge(only_sorry), "fiat")
        self.assertEqual(DepsIndex.classify_bridge([]), "none")

    def test_sorry_is_detected_through_sorryAx(self):
        self.assertTrue(self.deps.uses_sorry("Repo.t_sorry"))
        self.assertFalse(self.deps.uses_sorry("Repo.t"))


class RulesOnSyntheticDepsTest(unittest.TestCase):
    """Both rules, on a two-constant book with a hand-made stage table."""

    def setUp(self):
        self.stages = StageTable({
            "book": "toy", "chapter": 1, "default_stage": 0, "stage_beyond_book": 8,
            "module_stages": [["Lib.Late", 4], ["Lib.Measure", 2], ["Lib.Unknown", None]],
            "mathlib_objects": {}, "native_tokens": [],
        })
        self.deps = DepsIndex({
            "repo_constants": [
                _const("Repo.early", value_deps=["Lib.later"]),
                _const("Repo.same", value_deps=["Lib.measure"]),
                _const("Repo.murky", value_deps=["Lib.mystery"]),
            ],
            "referenced": [
                {"name": "Lib.later", "module": "Lib.Late", "kind": "def"},
                {"name": "Lib.measure", "module": "Lib.Measure", "kind": "def"},
                {"name": "Lib.mystery", "module": "Lib.Unknown", "kind": "def"},
            ],
        })

    def test_order_violation(self):
        rows = [_row("Definition 1.1.1", "Repo.early", "def Repo.early : Prop")]
        report = analyze_stages.analyze(self.deps, self.stages, rows)
        self.assertEqual(len(report["order_violations"]), 1)
        self.assertEqual(report["order_violations"][0]["max_stage"], 4)
        self.assertEqual(report["same_stage_primitives"], [])

    def test_same_stage_primitive(self):
        rows = [_row("Definition 1.2.1", "Repo.same", "def Repo.same : Prop")]
        report = analyze_stages.analyze(self.deps, self.stages, rows)
        self.assertEqual(report["order_violations"], [])
        self.assertEqual(len(report["same_stage_primitives"]), 1)
        self.assertEqual(report["same_stage_primitives"][0]["direct_same_stage"][0][1], "Lib.measure")

    def test_unclassified_modules_are_reported_not_swallowed(self):
        rows = [_row("Definition 1.1.1", "Repo.murky", "def Repo.murky : Prop")]
        report = analyze_stages.analyze(self.deps, self.stages, rows)
        self.assertEqual(report["unclassified_modules"], {"Lib.Unknown": 1})
        self.assertEqual(report["order_violations"], [])

    def test_theorem_rows_and_phantom_declarations_are_not_key_definitions(self):
        rows = [
            _row("Definition 1.1.1", "Repo.early", "def Repo.early : Prop"),
            _row("Definition 1.1.2", "Repo.absent", "def Repo.absent : Prop"),
        ]
        pairs, unknown = analyze_stages.key_declarations(rows, self.deps)
        self.assertEqual(pairs, [("Definition 1.1.1", "Repo.early")])
        self.assertEqual(unknown, ["Repo.absent"])

    def test_key_flag_is_honoured_when_the_table_has_one(self):
        rows = [
            _row("Definition 1.1.1", "Repo.early", "def Repo.early : Prop", key=False),
            _row("Definition 1.2.1", "Repo.same", "def Repo.same : Prop", key=True),
        ]
        pairs, _ = analyze_stages.key_declarations(rows, self.deps)
        self.assertEqual(pairs, [("Definition 1.2.1", "Repo.same")])
        pairs_all, _ = analyze_stages.key_declarations(rows, self.deps, key_only=False)
        self.assertEqual(len(pairs_all), 2)


# ==========================================================================
# unit tests: DumpDeps template and dump_deps.sh
# ==========================================================================
class DumpDepsTemplateTest(unittest.TestCase):
    TEMPLATE = REPO / "scripts" / "detect" / "DumpDeps.lean.tmpl"
    SCRIPT = REPO / "scripts" / "detect" / "dump_deps.sh"

    def test_template_has_the_four_placeholders(self):
        text = self.TEMPLATE.read_text()
        for key in ("{{package}}", "{{root_module}}", "{{module_prefix}}", "{{output_path}}"):
            self.assertIn(key, text)

    def test_rendering_leaves_no_placeholder(self):
        text = self.TEMPLATE.read_text()
        for key, value in [("package", "TaoMeasure"), ("root_module", "TaoMeasure"),
                           ("module_prefix", "TaoMeasure"), ("output_path", "/tmp/deps.json")]:
            text = text.replace("{{" + key + "}}", value)
        self.assertNotIn("{{", text)
        self.assertIn("import TaoMeasure", text)
        self.assertIn('IO.FS.writeFile "/tmp/deps.json"', text)

    def _run(self, args, cwd):
        return subprocess.run([str(self.SCRIPT), *args], capture_output=True, text=True, cwd=cwd)

    def test_script_refuses_an_output_inside_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "companion"
            (repo / ".lake").mkdir(parents=True)
            out = self._run(["--repo", str(repo), "--out", str(repo / "deps.json"),
                             "--root-module", "M", "--module-prefix", "M.Tao", "--dry-run"], tmp)
            self.assertNotEqual(out.returncode, 0)
            self.assertIn("inside the companion tree", out.stderr)

    def test_script_refuses_an_unbuilt_companion(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "companion"
            repo.mkdir()
            out = self._run(["--repo", str(repo), "--out", str(Path(tmp) / "deps.json"),
                             "--root-module", "M", "--module-prefix", "M.Tao", "--dry-run"], tmp)
            self.assertNotEqual(out.returncode, 0)
            self.assertIn("no .lake", out.stderr)

    def test_dry_run_renders_and_prints_the_command_without_running_lean(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "companion"
            (repo / ".lake").mkdir(parents=True)
            out_path = Path(tmp) / "detect" / "adam" / "deps.json"
            res = self._run(["--repo", str(repo), "--out", str(out_path), "--package", "MeasureTheory",
                             "--root-module", "MeasureTheory", "--module-prefix", "MeasureTheory.Tao",
                             "--dry-run"], tmp)
            self.assertEqual(res.returncode, 0, res.stderr)
            rendered = out_path.parent / "DumpDeps.lean"
            self.assertTrue(rendered.exists())
            text = rendered.read_text()
            self.assertNotIn("{{", text)
            self.assertIn("MeasureTheory.Tao", text)
            self.assertIn("lake env lean --run", res.stdout)
            self.assertFalse(out_path.exists())  # nothing was built or dumped

    def test_it_survives_a_PATH_with_no_timeout_under_bash_3_2(self):
        """The documented no-timeout fallback used to die on the next line.

        ``runner=()`` expanded as ``"${runner[@]}"`` is an unbound-variable
        error in bash 3.2 with ``set -u`` -- and bash 3.2 is what
        ``#!/usr/bin/env bash`` resolves to on a stock macOS.
        """
        shell = "/bin/bash"
        if not Path(shell).exists():
            self.skipTest("no /bin/bash")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "companion"
            (repo / ".lake").mkdir(parents=True)
            out_path = Path(tmp) / "deps.json"
            # a PATH with no timeout(1)/gtimeout(1) anywhere on it
            clean = Path(tmp) / "bin"
            clean.mkdir()
            for tool in ("sed", "dirname", "mkdir", "wc", "tr", "git", "python3", "env"):
                src = shutil.which(tool)
                if src:
                    os.symlink(src, clean / tool)
            res = subprocess.run(
                [shell, str(self.SCRIPT), "--repo", str(repo), "--out", str(out_path),
                 "--package", "M", "--root-module", "M", "--module-prefix", "M", "--dry-run"],
                capture_output=True, text=True, cwd=tmp,
                env={"HOME": os.environ.get("HOME", tmp), "PATH": str(clean)})
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("no timeout(1) found", res.stderr)
            self.assertNotIn("unbound variable", res.stderr)
            self.assertIn("lake env lean --run", res.stdout)


# ==========================================================================
# regression tests against the baseline (skip when the env vars are unset)
# ==========================================================================
@unittest.skipUnless(BASELINE_DIFF and BOOK_TXT, "set CF_BASELINE_DIFF and CF_BOOK_TXT")
class BaselineDefCoverageTest(unittest.TestCase):
    """The cheap tier must reproduce def-coverage.ours.json: 32 / 4 / 0."""

    @classmethod
    def setUpClass(cls):
        cls.diff = Path(BASELINE_DIFF)
        cls.rows = _read_jsonl(cls.diff / "ours.jsonl")
        book = Path(BOOK_TXT).read_text(errors="ignore")
        cls.items = _items_from_book(book)
        cls.expected = json.loads((cls.diff / "def-coverage.ours.json").read_text())

    def test_reproduces_the_baseline_table(self):
        definitions = def_coverage.load_items(self.items, kind="Definition", chapter=1)
        self.assertEqual(len(definitions), 36)
        cov = def_coverage.compute_def_coverage(definitions, self.rows)
        self.assertEqual(cov["stated"], self.expected["stated"])
        self.assertEqual(cov["bridge_only"], self.expected["bridge_only"])
        self.assertEqual(cov["missing"], self.expected["missing"])
        self.assertEqual((len(cov["stated"]), len(cov["bridge_only"]), len(cov["missing"])), (32, 4, 0))

    def test_bridge_only_items_name_the_declarations_that_cite_them(self):
        definitions = def_coverage.load_items(self.items, kind="Definition", chapter=1)
        cov = def_coverage.compute_def_coverage(definitions, self.rows)
        detail = cov["bridge_only_detail"]
        self.assertEqual(sorted(detail), sorted(self.expected["bridge_only"]))
        for decls in detail.values():
            self.assertTrue(decls)

    def test_uncovered_items_match_the_baseline(self):
        expected = json.loads((self.diff / "uncovered.ours.json").read_text())
        all_items = def_coverage.load_items(self.items, chapter=1)
        self.assertEqual(def_coverage.uncovered_items(all_items, self.rows), expected)

    def test_whitespace_tolerant_parsing_keeps_exercise_1_3_19(self):
        # the baseline's single-space regex dropped this heading
        self.assertIn("Exercise 1.3.19", def_coverage.load_items(self.items, chapter=1))


@unittest.skipUnless(
    BASELINE_DIFF and (Path(BASELINE_DIFF or ".") / "adam-detector" / "deps.json").exists(),
    "set CF_BASELINE_DIFF (needs adam-detector/deps.json)",
)
class BaselineAnalyzeStagesTest(unittest.TestCase):
    """analyze_stages over the real dump + the adam statement table."""

    ELEMENTARY = "MeasureTheory.Tao.Elementary.elementaryMeasure"
    OPEN_EXCESS = "MeasureTheory.Tao.Lebesgue.OpenExcessLebesgueMeasurable"

    @classmethod
    def setUpClass(cls):
        diff = Path(BASELINE_DIFF)
        cls.raw = json.loads((diff / "adam-detector" / "deps.json").read_text())
        cls.rows = _read_jsonl(diff / "adam.jsonl")
        # the broad scan needs the COMPANION half of the table (that repo's own
        # module layout), which is no longer shipped inside the source's table:
        # in a workspace it is companions/<name>/stage-overlay.json
        cls.stages = StageTable.for_source(PUBLISHED_ID).with_companion_overlay(
            Path(__file__).resolve().parent / "data" / "stage-overlay.adam.json")
        cls.deps = DepsIndex(cls.raw)
        cls.report = analyze_stages.analyze(cls.deps, cls.stages, cls.rows)

    # -- the Definition 1.2.2 hit ------------------------------------------
    def test_definition_1_2_2_is_a_same_stage_primitive(self):
        hits = [h for h in self.report["same_stage_primitives"]
                if h["item"] == "Definition 1.2.2" and h["decl"] == self.OPEN_EXCESS]
        self.assertEqual(len(hits), 1, "Definition 1.2.2 / OpenExcessLebesgueMeasurable not flagged")
        constants = [c for _s, c, _m in hits[0]["direct_same_stage"]]
        self.assertIn("MeasureTheory.MeasureSpace.volume", constants)
        self.assertIn("MeasureTheory.Measure.toOuterMeasure", constants)
        self.assertTrue(all(stage == 2 for stage, _c, _m in hits[0]["direct_same_stage"]))

    def test_definition_1_2_2_becomes_a_proxy_finding(self):
        rows = analyze_stages.build_finding_rows(
            self.report, self.deps, self.stages,
            companion="adam", run_id="detect-adam-lean",
            source_id=PUBLISHED_ID, source_sha=PUBLISHED_SHA)
        rows = [r for r in rows if r["item"] == "Definition 1.2.2"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["kind"], vocab.PROXY)
        self.assertEqual(row["unfaithful_decls"], [f"adam:{self.OPEN_EXCESS}"])
        self.assertIn(row["bridge"], vocab.BRIDGE_KINDS)
        self.assertEqual(row["evidence"]["rule"], analyze_stages.RULE_SAME_STAGE_PRIMITIVE)
        self.assertEqual(row["source_id"], PUBLISHED_ID)
        self.assertEqual(row["source_sha"], PUBLISHED_SHA)

    # -- the elementaryMeasure history -------------------------------------
    def test_elementary_measure_is_native_at_this_commit(self):
        """After PRs #6/#8 the definition is intrinsic; only a bridge mentions volume."""
        flagged = [h["decl"] for h in self.report["order_violations"] + self.report["same_stage_primitives"]
                   if h["decl"] == self.ELEMENTARY]
        self.assertEqual(flagged, [], "elementaryMeasure should be native at this commit")
        value_deps = self.deps.repo[self.ELEMENTARY]["value_deps"]
        self.assertNotIn("MeasureTheory.MeasureSpace.volume", value_deps)
        bridges = self.deps.bridge_theorems(self.ELEMENTARY, ["MeasureTheory.MeasureSpace.volume"])
        self.assertIn("MeasureTheory.Tao.Elementary.elementaryMeasure_eq_toNNReal_volume",
                      [b["decl"] for b in bridges])
        self.assertEqual(DepsIndex.classify_bridge(bridges), "proved")

    def test_the_pre_pr8_shape_would_be_caught(self):
        """`elementaryMeasure := (volume E).toNNReal` is a development-order hit."""
        mutated = copy.deepcopy(self.raw)
        for const in mutated["repo_constants"]:
            if const["name"] == self.ELEMENTARY:
                const["value_deps"] = ["MeasureTheory.Tao.Elementary.ElementarySet",
                                       "MeasureTheory.MeasureSpace.volume", "ENNReal.toNNReal"]
        deps = DepsIndex(mutated)
        report = analyze_stages.analyze(deps, self.stages, self.rows)
        hits = [h for h in report["order_violations"] if h["decl"] == self.ELEMENTARY]
        self.assertEqual(len(hits), 1, "the pre-PR#8 proxy shape was not caught")
        hit = hits[0]
        self.assertEqual(hit["item"], "Lemma 1.1.2")
        self.assertEqual(hit["stage"], 1)          # elementary measure is S1.1 ...
        self.assertEqual(hit["max_stage"], 2)      # ... volume is S1.2
        self.assertIn("MeasureTheory.MeasureSpace.volume", [c for _s, c, _m in hit["later_hits"]])
        self.assertEqual(hit["example_path"], [self.ELEMENTARY, "MeasureTheory.MeasureSpace.volume"])

    # -- the analysis as a whole -------------------------------------------
    def test_totals_match_the_recorded_report(self):
        """analyze2.py's report_keydefs.json: 2 order violations, 34 same-stage."""
        self.assertEqual(len(self.report["order_violations"]), 2)
        self.assertEqual(len(self.report["same_stage_primitives"]), 34)

    def test_no_unclassified_modules_and_no_phantom_declarations(self):
        self.assertEqual(self.report["unclassified_modules"], {})
        self.assertEqual(self.report["decls_not_in_deps"], [])
        self.assertEqual(self.report["items_without_stage"], [])

    def test_broad_scan_matches_the_recorded_report(self):
        expected = json.loads((Path(BASELINE_DIFF) / "adam-detector" / "report_broad_defs.json").read_text())
        broad = analyze_stages.broad_scan(self.deps, self.stages)
        self.assertEqual([b["decl"] for b in broad], [b["decl"] for b in expected])


@unittest.skipUnless(BASELINE_DIFF and BOOK_TXT, "set CF_BASELINE_DIFF and CF_BOOK_TXT")
class CommandEndToEndTest(unittest.TestCase):
    """`cf detect-proxies` over a temp workspace built from the baseline.

    The workspace holds BOTH Tao editions and a companion audited against both,
    so `--source` is what decides which items.json the detector reads -- and the
    rows, the corpus filenames and the gate all have to agree with it.
    """

    @classmethod
    def setUpClass(cls):
        from cfcmd import detect_proxies  # imported late: needs scripts/ on sys.path
        from cflib import log

        log.set_level(0)  # the command's own progress lines are not test output
        cls.mod = detect_proxies
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "companions" / "ours").mkdir(parents=True)
        # An empty stand-in repo: the detector never reads the Lean here, but
        # workspace.yaml validation refuses a companion whose repo is not a dir.
        (root / "repos" / "ours").mkdir(parents=True)
        (root / "companions" / "ours" / "statements.jsonl").write_text(
            (Path(BASELINE_DIFF) / "ours.jsonl").read_text())
        book_text = Path(BOOK_TXT).read_text(errors="ignore")
        items = _items_from_book(book_text)
        for sid in (PUBLISHED_ID, DRAFT_ID):
            sdir = root / "sources" / sid
            sdir.mkdir(parents=True)
            sdir.joinpath("items.json").write_text(
                json.dumps({"source_id": sid, "offset": 16, "items": items}))
            # the corpus emission gates against the `mechanical` preset, and the
            # gate fails closed without the pinned source text
            sdir.joinpath("text.txt").write_text(book_text)
        (root / "workspace.yaml").write_text(
            "version: 2\n"
            "workspace:\n  name: tao\n"
            "companions:\n"
            f"  ours:\n    repo: {root / 'repos' / 'ours'}\n"
            "    adapter: docstring_tags\n"
            f"    sources: [{PUBLISHED_ID}, {DRAFT_ID}]\n"
            f"    anchor_source: {PUBLISHED_ID}\n"
            "sources:\n"
            f"  {PUBLISHED_ID}:\n"
            f'    sha256: "{PUBLISHED_SHA}"\n'
            "    title: An Introduction to Measure Theory\n"
            '    edition: "AMS GSM 126 (published pagination)"\n'
            "    offset: 16\n    chapters: [1]\n    role: audited\n"
            f"  {DRAFT_ID}:\n"
            f'    sha256: "{DRAFT_SHA}"\n'
            "    title: An Introduction to Measure Theory\n"
            '    edition: "2011-01-24 draft"\n'
            "    offset: 16\n    chapters: [1]\n    role: reference\n")
        (root / "companions" / "ours" / "state.json").write_text(json.dumps({"name": "ours"}))
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        from cflib import log

        log.set_level(1)
        cls.tmp.cleanup()

    def _run(self, *extra):
        import argparse

        ap = argparse.ArgumentParser()
        ap.add_argument("--workspace")
        sub = ap.add_subparsers()
        self.mod.register(sub)
        argv = ["--workspace", str(self.root), "detect-proxies", "ours", *extra]
        if "--source" not in extra:
            argv += ["--source", PUBLISHED_ID]
        args = ap.parse_args(argv)
        return args.func(args)

    def coverage_json(self, source_id: str = PUBLISHED_ID) -> Path:
        """pairs/ is per source: the detector writes under the one it read."""
        return self.root / "pairs" / source_id / f"def-coverage.ours.json"

    def test_writes_coverage_findings_and_state(self):
        self.assertEqual(self._run(), 0)
        cov = json.loads(self.coverage_json().read_text())
        self.assertEqual((len(cov["stated"]), len(cov["bridge_only"]), len(cov["missing"])), (32, 4, 0))
        self.assertEqual(cov["_meta"]["source_id"], PUBLISHED_ID)
        self.assertEqual(cov["_meta"]["source_sha"], PUBLISHED_SHA)
        self.assertNotIn("book_sha", cov["_meta"])

        rows = _read_jsonl(self.root / "detect" / "ours" / "findings.cheap.jsonl")
        self.assertIn("_meta", rows[0])
        self.assertEqual(rows[0]["_meta"]["status"], vocab.UNCHALLENGED)
        self.assertEqual(len(rows) - 1, 4)  # the four bridge-only Definitions
        for row in rows[1:]:
            self.assertEqual(row["source_id"], PUBLISHED_ID)
            self.assertEqual(row["source_sha"], PUBLISHED_SHA)

        state = json.loads((self.root / "companions" / "ours" / "state.json").read_text())
        self.assertEqual(state["coverage"]["def_bridge_only"], 4)
        self.assertEqual(state["detect"]["cheap"]["stated"], 32)

    def test_the_emitted_corpus_reaches_the_tally(self):
        """`cf report` reads reports/tally.*.json and nothing else.

        The documented order is tally, detect-proxies, route, report, so the
        tally always predated the detector's rows: a companion whose only
        findings came from the detector printed `TODO(tally for <name>)` in the
        handoff while its corpus sat on disk.  `cf harvest` and
        `cf import-corpus` both re-tally what they land; so does this now.
        """
        self.assertEqual(self._run("--run-id", "d-tally"), 0)
        tally = self.root / "reports" / "tally.d-tally.json"
        self.assertTrue(tally.exists(), "the emitted corpus was never tallied")
        data = json.loads(tally.read_text())
        self.assertIn("d-tally", data["runs"])
        entry = data["repos"]["ours"]["sources"][PUBLISHED_ID]
        self.assertEqual(entry["rows"], 4)      # the four bridge-only Definitions
        self.assertEqual(entry["by_kind"], {vocab.COVERAGE: 4})

    def test_no_tally_skips_it(self):
        self.assertEqual(self._run("--run-id", "d-quiet", "--no-tally"), 0)
        self.assertTrue((self.root / "corpus" / "d-quiet").is_dir())
        self.assertFalse((self.root / "reports" / "tally.d-quiet.json").exists())

    def test_the_other_source_gets_its_own_tree_and_its_own_stamp(self):
        """One companion, two editions: two detector runs that never collide."""
        self.assertEqual(self._run("--source", PUBLISHED_ID, "--run-id", "d-pub"), 0)
        self.assertEqual(self._run("--source", DRAFT_ID, "--run-id", "d-dft"), 0)
        self.assertTrue(self.coverage_json(PUBLISHED_ID).exists())
        self.assertTrue(self.coverage_json(DRAFT_ID).exists())
        self.assertEqual(
            json.loads(self.coverage_json(DRAFT_ID).read_text())["_meta"]["source_sha"],
            DRAFT_SHA)
        pub = {p.name for p in (self.root / "corpus" / "d-pub").glob("*.json")}
        dft = {p.name for p in (self.root / "corpus" / "d-dft").glob("*.json")}
        # the corpus filenames are qualified, so the same Definition in the two
        # editions is two files even inside one corpus directory
        self.assertTrue(pub - {"RECEIPT.json"})
        self.assertEqual((pub & dft) - {"RECEIPT.json"}, set())
        for name in pub - {"RECEIPT.json"}:
            self.assertTrue(name.startswith(PUBLISHED_ID), name)

    def test_is_idempotent(self):
        self._run()
        first = self.coverage_json().read_text()
        firstf = (self.root / "detect" / "ours" / "findings.cheap.jsonl").read_text()
        corpus = self.root / "corpus" / "detect-ours-cheap"
        firstc = {p.name: p.read_text() for p in sorted(corpus.glob("*.json"))
                  if p.name != "RECEIPT.json"}
        self._run()
        self.assertEqual(first, self.coverage_json().read_text())
        self.assertEqual(firstf, (self.root / "detect" / "ours" / "findings.cheap.jsonl").read_text())
        self.assertEqual(firstc, {p.name: p.read_text() for p in sorted(corpus.glob("*.json"))
                                  if p.name != "RECEIPT.json"})

    def test_the_rows_join_the_corpus_so_a_tally_can_see_them(self):
        """Stage 8's own gate: a hit only counts once it joins the corpus."""
        from harvest import tally as tally_mod

        self.assertEqual(self._run(), 0)
        corpus = self.root / "corpus" / "detect-ours-cheap"
        docs = sorted(p for p in corpus.glob("*.json") if p.name != "RECEIPT.json")
        self.assertEqual(len(docs), 4)          # the four bridge-only Definitions
        doc = json.loads(docs[0].read_text())
        self.assertEqual(doc["schema_version"], 2)
        self.assertEqual(doc["run_id"], "detect-ours-cheap")
        self.assertEqual(doc["source_id"], PUBLISHED_ID)
        self.assertEqual(doc["source_sha"], PUBLISHED_SHA)
        self.assertTrue(doc["book_located"])
        self.assertIsNone(doc["book_passage_quote"])
        self.assertEqual(len(doc["disagreements"]), 1)
        self.assertEqual(doc["disagreements"][0]["kind"], vocab.COVERAGE)
        self.assertEqual(doc["disagreements"][0]["repos_involved"], ["ours"])

        receipt = json.loads((corpus / "RECEIPT.json").read_text())
        self.assertTrue(receipt["complete"])
        self.assertTrue(receipt["gate"]["ok"], receipt["gate"]["problems"])
        self.assertEqual(receipt["expected"]["count"], 4)

        result = tally_mod.tally({"detect-ours-cheap": corpus})
        self.assertEqual(result["runs"]["detect-ours-cheap"]["files"], 4)
        self.assertGreaterEqual(result["rows"], 4)
        # the tally groups by (source_id, repo), and the detector stamped one
        self.assertEqual(result["sources"], {PUBLISHED_ID: PUBLISHED_SHA})
        self.assertEqual(list(result["groups"]),
                         [tally_mod.group_key({"source_id": PUBLISHED_ID, "repo": "ours"})])

    def test_every_emitted_document_passes_the_mechanical_gate(self):
        from gate import verify_findings as vf
        from plan import presets as presets_mod

        self._run()
        corpus = self.root / "corpus" / "detect-ours-cheap"
        cfg = vf.GateConfig(presets_mod.gate_config(
            presets_mod.get("mechanical"), run_id="detect-ours-cheap",
            sources={PUBLISHED_ID: {
                "text": str(self.root / "sources" / PUBLISHED_ID / "text.txt"),
                "sha256": PUBLISHED_SHA, "page_offset": 16}},
            repos=["ours"]))
        cfg.findings_dir = "."
        books, errs = vf.load_sources(cfg)
        self.assertEqual(errs, [])
        problems = []
        for path in sorted(corpus.glob("*.json")):
            if path.name == "RECEIPT.json":
                continue
            problems += vf.validate_doc(json.loads(path.read_text()), path.name, cfg, books)
        self.assertEqual(problems, [])

    def test_no_emit_corpus_writes_nothing_under_corpus(self):
        # a distinct --run-id, because the class shares one workspace directory
        self._run("--no-emit-corpus", "--run-id", "detect-quiet")
        self.assertFalse(list((self.root / "corpus").glob("detect-quiet/*.json")))
        self._run("--run-id", "detect-loud")
        self.assertTrue(list((self.root / "corpus").glob("detect-loud/*.json")))

    def test_lean_tier_refuses_without_a_dump_and_never_runs_lake(self):
        with self.assertRaises(Exception) as ctx:
            self._run("--lean")
        hint = getattr(ctx.exception, "hint", "") or ""
        self.assertIn("dump_deps.sh", hint)
        self.assertIn("never builds", hint)

    def test_unclassified_modules_fail_the_run_unless_allowed(self):
        """Stage 8's gate: `unclassified_modules` must be empty (exit 1)."""
        deps = self.root / "detect" / "ours" / "deps.json"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.write_text(json.dumps({
            "repo_constants": [{"name": "Repo.d", "module": "Repo.A", "kind": "def",
                                "type_deps": [], "value_deps": ["Lib.mystery"]}],
            "referenced": [{"name": "Lib.mystery", "module": "Lib.Unknown", "kind": "def"}],
        }))
        table = self.root / "stages.json"
        table.write_text(json.dumps({
            "source_id": "toy", "chapter": 1, "default_stage": 0,
            "module_stages": [["Lib.Unknown", None]], "mathlib_objects": {}, "native_tokens": [],
        }))
        statements = self.root / "companions" / "ours" / "tiny.jsonl"
        statements.write_text(json.dumps(
            {"item": "Definition 1.1.1", "decl": "Repo.d", "statement": "def Repo.d : Prop",
             "repo": "ours"}) + "\n")
        common = ["--lean", "--deps", str(deps), "--stages", str(table),
                  "--statements", str(statements)]
        self.assertEqual(self._run(*common), 1)
        self.assertEqual(self._run(*common, "--allow-unclassified"), 0)
        report = json.loads((self.root / "detect" / "ours" / "report_keydefs.json").read_text())
        self.assertEqual(report["unclassified_modules"], {"Lib.Unknown": 1})
        deps.unlink()  # leave the workspace as the other tests expect it

    def test_render_dump_deps_writes_outside_the_companion(self):
        self.assertEqual(self._run("--render-dump-deps"), 0)
        rendered = self.root / "detect" / "ours" / "DumpDeps.lean"
        self.assertTrue(rendered.exists())
        self.assertNotIn("{{", rendered.read_text())


if __name__ == "__main__":
    unittest.main()
