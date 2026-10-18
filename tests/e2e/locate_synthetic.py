#!/usr/bin/env python3
"""locate_synthetic.py -- the whole locate flow on synthetic data, end to end.

New code, for the integration of the locate packages: each stage below is
another package's, and each package's own tests build the stage before it from
the documented format (references/locate-documents.md sections 7-10,
references/locate-bench.md).  This script instead chains the real commands, so
an artifact one stage writes is the one the next stage reads:

1. a universe through ``cf corpus`` (init, add, a canary, ingest, build-db) from
   synthetic probe indexes (section 8);
2. a sourceset through ``cf sourceset`` (create, enumerate-wikiproject, fetch,
   render) from the recorded Wikipedia answers under ``tests/data/locate/wiki``
   (section 7);
3. ``cf locate candidates`` (L2) with a synthetic 1000.yaml;
4. ``cf plan-locate`` for all three presets (section 9);
5. per run, documents written the way a worker writes them (quotes only, then
   the STAGED ``passage_ids.py``), the STAGED gate run from a copy of the
   planned root's ``gate/`` directory outside this checkout, with ``python3
   -I``, a bare environment and a scratch working directory: the good documents
   pass and the bad ones are refused for the right reason (the field defect P2
   was a staged gate that could not import, which tests run from
   ``scripts/gate/`` never saw);
6. ``cf harvest --from <findings>`` with a usage export for the cost block;
7. ``cf tally --locate`` (section 10) and ``cf export literature-links``;
8. ``cf bench build | anchor | freeze | score`` over the harvested runs.

Every page, library and declaration is made up ("Widget lemma", page 1001,
revision 2002, Q99999001; the libraries ``mathlib`` and ``alpha`` here are
synthetic stand-ins).  Every check reads an artifact or an exit code, never a
log line alone.

usage:  python3 tests/e2e/locate_synthetic.py [<scratch dir>]
        with no argument it works under a fresh temporary directory, never inside
        the checkout; it exits 0 when every check passed.
``tests/test_e2e_locate_synthetic.py`` runs it as part of the suite.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
SKILL = TESTS.parent
SCRIPTS = SKILL / "scripts"
for _p in (str(TESTS), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _hermetic  # noqa: E402,F401  -- HOME pinned before cf is imported

import cf as cf_main  # noqa: E402

FIXTURES = TESTS / "data" / "locate" / "wiki"
SS, U = "wpm", "u1"
TOOLCHAIN = "4.34.0"
MATHLIB, ALPHA = "a" * 40, "b" * 40
W1, W2, W3 = "enwiki-1001-2002", "enwiki-1002-2003", "enwiki-1003-2004"
MODEL = "claude-opus-5"           # priced by the shipped workspace.yaml table


class Failed(Exception):
    pass


class Flow:
    """One scratch directory, one workspace, and the checks made so far."""

    def __init__(self, scratch: Path, out=sys.stdout):
        self.scratch = scratch.resolve()
        self.ws = self.scratch / "ws"
        self.out = out
        self.failed = 0
        self.passed = 0

    # -- reporting --------------------------------------------------------
    def say(self, text: str) -> None:
        print(text, file=self.out)

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            self.say(f"  ok    {label}")
        else:
            self.failed += 1
            self.say(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        return ok

    def equal(self, label: str, got, want) -> bool:
        return self.check(label, got == want, f"got {got!r}, want {want!r}")

    # -- running ----------------------------------------------------------
    def cf(self, *argv, want: int = 0) -> str:
        """``cf --workspace <ws> argv``, in process; the output, or Failed on another exit."""
        out, err = io.StringIO(), io.StringIO()
        args = [str(a) for a in argv]
        if args[0] != "init":
            args = ["--workspace", str(self.ws), *args]
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cf_main.cli(args)
            except SystemExit as exc:
                code = int(exc.code or 0)
        text = out.getvalue() + err.getvalue()
        if code != want:
            raise Failed(f"cf {' '.join(args)} exited {code}, want {want}:\n{text}")
        return text

    def isolated(self, script: Path, *argv, cwd: Path) -> subprocess.CompletedProcess:
        """A staged tool as the run host runs it: python3 -I, a bare environment."""
        cwd.mkdir(parents=True, exist_ok=True)
        return subprocess.run([sys.executable, "-I", str(script), *map(str, argv)],
                              capture_output=True, text=True, cwd=str(cwd), timeout=300,
                              env={"PATH": "/usr/bin:/bin", "HOME": str(cwd)})

    # -- the paths --------------------------------------------------------
    @property
    def paths(self):
        from cflib.paths import WorkspacePaths
        return WorkspacePaths(self.ws)

    def source_dir(self, sid: str) -> Path:
        return self.paths.sourceset_source_dir(SS, sid)

    def layer(self, sid: str) -> str:
        return (self.source_dir(sid) / "layer.txt").read_text(encoding="utf-8")

    def read(self, path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def jsonl(self, path: Path) -> list:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]


# --------------------------------------------------------------------------
# 1. the universe
# --------------------------------------------------------------------------

def _decl(name: str, module: str, kind: str = "theorem", status: str = "proved", *,
          line: int, signature: str = "True", docstring: str | None = None,
          axioms: tuple = ()) -> dict:
    return {"record": "declaration", "name": name, "module": module, "path": None,
            "kind": kind, "line": line, "start_line": line, "end_line": line + 3,
            "signature": signature, "docstring": docstring, "mentions": [], "private": False,
            "wrapper": None, "proof_status": status, "nonstandard_axioms": list(axioms),
            "deprecated": None, "truncated": False, "truncated_fields": [], "errors": []}


MATHLIB_DECLS = [
    _decl("Widget.exists_spanning_gadget", "Mathlib.Widget", line=10,
          signature="∀ (G : Widget), G.Finite → ∃ g : Gadget, g.Spans G",
          docstring="The widget lemma (https://en.wikipedia.org/wiki/Widget_lemma): every "
                    "finite widget has a spanning gadget."),
    _decl("Widget.spanning_gadget_card_dvd", "Mathlib.Widget", line=20,
          signature="∀ (G : Widget) (g : Gadget), g.Spans G → g.card ∣ G.order",
          docstring="A spanning gadget's size divides the order of the widget."),
    _decl("Widget.spanning_conj", "Mathlib.Widget", line=30, status="statement_only",
          signature="∀ (G : Widget) (g h : Gadget), g.Spans G → h.Spans G → IsConj g h",
          docstring="Every two spanning gadgets are conjugate.", axioms=("sorryAx",)),
    _decl("Widget.Spans", "Mathlib.Widget", kind="def", status="definition", line=40,
          signature="Widget → Gadget → Prop", docstring="A gadget spans a widget."),
]
ALPHA_DECLS = [
    _decl("Alpha.prime_widget_unique", "Alpha.Widget", line=10,
          signature="∀ (G : Widget), G.Prime → ∃! g : Gadget, g.Spans G",
          docstring="A prime widget has exactly one spanning gadget."),
]
CANARY_DECLS = [
    _decl("CfCanary.hiddenSorry", "CfCanary.Basic", status="statement_only", line=1,
          axioms=("sorryAx",)),
    dict(_decl("CfCanary.wantedTheorem", "CfCanary.Basic", status="wanted", line=2),
         wrapper="CfCanary.TheoremWanted"),
    _decl("CfCanary.propDef", "CfCanary.Basic", kind="def", status="definition", line=3,
          signature="Prop"),
    _decl("CfCanary.axiomTheorem", "CfCanary.Basic", line=4, axioms=("CfCanary.canaryAxiom",)),
    _decl("CfCanary.canaryAxiom", "CfCanary.Basic", kind="axiom", status="axiom", line=5,
          axioms=("CfCanary.canaryAxiom",)),
]


def write_probe(directory: Path, decls: list, *, library: str, commit: str,
                modules: tuple) -> Path:
    """A probe output directory as ``scripts/corpus/probe_merge.py`` writes it."""
    directory.mkdir(parents=True, exist_ok=True)
    head = {"record": "header", "schema": "formal-companion-library-index/v0",
            "library": library, "toolchain": TOOLCHAIN, "probe_revision": 2, "commit": commit,
            "declaration_count": len(decls), "module_count": len(modules)}
    lines = [head] + [{"record": "module", "name": n, "path": p, "module_doc": None,
                       "truncated": False} for n, p in modules] + list(decls)
    index = directory / "index.jsonl"
    index.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines),
                     encoding="utf-8")
    (directory / "manifest.json").write_text(json.dumps({
        "library": library, "commit": commit, "toolchain": f"leanprover/lean4:v{TOOLCHAIN}",
        "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
        "index_bytes": index.stat().st_size,
        "records": {"header": 1, "module": len(modules), "declaration": len(decls)},
        "dirty": False, "template_sha256": "0" * 64, "modules_without_path": 0}),
        encoding="utf-8")
    return directory


def stage_universe(f: Flow) -> None:
    f.say("\n=== 1. the universe (cf corpus)")
    probes = f.scratch / "probes"
    f.cf("corpus", "init", U, "--frozen", "2026-10-17")
    for lib, commit, root in (("mathlib", MATHLIB, "Mathlib"), ("alpha", ALPHA, "Alpha")):
        f.cf("corpus", "add", U, "--library", lib, "--repo", f"https://example.org/{lib}",
             "--commit", commit, "--toolchain", f"leanprover/lean4:v{TOOLCHAIN}", "--roots", root)
    canary = write_probe(probes / "canary", CANARY_DECLS, library="cf-canary", commit=MATHLIB,
                         modules=(("CfCanary.Basic", "CfCanary/Basic.lean"),))
    f.check("the toolchain canary passes", "PASSED" in f.cf("corpus", "ingest", U, "--canary",
                                                           "--from", canary))
    for lib, commit, decls, module in (("mathlib", MATHLIB, MATHLIB_DECLS, "Mathlib.Widget"),
                                       ("alpha", ALPHA, ALPHA_DECLS, "Alpha.Widget")):
        probe = write_probe(probes / lib, decls, library=lib, commit=commit,
                            modules=((module, module.replace(".", "/") + ".lean"),))
        out = f.cf("corpus", "ingest", U, "--library", lib, "--from", probe)
        f.check(f"{lib}: {len(decls)} declarations ingested", f"{len(decls)} declarations" in out)
    f.cf("corpus", "build-db", U)
    status = json.loads(f.cf("corpus", "status", U, "--json"))
    f.equal("corpus.sqlite is current, with every declaration",
            (status["corpus_db_state"], status["corpus_db"]["declarations"]),
            ("current", len(MATHLIB_DECLS) + len(ALPHA_DECLS)))
    hits = [json.loads(line) for line in
            f.cf("corpus", "search", U, "spanning gadget", "--json").splitlines() if line.strip()]
    f.check("the corpus search finds the widget lemma",
            any(h.get("declaration") == "Widget.exists_spanning_gadget" for h in hits),
            str(hits[:3]))


# --------------------------------------------------------------------------
# 2-3. the sourceset and L2
# --------------------------------------------------------------------------

def stage_sourceset(f: Flow) -> None:
    f.say("\n=== 2. the sourceset (cf sourceset, recorded answers)")
    f.cf("sourceset", "create", SS)
    f.cf("sourceset", "enumerate-wikiproject", SS, "--project", "Mathematics",
         "--fixtures", FIXTURES)
    f.cf("sourceset", "fetch", SS, "--fixtures", FIXTURES)
    f.cf("sourceset", "render", SS)
    report = json.loads(f.cf("sourceset", "status", SS, "--json", "--verify"))
    f.equal("three sources rendered", report["counts"]["rendered"], 3)
    from sources import render_html
    for sid in (W1, W2, W3):
        entry = f.read(f.source_dir(sid) / "source.json")
        f.equal(f"{sid} is rendered by {render_html.RENDERER}", entry.get("renderer"),
                render_html.RENDERER)


def stage_candidates(f: Flow) -> None:
    f.say("\n=== 3. L2 candidates (cf locate candidates)")
    curated = f.scratch / "inputs" / "1000.yaml"
    curated.parent.mkdir(parents=True, exist_ok=True)
    curated.write_text("Q99999001:\n  title: Widget lemma\n  decl: Widget.exists_spanning_gadget\n",
                       encoding="utf-8")
    f.curated_1000 = curated
    f.cf("locate", "candidates", "--sourceset", SS, "--universe", U, "--curated-1000", curated)
    doc = f.read(f.paths.candidates_json(SS, U, W1))
    f.check("W1's curated hit comes from 1000.yaml",
            {"mathlib_1000"} <= {c["via"] for c in doc["curated"]
                                 if c["declaration"] == "Widget.exists_spanning_gadget"},
            str(doc["curated"]))
    lexical = {(h["library"], h["declaration"]) for h in doc["lexical"]}
    f.check("W1's lexical hits are the other widget declarations, the curated one left out",
            ("mathlib", "Widget.spanning_gadget_card_dvd") in lexical
            and ("mathlib", "Widget.exists_spanning_gadget") not in lexical, str(sorted(lexical)))


# --------------------------------------------------------------------------
# 4-6. the three runs: plan, write, the staged gate, harvest
# --------------------------------------------------------------------------

class Run:
    """One planned run, its staged gate copied outside the root, and a worktree."""

    def __init__(self, f: Flow, run_id: str):
        self.f = f
        self.run_id = run_id
        self.root = f.paths.tend_root(run_id)
        self.host_gate = f.scratch / "host" / f"{run_id}-gate"
        shutil.rmtree(self.host_gate, ignore_errors=True)
        shutil.copytree(self.root / "gate", self.host_gate)
        self.worktree = f.scratch / "worktrees" / run_id
        self.manifests = [f.read(p) for p in sorted((self.root / "gate" / "tasks").glob("*.json"))]

    def gate(self, rel: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return self.f.isolated(self.host_gate / "verify_locate.py", "--gate-config",
                               self.host_gate / "gate.json", "--findings", rel,
                               cwd=cwd or self.worktree)

    def write(self, rel: str, doc: dict, root: Path | None = None) -> Path:
        path = (root or self.worktree) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def passed(self, label: str, rel: str) -> None:
        proc = self.gate(rel)
        self.f.check(f"{label}: the staged gate passes it", proc.returncode == 0
                     and "OK: 1 locate document(s) validated" in proc.stdout,
                     (proc.stdout + proc.stderr)[-1500:])

    def refused(self, label: str, rel: str, doc: dict, needle: str) -> None:
        bad = self.f.scratch / "bad" / self.run_id / label.replace(" ", "-")
        self.write(rel, doc, root=bad)
        proc = self.gate(rel, cwd=bad)
        self.f.check(f"{label}: refused (exit 1, VALIDATION FAILED, {needle!r})",
                     proc.returncode == 1 and "VALIDATION FAILED" in proc.stderr
                     and needle in proc.stderr and "could not run" not in proc.stderr,
                     (proc.stdout + proc.stderr)[-1500:])

    def harvest(self, usage: Path) -> dict:
        self.f.cf("harvest", self.run_id, "--from", self.worktree / "findings",
                  "--transcripts", usage)
        receipt = self.f.read(self.f.paths.corpus_run_dir(self.run_id) / "RECEIPT.json")
        self.f.check(f"{self.run_id}: harvested complete and gated",
                     bool(receipt.get("complete")) and bool((receipt.get("gate") or {}).get("ok")),
                     json.dumps(receipt.get("gate"))[:800])
        self.f.check(f"{self.run_id}: the receipt carries a priced cost",
                     float((receipt.get("cost") or {}).get("usd") or 0) > 0)
        return receipt


def _header(run: Run, mode: str, manifest: dict, doc_m: dict, *, universe: bool) -> dict:
    doc = {"schema": "cf-locate/1", "mode": mode, "run_id": run.run_id,
           "task_id": manifest["task_id"], "source_id": doc_m["source_id"],
           "source_sha": doc_m["source_sha"], "layer_sha256": doc_m["layer_sha256"],
           "renderer": doc_m["renderer"]}
    if universe:
        from locate import inputs
        u = inputs.load_universe(run.f.paths, U)
        doc.update(universe=u.id, universe_sha256=u.sha256)
    return doc


def _quote_at(layer: str, start: int) -> dict:
    """The shortest run of the passage's first words that occurs once in the layer."""
    end = start
    for _ in range(40):
        while end < len(layer) and not layer[end].isspace():
            end += 1
        exact = layer[start:end]
        if len(exact.split()) >= 4 and layer.count(exact) == 1:
            return {"exact": exact, "prefix": "", "suffix": ""}
        while end < len(layer) and layer[end].isspace():
            end += 1
    raise Failed(f"no unique quote at {start}")


