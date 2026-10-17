#!/usr/bin/env python3
"""Show one declaration of a universe's ``corpus.sqlite``: the locate worker's reading tool.

New code for docs/proposals/wikipedia-locate.md section 6 (tools on the host),
with the command line references/locate-documents.md section 8 fixes::

    python3 <gate>/corpus_show.py --db <corpus.sqlite> <library> <declaration>

Standard library only, importing no ``cflib``; its database helpers are
``corpus_search.py``'s, which the planner copies into the same gate directory.
It prints one JSON line -- ``library``, ``declaration``, ``module``, ``path``,
``line``, ``end_line``, ``kind``, ``signature``, ``docstring``, ``tags`` -- and
reads nothing but the database.  The declaration's proof status is not shown:
it is joined from the index at export and never written by a worker.

Exit status: 0 shown, 1 no such declaration in that library (an answer, not a
failure), 2 bad arguments, 3 the database could not be read.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from corpus_search import (EXIT_BROKEN, EXIT_OK, CorpusError, _Parser, connect,  # noqa: E402
                           emit, show)

EXIT_NOT_FOUND = 1


def main(argv: list[str] | None = None) -> int:
    ap = _Parser(prog="corpus_show.py", description="show one declaration of a corpus.sqlite")
    ap.add_argument("--db", required=True, help="the universe's corpus.sqlite")
    ap.add_argument("library", help="the library's universe id")
    ap.add_argument("declaration", help="the declaration's full Lean name")
    args = ap.parse_args(argv)
    try:
        conn = connect(args.db)
        try:
            record = show(conn, args.library, args.declaration)
        finally:
            conn.close()
    except CorpusError as exc:
        sys.stderr.write(f"corpus_show: {exc} (a problem of the host, not of your query)\n")
        return EXIT_BROKEN
    if record is None:
        sys.stderr.write(f"corpus_show: {args.library} has no declaration {args.declaration}\n")
        return EXIT_NOT_FOUND
    emit(record)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
