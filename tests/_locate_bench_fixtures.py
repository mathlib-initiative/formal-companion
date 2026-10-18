"""Synthetic inputs for the bench tests, built by the packages that own each format.

New code.  The bench reads what four other packages write, so these fixtures
build each artifact through its owner wherever one exists: the workspace with
``cf init``; the sourceset with ``sources.sourceset.Sourceset`` and each article
rendered from synthetic HTML by ``sources.render_html``; the universe with
``corpus.universe.Universe`` (libraries, canary, indexes) and its
``corpus.sqlite`` with ``corpus.build_db.build`` over ``decls.jsonl`` indexes.
A harvested run has no single producer to call without a host, so it is written
in the formats references/locate-documents.md sections 9 and 10 fix, with the
harvested layout ``corpus/<run>/<task-id>/<source-id>.json``, a gated
``RECEIPT.json``, the ``run.yaml`` locate block the planner writes, its task
manifests, the outcome rows and tally, and -- when a test asks -- the gate log
and canary scan ``cf harvest`` brings home under ``runs/<run>/host/``.

Every article, library and declaration is made up ("Widget lemma", page 1001,
the libraries ``mathlib`` and ``alpha``), and every tree is written under a
temporary directory.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cf as cf_main  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from cflib.yamlio import dump_yaml  # noqa: E402
from corpus import build_db  # noqa: E402
from corpus.universe import Universe  # noqa: E402
from sources import render_html, websource  # noqa: E402
from sources.sourceset import Sourceset  # noqa: E402

COMMIT_M = "a" * 40
COMMIT_A = "b" * 40
SOURCESET = "wpm"
UNIVERSE = "u1"
TOOLCHAIN = "leanprover/lean4:v4.0.0"
#: what a planner records for a run's own checkout and staged gate files (D6)
RUN_COMMIT = "c" * 40
GATE_FILES = {"verify_locate.py": "d" * 64, "locate_text.py": "e" * 64}


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_workspace(root: Path) -> Path:
    """``cf init``: the workspace's own writer."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = cf_main.main(["init", str(root), "--no-git", "-q"])
    if code != 0:
        raise AssertionError(f"cf init {root} exited {code}")
    return root


def article_paragraphs(n: int) -> list[str]:
    """Four paragraphs per article, each unique to the article."""
    return [
        f"The widget lemma {n} states that every finite widget of order {n} has a spanning gadget.",
        f"Theorem. Let G be a finite widget of rank {n}; then the gadgets of G are conjugate in pairs.",
        f"History of widget {n}: the result was first stated in a letter about gadget counting.",
        f"A gadget {n} is called spanning when it meets every face of the widget exactly once.",
    ]


def article_html(n: int) -> str:
    """A parsoid-like body: a lead with a bold term, a ``Theorems`` heading, three paragraphs."""
    paras = article_paragraphs(n)
    lead = paras[0].replace(f"widget lemma {n}", f"<b>widget lemma {n}</b>", 1)
    return ("<div class=\"mw-parser-output\">\n"
            f"<p>{lead}</p>\n<h2 id=\"Theorems\">Theorems</h2>\n"
            + "".join(f"<p>{p}</p>\n" for p in paras[1:]) + "</div>\n")


#: (library, name, module, proof_status, deprecated new name, tags)
DECLARATIONS = (
    ("mathlib", "Widget.spanning_zero", "Mathlib.Widget.Basic", "proved", None, ()),
    ("mathlib", "Widget.spanning_one", "Mathlib.Widget.Basic", "proved", None, ()),
    ("mathlib", "Widget.mul_spanning", "Mathlib.Widget.Basic", "proved", None, ()),
    ("mathlib", "Widget.add_spanning", "Mathlib.Widget.Basic", "proved", None, ()),
    ("mathlib", "Widget.tagged_two", "Mathlib.Widget.Tags", "proved", None,
     (("wikidata", "Q99999002"),)),
    ("mathlib", "Widget.new_name", "Mathlib.Widget.Basic", "proved", None, ()),
    # a deprecated alias the index knows the new name of (corpus ``deprecated_to``)
    ("mathlib", "Widget.old_alias", "Mathlib.Widget.Alias", "proved", "Widget.canonical", ()),
    ("mathlib", "Widget.canonical", "Mathlib.Widget.Alias", "proved", None, ()),
    ("mathlib", "Widget.conj_three", "Mathlib.Widget.Conj", "proved", None, ()),
    ("mathlib", "Widget.sorry_four", "Mathlib.Widget.Conj", "statement_only", None, ()),
    ("mathlib", "Gadget.Spanning", "Mathlib.Widget.Gadget", "definition", None, ()),
    ("alpha", "Alpha.widget_lemma", "Alpha.Widget", "proved", None, ()),
    ("alpha", "Alpha.unrelated", "Alpha.Other", "proved", None, ()),
)


