"""The locate gate before Tier A: the span end, the label, the views and the two pins.

New code, for references/locate-documents.md sections 2, 5, 9 and 10 and
docs/design/stage_map.md section 4.0:

* **the span end** (gate rule 2): an inventory passage ends where
  ``passage_ids.py`` ends it (``locate_text.passage_end``), so an end edited by
  hand, moved into the next passage or counted in UTF-16 units is refused --
  pinned on a layer of astral-plane characters, where a UTF-16 end differs --
  in a run whose ``gate.json`` names ``span_end_rule``, and no other: an
  inventory in the old shape (its end placed by an ``until`` the earlier
  ``passage_ids.py`` then dropped) still passes under a gate.json without the
  key, also at a re-gate by the checkout's gate, and a locate run that reuses
  it gates as before;
* **the label**: the command line refuses ``--findings`` with
  ``--changed-files``, an abbreviation, a repeated ``--gate-config``,
  ``--pre-merge`` alone and, in a gate directory, any config but its own; the
  bridge allowlists a locate agent only the ``--findings`` check while a
  faithfulness worker keeps the bare gate; the log line says its ``caller``;
* **the views**: a source pin's ``views`` holds ``formulas.json`` and
  ``paragraphs.json`` to the shas the planner copied from ``render.json``;
* **the two holes**: the gate compares ``corpus.sqlite`` with its pinned sha
  (a doctored database no longer passes a phantom declaration) and each
  candidates file with ``candidates_sha256``; ``cf harvest`` re-gates with the
  run's own gate copy and records its hash -- from its sources only (a planted
  ``.pyc`` never runs), with a config that is the staged ``gate.json`` but for
  its paths and the task manifests ``planned.json`` records, and a refusal that
  names what settles it.

Every page, library and declaration is made up (the "Widget lemma", page id
1001; libraries ``alpha`` and ``beta``).
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import importlib.machinery
import io
import json
import os
import py_compile
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
for _p in (SCRIPTS, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import _locate_fixtures as F  # noqa: E402
import test_bridge_hardening as tbh  # noqa: E402
import test_verify_locate as tvl  # noqa: E402
from bench import metrics  # noqa: E402
from cf import main as cf_main  # noqa: E402
from cflib import hashing, vocab  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from cflib.yamlio import read_yaml, write_yaml  # noqa: E402
from gate import verify_locate as vl  # noqa: E402
from harvest import gate_corpus  # noqa: E402
from locate import passage_ids, plan as lplan, tend_root as ltr  # noqa: E402
from run import remote, watchdog  # noqa: E402
from sources import render_html  # noqa: E402

GATE = tvl.GATE
SID, TASK = tvl.SID, tvl.TASK


_sha = tvl._sha


def environ(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_CONFIG_", "TEND_"))}
    env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# F4: the span end
# --------------------------------------------------------------------------- #

class TestTheSpanEnd(tvl.GateTestCase):
    """An inventory passage ends where passage_ids.py's rule ends it, and nowhere else.

    Span-end rule 1, which every inventory planned before the statement cut's flip names
    (a run planned now names rule 2: tests/test_locate_gate_statement_cut.py).
    """

    def setUp(self):
        self.fx = tvl.Fixture(self, "locate-inventory")
        self.fx.gate["span_end_rule"] = 1          # a run planned before the flip
        self.fx.write_gate()
        self.fx.manifest(TASK, tvl.inventory_manifest_doc(self.fx))

    def run_body(self, body):
        return self.fx.run(self.fx.document(TASK, body))

    def test_the_paragraph_end_passes(self):
        self.assertOk(self.run_body(tvl.inventory_body()))

    def test_an_end_moved_by_hand_is_refused(self):
        """The review's probe: the lead's end moved 1, 7 and 40 characters (7 and 40 run
        into the kept theorem passage), and shrunk to its quote, all passed before."""
        for delta in (1, 7, 40):
            with self.subTest(delta=delta):
                body = tvl.inventory_body()
                a, b = body["passages"][0]["span"]
                body["passages"][0]["span"] = [a, b + delta]
                self.assertRule(self.run_body(body), 2, f"passage_ids.py ends it at {b}")
        body = tvl.inventory_body()
        a, _b = body["passages"][0]["span"]
        body["passages"][0]["span"] = [a, a + len(body["passages"][0]["quote"]["exact"])]
        self.assertRule(self.run_body(body), 2, "the end of the paragraph it starts in")
        body = tvl.inventory_body()
        body["passages"][1]["span"] = [tvl.THEOREM_SPAN[0], len(tvl.LAYER)]
        self.assertRule(self.run_body(body), 2, "passage_ids.py ends it at")

    def test_an_end_past_the_paragraph_needs_the_until_that_puts_it_there(self):
        body = tvl.inventory_body()
        a = body["passages"][0]["span"][0]
        end = tvl.THEOREM_SPAN[0] + len("Theorem.")
        body["passages"][0]["span"] = [a, end]
        self.assertRule(self.run_body(body), 2, "the end of the paragraph it starts in")
        body["passages"][0]["until"] = "Theorem."
        self.assertOk(self.run_body(body))
        # the until's own end, and nothing else, once it is given
        body["passages"][0]["span"] = list(tvl.LEAD_SPAN)
        self.assertRule(self.run_body(body), 2, "the end of its `until`")
        body["passages"][0]["until"] = "no such words"
        self.assertRule(self.run_body(body), 2, "is not on the layer")
        body["passages"][0]["until"] = 7
        self.assertRule(self.run_body(body), 2, "not the passage's last words")

    def test_the_tool_writes_what_the_gate_reads(self):
        """passage_ids keeps `until`, so the gate can re-compute the end it wrote."""
        source = self.fx.root / "sourcesets" / "wpm" / "sources" / SID
        layer = (source / "layer.txt").read_text(encoding="utf-8")
        formulas = json.loads((source / "formulas.json").read_text(encoding="utf-8"))
        paragraphs = json.loads((source / "paragraphs.json").read_text(encoding="utf-8"))
        body = tvl.inventory_body()
        for p in body["passages"]:
            for key in ("item", "head_words", "span"):
                p.pop(key)
        body["passages"][0]["until"] = "Theorem."
        self.assertEqual(passage_ids.place(body, layer, formulas, paragraphs), [])
        self.assertEqual(body["passages"][0]["until"], "Theorem.")
        self.assertEqual(body["passages"][0]["span"],
                         [tvl.LEAD_SPAN[0], tvl.THEOREM_SPAN[0] + len("Theorem.")])
        self.assertEqual(body["passages"][1]["span"], list(tvl.THEOREM_SPAN))
        self.assertOk(self.run_body(body))
        # a re-run places the same spans: the until is still there to place it by
        again = copy.deepcopy(body)
        self.assertEqual(passage_ids.place(again, layer, formulas, paragraphs), [])
        self.assertEqual(again["passages"], body["passages"])

    def test_the_bench_classes_the_refusal_as_a_span_end(self):
        body = tvl.inventory_body()
        a, b = body["passages"][0]["span"]
        body["passages"][0]["span"] = [a, b + 7]
        result = self.run_body(body)
        reason = next(p for p in result["problems"] if "passage_ids.py ends it" in p)
        self.assertEqual(metrics.classify_reason(reason), "rule_2:span_end")


class TestTheAstralLayer(tvl.GateTestCase):
    """F4 on a layer of astral-plane characters: every offset counts code points.

    A JavaScript consumer slices with UTF-16 units, where each of these letters
    counts two; the end of the passage counted that way, [43, 120], lies inside
    the layer and after the quote, so the old bound admitted it, and the record
    would have highlighted six characters of the next paragraph.
    """

    BODY = ('<div class="mw-parser-output"><p>Let \U0001D53D be a field and \U0001D540 an '
            'ideal.</p>\n<h2 id="Statement">Statement</h2>\n'
            '<p>The widget lemma holds for every \U0001D53D-module \U0001D544 with '
            '\U0001D53E acting, and \U0001F600 never.</p>\n'
            '<p>Sprockets turn in every finite case of \U0001D542.</p></div>')
    QUOTE = "The widget lemma holds for every"

    def setUp(self):
        self.fx = tvl.Fixture(self, "locate-inventory", curated=False)
        rendered = render_html.render(self.BODY)
        self.layer, self.paragraphs = rendered.layer, rendered.paragraphs
        source = self.fx.root / "sourcesets" / "wpm" / "sources" / SID
        (source / "layer.txt").write_text(self.layer, encoding="utf-8")
        (source / "formulas.json").write_text(json.dumps(rendered.formulas), encoding="utf-8")
        (source / "paragraphs.json").write_text(json.dumps(self.paragraphs), encoding="utf-8")
        self.fx.layer_sha = _sha(self.layer.encode("utf-8"))
        self.fx.gate["sources"][SID]["layer_sha256"] = self.fx.layer_sha
        self.fx.gate["span_end_rule"] = vocab.SPAN_END_RULE
        self.fx.write_gate()
        self.fx.manifest(TASK, dict(tvl.inventory_manifest_doc(self.fx), finders=[]))
        doc = {"passages": [{"quote": {"exact": self.QUOTE, "prefix": "", "suffix": ""}}]}
        self.assertEqual(passage_ids.place(doc, self.layer, rendered.formulas,
                                           self.paragraphs), [])
        self.placed = doc["passages"][0]

    def body(self, span) -> dict:
        passage = dict(self.placed, span=list(span), section=["Statement"], fragment=None,
                       passage_kind="theorem", prominence="subject", scope="formal_math",
                       statement="a synthetic restatement", concept=None, finder="model")
        skips = [{"span": p["span"], "reason": "the lead"} for p in self.paragraphs
                 if p.get("math") and p["span"][1] <= span[0]]
        return {"passages": [passage], "skips": skips, "notes": ""}

    def utf16(self, offset: int) -> int:
        return len(self.layer[:offset].encode("utf-16-le")) // 2

    def test_the_code_point_end_passes_and_the_utf16_end_fails(self):
        start, end = self.placed["span"]
        self.assertEqual((start, end), (43, 114))
        self.assertOk(self.fx.run(self.fx.document(TASK, self.body([start, end]))))
        wrong = [start, self.utf16(end)]
        self.assertEqual(wrong, [43, 120])
        self.assertIsNotNone(vl._span(wrong, len(self.layer)))      # what the old bound admitted
        self.assertRule(self.fx.run(self.fx.document(TASK, self.body(wrong))), 2,
                        "passage_ids.py ends it at 114")


class TestTheSpanEndBindsTheRunsPlannedUnderIt(tvl.GateTestCase):
    """Rule 2's end check binds a run whose gate.json names ``span_end_rule``, and no other.

    The final review's probe: ``passage_ids.py`` before the rule placed an end by
    the passage's ``until`` and then dropped the ``until``, so the end check,
    applied to every inventory, refused 117 passages of a gated smoke inventory
    at a re-gate by the checkout's gate.  The planner now writes the key, as it
    writes ``judgment_rules``; a gate.json without it keeps the bound the run was
    gated under (inside the layer, after the quote).
    """

    UNTIL = "Theorem."

    def setUp(self):
        self.fx = tvl.Fixture(self, "locate-inventory")
        self.fx.manifest(TASK, tvl.inventory_manifest_doc(self.fx))

    def rule(self, version) -> None:
        if version is None:
            self.fx.gate.pop("span_end_rule", None)
        else:
            self.fx.gate["span_end_rule"] = version
        self.fx.write_gate()

    def old_shape(self) -> dict:
        """An inventory as the earlier passage_ids.py wrote it: placed by `until`, then without it."""
        source = self.fx.root / "sourcesets" / "wpm" / "sources" / SID
        layer = (source / "layer.txt").read_text(encoding="utf-8")
        formulas = json.loads((source / "formulas.json").read_text(encoding="utf-8"))
        paragraphs = json.loads((source / "paragraphs.json").read_text(encoding="utf-8"))
        body = tvl.inventory_body()
        for p in body["passages"]:
            for key in ("item", "head_words", "span"):
                p.pop(key)
        body["passages"][0]["until"] = self.UNTIL
        self.assertEqual(passage_ids.place(body, layer, formulas, paragraphs), [])
        # the lead runs past its paragraph to the until's end, into the theorem box
        self.assertEqual(body["passages"][0]["span"],
                         [tvl.LEAD_SPAN[0], tvl.THEOREM_SPAN[0] + len(self.UNTIL)])
        body["passages"][0].pop("until")
        return body

    def test_an_old_inventory_passes_without_the_key_and_fails_with_it(self):
        body = self.old_shape()
        self.rule(None)
        self.assertEqual(self.fx.gate_cfg().span_end_rule, 0)
        self.assertOk(self.fx.run(self.fx.document(TASK, body)))
        self.rule(1)                                 # span-end rule 1, before the flip
        self.assertEqual(self.fx.gate_cfg().span_end_rule, 1)
        self.assertRule(self.fx.run(self.fx.document(TASK, body)), 2,
                        "the end of the paragraph it starts in")
        # the shape passage_ids.py writes now, which keeps its until, passes under it
        body["passages"][0]["until"] = self.UNTIL
        self.assertOk(self.fx.run(self.fx.document(TASK, body)))

    def test_without_the_key_the_old_bound_still_holds(self):
        """An end before the quote's, or past the layer, was refused before the rule too."""
        self.rule(None)
        body = tvl.inventory_body()
        a = body["passages"][0]["span"][0]
        body["passages"][0]["span"] = [a, a + 3]
        self.assertRule(self.fx.run(self.fx.document(TASK, body)), 2,
                        "the quote starts the passage and lies inside it")
        body["passages"][0]["span"] = [a, len(tvl.LAYER) + 1]
        self.assertRule(self.fx.run(self.fx.document(TASK, body)), 2, "inside the layer")

    def test_the_checkout_gate_re_gates_an_old_inventory_as_it_was_gated(self):
        """`cf harvest --checkout-gate` of an inventory planned before the rule passes."""
        landed = self.fx.root / "landed"
        self.fx.document(TASK, self.old_shape(), directory=landed)
        config = self.fx.root / "gate" / "gate.json"
        for version, ok in ((None, True), (1, False)):
            with self.subTest(span_end_rule=version):
                self.rule(version)
                result = gate_corpus.gate_locate_corpus(landed, config)
                self.assertEqual(result["gate_code"]["from"], "checkout")
                self.assertEqual(result["ok"], ok, result["problems"])
                self.assertEqual(any("passage_ids.py ends it" in p for p in result["problems"]),
                                 not ok)

    def test_a_locate_run_reusing_an_old_inventory_gates_under_the_key(self):
        """A locate document has no span of its own: its passages' are the task manifest's.

        The planner writes the key into a locate run's gate.json too, and the frozen
        passage whose end an old inventory placed by a dropped `until` is read from
        the manifest, never end-checked, so the run gates as it did.
        """
        fx = tvl.Fixture(self, "locate")
        fx.gate["span_end_rule"] = vocab.SPAN_END_RULE
        fx.write_gate()
        manifest = tvl.locate_manifest_doc(fx)
        manifest["passages"][0]["span"] = [tvl.LEAD_SPAN[0],
                                           tvl.THEOREM_SPAN[0] + len(self.UNTIL)]
        task = "t-loc-0001"
        fx.manifest(task, manifest)
        self.assertOk(fx.run(fx.document(task, tvl.locate_body())))

    def test_a_gate_json_naming_a_newer_rule_cannot_run(self):
        # the gate knows the statement cut's span-end rule 2 before the planner writes it
        # (docs/proposals/statement-cut.md section 7: the flip)
        self.assertEqual(vl.KNOWN_SPAN_END_RULE,
                         max(vocab.SPAN_END_RULE, vocab.SPAN_END_STATEMENT_CUT))
        for value, needle in ((vl.KNOWN_SPAN_END_RULE + 1, "this gate knows"),
                              ("1", "not a version number"), (-1, "not a version number"),
                              (True, "not a version number")):
            self.rule(value)
            with self.subTest(value=value), self.assertRaises(vl.GateBroken) as caught:
                self.fx.gate_cfg()
            self.assertIn(needle, str(caught.exception))
            self.assertIn("span_end_rule" if needle != "this gate knows" else "span-end rule",
                          str(caught.exception))

    def test_the_gates_copy_is_cflibs(self):
        self.assertEqual(vl.SPAN_END_RULE, vocab.SPAN_END_RULE)


