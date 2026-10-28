"""Synthetic locate inputs, built by the owners' own producers.

New code, for the planner's tests (tests/test_locate_planner.py).  The
sourceset, the rendered web sources, the universe and ``corpus.sqlite`` are
other packages' artifacts, so these fixtures make them the way those packages
do (references/locate-documents.md sections 7 and 8): a sourceset through
``sources.sourceset.Sourceset`` with entries from ``sources.websource.entry``,
each source's views from ``sources.render_html.render_directory`` over a real
HTML body, and a universe through ``corpus.universe.Universe`` whose
``decls.jsonl`` indexes are pinned with ``set_index`` and built into
``corpus.sqlite`` (and the G1-blind ``corpus.blind.sqlite``) by
``corpus.build_db.build``.  The inventory passages are what a worker following
the prompt writes -- a quote that starts each passage, one passage per finder
start -- placed by ``scripts/locate/passage_ids.py``.  Every page, library and
declaration is made up: the article "Widget lemma" (page 1001, revision 2002,
Q99999001), "Gadget theorem" (page 1003, revision 2004, Q99999002) and the
libraries ``alpha`` and ``beta``.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import hashlib
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main  # noqa: E402
from cflib import hashing, vocab  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from corpus import build_db  # noqa: E402
from corpus.universe import Universe  # noqa: E402
from locate import inputs, passage_ids  # noqa: E402
from sources import render_html, websource  # noqa: E402
from sources.sourceset import Sourceset  # noqa: E402

ALPHA = "a" * 40
BETA = "b" * 40
SS = "wpm"
U = "u1"
W1 = "enwiki-1001-2002"
W2 = "enwiki-1003-2004"
W3 = "enwiki-1005-2006"          # enumerated, never fetched or rendered
TOOLCHAIN = "leanprover/lean4:v4.34.0"


def math(tex: str) -> str:
    """An inline formula the way MediaWiki's MathML output wraps one."""
    alt = "{\\displaystyle " + tex + "}"
    return (f'<span class="mwe-math-element"><span class="mwe-math-mathml-inline">'
            f'<math alttext="{alt}"><semantics><mrow><mi>{tex}</mi></mrow>'
            f'<annotation encoding="application/x-tex">{alt}</annotation></semantics></math>'
            f'</span></span>')


#: each article: its metadata and a real body.  Widget lemma's lead paragraph is
#: the lead AND a bold definition (two finders at one start), its theorem box's
#: first paragraph opens on a bold that only labels it ("Theorem.", which proposes
#: no bold definition), and a bold term outside the lead ("spanning") is not one
#: of its terms.
ARTICLES = {
    W1: {
        "title": "Widget lemma", "pageid": 1001, "revision": 2002, "wikidata": "Q99999001",
        "assessment": {"class": "B", "importance": "high"},
        "html": (
            '<div class="mw-parser-output">'
            "<p>In widget theory, the <b>widget lemma</b> states that every finite widget has "
            "a spanning gadget, and it is a central result of the subject that every course on "
            "widgets covers.</p>\n"
            '<h2 id="Statement">Statement</h2>\n'
            '<div class="math_theorem"><p><b>Theorem.</b> Let ' + math("G") + " be a finite "
            "widget of order " + math("n") + ". Then " + math("G") + " has a spanning gadget "
            "whose size divides " + math("n") + " and no smaller gadget spans it.</p></div>\n"
            "<p>A gadget is called <b>spanning</b> when it meets every sprocket of the widget, "
            "and any two spanning gadgets are conjugate under the widget group action on "
            "sprockets.</p>\n"
            '<h2 id="History">History</h2>\n'
            "<p>The lemma was first stated by a committee of widget theorists in a widely "
            "circulated letter that nobody has since found.</p>"
            "</div>"),
    },
    W2: {
        "title": "Gadget theorem", "pageid": 1003, "revision": 2004, "wikidata": "Q99999002",
        "assessment": {"class": "C", "importance": "mid"},
        "html": (
            '<div class="mw-parser-output">'
            "<p>The <b>gadget lattice theorem</b> says that the gadgets of a finite widget form "
            "a lattice under inclusion whose height is bounded by the order of the widget "
            "itself.</p>\n"
            '<h2 id="Proof">Proof</h2>\n'
            "<p>The proof inducts on the order " + math("n") + " and uses the widget lemma at "
            "every step of the induction on the lattice.</p>"
            "</div>"),
    },
}


#: what the planner wrote before the statement cut's flip (docs/proposals/statement-cut.md
#: section 7): judgment rules 2, span-end rule 1, no statement_cut_rule and no status_rule,
#: and the supporting-absence sample at 0.25 -- what every run of an operator's workspace
#: planned so far names, and so what these fixtures' documents, which write their own claim
#: lists, are written for
BEFORE_THE_FLIP = {"JUDGMENT_RULES": vocab.RULES_PD5_PD9, "SPAN_END_RULE": 1,
                   "CHALLENGE_ABSENCE_SAMPLE_RATE": 0.25}