def decl_record(name: str, module: str, status: str, new_name: str | None, tags) -> dict:
    """One ``decls.jsonl`` record, in the shape ``cf corpus ingest`` writes."""
    record = {"name": name, "module": module, "path": module.replace(".", "/") + ".lean",
              "line": 10, "end_line": 20,
              "kind": "def" if status == "definition" else "theorem",
              "proof_status": status, "statement": False, "printable": True,
              "signature": f"sig of {name}", "docstring": "",
              "tags": [{"tag": t, "value": v} for t, v in tags]}
    if new_name:
        record["deprecated"] = {"new_name": new_name, "since": "2026-01-01"}
    return record


class World:
    """One workspace with a sourceset, a universe and the runs a test asks for."""

    def __init__(self, root: Path, articles: int = 6):
        self.root = write_workspace(root)
        self.paths = WorkspacePaths(self.root)
        self.articles: dict[str, dict] = {}
        ss = Sourceset.create(self.paths, SOURCESET, "enwiki", now="2026-10-17T00:00:00Z")
        family = websource.family("enwiki")
        entries = []
        for i in range(articles):
            pageid, revision = 1001 + i, 2002 + i
            qid = f"Q9999900{i}" if i != 5 else "Q99999000"   # article 5 shares article 0's QID
            entry = websource.entry(family, pageid, revision, title=f"Widget lemma {i}",
                                    wikidata=qid,
                                    assessment={"class": "B",
                                                "importance": "high" if i % 2 else "mid"})
            sid = entry["source_id"]
            body = article_html(i)
            rendering = render_html.render(body)
            sdir = ss.source_dir(sid)
            render_html.write_rendering(sdir, rendering)
            render_html.write_bytes(sdir / "source.raw.html", body.encode("utf-8"))
            entry.update(fetch=websource.fetch_block(),
                         raw_sha256=sha256_bytes((sdir / "source.raw.html").read_bytes()),
                         html_sha256=sha256_bytes((sdir / "body.html").read_bytes()),
                         layer_sha256=sha256_bytes((sdir / "layer.txt").read_bytes()),
                         renderer=render_html.RENDERER)
            entries.append(entry)
            spans = [p["span"] for p in rendering.paragraphs if p["block"] == "p"]
            self.articles[sid] = {"entry": entry, "layer": rendering.layer, "spans": spans,
                                  "index": i}
        self.manifest = ss.write_version(ss.latest(), entries, now="2026-10-17T00:00:01Z")
        self.manifest_sha = self.manifest.sha256
        self.build_universe()

    # -- the universe ---------------------------------------------------------
    def build_universe(self, declarations=DECLARATIONS, *, libraries=("mathlib", "alpha"),
                       mathlib_id: str = "mathlib") -> Universe:
        """``corpora/u1``: universe.yaml, one decls.jsonl index per library, corpus.sqlite."""
        path = self.paths.universe_yaml(UNIVERSE)
        udir = self.paths.universe_dir(UNIVERSE)
        if path.exists():
            path.unlink()
        udir.mkdir(parents=True, exist_ok=True)
        universe = Universe.create(path, UNIVERSE, "2026-10-17")
        ids = {"mathlib": mathlib_id, "alpha": "alpha"}
        for lib in libraries:
            universe.add_library(library_id=ids[lib], repo=f"https://example.org/{lib}",
                                 commit=COMMIT_M if lib == "mathlib" else COMMIT_A,
                                 toolchain=TOOLCHAIN,
                                 roots=["Mathlib" if lib == "mathlib" else "Alpha"],
                                 brain_lib="Mathlib" if lib == "mathlib" else "Alpha")
        universe.record_canary(toolchain=TOOLCHAIN, passed=True, cases={}, index_sha256="0" * 64,
                               checked="2026-10-17T00:00:00Z")
        for lib in libraries:
            rows = [decl_record(name, module, status, new, tags)
                    for owner, name, module, status, new, tags in declarations if owner == lib]
            index = udir / "index" / ids[lib]
            index.mkdir(parents=True, exist_ok=True)
            (index / "decls.jsonl").write_text("".join(canonical(r) + "\n" for r in rows),
                                               encoding="utf-8")
            universe.set_index(ids[lib], path=f"index/{ids[lib]}/",
                               sha256=sha256_bytes((index / "decls.jsonl").read_bytes()),
                               declarations=len(rows))
        db = self.paths.corpus_db(UNIVERSE)
        build_db.build(universe, udir, db, force=True)
        universe.save()
        self.universe_sha = universe.sha256()
        self.corpus_sha = sha256_bytes(db.read_bytes())
        return universe

    def universe(self) -> Universe:
        return Universe.load(self.paths.universe_yaml(UNIVERSE))

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
        # 1: by title; 2: by title under L2's normalisation only ("The", "_"), naming a
        # deprecated alias; 3: by the QID it carries, under a title no article has
        return self.write("inputs/100.yaml",
                          "1:\n  title: Widget lemma 1\n  decls:\n    - Widget.spanning_one\n"
                          "2:\n  title: The Widget_lemma 4\n  decl: Widget.old_alias\n"
                          "3:\n  title: Conjugate gadgets\n  wikidata: Q99999003\n"
                          "  decl: Widget.conj_three\n")

    def renames(self) -> Path:
        return self.write("inputs/renames.txt", "Widget.old_name -> Widget.new_name\n")

    # -- a harvested run ------------------------------------------------------
    def run_record(self, rid: str, mode: str, *, arm: str = "assisted",
                   inventory_run: str | None = None, source_ids: list[str] | None = None,
                   subject_only: bool = False, prompts: dict | None = None,
                   corpus_db_sha: str | None = None, universe_sha: str | None = None,
                   tools: dict | None = None, skill_commit: str | None = RUN_COMMIT,
                   challenge_of: list[str] | None = None) -> None:
        """``runs/<rid>/run.yaml`` and ``gate.json``: what plan-locate and cf run record."""
        locate = {"mode": mode, "arm": arm, "inventory_run": inventory_run,
                  "sourceset": {"id": SOURCESET, "version": self.manifest.version,
                                "manifest_sha256": self.manifest_sha},
                  "universe": {"id": UNIVERSE, "sha256": universe_sha or self.universe_sha,
                               "corpus_db_sha256": corpus_db_sha or self.corpus_sha,
                               "tools_corpus_db": tools or {
                                   "kind": "full", "sha256": corpus_db_sha or self.corpus_sha}},
                  "source_ids": sorted(source_ids or []), "subject_only": subject_only,
                  "gate_files": dict(GATE_FILES)}
        if skill_commit is not None:
            locate["skill_commit"] = skill_commit
        if challenge_of is not None:
            locate["challenged_runs"] = list(challenge_of)
        self.write(f"runs/{rid}/run.yaml", dump_yaml({
            "run_id": rid, "preset": mode, "prompts": prompts or {"worker.md": "e" * 64},
            "versions": {"agent_cli": "2.0.0", "tend": "0.9", "agentbox": "1.1"},
            "locate": locate}))
        self.write(f"runs/{rid}/gate.json", json.dumps({"schema": "cf-gate-locate/1", "mode": mode}))

    def receipt(self, rid: str, *, complete: bool = True, gated: bool | None = True,
                cost: dict | None = None, models: dict | None = None, usd: float = 12.0) -> None:
        """``corpus/<rid>/RECEIPT.json``: complete, gated (``gate.ok``) and priced."""
        block = cost if cost is not None else {
            "usd": usd, "tokens": models or {"claude-opus-test": {"input": 100, "output": 50}},
            "price_table_sha256": "f" * 64, "message_count": 10, "transcript_count": 2}
        gate = None if gated is None else {"ok": bool(gated), "documents": 1}
        self.write(f"corpus/{rid}/RECEIPT.json", json.dumps(
            {"run_id": rid, "complete": complete, "gate": gate, "cost": block}))

    def task_manifest(self, rid: str, task_id: str, mode: str, sids: list[str]) -> None:
        self.write(f"runs/{rid}/tend-root/gate/tasks/{task_id}.json", json.dumps(
            {"task_id": task_id, "mode": mode, "run_id": rid, "curated": [],
             "documents": [{"source_id": s} for s in sids]}))

    def document(self, rid: str, task_id: str, sid: str, doc: dict) -> Path:
        """A harvested document at ``corpus/<run>/<task-id>/<source-id>.json`` (D4)."""
        return self.write(f"corpus/{rid}/{task_id}/{sid}.json", json.dumps(doc))

    def run(self, run_id: str, *, inventory_run: str, passages: dict[str, list[dict]],
            outcomes: list[dict] | None = None, arm: str = "assisted",
            inventory_arm: str | None = None, complete: bool = True, gated: bool | None = True,
            cost: dict | None = None, corpus_db_sha: str | None = None,
            universe_sha: str | None = None, tools: dict | None = None,
            unresolved: set[str] = frozenset(), skip_docs: set[str] = frozenset(),
            planned: list[str] | None = None, locate_sources: list[str] | None = None,
            models: dict | None = None, prompts: dict | None = None,
            renderer_layer: dict | None = None, subject_only: bool = False,
            challenge_runs: list[str] = (), inventory_usd: float = 12.0,
            locate_usd: float = 12.0, skill_commit: str | None = RUN_COMMIT) -> None:
        """An inventory run and a locate run whose outcome rows are ``outcomes``."""
        sids = planned if planned is not None else sorted(passages)
        loc_sids = locate_sources if locate_sources is not None else sorted(passages)
        self.run_record(inventory_run, "locate-inventory", arm=inventory_arm or arm,
                        source_ids=sids, prompts=prompts, corpus_db_sha=corpus_db_sha,
                        universe_sha=universe_sha, skill_commit=skill_commit)
        self.run_record(run_id, "locate", arm=arm, inventory_run=inventory_run,
                        source_ids=loc_sids, subject_only=subject_only, prompts=prompts,
                        corpus_db_sha=corpus_db_sha, universe_sha=universe_sha, tools=tools,
                        skill_commit=skill_commit)
        for rid, usd in ((inventory_run, inventory_usd), (run_id, locate_usd)):
            self.receipt(rid, complete=complete, gated=gated, cost=cost, models=models, usd=usd)
        self.task_manifest(inventory_run, "t-inv", "locate-inventory", sids)
        self.task_manifest(run_id, "t-loc", "locate", loc_sids)
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
            self.document(inventory_run, "t-inv", sid, doc)
        by_source: dict[str, list[dict]] = {}
        for row in outcomes or []:
            if row.get("outcome") and not row.get("missing"):
                by_source.setdefault(row["source_id"], []).append(
                    {"item": row["item"], "outcome": row["outcome"],
                     "locations": row.get("locations") or [],
                     "receipt": row.get("receipt") or {}})
        for sid, rows in sorted(by_source.items()):
            self.document(run_id, "t-loc", sid, {
                "schema": "cf-locate/1", "mode": "locate", "run_id": run_id, "task_id": "t-loc",
                "source_id": sid, "source_sha": "1" * 64,
                "layer_sha256": self.articles[sid]["entry"]["layer_sha256"], "passages": rows})
        self.write(f"reports/locate-outcomes.{run_id}.jsonl",
                   "".join(canonical(r) + "\n" for r in (outcomes or [])))
        self.write(f"reports/locate-tally.{run_id}.json", json.dumps(
            {"run_id": run_id, "challenge_runs": list(challenge_runs)}))

    def challenge_run(self, rid: str, *, of: str, arm: str = "assisted", gated: bool = True,
                      usd: float = 3.0, sids: list[str] = ()) -> None:
        """A harvested challenge run of locate run ``of``."""
        self.run_record(rid, "locate-challenge", arm=arm, source_ids=list(sids), challenge_of=[of])
        self.receipt(rid, gated=gated, usd=usd)

    def host(self, rid: str, *, gate_lines: list[dict] | None = None,
             canary: dict | None = None) -> None:
        """What ``cf harvest`` brings home under ``runs/<rid>/host/`` (D3)."""
        if gate_lines is not None:
            self.write(f"runs/{rid}/host/gate.jsonl",
                       "".join(json.dumps(line) + "\n" for line in gate_lines))
        if canary is not None:
            self.write(f"runs/{rid}/host/canary.json", json.dumps(canary))


