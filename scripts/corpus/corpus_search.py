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

**How it matches.**  A declaration's name and a query are split into words by
ONE rule, :func:`words` (references/locate-documents.md section 8): a word is a
run of letters and digits, every other character -- ``.``, ``_``, FTS5's
operators and quotes included -- separates, and a run is split again where a
lower-case letter or a digit is followed by an upper-case one; words are
lower-cased.  So ``natDegree``, ``nat_degree`` and ``Nat.degree`` all query
``nat degree``, which is how ``Polynomial.natDegree`` is indexed.  Each query
word is quoted and the words are OR-ed: a declaration matching more words ranks
higher (``bm25`` with the name weighted over the signature over the docstring).
No query string can reach FTS5's own syntax, so ``NEAR``, a column filter or an
unbalanced quote is just a word.

**Output.**  One JSON object per line: ``library``, ``declaration``, ``module``,
``path``, ``line``, ``end_line``, ``kind``, ``signature``, ``score``.  No line at
all when nothing matched.  Exit status: 0 searched, 2 bad arguments (an argument
that is not valid UTF-8 included), 3 the database could not be read or is not a
``cf-corpus/1`` database -- a problem of the host, never of the query.  Never a
traceback.
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

#: a run of letters and digits (``_`` is a word character to ``\w``, not here)
_RUN = re.compile(r"[^\W_]+")


class CorpusError(RuntimeError):
    """The database cannot be read as a corpus; the message says why."""


def _split_run(run: str):
    """One letter-and-digit run, split where a lower-case letter or digit meets an upper-case one."""
    start = 0
    for i in range(1, len(run)):
        if run[i].isupper() and (run[i - 1].islower() or run[i - 1].isdigit()):
            yield run[start:i]
            start = i
    yield run[start:]


def iter_words(text: str):
    """The words of ``text`` by the one rule, lower-cased, in order (a generator)."""
    for match in _RUN.finditer(str(text or "")):
        for piece in _split_run(match.group(0)):
            if piece:
                yield piece.lower()


def name_words(name: str) -> str:
    """``Widget.exists_spanning_gadget`` -> ``widget exists spanning gadget``.

    The column FTS5 indexes a declaration's name under (references/locate-documents.md
    section 8): :func:`iter_words` of the name joined by single spaces, so
    ``Polynomial.natDegree`` is ``polynomial nat degree`` and ``T2Space`` is
    ``t2 space``.
    """
    return " ".join(iter_words(name))


def query_words(text: str) -> list[str]:
    """A query's words by the same rule, without repeats, the first MAX_WORDS of them.

    Linear in the query: it stops reading once it has MAX_WORDS distinct words,
    so a pasted paragraph costs no more than its first few lines.
    """
    out: list[str] = []
    seen: set[str] = set()
    for word in iter_words(text):
        if word not in seen:
            seen.add(word)
            out.append(word)
            if len(out) >= MAX_WORDS:
                break
    return out


def fts_match(words: list[str], *, all_words: bool = False) -> str:
    """An FTS5 MATCH expression over the words, each a quoted string: OR-ed, or AND-ed."""
    return (" AND " if all_words else " OR ").join('"' + w.replace('"', '""') + '"'
                                                   for w in words)


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
           limit: int = DEFAULT_LIMIT, all_words: bool = False) -> list[dict]:
    """The best ``limit`` declarations for ``text``, best first.

    ``all_words`` keeps only declarations matching every word (AND) instead of
    any of them (OR, the default); the ranking is the same either way.
    """
    words = query_words(text)
    if not words:
        return []
    limit = max(1, min(int(limit), MAX_LIMIT))
    sql = ("SELECT d.library, d.name, d.module, d.path, d.line, d.end_line, d.kind, "
           "d.signature, bm25(decls_fts, ?, ?, ?) AS rank "
           "FROM decls_fts JOIN decls d ON d.id = decls_fts.rowid "
           "WHERE decls_fts MATCH ?")
    params: list = [*WEIGHTS, fts_match(words, all_words=all_words)]
    if library:
        sql += " AND d.library = ?"
        params.append(library)
    sql += " ORDER BY rank, d.library, d.name LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except (sqlite3.Error, UnicodeError, ValueError) as exc:
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
    except (sqlite3.Error, UnicodeError, ValueError) as exc:
        raise CorpusError(f"the lookup failed ({exc})") from None
    return {"library": row["library"], "declaration": row["name"], "module": row["module"],
            "path": row["path"], "line": row["line"], "end_line": row["end_line"],
            "kind": row["kind"], "signature": row["signature"], "docstring": row["docstring"],
            "tags": [{"tag": t["tag"], "value": t["value"]} for t in tags]}


def check_text(ap: "argparse.ArgumentParser", values) -> None:
    """Refuse (exit 2) an argument that is not valid UTF-8.

    A byte that does not decode arrives as a lone surrogate, which sqlite3 cannot
    bind; it is a bad argument, and saying so is better than a traceback.
    """
    for value in values:
        if value is None:
            continue
        try:
            str(value).encode("utf-8")
        except UnicodeEncodeError:
            ap.error(f"argument {str(value).encode('utf-8', 'backslashreplace').decode()!r} "
                     "is not valid UTF-8")


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
    check_text(ap, [args.db, args.library, *args.words])
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
