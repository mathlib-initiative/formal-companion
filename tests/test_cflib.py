"""Unit tests for the cf core: item grammar, state, workspace, registry,
discovery, inference, yaml, vocab.

Run:  python3 -m unittest discover -s /Users/jack/Desktop/companion-faithfulness/tests -v
(or:  python3 /Users/jack/Desktop/companion-faithfulness/tests/test_cflib.py)

Regression tests that read the local baseline are skipped cleanly unless the
environment names it:

    CF_BASELINE_DIFF=/Users/jack/Desktop/tao-differential   (statement tables)
    CF_BASELINE_CORPUS=/Users/jack/Desktop/tao-diff-run     (2,672 findings)
    CF_TML=/Users/jack/Desktop/tao-measure-lean
    CF_MT=/Users/jack/Desktop/measure_theory
    CF_BOOK_TXT=/Users/jack/Desktop/tao-differential/book.official.txt

Nothing here touches the network, and nothing reads a PDF: discovery is
exercised against FIXTURE repos that carry the same signals the two real
companions carry (docs/design/multi-source-model.md S4.3).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import discovery, hashing, inference, items, state as state_mod  # noqa: E402
from cflib import templates, vocab, yamlio  # noqa: E402
from cflib.errors import CfError, ItemGrammarError, SourceError, StateError  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from cflib.registry import Registry, RegistryEntry, source_id_for  # noqa: E402
from cflib.workspace import WORKSPACE_FILE, Workspace, find_workspace_root  # noqa: E402

#: the two editions the shipped registry ships, used by the discovery fixtures
PUBLISHED_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
DRAFT_SHA = "62d9565841b8593a7a3d12b3cbcea6ce8d3d3ae2f83b60ebf024d59714c0c6e8"
PUBLISHED_ID = "tao-measure-gsm126-published"
DRAFT_ID = "tao-measure-2011-draft"


# ---------------------------------------------------------------------------
# item grammar
# ---------------------------------------------------------------------------

class TestItemGrammar(unittest.TestCase):
    """The grammar itself is UNCHANGED by the multi-source model: what changes is
    that its `kinds` come from one source's `sources.<id>.kinds` (S6.1)."""

    def test_plain_numbered_item(self):
        it = items.parse("Exercise 1.1.1")
        self.assertEqual(it.id, "Exercise 1.1.1")
        self.assertEqual((it.kind, it.chapter, it.section, it.number), ("Exercise", 1, 1, 1))
        self.assertEqual(it.sub, "")
        self.assertFalse(it.in_text)

    def test_irregular_whitespace_is_tolerated(self):
        """The baseline lost `Exercise    1.3.19` to a single-space regex."""
        for raw in ["Exercise    1.3.19", "Exercise\t1.3.19", "  Exercise 1.3.19  ",
                    "Exercise 1.3.19.", "`Exercise 1.3.19`", "Exercise 1.3.19"]:
            self.assertEqual(items.normalise(raw), "Exercise 1.3.19", raw)

    def test_sub_parts(self):
        cases = {
            "Exercise 1.1.6(1)": ("Exercise 1.1.6", "(1)"),
            "Exercise 1.2.24(ii)": ("Exercise 1.2.24", "(ii)"),
            "Exercise 1.1.6 (i) (Boolean closure)": ("Exercise 1.1.6", "(i)"),
            "Exercise 1.3.3(iii)": ("Exercise 1.3.3", "(iii)"),
            "Lemma 1.2.13(vi) (Countable union)": ("Lemma 1.2.13", "(vi)"),
            "Exercise 1.1.8(ii) (Jordan measure of a triangle)": ("Exercise 1.1.8", "(ii)"),
            "Exercise 1.4.29 (3)": ("Exercise 1.4.29", "(3)"),
        }
        for raw, (ident, sub) in cases.items():
            with self.subTest(raw=raw):
                it = items.parse(raw)
                self.assertEqual((it.id, it.sub), (ident, sub))
                self.assertEqual(it.full, ident + sub)

    def test_sub_part_case_and_order(self):
        self.assertEqual(items.parse("Exercise 1.1.6 (IV)").sub, "(iv)")
        self.assertLess(items.sub_rank("(i)"), items.sub_rank("(ii)"))
        self.assertLess(items.sub_rank("(2)"), items.sub_rank("(10)"))
        order = sorted(["Exercise 1.1.6(iii)", "Exercise 1.1.6(i)", "Exercise 1.1.6",
                        "Exercise 1.1.6(ii)"], key=items.sort_key)
        self.assertEqual(order, ["Exercise 1.1.6", "Exercise 1.1.6(i)",
                                 "Exercise 1.1.6(ii)", "Exercise 1.1.6(iii)"])

    def test_title_and_trailing_code_leak(self):
        """teorth rows leak Lean code after the title; keep the item, flag the leak."""
        it = items.parse("Definition 1.3.8 (Unsigned measurable function) def UnsignedMeasurable {d")
        self.assertEqual(it.id, "Definition 1.3.8")
        self.assertEqual(it.title, "Unsigned measurable function")
        self.assertTrue(it.trailing.startswith("def UnsignedMeasurable"))

    def test_two_level_item_number(self):
        it = items.parse("Theorem 1.2")
        self.assertEqual((it.id, it.chapter, it.section, it.number), ("Theorem 1.2", 1, 2, None))

    def test_in_text_pages(self):
        cases = {
            "In-text, book p. 8": 8,
            "In-text definition, book p. 29": 29,
            "In-text convention, book p. 29": 29,
            "In-text p.194": 194,
            "In-text proof (book p. 241)": 241,
            "In-text, book pp. 50–51": 50,          # en dash range -> first page
            "In-text pp. 12-13": 12,
            "In-text definition, page 40": 40,
            "In-text proof of Lemma 1.1.2(ii), book p. 6": 6,
        }
        for raw, page in cases.items():
            with self.subTest(raw=raw):
                it = items.parse(raw)
                self.assertEqual(it.id, f"In-text p.{page}")
                self.assertEqual(it.page, page)
                self.assertTrue(it.in_text)

    def test_in_text_cross_reference_without_a_page(self):
        it = items.parse("In-text definition, Lemma 1.2.13(iii)")
        self.assertEqual(it.id, "In-text Lemma 1.2.13(iii)")
        self.assertEqual(it.qualifier, "definition")
        it2 = items.parse("In-text step in the proof of Lemma 1.2.13(ii)")
        self.assertEqual(it2.id, "In-text Lemma 1.2.13(ii)")
        self.assertTrue(it2.in_text)

    def test_reject_list(self):
        """`compare.norm` keyed junk as a first-class item; the grammar refuses."""
        rejects = {
            "Corollary (Lemma 1.2.5 + Exercise 1.2.4)": items.REJECT_NO_NUMBER,
            "Foo 1.2.3": items.REJECT_UNKNOWN_KIND,
            "": items.REJECT_EMPTY,
            "   ": items.REJECT_EMPTY,
            "In-text": items.REJECT_IN_TEXT_NO_TARGET,
            "In-text, somewhere": items.REJECT_IN_TEXT_NO_TARGET,
        }
        for raw, reason in rejects.items():
            with self.subTest(raw=raw):
                item, reject = items.try_parse(raw)
                self.assertIsNone(item)
                self.assertEqual(reject.reason, reason)
                with self.assertRaises(ItemGrammarError):
                    items.parse(raw)

    def test_kinds_come_from_the_source(self):
        """The grammar is per SOURCE: kinds are sources.<id>.kinds, not a constant."""
        self.assertEqual(items.parse("Widget 2.3.4", kinds=("Widget",)).id, "Widget 2.3.4")
        item, reject = items.try_parse("Exercise 1.1.1", kinds=("Widget",))
        self.assertIsNone(item)
        self.assertEqual(reject.reason, items.REJECT_UNKNOWN_KIND)
        self.assertIsNone(items.try_parse("Strategy 1.1.1", ("Definition",))[0])
        self.assertEqual(items.parse("Strategy 1.1.1", ("Definition", "Strategy")).kind,
                         "Strategy")

    def test_parse_all_partitions(self):
        good, bad = items.parse_all(["Exercise 1.1.1", "junk", "Lemma 1.2.3"])
        self.assertEqual([g.id for g in good], ["Exercise 1.1.1", "Lemma 1.2.3"])
        self.assertEqual([b.raw for b in bad], ["junk"])

    def test_slugs_match_the_baseline_spellings(self):
        self.assertEqual(items.slug("Corollary 1.3.14"), "corollary-1_3_14")          # tend tasks
        self.assertEqual(items.slug("Corollary 1.3.14", "dash"), "corollary-1-3-14")  # attack findings
        self.assertEqual(items.slug("Exercise 1.1.6(i)"), "exercise-1_1_6-i")
        self.assertEqual(items.slug("In-text p.35"), "in-text-p35")
        self.assertIn("in-text-book-p-10", items.slug_variants("In-text p.10", raw="In-text, book p. 10"))

    def test_sort_key_orders_by_book_position(self):
        order = sorted(["Lemma 1.2.13", "Exercise 1.1.1", "In-text p.5", "Theorem 1.10.1"],
                       key=items.sort_key)
        self.assertEqual(order, ["Exercise 1.1.1", "Lemma 1.2.13", "Theorem 1.10.1", "In-text p.5"])


class TestItemNamespacing(unittest.TestCase):
    """An item is the pair (source_id, item) -- S6.1."""

    def test_qualified_item_round_trips_and_cleans(self):
        q = items.qualified_item(PUBLISHED_ID, "Exercise    1.3.19")
        self.assertEqual(q, f"{PUBLISHED_ID}::Exercise 1.3.19")
        self.assertEqual(items.split_qualified(q), (PUBLISHED_ID, "Exercise 1.3.19"))

    def test_a_bare_item_is_never_a_key(self):
        with self.assertRaises(ItemGrammarError):
            items.split_qualified("Exercise 1.3.19")
        with self.assertRaises(ItemGrammarError):
            items.qualified_item("", "Exercise 1.3.19")

    def test_qualified_slug_is_per_source(self):
        a = items.qualified_slug(PUBLISHED_ID, "Exercise 1.3.19")
        b = items.qualified_slug(DRAFT_ID, "Exercise 1.3.19")
        self.assertNotEqual(a, b)
        self.assertEqual(a, f"{PUBLISHED_ID}--exercise-1-3-19")

    def test_the_separator_is_one_constant(self):
        self.assertEqual(items.QUALIFIED_SEP, "::")


# ---------------------------------------------------------------------------
# state.json (v2)
# ---------------------------------------------------------------------------

