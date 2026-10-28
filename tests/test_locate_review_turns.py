"""Smoke 4's reviewer fixes: the challenge reviewer's rule card, the gate's OK line, the
challenge worker's rule, the locate revision, the needs_review ledger, the reviewer's
read-only git and tamper guard, and the harvest's check that a merged document is its
worker's.

New code, for references/locate-documents.md sections 4, 9 and 10.  Every case is
offline: the prompts are rendered from a synthetic workspace, the gate runs on the
made-up "Widget theory" fixture, the bridge runs against a FAKE ``agentbox`` that records
its argv and does what the case asks in the worktree, and the harvest fetches from a
'host' that is a local directory through fake ``ssh`` and ``rsync``.  No agent is called.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
for _p in (str(HERE), str(SCRIPTS), str(SCRIPTS / "run")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_harvest_recursive as thr  # noqa: E402
import test_locate_gate_statement_cut as tsc  # noqa: E402
import test_locate_judgment as tj  # noqa: E402
import test_verify_locate as tvl  # noqa: E402
from test_locate_gate_tier_a import permits  # noqa: E402
from cflib import vocab  # noqa: E402
from gate import verify_locate as vl  # noqa: E402
from harvest import host_artifacts as host_mod  # noqa: E402
from harvest import turn_records  # noqa: E402
from locate import presets as lp  # noqa: E402
from locate import tend_root  # noqa: E402
from run import review_record  # noqa: E402

BRIDGE = SCRIPTS / "run" / "bridge-agentbox.sh"
CLAUDE_P = SCRIPTS / "run" / "bridge-claude-p.sh"
LOCATE_GATE = "python3 /srv/cf/r-gate/verify_locate.py --gate-config /srv/cf/r-gate/gate.json"
FINDINGS_GATE = "python3 /srv/cf/r-gate/verify_findings.py --gate-config /srv/cf/r-gate/gate.json"
TASK = "chl-0001"
DECLINE = "do not change a verdict or a judgment to make the request satisfiable"
COMPLY = "FIX EXACTLY WHAT IT NAMES"


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-review-turns-")).resolve()
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CF_", "TEND_", "GIT_CONFIG_", "FAKE_"))}
    env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# P1 and P3: the prompts
# --------------------------------------------------------------------------- #

class TestTheRuleCards(unittest.TestCase):
    """The challenge reviewer's card says the conditional rule, that the gate counts the
    records and what its ownership line is about; the challenge worker never overturns a
    prominence to comply.  The locate and inventory cards keep their check 2."""

    @classmethod
    def setUpClass(cls):
        cls.rendered = tj.render_locate_prompts()

    def card(self, mode: str) -> str:
        return " ".join(self.rendered[mode]["reviewer-system.md"].split())

    def test_the_challenge_card_says_the_conditional_rule(self):
        card = self.card(vocab.LOCATE_CHALLENGE)
        for needle in ('A record marked `conditional: "prominence"` is owed only when the same '
                       "document overturns its passage's prominence to `subject` or `central`",
                       "Never ask for a verdict on a conditional record, or for a prominence "
                       "overturn that would make one owed",
                       "after an exit 0 do not request changes on a count of your own",
                       "This run is the challenge: no later task re-judges the verdicts you "
                       "review"):
            self.assertIn(needle, card)
        self.assertNotIn("decided later, by separate challenge tasks", card)
        self.assertNotIn("2. Every passage or record of the manifest is answered", card)

    def test_the_locate_and_inventory_cards_keep_their_check(self):
        for mode in (vocab.LOCATE, vocab.LOCATE_INVENTORY):
            with self.subTest(mode=mode):
                card = self.card(mode)
                self.assertIn("2. Every passage or record of the manifest is answered, once, "
                              "and nothing else is.", card)
                self.assertIn("decided later, by separate challenge tasks", card)
                self.assertNotIn("conditional", card)
                self.assertNotIn("This run is the challenge", card)

    def test_every_card_reads_the_ownership_line_as_the_change_set(self):
        for mode in (vocab.LOCATE, vocab.LOCATE_CHALLENGE, vocab.LOCATE_INVENTORY):
            with self.subTest(mode=mode):
                raw = self.rendered[mode]["reviewer-system.md"]
                # the four checks are four lines, whichever check 2 the mode renders
                self.assertEqual(len(re.findall(r"(?m)^[1-4]\. ", raw)), 4, raw)
                self.assertNotIn("{{", raw)
                card = self.card(mode)
                self.assertIn("`git diff --name-only main...HEAD`", card)
                self.assertIn("is about the change set", card)
                self.assertIn("never add `--changed-files` to the command", card)
                # and every git command it names is on the reviewer's read-only list
                git = tend_root.reviewer_git()
                for cmd in re.findall(r"`(git [^`]+)`", card):
                    self.assertTrue(permits(git, cmd), cmd)

    def test_every_gate_message_the_card_quotes_is_the_gates(self):
        """A drift guard: the card quotes the gate's own words, so they must stay its own."""
        card = self.card(vocab.LOCATE_CHALLENGE)
        quoted = re.findall(r'\("([^"]+)"\)', card)
        self.assertGreaterEqual(len(quoted), 6, quoted)
        gate = (SCRIPTS / "gate" / "verify_locate.py").read_text(encoding="utf-8")
        for words in quoted:
            with self.subTest(words=words):
                self.assertIn(words.replace("each of the N ", ""), gate)
        self.assertIn("ownership: not checked (no --changed-files: these documents", gate)

    def test_the_challenge_worker_never_overturns_to_comply(self):
        worker = " ".join(self.rendered[vocab.LOCATE_CHALLENGE]["worker.md"].split())
        self.assertIn("a request for a verdict on a conditional record, a reviewer's included, "
                      "is never a reason to overturn it", worker)
        self.assertIn("quote this rule in your summary, and finish with status `needs_review`",
                      worker)
        for mode in (vocab.LOCATE, vocab.LOCATE_INVENTORY):
            self.assertNotIn("is never a reason to overturn it",
                             self.rendered[mode]["worker.md"])


