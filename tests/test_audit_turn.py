"""The audited inventory's turn machine, dormant without ``CF_AUDIT_RULE``.

New code, for references/locate-documents.md section 13.9: ``scripts/run/audit_turn.py``
(the state file, the staging and restore of ``.tend/audit``, chunked calls through
``agent_call.py --step``, the tamper guard, the revision gate-loop reset, the revert to
the best snapshot, ``audit_failed`` after one uncounted retry, reuse for the same sha),
the one branch of ``scripts/run/bridge-agentbox.sh``, ``agent_call.py --step``,
``attempt_cap.py`` (``count``, and the audited cap that restores the best snapshot),
``watchdog.py``'s two envelope names and ``BRIDGE_HELPERS``.  It holds a property test
over random outcome sequences, the skeptic's calls without the audit present, the revert
to the best snapshot, and a golden test that the unaudited bridge is byte for byte what
it was.  The agents are fakes; the host tools and the gate are the real ones, on the
made-up "Widget theory" layer of ``tests/test_locate_gate_statement_cut.py``.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import importlib.util
import io
import json
import os
import random
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

import test_locate_gate_statement_cut as SC  # noqa: E402
import test_locate_inventory_audit as TL  # noqa: E402
import test_verify_locate as G  # noqa: E402
from cflib import vocab  # noqa: E402
from run import agent_call, attempt_cap, watchdog  # noqa: E402

ia = TL.ia
ir = TL.ir


def _bin(name: str):
    """A bin/ helper by its plain name, as the run host imports it beside the bridge."""
    if name in sys.modules and Path(sys.modules[name].__file__).parent == SCRIPTS / "run":
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "run" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


at = _bin("audit_turn")
vl = sys.modules.get("verify_locate") or importlib.import_module("verify_locate")
SID = TL.SID
TASK = TL.TASK
BRIDGE = SCRIPTS / "run" / "bridge-agentbox.sh"
SUCCESS = {"type": "result", "subtype": "success", "is_error": False, "result": "done",
           "total_cost_usd": 0.25, "duration_ms": 1000}
PROMPTS = {"worker-system.md": "worker system for $TEND_TASK_ID\n",
           "worker.md": "inventory task $TEND_TASK_ID in $TEND_WORKTREE_PATH\n",
           "worker-revision.md": "AUDIT REVISION $CF_AUDIT_ROUND of $CF_AUDIT_ROUNDS for "
                                 "$CF_AUDIT_DOCUMENT\n",
           "reviewer-system.md": "audit system, round $CF_AUDIT_ROUND\n",
           "reviewer.md": "AUDITOR of $CF_AUDIT_SOURCE\n",
           "reviewer-check.md": "CHECKER round $CF_AUDIT_ROUND\n",
           "reviewer-skeptic.md": "SKEPTIC round $CF_AUDIT_ROUND\n"}
IDS = re.compile(r"r[0-9]+-v[0-9]{3,}")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          check=True).stdout.strip()


# --------------------------------------------------------------------------- #
# the fakes: the agents, and the host tools run in this process
# --------------------------------------------------------------------------- #

class Agents:
    """Fake agents.  Each writes the file its call names, from what was staged for it;
    the policy fields say what it judges, and ``script`` what a call returns."""

    def __init__(self, layer: str = SC.LAYER):
        self.layer = layer
        self.flag: dict[str, dict] = {}       # round 1: item -> audit row fields
        self.uphold = True
        self.statuses: dict[str, str] = {}    # verdict id -> check status
        self.default_status = "missing"
        self.harmful = False
        self.invalid: dict[str, int] = {}     # output name -> invalid writes before a valid one
        self.exits: dict[str, list[int]] = {} # output or label -> exit codes to return first
        self.edit = None                      # the worker's change: callable(turn, call)
        self.wrote_nothing: set[str] = set()

    def evidence(self, turn, item: str) -> str:
        doc = json.loads(turn.doc_path().read_text(encoding="utf-8"))
        for p in doc["passages"]:
            if p["item"] == item:
                return " ".join(self.layer[p["span"][0]:p["span"][1]].split()[:4])
        return "not on the layer at all"

    def __call__(self, turn, call) -> int:
        key = call.out or call.label
        queue = self.exits.get(key) or []
        if queue:
            return queue.pop(0)
        call.raw.write_text(json.dumps(SUCCESS), encoding="utf-8")
        if call.role == "worker":
            return self.worker(turn, call)
        if call.out in self.wrote_nothing:
            return 0
        data = getattr(self, call.step)(turn, call)
        if self.invalid.get(call.out):
            self.invalid[call.out] -= 1
            data = dict(data, surplus="a key the schema does not take")
        (turn.mirror / call.out).write_text(json.dumps(data), encoding="utf-8")
        return 0

    def head(self, kind: str, k: int) -> dict:
        return {"schema": ia.AUDIT_SCHEMA, "kind": kind, "round": k, "source_id": SID}

    def audit(self, turn, call) -> dict:
        index = json.loads((turn.mirror / "r1.render.json").read_text())
        part = index["parts"][call.part - 1]
        rows = []
        for item in part["formal"]:
            row = {"item": item, "origin": "lead", "verdict": "clean", "rule": 0,
                   "merge_with": "", "reason": "", "action": ""}
            row.update(self.flag.get(item, {}))
            rows.append(row)
        return {**self.head("audit", 1), "passages": rows, "expository_flags": [],
                "per_spec_formal_estimate": len(rows), "patterns": [],
                "subject_check": "one subject"}

    def skeptic(self, turn, call) -> dict:
        k, i = call.round, call.part
        flags = json.loads((turn.mirror / f"r{k}.flags.part{i}.json").read_text())
        index = json.loads((turn.mirror / f"r{k}.render.json").read_text())
        flagged = {f["item"] for f in flags["flags"]}
        verdicts = [{"flag_id": f["flag_id"], "upheld": self.uphold, "verdict": f["verdict"],
                     "rule": f["rule"] if self.uphold else 0,
                     "evidence": self.evidence(turn, f["item"]) if self.uphold else "",
                     "reason": "the spec says so"} for f in flags["flags"]]
        return {**self.head("skeptic", k), "verdicts": verdicts, "missed": [],
                "disputes": [{"verdict_id": d["verdict_id"], "worker_right": False,
                              "reason": "the spec as written"} for d in flags["disputes"]],
                "examined": [x for x in index["parts"][i - 1]["formal"] if x not in flagged],
                "per_spec_formal_estimate": 1, "summary": f"part {i} judged"}

    def check(self, turn, call) -> dict:
        k, i = call.round, call.part
        expect = json.loads((turn.mirror / f"r{k}.check.part{i}.expect.json").read_text())
        checked = []
        for vid in (x for x in expect if "-v" in x):
            status = self.statuses.get(vid, self.default_status)
            checked.append({"verdict_id": vid, "status": status,
                            "reason": "" if status == "applied" else "not as asked"})
        return {**self.head("check", k), "checked": checked,
                "collateral": [{"id": x, "harmful": self.harmful, "reason": "no verdict"}
                               for x in expect if "-x" in x],
                "new": [], "regressions": []}

    def worker(self, turn, call) -> int:
        result = {"status": "completed", "summary": "done"}
        (turn.worktree / ".tend").mkdir(exist_ok=True)
        if call.step == "revision" and turn.mirror.is_dir():
            verdicts = sorted(turn.mirror.glob("r*.verdicts.json"))
            if verdicts:
                k = json.loads(verdicts[-1].read_text())["round"]
                line = next((ln for ln in call.prompt.splitlines()
                             if ln.startswith("- The verdicts:")), "")
                ids = IDS.findall(line.split(";", 1)[-1]) if line else []
                path = turn.mirror / f"r{k}.repair.json"
                repair = json.loads(path.read_text()) if path.is_file() else \
                    {**self.head("repair", k), "entries": []}
                repair["entries"] += [{"verdict_id": v, "status": "not_applied",
                                       "why": "left for the checker"} for v in ids]
                path.write_text(json.dumps(repair), encoding="utf-8")
                ops = turn.mirror / f"r{k}.ops.json"
                if not ops.is_file():
                    ops.write_text(json.dumps({**self.head("ops", k), "ops": []}))
        if self.edit is not None:
            self.edit(turn, call)
        turn.result_path.write_text(json.dumps(result), encoding="utf-8")
        return 0


class InProcess(at.Runner):
    """The real host tools and gate, called in this process (python3 -I's semantics: the
    working directory is the worktree); the agents are :class:`Agents`."""

    MODULES = {"inventory_audit.py": ia, "inventory_render.py": ir}

    def __init__(self, agents: Agents):
        self.agents = agents
        self.calls: list[tuple] = []          # (Call, the mirror's files when it ran)

    def agent(self, turn, call) -> int:
        seen = sorted(p.name for p in turn.mirror.iterdir()) if turn.mirror.is_dir() else []
        self.calls.append((call, seen))
        return self.agents(turn, call)

    def _main(self, turn, fn, args) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(turn.worktree)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = fn(args)
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 2
        finally:
            os.chdir(cwd)
        return int(code or 0), out.getvalue(), err.getvalue()

    def tool(self, turn, script, args):
        return self._main(turn, self.MODULES[script].main, args)

    def gate(self, turn):
        return self._main(turn, vl.main, [*turn.gate_cmd[2:], "--findings", turn.doc_rel])

    def steps(self, step: str | None = None) -> list:
        return [c for c, _seen in self.calls if step is None or c.step == step]


class TurnCase(TL.HostCase):
    """A tend root (the fixture's root: gate/ beside prompts/, audit/, attempts/) and a
    task worktree holding the committed good cut, driven turn by turn."""

    ROUNDS = 3
    PART_BYTES = ""

    def setUp(self):
        super().setUp()
        self.root = self.fx.root
        self.prompts = self.root / "prompts"
        self.prompts.mkdir()
        for name, text in PROMPTS.items():
            (self.prompts / name).write_text(text, encoding="utf-8")
        self.wt = Path(tempfile.mkdtemp(prefix="cf-wt-")).resolve()
        self.addCleanup(shutil.rmtree, self.wt, ignore_errors=True)
        git(self.wt, "init", "-q", "-b", "main")
        git(self.wt, "config", "user.email", "t@example.invalid")
        git(self.wt, "config", "user.name", "t")
        (self.wt / ".git" / "info" / "exclude").write_text(".tend/\n", encoding="utf-8")
        (self.wt / "tasks").mkdir()
        (self.wt / "tasks" / f"{TASK}.yaml").write_text(f"id: {TASK}\nstatus: open\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-qm", "tasks")
        git(self.wt, "checkout", "-q", "-b", TASK)
        self.doc_file = self.wt / "findings" / TASK / f"{SID}.json"
        self.doc_file.parent.mkdir(parents=True)
        shutil.copyfile(self.doc_path, self.doc_file)
        (self.wt / "tasks" / f"{TASK}.yaml").write_text(f"id: {TASK}\nstatus: complete\n")
        self.commit("the inventory")
        self.agents = Agents()
        self.runner = InProcess(self.agents)
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TEND_TASK_ID": TASK,
                    "TEND_WORKTREE_ID": "wt-1", "CF_AUDIT_RULE": "1",
                    "CF_AUDIT_ROUNDS": str(self.ROUNDS),
                    "CF_GATE_CMD": f"python3 {self.gate_dir}/verify_locate.py --gate-config "
                                   f"{self.gate_dir}/gate.json",
                    "CF_TASK_MANIFESTS": str(self.fx.tasks), "CF_ATTEMPT_CAP": "3",
                    "CF_AUDIT_PART_BYTES": self.PART_BYTES}

    def commit(self, message: str) -> None:
        git(self.wt, "add", "-A", "--", "findings", "tasks")
        git(self.wt, "commit", "-qm", message, "--allow-empty")

    def turn(self, role: str) -> tuple[int, dict | None]:
        out, err = io.StringIO(), io.StringIO()
        argv = [role, "--root", str(self.root), "--prompts", str(self.prompts), "--profile",
                "examplebox", "--tools", "Read,Glob", "--err", str(self.root / "turn.err")]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = at.main(argv, runner=self.runner, env=self.env, worktree=self.wt)
        text = out.getvalue().strip()
        self.last_err = err.getvalue()
        return code, (json.loads(text) if text else None)

    def state(self) -> dict:
        return json.loads(attempt_cap.audit_state_path(self.root, TASK).read_text())

    def auth(self, name: str) -> dict:
        return json.loads((attempt_cap.audit_task_dir(self.root, TASK) / SID / name).read_text())

    def items(self) -> list[str]:
        return TL.formal(json.loads(self.doc_file.read_text()))

    def flag_all(self, verdict: str = "wrong_prominence") -> None:
        for item in self.items():
            self.agents.flag[item] = {"verdict": verdict, "rule": 9, "reason": "too prominent",
                                      "action": "set prominence supporting"}

    def restate(self, which: int, text: str):
        """A worker edit: one formal passage's statement, committed (gate-clean)."""
        def edit(turn, call):
            doc = json.loads(self.doc_file.read_text())
            formal = [p for p in doc["passages"] if p["scope"] == "formal_math"]
            formal[which]["statement"] = text
            self.doc_file.write_text(json.dumps(doc), encoding="utf-8")
            self.commit(f"repair: {text}")
        return edit

    def rejections(self) -> int:
        return attempt_cap.read_count(self.root, TASK)


# --------------------------------------------------------------------------- #
# one definition of every value
# --------------------------------------------------------------------------- #

class TestTheHostCopiesAreCflibs(unittest.TestCase):

    PAIRS = {"TURN_SCHEMA": "AUDIT_TURN_SCHEMA", "AUDIT_RULES": "INVENTORY_AUDIT_RULES",
             "SKIP_VERDICT": "AUDIT_SKIP_VERDICT", "GAP_VERDICT": "AUDIT_GAP_VERDICT",
             "AUDIT_REVIEW_ROUNDS": "AUDIT_REVIEW_ROUNDS", "AUDIT_STEPS": "AUDIT_STEPS",
             "OUTPUT_RETRIES": "AUDIT_OUTPUT_RETRIES", "GATE_RESETS": "AUDIT_GATE_RESETS",
             "DISCUSSION_TAIL": "AUDIT_DISCUSSION_TAIL",
             "REVERT_ATTEMPTS": "AUDIT_REVERT_ATTEMPTS",
             "REPAIR_VERDICTS_PER_SESSION": "AUDIT_REPAIR_VERDICTS_PER_SESSION",
             "SUMMARY_WORDS": "AUDIT_SUMMARY_WORDS"}

    def test_the_turn_machines_copy(self):
        for host, ours in self.PAIRS.items():
            with self.subTest(host=host):
                self.assertEqual(getattr(at, host), getattr(vocab, ours))

    def test_the_steps_agent_call_and_the_watchdog_know(self):
        self.assertEqual(agent_call.AUDIT_STEPS, vocab.AUDIT_STEPS)
        self.assertEqual(watchdog.AUDIT_STEPS, vocab.AUDIT_STEPS)

    def test_the_bridges_tail_is_the_revisions(self):
        self.assertIn(f"tail -c {vocab.AUDIT_DISCUSSION_TAIL} ", BRIDGE.read_text())

    def test_the_best_snapshot_is_the_merges_rule(self):
        rng = random.Random(7)
        for _ in range(200):
            rounds = [{"round": r, "actionable": rng.randrange(4), "harm": r > 1 and rng.random() < .4,
                       "document_sha256": f"{r:064x}"} for r in range(1, rng.randrange(2, 4) + 1)]
            turn = mock.Mock(rel=lambda p: str(p), auth=Path("a"))
            got = at.Turn.best(turn, {"rounds": rounds})
            self.assertEqual(got["round"], ia.best_round(rounds), rounds)

    def test_every_new_value_is_documented(self):
        vocabulary = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")
        documents = (SKILL / "references" / "locate-documents.md").read_text(encoding="utf-8")
        for name in ("AUDIT_TURN_SCHEMA", "AUDIT_STEPS", "AUDIT_OUTPUT_RETRIES",
                     "AUDIT_GATE_RESETS", "AUDIT_DISCUSSION_TAIL", "AUDIT_REVERT_ATTEMPTS"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", vocabulary)
        for value in (vocab.AUDIT_TURN_SCHEMA, *vocab.AUDIT_STEPS):
            with self.subTest(value=value):
                self.assertIn(f"`{value}`", vocabulary)
                self.assertIn(f"`{value}`", documents)


class TestItRunsOnTheHost(unittest.TestCase):

    def test_it_imports_the_standard_library_and_its_siblings_only(self):
        import ast
        tree = ast.parse((SCRIPTS / "run" / "audit_turn.py").read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for a in node.names}
        names |= {node.module.split(".")[0] for node in ast.walk(tree)
                  if isinstance(node, ast.ImportFrom) and node.module}
        siblings = {"attempt_cap", "normalize_result"}
        self.assertTrue(names - siblings <= set(sys.stdlib_module_names) | {"__future__"},
                        names - siblings - set(sys.stdlib_module_names))
        self.assertNotIn("cflib", names)

    def test_it_answers_help_under_python_dash_I(self):
        proc = subprocess.run([sys.executable, "-I", str(SCRIPTS / "run" / "audit_turn.py"),
                               "--help"], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin"}, cwd=tempfile.gettempdir())
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_it_is_staged_beside_the_bridge_in_every_locate_root(self):
        from plan.make_tend_root import BRIDGE_HELPERS
        from run import manifest
        self.assertIn("audit_turn.py", BRIDGE_HELPERS)
        self.assertIn("audit_turn.py", manifest.BRIDGE_FILES)


# --------------------------------------------------------------------------- #
# the reviewer's round 1
# --------------------------------------------------------------------------- #

class TestRoundOne(TurnCase):

    def test_a_clean_audit_approves_and_records_nothing(self):
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        self.assertEqual([c.step for c in self.runner.steps()], ["audit", "skeptic"])
        state = self.state()
        self.assertEqual(state["approved"]["round"], 1)
        self.assertEqual(state["round"], 1)
        self.assertEqual(self.rejections(), 0)
        verdicts = self.auth("r1.verdicts.json")
        self.assertEqual(verdicts["decision"], "approve")
        self.assertEqual(self.auth("r1.document.json"), json.loads(self.doc_file.read_text()))

    def test_a_rejection_advances_the_round_and_is_recorded_once(self):
        self.flag_all()
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "request_changes")
        self.assertIn(".tend/audit/r1.verdicts.json", out["feedback_text"])
        state = self.state()
        self.assertEqual(state["round"], 2)
        self.assertEqual(state["revision"]["round"], 1)
        self.assertEqual(self.rejections(), 1)
        # tend asks again before any worker turn: the same contract, not a second record
        before = len(self.runner.calls)
        code, again = self.turn("reviewer")
        self.assertEqual(again, out)
        self.assertEqual(len(self.runner.calls), before)
        self.assertEqual(self.rejections(), 1)

    def test_the_skeptic_runs_without_the_audit_present(self):
        self.flag_all()
        self.turn("reviewer")
        for call, seen in self.runner.calls:
            with self.subTest(step=call.step, part=call.part):
                if call.step == "skeptic":
                    self.assertFalse([n for n in seen if ".audit." in n], seen)
                    self.assertIn(f"r1.flags.part{call.part}.json", seen)
                    self.assertNotIn("r1.flags.json", seen)
                    self.assertNotIn("AUDITOR", call.prompt)
                if call.step == "audit":
                    self.assertFalse([n for n in seen if "skeptic" in n or "flags" in n], seen)
                    self.assertIn("AUDITOR of " + SID, call.prompt)
        # the authoritative copies are outside the worktree, all of them
        names = {p.name for p in (attempt_cap.audit_task_dir(self.root, TASK) / SID).iterdir()}
        self.assertTrue({"r1.audit.part1.json", "r1.flags.json", "r1.skeptic.json",
                         "r1.verdicts.json", "r1.document.json"} <= names, names)

    def test_an_unaudited_document_is_approved_after_the_gate(self):
        doc = json.loads(self.doc_file.read_text())
        doc.update(passages=[], unresolved={"reason": "no theorem here", "by": "worker"})
        self.doc_file.write_text(json.dumps(doc))
        self.commit("unresolved")
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.state()["counts"]["unaudited"], 1)

    def test_a_precheck_refusal_is_not_recorded_and_keeps_the_round(self):
        doc = json.loads(self.doc_file.read_text())
        doc["run_id"] = "another-run"
        self.doc_file.write_text(json.dumps(doc))
        self.commit("broken")
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "request_changes")
        self.assertIn("precheck", out["feedback_text"])
        self.assertEqual(self.rejections(), 0)
        self.assertEqual(self.state()["round"], 1)
        self.assertEqual(self.state()["last"]["event"], "precheck")
        self.assertEqual(self.runner.calls, [])


