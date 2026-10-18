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

**What it reads.**  The database, opened ``mode=ro&immutable=1``: no journal,
no lock, no write, whatever the file's permissions say -- and, in a gate
directory, the ``gate.json`` beside it for the database's pin.

**How it matches.**  A declaration's name and a query are split into words by
ONE rule, :func:`iter_words` (references/locate-documents.md section 8): a word is a
run of letters and digits, every other character -- ``.``, ``_``, FTS5's
operators and quotes included -- separates, and a run is split again where a
lower-case letter or a digit is followed by an upper-case one; words are
lower-cased.  So ``natDegree``, ``nat_degree`` and ``Nat.degree`` all query
``nat degree``, which is how ``Polynomial.natDegree`` is indexed.  Each query
word is quoted and the words are OR-ed: a declaration matching more words ranks
higher (``bm25`` with the name weighted over the signature over the docstring).
No query string can reach FTS5's own syntax, so ``NEAR``, a column filter or an
unbalanced quote is just a word.

**An identifier ranks first** (:func:`identifier_tier`).  ``bm25`` rewards a
name that REPEATS the query's words, so on a real library the declaration a
query names was beaten by hundreds of its own lemmas (``IsCompact`` ranked
about 452nd for ``IsCompact``, under ``isCompact_iff_isCompact_univ`` and
``IsCompact.isSigmaCompact``: field defect D9).  So the declarations whose name
matches the query as an identifier come before every word match, in three
tiers, each ranked by ``bm25`` within: the name IS the query
(:data:`TIER_NAME`); the name is the query's identifier in another spelling,
its words being exactly the query's words (:data:`TIER_IDENTIFIER`:
``is_compact`` finds ``IsCompact``, ``compact space`` finds ``CompactSpace``);
the name's last components are, at a ``.`` (:data:`TIER_COMPONENTS`: ``Prime``
finds ``Nat.Prime``, ``nat degree`` finds ``Polynomial.natDegree``); within a
tier the name with fewer words comes first (:func:`identifier_key`).  They are
found through the index -- the query's words as one phrase of the name column
-- never by a scan of the table.

**Which database.**  In a gate directory the planner stages ``gate.json`` beside
this file, and its ``tools_corpus_db.path`` is the one database these tools read:
a ``--db`` naming any other file is refused (exit 2), as is ``--db`` given twice
or abbreviated (argparse would keep the LAST of a repeated option, so
``--db <pinned> --db <full corpus>`` read the full corpus past an allowlist
whose prefix carries the pinned path).  Outside a gate directory -- no
``gate.json`` beside the file -- ``--db`` is the caller's; a ``gate.json`` there
that names no tools database is exit 3.

