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

for _path in (SCRIPTS, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import _locate_fixtures as F  # noqa: E402
from cflib import vocab  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from locate import challenge_plan, outcomes, records  # noqa: E402

import test_verify_locate as G  # noqa: E402
from test_locate_planner import Campaign, ws  # noqa: E402

EXT = records.EXTENSION

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
        challenges = {c["coverage"].get("challenge") for c in cases if c["coverage"]}
        self.assertLessEqual(vocab.CHALLENGE_VERDICTS | {None}, challenges)
        self.assertEqual({c["absent"] for c in cases}, {True, False})
        self.assertEqual({c["coverage"] is None for c in cases}, {True, False})
        expected = {c.get("expected") for c in cases if not c.get("refused")}
        self.assertEqual(expected, set(vocab.DERIVED_STATUSES) | {None})
        self.assertEqual({c["table"] for c in cases}, {"old", "new"})

    def test_what_no_record_carries_is_refused(self):
        """Parity round 2: an owed claim, a claim beside its note, one key twice."""
        cases = self.doc["cases"]
        owed = [c for c in cases if c["coverage"] and c["coverage"].get("challenge") is None]
        noted = [c for c in cases
                 if c["coverage"] and set(c["coverage"]) - vocab.COVERAGE_RECORD_KEYS]
        twice = [c for c in cases if len({l["location_key"] for l in c["locations"]})
                 != len(c["locations"])]
        for found in (owed, noted, twice):
            self.assertTrue(found)
            for case in found:
                with self.subTest(case=case["id"]):
                    self.assertTrue(case.get("refused"))
        self.assertIn("C14", {c["id"] for c in owed})

    def test_an_open_reading_is_documented(self):
        for question in sorted({c["question"] for c in self.doc["cases"] if c.get("question")}):
            with self.subTest(question=question):
                self.assertIn(f"**{question}**", VOCABULARY_MD)

    def test_round_three_resolved_the_questions(self):
        """The reading the table carries is the one vocabulary.md section 1.11 records."""
        reading = self.doc["reading"]
        self.assertEqual((reading["round"], reading["resolves"]), (3, ["Q1", "Q2", "Q3"]))
        self.assertEqual({c["question"] for c in self.doc["cases"] if c.get("question")},
                         {"Q1", "Q2", "Q3"})
        self.assertIn("**Parity, round 3**", VOCABULARY_MD)
        # the cases round 2 expected otherwise, each named in its rule
        changed = {c["id"]: c["expected"] for c in self.doc["cases"]
                   if "round 2 expected" in c["rule"] and not c.get("refused")}
        self.assertEqual(changed, {"C08": "formalized", "C09": "statement_only",
                                   "D04": "statement_only", "D07": "statement_only",
                                   "D08": "statement_only", "E03": "formalized",
                                   "E04": "formalized"})

    def test_the_parts_a_confirmed_claim_confirms(self):
        """Round 3: each case's locations through the outcomes' coverage verdict."""
        cases = [c for c in self.doc["cases"] if "expected_challenges" in c]
        self.assertTrue(cases)
        self.assertIn("sampled_out", {l.get("challenge") for c in cases for l in c["locations"]})
        for case in cases:
            with self.subTest(case=case["id"], rule=case["rule"]):
                self.assertEqual(exported_challenges(case), case["expected_challenges"])


def exported_challenges(case: dict) -> dict:
    """``{location_key: challenge}`` once a parity case's claim verdict is applied.

    The claim is planned with its parts, as the challenge planner plans every
    claim, and its verdict is ``confirm`` for a ``survived`` claim and ``refute``
    for a ``downgraded`` one (a refute removes the claim).
    """
    sid, item = "s", "In-text #0a1b2c3d"
    claim = case["coverage"]
    row = {"source_id": sid, "item": item, "refuted": [], "runs": {"challenge": None},
           "locations": [dict(l, library="alpha", declaration=l["location_key"], rank=rank)
                         for rank, l in enumerate(case["locations"], 1)],
           "coverage": {"complete": True, "parts": list(claim["parts"]),
                        "note": "the parts state every clause", "challenge": None}}
    key = outcomes.record_key(vocab.COVERAGE_TARGET, sid, item)
    planned = {key: {"planned": vocab.CHALLENGE_REQUIRED, "parts": list(claim["parts"]),
                     "challenge_run": "chl"}}
    action = {vocab.CHALLENGE_SURVIVED: "confirm",
              vocab.CHALLENGE_DOWNGRADED: "refute"}[claim["challenge"]]
    verdicts = {key: {"action": action, "witness": "checked", "challenge_run": "chl"}}
    problems: list[str] = []
    outcomes._apply_coverage(row, planned, verdicts, problems)
    assert not problems, problems
    return {l["location_key"]: l.get("challenge") for l in row["locations"]}


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
        for value in sorted(vocab.CHALLENGE_VERDICTS - {vocab.CHALLENGE_SURVIVED}):
            with self.subTest(challenge=value):
                self.assertEqual(vocab.derive_status("theorem", parts, False,
                                                     claim("ab", value)), "partial")

    def test_an_owed_claim_is_no_records_claim(self):
        """Parity round 2, C14: a claim with no challenge value is refused, as the
        consumer refuses it; its holder passes no claim, which gives the same status."""
        parts = [loc("a", "hasPart", "proved"), loc("b", "hasPart", "proved")]
        with self.assertRaisesRegex(CfError, "carries its challenge value"):
            vocab.derive_status("theorem", parts, False, claim("ab", None))
        self.assertEqual(vocab.derive_status("theorem", parts, False, None), "partial")

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
                    {"parts": ["a", "b"], "challenge": "survived"},
                    {"complete": True, "parts": ["a", "b"]},
                    {"complete": True, "parts": ["a", "b"], "challenge": 1},
                    [claim("ab"), claim("ab")]):
            with self.subTest(claim=bad):
                with self.assertRaises(CfError):
                    vocab.derive_status("theorem", parts, False, bad)

    def test_the_claim_is_the_records_three_keys(self):
        """Parity round 2, A14: the record format's claim, and no note beside it."""
        parts = [loc("a", "hasPart", "proved"), loc("b", "hasPart", "proved")]
        self.assertEqual(vocab.COVERAGE_RECORD_KEYS, {"complete", "parts", "challenge"})
        with self.assertRaisesRegex(CfError, "unexpected note"):
            vocab.derive_status("theorem", parts, False, dict(claim("ab"), note="both halves"))

    def test_a_refuted_claim_is_not_one(self):
        """A refute removes the claim: ``refuted`` is no challenge value."""
        parts = [loc("a", "hasPart", "proved"), loc("b", "hasPart", "proved")]
        with self.assertRaisesRegex(CfError, "invalid coverage challenge: 'refuted'"):
            vocab.derive_status("theorem", parts, False, claim("ab", "refuted"))

    def test_one_location_key_on_two_locations_is_refused(self):
        """Parity round 2, A15/A15b: whichever comes last, and with no claim at all."""
        for locations in ([loc("b", "relatedMatch", "proved"), loc("a", "hasPart", "proved"),
                           loc("b", "hasPart", "proved")],
                          [loc("a", "hasPart", "proved"), loc("b", "hasPart", "proved"),
                           loc("b", "relatedMatch", "proved")]):
            with self.subTest(order=[l["relation"] for l in locations]):
                with self.assertRaisesRegex(CfError, "appears twice"):
                    vocab.derive_status("theorem", locations, False, claim("ab"))
        with self.assertRaisesRegex(CfError, "appears twice"):
            vocab.derive_status("theorem", [loc("a", "hasPart", "proved"),
                                            loc("a", "exactMatch", "proved")], False)
        # locations with no key (the bench's gold declarations) are not compared
        bare = {"relation": "exactMatch", "proof_status": "proved"}
        self.assertEqual(vocab.derive_status("theorem", [bare, dict(bare)], False),
                         "formalized")

    def test_a_survived_claim_is_one_more_exact_match(self):
        """Round 3: the claim acts as one exactMatch location -- a proved theorem (theorem
        row) or a definition (definition row) when every part is finished, a statement_only
        declaration otherwise -- for every passage kind and every pair of parts."""
        parts = [(vocab.PROOF_PROVED, False), (vocab.PROOF_DEFINITION, False),
                 (vocab.PROOF_DEFINITION, True), (vocab.PROOF_STATEMENT_ONLY, False),
                 (vocab.PROOF_STATEMENT_ONLY, True), (vocab.PROOF_WANTED, False),
                 (vocab.PROOF_AXIOM_DEPENDENT, True)]
        finished = {(vocab.PROOF_PROVED, False), (vocab.PROOF_DEFINITION, False)}
        for kind in sorted(vocab.PASSAGE_KINDS):
            for first in parts:
                for second in parts:
                    locations = [loc("a", "hasPart", *first), loc("b", "hasPart", *second)]
                    if {first, second} <= finished:
                        stands_for = (vocab.PROOF_DEFINITION
                                      if kind in vocab.DEFINITION_PASSAGE_KINDS
                                      else vocab.PROOF_PROVED)
                    else:
                        stands_for = vocab.PROOF_STATEMENT_ONLY
                    with self.subTest(kind=kind, parts=(first, second)):
                        self.assertEqual(
                            vocab.derive_status(kind, locations, False, claim("ab")),
                            vocab.derive_status(kind, locations + [loc("c", "exactMatch",
                                                                       stands_for)], False))
                        self.assertIn(vocab.derive_status(kind, locations, False, claim("ab")),
                                      {"formalized", "statement_only"})

    def test_the_constants(self):
        self.assertEqual(vocab.COVERAGE_TARGET, "coverage")
        self.assertIn(vocab.COVERAGE_TARGET, vocab.CHALLENGE_TARGETS)
        self.assertEqual(vocab.MIN_COVERAGE_PARTS, 2)
        self.assertEqual(vocab.COVERAGE_ACTIONS, {"confirm", "refute"})
        self.assertLessEqual(vocab.COVERAGE_ACTIONS, vocab.CHALLENGE_ACTIONS)
        for name in ("COVERAGE_TARGET", "MIN_COVERAGE_PARTS", "COVERAGE_ACTIONS",
                     "COVERAGE_RECORD_KEYS"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", VOCABULARY_MD)

    def test_the_decision_is_quoted_and_dated(self):
        flat = " ".join(VOCABULARY_MD.replace("\n> ", "\n").split())
        self.assertIn(DECISION, flat)
        self.assertIn("2026-10-18", VOCABULARY_MD)



# --------------------------------------------------------------------------
# the gate: a coverage claim in a locate document, a coverage verdict
# --------------------------------------------------------------------------

def covered_body(**claim) -> dict:
    """``locate_body`` with its theorem passage stated by two hasPart parts and a claim."""
    body = G.locate_body()
    body["passages"][0]["locations"] = [
        G.location(relation="hasPart", rank=1),
        G.location(decl="Widget.Spanning", relation="hasPart", rank=2)]
    body["passages"][0]["coverage"] = {"complete": True, "parts": [1, 2],
                                       "note": "the existence half and the definition half "
                                               "together give the statement", **claim}
    return body


class TestTheGateReadsAClaim(G.GateTestCase):

    TASK = "t-loc-0001"

    def setUp(self):
        self.fx = G.Fixture(self, "locate")
        self.fx.manifest(self.TASK, G.locate_manifest_doc(self.fx))

    def run_body(self, body):
        return self.fx.run(self.fx.document(self.TASK, body))

    def test_a_good_claim_passes(self):
        self.assertOk(self.run_body(covered_body()))

    def test_the_parts_are_two_distinct_hasPart_ranks(self):
        for parts, needle in (([1], "at least 2 parts"), ([1, 1], "names a rank twice"),
                              ([1, 3], "part 3 is the rank of no location"),
                              (["1", "2"], "parts is a list of the ranks"),
                              ([True, 2], "parts is a list of the ranks")):
            with self.subTest(parts=parts):
                self.assertRule(self.run_body(covered_body(parts=parts)), 1, needle)
        body = covered_body()
        body["passages"][0]["locations"][1]["relation"] = "narrowMatch"
        self.assertRule(self.run_body(body), 1, "part 2 is a narrowMatch location")

    def test_the_shape(self):
        self.assertRule(self.run_body(covered_body(complete=False)), 1, "complete is true")
        self.assertRule(self.run_body(covered_body(challenge="survived")), 1,
                        "'challenge'")
        body = covered_body()
        body["passages"][0]["coverage"] = [1, 2]
        self.assertRule(self.run_body(body), 1, "coverage is {complete")

    def test_the_note_is_required_and_in_the_workers_own_words(self):
        self.assertRule(self.run_body(covered_body(note=" ")), 1, "note says")
        self.assertRule(self.run_body(covered_body(note="x" * 2001)), 1, "at most 2000")
        verbatim = " ".join(G.LEAD.split()[:15])
        self.assertRule(self.run_body(covered_body(note=verbatim)), 1, "verbatim")

    def test_no_claim_on_an_absent_or_unresolved_passage(self):
        body = G.locate_body()
        body["passages"][1]["coverage"] = {"complete": True, "parts": [1, 2], "note": "both"}
        self.assertRule(self.run_body(body), 1, "this one is absent")
        body = covered_body()
        body["passages"][0].update(outcome="unresolved", locations=[], reason="unsure")
        body["passages"][0]["receipt"]["candidates"] = [
            {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
             "verdict": "rejected", "reason": "unsure"}]
        self.assertRule(self.run_body(body), 1, "this one is unresolved")


class TestTheGateReadsACoverageVerdict(G.GateTestCase):

    TASK = "t-chl-0001"
    KEY = "9" * 64
    PART = "7" * 64

    def setUp(self):
        self.fx = G.Fixture(self, "locate-challenge")
        doc = G.locate_manifest_doc(self.fx)
        doc.pop("passages")
        doc["records"] = [
            {"item": G.THM_ITEM, "target": "coverage", "span": G.THEOREM_SPAN,
             "passage_kind": "theorem", "note": "the two halves",
             "parts": [{"location_key": self.KEY, "library": "alpha", "relation": "hasPart",
                        "declaration": "Widget.exists_spanning_gadget", "rank": 1},
                       {"location_key": self.PART, "library": "alpha", "relation": "hasPart",
                        "declaration": "Widget.Spanning", "rank": 2}]},
            {"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
             "span": G.THEOREM_SPAN, "passage_kind": "theorem", "relation": "hasPart"}]
        self.fx.manifest(self.TASK, doc)

    def body(self, coverage="confirm", location="confirm"):
        return {"verdicts": [
            {"item": G.THM_ITEM, "target": "coverage", "action": coverage,
             "witness": "the first part states existence, the second the definition"},
            {"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
             "action": location, "witness": "the signature states the existence half"}]}

    def test_confirm_and_refute(self):
        self.assertOk(self.fx.run(self.fx.document(self.TASK, self.body())))
        self.assertOk(self.fx.run(self.fx.document(self.TASK, self.body("refute"))))
        self.assertOk(self.fx.run(self.fx.document(self.TASK, self.body("refute", "refute"))))
        for action in ("uphold", "downgrade", "overturn"):
            with self.subTest(action=action):
                self.assertRule(self.fx.run(self.fx.document(self.TASK, self.body(action))), 1,
                                "does not apply to a coverage")

    def test_a_claim_is_not_confirmed_beside_a_refuted_part(self):
        self.assertRule(self.fx.run(self.fx.document(self.TASK, self.body("confirm", "refute"))),
                        1, "confirms the coverage claim")
        body = self.body()
        body["verdicts"][1].update(action="downgrade", relation="relatedMatch")
        self.assertRule(self.fx.run(self.fx.document(self.TASK, body)), 1,
                        "confirms the coverage claim")


# --------------------------------------------------------------------------
# the planner, the outcomes and the export, over the synthetic campaign
# --------------------------------------------------------------------------

def _records(out_dir: Path) -> list[dict]:
    return [json.loads(line) for path in sorted((out_dir / "records").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


class _Covered(Campaign):
    """``loc-1`` with Widget lemma's central theorem stated by two hasPart parts."""

    SECOND = "Widget.exists_spanning_gadget"

    def setUp(self):
        super().setUp()
        F.plan_locate(self, self.root)
        docs = F.locate_documents(self.paths, "loc-1")
        for doc in docs.values():
            if doc["source_id"] == F.W1:
                passage = doc["passages"][1]
                passage["locations"].append(F._loc("alpha", self.SECOND, "hasPart", 2))
                passage["coverage"] = {"complete": True, "parts": [1, 2],
                                       "note": "divisibility and existence together state it"}
        F.write_harvest(self.paths, "loc-1", docs)
        self.item = F.frozen_formal_math(self.paths, F.W1)[1]["item"]

    def row(self, challenge_runs=("chl-1",)) -> dict:
        built = outcomes.build(self.paths, "loc-1", challenge_runs=list(challenge_runs))
        return next(r for r in built["outcomes"] if r["item"] == self.item)

    def export(self):
        return ws(self.root, "export", "literature-links", "--run", "loc-1", "--challenge-run",
                  "chl-1", "--set-id", "set-1")

    def passage_records(self) -> list[dict]:
        return [r for r in _records(self.paths.export_dir("set-1"))
                if r["extensions"][EXT]["item"] == self.item]


class TestTheClaimThroughTheRun(_Covered):

    def test_unchallenged_it_is_owed_and_upgrades_nothing(self):
        row = self.row(challenge_runs=())
        keys = {l["declaration"]: l["location_key"] for l in row["locations"]}
        self.assertEqual(row["coverage"], {
            "complete": True, "parts": [keys["Widget.card_dvd"], keys[self.SECOND]],
            "note": "divisibility and existence together state it", "challenge": None})
        self.assertEqual(row["status"], "partial")
        self.assertIsNone(row["challenge"])
        self.assertIn("coverage", outcomes.outcome_row(row))

    def test_every_claim_is_planned_with_its_parts_in_one_task(self):
        self.challenge()
        plan = read_json(self.paths.run_dir("chl-1") / "challenge-plan.json")
        (rec,) = [r for r in plan["records"] if r["target"] == "coverage"]
        self.assertEqual((rec["rule"], rec["planned"]), ("refute_by_default", "required"))
        row = self.row(challenge_runs=())
        self.assertEqual(rec["parts"], row["coverage"]["parts"])
        tasks = {r["task_id"] for r in plan["records"]
                 if r["item"] == self.item and r["planned"] == "required"}
        self.assertEqual(tasks, {rec["task_id"]})
        manifest = read_json(self.paths.tend_root("chl-1") / "gate" / "tasks"
                             / f"{rec['task_id']}.json")
        (mrec,) = [r for d in manifest["documents"] for r in d["records"]
                   if r["target"] == "coverage"]
        self.assertEqual([p["declaration"] for p in mrec["parts"]],
                         ["Widget.card_dvd", self.SECOND])
        self.assertEqual(mrec["note"], "divisibility and existence together state it")

    def test_confirmed_it_formalizes_the_passage_and_rides_on_every_record(self):
        self.challenge()
        row = self.row()
        self.assertEqual(row["coverage"]["challenge"], "survived")
        self.assertEqual((row["status"], row["challenge"]), ("formalized", "survived"))
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        recs = self.passage_records()
        self.assertEqual(len(recs), 2)
        for rec in recs:
            self.assertEqual(rec["status"], "formalized")
            self.assertEqual(rec["extensions"][EXT]["coverage"],
                             {"complete": True, "parts": row["coverage"]["parts"],
                              "challenge": "survived"})
            self.assertEqual(records.check(rec), [])

    def test_refuted_it_is_removed_and_its_parts_stay_hasPart(self):
        def refute(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "coverage":
                v.update(action="refute", witness="neither part states the minimality clause")
            return v
        self.challenge(verdict=refute)
        row = self.row()
        self.assertIsNone(row["coverage"])
        self.assertEqual(row["coverage_refuted"]["challenge"], "downgraded")
        self.assertEqual({l["relation"] for l in row["locations"]}, {"hasPart"})
        self.assertEqual((row["status"], row["challenge"]), ("partial", "downgraded"))
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        for rec in self.passage_records():
            self.assertNotIn("coverage", rec["extensions"][EXT])
            self.assertEqual(rec["status"], "partial")

    def test_a_claim_beside_a_refuted_part_no_longer_holds(self):
        def refute_part(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "location" and rec.get("declaration") == self.SECOND \
                    and rec["item"] == self.item:
                v.update(action="refute", witness="it states another widget's lemma")
            return v
        self.challenge(verdict=refute_part)
        row = self.row()
        self.assertIn("no longer holds", row["problem"])
        self.assertEqual(row["status"], "partial")
        code, out, err = self.export()
        self.assertEqual(code, 1, out + err)
        report = read_json(self.paths.export_report_json("set-1"))
        self.assertTrue(any(r["item"] == self.item and "no longer holds" in r["reason"]
                            for r in report["refused"]), report["refused"])


class TestAStatementOnlyPart(_Covered):

    SECOND = "Widget.spanning_conj"

    def test_it_gives_statement_only(self):
        self.challenge()
        self.assertEqual(self.row()["status"], "statement_only")


class TestAConfirmedClaimConfirmsItsParts(Campaign):
    """Round 3: a supporting definition passage whose parts the sample left out.

    Widget lemma's spanning definition (a ``supporting`` passage) is stated by two
    ``hasPart`` parts -- the definition and a proved lemma -- with a claim.  At
    sample rate 0 neither part is drawn (``sampled_out``) and only the claim is
    required.
    """

    PARTS = ("Widget.Spanning", "Widget.card_dvd")

    def setUp(self):
        super().setUp()
        F.plan_locate(self, self.root)
        docs = F.locate_documents(self.paths, "loc-1")
        self.item = F.frozen_formal_math(self.paths, F.W1)[2]["item"]
        for doc in docs.values():
            if doc["source_id"] == F.W1:
                (passage,) = [p for p in doc["passages"] if p["item"] == self.item]
                passage["locations"] = [F._loc("alpha", decl, "hasPart", rank)
                                        for rank, decl in enumerate(self.PARTS, 1)]
                passage["coverage"] = {"complete": True, "parts": [1, 2],
                                       "note": "the predicate and its bound give it"}
        F.write_harvest(self.paths, "loc-1", docs)

    def row(self) -> dict:
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        return next(r for r in built["outcomes"] if r["item"] == self.item)

    def test_the_parts_are_sampled_out_and_the_claim_required(self):
        self.challenge("--sample-rate", "0")
        plan = read_json(self.paths.run_dir("chl-1") / "challenge-plan.json")
        planned = {(r["target"], r.get("declaration")): r["planned"] for r in plan["records"]
                   if r["item"] == self.item and r["target"] in ("location", "coverage")}
        self.assertEqual(planned, {("location", "Widget.Spanning"): "sampled_out",
                                   ("location", "Widget.card_dvd"): "sampled_out",
                                   ("coverage", None): "required"})

    def test_confirmed_its_parts_are_survived_on_every_record(self):
        self.challenge("--sample-rate", "0")
        row = self.row()
        self.assertEqual(row["coverage"]["challenge"], "survived")
        self.assertEqual({l["declaration"]: l["challenge"] for l in row["locations"]},
                         {decl: "survived" for decl in self.PARTS})
        self.assertEqual((row["status"], row["challenge"]), ("formalized", "survived"))
        code, out, err = ws(self.root, "export", "literature-links", "--run", "loc-1",
                            "--challenge-run", "chl-1", "--set-id", "set-1")
        self.assertEqual(code, 0, out + err)
        recs = [r for r in _records(self.paths.export_dir("set-1"))
                if r["extensions"][EXT]["item"] == self.item]
        self.assertEqual(len(recs), 2)
        for rec in recs:
            self.assertEqual(rec["extensions"][EXT]["challenge"], "survived")
            self.assertEqual(rec["extensions"][EXT]["coverage"]["challenge"], "survived")
            self.assertEqual(rec["status"], "formalized")

    def test_refuted_its_parts_stay_sampled_out(self):
        def refute(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "coverage":
                v.update(action="refute", witness="neither part states the bound")
            return v
        self.challenge("--sample-rate", "0", verdict=refute)
        row = self.row()
        self.assertIsNone(row["coverage"])
        self.assertEqual({l["challenge"] for l in row["locations"]}, {"sampled_out"})
        self.assertEqual(row["status"], "partial")


class TestConfirmParts(unittest.TestCase):
    """``outcomes.confirm_parts``: only a record no verdict judged changes."""

    def test_unjudged_parts_survive_and_judged_ones_keep_their_verdict(self):
        parts = [{"challenge": value} for value in ("not_required", "sampled_out", "survived",
                                                     None, "challenge_pending")]
        outcomes.confirm_parts(parts)
        self.assertEqual([p["challenge"] for p in parts],
                         ["survived", "survived", "survived", None, "challenge_pending"])

    def test_a_lapsed_claim_confirms_nothing(self):
        case = {"locations": [loc("a", "hasPart", "proved"), loc("b", "relatedMatch", "proved")],
                "coverage": claim("ab")}
        for l in case["locations"]:
            l["challenge"] = "sampled_out"
        sid, item = "s", "In-text #0a1b2c3d"
        row = {"source_id": sid, "item": item, "refuted": [], "runs": {"challenge": None},
               "locations": [dict(l, library="alpha", declaration=l["location_key"], rank=r)
                             for r, l in enumerate(case["locations"], 1)],
               "coverage": {"complete": True, "parts": ["a", "b"], "note": "n",
                            "challenge": None}}
        key = outcomes.record_key(vocab.COVERAGE_TARGET, sid, item)
        problems: list[str] = []
        outcomes._apply_coverage(row, {key: {"planned": "required", "parts": ["a", "b"],
                                             "challenge_run": "chl"}},
                                 {key: {"action": "confirm", "challenge_run": "chl"}}, problems)
        self.assertTrue(row["coverage_lapsed"])
        self.assertIn("no longer holds", problems[0])
        self.assertEqual({l["challenge"] for l in row["locations"]}, {"sampled_out"})


class TestTheClaimReader(unittest.TestCase):

    LOCS = [{"rank": 1, "relation": "hasPart", "location_key": "a"},
            {"rank": 2, "relation": "hasPart", "location_key": "b"},
            {"rank": 3, "relation": "exactMatch", "location_key": "c"}]

    def read(self, outcome="located", **claim):
        given = {"complete": True, "parts": [2, 1], "note": " both  parts ", **claim}
        return outcomes.coverage_claim(given, outcome, self.LOCS)

    def test_ranks_become_location_keys_in_the_documents_order(self):
        self.assertEqual(self.read(), ({"complete": True, "parts": ["b", "a"],
                                        "note": "both parts", "challenge": None}, ""))

    def test_what_it_refuses(self):
        for kwargs, needle in (({"outcome": "absent"}, "absent passage"),
                               ({"complete": False}, "complete: true"),
                               ({"parts": [1]}, "at least 2"), ({"parts": [1, 1]}, "at least 2"),
                               ({"parts": [1, 3]}, "part 3"), ({"parts": [1, 4]}, "part 4"),
                               ({"note": ""}, "note")):
            with self.subTest(**kwargs):
                claim, why = self.read(**kwargs)
                self.assertIsNone(claim)
                self.assertIn(needle, why)


class TestTheStatusReadsTheRecordsClaim(unittest.TestCase):
    """Parity round 2: the outcomes derive the status from the claim the records carry.

    ``records.coverage_ext`` is the one projection: the export writes it, and
    ``outcomes._derive`` hands it to ``derive_status`` -- so the claim's note, which
    the workspace keeps and no record carries, cannot change a status, and an owed
    claim (no challenge value) is passed as no claim.
    """

    @staticmethod
    def row(challenge="survived", **extra):
        locations = [{"location_key": k, "relation": "hasPart", "proof_status": "proved",
                      "statement": False, "challenge": "survived", "rank": r}
                     for r, k in enumerate(("a" * 64, "b" * 64), 1)]
        return {"source_id": "s", "item": "In-text #0a1b2c3d", "passage_kind": "theorem",
                "outcome": "located", "missing": False, "locations": locations,
                "refuted": [], "absence": None, "coverage_refuted": None,
                "coverage": {"complete": True, "parts": ["a" * 64, "b" * 64],
                             "note": "the two halves state both clauses",
                             "challenge": challenge}, **extra}

    def derive(self, row):
        problems = []
        outcomes._derive(row, problems)
        return row["status"], problems

    def test_the_projection_is_the_three_keys(self):
        claim = self.row()["coverage"]
        self.assertEqual(records.coverage_ext(claim),
                         {"complete": True, "parts": claim["parts"], "challenge": "survived"})
        self.assertEqual(set(records.coverage_ext(claim)), vocab.COVERAGE_RECORD_KEYS)

    def test_a_survived_claim_with_its_note_formalizes(self):
        self.assertEqual(self.derive(self.row()), ("formalized", []))

    def test_an_owed_claim_upgrades_nothing_and_is_no_problem_here(self):
        row = self.row(challenge=None)
        self.assertEqual(self.derive(row), ("partial", []))
        self.assertIsNone(row["challenge"])          # the export refuses it as owed

    def test_a_lapsed_claim_upgrades_nothing(self):
        self.assertEqual(self.derive(self.row(coverage_lapsed=True)), ("partial", []))

    def test_the_export_refuses_a_claim_the_consumer_refuses(self):
        row = self.row()
        passage = {"item": row["item"], "locations": row["locations"],
                   "coverage": dict(row["coverage"])}
        self.assertIsNone(records.coverage_problem(passage))
        for value, needle in ((None, "no challenge value"), ("refuted", "is not one of")):
            with self.subTest(challenge=value):
                passage["coverage"]["challenge"] = value
                self.assertIn(needle, records.coverage_problem(passage))


class TestTheBatches(unittest.TestCase):

    @staticmethod
    def rec(item, target, n):
        return {"source_id": "s", "span": [int(item[-1]), 0], "item": item, "target": target,
                "key": f"{item}-{target}-{n}", "planned": vocab.CHALLENGE_REQUIRED}

    def test_a_covered_passage_is_never_split(self):
        records = [self.rec("p1", "location", i) for i in range(3)]
        records += [self.rec("p2", "coverage", 0)] + [self.rec("p2", "location", i)
                                                      for i in range(2)]
        got = [[r["item"] for r in batch] for batch in challenge_plan.batches(records, size=4)]
        self.assertEqual(got, [["p1", "p1", "p1"], ["p2", "p2", "p2"]])
        # without the claim the old cut applies: four to a task
        plain = [r for r in records if r["target"] != "coverage"]
        got = [[r["item"] for r in batch] for batch in challenge_plan.batches(plain, size=4)]
        self.assertEqual(got, [["p1", "p1", "p1", "p2"], ["p2"]])


class TestThePrompts(unittest.TestCase):
    """When to claim coverage, and how a challenger checks a claim: adversarially."""

    @classmethod
    def setUpClass(cls):
        from locate import inputs, plan as lplan, presets as lp, prompts as lprompts
        from test_locate_planner import scratch

        cls.rendered = {}
        case = unittest.TestCase()
        paths = F.make_workspace(scratch(case))
        F.write_universe(paths)
        db = {"path": str(paths.corpus_db(F.U)), "sha256": "1" * 64, "kind": "full"}
        layout = lplan.host_layout(paths, host="examplebox", run_id="r1", sourceset_id=F.SS,
                                   universe_id=F.U, tools_db=db, remote_root="/srv/cf")["host"]
        for mode in (vocab.LOCATE, vocab.LOCATE_CHALLENGE):
            preset = lp.get(mode)
            ctx = lprompts.build_context(preset, run_id="r1", host=layout,
                                         universe=inputs.load_universe(paths, F.U), per_task=8)
            cls.rendered[mode] = lprompts.render_prompts(preset, ctx)
        case.doCleanups()

    def test_the_worker_is_told_when_to_claim(self):
        worker = self.rendered[vocab.LOCATE]["worker.md"]
        self.assertIn('"coverage": {"complete": true, "parts": [1, 2]', worker)
        self.assertIn("**together** state **every** clause", worker)
        self.assertIn("no part assumes more than the passage does", worker)
        self.assertIn("at least 2 of this passage's `hasPart` locations", worker)
        self.assertIn("Never put `coverage` on an `absent` or `unresolved` passage", worker)
        self.assertIn("the Chinese remainder theorem as a ring isomorphism", worker)
        system = self.rendered[vocab.LOCATE]["worker-system.md"]
        self.assertIn("a coverage claim on a passage that is not located", system)

    def test_the_challenger_refutes_unless_every_clause_is_matched(self):
        worker = self.rendered[vocab.LOCATE_CHALLENGE]["worker.md"]
        self.assertIn("`prominence` or `coverage`", worker)
        self.assertIn("**refute** it unless every clause of the passage is matched by a part "
                      "AND no part adds a hypothesis the passage does not have", worker)
        self.assertIn("The worker's `note` is a claim to check, not evidence", worker)
        self.assertIn('"target": "coverage", "action": "refute"', worker)
        self.assertIn("a coverage claim takes `confirm` or `refute` only", worker)
        self.assertNotIn("downgraded", worker)
        for name, text in self.rendered[vocab.LOCATE_CHALLENGE].items():
            with self.subTest(name=name):
                self.assertNotRegex(text, r"\{\{")


if __name__ == "__main__":
    unittest.main()