def _section(sections: list, start: int) -> tuple[list, str | None]:
    best = None
    for s in sections:
        a, b = s["span"]
        if a <= start < b and (best is None or s["level"] >= best["level"]):
            best = s
    return (list(best["path"]), best.get("fragment")) if best else ([], None)


#: the passages a worker adds beside the finders', by the first words of the paragraph
MODEL_PASSAGES = {W1: [("Every two spanning gadgets", "theorem", "supporting",
                        "Any two spanning gadgets of a finite widget are conjugate.")]}
#: what the finders' passages are, per source: (kind, prominence, statement), in start order
FINDER_LABELS = {
    W1: [("theorem", "subject", "Every finite widget has a spanning gadget."),
         ("theorem", "central", "A finite widget of order n has a spanning gadget whose size "
                                "divides n.")],
    W2: [("theorem", "subject", "Every gadget is the union of two widgets.")],
}


def inventory_document(run: Run, manifest: dict, doc_m: dict) -> dict:
    """What a worker writes before ``passage_ids.py``: quotes, labels, no ids."""
    sid = doc_m["source_id"]
    doc = _header(run, "locate-inventory", manifest, doc_m, universe=False)
    if sid == W3:                       # a stub that is no mathematics
        doc.update(passages=[], unresolved={"reason": "the article is not mathematics",
                                            "by": "worker"}, notes="")
        return doc
    layer = run.f.layer(sid)
    sections = run.f.read(run.f.source_dir(sid) / "sections.json")
    rows, seen = [], set()
    labels = iter(FINDER_LABELS[sid])
    for finder in doc_m["finders"]:
        if finder["start"] in seen:     # finders that share a start are one passage
            continue
        seen.add(finder["start"])
        kind, prominence, statement = next(labels)
        path, fragment = _section(sections, finder["start"])
        rows.append({"item": "", "head_words": 0, "span": None,
                     "quote": _quote_at(layer, finder["start"]), "section": path,
                     "fragment": fragment, "passage_kind": kind, "prominence": prominence,
                     "scope": "formal_math", "statement": statement, "concept": None,
                     "finder": finder["finder"]})
    for words, kind, prominence, statement in MODEL_PASSAGES.get(sid, []):
        start = layer.index(words)
        path, fragment = _section(sections, start)
        rows.append({"item": "", "head_words": 0, "span": None,
                     "quote": _quote_at(layer, start), "section": path, "fragment": fragment,
                     "passage_kind": kind, "prominence": prominence, "scope": "formal_math",
                     "statement": statement, "concept": None, "finder": "model"})
    doc.update(passages=rows, skips=[], notes="")
    return doc