class TestChunks(TurnCase):
    """Small parts: one validated call per part, the skeptic's per part, combined."""

    PART_BYTES = "1000"

    def test_one_call_per_part_each_validated_against_its_part(self):
        self.flag_all()
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        index = self.auth("r1.render.json")
        self.assertGreater(len(index["parts"]), 2)
        audits = self.runner.steps("audit")
        self.assertEqual([c.part for c in audits], list(range(1, len(index["parts"]) + 1)))
        for call in audits:
            with self.subTest(part=call.part):
                self.assertIn(f"part {call.part} of {len(index['parts'])}", call.prompt)
                self.assertIn(f"--part {call.part}", call.prompt)
                self.assertIn(f".tend/audit/r1.audit.part{call.part}.json", call.prompt)
        skeptic = self.auth("r1.skeptic.json")
        flags = self.auth("r1.flags.json")
        self.assertEqual(sorted(v["flag_id"] for v in skeptic["verdicts"]),
                         sorted(f["flag_id"] for f in flags["flags"]))
        self.assertLessEqual(len(skeptic["summary"].split()), vocab.AUDIT_SUMMARY_WORDS)
        self.assertEqual(out["verdict"], "request_changes")

    def test_a_skeptic_part_is_held_to_its_own_hunt(self):
        doc = json.loads(self.doc_file.read_text())
        self.turn("reviewer")
        index = self.auth("r1.render.json")
        flags = {"schema": ia.AUDIT_SCHEMA, "kind": "flags", "round": 1, "source_id": SID,
                 "flags": [], "disputes": []}
        part = index["parts"][0]
        skeptic = {"schema": ia.AUDIT_SCHEMA, "kind": "skeptic", "round": 1, "source_id": SID,
                   "verdicts": [], "missed": [], "disputes": [], "examined": [],
                   "per_spec_formal_estimate": 1, "summary": "s"}
        problems = ia.validate_skeptic(skeptic, doc, flags, round_=1, expect=part["formal"])
        self.assertTrue(any("judges at least" in p for p in problems), problems)
        other = [i for i in TL.formal(doc) if i not in part["formal"]][:1]
        self.assertTrue(other)
        problems = ia.validate_skeptic(dict(skeptic, examined=part["formal"] + other), doc,
                                       flags, round_=1, expect=part["formal"])
        self.assertTrue(any("no formal passage of this part" in p for p in problems), problems)
        self.assertEqual(ia.validate_skeptic(dict(skeptic, examined=part["formal"]), doc,
                                             flags, round_=1, expect=part["formal"]), [])