class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = WorkspacePaths(root=Path(self.tmp.name))
        self.paths.ensure_skeleton()

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        st = state_mod.new_state("ours", repo="/repo", commit="abc123",
                                 adapter={"kind": "docstring_tags"},
                                 sources=[PUBLISHED_ID], anchor_source=PUBLISHED_ID)
        state_mod.set_section(st, "extract", {"rows": 1157, "items": 763, "sorry": 516})
        state_mod.source_section(st, PUBLISHED_ID, {"sha": PUBLISHED_SHA, "rows": 1157})
        state_mod.record_run(st, "attack-hunt-claude", {"tasks": 763, "findings": 763})
        state_mod.note(st, "extract", rows=1157)
        state_mod.write_state(self.paths, "ours", st)

        back = state_mod.read_state(self.paths, "ours")
        self.assertEqual(back["version"], 2)
        self.assertEqual(back["name"], "ours")
        self.assertEqual(back["extract"]["rows"], 1157)
        self.assertIn("at", back["extract"])
        self.assertEqual(back["runs"]["attack-hunt-claude"]["tasks"], 763)
        self.assertEqual(back["history"][-1]["event"], "extract")
        self.assertEqual(back["anchor_source"], PUBLISHED_ID)
        self.assertEqual(back["sources"][PUBLISHED_ID]["sha"], PUBLISHED_SHA)

    def test_the_sha_is_per_source_not_per_companion(self):
        """v1's single top-level book_sha cannot describe two sources."""
        st = state_mod.new_state("ours", sources=[PUBLISHED_ID, DRAFT_ID])
        self.assertNotIn("book_sha", st)
        state_mod.source_section(st, PUBLISHED_ID, {"sha": PUBLISHED_SHA, "items": 763})
        state_mod.source_section(st, DRAFT_ID, {"sha": DRAFT_SHA, "items": 761})
        self.assertEqual(st["sources"][PUBLISHED_ID]["sha"], PUBLISHED_SHA)
        self.assertEqual(st["sources"][DRAFT_ID]["sha"], DRAFT_SHA)

    def test_update_context_manager_preserves_unknown_sections(self):
        st = state_mod.new_state("ours")
        st["future_stage"] = {"kept": True}
        state_mod.write_state(self.paths, "ours", st)
        with state_mod.update(self.paths, "ours") as doc:
            state_mod.merge_section(doc, "coverage", {"single_source": 567})
        with state_mod.update(self.paths, "ours") as doc:
            state_mod.merge_section(doc, "coverage", {"uncovered": 29})
        back = state_mod.read_state(self.paths, "ours")
        self.assertEqual(back["future_stage"], {"kept": True})
        self.assertEqual(back["coverage"]["single_source"], 567)
        self.assertEqual(back["coverage"]["uncovered"], 29)

    def test_update_does_not_write_when_the_body_raises(self):
        state_mod.write_state(self.paths, "ours", state_mod.new_state("ours"))
        before = self.paths.state_json("ours").read_text()
        with self.assertRaises(RuntimeError):
            with state_mod.update(self.paths, "ours") as doc:
                doc["extract"] = {"rows": 1}
                raise RuntimeError("boom")
        self.assertEqual(self.paths.state_json("ours").read_text(), before)

    def test_missing_state_is_an_error_unless_allowed(self):
        with self.assertRaises(StateError):
            state_mod.read_state(self.paths, "nobody")
        self.assertEqual(state_mod.read_state(self.paths, "nobody", missing_ok=True)["name"], "nobody")

    def test_summary_row_shape(self):
        st = state_mod.new_state("ours", commit="abcdef1234", sources=[PUBLISHED_ID],
                                 anchor_source=PUBLISHED_ID)
        state_mod.set_section(st, "extract", {"rows": 10, "items": 5})
        row = state_mod.summary_row(st)
        self.assertEqual(row["commit"], "abcdef1")
        self.assertEqual((row["rows"], row["items"]), (10, 5))
        self.assertEqual((row["sources"], row["anchor"]), (1, PUBLISHED_ID))


# ---------------------------------------------------------------------------
# workspace.yaml
# ---------------------------------------------------------------------------

WORKSPACE_MIN = """\
version: 2
workspace:
  name: tao
companions:
  ours:
    repo: {repo_a}
    adapter: docstring_tags
    sources: [tao-measure-gsm126-published]
    anchor_source: tao-measure-gsm126-published
    bundled_sources: [tao-measure-2011-draft]
    write_policy: read_only
  adam:
    repo: {repo_b}
    adapter: attribution_table
    sources: [tao-measure-gsm126-published]
    anchor_source: tao-measure-gsm126-published
    write_policy: read_only
sources:
  tao-measure-gsm126-published:
    sha256: "{published}"
    title: An Introduction to Measure Theory
    edition: "AMS GSM 126 (published pagination)"
    offset: 16
    offset_confidence: 0.9959
    offset_method: running_head_mode
    kinds: [Definition, Lemma, Exercise]
    chapters: [1]
    role: audited
    confirmed: true
    same_work: [tao-measure-2011-draft]
    page_map_to:
      tao-measure-2011-draft:
        default_shift: 0
        exception_shift: 1
        exceptions: [Lemma 1.6.3]
        note: "~65 items of S1.6 print one page later in the draft"
  tao-measure-2011-draft:
    sha256: "{draft}"
    title: An Introduction to Measure Theory
    edition: "2011-01-24 draft"
    offset: 16
    offset_confidence: 0.996
    offset_method: running_head_mode
    kinds: [Definition, Lemma, Exercise]
    role: reference
    confirmed: false
    same_work: [tao-measure-gsm126-published]
hosts:
  allowlist: [buildbox.example]
models:
  worker: claude-sonnet-5
defaults:
  mode: attack:vac
  local_fleet_max_items: 100
policy:
  allow_download: false
"""


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # resolve(): on macOS the temp dir is /var/... but cflib resolves paths,
        # which gives /private/var/... -- compare like with like.
        self.root = Path(self.tmp.name).resolve()
        self.repo_a = self.root / "repo-a"
        self.repo_b = self.root / "repo-b"
        self.repo_a.mkdir()
        self.repo_b.mkdir()
        self._write(WORKSPACE_MIN)

    def _write(self, text: str) -> None:
        (self.root / WORKSPACE_FILE).write_text(text.format(
            repo_a=self.repo_a, repo_b=self.repo_b,
            published=PUBLISHED_SHA, draft=DRAFT_SHA))

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_and_accessors(self):
        ws = Workspace.load(self.root)
        self.assertEqual(ws.name, "tao")
        self.assertEqual(sorted(ws.sources), [DRAFT_ID, PUBLISHED_ID])
        self.assertEqual(ws.offset(PUBLISHED_ID), 16)
        self.assertEqual(ws.kinds(PUBLISHED_ID), ("Definition", "Lemma", "Exercise"))
        self.assertEqual(ws.chapters(PUBLISHED_ID), (1,))
        self.assertEqual(ws.model("worker"), "claude-sonnet-5")
        self.assertEqual(ws.default("local_fleet_max_items"), 100)
        self.assertEqual(ws.companion("ours").adapter, "docstring_tags")
        self.assertEqual(sorted(ws.companion_names()), ["adam", "ours"])

    def test_paths_are_keyed_by_source_id(self):
        ws = Workspace.load(self.root)
        self.assertEqual(ws.paths.source_text(PUBLISHED_ID),
                         self.root / "sources" / PUBLISHED_ID / "text.txt")
        self.assertEqual(ws.paths.source_meta(DRAFT_ID),
                         self.root / "sources" / DRAFT_ID / "meta.json")
        self.assertEqual(ws.paths.pairs_jsonl(PUBLISHED_ID),
                         self.root / "pairs" / PUBLISHED_ID / "pairs.jsonl")
        self.assertEqual(ws.paths.statements_jsonl("ours"),
                         self.root / "companions" / "ours" / "statements.jsonl")

    def test_a_differential_is_exactly_a_shared_source(self):
        """R1: several companions may share sources; a differential is possible
        exactly where two companions' source lists overlap."""
        ws = Workspace.load(self.root)
        self.assertEqual(ws.shared_sources(), {PUBLISHED_ID: ["ours", "adam"]})
        self.assertEqual(ws.sources_of("ours"), [PUBLISHED_ID])
        self.assertEqual(sorted(ws.companions_of(PUBLISHED_ID)), ["adam", "ours"])
        # the draft is bundled by `ours` and audited by nobody: no differential
        self.assertEqual(ws.companions_of(DRAFT_ID), [])
        self.assertNotIn(DRAFT_ID, ws.shared_sources())

    def test_the_anchor_edition_is_not_always_a_bundled_one(self):
        ws = Workspace.load(self.root)
        ours = ws.companion("ours")
        self.assertEqual(ws.anchor_source("ours"), PUBLISHED_ID)
        self.assertEqual(ours.bundled_sources, (DRAFT_ID,))
        self.assertNotIn(DRAFT_ID, ours.sources)

    def test_page_map_to_a_sibling_edition(self):
        ws = Workspace.load(self.root)
        published = ws.source(PUBLISHED_ID)
        self.assertEqual(published.shift_to(DRAFT_ID), 0)
        self.assertEqual(published.shift_to(DRAFT_ID, item="Lemma 1.6.3"), 1)
        self.assertIsNone(published.shift_to("nobody"))

    def test_printed_and_pdf_pages(self):
        published = Workspace.load(self.root).source(PUBLISHED_ID)
        self.assertEqual(published.printed_page(17), 1)
        self.assertEqual(published.pdf_page(1), 17)

    def test_host_allowlist_is_enforced(self):
        ws = Workspace.load(self.root)
        self.assertEqual(ws.check_host("buildbox.example"), "buildbox.example")
        with self.assertRaises(CfError):
            ws.check_host("someone-elses-server")

    def test_source_gate(self):
        ws = Workspace.load(self.root)
        ws.require_source_sha(PUBLISHED_ID, PUBLISHED_SHA, "findings")   # same edition: fine
        with self.assertRaises(CfError):
            ws.require_source_sha(PUBLISHED_ID, DRAFT_SHA, "findings")   # the 2011 draft
        with self.assertRaises(SourceError):
            ws.require_source_sha("nobody", PUBLISHED_SHA, "findings")

    def test_unconfirmed_sources_are_visible(self):
        ws = Workspace.load(self.root)
        self.assertEqual([s.id for s in ws.unconfirmed_sources()], [DRAFT_ID])

    def test_unknown_companion_and_source_raise(self):
        ws = Workspace.load(self.root)
        with self.assertRaises(CfError):
            ws.companion("nope")
        with self.assertRaises(SourceError):
            ws.source("nope")

    def test_search_upward_and_env(self):
        nested = self.root / "companions" / "ours"
        nested.mkdir(parents=True)
        self.assertEqual(find_workspace_root(nested), self.root.resolve())
        old = os.environ.get("CF_WORKSPACE")
        os.environ["CF_WORKSPACE"] = str(self.root)
        try:
            self.assertEqual(find_workspace_root(None), self.root.resolve())
        finally:
            os.environ.pop("CF_WORKSPACE", None)
            if old is not None:
                os.environ["CF_WORKSPACE"] = old

    def test_version_1_is_refused(self):
        (self.root / WORKSPACE_FILE).write_text("version: 1\nbook:\n  id: x\n")
        with self.assertRaises(CfError):
            Workspace.load(self.root)

    def test_two_sources_may_not_share_a_sha(self):
        self._write(WORKSPACE_MIN.replace('sha256: "{draft}"', 'sha256: "{published}"'))
        with self.assertRaises(CfError) as ctx:
            Workspace.load(self.root)
        self.assertIn("same bytes are one source", str(ctx.exception))

    def test_an_anchor_source_must_be_one_of_the_companion_s_sources(self):
        self._write(WORKSPACE_MIN.replace(
            "    anchor_source: tao-measure-gsm126-published\n    bundled_sources",
            "    anchor_source: tao-measure-2011-draft\n    bundled_sources"))
        with self.assertRaises(CfError) as ctx:
            Workspace.load(self.root)
        self.assertIn("anchor_source", str(ctx.exception))

    def test_an_audited_source_nobody_audits_is_refused(self):
        self._write(WORKSPACE_MIN.replace("    role: reference", "    role: audited"))
        with self.assertRaises(CfError) as ctx:
            Workspace.load(self.root)
        self.assertIn("role is audited but no companion lists it", str(ctx.exception))

    def test_upsert_round_trips_through_save(self):
        ws = Workspace.load(self.root)
        ws.upsert_source("smith-analysis-2e", title="Analysis", edition="2e", offset=4,
                         role="reference", confirmed=False)
        ws.upsert_companion("adam", owner="external")
        ws.save()
        again = Workspace.load(self.root)
        self.assertEqual(again.source("smith-analysis-2e").offset, 4)
        self.assertEqual(again.companion("adam").owner, "external")
        self.assertEqual(len(again.sources), 3)

    def test_save_keeps_the_load_bearing_comments(self):
        ws = Workspace.load(self.root)
        ws.save()
        text = (self.root / WORKSPACE_FILE).read_text()
        self.assertIn("anchor_source", text)
        self.assertIn("the edition whose PAGINATION", text)
        self.assertIn("HARD account limit", text)


