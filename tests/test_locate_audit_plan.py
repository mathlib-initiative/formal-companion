"""The audited inventory's planner and prompts: ``cf plan-locate --audit-rounds``.

New code, for references/locate-documents.md section 13 (the audit's records and the
turn machine of 13.9) and docs/proposals/wikipedia-locate.md section 5 (the tend
reviewer of an audited inventory).  What is pinned: the flag (an inventory's only, 2
or 3, kept by a re-plan), ``run.yaml`` ``locate.audit`` and the configuration pins,
the wrappers' exports and allowlists (every command the prompts render and the turn
machine appends is admitted, nothing wider), the seven prompts the turn machine
reads with no unfilled slot of the plan's or the turn's, the rule card's C1 to C10
against docs/proposals/statement-cut.md section 3.4, every JSON shape the prompts
show against the schema ``inventory_audit.py`` holds the files to, and an unaudited
plan equal to the base commit's (99fbad4) apart from its commit, the staged files
whose source changed since and the inventory prompt's kind sentence.  Every page,
library and document is made up.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import dataclasses
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
for path in (SCRIPTS, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import _locate_fixtures as F  # noqa: E402
import test_audit_turn as TA  # noqa: E402
import test_locate_inventory_audit as TL  # noqa: E402
from cflib import hashing, vocab  # noqa: E402
from cflib.yamlio import read_yaml  # noqa: E402
from locate import plan as lplan, presets as lp, prompts as lprompts  # noqa: E402
from locate import tend_root as ltr  # noqa: E402
import test_locate_statement_cut as TS  # noqa: E402
from test_locate_planner import Campaign, scratch, ws  # noqa: E402

at = TA.at
ia = TL.ia
PROPOSAL = (SKILL / "docs" / "proposals" / "statement-cut.md").read_text(encoding="utf-8")
#: the base commit the audit's build branched from: an unaudited plan is its plan
BASE = "99fbad4"
AUDIT_TEMPLATE_NAMES = ("locate-inventory-audit-system.md.tmpl", "locate-inventory-audit.md.tmpl",
                        "locate-inventory-check.md.tmpl", "locate-inventory-skeptic.md.tmpl",
                        "locate-inventory-revision.md.tmpl")
#: the slots the turn machine fills (audit_turn.Turn.text) and the bridge's
TURN_SLOTS = {"CF_AUDIT_ROUND", "CF_AUDIT_ROUNDS", "CF_AUDIT_SOURCE", "CF_AUDIT_DOCUMENT"}
TEND_SLOTS = {"TEND_TASK_ID", "TEND_WORKTREE_PATH", "TEND_WORKTREE_ID"}


def section_34() -> str:
    start = PROPOSAL.index("### 3.4 ")
    return PROPOSAL[start:PROPOSAL.index("### 3.5 ", start)]


def rule_names() -> list[str]:
    """The ten rules of the proposal's section 3.4, by their bold names, in order."""
    found = re.findall(r"(?m)^(\d+)\. \*\*([^*]+?)\.?\*\*", section_34())
    assert [int(n) for n, _ in found] == list(range(1, 11)), found
    return [name.strip().rstrip(".") for _n, name in found]


def exports(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in
            re.finditer(r'(?m)^export ([A-Z_]+)="?(.*?)"?$', text)}


def admitted(command: str, tools: str) -> bool:
    """Whether a ``Bash(<prefix>:*)`` entry of the allowlist admits ``command``."""
    for entry in tools.split(","):
        m = re.fullmatch(r"Bash\((.*):\*\)", entry)
        if m and (command == m.group(1) or command.startswith(m.group(1) + " ")):
            return True
    return False


def json_blocks(text: str) -> list[str]:
    return re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)


