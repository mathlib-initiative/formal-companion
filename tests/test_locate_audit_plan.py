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
                         {"rule": vocab.INVENTORY_AUDIT_RULE, "review_rounds": 3})
        prompts = block["config_shas"]["prompts"]
        tdir = SKILL / "templates" / "prompts"
        for name in AUDIT_TEMPLATE_NAMES:
            self.assertEqual(prompts[name], hashing.sha256_file(tdir / name), name)
        # the audit's reviewer prompts replace the form reviewer's
        self.assertNotIn("locate-reviewer.md.tmpl", prompts)
        self.assertNotIn("locate-reviewer-system.md.tmpl", prompts)
        self.assertIn("locate-inventory.md.tmpl", prompts)
        self.assertIn(f"audit    : rule {vocab.INVENTORY_AUDIT_RULE}, at most 3 review rounds",
                      self.out)
        plain = read_yaml(self.paths.run_yaml("inv-1"))["locate"]
        self.assertNotIn("audit", plain)
        self.assertNotIn("audit", plain["config_shas"]["agents"])

    def test_two_rounds_are_recorded_as_two(self):
        code, out, err = self.plan("inv-2", "--audit-rounds", "2")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read_yaml(self.paths.run_yaml("inv-2"))["locate"]["audit"],
                         {"rule": vocab.INVENTORY_AUDIT_RULE, "review_rounds": 2})
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
        rule = f"audited under audit rule {vocab.INVENTORY_AUDIT_RULE} in"
        for run_id, extra, said in (("inv-a", (), f"{rule} 3 review rounds, not unaudited"),
                                    ("inv-a", ("--audit-rounds", "2"),
                                     f"{rule} 3 review rounds, not {rule} 2"),
                                    ("inv-1", ("--audit-rounds", "3"),
                                     f"unaudited, not {rule} 3 review rounds")):
            with self.subTest(run_id=run_id, extra=extra):
                code, out, err = self.plan(run_id, *extra)
                self.assertNotEqual(code, 0)
                self.assertIn(said, out + err)
        self.assertEqual(snapshot("inv-a"), before)
        # a run planned under the other audit rule (before the skip audit's flip, or after
        # it) keeps it: a re-plan with the same rounds is refused, and says both rules
        other = 3 - vocab.INVENTORY_AUDIT_RULE
        with mock.patch.object(vocab, "INVENTORY_AUDIT_RULE", other):
            code, out, err = self.plan("inv-r", "--audit-rounds", "3")
            self.assertEqual(code, 0, out + err)
        code, out, err = self.plan("inv-r", "--audit-rounds", "3")
        self.assertNotEqual(code, 0)
        self.assertIn(f"was planned audited under audit rule {other} in 3 review rounds, not "
                      f"{rule} 3 review rounds", out + err)
        self.assertIn("keeps a run's audit, its rule and its rounds", out + err)

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

    #: each rule's clauses a verdict can turn on, as section 3.4 says them: the card line
    #: carries every one
    KEY_CLAUSES = {
        1: ("one Lean declaration could state it whole", "even when a library states it in two"),
        2: ("only inside a theorem box", "the paragraph that finishes that sentence after them",
            "over the items of one list of a compound statement"),
        3: ("its end-to-end equation", "shape `equations`"),
        4: ("between a hypothesis and its conclusion", "inside an iff",
            "between conjuncts about one subject"),
        5: ("one passage per item", "a display inside a list item belongs to that item"),
        6: ("judged a sentence at a time", "never a conjunct of the example"),
        7: ("no `formal_math` passage of its own", "is a `notation` passage",
            "`statement` carries the hypotheses and objects", "so it reads on its own"),
        8: ("Anything else", "is two statements"),
        9: ("would stay true whatever the result concluded", "at most one passage",
            "a theorem stated only in parts takes the passage that states its first part",
            "a later restatement is `central`"),
        10: ("no neighbouring passage already states", "no passage and no clause"),
    }

    def test_each_card_rule_keeps_the_clauses_a_verdict_turns_on(self):
        system = self.prompts()["reviewer-system.md"]
        section = " ".join(section_34().split())
        cards = dict(re.findall(r"(?m)^- \*\*C(\d+) [^*]+?\*\* (.*)$", system))
        for n, clauses in self.KEY_CLAUSES.items():
            card = cards[str(n)]
            for clause in clauses:
                with self.subTest(rule=n, clause=clause):
                    self.assertIn(clause.lower(), card.lower())
                    self.assertIn(clause.lower(), section.lower())
        # the verdict that rests on rule 7's last sentence names it
        self.assertRegex(self.prompts()["reviewer.md"],
                         r"`statement_misstates` when [^;]*read on its own \(C7\)")

    def test_what_the_gate_allows_is_the_plans_statement_cut_rule(self):
        system = self.prompts()["reviewer-system.md"]
        rule = lplan.rule_versions()["statement_cut_rule"]
        self.assertIn(f"**What the gate allows.** {lprompts.gate_bound(rule)}", system)
        self.assertIn(f"(statement-cut rule {rule})", system)
        gate = json.loads((self.gate_dir() / "gate.json").read_text())
        self.assertEqual(gate["statement_cut_rule"], rule)
        wide = lprompts.gate_bound(vocab.STATEMENT_CUT_WIDE)
        for words in ("footnote mark between the blocks", "`dd` or paragraph whose first word",
                      "a formula followed by a lower-case word",
                      "text-only `dd` that starts with a capital", '"Then"',
                      "never from a `dt` term or a quotation"):
            self.assertIn(words, wide)
            self.assertNotIn(words, lprompts.gate_bound(vocab.STATEMENT_CUT_CONTINUED))
        # the card names no refusal the gate does not make: the wide bound admits a
        # text-only `dd` with a lower-case first word, so the card qualifies its refusal
        self.assertNotRegex(wide, r"text-only `dd`(,| stays| stay)")
        self.assertNotIn("before its display", wide)
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

    def test_the_revision_says_how_a_revert_verdict_is_applied(self):
        """A middle round can send the merge's ``revert`` verdicts (rule 0, origin
        ``collateral``) to the worker: the revision names them and the collateral file the
        turn stages beside the verdicts."""
        revision = self.prompts()["worker-revision.md"]
        self.assertIn(f"A `{vocab.AUDIT_VERDICT_REVERT}` verdict** (rule 0, origin `collateral`)",
                      revision)
        self.assertIn(".tend/audit/r$CF_AUDIT_ROUND.collateral.json", revision)
        for change in ("hand_edit", "moved"):
            self.assertIn(f"`{change}`", revision)
        for op in ("set", "add", "drop"):
            self.assertIn(f"`{op}`", revision.split("verdict** (rule 0")[1].split("\n")[0])

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
                    rule = vocab.INVENTORY_AUDIT_RULE
                    self.assertEqual(data["schema"], vocab.INVENTORY_AUDIT_SCHEMAS[rule])
                    self.assertEqual(data["kind"], kind)
                    problems: list[str] = []
                    self.assertTrue(ia.check_record(data, kind, kind, problems, rule=rule),
                                    problems)
                    if kind == "ops":
                        self.assertEqual(ia.validate_ops(data, None, rule=rule), [])
                        self.assertEqual({op["op"] for op in data["ops"]},
                                         {"merge", "split", "set"})
                    for row in data.get("passages") or []:
                        bad: list[str] = []
                        ia._verdict_rules(row, "row", bad, items={
                            r["item"]: {} for r in data["passages"]})
                        self.assertEqual(bad, [], row)
                    for row in data.get("skips") or []:
                        bad = []
                        ia._skip_verdict_rules(row, "row", {}, bad)
                        self.assertEqual(bad, [], row)
                    self.assertEqual("skips" in data, kind == "audit" and rule >= 2)
                    self.assertEqual("examined_skips" in data, kind == "skeptic" and rule >= 2)


