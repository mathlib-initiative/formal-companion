"""A ``--library`` search is as fast as an unfiltered one (field defect S1).

New code, for docs/proposals/wikipedia-locate.md section 6 (the host tools) and
references/locate-documents.md section 8 (``corpus_search.py``).  On the run host
every ``corpus_search.py --library mathlib`` call took about 90 s and the same
query without the filter 0.05 s: the ``AND d.library = ?`` term let SQLite's
planner walk every row of the library through ``decls``' unique index and re-run
the FTS5 match once per row.  Whether a planner falls into that depends on its
version (3.43 does, 3.50 does not on a database without statistics) and on the
statistics it holds, so the reduction here forces the temptation the host's
planner fell for: a synthetic database of 200,000 declarations in two libraries,
the same database with ``sqlite_stat1`` rows that make ``library = ?`` look
selective, and on both the filtered search must stay within a small factor of
the unfiltered one, through the index first.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import random
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from corpus import build_db, corpus_search, query  # noqa: E402

ROWS = 200_000
#: a filtered search may cost this many times the unfiltered one, plus SLACK seconds
FACTOR = 5.0
SLACK = 0.05
QUERIES = ("widget gadget spanning", "Gadget", "prime factor unique", "w17 w18")


def _build(path: Path, rows: int = ROWS) -> None:
    """A ``cf-corpus/1`` database through the section 8 DDL: 90% alpha, 10% beta."""
    rnd = random.Random(20261018)
    words = ["widget", "gadget", "spanning", "prime", "factor", "unique", "ring", "ideal",
             "module", "field", "norm", "limit", "series", "integral", "compact", "open"]
    words += [f"w{i}" for i in range(2000)]
    conn = sqlite3.connect(path)
    for statement in build_db.DDL:
        conn.execute(statement)
    conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)",
                     [("schema", corpus_search.SCHEMA), ("universe", "u1"),
                      ("universe_sha256", "0" * 64), ("built", "2026-10-18T00:00:00Z"),
                      ("blind", "0")])
    decls, fts = [], []
    for i in range(1, rows + 1):
        library = "beta" if i % 10 == 0 else "alpha"
        head = "".join(w.capitalize() for w in rnd.sample(words, 2))
        name = f"{head}.{'_'.join(rnd.sample(words, 3))}"
        signature = " ".join(rnd.sample(words, 5))
        docstring = " ".join(rnd.sample(words, 5))
        decls.append((i, library, ("a" if library == "alpha" else "b") * 40, name, "Alpha.W",
                      "Alpha/W.lean", 1, 2, "theorem", "proved", 0, 1, signature, docstring,
                      None))
        fts.append((i, corpus_search.name_words(name), signature, docstring))
    conn.executemany("INSERT INTO decls (id, library, commit_sha, name, module, path, line, "
                     "end_line, kind, proof_status, statement, printable, signature, "
                     "docstring, deprecated_to) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                     "?, ?)", decls)
    conn.executemany("INSERT INTO decls_fts (rowid, name_words, signature, docstring) "
                     "VALUES (?, ?, ?, ?)", fts)
    conn.commit()
    conn.close()


def _tempt(path: Path) -> None:
    """Statistics that make ``library = ?`` look like one row: the host planner's mistake."""
    conn = sqlite3.connect(path)
    conn.execute("ANALYZE")
    conn.execute("UPDATE sqlite_stat1 SET stat = ? WHERE idx = 'sqlite_autoindex_decls_1'",
                 (f"{ROWS} 1 1 1",))
    conn.commit()
    conn.close()


def _best_of(fn, n: int = 3) -> float:
    best = None
    for _ in range(n):
        start = time.perf_counter()
        fn()
        took = time.perf_counter() - start
        best = took if best is None else min(best, took)
    return best


class TestALibraryFilterKeepsTheIndexFirst(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.plain = cls.tmp / "corpus.sqlite"
        _build(cls.plain)
        cls.tempted = cls.tmp / "tempted.sqlite"
        shutil.copyfile(cls.plain, cls.tempted)
        _tempt(cls.tempted)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _timings(self, db: Path):
        conn = corpus_search.connect(db)
        self.addCleanup(conn.close)
        for text in QUERIES:
            open_ = _best_of(lambda: corpus_search.search(conn, text, limit=15))
            narrow = _best_of(lambda: corpus_search.search(conn, text, library="alpha",
                                                           limit=15))
            yield text, open_, narrow

    def test_a_filtered_search_is_within_a_small_factor_of_an_unfiltered_one(self):
        for db in (self.plain, self.tempted):
            for text, open_, narrow in self._timings(db):
                with self.subTest(db=db.name, query=text):
                    self.assertLessEqual(narrow, FACTOR * open_ + SLACK,
                                         f"--library alpha took {narrow:.3f} s against "
                                         f"{open_:.3f} s unfiltered")

    def test_every_search_the_tool_issues_reads_the_index_first(self):
        conn = corpus_search.connect(self.tempted)
        self.addCleanup(conn.close)
        issued: list[str] = []
        conn.set_trace_callback(issued.append)
        corpus_search.search(conn, "widget gadget spanning", library="alpha", limit=5)
        conn.set_trace_callback(None)
        selects = [sql for sql in issued if "decls_fts MATCH" in sql]
        self.assertEqual(len(selects), 2, issued)       # the identifier tier and the words
        for sql in selects:
            # the bound values are inlined in the trace; re-plan the statement as issued
            plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)]
            self.assertTrue(plan and plan[0].startswith("SCAN decls_fts"), plan)
            self.assertFalse(any("sqlite_autoindex_decls_1" in step for step in plan), plan)

    def test_the_filter_keeps_only_the_library_and_the_order(self):
        conn = corpus_search.connect(self.tempted)
        self.addCleanup(conn.close)
        for text in QUERIES:
            narrow = corpus_search.search(conn, text, library="beta", limit=corpus_search.MAX_LIMIT)
            wide = corpus_search.search(conn, text, limit=corpus_search.MAX_LIMIT)
            self.assertTrue(narrow, text)
            self.assertEqual({r["library"] for r in narrow}, {"beta"})
            # the beta rows of the unfiltered search come in the same order
            beta = [r["declaration"] for r in wide if r["library"] == "beta"]
            self.assertEqual([r["declaration"] for r in narrow][:len(beta)], beta)

    def test_the_workspace_twin_is_the_same_search(self):
        conn = query.open_db(self.tempted)
        self.addCleanup(conn.close)
        took = _best_of(lambda: query.search(conn, "prime factor unique", library="alpha",
                                             limit=15), n=1)
        self.assertLess(took, 2.0)


if __name__ == "__main__":
    unittest.main()