# --------------------------------------------------------------------------- #
# P2: the gate's OK line
# --------------------------------------------------------------------------- #

class TestTheOkLineCountsTheRecords(tsc.ChallengeCase):
    """test_a3's task (a sampled supporting prominence with a conditional absence), run
    through the gate's command line: its OK line says the task's records were counted."""

    def conditional(self) -> tuple[dict, dict]:
        prominence = {"item": tvl.LEAD_ITEM, "target": "prominence", "span": tvl.LEAD_SPAN,
                      "passage_kind": "theorem", "outcome": "absent", "prominence": "subject"}
        self.records(prominence)
        path = self.fx.tasks / f"{self.TASK}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        records = doc["documents"][0]["records"]
        records[3]["conditional"] = "prominence"
        records[4]["prominence"] = "supporting"
        path.write_text(json.dumps(doc), encoding="utf-8")
        body = self.body()
        absence = body["verdicts"].pop(3)
        body["verdicts"].append({"item": tvl.LEAD_ITEM, "target": "prominence",
                                 "action": "uphold", "witness": "supporting it is"})
        return body, absence

    def cli(self, body: dict) -> tuple[int, str, str]:
        self.fx.gate["judgment_rules"] = vocab.RULES_STATEMENT_CUT
        config = self.fx.write_gate()
        doc = self.fx.document(self.TASK, body)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = vl._main(["--gate-config", str(config), "--root", str(self.fx.root),
                             "--findings", str(doc)])
        return code, out.getvalue(), err.getvalue()

    def test_an_upheld_prominence_leaves_its_conditional_record_unowed(self):
        body, _absence = self.conditional()
        code, out, err = self.cli(body)
        self.assertEqual(code, 0, err)
        self.assertIn("; each of the 4 record(s) the task owes has one verdict, and 1 "
                      "conditional record(s) are not owed (no prominence overturn to subject or "
                      "central)", out)
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("ownership: not checked"))
        (stats,) = self.gate(body, vocab.RULES_STATEMENT_CUT)["stats"]
        self.assertEqual((stats["records_owed"], stats["conditional_not_owed"]), (4, 1))

    def test_an_overturn_owes_it_and_the_line_says_so(self):
        body, absence = self.conditional()
        body["verdicts"][-1] = {"item": tvl.LEAD_ITEM, "target": "prominence",
                                "action": "overturn", "prominence": "central",
                                "witness": "the article's centre"}
        body["verdicts"].append(absence)
        code, out, err = self.cli(body)
        self.assertEqual(code, 0, err)
        self.assertIn("; each of the 5 record(s) the task owes has one verdict\n", out)
        self.assertNotIn("conditional record(s) are not owed", out)
        # left out, the owed record is the refusal the card quotes
        body["verdicts"].pop()
        code, out, err = self.cli(body)
        self.assertEqual(code, 1)
        self.assertIn("record(s) of this task have no verdict", err)
        self.assertNotIn("the task owes", out)

    def test_only_a_challenge_says_it(self):
        stats = [{"records_owed": 3, "conditional_not_owed": 0}]
        for mode in (vocab.LOCATE, vocab.LOCATE_INVENTORY):
            self.assertEqual(vl.coverage_line(types.SimpleNamespace(mode=mode), stats), "")
        cfg = types.SimpleNamespace(mode=vocab.LOCATE_CHALLENGE)
        self.assertEqual(vl.coverage_line(cfg, stats),
                         "; each of the 3 record(s) the task owes has one verdict")
        # an unresolved challenge counts no records
        self.assertEqual(vl.coverage_line(cfg, [{"verdicts": 0, "unresolved": True}]), "")


# --------------------------------------------------------------------------- #
# P6: the reviewer's git
# --------------------------------------------------------------------------- #

