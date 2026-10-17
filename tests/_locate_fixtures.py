"""Synthetic locate inputs, built by the formats of references/locate-documents.md.

New code, for the planner's tests (tests/test_locate_planner.py and its
siblings).  The sourceset, the rendered web sources, the universe and
``corpus.sqlite`` are other packages' artifacts; these tests build them from
the documented format (sections 7 and 8) so the planner is tested against the
format and nothing else.  Every page, library and declaration is made up: the
article "Widget lemma" (page 1001, revision 2002, Q99999001), "Gadget theorem"
(page 1003, revision 2004, Q99999002) and the libraries ``alpha`` and ``beta``.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import io
import json
import sqlite3
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main  # noqa: E402
from cflib import hashing, items  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from gate import locate_text  # noqa: E402

ALPHA = "a" * 40
BETA = "b" * 40
SS = "wpm"
U = "u1"
W1 = "enwiki-1001-2002"
W2 = "enwiki-1003-2004"
W3 = "enwiki-1005-2006"          # enumerated, never rendered

#: one article as paragraphs; a paragraph is (section path, block, pieces); a
#: piece is text or ("math", tex) or ("bold", text)
ARTICLES = {
    W1: {
        "title": "Widget lemma", "pageid": 1001, "revision": 2002, "wikidata": "Q99999001",
        "assessment": {"class": "B", "importance": "high"},
        "blocks": [
            ([], "p", ["In widget theory, the ", ("bold", "widget lemma"),
                       " states that every finite widget has a spanning gadget, and it is "
                       "a central result of the subject that every course on widgets covers."]),
            (["Statement"], "h2", ["Statement"]),
            (["Statement"], "theorem", ["Theorem. Let ", ("math", "G"), " be a finite widget of "
                                        "order ", ("math", "n"), ". Then ", ("math", "G"),
                                        " has a spanning gadget whose size divides ",
                                        ("math", "n"), " and no smaller gadget spans it."]),
            (["Statement"], "p", ["A gadget is called ", ("bold", "spanning"), " when it meets "
                                  "every sprocket of the widget, and any two spanning gadgets "
                                  "are conjugate under the widget group action on sprockets."]),
            (["History"], "h2", ["History"]),
            (["History"], "p", ["The lemma was first stated by a committee of widget theorists "
                                "in a widely circulated letter that nobody has since found."]),
        ],
    },
    W2: {
        "title": "Gadget theorem", "pageid": 1003, "revision": 2004, "wikidata": "Q99999002",
        "assessment": {"class": "C", "importance": "mid"},
        "blocks": [
            ([], "p", ["The ", ("bold", "gadget theorem"), " says that the gadgets of a finite "
                       "widget form a lattice under inclusion whose height is bounded by the "
                       "order of the widget itself."]),
            (["Proof"], "h2", ["Proof"]),
            (["Proof"], "p", ["The proof inducts on the order ", ("math", "n"), " and uses the "
                              "widget lemma at every step of the induction on the lattice."]),
        ],
    },
}


def render_article(article: dict) -> dict:
    """Layer, formulas, head, finders, paragraphs and sections of one synthetic article."""
    layer = ""
    formulas: list[dict] = []
    paragraphs: list[dict] = []
    finders: list[dict] = []
    headings: list[tuple[int, list[str], str]] = []
    first_paragraph = True
    for path, block, pieces in article["blocks"]:
        if layer:
            layer += " "
        start = len(layer)
        count = 0
        terms: list[str] = []
        for piece in pieces:
            if isinstance(piece, tuple) and piece[0] == "math":
                alttext = "{\\displaystyle " + piece[1] + "}"
                text = piece[1] + alttext
                formulas.append({"span": [len(layer), len(layer) + len(text)],
                                 "alttext": alttext})
                layer += text
                count += 1
            elif isinstance(piece, tuple) and piece[0] == "bold":
                terms.append(piece[1])
                layer += piece[1]
            else:
                layer += piece
        end = len(layer)
        if block == "h2":
            headings.append((start, list(path), pieces[0]))
            continue
        hits = []
        if block == "theorem":
            hits.append({"finder": "theorem_box", "term": ""})
        if terms and block == "p":
            hits.append({"finder": "bold_definition", "term": terms[0]})
        if first_paragraph and block == "p":
            hits.append({"finder": "lead", "term": ""})
            first_paragraph = False
        for hit in hits:
            finders.append({"finder": hit["finder"], "start": start, "span": [start, end],
                            "section": list(path), "term": hit["term"]})
        paragraphs.append({"span": [start, end], "section": list(path),
                           "block": "div" if block == "theorem" else block,
                           "formulas": count, "math": bool(count or hits)})
    sections = []
    bounds = [0] + [h[0] for h in headings] + [len(layer)]
    sections.append({"path": [], "fragment": None, "level": 1, "span": [0, bounds[1]]})
    for i, (start, path, title) in enumerate(headings):
        sections.append({"path": path, "fragment": title, "level": 2,
                         "span": [start, bounds[i + 2]]})
    head = locate_text.HeadMap(layer, formulas).head
    return {"layer": layer, "formulas": formulas, "head": head, "finders": finders,
            "paragraphs": paragraphs, "sections": sections}


def cf(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cf_main.cli(list(argv))
        except SystemExit as exc:          # argparse
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def make_workspace(root: Path) -> WorkspacePaths:
    code, out, err = cf("init", str(root), "--no-git", "-q")
    assert code == 0, out + err
    return WorkspacePaths(root)


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def write_sourceset(paths: WorkspacePaths, *, sources=(W1, W2), unrendered=(W3,),
                    version: int = 1) -> Path:
    """A rendered sourceset ``wpm`` at ``version``; returns the manifest path."""
    entries = []
    for sid in sources:
        art = ARTICLES[sid]
        rendered = render_article(art)
        d = paths.sourceset_source_dir(SS, sid)
        d.mkdir(parents=True, exist_ok=True)
        body = "<div>" + rendered["layer"] + "</div>"
        (d / "body.html").write_text(body, encoding="utf-8")
        (d / "source.raw.html").write_text(body, encoding="utf-8")
        (d / "layer.txt").write_text(rendered["layer"], encoding="utf-8")
        (d / "head.txt").write_text(rendered["head"], encoding="utf-8")
        (d / "text.txt").write_text(rendered["layer"], encoding="utf-8")
        for name in ("formulas", "finders", "paragraphs", "sections"):
            (d / f"{name}.json").write_text(json.dumps(rendered[name]), encoding="utf-8")
        entry = {"source_id": sid, "family": "enwiki", "pageid": art["pageid"],
                 "revision": art["revision"], "title": art["title"],
                 "wikidata": art["wikidata"], "assessment": art["assessment"],
                 "origin": "enumerated", "licence_class": "open_share_alike",
                 "fetch": {"api": "action=parse", "params_sha256": "c" * 64},
                 "raw_sha256": hashing.sha256_file(d / "source.raw.html"),
                 "html_sha256": hashing.sha256_file(d / "body.html"),
                 "layer_sha256": hashing.sha256_file(d / "layer.txt"),
                 "renderer": "web-html/1"}
        (d / "source.json").write_text(json.dumps(entry), encoding="utf-8")
        entries.append(entry)
    for sid in unrendered:
        family, pageid, revision = sid.split("-")
        entries.append({"source_id": sid, "family": family, "pageid": int(pageid),
                        "revision": int(revision), "title": "Sprocket",
                        "wikidata": "Q99999003", "assessment": {"class": "Stub",
                                                                "importance": "low"},
                        "origin": "enumerated", "licence_class": "open_share_alike",
                        "fetch": None, "raw_sha256": None, "html_sha256": None,
                        "layer_sha256": None, "renderer": None})
    entries.sort(key=lambda e: e["source_id"])
    header = {"schema": "cf-sourceset/1", "id": SS, "version": version, "family": "enwiki",
              "previous_sha256": None, "created": "2026-10-17T12:00:00Z",
              "sources": len(entries)}
    path = paths.sourceset_manifest(SS, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(canonical(r) for r in [header, *entries]) + "\n",
                    encoding="utf-8")
    return path


DECLS = [
    # library, name, module, path, line, end_line, kind, proof_status, statement, signature,
    # docstring, tags
    ("alpha", "Widget.exists_spanning_gadget", "Alpha.Widget", "Alpha/Widget.lean", 612, 640,
     "theorem", "proved", False, "∀ (w : Widget), w.Finite → ∃ g, g.Spans w",
     "Every finite widget has a spanning gadget.", [("wikidata", "Q99999001")]),
    ("alpha", "Widget.Spanning", "Alpha.Widget", "Alpha/Widget.lean", 20, 30, "def",
     "definition", False, "Widget → Gadget → Prop", "A gadget spans a widget.", []),
    ("alpha", "Widget.spanning_conj", "Alpha.Widget", "Alpha/Widget.lean", 700, 720, "theorem",
     "statement_only", False, "∀ g h, g.Spans w → h.Spans w → Conj g h",
     "Spanning gadgets are conjugate.", []),
    ("alpha", "Widget.card_dvd", "Alpha.Widget", "Alpha/Widget.lean", 650, 660, "theorem",
     "proved", False, "∀ g, g.Spans w → g.card ∣ w.order", "", []),
    ("beta", "Beta.Widget.gadget_of_finite", "Beta.Widget", "Beta/Widget.lean", 40, 52,
     "theorem", "proved", False, "∀ (w : Widget), Finite w → Gadget w",
     "See https://en.wikipedia.org/wiki/Widget_lemma for the statement.",
     [("wikipedia", "https://en.wikipedia.org/wiki/Widget_lemma")]),
    ("beta", "Beta.GadgetLattice", "Beta.Gadget", "Beta/Gadget.lean", 10, 18, "def",
     "definition", True, "Widget → Prop", "The gadget lattice conjecture.", []),
    ("beta", "Beta.gadget_lattice_height", "Beta.Gadget", "Beta/Gadget.lean", 30, 44, "theorem",
     "proved", False, "∀ w, (gadgets w).height ≤ w.order", "Gadget theorem height bound.",
     [("wikidata", "Q99999002")]),
]

CORPUS_DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE libraries (id TEXT PRIMARY KEY, repo TEXT NOT NULL, commit_sha TEXT NOT NULL,
  toolchain TEXT, brain_lib TEXT, index_sha256 TEXT NOT NULL, declarations INTEGER NOT NULL);
CREATE TABLE decls (id INTEGER PRIMARY KEY, library TEXT NOT NULL, commit_sha TEXT NOT NULL,
  name TEXT NOT NULL, module TEXT, path TEXT, line INTEGER, end_line INTEGER, kind TEXT,
  proof_status TEXT NOT NULL, statement INTEGER NOT NULL DEFAULT 0,
  printable INTEGER NOT NULL DEFAULT 1, signature TEXT, docstring TEXT,
  UNIQUE (library, commit_sha, name));
CREATE VIRTUAL TABLE decls_fts USING fts5(name_words, signature, docstring,
  tokenize = 'unicode61');
CREATE TABLE tags (decl_id INTEGER NOT NULL, tag TEXT NOT NULL, value TEXT NOT NULL);
CREATE INDEX tags_by_value ON tags (tag, value);
"""

