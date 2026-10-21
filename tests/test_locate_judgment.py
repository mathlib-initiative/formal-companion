"""The locate judgment rules: gate rule 9, the documents' new fields and their prompts.

New code, for references/vocabulary.md section 1.11 (rules R1-R7 and the policy
defaults) and references/locate-documents.md sections 3, 4 and 10, from the
smoke-2 audit's failure modes (references/vocabulary.md section 1.11 names
them): a whole-passage relation for a declaration that states some clauses
(FM1), an absence whose queries missed a clause (FM5), a declaration whose
hypotheses exclude the passage's objects (FM6), a proof ingredient credited as a
part (FM3), a closeMatch for what is no convention (FM4) and a challenger's
reasoning slip (FM7).  The gate's artifacts are test_verify_locate's synthetic ones (the
"Widget lemma" layer, libraries ``alpha`` and ``beta``); every claim, note and
query below is made up.  Rule 9 binds a run whose ``gate.json`` names
``judgment_rules`` and no other, so every document a run planned before the
rules wrote is gated as it was: each test that refuses a document checks that.
Version 2 of the rules (the maintainer's PD5-PD9 of 2026-10-20) adds two checks,
PD6's whole part and PD7's junk value, and each of their tests gates the same
document under version 1 too.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import json
import re
import sqlite3
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
#: a part's step: what the declaration alone entails of the passage (FM3)
STEP = "the existence of a spanning gadget for the widget"


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
    lead["receipt"]["query_clauses"] = {q: ["c1"] for q in lead["receipt"]["queries"]}
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

    def test_claims_split_over_two_are_the_source_still(self):
        words = G.LEAD.split()
        body = judged_body()
        body["passages"][1]["claims"] = [{"id": "c1", "text": " ".join(words[:12])},
                                         {"id": "c2", "text": " ".join(words[12:24])}]
        body["passages"][1]["receipt"]["query_clauses"] = {
            "widget lemma": ["c1"], "finite widget spanning": ["c2"], "Widget.spanning": ["c1"]}
        self.assertRule(self.run_body(body), 1, "the claims, read one after the other,")

    def test_a_format_character_hides_no_word(self):
        body = judged_body()
        body["passages"][0]["claims"][0]["text"] = "\u200b ".join(G.THEOREM.split()[:15])
        self.assertRule(self.run_body(body), 1, "as the reading copy or the layer spells it")

    def test_the_reading_copys_spelling_is_the_source_too(self):
        """text.txt writes a formula as $G$, the layer as G{\\displaystyle G}."""
        copied = "Let $G$ be a finite widget of order $n$; then $G$ has a spanning gadget"
        self.assertIsNone(vl.lr.verbatim_run(copied, G.LAYER))
        body = judged_body()
        body["passages"][0]["claims"][0]["text"] = copied
        self.assertRule(self.run_body(body), 1, "as the reading copy or the layer spells it")
        # an exported note is held to it under the rules only: a run planned before them
        # exports what it did
        body = judged_body()
        body["passages"][0]["locations"][0]["note"] = copied
        self.assertRefusedOnlyUnderTheRules(body, "as the reading copy or the layer spells it",
                                            rule=1)

    def test_a_note_copied_from_the_layer_is_refused_once(self):
        """Rule 1's own check names a note that copies the layer; the reading-copy check
        adds no second refusal of it."""
        body = judged_body()
        body["passages"][0]["locations"][0]["note"] = " ".join(G.LEAD.split()[:14])
        result = self.run_body(body)
        self.assertEqual(sum("verbatim" in p for p in result["problems"]), 1,
                         result["problems"])

    def test_a_blank_instantiation_is_none(self):
        sign(self.fx, "Widget.exists_spanning_gadget", GENERIC)
        body = judged_body()
        body["passages"][0]["locations"][0]["instantiation"] = "   "
        self.assertRule(self.run_body(body), 9, "takes instance argument(s)")

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
            if ok:
                loc["step"] = STEP
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
            body["passages"][0]["locations"][0].update(relation="hasPart", clauses=["c1"],
                                                       step=STEP)
            self.assertOk(self.run_body(body))

    def test_a_gap_admitted_in_the_instantiation_is_a_gap_too(self):
        body = judged_body()
        body["passages"][0]["locations"][0]["instantiation"] = (
            "W is the passage's widget; the divisibility clause is not stated")
        self.assertRefusedOnlyUnderTheRules(body, "the instantiation says 'not stated'")
        body["passages"][0]["locations"][0]["instantiation"] = "W is the passage's widget G"
        self.assertOk(self.run_body(body))

    def test_a_gap_note_is_refused_with_the_way_out(self):
        body = judged_body()
        body["passages"][0]["locations"][0]["note"] = "It does not state the divisibility."
        result = self.run_body(body)
        self.assertRule(result, 9, "then the relation is hasPart (R4), not exactMatch")
        self.assertRule(result, 9, "say what the declaration does")

    def test_a_coverage_claims_parts_state_every_clause(self):
        body = judged_body()
        thm = body["passages"][0]
        thm["locations"] = [judged(G.location(relation="hasPart"), ["c1"], step=STEP),
                            judged(G.location(decl="Widget.Spanning", relation="hasPart",
                                              rank=2), ["c1"], step="the spanning property of "
                                                                   "the gadget")]
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


# --------------------------------------------------------------------------- #
# FM5: an absence searched for every clause
# --------------------------------------------------------------------------- #

class TestAnAbsenceSearchedEveryClause(JudgedLocate):

    def absent_body(self, claims, query_clauses) -> dict:
        body = judged_body()
        lead = body["passages"][1]
        lead["claims"] = claims
        if query_clauses is None:
            lead["receipt"].pop("query_clauses", None)
        else:
            lead["receipt"]["query_clauses"] = query_clauses
        return body

    def test_an_absence_tags_its_queries(self):
        self.assertRefusedOnlyUnderTheRules(self.absent_body(LEAD_CLAIMS, None),
                                            "tags each query with the claims it targets")

    def test_no_claim_is_left_without_a_query(self):
        two = LEAD_CLAIMS + [{"id": "c2", "text": "the gadget is unique up to conjugacy"}]
        body = self.absent_body(two, {"widget lemma": ["c1"], "finite widget spanning": ["c1"]})
        self.assertRefusedOnlyUnderTheRules(body, "no query targets c2")
        body["passages"][1]["receipt"]["query_clauses"]["Widget.spanning"] = ["c2"]
        self.assertOk(self.run_body(body))

    def test_a_tag_names_a_query_and_a_claim(self):
        body = self.absent_body(LEAD_CLAIMS, {"widget lemma": ["c1"], "a query never run": ["c1"]})
        self.assertRule(self.run_body(body), 9, "not one of receipt.queries")
        body = self.absent_body(LEAD_CLAIMS, {"widget lemma": ["c7"]})
        self.assertRule(self.run_body(body), 9, "c7 are neither claim ids")
        body = self.absent_body(LEAD_CLAIMS, {"widget lemma": []})
        self.assertRule(self.run_body(body), 9, "non-empty list")
        body = self.absent_body(LEAD_CLAIMS, [["widget lemma", "c1"]])
        self.assertRule(self.run_body(body), 9, "receipt.query_clauses is {<query>")

    def test_a_worked_examples_absence_searches_for_its_general_theorem(self):
        doc = G.locate_manifest_doc(self.fx)
        doc["passages"][0]["passage_kind"] = "example"
        self.fx.manifest(self.TASK, doc)
        body = judged_body()
        self.assertRefusedOnlyUnderTheRules(body, "a query tagged general")
        body["passages"][1]["receipt"]["query_clauses"]["Widget.spanning"] = ["general"]
        self.assertOk(self.run_body(body))
        # the general theorem's query does not stand in for a clause's
        body["passages"][1]["receipt"]["query_clauses"] = {"Widget.spanning": ["general"]}
        self.assertRule(self.run_body(body), 9, "no query targets c1")

    def test_a_located_passages_tags_are_optional_and_checked(self):
        body = judged_body()
        self.assertNotIn("query_clauses", body["passages"][0]["receipt"])
        self.assertOk(self.run_body(body))
        body["passages"][0]["receipt"]["query_clauses"] = {"widget spanning gadget": ["c3"]}
        self.assertRule(self.run_body(body), 9, "c3 are neither claim ids")

    def test_every_query_is_tagged_and_each_claim_has_one_of_its_own(self):
        """One query tagged with every claim, beside untagged others, searched for one."""
        two = LEAD_CLAIMS + [{"id": "c2", "text": "the gadget is unique up to conjugacy"}]
        body = self.absent_body(two, {"widget lemma": ["c1", "c2"]})
        self.assertRefusedOnlyUnderTheRules(body, "carries no tag")
        # every query tagged, but one query stands for two claims
        three = two + [{"id": "c3", "text": "its size divides the order"}]
        body = self.absent_body(three, {"widget lemma": ["c1", "c2", "c3"],
                                        "finite widget spanning": ["c1"],
                                        "Widget.spanning": ["c1"]})
        self.assertRefusedOnlyUnderTheRules(body, "a query of its own")
        body["passages"][1]["receipt"]["query_clauses"].update(
            {"finite widget spanning": ["c2"], "Widget.spanning": ["c3"]})
        self.assertOk(self.run_body(body))
        # two spellings of one query are one query
        body = self.absent_body(three, {"widget lemma": ["c1"], "finite widget spanning": ["c3"],
                                        "Widget.spanning": ["c3"]})
        body["passages"][1]["receipt"]["queries"].append("WIDGET  lemma")
        body["passages"][1]["receipt"]["query_clauses"]["WIDGET  lemma"] = ["c2"]
        self.assertRule(self.run_body(body), 9, "a query of its own")

    def test_a_worked_examples_general_query_is_a_query_of_its_own(self):
        doc = G.locate_manifest_doc(self.fx)
        doc["passages"][0]["passage_kind"] = "example"
        self.fx.manifest(self.TASK, doc)
        body = self.absent_body(LEAD_CLAIMS, {"widget lemma": ["c1", "general"],
                                              "finite widget spanning": ["c1"],
                                              "Widget.spanning": ["c1"]})
        self.assertOk(self.run_body(body))
        # the only query that targets the general theorem is the one that targets c1
        body = self.absent_body(LEAD_CLAIMS, {"widget lemma": ["c1", "general"],
                                              "finite widget spanning": ["general"],
                                              "Widget.spanning": ["general"]})
        self.assertOk(self.run_body(body))
        lone = self.absent_body(LEAD_CLAIMS, {"widget lemma": ["c1", "general"]})
        lone["passages"][1]["receipt"]["queries"] = ["widget lemma", "widget lemma ", "WIDGET lemma"]
        self.assertRule(self.run_body(lone), 5, "distinct")
        self.assertRule(self.run_body(lone), 9, "a query of its own")

    def test_an_absence_rejects_no_candidate_for_stating_a_part(self):
        """The audit's false absences: the stating declaration was rejected as "not the
        worked example" or "not the whole passage"."""
        for reason, phrase in (("not the worked example", "not the worked example"),
                               ("states only part of the passage", "only part"),
                               ("a special case for finite widgets", "special case"),
                               ("more general than the passage", "more general*")):
            body = self.absent_body(LEAD_CLAIMS, {q: ["c1"] for q in (
                "widget lemma", "finite widget spanning", "Widget.spanning")})
            body["passages"][1]["receipt"]["candidates"] = [
                {"library": "beta", "declaration": "Beta.Widget.gadget_of_finite",
                 "verdict": "rejected", "reason": reason}]
            with self.subTest(reason=reason):
                self.assertRefusedOnlyUnderTheRules(body, f"rejected as {phrase!r}")
        body["passages"][1]["receipt"]["candidates"][0]["reason"] = (
            "states the spanning of gadgets, not of widgets")
        self.assertOk(self.run_body(body))


# --------------------------------------------------------------------------- #
# FM6: the declaration instantiated at the passage's objects
# --------------------------------------------------------------------------- #

#: synthetic signatures in the shape the library index prints them
GENERIC = ("\u2200 {W : Type u_1} [inst : WidgetLike W] [Finite W] (w : W), "
           "\u2203 g, Spans g w")
PLAIN = "\u2200 (w : Widget), w.Finite \u2192 \u2203 g, g.Spans w"


def sign(fx, name: str, signature: str | None) -> None:
    """Record ``signature`` for every declaration ``name`` in the fixture's corpus.sqlite."""
    con = sqlite3.connect(fx.db)
    con.execute("UPDATE decls SET signature = ? WHERE name = ?", (signature, name))
    con.commit()
    con.close()
    fx.repin_corpus()                 # the gate holds the database to gate.json's sha