class TestRetriesAndReuse(TurnCase):

    def test_an_invalid_output_is_re_called_once_with_the_validators_lines(self):
        self.agents.invalid["r1.audit.part1.json"] = 1
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        audits = self.runner.steps("audit")
        self.assertEqual(len(audits), 2)
        self.assertIn("was invalid", audits[1].prompt)
        self.assertIn("surplus", audits[1].prompt)
        self.assertEqual(out["verdict"], "approve")
        self.assertFalse(self.auth("r1.verdicts.json")["audit_failed"])
        self.assertEqual(self.state()["counts"]["retries"], 1)

    def test_still_invalid_after_the_retry_is_audit_failed_approved_and_counted(self):
        self.flag_all()
        self.agents.invalid["r1.audit.part1.json"] = 2
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(len(self.runner.steps("audit")), 2)
        self.assertEqual(self.runner.steps("skeptic"), [])
        self.assertEqual(out["verdict"], "approve")
        verdicts = self.auth("r1.verdicts.json")
        self.assertTrue(verdicts["audit_failed"])
        self.assertEqual(self.state()["counts"]["audit_failed"], 1)
        self.assertEqual(self.rejections(), 0)

    def test_no_output_at_all_is_invalid_too(self):
        self.agents.wrote_nothing.add("r1.audit.part1.json")
        code, out = self.turn("reviewer")
        self.assertEqual(out["verdict"], "approve")
        self.assertTrue(self.auth("r1.verdicts.json")["audit_failed"])

    def test_an_infrastructure_failure_is_retried_by_tend_and_the_audit_reused(self):
        self.flag_all()
        self.agents.exits["r1.skeptic.part1.json"] = [124]
        code, out = self.turn("reviewer")
        self.assertEqual(code, 124)
        self.assertIsNone(out)
        self.assertEqual(len(self.runner.steps("audit")), 1)
        # tend retries the turn: the audit is not paid for twice
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(len(self.runner.steps("audit")), 1)
        self.assertEqual(len(self.runner.steps("skeptic")), 2)
        self.assertEqual(out["verdict"], "request_changes")
        self.assertEqual(self.rejections(), 1)

    def test_a_document_that_changed_under_a_round_is_audited_afresh(self):
        self.agents.exits["r1.skeptic.part1.json"] = [75]
        self.turn("reviewer")
        self.restate(0, "a changed statement")(None, None)
        self.turn("reviewer")
        self.assertEqual(len(self.runner.steps("audit")), 2)
        discarded = attempt_cap.audit_task_dir(self.root, TASK) / SID / "discarded"
        self.assertTrue(any(discarded.iterdir()))


