"""Judgment rules 4 at the gate: the smoke-4 audit's four mechanical checks.

New code, for references/vocabulary.md section 1.11 ("Judgment rules 4") and the
maintainer's calls of 2026-10-27 and 2026-10-28.  Each check binds only a gate.json
that names judgment rules 4, so each test gates its document both ways: under rules
4, and under rules 3, which gate it as they always did.

* PD6's shape tripwire: on an ``existence_uniqueness`` or ``iff`` statement, a bundle
  part whose declaration concludes with an iff, an equivalence, a unique existence or
  a bijection writes ``pd6``, and a challenger's confirm of the bundle ``part_limits``;
* the readings a confirm credits a bundle's parts with (Fix 6d): ``part_conventions``
  for every part its ``part_relations`` credit with ``closeMatch``;
* a part's ``conjunct_relation`` may say ``hasPart``, which owes a ``step`` and no
  ``tests`` and on which no bundle stands;
* a challenge downgrade of a strong record to ``relatedMatch`` names its ``bridge``,
  a lemma in the corpus, or says why the two are ``incomparable``.

Every passage, declaration and phrase below is made up (the "Widget theory" fixture
of ``tests/test_locate_gate_statement_cut.py``).
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

import test_locate_gate_statement_cut as S  # noqa: E402
from cflib import vocab  # noqa: E402
from gate import verify_locate as vl  # noqa: E402

R3, R4 = vocab.RULES_STATEMENT_CUT, vocab.RULES_SMOKE4
#: a characterisation of the passage's witness: the conclusion an iff
IFF = "∀ (w : Widget) (g : Gadget), w.Finite → (g.Spans w ↔ g = w.canonicalGadget)"
EQUIV = "(w : Widget) → w.Finite → Gadget w ≃ Unit"
UNIQUE = "∀ (w : Widget), w.Finite → ∃! g, g.Spans w"
BIJECTIVE = "∀ (w : Widget), w.Finite → Function.Bijective (Gadget.size w)"
PLAIN = "∀ (w : Widget) (g : Gadget), w.Finite → g.Spans w → g = w.canonicalGadget"
#: an iff in a hypothesis is no conclusion
IN_A_BINDER = "∀ (w : Widget) (h : w.Finite ↔ w.Small), ∃ g, g.Spans w"
PD6_LINE = "it states uniqueness only: existence needs the gadget to be built first"


class TheConstants(unittest.TestCase):

    def test_the_gates_copies_are_cflibs(self):
        self.assertEqual(vl.RULES_SMOKE4, vocab.RULES_SMOKE4)
        self.assertEqual(vl.KNOWN_JUDGMENT_RULES, vocab.RULES_SMOKE4)
        self.assertEqual(vl.CONJUNCT_RELATIONS_PART, vocab.CONJUNCT_RELATIONS_PART)
        self.assertEqual(vl.PD6_SHAPES, vocab.PD6_SHAPES)
        self.assertEqual(vl.PD6_CONCLUSION_MARKS, vocab.PD6_CONCLUSION_MARKS)
        self.assertEqual(vocab.conjunct_relations(R3), vocab.CONJUNCT_RELATIONS)
        self.assertEqual(vocab.conjunct_relations(R4), vocab.CONJUNCT_RELATIONS | {"hasPart"})
        self.assertNotIn("hasPart", vocab.BUNDLE_RELATIONS)
        self.assertIn("pd6", vocab.JUDGMENT_LOCATION_FIELDS)

    def test_the_conclusion_of_a_signature(self):
        for signature, tail in (
                (IFF, "(g.Spans w ↔ g = w.canonicalGadget)"),
                (EQUIV, "Gadget w ≃ Unit"),
                (UNIQUE, "∃! g, g.Spans w"),
                (BIJECTIVE, "Function.Bijective (Gadget.size w)"),
                (PLAIN, "g = w.canonicalGadget"),
                (IN_A_BINDER, "∃ g, g.Spans w"),
                ("∀ (w : Widget", ""), (None, "")):
            with self.subTest(signature=signature):
                self.assertEqual(vl.conclusion(signature), tail)
        # the binder groups are what they were
        self.assertEqual(vl.binder_groups(IN_A_BINDER), ["(w : Widget)", "(h : w.Finite ↔ w.Small)"])


class Shaped(S.LocateCase):
    """The compound theorem, its shape set; part 2 the beta lemma, whose signature varies."""

    SHAPE = "existence_uniqueness"

    def setUp(self):
        super().setUp()
        self.fx.manifest(self.TASK, S.cut_manifest_doc(self.fx, thm={"shape": self.SHAPE}))

    def assertRules4Only(self, body: dict, needle: str, rule: int = 9):
        self.assertRule(self.gate(body, R4), rule, needle)
        self.assertFalse([p for p in self.gate(body, R3)["problems"] if needle in p])


class TestPD6sShapeTripwire(Shaped):

    def test_a_part_that_concludes_with_an_iff_says_what_it_does_not_state(self):
        S.sign(self.fx, "Beta.Widget.gadget_of_finite", IFF)
        self.assertRules4Only(S.bundle_body(),
                              f"concludes with '↔' on a statement of shape {self.SHAPE}")
        self.assertOk(self.gate(S.bundle_body(), R3))
        body = S.bundle_body()
        body["passages"][0]["locations"][1]["pd6"] = PD6_LINE
        self.assertOk(self.gate(body, R4))

    def test_every_mark(self):
        for signature, mark in ((EQUIV, "≃"), (UNIQUE, "∃!"), (BIJECTIVE, "Bijective")):
            S.sign(self.fx, "Beta.Widget.gadget_of_finite", signature)
            with self.subTest(mark=mark):
                self.assertRule(self.gate(S.bundle_body(), R4), 9, f"concludes with {mark!r}")

    def test_a_conclusion_with_no_mark_is_not_held(self):
        for signature in (PLAIN, IN_A_BINDER, None):
            S.sign(self.fx, "Beta.Widget.gadget_of_finite", signature)
            with self.subTest(signature=signature):
                self.assertOk(self.gate(S.bundle_body(), R4))

    def test_a_pd6_line_is_a_line_of_its_own(self):
        S.sign(self.fx, "Beta.Widget.gadget_of_finite", IFF)
        body = S.bundle_body()
        body["passages"][0]["locations"][1]["pd6"] = "n/a"
        self.assertRule(self.gate(body, R4), 9, "pd6 'n/a' says nothing")

    def test_pd6_belongs_to_a_part(self):
        body = S.cut_locate_body()
        body["passages"][0]["locations"][0]["pd6"] = PD6_LINE
        self.assertRules4Only(body, "pd6 belongs to a hasPart")


class TestTheTripwireOnAnIff(TestPD6sShapeTripwire):
    SHAPE = "iff"


class TestOtherShapesAreNotHeld(Shaped):
    SHAPE = "cases"

    def test_a_cases_bundle_whose_part_is_an_iff(self):
        S.sign(self.fx, "Beta.Widget.gadget_of_finite", IFF)
        self.assertOk(self.gate(S.bundle_body(), R4))


class TestAPartThatStatesPartOfItsConjunct(S.LocateCase):
    """Judgment rules 4: ``conjunct_relation: hasPart``, an honest label for half a conjunct."""

    def body(self, **second) -> dict:
        body = S.bundle_body()
        del body["passages"][0]["coverage"]
        loc = body["passages"][0]["locations"][1]
        loc["conjunct_relation"] = "hasPart"
        for key in ("tests", "instantiation"):
            loc.pop(key)
        loc.update(second)
        return body

    def test_it_owes_a_step_and_no_tests(self):
        self.assertOk(self.gate(self.body(), R4))
        self.assertRule(self.gate(self.body(), R3), 9, "conjunct_relation, one of broadMatch, "
                                                       "closeMatch, exactMatch, narrowMatch, judged")
        body = self.body()
        del body["passages"][0]["locations"][1]["step"]
        self.assertRule(self.gate(body, R4), 9, "step")

    def test_it_names_no_convention(self):
        self.assertRule(self.gate(self.body(convention="encoding"), R4), 9,
                        "convention belongs to a closeMatch")

    def test_no_bundle_stands_on_it(self):
        body = self.body()
        body["passages"][0]["coverage"] = {"complete": True, "parts": [1, 2],
                                           "note": "existence and half the bound together"}
        self.assertRule(self.gate(body, R4), 9, "bears 'hasPart' to its conjuncts")

    def test_and_none_is_owed_without_it(self):
        """Fix 4a's unwritten bundle: a strong part beside a hasPart part owes none."""
        self.assertFalse([p for p in self.gate(self.body(), R4)["problems"]
                          if "write the bundle" in p])

    def test_the_refusal_names_the_rules_relations(self):
        body = self.body(conjunct_relation="relatedMatch")
        self.assertRule(self.gate(body, R4), 9, "one of broadMatch, closeMatch, exactMatch, "
                                                "hasPart, narrowMatch")
        self.assertRule(self.gate(body, R3), 9, "one of broadMatch, closeMatch, exactMatch, "
                                                "narrowMatch, judged against those conjuncts by "
                                                "R1-R3 and PD1-PD10")