# --------------------------------------------------------------------------- #
# §2 item 2: the label
# --------------------------------------------------------------------------- #

class TestTheCommandLineIsTheLabel(tvl.GateTestCase):
    """A command line that could fake the log line's label is exit 3 and logs nothing."""

    def setUp(self):
        self.fx = tvl.Fixture(self, "locate-inventory")
        self.fx.manifest(TASK, tvl.inventory_manifest_doc(self.fx))
        self.doc = self.fx.document(TASK, tvl.inventory_body())
        self.config = self.fx.root / "gate" / "gate.json"
        self.log = self.fx.root / "gate-log" / "gate.jsonl"

    def cli(self, *argv, gate: Path = GATE) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(gate), *argv], capture_output=True,
                              text=True, env=environ())

    def lines(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def assertRefused(self, proc, needle: str):
        self.assertEqual(proc.returncode, vl.EXIT_GATE_BROKEN, proc.stdout + proc.stderr)
        self.assertIn("the command line is refused", proc.stderr)
        self.assertIn(needle, proc.stderr)
        self.assertEqual(proc.stdout.splitlines()[-1],
                         "ownership: not checked (the gate could not run)")

    def test_the_check_itself_runs(self):
        proc = self.cli("--gate-config", str(self.config), "--findings", str(self.doc))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(self.log.read_text().splitlines()[-1])["caller"],
                         vl.CALLER_FINDINGS)

    def test_findings_with_changed_files_is_refused(self):
        for argv in (("--findings", str(self.doc), "--changed-files"),
                     ("--changed-files", "--findings", str(self.doc))):
            with self.subTest(argv=argv):
                self.assertRefused(self.cli("--gate-config", str(self.config), *argv),
                                   "not allowed with argument")
        self.assertEqual(self.lines(), 0)

    def test_a_repeated_gate_config_is_refused(self):
        other = self.fx.root / "other.json"
        shutil.copy2(self.config, other)
        self.assertRefused(self.cli("--gate-config", str(self.config), "--findings",
                                    str(self.doc), "--gate-config", str(other)), "given twice")
        self.assertRefused(self.cli("--gate-config", str(self.config), "--findings",
                                    str(self.doc), f"--gate-config={other}"), "given twice")
        self.assertEqual(self.lines(), 0)

    def test_an_abbreviation_is_refused(self):
        self.assertRefused(self.cli("--gate-config", str(self.config), "--findings",
                                    str(self.doc), "--changed"), "unrecognized arguments")
        self.assertRefused(self.cli("--gate-conf", str(self.config), "--findings",
                                    str(self.doc)), "unrecognized arguments")

    def test_pre_merge_comes_with_changed_files(self):
        self.assertRefused(self.cli("--gate-config", str(self.config), "--findings",
                                    str(self.doc), "--pre-merge"), "--pre-merge")

    def test_a_gate_directory_runs_with_its_own_config_only(self):
        run = self.fx.root / "runs" / "r-1"
        tvl.stage_run_gate(run)
        gate_dir = run / "tend-root" / "gate"
        shutil.copy2(self.config, gate_dir / "gate.json")
        staged = gate_dir / "verify_locate.py"
        ok = self.cli("--gate-config", str(gate_dir / "gate.json"), "--findings",
                      str(self.doc), gate=staged)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.cli("--findings", str(self.doc), gate=staged).returncode, 0)
        spelled = gate_dir / ".." / "gate" / "gate.json"
        self.assertEqual(self.cli("--gate-config", str(spelled), "--findings", str(self.doc),
                                  gate=staged).returncode, 0)
        # the same bytes elsewhere are another config, and so is gate.local.json
        self.assertRefused(self.cli("--gate-config", str(self.config), "--findings",
                                    str(self.doc), gate=staged), "the config beside this gate")
        # the checkout's scripts/gate/ holds no gate.json: a hand check takes any config
        self.assertFalse((SCRIPTS / "gate" / "gate.json").exists())


