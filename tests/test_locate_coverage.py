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