class AtRule:
    """A mixin: the planner writes audit rule ``RULE`` (``vocab.INVENTORY_AUDIT_RULE``
    patched as a flip sets it), so the tests above run at that rule."""

    RULE = 1

    def setUp(self):
        patcher = mock.patch.object(vocab, "INVENTORY_AUDIT_RULE", self.RULE)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()


class TestTheFlagAtRule1(AtRule, TestTheFlag):
    RULE = 1


class TestTheFlagAtRule2(AtRule, TestTheFlag):
    RULE = 2


class TestTheWrappersAtRule1(AtRule, TestTheWrappers):
    RULE = 1


class TestTheWrappersAtRule2(AtRule, TestTheWrappers):
    RULE = 2


class TestThePromptsAtRule1(AtRule, TestThePrompts):
    RULE = 1


class TestThePromptsAtRule2(AtRule, TestThePrompts):
    RULE = 2


#: what a rule-2 plan's prompts say and a rule-1 plan's never do
SKIP_WORDS = ("skipped_statements", "`unskip", "`skip_edit`", "examined_skips", "missed_skips",
              "skip_ok", "`skip-rule`", "`mostly-skipped`", "--rule 2")


class TestTheSkipPrompts(AtRule, Audited):
    """Audit rule 2's prompts (references/locate-documents.md section 13.11): each says
    what its call reads and writes of the skips, and every command they render names the
    rule; a rule-1 plan's say none of it."""

    RULE = 2

    def test_each_prompt_says_its_part_of_the_skips(self):
        prompts = self.prompts()
        for name, needles in (
                ("reviewer-system.md", ("**Skips (audit rule 2).**", "`skipped_statements`",
                                        "'<skip id>' --rule 2 --document")),
                ("reviewer.md", ('"skips": [', "`skip-rule`", "`mostly-skipped`", "`partial`",
                                 "`furniture`", '"skip_ok": true',
                                 f"at most {vocab.AUDIT_SKIP_STATEMENTS_MAX}",
                                 f"`at` {vocab.AUDIT_SKIP_AT_WORDS[0]} to "
                                 f"{vocab.AUDIT_SKIP_AT_WORDS[1]} words")),
                ("reviewer-skeptic.md", ('"examined_skips": ["r1-s003"]', "INSIDE the skip",
                                         "`missed_skips`", "EVERY skip of your part")),
                ("reviewer-check.md", ("`skip_edit`", "`SKIP VERDICT`", "`SKIPS` section")),
                ("worker-revision.md", ("`unskip {skip, keep?}`", "`unskip`", "`merge`",
                                        "inventory_edit.py --rule 2 --document",
                                        "never a skip", "by weight"))):
            for needle in needles:
                with self.subTest(prompt=name, needle=needle):
                    self.assertIn(needle, prompts[name])

    def test_a_rule_1_plan_says_none_of_it(self):
        with mock.patch.object(vocab, "INVENTORY_AUDIT_RULE", 1):
            code, out, err = self.plan("inv-r1", "--audit-rounds", "3")
        self.assertEqual(code, 0, out + err)
        for name, text in self.prompts("inv-r1").items():
            for needle in SKIP_WORDS:
                with self.subTest(prompt=name, needle=needle):
                    self.assertNotIn(needle, text)
        for needle in ("skipped_statements", "--rule 2"):
            self.assertIn(needle, self.prompts()["worker-revision.md"] +
                          self.prompts()["reviewer.md"])


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
    section 7) and nothing else; and smoke 4's reviewer fixes: the reviewer's rule card,
    which gains check 1's git command and the reading of the gate's ownership line (its
    challenge text renders empty in an inventory), and the reviewer's wrapper, whose git
    is read-only by subcommand.  ``run.yaml`` differs only in its skill commit, those
    files' pins and the two prompts' shas.
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
        # the reviewer's card gains two sentences, and only those
        reviewer = ("prompts/reviewer-system.md", "tend-root/prompts/reviewer-system.md")
        added = (" Read the change set with `git diff --name-only main...HEAD`.",
                 " The gate's last line, `ownership: not checked (no --changed-files: \u2026)`, "
                 "is about the change set, which check 1 has you read with git, not about the "
                 "manifest's passages or records: never add `--changed-files` to the command "
                 "(beside `--findings` the gate refuses it, exit 3).")
        for name in reviewer:
            with self.subTest(prompt=name):
                text = now[name].decode("utf-8")
                for sentence in added:
                    self.assertEqual(text.count(sentence), 1, sentence)
                    text = text.replace(sentence, "")
                self.assertEqual(text, then[name].decode("utf-8"))
        self.assertEqual(sorted(set(differ) & set(reviewer)), sorted(reviewer))
        # the reviewer's wrapper: read-only git by subcommand for Bash(git:*), and only that
        wrapper = "tend-root/bin/reviewer-agent.sh"
        self.assertEqual(then[wrapper].count(b"Bash(git:*)"), 1)
        self.assertEqual(now[wrapper], then[wrapper].replace(
            b"Bash(git:*)", b"Bash(git diff:*),Bash(git show:*),Bash(git log:*),"
                            b"Bash(git status:*)"))
        allowed.add(wrapper)
        differ = [k for k in differ if k not in worker + gates + reviewer]
        self.assertLessEqual(set(differ), allowed)
        self.assertNotIn("tend-root/bin/audit_turn.py", then)
        # the branch's gate and bridge changes are seen, and nothing of the audit's gate
        for name in ("tend-root/bin/audit_turn.py", "tend-root/gate/verify_locate.py",
                     "tend-root/gate/locate_text.py", "tend-root/bin/bridge-agentbox.sh"):
            self.assertIn(name, differ)
        self.assertFalse([n for n in now if "inventory_" in n])
        # what a run is launched with and told is untouched
        for name in sorted(then):
            if name in worker + gates + reviewer + (wrapper,):
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
        # and the reviewer card's, likewise
        self.assertEqual(now_yaml["prompts"].pop("reviewer-system.md"),
                         hashlib.sha256(now["prompts/reviewer-system.md"]).hexdigest())
        then_yaml["prompts"].pop("reviewer-system.md")
        template = "locate-reviewer-system.md.tmpl"
        self.assertEqual(now_yaml["locate"]["config_shas"]["prompts"].pop(template),
                         hashing.sha256_file(SKILL / "templates" / "prompts" / template))
        then_yaml["locate"]["config_shas"]["prompts"].pop(template)
        for block in (then_yaml["locate"], now_yaml["locate"]):
            block.pop("gate_files")
            block["config_shas"].pop("gate_files")
        self.assertEqual(now_yaml, then_yaml)


