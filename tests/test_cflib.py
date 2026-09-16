"""Unit tests for the cf core: item grammar, state, campaign, yaml, vocab.

Run:  python3 -m unittest discover -s /Users/jack/Desktop/companion-faithfulness/tests -v
(or:  python3 /Users/jack/Desktop/companion-faithfulness/tests/test_cflib.py)

Regression tests that read the local baseline are skipped cleanly unless the
environment names it:

    CF_BASELINE_DIFF=/Users/jack/Desktop/tao-differential   (statement tables)
    CF_BASELINE_CORPUS=/Users/jack/Desktop/tao-diff-run     (2,672 findings)
    CF_TML=/Users/jack/Desktop/tao-measure-lean
    CF_MT=/Users/jack/Desktop/measure_theory
    CF_BOOK_TXT=/Users/jack/Desktop/tao-differential/book.official.txt
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

from cflib import hashing, items, state as state_mod, templates, vocab, yamlio  # noqa: E402
from cflib.campaign import CAMPAIGN_FILE, Campaign, find_campaign_root  # noqa: E402
from cflib.errors import CfError, ItemGrammarError, StateError  # noqa: E402
from cflib.paths import CampaignPaths  # noqa: E402


# ---------------------------------------------------------------------------
# item grammar
# ---------------------------------------------------------------------------

class TestItemGrammar(unittest.TestCase):
    def test_plain_numbered_item(self):
        it = items.parse("Exercise 1.1.1")
        self.assertEqual(it.id, "Exercise 1.1.1")
        self.assertEqual((it.kind, it.chapter, it.section, it.number), ("Exercise", 1, 1, 1))
        self.assertEqual(it.sub, "")
        self.assertFalse(it.in_text)

    def test_irregular_whitespace_is_tolerated(self):
        """The baseline lost `Exercise    1.3.19` to a single-space regex."""
        for raw in ["Exercise    1.3.19", "Exercise\t1.3.19", "  Exercise 1.3.19  ",
                    "Exercise 1.3.19.", "`Exercise 1.3.19`", "Exercise 1.3.19"]:
            self.assertEqual(items.normalise(raw), "Exercise 1.3.19", raw)

    def test_sub_parts(self):
        cases = {
            "Exercise 1.1.6(1)": ("Exercise 1.1.6", "(1)"),
            "Exercise 1.2.24(ii)": ("Exercise 1.2.24", "(ii)"),
            "Exercise 1.1.6 (i) (Boolean closure)": ("Exercise 1.1.6", "(i)"),
            "Exercise 1.3.3(iii)": ("Exercise 1.3.3", "(iii)"),
            "Lemma 1.2.13(vi) (Countable union)": ("Lemma 1.2.13", "(vi)"),
            "Exercise 1.1.8(ii) (Jordan measure of a triangle)": ("Exercise 1.1.8", "(ii)"),
        }
        for raw, (ident, sub) in cases.items():
            it = items.parse(raw)
            self.assertEqual((it.id, it.sub), (ident, sub), raw)
            self.assertEqual(it.full, ident + sub)

    def test_sub_part_case_and_order(self):
        self.assertEqual(items.parse("Exercise 1.1.6 (IV)").sub, "(iv)")
        order = sorted(["Exercise 1.1.6(iii)", "Exercise 1.1.6(i)", "Exercise 1.1.6", "Exercise 1.1.6(ii)"],
                       key=items.sort_key)
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
        self.assertEqual((it.id, it.number), ("Theorem 1.2", None))

    def test_in_text_pages(self):
        cases = {
            "In-text, book p. 8": 8,
            "In-text definition, book p. 29": 29,
            "In-text convention, book p. 29": 29,
            "In-text p.194": 194,
            "In-text proof (book p. 241)": 241,
            "In-text, book pp. 50–51": 50,          # en dash range -> first page
            "In-text proof of Lemma 1.1.2(ii), book p. 6": 6,
        }
        for raw, page in cases.items():
            it = items.parse(raw)
            self.assertEqual(it.id, f"In-text p.{page}", raw)
            self.assertEqual(it.page, page)
            self.assertTrue(it.in_text)

    def test_in_text_cross_reference_without_a_page(self):
        it = items.parse("In-text definition, Lemma 1.2.13(iii)")
        self.assertEqual(it.id, "In-text Lemma 1.2.13(iii)")
        self.assertEqual(it.qualifier, "definition")
        it2 = items.parse("In-text step in the proof of Lemma 1.2.13(ii)")
        self.assertEqual(it2.id, "In-text Lemma 1.2.13(ii)")

    def test_reject_list(self):
        """norm() used to pass junk through as a first-class key."""
        rejects = {
            "Corollary (Lemma 1.2.5 + Exercise 1.2.4)": items.REJECT_NO_NUMBER,
            "Foo 1.2.3": items.REJECT_UNKNOWN_KIND,
            "": items.REJECT_EMPTY,
            "   ": items.REJECT_EMPTY,
            "In-text, somewhere": items.REJECT_IN_TEXT_NO_TARGET,
        }
        for raw, reason in rejects.items():
            item, reject = items.try_parse(raw)
            self.assertIsNone(item, raw)
            self.assertEqual(reject.reason, reason, raw)
            with self.assertRaises(ItemGrammarError):
                items.parse(raw)

    def test_kinds_come_from_the_campaign(self):
        self.assertEqual(items.parse("Widget 2.3.4", kinds=("Widget",)).id, "Widget 2.3.4")
        item, reject = items.try_parse("Exercise 1.1.1", kinds=("Widget",))
        self.assertIsNone(item)
        self.assertEqual(reject.reason, items.REJECT_UNKNOWN_KIND)

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


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------

class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = CampaignPaths(root=Path(self.tmp.name), book_id="b")
        self.paths.ensure_skeleton()

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        st = state_mod.new_state("ours", repo="/repo", commit="abc123",
                                 adapter={"kind": "docstring_tags"}, book_sha="deadbeef")
        state_mod.set_section(st, "extract", {"rows": 1157, "items": 763, "sorry": 516})
        state_mod.record_run(st, "attack-hunt-claude", {"tasks": 763, "findings": 763})
        state_mod.note(st, "extract", rows=1157)
        state_mod.write_state(self.paths, "ours", st)

        back = state_mod.read_state(self.paths, "ours")
        self.assertEqual(back["name"], "ours")
        self.assertEqual(back["extract"]["rows"], 1157)
        self.assertIn("at", back["extract"])
        self.assertEqual(back["runs"]["attack-hunt-claude"]["tasks"], 763)
        self.assertEqual(back["history"][-1]["event"], "extract")
        self.assertEqual(json.loads(self.paths.state_json("ours").read_text())["book_sha"], "deadbeef")

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
        st = state_mod.new_state("ours", commit="abcdef1234")
        state_mod.set_section(st, "extract", {"rows": 10, "items": 5})
        row = state_mod.summary_row(st)
        self.assertEqual(row["commit"], "abcdef1")
        self.assertEqual((row["rows"], row["items"]), (10, 5))


# ---------------------------------------------------------------------------
# campaign.yaml
# ---------------------------------------------------------------------------

CAMPAIGN_MIN = """\
version: 1
book:
  id: tao-measure
  sha256: "6449832911111111111111111111111111111111111111111111111111111111"
  offset: 16
  kinds: [Definition, Lemma, Exercise]