**Output.**  One JSON object per line, best first: ``library``, ``declaration``,
``module``, ``path``, ``line``, ``end_line``, ``kind``, ``signature``, ``score``
(``-bm25`` of the match that found the row: the name phrase for an identifier,
the query's words otherwise, so it orders a tier, never two tiers).  No line at
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
#: the identifier tiers (:func:`identifier_tier`), best first; a word match is
#: :data:`TIER_WORDS`, after all three
TIER_NAME = 0
TIER_IDENTIFIER = 1
TIER_COMPONENTS = 2
TIER_WORDS = 3

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


def identifier_words(text: str) -> list[str]:
    """The query's words in order, repeats kept: the identifier it spells; [] if it is none.

    A query of more than :data:`MAX_WORDS` words is a paragraph, not an identifier.
    """
    words: list[str] = []
    for word in iter_words(str(text or "").strip()):
        words.append(word)
        if len(words) > MAX_WORDS:
            return []
    return words


def identifier_tier(name: str, text: str) -> int | None:
    """How a declaration's ``name`` matches the query ``text`` as an identifier, or None.

    :data:`TIER_NAME` when the name is the query; :data:`TIER_IDENTIFIER` when
    the name's words (:func:`iter_words`) are exactly the query's, in order;
    :data:`TIER_COMPONENTS` when the words of the name's last components, cut at
    a ``.``, are.  The one definition: :func:`search` ranks by it, and L2
    (``scripts/locate/candidates.py``) orders its lexical hits by it.
    """
    words = identifier_words(text)
    if not words:
        return None
    if name == str(text).strip():
        return TIER_NAME
    if list(iter_words(name)) == words:
        return TIER_IDENTIFIER
    parts = str(name).split(".")
    for k in range(1, len(parts)):
        if list(iter_words(".".join(parts[k:]))) == words:
            return TIER_COMPONENTS
    return None


def identifier_key(name: str, text: str, rank: float = 0.0) -> tuple:
    """The sort key of a row for ``text``: its tier, then the fewer words, then ``bm25``.

    Within a tier the name with fewer words is the closer one (``Nat.Prime``
    before ``Nat.IsSocialistPrime.prime`` for ``Prime``); a word match is
    :data:`TIER_WORDS`.  ``rank`` is the row's ``bm25`` (lower is better).
    """
    tier = identifier_tier(name, text)
    if tier is None:
        return (TIER_WORDS, 0, rank)
    return (tier, sum(1 for _ in iter_words(name)), rank)


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


#: Every search reads the full-text index FIRST and joins each match to ``decls`` by
#: rowid.  ``CROSS JOIN`` is SQLite's documented way to fix that order, and the
#: library filter is written ``+d.library`` so no index can serve it: left to
#: itself, the planner of some SQLite versions (3.43, and any build that has
#: statistics making ``library = ?`` look selective) walked every row of the
#: library through ``decls``' index and re-ran the FTS5 match per row -- about
#: 90 s a search on a real universe, against 0.05 s unfiltered (field defect S1).
_ROW_SQL = ("SELECT d.id, d.library, d.name, d.module, d.path, d.line, d.end_line, d.kind, "
            "d.signature, bm25(decls_fts, ?, ?, ?) AS rank "
            "FROM decls_fts CROSS JOIN decls d ON d.id = decls_fts.rowid "
            "WHERE decls_fts MATCH ?")
#: the library filter, applied to the rows the index found (see :data:`_ROW_SQL`)
_LIBRARY_SQL = " AND +d.library = ?"


def _rows(conn: sqlite3.Connection, sql: str, params: list) -> list:
    try:
        return conn.execute(sql, params).fetchall()
    except (sqlite3.Error, UnicodeError, ValueError) as exc:
        raise CorpusError(f"the search failed ({exc})") from None


def _identifier_rows(conn: sqlite3.Connection, text: str, library: str | None) -> list:
    """The rows whose name matches ``text`` as an identifier, best first (tier, then bm25).

    Found through the index: the query's words as ONE phrase of the name column,
    kept when the name's words end with the phrase -- a superset of the three
    tiers -- and then sorted out by :func:`identifier_tier` itself.
    """
    words = identifier_words(text)
    if not words:
        return []
    joined = " ".join(words)
    sql = _ROW_SQL + (" AND (decls_fts.name_words = ? OR decls_fts.name_words LIKE ?)")
    params: list = [*WEIGHTS, "name_words : " + fts_match([joined]), joined, "% " + joined]
    if library:
        sql += _LIBRARY_SQL
        params.append(library)
    ranked = []
    for row in _rows(conn, sql, params):
        tier = identifier_tier(row["name"], text)
        if tier is not None:
            ranked.append((identifier_key(row["name"], text, float(row["rank"])), row))
    ranked.sort(key=lambda t: (t[0], t[1]["library"], t[1]["name"]))
    return [row for _key, row in ranked]


def search(conn: sqlite3.Connection, text: str, *, library: str | None = None,
           limit: int = DEFAULT_LIMIT, all_words: bool = False) -> list[dict]:
    """The best ``limit`` declarations for ``text``, best first.

    The identifier tiers first (:func:`identifier_tier`, each by ``bm25``), then
    the word matches by ``bm25``, no declaration twice.  ``all_words`` keeps
    only declarations matching every word (AND) instead of any of them (OR, the
    default); an identifier match holds every word, so it is kept either way.
    """
    words = query_words(text)
    if not words:
        return []
    limit = max(1, min(int(limit), MAX_LIMIT))
    first = _identifier_rows(conn, text, library)[:limit]
    sql = _ROW_SQL
    params: list = [*WEIGHTS, fts_match(words, all_words=all_words)]
    if library:
        sql += _LIBRARY_SQL
        params.append(library)
    sql += " ORDER BY rank, d.library, d.name LIMIT ?"
    params.append(limit + len(first))
    seen = {r["id"] for r in first}
    rows = first + [r for r in _rows(conn, sql, params) if r["id"] not in seen]
    return [{"library": r["library"], "declaration": r["name"], "module": r["module"],
             "path": r["path"], "line": r["line"], "end_line": r["end_line"],
             "kind": r["kind"], "signature": r["signature"],
             "score": round(-float(r["rank"]), 4)} for r in rows[:limit]]


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
    """argparse with exit 2 for every bad argument and no abbreviated options
    (``--d <file>`` is not ``--db``)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str):  # noqa: D401 - argparse's hook
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: {message}\n")
        raise SystemExit(EXIT_USAGE)


class Once(argparse.Action):
    """An option that may be given once: a second ``--db`` is an error, never the winner."""

    def __call__(self, parser, namespace, values, option_string=None):
        seen = f"_{self.dest}_given"
        if getattr(namespace, seen, False):
            parser.error(f"{option_string} is given twice; these tools read one database, the "
                         f"run's")
        setattr(namespace, seen, True)
        setattr(namespace, self.dest, values)


#: the gate directory's ``gate.json``: beside this file when the planner staged it
GATE_JSON = Path(__file__).resolve().parent / "gate.json"


def pinned_db(gate_json: Path = GATE_JSON) -> Path | None:
    """The tools database ``gate.json`` beside this file pins, or None with no ``gate.json``.

    A ``gate.json`` that cannot be read or names no ``tools_corpus_db.path`` is
    :class:`CorpusError`: in a gate directory the pin is required.
    """
    if not gate_json.is_file():
        return None
    try:
        data = json.loads(gate_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorpusError(f"{gate_json} is unreadable ({exc})") from None
    tools = data.get("tools_corpus_db") if isinstance(data, dict) else None
    path = tools.get("path") if isinstance(tools, dict) else None
    if not isinstance(path, str) or not path:
        raise CorpusError(f"{gate_json} pins no tools database (tools_corpus_db.path)")
    return Path(path).expanduser()


def check_db(ap: "argparse.ArgumentParser", db: str, gate_json: Path = GATE_JSON) -> None:
    """Refuse (exit 2) a ``--db`` that is not the run's pinned tools database."""
    pin = pinned_db(gate_json)
    if pin is not None and Path(db).expanduser().resolve() != pin.resolve():
        ap.error(f"--db {db} is not this run's tools database; {gate_json} pins {pin}")


def main(argv: list[str] | None = None) -> int:
    ap = _Parser(prog="corpus_search.py", description="search a universe's corpus.sqlite")
    ap.add_argument("--db", required=True, action=Once, help="the universe's corpus.sqlite")
    ap.add_argument("--library", default=None, help="only this library (its universe id)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"how many results (default {DEFAULT_LIMIT}, at most {MAX_LIMIT})")
    ap.add_argument("words", nargs="+", help="the words to search for")
    args = ap.parse_args(argv)
    if args.limit < 1:
        ap.error("--limit must be positive")
    check_text(ap, [args.db, args.library, *args.words])
    try:
        check_db(ap, args.db)
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
