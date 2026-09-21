"""Tests for the statement-table adapters (Stage 2).

Two layers:

* **unit tests** over synthetic Lean text and synthetic rows -- always run;
* **regression tests** against the real baseline -- skipped unless the
  environment names it:

      CF_TML           a docstring-tag companion checkout
      CF_MT            an attribution-table companion checkout
      CF_BASELINE_DIFF the v1 differential directory (ours.jsonl, adam.jsonl)
      CF_TEST_TMP      where the end-to-end test may create a scratch workspace
                       (default: the system temp directory)

The regression numbers come from the tables the audit actually ran on:
``ours.jsonl`` has 1,157 rows over 763 raw tags, ``adam.jsonl`` 562 rows over
178 items.  Both repos have moved on since, so the assertions are "no item the
baseline knew about has been lost", not equality -- with the four In-text page
anchors tao-measure-lean re-anchored afterwards listed explicitly.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

# The first import, before anything reads HOME: it pins HOME at a temporary
# directory for `unittest discover`, which loads no conftest.py.  The second
# spelling is for `python3 -m unittest tests.<module>`.  See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from adapters import api, base, itemtag, leanparse  # noqa: E402
from adapters.api import from_config, load  # noqa: E402

#: The published GSM 126 edition: the bytes both target companions were read
#: against.  Rows carry the PAIR (source_id, source_sha) -- the id so an item is
#: namespaced, the sha so a row can never be compared against another edition.
SOURCE_ID = "tao-measure-gsm126-published"
SOURCE_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
#: the 2011 draft tao-measure-lean bundles but does not anchor to
DRAFT_ID = "tao-measure-2011-draft"
DRAFT_SHA = "62d9565841b8593a7a3d12b3cbcea6ce8d3d3ae2f83b60ebf024d59714c0c6e8"
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

/-- `Definition 1.1.1` (widgets).  A *widget* is a gadget built over `ℝ`. -/
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
§1.7 (it is sketched early, on book p. 20).  The bound `0 < d` matters. -/
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
        hit = itemtag.find_tag("`Definition 1.1.1` (widgets).  See `Lemma 1.2.5`.")
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
        doc = "How far apart two widgets sit (`Lemma 1.2.5`, book p. 23), valued in ℝ≥0∞."
        hit = itemtag.find_page_anchor(doc, itemtag.find_tag(doc))
        self.assertEqual((hit.page, hit.source), (23, "own"))

    def test_page_ranges_keep_the_first_page(self):
        doc = "`Lemma 1.6.22` (Vitali covering; book pp. 148-150)."
        hit = itemtag.find_page_anchor(doc, itemtag.find_tag(doc))
        self.assertEqual((hit.page, hit.last_page, hit.source), (148, 150, "own"))

    def test_cross_reference_page_is_refused(self):
        doc = ("`Exercise 1.2.17` (Caratheodory criterion, one direction).  (4) is the "
               "full condition of §1.7 (it is sketched early, on book p. 20).  The bound matters.")
        hit = itemtag.find_page_anchor(doc, itemtag.find_tag(doc))
        self.assertEqual((hit.page, hit.source), (None, "crossref"))

    def test_no_page_at_all(self):
        doc = "`Definition 1.1.1` (widgets).  A widget is a gadget built over ℝ."
        hit = itemtag.find_page_anchor(doc, itemtag.find_tag(doc))
        self.assertEqual((hit.page, hit.source), (None, "none"))


def valid_row(**overrides) -> dict:
    row = base.make_row(
        item_raw="Exercise 1.3.19", item="Exercise 1.3.19", sub="", item_kind="Exercise",
        source_id=SOURCE_ID,
        decl="TaoMeasure.foo", kind="theorem", namespace="TaoMeasure",
        file="TaoMeasure/Section_1_3_3.lean", line=12,
        statement="theorem foo : True", body=None, sorry=True, sorry_class="exercise",
        page_anchor=66, page_anchor_raw="book p. 66", anchor_source="own",
        repo="ours", key=True, anonymous=False, private=False, granularity="item",
        source_sha=SOURCE_SHA)
    row.update(overrides)
    return row


class RowSchemaTest(unittest.TestCase):
    """Schema v2: every field, every enum, and per-source uniqueness."""

    def test_a_good_row_validates(self):
        self.assertEqual(base.validate_row(valid_row(), kinds=KINDS), [])

    def test_missing_source_sha_is_a_problem(self):
        self.assertTrue(base.validate_row(valid_row(source_sha=None), kinds=KINDS))
        self.assertEqual(base.validate_row(valid_row(source_sha=None), kinds=KINDS,
                                           require_source_sha=False), [])

    def test_a_row_without_a_source_id_is_a_problem(self):
        # An item is the pair (source_id, item): half of it is not an item.
        for bad in (None, "", 7):
            self.assertTrue(base.validate_row(valid_row(source_id=bad), kinds=KINDS), bad)

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

    def test_the_same_item_in_two_sources_is_not_a_duplicate(self):
        # One declaration may legitimately formalise `Exercise 1.3.19` of two
        # editions; calling that a duplicate would refuse a table the model
        # exists to allow.
        rows = [valid_row(), valid_row(source_id=DRAFT_ID, source_sha=DRAFT_SHA)]
        self.assertEqual(base.validate_rows(rows, kinds=KINDS), [])

    def test_rows_sort_by_source_first(self):
        rows = sorted([valid_row(source_id="z-source"), valid_row(source_id="a-source")],
                      key=base.row_sort_key)
        self.assertEqual([r["source_id"] for r in rows], ["a-source", "z-source"])

    def test_row_fields_cover_the_core_required_set(self):
        from cflib.adapters_api import ROW_REQUIRED_FIELDS
        self.assertTrue(set(ROW_REQUIRED_FIELDS) <= set(base.ROW_FIELDS))
        self.assertIn("source_id", base.ROW_FIELDS)
        self.assertIn("source_sha", base.ROW_FIELDS)
        self.assertNotIn("book_sha", base.ROW_FIELDS)


class AdapterLoadingTest(unittest.TestCase):
    """adapter.yaml -> Adapter, and the errors a bad config gives."""

    def test_shipped_templates_load(self):
        """Both examples parse and name their kind -- on any machine.

        `repo:` in a shipped template is a placeholder, so the config is loaded
        and then pointed at a scratch repo carrying the exports the
        attribution-table adapter validates.  With the template naming one
        developer's absolute path, this test passed only on that machine.
        """
        from adapters import yamlio
        parent = Path(tempfile.mkdtemp(prefix="cf-tmpl-",
                                       dir=os.environ.get("CF_TEST_TMP") or None))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        repo = parent / "companion"
        exports = repo / "pdf-overlay" / "data"
        exports.mkdir(parents=True)
        for name in ("target-refinement.json", "declaration-snippets.json"):
            (exports / name).write_text("{}", encoding="utf-8")
        cfg_path = parent / "adapter.yaml"
        for path, kind in ((TEMPLATES / "docstring_tags.tao-measure-lean.yaml", "docstring_tags"),
                           (TEMPLATES / "attribution_table.measure_theory.yaml", "attribution_table")):
            with self.subTest(path.name):
                config = yamlio.load_yaml(path)
                self.assertEqual(config["repo"], "<path to the companion repo>",
                                 "a shipped template names no real repo")
                # `repo:` is written RELATIVE here, because that is the second
                # thing `load` does and the only place it is exercised: a
                # relative repo resolves against the adapter.yaml's own parent.
                cfg_path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        f"repo: {config['repo']}", f"repo: {repo.name}"),
                    encoding="utf-8")
                adapter = load(cfg_path)
                self.assertEqual(adapter.kind, kind)
                self.assertEqual(Path(adapter.config["repo"]), repo.resolve())

    def test_a_missing_adapter_yaml_names_the_command_that_writes_one(self):
        """The refusal every command reaches through `cflib.adapters_api.load`."""
        missing = Path(tempfile.gettempdir()) / "cf-no-such-companion" / "adapter.yaml"
        with self.assertRaises(api.AdapterError) as caught:
            load(missing)
        self.assertIn("adapter config not found", str(caught.exception))
        self.assertIn("cf onboard", caught.exception.hint or "")

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
        cls.rows = list(cls.adapter.extract(sources={SOURCE_ID: SOURCE_SHA}))

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

    def test_every_row_carries_the_source_it_was_read_against(self):
        self.assertTrue(all(r["source_id"] == SOURCE_ID for r in self.rows))
        self.assertTrue(all(r["source_sha"] == SOURCE_SHA for r in self.rows))

    def test_the_single_bound_source_is_the_default_without_adapter_yaml_saying_so(self):
        # adapter.yaml here has no `sources:` block at all; one bound source is
        # unambiguous, and a guess is never needed.
        self.assertEqual(self.adapter.default_source({SOURCE_ID: SOURCE_SHA}), SOURCE_ID)

    def test_two_bound_sources_and_no_default_is_refused_not_guessed(self):
        with self.assertRaises(api.AdapterError):
            self.adapter.default_source({SOURCE_ID: SOURCE_SHA, DRAFT_ID: DRAFT_SHA})

    def test_a_tag_prefix_rule_sends_a_row_to_another_source(self):
        adapter = from_config({
            **self.adapter.config,
            "sources": {"default": SOURCE_ID,
                        "map": [{"source": DRAFT_ID, "tag_prefix": "Exercise"}]},
        }, source="<synthetic-map>")
        rows = list(adapter.extract(sources={SOURCE_ID: SOURCE_SHA, DRAFT_ID: DRAFT_SHA}))
        by_item = {r["item"]: r for r in rows}
        self.assertEqual(by_item["Exercise 1.1.3"]["source_id"], DRAFT_ID)
        self.assertEqual(by_item["Exercise 1.1.3"]["source_sha"], DRAFT_SHA)
        self.assertEqual(by_item["Definition 1.1.1"]["source_id"], SOURCE_ID)
        self.assertEqual(base.validate_rows(rows, kinds=KINDS), [])

    def test_the_bound_anchor_beats_a_stale_sources_default(self):
        # `cf onboard` re-decides the anchor edition against the rows it is about
        # to extract, so adapter.yaml's default can be the older answer.
        adapter = from_config({**self.adapter.config, "sources": {"default": "gone-source"}},
                              source="<synthetic-stale>")
        rows = list(adapter.extract(sources={SOURCE_ID: SOURCE_SHA}))
        self.assertTrue(all(r["source_id"] == SOURCE_ID for r in rows))

    def test_a_default_naming_an_unbound_source_is_an_error_naming_both(self):
        adapter = from_config({**self.adapter.config, "sources": {"default": "gone-source"}},
                              source="<synthetic-stale>")
        with self.assertRaises(api.AdapterError) as caught:
            adapter.default_source({SOURCE_ID: SOURCE_SHA, DRAFT_ID: DRAFT_SHA})
        self.assertIn("gone-source", str(caught.exception))
        self.assertIn(SOURCE_ID, str(caught.exception))

    def test_the_extract_key_changes_with_the_bound_edition(self):
        other = from_config({**self.adapter.config, "sources": {"default": DRAFT_ID}},
                            source="<synthetic-draft>")
        self.assertNotEqual(self.adapter.key(), other.key())

    def test_observed_kinds_are_read_from_the_tags_not_a_list(self):
        # Nothing here consults a keyword list: the kinds ARE the words the repo
        # wrote before a c.s.n number.
        kinds = self.adapter.observed_kinds(min_count=1)
        self.assertEqual(set(kinds),
                         {"Definition", "Exercise", "Lemma", "Example", "Remark"})
        self.assertEqual(self.adapter.observed_kinds(min_count=2), ("Exercise",))

    def test_dry_run_reports_the_stage_0_gate_counts(self):
        report = self.adapter.dry_run(sources={SOURCE_ID: SOURCE_SHA})
        for key in ("declarations", "tagged", "items", "untagged", "private", "rejects"):
            self.assertIn(key, report)
        self.assertEqual(report["untagged"], 1)
        self.assertEqual(report["private"], 1)

    def test_dry_run_reports_the_discovered_sources(self):
        report = self.adapter.dry_run(sources={SOURCE_ID: SOURCE_SHA})
        self.assertIn("sources", report)
        self.assertEqual(report["rows_by_source"], {SOURCE_ID: len(self.rows)})

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
        again = list(self.adapter.extract(sources={SOURCE_ID: SOURCE_SHA}))
        self.assertEqual(json.dumps(self.rows, sort_keys=True),
                         json.dumps(again, sort_keys=True))


class GeneratedMirrorTest(unittest.TestCase):
    """A second, TRACKED copy of the companion's own modules inside the glob.

    When two committed files define the same declaration, neither can be
    preferred mechanically -- a stub and the proof it mirrors look identical
    from here -- so the collision is REPORTED at the stage where narrowing
    `glob:` is still one edit, and the schema gate refuses the ambiguity.

    (An earlier version of this docstring blamed tao-measure-lean's
    `Blueprint/Gen/S_*.lean`.  That was wrong: those are Verso `#doc` manual
    files that define nothing and parse to zero declarations; the declaration
    names in them are markdown link labels.  The duplicates in that repo came
    from the git-IGNORED `scratch/`, which is what `IgnoredFilesTest` covers.
    The synthetic repo here is deliberately not a git checkout, so the ignore
    filter is a no-op and this test still measures the tracked-mirror case.)
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cf-mirror-",
                                        dir=os.environ.get("CF_TEST_TMP") or None))
        (cls.tmp / "Src").mkdir()
        (cls.tmp / "Gen").mkdir()
        (cls.tmp / "Src" / "Sample.lean").write_text(SAMPLE, encoding="utf-8")
        (cls.tmp / "Gen" / "Mirror.lean").write_text(SAMPLE, encoding="utf-8")
        cls.adapter = from_config({
            "adapter": "docstring_tags", "name": "demo", "repo": str(cls.tmp),
            "glob": "**/*.lean", "item_kinds": list(KINDS),
        }, source="<mirror>")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_collision_is_reported_with_both_files(self):
        report = self.adapter.dry_run(sources={SOURCE_ID: SOURCE_SHA})
        self.assertEqual(report["duplicate_declarations"], 5)   # the named declarations
        files = {tuple(d["files"]) for d in report["duplicates"]}
        self.assertEqual(files, {("Gen/Mirror.lean", "Src/Sample.lean")})

    def test_neither_copy_is_silently_dropped(self):
        rows = list(self.adapter.extract(sources={SOURCE_ID: SOURCE_SHA}))
        self.assertEqual(len(rows), 12)
        problems = base.validate_rows(rows, kinds=KINDS)
        self.assertTrue(any("duplicate" in p for p in problems),
                        "the schema gate is what refuses the ambiguity")

    def test_a_narrowed_glob_is_the_fix(self):
        adapter = from_config({**self.adapter.config, "glob": "Src/*.lean"},
                              source="<mirror-narrowed>")
        rows = list(adapter.extract(sources={SOURCE_ID: SOURCE_SHA}))
        self.assertEqual(base.validate_rows(rows, kinds=KINDS), [])
        self.assertEqual(adapter.dry_run(sources={SOURCE_ID: SOURCE_SHA})
                         ["duplicate_declarations"], 0)


