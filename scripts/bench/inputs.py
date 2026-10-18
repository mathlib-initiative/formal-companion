"""Read what the other locate packages write, by the documented formats only.

New code.  The bench reads four producers' files and imports none of their
modules, because it was built beside them (references/locate-documents.md
sections 7-10 are the contract), with one exception: which source of a page a
run reads is the sourceset's own rule (``Manifest.preferred`` in
``scripts/sources/sourceset.py``, section 7 "One source per page is planned"),
and :meth:`Manifest.preferred` here delegates to it, so the bench can never
resolve a page to another revision than the one a run plans:

* a sourceset (section 7): ``manifest-<n>.jsonl`` -- a header line, then one
  canonical entry per source -- and a source directory's ``layer.txt``,
  ``sections.json``, ``paragraphs.json`` and ``finders.json``; a version written
  as a delta (``cf-sourceset-delta/1``) is replayed by the sourceset's own
  reader, ``sources.sourceset.Sourceset.load``, the one import from a producer;
* a universe (section 8): ``universe.yaml`` and ``corpus.sqlite``, opened
  ``mode=ro&immutable=1`` as everywhere else;
* a harvested locate run (sections 9 and 10): ``runs/<run>/run.yaml``, the task
  manifests under the tend root's ``gate/tasks/``, ``corpus/<run>/RECEIPT.json``,
  the documents under ``corpus/<run>/`` and ``reports/locate-outcomes.<run>.jsonl``;
* the gate log (section 9): one JSON line per gate call.

Everything here only reads.  A file in the wrong shape is a ``CfError`` naming
it, never a silently empty answer -- an empty answer would score as a run that
found nothing.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from cflib.errors import CfError
from cflib.jsonio import read_json
from cflib.paths import WorkspacePaths
from cflib.yamlio import read_yaml

from sources.sourceset import Manifest as _SourcesetManifest
from sources.sourceset import is_adopted

from .layout import read_jsonl_rows


# --------------------------------------------------------------------------
# sourcesets
# --------------------------------------------------------------------------

@dataclass
class Manifest:
    """One version of a sourceset manifest."""

    sourceset: str
    version: int
    path: Path
    sha256: str
    header: dict
    entries: dict[str, dict] = field(default_factory=dict)

    @property
    def pin(self) -> dict:
        return {"id": self.sourceset, "version": self.version, "manifest_sha256": self.sha256}

    def preferred(self) -> dict[int, str]:
        """``{pageid: source_id}``: the one source per page a run reads.

        The sourceset's rule, not a copy of it: a page a consumer pinned is read
        at its adopted revision (the highest adopted one when there are several),
        any other page at its highest enumerated revision -- whether or not that
        source is rendered yet.
        """
        paged = {sid: e for sid, e in self.entries.items()
                 if e.get("pageid") is not None and e.get("revision") is not None}
        return _SourcesetManifest(self.sourceset, self.version, self.path, self.sha256,
                                  self.header, paged).preferred()

    def page_entries(self) -> dict[int, list[dict]]:
        """``{pageid: [entries]}``: every revision the manifest holds of each page."""
        out: dict[int, list[dict]] = {}
        for sid in sorted(self.entries):
            entry = self.entries[sid]
            if entry.get("pageid") is not None:
                out.setdefault(int(entry["pageid"]), []).append(entry)
        return out


def adopted(entry: dict) -> bool:
    """Did a consumer pin this source (references/locate-documents.md section 7)?"""
    return is_adopted(entry)


def load_manifest(paths: WorkspacePaths, sourceset: str, version: int | None = None) -> Manifest:
    """The pinned (or the latest) manifest of a sourceset."""
    available = paths.sourceset_manifests(sourceset)
    if not available:
        raise CfError(f"sourceset {sourceset!r} has no manifest under {paths.sourceset_dir(sourceset)}",
                      hint="create and enumerate the sourceset first")
    if version is None:
        version, path = available[-1]
    else:
        path = paths.sourceset_manifest(sourceset, version)
        if not path.is_file():
            raise CfError(f"sourceset {sourceset!r} has no manifest version {version}",
                          hint="versions on disk: " + ", ".join(str(n) for n, _ in available))
    raw = path.read_bytes()
    lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
    if not lines:
        raise CfError(f"{path} is empty: a manifest's first line is its header")
    try:
        header = json.loads(lines[0])
        rows = [json.loads(line) for line in lines[1:]]
    except json.JSONDecodeError as exc:
        raise CfError(f"{path}: not JSON lines ({exc.msg})") from exc
    if isinstance(header, dict) and header.get("schema") == "cf-sourceset-delta/1":
        from sources.sourceset import Sourceset
        full = Sourceset(paths, sourceset).load(int(version))
        return Manifest(sourceset, int(version), path, full.sha256, full.header,
                        {sid: full.entries[sid] for sid in sorted(full.entries)})
    if not isinstance(header, dict) or header.get("schema") != "cf-sourceset/1":
        raise CfError(f"{path}: the first line is not a cf-sourceset/1 header")
    entries: dict[str, dict] = {}
    for row in rows:
        sid = row.get("source_id") if isinstance(row, dict) else None
        if not sid:
            raise CfError(f"{path}: an entry with no source_id")
        entries[str(sid)] = row
    return Manifest(sourceset, int(version), path, hashlib.sha256(raw).hexdigest(), header, entries)


def source_dir(paths: WorkspacePaths, sourceset: str, source_id: str) -> Path:
    return paths.sourceset_source_dir(sourceset, source_id)


def read_layer(paths: WorkspacePaths, sourceset: str, source_id: str,
               layer_sha256: str | None = None) -> str:
    """``layer.txt``, refused when it is not the pinned layer."""
    path = source_dir(paths, sourceset, source_id) / "layer.txt"
    if not path.is_file():
        raise CfError(f"{source_id}: no layer.txt at {path}",
                      hint="render the source first (the sourceset's render step)")
    raw = path.read_bytes()
    if layer_sha256 and hashlib.sha256(raw).hexdigest() != layer_sha256:
        raise CfError(f"{source_id}: layer.txt is not the pinned layer {layer_sha256[:12]}…",
                      hint="the bench pins the sourceset manifest; re-render or re-pin")
    return raw.decode("utf-8")


def read_source_json(paths: WorkspacePaths, sourceset: str, source_id: str, name: str,
                     required: bool = True) -> Any:
    path = source_dir(paths, sourceset, source_id) / name
    if not path.is_file():
        if required:
            raise CfError(f"{source_id}: no {name} at {path}",
                          hint="render the source first (the sourceset's render step)")
        return None
    return read_json(path)


# --------------------------------------------------------------------------
# universes
# --------------------------------------------------------------------------

def load_universe(paths: WorkspacePaths, universe: str) -> dict:
    path = paths.universe_yaml(universe)
    if not path.is_file():
        raise CfError(f"universe {universe!r} has no universe.yaml at {path}",
                      hint="create the universe and add its libraries first")
    doc = read_yaml(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("libraries"), list):
        raise CfError(f"{path} is not a universe: it has no libraries list")
    return doc


def libraries(universe: dict) -> dict[str, dict]:
    """``{library id: entry}`` of a loaded universe, each with a 40-hex commit."""
    out: dict[str, dict] = {}
    for entry in universe.get("libraries") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        commit = str(entry.get("commit") or "")
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise CfError(f"universe library {entry.get('id')!r} has no 40-hex commit")
        out[str(entry["id"])] = entry
    return out


def open_corpus(path: Path) -> sqlite3.Connection:
    """``corpus.sqlite`` read-only and immutable (references/locate-documents.md section 8)."""
    if not Path(path).is_file():
        raise CfError(f"no corpus.sqlite at {path}", hint="build corpus.sqlite from the universe's indexes first")
    uri = f"file:{Path(path).resolve()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def decl(conn: sqlite3.Connection, library: str, commit: str, name: str) -> dict | None:
    """The exact-table row of one declaration, or None."""
    row = conn.execute(
        "SELECT id, library, commit_sha, name, module, path, line, end_line, kind, "
        "proof_status, statement, signature, docstring FROM decls "
        "WHERE library = ? AND commit_sha = ? AND name = ?", (library, commit, name)).fetchone()
    return dict(row) if row else None


def tagged(conn: sqlite3.Connection, tag: str, value: str,
           library_ids: Iterable[str]) -> list[dict]:
    """Declarations of these libraries carrying ``tag = value``."""
    wanted = sorted(set(library_ids))
    if not wanted:
        return []
    marks = ",".join("?" for _ in wanted)
    rows = conn.execute(
        "SELECT d.library, d.commit_sha, d.name, d.module FROM tags t JOIN decls d ON d.id = t.decl_id "
        f"WHERE t.tag = ? AND t.value = ? AND d.library IN ({marks}) ORDER BY d.library, d.name",
        (tag, value, *wanted)).fetchall()
    return [dict(r) for r in rows]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------

def run_yaml(paths: WorkspacePaths, run_id: str) -> dict:
    path = paths.run_yaml(run_id)
    if not path.is_file():
        raise CfError(f"run {run_id!r} has no run.yaml at {path}")
    doc = read_yaml(path)
    if not isinstance(doc, dict):
        raise CfError(f"{path} is not a mapping")
    return doc


def receipt(paths: WorkspacePaths, run_id: str) -> tuple[dict, str]:
    """``(RECEIPT.json, its sha256)`` of a harvested run."""
    path = paths.receipt_json(run_id)
    if not path.is_file():
        raise CfError(f"run {run_id!r} has no RECEIPT.json at {path}",
                      hint=f"harvest it first: cf harvest {run_id}")
    raw = path.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CfError(f"{path}: invalid JSON ({exc.msg})") from exc
    if not isinstance(doc, dict):
        raise CfError(f"{path} is not a JSON object")
    return doc, hashlib.sha256(raw).hexdigest()


def outcomes(paths: WorkspacePaths, run_id: str) -> list[dict]:
    """``reports/locate-outcomes.<run>.jsonl``: one row per passage after challenge."""
    path = paths.locate_outcomes_jsonl(run_id)
    if not path.is_file():
        raise CfError(f"run {run_id!r} has no passage outcomes at {path}",
                      hint="harvest the run and write its passage outcomes first (the locate tally)")
    return read_jsonl_rows(path)


def documents(paths: WorkspacePaths, run_id: str) -> list[dict]:
    """Every locate document landed under ``corpus/<run>/`` (recursive)."""
    root = paths.corpus_run_dir(run_id)
    out = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*.json")):
        if path.name.startswith(".") or path.name == "RECEIPT.json":
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict) and doc.get("schema") == "cf-locate/1":
            out.append(doc)
    return out


def task_manifests(paths: WorkspacePaths, run_id: str) -> list[dict]:
    """The task manifests a locate run was planned with (``gate/tasks/*.json`` in its tend root)."""
    directory = paths.tend_root(run_id) / "gate" / "tasks"
    out = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            doc = read_json(path)
            if isinstance(doc, dict):
                out.append(doc)
    return out


def gate_log(directory: Path) -> list[dict]:
    """Every gate call recorded under a ``gate-log/`` directory (or one file)."""
    path = Path(directory)
    files = [path] if path.is_file() else sorted(path.rglob("*.jsonl")) if path.is_dir() else []
    if not files:
        raise CfError(f"no gate log at {path}", hint="pass the harvested copy of <tend root>/gate-log/")
    rows: list[dict] = []
    for file in files:
        rows.extend(read_jsonl_rows(file))
    return rows
