"""The locate judgment rules: gate rule 9, the documents' new fields and their prompts.

New code, for references/vocabulary.md section 1.11 (rules R1-R7 and the policy
defaults) and references/locate-documents.md sections 3, 4 and 10, from the
smoke-2 audit's failure modes: a whole-passage relation for a declaration that
states some clauses (FM1), an absence whose queries missed a clause (FM5), a
declaration whose hypotheses exclude the passage's objects (FM6), a proof
ingredient credited as a part (FM3) and a closeMatch for what is no convention
(FM4).  The gate's artifacts are test_verify_locate's synthetic ones (the
"Widget lemma" layer, libraries ``alpha`` and ``beta``); every claim, note and
query below is made up.  Rule 9 binds a run whose ``gate.json`` names
``judgment_rules`` and no other, so every document a run planned before the
rules wrote is gated as it was: each test that refuses a document checks that.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
for path in (SCRIPTS, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import _locate_fixtures as F  # noqa: E402
import test_verify_locate as G  # noqa: E402
from cflib import vocab  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from gate import verify_locate as vl  # noqa: E402
from locate import outcomes  # noqa: E402
from test_locate_planner import scratch, ws  # noqa: E402

THM_CLAIMS = [{"id": "c1", "text": "a finite widget has a spanning gadget"},
              {"id": "c2", "text": "that gadget's size divides the order"}]
LEAD_CLAIMS = [{"id": "c1", "text": "finite widgets carry spanning gadgets"}]


def judged(loc: dict, clauses=("c1", "c2"), **extra) -> dict:
    """A location as the judgment rules have it written: the claim ids it states."""
    return {**loc, "clauses": list(clauses), **extra}


def judged_body() -> dict:
    """``test_verify_locate.locate_body`` with the judgment rules' fields."""
    body = G.locate_body()
    thm, lead = body["passages"]
    thm["claims"] = copy.deepcopy(THM_CLAIMS)
    thm["locations"] = [judged(loc) for loc in thm["locations"]]
    lead["claims"] = copy.deepcopy(LEAD_CLAIMS)
    return body


class JudgedLocate(G.GateTestCase):
    """A locate run planned under the judgment rules (``gate.json`` names them)."""

    TASK = "t-loc-0001"

    def setUp(self):
        self.fx = G.Fixture(self, "locate")
        self.fx.gate["judgment_rules"] = vocab.JUDGMENT_RULES
        self.fx.write_gate()
        self.fx.manifest(self.TASK, G.locate_manifest_doc(self.fx))

    def run_body(self, body: dict) -> dict:
        return self.fx.run(self.fx.document(self.TASK, body))

    def unruled(self, body: dict) -> dict:
        """The same document gated as a run planned before the judgment rules."""
        self.fx.gate.pop("judgment_rules", None)
        self.fx.write_gate()
        try:
            return self.run_body(body)
        finally:
            self.fx.gate["judgment_rules"] = vocab.JUDGMENT_RULES
            self.fx.write_gate()

    def assertRefusedOnlyUnderTheRules(self, body: dict, needle: str, rule: int = 9):
        self.assertRule(self.run_body(body), rule, needle)
        self.assertOk(self.unruled(body))


# --------------------------------------------------------------------------- #
# FM1: the claim list and the clauses a record states
# --------------------------------------------------------------------------- #

