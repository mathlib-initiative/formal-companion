"""Synthetic inputs for the bench tests, built by the documented formats only.

New code.  The bench reads what four other packages write (a sourceset, a
universe with its ``corpus.sqlite``, a harvested locate run, a gate log); those
packages are built beside it, so these fixtures write their files directly from
references/locate-documents.md sections 7 to 10.  Every article, library and
declaration is made up ("Widget lemma", page 1001, the libraries ``mathlib``
and ``alpha``), and every tree is written under a temporary directory.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cflib.yamlio import dump_yaml  # noqa: E402

COMMIT_M = "a" * 40
COMMIT_A = "b" * 40
SOURCESET = "wpm"
UNIVERSE = "u1"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def write_workspace(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(
        "version: 2\nworkspace:\n  name: bench-test\ncompanions: {}\nsources: {}\n"
        "hosts:\n  allowlist: []\n", encoding="utf-8")
    return root


def article_paragraphs(n: int) -> list[str]:
    """Four paragraphs per article, each unique to the article."""
    return [
        f"The widget lemma {n} states that every finite widget of order {n} has a spanning gadget.",
        f"Theorem. Let G be a finite widget of rank {n}; then the gadgets of G are conjugate in pairs.",
        f"History of widget {n}: the result was first stated in a letter about gadget counting.",
        f"A gadget {n} is called spanning when it meets every face of the widget exactly once.",
    ]


class World:
    """One workspace with a sourceset, a universe and the runs a test asks for."""

    def __init__(self, root: Path, articles: int = 6):
        self.root = write_workspace(root)
        self.articles: dict[str, dict] = {}
        entries = []
        for i in range(articles):
            pageid, revision = 1001 + i, 2002 + i
            sid = f"enwiki-{pageid}-{revision}"
            paras = article_paragraphs(i)
            layer = " ".join(paras)
            spans, at = [], 0
            for p in paras:
                spans.append([at, at + len(p)])
                at += len(p) + 1
            sdir = self.root / "sourcesets" / SOURCESET / "sources" / sid
            sdir.mkdir(parents=True)
            (sdir / "layer.txt").write_text(layer, encoding="utf-8")
            (sdir / "text.txt").write_text("\n\n".join(paras) + "\n", encoding="utf-8")
            (sdir / "sections.json").write_text(json.dumps([
                {"path": [], "fragment": None, "level": 1, "span": [0, spans[1][0]]},
                {"path": ["Theorems"], "fragment": "Theorems", "level": 2,
                 "span": [spans[1][0], len(layer)]}]), encoding="utf-8")
            (sdir / "paragraphs.json").write_text(json.dumps([
                {"span": s, "section": [] if k == 0 else ["Theorems"], "block": "p",
                 "formulas": 0, "math": k in (0, 1, 3)} for k, s in enumerate(spans)]),
                encoding="utf-8")
            (sdir / "finders.json").write_text(json.dumps([
                {"finder": "lead", "start": spans[0][0], "span": spans[0], "section": [],
                 "term": "widget lemma"},
                {"finder": "theorem_box", "start": spans[1][0], "span": spans[1],
                 "section": ["Theorems"], "term": ""}]), encoding="utf-8")
            qid = f"Q9999900{i}" if i != 5 else "Q99999000"   # article 5 shares article 0's QID
            entry = {"source_id": sid, "family": "enwiki", "pageid": pageid, "revision": revision,
                     "title": f"Widget lemma {i}", "wikidata": qid,
                     "assessment": {"class": "B", "importance": "high" if i % 2 else "mid"},
                     "origin": "enumerated", "licence_class": "open_share_alike",
                     "fetch": None, "raw_sha256": "0" * 64, "html_sha256": "1" * 64,
                     "layer_sha256": hashlib.sha256(layer.encode("utf-8")).hexdigest(),
                     "renderer": "web-html/1"}
            entries.append(entry)
            self.articles[sid] = {"entry": entry, "layer": layer, "spans": spans, "index": i}
        header = {"schema": "cf-sourceset/1", "id": SOURCESET, "version": 1, "family": "enwiki",
                  "previous_sha256": None, "created": "2026-10-17T00:00:00Z",
                  "sources": len(entries)}
        manifest = self.root / "sourcesets" / SOURCESET / "manifest-1.jsonl"
        manifest.write_text("".join(canonical(x) + "\n" for x in [header, *sorted(
            entries, key=lambda e: e["source_id"])]), encoding="utf-8")
        self.manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        self.build_universe()

    # -- the universe ---------------------------------------------------------
    def build_universe(self, declarations: list[tuple] | None = None) -> None:
        udir = self.root / "corpora" / UNIVERSE
        udir.mkdir(parents=True, exist_ok=True)
        self.universe_sha = hashlib.sha256(
            f"alpha\t{COMMIT_A}\t{'c' * 64}\nmathlib\t{COMMIT_M}\t{'d' * 64}".encode()).hexdigest()
        universe = {
            "id": UNIVERSE, "frozen": "2026-10-17",
            "libraries": [
                {"id": "mathlib", "brain_lib": "Mathlib", "repo": "https://example.org/mathlib",
                 "commit": COMMIT_M, "toolchain": "leanprover/lean4:v4.0.0",
                 "index": {"path": "index/mathlib/", "sha256": "d" * 64, "declarations": 9}},
                {"id": "alpha", "brain_lib": "Alpha", "repo": "https://example.org/alpha",
                 "commit": COMMIT_A, "toolchain": "leanprover/lean4:v4.0.0",
                 "index": {"path": "index/alpha/", "sha256": "c" * 64, "declarations": 2}}],
            "excluded": [], "universe_sha256": self.universe_sha}
        (udir / "universe.yaml").write_text(dump_yaml(universe), encoding="utf-8")
        db = udir / "corpus.sqlite"
        if db.exists():
            db.unlink()
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE libraries (id TEXT PRIMARY KEY, repo TEXT NOT NULL, commit_sha TEXT NOT NULL,
              toolchain TEXT, brain_lib TEXT, index_sha256 TEXT NOT NULL, declarations INTEGER NOT NULL);
            CREATE TABLE decls (id INTEGER PRIMARY KEY, library TEXT NOT NULL, commit_sha TEXT NOT NULL,
              name TEXT NOT NULL, module TEXT, path TEXT, line INTEGER, end_line INTEGER, kind TEXT,
              proof_status TEXT NOT NULL, statement INTEGER NOT NULL DEFAULT 0,
              printable INTEGER NOT NULL DEFAULT 1, signature TEXT, docstring TEXT,
              UNIQUE (library, commit_sha, name));
            CREATE VIRTUAL TABLE decls_fts USING fts5(name_words, signature, docstring, tokenize = 'unicode61');
            CREATE TABLE tags (decl_id INTEGER NOT NULL, tag TEXT NOT NULL, value TEXT NOT NULL);
            CREATE INDEX tags_by_value ON tags (tag, value);
        """)
        conn.execute("INSERT INTO meta VALUES ('schema', 'cf-corpus/1')")
        conn.execute("INSERT INTO libraries VALUES ('mathlib', 'https://example.org/mathlib', ?, "
                     "'v', 'Mathlib', ?, 9)", (COMMIT_M, "d" * 64))
        conn.execute("INSERT INTO libraries VALUES ('alpha', 'https://example.org/alpha', ?, "
                     "'v', 'Alpha', ?, 2)", (COMMIT_A, "c" * 64))
        rows = declarations if declarations is not None else self.default_declarations()
        for lib, name, module, status in rows:
            commit = COMMIT_M if lib == "mathlib" else COMMIT_A
            cur = conn.execute(
                "INSERT INTO decls (library, commit_sha, name, module, path, line, end_line, kind, "
                "proof_status, statement, signature, docstring) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (lib, commit, name, module, module.replace(".", "/") + ".lean", 10, 20,
                 "theorem" if status == "proved" else "def", status, 0, f"sig of {name}", ""))
            conn.execute("INSERT INTO decls_fts (rowid, name_words, signature, docstring) "
                         "VALUES (?,?,?,?)", (cur.lastrowid, name.replace(".", " "), "", ""))
        # one @[wikidata] tag: article 2's QID on Widget.tagged_two
        tagged = conn.execute("SELECT id FROM decls WHERE name = 'Widget.tagged_two'").fetchone()
        if tagged:
            conn.execute("INSERT INTO tags VALUES (?, 'wikidata', 'Q99999002')", (tagged[0],))
        conn.commit()
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.close()
        self.corpus_sha = hashlib.sha256(db.read_bytes()).hexdigest()

    @staticmethod
    def default_declarations() -> list[tuple]:
        return [
            ("mathlib", "Widget.spanning_zero", "Mathlib.Widget.Basic", "proved"),
            ("mathlib", "Widget.spanning_one", "Mathlib.Widget.Basic", "proved"),
            ("mathlib", "Widget.mul_spanning", "Mathlib.Widget.Basic", "proved"),
            ("mathlib", "Widget.add_spanning", "Mathlib.Widget.Basic", "proved"),
            ("mathlib", "Widget.tagged_two", "Mathlib.Widget.Tags", "proved"),
            ("mathlib", "Widget.new_name", "Mathlib.Widget.Basic", "proved"),
            ("mathlib", "Widget.conj_three", "Mathlib.Widget.Conj", "proved"),
            ("mathlib", "Widget.sorry_four", "Mathlib.Widget.Conj", "statement_only"),
            ("mathlib", "Gadget.Spanning", "Mathlib.Widget.Gadget", "definition"),
            ("alpha", "Alpha.widget_lemma", "Alpha.Widget", "proved"),
            ("alpha", "Alpha.unrelated", "Alpha.Other", "proved"),
        ]

    def sid(self, i: int) -> str:
        return f"enwiki-{1001 + i}-{2002 + i}"

    def para(self, i: int, k: int) -> list[int]:
        return list(self.articles[self.sid(i)]["spans"][k])

    def quote(self, i: int, k: int, words: int = 8) -> dict:
        text = self.articles[self.sid(i)]["layer"]
        a, b = self.para(i, k)
        return {"exact": " ".join(text[a:b].split()[:words])}

    # -- files ----------------------------------------------------------------
    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def frame_file(self, ids: list[int] | None = None) -> Path:
        ids = list(range(len(self.articles))) if ids is None else ids
        return self.write("inputs/tier-a.txt", "".join(f"{1001 + i}\n" for i in ids))

    def curated_1000(self) -> Path:
        return self.write("inputs/1000.yaml",
                          "Q99999000:\n  title: Widget lemma 0\n  decl: Widget.spanning_zero\n"
                          "Q99999001:\n  title: Widget lemma 1\n  decls:\n    - Widget.mul_spanning\n"
                          "Q99999003:\n  title: Widget lemma 3\n  decl: Widget.old_name\n"
                          "Q99999004:\n  title: Widget lemma 4\n  decl: Widget.gone_forever\n"
                          "Q88888888:\n  title: Not in the frame\n  decl: Widget.spanning_zero\n"
                          "Q77777777:\n  title: Not formalized\n")

    def curated_100(self) -> Path:
        return self.write("inputs/100.yaml", "1:\n  title: Widget lemma 1\n  decls:\n    - Widget.spanning_one\n")

    def renames(self) -> Path:
        return self.write("inputs/renames.txt", "Widget.old_name -> Widget.new_name\n")

    # -- a harvested run ------------------------------------------------------
    def run(self, run_id: str, *, inventory_run: str, passages: dict[str, list[dict]],
            outcomes: list[dict] | None = None, arm: str = "assisted", complete: bool = True,
            cost: dict | None = None, corpus_db_sha: str | None = None,
            unresolved: set[str] = frozenset(), skip_docs: set[str] = frozenset(),
            planned: list[str] | None = None, models: dict | None = None,
            prompts: dict | None = None, renderer_layer: dict | None = None) -> None:
        """An inventory run and a locate run whose outcomes are ``outcomes``."""
        for rid, mode in ((inventory_run, "locate-inventory"), (run_id, "locate")):
            self.write(f"runs/{rid}/run.yaml", dump_yaml({
                "run_id": rid, "preset": mode, "prompts": prompts or {"worker.md": "e" * 64},
                "versions": {"agent_cli": "2.0.0", "tend": "0.9", "agentbox": "1.1"},
                "locate": {"mode": mode, "arm": arm,
                           "inventory_run": inventory_run if mode == "locate" else None,
                           "sourceset": {"id": SOURCESET, "version": 1,
                                         "manifest_sha256": self.manifest_sha},
                           "universe": {"id": UNIVERSE, "sha256": self.universe_sha,
                                        "corpus_db_sha256": corpus_db_sha or self.corpus_sha}}}))
            self.write(f"runs/{rid}/gate.json", json.dumps({"schema": "cf-gate-locate/1", "mode": mode}))
            block = cost if cost is not None else {
                "usd": 12.0, "tokens": models or {"claude-opus-test": {"input": 100, "output": 50}},
                "price_table_sha256": "f" * 64, "message_count": 10, "transcript_count": 2}
            self.write(f"corpus/{rid}/RECEIPT.json", json.dumps(
                {"run_id": rid, "complete": complete, "cost": block}))
        sids = planned if planned is not None else sorted(passages)
        self.write(f"runs/{inventory_run}/tend-root/gate/tasks/t-inv.json", json.dumps(
            {"task_id": "t-inv", "mode": "locate-inventory", "run_id": inventory_run,
             "documents": [{"source_id": s} for s in sids]}))
        for sid, rows in sorted(passages.items()):
            if sid in skip_docs:
                continue
            layer_sha = (renderer_layer or {}).get(sid) or self.articles[sid]["entry"]["layer_sha256"]
            doc = {"schema": "cf-locate/1", "mode": "locate-inventory", "run_id": inventory_run,
                   "task_id": "t-inv", "source_id": sid, "source_sha": "1" * 64,
                   "layer_sha256": layer_sha, "renderer": "web-html/1",
                   "passages": [] if sid in unresolved else rows}
            if sid in unresolved:
                doc["unresolved"] = {"reason": "the article is a list", "by": "worker"}
            self.write(f"corpus/{inventory_run}/findings/t-inv/{sid}.json", json.dumps(doc))
        self.write(f"reports/locate-outcomes.{run_id}.jsonl",
                   "".join(canonical(r) + "\n" for r in (outcomes or [])))


def passage(world: World, i: int, k: int, item: str, *, scope: str = "formal_math",
            kind: str = "theorem") -> dict:
    return {"item": item, "span": world.para(i, k), "scope": scope, "passage_kind": kind,
            "prominence": "central", "quote": world.quote(i, k)}


def outcome(world: World, i: int, item: str, *, status: str | None, outcome_: str,
            locations: list[tuple] = (), inventory_run: str = "inv1", run_id: str = "loc1",
            scope: str = "formal_math") -> dict:
    return {"source_id": world.sid(i), "item": item, "passage_kind": "theorem",
            "prominence": "central", "scope": scope, "outcome": outcome_,
            "locations": [{"library": lib, "commit": COMMIT_M if lib == "mathlib" else COMMIT_A,
                           "declaration": name, "relation": rel, "rank": rank,
                           "confidence": "high", "proof_status": "proved", "statement": False,
                           "location_key": "k", "challenge": "survived"}
                          for rank, (lib, name, rel) in enumerate(locations, 1)],
            "absence": None, "challenge": "survived", "status": status,
            "runs": {"inventory": inventory_run, "locate": run_id, "challenge": None}}
