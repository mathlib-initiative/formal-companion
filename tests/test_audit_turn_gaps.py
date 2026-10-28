"""The turn machine under audit rule 3: a notation sentence left in a gap, end to end.

New code, for references/locate-documents.md sections 13.9 and 13.12:
``scripts/run/audit_turn.py`` with ``CF_AUDIT_RULE=3`` -- ``--rule 3`` on every host tool
call and validator line, ``--gate-config`` on every strip (the gap flags' spans are read on
the layer), each part's gaps named in its call's block, a gap flag placed in the part that
shows its gap, the gap hunt's skeptic call for a part with an unflagged gap, the skeptic's
parts combined with their ``examined_gaps`` and ``missed_gaps``, and a gap verdict weighed
in a revision session -- driven turn by turn with fake agents over the real host tools and
gate, on ``tests/_locate_fixtures.py``'s made-up "Widget gaps" page under the prose floor:
the notation gap flagged, upheld, repaired with an ``add`` through ``inventory_edit.py
--rule 3``, ``passage_ids.py`` and the gate, checked and approved.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
for path in (SCRIPTS, SCRIPTS / "run", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import _locate_fixtures as F  # noqa: E402
import test_audit_turn as TA  # noqa: E402
import test_audit_turn_skips as TT  # noqa: E402
import test_locate_gate_statement_cut as SC  # noqa: E402
import test_locate_inventory_gaps as TG  # noqa: E402
import test_verify_locate as G  # noqa: E402
from cflib import vocab  # noqa: E402
from run import attempt_cap  # noqa: E402

at, ia = TA.at, TA.ia
SID, TASK = TA.SID, TA.TASK
SCHEMA3 = ia.AUDIT_SCHEMAS[3]


class Agents3(TT.Agents2):
    """:class:`test_audit_turn_skips.Agents2` writing rule-3 files: every gap of its part
    judged (``gap_flag`` by gap id, else clean), gap flags ruled with evidence from inside
    the gap, the gap hunt over every unflagged gap of the part in round 1."""

    def __init__(self):
        super().__init__(F.GAP_LAYER)
        self.gap_flag: dict[str, dict] = {}
        self.uphold_gaps = True

    def head(self, kind: str, k: int) -> dict:
        return {"schema": SCHEMA3, "kind": kind, "round": k, "source_id": SID}

    def audit(self, turn, call) -> dict:
        data = super().audit(turn, call)
        index = json.loads((turn.mirror / "r1.render.json").read_text())
        rows = []
        for gid in index["parts"][call.part - 1]["gaps"]:
            row = {"gap": gid, "verdict": "clean", "rule": 0, "merge_with": "", "reason": "",
                   "action": "", "statements": []}
            row.update(self.gap_flag.get(gid, {}))
            rows.append(row)
        return {**data, "gaps": rows}

    def skeptic(self, turn, call) -> dict:
        k, i = call.round, call.part
        flags = json.loads((turn.mirror / f"r{k}.flags.part{i}.json").read_text())
        data = super().skeptic(turn, call)
        verdicts = []
        for f, v in zip(flags["flags"], data["verdicts"]):
            if f.get("gap"):
                a, _b = f["span"]
                ok = self.uphold_gaps
                v = {**v, "upheld": ok, "verdict": f["verdict"], "rule": f["rule"] if ok else 0,
                     "evidence": " ".join(self.layer[a:].split()[:4]) if ok else ""}
            verdicts.append(v)
        index = json.loads((turn.mirror / f"r{k}.render.json").read_text())
        flagged = {f.get("gap") for f in flags["flags"]}
        examined = [gid for gid in index["parts"][i - 1].get("gaps") or []
                    if gid not in flagged] if k == 1 else []
        return {**data, "verdicts": verdicts, "examined_gaps": examined, "missed_gaps": []}


class GapTurnCase(TA.TurnCase):
    """A task worktree holding the Widget-gaps cut committed (its notation sentence in a
    gap), its turns under CF_AUDIT_RULE=3 and the prose floor."""

    LAYER = F.GAP_LAYER
    FORMULAS = F.GAP_FORMULAS
    PARAGRAPHS = F.GAP_PARAGRAPHS
    KEYS = {**SC.CUT_3, "prose_floor_rule": vocab.PROSE_FLOOR_RULE}

    def setUp(self):
        super().setUp()
        self.fx.manifest(TASK, {"source_id": G.SID, "source_sha": G.HTML_SHA,
                                "layer_sha256": self.fx.layer_sha, "renderer": "web-html/1",
                                "finders": []})
        doc = {**self.fx.header(TASK), **F.gap_body()}
        self.doc_path.write_text(json.dumps(doc), encoding="utf-8")
        self.doc_file.write_text(json.dumps(doc), encoding="utf-8")
        self.commit("the inventory: the widget-gaps cut")
        self.agents = Agents3()
        self.runner = TA.InProcess(self.agents)
        self.env["CF_AUDIT_RULE"] = "3"

    def host(self, turn, script: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", str(self.gate_dir / script), *argv],
                              capture_output=True, text=True, cwd=str(turn.worktree),
                              env={"PATH": "/usr/bin:/bin", "HOME": str(self.root)},
                              timeout=120)

    def repair(self):
        """The worker's revision: one ``add`` of the notation passage per gap verdict,
        after the passage the verdict names, applied with ``inventory_edit.py --rule 3``,
        re-keyed, gated and committed, every verdict reported applied."""
        def edit(turn, call):
            if call.step != "revision":
                return
            k = json.loads(sorted(turn.mirror.glob("r*.verdicts.json"))[-1].read_text())["round"]
            verdicts = json.loads((turn.mirror / f"r{k}.verdicts.json").read_text())["verdicts"]
            ops = []
            for v in verdicts:
                for st in v.get("statements") or []:
                    ops.append({"op": "add", "after": v["after"], "verdict": v["id"],
                                "quote": {"exact": st["at"]},
                                "fields": {"statement": "N is the product of the counts",
                                           "passage_kind": st["passage_kind"],
                                           "scope": "formal_math",
                                           "prominence": "supporting"}})
            (turn.mirror / f"r{k}.ops.json").write_text(json.dumps(
                {**self.agents.head("ops", k), "ops": ops}), encoding="utf-8")
            (turn.mirror / f"r{k}.repair.json").write_text(json.dumps(
                {**self.agents.head("repair", k), "entries": [
                    {"verdict_id": v["id"], "status": "applied", "why": ""}
                    for v in verdicts]}), encoding="utf-8")
            done = self.host(turn, "inventory_edit.py", "--document", turn.doc_rel, "--ops",
                             f".tend/audit/r{k}.ops.json", "--base",
                             f".tend/audit/r{k}.document.json", "--verdicts",
                             f".tend/audit/r{k}.verdicts.json", "--rule", "3", "--write")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            done = self.host(turn, "passage_ids.py", "--source-dir",
                             str(Path(self.fx.gate["sourceset"]["sources_dir"]) / SID),
                             "--document", turn.doc_rel, "--write")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            gate = self.host(turn, "verify_locate.py", "--gate-config",
                             str(self.gate_dir / "gate.json"), "--findings", turn.doc_rel)
            self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
            self.commit(f"audit revision {k}")
        return edit

    def record_tools(self):
        self.tools: list[tuple[str, list[str]]] = []
        real = self.runner.tool

        def tool(turn, script, args):
            self.tools.append((script, list(args)))
            return real(turn, script, args)
        self.runner.tool = tool


class TestANotationSentenceLeftInAGap(GapTurnCase):

    def test_flagged_upheld_repaired_checked_and_approved(self):
        self.record_tools()
        self.agents.gap_flag["r1-g001"] = TG.NOTATION_FLAG
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "request_changes")
        r1 = self.auth("r1.verdicts.json")
        self.assertEqual((r1["schema"], r1["decision_rule"]), (SCHEMA3, "audit/3"))
        (v,) = r1["verdicts"]
        self.assertEqual((v["gap"], v["origin"], v["verdict"], v["statements"]),
                         ("r1-g001", "auditor", "gap_statements",
                          TG.NOTATION_FLAG["statements"]))
        self.assertEqual((r1["counts"]["gaps"], r1["counts"]["gaps_upheld"]), (1, 1))
        self.assertEqual(r1["gap_report"]["by_section"], {"Moduli": {"gaps": 1, "words": 15}})
        # every host tool call names the rule, and every strip reads the gaps on the layer
        self.assertTrue(all(args[-2:] == ["--rule", "3"] for _s, args in self.tools),
                        self.tools)
        strips = [args for _s, args in self.tools if args[:1] == ["strip"]]
        self.assertTrue(strips)
        self.assertTrue(all("--gate-config" in args for args in strips), strips)
        for call in self.runner.steps():
            self.assertIn("- The gaps this part shows: r1-g001.", call.prompt)
            line = call.prompt.split("Check it before you finish:")[1].splitlines()[0]
            self.assertIn("--rule 3", line)
        self.assertEqual(self.state()["rule"], 3)
        # the revision: an add of the notation passage, through the edit tool, ids and gate
        self.agents.edit = self.repair()
        code, _out = self.turn("worker")
        self.assertEqual(code, 0, self.last_err)
        doc = json.loads(self.doc_file.read_text())
        notation = [p for p in doc["passages"] if p["passage_kind"] == "notation"]
        self.assertEqual(len(notation), 1)
        # round 2 checks it applied and approves; no gap is left
        self.agents.statuses = {v["id"]: "applied"}
        self.runner.calls.clear()
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        r2 = self.auth("r2.verdicts.json")
        self.assertEqual((r2["decision"], r2["verdicts"], r2["counts"]["status_applied"]),
                         ("approve", [], 1))
        self.assertEqual(r2["gap_report"]["gaps"], 0)
        self.assertEqual(r2["history"][0]["span"], v["span"])
        diff = (attempt_cap.audit_task_dir(self.root, TASK) / SID / "r2.diff.md").read_text()
        self.assertIn("GAP VERDICT r1-v001 on r1-g001 [979, 1048]: 15 word(s) held by no "
                      "passage and no skip before, 0 now; 1 of its 1 statement(s) now start "
                      "inside a passage.", diff)
        self.assertEqual(self.rejections(), 1)

    def test_a_gap_judged_clean_approves(self):
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        verdicts = self.auth("r1.verdicts.json")
        self.assertEqual((verdicts["verdicts"], verdicts["counts"]["gaps_judged"]), ([], 1))
        self.assertEqual(self.auth("r1.skeptic.json")["examined_gaps"], ["r1-g001"])

    def test_a_refuted_gap_flag_raises_nothing(self):
        self.agents.gap_flag["r1-g001"] = TG.NOTATION_FLAG
        self.agents.uphold_gaps = False
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        self.assertEqual(self.auth("r1.verdicts.json")["refuted"][0]["gap"], "r1-g001")


class TestTheGapHunt(GapTurnCase):
    """A part with no flag and no formal passage still gets a skeptic call when it shows an
    unflagged gap; under rule 2 it would get none."""

    PART_BYTES = "1500"

    def setUp(self):
        super().setUp()
        doc = json.loads(self.doc_file.read_text())
        for p in doc["passages"]:
            if p["section"] == ["Moduli"]:
                p.update(scope="expository", prominence="supporting")
        self.doc_file.write_text(json.dumps(doc), encoding="utf-8")
        self.commit("the moduli scoped expository")

    def test_the_part_that_shows_the_gap_is_hunted(self):
        code, _out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        index = self.auth("r1.render.json")
        part = next(i for i, p in enumerate(index["parts"], 1) if "r1-g001" in p["gaps"])
        self.assertEqual(index["parts"][part - 1]["formal"], [])
        skeptics = list(self.runner.steps("skeptic"))
        self.assertIn(part, [c.part for c in skeptics])
        self.assertIn("r1-g001", self.auth("r1.skeptic.json")["examined_gaps"])


class TestTheSessionsWeighAGapVerdict(unittest.TestCase):

    def test_a_gap_verdict_weighs_its_statements(self):
        verdicts = [{"id": "r1-v001", "verdict": "gap_statements",
                     "statements": [{"at": "a b c"}] * 3},
                    {"id": "r1-v002", "verdict": "wrong_kind"},
                    {"id": "r1-v003", "verdict": "gap_statements", "statements": []}]
        turn = mock.Mock(rule=3)
        with mock.patch.object(at, "REPAIR_VERDICTS_PER_SESSION", 3):
            self.assertEqual(at.Turn.sessions(turn, verdicts),
                             [["r1-v001"], ["r1-v002", "r1-v003"]])
        # rule 1 counts verdicts, whatever they are
        turn = mock.Mock(rule=1)
        with mock.patch.object(at, "REPAIR_VERDICTS_PER_SESSION", 3):
            self.assertEqual(at.Turn.sessions(turn, verdicts),
                             [["r1-v001", "r1-v002", "r1-v003"]])

    def test_rule_2_strips_without_the_layer(self):
        turn = mock.Mock(rule=2, gate_config="g.json")
        self.assertEqual(at.Turn.gap_source(turn), [])
        turn = mock.Mock(rule=3, gate_config="g.json")
        self.assertEqual(at.Turn.gap_source(turn), ["--gate-config", "g.json"])


if __name__ == "__main__":
    unittest.main()
