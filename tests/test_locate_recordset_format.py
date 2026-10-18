"""The sealed record set in the shape its consumer reads (smoke 2: X1, X2).

New code: regression tests for the record-set mismatches the second locate smoke
run found between ``cf export literature-links`` and the first consumer's store
builder, each reduced to the synthetic campaign of ``tests/_locate_fixtures.py``
(``Widget lemma``; libraries ``alpha`` and ``beta``; no article text).  The shape
is references/locate-documents.md section 6 and docs/proposals/wikipedia-locate.md
sections 2.3 and 2.7:

* M1 -- ``SEAL.json`` ``campaign`` was null for a run in no campaign; it is a
  token: the campaign id, else ``run:<locate run>[+<challenge run>...]``;
* M2-M4 -- ``universe.json`` was a dict of libraries under ``sha256`` with no
  ``frozen`` or ``roots``; it is a LIST of libraries under ``universe_sha256``
  with ``frozen`` and every library's ``roots``;
* M5 -- a published index records ``line`` only: the lean end then carries no
  ``end_line``, never a null or a guessed one;
* M6 -- an ``exactMatch`` a challenge downgraded to ``closeMatch`` was exported
  with no note; the challenger's note, else its witness's first sentence, is it;
* M7 -- no record carried its passage's ``section`` or ``prominence``;
* X1 -- an absence carried one ``lean`` end per library WITH an extension (about
  12 KB at 48 libraries) and the default 256 KiB per-source budget refused 4 of
  10 sources; an absence's ends are ``{type, uri, version}`` only, the default
  budget is off and the seal records the budget that applied.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import re
import shutil
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
from cflib.jsonio import read_json  # noqa: E402
from cflib.yamlio import read_yaml, write_yaml  # noqa: E402
from locate import export as export_mod, outcomes, records  # noqa: E402

from test_locate_planner import Campaign, ws  # noqa: E402

EXT = records.EXTENSION
UNIVERSE_LIBRARY_KEYS = {"id", "brain_lib", "repo", "commit", "toolchain", "roots", "licence",
                         "display_name"}


def _records(out_dir: Path) -> list[dict]:
    return [json.loads(line) for path in sorted((out_dir / "records").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


class _Exported(Campaign):

    def setUp(self):
        super().setUp()
        self.locate()

    def export(self, *extra, set_id="set-1", challenge=("chl-1",)):
        argv = ["export", "literature-links", "--run", "loc-1", "--set-id", set_id]
        for run in challenge:
            argv += ["--challenge-run", run]
        return ws(self.root, *argv, *extra)

    def rechallenge(self, verdict):
        shutil.rmtree(self.paths.corpus_run_dir("chl-1"))
        F.write_harvest(self.paths, "chl-1",
                        F.challenge_documents(self.paths, "chl-1", verdict=verdict))


class TestTheSeal(_Exported):

    def setUp(self):
        super().setUp()
        self.challenge()

    def test_m1_a_run_in_no_campaign_is_sealed_under_its_runs(self):
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        seal = read_json(self.paths.export_dir("set-1") / "SEAL.json")
        self.assertEqual(seal["campaign"], "run:loc-1+chl-1")
        self.assertRegex(seal["campaign"], r"\A[A-Za-z0-9][A-Za-z0-9._:+-]*\Z")

    def test_m1_a_shard_is_sealed_under_its_campaign(self):
        path = self.paths.run_yaml("loc-1")
        run = read_yaml(path)
        run["locate"]["campaign"] = "wpm-tier-a"
        write_yaml(path, run)
        self.assertEqual(self.export()[0], 0)
        seal = read_json(self.paths.export_dir("set-1") / "SEAL.json")
        self.assertEqual(seal["campaign"], "wpm-tier-a")

    def test_the_seal_carries_every_agreed_key(self):
        self.assertEqual(self.export()[0], 0)
        out_dir = self.paths.export_dir("set-1")
        seal = read_json(out_dir / "SEAL.json")
        pin = F.universe_pin(self.paths)
        self.assertEqual(seal["schema"], "cf-locate-recordset/1")
        self.assertEqual(seal["set_id"], "set-1")
        self.assertEqual(seal["universe"], {"id": pin["id"], "universe_sha256": pin["sha256"]})
        self.assertLessEqual({"id", "manifest_sha256"}, set(seal["sourceset"]))
        self.assertRegex(seal["sourceset"]["manifest_sha256"], r"\A[0-9a-f]{64}\Z")
        self.assertRegex(seal["skill_commit"], r"\A[0-9a-f]{40}\Z")
        self.assertRegex(seal["created"], r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")
        self.assertEqual(seal["byte_budget"], 0)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", sha) for sha in seal["files"].values()))
        self.assertEqual(export_mod.verify_seal(out_dir), [])


class TestTheUniverseFile(_Exported):

    def setUp(self):
        super().setUp()
        self.challenge()
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        self.out_dir = self.paths.export_dir("set-1")
        self.uni = read_json(self.out_dir / "universe.json")

    def test_m2_m4_libraries_are_a_list_under_universe_sha256(self):
        uni = self.uni
        self.assertNotIn("sha256", uni)
        self.assertEqual(uni["universe_sha256"],
                         read_json(self.out_dir / "SEAL.json")["universe"]["universe_sha256"])
        self.assertEqual(uni["id"], F.U)
        self.assertEqual(uni["frozen"], "2026-10-17")
        self.assertIsInstance(uni["libraries"], list)
        self.assertEqual([lib["id"] for lib in uni["libraries"]], ["alpha", "beta"])
        for lib in uni["libraries"]:
            with self.subTest(library=lib["id"]):
                self.assertEqual(set(lib), UNIVERSE_LIBRARY_KEYS)
                self.assertEqual(lib["repo"], F.LIBRARIES[lib["id"]]["repo"])
                self.assertEqual(lib["commit"], F.LIBRARIES[lib["id"]]["commit"])
                self.assertEqual(lib["roots"], F.LIBRARIES[lib["id"]]["roots"])
                self.assertEqual(lib["toolchain"], F.TOOLCHAIN)
                self.assertEqual(lib["licence"], {"spdx": F.LIBRARIES[lib["id"]]["licence"]})
                self.assertEqual(lib["display_name"], lib["brain_lib"])
        self.assertEqual(uni["excluded"], [{"id": "gamma", "reason": "did not build"}])


class TestTheRecords(_Exported):

    def test_m7_every_record_carries_its_passages_section_and_prominence(self):
        self.challenge()
        self.assertEqual(self.export()[0], 0)
        built = outcomes.build(self.paths, "loc-1", challenge_runs=["chl-1"])
        want = {(r["source_id"], r["item"]): (r["section"], r["prominence"])
                for r in built["outcomes"]}
        recs = _records(self.paths.export_dir("set-1"))
        self.assertTrue(recs)
        for rec in recs:
            ext = rec["extensions"][EXT]
            sid = rec["id"].split("::")[0]
            with self.subTest(record=rec["id"], relation=rec.get("relation")):
                self.assertIsInstance(ext["section"], list)
                self.assertIn(ext["prominence"], vocab.PROMINENCE)
                self.assertEqual((ext["section"], ext["prominence"]), want[(sid, ext["item"])])

    def test_x1_an_absence_names_each_library_by_a_minimal_end(self):
        def uphold_absence(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "absence":
                v = {"item": rec["item"], "target": "absence", "action": "uphold",
                     "witness": "searched gadget lattice height bounds"}
            return v
        self.challenge(verdict=uphold_absence)
        self.assertEqual(self.export()[0], 0)
        (absence,) = [r for r in _records(self.paths.export_dir("set-1"))
                      if r.get("relation") == "noMatch"]
        self.assertEqual(absence["object"], [
            {"type": "lean", "uri": F.LIBRARIES[lib]["repo"],
             "version": F.LIBRARIES[lib]["commit"]} for lib in ("alpha", "beta")])
        self.assertEqual(records.check(absence), [])

    def test_m6_a_downgrade_to_close_match_keeps_a_note(self):
        witness = ("The declaration indexes gadgets from 0 where the passage counts from 1. "
                   "Otherwise it states the passage.")

        def downgrade(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "location" and rec.get("relation") == "exactMatch" \
                    and rec.get("declaration") == "Widget.exists_spanning_gadget":
                v = {"item": rec["item"], "target": "location",
                     "location_key": rec["location_key"], "action": "downgrade",
                     "relation": "closeMatch", "witness": witness}
            return v
        self.challenge(verdict=downgrade)
        self.assertEqual(self.export()[0], 0)
        (rec,) = [r for r in _records(self.paths.export_dir("set-1"))
                  if r["object"] and r["object"][0].get("location", {}).get("declaration")
                  == "Widget.exists_spanning_gadget"]
        self.assertEqual(rec["relation"], "closeMatch")
        self.assertEqual(rec["extensions"][EXT]["challenge"], "downgraded")
        self.assertEqual(rec["note"],
                         "The declaration indexes gadgets from 0 where the passage counts from 1.")

    def test_m6_the_challengers_own_note_wins(self):
        self.assertEqual(outcomes.downgrade_note({"note": " 0 ∈ ℕ here ", "witness": "Long."}),
                         "0 ∈ ℕ here")
        long_witness = "word " * 100
        note = outcomes.downgrade_note({"witness": long_witness})
        self.assertLessEqual(len(note), outcomes.NOTE_FROM_WITNESS_CHARS + 1)
        self.assertTrue(note.endswith("\u2026"))
        self.assertEqual(outcomes.downgrade_note({}), "")


class TestTheBudget(_Exported):

    def setUp(self):
        super().setUp()
        self.challenge()

    def test_x1_no_budget_applies_unless_asked_and_the_seal_says_which(self):
        self.assertEqual(export_mod.DEFAULT_BYTE_BUDGET, 0)
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        report = read_json(self.paths.export_report_json("set-1"))
        self.assertEqual((report["byte_budget"], report["refused"]), (0, []))
        code, out, err = self.export("--byte-budget", "200", set_id="set-capped")
        self.assertEqual(code, 1, out + err)
        seal = read_json(self.paths.export_dir("set-capped") / "SEAL.json")
        self.assertEqual(seal["byte_budget"], 200)
        report = read_json(self.paths.export_report_json("set-capped"))
        self.assertTrue(all("over the per-source budget of 200" in r["reason"]
                            for r in report["refused"]))


class TestRecordUnits(unittest.TestCase):
    """The record builder on the field case's shapes, without a campaign."""

    LAYER = ("a widget meets every gadget once and a widget meets every gadget twice "
             "while sprockets turn and the widget lemma holds in every finite case.")

    def source(self):
        return {"source_id": F.W1, "pageid": 1001, "revision": 2002, "title": "Widget lemma",
                "html_sha256": "1" * 64, "renderer": "web-html/3",
                "uri": "https://en.wikipedia.org/w/index.php?curid=1001&oldid=2002",
                "container_title": "Wikipedia", "licence": "CC-BY-SA-4.0"}

    def universe(self, n=2):
        libs = [{"id": f"lib{i:02d}", "repo": f"https://example.org/lib{i:02d}",
                 "commit": f"{i:040x}"} for i in range(1, n + 1)]
        return {"id": "u1", "sha256": "2" * 64, "libraries": libs}

    def passage(self, **kw):
        return {"source_id": F.W1, "item": "In-text #3fa91c07", "passage_kind": "theorem",
                "span": [0, 40], "section": ["Statement", "Finite case"],
                "prominence": "central", "challenge": "survived", **kw}

    def location(self, **kw):
        loc = {"library": "lib01", "declaration": "Widget.x", "relation": "exactMatch",
               "rank": 1, "confidence": "high", "note": "", "location_key": "3" * 64,
               "proof_status": "proved", "module": "Alpha.W", "path": "Alpha/W.lean",
               "line": 81, "challenge": "survived"}
        loc.update(kw)
        return loc

    def test_m5_a_line_only_index_exports_no_end_line(self):
        for given in ({}, {"end_line": None}):
            loc = self.location(**given)
            recs, _ = records.passage_records(
                self.source(), self.passage(outcome="located", status="formalized",
                                            locations=[loc]), self.LAYER, self.universe())
            end_ext = recs[0]["object"][0]["extensions"][EXT]
            with self.subTest(given=given):
                self.assertEqual(end_ext["line"], 81)
                self.assertNotIn("end_line", end_ext)
                self.assertEqual(records.check_all(recs), [])
        recs, _ = records.passage_records(
            self.source(), self.passage(outcome="located", status="formalized",
                                        locations=[self.location(end_line=90)]),
            self.LAYER, self.universe())
        self.assertEqual(recs[0]["object"][0]["extensions"][EXT]["end_line"], 90)

    def test_x1_an_absence_at_48_libraries_is_a_few_kilobytes(self):
        """The field universe had 48 libraries; the absence was about 12.1 KB."""
        passage = self.passage(outcome="absent", status="not_formalized", locations=[],
                               absence={"receipt": {"queries": ["a", "b", "c"],
                                                    "candidates": []},
                                        "challenge": "survived"})
        recs, _ = records.passage_records(self.source(), passage, self.LAYER, self.universe(48))
        (absence,) = recs
        self.assertEqual(len(absence["object"]), 48)
        self.assertTrue(all(set(end) == {"type", "uri", "version"} for end in absence["object"]))
        size = len(records.canonical(absence).encode("utf-8"))
        # the form the smoke exported: every end also named its library in an extension
        before = json.loads(records.canonical(absence))
        for end, lib in zip(before["object"], self.universe(48)["libraries"]):
            end["extensions"] = {EXT: {"library": lib["id"]}}
        self.assertLess(size, 6500)
        self.assertLess(size, 0.65 * len(records.canonical(before).encode("utf-8")))
        self.assertEqual(records.check(absence), [])

    def test_m7_a_passage_with_no_section_or_prominence_is_refused(self):
        for bad in ({"section": None}, {"section": ["Statement", ""]},
                    {"prominence": None}, {"prominence": "minor"}):
            passage = self.passage(outcome="located", status="formalized",
                                   locations=[self.location()], **bad)
            with self.subTest(bad=bad), self.assertRaises(records.RecordError):
                records.passage_records(self.source(), passage, self.LAYER, self.universe())
        # the lead is a section too: its heading path is empty
        recs, _ = records.passage_records(
            self.source(), self.passage(outcome="located", status="formalized", section=[],
                                        locations=[self.location()]),
            self.LAYER, self.universe())
        self.assertEqual(recs[0]["extensions"][EXT]["section"], [])

    def test_m6_a_close_match_with_no_note_is_refused(self):
        passage = self.passage(outcome="located", status="formalized",
                               locations=[self.location(relation="closeMatch", note=" ")])
        with self.assertRaises(records.RecordError) as caught:
            records.passage_records(self.source(), passage, self.LAYER, self.universe())
        self.assertIn("convention difference", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
