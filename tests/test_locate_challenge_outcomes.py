"""What a challenge leaves on a passage's records (smoke 2: CH2, X4, X5).

New code: regression tests for the challenge-outcome defects the second locate
smoke run found, each reduced to the synthetic campaign of
``tests/_locate_fixtures.py`` (``Widget lemma``; no article text).  What they pin
is docs/proposals/wikipedia-locate.md section 5 as ``scripts/locate/outcomes.py``
applies it, in the shapes of references/locate-documents.md sections 6 and 10:

* CH2 -- a prominence overturn from ``supporting`` to ``central`` left the
  passage's records ``not_required``: two central absences nobody hunted were
  exported as if no rule applied.  Every record no verdict judged is now
  ``challenge_pending`` (``cflib.vocab.CHALLENGE_PENDING``), exported as such;
* X4 -- a refuted location was dropped and the others kept their ranks, so two
  passages exported ranks ``[1, 3]``; they are ranked 1..n again;
* X5 -- the locations an absence overturn added carried ``downgraded``, a
  value for a weakened record; they are the challenger's own witnessed
  findings and carry ``survived``.
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
from cflib import vocab  # noqa: E402
from locate import outcomes, records  # noqa: E402

from test_locate_planner import Campaign, ws  # noqa: E402

EXT = records.EXTENSION


def _records(out_dir: Path) -> list[dict]:
    return [json.loads(line) for path in sorted((out_dir / "records").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


class _Challenged(Campaign):
    """``loc-1`` with W2's supporting passage made a checked absence, as the field's was,
    and W1's supporting definition passage given a second strong location beside its
    basis (a ``closeMatch`` to a proved theorem, ``partial`` on a definition passage)."""

    def setUp(self):
        super().setUp()
        F.plan_locate(self, self.root)
        docs = F.locate_documents(self.paths, "loc-1")
        for doc in docs.values():
            if doc["source_id"] == F.W2:
                p = doc["passages"][1]
                doc["passages"][1] = {"item": p["item"], "outcome": "absent", "locations": [],
                                      "receipt": F._receipt("gadget induction",
                                                            "induction on gadgets",
                                                            "widget induction step")}
            if doc["source_id"] == F.W1:
                doc["passages"][2]["locations"].append(
                    F._loc("alpha", "Widget.card_dvd", "closeMatch", 3,
                           note="the order is written as a cardinal"))
        F.write_harvest(self.paths, "loc-1", docs)
        self.items = {sid: [p["item"] for p in F.frozen_formal_math(self.paths, sid)]
                      for sid in (F.W1, F.W2)}

    def outcome(self, sid: str, index: int, challenge_runs=("chl-1",)) -> dict:
        built = outcomes.build(self.paths, "loc-1", challenge_runs=list(challenge_runs))
        item = self.items[sid][index]
        return next(r for r in built["outcomes"] if r["source_id"] == sid and r["item"] == item)

    def export(self):
        return ws(self.root, "export", "literature-links", "--run", "loc-1", "--challenge-run",
                  "chl-1", "--set-id", "set-1")


class TestCH2AProminenceOverturnReopensThePassage(_Challenged):

    def setUp(self):
        super().setUp()

        def promote(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "prominence" and rec["item"] in (self.items[F.W2][1],
                                                                 self.items[F.W1][2]):
                v = {"item": rec["item"], "target": "prominence", "action": "overturn",
                     "prominence": "central",
                     "witness": "a named statement the article states as its own"}
            return v
        # sample rate 1: every supporting passage's prominence record is judged; the
        # supporting absence and the strong location beside the basis are left undrawn
        self.challenge("--sample-rate", "1", "--absence-sample-rate", "0",
                       "--strong-sample-rate", "0", verdict=promote)

    def test_ch2_an_unhunted_absence_is_pending_not_not_required(self):
        row = self.outcome(F.W2, 1)
        self.assertEqual(row["prominence"], "central")
        self.assertEqual(row["outcome"], "absent")
        self.assertEqual(row["absence"]["challenge"], vocab.CHALLENGE_PENDING)
        self.assertEqual(row["challenge"], vocab.CHALLENGE_PENDING)

    def test_ch2_an_unjudged_location_is_pending_and_a_judged_one_keeps_its_value(self):
        row = self.outcome(F.W1, 2)
        self.assertEqual(row["prominence"], "central")
        by_decl = {loc["declaration"]: loc["challenge"] for loc in row["locations"]}
        # the closeMatch was in the strong sample and not drawn under `supporting`; the
        # exactMatch, the passage's basis, and the relatedMatch, in the weak-relation
        # sample, were judged, and a verdict confirmed each
        self.assertEqual(by_decl, {"Widget.Spanning": vocab.CHALLENGE_SURVIVED,
                                   "Widget.card_dvd": vocab.CHALLENGE_PENDING,
                                   "Widget.spanning_conj": vocab.CHALLENGE_SURVIVED})
        self.assertEqual(row["challenge"], vocab.CHALLENGE_PENDING)

    def test_ch2_the_export_carries_pending_for_the_consumer(self):
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        recs = _records(self.paths.export_dir("set-1"))
        absence = next(r for r in recs if r.get("relation") == "noMatch"
                       and r["extensions"][EXT]["item"] == self.items[F.W2][1])
        self.assertEqual(absence["extensions"][EXT]["challenge"], vocab.CHALLENGE_PENDING)
        self.assertEqual(absence["extensions"][EXT]["prominence"], "central")
        self.assertEqual(records.check(absence), [])
        self.assertIn(vocab.CHALLENGE_PENDING, vocab.CHALLENGE_VERDICTS)

    def test_ch2_a_supporting_passage_that_stays_supporting_is_untouched(self):
        self.challenge("--sample-rate", "1", "--absence-sample-rate", "0",
                       run_id="chl-2")                           # every prominence upheld
        row = self.outcome(F.W2, 1, challenge_runs=("chl-2",))
        self.assertEqual(row["prominence"], "supporting")
        # inside the supporting absences' sample, and not drawn
        self.assertEqual(row["absence"]["challenge"], vocab.CHALLENGE_SAMPLED_OUT)


class TestX4RanksAfterARefute(Campaign):
    """The field case: a subject passage with three ranked locations, rank 2 refuted."""

    def setUp(self):
        super().setUp()
        F.plan_locate(self, self.root)
        docs = F.locate_documents(self.paths, "loc-1")
        for doc in docs.values():
            if doc["source_id"] == F.W1:
                doc["passages"][0]["locations"].append(
                    F._loc("alpha", "Widget.card_dvd", "hasPart", 3))
        F.write_harvest(self.paths, "loc-1", docs)
        self.subject = F.frozen_formal_math(self.paths, F.W1)[0]["item"]

        def refute_rank_2(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "location" and rec["item"] == self.subject \
                    and rec.get("declaration") == "Beta.Widget.gadget_of_finite":
                v.update(action="refute", witness="states the finite case of another object")
            return v
        self.challenge(verdict=refute_rank_2)

    def test_x4_the_locations_a_refute_leaves_are_ranked_one_to_n(self):
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        row = next(r for r in built["outcomes"] if r["item"] == self.subject)
        self.assertEqual([(l["declaration"], l["rank"]) for l in row["locations"]],
                         [("Widget.exists_spanning_gadget", 1), ("Widget.card_dvd", 2)])
        self.assertEqual([l["declaration"] for l in row["refuted"]],
                         ["Beta.Widget.gadget_of_finite"])
        code, out, err = ws(self.root, "export", "literature-links", "--run", "loc-1",
                            "--challenge-run", "chl-1", "--set-id", "set-1")
        self.assertEqual(code, 0, out + err)
        ranks = sorted(r["extensions"][EXT]["rank"] for r in _records(
            self.paths.export_dir("set-1")) if r["extensions"][EXT]["item"] == self.subject)
        self.assertEqual(ranks, [1, 2])

    def test_x4_an_unchallenged_passage_keeps_its_order(self):
        built = outcomes.build(self.paths, "loc-1")
        row = next(r for r in built["outcomes"] if r["item"] == self.subject)
        self.assertEqual([l["rank"] for l in row["locations"]], [1, 2, 3])


class TestX5LocationsAnOverturnAdds(Campaign):

    def test_x5_they_survive_and_the_passage_says_it_changed(self):
        self.locate()
        self.challenge()                 # the default overturns W2's subject absence
        item = F.frozen_formal_math(self.paths, F.W2)[0]["item"]
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        row = next(r for r in built["outcomes"] if r["item"] == item)
        self.assertEqual(row["outcome"], "located")
        self.assertEqual([(l["declaration"], l["challenge"]) for l in row["locations"]],
                         [("Beta.gadget_lattice_height", vocab.CHALLENGE_SURVIVED)])
        self.assertEqual(row["challenge"], vocab.CHALLENGE_DOWNGRADED)
        code, out, err = ws(self.root, "export", "literature-links", "--run", "loc-1",
                            "--challenge-run", "chl-1", "--set-id", "set-1")
        self.assertEqual(code, 0, out + err)
        (rec,) = [r for r in _records(self.paths.export_dir("set-1"))
                  if r["extensions"][EXT]["item"] == item]
        self.assertEqual(rec["extensions"][EXT]["challenge"], vocab.CHALLENGE_SURVIVED)


if __name__ == "__main__":
    unittest.main()