class TestTheTamperGuard(TurnCase):

    def test_a_reviewer_that_edits_or_leaves_a_file_is_undone(self):
        real = self.agents.audit

        def tamper(turn, call):
            doc = json.loads(self.doc_file.read_text())
            doc["passages"][0]["statement"] = "tampered"
            self.doc_file.write_text(json.dumps(doc))
            (turn.worktree / "findings" / "x.json").write_text("{}")
            return real(turn, call)

        self.agents.audit = tamper
        sha = TL.ia.sha256_file(self.doc_file)
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(TL.ia.sha256_file(self.doc_file), sha)
        self.assertFalse((self.wt / "findings" / "x.json").exists())
        self.assertEqual(self.state()["counts"]["reviewer_tampered"], 1)
        self.assertEqual(git(self.wt, "status", "--porcelain"), "")


# --------------------------------------------------------------------------- #
# the worker's revisions
# --------------------------------------------------------------------------- #

class TestTheRevision(TurnCase):

    def setUp(self):
        super().setUp()
        self.flag_all()
        self.turn("reviewer")
        self.runner.calls.clear()

    def test_the_revision_prompt_names_the_round_and_its_verdicts(self):
        (self.wt / ".tend" / "discussion.md").write_text("reviewer: Audit round 1: see files\n")
        code, out = self.turn("worker")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["status"], "completed")
        (call, seen), = self.runner.calls
        self.assertEqual(call.step, "revision")
        self.assertIn("AUDIT REVISION 1 of 3 for " + f"findings/{TASK}/{SID}.json", call.prompt)
        self.assertNotIn("a reviewer rejected", call.prompt)
        self.assertIn("r1.verdicts.json", seen)
        self.assertIn("r1.document.json", seen)
        self.assertIn("reviewer: Audit round 1", call.prompt)
        self.assertEqual(sorted(IDS.findall(call.prompt.split("this session applies")[1]
                                            .split("\n")[0])),
                         [v["id"] for v in self.auth("r1.verdicts.json")["verdicts"]])
        # the worker's files are kept outside the worktree
        self.assertEqual(len(self.auth("r1.repair.json")["entries"]),
                         len(self.auth("r1.verdicts.json")["verdicts"]))

    def test_sessions_of_at_most_the_cap_each_named_its_verdicts(self):
        with mock.patch.object(at, "REPAIR_VERDICTS_PER_SESSION", 2):
            code, _out = self.turn("worker")
        self.assertEqual(code, 0, self.last_err)
        ids = [v["id"] for v in self.auth("r1.verdicts.json")["verdicts"]]
        sessions = [c for c, _ in self.runner.calls]
        self.assertEqual(len(sessions), -(-len(ids) // 2))
        named = [IDS.findall(c.prompt.split("this session applies")[1].split("\n")[0])
                 for c in sessions]
        self.assertEqual([i for chunk in named for i in chunk], ids)
        self.assertTrue(all(len(chunk) <= 2 for chunk in named))
        self.assertEqual(sorted(e["verdict_id"] for e in self.auth("r1.repair.json")["entries"]),
                         sorted(ids))

    def test_an_interrupted_revision_resumes_at_its_next_session(self):
        with mock.patch.object(at, "REPAIR_VERDICTS_PER_SESSION", 2):
            self.agents.exits["r1.revision.s2"] = [75]
            code, _ = self.turn("worker")
            self.assertEqual(code, 75)
            code, _ = self.turn("worker")
        self.assertEqual(code, 0, self.last_err)
        labels = [c.label for c, _ in self.runner.calls]
        self.assertEqual(labels[0], "r1.revision.s1")
        self.assertEqual(labels.count("r1.revision.s1"), 1)

    def test_a_wiped_tend_directory_loses_nothing(self):
        self.agents.edit = self.restate(0, "a repaired statement")
        self.turn("worker")
        shutil.rmtree(self.wt / ".tend")
        self.runner.calls.clear()
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        (check, seen) = next((c, s) for c, s in self.runner.calls if c.step == "check")
        self.assertTrue({"r1.verdicts.json", "r1.repair.json", "r1.document.json",
                         "r2.collateral.json"} <= set(seen), seen)

    def test_a_refused_revision_is_re_run_on_the_gate_and_reset_at_the_third(self):
        self.agents.edit = self.restate(0, "an edit the gate refuses, as the fake says")
        reviewed = self.state()["rounds"]["1"]["head"]
        self.turn("worker")
        (self.wt / ".tend" / "discussion.md").write_text("gate: rule 10 refuses the cut\n")
        for n in range(1, at.GATE_RESETS):
            self.runner.calls.clear()
            code, out = self.turn("worker")        # tend's pre-review gate refused it
            self.assertEqual(code, 0, self.last_err)
            (call, _seen), = self.runner.calls
            self.assertIn("the validation gate refused your last commit", call.prompt)
            self.assertIn("gate: rule 10 refuses", call.prompt)
            self.assertNotIn("this session applies", call.prompt)
        self.runner.calls.clear()
        code, out = self.turn("worker")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(self.runner.calls, [], "the reset called an agent")
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), reviewed)
        self.assertIn("reset", out["summary"])
        self.assertEqual(self.state()["counts"]["gate_resets"], 1)
        # the next review finds no progress and approves it mechanically: no check is
        # paid for, and the reset's stale ops file is never re-applied as collateral
        self.runner.calls.clear()
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        self.assertIn("no progress since round 1", out["notes"])
        self.assertEqual(self.runner.calls, [])
        self.assertFalse((attempt_cap.audit_task_dir(self.root, TASK) / SID
                          / "r2.collateral.json").exists())
        self.assertEqual(self.state()["counts"]["no_progress"], 1)
        self.assertEqual(self.rejections(), 1)

    def test_a_revision_that_changed_nothing_is_no_progress(self):
        """A stale ops file beside an unchanged document is not judged: the reviewer
        approves round 1's bytes with no agent call."""
        self.turn("worker")
        ops = attempt_cap.audit_task_dir(self.root, TASK) / SID / "r1.ops.json"
        items = self.items()
        ops.write_text(json.dumps({**self.agents.head("ops", 1), "ops": [
            {"op": "merge", "keep": items[0], "drop": items[1],
             "verdict": self.auth("r1.verdicts.json")["verdicts"][0]["id"]}]}))
        self.runner.calls.clear()
        code, out = self.turn("reviewer")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        self.assertEqual(self.runner.calls, [])
        state = self.state()
        self.assertEqual(state["approved"], {"round": 1, "sha256": state["rounds"]["1"]
                                             ["document_sha256"],
                                             "why": "no progress since round 1"})
        self.assertIsNone(state["revision"])
        # asked again, it answers as it did
        code, again = self.turn("reviewer")
        self.assertEqual(again["verdict"], "approve")
        self.assertEqual(self.state()["counts"]["no_progress"], 1)

    def failing_tool(self, script: str, word: str | None):
        """Round 2's reviewer turn with ``script`` (its ``word`` subcommand) exiting 1 as a
        traceback does."""
        self.agents.edit = self.restate(0, "a repair")
        self.turn("worker")
        real = self.runner.tool

        def tool(turn, name, args):
            if name == script and (word is None or word in args):
                return 1, "", "Traceback: ValueError: list.remove(x): x not in list"
            return real(turn, name, args)
        with mock.patch.object(self.runner, "tool", tool):
            return self.turn("reviewer")

    def assertAuditFailed(self, code: int, out: dict | None) -> None:
        """A host tool that fails on its input ends the round as audit_failed (approve,
        counted), so a deterministic failure cannot rerun the reviewer turn on exit 1
        for ever."""
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        state = self.state()
        self.assertIn("failed (exit 1)", state["approved"]["why"])
        self.assertEqual(state["counts"]["audit_failed"], 1)

    def test_a_collateral_that_fails_is_audit_failed_not_a_loop(self):
        self.assertAuditFailed(*self.failing_tool("inventory_audit.py", "collateral"))

    def test_a_rendering_that_fails_is_audit_failed_not_a_loop(self):
        self.assertAuditFailed(*self.failing_tool("inventory_render.py", None))


