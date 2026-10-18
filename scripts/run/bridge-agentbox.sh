#!/usr/bin/env bash
# Ported from the v1 campaign's tao-diff-tend/bin/{worker,reviewer}-agent.sh and the
# codex variants in its tao-diff-tend-gpt and tao-attack-{vac,hunt}-tend roots.
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
#   CF_REVIEWER_TOOLS      reviewer allowlist         (default: Read,Glob,Grep,Bash(git:*)
#                          plus the same CF_GATE_CMD entry: both reviewer prompts tell
#                          the reviewer to run the gate, and claude -p denies an
#                          unlisted Bash command, so without it a reviewer either
#                          approved unchecked or rejected sound work it could not check.)
#   CF_CODEX_SANDBOX       codex --sandbox mode       (default: workspace-write)
#   CF_ALLOW_SANDBOX_BYPASS=1  run with approvals and the sandbox OFF. An explicit
#                          opt-in, NOT the default: the run host holds the companion
#                          checkouts, and "these repositories are READ-ONLY" was a
#                          sentence in a system prompt. `cf run` records in run.yaml
#                          (run.agents.permission_bypass) whether the STAGED WRAPPER
#                          asks for it; `cf plan-run --allow-sandbox-bypass` is what
#                          stages it, so a bypass exported in the shell that launches
#                          tend here instead is not in that record.
#                          The bypass= line this script appends to
#                          $ROOT/agentbox-<role>.err is the record that is.
#   CF_PERMISSION_FLAGS    claude permission flags, honoured only WITH that opt-in
#   CF_GATE_DIR            out-of-tree gate directory (cf run rsyncs the gate there)
#   CF_GATE_CMD            gate command normalize_result.py runs for the observable-state
#                          fallback (default: python3 $CF_GATE_DIR/verify_findings.py, else
#                          python3 scripts/verify_findings.py)
#   CF_FINDINGS_GLOB       findings glob for that fallback (default: findings/*.json; a
#                          locate run's nested layout is findings/$TEND_TASK_ID/*.json)
#   CF_EXTRA_TOOLS         allowlist entries appended for the WORKER only (a locate run's
#                          two host tools, Bash(python3 <gate>/corpus_search.py:*), ...)
#   CF_SLOT_DIR, CF_SLOTS  the per-user session-slot directory and its size (default
#                          ~/.cache/cf-agent-slots, 15): agent_call.py takes a slot with
#                          flock before the agent call and WAITS for one (the account
#                          allows 16 sessions per Unix user, across every root)
#   CF_AGENT_TIMEOUT       the agent call's timeout (default 45m); an expiry, an empty
#                          envelope and a 409 exit non-zero so tend retries the worktree:
#                          an infrastructure failure is never a verdict
#   CF_ATTEMPT_CAP         genuine reviewer rejections after which the task ends
#                          UNRESOLVED (attempt_cap.py; unset or 0 = off, a locate run sets
#                          3), with CF_TASK_MANIFESTS the gate's tasks/ directory
# Claude's JSON envelope is KEPT, outside the worktree, under $ROOT/usage/ (one file
# per call), for the spend watchdog and the cost cross-check; the counter is
# $ROOT/attempts/<task-id>.json.
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
PROFILE="${CF_AGENTBOX_PROFILE:-${USER:-}}"
[ -n "$PROFILE" ] || { echo "bridge-agentbox: no agentbox profile -- set CF_AGENTBOX_PROFILE" >&2; exit 78; }
ERR="$ROOT/agentbox-$ROLE.err"
BINDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for helper in agent_call.py normalize_result.py; do
  [ -r "$BINDIR/$helper" ] || {
    echo "bridge-agentbox: missing helper $BINDIR/$helper (cf run ships it beside the bridge)" >&2
    exit 78; }
done
CAP="${CF_ATTEMPT_CAP:-0}"
if [ "$CAP" != 0 ] && [ ! -r "$BINDIR/attempt_cap.py" ]; then
  echo "bridge-agentbox: CF_ATTEMPT_CAP=$CAP but $BINDIR/attempt_cap.py is missing" >&2; exit 78
fi

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
#
# The REVIEWER's default permits the same gate entry: both reviewer prompts render
# the gate command and tell the reviewer to check its output (a locate reviewer's
# "the gate passes" is one of its four checks), and the gate only reads.  Without
# it `claude -p` denies the call, and the reviewer either approves without the
# check or requests changes because it cannot run it (field defect P1).
#
# CF_GATE_CMD is what the generated wrapper actually stages (CF_GATE_DIR is not),
# so the allowlist entry is derived from it: the program, without its --flags --
# exactly the prefix of every gate invocation the prompts render.
GATE_TOOL=""
case "${CF_GATE_CMD:-}" in
  "" | *,*) ;;    # unset, or a path an --allowedTools list cannot carry anyway
  *) GATE_TOOL=",Bash(${CF_GATE_CMD%% --*}:*)" ;;
