"""Status rule 2: a bundle takes its weakest part's relation, and its record set is ``/2``.

New code, for docs/proposals/statement-cut.md sections 2.1, 2.3 and 2.4 (the
maintainer's answer of 2026-10-21 to question 9: a bundle of broader parts is
``broadMatch``, so a definition passage with only broader parts is ``partial``).
Both ways, everywhere:

* ``cflib.vocab.derive_status(..., status_rule=2)`` over the hand-written table
  ``tests/data/locate/status-parity-2.json`` (``cf-locate-status-parity/2``), and
  status rule 1 over the round-3 table, unchanged, called by its version too;
* the revision-free keys ``locate.records`` restates, in lockstep with
  ``cflib.hashing``;
* the outcomes and the export of a synthetic run whose ``run.yaml`` names
  ``locate.status_rule: 2`` (set by hand: no planner writes it before the flip),
  beside the same documents tallied and exported under status rule 1, which give
  the D1 status and the ``cf-locate-recordset/1`` set they always did.
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

import _locate_fixtures as F  # noqa: E402
from cflib import hashing, vocab  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from cflib.yamlio import read_yaml, write_yaml  # noqa: E402
from locate import export as export_mod, outcomes, records  # noqa: E402

from test_locate_planner import Campaign, ws  # noqa: E402

EXT = records.EXTENSION
PARITY_1 = HERE / "data" / "locate" / "status-parity.json"
PARITY_2 = HERE / "data" / "locate" / "status-parity-2.json"
VOCABULARY_MD = (HERE.parent / "references" / "vocabulary.md").read_text(encoding="utf-8")


def loc(key, relation, proof_status, statement=False):
    return {"location_key": key, "relation": relation, "proof_status": proof_status,
            "statement": statement}


def bundle(relation, parts=("a", "b"), challenge="survived"):
    return {"complete": True, "parts": list(parts), "challenge": challenge, "relation": relation}


class TestTheParityTableOfStatusRule2(unittest.TestCase):
    """Every case of the new table, written by hand; WikiLean ports it (section 8)."""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(PARITY_2.read_text(encoding="utf-8"))

    def test_the_table_is_status_rule_2s(self):
        self.assertEqual(self.doc["schema"], "cf-locate-status-parity/2")
        self.assertEqual(self.doc["status_rule"], vocab.STATUS_RULE_BUNDLE)
        self.assertEqual((self.doc["decision"]["id"], self.doc["decision"]["date"]),
                         ("Q9", "2026-10-21"))
        ids = [case["id"] for case in self.doc["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 40)
        self.assertEqual({case["status_rule"] for case in self.doc["cases"]}, {2})

    def test_every_case(self):
        for case in self.doc["cases"]:
            with self.subTest(case=case["id"], rule=case["rule"]):
                call = (case["passage_kind"], case["locations"], case["absent"],
                        case["coverage"])
                if case.get("refused"):
                    with self.assertRaises(CfError):
                        vocab.derive_status(*call, status_rule=2)
                else:
                    self.assertEqual(vocab.derive_status(*call, status_rule=2),
                                     case["expected"])

    def test_what_status_rule_1_derives_beside_each(self):
        """The D1 projection of each case under status rule 1: what rule 2 changes."""
        changed = {}
        for case in self.doc["cases"]:
            if "expected_status_rule_1" not in case:
                continue
            claim = case["coverage"]
            d1 = claim and {k: claim[k] for k in vocab.COVERAGE_RECORD_KEYS if k in claim}
            with self.subTest(case=case["id"]):
                got = vocab.derive_status(case["passage_kind"], case["locations"],
                                          case["absent"], d1)
                self.assertEqual(got, case["expected_status_rule_1"])
            if not case.get("refused") and got != case["expected"]:
                changed[case["id"]] = (got, case["expected"])
        # only the definition row's broadMatch moves (section 2.3's table)
        self.assertEqual({cid for cid in changed},
                         {"B13", "B14", "B15", "B17", "B18", "B25", "B27"})
        kinds = {c["id"]: c["passage_kind"] for c in self.doc["cases"]}
        self.assertLessEqual({kinds[cid] for cid in changed}, vocab.DEFINITION_PASSAGE_KINDS)
        self.assertEqual({c["coverage"]["relation"] for c in self.doc["cases"]
                          if c["id"] in changed}, {"broadMatch"})
        self.assertEqual({new for _old, new in changed.values()}, {"partial"})

    def test_the_table_spans_the_rows(self):
        cases = self.doc["cases"]
        bundles = [c for c in cases if c["coverage"] and not c.get("refused")]
        self.assertEqual({c["coverage"]["relation"] for c in bundles}, vocab.BUNDLE_RELATIONS)
        self.assertEqual({c["passage_kind"] for c in cases}, vocab.PASSAGE_KINDS - {"lemma"}
                         if "lemma" in vocab.PASSAGE_KINDS else vocab.PASSAGE_KINDS)
        self.assertLessEqual(vocab.CHALLENGE_VERDICTS,
                             {c["coverage"]["challenge"] for c in bundles})
        self.assertEqual({c.get("expected") for c in cases if not c.get("refused")},
                         set(vocab.DERIVED_STATUSES) | {None})

    def test_the_round_3_table_is_status_rule_1_called_by_its_version(self):
        """Status rule 1 is today's function: every round-3 case, the keyword given."""
        doc = json.loads(PARITY_1.read_text(encoding="utf-8"))
        self.assertEqual(doc["schema"], "cf-locate-status-parity/1")
        for case in doc["cases"]:
            with self.subTest(case=case["id"]):
                call = (case["passage_kind"], case["locations"], case["absent"],
                        case["coverage"])
                if case.get("refused"):
                    with self.assertRaises(CfError):
                        vocab.derive_status(*call, status_rule=vocab.STATUS_RULE_D1)
                else:
                    self.assertEqual(vocab.derive_status(*call, status_rule=1),
                                     case["expected"])
                    self.assertEqual(vocab.derive_status(*call), case["expected"])

    def test_the_table_is_documented(self):
        self.assertIn("`tests/data/locate/status-parity-2.json`", VOCABULARY_MD)
        self.assertIn("`cf-locate-status-parity/2`", VOCABULARY_MD)
        self.assertIn("**Status rule 2: a bundle takes its weakest part's relation**",
                      VOCABULARY_MD)


