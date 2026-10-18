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

And from the re-export of smoke 2 in the agreed format (the proof run):

* P1 -- the seal still carried ``runs`` and ``sourceset.version`` beside the agreed
  keys, and the consumer refuses a seal with any other key; a run token named every
  challenge run, where the agreed grammar admits one;
* P2 -- FTC #19a718e1's downgrade note was its witness's first sentence, which
  quotes 19 words of the article ("The passage says only that '...' -- i.e. ..."),
  and the export refused the passage over the quote cap: the note is the first
  sentence the cap admits, and "i.e." ends no sentence.
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
        self.assertEqual(set(seal["sourceset"]), {"id", "manifest_sha256"})
        self.assertRegex(seal["sourceset"]["manifest_sha256"], r"\A[0-9a-f]{64}\Z")
        self.assertRegex(seal["skill_commit"], r"\A[0-9a-f]{40}\Z")
        self.assertRegex(seal["created"], r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")
        self.assertEqual(seal["byte_budget"], 0)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", sha) for sha in seal["files"].values()))
        self.assertEqual(export_mod.verify_seal(out_dir), [])


    def test_p1_the_seal_has_exactly_the_agreed_keys(self):
        """The consumer refuses ``runs`` and ``sourceset.version`` by name; the report
        keeps them."""
        self.assertEqual(self.export()[0], 0)
        seal = read_json(self.paths.export_dir("set-1") / "SEAL.json")
        self.assertEqual(set(seal), {"schema", "set_id", "campaign", "universe", "sourceset",
                                     "skill_commit", "created", "byte_budget", "files"})
        self.assertEqual(set(seal), set(export_mod.SEAL_KEYS))
        self.assertEqual(set(seal["universe"]), {"id", "universe_sha256"})
        self.assertEqual(set(seal["sourceset"]), {"id", "manifest_sha256"})
        report = read_json(self.paths.export_report_json("set-1"))
        self.assertEqual((report["run_id"], report["challenge_runs"], report["inventory_run"]),
                         ("loc-1", ["chl-1"], "inv-1"))
        self.assertEqual(report["campaign"], seal["campaign"])
        self.assertEqual(report["sourceset"]["manifest_sha256"],
                         seal["sourceset"]["manifest_sha256"])
        self.assertIsInstance(report["sourceset"]["version"], int)

    def test_p1_a_campaign_the_grammar_refuses_writes_nothing(self):
        path = self.paths.run_yaml("loc-1")
        for bad in ("run:loc-1", "wpm tier a", "-wpm"):
            run = read_yaml(path)
            run["locate"]["campaign"] = bad
            write_yaml(path, run)
            with self.subTest(campaign=bad):
                code, out, err = self.export(set_id="set-bad")
                self.assertNotEqual(code, 0, out + err)
                self.assertIn("not a campaign id a record set may carry", out + err)
                self.assertFalse(self.paths.export_dir("set-bad").exists())


class TestTheCampaignToken(unittest.TestCase):
    """``run:<run-id>[+<challenge-run-id>]`` or a campaign id, and nothing else."""

    def test_p1_a_run_token_names_at_most_one_challenge_run(self):
        run = {"locate": {}}
        self.assertEqual(export_mod.campaign_token(run, "loc-1", []), "run:loc-1")
        self.assertEqual(export_mod.campaign_token(run, "loc-1", ["chl-1"]), "run:loc-1+chl-1")
        with self.assertRaises(export_mod.CfError) as caught:
            export_mod.campaign_token(run, "loc-1", ["chl-1", "chl-2"])
        self.assertIn("at most one challenge run", str(caught.exception))

    def test_p1_a_campaign_shard_may_apply_several_challenge_runs(self):
        run = {"locate": {"campaign": "wpm-tier-a"}}
        self.assertEqual(export_mod.campaign_token(run, "loc-1", ["chl-1", "chl-2"]),
                         "wpm-tier-a")

    def test_p1_only_ids_the_grammar_admits(self):
        for run_id in ("loc 1", "loc+1", "loc:1", "-loc", "l" * 65):
            with self.subTest(run_id=run_id), self.assertRaises(export_mod.CfError):
                export_mod.campaign_token({"locate": {}}, run_id, [])
        for campaign in ("run:x", "a b", "x" * 129, "+x"):
            with self.subTest(campaign=campaign), self.assertRaises(export_mod.CfError):
                export_mod.campaign_token({"locate": {"campaign": campaign}}, "loc-1", [])
        self.assertEqual(export_mod.campaign_token({"locate": {"campaign": "w:p.m_3-a"}},
                                                   "loc-1", []), "w:p.m_3-a")


class TestTheSealsOtherTokens(_Exported):
    """R2-C: ``set_id`` and ``sourceset.id`` pass the consumer's token grammar.

    The review's round trips: ``--set-id smoke+2`` and ``--set-id _draft``, and a
    sourceset ``wpm+math``, each exported with nothing refused and each refused by
    the consumer over the seal (``set_id must be a token``, ``sourceset.id must be
    a token``).  The campaign token was already checked (P1); these were not.
    """

    def setUp(self):
        super().setUp()
        self.challenge()

    def test_r2_a_set_id_the_grammar_refuses_writes_nothing(self):
        for bad in ("smoke+2", "_draft", "a b", "s" * 129):
            with self.subTest(set_id=bad):
                code, out, err = self.export(set_id=bad)
                self.assertNotEqual(code, 0, out + err)
                self.assertIn("--set-id", out + err)
                self.assertIn("not a token a record set's seal may carry", out + err)
                self.assertFalse((self.paths.exports_dir / bad).exists())
                self.assertFalse(self.paths.export_report_json(bad).exists())
        code, out, err = self.export(set_id="smoke-2b:rerun.1")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_json(self.paths.export_dir("smoke-2b:rerun.1") / "SEAL.json")
                         ["set_id"], "smoke-2b:rerun.1")