class ReadOnlyGit:
    """What a locate reviewer's allowlist admits and refuses."""

    ADMITTED = ("git diff --name-only main...HEAD", "git status --porcelain", "git log -1",
                "git show HEAD:findings/chl-0001/enwiki-1001-2002.json")
    REFUSED = ("git checkout -- findings/x.json", "git commit -m x", "git reset --hard",
               "git stash", "git config core.hooksPath /tmp/x", "git -c core.hooksPath=/x diff",
               "git add -A", "python3 /srv/cf/r-gate/verify_locate.py --gate-config "
               "/srv/cf/r-gate/gate.json --changed-files")

    def check(self, tools: list[str], gate: str) -> None:
        for cmd in self.ADMITTED + (f"{gate} --findings findings/{TASK}/s.json",):
            self.assertTrue(permits(tools, cmd), cmd)
        for cmd in self.REFUSED:
            self.assertFalse(permits(tools, cmd), cmd)
        self.assertFalse([t for t in tools if t in ("Edit", "Write", "Bash(git:*)")])


class TestTheReviewersGitIsReadOnly(ReadOnlyGit, unittest.TestCase):

    def test_the_planned_unaudited_reviewer(self):
        env = tend_root.wrapper_env(lp.get(vocab.LOCATE_CHALLENGE), "/srv/cf/r-gate",
                                    "/srv/cf/tools.sqlite")
        tools = env["reviewer"]["CF_REVIEWER_TOOLS"].split(",")
        self.check(tools, LOCATE_GATE)
        # the audited reviewer's git, entry for entry
        audited = tend_root.audit_tools("/srv/cf/r-gate", LOCATE_GATE)["reviewer"]
        self.assertEqual([t for t in tools if t.startswith("Bash(git ")],
                         [t for t in audited if t.startswith("Bash(git ")])



# --------------------------------------------------------------------------- #
# the bridge: P4, P5 and P7, against a fake agentbox
# --------------------------------------------------------------------------- #

FAKE_AGENTBOX = r'''#!/usr/bin/env python3
"""A fake agentbox: records its argv as one JSON line, runs $FAKE_ACT in the worktree."""
import json, os, subprocess, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_ARGV"], "a") as fh:
    fh.write(json.dumps(argv) + "\n")
act = os.environ.get("FAKE_ACT", "")
if act:
    subprocess.run(["bash", "-c", act], check=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done",
                  "total_cost_usd": 0.1}))
'''

WORKER_DONE = ('mkdir -p .tend && printf \'{"schema_version": 1, "status": "%s", "summary": '
               '"%s", "files_changed": []}\' > .tend/agent_result.json')
REVIEWER_SAYS = ('mkdir -p .tend && printf \'{"schema_version": 1, "verdict": "%s", '
                 '"notes": "%s", "feedback_text": "%s"}\' > .tend/agent_result.json')


