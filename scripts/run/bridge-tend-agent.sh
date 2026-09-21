#!/usr/bin/env bash
# STUB: the stock tend-agent bridge (API key through the gateway).
#
# This is the third backend named in the design decisions record ("Fleet substrate"):
# tend's own agent, billed to an Anthropic API key rather than a subscription.  It
# is the only backend whose spend tend can actually see (`tend-control budget
# --max-cost` counts tend-agent sessions only; agentbox hides the token and reports
# "aggregate usage: input=0"), so it is the right choice once credit exists.
#
# IT IS NOT THE DEFAULT, deliberately.  On 2026-09-05 this path was used with a key
# that had no credit: 20 requests, 18 retries, HTTP 404, no work done.  Check the
# balance and run the 1/1 smoke test before pointing a 763-task run at it.
#
# Environment:
#   ANTHROPIC_API_KEY (required)   ANTHROPIC_BASE_URL (optional gateway)
#   CF_ROLE worker|reviewer        CF_MODEL / CF_REASONING_EFFORT
#   CF_BRIDGE_ROOT (prompts/)      TEND_* as provided by tend
set -euo pipefail

ROLE="${CF_ROLE:-${1:-worker}}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${CF_BRIDGE_ROOT:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
PROMPTS="${CF_PROMPT_DIR:-$ROOT/prompts}"

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "bridge-tend-agent: ANTHROPIC_API_KEY is unset -- use bridge-claude-p.sh or bridge-agentbox.sh" >&2
  exit 78
fi

# TODO(v1): tend-agent's own CLI contract (flags, prompt-registry variant, how the
# role is selected) has not been exercised from this machine, so the invocation below
# is a placeholder.  Confirm it against your own `tend-agent --help` and a 1/1
# smoke test before using this backend for a real run.
exec tend-agent \
  --role "$ROLE" \
  --prompt-file "$PROMPTS/$ROLE.md" \
  ${CF_MODEL:+--model "$CF_MODEL"} \
  ${CF_REASONING_EFFORT:+--reasoning-effort "$CF_REASONING_EFFORT"} \
  "$@"