def before_the_flip():
    """Plan as the planner did before the statement cut's flip: a run of the earlier flow.

    A context manager over ``cflib.vocab`` (:data:`BEFORE_THE_FLIP`): a test of how a run
    planned before the flip is gated, tallied, re-planned or exported plans its runs in it,
    since a run planned now names the statement cut's versions and refuses that flow.  What
    it reproduces is the run's gate.json and run.yaml; its prompts are the statement cut's,
    since the templates keep no pre-flip text (a run planned before the flip keeps the
    prompts it rendered then), and no gate, tally or export reads a prompt.
    """
    from unittest import mock

    return mock.patch.multiple(vocab, **BEFORE_THE_FLIP)


#: what the planner wrote from the statement cut's flip to the flip of inv-pilot-1's fixes
#: (docs/proposals/statement-cut.md section 7): span-end rule 2 and statement-cut rule 1,
#: the pair inv-pilot-1 names, beside judgment rules 3
BEFORE_THE_CONTINUATION = {"SPAN_END_RULE": vocab.SPAN_END_STATEMENT_CUT,
                           "STATEMENT_CUT_RULE": vocab.STATEMENT_CUT_FIRST}


def before_the_continuation():
    """Plan as the planner did before the flip of inv-pilot-1's fixes: (2, 1), not (3, 2).

    A context manager over ``cflib.vocab`` (:data:`BEFORE_THE_CONTINUATION`), for a test
    of how a run planned under statement-cut rule 1 is gated or re-planned: its gate.json
    names the pair a run of the statement cut's first version names.  Its prompts are the
    continued cut's, as :func:`before_the_flip`'s are the statement cut's.
    """
    from unittest import mock

    return mock.patch.multiple(vocab, **BEFORE_THE_CONTINUATION)


#: what the planner wrote from the flip of inv-pilot-1's fixes to the flip of the wide bound
#: (docs/proposals/statement-cut.md sections 3.3 and 7): statement-cut rule 2 beside
#: span-end rule 3, the pair inv-pilot-2 names, under the gate that knows statement-cut
#: rule 3 too
BEFORE_THE_WIDE = {"STATEMENT_CUT_RULE": vocab.STATEMENT_CUT_CONTINUED}


def before_the_wide():
    """Plan as the planner did before the flip of the wide bound: (3, 3, 2), not (3, 3, 3).

    A context manager over ``cflib.vocab`` (:data:`BEFORE_THE_WIDE`), for a test of how a
    run planned under statement-cut rule 2 is gated or re-planned now that the planner
    writes 3.  Its prompts are the ones such a plan rendered: the worker's are the same
    under either rule, and an audited plan's rule card reads the patched rule
    (``locate.prompts.gate_bound``).
    """
    from unittest import mock

    return mock.patch.multiple(vocab, **BEFORE_THE_WIDE)


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


# --------------------------------------------------------------------------
# the sourceset (section 7), through sources.sourceset and sources.render_html
# --------------------------------------------------------------------------

def write_sourceset(paths: WorkspacePaths, *, sources=(W1, W2), unrendered=(W3,)) -> Path:
    """Sourceset ``wpm``: version 1 empty (``Sourceset.create``), version 2 its sources.

    Each rendered source is what ``cf sourceset fetch`` and ``render`` leave: the
    raw response bytes, the canonical body, every view ``render_directory``
    writes and a ``source.json``; the unrendered one is an enumerated entry with
    no bytes yet.  Returns the newest manifest's path.
    """
    ss = Sourceset.create(paths, SS, "enwiki")
    fam = websource.family("enwiki")
    entries = []
    for sid in sources:
        art = ARTICLES[sid]
        entry = websource.entry(fam, art["pageid"], art["revision"], title=art["title"],
                                wikidata=art["wikidata"], assessment=art["assessment"])
        directory = ss.source_dir(sid)
        raw = art["html"].encode("utf-8")
        render_html.write_bytes(directory / "source.raw.html", raw)
        body = render_html.canonical_body(raw)
        render_html.write_bytes(directory / "body.html", body.encode("utf-8"))
        rendering = render_html.render_directory(directory)
        entry.update(fetch=websource.fetch_block(), raw_sha256=hashlib.sha256(raw).hexdigest(),
                     html_sha256=render_html.sha256_text(body),
                     layer_sha256=rendering.layer_sha256, renderer=render_html.RENDERER)
        (directory / "source.json").write_text(json.dumps(entry, sort_keys=True),
                                               encoding="utf-8")
        entries.append(entry)
    for sid in unrendered:
        _family, pageid, revision = sid.split("-")
        entries.append(websource.entry(fam, int(pageid), int(revision), title="Sprocket",
                                       wikidata="Q99999003",
                                       assessment={"class": "Stub", "importance": "low"}))
    return ss.write_version(ss.latest(), entries).path