def add_skips(run: Run, doc: dict) -> None:
    """A skip for every math paragraph no passage covers (the coverage floor)."""
    if doc.get("unresolved"):
        return
    paragraphs = run.f.read(run.f.source_dir(doc["source_id"]) / "paragraphs.json")
    covered = [tuple(p["span"]) for p in doc["passages"]]
    for para in paragraphs:
        a, b = para["span"]
        if para.get("math") and not any(a < y and x < b for x, y in covered):
            doc["skips"].append({"span": [a, b], "reason": "a displayed formula or setup "
                                                          "sentence of a passage kept above"})
            covered.append((a, b))


def stage_inventory(f: Flow, usage: Path) -> None:
    f.say("\n=== 4a. inventory: plan, write, the staged gate, harvest")
    f.cf("plan-locate", "--preset", "locate-inventory", "--sourceset", SS, "--universe", U,
         "--run-id", "inv-1")
    run = Run(f, "inv-1")
    f.equal("one task per rendered source", len(run.manifests), 3)
    proc = f.isolated(run.host_gate / "verify_locate.py", "--gate-config",
                      run.host_gate / "gate.json", "--self-check", cwd=run.worktree)
    f.check("the staged gate's self-check passes under python3 -I",
            proc.returncode == 0 and str(run.host_gate / "vendor") in proc.stdout,
            proc.stdout + proc.stderr)
    empty = f.scratch / "empty-worktree"
    (empty / "findings").mkdir(parents=True, exist_ok=True)
    proc = f.isolated(run.host_gate / "verify_locate.py", "--gate-config",
                      run.host_gate / "gate.json", cwd=empty)
    f.check("an empty findings tree is refused, not passed",
            proc.returncode == 1 and "an empty set is not a clean one" in proc.stderr,
            proc.stdout + proc.stderr)
    good = {}
    for manifest in run.manifests:
        for doc_m in manifest["documents"]:
            rel = f"findings/{manifest['task_id']}/{doc_m['source_id']}.json"
            doc = inventory_document(run, manifest, doc_m)
            run.write(rel, doc)
            if doc["passages"]:
                proc = f.isolated(run.host_gate / "passage_ids.py", "--source-dir",
                                  f.source_dir(doc_m["source_id"]), "--document", rel, "--write",
                                  cwd=run.worktree)
                f.check(f"{doc_m['source_id']}: the staged passage_ids.py keys every passage",
                        proc.returncode == 0, proc.stdout + proc.stderr)
                doc = f.read(run.worktree / rel)
                add_skips(run, doc)
                run.write(rel, doc)
            run.passed(f"inventory {doc_m['source_id']}", rel)
            good[doc_m["source_id"]] = (rel, doc)
    rel, doc = good[W1]
    forged = copy.deepcopy(doc)
    forged["passages"][1]["quote"]["exact"] = "Widget lemma — every forged word"
    run.refused("a forged quote", rel, forged, "the quote matches the layer 0 times")
    wrong = copy.deepcopy(doc)
    item = wrong["passages"][0]["item"]
    wrong["passages"][0]["item"] = item[:-1] + ("0" if item[-1] != "0" else "1")
    run.refused("a wrong passage id", rel, wrong, "-word head, not 'In-text #")
    dropped = copy.deepcopy(doc)
    box = next(p for p in doc["passages"] if p["finder"] == "theorem_box")
    dropped["passages"] = [p for p in doc["passages"] if p is not box and p != box]
    run.refused("a dropped finder passage", rel, dropped,
                f"the theorem_box passage at {box['span']} was dropped with no skip")
    run.harvest(usage)
    f.inventory = {sid: d for sid, (_rel, d) in good.items()}


