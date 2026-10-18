"""The locate foundation: vocabulary, the passage item, the head hash, licence classes.

New code, for milestone L-M0 of docs/proposals/wikipedia-locate.md (sections
2.1, 2.2, 2.4, 2.6, 2.7, 5 and 10).  Everything here is what the other locate
packages -- renderer, sourcesets, corpus, planner, gate, bench -- build on, so
the tests pin the definitions rather than any stage: the values and where they
are documented, the ``In-text #<hex>`` production and the proof that adding
it moved no existing id, the head hash and its refusals, and the registry's
licence class and web-source family.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from formal_companion_annotations import ids as fmt_ids  # noqa: E402
from formal_companion_annotations import vocab as fmt_vocab  # noqa: E402

from cflib import hashing, items, passages, registry, vocab, yamlio  # noqa: E402
from cflib.errors import CfError, ItemGrammarError, PassageIdError  # noqa: E402

VOCABULARY_MD = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")


def _scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------

class TestTheLocateModesAndKinds(unittest.TestCase):

    def test_the_three_modes_are_run_modes_with_their_kinds(self):
        self.assertEqual(vocab.LOCATE_MODES, {"locate-inventory", "locate", "locate-challenge"})
        self.assertLessEqual(vocab.LOCATE_MODES, vocab.RUN_MODES)
        self.assertEqual(vocab.kinds_for_mode("locate-inventory"), {"PASSAGE", "UNRESOLVED"})
        self.assertEqual(vocab.kinds_for_mode("locate"), {"LOCATION", "ABSENCE", "UNRESOLVED"})
        self.assertEqual(vocab.kinds_for_mode("locate-challenge"),
                         {"LOCATION", "ABSENCE", "UNRESOLVED"})
        for mode in vocab.RUN_MODES:
            with self.subTest(mode=mode):
                self.assertIn(mode, vocab.KINDS_BY_MODE)
        self.assertTrue(vocab.locate_mode(vocab.LOCATE))
        self.assertFalse(vocab.locate_mode(vocab.DIFFERENTIAL))

    def test_no_locate_mode_is_an_attack_and_none_needs_a_witness(self):
        for mode in vocab.LOCATE_MODES:
            for kind in vocab.kinds_for_mode(mode):
                self.assertFalse(vocab.witness_required(mode, kind))

    def test_the_faithfulness_kinds_did_not_move(self):
        """The ledger validator, the answer key and harvest's substantive kinds read them."""
        self.assertEqual(vocab.FINDING_KINDS, {"CONTENT", "VACUITY", "PROXY",
                                               "SUSPECTED_ERRATA", "COVERAGE", "PAGE_ANCHOR"})
        self.assertFalse(vocab.FINDING_KINDS & vocab.LOCATE_FINDING_KINDS)
        self.assertEqual(vocab.ALL_FINDING_KINDS, vocab.FINDING_KINDS | vocab.LOCATE_FINDING_KINDS)
        from harvest._compat import SUBSTANTIVE_KINDS
        self.assertEqual(SUBSTANTIVE_KINDS, {"CONTENT", "VACUITY", "PROXY", "SUSPECTED_ERRATA"})
        for mode in (vocab.DIFFERENTIAL, vocab.SINGLE_SOURCE, vocab.ATTACK_VAC, vocab.ATTACK_HUNT):
            self.assertFalse(vocab.kinds_for_mode(mode) & vocab.LOCATE_FINDING_KINDS)

    def test_the_presets_still_derive_the_same_gate_kinds(self):
        from plan import presets
        self.assertEqual(presets.DIFFERENTIAL_KINDS,
                         ["CONTENT", "COVERAGE", "SUSPECTED_ERRATA", "VACUITY"])
        self.assertEqual(presets.ATTACK_KINDS, ["CONTENT", "SUSPECTED_ERRATA", "VACUITY"])


class TestTheLocateValues(unittest.TestCase):

    def test_the_sets_are_the_proposals(self):
        self.assertEqual(vocab.LOCATE_RELATIONS, {"exactMatch", "closeMatch", "broadMatch",
                                                  "narrowMatch", "hasPart", "relatedMatch"})
        self.assertEqual(vocab.NO_MATCH, "noMatch")
        self.assertNotIn(vocab.NO_MATCH, vocab.LOCATE_RELATIONS)
        self.assertEqual(vocab.PASSAGE_KINDS, {"definition", "theorem", "conjecture", "example",
                                               "construction", "notation", "other"})
        self.assertEqual(vocab.PROMINENCE, {"subject", "central", "supporting"})
        self.assertEqual(vocab.SCOPES, {"formal_math", "expository", "non_math"})
        self.assertEqual(vocab.LOCATED_SCOPES, {"formal_math"})
        self.assertEqual(vocab.PROOF_STATUSES, ("wanted", "statement_only", "axiom_dependent",
                                                "proved", "definition"))
        self.assertEqual(vocab.DERIVED_STATUSES, ("formalized", "statement_only", "partial",
                                                  "not_formalized"))
        # proposal section 2.7, with challenge_pending since smoke 2's CH2
        self.assertEqual(vocab.CHALLENGE_VERDICTS, {"survived", "downgraded", "not_required",
                                                    "sampled_out", "challenge_pending"})
        self.assertEqual(vocab.LICENCE_CLASSES, {"closed", "open_attribution",
                                                 "open_share_alike", "public_domain"})
        self.assertEqual(vocab.DEFAULT_LICENCE_CLASS, "closed")
        self.assertNotIn("closed", vocab.OPEN_LICENCE_CLASSES)
        self.assertEqual(vocab.LOCATE_EXTENSION, "org.mathlib-initiative.formal-companion.locate")
        self.assertEqual(vocab.MAX_LOCATIONS_PER_PASSAGE, 5)
        self.assertEqual(vocab.REFUTE_BY_DEFAULT_PROMINENCE, {"subject", "central"})
        self.assertLessEqual(vocab.WEAK_RELATIONS, vocab.LOCATE_RELATIONS)

    def test_the_passage_kind_rows_cover_every_kind_once(self):
        rows = (vocab.THEOREM_PASSAGE_KINDS, vocab.DEFINITION_PASSAGE_KINDS,
                vocab.EITHER_PASSAGE_KINDS)
        self.assertEqual(set().union(*rows), vocab.PASSAGE_KINDS)
        self.assertEqual(sum(len(r) for r in rows), len(vocab.PASSAGE_KINDS))

    def test_every_value_is_documented_in_the_vocabulary(self):
        """CLAUDE.md S4: a new value is a change to vocab.py AND references/vocabulary.md."""
        values = (set(vocab.LOCATE_MODES) | vocab.LOCATE_FINDING_KINDS | vocab.LOCATE_RELATIONS
                  | {vocab.NO_MATCH} | vocab.PASSAGE_KINDS | vocab.PROMINENCE | vocab.SCOPES
                  | set(vocab.PROOF_STATUSES) | set(vocab.DERIVED_STATUSES)
                  | vocab.CHALLENGE_VERDICTS | vocab.LICENCE_CLASSES | {vocab.LOCATE_EXTENSION}
                  | {items.REJECT_BAD_PASSAGE_ID})
        for value in sorted(values):
            with self.subTest(value=value):
                self.assertIn(f"`{value}`", VOCABULARY_MD)

    def test_nothing_new_leaked_into_the_vendored_format(self):
        """The format owns its vocabulary; the locate sets are the skill's, like the kinds."""
        for name in ("LOCATE_RELATIONS", "PASSAGE_KINDS", "PROOF_STATUSES", "DERIVED_STATUSES",
                     "CHALLENGE_VERDICTS", "LICENCE_CLASSES", "LOCATE_FINDING_KINDS"):
            self.assertFalse(hasattr(fmt_vocab, name), name)
        # the one value the formats share: the vendored coverage class of the same name
        self.assertIn(vocab.STATUS_FORMALIZED, fmt_vocab.ANNOTATION_COVERAGE)
        self.assertIn(vocab.STATUS_NOT_FORMALIZED, fmt_vocab.ANNOTATION_COVERAGE)