# --------------------------------------------------------------------------- #
# an audited plan is f523584's but for the rule it names
# --------------------------------------------------------------------------- #

#: the base commit the skip audit branched from
SKIP_BASE = "f523584"
#: the audit's prompts, as a plan renders them (and its tend root copies them)
AUDIT_PROMPTS = ("reviewer-system.md", "reviewer.md", "reviewer-check.md", "reviewer-skeptic.md",
                 "worker-revision.md")


class TestAnAuditedPlanIsTheBaseCommits(unittest.TestCase):
    """f523584's ``cf plan-locate --audit-rounds 3`` and this one's, as the same run.

    Under audit rule 1 every file of the run directory is byte for byte the base
    commit's -- the rendered prompts, the wrappers, the gate.json files, the tasks -- but
    the files staged from a source this branch changed (the three audit tools,
    ``bin/audit_turn.py``, and since smoke 4's reviewer fixes the gate, the bridge and
    ``bin/review_record.py``); ``run.yaml`` differs only in its skill commit, those files'
    pins and the five audit templates' shas.  Under rule 2 the five rendered audit
    prompts (in ``prompts/`` and the tend root's copy), the two wrappers' ``CF_AUDIT_RULE``,
    ``locate.audit.rule`` and ``config_shas.agents.audit.rule`` and the rendered prompts'
    shas differ too, and nothing else.  An unaudited plan stays the base commit's
    (:class:`TestAnUnauditedPlanIsTheBaseCommits`; no file it stages changed here but
    ``bin/audit_turn.py``).
    """

    def setUp(self):
        self.root = scratch(self)
        self.paths = F.campaign(self, self.root)
        tar = subprocess.run(["git", "-C", str(SKILL), "archive", "--format=tar", SKIP_BASE,
                              "scripts", "templates", "vendor", "registry"],
                             capture_output=True)
        if tar.returncode != 0:
            self.skipTest(f"this checkout does not hold commit {SKIP_BASE}")
        self.base = self.root.parent / "base-skips"
        with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as archive:
            archive.extractall(self.base, filter="data") if sys.version_info >= (3, 12) \
                else archive.extractall(self.base)
        changed = subprocess.run(["git", "-C", str(SKILL), "diff", "--name-only", SKIP_BASE,
                                  "--", "scripts", "vendor"], capture_output=True, text=True)
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.changed = set(changed.stdout.split())

    def snapshot(self, run_id: str) -> dict[str, bytes]:
        rd = self.paths.run_dir(run_id)
        return {p.relative_to(rd).as_posix(): p.read_bytes() for p in sorted(rd.rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts}

    def both(self, rule: int) -> tuple[dict, dict, dict, dict]:
        argv = ["--workspace", str(self.root), "plan-locate", "--preset", "locate-inventory",
                "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-q",
                "--audit-rounds", "3"]
        old = subprocess.run([sys.executable, str(self.base / "scripts" / "cf.py"), *argv],
                             capture_output=True, text=True, cwd=str(self.root.parent))
        self.assertEqual(old.returncode, 0, old.stdout + old.stderr)
        then, then_yaml = self.snapshot("inv-q"), read_yaml(self.paths.run_yaml("inv-q"))
        shutil.move(self.paths.run_dir("inv-q"), self.root.parent / f"inv-q-base-{rule}")
        with mock.patch.object(vocab, "INVENTORY_AUDIT_RULE", rule):
            code, out, err = ws(self.root, *argv[2:])
        self.assertEqual(code, 0, out + err)
        return then, self.snapshot("inv-q"), then_yaml, read_yaml(self.paths.run_yaml("inv-q"))

    def staged_changed(self) -> set[str]:
        from plan.make_tend_root import BRIDGE_SCRIPTS
        out = {f"tend-root/gate/{name}" for rel, name in ltr.gate_file_rows(True)
               if rel in self.changed}
        out |= {f"tend-root/bin/{name}" for name in ltr.LOCATE_BIN_HELPERS
                + (BRIDGE_SCRIPTS["agentbox-claude"][0],) if f"scripts/run/{name}" in self.changed}
        return out

    def yaml_left(self, then_yaml: dict, now_yaml: dict) -> tuple[dict, dict]:
        """Both run.yaml files with what may differ popped: the time each was made (two
        fresh plans), the commit, the staged files' pins and the audit templates' shas."""
        for doc in (then_yaml, now_yaml):
            doc.pop("created")
        for block in (then_yaml["locate"], now_yaml["locate"]):
            block.pop("skill_commit")
            block.pop("gate_files")
            block["config_shas"].pop("gate_files")
            for name in AUDIT_TEMPLATE_NAMES:
                block["config_shas"]["prompts"].pop(name)
        return then_yaml, now_yaml

    def test_rule_1_is_the_base_commits_plan(self):
        then, now, then_yaml, now_yaml = self.both(1)
        differ = {k for k in then.keys() | now.keys()
                  if k != "run.yaml" and then.get(k) != now.get(k)}
        self.assertEqual(differ, self.staged_changed())
        # and smoke 4's reviewer fixes: the gate (a challenge's OK line), the bridge and the
        # recorder whose tamper guard the turn machine shares (the audit's prompts and
        # wrappers are untouched)
        self.assertEqual({"tend-root/bin/audit_turn.py", "tend-root/gate/inventory_audit.py",
                          "tend-root/gate/inventory_render.py",
                          "tend-root/gate/inventory_edit.py", "tend-root/gate/verify_locate.py",
                          "tend-root/bin/bridge-agentbox.sh",
                          "tend-root/bin/review_record.py"}, differ)
        for name in AUDIT_PROMPTS:
            self.assertEqual(now[f"prompts/{name}"], then[f"prompts/{name}"], name)
        self.assertEqual(now_yaml["locate"]["audit"], {"rule": 1, "review_rounds": 3})
        moved = {f"tend-root/{k}" for k, v in now_yaml["locate"]["gate_files"].items()
                 if then_yaml["locate"]["gate_files"].get(k) != v}
        self.assertEqual(moved, differ)
        then_yaml, now_yaml = self.yaml_left(then_yaml, now_yaml)
        self.assertEqual(now_yaml, then_yaml)

    def test_rule_2_differs_in_the_rule_and_its_prompts_only(self):
        then, now, then_yaml, now_yaml = self.both(2)
        differ = {k for k in then.keys() | now.keys()
                  if k != "run.yaml" and then.get(k) != now.get(k)}
        prompts = {f"{d}{name}" for d in ("prompts/", "tend-root/prompts/")
                   for name in AUDIT_PROMPTS}
        wrappers = {"tend-root/bin/worker-agent.sh", "tend-root/bin/reviewer-agent.sh"}
        self.assertEqual(differ, self.staged_changed() | prompts | wrappers)
        for name in wrappers:
            self.assertEqual(now[name], then[name].replace(b'export CF_AUDIT_RULE="1"',
                                                           b'export CF_AUDIT_RULE="2"'))
        self.assertEqual(now_yaml["locate"]["audit"], {"rule": 2, "review_rounds": 3})
        self.assertEqual(now_yaml["locate"]["config_shas"]["agents"]["audit"]["rule"], 2)
        for block in (then_yaml["locate"], now_yaml["locate"]):
            block["audit"].pop("rule")
            block["config_shas"]["agents"]["audit"].pop("rule")
        for name in AUDIT_PROMPTS:
            self.assertEqual(now_yaml["prompts"].pop(name),
                             hashlib.sha256(now[f"prompts/{name}"]).hexdigest())
            then_yaml["prompts"].pop(name)
        then_yaml, now_yaml = self.yaml_left(then_yaml, now_yaml)
        self.assertEqual(now_yaml, then_yaml)


if __name__ == "__main__":
    unittest.main()