# ---------------------------------------------------------------------------
# the source registry
# ---------------------------------------------------------------------------

class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load()

    def test_the_shipped_registry_is_valid(self):
        self.assertEqual(self.registry.validate(), [])

    def test_both_tao_editions_ship(self):
        for sid in (PUBLISHED_ID, DRAFT_ID):
            with self.subTest(sid=sid):
                entry = self.registry.get(sid)
                self.assertIsNotNone(entry)
                self.assertTrue(entry.confirmed)
                self.assertEqual(entry.offset, 16)
                self.assertGreaterEqual(entry.offset_confidence, 0.99)

    def test_lookup_by_sha_prefix_url_filename_and_title(self):
        self.assertEqual(self.registry.by_sha(PUBLISHED_SHA).id, PUBLISHED_ID)
        self.assertEqual([e.id for e in self.registry.by_sha_prefix("62d95658")], [DRAFT_ID])
        self.assertEqual(
            self.registry.by_url(
                "https://terrytao.wordpress.com/wp-content/uploads/2012/12/"
                "gsm-126-tao5-measure-book.pdf").id,
            PUBLISHED_ID)
        self.assertEqual([e.id for e in self.registry.by_filename("tao-measure-theory.pdf")],
                         [DRAFT_ID])
        hits = self.registry.by_title("An Introduction to Measure Theory")
        self.assertEqual(sorted(e.id for e in hits), [DRAFT_ID, PUBLISHED_ID])

    def test_a_work_level_url_does_not_identify_an_edition(self):
        """Both editions carry the author's book page: it names the WORK."""
        self.assertIsNone(self.registry.by_url(
            "https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/"))

    def test_page_map_is_symmetric_and_carries_the_one_page_shift(self):
        self.assertEqual(self.registry.page_shift(PUBLISHED_ID, DRAFT_ID), 0)
        entry = self.registry.get(PUBLISHED_ID)
        body = entry.page_map_to[DRAFT_ID]
        self.assertEqual(body["exception_shift"], 1)
        mirror = self.registry.get(DRAFT_ID).page_map_to[PUBLISHED_ID]
        self.assertEqual(mirror["exception_shift"], -1)
        self.assertTrue(body["note"].strip())

    def test_page_map_asymmetry_is_a_validation_error(self):
        broken = Registry({
            "a": RegistryEntry(id="a", title="A", edition="1", same_work=("b",),
                               page_map_to={"b": {"default_shift": 0, "exception_shift": 1,
                                                  "exceptions": [], "note": "x"}}),
            "b": RegistryEntry(id="b", title="A", edition="2", same_work=("a",),
                               page_map_to={"a": {"default_shift": 0, "exception_shift": 1,
                                                  "exceptions": [], "note": "x"}}),
        })
        problems = broken.validate()
        self.assertTrue(any("not the negative" in p for p in problems), problems)

    def test_the_registry_never_carries_book_text(self):
        """A copyright boundary, not a convention (S2)."""
        text = (SKILL / "registry" / "sources.yaml").read_text()
        for key in ("text", "body", "quote", "excerpt"):
            self.assertNotIn(f"\n    {key}:", text)
        for entry in self.registry.entries.values():
            self.assertFalse(entry.licence.get("redistribute"))

    def test_only_an_author_published_file_may_be_fetched(self):
        self.assertTrue(self.registry.get(PUBLISHED_ID).may_fetch())
        self.assertFalse(self.registry.get(DRAFT_ID).may_fetch())

    def test_the_overlay_shadows_the_skill_registry_and_is_never_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = Registry.load()
            entry = RegistryEntry(id="smith-analysis-2e", title="Analysis", edition="2e",
                                  confirmed=True, offset=2)
            path = registry.append_overlay(root, entry)
            self.assertEqual(path.name, "registry.yaml")
            again = Registry.load(root)
            self.assertEqual(again.get("smith-analysis-2e").origin, "overlay")
            self.assertFalse(again.get("smith-analysis-2e").confirmed)
            self.assertEqual([e.id for e in again.unconfirmed()], ["smith-analysis-2e"])
            # the skill's registry is untouched
            self.assertNotIn("smith-analysis-2e",
                             (SKILL / "registry" / "sources.yaml").read_text())
            # and a shadowed field wins over the shipped one
            registry.append_overlay(root, RegistryEntry(id=PUBLISHED_ID, title="T", edition="E",
                                                        offset=99))
            self.assertEqual(Registry.load(root).get(PUBLISHED_ID).offset, 99)


class TestSourceIdRule(unittest.TestCase):
    """S3: <author>-<title>-<edition>, derived, stable and collision-free."""

    def test_the_two_worked_examples(self):
        self.assertEqual(
            source_id_for("An Introduction to Measure Theory",
                          "AMS GSM 126, author's preliminary AMS version "
                          "(published pagination; PDF metadata 2012-12-04)",
                          ["Terence Tao"]),
            PUBLISHED_ID)
        self.assertEqual(
            source_id_for("An Introduction to Measure Theory",
                          "2011-01-24 draft (pre-publication pagination)", ["Terence Tao"]),
            DRAFT_ID)

    def test_stopwords_and_generic_nouns_are_dropped(self):
        self.assertEqual(source_id_for("A First Course in Real Analysis", "2e", ["Ann Smith"]),
                         "smith-real-2e")

    def test_collisions_never_silently_reuse_an_id(self):
        taken = {PUBLISHED_ID}
        again = source_id_for("An Introduction to Measure Theory",
                              "AMS GSM 126 (published)", ["Terence Tao"], taken=taken)
        self.assertEqual(again, PUBLISHED_ID + "-2")

    def test_without_an_edition_token_a_collision_falls_back_to_the_sha(self):
        first = source_id_for("Measure Theory", "", ["Terence Tao"])
        second = source_id_for("Measure Theory", "", ["Terence Tao"],
                               sha256=PUBLISHED_SHA, taken={first})
        self.assertEqual(second, first + "-" + PUBLISHED_SHA[:8])

    def test_ids_are_short_and_filename_safe(self):
        sid = source_id_for("An Extremely Long Title About Everything Under The Sun",
                            "second revised corrected edition", ["Jean-Luc de la Fontaine"])
        self.assertLessEqual(len(sid), 48)
        self.assertRegex(sid, r"^[a-z0-9-]+$")


# ---------------------------------------------------------------------------
# inference (pure: text in, numbers out)
# ---------------------------------------------------------------------------

def _paged(pages: list[str]) -> list[str]:
    return pages


def _book_pages(n_pages: int = 60, offset: int = 16, blank_first: int = 16) -> list[str]:
    """Synthetic pdftotext output: front matter, then running-head page numbers."""
    pages = ["front matter\n" for _ in range(blank_first)]
    for printed in range(1, n_pages - blank_first + 1):
        pages.append(f"{printed}   1.2. LEBESGUE MEASURE\n\nbody text\n")
    return pages