class TestTheProofStatusOrder(unittest.TestCase):

    def decide(self, wanted=False, axioms=(), is_prop=True):
        return vocab.decide_proof_status(wanted=wanted, axioms=axioms, is_prop=is_prop)

    def test_each_rule_in_order(self):
        self.assertEqual(self.decide(), "proved")
        self.assertEqual(self.decide(axioms=["propext", "Classical.choice", "Quot.sound"]),
                         "proved")
        self.assertEqual(self.decide(is_prop=False), "definition")
        self.assertEqual(self.decide(axioms=["sorryAx"]), "statement_only")
        self.assertEqual(self.decide(axioms=["Foo.myAxiom"]), "axiom_dependent")
        self.assertEqual(self.decide(wanted=True), "wanted")

    def test_the_first_rule_that_applies_wins_whatever_the_kind(self):
        self.assertEqual(self.decide(wanted=True, axioms=["sorryAx", "Foo.ax"]), "wanted")
        self.assertEqual(self.decide(axioms=["sorryAx", "Foo.ax"]), "statement_only")
        self.assertEqual(self.decide(axioms=["sorryAx"], is_prop=False), "statement_only")
        self.assertEqual(self.decide(axioms=["Foo.ax"], is_prop=False), "axiom_dependent")


class TestTheDerivedStatus(unittest.TestCase):
    """docs/proposals/wikipedia-locate.md section 2.6, row by row."""

    @staticmethod
    def loc(relation, proof_status, statement=False):
        return {"relation": relation, "proof_status": proof_status, "statement": statement}

    def status(self, kind, *locations, absent=False):
        return vocab.derive_status(kind, list(locations), absent)

    def test_formalized(self):
        for relation in ("exactMatch", "closeMatch", "broadMatch"):
            with self.subTest(relation=relation):
                self.assertEqual(self.status("theorem", self.loc(relation, "proved")), "formalized")
                self.assertEqual(self.status("conjecture", self.loc(relation, "proved")),
                                 "formalized")
        for relation in ("exactMatch", "closeMatch"):
            with self.subTest(relation=relation):
                self.assertEqual(self.status("definition", self.loc(relation, "definition")),
                                 "formalized")

    def test_close_match_counts_as_formalized(self):
        self.assertEqual(self.status("theorem", self.loc("closeMatch", "proved")), "formalized")

    def test_statement_only(self):
        for proof_status in ("statement_only", "wanted", "axiom_dependent"):
            with self.subTest(proof_status=proof_status):
                self.assertEqual(self.status("theorem", self.loc("exactMatch", proof_status)),
                                 "statement_only")
                self.assertEqual(self.status("definition", self.loc("closeMatch", proof_status)),
                                 "statement_only")

    def test_a_prop_valued_definition_never_formalizes_a_theorem_passage(self):
        riemann = self.loc("exactMatch", "definition", statement=True)
        self.assertEqual(self.status("conjecture", riemann), "statement_only")
        self.assertEqual(self.status("theorem", riemann), "statement_only")
        for proof_status in ("wanted", "statement_only", "axiom_dependent"):
            with self.subTest(proof_status=proof_status):
                self.assertEqual(self.status("theorem",
                                             self.loc("exactMatch", proof_status, True)),
                                 "statement_only")
        # a predicate IS a definition of a definition passage
        self.assertEqual(self.status("definition", riemann), "formalized")

    def test_a_location_without_the_statement_flag_is_an_ordinary_one(self):
        """The flag is optional: most index rows are theorems, and leave it out."""
        bare = {"relation": "exactMatch", "proof_status": "proved"}
        self.assertEqual(vocab.derive_status("theorem", [bare], False), "formalized")
        self.assertEqual(vocab.derive_status("theorem", [dict(bare, statement=None)], False),
                         "formalized")

        class Located:
            relation, proof_status = "exactMatch", "proved"
        self.assertEqual(vocab.derive_status("theorem", [Located()], False), "formalized")
        self.assertEqual(vocab.derive_status("definition",
                                             [{"relation": "exactMatch",
                                               "proof_status": "definition"}], False),
                         "formalized")

    def test_partial(self):
        """The relation decides before the proof status or the flag: a part is a part.

        Whatever the declaration -- proved, sorry'd, wanted, a Prop-valued def --
        a location that states only part of the passage, or a special case of
        it, is ``partial``; ranking a sorry'd lemma stating one part as
        ``statement_only`` would overclaim the passage.
        """
        for proof_status in vocab.PROOF_STATUSES:
            for statement in (False, True):
                if statement and proof_status == "proved":
                    continue                    # refused: see the contradictions test
                for relation in ("hasPart", "narrowMatch"):
                    with self.subTest(relation=relation, proof_status=proof_status,
                                      statement=statement):
                        location = self.loc(relation, proof_status, statement)
                        for kind in ("theorem", "conjecture", "definition", "construction",
                                     "notation", "example", "other"):
                            self.assertEqual(self.status(kind, location), "partial", kind)
                with self.subTest(relation="broadMatch", proof_status=proof_status,
                                  statement=statement):
                    location = self.loc("broadMatch", proof_status, statement)
                    for kind in ("definition", "construction", "notation"):
                        self.assertEqual(self.status(kind, location), "partial", kind)
        self.assertEqual(self.status("theorem", self.loc("broadMatch", "definition")), "partial")

    def test_not_formalized_and_unresolved(self):
        self.assertEqual(self.status("theorem", absent=True), "not_formalized")
        self.assertEqual(self.status("theorem", self.loc("relatedMatch", "proved"), absent=True),
                         "not_formalized")
        self.assertIsNone(self.status("theorem"))

    def test_related_matches_alone_with_no_absence_are_unresolved(self):
        """The state a challenge leaves when it downgrades the one location.

        Nothing says the passage is formalized and nobody checked that it is
        absent, so it has no status: counted as unresolved (a miss for the
        safety metric), never as ``not_formalized`` -- a decision documented in
        references/vocabulary.md section 1.11.
        """
        for kind in sorted(vocab.PASSAGE_KINDS):
            with self.subTest(kind=kind):
                self.assertIsNone(self.status(kind, self.loc("relatedMatch", "proved"),
                                              self.loc("relatedMatch", "definition")))
        self.assertIn("downgrades", VOCABULARY_MD)

    def test_the_best_location_wins(self):
        self.assertEqual(self.status("theorem", self.loc("hasPart", "proved"),
                                     self.loc("exactMatch", "statement_only")), "statement_only")
        self.assertEqual(self.status("theorem", self.loc("exactMatch", "statement_only"),
                                     self.loc("broadMatch", "proved")), "formalized")

    def test_a_theorem_stated_exactly_by_data_is_formalized(self):
        """The maintainer's decision D1: an equivalence or other data declaration that
        states the theorem (the Chinese remainder theorem as a RingEquiv) formalizes it;
        its broadMatch stays partial, and a Prop-valued definition stays statement_only."""
        for kind in ("theorem", "conjecture"):
            for relation in ("exactMatch", "closeMatch"):
                with self.subTest(kind=kind, relation=relation):
                    self.assertEqual(self.status(kind, self.loc(relation, "definition")),
                                     "formalized")
                    self.assertEqual(self.status(kind, self.loc(relation, "definition", True)),
                                     "statement_only")
                    self.assertEqual(self.status(kind, self.loc(relation, "statement_only")),
                                     "statement_only")
        self.assertEqual(self.status("theorem", self.loc("broadMatch", "definition")), "partial")

    def test_the_cases_the_table_does_not_name_are_conservative(self):
        # a definition passage stated exactly by a theorem
        self.assertEqual(self.status("definition", self.loc("exactMatch", "proved")), "partial")
        # construction and notation are definition passages
        self.assertEqual(self.status("construction", self.loc("exactMatch", "definition")),
                         "formalized")
        self.assertEqual(self.status("notation", self.loc("broadMatch", "proved")), "partial")
        # an example takes the better of the two rows
        self.assertEqual(self.status("example", self.loc("exactMatch", "definition")),
                         "formalized")
        self.assertEqual(self.status("example", self.loc("broadMatch", "proved")), "formalized")
        self.assertEqual(self.status("other", self.loc("narrowMatch", "proved")), "partial")
        # ...and so does an `other` passage: each row alone would answer `partial`
        self.assertEqual(self.status("other", self.loc("exactMatch", "definition")),
                         "formalized")
        self.assertEqual(self.status("other", self.loc("broadMatch", "proved")), "formalized")
        self.assertEqual(self.status("other", self.loc("exactMatch", "proved")), "formalized")

    def test_objects_work_as_well_as_mappings(self):
        class Located:
            relation, proof_status, statement = "exactMatch", "proved", False
        self.assertEqual(vocab.derive_status("theorem", [Located()], False), "formalized")

    def test_contradictions_and_unknown_values_are_refused(self):
        with self.assertRaises(CfError):
            self.status("theorem", self.loc("exactMatch", "proved"), absent=True)
        with self.assertRaises(CfError):
            self.status("lemma", self.loc("exactMatch", "proved"))
        with self.assertRaises(CfError):
            self.status("theorem", self.loc("noMatch", "proved"))
        with self.assertRaises(CfError):
            self.status("theorem", self.loc("exactMatch", "sorry"))
        with self.assertRaises(CfError):
            self.status("theorem", {"relation": "exactMatch"})
        # the statement flag marks a Prop-valued DEFINITION, never a proved theorem
        for kind in ("theorem", "definition", "example"):
            with self.subTest(kind=kind):
                with self.assertRaises(CfError) as caught:
                    self.status(kind, self.loc("exactMatch", "proved", statement=True))
                self.assertIn("statement flag", caught.exception.message)
        with self.assertRaises(CfError):
            self.status("theorem", self.loc("relatedMatch", "proved", statement=True))
        for flag in ("false", 1, "yes"):
            with self.subTest(statement=flag):
                with self.assertRaises(CfError):
                    self.status("theorem", self.loc("exactMatch", "definition", statement=flag))


