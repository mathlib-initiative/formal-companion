"""Read the Literature Links shards a generated overlay shows.

Section 2 of the overlay contract (`docs/overlay.md` in the formal-companion
skill).  The shards are JSONL files, one annotation per line, in the
directory `GENERATED.json`'s `annotations` block names (relative to the
companion root); that block lists every shard with its sha256, because a
browser cannot list a directory.  `serve.py`, `build_site.py` and
`check_generated.py` read the shards through this module, with the standard
library alone.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

GENERATED_NAME = "GENERATED.json"
#: The extension key the overlay's records carry its own facts under.
NAMESPACE = "org.mathlib-initiative.formal-companion.overlay"
SHARD_NAME = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*\.jsonl\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ShardError(Exception):
    """The shards or their record cannot be read; the message says which."""


def strict_json(text: str, label: str) -> Any:
    """JSON with no duplicate keys and no `NaN`/`Infinity`."""
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise ShardError(f"{label}: duplicate JSON key {key!r}")
            out[key] = value
        return out

    def constant(token: str) -> Any:
        raise ShardError(f"{label}: non-standard JSON constant {token}")

    try:
        return json.loads(text, object_pairs_hook=unique, parse_constant=constant)
    except json.JSONDecodeError as error:
        raise ShardError(f"{label}: invalid JSON: {error}") from error


def safe_relative(value: Any) -> bool:
    """A normalised, traversal-free relative POSIX path."""
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    pure = PurePosixPath(value)
    return (not pure.is_absolute() and str(pure) == value
            and not any(part in {"", ".", ".."} for part in pure.parts))


def read_regular(path: Path, label: str) -> bytes:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise ShardError(f"{label}: {path} does not exist") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ShardError(f"{label}: expected an ordinary nonsymlink file")
    return path.read_bytes()


def annotations_block(overlay: Path) -> dict[str, Any]:
    """`GENERATED.json`'s `annotations` block, its paths checked."""
    try:
        record = strict_json(read_regular(overlay / GENERATED_NAME, GENERATED_NAME).decode("utf-8"),
                             GENERATED_NAME)
    except UnicodeDecodeError as error:
        raise ShardError(f"{GENERATED_NAME}: not UTF-8") from error
    block = record.get("annotations") if isinstance(record, dict) else None
    if not isinstance(block, dict):
        raise ShardError(f"{GENERATED_NAME}.annotations: expected an object; "
                         f"`cf overlay export` and `cf overlay sync` write it")
    directory = block.get("directory")
    if not safe_relative(directory):
        raise ShardError(f"{GENERATED_NAME}.annotations.directory: expected a relative path")
    shards = block.get("shards")
    if not isinstance(shards, list):
        raise ShardError(f"{GENERATED_NAME}.annotations.shards: expected an array")
    for index, row in enumerate(shards):
        path = row.get("path") if isinstance(row, dict) else None
        if not (safe_relative(path) and str(PurePosixPath(path).parent) == directory
                and SHARD_NAME.fullmatch(PurePosixPath(path).name)
                and isinstance(row.get("sha256"), str) and HEX64.fullmatch(row["sha256"])):
            raise ShardError(f"{GENERATED_NAME}.annotations.shards[{index}]: expected "
                             f"{{path: {directory}/<name>.jsonl, sha256}}")
    return block


def read_records(raw: bytes, label: str) -> list[dict[str, Any]]:
    """The annotations of one JSONL shard, one per nonblank line."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ShardError(f"{label}: not UTF-8") from error
    out = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip(" \t"):
            continue
        value = strict_json(line, f"{label}:{number}")
        if not isinstance(value, dict):
            raise ShardError(f"{label}:{number}: expected a JSON object")
        out.append(value)
    return out


def shard_records(overlay: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    """``[(path, records)]`` for every shard `GENERATED.json` lists, read from the companion."""
    root = overlay.parent
    out = []
    for row in annotations_block(overlay)["shards"]:
        path = row["path"]
        out.append((path, read_records(read_regular(root.joinpath(*PurePosixPath(path).parts), path), path)))
    return out


def extension(node: Any) -> dict[str, Any]:
    """The overlay's own extension object of a record, source or target, or {}."""
    value = node.get("extensions", {}).get(NAMESPACE) if isinstance(node, dict) else None
    return value if isinstance(value, dict) else {}


def pdf_source(overlay: Path, source_id: str) -> dict[str, Any]:
    """The `source` of the first record whose PDF the config's `source_id` names."""
    for path, records in shard_records(overlay):
        for record in records:
            source = record.get("source")
            if isinstance(source, dict) and source.get("type") == "pdf" \
                    and extension(source).get("source_id") == source_id:
                return source
    raise ShardError(f"no shard holds a record of the PDF {source_id!r}, which the config's "
                     f"source_id names; `cf overlay export` writes them")