class TestTheClaimList(JudgedLocate):

    def test_a_judged_document_passes(self):
        self.assertOk(self.run_body(judged_body()))

    def test_a_located_or_absent_passage_writes_its_claims_first(self):
        for i in (0, 1):
            body = judged_body()
            del body["passages"][i]["claims"]
            with self.subTest(passage=i):
                self.assertRefusedOnlyUnderTheRules(body, "claim list first")
        # an unresolved passage may leave them out
        body = judged_body()
        body["passages"][1] = {"item": G.LEAD_ITEM, "outcome": "unresolved", "locations": [],
                               "reason": "ambiguous", "receipt": {"queries": [],
                                                                  "candidates": []}}
        self.assertOk(self.run_body(body))

    def test_the_claim_lists_shape(self):
        cases = {
            "not c1, c2": [{"id": "1", "text": "a clause"}],
            "given twice": [{"id": "c1", "text": "a clause"}, {"id": "c1", "text": "another"}],
            "nothing else": [{"id": "c1", "text": "a clause", "source": "x"}],
            "short paraphrase": [{"id": "c1", "text": "  "}],
            "one line": [{"id": "c1", "text": "a clause\nand a second line"}],
            "non-empty list": [],
            "at most 12": [{"id": f"c{i}", "text": f"clause {i}"} for i in range(1, 14)],
        }
        for needle, claims in cases.items():
            body = judged_body()
            body["passages"][1]["claims"] = claims
            with self.subTest(needle=needle):
                self.assertRule(self.run_body(body), 9, needle)

    def test_a_claim_is_paraphrased_never_the_source_verbatim(self):
        body = judged_body()
        body["passages"][0]["claims"][0]["text"] = " ".join(G.THEOREM.split()[:15])
        self.assertRule(self.run_body(body), 1, "own words")

    def test_a_counting_location_names_the_claims_it_states(self):
        body = judged_body()
        del body["passages"][0]["locations"][0]["clauses"]
        self.assertRefusedOnlyUnderTheRules(body, "names the ids of the claims it states")
        body = judged_body()
        body["passages"][0]["locations"][0]["clauses"] = ["c1", "c9"]
        self.assertRule(self.run_body(body), 9, "c9 are not ids of the passage's claims")
        body["passages"][0]["locations"][0]["clauses"] = ["c1", "c1"]
        self.assertRule(self.run_body(body), 9, "distinct claim ids")

    def test_a_strong_relation_states_every_clause(self):
        for relation, ok in (("exactMatch", False), ("closeMatch", False),
                             ("broadMatch", False), ("hasPart", True), ("narrowMatch", True)):
            body = judged_body()
            loc = body["passages"][0]["locations"][0]
            loc.update(relation=relation, clauses=["c1"])
            if relation == "closeMatch":
                loc.update(note="the order is written as a cardinal",
                           convention="encoding")
            with self.subTest(relation=relation):
                if ok:
                    self.assertOk(self.run_body(body))
                else:
                    self.assertRefusedOnlyUnderTheRules(body, "does not state c2")

    def test_a_related_match_states_no_clause(self):
        body = judged_body()
        body["passages"][1]["locations"] = [judged(G.location(decl="Widget.Spanning",
                                                              relation="relatedMatch"), ["c1"])]
        self.assertRefusedOnlyUnderTheRules(body, "relatedMatch states no clause")
        body["passages"][1]["locations"][0]["clauses"] = []
        self.assertOk(self.run_body(body))

    def test_a_note_that_admits_a_missing_clause_is_no_strong_relation(self):
        for note in ("It does not state the divisibility of the size.",
                     "Gives only the existence; the size bound is a separate lemma.",
                     "The size bound is not part of this declaration.",
                     "Stated as a bijection rather than asserting the divisibility"):
            body = judged_body()
            body["passages"][0]["locations"][0].update(relation="broadMatch", note=note)
            with self.subTest(note=note):
                self.assertRefusedOnlyUnderTheRules(body, "admits a clause")
            body["passages"][0]["locations"][0].update(relation="hasPart", clauses=["c1"])
            self.assertOk(self.run_body(body))

    def test_a_coverage_claims_parts_state_every_clause(self):
        body = judged_body()
        thm = body["passages"][0]
        thm["locations"] = [judged(G.location(relation="hasPart"), ["c1"]),
                            judged(G.location(decl="Widget.Spanning", relation="hasPart",
                                              rank=2), ["c1"])]
        thm["coverage"] = {"complete": True, "parts": [1, 2],
                           "note": "the two lemmas together give the theorem"}
        self.assertRefusedOnlyUnderTheRules(body, "no part states c2")
        thm["locations"][1]["clauses"] = ["c2"]
        self.assertOk(self.run_body(body))

    def test_a_gate_json_naming_newer_rules_cannot_run(self):
        for value, needle in ((vocab.JUDGMENT_RULES + 1, "this gate knows"),
                              ("1", "not a version number"), (-1, "not a version number")):
            self.fx.gate["judgment_rules"] = value
            self.fx.write_gate()
            with self.subTest(value=value), self.assertRaises(vl.GateBroken) as caught:
                self.fx.gate_cfg()
            self.assertIn(needle, str(caught.exception))


