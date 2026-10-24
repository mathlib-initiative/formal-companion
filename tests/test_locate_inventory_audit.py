"""The inventory audit's host tools: render, edit, and the one schema with its decision table.

New code, for references/locate-documents.md section 13 (``cf-inventory-audit/1``):
``scripts/gate/inventory_render.py`` (the golden rendering, its parts cut between
passages, ``--show``, the ``--previous`` diff, the hints, exit 3 on a pin mismatch,
the output directory), ``scripts/gate/inventory_edit.py`` (each operation, an
operation that cites no verdict refused, only the named document written, and an
edit -> ``passage_ids.py --write`` -> gate OK round trip from a staged gate
directory under ``python3 -I``), ``scripts/gate/inventory_audit.py`` (validation
failures, strip, collateral, both merges, the mechanical kind verdict, the decision
table, ``audit_failed``) and ``AUDIT_GATE_FILES`` (staged only for an audited
plan).  Every article, passage and phrase is the made-up "Widget theory" layer of
``tests/test_locate_gate_statement_cut.py``.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import functools
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
for path in (SCRIPTS, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import _locate_fixtures as F  # noqa: E402
import test_locate_gate_statement_cut as SC  # noqa: E402
import test_verify_locate as G  # noqa: E402
from cflib import vocab  # noqa: E402
from cflib.yamlio import read_yaml  # noqa: E402
from locate import tend_root as ltr  # noqa: E402
from test_locate_planner import Campaign, ws  # noqa: E402


def _load(name: str):
    """A host tool by its plain name, as the run host imports it from the gate directory."""
    if name in sys.modules and Path(sys.modules[name].__file__).parent == SCRIPTS / "gate":
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "gate" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ia = _load("inventory_audit")
ie = _load("inventory_edit")
ir = _load("inventory_render")

SID = G.SID
TASK = "t-inv-0001"
LAYER = SC.LAYER
NAMES = [row[0] for row in SC.GOOD]


def good_doc(rows=None) -> dict:
    """The good cut as a document (items and spans placed)."""
    return {"schema": "cf-locate/1", "mode": "locate-inventory", "run_id": "r-1",
            "task_id": TASK, "source_id": SID, **SC.cut_body(rows)}


def item_of(doc: dict, name: str) -> str:
    return doc["passages"][NAMES.index(name)]["item"]


def formal(doc: dict) -> list[str]:
    return [p["item"] for p in doc["passages"] if p["scope"] == "formal_math"]


def audit_file(doc: dict, **rows) -> dict:
    """A round-1 audit: every formal passage clean but the rows given, by name."""
    out = []
    for p, name in zip(doc["passages"], NAMES):
        if p["scope"] != "formal_math":
            continue
        row = {"item": p["item"], "origin": "lead", "verdict": "clean", "rule": 0,
               "merge_with": "", "reason": "", "action": ""}
        row.update(rows.get(name, {}))
        out.append(row)
    return {"schema": ia.AUDIT_SCHEMA, "kind": "audit", "round": 1, "source_id": SID,
            "passages": out, "expository_flags": [], "per_spec_formal_estimate": 5,
            "patterns": [], "subject_check": "one subject"}


def skeptic_file(flags: dict, *, round_: int = 1, upheld=(), evidence="", examined=(),
                 missed=(), disputes=None, **override) -> dict:
    verdicts = []
    for f in flags["flags"]:
        ok = f["flag_id"] in upheld or f["item"] in upheld
        verdicts.append({"flag_id": f["flag_id"], "upheld": ok, "verdict": f["verdict"],
                         "rule": f["rule"] if ok else 0,
                         "evidence": override.get("evidence_for", {}).get(f["item"], evidence),
                         "reason": "the spec says so"})
    return {"schema": ia.AUDIT_SCHEMA, "kind": "skeptic", "round": round_, "source_id": SID,
            "verdicts": verdicts, "missed": list(missed),
            "disputes": [{"verdict_id": d["verdict_id"], "worker_right": right,
                          "reason": "the spec as written"}
                         for d, right in zip(flags["disputes"], disputes or [])],
            "examined": list(examined), "per_spec_formal_estimate": 5, "summary": "fine"}


def ops_doc(rows: list, round_: int = 1) -> dict:
    return {"schema": ia.AUDIT_SCHEMA, "kind": "ops", "round": round_, "source_id": SID,
            "ops": rows}


MERGE_DEF = {"verdict": "over_cut", "rule": 1, "reason": "one statement with its neighbour",
             "action": "merge into the definition"}


# --------------------------------------------------------------------------- #
# the vocabulary
# --------------------------------------------------------------------------- #

class TestTheVocabularyIsOneDefinition(unittest.TestCase):
    """CLAUDE.md S4: cflib.vocab defines the audit's values; the host copy is pinned."""

    PAIRS = {"AUDIT_SCHEMA": "INVENTORY_AUDIT_SCHEMA", "AUDIT_RULES": "INVENTORY_AUDIT_RULES",
             "AUDIT_SCHEMAS": "INVENTORY_AUDIT_SCHEMAS",
             "AUDIT_REVIEW_ROUNDS": "AUDIT_REVIEW_ROUNDS", "VERDICT_CLEAN": "AUDIT_VERDICT_CLEAN",
             "AUDIT_VERDICTS": "AUDIT_VERDICTS", "VERDICT_REVERT": "AUDIT_VERDICT_REVERT",
             "AUDIT_ORIGINS": "AUDIT_ORIGINS", "CHECK_STATUSES": "AUDIT_CHECK_STATUSES",
             "CHECK_ACTIONABLE": "AUDIT_CHECK_ACTIONABLE",
             "REPAIR_STATUSES": "AUDIT_REPAIR_STATUSES",
             "VERDICT_ORIGINS": "AUDIT_VERDICT_ORIGINS", "AUDIT_DECISIONS": "AUDIT_DECISIONS",
             "EDIT_OPS": "AUDIT_EDIT_OPS", "EDIT_FIELDS": "AUDIT_EDIT_FIELDS",
             "COLLATERAL_CHANGES": "AUDIT_COLLATERAL_CHANGES",
             "AUDIT_THRESHOLD_MIN": "AUDIT_THRESHOLD_MIN",
             "AUDIT_THRESHOLD_SHARE": "AUDIT_THRESHOLD_SHARE",
             "SKEPTIC_FLOOR": "AUDIT_SKEPTIC_FLOOR", "SKEPTIC_SHARE": "AUDIT_SKEPTIC_SHARE",
             "REASON_WORDS": "AUDIT_REASON_WORDS", "ACTION_WORDS": "AUDIT_ACTION_WORDS",
             "EVIDENCE_WORDS": "AUDIT_EVIDENCE_WORDS", "SUMMARY_WORDS": "AUDIT_SUMMARY_WORDS",
             "MAX_PATTERNS": "AUDIT_MAX_PATTERNS", "PART_BYTES": "AUDIT_PART_BYTES",
             "REPAIR_VERDICTS_PER_SESSION": "AUDIT_REPAIR_VERDICTS_PER_SESSION",
             "KIND_RULE_RULE": "AUDIT_KIND_RULE_RULE",
             "CONNECTIVE_HINTS": "AUDIT_CONNECTIVE_HINTS"}

    def test_the_host_copy_is_cflibs(self):
        for host, ours in self.PAIRS.items():
            with self.subTest(host=host):
                self.assertEqual(getattr(ia, host), getattr(vocab, ours))

    def test_the_passage_values_are_the_gates(self):
        from gate import verify_locate as vl

        self.assertEqual(ia.KIND_OTHER, vl.PASSAGE_OTHER)
        self.assertEqual(ia.FORMAL, "formal_math")
        self.assertIn(ia.FORMAL, vl.SCOPES)
        self.assertEqual(ia.ITEM.pattern, vl.ITEM.pattern)
        self.assertEqual(ia.QID.pattern, vl.QID.pattern)
        self.assertEqual(ia.DEFAULT_RULE, 1)

    def test_every_value_is_documented(self):
        vocabulary = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")
        documents = (SKILL / "references" / "locate-documents.md").read_text(encoding="utf-8")
        values = (set(vocab.AUDIT_VERDICTS) | set(vocab.AUDIT_ORIGINS)
                  | set(vocab.AUDIT_CHECK_STATUSES) | set(vocab.AUDIT_REPAIR_STATUSES)
                  | set(vocab.AUDIT_VERDICT_ORIGINS) | set(vocab.AUDIT_DECISIONS)
                  | set(vocab.AUDIT_EDIT_OPS) | set(vocab.AUDIT_EDIT_FIELDS)
                  | set(vocab.AUDIT_COLLATERAL_CHANGES)
                  | {vocab.INVENTORY_AUDIT_SCHEMA, vocab.AUDIT_VERDICT_REVERT})
        for value in sorted(values):
            with self.subTest(value=value):
                self.assertTrue(f"`{value}`" in vocabulary, "references/vocabulary.md")
                self.assertTrue(f"`{value}`" in documents, "references/locate-documents.md")

    def test_the_schema_lists_every_record_and_operation(self):
        self.assertEqual(set(ia.OP_KEYS), set(ia.EDIT_OPS))
        for kind in ia.AGENT_KINDS:
            self.assertIn(kind, ia.RECORDS)
            self.assertIn(kind, ia.FILE_KINDS)


