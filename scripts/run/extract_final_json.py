#!/usr/bin/env python3
"""Extract the bare contribution/verdict JSON from `claude -p --output-format json`.

Ported verbatim in behaviour from /Users/jack/Desktop/ff-live/orch/bin/extract_final_json.py
(ff-orchestrator), with the role key sets kept and the failure messages unchanged.

Reads the Claude Code result envelope on stdin, persists the session id for
``--resume``, pulls the final assistant text, and prints ONLY the validated payload
JSON to stdout -- the orchestrator's stdout contract.  Anything wrong exits 1 with
diagnostics on stderr so the orchestrator retries the worktree.

Difference from normalize_result.py: this one FAILS CLOSED (a malformed payload is a
failed turn), because `claude -p` is asked to emit the contract JSON as its final
message.  normalize_result.py instead COERCES, because the agentbox hosts write the
result to a file and codex often writes none at all.
"""
from __future__ import annotations

import json
import sys

REQUIRED = {
    "worker": ("schema_version", "status", "summary"),
    "reviewer": ("schema_version", "verdict", "notes"),
}


def fail(message: str) -> None:
    print(f"claude output extraction failed: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    role = sys.argv[1] if len(sys.argv) > 1 else "worker"
    session_id_file = sys.argv[2] if len(sys.argv) > 2 else None

    raw = sys.stdin.read()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"envelope is not JSON ({exc}); head: {raw[:200]!r}")
    if not isinstance(envelope, dict):
        fail("envelope is not a JSON object")

    session_id = envelope.get("session_id")
    if session_id_file and isinstance(session_id, str) and session_id:
        try:
            with open(session_id_file, "w", encoding="utf-8") as fh:
                fh.write(session_id)
        except OSError as exc:
            print(f"warning: could not persist session id: {exc}", file=sys.stderr)

    if envelope.get("is_error"):
        fail(f"claude reported an error; subtype={envelope.get('subtype')!r}")
    text = envelope.get("result")
    if not isinstance(text, str) or not text.strip():
        fail(f"no result text; subtype={envelope.get('subtype')!r}")

    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        fail(f"no JSON object in final message: {text[:200]!r}")
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        fail(f"final message is not valid JSON ({exc}): {text[:300]!r}")
    if not isinstance(payload, dict):
        fail("final message JSON is not an object")

    required = REQUIRED.get(role)
    if required is None:
        fail(f"unknown role {role!r}")
    missing = [key for key in required if key not in payload]
    if missing:
        fail(f"payload missing required keys {missing}: {payload!r}")

    json.dump(payload, sys.stdout)
    print()


if __name__ == "__main__":
    main()