class TestTheInstantiationLine(JudgedLocate):

    def test_a_declaration_with_instance_arguments_says_how_it_applies(self):
        sign(self.fx, "Widget.exists_spanning_gadget", GENERIC)
        body = judged_body()
        self.assertRefusedOnlyUnderTheRules(body, "takes instance argument(s) "
                                                  "[inst : WidgetLike W] [Finite W]")
        body["passages"][0]["locations"][0]["instantiation"] = (
            "W is the passage's widget G; WidgetLike and Finite hold for it")
        self.assertOk(self.run_body(body))

    def test_one_without_them_or_without_a_recorded_signature_needs_none(self):
        for signature in (PLAIN, None):
            sign(self.fx, "Widget.exists_spanning_gadget", signature)
            with self.subTest(signature=signature):
                self.assertOk(self.run_body(judged_body()))

    def test_a_related_match_asserts_nothing_to_instantiate(self):
        sign(self.fx, "Widget.Spanning", GENERIC)
        body = judged_body()
        body["passages"][1]["locations"] = [G.location(decl="Widget.Spanning",
                                                       relation="relatedMatch")]
        self.assertOk(self.run_body(body))

    def test_the_line_is_one_line_in_the_workers_words(self):
        sign(self.fx, "Widget.exists_spanning_gadget", GENERIC)
        body = judged_body()
        loc = body["passages"][0]["locations"][0]
        loc["instantiation"] = "W is the widget\nand Finite holds"
        self.assertRule(self.run_body(body), 9, "one line")
        loc["instantiation"] = " ".join(G.THEOREM.split()[:15])
        self.assertRule(self.run_body(body), 1, "own words")

    def test_the_binder_positions_as_the_index_prints_them(self):
        cases = {
            GENERIC: ["[inst : WidgetLike W]", "[Finite W]"],
            PLAIN: [],
            "{R : Type u} \u2192 [inst : Ring R] \u2192 (f : \u03b9 \u2192 R) \u2192 "
            "Pairwise f \u2192 R": ["[inst : Ring R]"],
            "\u2200 {x n : \u2115}, x.Coprime n \u2192 x ^ n \u2261 1 [MOD n]": [],
            "\u2200 {E : Type u} [inst : Normed E] (T : E \u2192\u2097[\u211d] E), "
            "T.Symm": ["[inst : Normed E]"],
            "\u2200 \u2983a b : \u03b1\u2984 [Fact (Prime p)], a = b": ["[Fact (Prime p)]"],
            # a binder after a binder list, an anonymous argument or a hypothesis is at a
            # binder position too
            "\u2200 (n : \u2115), \u2200 [inst : Later n], n = n": ["[inst : Later n]"],
            "{M : Type u_1} \u2192 Set M \u2192 [inst : WidgetLike M] \u2192 Widget M":
                ["[inst : WidgetLike M]"],
            "\u2200 {p : \u2115}, Nat.Prime p \u2192 \u2200 (W : Type u_1) [inst : WidgetLike W], "
            "\u2203 g, Spans g W": ["[inst : WidgetLike W]"],
            "Type u \u2192 [inst : Ring R] \u2192 R": ["[inst : Ring R]"],
            # the conclusion is no binder position: an existential's body, a statement
            "\u2203 x, P x \u2192 [inst : Foo x] \u2192 Q": [],
            "\u2200 (a : \u03b1), a \u2208 s \u2192 a \u2261 b [ZMOD n]": [],
            "\u2200 {f : \u03b9 \u2192 \u03b1} (h : \u2200 i, f i = 0), [Foo] = [Foo]": [],
            "Widget.Finite w": [], "": [],
        }
        for signature, want in cases.items():
            with self.subTest(signature=signature):
                self.assertEqual(vl.instance_binders(signature), want)
        # an unbalanced group ends the scan, and nothing raises
        self.assertEqual(vl.binder_groups("\u2200 (a : \u03b1) (b"), ["(a : \u03b1)"])
        self.assertEqual(vl.binder_groups("(a : \u03b1 \u2192 (b"), [])

    def test_a_late_instance_binder_needs_the_line_too(self):
        late = ("{M : Type u_1} \u2192 Set M \u2192 [inst : WidgetLike M] \u2192 Widget M")
        sign(self.fx, "Widget.exists_spanning_gadget", late)
        self.assertRefusedOnlyUnderTheRules(judged_body(), "[inst : WidgetLike M]")

    def test_a_placeholder_is_no_instantiation(self):
        sign(self.fx, "Widget.exists_spanning_gadget", GENERIC)
        for line in ("n/a", "-", "none needed"):
            body = judged_body()
            body["passages"][0]["locations"][0]["instantiation"] = line
            with self.subTest(line=line):
                self.assertRule(self.run_body(body), 9, "says nothing")


# --------------------------------------------------------------------------- #
# FM3: a part says what the declaration alone entails of the passage
# --------------------------------------------------------------------------- #

class TestAPartNamesItsStep(JudgedLocate):
    """Every hasPart and narrowMatch, on any passage kind; the audit's FM3 items sat on
    theorem and definition passages as often as on proofs."""

    def kind(self, passage_kind: str) -> None:
        doc = G.locate_manifest_doc(self.fx)
        doc["passages"][1]["passage_kind"] = passage_kind    # the theorem box
        self.fx.manifest(self.TASK, doc)

    def body(self, relation="hasPart", **extra) -> dict:
        body = judged_body()
        body["passages"][0]["locations"][0].update(relation=relation, clauses=["c1"], **extra)
        return body

    def test_a_has_part_or_narrow_match_names_the_step_on_every_kind(self):
        for kind in ("other", "theorem", "definition"):
            self.kind(kind)
            for relation in ("hasPart", "narrowMatch"):
                with self.subTest(kind=kind, relation=relation):
                    needle = ("the displayed step" if kind == "other" else
                              "the clause it states")
                    self.assertRefusedOnlyUnderTheRules(self.body(relation), needle)
                    self.assertOk(self.run_body(self.body(relation, step=STEP)))

    def test_the_step_is_a_line_that_says_something(self):
        self.kind("other")
        for step, needle in (("-", "says nothing"), ("n/a", "says nothing"),
                             ("first\nsecond line here", "one line"),
                             ("first line\rsecond line", "one line"),
                             ("first line\u2028second line", "one line"),
                             (["the", "step"], "not a list")):
            with self.subTest(step=step):
                self.assertRule(self.run_body(self.body(step=step)), 9, needle)

    def test_no_other_relation_carries_a_step(self):
        body = judged_body()                                   # an exactMatch of every claim
        self.assertOk(self.run_body(body))
        body["passages"][0]["locations"][0]["step"] = STEP
        self.assertRefusedOnlyUnderTheRules(body, "step belongs to a hasPart or narrowMatch")
        # a long step on an exactMatch is checked like any step: the source verbatim
        body["passages"][0]["locations"][0]["step"] = " ".join(G.THEOREM.split()[:15])
        self.assertRule(self.run_body(body), 1, "own words")


class TestAChallengersPartNamesItsStep(G.GateTestCase):

    TASK = "t-chl-0001"
    KEY = "9" * 64

    def setUp(self):
        self.fx = G.Fixture(self, "locate-challenge")
        self.fx.gate["judgment_rules"] = vocab.JUDGMENT_RULES
        self.fx.write_gate()

    def records(self, relation, kind="other", **extra):
        doc = G.locate_manifest_doc(self.fx)
        doc.pop("passages")
        doc["records"] = [{"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
                           "span": G.THEOREM_SPAN, "passage_kind": kind,
                           "relation": relation, **extra}]
        self.fx.manifest(self.TASK, doc)

    def run_verdict(self, **verdict) -> dict:
        body = {"verdicts": [{"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
                              "witness": "read at the pin", **verdict}],
                "claims": {G.THM_ITEM: copy.deepcopy(THM_CLAIMS)}}
        return self.fx.run(self.fx.document(self.TASK, body))

    def test_a_downgrade_to_a_part_names_the_step(self):
        for kind in ("other", "theorem"):
            for relation in ("hasPart", "narrowMatch"):
                self.records("exactMatch", kind)
                with self.subTest(kind=kind, relation=relation):
                    self.assertRule(self.run_verdict(action="downgrade", relation=relation,
                                                     clauses=["c1"]), 9, "names, in step")
                    self.assertOk(self.run_verdict(action="downgrade", relation=relation,
                                                   clauses=["c1"], step=STEP))

    def test_a_confirm_names_it_when_the_record_does_not(self):
        self.records("hasPart")
        self.assertRule(self.run_verdict(action="confirm", clauses=["c1"]), 9, "displayed step")
        self.assertOk(self.run_verdict(action="confirm", clauses=["c1"], step=STEP))
        self.records("hasPart", step="the worker's own step line")
        self.assertOk(self.run_verdict(action="confirm", clauses=["c1"]))
        self.records("narrowMatch", "theorem")
        self.assertRule(self.run_verdict(action="confirm", clauses=["c1"]), 9, "names, in step")

    def test_a_verdict_that_leaves_no_part_carries_no_step(self):
        self.records("exactMatch", "theorem")
        self.assertRule(self.run_verdict(action="confirm", clauses=["c1", "c2"], step=STEP), 9,
                        "step belongs to a hasPart or narrowMatch")
        self.assertRule(self.run_verdict(action="downgrade", relation="broadMatch",
                                         clauses=["c1", "c2"], step=STEP), 9,
                        "step belongs to a hasPart or narrowMatch")


# --------------------------------------------------------------------------- #
# FM4: a closeMatch differs by a convention, named from a closed list
# --------------------------------------------------------------------------- #

class TestACloseMatchNamesItsConvention(JudgedLocate):

    def body(self, **loc) -> dict:
        body = judged_body()
        body["passages"][0]["locations"][0].update(relation="closeMatch", **loc)
        return body

    def test_a_close_match_names_a_listed_convention(self):
        note = "the size is written as a cardinal"
        self.assertRefusedOnlyUnderTheRules(self.body(note=note), "names the convention it "
                                                                   "differs by")
        self.assertRule(self.run_body(self.body(note=note, convention="generality")), 9,
                        "bundling, encoding, normalisation, notation, renaming, unfolding "
                        "(convention")
        for convention in sorted(vocab.CLOSE_MATCH_CONVENTIONS):
            with self.subTest(convention=convention):
                self.assertOk(self.run_body(self.body(note=note, convention=convention)))

    #: notes that name generality or strength, each with the phrase that catches it
    GENERAL = (("a strict derivative for the passage's plain one", "strict derivative*"),
               ("Stronger: any Banach codomain", "stronger"),
               ("it is more general than the passage", "more general*"),
               ("Mathlib proves it more generally, for every widget-like type", "more general*"),
               ("it generalises the passage to any widget", "generali*"),
               ("a generalization of the passage", "generali*"),
               ("the passage is a special case of it", "special case"),
               ("a weaker form of the passage", "weaker"),
               ("it strengthens the passage's bound", "strengthen*"),
               ("stated in greater generality", "generali*"),
               ("a broader codomain than the passage's", "broader"),
               ("phrased as strict differentiability", "strict differentiab*"),
               ("it is strictly differentiable at every point", "strictly differentiab*"))
    #: notes that name the mathematics, not a generality: a correct closeMatch says them
    PLAIN = ("written with HasStrictDerivAt's notation",
             "the passage's strict inequality a < b is written b > a",
             "a strictly increasing list of primes encodes the factorisation",
             "the gadget is written as a finite set")

    def test_a_close_match_note_says_no_generality(self):
        for note, phrase in self.GENERAL:
            with self.subTest(note=note):
                self.assertRefusedOnlyUnderTheRules(self.body(note=note, convention="encoding"),
                                                    f"says {phrase!r}")
        for note in self.PLAIN:
            with self.subTest(note=note):
                self.assertOk(self.run_body(self.body(note=note, convention="notation")))

    def test_an_exact_match_note_says_no_generality_either(self):
        """The audit's exactMatch for a general law against a finite table."""
        body = judged_body()
        body["passages"][0]["locations"][0]["note"] = "the law for every prime, more general"
        self.assertRefusedOnlyUnderTheRules(body, "the exactMatch note says 'more general*'")
        body["passages"][0]["locations"][0]["note"] = "the gadget is written as a finite set"
        self.assertOk(self.run_body(body))

    def test_no_other_relation_names_a_convention(self):
        for relation, extra in (("exactMatch", {}), ("broadMatch", {}),
                                ("hasPart", {"clauses": ["c1"], "step": STEP})):
            body = judged_body()
            body["passages"][0]["locations"][0].update(relation=relation, convention="notation",
                                                       **extra)
            with self.subTest(relation=relation):
                self.assertRefusedOnlyUnderTheRules(body, "convention belongs to a closeMatch")
        body["passages"][0]["locations"][0]["convention"] = ""          # blank is none
        self.assertOk(self.run_body(body))

    def test_a_hostile_shape_is_named_not_a_traceback(self):
        """A list or an object where a word belongs is refused by name, and the rest of the
        document is still read (no 'shape could not be checked' backstop)."""
        cases = ({"relation": ["exactMatch"]}, {"convention": ["notation"]},
                 {"convention": {"a": 1}}, {"clauses": {"c1": True}},
                 {"instantiation": {"W": "G"}}, {"step": ["a", "step"]})
        for change in cases:
            body = self.body(note="the gadget is written as a finite set", convention="notation")
            body["passages"][0]["locations"][0].update(change)
            body["passages"][1]["claims"] = "not a list"             # still read and refused
            result = self.run_body(body)
            with self.subTest(change=change):
                self.assertEqual(result["exit"], vl.EXIT_REJECTED, result)
                self.assertFalse(any("could not be checked" in p for p in result["problems"]),
                                 result["problems"])
                self.assertTrue(any("claims is a non-empty list" in p
                                    for p in result["problems"]), result["problems"])

    def test_the_closed_lists_are_cflibs(self):
        self.assertEqual(vl.CLOSE_MATCH_CONVENTIONS, vocab.CLOSE_MATCH_CONVENTIONS)
        self.assertEqual(vl.LEGACY_CLOSE_MATCH_CONVENTIONS, vocab.LEGACY_CLOSE_MATCH_CONVENTIONS)
        self.assertEqual(vl.CONVENTION_JUNK_VALUE, vocab.CONVENTION_JUNK_VALUE)
        self.assertEqual(vl.CLOSE_MATCH_GENERALITY_PHRASES, vocab.CLOSE_MATCH_GENERALITY_PHRASES)
        # PD7 (2026-10-20) took junk values off R1's list; rules 1 keep them
        self.assertEqual(vocab.CLOSE_MATCH_CONVENTIONS,
                         {"notation", "encoding", "normalisation", "renaming", "unfolding",
                          "bundling"})
        self.assertEqual(vocab.LEGACY_CLOSE_MATCH_CONVENTIONS,
                         vocab.CLOSE_MATCH_CONVENTIONS | {"junk_value"})
        for note, phrase in self.GENERAL:
            self.assertEqual((vocab.says_generality(note), vl.says_generality(note)),
                             (phrase, phrase))
        for note in (*self.PLAIN, "a restriction of the codomain", "strict", "strictly"):
            self.assertEqual((vocab.says_generality(note), vl.says_generality(note)),
                             (None, None))


