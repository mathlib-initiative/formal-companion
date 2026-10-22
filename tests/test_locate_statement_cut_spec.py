"""The statement cut's shared names: the versions, the shapes, the fates and the keys.

New code, for docs/proposals/statement-cut.md sections 1-4 and 6.  Four
packages build the statement cut in parallel against one set of names; this
module pins the names they share -- ``cflib.vocab``'s statement-cut block and the
revision-free keys of ``cflib.hashing`` -- to the proposal, so a package that
needs another value changes the proposal, ``references/vocabulary.md`` and this
module together.  Nothing here runs a stage; from the proposal's flip the planner
writes these versions, and ``TestTheVersions`` says which.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import hashing, vocab  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from locate import records  # noqa: E402

PROPOSAL = (SKILL / "docs" / "proposals" / "statement-cut.md").read_text(encoding="utf-8")
VOCABULARY_MD = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestTheVersions(unittest.TestCase):
    """Section 2: the minimal set of version keys, and where each is recorded."""

    def test_the_target_versions(self):
        self.assertEqual(vocab.RULES_STATEMENT_CUT, 3)
        self.assertEqual(vocab.SPAN_END_STATEMENT_CUT, 2)
        self.assertEqual(vocab.STATEMENT_CUT_FIRST, 1)
        # the continued cut of inv-pilot-1's fixes, written from their flip on
        self.assertEqual((vocab.SPAN_END_CONTINUED, vocab.STATEMENT_CUT_CONTINUED), (3, 2))
        self.assertEqual(vocab.STATEMENT_CUT_SPAN_END, {1: 2, 2: 3})
        self.assertEqual(vocab.STATEMENT_CUT_RULE, vocab.STATEMENT_CUT_CONTINUED)
        self.assertEqual(vocab.STATEMENT_CUT_RULE, 2)
        self.assertEqual((vocab.STATUS_RULE_D1, vocab.STATUS_RULE_BUNDLE), (1, 2))
        self.assertEqual(vocab.STATUS_RULE, vocab.STATUS_RULE_BUNDLE)

    def test_the_planner_writes_no_version_ahead_of_the_gate(self):
        """From the flip (the proposal's section 7) the written versions are the cut's, and
        from the flip of inv-pilot-1's fixes the continued cut's: span-end rule 3 beside the
        statement-cut rule that reads it, never a pair the gate refuses."""
        self.assertEqual(vocab.JUDGMENT_RULES, vocab.RULES_STATEMENT_CUT)
        self.assertEqual(vocab.SPAN_END_RULE, vocab.SPAN_END_CONTINUED)
        self.assertEqual(vocab.STATEMENT_CUT_SPAN_END[vocab.STATEMENT_CUT_RULE],
                         vocab.SPAN_END_RULE)
        from gate import verify_locate as vl
        self.assertLessEqual(vocab.SPAN_END_RULE, vl.KNOWN_SPAN_END_RULE)
        self.assertLessEqual(vocab.STATEMENT_CUT_RULE, vl.KNOWN_STATEMENT_CUT_RULE)
        self.assertLess(vocab.RULES_PD5_PD9, vocab.RULES_STATEMENT_CUT)
        self.assertEqual(vocab.CHALLENGE_ABSENCE_SAMPLE_RATE, 0.5)          # fix 5c
        from locate import plan as lplan
        self.assertEqual(lplan.rule_versions(), {
            "judgment_rules": vocab.RULES_STATEMENT_CUT,
            "span_end_rule": vocab.SPAN_END_CONTINUED,
            "statement_cut_rule": vocab.STATEMENT_CUT_CONTINUED})
        self.assertEqual(tuple(lplan.rule_versions().values()), (3, 3, 2))
        self.assertEqual(lplan.status_rule(), vocab.STATUS_RULE_BUNDLE)

    def test_the_proposal_names_every_key(self):
        for key in ("judgment_rules", "span_end_rule", "statement_cut_rule", "status_rule",
                    "cf-locate-recordset/2", "cf-locate-carry/1",
                    "cf-locate-status-parity/2"):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", PROPOSAL)


class TestTheShapes(unittest.TestCase):
    """Section 1: the closed list of compound shapes and the conjuncts' bounds."""

    def test_the_closed_list(self):
        self.assertEqual(vocab.COMPOUND_SHAPES, {"existence_uniqueness", "iff", "cases",
                                                 "conclusions", "equations"})
        self.assertEqual(frozenset(vocab.COMPOUND_SHAPE_ORDER), vocab.COMPOUND_SHAPES)
        self.assertEqual(len(vocab.COMPOUND_SHAPE_ORDER), len(vocab.COMPOUND_SHAPES))

    def test_the_conjunct_bounds(self):
        self.assertEqual((vocab.MIN_CONJUNCTS, vocab.MAX_CONJUNCTS), (2, 5))
        self.assertLessEqual(set(vocab.SHAPE_CONJUNCT_COUNTS), vocab.COMPOUND_SHAPES)
        for shape, count in vocab.SHAPE_CONJUNCT_COUNTS.items():
            with self.subTest(shape=shape):
                self.assertTrue(vocab.MIN_CONJUNCTS <= count <= vocab.MAX_CONJUNCTS)
        self.assertEqual(vocab.SHAPE_CONJUNCT_COUNTS, {"existence_uniqueness": 2})
        self.assertLessEqual(vocab.MAX_CONJUNCTS, vocab.MAX_PASSAGE_CLAIMS)

    def test_a_simple_statement_has_one_clause_id_of_the_claim_grammar(self):
        self.assertEqual(vocab.SIMPLE_STATEMENT_CLAUSE, "c1")
        self.assertTrue(vocab.CLAIM_ID.match(vocab.SIMPLE_STATEMENT_CLAUSE))

    def test_the_relations_of_parts_and_bundles(self):
        self.assertEqual(vocab.CONJUNCT_RELATIONS,
                         vocab.LOCATE_RELATIONS - {vocab.HAS_PART, vocab.RELATED_MATCH})
        self.assertEqual(vocab.BUNDLE_RELATIONS, vocab.STRONG_RELATIONS)
        self.assertLess(vocab.BUNDLE_RELATIONS, vocab.CONJUNCT_RELATIONS)
        self.assertEqual(vocab.TWO_WAY_TEST_KEYS, ("r2", "r3"))

    def test_the_bundle_record_keeps_the_d1_keys(self):
        self.assertEqual(vocab.COVERAGE_RECORD_KEYS_BUNDLE - vocab.COVERAGE_RECORD_KEYS,
                         {"relation", "conjuncts", "bundle_key"})

    def test_the_cut_vocabulary(self):
        self.assertEqual(vocab.CUT_CONNECTIVES,
                         (";", "since", "because", "so", "hence", "then", "moreover", ", and"))
        self.assertEqual(vocab.CUT_FLAGS, {"not_a_statement", "several_statements"})

    def test_the_cut_generality_words_extend_the_old_list(self):
        old = vocab.CLOSE_MATCH_GENERALITY_PHRASES
        new = vocab.CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT
        self.assertEqual(new[:len(old)], old)
        self.assertEqual(new[len(old):], ("table", "for all primes", "every point",
                                          "differentiable at"))

    def test_an_older_document_keeps_its_rows(self):
        """The two new location fields come last: a rules-2 location's copy is unchanged."""
        self.assertEqual(vocab.JUDGMENT_LOCATION_FIELDS[:4],
                         ("clauses", "instantiation", "step", "convention"))
        self.assertEqual(vocab.JUDGMENT_LOCATION_FIELDS[4:], ("conjunct_relation", "tests"))


class TestTheFates(unittest.TestCase):
    """Section 4: the carry-over map's closed list of fates."""

    def test_the_closed_list(self):
        self.assertEqual(vocab.CARRY_FATES, {"unchanged", "end_moved", "split", "merged",
                                             "reshaped", "removed", "changed"})
        self.assertNotIn(vocab.CARRY_NEW, vocab.CARRY_FATES)


class TestEveryValueIsDocumented(unittest.TestCase):
    """CLAUDE.md section 4: a new value is a change to vocab.py AND references/vocabulary.md."""

    def test_every_value(self):
        values = (vocab.COMPOUND_SHAPES | vocab.CARRY_FATES | {vocab.CARRY_NEW}
                  | vocab.CUT_FLAGS | {"conjunct_relation", "tests", "statement_cut_rule",
                                       "status_rule", "passage_key", "pair_key", "bundle_key",
                                       "cut_id"})
        for value in sorted(values):
            with self.subTest(value=value):
                self.assertIn(f"`{value}`", VOCABULARY_MD)
                self.assertIn(f"`{value}`", PROPOSAL)


LAYER = "A widget is finite. Every finite widget has a spanning gadget; it is unique."
PAGE = 1001
REPO = "https://example.org/alpha"


class TestTheKeys(unittest.TestCase):
    """Section 4: the revision-free keys, each one definition, pinned to its formula."""

    def test_span_sha256_is_the_records(self):
        for span in ([0, 19], [20, 61], [63, 75]):
            with self.subTest(span=span):
                self.assertEqual(hashing.span_sha256(LAYER, span),
                                 records.sha256_text(LAYER[span[0]:span[1]]))
                self.assertEqual(hashing.span_sha256(LAYER, span),
                                 sha(LAYER[span[0]:span[1]]))

    def test_passage_key(self):
        text_sha = sha(LAYER[20:61])
        self.assertEqual(hashing.passage_key(PAGE, text_sha), sha(f"{PAGE}\n{text_sha}"))

    def test_the_same_text_is_the_same_passage_wherever_it_moves(self):
        moved = "A lead sentence. " + LAYER
        at = moved.index("Every finite")
        self.assertEqual(hashing.passage_key(PAGE, hashing.span_sha256(moved, [at, at + 41])),
                         hashing.passage_key(PAGE, hashing.span_sha256(LAYER, [20, 61])))
        self.assertNotEqual(hashing.passage_key(PAGE, hashing.span_sha256(LAYER, [20, 61])),
                            hashing.passage_key(PAGE + 1, hashing.span_sha256(LAYER, [20, 61])))

    def test_pair_key(self):
        pk = hashing.passage_key(PAGE, sha("x"))
        self.assertEqual(hashing.pair_key(pk, REPO, "Widget.exists_gadget"),
                         sha(f"{pk}\n{REPO}\nWidget.exists_gadget"))

    def test_bundle_key_is_order_free(self):
        pk = hashing.passage_key(PAGE, sha("x"))
        a = hashing.pair_key(pk, REPO, "Widget.exists_gadget")
        b = hashing.pair_key(pk, REPO, "Widget.gadget_unique")
        self.assertEqual(hashing.bundle_key(pk, [a, b]), hashing.bundle_key(pk, [b, a]))
        self.assertEqual(hashing.bundle_key(pk, [b, a]), sha("\n".join([pk, *sorted([a, b])])))

    def test_cut_id(self):
        layer_sha = sha(LAYER)
        want = sha(json.dumps({"pageid": PAGE, "layer_sha256": layer_sha,
                               "spans": [[0, 19], [20, 61]]},
                              sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        self.assertEqual(hashing.cut_id(PAGE, layer_sha, [[20, 61], (0, 19)]), want)

    def test_what_no_key_accepts(self):
        pk = hashing.passage_key(PAGE, sha("x"))
        pair = hashing.pair_key(pk, REPO, "Widget.exists_gadget")
        refused = [
            lambda: hashing.passage_key(True, sha("x")),
            lambda: hashing.passage_key(0, sha("x")),
            lambda: hashing.passage_key("1001", sha("x")),
            lambda: hashing.passage_key(PAGE, "ABC"),
            lambda: hashing.pair_key(pk, "", "Widget.exists_gadget"),
            lambda: hashing.pair_key(pk, REPO + " ", "Widget.exists_gadget"),
            lambda: hashing.pair_key(pk, REPO, "Widget.a\nWidget.b"),
            lambda: hashing.bundle_key(pk, [pair]),
            lambda: hashing.bundle_key(pk, [pair, pair]),
            lambda: hashing.span_sha256(LAYER, [5, 5]),
            lambda: hashing.span_sha256(LAYER, [0, len(LAYER) + 1]),
            lambda: hashing.cut_id(PAGE, sha("x"), [[3, 2]]),
        ]
        for i, call in enumerate(refused):
            with self.subTest(case=i):
                with self.assertRaises(CfError):
                    call()


class TestThePromptSlots(unittest.TestCase):
    """Section 6: the slots the cut and fixes packages render, defined once."""

    def test_the_slots_say_what_vocab_defines(self):
        from locate import presets as lpresets, prompts as lprompts

        preset = lpresets.get(vocab.LOCATE_INVENTORY)

        class _Universe:
            id, sha256, libraries = "u1", "0" * 64, {}

        host = {"gate_dir": "<gate>", "tools_corpus_db": "<db>", "gate_command": "<gate cmd>",
                "sources_dir": "<sources>", "candidates_dir": "<candidates>",
                "tasks_dir": "<tasks>"}
        ctx = lprompts.build_context(preset, run_id="r1", host=host, universe=_Universe(),
                                     per_task=8)
        self.assertEqual(ctx["compound_shapes"], "`existence_uniqueness`, `iff`, `cases`, "
                                                 "`conclusions`, `equations`")
        self.assertEqual((ctx["min_conjuncts"], ctx["max_conjuncts"]), (2, 5))
        self.assertEqual(ctx["cut_flags"], "`not_a_statement`, `several_statements`")
        self.assertIn('"differentiable at"', ctx["generality_phrases_statement_cut"])
        self.assertNotIn('"differentiable at"', ctx["generality_phrases"])
        self.assertTrue(ctx["cut_connectives"].startswith('";", "since"'))
        self.assertEqual(ctx["simple_statement_clause"], "c1")


class TestTheProposalIsSynthetic(unittest.TestCase):
    """CLAUDE.md section 1: examples in the repo are synthetic."""

    def test_no_real_passage_id(self):
        """An item hex is a hash of a real article's words: the proposal's are made up."""
        for hexes in re.findall(r"#([0-9a-f]{8})\b", PROPOSAL):
            with self.subTest(item=hexes):
                self.assertTrue(hexes.startswith("0000"), f"In-text #{hexes} is not synthetic")

    def test_no_wikidata_id(self):
        # Q1-Q9 are the maintainer's question numbers; a QID would have more digits
        self.assertEqual(re.findall(r"\bQ[1-9][0-9]+\b", PROPOSAL), [])


if __name__ == "__main__":
    unittest.main()