class TestTheCallerField(tvl.OwnershipCase):
    """Each log line says which call it was, and the readers of 27656b3 still agree."""

    def test_the_four_callers_and_the_readers(self):
        self.contribute()
        self.assertEqual(self.cli("--task-id", TASK).returncode, 0)
        self.assertEqual(self.cli("--pre-merge").returncode, 0)
        doc = self.wt / "findings" / TASK / f"{SID}.json"
        proc = subprocess.run([sys.executable, str(GATE), "--gate-config",
                               str(self.fx.root / "gate" / "gate.json"), "--findings", str(doc)],
                              capture_output=True, text=True, env=environ())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = vl.run_gate(self.fx.gate_cfg(), root=self.wt, findings_dir="findings")
        self.assertEqual(result["caller"], vl.CALLER_DIRECTORY)
        log = self.fx.root / "gate-log" / "gate.jsonl"
        lines = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([line["caller"] for line in lines],
                         [vl.CALLER_VALIDATION, vl.CALLER_PRE_MERGE, vl.CALLER_FINDINGS,
                          vl.CALLER_DIRECTORY])
        self.assertEqual([(line["changed_files"], line["pre_merge"]) for line in lines],
                         [(True, False), (True, True), (False, False), (False, False)])
        self.assertEqual(set(vl.CALLERS), {line["caller"] for line in lines})
        counts = watchdog.gate_counts(log)
        self.assertEqual((counts["calls"], counts["pre_merge_calls"]), (1, 1))
        rejections = metrics.gate_rejections(lines)
        self.assertEqual((rejections["attempts"], rejections["pre_merge"]["calls"]), (1, 1))
        # the one call the readers count as an attempt is the one labelled validation
        self.assertEqual([l["caller"] for l in lines if l["changed_files"] and not l["pre_merge"]],
                         [vl.CALLER_VALIDATION])

    def test_the_callers_are_cflibs(self):
        from cflib import vocab

        self.assertEqual(vl.CALLERS, vocab.GATE_CALLERS)

    def test_caller_of(self):
        self.assertEqual(vl.caller_of(changed_files=True, pre_merge=False, findings=False),
                         "validation")
        self.assertEqual(vl.caller_of(changed_files=True, pre_merge=True, findings=False),
                         "pre_merge")
        self.assertEqual(vl.caller_of(changed_files=False, pre_merge=False, findings=True),
                         "findings_check")
        self.assertEqual(vl.caller_of(changed_files=False, pre_merge=False, findings=False),
                         "directory")


