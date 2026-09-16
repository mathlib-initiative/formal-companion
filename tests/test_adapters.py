"""Tests for the statement-table adapters (Stage 2).

Two layers:

* **unit tests** over synthetic Lean text and synthetic rows -- always run;
* **regression tests** against the real baseline -- skipped unless the
  environment names it:

      CF_TML           /Users/jack/Desktop/tao-measure-lean
      CF_MT            /Users/jack/Desktop/measure_theory
      CF_BASELINE_DIFF /Users/jack/Desktop/tao-differential   (ours.jsonl, adam.jsonl)
      CF_TEST_TMP      where the end-to-end test may create a scratch campaign
                       (default: the system temp directory)

The regression numbers come from the tables the audit actually ran on:
``ours.jsonl`` has 1,157 rows over 763 raw tags, ``adam.jsonl`` 562 rows over
178 items.  Both repos have moved on since, so the assertions are "no item the
baseline knew about has been lost", not equality -- with the four In-text page
anchors tao-measure-lean re-anchored afterwards listed explicitly.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from adapters import api, base, itemtag, leanparse  # noqa: E402
from adapters.api import from_config, load  # noqa: E402

BOOK_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
KINDS = ("Definition", "Lemma", "Theorem", "Proposition", "Corollary",
         "Exercise", "Example", "Remark", "Strategy", "Claim", "In-text")

TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "adapters"

#: ours.jsonl was built before tao-measure-lean re-anchored these four In-text
#: items (each moved one printed page when the companion was re-anchored to the
#: published edition).  They are the only items the baseline has that HEAD does
#: not; add to this set only with the diff that justifies it.
BASELINE_ANCHOR_DRIFT = {"In-text p.156", "In-text p.166", "In-text p.195", "In-text p.197"}

#: The one raw tag in ours.jsonl that the strict item grammar refuses: the
#: whole tag is parenthesised, so there is no item number to key on.
BASELINE_JUNK_TAGS = {"Corollary (Lemma 1.2.5 + Exercise 1.2.4)"}


def env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.exists() else None


SAMPLE = '''\
import Mathlib

/-!
# A section header mentioning `theorem` and examples in prose.
-/

namespace Demo

open Set

/-- `Definition 1.1.1` (intervals).  An *interval* is a subset of `ℝ`. -/
def IsInterval (I : Set ℝ) : Prop :=
  ∃ a b : ℝ, a ≤ b

/-- `Exercise 1.1.3(ii)` (book p. 12).  Left to the reader. -/
theorem foo_bar {d : ℕ} (hd : 0 < d) : True := by
  sorry

/-- `Lemma 1.2.5` (Finite additivity).  Disjointness alone is not enough
(book p. 23): entangled sets fail. -/
@[simp]
private noncomputable def helper (x : ℝ) : ℝ :=
  x + 1

/-- `Exercise 1.2.17` (Caratheodory criterion).  (4) is the condition of
§1.7 (previewed on book p. 20).  Here `0 < d` is essential. -/
theorem crossref_case : True := by
  sorry -- TODO(autoform)

/-- `Example 1.1.9`.  An anonymous worked example. -/
example : (1 : ℕ) = 1 := rfl

/-- Infrastructure with no book tag at all. -/
theorem untagged : True := trivial

end Demo

namespace Demo.Inner

/-- `Remark 1.3.4`. -/
theorem nested : True := trivial

end Demo.Inner
'''


class LeanParserTest(unittest.TestCase):
    """One parser: prefixes, comment depth, namespaces, anonymous ids."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cf-parse-",
                                        dir=os.environ.get("CF_TEST_TMP") or None))
        cls.file = cls.tmp / "Sample.lean"
        cls.file.write_text(SAMPLE, encoding="utf-8")
        cls.decls = leanparse.parse_file(cls.file, rel="Sample.lean")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def by_name(self, name: str):
        for d in self.decls:
            if d.name == name:
                return d
        self.fail(f"{name} not parsed; got {[d.name for d in self.decls]}")

    def test_prose_keywords_are_not_declarations(self):
        # "`theorem` and examples in prose" sits inside a /-! -/ block.
        self.assertEqual(len(self.decls), 7, [d.name for d in self.decls])

    def test_attribute_and_modifier_prefixes(self):
        helper = self.by_name("helper")
        self.assertEqual(helper.kind, "def")
        self.assertTrue(helper.private)
        self.assertIn("noncomputable", helper.modifiers)

    def test_namespace_tracking(self):
        self.assertEqual(self.by_name("IsInterval").full_name, "Demo.IsInterval")
        self.assertEqual(self.by_name("nested").full_name, "Demo.Inner.nested")

    def test_anonymous_example_gets_a_synthetic_id(self):
        anon = [d for d in self.decls if d.anonymous]
        self.assertEqual(len(anon), 1)
        self.assertTrue(anon[0].full_name.startswith("example@Sample.lean:"))

    def test_docstring_attach_skips_attribute_lines(self):
        self.assertIn("Lemma 1.2.5", self.by_name("helper").doc or "")

    def test_signature_stops_at_assignment_or_by(self):
        self.assertEqual(leanparse.signature(self.by_name("IsInterval").body),
                         "def IsInterval (I : Set ℝ) : Prop")
        self.assertEqual(leanparse.signature(self.by_name("foo_bar").body),
                         "theorem foo_bar {d : ℕ} (hd : 0 < d) : True")

    def test_definition_bodies_are_captured_theorems_are_not(self):
        self.assertIsNotNone(self.by_name("helper").def_body)
        self.assertIn("x + 1", self.by_name("helper").def_body)
        self.assertIsNone(self.by_name("foo_bar").def_body)

    def test_strip_comments_handles_nesting(self):
        self.assertEqual(leanparse.strip_comments("a /- b /- c -/ d -/ e").strip(), "a  e")
        self.assertEqual(leanparse.strip_comments("keep -- drop\nnext").strip(), "keep \nnext".strip())


