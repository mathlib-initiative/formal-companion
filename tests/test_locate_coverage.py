"""The maintainer's decision D1: coverage claims and data that states a theorem.

New code, for docs/proposals/wikipedia-locate.md section 2.6 as the decision of
2026-10-18 amends it (references/vocabulary.md section 1.11):

    "D1: yes. Formalized when the hasPart locations together cover every clause
    (challenger confirms coverage), and when an exact/closeMatch is to an
    equivalence or data declaration that states the theorem (e.g. CRT's
    RingEquiv). Update vocab and WikiLean's parity table."

The parity table ``tests/data/locate/status-parity.json`` is shared with the
consumer's own implementation of the derived status: every expectation in it is
written by hand from the table and the decision, never computed, and this
module runs ``cflib.vocab.derive_status`` over every case.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vocab  # noqa: E402
from cflib.errors import CfError  # noqa: E402

PARITY = HERE / "data" / "locate" / "status-parity.json"
VOCABULARY_MD = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")
DECISION = ("D1: yes. Formalized when the hasPart locations together cover every clause "
            "(challenger confirms coverage), and when an exact/closeMatch is to an equivalence "
            "or data declaration that states the theorem (e.g. CRT's RingEquiv). Update vocab "
            "and WikiLean's parity table.")


def loc(key, relation, proof_status, statement=False):
    return {"location_key": key, "relation": relation, "proof_status": proof_status,
            "statement": statement}


def claim(parts, challenge="survived"):
    return {"complete": True, "parts": list(parts), "challenge": challenge}


class TestTheParityTable(unittest.TestCase):
    """Every case of the shared table, through the one implementation here."""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(PARITY.read_text(encoding="utf-8"))

    def test_the_table_is_the_decisions(self):
        self.assertEqual(self.doc["schema"], "cf-locate-status-parity/1")
        self.assertEqual(self.doc["decision"], {"id": "D1", "date": "2026-10-18",
                                                "text": DECISION})
        ids = [case["id"] for case in self.doc["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 40)

    def test_every_case(self):
        for case in self.doc["cases"]:
            with self.subTest(case=case["id"], rule=case["rule"]):
                call = (case["passage_kind"], case["locations"], case["absent"],
                        case["coverage"])
                if case.get("refused"):
                    with self.assertRaises(CfError):
                        vocab.derive_status(*call)
                else:
                    self.assertEqual(vocab.derive_status(*call), case["expected"])

    def test_the_table_spans_every_row(self):
        """Passage kind x relation x proof status x flag x claim x challenge x absence."""
        cases = self.doc["cases"]
        self.assertEqual({c["passage_kind"] for c in cases} - {"lemma"}, vocab.PASSAGE_KINDS)
        locations = [l for c in cases for l in c["locations"]]
        self.assertLessEqual(vocab.LOCATE_RELATIONS, {l["relation"] for l in locations})
        self.assertEqual({l["proof_status"] for l in locations}, vocab.PROOF_STATUS_SET)
        self.assertEqual({l["statement"] for l in locations}, {True, False})
        challenges = {c["coverage"]["challenge"] for c in cases if c["coverage"]}
        self.assertLessEqual(vocab.CHALLENGE_VERDICTS | {None}, challenges)
        self.assertEqual({c["absent"] for c in cases}, {True, False})
        self.assertEqual({c["coverage"] is None for c in cases}, {True, False})
        expected = {c.get("expected") for c in cases if not c.get("refused")}
        self.assertEqual(expected, set(vocab.DERIVED_STATUSES) | {None})
        self.assertEqual({c["table"] for c in cases}, {"old", "new"})

    def test_an_open_reading_is_documented(self):
        for question in sorted({c["question"] for c in self.doc["cases"] if c.get("question")}):
            with self.subTest(question=question):
                self.assertIn(f"**{question}**", VOCABULARY_MD)


class TestTheCoverageArgument(unittest.TestCase):

    def test_the_three_argument_call_is_unchanged(self):
        parts = [loc("a", "hasPart", "proved"), loc("b", "hasPart", "proved")]
        self.assertEqual(vocab.derive_status("theorem", parts, False), "partial")
        self.assertEqual(vocab.derive_status("theorem", parts, absent=False), "partial")
        self.assertEqual(vocab.derive_status("theorem", parts, False, None), "partial")

    def test_a_survived_claim_upgrades_and_nothing_else_does(self):
        parts = [loc("a", "hasPart", "proved"), loc("b", "hasPart", "proved")]
        self.assertEqual(vocab.derive_status("theorem", parts, False, claim("ab")),
                         "formalized")
        for value in sorted(vocab.CHALLENGE_VERDICTS - {vocab.CHALLENGE_SURVIVED}) + [None]:
            with self.subTest(challenge=value):
                self.assertEqual(vocab.derive_status("theorem", parts, False,
                                                     claim("ab", value)), "partial")

    def test_objects_work_as_well_as_mappings(self):
        class Part:
            relation, proof_status, statement = "hasPart", "proved", False

            def __init__(self, key):
                self.location_key = key

        class Claim:
            complete, parts, challenge = True, ["a", "b"], "survived"
        self.assertEqual(vocab.derive_status("conjecture", [Part("a"), Part("b")], False,
                                             Claim()), "formalized")

    def test_the_locations_may_be_any_iterable(self):
        parts = (loc(k, "hasPart", "definition") for k in "ab")
        self.assertEqual(vocab.derive_status("definition", parts, False, claim("ab")),
                         "formalized")

    def test_a_claim_with_a_bad_shape_is_refused(self):
        parts = [loc("a", "hasPart", "proved"), loc("b", "hasPart", "proved")]
        for bad in ({"complete": True, "parts": "ab", "challenge": "survived"},
                    {"complete": True, "parts": ["a", 2], "challenge": "survived"},
                    {"complete": "true", "parts": ["a", "b"], "challenge": "survived"},
                    {"parts": ["a", "b"], "challenge": "survived"}):
            with self.subTest(claim=bad):
                with self.assertRaises(CfError):
                    vocab.derive_status("theorem", parts, False, bad)

    def test_the_constants(self):
        self.assertEqual(vocab.COVERAGE_TARGET, "coverage")
        self.assertIn(vocab.COVERAGE_TARGET, vocab.CHALLENGE_TARGETS)
        self.assertEqual(vocab.MIN_COVERAGE_PARTS, 2)
        self.assertEqual(vocab.COVERAGE_ACTIONS, {"confirm", "refute"})
        self.assertLessEqual(vocab.COVERAGE_ACTIONS, vocab.CHALLENGE_ACTIONS)
        for name in ("COVERAGE_TARGET", "MIN_COVERAGE_PARTS", "COVERAGE_ACTIONS"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", VOCABULARY_MD)

    def test_the_decision_is_quoted_and_dated(self):
        flat = " ".join(VOCABULARY_MD.replace("\n> ", "\n").split())
        self.assertIn(DECISION, flat)
        self.assertIn("2026-10-18", VOCABULARY_MD)


if __name__ == "__main__":
    unittest.main()