class BridgeHarness(unittest.TestCase):
    """bridge-agentbox.sh in a temp tend root, on a git worktree holding the task's
    committed document, with a fake agentbox on PATH."""

    def setUp(self):
        self.tmp = scratch(self)
        self.root = self.tmp / "tend-root"
        prompts = self.root / "prompts"
        prompts.mkdir(parents=True)
        for role in ("worker", "reviewer"):
            (prompts / f"{role}-system.md").write_text("system\n", encoding="utf-8")
            (prompts / f"{role}.md").write_text(f"{role} task $TEND_TASK_ID\n",
                                                encoding="utf-8")
        self.bin = self.tmp / "fakebin"
        self.bin.mkdir()
        (self.bin / "agentbox").write_text(FAKE_AGENTBOX, encoding="utf-8")
        (self.bin / "agentbox").chmod(0o755)
        self.argv_log = self.tmp / "argv.jsonl"
        self.wt = self.tmp / "worktree"
        self.wt.mkdir()
        tvl.git(self.wt, "init", "-q", "-b", "main")
        (self.wt / ".git" / "info" / "exclude").write_text(".tend/\n", encoding="utf-8")
        (self.wt / "tasks").mkdir()
        (self.wt / "tasks" / f"{TASK}.yaml").write_text(f"id: {TASK}\nstatus: open\n",
                                                        encoding="utf-8")
        tvl.git(self.wt, "add", "-A")
        tvl.git(self.wt, "commit", "-qm", "tasks")
        tvl.git(self.wt, "checkout", "-q", "-b", TASK)
        self.doc = self.wt / "findings" / TASK / f"{tvl.SID}.json"
        self.doc.parent.mkdir(parents=True)
        self.doc.write_text('{"verdicts": []}\n', encoding="utf-8")
        tvl.git(self.wt, "add", "-A")
        tvl.git(self.wt, "commit", "-qm", "the worker's document")
        self.worker_head = tvl.git(self.wt, "rev-parse", "HEAD").strip()

    def run_bridge(self, role: str, *, gate: str = LOCATE_GATE, act: str = "",
                   script: Path = BRIDGE, **env) -> subprocess.CompletedProcess:
        environ = clean_env(PATH=f"{self.bin}:{os.environ['PATH']}",
                            CF_BRIDGE_ROOT=str(self.root), CF_AGENTBOX_PROFILE="examplebox",
                            CF_SLOT_DIR=str(self.tmp / "slots"), CF_GATE_CMD=gate,
                            TEND_TASK_ID=TASK, TEND_WORKTREE_ID="wt-1",
                            FAKE_ARGV=str(self.argv_log), FAKE_ACT=act,
                            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
        environ.update(env)
        # macOS's /bin/bash (3.2) where there is one, the oldest shell a bridge meets
        shell = "/bin/bash" if Path("/bin/bash").exists() else "bash"
        proc = subprocess.run([shell, str(script), role], capture_output=True, text=True,
                              cwd=str(self.wt), env=environ, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1, proc.stdout)
        return proc

    def argv(self) -> list[dict]:
        """Each call's ``{prompt, tools, argv}``."""
        out = []
        for line in self.argv_log.read_text(encoding="utf-8").splitlines():
            argv = json.loads(line)
            out.append({"argv": argv, "prompt": argv[argv.index("-p") + 1],
                        "tools": argv[argv.index("--allowedTools") + 1].split(",")})
        return out

    def contracts(self, role: str) -> list[dict]:
        return review_record.contracts(self.root, TASK, role)

    def ledger(self, name: str) -> list[dict]:
        path = self.root / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def discuss(self, text: str) -> None:
        (self.wt / ".tend").mkdir(exist_ok=True)
        (self.wt / ".tend" / "discussion.md").write_text(text, encoding="utf-8")


class TestTheBridgesReviewerGit(ReadOnlyGit, BridgeHarness):

    def test_the_bridges_default_on_a_locate_gate_only(self):
        self.run_bridge("reviewer", gate=LOCATE_GATE, act=REVIEWER_SAYS % ("approve", "ok", ""))
        self.check(self.argv()[-1]["tools"], LOCATE_GATE)
        self.run_bridge("reviewer", gate=FINDINGS_GATE, act=REVIEWER_SAYS % ("approve", "ok", ""))
        self.assertIn("Bash(git:*)", self.argv()[-1]["tools"])


class TestTheBridgesParse(unittest.TestCase):

    def test_under_every_bash_on_this_machine(self):
        """macOS's /bin/bash is 3.2, which refused a heredoc inside ``$(...)`` holding an
        apostrophe; the suite's ``bash`` may be a newer one that parses it."""
        shells = [b for b in ("/bin/bash", shutil.which("bash")) if b and Path(b).exists()]
        self.assertTrue(shells)
        for shell in dict.fromkeys(shells):
            for script in (BRIDGE, CLAUDE_P):
                with self.subTest(shell=shell, script=script.name):
                    proc = subprocess.run([shell, "-n", str(script)], capture_output=True,
                                          text=True)
                    self.assertEqual(proc.returncode, 0, proc.stderr)


class TestTheRevision(BridgeHarness):
    """P4: a locate worker may decline feedback its rules forbid; the faithfulness
    pipeline's revision is what it was."""

    def test_a_locate_revision_declines_what_the_rules_forbid(self):
        self.discuss("reviewer: give the conditional record a verdict\n")
        self.run_bridge("worker", act=WORKER_DONE % ("completed", "s"))
        prompt = self.argv()[-1]["prompt"]
        self.assertIn("## THIS IS A REVISION", prompt)
        self.assertIn(DECLINE, prompt)
        self.assertIn("finish with status `needs_review`", prompt)
        self.assertIn("reviewer: give the conditional record a verdict", prompt)
        self.assertNotIn(COMPLY, prompt)
        self.assertNotIn("re-litigate", prompt)

    def test_a_faithfulness_revision_is_unchanged(self):
        self.discuss("reviewer: the witness is thin\n")
        self.run_bridge("worker", gate=FINDINGS_GATE, act=WORKER_DONE % ("completed", "s"))
        prompt = self.argv()[-1]["prompt"]
        self.assertIn(COMPLY + ". Do not restart from scratch and do\nnot re-litigate the "
                      "reviewer's judgement", prompt)
        self.assertNotIn(DECLINE, prompt)
        self.assertFalse((self.root / "contracts").exists())

    def test_the_claude_p_bridge_says_the_same(self):
        fake = self.bin / "claude"
        fake.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                        "open(os.environ['FAKE_ARGV'], 'a').write(json.dumps(sys.argv[1:]) + "
                        "'\\n')\nprint(json.dumps({'session_id': 's', 'result': json.dumps("
                        "{'schema_version': 1, 'status': 'completed', 'summary': 'ok'})}))\n",
                        encoding="utf-8")
        fake.chmod(0o755)
        self.discuss("reviewer: the receipt is thin\n")
        for gate, want, refuse in ((LOCATE_GATE, DECLINE, COMPLY),
                                   (FINDINGS_GATE, "Fix exactly what the feedback names; re-run",
                                    DECLINE)):
            with self.subTest(gate=gate):
                self.run_bridge("worker", gate=gate, script=CLAUDE_P,
                                TEND_AGENT_SESSION_DIR=str(self.tmp / "session"),
                                TEND_AGENT_RESUME="1")
                argv = json.loads(self.argv_log.read_text().splitlines()[-1])
                prompt = " ".join(argv[len(argv) - 1 - argv[::-1].index("-p") + 1].split())
                self.assertIn(want, prompt)
                self.assertNotIn(refuse, prompt)