class TestAdmitsAMissingClause(unittest.TestCase):

    def test_the_closed_phrase_list(self):
        for note, phrase in (
                ("The bound is NOT STATED here.", "not stated"),
                ("it doesn’t state uniqueness", "doesn't state"),
                ("states one direction; not the converse", "not the converse"),
                ("existence, without uniqueness", "without uniqueness"),
                ("the negative powers are not covered", "not covered")):
            with self.subTest(note=note):
                self.assertEqual(vocab.admits_missing_clause(note), phrase)
                self.assertEqual(vl.admits_missing_clause(note), phrase)
        for note in ("", None, "junk value of deriv off the differentiable points",
                     "the notstated macro", "stated as a bijection of quotients"):
            with self.subTest(note=note):
                self.assertIsNone(vocab.admits_missing_clause(note))
                self.assertIsNone(vl.admits_missing_clause(note))

    def test_the_gates_copies_are_cflibs(self):
        self.assertEqual(vl.JUDGMENT_RULES, vocab.JUDGMENT_RULES)
        self.assertEqual(vl.STRONG_RELATIONS, vocab.STRONG_RELATIONS)
        self.assertEqual(vl.CLAUSE_GAP_PHRASES, vocab.CLAUSE_GAP_PHRASES)
        self.assertEqual(vl.CLAIM_ID.pattern, vocab.CLAIM_ID.pattern)
        self.assertEqual((vl.MAX_PASSAGE_CLAIMS, vl.MAX_CLAIM_CHARS),
                         (vocab.MAX_PASSAGE_CLAIMS, vocab.MAX_CLAIM_CHARS))


# --------------------------------------------------------------------------- #
# the challenge document: the challenger's own claim list
# --------------------------------------------------------------------------- #

class TestTheChallengersClaims(G.GateTestCase):

    TASK = "t-chl-0001"
    KEY = "9" * 64

    def setUp(self):
        self.fx = G.Fixture(self, "locate-challenge")
        self.fx.gate["judgment_rules"] = vocab.JUDGMENT_RULES
        self.fx.write_gate()
        self.records(note="")

    def records(self, *, relation="broadMatch", note=""):
        doc = G.locate_manifest_doc(self.fx)
        doc.pop("passages")
        doc["records"] = [{"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
                           "span": G.THEOREM_SPAN, "passage_kind": "theorem",
                           "relation": relation, "note": note},
                          {"item": G.LEAD_ITEM, "target": "absence", "span": G.LEAD_SPAN,
                           "passage_kind": "theorem"}]
        self.fx.manifest(self.TASK, doc)

    def body(self) -> dict:
        return {"verdicts": [
            {"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
             "action": "confirm", "clauses": ["c1", "c2"], "witness": "the signature states it"},
            {"item": G.LEAD_ITEM, "target": "absence", "action": "overturn",
             "locations": [judged(G.location(lib="beta", decl="Beta.Widget.gadget_of_finite"),
                                  ["c1"])],
             "witness": "found with a type search"}],
            "claims": {G.THM_ITEM: copy.deepcopy(THM_CLAIMS),
                       G.LEAD_ITEM: copy.deepcopy(LEAD_CLAIMS)}}

    def run_body(self, body: dict) -> dict:
        return self.fx.run(self.fx.document(self.TASK, body))

    def unruled(self, body: dict) -> dict:
        self.fx.gate.pop("judgment_rules", None)
        self.fx.write_gate()
        return self.run_body(body)

    def test_a_judged_challenge_passes(self):
        self.assertOk(self.run_body(self.body()))

    def test_a_confirm_names_the_challengers_claims(self):
        body = self.body()
        del body["claims"][G.THM_ITEM]
        self.assertRule(self.run_body(body), 9, "claim list under claims first")
        body = self.body()
        del body["verdicts"][0]["clauses"]
        self.assertRule(self.run_body(body), 9, "names the ids of the claims it states")
        body = self.body()
        body["verdicts"][0]["clauses"] = ["c1"]
        self.assertRule(self.run_body(body), 9, "does not state c2")
        self.assertOk(self.unruled(body))

    def test_a_downgrade_names_what_the_relation_it_leaves_states(self):
        body = self.body()
        body["verdicts"][0].update(action="downgrade", relation="hasPart", clauses=["c2"])
        self.assertOk(self.run_body(body))
        body["verdicts"][0].update(relation="relatedMatch", clauses=["c2"])
        self.assertRule(self.run_body(body), 9, "relatedMatch states no clause")
        del body["verdicts"][0]["clauses"]
        self.assertOk(self.run_body(body))
        # a refute leaves no relation, and names nothing
        body["verdicts"][0] = {"item": G.THM_ITEM, "target": "location",
                               "location_key": self.KEY, "action": "refute", "witness": "no"}
        self.assertOk(self.run_body(body))

    def test_a_record_whose_note_admits_a_gap_is_downgraded_never_confirmed(self):
        self.records(note="the size bound is not part of this declaration")
        self.assertRule(self.run_body(self.body()), 9, "a downgrade to hasPart")
        body = self.body()
        body["verdicts"][0].update(action="downgrade", relation="hasPart", clauses=["c1"])
        self.assertOk(self.run_body(body))

    def test_an_overturns_locations_are_judged_like_a_workers(self):
        body = self.body()
        del body["claims"][G.LEAD_ITEM]
        self.assertRule(self.run_body(body), 9, "write the passage's claim list")
        body = self.body()
        del body["verdicts"][1]["locations"][0]["clauses"]
        self.assertRule(self.run_body(body), 9, "names the ids")
        self.assertOk(self.unruled(body))

    def test_claims_for_a_passage_the_task_does_not_judge(self):
        body = self.body()
        body["claims"]["In-text #0badc0de"] = copy.deepcopy(LEAD_CLAIMS)
        self.assertRule(self.run_body(body), 9, "no record of this task is on")
        body = self.body()
        body["claims"] = [THM_CLAIMS]
        self.assertRule(self.run_body(body), 9, "claims is {<item>")


