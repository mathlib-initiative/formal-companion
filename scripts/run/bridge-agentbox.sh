#!/usr/bin/env bash
# Ported from scratchpad/ffmain/tao-diff-tend/bin/{worker,reviewer}-agent.sh and the
# codex variants in .../tao-diff-tend-gpt/bin and .../tao-attack-{vac,hunt}-tend/bin.
# Those five roots differed only in (a) role, (b) agentbox host claude|codex, (c) the
# model id, (d) the tend root path -- so this is one script driven by CF_* env.
#
#   tend worker_agent_command: [ .../bin/bridge-agentbox.sh, worker ]
#   tend reviewer_agent_command: [ .../bin/bridge-agentbox.sh, reviewer ]
#
# stdout MUST carry ONLY the tend contract JSON. Everything else goes to stderr or
# to $ROOT/agentbox-<role>.err, because tend parses stdout with a StrictModel and a
# single stray line loses the whole contribution.
#
# Environment (all optional except where noted):
#   CF_ROLE                worker|reviewer            (default: $1, then "worker")
#   CF_BRIDGE_ROOT         tend root holding prompts/ and bin/   (default: this script's ..)
#   CF_AGENT_HOST          claude|codex               (default: claude)
#   CF_AGENTBOX_PROFILE    agentbox profile name      (default: $USER)
#   CF_MODEL               model id passed to the host (claude: --model, codex: -m)
#   CF_PROMPT_DIR          prompt directory           (default: $CF_BRIDGE_ROOT/prompts)
#   CF_ALLOWED_TOOLS       claude --allowedTools list (default: the role allowlist below)
#   CF_WORKER_TOOLS        worker allowlist           (default: Read,Glob,Grep,Edit,Write,Bash(git:*)
#                          plus the CF_GATE_CMD program itself -- the worker prompt
#                          tells the worker to run the gate before it commits, and an
#                          allowlist that denied that would cost a rework turn per
#                          task. Nothing wider: python3 in general is NOT allowlisted.)
#   CF_REVIEWER_TOOLS      reviewer allowlist         (default: Read,Glob,Grep,Bash(git:*))
#   CF_CODEX_SANDBOX       codex --sandbox mode       (default: workspace-write)
#   CF_ALLOW_SANDBOX_BYPASS=1  run with approvals and the sandbox OFF. An explicit
#                          opt-in, NOT the default: the run host holds the companion
#                          checkouts, and "these repositories are READ-ONLY" was a
#                          sentence in a system prompt. `cf run` records in run.yaml
#                          (run.agents.permission_bypass) whether the STAGED WRAPPER
#                          asks for it; nothing stages it today, so a bypass exported
#                          in the shell that launches tend here is not in that record.
#                          The bypass= line this script appends to
#                          $ROOT/agentbox-<role>.err is the record that is.
#   CF_PERMISSION_FLAGS    claude permission flags, honoured only WITH that opt-in
#   CF_GATE_DIR            out-of-tree gate directory (cf run rsyncs the gate there)
#   CF_GATE_CMD            gate command normalize_result.py runs for the observable-state
#                          fallback (default: python3 $CF_GATE_DIR/verify_findings.py, else
#                          python3 scripts/verify_findings.py)
#   CF_FINDINGS_GLOB       findings glob for that fallback (default: findings/*.json)
# tend provides TEND_TASK_ID, TEND_WORKTREE_PATH, TEND_WORKTREE_ID; they are substituted
# into the prompt text wherever it writes $TEND_TASK_ID etc.
#
# NOTE ON `--resume`: tend appends its resume_argv (`--resume`) on a rework turn. The
# agentbox hosts have no resumable session here, so the flag is accepted and ignored;
# the rework context comes from .tend/discussion.md below.
set -uo pipefail

ROLE="${CF_ROLE:-${1:-worker}}"
case "$ROLE" in worker|reviewer) ;; --resume) ROLE="${CF_ROLE:-worker}" ;; *)
  echo "bridge-agentbox: unknown role '$ROLE' (want worker|reviewer)" >&2; exit 64 ;;
esac

ROOT="${CF_BRIDGE_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
PROMPTS="${CF_PROMPT_DIR:-$ROOT/prompts}"
HOST="${CF_AGENT_HOST:-claude}"
PROFILE="${CF_AGENTBOX_PROFILE:-${USER:-jack}}"
ERR="$ROOT/agentbox-$ROLE.err"

for f in "$PROMPTS/$ROLE-system.md" "$PROMPTS/$ROLE.md"; do
  [ -r "$f" ] || { echo "bridge-agentbox: missing prompt $f" >&2; exit 66; }
done
SYS="$(cat "$PROMPTS/$ROLE-system.md")"
PROMPT="$(sed -e "s#\$TEND_TASK_ID#${TEND_TASK_ID:-}#g" \
              -e "s#\$TEND_WORKTREE_PATH#${TEND_WORKTREE_PATH:-$PWD}#g" \
              -e "s#\$TEND_WORKTREE_ID#${TEND_WORKTREE_ID:-}#g" "$PROMPTS/$ROLE.md")"

