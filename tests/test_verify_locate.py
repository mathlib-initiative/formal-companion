"""The out-of-tree locate gate: rules 1-8 over one task's own documents.

New code, for docs/proposals/wikipedia-locate.md section 4 and
references/locate-documents.md sections 1-4 and 9.  Every artifact the gate
reads -- a rendered source (layer, formulas, paragraphs), corpus.sqlite, the
L2 candidates file, the task manifests and gate.json -- is built here
synthetically by the documented format, because the packages that write them
are built on other branches.  The article is the synthetic "Widget lemma"
(page id 1001, revision 2002); the libraries are ``alpha`` and ``beta``.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vocab  # noqa: E402
from gate import verify_locate as vl  # noqa: E402

lt = vl.lt
GATE = SCRIPTS / "gate" / "verify_locate.py"
SID = "enwiki-1001-2002"
HTML_SHA = "a" * 64
COMMIT_A = "1" * 40
COMMIT_B = "2" * 40
TASK = "t-inv-0001"

LEAD = ("In widget theory, a widget is spanning when every gadget meets it. The widget "
        "lemma states that every finite widget W{\\displaystyle W} has a spanning gadget.")
THEOREM = ("Theorem. Let G{\\displaystyle G} be a finite widget of order n{\\displaystyle n}; "
           "then G{\\displaystyle G} has a spanning gadget whose size divides "
           "n{\\displaystyle n}.")
HISTORY = "History The lemma was first stated in a synthetic note about widgets."
LAYER = f"Widget lemma {LEAD} {THEOREM} {HISTORY}"


def _formulas(layer: str) -> list[dict]:
    out, at = [], 0
    for text in ("W{\\displaystyle W}", "G{\\displaystyle G}", "n{\\displaystyle n}",
                 "G{\\displaystyle G}", "n{\\displaystyle n}"):
        a = layer.index(text, at)
        out.append({"span": [a, a + len(text)], "alttext": text[1:]})
        at = a + len(text)
    return out


def _span_of(text: str) -> list[int]:
    a = LAYER.index(text)
    return [a, a + len(text)]


LEAD_SPAN = _span_of(LEAD)
THEOREM_SPAN = _span_of(THEOREM)
HISTORY_SPAN = _span_of(HISTORY)


def _ids() -> dict[int, tuple[str, int]]:
    hm = lt.HeadMap(LAYER, _formulas(LAYER))
    starts = [hm.to_head(LEAD_SPAN[0]), hm.to_head(THEOREM_SPAN[0])]
    return {layer: (f"In-text #{h}", w) for layer, (_s, h, w)
            in zip((LEAD_SPAN[0], THEOREM_SPAN[0]), lt.assign_ids(hm.head, starts))}


IDS = _ids()
LEAD_ITEM, LEAD_WORDS = IDS[LEAD_SPAN[0]]
THM_ITEM, THM_WORDS = IDS[THEOREM_SPAN[0]]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}
    for key in [k for k in env if k.startswith("GIT_CONFIG_") and k != "GIT_CONFIG_NOSYSTEM"]:
        env.pop(key)
    out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if out.returncode != 0:  # pragma: no cover - a fixture failure
        raise AssertionError(f"git {args}: {out.stderr}")
    return out.stdout


class Fixture:
    """A rendered source, a corpus, L2, task manifests and gate.json, in one temp dir."""

    def __init__(self, case: unittest.TestCase, mode: str = "locate-inventory", *,
                 curated: bool = True, blind: bool = False):
        # resolved: the gate resolves --root, and on macOS the temporary directory is
        # reached through a symlink (/var -> /private/var)
        self.root = Path(tempfile.mkdtemp()).resolve()
        case.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.mode = mode
        sdir = self.root / "sourcesets" / "wpm" / "sources" / SID
        sdir.mkdir(parents=True)
        (sdir / "layer.txt").write_text(LAYER, encoding="utf-8")
        (sdir / "formulas.json").write_text(json.dumps(_formulas(LAYER)), encoding="utf-8")
        (sdir / "paragraphs.json").write_text(json.dumps([
            {"span": [0, len("Widget lemma")], "section": [], "block": "p", "formulas": 0, "math": False},
            {"span": LEAD_SPAN, "section": [], "block": "p", "formulas": 1, "math": True},
            {"span": THEOREM_SPAN, "section": ["Theorems"], "block": "div", "formulas": 4, "math": True},
            {"span": HISTORY_SPAN, "section": ["History"], "block": "p", "formulas": 0, "math": False},
        ]), encoding="utf-8")
        self.layer_sha = _sha(LAYER.encode("utf-8"))
        self.db = self.root / "corpus.sqlite"
        con = sqlite3.connect(self.db)
        con.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE decls (id INTEGER PRIMARY KEY, library TEXT NOT NULL, commit_sha TEXT NOT NULL,
              name TEXT NOT NULL, module TEXT, path TEXT, line INTEGER, end_line INTEGER, kind TEXT,
              proof_status TEXT NOT NULL, statement INTEGER NOT NULL DEFAULT 0,
              printable INTEGER NOT NULL DEFAULT 1, signature TEXT, docstring TEXT,
              UNIQUE (library, commit_sha, name));
        """)
        for lib, commit, name in (("alpha", COMMIT_A, "Widget.exists_spanning_gadget"),
                                  ("alpha", COMMIT_A, "Widget.Spanning"),
                                  ("beta", COMMIT_B, "Beta.Widget.gadget_of_finite")):
            con.execute("INSERT INTO decls (library, commit_sha, name, proof_status) VALUES (?,?,?,?)",
                        (lib, commit, name, "proved"))
        con.commit()
        con.close()
        cdir = self.root / "candidates"
        cdir.mkdir()
        self.candidates = {"schema": "cf-locate-candidates/1", "source_id": SID,
                           "source_sha": HTML_SHA, "universe": "u1", "universe_sha256": "f" * 64,
                           "article_qid": "Q99999001", "article_title": "Widget lemma",
                           "curated": ([{"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
                                         "via": "wikidata_tag", "evidence": "synthetic"}]
                                       if curated else []),
                           "lexical": []}
        self.write_candidates()
        self.tasks = self.root / "gate" / "tasks"
        self.tasks.mkdir(parents=True)
        self.gate = {
            "schema": "cf-gate-locate/1", "mode": mode, "preset": mode, "run_id": "r-1",
            "kinds": sorted(vocab.kinds_for_mode(mode)),
            "universe": {"id": "u1", "sha256": "f" * 64,
                         "libraries": {"alpha": {"repo": "https://example.org/alpha", "commit": COMMIT_A},
                                       "beta": {"repo": "https://example.org/beta", "commit": COMMIT_B}}},
            "corpus_db": {"path": str(self.db), "sha256": _sha(self.db.read_bytes())},
            "sourceset": {"id": "wpm", "version": 3, "manifest_sha256": "e" * 64,
                          "sources_dir": str(self.root / "sourcesets" / "wpm" / "sources")},
            "sources": {SID: {"html_sha256": HTML_SHA, "layer_sha256": self.layer_sha,
                              "renderer": "web-html/1"}},
            "candidates_dir": str(cdir), "blind": blind,
            "inventory": None,
            "tasks_dir": str(self.tasks), "log_dir": str(self.root / "gate-log"),
            "orchestrator_db": str(self.root / "orchestrator.sqlite"),
            "max_locations": 5, "min_absence_queries": 3,
            "vendor": {pkg: _sha((SKILL / "vendor" / f"{pkg}.manifest.json").read_bytes())
                       for pkg in vl.VENDORED_PACKAGES},
        }
        self.write_gate()

    # -- writers -------------------------------------------------------------
    def write_gate(self) -> Path:
        path = self.root / "gate" / "gate.json"
        path.write_text(json.dumps(self.gate), encoding="utf-8")
        return path

    def write_candidates(self) -> None:
        (self.root / "candidates" / f"{SID}.json").write_text(json.dumps(self.candidates),
                                                             encoding="utf-8")

    def manifest(self, task: str, document: dict) -> Path:
        path = self.tasks / f"{task}.json"
        path.write_text(json.dumps({"task_id": task, "mode": self.mode, "run_id": "r-1",
                                    "documents": [document]}), encoding="utf-8")
        return path

    def header(self, task: str) -> dict:
        h = {"schema": "cf-locate/1", "mode": self.mode, "run_id": "r-1", "task_id": task,
             "source_id": SID, "source_sha": HTML_SHA, "layer_sha256": self.layer_sha,
             "renderer": "web-html/1"}
        if self.mode != "locate-inventory":
            h.update(universe="u1", universe_sha256="f" * 64)
        return h

    def document(self, task: str, body: dict, *, directory: Path | None = None) -> Path:
        directory = directory or self.root / "findings"
        path = directory / task / f"{SID}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**self.header(task), **body}), encoding="utf-8")
        return path

    def gate_cfg(self) -> vl.GateConfig:
        return vl.GateConfig.load(self.root / "gate" / "gate.json")

    def run(self, *paths: Path, **kwargs) -> dict:
        return vl.run_gate(self.gate_cfg(), root=self.root, findings=list(paths), **kwargs)