def permits(tools: list[str], command: str) -> bool:
    """Does an ``--allowedTools`` list admit ``command``?  ``Bash(<prefix>:*)`` is a prefix."""
    for tool in tools:
        if tool.startswith("Bash(") and tool.endswith(":*)"):
            prefix = tool[len("Bash("):-len(":*)")]
            if command == prefix or command.startswith(prefix + " "):
                return True
    return False


class TestTheGateEntryDependsOnTheGate(tbh.BridgeCase):
    """§2 item 2 (b): the bridge's GATE_TOOL is the gate's own, by its script."""

    SUCCESS = tbh.SUCCESS

    def tools(self, role: str, gate_cmd: str) -> list[str]:
        self.agentbox(f"printf '%s' '{json.dumps(self.SUCCESS)}'\n")
        proc = self.bridge(role, CF_GATE_CMD=gate_cmd)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = (self.tmp / "argv").read_text().splitlines()
        return argv[argv.index("--allowedTools") + 1].split(",")

    def test_a_faithfulness_worker_runs_the_bare_gate(self):
        gate = "python3 /srv/cf/r-gate/verify_findings.py --gate-config /srv/cf/r-gate/gate.json"
        tools = self.tools("worker", gate)
        self.assertIn("Bash(python3 /srv/cf/r-gate/verify_findings.py:*)", tools)
        # worker.md.tmpl renders the bare {{gate_command}}
        self.assertTrue(permits(tools, gate))
        self.assertTrue(permits(self.tools("reviewer", gate), gate))

    def test_a_locate_worker_runs_findings_and_not_changed_files(self):
        gate = "python3 /srv/cf/r-gate/verify_locate.py --gate-config /srv/cf/r-gate/gate.json"
        for role in ("worker", "reviewer"):
            with self.subTest(role=role):
                tools = self.tools(role, gate)
                self.assertIn(f"Bash({gate} --findings:*)", tools)
                self.assertNotIn("Bash(python3 /srv/cf/r-gate/verify_locate.py:*)", tools)
                self.assertTrue(permits(tools, f"{gate} --findings findings/t-1/s.json"))
                self.assertFalse(permits(tools, f"{gate} --changed-files --base main"))
                self.assertFalse(permits(tools, "python3 /srv/cf/r-gate/verify_locate.py "
                                                "--changed-files"))
                self.assertFalse(permits(tools, f"{gate} --root . --changed-files"))

    def test_what_the_prefix_still_admits_the_gate_refuses(self):
        """The prefix admits anything appended to --findings; the gate refuses each fake."""
        fx = tvl.Fixture(self, "locate-inventory")
        fx.manifest(TASK, tvl.inventory_manifest_doc(fx))
        doc = fx.document(TASK, tvl.inventory_body())
        run = fx.root / "runs" / "r-1"
        tvl.stage_run_gate(run)
        gate_dir = run / "tend-root" / "gate"
        shutil.copy2(fx.root / "gate" / "gate.json", gate_dir / "gate.json")
        gate = [sys.executable, "-I", str(gate_dir / "verify_locate.py"), "--gate-config",
                str(gate_dir / "gate.json"), "--findings", str(doc)]
        other = fx.root / "gate" / "gate.json"
        for tail in ((), ("--changed-files",), ("--changed",), ("--gate-config", str(other)),
                     ("--pre-merge",)):
            with self.subTest(tail=tail):
                proc = subprocess.run([*gate, *tail], capture_output=True, text=True,
                                      env=environ())
                self.assertEqual(proc.returncode, 0 if not tail else 3,
                                 proc.stdout + proc.stderr)
        lines = (fx.root / "gate-log" / "gate.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(l)["caller"] for l in lines], [vl.CALLER_FINDINGS])

    def test_the_planned_reviewer_list_is_the_same_entry(self):
        env = ltr.wrapper_env(_preset("locate"), "/srv/cf/r-gate", "/srv/cf/u1.sqlite")
        tools = env["reviewer"]["CF_REVIEWER_TOOLS"].split(",")
        gate = "python3 /srv/cf/r-gate/verify_locate.py --gate-config /srv/cf/r-gate/gate.json"
        self.assertIn(f"Bash({gate} --findings:*)", tools)
        self.assertFalse(permits(tools, f"{gate} --changed-files"))


def _preset(mode: str):
    from locate import presets as lp

    return next(lp.get(name) for name in sorted(lp.LOCATE_PRESETS) if lp.get(name).mode == mode)


# --------------------------------------------------------------------------- #
# §2 item 6: the views
# --------------------------------------------------------------------------- #

class TestTheViews(tvl.GateTestCase):
    """A source pin's views hold formulas.json and paragraphs.json as layer_sha256 holds the layer."""

    def setUp(self):
        self.fx = tvl.Fixture(self, "locate-inventory")
        self.fx.manifest(TASK, tvl.inventory_manifest_doc(self.fx))
        self.source = self.fx.root / "sourcesets" / "wpm" / "sources" / SID
        self.fx.gate["sources"][SID]["views"] = {
            name: _sha((self.source / name).read_bytes()) for name in vl.PINNED_VIEWS}
        self.fx.write_gate()

    def run_doc(self):
        return self.fx.run(self.fx.document(TASK, tvl.inventory_body()))

    def rewrite(self, name: str) -> None:
        """The same table in other bytes: what a re-render or an edit on the host leaves."""
        path = self.source / name
        path.write_text(json.dumps(json.loads(path.read_text()), indent=2), encoding="utf-8")

    def test_the_pinned_views_pass(self):
        self.assertOk(self.run_doc())
        self.assertEqual(vl.PINNED_VIEWS, lplan.GATE_VIEWS)

    def test_a_view_that_is_not_the_pin_breaks_the_gate(self):
        for name in vl.PINNED_VIEWS:
            with self.subTest(name=name):
                saved = (self.source / name).read_bytes()
                self.rewrite(name)
                self.assertBroken(self.run_doc(), f"{name} on this host is")
                (self.source / name).write_bytes(saved)
        self.assertOk(self.run_doc())

    def test_a_malformed_pin_breaks_the_gate(self):
        for views in ("abc", {"formulas.json": "0" * 64}, {"formulas.json": 5,
                                                          "paragraphs.json": "0" * 64}):
            with self.subTest(views=views):
                self.fx.gate["sources"][SID]["views"] = views
                self.fx.write_gate()
                self.assertBroken(self.run_doc(), "views is not a sha256 pin")

    def test_a_gate_json_without_views_reads_the_tables_as_found(self):
        del self.fx.gate["sources"][SID]["views"]
        self.fx.write_gate()
        self.rewrite("paragraphs.json")
        self.rewrite("formulas.json")
        self.assertOk(self.run_doc())

    def test_the_verify_stage_holds_the_views_too(self):
        config = self.fx.root / "gate" / "gate.json"
        command = remote.layer_check(str(config), hashing.sha256_file(config))
        done = subprocess.run(["sh", "-c", command], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.rewrite("paragraphs.json")
        done = subprocess.run(["sh", "-c", command], capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(f"{SID}/paragraphs.json: FAILED", done.stdout)
        self.assertIn("not its pinned view", done.stderr)


# --------------------------------------------------------------------------- #
# the two gate holes
# --------------------------------------------------------------------------- #

def add_phantom(db: Path, name: str = "Widget.phantom_lemma") -> None:
    """Doctor corpus.sqlite: one more declaration, at the universe's alpha commit."""
    con = sqlite3.connect(db)
    con.execute("INSERT INTO decls (library, commit_sha, name, proof_status) VALUES (?,?,?,?)",
                ("alpha", tvl.COMMIT_A, name, "proved"))
    con.commit()
    con.close()


class TestTheCorpusPin(tvl.GateTestCase):
    """Rule 3 asks the database gate.json pins, and no other."""

    TASK = "t-loc-0001"

    def setUp(self):
        self.fx = tvl.Fixture(self, "locate")
        self.fx.manifest(self.TASK, tvl.locate_manifest_doc(self.fx))

    def phantom_body(self) -> dict:
        body = tvl.locate_body()
        body["passages"][0]["locations"] = [tvl.location(decl="Widget.phantom_lemma")]
        body["passages"][0]["receipt"]["candidates"] = [
            {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
             "verdict": "rejected", "reason": "a synthetic rejection"}]
        return body

    def test_a_doctored_database_no_longer_passes_a_phantom_declaration(self):
        doc = self.fx.document(self.TASK, self.phantom_body())
        self.assertRule(self.fx.run(doc), 3, "phantom")          # the honest database
        add_phantom(self.fx.db)
        self.assertBroken(self.fx.run(doc), "not the database this run was planned with")
        # what the pin guards: re-pinned (a re-plan), the doctored row would pass rule 3
        self.fx.repin_corpus()
        self.assertOk(self.fx.run(doc))

    def test_no_pin_is_no_rule_3(self):
        self.fx.gate["corpus_db"]["sha256"] = ""
        self.fx.write_gate()
        self.assertBroken(self.fx.run(self.fx.document(self.TASK, tvl.locate_body())),
                          "pins no corpus_db.sha256")

    def test_the_self_check_compares_it(self):
        config = self.fx.root / "gate" / "gate.json"
        proc = subprocess.run([sys.executable, str(GATE), "--gate-config", str(config),
                               "--self-check"], capture_output=True, text=True, env=environ())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("corpus.sqlite the pinned bytes", proc.stdout)
        add_phantom(self.fx.db)
        proc = subprocess.run([sys.executable, str(GATE), "--gate-config", str(config),
                               "--self-check"], capture_output=True, text=True, env=environ())
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertIn("not the database this run was planned with", proc.stderr)

    def test_an_inventory_call_never_reads_it(self):
        fx = tvl.Fixture(self, "locate-inventory")
        fx.manifest(TASK, tvl.inventory_manifest_doc(fx))
        add_phantom(fx.db)
        self.assertOk(fx.run(fx.document(TASK, tvl.inventory_body())))


class TestTheCandidatesPin(tvl.GateTestCase):
    """Rule 4 reads the candidates file gate.json pins, and no other."""

    TASK = "t-loc-0001"

    def setUp(self):
        self.fx = tvl.Fixture(self, "locate")
        self.fx.manifest(self.TASK, tvl.locate_manifest_doc(self.fx))
        self.path = self.fx.root / "candidates" / f"{SID}.json"
        self.fx.gate["candidates_sha256"] = {SID: _sha(self.path.read_bytes())}
        self.fx.write_gate()

    def run_doc(self, body=None):
        return self.fx.run(self.fx.document(self.TASK, body or tvl.locate_body()))

    def test_the_pinned_file_passes(self):
        self.assertOk(self.run_doc())

    def test_a_doctored_file_breaks_the_gate(self):
        """Emptying the curated hits would turn rule 4 off for the article."""
        self.fx.candidates["curated"] = []
        self.fx.write_candidates()
        self.assertBroken(self.run_doc(), "not the file this run was planned with")

    def test_a_source_the_pin_leaves_out_breaks_the_gate(self):
        self.fx.gate["candidates_sha256"] = {}
        self.fx.write_gate()
        self.assertBroken(self.run_doc(), "pins the candidates files and none for this source")

    def test_a_malformed_pin_breaks_the_gate(self):
        self.fx.gate["candidates_sha256"] = {SID: "not a sha"}
        with self.assertRaisesRegex(vl.GateBroken, "candidates_sha256 is not a map"):
            vl.GateConfig.load(self.fx.write_gate())

    def test_a_gate_json_without_the_key_reads_the_file_as_found(self):
        del self.fx.gate["candidates_sha256"]
        self.fx.write_gate()
        self.fx.candidates["curated"] = []
        self.fx.write_candidates()
        self.assertOk(self.run_doc())

    def test_the_blind_arm_reads_no_candidates(self):
        self.fx.gate["blind"] = True
        self.fx.write_gate()
        self.fx.candidates["curated"] = []
        self.fx.write_candidates()
        self.assertOk(self.run_doc())

    def test_the_self_check_compares_them(self):
        config = self.fx.root / "gate" / "gate.json"
        argv = [sys.executable, str(GATE), "--gate-config", str(config), "--self-check"]
        proc = subprocess.run(argv, capture_output=True, text=True, env=environ())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("1 candidates file(s) the pinned bytes", proc.stdout)
        self.fx.candidates["curated"] = []
        self.fx.write_candidates()
        proc = subprocess.run(argv, capture_output=True, text=True, env=environ())
        self.assertEqual(proc.returncode, 3, proc.stdout)


# --------------------------------------------------------------------------- #
# the planner writes the pins
# --------------------------------------------------------------------------- #

class TestThePlannerPins(unittest.TestCase):
    """gate.json carries each source's views and, where the gate reads them, the candidates."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve() / "ws"
        self.addCleanup(shutil.rmtree, self.root.parent, ignore_errors=True)
        self.paths = F.campaign(self, self.root)

    def gates(self, run: str):
        rd = self.paths.run_dir(run)
        return read_json(rd / "gate.json"), read_json(rd / "gate.local.json")

    def views_of(self, sid: str) -> dict:
        record = read_json(self.paths.sourceset_source_dir(F.SS, sid) / "render.json")
        return {name: record["views"][name] for name in lplan.GATE_VIEWS}

    def candidates_of(self, sid: str) -> str:
        return hashing.sha256_file(self.paths.candidates_dir(F.SS, F.U) / f"{sid}.json")

    def test_an_inventory_plan(self):
        for gate in self.gates("inv-1"):
            self.assertEqual(gate["span_end_rule"], vocab.SPAN_END_RULE)
            self.assertEqual(sorted(gate["candidates_sha256"]), sorted(gate["sources"]))
            for sid, pin in gate["sources"].items():
                self.assertEqual(pin["views"], self.views_of(sid))
                self.assertEqual(gate["candidates_sha256"][sid], self.candidates_of(sid))

    def test_a_locate_plan_and_a_challenge_plan(self):
        F.plan_locate(self, self.root)
        for gate in self.gates("loc-1"):
            self.assertEqual(gate["span_end_rule"], vocab.SPAN_END_RULE)
            self.assertTrue(gate["candidates_sha256"])
            for sid in gate["candidates_sha256"]:
                self.assertEqual(gate["candidates_sha256"][sid], self.candidates_of(sid))
            for sid, pin in gate["sources"].items():
                self.assertEqual(pin["views"], self.views_of(sid))
        F.write_harvest(self.paths, "loc-1", F.locate_documents(self.paths, "loc-1"))
        code, out, err = F.cf("--workspace", str(self.root), "plan-locate", "--preset",
                              "locate-challenge", "--sourceset", F.SS, "--universe", F.U,
                              "--challenge-of", "loc-1", "--run-id", "chl-1")
        self.assertEqual(code, 0, out + err)
        for gate in self.gates("chl-1"):
            self.assertEqual(gate["span_end_rule"], vocab.SPAN_END_RULE)
            self.assertNotIn("candidates_sha256", gate)            # its gate reads none
            for sid, pin in gate["sources"].items():
                self.assertEqual(pin["views"], self.views_of(sid))

    def test_a_view_its_render_json_does_not_record_is_refused_at_plan(self):
        path = self.paths.sourceset_source_dir(F.SS, F.W1) / "paragraphs.json"
        path.write_text(json.dumps(json.loads(path.read_text()), indent=3), encoding="utf-8")
        with self.assertRaises(Exception) as caught:
            lplan.source_views(self.paths, lplan.inputs.manifest(self.paths, F.SS), [F.W1])
        self.assertIn("paragraphs.json", str(caught.exception))
        self.assertIn("render.json records", str(caught.exception))

    def test_a_real_plan_passes_the_harvests_checks_of_its_own_records(self):
        """The run's gate copy, the staged gate.json, gate.local.json and planned.json agree."""
        F.plan_locate(self, self.root)
        for run in ("inv-1", "loc-1"):
            rd = self.paths.run_dir(run)
            doc = read_yaml(rd / "run.yaml")
            pins = doc["locate"]["gate_files"]
            gate_dir = Path(self.paths.tend_root(run)) / "gate"
            self.assertIn("gate/gate.json", pins)
            self.assertTrue((rd / "planned.json").is_file())
            self.assertEqual(gate_corpus.run_gate_code(gate_dir, pins)[1], [], run)
            self.assertEqual(gate_corpus.run_config_problems(
                rd / "gate.local.json", run_doc=doc, planned=rd / "planned.json",
                gate_dir=gate_dir, gate_files=pins), [], run)

    def test_the_layout_fields_are_every_path_a_layout_gives_gate_json(self):
        """What the cross-check lets differ is exactly what ``gate_config`` takes from a layout."""

        class Layout(dict):
            def __init__(self, tag: str):
                super().__init__()
                self.tag = tag

            def __missing__(self, key):
                return f"/{self.tag}/{key}"

        manifest = lplan.inputs.manifest(self.paths, F.SS)
        universe = lplan.inputs.load_universe(self.paths, F.U)
        a, b = (lplan.gate_config(
            preset_mode="locate", preset_name="locate-default", run_id="r-x", layout=Layout(tag),
            universe=universe, corpus_db_sha256="0" * 64,
            tools_db={"path": "/t", "sha256": "1" * 64, "kind": "full"}, manifest=manifest,
            source_ids=[F.W1], blind=False, inventory_run=None, challenged_runs=[])
            for tag in ("one", "two"))
        self.assertEqual(set(gate_corpus._differing(a, b)),
                         {".".join(field) for field in gate_corpus.LAYOUT_FIELDS})
        self.assertEqual(gate_corpus._differing(gate_corpus._without_layout(a),
                                                gate_corpus._without_layout(b)), [])


# --------------------------------------------------------------------------- #
# the harvest re-gates with the run's own gate
# --------------------------------------------------------------------------- #

class TestTheHarvestUsesTheRunsOwnGate(unittest.TestCase):
    """`cf harvest` judges a locate corpus by the gate its plan staged, and says which."""

    def setUp(self):
        self.fx = tvl.Fixture(self, "locate")
        self.fx.manifest("t-loc-0001", tvl.locate_manifest_doc(self.fx))
        self.landed = self.fx.root / "landed"
        self.fx.document("t-loc-0001", tvl.locate_body(), directory=self.landed)
        self.ws = self.fx.root / "ws"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cf_main(["init", str(self.ws), "--no-git", "-q"])
        self.run_dir = self.ws / "runs" / "r-1"
        self.run_dir.mkdir(parents=True)
        self.pins = tvl.stage_planned_run(self.run_dir, self.fx)
        write_yaml(self.run_dir / "run.yaml", {"version": 1, "run_id": "r-1", "mode": "locate",
                                               "locate": {"mode": "locate",
                                                          "gate_files": self.pins}})
        self.gate_dir = self.run_dir / "tend-root" / "gate"

    def harvest(self, *extra: str) -> tuple[int, dict, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cf_main(["--workspace", str(self.ws), "harvest", "r-1", "--from",
                            str(self.landed), *extra])
        receipt = json.loads((self.ws / "corpus" / "r-1" / "RECEIPT.json").read_text())
        return code, receipt, out.getvalue()

    def repin(self, rel: str) -> None:
        doc = read_yaml(self.run_dir / "run.yaml")
        doc["locate"]["gate_files"][rel] = hashing.sha256_file(self.run_dir / "tend-root" / rel)
        write_yaml(self.run_dir / "run.yaml", doc)

    def test_the_receipt_names_the_runs_own_gate(self):
        code, receipt, out = self.harvest()
        self.assertEqual(code, 0, out)
        gate = receipt["gate"]
        self.assertTrue(gate["ok"], gate["problems"])
        block = gate["gate_code"]
        self.assertEqual((block["from"], block["dir"]), ("run", str(self.gate_dir)))
        self.assertEqual(block["sha256"], self.pins["gate/verify_locate.py"])
        self.assertEqual(block["sha256"], hashing.sha256_file(self.gate_dir / "verify_locate.py"))
        checked = {r: s for r, s in self.pins.items()
                   if r.startswith("gate/") and r != "gate/gate.json"}
        self.assertEqual(block["files"], len(checked))
        lines = "".join(f"{r}\t{s}\n" for r, s in sorted(checked.items()))
        self.assertEqual(block["files_sha256"], _sha(lines.encode("utf-8")))
        self.assertIn("gate code: the run's own copy", out)

    def test_a_run_gate_edited_since_the_plan_judges_nothing(self):
        path = self.gate_dir / "locate_text.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertFalse(receipt["gate"]["ok"])
        self.assertIn("gate/locate_text.py is not the bytes its plan staged",
                      " ".join(receipt["gate"]["problems"]))
        # --checkout-gate is the explicit way round it, and the receipt says so
        code, receipt, out = self.harvest("--checkout-gate")
        self.assertEqual(code, 0, out)
        self.assertEqual(receipt["gate"]["gate_code"]["from"], "checkout")
        self.assertEqual(receipt["gate"]["gate_code"]["sha256"], hashing.sha256_file(GATE))

    def test_a_module_the_plan_never_staged_judges_nothing(self):
        (self.gate_dir / "json.py").write_text("raise SystemExit('planted')\n", encoding="utf-8")
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertIn("holds gate/json.py, which its plan did not stage",
                      " ".join(receipt["gate"]["problems"]))

    def test_the_import_leaves_no_bytecode_in_the_run(self):
        prefix = sys.pycache_prefix
        code, receipt, out = self.harvest()
        self.assertEqual(code, 0, out)
        self.assertEqual(list(self.gate_dir.rglob("__pycache__")), [])
        self.assertEqual(sys.pycache_prefix, prefix)

    def test_the_copy_that_runs_is_the_runs(self):
        """A run whose gate differs from the checkout's is judged by its own."""
        path = self.gate_dir / "verify_locate.py"
        path.write_text(path.read_text(encoding="utf-8").replace(
            'GATE_SCHEMA = "cf-gate-locate/1"', 'GATE_SCHEMA = "cf-gate-locate/0"'),
            encoding="utf-8")
        self.repin("gate/verify_locate.py")
        names = gate_corpus.gate_module_names(self.gate_dir)
        before = {n: m for n, m in sys.modules.items() if gate_corpus._is_gate_module(n, names)}
        path_before = list(sys.path)
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertIn("not 'cf-gate-locate/0'", " ".join(receipt["gate"]["problems"]))
        self.assertEqual(receipt["gate"]["gate_code"]["sha256"], hashing.sha256_file(path))
        # the checkout's modules and path are back as they were
        after = {n: m for n, m in sys.modules.items() if gate_corpus._is_gate_module(n, names)}
        self.assertEqual(after, before)
        self.assertEqual(sys.path, path_before)
        self.assertEqual(vl.GATE_SCHEMA, "cf-gate-locate/1")

    def test_a_run_with_no_gate_files_is_refused_by_name(self):
        write_yaml(self.run_dir / "run.yaml", {"version": 1, "run_id": "r-1", "mode": "locate",
                                               "locate": {"mode": "locate"}})
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertIn("records no locate.gate_files", " ".join(receipt["gate"]["problems"]))
        # the refusal names the flag that settles it, and blames no finding
        self.assertIn("--checkout-gate", receipt["gate"]["hint"])
        self.assertIn("hint: `cf harvest <run-id> --checkout-gate`", out)
        self.assertIn("could not judge the findings", out)
        self.assertIn("`cf harvest r-1 --checkout-gate` re-gates", out)
        self.assertNotIn("do not validate", out)
        code, receipt, out = self.harvest("--checkout-gate")
        self.assertEqual(code, 0, out)

    def test_a_doctored_corpus_fails_the_harvest(self):
        """The contributor's own gate: a phantom declaration in its database no longer passes."""
        body = tvl.locate_body()
        body["passages"][0]["locations"] = [tvl.location(decl="Widget.phantom_lemma")]
        body["passages"][0]["receipt"]["candidates"] = [
            {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
             "verdict": "rejected", "reason": "a synthetic rejection"}]
        self.fx.document("t-loc-0001", body, directory=self.landed)
        add_phantom(self.fx.db)
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertIn("not the database this run was planned with",
                      " ".join(receipt["gate"]["broken"]))


    # -- review fixes: bytecode, the plan's records, the hint, the module names --
    def phantom_document(self) -> None:
        """A document locating a declaration only a doctored database holds."""
        body = tvl.locate_body()
        body["passages"][0]["locations"] = [tvl.location(decl="Widget.phantom_lemma")]
        body["passages"][0]["receipt"]["candidates"] = [
            {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
             "verdict": "rejected", "reason": "a synthetic rejection"}]
        self.fx.document("t-loc-0001", body, directory=self.landed)

    def edit_json(self, path: Path, change) -> None:
        doc = json.loads(path.read_text(encoding="utf-8"))
        change(doc)
        path.write_text(json.dumps(doc), encoding="utf-8")

    def test_a_planted_bytecode_never_runs_in_place_of_its_source(self):
        """A .pyc in the run's __pycache__/ whose header claims the pinned source's mtime and size."""
        self.phantom_document()
        src = self.gate_dir / "verify_locate.py"
        text = src.read_text(encoding="utf-8")
        needle = "            self._exists[key] = row is not None"
        self.assertIn(needle, text)
        doctored = self.fx.root / "doctored" / "verify_locate.py"
        doctored.parent.mkdir()
        doctored.write_text(text.replace(needle, "            self._exists[key] = True"),
                            encoding="utf-8")
        cache = self.gate_dir / "__pycache__" / f"verify_locate.{sys.implementation.cache_tag}.pyc"
        cache.parent.mkdir()
        py_compile.compile(str(doctored), cfile=str(cache), dfile=str(src), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        stat = src.stat()
        data = bytearray(cache.read_bytes())
        data[8:12] = struct.pack("<I", int(stat.st_mtime) & 0xFFFFFFFF)
        data[12:16] = struct.pack("<I", stat.st_size & 0xFFFFFFFF)
        cache.write_bytes(bytes(data))
        # the plant is a good one: a plain import of the source would run the doctored code
        prefix, sys.pycache_prefix = sys.pycache_prefix, None
        try:
            loaded = importlib.machinery.SourceFileLoader("verify_locate", str(src)).get_code(
                "verify_locate")
        finally:
            sys.pycache_prefix = prefix
        self.assertNotEqual(loaded, compile(text, str(src), "exec"))
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertIn("a phantom declaration", " ".join(receipt["gate"]["problems"]))
        self.assertEqual(receipt["gate"]["gate_code"]["sha256"], self.pins["gate/verify_locate.py"])

    def test_a_local_config_repinned_to_a_doctored_database_is_refused(self):
        """gate.local.json is the staged gate.json but for its paths: its pins are the plan's."""
        self.phantom_document()
        add_phantom(self.fx.db)
        self.edit_json(self.run_dir / "gate.local.json",
                       lambda d: d["corpus_db"].update(sha256=_sha(self.fx.db.read_bytes())))
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertFalse(receipt["gate"]["ok"])
        self.assertIn("gate.local.json is not the gate/gate.json its plan staged but for the "
                      "layout's paths: corpus_db.sha256 differ", " ".join(receipt["gate"]["broken"]))
        self.assertIn("re-plan the run", receipt["gate"]["hint"])
        self.assertNotIn("--checkout-gate", receipt["gate"]["hint"])
        self.assertIn("could not judge the findings", out)

    def test_run_yaml_holds_the_corpus_pin_with_the_checkout_gate_too(self):
        doc = read_yaml(self.run_dir / "run.yaml")
        doc["locate"]["universe"] = {"corpus_db_sha256": self.fx.gate["corpus_db"]["sha256"]}
        write_yaml(self.run_dir / "run.yaml", doc)
        code, receipt, out = self.harvest("--checkout-gate")
        self.assertEqual(code, 0, out)
        self.phantom_document()
        add_phantom(self.fx.db)
        self.edit_json(self.run_dir / "gate.local.json",
                       lambda d: d["corpus_db"].update(sha256=_sha(self.fx.db.read_bytes())))
        code, receipt, out = self.harvest("--checkout-gate")
        self.assertNotEqual(code, 0)
        self.assertEqual(receipt["gate"]["gate_code"]["from"], "checkout")
        self.assertIn("run.yaml locate.universe.corpus_db_sha256",
                      " ".join(receipt["gate"]["broken"]))

    def test_a_task_manifest_edited_since_the_plan_is_refused(self):
        """An emptied `curated` list would hand the task no curated hit, and turn rule 4 off."""
        body = tvl.locate_body()
        body["passages"][0]["locations"] = [tvl.location(decl="Widget.Spanning")]
        body["passages"][0]["receipt"]["candidates"] = []
        self.fx.document("t-loc-0001", body, directory=self.landed)
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertIn("[rule 4]", " ".join(receipt["gate"]["problems"]))
        self.edit_json(self.fx.tasks / "t-loc-0001.json",
                       lambda d: d["documents"][0].update(curated=[]))
        for extra in ((), ("--checkout-gate",)):
            code, receipt, out = self.harvest(*extra)
            self.assertNotEqual(code, 0, extra)
            self.assertIn("t-loc-0001 in", " ".join(receipt["gate"]["broken"]))
            self.assertIn("are not the ones planned.json records", " ".join(receipt["gate"]["broken"]))

    def test_a_task_manifest_the_plan_never_wrote_is_refused(self):
        self.fx.manifest("t-loc-9999", tvl.locate_manifest_doc(self.fx))
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertIn("holds task manifest(s) t-loc-9999 that planned.json does not record",
                      " ".join(receipt["gate"]["broken"]))

    def test_a_run_without_planned_json_is_refused_by_its_own_gate(self):
        (self.run_dir / "planned.json").unlink()
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertIn("planned.json is missing", " ".join(receipt["gate"]["broken"]))
        # the checkout's gate reads the manifests as found, and the receipt says whose gate ran
        code, receipt, out = self.harvest("--checkout-gate")
        self.assertEqual(code, 0, out)
        self.assertEqual(receipt["gate"]["gate_code"]["from"], "checkout")

    def test_the_staged_gate_json_is_held_to_its_pin(self):
        staged = self.gate_dir / "gate.json"
        staged.write_text(staged.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertIn("the run's gate/gate.json is not the bytes its plan staged",
                      " ".join(receipt["gate"]["broken"]))
        doc = read_yaml(self.run_dir / "run.yaml")
        del doc["locate"]["gate_files"]["gate/gate.json"]
        write_yaml(self.run_dir / "run.yaml", doc)
        code, receipt, out = self.harvest()
        self.assertNotEqual(code, 0)
        self.assertIn("pins no gate/gate.json", " ".join(receipt["gate"]["broken"]))

    def test_the_layout_paths_may_differ(self):
        """gate.local.json names this machine's paths: those are the only fields it may change."""
        tasks = self.fx.root / "local-tasks"
        shutil.copytree(self.fx.tasks, tasks)

        def local(d):
            d.update(log_dir=str(self.fx.root / "elsewhere-log"), tasks_dir=str(tasks),
                     orchestrator_db=str(self.fx.root / "elsewhere.sqlite"))
        self.edit_json(self.run_dir / "gate.local.json", local)
        code, receipt, out = self.harvest()
        self.assertEqual(code, 0, out)
        self.assertTrue(receipt["gate"]["ok"], receipt["gate"]["problems"])

    def test_the_gate_modules_are_read_off_the_directory(self):
        modules, packages = gate_corpus.gate_module_names(self.gate_dir)
        self.assertEqual(modules, {Path(name).stem for _rel, name in ltr.GATE_FILES})
        self.assertLessEqual({"verify_locate", *(Path(m).stem for m in vl.GATE_MODULES)}, modules)
        self.assertEqual(packages, set(ltr.VENDORED))
        self.assertEqual(packages, set(vl.VENDORED_PACKAGES))

    def test_a_sibling_the_checkout_holds_is_the_runs_while_the_gate_runs(self):
        """No list to keep in step: any staged sibling is set aside, not only the ones named."""
        stale = types.ModuleType("passage_ids")
        saved = sys.modules.get("passage_ids", None)
        sys.modules["passage_ids"] = stale
        try:
            with gate_corpus._isolated_gate(self.gate_dir):
                import passage_ids as seen  # noqa: PLC0415 - the run's copy, by name
                self.assertEqual(Path(seen.__file__).resolve(),
                                 (self.gate_dir / "passage_ids.py").resolve())
            self.assertIs(sys.modules["passage_ids"], stale)
        finally:
            if saved is None:
                sys.modules.pop("passage_ids", None)
            else:
                sys.modules["passage_ids"] = saved


if __name__ == "__main__":
    unittest.main()
