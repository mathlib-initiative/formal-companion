#!/usr/bin/env python3
"""Extract the bare contribution/verdict JSON from `claude -p --output-format json`.

Ported from the ff-orchestrator's ``orch/bin/extract_final_json.py``, with the role
key sets kept.  One behaviour is deliberately NOT the original's: the payload is
found by scanning balanced JSON objects from the end rather than by taking the
first ``{`` to the last ``}``, because that scan failed on every real final
message that carried one sentence of prose as well as the payload.

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
import re
import sys

REQUIRED = {
    "worker": ("schema_version", "status", "summary"),
    "reviewer": ("schema_version", "verdict", "notes"),
}

#: ```json … ``` (or a bare fence) around the payload -- the shape the prompt asks
#: for, and the only shape the original first-brace-to-last-brace scan handled
_FENCE = re.compile(r"```(?:json)?\s*(?P<body>[\[{].*?)\s*```", re.DOTALL | re.IGNORECASE)


def fail(message: str) -> None:
    print(f"claude output extraction failed: {message}", file=sys.stderr)
    sys.exit(1)


def _objects(text: str):
    """Every balanced top-level ``{…}`` in *text*, in order, as strings.

    Brace counting, string- and escape-aware, because the payload's own values
    contain braces.  ``text.find("{")`` to ``text.rfind("}")`` treated the whole
    message as one object, so a worker that wrote one sentence of prose around a
    perfectly good payload -- "I set {status} as required", a draft object before
    the final one, a closing offer of more detail -- lost its whole turn and got
    its worktree requeued.  ``normalize_result.py`` calls that the silent-requeue
    trap and was written to avoid it.
    """
    out: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append(text[start:i + 1])
    return out


def extract_payload(text: str, required: tuple) -> "dict | None":
    """The last JSON object in *text* that carries every key in *required*.

    A fenced block is preferred -- it is what the prompt asks for, and an
    unbalanced brace in the surrounding prose ("I opened a { here") defeats the
    balanced scan entirely.  Otherwise the balanced objects are tried from the
    LAST one backwards, because a final message that revises itself means the
    last object.  Still fail-closed: an object without the contract keys is not
    a payload.
    """
    fenced = [m.group("body") for m in _FENCE.finditer(text)]
    for raw in list(reversed(fenced)) + list(reversed(_objects(text))):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and all(key in payload for key in required):
            return payload
    return None


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

    required = REQUIRED.get(role)
    if required is None:
        fail(f"unknown role {role!r}")

    text = text.strip()
    if "{" not in text:
        fail(f"no JSON object in final message: {text[:200]!r}")
    payload = extract_payload(text, required)
    if payload is None:
        fail(f"no JSON object with {list(required)} in final message: {text[:300]!r}")

    json.dump(payload, sys.stdout)
    print()


if __name__ == "__main__":
    main()
