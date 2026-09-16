"""sha256 helpers and the one canonical finding_id definition.

New code. The finding_id rule comes from design/critique.json ("define
finding_id as sha256(run-id, item, kind, sorted unfaithful_decls)"): the
baseline's sha256(run-id, item, concept) was unstable because `concept` is
LLM-written prose, so the same defect got a different id in every run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Sequence

_CHUNK = 1 << 20


def sha256_file(path: str | Path) -> str:
    """sha256 of a file's bytes (chunked; the pinned PDF is ~10 MB)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    """sha256 of a string's UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(obj) -> str:
    """sha256 of a canonical JSON encoding (sorted keys, no spaces).

    Used for adapter/config keys so the same config always hashes the same.
    """
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(blob)


def sha256_parts(parts: Iterable[object]) -> str:
    """sha256 over a sequence of strings joined by NUL (order significant)."""
    h = hashlib.sha256()
    for i, part in enumerate(parts):
        if i:
            h.update(b"\x00")
        h.update(str(part).encode("utf-8"))
    return h.hexdigest()


def short(digest: str, n: int = 12) -> str:
    """First n hex characters of a digest (for display only, never for keys)."""
    return str(digest)[:n]


def finding_id(run_id: str, item: str, kind: str, unfaithful_decls: Sequence[str]) -> str:
    """The stable id every finding carries.

    ``sha256(run_id, item, kind, sorted(unfaithful_decls))``. Deterministic
    across runs of the same defect, so union/agreed/adjudication link by id
    instead of by a 22-character label prefix.
    """
    decls = sorted({str(d).strip() for d in unfaithful_decls if str(d).strip()})
    return sha256_parts([run_id, item, kind, "|".join(decls)])


def manifest(paths: Iterable[str | Path], root: str | Path | None = None) -> dict[str, str]:
    """{relative path: sha256} for a set of files (harvest RECEIPT manifests)."""
    root_p = Path(root).resolve() if root else None
    out: dict[str, str] = {}
    for p in sorted(Path(x) for x in paths):
        key = str(p.resolve().relative_to(root_p)) if root_p else str(p)
        out[key] = sha256_file(p)
    return out
