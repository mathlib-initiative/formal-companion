"""Readers of the formats the other locate packages write.

New code, reading references/locate-documents.md section 7 (sourcesets and
rendered web sources), section 8 (universes, library indexes and
``corpus.sqlite``) and section 10 (harvested documents), for the planner,
the outcomes and the export.

**These are deliberately small, private readers.**  The sourceset writer and
the renderer are ``scripts/sources/``'s, the universe and ``corpus.sqlite``
builder ``scripts/corpus/``'s; this package reads their on-disk formats
directly so that it depends on the documented format and nothing else, and
the integrate step replaces these readers with those packages' own where they
exist.  Every reader refuses what the format does not allow (a manifest whose
header is not ``cf-sourceset/1``, a universe whose recorded sha is not the one
its libraries hash to) instead of guessing.  A manifest version written as a
delta (``cf-sourceset-delta/1``, section 7) is read by the sourceset's own
reader, ``sources.sourceset.Sourceset.load``, which replays it onto the
snapshot before it and checks the chain.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from cflib import hashing, registry
from cflib.errors import CfError
from cflib.jsonio import read_json
from cflib.paths import WorkspacePaths
from cflib.yamlio import read_yaml

SOURCESET_SCHEMA = "cf-sourceset/1"
SOURCESET_DELTA_SCHEMA = "cf-sourceset-delta/1"
CORPUS_SCHEMA = "cf-corpus/1"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

#: files that sit beside findings in a harvested run and are not documents
#: (``harvest._compat.NON_FINDING_FILES``'s names; matched on the basename)
NON_DOCUMENT_FILES = {"RECEIPT.json", "run.yaml", "run.json", "manifest.json",
                      "tally.json", "journal-harvest.json", "gate.json", "gate.local.json",
                      "queue_remaining.json"}


# --------------------------------------------------------------------------
# sourcesets (section 7)
# --------------------------------------------------------------------------

@dataclass
class Manifest:
    """One version of a sourceset: its header, its entries by source id, its sha."""

    sourceset: str
    version: int
    path: Path
    sha256: str
    header: dict
    entries: dict[str, dict] = field(default_factory=dict)

    def pin(self) -> dict:
        """``{id, version, manifest_sha256}``: what a run records."""
        return {"id": self.sourceset, "version": self.version, "manifest_sha256": self.sha256}

    def rendered(self, source_id: str) -> bool:
        entry = self.entries.get(source_id) or {}
        return bool(entry.get("html_sha256") and entry.get("layer_sha256")
                    and entry.get("renderer"))


def _manifest_lines(path: Path) -> Iterator[tuple[int, dict]]:
    with path.open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CfError(f"{path}:{number}: not JSON ({exc.msg})") from None
            if not isinstance(row, dict):
                raise CfError(f"{path}:{number}: a manifest line is a JSON object")
            yield number, row


def load_manifest(paths: WorkspacePaths, sourceset_id: str,
                  version: int | None = None) -> Manifest:
    """Version ``version`` of a sourceset (default: the newest), validated."""
    versions = paths.sourceset_manifests(sourceset_id)
    if not versions:
        raise CfError(f"sourceset {sourceset_id!r} has no manifest under "
                      f"{paths.sourceset_dir(sourceset_id)}",
                      hint="the sourceset is created, enumerated, fetched and rendered by the "
                           "sources stage (references/locate-documents.md section 7)")
    if version is None:
        version, path = versions[-1]
    else:
        found = dict(versions)
        if version not in found:
            raise CfError(f"sourceset {sourceset_id!r} has no manifest version {version}",
                          hint="versions on disk: " + ", ".join(str(n) for n, _ in versions))
        path = found[version]
    rows = list(_manifest_lines(path))
    if not rows:
        raise CfError(f"{path} is empty: line 1 is the cf-sourceset/1 header")
    header = rows[0][1]
    if header.get("schema") == SOURCESET_DELTA_SCHEMA:
        from sources.sourceset import Sourceset
        full = Sourceset(paths, sourceset_id).load(version)
        return Manifest(sourceset=sourceset_id, version=version, path=path,
                        sha256=full.sha256, header=full.header,
                        entries={sid: full.entries[sid] for sid in sorted(full.entries)})
    if header.get("schema") != SOURCESET_SCHEMA:
        raise CfError(f"{path}: line 1 is not a {SOURCESET_SCHEMA} header")
    if str(header.get("id")) != sourceset_id or header.get("version") != version:
        raise CfError(f"{path}: the header names sourceset {header.get('id')!r} version "
                      f"{header.get('version')!r}, not {sourceset_id!r} version {version}")
    entries: dict[str, dict] = {}
    previous = ""
    for number, row in rows[1:]:
        sid = str(row.get("source_id") or "")
        if registry.parse_web_source_id(sid) is None:
            raise CfError(f"{path}:{number}: {sid!r} is not a web source id")
        if sid in entries:
            raise CfError(f"{path}:{number}: source {sid} is listed twice")
        if sid < previous:
            raise CfError(f"{path}:{number}: the entries are not sorted by source_id")
        previous = sid
        entries[sid] = row
    return Manifest(sourceset=sourceset_id, version=version, path=path,
                    sha256=hashing.sha256_file(path), header=header, entries=entries)


class SourceView:
    """The files of one rendered web source (section 7's table)."""

    def __init__(self, directory: Path, source_id: str):
        self.dir = Path(directory)
        self.source_id = source_id

    def _path(self, name: str) -> Path:
        path = self.dir / name
        if not path.is_file():
            raise CfError(f"{self.source_id}: {name} is missing from {self.dir}",
                          hint="the source is not rendered; the sources stage renders it")
        return path

    def text(self, name: str) -> str:
        return self._path(name).read_text(encoding="utf-8")

    def json(self, name: str, default: Any = None) -> Any:
        path = self.dir / name
        if not path.is_file():
            if default is not None:
                return default
            self._path(name)
        return read_json(path)

    def layer(self) -> str:
        return self.text("layer.txt")

    def formulas(self) -> list[dict]:
        return list(self.json("formulas.json", []))

    def finders(self) -> list[dict]:
        return list(self.json("finders.json", []))

    def paragraphs(self) -> list[dict]:
        return list(self.json("paragraphs.json", []))

    def sections(self) -> list[dict]:
        return list(self.json("sections.json", []))

    def layer_sha256(self) -> str:
        return hashing.sha256_file(self._path("layer.txt"))


def source_view(paths: WorkspacePaths, sourceset_id: str, source_id: str) -> SourceView:
    return SourceView(paths.sourceset_source_dir(sourceset_id, source_id), source_id)


# --------------------------------------------------------------------------
# universes (section 8)
# --------------------------------------------------------------------------

def universe_sha256(libraries: Iterable[dict]) -> str:
    """sha256 over ``<id>\\t<commit>\\t<index sha256>`` per library, sorted by id, ``\\n``-joined."""
    lines = sorted(f"{lib['id']}\t{lib['commit']}\t{(lib.get('index') or {}).get('sha256', '')}"
                   for lib in libraries)
    return hashing.sha256_text("\n".join(lines))


@dataclass
class Universe:
    id: str
    sha256: str
    libraries: dict[str, dict]
    excluded: list[dict]
    data: dict

    def library(self, library_id: str) -> dict:
        try:
            return self.libraries[library_id]
        except KeyError:
            raise CfError(f"library {library_id!r} is not in universe {self.id}",
                          hint="libraries: " + ", ".join(sorted(self.libraries))) from None

    def commit(self, library_id: str) -> str:
        return str(self.library(library_id)["commit"])

    def pin(self) -> dict:
        return {"id": self.id, "sha256": self.sha256}

    def gate_libraries(self) -> dict:
        return {lid: {"repo": str(lib.get("repo") or ""), "commit": str(lib["commit"])}
                for lid, lib in sorted(self.libraries.items())}


def load_universe(paths: WorkspacePaths, universe_id: str) -> Universe:
    path = paths.universe_yaml(universe_id)
    if not path.is_file():
        raise CfError(f"universe {universe_id!r} has no universe.yaml at {path}",
                      hint="the corpus stage writes it (references/locate-documents.md section 8)")
    data = read_yaml(path)
    if not isinstance(data, dict) or str(data.get("id")) != universe_id:
        raise CfError(f"{path} is not universe {universe_id!r}")
    libraries: dict[str, dict] = {}
    for lib in data.get("libraries") or []:
        if not isinstance(lib, dict) or not lib.get("id"):
            raise CfError(f"{path}: a library without an id")
        lid = str(lib["id"])
        if not _COMMIT.match(str(lib.get("commit") or "")):
            raise CfError(f"{path}: library {lid} has no 40-hex commit")
        if lid in libraries:
            raise CfError(f"{path}: library {lid} is listed twice")
        libraries[lid] = dict(lib)
    if not libraries:
        raise CfError(f"{path}: universe {universe_id} has no libraries")
    computed = universe_sha256(libraries.values())
    recorded = str(data.get("universe_sha256") or "")
    if recorded != computed:
        raise CfError(f"{path}: universe_sha256 {recorded[:12] or '(none)'} is not the sha its "
                      f"libraries hash to ({computed[:12]})",
                      hint="a re-probe or an edited library changes the universe; rebuild it "
                           "through the corpus stage rather than editing the sha")
    return Universe(id=universe_id, sha256=computed, libraries=libraries,
                    excluded=list(data.get("excluded") or []), data=data)


# --------------------------------------------------------------------------
# corpus.sqlite (section 8)
# --------------------------------------------------------------------------

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN = re.compile(r"\w+", re.UNICODE)


def name_words(name: str) -> str:
    """A declaration name split at ``.``, ``_`` and lower-to-upper changes, lowercased."""
    parts = re.split(r"[._]+", str(name or ""))
    words: list[str] = []
    for part in parts:
        words.extend(w for w in _CAMEL.split(part) if w)
    return " ".join(w.lower() for w in words)


def fts_query(text: str, *, any_word: bool = False) -> str:
    """An FTS5 MATCH expression for the words of ``text``: every word, or any (``OR``)."""
    words = [w.lower() for w in _TOKEN.findall(str(text or ""))]
    quoted = ['"' + w.replace('"', '""') + '"' for w in dict.fromkeys(words)]
    return (" OR " if any_word else " ").join(quoted)


class CorpusDB:
    """``corpus.sqlite`` opened read-only and immutable, as everywhere but its builder."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise CfError(f"corpus.sqlite is missing at {self.path}",
                          hint="the corpus stage builds it (references/locate-documents.md "
                               "section 8)")
        uri = f"file:{self.path.resolve().as_posix()}?mode=ro&immutable=1"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row
        meta = dict(self.conn.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("schema") != CORPUS_SCHEMA:
            raise CfError(f"{self.path} is not a {CORPUS_SCHEMA} database")
        self.meta = meta

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "CorpusDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def sha256(self) -> str:
        return hashing.sha256_file(self.path)

    def decl(self, library: str, commit: str, name: str) -> dict | None:
        """The exact table's row, or None: the existence check of gate rule 3."""
        row = self.conn.execute(
            "SELECT * FROM decls WHERE library = ? AND commit_sha = ? AND name = ?",
            (library, commit, name)).fetchone()
        return dict(row) if row is not None else None

    def tagged(self, tag: str, value: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT d.*, t.value AS tag_value FROM tags t JOIN decls d ON d.id = t.decl_id "
            "WHERE t.tag = ? AND t.value = ? ORDER BY d.library, d.name", (tag, value)).fetchall()
        return [dict(r) for r in rows]

    def tags_of_kind(self, tag: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT d.*, t.value AS tag_value FROM tags t JOIN decls d ON d.id = t.decl_id "
            "WHERE t.tag = ? ORDER BY d.library, d.name, t.value", (tag,)).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        """FTS5 hits for a MATCH expression, best first, with a positive ``score``."""
        if not query:
            return []
        try:
            rows = self.conn.execute(
                "SELECT d.*, bm25(decls_fts) AS rank FROM decls_fts "
                "JOIN decls d ON d.id = decls_fts.rowid WHERE decls_fts MATCH ? "
                "ORDER BY rank, d.library, d.name LIMIT ?", (query, int(limit))).fetchall()
        except sqlite3.OperationalError as exc:
            raise CfError(f"{self.path}: the full-text query {query!r} failed ({exc})") from None
        out = []
        for row in rows:
            item = dict(row)
            item["score"] = round(-float(item.pop("rank")), 4)
            out.append(item)
        return out


# --------------------------------------------------------------------------
# harvested runs (section 10)
# --------------------------------------------------------------------------

def harvested_documents(run_dir: Path) -> list[tuple[str, dict]]:
    """``[(relative path, document)]`` of every JSON document under a harvested run, recursively.

    ``findings/<task-id>/<source-id>.json`` is the layout; a document that is not
    a JSON object is refused by name, as the harvest receipt refuses it.
    """
    root = Path(run_dir)
    if not root.is_dir():
        return []
    out: list[tuple[str, dict]] = []
    for path in sorted(root.rglob("*.json")):
        if path.name in NON_DOCUMENT_FILES or path.name.startswith("."):
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CfError(f"{rel} in {root} is not readable JSON ({exc})") from None
        if not isinstance(doc, dict):
            raise CfError(f"{rel} in {root} is not a JSON object")
        out.append((rel, doc))
    return out


def receipt(run_dir: Path) -> dict | None:
    path = Path(run_dir) / "RECEIPT.json"
    if not path.is_file():
        return None
    data = read_json(path)
    return data if isinstance(data, dict) else None


def require_gated(run_dir: Path, run_id: str, what: str) -> dict:
    """The receipt of a harvested run that is complete and passed its gate, or refuse.

    A frozen inventory and a challenged run are inputs only once the gate froze
    them: a receipt that is incomplete, or carries no gate block, or whose gate
    failed, is not one.
    """
    got = receipt(run_dir)
    if got is None:
        raise CfError(f"{what} {run_id!r} has no RECEIPT.json under {run_dir}",
                      hint=f"harvest it first: cf harvest {run_id}")
    if not got.get("complete"):
        raise CfError(f"{what} {run_id!r}: the harvest receipt is not complete",
                      hint=f"re-run the missing tasks, then cf harvest {run_id} again")
    gate = got.get("gate")
    if not isinstance(gate, dict) or not gate.get("ok"):
        raise CfError(f"{what} {run_id!r} was not gated, or its gate failed",
                      hint="a document is frozen only once the gate passed it")
    return got
