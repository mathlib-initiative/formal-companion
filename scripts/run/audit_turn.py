#!/usr/bin/env python3
"""The audited inventory's turn machine: one tend turn of an audited task, worker or reviewer.

New code, for references/locate-documents.md section 13.9 (the turn machine) on the
records of its section 13 (``cf-inventory-audit/1``) and the convergence rules of
docs/proposals/wikipedia-locate.md section 6.  ``bridge-agentbox.sh`` hands every turn
of an audited run to this file when the wrappers export ``CF_AUDIT_RULE``; without it
the bridge never calls it.

    audit_turn.py worker|reviewer --root <tend-root> --prompts <dir> --host claude \\
        --profile <p> --bypass 0|1 --tools <allowlist> --err <log>

tend has two roles, so an audited task's rounds map onto them.  The **reviewer** turn
of round ``k`` (1 to ``CF_AUDIT_ROUNDS``, R) runs the gate's ``--findings`` check on
the document (the precheck), snapshots it, renders it in parts
(``inventory_render.py``), runs one agent call per part -- the auditor in round 1,
the checker from round 2, each validated by ``inventory_audit.py validate`` and
re-called once with the validator's lines when its output is invalid -- strips the
flags, runs one skeptic call per part that has flags, disputes or (round 1) passages
to hunt over, without the auditor's file in the worktree, and has
``inventory_audit.py merge`` decide.  The **worker** turn after a rejection applies
the round's verdicts in sessions of at most ``REPAIR_VERDICTS_PER_SESSION``, each a
fresh call with the revision prompt.  What this file adds to tend's loop:

* **The state** lives outside every worktree, in ``<tend-root>/audit/<task-id>/
  state.json`` (``attempt_cap.audit_state_path``): the round, each round's document
  sha256, head and steps, the revision in progress, the best reviewed snapshot, and
  the last event.  The round advances only on the merge's ``request_changes``, so a
  precheck refusal, an infrastructure failure or a revert never moves it, and at most
  R - 1 rejections are recorded: below the attempt cap.
* **The files** live there too (``<tend-root>/audit/<task-id>/<source-id>/``);
  ``.tend/audit/`` in the worktree is a mirror, cleared and staged before every call
  with exactly what that call may read, and its outputs copied back after.  A wiped
  ``.tend/`` loses nothing.
* **Reuse.**  A step whose output validated for the round's document sha is not run
  again, so a turn tend retries after a timeout or a 409 pays only for what is left.
* **The tamper guard.**  Around every reviewer call: a changed HEAD, a changed
  tracked file or a new untracked file outside ``.tend/`` is undone with ``git reset
  --hard <head>`` and ``git clean -fd -e .tend``, and counted.
* **audit_failed.**  A step still invalid after its one retry, round files the
  merge refuses, or a rendering or collateral that fails on its input (any exit but
  0 or 3), end the round as ``merge --audit-failed``: approve, counted.
* **Revert.**  The merge's ``revert`` is a request the bridge writes and does not
  record; the next worker turn restores the snapshot's bytes and commits them with
  no agent call, and the reviewer approves when the document's sha256 is the
  snapshot's.
* **The gate-loop reset.**  A revision tend's pre-review gate (or the precheck)
  refuses ``GATE_RESETS`` times is reset to the round's reviewed commit with no agent
  call, so the next review finds no progress and approves.
* **No progress.**  A reviewer turn whose document is the very bytes the last round
  rejected approves mechanically, with no agent call and counted (``no_progress``): a
  check of an unchanged document has nothing to judge, and the last round's ops file
  would be re-applied as collateral the document does not hold.
* **The revision prompt** is chosen by the last event: the round's verdicts after a
  rejection, the gate's refusal after a refused commit -- never "a reviewer rejected"
  after a gate failure.

Under ``CF_AUDIT_RULE=2`` (section 13.11, audit rule 2: the skips audited too) every host
tool call and every validator line an agent is given carries ``--rule 2`` (an auditor's and
a skeptic's also ``--gate-config``, :meth:`Turn.advised`), the state records
the rule, a part showing an unflagged skip over math no passage holds gets the round-1 skip
hunt's skeptic call, a skip flag goes to the part that shows its skip, the rendering's diff
reads the last round's verdicts, and the revision's sessions are weighed (:meth:`Turn.sessions`).
Under rule 1 every call, prompt and command line is the base commit's.

Every agent call goes through ``agent_call.py --step`` (its session slot, timeout and
kept envelope; the envelope's name carries the step).  Standard library only, no
cflib, run as ``python3 -I``: the host tools are run from the gate directory, the
gate as ``CF_GATE_CMD``.  stdout carries only tend's contract JSON.  Exit 0 with a
contract, 124/75/64 or 128 + N for an infrastructure failure (tend retries the
turn), 66 for a missing prompt, 78 for a configuration it refuses, 1 when the turn
could not finish.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import attempt_cap  # noqa: E402  (bin/'s sibling: the counter, the git boundary, the state path)
import normalize_result  # noqa: E402  (bin/'s sibling: the one shape of a contract)

# --------------------------------------------------------------------------- #
# COPIED from cflib.vocab (no cflib on the host); tests/test_audit_turn.py pins them
# --------------------------------------------------------------------------- #

TURN_SCHEMA = "cf-audit-turn/1"
#: the audit rules this machine runs (CF_AUDIT_RULE): rule 1 audits the passages, rule 2 the
#: skips too (references/locate-documents.md section 13.11)
AUDIT_RULES = (1, 2)
AUDIT_REVIEW_ROUNDS = (2, 3)
AUDIT_STEPS = ("audit", "skeptic", "check", "revision")
OUTPUT_RETRIES = 1
GATE_RESETS = 3
DISCUSSION_TAIL = 6000
REVERT_ATTEMPTS = 3
REPAIR_VERDICTS_PER_SESSION = 40
SUMMARY_WORDS = 80
SKIP_VERDICT = "skipped_statements"

EXIT_FAILED = 1
EXIT_USAGE = 64
EXIT_NO_PROMPT = 66
EXIT_TEMPFAIL = 75
EXIT_CONFIG = 78
#: what an agent call exits with when it failed as infrastructure (agent_call.py)
INFRA_EXITS = (124, 75, 64)
MIRROR = (".tend", "audit")
RESULT = (".tend", "agent_result.json")
DISCUSSION = (".tend", "discussion.md")
#: the prompt files each role reads (the bridge checks ``<role>-system.md`` and ``<role>.md``)
PROMPT_FILES = {"worker": ("worker-system.md", "worker.md", "worker-revision.md"),
                "reviewer": ("reviewer-system.md", "reviewer.md", "reviewer-check.md",
                             "reviewer-skeptic.md")}
STEP_PROMPT = {"audit": "reviewer.md", "check": "reviewer-check.md",
               "skeptic": "reviewer-skeptic.md"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SLOT = re.compile(r"\$(CF_AUDIT_[A-Z_]+)(?![A-Za-z0-9_])")
MAX_PROBLEM_LINES = 60
MAX_EVENTS = 500


class Config(Exception):
    """A configuration the turn refuses (exit 78)."""


class NoPrompt(Exception):
    """A prompt file the run did not stage (exit 66, the bridge's own)."""


class Infra(Exception):
    """An agent call or a host tool failed as infrastructure: tend retries the turn."""

    def __init__(self, code: int, why: str):
        super().__init__(why)
        self.code = code


class Refused(Exception):
    """The turn could not finish (exit 1); logged, never a verdict."""


class StepFailed(Exception):
    """A host tool of the round failed on its input: the round is ``audit_failed``, so a
    deterministic failure never loops the reviewer turn on exit 1."""


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cap_words(text: str, n: int) -> str:
    return " ".join(str(text).split()[:n])


def invalid_lines(*texts: str) -> list[str]:
    """The validator's problem lines (``INVALID: ...``), else its last lines."""
    lines = [ln for t in texts for ln in (t or "").splitlines() if ln.strip()]
    marked = [ln for ln in lines if ln.startswith("INVALID")]
    return (marked or lines[-10:] or ["the validator said nothing"])[:MAX_PROBLEM_LINES]


@dataclass
class Call:
    """One agent call: what the runner needs to make it, and what the tests read."""

    role: str
    step: str | None
    round: int
    label: str
    prompt: str
    system: str
    raw: Path
    part: int | None = None
    parts: int | None = None
    out: str | None = None


class Runner:
    """What a turn runs outside itself: the agent, the host tools, the gate.  The tests
    substitute these; nothing else in a turn reaches a process but git."""

    def agent(self, turn: "Turn", call: Call) -> int:
        argv = [sys.executable, "-I", str(turn.bindir / "agent_call.py"), "--role", call.role,
                "--raw", str(call.raw), "--err", str(turn.err), "--usage-dir",
                str(turn.root / "usage"), "--task-id", turn.task]
        if call.step:
            argv += ["--step", call.step]
        argv += ["--", "agentbox", "host", "claude", "--profile", turn.profile, "--",
                 "-p", call.prompt, "--append-system-prompt", call.system,
                 "--output-format", "json", *turn.permission, "--allowedTools", turn.tools]
        if turn.model:
            argv += ["--model", turn.model]
        return subprocess.run(argv, cwd=str(turn.worktree), stdin=subprocess.DEVNULL,
                              stdout=sys.stderr.fileno(), check=False).returncode

    def tool(self, turn: "Turn", script: str, args: list[str]) -> tuple[int, str, str]:
        got = subprocess.run([sys.executable, "-I", str(turn.gate_dir / script), *args],
                             cwd=str(turn.worktree), capture_output=True, text=True,
                             timeout=1800, check=False)
        return got.returncode, got.stdout, got.stderr

    def gate(self, turn: "Turn") -> tuple[int, str, str]:
        got = subprocess.run([*turn.gate_cmd, "--findings", turn.doc_rel],
                             cwd=str(turn.worktree), capture_output=True, text=True,
                             timeout=1800, check=False)
        return got.returncode, got.stdout, got.stderr


class Turn:
    """One turn of one audited task, in the worktree it runs in."""

    def __init__(self, args, runner: Runner, env: dict | None = None,
                 worktree: Path | None = None):
        env = dict(os.environ if env is None else env)
        self.env = env
        self.role = args.role
        self.runner = runner
        self.root = Path(args.root)
        self.prompts = Path(args.prompts)
        self.tools = args.tools
        self.profile = args.profile
        self.err = Path(args.err) if args.err else self.root / f"agentbox-{self.role}.err"
        self.bindir = Path(__file__).resolve().parent
        self.worktree = (worktree or Path.cwd()).resolve()
        if args.host != "claude":
            raise Config(f"an audited run calls claude, not {args.host!r} (CF_AGENT_HOST)")
        self.task = str(env.get("TEND_TASK_ID") or "")
        if not attempt_cap._safe(self.task):
            raise Config(f"no usable task id ({self.task!r}, TEND_TASK_ID)")
        self.worktree_id = str(env.get("TEND_WORKTREE_ID") or "")
        rule = str(env.get("CF_AUDIT_RULE") or "")
        if rule not in {str(r) for r in AUDIT_RULES}:
            raise Config(f"CF_AUDIT_RULE={rule!r} is not a rule this bridge knows "
                         f"({', '.join(map(str, AUDIT_RULES))})")
        self.rule = int(rule)
        #: what every host tool call and validator line carries: nothing under rule 1, so
        #: a rule-1 run's calls are inv-pilot-3's byte for byte
        self.rule_args = ["--rule", str(self.rule)] if self.rule >= 2 else []
        try:
            self.review_rounds = int(env.get("CF_AUDIT_ROUNDS") or "")
        except ValueError:
            self.review_rounds = 0
        if self.review_rounds not in AUDIT_REVIEW_ROUNDS:
            raise Config(f"CF_AUDIT_ROUNDS={env.get('CF_AUDIT_ROUNDS')!r} is not one of "
                         f"{AUDIT_REVIEW_ROUNDS}")
        self.gate_cmd = shlex.split(str(env.get("CF_GATE_CMD") or ""))
        if len(self.gate_cmd) < 2 or not self.gate_cmd[1].endswith("verify_locate.py") \
                or "--gate-config" not in self.gate_cmd[:-1]:
            raise Config(f"CF_GATE_CMD {env.get('CF_GATE_CMD')!r} is not the locate gate with "
                         f"its --gate-config")
        self.gate_dir = Path(self.gate_cmd[1]).parent
        self.gate_config = self.gate_cmd[self.gate_cmd.index("--gate-config") + 1]
        for script in ("inventory_audit.py", "inventory_render.py", "inventory_edit.py"):
            if not (self.gate_dir / script).is_file():
                raise Config(f"the gate directory {self.gate_dir} holds no {script} (an "
                             f"audited plan stages it)")
        manifests = Path(env.get("CF_TASK_MANIFESTS") or self.gate_dir / "tasks")
        try:
            manifest = attempt_cap.load_manifest(manifests, self.task)
        except (OSError, ValueError) as exc:
            raise Config(f"task {self.task}'s manifest cannot be read: {exc}") from None
        sources = [str(d.get("source_id") or "") for d in manifest.get("documents") or []
                   if isinstance(d, dict)]
        if len(sources) != 1 or not attempt_cap._safe(sources[0]):
            raise Config(f"an audited task holds one document; task {self.task}'s manifest "
                         f"names {len(sources)}")
        self.source = sources[0]
        self.doc_rel = f"findings/{self.task}/{self.source}.json"
        try:
            self.cap = max(0, int(env.get("CF_ATTEMPT_CAP") or 0))
        except ValueError:
            self.cap = 0
        self.model = str(env.get("CF_MODEL") or "")
        if str(args.bypass) == "1":
            self.permission = str(env.get("CF_PERMISSION_FLAGS",
                                          "--dangerously-skip-permissions")).split()
        else:
            self.permission = ["--permission-mode", "acceptEdits"]
        self.part_bytes = str(env.get("CF_AUDIT_PART_BYTES") or "")
        self.auth = attempt_cap.audit_task_dir(self.root, self.task) / self.source
        self.state_path = attempt_cap.audit_state_path(self.root, self.task)
        for name in PROMPT_FILES[self.role]:
            if not (self.prompts / name).is_file():
                raise NoPrompt(f"missing prompt {self.prompts / name}")

    # ------------------------------------------------------------------ #
    # logging, state
    # ------------------------------------------------------------------ #

    def log(self, line: str) -> None:
        text = f"audit_turn: {self.role} task={self.task}: {line}"
        print(text, file=sys.stderr)
        try:
            with open(self.err, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass

    def fresh(self) -> dict:
        return {"schema": TURN_SCHEMA, "rule": self.rule, "task_id": self.task,
                "source_id": self.source, "document": self.doc_rel,
                "review_rounds": self.review_rounds, "round": 1, "rounds": {},
                "revision": None, "pending": None, "approved": None, "best": None,
                "last": None, "rejections": 0, "worktrees": [],
                "counts": {"reviewer_tampered": 0, "gate_resets": 0, "audit_failed": 0,
                           "precheck_refused": 0, "reverts": 0, "retries": 0,
                           "unaudited": 0, "repair_invalid": 0, "no_progress": 0},
                "events": []}

    def load(self) -> dict:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = self.fresh()
        except (OSError, ValueError) as exc:
            raise Refused(f"the audit state {self.state_path} cannot be read: {exc}") from None
        if state.get("schema") != TURN_SCHEMA or state.get("task_id") != self.task \
                or state.get("source_id") != self.source:
            raise Refused(f"{self.state_path} is not task {self.task}'s audit state")
        if state.get("rule", self.rule) != self.rule:
            raise Refused(f"{self.state_path} records audit rule {state.get('rule')!r}; this "
                          f"turn runs rule {self.rule} (CF_AUDIT_RULE)")
        if self.worktree_id and self.worktree_id not in state["worktrees"]:
            state["worktrees"].append(self.worktree_id)
        return state

    def save(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    def event(self, state: dict, name: str, **detail) -> None:
        row = {"event": name, "round": state["round"], "at": stamp(), **detail}
        state["last"] = row
        state["events"] = (state["events"] + [row])[-MAX_EVENTS:]

    # ------------------------------------------------------------------ #
    # files: the authoritative copies and the worktree's mirror
    # ------------------------------------------------------------------ #

    @property
    def mirror(self) -> Path:
        return self.worktree.joinpath(*MIRROR)

    @property
    def result_path(self) -> Path:
        return self.worktree.joinpath(*RESULT)

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def m(self, name: str) -> str:
        """``name`` as the tools read it, relative to the worktree."""
        return "/".join(MIRROR + (name,))

    def _clear_mirror(self) -> Path:
        tend = self.worktree / MIRROR[0]
        if tend.is_symlink():
            raise Refused(f"{tend} is a symlink; nothing is staged through it")
        tend.mkdir(exist_ok=True)
        mirror = self.mirror
        if mirror.is_symlink() or (mirror.exists() and not mirror.is_dir()):
            mirror.unlink()
        mirror.mkdir(exist_ok=True)
        for child in mirror.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        return mirror

    def stage(self, names: list[str]) -> None:
        """Clear the mirror and copy in exactly ``names`` (those that exist)."""
        mirror = self._clear_mirror()
        for name in names:
            if not _NAME.match(name):
                raise Refused(f"refused to stage {name!r}")
            src = self.auth / name
            if src.is_file():
                shutil.copyfile(src, mirror / name)

    def collect(self, name: str) -> bool:
        """Copy the mirror's ``name`` back, when it is a regular file the call wrote."""
        src = self.mirror / name
        if src.is_symlink() or not src.is_file():
            return False
        self.auth.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, self.auth / name)
        return True

    def write_auth(self, name: str, data) -> None:
        self.auth.mkdir(parents=True, exist_ok=True)
        (self.auth / name).write_text(json.dumps(data, ensure_ascii=False, indent=1,
                                                 sort_keys=True) + "\n", encoding="utf-8")

    def read_auth(self, name: str):
        try:
            return json.loads((self.auth / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Refused(f"{self.auth / name} cannot be read: {exc}") from None

    def parses(self, name: str) -> bool:
        try:
            return isinstance(json.loads((self.auth / name).read_text(encoding="utf-8")), dict)
        except (OSError, ValueError):
            return False

    def discard(self, k: int) -> None:
        """Move round ``k``'s files aside (its document changed under it)."""
        files = [p for p in self.auth.glob(f"r{k}.*") if p.is_file()] if self.auth.is_dir() else []
        if not files:
            return
        dest = self.auth / "discarded" / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-r{k}"
        dest.mkdir(parents=True, exist_ok=True)
        for path in files:
            os.replace(path, dest / path.name)

    # ------------------------------------------------------------------ #
    # git, the tools, the document
    # ------------------------------------------------------------------ #

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return attempt_cap._git(self.worktree, *args)

    def head(self) -> str:
        got = self.git("rev-parse", "HEAD")
        return got.stdout.strip() if got.returncode == 0 else ""

    def fingerprint(self) -> tuple:
        diff = self.git("diff", "--binary", "HEAD")
        others = self.git("ls-files", "--others", "--exclude-standard", "-z")
        untracked = sorted(p for p in others.stdout.split("\0")
                           if p and not p.startswith(MIRROR[0] + "/"))
        return self.head(), sha256_bytes(diff.stdout.encode("utf-8", "replace")), tuple(untracked)

    def guard(self, state: dict, before: tuple) -> None:
        """The tamper guard: undo whatever a reviewer call changed outside ``.tend/``."""
        if self.fingerprint() == before:
            return
        self.git("reset", "-q", "--hard", before[0])
        self.git("clean", "-q", "-fd", "-e", MIRROR[0])
        state["counts"]["reviewer_tampered"] += 1
        state["events"].append({"event": "reviewer_tampered", "round": state["round"],
                                "at": stamp()})
        self.log(f"reviewer_tampered: a reviewer call changed the worktree; reset to "
                 f"{before[0][:12]} and cleaned (outside .tend/)")

    def tool(self, script: str, *args) -> tuple[int, str, str]:
        code, out, err = self.runner.tool(self, script, [str(a) for a in args])
        if code == 3:
            raise Infra(EXIT_TEMPFAIL, f"{script} could not read the gate configuration or a "
                                       f"pinned source: {(err or out).strip()[-300:]}")
        return code, out, err

    def need(self, got: tuple[int, str, str], what: str) -> str:
        code, out, err = got
        if code != 0:
            raise Refused(f"{what} failed (exit {code}): {(err or out).strip()[-400:]}")
        return out

    def doc_path(self) -> Path:
        return self.worktree / self.doc_rel

    def doc_sha(self) -> str | None:
        try:
            return sha256_bytes(self.doc_path().read_bytes())
        except OSError:
            return None

    def read_doc(self) -> dict:
        try:
            data = json.loads(self.doc_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ #
    # prompts and calls
    # ------------------------------------------------------------------ #

    def text(self, name: str, slots: dict | None = None) -> str:
        """A prompt file with the bridge's ``$TEND_*`` and this turn's ``$CF_AUDIT_*`` slots."""
        try:
            raw = (self.prompts / name).read_text(encoding="utf-8")
        except OSError as exc:
            raise NoPrompt(f"missing prompt {self.prompts / name}: {exc}") from None
        for var, value in (("$TEND_TASK_ID", self.env.get("TEND_TASK_ID", "")),
                           ("$TEND_WORKTREE_PATH", self.env.get("TEND_WORKTREE_PATH")
                            or str(self.worktree)),
                           ("$TEND_WORKTREE_ID", self.env.get("TEND_WORKTREE_ID", ""))):
            raw = raw.replace(var, str(value))
        values = {"CF_AUDIT_ROUNDS": self.review_rounds, "CF_AUDIT_SOURCE": self.source,
                  "CF_AUDIT_DOCUMENT": self.doc_rel, **(slots or {})}
        raw = _SLOT.sub(lambda mt: str(values[mt.group(1)]) if mt.group(1) in values
                        else mt.group(0), raw)
        return raw.rstrip("\n")

    def validate_line(self, args: list[str]) -> str:
        return " ".join(shlex.quote(a) for a in
                        ["python3", str(self.gate_dir / "inventory_audit.py"), *args])

    def ruled(self, args: list) -> list:
        """A host tool's arguments with the run's rule (none under rule 1)."""
        return [*args, *self.rule_args]

    def discussion_tail(self) -> str:
        path = self.worktree.joinpath(*DISCUSSION)
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                return ""
            data = path.read_bytes()[-DISCUSSION_TAIL:]
        except OSError:
            return ""
        return data.decode("utf-8", errors="replace").rstrip("\n")

    def call(self, state: dict, *, step: str | None, label: str, round_: int, prompt: str,
             system: str, part: int | None = None, parts: int | None = None,
             out: str | None = None) -> tuple[int, Path]:
        raw = self.auth / "raw" / f"{label}.json"
        raw.parent.mkdir(parents=True, exist_ok=True)
        if raw.exists():
            raw.unlink()
        try:
            self.result_path.unlink()
        except OSError:
            pass
        call = Call(role=self.role, step=step, round=round_, label=label, prompt=prompt,
                    system=system, raw=raw, part=part, parts=parts, out=out)
        before = self.fingerprint() if self.role == "reviewer" else None
        code = self.runner.agent(self, call)
        if before is not None:
            self.guard(state, before)
        return code, raw

    @staticmethod
    def infra(code: int) -> bool:
        return code in INFRA_EXITS or code >= 128 or code < 0

    # ------------------------------------------------------------------ #
    # contracts
    # ------------------------------------------------------------------ #

    def emit(self, contract: dict) -> int:
        print(json.dumps(contract))
        return 0

    def review(self, verdict: str, notes: str, feedback: str | None = None) -> dict:
        out = {"verdict": verdict, "notes": notes[:1500]}
        if verdict == "request_changes":
            out["feedback_text"] = (feedback or notes)[:1500]
        return normalize_result.reviewer_result(out, "")

    def worked(self, summary: str, notes: str) -> dict:
        return {"schema_version": 1, "status": "completed", "summary": summary,
                "files_changed": [self.doc_rel], "notes": notes}

    def worker_contract(self, raw: Path) -> dict:
        """normalize_result's worker contract of the call that wrote ``raw``."""
        data = normalize_result.load(str(self.result_path))
        envelope = normalize_result.load_raw(str(raw))
        text = (envelope.get("result") or "").strip()
        out = normalize_result.worker_result(data, text)
        if (envelope.get("subtype") != "success" or envelope.get("is_error")) \
                and out["status"] == "completed":
            out["status"] = "needs_review"
        return out

    # ------------------------------------------------------------------ #
    # the turn
    # ------------------------------------------------------------------ #

    def run(self) -> int:
        state = self.load()
        try:
            return self.reviewer(state) if self.role == "reviewer" else self.worker(state)
        finally:
            self.save(state)

    # ------------------------------ reviewer --------------------------- #

    def reviewer(self, state: dict) -> int:
        sha = self.doc_sha()
        pending = state.get("pending")
        if pending and pending.get("kind") == "revert":
            return self.reverted(state, pending, sha)
        approved = state.get("approved")
        if approved and approved.get("sha256") == sha:
            return self.emit(self.review("approve", f"Audit: approved before for this document "
                                                    f"({approved.get('why')})."))
        last = state.get("last") or {}
        if last.get("event") == "review" and last.get("decision") == "request_changes" \
                and last.get("sha256") == sha:
            # the contract of a decision already recorded, printed again
            return self.emit(self.read_auth(f"r{last['reviewed']}.result.json"))
        code, out, err = self.runner.gate(self)
        if code == 1 or (code != 0 and sha is None):
            state["counts"]["precheck_refused"] += 1
            self.event(state, "precheck", sha256=sha)
            lines = (out + "\n" + err).strip()[-1200:]
            text = (f"Audit round {state['round']}: the gate refused the document before the "
                    f"audit (the bridge's precheck); fix what it names. {lines}")
            return self.emit(self.review("request_changes", text))
        if code != 0:
            raise Infra(EXIT_TEMPFAIL, f"the precheck gate could not run (exit {code}): "
                                       f"{(err or out).strip()[-300:]}")
        k = state["round"]
        prior = state["rounds"].get(str(k - 1)) if k > 1 else None
        if prior and prior.get("decision") == "request_changes" \
                and prior.get("document_sha256") == sha:
            return self.no_progress(state, k, sha)
        state["approved"] = None
        rd = state["rounds"].get(str(k))
        if rd is not None and rd.get("document_sha256") != sha:
            self.discard(k)
            rd = None
        if rd is None:
            rd = state["rounds"][str(k)] = {"document_sha256": sha, "head": self.head(),
                                            "worktree_id": self.worktree_id, "steps": {}}
        self.auth.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.doc_path(), self.auth / f"r{k}.document.json")
        self.save(state)
        doc = self.read_doc()
        if isinstance(doc.get("unresolved"), dict) or not doc.get("passages"):
            state["counts"]["unaudited"] += 1
            why = ("an unresolved document" if isinstance(doc.get("unresolved"), dict)
                   else "a document with no passage")
            state["approved"] = {"round": k, "sha256": sha, "why": f"not audited: {why}"}
            state["revision"] = None
            self.event(state, "approved", decision="approve", sha256=sha, unaudited=True)
            return self.emit(self.review("approve", f"Audit round {k}: not audited ({why}); "
                                                    f"approved after the gate's check."))
        try:
            verdicts = self.round1(state, rd) if k == 1 else self.round_later(state, rd, k)
        except StepFailed as exc:
            verdicts = self.failed(state, k, str(exc))
        return self.decided(state, rd, k, verdicts)

    def no_progress(self, state: dict, k: int, sha: str | None) -> int:
        """Round k's document is the one round k - 1 rejected (a gate-loop reset, or a
        revision that changed nothing): approve it mechanically, counted."""
        counts = state["counts"]
        counts["no_progress"] = int(counts.get("no_progress") or 0) + 1
        state["revision"] = None
        why = f"no progress since round {k - 1}"
        state["approved"] = {"round": k - 1, "sha256": sha, "why": why}
        self.event(state, "approved", decision="approve", sha256=sha, no_progress=k - 1)
        return self.emit(self.review("approve", f"Audit round {k}: approved mechanically, {why}: "
                                                f"the document is the one round {k - 1} "
                                                f"reviewed (no agent call)."))

    def reverted(self, state: dict, pending: dict, sha: str | None) -> int:
        if sha == pending.get("sha256"):
            state["pending"] = None
            state["revision"] = None
            why = f"reverted to round {pending['to']}'s snapshot"
            state["approved"] = {"round": pending["round"], "sha256": sha, "why": why}
            self.event(state, "approved", decision="approve", sha256=sha, reverted_to=pending["to"])
            return self.emit(self.review("approve", f"Audit round {pending['round']}: approved "
                                                    f"mechanically, {why} (no agent call)."))
        pending["attempts"] = int(pending.get("attempts") or 0) + 1
        if pending["attempts"] > REVERT_ATTEMPTS:
            raise Refused(f"the revert to round {pending['to']}'s snapshot was not restored "
                          f"after {REVERT_ATTEMPTS} requests")
        self.event(state, "revert_requested", to=pending["to"])
        text = (f"Audit round {pending['round']}: revert to the round {pending['to']} snapshot; "
                f"the bridge restores it on the next worker turn (no agent call).")
        return self.emit(self.review("request_changes", text))

    def decided(self, state: dict, rd: dict, k: int, verdicts: dict) -> int:
        decision = verdicts["decision"]
        rd.update(decision=decision, actionable=len(verdicts["verdicts"]),
                  harm=bool(verdicts.get("harm")), audit_failed=bool(verdicts.get("audit_failed")),
                  revert_to=verdicts.get("revert_to"))
        state["best"] = self.best(verdicts)
        result = normalize_result.load(str(self.result_path))
        if not isinstance(result, dict):
            raise Refused("the merge wrote no .tend/agent_result.json")
        contract = normalize_result.reviewer_result(result, "")
        self.write_auth(f"r{k}.result.json", contract)
        sha = rd["document_sha256"]
        if decision == "request_changes" and k < self.review_rounds:
            state["round"] = k + 1
            state["revision"] = {"round": k, "next": 0, "sessions": None, "complete": False,
                                 "gatefix": False, "gatefails": 0}
            state["rejections"] += 1
            self.event(state, "review", decision=decision, sha256=sha, reviewed=k)
            self.save(state)
            if self.cap and rd.get("raw"):
                attempt_cap.record(self.root, self.task, self.root / rd["raw"],
                                   json.dumps(contract), self.result_path)
            return self.emit(contract)
        if decision == "revert":
            j = int(verdicts["revert_to"])
            state["pending"] = {"kind": "revert", "round": k, "to": j,
                                "sha256": state["rounds"][str(j)]["document_sha256"],
                                "snapshot": self.rel(self.auth / f"r{j}.document.json"),
                                "attempts": 0}
            state["counts"]["reverts"] += 1
            self.event(state, "review", decision=decision, sha256=sha, reviewed=k,
                       revert_to=j)
            return self.emit(contract)
        state["approved"] = {"round": k, "sha256": sha, "why": verdicts.get("why")}
        state["revision"] = None
        self.event(state, "approved", decision="approve", sha256=sha,
                   audit_failed=bool(verdicts.get("audit_failed")))
        return self.emit(self.review("approve", contract.get("notes") or "approve"))

    def best(self, verdicts: dict) -> dict:
        """The best reviewed snapshot (inventory_audit.best_round's rule over every round)."""
        rounds = verdicts.get("rounds") or []
        sound = [r for r in rounds if not r.get("harm")] or \
            [r for r in rounds if r.get("round") == 1] or rounds
        pick = min(sound, key=lambda r: (r["actionable"], -r["round"]))
        return {"round": pick["round"], "sha256": pick["document_sha256"],
                "snapshot": self.rel(self.auth / f"r{pick['round']}.document.json")}

    # the rounds

    def render(self, k: int, previous: str | None) -> dict:
        verdicts = f"r{k - 1}.verdicts.json" if previous and self.rule >= 2 else None
        self.stage(([previous] if previous else []) + ([verdicts] if verdicts else []))
        args = ["--gate-config", self.gate_config, "--document", self.doc_rel,
                "--out-dir", "/".join(MIRROR), "--label", f"r{k}"]
        if previous:
            args += ["--previous", self.m(previous)]
        if verdicts:
            args += ["--previous-verdicts", self.m(verdicts)]
        if self.part_bytes:
            args += ["--part-bytes", self.part_bytes]
        code, out, err = self.tool("inventory_render.py", *self.ruled(args))
        if code != 0:
            raise StepFailed(f"the rendering failed (exit {code}): "
                             + "; ".join(invalid_lines(out, err)[:5]))
        for path in self.mirror.iterdir():
            if path.name.startswith(f"r{k}.render.") or path.name == f"r{k}.diff.md":
                self.collect(path.name)
        return self.read_auth(f"r{k}.render.json")

    def geometry(self, index: dict) -> tuple[list[set], list[int], dict]:
        starts = {}
        for p in self.read_doc().get("passages") or []:
            span = p.get("span") if isinstance(p, dict) else None
            if isinstance(span, list) and span and isinstance(span[0], int):
                starts[str(p.get("item"))] = span[0]
        items = [set(part["items"]) for part in index["parts"]]
        firsts = [min((starts[i] for i in part["items"] if i in starts), default=0)
                  for part in index["parts"]]
        return items, firsts, starts

    @staticmethod
    def part_at(firsts: list[int], start) -> int:
        best = 1
        if not isinstance(start, int):
            return best
        for i, first in enumerate(firsts, 1):
            if first <= start:
                best = i
        return best

    def part_of(self, geometry, item: str) -> int:
        items, firsts, starts = geometry
        for i, held in enumerate(items, 1):
            if item in held:
                return i
        return self.part_at(firsts, starts.get(item))

    def advised(self, step: str, validate: list[str]) -> list[str]:
        """The validator line an agent is given: the turn's own check, and under rule 2 for
        an auditor or a skeptic also ``--gate-config``, so the agent's run of it names a
        skip statement anchored outside its skip and skip evidence from elsewhere (what the
        merge would drop); the turn accepts the file without it, so such a slip costs a
        statement or a flag, never the round."""
        if self.rule >= 2 and step in ("audit", "skeptic"):
            return [*validate, "--gate-config", self.gate_config]
        return validate

    def brief(self, step: str, k: int, part: int, parts: int, index_part: dict, out: str,
              inputs: list[str], validate: list[str]) -> str:
        lines = [
            f"## This call: the {step} of round {k}, part {part} of {parts}",
            "",
            f"- The rendering of this part: {self.m(index_part['file'])} (passages "
            f"[{index_part['first']}] to [{index_part['last']}]); its index is "
            f"{self.m(f'r{k}.render.json')}."]
        if self.rule >= 2 and isinstance(index_part.get("skips"), list):
            lines.append("- The skips this part shows: "
                         + (", ".join(index_part["skips"]) or "none") + ".")
        lines += [
            f"- Staged for you under {'/'.join(MIRROR)}/: " + ", ".join(inputs) + ".",
            f"- Write exactly one file: {self.m(out)}.",
            f"- Check it before you finish: "
            f"{self.validate_line(self.ruled(self.advised(step, validate)))}",
        ]
        return "\n".join(lines)

    def agent_step(self, state: dict, rd: dict, *, step: str, k: int, part: int, parts: int,
                   index_part: dict, out: str, inputs: list[str], validate: list[str]) -> bool:
        """One validated agent call of the round: reused when valid, re-called once when not."""
        rec = rd["steps"].setdefault(out, {"attempts": 0, "valid": False, "problems": []})
        if rec["valid"] and (self.auth / out).is_file():
            return True
        while rec["attempts"] <= OUTPUT_RETRIES:
            retry = rec["attempts"] > 0
            self.stage(inputs + ([out] if retry else []))
            prompt = self.text(STEP_PROMPT[step], {"CF_AUDIT_ROUND": k}) + "\n\n" + \
                self.brief(step, k, part, parts, index_part, out, inputs, validate)
            if retry:
                state["counts"]["retries"] += 1
                prompt += ("\n\n## Your last " + self.m(out) + " was invalid\n\nThe validator "
                           "said:\n" + "\n".join(rec["problems"]) + "\n\nWrite the whole file "
                           "again (your last one is staged) so that the check passes.")
            system = self.text("reviewer-system.md", {"CF_AUDIT_ROUND": k})
            code, raw = self.call(state, step=step, label=f"{out[:-5]}.a{rec['attempts'] + 1}",
                                  round_=k, prompt=prompt, system=system, part=part,
                                  parts=parts, out=out)
            if self.infra(code):
                raise Infra(code, f"the {step} call of round {k}, part {part}, failed as "
                                  f"infrastructure (exit {code})")
            rec["attempts"] += 1
            rd["raw"] = self.rel(raw)
            if self.collect(out):
                self.stage(inputs + [out])
                vcode, vout, verr = self.tool("inventory_audit.py", *self.ruled(validate))
            else:
                vcode, vout, verr = 1, f"INVALID: the call wrote no {self.m(out)}", ""
            if vcode == 0:
                rec.update(valid=True, problems=[])
                self.save(state)
                return True
            if vcode not in (1, 2):
                raise Refused(f"the validator failed (exit {vcode}): {verr.strip()[-300:]}")
            rec["problems"] = invalid_lines(vout, verr)
            self.save(state)
        return False

    def failed(self, state: dict, k: int, reason: str) -> dict:
        """The round as ``audit_failed``: approve, counted."""
        state["counts"]["audit_failed"] += 1
        state["events"].append({"event": "audit_failed", "round": k, "at": stamp(),
                                "reason": reason[:300]})
        self.log(f"round {k}: audit_failed: {reason[:300]}")
        previous = [f"r{k - 1}.verdicts.json"] if k > 1 else []
        self.stage(previous)
        args = ["merge", "--round", k, "--review-rounds", self.review_rounds, "--document",
                self.doc_rel, "--audit-failed", reason[:300]]
        if previous:
            args += ["--previous", self.m(previous[0])]
        args += ["--out", self.m(f"r{k}.verdicts.json"), "--result", "/".join(RESULT)]
        if self.rule >= 2:
            # the round's skip report, when the source still reads (else none)
            args += ["--gate-config", self.gate_config]
        self.need(self.tool("inventory_audit.py", *self.ruled(args)), "the audit_failed merge")
        self.collect(f"r{k}.verdicts.json")
        return self.read_auth(f"r{k}.verdicts.json")

    def merge(self, state: dict, k: int, inputs: list[str], args: list) -> dict:
        self.stage(inputs)
        code, out, err = self.tool("inventory_audit.py", *self.ruled([
            "merge", "--round", k, "--review-rounds", self.review_rounds, "--gate-config",
            self.gate_config, "--document", self.doc_rel, *args,
            "--out", self.m(f"r{k}.verdicts.json"), "--result", "/".join(RESULT)]))
        if code in (1, 2):
            return self.failed(state, k, "the merge refused the round's files: "
                                         + "; ".join(invalid_lines(out, err)[:5]))
        self.need((code, out, err), "the merge")
        self.collect(f"r{k}.verdicts.json")
        return self.read_auth(f"r{k}.verdicts.json")

    def round1(self, state: dict, rd: dict) -> dict:
        index = self.render(1, None)
        parts = index["parts"]
        audits = []
        for i, part in enumerate(parts, 1):
            out = f"r1.audit.part{i}.json"
            inputs = [part["file"], "r1.render.json"]
            validate = ["validate", "--kind", "audit", "--file", self.m(out), "--document",
                        self.doc_rel, "--render-index", self.m("r1.render.json"), "--part",
                        str(i)]
            if not self.agent_step(state, rd, step="audit", k=1, part=i, parts=len(parts),
                                   index_part=part, out=out, inputs=inputs, validate=validate):
                return self.failed(state, 1, f"the audit of part {i} stayed invalid: "
                                             + "; ".join(rd["steps"][out]["problems"][:3]))
            audits.append(out)
        audit_args = [a for name in audits for a in ("--audit", self.m(name))]
        self.stage(audits)
        code, out, err = self.tool("inventory_audit.py", *self.ruled([
            "strip", "--round", 1, "--document", self.doc_rel, *audit_args,
            "--out", self.m("r1.flags.json")]))
        if code in (1, 2):
            return self.failed(state, 1, "the audit parts do not hold together: "
                                         + "; ".join(invalid_lines(out, err)[:5]))
        self.need((code, out, err), "the strip")
        self.collect("r1.flags.json")
        skeptics = self.skeptic_steps(state, rd, 1, index, self.read_auth("r1.flags.json"), None)
        if skeptics is None:
            return self.failed(state, 1, "a skeptic part stayed invalid")
        return self.merge(state, 1, audits + ["r1.skeptic.json"],
                          ["--skeptic", self.m("r1.skeptic.json"), *audit_args])

    def repair_valid(self, k: int) -> bool:
        name, verdicts = f"r{k}.repair.json", f"r{k}.verdicts.json"
        if not (self.auth / name).is_file():
            return False
        self.stage([name, verdicts])
        code, _out, _err = self.tool("inventory_audit.py", *self.ruled([
            "validate", "--kind", "repair", "--file", self.m(name), "--document", self.doc_rel,
            "--verdicts", self.m(verdicts)]))
        return code == 0

    def round_later(self, state: dict, rd: dict, k: int) -> dict:
        prev, snap = f"r{k - 1}.verdicts.json", f"r{k - 1}.document.json"
        ops, repair, coll = f"r{k - 1}.ops.json", f"r{k - 1}.repair.json", f"r{k}.collateral.json"
        previous = self.read_auth(prev)
        index = self.render(k, snap)
        parts = index["parts"]
        has_ops = self.parses(ops)
        self.stage([snap, prev] + ([ops] if has_ops else []))
        cargs = ["collateral", "--round", k, "--snapshot", self.m(snap), "--document",
                 self.doc_rel, "--verdicts", self.m(prev), "--out", self.m(coll)]
        if has_ops:
            cargs += ["--ops", self.m(ops)]
        code, out, err = self.tool("inventory_audit.py", *self.ruled(cargs))
        if code != 0:
            raise StepFailed(f"the collateral failed (exit {code}): "
                             + "; ".join(invalid_lines(out, err)[:5]))
        self.collect(coll)
        has_repair = self.repair_valid(k - 1)
        if not has_repair and (self.auth / repair).is_file():
            state["counts"]["repair_invalid"] += 1
        geometry = self.geometry(index)
        _items, firsts, starts = geometry
        owed: dict[int, list[str]] = {i: [] for i in range(1, len(parts) + 1)}
        for v in previous.get("verdicts") or []:
            owed[self.part_at(firsts, v.get("start"))].append(v["id"])
        for entry in self.read_auth(coll).get("collateral") or []:
            brief = entry.get("after") or entry.get("before") or {}
            span = brief.get("span") if isinstance(brief, dict) else None
            item = entry.get("item")
            start = starts.get(str(item)) if item else None
            if start is None and isinstance(span, list) and span:
                start = span[0]
            owed[self.part_at(firsts, start)].append(entry["id"])
        chosen = [i for i in owed if owed[i]] or [1]
        checks = []
        for i in chosen:
            part = parts[i - 1]
            expect = f"r{k}.check.part{i}.expect.json"
            self.write_auth(expect, owed[i])
            out = f"r{k}.audit.part{i}.json"
            inputs = [part["file"], f"r{k}.render.json", f"r{k}.diff.md", coll, prev, snap,
                      expect] + ([repair] if has_repair else [])
            validate = ["validate", "--kind", "check", "--file", self.m(out), "--document",
                        self.doc_rel, "--previous", self.m(prev), "--collateral", self.m(coll),
                        "--expect", self.m(expect)]
            if not self.agent_step(state, rd, step="check", k=k, part=i, parts=len(parts),
                                   index_part=part, out=out, inputs=inputs, validate=validate):
                return self.failed(state, k, f"the check of part {i} stayed invalid: "
                                             + "; ".join(rd["steps"][out]["problems"][:3]))
            checks.append(out)
        check_args = [a for name in checks for a in ("--check", self.m(name))]
        tail = ["--previous", self.m(prev), "--collateral", self.m(coll)] + \
            (["--repair", self.m(repair)] if has_repair else [])
        support = [prev, coll] + ([repair] if has_repair else [])
        self.stage(checks + support)
        code, out, err = self.tool("inventory_audit.py", *self.ruled([
            "strip", "--round", k, "--document", self.doc_rel, *check_args, *tail,
            "--out", self.m(f"r{k}.flags.json")]))
        if code in (1, 2):
            return self.failed(state, k, "the check parts do not hold together: "
                                         + "; ".join(invalid_lines(out, err)[:5]))
        self.need((code, out, err), "the strip")
        self.collect(f"r{k}.flags.json")
        skeptics = self.skeptic_steps(state, rd, k, index, self.read_auth(f"r{k}.flags.json"),
                                      previous)
        if skeptics is None:
            return self.failed(state, k, "a skeptic part stayed invalid")
        return self.merge(state, k, checks + support + [f"r{k}.skeptic.json"],
                          ["--skeptic", self.m(f"r{k}.skeptic.json"), *check_args, *tail])

    def skeptic_steps(self, state: dict, rd: dict, k: int, index: dict, flags: dict,
                      previous: dict | None) -> list[str] | None:
        """One skeptic call per part with flags, disputes or (round 1) passages to hunt
        over, each seeing that part's flags only -- never the auditor's or checker's file;
        then the parts combined into ``r<k>.skeptic.json``."""
        parts = index["parts"]
        geometry = self.geometry(index)
        _items, firsts, _starts = geometry
        mine = {i: {"flags": [], "disputes": []} for i in range(1, len(parts) + 1)}
        # rule 2: a skip is the part's that shows it (the index), its flag with it
        owner = {row["id"]: row.get("part") or 1 for row in index.get("skips") or []}
        for flag in flags.get("flags") or []:
            if flag.get("skip"):
                mine[owner.get(flag["skip"], 1)]["flags"].append(flag)
            else:
                mine[self.part_of(geometry, flag["item"])]["flags"].append(flag)
        asked = {v["id"]: v.get("start") for v in (previous or {}).get("verdicts") or []}
        for dispute in flags.get("disputes") or []:
            mine[self.part_at(firsts, asked.get(dispute["verdict_id"]))]["disputes"].append(dispute)
        names = []
        for i, part in enumerate(parts, 1):
            held = mine[i]
            flagged = {f["item"] for f in held["flags"]}
            hunt = k == 1 and any(item not in flagged for item in part["formal"])
            if self.rule >= 2 and k == 1:
                # the skip hunt: every unflagged skip of the part over math no passage holds
                skips = {f["skip"] for f in held["flags"] if f.get("skip")}
                hunt = hunt or any(row["id"] not in skips and row.get("unheld_outside")
                                   for row in index.get("skips") or []
                                   if owner.get(row["id"]) == i)
            if not held["flags"] and not held["disputes"] and not hunt:
                continue
            own = f"r{k}.flags.part{i}.json"
            self.write_auth(own, {**{key: flags[key] for key in ("schema", "kind", "round",
                                                                  "source_id")}, **held})
            out = f"r{k}.skeptic.part{i}.json"
            inputs = [part["file"], f"r{k}.render.json", own]
            validate = ["validate", "--kind", "skeptic", "--file", self.m(out), "--document",
                        self.doc_rel, "--flags", self.m(own), "--render-index",
                        self.m(f"r{k}.render.json"), "--part", str(i)]
            if not self.agent_step(state, rd, step="skeptic", k=k, part=i, parts=len(parts),
                                   index_part=part, out=out, inputs=inputs, validate=validate):
                return None
            names.append(out)
        self.combine(k, names, flags)
        return names

    def combine(self, k: int, names: list[str], flags: dict) -> None:
        """The skeptic's parts as the one file the merge reads (under rule 2 with the skip
        hunt's ``examined_skips`` and ``missed_skips``, each skip once)."""
        parts = [self.read_auth(name) for name in names]
        missed, seen, examined = [], set(), []
        for p in parts:
            for row in p.get("missed") or []:
                key = (row.get("item"), row.get("verdict"))
                if key not in seen:
                    seen.add(key)
                    missed.append(row)
            for item in p.get("examined") or []:
                if item not in examined:
                    examined.append(item)
        summary = " | ".join(str(p.get("summary") or "").strip() for p in parts
                             if str(p.get("summary") or "").strip())
        combined = {
            "schema": flags["schema"], "kind": "skeptic", "round": k,
            "source_id": flags["source_id"],
            "verdicts": [v for p in parts for v in p.get("verdicts") or []],
            "missed": missed,
            "disputes": [d for p in parts for d in p.get("disputes") or []],
            "examined": examined,
            "per_spec_formal_estimate": sum(int(p.get("per_spec_formal_estimate") or 0)
                                            for p in parts),
            "summary": cap_words(summary or "no flags or disputes to judge: no skeptic call",
                                 SUMMARY_WORDS)}
        if self.rule >= 2:
            skips, missed_skips = [], []
            for p in parts:
                for sid in p.get("examined_skips") or []:
                    if sid not in skips:
                        skips.append(sid)
                for row in p.get("missed_skips") or []:
                    if all(row.get("skip") != r.get("skip") for r in missed_skips):
                        missed_skips.append(row)
            combined.update(examined_skips=skips, missed_skips=missed_skips)
        self.write_auth(f"r{k}.skeptic.json", combined)

    # ------------------------------- worker ---------------------------- #

    def worker(self, state: dict) -> int:
        pending = state.get("pending")
        if pending and pending.get("kind") == "revert":
            return self.restore(state, pending)
        revision = state.get("revision")
        tail = self.discussion_tail()
        if revision is None:
            # the first worker call, or a re-run of it the gate (or the precheck) refused
            prompt = self.text("worker.md")
            if tail:
                prompt += "\n\n" + self.gate_block(tail)
            label = "w1" if not state["rounds"] else f"r{state['round']}.gatefix"
            code, raw = self.call(state, step="revision" if state["rounds"] else None,
                                  label=label, round_=state["round"], prompt=prompt,
                                  system=self.text("worker-system.md"))
            if self.infra(code):
                raise Infra(code, f"the worker call failed as infrastructure (exit {code})")
            self.event(state, "worker", first=not state["rounds"])
            return self.emit(self.worker_contract(raw))
        k = revision["round"]
        last = (state.get("last") or {}).get("event")
        if revision["complete"]:
            # the revision was committed and came back without a review: refused
            revision["gatefails"] += 1
            if revision["gatefails"] >= GATE_RESETS:
                return self.reset(state, revision)
            revision.update(complete=False, gatefix=True)
            self.event(state, "gate_refused", after=last)
            self.save(state)
        verdicts = self.read_auth(f"r{k}.verdicts.json").get("verdicts") or []
        sessions = self.sessions(verdicts)
        revision["sessions"] = len(sessions)
        raw = None
        if revision.get("gatefix"):
            raw = self.revision_call(state, k, None, len(sessions), [], tail)
            revision["gatefix"] = False
        else:
            while revision["next"] < len(sessions):
                j = revision["next"]
                raw = self.revision_call(state, k, j + 1, len(sessions), sessions[j], tail)
                revision["next"] = j + 1
                self.save(state)
        if raw is None:     # every session ran before a turn that stopped short of this line
            raw = self.auth / "raw" / f"r{k}.revision.s{len(sessions)}.json"
        revision["complete"] = True
        self.event(state, "worker", revision=k)
        return self.emit(self.worker_contract(raw))

    def sessions(self, verdicts: list[dict]) -> list[list[str]]:
        """The revision's sessions, in verdict order: at most REPAIR_VERDICTS_PER_SESSION
        verdicts each (rule 1); under rule 2 at most that much weight, a skip verdict
        weighing max(1, its statements, the math paragraphs under it no passage holds) and
        any other 1, a heavier verdict a session of its own."""
        ids = [v["id"] for v in verdicts]
        if self.rule < 2:
            return [ids[i:i + REPAIR_VERDICTS_PER_SESSION]
                    for i in range(0, len(ids), REPAIR_VERDICTS_PER_SESSION)] or [[]]
        out: list[list[str]] = []
        load = 0
        for v in verdicts:
            weight = 1
            if v.get("verdict") == SKIP_VERDICT:
                weight = max(1, len(v.get("statements") or []), int(v.get("unheld") or 0))
            if not out or load + weight > REPAIR_VERDICTS_PER_SESSION:
                out.append([])
                load = 0
            out[-1].append(v["id"])
            load += weight
        return out or [[]]

    def gate_block(self, tail: str) -> str:
        return ("## THIS IS A RE-RUN — the validation gate refused your last commit\n\n"
                "The gate's output is in the discussion below. Fix exactly what it names, run "
                "the gate's --findings check until it passes, and commit again. Do not restart "
                "from scratch.\n\n--- prior discussion (most recent last) ---\n"
                f"{tail}\n--- end ---")

    def revision_call(self, state: dict, k: int, session: int | None, sessions: int,
                      ids: list[str], tail: str) -> Path:
        files = [f"r{k}.verdicts.json", f"r{k}.document.json", f"r{k}.collateral.json",
                 f"r{k}.ops.json", f"r{k}.repair.json"]
        self.stage(files)
        ops_check = self.ruled(["validate", "--kind", "ops", "--file", self.m(f"r{k}.ops.json"),
                                "--document", self.doc_rel, "--verdicts",
                                self.m(f"r{k}.verdicts.json")])
        repair_check = self.ruled(["validate", "--kind", "repair", "--file",
                                   self.m(f"r{k}.repair.json"), "--document", self.doc_rel,
                                   "--verdicts", self.m(f"r{k}.verdicts.json")])
        slots = {"CF_AUDIT_ROUND": k}
        lines = []
        if session is None:
            lines += ["## This session: the gate refused your last commit of this revision",
                      "",
                      f"- The round's verdicts are still {self.m(f'r{k}.verdicts.json')}; your "
                      f"ops and repair files so far are staged beside them. Fix what the gate "
                      f"names, keep the files, and commit again."]
        else:
            lines += [f"## This session: the revision of round {k}'s verdicts, session "
                      f"{session} of {sessions}", "",
                      f"- The verdicts: {self.m(f'r{k}.verdicts.json')}; this session applies "
                      + (", ".join(ids) if ids else "none") + " (any others belong to the "
                      "other sessions).",
                      f"- The snapshot they name: {self.m(f'r{k}.document.json')}."]
            if (self.auth / f"r{k}.collateral.json").is_file():
                lines.append(f"- The collateral a `revert` verdict names: "
                             f"{self.m(f'r{k}.collateral.json')}.")
        lines += [f"- Your files: {self.m(f'r{k}.ops.json')} and {self.m(f'r{k}.repair.json')}; "
                  f"earlier sessions' entries are in them: keep them and add yours.",
                  f"- Check them before you finish: {self.validate_line(ops_check)} and, at "
                  f"the last session, {self.validate_line(repair_check)}"]
        prompt = "\n\n".join([self.text("worker.md"), self.text("worker-revision.md", slots),
                              "\n".join(lines)])
        if tail:
            if session is None:
                prompt += "\n\n" + self.gate_block(tail)
            else:
                prompt += ("\n\n--- prior discussion (most recent last) ---\n"
                           f"{tail}\n--- end ---")
        label = f"r{k}.revision." + ("gatefix" if session is None else f"s{session}")
        code, raw = self.call(state, step="revision", label=label, round_=k, prompt=prompt,
                              system=self.text("worker-system.md", slots))
        for name in (f"r{k}.ops.json", f"r{k}.repair.json"):
            self.collect(name)
        if self.infra(code):
            raise Infra(code, f"the revision call failed as infrastructure (exit {code})")
        return raw

    def reset(self, state: dict, revision: dict) -> int:
        """Back to the round's reviewed commit, with no agent call."""
        k = revision["round"]
        head = state["rounds"][str(k)]["head"]
        done = self.git("reset", "-q", "--hard", head)
        if done.returncode != 0:
            raise Refused(f"could not reset to {head[:12]}: {done.stderr.strip()[:300]}")
        self.git("clean", "-q", "-fd", "-e", MIRROR[0])
        revision.update(complete=True, gatefix=False)
        state["counts"]["gate_resets"] += 1
        self.event(state, "reset", head=head)
        self.log(f"the revision of round {k} was refused {revision['gatefails']} times; reset "
                 f"to the reviewed commit {head[:12]}")
        return self.emit(self.worked(
            f"audit: the revision of round {k}'s verdicts was refused {revision['gatefails']} "
            f"times; the bridge reset it to the reviewed commit (no agent call)",
            "the next review finds no progress and approves"))

    def restore(self, state: dict, pending: dict) -> int:
        """The revert: the snapshot's bytes, committed, with no agent call."""
        path = self.root / pending["snapshot"]
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise Refused(f"the snapshot {path} cannot be read: {exc}") from None
        if sha256_bytes(data) != pending["sha256"]:
            raise Refused(f"the snapshot {path} is not the bytes round {pending['to']} reviewed")
        target = self.doc_path()
        for part in (self.worktree / "findings", target.parent, target):
            if part.is_symlink():
                raise Refused(f"{part} is a symlink; the snapshot is not written through it")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        added = self.git("add", "--", self.doc_rel)
        if added.returncode != 0:
            raise Refused(f"git add failed: {added.stderr.strip()[:300]}")
        if self.git("diff", "--cached", "--quiet").returncode != 0:
            done = self.git("commit", "-q", "-m", f"Audit: revert to round {pending['to']}'s "
                                                  f"reviewed snapshot, task {self.task}")
            if done.returncode != 0:
                raise Refused(f"git commit failed: {done.stderr.strip()[:300]}")
        self.event(state, "restored", to=pending["to"])
        return self.emit(self.worked(
            f"audit: reverted to round {pending['to']}'s reviewed snapshot (no agent call)",
            f"round {pending['round']}'s check found harm; the reviewer approves the snapshot"))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="audit_turn.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("role", choices=("worker", "reviewer"))
    ap.add_argument("--root", required=True, help="the tend root")
    ap.add_argument("--prompts", required=True, help="the rendered prompts")
    ap.add_argument("--host", default="claude")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--bypass", default="0")
    ap.add_argument("--tools", required=True, help="the bridge's allowlist for this role")
    ap.add_argument("--err", default=None, help="the bridge's log")
    return ap


def main(argv: list[str] | None = None, runner: Runner | None = None,
         env: dict | None = None, worktree: Path | None = None) -> int:
    args, _rest = parser().parse_known_args(argv)     # tend may append --resume
    try:
        turn = Turn(args, runner or Runner(), env=env, worktree=worktree)
    except Config as exc:
        print(f"audit_turn: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except NoPrompt as exc:
        print(f"audit_turn: {exc}", file=sys.stderr)
        return EXIT_NO_PROMPT
    try:
        return turn.run()
    except NoPrompt as exc:
        turn.log(str(exc))
        return EXIT_NO_PROMPT
    except Infra as exc:
        turn.log(f"{exc}; no contribution reported, so tend retries this worktree")
        return exc.code
    except Refused as exc:
        turn.log(f"the turn could not finish: {exc}")
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