class TestInference(unittest.TestCase):
    def test_offset_from_running_heads(self):
        guess = inference.infer_offset(_book_pages())
        self.assertEqual(guess.offset, 16)
        self.assertEqual(guess.confidence, 1.0)
        self.assertTrue(guess.ok())
        self.assertEqual(guess.voting_pages, 44)

    def test_a_declared_offset_only_cross_checks(self):
        agree = inference.infer_offset(_book_pages(), declared=16)
        self.assertGreaterEqual(agree.confidence, 0.95)
        with self.assertRaises(SourceError) as ctx:
            inference.infer_offset(_book_pages(), declared=17)
        self.assertIn("17", str(ctx.exception))

    def test_a_weak_inference_is_not_ok_so_the_caller_must_ask(self):
        noisy = ["".join(f"{i * 7 % 40}\nbody\n") for i in range(1, 30)]
        guess = inference.infer_offset(noisy)
        self.assertFalse(guess.ok())
        self.assertLess(guess.confidence, inference.MIN_OFFSET_CONFIDENCE)

    def test_a_text_with_no_running_heads_votes_with_nobody(self):
        guess = inference.infer_offset(["body\n", "more body\n"])
        self.assertEqual(guess.voting_pages, 0)
        self.assertFalse(guess.ok())

    def test_kinds_come_from_the_headings_and_structure_is_reported_separately(self):
        text = "\n".join(
            [f"Definition 1.{i}.1 (X)" for i in range(1, 6)]
            + [f"Exercise 1.1.{i}." for i in range(1, 5)]
            + ["Lemma 1.2.3.", "Lemma 1.2.4.", "Lemma 1.2.5 (Y)"]
            + ["Section 1.2. More", "Figure 1.4. A picture", "Table 1.5. Numbers"]
            + ["Chapter 1. Measure", "Widget 1.1.1"])
        kinds, structural = inference.infer_kinds(text)
        self.assertEqual(sorted(kinds), ["Definition", "Exercise", "Lemma"])
        self.assertEqual(kinds["Definition"], 5)
        # structural words are counted and REPORTED, never offered as item kinds
        for word in ("Section", "Figure", "Table"):
            self.assertIn(word, structural)
            self.assertNotIn(word, kinds)
        # `Chapter 1.` carries ONE number, so S5.2's two-level HEADING does not
        # see it at all -- it is neither an item kind nor a structural count.
        self.assertNotIn("Chapter", structural)
        self.assertNotIn("Chapter", kinds)
        self.assertNotIn("Widget", kinds)          # below min_count
        self.assertEqual(inference.infer_kinds(text, min_count=1)[0]["Widget"], 1)

    def test_scope_is_what_the_companions_tag(self):
        items_json = {"items": [{"item": f"Definition {c}.1.{i}", "chapter": c}
                                for c in (1, 2, 3) for i in range(1, 11)]}
        rows = ([{"item": f"Definition 1.1.{i}", "source_id": PUBLISHED_ID} for i in range(1, 9)]
                + [{"item": "Definition 2.1.1", "source_id": PUBLISHED_ID}])
        scope = inference.infer_scope(rows, items_json, source_id=PUBLISHED_ID)
        self.assertEqual(scope["chapters"], [1])
        self.assertEqual(scope["method"], "companion_tags")
        self.assertEqual(scope["by_chapter"]["1"]["tagged"], 8)
        self.assertEqual(scope["by_chapter"]["3"]["tagged"], 0)

    def test_scope_ignores_rows_of_another_source(self):
        items_json = {"items": [{"item": "Definition 1.1.1", "chapter": 1}]}
        rows = [{"item": f"Definition 1.1.{i}", "source_id": DRAFT_ID} for i in range(1, 9)]
        self.assertEqual(inference.infer_scope(rows, items_json, source_id=PUBLISHED_ID)["chapters"],
                         [])

    def test_errata_url_is_ranked_registry_first(self):
        entry = Registry.load().get(PUBLISHED_ID)
        url, confidence, method = inference.infer_errata_url([], entry)
        self.assertEqual(confidence, 1.0)
        self.assertEqual(method, "registry")
        self.assertIn("an-introduction-to-measure-theory", url)

        claim = discovery.SourceClaim(signal="citation", path="README.md",
                                      book_page_url="https://example.org/book/")
        url, confidence, method = inference.infer_errata_url([claim], None)
        self.assertEqual((url, method), ("https://example.org/book/", "repo_book_page"))
        self.assertEqual(confidence, 0.8)

    def test_confirm_errata_reads_the_fetched_page(self):
        self.assertTrue(inference.confirm_errata("<h2>Errata</h2>"))
        self.assertFalse(inference.confirm_errata("<h2>Reviews</h2>"))


# ---------------------------------------------------------------------------
# discovery (fixture repos; no network, no PDFs)
# ---------------------------------------------------------------------------

def make_ledger_repo(root: Path) -> Path:
    """A fixture carrying measure_theory's signals (S2, S3, S6, S7)."""
    repo = root / "measure_theory"
    (repo / "pdf-overlay" / "data").mkdir(parents=True)
    (repo / "pdf-overlay" / "data" / "chapter1.json").write_text('{"rows": []}')
    (repo / "pdf-overlay" / "official-source.json").write_text(json.dumps({
        "bytes": 1338765,
        "canonical_pdf_url": "https://terrytao.wordpress.com/wp-content/uploads/2012/12/"
                             "gsm-126-tao5-measure-book.pdf",
        "final_pdf_url": "https://terrytao.wordpress.com/wp-content/uploads/2012/12/"
                         "gsm-126-tao5-measure-book.pdf",
        "legacy_page_url": "https://terrytao.wordpress.com/books/"
                           "an-introduction-to-measure-theory/",
        "sha256": PUBLISHED_SHA,
    }))
    (repo / "source-formalization-ledger.json").write_text(json.dumps({
        "source": {"kind": "pdf", "path": "sources/gsm-126-tao5-measure-book.pdf",
                   "revision": "Terence Tao, An Introduction to Measure Theory, author's "
                               "preliminary AMS version; PDF metadata modified 2012-12-04",
                   "sha256": PUBLISHED_SHA}}))
    (repo / "README.md").write_text(
        "# measure_theory\n\nThe book page (errata live there):\n"
        "<https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/>\n"
        "The PDF: <https://terrytao.wordpress.com/wp-content/uploads/2012/12/"
        "gsm-126-tao5-measure-book.pdf>\n"
        "The lake manifest sha is "
        "38aaa20fbcd76cf48f790617d5169a4dd4047e1ee63b262e41c5121048ca6c9b, which is "
        "not a book at all.\n")
    return repo