class TestAChallengersCloseMatchNamesItsConvention(G.GateTestCase):

    TASK = "t-chl-0001"
    KEY = "9" * 64

    def setUp(self):
        self.fx = G.Fixture(self, "locate-challenge")
        self.fx.gate["judgment_rules"] = vocab.JUDGMENT_RULES
        self.fx.write_gate()

    def records(self, relation, **extra):
        doc = G.locate_manifest_doc(self.fx)
        doc.pop("passages")
        doc["records"] = [{"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
                           "span": G.THEOREM_SPAN, "passage_kind": "theorem",
                           "relation": relation, **extra}]
        self.fx.manifest(self.TASK, doc)

    def run_verdict(self, **verdict) -> dict:
        body = {"verdicts": [{"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
                              "witness": "Read at the pin.", **verdict}],
                "claims": {G.THM_ITEM: copy.deepcopy(THM_CLAIMS)}}
        return self.fx.run(self.fx.document(self.TASK, body))

    def test_a_downgrade_to_close_match_names_its_convention(self):
        self.records("exactMatch")
        down = dict(action="downgrade", relation="closeMatch", clauses=["c1", "c2"],
                    note="the order is a cardinal")
        self.assertRule(self.run_verdict(**down), 9, "names the convention")
        self.assertOk(self.run_verdict(**down, convention="encoding"))
        self.assertRule(self.run_verdict(**{**down, "note": "a strictly stronger form"},
                                         convention="encoding"), 9, "'stronger'")
        # without a note, the witness sentence the outcomes would take is the one read
        self.assertRule(self.run_verdict(**{k: v for k, v in down.items() if k != "note"},
                                         convention="encoding",
                                         witness="It is more general than the passage."),
                        9, "'more general*'")

    def test_a_confirm_names_it_when_the_record_does_not(self):
        self.records("closeMatch", note="the order is a cardinal")
        self.assertRule(self.run_verdict(action="confirm", clauses=["c1", "c2"]), 9,
                        "names the convention")
        self.assertOk(self.run_verdict(action="confirm", clauses=["c1", "c2"],
                                       convention="encoding"))
        self.records("closeMatch", note="the order is a cardinal", convention="encoding")
        self.assertOk(self.run_verdict(action="confirm", clauses=["c1", "c2"]))
        # the record's own note is the worker's (a strictly increasing list is no
        # generality): it never forces the challenger's verdict
        self.records("closeMatch", note="a strictly increasing list of primes",
                     convention="encoding")
        self.assertOk(self.run_verdict(action="confirm", clauses=["c1", "c2"]))
        # a downgrade to closeMatch keeps a record's own note, and the challenger's is
        # held to the words only where the outcomes would write it
        self.records("exactMatch", note="the order is a cardinal")
        self.assertOk(self.run_verdict(action="downgrade", relation="closeMatch",
                                       clauses=["c1", "c2"], convention="encoding"))
        # a blank convention on a confirm is none: the record's stands
        self.records("closeMatch", note="the order is a cardinal", convention="encoding")
        self.assertOk(self.run_verdict(action="confirm", clauses=["c1", "c2"], convention=""))

    def test_a_verdict_that_leaves_no_close_match_names_no_convention(self):
        self.records("exactMatch")
        self.assertRule(self.run_verdict(action="confirm", clauses=["c1", "c2"],
                                         convention="notation"), 9,
                        "convention belongs to a closeMatch")
        self.records("closeMatch", note="the order is a cardinal", convention="notation")
        self.assertRule(self.run_verdict(action="downgrade", relation="broadMatch",
                                         clauses=["c1", "c2"], convention="notation"), 9,
                        "convention belongs to a closeMatch")
        # the record's own convention is the outcomes' to drop, not the challenger's fault
        self.assertOk(self.run_verdict(action="downgrade", relation="broadMatch",
                                       clauses=["c1", "c2"]))

    def test_a_confirm_carries_no_field_the_source_verbatim(self):
        self.records("exactMatch")
        verbatim = " ".join(G.THEOREM.split()[:15])
        self.assertRule(self.run_verdict(action="confirm", clauses=["c1", "c2"],
                                         instantiation=verbatim), 1, "own words")
        self.assertRule(self.run_verdict(action="confirm", clauses=["c1", "c2"],
                                         instantiation="the widget\nand its gadget here"), 9,
                        "one line")


# --------------------------------------------------------------------------- #
# PD5-PD9 (judgment rules 2): PD6's whole part and PD7's junk value
# --------------------------------------------------------------------------- #

class TestTheRulesOfPD5ToPD9(JudgedLocate):
    """The mechanical part of PD5-PD9 binds a run planned under judgment rules 2.

    The same document is gated under rules 1 (a run planned before 2026-10-20) and
    under none (a run planned before the rules): neither refuses it.
    """

    def under(self, version, body: dict) -> dict:
        if version is None:
            self.fx.gate.pop("judgment_rules", None)
        else:
            self.fx.gate["judgment_rules"] = version
        self.fx.write_gate()
        try:
            return self.run_body(body)
        finally:
            self.fx.gate["judgment_rules"] = vocab.JUDGMENT_RULES
            self.fx.write_gate()

    def assertRefusedOnlyUnderPD(self, body: dict, needle: str):
        self.assertRule(self.run_body(body), 9, needle)
        self.assertOk(self.under(1, body))
        self.assertOk(self.under(None, body))

    def covered(self, first=("c1", "c2")) -> dict:
        body = judged_body()
        thm = body["passages"][0]
        thm["locations"] = [judged(G.location(relation="hasPart"), list(first), step=STEP),
                            judged(G.location(decl="Widget.Spanning", relation="hasPart",
                                              rank=2), ["c2"], step="the spanning property of "
                                                                   "the gadget")]
        thm["coverage"] = {"complete": True, "parts": [1, 2],
                           "note": "the two lemmas together give the theorem"}
        return body

    def test_the_version_is_cflibs(self):
        self.assertEqual((vocab.JUDGMENT_RULES, vocab.RULES_PD5_PD9), (2, 2))
        self.assertEqual(vl.RULES_PD5_PD9, vocab.RULES_PD5_PD9)
        self.assertEqual(self.fx.gate_cfg().judgment_rules, 2)

    def test_pd6_a_coverage_part_that_states_every_clause_is_no_part(self):
        body = self.covered()
        self.assertRefusedOnlyUnderPD(body, "a part that alone states every clause is no part "
                                            "(PD6)")
        self.assertRule(self.run_body(body), 9, "part rank 1 names every claim (c1, c2)")
        self.assertRule(self.run_body(body), 9, "give it its relation")
        # parts that each state some of the clauses are a claim
        self.assertOk(self.run_body(self.covered(first=("c1",))))
        # the part's own record is not refused for naming them all: only the claim is
        body = self.covered()
        del body["passages"][0]["coverage"]
        self.assertOk(self.run_body(body))

    def test_pd7_a_junk_value_is_no_convention(self):
        def close(convention: str) -> dict:
            body = judged_body()
            body["passages"][0]["locations"][0].update(
                relation="closeMatch", convention=convention,
                note="Lean returns 0 where the passage leaves the value undefined")
            return body

        self.assertRefusedOnlyUnderPD(close("junk_value"), "a junk value is no convention (PD7)")
        self.assertRule(self.run_body(close("junk_value")), 9,
                        "exactMatch when every case outside it is trivial")
        # rules 1 gate by the list they had, junk_value on it
        self.assertRule(self.under(1, close("generality")), 9,
                        "bundling, encoding, junk_value, normalisation, notation, renaming, "
                        "unfolding")
        # the exactMatch PD7 makes of it passes, its note saying why the case is trivial
        body = judged_body()
        body["passages"][0]["locations"][0]["note"] = ("Lean also returns 0 at n = 0, which the "
                                                       "passage leaves undefined")
        self.assertOk(self.run_body(body))

    def test_pd8_and_pd9_are_judgment_not_gate_rules(self):
        """A broadMatch whose instantiation names a declaration, and a claim list without a
        stated reason, are left to the challenge: the gate cannot tell a bridge lemma from
        the instances an instantiation names by right."""
        body = judged_body()
        body["passages"][0]["locations"][0].update(
            relation="broadMatch", instantiation="Widget.mul_mod at the passage's widget W")
        self.assertOk(self.run_body(body))