def _loc(lib: str, decl: str, relation: str = "exactMatch", rank: int = 1) -> dict:
    return {"library": lib, "declaration": decl, "relation": relation, "rank": rank,
            "confidence": "high", "note": "", "witness": "the statement says it"}


def locate_answer(sid: str, passage: dict) -> dict:
    """The outcome a worker writes for one frozen passage of the synthetic sources."""
    item = passage["item"]
    first = passage.get("quote", {}).get("exact", "") if isinstance(passage.get("quote"), dict) else ""
    if sid == W2:
        return {"item": item, "outcome": "absent", "locations": [],
                "receipt": {"queries": ["gadget union of two widgets", "gadget theorem",
                                        "gadget spine split"], "candidates": []}}
    if first.startswith("In widget theory"):
        return {"item": item, "outcome": "located",
                "locations": [_loc("mathlib", "Widget.exists_spanning_gadget")],
                "receipt": {"queries": ["widget lemma", "spanning gadget"], "candidates": []}}
    if first.startswith("Every two spanning"):
        return {"item": item, "outcome": "located",
                "locations": [_loc("mathlib", "Widget.spanning_conj")],
                "receipt": {"queries": ["spanning gadgets conjugate"], "candidates": []}}
    return {"item": item, "outcome": "located",
            "locations": [_loc("mathlib", "Widget.spanning_gadget_card_dvd", "broadMatch"),
                          _loc("mathlib", "Widget.exists_spanning_gadget", "relatedMatch", 2)],
            "receipt": {"queries": ["gadget size divides order"], "candidates": []}}