class TestTheFirstWorkerTurn(TurnCase):

    def test_the_first_call_is_the_worker_prompt_alone_and_no_step(self):
        code, out = self.turn("worker")
        self.assertEqual(code, 0, self.last_err)
        (call, _), = self.runner.calls
        self.assertIsNone(call.step)
        self.assertEqual(call.prompt, f"inventory task {TASK} in {self.wt}")
        self.assertEqual(call.system, f"worker system for {TASK}")

    def test_a_gate_refusal_before_any_review_names_the_gate_not_a_reviewer(self):
        self.turn("worker")
        (self.wt / ".tend" / "discussion.md").write_text("gate: refused rule 2\n")
        self.runner.calls.clear()
        self.turn("worker")
        (call, _), = self.runner.calls
        self.assertIn("the validation gate refused your last commit", call.prompt)
        self.assertNotIn("a reviewer rejected", call.prompt)
        self.assertIn("gate: refused rule 2", call.prompt)


# --------------------------------------------------------------------------- #
# the later rounds: revert to the best snapshot
# --------------------------------------------------------------------------- #

class TestRevertToTheBest(TurnCase):

    def test_a_harmless_middle_round_below_t_approves_as_it_is(self):
        self.flag_all()
        code, out = self.turn("reviewer")                  # R1: the five formal passages
        self.assertEqual(out["verdict"], "request_changes")
        first = self.auth("r1.verdicts.json")
        self.assertEqual(len(first["verdicts"]), 5)
        self.agents.edit = self.restate(0, "the first repair")
        self.turn("worker")
        self.agents.statuses = {first["verdicts"][0]["id"]: "applied"}
        code, out = self.turn("reviewer")                  # R2: 4 actionable, below T = 5
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(out["verdict"], "approve")
        self.assertEqual(self.state()["approved"]["round"], 2)
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.rejections(), 1)

    def test_a_wrong_application_in_the_final_round_restores_the_best_bytes(self):
        with mock.patch.object(ia, "AUDIT_THRESHOLD_MIN", 1):
            self.flag_all()
            self.turn("reviewer")                          # R1: 5 actionable
            r1 = self.doc_file.read_bytes()
            self.agents.edit = self.restate(0, "the first repair")
            self.turn("worker")                            # W2
            ids1 = [v["id"] for v in self.auth("r1.verdicts.json")["verdicts"]]
            self.agents.statuses = {ids1[0]: "applied", ids1[1]: "applied"}
            code, out = self.turn("reviewer")              # R2: 3 actionable, progress
            self.assertEqual(out["verdict"], "request_changes", self.last_err)
            r2 = self.doc_file.read_bytes()
            self.assertNotEqual(r1, r2)
            self.agents.edit = self.restate(1, "the second repair, wrongly")
            self.turn("worker")                            # W3
            ids2 = [v["id"] for v in self.auth("r2.verdicts.json")["verdicts"]]
            self.agents.statuses = {ids2[0]: "wrong"}
            self.runner.calls.clear()
            code, out = self.turn("reviewer")              # R3, final: harm -> revert
            self.assertEqual(code, 0, self.last_err)
            self.assertEqual(out["verdict"], "request_changes")
            self.assertIn("revert to the round 2 snapshot", out["feedback_text"])
            self.assertEqual(self.rejections(), 2, "a revert is not a genuine rejection")
            state = self.state()
            self.assertEqual(state["pending"]["to"], 2)
            self.assertEqual(state["best"]["round"], 2)
            # the worker turn restores round 2's bytes and commits, with no agent call
            self.runner.calls.clear()
            code, out = self.turn("worker")
            self.assertEqual(code, 0, self.last_err)
            self.assertEqual(self.runner.calls, [])
            self.assertEqual(self.doc_file.read_bytes(), r2)
            self.assertIn("revert to round 2", git(self.wt, "log", "-1", "--format=%s"))
            self.assertEqual(git(self.wt, "status", "--porcelain"), "")
            # and the reviewer approves it mechanically when the sha matches
            code, out = self.turn("reviewer")
            self.assertEqual(code, 0, self.last_err)
            self.assertEqual(out["verdict"], "approve")
            self.assertIn("reverted to round 2", out["notes"])
            self.assertEqual(self.runner.calls, [])
            self.assertEqual(self.rejections(), 2)

    def test_a_middle_rounds_revert_verdict_reaches_the_revision_with_its_collateral(self):
        with mock.patch.object(ia, "AUDIT_THRESHOLD_MIN", 1):
            self.flag_all()
            self.turn("reviewer")                          # R1: 5 actionable
            self.agents.edit = self.restate(0, "the first repair, by hand")
            self.turn("worker")
            ids1 = [v["id"] for v in self.auth("r1.verdicts.json")["verdicts"]]
            self.agents.statuses = {ids1[0]: "applied", ids1[1]: "applied"}
            self.agents.harmful = True
            code, out = self.turn("reviewer")              # R2: progress, and harm
            self.assertEqual(out["verdict"], "request_changes", self.last_err)
        reverts = [v for v in self.auth("r2.verdicts.json")["verdicts"]
                   if v["verdict"] == ia.VERDICT_REVERT]
        self.assertTrue(reverts)
        self.assertTrue(all(v["rule"] == 0 and v["origin"] == "collateral" for v in reverts))
        self.agents.edit = None
        self.runner.calls.clear()
        code, _out = self.turn("worker")
        self.assertEqual(code, 0, self.last_err)
        (call, seen), = self.runner.calls
        self.assertIn("r2.collateral.json", seen)
        self.assertIn("The collateral a `revert` verdict names: .tend/audit/r2.collateral.json",
                      call.prompt)

    def test_a_restore_the_reviewer_does_not_find_is_asked_for_again(self):
        with mock.patch.object(ia, "AUDIT_THRESHOLD_MIN", 1):
            self.flag_all()
            self.turn("reviewer")
            self.agents.edit = self.restate(0, "a repair")
            self.turn("worker")
            self.agents.default_status = "wrong"
            code, out = self.turn("reviewer")
        self.assertIn("revert", out["feedback_text"])
        # a worker turn that did not happen: the reviewer asks again, unrecorded
        code, out = self.turn("reviewer")
        self.assertEqual(out["verdict"], "request_changes")
        self.assertEqual(self.state()["pending"]["attempts"], 1)
        self.assertEqual(self.rejections(), 1)
        for n in range(2, at.REVERT_ATTEMPTS + 1):
            code, out = self.turn("reviewer")
            self.assertEqual((code, out["verdict"]), (0, "request_changes"))
            self.assertEqual(self.state()["pending"]["attempts"], n)
        # past the limit the turn stops, exit 1, never a verdict
        code, out = self.turn("reviewer")
        self.assertEqual(code, at.EXIT_FAILED)
        self.assertIsNone(out)
        self.assertIn("was not restored after", self.last_err)
        self.assertEqual(self.rejections(), 1)


# --------------------------------------------------------------------------- #
# the cap in an audited run
# --------------------------------------------------------------------------- #

