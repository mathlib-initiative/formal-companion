"""The turn machine under audit rule 2: a skip that hides statements, end to end.

New code, for references/locate-documents.md sections 13.9 and 13.11:
``scripts/run/audit_turn.py`` with ``CF_AUDIT_RULE=2`` -- ``--rule 2`` on every host tool
call and validator line, the skip hunt's skeptic call for a part with an unflagged skip
over math no passage holds, the skip flags placed by the part that shows their skip, the
revision's sessions weighed, the round's skip report -- driven turn by turn with fake
agents over the real host tools and gate, on ``tests/_locate_fixtures.py``'s made-up
"Widget bounds" page (F1, its proof skipped whole): a skip verdict flagged, upheld,
repaired into passages through ``inventory_edit.py --rule 2``, ``passage_ids.py`` and the
gate, checked and approved; the mechanical skip rule and ``skip_ok``; the random-outcome
property under rule 2; and the rule-1 golden -- the base commit f523584's turn machine and
this one make the same calls, prompts and tool command lines on one run.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
import test_locate_inventory_skips as TS  # noqa: E402
import test_verify_locate as G  # noqa: E402
from run import attempt_cap  # noqa: E402

at, ia = TA.at, TA.ia
SID, TASK = TA.SID, TA.TASK
SCHEMA2 = ia.AUDIT_SCHEMAS[2]
BASE = "f523584"


class Agents2(TA.Agents):
    """:class:`test_audit_turn.Agents` writing rule-2 files: every skip of its part judged
    (``skip_flag`` by skip id, else clean), skip flags ruled with evidence from inside the
    skip, the skip hunt over every unflagged skip of the part."""

    def __init__(self, layer: str = F.SKIP_LAYER):
        super().__init__(layer)
        self.skip_flag: dict[str, dict] = {}
        self.uphold_skips = True

    def head(self, kind: str, k: int) -> dict:
        return {"schema": SCHEMA2, "kind": kind, "round": k, "source_id": SID}

    def audit(self, turn, call) -> dict:
        data = super().audit(turn, call)
        index = json.loads((turn.mirror / "r1.render.json").read_text())
        rows = []
        for sid in index["parts"][call.part - 1]["skips"]:
            row = {"skip": sid, "verdict": "clean", "rule": 0, "merge_with": "", "reason": "",
                   "action": "", "statements": []}
            row.update(self.skip_flag.get(sid, {}))
            rows.append(row)
        return {**data, "skips": rows}

    def skeptic(self, turn, call) -> dict:
        k, i = call.round, call.part
        flags = json.loads((turn.mirror / f"r{k}.flags.part{i}.json").read_text())
        data = super().skeptic(turn, call)
        verdicts = []
        for f, v in zip(flags["flags"], data["verdicts"]):
            if f.get("skip"):
                a, _b = f["span"]
                ok = self.uphold_skips
                v = {**v, "upheld": ok, "rule": f["rule"] if ok else 0,
                     "evidence": " ".join(self.layer[a:].split()[:4]) if ok else ""}
            verdicts.append(v)
        index = json.loads((turn.mirror / f"r{k}.render.json").read_text())
        flagged = {f.get("skip") for f in flags["flags"]}
        examined = [sid for sid in index["parts"][i - 1].get("skips") or []
                    if sid not in flagged] if k == 1 else []
        return {**data, "verdicts": verdicts, "examined_skips": examined, "missed_skips": []}


class SkipTurnCase(TA.TurnCase):
    """A task worktree holding F1 committed, its turns under CF_AUDIT_RULE=2."""

    LAYER = F.SKIP_LAYER
    FORMULAS = F.SKIP_FORMULAS
    PARAGRAPHS = F.SKIP_PARAGRAPHS
    BODY: dict = {}

    def setUp(self):
        super().setUp()
        self.fx.manifest(TASK, {"source_id": G.SID, "source_sha": G.HTML_SHA,
                                "layer_sha256": self.fx.layer_sha, "renderer": "web-html/1",
                                "finders": []})
        doc = {**self.fx.header(TASK), **F.skip_body(**self.BODY)}
        self.doc_path.write_text(json.dumps(doc), encoding="utf-8")
        shutil.copyfile(self.doc_path, self.doc_file)
        self.commit("the inventory: the widget-bounds cut")
        self.agents = Agents2()
        self.runner = TA.InProcess(self.agents)
        self.env["CF_AUDIT_RULE"] = "2"

    # the fake worker's repair: real operations through the staged tools

    def host(self, turn, script: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", str(self.gate_dir / script), *argv],
                              capture_output=True, text=True, cwd=str(turn.worktree),
                              env={"PATH": "/usr/bin:/bin", "HOME": str(self.root)},
                              timeout=120)

    def repair(self, ops_for):
        """A worker edit: ``ops_for(verdicts)`` operations written, applied with
        ``inventory_edit.py --rule 2``, re-keyed and gated, each verdict reported applied,
        and the document committed."""
        def edit(turn, call):
            if call.step != "revision":
                return
            k = json.loads(sorted(turn.mirror.glob("r*.verdicts.json"))[-1].read_text())["round"]
            verdicts = json.loads((turn.mirror / f"r{k}.verdicts.json").read_text())["verdicts"]
            ops = {**self.agents.head("ops", k), "ops": ops_for(verdicts)}
            (turn.mirror / f"r{k}.ops.json").write_text(json.dumps(ops), encoding="utf-8")
            repair = {**self.agents.head("repair", k), "entries": [
                {"verdict_id": v["id"], "status": "applied", "why": ""} for v in verdicts]}
            (turn.mirror / f"r{k}.repair.json").write_text(json.dumps(repair), encoding="utf-8")
            done = self.host(turn, "inventory_edit.py", "--document", turn.doc_rel, "--ops",
                             f".tend/audit/r{k}.ops.json", "--base",
                             f".tend/audit/r{k}.document.json", "--verdicts",
                             f".tend/audit/r{k}.verdicts.json", "--rule", "2", "--write")
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

    def proof_ops(self, verdicts: list[dict]) -> list[dict]:
        """F1's repair: the proof skip dropped, its statement and its narration added."""
        (v,) = [v for v in verdicts if v["verdict"] == "skipped_statements"]
        stmt = TS.TL.formal(json.loads((self.wt / ".tend" / "audit" / "r1.document.json")
                                       .read_text()))[1]
        ops = [{"op": "unskip", "skip": v["skip"], "verdict": v["id"]}]
        for name, kind, scope in (("PF1", "other", "expository"),
                                  ("PF2", "theorem", "formal_math"),
                                  ("PF4", "other", "expository")):
            ops.append({**TS.TestTheRepairOnTheHost.add(stmt, name, kind, scope),
                        "verdict": v["id"]})
        return ops

    def tool_lines(self) -> list[list[str]]:
        return [args for _script, args in self.tools]

    def record_tools(self):
        self.tools: list[tuple[str, list[str]]] = []
        real = self.runner.tool

        def tool(turn, script, args):
            self.tools.append((script, list(args)))
            return real(turn, script, args)
        self.runner.tool = tool