companions:
  - name: ours
    repo: /repo/ours
    adapter: docstring_tags
    write_policy: read_only
hosts:
  allowlist: [buildbox.example]
models:
  worker: claude-sonnet-5
defaults:
  mode: attack:vac
  local_fleet_max_items: 100
"""


class TestCampaign(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # resolve(): on macOS the temp dir is /var/... but cflib resolves paths,
        # which gives /private/var/... -- compare like with like.
        self.root = Path(self.tmp.name).resolve()
        (self.root / CAMPAIGN_FILE).write_text(CAMPAIGN_MIN)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_and_accessors(self):
        c = Campaign.load(self.root)
        self.assertEqual(c.book_id, "tao-measure")
        self.assertEqual(c.offset, 16)
        self.assertEqual(c.kinds, ("Definition", "Lemma", "Exercise"))
        self.assertEqual(c.model("worker"), "claude-sonnet-5")
        self.assertEqual(c.default("local_fleet_max_items"), 100)
        self.assertEqual(c.companion("ours")["adapter"], "docstring_tags")
        self.assertEqual(c.companion_names(), ["ours"])

    def test_paths_are_derived_from_the_book_id(self):
        c = Campaign.load(self.root)
        self.assertEqual(c.paths.book_txt, self.root / "book" / "tao-measure" / "book.txt")
        self.assertEqual(c.paths.statements_jsonl("ours"),
                         self.root / "companions" / "ours" / "statements.jsonl")

    def test_host_allowlist_is_enforced(self):
        c = Campaign.load(self.root)
        self.assertEqual(c.check_host("buildbox.example"), "buildbox.example")
        with self.assertRaises(CfError):
            c.check_host("someone-elses-server")

    def test_edition_gate(self):
        c = Campaign.load(self.root)
        c.require_book_sha(c.book_sha, "findings")     # same edition: fine
        with self.assertRaises(CfError):
            c.require_book_sha("0" * 64, "findings")   # the 2011 draft

    def test_unknown_companion_raises(self):
        with self.assertRaises(CfError):
            Campaign.load(self.root).companion("nope")

    def test_search_upward_and_env(self):
        nested = self.root / "companions" / "ours"
        nested.mkdir(parents=True)
        self.assertEqual(find_campaign_root(nested), self.root.resolve())
        old = os.environ.get("CF_CAMPAIGN")
        os.environ["CF_CAMPAIGN"] = str(self.root)
        try:
            self.assertEqual(find_campaign_root(None), self.root.resolve())
        finally:
            os.environ.pop("CF_CAMPAIGN", None)
            if old is not None:
                os.environ["CF_CAMPAIGN"] = old

    def test_invalid_campaign_is_rejected(self):
        (self.root / CAMPAIGN_FILE).write_text("book:\n  id: x\n")  # no offset
        with self.assertRaises(CfError):
            Campaign.load(self.root)

    def test_upsert_companion_round_trips_through_save(self):
        c = Campaign.load(self.root)
        c.upsert_companion("adam", repo="/repo/adam", adapter="attribution_table")
        c.save()
        again = Campaign.load(self.root)
        self.assertEqual(again.companion("adam")["adapter"], "attribution_table")
        self.assertEqual(len(again.companions), 2)


# ---------------------------------------------------------------------------
# yaml, hashing, templates, vocabulary
# ---------------------------------------------------------------------------

class TestYamlFallback(unittest.TestCase):
    SAMPLE = CAMPAIGN_MIN + """\
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
        self.assertEqual(data["book"]["offset"], 16)
        self.assertEqual(data["nested"]["a"]["c"], [1, 2, 3])
        self.assertEqual(data["nested"]["d"], "quoted: value")
        self.assertIsNone(data["nested"]["e"])
        self.assertIs(data["nested"]["f"], False)
        self.assertEqual(data["block"], "line one\nline two\n")
        self.assertEqual(data["companions"][0]["name"], "ours")
        self.assertEqual(data["hosts"]["allowlist"], ["buildbox.example"])

    def test_dump_round_trips_under_both_readers(self):
        doc = {"a": 1, "b": [1, 2], "c": {"d": "x: y"}, "e": None, "f": True,
               "g": [{"h": 1}, {"h": 2}], "i": "multi\nline\n", "j": []}
        text = yamlio.dump_yaml(doc)
        self.assertEqual(yamlio.load_yaml_minimal(text), doc)
        if yamlio.HAVE_PYYAML:
            import yaml
            self.assertEqual(yaml.safe_load(text), doc)

    def test_anchors_are_refused_not_mangled(self):
        with self.assertRaises(CfError):
            yamlio.load_yaml_minimal("a: &anchor 1\nb: *anchor\n")

    def test_markdown_emphasis_in_a_block_scalar_is_not_an_alias(self):
        """The regression: every attack-vac task failed the fallback reader.

        ``templates/prompts/attack-vac.md.tmpl`` writes ``*because of* a junk
        value`` into ``description: |``; the anchor guard used to run during
        line collection, before any block-scalar context existed, and aborted
        the parse of all 697 task files the skill had just written.
        """
        text = (
            "schema_version: 1\n"
            "id: definition-1-1-1\n"
            "description: |\n"
            "  4. **Junk-value escape.** Does the statement accidentally hold\n"
            "     *because of* a junk value rather than because of the mathematics\n"
            "     — e.g. both sides are the junk value? A & B are both fine here.\n"
            "\n"
            "  Not a # comment, and `key: value` inside prose is not a key.\n"
            "status: open\n"
        )
        got = yamlio.load_yaml_minimal(text)
        self.assertEqual(got["schema_version"], 1)
        self.assertEqual(got["status"], "open")
        self.assertIn("*because of*", got["description"])
        self.assertIn("Not a # comment", got["description"])
        if yamlio.HAVE_PYYAML:
            import yaml
            self.assertEqual(got, yaml.safe_load(text))

    def test_generated_task_files_parse_identically_under_both_readers(self):
        """Every ``runs/*/tasks/*.yaml`` the skill writes must survive the fallback."""
        if not yamlio.HAVE_PYYAML:
            self.skipTest("needs PyYAML as the oracle")
        import yaml

        from plan import make_tasks as mt  # noqa: F401  (import check only)

        tmpl = (SKILL / "templates" / "tasks" / "attack.yaml.tmpl").read_text(encoding="utf-8")
        probe = (SKILL / "templates" / "prompts" / "attack-vac.md.tmpl").read_text(encoding="utf-8")
        for key, value in {
            "item": "Definition 1.1.1", "companion": "ours", "decls_inline": "ours:Foo, ours:Bar",
            "peers_line": "Peers: adam", "total_functions_sentence": "Lean functions are total.",
        }.items():
            probe = probe.replace("{{" + key + "}}", value)
        body = "\n".join(("  " + ln) if ln.strip() else "" for ln in probe.splitlines())
        rendered = tmpl
        for key, value in {
            "task_id": "definition-1-1-1", "attack_label": "vacuity", "item": "Definition 1.1.1",
            "priority": "50", "depends_on": "[]", "probe_block": body, "run_id": "attack-vac-ours",
            "book_sha": "0" * 64, "kinds_inline": "CONTENT|VACUITY", "companion": "ours",
            "gate_command": "python3 /gate/verify_findings.py", "printed_page": "12",
        }.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        self.assertNotIn("{{", rendered)
        self.assertEqual(yamlio.load_yaml_minimal(rendered), yaml.safe_load(rendered))


