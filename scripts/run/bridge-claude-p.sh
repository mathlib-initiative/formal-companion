#!/usr/bin/env bash
# Ported from /Users/jack/Desktop/ff-live/orch/bin/claude-worker.sh and
# claude-reviewer.sh (one script, role-driven), with the ff-orchestrator env names
# FF_ASYNC_* renamed to tend's TEND_*.
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
#   CF_REVIEWER_TOOLS      --allowedTools for the reviewer (read-only surface)
#   TEND_AGENT_SESSION_DIR where the claude session id and revision prompt live
#   TEND_TASK_ID / TEND_WORKTREE_PATH / TEND_WORKTREE_ID  substituted into the prompt
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

CLAUDE_ARGS=(--output-format json)
if [ "$ROLE" = reviewer ]; then
  # The reviewer judges the branch; it does not edit it.
  CLAUDE_ARGS+=(--allowedTools "${CF_REVIEWER_TOOLS:-Read,Glob,Grep,Bash(git:*)}")
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
    PROMPT="$PROMPT

## THIS IS A REVISION — a reviewer rejected the previous attempt
Fix exactly what the feedback names; re-run the validation gate before finishing.
--- prior discussion (most recent last) ---
$(tail -c 6000 "$PWD/.tend/discussion.md")
--- end ---"
  fi
fi

claude "${CLAUDE_ARGS[@]}" -p "$PROMPT" \
  | python3 "$SCRIPT_DIR/extract_final_json.py" "$ROLE" "$SESSION_ID_FILE"