# --------------------------------------------------------------------------- #
# a gate directory as the run host has it
# --------------------------------------------------------------------------- #

class HostCase(SC.InventoryCase):
    """The Widget-theory fixture with a staged gate directory and a worktree at its root.

    The gate directory holds ``gate_file_rows(audited=True)`` and the vendored packages
    beside the fixture's gate.json and task manifests; every tool runs from it with
    ``python3 -I``, a bare environment and the fixture's root as the working directory.
    """

    KEYS = SC.CUT_3

    def setUp(self):
        super().setUp()
        self.fx.versions(**self.KEYS)
        self.gate_dir = self.fx.root / "gate"
        for rel, name in ltr.gate_file_rows(audited=True):
            shutil.copy2(SKILL / rel, self.gate_dir / name)
        for pkg in ltr.VENDORED:
            shutil.copytree(SKILL / "vendor" / pkg, self.gate_dir / "vendor" / pkg,
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copy2(SKILL / "vendor" / f"{pkg}.manifest.json",
                         self.gate_dir / "vendor" / f"{pkg}.manifest.json")
        self.doc_path = self.fx.document(TASK, SC.cut_body())
        self.doc_rel = str(self.doc_path.relative_to(self.fx.root))
        self.gate_json = str(self.gate_dir / "gate.json")

    def tool(self, script: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", str(self.gate_dir / script), *argv],
                              capture_output=True, text=True, cwd=str(self.fx.root),
                              env={"PATH": "/usr/bin:/bin", "HOME": str(self.fx.root)},
                              timeout=120)

    def doc(self) -> dict:
        return json.loads(self.doc_path.read_text(encoding="utf-8"))

    def write_doc(self, doc: dict) -> None:
        self.doc_path.write_text(json.dumps(doc), encoding="utf-8")

    def gate_ok(self) -> None:
        proc = self.tool("verify_locate.py", "--gate-config", self.gate_json, "--findings",
                         self.doc_rel)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def rekey(self) -> None:
        proc = self.tool("passage_ids.py", "--source-dir",
                         str(Path(self.fx.gate["sourceset"]["sources_dir"]) / SID),
                         "--document", self.doc_rel, "--write")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def ops_file(self, ops: list, name: str = "r1.ops.json") -> str:
        path = self.fx.root / ".tend" / "audit" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": ia.AUDIT_SCHEMA, "kind": "ops", "round": 1,
                                    "source_id": SID, "ops": ops}), encoding="utf-8")
        return str(path.relative_to(self.fx.root))