def inventory_passage(item, words, span, *, finder, prominence, kind, scope="formal_math"):
    exact = LAYER[span[0]:span[1]][:80]
    return {"item": item, "head_words": words, "span": list(span),
            "quote": {"exact": exact, "prefix": LAYER[max(0, span[0] - 12):span[0]], "suffix": ""},
            "section": [], "fragment": None, "passage_kind": kind, "prominence": prominence,
            "scope": scope, "statement": "a synthetic restatement", "concept": None,
            "finder": finder}


def inventory_body() -> dict:
    return {"passages": [
        inventory_passage(LEAD_ITEM, LEAD_WORDS, LEAD_SPAN, finder="lead", prominence="subject",
                          kind="theorem"),
        inventory_passage(THM_ITEM, THM_WORDS, THEOREM_SPAN, finder="theorem_box",
                          prominence="central", kind="theorem")],
        "skips": [{"span": HISTORY_SPAN, "reason": "history"}], "notes": ""}


FINDERS = [{"finder": "lead", "start": LEAD_SPAN[0], "span": LEAD_SPAN, "section": []},
           {"finder": "theorem_box", "start": THEOREM_SPAN[0], "span": THEOREM_SPAN,
            "section": ["Theorems"]}]


def inventory_manifest_doc(fx) -> dict:
    return {"source_id": SID, "source_sha": HTML_SHA, "layer_sha256": fx.layer_sha,
            "renderer": "web-html/1", "finders": copy.deepcopy(FINDERS)}