class Audited(Campaign):
    """The campaign's workspace, with ``inv-a`` planned audited in three review rounds."""

    ROUNDS = 3

    def setUp(self):
        super().setUp()
        code, out, err = self.plan("inv-a", "--audit-rounds", str(self.ROUNDS))
        self.assertEqual(code, 0, out + err)
        self.out = out + err

    def plan(self, run_id: str, *extra: str, preset: str = "locate-inventory"):
        return ws(self.root, "plan-locate", "--preset", preset, "--sourceset", F.SS,
                  "--universe", F.U, "--run-id", run_id, *extra)

    def wrappers(self, run_id: str) -> dict[str, str]:
        bindir = self.paths.tend_root(run_id) / "bin"
        return {role: (bindir / f"{role}-agent.sh").read_text(encoding="utf-8")
                for role in ("worker", "reviewer")}

    def prompts(self, run_id: str = "inv-a") -> dict[str, str]:
        pdir = self.paths.prompts_dir(run_id)
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(pdir.iterdir())}

    def gate_dir(self, run_id: str = "inv-a") -> Path:
        return self.paths.tend_root(run_id) / "gate"


# --------------------------------------------------------------------------- #
# the flag and run.yaml
# --------------------------------------------------------------------------- #

class TestTheFlag(Audited):

    def test_run_yaml_records_the_audit_and_its_pins(self):
        block = read_yaml(self.paths.run_yaml("inv-a"))["locate"]
        self.assertEqual(block["audit"], {"rule": vocab.INVENTORY_AUDIT_RULE,
                                          "review_rounds": 3})
        self.assertEqual(block["config_shas"]["agents"]["audit"],
                         {"rule": 1, "review_rounds": 3})
        prompts = block["config_shas"]["prompts"]
        tdir = SKILL / "templates" / "prompts"
        for name in AUDIT_TEMPLATE_NAMES:
            self.assertEqual(prompts[name], hashing.sha256_file(tdir / name), name)
        # the audit's reviewer prompts replace the form reviewer's
        self.assertNotIn("locate-reviewer.md.tmpl", prompts)
        self.assertNotIn("locate-reviewer-system.md.tmpl", prompts)
        self.assertIn("locate-inventory.md.tmpl", prompts)
        self.assertIn("audit    : rule 1, at most 3 review rounds", self.out)
        plain = read_yaml(self.paths.run_yaml("inv-1"))["locate"]
        self.assertNotIn("audit", plain)
        self.assertNotIn("audit", plain["config_shas"]["agents"])

    def test_two_rounds_are_recorded_as_two(self):
        code, out, err = self.plan("inv-2", "--audit-rounds", "2")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_yaml(self.paths.run_yaml("inv-2"))["locate"]["audit"],
                         {"rule": 1, "review_rounds": 2})
        for text in self.wrappers("inv-2").values():
            self.assertIn('export CF_AUDIT_ROUNDS="2"\n', text)

    def test_only_two_or_three_rounds(self):
        for value in ("1", "4", "0"):
            with self.subTest(value=value):
                code, out, err = self.plan("inv-x", "--audit-rounds", value)
                self.assertNotEqual(code, 0)
                self.assertIn("--audit-rounds", out + err)
                self.assertFalse(self.paths.run_yaml("inv-x").exists())

    def test_only_an_inventory_is_audited(self):
        F.plan_locate(self, self.root, run_id="loc-1")
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate", "--sourceset", F.SS,
                            "--universe", F.U, "--inventory-run", "inv-1", "--run-id", "loc-a",
                            "--audit-rounds", "3")
        self.assertNotEqual(code, 0)
        self.assertIn("--audit-rounds belongs to the locate-inventory preset", out + err)
        self.assertFalse(self.paths.run_yaml("loc-a").exists())
        with self.assertRaises(ValueError):
            lprompts.template_files(lp.get(vocab.LOCATE), audited=True)
        from cflib.errors import CfError
        with self.assertRaises(CfError):
            ltr.wrapper_env(lp.get(vocab.LOCATE), "/g", "/db", audit_rounds=3)
        with self.assertRaises(CfError):
            ltr.wrapper_env(lp.get(vocab.LOCATE_INVENTORY), "/g", "/db", audit_rounds=4)

    def test_only_the_bridge_that_runs_the_turn_machine(self):
        inventory = lp.get(vocab.LOCATE_INVENTORY)
        other = dataclasses.replace(inventory, bridge="claude-p")
        with mock.patch.object(lp, "get", return_value=other):
            code, out, err = self.plan("inv-p", "--audit-rounds", "3")
        self.assertNotEqual(code, 0)
        self.assertIn("cannot run an audit", out + err)
        self.assertFalse(self.paths.run_yaml("inv-p").exists())

    def test_a_re_plan_keeps_the_audit(self):
        def snapshot(run_id):
            rd = self.paths.run_dir(run_id)
            return {p.relative_to(rd).as_posix(): p.read_bytes() for p in sorted(rd.rglob("*"))
                    if p.is_file() and p.name != "run.yaml"}
        before = snapshot("inv-a")
        code, out, err = self.plan("inv-a", "--audit-rounds", "3")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(snapshot("inv-a"), before)
        for run_id, extra, said in (("inv-a", (), "audited in 3 review rounds, not unaudited"),
                                    ("inv-a", ("--audit-rounds", "2"),
                                     "audited in 3 review rounds, not audited in 2"),
                                    ("inv-1", ("--audit-rounds", "3"),
                                     "unaudited, not audited in 3 review rounds")):
            with self.subTest(run_id=run_id, extra=extra):
                code, out, err = self.plan(run_id, *extra)
                self.assertNotEqual(code, 0)
                self.assertIn(said, out + err)
        self.assertEqual(snapshot("inv-a"), before)

    def test_the_audit_tools_are_staged_and_pinned(self):
        pins = read_yaml(self.paths.run_yaml("inv-a"))["locate"]["gate_files"]
        for rel, name in ltr.AUDIT_GATE_FILES:
            self.assertEqual(pins[f"gate/{name}"], hashing.sha256_file(SKILL / rel))
        self.assertIn("bin/audit_turn.py", pins)