class TestEveryToolRunsAloneOnTheHost(HostCase):

    def test_every_import_is_the_standard_library_or_the_directory(self):
        import ast

        local = {p.stem for p in self.gate_dir.glob("*.py")}
        for _rel, name in ltr.AUDIT_GATE_FILES:
            tree = ast.parse((self.gate_dir / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                         [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for module in names:
                    top = module.split(".")[0]
                    with self.subTest(file=name, module=module):
                        self.assertNotEqual(top, "cflib")
                        self.assertTrue(top in sys.stdlib_module_names or top in local, module)

    def test_each_answers_help_under_python_dash_I(self):
        for _rel, name in ltr.AUDIT_GATE_FILES:
            with self.subTest(name=name):
                proc = self.tool(name, "--help")
                self.assertEqual(proc.returncode, 0, proc.stderr)


# --------------------------------------------------------------------------- #
# inventory_render.py
# --------------------------------------------------------------------------- #

GOLDEN = """\
# enwiki-1001-2002: inventory, 7 passages (5 formal)

Each passage: [n] item · span · scope · kind · prominence · section, then TEXT (the span on the layer; ⏎ marks a line break), STATEMENT, SHAPE and its conjuncts when compound, and HINT.
GAP lines are layer text no passage holds (past 400 characters, its first and last 200).
HINTs: kind-other (a formal passage of kind other); kind-rule (in a proof or an example section: the stage flags it wrong_kind unless the audit says, with a reason, that other is right); in-proof; in-example; connective-start (it starts at and, so, hence, thus, therefore, then or but); widened (it ends past its loose extent: only the wide bound of statement-cut rule 3 lets it run).
Worker notes: (none)

GAP: Widget theory ⏎
[1] @DEF1@ · [14, 69] · formal_math · definition · supporting · (lead)
  TEXT: A widget is finite when it has finitely many sprockets.
  STATEMENT: a synthetic restatement of the def1 passage
[2] @THM1@ · [70, 112] · formal_math · theorem · central · (lead)
  TEXT: Every finite widget has a spanning gadget.
  STATEMENT: a synthetic restatement of the thm1 passage
[3] @P2@ · [113, 214] · formal_math · theorem · supporting · (lead)
  TEXT: For every finite widget W{\\displaystyle W} the gadget count satisfies: ⏎ c(W)=2n{\\displaystyle c(W)=2n}
  STATEMENT: a synthetic restatement of the p2 passage
[4] @P3@ · [215, 266] · expository · other · supporting · (lead)
  TEXT: Here n{\\displaystyle n} is the order of the widget.
  STATEMENT: a synthetic restatement of the p3 passage
[5] @LIST@ · [267, 368] · formal_math · theorem · supporting · (lead)
  TEXT: a gadget of a finite widget is unique up to conjugacy; ⏎ a gadget of an infinite widget need not exist.
  STATEMENT: a synthetic restatement of the list passage
  SHAPE cases: c1: finite widgets have one gadget up to conjugacy | c2: infinite widgets may lack one
[6] @BOX@ · [369, 453] · formal_math · theorem · central · (lead)
  TEXT: Theorem. Every finite widget W{\\displaystyle W} has a spanning gadget. It is unique.
  STATEMENT: a synthetic restatement of the box passage
  SHAPE existence_uniqueness: c1: a finite widget has a spanning gadget | c2: there is at most one such gadget
[7] @P4@ · [454, 467] · expository · other · supporting · (lead)
  TEXT: It is unique.
  STATEMENT: a synthetic restatement of the p4 passage
GAP (to end):  ⏎ History The lemma was first stated in a synthetic note about widgets.

SKIPS: [{"span": [468, 537], "reason": "history"}]
"""


class TestTheRendering(HostCase):

    def render(self, *argv: str) -> subprocess.CompletedProcess:
        return self.tool("inventory_render.py", "--gate-config", self.gate_json, "--document",
                         self.doc_rel, *argv)

    def test_the_golden_rendering(self):
        proc = self.render()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.doc()
        want = GOLDEN
        for name in NAMES:
            want = want.replace(f"@{name}@", item_of(doc, name))
        self.assertEqual(proc.stdout, want)

    def test_a_layer_or_a_table_that_is_not_the_pin_is_exit_3(self):
        sdir = Path(self.fx.gate["sourceset"]["sources_dir"]) / SID
        layer = sdir / "layer.txt"
        layer.write_text(LAYER + " ", encoding="utf-8")
        proc = self.render()
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("not the rendering this run was planned on", proc.stderr)
        layer.write_text(LAYER, encoding="utf-8")
        self.fx.gate["sources"][SID]["views"] = {"formulas.json": "0" * 64,
                                                 "paragraphs.json": "0" * 64}
        self.fx.write_gate()
        proc = self.render()
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("formulas.json on this host is", proc.stderr)

    def test_the_parts_are_cut_between_passages(self):
        proc = self.render("--out-dir", ".tend/audit", "--label", "r1", "--part-bytes", "1500")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = self.fx.root / ".tend" / "audit"
        index = json.loads((out / "r1.render.json").read_text(encoding="utf-8"))
        self.assertGreater(len(index["parts"]), 2)
        doc = self.doc()
        self.assertEqual([i for p in index["parts"] for i in p["items"]],
                         [p["item"] for p in doc["passages"]])
        self.assertEqual([i for p in index["parts"] for i in p["formal"]], formal(doc))
        whole = self.render().stdout
        body = whole.split("\n\n", 2)[2].split("\n", 3)[3]     # past the header
        joined = []
        for n, part in enumerate(index["parts"], 1):
            text = (out / part["file"]).read_text(encoding="utf-8")
            self.assertEqual(len(text.encode("utf-8")), part["bytes"])
            head, rest = text.split(f"Part {n} of {len(index['parts'])}: ", 1)
            self.assertTrue(whole.startswith(head))
            lines = rest.split("\n")[2:]
            first = next(line for line in lines if line.startswith("["))
            self.assertTrue(first.startswith(f"[{part['first']}] "), first)
            joined.append("\n".join(lines).rstrip("\n"))
        self.assertEqual("\n".join(joined), whole[whole.index("GAP: Widget"):].rstrip("\n"))
        self.assertTrue(body)

    def test_a_passage_longer_than_a_part_is_a_part_of_its_own(self):
        doc = self.doc()
        parts = ir.parts(doc, LAYER, None, part_bytes=1000)
        self.assertEqual(len(parts), len(doc["passages"]))
        self.assertEqual([p["first"] for p in parts], list(range(1, len(parts) + 1)))

    def test_the_output_directory_is_the_worktrees_tend_audit_only(self):
        for where in ("elsewhere", ".tend", "../.tend/audit", "/tmp/x/.tend/audit"):
            with self.subTest(where=where):
                proc = self.render("--out-dir", where, "--label", "r1")
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn("writes only into .tend/audit/", proc.stderr)
        (self.fx.root / ".tend").mkdir(exist_ok=True)
        os.symlink(self.fx.root, self.fx.root / ".tend" / "audit")
        proc = self.render("--out-dir", ".tend/audit", "--label", "r1")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("symlink", proc.stderr)

    def test_show_prints_one_passage_with_its_context(self):
        doc = self.doc()
        proc = self.render("--show", item_of(doc, "THM1"), "--context", "10")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertTrue(lines[0].startswith(f"[2] {item_of(doc, 'THM1')} · [70, 112]"))
        self.assertEqual(lines[1], f"  BEFORE: {ir.flat(LAYER[60:70])}")
        self.assertEqual(lines[3], f"  AFTER: {ir.flat(LAYER[112:122])}")
        proc = self.render("--show", "In-text #00000000")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("is no passage of the document", proc.stderr)

    def test_the_hints(self):
        layer = "so the widget is finite. Then it has a gadget."
        row = {"item": "In-text #00000001", "span": [0, 24], "scope": "formal_math",
               "passage_kind": "other", "section": ["Proof of the lemma"]}
        self.assertEqual(ir.hints(row, layer, None),
                         ["kind-other", "kind-rule", "in-proof", "connective-start"])
        row.update(section=["Examples"], passage_kind="theorem", span=[25, 46])
        self.assertEqual(ir.hints(row, layer, None), ["in-example", "connective-start"])
        row.update(scope="expository", passage_kind="other")
        self.assertEqual(ir.hints(row, layer, None), ["in-example", "connective-start"])

        class Loose:
            def extent(self, start, *, compound=False, loose=False, **_):
                return 30 if loose else None
        row.update(scope="formal_math", section=[], span=[25, 46], passage_kind="theorem")
        self.assertEqual(ir.hints(row, layer, Loose()), ["connective-start", "widened"])
        row["span"] = [25, 29]
        self.assertEqual(ir.hints(row, layer, Loose()), ["connective-start"])

    def test_a_widened_merge_is_hinted_from_the_real_bound(self):
        doc = self.doc()
        blocks = SC.lt.Blocks(LAYER, SC.PARAGRAPHS, SC.FORMULAS)
        # P2 run on over P3: past the loose extent, so never gate-clean, but hinted
        doc["passages"][2]["span"] = [SC.SPANS["P2"][0], SC.SPANS["P3"][1]]
        self.assertIn("widened", ir.hints(doc["passages"][2], LAYER, blocks))
        self.assertNotIn("widened", ir.hints(self.doc()["passages"][2], LAYER, blocks))

    def test_the_diff_names_what_changed_was_added_and_was_removed(self):
        before = self.doc()
        snapshot = self.fx.root / ".tend" / "audit" / "r1.document.json"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(json.dumps(before), encoding="utf-8")
        ops = [{"op": "merge", "keep": item_of(before, "DEF1"), "drop": item_of(before, "THM1"),
                "verdict": "r1-v001"},
               {"op": "drop", "item": item_of(before, "P4"), "verdict": "r1-v002"},
               {"op": "split", "item": item_of(before, "LIST"), "verdict": "r1-v003",
                "at": {"exact": "a gadget of an infinite widget"},
                "new": {"statement": "an infinite widget may have no gadget",
                        "passage_kind": "theorem", "scope": "formal_math",
                        "prominence": "supporting"},
                "earlier": {"shape": None, "conjuncts": None, "until": "up to conjugacy;"}}]
        proc = self.tool("inventory_edit.py", "--document", self.doc_rel, "--ops",
                         self.ops_file(ops), "--write")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.rekey()
        proc = self.render("--out-dir", ".tend/audit", "--label", "r2", "--previous",
                           str(snapshot.relative_to(self.fx.root)))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = (self.fx.root / ".tend" / "audit" / "r2.diff.md").read_text(encoding="utf-8")
        after = self.doc()
        self.assertIn("2 changed, 1 added, 2 removed (7 passages before, 6 after)", text)
        self.assertIn(f"CHANGED {item_of(before, 'DEF1')} (fields: until, span)", text)
        self.assertIn(f"REMOVED {item_of(before, 'THM1')} (its text now in "
                      f"{item_of(before, 'DEF1')})", text)
        self.assertIn(f"REMOVED {item_of(before, 'P4')}\n", text)
        added = next(p for p in after["passages"] if p["item"] not in
                     {q["item"] for q in before["passages"]})
        self.assertIn(f"ADDED {added['item']} (inside {item_of(before, 'LIST')} before)", text)
        index = json.loads((self.fx.root / ".tend" / "audit" / "r2.render.json").read_text())
        self.assertEqual(index["diff"], "r2.diff.md")


# --------------------------------------------------------------------------- #
# inventory_edit.py
# --------------------------------------------------------------------------- #

class TestEachOperation(unittest.TestCase):

    def setUp(self):
        self.doc = good_doc()
        self.item = functools.partial(item_of, self.doc)

    def apply(self, *ops, ids=None):
        return ie.apply(self.doc, list(ops), verdict_ids=ids)

    def passage(self, doc, quote_start: str) -> dict:
        return next(p for p in doc["passages"] if p["quote"]["exact"].startswith(quote_start))

    def test_merge_keeps_the_earlier_start_and_the_later_end(self):
        out, log, problems = self.apply({"op": "merge", "keep": self.item("DEF1"),
                                         "drop": self.item("THM1"), "verdict": "r1-v001",
                                         "fields": {"statement": "a merged statement"}})
        self.assertEqual(problems, [])
        self.assertEqual(len(out["passages"]), 6)
        merged = out["passages"][0]
        self.assertEqual(merged["quote"], self.doc["passages"][0]["quote"])
        self.assertNotIn("until", merged)            # THM1 had none: the default end
        self.assertEqual(merged["statement"], "a merged statement")
        self.assertEqual(merged["passage_kind"], "definition")
        self.assertIn("absorbs", log[0])
        self.assertEqual(self.doc["passages"][0]["until"], "many sprockets.")   # a copy

    def test_merge_into_a_later_passage_takes_the_earlier_quote_and_finder(self):
        doc = good_doc()
        doc["passages"][4]["finder"] = "lead"
        out, _log, problems = ie.apply(doc, [{"op": "merge", "keep": item_of(doc, "BOX"),
                                              "drop": [item_of(doc, "LIST")],
                                              "verdict": "r1-v001"}])
        self.assertEqual(problems, [])
        box = next(p for p in out["passages"] if p["statement"].endswith("box passage"))
        self.assertEqual(box["quote"], doc["passages"][4]["quote"])
        self.assertEqual(box["finder"], "lead")
        self.assertNotIn("until", box)

    def test_merge_of_two_earlier_passages_keeps_the_earliest_start(self):
        out, _log, problems = self.apply({"op": "merge", "keep": self.item("P2"),
                                          "drop": [self.item("DEF1"), self.item("THM1")],
                                          "verdict": "r1-v001"})
        self.assertEqual(problems, [])
        p2 = next(p for p in out["passages"] if p["statement"].endswith("p2 passage"))
        self.assertEqual(p2["quote"], self.doc["passages"][0]["quote"])
        self.assertEqual(p2["until"], SC.GOOD[2][5])

    def test_split_hands_the_end_to_the_new_piece(self):
        out, _log, problems = self.apply(
            {"op": "split", "item": self.item("DEF1"), "verdict": "r1-v002",
             "at": {"exact": "when it has finitely"},
             "new": {"statement": "the condition", "passage_kind": "definition",
                     "scope": "formal_math", "prominence": "supporting", "section": ["X"]},
             "earlier": {"statement": "the head"}})
        self.assertEqual(problems, [])
        head, new = out["passages"][0], out["passages"][1]
        self.assertNotIn("until", head)
        self.assertEqual(head["statement"], "the head")
        self.assertEqual(new["until"], "many sprockets.")
        self.assertEqual(new["quote"], {"exact": "when it has finitely", "prefix": "",
                                        "suffix": ""})
        self.assertEqual((new["item"], new["span"], new["finder"], new["section"]),
                         ("", None, "model", ["X"]))

    def test_set_with_null_removes_and_quote_end_shortens(self):
        out, _log, problems = self.apply(
            {"op": "set", "item": self.item("LIST"), "verdict": "r1-v003",
             "fields": {"shape": None, "conjuncts": None, "until": None, "concept": "Q42",
                        "quote_end": "a gadget"}})
        self.assertEqual(problems, [])
        row = out["passages"][4]
        for key in ("shape", "conjuncts", "until"):
            self.assertNotIn(key, row)
        self.assertEqual(row["concept"], "Q42")
        self.assertEqual(row["quote"]["exact"], "a gadget")
        self.assertEqual(row["quote"]["suffix"], "")
        self.assertEqual(row["quote"]["prefix"], self.doc["passages"][4]["quote"]["prefix"])

    def test_a_passage_scoped_out_of_formal_loses_its_compound_and_its_prominence(self):
        out, _log, problems = self.apply({"op": "set", "item": self.item("BOX"),
                                          "verdict": "r1-v004",
                                          "fields": {"scope": "expository"}})
        self.assertEqual(problems, [])
        row = out["passages"][5]
        self.assertEqual((row["scope"], row["prominence"]), ("expository", "supporting"))
        self.assertNotIn("conjuncts", row)
        self.assertNotIn("shape", row)

    def test_add_and_drop(self):
        out, _log, problems = self.apply(
            {"op": "drop", "item": self.item("P4"), "verdict": "r1-v005"},
            {"op": "add", "after": None, "neighbour_of": "r1-v005",
             "quote": {"exact": "Widget theory"},
             "fields": {"statement": "a title", "passage_kind": "other",
                        "scope": "non_math", "prominence": "supporting"}})
        self.assertEqual(problems, [])
        self.assertEqual(out["passages"][0]["quote"]["exact"], "Widget theory")
        self.assertEqual(out["passages"][0]["section"], [])
        self.assertEqual(len(out["passages"]), 7)
        self.assertNotIn(self.item("P4"), {p["item"] for p in out["passages"]})

    def test_an_operation_that_cites_no_verdict_is_refused(self):
        bad = [({"op": "drop", "item": self.item("P4")}, "cites exactly one verdict"),
               ({"op": "drop", "item": self.item("P4"), "verdict": "r1-v001",
                 "neighbour_of": "r1-v002"}, "cites exactly one verdict"),
               ({"op": "drop", "item": self.item("P4"), "verdict": "v1"}, "not an id"),
               ({"op": "drop", "item": self.item("P4"), "verdict": "r1-v009"},
                "no verdict of this round")]
        for op, needle in bad:
            with self.subTest(op=op):
                _out, _log, problems = self.apply(op, ids={"r1-v001"})
                self.assertTrue(any(needle in p for p in problems), problems)

    def test_what_the_tool_refuses(self):
        cases = [
            ({"op": "drop", "item": "In-text #00000000", "verdict": "r1-v001"},
             "no passage of the base document"),
            ({"op": "set", "item": self.item("P4"), "verdict": "r1-v001",
              "fields": {"span": [1, 2]}}, "not a field an operation sets"),
            ({"op": "set", "item": self.item("P4"), "verdict": "r1-v001",
              "fields": {"passage_kind": "lemma"}}, "passage_kind is 'lemma'"),
            ({"op": "set", "item": self.item("P4"), "verdict": "r1-v001",
              "fields": {"quote_end": "absent words"}}, "is not in the quote"),
            ({"op": "split", "item": self.item("P4"), "verdict": "r1-v001",
              "at": {"exact": "is unique"}, "new": {"statement": "x"}},
             "a new passage needs 'passage_kind'"),
            ({"op": "merge", "keep": self.item("P4"), "drop": self.item("P4"),
              "verdict": "r1-v001"}, "keeps and drops the same passage"),
            ({"op": "merge", "keep": self.item("P3"), "drop": [self.item("P4"),
                                                              self.item("P4")],
              "verdict": "r1-v001"}, "drops the same passage twice"),
            ({"op": "drop", "verdict": "r1-v001"}, "drop needs 'item'"),
            ({"op": "rewrite", "verdict": "r1-v001"}, "not one of merge"),
        ]
        for op, needle in cases:
            with self.subTest(needle=needle):
                _out, _log, problems = self.apply(op)
                self.assertTrue(any(needle in p for p in problems), problems)
        _out, _log, problems = self.apply(
            {"op": "drop", "item": self.item("P4"), "verdict": "r1-v001"},
            {"op": "set", "item": self.item("P4"), "verdict": "r1-v001",
             "fields": {"statement": "y"}})
        self.assertTrue(any("dropped or merged away" in p for p in problems), problems)

    def test_named_items(self):
        ops = [{"op": "merge", "keep": "a", "drop": ["b", "c"]}, {"op": "add", "after": None},
               {"op": "set", "item": "d"}]
        self.assertEqual(ie.named_items(ops), {"a", "b", "c", "d"})


class TestTheEditToolOnTheHost(HostCase):

    def test_edit_then_passage_ids_then_the_gate(self):
        """A merge, re-keyed and gated; then the split that undoes it gives the good cut back."""
        good = self.doc()
        self.gate_ok()
        snapshot = self.fx.root / ".tend" / "audit" / "r1.document.json"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(json.dumps(good), encoding="utf-8")
        ops = [{"op": "merge", "keep": item_of(good, "DEF1"), "drop": item_of(good, "THM1"),
                "verdict": "r1-v001", "fields": {"statement": "a widget's definition and its "
                                                              "gadget theorem"}},
               {"op": "set", "item": item_of(good, "P3"), "verdict": "r1-v002",
                "fields": {"scope": "formal_math", "passage_kind": "notation"}},
               {"op": "drop", "item": item_of(good, "P4"), "verdict": "r1-v003"}]
        verdicts = self.fx.root / ".tend" / "audit" / "r1.verdicts.json"
        verdicts.write_text(json.dumps({"verdicts": [{"id": f"r1-v00{n}"} for n in (1, 2, 3)]}))
        argv = ("--document", self.doc_rel, "--ops", self.ops_file(ops), "--base",
                str(snapshot.relative_to(self.fx.root)), "--verdicts",
                str(verdicts.relative_to(self.fx.root)))
        dry = self.tool("inventory_edit.py", *argv)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn("dry run: nothing written", dry.stdout)
        self.assertEqual(self.doc(), good)
        proc = self.tool("inventory_edit.py", *argv, "--write")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.rekey()
        self.gate_ok()
        merged = self.doc()
        self.assertEqual(len(merged["passages"]), 5)
        self.assertEqual(merged["passages"][0]["span"], SC.SPANS["P1"])
        self.assertEqual(merged["passages"][2]["scope"], "formal_math")
        # the split back, from the merged document
        back = [{"op": "split", "item": merged["passages"][0]["item"], "verdict": "r2-v001",
                 "at": dict(good["passages"][1]["quote"]),
                 "new": {k: good["passages"][1][k] for k in ("statement", "passage_kind",
                                                             "scope", "prominence")},
                 "earlier": {"until": "many sprockets.",
                             "statement": good["passages"][0]["statement"]}},
                {"op": "set", "item": merged["passages"][2]["item"], "verdict": "r2-v002",
                 "fields": {"scope": "expository", "passage_kind": "other"}},
                {"op": "add", "after": merged["passages"][4]["item"], "verdict": "r2-v003",
                 "quote": dict(good["passages"][6]["quote"]),
                 "fields": {k: good["passages"][6][k] for k in ("statement", "passage_kind",
                                                                "scope", "prominence")}}]
        proc = self.tool("inventory_edit.py", "--document", self.doc_rel, "--ops",
                         self.ops_file(back, "r2.ops.json"), "--write")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.rekey()
        self.gate_ok()
        self.assertEqual([(p["item"], p["span"]) for p in self.doc()["passages"]],
                         [(p["item"], p["span"]) for p in good["passages"]])

    def test_it_writes_only_the_named_document(self):
        before = {p: p.read_bytes() for p in self.fx.root.rglob("*") if p.is_file()
                  and "__pycache__" not in p.parts}
        other = self.fx.root / "findings" / "t-other" / f"{SID}.json"
        other.parent.mkdir()
        other.write_text(self.doc_path.read_text())
        ops = self.ops_file([{"op": "drop", "item": item_of(self.doc(), "P4"),
                              "verdict": "r1-v001"}])
        for where, needle in ((f"{SID}.json", "not findings/<task>/<source>.json"),
                              ("../x/findings/a/b.json", "outside the working directory"),
                              ("findings/t-none/x.json", "is not a file"),
                              (ops, "not findings/<task>/<source>.json")):
            with self.subTest(where=where):
                proc = self.tool("inventory_edit.py", "--document", where, "--ops", ops,
                                 "--write")
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn(needle, proc.stderr)
        link = self.fx.root / "findings" / "t-link"
        os.symlink(other.parent, link)
        proc = self.tool("inventory_edit.py", "--document", f"findings/t-link/{SID}.json",
                         "--ops", ops, "--write")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("symlink", proc.stderr)
        proc = self.tool("inventory_edit.py", "--document", self.doc_rel, "--ops", ops,
                         "--write")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        after = {p: p.read_bytes() for p in self.fx.root.rglob("*") if p.is_file()
                 and not p.is_relative_to(other.parent) and not p.is_relative_to(link)
                 and "__pycache__" not in p.parts}
        changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
        self.assertEqual(changed, {self.doc_path, self.fx.root / ops})

    def test_a_refused_operation_writes_nothing(self):
        ops = self.ops_file([{"op": "drop", "item": item_of(self.doc(), "P4")}])
        before = self.doc_path.read_bytes()
        proc = self.tool("inventory_edit.py", "--document", self.doc_rel, "--ops", ops,
                         "--write")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("REFUSED: ops.ops[0]: an operation cites exactly one verdict", proc.stdout)
        self.assertIn("nothing written", proc.stdout)
        self.assertEqual(self.doc_path.read_bytes(), before)


# --------------------------------------------------------------------------- #
# inventory_audit.py: validation
# --------------------------------------------------------------------------- #

class TestValidation(unittest.TestCase):

    def setUp(self):
        self.doc = good_doc()

    def problems(self, data, **kw):
        return ia.validate_audit(data, self.doc, **kw)

    def assertInvalid(self, problems, needle):
        self.assertTrue(any(needle in p for p in problems), (needle, problems))

    def test_a_sound_audit(self):
        self.assertEqual(self.problems(audit_file(self.doc, THM1=MERGE_DEF | {
            "merge_with": item_of(self.doc, "DEF1")})), [])

    def test_the_audit_failures(self):
        doc = self.doc
        good = audit_file(doc)
        cases = []
        data = copy.deepcopy(good)
        del data["passages"][0]
        cases.append((data, "1 formal passage(s) not judged"))
        data = copy.deepcopy(good)
        data["passages"].append(copy.deepcopy(data["passages"][0]))
        cases.append((data, "is judged 2 times, not once"))
        data = copy.deepcopy(good)
        data["passages"][0]["item"] = "In-text #00000000"
        cases.append((data, "is no passage of the document"))
        data = copy.deepcopy(good)
        data["passages"][0]["verdict"] = "fine"
        cases.append((data, "not one of clean"))
        data = copy.deepcopy(good)
        data["passages"][0]["origin"] = "preface"
        cases.append((data, "origin is 'preface'"))
        data = copy.deepcopy(good)
        data["passages"].append({**good["passages"][0], "item": item_of(doc, "P3")})
        cases.append((data, "not formal_math"))
        data = copy.deepcopy(good)
        data["passages"][0]["rule"] = 4
        cases.append((data, "a clean verdict gives rule 0"))
        data = copy.deepcopy(good)
        data["passages"][1].update(MERGE_DEF)
        cases.append((data, "over_cut names no merge_with"))
        data = copy.deepcopy(good)
        data["passages"][1].update(MERGE_DEF, rule=0)
        cases.append((data, "names no rule"))
        data = copy.deepcopy(good)
        data["passages"][1].update(MERGE_DEF, merge_with=item_of(doc, "DEF1"),
                                   reason="word " * 31)
        cases.append((data, "has 31 words, more than 30"))
        data = copy.deepcopy(good)
        data["passages"][1].update(MERGE_DEF, merge_with=item_of(doc, "DEF1"), action="")
        cases.append((data, "gives no action"))
        data = copy.deepcopy(good)
        data["passages"][0]["note"] = "x"
        cases.append((data, "has the key 'note'"))
        data = copy.deepcopy(good)
        data["round"] = 2
        cases.append((data, "audit.round is 2, not 1"))
        data = copy.deepcopy(good)
        data["source_id"] = "enwiki-1-2"
        cases.append((data, "not the document's"))
        data = copy.deepcopy(good)
        data["passages"][0]["kind_other_ok"] = True
        cases.append((data, "kind_other_ok on a passage of kind definition"))
        data = copy.deepcopy(good)
        data["expository_flags"] = [{"item": item_of(doc, "DEF1"), "rule": 6,
                                     "reason": "r", "action": "a"}]
        cases.append((data, "is already formal_math"))
        data = copy.deepcopy(good)
        data["patterns"] = ["p"] * 6
        cases.append((data, "more than 5"))
        data = copy.deepcopy(good)
        data["schema"] = "cf-inventory-audit/2"
        cases.append((data, "not 'cf-inventory-audit/1'"))
        for data, needle in cases:
            with self.subTest(needle=needle):
                self.assertInvalid(self.problems(data), needle)

    def test_a_part_judges_its_own_passages(self):
        doc = self.doc
        data = audit_file(doc)
        data["passages"] = data["passages"][:2]
        self.assertEqual(self.problems(data, expect=formal(doc)[:2]), [])
        self.assertInvalid(self.problems(data, expect=formal(doc)[:3]), "1 formal passage(s)")
        self.assertInvalid(self.problems(data, expect=formal(doc)[:1]),
                           "is not one this file is to judge")

    def test_the_skeptic_failures(self):
        doc = self.doc
        audit = audit_file(doc, THM1=MERGE_DEF | {"merge_with": item_of(doc, "DEF1")})
        flags = ia.strip(1, doc, audits=[audit])
        clean = [i for i in formal(doc) if i != item_of(doc, "THM1")]
        sound = skeptic_file(flags, examined=clean)
        self.assertEqual(ia.validate_skeptic(sound, doc, flags, round_=1), [])
        data = copy.deepcopy(sound)
        data["verdicts"] = []
        self.assertInvalid(ia.validate_skeptic(data, doc, flags, round_=1), "flag(s) not judged")
        data = copy.deepcopy(sound)
        data["examined"] = clean[:2]
        self.assertInvalid(ia.validate_skeptic(data, doc, flags, round_=1),
                           "holds 2 unflagged formal passage(s); the hunt judges at least 4")
        data = copy.deepcopy(sound)
        data["examined"] = clean + [item_of(doc, "THM1")]
        self.assertInvalid(ia.validate_skeptic(data, doc, flags, round_=1), "is flagged")
        data = copy.deepcopy(sound)
        data["verdicts"][0].update(upheld=True, verdict="clean")
        self.assertInvalid(ia.validate_skeptic(data, doc, flags, round_=1), "never clean")

    def test_the_floor_of_the_hunt(self):
        self.assertEqual(ia.skeptic_floor(4), 4)
        self.assertEqual(ia.skeptic_floor(50), 10)
        self.assertEqual(ia.skeptic_floor(184), 19)

    def test_the_repair_and_check_failures(self):
        verdicts = {"round": 1, "verdicts": [{"id": "r1-v001"}, {"id": "r1-v002"}]}
        entry = {"verdict_id": "r1-v001", "status": "applied", "why": ""}
        data = {"schema": ia.AUDIT_SCHEMA, "kind": "repair", "round": 1, "source_id": SID,
                "entries": [entry, {"verdict_id": "r1-v002", "status": "gate_refused",
                                    "why": "the gate refuses the merge"}]}
        self.assertInvalid(ia.validate_repair(data, verdicts, source_id=SID), "no gate_line")
        data["entries"][1]["gate_line"] = "[rule 10] passages[0]: ends past its extent"
        self.assertEqual(ia.validate_repair(data, verdicts, source_id=SID), [])
        data["entries"].pop()
        self.assertInvalid(ia.validate_repair(data, verdicts, source_id=SID), "not judged")
        check = {"schema": ia.AUDIT_SCHEMA, "kind": "check", "round": 2, "source_id": SID,
                 "checked": [{"verdict_id": "r1-v001", "status": "missing", "reason": ""}],
                 "collateral": [], "new": [], "regressions": [
                     {"verdict_id": "r1-v077", "reason": "undone"}]}
        problems = ia.validate_check(check, self.doc, verdicts, {"collateral": []}, round_=2)
        self.assertInvalid(problems, "1 verdict(s) not judged: r1-v002")
        self.assertInvalid(problems, "status missing gives no reason")
        self.assertInvalid(problems, "r1-v077 is no verdict of an earlier round")

    def test_the_command_line(self):
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "doc.json").write_text(json.dumps(self.doc))
        (root / "a.json").write_text(json.dumps(audit_file(self.doc)))
        bad = audit_file(self.doc)
        bad["passages"].pop()
        (root / "b.json").write_text(json.dumps(bad))
        run = functools.partial(subprocess.run, capture_output=True, text=True, cwd=root)
        tool = [sys.executable, "-I", str(SCRIPTS / "gate" / "inventory_audit.py"), "validate",
                "--kind", "audit", "--document", "doc.json", "--file"]
        proc = run([*tool, "a.json"])
        self.assertEqual((proc.returncode, proc.stdout), (0, "OK: the audit file is valid\n"))
        proc = run([*tool, "b.json"])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("INVALID: audit.passages: 1 formal passage(s) not judged", proc.stdout)
        proc = run([*tool, "missing.json"])
        self.assertEqual(proc.returncode, 2)


# --------------------------------------------------------------------------- #
# strip and the merges
# --------------------------------------------------------------------------- #

class MergeCase(unittest.TestCase):

    def setUp(self):
        self.doc = good_doc()
        self.item = functools.partial(item_of, self.doc)
        self.clean = lambda doc, *flagged: [i for i in formal(doc) if i not in flagged]

    def round1(self, audit, skeptic, doc=None):
        return ia.merge_round1(doc or self.doc, [audit], skeptic, layer=LAYER)


class TestStripAndRound1(MergeCase):

    def test_strip_withholds_actions_patterns_estimates_and_clean_judgments(self):
        audit = audit_file(self.doc, THM1=MERGE_DEF | {"merge_with": self.item("DEF1")})
        audit["patterns"] = ["over-cut leads"]
        audit["expository_flags"] = [{"item": self.item("P3"), "rule": 7, "reason": "notation",
                                      "action": "make it formal"}]
        flags = ia.strip(1, self.doc, audits=[audit])
        self.assertEqual(flags["flags"], [
            {"flag_id": "r1-f001", "item": self.item("THM1"), "verdict": "over_cut", "rule": 1,
             "merge_with": self.item("DEF1"), "reason": MERGE_DEF["reason"]},
            {"flag_id": "r1-f002", "item": self.item("P3"), "verdict": "should_be_formal",
             "rule": 7, "merge_with": "", "reason": "notation"}])
        text = json.dumps(flags)
        for withheld in ("merge into the definition", "over-cut leads", "make it formal",
                         "per_spec", "patterns"):
            self.assertNotIn(withheld, text)

    def test_upheld_plus_missed_less_refuted(self):
        doc = self.doc
        audit = audit_file(doc, THM1=MERGE_DEF | {"merge_with": self.item("DEF1")},
                           LIST={"verdict": "under_cut", "rule": 4, "reason": "two results",
                                 "action": "split at the semicolon"})
        flags = ia.strip(1, doc, audits=[audit])
        missed = [{"item": self.item("BOX"), "verdict": "wrong_prominence", "rule": 9,
                   "merge_with": "", "evidence": "Theorem. Every finite widget",
                   "reason": "a later restatement", "action": "set central"},
                  {"item": self.item("P2"), "verdict": "clean", "rule": 0, "merge_with": "",
                   "evidence": "", "reason": "", "action": ""},
                  {"item": self.item("DEF1"), "verdict": "under_cut", "rule": 1,
                   "merge_with": "", "evidence": "words that are nowhere",
                   "reason": "x", "action": "y"}]
        skeptic = skeptic_file(flags, upheld=[self.item("THM1")],
                               evidence="Every finite widget has a spanning gadget.",
                               examined=self.clean(doc, self.item("THM1"), self.item("LIST")),
                               missed=missed)
        skeptic["verdicts"][0]["verdict"] = "redundant"          # upheld, corrected
        merged = self.round1(audit, skeptic)
        got = [(v["id"], v["item"], v["verdict"], v["origin"]) for v in merged["verdicts"]]
        self.assertEqual(got, [("r1-v001", self.item("THM1"), "redundant", "auditor"),
                               ("r1-v002", self.item("BOX"), "wrong_prominence",
                                "skeptic_missed")])
        self.assertEqual(merged["verdicts"][0]["corrected_from"], "over_cut")
        self.assertEqual(merged["verdicts"][0]["action"], MERGE_DEF["action"])
        self.assertEqual([r["item"] for r in merged["refuted"]], [self.item("LIST")])
        counts = merged["counts"]
        self.assertEqual((counts["flagged"], counts["upheld"], counts["refuted"],
                          counts["corrected"], counts["missed_added"], counts["missed_dropped"],
                          counts["actionable"]), (2, 1, 1, 1, 1, 2, 2))
        self.assertEqual(counts["by_verdict"], {"redundant": 1, "wrong_prominence": 1})

    def test_an_upheld_flag_without_evidence_on_the_layer_counts_as_refuted(self):
        audit = audit_file(self.doc, THM1=MERGE_DEF | {"merge_with": self.item("DEF1")})
        flags = ia.strip(1, self.doc, audits=[audit])
        examined = self.clean(self.doc, self.item("THM1"))
        for evidence in ("", "a paraphrase of the theorem"):
            with self.subTest(evidence=evidence):
                skeptic = skeptic_file(flags, upheld=[self.item("THM1")], evidence=evidence,
                                       examined=examined)
                merged = self.round1(audit, skeptic)
                self.assertEqual(merged["verdicts"], [])
                self.assertEqual(merged["refuted"][0]["why"], "no_evidence")
                self.assertEqual(merged["counts"]["refuted_no_evidence"], 1)
        # the rendering's line mark and its spacing read as the layer's whitespace
        skeptic = skeptic_file(flags, upheld=[self.item("THM1")], examined=examined,
                               evidence=f"sprockets.  {ir.LINE_MARK} Every finite")
        skeptic["verdicts"][0]["evidence"] = "satisfies: ⏎ c(W)=2n"
        self.assertEqual(len(self.round1(audit, skeptic)["verdicts"]), 1)

    def test_the_kind_rule_is_mechanical(self):
        rows = SC.replace("P2", kind="other")
        doc = good_doc(rows)
        for p in doc["passages"]:
            p["section"] = ["Proof"]
        audit = audit_file(doc)
        flags = ia.strip(1, doc, audits=[audit])
        skeptic = skeptic_file(flags, examined=formal(doc))
        merged = ia.merge_round1(doc, [audit], skeptic, layer=LAYER)
        self.assertEqual([(v["item"], v["verdict"], v["rule"], v["origin"])
                          for v in merged["verdicts"]],
                         [(item_of(doc, "P2"), "wrong_kind", 6, "kind_rule")])
        self.assertEqual(merged["counts"]["kind_other_in_proof_or_example"], 1)
        # an auditor who says, with a reason, that other is right exempts it
        audit = audit_file(doc, P2={"kind_other_ok": True, "reason": "a formal remark"})
        merged = ia.merge_round1(doc, [audit], skeptic, layer=LAYER)
        self.assertEqual(merged["verdicts"], [])
        self.assertEqual(merged["kind_other_ok"][0]["item"], item_of(doc, "P2"))
        # an upheld wrong_kind on it is the verdict, not a second one
        audit = audit_file(doc, P2={"verdict": "wrong_kind", "rule": 6, "reason": "a theorem",
                                    "action": "set theorem"})
        flags = ia.strip(1, doc, audits=[audit])
        skeptic = skeptic_file(flags, upheld=[item_of(doc, "P2")], evidence="the gadget count",
                               examined=[i for i in formal(doc) if i != item_of(doc, "P2")])
        merged = ia.merge_round1(doc, [audit], skeptic, layer=LAYER)
        self.assertEqual([v["origin"] for v in merged["verdicts"]], ["auditor"])
        # outside a proof or an example section the rule is silent
        doc = good_doc(rows)
        audit = audit_file(doc)
        merged = ia.merge_round1(doc, [audit], skeptic_file(ia.strip(1, doc, audits=[audit]),
                                                            examined=formal(doc)), layer=LAYER)
        self.assertEqual(merged["verdicts"], [])


class TestTheDecisionTable(unittest.TestCase):

    def rounds(self, *rows):
        return [{"round": r, "actionable": a, "harm": h} for r, a, h in rows]

    def test_the_table(self):
        formal = 100                                              # T = 5
        cases = [
            # round, R, actionable, before, harm -> decision, revert_to
            ((1, 3, 0, None, False), ("approve", None)),
            ((1, 3, 1, None, False), ("request_changes", None)),
            ((2, 3, 6, 40, False), ("request_changes", None)),
            ((2, 3, 4, 40, False), ("approve", None)),          # below T
            ((2, 3, 40, 40, False), ("approve", None)),         # no progress
            ((2, 3, 6, 40, True), ("request_changes", None)),   # harm, still progressing
            ((2, 3, 4, 40, True), ("revert", 1)),
            ((2, 2, 30, 40, False), ("approve", None)),         # final
            ((3, 3, 30, 12, False), ("approve", None)),         # final, more than before
            ((3, 3, 2, 12, True), ("revert", 2)),
        ]
        history = {2: self.rounds((1, 40, False)),
                   3: self.rounds((1, 40, False), (2, 12, False))}
        for (k, r, n, before, harm), (decision, revert_to) in cases:
            with self.subTest(round=k, R=r, actionable=n, before=before, harm=harm):
                rounds = history.get(k, []) + self.rounds((k, n, harm))
                got = ia.decide(k, r, n, before, harm, formal, rounds)
                self.assertEqual(got[:2], (decision, revert_to), got[2])

    def test_the_threshold(self):
        self.assertEqual(ia.threshold(89), 5)
        self.assertEqual(ia.threshold(184), 6)

    def test_the_best_snapshot(self):
        self.assertEqual(ia.best_round(self.rounds((1, 40, False), (2, 9, True))), 1)
        self.assertEqual(ia.best_round(self.rounds((1, 40, False), (2, 9, False))), 2)
        self.assertEqual(ia.best_round(self.rounds((1, 9, False), (2, 9, False))), 2)
        self.assertEqual(ia.best_round([]), 1)


class TestRound2(MergeCase):
    """A round-1 verdicts file asking for four things, and a check of the repaired document."""

    def setUp(self):
        super().setUp()
        doc = self.doc
        audit = audit_file(doc, THM1=MERGE_DEF | {"merge_with": self.item("DEF1")},
                           LIST={"verdict": "under_cut", "rule": 4, "reason": "two results",
                                 "action": "split at the semicolon"},
                           BOX={"verdict": "wrong_prominence", "rule": 9, "reason": "later",
                                "action": "set supporting"},
                           P2={"verdict": "statement_misstates", "rule": 1, "reason": "wrong",
                               "action": "restate"})
        flags = ia.strip(1, doc, audits=[audit])
        skeptic = skeptic_file(flags, upheld=[f["flag_id"] for f in flags["flags"]],
                               evidence="widget", examined=[self.item("DEF1")])
        merged = ia.merge_round1(doc, [audit], skeptic, layer=LAYER)
        self.previous = ia.verdicts_file(1, 3, doc, "a" * 64, merged, None)
        self.assertEqual(self.previous["decision"], "request_changes")
        self.ids = {v["item"]: v["id"] for v in self.previous["verdicts"]}

    def check(self, statuses, *, collateral=(), new=(), regressions=()):
        return {"schema": ia.AUDIT_SCHEMA, "kind": "check", "round": 2, "source_id": SID,
                "checked": [{"verdict_id": self.ids[self.item(name)], "status": status,
                             "reason": "" if status == "applied" else "see the diff"}
                            for name, status in statuses.items()],
                "collateral": list(collateral), "new": list(new),
                "regressions": list(regressions)}

    def repair(self, **statuses):
        return {"schema": ia.AUDIT_SCHEMA, "kind": "repair", "round": 1, "source_id": SID,
                "entries": [{"verdict_id": self.ids[self.item(name)], "status": status,
                             "why": "" if status == "applied" else "the spec allows it"}
                            for name, status in statuses.items()]}

    def merge(self, check, repair, collateral_doc, *, disputes=(), upheld=(), review_rounds=3):
        flags = ia.strip(2, self.doc, checks=[check], repair=repair, previous=self.previous)
        skeptic = skeptic_file(flags, round_=2, upheld=upheld, evidence="widget",
                               disputes=list(disputes))
        self.assertEqual(ia.validate_check(check, self.doc, self.previous, collateral_doc,
                                           round_=2), [])
        self.assertEqual(ia.validate_skeptic(skeptic, self.doc, flags, round_=2), [])
        merged = ia.merge_later(2, self.doc, [check], skeptic, self.previous, repair,
                                collateral_doc, layer=LAYER)
        return ia.verdicts_file(2, review_rounds, self.doc, "b" * 64, merged, self.previous)

    def test_statuses_disputes_and_gate_refusals(self):
        check = self.check({"THM1": "applied", "LIST": "gate_refused_ok", "BOX": "disputed",
                            "P2": "partial"})
        repair = self.repair(THM1="applied", LIST="gate_refused", BOX="disputed",
                             P2="applied")
        repair["entries"][1]["gate_line"] = "[rule 10] refused"
        out = self.merge(check, repair, {"collateral": []}, disputes=[True])
        self.assertEqual([(v["origin"], v["of"]) for v in out["verdicts"]],
                         [("carried", self.ids[self.item("P2")])])
        self.assertEqual(sorted(e["why"] for e in out["set_aside"]),
                         ["dispute_upheld", "gate_refused_ok"])
        self.assertEqual([h["id"] for h in out["history"]], [self.ids[self.item("THM1")]])
        self.assertEqual(out["counts"]["disputed_upheld"], 1)
        # 1 actionable < T: approve, and nothing harmful was found
        self.assertEqual(out["decision"], "approve")
        self.assertEqual([r["round"] for r in out["rounds"]], [1, 2])
        refuted = self.merge(check, repair, {"collateral": []}, disputes=[False])
        self.assertEqual(sorted(v["origin"] for v in refuted["verdicts"]),
                         ["carried", "dispute_refuted"])

    def test_harmful_collateral_a_regression_and_a_wrong_application_revert(self):
        collateral_doc = {"collateral": [{"id": "r2-x001", "change": "moved",
                                          "item": self.item("P3"), "fields": ["span"]}]}
        for check in (
                self.check({"THM1": "wrong", "LIST": "applied", "BOX": "applied",
                            "P2": "applied"}, collateral=[{"id": "r2-x001", "harmful": False,
                                                           "reason": ""}]),
                self.check({n: "applied" for n in ("THM1", "LIST", "BOX", "P2")},
                           collateral=[{"id": "r2-x001", "harmful": True,
                                        "reason": "it swallowed a definition"}]),
                self.check({n: "applied" for n in ("THM1", "LIST", "BOX")} | {"P2": "applied"},
                           collateral=[{"id": "r2-x001", "harmful": False, "reason": ""}],
                           regressions=[{"verdict_id": self.ids[self.item("LIST")],
                                         "reason": "the split was undone"}])):
            with self.subTest(check=check):
                out = self.merge(check, None, collateral_doc, review_rounds=2)
                self.assertTrue(out["harm"])
                self.assertEqual((out["decision"], out["revert_to"]), ("revert", 1))
                result = ia.result_for(out, "r2.verdicts.json")
                self.assertEqual(result["verdict"], "request_changes")
                self.assertIn("revert to the round 1 snapshot", result["feedback_text"])
        out = self.merge(self.check({n: "applied" for n in ("THM1", "LIST", "BOX", "P2")},
                                    collateral=[{"id": "r2-x001", "harmful": True,
                                                 "reason": "it swallowed a definition"}]),
                         None, collateral_doc)
        self.assertEqual([(v["verdict"], v["origin"], v["item"]) for v in out["verdicts"]],
                         [("revert", "collateral", self.item("P3"))])

    def test_new_entries_need_the_skeptic(self):
        new = [{"item": self.item("DEF1"), "verdict": "wrong_kind", "rule": 6,
                "merge_with": "", "reason": "a theorem", "action": "set theorem"}]
        check = self.check({n: "applied" for n in ("THM1", "LIST", "BOX", "P2")}, new=new)
        out = self.merge(check, None, {"collateral": []}, upheld=["r2-f001"])
        self.assertEqual([v["origin"] for v in out["verdicts"]], ["check_new"])
        out = self.merge(check, None, {"collateral": []})
        self.assertEqual(out["verdicts"], [])
        self.assertEqual(out["counts"]["refuted"], 1)

    def test_an_invalid_check_is_refused_before_any_merge(self):
        check = self.check({"THM1": "applied"})
        problems = ia.validate_check(check, self.doc, self.previous, {"collateral": []},
                                     round_=2)
        self.assertTrue(any("3 verdict(s) not judged" in p for p in problems), problems)


class TestCollateral(HostCase):

    def snapshot(self) -> dict:
        return self.doc()

    def test_what_the_cited_operations_produce_is_no_collateral(self):
        before = self.snapshot()
        ops = ops_doc([{"op": "drop", "item": item_of(before, "P4"), "verdict": "r1-v001"},
                       {"op": "set", "item": item_of(before, "P3"), "verdict": "r1-v002",
                        "fields": {"statement": "renamed"}}])
        result, _log, problems = ie.apply(before, ops["ops"])
        self.assertEqual(problems, [])
        self.write_doc(result)
        self.rekey()
        out = ia.collateral(2, before, self.doc(), ops,
                            {"verdicts": [{"id": "r1-v001"}, {"id": "r1-v002"}]})
        self.assertEqual(out["collateral"], [])

    def test_a_hand_edit_a_moved_neighbour_and_an_uncited_operation(self):
        before = self.snapshot()
        # THM1 dropped: DEF1's end is its until, so it does not move; drop P2 instead,
        # whose neighbour THM1 has no until and runs on to its block's end
        ops = ops_doc([{"op": "drop", "item": item_of(before, "THM1"), "verdict": "r1-v001"},
                       {"op": "set", "item": item_of(before, "LIST"), "verdict": "r1-v009",
                        "fields": {"statement": "restated"}}])
        result, _log, problems = ie.apply(before, ops["ops"])
        self.assertEqual(problems, [])
        result["passages"][0].pop("until")                          # DEF1 now runs on
        result["passages"][-1]["statement"] = "changed by hand"     # P4
        self.write_doc(result)
        self.rekey()
        out = ia.collateral(2, before, self.doc(), ops, {"verdicts": [{"id": "r1-v001"}]})
        changes = [(e["change"], e.get("item"), tuple(e.get("fields") or ())) for e in
                   out["collateral"]]
        now = self.doc()
        self.assertIn(("uncited_op", None, ()), changes)
        self.assertIn(("hand_edit", now["passages"][0]["item"], ("until",)), changes)
        self.assertIn(("hand_edit", now["passages"][-1]["item"], ("statement",)), changes)
        self.assertEqual([e["id"] for e in out["collateral"]],
                         [f"r2-x{n:03d}" for n in range(1, len(out["collateral"]) + 1)])

    def test_a_neighbour_whose_default_end_moved(self):
        before = self.snapshot()
        rows = SC.replace("DEF1", until=None)
        doc = SC.cut_body(rows)
        before = {**self.doc(), "passages": doc["passages"]}
        ops = ops_doc([{"op": "drop", "item": item_of(before, "THM1"), "verdict": "r1-v001"}])
        result, _log, _problems = ie.apply(before, ops["ops"])
        self.write_doc(result)
        self.rekey()
        out = ia.collateral(2, before, self.doc(), ops, {"verdicts": [{"id": "r1-v001"}]})
        self.assertEqual([(e["change"], e["was"]) for e in out["collateral"]],
                         [("moved", item_of(before, "DEF1"))])

    def test_a_merge_that_drops_one_passage_twice_is_refused_not_a_crash(self):
        """The validator refuses the repeated item, so the edit tool and ``collateral``
        report it (``ops_refused``) where ``list.remove`` once raised."""
        before = self.snapshot()
        b = item_of(before, "P4")
        op = {"op": "merge", "keep": item_of(before, "P3"), "drop": [b, b],
              "verdict": "r1-v001"}
        self.assertTrue(any("drops the same passage twice" in p
                            for p in ia.op_problems(op, "ops[0]")))
        self.assertTrue(ia.validate_ops(ops_doc([op]), before))
        _out, _log, problems = ie.apply(before, [op])
        self.assertTrue(problems)
        out = ia.collateral(2, before, before, ops_doc([op]), {"verdicts": [{"id": "r1-v001"}]})
        self.assertEqual([e["change"] for e in out["collateral"]], ["ops_refused"])
        # the edit's own guard holds even past a validator that let it through
        with mock.patch.object(ia, "op_problems", return_value=[]):
            _out, _log, problems = ie.apply(before, [op])
        self.assertEqual(problems, ["ops[0]: merge names one passage twice"])

    def test_operations_the_tool_refuses_make_every_change_a_hand_edit(self):
        before = self.snapshot()
        doc = copy.deepcopy(before)
        doc["passages"][3]["statement"] = "by hand"
        out = ia.collateral(2, before, doc, ops_doc([{"op": "drop"}]), None)
        self.assertEqual([e["change"] for e in out["collateral"]], ["ops_refused", "hand_edit"])


class TestTheMergeCommand(HostCase):
    """``merge`` from the command line, as the bridge runs it: inputs validated, the
    verdicts and the reviewer's result written only where they belong."""

    def setUp(self):
        super().setUp()
        self.audit_dir = self.fx.root / ".tend" / "audit"
        self.audit_dir.mkdir(parents=True)
        doc = self.doc()
        self.audit = audit_file(doc, THM1=MERGE_DEF | {"merge_with": item_of(doc, "DEF1")})
        self.flags = ia.strip(1, doc, audits=[self.audit])
        self.skeptic = skeptic_file(self.flags, upheld=[item_of(doc, "THM1")],
                                    evidence="Every finite widget has",
                                    examined=[i for i in formal(doc)
                                              if i != item_of(doc, "THM1")])
        for name, data in (("r1.audit.json", self.audit), ("r1.skeptic.json", self.skeptic)):
            (self.audit_dir / name).write_text(json.dumps(data), encoding="utf-8")

    def merge(self, *extra: str) -> subprocess.CompletedProcess:
        return self.tool("inventory_audit.py", "merge", "--round", "1", "--review-rounds", "3",
                         "--gate-config", self.gate_json, "--document", self.doc_rel,
                         "--audit", ".tend/audit/r1.audit.json",
                         "--skeptic", ".tend/audit/r1.skeptic.json", *extra)

    def test_it_writes_the_verdicts_and_the_result(self):
        proc = self.merge("--out", ".tend/audit/r1.verdicts.json",
                          "--result", ".tend/agent_result.json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        verdicts = json.loads((self.audit_dir / "r1.verdicts.json").read_text())
        self.assertEqual(verdicts["decision"], "request_changes")
        self.assertEqual(verdicts["document_sha256"], ia.sha256_file(self.doc_path))
        self.assertEqual(verdicts["decision_rule"], "audit/1")
        self.assertEqual([v["id"] for v in verdicts["verdicts"]], ["r1-v001"])
        result = json.loads((self.fx.root / ".tend" / "agent_result.json").read_text())
        self.assertEqual(result["verdict"], "request_changes")
        self.assertIn(".tend/audit/r1.verdicts.json", result["feedback_text"])
        self.assertLess(len(result["feedback_text"]), 1500)

    def test_strip_and_validate_from_the_command_line(self):
        proc = self.tool("inventory_audit.py", "strip", "--round", "1", "--document",
                         self.doc_rel, "--audit", ".tend/audit/r1.audit.json",
                         "--out", ".tend/audit/r1.flags.json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads((self.audit_dir / "r1.flags.json").read_text()),
                         self.flags)
        proc = self.tool("inventory_audit.py", "validate", "--kind", "skeptic", "--file",
                         ".tend/audit/r1.skeptic.json", "--document", self.doc_rel,
                         "--flags", ".tend/audit/r1.flags.json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_an_invalid_input_is_exit_1_and_writes_nothing(self):
        self.audit["passages"].pop()
        (self.audit_dir / "r1.audit.json").write_text(json.dumps(self.audit))
        proc = self.merge("--out", ".tend/audit/r1.verdicts.json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("INVALID:", proc.stdout)
        self.assertFalse((self.audit_dir / "r1.verdicts.json").exists())

    def test_an_audit_part_is_validated_against_its_part_of_the_rendering(self):
        proc = self.tool("inventory_render.py", "--gate-config", self.gate_json, "--document",
                         self.doc_rel, "--out-dir", ".tend/audit", "--label", "r1",
                         "--part-bytes", "1500")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        index = json.loads((self.audit_dir / "r1.render.json").read_text())
        first = index["parts"][0]["formal"]
        part = copy.deepcopy(self.audit)
        part["passages"] = [r for r in part["passages"] if r["item"] in first]
        (self.audit_dir / "r1.audit.part1.json").write_text(json.dumps(part))
        argv = ("inventory_audit.py", "validate", "--kind", "audit", "--file",
                ".tend/audit/r1.audit.part1.json", "--document", self.doc_rel,
                "--render-index", ".tend/audit/r1.render.json")
        proc = self.tool(*argv, "--part", "1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = self.tool(*argv, "--part", "2")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("is not one this file is to judge", proc.stdout)
        proc = self.tool(*argv, "--part", "99")
        self.assertEqual(proc.returncode, 2)
        doc = self.doc()
        doc["notes"] = "edited after the rendering"
        self.write_doc(doc)
        proc = self.tool(*argv, "--part", "1")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("is of another document", proc.stderr)

    def test_audit_failed_approves_and_says_so(self):
        proc = self.tool("inventory_audit.py", "merge", "--round", "1", "--review-rounds", "2",
                         "--document", self.doc_rel, "--audit-failed", "invalid twice",
                         "--out", ".tend/audit/r1.verdicts.json",
                         "--result", ".tend/agent_result.json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        verdicts = json.loads((self.audit_dir / "r1.verdicts.json").read_text())
        self.assertEqual((verdicts["decision"], verdicts["audit_failed"], verdicts["verdicts"]),
                         ("approve", True, []))
        result = json.loads((self.fx.root / ".tend" / "agent_result.json").read_text())
        self.assertEqual(result["verdict"], "approve")

    def test_outputs_go_only_under_tend(self):
        for flag, where in (("--out", "findings/x.json"), ("--out", "r1.verdicts.json"),
                            ("--result", ".tend/audit/agent_result.json")):
            with self.subTest(where=where):
                argv = {"--out": ".tend/audit/r1.verdicts.json", flag: where}
                proc = self.merge(*[x for kv in argv.items() for x in kv])
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_a_layer_that_is_not_the_pin_is_exit_3(self):
        sdir = Path(self.fx.gate["sourceset"]["sources_dir"]) / SID
        (sdir / "layer.txt").write_text(LAYER + "\n", encoding="utf-8")
        proc = self.merge("--out", ".tend/audit/r1.verdicts.json")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)


# --------------------------------------------------------------------------- #
# staging: only an audited plan carries the tools
# --------------------------------------------------------------------------- #

class TestOnlyAnAuditedPlanStagesTheTools(Campaign):

    def test_an_unaudited_plan_stages_none(self):
        gate_dir = self.paths.tend_root("inv-1") / "gate"
        for _rel, name in ltr.AUDIT_GATE_FILES:
            self.assertFalse((gate_dir / name).exists(), name)
        pins = read_yaml(self.paths.run_yaml("inv-1"))["locate"]["gate_files"]
        self.assertFalse([k for k in pins if "inventory_" in k])
        self.assertEqual(ltr.gate_file_rows(), ltr.GATE_FILES)

    def test_an_audited_plan_stages_and_pins_all_three(self):
        code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                            "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-a",
                            "--audit-rounds", "3")
        self.assertEqual(code, 0, out + err)
        gate_dir = self.paths.tend_root("inv-a") / "gate"
        pins = read_yaml(self.paths.run_yaml("inv-a"))["locate"]["gate_files"]
        for rel, name in ltr.AUDIT_GATE_FILES:
            self.assertEqual((gate_dir / name).read_bytes(), (SKILL / rel).read_bytes())
            self.assertIn(f"gate/{name}", pins)
            proc = subprocess.run([sys.executable, "-I", str(gate_dir / name), "--help"],
                                  cwd=self.root.parent, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_missing_tool_is_a_planning_error_for_an_audited_plan(self):
        absent = "/".join(("scripts", "gate", "no_such_tool.py"))   # not a citation
        rows = (*ltr.AUDIT_GATE_FILES, (absent, "no_such_tool.py"))
        with mock.patch.object(ltr, "AUDIT_GATE_FILES", rows):
            code, out, err = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                                "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-b",
                                "--audit-rounds", "2")
        self.assertNotEqual(code, 0)
        self.assertIn("no_such_tool.py", out + err)


if __name__ == "__main__":
    unittest.main()
