#!/usr/bin/env python3
"""Search a universe's ``corpus.sqlite`` by words: the locate worker's retrieval tool.

New code for docs/proposals/wikipedia-locate.md section 6 (tools on the host),
with the command line references/locate-documents.md section 8 fixes::

    python3 <gate>/corpus_search.py --db <corpus.sqlite> [--library L] [--limit N] <query words...>

Standard library only and importing no ``cflib``: the planner copies this file
into the read-only gate directory, where a worker runs it by absolute path, the
way it runs ``verify_findings.py``.  ``corpus_show.py`` imports its database
helpers from here, and ``scripts/corpus/query.py`` (the workspace side) wraps
the same functions, so there is one definition of how a declaration is found.

**What it reads.**  The database only, opened ``mode=ro&immutable=1``: no
journal, no lock, no write, whatever the file's permissions say.

**How it matches.**  The query is split into words (letters and digits; every
other character, FTS5's operators and quotes included, is a separator), each
word is quoted, and the words are OR-ed: a declaration matching more words
ranks higher (``bm25`` with the name weighted over the signature over the
docstring).  No query string can reach FTS5's own syntax, so ``NEAR``, a column
filter or an unbalanced quote is just a word.

**Output.**  One JSON object per line: ``library``, ``declaration``, ``module``,
``path``, ``line``, ``end_line``, ``kind``, ``signature``, ``score``.  No line at
all when nothing matched.  Exit status: 0 searched, 2 bad arguments, 3 the
database could not be read or is not a ``cf-corpus/1`` database -- a problem
of the host, never of the query.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.parse
from pathlib import Path

SCHEMA = "cf-corpus/1"
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BROKEN = 3
DEFAULT_LIMIT = 20
MAX_LIMIT = 200
#: words kept of a query; a longer query is a paragraph, not a search
MAX_WORDS = 32
#: bm25 column weights: name_words, signature, docstring
WEIGHTS = (10.0, 2.0, 1.0)

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[^\W_]+")


class CorpusError(RuntimeError):
    """The database cannot be read as a corpus; the message says why."""


def name_words(name: str) -> str:
    """``Widget.exists_spanning_gadget`` -> ``widget exists spanning gadget``.

    The name split at ``.``, ``_`` and a lower-to-upper case change (a digit
    counts as lower), lowercased and joined by single spaces: the column FTS5
    indexes a declaration's name under (references/locate-documents.md section 8).
    """
    parts: list[str] = []
    for segment in re.split(r"[._]", str(name or "")):
        for piece in _CAMEL.split(segment):
            if piece:
                parts.append(piece.lower())
    return " ".join(parts)


def query_words(text: str) -> list[str]:
    """The words of a query, lowercased, in order, without repeats, at most MAX_WORDS."""
    out: list[str] = []
    for word in _WORD.findall(str(text or "").lower()):
        if word not in out:
            out.append(word)
    return out[:MAX_WORDS]


def fts_match(words: list[str]) -> str:
    """An FTS5 MATCH expression that ORs the words, each as a quoted string."""
    return " OR ".join('"' + w.replace('"', '""') + '"' for w in words)


def connect(db: str | Path) -> sqlite3.Connection:
    """Open ``db`` read-only and immutable; refuse a file that is not a corpus."""
    path = Path(db).expanduser()
    if not path.is_file():
        raise CorpusError(f"no database at {path}")
    uri = "file:" + urllib.parse.quote(str(path.resolve())) + "?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    except sqlite3.Error as exc:
        raise CorpusError(f"{path} is not readable as a corpus database ({exc})") from None
    if row is None or row["value"] != SCHEMA:
        conn.close()
        raise CorpusError(f"{path} is not a {SCHEMA} database")
    return conn


def search(conn: sqlite3.Connection, text: str, *, library: str | None = None,
           limit: int = DEFAULT_LIMIT) -> list[dict]:
    """The best ``limit`` declarations for ``text``, best first."""
    words = query_words(text)
    if not words:
        return []
    limit = max(1, min(int(limit), MAX_LIMIT))
    sql = ("SELECT d.library, d.name, d.module, d.path, d.line, d.end_line, d.kind, "
           "d.signature, bm25(decls_fts, ?, ?, ?) AS rank "
           "FROM decls_fts JOIN decls d ON d.id = decls_fts.rowid "
           "WHERE decls_fts MATCH ?")
    params: list = [*WEIGHTS, fts_match(words)]
    if library:
        sql += " AND d.library = ?"
        params.append(library)
    sql += " ORDER BY rank, d.library, d.name LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        raise CorpusError(f"the search failed ({exc})") from None
    return [{"library": r["library"], "declaration": r["name"], "module": r["module"],
             "path": r["path"], "line": r["line"], "end_line": r["end_line"],
             "kind": r["kind"], "signature": r["signature"],
             "score": round(-float(r["rank"]), 4)} for r in rows]


def show(conn: sqlite3.Connection, library: str, name: str) -> dict | None:
    """One declaration of one library with its docstring and tags, or None."""
    try:
        row = conn.execute("SELECT id, library, name, module, path, line, end_line, kind, "
                           "signature, docstring FROM decls WHERE library = ? AND name = ?",
                           (library, name)).fetchone()
        if row is None:
            return None
        tags = conn.execute("SELECT tag, value FROM tags WHERE decl_id = ? ORDER BY tag, value",
                            (row["id"],)).fetchall()
    except sqlite3.Error as exc:
        raise CorpusError(f"the lookup failed ({exc})") from None
    return {"library": row["library"], "declaration": row["name"], "module": row["module"],
            "path": row["path"], "line": row["line"], "end_line": row["end_line"],
            "kind": row["kind"], "signature": row["signature"], "docstring": row["docstring"],
            "tags": [{"tag": t["tag"], "value": t["value"]} for t in tags]}


def emit(record: dict) -> None:
    sys.stdout.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # noqa: D401 - argparse's hook
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: {message}\n")
        raise SystemExit(EXIT_USAGE)


def main(argv: list[str] | None = None) -> int:
    ap = _Parser(prog="corpus_search.py", description="search a universe's corpus.sqlite")
    ap.add_argument("--db", required=True, help="the universe's corpus.sqlite")
    ap.add_argument("--library", default=None, help="only this library (its universe id)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"how many results (default {DEFAULT_LIMIT}, at most {MAX_LIMIT})")
    ap.add_argument("words", nargs="+", help="the words to search for")
    args = ap.parse_args(argv)
    if args.limit < 1:
        ap.error("--limit must be positive")
    try:
        conn = connect(args.db)
        try:
            for record in search(conn, " ".join(args.words), library=args.library,
                                 limit=args.limit):
                emit(record)
        finally:
            conn.close()
    except CorpusError as exc:
        sys.stderr.write(f"corpus_search: {exc} (a problem of the host, not of your query)\n")
        return EXIT_BROKEN
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