def stage_locate(f: Flow, usage: Path) -> None:
    f.say("\n=== 4b. locate: plan, write, the staged gate, harvest")
    f.cf("plan-locate", "--preset", "locate", "--sourceset", SS, "--universe", U,
         "--inventory-run", "inv-1", "--run-id", "loc-1")
    run = Run(f, "loc-1")
    frozen = {p["item"]: p for d in f.inventory.values() for p in d.get("passages", [])}
    planned = [p for m in run.manifests for d in m["documents"] for p in d["passages"]]
    f.equal("the locate tasks carry every formal_math passage of the inventory",
            sorted(p["item"] for p in planned), sorted(frozen))
    first = None
    for manifest in run.manifests:
        for doc_m in manifest["documents"]:
            sid = doc_m["source_id"]
            rel = f"findings/{manifest['task_id']}/{sid}.json"
            doc = _header(run, "locate", manifest, doc_m, universe=True)
            doc["passages"] = [locate_answer(sid, frozen[p["item"]]) for p in doc_m["passages"]]
            run.write(rel, doc)
            run.passed(f"locate {manifest['task_id']}", rel)
            if first is None and any(p["outcome"] == "located" for p in doc["passages"]):
                first = (rel, doc)
    rel, doc = first
    ghost = copy.deepcopy(doc)
    loc = next(p for p in ghost["passages"] if p["outcome"] == "located")
    loc["locations"][0]["declaration"] = "Widget.never_declared"
    run.refused("a declaration outside the universe", rel, ghost, "Widget.never_declared")
    run.harvest(usage)