class TagAndAnchorTest(unittest.TestCase):
    """The tag grammar and the own-versus-cross-reference anchor rule."""

    def test_leading_backticked_tag_wins(self):
        hit = itemtag.find_tag("`Definition 1.1.1` (intervals).  See `Lemma 1.2.5`.")
        self.assertEqual(hit.tag, "Definition 1.1.1")
        self.assertEqual(hit.position, "leading")

    def test_inline_tag_is_the_fallback(self):
        hit = itemtag.find_tag("Every box is elementary (`In-text, book p. 5`).")
        self.assertEqual(hit.tag, "In-text, book p. 5")
        self.assertEqual(hit.position, "inline")

    def test_untagged_docstring(self):
        self.assertIsNone(itemtag.find_tag("Pure infrastructure, no book item."))

    def test_bare_leading_label_needs_require_backticks_false(self):
        doc = "Definition 1.3.8 (Unsigned measurable function) def Unsigned {d"
        self.assertIsNone(itemtag.find_tag(doc))
        hit = itemtag.find_tag(doc, require_backticks=False)
        self.assertEqual(hit.tag, "Definition 1.3.8 (Unsigned measurable function) def Unsigned {d")

    def test_page_inside_the_tag_is_own(self):
        doc = "`In-text, book p. 24` (subdivision observation)."
        hit = itemtag.find_page_anchor(doc, itemtag.find_tag(doc))
        self.assertEqual((hit.page, hit.source), (24, "own"))

    def test_page_in_the_following_clause_is_own(self):
        doc = "The distance between two sets (`Lemma 1.2.5`, book p. 23), taken in ℝ≥0∞."
        hit = itemtag.find_page_anchor(doc, itemtag.find_tag(doc))
        self.assertEqual((hit.page, hit.source), (23, "own"))

    def test_page_ranges_keep_the_first_page(self):
        doc = "`Lemma 1.6.22` (Vitali covering; book pp. 148-150)."
        hit = itemtag.find_page_anchor(doc, itemtag.find_tag(doc))
        self.assertEqual((hit.page, hit.last_page, hit.source), (148, 150, "own"))

    def test_cross_reference_page_is_refused(self):
        doc = ("`Exercise 1.2.17` (Caratheodory criterion, one direction).  (4) is the "
               "full condition of §1.7 (previewed on book p. 20).  Here 0 < d matters.")
        hit = itemtag.find_page_anchor(doc, itemtag.find_tag(doc))
        self.assertEqual((hit.page, hit.source), (None, "crossref"))

    def test_no_page_at_all(self):
        doc = "`Definition 1.1.1` (intervals).  An interval is a subset of ℝ."
        hit = itemtag.find_page_anchor(doc, itemtag.find_tag(doc))
        self.assertEqual((hit.page, hit.source), (None, "none"))


