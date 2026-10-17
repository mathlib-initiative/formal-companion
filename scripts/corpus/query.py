"""Read a universe's ``corpus.sqlite``: the API the other locate stages call.

New code for docs/proposals/wikipedia-locate.md sections 2.4 and 3 (L2), over
the schema references/locate-documents.md section 8 fixes.  The candidates
stage, the export and ``cf corpus search|show`` read the database through here;
the gate and the host tools read it on the run host through
``corpus_search.py``, whose functions these wrap, so there is one definition of
how a word query is matched and ranked.

Every connection is ``mode=ro&immutable=1``: nothing here can write the file,
take a lock or leave a journal beside it.  Failures are ``CfError``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from cflib.errors import CfError

from . import corpus_search as _core

#: re-exported: the FTS5 ``name_words`` column's definition
name_words = _core.name_words
query_words = _core.query_words
SCHEMA = _core.SCHEMA


def open_db(path: str | Path) -> sqlite3.Connection:
    """A read-only, immutable connection to a ``cf-corpus/1`` database; ``CfError`` otherwise."""
    try:
        return _core.connect(path)
    except _core.CorpusError as exc:
        raise CfError(str(exc), hint="build it with `cf corpus build-db <universe>`") from None


def meta(conn: sqlite3.Connection) -> dict[str, str]:
    """The ``meta`` table: ``schema``, ``universe``, ``universe_sha256``, ``built``."""
    return {k: v for k, v in conn.execute("SELECT key, value FROM meta").fetchall()}


def libraries(conn: sqlite3.Connection) -> list[dict]:
    """Every library row, sorted by id."""
    rows = conn.execute("SELECT id, repo, commit_sha, toolchain, brain_lib, index_sha256, "
                        "declarations FROM libraries ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def exists(conn: sqlite3.Connection, library: str, commit: str, name: str) -> bool:
    """The exact-table check: is ``name`` a declaration of ``library`` at ``commit``?"""
    row = conn.execute("SELECT 1 FROM decls WHERE library = ? AND commit_sha = ? AND name = ?",
                       (library, commit, name)).fetchone()
    return row is not None


def declaration(conn: sqlite3.Connection, library: str, name: str) -> dict | None:
    """Every column of one declaration (proof status and statement flag included), or None."""
    row = conn.execute("SELECT * FROM decls WHERE library = ? AND name = ?",
                       (library, name)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["statement"] = bool(out["statement"])
    out["printable"] = bool(out["printable"])
    out["tags"] = [dict(t) for t in conn.execute(
        "SELECT tag, value FROM tags WHERE decl_id = ? ORDER BY tag, value", (out["id"],))]
    return out


def search(conn: sqlite3.Connection, words: str | Iterable[str], *, library: str | None = None,
           limit: int = _core.DEFAULT_LIMIT) -> list[dict]:
    """The host tool's ranked word search: words OR-ed, quoted, bm25-ranked."""
    text = words if isinstance(words, str) else " ".join(words)
    try:
        return _core.search(conn, text, library=library, limit=limit)
    except _core.CorpusError as exc:
        raise CfError(str(exc)) from None


def show(conn: sqlite3.Connection, library: str, name: str) -> dict | None:
    """What ``corpus_show.py`` prints for one declaration, or None."""
    try:
        return _core.show(conn, library, name)
    except _core.CorpusError as exc:
        raise CfError(str(exc)) from None


def tagged(conn: sqlite3.Connection, tag: str, value: str) -> list[dict]:
    """Every declaration carrying ``tag`` = ``value`` (the L2 curated join), sorted."""
    rows = conn.execute("SELECT d.library, d.commit_sha, d.name FROM tags t "
                        "JOIN decls d ON d.id = t.decl_id WHERE t.tag = ? AND t.value = ? "
                        "ORDER BY d.library, d.name", (tag, value)).fetchall()
    return [{"library": r["library"], "commit": r["commit_sha"], "declaration": r["name"]}
            for r in rows]