def make_docstring_repo(root: Path) -> Path:
    """A fixture carrying tao-measure-lean's signals (S1, S4, S5, S6)."""
    repo = root / "tao-measure-lean"
    (repo / "Companion").mkdir(parents=True)
    (repo / "sources").mkdir(parents=True)
    (repo / "sources" / "tao-measure-theory.pdf").write_bytes(b"%PDF-1.4 not a real pdf")
    (repo / "formalization.yaml").write_text(
        "version: v0.3\n"
        "sources:\n"
        "- title: An Introduction to Measure Theory\n"
        "  authors:\n"
        "  - Terence Tao\n"
        "  id: 'ISBN 978-0-8218-6919-2 (AMS GSM 126); free preprint: "
        "https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/'\n"
        "  type: textbook\n"
        "  license: 'AMS copyright; free preprint version available on the author''s site'\n")
    (repo / "graph.json").write_text(json.dumps({
        "metadata": {"sources": [{"file": "sources/tao-measure-theory.pdf",
                                  "title": "An Introduction to Measure Theory (Tao)",
                                  "format": "pdf",
                                  "page_offset_note": "PDF page = book page + 16; scope = "
                                                      "Chapter 1"}]},
        "nodes": []}))
    (repo / "CLAUDE.md").write_text(
        "- Source PDF is **not** committed (copyrighted). Two editions exist locally.\n"
        "  `~/Downloads/Measure.Theory.Tao.pdf` (= `sources/tao-measure-theory.pdf`,\n"
        "  sha256 `62d95658…`, created 2011-01-24) is an earlier *draft*. The published\n"
        "  book's pagination is the official PDF (sha256 `64498329…`).\n"
        "  Both satisfy PDF page = book page + 16, but ~65 numbered items sit one printed\n"
        "  page earlier in the published book. **Book-page anchors in docstrings follow the\n"
        "  PUBLISHED pagination** (corrected 2026-09-04).\n")
    for i in range(25):
        (repo / "Companion" / f"File{i}.lean").write_text(
            f'/-- `Definition 1.1.{i + 1}` (book p. {20 + i}) -/\ntheorem foo{i} : True := trivial\n')
    return repo


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.ledger_repo = make_ledger_repo(self.root)
        self.docstring_repo = make_docstring_repo(self.root)
        self.registry = Registry.load()

    def tearDown(self):
        self.tmp.cleanup()

    # -- adapter detection (S4.2) --------------------------------------
    def test_detect_adapter_on_both_fixtures(self):
        kind, score, evidence = discovery.detect_adapter(self.ledger_repo)
        self.assertEqual(kind, "attribution_table")
        self.assertGreaterEqual(score, 0.9)
        self.assertIn("official-source.json", evidence["reason"])

        kind, score, _ = discovery.detect_adapter(self.docstring_repo)
        self.assertEqual(kind, "docstring_tags")
        self.assertGreaterEqual(score, 0.5)

    def test_an_unreadable_repo_is_refused_not_guessed(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(CfError) as ctx:
            discovery.detect_adapter(empty)
        self.assertIn("--adapter", getattr(ctx.exception, "hint", ""))

    # -- the seven signals (S4.3) --------------------------------------
    def test_every_signal_runs_for_every_adapter_kind(self):
        """tao-measure-lean carries BOTH a formalization.yaml and a graph.json."""
        signals = {c.signal for c in discovery.discover_sources(self.docstring_repo,
                                                               adapter="docstring_tags")}
        self.assertIn("formalization_yaml", signals)
        self.assertIn("graph_json", signals)
        self.assertIn("prose", signals)
        self.assertIn("filesystem", signals)

    def test_the_ledger_sha_is_read_and_the_offset_note_is_parsed(self):
        claims = discovery.discover_sources(self.ledger_repo)
        ledger = next(c for c in claims if c.signal == "ledger_source")
        self.assertEqual(ledger.sha256, PUBLISHED_SHA)
        self.assertEqual(ledger.pdf_hints, ("sources/gsm-126-tao5-measure-book.pdf",))

        graph = next(c for c in discovery.discover_sources(self.docstring_repo)
                     if c.signal == "graph_json")
        self.assertEqual(graph.offset_hint, 16)

    def test_a_truncated_sha_is_a_prefix_never_an_identity(self):
        claims = discovery.discover_sources(self.docstring_repo)
        prose = [c for c in claims if c.signal == "prose" and c.sha256_prefix]
        self.assertEqual(sorted(c.sha256_prefix for c in prose), ["62d95658", "64498329"])
        self.assertTrue(all(not c.sha256 for c in prose))

    def test_the_anchor_sentence_is_picked_up(self):
        claims = discovery.discover_sources(self.docstring_repo)
        hints = [c.anchor_hint for c in claims if c.anchor_hint]
        self.assertTrue(any("PUBLISHED" in h for h in hints), hints)

    def test_merging_finds_exactly_the_two_tao_editions(self):
        merged = discovery.merge_claims(
            discovery.discover_sources(self.docstring_repo), self.registry)
        prefixes = sorted(c.sha256_prefix for c in merged if c.sha256_prefix)
        self.assertEqual(prefixes, ["62d95658", "64498329"])
        self.assertEqual(len(merged), 2)

    def test_a_shared_book_page_link_does_not_become_a_third_source(self):
        merged = discovery.merge_claims(
            discovery.discover_sources(self.ledger_repo), self.registry)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].sha256, PUBLISHED_SHA)

    # -- resolution (S4.4, S4.7) ---------------------------------------
    def test_resolve_claims_registers_both_editions_from_the_registry(self):
        ws = _fresh_workspace(self.root / "ws")
        result = discovery.resolve_claims(
            discovery.discover_sources(self.docstring_repo), repo=self.docstring_repo,
            workspace=ws, registry=self.registry, library_paths=[])
        self.assertEqual(sorted(result.source_ids()), [DRAFT_ID, PUBLISHED_ID])
        for body in result.sources:
            self.assertEqual(body["registry"], "skill")
            self.assertTrue(body["confirmed"])
            self.assertEqual(body["offset"], 16)
            self.assertIn("errata_url", body)

    def test_a_prose_sha_that_resolves_to_nothing_is_reported_not_adopted(self):
        """measure_theory's README quotes the sha of lake-manifest.json."""
        ws = _fresh_workspace(self.root / "ws2")
        result = discovery.resolve_claims(
            discovery.discover_sources(self.ledger_repo), repo=self.ledger_repo,
            workspace=ws, registry=self.registry, library_paths=[])
        self.assertEqual(result.source_ids(), [PUBLISHED_ID])

    def test_a_source_nobody_catalogued_is_unconfirmed(self):
        repo = self.root / "unknown"
        repo.mkdir()
        (repo / "source-formalization-ledger.json").write_text(json.dumps({
            "source": {"kind": "pdf", "path": "sources/smith.pdf",
                       "revision": "Ann Smith, A First Course in Real Analysis, 2e",
                       "sha256": "a" * 64}}))
        ws = _fresh_workspace(self.root / "ws3")
        result = discovery.resolve_claims(
            discovery.discover_sources(repo), repo=repo, workspace=ws,
            registry=self.registry, library_paths=[])
        self.assertEqual(len(result.sources), 1)
        body = result.sources[0]
        self.assertEqual(body["registry"], "local")
        self.assertFalse(body["confirmed"])
        self.assertEqual(body["sha256"], "a" * 64)

    def test_the_pdf_search_order_prefers_what_the_repo_names(self):
        claim = discovery.SourceClaim(signal="filesystem", path="x",
                                      pdf_hints=("sources/tao-measure-theory.pdf",))
        order = discovery.search_order(claim, repo=self.docstring_repo, workspace=None,
                                       library_paths=[])
        self.assertTrue(order)
        first_path, where, rank = order[0]
        self.assertEqual(first_path.name, "tao-measure-theory.pdf")
        self.assertEqual((where, rank), ("repo_declared", 0))

    def test_a_contradicting_sha_is_a_warning_not_an_adoption(self):
        """The batch-1 mistake: adopting a file that is a different edition."""
        claim = discovery.SourceClaim(signal="ledger_source", path="x", sha256=PUBLISHED_SHA,
                                      pdf_hints=("sources/tao-measure-theory.pdf",))
        warnings: list[str] = []
        found = discovery.locate_pdf(claim, repo=self.docstring_repo, workspace=None,
                                     registry=self.registry, library_paths=[],
                                     warnings=warnings)
        self.assertIsNone(found)
        self.assertTrue(any("edition_mismatch" in w for w in warnings), warnings)

    def test_a_download_is_refused_without_a_licence_that_permits_it(self):
        ws = _fresh_workspace(self.root / "ws4")
        warnings: list[str] = []
        claim = discovery.SourceClaim(signal="prose", path="x", sha256_prefix="62d95658")
        found = discovery.locate_pdf(claim, repo=self.root / "nowhere", workspace=ws,
                                     registry=self.registry, library_paths=[],
                                     download=True, entry=self.registry.get(DRAFT_ID),
                                     warnings=warnings)
        self.assertIsNone(found)
        self.assertTrue(any("download refused" in w for w in warnings), warnings)

    def test_render_names_every_inference_and_its_confidence(self):
        ws = _fresh_workspace(self.root / "ws5")
        result = discovery.resolve_claims(
            discovery.discover_sources(self.docstring_repo), repo=self.docstring_repo,
            workspace=ws, registry=self.registry, library_paths=[])
        result.adapter, result.adapter_confidence = "docstring_tags", 0.95
        text = result.render()
        self.assertIn("sources discovered", text)
        self.assertIn(PUBLISHED_ID, text)
        self.assertIn("offset   16", text)
        self.assertIn("confidence", text)

    # -- the anchor edition (S4.6) -------------------------------------
    def test_one_candidate_is_the_anchor(self):
        ws = _fresh_workspace(self.root / "ws6")
        sid, confidence, evidence = discovery.choose_anchor_source(
            self.docstring_repo, [], [PUBLISHED_ID], workspace=ws)
        self.assertEqual((sid, confidence), (PUBLISHED_ID, 1.0))
        self.assertEqual(evidence["reason"], "single_candidate")

    def test_the_mechanical_check_decides_between_two_editions(self):
        ws = _two_edition_workspace(self.root / "ws7")
        rows = [{"item": "Lemma 1.6.1", "page_anchor": 100},
                {"item": "Lemma 1.6.2", "page_anchor": 101},
                {"item": "Lemma 1.6.3", "page_anchor": 102},
                {"item": "Definition 1.1.1", "page_anchor": 5}]
        sid, confidence, evidence = discovery.choose_anchor_source(
            self.docstring_repo, rows, [PUBLISHED_ID, DRAFT_ID], workspace=ws)
        self.assertEqual(sid, PUBLISHED_ID)
        self.assertGreaterEqual(confidence, 0.8)
        self.assertEqual(evidence["agreement"][PUBLISHED_ID],
                         {"ok": 4, "mismatched": 0, "agreement": 1.0})
        # Definition 1.1.1 sits on the same printed page in BOTH editions, so the
        # draft is not at zero -- only the three S1.6 items separate them, which
        # is exactly why S4.6 needs a MARGIN and not just a winner.
        self.assertEqual(evidence["agreement"][DRAFT_ID],
                         {"ok": 1, "mismatched": 3, "agreement": 0.25})

    def test_a_prose_hint_that_contradicts_the_anchors_is_refused(self):
        ws = _two_edition_workspace(self.root / "ws8")
        rows = [{"item": "Lemma 1.6.1", "page_anchor": 100},
                {"item": "Lemma 1.6.2", "page_anchor": 101},
                {"item": "Lemma 1.6.3", "page_anchor": 102},
                {"item": "Definition 1.1.1", "page_anchor": 5}]
        with self.assertRaises(CfError) as ctx:
            discovery.choose_anchor_source(
                self.docstring_repo, rows, [PUBLISHED_ID, DRAFT_ID], workspace=ws,
                hints=["anchors follow the 2011 draft pagination"])
        self.assertIn("--anchor-source", getattr(ctx.exception, "hint", ""))

    def test_no_anchors_to_score_and_no_hint_is_a_refusal(self):
        ws = _two_edition_workspace(self.root / "ws9")
        with self.assertRaises(CfError):
            discovery.choose_anchor_source(self.docstring_repo, [], [PUBLISHED_ID, DRAFT_ID],
                                           workspace=ws)

    def test_a_prose_hint_alone_decides_at_low_confidence(self):
        ws = _two_edition_workspace(self.root / "ws10")
        sid, confidence, evidence = discovery.choose_anchor_source(
            self.docstring_repo, [], [PUBLISHED_ID, DRAFT_ID], workspace=ws,
            hints=["Book-page anchors in docstrings follow the PUBLISHED pagination"])
        self.assertEqual(sid, PUBLISHED_ID)
        self.assertEqual(confidence, 0.6)
        self.assertEqual(evidence["reason"], "prose_hint_only")


def _fresh_workspace(root: Path) -> Workspace:
    """A minimal valid workspace on disk (no companions, no sources yet)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / WORKSPACE_FILE).write_text(
        "version: 2\nworkspace:\n  name: t\ncompanions: {}\nsources: {}\n"
        "hosts:\n  allowlist: []\nlibrary_paths: []\n")
    return Workspace.load(root)


def _two_edition_workspace(root: Path) -> Workspace:
    """A workspace with both Tao editions PREPARED, differing by one page in S1.6."""
    ws = _fresh_workspace(root)
    for sid, shift in ((PUBLISHED_ID, 0), (DRAFT_ID, 1)):
        ws.upsert_source(sid, sha256=(PUBLISHED_SHA if sid == PUBLISHED_ID else DRAFT_SHA),
                         title="An Introduction to Measure Theory", edition=sid,
                         offset=16, offset_confidence=0.99, kinds=["Definition", "Lemma"],
                         role="audited", confirmed=True)
        rows = [{"item": "Definition 1.1.1", "page": 5, "pdf_page": 21, "chapter": 1}]
        rows += [{"item": f"Lemma 1.6.{i}", "page": 99 + i + shift, "pdf_page": 115 + i + shift,
                  "chapter": 1} for i in range(1, 4)]
        path = ws.paths.source_items(sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"source_id": sid, "offset": 16, "items": rows}))
    ws.save()
    return ws


# ---------------------------------------------------------------------------
# yaml, hashing, templates, vocabulary
# ---------------------------------------------------------------------------

class TestYamlFallback(unittest.TestCase):
    SAMPLE = WORKSPACE_MIN.format(repo_a="/repo/a", repo_b="/repo/b",
                                  published=PUBLISHED_SHA, draft=DRAFT_SHA) + """\
nested:
  a:
    b: 1
    c: [1, 2, 3]
  d: "quoted: value"
  e: null
  f: false
block: |
  line one
  line two