# --------------------------------------------------------------------------- #
# the wrappers
# --------------------------------------------------------------------------- #

class TestTheWrappers(Audited):

    def test_both_export_the_rule_and_the_rounds(self):
        for role, text in self.wrappers("inv-a").items():
            with self.subTest(role=role):
                env = exports(text)
                self.assertEqual(env["CF_AUDIT_RULE"], str(vocab.INVENTORY_AUDIT_RULE))
                self.assertEqual(env["CF_AUDIT_ROUNDS"], "3")
                self.assertLess(text.index("CF_AUDIT_RULE"), text.index("exec "))
        for text in self.wrappers("inv-1").values():
            self.assertNotIn("CF_AUDIT", text)

    def test_the_reviewer_is_narrowed_and_the_worker_widened_by_the_tools_only(self):
        gate = self.gate_dir()
        audited, plain = self.wrappers("inv-a"), self.wrappers("inv-1")
        reviewer = exports(audited["reviewer"])["CF_REVIEWER_TOOLS"].split(",")
        gate_cmd = f"python3 {gate}/verify_locate.py --gate-config {gate}/gate.json"
        show = f"python3 {gate}/inventory_render.py --gate-config {gate}/gate.json --show"
        self.assertEqual(reviewer, [
            "Read", "Glob", "Grep", "Bash(git diff:*)", "Bash(git show:*)", "Bash(git log:*)",
            "Bash(git status:*)", f"Bash({gate_cmd} --findings:*)", f"Bash({show}:*)",
            f"Bash(python3 {gate}/inventory_audit.py validate:*)"])
        self.assertNotIn("Bash(git:*)", reviewer)
        worker = exports(audited["worker"])["CF_EXTRA_TOOLS"].split(",")
        before = exports(plain["worker"])["CF_EXTRA_TOOLS"].replace("/inv-1/", "/inv-a/")
        self.assertEqual(worker[:3], before.split(","))
        self.assertEqual(worker[3:], [f"Bash(python3 {gate}/inventory_edit.py:*)",
                                      f"Bash({show}:*)",
                                      f"Bash(python3 {gate}/inventory_audit.py validate:*)"])
        self.assertNotIn("CF_REVIEWER_TOOLS", audited["worker"])
        self.assertNotIn("CF_EXTRA_TOOLS", audited["reviewer"])

    def test_every_command_a_call_is_given_is_admitted(self):
        """The validator lines the turn machine appends, and every host-tool line the
        prompts render, pass the role's allowlist; the bridge's own subcommands do not."""
        gate = self.gate_dir()
        wrappers = {role: exports(text) for role, text in self.wrappers("inv-a").items()}
        reviewer = wrappers["reviewer"]["CF_REVIEWER_TOOLS"]
        # the worker's list is the bridge's default with the gate entry, plus its extras
        gate_cmd = wrappers["worker"]["CF_GATE_CMD"]
        worker = (f"Read,Glob,Grep,Edit,Write,Bash(git:*),Bash({gate_cmd} --findings:*),"
                  + wrappers["worker"]["CF_EXTRA_TOOLS"])
        turn = type("T", (), {"gate_dir": gate})()
        doc = f"findings/t-1/{F.W1}.json"
        lines = {
            "reviewer": [at.Turn.validate_line(turn, ["validate", "--kind", kind, "--file",
                                                      f".tend/audit/r1.{kind}.part1.json",
                                                      "--document", doc])
                         for kind in ("audit", "check", "skeptic")],
            "worker": [at.Turn.validate_line(turn, ["validate", "--kind", kind, "--file",
                                                    f".tend/audit/r1.{kind}.json", "--document",
                                                    doc]) for kind in ("ops", "repair")],
        }
        prompts = self.prompts()
        for name in ("reviewer-system.md", "reviewer.md", "reviewer-check.md",
                     "reviewer-skeptic.md"):
            lines["reviewer"] += [ln.strip() for ln in prompts[name].splitlines()
                                  if ln.strip().startswith("python3 ")]
            lines["reviewer"] += re.findall(r"`(python3 [^`]+)`", prompts[name])
        lines["worker"] += [ln.strip() for ln in prompts["worker-revision.md"].splitlines()
                            if ln.strip().startswith("python3 ")]
        lines["worker"] += re.findall(r"`(python3 [^`]+)`", prompts["worker-revision.md"])
        tools = {"reviewer": reviewer, "worker": worker}
        for role, commands in lines.items():
            self.assertGreaterEqual(len(commands), 4, role)
            for command in commands:
                command = command.replace("$CF_AUDIT_DOCUMENT", doc)
                with self.subTest(role=role, command=command[:80]):
                    self.assertTrue(admitted(command, tools[role]), command)
        for role in ("reviewer", "worker"):
            for sub in ("merge", "strip", "collateral"):
                with self.subTest(role=role, sub=sub):
                    self.assertFalse(admitted(f"python3 {gate}/inventory_audit.py {sub} --round 1",
                                              tools[role]))
        self.assertFalse(admitted(f"python3 {gate}/inventory_edit.py --document {doc} --ops x",
                                  reviewer))
        self.assertFalse(admitted("git commit -m x", reviewer))
        self.assertFalse(admitted(f"python3 {gate}/inventory_render.py --gate-config "
                                  f"{gate}/gate.json --document {doc} --out-dir .tend/audit",
                                  reviewer))