# ---------------------------------------------------------------------------
# the item production
# ---------------------------------------------------------------------------

#: (raw, parse, reject, join key, join reject, finding_id) computed with the
#: items module BEFORE `In-text #<hex>` existed.  None of these may move.
PINNED = (
    ("Exercise 1.3.19", "Exercise 1.3.19", None, "Exercise 1.3.19", None,
     "92c24780898412a6cb4010a9cdcc37bae45f02ad8d7364056c19d03c73b73c28"),
    ("Exercise    1.3.19", "Exercise 1.3.19", None, "Exercise 1.3.19", None,
     "92c24780898412a6cb4010a9cdcc37bae45f02ad8d7364056c19d03c73b73c28"),
    ("Exercise 1.6.27(iii)", "Exercise 1.6.27(iii)", None, "Exercise 1.6.27(iii)", None,
     "938a14fc00b0eeda6747919534199c829fe4b5a0735ac663747e337737c689ff"),
    ("Definition 1.2.2 (Lebesgue measurability)", "Definition 1.2.2", None,
     "Definition 1.2.2 (Lebesgue measurability)", None,
     "33bf1550dbf3ead98227bdc8b5962207b3a0eba20d16e69a6466963cb0c09281"),
    ("Corollary (Lemma 1.2.5 + Exercise 1.2.4)", None, "no_number",
     "Corollary (Lemma 1.2.5 + Exercise 1.2.4)", None,
     "a269a62132b5ecf97b246c872daed481182416879c686b1369334eaeb0027d4c"),
    ("In-text, book p. 71", "In-text p.71", None, "In-text p.71", None,
     "bcb418fb7ef29898e32f57f15f9fcfb7b460b3ccdf2bf3b7f961eaa236428ac5"),
    ("In-text p.71", "In-text p.71", None, "In-text p.71", None,
     "da0f37eb15f68b9362b83d29417bd28e736986b694e55c98d5d628244081f7fb"),
    ("In-text convention, book p. 29 (Exercise 1.2.5: ...)", "In-text p.29", None,
     "In-text p.29", None,
     "fa24ad7a28be5108ea00f082932a809dac9e4034c8ad71ca1a25dcced02b06b6"),
    ("In-text, Definition 1.4.35", "In-text Definition 1.4.35", None,
     "In-text Definition 1.4.35", None,
     "96ce2a4ca9c6915932244fd1e5d1ff75100dfdc29eb2074cb30a8734759edbf2"),
    ("In-text", None, "in_text_no_target", "In-text", "in_text_no_target",
     "810ac55092e7fb5290218e4166b9a431e66ba18915a7adc8817fb10dc2946865"),
    ("In-text, somewhere", None, "in_text_no_target", "In-text, somewhere", "in_text_no_target",
     "79718bcc555f0e1c8cbee39a874cdc319492dba319f809fa048571bd75a42536"),
    ("In-text step in the proof of Lemma 1.2.13(ii)", "In-text Lemma 1.2.13(ii)", None,
     "In-text Lemma 1.2.13(ii)", None,
     "eb7d699fa3379811005490a701a309ba616be46a650d9f34a1789c426239b9d4"),
    ("In-text, book p. 51 / Definition 1.3.6", "In-text p.51", None, "In-text p.51", None,
     "b070bf4fb8f91625caa2595dac44317d8c113d021d0cf7c0c0184bc23c114c9c"),
    ("In-text pp. 40-41", "In-text p.40", None, "In-text p.40", None,
     "44ff67d53232dbeef28e5119881b54c99ca87b1a546c2a4f34c5f4cd6a3c2aef"),
    ("", None, "empty", "", "empty",
     "d5c849e587db977ecf84ad4206d2dbe803457a5091c33091617d14dc519cce33"),
    ("Theorem 2.3.4.5", None, "too_many_segments", "Theorem 2.3.4.5", None,
     "fd0b2f9b3be97206ebab4774c08b7b19456879c09d1eb09768e054e835c12b79"),
    # a `#` body that carries a printed page or a numbered item parsed, and
    # joined, as that page or item; the passage production may not take it
    ("In-text #3, book p. 71", "In-text p.71", None, "In-text p.71", None,
     "1824e76401b23e27733e32d1a46cbaf1fd71299d7649db214e844b7bc5dbdb4a"),
    ("In-text #2 (p. 40)", "In-text p.40", None, "In-text p.40", None,
     "7449b3df528fc1d1351982d1afb0467de2c563a5fca332bcd73e7021747c321f"),
    ("In-text #1, Definition 1.4.35", "In-text Definition 1.4.35", None,
     "In-text Definition 1.4.35", None,
     "eca773eee6dec1d866d1072a28250a9dc3910ffad8d35aacbee84223c37425ff"),
    ("In-text #12 p.5", "In-text p.5", None, "In-text p.5", None,
     "5b37c20cd4d7d110fdd0cdd2e691db489160ed6a059ec7228756715986990280"),
    ("In-text # p. 12", "In-text p.12", None, "In-text p.12", None,
     "a1823794307952c04cf3a2628d9117ec7caf89109243639b779b544c98989b84"),
    ("In-text #3fa91c07, p. 12", "In-text p.12", None, "In-text p.12", None,
     "6697708cce6d7550dab17b75e91cb1f3b8b3430e60d79c09fecf2c10a00d59b6"),
    ("In-text #3fa91c07 p. 12", "In-text p.12", None, "In-text p.12", None,
     "5bb177534b7d7a111656937a71db2b502fc5a84f823ae90aafa144ebb27fd789"),
    ("In-text #p 12", "In-text p.12", None, "In-text p.12", None,
     "7482f60d6ac919f98d21497fd3497a9932efaeb8da9be0b4ae55c3bcfc3e75ef"),
    ("In-text #deadbeef Theorem 1.2.3", "In-text Theorem 1.2.3", None,
     "In-text Theorem 1.2.3", None,
     "4e410317dd301a215cf192354ad42179c065b7ae5c9cb4b09625bbd226a0716f"),
    ("In-text #2, Theorem 1.2.3 (ii)", "In-text Theorem 1.2.3(ii)", None,
     "In-text Theorem 1.2.3(ii)", None,
     "93b620ad9bbc1ab499f302db0dd5f0b5a352e665cced7a9106d927e528d6c505"),
)
#: finding_id of the passage labels BEFORE the production existed: the id is
#: the label as written, so admitting the label changed none of them.
PINNED_PASSAGE_IDS = {
    "In-text #3fa91c07": "a527661bc289e76fb7d11a0c84e4585f1981dd7f63917ace270a009b060f164d",
    "In-text # 3fa91c07": "042e1f91b5777dfbce34c86685aab0499ab752eca483514c4f71ad00a71ab98b",
    "In-text#3fa91c07": "6cd8b432223f96ccbd61458ad5bd542b87542b83e9eb7eba5f89e54bb7e0cb84",
    "in-text #0123456789abcdef":
        "64f3d4354c1c4cb3cfeb8f532de24256950379f609f09ccd5b38539cd0033489",
}