class IgnoredFilesTest(unittest.TestCase):
    """A file the repository ignores is not part of the thing under audit.

    A companion is audited AT A COMMIT, so reading a git-ignored working file
    invents rows.  tao-measure-lean keeps spliced copies of its own modules in
    `scratch/` (.gitignore line 44); with the onboarding default `**/*.lean`
    they were parsed a second and third time, producing 142 duplicate keys that
    made `cf extract` refuse a repo `cf onboard` had just accepted.
    """

    def _repo(self, *, git: bool) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="cf-ignored-",
                                    dir=os.environ.get("CF_TEST_TMP") or None)).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "Src").mkdir()
        (tmp / "scratch").mkdir()
        (tmp / "Src" / "Sample.lean").write_text(SAMPLE, encoding="utf-8")
        (tmp / "scratch" / "Splice.lean").write_text(SAMPLE, encoding="utf-8")
        if git:
            (tmp / ".gitignore").write_text("scratch/\n", encoding="utf-8")
            env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.invalid",
                       GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e.invalid")
            subprocess.run(["git", "init", "-q", str(tmp)], check=True, env=env)
            subprocess.run(["git", "-C", str(tmp), "add", "-A"], check=True, env=env)
            subprocess.run(["git", "-C", str(tmp), "commit", "-qm", "t"], check=True, env=env)
        return tmp

    def _adapter(self, repo: Path):
        return from_config({"adapter": "docstring_tags", "name": "demo", "repo": str(repo),
                            "glob": "**/*.lean", "item_kinds": list(KINDS)},
                           source="<ignored>")

    def test_an_ignored_copy_is_not_read(self):
        adapter = self._adapter(self._repo(git=True))
        files = {p.name for p in adapter.source_files(adapter.resolve_repo())}
        self.assertEqual(files, {"Sample.lean"})
        rows = list(adapter.extract(sources={SOURCE_ID: SOURCE_SHA}))
        self.assertEqual(base.validate_rows(rows, kinds=KINDS), [])
        self.assertEqual(adapter.dry_run(sources={SOURCE_ID: SOURCE_SHA})
                         ["duplicate_declarations"], 0)

    def test_the_same_tree_without_git_reads_everything(self):
        """No repository to ask means read it all, not audit nothing."""
        adapter = self._adapter(self._repo(git=False))
        files = {p.name for p in adapter.source_files(adapter.resolve_repo())}
        self.assertEqual(files, {"Sample.lean", "Splice.lean"})
        problems = base.validate_rows(
            list(adapter.extract(sources={SOURCE_ID: SOURCE_SHA})), kinds=KINDS)
        self.assertTrue(any("duplicate" in p for p in problems))

    def test_ignored_files_is_empty_for_a_non_repo(self):
        tmp = Path(tempfile.mkdtemp(prefix="cf-ignored-none-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "a.lean").write_text("-- x\n", encoding="utf-8")
        self.assertEqual(api.Adapter.ignored_files(tmp, [tmp / "a.lean"]), [])

    def test_tracked_files_are_never_dropped(self):
        """The filter removes ignored paths only -- nothing else."""
        repo = self._repo(git=True)
        kept = [repo / "Src" / "Sample.lean"]
        self.assertEqual(api.Adapter.ignored_files(repo, kept), [])

    def test_asking_git_does_not_run_a_program_the_repo_named(self):
        """`check-ignore` refreshes the index, and that runs `core.fsmonitor`.

        So the question "which files does this tree ignore?" was an execve of a
        command the audited tree had chosen, on the flagless onboarding path.
        """
        repo = self._repo(git=True)
        marker = repo.parent / "PWNED-fsmonitor"
        hook = repo.parent / "fsmon.sh"
        hook.write_text(f'#!/bin/sh\ntouch "{marker}"\necho ""\n')
        hook.chmod(0o755)
        subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)],
                       check=True)
        marker.unlink(missing_ok=True)
        api.Adapter.ignored_files(repo, [repo / "Src" / "Sample.lean"])
        self.assertFalse(marker.exists(), "the companion repo's fsmonitor ran")


