"""The locate pipeline end to end, on synthetic inputs, through the real CLI.

New code, for the locate integration (docs/proposals/wikipedia-locate.md
sections 2-9; the formats are references/locate-documents.md).  Every stage is
the shipped producer, not a fixture of the next stage's format:

    universe   cf corpus init / add / ingest --canary / ingest / build-db
               over a synthetic probe index and a synthetic library checkout
    sourceset  cf sourceset create / adopt / render over a producer's delivery:
               pins.jsonl and the response bytes written over the recorded
               synthetic parse responses in tests/data/locate/wiki, each row
               with the fixed fetch and the producer's layer sha (computed here
               with this skill's own renderer); a delivery whose layer sha is
               not this renderer's layer is refused by render
               (references/locate-documents.md section 7)
    L2         cf locate candidates with a synthetic 1000.yaml-shaped list
    L3a        cf plan-locate --preset locate-inventory; a worker's documents
               filled by the staged passage_ids.py; the STAGED gate (out of
               tree, from the tend root) over good and bad documents; cf harvest
    L3b        cf plan-locate --preset locate over the frozen inventory; the
               staged gate over good and bad documents; cf harvest
    L5         cf plan-locate --preset locate-challenge; verdicts; gate; harvest
    L6         cf tally --locate, cf export literature-links (sealed, checked)
    bench      cf bench build / anchor / freeze / score over the same runs

Nothing touches the network or a model.  Every check reads an artifact, never
a log line.  Run it on its own with ``python3 tests/e2e/locate_synthetic/driver.py
[--keep <dir>]``; ``tests/test_e2e_locate_synthetic.py`` runs it in the suite.
"""
from __future__ import annotations

import argparse
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
SKILL = HERE.parents[2]
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main  # noqa: E402
from cflib.errors import CfError  # noqa: E402