# --------------------------------------------------------------------------- #
# the challenge
# --------------------------------------------------------------------------- #

class Challenge(S.ChallengeCase):

    SHAPE = "conclusions"
    #: the second part's own conjunct relation (strong: the record is held strong)
    CONJUNCT_B = "broadMatch"

    def thm(self, **extra) -> dict:
        return super().thm(**{"shape": self.SHAPE, **extra})

    def records(self, *extra: dict) -> None:
        """The statement-cut fixture's records, with the second part's conjunct relation."""
        S.ChallengeCase.records(self, *extra)
        if self.CONJUNCT_B == "broadMatch":
            return
        path = self.fx.tasks / f"{self.TASK}.json"
        task = json.loads(path.read_text(encoding="utf-8"))
        (doc,) = task["documents"]
        for rec in doc["records"]:
            if rec.get("location_key") == S.KEY_B:
                rec["conjunct_relation"] = self.CONJUNCT_B
            for part in rec.get("parts") or []:
                if part["location_key"] == S.KEY_B:
                    part["conjunct_relation"] = self.CONJUNCT_B
        self.fx.manifest(self.TASK, doc)

    def verdict(self, body: dict, key: str) -> dict:
        return next(v for v in body["verdicts"] if v.get("location_key") == key
                    or (key == "coverage" and v["target"] == "coverage"))

    def assertRules4Only(self, body: dict, needle: str, rule: int = 9):
        self.assertRule(self.gate(body, R4), rule, needle)
        self.assertFalse([p for p in self.gate(body, R3)["problems"] if needle in p])