def locate_manifest_doc(fx) -> dict:
    return {"source_id": SID, "source_sha": HTML_SHA, "layer_sha256": fx.layer_sha,
            "renderer": "web-html/1",
            "passages": [{"item": LEAD_ITEM, "span": LEAD_SPAN, "head_words": LEAD_WORDS,
                          "prominence": "subject", "passage_kind": "theorem"},
                         {"item": THM_ITEM, "span": THEOREM_SPAN, "head_words": THM_WORDS,
                          "prominence": "central", "passage_kind": "theorem"}]}


def location(lib="alpha", decl="Widget.exists_spanning_gadget", relation="exactMatch", rank=1,
             note=""):
    return {"library": lib, "declaration": decl, "relation": relation, "rank": rank,
            "confidence": "high", "note": note, "witness": "states it"}


def locate_body() -> dict:
    return {"passages": [
        {"item": THM_ITEM, "outcome": "located", "locations": [location()],
         "receipt": {"queries": ["widget spanning gadget"],
                     "candidates": [{"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
                                     "verdict": "accepted", "reason": ""}]}},
        {"item": LEAD_ITEM, "outcome": "absent", "locations": [],
         "receipt": {"queries": ["widget lemma", "finite widget spanning", "Widget.spanning"],
                     "candidates": []}}]}


class GateTestCase(unittest.TestCase):

    def assertOk(self, result):
        self.assertEqual(result["exit"], vl.EXIT_OK, result["problems"] + result["broken"])

    def assertRule(self, result, rule: int, needle: str = ""):
        self.assertEqual(result["exit"], vl.EXIT_REJECTED, result)
        hits = [p for p in result["problems"] if p.startswith(f"[rule {rule}]")]
        self.assertTrue(hits, f"no rule {rule} problem in {result['problems']}")
        if needle:
            self.assertTrue(any(needle in p for p in hits), (needle, hits))

    def assertBroken(self, result, needle: str = ""):
        self.assertEqual(result["exit"], vl.EXIT_GATE_BROKEN, result)
        if needle:
            self.assertTrue(any(needle in b for b in result["broken"]), result["broken"])


# --------------------------------------------------------------------------- #
# the inlined vocabulary
# --------------------------------------------------------------------------- #

class TestTheInlinedVocabularyIsCflibs(unittest.TestCase):

    def test_every_set(self):
        pairs = {
            "LOCATE_MODES": vocab.LOCATE_MODES, "PASSAGE_KINDS": vocab.PASSAGE_KINDS,
            "PROMINENCE": vocab.PROMINENCE, "SCOPES": vocab.SCOPES,
            "LOCATED_SCOPES": vocab.LOCATED_SCOPES, "LOCATE_RELATIONS": vocab.LOCATE_RELATIONS,
            "LOCATE_OUTCOMES": vocab.LOCATE_OUTCOMES, "FINDERS": vocab.FINDERS,
            "DETERMINISTIC_FINDERS": vocab.DETERMINISTIC_FINDERS, "CONFIDENCES": vocab.CONFIDENCES,
            "CANDIDATE_VERDICTS": vocab.CANDIDATE_VERDICTS,
            "CHALLENGE_TARGETS": vocab.CHALLENGE_TARGETS, "CHALLENGE_ACTIONS": vocab.CHALLENGE_ACTIONS,
        }
        for name, theirs in pairs.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(vl, name), theirs)
        for mode in vocab.LOCATE_MODES:
            self.assertEqual(vl.KINDS_BY_MODE[mode], vocab.kinds_for_mode(mode))
        self.assertEqual(vl.MAX_LOCATIONS_PER_PASSAGE, vocab.MAX_LOCATIONS_PER_PASSAGE)
        self.assertEqual(vl.MIN_ABSENCE_QUERIES, vocab.MIN_ABSENCE_QUERIES)
        self.assertEqual(vl.LOCATE_EXTENSION, vocab.LOCATE_EXTENSION)
        self.assertEqual(vl.NO_MATCH, vocab.NO_MATCH)
        self.assertEqual(vl.DOCUMENT_SCHEMA, vocab.LOCATE_DOCUMENT_SCHEMA)
        self.assertEqual(vl.UNRESOLVED_BY, {"worker", vocab.UNRESOLVED_ATTEMPT_CAP})
        self.assertEqual(set().union(*vl.CHALLENGE_COMBOS.values()), vocab.CHALLENGE_ACTIONS)
        self.assertEqual(set(vl.CHALLENGE_COMBOS), vocab.CHALLENGE_TARGETS)

    def test_the_gate_imports_no_cflib(self):
        import ast

        tree = ast.parse(GATE.read_text(encoding="utf-8"))
        modules = {(n.module or "") if isinstance(n, ast.ImportFrom) else a.name
                   for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                   for a in (n.names if isinstance(n, ast.Import) else [None])}
        self.assertFalse({m for m in modules if m.split(".")[0] in ("cflib", "cfcmd")}, modules)