class TestTheAuditedCap(TurnCase):

    def cap(self, role: str) -> subprocess.CompletedProcess:
        env = {**{k: v for k, v in self.env.items()}, "CF_MERGE_TARGET": "main"}
        return subprocess.run([sys.executable, "-I", str(SCRIPTS / "run" / "attempt_cap.py"),
                               role, "--root", str(self.root), "--task", TASK, "--cap", "3",
                               "--worktree", str(self.wt), "--manifests", str(self.fx.tasks)],
                              capture_output=True, text=True, env=env, cwd=str(self.wt))

    def test_the_cap_restores_the_best_snapshot_never_unresolved(self):
        self.flag_all()
        self.turn("reviewer")
        best = self.doc_file.read_bytes()
        self.agents.edit = self.restate(0, "a later repair")
        self.turn("worker")
        (attempt_cap.counter_path(self.root, TASK)).write_text(
            json.dumps({"task_id": TASK, "rejections": 3, "history": []}))
        proc = self.cap("worker")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("restored round 1", json.loads(proc.stdout)["summary"])
        self.assertEqual(self.doc_file.read_bytes(), best)
        self.assertNotIn("unresolved", json.loads(self.doc_file.read_text()))
        result = vl.run_gate(self.fx.gate_cfg(), root=self.wt, changed_files=True,
                             base="main", task_id=TASK)
        self.assertEqual(result["exit"], 0, result["problems"] + result["broken"])
        proc = self.cap("reviewer")
        self.assertEqual(json.loads(proc.stdout)["verdict"], "approve", proc.stderr)
        # a tampered restore is not approved mechanically
        self.doc_file.write_text(self.doc_file.read_text().replace("synthetic", "Synthetic"))
        self.assertEqual(self.cap("reviewer").returncode, attempt_cap.EXIT_NOT_CAPPED)

    def test_without_the_audit_the_cap_is_what_it_was(self):
        self.flag_all()
        self.turn("reviewer")
        (attempt_cap.counter_path(self.root, TASK)).write_text(
            json.dumps({"task_id": TASK, "rejections": 3, "history": []}))
        self.env.pop("CF_AUDIT_RULE")
        proc = self.cap("worker")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(self.doc_file.read_text())["unresolved"]["by"], "attempt_cap")

    def test_count_prints_the_count(self):
        attempt_cap.counter_path(self.root, TASK).parent.mkdir(parents=True, exist_ok=True)
        attempt_cap.counter_path(self.root, TASK).write_text(json.dumps({"rejections": 2}))
        proc = subprocess.run([sys.executable, "-I", str(SCRIPTS / "run" / "attempt_cap.py"),
                               "count", "--root", str(self.root), "--task", TASK],
                              capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "2")


# --------------------------------------------------------------------------- #
# what the turn refuses before it runs (references/locate-documents.md section 13.9)
# --------------------------------------------------------------------------- #

class TestTheConfigurationIsRefused(TurnCase):
    """Each configuration the turn refuses, one value changed at a time: exit 78 with the
    problem named, 66 for a missing prompt, and no agent call."""

    def refused(self, *extra: str, code: int = at.EXIT_CONFIG, role: str = "reviewer") -> str:
        out, err = io.StringIO(), io.StringIO()
        argv = [role, "--root", str(self.root), "--prompts", str(self.prompts), "--profile",
                "examplebox", "--tools", "Read,Glob", *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            got = at.main(argv, runner=self.runner, env=self.env, worktree=self.wt)
        self.assertEqual(got, code, err.getvalue())
        self.assertEqual(out.getvalue(), "", "a refusal prints no contract")
        self.assertEqual(self.runner.calls, [])
        return err.getvalue()

    def test_another_host(self):
        self.assertIn("calls claude, not 'codex'", self.refused("--host", "codex"))

    def test_an_unknown_rule(self):
        for rule in ("4", "", "audit/1", "0"):
            with self.subTest(rule=rule):
                self.env["CF_AUDIT_RULE"] = rule
                self.assertIn("is not a rule this bridge knows", self.refused())

    def test_review_rounds_outside_the_two(self):
        for rounds in ("1", "4", "", "three"):
            with self.subTest(rounds=rounds):
                self.env["CF_AUDIT_ROUNDS"] = rounds
                self.assertIn(f"CF_AUDIT_ROUNDS={rounds!r} is not one of", self.refused())

    def test_a_gate_command_that_is_not_the_locate_gate_with_its_config(self):
        gate = f"python3 {self.gate_dir}"
        for cmd in (f"{gate}/verify_findings.py --gate-config {self.gate_dir}/gate.json",
                    f"{gate}/verify_locate.py",
                    f"{gate}/verify_locate.py {self.gate_dir}/gate.json",
                    f"{gate}/verify_locate.py --gate-config", ""):
            with self.subTest(cmd=cmd):
                self.env["CF_GATE_CMD"] = cmd
                self.assertIn("is not the locate gate with its --gate-config", self.refused())

    def test_a_gate_directory_missing_a_host_tool(self):
        for script in ("inventory_audit.py", "inventory_render.py", "inventory_edit.py"):
            with self.subTest(script=script):
                kept = self.gate_dir / (script + ".kept")
                os.replace(self.gate_dir / script, kept)
                try:
                    self.assertIn(f"holds no {script}", self.refused())
                finally:
                    os.replace(kept, self.gate_dir / script)

    def test_a_task_of_more_than_one_document(self):
        manifests = Path(tempfile.mkdtemp(prefix="cf-manifests-"))
        self.addCleanup(shutil.rmtree, manifests, ignore_errors=True)
        manifest = json.loads((Path(self.env["CF_TASK_MANIFESTS"]) / f"{TASK}.json")
                              .read_text())
        self.env["CF_TASK_MANIFESTS"] = str(manifests)
        for documents in (manifest["documents"] * 2, []):
            with self.subTest(documents=len(documents)):
                (manifests / f"{TASK}.json").write_text(json.dumps(
                    {**manifest, "documents": documents}))
                self.assertIn(f"holds one document; task {TASK}'s manifest names "
                              f"{len(documents)}", self.refused())

    def test_a_missing_prompt(self):
        (self.prompts / "reviewer-skeptic.md").unlink()
        self.assertIn("missing prompt", self.refused(code=at.EXIT_NO_PROMPT))
        # the worker's three are its own: it still runs without the reviewer's
        self.assertEqual(self.turn("worker")[0], 0, self.last_err)

    def test_a_tend_directory_that_is_a_symlink(self):
        elsewhere = Path(tempfile.mkdtemp(prefix="cf-elsewhere-"))
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        (self.wt / ".tend").symlink_to(elsewhere, target_is_directory=True)
        code, out = self.turn("reviewer")
        self.assertEqual(code, at.EXIT_FAILED)
        self.assertIsNone(out)
        self.assertIn("is a symlink; nothing is staged through it", self.last_err)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(list(elsewhere.iterdir()), [])


# --------------------------------------------------------------------------- #
# the property: every path ends, within the bound, never UNRESOLVED
# --------------------------------------------------------------------------- #

class TestEveryPathEnds(TurnCase):
    """Random sequences of outcomes -- rejections, infrastructure failures (124, 75, a
    409's 75), gate refusals, invalid audits -- through a simulated tend loop."""

    #: 40 seeds take every path below; at 24 none rejected twice once an unchanged
    #: document stopped paying for a check (the fake worker that edits does it by hand)
    SEEDS = 40
    MAX_TURNS = 150

    def simulate(self, seed: int) -> dict:
        rng = random.Random(seed)
        rounds = rng.choice(vocab.AUDIT_REVIEW_ROUNDS)
        self.env["CF_AUDIT_ROUNDS"] = str(rounds)
        agents = self.agents
        p_infra, p_invalid, p_gate = rng.choice([0, .1, .25]), rng.choice([0, .15, .4]), \
            rng.choice([0, .2, .5])
        items = self.items()

        def decide(turn, call):
            if rng.random() < p_infra:
                return rng.choice([124, 75])
            if call.role == "reviewer":
                if call.step == "audit":
                    agents.flag = {i: {"verdict": "wrong_prominence", "rule": 9,
                                       "reason": "too prominent", "action": "lower it"}
                                   for i in items if rng.random() < .6}
                if rng.random() < p_invalid:
                    agents.invalid[call.out] = 1
                agents.uphold = rng.random() < .8
                if call.step == "check":
                    expect = json.loads((turn.mirror / f"r{call.round}.check.part{call.part}"
                                                       f".expect.json").read_text())
                    agents.statuses = {v: rng.choice(["missing", "applied", "applied",
                                                      "partial", "wrong"])
                                       for v in expect if "-v" in v}
                agents.harmful = rng.random() < .2
            else:
                n = rng.randrange(100)
                agents.edit = (self.restate(rng.randrange(len(items)), f"repair {seed}.{n}")
                               if rng.random() < .6 else None)
            return None

        real = Agents.__call__

        def fake(self_, turn, call):
            got = decide(turn, call)
            return real(self_, turn, call) if got is None else got

        with mock.patch.object(Agents, "__call__", fake), \
                mock.patch.object(ia, "AUDIT_THRESHOLD_MIN", rng.choice([1, 2, 5])):
            queue, done, turns, outcomes = "worker", False, 0, []
            while turns < self.MAX_TURNS and not done:
                turns += 1
                if rng.random() < .1 and (self.wt / ".tend").exists():
                    shutil.rmtree(self.wt / ".tend")            # tend wiped its scratch
                if queue == "worker":
                    count = attempt_cap.read_count(self.root, TASK)
                    self.assertLess(count, 3, "the cap would fire")
                    code, out = self.turn("worker")
                    if code != 0:
                        outcomes.append(("worker", code))
                        continue
                    if rng.random() < p_gate:
                        (self.wt / ".tend").mkdir(exist_ok=True)
                        with open(self.wt / ".tend" / "discussion.md", "a") as fh:
                            fh.write("gate: refused\n")
                        outcomes.append(("gate", 1))
                        continue
                    queue = "reviewer"
                else:
                    code, out = self.turn("reviewer")
                    if code != 0:
                        outcomes.append(("reviewer", code))
                        continue
                    outcomes.append(("reviewer", out["verdict"]))
                    if out["verdict"] == "approve":
                        done = True
                    else:
                        (self.wt / ".tend").mkdir(exist_ok=True)
                        with open(self.wt / ".tend" / "discussion.md", "a") as fh:
                            fh.write(out["feedback_text"] + "\n")
                        queue = "worker"
        return {"done": done, "rounds": rounds, "outcomes": outcomes, "turns": turns}

    def test_random_sequences(self):
        seen = {"two_rejections": 0, "reverts": 0, "gate_resets": 0, "audit_failed": 0,
                "retries": 0, "infra": 0, "no_progress": 0}
        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                if seed:
                    self.tearDown_between()
                got = self.simulate(seed)
                counts = self.state()["counts"]
                seen["two_rejections"] += self.rejections() == 2
                for key in ("reverts", "gate_resets", "audit_failed", "retries", "no_progress"):
                    seen[key] += counts[key]
                seen["infra"] += sum(1 for _who, what in got["outcomes"] if what in (124, 75))
                self.assertTrue(got["done"], got)
                self.assertLessEqual(self.rejections(), got["rounds"] - 1, got)
                doc = json.loads(self.doc_file.read_text())
                self.assertNotIn("unresolved", doc)
                state = self.state()
                self.assertLessEqual(state["round"], got["rounds"])
                self.assertEqual(state["rejections"], self.rejections())
                self.assertEqual(TL.ia.sha256_file(self.doc_file), state["approved"]["sha256"])
        # the seeds took every path the property is about
        self.assertTrue(all(seen.values()), seen)

    def tearDown_between(self):
        """A fresh task between seeds: the state, the counter and the worktree's commits."""
        shutil.rmtree(self.root / "audit", ignore_errors=True)
        shutil.rmtree(self.root / "attempts", ignore_errors=True)
        shutil.rmtree(self.wt / ".tend", ignore_errors=True)
        git(self.wt, "reset", "-q", "--hard", f"{TASK}@{{0}}")
        git(self.wt, "checkout", "-q", "main")
        git(self.wt, "branch", "-q", "-D", TASK)
        git(self.wt, "checkout", "-q", "-b", TASK)
        self.doc_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.doc_path, self.doc_file)
        (self.wt / "tasks" / f"{TASK}.yaml").write_text(f"id: {TASK}\nstatus: complete\n")
        self.commit("the inventory")
        self.agents.__init__()
        self.runner.calls.clear()