class SourceAttributionTest(unittest.TestCase):
    """Which source a row belongs to when a companion formalises TWO books.

    `cf onboard` writes the `sources.map` rules commented out, so every row fell
    through to `sources.default` -- the anchor -- and a second book's items were
    namespaced under the first book's id.  With no map, the attribution now asks
    each bound source's items.json.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-attr-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "Src").mkdir()
        (self.tmp / "Src" / "Sample.lean").write_text(SAMPLE, encoding="utf-8")
        self.ws = _ItemsWorkspace(self.tmp / "ws", {
            SOURCE_ID: ["Exercise 1.1.3", "Definition 1.1.1", "Lemma 1.2.5", "Example 1.1.9"],
            "other-book-1e": ["Exercise 1.2.17", "Definition 1.1.1"],
        })
        self.adapter = from_config(
            {"adapter": "docstring_tags", "name": "demo", "repo": str(self.tmp),
             "glob": "Src/*.lean", "item_kinds": list(KINDS)},
            workspace=self.ws, source="<attribution>")
        # what `cf onboard` / `cf extract` do: pin the anchor as the default for
        # rows no rule and no items.json can place.
        self.adapter.bind_default_source(SOURCE_ID)

    def test_each_row_goes_to_the_source_that_numbers_its_item(self):
        rows = list(self.adapter.extract(sources={SOURCE_ID: SOURCE_SHA,
                                                  "other-book-1e": "b" * 64}))
        by_item = {r["item"]: r["source_id"] for r in rows}
        self.assertEqual(by_item["Exercise 1.1.3"], SOURCE_ID)
        self.assertEqual(by_item["Exercise 1.2.17"], "other-book-1e")
        self.assertEqual({r["source_sha"] for r in rows if r["source_id"] == "other-book-1e"},
                         {"b" * 64})

    def test_an_item_both_books_number_is_reported_not_guessed(self):
        list(self.adapter.extract(sources={SOURCE_ID: SOURCE_SHA, "other-book-1e": "b" * 64}))
        binding = self.adapter.last_binding
        self.assertIn("Definition 1.1.1", binding.ambiguous)
        self.assertNotIn("Definition 1.1.1", binding.unattributed)

    def test_one_bound_source_still_takes_every_row(self):
        rows = list(self.adapter.extract(sources={SOURCE_ID: SOURCE_SHA}))
        self.assertEqual({r["source_id"] for r in rows}, {SOURCE_ID})
        self.assertEqual(self.adapter.last_binding.ambiguous, [])

    def test_an_explicit_map_still_wins(self):
        adapter = from_config(
            {**self.adapter.config,
             "sources": {"default": SOURCE_ID,
                         "map": [{"source": "other-book-1e", "tag_prefix": "Exercise 1.1"}]}},
            workspace=self.ws, source="<attribution-map>")
        rows = list(adapter.extract(sources={SOURCE_ID: SOURCE_SHA, "other-book-1e": "b" * 64}))
        by_item = {r["item"]: r["source_id"] for r in rows}
        self.assertEqual(by_item["Exercise 1.1.3"], "other-book-1e")


class _ItemsWorkspace:
    """The slice of a Workspace an adapter reads: kinds, sha and items.json."""

    def __init__(self, root: Path, items_by_source: dict):
        from cflib.paths import WorkspacePaths
        from cflib.workspace import SourceRef

        self.root = root
        self.paths = WorkspacePaths(root=root)
        self._refs = {}
        for sid, items in items_by_source.items():
            self._refs[sid] = SourceRef(id=sid, title=sid, edition="1e", kinds=tuple(KINDS),
                                        sha256=(SOURCE_SHA if sid == SOURCE_ID else "b" * 64))
            path = self.paths.source_items(sid)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"source_id": sid,
                                        "items": [{"item": i} for i in items]}))

    def source(self, source_id):
        from cflib.errors import SourceError
        if source_id not in self._refs:
            raise SourceError(f"unknown source {source_id!r}")
        return self._refs[source_id]


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
        cls.rows = list(cls.adapter.extract(sources={SOURCE_ID: SOURCE_SHA}))

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

    def test_every_row_names_the_published_edition_it_was_read_against(self):
        self.assertEqual({r["source_id"] for r in self.rows}, {SOURCE_ID})
        self.assertEqual({r["source_sha"] for r in self.rows}, {SOURCE_SHA})

    def test_discovery_finds_this_repo_s_two_editions_and_its_own_grammar(self):
        """S1 + S5: the formalization.yaml sources list and CLAUDE.md's editions.

        tao-measure-lean commits no PDF, names the book in `formalization.yaml`
        and states BOTH editions in CLAUDE.md as truncated shas.  The adapter
        reports them; nothing here adopts a truncated sha as an identity.
        """
        claims = self.adapter.discover_sources()
        by_signal = {}
        for claim in claims:
            by_signal.setdefault(claim.signal, []).append(claim)

        self.assertIn("formalization_yaml", by_signal)
        titled = by_signal["formalization_yaml"][0]
        self.assertIn("Measure Theory", titled.title)
        self.assertTrue(titled.authors)

        prefixes = {c.sha256_prefix for c in by_signal.get("prose", [])}
        self.assertLessEqual({"62d95658", "64498329"}, prefixes,
                             "CLAUDE.md states both editions")
        self.assertFalse(any(c.sha256 for c in by_signal.get("prose", [])
                             if c.sha256_prefix),
                         "a truncated sha must never be written as an identity")

        # this kind's own signals are ranked first, and they carry the grammar
        self.assertIn(claims[0].signal, set(self.adapter.PRIMARY_SIGNALS))
        self.assertLessEqual({"Exercise", "Definition", "In-text"}, set(claims[0].kinds))

    def test_the_item_kinds_come_from_the_repo_s_tags(self):
        kinds = self.adapter.observed_kinds()
        self.assertLessEqual({"Definition", "Lemma", "Theorem", "Exercise", "In-text"},
                             set(kinds))
        # frequency order, most-tagged first: Exercise dominates this repo
        self.assertEqual(kinds[0], "Exercise")

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
        cls.rows = list(cls.adapter.extract(sources={SOURCE_ID: SOURCE_SHA}))

    def test_row_count(self):
        self.assertEqual(len(self.rows), 562)

    def test_every_row_names_the_edition_the_ledger_pins(self):
        self.assertEqual({r["source_id"] for r in self.rows}, {SOURCE_ID})
        self.assertEqual({r["source_sha"] for r in self.rows}, {SOURCE_SHA})

    def test_discovery_finds_the_ledger_sha_the_official_record_and_the_book_page(self):
        """S2 + S3 + S7: the three signals this convention owns.

        measure_theory's ledger pins the bytes, official-source.json carries the
        author's download URL and the book page, and the README cites the page
        the errata live on.
        """
        claims = self.adapter.discover_sources()
        by_signal = {}
        for claim in claims:
            by_signal.setdefault(claim.signal, []).append(claim)

        ledger = by_signal["ledger_source"][0]
        self.assertEqual(ledger.sha256, SOURCE_SHA)
        self.assertIn("sources/gsm-126-tao5-measure-book.pdf", ledger.pdf_hints)

        official = by_signal["official_source_json"][0]
        self.assertEqual(official.sha256, SOURCE_SHA)
        self.assertTrue(any(u.endswith(".pdf") for u in official.urls))
        self.assertTrue(official.book_page_url.startswith("https://"))

        self.assertIn("citation", by_signal)
        self.assertTrue(by_signal["citation"][0].urls)

        # this kind's own signals are ranked first
        self.assertIn(claims[0].signal, set(self.adapter.PRIMARY_SIGNALS))

    def test_the_item_kinds_come_from_the_overlay_labels(self):
        kinds = self.adapter.observed_kinds()
        self.assertLessEqual({"Definition", "Exercise", "Theorem"}, set(kinds))

    def test_dry_run_reports_the_discovered_sources(self):
        report = self.adapter.dry_run(sources={SOURCE_ID: SOURCE_SHA})
        signals = {claim["signal"] for claim in report["sources"]}
        self.assertLessEqual({"ledger_source", "official_source_json"}, signals)
        self.assertEqual(report["rows_by_source"], {SOURCE_ID: 562})

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
    """`cf extract` writes, is idempotent, is byte-stable, and stamps the source.

    The fixture is a v2 WORKSPACE, not a campaign: a companion is audited against
    a LIST of sources, so the sha a row was read against is per source and lives
    under ``sources.<id>``, never at the top level.
    """

    @classmethod
    def setUpClass(cls):
        from cflib import log
        cls._log_level = log.level()
        log.set_level(0)                     # the command's own output is not the test's
        cls.tmp = Path(tempfile.mkdtemp(prefix="cf-extract-",
                                        dir=os.environ.get("CF_TEST_TMP") or None))
        (cls.tmp / "workspace.yaml").write_text(
            "version: 2\n"
            "workspace:\n"
            "  name: extract-test\n"
            "companions:\n"
            "  ours:\n"
            f"    repo: {env_path('CF_TML')}\n"
            "    adapter: docstring_tags\n"
            f"    sources: [{SOURCE_ID}]\n"
            f"    anchor_source: {SOURCE_ID}\n"
            "    owner: test\n"
            "    write_policy: read_only\n"
            "sources:\n"
            f"  {SOURCE_ID}:\n"
            "    title: An Introduction to Measure Theory\n"
            "    edition: AMS GSM 126 (published pagination)\n"
            f"    sha256: {SOURCE_SHA}\n"
            "    offset: 16\n"
            "    offset_confidence: 0.996\n"
            "    kinds: [Definition, Lemma, Theorem, Proposition, Corollary, Exercise,"
            " Example, Remark, Strategy, Claim]\n"
            "    role: audited\n"
            "    confirmed: true\n"
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
            "sources:\n"
            f"  default: {SOURCE_ID}\n"
            "  map: []\n"
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
        args = argparse.Namespace(workspace=str(self.tmp), companions=None, repo=None,
                                  force=False, dry_run=False, allow_missing_source_sha=False)
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
        # v2: the sha a table was built against is PER SOURCE, not top level.
        self.assertNotIn("book_sha", state)
        self.assertEqual(state["sources"][SOURCE_ID]["sha"], SOURCE_SHA)
        self.assertEqual(state["sources"][SOURCE_ID]["rows"], len(first.splitlines()))
        self.assertEqual(state["anchor_source"], SOURCE_ID)

        mtime = out.stat().st_mtime_ns
        self.assertEqual(self._run(), 0)          # unchanged key: a no-op
        self.assertEqual(out.stat().st_mtime_ns, mtime)

        self.assertEqual(self._run(force=True), 0)
        self.assertEqual(out.read_bytes(), first)  # byte-stable

    def test_every_written_row_carries_the_source_id_and_sha(self):
        self._run()
        out = self.tmp / "companions" / "ours" / "statements.jsonl"
        rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        self.assertTrue(all(r["source_id"] == SOURCE_ID for r in rows))
        self.assertTrue(all(r["source_sha"] == SOURCE_SHA for r in rows))
        self.assertTrue(all("book_sha" not in r for r in rows))

    def test_a_source_with_no_sha_is_refused_rather_than_stamped_null(self):
        """A row stamped with an unidentified edition is the batch-1 mistake."""
        from cflib.workspace import Workspace
        ws = Workspace.load(self.tmp)
        original = dict(ws.data["sources"][SOURCE_ID])
        ws.data["sources"][SOURCE_ID].pop("sha256")
        ws.save()
        try:
            self.assertEqual(self._run(force=True), 1)   # reported, not raised
        finally:
            ws = Workspace.load(self.tmp)
            ws.data["sources"][SOURCE_ID] = original
            ws.save()

    def test_a_row_attributed_outside_the_companion_s_sources_is_refused(self):
        """The table's own edition gate: no row may name a source nobody bound."""
        from cfcmd import extract as extract_cmd
        rows = [valid_row(source_id="an-edition-nobody-bound")]
        problems = extract_cmd._source_problems(rows, {SOURCE_ID: SOURCE_SHA}, "ours")
        self.assertTrue(any("an-edition-nobody-bound" in p for p in problems))

    def test_a_row_stamped_with_another_edition_s_sha_is_refused(self):
        from cfcmd import extract as extract_cmd
        rows = [valid_row(source_id=SOURCE_ID, source_sha=DRAFT_SHA)]
        problems = extract_cmd._source_problems(rows, {SOURCE_ID: SOURCE_SHA}, "ours")
        self.assertTrue(any("source_sha" in p for p in problems))


class TestRegistryMatchesTheVocabulary(unittest.TestCase):
    """`cf onboard` refuses a kind outside IMPLEMENTED_ADAPTER_KINDS, so it must be true."""

    def test_implemented_set_equals_the_registry(self):
        from adapters.api import registry
        from cflib import vocab

        self.assertEqual(set(registry()), set(vocab.IMPLEMENTED_ADAPTER_KINDS))
        self.assertTrue(vocab.IMPLEMENTED_ADAPTER_KINDS <= vocab.ADAPTER_KINDS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