def challenge_verdict(record: dict) -> dict:
    verdict = {"item": record["item"], "target": record["target"], "witness": "checked"}
    if record["target"] == "location":
        verdict.update(location_key=record["location_key"], action="confirm")
    else:
        verdict["action"] = "uphold"
    return verdict


def stage_challenge(f: Flow, usage: Path) -> None:
    f.say("\n=== 4c. challenge: plan, write, the staged gate, harvest")
    f.cf("plan-locate", "--preset", "locate-challenge", "--sourceset", SS, "--universe", U,
         "--challenge-of", "loc-1", "--run-id", "chl-1")
    run = Run(f, "chl-1")
    records = [r for m in run.manifests for d in m["documents"] for r in d["records"]]
    f.check("the challenge plan holds a location and the absence",
            {"location", "absence"} <= {r["target"] for r in records}, str(records)[:400])
    first = None
    for manifest in run.manifests:
        for doc_m in manifest["documents"]:
            rel = f"findings/{manifest['task_id']}/{doc_m['source_id']}.json"
            doc = _header(run, "locate-challenge", manifest, doc_m, universe=True)
            doc["verdicts"] = [challenge_verdict(r) for r in doc_m["records"]]
            run.write(rel, doc)
            run.passed(f"challenge {manifest['task_id']}", rel)
            first = first or (rel, doc)
    rel, doc = first
    owed = copy.deepcopy(doc)
    owed["verdicts"][0]["action"] = "endorse"
    run.refused("a verdict outside the vocabulary", rel, owed, "endorse")
    run.harvest(usage)


