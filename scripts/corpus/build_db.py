"""Build a universe's ``corpus.sqlite`` from its normalised library indexes.

New code for docs/proposals/wikipedia-locate.md section 2.4 (``corpus.sqlite``),
with exactly the schema references/locate-documents.md section 8 fixes: the
``meta`` and ``libraries`` tables, the exact table ``decls`` keyed ``UNIQUE
(library, commit_sha, name)`` for existence checks, the FTS5 index
``decls_fts`` over ``name_words``, signature and docstring (rowid = ``decls.id``),
and ``tags``.  ``decls.deprecated_to`` is the new name a deprecated alias points at.

The database is DERIVED: everything in it comes from ``universe.yaml`` and the
``decls.jsonl`` each library's index pins by sha256, and the build refuses an
index whose bytes are not the pinned ones.  It is written with
``journal_mode=DELETE`` (no ``-wal`` file to copy or lose), into a temporary
file beside the target that is renamed into place only when complete, and its
sha256 is recorded in ``universe.yaml`` (``corpus_db``) and must be verified
once wherever it is copied; every reader opens it ``mode=ro&immutable=1``.

**Idempotent.**  When ``corpus.sqlite`` already describes this universe (its
``meta.universe_sha256`` is the universe's and its bytes are the recorded
sha256) the build is a no-op.  A rebuild from the same inputs gives the same
rows in the same order with the same ids; the file's BYTES also carry the
``built`` time in ``meta`` and depend on the SQLite version, so two builds are
compared by content (``universe_sha256`` and the row counts), not by file sha.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Iterator

from cflib import state as state_mod
from cflib.errors import CfError
from cflib.hashing import sha256_file

from .corpus_search import SCHEMA, CorpusError, connect, name_words
from .ingest import strip_wikipedia_urls
from .universe import Universe

#: the section 8 schema, statement by statement
DDL = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE libraries (id TEXT PRIMARY KEY, repo TEXT NOT NULL, commit_sha TEXT NOT NULL, "
    "toolchain TEXT, brain_lib TEXT, index_sha256 TEXT NOT NULL, declarations INTEGER NOT NULL)",
    "CREATE TABLE decls (id INTEGER PRIMARY KEY, library TEXT NOT NULL, commit_sha TEXT NOT NULL, "
    "name TEXT NOT NULL, module TEXT, path TEXT, line INTEGER, end_line INTEGER, kind TEXT, "
    "proof_status TEXT NOT NULL, statement INTEGER NOT NULL DEFAULT 0, "
    "printable INTEGER NOT NULL DEFAULT 1, signature TEXT, docstring TEXT, deprecated_to TEXT, "
    "UNIQUE (library, commit_sha, name))",
    "CREATE VIRTUAL TABLE decls_fts USING fts5(name_words, signature, docstring, "
    "tokenize = 'unicode61')",
    "CREATE TABLE tags (decl_id INTEGER NOT NULL, tag TEXT NOT NULL, value TEXT NOT NULL)",
    "CREATE INDEX tags_by_value ON tags (tag, value)",
)
_BATCH = 5000


def _check_fts5() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
    except sqlite3.Error as exc:
        raise CfError(f"this Python's sqlite3 has no FTS5 ({exc})",
                      hint="corpus.sqlite needs an sqlite3 built with FTS5; CPython's "
                           "official builds have it") from None
    finally:
        conn.close()


def _decls(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CfError(f"{path} line {line_no} is not JSON ({exc.msg})") from None


def current(universe: Universe, db: Path, *, blind: bool = False) -> bool:
    """Does ``db`` already describe ``universe``, byte for byte as recorded?"""
    recorded = universe.corpus_db_blind if blind else universe.corpus_db
    if not recorded or not Path(db).is_file():
        return False
    if recorded.get("universe_sha256") != universe.sha256():
        return False
    if sha256_file(db) != recorded.get("sha256"):
        return False
    try:
        conn = connect(db)
    except CorpusError:
        return False
    try:
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return (meta.get("schema") == SCHEMA and meta.get("universe_sha256") == universe.sha256()
            and meta.get("blind") == ("1" if blind else "0"))


def _deprecated_to(record: dict) -> str | None:
    """The new name a deprecated alias points at (the probe's ``deprecated.new_name``)."""
    value = record.get("deprecated")
    if isinstance(value, dict) and isinstance(value.get("new_name"), str) and value["new_name"]:
        return value["new_name"]
    return None


def build(universe: Universe, universe_dir: Path, db: Path, *, force: bool = False,
          blind: bool = False) -> dict:
    """Write ``db`` from the universe's indexes; returns ``{built, sha256, declarations, ...}``.

    ``blind`` builds the G1-blind database (docs/proposals/wikipedia-locate.md
    section 8.1: "the tag table turned off"): the same declarations with an
    EMPTY ``tags`` table, every Wikipedia link stripped from the docstrings in
    ``decls`` and ``decls_fts``, and ``meta.blind = 1`` -- so neither the
    curated joins nor the host tools can reach an article's tag or URL.  It is
    recorded as ``corpus_db_blind``, the full one as ``corpus_db``.
    ``universe`` is changed in memory; the caller saves it.
    """
    problems = universe.validate(require_indexes=True)
    if problems:
        raise CfError("the universe cannot be built into a database: " + "; ".join(problems[:5]),
                      hint="ingest every library (`cf corpus ingest`) or exclude it")
    db = Path(db)
    if not force and current(universe, db, blind=blind):
        recorded = (universe.corpus_db_blind if blind else universe.corpus_db) or {}
        return {"built": False, "sha256": recorded.get("sha256"),
                "declarations": recorded.get("declarations"), "path": str(db)}
    _check_fts5()
    indexes: list[tuple[dict, Path]] = []
    for lib in sorted(universe.libraries, key=lambda row: str(row["id"])):
        decls = Path(universe_dir) / str(lib["index"]["path"]) / "decls.jsonl"
        if not decls.is_file():
            raise CfError(f"{lib['id']}: no index at {decls}", hint="re-run `cf corpus ingest`")
        got = sha256_file(decls)
        if got != lib["index"]["sha256"]:
            raise CfError(f"{lib['id']}: {decls} has sha256 {got[:12]}…, but universe.yaml pins "
                          f"{str(lib['index']['sha256'])[:12]}…",
                          hint="the index changed after it was pinned; re-run `cf corpus ingest`")
        indexes.append((lib, decls))
    db.parent.mkdir(parents=True, exist_ok=True)
    staged = db.with_name(f".{db.name}.{os.getpid()}.tmp")
    if staged.exists():
        staged.unlink()
    built = state_mod.now()
    total = 0
    tag_rows = 0
    conn = sqlite3.connect(staged)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        for statement in DDL:
            conn.execute(statement)
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)",
                         [("schema", SCHEMA), ("universe", universe.id),
                          ("universe_sha256", universe.sha256()), ("built", built),
                          ("blind", "1" if blind else "0")])
        decl_id = 0
        for lib, decls in indexes:
            count = 0
            rows, fts, tags = [], [], []
            for record in _decls(decls):
                decl_id += 1
                count += 1
                docstring = record.get("docstring")
                if blind:
                    docstring = strip_wikipedia_urls(docstring)
                rows.append((decl_id, lib["id"], lib["commit"], record["name"], record.get("module"),
                             record.get("path"), record.get("line"), record.get("end_line"),
                             record.get("kind"), record["proof_status"],
                             int(bool(record.get("statement"))),
                             int(bool(record.get("printable", True))),
                             record.get("signature"), docstring, _deprecated_to(record)))
                fts.append((decl_id, name_words(record["name"]), record.get("signature") or "",
                            docstring or ""))
                if not blind:
                    for tag in record.get("tags") or []:
                        tags.append((decl_id, tag["tag"], tag["value"]))
                if len(rows) >= _BATCH:
                    tag_rows += _flush(conn, rows, fts, tags)
                    rows, fts, tags = [], [], []
            tag_rows += _flush(conn, rows, fts, tags)
            if count != int(lib["index"]["declarations"]):
                raise CfError(f"{lib['id']}: {count} declarations in {decls}, but universe.yaml "
                              f"records {lib['index']['declarations']}")
            conn.execute("INSERT INTO libraries (id, repo, commit_sha, toolchain, brain_lib, "
                         "index_sha256, declarations) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (lib["id"], lib["repo"], lib["commit"], lib.get("toolchain"),
                          lib.get("brain_lib"), lib["index"]["sha256"], count))
            total += count
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.close()
        staged.unlink(missing_ok=True)
        raise CfError(f"the indexes do not fit the exact table ({exc})",
                      hint="one library's index names a declaration twice; re-run its ingest") from None
    except BaseException:
        conn.close()
        staged.unlink(missing_ok=True)
        raise
    conn.close()
    digest = sha256_file(staged)
    os.replace(staged, db)
    universe.set_corpus_db(sha256=digest, built=built, declarations=total, blind=blind)
    return {"built": True, "sha256": digest, "declarations": total, "tags": tag_rows,
            "libraries": len(indexes), "path": str(db), "blind": blind}


def _flush(conn: sqlite3.Connection, rows: list, fts: list, tags: list) -> int:
    if rows:
        conn.executemany("INSERT INTO decls (id, library, commit_sha, name, module, path, line, "
                         "end_line, kind, proof_status, statement, printable, signature, "
                         "docstring, deprecated_to) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.executemany("INSERT INTO decls_fts (rowid, name_words, signature, docstring) "
                         "VALUES (?, ?, ?, ?)", fts)
    if tags:
        conn.executemany("INSERT INTO tags (decl_id, tag, value) VALUES (?, ?, ?)", tags)
    return len(tags)