def manifest_entries(paths: WorkspacePaths) -> dict:
    return dict(inputs.manifest(paths, SS).entries)


# --------------------------------------------------------------------------
# the universe and its databases (section 8), through corpus.universe / build_db
# --------------------------------------------------------------------------

def _decl(name, module, path, line, end_line, kind, status, signature, docstring="",
          statement=False, wikidata=None) -> dict:
    """One ``decls.jsonl`` record in the shape ``corpus.ingest`` writes."""
    from corpus.ingest import wikipedia_urls

    tags = [{"tag": "wikidata", "value": wikidata}] if wikidata else []
    tags += [{"tag": "wikipedia", "value": url} for url in wikipedia_urls(docstring or "")]
    return {"name": name, "module": module, "path": path, "line": line,
            "start_line": line, "end_line": end_line, "kind": kind, "proof_status": status,
            "statement": statement, "printable": True, "signature": signature,
            "docstring": docstring or None, "axioms": [], "deprecated": None,
            "private": False, "probe_proof_status": status, "flags": [],
            "tags": sorted(tags, key=lambda t: (t["tag"], t["value"]))}


DECLS = {
    "alpha": [
        _decl("Widget.exists_spanning_gadget", "Alpha.Widget", "Alpha/Widget.lean", 612, 640,
              "theorem", "proved", "∀ (w : Widget), w.Finite → ∃ g, g.Spans w",
              "Every finite widget has a spanning gadget.", wikidata="Q99999001"),
        _decl("Widget.Spanning", "Alpha.Widget", "Alpha/Widget.lean", 20, 30, "def",
              "definition", "Widget → Gadget → Prop", "A gadget spans a widget."),
        _decl("Widget.spanning_conj", "Alpha.Widget", "Alpha/Widget.lean", 700, 720, "theorem",
              "statement_only", "∀ g h, g.Spans w → h.Spans w → Conj g h",
              "Spanning gadgets are conjugate."),
        _decl("Widget.card_dvd", "Alpha.Widget", "Alpha/Widget.lean", 650, 660, "theorem",
              "proved", "∀ g, g.Spans w → g.card ∣ w.order"),
    ],
    "beta": [
        _decl("Beta.Widget.gadget_of_finite", "Beta.Widget", "Beta/Widget.lean", 40, 52,
              "theorem", "proved", "∀ (w : Widget), Finite w → Gadget w",
              "See https://en.wikipedia.org/wiki/Widget_lemma for the statement."),
        _decl("Beta.GadgetLattice", "Beta.Gadget", "Beta/Gadget.lean", 10, 18, "def",
              "definition", "Widget → Prop", "The gadget lattice conjecture.", statement=True),
        _decl("Beta.gadget_lattice_height", "Beta.Gadget", "Beta/Gadget.lean", 30, 44,
              "theorem", "proved", "∀ w, (gadgets w).height ≤ w.order",
              "Gadget theorem height bound.", wikidata="Q99999002"),
    ],
}

LIBRARIES = {
    "alpha": {"repo": "https://example.org/alpha", "commit": ALPHA, "brain_lib": "Alpha",
              "roots": ["Alpha"], "licence": "Apache-2.0"},
    "beta": {"repo": "https://example.org/beta", "commit": BETA, "brain_lib": "Beta",
             "roots": ["Beta"], "licence": "MIT"},
}


def write_universe(paths: WorkspacePaths, *, decls=None, blind: bool = True) -> Path:
    """Universe ``u1`` (alpha, beta; gamma excluded), its indexes and its databases.

    The indexes are synthetic ``decls.jsonl`` files pinned with ``set_index``;
    ``corpus.build_db.build`` writes ``corpus.sqlite`` (and, with ``blind``,
    ``corpus.blind.sqlite``) and records them in ``universe.yaml``.
    """
    decls = DECLS if decls is None else decls
    path = paths.universe_yaml(U)
    path.parent.mkdir(parents=True, exist_ok=True)
    universe = Universe.create(path, U, "2026-10-17")
    for lid, lib in LIBRARIES.items():
        universe.add_library(library_id=lid, repo=lib["repo"], commit=lib["commit"],
                             toolchain=TOOLCHAIN, roots=lib["roots"], brain_lib=lib["brain_lib"],
                             licence=lib["licence"])
    universe.exclude("gamma", "did not build")
    universe.record_canary(toolchain=TOOLCHAIN, passed=True, cases={}, index_sha256="0" * 64,
                           checked="2026-10-17T00:00:00Z")
    udir = paths.universe_dir(U)
    for lid in LIBRARIES:
        rows = sorted(decls.get(lid, []), key=lambda r: r["name"])
        index = udir / "index" / lid / "decls.jsonl"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False) + "\n" for r in rows),
                         encoding="utf-8")
        universe.set_index(lid, path=f"index/{lid}/", sha256=hashing.sha256_file(index),
                           declarations=len(rows))
    build_db.build(universe, udir, paths.corpus_db(U), force=True)
    if blind:
        build_db.build(universe, udir, paths.corpus_db_blind(U), force=True, blind=True)
    universe.save()
    return paths.corpus_db(U)