# --------------------------------------------------------------------------- #
# the prompts
# --------------------------------------------------------------------------- #

class TestThePrompts(Audited):

    def test_the_seven_the_turn_machine_reads_with_no_unfilled_slot(self):
        prompts = self.prompts()
        wanted = {name for names in at.PROMPT_FILES.values() for name in names}
        self.assertEqual(set(prompts), wanted)
        self.assertEqual(set(lprompts.AUDIT_PROMPT_FILES), wanted)
        self.assertEqual(set(self.prompts("inv-1")), set(lprompts.PROMPT_FILES))
        for name, text in prompts.items():
            with self.subTest(prompt=name):
                self.assertNotRegex(text, r"\{\{")
                self.assertLessEqual(set(re.findall(r"\$(CF_AUDIT_[A-Z_]+)", text)), TURN_SLOTS)
                self.assertLessEqual(set(re.findall(r"\$(TEND_[A-Z_]+)", text)), TEND_SLOTS)
                self.assertNotIn("/Users/", text)

    def test_the_worker_keeps_its_prompts(self):
        audited, plain = self.prompts(), self.prompts("inv-1")
        for name in ("worker-system.md", "worker.md"):
            with self.subTest(prompt=name):
                self.assertEqual(audited[name], plain[name].replace("inv-1", "inv-a"))
        for name in ("reviewer-system.md", "reviewer.md"):
            self.assertIn("Your job here is form, not judgment", plain["reviewer-system.md"])
            self.assertNotIn("form, not judgment", audited[name])

    def test_the_turn_machine_fills_every_slot_from_the_planned_root(self):
        """audit_turn.Turn, built on the planned root as the bridge builds it, finds every
        prompt and leaves no slot of the turn's or the bridge's unfilled."""
        root = self.paths.tend_root("inv-a")
        env = exports(self.wrappers("inv-a")["reviewer"])
        task = sorted(p.stem for p in (self.gate_dir() / "tasks").glob("*.json"))[0]
        wt = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, wt, ignore_errors=True)
        env.update(TEND_TASK_ID=task, TEND_WORKTREE_ID="wt-1", TEND_WORKTREE_PATH=str(wt))
        for role, names in at.PROMPT_FILES.items():
            args = at.parser().parse_args([role, "--root", str(root), "--prompts",
                                           str(root / "prompts"), "--profile", "examplebox",
                                           "--tools", "Read"])
            turn = at.Turn(args, at.Runner(), env=env, worktree=wt)
            self.assertEqual(turn.review_rounds, 3)
            for name in names:
                with self.subTest(role=role, prompt=name):
                    text = turn.text(name, {"CF_AUDIT_ROUND": 2})
                    self.assertNotRegex(text, r"\$(CF_AUDIT|TEND)_")
                    if name not in ("worker.md", "worker-system.md"):
                        self.assertIn(turn.source, text)

    def test_the_rule_card_is_the_proposals_section_3_4(self):
        system = self.prompts()["reviewer-system.md"]
        card = re.findall(r"(?m)^- \*\*C(\d+) ([^*]+?)\.\*\*", system)
        self.assertEqual([int(n) for n, _ in card], list(range(1, 11)))
        self.assertEqual([name for _n, name in card], rule_names())
        # the inventory prompt the card points at holds the same rules in the same order,
        # and the revision names them in that order
        worker = self.prompts()["worker.md"]
        cut = worker[worker.index("## How to cut"):worker.index("For each passage decide")]
        bullets = [b.rstrip(".") for b in re.findall(r"(?m)^- \*\*([^*]+?)\*\*", cut)]
        self.assertEqual(bullets, rule_names())
        named = re.search(r"numbered 1 to 10 as they come: ([^)]*)\)",
                          self.prompts()["worker-revision.md"]).group(1)
        self.assertEqual(named.split(", "), [n[0].lower() + n[1:] if n != "PD9" else n
                                             for n in rule_names()])
        self.assertIn(", ".join(f'"{c}"' for c in vocab.CUT_CONNECTIVES), system)
        self.assertIn(vocab.AUDIT_VERDICTS[0], system)

    def test_what_the_gate_allows_is_the_plans_statement_cut_rule(self):
        system = self.prompts()["reviewer-system.md"]
        rule = lplan.rule_versions()["statement_cut_rule"]
        self.assertIn(f"**What the gate allows.** {lprompts.gate_bound(rule)}", system)
        self.assertIn(f"(statement-cut rule {rule})", system)
        gate = json.loads((self.gate_dir() / "gate.json").read_text())
        self.assertEqual(gate["statement_cut_rule"], rule)
        wide = lprompts.gate_bound(vocab.STATEMENT_CUT_WIDE)
        for words in ("footnote mark", "`dd` that holds text", "opens with a formula",
                      "text-only `dd`", '"Then"'):
            self.assertIn(words, wide)
            self.assertNotIn(words, lprompts.gate_bound(vocab.STATEMENT_CUT_CONTINUED))
        with self.assertRaises(ValueError):
            lprompts.gate_bound(vocab.STATEMENT_CUT_FIRST)

    def test_each_prompt_names_the_vocabulary_its_file_takes(self):
        prompts = self.prompts()
        audit, check = prompts["reviewer.md"], prompts["reviewer-check.md"]
        skeptic, revision = prompts["reviewer-skeptic.md"], prompts["worker-revision.md"]
        for verdict in vocab.AUDIT_VERDICTS:
            if verdict != "should_be_formal":
                self.assertIn(f"`{verdict}`", audit)
        for origin in vocab.AUDIT_ORIGINS:
            self.assertIn(f"`{origin}`", audit)
        for hint in ("kind-other", "kind-rule", "in-proof", "in-example", "connective-start",
                     "widened"):
            self.assertIn(f"`{hint}`", audit)
            self.assertIn(hint, ir_hint_names())
        self.assertIn("`HINT widened`", check)
        for status in vocab.AUDIT_CHECK_STATUSES:
            self.assertIn(f"`{status}`", check)
        for change in vocab.AUDIT_COLLATERAL_CHANGES:
            self.assertIn(f"`{change}`", check)
        for status in vocab.AUDIT_REPAIR_STATUSES:
            self.assertIn(f"`{status}`", revision)
        for name in (*vocab.AUDIT_EDIT_OPS, *vocab.AUDIT_EDIT_FIELDS):
            self.assertIn(f"`{name}`", revision)
        self.assertIn(f"at least {vocab.AUDIT_SKEPTIC_FLOOR} of them", skeptic)
        self.assertIn("Default to refuting", skeptic)
        self.assertIn("withheld", skeptic)
        # the reviewer never writes the result; the worker finishes as worker.md says
        for name in ("reviewer-system.md", "reviewer.md", "reviewer-check.md",
                     "reviewer-skeptic.md"):
            self.assertNotIn('"verdict": "approve"', prompts[name])
        self.assertIn("Do not write `.tend/agent_result.json`", prompts["reviewer-system.md"])

    def test_every_shape_the_prompts_show_is_one_the_schema_takes(self):
        prompts = self.prompts()
        expected = {"reviewer.md": ["audit"], "reviewer-skeptic.md": ["skeptic"],
                    "reviewer-check.md": ["check"], "worker-revision.md": ["ops", "repair"]}
        for name, kinds in expected.items():
            blocks = json_blocks(prompts[name])
            self.assertEqual(len(blocks), len(kinds), name)
            for kind, block in zip(kinds, blocks):
                with self.subTest(prompt=name, kind=kind):
                    k = 2 if kind == "check" else 1
                    data = json.loads(block.replace("$CF_AUDIT_ROUND", str(k))
                                      .replace("$CF_AUDIT_SOURCE", F.W1))
                    self.assertEqual(data["schema"], vocab.INVENTORY_AUDIT_SCHEMA)
                    self.assertEqual(data["kind"], kind)
                    problems: list[str] = []
                    self.assertTrue(ia.check_record(data, kind, kind, problems), problems)
                    if kind == "ops":
                        self.assertEqual(ia.validate_ops(data, None), [])
                        self.assertEqual({op["op"] for op in data["ops"]},
                                         {"merge", "split", "set"})
                    for row in data.get("passages") or []:
                        bad: list[str] = []
                        ia._verdict_rules(row, "row", bad, items={
                            r["item"]: {} for r in data["passages"]})
                        self.assertEqual(bad, [], row)