class TestASkipThatHidesStatements(SkipTurnCase):

    def test_flagged_upheld_repaired_checked_and_approved(self):
        self.record_tools()
        self.agents.skip_flag["r1-s003"] = TS.PROOF_FLAG
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "request_changes")
        r1 = self.auth("r1.verdicts.json")
        self.assertEqual((r1["schema"], r1["decision_rule"]), (SCHEMA2, "audit/2"))
        (v,) = r1["verdicts"]
        self.assertEqual((v["skip"], v["origin"], v["verdict"], v["statements"]),
                         ("r1-s003", "auditor", "skipped_statements",
                          TS.PROOF_FLAG["statements"]))
        self.assertEqual(r1["counts"]["skips_upheld"], 1)
        self.assertEqual(r1["skip_report"]["mostly_skipped"], ["Proof"])
        # every host tool call of the round names the rule
        self.assertTrue(all(args[-2:] == ["--rule", "2"] for args in self.tool_lines()),
                        self.tool_lines())
        for call in self.runner.steps():
            self.assertIn("--rule 2", call.prompt.split("Check it before you finish:")[1])
            self.assertIn("- The skips this part shows: r1-s001, r1-s002, r1-s003, r1-s004.",
                          call.prompt)
        self.assertEqual(self.state()["rule"], 2)
        # the revision: unskip and the adds, through the edit tool, the ids and the gate
        self.agents.edit = self.repair(self.proof_ops)
        code, out = self.turn("worker")
        self.assertEqual(code, 0, self.last_err)
        doc = json.loads(self.doc_file.read_text())
        self.assertNotIn(F.PROOF_SKIP["reason"], [s["reason"] for s in doc["skips"]])
        self.assertIn("formal_math", [p["scope"] for p in doc["passages"]
                                      if p["section"] == ["Proof"]])
        # round 2 checks it applied and approves
        self.agents.statuses = {v["id"]: "applied"}
        self.runner.calls.clear()
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        r2 = self.auth("r2.verdicts.json")
        self.assertEqual((r2["decision"], r2["verdicts"], r2["counts"]["status_applied"]),
                         ("approve", [], 1))
        self.assertEqual(r2["history"][0]["span"], F.PROOF_SKIP["span"])
        self.assertEqual(ia.skipped_math_outside(r2["skip_report"]), 0)
        self.assertEqual(self.auth("r2.collateral.json")["collateral"], [])
        diff = (attempt_cap.audit_task_dir(self.root, TASK) / SID / "r2.diff.md").read_text()
        self.assertIn("SKIP VERDICT r1-v001 on r1-s003 [355, 621]: 4 math paragraph(s) unheld "
                      "before, 0 now; 1 of its 1 statement(s) now start inside a passage; the "
                      "skip is gone.", diff)
        self.assertEqual(self.rejections(), 1)
        self.assertEqual(self.state()["approved"]["round"], 2)

    def test_the_auditor_says_clean_and_the_skip_rule_raises_it(self):
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "request_changes")
        (v,) = self.auth("r1.verdicts.json")["verdicts"]
        self.assertEqual((v["skip"], v["origin"], v["rule"], v["action"]),
                         ("r1-s003", "skip_rule", 6, ia.SKIP_RULE_ACTION))

    def test_skip_ok_exempts_it(self):
        self.agents.skip_flag["r1-s003"] = {"skip_ok": True, "reason": "a synthetic exemption"}
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        verdicts = self.auth("r1.verdicts.json")
        self.assertEqual(verdicts["skip_ok"][0]["skip"], "r1-s003")
        self.assertEqual(verdicts["counts"]["skip_ok"], 1)

    def test_a_refuted_skip_flag_still_meets_the_skip_rule(self):
        self.agents.skip_flag["r1-s003"] = TS.PROOF_FLAG
        self.agents.uphold_skips = False
        self.turn("reviewer")
        verdicts = self.auth("r1.verdicts.json")
        self.assertEqual([v["origin"] for v in verdicts["verdicts"]], ["skip_rule"])
        self.assertEqual(verdicts["refuted"][0]["skip"], "r1-s003")