# --------------------------------------------------------------------------- #
# the bridge: the unaudited path byte for byte, the audited path through it
# --------------------------------------------------------------------------- #

FAKE_AGENTBOX = r'''#!/usr/bin/env python3
"""A fake agentbox: records its argv (NUL-separated) and answers as the call asks."""
import json, os, re, sys
log = os.environ["FAKE_LOG"]
with open(log, "ab") as fh:
    fh.write(b"\0".join(a.encode() for a in sys.argv[1:]) + b"\n--\n")
argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
out = re.search(r"Write exactly one file: (\S+?)\.\n", prompt + "\n")
if out:
    path = out.group(1)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if "AUDITOR" in prompt:
        idx = json.load(open(".tend/audit/r1.render.json"))
        part = int(re.search(r"part (\d+) of", prompt).group(1))
        rows = [{"item": i, "origin": "lead", "verdict": "clean", "rule": 0, "merge_with": "",
                 "reason": "", "action": ""} for i in idx["parts"][part - 1]["formal"]]
        data = {"passages": rows, "expository_flags": [], "per_spec_formal_estimate": len(rows),
                "patterns": [], "subject_check": "one", "kind": "audit"}
    else:
        idx = json.load(open(".tend/audit/r1.render.json"))
        part = int(re.search(r"part (\d+) of", prompt).group(1))
        data = {"verdicts": [], "missed": [], "disputes": [],
                "examined": idx["parts"][part - 1]["formal"], "per_spec_formal_estimate": 1,
                "summary": "fine", "kind": "skeptic"}
    data.update(schema="cf-inventory-audit/1", round=1, source_id=os.environ["FAKE_SID"])
    json.dump(data, open(path, "w"))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done",
                  "total_cost_usd": 0.5, "duration_ms": 1000}))
'''


class BridgeCase(TurnCase):

    def bridge(self, role: str, extra: dict | None = None, discussion: str | None = None,
               source: Path = BRIDGE):
        fakebin = self.root / "fakebin"
        fakebin.mkdir(exist_ok=True)
        (fakebin / "agentbox").write_text(FAKE_AGENTBOX, encoding="utf-8")
        (fakebin / "agentbox").chmod(0o755)
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        for name in ("agent_call.py", "normalize_result.py", "attempt_cap.py",
                     "audit_turn.py"):
            shutil.copy2(SCRIPTS / "run" / name, bindir / name)
        shutil.copy2(source, bindir / "bridge-agentbox.sh")
        if discussion is not None:
            (self.wt / ".tend").mkdir(exist_ok=True)
            (self.wt / ".tend" / "discussion.md").write_text(discussion)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CF_", "TEND_", "GIT_CONFIG_"))}
        env.update(PATH=f"{fakebin}:{os.environ['PATH']}", CF_BRIDGE_ROOT=str(self.root),
                   CF_AGENTBOX_PROFILE="examplebox", CF_SLOT_DIR=str(self.root / "slots"),
                   TEND_TASK_ID=TASK, FAKE_LOG=str(self.root / "agentbox.log"), FAKE_SID=SID,
                   CF_GATE_CMD=self.env["CF_GATE_CMD"], CF_TASK_MANIFESTS=self.env[
                       "CF_TASK_MANIFESTS"], **(extra or {}))
        return subprocess.run(["bash", str(bindir / "bridge-agentbox.sh"), role],
                              capture_output=True, text=True, cwd=str(self.wt), env=env,
                              timeout=300)

    def argvs(self) -> list[list[str]]:
        path = self.root / "agentbox.log"
        if not path.exists():
            return []
        return [chunk.split("\0") for chunk in
                path.read_text().split("\n--\n") if chunk]


class TestTheAuditedBridge(BridgeCase):

    def test_a_round_through_the_bridge_with_stepped_envelopes(self):
        proc = self.bridge("reviewer", {"CF_AUDIT_RULE": "1", "CF_AUDIT_ROUNDS": "2"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["verdict"], "approve")
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1)
        names = sorted(p.name for p in (self.root / "usage").iterdir())
        steps = [watchdog.envelope_name(n)["step"] for n in names]
        self.assertEqual(sorted(steps), ["audit", "skeptic"])
        argv = self.argvs()[0]
        self.assertEqual(argv[:5], ["host", "claude", "--profile", "examplebox", "--"])
        self.assertIn("--permission-mode", argv)
        spent = watchdog.ledger(self.root / "usage")
        self.assertEqual(spent["by_step"], {"audit": {"envelopes": 1, "dollars": 0.5},
                                            "skeptic": {"envelopes": 1, "dollars": 0.5}})

    def test_a_missing_turn_machine_is_refused(self):
        (self.root / "bin").mkdir(exist_ok=True)
        proc = self.bridge("reviewer", {"CF_AUDIT_RULE": "1", "CF_AUDIT_ROUNDS": "2"})
        os.remove(self.root / "bin" / "audit_turn.py")
        bindir = self.root / "bin"
        env = {"PATH": os.environ["PATH"], "CF_BRIDGE_ROOT": str(self.root),
               "CF_AGENTBOX_PROFILE": "examplebox", "CF_AUDIT_RULE": "1", "TEND_TASK_ID": TASK}
        proc = subprocess.run(["bash", str(bindir / "bridge-agentbox.sh"), "reviewer"],
                              capture_output=True, text=True, cwd=str(self.wt), env=env)
        self.assertEqual(proc.returncode, 78)
        self.assertIn("audit_turn.py is missing", proc.stderr)