# --------------------------------------------------------------------------- #
# inventory documents
# --------------------------------------------------------------------------- #

class TestInventory(GateTestCase):

    def setUp(self):
        self.fx = Fixture(self, "locate-inventory")
        self.fx.manifest(TASK, inventory_manifest_doc(self.fx))

    def doc(self, body=None):
        return self.fx.document(TASK, body or inventory_body())

    def test_a_good_inventory_passes(self):
        self.assertOk(self.fx.run(self.doc()))

    def test_the_item_is_recomputed(self):
        body = inventory_body()
        body["passages"][1]["item"] = "In-text #00000000"
        self.assertRule(self.fx.run(self.doc(body)), 2, "is In-text #")
        body = inventory_body()
        body["passages"][1]["item"] = THM_ITEM.replace("#", "#  ")
        self.assertRule(self.fx.run(self.doc(body)), 1, "as the stages write it")

    def test_head_words_must_be_the_shortest_unique_head(self):
        body = inventory_body()
        body["passages"][0]["head_words"] = LEAD_WORDS + 3
        self.assertRule(self.fx.run(self.doc(body)), 2, "-word head")

    def test_a_quote_matching_twice_or_elsewhere_is_refused(self):
        body = inventory_body()
        body["passages"][0]["quote"] = {"exact": "widget", "prefix": "", "suffix": ""}
        self.assertRule(self.fx.run(self.doc(body)), 2, "times")
        body = inventory_body()
        body["passages"][0]["quote"] = {"exact": LAYER[LEAD_SPAN[0] + 3:LEAD_SPAN[0] + 60],
                                        "prefix": "", "suffix": ""}
        self.assertRule(self.fx.run(self.doc(body)), 2, "starts the passage")

    def test_a_passage_inside_a_formula_is_refused(self):
        body = inventory_body()
        start = _formulas(LAYER)[1]["span"][0] + 1
        body["passages"][1]["span"] = [start, THEOREM_SPAN[1]]
        body["passages"][1]["quote"] = {"exact": LAYER[start:start + 30]}
        self.assertRule(self.fx.run(self.doc(body)), 2)

    def test_a_dropped_finder_passage_needs_a_skip(self):
        body = inventory_body()
        del body["passages"][1]
        result = self.fx.run(self.doc(body))
        self.assertRule(result, 8, "theorem_box passage")
        self.assertRule(result, 8, "coverage floor")
        body["skips"].append({"span": THEOREM_SPAN, "reason": "a statement of a lemma elsewhere"})
        self.assertOk(self.fx.run(self.doc(body)))

    def test_a_claimed_finder_must_be_the_finders(self):
        body = inventory_body()
        body["passages"][0]["finder"] = "theorem_box"
        self.assertRule(self.fx.run(self.doc(body)), 8, "is claimed")

    def test_the_coverage_floor(self):
        fx = self.fx
        paragraphs = json.loads((fx.root / "sourcesets/wpm/sources" / SID / "paragraphs.json").read_text())
        paragraphs[3]["math"] = True
        (fx.root / "sourcesets/wpm/sources" / SID / "paragraphs.json").write_text(json.dumps(paragraphs))
        body = inventory_body()
        body["skips"] = []
        self.assertRule(fx.run(self.doc(body)), 8, "coverage floor")

    def test_curated_hits_keep_the_subject_in_scope(self):
        body = inventory_body()
        body["passages"][0]["scope"] = "expository"
        self.assertRule(self.fx.run(self.doc(body)), 4, "subject passage")
        for p in body["passages"]:
            p["scope"] = "non_math"
        self.assertRule(self.fx.run(self.doc(body)), 4, "keeps a formal_math passage")

    def test_the_blind_arm_turns_rule_four_off(self):
        self.fx.gate["blind"] = True
        self.fx.write_gate()
        body = inventory_body()
        body["passages"][0]["scope"] = "expository"
        self.assertOk(self.fx.run(self.doc(body)))

    def test_the_pins(self):
        doc = self.doc()
        data = json.loads(doc.read_text())
        data["source_sha"] = "b" * 64
        doc.write_text(json.dumps(data))
        self.assertRule(self.fx.run(doc), 2, "pinned sourceset")
        data = json.loads(doc.read_text())
        data.update(source_sha=HTML_SHA, universe="u1")
        doc.write_text(json.dumps(data))
        self.assertRule(self.fx.run(doc), 1, "no universe")

    def test_a_layer_that_is_not_the_pin_breaks_the_gate(self):
        (self.fx.root / "sourcesets/wpm/sources" / SID / "layer.txt").write_text(LAYER + " ", encoding="utf-8")
        self.assertBroken(self.fx.run(self.doc()), "not the rendering")

    def test_proof_status_and_commits_are_not_a_workers(self):
        body = inventory_body()
        body["passages"][0]["proof_status"] = "proved"
        self.assertRule(self.fx.run(self.doc(body)), 3, "proof_status")

    def test_strict_json(self):
        path = self.doc()
        text = path.read_text()
        path.write_text(text[:-1] + ', "notes": "again"}')
        self.assertRule(self.fx.run(path), 1, "duplicate key")

    def test_an_unresolved_inventory_is_valid_and_carries_nothing(self):
        body = {"passages": [], "skips": [], "unresolved": {"reason": "attempt cap: 3 rejections",
                                                            "by": "attempt_cap"}}
        result = self.fx.run(self.doc(body))
        self.assertOk(result)
        self.assertTrue(result["stats"][0]["attempt_capped"])
        body["passages"] = inventory_body()["passages"]
        self.assertRule(self.fx.run(self.doc(body)), 1, "carries no passages")
        body = {"passages": [], "unresolved": {"reason": "", "by": "someone"}}
        self.assertRule(self.fx.run(self.doc(body)), 1, "unresolved must be")

    def test_the_document_must_be_its_tasks(self):
        path = self.fx.document("t-not-planned", inventory_body())
        self.assertRule(self.fx.run(path), 7, "has no manifest")

    def test_directory_mode_and_the_empty_set(self):
        self.doc()
        result = vl.run_gate(self.fx.gate_cfg(), root=self.fx.root, findings_dir="findings")
        self.assertOk(result)
        self.assertEqual(result["documents"], 1)
        result = vl.run_gate(self.fx.gate_cfg(), root=self.fx.root, findings_dir="nothing-here")
        self.assertRule(result, 1, "empty set")

    def test_only_the_sources_named_are_loaded(self):
        """A second pinned source whose files are missing does not matter to this document."""
        self.fx.gate["sources"]["enwiki-1003-2004"] = {"html_sha256": "c" * 64,
                                                       "layer_sha256": "d" * 64,
                                                       "renderer": "web-html/1"}
        self.fx.write_gate()
        self.assertOk(self.fx.run(self.doc()))