# --------------------------------------------------------------------------
# 7. the tally and the export
# --------------------------------------------------------------------------

def stage_tally_and_export(f: Flow) -> None:
    f.say("\n=== 5. the tally (cf tally --locate) and the export")
    f.cf("tally", "--locate", "--run", "loc-1", "--challenge-run", "chl-1")
    rows = f.jsonl(f.paths.locate_outcomes_jsonl("loc-1"))
    by = {(r["source_id"], r["outcome"]): r for r in rows}
    f.equal("the outcomes: three located on W1, the absence on W2",
            sorted((r["source_id"], r["outcome"]) for r in rows),
            sorted([(W1, "located")] * 3 + [(W2, "absent")]))
    f.check("every row's status is derived and its challenge applied",
            all(r["status"] is not None and r["challenge"] for r in rows),
            json.dumps([(r["status"], r["challenge"]) for r in rows]))
    f.equal("the absence is not_formalized after the challenge upheld it",
            by[(W2, "absent")]["status"], "not_formalized")
    out = f.cf("export", "literature-links", "--run", "loc-1", "--challenge-run", "chl-1",
               "--set-id", "set-1")
    from locate import export as export_mod, records
    out_dir = f.paths.export_dir("set-1")
    f.equal("the exported set's seal verifies", export_mod.verify_seal(out_dir), [])
    recs = [json.loads(line) for p in sorted((out_dir / "records").glob("*.jsonl"))
            for line in p.read_text(encoding="utf-8").splitlines()]
    f.check("the records pass the vendored Literature Links checker",
            bool(recs) and records.check_all(recs) == [], out[-800:])


# --------------------------------------------------------------------------
# 8. the bench
# --------------------------------------------------------------------------

