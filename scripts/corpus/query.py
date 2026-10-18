"""Read a universe's ``corpus.sqlite``: the API the other locate stages call.

New code for docs/proposals/wikipedia-locate.md sections 2.4 and 3 (L2), over
the schema references/locate-documents.md section 8 fixes.  The candidates
stage, the outcomes join, the export, the bench and ``cf corpus search|show``
read the database through here and nowhere else; the gate and the host tools
read it on the run host through ``corpus_search.py``, whose functions these
wrap, so there is one definition of how a word query is matched and ranked and
one place that knows the schema's column names.

Every connection is ``mode=ro&immutable=1``: nothing here can write the file,
take a lock or leave a journal beside it.  Failures are ``CfError``.  A caller
that pins a database to a universe calls :func:`check_universe` first.

The read surface:

* :func:`open_db`, :func:`meta`, :func:`check_universe`, :func:`is_blind`,
  :func:`libraries`;
* :func:`exists` -- the exact-table check on ``(library, commit, name)``;
* :func:`declaration` -- every column of one declaration (``proof_status``,
  ``statement``, ``deprecated_to`` ...) with its tags;
* :func:`search` -- the host tool's ranked word search, any word or all words;
* :func:`show` -- what ``corpus_show.py`` prints;
* :func:`tagged` and :func:`tags_of_kind` -- the tag joins, each row with the
  declaration's path, line and the tag's value.
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
    """The ``meta`` table: ``schema``, ``universe``, ``universe_sha256``, ``built``, ``blind``."""
    return {k: v for k, v in conn.execute("SELECT key, value FROM meta").fetchall()}


def is_blind(conn: sqlite3.Connection) -> bool:
    """Is this the G1-blind database (no tags, no docstring Wikipedia URLs)?"""
    return meta(conn).get("blind") == "1"


def check_universe(conn: sqlite3.Connection, universe, *, blind: bool | None = None) -> dict:
    """Refuse a database that does not describe ``universe`` as it is now; its meta otherwise.

    ``universe`` is a ``corpus.universe.Universe``.  The database must be a
    ``cf-corpus/1`` one whose ``meta.universe_sha256`` is ``universe.sha256()``
    -- an index ingested after the build changes the universe and makes the
    database stale.  With ``blind`` given, the database must also be (or not
    be) the G1-blind one.
    """
    got = meta(conn)
    hint = f"`cf corpus build-db {universe.id}" + (" --blind`" if blind else "`")
    if got.get("schema") != SCHEMA:
        raise CfError(f"the database is {got.get('schema')!r}, not a {SCHEMA} database",
                      hint=hint)
    if got.get("universe_sha256") != universe.sha256():
        raise CfError(f"the database was built for another state of universe {universe.id} "
                      f"(its universe_sha256 {str(got.get('universe_sha256'))[:12]}..., the "
                      f"universe's {universe.sha256()[:12]}...)", hint=hint)
    if blind is not None and (got.get("blind") == "1") != bool(blind):
        raise CfError(f"the database is {'' if got.get('blind') == '1' else 'not '}the "
                      f"G1-blind one, and this caller needs {'it' if blind else 'the full one'}",
                      hint=hint)
    return got


def libraries(conn: sqlite3.Connection) -> list[dict]:
    """Every library row, sorted by id.

    Keys: ``id, repo, commit_sha, toolchain, brain_lib, index_sha256, declarations``.
    """
    rows = conn.execute("SELECT id, repo, commit_sha, toolchain, brain_lib, index_sha256, "
                        "declarations FROM libraries ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def exists(conn: sqlite3.Connection, library: str, commit: str, name: str) -> bool:
    """The exact-table check: is ``name`` a declaration of ``library`` at ``commit``?"""
    row = conn.execute("SELECT 1 FROM decls WHERE library = ? AND commit_sha = ? AND name = ?",
                       (library, commit, name)).fetchone()
    return row is not None


def declaration(conn: sqlite3.Connection, library: str, name: str, *,
                commit: str | None = None) -> dict | None:
    """Every column of one declaration, with its tags, or None.

    The columns are the ``decls`` table's (``commit_sha``, ``proof_status``,
    ``statement`` and ``printable`` as booleans, ``deprecated_to`` ...) plus
    ``tags``: ``[{tag, value}]``.  ``commit`` narrows to that commit, the
    exact-table key; a database holds one commit per library, so without it the
    library and name are enough.
    """
    sql = "SELECT * FROM decls WHERE library = ? AND name = ?"
    params: list = [library, name]
    if commit is not None:
        sql += " AND commit_sha = ?"
        params.append(commit)
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["statement"] = bool(out["statement"])
    out["printable"] = bool(out["printable"])
    out["tags"] = [dict(t) for t in conn.execute(
        "SELECT tag, value FROM tags WHERE decl_id = ? ORDER BY tag, value", (out["id"],))]
    return out


def search(conn: sqlite3.Connection, words: str | Iterable[str], *, library: str | None = None,
           limit: int = _core.DEFAULT_LIMIT, all_words: bool = False) -> list[dict]:
    """The host tool's ranked word search: words split by the one rule, quoted, bm25-ranked.

    Any word matches by default (OR); ``all_words`` keeps only declarations
    matching every word (AND).  Rows: ``library, declaration, module, path,
    line, end_line, kind, signature, score``.
    """
    text = words if isinstance(words, str) else " ".join(words)
    try:
        return _core.search(conn, text, library=library, limit=limit, all_words=all_words)
    except _core.CorpusError as exc:
        raise CfError(str(exc)) from None


def show(conn: sqlite3.Connection, library: str, name: str) -> dict | None:
    """What ``corpus_show.py`` prints for one declaration, or None."""
    try:
        return _core.show(conn, library, name)
    except _core.CorpusError as exc:
        raise CfError(str(exc)) from None


_TAG_ROWS = ("SELECT d.library, d.commit_sha, d.name, d.path, d.line, t.tag, t.value "
             "FROM tags t JOIN decls d ON d.id = t.decl_id")


def _tag_row(r) -> dict:
    return {"library": r["library"], "commit": r["commit_sha"], "declaration": r["name"],
            "path": r["path"], "line": r["line"], "tag": r["tag"], "value": r["value"]}


def tagged(conn: sqlite3.Connection, tag: str, value: str) -> list[dict]:
    """Every declaration carrying ``tag`` = ``value`` (the L2 curated join), sorted.

    Rows: ``library, commit, declaration, path, line, tag, value``.
    """
    rows = conn.execute(_TAG_ROWS + " WHERE t.tag = ? AND t.value = ? "
                        "ORDER BY d.library, d.name", (tag, value)).fetchall()
    return [_tag_row(r) for r in rows]


def tags_of_kind(conn: sqlite3.Connection, tag: str) -> list[dict]:
    """Every row of one tag kind (``cflib.vocab.TAG_KINDS``), sorted by value, library, name.

    Rows as :func:`tagged`'s; what an index keyed on the tag's value is built from.
    """
    rows = conn.execute(_TAG_ROWS + " WHERE t.tag = ? ORDER BY t.value, d.library, d.name",
                        (tag,)).fetchall()
    return [_tag_row(r) for r in rows]