def _fid(label: str) -> str:
    return hashing.finding_id("run-1", "src-a", label, "CONTENT", ["ours:Foo.bar", "adam:Baz"])


class TestNoExistingIdMoved(unittest.TestCase):

    def test_parse_join_and_finding_id_are_what_they_were(self):
        for raw, parsed, reason, key, join_reason, fid in PINNED:
            with self.subTest(raw=raw):
                item, reject = items.try_parse(raw)
                self.assertEqual(item.full if item else None, parsed)
                self.assertEqual(reject.reason if reject else None, reason)
                got_key, join_reject = items.try_join_key(raw)
                self.assertEqual(got_key, key)
                self.assertEqual(join_reject.reason if join_reject else None, join_reason)
                self.assertEqual(_fid(raw), fid)

    def test_the_passage_labels_hash_as_they_always_did(self):
        for label, fid in PINNED_PASSAGE_IDS.items():
            with self.subTest(label=label):
                self.assertEqual(items.clean(label), " ".join(label.split()))
                self.assertEqual(_fid(label), fid)

    def test_clean_is_still_whitespace_only(self):
        self.assertEqual(items.clean("  In-text   #3fa91c07 . "), "In-text #3fa91c07")
        self.assertEqual(items.clean("In-text #3FA91C07"), "In-text #3FA91C07")


class TestThePassageProduction(unittest.TestCase):

    def test_the_canonical_form_parses(self):
        item = items.parse("In-text #3fa91c07")
        self.assertEqual(item.id, "In-text #3fa91c07")
        self.assertEqual(item.full, "In-text #3fa91c07")
        self.assertEqual(item.kind, items.IN_TEXT)
        self.assertTrue(item.in_text and item.is_passage and not item.numbered)
        self.assertEqual(item.passage_hex, "3fa91c07")
        self.assertIsNone(item.page)
        self.assertEqual(items.parse("In-text #0123456789abcdef").passage_hex, "0123456789abcdef")

    def test_it_is_whitespace_tolerant(self):
        for raw in ("In-text#3fa91c07", "In-text   #3fa91c07", "In-text # 3fa91c07",
                    "in text #3fa91c07", "In-text\t#\t3fa91c07", " In-text #3fa91c07. ",
                    "In-text, #3fa91c07"):
            with self.subTest(raw=raw):
                self.assertEqual(items.parse(raw).id, "In-text #3fa91c07")
                self.assertEqual(items.join_key(raw), "In-text #3fa91c07")

    def test_the_kind_list_does_not_matter(self):
        self.assertEqual(items.parse("In-text #3fa91c07", kinds=("Widget",)).passage_hex,
                         "3fa91c07")

    def test_malformed_hashes_are_rejects_with_the_problem_named(self):
        cases = {"In-text #": "empty", "In-text #3FA91C07": "uppercase",
                 "In-text #3fa9": "length", "In-text #" + "a" * 7: "length",
                 "In-text #" + "a" * 17: "length",
                 "In-text #zz12zz12": "not_hex", "In-text #3fa91c0g": "not_hex",
                 "In-text #3fa91c07 (Sylow)": "trailing_text",
                 "In-text #3fa91c07 trailing": "trailing_text"}
        for raw, problem in cases.items():
            with self.subTest(raw=raw):
                item, reject = items.try_parse(raw)
                self.assertIsNone(item)
                self.assertEqual(reject.reason, items.REJECT_BAD_PASSAGE_ID)
                self.assertEqual(reject.context["problem"], problem)
                key, join_reject = items.try_join_key(raw)
                self.assertEqual(key, items.clean(raw))     # keeps itself, joins nothing
                self.assertEqual(join_reject.reason, items.REJECT_BAD_PASSAGE_ID)
                with self.assertRaises(ItemGrammarError):
                    items.join_key(raw)

    def test_the_bounds_are_eight_and_sixteen_digits(self):
        for n in (8, 9, 15, 16):
            with self.subTest(digits=n):
                self.assertEqual(items.parse("In-text #" + "a" * n).passage_hex, "a" * n)
        for n in (7, 17):
            with self.subTest(digits=n):
                self.assertEqual(items.try_parse("In-text #" + "a" * n)[1].context["problem"],
                                 "length")

    def test_a_page_or_a_named_item_wins_over_a_hash(self):
        """The passage production takes only what the page and reference grammar refuse.

        So a label that parsed before the production existed parses as it did
        (TestNoExistingIdMoved pins them), and ``passage_hex`` agrees with
        ``try_parse``: none of these is a passage.
        """
        for raw, item in (("In-text #3fa91c07, p. 12", "In-text p.12"),
                          ("In-text #3fa91c07 (p. 12)", "In-text p.12"),
                          ("In-text #2, Definition 1.4.35", "In-text Definition 1.4.35")):
            with self.subTest(raw=raw):
                parsed = items.parse(raw)
                self.assertEqual(parsed.full, item)
                self.assertFalse(parsed.is_passage)
                self.assertEqual(items.join_key(raw), item)
                self.assertFalse(items.is_passage_item(raw))
                with self.assertRaises(ItemGrammarError) as caught:
                    items.passage_hex(raw)
                self.assertEqual(caught.exception.reason, "not a passage item")
        # and a hash alone is never a page
        self.assertIsNone(items.try_parse("In-text #3fa91c07")[0].page)

    def test_a_reject_list_reports_it(self):
        found, rejects = items.parse_all(["In-text #3fa91c07", "In-text #3FA91C07"])
        self.assertEqual([i.id for i in found], ["In-text #3fa91c07"])
        self.assertEqual([r.reason for r in rejects], [items.REJECT_BAD_PASSAGE_ID])

    def test_slugs_sort_keys_and_qualified_items(self):
        item = items.parse("In-text #3fa91c07")
        self.assertEqual(items.slug(item), "in-text-3fa91c07")
        self.assertEqual(items.qualified_slug("enwiki-28767-1371234567", item),
                         "enwiki-28767-1371234567--in-text-3fa91c07")
        q = items.qualified_item("enwiki-28767-1371234567", item)
        self.assertEqual(q, "enwiki-28767-1371234567::In-text #3fa91c07")
        self.assertEqual(items.split_qualified(q), ("enwiki-28767-1371234567", "In-text #3fa91c07"))
        ordered = sorted(["In-text #ffff0000", "In-text p.3", "In-text #0000ffff", "Lemma 1.1"],
                         key=items.sort_key)
        self.assertEqual(ordered, ["Lemma 1.1", "In-text #0000ffff", "In-text #ffff0000",
                                   "In-text p.3"])

    def test_the_tally_collapses_it_onto_itself(self):
        from harvest.tally import collapse_to_book_item
        from ledger.compat import item_join_key
        self.assertEqual(collapse_to_book_item("In-text  #3fa91c07"), "In-text #3fa91c07")
        self.assertEqual(item_join_key("in text #3fa91c07"), "In-text #3fa91c07")