class TestASourcesetIdTheSealCannotCarry(Campaign):
    """R2-C: a sourceset made before ``cf sourceset create`` checked its id."""

    BAD = "wpm+math"

    def setUp(self):
        from unittest import mock

        with mock.patch.object(F, "SS", self.BAD), \
                mock.patch("sources.sourceset.check_new_id", lambda sid: sid, create=True):
            super().setUp()
            self.locate()
            self.challenge()

    def test_r2_the_export_refuses_it_before_a_file_is_written(self):
        self.assertEqual(read_yaml(self.paths.run_yaml("loc-1"))["locate"]["sourceset"]["id"],
                         self.BAD)
        code, out, err = ws(self.root, "export", "literature-links", "--run", "loc-1",
                            "--challenge-run", "chl-1", "--set-id", "set-1")
        self.assertNotEqual(code, 0, out + err)
        self.assertIn(f"sourceset id {self.BAD!r} is not a token", out + err)
        self.assertFalse(self.paths.export_dir("set-1").exists())

    def test_r2_cf_sourceset_create_refuses_it_where_it_is_chosen(self):
        for bad in ("wpm+2", "_wpm", "w" * 129):
            with self.subTest(sourceset=bad):
                code, out, err = ws(self.root, "sourceset", "create", bad)
                self.assertNotEqual(code, 0, out + err)
                self.assertIn("is not letters, digits and ._:-", out + err)
                self.assertFalse((self.paths.sourcesets_dir / bad).exists())
                code, out, err = ws(self.root, "sourceset", "create", bad, "--from", self.BAD)
                self.assertNotEqual(code, 0, out + err)
                self.assertIn("is not letters, digits and ._:-", out + err)
        code, out, err = ws(self.root, "sourceset", "create", "wpm-4:b")
        self.assertEqual(code, 0, out + err)


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

    def test_p2_a_witness_that_opens_by_quoting_the_passage_lends_its_next_sentence(self):
        """Field case FTC #19a718e1, reduced: the witness's first sentence quotes 14
        words of the article and runs through an "i.e."; the old note was that
        sentence (cut at the "i.e."), and the export refused the passage."""
        witness = ("The passage says only that 'every finite widget has a spanning gadget, and "
                   "it is a central result of the subject' -- i.e. G has one. The declaration "
                   "indexes gadgets from 0 where the passage counts from 1. Otherwise it "
                   "states the passage.")

        def downgrade(rec):
            v = F.default_verdict(rec)
            if rec["target"] == "location" and rec.get("relation") == "exactMatch" \
                    and rec.get("declaration") == "Widget.exists_spanning_gadget":
                v = {"item": rec["item"], "target": "location",
                     "location_key": rec["location_key"], "action": "downgrade",
                     "relation": "closeMatch", "witness": witness}
            return v
        self.challenge(verdict=downgrade)
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_json(self.paths.export_report_json("set-1"))["refused"], [])
        (rec,) = [r for r in _records(self.paths.export_dir("set-1"))
                  if r["object"] and r["object"][0].get("location", {}).get("declaration")
                  == "Widget.exists_spanning_gadget"]
        self.assertEqual((rec["relation"], rec["extensions"][EXT]["challenge"]),
                         ("closeMatch", "downgraded"))
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


class TestTheDowngradeNote(unittest.TestCase):
    """``outcomes.downgrade_note`` against a layer (P2), without a campaign."""

    LAYER = ("In widget theory, the widget lemma states that every finite widget has a "
             "spanning gadget, and it is a central result of the subject that every course "
             "on widgets covers.")
    QUOTING = ("The passage says only that 'every finite widget has a spanning gadget, and it "
               "is a central result of the subject' -- i.e. G has one.")

    def test_p2_an_abbreviation_ends_no_sentence(self):
        self.assertEqual(outcomes.witness_sentences(
            "It holds, i.e. for all n. It is stronger (e.g. strict). Done!  Next?"),
            ["It holds, i.e. for all n.", "It is stronger (e.g. strict).", "Done!", "Next?"])
        self.assertEqual(outcomes.witness_sentences(" \n "), [])

    def test_p2_the_first_sentence_the_quote_cap_admits(self):
        verdict = {"witness": self.QUOTING + " It indexes from 0. It states the rest."}
        self.assertEqual(outcomes.downgrade_note(verdict, self.LAYER), "It indexes from 0.")
        self.assertIsNone(records.verbatim_run(outcomes.downgrade_note(verdict, self.LAYER),
                                               self.LAYER))
        # without the layer nothing can be checked: the first sentence, as before
        self.assertEqual(outcomes.downgrade_note(verdict), self.QUOTING)
        # every sentence quotes: the first, which the export refuses by name
        self.assertEqual(outcomes.downgrade_note({"witness": self.QUOTING}, self.LAYER),
                         self.QUOTING)
        # the challenger's own note is never second-guessed here
        self.assertEqual(outcomes.downgrade_note({"note": "0-indexed", "witness": self.QUOTING},
                                                 self.LAYER), "0-indexed")


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
