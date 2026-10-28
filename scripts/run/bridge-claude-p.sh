#!/usr/bin/env bash
# Ported from an earlier local orchestrator's claude-worker.sh and
# claude-reviewer.sh (one script, role-driven; not in this repo), with its env
# names FF_ASYNC_* renamed to tend's TEND_*.
#
# The LOCAL default bridge: `claude -p` bills the operator's Claude subscription, so
# the local smoke test needs no API credit.  (The 2026-09-05 failure was the opposite
# choice: bridge-tend-agent.sh on an API key with no credit -- 20 requests, 18 retries,
# HTTP 404.  Do not make tend-agent the default without checking the balance.)
#
#   tend worker_agent_command: [ .../bin/bridge-claude-p.sh, worker ]
#     resume_argv: [ --resume ]
#
# stdout MUST carry ONLY the tend contract JSON; extract_final_json.py enforces that.
#
# Environment:
#   CF_ROLE                worker|reviewer  (default: $1, then "worker")
#   CF_BRIDGE_ROOT         root holding prompts/   (default: this script's ..)
#   CF_PROMPT_DIR          prompt dir              (default: $CF_BRIDGE_ROOT/prompts)
#   CF_MODEL               --model id (e.g. claude-opus-5); unset uses the CLI default
#   CF_WORKER_TOOLS        --allowedTools for the worker
#   CF_REVIEWER_TOOLS      --allowedTools for the reviewer (read-only surface; a locate
#                          run's default git is read-only by subcommand)
#   CF_GATE_CMD            the gate command; the locate gate (verify_locate.py) makes this a
#                          locate run, whose revision prompt lets the worker decline feedback
#                          its rules forbid (status needs_review), as bridge-agentbox.sh's does
#   TEND_AGENT_SESSION_DIR where the claude session id and revision prompt live
#   TEND_TASK_ID / TEND_WORKTREE_PATH / TEND_WORKTREE_ID  substituted into the prompt
#   CF_SLOT_DIR / CF_SLOTS / CF_AGENT_TIMEOUT  the session slots and the call's timeout
#                          (agent_call.py: ~/.cache/cf-agent-slots, 15, 45m)
set -euo pipefail

ROLE="${CF_ROLE:-worker}"
RESUME=0
for arg in "$@"; do
  case "$arg" in
    worker|reviewer) ROLE="$arg" ;;
    --resume) RESUME=1 ;;
  esac
done
[ "${TEND_AGENT_RESUME:-0}" = "1" ] && RESUME=1

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${CF_BRIDGE_ROOT:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
PROMPTS="${CF_PROMPT_DIR:-$ROOT/prompts}"
SESSION_DIR="${TEND_AGENT_SESSION_DIR:-$PWD/.tend}"
mkdir -p "$SESSION_DIR"
SESSION_ID_FILE="$SESSION_DIR/claude-session-id"

PROMPT="$(sed -e "s#\$TEND_TASK_ID#${TEND_TASK_ID:-unknown}#g" \
              -e "s#\$TEND_WORKTREE_PATH#${TEND_WORKTREE_PATH:-$PWD}#g" \
              -e "s#\$TEND_WORKTREE_ID#${TEND_WORKTREE_ID:-}#g" "$PROMPTS/$ROLE.md")"

LOCATE=0
case "${CF_GATE_CMD:-}" in *"/verify_locate.py --"*) LOCATE=1 ;; esac

CLAUDE_ARGS=(--output-format json)
if [ "$ROLE" = reviewer ]; then
  # The reviewer judges the branch; it does not edit it.
  REVIEWER_DEFAULT="Read,Glob,Grep,Bash(git:*)"
  if [ "$LOCATE" = 1 ]; then
    REVIEWER_DEFAULT="Read,Glob,Grep,Bash(git diff:*),Bash(git show:*),Bash(git log:*),Bash(git status:*)"
  fi
  CLAUDE_ARGS+=(--allowedTools "${CF_REVIEWER_TOOLS:-$REVIEWER_DEFAULT}")
else
  CLAUDE_ARGS+=(--permission-mode acceptEdits
                --allowedTools "${CF_WORKER_TOOLS:-Read,Glob,Grep,Edit,Write,Bash(git:*)}")
fi
[ -n "${CF_MODEL:-}" ] && CLAUDE_ARGS+=(--model "$CF_MODEL")
if [ -r "$PROMPTS/$ROLE-system.md" ]; then
  CLAUDE_ARGS+=(--append-system-prompt "$(cat "$PROMPTS/$ROLE-system.md")")
fi

if [ "$RESUME" = 1 ]; then
  [ -s "$SESSION_ID_FILE" ] && CLAUDE_ARGS+=(--resume "$(cat "$SESSION_ID_FILE")")
  # Same silent-requeue trap as the agentbox bridge: on rework the feedback is the
  # only thing that stops the agent repeating its mistake. tend writes discussion.md;
  # the ff-orchestrator wrote revision-prompt.md. Prefer whichever exists.
  if [ -s "$SESSION_DIR/revision-prompt.md" ]; then
    PROMPT="$(cat "$SESSION_DIR/revision-prompt.md")"
  elif [ -s "$PWD/.tend/discussion.md" ]; then
    REVISE="Fix exactly what the feedback names; re-run the validation gate before finishing."
    if [ "$LOCATE" = 1 ]; then
      # a plain string, not a heredoc in $(...), which bash 3.2 mis-parses
      REVISE="Fix what the feedback names, without restarting from scratch. Your task prompt and the
validation gate are the rules; the reviewer checks your work against them and can misread
them. If the feedback asks for something your prompt rules out or the gate refuses, do not
change a verdict or a judgment to make the request satisfiable: leave that part as it was,
quote the rule (and the gate's refusal, if you ran it) in your summary, and finish with
status \`needs_review\`. Re-run the validation gate before finishing."
    fi
    PROMPT="$PROMPT

## THIS IS A REVISION — a reviewer rejected the previous attempt
$REVISE
--- prior discussion (most recent last) ---
$(tail -c 6000 "$PWD/.tend/discussion.md")
--- end ---"
  fi
fi

# agent_call.py takes one of the per-user session slots (flock, waiting for one),
# runs the call under CF_AGENT_TIMEOUT (45m) in its own process group, and keeps
# the JSON envelope under $ROOT/usage/; a timeout (124) or an empty or 409
# envelope (75) is an infrastructure failure, so no contract JSON is printed and
# the orchestrator retries.
RAW="$(mktemp)"
trap 'rm -f "$RAW"' EXIT
CALLRC=0
python3 "$SCRIPT_DIR/agent_call.py" --role "$ROLE" --raw "$RAW" --usage-dir "$ROOT/usage" \
  --task-id "${TEND_TASK_ID:-}" -- claude "${CLAUDE_ARGS[@]}" -p "$PROMPT" || CALLRC=$?
case "$CALLRC" in
  124|75|64) echo "bridge-claude-p: the agent call failed as infrastructure (exit $CALLRC)" >&2
             exit "$CALLRC" ;;
esac
python3 "$SCRIPT_DIR/extract_final_json.py" "$ROLE" "$SESSION_ID_FILE" <"$RAW"
[ "$CALLRC" = 0 ] || exit "$CALLRC"