class TestTheConversions(unittest.TestCase):

    def test_item_and_hex_round_trip(self):
        for digits in ("3fa91c07", "0123456789abcdef", "0" * 12):
            with self.subTest(hex=digits):
                item = items.passage_item(digits)
                self.assertEqual(item, f"In-text #{digits}")
                self.assertEqual(items.passage_hex(item), digits)
                self.assertEqual(items.passage_hex(items.parse(item)), digits)
                self.assertTrue(items.is_passage_item(item))

    def test_passage_item_refuses_what_the_grammar_refuses(self):
        for bad in ("", "3FA91C07", "3fa9", "a" * 17, "xyz12345", "3fa9 1c07"):
            with self.subTest(hex=bad):
                with self.assertRaises(ItemGrammarError):
                    items.passage_item(bad)

    def test_passage_hex_refuses_another_item(self):
        for other in ("In-text p.71", "Exercise 1.3.19", "In-text #3FA91C07", "In-text", "",
                      "#3fa91c07", " #3fa91c07", "passage-3fa91c07", "In-text #3fa91c0g"):
            with self.subTest(item=other):
                with self.assertRaises(ItemGrammarError):
                    items.passage_hex(other)
                self.assertFalse(items.is_passage_item(other))
        with self.assertRaises(ItemGrammarError):
            items.passage_hex(items.parse("In-text p.71"))

    def test_to_and_from_the_vendored_id(self):
        self.assertEqual(items.to_vendored_passage_id("In-text #3fa91c07"), "passage-3fa91c07")
        self.assertEqual(items.from_vendored_passage_id("passage-3fa91c07"), "In-text #3fa91c07")
        vid = items.to_vendored_passage_id(items.passage_item("deadbeef"))
        self.assertRegex(vid, r"\A" + fmt_vocab.PASSAGE_QUOTE_ID_PATTERN + r"\Z")
        self.assertEqual(items.from_vendored_passage_id(vid), "In-text #deadbeef")

    def test_the_vendored_form_admits_eight_digits_and_no_title_hash(self):
        with self.assertRaises(ItemGrammarError):
            items.to_vendored_passage_id("In-text #0123456789abcdef")
        for bad in ("passage-p12-3fa91c07", "passage-3FA91C07", "passage-3fa9", "3fa91c07",
                    "In-text #3fa91c07", "passage-3fa91c07ff", "passage-3fa91c070",
                    "xpassage-3fa91c07"):
            with self.subTest(id=bad):
                with self.assertRaises(ItemGrammarError):
                    items.from_vendored_passage_id(bad)

    def test_the_vendored_id_of_a_head_is_the_vendored_hash_of_it(self):
        head = "The Sylow theorems are a collection of theorems named after the Norwegian"
        heads = passages.assign_passage_ids(head + " mathematician.", [0])
        self.assertEqual(items.to_vendored_passage_id(heads[0].item),
                         fmt_ids.passage_quote_id(head))


# ---------------------------------------------------------------------------
# the head hash
# ---------------------------------------------------------------------------

def words(n: int, stem: str = "w") -> str:
    return " ".join(f"{stem}{i}" for i in range(n))


class TestFormulaTokens(unittest.TestCase):

    def test_the_alttext_normalization(self):
        cases = {
            r"{\displaystyle G}": "G",
            r"  {\displaystyle  {x}  }  ": "x",
            r"{\displaystyle {\mathcal {P}}(X)}": r"{\mathcal {P}}(X)",
            r"{a}{b}": r"{a}{b}",
            r"{a\}": r"{a\}",
            "a  +\n b": "a + b",
            r"\displaystyle\sum_{i} x_i": r"\sum_{i} x_i",
            r"\displaystyleX": r"\displaystyleX",
            r"{{{\displaystyle n!}}}": "n!",
            "": "",
        }
        for alttext, normal in cases.items():
            with self.subTest(alttext=alttext):
                self.assertEqual(passages.normalize_alttext(alttext), normal)

    def test_the_token(self):
        self.assertEqual(passages.formula_token(r"{\displaystyle G}"), "⟦G⟧")
        self.assertEqual(passages.formula_token(r"{\displaystyle p^{k}}"), "⟦p^{k}⟧")

    def test_two_renderings_of_one_formula_key_one_passage(self):
        prose = "Let {} be a finite group of order {} where p is a prime and m is coprime to p."
        a = prose.format(passages.formula_token(r"{\displaystyle G}"),
                         passages.formula_token(r"{\displaystyle p^{k}m}"))
        b = prose.format(passages.formula_token("G"), passages.formula_token("  p^{k}m "))
        self.assertEqual(passages.assign_passage_ids(a, [0])[0].hex,
                         passages.assign_passage_ids(b, [0])[0].hex)
        c = prose.format(passages.formula_token("H"), passages.formula_token("p^{k}m"))
        self.assertNotEqual(passages.assign_passage_ids(a, [0])[0].hex,
                            passages.assign_passage_ids(c, [0])[0].hex)

    def test_a_formula_is_not_the_word_it_spells(self):
        a = "the group " + passages.formula_token("G") + " " + words(12)
        b = "the group G " + words(12)
        self.assertNotEqual(passages.assign_passage_ids(a, [0])[0].hex,
                            passages.assign_passage_ids(b, [0])[0].hex)


