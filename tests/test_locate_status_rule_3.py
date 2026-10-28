"""Status rule 3 (R-BROAD): a broadMatch formalizes whatever its declaration is.

New code, for the maintainer's call R-BROAD of 2026-10-28 (references/vocabulary.md
section 1.11, "Status rule 3"): a ``broadMatch`` formalizes a theorem or conjecture
passage whether its declaration is a proved theorem or data, and a definition passage
when its declaration is a definition; everything else is status rule 2.  Checked:

* ``cflib.vocab.derive_status(..., status_rule=3)`` over the hand-written table
  ``tests/data/locate/status-parity-3.json`` (``cf-locate-status-parity/3``), with
  status rule 2's expectation beside each case;
* the version's names, as the planner, the outcomes, the records, the export and the
  bench read them;
* the outcomes and the export of the synthetic bundled run of
  ``tests/test_locate_bundle_status.py`` with ``locate.status_rule: 3`` in its
  run.yaml: the status rule 3 derives, and a ``cf-locate-recordset/2`` set whose seal
  names 3.
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
SCRIPTS = HERE.parent / "scripts"
for _path in (SCRIPTS, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from cflib import vocab  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from locate import export as export_mod, outcomes, plan as lplan, records  # noqa: E402

from test_locate_bundle_status import _Bundled, _records, set_status_rule  # noqa: E402

EXT = records.EXTENSION
PARITY_2 = HERE / "data" / "locate" / "status-parity-2.json"
PARITY_3 = HERE / "data" / "locate" / "status-parity-3.json"
VOCABULARY_MD = (HERE.parent / "references" / "vocabulary.md").read_text(encoding="utf-8")


def loc(key, relation, proof_status, statement=False):
    return {"location_key": key, "relation": relation, "proof_status": proof_status,
            "statement": statement}


class TestTheParityTableOfStatusRule3(unittest.TestCase):
    """Every case of the table, written by hand; a consumer ports it beside /2."""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(PARITY_3.read_text(encoding="utf-8"))

    def test_the_table_is_status_rule_3s(self):
        self.assertEqual(self.doc["schema"], "cf-locate-status-parity/3")
        self.assertEqual(self.doc["status_rule"], vocab.STATUS_RULE_BROAD)
        self.assertEqual((self.doc["decision"]["id"], self.doc["decision"]["date"]),
                         ("R-BROAD", "2026-10-28"))
        ids = [case["id"] for case in self.doc["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({case["status_rule"] for case in self.doc["cases"]}, {3})

    def test_every_case(self):
        for case in self.doc["cases"]:
            with self.subTest(case=case["id"], rule=case["rule"]):
                call = (case["passage_kind"], case["locations"], case["absent"],
                        case["coverage"])
                if case.get("refused"):
                    with self.assertRaises(CfError):
                        vocab.derive_status(*call, status_rule=3)
                else:
                    self.assertEqual(vocab.derive_status(*call, status_rule=3),
                                     case["expected"])

    def test_status_rule_2s_cases_are_all_here(self):
        """Every case of the /2 table, under its id, with its rule-2 expectation beside it."""
        two = {c["id"]: c for c in json.loads(PARITY_2.read_text(encoding="utf-8"))["cases"]}
        three = {c["id"]: c for c in self.doc["cases"]}
        self.assertLessEqual(set(two), set(three))
        for cid, case in two.items():
            with self.subTest(case=cid):
                self.assertEqual(three[cid]["locations"], case["locations"])
                self.assertEqual(three[cid]["coverage"], case["coverage"])
                self.assertEqual(bool(three[cid].get("refused")), bool(case.get("refused")))
                if not case.get("refused"):
                    self.assertEqual(three[cid]["expected_status_rule_2"], case["expected"])

    def test_what_status_rule_2_derives_beside_each(self):
        """Only a broadMatch moves, and only to formalized: what R-BROAD changes."""
        changed = {}
        for case in self.doc["cases"]:
            if case.get("refused"):
                continue
            call = (case["passage_kind"], case["locations"], case["absent"], case["coverage"])
            with self.subTest(case=case["id"]):
                got = vocab.derive_status(*call, status_rule=2)
                self.assertEqual(got, case["expected_status_rule_2"])
            if got != case["expected"]:
                changed[case["id"]] = (got, case["expected"])
        self.assertEqual(set(changed), {"B13", "B14", "B17", "B18", "B25", "B27", "B36",
                                        "S01", "S02", "S06", "S07", "S08", "S11", "S12",
                                        "S13", "S14", "S15", "S21"})
        self.assertEqual({new for _old, new in changed.values()}, {"formalized"})
        for cid in changed:
            case = next(c for c in self.doc["cases"] if c["id"] == cid)
            relations = {l["relation"] for l in case["locations"]}
            if case["coverage"]:
                relations.add(case["coverage"]["relation"])
            with self.subTest(case=cid):
                self.assertIn("broadMatch", relations)

    def test_the_table_spans_the_rows(self):
        cases = [c for c in self.doc["cases"] if not c.get("refused")]
        self.assertEqual({c["passage_kind"] for c in cases}, vocab.PASSAGE_KINDS)
        self.assertEqual({c.get("expected") for c in cases},
                         set(vocab.DERIVED_STATUSES) | {None})
        broad = [c for c in cases if not c["coverage"] and len(c["locations"]) == 1
                 and c["locations"][0]["relation"] == "broadMatch"]
        self.assertEqual({l["proof_status"] for c in broad for l in c["locations"]},
                         set(vocab.PROOF_STATUSES))

    def test_the_table_is_documented(self):
        self.assertIn("`tests/data/locate/status-parity-3.json`", VOCABULARY_MD)
        self.assertIn("`cf-locate-status-parity/3`", VOCABULARY_MD)
        self.assertIn("**Status rule 3: a broadMatch formalizes whatever its declaration is**",
                      VOCABULARY_MD)


class TestTheRows(unittest.TestCase):
    """R-BROAD on the rows, case by case, beside status rule 2."""

    def test_a_broad_match_to_data_formalizes_a_theorem(self):
        data = [loc("a", "broadMatch", "definition")]
        for kind in ("theorem", "conjecture", "example", "other"):
            with self.subTest(kind=kind):
                self.assertEqual(vocab.derive_status(kind, data, False, status_rule=2), "partial")
                self.assertEqual(vocab.derive_status(kind, data, False, status_rule=3),
                                 "formalized")

    def test_a_broad_match_to_a_definition_formalizes_a_definition(self):
        data = [loc("a", "broadMatch", "definition")]
        for kind in sorted(vocab.DEFINITION_PASSAGE_KINDS):
            with self.subTest(kind=kind):
                self.assertEqual(vocab.derive_status(kind, data, False, status_rule=2), "partial")
                self.assertEqual(vocab.derive_status(kind, data, False, status_rule=3),
                                 "formalized")

    def test_everything_else_is_status_rule_2(self):
        for relation in sorted(vocab.LOCATE_RELATIONS):
            for status in vocab.PROOF_STATUSES:
                for flag in ((False, True) if status in ("definition", "statement_only",
                                                         "wanted", "axiom_dependent")
                             else (False,)):
                    for kind in sorted(vocab.PASSAGE_KINDS):
                        locations = [loc("a", relation, status, flag)]
                        two = vocab.derive_status(kind, locations, False, status_rule=2)
                        three = vocab.derive_status(kind, locations, False, status_rule=3)
                        # a definition row reads no flag; the theorem row refuses a flagged one
                        moves = (relation == "broadMatch" and status == "definition"
                                 and (kind not in vocab.THEOREM_PASSAGE_KINDS or not flag))
                        with self.subTest(relation=relation, status=status, flag=flag,
                                          kind=kind):
                            if moves:
                                self.assertEqual(three, "formalized")
                                self.assertEqual(two, "statement_only"
                                                 if flag and kind in vocab.EITHER_PASSAGE_KINDS
                                                 else "partial")
                            else:
                                self.assertEqual(three, two)

    def test_only_the_defined_rules(self):
        self.assertEqual(vocab.STATUS_RULES, (1, 2, 3))
        for bad in (0, 4, True, "3", None, 3.0):
            with self.subTest(status_rule=bad):
                with self.assertRaises(CfError):
                    vocab.derive_status("theorem", [], False, None, status_rule=bad)

    def test_a_bundle_is_read_as_status_rule_2_reads_it(self):
        parts = [loc("a", "hasPart", "definition"), loc("b", "hasPart", "definition")]
        claim = {"complete": True, "parts": ["a", "b"], "challenge": "survived",
                 "relation": "broadMatch"}
        self.assertEqual(vocab.derive_status("definition", parts, False, claim, status_rule=2),
                         "partial")
        self.assertEqual(vocab.derive_status("definition", parts, False, claim, status_rule=3),
                         "formalized")
        d1 = {k: v for k, v in claim.items() if k != "relation"}
        with self.assertRaisesRegex(CfError, "carries its relation"):
            vocab.derive_status("definition", parts, False, d1, status_rule=3)
        with self.assertRaisesRegex(CfError, "unexpected note"):
            vocab.derive_status("definition", parts, False, {**claim, "note": "x"},
                                status_rule=3)


class TestTheVersionsNames(unittest.TestCase):

    def test_the_constants(self):
        self.assertEqual(vocab.STATUS_RULE_BROAD, 3)
        self.assertEqual((records.STATUS_RULE_D1, records.STATUS_RULE_BUNDLE,
                          records.STATUS_RULE_BROAD), vocab.STATUS_RULES)
        self.assertEqual(records.STATUS_RULES, vocab.STATUS_RULES)
        self.assertTrue(vocab.reads_bundles(3) and vocab.reads_bundles(2))
        self.assertFalse(vocab.reads_bundles(1))

    def test_the_status_rule_of_each_judgment_rules(self):
        self.assertEqual([vocab.status_rule_of_judgment(r) for r in (0, 1, 2, 3, 4)],
                         [1, 1, 1, 2, 3])
        self.assertEqual(vocab.status_rule_of_judgment(vocab.JUDGMENT_RULES), vocab.STATUS_RULE)
        self.assertEqual(lplan.status_rule(), vocab.STATUS_RULE)

    def test_the_record_set_of_status_rule_3_is_a_2(self):
        self.assertEqual(export_mod.SEAL_SCHEMAS[vocab.STATUS_RULE_BROAD],
                         export_mod.SEAL_SCHEMA_BUNDLE)
        self.assertEqual(records.coverage_ext({"parts": ["a", "b"], "challenge": "survived",
                                               "relation": "broadMatch"}, status_rule=3),
                         {"complete": True, "parts": ["a", "b"], "challenge": "survived",
                          "relation": "broadMatch"})

    def test_the_outcomes_read_it(self):
        self.assertEqual(outcomes.status_rule_of({"locate": {"status_rule": 3}}), 3)
        with self.assertRaisesRegex(CfError, "status rule 4"):
            outcomes.status_rule_of({"locate": {"status_rule": 4}})

    def test_the_bench_derives_gold_of_rules_4_by_it(self):
        from bench import gold as gold_mod
        for version, rules in gold_mod.LABELER_RULES.items():
            with self.subTest(version=version):
                self.assertEqual(gold_mod.gold_status_rule(version),
                                 vocab.status_rule_of_judgment(rules))


class TestStatusRule3ThroughTheRun(_Bundled):
    """The bundled run: a definition passage whose bundle is broadMatch, finished parts."""

    def test_the_broad_bundle_formalizes_the_definition(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge()
        row, built = self.row()
        self.assertEqual((built["status_rule"], row["status"]), (2, "partial"))
        set_status_rule(self.paths, "loc-1", 3)
        row, built = self.row()
        self.assertEqual(built["status_rule"], 3)
        self.assertEqual(row["coverage"]["relation"], "broadMatch")
        self.assertEqual((row["passage_kind"], row["status"]), ("definition", "formalized"))
        # status rule 2's rows and counts, unchanged in shape
        self.assertEqual(row["conjuncts"][0]["id"], "c1")
        self.assertEqual(built["counts"]["bundles"],
                         {"written": 1, "survived": 1, "by_relation": {"broadMatch": 1}})
        self.assertEqual(built["counts"]["owed_hunt"], 0)

    def test_the_record_set_is_a_2_whose_seal_names_3(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge()
        code, out, err = self.export("set-2")
        self.assertEqual(code, 0, out + err)
        set_status_rule(self.paths, "loc-1", 3)
        code, out, err = self.export("set-3")
        self.assertEqual(code, 0, out + err)
        seal = read_json(self.paths.export_dir("set-3") / "SEAL.json")
        self.assertEqual((seal["schema"], seal["status_rule"]), ("cf-locate-recordset/2", 3))
        self.assertEqual(tuple(sorted(seal)), tuple(sorted(export_mod.SEAL_KEYS_BUNDLE)))
        self.assertEqual(export_mod.verify_seal(self.paths.export_dir("set-3")), [])
        two, three = (_records(self.paths.export_dir(s)) for s in ("set-2", "set-3"))
        mine = [r for r in three if r["extensions"][EXT]["item"] == self.item]
        self.assertEqual({r["status"] for r in mine}, {"formalized"})
        for rec in three:
            self.assertEqual(records.check(rec), [])
        # the same records but for the statuses status rule 3 moved
        strip = lambda rec: {k: v for k, v in rec.items() if k != "status"}
        self.assertEqual(sorted(json.dumps(strip(r), sort_keys=True) for r in two),
                         sorted(json.dumps(strip(r), sort_keys=True) for r in three))
        moved = {r["id"] for r in two} - {r["id"] for r in two if r in three}
        self.assertTrue(moved)
        self.assertLessEqual(moved, {r["id"] for r in three if r.get("status") == "formalized"})


if __name__ == "__main__":
    unittest.main()