def valid_row(**overrides) -> dict:
    row = base.make_row(
        item_raw="Exercise 1.3.19", item="Exercise 1.3.19", sub="", item_kind="Exercise",
        decl="TaoMeasure.foo", kind="theorem", namespace="TaoMeasure",
        file="TaoMeasure/Section_1_3_3.lean", line=12,
        statement="theorem foo : True", body=None, sorry=True, sorry_class="exercise",
        page_anchor=66, page_anchor_raw="book p. 66", anchor_source="own",
        repo="ours", key=True, anonymous=False, private=False, granularity="item",
        book_sha=BOOK_SHA)
    row.update(overrides)
    return row


class RowSchemaTest(unittest.TestCase):
    """Schema v2: every field, every enum, and (item, sub, decl) uniqueness."""

    def test_a_good_row_validates(self):
        self.assertEqual(base.validate_row(valid_row(), kinds=KINDS), [])

    def test_missing_book_sha_is_a_problem(self):
        self.assertTrue(base.validate_row(valid_row(book_sha=None), kinds=KINDS))
        self.assertEqual(base.validate_row(valid_row(book_sha=None), kinds=KINDS,
                                           require_book_sha=False), [])

    def test_unknown_item_kind_is_a_problem(self):
        self.assertTrue(base.validate_row(valid_row(item_kind="Display"), kinds=KINDS))

    def test_anchor_source_and_page_must_agree(self):
        self.assertTrue(base.validate_row(valid_row(anchor_source="none"), kinds=KINDS))
        self.assertTrue(base.validate_row(
            valid_row(anchor_source="own", page_anchor=None), kinds=KINDS))

    def test_sorry_and_sorry_class_must_agree(self):
        self.assertTrue(base.validate_row(valid_row(sorry=False), kinds=KINDS))
        self.assertTrue(base.validate_row(valid_row(sorry_class="none"), kinds=KINDS))

    def test_bad_enum_and_types(self):
        for bad in (valid_row(sorry_class="todo"), valid_row(line=0),
                    valid_row(statement="  "), valid_row(decl="has space"),
                    valid_row(granularity="module")):
            self.assertTrue(base.validate_row(bad, kinds=KINDS), bad)

    def test_unknown_field_is_rejected(self):
        row = valid_row()
        row["surprise"] = 1
        self.assertTrue(base.validate_row(row, kinds=KINDS))

    def test_duplicate_rows_are_reported(self):
        problems = base.validate_rows([valid_row(), valid_row()], kinds=KINDS)
        self.assertTrue(any("duplicate" in p for p in problems))

    def test_row_fields_cover_the_core_required_set(self):
        from cflib.adapters_api import ROW_REQUIRED_FIELDS
        self.assertTrue(set(ROW_REQUIRED_FIELDS) <= set(base.ROW_FIELDS))