class TestABridge(Challenge):
    """A downgrade of a strong record to relatedMatch names its bridge, or incomparable."""

    def body(self, **fields) -> dict:
        body = self.body_of()
        down = self.verdict(body, S.KEY_B)
        down.update(action="downgrade", relation="relatedMatch", clauses=[],
                    witness="reaching the bound needs a second lemma", **fields)
        down.pop("tests")
        cover = self.verdict(body, "coverage")
        cover.clear()
        cover.update(item=vl_item(), target="coverage", action="refute", unstated=["c2"],
                     part_reasons={S.KEY_A: "it states existence, which no convention moves",
                                   S.KEY_B: "it reaches the bound only through another lemma"},
                     witness="no part states the bound on its own")
        return body

    def body_of(self) -> dict:
        return S.ChallengeCase.body(self)

    def test_a_downgrade_names_one_of_them(self):
        self.assertOk(self.gate(self.body(), R3))
        self.assertRules4Only(self.body(), "says why, by exactly one of bridge")
        bridge = {"library": "alpha", "declaration": "Widget.Spanning",
                  "why": "the bound follows only once spanning is unfolded by this lemma"}
        self.assertOk(self.gate(self.body(bridge=bridge), R4))
        self.assertOk(self.gate(self.body(
            incomparable="the lemma is about infinite widgets the passage never has"), R4))
        self.assertRule(self.gate(self.body(bridge=bridge, incomparable="the settings differ "
                                                                        "in kind entirely"), R4),
                        9, "exactly one of bridge")

    def test_the_bridge_is_a_declaration_of_the_corpus(self):
        phantom = {"library": "alpha", "declaration": "Widget.no_such_bridge",
                   "why": "the bound follows only through this lemma"}
        self.assertRule(self.gate(self.body(bridge=phantom), R4), 3, "a phantom declaration")
        elsewhere = {**phantom, "library": "gamma"}
        self.assertRule(self.gate(self.body(bridge=elsewhere), R4), 3, "not in universe")
        own = {"library": "beta", "declaration": "Beta.Widget.gadget_of_finite",
               "why": "the bound follows only through this lemma"}
        self.assertRule(self.gate(self.body(bridge=own), R4), 9, "the record's own declaration")
        for bad, needle in (({"library": "alpha", "declaration": "Widget.Spanning"},
                             "bridge is {library, declaration, why}"),
                            ({"library": "alpha", "declaration": "Widget.Spanning", "why": "-"},
                             "bridge.why '-' says nothing")):
            with self.subTest(bad=bad):
                self.assertRule(self.gate(self.body(bridge=bad), R4), 9, needle)

    def test_only_a_downgrade_to_related_match_carries_one(self):
        body = self.body_of()
        self.verdict(body, S.KEY_A)["incomparable"] = "the settings differ in kind entirely"
        self.assertRules4Only(body, "bridge and incomparable belong to a downgrade")