"""

    def test_minimal_reader_matches_pyyaml(self):
        if not yamlio.HAVE_PYYAML:
            self.skipTest("PyYAML not installed")
        import yaml
        self.assertEqual(yamlio.load_yaml_minimal(self.SAMPLE), yaml.safe_load(self.SAMPLE))

    def test_minimal_reader_standalone(self):
        data = yamlio.load_yaml_minimal(self.SAMPLE)
        self.assertEqual(data["sources"][PUBLISHED_ID]["offset"], 16)
        self.assertEqual(data["nested"]["a"]["c"], [1, 2, 3])
        self.assertEqual(data["nested"]["d"], "quoted: value")
        self.assertIsNone(data["nested"]["e"])
        self.assertIs(data["nested"]["f"], False)
        self.assertEqual(data["block"], "line one\nline two\n")
        self.assertEqual(data["companions"]["ours"]["sources"], [PUBLISHED_ID])

    def test_the_shipped_registry_parses_identically_under_both_readers(self):
        """It uses nested maps and `>-` folded scalars, which the fallback must
        read the same way PyYAML does -- the registry is the skill's memory."""
        text = (SKILL / "registry" / "sources.yaml").read_text()
        minimal = yamlio.load_yaml_minimal(text)
        self.assertIn(PUBLISHED_ID, minimal["sources"])
        entry = minimal["sources"][PUBLISHED_ID]
        self.assertEqual(entry["page_map_to"][DRAFT_ID]["exception_shift"], 1)
        self.assertEqual(entry["licence"]["fetch"], "author_site")
        self.assertIn("AMS copyright", entry["licence"]["note"])
        self.assertEqual(entry["aliases"]["filenames"], ["gsm-126-tao5-measure-book.pdf"])
        if yamlio.HAVE_PYYAML:
            import yaml
            self.assertEqual(minimal, yaml.safe_load(text))

    def test_a_written_workspace_means_the_same_thing_to_both_readers(self):
        """PyYAML types a bare `2026-09-16` as a date; the fallback keeps a string.

        `cf onboard` rewrites workspace.yaml through `dump_yaml`, so an unquoted
        date would make the SAME file parse differently depending on whether
        PyYAML happens to be installed on the box reading it.
        """
        text = yamlio.dump_yaml({"workspace": {"name": "t", "created": "2026-09-16"}})
        self.assertIn('created: "2026-09-16"', text)
        self.assertEqual(yamlio.load_yaml_minimal(text)["workspace"]["created"], "2026-09-16")
        if yamlio.HAVE_PYYAML:
            import yaml
            self.assertEqual(yaml.safe_load(text), yamlio.load_yaml_minimal(text))

    def test_dump_round_trips_under_both_readers(self):
        obj = {"a": 1, "b": [1, 2], "c": {"d": "x: y"}, "e": None, "f": True,
               "g": "multi\nline\n", "h": {}, "i": []}
        text = yamlio.dump_yaml(obj)
        self.assertEqual(yamlio.load_yaml_minimal(text), obj)
        if yamlio.HAVE_PYYAML:
            import yaml
            self.assertEqual(yaml.safe_load(text), obj)

    def test_anchors_are_refused_not_mangled(self):
        with self.assertRaises(CfError):
            yamlio.load_yaml_minimal("a: &anchor 1\nb: *anchor\n")

    def test_markdown_emphasis_in_a_block_scalar_is_not_an_alias(self):
        text = "description: |\n  fails *because of* a junk value\n  and *that* is prose\nid: x\n"
        data = yamlio.load_yaml_minimal(text)
        self.assertIn("*because of*", data["description"])
        self.assertEqual(data["id"], "x")

    #: rendered as a task file would be: the two BLOCK values keep the two-space
    #: indent `description: |` requires, because a flush-left substitution would
    #: test a file the skill never writes.
    TASK_VALUES = {
        "task_id": "definition-1-1-1", "attack_label": "vacuity",
        "item": "Definition 1.1.1", "priority": "50", "depends_on": "[]",
        "run_id": "attack-vac-ours",
        "source_id": PUBLISHED_ID, "source_sha": "0" * 64,
        "kinds_inline": "CONTENT|VACUITY", "companion": "ours",
        "gate_command": "python3 /gate/verify_findings.py", "printed_page": "12",
        "repos_inline": "ours, adam", "repo_count": "2",
        "decls_inline": "ours:Foo, adam:Bar",
    }

    @staticmethod
    def _indent(text: str) -> str:
        return "\n".join(("  " + ln) if ln.strip() else "" for ln in text.splitlines())

    def test_generated_task_files_parse_identically_under_both_readers(self):
        """Every ``runs/*/tasks/*.yaml`` the skill writes must survive the fallback.

        A worker host may have no PyYAML, so the minimal reader is what actually
        parses these there; PyYAML is only the oracle.
        """
        if not yamlio.HAVE_PYYAML:
            self.skipTest("needs PyYAML as the oracle")
        import yaml

        from plan import make_tasks as mt  # noqa: F401  (import check only)

        probe = (SKILL / "templates" / "prompts" / "attack-vac.md.tmpl").read_text(encoding="utf-8")
        for key, value in {
            "item": "Definition 1.1.1", "companion": "ours", "decls_inline": "ours:Foo, ours:Bar",
            "peers_line": "Peers: adam", "total_functions_sentence": "Lean functions are total.",
        }.items():
            probe = probe.replace("{{" + key + "}}", value)
        values = dict(self.TASK_VALUES)
        values["probe_block"] = self._indent(probe)
        values["decls_block"] = self._indent("ours -> Foo\nadam -> Bar")

        for path in sorted((SKILL / "templates" / "tasks").glob("*.tmpl")):
            rendered = path.read_text(encoding="utf-8")
            for key, value in values.items():
                rendered = rendered.replace("{{" + key + "}}", value)
            with self.subTest(path=path.name):
                self.assertNotIn("{{", rendered)
                self.assertEqual(yamlio.load_yaml_minimal(rendered), yaml.safe_load(rendered))


class TestHashing(unittest.TestCase):
    def test_finding_id_is_order_independent_and_stable(self):
        a = hashing.finding_id("run-1", PUBLISHED_ID, "Exercise 1.1.1", "CONTENT", ["b:y", "a:x"])
        b = hashing.finding_id("run-1", PUBLISHED_ID, "Exercise 1.1.1", "CONTENT",
                               ["a:x", "b:y", "a:x"])
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)
        self.assertNotEqual(a, hashing.finding_id("run-2", PUBLISHED_ID, "Exercise 1.1.1",
                                                  "CONTENT", ["a:x"]))
        self.assertNotEqual(a, hashing.finding_id("run-1", PUBLISHED_ID, "Exercise 1.1.1",
                                                  "VACUITY", ["a:x", "b:y"]))

    def test_the_same_item_of_two_sources_is_two_findings(self):
        """S6.1: an item is the pair (source_id, item), so ids cannot collide."""
        a = hashing.finding_id("r", PUBLISHED_ID, "Exercise 1.3.19", "CONTENT", ["ours:x"])
        b = hashing.finding_id("r", DRAFT_ID, "Exercise 1.3.19", "CONTENT", ["ours:x"])
        self.assertNotEqual(a, b)

    def test_the_whitespace_variant_still_hashes_to_the_canonical_item(self):
        self.assertEqual(
            hashing.finding_id("r", PUBLISHED_ID, "Exercise    1.3.19", "CONTENT", ["ours:a"]),
            hashing.finding_id("r", PUBLISHED_ID, "Exercise 1.3.19", "CONTENT", ["ours:a"]))

    def test_sha256_text_and_json(self):
        self.assertEqual(hashing.sha256_text(""),
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        self.assertEqual(hashing.sha256_json({"a": 1, "b": 2}), hashing.sha256_json({"b": 2, "a": 1}))

    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.txt"
            p.write_text("hello")
            self.assertEqual(hashing.sha256_file(p), hashing.sha256_text("hello"))


class TestTemplates(unittest.TestCase):
    def test_render_substitutes_and_reports_missing(self):
        self.assertEqual(templates.render("a {{ x }} b", {"x": 1}), "a 1 b")
        with self.assertRaises(CfError):
            templates.render("{{ missing }}", {})
        self.assertEqual(templates.render("{{ missing }}", {}, allow_missing=True), "{{ missing }}")

    def test_shipped_templates_render(self):
        text = templates.render_template("workspace.yaml.example", {
            "workspace_name": "t", "created": '"2026-01-01"'})
        data = yamlio.load_yaml_minimal(text)
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["companions"], {})
        self.assertEqual(data["sources"], {})
        self.assertEqual(data["hosts"]["allowlist"], [])
        self.assertIs(data["policy"]["publish_unchallenged"], False)
        self.assertIs(data["policy"]["allow_download"], False)
        self.assertIn("~/Downloads", data["library_paths"])
        self.assertEqual(data["defaults"]["source_access"], "text")

    def test_the_adapter_template_names_the_source(self):
        text = templates.render_template("adapter.yaml.example", {
            "name": "ours", "repo": "/r", "commit": "null", "adapter": "docstring_tags",
            "granularity": "item", "glob": '"**/*.lean"', "anchor": "docstring",
            "write_policy": "read_only", "owner": '"jack"',
            "source_default": PUBLISHED_ID, "source_map": ""})
        data = yamlio.load_yaml_minimal(text)
        self.assertEqual(data["sources"]["default"], PUBLISHED_ID)
        self.assertEqual(data["sources"]["map"], [])


class TestVocabulary(unittest.TestCase):
    def test_kinds_per_mode(self):
        self.assertNotIn(vocab.PAGE_ANCHOR, vocab.kinds_for_mode(vocab.DIFFERENTIAL))
        self.assertNotIn(vocab.COVERAGE, vocab.kinds_for_mode(vocab.ATTACK_HUNT))
        self.assertIn(vocab.VACUITY, vocab.kinds_for_mode(vocab.ATTACK_VAC))
        self.assertIn(vocab.PAGE_ANCHOR, vocab.MECHANICAL_ONLY_KINDS)
        with self.assertRaises(CfError):
            vocab.kinds_for_mode("attack")

    def test_witness_gate_applies_to_attack_modes_only(self):
        self.assertTrue(vocab.witness_required(vocab.ATTACK_VAC, vocab.CONTENT))
        self.assertFalse(vocab.witness_required(vocab.DIFFERENTIAL, vocab.CONTENT))
        self.assertFalse(vocab.witness_required(vocab.ATTACK_HUNT, vocab.SUSPECTED_ERRATA))

    def test_v1_aliases(self):
        self.assertEqual(vocab.canonical_match_verdict("NO_WEAKNESS_FOUND"), vocab.NO_DEFECT_FOUND)
        self.assertEqual(vocab.canonical_match_verdict("no_defect_found"), vocab.NO_DEFECT_FOUND)
        with self.assertRaises(CfError):
            vocab.canonical_match_verdict("PROBABLY_FINE")

    def test_resolutions(self):
        for good in ["fixed:PR#8", "in_progress:fix/batch2", "refuted", "argued_correct",
                     "out_of_scope"]:
            self.assertEqual(vocab.check_resolution(good), good)
        for bad in ["fixed", "fixed:", "done", ""]:
            with self.assertRaises(CfError):
                vocab.check_resolution(bad)

    def test_every_ledger_verdict_maps_to_the_viewer(self):
        self.assertEqual(set(vocab.LEDGER_VERDICTS), set(vocab.LEDGER_TO_VIEWER))
        for viewer in vocab.LEDGER_TO_VIEWER.values():
            self.assertTrue(viewer in vocab.VIEWER_VERDICTS or viewer in vocab.VIEWER_STATUS)

    def test_the_source_vocabulary(self):
        self.assertEqual(vocab.SOURCE_ROLES, {"audited", "reference"})
        self.assertEqual(vocab.LICENCE_FETCH, {"none", "author_site", "open"})
        self.assertEqual(tuple(vocab.DISCOVERY_SIGNALS), discovery.SIGNALS)
        self.assertIn("running_head_mode", vocab.OFFSET_METHODS)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