def stage_bench(f: Flow) -> None:
    f.say("\n=== 6. the bench (cf bench build | anchor | freeze | score)")
    from bench import gold as gold_mod
    from bench.layout import BenchLayout
    frame = f.scratch / "inputs" / "tier-a.txt"
    frame.write_text("1001\n1002\n1003\n", encoding="utf-8")
    f.cf("bench", "build", "b1", "--sourceset", SS, "--frame", frame, "--curated-qids",
         f.curated_1000, "--uniform", 3, "--curated", 1, "--spans-per-article", 2,
         "--dev", 0, "--eval", 1, "--seed", 7)
    lay = BenchLayout.of(f.paths, "b1")
    f.cf("bench", "anchor", "b1", "--universe", U, "--curated-1000", f.curated_1000)

    def transcribe(mode: str, make) -> None:
        f.cf("bench", "anchor", "b1", "--emit-args", mode)
        args = f.read(lay.label_args(mode))
        labels = [make(t) for t in args["tasks"]]
        result = {"schema": "cf-locate-label-v1", "mode": args["mode"], "bench": args["bench"],
                  "args_digest": args["digest"],
                  "labeler": {"version": gold_mod.LABELER_VERSION}, "labels": labels}
        path = f.scratch / f"label-{mode}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        f.cf("bench", "anchor", "b1", "--transcribe", path)

    subject = {sid: next(p for p in d["passages"] if p["prominence"] == "subject")
               for sid, d in f.inventory.items() if d.get("passages")}
    transcribe("anchor", lambda t: {"task_id": t["task_id"],
                                    "quote": {"exact": subject[t["source_id"]]["quote"]["exact"]},
                                    "relation": "exactMatch", "passage_kind": "theorem",
                                    "evidence": "read at the pin"})
    transcribe("label", lambda t: {"task_id": t["task_id"], "scope": "formal_math",
                                   "passage_kind": "theorem",
                                   "declarations": [{"library": "mathlib",
                                                     "declaration": "Widget.exists_spanning_gadget",
                                                     "relation": "exactMatch"}],
                                   "evidence": "e", "hunters": []})
    f.cf("bench", "freeze", "b1")
    version, manifest = gold_mod.latest_manifest(lay)
    f.check("the bench froze with G1 and G3 gold", {"G1", "G3"} <= set(manifest["tiers"]),
            str(manifest.get("tiers")))
    f.equal("every frame article is in eval", sorted(manifest["splits"]["eval"]),
            [W1, W2, W3])
    f.cf("bench", "score", "b1", "--run", "loc-1", "--split", "eval", "--no-canary-check",
         "--bootstrap", 50)
    newest = max(lay.scores_dir.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    score = f.read(newest)
    f.equal("the score covers the three eval articles", score["coverage"]["articles"], 3)
    f.check("the score is priced from the harvested receipts",
            float((score.get("cost") or {}).get("usd_total") or 0) > 0,
            json.dumps(score.get("cost")))
    g3 = score["tiers"]["G3"]["metrics"]
    f.check("G3 is measured, the absence and the unresolved stub counted as misses",
            g3["fnf_rate"]["causes"]["absence"] >= 1 and g3["fnf_rate"]["causes"]["unresolved"] >= 1,
            json.dumps(g3["fnf_rate"]))
    g1 = score["tiers"]["G1"]["metrics"]
    f.check("G1 is measured on the curated article", bool(g1), json.dumps(g1)[:400])


# --------------------------------------------------------------------------

def usage_export(f: Flow) -> Path:
    """A usage export (``scripts/cost/usage.py``): one priced response per run."""
    path = f.scratch / "usage" / "usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": MODEL, "usage": {
        "input_tokens": 12000, "output_tokens": 3000, "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0}}) + "\n", encoding="utf-8")
    return path


def main(scratch: Path | None = None, out=sys.stdout) -> Flow:
    own = scratch is None
    scratch = Path(tempfile.mkdtemp(prefix="cf-locate-e2e-")) if own else Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    if str(scratch.resolve()).startswith(str(SKILL) + os.sep):
        raise SystemExit("the scratch directory must be outside the skill checkout")
    f = Flow(scratch, out)
    f.cf("init", f.ws, "--no-git", "-q")
    usage = usage_export(f)
    try:
        for stage in (stage_universe, stage_sourceset, stage_candidates):
            stage(f)
        stage_inventory(f, usage)
        stage_locate(f, usage)
        stage_challenge(f, usage)
        stage_tally_and_export(f)
        stage_bench(f)
    except Failed as exc:
        f.failed += 1
        f.say(f"  FAIL  {exc}")
    f.say(f"\n{f.passed} check(s) passed, {f.failed} failed; workspace {f.ws}")
    return f


if __name__ == "__main__":
    flow = main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    sys.exit(1 if flow.failed else 0)