class TestTheStatusRuleArgument(unittest.TestCase):

    PARTS = [loc("a", "hasPart", "definition"), loc("b", "hasPart", "definition")]

    def test_only_the_defined_rules(self):
        for bad in (0, 3, True, "2", None, 2.0):
            with self.subTest(status_rule=bad):
                with self.assertRaises(CfError):
                    vocab.derive_status("theorem", [], False, None, status_rule=bad)

    def test_status_rule_1_refuses_a_bundles_relation(self):
        """A record of status rule 1 carries the three D1 keys; a relation is none of them."""
        with self.assertRaisesRegex(CfError, "unexpected relation"):
            vocab.derive_status("definition", self.PARTS, False, bundle("broadMatch"))

    def test_status_rule_2_reads_the_relation_where_1_reads_exact_match(self):
        d1 = {k: v for k, v in bundle("broadMatch").items() if k != "relation"}
        self.assertEqual(vocab.derive_status("definition", self.PARTS, False, d1), "formalized")
        self.assertEqual(vocab.derive_status("definition", self.PARTS, False,
                                             bundle("broadMatch"), status_rule=2), "partial")
        self.assertEqual(vocab.derive_status("definition", self.PARTS, False,
                                             bundle("closeMatch"), status_rule=2), "formalized")
        with self.assertRaisesRegex(CfError, "carries its relation"):
            vocab.derive_status("definition", self.PARTS, False, d1, status_rule=2)

    def test_without_a_claim_the_two_rules_agree(self):
        for locations, absent in (([loc("a", "exactMatch", "proved")], False),
                                  ([loc("a", "hasPart", "proved")], False),
                                  ([loc("a", "relatedMatch", "proved")], True),
                                  ([loc("a", "relatedMatch", "proved")], False),
                                  ([loc("a", "broadMatch", "definition")], False)):
            for kind in sorted(vocab.PASSAGE_KINDS):
                with self.subTest(kind=kind, locations=locations, absent=absent):
                    self.assertEqual(vocab.derive_status(kind, locations, absent),
                                     vocab.derive_status(kind, locations, absent,
                                                         status_rule=2))