class TestCli(unittest.TestCase):
    def setUp(self):
        import cf
        self.cf = cf
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovery_registers_the_core_commands(self):
        _, mapping = self.cf.build_parser()
        for cmd in ("init", "status", "onboard", "sources"):
            self.assertIn(cmd, mapping, f"{cmd} not discovered")

    def test_cf_book_is_gone(self):
        """`cf sources refresh` replaces it: there is no single book any more."""
        _, mapping = self.cf.build_parser()
        self.assertNotIn("book", mapping)

    def test_init_takes_no_source_flags(self):
        root = Path(self.tmp.name) / "ws"
        rc = self.cf.main(["init", str(root), "--no-git", "-q"])
        self.assertEqual(rc, 0)
        self.assertTrue((root / WORKSPACE_FILE).exists())
        self.assertTrue((root / "registry.yaml").exists())
        self.assertTrue((root / "companions").is_dir())
        self.assertTrue((root / "sources").is_dir())
        ignore = (root / ".gitignore").read_text()
        self.assertIn("sources/", ignore)
        self.assertNotIn("\nbook/", ignore)
        self.assertNotIn("\ncorpus/", ignore)
        self.assertEqual(self.cf.main(["init", str(root), "--no-git", "-q"]), 0)  # idempotent
        self.assertEqual(self.cf.main(["status", "--workspace", str(root), "-q"]), 0)
        self.assertEqual(self.cf.main(["sources", "list", "--workspace", str(root), "-q"]), 0)

    def test_init_refuses_the_old_book_flags(self):
        root = Path(self.tmp.name) / "ws2"
        with self.assertRaises(SystemExit):
            self.cf.main(["init", str(root), "--book", "b", "--offset", "16", "--no-git"])

    def test_status_on_an_unknown_companion_is_an_error(self):
        root = Path(self.tmp.name) / "ws3"
        self.cf.main(["init", str(root), "--no-git", "-q"])
        with self.assertRaises(CfError):
            self.cf.main(["status", "--workspace", str(root), "--companion", "ghost", "-q"])

    def test_init_refuses_to_write_inside_the_skill_repo(self):
        from cflib.paths import skill_root
        with self.assertRaises(CfError):
            self.cf.main(["init", str(skill_root() / "nope"), "--no-git"])


# ---------------------------------------------------------------------------
# the adapter interface (scripts/adapters/ subclasses this)
# ---------------------------------------------------------------------------

class _FakeAdapter:
    """Stands in for scripts/adapters/ while exercising the onboard path.

    It is a DUCK, not a subclass, on purpose: ``cflib.adapters_api.load`` is a
    seam a third-party adapter may fill, so the stub asserts what the seam
    actually requires -- ``bind_default_source`` (which source unmapped rows
    belong to), ``dry_run(repo, sources=)`` and ``extract(repo, sources=)``
    yielding rows that already carry their own ``source_id`` / ``source_sha``.
    """

    counts = {"declarations": 2207, "tagged": 1496, "items": 519,
              "rejects": [{"raw": "Corollary (Lemma 1.2.5)", "reason": "no_number"}]}
    rows = [{"item": "Definition 1.1.1", "item_raw": "Definition 1.1.1", "decl": "Foo",
             "file": "A.lean", "line": 3, "statement": "theorem foo", "repo": "ours",
             "page_anchor": 5}]

    def __init__(self, workspace=None):
        self.workspace = workspace
        self.default_source_id = None

    def bind_default_source(self, source_id):
        self.default_source_id = source_id

    def _bind(self, sources):
        bound = dict(sources or {})
        source_id = self.default_source_id or (next(iter(bound)) if bound else None)
        return source_id, bound.get(source_id)

    def dry_run(self, repo=None, *, sources=None):
        source_id, _sha = self._bind(sources)
        return {**self.counts, "sources": [], "rows_by_source": {source_id: len(self.rows)}}

    def extract(self, repo=None, *, sources=None):
        source_id, sha = self._bind(sources)
        for row in self.rows:
            yield {**row, "source_id": source_id, "source_sha": sha}

    def key_inputs(self):
        return {"adapter_kind": "docstring_tags"}


class TestAdapterApi(unittest.TestCase):
    def test_a_row_must_name_its_source(self):
        from cflib.adapters_api import ROW_REQUIRED_FIELDS, Adapter

        self.assertIn("source_id", ROW_REQUIRED_FIELDS)
        self.assertIn("source_sha", ROW_REQUIRED_FIELDS)
        self.assertNotIn("book_sha", ROW_REQUIRED_FIELDS)

        class Demo(Adapter):
            kind = "custom"

            def dry_run(self, repo=None):
                return {}

            def extract(self, repo=None, *, sources=None):
                return iter(())

        row = {f: "x" for f in ROW_REQUIRED_FIELDS}
        self.assertEqual(Demo({"name": "d"}).validate_row(row), row)
        with self.assertRaises(CfError):
            Demo({"name": "d"}).validate_row({"item": "Exercise 1.1.1"})

    def test_source_for_uses_the_default_then_the_map(self):
        from cflib.adapters_api import Adapter

        class Demo(Adapter):
            kind = "custom"

            def dry_run(self, repo=None):
                return {}

            def extract(self, repo=None, *, sources=None):
                return iter(())

        one = Demo({"name": "d", "sources": {"default": PUBLISHED_ID}})
        self.assertEqual(one.source_for(file=Path("A.lean"), tag="Lemma 1.1.1", decl="x"),
                         PUBLISHED_ID)
        two = Demo({"name": "d", "sources": {
            "default": PUBLISHED_ID,
            "map": [{"source": DRAFT_ID, "files": ["Chapter2/*.lean"]},
                    {"source": DRAFT_ID, "tag_prefix": "AB:"}]}})
        self.assertEqual(two.source_for(file=Path("Chapter2/B.lean"), tag="Lemma 1.1.1", decl="x"),
                         DRAFT_ID)
        self.assertEqual(two.source_for(file=Path("A.lean"), tag="AB: Lemma 1.1.1", decl="x"),
                         DRAFT_ID)
        self.assertEqual(two.source_for(file=Path("A.lean"), tag="Lemma 1.1.1", decl="x"),
                         PUBLISHED_ID)
        none = Demo({"name": "d"})
        with self.assertRaises(CfError):
            none.source_for(file=Path("A.lean"), tag="Lemma 1.1.1", decl="x")

    def test_key_stability(self):
        from cflib.adapters_api import Adapter

        class Demo(Adapter):
            kind = "custom"

            def dry_run(self, repo=None):
                return {}

            def extract(self, repo=None, *, sources=None):
                return iter(())

        a = Demo({"name": "d", "glob": "x"})
        b = Demo({"name": "d", "glob": "x"})
        c = Demo({"name": "d", "glob": "y"})
        self.assertEqual(a.key(), b.key())
        self.assertNotEqual(a.key(), c.key())
        self.assertTrue(a.key().startswith("sha256:"))


# ---------------------------------------------------------------------------
# cf onboard, end to end (fixture repo, fake adapter, no PDF, no network)
# ---------------------------------------------------------------------------

class TestOnboardIntegration(unittest.TestCase):
    """`cf onboard <repo>` with NO flags must discover, register and extract."""

    def setUp(self):
        import cf
        import types
        self.cf = cf
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.ws = self.root / "ws"
        self.repo = make_ledger_repo(self.root)
        self.cf.main(["init", str(self.ws), "--no-git", "-q"])
        fake = types.ModuleType("adapters")
        fake.load = lambda adapter_yaml, workspace=None: _FakeAdapter(workspace)
        self._saved = sys.modules.get("adapters")
        sys.modules["adapters"] = fake

    def tearDown(self):
        if self._saved is not None:
            sys.modules["adapters"] = self._saved
        else:
            sys.modules.pop("adapters", None)
        self.tmp.cleanup()

    def _onboard(self, *extra):
        return self.cf.main(["onboard", str(self.repo), "--workspace", str(self.ws),
                             "--no-errata", "-q", *extra])

    def test_onboard_discovers_the_source_without_a_single_flag(self):
        self.assertEqual(self._onboard(), 0)
        ws = Workspace.load(self.ws)
        self.assertEqual(ws.source_ids(), [PUBLISHED_ID])
        source = ws.source(PUBLISHED_ID)
        self.assertEqual(source.sha256, PUBLISHED_SHA)
        self.assertEqual(source.offset, 16)             # from the registry, never typed
        self.assertTrue(source.confirmed)
        self.assertIn("terrytao.wordpress.com", source.errata_url)
        companion = ws.companion("measure-theory")
        self.assertEqual(companion.adapter, "attribution_table")
        self.assertEqual(companion.sources, (PUBLISHED_ID,))
        self.assertEqual(companion.anchor_source, PUBLISHED_ID)
        self.assertEqual(companion.discovered["anchor_confidence"], 1.0)
        self.assertIn("source-formalization-ledger.json#source",
                      companion.discovered["signals"])

    def test_rows_carry_the_source_and_its_sha(self):
        self._onboard()
        ws = Workspace.load(self.ws)
        rows = [json.loads(line) for line in
                ws.paths.statements_jsonl("measure-theory").read_text().splitlines() if line]
        body = [r for r in rows if "_meta" not in r]
        self.assertTrue(body)
        for row in body:
            self.assertEqual(row["source_id"], PUBLISHED_ID)
            self.assertEqual(row["source_sha"], PUBLISHED_SHA)
            self.assertNotIn("book_sha", row)

    def test_state_records_the_sha_per_source(self):
        self._onboard()
        ws = Workspace.load(self.ws)
        st = state_mod.read_state(ws, "measure-theory")
        self.assertEqual(st["anchor_source"], PUBLISHED_ID)
        self.assertEqual(st["sources"][PUBLISHED_ID]["sha"], PUBLISHED_SHA)
        self.assertEqual(st["adapter"]["kind"], "attribution_table")
        self.assertEqual(st["adapter"]["dry_run"]["tagged"], 1496)

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self._onboard("--dry-run"), 0)
        ws = Workspace.load(self.ws)
        self.assertEqual(ws.source_ids(), [])
        self.assertEqual(ws.companion_names(), [])

    def test_onboard_is_idempotent_and_keeps_edited_conventions(self):
        self._onboard()
        ws = Workspace.load(self.ws)
        ws.paths.conventions_md("measure-theory").write_text("# edited by the owner\n")
        self._onboard()
        ws = Workspace.load(self.ws)
        self.assertEqual(ws.paths.conventions_md("measure-theory").read_text(),
                         "# edited by the owner\n")
        self.assertEqual(len(ws.companions), 1)
        self.assertEqual(ws.source_ids(), [PUBLISHED_ID])

    def test_a_second_companion_on_the_same_source_makes_a_differential(self):
        self._onboard()
        other = self.root / "second"
        other.mkdir()
        (other / "pdf-overlay" / "data").mkdir(parents=True)
        (other / "pdf-overlay" / "data" / "x.json").write_text("{}")
        (other / "pdf-overlay" / "official-source.json").write_text(
            json.dumps({"sha256": PUBLISHED_SHA}))
        rc = self.cf.main(["onboard", str(other), "--workspace", str(self.ws),
                           "--name", "adam", "--no-errata", "-q"])
        self.assertEqual(rc, 0)
        ws = Workspace.load(self.ws)
        self.assertEqual(ws.shared_sources(), {PUBLISHED_ID: ["measure-theory", "adam"]})

    def test_an_unimplemented_adapter_kind_is_refused_before_anything_is_written(self):
        """onboarding-contract.md: `--adapter ledger_nodes` refuses. It used to warn."""
        with self.assertRaises(CfError) as ctx:
            self.cf.main(["onboard", str(self.repo), "--workspace", str(self.ws),
                          "--adapter", "ledger_nodes", "--name", "node", "-q"])
        self.assertIn("not implemented", str(ctx.exception))
        self.assertIn("onboarding-contract.md", getattr(ctx.exception, "hint", ""))
        ws = Workspace.load(self.ws)
        self.assertFalse(ws.paths.adapter_yaml("node").exists())
        self.assertFalse(ws.paths.state_json("node").exists())
        self.assertEqual(ws.companion_names(), [])
        self.assertEqual(ws.source_ids(), [])

    def test_the_offered_adapter_kinds_are_the_implemented_ones(self):
        implemented = set(vocab.IMPLEMENTED_ADAPTER_KINDS)
        for kind in sorted(vocab.ADAPTER_KINDS):
            if kind in implemented:
                continue
            with self.subTest(kind=kind):
                with self.assertRaises(CfError):
                    self.cf.main(["onboard", str(self.repo), "--workspace", str(self.ws),
                                  "--adapter", kind, "--name", "x", "-q"])

    def test_zero_tagged_declarations_fails_onboarding(self):
        saved = _FakeAdapter.counts
        _FakeAdapter.counts = {"declarations": 10, "tagged": 0, "items": 0}
        try:
            with self.assertRaises(CfError):
                self._onboard()
        finally:
            _FakeAdapter.counts = saved

    def test_a_repo_naming_no_source_is_refused(self):
        bare = self.root / "bare"
        (bare / "pdf-overlay" / "data").mkdir(parents=True)
        (bare / "pdf-overlay" / "data" / "x.json").write_text("{}")
        (bare / "pdf-overlay" / "official-source.json").write_text("{}")
        with self.assertRaises(CfError) as ctx:
            self.cf.main(["onboard", str(bare), "--workspace", str(self.ws),
                          "--name", "bare", "-q"])
        self.assertIn("no source could be discovered", str(ctx.exception))

    def test_an_unknown_source_lands_in_the_overlay_as_unconfirmed(self):
        repo = self.root / "unknown-book"
        repo.mkdir()
        (repo / "pdf-overlay" / "data").mkdir(parents=True)
        (repo / "pdf-overlay" / "data" / "x.json").write_text("{}")
        (repo / "pdf-overlay" / "official-source.json").write_text(json.dumps({
            "sha256": "b" * 64,
            "canonical_pdf_url": "https://example.org/books/analysis-2e.pdf",
            "legacy_page_url": "https://example.org/books/"}))
        rc = self.cf.main(["onboard", str(repo), "--workspace", str(self.ws),
                           "--name", "smith", "--no-errata", "-q"])
        self.assertEqual(rc, 0)
        ws = Workspace.load(self.ws)
        unconfirmed = ws.unconfirmed_sources()
        self.assertEqual(len(unconfirmed), 1)
        self.assertEqual(unconfirmed[0].registry, "overlay")
        overlay = yamlio.read_yaml(ws.paths.registry_yaml)
        self.assertIn(unconfirmed[0].id, overlay["sources"])
        self.assertFalse(overlay["sources"][unconfirmed[0].id]["confirmed"])