class TestAChallengerUnderPD5ToPD9(G.GateTestCase):
    """The challenge side of PD6 and PD7, under judgment rules 2 and not under 1."""

    TASK = "t-chl-0001"
    KEY = "9" * 64
    OTHER = "8" * 64

    def setUp(self):
        self.fx = G.Fixture(self, "locate-challenge")
        self.fx.gate["judgment_rules"] = vocab.JUDGMENT_RULES
        self.fx.write_gate()

    def under(self, version: int, body: dict) -> dict:
        self.fx.gate["judgment_rules"] = version
        self.fx.write_gate()
        try:
            return self.fx.run(self.fx.document(self.TASK, body))
        finally:
            self.fx.gate["judgment_rules"] = vocab.JUDGMENT_RULES
            self.fx.write_gate()

    def records(self, *records: dict) -> None:
        doc = G.locate_manifest_doc(self.fx)
        doc.pop("passages")
        doc["records"] = list(records)
        self.fx.manifest(self.TASK, doc)

    def location(self, relation: str, **extra) -> dict:
        return {"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
                "span": G.THEOREM_SPAN, "passage_kind": "theorem", "relation": relation, **extra}

    def verdict(self, **verdict) -> dict:
        return {"verdicts": [{"item": G.THM_ITEM, "target": "location",
                              "location_key": self.KEY, "witness": "Read at the pin.",
                              **verdict}],
                "claims": {G.THM_ITEM: copy.deepcopy(THM_CLAIMS)}}

    def test_pd7_a_confirm_keeps_a_records_own_junk_value(self):
        """A record judged under rules 1 may say junk_value; PD7 would make it an
        exactMatch, which no challenge can raise it to, so the confirm keeps it."""
        self.records(self.location("closeMatch", note="Lean returns 0 at n = 0",
                                   convention="junk_value"))
        body = self.verdict(action="confirm", clauses=["c1", "c2"])
        self.assertOk(self.under(2, body))
        self.assertOk(self.under(1, body))
        # a convention the challenger names itself is held to the list of its rules
        self.records(self.location("closeMatch", note="Lean returns 0 at n = 0"))
        body = self.verdict(action="confirm", clauses=["c1", "c2"], convention="junk_value")
        self.assertRule(self.under(2, body), 9, "a junk value is no convention (PD7)")
        self.assertOk(self.under(1, body))

    def test_pd7_a_confirm_that_names_the_records_junk_value_again_keeps_it(self):
        """The prompt says to confirm such a record as it stands: naming its own junk_value
        again keeps it, as naming none does (chal-smoke-3 wrote such confirms, and the gate
        refused them under rules 2, telling the challenger to raise a relation it cannot)."""
        self.records(self.location("closeMatch", note="Lean returns 0 at n = 0",
                                   convention="junk_value"))
        body = self.verdict(action="confirm", clauses=["c1", "c2"], convention="junk_value")
        self.assertOk(self.under(2, body))
        self.assertOk(self.under(1, body))
        # PD7's third case: the two differ where both are defined, so the confirm relabels the
        # record under the real convention, which the outcomes put in place of the record's
        body = self.verdict(action="confirm", clauses=["c1", "c2"], convention="encoding")
        self.assertOk(self.under(2, body))
        # a convention of its own that is on no list is refused, as a downgrade's is
        body = self.verdict(action="confirm", clauses=["c1", "c2"], convention="generality")
        self.assertRule(self.under(2, body), 9, "one of bundling, encoding, normalisation, "
                                                "notation, renaming, unfolding (convention")

    def test_pd7_a_confirm_that_keeps_nothing_is_shown_its_rules_list(self):
        """A closeMatch record with no convention (a run planned before the rules): a confirm
        that names none has nothing to keep, so the list it is shown is its rules', and under
        rules 2 that list has no junk_value, which would be refused if named."""
        self.records(self.location("closeMatch", note="the order is a cardinal"))
        body = self.verdict(action="confirm", clauses=["c1", "c2"])
        result = self.under(2, body)
        self.assertRule(result, 9, "one of bundling, encoding, normalisation, notation, renaming, "
                                   "unfolding (convention")
        self.assertFalse([p for p in result["problems"] if "junk_value" in p], result["problems"])
        self.assertRule(self.under(1, body), 9, "bundling, encoding, junk_value, normalisation, "
                                                "notation, renaming, unfolding")
        body = self.verdict(action="confirm", clauses=["c1", "c2"], convention="junk_value")
        self.assertRule(self.under(2, body), 9, "a junk value is no convention (PD7)")
        self.assertOk(self.under(2, self.verdict(action="confirm", clauses=["c1", "c2"],
                                                 convention="encoding")))

    def test_pd7_the_gap_hint_gives_no_junk_value_as_a_convention(self):
        message = vl._gap_problem("does not state", "exactMatch", "note")
        self.assertIn("a convention or a case outside the passage's domain", message)
        self.assertIn("Lean also returns 0 at n = 0, which the passage leaves undefined", message)
        self.assertIsNone(vl.admits_missing_clause("Lean also returns 0 at n = 0, which the "
                                                   "passage leaves undefined"))
        proposal = (HERE.parent / "docs" / "proposals" / "wikipedia-locate.md").read_text(
            encoding="utf-8")
        row = next(line for line in proposal.splitlines() if line.startswith("| `closeMatch` |"))
        self.assertNotIn("junk values", row)
        self.assertIn("since PD7", row)

    def test_pd7_a_downgrade_names_no_junk_value(self):
        self.records(self.location("exactMatch"))
        body = self.verdict(action="downgrade", relation="closeMatch", clauses=["c1", "c2"],
                            convention="junk_value", note="Lean returns 0 at n = 0")
        self.assertRule(self.under(2, body), 9, "a junk value is no convention (PD7)")
        self.assertOk(self.under(1, body))

    def test_pd6_a_coverage_confirm_rests_on_no_whole_part(self):
        self.records({"item": G.THM_ITEM, "target": "coverage", "span": G.THEOREM_SPAN,
                      "passage_kind": "theorem",
                      "parts": [{"location_key": self.KEY, "relation": "hasPart"},
                                {"location_key": self.OTHER, "relation": "hasPart"}]})
        verdict = {"item": G.THM_ITEM, "target": "coverage", "action": "confirm",
                   "part_clauses": {self.KEY: ["c1", "c2"], self.OTHER: ["c2"]},
                   "witness": "the first states both clauses, the second divisibility"}
        body = {"verdicts": [verdict], "claims": {G.THM_ITEM: copy.deepcopy(THM_CLAIMS)}}
        result = self.under(2, body)
        self.assertRule(result, 9, "part_clauses[999999999999] names every claim of your list")
        self.assertRule(result, 9, "a part that alone states every clause is no part (PD6)")
        self.assertOk(self.under(1, body))
        verdict["part_clauses"] = {self.KEY: ["c1"], self.OTHER: ["c2"]}
        self.assertOk(self.under(2, body))
        # the refute PD6 asks for passes
        body = {"verdicts": [{"item": G.THM_ITEM, "target": "coverage", "action": "refute",
                              "witness": "its first part states every clause alone"}],
                "claims": {G.THM_ITEM: copy.deepcopy(THM_CLAIMS)}}
        self.assertOk(self.under(2, body))


class TestAdmitsAMissingClause(unittest.TestCase):

    #: notes that admit a clause the declaration does not state, with the phrase
    GAPS = (("The bound is NOT STATED here.", "not stated"),
            ("it doesn\u2019t state uniqueness", "does not state"),
            ("states one direction; not the converse", "not the converse"),
            ("existence, without uniqueness", "without uniqueness"),
            ("the negative powers are not covered", "not covered"),
            ("the size bound isn't stated", "not stated"),
            ("it doesn't cover the divisibility", "does not cover"),
            ("omits the divisibility clause", "omits"),
            ("only proves existence", "only proves"),
            ("the converse is not included", "converse is not"),
            ("states one direction only", "one direction only"),
            ("uniqueness is not asserted", "uniqueness is not"),
            ("the bound is not a part of it", "not a part of"),
            ("unlike the passage, it does not state the converse", "does not state"),
            ("the passage's bound is not stated", "not stated"))
    #: notes that admit nothing of the declaration's: negated, about the passage, or a
    #: relative clause on an "unlike" clause's object
    CLEAN = ("states both implications, not only one direction",
             "existence and uniqueness, not only the existence",
             "unlike its corollary, which does not state the bound, this one states both "
             "clauses",
             "junk value: Lean returns 0 at 0, a value which is not part of the passage",
             "junk value: the passage does not cover n = 0, where Lean returns 0",
             "encoding: the passage leaves the case x = 0 not covered",
             "normalisation: the leading coefficient is 1, which the passage has not stated",
             "", None, "junk value of deriv off the differentiable points",
             "the notstated macro", "stated as a bijection of quotients")

    def test_the_closed_phrase_list(self):
        for note, phrase in self.GAPS:
            with self.subTest(note=note):
                self.assertEqual(vocab.admits_missing_clause(note), phrase)
                self.assertEqual(vl.admits_missing_clause(note), phrase)
        for note in self.CLEAN:
            with self.subTest(note=note):
                self.assertIsNone(vocab.admits_missing_clause(note))
                self.assertIsNone(vl.admits_missing_clause(note))

    def test_a_contraction_is_spelled_out(self):
        self.assertEqual(vocab.fold_words("It DOESN\u2019T  cover it; can't; won't"),
                         "it does not cover it; can not; will not")
        self.assertEqual(vocab.fold_words("x"), vl.fold_words("x"))

    def test_the_gates_copies_are_cflibs(self):
        self.assertEqual(vl.JUDGMENT_RULES, vocab.JUDGMENT_RULES)
        self.assertEqual(vl.STRONG_RELATIONS, vocab.STRONG_RELATIONS)
        self.assertEqual(vl.STEP_RELATIONS, vocab.STEP_RELATIONS)
        self.assertEqual(vl.CLAUSE_GAP_PHRASES, vocab.CLAUSE_GAP_PHRASES)
        self.assertEqual(vl.CANDIDATE_PART_PHRASES, vocab.CANDIDATE_PART_PHRASES)
        self.assertEqual(vl.CLAIM_ID.pattern, vocab.CLAIM_ID.pattern)
        self.assertEqual((vl.MAX_PASSAGE_CLAIMS, vl.MAX_CLAIM_CHARS),
                         (vocab.MAX_PASSAGE_CLAIMS, vocab.MAX_CLAIM_CHARS))
        self.assertEqual((vl.MAX_JUDGMENT_LINE_CHARS, vl.MIN_JUDGMENT_LINE_WORDS),
                         (vocab.MAX_JUDGMENT_LINE_CHARS, vocab.MIN_JUDGMENT_LINE_WORDS))
        self.assertEqual(vl.GENERAL_QUERY_TAG, vocab.GENERAL_QUERY_TAG)
        for reason in ("not the worked example", "the whole thing", "generalises it", ""):
            self.assertEqual(vl.rejects_a_part(reason), vocab.rejects_a_part(reason))
        for phrase in (*vocab.CLAUSE_GAP_PHRASES, *vocab.CLOSE_MATCH_GENERALITY_PHRASES):
            self.assertEqual(vl.phrase_pattern(phrase).pattern,
                             vocab.phrase_pattern(phrase).pattern)



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
        self.assertRule(self.run_body(body), 9, "rests on your own claim list")
        body = self.body()
        del body["verdicts"][0]["clauses"]
        self.assertRule(self.run_body(body), 9, "names the ids of the claims it states")
        body = self.body()
        body["verdicts"][0]["clauses"] = ["c1"]
        self.assertRule(self.run_body(body), 9, "does not state c2")
        self.assertOk(self.unruled(body))

    def test_a_downgrade_names_what_the_relation_it_leaves_states(self):
        body = self.body()
        body["verdicts"][0].update(action="downgrade", relation="hasPart", clauses=["c2"],
                                   step="the divisibility of the gadget's size")
        self.assertOk(self.run_body(body))
        body["verdicts"][0].pop("step")
        body["verdicts"][0].update(relation="relatedMatch", clauses=["c2"])
        self.assertRule(self.run_body(body), 9, "relatedMatch states no clause")
        del body["verdicts"][0]["clauses"]
        self.assertOk(self.run_body(body))
        # a refute leaves no relation, and names nothing
        body["verdicts"][0] = {"item": G.THM_ITEM, "target": "location",
                               "location_key": self.KEY, "action": "refute", "witness": "no"}
        self.assertOk(self.run_body(body))

    def test_a_downgrades_own_note_admits_no_gap_on_a_strong_relation(self):
        self.records(relation="exactMatch")
        body = self.body()
        body["verdicts"][0].update(action="downgrade", relation="broadMatch",
                                   note="it does not state the size bound")
        self.assertRule(self.run_body(body), 9, "admits a clause")
        body["verdicts"][0].update(relation="hasPart", clauses=["c1"], step=STEP)
        self.assertOk(self.run_body(body))

    def test_a_records_own_note_never_forces_the_challengers_verdict(self):
        """The worker's note is the worker's: a gap it admits makes the plan refute the
        record by default, and the challenger judges it -- the gate holds the
        challenger's own words only, so a misworded note forces no relation."""
        self.records(note="the size bound is not part of this declaration")
        self.assertOk(self.run_body(self.body()))

    def test_an_overturns_locations_are_judged_like_a_workers(self):
        body = self.body()
        del body["claims"][G.LEAD_ITEM]
        self.assertRule(self.run_body(body), 9, "a verdict on an absence rests on your own")
        body = self.body()
        del body["verdicts"][1]["locations"][0]["clauses"]
        self.assertRule(self.run_body(body), 9, "names the ids")
        self.assertOk(self.unruled(body))

    def test_an_overturn_location_says_how_its_declaration_applies(self):
        sign(self.fx, "Beta.Widget.gadget_of_finite", GENERIC)
        body = self.body()
        self.assertRule(self.run_body(body), 9, "takes instance argument(s)")
        body["verdicts"][1]["locations"][0]["instantiation"] = "W is the passage's widget"
        self.assertOk(self.run_body(body))

    def test_every_verdict_on_a_passage_rests_on_the_challengers_claims(self):
        """The FM7-prone verdicts -- a refute, a downgrade to relatedMatch, an uphold --
        list the passage's clauses first, as a confirm does."""
        for verdict in ({"action": "refute"},
                        {"action": "downgrade", "relation": "relatedMatch"}):
            body = self.body()
            del body["claims"][G.THM_ITEM]
            body["verdicts"][0] = {"item": G.THM_ITEM, "target": "location",
                                   "location_key": self.KEY, "witness": "read at the pin",
                                   **verdict}
            with self.subTest(verdict=verdict):
                self.assertRule(self.run_body(body), 9, "rests on your own claim list")
                self.assertOk(self.unruled(body))
                self.fx.gate["judgment_rules"] = vocab.JUDGMENT_RULES
                self.fx.write_gate()
        body = self.body()
        del body["claims"][G.LEAD_ITEM]
        body["verdicts"][1] = {"item": G.LEAD_ITEM, "target": "absence", "action": "uphold",
                               "witness": "searched again", **self.SEARCH}
        self.assertRule(self.run_body(body), 9, "a verdict on an absence rests on your own")

    def test_a_challenger_never_drops_a_workers_claim(self):
        worker = THM_CLAIMS + [{"id": "c3", "text": "the gadget is unique"}]
        doc = G.locate_manifest_doc(self.fx)
        doc.pop("passages")
        doc["records"] = [{"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
                           "span": G.THEOREM_SPAN, "passage_kind": "theorem",
                           "relation": "exactMatch", "claims": worker,
                           "clauses": ["c1", "c2", "c3"]}]
        self.fx.manifest(self.TASK, doc)
        body = {"verdicts": [{"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
                              "action": "confirm", "clauses": ["c1"], "witness": "states it"}],
                "claims": {G.THM_ITEM: [THM_CLAIMS[0]]}}
        self.assertRule(self.run_body(body), 9, "split a clause, never drop one")
        # a downgrade to a part needs no longer list
        body["verdicts"][0].update(action="downgrade", relation="hasPart", step=STEP)
        self.assertOk(self.run_body(body))

    #: an uphold's own search, tagged against LEAD_CLAIMS
    SEARCH = {"queries": ["gadget spanning finite", "widget cover lemma", "Widget.cover"],
              "query_clauses": {"gadget spanning finite": ["c1"], "widget cover lemma": ["c1"],
                                "Widget.cover": ["c1"]}}

    def test_an_uphold_lists_its_tagged_queries(self):
        body = self.body()
        body["verdicts"][1] = {"item": G.LEAD_ITEM, "target": "absence", "action": "uphold",
                               "witness": "nothing states it"}
        self.assertRule(self.run_body(body), 9, "an uphold lists the queries you ran")
        self.assertOk(self.unruled(body))
        self.fx.gate["judgment_rules"] = vocab.JUDGMENT_RULES
        self.fx.write_gate()
        body["verdicts"][1].update(copy.deepcopy(self.SEARCH))
        self.assertOk(self.run_body(body))
        body["verdicts"][1]["queries"] = self.SEARCH["queries"][:2]
        body["verdicts"][1]["query_clauses"] = {q: ["c1"] for q in self.SEARCH["queries"][:2]}
        self.assertRule(self.run_body(body), 9, "2 distinct queries; at least 3")
        body["verdicts"][1].update(copy.deepcopy(self.SEARCH))
        body["verdicts"][1]["query_clauses"].pop("Widget.cover")
        self.assertRule(self.run_body(body), 9, "carries no tag")

    def test_a_coverage_confirm_names_each_parts_clauses(self):
        other = "8" * 64
        doc = G.locate_manifest_doc(self.fx)
        doc.pop("passages")
        doc["records"] = [{"item": G.THM_ITEM, "target": "coverage", "span": G.THEOREM_SPAN,
                           "passage_kind": "theorem",
                           "parts": [{"location_key": self.KEY, "relation": "hasPart"},
                                     {"location_key": other, "relation": "hasPart"}]}]
        self.fx.manifest(self.TASK, doc)
        verdict = {"item": G.THM_ITEM, "target": "coverage", "action": "confirm",
                   "witness": "the first states existence, the second divisibility"}
        body = {"verdicts": [verdict], "claims": {G.THM_ITEM: copy.deepcopy(THM_CLAIMS)}}
        self.assertRule(self.run_body(body), 9, "part_clauses")
        verdict["part_clauses"] = {self.KEY: ["c1"], other: ["c1"]}
        self.assertRule(self.run_body(body), 9, "no part states c2")
        verdict["part_clauses"] = {self.KEY: ["c1"]}
        self.assertRule(self.run_body(body), 9, "gives part 888888888888 no clauses")
        verdict["part_clauses"] = {self.KEY: ["c1"], other: ["c2"]}
        self.assertOk(self.run_body(body))
        del body["claims"]
        self.assertRule(self.run_body(body), 9, "a verdict on a coverage rests on your own")
        # a refute rests on the claim list too
        body = {"verdicts": [{"item": G.THM_ITEM, "target": "coverage", "action": "refute",
                              "witness": "no part states the divisibility"}]}
        self.assertRule(self.run_body(body), 9, "a verdict on a coverage rests on your own")

    def test_a_confirm_instantiates_what_the_record_did_not(self):
        """A run planned before the rules has no instantiation on any record: the
        challenger who keeps a relation on a generic declaration instantiates it."""
        sign(self.fx, "Widget.exists_spanning_gadget", GENERIC)
        doc = G.locate_manifest_doc(self.fx)
        doc.pop("passages")
        doc["records"] = [{"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
                           "span": G.THEOREM_SPAN, "passage_kind": "theorem",
                           "relation": "broadMatch", "library": "alpha",
                           "declaration": "Widget.exists_spanning_gadget"}]
        self.fx.manifest(self.TASK, doc)
        body = {"verdicts": [{"item": G.THM_ITEM, "target": "location", "location_key": self.KEY,
                              "action": "confirm", "clauses": ["c1", "c2"],
                              "witness": "states it"}],
                "claims": {G.THM_ITEM: copy.deepcopy(THM_CLAIMS)}}
        self.assertRule(self.run_body(body), 9, "instantiate it yourself")
        body["verdicts"][0]["instantiation"] = "W is the passage's widget G, WidgetLike by its law"
        self.assertOk(self.run_body(body))
        # a refute keeps nothing, and instantiates nothing
        body["verdicts"][0] = {"item": G.THM_ITEM, "target": "location",
                               "location_key": self.KEY, "action": "refute", "witness": "no"}
        self.assertOk(self.run_body(body))

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

    def test_a_downgrades_step_is_the_record_it_leaves(self):
        def verdict(rec):
            v = F.default_verdict(rec)
            if rec.get("declaration") == "Widget.exists_spanning_gadget":
                v.update(action="downgrade", relation="hasPart", clauses=["c1"],
                         step="the displayed existence of the gadget")
            if rec.get("declaration") == "Widget.card_dvd":     # no downgrade: equally strong
                v.update(action="downgrade", relation="narrowMatch", clauses=["c1"],
                         step="ignored with the verdict")
            return v

        self.challenge(verdict=verdict)
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        locs = {l["declaration"]: l for r in built["outcomes"] for l in r["locations"]}
        lead = locs["Widget.exists_spanning_gadget"]
        self.assertEqual((lead["relation"], lead["step"], lead["challenge_clauses"]),
                         ("hasPart", "the displayed existence of the gadget", ["c1"]))
        # a verdict that is no downgrade changes nothing of the record, and leaves it owed
        part = locs["Widget.card_dvd"]
        self.assertEqual((part["relation"], part["challenge"]), ("hasPart", None))
        self.assertFalse({"step", "challenge_clauses"} & set(part))