LIBRARIES = {
    "alpha": {"repo": "https://example.org/alpha", "commit": ALPHA, "brain_lib": "Alpha",
              "toolchain": "leanprover/lean4:v4.0.0", "roots": ["Alpha"],
              "licence": {"spdx": "Apache-2.0"}},
    "beta": {"repo": "https://example.org/beta", "commit": BETA, "brain_lib": "Beta",
             "toolchain": "leanprover/lean4:v4.0.0", "roots": ["Beta"],
             "licence": {"spdx": "MIT"}},
}


def write_universe(paths: WorkspacePaths, *, decls=DECLS) -> Path:
    """Universe ``u1`` (alpha, beta) with its universe.yaml and corpus.sqlite."""
    from locate.inputs import name_words, universe_sha256
    from cflib.yamlio import write_yaml

    libs = []
    for lid, lib in LIBRARIES.items():
        count = sum(1 for d in decls if d[0] == lid)
        libs.append({"id": lid, **lib, "index": {"path": f"index/{lid}/",
                                                 "sha256": hashing.sha256_text(lid),
                                                 "declarations": count}})
    data = {"id": U, "frozen": "2026-10-17", "libraries": libs,
            "excluded": [{"id": "gamma", "reason": "did not build"}],
            "universe_sha256": universe_sha256(libs)}
    paths.universe_dir(U).mkdir(parents=True, exist_ok=True)
    write_yaml(paths.universe_yaml(U), data)
    db = paths.corpus_db(U)
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.executescript(CORPUS_DDL)
    conn.executemany("INSERT INTO meta VALUES (?, ?)",
                     [("schema", "cf-corpus/1"), ("universe", U),
                      ("universe_sha256", data["universe_sha256"]), ("built", "2026-10-17")])
    for lib in libs:
        conn.execute("INSERT INTO libraries VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (lib["id"], lib["repo"], lib["commit"], lib["toolchain"],
                      lib["brain_lib"], lib["index"]["sha256"], lib["index"]["declarations"]))
    for i, (lid, name, module, path, line, end, kind, status, statement, sig, doc,
            tags) in enumerate(decls, 1):
        conn.execute("INSERT INTO decls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (i, lid, LIBRARIES[lid]["commit"], name, module, path, line, end, kind,
                      status, int(statement), 1, sig, doc))
        conn.execute("INSERT INTO decls_fts (rowid, name_words, signature, docstring) "
                     "VALUES (?, ?, ?, ?)", (i, name_words(name), sig, doc))
        for tag, value in tags:
            conn.execute("INSERT INTO tags VALUES (?, ?, ?)", (i, tag, value))
    conn.commit()
    conn.close()
    return db