class TestHeadIds(unittest.TestCase):

    def test_twelve_words_hashed_with_the_vendored_rule(self):
        text = words(30)
        (head,) = passages.assign_passage_ids(text, [0])
        self.assertEqual(head.head_words, 12)
        self.assertEqual(head.head, words(12))
        self.assertEqual("passage-" + head.hex, fmt_ids.passage_quote_id(words(12)))
        self.assertEqual(head.item, f"In-text #{head.hex}")
        self.assertRegex(head.hex, r"\A[0-9a-f]{8}\Z")
        self.assertEqual(passages.passage_id(text, 0, [0]), (head.hex, 12))
        self.assertEqual(passages.hex_at(text, 0, 12), head.hex)

    def test_a_shared_prefix_extends_both_heads_word_by_word(self):
        shared = words(15, "s")
        text = f"{shared} alpha tail tail. {shared} beta tail tail."
        second = text.index(shared, 1)
        a, b = passages.assign_passage_ids(text, [0, second])
        self.assertEqual((a.head_words, b.head_words), (16, 16))
        self.assertTrue(a.head.endswith("alpha") and b.head.endswith("beta"))
        self.assertNotEqual(a.hex, b.hex)
        self.assertEqual(passages.hex_at(text, second, 16), b.hex)
        # an unrelated third passage keeps twelve
        third = text.index("tail tail.")
        heads = passages.assign_passage_ids(text, [0, second, third])
        self.assertEqual(heads[2].head_words, min(12, len(text[third:].split())))

    def test_each_passage_takes_its_own_shortest_unique_head(self):
        """Not the longest of its group: A leaves B and C at its thirteenth word.

        A shares twelve words with B and C, and B and C share fourteen.  A is
        unique at thirteen words, B and C only at fifteen, and A's id is the
        hash of its own thirteen -- it does not wait for B and C.
        """
        shared = words(12, "s")
        a = f"{shared} a12 a13 a14 a15 a16"
        b = f"{shared} t12 t13 b14 b15 b16"
        c = f"{shared} t12 t13 c14 c15 c16"
        text = f"{a}. {b}. {c}."
        starts = [0, text.index(b), text.index(c)]
        heads = passages.assign_passage_ids(text, starts)
        self.assertEqual([h.head_words for h in heads], [13, 15, 15])
        self.assertEqual(heads[0].hex, passages.head_hex(a.split()[:13]))
        self.assertEqual(heads[0].head, " ".join(a.split()[:13]))
        self.assertEqual(heads[1].hex, passages.head_hex(b.split()[:15]))
        self.assertEqual(heads[2].hex, passages.head_hex(c.split()[:15]))
        for head in heads:
            self.assertEqual(passages.hex_at(text, head.start, head.head_words), head.hex)

    def test_the_result_does_not_depend_on_the_order_of_the_starts(self):
        shared = words(13, "s")
        text = f"{shared} one x x. {shared} two y y. other words here entirely."
        starts = [0, text.index(shared, 1), text.index("other")]
        forward = {h.start: h for h in passages.assign_passage_ids(text, starts)}
        backward = {h.start: h for h in passages.assign_passage_ids(text, starts[::-1])}
        self.assertEqual(forward, backward)

    def test_a_short_tail_uses_the_words_it_has(self):
        text = words(20) + " last three words"
        start = text.index("last")
        heads = passages.assign_passage_ids(text, [0, start])
        self.assertEqual(heads[1].head_words, 3)
        self.assertEqual(heads[1].head, "last three words")
        self.assertEqual(passages.hex_at(text, start, 3), heads[1].hex)
        self.assertEqual(passages.hex_at(text, start, 12), heads[1].hex)

    def test_forty_shared_words_are_refused_naming_both(self):
        shared = words(45, "s")
        text = f"{shared} A. {shared} B."
        second = text.index(shared, 1)
        with self.assertRaises(PassageIdError) as caught:
            passages.assign_passage_ids(text, [0, second])
        self.assertEqual(set(caught.exception.starts), {0, second})
        self.assertIn(str(second), caught.exception.message)
        self.assertIn("40", caught.exception.message)
        # thirty-nine shared words are not a collision: the fortieth separates them
        shared = words(39, "s")
        text = f"{shared} A. {shared} B."
        a, b = passages.assign_passage_ids(text, [0, text.index(shared, 1)])
        self.assertEqual((a.head_words, b.head_words), (40, 40))

    def test_a_start_given_twice_is_refused(self):
        with self.assertRaises(PassageIdError) as caught:
            passages.assign_passage_ids(words(20), [0, 0])
        self.assertEqual(caught.exception.starts, (0, 0))
        self.assertIn("given twice", caught.exception.message)
        # ...and through the one-passage view, which adds `start` only when absent
        with self.assertRaises(PassageIdError) as caught:
            passages.passage_id(words(20), 0, [0, 0])
        self.assertIn("given twice", caught.exception.message)
        self.assertEqual(passages.passage_id(words(20), 0, [0]),
                         passages.passage_id(words(20), 0, []))

    def test_a_start_that_is_not_a_word_start_is_refused(self):
        text = words(20)
        for bad in (-1, len(text), text.index(" "), "0", None, True):
            with self.subTest(start=bad):
                with self.assertRaises(PassageIdError):
                    passages.assign_passage_ids(text, [bad])
        for bad in (True, "0", text.index(" ")):
            with self.subTest(start=bad, view="passage_id"):
                with self.assertRaises(PassageIdError):
                    passages.passage_id(text, bad, [])
        # True == 1, so a bool start must be refused before it is looked up among the starts
        spaced = " " + text
        self.assertEqual(passages.passage_id(spaced, 1, [1]), passages.passage_id(text, 0, [0]))
        with self.assertRaises(PassageIdError):
            passages.passage_id(spaced, True, [1])

    def test_a_start_inside_a_word_is_refused(self):
        """An off-by-one offset would hash a fragment, and hex_at would agree with it."""
        text = "hello world " + words(20)
        inside = [1, 4, text.index("w0") + 1, text.index("w12") + 2]
        for bad in inside:
            with self.subTest(start=bad):
                with self.assertRaises(PassageIdError) as caught:
                    passages.assign_passage_ids(text, [bad])
                self.assertEqual(caught.exception.starts, (bad,))
                self.assertIn("inside a word", caught.exception.message)
                with self.assertRaises(PassageIdError):
                    passages.hex_at(text, bad, 12)
                with self.assertRaises(PassageIdError):
                    passages.head_at(text, bad)
        # a word starts at 0 or after any whitespace, and punctuation is part of it
        odd = "(Sylow) first\nsecond\tthird\u00a0fourth " + words(12)
        for good in (0, odd.index("second"), odd.index("third"), odd.index("fourth")):
            with self.subTest(start=good):
                (head,) = passages.assign_passage_ids(odd, [good])
                self.assertEqual(head.start, good)
        self.assertTrue(passages.assign_passage_ids(odd, [0])[0].head.startswith("(Sylow) "))

    def test_a_hash_collision_between_different_heads_is_refused(self):
        text = words(20) + " " + words(20, "x")
        second = text.index("x0")
        with mock.patch.object(passages, "head_hex", return_value="deadbeef"):
            with self.assertRaises(PassageIdError) as caught:
                passages.assign_passage_ids(text, [0, second])
        self.assertEqual(set(caught.exception.starts), {0, second})
        self.assertIn("deadbeef", caught.exception.message)

    def test_passage_id_refuses_with_the_source(self):
        shared = words(45, "s")
        text = f"{shared} A. {shared} B. {words(12, 'z')}"
        with self.assertRaises(PassageIdError):
            passages.passage_id(text, text.index("z0"), [0, text.index(shared, 1)])

    def test_hex_at_checks_its_count(self):
        for bad in (0, -1, 41, "12", True):
            with self.subTest(head_words=bad):
                with self.assertRaises(PassageIdError):
                    passages.hex_at(words(20), 0, bad)


class TestTheHeadIsBlindToWhitespace(unittest.TestCase):

    def test_spaces_newlines_tabs_and_nbsp_do_not_change_an_id(self):
        plain = "Every finite group of order n has a subgroup of order p for each prime p dividing n."
        noisy = ("Every  finite\ngroup\tof order n has a subgroup of   order p for each "
                 "prime p\r\ndividing n.")
        self.assertEqual(passages.assign_passage_ids(plain, [0])[0],
                         passages.assign_passage_ids(noisy, [0])[0])

    def test_the_offsets_move_and_the_ids_do_not(self):
        lead = words(14, "a") + ". " + words(14, "b") + "."
        spaced = lead.replace(" ", "   ")
        a = passages.assign_passage_ids(lead, [0, lead.index("b0")])
        b = passages.assign_passage_ids(spaced, [0, spaced.index("b0")])
        self.assertEqual([h.hex for h in a], [h.hex for h in b])
        self.assertEqual([h.head_words for h in a], [h.head_words for h in b])

    def test_nfkc_is_the_vendored_one(self):
        ligature = "The ﬁeld of fractions " + words(12)
        folded = "The field of fractions " + words(12)
        self.assertEqual(passages.assign_passage_ids(ligature, [0])[0].hex,
                         passages.assign_passage_ids(folded, [0])[0].hex)

    def test_a_long_text_is_read_past_the_first_window(self):
        long_words = " ".join("x" * 60 + str(i) for i in range(45))
        head = passages.head_at(long_words, 0)
        self.assertEqual(len(head), passages.MAX_HEAD_WORDS)
        self.assertEqual(head[-1], "x" * 60 + "39")

    def test_the_window_never_keeps_a_word_it_cut_in_half(self):
        """Exactly forty pieces in the first window, the fortieth cut short.

        Twenty-five-character words: thirty-nine of them and their spaces fill
        1014 characters, so the window of 1024 holds ten characters of the
        fortieth.  The head must read on and keep that word whole.
        """
        full = ["x" * 23 + f"{i:02d}" for i in range(45)]
        text = " ".join(full)
        self.assertEqual(len(" ".join(full[:39])) + 1, 1014)
        head = passages.head_at(text, 0)
        self.assertEqual(head, full[:40])
        self.assertEqual(passages.hex_at(text, 0, 40), passages.head_hex(full[:40]))


# ---------------------------------------------------------------------------
# licence classes and web-source families
# ---------------------------------------------------------------------------

def _registry_file(root: Path, body: str) -> Path:
    path = root / "registry.yaml"
    path.write_text("version: 1\n" + body, encoding="utf-8")
    return path


GOOD_FAMILY = (
    "families:\n"
    "  testwiki:\n"
    "    title: Test Wiki\n"
    "    site: https://wiki.example.org\n"
    "    licence_class: open_attribution\n"
    "    licence: {spdx: CC-BY-4.0, note: CC BY 4.0 text, redistribute: false}\n"
    "    attribution: '\"{title}\", Test Wiki, {url}, {licence}'\n"
    "    revision_url: \"https://wiki.example.org/?oldid={revision}\"\n"
)


