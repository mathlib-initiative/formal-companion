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
from unittest import mock

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


#: the reason the bridge writes at the attempt cap (attempt_cap.cap_reason(3))
CAP_REASON = ("attempt_cap: 3 genuine reviewer rejections; the bridge ended the task without "
              "another agent call")


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
        self.assertEqual(vl.CHALLENGE_COMBOS[vocab.COVERAGE_TARGET], vocab.COVERAGE_ACTIONS)

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
        body = {"passages": [], "skips": [], "unresolved": {"reason": CAP_REASON,
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
        with mock.patch.object(vl.lr, "check", return_value=["SCHEMA: a synthetic problem"]):
            self.assertRule(self.fx.run(self.doc(body)), 1, "Literature Links checker")
        # a lone surrogate never gets that far: it is not UTF-8 text at all
        body["passages"][0]["locations"][0]["note"] = "\ud800 a lone surrogate"
        self.assertRule(self.fx.run(self.doc(body)), 1, "lone surrogate")

    def test_an_attempt_capped_document(self):
        body = {"passages": [{"item": i, "outcome": "unresolved", "locations": [],
                              "receipt": {"queries": [], "candidates": []}}
                             for i in (LEAD_ITEM, THM_ITEM)],
                "unresolved": {"reason": CAP_REASON, "by": "attempt_cap"}}
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
                           "span": THEOREM_SPAN, "passage_kind": "theorem",
                           "relation": "broadMatch"},
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

    def test_a_downgrade_names_a_weaker_relation_than_the_records(self):
        """Review #59: the record is a broadMatch; exactMatch or broadMatch is no downgrade."""
        for relation, ok in (("exactMatch", False), ("broadMatch", False),
                             ("narrowMatch", True), ("relatedMatch", True)):
            body = self.body()
            body["verdicts"][0].update(action="downgrade", relation=relation)
            result = self.fx.run(self.fx.document(self.TASK, body))
            with self.subTest(relation=relation):
                if ok:
                    self.assertOk(result)
                else:
                    self.assertRule(result, 1, "weaker than the record's broadMatch")
        doc = json.loads((self.fx.tasks / f"{self.TASK}.json").read_text())["documents"][0]
        del doc["records"][0]["relation"]
        self.fx.manifest(self.TASK, doc)
        body = self.body()
        body["verdicts"][0].update(action="downgrade", relation="relatedMatch")
        self.assertBroken(self.fx.run(self.fx.document(self.TASK, body)), "carries no relation")


# --------------------------------------------------------------------------- #
# rule 7, the CLI's last line, the log
# --------------------------------------------------------------------------- #

class OwnershipCase(GateTestCase):
    """A tend-like worktree: main holds the task files, the task's branch its contribution."""

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


class TestOwnership(OwnershipCase):

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
        # tend records the worktree's real path, the one git reports (on macOS the
        # temporary directory /var/folders/... resolves to /private/var/folders/...)
        con.execute("INSERT INTO worktrees VALUES ('w1', ?, ?, 'running')",
                    (TASK, str(self.wt.resolve())))
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
        # and the line says why the lookup found no task, instead of dropping it
        self.assertIn("(orchestrator.sqlite names 2 tasks for this worktree)",
                      proc.stdout.splitlines()[-1])

    def live_writer(self) -> Path:
        """tend's shape: WAL, the writer's connection open, the rows only in ``-wal``."""
        db = self.fx.root / "orchestrator.sqlite"
        writer = sqlite3.connect(db, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE worktrees (worktree_id TEXT, task_id TEXT, path TEXT)")
        writer.execute("INSERT INTO worktrees VALUES ('w-1', ?, ?)", (TASK, str(self.wt)))
        self.assertTrue(Path(f"{db}-wal").is_file())
        return db

    def failing_live_read(self):
        """``sqlite3.connect`` as the gate sees it when the ``mode=ro`` read fails (a busy
        timeout, say); an ``immutable=1`` open still works."""
        real = sqlite3.connect

        def connect(database, *a, **kw):
            if str(database).endswith("?mode=ro"):
                raise sqlite3.OperationalError("database is locked")
            return real(database, *a, **kw)
        return mock.patch.object(vl.sqlite3, "connect", connect)

    def test_g1_a_failed_live_read_is_never_silently_an_immutable_one(self):
        """The fallback read the main file alone on ANY failure of the ``mode=ro`` read --
        on a live WAL database, exactly the G1 failure, with no word of it."""
        self.contribute()
        db = self.live_writer()
        with self.failing_live_read():
            task, how = vl.task_from_orchestrator(db, self.wt)
        self.assertEqual(task, "")
        self.assertIn("will not open", how)
        self.assertIn("would miss the rows in its -wal", how)
        self.assertIn("ownership: single-stem rule (no task id)",
                      vl.ownership_line(task, how))
        self.assertIn("would miss the rows in its -wal", vl.ownership_line(task, how))

    def test_g1_an_unwritable_directory_is_read_immutably_and_says_so(self):
        self.contribute()
        db = self.live_writer()
        real_access = os.access
        unwritable = mock.patch.object(vl.os, "access", lambda path, mode: False if (
            Path(path) == db.parent and mode == os.W_OK) else real_access(path, mode))
        with self.failing_live_read(), unwritable:
            task, how = vl.task_from_orchestrator(db, self.wt)
        self.assertEqual(task, "")                      # the row is in -wal, unread
        self.assertIn(vl.IMMUTABLE_NOTE, how)
        self.assertIn(vl.IMMUTABLE_NOTE, vl.ownership_line(task, how))
        # with the rows checkpointed into the main file, the same read finds the task
        con = sqlite3.connect(db)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
        with self.failing_live_read(), unwritable:
            task, how = vl.task_from_orchestrator(db, self.wt)
        self.assertEqual(task, TASK)
        self.assertIn("verify on the host; " + vl.IMMUTABLE_NOTE, vl.ownership_line(task, how))

    def test_g1_with_no_wal_the_immutable_read_is_complete(self):
        self.contribute()
        db = self.fx.root / "orchestrator.sqlite"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE worktrees (worktree_id TEXT, task_id TEXT, path TEXT)")
        con.execute("INSERT INTO worktrees VALUES ('w-1', ?, ?)", (TASK, str(self.wt)))
        con.commit()
        con.close()
        self.assertFalse(Path(f"{db}-wal").exists())
        with self.failing_live_read():
            self.assertEqual(vl.task_from_orchestrator(db, self.wt), (TASK, "orchestrator.sqlite"))

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

    def last_log(self) -> dict:
        lines = (self.fx.root / "gate-log" / "gate.jsonl").read_text().splitlines()
        return json.loads(lines[-1])

    def test_w1_the_log_says_how_the_task_id_was_obtained(self):
        """Field watch item W1: an agent runs with TEND_TASK_ID in its environment, so its
        own gate calls logged a task id, and a non-empty task_id was taken for proof
        that the validation command's orchestrator.sqlite lookup worked.  Each line
        now carries the ownership line, where the id came from and --changed-files."""
        self.contribute()
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rec = self.last_log()
        self.assertEqual((rec["task_id"], rec["task_id_from"], rec["changed_files"]),
                         (TASK, "--task-id", True))
        self.assertEqual(rec["ownership"],
                         f"ownership checked against task {TASK} (task id from --task-id)")
        self.assertIn(rec["ownership"], proc.stdout.splitlines()[-1])

        proc = self.cli(env={"TEND_TASK_ID": TASK})          # what an agent's own call does
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rec = self.last_log()
        self.assertEqual((rec["task_id"], rec["task_id_from"]), (TASK, "$TEND_TASK_ID"))
        self.assertIn("(task id from $TEND_TASK_ID)", rec["ownership"])

        proc = self.cli()                                    # no id, no orchestrator table
        rec = self.last_log()
        self.assertEqual((rec["task_id"], rec["task_id_from"], rec["changed_files"]),
                         ("", None, True))
        self.assertIn("single-stem rule", rec["ownership"])
        self.assertTrue(rec["ownership_note"])

    def test_w1_the_orchestrator_lookup_and_a_plain_validation_are_logged_as_such(self):
        self.contribute()
        con = sqlite3.connect(self.fx.root / "orchestrator.sqlite")
        con.execute("CREATE TABLE worktrees (id TEXT, task_id TEXT, path TEXT)")
        con.execute("INSERT INTO worktrees VALUES ('w1', ?, ?)", (TASK, str(self.wt.resolve())))
        con.commit()
        con.close()
        self.assertEqual(self.cli().returncode, 0)
        rec = self.last_log()
        self.assertEqual((rec["task_id"], rec["task_id_from"]), (TASK, "orchestrator.sqlite"))
        self.assertIn("recovered from orchestrator.sqlite", rec["ownership"])
        doc = self.wt / "findings" / TASK / f"{SID}.json"
        environ = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
        environ.pop("TEND_TASK_ID", None)
        proc = subprocess.run([sys.executable, str(GATE), "--gate-config",
                               str(self.fx.root / "gate" / "gate.json"), "--findings", str(doc)],
                              capture_output=True, text=True, env=environ)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rec = self.last_log()
        self.assertEqual((rec["changed_files"], rec["ownership"], rec["task_id_from"]),
                         (False, None, None))

    def test_an_unwritable_log_never_fails_the_gate(self):
        self.contribute()
        blocker = self.fx.root / "blocker"
        blocker.write_text("a file, not a directory")
        self.fx.gate["log_dir"] = str(blocker / "gate-log")
        self.fx.write_gate()
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_vendor_pin_that_does_not_match(self):
        """Review #61: --changed-files that broke says the gate could not run."""
        self.contribute()
        self.fx.gate["vendor"] = dict(self.fx.gate["vendor"], literature_links="0" * 64)
        self.fx.write_gate()
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(proc.stdout.splitlines()[-1],
                         "ownership: not checked (the gate could not run)")

    def test_a_committed_symlink_is_refused(self):
        elsewhere = self.fx.document(TASK, inventory_body(), directory=self.fx.root / "elsewhere")
        target = self.wt / "findings" / TASK / f"{SID}.json"
        target.parent.mkdir(parents=True)
        target.symlink_to(elsewhere)
        (self.wt / "tasks" / f"{TASK}.yaml").write_text(f"id: {TASK}\nstatus: complete\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-qm", "a link")
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("not a regular file", proc.stderr)


# --------------------------------------------------------------------------- #
# the package review's findings: hostile shapes are refusals or exit 3
# --------------------------------------------------------------------------- #

class TestHostileDocumentsAreRefusals(GateTestCase):
    """Review #48, #50, #51, #52, #54, #57, #58: rule refusals, never tracebacks."""

    TASK = "t-loc-0001"

    def setUp(self):
        self.fx = Fixture(self, "locate")
        self.fx.manifest(self.TASK, locate_manifest_doc(self.fx))

    def doc(self, body):
        return self.fx.document(self.TASK, body)

    def raw(self, text: str, name: str = f"{SID}.json") -> Path:
        path = self.fx.root / "findings" / self.TASK / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_symlink_or_a_device_is_not_read(self):
        elsewhere = self.fx.document(self.TASK, locate_body(), directory=self.fx.root / "elsewhere")
        target = self.fx.root / "findings" / self.TASK / f"{SID}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(elsewhere)
        self.assertRule(self.fx.run(target), 1, "not a regular file")
        target.unlink()
        target.symlink_to("/dev/zero")
        self.assertRule(self.fx.run(target), 1, "not a regular file")
        # and in directory mode, where a harvest's rsync -a lands a link as a link
        result = vl.run_gate(self.fx.gate_cfg(), root=self.fx.root, findings_dir="findings")
        self.assertRule(result, 1, "not a regular file")

    def test_a_list_or_an_object_where_a_word_belongs(self):
        def located(**loc):
            body = locate_body()
            body["passages"][0]["locations"][0].update(loc)
            return body

        def passage(**fields):
            body = locate_body()
            body["passages"][0].update(fields)
            return body

        cases = {"relation": located(relation=["exactMatch"]), "library": located(library=["alpha"]),
                 "confidence": located(confidence={"a": 1}), "item": passage(item=[THM_ITEM]),
                 "outcome": passage(outcome={"a": 1}),
                 "verdict": passage(receipt={"queries": ["q"], "candidates": [
                     {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
                      "verdict": ["accepted"]}]}),
                 "unresolved.by": dict(locate_body(), unresolved={"reason": "x", "by": ["worker"]})}
        for field, body in cases.items():
            with self.subTest(field=field):
                result = self.fx.run(self.doc(body))
                self.assertEqual(result["exit"], vl.EXIT_REJECTED, result)
                self.assertTrue(result["problems"])

    def test_a_deep_document_and_an_overflowing_number(self):
        text = json.dumps({**self.fx.header(self.TASK), **locate_body()})[:-1]
        for depth in (995, 100000):
            with self.subTest(depth=depth):
                path = self.raw(text + ', "x": ' + "[" * depth + "]" * depth + "}")
                self.assertRule(self.fx.run(path), 1, "nested deeper")
        path = self.raw(text + ', "x_score": 1e999}')
        self.assertRule(self.fx.run(path), 1, "not a finite JSON number")

    def test_a_lone_surrogate_anywhere(self):
        for where in ("witness", "query"):
            body = locate_body()
            if where == "witness":
                body["passages"][0]["locations"][0]["witness"] = "\udfff"
            else:
                body["passages"][1]["receipt"]["queries"][0] = "widget \ud800 lemma"
            text = json.dumps({**self.fx.header(self.TASK), **body})   # ASCII: \\uXXXX escapes
            with self.subTest(where=where):
                self.assertRule(self.fx.run(self.raw(text)), 1, "lone surrogate")

    def test_the_file_is_named_for_its_source(self):
        path = self.fx.document(self.TASK, locate_body())
        moved = path.with_name("enwiki-1003-2004.json")
        path.rename(moved)
        self.assertRule(self.fx.run(moved), 1, f"holds source {SID}'s document")

    def test_one_location_per_declaration(self):
        body = locate_body()
        body["passages"][0]["locations"] = [location(rank=1), location(rank=2, relation="broadMatch")]
        self.assertRule(self.fx.run(self.doc(body)), 6, "located more than once")


class TestRuleFourMeansLocated(GateTestCase):
    """Review #49: accepted means located; a relatedMatch discharges no curated hit."""

    TASK = "t-loc-0001"

    def setUp(self):
        self.fx = Fixture(self, "locate")
        self.fx.manifest(self.TASK, locate_manifest_doc(self.fx))

    def absent_body(self, **receipt_extra):
        body = locate_body()
        queries = ["widget lemma", "finite widget spanning", "Widget.spanning"]
        body["passages"][0] = {"item": THM_ITEM, "outcome": "absent", "locations": [],
                               "receipt": {"queries": list(queries), "candidates": []}}
        body["passages"][0]["receipt"].update(receipt_extra)
        return body

    def test_an_accepted_candidate_with_no_location(self):
        body = self.absent_body(candidates=[{"library": "alpha",
                                             "declaration": "Widget.exists_spanning_gadget",
                                             "verdict": "accepted", "reason": ""}])
        result = self.fx.run(self.fx.document(self.TASK, body))
        self.assertRule(result, 1, "is not one of this passage's locations")
        self.assertRule(result, 4, "neither located")

    def test_a_related_match_alone_does_not_discharge_a_curated_hit(self):
        body = self.absent_body()
        body["passages"][0]["locations"] = [location(relation="relatedMatch")]
        self.assertRule(self.fx.run(self.fx.document(self.TASK, body)), 4, "a relatedMatch is not")
        body["passages"][0]["receipt"]["candidates"] = [
            {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
             "verdict": "rejected", "reason": "states only a special case"}]
        self.assertOk(self.fx.run(self.fx.document(self.TASK, body)))


class TestTheInventoryKeepsItsSubject(GateTestCase):
    """Review #60: demoting the subject passage does not turn rule 4 off."""

    def test_a_relabelled_lead(self):
        fx = Fixture(self, "locate-inventory")
        fx.manifest(TASK, inventory_manifest_doc(fx))
        body = inventory_body()
        body["passages"][0].update(prominence="central", scope="expository")
        self.assertRule(fx.run(fx.document(TASK, body)), 4, "prominence subject is kept")
        self.assertOk(fx.run(fx.document(TASK, inventory_body())))


class TestEveryDocumentOfTheTask(GateTestCase):
    """Review #53: a task that writes documents writes one per source its manifest names."""

    TASK = "t-loc-0001"
    OTHER = "enwiki-1003-2004"

    def setUp(self):
        self.fx = Fixture(self, "locate")
        self.fx.gate["sources"][self.OTHER] = dict(self.fx.gate["sources"][SID])
        self.fx.write_gate()
        second = dict(locate_manifest_doc(self.fx), source_id=self.OTHER, passages=[])
        path = self.fx.manifest(self.TASK, locate_manifest_doc(self.fx))
        doc = json.loads(path.read_text())
        doc["documents"].append(second)
        path.write_text(json.dumps(doc))
        self.fx.document(self.TASK, locate_body())

    def test_a_corpus_missing_one(self):
        result = vl.run_gate(self.fx.gate_cfg(), root=self.fx.root, findings_dir="findings")
        self.assertRule(result, 1, f"documents for {self.OTHER} are missing")

    def test_a_change_set_missing_one(self):
        ctx = vl.Context(self.fx.gate_cfg())
        (self.fx.root / "tasks").mkdir()
        (self.fx.root / "tasks" / f"{self.TASK}.yaml").write_text("id: x\n")
        entries = {f"findings/{self.TASK}/{SID}.json": "A", f"tasks/{self.TASK}.yaml": "M"}
        problems = vl.check_changed(entries, ctx, task_id=self.TASK, root=self.fx.root)
        self.assertTrue(any(self.OTHER in line and "missing" in line for line in problems.lines()),
                        problems.lines())
        # the other document already landed in an earlier commit of the task
        other = self.fx.root / "findings" / self.TASK / f"{self.OTHER}.json"
        other.write_text("{}")
        self.assertFalse(vl.check_changed(entries, ctx, task_id=self.TASK, root=self.fx.root))
        # a symlinked path in the change set is refused under rule 7
        doc = self.fx.root / "findings" / self.TASK / f"{SID}.json"
        doc.rename(self.fx.root / "real.json")
        doc.symlink_to(self.fx.root / "real.json")
        problems = vl.check_changed(entries, ctx, task_id=self.TASK, root=self.fx.root)
        self.assertTrue(any(line.startswith("[rule 7]") and "regular file" in line
                            for line in problems.lines()), problems.lines())
        ctx.close()


class TestACorpusHoldsOnlyDocuments(GateTestCase):
    """Review #63: JSON off the <task>/<source>.json layout is named, not skipped."""

    def test_strays_are_refused(self):
        fx = Fixture(self, "locate")
        fx.manifest("t-loc-0001", locate_manifest_doc(fx))
        landed = fx.root / "landed"
        fx.document("t-loc-0001", locate_body(), directory=landed)
        (landed / "RECEIPT.json").write_text("{}")
        cfg = fx.gate_cfg()
        self.assertOk(vl.run_gate(cfg, root=landed, findings_dir="."))
        (landed / "stray.json").write_text("{}")
        (landed / "t-loc-0001" / "sub").mkdir()
        (landed / "t-loc-0001" / "sub" / f"{SID}.json").write_text("{}")
        result = vl.run_gate(cfg, root=landed, findings_dir=".")
        stray = [p for p in result["problems"] if p.startswith("[rule 7]")]
        self.assertEqual(len(stray), 2, result["problems"])
        self.assertTrue(any("stray.json" in p for p in stray))
        self.assertTrue(any(f"t-loc-0001/sub/{SID}.json" in p for p in stray))


class TestABrokenGateSaysSo(GateTestCase):
    """Review #55, #56, #61: a malformed gate input is exit 3, and the last line says so."""

    def test_malformed_gate_json_values(self):
        fx = Fixture(self, "locate")
        for key, value in (("max_locations", "five"), ("kinds", 7), ("max_document_bytes", 0),
                           ("corpus_db", {"path": 5, "sha256": "b" * 64}),
                           ("tasks_dir", ["x"])):
            gate = dict(fx.gate, **{key: value})
            path = fx.root / "bad.json"
            path.write_text(json.dumps(gate))
            with self.subTest(key=key), self.assertRaises(vl.GateBroken):
                vl.GateConfig.load(path)
        proc = subprocess.run([sys.executable, str(GATE), "--gate-config", str(path),
                               "--root", str(fx.root)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_malformed_tables_are_not_read_as_empty(self):
        fx = Fixture(self, "locate-inventory")
        fx.manifest(TASK, inventory_manifest_doc(fx))
        sdir = fx.root / "sourcesets" / "wpm" / "sources" / SID
        paragraphs = (sdir / "paragraphs.json").read_text()
        (sdir / "paragraphs.json").write_text(json.dumps({"paragraphs": json.loads(paragraphs)}))
        self.assertBroken(fx.run(fx.document(TASK, inventory_body())), "paragraphs.json is not")
        (sdir / "paragraphs.json").write_text(paragraphs)
        (sdir / "formulas.json").write_text(json.dumps({"formulas": []}))
        self.assertBroken(fx.run(fx.document(TASK, inventory_body())), "formulas.json is not")


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# wave 2: the gate directory, quoted URIs, the export's records, per-task hits
# --------------------------------------------------------------------------- #

class TestAnIncompleteGateDirectory(unittest.TestCase):
    """D1: a sibling that does not import is exit 3 with ONE FAIL line, never a traceback."""

    def test_a_lone_gate_script(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gate = tmp / "gate"
        gate.mkdir()
        shutil.copy2(GATE, gate / "verify_locate.py")
        (gate / "gate.json").write_text("{}", encoding="utf-8")
        environ = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        proc = subprocess.run([sys.executable, str(gate / "verify_locate.py"), "--gate-config",
                               str(gate / "gate.json")], capture_output=True, text=True,
                              cwd=str(tmp), env=environ)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        fails = [line for line in proc.stderr.splitlines() if line.startswith("FAIL:")]
        self.assertEqual(len(fails), 1, proc.stderr)
        for sibling in ("locate_text.py", "verify_findings.py", "locate_records.py"):
            self.assertIn(sibling, fails[0])
        self.assertIn("not a finding of yours", fails[0])
        self.assertEqual(proc.stdout.strip().splitlines()[-1],
                         "ownership: not checked (the gate could not run)")

    def test_in_process_it_is_broken_not_a_raise(self):
        fx = Fixture(self, "locate-inventory")
        from unittest import mock

        with mock.patch.object(vl, "IMPORT_ERRORS", ["locate_records.py (ModuleNotFoundError)"]):
            result = fx.run()
        self.assertEqual(result["exit"], vl.EXIT_GATE_BROKEN)
        self.assertIn("locate_records.py", result["broken"][0])


class TestSqliteUrisAreQuoted(GateTestCase):
    """#17: '?', '#' and '%xx' in a directory name may neither truncate nor redirect the URI."""

    def test_odd_directory_names(self):
        for odd in ("q?dir", "hash#dir", "pct%41dir"):
            with self.subTest(directory=odd):
                fx = Fixture(self, "locate")
                fx.manifest("t-loc-0001", locate_manifest_doc(fx))
                moved = fx.root / odd / "corpus.sqlite"
                moved.parent.mkdir()
                shutil.move(str(fx.db), moved)
                fx.gate["corpus_db"]["path"] = str(moved)
                fx.write_gate()
                doc = fx.document("t-loc-0001", locate_body())
                before = {p.name for p in fx.root.iterdir()}
                self.assertOk(fx.run(doc))
                self.assertLessEqual({p.name for p in fx.root.iterdir()} - before, {"gate-log"},
                                     "the read-only open created a file")
                self.assertEqual(sorted(p.name for p in moved.parent.iterdir()), ["corpus.sqlite"])
        self.assertIn("%3F", vl.ro_uri(Path("/a/q?b/c.sqlite")))
        self.assertTrue(vl.ro_uri(Path("/a/x#y")).endswith("x%23y?mode=ro&immutable=1"))

    def test_the_orchestrator_lookup_too(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        db = tmp / "root#1" / "orchestrator.sqlite"
        db.parent.mkdir()
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE worktrees (task_id TEXT, path TEXT)")
        con.execute("INSERT INTO worktrees VALUES ('t-9', '/w/t-9')")
        con.commit()
        con.close()
        self.assertEqual(vl.task_from_orchestrator(db, Path("/w/t-9"))[0], "t-9")
        self.assertFalse((tmp / "root").exists())


class TestRuleOneUsesTheExportsQuote(GateTestCase):
    """#36/#73 (D8): a passage the export cannot quote uniquely is refused at the gate."""

    def test_a_head_no_32_characters_can_make_unique(self):
        ctx = "and in every case the same context holds there: "
        head = ("every finite widget has a spanning gadget whose size divides the order of "
                "the widget group")
        tail = " and this tail is identical in both places too."
        repeated = ctx + head + tail
        layer = f"Uno distinct opening words here. {repeated} Dos other words here. {repeated}"
        fx = Fixture(self, "locate-inventory")
        sdir = fx.root / "sourcesets" / "wpm" / "sources" / SID
        (sdir / "layer.txt").write_text(layer, encoding="utf-8")
        (sdir / "formulas.json").write_text("[]", encoding="utf-8")
        (sdir / "paragraphs.json").write_text("[]", encoding="utf-8")
        fx.layer_sha = _sha(layer.encode("utf-8"))
        fx.gate["sources"][SID]["layer_sha256"] = fx.layer_sha
        fx.write_gate()
        fx.candidates["curated"] = []
        fx.write_candidates()
        fx.manifest(TASK, {"source_id": SID, "source_sha": HTML_SHA, "layer_sha256": fx.layer_sha,
                           "renderer": "web-html/1", "finders": []})
        start = layer.index(head)
        ((_s, digest, words),) = lt.assign_ids(layer, [start])
        passage = {"item": f"In-text #{digest}", "head_words": words,
                   "span": [start, start + len(head)],
                   "quote": {"exact": head, "prefix": layer[:start], "suffix": ""},
                   "section": [], "fragment": None, "passage_kind": "theorem",
                   "prominence": "central", "scope": "formal_math",
                   "statement": "a restatement", "concept": None, "finder": "model"}
        self.assertEqual(lt.find_quote(layer, head, layer[:start]), [start])
        result = fx.run(fx.document(TASK, {"passages": [passage], "skips": []}))
        self.assertRule(result, 1, "the export cannot quote this passage")

    def test_the_gate_carries_no_quote_cap_of_its_own(self):
        self.assertFalse(hasattr(vl, "EXPORT_QUOTE_WORDS"))
        self.assertFalse(hasattr(vl, "_preview_records"))
        self.assertIs(vl.lr.cap_quote, vl.capped_quote.__globals__["lr"].cap_quote)


class TestRuleFourIsPerTask(GateTestCase):
    """#29 (D7): the manifest's `curated` list, intersected with L2, is what a task must handle."""

    TASK = "t-loc-0002"

    def body(self):
        body = locate_body()
        body["passages"][0] = {"item": THM_ITEM, "outcome": "unresolved", "locations": [],
                               "reason": "ambiguous", "receipt": {"queries": [], "candidates": []}}
        return body

    def run_with(self, curated):
        fx = Fixture(self, "locate")
        doc = locate_manifest_doc(fx)
        if curated is not None:
            doc["curated"] = curated
        fx.manifest(self.TASK, doc)
        return fx.run(fx.document(self.TASK, self.body()))

    def test_a_task_handed_no_hit_owes_none(self):
        self.assertOk(self.run_with([]))

    def test_a_task_handed_the_hit_owes_it(self):
        self.assertRule(self.run_with([{"library": "alpha",
                                        "declaration": "Widget.exists_spanning_gadget"}]),
                        4, "alpha:Widget.exists_spanning_gadget")

    def test_a_manifest_cannot_add_a_hit(self):
        self.assertOk(self.run_with([{"library": "beta", "declaration": "Beta.not.in.l2"}]))

    def test_without_the_key_the_task_owes_every_hit_of_the_file(self):
        self.assertRule(self.run_with(None), 4)


#: fourteen consecutive words of the layer: past the export's twelve-word quote cap
VERBATIM = " ".join(LEAD.split()[:14])


class TestRuleOneAppliesTheExportsFreeTextCap(GateTestCase):
    """A note, a query or a reason the export would refuse as verbatim source text
    is refused at the gate, by the export's own code, while the worker can fix it."""

    TASK = "t-loc-0001"

    def setUp(self):
        self.fx = Fixture(self, "locate")
        self.fx.manifest(self.TASK, locate_manifest_doc(self.fx))

    def run_body(self, body):
        return self.fx.run(self.fx.document(self.TASK, body))

    def test_a_location_note(self):
        body = locate_body()
        body["passages"][0]["locations"][0]["note"] = VERBATIM
        self.assertRule(self.run_body(body), 1, "a location's note carries 13+ words of the "
                                                "source verbatim")
        body["passages"][0]["locations"][0]["note"] = " ".join(LEAD.split()[:12])
        self.assertOk(self.run_body(body))

    def test_an_absences_query_and_a_candidates_reason(self):
        body = locate_body()
        body["passages"][1]["receipt"]["queries"][0] = VERBATIM
        self.assertRule(self.run_body(body), 1, "a receipt query carries")
        body = locate_body()
        body["passages"][1]["receipt"]["candidates"] = [
            {"library": "alpha", "declaration": "Widget.Spanning", "verdict": "rejected",
             "reason": f"it says {VERBATIM} and no more"}]
        self.assertRule(self.run_body(body), 1, "a candidate's reason carries")

    def test_only_what_the_export_writes(self):
        """A located passage's receipt is not exported, so the cap is the export's, no wider."""
        body = locate_body()
        body["passages"][0]["receipt"]["queries"].append(VERBATIM)
        self.assertOk(self.run_body(body))

    def test_a_challenge_overturns_note(self):
        fx = Fixture(self, "locate-challenge")
        doc = locate_manifest_doc(fx)
        doc.pop("passages")
        doc["records"] = [{"item": LEAD_ITEM, "target": "absence", "span": LEAD_SPAN,
                           "passage_kind": "theorem"}]
        fx.manifest("t-chl-0001", doc)
        body = {"verdicts": [{"item": LEAD_ITEM, "target": "absence", "action": "overturn",
                              "locations": [location(lib="beta",
                                                     decl="Beta.Widget.gadget_of_finite",
                                                     note=VERBATIM)],
                              "witness": "found with a type search"}]}
        self.assertRule(fx.run(fx.document("t-chl-0001", body)), 1, "verbatim")

    def test_a_challenge_downgrades_note(self):
        """A downgrade to closeMatch exports a note: the verdict's, else a witness sentence.

        The outcomes write ``downgrade_note(verdict, layer)`` as the location's note
        and the export refuses one past the cap, so the gate refuses the same note,
        computed by the same function, while the challenger can still rewrite it."""
        key = "9" * 64
        fx = Fixture(self, "locate-challenge")
        doc = locate_manifest_doc(fx)
        doc.pop("passages")
        doc["records"] = [{"item": THM_ITEM, "target": "location", "location_key": key,
                           "span": THEOREM_SPAN, "passage_kind": "theorem",
                           "relation": "exactMatch"}]
        fx.manifest("t-chl-0001", doc)

        def run(relation="closeMatch", **fields):
            verdict = {"item": THM_ITEM, "target": "location", "location_key": key,
                       "action": "downgrade", "relation": relation, **fields}
            return fx.run(fx.document("t-chl-0001", {"verdicts": [verdict]}))

        own = "the signature adds a finiteness hypothesis."
        quoted = " ".join(LEAD.split()[12:])      # one sentence, fourteen words of the layer
        result = run(note=VERBATIM, witness=own)
        self.assertRule(result, 1, "(a downgrade to closeMatch exports its note")
        self.assertRule(result, 1, f"verdicts[0]: {THM_ITEM}: a location's note carries "
                                   "13+ words of the source verbatim")
        # no note, and every sentence of the witness quotes the article past the cap
        self.assertRule(run(witness=f"It reads: {quoted}"), 1, "verbatim")
        # the first sentence quotes, the second does not: the second is the note
        self.assertOk(run(witness=f"It reads: {quoted} {own.capitalize()}"))
        self.assertOk(run(note="0 is a natural number here", witness=f"It reads: {quoted}"))
        # a downgrade to another relation exports no note: only what the export writes
        self.assertOk(run(relation="narrowMatch", note=VERBATIM, witness=own))
        # the gate's note is the outcomes' note: one definition, in the staged module
        from locate import outcomes, records  # noqa: PLC0415
        self.assertIs(outcomes.downgrade_note, records.downgrade_note)
        with self.assertRaisesRegex(records.RecordError, "verbatim"):
            records.check_note(THM_ITEM, outcomes.downgrade_note(
                {"witness": f"It reads: {quoted}"}, LAYER), LAYER)

    def test_the_problem_names_the_passage_once_seam3(self):
        """Review SEAM-3: the export's message starts with the item, and a locate
        document's where ends with it; the line names it once.  A challenge verdict's
        where (``verdicts[i]``) does not name the item, so its message keeps it."""
        body = locate_body()
        body["passages"][0]["locations"][0]["note"] = VERBATIM
        body["passages"][1]["receipt"]["queries"][0] = VERBATIM
        hits = [p for p in self.run_body(body)["problems"] if "verbatim" in p]
        self.assertEqual(len(hits), 2, hits)
        for line, item in zip(hits, (THM_ITEM, LEAD_ITEM)):
            with self.subTest(item=item):
                self.assertEqual(line.count(item), 1, line)
                self.assertIn(f": {item}: a ", line)
        fx = Fixture(self, "locate-challenge")
        doc = locate_manifest_doc(fx)
        doc.pop("passages")
        doc["records"] = [{"item": LEAD_ITEM, "target": "absence", "span": LEAD_SPAN,
                           "passage_kind": "theorem"}]
        fx.manifest("t-chl-0001", doc)
        verdict = {"verdicts": [{"item": LEAD_ITEM, "target": "absence", "action": "overturn",
                                 "locations": [location(lib="beta",
                                                        decl="Beta.Widget.gadget_of_finite",
                                                        note=VERBATIM)],
                                 "witness": "found with a type search"}]}
        hits = [p for p in fx.run(fx.document("t-chl-0001", verdict))["problems"]
                if "verbatim" in p]
        self.assertTrue(hits)
        self.assertIn(f"verdicts[0]: {LEAD_ITEM}: a location's note", hits[0])

    def test_the_export_and_the_gate_refuse_the_same_passage(self):
        """One definition: the gate calls the export's check_note and receipt_document."""
        body = locate_body()
        body["passages"][0]["locations"][0]["note"] = VERBATIM
        calls = []
        real = vl.lr.check_note

        def spy(*args):
            calls.append(args[1])
            return real(*args)

        with mock.patch.object(vl.lr, "check_note", side_effect=spy):
            self.assertRule(self.run_body(body), 1, "verbatim")
        self.assertIn(VERBATIM, calls)
        layer = LAYER
        passage = {"item": THM_ITEM, "span": THEOREM_SPAN, "outcome": "located",
                   "challenge": "survived", "passage_kind": "theorem",
                   "section": ["Theorems"], "prominence": "central",
                   "locations": [{"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
                                  "relation": "exactMatch", "rank": 1, "proof_status": "proved",
                                  "location_key": "0" * 64, "challenge": "survived",
                                  "note": VERBATIM}]}
        universe = vl._preview_universe(self.fx.gate_cfg())
        with self.assertRaisesRegex(vl.lr.RecordError, "a location's note carries"):
            vl.lr.passage_records(vl._preview_source(self.fx.gate_cfg(), SID), passage, layer,
                                  universe)


class TestRuleOneBuildsTheExportsSubjectEnd(GateTestCase):
    """S5: the records rule 1 builds carry the passage's span on the pinned layer, as the
    export's do, so a document the gate passes is one whose records the export can write."""

    TASK = "t-loc-0001"

    def test_the_preview_records_carry_the_span_and_the_pinned_layer_sha(self):
        fx = Fixture(self, "locate")
        fx.manifest(self.TASK, locate_manifest_doc(fx))
        built = []
        real = vl.lr.check

        def spy(record):
            built.append(record)
            return real(record)

        with mock.patch.object(vl.lr, "check", side_effect=spy):
            self.assertOk(fx.run(fx.document(self.TASK, locate_body())))
        spans = {rec["extensions"][vl.lr.EXTENSION]["item"]:
                 rec["subject"][0]["extensions"][vl.lr.EXTENSION] for rec in built}
        self.assertEqual(set(spans), {THM_ITEM, LEAD_ITEM})
        for item, span in ((THM_ITEM, THEOREM_SPAN), (LEAD_ITEM, LEAD_SPAN)):
            ext = spans[item]
            with self.subTest(item=item):
                self.assertEqual(ext["span"], [span[0], span[1]])
                self.assertEqual(ext["layer_sha256"], fx.layer_sha)
                self.assertEqual(ext["span_sha256"], _sha(LAYER[span[0]:span[1]].encode("utf-8")))


# --------------------------------------------------------------------------- #
# bounded work, ownership, the log, the cap and the skips
# --------------------------------------------------------------------------- #

class TestAHugeDocumentIsRefusedFast(GateTestCase):
    """Rule 6's checks were quadratic in a passage's locations, and every location added
    lines; over the cap nothing more is read, and the problems are capped."""

    TASK = "t-loc-0001"

    def setUp(self):
        self.fx = Fixture(self, "locate")
        self.fx.manifest(self.TASK, locate_manifest_doc(self.fx))

    def test_fifty_thousand_locations(self):
        body = locate_body()
        body["passages"][0]["locations"] = [{} for _ in range(50_000)]
        self.fx.gate["max_document_bytes"] = 4 * 1024 * 1024
        self.fx.write_gate()
        import time

        started = time.monotonic()
        result = self.fx.run(self.fx.document(self.TASK, body))
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertRule(result, 6, "50000 locations; at most 5")
        self.assertLess(len(result["problems"]), 10, result["problems"][:5])

    def test_the_problems_kept_are_capped_and_the_rest_counted(self):
        body = locate_body()
        body["passages"] += [{"item": f"In-text #{i:08x}", "outcome": "absent", "locations": [],
                              "receipt": {"queries": [], "candidates": []}}
                             for i in range(3 * vl.MAX_PROBLEMS)]
        result = self.fx.run(self.fx.document(self.TASK, body))
        self.assertEqual(result["exit"], vl.EXIT_REJECTED)
        self.assertEqual(len(result["problems"]), vl.MAX_PROBLEMS + 1)
        self.assertIn("more problem(s) not listed", result["problems"][-1])
        self.assertEqual(result["by_rule"]["2"], 3 * vl.MAX_PROBLEMS)


class TestTheChangeSetsTaskAndLogLine(OwnershipCase):
    """Ownership, the log line, the bridge's cap and the task file, through a real change set."""

    def lines(self) -> list[dict]:
        log = self.fx.root / "gate-log" / "gate.jsonl"
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    # -- several tasks without a task id ------------------------------------
    def test_a_worktree_of_two_tasks_needs_the_pre_merge_flag(self):
        self.contribute()
        self.contribute("t-inv-0002")
        proc = self.cli()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("one worker's contribution is one task's", proc.stderr)
        proc = self.cli("--pre-merge")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_the_pre_merge_call_says_so_on_its_log_line(self):
        """Port3 section 5 item 1: the pre-merge call's line was a validation's, so both of
        the log's readers counted a merged contribution twice.  The gate writes
        ``pre_merge`` on every line, and the readers count the validation alone."""
        from bench import metrics
        from run import watchdog

        self.contribute()
        self.assertEqual(self.cli("--task-id", TASK).returncode, 0)   # tend's validation
        self.assertEqual(self.cli("--pre-merge").returncode, 0)      # tend's pre-merge
        doc = self.wt / "findings" / TASK / f"{SID}.json"
        environ = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
        environ.pop("TEND_TASK_ID", None)
        proc = subprocess.run([sys.executable, str(GATE), "--gate-config",
                               str(self.fx.root / "gate" / "gate.json"), "--findings", str(doc)],
                              capture_output=True, text=True, env=environ)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = self.lines()
        self.assertEqual([(l["changed_files"], l["pre_merge"]) for l in lines],
                         [(True, False), (True, True), (False, False)])
        found = metrics.gate_rejections(lines)
        self.assertEqual((found["attempts"], found["pre_merge"]["calls"], found["dropped_lines"]),
                         (1, 1, 1))
        counts = watchdog.gate_counts(self.fx.root / "gate-log" / "gate.jsonl")
        self.assertEqual((counts["calls"], counts["pre_merge_calls"]), (1, 1))

    def test_the_template_puts_the_flag_on_the_pre_merge_command_only(self):
        text = (SKILL / "templates" / "tend" / "config-locate.yaml.tmpl").read_text()
        validation, pre_merge = [l for l in text.splitlines() if "argv:" in l
                                 and "--changed-files" in l]
        self.assertNotIn("--pre-merge", validation)
        self.assertIn("--pre-merge", pre_merge)

    # -- by: attempt_cap is the bridge's -------------------------------------
    def capped(self, reason: str) -> None:
        self.fx.document(TASK, {"passages": [], "skips": [],
                                "unresolved": {"reason": reason, "by": "attempt_cap"}},
                         directory=self.wt / "findings")
        (self.wt / "tasks" / f"{TASK}.yaml").write_text(f"id: {TASK}\nstatus: complete\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-qm", "capped")

    def test_a_worker_written_attempt_cap_is_refused(self):
        self.capped("attempt_cap: whatever")
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("written by the bridge", proc.stderr)

    def test_the_bridges_form_needs_the_counter_at_the_cap(self):
        self.capped(CAP_REASON)
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("0 counted rejection(s)", proc.stderr)
        counter = vl.attempts_counter(self.fx.gate_cfg(), TASK)
        self.assertEqual(counter, self.fx.root / "attempts" / f"{TASK}.json")
        counter.parent.mkdir()
        counter.write_text(json.dumps({"task_id": TASK, "rejections": 3, "history": []}))
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # a harvested corpus has no counter: the bridge's form is enough there
        self.assertOk(self.fx.run(self.wt / "findings" / TASK / f"{SID}.json"))

    def test_the_counter_is_where_the_bridge_writes_it_on_either_layout(self):
        """The bridge counts under its own root, the tend root above its ``bin/``.  On a
        run host the gate directory is the tend root's sibling (``<root>/<run>-gate``),
        so a counter looked for above the gate directory was never the bridge's, and
        every capped task would have been refused there."""
        from cflib.paths import WorkspacePaths
        from locate import plan as lplan
        from run import attempt_cap

        paths = WorkspacePaths(self.fx.root / "ws")
        tools = {"path": str(paths.corpus_db("u1")), "sha256": "1" * 64, "kind": "full"}
        for host in (lplan.LOCAL, "examplebox"):
            layout = lplan.host_layout(paths, host=host, run_id="r-1", sourceset_id="wpm",
                                       universe_id="u1", tools_db=tools,
                                       remote_root="/srv/cf")["host"]
            cfg = vl.GateConfig({**self.fx.gate, "tasks_dir": layout["tasks_dir"],
                                 "orchestrator_db": layout["orchestrator_db"]})
            with self.subTest(host=host):
                self.assertEqual(vl.attempts_counter(cfg, TASK),
                                 attempt_cap.counter_path(Path(layout["tend_root"]), TASK))
        cfg = vl.GateConfig({**self.fx.gate, "orchestrator_db": None})
        with self.assertRaisesRegex(vl.GateBroken, "names no orchestrator_db"):
            vl.attempts_counter(cfg, TASK)

    # -- the task file is the task's own .yaml, changed in its status -------
    def test_a_rewritten_task_id_or_a_second_task_file_is_refused(self):
        self.contribute()
        (self.wt / "tasks" / f"{TASK}.yaml").write_text(f"id: t-inv-0002\nstatus: complete\n")
        git(self.wt, "commit", "-qam", "another id")
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("changes more than its status line", proc.stderr)
        (self.wt / "tasks" / f"{TASK}.yaml").write_text(f"id: {TASK}\nstatus: complete\n")
        (self.wt / "tasks" / f"{TASK}.yml").write_text("id: new-task\nstatus: open\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-qm", "a second task file")
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(f"tasks/{TASK}.yml: is outside", proc.stderr)

    def test_the_status_line_holds_one_status_word(self):
        """The comparison leaves the status line out, so what it holds is checked: a flow
        mapping that rewrites the plan's keys, an unparseable value and a YAML tag are
        refused, and so is a second status line; one status word passes."""
        self.contribute()
        task_file = self.wt / "tasks" / f"{TASK}.yaml"
        hostile = {
            "status: {id: t-inv-0002, depends_on: [t-inv-0001], priority: 99}":
                "sets its status line to something other than one status word",
            "status: [": "sets its status line to something other than one status word",
            "status: !!python/object/apply:os.system ['true']":
                "sets its status line to something other than one status word",
            "status: complete\nstatus: open": "has 2 status lines",
        }
        for line, needle in hostile.items():
            task_file.write_text(f"id: {TASK}\n{line}\n")
            git(self.wt, "commit", "-qam", "status")
            proc = self.cli("--task-id", TASK)
            with self.subTest(line=line):
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                rule7 = [s for s in proc.stderr.splitlines() if "[rule 7]" in s]
                self.assertEqual(len(rule7), 1, proc.stderr)
                self.assertIn(f"tasks/{TASK}.yaml: {needle}", rule7[0])
        # (the merge base says `status: open`: a contribution changes it)
        for line in ("status: complete", "status: in_progress  ", "status: needs-review"):
            task_file.write_text(f"id: {TASK}\n{line}\n")
            git(self.wt, "commit", "-qam", "status")
            proc = self.cli("--task-id", TASK)
            with self.subTest(line=line):
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_an_added_task_file_is_refused(self):
        self.fx.manifest("t-inv-0003", inventory_manifest_doc(self.fx))
        self.contribute("t-inv-0003")
        proc = self.cli("--task-id", "t-inv-0003")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("adds a task file", proc.stderr)
        self.assertNotIn("has no tasks/", proc.stderr)

    def test_a_refused_task_file_is_one_refusal_not_two(self):
        """A task file refused for its own change is still in the change set: the gate
        says why it is refused, and never also that the documents came with no task file."""
        self.contribute()
        task_file = self.wt / "tasks" / f"{TASK}.yaml"
        task_file.write_text(task_file.read_text() + "priority: 99\n")
        git(self.wt, "commit", "-qam", "a higher priority")
        proc = self.cli("--task-id", TASK)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        rule7 = [line for line in proc.stderr.splitlines() if "[rule 7]" in line]
        self.assertEqual(len(rule7), 1, proc.stderr)
        self.assertIn(f"tasks/{TASK}.yaml: changes more than its status line", rule7[0])
        self.assertIn("priority: 99", rule7[0])
        self.assertNotIn("has no tasks/", proc.stderr)


class TestTheGateGroupCitesWhatResolves(unittest.TestCase):
    """CLAUDE.md section 3: a docstring cites what is in the tree.  A review's finding id
    names a document the tree does not hold, and a ``module.name`` or :func:`name` that
    was renamed sends a reader to nothing; tests/test_rf_docs.py checks neither."""

    #: the host-side helpers and their stage-side neighbours, by the name a docstring
    #: calls them (the record builder is staged as locate_records.py)
    MODULES = {"verify_locate": "gate/verify_locate.py", "verify_findings": "gate/verify_findings.py",
               "locate_text": "gate/locate_text.py", "locate_records": "locate/records.py",
               "passage_ids": "locate/passage_ids.py", "attempt_cap": "run/attempt_cap.py",
               "normalize_result": "run/normalize_result.py",
               "extract_final_json": "run/extract_final_json.py", "agent_call": "run/agent_call.py",
               "corpus_search": "corpus/corpus_search.py", "corpus_show": "corpus/corpus_show.py",
               "gate_corpus": "harvest/gate_corpus.py", "tend_root": "locate/tend_root.py"}
    FILE_SUFFIXES = {"py", "json", "jsonl", "sqlite", "txt", "sh", "tmpl", "yaml", "md"}

    @staticmethod
    def top_level_names(path: Path) -> set[str]:
        import ast

        out: set[str] = set()

        def collect(body):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    out.add(node.name)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    out.update(n.id for t in targets for n in ast.walk(t)
                               if isinstance(n, ast.Name))
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    out.update((a.asname or a.name).split(".")[0] for a in node.names)
                elif isinstance(node, ast.Try):
                    collect(node.body)
                    for handler in node.handlers:
                        collect(handler.body)
        collect(ast.parse(path.read_text(encoding="utf-8")).body)
        return out

    def owned(self) -> list[Path]:
        return [SCRIPTS / rel for rel in self.MODULES.values() if rel != "gate/verify_findings.py"]

    #: a review's finding ids: "review 12", "Review #59", C-02, M-01, OPS-05, SEAM-1,
    #: G-04, R-02, S-3 ... (one digit or more)
    FINDING_ID = r"\b[Rr]eview #?\d+\b|\b(?:SEAM|OPS|[GCMRS])-\d+\b"

    def test_no_review_finding_id_is_cited(self):
        """Not in the gate group, and not anywhere else in scripts/ or templates/: a
        comment states its reason in words, or cites a section of a document in the
        tree.  Vendored files are someone else's text and are not read."""
        import re

        finding = re.compile(self.FINDING_ID)
        files = sorted(p for p in SCRIPTS.rglob("*") if p.is_file()
                       and p.suffix in (".py", ".sh", ".tmpl") and "__pycache__" not in p.parts)
        files += sorted(p for p in (SKILL / "templates").rglob("*") if p.is_file()
                        and "vendor" not in p.parts and p.suffix != ".pyc")
        self.assertTrue(set(self.owned()) <= set(files))
        self.assertGreater(len(files), 200)
        for path in files:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                with self.subTest(file=str(path.relative_to(SKILL)), line=n):
                    self.assertIsNone(finding.search(line), line)

    def test_the_finding_id_pattern_catches_every_form(self):
        import re

        finding = re.compile(self.FINDING_ID)
        for cited in ("(review SEAM-3)", "review / SEAM-1:", "Review C-03:", "(review OPS-09)",
                      "G-04", "R-02", "S-1", "M-01", "review 12", "Review #59"):
            with self.subTest(cited=cited):
                self.assertIsNotNone(finding.search(cited))
        for plain in ("sha256-1", "UTF-8", "web-html/3", "cf-locate/1", "OPS", "SEAM",
                      "G-x", "reviewer 2", "pull request 16"):
            with self.subTest(plain=plain):
                self.assertIsNone(finding.search(plain))

    def test_every_named_function_and_value_exists(self):
        import re

        qualified = re.compile(r"`{1,2}(" + "|".join(self.MODULES) + r")\.([A-Za-z_]\w*)")
        local = re.compile(r":(?:func|data|class):`~?([A-Za-z_]\w*)`")
        names = {mod: self.top_level_names(SCRIPTS / rel) for mod, rel in self.MODULES.items()}
        for path in self.owned():
            text = path.read_text(encoding="utf-8")
            own = self.top_level_names(path)
            for mod, name in qualified.findall(text):
                if name not in self.FILE_SUFFIXES:
                    with self.subTest(file=path.name, ref=f"{mod}.{name}"):
                        self.assertIn(name, names[mod])
            for name in local.findall(text):
                with self.subTest(file=path.name, ref=name):
                    self.assertIn(name, own)


class TestRuleEightSkipsAreVisible(GateTestCase):
    """A skip holding a kept passage is refused, one inside it is not, and what skips
    removed is counted."""

    def setUp(self):
        self.fx = Fixture(self, "locate-inventory")
        self.fx.manifest(TASK, inventory_manifest_doc(self.fx))

    def test_a_whole_layer_skip_beside_the_subject_is_refused(self):
        body = inventory_body()
        body["passages"] = body["passages"][:1]
        body["skips"] = [{"span": [0, len(LAYER)], "reason": "x"}]
        self.assertRule(self.fx.run(self.fx.document(TASK, body)), 8,
                        f"holds the whole kept passage at {LEAD_SPAN}")

    def test_a_skip_of_exactly_a_kept_passage_is_refused_and_a_partial_one_is_not(self):
        body = inventory_body()
        body["skips"].append({"span": THEOREM_SPAN, "reason": "kept and skipped"})
        self.assertRule(self.fx.run(self.fx.document(TASK, body)), 8,
                        f"holds the whole kept passage at {THEOREM_SPAN}")
        body = inventory_body()
        body["skips"].append({"span": [LEAD_SPAN[1] - 5, THEOREM_SPAN[0] + 5],
                              "reason": "the seam between two passages"})
        self.assertOk(self.fx.run(self.fx.document(TASK, body)))

    def test_a_finder_nested_in_a_kept_passage_is_dropped_by_a_skip_inside_it(self):
        """The renderer's blocks nest: a bolded definition inside a kept theorem box is a
        finder passage of its own start, and the skip that drops it lies inside the box."""
        nested = [THEOREM_SPAN[0] + len("Theorem. "), THEOREM_SPAN[1]]
        doc = inventory_manifest_doc(self.fx)
        doc["finders"].append({"finder": "bold_definition", "start": nested[0], "span": nested,
                               "section": ["Theorems"]})
        self.fx.manifest(TASK, doc)
        body = inventory_body()
        self.assertRule(self.fx.run(self.fx.document(TASK, body)), 8,
                        f"the bold_definition passage at {nested} was dropped")
        body["skips"].append({"span": nested, "reason": "the box's own statement, kept whole"})
        result = self.fx.run(self.fx.document(TASK, body))
        self.assertOk(result)
        self.assertEqual(result["stats"][0]["skipped"]["finder_passages"], 1)

    def test_the_stats_and_the_receipt_count_what_skips_removed(self):
        body = inventory_body()
        del body["passages"][1]
        body["skips"].append({"span": THEOREM_SPAN, "reason": "a lemma stated elsewhere"})
        corpus = self.fx.root / "corpus"
        self.fx.document(TASK, body, directory=corpus)
        result = self.fx.run(corpus / TASK / f"{SID}.json")
        self.assertOk(result)
        want = {"skips": 2, "chars": len(THEOREM) + len(HISTORY), "math_paragraphs": 1,
                "finder_passages": 1}
        self.assertEqual(result["stats"][0]["skipped"], want)
        from harvest import gate_corpus

        gated = gate_corpus.gate_locate_corpus(corpus, self.fx.root / "gate" / "gate.json")
        self.assertTrue(gated["ok"], gated["problems"])
        self.assertEqual(gated["skipped"], {**want, "by_source": {SID: want}})
        self.assertIn("1 math paragraph(s)", gate_corpus.render(gated))

    def test_many_passages_and_skips_cost_n_log_n(self):
        """Every skip asked every kept passage whether it overlapped: 20,000 of each took
        twenty seconds.  The spans are sorted once, and each question is a bisection."""
        n = 20_000
        body = {"passages": [{"span": [0, 1]} for _ in range(n)],
                "skips": [{"span": [2, 3], "reason": "x"} for _ in range(n)], "notes": ""}
        self.fx.gate["max_document_bytes"] = 4 * 1024 * 1024
        self.fx.write_gate()
        import time

        started = time.monotonic()
        result = self.fx.run(self.fx.document(TASK, body))
        self.assertLess(time.monotonic() - started, 6.0)
        self.assertEqual(result["exit"], vl.EXIT_REJECTED)
        self.assertEqual(len(result["problems"]), vl.MAX_PROBLEMS + 1)

    def test_the_span_index_answers_as_a_scan_would(self):
        import random

        rng = random.Random(1001)
        for _ in range(200):
            spans = [(a, a + rng.randint(1, 12)) for a in
                     (rng.randint(0, 60) for _ in range(rng.randint(0, 9)))]
            index = vl._SpanIndex(spans)
            for _ in range(20):
                a = rng.randint(0, 70)
                q = (a, a + rng.randint(1, 15))
                with self.subTest(spans=spans, q=q):
                    self.assertEqual(index.overlapping(q) is not None,
                                     any(s[0] < q[1] and q[0] < s[1] for s in spans))
                    self.assertEqual(index.covering(q) is not None,
                                     any(s[0] <= q[0] and q[1] <= s[1] for s in spans))
                    self.assertEqual(index.inside(q) is not None,
                                     any(q[0] <= s[0] and s[1] <= q[1] for s in spans))
                    for got, holds in ((index.overlapping(q), lambda s: s[0] < q[1] and q[0] < s[1]),
                                       (index.covering(q), lambda s: s[0] <= q[0] and q[1] <= s[1]),
                                       (index.inside(q), lambda s: q[0] <= s[0] and s[1] <= q[1])):
                        if got is not None:
                            self.assertIn(got, spans)
                            self.assertTrue(holds(got))