class TestSourcesCommand(unittest.TestCase):
    def setUp(self):
        import cf
        self.cf = cf
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.ws_dir = self.root / "ws"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.cf.main(["init", str(self.ws_dir), "--no-git", "-q"])
        ws = Workspace.load(self.ws_dir)
        ws.upsert_source(PUBLISHED_ID, **Registry.load().get(PUBLISHED_ID).to_source_fields())
        ws.upsert_companion("ours", repo=str(self.repo), adapter="docstring_tags",
                            sources=[PUBLISHED_ID], anchor_source=PUBLISHED_ID)
        ws.save()

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_and_show(self):
        self.assertEqual(self.cf.main(["sources", "list", "--workspace", str(self.ws_dir)]), 0)
        self.assertEqual(self.cf.main(["sources", "show", PUBLISHED_ID,
                                       "--workspace", str(self.ws_dir)]), 0)

    def test_set_marks_an_override_as_manual(self):
        rc = self.cf.main(["sources", "set", PUBLISHED_ID, "offset=17", "chapters=1,2",
                           "--workspace", str(self.ws_dir), "-q"])
        self.assertEqual(rc, 0)
        source = Workspace.load(self.ws_dir).source(PUBLISHED_ID)
        self.assertEqual(source.offset, 17)
        self.assertEqual(source.offset_method, "manual")
        self.assertEqual(source.chapters, (1, 2))
        self.assertEqual(source.scope_method, "manual")

    def test_set_refuses_an_unknown_key_and_an_unknown_source(self):
        with self.assertRaises(CfError):
            self.cf.main(["sources", "set", PUBLISHED_ID, "nonsense=1",
                          "--workspace", str(self.ws_dir), "-q"])
        with self.assertRaises(CfError):
            self.cf.main(["sources", "set", "ghost", "offset=1",
                          "--workspace", str(self.ws_dir), "-q"])

    def test_confirming_a_source_is_one_command(self):
        ws = Workspace.load(self.ws_dir)
        ws.upsert_source("smith-analysis-2e", title="Analysis", edition="2e", offset=0,
                         role="reference", confirmed=False)
        ws.save()
        self.assertEqual([s.id for s in Workspace.load(self.ws_dir).unconfirmed_sources()],
                         ["smith-analysis-2e"])
        self.cf.main(["sources", "set", "smith-analysis-2e", "confirmed=true",
                      "--workspace", str(self.ws_dir), "-q"])
        self.assertEqual(Workspace.load(self.ws_dir).unconfirmed_sources(), [])


class TestOnboardDefaults(unittest.TestCase):
    def test_every_adapter_kind_has_defaults(self):
        from cfcmd.onboard import DEFAULTS
        self.assertEqual(set(DEFAULTS), set(vocab.ADAPTER_KINDS))
        for kind, defaults in DEFAULTS.items():
            self.assertIn(defaults["anchor"], vocab.ANCHOR_CONVENTIONS, kind)
            self.assertIn(defaults["granularity"], vocab.GRANULARITIES, kind)
        self.assertEqual(DEFAULTS["ledger_nodes"]["granularity"], "node")


# ---------------------------------------------------------------------------
# baseline regressions (skipped unless the environment names the baseline)
# ---------------------------------------------------------------------------

def _env_dir(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    p = Path(value).expanduser()
    return p if p.exists() else None


class TestBaselineItemGrammar(unittest.TestCase):
    """The grammar must swallow the real statement tables, not 99% of them."""

    def setUp(self):
        self.base = _env_dir("CF_BASELINE_DIFF")
        if self.base is None:
            self.skipTest("CF_BASELINE_DIFF not set")

    def test_every_baseline_item_parses_except_the_known_junk(self):
        rejects, total = [], 0
        for name in ("ours", "teorth", "adam"):
            path = self.base / f"{name}.jsonl"
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                total += 1
                raw = json.loads(line)["item"]
                item, reject = items.try_parse(raw)
                if item is None:
                    rejects.append((name, raw, reject.reason))
        if total == 0:
            self.skipTest("no baseline statement tables found")
        self.assertGreater(total, 1000)
        self.assertEqual([r[1] for r in rejects], ["Corollary (Lemma 1.2.5 + Exercise 1.2.4)"],
                         f"unexpected rejects: {rejects}")

    def test_exercise_1_3_19_survives(self):
        """The row the single-space regex dropped."""
        found = False
        for name in ("ours", "teorth", "adam"):
            path = self.base / f"{name}.jsonl"
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                raw = json.loads(line)["item"]
                item, _ = items.try_parse(raw)
                if item is not None and item.id == "Exercise 1.3.19":
                    found = True
        if not found:
            self.skipTest("no Exercise 1.3.19 row in this baseline")
        self.assertTrue(found)


class TestBaselineCorpus(unittest.TestCase):
    def setUp(self):
        self.corpus = _env_dir("CF_BASELINE_CORPUS")
        if self.corpus is None:
            self.skipTest("CF_BASELINE_CORPUS not set")

    def test_finding_filenames_are_reachable_from_the_item_grammar(self):
        """Every finding file must be addressable by one of our slug spellings."""
        checked = unreachable = 0
        for sub in ("findings", "findings-vac", "findings-hunt"):
            d = self.corpus / sub
            if not d.is_dir():
                continue
            for path in sorted(d.glob("*.json"))[:200]:
                payload = json.loads(path.read_text())
                raw = payload.get("item") or payload.get("book_item")
                if not raw:
                    continue
                item, _ = items.try_parse(raw)
                if item is None:
                    continue
                checked += 1
                if path.stem not in items.slug_variants(item, raw=raw):
                    unreachable += 1
        if checked == 0:
            self.skipTest("no findings with an item field")
        self.assertLessEqual(unreachable / checked, 0.05,
                             f"{unreachable}/{checked} baseline filenames unreachable")


class TestRealCompanionDiscovery(unittest.TestCase):
    """Discovery against the two REAL companions (skipped without CF_TML/CF_MT)."""

    def test_tao_measure_lean_yields_both_editions_and_the_anchor_sentence(self):
        repo = _env_dir("CF_TML")
        if repo is None:
            self.skipTest("CF_TML not set")
        registry = Registry.load()
        merged = discovery.merge_claims(discovery.discover_sources(repo), registry)
        prefixes = {c.sha256_prefix for c in merged if c.sha256_prefix}
        self.assertEqual(prefixes, {"62d95658", "64498329"})
        self.assertTrue(any("PUBLISHED" in (c.anchor_hint or "") for c in merged))
        self.assertTrue(any(c.offset_hint == 16 for c in merged))
        kind, score, _ = discovery.detect_adapter(repo)
        self.assertEqual(kind, "docstring_tags")
        self.assertGreater(score, 0.5)

    def test_measure_theory_yields_one_sha_confirmed_source(self):
        repo = _env_dir("CF_MT")
        if repo is None:
            self.skipTest("CF_MT not set")
        registry = Registry.load()
        merged = discovery.merge_claims(discovery.discover_sources(repo), registry)
        self.assertEqual([c.sha256 for c in merged if c.sha256], [PUBLISHED_SHA])
        self.assertEqual(registry.by_sha(PUBLISHED_SHA).id, PUBLISHED_ID)
        kind, score, _ = discovery.detect_adapter(repo)
        self.assertEqual(kind, "attribution_table")
        self.assertGreaterEqual(score, 0.9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