class TestLicenceClassOnEntries(unittest.TestCase):

    def test_every_shipped_entry_is_valid_and_the_books_are_closed(self):
        self.assertEqual(registry.metadata_problems(registry.skill_registry_path()), [])
        shipped = registry.Registry.load(None)
        self.assertEqual(shipped.validate(), [])
        for sid, entry in shipped.entries.items():
            with self.subTest(source=sid):
                self.assertEqual(entry.licence_class, "closed")
                self.assertEqual(shipped.licence_class(sid), "closed")

    def test_the_default_is_closed_and_is_not_written(self):
        entry = registry.RegistryEntry.from_dict("a-book", {"title": "A", "edition": "1"})
        self.assertEqual(entry.licence_class, "closed")
        self.assertNotIn("licence_class", entry.to_dict())
        open_entry = registry.RegistryEntry.from_dict(
            "a-book", {"title": "A", "edition": "1", "licence_class": "public_domain"})
        self.assertEqual(open_entry.to_dict()["licence_class"], "public_domain")

    def test_an_unknown_class_is_a_problem(self):
        reg = registry.Registry({"a": registry.RegistryEntry(id="a", title="A", edition="1",
                                                             licence_class="copyleft")})
        self.assertTrue(any("licence_class" in p for p in reg.validate()), reg.validate())

    def test_an_overlay_row_does_not_close_a_source_the_skill_calls_open(self):
        root = _scratch(self)
        skill = _registry_file(root, "sources:\n  pd-book:\n    title: A\n    edition: '1'\n"
                                     "    licence_class: public_domain\n    confirmed: true\n")
        ws = root / "ws"
        ws.mkdir()
        with mock.patch.object(registry, "skill_registry_path", return_value=skill):
            reg = registry.Registry.load(ws)
            reg.append_overlay(ws, registry.RegistryEntry(id="pd-book", title="A", edition="1"))
            self.assertNotIn("licence_class", (ws / "registry.yaml").read_text())
            self.assertEqual(registry.Registry.load(ws).licence_class("pd-book"), "public_domain")

    def test_the_file_check_accepts_the_key(self):
        root = _scratch(self)
        path = _registry_file(root, "sources:\n  a-book:\n    title: A\n    edition: '1'\n"
                                    "    licence_class: open_share_alike\n")
        self.assertEqual(registry.metadata_problems(path), [])