# THE SILENT-REQUEUE TRAP.
# On rework tend returns the worktree to pending with the reviewer's feedback in
# .tend/discussion.md and re-invokes this command. Without the block below the agent
# re-runs the ORIGINAL prompt, never learns why it was rejected, repeats the same
# mistake and the task loops until the run is stopped. (The sibling trap is in
# normalize_result.py: a contribution whose `validation` field is a sentence instead of
# list[ValidationEvidence] is rejected by tend's StrictModel and the finished work is
# silently returned to the queue -- that is why nothing here prints to stdout.)
if [ "$ROLE" = worker ] && [ -s "$PWD/.tend/discussion.md" ]; then
  PRIOR="$(tail -c 6000 "$PWD/.tend/discussion.md")"
  PROMPT="$PROMPT

## THIS IS A REVISION — a reviewer rejected the previous attempt

Read the feedback below and FIX EXACTLY WHAT IT NAMES. Do not restart from scratch and do
not re-litigate the reviewer's judgement; if you disagree, address the point explicitly in
your summary. Re-run the validation gate before finishing.

--- prior discussion (most recent last) ---
$PRIOR
--- end ---"
fi

# The observable-state fallback in normalize_result.py must run the SAME gate tend
# runs, and that gate lives OUTSIDE the worktree (a worker with Write and Bash(git:*)
# could otherwise edit an in-tree gate and pass it twice). cf run rsyncs it to
# $CF_GATE_DIR; the tend config passes that through.
if [ -z "${CF_GATE_CMD:-}" ] && [ -n "${CF_GATE_DIR:-}" ]; then
  export CF_GATE_CMD="python3 $CF_GATE_DIR/verify_findings.py"
fi

# DENY BY DEFAULT, like bridge-claude-p.sh.  Both hosts used to be launched with
# their permission and sandbox checks off -- the codex path unconditionally -- on a
# box that holds the companion checkouts and the copyrighted source text, so
# "these repositories are READ-ONLY" (worker-system.md) was enforced by nothing
# but the prompt the agent had just been told to read.
#
# The worker's default also permits the GATE COMMAND, and only that: worker.md
# says "run the validation gate yourself and fix anything it reports" before
# committing, so a default that denied it would send every task back for a rework
# turn it did not need. It is the gate script by absolute path, not Bash(python3:*)
# -- the whole point of the out-of-tree gate is that the worker cannot substitute
# its own.
if [ "$ROLE" = reviewer ]; then
  TOOLS="${CF_ALLOWED_TOOLS:-${CF_REVIEWER_TOOLS:-Read,Glob,Grep,Bash(git:*)}}"
else
  # CF_GATE_CMD is what the generated wrapper actually stages (CF_GATE_DIR is not),
  # so the allowlist entry is derived from it: the program, without its --flags.
  GATE_TOOL=""
  case "${CF_GATE_CMD:-}" in
    "" | *,*) ;;    # unset, or a path an --allowedTools list cannot carry anyway
    *) GATE_TOOL=",Bash(${CF_GATE_CMD%% --*}:*)" ;;
  esac
  TOOLS="${CF_ALLOWED_TOOLS:-${CF_WORKER_TOOLS:-Read,Glob,Grep,Edit,Write,Bash(git:*)$GATE_TOOL}}"
fi
BYPASS="${CF_ALLOW_SANDBOX_BYPASS:-0}"
printf 'bridge-agentbox: role=%s host=%s bypass=%s tools=%s\n' \
  "$ROLE" "$HOST" "$BYPASS" "$TOOLS" >>"$ERR"

RESULT="$PWD/.tend/agent_result.json"
mkdir -p "$PWD/.tend"; rm -f "$RESULT"
RAW="$(mktemp)"
trap 'rm -f "$RAW"' EXIT

if [ "$HOST" = codex ]; then
  # codex takes one prompt argument and no system-prompt flag, so the system block is
  # prepended; `printf '' |` keeps it from waiting on stdin (it hung the first attempt).
  CODEX_ARGS=(exec --skip-git-repo-check -C "$PWD")
  if [ "$BYPASS" = 1 ]; then
    CODEX_ARGS+=(--dangerously-bypass-approvals-and-sandbox)
  else
    # workspace-write keeps the agent's writes in the worktree it was given, which
    # is exactly the invariant: the companion checkouts beside it are read-only.
    CODEX_ARGS+=(--sandbox "${CF_CODEX_SANDBOX:-workspace-write}")
  fi
  [ -n "${CF_MODEL:-}" ] && CODEX_ARGS+=(-m "$CF_MODEL")
  printf '' | agentbox host codex --profile "$PROFILE" -- "${CODEX_ARGS[@]}" \
    "$SYS

---

$PROMPT" >"$RAW" 2>>"$ERR"
else
  CLAUDE_ARGS=(-p "$PROMPT" --append-system-prompt "$SYS" --output-format json)
  if [ "$BYPASS" = 1 ]; then
    # shellcheck disable=SC2206
    CLAUDE_ARGS+=(${CF_PERMISSION_FLAGS---dangerously-skip-permissions})
  else
    CLAUDE_ARGS+=(--permission-mode acceptEdits)
  fi
  CLAUDE_ARGS+=(--allowedTools "$TOOLS")
  [ -n "${CF_MODEL:-}" ] && CLAUDE_ARGS+=(--model "$CF_MODEL")
  agentbox host claude --profile "$PROFILE" -- "${CLAUDE_ARGS[@]}" >"$RAW" 2>>"$ERR"
fi

python3 "$(dirname -- "${BASH_SOURCE[0]}")/normalize_result.py" "$ROLE" "$RESULT" "$RAW"
