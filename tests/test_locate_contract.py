"""The locate wave-2 contract: the values, paths and helpers every locate package shares.

New code, for references/locate-documents.md sections 7-11 and the values
references/vocabulary.md section 1.11 adds for them.  The sources, corpus,
planner, gate and bench packages are built against these definitions on
separate branches, so the tests pin the definitions themselves: every new
value documented, ``location_key``'s parts and refusals, the workspace paths,
and the stdlib head-text helper the gate carries -- in lockstep with
``cflib.passages``, as ``verify_findings.finding_id`` is with
``cflib.hashing.finding_id``.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import hashing, items, passages, vocab  # noqa: E402
from cflib.errors import CfError, PassageIdError  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from gate import locate_text as lt  # noqa: E402

VOCABULARY_MD = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")
DOCUMENTS_MD = (SKILL / "references" / "locate-documents.md").read_text(encoding="utf-8")


def _scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


class TestTheDocumentValues(unittest.TestCase):

    def test_the_sets(self):
        self.assertEqual(vocab.LOCATE_OUTCOMES, {"located", "absent", "unresolved"})
        self.assertEqual(vocab.OUTCOME_KIND, {"located": "LOCATION", "absent": "ABSENCE",
                                              "unresolved": "UNRESOLVED"})
        self.assertEqual(set(vocab.OUTCOME_KIND.values()), vocab.LOCATE_OUTCOME_KINDS)
        self.assertEqual(vocab.FINDERS, {"theorem_box", "bold_definition", "lead", "model"})
        self.assertNotIn("model", vocab.DETERMINISTIC_FINDERS)
        self.assertEqual(vocab.CHALLENGE_ACTIONS, vocab.CHALLENGE_KEEP | vocab.CHALLENGE_CHANGE)
        self.assertFalse(vocab.CHALLENGE_KEEP & vocab.CHALLENGE_CHANGE)
        self.assertEqual(vocab.CANDIDATE_VIA, {"wikidata_tag", "mathlib_1000", "mathlib_100",
                                               "docstring_url", "library_metadata"})
        self.assertEqual(vocab.MIN_ABSENCE_QUERIES, 3)
        self.assertEqual(vocab.MAX_PASSAGES_PER_LOCATE_TASK, 8)
        self.assertEqual((vocab.EXPORT_QUOTE_WORDS, vocab.EXPORT_CONTEXT_CHARS), (12, 32))
        self.assertEqual(vocab.BENCH_ARMS, {"assisted", "blind"})
        self.assertIn("E-MEMORY", vocab.ERROR_CODES)

    def test_every_value_is_documented_in_the_vocabulary(self):
        """CLAUDE.md S4: a new value is a change to vocab.py AND references/vocabulary.md."""
        values = (vocab.LOCATE_OUTCOMES | vocab.FINDERS | vocab.CONFIDENCES
                  | vocab.CANDIDATE_VERDICTS | vocab.CANDIDATE_VIA | vocab.TAG_KINDS
                  | vocab.CHALLENGE_TARGETS | vocab.CHALLENGE_ACTIONS | vocab.BENCH_TIERS
                  | vocab.BENCH_ARMS | vocab.BENCH_SPLITS | vocab.FNF_CAUSES | vocab.ERROR_CODES
                  | vocab.BENCH_AA_VERDICTS
                  | vocab.SOURCE_ORIGINS | vocab.UNRESOLVED_BY | vocab.CHALLENGE_RULES
                  | vocab.CHALLENGE_PLANNED | vocab.INDEX_FLAGS | set(vocab.TOOLS_DB_KINDS)
                  | vocab.LOCATE_SKIP_REASONS
                  | {vocab.LOCATE_DOCUMENT_SCHEMA, vocab.RENDERER_WEB_HTML,
                     vocab.UNRESOLVED_ATTEMPT_CAP, vocab.AGENT_TIMEOUT})
        for value in sorted(values):
            with self.subTest(value=value):
                self.assertIn(f"`{value}`", VOCABULARY_MD)

    def test_the_skip_reasons_are_one_definition(self):
        """The planner's ``locate.skipped`` and the bench draw's excluded reasons came
        from a constant in locate.inputs and two bare literals (CLAUDE.md S4)."""
        from locate import inputs
        self.assertEqual(vocab.LOCATE_SKIP_REASONS,
                         {"not rendered", "parity refused", "no concept article"})
        self.assertFalse(hasattr(inputs, "PARITY_REFUSED"))
        self.assertIn("`LOCATE_SKIP_REASONS`", VOCABULARY_MD)
        for rel in ("cfcmd/plan_locate.py", "cfcmd/bench.py", "locate/plan.py"):
            text = (SKILL / "scripts" / rel).read_text(encoding="utf-8")
            with self.subTest(file=rel):
                for value in sorted(vocab.LOCATE_SKIP_REASONS):
                    self.assertNotIn(f'"{value}"', text)
                    self.assertNotIn(f'"{value} (', text)

    def test_the_run_bounds_and_the_shared_sets(self):
        """Wave 1 #85: the values packages had defined privately, now one definition."""
        self.assertEqual((vocab.MAX_TASKS_PER_ROOT, vocab.LOCATE_ATTEMPT_CAP, vocab.SESSION_SLOTS,
                          vocab.AGENT_TIMEOUT), (500, 3, 15, "45m"))
        self.assertEqual(vocab.UNRESOLVED_BY, {"worker", "attempt_cap"})
        self.assertEqual(vocab.CHALLENGE_RULES, {"refute_by_default", "hunt", "basis", "sample",
                                                 "none"})
        self.assertEqual(vocab.STRONG_RELATIONS, {"exactMatch", "closeMatch", "broadMatch"})
        self.assertEqual(vocab.STRONG_RELATIONS | vocab.WEAK_RELATIONS, vocab.LOCATE_RELATIONS)
        self.assertEqual((vocab.CHALLENGE_SAMPLE_RATE, vocab.CHALLENGE_STRONG_SAMPLE_RATE,
                          vocab.CHALLENGE_ABSENCE_SAMPLE_RATE), (0.2, 0.5, 0.5))
        self.assertEqual(vocab.CHALLENGE_PLANNED, {"required", "sampled_out", "not_required"})
        self.assertEqual(vocab.INDEX_FLAGS, {"is_prop_unknown", "published_status"})
        for name in ("MAX_TASKS_PER_ROOT", "LOCATE_ATTEMPT_CAP", "SESSION_SLOTS", "AGENT_TIMEOUT",
                     "UNRESOLVED_BY", "CHALLENGE_RULES", "CHALLENGE_PLANNED", "INDEX_FLAGS",
                     "STRONG_RELATIONS", "CHALLENGE_STRONG_SAMPLE_RATE",
                     "CHALLENGE_ABSENCE_SAMPLE_RATE", "CHALLENGE_RULE_BASIS"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", VOCABULARY_MD)
        from corpus import ingest
        self.assertEqual(set(ingest.FLAG_MEANINGS), vocab.INDEX_FLAGS)

    def test_the_tiers_and_the_tools_databases_are_one_definition(self):
        """The tools-database kinds and the tier tuples were defined in locate.inputs,
        bench.score and bench.compare beside vocab.BENCH_TIERS (CLAUDE.md S4)."""
        from bench import compare, layout, score
        from locate import inputs
        self.assertEqual(vocab.TOOLS_DB_KINDS, ("full", "blind", "planted"))
        self.assertEqual(set(vocab.BENCH_TIER_ORDER), vocab.BENCH_TIERS)
        self.assertEqual(len(vocab.BENCH_TIER_ORDER), len(vocab.BENCH_TIERS))
        self.assertLess(set(vocab.BENCH_LABELLED_TIERS), vocab.BENCH_TIERS)
        self.assertLess(set(vocab.BENCH_CLAIM_TIERS), vocab.BENCH_TIERS)
        self.assertEqual(set(layout.LABEL_FILES), set(vocab.BENCH_LABELLED_TIERS))
        self.assertIs(compare.TIERS, vocab.BENCH_TIER_ORDER)
        self.assertIs(compare.CLAIM_TIERS, vocab.BENCH_CLAIM_TIERS)
        self.assertIs(inputs.TOOLS_KINDS, vocab.TOOLS_DB_KINDS)
        self.assertEqual((score.TOOLS_FULL, score.TOOLS_BLIND, score.TOOLS_PLANTED),
                         vocab.TOOLS_DB_KINDS)
        for name in ("TOOLS_DB_KINDS", "BENCH_TIER_ORDER", "BENCH_LABELLED_TIERS",
                     "BENCH_CLAIM_TIERS"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", VOCABULARY_MD)
        for path in ("locate/inputs.py", "bench/score.py", "bench/compare.py",
                     "cfcmd/locate.py", "cfcmd/bench.py"):
            text = (SKILL / "scripts" / path).read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotRegex(text, r'== "(?:full|blind|planted)"|or "full"')
                self.assertNotIn('("G1", "G3"', text)

    def test_the_documents_name_the_values_they_carry(self):
        for value in (vocab.LOCATE_DOCUMENT_SCHEMA, vocab.RENDERER_WEB_HTML, "cf-sourceset/1",
                      "cf-corpus/1", "cf-gate-locate/1", "cf-layer-map/2",
                      "cf-locate-candidates/1", "cf-locate-recordset/1"):
            with self.subTest(value=value):
                self.assertIn(value, DOCUMENTS_MD)


class TestTheLocationKey(unittest.TestCase):

    def test_it_is_five_parts_joined_by_newlines_with_the_item_cleaned(self):
        key = hashing.location_key("enwiki-1001-2002", "In-text   #3fa91c07", "alpha",
                                   "a" * 40, "Widget.exists_spanning_gadget")
        payload = "\n".join(["enwiki-1001-2002", "In-text #3fa91c07", "alpha", "a" * 40,
                             "Widget.exists_spanning_gadget"])
        self.assertEqual(key, hashlib.sha256(payload.encode("utf-8")).hexdigest())

    def test_it_holds_no_run_and_differs_from_the_defect_key(self):
        args = ("enwiki-1001-2002", "In-text #3fa91c07", "alpha", "a" * 40, "W.x")
        spaced = (args[0], "In-text  #3fa91c07", *args[2:])
        self.assertEqual(hashing.location_key(*spaced), hashing.location_key(*args))
        self.assertNotEqual(hashing.location_key(*args),
                            hashing.defect_key(args[0], args[1], "LOCATION",
                                               ["alpha:W.x@" + "a" * 40]))
        self.assertNotEqual(hashing.location_key(*args),
                            hashing.location_key(args[0], args[1], "beta", args[3], args[4]))

    def test_a_missing_part_is_refused(self):
        for index in range(5):
            parts = ["enwiki-1001-2002", "In-text #3fa91c07", "alpha", "a" * 40, "W.x"]
            parts[index] = "  "
            with self.subTest(part=index), self.assertRaises(CfError):
                hashing.location_key(*parts)


class TestTheLocatePaths(unittest.TestCase):

    def setUp(self):
        self.root = _scratch(self)
        self.paths = WorkspacePaths(self.root)

    def test_the_layout(self):
        p = self.paths
        self.assertEqual(p.sourceset_manifest("wpm", 3), self.root / "sourcesets/wpm/manifest-3.jsonl")
        self.assertEqual(p.sourceset_source_dir("wpm", "enwiki-1001-2002"),
                         self.root / "sourcesets/wpm/sources/enwiki-1001-2002")
        self.assertEqual(p.candidates_json("wpm", "u1", "enwiki-1001-2002"),
                         self.root / "sourcesets/wpm/candidates/u1/enwiki-1001-2002.json")
        self.assertEqual(p.universe_yaml("u1"), self.root / "corpora/u1/universe.yaml")
        self.assertEqual(p.universe_index_dir("u1", "alpha"), self.root / "corpora/u1/index/alpha")
        self.assertEqual(p.corpus_db("u1"), self.root / "corpora/u1/corpus.sqlite")
        self.assertEqual(p.export_dir("set-1"), self.root / "exports/set-1")
        self.assertEqual(p.campaign_yaml("c1"), self.root / "campaigns/c1/campaign.yaml")
        self.assertEqual(p.locate_outcomes_jsonl("r1"),
                         self.root / "reports/locate-outcomes.r1.jsonl")
        self.assertNotEqual(p.corpora_dir, p.corpus_dir)

    def test_the_source_text_is_under_a_directory_the_workspace_ignores(self):
        from cfcmd.init import GITIGNORE

        self.assertIn("\nsources/\n", GITIGNORE)
        self.assertEqual(self.paths.sourceset_source_dir("wpm", "enwiki-1-2").parent.name, "sources")
        self.assertIn("corpora/*/corpus.sqlite", GITIGNORE)

    def test_the_derived_databases_and_the_labeler_excerpts_are_ignored(self):
        """Wave 1 #91: a universe database copy and source excerpts never enter git."""
        import fnmatch

        from cfcmd.init import GITIGNORE

        patterns = [line for line in GITIGNORE.splitlines() if line and not line.startswith("#")]
        for pattern in ("corpora/*/corpus.blind.sqlite", "benches/*/gold/planted/*.sqlite",
                        "benches/*/labeler/"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, patterns)
        for path in ("corpora/u1/corpus.sqlite", "corpora/u1/corpus.blind.sqlite",
                     "benches/v0/gold/planted/corpus.sqlite"):
            with self.subTest(path=path):
                self.assertTrue(any(fnmatch.fnmatch(path, p) for p in patterns), path)
        self.assertEqual(self.paths.corpus_db_blind("u1"),
                         self.root / "corpora/u1/corpus.blind.sqlite")
        # the gold labels themselves stay tracked, as corpus/ does
        self.assertFalse(any(fnmatch.fnmatch("benches/v0/gold/labels.jsonl", p)
                             for p in patterns))

    def test_the_manifests_are_listed_in_version_order(self):
        directory = self.paths.sourceset_dir("wpm")
        directory.mkdir(parents=True)
        for name in ("manifest-10.jsonl", "manifest-2.jsonl", "manifest-1.jsonl",
                     "manifest-01.jsonl", "manifest-x.jsonl"):
            (directory / name).write_text("{}\n", encoding="utf-8")
        self.assertEqual([n for n, _ in self.paths.sourceset_manifests("wpm")], [1, 2, 10])
        self.assertEqual(self.paths.sourceset_manifests("missing"), [])

    def test_unsafe_ids_and_versions_are_refused(self):
        for bad in ("../x", "/etc", "", "a/b"):
            with self.subTest(bad=bad), self.assertRaises(CfError):
                self.paths.sourceset_source_dir("wpm", bad)
            with self.subTest(bad=bad), self.assertRaises(CfError):
                self.paths.universe_dir(bad)
        for version in (0, -1, True, "3"):
            with self.subTest(version=version), self.assertRaises(CfError):
                self.paths.sourceset_manifest("wpm", version)


LAYER = ("A widget is spanning when every gadget meets it. Theorem. Let "
         "G{\\displaystyle G} be a finite widget of order n{\\displaystyle n}; "
         "then G{\\displaystyle G} has a spanning gadget whose size divides n "
         "and every two such gadgets are conjugate under the widget group action.")


def _formulas(layer: str) -> list[dict]:
    out, at = [], 0
    for text in ("G{\\displaystyle G}", "n{\\displaystyle n}", "G{\\displaystyle G}"):
        a = layer.index(text, at)
        out.append({"span": [a, a + len(text)], "alttext": text[1:]})
        at = a + len(text)
    return out


class TestTheHeadText(unittest.TestCase):

    def setUp(self):
        self.map = lt.HeadMap(LAYER, _formulas(LAYER))

    def test_formulas_become_tokens(self):
        self.assertIn("Let ⟦G⟧ be a finite widget of order ⟦n⟧;", self.map.head)
        self.assertNotIn("displaystyle", self.map.head)

    def test_offsets_round_trip_outside_formulas_and_are_refused_inside(self):
        for word in ("Theorem.", "be a finite", "has a spanning", "conjugate"):
            layer_at = LAYER.index(word)
            head_at = self.map.to_head(layer_at)
            self.assertEqual(self.map.head[head_at:head_at + len(word)], word)
            self.assertEqual(self.map.to_layer(head_at), layer_at)
        start = _formulas(LAYER)[0]["span"][0]
        self.assertEqual(self.map.head[self.map.to_head(start)], "⟦")
        with self.assertRaises(lt.HeadError):
            self.map.to_head(start + 1)
        with self.assertRaises(lt.HeadError):
            self.map.to_layer(self.map.to_head(start) + 1)
        self.assertEqual(self.map.to_head(len(LAYER)), len(self.map.head))

    def test_bad_formula_lists_are_refused(self):
        for formulas in ([{"span": [5, 5], "alttext": "x"}],
                         [{"span": [5, 9], "alttext": "x"}, {"span": [8, 12], "alttext": "y"}],
                         [{"span": [0, len(LAYER) + 1], "alttext": "x"}],
                         [{"alttext": "x"}]):
            with self.subTest(formulas=formulas), self.assertRaises(lt.HeadError):
                lt.HeadMap(LAYER, formulas)

    def test_the_tokens_are_cflib_passages_own(self):
        for alttext in ("{\\displaystyle G}", " x ", "{\\displaystyle {x}}", "{\\mathcal {P}}(X)",
                        "{a\\}", "{a}{b}", "\\displaystyle\\alpha", "ｘ　+ 1"):
            with self.subTest(alttext=alttext):
                self.assertEqual(lt.normalize_alttext(alttext), passages.normalize_alttext(alttext))
                self.assertEqual(lt.formula_token(alttext), passages.formula_token(alttext))


class TestThePassageIdsAreInLockstep(unittest.TestCase):

    def heads(self):
        head = lt.HeadMap(LAYER, _formulas(LAYER)).head
        starts = [0, head.index("Theorem."), head.index("Let"), head.index("then"),
                  head.index("and every")]
        return head, starts

    def test_assign_and_hex_agree_with_cflib(self):
        head, starts = self.heads()
        mine = lt.assign_ids(head, starts)
        theirs = passages.assign_passage_ids(head, starts)
        self.assertEqual(mine, [(h.start, h.hex, h.head_words) for h in theirs])
        for start, digest, words in mine:
            self.assertEqual(lt.hex_at(head, start, words), digest)
            self.assertEqual(passages.hex_at(head, start, words), digest)
            self.assertEqual(items.passage_item(digest), f"In-text #{digest}")

    def test_a_shared_prefix_extends_both_heads_in_both(self):
        common = " ".join(f"w{i}" for i in range(14))
        head = f"{common} alpha tail. {common} beta tail."
        starts = [0, head.index(common, 1)]
        mine = lt.assign_ids(head, starts)
        self.assertEqual(mine, [(h.start, h.hex, h.head_words)
                                for h in passages.assign_passage_ids(head, starts)])
        self.assertEqual([w for _s, _h, w in mine], [15, 15])

    def test_the_refusals_are_the_same(self):
        head, starts = self.heads()
        twin = " ".join(f"w{i}" for i in range(45))
        for bad_head, bad_starts in ((head, [starts[0], starts[0]]), (head, [1]),
                                     (head, [len(head) + 5]), (f"{twin} {twin}", [0, len(twin) + 1])):
            with self.subTest(starts=bad_starts):
                with self.assertRaises(lt.HeadError):
                    lt.assign_ids(bad_head, bad_starts)
                with self.assertRaises(PassageIdError):
                    passages.assign_passage_ids(bad_head, bad_starts)
        with self.assertRaises(lt.HeadError):
            lt.hex_at(head, 0, 41)


class TestFindQuote(unittest.TestCase):

    def test_exact_with_context(self):
        text = "a gadget meets it. a gadget meets them."
        self.assertEqual(lt.find_quote(text, "a gadget meets"), [0, 19])
        self.assertEqual(lt.find_quote(text, "gadget meets", prefix="a ", suffix=" them"), [21])
        self.assertEqual(lt.find_quote(text, "gadget  meets"), [])
        self.assertEqual(lt.find_quote(text, ""), [])
        self.assertEqual(lt.find_quote("aaa", "aa"), [0, 1])


if __name__ == "__main__":
    unittest.main()


class TestEveryHostCopyOfTheVocabularyIsPinned(unittest.TestCase):
    """#84/#85: what runs on the host carries its own copy of a cflib.vocab value; each is pinned.

    The gate, the bridge's helpers and the record builder are copied to a run
    host with no cflib, so they inline the values they need.  A change to one in
    cflib.vocab would otherwise silently fork the host: the bridge would write
    UNRESOLVED documents the gate refuses, or the gate would quote passages
    differently from the export.
    """

    def test_the_gate(self):
        from gate import verify_locate as vl
        from locate import plan as locate_plan

        self.assertEqual(vl.GATE_SCHEMA, locate_plan.GATE_SCHEMA)
        self.assertEqual(vl.DOCUMENT_SCHEMA, vocab.LOCATE_DOCUMENT_SCHEMA)
        self.assertEqual(vl.UNRESOLVED_BY, vocab.UNRESOLVED_BY)
        self.assertEqual(vl.MAX_LOCATIONS_PER_PASSAGE, vocab.MAX_LOCATIONS_PER_PASSAGE)
        self.assertEqual(vl.MIN_ABSENCE_QUERIES, vocab.MIN_ABSENCE_QUERIES)
        self.assertEqual(vl.LOCATE_EXTENSION, vocab.LOCATE_EXTENSION)
        self.assertEqual(vl.NO_MATCH, vocab.NO_MATCH)
        self.assertEqual(vl.RELATION_STRENGTH, vocab.RELATION_STRENGTH)
        self.assertEqual(vl.UNRESOLVED_ATTEMPT_CAP, vocab.UNRESOLVED_ATTEMPT_CAP)
        self.assertEqual(vl.LOCATE_ATTEMPT_CAP, vocab.LOCATE_ATTEMPT_CAP)

    def test_the_gate_accepts_exactly_the_cap_reason_the_bridge_writes(self):
        """The gate's ``by: attempt_cap`` form is the text the bridge writes at the cap."""
        from gate import verify_locate as vl
        from run import attempt_cap

        said = vl.ATTEMPT_CAP_REASON.match(attempt_cap.cap_reason(vocab.LOCATE_ATTEMPT_CAP))
        self.assertIsNotNone(said)
        self.assertEqual(int(said.group(1)), vocab.LOCATE_ATTEMPT_CAP)
        self.assertIsNone(vl.ATTEMPT_CAP_REASON.match(
            attempt_cap.cap_reason(vocab.LOCATE_ATTEMPT_CAP) + " and more"))

    def test_one_relation_order(self):
        """The gate and the outcomes accept the same downgrades: one order, in cflib.vocab."""
        from gate import verify_locate as vl
        from locate import outcomes

        self.assertIs(outcomes.RELATION_STRENGTH, vocab.RELATION_STRENGTH)
        self.assertEqual(set(vocab.RELATION_STRENGTH), set(vocab.LOCATE_RELATIONS))
        self.assertEqual(set(vl.RELATION_STRENGTH), vl.LOCATE_RELATIONS)
        s = vocab.RELATION_STRENGTH
        # broadMatch counts for a theorem in derive_status, narrowMatch and hasPart do not
        self.assertLess(s[vocab.BROAD_MATCH], s[vocab.NARROW_MATCH])
        self.assertEqual(s[vocab.NARROW_MATCH], s[vocab.HAS_PART])
        self.assertLess(s[vocab.CLOSE_MATCH], s[vocab.BROAD_MATCH])
        self.assertLess(s[vocab.HAS_PART], s[vocab.RELATED_MATCH])

    def test_the_bridge_helpers(self):
        from run import agent_call, attempt_cap

        self.assertEqual(attempt_cap.UNRESOLVED_ATTEMPT_CAP, vocab.UNRESOLVED_ATTEMPT_CAP)
        self.assertEqual(attempt_cap.DOCUMENT_SCHEMA, vocab.LOCATE_DOCUMENT_SCHEMA)
        self.assertEqual(attempt_cap.LOCATE_INVENTORY, vocab.LOCATE_INVENTORY)
        self.assertEqual(attempt_cap.LOCATE_ATTEMPT_CAP, vocab.LOCATE_ATTEMPT_CAP)
        self.assertEqual(agent_call.DEFAULT_SLOTS, vocab.SESSION_SLOTS)
        self.assertEqual(agent_call.parse_duration(agent_call.DEFAULT_TIMEOUT),
                         agent_call.parse_duration(vocab.AGENT_TIMEOUT))

    def test_the_record_builder(self):
        from locate import records

        self.assertEqual(records.QUOTE_WORDS, vocab.EXPORT_QUOTE_WORDS)
        self.assertEqual(records.CONTEXT_CHARS, vocab.EXPORT_CONTEXT_CHARS)
        self.assertEqual(records.EXTENSION, vocab.LOCATE_EXTENSION)
        self.assertEqual(records.MAX_LOCATIONS, vocab.MAX_LOCATIONS_PER_PASSAGE)
        self.assertEqual(records.NO_MATCH, vocab.NO_MATCH)
        self.assertEqual(records.NOT_FORMALIZED, vocab.STATUS_NOT_FORMALIZED)
        # restated by 568905a (the record set's prominence and closeMatch-note rules)
        self.assertEqual(records.PROMINENCES, vocab.PROMINENCE)
        self.assertEqual(records.CLOSE_MATCH, vocab.CLOSE_MATCH)
        # restated by the coverage claim (the maintainer's decision D1)
        self.assertEqual(records.HAS_PART, vocab.HAS_PART)
        self.assertEqual(records.MIN_COVERAGE_PARTS, vocab.MIN_COVERAGE_PARTS)
        self.assertEqual(records.COVERAGE_KEYS, vocab.COVERAGE_RECORD_KEYS)
        self.assertEqual(records.CHALLENGES, vocab.CHALLENGE_VERDICTS)

    def test_the_workspace_side_reads_cflib(self):
        from harvest import harvest
        from run import campaign

        self.assertEqual(harvest.LOCATE_SCHEMA, vocab.LOCATE_DOCUMENT_SCHEMA)
        self.assertEqual(campaign.MAX_ROOT_TASKS, vocab.MAX_TASKS_PER_ROOT)