class TestTheSessionsAreWeighed(SkipTurnCase):

    def test_a_heavy_skip_verdict_has_a_session_of_its_own(self):
        self.flag_all()
        self.agents.skip_flag["r1-s003"] = TS.PROOF_FLAG
        self.turn("reviewer")
        verdicts = self.auth("r1.verdicts.json")["verdicts"]
        skip = next(v for v in verdicts if v["verdict"] == "skipped_statements")
        self.assertEqual(skip["unheld"], 4)                   # its weight: max(1, 1, 4)
        self.runner.calls.clear()
        with mock.patch.object(at, "REPAIR_VERDICTS_PER_SESSION", 3):
            code, _out = self.turn("worker")
        self.assertEqual(code, 0, self.last_err)
        named = [TA.IDS.findall(c.prompt.split("this session applies")[1].split("\n")[0])
                 for c, _seen in self.runner.calls]
        self.assertIn([skip["id"]], named)
        self.assertEqual(sorted(i for chunk in named for i in chunk),
                         sorted(v["id"] for v in verdicts))
        for chunk in named:
            if skip["id"] not in chunk:
                self.assertLessEqual(len(chunk), 3)
        # rule 1 counts verdicts: the same verdicts in sessions of three
        turn = mock.Mock(rule=1)
        self.assertEqual(at.Turn.sessions(turn, verdicts),
                         [[v["id"] for v in verdicts[i:i + 40]] for i in range(0, len(verdicts), 40)])


class TestTheSkipHunt(SkipTurnCase):
    """A part with no flag and no formal passage still gets a skeptic call when it shows
    an unflagged skip over math no passage holds; rule 1 would make none."""

    PART_BYTES = "1500"

    def setUp(self):
        super().setUp()
        doc = json.loads(self.doc_file.read_text())
        for p in doc["passages"]:
            if p["section"] == ["Gadget bound"]:
                p.update(scope="expository", prominence="supporting")
        self.doc_file.write_text(json.dumps(doc), encoding="utf-8")
        self.commit("the gadget bound scoped expository")

    def test_the_part_that_shows_the_proof_skip_is_hunted(self):
        code, _out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        index = self.auth("r1.render.json")
        part = next(i for i, p in enumerate(index["parts"], 1) if "r1-s003" in p["skips"])
        self.assertEqual(index["parts"][part - 1]["formal"], [])
        skeptics = [c for c in self.runner.steps("skeptic")]
        self.assertIn(part, [c.part for c in skeptics])
        combined = self.auth("r1.skeptic.json")
        self.assertIn("r1-s003", combined["examined_skips"])
        # the parts without a formal passage and with only furniture skips get no call
        for i, p in enumerate(index["parts"], 1):
            if not p["formal"] and not any(r["unheld_outside"] for r in index["skips"]
                                           if r["part"] == i):
                self.assertNotIn(i, [c.part for c in skeptics])