def universe_pin(paths: WorkspacePaths) -> dict:
    u = inputs.load_universe(paths, U)
    return {"id": u.id, "sha256": u.sha256}


# --------------------------------------------------------------------------
# documents, the way the prompts say to write them
# --------------------------------------------------------------------------

def header(mode: str, run_id: str, task_id: str, sid: str, entry: dict, *, universe=None) -> dict:
    doc = {"schema": "cf-locate/1", "mode": mode, "run_id": run_id, "task_id": task_id,
           "source_id": sid, "source_sha": entry["html_sha256"],
           "layer_sha256": entry["layer_sha256"], "renderer": entry["renderer"]}
    if universe is not None:
        doc.update(universe=universe["id"], universe_sha256=universe["sha256"])
    return doc


#: (passage_kind, prominence, scope) per finder start / paragraph start, in layer order
INVENTORY_LABELS = {
    W1: [("definition", "subject", "formal_math"), ("theorem", "central", "formal_math"),
         ("definition", "supporting", "formal_math"), ("other", "supporting", "expository")],
    W2: [("theorem", "subject", "formal_math"), ("other", "supporting", "formal_math")],
}


def passage_rows(paths: WorkspacePaths, sid: str) -> list[dict]:
    """One passage per paragraph START of the rendered source, placed by passage_ids.

    Two blocks (a theorem box and its first paragraph) or two finders (the lead
    and a bold definition) at one start are one passage, its ``finder`` one of
    that start's finders (``model`` where none starts); each passage's quote is
    the first words of the layer at its start.
    """
    view = inputs.source_view(paths, SS, sid)
    layer, formulas = view.layer_and_formulas()
    paragraphs = view.paragraphs()
    sections = {tuple(s["path"]): s.get("fragment") for s in view.sections()}
    finders: dict[int, list[str]] = {}
    for f in view.finders():
        finders.setdefault(int(f["start"]), []).append(f["finder"])
    rows, seen = [], set()
    for para in paragraphs:
        start = int(para["span"][0])
        if start in seen:
            continue
        seen.add(start)
        exact = " ".join(layer[start:].split(" ")[:5])
        rows.append({"item": "", "head_words": 0, "span": None,
                     "quote": {"exact": exact, "prefix": "", "suffix": ""},
                     "section": list(para["section"]),
                     "fragment": sections.get(tuple(para["section"])),
                     "passage_kind": "other", "prominence": "supporting",
                     "scope": "formal_math", "statement": "informal restatement",
                     "concept": None, "finder": sorted(finders.get(start, ["model"]))[0]})
    doc = {"passages": rows}
    problems = passage_ids.place(doc, layer, formulas, paragraphs)
    assert not problems, problems
    return doc["passages"]


def write_harvest(paths: WorkspacePaths, run_id: str, docs: dict[str, dict], *,
                  complete=True, gate_ok=True) -> Path:
    """``corpus/<run>/<task-id>/<source-id>.json`` (the harvested layout) and a RECEIPT.json."""
    root = paths.corpus_run_dir(run_id)
    for rel, doc in docs.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    (root / "RECEIPT.json").write_text(json.dumps(
        {"run_id": run_id, "complete": complete, "harvested_at": "2026-10-18T00:00:00Z",
         "gate": {"ok": gate_ok} if gate_ok is not None else None}), encoding="utf-8")
    return root


def inventory_documents(paths: WorkspacePaths, run_id: str = "inv-1") -> dict[str, dict]:
    entries = manifest_entries(paths)
    docs = {}
    for sid, labels in INVENTORY_LABELS.items():
        rows = passage_rows(paths, sid)
        assert len(rows) == len(labels), (sid, len(rows))
        for row, (kind, prominence, scope) in zip(rows, labels):
            row.update(passage_kind=kind, prominence=prominence, scope=scope)
        tid = f"inv-{sid}"
        doc = header("locate-inventory", run_id, tid, sid, entries[sid])
        doc.update(passages=rows, skips=[], notes="")
        docs[f"{tid}/{sid}.json"] = doc
    return docs


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
            order = [p["item"] for p in frozen_formal_math(paths, sid)]
            doc["passages"] = [LOCATE_ANSWERS[sid][order.index(p["item"])](p["item"])
                               for p in d["passages"]]
            docs[f"{tid}/{sid}.json"] = doc
    return docs