# --------------------------------------------------------------------------- #
# the outcomes and the challenger see what the documents said
# --------------------------------------------------------------------------- #

class TestTheFieldsReachTheOutcomesAndTheChallenger(unittest.TestCase):
    """The judgment rules' fields are the documents' and the outcomes', never the export's.

    Widget lemma's subject passage gets a claim list and clauses; its supporting
    definition passage an ``exactMatch`` whose note admits a clause it does not state."""

    GAP = "the conjugacy clause is not stated by this definition"

    def setUp(self):
        self.root = scratch(self)
        self.paths = F.campaign(self, self.root)
        F.plan_locate(self, self.root, run_id="loc-1")
        self.docs = F.locate_documents(self.paths, "loc-1")
        self.plain = copy.deepcopy(self.docs)
        for doc in self.docs.values():
            for p in doc["passages"]:
                names = [l["declaration"] for l in p["locations"]]
                if "Widget.exists_spanning_gadget" in names:
                    p["claims"] = copy.deepcopy(LEAD_CLAIMS)
                    for loc in p["locations"]:
                        loc["clauses"] = ["c1"]
                if "Widget.Spanning" in names:
                    p["locations"][0]["note"] = self.GAP
        F.write_harvest(self.paths, "loc-1", self.docs)

    def subject(self, built: dict) -> dict:
        return next(r for r in built["outcomes"] if r["source_id"] == F.W1
                    and r["prominence"] == "subject")

    def challenge(self, verdict=None, claims=None) -> None:
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-challenge",
                            "--sourceset", F.SS, "--universe", F.U, "--challenge-of", "loc-1",
                            "--run-id", "chl-1")
        self.assertEqual(code, 0, out + err)
        docs = F.challenge_documents(self.paths, "chl-1", verdict=verdict)
        for doc in docs.values():
            if claims:
                doc["claims"] = claims
        F.write_harvest(self.paths, "chl-1", docs)

    def test_the_outcomes_keep_the_claims_and_clauses_a_document_wrote(self):
        built = outcomes.build(self.paths, "loc-1", require_gate=True)
        row = self.subject(built)
        self.assertEqual(row["claims"], LEAD_CLAIMS)
        self.assertEqual([l["clauses"] for l in row["locations"]], [["c1"], ["c1"]])
        self.assertEqual(outcomes.outcome_row(row)["claims"], LEAD_CLAIMS)
        theorem = next(r for r in built["outcomes"] if r["passage_kind"] == "theorem"
                       and r["source_id"] == F.W1)
        self.assertNotIn("claims", outcomes.outcome_row(theorem))
        self.assertNotIn("clauses", theorem["locations"][0])

    def test_a_document_without_them_yields_the_rows_it_always_did(self):
        F.write_harvest(self.paths, "loc-1", self.plain)
        built = outcomes.build(self.paths, "loc-1", require_gate=True)
        for row in built["outcomes"]:
            out = outcomes.outcome_row(row)
            self.assertFalse({"claims", "challenge_claims"} & set(out), out)
            for loc in out["locations"] or []:
                self.assertFalse(set(vocab.JUDGMENT_LOCATION_FIELDS) & set(loc), loc)

    def test_the_challenger_sees_them_and_a_gap_note_is_refuted_by_default(self):
        self.challenge()
        plan = read_json(self.paths.run_dir("chl-1") / "challenge-plan.json")
        spanning = next(r for r in plan["records"] if r.get("declaration") == "Widget.Spanning")
        self.assertEqual((spanning["prominence"], spanning["rule"], spanning["planned"]),
                         ("supporting", "refute_by_default", "required"))
        records = [r for task in sorted((self.paths.tend_root("chl-1") / "gate" / "tasks")
                                        .glob("*.json"))
                   for d in read_json(task)["documents"] for r in d["records"]]
        lead = next(r for r in records if r.get("declaration") == "Widget.exists_spanning_gadget")
        self.assertEqual((lead["claims"], lead["clauses"]), (LEAD_CLAIMS, ["c1"]))
        gap = next(r for r in records if r.get("declaration") == "Widget.Spanning")
        self.assertEqual(gap["note"], self.GAP)

    def test_the_challengers_claims_and_clauses_sit_beside_the_workers(self):
        theirs = [{"id": "c1", "text": "finite widgets have spanning gadgets"},
                  {"id": "c2", "text": "it is the subject's main result"}]

        def verdict(rec):
            v = F.default_verdict(rec)
            if rec.get("declaration") == "Widget.exists_spanning_gadget":
                v.update(action="downgrade", relation="hasPart", clauses=["c1"])
            return v

        lead_item = next(p["item"] for d in self.docs.values() for p in d["passages"]
                         if p.get("claims"))
        self.challenge(verdict=verdict, claims={lead_item: theirs})
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        row = self.subject(built)
        self.assertEqual((row["claims"], row["challenge_claims"]), (LEAD_CLAIMS, theirs))
        loc = next(l for l in row["locations"] if l["declaration"] ==
                   "Widget.exists_spanning_gadget")
        self.assertEqual((loc["relation"], loc["clauses"], loc["challenge_clauses"]),
                         ("hasPart", ["c1"], ["c1"]))
        self.assertEqual(outcomes.outcome_row(row)["challenge_claims"], theirs)