class AdapterLoadingTest(unittest.TestCase):
    """adapter.yaml -> Adapter, and the errors a bad config gives."""

    def test_shipped_templates_load(self):
        for path, kind in ((TEMPLATES / "docstring_tags.tao-measure-lean.yaml", "docstring_tags"),
                           (TEMPLATES / "attribution_table.measure_theory.yaml", "attribution_table")):
            with self.subTest(path.name):
                adapter = load(path)
                self.assertEqual(adapter.kind, kind)

    def test_unknown_kind_is_a_readable_error(self):
        with self.assertRaises(api.AdapterError):
            from_config({"adapter": "telepathy", "name": "x", "repo": "/tmp"})

    def test_missing_kind_is_a_readable_error(self):
        with self.assertRaises(api.AdapterError):
            from_config({"name": "x", "repo": "/tmp"})

    def test_registry_knows_the_shipped_kinds(self):
        self.assertLessEqual({"docstring_tags", "attribution_table"}, set(api.registry()))


class DocstringTagsSyntheticTest(unittest.TestCase):
    """End-to-end over the synthetic file: rows, ids, anchors, sorry classes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cf-docstring-",
                                        dir=os.environ.get("CF_TEST_TMP") or None))
        (cls.tmp / "Src").mkdir()
        (cls.tmp / "Src" / "Sample.lean").write_text(SAMPLE, encoding="utf-8")
        cls.adapter = from_config({
            "adapter": "docstring_tags", "name": "demo", "repo": str(cls.tmp),
            "glob": "Src/*.lean", "item_kinds": list(KINDS),
            "sorry": {"exercise_sorry": "permanent", "gap_marker": "sorry -- TODO"},
        }, source="<synthetic>")
        cls.rows = list(cls.adapter.extract(book_sha=BOOK_SHA))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def row(self, item: str) -> dict:
        for r in self.rows:
            if r["item"] == item:
                return r
        self.fail(f"{item} not extracted; got {[r['item'] for r in self.rows]}")

    def test_every_row_validates(self):
        self.assertEqual(base.validate_rows(self.rows, kinds=KINDS), [])

    def test_untagged_declarations_are_skipped(self):
        self.assertEqual(len(self.rows), 6, [r["decl"] for r in self.rows])

    def test_sub_parts_are_parsed_out(self):
        row = self.row("Exercise 1.1.3")
        self.assertEqual(row["sub"], "(ii)")
        self.assertEqual(row["item_raw"], "Exercise 1.1.3(ii)")
        self.assertEqual((row["page_anchor"], row["anchor_source"]), (12, "own"))

    def test_sorry_classification(self):
        self.assertEqual(self.row("Exercise 1.1.3")["sorry_class"], "exercise")
        self.assertEqual(self.row("Exercise 1.2.17")["sorry_class"], "gap")
        self.assertEqual(self.row("Definition 1.1.1")["sorry_class"], "none")

    def test_cross_reference_anchor_is_not_taken(self):
        row = self.row("Exercise 1.2.17")
        self.assertIsNone(row["page_anchor"])
        self.assertEqual(row["anchor_source"], "crossref")

    def test_anonymous_example_is_attributable(self):
        row = self.row("Example 1.1.9")
        self.assertTrue(row["anonymous"])
        self.assertTrue(row["decl"].startswith("example@Src/Sample.lean:"))

    def test_definition_body_is_kept(self):
        self.assertIn("x + 1", self.row("Lemma 1.2.5")["body"])
        self.assertIsNone(self.row("Exercise 1.1.3")["body"])

    def test_dry_run_reports_the_stage_0_gate_counts(self):
        report = self.adapter.dry_run()
        for key in ("declarations", "tagged", "items", "untagged", "private", "rejects"):
            self.assertIn(key, report)
        self.assertEqual(report["untagged"], 1)
        self.assertEqual(report["private"], 1)

    def test_key_changes_when_a_source_file_changes(self):
        before = self.adapter.key()
        path = self.tmp / "Src" / "Sample.lean"
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original + "\n-- a comment\n", encoding="utf-8")
            self.assertNotEqual(before, self.adapter.key())
        finally:
            path.write_text(original, encoding="utf-8")
        self.assertEqual(before, self.adapter.key())

    def test_extraction_is_deterministic(self):
        again = list(self.adapter.extract(book_sha=BOOK_SHA))
        self.assertEqual(json.dumps(self.rows, sort_keys=True),
                         json.dumps(again, sort_keys=True))


@unittest.skipUnless(env_path("CF_TML"), "set CF_TML to the tao-measure-lean checkout")
class DocstringTagsBaselineTest(unittest.TestCase):
    """Regression against ours.jsonl (1,157 rows, 763 raw tags)."""

    @classmethod
    def setUpClass(cls):
        cls.repo = env_path("CF_TML")
        cls.adapter = from_config({
            "adapter": "docstring_tags", "name": "ours", "repo": str(cls.repo),
            "glob": "TaoMeasure/Section_*.lean", "item_kinds": list(KINDS),
            "sorry": {"exercise_sorry": "permanent", "gap_marker": "sorry -- TODO"},
        }, source="<CF_TML>")
        cls.rows = list(cls.adapter.extract(book_sha=BOOK_SHA))

    def test_at_least_the_baseline_row_count(self):
        self.assertGreaterEqual(len(self.rows), 1157, "ours.jsonl had 1,157 rows")

    def test_every_row_validates(self):
        self.assertEqual(base.validate_rows(self.rows, kinds=KINDS), [])

    def test_fully_qualified_names_and_no_duplicates(self):
        decls = [r["decl"] for r in self.rows]
        self.assertTrue(all(d.startswith("TaoMeasure.") or d.startswith("example@")
                            for d in decls), "namespace tracking lost a prefix")
        self.assertEqual(len(decls), len(set(decls)))

    def test_anonymous_examples_are_extracted(self):
        self.assertTrue(any(r["anonymous"] for r in self.rows))

    def test_sorries_are_classified_not_reported(self):
        self.assertTrue(all(r["sorry_class"] in ("exercise", "gap")
                            for r in self.rows if r["sorry"]))
        self.assertTrue(any(r["sorry_class"] == "gap" for r in self.rows))

    @unittest.skipUnless(env_path("CF_BASELINE_DIFF"), "set CF_BASELINE_DIFF for ours.jsonl")
    def test_item_set_covers_the_baseline(self):
        from cflib import items as cf_items
        path = env_path("CF_BASELINE_DIFF") / "ours.jsonl"
        raws = {json.loads(line)["item"] for line in path.read_text().splitlines() if line.strip()}
        numbered = tuple(k for k in KINDS if k != "In-text")
        baseline, rejected = set(), set()
        for raw in raws:
            item, reject = cf_items.try_parse(raw, numbered)
            (baseline.add(item.id) if item is not None else rejected.add(raw))
        self.assertEqual(rejected, BASELINE_JUNK_TAGS,
                         "the strict grammar's reject list changed")
        missing = baseline - {r["item"] for r in self.rows}
        self.assertLessEqual(
            missing, BASELINE_ANCHOR_DRIFT,
            f"items lost since the baseline: {sorted(missing - BASELINE_ANCHOR_DRIFT)}")


@unittest.skipUnless(env_path("CF_MT"), "set CF_MT to the measure_theory checkout")
class AttributionTableBaselineTest(unittest.TestCase):
    """Regression against adam.jsonl (562 rows, 178 items)."""

    @classmethod
    def setUpClass(cls):
        cls.repo = env_path("CF_MT")
        cls.adapter = from_config({
            "adapter": "attribution_table", "name": "adam", "repo": str(cls.repo),
            "page_offset": 16, "item_kinds": list(KINDS),
        }, source="<CF_MT>")
        cls.rows = list(cls.adapter.extract(book_sha=BOOK_SHA))

    def test_row_count(self):
        self.assertEqual(len(self.rows), 562)

    def test_every_row_validates(self):
        self.assertEqual(base.validate_rows(self.rows, kinds=KINDS), [])

    def test_key_includes_the_ledger_and_refinement_shas(self):
        inputs = self.adapter.key_inputs()
        for field in ("ledger_sha", "target_refinement_sha", "snippets_sha", "geometry_shas"):
            self.assertIn(field, inputs)
        self.assertFalse(inputs["ledger_stale"],
                         "target-refinement.json was curated against another ledger")

    def test_page_anchors_are_printed_pages(self):
        pages = [r["page_anchor"] for r in self.rows]
        self.assertTrue(all(isinstance(p, int) and 0 < p < 400 for p in pages))
        self.assertTrue(all(r["anchor_source"] == "own" for r in self.rows))

    @unittest.skipUnless(env_path("CF_BASELINE_DIFF"), "set CF_BASELINE_DIFF for adam.jsonl")
    def test_item_decl_pairs_match_the_baseline(self):
        path = env_path("CF_BASELINE_DIFF") / "adam.jsonl"
        old = {(json.loads(l)["item"], json.loads(l)["decl"])
               for l in path.read_text().splitlines() if l.strip()}
        new = {(r["item"] + r["sub"], r["decl"]) for r in self.rows}
        self.assertEqual(new, old)


@unittest.skipUnless(env_path("CF_TML"), "set CF_TML to the tao-measure-lean checkout")
class ExtractCommandTest(unittest.TestCase):
    """`cf extract` writes, is idempotent, and is byte-stable."""

    @classmethod
    def setUpClass(cls):
        from cflib import log
        cls._log_level = log.level()
        log.set_level(0)                     # the command's own output is not the test's
        cls.tmp = Path(tempfile.mkdtemp(prefix="cf-extract-",
                                        dir=os.environ.get("CF_TEST_TMP") or None))
        (cls.tmp / "campaign.yaml").write_text(
            "version: 1\n"
            "book:\n"
            "  id: tao-measure\n"
            f"  sha256: {BOOK_SHA}\n"
            "  offset: 16\n"
            "  kinds: [Definition, Lemma, Theorem, Proposition, Corollary, Exercise,"
            " Example, Remark, Strategy, Claim]\n"
            "companions:\n"
            "  - name: ours\n"
            f"    repo: {env_path('CF_TML')}\n"
            "    adapter: docstring_tags\n"
            "hosts:\n"
            "  allowlist: []\n", encoding="utf-8")
        comp = cls.tmp / "companions" / "ours"
        comp.mkdir(parents=True)
        (comp / "adapter.yaml").write_text(
            "name: ours\n"
            f"repo: {env_path('CF_TML')}\n"
            "adapter: docstring_tags\n"
            "granularity: item\n"
            'glob: "TaoMeasure/Section_*.lean"\n'
            "exclude: []\n"
            "tag:\n"
            "  position: docstring_start\n"
            "sorry:\n"
            "  exercise_sorry: permanent\n"
            '  gap_marker: "sorry -- TODO"\n', encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        from cflib import log
        log.set_level(cls._log_level)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, **flags) -> int:
        import argparse

        from cfcmd import extract as extract_cmd
        args = argparse.Namespace(campaign=str(self.tmp), companions=None, repo=None,
                                  force=False, dry_run=False, allow_missing_book_sha=False)
        for k, v in flags.items():
            setattr(args, k, v)
        return extract_cmd.run(args)

    def test_writes_then_no_ops_then_reproduces_bytes(self):
        out = self.tmp / "companions" / "ours" / "statements.jsonl"
        self.assertEqual(self._run(), 0)
        first = out.read_bytes()
        self.assertGreaterEqual(len(first.splitlines()), 1157)

        state = json.loads((self.tmp / "companions" / "ours" / "state.json").read_text())
        self.assertEqual(state["extract"]["rows"], len(first.splitlines()))
        self.assertTrue(state["extract"]["key"].startswith("sha256:"))
        self.assertEqual(state["book_sha"], BOOK_SHA)

        mtime = out.stat().st_mtime_ns
        self.assertEqual(self._run(), 0)          # unchanged key: a no-op
        self.assertEqual(out.stat().st_mtime_ns, mtime)

        self.assertEqual(self._run(force=True), 0)
        self.assertEqual(out.read_bytes(), first)  # byte-stable

    def test_every_written_row_carries_the_book_sha(self):
        self._run()
        out = self.tmp / "companions" / "ours" / "statements.jsonl"
        rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        self.assertTrue(all(r["book_sha"] == BOOK_SHA for r in rows))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