class TestTheKeysAreRestatedInLockstep(unittest.TestCase):
    """``locate.records`` imports no cflib: its keys are ``cflib.hashing``'s, value for value."""

    def test_the_same_keys(self):
        span_sha = hashing.sha256_text("Every finite widget has a spanning gadget")
        for pageid in (1, 1001, 99999999):
            pk = hashing.passage_key(pageid, span_sha)
            self.assertEqual(records.passage_key(pageid, span_sha), pk)
            a = hashing.pair_key(pk, "https://example.org/alpha", "Widget.exists_spanning_gadget")
            b = hashing.pair_key(pk, "https://example.org/beta", "Beta.Widget.gadget_of_finite")
            self.assertEqual(records.pair_key(pk, "https://example.org/alpha",
                                              "Widget.exists_spanning_gadget"), a)
            self.assertEqual(records.bundle_key(pk, [b, a]), hashing.bundle_key(pk, [a, b]))

    def test_the_same_refusals(self):
        pk = hashing.passage_key(1001, "0" * 64)
        pair = hashing.pair_key(pk, "https://example.org/alpha", "Widget.a")
        for call in (lambda m: m.passage_key(True, "0" * 64),
                     lambda m: m.passage_key(0, "0" * 64),
                     lambda m: m.passage_key("1001", "0" * 64),
                     lambda m: m.passage_key(1001, "ABC"),
                     lambda m: m.pair_key(pk, "", "Widget.a"),
                     lambda m: m.pair_key(pk, " https://example.org/alpha", "Widget.a"),
                     lambda m: m.pair_key(pk, "https://example.org/alpha", "Widget.a\nb"),
                     lambda m: m.bundle_key(pk, [pair]),
                     lambda m: m.bundle_key(pk, [pair, pair])):
            with self.assertRaises(CfError):
                call(hashing)
            with self.assertRaises(records.RecordError):
                call(records)

    def test_the_version_constants(self):
        self.assertEqual((records.STATUS_RULE_D1, records.STATUS_RULE_BUNDLE),
                         (vocab.STATUS_RULE_D1, vocab.STATUS_RULE_BUNDLE))
        self.assertEqual(records.COVERAGE_KEYS_BUNDLE, vocab.COVERAGE_RECORD_KEYS_BUNDLE)
        self.assertEqual(records.BUNDLE_RELATIONS, vocab.BUNDLE_RELATIONS)
        self.assertEqual(export_mod.SEAL_SCHEMAS, {1: "cf-locate-recordset/1",
                                                   2: "cf-locate-recordset/2"})
        self.assertEqual(export_mod.SEAL_KEYS_BUNDLE, export_mod.SEAL_KEYS + ("status_rule",))