def passage(world: World, i: int, k: int, item: str, *, scope: str = "formal_math",
            kind: str = "theorem") -> dict:
    return {"item": item, "span": world.para(i, k), "scope": scope, "passage_kind": kind,
            "prominence": "central", "quote": world.quote(i, k)}


def outcome(world: World, i: int, item: str, *, status: str | None, outcome_: str | None,
            locations: list[tuple] = (), inventory_run: str = "inv1", run_id: str = "loc1",
            scope: str = "formal_math", k: int | None = None, challenge: str | None = "survived",
            missing: bool = False) -> dict:
    """One row of ``reports/locate-outcomes.<run>.jsonl`` (``locate.outcomes.outcome_row``)."""
    return {"source_id": world.sid(i), "item": item, "passage_kind": "theorem",
            "prominence": "central", "scope": scope, "outcome": outcome_,
            "locations": [{"library": lib, "commit": COMMIT_M if lib == "mathlib" else COMMIT_A,
                           "declaration": name, "relation": rel, "rank": rank,
                           "confidence": "high", "proof_status": "proved", "statement": False,
                           "location_key": "k", "challenge": challenge}
                          for rank, (lib, name, rel) in enumerate(locations, 1)],
            "absence": None, "challenge": challenge, "status": status,
            "runs": {"inventory": None if missing else inventory_run, "locate": run_id,
                     "challenge": None},
            "span": world.para(i, k) if k is not None else None, "head_words": None,
            "section": None, "reason": "no document landed for this passage" if missing else "",
            "unresolved_by": None, "missing": missing, "refuted": [],
            "task_id": "t-loc"}