class TestWebSourceFamilies(unittest.TestCase):

    def test_enwiki_ships_open_share_alike(self):
        shipped = registry.Registry.load(None)
        family = shipped.family("enwiki")
        self.assertIsNotNone(family)
        self.assertEqual(family.licence_class, "open_share_alike")
        self.assertEqual(family.spdx, "CC-BY-SA-4.0")
        self.assertFalse(family.licence.get("redistribute"))
        self.assertEqual(family.problems(), [])
        self.assertNotIn("enwiki", shipped.entries)          # a family is not an edition
        sid = family.source_id(28767, 1371234567)
        self.assertEqual(sid, "enwiki-28767-1371234567")
        self.assertIs(shipped.family_of(sid), family)
        self.assertEqual(shipped.licence_class(sid), "open_share_alike")
        self.assertEqual(shipped.licence_class("frwiki-1-2"), "closed")
        line = family.attribution_for(title="Sylow theorems", pageid=28767, revision=1371234567)
        self.assertIn("Sylow theorems", line)
        self.assertIn("oldid=1371234567", line)
        self.assertIn("CC-BY-SA-4.0", line)

    def test_the_fallback_reader_reads_the_family_block(self):
        """Stdlib only (CLAUDE.md section 3): the minimal reader alone gets it right."""
        text = registry.skill_registry_path().read_text(encoding="utf-8")
        enwiki = yamlio.load_yaml_minimal(text)["families"]["enwiki"]
        self.assertIs(enwiki["confirmed"], True)
        self.assertIs(enwiki["licence"]["redistribute"], False)
        self.assertEqual(enwiki["licence_class"], "open_share_alike")
        self.assertEqual(enwiki["licence"]["spdx"], "CC-BY-SA-4.0")
        self.assertEqual(enwiki["attribution"],
                         '"{title}", Wikipedia, revision {revision}, {url}, licensed {licence}')
        self.assertEqual(enwiki["revision_url"],
                         "https://en.wikipedia.org/w/index.php?curid={pageid}&oldid={revision}")
        # a folded scalar: one line, single spaces, no trailing newline
        self.assertTrue(enwiki["notes"].startswith("The first locate corpus is WikiProject"))
        self.assertNotIn("\n", enwiki["notes"])
        self.assertTrue(enwiki["notes"].endswith("revision."))
        self.assertTrue(enwiki["licence"]["note"].startswith("Article text is CC BY-SA 4.0."))

    def test_the_family_block_reads_the_same_under_both_yaml_readers(self):
        if not yamlio.HAVE_PYYAML:
            self.skipTest("PyYAML is not installed; the fallback reader is checked alone above")
        import yaml
        text = registry.skill_registry_path().read_text(encoding="utf-8")
        self.assertEqual(yamlio.load_yaml_minimal(text)["families"],
                         yaml.safe_load(text)["families"])

    def test_web_source_ids(self):
        self.assertEqual(registry.parse_web_source_id("enwiki-28767-1371234567"),
                         ("enwiki", 28767, 1371234567))
        for other in ("tao-measure-gsm126-published", "enwiki-0-1", "en-wiki-1-2", "enwiki-1",
                      "", "Enwiki-1-2"):
            with self.subTest(id=other):
                self.assertIsNone(registry.parse_web_source_id(other))
        for bad in (("en-wiki", 1, 2), ("enwiki", 0, 2), ("enwiki", 1, -2), ("enwiki", True, 2)):
            with self.subTest(parts=bad):
                with self.assertRaises(CfError):
                    registry.web_source_id(*bad)

    def test_a_good_family_passes_both_checks(self):
        root = _scratch(self)
        path = _registry_file(root, GOOD_FAMILY + "sources: {}\n")
        self.assertEqual(registry.metadata_problems(path), [])
        with mock.patch.object(registry, "skill_registry_path", return_value=path):
            reg = registry.Registry.load(None)
        self.assertEqual(reg.validate(), [])
        self.assertEqual(reg.family("testwiki").revision_link(5, 9),
                         "https://wiki.example.org/?oldid=9")

    def test_a_family_holds_no_per_revision_row(self):
        root = _scratch(self)
        for key in ("sha256: " + "a" * 64, "revision: 12", "pageid: 3", "bytes: 10"):
            with self.subTest(key=key):
                path = _registry_file(root, GOOD_FAMILY + f"    {key}\n")
                problems = registry.metadata_problems(path)
                self.assertTrue(any("unknown key" in p for p in problems), problems)

    def test_the_structural_problems(self):
        base = registry.SourceFamily.from_dict("testwiki", yamlio.load_yaml(
            "version: 1\n" + GOOD_FAMILY)["families"]["testwiki"])
        self.assertEqual(base.problems(), [])
        cases = {
            "the id": dict(id="test-wiki"),
            "licence_class": dict(licence_class="copyleft"),
            "redistribute": dict(licence={"spdx": "CC-BY-4.0", "redistribute": True}),
            "licence.spdx": dict(licence={"redistribute": False}),
            "names {editor}": dict(attribution="{title} {url} {licence} {editor}"),
            "must name {licence}": dict(attribution="{title} {url}"),
            "must name {revision}": dict(revision_url="https://wiki.example.org/{pageid}"),
            "bare field name": dict(attribution="{title!r} {url} {licence}"),
            "revision_url must be an https": dict(revision_url="http://x/{revision}"),
            "site must be an https": dict(site="http://wiki.example.org"),
            "title is required": dict(title=""),
            "attribution is required": dict(attribution=""),
            "revision_url is required": dict(revision_url=""),
        }
        from dataclasses import replace
        for needle, change in cases.items():
            with self.subTest(case=needle):
                problems = replace(base, **change).problems()
                self.assertTrue(any(needle in p for p in problems), problems)

    def test_validate_reports_a_family_problem(self):
        """`cf sources validate-registry` runs validate(); a bad family must reach it."""
        from dataclasses import replace
        good = registry.Registry.load(None).family("enwiki")
        for needle, change in (("licence_class", dict(licence_class="copyleft")),
                               ("redistribute", dict(licence={"spdx": "CC-BY-SA-4.0",
                                                              "redistribute": True})),
                               ("the id", dict(id="en-wiki"))):
            with self.subTest(case=needle):
                bad = replace(good, **change)
                reg = registry.Registry({}, families={bad.id: bad})
                problems = reg.validate()
                self.assertTrue(any(needle in p and bad.id in p for p in problems), problems)

    def test_a_revision_link_refuses_a_bad_page_or_revision(self):
        family = registry.Registry.load(None).family("enwiki")
        for pageid, revision in ((0, 1), (1, 0), (-1, 5), (True, 5), (1, True)):
            with self.subTest(pageid=pageid, revision=revision):
                with self.assertRaises(CfError):
                    family.revision_link(pageid, revision)
                with self.assertRaises(CfError):
                    family.attribution_for(title="T", pageid=pageid, revision=revision)

    def test_the_file_check_refuses_a_misshapen_family_block(self):
        root = _scratch(self)
        cases = {
            "families: [enwiki]\n": "`families:` must be a map",
            "families:\n  testwiki: text\n": "family testwiki: the entry must be a map",
            GOOD_FAMILY.replace("redistribute: false}", "redistribute: false, fetch: open}"):
                "unknown licence key `fetch`",
        }
        for body, needle in cases.items():
            with self.subTest(case=needle):
                path = _registry_file(root, body)
                problems = registry.metadata_problems(path)
                self.assertTrue(any(needle in p for p in problems), problems)

    def test_a_misshapen_overlay_family_block_is_a_refusal_not_a_crash(self):
        for body in ("families: [enwiki]\n", "families:\n  testwiki: text\n",
                     "families:\n  testwiki: [a, b]\n"):
            with self.subTest(body=body):
                ws = _scratch(self)
                _registry_file(ws, body)
                with self.assertRaises(CfError) as caught:
                    registry.Registry.load(ws)
                self.assertIn("must be a map", caught.exception.message)

    def test_an_overlay_family_is_merged_like_a_source(self):
        """An overlay may add a family, and its licence is its own."""
        ws = _scratch(self)
        _registry_file(ws, GOOD_FAMILY)
        reg = registry.Registry.load(ws)
        family = reg.family("testwiki")
        self.assertIsNotNone(family)
        self.assertEqual(family.origin, "overlay")
        self.assertFalse(family.confirmed)
        self.assertEqual(reg.licence_class("testwiki-5-9"), "open_attribution")
        self.assertEqual(reg.family_of("testwiki-5-9"), family)
        self.assertEqual(reg.shadowed_families, {})
        self.assertEqual(reg.validate(), [])
        # the shipped family is still there, untouched
        self.assertEqual(reg.family("enwiki").origin, "skill")
        self.assertEqual(reg.licence_class("enwiki-1-2"), "open_share_alike")

    def test_an_overlay_cannot_reopen_or_demote_a_confirmed_family(self):
        """`confirmed` and the licence of a curated family stand, and the shadow is reported.

        Without this, a workspace registry.yaml saying `public_domain` made every
        Wikipedia record quotable with no CC BY-SA attribution, and nothing --
        not validate(), not `cf status` -- said so.
        """
        ws = _scratch(self)
        _registry_file(ws, "families:\n  enwiki:\n    licence_class: public_domain\n"
                           "    licence: {spdx: CC0-1.0, redistribute: false}\n"
                           "    confirmed: false\n    container_title: Wikipedia (overlay)\n")
        reg = registry.Registry.load(ws)
        family = reg.family("enwiki")
        self.assertEqual(family.origin, "overlay")
        self.assertTrue(family.confirmed)
        self.assertEqual(family.licence_class, "open_share_alike")
        self.assertEqual(family.spdx, "CC-BY-SA-4.0")
        self.assertEqual(reg.licence_class("enwiki-28767-1371234567"), "open_share_alike")
        self.assertIn("CC-BY-SA-4.0", family.attribution_for(title="T", pageid=1, revision=2))
        self.assertEqual(family.container_title, "Wikipedia (overlay)")   # the rest merges
        self.assertEqual(reg.shadowed_families, {"enwiki": ("licence_class", "licence")})
        problems = reg.validate()
        shadow = [p for p in problems if p.startswith("family enwiki:")]
        self.assertEqual(len(shadow), 1, problems)
        self.assertIn("shadows a CONFIRMED family", shadow[0])
        self.assertIn("licence_class, licence were not applied", shadow[0])
        # an overlay row that changes nothing licence-shaped is still reported
        _registry_file(ws, "families:\n  enwiki:\n    notes: seen here first\n")
        again = registry.Registry.load(ws)
        self.assertEqual(again.shadowed_families, {"enwiki": ()})
        self.assertTrue(any("shadows a CONFIRMED family" in p and "not applied" not in p
                            for p in again.validate()))
        # and a more restrictive class is held as well: the curated licence is one fact
        _registry_file(ws, "families:\n  enwiki:\n    licence_class: closed\n")
        held = registry.Registry.load(ws)
        self.assertEqual(held.licence_class("enwiki-1-2"), "open_share_alike")
        self.assertEqual(held.shadowed_families, {"enwiki": ("licence_class",)})

    def test_cf_status_reports_a_shadowed_family(self):
        ws = _scratch(self)
        _registry_file(ws, "families:\n  enwiki:\n    licence_class: public_domain\n")
        from cfcmd import status
        with mock.patch.object(status.log, "warn_block") as warn_block:
            status._print_registry_problems(ws)
        lines = [line for call in warn_block.call_args_list for line in call.args[1]]
        self.assertTrue(any("family enwiki" in line and "licence_class" in line
                            for line in lines), lines)

    def test_a_web_source_is_never_a_registry_entry(self):
        reg = registry.Registry.load(None)
        reg.entries["enwiki-28767-1371234567"] = registry.RegistryEntry(
            id="enwiki-28767-1371234567", title="Sylow theorems", edition="1371234567")
        self.assertTrue(any("sourceset row" in p for p in reg.validate()))
        # a book id that merely has the shape names no known family
        reg = registry.Registry({"smith-1999-2": registry.RegistryEntry(
            id="smith-1999-2", title="A", edition="1999")})
        self.assertEqual(reg.validate(), [])

    def test_the_family_free_text_is_capped(self):
        root = _scratch(self)
        prose = " ".join(["word"] * 45)
        path = _registry_file(root, GOOD_FAMILY.replace(
            "attribution: '\"{title}\", Test Wiki, {url}, {licence}'",
            f"attribution: '{prose} {{title}} {{url}} {{licence}}'"))
        self.assertTrue(any("attribution" in p and "cap 40" in p
                            for p in registry.metadata_problems(path)))
        path = _registry_file(root, GOOD_FAMILY.replace(
            "title: Test Wiki", "title: Test Wiki. It is the wiki we use for these tests"))
        self.assertTrue(any("second sentence" in p for p in registry.metadata_problems(path)))


class TestTheDocumentsSayWhatIsBuilt(unittest.TestCase):

    def test_the_policy_and_the_rule_name_the_classes(self):
        policy = (SKILL / "POLICY.md").read_text(encoding="utf-8")
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for value in vocab.LICENCE_CLASSES:
            self.assertIn(f"`{value}`", policy)
            self.assertIn(f"`{value}`", skill)
        self.assertIn("### 4.1 Licence classes", policy)

    def test_the_network_exception_is_named_with_its_one_module(self):
        """Built by the sources package: the exception names the module and the opt-in."""
        claude = " ".join((SKILL / "CLAUDE.md").read_text(encoding="utf-8").split())
        self.assertRegex(claude, r"the Wikipedia fetcher .{0,200}`scripts/sources/wikinet\.py`")
        self.assertRegex(claude, r"wikinet\.py`.{0,400}--execute")
        self.assertTrue((SCRIPTS / "sources" / "wikinet.py").is_file())

    def test_the_spec_states_the_new_production(self):
        spec = (SKILL / "docs" / "design" / "stage_map.md").read_text(encoding="utf-8")
        self.assertIn("`In-text #<hex>`", spec)
        self.assertIn("bad_passage_id", spec)
        self.assertTrue(re.search(r"In-text #<hex>", VOCABULARY_MD))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