class TestTheNeedsReviewLedger(BridgeHarness):
    """P5: a worker's needs_review reaches the reviewer's prompt and the ledger, and every
    call leaves its contract, with the blobs of the task's documents at HEAD."""

    def test_a_needs_review_is_recorded_and_shown_to_the_reviewer(self):
        self.run_bridge("worker", act=WORKER_DONE % ("needs_review",
                                                     "the reviewer asked for a forbidden verdict"))
        (worker,) = self.contracts("worker")
        blob = tvl.git(self.wt, "rev-parse", f"HEAD:findings/{TASK}/{tvl.SID}.json").strip()
        self.assertEqual((worker["status"], worker["head"], worker["worktree_id"]),
                         ("needs_review", self.worker_head, "wt-1"))
        self.assertEqual(worker["documents"], {f"findings/{TASK}/{tvl.SID}.json": blob})
        self.assertEqual(blob, turn_records.git_blob_id(self.doc.read_bytes()))
        proc = self.run_bridge("reviewer", act=REVIEWER_SAYS % ("approve", "form is fine", ""))
        self.assertEqual(json.loads(proc.stdout)["verdict"], "approve")   # it still merges
        prompt = self.argv()[-1]["prompt"]
        self.assertTrue(prompt.startswith(f"reviewer task {TASK}\n\n"
                                          f"{review_record.BLOCK_HEADING}\n"), prompt)
        self.assertIn("the reviewer asked for a forbidden verdict", prompt)
        self.assertIn("A needs_review is not grounds to reject", prompt)
        (line,) = self.ledger("needs-review.jsonl")
        self.assertEqual({k: line[k] for k in ("task_id", "worktree_id", "worker_head",
                                               "reviewer_verdict", "reviewer_notes")},
                         {"task_id": TASK, "worktree_id": "wt-1", "worker_head": self.worker_head,
                          "reviewer_verdict": "approve", "reviewer_notes": "form is fine"})
        (reviewer,) = self.contracts("reviewer")
        self.assertEqual((reviewer["verdict"], reviewer["tampered"]), ("approve", False))

    def test_a_completed_worker_adds_nothing(self):
        self.run_bridge("worker", act=WORKER_DONE % ("completed", "done"))
        self.run_bridge("reviewer", act=REVIEWER_SAYS % ("approve", "ok", ""))
        self.assertNotIn(review_record.BLOCK_HEADING, self.argv()[-1]["prompt"])
        self.assertEqual(self.ledger("needs-review.jsonl"), [])
        self.assertEqual(len(self.contracts("worker")), 1)

    def test_a_needs_review_the_normaliser_made_is_recorded_too(self):
        """A completed status from a call that failed is needs_review (normalize_result)."""
        failing = self.bin / "agentbox"
        failing.write_text(FAKE_AGENTBOX.replace('"subtype": "success", "is_error": False',
                                                 '"subtype": "error_max_turns", "is_error": True'),
                           encoding="utf-8")
        proc = self.run_bridge("worker", act=WORKER_DONE % ("completed", "ran out of turns"))
        self.assertEqual(json.loads(proc.stdout)["status"], "needs_review")
        (failing).write_text(FAKE_AGENTBOX, encoding="utf-8")
        self.run_bridge("reviewer", act=REVIEWER_SAYS % ("request_changes", "n", "finish it"))
        (line,) = self.ledger("needs-review.jsonl")
        self.assertEqual(line["reviewer_verdict"], "request_changes")

    def test_another_worktrees_needs_review_is_not_this_ones(self):
        self.run_bridge("worker", act=WORKER_DONE % ("needs_review", "old attempt"))
        self.run_bridge("reviewer", act=REVIEWER_SAYS % ("approve", "ok", ""),
                        TEND_WORKTREE_ID="wt-2")
        self.assertNotIn(review_record.BLOCK_HEADING, self.argv()[-1]["prompt"])
        self.assertEqual(self.ledger("needs-review.jsonl"), [])

    def test_an_audited_or_faithfulness_turn_keeps_no_record(self):
        self.run_bridge("worker", gate=FINDINGS_GATE, act=WORKER_DONE % ("needs_review", "s"))
        self.run_bridge("reviewer", gate=FINDINGS_GATE, act=REVIEWER_SAYS % ("approve", "ok", ""))
        self.assertFalse((self.root / "contracts").exists())
        self.assertNotIn(review_record.BLOCK_HEADING, self.argv()[-1]["prompt"])