def ir_hint_names() -> str:
    return (SCRIPTS / "gate" / "inventory_render.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# an unaudited plan is the base commit's
# --------------------------------------------------------------------------- #

class TestAnUnauditedPlanIsTheBaseCommits(unittest.TestCase):
    """The base commit's planner and this one, on one workspace, write the same plan.

    The plan is made by 99fbad4's ``cf plan-locate`` (its tree from ``git archive``)
    and then re-made by this checkout's, as the same run.  Every file of the run
    directory is byte for byte the same -- the rendered prompts, the wrappers,
    ``config.yaml``, the task files and manifests, both gate.json files -- but the
    files staged from a source this branch changed, ``audit_turn.py``, which
    every locate root's ``bin/`` stages since the turn machine, the worker
    prompt, which is the base commit's with the kind sentence
    (``test_locate_statement_cut.KIND_SENTENCE``) after the kinds and nothing else,
    and the three gate.json files, which name statement-cut rule 3 where the base
    commit's named 2 (the flip of the wide bound, docs/proposals/statement-cut.md
    section 7) and nothing else; ``run.yaml`` differs only in its skill commit, those
    files' pins and the worker prompt's two shas.
    """

    def setUp(self):
        self.root = scratch(self)
        self.paths = F.campaign(self, self.root)
        tar = subprocess.run(["git", "-C", str(SKILL), "archive", "--format=tar", BASE,
                              "scripts", "templates", "vendor", "registry"],
                             capture_output=True)
        if tar.returncode != 0:
            self.skipTest(f"this checkout does not hold commit {BASE}")
        self.base = self.root.parent / "base"
        with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as archive:
            archive.extractall(self.base, filter="data") if sys.version_info >= (3, 12) \
                else archive.extractall(self.base)
        changed = subprocess.run(["git", "-C", str(SKILL), "diff", "--name-only", BASE, "--",
                                  "scripts", "vendor"], capture_output=True, text=True)
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.changed = set(changed.stdout.split())

    def snapshot(self, run_id: str) -> dict[str, bytes]:
        rd = self.paths.run_dir(run_id)
        return {p.relative_to(rd).as_posix(): p.read_bytes() for p in sorted(rd.rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts}

    def staged_from(self) -> dict[str, str]:
        """``{path under the tend root: source in the skill}`` of every staged script."""
        from plan.make_tend_root import BRIDGE_SCRIPTS
        out = {f"gate/{name}": rel for rel, name in ltr.gate_file_rows(True)}
        for name in ltr.LOCATE_BIN_HELPERS + (BRIDGE_SCRIPTS["agentbox-claude"][0],):
            out[f"bin/{name}"] = f"scripts/run/{name}"
        return out

    def test_the_base_commits_plan_and_this_one_are_one(self):
        argv = ["--workspace", str(self.root), "plan-locate", "--preset", "locate-inventory",
                "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-z"]
        old = subprocess.run([sys.executable, str(self.base / "scripts" / "cf.py"), *argv],
                             capture_output=True, text=True, cwd=str(self.root.parent))
        self.assertEqual(old.returncode, 0, old.stdout + old.stderr)
        then = self.snapshot("inv-z")
        then_yaml = read_yaml(self.paths.run_yaml("inv-z"))
        code, out, err = ws(self.root, *argv[2:])
        self.assertEqual(code, 0, out + err)
        now = self.snapshot("inv-z")
        now_yaml = read_yaml(self.paths.run_yaml("inv-z"))
        sources = self.staged_from()
        differ = sorted(k for k in then.keys() | now.keys()
                        if k != "run.yaml" and then.get(k) != now.get(k))
        allowed = {f"tend-root/{staged}" for staged, rel in sources.items()
                   if rel in self.changed}
        self.assertIn("tend-root/bin/audit_turn.py", allowed)
        # the worker prompt gains the kind sentence after the kinds, and only that
        kinds = b"(`passage_kind`; `other` when it states something that is none of these)."
        worker = ("prompts/worker.md", "tend-root/prompts/worker.md")
        for name in worker:
            with self.subTest(prompt=name):
                self.assertEqual(then[name].count(kinds), 1)
                self.assertEqual(now[name], then[name].replace(
                    kinds, kinds + b" " + TS.KIND_SENTENCE.encode("utf-8")))
        self.assertEqual(sorted(set(differ) & set(worker)), sorted(worker))
        # the gate.json files name statement-cut rule 3 for 2, and only that
        gates = ("gate.json", "gate.local.json", "tend-root/gate/gate.json")
        rule = b'"statement_cut_rule": '
        for name in gates:
            with self.subTest(gate=name):
                was = rule + str(vocab.STATEMENT_CUT_CONTINUED).encode()
                self.assertEqual(then[name].count(was), 1)
                self.assertEqual(now[name], then[name].replace(
                    was, rule + str(vocab.STATEMENT_CUT_WIDE).encode()))
                self.assertEqual(json.loads(now[name])["statement_cut_rule"],
                                 vocab.STATEMENT_CUT_RULE)
        self.assertEqual(sorted(set(differ) & set(gates)), sorted(gates))
        differ = [k for k in differ if k not in worker + gates]
        self.assertLessEqual(set(differ), allowed)
        self.assertNotIn("tend-root/bin/audit_turn.py", then)
        # the branch's gate and bridge changes are seen, and nothing of the audit's gate
        for name in ("tend-root/bin/audit_turn.py", "tend-root/gate/verify_locate.py",
                     "tend-root/gate/locate_text.py", "tend-root/bin/bridge-agentbox.sh"):
            self.assertIn(name, differ)
        self.assertFalse([n for n in now if "inventory_" in n])
        # what a run is launched with and told is untouched
        for name in sorted(then):
            if name in worker + gates:
                continue
            if name.startswith(("prompts/", "tend-root/prompts/", "tasks/")) or name in (
                    "gate.json", "gate.local.json", "tend-root/config.yaml",
                    "tend-root/bin/worker-agent.sh", "tend-root/bin/reviewer-agent.sh",
                    "tend-root/gate/gate.json"):
                self.assertEqual(now[name], then[name], name)
        self.assertTrue(any(n.startswith("prompts/") for n in then))
        # run.yaml: the commit, and the pins of exactly those files
        self.assertIsNotNone(now_yaml["locate"].pop("skill_commit"))
        then_yaml["locate"].pop("skill_commit")
        moved = {f"tend-root/{k}" for k in now_yaml["locate"]["gate_files"]
                 if then_yaml["locate"]["gate_files"].get(k)
                 != now_yaml["locate"]["gate_files"][k]}
        self.assertEqual(moved, set(differ) | {"tend-root/gate/gate.json"})
        # the worker prompt's shas: the rendered prompt's and its template's
        self.assertEqual(now_yaml["prompts"].pop("worker.md"),
                         hashlib.sha256(now["prompts/worker.md"]).hexdigest())
        then_yaml["prompts"].pop("worker.md")
        template = "locate-inventory.md.tmpl"
        self.assertEqual(now_yaml["locate"]["config_shas"]["prompts"].pop(template),
                         hashing.sha256_file(SKILL / "templates" / "prompts" / template))
        then_yaml["locate"]["config_shas"]["prompts"].pop(template)
        for block in (then_yaml["locate"], now_yaml["locate"]):
            block.pop("gate_files")
            block["config_shas"].pop("gate_files")
        self.assertEqual(now_yaml, then_yaml)


if __name__ == "__main__":
    unittest.main()