esac
if [ "$ROLE" = reviewer ]; then
  # The gate entry above: the gate reads, and its one write is the append-only
  # gate log outside the worktree. A reviewer that could not verify requested
  # changes, and that counted as a genuine rejection towards the attempt cap.
  TOOLS="${CF_ALLOWED_TOOLS:-${CF_REVIEWER_TOOLS:-Read,Glob,Grep,Bash(git:*)$GATE_TOOL}}"
else
  TOOLS="${CF_ALLOWED_TOOLS:-${CF_WORKER_TOOLS:-Read,Glob,Grep,Edit,Write,Bash(git:*)$GATE_TOOL}}"
  # A locate worker's host tools (corpus_search.py, corpus_show.py), by absolute path.
  [ -n "${CF_EXTRA_TOOLS:-}" ] && TOOLS="$TOOLS,$CF_EXTRA_TOOLS"
fi
BYPASS="${CF_ALLOW_SANDBOX_BYPASS:-0}"
printf 'bridge-agentbox: role=%s host=%s bypass=%s tools=%s\n' \
  "$ROLE" "$HOST" "$BYPASS" "$TOOLS" >>"$ERR"

# THE ATTEMPT CAP. A task a reviewer keeps rejecting used to loop until the run was
# stopped. At the cap the worker side writes the task's UNRESOLVED documents and
# commits them without an agent call, and the reviewer side approves exactly those
# mechanically; exit 10 from attempt_cap.py means "not capped: call the agent".
if [ "$CAP" != 0 ] && [ -n "${TEND_TASK_ID:-}" ]; then
  CAPPED="$(python3 "$BINDIR/attempt_cap.py" "$ROLE" --root "$ROOT" --task "$TEND_TASK_ID" \
              --cap "$CAP" --worktree "$PWD" 2>>"$ERR")"
  CAPRC=$?
  if [ "$CAPRC" = 0 ]; then printf '%s\n' "$CAPPED"; exit 0; fi
  if [ "$CAPRC" != 10 ]; then
    echo "bridge-agentbox: the attempt cap could not end task $TEND_TASK_ID (exit $CAPRC)" >&2
    exit 1
  fi
fi

RESULT="$PWD/.tend/agent_result.json"
mkdir -p "$PWD/.tend"; rm -f "$RESULT"
RAW="$(mktemp)"
trap 'rm -f "$RAW"' EXIT
# agent_call.py takes a session slot (flock, waiting), runs the call under
# CF_AGENT_TIMEOUT in its own process group, keeps the envelope under usage/ and
# exits 124 (timeout) or 75 (empty or 409) for an infrastructure failure.
CALL=(python3 "$BINDIR/agent_call.py" --role "$ROLE" --raw "$RAW" --err "$ERR"
      --usage-dir "$ROOT/usage" --task-id "${TEND_TASK_ID:-}" --)

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
  # agent_call.py gives the agent an empty stdin, which is what keeps codex from
  # waiting on it (it hung the first attempt).
  "${CALL[@]}" agentbox host codex --profile "$PROFILE" -- "${CODEX_ARGS[@]}" \
    "$SYS

---

$PROMPT"
  CALLRC=$?
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
  "${CALL[@]}" agentbox host claude --profile "$PROFILE" -- "${CLAUDE_ARGS[@]}"
  CALLRC=$?
fi
case "$CALLRC" in
  124|75|64)
    echo "bridge-agentbox: the agent call failed as infrastructure (exit $CALLRC; see $ERR);" \
         "no contribution reported, so tend retries this worktree" >&2
    exit "$CALLRC" ;;
esac

OUT="$(python3 "$BINDIR/normalize_result.py" "$ROLE" "$RESULT" "$RAW")"
NORMRC=$?
printf '%s\n' "$OUT"
if [ "$ROLE" = reviewer ] && [ "$CAP" != 0 ] && [ -n "${TEND_TASK_ID:-}" ]; then
  # Only a request_changes from a call that completed counts; attempt_cap.py checks both.
  printf '%s' "$OUT" | python3 "$BINDIR/attempt_cap.py" record --root "$ROOT" \
    --task "$TEND_TASK_ID" --raw "$RAW" >/dev/null 2>>"$ERR" || true
fi
exit "$NORMRC"