class TestTheBundleTheRecordsCarry(unittest.TestCase):
    """``records.bundle_ext`` and ``coverage_ext`` on synthetic passages."""

    PASSAGE = {"item": "In-text #0000c0de",
               "conjuncts": [{"id": "c1", "text": "a finite widget has a spanning gadget"},
                             {"id": "c2", "text": "two spanning gadgets of one widget are equal"}],
               "locations": [{"location_key": "k1", "relation": "hasPart", "clauses": ["c1"]},
                             {"location_key": "k2", "relation": "hasPart", "clauses": ["c2"]}]}
    CLAIM = {"complete": True, "parts": ["k1", "k2"], "challenge": "survived",
             "note": "existence and uniqueness", "relation": "broadMatch"}

    def keys(self):
        pk = hashing.passage_key(1001, "1" * 64)
        return pk, {"k1": hashing.pair_key(pk, "https://example.org/alpha", "Widget.a"),
                    "k2": hashing.pair_key(pk, "https://example.org/alpha", "Widget.b")}

    def test_status_rule_1s_projection_is_unchanged(self):
        self.assertEqual(records.coverage_ext(self.CLAIM),
                         {"complete": True, "parts": ["k1", "k2"], "challenge": "survived"})
        self.assertEqual(records.coverage_ext(self.CLAIM, status_rule=2),
                         {"complete": True, "parts": ["k1", "k2"], "challenge": "survived",
                          "relation": "broadMatch"})

    def test_the_bundle(self):
        pk, pairs = self.keys()
        got = records.bundle_ext(self.PASSAGE, self.CLAIM, passage_key=pk, pair_keys=pairs)
        self.assertEqual(set(got), vocab.COVERAGE_RECORD_KEYS_BUNDLE)
        self.assertEqual(got["conjuncts"], [
            {"id": "c1", "text": "a finite widget has a spanning gadget", "part": "k1"},
            {"id": "c2", "text": "two spanning gadgets of one widget are equal", "part": "k2"}])
        self.assertEqual(got["bundle_key"], hashing.bundle_key(pk, pairs.values()))
        # the record's claim is one derive_status reads under status rule 2
        parts = [loc("k1", "hasPart", "proved"), loc("k2", "hasPart", "proved")]
        self.assertEqual(vocab.derive_status("theorem", parts, False, got, status_rule=2),
                         "formalized")

    def test_what_no_bundle_carries(self):
        pk, pairs = self.keys()
        simple = {**self.PASSAGE, "conjuncts": None}
        twice = {**self.PASSAGE, "locations": [
            {"location_key": "k1", "relation": "hasPart", "clauses": ["c1", "c2"]},
            {"location_key": "k2", "relation": "hasPart", "clauses": ["c2"]}]}
        idle = {**self.PASSAGE, "locations": [
            {"location_key": "k1", "relation": "hasPart", "clauses": ["c1", "c2"]},
            {"location_key": "k2", "relation": "hasPart", "clauses": []}]}
        for passage, claim, why in (
                (simple, self.CLAIM, "no conjuncts"),
                (twice, self.CLAIM, "stated by 2 part"),
                (idle, self.CLAIM, "states no conjunct"),
                (self.PASSAGE, {**self.CLAIM, "relation": "narrowMatch"}, "relation is one of"),
                (self.PASSAGE, {**self.CLAIM, "relation": None}, "relation is one of")):
            with self.subTest(why=why):
                with self.assertRaisesRegex(records.RecordError, why):
                    records.bundle_ext(passage, claim, passage_key=pk, pair_keys=pairs)

    def test_the_coverage_problem_of_each_rule(self):
        passage = {"item": "x", "coverage": {**self.CLAIM, "relation": None},
                   "locations": self.PASSAGE["locations"]}
        self.assertIsNone(records.coverage_problem(passage))
        self.assertIn("relation", records.coverage_problem(passage, status_rule=2))


# --------------------------------------------------------------------------
# the outcomes and the export of the synthetic campaign, under each status rule
# --------------------------------------------------------------------------

