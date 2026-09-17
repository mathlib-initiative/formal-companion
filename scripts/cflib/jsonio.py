"""Atomic JSON / JSONL io.

New code. Every workspace artifact is rewritten in place by idempotent
commands, so every write goes through a temp file + os.replace: a killed
`cf harvest` must never leave a half-written state.json behind.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator

from .errors import CfError


def read_json(path: str | Path, default: Any = None) -> Any:
    """Parse a JSON file; return ``default`` when the file does not exist."""
    p = Path(path)
    if not p.exists():
        if default is not None or default == {} or default == []:
            return default
        raise CfError(f"missing file: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CfError(f"{p}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc


def atomic_write(path: str | Path, text: str) -> Path:
    """Write text to ``path`` atomically (temp file in the same dir + replace)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def write_json(path: str | Path, obj: Any, indent: int = 2, sort_keys: bool = False) -> Path:
    """Write pretty JSON with a trailing newline, atomically."""
    text = json.dumps(obj, indent=indent, ensure_ascii=False, sort_keys=sort_keys) + "\n"
    return atomic_write(path, text)


def iter_jsonl(path: str | Path, skip_meta: bool = False) -> Iterator[dict]:
    """Yield objects from a .jsonl file.

    ``skip_meta`` drops a leading ``_meta`` row (the routed-findings
    convention: line 1 carries status/source_sha/routing rule).
    """
    p = Path(path)
    if not p.exists():
        return
    with p.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CfError(f"{p}:{n}: invalid JSON: {exc.msg}") from exc
            if skip_meta and isinstance(row, dict) and "_meta" in row and len(row) == 1:
                continue
            yield row


def read_jsonl(path: str | Path, skip_meta: bool = False) -> list[dict]:
    """All rows of a .jsonl file as a list."""
    return list(iter_jsonl(path, skip_meta=skip_meta))


def write_jsonl(path: str | Path, rows: Iterable[dict], meta: dict | None = None) -> Path:
    """Write rows as JSONL atomically, optionally preceded by a ``_meta`` row."""
    parts = []
    if meta is not None:
        parts.append(json.dumps({"_meta": meta}, ensure_ascii=False))
    for row in rows:
        parts.append(json.dumps(row, ensure_ascii=False))
    return atomic_write(path, "\n".join(parts) + ("\n" if parts else ""))