#: the unaudited bridge's agent argv and contract, as 99fbad4's bridge-agentbox.sh made
#: them (recorded by running it in this harness), for three turns: a worker's first call,
#: a worker's revision (a discussion on disk) and a reviewer, each against a fake agent
#: that writes no result file; <WT> is the worktree, <GATE> the gate directory.  The
#: system prompt is passed as staged ($TEND_TASK_ID unsubstituted), as it always was
GOLDEN = {
    "worker": ["host", "claude", "--profile", "examplebox", "--", "-p",
               "inventory task t-inv-0001 in <WT>", "--append-system-prompt",
               "worker system for $TEND_TASK_ID", "--output-format", "json",
               "--permission-mode", "acceptEdits", "--allowedTools",
               "Read,Glob,Grep,Edit,Write,Bash(git:*),Bash(python3 <GATE>/verify_locate.py "
               "--gate-config <GATE>/gate.json --findings:*)"],
    "revision": ["host", "claude", "--profile", "examplebox", "--", "-p",
                 "inventory task t-inv-0001 in <WT>\n\n## THIS IS A REVISION — a reviewer "
                 "rejected the previous attempt\n\nRead the feedback below and FIX EXACTLY "
                 "WHAT IT NAMES. Do not restart from scratch and do\nnot re-litigate the "
                 "reviewer's judgement; if you disagree, address the point explicitly in\nyour "
                 "summary. Re-run the validation gate before finishing.\n\n--- prior discussion "
                 "(most recent last) ---\nreviewer: the receipt is thin\n--- end ---",
                 "--append-system-prompt", "worker system for $TEND_TASK_ID", "--output-format",
                 "json", "--permission-mode", "acceptEdits", "--allowedTools",
                 "Read,Glob,Grep,Edit,Write,Bash(git:*),Bash(python3 <GATE>/verify_locate.py "
                 "--gate-config <GATE>/gate.json --findings:*)"],
    "reviewer": ["host", "claude", "--profile", "examplebox", "--", "-p",
                 "reviewer task", "--append-system-prompt", "reviewer system",
                 "--output-format", "json", "--permission-mode", "acceptEdits",
                 "--allowedTools", "Read,Glob,Grep,Bash(git:*),Bash(python3 "
                 "<GATE>/verify_locate.py --gate-config <GATE>/gate.json --findings:*)"],
}
GOLDEN_STDOUT = {
    "worker": {"schema_version": 1, "status": "needs_review", "summary": "done"},
    "revision": {"schema_version": 1, "status": "needs_review", "summary": "done"},
    "reviewer": {"schema_version": 1, "verdict": "request_changes",
                 "notes": "done | verdict parsed from reviewer prose (no result file written)",
                 "feedback_text": "done"},
}


class TestTheUnauditedBridgeIsUnchanged(BridgeCase):
    """Without CF_AUDIT_RULE the bridge makes the call 99fbad4's made, byte for byte."""

    def setUp(self):
        super().setUp()
        (self.prompts / "reviewer.md").write_text("reviewer task\n")
        (self.prompts / "reviewer-system.md").write_text("reviewer system\n")

    def run_three(self, bridge: Path = BRIDGE) -> tuple[list, list]:
        argvs, outs = [], []
        for name, role, discussion in (("worker", "worker", None),
                                       ("revision", "worker", "reviewer: the receipt is thin\n"),
                                       ("reviewer", "reviewer", None)):
            shutil.rmtree(self.wt / ".tend", ignore_errors=True)
            (self.root / "agentbox.log").unlink(missing_ok=True)
            # CF_AUDIT_ROUNDS alone is no audit: only CF_AUDIT_RULE turns it on
            proc = self.bridge(role, {"CF_PROMPT_DIR": str(self.prompts),
                                      "CF_AUDIT_ROUNDS": "3"}, discussion, source=bridge)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            argv = [a.replace(str(self.wt), "<WT>").replace(str(self.gate_dir), "<GATE>")
                    for a in self.argvs()[-1]]
            argvs.append((name, argv))
            outs.append((name, json.loads(proc.stdout)))
        return argvs, outs

    def test_the_prompt_and_arguments_are_the_golden_ones(self):
        argvs, outs = self.run_three()
        for name, argv in argvs:
            with self.subTest(turn=name):
                self.assertEqual(argv, GOLDEN[name])
        for name, out in outs:
            with self.subTest(turn=name):
                self.assertEqual(out, GOLDEN_STDOUT[name])
        names = sorted(p.name for p in (self.root / "usage").iterdir())
        self.assertTrue(names and all(watchdog.envelope_name(n)["step"] is None
                                      for n in names), names)
        self.assertFalse((self.root / "audit").exists())

    def test_the_bridge_of_the_base_commit_makes_the_same_calls(self):
        got = subprocess.run(["git", "-C", str(SKILL), "show",
                              "99fbad4:scripts/run/bridge-agentbox.sh"],
                             capture_output=True, text=True)
        if got.returncode != 0:
            self.skipTest("this checkout does not hold commit 99fbad4")
        old = self.root / "old-bridge.sh"
        old.write_text(got.stdout)
        then, then_out = self.run_three(old)
        now, now_out = self.run_three()
        self.assertEqual(then, now)
        self.assertEqual(then_out, now_out)


class TestTheEnvelopeNames(unittest.TestCase):

    def test_agent_call_names_a_step_and_only_then(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        plain = agent_call.keep_envelope(tmp, b"{}", task_id="t-1", role="reviewer")
        stepped = agent_call.keep_envelope(tmp, b"{}", task_id="t-1", role="reviewer",
                                           step="skeptic")
        self.assertRegex(plain.name, r"^t-1\.reviewer\.\d{8}T\d{6}Z\.\d+\.json$")
        self.assertRegex(stepped.name, r"^t-1\.reviewer\.skeptic\.\d{8}T\d{6}Z\.\d+\.json$")
        record = agent_call.call_record(outcome="timeout", role="worker", task_id="t", slot=1,
                                        seconds=1, timeout=2, code=124)
        self.assertNotIn("step", record["cf_agent_call"])
        record = agent_call.call_record(outcome="timeout", role="worker", task_id="t", slot=1,
                                        seconds=1, timeout=2, code=124, step="revision")
        self.assertEqual(record["cf_agent_call"]["step"], "revision")

    def test_the_watchdog_reads_both_forms(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name, cost in (("t.1.worker.20261022T101010Z.11.json", 1.0),
                           ("t.1.reviewer.audit.20261022T101011Z.12.json", 2.0),
                           ("t.1.reviewer.skeptic.20261022T101012Z.13.json", 0.5)):
            (tmp / name).write_text(json.dumps({"total_cost_usd": cost, "duration_ms": 10}))
        spent = watchdog.ledger(tmp)
        self.assertEqual(spent["envelopes"], 3)
        self.assertEqual(spent["dollars"], 3.5)
        self.assertEqual(spent["by_step"], {"audit": {"envelopes": 1, "dollars": 2.0},
                                            "skeptic": {"envelopes": 1, "dollars": 0.5}})
        self.assertEqual(watchdog.envelope_name("t.1.worker.20261022T101010Z.11.json"),
                         {"task_id": "t.1", "role": "worker", "step": None,
                          "stamp": "20261022T101010Z", "pid": 11})
        self.assertIsNone(watchdog.envelope_name("t.1.auditor.20261022T101010Z.11.json"))
        os.remove(tmp / "t.1.reviewer.audit.20261022T101011Z.12.json")
        os.remove(tmp / "t.1.reviewer.skeptic.20261022T101012Z.13.json")
        self.assertNotIn("by_step", watchdog.ledger(tmp))


if __name__ == "__main__":
    unittest.main()
