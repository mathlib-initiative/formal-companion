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

    Every run is planned under the statement cut (docs/proposals/statement-cut.md,
    from its flip on, the continued cut of inv-pilot-1's fixes from theirs, and the
    wide cut of inv-pilot-2's repairs from the flip of the wide bound, and the smoke-4
    audit's calls from theirs: judgment rules 4, span-end rule 3, statement-cut rule 3,
    and a locate run's status rule 3), and its documents take its shapes:

    L3a        cf plan-locate --preset locate-inventory; a worker's documents cut
               one passage per statement (a compound statement's conjuncts and
               shape), filled by the staged passage_ids.py; the STAGED gate (out of
               tree, from the tend root) over good and bad documents; cf harvest
    L3b        cf plan-locate --preset locate over the frozen inventory; documents
               citing the inventory's clause ids (no claim list), strong locations
               with their tests, a bundle on the compound statement, absences with
               their nearest special case; the staged gate over good and bad
               documents; cf harvest
    L5         cf plan-locate --preset locate-challenge; verdicts (the bundle's
               part relations, a strong verdict's tests); gate; harvest
    cut        the statement cut's rules over those runs' documents: bad ones refused
               under rule 10, rule 2's end rule 3 and rule 9's rules 3, by the run's
               own staged gate or a copy of it where a case needs another version,
               database or record
    L6         cf tally --locate (status rule 3: the bundle's weakest part's
               relation, a broadMatch formalizing whatever its declaration),
               cf export literature-links (cf-locate-recordset/2, its seal naming
               status rule 3, sealed, checked)
    bench      cf bench build / anchor / freeze / score over the same runs
    A/A        loc-2 planned exactly as loc-1, run, challenged as chl-1 was,
               tallied and scored; cf bench compare --aa answers "aa ok", with
               agreement shown on every row (references/locate-bench.md
               section 9, rule 5)
    A/A refusal  loc-3 planned as loc-1 but challenged at another
               --strong-sample-rate: cf bench compare --aa refuses it, naming
               the planning key
    carry      inv-2, a re-cut of the same revisions; cf locate carry from inv-1's
               cut to it (a split, an end moved, the rest unchanged; --rows carried
               by their passages' fates) and cf bench carry over the frozen gold
    audit      inv-aud, an audited inventory (cf plan-locate --audit-rounds 2, audit
               rule 2): every turn through the planned wrappers, bridge-agentbox.sh and
               the turn machine, the agents a fake agentbox; the widget page's worker
               skipped its first corollary, which states a result; round 1 flags a
               passage and the skip, the skeptic upholds the skip with evidence inside
               it, and the merge rejects; the fake worker applies the operations --
               a set, and an unskip with an add for the statement -- with the staged edit
               tool, round 2 checks them and approves; the tend root's audit/ and
               usage/ landed as a remote harvest fetches them, cf harvest's receipt
               carries the audit block with its skips before and after
               (references/locate-documents.md sections 13.10 and 13.11)

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
import sqlite3
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
            # an instance argument among its binders: a location on it says how it is
            # instantiated at the passage's objects (the judgment rules, FM6)
            self._decl("Widget.card_dvd", "Alpha.Widget", "Alpha/Widget.lean", 12, 12,
                       signature="∀ {W : Type} [inst : Fintype W] (w : W), True"),
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
        fine from ``scripts/gate/``, where its missing sibling sat beside it).  The
        "host" is this machine, so its ``gate.json`` is the run's ``gate.local.json``,
        and the gate runs with the config beside it, the only one a gate directory
        accepts.
        """
        copy = self.made / "host" / f"{run_id}-gate"
        if not copy.is_dir():
            shutil.copytree(cf_main_paths(self.ws).tend_root(run_id) / "gate", copy)
            shutil.copy2(self._run_dir(run_id) / "gate.local.json", copy / "gate.json")
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
        argv = ["--gate-config", str(self.host_gate(run_id) / "gate.json")]
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

    #: the reason this run's worker gives the skip of a paragraph the prose floor holds
    #: (gate rule 8 under ``prose_floor_rule`` 1) and it locates nothing in
    PROSE_SKIP_REASON = "a paragraph without a formula: nothing to locate"

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
        local = self.read_json(self._run_dir("inv-1") / "gate.local.json")
        check({k: local.get(k) for k in self.CUT_KEYS} == self.CUT_KEYS,
              f"inv-1 was not planned under the statement cut: its gate.local.json names "
              f"{ {k: local.get(k) for k in self.CUT_KEYS} }")
        out = self.made / "inv-1" / "findings"
        self.inventory_docs = self._inventory_documents("inv-1", out)
        for sid, path in self.inventory_docs.items():
            code, text = self.gate("inv-1", path)
            check(code == 0, f"the staged gate refused the good inventory of {sid}:\n{text}")
        self._inventory_refusals(paths)
        self.harvest("inv-1", out)
        # the receipt's cut block is statement-cut rule 3's: the subject, continuation and
        # connective counts beside the first version's, and the wide bound's, one subject
        # on every page; the recorded pages hold no footnote mark, no dd and no paragraph
        # opening with a formula, so nothing runs on past the loose bound
        from harvest import gate_corpus

        cut = self.read_json(paths.receipt_json("inv-1"))["gate"]["cut"]
        check(set(gate_corpus.CUT_COUNTS_3) <= set(cut) and "by_connective" in cut,
              f"inv-1's receipt does not count statement-cut rule 3's measures: {sorted(cut)}")
        check((cut["widened"], cut["wide_starts"], cut["wide_unheld"]) == (0, 0, 0),
              f"inv-1's receipt counts what the wide bound alone reaches: "
              f"{ {k: cut[k] for k in ('widened', 'wide_starts', 'wide_unheld')} }")
        check({sid: row.get("subjects") for sid, row in cut["by_source"].items()}
              == {sid: 1 for sid in self.inventory_docs},
              f"inv-1's receipt does not count one subject per page: {cut['by_source']}")

    def _inventory_documents(self, run_id: str, out: Path, *,
                             more_starts: dict[str, list[tuple[int, str]]] | None = None,
                             untils: dict[str, dict[int, str]] | None = None
                             ) -> dict[str, Path]:
        """An inventory worker's documents for ``run_id``, cut one passage per statement.

        The finders' passages, then one "model" passage per math paragraph the finders
        left uncovered (the coverage floor, gate rule 8) and one skip per other paragraph of
        the body of at least ``PROSE_FLOOR_MIN_WORDS`` words they left uncovered (rule 8's
        prose floor, which the plan's gate.json names), and a re-cut's ``more_starts``
        (``{source: [(start, scope)]}``) and ``untils`` (``{source: {start: until}}``).
        Each quote stops before the passage's end, since under the span-end rule 3 (as
        under 2) a passage ends at the next passage's start, or its ``until``, and never
        before its quote's end (the recorded pages hold no sentence that continues after
        its display, so rule 3's ends are rule 2's here); the widget theorem box is a compound statement (its conjuncts, shape
        ``conclusions``), and a sentence that only sets up its objects ("Let ...") states
        nothing and is scoped ``expository``, as the cut rules say
        (docs/proposals/statement-cut.md section 3.4).  ``passage_ids.py`` places and
        ends every passage, by the end rule of the gate.json beside it.
        """
        from cflib import vocab
        from gate import locate_text as lt

        paths = cf_main_paths(self.ws)
        docs: dict[str, Path] = {}
        for task_id, manifest in self._manifests(run_id).items():
            for d in manifest["documents"]:
                sid = d["source_id"]
                source_dir = paths.sourceset_source_dir(SS, sid)
                layer = (source_dir / "layer.txt").read_text(encoding="utf-8")
                paragraphs = self.read_json(source_dir / "paragraphs.json")
                formulas = self.read_json(source_dir / "formulas.json")
                proposals = [dict(f) for f in d.get("finders") or []]
                skips = []
                for para in paragraphs:
                    a, b = para["span"]
                    if any(f["span"][0] <= a < f["span"][1] for f in proposals):
                        continue
                    section = para.get("section") or []
                    if para.get("math"):
                        proposals.append({"finder": "model", "start": a, "span": [a, b],
                                          "section": section})
                    elif (len(layer[a:b].split()) >= vocab.PROSE_FLOOR_MIN_WORDS
                          and not (section and str(section[0]).strip().casefold()
                                   in vocab.AUDIT_BACK_MATTER)):
                        # the prose floor: a paragraph of the body this worker locates
                        # nothing in is left out with a skip and its reason, never
                        # silently (a skip, not an expository passage, so the passages
                        # and their ids, which the later stages pin, are the ones every
                        # earlier plan made)
                        skips.append({"span": [a, b], "reason": self.PROSE_SKIP_REASON})
                for start, scope in (more_starts or {}).get(sid, []):
                    proposals.append({"finder": "model", "start": start, "scope": scope,
                                      "span": [start, start], "section": []})
                until_at = (untils or {}).get(sid, {})
                chosen, seen = [], set()
                for f in sorted(proposals, key=lambda f: f["start"]):
                    if f["start"] not in seen:          # one passage, two finders: one start
                        seen.add(f["start"])
                        chosen.append(f)
                starts = [f["start"] for f in chosen]
                passages = []
                for n, f in enumerate(chosen):
                    kind, prominence, scope = self.LABELS[f["finder"]]
                    scope = f.get("scope") or scope
                    if passages and prominence == "subject":
                        prominence = "supporting"
                    start = f["start"]
                    until = until_at.get(start)
                    end = lt.passage_end(layer, paragraphs, start, "", until,
                                         rule=vocab.SPAN_END_RULE,
                                         next_start=starts[n + 1] if n + 1 < len(starts)
                                         else None, formulas=formulas)
                    exact = self._layer_words(layer, start)
                    if len(exact) > end - start:
                        exact = layer[start:end]
                    passage = {"item": "", "head_words": 0, "span": None,
                               "quote": {"exact": exact,
                                         "prefix": layer[max(0, start - 24):start],
                                         "suffix": ""},
                               "section": f.get("section") or [], "fragment": None,
                               "passage_kind": kind, "prominence": prominence,
                               "scope": scope, "concept": None, "finder": f["finder"],
                               "statement": f"synthetic restatement of {exact[:30]}"}
                    if until is not None:
                        passage["until"] = until
                    if f["finder"] == "theorem_box":
                        passage.update(conjuncts=json.loads(json.dumps(self.BOX_CONJUNCTS)),
                                       shape="conclusions")
                    elif kind == "other" and layer[start:end].startswith("Let "):
                        passage["scope"] = "expository"
                    passages.append(passage)
                doc = self._header("locate-inventory", run_id, task_id, d)
                doc.update(passages=passages, skips=skips, notes="")
                path = self.write_json(out / task_id / f"{sid}.json", doc)
                proc = self.isolated(run_id, "passage_ids.py", "--source-dir", str(source_dir),
                                     "--document", str(path), "--write")
                check(proc.returncode == 0, f"passage_ids.py on {sid}: {proc.stderr}")
                docs[sid] = path
        return docs

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
        # the subject passage alone, and one skip over it and everything else
        skipped = json.loads(json.dumps(good))
        skipped["passages"] = [p for p in skipped["passages"] if p["prominence"] == "subject"]
        skipped["skips"] = [{"span": [0, max(p["span"][1] for p in good["passages"])],
                             "reason": "x"}]
        cases["rule 8 "] = skipped
        for rule, doc in cases.items():
            # under the task's own directory and the source's own name: a document
            # anywhere else is refused under rule 7 before the rule it breaks is read
            path = self.write_json(bad_dir / f"{len(rule)}-{rule.strip().replace(' ', '-')}"
                                   / good["task_id"] / f"{WIDGET}.json", doc)
            code, text = self.gate("inv-1", path)
            self.refused(code, text, rule.strip(), rule.strip())
        # A run planned now names the span-end rule, so its staged gate holds an
        # inventory passage's end to the one passage_ids.py wrote (rule 2); a
        # gate.json without the key -- a run planned before it -- would not.
        from cflib import vocab

        local = self.read_json(self._run_dir("inv-1") / "gate.local.json")
        check(local.get("span_end_rule") == vocab.SPAN_END_RULE,
              f"inv-1's gate.local.json names span_end_rule {local.get('span_end_rule')!r}, "
              f"not {vocab.SPAN_END_RULE}")
        ended = json.loads(json.dumps(good))
        ended["passages"][0]["span"][1] += 1     # an end moved by hand
        path = self.write_json(bad_dir / "span-end" / good["task_id"] / f"{WIDGET}.json", ended)
        code, text = self.gate("inv-1", path)
        self.refused(code, text, "span end", "rule 2")
        check("passage_ids.py ends it" in text,
              f"the moved end was refused, but not by the span-end rule:\n{text}")

    # ------------------------------------------------------------------ L3b
    #: the version keys of a run planned after the flip of the smoke-4 audit's calls
    #: (references/vocabulary.md section 1.11), which every run here is: their judgment
    #: rules 4 (the statement cut's 3 and the calls' checks), the continued cut's end and
    #: the wide cut's rule 10
    CUT_KEYS = {"judgment_rules": 4, "span_end_rule": 3, "statement_cut_rule": 3}
    #: the widget theorem's conjuncts, as the cut's inventory writes them (synthetic)
    BOX_CONJUNCTS = [{"id": "c1", "text": "each finite widget has a spanning gadget"},
                     {"id": "c2", "text": "that gadget's size divides the order"}]
    #: a strong location's two instantiation tests (fix 2a), and an absence's nearest
    #: special case (fix 5b), synthetic
    TESTS = {"r2": "the passage is the declaration read at its widget",
             "r3": "a finite widget meets every hypothesis the declaration has"}
    SPECIAL = {"reason": "no library states it for a special widget either"}

    def _location(self, lib: str, decl: str, relation: str = "exactMatch", rank: int = 1,
                  note: str = "", clauses=("c1",), *, conjunct_relation: str | None = None,
                  step: str | None = None) -> dict:
        """One location under judgment rules 3: the clause ids are the manifest's (the
        inventory's conjunct ids, else ``c1``); a strong one, or a part whose
        ``conjunct_relation`` is strong, writes its ``instantiation`` and both ``tests``."""
        loc = {"library": lib, "declaration": decl, "relation": relation, "rank": rank,
               "confidence": "high", "note": note,
               "witness": "the signature states the passage (synthetic)"}
        if relation != "relatedMatch":
            loc["clauses"] = list(clauses)
        if conjunct_relation is not None:
            loc["conjunct_relation"] = conjunct_relation
        if step is not None:
            loc["step"] = step
        if relation in ("exactMatch", "closeMatch", "broadMatch") or conjunct_relation in (
                "exactMatch", "closeMatch", "broadMatch"):
            loc.update(instantiation="W is the passage's finite widget, Fintype by finiteness",
                       tests=dict(self.TESTS))
        return loc

    def _answer(self, sid: str, passage: dict, curated: list[dict]) -> dict:
        """A worker's answer for one passage under judgment rules 3: the widget lemma's
        are located, the rest absent.  No document writes a claim list: the clause ids
        are the inventory's conjunct ids (the widget theorem box: ``c1``, ``c2``), or
        ``c1`` for a simple statement."""
        receipt = {"queries": ["widget spanning gadget", "finite widget gadget", "Widget.exists"],
                   "candidates": [{"library": c["library"], "declaration": c["declaration"],
                                   "verdict": "accepted" if passage.get("prominence") == "subject"
                                   else "rejected",
                                   "reason": "" if passage.get("prominence") == "subject"
                                   else "states the subject passage, not this one"}
                                  for c in curated]}
        ids = [c["id"] for c in passage.get("conjuncts") or []] or ["c1"]
        if sid == WIDGET and passage.get("prominence") == "subject":
            return {"item": passage["item"], "outcome": "located", "receipt": receipt,
                    "locations": [self._location("alpha", "Widget.exists_spanning_gadget")]}
        if sid == WIDGET and passage.get("passage_kind") == "theorem":
            # a compound statement (the cut): one part per conjunct, claimed together as a
            # bundle (J3), whose relation is its weakest part's (status rule 2)
            check(ids == ["c1", "c2"], f"the widget theorem's manifest passage carries "
                                       f"no conjuncts: {passage}")
            size = self._location("alpha", "Widget.card_dvd", "hasPart", 1, clauses=("c2",),
                                  conjunct_relation="exactMatch",
                                  step="the divisibility of the gadget's size by the order")
            exists = self._location("alpha", "Widget.exists_spanning_gadget", "hasPart", 2,
                                    clauses=("c1",), conjunct_relation="broadMatch",
                                    step="a spanning gadget for every widget, finite or not")
            return {"item": passage["item"], "outcome": "located", "receipt": receipt,
                    "locations": [size, exists],
                    "coverage": {"complete": True, "parts": [1, 2],
                                 "note": "existence for every widget, the size bound exactly"}}
        if sid == WIDGET and passage.get("passage_kind") == "other" and not self.proof_part:
            # the widget's display, a step of the theorem's proof: a hasPart of a simple
            # statement names c1 and carries no conjunct relation (the judgment rules, FM3)
            self.proof_part = passage["item"]
            step = self._location("alpha", "Widget.card_dvd", "hasPart",
                                  step="the displayed divisibility of the gadget's size")
            step["instantiation"] = "W is the display's widget, a Fintype"
            return {"item": passage["item"], "outcome": "located", "receipt": receipt,
                    "locations": [step]}
        # an absence searched for every clause, each query tagged with the one it targets,
        # and the nearest special case it found none of (fix 5b); a worked example's also
        # for its general theorem (the judgment rules, FM5)
        receipt["query_clauses"] = {q: [ids[n % len(ids)]]
                                    for n, q in enumerate(receipt["queries"])}
        if passage.get("passage_kind") == "example":
            receipt["query_clauses"][receipt["queries"][-1]] = [ids[0], "general"]
        receipt["special_case"] = dict(self.SPECIAL)
        return {"item": passage["item"], "outcome": "absent", "locations": [], "receipt": receipt}

    #: how every locate run of the campaign is planned (the A/A stage plans loc-2 so too)
    LOCATE_PLAN = ("--preset", "locate", "--sourceset", SS, "--universe", U,
                   "--inventory-run", "inv-1", "--max-cost", "50")

    def _locate_run(self, run_id: str) -> tuple[Path, dict[tuple[str, str], Path]]:
        """Plan one locate run as :data:`LOCATE_PLAN` says, answer and gate every document."""
        self.proof_part = None
        self.cf("plan-locate", *self.LOCATE_PLAN, "--run-id", run_id)
        out = self.made / run_id / "findings"
        docs: dict[tuple[str, str], Path] = {}
        manifests = self._manifests(run_id)
        check(manifests, f"the {run_id} plan wrote no task manifest")
        for task_id, manifest in manifests.items():
            for d in manifest["documents"]:
                sid = d["source_id"]
                check(len(d["passages"]) <= 8, f"{task_id} carries {len(d['passages'])} passages")
                curated = d.get("curated") or []
                doc = self._header("locate", run_id, task_id, d, universe=True)
                doc["passages"] = [self._answer(sid, p, curated) for p in d["passages"]]
                path = self.write_json(out / task_id / f"{sid}.json", doc)
                docs[(task_id, sid)] = path
                code, text = self.gate(run_id, path)
                check(code == 0, f"the staged gate refused the good locate document "
                                 f"{task_id}/{sid}:\n{text}")
        return out, docs

    def locate(self) -> None:
        out, self.locate_docs = self._locate_run("loc-1")
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
        box = next(i for i, p in enumerate(good["passages"]) if p.get("coverage"))
        def few_queries(doc):
            p = doc["passages"][located]
            p.update(outcome="absent", locations=[])
            p["receipt"]["queries"] = ["widget", "Widget "]
            p["receipt"]["query_clauses"] = {"widget": ["c1"]}
            p["receipt"]["special_case"] = dict(self.SPECIAL)
        def close_without_convention(doc):
            doc["passages"][located]["locations"][0].update(
                relation="closeMatch", note="the gadget is written as a subset")
        def close_and_general(doc):
            doc["passages"][located]["locations"][0].update(
                relation="closeMatch", convention="encoding",
                note="more general: any widget-like type")
        def stepless(doc):
            for p in doc["passages"]:
                for loc in p["locations"]:
                    loc.pop("step", None)
        def uninstantiated(doc):
            for p in doc["passages"]:
                for loc in p["locations"]:
                    loc.pop("instantiation", None)
        def untagged_absence(doc):
            p = doc["passages"][located]
            p.update(outcome="absent", locations=[])
            p["receipt"]["special_case"] = dict(self.SPECIAL)
            for cand in p["receipt"]["candidates"]:
                cand.update(verdict="rejected", reason="states the subject, not this")
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
                p.pop("coverage", None)
                p["receipt"]["candidates"] = []
        def close_without_note(doc):
            doc["passages"][located]["locations"][0]["relation"] = "closeMatch"
        def claims_written(doc):
            # J1: the inventory owns the clause list, so no locate document writes one
            doc["passages"][located]["claims"] = [{"id": "c1", "text": "a synthetic clause"}]
        def junk_value(doc):
            # PD7 (from judgment rules 2 on; this plan's gate.json names 3): no convention
            doc["passages"][located]["locations"][0].update(
                relation="closeMatch", convention="junk_value",
                note="Lean returns 0 where the passage leaves the value undefined")
        def whole_part(doc):
            # PD6, under rules 3 part of J3: a part of the widget theorem's bundle that
            # names every conjunct alone is one location, no part
            doc["passages"][box]["locations"][1]["clauses"] = ["c1", "c2"]
        def strong_missing_a_clause(doc):
            # a strong location names the passage's clause ids, the manifest's, and no other
            doc["passages"][located]["locations"][0]["clauses"] = ["c1", "c2"]
        layer = (cf_main_paths(self.ws).sourceset_source_dir(SS, WIDGET) / "layer.txt"
                 ).read_text(encoding="utf-8")
        verbatim = " ".join(layer.split()[:14])     # past the export's twelve-word cap
        def note_verbatim(doc):
            doc["passages"][located]["locations"][0]["note"] = verbatim
        def query_verbatim(doc):
            p = doc["passages"][located]
            p.update(outcome="absent", locations=[])
            p["receipt"]["special_case"] = dict(self.SPECIAL)
            p["receipt"]["queries"][0] = verbatim
            p["receipt"]["query_clauses"] = {q: ["c1"] for q in p["receipt"]["queries"]}
            for cand in p["receipt"]["candidates"]:
                cand.update(verdict="rejected", reason="states the subject, not this")
        cases = {"rule 3 phantom": phantom, "rule 3 proof_status": status,
                 "rule 3 commit": commit, "rule 5": few_queries, "rule 6 count": too_many,
                 "rule 6 rank": duplicate_rank, "rule 4": curated_ignored,
                 "closeMatch note": close_without_note,
                 # the judgment rules (rule 9): no claim list of the document's own, and the
                 # manifest's clause ids on a strong one
                 "rule 9 claims": claims_written, "rule 9 clauses": strong_missing_a_clause,
                 "rule 9 queries": untagged_absence, "rule 9 instantiation": uninstantiated,
                 "rule 9 step": stepless, "rule 9 convention": close_without_convention,
                 "rule 9 generality": close_and_general,
                 # PD5-PD9's mechanical part, from rule 9 version 2 on
                 "rule 9 junk value": junk_value, "rule 9 whole part": whole_part,
                 # the export's verbatim-source cap, applied by the gate (rule 1)
                 "rule 1 note verbatim": note_verbatim, "rule 1 query verbatim": query_verbatim}
        check(any(loc.get("instantiation") for p in good["passages"] for loc in p["locations"]),
              "the widget's locate document has no location to strip an instantiation from")
        check(any(loc.get("step") for p in good["passages"] for loc in p["locations"]),
              "the widget's locate document has no part of a proof to strip a step from")
        for name, fn in cases.items():
            path = self.write_json(self.made / "loc-bad" / name.replace(" ", "-")
                                   / task_id / f"{WIDGET}.json", mutate(fn))
            code, text = self.gate("loc-1", path)
            self.refused(code, text, name,
                         " ".join(name.split()[:2]) if name.startswith("rule ") else None)
            for case, label in (("rule 9 junk value", "(PD7)"), ("rule 9 whole part", "(PD6)")):
                if name == case:
                    check(label in text, f"the {name} violation was refused, but not by {label}:"
                                         f"\n{text}")

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
    #: how every challenge run is planned, but for the run it challenges
    CHALLENGE_PLAN = ("--preset", "locate-challenge", "--sourceset", SS, "--universe", U,
                      "--max-cost", "50")

    def challenge(self) -> None:
        self.bad_upholds = self._challenge_run("chl-1", "loc-1", refusals=True)
        check(self.bad_upholds > 0, "no challenge task upheld an absence, so the uphold's "
                                    "search rule was never exercised")

    def _challenge_run(self, run_id: str, of: str, *flags: str, refusals: bool = False) -> int:
        """Plan a challenge of ``of``, answer and gate it, harvest; the bad upholds refused."""
        bad_upholds = 0
        self.cf("plan-locate", *self.CHALLENGE_PLAN, "--challenge-of", of, "--run-id", run_id,
                *flags)
        out = self.made / run_id / "findings"
        for task_id, manifest in self._manifests(run_id).items():
            for d in manifest["documents"]:
                doc = self._header("locate-challenge", run_id, task_id, d, universe=True)
                verdicts = self._verdicts(d["records"])
                doc["verdicts"] = verdicts
                path = self.write_json(out / task_id / f"{d['source_id']}.json", doc)
                code, text = self.gate(run_id, path)
                check(code == 0, f"the gate refused a good {run_id} challenge document:\n{text}")
                upheld = [i for i, v in enumerate(verdicts) if v.get("queries")]
                if upheld and refusals:
                    # an uphold that says nothing of its search is refused (rule 9)
                    bare = json.loads(json.dumps(doc))
                    for key in ("queries", "query_clauses"):
                        bare["verdicts"][upheld[0]].pop(key)
                    bad = self.write_json(self.made / "chl-bad" / task_id
                                          / f"{d['source_id']}.json", bare)
                    code, text = self.gate(run_id, bad)
                    self.refused(code, text, "uphold without its search", "rule 9")
                    bad_upholds += 1
        self.harvest(run_id, out)
        return bad_upholds

    def _verdicts(self, records: list[dict]) -> list[dict]:
        """A challenger's verdicts under judgment rules 3: every record confirmed or upheld.

        No claim list: a verdict's clause ids are the record's, the inventory's
        conjunct ids or ``c1``.  A verdict that keeps a strong relation -- a part's by
        its ``conjunct_relation`` -- gives its own ``tests`` (fix 2d); a bundle's confirm
        gives each part's relation, no stronger than its own (``part_relations``); an
        uphold lists the challenger's own search, a query for each clause, and the
        nearest special case (FM5, fix 5b).  A conditional record (anomaly A3) is owed a
        verdict only beside an overturn of its passage's prominence, which this
        challenger never makes, so it gets none.
        """
        strong = ("exactMatch", "closeMatch", "broadMatch")
        verdicts = []
        for rec in records:
            if rec.get("conditional"):
                continue
            ids = [c["id"] for c in rec.get("conjuncts") or []] or ["c1"]
            v = {"item": rec["item"], "target": rec["target"],
                 "witness": "re-read the signature at the pin (synthetic)"}
            if rec["target"] == "location":
                v.update(location_key=rec["location_key"], action="confirm")
                if rec["relation"] in strong:
                    v.update(clauses=ids, tests=dict(self.TESTS))
                elif rec["relation"] != "relatedMatch":
                    v["clauses"] = [c for c in rec.get("clauses") or [] if c in ids] or ids[:1]
                    if rec.get("conjunct_relation") in strong:
                        v["tests"] = dict(self.TESTS)    # a strong part, held as strong
            elif rec["target"] == "coverage":
                v.update(action="confirm", part_relations={
                    part["location_key"]: part["conjunct_relation"] for part in rec["parts"]})
            elif rec["target"] in ("absence", "unresolved"):
                queries = [f"synthetic hunt {n} for {rec['item'][-8:]}" for n in range(3)]
                tags = {q: [ids[n % len(ids)]] for n, q in enumerate(queries)}
                if rec.get("passage_kind") == "example":
                    tags[queries[-1]] = tags[queries[-1]] + ["general"]
                v.update(action="uphold", queries=queries, query_clauses=tags,
                         special_case=dict(self.SPECIAL))
            else:
                v["action"] = "uphold"
            verdicts.append(v)
        return verdicts

    # ------------------------------------------------------------- the cut

    def cut_gate(self, run_id: str, manifest_fn=None, *, suffix: str = "",
                 signatures: dict[str, str] | None = None, keys: dict | None = None) -> Path:
        """A copy of ``run_id``'s staged gate, for a case its own gate cannot hold.

        The run is planned under the statement cut (its gate.json names
        :data:`CUT_KEYS`, and its manifests carry the inventory's statement,
        conjuncts and shape, docs/proposals/statement-cut.md section 1.2).  The copy's
        task manifests are what ``manifest_fn`` rewrites them to (a record the plan did
        not make), its gate.json names ``keys`` over the run's own (another version),
        and ``signatures`` (``{declaration: signature}``) gives it its own
        corpus.sqlite with those signatures, pinned by its gate.json as a planner pins
        the database it planned with: the synthetic universe has no number-typed
        binder, which the naturals-for-integers tripwire reads.
        """
        cut = self.made / "host" / f"{run_id}-gate-cut{suffix}"
        if cut.is_dir():
            shutil.rmtree(cut)
        shutil.copytree(self.host_gate(run_id), cut)
        gate = self.read_json(cut / "gate.json")
        check({k: gate.get(k) for k in self.CUT_KEYS} == self.CUT_KEYS,
              f"{run_id}'s staged gate does not name the statement cut's versions: {gate}")
        gate.update(keys or {}, tasks_dir=str(cut / "tasks"), log_dir=str(cut / "log"))
        if signatures:
            db = cut / "corpus.sqlite"
            shutil.copy2(gate["corpus_db"]["path"], db)
            con = sqlite3.connect(db)
            for name, signature in signatures.items():
                con.execute("UPDATE decls SET signature = ? WHERE name = ?", (signature, name))
            con.commit()
            con.close()
            gate["corpus_db"] = {"path": str(db),
                                 "sha256": hashlib.sha256(db.read_bytes()).hexdigest()}
        self.write_json(cut / "gate.json", gate)
        for path in sorted((cut / "tasks").glob("*.json")):
            manifest = self.read_json(path)
            if manifest_fn is not None:
                manifest_fn(manifest)
            self.write_json(path, manifest)
        return cut

    def gate_cut(self, gate_dir: Path, document: Path) -> tuple[int, str]:
        """The cut gate copy over one document, run as the host runs a staged gate."""
        cwd = self.made / "cwd"
        cwd.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([sys.executable, "-I", str(gate_dir / "verify_locate.py"),
                               "--gate-config", str(gate_dir / "gate.json"),
                               "--findings", str(document)], capture_output=True, text=True,
                              cwd=str(cwd), env={"PATH": "/usr/bin:/bin", "HOME": str(cwd),
                                                 "TEND_TASK_ID": ""})
        return proc.returncode, proc.stdout + proc.stderr

    def statement_cut(self) -> None:
        self._cut_inventory()
        self._cut_locate()
        self._cut_challenge()

    def _cut_inventory(self) -> None:
        """inv-1's cut of the widget lemma, held by rule 10 and the end rule 3: its bad twins."""
        from gate import locate_text as lt

        paths = cf_main_paths(self.ws)
        source_dir = paths.sourceset_source_dir(SS, WIDGET)
        layer = (source_dir / "layer.txt").read_text(encoding="utf-8")
        paragraphs = self.read_json(source_dir / "paragraphs.json")
        formulas = self.read_json(source_dir / "formulas.json")
        good = self.read_json(self.inventory_docs[WIDGET])
        doc = json.loads(json.dumps(good))
        gate_dir = self.cut_gate("inv-1")
        task_id = good["task_id"]
        # the same starts ended by the span-end rule 1 -- each at its paragraph's end, never
        # before its quote's, so the display passage's quote runs on over the next
        # passage (the smoke-3 audit's FM9) -- are refused under the cut
        nested = json.loads(json.dumps(doc))
        for p in nested["passages"]:
            start = p["span"][0]
            p["quote"]["exact"] = self._layer_words(layer, start)
            p["span"][1] = lt.passage_end(layer, paragraphs, start, p["quote"]["exact"], rule=1)
        path = self.write_json(self.made / "inv-cut" / "rule-1-ends" / task_id
                               / f"{WIDGET}.json", nested)
        code, text = self.gate_cut(gate_dir, path)
        self.refused(code, text, "the earlier cut's nesting", "rule 10")
        check("shares text with" in text, f"the earlier cut was refused, but not for its "
                                          f"nesting:\n{text}")
        lead = next(i for i, p in enumerate(doc["passages"]) if p["prominence"] == "subject")
        box = next(i for i, p in enumerate(doc["passages"]) if p["finder"] == "theorem_box")
        context = next(i for i, p in enumerate(doc["passages"]) if p["scope"] == "expository")
        display = next(i for i, p in enumerate(doc["passages"])
                       if layer[p["span"][0]:p["span"][1]].startswith("|"))

        def mutate(fn):
            bad = json.loads(json.dumps(doc))
            fn(bad["passages"])
            return bad

        def overlap(ps):                     # the lead runs on over the context sentence
            ps[lead]["until"] = "of order n{\\displaystyle n}."
            ps[lead]["span"][1] = layer.index("of order n{\\displaystyle n}.") + len(
                "of order n{\\displaystyle n}.")

        def run_on(ps):                      # ... with the context sentence no passage
            overlap(ps)
            del ps[context]

        def cut_display(ps):                 # a display cut inside its formula
            ps[display]["until"] = "|S|"
            ps[display]["span"][1] = layer.index("|S|", ps[display]["span"][0]) + 3
            ps[display]["quote"]["exact"] = layer[ps[display]["span"][0]:ps[display]["span"][1]]

        def one_conjunct(ps):
            ps[box]["conjuncts"] = ps[box]["conjuncts"][:1]

        def shapeless(ps):
            ps[box]["shape"] = "lemma_with_corollary"

        def scoped_out_conjuncts(ps):
            ps[context].update(conjuncts=json.loads(json.dumps(self.BOX_CONJUNCTS)),
                               shape="conclusions")

        def scoped_out_central(ps):
            ps[context]["prominence"] = "central"

        def two_subjects(ps):                # the theorem box marked subject beside the lead
            ps[box]["prominence"] = "subject"

        def end_moved(ps):
            ps[lead]["span"][1] -= 1

        head = lt.HeadMap(layer, formulas)

        def same_text(ps):                   # two passages of one word: one passage_key
            for start in (layer.index("gadget version"),
                          layer.rindex("spanning gadget.") + len("spanning ")):
                ps.append({"item": "", "head_words": 0, "span": [start, start + len("gadget")],
                           "quote": {"exact": "gadget",
                                     "prefix": layer[max(0, start - 24):start], "suffix": ""},
                           "until": "gadget", "section": [], "fragment": None,
                           "passage_kind": "other", "prominence": "supporting",
                           "scope": "non_math", "concept": None, "finder": "model",
                           "statement": "a synthetic word, twice"})
            ps.sort(key=lambda p: p["span"][0])
            keyed = lt.assign_ids(head.head, [head.to_head(p["span"][0]) for p in ps])
            for p, (_start, digest, words) in zip(ps, keyed):
                p.update(item=f"In-text #{digest}", head_words=words)

        cases = {"rule 10 overlap": (overlap, "shares text with"),
                 "rule 10 run on": (run_on, "only inside a theorem box"),
                 "rule 10 display": (cut_display, "inside the formula"),
                 "rule 10 conjuncts": (one_conjunct, "a list of 2 to 5"),
                 "rule 10 shape": (shapeless, "the closed list"),
                 "rule 10 scope": (scoped_out_conjuncts, "belong to a formal_math passage"),
                 "rule 10 prominence": (scoped_out_central, "the subject marker goes"),
                 "rule 10 two subjects": (two_subjects, "2 passages carry `prominence: "
                                                        "subject`"),
                 "rule 10 same text": (same_text, "two passages with one span text"),
                 "rule 2 end rule 3": (end_moved, "passage_ids.py ends it")}
        for name, (fn, needle) in cases.items():
            path = self.write_json(self.made / "inv-cut" / name.replace(" ", "-") / task_id
                                   / f"{WIDGET}.json", mutate(fn))
            code, text = self.gate_cut(gate_dir, path)
            self.refused(code, text, name, " ".join(name.split()[:2]))
            check(needle in text, f"the {name} violation was refused, but not for "
                                  f"{needle!r}:\n{text}")

    def _inventory_items(self) -> dict:
        return {p["item"]: p for path in self.inventory_docs.values()
                for p in self.read_json(path)["passages"]}

    def _cut_locate(self) -> None:
        """loc-1's widget document under judgment rules 3: its bad twins, refused by rule 9."""
        items = self._inventory_items()
        gate_dir = self.cut_gate("loc-1")
        task_id, path, good = self._widget_locate()
        doc = json.loads(json.dumps(good))
        code, text = self.gate_cut(gate_dir, path)
        check(code == 0, f"the cut gate refused loc-1's own rules-3 locate document:\n{text}")
        # a run's own versions bind it: a gate.json of judgment rules 2 wants claim lists
        older = self.cut_gate("loc-1", suffix="-rules-2", keys={"judgment_rules": 2})
        code, text = self.gate_cut(older, path)
        self.refused(code, text, "a rules-3 document under rules 2", "rule 9")
        subject = next(i for i, p in enumerate(doc["passages"]) if p["outcome"] == "located"
                       and p["locations"][0]["relation"] == "exactMatch")
        box = next(i for i, p in enumerate(doc["passages"])
                   if (items.get(p["item"]) or {}).get("finder") == "theorem_box")
        absent = next(i for i, p in enumerate(doc["passages"]) if p["outcome"] == "absent")

        def mutate(fn):
            bad = json.loads(json.dumps(doc))
            fn(bad["passages"])
            return bad

        def claims(ps):
            ps[subject]["claims"] = [{"id": "c1", "text": "a synthetic clause"}]

        def no_tests(ps):
            ps[subject]["locations"][0].pop("tests")

        def no_instantiation(ps):
            ps[subject]["locations"][0].pop("instantiation")

        def general(ps):
            ps[subject]["locations"][0]["note"] = "it holds at every point of the widget"

        def no_conjunct_relation(ps):
            ps[box]["locations"][0].pop("conjunct_relation")

        def simple_bundle(ps):
            loc = ps[subject]["locations"][0]
            ps[subject]["locations"] = [
                dict(loc, relation="hasPart", rank=1, step="the existence of the gadget"),
                dict(self._location("alpha", "Widget.card_dvd", "hasPart", 2),
                     step="the divisibility of the gadget's size")]
            for part in ps[subject]["locations"]:
                part.pop("tests", None)
            ps[subject]["coverage"] = {"complete": True, "parts": [1, 2],
                                       "note": "two lemmas give the statement"}

        def unclaimed(ps):                    # the bundle's parts, its claim left out
            ps[box].pop("coverage")

        def no_special_case(ps):
            ps[absent]["receipt"].pop("special_case")

        def flagged(ps):
            ps[subject]["cut_flag"] = "several_statements"

        cases = {"J1 claims": (claims, "claims is the inventory's"),
                 "2a tests": (no_tests, "writes both instantiation tests"),
                 "2b instantiation": (no_instantiation, "whatever its binders"),
                 "2c generality": (general, "every point"),
                 "J2 conjunct relation": (no_conjunct_relation, "says what it bears"),
                 "J3 bundle": (simple_bundle, "on a compound statement only"),
                 "4a unclaimed bundle": (unclaimed, "write the bundle"),
                 "5b special case": (no_special_case, "nearest special case"),
                 "J4 cut flag": (flagged, "belongs to an unresolved passage")}
        for name, (fn, needle) in cases.items():
            path = self.write_json(self.made / "loc-cut" / name.replace(" ", "-") / task_id
                                   / f"{WIDGET}.json", mutate(fn))
            code, text = self.gate_cut(gate_dir, path)
            self.refused(code, text, f"rules-3 {name}", "rule 9")
            check(needle in text, f"the rules-3 {name} violation was refused, but not for "
                                  f"{needle!r}:\n{text}")
        self._cut_bundles(gate_dir, task_id, doc, items, box, subject)

    def _cut_bundles(self, gate_dir: Path, task_id: str, doc: dict, items: dict, box: int,
                     subject: int) -> None:
        """The widget theorem's bundle under judgment rules 3, its bad twins, and fix 2b.

        The theorem box is a compound statement; loc-1's worker claims its two strong
        hasPart parts, one per conjunct, together as a bundle (J3).  Each part is held by
        its conjunct_relation, as a strong location is (fixes 2a-2c): under status rule 2
        the bundle takes its weakest part's relation.
        """
        check(doc["passages"][box].get("coverage"), "loc-1's widget theorem claims no bundle")

        def on_parts(fn):
            def apply(ps):
                fn(ps[box]["locations"])
            return apply

        def mutate(fn):
            bad = json.loads(json.dumps(doc))
            fn(bad["passages"])
            return bad

        def partition(locs):
            locs[1]["clauses"] = ["c2"]

        def special_case_part(locs):
            locs[1]["conjunct_relation"] = "narrowMatch"

        def close_part(locs):
            locs[0]["conjunct_relation"] = "closeMatch"

        def part_tests(locs):
            locs[0].pop("tests")

        def part_generality(locs):
            locs[0]["note"] = "a stronger statement than the conjunct"

        cases = {"J3 one part per conjunct": (partition, "both name c2"),
                 "J3 a strong part": (special_case_part, "a bundle's part states them strongly"),
                 "J2 a close part's convention": (close_part, "names the convention it differs"),
                 "2a a part's tests": (part_tests, "a part whose conjunct_relation is "
                                                   "exactMatch writes both instantiation tests"),
                 "R1 a part's generality": (part_generality, "the note of a part whose "
                                                             "conjunct_relation is exactMatch "
                                                             "says 'stronger'")}
        for name, (fn, needle) in cases.items():
            path = self.write_json(self.made / "loc-cut" / name.replace(" ", "-") / task_id
                                   / f"{WIDGET}.json", mutate(on_parts(fn)))
            code, text = self.gate_cut(gate_dir, path)
            self.refused(code, text, f"rules-3 {name}", "rule 9")
            check(needle in text, f"the rules-3 {name} violation was refused, but not for "
                                  f"{needle!r}:\n{text}")
        # fix 2b: the naturals-for-integers tripwire, on a whole location and on a part;
        # the gate copies differ in which passage speaks of integers
        nat = {"Widget.exists_spanning_gadget": "∀ (n : ℕ) (w : Widget), ∃ g, g.Spans w"}
        for which, at in (("whole", subject), ("part", box)):
            item = doc["passages"][at]["item"]

            def integers(manifest, item=item):
                for d in manifest["documents"]:
                    for row in d.get("passages") or []:
                        if row["item"] == item:
                            row["statement"] = "every finite widget of integer order spans"

            nat_dir = self.cut_gate("loc-1", integers, suffix=f"-nat-{which}", signatures=nat)
            path = self.write_json(self.made / "loc-cut" / f"2b-nat-{which}" / task_id
                                   / f"{WIDGET}.json", doc)
            code, text = self.gate_cut(nat_dir, path)
            self.refused(code, text, f"rules-3 2b naturals for integers ({which})", "rule 9")
            check(f"{item}.locations[" in text and "every number-typed binder" in text,
                  f"the naturals-for-integers violation ({which}) was refused, but not on "
                  f"{item}'s location for its binders:\n{text}")
            answered = json.loads(json.dumps(doc))
            for loc in answered["passages"][at]["locations"]:
                if loc["declaration"] in nat:
                    loc["note"] = "the orders are natural numbers here"
            path = self.write_json(self.made / "loc-cut" / f"2b-nat-{which}-answered" / task_id
                                   / f"{WIDGET}.json", answered)
            code, text = self.gate_cut(nat_dir, path)
            check(code == 0, f"the cut gate refused an answered naturals-for-integers "
                             f"({which}):\n{text}")

    def _conditional_manifests(self) -> callable:
        """chl-1's manifests with a conditional absence of the sampled supporting passage.

        A challenge plan of judgment rules 3 puts a supporting passage's unrequired
        records in the task of its required prominence record, ``conditional:
        "prominence"`` (anomaly A3; docs/proposals/statement-cut.md section 5).  chl-1's
        plan drew none, so this copy of its manifests adds one, as such a plan writes it.
        """
        def rewrite(manifest: dict) -> None:
            for d in manifest["documents"]:
                if d["source_id"] != WIDGET:
                    continue
                recs = d["records"]
                sampled = next(r for r in recs if r["target"] == "prominence"
                               and r.get("prominence") == "supporting")
                recs.append(dict(json.loads(json.dumps(sampled)), target="absence",
                                 conditional="prominence"))
        return rewrite

    def _cut_challenge(self) -> None:
        """chl-1's widget document under judgment rules 3: the bundle's confirm, and the bad
        twins refused by rule 9 (or rule 1)."""
        gate_dir = self.cut_gate("chl-1", self._conditional_manifests())
        manifests = {p.stem: self.read_json(p) for p in sorted((gate_dir / "tasks").glob("*.json"))}
        task_id, d = next((t, d) for t, m in manifests.items() for d in m["documents"]
                          if d["source_id"] == WIDGET)
        check(any(r["target"] == "coverage" for r in d["records"]),
              "chl-1's plan holds no record of the widget theorem's bundle")
        verdicts = self._verdicts(d["records"])
        doc = self._header("locate-challenge", "chl-1", task_id, d, universe=True)
        doc["verdicts"] = verdicts
        path = self.write_json(self.made / "chl-cut" / "good" / task_id / f"{WIDGET}.json", doc)
        code, text = self.gate_cut(gate_dir, path)
        check(code == 0, f"the cut gate refused the good rules-3 challenge document:\n{text}")
        strong = next(i for i, v in enumerate(verdicts) if v.get("tests"))
        upheld = next(i for i, v in enumerate(verdicts) if v.get("special_case"))

        def mutate(fn):
            bad = json.loads(json.dumps(doc))
            fn(bad)
            return bad

        def claims(bad):
            bad["claims"] = {verdicts[strong]["item"]: [{"id": "c1", "text": "a clause"}]}

        def no_tests(bad):
            bad["verdicts"][strong].pop("tests")

        def no_special_case(bad):
            bad["verdicts"][upheld].pop("special_case")

        def emptied(bad):                    # the subject's only location refuted, no fill
            bad["verdicts"][strong] = {"item": verdicts[strong]["item"], "target": "location",
                                       "location_key": verdicts[strong]["location_key"],
                                       "action": "refute", "witness": "states another widget"}

        cases = {"no claims": (claims, "claims is the inventory's"),
                 "2d tests": (no_tests, "a verdict that keeps or leaves a"),
                 "5b special case": (no_special_case, "nearest special case"),
                 "A1 fills": (emptied, "give fills[")}
        for name, (fn, needle) in cases.items():
            path = self.write_json(self.made / "chl-cut" / name.replace(" ", "-") / task_id
                                   / f"{WIDGET}.json", mutate(fn))
            code, text = self.gate_cut(gate_dir, path)
            self.refused(code, text, f"rules-3 challenge {name}", "rule 9")
            check(needle in text, f"the rules-3 challenge {name} violation was refused, but not "
                                  f"for {needle!r}:\n{text}")
        # the fill that answers the emptied passage: an absence with its hunt
        filled = mutate(emptied)
        queries = ["synthetic fill hunt a", "synthetic fill hunt b", "synthetic fill hunt c"]
        filled["fills"] = {verdicts[strong]["item"]: {"outcome": "absent", "receipt": {
            "queries": queries, "candidates": [], "query_clauses": {q: ["c1"] for q in queries},
            "special_case": dict(self.SPECIAL)}}}
        path = self.write_json(self.made / "chl-cut" / "filled" / task_id / f"{WIDGET}.json",
                               filled)
        code, text = self.gate_cut(gate_dir, path)
        check(code == 0, f"the cut gate refused an emptied passage's fill:\n{text}")
        self._cut_challenge_bundle(gate_dir, task_id, d, doc, filled, verdicts[strong]["item"])

    def _cut_challenge_bundle(self, gate_dir: Path, task_id: str, d: dict, doc: dict,
                              filled: dict, emptied_item: str) -> None:
        """Rules 3's bundle verdicts, conditional records and fills, refused for their rules."""
        verdicts = doc["verdicts"]
        records = d["records"]
        conditional = next(r for r in records if r.get("conditional"))
        box_part = next(r for r in records if r["target"] == "location"
                        and r.get("conjunct_relation") == "exactMatch")
        bundle = next(i for i, v in enumerate(verdicts) if v["target"] == "coverage")
        part = next(i for i, v in enumerate(verdicts)
                    if v.get("location_key") == box_part["location_key"])
        broad = next(r for r in records if r["target"] == "location"
                     and r.get("conjunct_relation") == "broadMatch")
        other = next(i for i, v in enumerate(verdicts)
                     if v.get("location_key") == broad["location_key"])
        sampled = next(i for i, v in enumerate(verdicts) if v["target"] == "prominence"
                       and v["item"] == conditional["item"])
        queries = [f"synthetic hunt {n} for {conditional['item'][-8:]}" for n in range(3)]
        hunted = {"item": conditional["item"], "target": "absence", "action": "uphold",
                  "queries": queries, "query_clauses": {q: ["c1"] for q in queries},
                  "special_case": dict(self.SPECIAL),
                  "witness": "searched again (synthetic)"}
        overturn = {"item": conditional["item"], "target": "prominence", "action": "overturn",
                    "prominence": "central", "witness": "the article's centre (synthetic)"}

        def mutate(fn, base=doc):
            bad = json.loads(json.dumps(base))
            fn(bad)
            return bad

        def reopened(bad):                   # an overturn to central, and the record it owes
            bad["verdicts"][sampled] = dict(overturn)
            bad["verdicts"].append(dict(hunted))

        path = self.write_json(self.made / "chl-cut" / "A3-reopened" / task_id
                               / f"{WIDGET}.json", mutate(reopened))
        code, text = self.gate_cut(gate_dir, path)
        check(code == 0, f"the cut gate refused an overturn that judges its conditional "
                         f"record:\n{text}")

        def stronger(bad):
            bad["verdicts"][bundle]["part_relations"][broad["location_key"]] = "exactMatch"

        def part_clauses(bad):
            bad["verdicts"][bundle]["part_clauses"] = {
                k: ["c1"] for k in bad["verdicts"][bundle]["part_relations"]}

        def bare_refute(bad):
            bad["verdicts"][bundle] = {"item": verdicts[bundle]["item"], "target": "coverage",
                                       "action": "refute", "unstated": ["c1"],
                                       "witness": "the existence part is another lemma"}

        def raised(bad):
            bad["verdicts"][other]["conjunct_relation"] = "exactMatch"

        def whole_part(bad):
            bad["verdicts"][part]["clauses"] = ["c1", "c2"]

        def part_tests(bad):
            bad["verdicts"][part].pop("tests")

        def unasked(bad):
            bad["verdicts"].append(dict(hunted))

        def unjudged(bad):
            bad["verdicts"][sampled] = dict(overturn)

        def needless_fill(bad):
            bad["fills"] = {verdicts[bundle]["item"]: json.loads(json.dumps(
                filled["fills"][emptied_item]))}

        def accepted_fill(bad):
            bad["fills"][emptied_item]["receipt"]["candidates"] = [
                {"library": "alpha", "declaration": "Widget.card_dvd", "verdict": "accepted",
                 "reason": ""}]

        cases = {
            "part_relations stronger": (stronger, "stronger than the part's own broadMatch", 9),
            "part_clauses": (part_clauses, "part_clauses is refused under the statement cut",
                             9),
            "1a part_reasons": (bare_refute, "says, for every part, why no R1 convention", 9),
            "a raised conjunct_relation": (raised, "stronger than the record's broadMatch", 9),
            "PD6 a whole part": (whole_part, "names every conjunct", 9),
            "2d a part's tests": (part_tests, "a verdict that keeps or leaves a part whose "
                                              "conjunct_relation is exactMatch writes both", 9),
            "A3 a conditional record unasked": (unasked, "is a conditional record", 1),
            "A3 a conditional record owed": (unjudged, "have no verdict", 1),
            "fills on a passage left a location": (needless_fill, "drop it", 9)}
        for name, (fn, needle, rule) in cases.items():
            path = self.write_json(self.made / "chl-cut" / name.replace(" ", "-") / task_id
                                   / f"{WIDGET}.json", mutate(fn))
            code, text = self.gate_cut(gate_dir, path)
            self.refused(code, text, f"rules-3 challenge {name}", f"rule {rule}")
            check(needle in text, f"the rules-3 challenge {name} violation was refused, but not "
                                  f"for {needle!r}:\n{text}")
        path = self.write_json(self.made / "chl-cut" / "accepted-fill" / task_id
                               / f"{WIDGET}.json", mutate(accepted_fill, filled))
        code, text = self.gate_cut(gate_dir, path)
        self.refused(code, text, "rules-3 challenge an absent fill's accepted candidate",
                     "rule 1")
        check("accepted candidate alpha:Widget.card_dvd is not one of this passage's" in text,
              f"an absent fill's accepted candidate was refused, but not for it:\n{text}")

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
        # status rule 2 (run.yaml locate.status_rule): the row carries the inventory's
        # statement, a strong location its clause ids and its tests, and no claim list
        check("claims" not in subject[0] and subject[0].get("statement")
              and subject[0]["locations"][0].get("clauses") == ["c1"]
              and subject[0]["locations"][0].get("tests") == self.TESTS,
              f"the outcomes lost the inventory's statement, a clause id or the tests: "
              f"{subject[0]}")
        check(all(r.get("challenge") is not None for r in rows if r.get("outcome")),
              "a passage still owes its challenge after the challenge run")
        theorem = [r for r in rows if r["source_id"] == WIDGET and r.get("passage_kind") == "theorem"
                   and r.get("prominence") == "central"]
        # the confirmed bundle takes its weakest part's relation, broadMatch, which formalizes
        # a theorem (docs/proposals/statement-cut.md section 2.3)
        check(theorem and theorem[0]["status"] == "formalized"
              and (theorem[0].get("coverage") or {}).get("relation") == "broadMatch"
              and (theorem[0].get("coverage") or {}).get("challenge") == "survived"
              and theorem[0].get("conjuncts") == self.BOX_CONJUNCTS
              and theorem[0].get("shape") == "conclusions",
              f"the widget theorem's bundle: {theorem}")
        proof = [r for r in rows if r["item"] == self.proof_part]
        check(proof and proof[0]["status"] == "partial"
              and proof[0]["locations"][0].get("step"),
              f"the part of a proof lost its step, or its partial status: {proof}")
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
            # the judgment rules' fields are the documents' and the outcomes', never the
            # record set's: its format is unchanged (references/locate-documents.md section 6)
            ext = json.dumps(record)
            check(not any(f'"{key}"' in ext for key in ("claims", "clauses", "challenge_claims")),
                  f"an exported record carries a judgment-rule field: {record}")
            quote = record["subject"][0].get("location", {}).get("quote") or {}
            check(len((quote.get("exact") or "").split()) <= 12, "an exported quote is over 12 words")
        statuses = {r.get("status") for r in records}
        check("formalized" in statuses, f"no formalized record exported: {statuses}")
        # a run of status rule 3 (R-BROAD, beside judgment rules 4) exports
        # cf-locate-recordset/2 (the statement cut's section 2.4): the seal names the rule,
        # every record its passage_key, a location record its pair_key, and the bundle
        # rides on its passage's records with its relation, its conjuncts each beside its
        # part, and its bundle_key
        seal = self.read_json(set_dir / "SEAL.json")
        check(seal.get("schema") == "cf-locate-recordset/2" and seal.get("status_rule") == 3,
              f"loc-1's record set is not status rule 3's: {seal.get('schema')}, "
              f"status_rule {seal.get('status_rule')}")
        exts = [self._locate_ext(record) for record in records]
        check(all(isinstance(ext.get("passage_key"), str) and len(ext["passage_key"]) == 64
                  for ext in exts), "an exported record carries no passage_key")
        located = [ext for ext in exts if ext.get("location_key")]
        check(located and all(len(str(ext.get("pair_key") or "")) == 64 for ext in located),
              "an exported location record carries no pair_key")
        bundles = [ext["coverage"] for ext in exts if (ext.get("coverage") or {}).get("bundle_key")]
        check(bundles and all(b["relation"] == "broadMatch"
                              and [c["id"] for c in b["conjuncts"]] == ["c1", "c2"]
                              and {c["part"] for c in b["conjuncts"]} == set(b["parts"])
                              for b in bundles),
              f"the widget theorem's bundle was not exported with its relation, conjuncts and "
              f"key: {bundles}")
        # idempotent: the same inputs seal the same bytes
        before = (set_dir / "SEAL.json").read_bytes()
        self.cf("export", "literature-links", "--run", "loc-1", "--challenge-run", "chl-1",
                "--set-id", "set-1")
        check((set_dir / "SEAL.json").read_bytes() == before, "a re-export changed the seal")

    @staticmethod
    def _locate_ext(record: dict) -> dict:
        """A record's locate extension (``references/locate-documents.md`` section 6)."""
        from cflib import vocab
        return (record.get("extensions") or {}).get(vocab.LOCATE_EXTENSION) or {}

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
        score = self._score("loc-1")
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

    #: how every run is scored (the A/A stages score their replicates so too)
    SCORE_FLAGS = ("--split", "eval", "--no-canary-check", "--bootstrap", "200")
    #: the A/A's claim: G3 is the claimable tier the synthetic bench labels (G1 is
    #: claimable in the blind arm only)
    AA_CLAIM = "G3.inventory_recall"

    def _score(self, run_id: str) -> dict:
        code, out = self.cf("bench", "score", "b0", "--run", run_id, *self.SCORE_FLAGS, "--json")
        return json.loads(out[out.index("{"):])

    def _score_path(self, score: dict) -> Path:
        from bench.layout import BenchLayout
        path = BenchLayout.of(cf_main_paths(self.ws), "b0").score_json(score["score_id"])
        check(path.is_file(), f"cf bench score wrote no {path}")
        return path

    def _replicate(self, locate_run: str, challenge_run: str, *challenge_flags: str) -> dict:
        """A locate run planned as loc-1 and answered by the same worker, its challenge
        planned as chl-1 was but for ``challenge_flags``, harvested, tallied and scored."""
        out, docs = self._locate_run(locate_run)
        receipt = self.harvest(locate_run, out)
        check((receipt.get("locate") or {}).get("documents") == len(docs),
              f"the {locate_run} receipt counts {receipt.get('locate')} locate documents")
        self._challenge_run(challenge_run, locate_run, *challenge_flags)
        self.cf("tally", "--locate", "--run", locate_run, "--challenge-run", challenge_run)
        return self._score(locate_run)

    def _aa_argv(self, baseline: dict, replicate: dict) -> tuple[str, ...]:
        return ("bench", "compare", "b0", str(self._score_path(baseline)),
                "--aa", str(self._score_path(replicate)), "--claim", self.AA_CLAIM,
                "--bootstrap", "400")

    # ------------------------------------------------------------------ A/A
    def aa(self) -> None:
        """The A/A stage: loc-2 is loc-1's configuration run again, and repeats it."""
        base = self.score
        replicate = self._replicate("loc-2", "chl-2")
        self.aa_replicate = replicate
        check(replicate["measurement_id"] != base["measurement_id"],
              "the replicate measured the baseline's own runs")
        check(replicate["runs"]["inventory"] == base["runs"]["inventory"] == ["inv-1"],
              f"the replicate read another inventory: {replicate['runs']}")
        planned = [sorted(json.dumps(v, sort_keys=True) for v in s["pins"]["planning"].values())
                   for s in (base, replicate)]
        check(planned[0] == planned[1], f"loc-2 was not planned as loc-1 was: {planned}")
        code, out = self.cf(*self._aa_argv(base, replicate), "--json")
        doc = json.loads(out[out.index("{"):])
        self.aa_result = doc
        check(doc.get("schema") == "cf-bench-aa/1" and doc.get("answer") == "aa ok"
              and doc.get("ok") is True and doc.get("failed") == [],
              f"the A/A of loc-1 and loc-2 does not answer aa ok: {doc.get('failed')}")
        check((doc["baseline"], doc["replicate"])
              == (base["measurement_id"], replicate["measurement_id"]),
              f"the A/A compared other measurements: {doc['baseline']}, {doc['replicate']}")
        agree = doc["aa"]["agreement"]
        check(self.AA_CLAIM in agree["metrics"] and "G1.fnf_rate" in agree["metrics"]
              and all(row["ok"] for row in agree["metrics"].values()) and agree["cost"]["ok"],
              f"the replicate's outcomes do not agree with the baseline's: {agree}")
        # the worker answers loc-2 as it answered loc-1: agreement is SHOWN on every row
        # (each interval inside the tolerance), not merely left unrefuted
        check(agree["unresolved"] == [] and len(agree["within"]) == agree["compared"] > 2,
              f"the A/A did not show agreement on every row it compared: within "
              f"{agree['within']}, unresolved {agree['unresolved']}")
        check(doc["aa"]["accepted"] is False,
              "the acceptance rule accepted the replicate over its own baseline")
        code, text = self.cf(*self._aa_argv(base, replicate))
        check(text.splitlines()[0].startswith("aa ok"),
              f"cf bench compare --aa does not answer aa ok:\n{text}")
        check(f"agreement within the tolerance shown for {agree['compared']} of "
              f"{agree['compared']}" in text,
              f"cf bench compare --aa does not say where it showed agreement:\n{text}")

    def aa_refusal(self) -> None:
        """One planning pin changed: loc-3's challenge samples strong records at another
        rate, so loc-3 is no replicate of loc-1, and the A/A check says which key."""
        from cflib import vocab
        rate = 1.0
        check(rate != vocab.CHALLENGE_STRONG_SAMPLE_RATE, "the changed pin is the default")
        changed = self._replicate("loc-3", "chl-3", "--strong-sample-rate", str(rate))
        planning = changed["pins"]["planning"]["chl-3"]
        check(planning.get("strong_sample_rate") == rate,
              f"chl-3's run.yaml does not record its strong-sample rate: {planning}")
        code, out = self.cf(*self._aa_argv(self.score, changed), ok=False)
        self.aa_refusal_text = out
        check(code == 2 and "not the baseline's configuration" in out
              and "planning differ" in out and "challenge strong_sample_rate" in out
              and "--strong-sample-rate" in out,
              f"the A/A check did not refuse another --strong-sample-rate by name "
              f"(exit {code}):\n{out}")
        check("aa ok" not in out and "aa FAILS" not in out,
              f"a refused A/A was answered anyway:\n{out}")

    # ---------------------------------------------------------------- carry
    #: the re-cut: the widget lead's second sentence, which names the lemma and states
    #: nothing, becomes an expository passage of its own (splitting the lead), and the
    #: corollary item ends at an ``until`` short of its stop (its end moved)
    RECUT_SPLIT = "It is named after"
    RECUT_UNTIL = "Every two spanning gadgets"

    def carry(self) -> None:
        """A re-cut of the sourceset (inv-2) and the carry-over map from inv-1's cut to it.

        ``cf locate carry`` (docs/proposals/statement-cut.md section 4) keys every passage
        of both cuts by its text, and says what the re-cut did to each of inv-1's: the
        lead is split, the corollary's end moved, the rest unchanged; with ``--rows`` a
        row keyed by an old passage key carries only where its passage is unchanged, and
        a row that read the conjuncts only while they are the same.  ``cf bench carry``
        then says which frozen gold rows the re-cut carries and which it relabels.
        """
        from cflib import vocab

        paths = cf_main_paths(self.ws)
        self.cf("plan-locate", "--preset", "locate-inventory", "--sourceset", SS,
                "--universe", U, "--run-id", "inv-2", "--max-cost", "50")
        layer = self.layer_widget
        corollary = layer.index(self.RECUT_UNTIL)
        out = self.made / "inv-2" / "findings"
        docs = self._inventory_documents(
            "inv-2", out, more_starts={WIDGET: [(layer.index(self.RECUT_SPLIT), "expository")]},
            untils={WIDGET: {corollary: self.RECUT_UNTIL}})
        for sid, path in docs.items():
            code, text = self.gate("inv-2", path)
            check(code == 0, f"the staged gate refused the re-cut inventory of {sid}:\n{text}")
        self.harvest("inv-2", out)
        self.cf("locate", "carry", "--from", "inv-1", "--to", "inv-2")
        report = self.read_json(paths.root / "reports" / "locate-carry" / "inv-1" / "inv-2.json")
        check(report.get("schema") == "cf-locate-carry/1" and not report.get("refused"),
              f"the carry-over map is no cf-locate-carry/1 map of every page: {report}")
        check((report["old"]["span_end_rule"], report["old"]["statement_cut_rule"],
               report["new"]["span_end_rule"], report["new"]["statement_cut_rule"])
              == (3, 3, 3, 3), f"the map does not name the two cuts' rules: {report['old']}, "
                               f"{report['new']}")
        page = next(pg for pg in report["pages"] if pg["new"]["source_id"] == WIDGET)
        check(page["same_layer"] is True, "the re-cut of one revision is on another layer")
        fates = {p["span"][0]: p["fate"] for p in page["passages"]}
        lead = next(p for p in page["passages"] if p["span"][0] == layer.index("In widget theory"))
        box = next(p for p in page["passages"] if p["scope"] == "formal_math"
                   and layer[p["span"][0]:].startswith("Widget lemma"))
        check(lead["fate"] == "split" and len(lead["into"]) == 2, f"the lead was not split: {lead}")
        check(fates.get(corollary) == "end_moved",
              f"the corollary's end did not move: {page['passages']}")
        check(box["fate"] == "unchanged" and not box["conjuncts_changed"],
              f"the widget theorem box did not carry: {box}")
        check(set(fates.values()) == {"split", "end_moved", "unchanged"}, f"fates: {fates}")
        # the rows a consumer keys by passage_key: a known answer on the split lead, the
        # bundle verdict on the theorem box (it read the conjuncts), the moved corollary's
        rows = self.made / "carry-rows.jsonl"
        rows.write_text("".join(json.dumps(row) + "\n" for row in (
            {"passage_key": lead["passage_key"], "known": "the widget lemma"},
            {"passage_key": box["passage_key"], "reads_conjuncts": True, "verdict": "bundle"},
            {"passage_key": next(p["passage_key"] for p in page["passages"]
                                 if p["span"][0] == corollary), "known": "a corollary"})),
            encoding="utf-8")
        carried = self.made / "carry-rows-out.jsonl"
        self.cf("locate", "carry", "--from", "inv-1", "--to", "inv-2", "--rows", str(rows),
                "--rows-out", str(carried))
        out_rows = [json.loads(line) for line in carried.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        check([(r["carry"]["fate"], r["carried"]) for r in out_rows]
              == [("split", False), ("unchanged", True), ("end_moved", False)],
              f"the rows did not carry by their passages' fates: {out_rows}")
        self.carry_report = report
        # the bench's frozen gold across the same re-cut: the anchored lead's row relabels
        from bench.layout import BenchLayout
        layout = BenchLayout.of(paths, "b0")
        before = {p.name: p.read_bytes() for p in (layout.root / "gold").glob("*.jsonl")}
        self.cf("bench", "carry", "b0", "--from", "inv-1", "--to", "inv-2")
        (path,) = sorted((layout.root / "gold").glob("carry-*.json"))
        bench = self.read_json(path)
        check(bench.get("schema") == "cf-bench-carry/1", f"cf bench carry wrote {bench}")
        states = {(r["tier"], r["state"]) for r in bench["rows"]}
        check(all(state in vocab.GOLD_CARRY_STATES for _tier, state in states),
              f"a gold row's state is none of GOLD_CARRY_STATES: {states}")
        g1 = [r for r in bench["rows"] if r["tier"] == "G1"]
        check(g1 and all(r["state"] == "relabel" and r["passages"][0]["fate"] == "split"
                         for r in g1), f"the anchored lead's gold did not relabel: {g1}")
        check(any(state == "carried" for _tier, state in states),
              f"no gold row carried across a re-cut that left most passages: {states}")
        check({p.name: p.read_bytes() for p in (layout.root / "gold").glob("*.jsonl")} == before,
              "cf bench carry edited a label file")


    # ------------------------------------------------------------------ audit
    #: the audited run's flagged page and the repaired statement its worker writes
    AUDIT_RUN = "inv-aud"
    AUDIT_REPAIRED = "synthetic restatement naming every hypothesis"

    def audit(self) -> None:
        """An audited inventory run, every turn as tend would make it, then its harvest.

        ``cf plan-locate --audit-rounds 2`` plans inv-aud; each task's committed cut sits
        in a git worktree of its own, and each turn runs the planned wrapper
        (``tend-root/bin/<role>-agent.sh``: ``CF_AUDIT_RULE`` makes the bridge hand it to
        ``bin/audit_turn.py``, which calls ``agent_call.py --step``) with a fake
        ``agentbox`` first on PATH (:data:`FAKE_AGENTBOX`).  The plan is audit rule 3's
        (references/locate-documents.md sections 13.11 and 13.12: the skips and the gaps
        judged; this page's gaps, if any, are judged clean), and the widget page's worker
        skipped its first corollary (:meth:`_skip_corollaries`).  Its auditor flags its
        first formal passage and the skip, with the statement the skip hides; the skeptic
        upholds both, the skip's with evidence from inside it; the merge adds rule 3's kind
        rule verdict on the page's model passage of kind other and rejects; the worker's
        revision applies a ``set`` per passage verdict and, for the skip, an ``unskip`` and
        an ``add`` through the staged ``inventory_edit.py --rule 3``, ``passage_ids.py`` and
        the gate, and commits; round 2's checker finds all three applied and, the final
        round, the merge approves.  The other pages are clean in round 1.  The tend root's ``audit/``
        and ``usage/`` are landed under ``runs/<run>/host/`` as the remote harvest's
        fetch lands them (``*.json`` only), and ``cf harvest`` writes the receipt's
        ``audit`` block (section 13.10), its ``skips`` one skip of one math paragraph
        before and none after, beside the prose floor's skips of paragraphs with no math,
        which stay.
        """
        from cflib import vocab

        run_id = self.AUDIT_RUN
        self.cf("plan-locate", "--preset", "locate-inventory", "--sourceset", SS,
                "--universe", U, "--run-id", run_id, "--max-cost", "50",
                "--audit-rounds", "2")
        paths = cf_main_paths(self.ws)
        root = paths.tend_root(run_id)
        rule = vocab.INVENTORY_AUDIT_RULE
        check(rule == 3, f"the planner writes audit rule {rule}, not the gap audit's 3")
        for role in ("worker", "reviewer"):
            wrapper = (root / "bin" / f"{role}-agent.sh").read_text(encoding="utf-8")
            check(f'export CF_AUDIT_RULE="{rule}"' in wrapper and 'export CF_AUDIT_ROUNDS="2"'
                  in wrapper, f"the planned {role} wrapper does not turn the audit on")
        drafts = self._inventory_documents(run_id, self.made / run_id / "drafts")
        self._skip_corollaries(run_id, drafts[WIDGET])
        fakebin = self._fake_agentbox()
        findings = self.made / run_id / "findings"
        for task_id, manifest in self._manifests(run_id).items():
            sid = manifest["documents"][0]["source_id"]
            wt = self._task_worktree(run_id, task_id, sid, drafts[sid])
            flagged = sid == WIDGET
            env = {"PATH": f"{fakebin}{os.pathsep}/usr/bin{os.pathsep}/bin",
                   "HOME": str(self.made / "home"), "TEND_TASK_ID": task_id,
                   "TEND_WORKTREE_PATH": str(wt), "TEND_WORKTREE_ID": f"wt-{task_id}",
                   "CF_AGENTBOX_PROFILE": "e2e", "CF_SLOT_DIR": str(self.made / "slots"),
                   "FAKE_FLAG": "1" if flagged else "", "FAKE_REPAIRED": self.AUDIT_REPAIRED,
                   "FAKE_SKIP": self.AUDIT_SKIP_REASON,
                   "FAKE_SOURCES": str(paths.sourceset_source_dir(SS, sid).parent),
                   **GIT_ENV}
            first = self._turn(root, wt, "reviewer", env)
            if flagged:
                check(first.get("verdict") == "request_changes"
                      and "r1.verdicts.json" in str(first.get("feedback_text")),
                      f"round 1 did not reject the flagged page with its verdicts: {first}")
                worked = self._turn(root, wt, "worker", env)
                check(worked.get("status") in ("completed", "needs_review"),
                      f"the revision did not finish: {worked}")
                second = self._turn(root, wt, "reviewer", env)
                check(second.get("verdict") == "approve",
                      f"round 2 did not approve the repaired page: {second}")
            else:
                check(first.get("verdict") == "approve",
                      f"round 1 did not approve the clean page {sid}: {first}")
            doc = wt / "findings" / task_id / f"{sid}.json"
            (findings / task_id).mkdir(parents=True, exist_ok=True)
            shutil.copy2(doc, findings / task_id / doc.name)
        widget = next(findings.glob(f"*/{WIDGET}.json"))
        check(self.AUDIT_REPAIRED in widget.read_text(encoding="utf-8"),
              "the merged widget page does not carry the repaired statement")
        code, text = self.gate(run_id, widget)
        check(code == 0, f"the staged gate refused the repaired page:\n{text}")
        host = paths.run_host_dir(run_id)
        self._land(root / "audit", host / "audit")
        self._land(root / "usage", host / "envelopes")
        receipt = self.harvest(run_id, findings)
        block = receipt.get("audit") or {}
        per = block.get("per_source") or {}
        check(block.get("rule") == rule and block.get("review_rounds") == 2
              and block.get("source") == "kept", f"the receipt's audit block is {block}")
        check(block.get("outcomes", {}).get("approved") == 3 and block.get("audited") == 3
              and not block.get("stale") and not block.get("unaudited"),
              f"not every page is the bytes its audit approved: {block.get('outcomes')}")
        rounds = block.get("rounds") or {}
        check(rounds.get("1", {}).get("decisions") == {"approve": 2, "request_changes": 1,
                                                       "revert": 0}
              and rounds.get("2", {}).get("decisions") == {"approve": 1, "request_changes": 0,
                                                           "revert": 0}
              and rounds["2"]["counts"].get("status_applied") == 3
              and rounds["1"]["counts"].get("skips_upheld") == 1
              and rounds["1"]["counts"].get("hidden_statements") == 1
              # rule 3's kind rule reads every formal passage of kind other: the widget
              # page's model passage of kind other, outside a proof, gets its verdict too
              and rounds["1"]["counts"].get("kind_rule") == 1
              and rounds["1"]["counts"].get("gaps") == 0,
              f"the rounds are not round 1's rejection and round 2's approval: {rounds}")
        check(per.get(WIDGET, {}).get("actionable_by_round") == [3, 0]
              and per[WIDGET].get("reviewed_round") == 2
              and block.get("residual", {}).get("actionable") == 0,
              f"the widget page's verdicts were not applied: {per.get(WIDGET)}")
        skips = block.get("skips") or {}
        before, after = skips.get("before") or {}, skips.get("after") or {}
        # beside the corollary's skip, the prose floor's: two on the widget page and one on
        # the gadget-lattice page, which hold no math paragraph and stay
        check((before.get("skips"), before.get("math_paragraphs"), before.get("outside"),
               before.get("documents")) == (4, 1, 1, 3)
              and (after.get("skips"), after.get("math_paragraphs")) == (3, 0)
              and (skips.get("by_source") or {}).get(WIDGET, {}).get("before", {})
              .get("by_section") == {"Statement > Corollaries": {"skipped": 1, "math": 1}},
              f"the receipt's skips are not one skip of one math paragraph before and none "
              f"after: {skips}")
        widget_doc = json.loads(widget.read_text(encoding="utf-8"))
        check({s["reason"] for s in widget_doc["skips"]} == {self.PROSE_SKIP_REASON}
              and sum(p["section"] == ["Statement", "Corollaries"]
                      for p in widget_doc["passages"]) == 1,
              f"the corollary was not repaired into its passage: {widget_doc['skips']}")
        check(block.get("turns", {}).get("rejections") == 1,
              f"the turn machine recorded {block.get('turns')} rejections, not one")
        cost = block.get("cost_usd") or {}
        check(all(cost.get(step, 0) > 0 for step in ("audit", "skeptic", "check",
                                                     "worker_revision"))
              and cost.get("worker_initial") == 0,
              f"the cost by step does not price the audit's calls: {cost}")
        check("audit" not in self.read_json(paths.receipt_json("inv-1")),
              "an unaudited run's receipt carries an audit block")

    #: the widget page's skip: its first corollary, a list item with a formula that states
    #: a result (the second starts inside a word on the layer, so no passage can start there)
    AUDIT_SKIP_REASON = "a corollary; the theorem above states it"

    def _skip_corollaries(self, run_id: str, draft: Path) -> None:
        """The widget page's worker skipped its first corollary: its passage dropped and a
        skip over the list item, re-keyed and gate-clean -- a skip the gate admits and the
        skip audit must see."""
        paths = cf_main_paths(self.ws)
        source_dir = paths.sourceset_source_dir(SS, WIDGET)
        items = [p["span"] for p in self.read_json(source_dir / "paragraphs.json")
                 if p.get("block") == "li" and p.get("section") == ["Statement", "Corollaries"]]
        check(len(items) == 2, f"the widget page's corollaries are not two list items: {items}")
        span = list(items[0])
        doc = self.read_json(draft)
        doc["passages"] = [p for p in doc["passages"]
                           if not span[0] <= p["span"][0] < span[1]]
        doc["skips"] = sorted(doc["skips"] + [{"span": span, "reason": self.AUDIT_SKIP_REASON}],
                              key=lambda s: s["span"])
        self.write_json(draft, doc)
        proc = self.isolated(run_id, "passage_ids.py", "--source-dir", str(source_dir),
                             "--document", str(draft), "--write")
        check(proc.returncode == 0, f"passage_ids.py on the skipped draft: {proc.stderr}")
        code, text = self.gate(run_id, draft)
        check(code == 0, f"the staged gate refused the draft that skips the corollaries:\n{text}")

    def _fake_agentbox(self) -> Path:
        """``fakebin/`` with :data:`FAKE_AGENTBOX` as ``agentbox`` and this interpreter as
        ``python3`` (the bridge and the turn machine run ``python3``; the system's may be
        older than the 3.11 the skill needs)."""
        fakebin = self.made / "fakebin"
        if not fakebin.is_dir():
            fakebin.mkdir(parents=True)
            (fakebin / "agentbox").write_text(FAKE_AGENTBOX, encoding="utf-8")
            (fakebin / "agentbox").chmod(0o755)
            (fakebin / "python3").symlink_to(sys.executable)
        return fakebin

    def _task_worktree(self, run_id: str, task_id: str, sid: str, draft: Path) -> Path:
        """A task's worktree as tend leaves it for review: ``main``, then the task's branch
        with the worker's committed document; ``.tend/`` excluded as in a tend worktree."""
        wt = self.made / run_id / "worktrees" / task_id
        wt.mkdir(parents=True)
        self.git(wt, "init", "-q", "-b", "main")
        (wt / ".git" / "info" / "exclude").write_text(".tend/\n", encoding="utf-8")
        self.git(wt, "commit", "-q", "--allow-empty", "-m", "base")
        self.git(wt, "checkout", "-q", "-b", task_id)
        doc = wt / "findings" / task_id / f"{sid}.json"
        doc.parent.mkdir(parents=True)
        shutil.copy2(draft, doc)
        self.git(wt, "add", "--", "findings")
        self.git(wt, "commit", "-q", "-m", f"inventory of {sid}")
        return wt

    def _turn(self, root: Path, wt: Path, role: str, env: dict) -> dict:
        """One tend turn: the planned wrapper, in the worktree; its stdout is the contract."""
        proc = subprocess.run(["bash", str(root / "bin" / f"{role}-agent.sh")],
                              capture_output=True, text=True, cwd=str(wt), env=env,
                              timeout=600)
        check(proc.returncode == 0 and len(proc.stdout.strip().splitlines()) == 1,
              f"the {role} turn exited {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")
        return json.loads(proc.stdout)

    @staticmethod
    def _land(remote: Path, local: Path) -> None:
        """What the harvest's fetch of a host directory lands: its ``*.json``, in place."""
        if local.exists():
            shutil.rmtree(local)
        for path in sorted(remote.rglob("*.json")):
            target = local / path.relative_to(remote)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


#: the audited run's agents (synthetic): an executable that answers each call
#: ``audit_turn.py`` makes through ``agent_call.py`` from what the call's prompt names, at the
#: audit rule its rendering index names.  The auditor flags the first formal passage of the
#: page when FAKE_FLAG is set, and every skip whose reason is FAKE_SKIP with the statements its
#: list items start, and (rule 3) every gap clean; the skeptic upholds every flag with the
#: first words of its span, a skip's from inside the skip, and hunts every unflagged skip and
#: (rule 3) every gap of its part; the checker finds
#: every verdict applied and no collateral harmful; the worker's revision writes one ``set``
#: operation per passage verdict and, per skip verdict, an ``unskip`` and an ``add`` per
#: statement, runs the edit tool, the id tool and the gate the prompt names, reports each
#: verdict applied and commits the document.  It prints a priced envelope.
FAKE_AGENTBOX = r"""#!/usr/bin/env python3
import json, os, re, shlex, subprocess, sys
argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
A = os.path.join(".tend", "audit")
task = os.environ["TEND_TASK_ID"]
(doc_rel,) = [os.path.join("findings", task, n) for n in os.listdir(os.path.join("findings", task))]
sid = os.path.basename(doc_rel)[:-5]
SOURCE = os.path.join(os.environ["FAKE_SOURCES"], sid)

def load(name):
    with open(os.path.join(A, name), encoding="utf-8") as fh:
        return json.load(fh)

def dump(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)

def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()

def head(kind, k, schema):
    return {"schema": schema, "kind": kind, "round": k, "source_id": sid}

def words(text, n=4):
    return " ".join(text.split()[:n])

def evidence(item):
    doc = json.loads(read(doc_rel))
    (p,) = [p for p in doc["passages"] if p["item"] == item]
    return words(read(os.path.join(SOURCE, "layer.txt"))[p["span"][0]:p["span"][1]])

def statements(span):
    # the list items the skip covers: each one's first words, a theorem
    layer = read(os.path.join(SOURCE, "layer.txt"))
    out = []
    for p in json.loads(read(os.path.join(SOURCE, "paragraphs.json"))):
        a, b = p["span"]
        if p.get("block") == "li" and span[0] <= a < span[1]:
            out.append({"at": words(layer[a:b]), "until": "", "passage_kind": "theorem",
                        "section": p.get("section") or []})
    return out

call = re.search(r"## This call: the (audit|check|skeptic) of round (\d+), part (\d+) of", prompt)
if call:
    step, k, part = call.group(1), int(call.group(2)), int(call.group(3))
    out = re.search(r"Write exactly one file: (\S+?)\.\n", prompt + "\n").group(1)
    index = load("r%d.render.json" % k)
    schema, rule = index["schema"], index.get("rule", 1)
    formal = index["parts"][part - 1]["formal"]
    shown = {r["id"]: r for r in index.get("skips") or []
             if r["id"] in (index["parts"][part - 1].get("skips") or [])}
    if step == "audit":
        rows = [{"item": item, "origin": "lead", "verdict": "clean", "rule": 0,
                 "merge_with": "", "reason": "", "action": ""} for item in formal]
        if os.environ.get("FAKE_FLAG") and part == 1 and rows:
            rows[0].update(verdict="statement_misstates", rule=8,
                           reason="the statement drops a hypothesis the passage states",
                           action="set the statement to name every hypothesis")
        data = dict(head("audit", 1, schema), passages=rows, expository_flags=[],
                    per_spec_formal_estimate=len(rows), patterns=[], subject_check="one subject")
        if rule >= 2:
            skips = []
            for skip_id, row in shown.items():
                flagged = row["reason"] == os.environ.get("FAKE_SKIP")
                skips.append({"skip": skip_id, "verdict": "skipped_statements" if flagged
                              else "clean", "rule": 5 if flagged else 0, "merge_with": "",
                              "reason": "each list item states a result" if flagged else "",
                              "action": "unskip it; add a passage per list item" if flagged
                              else "",
                              "statements": [{k_: v for k_, v in st.items() if k_ != "section"}
                                             for st in statements(row["span"])]
                              if flagged else []})
            data["skips"] = skips
        if rule >= 3:
            # every gap of the part judged: clean (this page's worker leaves none that states)
            data["gaps"] = [{"gap": gid, "verdict": "clean", "rule": 0, "merge_with": "",
                             "reason": "", "action": "", "statements": []}
                            for gid in index["parts"][part - 1].get("gaps") or []]
    elif step == "skeptic":
        flags = load("r%d.flags.part%d.json" % (k, part))
        flagged = {f["item"] for f in flags["flags"]}
        layer = read(os.path.join(SOURCE, "layer.txt"))
        verdicts = []
        for f in flags["flags"]:
            proof = (words(layer[f["span"][0]:f["span"][1]]) if f.get("skip")
                     else evidence(f["item"]))
            verdicts.append({"flag_id": f["flag_id"], "upheld": True, "verdict": f["verdict"],
                             "rule": f["rule"], "evidence": proof,
                             "reason": "the rule as written says so"})
        data = dict(head("skeptic", k, schema), verdicts=verdicts, missed=[],
                    disputes=[{"verdict_id": d["verdict_id"], "worker_right": False,
                               "reason": "the rule as written"} for d in flags["disputes"]],
                    examined=[x for x in formal if x not in flagged],
                    per_spec_formal_estimate=len(formal), summary="part %d judged" % part)
        if rule >= 2:
            skipped = {f.get("skip") for f in flags["flags"]}
            data.update(examined_skips=[x for x in shown if x not in skipped] if k == 1 else [],
                        missed_skips=[])
        if rule >= 3:
            gaps = index["parts"][part - 1].get("gaps") or []
            data.update(examined_gaps=list(gaps) if k == 1 else [], missed_gaps=[])
    else:
        expect = load("r%d.check.part%d.expect.json" % (k, part))
        data = dict(head("check", k, schema),
                    checked=[{"verdict_id": x, "status": "applied", "reason": "applied as asked"}
                             for x in expect if "-v" in x],
                    collateral=[{"id": x, "harmful": False, "reason": "harmless"}
                                for x in expect if "-x" in x], new=[], regressions=[])
    dump(out, data)
elif "## This session: the revision of round" in prompt:
    k = int(re.search(r"the revision of round (\d+)", prompt).group(1))
    line = next(ln for ln in prompt.splitlines() if ln.startswith("- The verdicts:"))
    ids = re.findall(r"r[0-9]+-v[0-9]{3,}", line.split(";", 1)[1])
    verdicts_file = load("r%d.verdicts.json" % k)
    schema = verdicts_file["schema"]
    verdicts = [v for v in verdicts_file["verdicts"] if v["id"] in ids]
    snapshot = load("r%d.document.json" % k)
    ops_path, repair_path = (os.path.join(A, "r%d.%s.json" % (k, n)) for n in ("ops", "repair"))
    ops = load("r%d.ops.json" % k) if os.path.isfile(ops_path) else dict(head("ops", k, schema),
                                                                          ops=[])
    repair = (load("r%d.repair.json" % k) if os.path.isfile(repair_path)
              else dict(head("repair", k, schema), entries=[]))
    for v in verdicts:
        if v["verdict"] == "skipped_statements":
            before = [p for p in snapshot["passages"] if p["span"][0] < v["span"][0]]
            after = max(before, key=lambda p: p["span"][0])["item"] if before else None
            ops["ops"].append({"op": "unskip", "skip": v["skip"], "verdict": v["id"]})
            for st in statements(v["span"]):
                ops["ops"].append({"op": "add", "after": after, "verdict": v["id"],
                                   "quote": {"exact": st["at"]},
                                   "fields": {"statement": "synthetic restatement of " + st["at"],
                                              "passage_kind": st["passage_kind"],
                                              "scope": "formal_math",
                                              "prominence": "supporting",
                                              "section": st["section"]}})
        else:
            ops["ops"].append({"op": "set", "item": v["item"], "verdict": v["id"],
                               "fields": {"statement": os.environ["FAKE_REPAIRED"]}})
        repair["entries"].append({"verdict_id": v["id"], "status": "applied", "why": ""})
    dump(ops_path, ops)
    dump(repair_path, repair)
    for tool in ("inventory_edit.py", "passage_ids.py --source-dir",
                 "verify_locate.py --gate-config"):
        (command,) = [ln for ln in prompt.splitlines()
                      if ln.startswith("python3 ") and tool in ln and "<" not in ln]
        subprocess.run(shlex.split(command), check=True, stdout=sys.stderr)
    subprocess.run(["git", "add", "--", doc_rel], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "audit revision %d" % k], check=True)
    dump(os.path.join(".tend", "agent_result.json"),
         {"status": "completed", "summary": "applied %d verdict(s)" % len(verdicts)})
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done",
                  "total_cost_usd": 0.5, "duration_ms": 1000,
                  "modelUsage": {"claude-opus-5": {"inputTokens": 10, "outputTokens": 20,
                                                   "cacheCreationInputTokens": 0,
                                                   "cacheReadInputTokens": 30,
                                                   "costUSD": 0.5}}}))
"""


def cf_main_paths(ws: Path):
    from cflib.paths import WorkspacePaths
    return WorkspacePaths(Path(ws))


STAGES = ("workspace", "universe", "sourceset", "candidates", "inventory", "locate",
          "challenge", "statement_cut", "export", "bench", "aa", "aa_refusal", "carry",
          "audit")


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