# --------------------------------------------------------------------------- #
# locate documents
# --------------------------------------------------------------------------- #

class TestLocate(GateTestCase):

    TASK = "t-loc-0001"

    def setUp(self):
        self.fx = Fixture(self, "locate")
        self.fx.manifest(self.TASK, locate_manifest_doc(self.fx))

    def doc(self, body=None):
        return self.fx.document(self.TASK, body or locate_body())

    def test_a_good_locate_document_passes(self):
        self.assertOk(self.fx.run(self.doc()))

    def test_a_phantom_or_foreign_declaration_is_refused(self):
        body = locate_body()
        body["passages"][0]["locations"] = [location(decl="Widget.no_such_lemma")]
        self.assertRule(self.fx.run(self.doc(body)), 3, "phantom")
        body["passages"][0]["locations"] = [location(lib="gamma")]
        self.assertRule(self.fx.run(self.doc(body)), 3, "not in universe")
        body = locate_body()
        body["passages"][0]["receipt"]["candidates"].append(
            {"library": "beta", "declaration": "Beta.nope", "verdict": "accepted", "reason": ""})
        self.assertRule(self.fx.run(self.doc(body)), 3, "accepted candidate")

    def test_a_commit_written_by_a_worker_is_refused(self):
        body = locate_body()
        body["passages"][0]["locations"][0]["commit"] = COMMIT_A
        self.assertRule(self.fx.run(self.doc(body)), 3, "universe's")

    def test_the_location_bounds(self):
        body = locate_body()
        body["passages"][0]["locations"] = [location(rank=r) for r in range(1, 7)]
        self.assertRule(self.fx.run(self.doc(body)), 6, "at most 5")
        body["passages"][0]["locations"] = [location(rank=1), location(decl="Widget.Spanning", rank=1)]
        self.assertRule(self.fx.run(self.doc(body)), 6, "not distinct")
        body["passages"][0]["locations"] = [location(relation="closeMatch")]
        self.assertRule(self.fx.run(self.doc(body)), 6, "closeMatch")

    def test_an_absence_needs_three_queries_and_the_universe(self):
        body = locate_body()
        body["passages"][1]["receipt"]["queries"] = ["widget lemma", "Widget  lemma", "WIDGET LEMMA"]
        self.assertRule(self.fx.run(self.doc(body)), 5, "1 distinct query")
        path = self.doc()
        data = json.loads(path.read_text())
        data["universe_sha256"] = "0" * 64
        path.write_text(json.dumps(data))
        self.assertRule(self.fx.run(path), 5, "frozen universe")

    def test_outcomes_and_their_locations(self):
        body = locate_body()
        body["passages"][0]["locations"] = [location(relation="relatedMatch")]
        self.assertRule(self.fx.run(self.doc(body)), 1, "relation counts")
        body = locate_body()
        body["passages"][1]["locations"] = [location(decl="Widget.Spanning", relation="hasPart")]
        self.assertRule(self.fx.run(self.doc(body)), 1, "relatedMatch locations only")
        body["passages"][1]["locations"] = [location(decl="Widget.Spanning", relation="relatedMatch")]
        self.assertOk(self.fx.run(self.doc(body)))

    def test_exactly_the_tasks_passages(self):
        body = locate_body()
        del body["passages"][1]
        self.assertRule(self.fx.run(self.doc(body)), 2, "have no outcome")
        body = locate_body()
        body["passages"].append(dict(body["passages"][1], item="In-text #0badc0de"))
        self.assertRule(self.fx.run(self.doc(body)), 2, "not one of this task's")

    def test_every_curated_hit_is_located_or_rejected(self):
        body = locate_body()
        body["passages"][0] = {"item": THM_ITEM, "outcome": "unresolved", "locations": [],
                               "reason": "ambiguous", "receipt": {"queries": [], "candidates": []}}
        self.assertRule(self.fx.run(self.doc(body)), 4, "end absent")
        body["passages"][0]["receipt"]["candidates"] = [
            {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
             "verdict": "rejected", "reason": "states the infinite case"}]
        self.assertOk(self.fx.run(self.doc(body)))

    def test_the_records_pass_the_vendored_checker(self):
        body = locate_body()
        body["passages"][0]["locations"][0]["note"] = "\ud800 a lone surrogate"
        self.assertRule(self.fx.run(self.doc(body)), 1, "Literature Links checker")

    def test_an_attempt_capped_document(self):
        body = {"passages": [{"item": i, "outcome": "unresolved", "locations": [],
                              "receipt": {"queries": [], "candidates": []}}
                             for i in (LEAD_ITEM, THM_ITEM)],
                "unresolved": {"reason": "attempt cap", "by": "attempt_cap"}}
        self.assertOk(self.fx.run(self.doc(body)))
        body["passages"][0]["outcome"] = "absent"
        self.assertRule(self.fx.run(self.doc(body)), 1, "every passage unresolved")

    def test_an_inconsistent_frozen_inventory_breaks_the_gate(self):
        doc = locate_manifest_doc(self.fx)
        doc["passages"][0]["head_words"] = LEAD_WORDS + 1
        doc["passages"][0]["item"] = "In-text #12345678"
        self.fx.manifest(self.TASK, doc)
        self.assertBroken(self.fx.run(self.doc()), "frozen inventory")

    def test_infrastructure_failures_are_exit_three(self):
        self.fx.db.unlink()
        self.assertBroken(self.fx.run(self.doc()), "corpus.sqlite is missing")
        fx = Fixture(self, "locate")
        fx.manifest(self.TASK, locate_manifest_doc(fx))
        (fx.root / "candidates" / f"{SID}.json").unlink()
        self.assertBroken(fx.run(fx.document(self.TASK, locate_body())), "no candidates file")
        fx = Fixture(self, "locate")
        fx.manifest(self.TASK, locate_manifest_doc(fx))
        fx.gate["vendor"]["literature_links"] = "0" * 64
        fx.write_gate()
        self.assertBroken(fx.run(fx.document(self.TASK, locate_body())), "not the release")
        shutil.rmtree(fx.tasks)
        fx.gate["vendor"]["literature_links"] = _sha(
            (SKILL / "vendor" / "literature_links.manifest.json").read_bytes())
        fx.write_gate()
        self.assertBroken(fx.run(fx.document(self.TASK, locate_body())), "task-manifest directory")