class TestEveryPathEndsUnderRule2(SkipTurnCase, TA.TestEveryPathEnds):
    """The random-outcome property with the skip rule raising F1's proof skip in round 1
    and carrying it after (the fake worker never unskips): rejections stay at most R - 1,
    and every document ends approved."""

    SEEDS = 24

    def test_random_sequences(self):
        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                if seed:
                    self.tearDown_between()
                    self.agents = Agents2()
                    self.runner = TA.InProcess(self.agents)
                got = self.simulate(seed)
                self.assertTrue(got["done"], got)
                self.assertLessEqual(self.rejections(), got["rounds"] - 1, got)
                state = self.state()
                self.assertLessEqual(state["round"], got["rounds"])
                self.assertEqual(state["rule"], 2)
                self.assertEqual(ia.sha256_file(self.doc_file), state["approved"]["sha256"])


# --------------------------------------------------------------------------- #
# rule 1 is the base commit's turn machine
# --------------------------------------------------------------------------- #

def _base_turn_machine(case) -> object | None:
    got = subprocess.run(["git", "-C", str(SKILL), "show", f"{BASE}:scripts/run/audit_turn.py"],
                         capture_output=True, text=True)
    if got.returncode != 0:
        return None
    out = Path(tempfile.mkdtemp(prefix="cf-base-turn-")).resolve()
    case.addCleanup(shutil.rmtree, out, ignore_errors=True)
    (out / "audit_turn_base.py").write_text(got.stdout, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("audit_turn_base", out / "audit_turn_base.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_turn_base"] = module          # the dataclasses look their module up
    case.addCleanup(sys.modules.pop, "audit_turn_base", None)
    spec.loader.exec_module(module)
    return module


class TestRule1IsTheBaseCommitsTurnMachine(TA.TurnCase):
    """One rule-1 run -- a rejection, a revision, a check -- through f523584's turn machine
    and through this one: the same agent calls (step, label, prompt, system prompt), the
    same host tool command lines and the same round files."""

    def scenario(self, module) -> tuple[list, list, dict]:
        self.agents = TA.Agents()
        tools: list = []

        class Recording(TA.InProcess):
            def tool(self_, turn, script, args):
                tools.append((script, list(args)))
                return super().tool(turn, script, args)

        self.runner = Recording(self.agents)
        self.flag_all()

        def turn(role):
            import contextlib
            import io
            out, err = io.StringIO(), io.StringIO()
            argv = [role, "--root", str(self.root), "--prompts", str(self.prompts),
                    "--profile", "examplebox", "--tools", "Read,Glob", "--err",
                    str(self.root / "turn.err")]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = module.main(argv, runner=self.runner, env=self.env, worktree=self.wt)
            self.assertEqual(code, 0, err.getvalue())
            return json.loads(out.getvalue())

        contracts = [turn("reviewer")]
        self.agents.edit = self.restate(0, "a repaired statement")
        contracts.append(turn("worker"))
        contracts.append(turn("reviewer"))
        calls = [(c.role, c.step, c.label, c.prompt.replace(str(self.wt), "<WT>"), c.system,
                  c.part, c.out) for c, _seen in self.runner.calls]
        files = {p.name: p.read_text() for p in
                 sorted((attempt_cap.audit_task_dir(self.root, TASK) / SID).glob("r*.json"))}
        return calls, [(s, a) for s, a in tools], {"contracts": contracts, "files": files}

    def reset(self):
        TA.TestEveryPathEnds.tearDown_between(self)

    def test_the_same_calls_and_files(self):
        base = _base_turn_machine(self)
        if base is None:
            self.skipTest(f"this checkout does not hold commit {BASE}")
        then = self.scenario(base)
        self.reset()
        now = self.scenario(at)
        self.assertEqual(now[0], then[0])
        self.assertEqual(now[1], then[1])
        self.assertEqual(now[2]["contracts"], then[2]["contracts"])
        stamp = re.compile(r'"(at|document_sha256)": "[^"]*"')
        self.assertEqual({k: stamp.sub("", v) for k, v in now[2]["files"].items()},
                         {k: stamp.sub("", v) for k, v in then[2]["files"].items()})
        self.assertTrue(any("--rule" not in a for _s, a in now[1]))
        self.assertFalse(any("--rule" in a for _s, a in now[1]))


if __name__ == "__main__":
    unittest.main()
