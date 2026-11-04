"""Stage receipts and approvals: `informalize/<id>/stages/I<n>.json` and `approve-<gate>.json`.

New code, for docs/proposals/informalize.md sections 5.7 and 13.  A receipt says what a
stage read and wrote, by sha256, what it checked, which runs it applied and what they
cost; an approval says who signed which gate when, and pins the files (or, for the
references gate, the list derived from one) it signed, so a later change voids it.  Paths
in both are relative to `informalize/<id>/` (`repo/informal/items.json`); a pin of a list
derived from a file is that file's path with a `#` fragment naming the list
(`repo/informal/items.json#references`).  Both are written atomically, and read back
through the same rules.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from cflib.errors import CfError
from cflib.hashing import sha256_file
from cflib.jsonio import read_json, write_json
from cflib.paths import WorkspacePaths
from cflib.state import now
from cflib.vocab import APPROVAL_GATES

RECEIPT_SCHEMA = "formal-companion/informalize-stage"
APPROVAL_SCHEMA = "formal-companion/informalize-approval"
SCHEMA_VERSION = 1
#: I0..I7, and a phase's own receipt in I5 (`I5-draft-<run>`)
STAGE_PATTERN = r"\AI[0-7](?:-[a-z]+-[A-Za-z0-9._-]+)?\Z"
_SHA = re.compile(r"\A[0-9a-f]{64}\Z")
RECEIPT_KEYS = ("schema", "schema_version", "id", "stage", "written_at", "inputs", "outputs",
                "checks", "runs", "cost_usd")
APPROVAL_KEYS = ("schema", "schema_version", "id", "gate", "by", "at", "pins", "note")


def _pins_problems(where: str, pins: Any) -> list[str]:
    if not isinstance(pins, dict):
        return [f"{where} is not an object"]
    return [f"{where}: {k!r} is not pinned to a sha256" for k, v in pins.items()
            if not isinstance(k, str) or not k or not isinstance(v, str) or not _SHA.match(v)]


def receipt_problems(doc: Any) -> list[str]:
    if not isinstance(doc, dict):
        return ["a receipt is an object"]
    out = [f"no {k!r}" for k in RECEIPT_KEYS if k not in doc]
    if doc.get("schema") != RECEIPT_SCHEMA or doc.get("schema_version") != SCHEMA_VERSION:
        out.append(f"not a {RECEIPT_SCHEMA} receipt of version {SCHEMA_VERSION}")
    if not isinstance(doc.get("stage"), str) or not re.match(STAGE_PATTERN, doc["stage"]):
        out.append(f"stage {doc.get('stage')!r} is not I0..I7")
    out += _pins_problems("inputs", doc.get("inputs"))
    out += _pins_problems("outputs", doc.get("outputs"))
    if not isinstance(doc.get("checks"), dict):
        out.append("checks is not an object")
    if not isinstance(doc.get("runs"), list):
        out.append("runs is not a list")
    cost = doc.get("cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        out.append(f"cost_usd {cost!r} is not a number of dollars")
    return out


def approval_problems(doc: Any) -> list[str]:
    if not isinstance(doc, dict):
        return ["an approval is an object"]
    out = [f"no {k!r}" for k in APPROVAL_KEYS if k not in doc]
    if doc.get("schema") != APPROVAL_SCHEMA or doc.get("schema_version") != SCHEMA_VERSION:
        out.append(f"not a {APPROVAL_SCHEMA} approval of version {SCHEMA_VERSION}")
    if doc.get("gate") not in APPROVAL_GATES:
        out.append(f"gate {doc.get('gate')!r} is not one of {', '.join(APPROVAL_GATES)}")
    if not isinstance(doc.get("by"), str) or not doc["by"].strip():
        out.append("by names nobody")
    out += _pins_problems("pins", doc.get("pins"))
    if not doc.get("pins"):
        out.append("pins pins nothing")
    if not isinstance(doc.get("note"), str):
        out.append("note is not text")
    return out


def write_receipt(paths: WorkspacePaths, informalize_id: str, stage: str, *,
                  inputs: dict[str, str], outputs: dict[str, str], checks: dict,
                  runs: list | None = None, cost_usd: float = 0.0) -> Path:
    doc = {"schema": RECEIPT_SCHEMA, "schema_version": SCHEMA_VERSION, "id": informalize_id,
           "stage": stage, "written_at": now(), "inputs": dict(sorted(inputs.items())),
           "outputs": dict(sorted(outputs.items())), "checks": checks, "runs": runs or [],
           "cost_usd": cost_usd}
    found = receipt_problems(doc)
    if found:
        raise CfError(f"refusing to write the {stage} receipt: {found[0]}")
    return write_json(paths.informalize_receipt(informalize_id, stage), doc)


def read_receipt(paths: WorkspacePaths, informalize_id: str, stage: str) -> dict | None:
    path = paths.informalize_receipt(informalize_id, stage)
    if not path.is_file():
        return None
    doc = read_json(path)
    found = receipt_problems(doc)
    if found:
        raise CfError(f"{path}: {found[0]}", hint="re-run the stage that writes it")
    return doc


def write_approval(paths: WorkspacePaths, informalize_id: str, gate: str, *, by: str,
                   pins: dict[str, str], note: str = "", extra: dict | None = None) -> dict:
    doc = {"schema": APPROVAL_SCHEMA, "schema_version": SCHEMA_VERSION, "id": informalize_id,
           "gate": gate, "by": by.strip(), "at": now(), "pins": dict(sorted(pins.items())),
           "note": note}
    doc.update(extra or {})
    found = approval_problems(doc)
    if found:
        raise CfError(f"refusing to write the {gate} approval: {found[0]}")
    write_json(paths.informalize_approval(informalize_id, gate), doc)
    return doc


def read_approval(paths: WorkspacePaths, informalize_id: str, gate: str) -> dict | None:
    path = paths.informalize_approval(informalize_id, gate)
    if not path.is_file():
        return None
    doc = read_json(path)
    found = approval_problems(doc)
    if found:
        raise CfError(f"{path}: {found[0]}",
                      hint=f"`cf informalize approve {informalize_id} {gate}` writes it again")
    return doc


def file_sha(home: Path, relative: str) -> str | None:
    """The sha256 of `informalize/<id>/<relative>`, or None when it is not a file."""
    path = home / relative
    if path.is_symlink() or not path.is_file():
        return None
    return sha256_file(path)


def stale_pins(home: Path, pins: dict[str, str],
               derived: dict[str, Callable[[], str | None]] | None = None) -> list[str]:
    """Every pin whose file (or derived list) no longer has its pinned sha256."""
    derived = derived or {}
    out = []
    for key, sha in sorted(pins.items()):
        if key in derived:
            current = derived[key]()
        elif "#" in key:
            out.append(f"{key}: no rule derives it")
            continue
        else:
            current = file_sha(home, key)
        if current is None:
            out.append(f"{key}: gone")
        elif current != sha:
            out.append(f"{key}: changed since it was approved")
    return out