# --------------------------------------------------------------------------- #
# challenge documents
# --------------------------------------------------------------------------- #

class TestChallenge(GateTestCase):

    TASK = "t-chl-0001"
    KEY = "9" * 64

    def setUp(self):
        self.fx = Fixture(self, "locate-challenge")
        doc = locate_manifest_doc(self.fx)
        doc.pop("passages")
        doc["records"] = [{"item": THM_ITEM, "target": "location", "location_key": self.KEY,
                           "span": THEOREM_SPAN, "passage_kind": "theorem"},
                          {"item": LEAD_ITEM, "target": "absence", "span": LEAD_SPAN,
                           "passage_kind": "theorem"}]
        self.fx.manifest(self.TASK, doc)

    def body(self):
        return {"verdicts": [
            {"item": THM_ITEM, "target": "location", "location_key": self.KEY,
             "action": "confirm", "witness": "the signature states it"},
            {"item": LEAD_ITEM, "target": "absence", "action": "overturn",
             "locations": [location(lib="beta", decl="Beta.Widget.gadget_of_finite")],
             "witness": "found with a type search"}]}

    def test_a_good_challenge_passes(self):
        self.assertOk(self.fx.run(self.fx.document(self.TASK, self.body())))

    def test_records_and_combinations(self):
        body = self.body()
        body["verdicts"][0]["location_key"] = "8" * 64
        self.assertRule(self.fx.run(self.fx.document(self.TASK, body)), 1, "not one of the records")
        body = self.body()
        body["verdicts"][0]["action"] = "overturn"
        self.assertRule(self.fx.run(self.fx.document(self.TASK, body)), 1, "does not apply")
        body = self.body()
        del body["verdicts"][1]
        self.assertRule(self.fx.run(self.fx.document(self.TASK, body)), 1, "no verdict")
        body = self.body()
        body["verdicts"][1]["locations"] = [location(decl="Widget.phantom")]
        self.assertRule(self.fx.run(self.fx.document(self.TASK, body)), 3, "phantom")
        body = self.body()
        body["verdicts"][0]["action"] = "downgrade"
        self.assertRule(self.fx.run(self.fx.document(self.TASK, body)), 1, "weaker relation")