class TestTheTamperGuard(BridgeHarness):
    """P7: whatever a reviewer call changes outside .tend/ is undone, and its verdict kept."""

    TAMPER = ('printf \'{"verdicts": ["mine"]}\\n\' > findings/' + TASK + '/' + tvl.SID
              + '.json && git add -A && git commit -qm "the reviewer\'s" && '
              'printf "x\\n" > stray.txt && printf "y\\n" >> tasks/' + TASK + '.yaml && ')

    def test_a_reviewers_commit_edit_and_stray_file_are_undone(self):
        before = self.doc.read_bytes()
        proc = self.run_bridge("reviewer", act=self.TAMPER + REVIEWER_SAYS % ("approve", "ok",
                                                                               ""))
        self.assertEqual(json.loads(proc.stdout)["verdict"], "approve")    # the verdict stands
        self.assertEqual(tvl.git(self.wt, "rev-parse", "HEAD").strip(), self.worker_head)
        self.assertEqual(self.doc.read_bytes(), before)
        self.assertFalse((self.wt / "stray.txt").exists())
        self.assertEqual(tvl.git(self.wt, "status", "--porcelain"), "")
        self.assertTrue((self.wt / ".tend" / "agent_result.json").is_file())
        (row,) = self.ledger("tamper.jsonl")
        self.assertEqual((row["task_id"], row["head"]), (TASK, self.worker_head))
        self.assertTrue(any(c.startswith("HEAD ") for c in row["changed"]), row)
        self.assertIn("untracked: stray.txt", row["changed"])
        self.assertIn("reviewer_tampered", (self.root / "agentbox-reviewer.err").read_text())
        (reviewer,) = self.contracts("reviewer")
        self.assertTrue(reviewer["tampered"])
        self.assertEqual(reviewer["head"], self.worker_head)

    def test_a_clean_reviewer_is_left_alone(self):
        (self.wt / ".tend").mkdir(exist_ok=True)
        (self.wt / ".tend" / "scratch-check.py").write_text("print(1)\n", encoding="utf-8")
        self.run_bridge("reviewer", act="mkdir -p .tend && echo note > .tend/notes.txt && "
                                        + REVIEWER_SAYS % ("approve", "ok", ""))
        self.assertEqual(self.ledger("tamper.jsonl"), [])
        self.assertTrue((self.wt / ".tend" / "notes.txt").is_file())
        err = self.root / "agentbox-reviewer.err"
        self.assertNotIn("reviewer_tampered", err.read_text() if err.exists() else "")
        (reviewer,) = self.contracts("reviewer")
        self.assertFalse(reviewer["tampered"])

    def test_a_commit_on_the_branch_then_a_switch_is_undone_too(self):
        """A call that commits on the task's branch and checks out another leaves its
        commit on the branch tend merges; the guard puts the branch back as well."""
        act = (self.TAMPER.split(" && printf \"x")[0] + " && git checkout -q -b elsewhere && "
               + REVIEWER_SAYS % ("approve", "ok", ""))
        self.run_bridge("reviewer", act=act)
        self.assertEqual(tvl.git(self.wt, "symbolic-ref", "--short", "HEAD").strip(), TASK)
        self.assertEqual(tvl.git(self.wt, "rev-parse", TASK).strip(), self.worker_head)
        (row,) = self.ledger("tamper.jsonl")
        self.assertIn(f"branch {TASK} -> elsewhere", row["changed"])

    def test_the_worker_side_is_never_guarded(self):
        self.run_bridge("worker", act='printf "z\\n" > extra.txt && '
                                      + WORKER_DONE % ("completed", "s"))
        self.assertTrue((self.wt / "extra.txt").exists())
        self.assertEqual(self.ledger("tamper.jsonl"), [])

    def test_the_audited_turn_machine_shares_the_guard(self):
        import audit_turn

        self.assertEqual(Path(audit_turn.review_record.__file__).resolve(),
                         Path(review_record.__file__).resolve())
        before = review_record.fingerprint(self.wt)
        (self.wt / "stray.txt").write_text("x\n", encoding="utf-8")
        self.assertNotEqual(review_record.fingerprint(self.wt), before)
        review_record.restore(self.wt, before[0], on=before[3])
        self.assertEqual(review_record.fingerprint(self.wt), before)
        self.assertEqual(before[3], TASK)