class TestAWeakRecordsDowngrade(TestABridge):
    """A part whose conjunct relation is a special case is no strong record: no bridge owed."""

    CONJUNCT_B = "narrowMatch"

    def test_a_downgrade_names_one_of_them(self):
        self.assertOk(self.gate(self.body(), R4))

    def test_the_bridge_is_a_declaration_of_the_corpus(self):
        self.assertRule(self.gate(self.body(incomparable="the settings differ in kind "
                                                         "entirely"), R4),
                        9, "bridge and incomparable belong to a downgrade")


def vl_item() -> str:
    return S.G.THM_ITEM


class TestTheReadingsAConfirmCredits(Challenge):
    """Fix 6d's mechanical half: a closeMatch credit names the convention it reads the part by."""

    def body(self, **cover) -> dict:
        body = S.ChallengeCase.body(self)
        self.verdict(body, "coverage").update(cover)
        return body

    def test_a_close_credit_names_its_convention(self):
        credit = {"part_relations": {S.KEY_A: "closeMatch", S.KEY_B: "broadMatch"}}
        self.assertOk(self.gate(self.body(**credit), R3))
        self.assertRules4Only(self.body(**credit), "part_conventions[")
        self.assertOk(self.gate(self.body(**credit, part_conventions={S.KEY_A: "encoding"}), R4))
        self.assertRule(self.gate(self.body(**credit, part_conventions={S.KEY_A: "junk_value"}),
                                  R4), 9, "the R1 convention you read it by")

    def test_and_only_a_close_credit(self):
        self.assertRules4Only(self.body(part_conventions={S.KEY_B: "encoding"}),
                              "part_conventions belongs to a part your part_relations credit")
        self.assertRules4Only(self.body(part_conventions={"d" * 64: "encoding"}),
                              "which is no part of the claim")
        self.assertOk(self.gate(self.body(), R4))


class TestPartLimitsOnAConfirm(Challenge):
    """PD6's shape tripwire, as the challenger confirms the bundle."""

    SHAPE = "existence_uniqueness"

    def body(self, **cover) -> dict:
        body = S.ChallengeCase.body(self)
        self.verdict(body, "coverage").update(cover)
        return body

    def test_a_confirm_says_what_the_tripped_part_does_not_state(self):
        S.sign(self.fx, "Beta.Widget.gadget_of_finite", IFF)
        self.assertOk(self.gate(self.body(), R3))
        self.assertRules4Only(self.body(), "part_limits[")
        self.assertOk(self.gate(self.body(part_limits={S.KEY_B: PD6_LINE}), R4))
        self.assertRule(self.gate(self.body(part_limits={S.KEY_B: "n/a"}), R4), 9,
                        "says nothing")

    def test_only_for_a_tripped_part(self):
        S.sign(self.fx, "Beta.Widget.gadget_of_finite", IFF)
        self.assertRules4Only(self.body(part_limits={S.KEY_B: PD6_LINE, S.KEY_A: PD6_LINE}),
                              "a line it does not owe")
        S.sign(self.fx, "Beta.Widget.gadget_of_finite", PLAIN)
        self.assertOk(self.gate(self.body(), R4))

    def test_a_refute_owes_none(self):
        S.sign(self.fx, "Beta.Widget.gadget_of_finite", IFF)
        body = self.body()
        self.verdict(body, "coverage").clear()
        self.verdict_of_coverage_refute(body)
        self.assertFalse([p for p in self.gate(body, R4)["problems"] if "part_limits" in p])

    def verdict_of_coverage_refute(self, body: dict) -> None:
        cover = next(v for v in body["verdicts"] if not v)
        cover.update(item=vl_item(), target="coverage", action="refute", unstated=["c1"],
                     part_reasons={S.KEY_A: "it states existence, which no convention moves",
                                   S.KEY_B: "its iff names both existence and uniqueness"},
                     witness="part two alone states both conjuncts")