# --------------------------------------------------------------------------- #
# the prompts say each rule the gate holds
# --------------------------------------------------------------------------- #

class TestThePrompts(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from locate import inputs, plan as lplan, presets as lp, prompts as lprompts

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

    def worker(self, mode=vocab.LOCATE) -> str:
        return self.rendered[mode]["worker.md"]

    def test_fm1_the_worker_writes_the_claim_list_first(self):
        worker = self.worker()
        self.assertIn("**write its claim list first**", worker)
        self.assertIn('{"id": "c1", "text": "<the clause, paraphrased in a few words>"}', worker)
        self.assertIn("never more than twelve consecutive words of the article", worker)
        self.assertIn("use one only when its `clauses` name every claim", worker)
        self.assertIn('"clauses": ["c1", "c2"]', worker)
        self.assertIn("their `clauses` together name every claim", worker)
        system = self.rendered[vocab.LOCATE]["worker-system.md"]
        self.assertIn("a located or absent passage without its claim list (`claims`)", system)

    def test_fm1_the_challenger_lists_the_clauses_itself(self):
        worker = self.worker(vocab.LOCATE_CHALLENGE)
        self.assertIn("**First, write the claim list of each passage you judge**", worker)
        self.assertIn("the worker may have listed too few clauses", worker)
        self.assertIn("stand only when they name every claim", worker)
        self.assertIn(' "claims": {"<item>": [', worker)
        self.assertNotRegex(worker, r"\{\{")


if __name__ == "__main__":
    unittest.main()