class TestTheCappedTurnsAreRecorded(BridgeHarness):
    """At the attempt cap the bridge answers without an agent; its contract is still kept,
    so the harvest can check the capped documents against it."""

    def test_the_capped_worker_leaves_a_contract(self):
        fx = tvl.Fixture(self, "locate")
        self.root = fx.root                        # gate/tasks beside attempts/, as on a host
        prompts = self.root / "prompts"
        prompts.mkdir()
        for role in ("worker", "reviewer"):
            (prompts / f"{role}-system.md").write_text("system\n", encoding="utf-8")
            (prompts / f"{role}.md").write_text("task\n", encoding="utf-8")
        fx.manifest(TASK, tvl.locate_manifest_doc(fx))
        (self.root / "attempts").mkdir()
        (self.root / "attempts" / f"{TASK}.json").write_text(json.dumps(
            {"task_id": TASK, "rejections": 3, "history": []}), encoding="utf-8")
        self.run_bridge("worker", CF_ATTEMPT_CAP="3", CF_TASK_MANIFESTS=str(fx.tasks))
        self.assertFalse(self.argv_log.exists(), "the capped worker called an agent")
        (worker,) = self.contracts("worker")
        head = tvl.git(self.wt, "rev-parse", "HEAD").strip()
        self.assertEqual(worker["head"], head)
        self.assertNotEqual(head, self.worker_head)
        rel = f"findings/{TASK}/{tvl.SID}.json"
        self.assertEqual(worker["documents"][rel],
                         turn_records.git_blob_id(self.doc.read_bytes()))


# --------------------------------------------------------------------------- #
# P5 and P8 at the harvest
# --------------------------------------------------------------------------- #

class TestTheHarvestReadsTheTurnRecords(thr._HostHarvest):
    """The receipt lists every needs_review, and a merged document that is not the blob
    its worker committed makes it INCOMPLETE."""

    def setUp(self):
        super().setUp()
        self.tend = self.remote / "r-1-tend"
        self.landed = self.remote / "r-1" / "findings" / "t-loc-0001" / f"{tvl.SID}.json"
        self.rel = f"findings/t-loc-0001/{tvl.SID}.json"

    def contract(self, blob: str, status: str = "completed", stamp: str = "20261026T010000"
                 ".000001Z") -> None:
        directory = self.tend / "contracts"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"t-loc-0001.worker.{stamp}.json").write_text(json.dumps({
            "schema": review_record.CONTRACT_SCHEMA, "role": "worker", "task_id": "t-loc-0001",
            "worktree_id": "wt-1", "head": "c" * 40, "documents": {self.rel: blob},
            "status": status, "summary": "s", "notes": "", "at": "2026-10-26T01:00:00Z"}))

    def receipt(self) -> dict:
        return json.loads((self.ws / "corpus" / "r-1" / "RECEIPT.json").read_text())

    def test_a_merged_document_that_is_its_workers(self):
        self.contract(turn_records.git_blob_id(self.landed.read_bytes()))
        code, out = self.harvest("--execute")
        self.assertEqual(code, 0, out)
        receipt = self.receipt()
        self.assertTrue(receipt["complete"])
        self.assertEqual(receipt["worker_documents"],
                         {"checked": True, "matched": 1, "unrecorded": []})
        self.assertEqual(receipt["merged_not_worker"], [])
        self.assertEqual(receipt["needs_review"]["count"], 0)
        self.assertEqual(receipt["host_artifacts"]["contracts"]["files"], 1)
        self.assertIn("1 merged document(s) are their worker's commit", out)

    def test_a_merged_document_that_is_not_is_incomplete(self):
        self.contract("0" * 40, stamp="20261026T010000.000001Z")
        # the last contract is the one that counts
        self.contract(turn_records.git_blob_id(b"{}\n"), stamp="20261026T020000.000001Z")
        code, out = self.harvest("--execute")
        self.assertEqual(code, 1, out)
        receipt = self.receipt()
        self.assertFalse(receipt["complete"])
        (row,) = receipt["merged_not_worker"]
        self.assertEqual((row["task_id"], row["path"], row["worker_blob"]),
                         ("t-loc-0001", self.rel, turn_records.git_blob_id(b"{}\n")))
        self.assertEqual(row["merged_blob"], turn_records.git_blob_id(self.landed.read_bytes()))
        self.assertIn("MERGED NOT THE WORKER'S", out)
        self.assertIn("merged document(s) are not the blob their worker committed", out)

    def test_every_needs_review_is_listed(self):
        self.contract(turn_records.git_blob_id(self.landed.read_bytes()), status="needs_review")
        (self.tend / "needs-review.jsonl").write_text(json.dumps({
            "task_id": "t-loc-0001", "worktree_id": "wt-1", "worker_head": "c" * 40,
            "worker_summary": "declined a forbidden verdict", "worker_notes": "",
            "reviewer_verdict": "approve", "reviewer_notes": "form is fine",
            "at": "2026-10-26T01:10:00Z"}) + "\n")
        code, out = self.harvest("--execute")
        self.assertEqual(code, 0, out)                  # it merges as before
        needs = self.receipt()["needs_review"]
        self.assertEqual((needs["count"], needs["tasks"], needs["merged"]),
                         (1, ["t-loc-0001"], ["t-loc-0001"]))
        self.assertEqual(needs["entries"][0]["worker_summary"], "declined a forbidden verdict")
        self.assertIn("NEEDS REVIEW in run r-1: 1 task(s)", out)
        self.assertIn("t-loc-0001 (merged so)", out)

    def test_tampering_is_reported(self):
        self.contract(turn_records.git_blob_id(self.landed.read_bytes()))
        (self.tend / "tamper.jsonl").write_text(json.dumps({
            "task_id": "t-loc-0001", "worktree_id": "wt-1", "head": "c" * 40,
            "changed": ["tracked files changed"], "at": "2026-10-26T01:10:00Z"}) + "\n")
        code, out = self.harvest("--execute")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.receipt()["reviewer_tampered"]["tasks"], ["t-loc-0001"])
        self.assertIn("reviewer_tampered: 1 reviewer call(s)", out)

    def test_contracts_are_expected_when_the_run_pinned_the_recorder(self):
        run_yaml = self.ws / "runs" / "r-1" / "run.yaml"
        run_yaml.write_text(run_yaml.read_text().replace(
            "  gate_files:\n", '  gate_files:\n    "bin/review_record.py": "' + "1" * 64 + '"\n'))
        code, out = self.harvest("--execute")
        self.assertEqual(code, 1, out)
        receipt = self.receipt()
        self.assertIn("contracts", receipt["host_artifacts_missing"])
        self.assertNotIn("worker_documents", receipt)
        # with them it is complete, and checked
        self.contract(turn_records.git_blob_id(self.landed.read_bytes()))
        code, out = self.harvest("--execute")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.receipt()["worker_documents"]["checked"])

    def test_a_run_without_records_gets_no_blocks(self):
        code, out = self.harvest("--execute")
        self.assertEqual(code, 0, out)
        receipt = self.receipt()
        for key in ("needs_review", "worker_documents", "merged_not_worker",
                    "reviewer_tampered"):
            self.assertNotIn(key, receipt)
        self.assertEqual(receipt["host_artifacts"]["contracts"]["reason"], "absent on the host")
        self.assertFalse(receipt["host_artifacts"]["contracts"]["expected"])