class TestPartLimitsOnOtherShapes(Challenge):
    SHAPE = "conclusions"

    def test_are_refused(self):
        S.sign(self.fx, "Beta.Widget.gadget_of_finite", IFF)
        body = S.ChallengeCase.body(self)
        self.verdict(body, "coverage")["part_limits"] = {S.KEY_B: PD6_LINE}
        self.assertRules4Only(body, "part_limits belongs to a bundle of a statement of shape "
                                    "existence_uniqueness or iff")
        del self.verdict(body, "coverage")["part_limits"]
        self.assertOk(self.gate(body, R4))


class TestAVerdictsHasPartConjunctRelation(Challenge):

    def test_a_verdict_may_say_a_part_states_part_of_its_conjunct(self):
        body = S.ChallengeCase.body(self)
        down = self.verdict(body, S.KEY_B)
        down.update(action="downgrade", relation="hasPart", conjunct_relation="hasPart",
                    step="half of the size bound, for prime orders only")
        down.pop("tests")
        cover = self.verdict(body, "coverage")
        cover.clear()
        cover.update(item=vl_item(), target="coverage", action="refute", unstated=["c2"],
                     part_reasons={S.KEY_A: "it states existence, which no convention moves",
                                   S.KEY_B: "it states half of the bound only"},
                     witness="no part states the whole bound")
        # a downgrade to hasPart of a hasPart is no weaker relation: confirm with it instead
        down["action"] = "confirm"
        down.pop("relation")
        self.assertOk(self.gate(body, R4))
        self.assertRule(self.gate(body, R3), 9, "conjunct_relation is one of")

    def test_a_confirm_credits_it_no_strong_relation(self):
        body = S.ChallengeCase.body(self)
        part = self.verdict(body, S.KEY_B)
        part.update(conjunct_relation="hasPart", step="half of the size bound only")
        part.pop("tests")
        self.assertRule(self.gate(body, R4), 9, "is stronger than the conjunct_relation hasPart")


# --------------------------------------------------------------------------- #
# what the outcomes keep of the four
# --------------------------------------------------------------------------- #

class TestTheOutcomesKeepThem(unittest.TestCase):
    """The rules-4 fields on the rows of a run of status rule 2 or 3, and none on rule 1's."""

    @staticmethod
    def challenge(row, verdicts, status_rule=3):
        from test_locate_fm_fixes import _challenge
        return _challenge(row, verdicts, status_rule=status_rule)

    @staticmethod
    def row(**extra):
        from test_locate_fm_fixes import _row
        return _row(**extra)

    def test_a_downgrades_bridge_rides_on_the_related_match_it_leaves(self):
        from locate import outcomes
        bridge = {"library": "alpha", "declaration": "W.link", "why": "the lemma links them"}
        row = self.row(coverage=None)
        got, _ = self.challenge(row, {"b" * 64: {"action": "downgrade",
                                                 "relation": "relatedMatch", "bridge": bridge}})
        (moved,) = [l for l in got["locations"] if l["location_key"] == "b" * 64]
        self.assertEqual((moved["relation"], moved["bridge"]), ("relatedMatch", bridge))
        self.assertNotIn("conjunct_relation", moved)
        out = outcomes.outcome_row(got)
        self.assertIn("bridge", [l for l in out["locations"]
                                 if l["location_key"] == "b" * 64][0])
        # a bridge on a verdict that leaves anything else is not kept
        got, _ = self.challenge(self.row(coverage=None), {"b" * 64: {
            "action": "confirm", "incomparable": "a line that does not belong here"}})
        self.assertNotIn("incomparable", got["locations"][1])

    def test_a_parts_pd6_line_is_kept_while_it_is_a_part(self):
        from locate import outcomes
        self.assertIn("pd6", outcomes.CUT_LOCATION_FIELDS)
        self.assertIn("pd6", outcomes._location_fields(3))
        self.assertNotIn("pd6", outcomes._location_fields(1))
        loc = {"relation": "broadMatch", "pd6": PD6_LINE}
        outcomes._fit_fields(loc)
        self.assertNotIn("pd6", loc)
        loc = {"relation": "hasPart", "pd6": PD6_LINE}
        outcomes._fit_fields(loc)
        self.assertEqual(loc["pd6"], PD6_LINE)

    def test_a_confirms_readings_and_limits_ride_beside_the_claim(self):
        from locate import outcomes
        verdict = {"action": "confirm", "part_relations": {"a" * 64: "closeMatch",
                                                           "b" * 64: "broadMatch"},
                   "part_conventions": {"a" * 64: "encoding"},
                   "part_limits": {"b" * 64: PD6_LINE}}
        got, problems = self.challenge(self.row(), {"coverage": verdict})
        self.assertEqual(got["coverage_part_conventions"], {"a" * 64: "encoding"})
        self.assertEqual(got["coverage_part_limits"], {"b" * 64: PD6_LINE})
        out = outcomes.outcome_row(got)
        self.assertEqual(out["coverage_part_limits"], {"b" * 64: PD6_LINE})
        got, _ = self.challenge(self.row(), {"coverage": verdict}, status_rule=1)
        self.assertNotIn("coverage_part_limits", got)

    def test_a_has_part_conjunct_relation_lapses_its_bundle_by_name(self):
        from locate import outcomes
        got, problems = self.challenge(self.row(), {
            "coverage": {"action": "confirm"},
            "b" * 64: {"action": "confirm", "conjunct_relation": "hasPart"}})
        self.assertTrue(got.get("coverage_lapsed"))
        self.assertTrue(any("states only part of its conjuncts" in p for p in problems),
                        problems)
        self.assertEqual(outcomes.bundle_relation(
            [{"location_key": "k", "conjunct_relation": "hasPart"}]), "hasPart")
        self.assertEqual(got["status"], "partial")