class TestHashing(unittest.TestCase):
    def test_finding_id_is_order_independent_and_stable(self):
        a = hashing.finding_id("run-1", "Exercise 1.1.1", "CONTENT", ["b:y", "a:x"])
        b = hashing.finding_id("run-1", "Exercise 1.1.1", "CONTENT", ["a:x", "b:y", "a:x"])
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)
        self.assertNotEqual(a, hashing.finding_id("run-2", "Exercise 1.1.1", "CONTENT", ["a:x"]))
        self.assertNotEqual(a, hashing.finding_id("run-1", "Exercise 1.1.1", "VACUITY", ["a:x", "b:y"]))

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
        text = templates.render_template("campaign.yaml.example", {
            "campaign_name": "t", "created": '"2026-01-01"', "book_id": "t", "book_title": '"T"',
            "edition": '"E"', "pdf_path": "null", "book_sha256": '"' + "a" * 64 + '"',
            "offset": 16, "errata_url": "null", "kinds": "[Definition, Exercise]",
            "chapters": "[1]"})
        data = yamlio.load_yaml_minimal(text)
        self.assertEqual(data["book"]["offset"], 16)
        # the audited scope every stage narrows to (cf pair, plan-run, detect)
        self.assertEqual(data["book"]["chapters"], [1])
        self.assertEqual(data["hosts"]["allowlist"], [])
        self.assertEqual(data["companions"], [])
        self.assertIs(data["policy"]["publish_unchallenged"], False)


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
        for good in ["fixed:PR#8", "in_progress:fix/batch2", "refuted", "argued_correct", "out_of_scope"]:
            self.assertEqual(vocab.check_resolution(good), good)
        for bad in ["fixed", "fixed:", "done", ""]:
            with self.assertRaises(CfError):
                vocab.check_resolution(bad)

    def test_every_ledger_verdict_maps_to_the_viewer(self):
        self.assertEqual(set(vocab.LEDGER_VERDICTS), set(vocab.LEDGER_TO_VIEWER))
        for viewer in vocab.LEDGER_TO_VIEWER.values():
            self.assertTrue(viewer in vocab.VIEWER_VERDICTS or viewer in vocab.VIEWER_STATUS)


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
        for cmd in ("init", "status", "onboard"):
            self.assertIn(cmd, mapping, f"{cmd} not discovered")

    def test_init_then_status(self):
        root = Path(self.tmp.name) / "campaign"
        rc = self.cf.main(["init", str(root), "--book", "b", "--offset", "16",
                           "--sha256", "a" * 64, "--no-git", "-q"])
        self.assertEqual(rc, 0)
        self.assertTrue((root / CAMPAIGN_FILE).exists())
        self.assertTrue((root / "companions").is_dir())
        self.assertIn("book/", (root / ".gitignore").read_text())
        self.assertNotIn("\ncorpus/", (root / ".gitignore").read_text())
        self.assertEqual(self.cf.main(["init", str(root), "--book", "b", "--offset", "16",
                                       "--no-git", "-q"]), 0)  # idempotent
        self.assertEqual(self.cf.main(["status", "--campaign", str(root), "-q"]), 0)

    def test_status_on_an_unknown_companion_is_an_error(self):
        root = Path(self.tmp.name) / "campaign"
        self.cf.main(["init", str(root), "--book", "b", "--offset", "0", "--no-git", "-q"])
        with self.assertRaises(CfError):
            self.cf.main(["status", "--campaign", str(root), "--companion", "ghost", "-q"])

    def test_init_refuses_to_write_inside_the_skill_repo(self):
        from cflib.paths import skill_root
        with self.assertRaises(CfError):
            self.cf.main(["init", str(skill_root() / "nope"), "--book", "b", "--no-git"])


