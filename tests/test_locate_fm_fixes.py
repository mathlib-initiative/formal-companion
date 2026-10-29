"""The smoke-3 audit's failure-mode fixes in the challenge plan and the outcomes, both ways.

New code, for docs/proposals/statement-cut.md section 5 (fixes 1a, 2d, 5b and the
three status anomalies' fix 6) and sections 1.2, 1.4, 1.5 and 2.5: each new rule
binds only what names it -- a run whose ``run.yaml`` says ``locate.status_rule: 2``
(set by hand here: no planner writes it before the flip), a challenge plan made
with ``conditional``, a document of judgment rules 3 -- and the same inputs without
it give the rows, statuses and record sets they always did.

* anomalies 1 and 2 (A1, A2): a located passage the challenge leaves with no
  counting location and no absence is owed a hunt under status rule 2 (challenge
  ``challenge_pending``, ``owed: "hunt"``, refused by the export), and a challenge
  document's ``fills`` give it an absence or replacement locations; under status
  rule 1 it is what it was, no status and exported;
* anomaly 3 (A3): a required prominence sample takes its passage's unrequired
  records into its task as conditional ones, whose verdicts apply only beside an
  overturn of that prominence to ``subject`` or ``central``;
* the verdict fields of judgment rules 3: a challenger's ``tests`` (fix 2d), a
  part's ``conjunct_relation`` (never raised), a refute's ``unstated`` and
  ``part_reasons`` (fix 1a), an uphold's ``special_case`` (fix 5b), read on a run of
  status rule 2 only, and a part held by its ``conjunct_relation`` (a strong part
  keeps its ``tests``);
* the rows of status rule 2 (section 1.5) and its counts.

The prompt rules of the same fixes (2a, 2e, 3a, 4b, 5a) are held word for word in
``tests/test_locate_judgment.py``.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
for _path in (SCRIPTS, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import _locate_fixtures as F  # noqa: E402
from cflib import vocab  # noqa: E402
from cflib.jsonio import read_json, write_json  # noqa: E402
from locate import challenge_plan as cplan, outcomes, records  # noqa: E402

from test_locate_bundle_status import set_status_rule  # noqa: E402
from test_locate_planner import Campaign, ws  # noqa: E402

EXT = records.EXTENSION

#: the counts every run of status rule 1 writes, exactly (status rule 2 adds three)
STATUS_1_COUNTS = {"passages", "by_outcome", "by_status", "by_library", "by_challenge",
                   "unresolved", "attempt_capped", "missing", "scoped_out", "problems", "rows"}


def _records(out_dir: Path) -> list[dict]:
    return [json.loads(line) for path in sorted((out_dir / "records").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


class _Challenged(Campaign):
    """``loc-1`` as the fixtures locate it, challenged by ``chl-1`` with chosen verdicts.

    Widget lemma's central theorem (``self.item``) has one ``hasPart`` location,
    required (a central passage's locations are refuted by default); its supporting
    definition (``self.basis``) an ``exactMatch`` basis beside a ``relatedMatch``.
    Planned as before the statement cut's flip, a run of status rule 1 unless a test
    names status rule 2 in its run.yaml by hand (``set_status_rule``).
    """

    #: a run of the earlier flow (its documents write their own claim lists), as the
    #: planner planned it before the statement cut's flip
    BEFORE_THE_FLIP = True

    def setUp(self):
        super().setUp()
        self.locate()
        formal = F.frozen_formal_math(self.paths, F.W1)
        self.item, self.basis = formal[1]["item"], formal[2]["item"]

    def challenge_with(self, verdict=None, fills=None, *extra):
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-challenge",
                            "--sourceset", F.SS, "--universe", F.U, "--challenge-of", "loc-1",
                            "--run-id", "chl-1", *extra)
        self.assertEqual(code, 0, out + err)
        self.write_challenge(verdict, fills)

    def write_challenge(self, verdict=None, fills=None):
        docs = F.challenge_documents(self.paths, "chl-1", verdict=verdict)
        for doc in docs.values():
            items = {v["item"] for v in doc["verdicts"]}
            mine = {item: fill for item, fill in (fills or {}).items() if item in items}
            if mine:
                doc["fills"] = mine
        if self.paths.corpus_run_dir("chl-1").exists():
            shutil.rmtree(self.paths.corpus_run_dir("chl-1"))
        F.write_harvest(self.paths, "chl-1", docs)

    def refuting(self, item, action="refute", relation=None):
        def verdict(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "location" and rec["item"] == item:
                v.update(action=action, witness="it states another widget's lemma")
                if relation:
                    v["relation"] = relation
            return v
        return verdict

    def built(self):
        return outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])

    def row(self, item=None, built=None):
        built = built or self.built()
        return next(r for r in built["outcomes"] if r["item"] == (item or self.item))

    def export(self, set_id="set-1"):
        return ws(self.root, "export", "literature-links", "--run", "loc-1", "--challenge-run",
                  "chl-1", "--set-id", set_id)

    def refused(self, set_id="set-1") -> dict:
        report = read_json(self.paths.export_report_json(set_id))
        return {r["item"]: r["reason"] for r in report["refused"]}


ABSENT_FILL = {"outcome": "absent",
               "receipt": {"queries": ["gadget size order divisibility", "widget order divides",
                                       "spanning gadget cardinality"],
                           "candidates": [],
                           "query_clauses": {"gadget size order divisibility": ["c1"],
                                             "widget order divides": ["c1"],
                                             "spanning gadget cardinality": ["c1"]},
                           "special_case": {"reason": "no library bounds a gadget's size"}}}
LOCATED_FILL = {"outcome": "located",
                "locations": [F._loc("alpha", "Widget.exists_spanning_gadget", "exactMatch")]}


class TestTheOwedHunt(_Challenged):
    """Anomalies 1 and 2: a verdict that leaves a located passage with nothing that counts."""

    def test_status_rule_1_leaves_it_as_it_was(self):
        self.challenge_with(self.refuting(self.item))
        built = self.built()
        row = self.row(built=built)
        self.assertEqual((row["outcome"], row["locations"], row["status"]), ("located", [], None))
        self.assertEqual(row["challenge"], "downgraded")
        self.assertNotIn("owed", row)
        self.assertFalse(row.get("problem"))
        self.assertEqual(set(built["counts"]), STATUS_1_COUNTS)
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        self.assertNotIn(self.item, self.refused())

    def test_status_rule_2_owes_it_a_hunt(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge_with(self.refuting(self.item))
        built = self.built()
        row = self.row(built=built)
        self.assertIsNone(row["status"])
        self.assertEqual((row["challenge"], row["owed"]), ("challenge_pending", "hunt"))
        self.assertIn("owed a hunt", row["problem"])
        self.assertEqual(outcomes.outcome_row(row)["owed"], "hunt")
        self.assertEqual(built["counts"]["owed_hunt"], 1)
        code, out, err = self.export()
        self.assertEqual(code, 1, out + err)
        self.assertIn("owed a hunt", self.refused()[self.item])

    def test_a_downgrade_to_related_match_owes_one_too(self):
        """Anomaly 1's mechanism: the basis downgraded, its relatedMatch sibling unjudged."""
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge_with(self.refuting(self.basis, "downgrade", "relatedMatch"))
        row = self.row(self.basis)
        self.assertEqual({l["relation"] for l in row["locations"]}, {"relatedMatch"})
        self.assertEqual(row.get("owed"), "hunt")

    def test_a_passage_that_still_counts_owes_nothing(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge_with()
        built = self.built()
        self.assertEqual(built["counts"]["owed_hunt"], 0)
        self.assertFalse(any(r.get("owed") for r in built["outcomes"]))

    def test_an_absent_fill_records_the_challengers_absence(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge_with(self.refuting(self.item), {self.item: ABSENT_FILL})
        row = self.row()
        self.assertEqual((row["outcome"], row["status"]), ("absent", "not_formalized"))
        self.assertEqual(row["absence"]["challenge"], "survived")
        self.assertEqual(row["absence"]["receipt"]["special_case"],
                         ABSENT_FILL["receipt"]["special_case"])
        self.assertEqual(row["fill"], {"outcome": "absent", "challenge_run": "chl-1"})
        self.assertNotIn("owed", row)
        self.assertFalse(row.get("problem"))
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        recs = [r for r in _records(self.paths.export_dir("set-1"))
                if r["extensions"][EXT]["item"] == self.item]
        self.assertEqual([(r["relation"], r["status"]) for r in recs],
                         [("noMatch", "not_formalized")])
        self.assertEqual(recs[0]["extensions"][EXT]["receipt"]["queries"], 3)

    def test_a_located_fill_brings_the_challengers_locations(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge_with(self.refuting(self.item), {self.item: LOCATED_FILL})
        row = self.row()
        self.assertEqual([(l["declaration"], l["challenge"]) for l in row["locations"]],
                         [("Widget.exists_spanning_gadget", "survived")])
        self.assertEqual((row["outcome"], row["status"]), ("located", "formalized"))
        self.assertNotIn("owed", row)

    def test_status_rule_1_applies_no_fill(self):
        self.challenge_with(self.refuting(self.item), {self.item: ABSENT_FILL})
        built = self.built()
        row = self.row(built=built)
        self.assertEqual((row["outcome"], row["status"]), ("located", None))
        self.assertNotIn("fill", row)
        self.assertTrue(any("no fill binds" in p for p in built["problems"]), built["problems"])

    def test_a_fill_on_a_passage_that_still_counts_is_ignored(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge_with(None, {self.item: ABSENT_FILL})
        row = self.row()
        self.assertEqual(row["outcome"], "located")
        self.assertIn("fill names a passage its verdicts did not leave", row["problem"])

    def test_a_fill_for_no_passage_of_the_run_is_named(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge_with(None)
        docs = F.challenge_documents(self.paths, "chl-1")
        next(iter(docs.values()))["fills"] = {"In-text #00000000": ABSENT_FILL}
        shutil.rmtree(self.paths.corpus_run_dir("chl-1"))
        F.write_harvest(self.paths, "chl-1", docs)
        built = self.built()
        self.assertTrue(any("which is no passage of run loc-1" in p for p in built["problems"]))


class TestConditionalRecords(_Challenged):
    """Anomaly 3: a prominence sample goes in the same task as all of its passage's records."""

    def plan(self, conditional):
        return cplan.plan(self.paths, ["loc-1"], seed=7, sample_rate=1.0, strong_sample_rate=0.0,
                          absence_sample_rate=0.0, conditional=conditional)

    def test_the_plan_marks_them_beside_a_required_prominence(self):
        built = self.plan(True)
        recs = built["records"]
        prominent = {(r["source_id"], r["item"]) for r in recs if r["target"] == "prominence"
                     and r["planned"] == "required" and r["prominence"] == "supporting"}
        self.assertTrue(prominent)
        marked = [r for r in recs if r.get("conditional")]
        self.assertTrue(marked)
        for rec in marked:
            self.assertEqual(rec["conditional"], "prominence")
            self.assertIn(rec["target"], ("location", "absence", "unresolved"))
            self.assertNotEqual(rec["planned"], "required")
            self.assertIn((rec["source_id"], rec["item"]), prominent)
        for rec in recs:
            if rec["planned"] == "required" or (rec["source_id"], rec["item"]) not in prominent:
                self.assertNotIn("conditional", rec)
        # each in the batch of its passage's prominence record
        where = {r["key"]: n for n, batch in enumerate(built["batches"]) for r in batch}
        for rec in marked:
            (prom,) = [r for r in recs if r["target"] == "prominence"
                       and (r["source_id"], r["item"]) == (rec["source_id"], rec["item"])]
            self.assertEqual(where[rec["key"]], where[prom["key"]])
        doc = cplan.plan_document("chl-x", built, seed=7, sample_rate=1.0, task_of={},
                                  challenged_runs=["loc-1"], strong_sample_rate=0.0,
                                  absence_sample_rate=0.0)
        rows = {r["key"]: r for r in doc["records"]}
        for rec in marked:
            self.assertEqual(rows[rec["key"]]["conditional"], "prominence")
            self.assertEqual(rows[rec["key"]]["planned"], rec["planned"])
        self.assertEqual(sum(1 for r in doc["records"] if "conditional" in r), len(marked))

    def test_without_it_the_plan_is_what_it_was(self):
        on, off = self.plan(True), self.plan(False)
        default = cplan.plan(self.paths, ["loc-1"], seed=7, sample_rate=1.0,
                             strong_sample_rate=0.0, absence_sample_rate=0.0)
        self.assertLess(vocab.JUDGMENT_RULES, vocab.RULES_STATEMENT_CUT)      # before the flip
        with mock.patch.object(vocab, "JUDGMENT_RULES", vocab.RULES_STATEMENT_CUT):
            flipped = cplan.plan(self.paths, ["loc-1"], seed=7, sample_rate=1.0,
                                 strong_sample_rate=0.0, absence_sample_rate=0.0)
        self.assertEqual(flipped["records"], on["records"])          # a plan made now marks
        for built in (off, default):
            self.assertFalse(any("conditional" in r for r in built["records"]))
            self.assertEqual(sum(len(b) for b in built["batches"]),
                             sum(1 for r in built["records"] if r["planned"] == "required"))
        self.assertGreater(sum(len(b) for b in on["batches"]),
                           sum(len(b) for b in off["batches"]))
        strip = [{k: v for k, v in r.items() if k != "conditional"} for r in on["records"]]
        self.assertEqual(strip, off["records"])

    def mark(self, item, *, conditional=True):
        """chl-1 planned as today at rates 0 (the basis alone required), then, as a plan
        made with ``conditional`` that drew ``item``'s prominence would write it, that
        prominence required and its sampled-out relatedMatch location conditional."""
        self.challenge_with(None, None, "--sample-rate", "0", "--strong-sample-rate", "0",
                            "--absence-sample-rate", "0")
        path = self.paths.run_dir("chl-1") / "challenge-plan.json"
        plan = read_json(path)
        mine = [r for r in plan["records"] if r["item"] == item]
        (task,) = {r["task_id"] for r in mine if r["planned"] == "required"}
        (prom,) = [r for r in mine if r["target"] == "prominence"]
        prom.update(planned="required", task_id=task)
        (rec,) = [r for r in mine if r["target"] == "location" and r["planned"] != "required"]
        if conditional:
            rec.update(conditional="prominence", task_id=task)
        write_json(path, plan, indent=1, sort_keys=True)
        return rec

    def verdicts(self, item, key, *, overturn: bool, judge: bool):
        docs = F.challenge_documents(self.paths, "chl-1")
        for doc in docs.values():
            if any(v["item"] == item for v in doc["verdicts"]):
                doc["verdicts"].append(
                    {"item": item, "target": "prominence", "witness": "the article states it",
                     **({"action": "overturn", "prominence": "central"} if overturn
                        else {"action": "uphold"})})
                if judge:
                    doc["verdicts"].append({"item": item, "target": "location",
                                            "location_key": key, "action": "confirm",
                                            "witness": "it states the definition"})
        shutil.rmtree(self.paths.corpus_run_dir("chl-1"))
        F.write_harvest(self.paths, "chl-1", docs)

    def located(self, item, key):
        built = self.built()
        row = self.row(item, built)
        return next(l for l in row["locations"] if l["location_key"] == key), row, built

    def test_beside_an_overturn_its_verdict_applies(self):
        rec = self.mark(self.basis)
        self.verdicts(self.basis, rec["location_key"], overturn=True, judge=True)
        loc, row, built = self.located(self.basis, rec["location_key"])
        self.assertEqual(row["prominence"], "central")
        self.assertEqual(loc["challenge"], "survived")
        self.assertFalse([p for p in built["problems"] if "conditional" in p], built["problems"])
        self.assertFalse(row.get("problem"))

    def test_an_overturn_without_its_verdict_reopens_it_as_before(self):
        rec = self.mark(self.basis)
        self.verdicts(self.basis, rec["location_key"], overturn=True, judge=False)
        loc, row, _built = self.located(self.basis, rec["location_key"])
        self.assertEqual(loc["challenge"], "challenge_pending")
        self.assertEqual(row["challenge"], "challenge_pending")

    def test_without_an_overturn_its_verdict_is_ignored(self):
        rec = self.mark(self.basis)
        self.verdicts(self.basis, rec["location_key"], overturn=False, judge=True)
        loc, row, built = self.located(self.basis, rec["location_key"])
        self.assertEqual(row["prominence"], "supporting")
        self.assertEqual(loc["challenge"], rec["planned"])
        self.assertTrue(any("conditional location record" in p for p in built["problems"]),
                        built["problems"])

    def test_a_record_not_marked_conditional_is_ignored_as_ever(self):
        rec = self.mark(self.basis, conditional=False)
        self.verdicts(self.basis, rec["location_key"], overturn=True, judge=True)
        loc, _row, built = self.located(self.basis, rec["location_key"])
        self.assertEqual(loc["challenge"], "challenge_pending")
        self.assertTrue(any("the verdict is ignored" in p for p in built["problems"]))

def _row(**extra) -> dict:
    """A located supporting theorem passage with two hasPart parts, as the outcomes hold it."""
    row = {"source_id": "s", "item": "In-text #0000c0de", "outcome": "located",
           "missing": False, "prominence": "supporting", "passage_kind": "theorem",
           "absence": None, "refuted": [], "runs": {"challenge": None}, "problem": None,
           "conjuncts": [{"id": "c1", "text": "a widget has a gadget"},
                         {"id": "c2", "text": "two gadgets of a widget are equal"}],
           "locations": [{"location_key": k, "library": "alpha", "declaration": name,
                          "relation": "hasPart", "rank": r, "proof_status": "proved",
                          "statement": False, "clauses": [c], "conjunct_relation": rel,
                          "step": "the conjunct it states"}
                         for r, (k, name, c, rel) in enumerate(
                             (("a" * 64, "W.exists", "c1", "exactMatch"),
                              ("b" * 64, "W.unique", "c2", "broadMatch")), 1)],
           "coverage": {"complete": True, "parts": ["a" * 64, "b" * 64], "note": "n",
                        "challenge": None},
           "coverage_refuted": None}
    row.update(extra)
    return row


def _challenge(row, verdicts, *, status_rule=2, planned_extra=None):
    """Apply ``verdicts`` ({target or location key: verdict}) to ``row`` and derive it."""
    sid, item = row["source_id"], row["item"]
    ckey = outcomes.record_key(vocab.COVERAGE_TARGET, sid, item)
    planned = {ckey: {"planned": "required", "parts": list(row["coverage"]["parts"]),
                      "challenge_run": "chl"} if row.get("coverage") else {}}
    for loc in row["locations"]:
        planned[loc["location_key"]] = {"planned": "required", "relation": loc["relation"],
                                        "challenge_run": "chl"}
    planned.update(planned_extra or {})
    out = {}
    for key, verdict in verdicts.items():
        out[ckey if key == "coverage" else key] = {"witness": "w", "challenge_run": "chl",
                                                   **verdict}
    problems: list[str] = []
    outcomes._apply_challenge(row, planned, out, None, None, problems, status_rule=status_rule)
    outcomes._derive(row, problems, status_rule=status_rule)
    return row, problems


class TestTheBundlesRelation(unittest.TestCase):
    """Section 2.1: the weakest part's, a confirm's part relations replacing the parts' own."""

    def test_the_weakest_part(self):
        row, problems = _challenge(_row(), {"coverage": {"action": "confirm"},
                                            "a" * 64: {"action": "confirm"},
                                            "b" * 64: {"action": "confirm"}})
        self.assertEqual(problems, [])
        self.assertEqual(row["coverage"]["relation"], "broadMatch")
        self.assertEqual((row["status"], row["challenge"]), ("formalized", "survived"))

    def test_a_confirms_part_relations_never_raise_one(self):
        row, _ = _challenge(_row(), {"coverage": {"action": "confirm", "part_relations": {
            "a" * 64: "closeMatch", "b" * 64: "exactMatch"}}})
        self.assertEqual(row["coverage"]["relation"], "broadMatch")
        self.assertEqual(row["coverage_part_relations"],
                         {"a" * 64: "closeMatch", "b" * 64: "exactMatch"})
        row, _ = _challenge(_row(), {"coverage": {"action": "confirm", "part_relations": {
            "a" * 64: "closeMatch", "b" * 64: "broadMatch"}}})
        self.assertEqual(row["coverage"]["relation"], "broadMatch")
        exact = _row()
        exact["locations"][1]["conjunct_relation"] = "exactMatch"
        row, _ = _challenge(exact, {"coverage": {"action": "confirm", "part_relations": {
            "a" * 64: "closeMatch", "b" * 64: "exactMatch"}}})
        self.assertEqual(row["coverage"]["relation"], "closeMatch")

    def test_bundle_relation(self):
        parts = [{"location_key": "a", "conjunct_relation": "exactMatch"},
                 {"location_key": "b", "conjunct_relation": "closeMatch"}]
        self.assertEqual(outcomes.bundle_relation(parts), "closeMatch")
        self.assertEqual(outcomes.bundle_relation(parts, {"a": "broadMatch"}), "broadMatch")
        self.assertEqual(outcomes.bundle_relation(parts, {"b": "exactMatch"}), "closeMatch")
        self.assertIsNone(outcomes.bundle_relation([{"location_key": "a"}, parts[1]]))
        self.assertEqual(outcomes.bundle_relation(
            [{"location_key": "a", "conjunct_relation": "narrowMatch"}, parts[0]]),
            "narrowMatch")

    def test_what_no_bundle_stands_on(self):
        simple = _row(conjuncts=None)
        narrow = _row()
        narrow["locations"][0]["conjunct_relation"] = "narrowMatch"
        bare = _row()
        bare["locations"][0].pop("conjunct_relation")
        for row, why in ((simple, "only on a compound statement"),
                         (narrow, "a narrowMatch of its conjuncts"),
                         (bare, "names no conjunct_relation")):
            with self.subTest(why=why):
                got, problems = _challenge(row, {"coverage": {"action": "confirm"}})
                self.assertIn(why, got["problem"])
                self.assertEqual(got["status"], "partial")
                self.assertNotIn("relation", got["coverage"])

    def test_status_rule_1_reads_the_same_claim_as_d1(self):
        for row in (_row(), _row(conjuncts=None)):
            got, problems = _challenge(row, {"coverage": {"action": "confirm"}}, status_rule=1)
            self.assertEqual(problems, [])
            self.assertEqual(set(got["coverage"]), {"complete", "parts", "note", "challenge"})
            self.assertEqual(got["status"], "formalized")


class TestTheVerdictFieldsOfRules3(unittest.TestCase):
    """Fixes 1a, 2d and 5b, and J2's part relation, as the outcomes keep them."""

    def test_a_challengers_tests_replace_the_workers(self):
        row = _row(coverage=None, locations=[{
            "location_key": "c" * 64, "library": "alpha", "declaration": "W.thm",
            "relation": "exactMatch", "rank": 1, "proof_status": "proved", "statement": False,
            "clauses": ["c1"], "tests": {"r2": "the worker's", "r3": "the worker's"}}])
        tests = {"r2": "the passage is the lemma at the widget", "r3": "no extra hypothesis"}
        got, _ = _challenge(row, {"c" * 64: {"action": "confirm", "tests": tests}})
        self.assertEqual(got["locations"][0]["tests"], tests)

    def test_a_downgrade_leaves_no_field_its_relation_does_not_take(self):
        def exact():
            return _row(coverage=None, locations=[{
                "location_key": "c" * 64, "library": "alpha", "declaration": "W.thm",
                "relation": "exactMatch", "rank": 1, "proof_status": "proved", "statement": False,
                "clauses": ["c1", "c2"], "tests": {"r2": "a", "r3": "b"}}])
        tests = {"r2": "the existence conjunct is the lemma at W", "r3": "nothing more at W"}
        # to a strong part: a strong location still (the gate's held_label), with the
        # challenger's own tests
        got, _ = _challenge(exact(), {"c" * 64: {
            "action": "downgrade", "relation": "hasPart", "clauses": ["c1"],
            "conjunct_relation": "exactMatch", "step": "the existence conjunct", "tests": tests}})
        loc = got["locations"][0]
        self.assertEqual((loc["relation"], loc["conjunct_relation"]), ("hasPart", "exactMatch"))
        self.assertEqual(loc["tests"], tests)
        # to a part of a special case, or to a part with no conjunct relation: no tests
        for given in ({"conjunct_relation": "narrowMatch"}, {}):
            with self.subTest(given=given):
                got, _ = _challenge(exact(), {"c" * 64: {
                    "action": "downgrade", "relation": "hasPart", "clauses": ["c1"],
                    "step": "the existence conjunct", "tests": tests, **given}})
                self.assertNotIn("tests", got["locations"][0])
        got, _ = _challenge(exact(), {"c" * 64: {"action": "downgrade", "relation": "narrowMatch",
                                                 "clauses": ["c1"], "step": "at a finite W"}})
        self.assertNotIn("tests", got["locations"][0])
        got, _ = _challenge(_row(coverage=None), {"a" * 64: {"action": "downgrade",
                                                              "relation": "relatedMatch"}})
        # by key, not by rank: a downgrade moves its card below every card it is now weaker
        # than (R-SPECIFIC's strength half, outcomes._renumbered), so the relatedMatch is last
        downgraded = next(l for l in got["locations"] if l["location_key"] == "a" * 64)
        self.assertEqual((downgraded["relation"], downgraded["rank"]),
                         ("relatedMatch", len(got["locations"])))
        self.assertNotIn("conjunct_relation", downgraded)
        self.assertNotIn("step", downgraded)

    def test_a_strong_part_keeps_its_tests(self):
        """A part whose conjunct_relation is strong is held as a strong location (the gate's
        reading, the proposal's J4 and section 1.4), so a confirm keeps its tests: the
        challenger's when it gives them, else the worker's; a confirm that weakens the part
        to a narrowMatch drops them, as an unchallenged narrowMatch part never has them."""
        worker_tests = {"r2": "the worker's", "r3": "the worker's"}
        mine = {"r2": "existence is the lemma at W", "r3": "no extra hypothesis at W"}

        def row():
            got = _row(coverage=None)
            got["locations"][0]["tests"] = dict(worker_tests)
            return got
        got, problems = _challenge(row(), {"a" * 64: {"action": "confirm", "tests": mine}})
        self.assertEqual(got["locations"][0]["tests"], mine)
        self.assertEqual(problems, [])
        got, _ = _challenge(row(), {"a" * 64: {"action": "confirm"}})
        self.assertEqual(got["locations"][0]["tests"], worker_tests)
        got, _ = _challenge(row(), {"a" * 64: {"action": "confirm", "tests": mine,
                                               "conjunct_relation": "narrowMatch"}})
        self.assertEqual(got["locations"][0]["conjunct_relation"], "narrowMatch")
        self.assertNotIn("tests", got["locations"][0])
        self.assertEqual(outcomes.held_relation(_row()["locations"][1]), "broadMatch")
        self.assertEqual(outcomes.held_relation({"relation": "hasPart"}), "hasPart")
        self.assertEqual(outcomes.held_relation({"relation": "closeMatch"}), "closeMatch")

    def test_a_part_relation_is_weakened_never_raised(self):
        got, problems = _challenge(_row(), {"b" * 64: {"action": "confirm",
                                                       "conjunct_relation": "narrowMatch"}})
        self.assertEqual(got["locations"][1]["conjunct_relation"], "narrowMatch")
        got, problems = _challenge(_row(), {"b" * 64: {"action": "confirm",
                                                       "conjunct_relation": "exactMatch"}})
        self.assertEqual(got["locations"][1]["conjunct_relation"], "broadMatch")
        self.assertIn("the conjunct relation 'exactMatch', stronger than its own 'broadMatch'",
                      got["problem"])
        # a value that is no conjunct relation is named as that, not as a stronger one
        got, problems = _challenge(_row(), {"b" * 64: {"action": "confirm",
                                                       "conjunct_relation": "relatedMatch"}})
        self.assertEqual(got["locations"][1]["conjunct_relation"], "broadMatch")
        self.assertIn("the conjunct relation 'relatedMatch', which is none of broadMatch, "
                      "closeMatch, exactMatch, hasPart, narrowMatch", got["problem"])
        self.assertNotIn("stronger", got["problem"])
        # judgment rules 4's hasPart (a part of its conjunct) is a weaker one, and kept
        got, problems = _challenge(_row(), {"b" * 64: {"action": "confirm",
                                                       "conjunct_relation": "hasPart"}})
        self.assertEqual(got["locations"][1]["conjunct_relation"], "hasPart")
        self.assertNotIn("tests", got["locations"][1])

    def test_a_close_match_parts_convention_is_kept(self):
        row = _row(coverage=None)
        row["locations"][0].update(conjunct_relation="closeMatch", convention="encoding")
        got, _ = _challenge(row, {"a" * 64: {"action": "confirm"}})
        self.assertEqual(got["locations"][0]["convention"], "encoding")

    def test_a_refute_keeps_what_it_names(self):
        got, _ = _challenge(_row(), {"coverage": {
            "action": "refute", "unstated": ["c2"],
            "part_reasons": {"a" * 64: "states existence only", "b" * 64: "bounds the size"}}})
        self.assertIsNone(got["coverage"])
        self.assertEqual(got["coverage_refuted"]["unstated"], ["c2"])
        self.assertEqual(set(got["coverage_refuted"]["part_reasons"]), {"a" * 64, "b" * 64})

    def test_an_uphold_keeps_its_special_case(self):
        sid, item = "s", "In-text #0000c0de"
        row = _row(coverage=None, locations=[], outcome="absent",
                   absence={"receipt": {"queries": ["q"]}, "challenge": None})
        key = outcomes.record_key("absence", sid, item)
        special = {"reason": "no library states a special case"}
        got, _ = _challenge(row, {key: {"action": "uphold", "queries": ["a", "b", "c"],
                                        "query_clauses": {"a": ["c1"]},
                                        "special_case": special}},
                            planned_extra={key: {"planned": "required", "outcome": "absent",
                                                 "challenge_run": "chl"}})
        self.assertEqual(got["challenge_receipt"]["special_case"], special)
        self.assertIn("challenge_receipt", outcomes.outcome_row(got))

    def test_a_rules_2_verdict_leaves_the_row_it_always_did(self):
        """No tests, conjunct_relation, part_relations or special_case: nothing new appears."""
        row = _row(coverage=None, conjuncts=None)
        for loc in row["locations"]:
            loc.pop("conjunct_relation")
        before = copy.deepcopy(row)
        got, _ = _challenge(row, {"a" * 64: {"action": "confirm"}}, status_rule=1)
        self.assertEqual(got["locations"][0], {**before["locations"][0], "challenge": "survived"})
        for key in ("coverage_part_relations", "challenge_receipt", "owed", "fill"):
            self.assertNotIn(key, got)

    def test_status_rule_1_reads_none_of_rules_3s_verdict_fields(self):
        """The proposal's section 7: every new field appears only under the versions that name
        it.  A verdict of a run of status rule 1 that carries rules 3's fields -- which its
        gate (rules 1 or 2) does not refuse -- leaves the row the same verdict without them
        leaves: no tests, conjunct relation, part relations, unstated, part reasons or special
        case, and no problem for a stronger conjunct relation."""
        extra = {"tests": {"r2": "a two-way test", "r3": "of rules 3"},
                 "conjunct_relation": "narrowMatch"}

        def rule_1_row():
            row = _row(conjuncts=None)
            for loc in row["locations"]:
                loc.pop("conjunct_relation")
            return row
        plain = {"a" * 64: {"action": "confirm"}, "coverage": {"action": "confirm"}}
        rich = {"a" * 64: {"action": "confirm", **extra},
                "b" * 64: {"action": "downgrade", "relation": "narrowMatch", "step": "at W",
                           **extra},
                "coverage": {"action": "confirm", "part_relations": {"a" * 64: "broadMatch"}}}
        plain["b" * 64] = {k: v for k, v in rich["b" * 64].items() if k not in extra}
        want, want_problems = _challenge(rule_1_row(), plain, status_rule=1)
        got, problems = _challenge(rule_1_row(), rich, status_rule=1)
        self.assertEqual((got, problems), (want, want_problems))
        self.assertNotIn("coverage_part_relations", got)
        refute = {"coverage": {"action": "refute", "unstated": ["c2"],
                               "part_reasons": {"a" * 64: "r", "b" * 64: "s"}}}
        got, _ = _challenge(rule_1_row(), refute, status_rule=1)
        self.assertEqual(set(got["coverage_refuted"]) & {"unstated", "part_reasons"}, set())
        sid, item = "s", "In-text #0000c0de"
        key = outcomes.record_key("absence", sid, item)
        for status_rule, kept in ((1, False), (2, True)):
            row = _row(coverage=None, locations=[], outcome="absent",
                       absence={"receipt": {"queries": ["q"]}, "challenge": None})
            got, _ = _challenge(row, {key: {"action": "uphold", "queries": ["a", "b", "c"],
                                            "special_case": {"reason": "none is stated"}}},
                                status_rule=status_rule,
                                planned_extra={key: {"planned": "required", "outcome": "absent",
                                                     "challenge_run": "chl"}})
            self.assertEqual("special_case" in got["challenge_receipt"], kept)


class TestTheRowsOfStatusRule2(_Challenged):
    """Section 1.5: the statement, conjuncts and shape, the cut flag; and the counts."""

    def answer_unresolved(self, flag):
        docs = F.locate_documents(self.paths, "loc-1")
        for doc in docs.values():
            for passage in doc["passages"]:
                if passage["outcome"] == "unresolved":
                    passage["cut_flag"] = flag
        shutil.rmtree(self.paths.corpus_run_dir("loc-1"))
        F.write_harvest(self.paths, "loc-1", docs)

    def test_status_rule_2_rows(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.answer_unresolved("several_statements")
        built = outcomes.build(self.paths, "loc-1")
        for row in built["outcomes"]:
            self.assertEqual(row.get("statement"), "informal restatement")
        (unresolved,) = [r for r in built["outcomes"] if r["outcome"] == "unresolved"]
        self.assertEqual(unresolved["cut_flag"], "several_statements")
        self.assertEqual(outcomes.outcome_row(unresolved)["cut_flag"], "several_statements")
        counts = built["counts"]
        self.assertEqual(set(counts) - STATUS_1_COUNTS, {"owed_hunt", "by_cut_flag", "bundles"})
        self.assertEqual(counts["by_cut_flag"], {"several_statements": 1})
        self.assertEqual(counts["bundles"], {"written": 0, "survived": 0, "by_relation": {}})

    def test_status_rule_1_rows(self):
        self.answer_unresolved("several_statements")
        built = outcomes.build(self.paths, "loc-1")
        for row in built["outcomes"]:
            out = outcomes.outcome_row(row)
            for key in ("statement", "conjuncts", "shape", "cut_flag", "owed", "fill"):
                self.assertNotIn(key, out)
        self.assertEqual(set(built["counts"]), STATUS_1_COUNTS)

    def test_a_document_of_status_rule_1_yields_its_rows_whatever_keys_it_carries(self):
        """A rules-1 or rules-2 gate does not refuse a key it does not know, so a locate
        document of a run of status rule 1 may carry ``tests`` and ``conjunct_relation``:
        its rows are the ones the same document without them gives, and only a run of
        status rule 2 keeps them (the proposal's section 7)."""
        want = outcomes.build(self.paths, "loc-1")
        docs = F.locate_documents(self.paths, "loc-1")
        for doc in docs.values():
            for passage in doc["passages"]:
                for loc in passage.get("locations") or []:
                    loc["tests"] = {"r2": "how it follows", "r3": "nothing more needed"}
                    loc["conjunct_relation"] = "exactMatch"
        shutil.rmtree(self.paths.corpus_run_dir("loc-1"))
        F.write_harvest(self.paths, "loc-1", docs)
        got = outcomes.build(self.paths, "loc-1")
        self.assertEqual([outcomes.outcome_row(r) for r in got["outcomes"]],
                         [outcomes.outcome_row(r) for r in want["outcomes"]])
        self.assertEqual(got["counts"], want["counts"])
        set_status_rule(self.paths, "loc-1", 2)
        kept = [loc for row in outcomes.build(self.paths, "loc-1")["outcomes"]
                for loc in row["locations"]]
        self.assertTrue(kept)
        for loc in kept:
            self.assertEqual("tests" in loc, outcomes.held_relation(loc) in vocab.STRONG_RELATIONS)
            self.assertEqual("conjunct_relation" in loc, loc["relation"] == "hasPart")

    def test_a_cut_flag_outside_the_list_is_a_problem(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.answer_unresolved("too_long")
        built = outcomes.build(self.paths, "loc-1")
        (unresolved,) = [r for r in built["outcomes"] if r["outcome"] == "unresolved"]
        self.assertNotIn("cut_flag", unresolved)
        self.assertIn("cut_flag 'too_long'", unresolved["problem"])


class TestTheChallengeRecordsCarryTheCut(unittest.TestCase):
    """Section 1.2: what a challenge record copies from a status-rule-2 outcome row."""

    def rows(self, cut: bool):
        row = _row()
        row["coverage"]["challenge"] = None
        if not cut:
            # a row of judgment rules 1 or 2: no conjuncts and no conjunct relation, but
            # its locations name the claim ids of the run's own claim list, as the
            # smoke-3 locate run's do
            row.pop("conjuncts")
            row["claims"] = [{"id": "c1", "text": "a widget has a gadget"},
                             {"id": "c2", "text": "two gadgets of a widget are equal"}]
            for loc in row["locations"]:
                loc.pop("conjunct_relation")
        else:
            row.update(statement="for a widget W: W has a unique gadget", shape="existence_uniqueness")
        return [row]

    def test_a_cut_rows_records(self):
        recs = cplan.classify(self.rows(True), run_id="loc-1", status_rule=2)
        for rec in recs:
            self.assertEqual(rec["shape"], "existence_uniqueness")
            self.assertEqual(len(rec["conjuncts"]), 2)
        (cov,) = [r for r in recs if r["target"] == "coverage"]
        self.assertEqual([(p["clauses"], p["conjunct_relation"]) for p in cov["parts"]],
                         [(["c1"], "exactMatch"), (["c2"], "broadMatch")])

    def test_an_earlier_rows_records_are_what_they_were(self):
        for status_rule in ({}, {"status_rule": 1}):
            recs = cplan.classify(self.rows(False), run_id="loc-1", **status_rule)
            for rec in recs:
                for key in ("statement", "conjuncts", "shape"):
                    self.assertNotIn(key, rec)
            (cov,) = [r for r in recs if r["target"] == "coverage"]
            # the parts' claim-id clauses stay off the record (re-planning a challenge of a
            # run of rules 1 or 2 writes the task records it always wrote)
            self.assertEqual({tuple(sorted(p)) for p in cov["parts"]},
                             {("declaration", "library", "location_key", "rank", "relation")})

    def test_status_rule_1_keeps_the_fields_off_even_where_a_row_has_them(self):
        """The fields bind by the run's status rule, never by what a row happens to hold."""
        recs = cplan.classify(self.rows(True), run_id="loc-1")
        for rec in recs:
            for key in ("statement", "conjuncts", "shape"):
                self.assertNotIn(key, rec)
        (cov,) = [r for r in recs if r["target"] == "coverage"]
        for part in cov["parts"]:
            self.assertNotIn("clauses", part)
            self.assertNotIn("conjunct_relation", part)

    def test_the_plan_passes_the_runs_status_rule(self):
        """``plan`` classifies each locate run by the status rule its outcomes were built by."""
        seen = []
        real = cplan.classify

        def spy(rows, *, run_id, status_rule=vocab.STATUS_RULE_D1):
            seen.append(status_rule)
            return real(rows, run_id=run_id, status_rule=status_rule)

        built = {"mode": vocab.LOCATE, "sourceset": "s", "universe": "u",
                 "outcomes": self.rows(True), "status_rule": 2}
        original_build, cplan.classify = cplan.outcomes_mod.build, spy
        cplan.outcomes_mod.build = lambda *a, **k: built
        try:
            out = cplan.plan(None, ["loc-1"], seed=0, sample_rate=1.0)
        finally:
            cplan.outcomes_mod.build, cplan.classify = original_build, real
        self.assertEqual(seen, [2])
        (cov,) = [r for r in out["records"] if r["target"] == "coverage"]
        self.assertEqual([p["clauses"] for p in cov["parts"]], [["c1"], ["c2"]])


if __name__ == "__main__":
    unittest.main()