def passage_rows(sid: str) -> list[dict]:
    """The inventory passages of one synthetic article: every paragraph a passage."""
    rendered = render_article(ARTICLES[sid])
    hm = locate_text.HeadMap(rendered["layer"], rendered["formulas"])
    starts = [p["span"][0] for p in rendered["paragraphs"]]
    ids = locate_text.assign_ids(hm.head, [hm.to_head(s) for s in starts])
    out = []
    for para, (_start, hexd, words) in zip(rendered["paragraphs"], ids):
        a, b = para["span"]
        exact = rendered["layer"][a:b][:80]
        out.append({"item": items.passage_item(hexd), "head_words": words, "span": [a, b],
                    "quote": {"exact": exact, "prefix": rendered["layer"][max(0, a - 10):a],
                              "suffix": ""},
                    "section": para["section"], "fragment": None,
                    "passage_kind": "theorem" if para["block"] == "div" else "definition",
                    "prominence": "central", "scope": "formal_math",
                    "statement": "informal restatement", "concept": None,
                    "finder": "model"})
    return out


def write_harvest(paths: WorkspacePaths, run_id: str, docs: dict[str, dict], *,
                  complete=True, gate_ok=True) -> Path:
    """``corpus/<run>/findings/<task>/<source>.json`` plus a RECEIPT.json."""
    root = paths.corpus_run_dir(run_id)
    for rel, doc in docs.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    (root / "RECEIPT.json").write_text(json.dumps(
        {"run_id": run_id, "complete": complete, "harvested_at": "2026-10-18T00:00:00Z",
         "gate": {"ok": gate_ok} if gate_ok is not None else None}), encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# a whole synthetic campaign, stage by stage
# --------------------------------------------------------------------------

def header(mode: str, run_id: str, task_id: str, sid: str, entry: dict, *, universe=None) -> dict:
    doc = {"schema": "cf-locate/1", "mode": mode, "run_id": run_id, "task_id": task_id,
           "source_id": sid, "source_sha": entry["html_sha256"],
           "layer_sha256": entry["layer_sha256"], "renderer": "web-html/1"}
    if universe is not None:
        doc.update(universe=universe["id"], universe_sha256=universe["sha256"])
    return doc


#: prominence/scope/kind per paragraph index of each article
INVENTORY_LABELS = {
    W1: [("definition", "subject", "formal_math"), ("theorem", "central", "formal_math"),
         ("definition", "supporting", "formal_math"), ("other", "supporting", "expository")],
    W2: [("theorem", "subject", "formal_math"), ("other", "supporting", "formal_math")],
}


def inventory_documents(entries: dict, run_id: str = "inv-1") -> dict[str, dict]:
    docs = {}
    for sid, labels in INVENTORY_LABELS.items():
        rows = passage_rows(sid)
        for row, (kind, prominence, scope) in zip(rows, labels):
            row.update(passage_kind=kind, prominence=prominence, scope=scope)
        tid = f"inv-{sid}"
        doc = header("locate-inventory", run_id, tid, sid, entries[sid])
        doc.update(passages=rows, skips=[], notes="")
        docs[f"findings/{tid}/{sid}.json"] = doc
    return docs


def manifest_entries(paths: WorkspacePaths) -> dict:
    out = {}
    path = paths.sourceset_manifest(SS, 1)
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        row = json.loads(line)
        out[row["source_id"]] = row
    return out


def universe_pin(paths: WorkspacePaths) -> dict:
    from locate import inputs
    u = inputs.load_universe(paths, U)
    return {"id": u.id, "sha256": u.sha256}


def locate_documents(paths: WorkspacePaths, run_id: str = "loc-1") -> dict[str, dict]:
    """Documents for every task of a planned locate run, from its task manifests."""
    entries = manifest_entries(paths)
    uni = universe_pin(paths)
    docs = {}
    for manifest_path in sorted((paths.tend_root(run_id) / "gate" / "tasks").glob("*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tid = manifest["task_id"]
        for d in manifest["documents"]:
            sid = d["source_id"]
            doc = header("locate", run_id, tid, sid, entries[sid], universe=uni)
            doc["passages"] = [LOCATE_ANSWERS[sid][i](p["item"])
                               for i, p in enumerate(_ordered(sid, d["passages"]))]
            docs[f"findings/{tid}/{sid}.json"] = doc
    return docs


def _ordered(sid, passages):
    return passages


def _receipt(*queries, candidates=()):
    return {"queries": list(queries), "candidates": list(candidates)}


def _loc(lib, decl, relation="exactMatch", rank=1, note=""):
    return {"library": lib, "declaration": decl, "relation": relation, "rank": rank,
            "confidence": "high", "note": note, "witness": "states it"}


LOCATE_ANSWERS = {
    W1: [
        lambda item: {"item": item, "outcome": "located",
                      "locations": [_loc("alpha", "Widget.exists_spanning_gadget"),
                                    _loc("beta", "Beta.Widget.gadget_of_finite", "broadMatch", 2)],
                      "receipt": _receipt("widget lemma", "spanning gadget exists")},
        lambda item: {"item": item, "outcome": "located",
                      "locations": [_loc("alpha", "Widget.card_dvd", "hasPart")],
                      "receipt": _receipt("gadget size divides order")},
        lambda item: {"item": item, "outcome": "located",
                      "locations": [_loc("alpha", "Widget.Spanning", "exactMatch"),
                                    _loc("alpha", "Widget.spanning_conj", "relatedMatch", 2)],
                      "receipt": _receipt("spanning gadget")},
    ],
    W2: [
        lambda item: {"item": item, "outcome": "absent", "locations": [],
                      "receipt": _receipt("gadget lattice", "gadget theorem", "lattice height")},
        lambda item: {"item": item, "outcome": "unresolved", "locations": [],
                      "reason": "the induction is not stated", "receipt": _receipt()},
    ],
}


def campaign(case, root: Path) -> WorkspacePaths:
    """Workspace + sourceset + universe + candidates + a harvested inventory run ``inv-1``."""
    paths = make_workspace(root)
    write_sourceset(paths)
    write_universe(paths)
    code, out, err = cf("--workspace", str(root), "locate", "candidates", "--sourceset", SS,
                        "--universe", U)
    case.assertEqual(code, 0, out + err)
    code, out, err = cf("--workspace", str(root), "plan-locate", "--preset", "locate-inventory",
                        "--sourceset", SS, "--universe", U, "--run-id", "inv-1")
    case.assertEqual(code, 0, out + err)
    write_harvest(paths, "inv-1", inventory_documents(manifest_entries(paths)))
    return paths


def plan_locate(case, root: Path, *extra: str, run_id: str = "loc-1") -> None:
    code, out, err = cf("--workspace", str(root), "plan-locate", "--preset", "locate",
                        "--sourceset", SS, "--universe", U, "--inventory-run", "inv-1",
                        "--run-id", run_id, *extra)
    case.assertEqual(code, 0, out + err)


def challenge_documents(paths: WorkspacePaths, run_id: str = "chl-1", *,
                        verdict=None) -> dict[str, dict]:
    """A verdict for every record of a planned challenge run (``verdict(record)`` decides)."""
    entries = manifest_entries(paths)
    uni = universe_pin(paths)
    docs = {}
    for manifest_path in sorted((paths.tend_root(run_id) / "gate" / "tasks").glob("*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tid = manifest["task_id"]
        for d in manifest["documents"]:
            sid = d["source_id"]
            doc = header("locate-challenge", run_id, tid, sid, entries[sid], universe=uni)
            doc["verdicts"] = [(verdict or default_verdict)(rec) for rec in d["records"]]
            docs[f"findings/{tid}/{sid}.json"] = doc
    return docs


def default_verdict(rec: dict) -> dict:
    """Confirm locations, uphold everything else; overturn the absence of Gadget theorem."""
    v = {"item": rec["item"], "target": rec["target"], "witness": "checked"}
    if rec["target"] == "location":
        v.update(location_key=rec["location_key"], action="confirm")
    elif rec["target"] == "absence":
        v.update(action="overturn",
                 locations=[_loc("beta", "Beta.gadget_lattice_height", "exactMatch")])
    else:
        v["action"] = "uphold"
    return v