# --------------------------------------------------------------------------- #
# rule 7, the CLI's last line, the log
# --------------------------------------------------------------------------- #

class TestOwnership(GateTestCase):

    def setUp(self):
        self.fx = Fixture(self, "locate-inventory")
        self.fx.manifest(TASK, inventory_manifest_doc(self.fx))
        self.fx.manifest("t-inv-0002", inventory_manifest_doc(self.fx))
        self.wt = self.fx.root / "worktree"
        self.wt.mkdir()
        git(self.wt, "init", "-q", "-b", "main")
        (self.wt / "tasks").mkdir()
        for task in (TASK, "t-inv-0002"):
            (self.wt / "tasks" / f"{task}.yaml").write_text(f"id: {task}\nstatus: open\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-qm", "tasks")
        git(self.wt, "checkout", "-q", "-b", TASK)

    def contribute(self, task=TASK):
        self.fx.document(task, inventory_body(), directory=self.wt / "findings")
        (self.wt / "tasks" / f"{task}.yaml").write_text(f"id: {task}\nstatus: complete\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-qm", "mine")

    def cli(self, *extra, env=None):
        environ = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
        environ.pop("TEND_TASK_ID", None)
        environ.update(env or {})
        return subprocess.run([sys.executable, str(GATE), "--gate-config",
                               str(self.fx.root / "gate" / "gate.json"), "--root", str(self.wt),
                               "--changed-files", "--base", "main", *extra],
                              capture_output=True, text=True, env=environ)

    def test_own_documents_pass_and_the_last_line_names_the_rule(self):
        self.contribute()
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"ownership checked against task {TASK}", proc.stdout.splitlines()[-1])
        proc = self.cli()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("single-stem rule", proc.stdout.splitlines()[-1])

    def test_a_neighbours_or_a_stray_path_is_refused(self):
        self.contribute()
        (self.wt / "scripts").mkdir()
        (self.wt / "scripts" / "x.py").write_text("print(1)\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-qm", "stray")
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("is outside findings/<task-id>/<source-id>.json", proc.stderr)
        self.assertIn("ownership checked against", proc.stdout.splitlines()[-1])

    def test_another_tasks_document_is_refused_with_a_task_id(self):
        self.contribute()
        self.contribute("t-inv-0002")
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("belongs to task 't-inv-0002'", proc.stderr)

    def test_a_deletion_is_refused(self):
        git(self.wt, "checkout", "-q", "main")
        self.fx.document("t-inv-0002", inventory_body(), directory=self.wt / "findings")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-qm", "landed")
        git(self.wt, "checkout", "-q", "-B", TASK)
        self.contribute()
        (self.wt / "findings" / "t-inv-0002" / f"{SID}.json").unlink()
        proc = self.cli()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("deleted", proc.stderr)

    def test_the_task_id_from_orchestrator_sqlite(self):
        self.contribute()
        con = sqlite3.connect(self.fx.root / "orchestrator.sqlite")
        con.execute("CREATE TABLE worktrees (id TEXT, task_id TEXT, path TEXT, status TEXT)")
        con.execute("INSERT INTO worktrees VALUES ('w1', ?, ?, 'running')", (TASK, str(self.wt)))
        con.execute("INSERT INTO worktrees VALUES ('w2', 't-inv-0002', '/elsewhere', 'running')")
        con.commit()
        con.close()
        proc = self.cli()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("orchestrator.sqlite", proc.stdout.splitlines()[-1])
        self.assertIn(f"against task {TASK}", proc.stdout.splitlines()[-1])
        task, how = vl.task_from_orchestrator(self.fx.root / "orchestrator.sqlite", Path("/nowhere"))
        self.assertEqual(task, "")
        self.assertIn("no task", how)

    def test_a_live_writers_wal_row_is_seen(self):
        """G1: tend writes orchestrator.sqlite in WAL mode and the gate runs mid-run.

        The writer below is the shape of tend's: WAL journal, its connection held
        open, no checkpoint since the worktree row was committed, so the row lives
        only in ``-wal``.  A read of the main file alone (what ``immutable=1``
        does) finds no table at all; the gate must still name the task.
        """
        self.contribute()
        db = self.fx.root / "orchestrator.sqlite"
        writer = sqlite3.connect(db, isolation_level=None)
        self.addCleanup(writer.close)
        self.assertEqual(writer.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE worktrees (worktree_id TEXT PRIMARY KEY, task_id TEXT, "
                       "path TEXT, state TEXT)")
        writer.execute("INSERT INTO worktrees VALUES ('w-1', ?, ?, 'worker_running')",
                       (TASK, str(self.wt)))
        writer.execute("INSERT INTO worktrees VALUES ('w-2', 't-inv-0002', '/elsewhere/w-2', "
                       "'pending')")
        self.assertTrue(Path(f"{db}-wal").is_file())
        frozen = sqlite3.connect(f"file:{db.as_posix()}?mode=ro&immutable=1", uri=True)
        try:   # the reproduction: the main file alone does not hold the row yet
            self.assertEqual(frozen.execute("SELECT name FROM sqlite_master").fetchall(), [])
        finally:
            frozen.close()
        task, how = vl.task_from_orchestrator(db, self.wt)
        self.assertEqual((task, how), (TASK, "orchestrator.sqlite"))
        proc = self.cli()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"against task {TASK}", proc.stdout.splitlines()[-1])
        self.assertIn("orchestrator.sqlite", proc.stdout.splitlines()[-1])
        # the lookup took no lock the writer then waits on
        writer.execute("INSERT INTO worktrees VALUES ('w-3', 't-inv-0003', '/elsewhere/w-3', "
                       "'pending')")

    def test_a_path_recorded_through_a_symlink_is_this_worktree(self):
        """tend records the path it was configured with; the gate is handed the resolved cwd."""
        self.contribute()
        link = self.fx.root / "linked-root"
        link.symlink_to(self.fx.root, target_is_directory=True)
        db = self.fx.root / "orchestrator.sqlite"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE worktrees (worktree_id TEXT, task_id TEXT, path TEXT)")
        con.execute("INSERT INTO worktrees VALUES ('w-1', ?, ?)", (TASK, f"{link}/worktree/"))
        # another root's worktree of the same name is not this one
        con.execute("INSERT INTO worktrees VALUES ('w-9', 't-inv-0002', '/elsewhere/worktree')")
        con.commit()
        con.close()
        self.assertEqual(vl.task_from_orchestrator(db, self.wt.resolve()),
                         (TASK, "orchestrator.sqlite"))
        self.assertEqual(vl.task_from_orchestrator(db, Path("/nowhere/worktree"))[0], "")

    def test_an_ambiguous_orchestrator_falls_back_to_the_single_stem_rule(self):
        self.contribute()
        con = sqlite3.connect(self.fx.root / "orchestrator.sqlite")
        con.execute("CREATE TABLE runs (worktree_path TEXT, task TEXT)")
        con.execute("INSERT INTO runs VALUES (?, 'a'), (?, 'b')", (str(self.wt), str(self.wt)))
        con.commit()
        con.close()
        proc = self.cli()
        self.assertIn("single-stem rule", proc.stdout.splitlines()[-1])

    def test_the_cli_exit_codes_and_the_log(self):
        self.contribute()
        proc = subprocess.run([sys.executable, str(GATE), "--gate-config",
                               str(self.fx.root / "nope.json")], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("not a finding of yours", proc.stderr)
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 0)
        lines = (self.fx.root / "gate-log" / "gate.jsonl").read_text().splitlines()
        record = json.loads(lines[-1])
        self.assertEqual((record["task_id"], record["ok"], record["mode"]),
                         (TASK, True, "locate-inventory"))

    def test_an_unwritable_log_never_fails_the_gate(self):
        self.contribute()
        blocker = self.fx.root / "blocker"
        blocker.write_text("a file, not a directory")
        self.fx.gate["log_dir"] = str(blocker / "gate-log")
        self.fx.write_gate()
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