# --------------------------------------------------------------------------- #
# the planner: judgment rules 4 and status rule 3 from the flip of the calls
# --------------------------------------------------------------------------- #

class TestThePlannerWritesRules4(unittest.TestCase):
    """A run planned now names judgment rules 4 and, a locate run, status rule 3; one planned
    before the flip of the calls (``F.before_the_smoke4_calls``, as smoke 4's runs were)
    names 3 and 2, and a challenge planned now of it is admitted under rules 4."""

    setUp = S.TestThePlanner.setUp
    plan = S.TestThePlanner.plan

    def cut_inventory(self, run_id: str) -> None:
        code, out, err = self.plan("--preset", "locate-inventory", "--run-id", run_id)
        self.assertEqual(code, 0, out + err)
        docs = S.F.inventory_documents(self.paths, run_id)
        theorem = docs[f"inv-{S.F.W1}/{S.F.W1}.json"]["passages"][1]
        theorem.update(conjuncts=[{"id": "c1", "text": "the gadget exists"},
                                  {"id": "c2", "text": "its size divides the order"}],
                       shape="conclusions")
        S.F.write_harvest(self.paths, run_id, docs)

    def test_a_run_planned_now(self):
        from cflib.jsonio import read_json
        from cflib.yamlio import read_yaml
        self.cut_inventory("inv-5")
        code, out, err = self.plan("--preset", "locate", "--inventory-run", "inv-5",
                                   "--run-id", "loc-5")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_json(self.paths.gate_json("loc-5"))["judgment_rules"], R4)
        self.assertEqual(read_yaml(self.paths.run_yaml("loc-5"))["locate"]["status_rule"],
                         vocab.STATUS_RULE_BROAD)

    def test_a_run_planned_before_and_its_challenge_planned_now(self):
        from cflib.jsonio import read_json
        from cflib.yamlio import read_yaml
        with S.F.before_the_smoke4_calls():
            self.cut_inventory("inv-6")
            code, out, err = self.plan("--preset", "locate", "--inventory-run", "inv-6",
                                       "--run-id", "loc-6")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_json(self.paths.gate_json("loc-6"))["judgment_rules"], R3)
        self.assertEqual(read_yaml(self.paths.run_yaml("loc-6"))["locate"]["status_rule"],
                         vocab.STATUS_RULE_BUNDLE)
        S.F.write_harvest(self.paths, "loc-6", S.F.locate_documents(self.paths, "loc-6"))
        code, out, err = self.plan("--preset", "locate-challenge", "--challenge-of", "loc-6",
                                   "--run-id", "chl-6", "--sample-rate", "1")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_json(self.paths.gate_json("chl-6"))["judgment_rules"], R4)
        # the locate run keeps its own status rule: a challenge never re-plans it
        self.assertEqual(read_yaml(self.paths.run_yaml("loc-6"))["locate"]["status_rule"],
                         vocab.STATUS_RULE_BUNDLE)


if __name__ == "__main__":
    unittest.main()