def _records(out_dir: Path) -> list[dict]:
    return [json.loads(line) for path in sorted((out_dir / "records").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


#: Widget lemma's supporting definition, cut as a compound statement: the inventory's
#: conjuncts (synthetic paraphrases, none the layer's words)
CONJUNCTS = [{"id": "c1", "text": "a spanning gadget covers every part of its widget"},
             {"id": "c2", "text": "its size divides the order of the widget"}]


def set_status_rule(paths, run_id: str, rule: int | None) -> None:
    """Name (or drop) ``locate.status_rule`` in a run's run.yaml, as the flip will write it."""
    path = paths.run_yaml(run_id)
    data = read_yaml(path)
    if rule is None:
        data["locate"].pop("status_rule", None)
    else:
        data["locate"]["status_rule"] = rule
    write_yaml(path, data)


class _Bundled(Campaign):
    """``loc-1`` with Widget lemma's supporting definition stated by a bundle of two parts.

    The inventory gives the passage two conjuncts (shape ``cases``); the locate
    document gives its two ``hasPart`` locations the conjuncts' clause ids and a
    ``conjunct_relation`` each -- ``exactMatch`` for the definition, ``broadMatch``
    for the proved lemma -- and a coverage claim over both.
    """

    PARTS = (("Widget.Spanning", "exactMatch"), ("Widget.card_dvd", "broadMatch"))

    def setUp(self):
        super().setUp()
        inv = F.inventory_documents(self.paths)
        self.item = None
        for doc in inv.values():
            if doc["source_id"] == F.W1:
                passage = doc["passages"][2]
                passage.update(conjuncts=CONJUNCTS, shape="cases")
                self.item = passage["item"]
        F.write_harvest(self.paths, "inv-1", inv)
        F.plan_locate(self, self.root)
        docs = F.locate_documents(self.paths, "loc-1")
        for doc in docs.values():
            if doc["source_id"] == F.W1:
                (passage,) = [p for p in doc["passages"] if p["item"] == self.item]
                passage["locations"] = [
                    {**F._loc("alpha", decl, "hasPart", rank), "clauses": [f"c{rank}"],
                     "conjunct_relation": relation, "step": "the conjunct it states"}
                    for rank, (decl, relation) in enumerate(self.PARTS, 1)]
                passage["coverage"] = {"complete": True, "parts": [1, 2],
                                       "note": "the predicate and its bound give both conjuncts"}
        F.write_harvest(self.paths, "loc-1", docs)

    def row(self, challenge_runs=("chl-1",)) -> dict:
        built = outcomes.build(self.paths, "loc-1", challenge_runs=list(challenge_runs))
        return next(r for r in built["outcomes"] if r["item"] == self.item), built

    def export(self, set_id="set-1"):
        return ws(self.root, "export", "literature-links", "--run", "loc-1", "--challenge-run",
                  "chl-1", "--set-id", set_id)


class TestTheBundleThroughTheRun(_Bundled):

    def test_status_rule_1_is_d1(self):
        """No status_rule in run.yaml: the claim is one more exactMatch, the rows unchanged."""
        self.challenge()
        row, built = self.row()
        self.assertEqual(built["status_rule"], 1)
        self.assertEqual(set(row["coverage"]), {"complete", "parts", "note", "challenge"})
        self.assertEqual(row["coverage"]["challenge"], "survived")
        self.assertEqual(row["status"], "formalized")
        out = outcomes.outcome_row(row)
        for key in ("statement", "conjuncts", "shape", "owed", "cut_flag"):
            self.assertNotIn(key, out)
        self.assertNotIn("owed_hunt", built["counts"])
        self.assertNotIn("bundles", built["counts"])

    def test_status_rule_2_takes_the_weakest_parts_relation(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge()
        row, built = self.row()
        self.assertEqual(built["status_rule"], 2)
        self.assertEqual(row["coverage"]["relation"], "broadMatch")
        self.assertEqual(row["coverage"]["challenge"], "survived")
        # a definition passage with only broader parts is partial (question 9)
        self.assertEqual((row["passage_kind"], row["status"]), ("definition", "partial"))
        self.assertEqual(row["conjuncts"], CONJUNCTS)
        self.assertEqual(row["shape"], "cases")
        self.assertEqual(row["statement"], "informal restatement")
        self.assertEqual(built["counts"]["bundles"],
                         {"written": 1, "survived": 1, "by_relation": {"broadMatch": 1}})
        self.assertEqual(built["counts"]["owed_hunt"], 0)

    def test_a_confirms_part_relations_replace_the_parts_own(self):
        set_status_rule(self.paths, "loc-1", 2)

        def confirm(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "coverage":
                keys = [p["location_key"] for p in rec["parts"]]
                v["part_relations"] = {keys[0]: "closeMatch", keys[1]: "broadMatch"}
            return v
        self.challenge(verdict=confirm)
        row, _built = self.row()
        self.assertEqual(row["coverage"]["relation"], "broadMatch")
        self.assertIn("coverage_part_relations", outcomes.outcome_row(row))

        def weaker(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "coverage":
                v["part_relations"] = {p["location_key"]: "closeMatch" for p in rec["parts"]}
            return v
        # a challenge never raises a relation: the broadMatch part stays broadMatch
        self.rechallenge(weaker)
        row, _built = self.row()
        self.assertEqual(row["coverage"]["relation"], "broadMatch")

    def rechallenge(self, verdict):
        import shutil
        shutil.rmtree(self.paths.corpus_run_dir("chl-1"))
        F.write_harvest(self.paths, "chl-1",
                        F.challenge_documents(self.paths, "chl-1", verdict=verdict))

    def test_the_record_set_of_each_rule(self):
        self.challenge()
        code, out, err = self.export("set-1")
        self.assertEqual(code, 0, out + err)
        seal = read_json(self.paths.export_dir("set-1") / "SEAL.json")
        self.assertEqual(seal["schema"], "cf-locate-recordset/1")
        self.assertEqual(tuple(sorted(seal)), tuple(sorted(export_mod.SEAL_KEYS)))
        old = _records(self.paths.export_dir("set-1"))
        for rec in old:
            self.assertNotIn("passage_key", rec["extensions"][EXT])
            self.assertNotIn("pair_key", rec["extensions"][EXT])
        mine = [r for r in old if r["extensions"][EXT]["item"] == self.item]
        self.assertEqual({tuple(sorted(r["extensions"][EXT]["coverage"])) for r in mine},
                         {("challenge", "complete", "parts")})
        self.assertEqual({r["status"] for r in mine}, {"formalized"})

        set_status_rule(self.paths, "loc-1", 2)
        code, out, err = self.export("set-2")
        self.assertEqual(code, 0, out + err)
        seal = read_json(self.paths.export_dir("set-2") / "SEAL.json")
        self.assertEqual(seal["schema"], "cf-locate-recordset/2")
        self.assertEqual(seal["status_rule"], 2)
        self.assertEqual(tuple(sorted(seal)), tuple(sorted(export_mod.SEAL_KEYS_BUNDLE)))
        self.assertEqual(export_mod.verify_seal(self.paths.export_dir("set-2")), [])
        new = _records(self.paths.export_dir("set-2"))
        sources = {s["source_id"]: s for s in
                   (json.loads(line) for line in (self.paths.export_dir("set-2")
                                                  / "sources.jsonl").read_text().splitlines())}
        for rec in new:
            ext = rec["extensions"][EXT]
            subject = rec["subject"][0]["extensions"][EXT]
            sid = rec["id"].split("::")[0]
            pk = hashing.passage_key(sources[sid]["pageid"], subject["span_sha256"])
            self.assertEqual(ext["passage_key"], pk)
            if rec["object"] and rec["object"][0].get("location"):
                lean = rec["object"][0]
                self.assertEqual(ext["pair_key"], hashing.pair_key(
                    pk, lean["uri"], lean["location"]["declaration"]))
            else:
                self.assertNotIn("pair_key", ext)
            self.assertEqual(records.check(rec), [])
        mine = [r for r in new if r["extensions"][EXT]["item"] == self.item]
        self.assertEqual(len(mine), 2)
        for rec in mine:
            claim = rec["extensions"][EXT]["coverage"]
            self.assertEqual(set(claim), vocab.COVERAGE_RECORD_KEYS_BUNDLE)
            self.assertEqual(claim["relation"], "broadMatch")
            by_part = {r["extensions"][EXT]["location_key"]: r["extensions"][EXT]["pair_key"]
                       for r in mine}
            self.assertEqual(claim["bundle_key"], hashing.bundle_key(
                rec["extensions"][EXT]["passage_key"], by_part.values()))
            self.assertEqual([c["id"] for c in claim["conjuncts"]], ["c1", "c2"])
            self.assertEqual([c["text"] for c in claim["conjuncts"]],
                             [c["text"] for c in CONJUNCTS])
            self.assertEqual(rec["status"], "partial")
        # the status a record carries is the one its claim gives, read as WikiLean will
        parts = [{"location_key": r["extensions"][EXT]["location_key"], "relation": r["relation"],
                  "proof_status": r["object"][0]["extensions"][EXT]["proof_status"],
                  "statement": bool(r["object"][0]["extensions"][EXT].get("statement"))}
                 for r in mine]
        self.assertEqual(vocab.derive_status("definition", parts, False,
                                             mine[0]["extensions"][EXT]["coverage"],
                                             status_rule=2), "partial")
        # every other passage's records are the /1 set's but for the two keys
        strip = lambda rec: {**rec, "extensions": {EXT: {
            k: v for k, v in rec["extensions"][EXT].items()
            if k not in ("passage_key", "pair_key")}}}
        others_old = sorted(json.dumps(r, sort_keys=True) for r in old
                            if r["extensions"][EXT]["item"] != self.item)
        others_new = sorted(json.dumps(strip(r), sort_keys=True) for r in new
                            if r["extensions"][EXT]["item"] != self.item)
        self.assertEqual(others_new, others_old)

    def test_a_conjunct_past_the_quote_cap_is_refused(self):
        set_status_rule(self.paths, "loc-1", 2)
        layer = (self.paths.sourceset_sources_dir(F.SS) / F.W1 / "layer.txt").read_text(
            encoding="utf-8")
        long_run = " ".join(layer.split()[:14])
        inv = F.inventory_documents(self.paths)
        for doc in inv.values():
            if doc["source_id"] == F.W1:
                doc["passages"][2].update(conjuncts=[{"id": "c1", "text": long_run},
                                                     CONJUNCTS[1]], shape="cases")
        F.write_harvest(self.paths, "inv-1", inv)
        self.challenge()
        code, out, err = self.export("set-3")
        self.assertEqual(code, 1, out + err)
        report = read_json(self.paths.export_report_json("set-3"))
        self.assertTrue(any(r["item"] == self.item and "conjunct c1" in r["reason"]
                            for r in report["refused"]), report["refused"])

    def test_a_target_holding_either_seal_can_be_re_exported(self):
        set_status_rule(self.paths, "loc-1", 2)
        self.challenge()
        code, out, err = self.export("set-4")
        self.assertEqual(code, 0, out + err)
        set_status_rule(self.paths, "loc-1", None)
        code, out, err = self.export("set-4")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_json(self.paths.export_dir("set-4") / "SEAL.json")["schema"],
                         "cf-locate-recordset/1")


class TestAStatusRuleTheCheckoutDoesNotKnow(Campaign):

    def test_is_refused_by_name(self):
        self.locate()
        for bad in (3, True, "2"):
            with self.subTest(status_rule=bad):
                set_status_rule(self.paths, "loc-1", bad)
                with self.assertRaisesRegex(CfError, "status rule"):
                    outcomes.build(self.paths, "loc-1")
        set_status_rule(self.paths, "loc-1", None)
        self.assertEqual(outcomes.build(self.paths, "loc-1")["status_rule"], 1)
        self.assertEqual(outcomes.status_rule_of({"locate": {}}), 1)
        self.assertEqual(outcomes.status_rule_of({"locate": {"status_rule": 2}}), 2)


if __name__ == "__main__":
    unittest.main()