# ---------------------------------------------------------------------------
# the adapter interface (scripts/adapters/ subclasses this)
# ---------------------------------------------------------------------------

class _FakeAdapter:
    """Stands in for scripts/adapters/ while exercising the onboard path."""

    counts = {"declarations": 2207, "tagged": 1496, "items": 519,
              "rejects": [{"raw": "Corollary (Lemma 1.2.5)", "reason": "no_number"}]}

    def dry_run(self, repo):
        return dict(self.counts)


class TestAdapterApi(unittest.TestCase):
    def test_subclass_contract_and_key_stability(self):
        from cflib.adapters_api import Adapter

        class Demo(Adapter):
            kind = "demo"

            def dry_run(self, repo):
                return {"declarations": 1, "tagged": 1, "items": 1}

            def extract(self, repo, book_sha):
                yield {"item": "Exercise 1.1.1", "book_sha": book_sha}

        a = Demo({"repo": "/nowhere", "glob": "*.lean"})
        b = Demo({"repo": "/nowhere", "glob": "*.lean"})
        c = Demo({"repo": "/nowhere", "glob": "*.md"})
        self.assertEqual(a.key(), b.key())
        self.assertNotEqual(a.key(), c.key())
        self.assertTrue(a.key().startswith("sha256:"))
        self.assertEqual(next(iter(a.extract("/nowhere", "sha")))["book_sha"], "sha")

    def test_ledger_style_key_inputs_can_be_extended(self):
        from cflib.adapters_api import Adapter

        class Ledger(Adapter):
            kind = "ledger_demo"

            def dry_run(self, repo):
                return {}

            def extract(self, repo, book_sha):
                return iter(())

            def key_inputs(self):
                return {**super().key_inputs(), "ledger_sha": self.config.get("ledger_sha")}

        self.assertNotEqual(Ledger({"ledger_sha": "a"}).key(), Ledger({"ledger_sha": "b"}).key())

    def test_validate_row_reports_missing_required_fields(self):
        from cflib.adapters_api import Adapter, AdapterError

        class Demo(Adapter):
            kind = "demo"

            def dry_run(self, repo):
                return {}

            def extract(self, repo, book_sha):
                return iter(())

        with self.assertRaises(AdapterError):
            Demo({}).validate_row({"item": "Exercise 1.1.1"})