class TestTheOutcomesKeepWhatFitsTheRelation(unittest.TestCase):
    """What a verdict leaves on a location fits the relation it leaves (outcomes'
    ``_fit_fields``), and an uphold's search and a coverage confirm's clauses are kept."""

    def setUp(self):
        self.root = scratch(self)
        self.paths = F.campaign(self, self.root)
        F.plan_locate(self, self.root, run_id="loc-1")
        docs = F.locate_documents(self.paths, "loc-1")
        for doc in docs.values():
            for p in doc["passages"]:
                names = [l["declaration"] for l in p["locations"]]
                if "Beta.Widget.gadget_of_finite" in names:
                    p["locations"][1].update(relation="closeMatch", convention="notation",
                                             note="the gadget is written as a finite set")
                if "Widget.card_dvd" in names:
                    p["locations"].append(F._loc("alpha", "Widget.Spanning", "hasPart", 2))
                    p["coverage"] = {"complete": True, "parts": [1, 2],
                                     "note": "the two lemmas together give the theorem"}
        F.write_harvest(self.paths, "loc-1", docs)

    def challenge(self, verdict) -> dict:
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-challenge",
                            "--sourceset", F.SS, "--universe", F.U, "--challenge-of", "loc-1",
                            "--run-id", "chl-1")
        self.assertEqual(code, 0, out + err)
        F.write_harvest(self.paths, "chl-1", F.challenge_documents(self.paths, "chl-1",
                                                                   verdict=verdict))
        return outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])

    def test_a_downgrade_off_close_match_leaves_no_convention(self):
        def verdict(rec):
            v = F.default_verdict(rec)
            if rec.get("declaration") == "Beta.Widget.gadget_of_finite":
                v.update(action="downgrade", relation="broadMatch", convention="notation")
            if rec.get("declaration") == "Widget.exists_spanning_gadget":
                # a confirm of an exactMatch that names a step and a convention keeps neither
                v.update(convention="notation", step="a step that fits no exactMatch")
            return v

        built = self.challenge(verdict)
        locs = {l["declaration"]: l for r in built["outcomes"] for l in r["locations"]}
        self.assertEqual(locs["Beta.Widget.gadget_of_finite"]["relation"], "broadMatch")
        self.assertNotIn("convention", locs["Beta.Widget.gadget_of_finite"])
        self.assertFalse({"convention", "step"} & set(locs["Widget.exists_spanning_gadget"]))

    def test_an_uphold_and_a_coverage_confirm_keep_what_the_challenger_said(self):
        search = {"queries": ["gadget lattice height", "lattice of gadgets", "height bound"],
                  "query_clauses": {"gadget lattice height": ["c1"],
                                    "lattice of gadgets": ["c1"], "height bound": ["c1"]}}
        part_clauses = {}

        def verdict(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "absence":
                v = {"item": rec["item"], "target": "absence", "action": "uphold",
                     "witness": "nothing states it", **search}
            if rec["target"] == "coverage":
                keys = [part["location_key"] for part in rec["parts"]]
                part_clauses.update({keys[0]: ["c1"], keys[1]: ["c2"]})
                v["part_clauses"] = dict(part_clauses)
            return v

        built = self.challenge(verdict)
        absent = next(r for r in built["outcomes"] if r["outcome"] == "absent"
                      and r.get("challenge_receipt"))
        self.assertEqual(absent["challenge_receipt"], search)
        self.assertEqual(outcomes.outcome_row(absent)["challenge_receipt"], search)
        covered = next(r for r in built["outcomes"] if r.get("coverage"))
        self.assertEqual(covered["coverage_challenge_clauses"], part_clauses)
        self.assertEqual(set(covered["coverage"]), {"complete", "parts", "note", "challenge"})
        self.assertEqual(outcomes.outcome_row(covered)["coverage_challenge_clauses"],
                         part_clauses)


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
        for mode in (vocab.LOCATE, vocab.LOCATE_CHALLENGE, vocab.LOCATE_INVENTORY):
            preset = lp.get(mode)
            ctx = lprompts.build_context(preset, run_id="r1", host=layout,
                                         universe=inputs.load_universe(paths, F.U), per_task=8)
            cls.rendered[mode] = lprompts.render_prompts(preset, ctx)
        case.doCleanups()

    def worker(self, mode=vocab.LOCATE) -> str:
        return self.rendered[mode]["worker.md"]

    def test_fm1_the_worker_reads_the_inventorys_clauses(self):
        """Judgment rules 3 (the statement cut's J1): the inventory owns the one clause list,
        so the worker writes no claim list of its own (FM1's fix of rules 1 and 2 was a
        worker's list, which the challenger's drifted from on 48 smoke-3 passages)."""
        worker = self.worker()
        self.assertIn("**Its clauses are the inventory's, and you write no claim list** (a "
                      "`claims` field is refused)", worker)
        self.assertIn("a **simple statement** carries none, and its one clause is `c1`", worker)
        self.assertIn("`conjuncts`, 2 to 5 entries", worker)
        self.assertIn("copies more than twelve consecutive words of the article", worker)
        self.assertIn("use one only when its `clauses` name every clause", worker)
        self.assertIn('"clauses": ["c1"]', worker)
        self.assertIn("their `clauses` pairwise disjoint and together naming every conjunct",
                      worker)
        self.assertNotIn("**write its claim list first**", worker)
        self.assertNotIn('"claims": [{"id": "c1"', worker)
        system = self.rendered[vocab.LOCATE]["worker-system.md"]
        self.assertIn("any `claims` list (the clause ids are the manifest passage's conjunct "
                      "ids, or `c1` for a simple statement)", system)
        self.assertNotIn("a located or absent passage without its claim list (`claims`)", system)

    def test_fm5_a_query_for_every_claim_and_the_general_theorem(self):
        worker = self.worker()
        self.assertIn("**a query of its own for every clause**", worker)
        self.assertIn("tag every query you list", worker)
        self.assertIn("**general theorem it is an instance of**, with a query of its own tagged "
                      "`general`", worker)
        self.assertIn('"query_clauses": {"<query a>": ["c1"]', worker)
        self.assertIn("**Check every candidate against each clause before you reject it.**",
                      worker)
        self.assertIn('"not the whole passage" or "not the worked example" is no reason to '
                      'reject it', worker)
        challenger = self.worker(vocab.LOCATE_CHALLENGE)
        self.assertIn("with a query of its own for every clause of the passage", challenger)
        self.assertNotIn("for every clause of your claim list", challenger)
        self.assertIn("listing the queries you ran (`queries`, at least 3 distinct) tagged with "
                      "the clauses (`query_clauses`", challenger)
        self.assertIn('"target": "absence", "action": "uphold",', challenger)
        system = self.rendered[vocab.LOCATE]["worker-system.md"]
        self.assertIn("an absence whose `query_clauses` leave a query untagged or a clause without "
                      "a query of its own", system)
        self.assertIn("or that rejects a candidate for stating part of the passage", system)

    def test_the_search_covers_every_library_at_once(self):
        """One search covers the universe; --library only when one library fills the result
        (never a loop over every library: the audit's universe has 48)."""
        worker = self.worker()
        self.assertIn("searches every library of the universe at once", worker)
        self.assertIn("run the query again with `--library <id>` for the libraries of the "
                      "passage's field, not for every library", worker)
        self.assertNotIn("Search every library of the universe, not only the largest", worker)

    def test_fm6_the_declaration_is_instantiated_at_the_passages_objects(self):
        worker = self.worker()
        self.assertIn("**Instantiate** each declaration you would cite at the passage's own "
                      "objects", worker)
        self.assertIn("**If the declaration's setting neither contains nor is contained in the "
                      "passage's**", worker)
        self.assertIn("the relation is `relatedMatch`, however close its statement reads", worker)
        self.assertIn("When its setting is a special case of the passage's (ℕ where the passage "
                      "has ℤ, a field where it has a commutative ring, the n = 1 case), it is a "
                      "`narrowMatch` (R3, PD2, PD3)", worker)
        self.assertIn('"instantiation": "<the passage', worker)
        challenger = self.worker(vocab.LOCATE_CHALLENGE)
        self.assertIn("check the record's `instantiation` against the signature, and when it has "
                      "none and the signature takes an instance argument, write your own",
                      challenger)
        self.assertIn("it is `narrowMatch` (R3), never `relatedMatch`", challenger)
        self.assertIn("without its `instantiation` line",
                      self.rendered[vocab.LOCATE]["worker-system.md"])

    def test_a_special_case_is_never_sent_to_related_match(self):
        """The narrowMatch rules contradicted each other: a special case's objects cannot
        meet the passage's hypotheses, and three texts sent that to relatedMatch."""
        for mode in (vocab.LOCATE, vocab.LOCATE_CHALLENGE):
            worker = self.worker(mode)
            with self.subTest(mode=mode):
                self.assertNotIn("If the passage's objects cannot meet the declaration's "
                                 "hypotheses", worker)
                self.assertNotIn("or the declaration needs finite dimension, finite rank or a "
                                 "field the passage does not have", worker)
        lines = self.rule_lines()
        self.assertIn("the passage, instantiated at the declaration's objects, says what the "
                      "declaration says", lines["R3"])
        self.assertIn("a setting that neither contains nor is contained in the passage's",
                      lines["R5"])
        self.assertIn("A declaration that follows from the passage by weakening states part of "
                      "it at most", lines["R5"])
        self.assertIn("a declaration with stronger hypotheses whose conclusion does not give the "
                      "passage's", lines["R5"])
        # PD4 (2026-10-19): stronger hypotheses with the passage's conclusion, or a stronger
        # one, is R3's special case, never relatedMatch.
        self.assertIn("under stronger hypotheses that some of the passage's own cases meet",
                      lines["R3"])
        self.assertNotIn("narrower in one respect and stronger or broader in another", lines["R5"])
        message = vl._instantiation_problem({"library": "alpha", "declaration": "X"},
                                            ["[inst : Foo]"], challenger=False)
        self.assertIn("A setting that neither contains nor is contained in the passage's is "
                      "relatedMatch (R5); a special case of it narrowMatch (R3)", message)

    def test_fm3_a_part_is_entailed_by_the_declaration_alone(self):
        worker = self.worker()
        self.assertIn("**`hasPart` and `narrowMatch` need the declaration alone to entail what "
                      "they credit it with**", worker)
        self.assertIn("a lemma the proof uses along the way", worker)
        self.assertIn("Every `hasPart` and `narrowMatch` says, in `step`", worker)
        # PD5 cut proofs into their statements, so the step is the conjunct, never a proof's
        # displayed step (which stays for the inventories planned before PD5)
        self.assertIn("what the declaration alone entails of this passage: the conjunct, as the "
                      "declaration states it at the passage's objects", worker)
        self.assertNotIn("on a proof, the displayed step it states", worker)
        challenger = self.worker(vocab.LOCATE_CHALLENGE)
        self.assertIn("A `hasPart` or `narrowMatch` stands only when the declaration alone "
                      "entails what it is credited with", challenger)
        self.assertIn("a `hasPart` or `narrowMatch` that does not say in `step` what the "
                      "declaration alone entails", self.rendered[vocab.LOCATE]["worker-system.md"])

    def test_fm4_a_close_match_names_a_listed_convention(self):
        worker = self.worker()
        self.assertIn("`closeMatch` — the same up to a convention (R1): name it in `convention`, "
                      "one of `notation`, `encoding`, `normalisation`, `renaming`, `unfolding` or "
                      "`bundling`", worker)
        # the domain-extension test is R1's, not a gloss on every closeMatch: a renaming or a
        # bundled map differs at no point and is still a convention
        self.assertNotIn("the two differing where both are defined", worker)
        self.assertIn("a case outside the passage's domain never makes a `closeMatch` (R1's "
                      "three-way test), a difference at a point where both are defined can",
                      worker)
        self.assertIn("describe the declaration, not what the passage leaves out", worker)
        challenger = self.worker(vocab.LOCATE_CHALLENGE)
        self.assertIn("A downgrade to `closeMatch` also gives `convention`", challenger)
        self.assertIn("a `closeMatch` without a listed `convention`",
                      self.rendered[vocab.LOCATE]["worker-system.md"])

    def test_every_phrase_the_gate_refuses_is_in_every_prompt_that_warns_of_it(self):
        """The prompts render the generality list from cflib.vocab: none lists fewer words
        than the gate refuses (it once left out "strictly")."""
        rendered = [self.worker(), self.worker(vocab.LOCATE_CHALLENGE),
                    self.rendered[vocab.LOCATE]["worker-system.md"]]
        for phrase in vocab.CLOSE_MATCH_GENERALITY_PHRASES:
            said = f'"{phrase[:-1]}…"' if phrase.endswith("*") else f'"{phrase}"'
            for text in rendered:
                with self.subTest(phrase=phrase):
                    self.assertIn(said, text)

    def test_the_challenger_reads_a_records_note_as_a_claim_not_a_verdict(self):
        """The prompt once said a closeMatch whose note says "strict" is a broadMatch at
        most: read as the record's note, that pushed the downgrade the gate refuses to
        force (a strictly increasing list is no strengthening)."""
        challenger = self.worker(vocab.LOCATE_CHALLENGE)
        self.assertNotIn("is a `broadMatch` at most", challenger)
        self.assertIn("A record's own note — the worker's — that uses one of them is a claim to "
                      "check, not a verdict", challenger)
        self.assertIn("Your own `note` (and the witness sentence that stands in for it)",
                      challenger)

    def test_the_challenge_prompt_puts_the_claim_list_check_first(self):
        challenger = self.worker(vocab.LOCATE_CHALLENGE)
        location = challenger.index("- **A location** is refuted by default.")
        first = challenger.index("  - Check it against the passage's clauses first", location)
        downgrade = challenger.index("downgrade", location)
        self.assertLess(first, downgrade)
        self.assertLess(challenger.index("**FM7**"),
                        challenger.index("## How to judge a relation: rules R1–R7"))
        self.assertIn("Every verdict on a location, a coverage claim, an absence or an "
                      "unresolved outcome rests on them", challenger)
        # one list, the inventory's: FM7's "at least as many claims as the worker's" is void
        self.assertNotIn("your list has at least as many claims as the worker's", challenger)
        self.assertIn("`part_relations`", challenger)
        self.assertIn("No `claims` and no `part_clauses`", challenger)
        self.assertIn("(`passage_locations`, read-only", challenger)

    def test_the_reviewer_reads_what_the_plan_now_does(self):
        reviewer = self.rendered[vocab.LOCATE]["reviewer-system.md"]
        self.assertNotIn("names its convention in `note`", reviewer)
        self.assertIn("a `closeMatch` names its `convention`", reviewer)
        self.assertIn("re-judge every strong location a supporting passage's status rests on",
                      reviewer)

    def test_no_real_article_title_in_a_prompt_example(self):
        for mode in (vocab.LOCATE, vocab.LOCATE_CHALLENGE):
            with self.subTest(mode=mode):
                self.assertNotIn("Chinese remainder", self.worker(mode))

    @staticmethod
    def rule_lines() -> dict[str, str]:
        """R1-R7, PD1-PD10 and FM7 as references/vocabulary.md section 1.11 writes them."""
        text = (HERE.parent / "references" / "vocabulary.md").read_text(encoding="utf-8")
        start = text.index("**How a relation is judged: rules R1\u2013R7**")
        block = text[start:text.index("**The derived status**", start)]
        return {m.group(1): "- **%s** \u2014 %s" % (m.group(1), " ".join(m.group(2).split()))
                for m in re.finditer(r"^- \*\*(R[1-7]|PD(?:10|[1-9])|FM7)\*\* \u2014 (.*?)(?=^\S|\Z)",
                                     block, re.M | re.S)}

    def test_the_rules_and_the_policy_defaults_are_the_vocabularys_word_for_word(self):
        lines = self.rule_lines()
        self.assertEqual(sorted(lines), sorted([f"R{i}" for i in range(1, 8)]
                                               + [f"PD{i}" for i in range(1, 11)] + ["FM7"]))
        for mode in (vocab.LOCATE, vocab.LOCATE_CHALLENGE):
            worker = " ".join(self.worker(mode).split())
            for name, line in lines.items():
                if name == "FM7" and mode == vocab.LOCATE:
                    continue
                with self.subTest(mode=mode, rule=name):
                    self.assertIn(line, worker)
        self.assertNotIn("**FM7**", self.worker())

    def test_the_labeler_judges_by_the_same_rules_word_for_word(self):
        """The bench's gold is labelled by the rules the workers it scores are given."""
        from bench import gold as gold_mod
        text = (HERE.parent / "workflows" / "locate-label.workflow.js").read_text(encoding="utf-8")
        rules = text[text.index("const RULES = `"):text.index("const huntTools")]
        rules = " ".join(rules.replace("\\`", "`").split())
        for name, line in self.rule_lines().items():
            if name == "FM7":
                continue
            with self.subTest(rule=name):
                self.assertIn(line, rules)
        self.assertIn(f"const LABELER_VERSION = '{gold_mod.LABELER_VERSION}'", text)
        self.assertEqual(gold_mod.LABELER_VERSION, "locate-label/5")
        self.assertEqual(gold_mod.LABELER_RULES[gold_mod.LABELER_VERSION], vocab.JUDGMENT_RULES)
        self.assertEqual(gold_mod.labeler_rules("locate-label/4"), 1)
        self.assertEqual(gold_mod.labeler_rules("locate-label/5"), 2)
        self.assertEqual(gold_mod.labeler_rules(None), 0)
        # PD7: the labeler's closeMatch line lists no junk value either
        self.assertNotIn("junk_value", text)
        self.assertIn("normalisation, renaming, unfolding, bundling (a junk value is none: R1's "
                      "three-way test, PD7)", " ".join(text.split()))
        # PD5 in the scope it gives and in the inventory it enumerates; PD9 on its claim list
        self.assertIn("each statement inside a proof is its own passage", " ".join(text.split()))
        self.assertIn("never a stated reason, which PD9 makes a passage of its own", rules)
        # the closeMatch gloss is R1's domain-extension test, not every closeMatch's
        self.assertNotIn("the two differing where both are defined", rules)
        self.assertIn("A case outside the passage's domain never makes a closeMatch; a difference "
                      "at a point where both are defined can", rules)
        # the rules' note and convention are the labeler's evidence
        self.assertIn("(where they say a location's `note` or `convention`, yours is in evidence)",
                      rules)

    def test_the_labeler_scopes_a_passage_as_the_inventory_does(self):
        """The bench's scope_error_rate compares the run's scope with the gold's: the two are
        given one definition (history and applications were non_math to the labeler and
        expository to the inventory)."""
        text = (HERE.parent / "workflows" / "locate-label.workflow.js").read_text(encoding="utf-8")
        rules = text[text.index("const RULES = `"):text.index("const huntTools")]
        rules = " ".join(rules.replace("\\`", "`").split())
        inventory = " ".join(self.rendered[vocab.LOCATE_INVENTORY]["worker.md"].split())
        start = inventory.index("`formal_math` — mathematics a library could state")
        end = inventory.index("`non_math` — anything else", start) + len("`non_math` — anything else")
        scopes = inventory[start:end]
        self.assertIn("proof narration and commentary (PD5)", scopes)
        self.assertIn(scopes, rules)
        self.assertNotIn("non_math (history", rules)
        vocabulary = " ".join((HERE.parent / "references" / "vocabulary.md")
                              .read_text(encoding="utf-8").split())
        self.assertIn("`expository` (history, motivation, applications told about mathematics, "
                      "and proof narration and commentary, PD5) `non_math` (anything else), as "
                      "the inventory prompt and the bench's labeler both give them", vocabulary)

    def test_the_policy_defaults_say_they_can_be_overridden(self):
        lines = self.rule_lines()
        self.assertIn("any ring where the passage has \u2102) is `broadMatch` (R2)", lines["PD1"])
        self.assertIn("over \u2115 where the passage has \u2124 is `narrowMatch` (R3)",
                      lines["PD2"])
        self.assertIn("is `narrowMatch`, never vacuous", lines["PD3"])
        self.assertIn("under stronger hypotheses that some of the passage's own cases meet", lines["PD4"])
        self.assertIn("is `narrowMatch` (R3)", lines["PD4"])
        # PD5-PD9, the maintainer's of 2026-10-20
        self.assertIn("are `formal_math` passages. Proof narration and commentary (what a proof "
                      "uses, how it goes) is none: the inventory scopes it `expository` or leaves "
                      "it out, so it is never located and has no status", lines["PD5"])
        self.assertIn("Inside a proof, each definition, theorem, equation or declarative "
                      "statement is a passage of its own", lines["PD5"])
        self.assertIn("a part of a coverage claim that alone states every clause is no part: it "
                      "is one location with its own relation", lines["PD6"])
        self.assertIn("is still a judgment: an `exactMatch` or `broadMatch` that rests on it "
                      "says why in its `note`", lines["PD7"])
        self.assertIn("closes by decidable arithmetic (`omega`, `norm_num`, `decide`)", lines["PD8"])
        self.assertIn("makes it `relatedMatch` (R5)", lines["PD8"])
        self.assertIn("a declarative mathematical statement that no neighbouring passage already "
                      "states", lines["PD9"])
        # R1: PD7's three-way test replaced its junk values
        self.assertNotIn("junk values", lines["R1"])
        # PD7's test for a value (the maintainer's, 2026-10-20): the passage's mathematics does
        # not depend on the value the declaration gives outside its domain, so riemannZeta at
        # its pole is R1's junk-value example again (40f364d had taken it out as the record's
        # call)
        self.assertIn("a junk or default value, given where the passage leaves f undefined, that "
                      "the passage's mathematics does not depend on (x/0 = 0, `riemannZeta` at "
                      "its pole s = 1), or an extra case that reduces to a tautology", lines["R1"])
        self.assertIn("a value the declaration gives where the passage leaves one undefined (a "
                      "junk or default value, R1) is trivial when the passage's mathematics does "
                      "not depend on it", lines["PD7"])
        self.assertIn("in the maintainer's words, \"it shouldn't matter what the value of "
                      "riemannZeta 1 is\" — so `riemannZeta` at s = 1 is a junk value against a "
                      "passage that defines the zeta function away from s = 1", lines["PD7"])
        # the test is of values: an extra case of the statement is trivial as a tautology, and
        # one with real content is still a generalisation, so the test never makes every
        # generalisation (whose extra cases the passage does not depend on either) an exactMatch
        self.assertIn("That test is for such values: an extra case of the statement itself (a "
                      "ring that is not a field, where the passage has a field) is trivial when it "
                      "reduces to a tautology, and one with real mathematical content makes a "
                      "generalisation, never a junk value.", lines["PD7"])
        for name in ("R1", "PD7"):
            for every_case in ("does not depend on f′'s value there",
                               "does not depend on the declaration's value there",
                               "a case outside the passage's domain is trivial (R1) when"):
                self.assertNotIn(every_case, lines[name])
        # PD10: one notion in two encodings is R1's encoding, no PD8 bridge
        for needle in ("one notion in two encodings is no bridge",
                       "(`IsCoprime m n` where the passage says m and n have no common factor "
                       "other than 1, linked by `Int.isCoprime_iff_gcd_eq_one`)",
                       "differs from the passage by an `encoding` (R1: a `closeMatch` when "
                       "nothing else differs)",
                       "is no PD8 bridge",
                       "It sharpens R1's encoding and limits PD8, which governs bridges between "
                       "statements, not between two definitions of one notion.",
                       # its exception: a link that is itself the passage's claim is no encoding
                       "Its exception is a link that is itself what the passage asserts: when the "
                       "lemma that links the two notions is the passage's own theorem, a "
                       "declaration stated with the notion whose definition already contains that "
                       "theorem's claim presupposes the passage, and it is `relatedMatch` (R5)."):
            self.assertIn(needle, lines["PD10"])
        # PD8 is about instantiation: R2's weakenings stay R2's
        self.assertIn("R2's weakenings (a strict derivative for a plain one, an iff for an if) are "
                      "no instantiation: PD8 leaves them to R2", lines["PD8"])
        self.assertIn("a strict derivative for a plain one", lines["R2"])
        for needle in ("neither is a junk value (PD7)",
                       "`riemannZeta` at its pole s = 1), or an extra case that reduces to a "
                       "tautology (p = q,",
                       "IsSquare 0 ↔ IsSquare 0) — it is `exactMatch`",
                       "some case outside X has real mathematical content, it is `broadMatch` "
                       "(R2), a generalisation",
                       "if f′(x) ≠ f(x) at some x where both are defined, it is `closeMatch`"):
            self.assertIn(needle, lines["R1"])
        vocabulary = " ".join((HERE.parent / "references" / "vocabulary.md")
                              .read_text(encoding="utf-8").split())
        self.assertIn("Each is a default, not a law of the format: it can be overridden",
                      vocabulary)
        self.assertIn("and to the bench's labeler (`workflows/locate-label.workflow.js`, whose "
                      "`LABELER_VERSION` it bumps), in one commit", vocabulary)
        self.assertIn("followed by a relabel of the bench's gold", vocabulary)
        self.assertIn("the bench's score refuses gold labelled under other judgment rules than "
                      "the runs it scores", vocabulary)
        # the maintainer's own words on PD7, attributed and verbatim, with his examples
        self.assertIn("**PD7 in the maintainer's words** (2026-10-20), verbatim:", vocabulary)
        self.assertIn("> A junk value means that X is exactly the same as Y, but the Lean case is "
                      "just defined over", vocabulary)
        self.assertIn("> generalization not a junk value.", vocabulary)
        self.assertIn("and, later the same day, of `riemannZeta` at s = 1: > RiemannZeta is hard "
                      "but I'd still classify it as a junk value personally since the mathematics "
                      "> here doesn't actually depend on the value of riemannZeta 1.", vocabulary)
        self.assertIn("> matter what the value of riemannZeta 1 is, and that is still the case.",
                      vocabulary)
        self.assertNotIn("is a judgment the note argues, not a settled example", vocabulary)
        # the test is of values, his first and last words; his second settles the extra cases
        self.assertIn("The test is of values, which his first and last words speak of (\"trivial "
                      "values … usually left undefined\", \"the value of riemannZeta 1\"), and not "
                      "of an extra case of the statement, which his second words settle",
                      vocabulary)
        self.assertIn("Its test for a value outside the passage's domain, the maintainer's, given "
                      "later the same day, is whether", vocabulary)
        self.assertNotIn("the maintainer's later the same day", vocabulary)
        # PD10's closeMatch is the relation table's, the convention the maintainer's
        self.assertIn("The maintainer named the difference, an encoding (R1); that it is a "
                      "`closeMatch` when nothing else differs is the relation table's definition "
                      "of `closeMatch`, the same up to a convention, not a further ruling of his.",
                      vocabulary)
        # nor is PD10's exception, which keeps R5's reading of a link that is the passage
        self.assertIn("Neither is PD10's exception, a link that is itself what the passage "
                      "asserts. His example links two definitions the passage takes as given",
                      vocabulary)
        self.assertIn("it is recorded as the exception so that PD10 does not reverse it, and it "
                      "awaits the maintainer's confirmation.", vocabulary)
        # which earlier rule or example each one replaces or sharpens
        for needle in ("PD5 sharpens the inventory's `expository` scope",
                       "PD6 sharpens R4 and the coverage claim of decision D1",
                       "PD7 replaces R1's \"junk values\"",
                       "PD8 sharpens R2's \"instantiation\" and R5's",
                       "PD9 sharpens the claim list (R4, FM1)",
                       "PD10 sharpens R1's `encoding` and limits PD8"):
            self.assertIn(needle, vocabulary)
        self.assertIn("(each overridable, never by you)", self.worker())

    def test_pd5_to_pd9_reach_every_prompt_that_acts_on_them(self):
        """Beside the rule lines word for word: each prompt's own instructions."""
        worker, challenger = self.worker(), self.worker(vocab.LOCATE_CHALLENGE)
        system = self.rendered[vocab.LOCATE]["worker-system.md"]
        inventory = self.rendered[vocab.LOCATE_INVENTORY]["worker.md"]
        # PD5: commentary is expository, a statement inside a proof is a passage of its own
        self.assertIn("**Only definitions, theorems, equations and declarative statements about "
                      "mathematical objects are `formal_math` passages** (the maintainer's PD5)",
                      inventory)
        self.assertIn("**Proof narration and commentary is no `formal_math` passage**", inventory)
        self.assertIn("write it as a passage scoped `expository`, which is never located and "
                      "gets no status, and otherwise leave it out", inventory)
        self.assertIn("(an equation or a declarative statement that asserts a fact is a "
                      "`theorem`, one that defines an object a `definition`)", inventory)
        self.assertIn("**Inside a proof, each definition, theorem, equation or declarative "
                      "statement is a passage of its own**", inventory)
        self.assertIn("and proof narration and commentary (PD5)", inventory)
        self.assertIn("Proof narration, history and motivation are no passages (PD5)", worker)
        # a narration passage the inventory scoped formal_math reaches the worker anyway: it
        # cannot re-cut it, so it ends unresolved with its cut_flag (J4), as vocabulary.md
        # section 1.11 documents
        self.assertIn("(proof narration or commentary, which PD5 makes no `formal_math` passage, "
                      "or a description), or it holds several statements the inventory did not "
                      "cut: that is the inventory's defect, which you cannot re-cut, so say so "
                      "with a `cut_flag`, one of `not_a_statement`, `several_statements`, and "
                      "name PD5 or the statements in `reason`", worker)
        self.assertIn('"cut_flag": "several_statements"', worker)
        vocabulary = " ".join((HERE.parent / "references" / "vocabulary.md")
                              .read_text(encoding="utf-8").split())
        self.assertIn("narration ends `unresolved`, its `reason` naming PD5 — exported as one "
                      "`UNRESOLVED` record with no status, counted in the unresolved rate",
                      vocabulary)
        # the challenger writes no claim list, so PD5 reaches it as the rule line only
        self.assertNotIn("never proof narration (PD5)", challenger)
        # PD6: a part that alone states every conjunct is no part
        self.assertIn("**A part that alone states every conjunct is no part** (PD6)", worker)
        self.assertIn("**Refute a claim one of whose parts alone states every conjunct** (PD6)",
                      challenger)
        self.assertIn("one of whose parts names every conjunct alone (PD6", system)
        self.assertNotIn("`part_clauses` give one part every claim", system)
        # PD7: junk values left closeMatch's list; the closeMatch example is no junk value
        self.assertNotIn("`junk_value`", worker)
        self.assertNotIn('"Lean returns 0 at n = 0"', worker + challenger)
        self.assertIn("its `note` says why each such case is trivial", worker)
        self.assertIn("A junk value is no convention (PD7)", worker)
        self.assertIn("a junk value is none (PD7)", challenger)
        self.assertIn("(a junk value is none: PD7)", system)
        self.assertIn('"convention": "normalisation"', challenger)
        self.assertNotIn('"convention": "junk_value"', challenger)
        # a record of the earlier rules: a challenge never raises it, so it is confirmed
        self.assertIn("A record whose `convention` is `junk_value` was judged before PD7", challenger)
        self.assertIn("so confirm it as it stands (naming no `convention`, or `junk_value` again)",
                      challenger)
        # all three of PD7's cases, the third a difference where both sides are defined
        self.assertIn("when one has real content, downgrade it to `broadMatch`", challenger)
        self.assertIn("when the two differ at a point where both are defined", challenger)
        self.assertIn("it was never a junk value: confirm it naming the real `convention` it "
                      "differs by, or downgrade it to the weaker relation it has", challenger)
        self.assertIn("a confirm may name another than the record's when the record's is wrong",
                      challenger)
        # PD6 at challenge time: the claim is refuted and the part stays a hasPart
        self.assertIn("a challenge never raises its `hasPart`, so the passage stays `partial` "
                      "until it is located again", challenger)
        # PD8: the bridge after instantiating is decidable arithmetic, or relatedMatch
        self.assertIn("makes it `relatedMatch`, not `broadMatch` (PD8)", worker)
        self.assertIn("a bridge through a named lemma makes it `relatedMatch` (PD8)", challenger)
        # PD9, reworded on 2026-10-21: a stated reason is a passage of its own, never a clause
        self.assertIn("a stated reason is a passage of its own, never a clause of the statement "
                      "it supports (PD9)", worker)
        for text in (worker, challenger):
            self.assertNotIn("makes X a claim only when", text)
        for text in (worker, challenger, inventory):
            self.assertNotRegex(text, r"\{\{")

    def test_pd7s_test_and_pd10_reach_every_prompt_that_acts_on_them(self):
        """The maintainer's answers of 2026-10-20 beside the rule lines: riemannZeta at s = 1
        is a junk value (exactMatch), and a lemma linking two definitions of one notion is an
        encoding (R1), not a PD8 bridge."""
        from bench import gold as gold_mod
        worker, challenger = self.worker(), self.worker(vocab.LOCATE_CHALLENGE)
        worker, challenger = " ".join(worker.split()), " ".join(challenger.split())
        labeler = (HERE.parent / "workflows" / "locate-label.workflow.js").read_text(encoding="utf-8")
        labeler = " ".join(labeler[labeler.index("const RULES = `"):labeler.index("const huntTools")]
                           .replace("\\`", "`").split())
        # PD7: the exactMatch instructions carry the test and the example
        tests_of_a_value = ("a junk or default value that the passage's mathematics does not "
                            "depend on, or an extra case that reduces to a tautology")
        self.assertIn("(Lean's x/0 = 0, `riemannZeta` at s = 1) still states it when it agrees "
                      "with the passage on the passage's domain and every case outside it is "
                      "trivial — " + tests_of_a_value + " —", worker)
        self.assertIn("(Lean's x/0 = 0, `riemannZeta` at s = 1) is judged by R1's three-way test "
                      "(PD7): an `exactMatch` when it agrees on the passage's domain and every "
                      "case outside it is trivial (" + tests_of_a_value + ")", challenger)
        self.assertIn("why each case outside the passage's domain is trivial ("
                      + tests_of_a_value + "), or which one has content (PD7)", labeler)
        # the dependence test is a test of values, never stated of every case outside the domain
        for text in (worker, challenger, labeler):
            self.assertNotIn("does not depend on the value there", text)
        # PD10 where each acts on PD8's bridge, PD8's own wording kept
        self.assertIn("makes it `relatedMatch`, not `broadMatch` (PD8). A lemma that only links "
                      "two definitions of one notion (`Int.isCoprime_iff_gcd_eq_one`, between "
                      "`IsCoprime` and \"no common factor other than 1\") is no such bridge: the "
                      "two differ by an `encoding` (PD10). A link that is itself what the passage "
                      "asserts is PD10's exception: the declaration presupposes the passage, and "
                      "it is `relatedMatch`.", worker)
        self.assertIn("a bridge through a named lemma makes it `relatedMatch` (PD8), and a lemma "
                      "that only links two definitions of one notion is no bridge: the two differ "
                      "by an `encoding` (PD10). A link that is itself what the passage asserts is "
                      "PD10's exception: the declaration presupposes the passage, and it is "
                      "`relatedMatch`.", challenger)
        self.assertIn("(PD8; R2's weakenings are no instantiation, and a lemma that only links two "
                      "definitions of one notion is no bridge but an encoding, PD10, unless that "
                      "link is itself what the passage asserts)", labeler)
        # PD10 is judgment: it adds no gate check, so the rules stay version 2 and the labeler
        # /5 (a /6 would label by the same judgment rules, LABELER_RULES)
        self.assertEqual(vocab.JUDGMENT_RULES, 2)
        self.assertEqual(gold_mod.LABELER_VERSION, "locate-label/5")
        self.assertEqual(gold_mod.labeler_rules(gold_mod.LABELER_VERSION), vocab.JUDGMENT_RULES)
        vocabulary = " ".join((HERE.parent / "references" / "vocabulary.md")
                              .read_text(encoding="utf-8").split())
        self.assertIn("**PD10 and PD7's test kept the labeler at `locate-label/5`**, and so did "
                      "PD10's exception, which joined them later the same day.", vocabulary)
        # what neither the version nor the rules hold: a rules-2 run of the earlier text
        self.assertIn("What neither version nor rules can hold is a run of judgment rules 2 "
                      "planned from the earlier text, whose prompts lack PD10 and PD7's test",
                      vocabulary)
        self.assertIn("one planned from a checkout of the earlier text is planned again from this "
                      "one before it is scored", vocabulary)
        self.assertIn("PD9's stated reasons and PD10's encodings are not checked mechanically",
                      vocabulary)

    @staticmethod
    def fix_lines() -> dict[str, str]:
        """The statement cut's prompt rules as references/vocabulary.md section 1.11 writes them."""
        text = (HERE.parent / "references" / "vocabulary.md").read_text(encoding="utf-8")
        start = text.index("**The statement cut's prompt rules**")
        block = text[start:text.index("**The failure modes**", start)]
        return {m.group(1): "- **Fix %s** — %s" % (m.group(1), " ".join(m.group(2).split()))
                for m in re.finditer(r"^- \*\*Fix (2a|2e|3a|4b|5a)\*\* — (.*?)(?=^\S|\Z)",
                                     block, re.M | re.S)}

    def test_the_statement_cuts_prompt_rules_are_word_for_word_in_all_three(self):
        """docs/proposals/statement-cut.md section 5: fixes 2a, 2e, 3a, 4b and 5a are written
        word for word in vocabulary.md, both locate prompts and the labeler."""
        lines = self.fix_lines()
        self.assertEqual(sorted(lines), ["2a", "2e", "3a", "4b", "5a"])
        labeler = (HERE.parent / "workflows" / "locate-label.workflow.js").read_text(encoding="utf-8")
        labeler = " ".join(labeler[labeler.index("const RULES = `"):labeler.index("const huntTools")]
                           .replace("\\`", "`").split())
        for name, line in sorted(lines.items()):
            for mode in (vocab.LOCATE, vocab.LOCATE_CHALLENGE):
                with self.subTest(fix=name, mode=mode):
                    self.assertIn(line, " ".join(self.worker(mode).split()))
            with self.subTest(fix=name, mode="labeler"):
                self.assertIn(line, labeler)
        proposal = (HERE.parent / "docs" / "proposals" / "statement-cut.md").read_text(
            encoding="utf-8")
        self.assertIn("The new prompt rules (fixes 2a, 2e, 3a, 4b and 5a)", proposal)
        # what each says, beside the words: the two-way test, PD7's test in the line, the
        # unfolding lemma, the defined object and the swapped roles, the special case
        self.assertIn("`r2`, how the passage follows from the declaration", lines["2a"])
        self.assertIn("states PD7's three-way test", lines["2e"])
        self.assertIn("`mem_*_iff`, `*_apply`, `*_def`", lines["3a"])
        self.assertIn("read it with its roles swapped", lines["4b"])
        self.assertIn("one query for its conclusion under a stronger hypothesis", lines["5a"])

    def test_pd9_carries_the_maintainers_rewording(self):
        """The answer of 2026-10-21 to question 5: a stated reason X is a passage of its own,
        in the words the proposal's section 3.4 gives every rule file."""
        proposal = (HERE.parent / "docs" / "proposals" / "statement-cut.md").read_text(
            encoding="utf-8")
        start = proposal.index("> **PD9** —")
        quoted = " ".join(line.lstrip("> ").strip() for line in
                          proposal[start:proposal.index("\n\n", start)].splitlines())
        self.assertEqual(self.rule_lines()["PD9"], "- " + quoted)
        self.assertIn("otherwise X is no passage and no clause", self.rule_lines()["PD9"])

    def test_the_rules_3_documents_reach_both_prompts(self):
        """Judgment rules 3's fields (the proposal's sections 1.3 and 1.4, and section 5's gate
        rows) are what the locate and challenge prompts ask for."""
        worker, challenger = self.worker(), self.worker(vocab.LOCATE_CHALLENGE)
        system = self.rendered[vocab.LOCATE]["worker-system.md"]
        for needle in (
                # J2: a part's relation to its conjuncts
                '"conjunct_relation": "broadMatch"',
                "**A `hasPart` of a compound statement names the conjuncts it states and its "
                "`conjunct_relation`**",
                # J3 and fix 4a: the bundle, and the under-claim it must not leave
                "**Write it whenever your parts so partition the conjuncts**",
                "its relation is its weakest part's, which the outcomes compute (you write none)",
                "Never put `coverage` on a simple statement",
                # fixes 2a and 2b: tests and instantiation on every strong location, ℕ for ℤ
                "**Every `exactMatch`, `closeMatch` and `broadMatch` writes its two-way test as "
                "`tests`**",
                "**required on every `exactMatch`, `closeMatch` and `broadMatch`, whatever its "
                "binders**",
                'when the `statement` or a conjunct says "integer", "integers" or "ℤ" and every '
                'numeric binder of the declaration is ℕ',
                # fix 5b: the absence's special case; J4: the cut flag
                '"special_case": {"library": "<id>"', "with a `cut_flag`, one of"):
            with self.subTest(needle=needle):
                self.assertIn(needle, worker)
        for needle in (
                # fix 2d: the challenger's own tests
                "**A verdict that keeps or leaves `exactMatch`, `closeMatch` or `broadMatch` "
                "gives your own two-way test as `tests`**",
                # section 1.4: a confirm's part relations, a refute's unstated and part reasons
                '"part_relations": {"<a part\'s location_key>": "broadMatch"',
                "none stronger than that part's own `conjunct_relation` (a challenge never "
                "raises a relation)",
                '"unstated": ["c2"]', '"part_reasons": {',
                '"The parts state it after unfolding or encoding" is a confirm',
                # fix 5b on an uphold, fix 6's fills and A3's conditional records
                '"special_case": {"reason":',
                "**Never leave a located passage with nothing.**", '"fills": {',
                "**When you overturn a `supporting` prominence to `subject` or `central`, judge "
                "in the same document every record of that passage your manifest marks "
                '`conditional: "prominence"`**',
                "without such an overturn, give a conditional record no verdict"):
            with self.subTest(needle=needle):
                self.assertIn(needle, challenger)
        # fix 2c: the longer generality list in every prompt that warns of it
        for phrase in vocab.CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT:
            said = f'"{phrase[:-1]}…"' if phrase.endswith("*") else f'"{phrase}"'
            for text in (worker, challenger, system):
                with self.subTest(phrase=phrase):
                    self.assertIn(said, text)
        for text in (worker, challenger, system):
            self.assertNotRegex(text, r"\{\{")

    def test_every_failure_mode_label_resolves_in_the_tree(self):
        """FM1-FM9 are cited by code, tests and the changelog: section 1.11 defines each."""
        text = (HERE.parent / "references" / "vocabulary.md").read_text(encoding="utf-8")
        for i in range(1, 10):
            with self.subTest(fm=i):
                self.assertRegex(text, rf"\n\| FM{i} \| ")

    def test_fm7_the_challenger_applies_d1_and_keeps_linked_declarations_together(self):
        line = self.rule_lines()["FM7"]
        for needle in ("check the record against the passage's clauses", "Apply D1",
                       "a bijection is the data form of a cardinality equation",
                       "Keep definitionally linked declarations consistent",
                       "your task holds every required location of a passage",
                       "`passage_locations`", "never a refute"):
            self.assertIn(needle, line)

    def test_fm1_the_challenger_reads_the_inventorys_clauses(self):
        worker = self.worker(vocab.LOCATE_CHALLENGE)
        self.assertIn("**The passage's clauses are the inventory's**, and you write no claim list "
                      "(a `claims` field is refused)", worker)
        self.assertIn("stand only when they name every clause", worker)
        self.assertNotIn("**First, write the claim list of each passage you judge**", worker)
        self.assertNotIn("the worker may have listed too few clauses", worker)
        self.assertNotIn(' "claims": {"<item>": [', worker)
        self.assertNotRegex(worker, r"\{\{")


if __name__ == "__main__":
    unittest.main()
