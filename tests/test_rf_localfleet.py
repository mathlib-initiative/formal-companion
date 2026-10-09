"""The local fleet, the four Workflow scripts, and `cf run`'s refusals.

These are the joins the rest of the suite could not see, because it pinned the
*inputs* of the Workflow scripts and never executed one:

* the LABEL/ID CONTRACT between ``workflows/*.js`` and
  ``harvest/harvest_journal.py``.  The old test pinned a label the shipped script
  never emitted (``chal0:Exercise_1.2.4`` against
  ``chal0:Exercise 1.2.4#0``), so every refuter verdict was dropped on the
  recovery path and ``summarise`` reported a row of zeros while the suite stayed
  green;
* a recovered local corpus against the very gate the tool prints as the next
  step -- three layers each contributed a rejection and every file failed;
* the script BODIES, run in a Node harness with a stubbed
  ``agent``/``pipeline``/``parallel``/``log``.  Nothing executed one before, so a
  challenge verdict stapled onto a different finding was invisible;
* ``cf run``'s consent refusal, smoke gate, missing-component refusal and
  ``remote.execute``'s real subprocess branch -- the command that spends money
  and copies source text off the machine had none of them under test.

Everything here is hermetic: no network, no ssh, no companion repository.  The
Node cases skip cleanly when ``node`` is not installed.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                              # noqa: E402
from cflib.errors import CfError                                  # noqa: E402
from harvest import harvest_journal                               # noqa: E402
from ledger import workflow_args                                  # noqa: E402
from plan import presets as presets_mod                           # noqa: E402
from run import fleet_local                                       # noqa: E402

NODE = shutil.which("node")
WORKFLOWS = ("adjudicate", "challenge", "verify-claims", "fleet-local")

#: the published Tao edition, by the id registry/sources.yaml gives it
PUBLISHED_ID = "tao-measure-gsm126-published"
PUBLISHED_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"

#: one book sentence, so a quote gate has something real to match against
BOOK_TEXT = ("Exercise 1.2.4. Let d >= 1 and let E be an elementary subset of R^d. "
             "Show that the elementary measure is finitely additive.\n")
BOOK_QUOTE = "Show that the elementary measure is finitely additive."

#: an item whose NAME contains a colon.  The baseline corpus holds exactly one,
#: and a label parser that takes the last colon-separated field truncates it to
#: the tail of its parenthetical -- which is the same broken join, for a shape
#: that really occurs.
COLON_ITEM = ("In-text convention, book p. 29 (Exercise 1.2.5: extension of "
              "widget weight to unbounded assemblies)")
#: an item whose name contains an underscore, which the legacy spelling of a
#: label (``chal0:Exercise_1.2.4``) turns into a space
UNDERSCORE_ITEM = "In-text convention, book p. 71 (outer_measure normalisation)"


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(dir=os.environ.get("CF_TEST_TMP") or None)).resolve()
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def js(name: str) -> str:
    return (SKILL_ROOT / "workflows" / f"{name}.workflow.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# a workspace with one companion, one source and one planned local run


def make_workspace(case: unittest.TestCase, *, errata_repo_supplied: bool = False) -> dict:
    """A workspace, a prepared source and ``runs/demo`` with three tasks."""
    from cflib.workspace import Workspace

    root = scratch(case)
    repo = root / "repos" / "ours"
    repo.mkdir(parents=True)
    (root / "workspace.yaml").write_text(
        "version: 2\n"
        "workspace:\n  name: tao\n"
        "companions:\n"
        "  ours:\n"
        f"    repo: {repo}\n"
        "    adapter: docstring_tags\n"
        f"    sources: [{PUBLISHED_ID}]\n"
        f"    anchor_source: {PUBLISHED_ID}\n"
        "sources:\n"
        f"  {PUBLISHED_ID}:\n"
        f'    sha256: "{PUBLISHED_SHA}"\n'
        "    title: An Introduction to Measure Theory\n"
        '    edition: "AMS GSM 126 (published pagination)"\n'
        "    offset: 16\n"
        "    role: audited\n"
        "hosts:\n  allowlist: [testbox]\n  remote_root: /home/tester\n"
        "  testbox:\n    ssh: tester@testbox\n"
        "defaults:\n  local_fleet_max_items: 100\n"
        "budget:\n  max_cost_usd: 25\n", encoding="utf-8")
    sdir = root / "sources" / PUBLISHED_ID
    (sdir / "pages").mkdir(parents=True)
    (sdir / "text.txt").write_text(BOOK_TEXT, encoding="utf-8")
    (sdir / "errata.md").write_text("# errata\n\n- nothing relevant\n", encoding="utf-8")
    (sdir / "errata.json").write_text(json.dumps({
        "url": "https://example.org/errata/", "url_provenance": "repo" if errata_repo_supplied
        else "registry", "repo_supplied": bool(errata_repo_supplied),
    }), encoding="utf-8")

    preset = presets_mod.PRESETS["differential-text"]
    run_dir = root / "runs" / "demo"
    (run_dir / "tasks").mkdir(parents=True)
    (run_dir / "tasks" / "t1.yaml").write_text(
        "schema_version: 1\nid: t1\ntitle: \"diff: Exercise 1.2.4\"\n"
        "item: Exercise 1.2.4\ndescription: |\n  compare\n", encoding="utf-8")
    run_yaml = (
        "run_id: demo\n"
        "mode: differential\n"
        "preset: differential-text\n"
        "companions: [ours]\n"
        "repos: [ours]\n"
        "source_access: text\n"
        "host:\n  kind: local\n  name: local\n"
        "sources:\n"
        f"  - id: {PUBLISHED_ID}\n"
        f'    sha256: "{PUBLISHED_SHA}"\n'
        "    offset: 16\n"
        "gate:\n"
        f"  config: {run_dir / 'gate.json'}\n"
        f"  local_config: {run_dir / 'gate.local.json'}\n"
        "  out_of_tree: true\n"
        "  kinds: [" + ", ".join(preset.kinds) + "]\n"
        "  verdicts: [" + ", ".join(preset.verdicts) + "]\n"
        "  witness_required: false\n"
        "tasks:\n  count: 1\n"
        "remote:\n  entrypoint: demo\n  tend_root: demo-tend\n")
    (run_dir / "run.yaml").write_text(run_yaml, encoding="utf-8")
    return {"root": root, "run_dir": run_dir, "repo": repo, "preset": preset,
            "workspace": Workspace.load(root)}


def cf(*argv) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cf_main.main(list(argv))
    return rc, buf.getvalue()


def write_journal(case: unittest.TestCase, rows: list, labels: dict) -> Path:
    """A ``wf_*`` directory holding ``journal.jsonl`` and its agent labels."""
    wf = scratch(case) / "wf_x"
    wf.mkdir()
    for agent_id, label in labels.items():
        (wf / f"agent-{agent_id}.meta.json").write_text(
            json.dumps({"label": label}), encoding="utf-8")
    (wf / "journal.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return wf


def orphan_journal(case: unittest.TestCase) -> Path:
    """One recovered item and one ruling that names a different item."""
    return write_journal(case, [
        {"type": "result", "agentId": "a1", "value": {
            "item": "Exercise 1.2.4", "book_located": True, "matches": [],
            "disagreements": [], "coverage_notes": ""}},
        {"type": "result", "agentId": "a2", "value": {
            "ruling_survives": False, "evidence": "e"}},
    ], {"a1": "work:Exercise 1.2.4", "a2": "chal0:Exercise 9.9.9#0"})


# ---------------------------------------------------------------------------
# rendering one `label:` expression out of workflows/*.js
#
# The round-trip has to be driven by the expressions the scripts really contain,
# not by a hand-written table of labels: a table is how the contract came to
# pin `chal0:Exercise_1.2.4` while the script emitted `chal0:Exercise 1.2.4#0`.

_SLICE = re.compile(r"^\((?P<inner>.+)\)\.slice\((?P<start>\d+),\s*(?P<stop>\d+)\)$")
_GROUP = re.compile(r"^\((?P<inner>.+)\)$")
_INCR = re.compile(r"^(?P<name>[A-Za-z_][\w.]*)\s*\+\s*(?P<add>\d+)$")


def _split_plus(expression: str) -> list:
    """Top-level ``+`` operands of a JS string concatenation."""
    out, depth, quote, buf = [], 0, "", ""
    for ch in expression:
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote, buf = ch, buf + ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "+" and depth == 0:
            out.append(buf.strip())
            buf = ""
            continue
        buf += ch
    out.append(buf.strip())
    return [term for term in out if term]


def _term(term: str, env: dict) -> str:
    if len(term) >= 2 and term[0] == term[-1] == "'":
        return term[1:-1]
    sliced = _SLICE.match(term)
    if sliced:
        value = _term("(" + sliced.group("inner") + ")", env)
        return value[int(sliced.group("start")):int(sliced.group("stop"))]
    grouped = _GROUP.match(term)
    if grouped:
        inner = grouped.group("inner")
        incr = _INCR.match(inner)
        if incr:
            return str(int(env[incr.group("name")]) + int(incr.group("add")))
        if "||" in inner:
            for alternative in (a.strip() for a in inner.split("||")):
                value = _term(alternative, env)
                if value:
                    return value
            return ""
        return _term(inner, env)
    if term not in env:
        raise AssertionError(f"the label renderer has no value for {term!r}")
    return str(env[term])


def render_js_label(expression: str, env: dict) -> str:
    """Evaluate one ``label:`` expression from ``workflows/*.js`` against *env*."""
    return "".join(_term(term, env) for term in _split_plus(expression))


def label_env(item: str) -> dict:
    """Values for every identifier the declared label expressions read."""
    return {
        "t.item": item, "f.item": item, "did": item + "#1",
        "d.label": item, "d.item": item, "u.label": item, "u.item": item,
        "k": 0, "i": 3, "l": "book",
        "a.title": "an assessment of the elementary measure findings",
        "l.key": "corpus-numbers",
    }


# ---------------------------------------------------------------------------
# 1. the label / id contract


class TestLabelContract(unittest.TestCase):
    """One grammar, emitted by the scripts and parsed by harvest."""

    #: (label as a script really renders it, role, key, item, disagreement index)
    ROUND_TRIP = (
        ("work:Exercise 1.2.4", "work", "Exercise 1.2.4", "Exercise 1.2.4", None),
        ("chal0:Exercise 1.2.4#0", "chal", "Exercise 1.2.4#0", "Exercise 1.2.4", 0),
        ("chal1:Exercise 1.2.4#11", "chal", "Exercise 1.2.4#11", "Exercise 1.2.4", 11),
        ("chal0:Exercise 1.2.4", "chal", "Exercise 1.2.4", "Exercise 1.2.4", None),
        ("F0:book:Exercise 1.2.4", "F", "Exercise 1.2.4", "Exercise 1.2.4", None),
        ("F3:reconcile:Exercise 1.2.4", "F", "Exercise 1.2.4", "Exercise 1.2.4", None),
        ("F3:skeptic-content:Exercise 1.2.4", "F", "Exercise 1.2.4", "Exercise 1.2.4", None),
        ("D2:reader1:Definition 1.2.3", "D", "Definition 1.2.3", "Definition 1.2.3", None),
        ("U4:Definition 1.2.3", "U", "Definition 1.2.3", "Definition 1.2.3", None),
        ("verify:numbers-independent", "verify", "numbers-independent",
         "numbers-independent", None),
        # the original's format, which the corpus on disk still carries
        ("chal0:Exercise_1.2.4", "chal", "Exercise_1.2.4", "Exercise 1.2.4", None),
        # an item whose own name contains a colon: the LAST field is a fragment
        # of it, and `item_full` is the reading that names the item.  Both are
        # candidates, which is what `label_item_candidates` is for.
        ("chal0:" + COLON_ITEM + "#0", "chal",
         "extension of widget weight to unbounded assemblies)#0",
         "extension of widget weight to unbounded assemblies)", 0),
        ("F0:book:" + COLON_ITEM, "F",
         "extension of widget weight to unbounded assemblies)",
         "extension of widget weight to unbounded assemblies)", None),
    )

    #: every declared label expression -> does the label it renders NAME an item?
    #: `critic` names nothing, a verify lens key is not an item, and an
    #: assessment title is truncated to 24 characters on purpose.
    CARRIES_ITEM = {
        "'work:' + t.item": True,
        "'chal' + k + ':' + did": True,
        "'chal' + k + ':' + f.item": True,
        "'F' + i + ':' + l + ':' + f.item": True,
        "'F' + i + ':reconcile:' + f.item": True,
        "'F' + i + ':skeptic-content:' + f.item": True,
        "'F' + i + ':skeptic-convention:' + f.item": True,
        "'D' + i + ':reader' + (k + 1) + ':' + (d.label || d.item || '')": True,
        "'D' + i + ':reconcile:' + (d.label || d.item || '')": True,
        "'U' + i + ':' + (u.label || u.item || '')": True,
        "'A' + i + ':' + (a.title || '').slice(0, 24)": False,
        "'critic'": False,
        "'verify:' + (l.key || i)": False,
    }

    #: item names to render each expression with.  The plain one is the shape
    #: everyone tests; the other two are the shapes that break a parser.
    ITEM_NAMES = ("Exercise 1.2.4", COLON_ITEM, UNDERSCORE_ITEM)

    def test_every_literal_label_expression_is_in_the_shipped_script(self):
        """The contract is asserted against the .js, not against a Python copy.

        tests/test_harvest.py pinned the Python side alone, so when
        fleet-local.workflow.js started appending `#<index>` nothing noticed.
        """
        for name, expressions in harvest_journal.JS_LABEL_EXPRESSIONS.items():
            text = (SKILL_ROOT / "workflows" / name).read_text(encoding="utf-8")
            for expression in expressions:
                self.assertIn("label: " + expression, text,
                              f"{name} no longer emits `{expression}`")

    def test_no_label_expression_in_the_scripts_is_undeclared(self):
        for name in WORKFLOWS:
            text = js(name)
            declared = harvest_journal.JS_LABEL_EXPRESSIONS.get(
                f"{name}.workflow.js", ())
            for found in re.findall(r"label: (.*?),\n?\s*phase", text):
                self.assertIn(found, declared,
                              f"{name}.workflow.js emits an undeclared label {found!r}; "
                              "add it to harvest_journal.JS_LABEL_EXPRESSIONS")

    def test_the_grammar_round_trips_every_shipped_label(self):
        for label, role, key, item, disagreement in self.ROUND_TRIP:
            with self.subTest(label=label):
                parsed = harvest_journal.parse_label(label)
                self.assertEqual(parsed["role"], role)
                self.assertEqual(parsed["key"], key)
                self.assertEqual(parsed["item"], item)
                self.assertEqual(parsed["disagreement"], disagreement)

    def test_every_declared_label_expression_is_given_a_round_trip_case(self):
        declared = {expression
                    for expressions in harvest_journal.JS_LABEL_EXPRESSIONS.values()
                    for expression in expressions}
        self.assertEqual(declared, set(self.CARRIES_ITEM),
                         "a declared label expression with no round-trip case is a "
                         "contract nobody checks; add it to CARRIES_ITEM")

    def test_every_declared_expression_round_trips_the_item_it_names(self):
        """Rendered from the .js expressions, not from a hand-written label list.

        A table of labels is how the old test came to pin one side of the
        contract; an expression whose rendered label does not round-trip -- an
        item with a colon in its name, say -- was invisible to it.
        """
        for expression, carries in self.CARRIES_ITEM.items():
            for item in self.ITEM_NAMES:
                with self.subTest(expression=expression, item=item):
                    label = render_js_label(expression, label_env(item))
                    candidates = harvest_journal.label_item_candidates(label)
                    if not carries:
                        self.assertNotIn(item, candidates, label)
                        continue
                    self.assertIn(item, candidates, label)
                    # and the join the harvest really performs uses them
                    linked = harvest_journal.link_challenges(
                        {item: {"item": item}},
                        [{"ruling_survives": True, "_label": label}])
                    self.assertEqual(len(linked[item]), 1, label)

    def test_a_fleet_local_challenge_ruling_links_to_its_item(self):
        """The regression: `chal0:<item>#<index>` matched nothing at all."""
        for item in ("Exercise 1.2.4", COLON_ITEM):
            with self.subTest(item=item):
                items = {item: {"item": item, "disagreements": [{"concept": "c"}]}}
                rows = [{"ruling_survives": False, "evidence": "e",
                         "disagreement_id": item + "#0",
                         "_label": "chal0:" + item + "#0"},
                        {"ruling_survives": True, "evidence": "e2",
                         "_label": "chal1:" + item + "#0"}]
                linked = harvest_journal.link_challenges(items, rows)
                self.assertEqual(len(linked[item]), 2)
                self.assertEqual([c["link_method"] for c in linked[item]],
                                 ["disagreement-id", "label-item"])
                summary = harvest_journal.summarise(items, linked, rows)
                self.assertEqual(summary["challenge_results"], 2)
                self.assertEqual(summary["items_refuted_by_any"], 1)
                self.assertEqual(summary["challenges_unlinked"], 0)

    def test_a_disagreement_id_is_split_not_parsed_as_a_label(self):
        """An id is `<item>#<index>`; reading it as a label truncated the item."""
        self.assertEqual(harvest_journal.split_disagreement_id(COLON_ITEM + "#4"),
                         (COLON_ITEM, 4))
        self.assertEqual(harvest_journal.split_disagreement_id(COLON_ITEM),
                         (COLON_ITEM, None))
        items = {COLON_ITEM: {"item": COLON_ITEM, "disagreements": []}}
        # the label is gone (a lost meta file); only the row's own id is left
        linked = harvest_journal.link_challenges(
            items, [{"ruling_survives": True, "disagreement_id": COLON_ITEM + "#0"}])
        self.assertEqual([c["link_method"] for c in linked[COLON_ITEM]],
                         ["disagreement-id"])

    def test_the_legacy_truncated_prefix_still_links(self):
        item = "Exercise 1.2.4 part (iii) of the elementary measure"
        items = {item: {"item": item, "disagreements": []}}
        key = item.replace(" ", "_")[:harvest_journal.LEGACY_PREFIX_LEN]
        linked = harvest_journal.link_challenges(
            items, [{"ruling_survives": True, "_label": "chal0:" + key}])
        self.assertEqual(linked[item][0]["link_method"], "legacy-prefix")

    def test_rulings_that_link_to_nothing_are_a_refusal_not_a_row_of_zeros(self):
        items = {"Exercise 1.2.4": {"item": "Exercise 1.2.4", "disagreements": []}}
        rows = [{"ruling_survives": False, "_label": "chal0:Exercise 9.9.9#0"}]
        with self.assertRaises(CfError) as ctx:
            harvest_journal.link_challenges(items, rows)
        self.assertIn("Exercise 9.9.9#0", str(ctx.exception))
        # and the escape hatch records the loss instead of hiding it
        linked = harvest_journal.link_challenges(items, rows, strict=False)
        summary = harvest_journal.summarise(items, linked, rows)
        self.assertEqual(summary["challenges_unlinked"], 1)
        self.assertEqual(summary["unmatched_labels"], ["chal0:Exercise 9.9.9#0"])


# ---------------------------------------------------------------------------
# 2. what a killed journal recovers


class TestJournalRecovery(unittest.TestCase):
    def _journal(self, rows: list[dict], labels: dict[str, str]) -> Path:
        return write_journal(self, rows, labels)

    def test_an_adjudication_journal_recovers_its_rows(self):
        """A killed `cf adjudicate` or `cf challenge` run recovered nothing at all."""
        wf = self._journal([
            {"type": "result", "agentId": "a1", "value": {
                "item": "Exercise 1.2.4", "verdict": "CONVENTION", "severity": "low",
                "confidence": 0.9, "book_claim": "b", "lean_claim": "l", "delta": "d",
                "fix": "f", "fix_effort": "trivial"}},
            {"type": "result", "agentId": "a2", "value": {
                "item": "Exercise 1.2.4", "verdict": "CONVENTION", "severity": "low",
                "confidence": 0.9, "rationale": "r", "fix_effort": "trivial",
                "pr_recommended": False}},
            {"type": "result", "agentId": "a3", "value": {
                "overturned": True, "confidence": 0.8, "argument": "a",
                "proposed_verdict": "CONFIRMED_CONTENT"}},
            {"type": "result", "agentId": "a4", "value": {
                "label": "Definition 1.2.2", "book_definition": "b",
                "per_definition": [], "missing_from_repo": "", "overall": "FAITHFUL"}},
            {"type": "result", "agentId": "a5", "value": {
                "label": "Definition 1.2.3", "book_definition_summary": "s",
                "classification": "LIBRARY_STANDIN_ONLY", "library_standin": "MeasureTheory",
                "same_meaning": "yes", "evidence": "e"}},
            {"type": "result", "agentId": "a6", "value": {
                "finding_id": "f" * 16, "item": "Exercise 1.2.4",
                "ruling_survives": False, "evidence": "e"}},
        ], {"a1": "F0:book:Exercise 1.2.4", "a2": "F0:reconcile:Exercise 1.2.4",
            "a3": "F0:skeptic-content:Exercise 1.2.4", "a4": "D0:reader1:Definition 1.2.2",
            "a5": "U0:Definition 1.2.3", "a6": "chal0:Exercise 1.2.4"})
        rows = harvest_journal.read_rows(wf)
        self.assertEqual(len(rows["lens"]), 1)
        self.assertEqual(len(rows["reconciled"]), 1)
        self.assertEqual(len(rows["skeptic"]), 1)
        self.assertEqual(len(rows["definition"]), 1)
        self.assertEqual(len(rows["uncovered"]), 1)
        self.assertEqual(len(rows["challenge"]), 1)
        self.assertEqual(rows["skeptic"][0]["_role"], "F")
        # the two-tuple the harvest command reads is unchanged
        items, challenges = harvest_journal.read_journal(wf)
        self.assertEqual(items, {})
        self.assertEqual(len(challenges), 1)

    def test_a_recovered_record_carries_the_four_keys_the_gate_reads(self):
        dest = scratch(self) / "corpus"
        harvest_journal.write_corpus(
            {"Exercise 1.2.4": {"item": "Exercise 1.2.4", "book_located": True,
                                "matches": [], "disagreements": [], "coverage_notes": ""}},
            {}, dest, "demo", source_id=PUBLISHED_ID, source_sha=PUBLISHED_SHA)
        record = json.loads(next(dest.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(record["schema_version"], harvest_journal.CORPUS_SCHEMA_VERSION)
        self.assertEqual(record["run_id"], "demo")
        self.assertEqual(record["source_id"], PUBLISHED_ID)
        self.assertEqual(record["source_sha"], PUBLISHED_SHA)

    def test_the_workers_own_stamps_win(self):
        dest = scratch(self) / "corpus"
        harvest_journal.write_corpus(
            {"Exercise 1.2.4": {"item": "Exercise 1.2.4", "schema_version": 7,
                                "run_id": "its-own", "disagreements": []}},
            {}, dest, "demo", source_id=PUBLISHED_ID)
        record = json.loads(next(dest.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(record["schema_version"], 7)
        self.assertEqual(record["run_id"], "its-own")


class TestWhoRefusesOrphanedRulings(unittest.TestCase):
    """The refusal belongs to the command that WRITES a corpus, and only there.

    ``link_challenges`` refuses by default so a run's adversarial half cannot be
    dropped in silence -- but a read-only progress query must report what it
    found, and a partial loss must show up in the counts of the command that did
    build the corpus.
    """

    def test_a_status_query_reports_the_orphans_instead_of_refusing(self):
        from run import status as run_status

        counts = run_status.journal_counts(orphan_journal(self))
        self.assertEqual(counts["items"], 1)
        self.assertEqual(counts["challenge_rows_seen"], 1)
        self.assertEqual(counts["challenges_unlinked"], 1)
        self.assertEqual(counts["unmatched_labels"], ["chal0:Exercise 9.9.9#0"])

    def test_the_status_command_prints_the_journal_line_and_exits_zero(self):
        ws = make_workspace(self)
        rc, out = cf("run", "demo", "--status", "--journal", str(orphan_journal(self)),
                     "--workspace", str(ws["root"]))
        self.assertEqual(rc, 0, out)
        self.assertIn("challenges_unlinked=1", out)

    def test_cf_harvest_reports_a_partial_loss_in_its_counts(self):
        """The half of the defect a zero-link refusal cannot catch.

        With SOME rulings linked, `cf harvest --journal` printed a plausible
        `challenge_results=1` and said nothing about the ruling it discarded.
        """
        ws = make_workspace(self)
        wf = write_journal(self, [
            {"type": "result", "agentId": "a1", "value": {
                "item": "Exercise 1.2.4", "book_located": True, "matches": [],
                "disagreements": [], "coverage_notes": ""}},
            {"type": "result", "agentId": "a2", "value": {
                "ruling_survives": True, "evidence": "e",
                "disagreement_id": "Exercise 1.2.4#0"}},
            {"type": "result", "agentId": "a3", "value": {
                "ruling_survives": False, "evidence": "e"}},
        ], {"a1": "work:Exercise 1.2.4", "a2": "chal0:Exercise 1.2.4#0",
            "a3": "chal1:Exercise 9.9.9#0"})
        rc, out = cf("harvest", "demo", "--journal", str(wf),
                     "--workspace", str(ws["root"]))
        self.assertEqual(rc, 0, out)
        line = [l for l in out.splitlines() if l.startswith("journal:")][0]
        self.assertIn("challenge_results=1", line)
        self.assertIn("challenge_rows_seen=2", line)
        self.assertIn("challenges_unlinked=1", line)
        self.assertIn("unmatched_labels=['chal1:Exercise 9.9.9#0']", line)

    def test_cf_harvest_refuses_when_nothing_links_and_names_its_own_escape(self):
        ws = make_workspace(self)
        wf = orphan_journal(self)
        with self.assertRaises(CfError) as ctx:
            cf("harvest", "demo", "--journal", str(wf), "--workspace", str(ws["root"]))
        self.assertIn("--allow-unlinked-challenges", str(ctx.exception.hint))
        rc, out = cf("harvest", "demo", "--journal", str(wf), "--workspace",
                     str(ws["root"]), "--allow-unlinked-challenges")
        self.assertEqual(rc, 0, out)
        self.assertIn("challenges_unlinked=1", out)

    def test_the_standalone_script_renders_the_refusal_not_a_traceback(self):
        """`harvest_journal.py` is run by hand; a traceback hides the hint."""
        wf = orphan_journal(self)
        dest = scratch(self) / "corpus"
        argv = [sys.executable, str(SCRIPTS / "harvest" / "harvest_journal.py"),
                str(wf), "--dest", str(dest)]
        proc = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertTrue(proc.stderr.startswith("error: "), proc.stderr)
        self.assertIn("--allow-unlinked-challenges", proc.stderr)
        allowed = subprocess.run(argv + ["--allow-unlinked-challenges"],
                                 capture_output=True, text=True)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn('"challenges_unlinked": 1', allowed.stdout)


# ---------------------------------------------------------------------------
# 3. a local corpus against the gate the tool prints next


class TestLocalCorpusPassesItsOwnGate(unittest.TestCase):
    """fleet_local.build() args -> a record shaped by the .js -> write_corpus -> gate."""

    def setUp(self):
        self.ws = make_workspace(self)

    def _fleet_args(self) -> dict:
        rc, out = cf("run", "demo", "--local", "--workspace", str(self.ws["root"]), "-q")
        self.assertEqual(rc, 0, out)
        path = self.ws["run_dir"] / "fleet-local.args.json"
        self.assertTrue(path.exists(), out)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_gate_block_carries_the_presets_enums(self):
        gate = self._fleet_args()["gate"]
        preset = self.ws["preset"]
        self.assertEqual(gate["kinds"], list(preset.kinds))
        self.assertEqual(gate["verdicts"], list(preset.verdicts))
        self.assertEqual(gate["schema_version"], fleet_local.DEFAULT_SCHEMA_VERSION)
        self.assertEqual(fleet_local.missing_keys(self._fleet_args()), [])

    def test_the_script_builds_its_output_enums_from_the_gate(self):
        text = js("fleet-local")
        self.assertIn("enum: VERDICTS", text)
        self.assertIn("enum: KINDS", text)
        self.assertIn("GATE.kinds && GATE.kinds.length", text)
        self.assertIn("GATE.verdicts && GATE.verdicts.length", text)
        self.assertIn("GATE.schema_version", text)
        # PROXY was in the hand-written kind enum and is in no preset's gate
        self.assertNotIn("'PROXY'", text)

    def test_a_conformant_local_record_passes_verify_findings(self):
        args = self._fleet_args()
        gate, source = args["gate"], args["source"]
        record = {
            "schema_version": gate["schema_version"],
            "run_id": args["run_id"],
            "source_id": source["source_id"],
            "source_sha": source["source_sha"],
            "item": "Exercise 1.2.4",
            "book_located": True,
            "book_passage_quote": BOOK_QUOTE,
            "matches": [{"concept": "finite additivity",
                         "decls": [{"repo": "ours", "decl": "foo"}],
                         "verdict": gate["verdicts"][0]}],
            "disagreements": [{
                "concept": "finite additivity",
                "kind": gate["kinds"][0],
                "repos_involved": ["ours"],
                "faithful": [],
                "unfaithful_decls": ["ours:foo"],
                "book_quote": BOOK_QUOTE,
                "reasoning": "the dimension hypothesis is missing",
                "severity": "high",
            }],
            "coverage_notes": "",
        }
        dest = scratch(self) / "corpus"
        harvest_journal.write_corpus({record["item"]: record}, {}, dest, args["run_id"],
                                     source_id=source["source_id"],
                                     source_sha=source["source_sha"])
        config = presets_mod.gate_config(
            self.ws["preset"], run_id=args["run_id"],
            sources={source["source_id"]: {"text": source["text"],
                                           "sha256": source["source_sha"],
                                           "page_offset": source["page_offset"]}},
            repos=["ours"])
        config_path = scratch(self) / "gate.json"
        config_path.write_text(json.dumps(config, indent=1), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "gate" / "verify_findings.py"),
             "--gate-config", str(config_path), "--root", str(dest), "--findings-dir", "."],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


# ---------------------------------------------------------------------------
# 4. the script bodies, actually executed


DRIVER_JS = """\
import { readFileSync } from 'node:fs'
import body from './body.mjs'

const spec = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const calls = []
const logs = []

function agent(prompt, opts) {
  const o = opts || {}
  const label = o.label || ''
  calls.push({ label, phase: o.phase || '', model: o.model === undefined ? null : o.model,
               schema: o.schema || null, prompt })
  const hit = Object.prototype.hasOwnProperty.call(spec.responses, label)
  return Promise.resolve(hit ? spec.responses[label] : null)
}

async function pipeline(items, ...stages) {
  const out = []
  for (let i = 0; i < items.length; i++) {
    let value = items[i]
    for (const stage of stages) value = await stage(value, items[i], i)
    out.push(value)
  }
  return out
}

const parallel = (thunks) => Promise.all(thunks.map((t) => t()))
const result = await body(spec.args, agent, pipeline, parallel, (m) => logs.push(String(m)))
process.stdout.write(JSON.stringify({ result, calls, logs }))
"""


def run_body(case: unittest.TestCase, name: str, args: dict, responses: dict) -> dict:
    """Execute one workflow body with stubbed runtime globals.

    The scripts get no filesystem access and no ``require``, so nothing is
    extracted into a sibling module: the body is wrapped as a function of
    ``(args, agent, pipeline, parallel, log)`` -- which is what the runtime gives
    it -- exactly as ``test_scripts_parse`` already wraps it for ``node --check``.
    """
    if not NODE:
        raise unittest.SkipTest("node is not installed")
    tmp = scratch(case)
    body = js(name).replace("export const meta =", "const meta =", 1)
    (tmp / "body.mjs").write_text(
        "export default async function(args, agent, pipeline, parallel, log) {\n"
        f"{body}\n}}\n", encoding="utf-8")
    (tmp / "driver.mjs").write_text(DRIVER_JS, encoding="utf-8")
    (tmp / "spec.json").write_text(json.dumps({"args": args, "responses": responses}),
                                   encoding="utf-8")
    proc = subprocess.run([NODE, str(tmp / "driver.mjs"), str(tmp / "spec.json")],
                          capture_output=True, text=True, cwd=str(tmp))
    case.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
    return json.loads(proc.stdout)


SOURCE_BLOCK = {
    "source_id": PUBLISHED_ID,
    "title": "An Introduction to Measure Theory",
    "edition": "AMS GSM 126",
    "source_sha": PUBLISHED_SHA,
    "page_offset": 16,
    "pages_dir": "/w/sources/pages",
    "text": "/w/sources/text.txt",
    "items_json": "/w/sources/items.json",
    "errata": "",
    "errata_withheld": workflow_args.ERRATA_WITHHELD,
    "errata_url": "https://example.org/errata/",
}

FLEET_ARGS = {
    "run_id": "demo",
    "source": {k: SOURCE_BLOCK[k] for k in
               ("source_id", "title", "source_sha", "text", "pages_dir",
                "page_offset", "errata", "errata_withheld")},
    "repos": [{"name": "ours", "repo": "/r/ours", "statements": "/c/ours.jsonl",
               "conventions": "total functions"}],
    "mode": "differential",
    "gate": {"command": "python3 gate.py", "config": "/r/gate.local.json",
             "kinds": ["CONTENT", "COVERAGE", "SUSPECTED_ERRATA", "VACUITY"],
             "verdicts": ["AGREE", "CONTENT_DELTA", "CONVENTION_ONLY"],
             "schema_version": 2, "witness_required": False,
             "quote_min_chars": 25, "scratch_dir": "/r/self-gate"},
    "model": "opus", "challenge": True, "challenge_model": "opus", "pairs": "",
    "items": [{"item": "Exercise 1.2.4", "source_id": PUBLISHED_ID, "repos": ["ours"],
               "pdf_pages": [21], "note": ""}],
}

#: one PAGE_ANCHOR row followed by one CONTENT row -- the shape that stapled two
#: verdicts about the CONTENT ruling onto the PAGE_ANCHOR one as well
WORK_RESULT = {
    "schema_version": 2, "run_id": "demo", "source_id": PUBLISHED_ID,
    "source_sha": PUBLISHED_SHA,
    "item": "Exercise 1.2.4", "book_located": True, "book_passage_quote": BOOK_QUOTE,
    "matches": [], "coverage_notes": "",
    "gate": {"command": "python3 gate.py", "passed": True, "output": "ok"},
    "disagreements": [
        {"concept": "anchor off by one", "kind": "PAGE_ANCHOR", "repos_involved": ["ours"],
         "faithful": [], "book_quote": BOOK_QUOTE, "reasoning": "r", "severity": "low"},
        {"concept": "missing d >= 1", "kind": "CONTENT", "repos_involved": ["ours"],
         "faithful": [], "book_quote": BOOK_QUOTE, "reasoning": "r", "severity": "high"},
    ],
}


def refuter(did: str, survives: bool = True, concept: str = "paraphrased") -> dict:
    return {"disagreement_id": did, "concept": concept, "ruling_survives": survives,
            "classification": "CONTENT", "evidence": "e", "corrected_ruling": ""}


class TestFleetLocalBody(unittest.TestCase):
    """The local fleet's reduction, executed."""

    def _run(self, responses: dict, args: dict | None = None) -> dict:
        return run_body(self, "fleet-local", args or FLEET_ARGS, responses)

    def test_verdicts_attach_to_the_disagreement_they_read(self):
        out = self._run({
            "work:Exercise 1.2.4": WORK_RESULT,
            "chal0:Exercise 1.2.4#1": refuter("Exercise 1.2.4#1"),
            "chal1:Exercise 1.2.4#1": refuter("Exercise 1.2.4#1"),
        })
        by_kind = {f["kind"]: f for f in out["result"]["findings"]}
        # the PAGE_ANCHOR row is not challenged, so nothing is stapled onto it
        self.assertEqual(by_kind["PAGE_ANCHOR"]["challenge_verdicts"], [])
        self.assertEqual(by_kind["PAGE_ANCHOR"]["challenges_expected"], 0)
        self.assertFalse(by_kind["PAGE_ANCHOR"]["survives_all"])
        self.assertEqual(len(by_kind["CONTENT"]["challenge_verdicts"]), 2)
        self.assertTrue(by_kind["CONTENT"]["survives_all"])
        self.assertEqual(by_kind["CONTENT"]["missing_votes"], 0)
        # the id is the index in the UNFILTERED list, so the labels say #1
        self.assertEqual(by_kind["CONTENT"]["disagreement_id"], "Exercise 1.2.4#1")
        labels = [c["label"] for c in out["calls"] if c["phase"] == "Challenge"]
        self.assertEqual(labels, ["chal0:Exercise 1.2.4#1", "chal1:Exercise 1.2.4#1"])

    def test_a_refuted_ruling_does_not_survive(self):
        out = self._run({
            "work:Exercise 1.2.4": WORK_RESULT,
            "chal0:Exercise 1.2.4#1": refuter("Exercise 1.2.4#1", survives=False),
            "chal1:Exercise 1.2.4#1": refuter("Exercise 1.2.4#1", survives=True),
        })
        content = [f for f in out["result"]["findings"] if f["kind"] == "CONTENT"][0]
        self.assertFalse(content["survives_all"])
        self.assertEqual(out["result"]["summary"]["survived_all"], 0)

    def test_a_missing_vote_is_missing_never_a_pass(self):
        out = self._run({
            "work:Exercise 1.2.4": WORK_RESULT,
            "chal0:Exercise 1.2.4#1": refuter("Exercise 1.2.4#1"),
        })
        content = [f for f in out["result"]["findings"] if f["kind"] == "CONTENT"][0]
        self.assertEqual(content["missing_votes"], 1)
        self.assertFalse(content["survives_all"])

    def test_a_ruling_that_dropped_the_id_is_reported_not_discarded(self):
        out = self._run({
            "work:Exercise 1.2.4": WORK_RESULT,
            "chal0:Exercise 1.2.4#1": refuter("Exercise 1.2.4#1"),
            "chal1:Exercise 1.2.4#1": {"concept": "missing d >= 1", "ruling_survives": False,
                                       "evidence": "e"},
        })
        self.assertEqual(len(out["result"]["unattached_challenges"]), 1)
        self.assertEqual(out["result"]["summary"]["unattached_challenges"], 1)
        self.assertTrue(any("disagreement_id" in m for m in out["logs"]))

    def test_the_prompt_and_schema_come_from_the_runs_gate(self):
        out = self._run({"work:Exercise 1.2.4": WORK_RESULT})
        work = [c for c in out["calls"] if c["phase"] == "Work"][0]
        self.assertIn("KINDS you may report: CONTENT, COVERAGE, SUSPECTED_ERRATA, VACUITY",
                      work["prompt"])
        self.assertIn(PUBLISHED_SHA, work["prompt"])
        kinds = work["schema"]["properties"]["disagreements"]["items"]["properties"]["kind"]
        self.assertEqual(kinds["enum"], FLEET_ARGS["gate"]["kinds"])
        verdicts = work["schema"]["properties"]["matches"]["items"]["properties"]["verdict"]
        self.assertEqual(verdicts["enum"], FLEET_ARGS["gate"]["verdicts"])
        for key in ("schema_version", "run_id", "source_id", "source_sha"):
            self.assertIn(key, work["schema"]["required"])

    def test_the_refuter_is_told_to_copy_the_id_through(self):
        out = self._run({
            "work:Exercise 1.2.4": WORK_RESULT,
            "chal0:Exercise 1.2.4#1": refuter("Exercise 1.2.4#1"),
            "chal1:Exercise 1.2.4#1": refuter("Exercise 1.2.4#1"),
        })
        chal = [c for c in out["calls"] if c["phase"] == "Challenge"][0]
        self.assertIn("disagreement_id : Exercise 1.2.4#1", chal["prompt"])
        self.assertIn("Copy disagreement_id through unchanged", chal["prompt"])
        self.assertIn("disagreement_id", chal["schema"]["required"])

    def test_a_withheld_errata_is_named_to_the_local_worker_too(self):
        """The two adjudication workflows say why there is no errata; this one did not.

        `cf run --local` already withheld an unconfirmed repo-supplied errata
        and put the reason in args, but the script never read the key -- so the
        local worker, alone among the three, was told nothing at all.
        """
        out = self._run({"work:Exercise 1.2.4": WORK_RESULT})
        work = [c for c in out["calls"] if c["phase"] == "Work"][0]
        self.assertIn(workflow_args.ERRATA_WITHHELD, work["prompt"])

    def test_a_confirmed_errata_is_named_as_a_path_and_not_as_withheld(self):
        args = json.loads(json.dumps(FLEET_ARGS))
        args["source"]["errata"] = "/w/sources/errata.md"
        out = self._run({"work:Exercise 1.2.4": WORK_RESULT}, args)
        work = [c for c in out["calls"] if c["phase"] == "Work"][0]
        self.assertIn("/w/sources/errata.md", work["prompt"])
        self.assertNotIn(workflow_args.ERRATA_WITHHELD, work["prompt"])

    def test_every_emitted_label_parses(self):
        out = self._run({
            "work:Exercise 1.2.4": WORK_RESULT,
            "chal0:Exercise 1.2.4#1": refuter("Exercise 1.2.4#1"),
            "chal1:Exercise 1.2.4#1": refuter("Exercise 1.2.4#1"),
        })
        for call in out["calls"]:
            parsed = harvest_journal.parse_label(call["label"])
            self.assertEqual(parsed["item"], "Exercise 1.2.4", call["label"])

    def test_an_item_with_no_result_is_reported_as_remaining(self):
        out = self._run({})
        self.assertEqual(out["result"]["summary"]["items_done"], 0)
        self.assertEqual(out["result"]["summary"]["remaining"], ["Exercise 1.2.4"])


class TestChallengeAndAdjudicateBodies(unittest.TestCase):
    FINDING = {"finding_id": "a" * 16, "item": "Exercise 1.2.4", "kind": "CONTENT",
               "severity": "high", "concept": "missing d >= 1", "run_id": "demo",
               "faithful": [], "unfaithful_decls": ["ours:foo"], "book_quote": BOOK_QUOTE,
               "reasoning": "r", "suggested_fix": "s", "source_id": PUBLISHED_ID,
               "source_sha": PUBLISHED_SHA, "decls": []}

    def _args(self, **extra) -> dict:
        args = {
            "workspace": {"dir": "/w", "name": "tao", "sources": [SOURCE_BLOCK]},
            "companion": {"name": "ours", "repo": "/r/ours", "commit": "abc",
                          "statements": "/c/ours.jsonl", "conventions": "total functions",
                          "sources": [PUBLISHED_ID], "anchor_source": PUBLISHED_ID},
            "peers": [], "findings": [self.FINDING], "notes": "", "model": "opus",
        }
        args.update(extra)
        return args

    def test_challenge_returns_survives_all_and_two_labels_per_finding(self):
        vote = {"finding_id": self.FINDING["finding_id"], "item": self.FINDING["item"],
                "ruling_survives": True, "evidence": "e"}
        out = run_body(self, "challenge", self._args(refuters=2), {
            "chal0:Exercise 1.2.4": vote, "chal1:Exercise 1.2.4": vote})
        result = out["result"]
        self.assertEqual(result["schema"], "cf-challenge-v2")
        self.assertEqual(result["survived"], 1)
        self.assertTrue(result["findings"][0]["survives_all"])
        self.assertEqual(result["findings"][0]["missing_votes"], 0)
        self.assertEqual([c["label"] for c in out["calls"]],
                         ["chal0:Exercise 1.2.4", "chal1:Exercise 1.2.4"])

    def test_challenge_counts_a_missing_vote_as_missing(self):
        vote = {"finding_id": self.FINDING["finding_id"], "item": self.FINDING["item"],
                "ruling_survives": True, "evidence": "e"}
        out = run_body(self, "challenge", self._args(refuters=2),
                       {"chal0:Exercise 1.2.4": vote})
        self.assertFalse(out["result"]["findings"][0]["survives_all"])
        self.assertEqual(out["result"]["findings"][0]["missing_votes"], 1)

    def test_the_sources_are_read_from_args_workspace_not_the_top_level(self):
        """`W = A.workspace`, so `sources` at the top level renders nothing.

        Nothing in the suite caught that nesting before, because no test ever
        executed the body: `srcOf` simply returned `{}` and the prompt named no
        text at all.
        """
        out = run_body(self, "challenge", self._args(refuters=1),
                       {"chal0:Exercise 1.2.4": None})
        self.assertIn(SOURCE_BLOCK["text"], out["calls"][0]["prompt"])
        flat = dict(self._args(refuters=1))
        flat["workspace"] = {"dir": "/w", "name": "tao"}
        flat["sources"] = [SOURCE_BLOCK]
        blind = run_body(self, "challenge", flat, {"chal0:Exercise 1.2.4": None})
        self.assertNotIn(SOURCE_BLOCK["text"], blind["calls"][0]["prompt"])

    def test_a_withheld_errata_is_named_as_withheld(self):
        out = run_body(self, "challenge", self._args(refuters=1),
                       {"chal0:Exercise 1.2.4": None})
        self.assertIn(workflow_args.ERRATA_WITHHELD, out["calls"][0]["prompt"])

    def test_adjudicate_runs_six_agents_per_finding_and_records_dissent(self):
        lens = {"item": "Exercise 1.2.4", "verdict": "CONVENTION", "severity": "low",
                "confidence": 0.9, "book_claim": "b", "lean_claim": "l", "delta": "d",
                "fix": "f", "fix_effort": "trivial"}
        reconciled = {"item": "Exercise 1.2.4", "verdict": "CONVENTION", "severity": "low",
                      "confidence": 0.9, "rationale": "r", "fix_effort": "trivial",
                      "pr_recommended": False}
        skeptic = {"overturned": True, "confidence": 0.8, "argument": "a",
                   "proposed_verdict": "CONFIRMED_CONTENT"}
        out = run_body(self, "adjudicate", self._args(
            definitions=[], uncovered_definitions=[], assessments=[],
            contested_threshold=0.7), {
            "F0:book:Exercise 1.2.4": lens,
            "F0:lean:Exercise 1.2.4": lens,
            "F0:policy:Exercise 1.2.4": lens,
            "F0:reconcile:Exercise 1.2.4": reconciled,
            "F0:skeptic-content:Exercise 1.2.4": skeptic,
            "F0:skeptic-convention:Exercise 1.2.4": dict(skeptic, overturned=False),
            "critic": "nothing missing",
        })
        result = out["result"]
        self.assertEqual(result["schema"], "cf-adjudicate-v2")
        self.assertEqual(result["counts"]["findings_out"], 1)
        self.assertEqual(result["counts"]["contested"], 1)
        self.assertEqual(len(result["findings"][0]["lenses"]), 3)
        self.assertEqual(result["findings"][0]["missing_votes"], 0)
        self.assertEqual(len([c for c in out["calls"] if c["label"].startswith("F0:")]), 6)

    def test_verify_claims_counts_a_silent_lens_as_missing(self):
        out = run_body(self, "verify-claims", {
            "draft": "the corpus holds 411 items",
            "sources": [{"label": "corpus", "path": "/w/corpus"}],
            "lenses": [{"key": "corpus-numbers", "prompt": "check the counts"}],
            "numbers": {"enabled": False},
            "model": "opus",
        }, {"critic": {"missing": [], "overdetailed": [], "misleading": [], "jargon": [],
                      "suggested_tldr": "fine"}})
        self.assertEqual(out["result"]["schema"], "cf-verify-claims-v1")
        self.assertEqual(out["result"]["missing_lenses"], ["corpus-numbers"])


class TestWorkflowArgsCoverEveryKeyTheScriptsRead(unittest.TestCase):
    """The shape check fleet-local had, for the two scripts that had none.

    ``challenge.workflow.js`` and ``adjudicate.workflow.js`` are 566 of the 965
    lines of ``workflows/`` and their args come from
    ``scripts/ledger/workflow_args.py``; nothing checked the two agreed.
    """

    #: attached only when the finding carries them (finding_blocks)
    OPTIONAL_FINDING_KEYS = {"witness", "bridge", "peer_rows"}

    def setUp(self):
        from ledger import compat

        self.ws = make_workspace(self)
        self.cfg = compat.load_workspace(str(self.ws["root"]))
        self.findings = [{"finding_id": "a" * 16, "run_id": "demo",
                          "source_id": PUBLISHED_ID, "source_sha": PUBLISHED_SHA,
                          "item": "Exercise 1.2.4", "kind": "CONTENT", "severity": "high",
                          "concept": "missing d >= 1", "faithful": [],
                          "unfaithful_decls": ["ours:foo"], "book_quote": BOOK_QUOTE,
                          "reasoning": "r", "suggested_fix": "s"}]

    @staticmethod
    def _source_keys(text: str) -> set:
        """``s.<key>`` inside a ``SOURCES.map(s => …)``.

        ``s`` names a skeptic elsewhere in adjudicate.workflow.js, so the scrape
        is scoped to the expressions that really read a source block.
        """
        keys: set = set()
        for match in re.finditer(r"SOURCES\.map\(s =>", text):
            window = text[match.start():match.start() + 600]
            keys |= set(re.findall(r"\bs\.([a-z_]+)\b", window))
        return keys

    @staticmethod
    def _finding_keys(text: str) -> set:
        """``f.<key>`` inside ``findingBlock(f)`` -- the block a prompt renders.

        ``f`` names an adjudicated result in the summary further down, which is a
        different shape entirely.
        """
        body = re.search(r"function findingBlock\(f\) \{(.*?)\n\}", text, re.DOTALL)
        assert body is not None, "findingBlock(f) is gone; the scrape needs updating"
        return set(re.findall(r"\bf\.([a-z_]+)\b", body.group(1)))

    def _check(self, name: str, payload: dict) -> None:
        text = js(name)
        blocks = (("A", payload, set(re.findall(r"\bA\.([a-z_]+)\b", text))),
                  ("W", payload["workspace"], set(re.findall(r"\bW\.([a-z_]+)\b", text))),
                  ("COMP", payload["companion"],
                   set(re.findall(r"\bCOMP\.([a-z_]+)\b", text))),
                  ("s", payload["workspace"]["sources"][0], self._source_keys(text)),
                  ("f", payload["findings"][0],
                   self._finding_keys(text) - self.OPTIONAL_FINDING_KEYS))
        for prefix, block, keys in blocks:
            self.assertTrue(keys, f"{name}.workflow.js: scraped no {prefix}.* keys")
            for key in sorted(keys):
                self.assertIn(key, block,
                              f"{name}.workflow.js reads {prefix}.{key}, "
                              "which the args builder does not supply")

    def test_challenge_args_carry_every_key(self):
        self._check("challenge", workflow_args.build_challenge_args(
            self.cfg, "ours", self.findings))

    def test_adjudicate_args_carry_every_key(self):
        self._check("adjudicate", workflow_args.build_adjudicate_args(
            self.cfg, "ours", self.findings))


# ---------------------------------------------------------------------------
# 5. every printed workflow path is absolute and exists


class TestPrintedWorkflowPaths(unittest.TestCase):
    """`cf` runs from the WORKSPACE, which has no workflows/ directory."""

    def test_the_local_fleet_prints_an_absolute_script_path(self):
        text = fleet_local.instructions(Path("/w/runs/demo/fleet-local.args.json"), 3)
        line = [l for l in text.splitlines() if l.strip().startswith("script:")][0]
        printed = Path(line.split(":", 1)[1].strip())
        self.assertTrue(printed.is_absolute(), printed)
        self.assertTrue(printed.exists(), printed)

    def test_all_four_workflow_references_resolve_through_one_function(self):
        from cflib.paths import skill_root
        from cfcmd import adjudicate as adjudicate_cmd
        from cfcmd import report as report_cmd

        self.assertEqual(Path(adjudicate_cmd._skill_root()), skill_root())
        self.assertEqual(Path(report_cmd._skill_root()), skill_root())
        for script in (adjudicate_cmd.ADJUDICATE_WORKFLOW, adjudicate_cmd.CHALLENGE_WORKFLOW,
                       report_cmd.VERIFY_WORKFLOW, fleet_local.WORKFLOW_SCRIPT):
            path = skill_root() / script
            self.assertTrue(path.exists(), path)

    def test_skill_root_is_one_definition(self):
        """No module re-derives the skill root by stacking dirname() calls.

        Three commands print a workflow path and each resolved the root its own
        way; the one that got it wrong printed a relative path.
        """
        stacked = "os.path.dirname(os.path.dirname(os.path.dirname("
        printers = ("cfcmd/adjudicate.py", "cfcmd/report.py", "cfcmd/run.py",
                    "run/fleet_local.py")
        offenders = [name for name in printers
                     if stacked in (SCRIPTS / name).read_text(encoding="utf-8")]
        self.assertEqual(offenders, [],
                         "use cflib.paths.skill_root() instead: " + ", ".join(offenders))


# ---------------------------------------------------------------------------
# 6. the local fleet's refusal names the real options


class TestLocalFleetRefusal(unittest.TestCase):
    def setUp(self):
        self.ws = make_workspace(self)

    def test_the_hint_names_the_three_real_escapes_and_no_anecdote(self):
        with self.assertRaises(CfError) as ctx:
            fleet_local.build(self.ws["run_dir"], "demo", max_items=0,
                              source={"source_id": PUBLISHED_ID, "title": "t",
                                      "source_sha": PUBLISHED_SHA, "text": "/w/t.txt",
                                      "pages_dir": "/w/p", "page_offset": 16, "errata": ""},
                              repos=[], mode="differential", gate={})
        hint = str(ctx.exception.hint or "")
        self.assertIn("--host <alias> --i-have-consent", hint)
        self.assertIn("--force", hint)
        self.assertIn("local_fleet_max_items", hint)
        # the old hint named `cf run` -- the command that had just failed -- and
        # told the operator a war story about someone else's run
        self.assertNotIn("129 of 225", hint)
        self.assertNotIn("batch 1", hint)


# ---------------------------------------------------------------------------
# 7. the errata guard is a property of the data, not of one transport


class TestErrataProvenanceOnTheLocalPath(unittest.TestCase):
    """`run/manifest.py` refuses to TRANSFER an unconfirmed repo-supplied errata.

    The local fleet never transfers anything, so the guard never ran -- and
    ``adjudicate.workflow.js`` makes ERRATA a verdict that discharges a finding.
    """

    def test_a_repo_supplied_errata_is_withheld_from_the_local_fleet(self):
        ws = make_workspace(self, errata_repo_supplied=True)
        rc, out = cf("run", "demo", "--local", "--workspace", str(ws["root"]))
        self.assertEqual(rc, 0, out)
        args = json.loads((ws["run_dir"] / "fleet-local.args.json").read_text())
        self.assertEqual(args["source"]["errata"], "")
        self.assertEqual(args["source"]["errata_withheld"], workflow_args.ERRATA_WITHHELD)

    def test_a_confirmed_errata_still_reaches_the_local_fleet(self):
        ws = make_workspace(self, errata_repo_supplied=False)
        rc, out = cf("run", "demo", "--local", "--workspace", str(ws["root"]), "-q")
        self.assertEqual(rc, 0, out)
        args = json.loads((ws["run_dir"] / "fleet-local.args.json").read_text())
        self.assertTrue(args["source"]["errata"].endswith("errata.md"))
        self.assertEqual(args["source"]["errata_withheld"], "")

    def test_the_two_guards_agree_on_the_same_source_directory(self):
        from run import manifest

        ws = make_workspace(self, errata_repo_supplied=True)
        sdir = ws["root"] / "sources" / PUBLISHED_ID
        self.assertTrue(workflow_args.errata_is_repo_supplied(sdir))
        self.assertTrue(manifest._errata_is_repo_supplied(sdir))
        clean = make_workspace(self, errata_repo_supplied=False)
        cdir = clean["root"] / "sources" / PUBLISHED_ID
        self.assertFalse(workflow_args.errata_is_repo_supplied(cdir))
        self.assertFalse(manifest._errata_is_repo_supplied(cdir))

    def test_the_adjudication_workflows_do_not_name_a_withheld_errata(self):
        block = workflow_args.source_block(
            _FakeCfg(make_workspace(self, errata_repo_supplied=True)["root"]), PUBLISHED_ID)
        self.assertEqual(block["errata"], "")
        self.assertEqual(block["errata_withheld"], workflow_args.ERRATA_WITHHELD)


class _FakeCfg:
    """The two attributes ``source_block`` reads, over a real workspace directory."""

    def __init__(self, root: Path):
        from cflib.workspace import Workspace

        self._ws = Workspace.load(root)
        self.paths = self._ws.paths

    def source(self, source_id: str):
        return self._ws.source(source_id)


# ---------------------------------------------------------------------------
# 8. cf run's guards, mirroring TestHarvestSafety


class TestRunSafety(unittest.TestCase):
    """`cf run --execute` copies source text to another machine and spends money.

    Its four guards had no test: only ``_concurrency``, ``_local_repos`` and
    ``_run_source`` were ever called.  The allowlist half IS covered elsewhere
    (``manifest.resolve_host`` / ``check_planned_host``), so it is not repeated.
    """

    def setUp(self):
        self.ws = make_workspace(self)
        self.root = self.ws["root"]
        # the run is planned for a real host, not for `local`
        path = self.ws["run_dir"] / "run.yaml"
        path.write_text(path.read_text(encoding="utf-8")
                        .replace("host:\n  kind: local\n  name: local\n",
                                 "host:\n  kind: remote\n  name: testbox\n"),
                        encoding="utf-8")

    def _run(self, *extra):
        return cf("run", "demo", "--host", "testbox", "--workspace", str(self.root),
                  "-q", *extra)

    def test_a_server_run_without_consent_is_refused(self):
        with self.assertRaises(CfError) as ctx:
            self._run()
        self.assertIn("consent", str(ctx.exception))

    def test_raising_concurrency_needs_a_watched_smoke_task(self):
        with self.assertRaises(CfError) as ctx:
            self._run("--i-have-consent", "--stage", "raise")
        self.assertIn("--smoke-verified", str(ctx.exception))

    def test_raise_monitor_and_stop_act_on_the_running_fleet_alone(self):
        """`--stage raise` re-ran preflight, transfer, verify and smoke (#7)."""
        from cflib.yamlio import read_yaml

        earlier = ("PREFLIGHT", "TRANSFER", "VERIFY", "SMOKE")
        for stage in ("raise", "monitor", "stop"):
            with self.subTest(stage=stage):
                rc, out = self._run_loud("--stage", stage, "--smoke-verified",
                                         "--workers", "4", "--reviewers", "3")
                self.assertEqual(rc, 0, out)
                self.assertIn(f"---- {stage.upper()} ", out)
                for other in earlier:
                    self.assertNotIn(f"---- {other} ", out)
                run = read_yaml(self.ws["run_dir"] / "run.yaml")["run"]
                self.assertEqual(run["stages_planned"], [stage])
                if stage == "raise":
                    self.assertLess(-1, out.find("tend-control budget"))
                    self.assertLess(out.find("tend-control budget"),
                                    out.find("tend-control limits"))

    def test_the_default_stage_still_runs_up_to_the_smoke_test(self):
        rc, out = self._run_loud()
        self.assertEqual(rc, 0, out)
        for stage in ("PREFLIGHT", "TRANSFER", "VERIFY", "SMOKE"):
            self.assertIn(f"---- {stage} ", out)
        self.assertNotIn("---- RAISE ", out)

    def test_force_resends_tasks_over_a_moved_entrypoint(self):
        rc, out = self._run_loud("--stage", "transfer")
        self.assertEqual(rc, 0, out)
        self.assertIn("moved past", out)
        rc, out = self._run_loud("--stage", "transfer", "--force")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("moved past", out)
        self.assertIn("rsync", out)

    def _run_loud(self, *extra):
        return cf("run", "demo", "--host", "testbox", "--workspace", str(self.root),
                  "--i-have-consent", *extra)

    def test_executing_with_a_missing_component_is_refused(self):
        with self.assertRaises(CfError) as ctx:
            self._run("--i-have-consent", "--execute")
        self.assertIn("missing components", str(ctx.exception))

    def test_a_dry_run_reaches_the_plan_without_refusing(self):
        rc, out = self._run("--i-have-consent")
        self.assertEqual(rc, 0, out)

    def test_execute_walks_the_subprocess_path_and_raises_on_failure(self):
        """`remote.execute(dry_run=False)` was only ever called with dry_run=True."""
        from run import remote

        ok = {"stage": "monitor", "title": "true", "argv": ["true"], "shell": "true"}
        bad = {"stage": "monitor", "title": "false", "argv": ["false"], "shell": "false"}
        later = {"stage": "monitor", "title": "never", "argv": ["true"], "shell": "true"}
        stream = io.StringIO()
        results = remote.execute([ok], ["monitor"], dry_run=False, stream=stream)
        self.assertEqual(results[0]["returncode"], 0)
        self.assertTrue(results[0]["ran"])
        with self.assertRaises(CfError) as ctx:
            remote.execute([bad, later], ["monitor"], dry_run=False, stream=stream)
        self.assertIn("remote step failed (1)", str(ctx.exception))
        self.assertNotIn("never", stream.getvalue())


# ---------------------------------------------------------------------------
# 9. the claude-p bridge's extractor


class TestExtractFinalJson(unittest.TestCase):
    """The four real final-message shapes, one of which is the documented one.

    ``normalize_result.py``'s docstring calls losing a good payload to one
    sentence of prose "the silent-requeue trap"; the sibling extractor
    reintroduced it by taking the first ``{`` to the last ``}``.
    """

    PAYLOAD = {"schema_version": 1, "status": "done", "summary": "wrote {3} tests"}

    def _extract(self, text: str, role: str = "worker") -> tuple[int, str, str]:
        envelope = json.dumps({"session_id": "s1", "is_error": False, "result": text})
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "run" / "extract_final_json.py"), role],
            input=envelope, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def test_the_four_real_shapes_all_yield_the_payload(self):
        body = json.dumps(self.PAYLOAD)
        cases = {
            "fenced": "Here it is:\n```json\n%s\n```" % body,
            "prose-brace": "I set {status} as required. %s" % body,
            "two-objects": "%s\n%s" % (json.dumps(
                dict(self.PAYLOAD, status="draft")), body),
            "trailing-prose": "%s\nLet me know if you want {more} detail." % body,
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                rc, out, err = self._extract(text)
                self.assertEqual(rc, 0, err)
                self.assertEqual(json.loads(out), self.PAYLOAD)

    def test_the_fenced_block_really_is_preferred(self):
        """The docstring's contract, now the implemented one.

        A fenced payload followed by a bare object -- a worker quoting the draft
        it discarded -- used to hand the orchestrator the draft, because the
        fenced candidates were tried last and so could never win.
        """
        draft = dict(self.PAYLOAD, status="draft", summary="first attempt")
        rc, out, err = self._extract(
            "```json\n%s\n```\nEarlier I had written %s, which I discarded."
            % (json.dumps(self.PAYLOAD), json.dumps(draft)))
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), self.PAYLOAD)

    def test_it_is_still_fail_closed(self):
        for text in ("I could not finish the task.",
                     json.dumps({"note": "no contract keys here"}),
                     "{not json at all}"):
            with self.subTest(text=text[:24]):
                rc, _out, err = self._extract(text)
                self.assertEqual(rc, 1)
                self.assertIn("extraction failed", err)

    def test_an_unknown_role_is_refused_before_parsing(self):
        rc, _out, err = self._extract(json.dumps(self.PAYLOAD), role="nobody")
        self.assertEqual(rc, 1)
        self.assertIn("unknown role", err)

    def test_the_session_id_is_still_persisted(self):
        tmp = scratch(self) / "session-id"
        envelope = json.dumps({"session_id": "abc123", "is_error": False,
                               "result": json.dumps(self.PAYLOAD)})
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "run" / "extract_final_json.py"),
             "worker", str(tmp)], input=envelope, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(tmp.read_text(encoding="utf-8"), "abc123")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