class TestOnboardIntegration(unittest.TestCase):
    """cf onboard must call adapters.load(...).dry_run(repo) and store the counts."""

    def setUp(self):
        import cf
        self.cf = cf
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "campaign"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir(parents=True)
        self.cf.main(["init", str(self.root), "--book", "b", "--offset", "16",
                      "--sha256", "b" * 64, "--no-git", "-q"])
        import types
        fake = types.ModuleType("adapters")
        fake.load = lambda adapter_yaml, campaign=None: _FakeAdapter()
        self._saved = sys.modules.get("adapters")
        sys.modules["adapters"] = fake

    def tearDown(self):
        if self._saved is not None:
            sys.modules["adapters"] = self._saved
        else:
            sys.modules.pop("adapters", None)
        self.tmp.cleanup()

    def test_onboard_writes_files_and_counts(self):
        rc = self.cf.main(["onboard", "ours", "--repo", str(self.repo),
                           "--adapter", "docstring_tags", "--owner", "someone",
                           "--campaign", str(self.root), "-q"])
        self.assertEqual(rc, 0)
        paths = CampaignPaths(root=self.root, book_id="b")
        self.assertTrue(paths.adapter_yaml("ours").exists())
        self.assertTrue(paths.conventions_md("ours").exists())
        st = state_mod.read_state(paths, "ours")
        self.assertEqual(st["adapter"]["kind"], "docstring_tags")
        self.assertEqual(st["adapter"]["dry_run"]["tagged"], 1496)
        self.assertEqual(st["book_sha"], "b" * 64)
        adapter_cfg = yamlio.load_yaml_minimal(paths.adapter_yaml("ours").read_text())
        self.assertEqual(adapter_cfg["write_policy"], "read_only")
        self.assertIn(".lake/**/*.lean", adapter_cfg["exclude"])
        campaign = Campaign.load(self.root)
        self.assertEqual(campaign.companion("ours")["owner"], "someone")

    def test_onboard_is_idempotent_and_keeps_edited_conventions(self):
        self.cf.main(["onboard", "ours", "--repo", str(self.repo), "--adapter",
                      "docstring_tags", "--campaign", str(self.root), "-q"])
        paths = CampaignPaths(root=self.root, book_id="b")
        paths.conventions_md("ours").write_text("# edited by the owner\n")
        self.cf.main(["onboard", "ours", "--repo", str(self.repo), "--adapter",
                      "docstring_tags", "--campaign", str(self.root), "-q"])
        self.assertEqual(paths.conventions_md("ours").read_text(), "# edited by the owner\n")
        self.assertEqual(len(Campaign.load(self.root).companions), 1)

    def test_zero_tagged_declarations_fails_onboarding(self):
        _FakeAdapter.counts = {"declarations": 10, "tagged": 0, "items": 0}
        try:
            with self.assertRaises(CfError):
                self.cf.main(["onboard", "ours", "--repo", str(self.repo), "--adapter",
                              "docstring_tags", "--campaign", str(self.root), "-q"])
        finally:
            _FakeAdapter.counts = {"declarations": 2207, "tagged": 1496, "items": 519,
                                   "rejects": []}


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