def frozen_formal_math(paths: WorkspacePaths, sid: str) -> list[dict]:
    """The inventory's formal_math passages of one source, in layer order."""
    rows = passage_rows(paths, sid)
    return [r for r, (_k, _p, scope) in zip(rows, INVENTORY_LABELS[sid])
            if scope == "formal_math"]


def _receipt(*queries, candidates=()):
    return {"queries": list(queries), "candidates": list(candidates)}


def _cand(lib, decl, verdict="accepted", reason=""):
    return {"library": lib, "declaration": decl, "verdict": verdict, "reason": reason}


def _loc(lib, decl, relation="exactMatch", rank=1, note=""):
    return {"library": lib, "declaration": decl, "relation": relation, "rank": rank,
            "confidence": "high", "note": note, "witness": "states it"}


#: per source, the answer for each formal_math passage in layer order
LOCATE_ANSWERS = {
    W1: [
        lambda item: {"item": item, "outcome": "located",
                      "locations": [_loc("alpha", "Widget.exists_spanning_gadget"),
                                    _loc("beta", "Beta.Widget.gadget_of_finite", "broadMatch", 2)],
                      "receipt": _receipt("widget lemma", "spanning gadget exists",
                                          candidates=[
                                              _cand("alpha", "Widget.exists_spanning_gadget"),
                                              _cand("beta", "Beta.Widget.gadget_of_finite")])},
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
    write_harvest(paths, "inv-1", inventory_documents(paths))
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
            docs[f"{tid}/{sid}.json"] = doc
    return docs


def default_verdict(rec: dict) -> dict:
    """Confirm locations and coverage claims, uphold everything else; overturn the
    absence of Gadget theorem."""
    v = {"item": rec["item"], "target": rec["target"], "witness": "checked"}
    if rec["target"] == "location":
        v.update(location_key=rec["location_key"], action="confirm")
    elif rec["target"] == "coverage":
        v["action"] = "confirm"
    elif rec["target"] == "absence":
        v.update(action="overturn",
                 locations=[_loc("beta", "Beta.gadget_lattice_height", "exactMatch")])
    else:
        v["action"] = "uphold"
    return v


# --------------------------------------------------------------------------
# the skip audit's layer (references/locate-documents.md section 13.11): a made-up
# "Widget bounds" page whose sections the skip rule reads -- a lead with an infobox
# and a sidebar of formulas, a statement, a proof, a gadget bound with a display, a
# definition and its remark, and References -- and the documents F1-F3b of the
# skip audit's tests (F4, a hand edit, is a revision of F1's)
# --------------------------------------------------------------------------

def skip_formula(tex: str) -> str:
    """A formula as the layer carries it: its fallback text, then its TeX annotation."""
    return f"{tex}{{\\displaystyle {tex}}}"


SKIP_TITLE = "Widget bounds"
#: (name, block, section, text): the page's blocks in reading order; a ``None`` block is a
#: heading, which no paragraphs.json entry holds
SKIP_BLOCKS = [
    ("TITLE", None, [], SKIP_TITLE),
    ("LEAD", "p", [], "A widget is a finite graph of sprockets joined by gadgets."),
    ("INFOBOX", "p", [], "Field Widget theory Introduced by a synthetic committee"),
    ("SIDE1", "li", [], skip_formula("e(W)\\geq 0")),
    ("SIDE2", "li", [], skip_formula("c(W)=2n")),
    ("SIDE3", "li", [], skip_formula("g(W)\\leq n")),
    ("H_STATEMENT", None, [], "Statement"),
    ("STMT", "p", ["Statement"], f"Every finite widget {skip_formula('W')} of order "
                                 f"{skip_formula('n')} has at most {skip_formula('n')} edges."),
    ("H_PROOF", None, [], "Proof"),
    ("PF1", "p", ["Proof"], f"Suppose {skip_formula('W')} is a finite widget with "
                            f"{skip_formula('n')} nodes."),
    ("PF2", "p", ["Proof"], f"Then every gadget of {skip_formula('W')} has at most "
                            f"{skip_formula('n')} edges, so"),
    ("PF3", "dd", ["Proof"], skip_formula("e(W)\\leq n")),
    ("PF4", "p", ["Proof"], f"This completes the proof, since {skip_formula('n')} bounds every "
                            f"gadget."),
    ("H_BOUND", None, [], "Gadget bound"),
    ("GB1", "p", ["Gadget bound"], f"The gadget bound of a connected widget "
                                   f"{skip_formula('W')} is"),
    ("GB2", "dd", ["Gadget bound"], skip_formula("g(W)=n-1")),
    ("GB3", "p", ["Gadget bound"], "A widget whose gadgets are all bounded is called tame, and "
                                   "tame widgets have no cycles."),
    ("H_REFS", None, [], "References"),
    ("REF1", "li", ["References"], "A. Author, Widgets, a synthetic book."),
    ("REF2", "li", ["References"], "B. Author, Gadgets, a synthetic paper."),
    ("REF3", "li", ["References"], "C. Author, Sprockets, a synthetic note."),
]
SKIP_LAYER = "\n".join(text for _n, _b, _s, text in SKIP_BLOCKS)


def _skip_spans() -> dict[str, list[int]]:
    out, cursor = {}, 0
    for name, _block, _section, text in SKIP_BLOCKS:
        a = SKIP_LAYER.index(text, cursor)
        out[name] = [a, a + len(text)]
        cursor = a + len(text)
    return out


SKIP_SPANS = _skip_spans()


def _skip_formulas() -> list[dict]:
    out = []
    for name, _block, _section, text in SKIP_BLOCKS:
        base = SKIP_SPANS[name][0]
        at = 0
        while True:
            j = text.find("{\\displaystyle ", at)
            if j < 0:
                break
            close, depth = j, 0
            for close in range(j, len(text)):
                depth += {"{": 1, "}": -1}.get(text[close], 0)
                if depth == 0:
                    break
            tex = text[j + len("{\\displaystyle "):close]
            start = j - len(tex)
            assert text[start:j] == tex, (name, tex)
            out.append({"span": [base + start, base + close + 1],
                        "alttext": "{\\displaystyle " + tex + "}"})
            at = close + 1
    return out


SKIP_FORMULAS = _skip_formulas()


def _skip_paragraphs() -> list[dict]:
    out = []
    for name, block, section, text in SKIP_BLOCKS:
        if block is None:
            continue
        n = text.count("{\\displaystyle ")
        out.append({"span": SKIP_SPANS[name], "section": list(section), "block": block,
                    "formulas": n, "math": n > 0})
    return out


SKIP_PARAGRAPHS = _skip_paragraphs()


def skip_passage(name: str, kind: str, scope: str, *, prominence: str = "supporting",
                 until: str | None = None, words: int = 4, at: str | None = None) -> dict:
    """A passage the way a worker writes one: a quote of the first words at the block
    ``name`` starts (or at the words ``at`` inside it), placed by ``passage_ids``."""
    a = SKIP_SPANS[name][0] if at is None else SKIP_LAYER.index(at, SKIP_SPANS[name][0])
    exact = " ".join(SKIP_LAYER[a:].split(" ")[:words])
    section = next(s for n, _b, s, _t in SKIP_BLOCKS if n == name)
    row = {"item": "", "head_words": 0, "span": None,
           "quote": {"exact": exact, "prefix": "", "suffix": ""},
           "section": list(section), "fragment": section[0] if section else None,
           "passage_kind": kind, "prominence": prominence if scope == "formal_math"
           else "supporting", "scope": scope,
           "statement": f"a synthetic restatement of the {name.lower()} passage",
           "concept": None, "finder": "model"}
    if until is not None:
        row["until"] = until
    return row


def skip_span(*names: str, start: str | None = None, end: str | None = None) -> list[int]:
    """The span of the blocks ``names`` (first to last), or from the words ``start`` /
    up to the end of the words ``end`` inside them."""
    a, b = SKIP_SPANS[names[0]][0], SKIP_SPANS[names[-1]][1]
    if start is not None:
        a = SKIP_LAYER.index(start, a)
    if end is not None:
        b = SKIP_LAYER.index(end, a) + len(end)
    return [a, b]


#: the page furniture every document skips: the infobox (no math), the lead's sidebar (math,
#: in the lead) and References (list items, no math)
FURNITURE_SKIPS = [
    {"span": skip_span("INFOBOX"), "reason": "infobox"},
    {"span": skip_span("SIDE1", "SIDE3"), "reason": "sidebar of related formulas"},
    {"span": skip_span("REF1", "REF3"), "reason": "references"},
]
PROOF_SKIP = {"span": skip_span("PF1", "PF4"),
              "reason": "proof narration; it argues the theorem above"}
#: F3's skip: the display the gadget bound's sentence introduces; F3b's: the remark that
#: starts inside the definition's paragraph
DISPLAY_SKIP = {"span": skip_span("GB2"), "reason": "a display"}
REMARK_SKIP = {"span": skip_span("GB3", start="tame widgets have no cycles."),
               "reason": "a remark"}


def skip_rows(*, proof: bool = True, display: bool = False, remark: bool = False) -> list[dict]:
    """The passages of a skip document: the lead and the statement, the proof's three
    statements and narration unless ``proof`` skips them, the gadget bound (its display
    held unless ``display`` skips it), and the definition (its remark kept unless
    ``remark`` skips it)."""
    rows = [skip_passage("LEAD", "definition", "formal_math", prominence="subject"),
            skip_passage("STMT", "theorem", "formal_math", prominence="central")]
    if not proof:
        rows += [skip_passage("PF1", "other", "expository"),
                 skip_passage("PF2", "theorem", "formal_math"),
                 skip_passage("PF4", "other", "expository")]
    rows.append(skip_passage("GB1", "definition", "formal_math",
                             until="connected widget W{\\displaystyle W} is" if display
                             else None))
    if remark:
        rows.append(skip_passage("GB3", "definition", "formal_math", until="called tame,",
                                 words=5))
    else:
        rows += [skip_passage("GB3", "definition", "formal_math", until="called tame,",
                              words=5),
                 skip_passage("GB3", "theorem", "formal_math", at="tame widgets have"
                              " no cycles.", words=4)]
    return rows


def skip_body(*, proof: bool = True, display: bool = False, remark: bool = False,
              furniture: bool = True) -> dict:
    """A skip document's body (``{passages, skips, notes}``) placed by ``passage_ids`` under
    span-end rule 3: F1 (``proof``: the proof skipped whole), F2 (``proof=False``: the
    furniture alone skipped), F3 (``display``) and F3b (``remark``)."""
    skips = [dict(s) for s in FURNITURE_SKIPS] if furniture else []
    if proof:
        skips.append(dict(PROOF_SKIP))
    if display:
        skips.append(dict(DISPLAY_SKIP))
    if remark:
        skips.append(dict(REMARK_SKIP))
    doc = {"passages": skip_rows(proof=proof, display=display, remark=remark),
           "skips": sorted(skips, key=lambda s: s["span"]), "notes": ""}
    problems = passage_ids.place(doc, SKIP_LAYER, SKIP_FORMULAS, SKIP_PARAGRAPHS,
                                 rule=vocab.SPAN_END_CONTINUED)
    assert not problems, problems
    return doc


# --------------------------------------------------------------------------
# the gap audit's page (audit rule 3, references/locate-documents.md section 13.12) and
# the prose floor's (gate rule 8): a made-up "Widget gaps", whose body holds formula-free
# paragraphs, one of them a statement in words, and a notation sentence with plain-text
# symbols, the two shapes the smoke-4 audit found no passage and no skip held
# --------------------------------------------------------------------------

GAP_TITLE = "Widget gaps"
#: BB2's statement in words, in the middle of the three formula-free paragraphs of
#: "Building blocks": past the first and last 200 characters of their gap together, where a
#: GAP line of the first renderer clipped it away
GAP_STATEMENT = "Every widget has a gadget, and the gadgets of a widget do not determine it."
#: MOD1's notation sentence: it names a symbol the next paragraph's statement uses
GAP_NOTATION = "We write N for the product of the counts c1, ..., ck of this section."
GAP_BLOCKS = [
    ("TITLE", None, [], GAP_TITLE),
    ("LEAD", "p", [], "A widget is a finite graph of sprockets joined by gadgets."),
    ("H_STATEMENT", None, [], "Statement"),
    ("STMT", "p", ["Statement"], f"Every finite widget {skip_formula('W')} of order "
                                 f"{skip_formula('n')} has at most {skip_formula('n')} edges."),
    ("H_BLOCKS", None, [], "Building blocks"),
    ("BB1", "p", ["Building blocks"],
     "Widgets have long been assembled from smaller widgets, and the assembly is told in a "
     "synthetic tradition of handbooks, lecture notes and survey articles that each describe "
     "the same few constructions in their own words and with their own pictures, none of "
     "them stating anything a library would record as a theorem or a definition."),
    ("BB2", "p", ["Building blocks"], GAP_STATEMENT),
    ("BB3", "p", ["Building blocks"],
     "The surveys go on to compare the constructions with each other at some length, discuss "
     "which of them a reader meets first in a course on widgets, and close with remarks on the "
     "pictures each one draws, again in words that describe the subject and state nothing "
     "about any particular widget or gadget."),
    ("H_MODULI", None, [], "Moduli"),
    ("MOD1", "p", ["Moduli"], "The sprocket counts c1, ..., ck are pairwise coprime "
                              "throughout. " + GAP_NOTATION),
    ("MOD2", "p", ["Moduli"], f"Then a widget of order {skip_formula('N')} has exactly one "
                              f"gadget of each count."),
    ("H_HISTORY", None, [], "History"),
    ("HIST", "p", ["History"], "The widget bound was first stated in a synthetic letter that "
                               "circulated among widget theorists."),
    ("SHORT", "p", ["History"], "See the gadgets page."),
    ("H_REFS", None, [], "References"),
    ("REF1", "li", ["References"], "A. Author, Widgets and their gadgets, a synthetic book "
                                   "of many pages."),
]
GAP_LAYER = "\n".join(text for _n, _b, _s, text in GAP_BLOCKS)


def _page_spans(blocks, layer: str) -> dict[str, list[int]]:
    out, cursor = {}, 0
    for name, _block, _section, text in blocks:
        a = layer.index(text, cursor)
        out[name] = [a, a + len(text)]
        cursor = a + len(text)
    return out


GAP_SPANS = _page_spans(GAP_BLOCKS, GAP_LAYER)


def _page_formulas(blocks, spans: dict[str, list[int]]) -> list[dict]:
    """The formulas of a page whose formulas are :func:`skip_formula`'s spelling."""
    out = []
    for name, _block, _section, text in blocks:
        base, at = spans[name][0], 0
        while True:
            j = text.find("{\\displaystyle ", at)
            if j < 0:
                break
            close = text.index("}", j)
            tex = text[j + len("{\\displaystyle "):close]
            out.append({"span": [base + j - len(tex), base + close + 1],
                        "alttext": "{\\displaystyle " + tex + "}"})
            at = close + 1
    return out


GAP_FORMULAS = _page_formulas(GAP_BLOCKS, GAP_SPANS)
GAP_PARAGRAPHS = [{"span": GAP_SPANS[name], "section": list(section), "block": block,
                   "formulas": text.count("{\\displaystyle "),
                   "math": "{\\displaystyle " in text}
                  for name, block, section, text in GAP_BLOCKS if block is not None]


def gap_passage(name: str, kind: str, scope: str, *, prominence: str = "supporting",
                until: str | None = None, words: int = 4, at: str | None = None) -> dict:
    """A passage of the gap page the way a worker writes one (:func:`skip_passage`'s)."""
    a = GAP_SPANS[name][0] if at is None else GAP_LAYER.index(at, GAP_SPANS[name][0])
    exact = " ".join(GAP_LAYER[a:].split(" ")[:words])
    section = next(s for n, _b, s, _t in GAP_BLOCKS if n == name)
    row = {"item": "", "head_words": 0, "span": None,
           "quote": {"exact": exact, "prefix": "", "suffix": ""},
           "section": list(section), "fragment": section[0] if section else None,
           "passage_kind": kind, "prominence": prominence if scope == "formal_math"
           else "supporting", "scope": scope,
           "statement": f"a synthetic restatement of the {name.lower()} passage",
           "concept": None, "finder": "model"}
    if until is not None:
        row["until"] = until
    return row


def gap_span(*names: str, start: str | None = None, end: str | None = None) -> list[int]:
    a, b = GAP_SPANS[names[0]][0], GAP_SPANS[names[-1]][1]
    if start is not None:
        a = GAP_LAYER.index(start, a)
    if end is not None:
        b = GAP_LAYER.index(end, a) + len(end)
    return [a, b]


def gap_body(*, blocks: str = "cover", notation: bool = False, history: str = "skip") -> dict:
    """A gap document's body placed by ``passage_ids`` under span-end rule 3.

    ``blocks``: the three paragraphs of "Building blocks" covered by passages (``cover``:
    two expository, the statement formal), left out by one skip (``skip``) or by nothing
    (``silent``, the shape the prose floor refuses).  ``notation``: MOD1's notation sentence
    a passage of its own; else MOD1's first sentence alone is a passage (context, expository,
    ended with ``until``), so the notation sentence is a run of the paragraph no passage and
    no skip holds -- a gap of audit rule 3.  ``history``: the History paragraph left out by a
    skip (``skip``), an expository passage (``cover``) or nothing (``silent``).  The short
    paragraph after it and the references are never covered."""
    rows = [gap_passage("LEAD", "definition", "formal_math", prominence="subject"),
            gap_passage("STMT", "theorem", "formal_math", prominence="central")]
    skips = [{"span": gap_span("REF1"), "reason": "references"}]
    if blocks == "cover":
        rows += [gap_passage("BB1", "other", "expository"),
                 gap_passage("BB2", "theorem", "formal_math"),
                 gap_passage("BB3", "other", "expository")]
    elif blocks == "skip":
        skips.append({"span": gap_span("BB1", "BB3"), "reason": "the history of the "
                                                                 "constructions"})
    rows.append(gap_passage("MOD1", "other", "expository",
                            until=None if notation else "pairwise coprime throughout."))
    if notation:
        rows.append(gap_passage("MOD1", "notation", "formal_math", at=GAP_NOTATION))
    rows.append(gap_passage("MOD2", "theorem", "formal_math"))
    if history == "cover":
        rows.append(gap_passage("HIST", "other", "expository"))
    elif history == "skip":
        skips.append({"span": gap_span("HIST"), "reason": "history"})
    doc = {"passages": rows, "skips": sorted(skips, key=lambda s: s["span"]), "notes": ""}
    problems = passage_ids.place(doc, GAP_LAYER, GAP_FORMULAS, GAP_PARAGRAPHS,
                                 rule=vocab.SPAN_END_CONTINUED)
    assert not problems, problems
    return doc