WIKI_FIXTURES = SKILL / "tests" / "data" / "locate" / "wiki"
#: the synthetic pages a producer delivers, with their metadata as a producer's
#: catalog spells it (importance capitalised, as the PageAssessments listing
#: gives it; adopt lower-cases it on the way in)
PAGES = (
    (1001, 2002, {"title": "Widget lemma", "wikidata": "Q99999001",
                  "assessment": {"class": "B", "importance": "High"}}),
    (1002, 2003, {"title": "Gadget theorem", "wikidata": "Q99999002",
                  "assessment": {"class": "Start", "importance": "Mid"}}),
    (1003, 2004, {"title": "Sprocket", "wikidata": None,
                  "assessment": {"class": "Stub", "importance": "Low"}}),
)
CONSUMER = "wikilean"
TOOLCHAIN = "leanprover/lean4:v4.34.0"
SS = "wpm"
U = "u1"
WIDGET = "enwiki-1001-2002"
GIT_ENV = {"GIT_AUTHOR_NAME": "e2e", "GIT_AUTHOR_EMAIL": "e2e@example.invalid",
           "GIT_COMMITTER_NAME": "e2e", "GIT_COMMITTER_EMAIL": "e2e@example.invalid",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


class StageError(AssertionError):
    """A stage produced the wrong artifact; the message names the stage and the file."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise StageError(message)


class Pipeline:
    """One synthetic campaign under ``root``; each method is one stage."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.ws = self.root / "ws"
        self.made = self.root / "made"
        self.made.mkdir(parents=True, exist_ok=True)
        self.log: list[str] = []

    # ------------------------------------------------------------------ cli
    def cf(self, *argv: str, ok: bool = True) -> tuple[int, str]:
        """Run ``cf`` in-process; ``ok`` demands exit 0 (a CfError counts as failure)."""
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                code = cf_main.main([*argv, "--workspace", str(self.ws)])
        except CfError as exc:
            code = getattr(exc, "exit_code", 1) or 1
            buf.write(f"error: {exc}\n")
        out = buf.getvalue()
        self.log.append(f"$ cf {' '.join(argv)} -> {code}")
        if ok:
            check(code == 0, f"cf {' '.join(argv)} exited {code}:\n{out}")
        return int(code), out

    def git(self, cwd: Path, *args: str) -> str:
        proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                              env={**os.environ, **GIT_ENV})
        check(proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}")
        return proc.stdout.strip()

    def read_json(self, path: Path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def write_json(self, path: Path, data) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    # -------------------------------------------------------------- stage 0
    def workspace(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = cf_main.main(["init", str(self.ws), "--no-git", "-q"])
        check(code == 0, f"cf init failed: {buf.getvalue()}")

    # ------------------------------------------------------------ the universe
    def _library_checkout(self, name: str, files: dict[str, str]) -> tuple[Path, str]:
        """A git checkout of a synthetic library; returns (path, its HEAD commit)."""
        repo = self.made / f"checkout-{name}"
        for rel, text in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git(repo.parent, "init", "-q", str(repo))
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", f"{name} at the universe's pin")
        return repo, self.git(repo, "rev-parse", "HEAD")

    @staticmethod
    def _decl(name: str, module: str, path: str, line: int, end_line: int, *, kind="theorem",
              status="proved", signature="True", docstring=None, axioms=(), wrapper=None,
              start_line=None) -> dict:
        # `line` is the name's line; `start_line` takes in the docstring and attributes
        return {"record": "declaration", "name": name, "module": module, "path": path,
                "kind": kind, "line": line, "start_line": start_line or line, "end_line": end_line,
                "signature": signature, "docstring": docstring, "mentions": [],
                "private": False, "wrapper": wrapper, "proof_status": status,
                "nonstandard_axioms": list(axioms), "deprecated": None, "truncated": False,
                "truncated_fields": [], "errors": []}

    def _probe_index(self, name: str, library: str, commit: str, modules, decls) -> Path:
        directory = self.made / f"probe-{name}"
        directory.mkdir(parents=True, exist_ok=True)
        header = {"record": "header", "schema": "formal-companion-library-index/v0",
                  "library": library, "toolchain": "4.34.0", "probe_revision": 1,
                  "commit": commit, "declaration_count": len(decls)}
        lines = [header] + [{"record": "module", "name": m, "path": p, "module_doc": None,
                             "truncated": False} for m, p in modules] + sorted(
                                 decls, key=lambda d: (d["name"], d["module"]))
        index = directory / "index.jsonl"
        index.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines),
                         encoding="utf-8")
        data = index.read_bytes()
        self.write_json(directory / "manifest.json", {
            "schema": "formal-companion-library-index/v0/manifest", "library": library,
            "commit": commit, "template_sha256": "0" * 64,
            "index_sha256": hashlib.sha256(data).hexdigest(), "index_bytes": len(data),
            "records": {"header": 1, "module": len(modules), "declaration": len(decls)}})
        return directory

    def universe(self) -> None:
        alpha_src = {
            "Alpha/Widget.lean": "\n".join([
                "import Alpha.Basic",                                            # 1
                "",                                                              # 2
                "/-- The widget lemma: every finite widget has a spanning gadget.",
                "See https://en.wikipedia.org/wiki/Widget_lemma -/",             # 4
                "@[wikidata Q99999001]",                                         # 5
                "theorem Widget.exists_spanning_gadget (w : Widget) : ∃ g, g.Spans w := by",
                "  exact sorry_free_proof w",                                    # 7
                "",                                                              # 8
                "/-- Spanning. -/",                                              # 9
                "def Widget.Spans (g : Gadget) (w : Widget) : Prop := True",     # 10
                "",                                                              # 11
                "theorem Widget.card_dvd (w : Widget) : True := trivial",        # 12
                "",                                                              # 13
                "theorem Widget.sorried (w : Widget) : True := sorry",           # 14
                ""]),
            "Alpha/Basic.lean": "structure Widget\nstructure Gadget\n",
        }
        alpha_dir, alpha_commit = self._library_checkout("alpha", alpha_src)
        beta_src = {"Beta/Gadget.lean": "theorem Beta.gadget_lattice_height : True := trivial\n"}
        beta_dir, beta_commit = self._library_checkout("beta", beta_src)
        self.commits = {"alpha": alpha_commit, "beta": beta_commit}
        alpha = self._probe_index("alpha", "alpha", alpha_commit,
                                  [("Alpha.Widget", "Alpha/Widget.lean"),
                                   ("Alpha.Basic", "Alpha/Basic.lean")], [
            self._decl("Widget.exists_spanning_gadget", "Alpha.Widget", "Alpha/Widget.lean", 6, 7,
                       start_line=3, signature="∀ (w : Widget), ∃ g, g.Spans w",
                       docstring="The widget lemma: every finite widget has a spanning gadget. "
                                 "See https://en.wikipedia.org/wiki/Widget_lemma"),
            self._decl("Widget.Spans", "Alpha.Widget", "Alpha/Widget.lean", 10, 10, kind="def",
                       status="definition", signature="Gadget → Widget → Prop",
                       docstring="Spanning."),
            self._decl("Widget.card_dvd", "Alpha.Widget", "Alpha/Widget.lean", 12, 12,
                       signature="∀ (w : Widget), True"),
            self._decl("Widget.sorried", "Alpha.Widget", "Alpha/Widget.lean", 14, 14,
                       status="statement_only", axioms=["sorryAx"]),
            self._decl("Widget", "Alpha.Basic", "Alpha/Basic.lean", 1, 1, kind="structure",
                       status="definition", signature="Type"),
            self._decl("Gadget", "Alpha.Basic", "Alpha/Basic.lean", 2, 2, kind="structure",
                       status="definition", signature="Type"),
        ])
        beta = self._probe_index("beta", "beta", beta_commit, [("Beta.Gadget", "Beta/Gadget.lean")], [
            self._decl("Beta.gadget_lattice_height", "Beta.Gadget", "Beta/Gadget.lean", 1, 1,
                       signature="True", docstring="The gadget theorem's lattice height bound."),
        ])
        canary = self._probe_index("canary", "cf-canary", "c" * 40,
                                   [("CfCanary.Basic", "CfCanary/Basic.lean")], [
            self._decl("CfCanary.hiddenSorry", "CfCanary.Basic", "CfCanary/Basic.lean", 3, 4,
                       status="statement_only", axioms=["sorryAx"]),
            self._decl("CfCanary.wantedTheorem", "CfCanary.Basic", "CfCanary/Basic.lean", 6, 6,
                       status="wanted", wrapper="CfCanary.TheoremWanted"),
            self._decl("CfCanary.propDef", "CfCanary.Basic", "CfCanary/Basic.lean", 8, 8,
                       kind="def", status="definition", signature="Prop"),
            self._decl("CfCanary.axiomTheorem", "CfCanary.Basic", "CfCanary/Basic.lean", 10, 10,
                       axioms=["CfCanary.canaryAxiom"]),
            self._decl("CfCanary.canaryAxiom", "CfCanary.Basic", "CfCanary/Basic.lean", 12, 12,
                       kind="axiom", status="axiom", axioms=["CfCanary.canaryAxiom"]),
        ])
        self.cf("corpus", "init", U, "--frozen", "2026-10-17")
        for lib, roots in (("alpha", "Alpha"), ("beta", "Beta")):
            self.cf("corpus", "add", U, "--library", lib, "--repo", f"https://example.org/{lib}",
                    "--commit", self.commits[lib], "--toolchain", TOOLCHAIN, "--roots", roots,
                    "--licence", "Apache-2.0")
        self.cf("corpus", "ingest", U, "--canary", "--from", str(canary))
        self.cf("corpus", "ingest", U, "--library", "alpha", "--from", str(alpha),
                "--checkout", str(alpha_dir))
        self.cf("corpus", "ingest", U, "--library", "beta", "--from", str(beta),
                "--checkout", str(beta_dir))
        self.cf("corpus", "build-db", U)
        paths = cf_main_paths(self.ws)
        check(paths.corpus_db(U).is_file(), "build-db wrote no corpus.sqlite")
        from corpus import query, universe as universe_mod
        uni = universe_mod.Universe.load(paths.universe_yaml(U))
        check(uni.corpus_db and uni.corpus_db.get("sha256"), "universe.yaml records no corpus sha")
        conn = query.open_db(paths.corpus_db(U))
        try:
            check(query.exists(conn, "alpha", alpha_commit, "Widget.exists_spanning_gadget"),
                  "the exact table lacks the widget lemma")
            tagged = query.tagged(conn, "wikidata", "Q99999001")
            check([t["declaration"] for t in tagged] == ["Widget.exists_spanning_gadget"],
                  f"the wikidata tag was not read from the checkout's span: {tagged}")
            sorried = query.declaration(conn, "alpha", "Widget.sorried")
            check(sorried["proof_status"] == "statement_only", f"sorried: {sorried}")
            spans = query.declaration(conn, "alpha", "Widget.Spans")
            check(spans["proof_status"] == "definition", f"Widget.Spans: {spans}")
        finally:
            conn.close()
        self.universe_sha = uni.sha256()

    # ------------------------------------------------------------- sourceset
    def pins(self, directory: Path) -> Path:
        """A producer's delivery: each page's response bytes and ``pins.jsonl``.

        The bytes are the recorded ``action=parse`` responses (each recording's
        query is checked to BE the fixed fetch of its revision, so the row's
        ``fetch`` claim is true), and ``layer_sha256`` is the sha of the text
        layer of those bytes as this skill's own renderer computes it -- what a
        producer whose layer conforms would record.
        """
        from sources import render_html, websource
        directory.mkdir(parents=True, exist_ok=True)
        rows = []
        for pageid, revision, meta in PAGES:
            recorded = self.read_json(WIKI_FIXTURES / f"parse-{revision}.json")
            check(recorded.get("query") == {**websource.PARSE_PARAMS, "oldid": str(revision)},
                  f"the recording of revision {revision} is not the fixed fetch")
            raw = json.dumps(recorded["json"], ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
            name = f"{pageid}-{revision}.json"
            (directory / name).write_bytes(raw)
            layer = render_html.render(render_html.canonical_body(raw)).layer
            rows.append({"pageid": pageid, "revision": revision, "bytes": name, **meta,
                         "fetch": websource.fetch_block(),
                         "layer_sha256": hashlib.sha256(layer.encode("utf-8")).hexdigest()})
        path = directory / "pins.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                                for r in rows), encoding="utf-8")
        return path

    def sourceset(self) -> None:
        self.cf("sourceset", "create", SS, "--family", "enwiki")
        pins = self.pins(self.made / "delivery")
        self.cf("sourceset", "adopt", SS, "--from", str(pins), "--consumer", CONSUMER)
        self.cf("sourceset", "render", SS)
        code, out = self.cf("sourceset", "status", SS, "--json", "--verify")
        status = json.loads(out)
        self.sourceset_status = status
        check(status["counts"]["adopted"] == status["counts"]["rendered"] == len(PAGES),
              f"the delivery was not adopted and rendered whole: {status['counts']}")
        paths = cf_main_paths(self.ws)
        from sources import websource
        from sources.sourceset import Sourceset
        manifest = Sourceset.open(paths, SS).latest()
        for entry in manifest:
            sid = entry["source_id"]
            check(entry["origin"] == "adopted" and entry["fetch"] == websource.fetch_block(),
                  f"{sid} is not an adopted fixed fetch: {entry}")
            check(entry["adopted_from"].get("layer_sha256") == entry["layer_sha256"],
                  f"{sid}: the rendered layer is not the producer's: {entry}")
        check(manifest.get(WIDGET)["assessment"] == {"class": "B", "importance": "high"},
              f"the widget's assessment was not normalised: {manifest.get(WIDGET)}")
        layer = paths.sourceset_source_dir(SS, WIDGET) / "layer.txt"
        check(layer.is_file(), f"render wrote no layer for {WIDGET}")
        self.layer_widget = layer.read_text(encoding="utf-8")
        check("spanning gadget" in self.layer_widget, "the widget layer lost its text")
        self._sourceset_refusals(pins)

    def _sourceset_refusals(self, pins: Path) -> None:
        """A delivery whose layer sha is not this renderer's layer of its bytes is not rendered."""
        rows = [json.loads(line) for line in pins.read_text(encoding="utf-8").splitlines()]
        rows[0]["layer_sha256"] = hashlib.sha256(b"a producer whose layer differs").hexdigest()
        wrong = pins.with_name("pins-wrong-layer.jsonl")
        wrong.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                         encoding="utf-8")
        self.cf("sourceset", "create", "wpm-parity", "--family", "enwiki")
        self.cf("sourceset", "adopt", "wpm-parity", "--from", str(wrong), "--consumer", CONSUMER)
        code, out = self.cf("sourceset", "render", "wpm-parity", ok=False)
        check(code == 1, f"render took a layer that is not the producer's (exit {code}):\n{out}")
        paths = cf_main_paths(self.ws)
        from sources.sourceset import Sourceset
        ss = Sourceset.open(paths, "wpm-parity")
        manifest = ss.latest()
        check(manifest.get(WIDGET)["layer_sha256"] is None
              and not (ss.source_dir(WIDGET) / "layer.txt").exists(),
              f"the refused {WIDGET} was rendered anyway: {manifest.get(WIDGET)}")
        check(all(e["layer_sha256"] for e in manifest if e["source_id"] != WIDGET),
              "a refused source stopped the others from rendering")
        render_state = ss.read_state()["commands"]["render"]
        check(render_state.get("not_the_producers_layer") == [WIDGET],
              f"the render state does not name the refusal: {render_state}")
        self.parity_refusal = render_state
        # the refusal stands past a render that does not visit it, and the
        # acceptance check names it
        others = [e["source_id"] for e in manifest if e["source_id"] != WIDGET]
        self.cf("sourceset", "render", "wpm-parity", "--source", others[0])
        code, out = self.cf("sourceset", "status", "wpm-parity", "--json", "--verify", ok=False)
        report = json.loads(out)
        check(code == 1 and report["producer_layer"]["refused"] == [WIDGET]
              and any(p.startswith(f"{WIDGET}: render refused it") for p in report["problems"]),
              f"status --verify does not name the standing refusal (exit {code}): {report}")
        self.parity_status = report

    # ------------------------------------------------------------------- L2
    def candidates(self) -> None:
        curated = self.made / "1000.yaml"
        curated.write_text("Q99999001:\n  title: Widget lemma\n"
                           "  decl: Widget.exists_spanning_gadget\n", encoding="utf-8")
        self.cf("locate", "candidates", "--sourceset", SS, "--universe", U,
                "--curated-1000", str(curated), "--curated-library", "alpha")
        paths = cf_main_paths(self.ws)
        doc = self.read_json(paths.candidates_json(SS, U, WIDGET))
        vias = sorted({c["via"] for c in doc.get("curated", [])})
        check("wikidata_tag" in vias and "mathlib_1000" in vias,
              f"the widget lemma's curated hits are {vias}")


    def usage_export(self, run_id: str, responses: int) -> Path:
        """A synthetic `cf cost --export` file for one run: what a host export brings home."""
        rows = [{"model": "claude-opus-5",
                 "usage": {"input_tokens": 1200, "output_tokens": 300,
                           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4000}}
                for _ in range(responses)]
        path = self.made / f"usage-{run_id}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path

    def harvest(self, run_id: str, out: Path) -> dict:
        self.cf("harvest", run_id, "--from", str(out),
                "--transcripts", str(self.usage_export(run_id, 4)))
        receipt = self.read_json(cf_main_paths(self.ws).receipt_json(run_id))
        check(receipt.get("complete") is True, f"{run_id} harvest incomplete: {receipt}")
        check((receipt.get("gate") or {}).get("ok") is True, f"the {run_id} harvest gate failed")
        check((receipt.get("cost") or {}).get("usd", 0) > 0, f"{run_id} was priced at nothing")
        return receipt

    # ------------------------------------------------------------------ L3a
    def _run_dir(self, run_id: str) -> Path:
        return cf_main_paths(self.ws).run_dir(run_id)

    def _manifests(self, run_id: str) -> dict[str, dict]:
        tasks = cf_main_paths(self.ws).tend_root(run_id) / "gate" / "tasks"
        return {p.stem: self.read_json(p) for p in sorted(tasks.glob("*.json"))}

    def _header(self, mode: str, run_id: str, task_id: str, doc: dict, universe=False) -> dict:
        head = {"schema": "cf-locate/1", "mode": mode, "run_id": run_id, "task_id": task_id,
                "source_id": doc["source_id"], "source_sha": doc["source_sha"],
                "layer_sha256": doc["layer_sha256"], "renderer": doc["renderer"]}
        if universe:
            head.update(universe=U, universe_sha256=self.universe_sha)
        return head

    def host_gate(self, run_id: str) -> Path:
        """The run's staged ``gate/``, copied outside the root as ``cf run`` stages it.

        A stand-in for ``<remote_root>/<run>-gate``: every gate and passage-id call
        below runs from this copy with ``python3 -I``, a bare environment and a
        scratch working directory, so a module the directory lacks fails here as it
        would on the host (field defect P2: a staged gate that could not import ran
        fine from ``scripts/gate/``, where its missing sibling sat beside it).
        """
        copy = self.made / "host" / f"{run_id}-gate"
        if not copy.is_dir():
            shutil.copytree(cf_main_paths(self.ws).tend_root(run_id) / "gate", copy)
        return copy

    def isolated(self, run_id: str, script: str, *argv: str,
                 cwd: Path | None = None) -> subprocess.CompletedProcess:
        """One staged tool of ``run_id``, run as the host runs it."""
        cwd = Path(cwd) if cwd is not None else self.made / "cwd"
        cwd.mkdir(parents=True, exist_ok=True)
        return subprocess.run([sys.executable, "-I", str(self.host_gate(run_id) / script),
                               *argv], capture_output=True, text=True, cwd=str(cwd),
                              env={"PATH": "/usr/bin:/bin", "HOME": str(cwd),
                                   "TEND_TASK_ID": ""})

    def gate(self, run_id: str, *documents: Path, changed: Path | None = None,
             task_id: str | None = None) -> tuple[int, str]:
        """The STAGED gate (this machine's paths: ``gate.local.json``), run as on the host."""
        argv = ["--gate-config", str(self._run_dir(run_id) / "gate.local.json")]
        if changed is not None:
            argv += ["--root", str(changed), "--changed-files", "--base", "main"]
            if task_id:
                argv += ["--task-id", task_id]
        for doc in documents:
            argv += ["--findings", str(doc)]
        proc = self.isolated(run_id, "verify_locate.py", *argv, cwd=changed)
        return proc.returncode, proc.stdout + proc.stderr

    @staticmethod
    def refused(code: int, text: str, what: str, rule: str | None = None) -> None:
        """A verdict, for the right reason: exit 1 with the list, never the gate breaking."""
        check(code == 1 and "VALIDATION FAILED" in text and "could not run" not in text,
              f"the gate did not refuse a {what} violation as a verdict (exit {code}):\n{text}")
        if rule:
            check(f"[{rule}]" in text, f"the {what} violation was refused, but not under "
                                       f"{rule}:\n{text}")

    def _layer_words(self, layer: str, start: int, n: int = 8) -> str:
        words, at = [], start
        for _ in range(n):
            end = layer.find(" ", at)
            if end < 0:
                words.append(layer[at:])
                break
            words.append(layer[at:end])
            at = end + 1
        return " ".join(words)

    #: how this run's worker labels each finder passage, by finder
    LABELS = {"theorem_box": ("theorem", "central", "formal_math"),
              # the widget lemma's lead bolds the name of the theorem it states
              "bold_definition": ("theorem", "subject", "formal_math"),
              "lead": ("theorem", "subject", "formal_math"),
              "model": ("other", "supporting", "formal_math")}

    def inventory(self) -> None:
        self.cf("plan-locate", "--preset", "locate-inventory", "--sourceset", SS,
                "--universe", U, "--run-id", "inv-1", "--max-cost", "50")
        paths = cf_main_paths(self.ws)
        tend = paths.tend_root("inv-1")
        out = self.made / "inv-1" / "findings"
        self.inventory_docs: dict[str, Path] = {}
        for task_id, manifest in self._manifests("inv-1").items():
            for d in manifest["documents"]:
                sid = d["source_id"]
                source_dir = paths.sourceset_source_dir(SS, sid)
                layer = (source_dir / "layer.txt").read_text(encoding="utf-8")
                passages, seen = [], set()
                # the finders' passages, then one "model" passage per math paragraph the
                # finders left uncovered: the coverage floor (gate rule 8) the prompt states
                proposals = [dict(f) for f in d.get("finders") or []]
                paragraphs = self.read_json(source_dir / "paragraphs.json")
                for para in paragraphs:
                    a, b = para["span"]
                    if para.get("math") and not any(f["span"][0] <= a < f["span"][1]
                                                    for f in proposals):
                        proposals.append({"finder": "model", "start": a, "span": [a, b],
                                          "section": para.get("section") or []})
                for f in sorted(proposals, key=lambda f: f["start"]):
                    if f["start"] in seen:
                        continue                  # one passage, two finders: one start
                    seen.add(f["start"])
                    kind, prominence, scope = self.LABELS[f["finder"]]
                    if passages and prominence == "subject":
                        prominence = "supporting"
                    exact = self._layer_words(layer, f["start"])
                    prefix = layer[max(0, f["start"] - 24):f["start"]]
                    passages.append({"item": "", "head_words": 0, "span": None,
                                     "quote": {"exact": exact, "prefix": prefix, "suffix": ""},
                                     "section": f.get("section") or [], "fragment": None,
                                     "passage_kind": kind, "prominence": prominence,
                                     "scope": scope, "concept": None, "finder": f["finder"],
                                     "statement": f"synthetic restatement of {exact[:30]}"})
                doc = self._header("locate-inventory", "inv-1", task_id, d)
                doc.update(passages=passages, skips=[], notes="")
                path = self.write_json(out / task_id / f"{sid}.json", doc)
                proc = self.isolated("inv-1", "passage_ids.py", "--source-dir", str(source_dir),
                                     "--document", str(path), "--write")
                check(proc.returncode == 0, f"passage_ids.py on {sid}: {proc.stderr}")
                self.inventory_docs[sid] = path
        for sid, path in self.inventory_docs.items():
            code, text = self.gate("inv-1", path)
            check(code == 0, f"the staged gate refused the good inventory of {sid}:\n{text}")
        self._inventory_refusals(paths)
        self.harvest("inv-1", out)

    def _inventory_refusals(self, paths) -> None:
        """Bad inventory documents the staged gate must refuse, each for its own rule."""
        good = self.read_json(self.inventory_docs[WIDGET])
        bad_dir = self.made / "inv-bad"
        cases = {}
        moved = json.loads(json.dumps(good))
        moved["passages"][0]["quote"]["exact"] = "a quote that is not on the layer at all"
        cases["rule 2"] = moved
        hexed = json.loads(json.dumps(good))
        hexed["passages"][0]["item"] = "In-text #00000000"
        cases["rule 2 "] = hexed
        smuggled = json.loads(json.dumps(good))
        smuggled["passages"][0]["proof_status"] = "proved"
        cases["rule 3"] = smuggled             # a worker never writes proof_status (rule 3)
        dropped = json.loads(json.dumps(good))
        dropped["passages"] = [p for p in dropped["passages"] if p["finder"] != "theorem_box"]
        cases["rule 8"] = dropped
        for rule, doc in cases.items():
            # under the task's own directory and the source's own name: a document
            # anywhere else is refused under rule 7 before the rule it breaks is read
            path = self.write_json(bad_dir / f"{len(rule)}-{rule.strip().replace(' ', '-')}"
                                   / good["task_id"] / f"{WIDGET}.json", doc)
            code, text = self.gate("inv-1", path)
            self.refused(code, text, rule.strip(), rule.strip())

    # ------------------------------------------------------------------ L3b
    @staticmethod
    def _location(lib: str, decl: str, relation: str = "exactMatch", rank: int = 1,
                  note: str = "") -> dict:
        return {"library": lib, "declaration": decl, "relation": relation, "rank": rank,
                "confidence": "high", "note": note,
                "witness": "the signature states the passage (synthetic)"}

    def _answer(self, sid: str, passage: dict, curated: list[dict]) -> dict:
        """A worker's answer for one passage: the widget lemma's are located, the rest absent."""
        receipt = {"queries": ["widget spanning gadget", "finite widget gadget", "Widget.exists"],
                   "candidates": [{"library": c["library"], "declaration": c["declaration"],
                                   "verdict": "accepted" if passage.get("prominence") == "subject"
                                   else "rejected",
                                   "reason": "" if passage.get("prominence") == "subject"
                                   else "states the subject passage, not this one"}
                                  for c in curated]}
        if sid == WIDGET and passage.get("prominence") == "subject":
            return {"item": passage["item"], "outcome": "located", "receipt": receipt,
                    "locations": [self._location("alpha", "Widget.exists_spanning_gadget")]}
        if sid == WIDGET and passage.get("passage_kind") == "theorem":
            return {"item": passage["item"], "outcome": "located", "receipt": receipt,
                    "locations": [self._location("alpha", "Widget.card_dvd", "hasPart")]}
        return {"item": passage["item"], "outcome": "absent", "locations": [], "receipt": receipt}

    def locate(self) -> None:
        self.cf("plan-locate", "--preset", "locate", "--sourceset", SS, "--universe", U,
                "--inventory-run", "inv-1", "--run-id", "loc-1", "--max-cost", "50")
        out = self.made / "loc-1" / "findings"
        self.locate_docs: dict[tuple[str, str], Path] = {}
        manifests = self._manifests("loc-1")
        check(manifests, "the locate plan wrote no task manifest")
        for task_id, manifest in manifests.items():
            for d in manifest["documents"]:
                sid = d["source_id"]
                check(len(d["passages"]) <= 8, f"{task_id} carries {len(d['passages'])} passages")
                curated = d.get("curated") or []
                doc = self._header("locate", "loc-1", task_id, d, universe=True)
                doc["passages"] = [self._answer(sid, p, curated) for p in d["passages"]]
                path = self.write_json(out / task_id / f"{sid}.json", doc)
                self.locate_docs[(task_id, sid)] = path
                code, text = self.gate("loc-1", path)
                check(code == 0, f"the staged gate refused the good locate document "
                                 f"{task_id}/{sid}:\n{text}")
        self._locate_refusals()
        self._ownership()
        receipt = self.harvest("loc-1", out)
        check((receipt.get("locate") or {}).get("documents") == len(self.locate_docs),
              f"the receipt counts {receipt.get('locate')} locate documents")

    def _widget_locate(self) -> tuple[str, Path, dict]:
        for (task_id, sid), path in sorted(self.locate_docs.items()):
            doc = self.read_json(path)
            if sid == WIDGET and any(p["outcome"] == "located" for p in doc["passages"]):
                return task_id, path, doc
        raise StageError("no located widget-lemma document to mutate")

    def _locate_refusals(self) -> None:
        """Bad locate documents, each refused by the staged gate for its own rule."""
        task_id, _path, good = self._widget_locate()
        located = next(i for i, p in enumerate(good["passages"]) if p["outcome"] == "located")
        def mutate(fn):
            doc = json.loads(json.dumps(good))
            fn(doc)
            return doc
        def phantom(doc):
            doc["passages"][located]["locations"][0]["declaration"] = "Widget.no_such_lemma"
        def status(doc):
            doc["passages"][located]["locations"][0]["proof_status"] = "proved"
        def commit(doc):
            doc["passages"][located]["locations"][0]["commit"] = "f" * 40
        def few_queries(doc):
            p = doc["passages"][located]
            p.update(outcome="absent", locations=[])
            p["receipt"]["queries"] = ["widget", "Widget "]
        def too_many(doc):
            doc["passages"][located]["locations"] = [
                self._location("alpha", "Widget.exists_spanning_gadget", "relatedMatch", r)
                for r in range(1, 7)]
        def duplicate_rank(doc):
            doc["passages"][located]["locations"].append(
                self._location("alpha", "Widget.card_dvd", "hasPart", 1))
        def curated_ignored(doc):
            for p in doc["passages"]:
                p.update(outcome="absent", locations=[])
                p["receipt"]["candidates"] = []
        def close_without_note(doc):
            doc["passages"][located]["locations"][0]["relation"] = "closeMatch"
        cases = {"rule 3 phantom": phantom, "rule 3 proof_status": status,
                 "rule 3 commit": commit, "rule 5": few_queries, "rule 6 count": too_many,
                 "rule 6 rank": duplicate_rank, "rule 4": curated_ignored,
                 "closeMatch note": close_without_note}
        for name, fn in cases.items():
            path = self.write_json(self.made / "loc-bad" / name.replace(" ", "-")
                                   / task_id / f"{WIDGET}.json", mutate(fn))
            code, text = self.gate("loc-1", path)
            self.refused(code, text, name,
                         " ".join(name.split()[:2]) if name.startswith("rule ") else None)

    def _ownership(self) -> None:
        """Rule 7 through a real change set: a tend-like worktree with main and a task branch."""
        paths = cf_main_paths(self.ws)
        task_id, path, doc = self._widget_locate()
        repo = self.made / "worktree"
        (repo / "tasks").mkdir(parents=True)
        for task in sorted(paths.tasks_dir("loc-1").glob("*.yaml")):
            shutil.copy2(task, repo / "tasks" / task.name)
        self.git(repo.parent, "init", "-q", "-b", "main", str(repo))
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "tasks for loc-1")
        self.git(repo, "checkout", "-q", "-b", task_id)
        own = repo / "findings" / task_id / f"{WIDGET}.json"
        own.parent.mkdir(parents=True)
        shutil.copy2(path, own)
        task_file = repo / "tasks" / f"{task_id}.yaml"
        task_file.write_text(task_file.read_text(encoding="utf-8").replace(
            "status: open", "status: complete"), encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "the worker's contribution")
        code, text = self.gate("loc-1", changed=repo, task_id=task_id)
        check(code == 0, f"the gate refused a task's own change set:\n{text}")
        check(f"ownership checked against task {task_id}" in text.strip().splitlines()[-1],
              f"the gate's last line does not name the ownership rule: {text}")
        others = [t for t in self._manifests("loc-1") if t != task_id]
        if others:
            stray = repo / "findings" / others[0] / f"{WIDGET}.json"
            stray.parent.mkdir(parents=True)
            shutil.copy2(path, stray)
            self.git(repo, "add", "-A")
            self.git(repo, "commit", "-qm", "a neighbour's directory")
            code, text = self.gate("loc-1", changed=repo, task_id=task_id)
            self.refused(code, text, "another task's findings", "rule 7")

    # ------------------------------------------------------------------- L5
    def challenge(self) -> None:
        self.cf("plan-locate", "--preset", "locate-challenge", "--sourceset", SS, "--universe", U,
                "--challenge-of", "loc-1", "--run-id", "chl-1", "--max-cost", "50")
        out = self.made / "chl-1" / "findings"
        for task_id, manifest in self._manifests("chl-1").items():
            for d in manifest["documents"]:
                doc = self._header("locate-challenge", "chl-1", task_id, d, universe=True)
                verdicts = []
                for rec in d["records"]:
                    v = {"item": rec["item"], "target": rec["target"],
                         "witness": "re-read the signature at the pin (synthetic)"}
                    if rec["target"] == "location":
                        v.update(location_key=rec["location_key"], action="confirm")
                    else:
                        v["action"] = "uphold"
                    verdicts.append(v)
                doc["verdicts"] = verdicts
                path = self.write_json(out / task_id / f"{d['source_id']}.json", doc)
                code, text = self.gate("chl-1", path)
                check(code == 0, f"the gate refused a good challenge document:\n{text}")
        self.harvest("chl-1", out)

    # ------------------------------------------------------------------- L6
    def export(self) -> None:
        paths = cf_main_paths(self.ws)
        self.cf("tally", "--locate", "--run", "loc-1", "--challenge-run", "chl-1")
        rows = [json.loads(line) for line in
                paths.locate_outcomes_jsonl("loc-1").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        subject = [r for r in rows if r["source_id"] == WIDGET and r.get("prominence") == "subject"]
        check(subject and subject[0]["status"] == "formalized",
              f"the widget lemma's subject passage is not formalized: {subject}")
        check(all(r.get("challenge") is not None for r in rows if r.get("outcome")),
              "a passage still owes its challenge after the challenge run")
        theorem = [r for r in rows if r["source_id"] == WIDGET and r.get("passage_kind") == "theorem"
                   and r.get("prominence") == "central"]
        check(theorem and theorem[0]["status"] == "partial", f"the hasPart theorem: {theorem}")
        self.cf("export", "literature-links", "--run", "loc-1", "--challenge-run", "chl-1",
                "--set-id", "set-1")
        set_dir = paths.export_dir("set-1")
        from locate import export as export_mod
        check(export_mod.verify_seal(set_dir) == [], "the sealed set does not verify")
        from cflib import vendored  # noqa: F401
        from literature_links import companion as ll
        records = [json.loads(line) for f in sorted((set_dir / "records").glob("*.jsonl"))
                   for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        check(records, "the export wrote no record")
        for record in records:
            check(ll.validate(record) == [], f"an exported record fails the checker: {record}")
            quote = record["subject"][0].get("location", {}).get("quote") or {}
            check(len((quote.get("exact") or "").split()) <= 12, "an exported quote is over 12 words")
        statuses = {r.get("status") for r in records}
        check("formalized" in statuses, f"no formalized record exported: {statuses}")
        # idempotent: the same inputs seal the same bytes
        before = (set_dir / "SEAL.json").read_bytes()
        self.cf("export", "literature-links", "--run", "loc-1", "--challenge-run", "chl-1",
                "--set-id", "set-1")
        check((set_dir / "SEAL.json").read_bytes() == before, "a re-export changed the seal")

    # ---------------------------------------------------------------- bench
    def _label_result(self, mode: str, make) -> Path:
        """Emit a labeler mode's args and transcribe a synthetic labeler's result for it."""
        from bench import gold as gold_mod
        from bench.layout import BenchLayout
        self.cf("bench", "anchor", "b0", "--emit-args", mode)
        layout = BenchLayout.of(cf_main_paths(self.ws), "b0")
        args = self.read_json(layout.label_args(mode))
        labels = [label for label in (make(task) for task in args["tasks"]) if label]
        result = self.write_json(self.made / f"label-{mode}.json", {
            "schema": "cf-locate-label-v1", "mode": args["mode"], "bench": args["bench"],
            "args_digest": args["digest"], "labeler": {"version": gold_mod.LABELER_VERSION},
            "labels": labels})
        self.cf("bench", "anchor", "b0", "--transcribe", str(result))
        return result

    def bench(self) -> None:
        paths = cf_main_paths(self.ws)
        frame = self.made / "frame.txt"
        manifest = self.read_json(paths.sourceset_source_dir(SS, WIDGET) / "source.json")
        frame.write_text("".join(f"{sid}\n" for sid in sorted(
            p.name for p in paths.sourceset_sources_dir(SS).iterdir() if p.is_dir())),
            encoding="utf-8")
        self.cf("bench", "build", "b0", "--sourceset", SS, "--frame", str(frame),
                "--curated-qids", str(self.made / "1000.yaml"), "--uniform", "3",
                "--curated", "1", "--spans-per-article", "2", "--dev", "0", "--eval", "1",
                "--seed", "11")
        self.cf("bench", "anchor", "b0", "--universe", U, "--library", "alpha",
                "--curated-1000", str(self.made / "1000.yaml"))
        layer = self.layer_widget
        subject = next(p for p in self.read_json(self.inventory_docs[WIDGET])["passages"]
                       if p["prominence"] == "subject")
        quote = self._layer_words(layer, subject["span"][0])
        self._label_result("anchor", lambda task: {
            "task_id": task["task_id"], "quote": {"exact": quote}, "relation": "exactMatch",
            "passage_kind": "theorem", "evidence": "the signature states the lemma (synthetic)"})
        self._label_result("label", lambda task: {
            "task_id": task["task_id"], "scope": "formal_math", "passage_kind": "other",
            "declarations": [], "evidence": "nothing at the pin states it (synthetic)",
            "hunters": []})
        self.cf("bench", "freeze", "b0")
        code, out = self.cf("bench", "score", "b0", "--run", "loc-1", "--split", "eval",
                            "--no-canary-check", "--bootstrap", "200", "--json")
        score = json.loads(out[out.index("{"):])
        self.score = score
        check(score.get("measurement_id"), "the score document has no measurement_id")
        g1 = (score.get("tiers") or {}).get("G1") or {}
        check(g1, f"the score has no G1 tier: {sorted(score.get('tiers') or {})}")
        check(g1.get("joined") == g1.get("gold") == 1,
              f"the anchored widget lemma did not join its run passage: {g1}")
        fnf = (g1.get("metrics") or {}).get("fnf_rate") or {}
        check(fnf and fnf.get("value") == 0, f"the widget lemma is formalized, so G1's "
                                             f"false-not-formalized rate is 0: {g1}")
        check(set(score.get("pins") or {}) >= {"sourceset_manifest_sha256", "universe_sha256"}
              or "pins" in score, "the score pins nothing")

def cf_main_paths(ws: Path):
    from cflib.paths import WorkspacePaths
    return WorkspacePaths(Path(ws))


STAGES = ("workspace", "universe", "sourceset", "candidates", "inventory", "locate",
          "challenge", "export", "bench")


def run(root: Path, stages=STAGES) -> Pipeline:
    pipe = Pipeline(root)
    for name in stages:
        getattr(pipe, name)()
    return pipe


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keep", help="build the campaign here and keep it (default: a temp dir)")
    args = ap.parse_args(argv)
    root = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="cf-locate-e2e-"))
    try:
        pipe = run(root)
    except StageError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    print("\n".join(pipe.log))
    print("OK: every stage produced what the next one reads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