class TestTheBlobIds(unittest.TestCase):

    def test_git_blob_id_is_gits(self):
        tmp = scratch(self)
        (tmp / "f.json").write_bytes(b'{"a": 1}\n')
        got = subprocess.run(["git", "hash-object", "--no-filters", str(tmp / "f.json")],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(turn_records.git_blob_id(b'{"a": 1}\n'), got)
        self.assertEqual(len(turn_records.git_blob_id(b"", length=64)), 64)

    def test_the_recorder_is_staged_and_pinned(self):
        from plan.make_tend_root import BRIDGE_HELPERS
        from run import manifest

        self.assertIn("review_record.py", BRIDGE_HELPERS)
        self.assertIn("review_record.py", manifest.BRIDGE_FILES)
        self.assertEqual(host_mod.RECORDER_PIN, "bin/review_record.py")

    def test_the_recorder_runs_on_the_host_from_bin_alone(self):
        import ast
        tree = ast.parse((SCRIPTS / "run" / "review_record.py").read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for a in node.names}
        names |= {node.module.split(".")[0] for node in ast.walk(tree)
                  if isinstance(node, ast.ImportFrom) and node.module}
        self.assertLessEqual(names - {"attempt_cap"}, set(sys.stdlib_module_names) | {"__future__"})
        bindir = scratch(self) / "bin"
        bindir.mkdir()
        for name in ("review_record.py", "attempt_cap.py", "normalize_result.py"):
            shutil.copy2(SCRIPTS / "run" / name, bindir / name)
        proc = subprocess.run([sys.executable, "-I", str(bindir / "review_record.py"),
                               "fingerprint", "--worktree", str(bindir)], capture_output=True,
                              text=True, env={"PATH": "/usr/bin:/bin"}, cwd=str(bindir))
        self.assertEqual((proc.returncode, proc.stdout), (1, ""), proc.stderr)
        self.assertIn("no HEAD", proc.stderr)

    def test_the_copied_schema_is_the_vocabularys(self):
        self.assertEqual(review_record.CONTRACT_SCHEMA, vocab.TURN_CONTRACT_SCHEMA)
        for name in ("references/vocabulary.md", "references/locate-documents.md"):
            self.assertIn(f"`{vocab.TURN_CONTRACT_SCHEMA}`",
                          (SKILL / name).read_text(encoding="utf-8"), name)
        # the harvest's local names are the recorder's
        self.assertEqual((turn_records.CONTRACTS, turn_records.NEEDS_REVIEW_LEDGER,
                          turn_records.TAMPER_LEDGER),
                         (review_record.CONTRACTS_DIR, review_record.NEEDS_REVIEW_LEDGER,
                          review_record.TAMPER_LEDGER))
        self.assertEqual((host_mod.CONTRACTS, host_mod.NEEDS_REVIEW, host_mod.TAMPER),
                         (review_record.CONTRACTS_DIR, review_record.NEEDS_REVIEW_LEDGER,
                          review_record.TAMPER_LEDGER))


if __name__ == "__main__":
    unittest.main()
