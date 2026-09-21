"""Review fixes for the ten seams wave 1 left open (stdlib unittest).

Each class is one hand-off: the half of a shared finding that lived in a file the
package which found it did not own.

* H1 the ownership rule was inert, because nothing passes the gate a task id;
* H2 the prompts asked for more of a quote than the gate allows, in numbers that
  could drift away from the gate's own;
* H3 the item-page map reached the gate from no caller, and the two callers that
  held a page held the PRINTED one;
* H4 two recorded figures and one spec passage described an older gate;
* H5 the gate's recompute-and-compare of ``finding_id`` never ran on a fleet;
* H6 ``gate_corpus()`` passed an empty directory, and only its caller refused it;
* H7 the v1 ledger migration hashed a matching form of a declaration;
* H8 nothing could stage the sandbox-bypass opt-in ``run.yaml`` records;
* H9 ``cf run`` launched a tend root that names another model than the record;
* H10 ``finding_id``'s docstring claimed a determinism it does not have.

The synthetic book below is a public-domain paraphrase: no source text lives in
this repo (CLAUDE.md S1).  Every test here runs without the private baselines.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))

from cflib import hashing  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from gate import verify_findings as vf  # noqa: E402
from harvest import gate_corpus as gate_mod  # noqa: E402
from ledger import migrate  # noqa: E402
from plan import make_tend_root as mtr  # noqa: E402
from plan import models as models_mod  # noqa: E402
from plan import presets as presets_mod  # noqa: E402

TMPDIR = os.environ.get("CF_TMPDIR") or None
SOURCE_ID = "a-book-2nd-edition"
SOURCE_SHA = "c" * 64
#: pdf_page = printed page + OFFSET, the shape of a real source's pagination
OFFSET = 16
#: the item is stated on this page OF THE PINNED TEXT; its printed page is 2
ITEM_PDF_PAGE = 18
ITEM = "Exercise 2.3.4"

_STATEMENT = (
    "Exercise 2.3.4 (Monotonicity of the outer measure). Show that a subset of "
    "an elementary set has no larger outer measure than the set itself."
)
_DECOY = (
    "We record here, for use in the next section, that a finite union of "
    "intervals is elementary and that its measure is additive."
)


def _book() -> str:
    """Twenty pages of prose: the item on page 18, a decoy sentence on page 2."""
    pages = []
    for n in range(1, 21):
        if n == ITEM_PDF_PAGE:
            body = _STATEMENT
        elif n == 2:
            body = _DECOY
        else:
            body = ("Ordinary prose carrying the argument of paragraph %d forward "
                    "without stating any numbered item." % n)
        pages.append(f"2.3. Outer measures\n\n{body}\n")
    return "".join(p + "\f" for p in pages)


BOOK = _book()


def gate_cfg(preset_name: str, book: pathlib.Path, *, source=None, **over) -> vf.GateConfig:
    data = presets_mod.gate_config(
        presets_mod.get(preset_name),
        run_id=over.pop("run_id", "test-run"),
        sources={SOURCE_ID: {"text": str(book), "sha256": SOURCE_SHA,
                             "page_offset": OFFSET, **(source or {})}},
        repos=over.pop("repos", ["ours"]),
    )
    data.update(over)
    return vf.GateConfig(data)


def doc_for(quote: str, *, item: str = ITEM, **over) -> dict:
    doc = {
        "schema_version": 2,
        "run_id": "test-run",
        "source_id": SOURCE_ID,
        "source_sha": SOURCE_SHA,
        "item": item,
        "book_located": True,
        "book_passage_quote": quote,
        "matches": [],
        "disagreements": [
            {
                "concept": "monotonicity",
                "kind": "CONTENT",
                "repos_involved": ["ours"],
                "faithful": [],
                "unfaithful_decls": ["ours:mono"],
                "book_quote": quote,
                "witness": {"object": "E = [0,1), F = [0,2)", "why": "the bound is reversed"},
                "reasoning": "the companion states the reverse inequality",
                "severity": "high",
            }
        ],
        "coverage_notes": "",
    }
    doc.update(over)
    return doc


def scratch(case: unittest.TestCase) -> pathlib.Path:
    tmp = tempfile.TemporaryDirectory(dir=TMPDIR)
    case.addCleanup(tmp.cleanup)
    return pathlib.Path(tmp.name)


# --------------------------------------------------------------------------- #
# H1 gates-correctness#7 — the single-stem rule stands in for a missing task id
# --------------------------------------------------------------------------- #
class WorktreeCase(unittest.TestCase):
    """A git worktree with one worker's landed pair already on ``main``."""

    def setUp(self):
        self.root = scratch(self)
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        self.git("init", "-q", "-b", "main")
        (self.root / "findings").mkdir()
        (self.root / "tasks").mkdir()
        (self.root / "findings" / "alice.json").write_text(
            json.dumps({"schema_version": 2, "disagreements": []}), encoding="utf-8")
        (self.root / "tasks" / "alice.yaml").write_text("id: alice\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "alice lands")
        self.git("checkout", "-q", "-b", "carol")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, env=self.env,
                       capture_output=True, text=True)

    def entries(self):
        got, errs = vf.changed_entries(self.root, "main")
        self.assertEqual(errs, [])
        return got

    def write_pair(self, stem: str):
        (self.root / "findings" / f"{stem}.json").write_text("{}\n", encoding="utf-8")
        (self.root / "tasks" / f"{stem}.yaml").write_text(f"id: {stem}\n", encoding="utf-8")


class TestSingleStemRuleWithoutATaskId(WorktreeCase):
    """Nothing passes ``--task-id``, so the ownership rule never ran on a run.

    The tend config's argv list is not shell-expanded, so a literal
    ``$TEND_TASK_ID`` element would make the gate refuse every contribution, and
    whether tend exports the variable into a validation command's environment has
    never been verified.  The single-stem count is the half that works with no id
    at all: before it, rewriting a neighbour's findings file beside your own
    passed the changed-files check with no task id and no complaint.
    """

    def test_a_second_task_in_the_change_set_is_refused_with_no_task_id(self):
        (self.root / "findings" / "alice.json").write_text(
            '{"disagreements": []}\n', encoding="utf-8")
        self.write_pair("carol")
        self.git("add", "-A")
        self.git("commit", "-qm", "tidy up")
        problems = vf.check_changed(self.entries())
        self.assertTrue(any("belong to 2 tasks" in p for p in problems), problems)
        named = [p for p in problems if "belong to 2 tasks" in p][0]
        self.assertIn("alice", named)
        self.assertIn("carol", named)
        self.assertIn("--task-id", named)

    def test_one_stem_still_passes_with_no_task_id(self):
        self.write_pair("carol")
        self.git("add", "-A")
        self.git("commit", "-qm", "mine")
        self.assertEqual(vf.check_changed(self.entries()), [])

    def test_the_task_id_still_wins_when_there_is_one(self):
        """With an id the stem count is irrelevant: ownership decides."""
        self.write_pair("carol")
        self.git("add", "-A")
        self.git("commit", "-qm", "mine")
        problems = vf.check_changed(self.entries(), task_id="dave")
        self.assertTrue(any("not to this worker's 'dave'" in p for p in problems), problems)
        self.assertFalse([p for p in problems if "belong to" in p],
                         "the single-stem count must not fire when an id is known")

    def test_both_gate_entry_points_say_which_rule_judged_the_contribution(self):
        self.assertIn("single-stem rule", vf.ownership_line(""))
        self.assertIn("--task-id", vf.ownership_line(""))
        self.assertIn("TEND_TASK_ID", vf.ownership_line(""))
        self.assertEqual(vf.ownership_line("carol"), "ownership checked against task carol")
        # check_changed_files.py loads the gate as a top-level module (it ships
        # beside it, on a host with no cflib), so this is equality of wording and
        # not of object identity -- one sentence, printed by both entry points.
        from gate import check_changed_files as ccf

        for task_id in ("", "carol"):
            self.assertEqual(ccf.ownership_line(task_id), vf.ownership_line(task_id))

    def test_the_standalone_check_exits_one_on_two_stems(self):
        """End to end, through the script a tend config may call."""
        (self.root / "findings" / "alice.json").write_text(
            '{"disagreements": []}\n', encoding="utf-8")
        self.write_pair("carol")
        self.git("add", "-A")
        self.git("commit", "-qm", "two stems")
        script = str(SKILL / "scripts" / "gate" / "check_changed_files.py")
        argv = [sys.executable, script, "--base", "main", "--root", str(self.root)]
        bare = subprocess.run(argv, capture_output=True, text=True,
                              env={**self.env, "TEND_TASK_ID": ""})
        self.assertEqual(bare.returncode, 1, bare.stdout + bare.stderr)
        self.assertIn("belong to 2 tasks", bare.stderr)
        named = subprocess.run(argv + ["--task-id", "carol"], capture_output=True, text=True,
                               env={**self.env, "TEND_TASK_ID": ""})
        self.assertEqual(named.returncode, 1, "alice's file is still not carol's")
        self.assertIn("not to this worker's", named.stderr)

    def test_the_standalone_check_names_its_rule_when_it_passes(self):
        self.write_pair("carol")
        self.git("add", "-A")
        self.git("commit", "-qm", "mine")
        script = str(SKILL / "scripts" / "gate" / "check_changed_files.py")
        argv = [sys.executable, script, "--base", "main", "--root", str(self.root)]
        out = subprocess.run(argv, capture_output=True, text=True,
                             env={**self.env, "TEND_TASK_ID": ""})
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("single-stem rule", out.stdout)


class TestABatchedPreMergeSetIsNotOneContribution(WorktreeCase):
    """The stem count also runs on a change set that is not one contribution.

    ``templates/tend/config.yaml.tmpl`` gives ``validation_commands`` and
    ``pre_merge_validation_commands`` the same argv and ships ``batched_merge:
    true``, so the pre-merge change set against ``merge_target_branch`` carries one
    findings/task pair PER BATCHED TASK and no task id.  A bare stem count refuses
    that whole batch, and no flag relaxes it; the 1/1 smoke test cannot show it,
    because at limits 1/1 there is nothing to batch.  So the count asks whether any
    touched path was already on the base branch, which is the case the rule is for.
    """

    def batch(self, *stems: str) -> dict:
        for stem in stems:
            self.write_pair(stem)
        self.git("add", "-A")
        self.git("commit", "-qm", "batched merge")
        return self.entries()

    def test_a_batch_of_new_pairs_passes_with_no_task_id(self):
        entries = self.batch("carol", "dave", "erin")
        self.assertEqual(sorted(set(entries.values())), ["A"])
        self.assertEqual(vf.check_changed(entries), [])

    def test_a_rewritten_neighbour_inside_a_batch_is_still_refused_by_name(self):
        (self.root / "findings" / "alice.json").write_text(
            '{"disagreements": []}\n', encoding="utf-8")
        entries = self.batch("carol", "dave")
        problems = vf.check_changed(entries)
        named = [p for p in problems if "belong to 3 tasks" in p]
        self.assertTrue(named, problems)
        self.assertIn("findings/alice.json", named[0])
        self.assertIn("--task-id", named[0])

    def test_a_deleted_neighbour_inside_a_batch_is_still_refused(self):
        self.write_pair("carol")
        self.write_pair("dave")
        self.git("rm", "-q", "findings/alice.json", "tasks/alice.yaml")
        self.git("add", "-A")
        self.git("commit", "-qm", "batched merge, alice dropped")
        problems = vf.check_changed(self.entries())
        self.assertTrue(any("deleted" in p for p in problems), problems)
        self.assertTrue(any("belong to 3 tasks" in p for p in problems), problems)

    def test_a_caller_with_no_statuses_keeps_the_strict_count(self):
        """``changed_paths`` throws the statuses away, so it cannot tell a new
        pair from a rewritten one and must stay strict."""
        problems = vf.check_changed({"findings/carol.json", "tasks/carol.yaml",
                                     "findings/dave.json", "tasks/dave.yaml"})
        self.assertTrue(any("belong to 2 tasks" in p for p in problems), problems)

    def test_the_two_documents_an_operator_reads_say_so(self):
        readme = (SKILL / "templates" / "tend" / "README.md.tmpl").read_text(encoding="utf-8")
        runbook = (SKILL / "references" / "runbook-server.md").read_text(encoding="utf-8")
        for text in (readme, runbook):
            self.assertIn("batched_merge", text)
            self.assertIn("per batched task", text)


class TestTheUnverifiedExportIsWrittenDown(unittest.TestCase):
    """(c) and (d) of the hand-off: say plainly that the export is unverified."""

    def test_the_tend_root_readme_tells_the_operator_what_to_read(self):
        text = (SKILL / "templates" / "tend" / "README.md.tmpl").read_text(encoding="utf-8")
        self.assertIn("TEND_TASK_ID", text)
        self.assertIn("unverified", text)
        self.assertIn("single-stem rule", text)

    def test_the_runbook_makes_it_a_step_of_the_smoke_test(self):
        text = (SKILL / "references" / "runbook-server.md").read_text(encoding="utf-8")
        self.assertIn("changed files:", text)
        self.assertIn("never been verified here", text)
        # and it says why the obvious fix does not work
        self.assertIn("not shell-expanded", text)

    def test_no_argv_list_carries_a_literal_task_id_variable(self):
        """An argv element is not shell-expanded: the gate would see the text."""
        for name in ("config.yaml.tmpl", "README.md.tmpl"):
            text = (SKILL / "templates" / "tend" / name).read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.lstrip().startswith("- argv:") or "verify_findings.py" in line:
                    self.assertNotIn("$TEND_TASK_ID", line, f"{name}: {line}")


# --------------------------------------------------------------------------- #
# H2 prompts-workflows#9 — the prompts' quote bound is the gate's
# --------------------------------------------------------------------------- #
class TestQuoteLengthNumbersCannotDrift(unittest.TestCase):
    """The prompts asked for more than the gate allows, in free text.

    ``worker-system.md.tmpl`` said "one or two sentences at most" with no bound
    and the local fleet's schema said "1-3 sentences", while the gate records an
    over-long quote at ``LONG_QUOTE_CHARS`` and refuses at ``MAX_QUOTE_CHARS``.
    The renderer cannot interpolate gate.json here (the two keys never travel into
    a prompt context), so the numbers are literals -- and these assertions are
    what stops them drifting away from the gate's own.
    """

    #: every shipped file that tells an agent how much to quote
    FILES = ("templates/prompts/worker-system.md.tmpl",
             "templates/prompts/reviewer-system.md.tmpl",
             "workflows/fleet-local.workflow.js")

    def text(self, rel: str) -> str:
        return (SKILL / rel).read_text(encoding="utf-8")

    def test_every_file_that_states_a_bound_states_the_gates(self):
        self.assertEqual(vf.LONG_QUOTE_CHARS, 600)
        self.assertEqual(vf.MAX_QUOTE_CHARS, 3000)
        for rel in self.FILES:
            body = self.text(rel)
            self.assertIn(f"at most {vf.LONG_QUOTE_CHARS} characters", body, rel)

    def test_the_refusal_ceiling_is_named_where_the_worker_reads_it(self):
        for rel in ("templates/prompts/worker-system.md.tmpl",
                    "templates/prompts/reviewer-system.md.tmpl",
                    "workflows/fleet-local.workflow.js"):
            body = self.text(rel).replace("3,000", str(vf.MAX_QUOTE_CHARS))
            self.assertIn(f"refuses a quote over {vf.MAX_QUOTE_CHARS}", body, rel)

    def test_nothing_asks_for_more_sentences_than_the_rule_allows(self):
        for rel in list(self.FILES) + ["templates/prompts/worker.md.tmpl",
                                       "templates/prompts/reviewer.md.tmpl"]:
            body = self.text(rel)
            for asked in ("1-3 sentences", "1-4 sentences", "three sentences"):
                self.assertNotIn(asked, body, rel)

    def test_a_quote_of_the_permitted_length_is_not_refused(self):
        """The bound the prompts print is one the gate really accepts."""
        root = scratch(self)
        book = root / "text.txt"
        long_passage = " ".join(f"Sentence number {n} of the permitted span." for n in range(1, 15))
        book.write_text(f"2.3. Outer measures\n\n{long_passage}\n\f", encoding="utf-8")
        self.assertGreater(len(long_passage), vf.LONG_QUOTE_CHARS // 2)
        self.assertLessEqual(len(long_passage), vf.LONG_QUOTE_CHARS)
        cfg = gate_cfg("attack-vac", book, source={"item_pages": {ITEM: 1}})
        books, errs = vf.load_sources(cfg)
        self.assertEqual(errs, [])
        self.assertEqual(vf.validate_doc(doc_for(long_passage), "x.json", cfg, books), [])
        notes = vf.binding_notes(doc_for(long_passage), "x.json", cfg, books[SOURCE_ID])
        self.assertEqual([n for n in notes if "over-long" in n], [])


# --------------------------------------------------------------------------- #
# H3 gates-correctness#2 — the item-page map, from the callers that hold it
# --------------------------------------------------------------------------- #
class TestItemPagesHoldThePinnedPage(unittest.TestCase):
    """The map is ``{item: pdf_page}``; the printed page would refuse real rows.

    ``prepare_source`` writes both (``page = pdf_page - offset``) and both
    intended callers held the printed one, so wiring the map with the obvious
    field would have placed every item of an offset source that many pages from
    its own quotes.
    """

    INDEX = {
        ITEM: {"item": ITEM, "pdf_page": ITEM_PDF_PAGE, "page": ITEM_PDF_PAGE - OFFSET},
        "Lemma 2.3.5": {"item": "Lemma 2.3.5", "pdf_page": 19, "page": 3},
        "In-text p.4": {"item": "In-text p.4", "page": 4},          # no pinned page
    }

    def test_item_pages_reads_pdf_page_and_drops_a_row_without_one(self):
        got = presets_mod.item_pages(self.INDEX)
        self.assertEqual(got, {ITEM: ITEM_PDF_PAGE, "Lemma 2.3.5": 19})
        self.assertNotIn(ITEM_PDF_PAGE - OFFSET, got.values())
        self.assertEqual(presets_mod.item_pages(None), {})

    def test_the_block_the_gate_reads_carries_it(self):
        root = scratch(self)
        book = root / "text.txt"
        book.write_text(BOOK, encoding="utf-8")
        cfg = gate_cfg("attack-vac", book,
                       source={"item_pages": presets_mod.item_pages(self.INDEX)})
        self.assertEqual(cfg.source(SOURCE_ID)["item_pages"][ITEM], ITEM_PDF_PAGE)

    def test_plan_run_and_harvest_read_the_sources_own_items_json(self):
        """Both build the map from items.json, and both take ``pdf_page``."""
        from cfcmd import harvest as harvest_cmd
        from cfcmd import plan_run

        root = scratch(self)
        sdir = root / "sources" / SOURCE_ID
        sdir.mkdir(parents=True)
        (sdir / "items.json").write_text(json.dumps({
            "source_id": SOURCE_ID, "source_sha": SOURCE_SHA, "offset": OFFSET,
            "items": list(self.INDEX.values()),
        }), encoding="utf-8")

        class Paths:
            def source_items(self, source_id):
                return root / "sources" / source_id / "items.json"

        class Ws:
            paths = Paths()

        want = {ITEM: ITEM_PDF_PAGE, "Lemma 2.3.5": 19}
        self.assertEqual(plan_run._item_pages(Paths(), SOURCE_ID), want)
        self.assertEqual(harvest_cmd._source_item_pages(Ws(), SOURCE_ID), want)
        # a source this workspace never prepared falls back to the heading search
        self.assertEqual(plan_run._item_pages(Paths(), "no-such-source"), {})
        self.assertEqual(harvest_cmd._source_item_pages(Ws(), "no-such-source"), {})

    def test_no_caller_fills_the_key_from_a_printed_page(self):
        """The one mistake that would refuse every finding of an offset source."""
        for rel in ("scripts/cfcmd/plan_run.py", "scripts/cfcmd/pair.py",
                    "scripts/cfcmd/harvest.py"):
            body = (SKILL / rel).read_text(encoding="utf-8")
            lines = [ln for ln in body.splitlines()
                     if '"item_pages"' in ln and not ln.lstrip().startswith("#")]
            self.assertTrue(lines, f"{rel} passes no item_pages to its gate source block")
            for line in lines:
                self.assertNotIn('["page"]', line, rel)
                self.assertNotIn('get("page")', line, rel)


class TestAQuoteFarFromItsItemIsRefused(unittest.TestCase):
    """The bind is on for every runnable preset, and now it has the good route."""

    def setUp(self):
        self.root = scratch(self)
        self.book = self.root / "text.txt"
        self.book.write_text(BOOK, encoding="utf-8")

    def check(self, quote: str, pages) -> list[str]:
        cfg = gate_cfg("attack-vac", self.book, source={"item_pages": {ITEM: pages}})
        books, errs = vf.load_sources(cfg)
        self.assertEqual(errs, [])
        return vf.validate_doc(doc_for(quote), "x.json", cfg, books)

    def test_the_items_own_passage_passes(self):
        self.assertEqual(self.check(_STATEMENT, ITEM_PDF_PAGE), [])

    def test_a_quote_sixteen_pages_away_is_refused(self):
        errs = self.check(_DECOY, ITEM_PDF_PAGE)
        self.assertTrue(errs, "a quote from another section passed as the item's passage")
        self.assertTrue(any("16 pages from" in e for e in errs), errs)
        self.assertTrue(any("gate.json item_pages" in e for e in errs), errs)

    def test_the_printed_page_in_the_map_would_refuse_the_real_passage(self):
        """Why the key holds pdf_page: the mistake is a mass FALSE refusal."""
        errs = self.check(_STATEMENT, ITEM_PDF_PAGE - OFFSET)
        self.assertTrue(any("16 pages from" in e for e in errs), errs)

    def test_bind_item_page_is_on_for_every_runnable_preset(self):
        for name in presets_mod.RUNNABLE:
            data = presets_mod.gate_config(
                presets_mod.get(name), run_id="r",
                sources={SOURCE_ID: {"text": str(self.book), "sha256": SOURCE_SHA}},
                repos=["ours"])
            self.assertTrue(data["bind_item_page"], name)


# --------------------------------------------------------------------------- #
# H4 — the figures and the spec describe this gate
# --------------------------------------------------------------------------- #
class TestRecordedFiguresAndTheSpec(unittest.TestCase):
    def test_the_operator_field_comment_carries_the_anchored_figure(self):
        """2 was the old independent window search; the anchored pass fails 5."""
        body = (SKILL / "scripts" / "plan" / "presets.py").read_text(encoding="utf-8")
        comment = body.split("check_operators: bool")[0].rsplit("#: run the gate's", 1)[-1]
        self.assertIn("5 of its 3,019 quotes", comment)
        self.assertNotIn("two of its 3,019 quotes", comment)

    def test_the_spec_lists_the_gate_source_block_this_code_writes(self):
        # docs/design/stage_map.md is the current spec; the 1,941-line design
        # record this used to read is archived and no longer normative.
        spec = (SKILL / "docs" / "design" / "stage_map.md").read_text(encoding="utf-8")
        keys = set(presets_mod.source_block("x"))
        self.assertIn("item_pages", keys)
        for passage in ("page_offset, images_dir, item_pages", '"item_pages": {item: pdf_page}'):
            self.assertIn(passage, spec)
        self.assertIn("max_match_pages", spec)
        # and the sentence that stops the printed-page mistake being re-made
        self.assertIn("never its printed `page`", spec)

    def test_the_runbook_prints_the_same_block(self):
        """An operator reading the runbook must not learn a five-key block the
        spec and the code have since grown out of."""
        runbook = (SKILL / "references" / "runbook-server.md").read_text(encoding="utf-8")
        self.assertIn("page_offset, images_dir, item_pages", runbook)


# --------------------------------------------------------------------------- #
# H5 gates-correctness#4 — a written finding_id is always the right one
# --------------------------------------------------------------------------- #
class TestFindingIdIsCheckedWhenItIsWritten(unittest.TestCase):
    """``require_finding_id`` is off for every runnable preset, so the recompute
    never ran on a fleet -- and a run that stamped ``"finding_id": "00"*32`` on
    every row had it stored verbatim.

    The robust half of the hand-off: no worker is asked to compute a sha256 (no
    prompt teaches it and no helper ships beside the gate), but an id that IS
    present is recomputed and compared on every preset.
    """

    def setUp(self):
        self.root = scratch(self)
        self.book = self.root / "text.txt"
        self.book.write_text(BOOK, encoding="utf-8")
        self.cfg = gate_cfg("attack-vac", self.book,
                            source={"item_pages": {ITEM: ITEM_PDF_PAGE}})
        self.books, errs = vf.load_sources(self.cfg)
        self.assertEqual(errs, [])

    def doc(self, fid=None) -> dict:
        doc = doc_for(_STATEMENT)
        if fid is not None:
            doc["disagreements"][0]["finding_id"] = fid
        return doc

    def errs(self, doc, cfg=None):
        return vf.validate_doc(doc, "x.json", cfg or self.cfg, self.books)

    def real_id(self) -> str:
        return vf.finding_id("test-run", SOURCE_ID, ITEM, "CONTENT", ["ours:mono"])

    def test_no_runnable_preset_demands_the_field(self):
        for name in presets_mod.RUNNABLE:
            self.assertFalse(presets_mod.get(name).require_finding_id, name)
        self.assertTrue(presets_mod.get("mechanical").require_finding_id)

    def test_a_row_with_no_id_still_passes_a_runnable_preset(self):
        self.assertEqual(self.errs(self.doc()), [])

    def test_a_wrong_id_is_refused_although_the_preset_does_not_require_one(self):
        errs = self.errs(self.doc("00" * 32))
        self.assertTrue(errs, "a stamped-wrong finding_id passed the fleet's own gate")
        self.assertTrue(any("finding_id" in e and "!= computed" in e for e in errs), errs)

    def test_the_right_id_passes(self):
        self.assertEqual(self.errs(self.doc(self.real_id())), [])

    def test_a_non_string_id_is_refused_and_does_not_crash_the_gate(self):
        """Reading the field on every preset makes its TYPE the worker's too.

        ``got[:12]`` raised ``TypeError`` on a JSON number or boolean and
        ``KeyError`` on an object, out of ``cf harvest`` (``gate_corpus`` calls
        ``validate_doc`` in a bare loop), out of ``cf pair``'s mechanical
        validation, and out of the tend validation command as a traceback where
        the repo's rule is a named refusal.
        """
        for bad in (12345, True, 3.5, {"x": 1}, ["a" * 64]):
            with self.subTest(bad=bad):
                errs = self.errs(self.doc(bad))
                self.assertTrue(
                    any("finding_id must be the 64-character hex string" in e for e in errs),
                    errs)
                self.assertTrue(any(repr(bad) in e for e in errs), errs)

    def test_a_required_and_missing_id_names_the_expected_one(self):
        """There is no other way to learn it from inside a worktree."""
        cfg = gate_cfg("attack-vac", self.book, source={"item_pages": {ITEM: ITEM_PDF_PAGE}},
                       require_finding_id=True)
        errs = self.errs(self.doc(), cfg)
        self.assertTrue(any("missing finding_id" in e for e in errs), errs)
        self.assertTrue(any(self.real_id() in e for e in errs), errs)

    def test_the_presets_notes_record_the_choice(self):
        notes = (SKILL / "templates" / "PRESETS.md").read_text(encoding="utf-8")
        self.assertIn("require_finding_id", notes)
        self.assertIn("finding_id_stored", notes)
        self.assertIn("present-but-wrong", notes)

    def test_the_notes_own_the_wider_scope_they_shipped(self):
        """The refusal has no gate.json key to switch it off, so it also judges
        the two import-only presets.  ``harvest.tally.computed_finding_id`` names
        the recompute as the remedy for a corpus of known-wrong stored ids; if
        PRESETS.md left the scope implicit the two documents would read as
        offering different remedies for the same historical failure.
        """
        notes = (SKILL / "templates" / "PRESETS.md").read_text(encoding="utf-8")
        self.assertIn("*-legacy", notes)
        self.assertIn("cf harvest --no-gate", notes)
        for name in ("differential-legacy", "attack-legacy"):
            self.assertFalse(presets_mod.get(name).require_finding_id, name)


# --------------------------------------------------------------------------- #
# H6 — an empty corpus is a gate failure where the function is
# --------------------------------------------------------------------------- #
class TestEmptyCorpusFailsInTheFunction(unittest.TestCase):
    """The rule lived in ``cf harvest``'s wrapper, so every other caller -- the
    tests among them -- inherited ``ok: True`` on nothing."""

    def setUp(self):
        self.root = scratch(self)
        self.book = self.root / "text.txt"
        self.book.write_text(BOOK, encoding="utf-8")
        self.cfg_path = self.root / "gate.local.json"
        self.cfg_path.write_text(json.dumps(
            gate_cfg("attack-vac", self.book,
                     source={"item_pages": {ITEM: ITEM_PDF_PAGE}}).data,
            default=str), encoding="utf-8")

    def test_an_empty_directory_is_not_a_pass(self):
        empty = self.root / "corpus"
        empty.mkdir()
        result = gate_mod.gate_corpus(empty, self.cfg_path)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["files"], 0)
        self.assertTrue(any("empty corpus is not a clean one" in p
                            for p in result["problems"]), result["problems"])

    def test_a_corpus_with_a_valid_finding_still_passes(self):
        dest = self.root / "corpus2"
        dest.mkdir()
        (dest / "exercise-2_3_4.json").write_text(
            json.dumps(doc_for(_STATEMENT)), encoding="utf-8")
        result = gate_mod.gate_corpus(dest, self.cfg_path)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["files"], 1)

    def test_the_render_says_it_failed(self):
        empty = self.root / "corpus3"
        empty.mkdir()
        self.assertIn("FAILED", gate_mod.render(gate_mod.gate_corpus(empty, self.cfg_path)))


# --------------------------------------------------------------------------- #
# H7 — the v1 ledger migration hashes the declaration as written
# --------------------------------------------------------------------------- #
class TestMigrateHashesTheDeclarationAsWritten(unittest.TestCase):
    """``normalise_decl`` strips INTERIOR whitespace because it is a matching
    form; the corpus, ``convert_v1``, the gate and ``harvest.tally`` all hash each
    declaration with nothing but ``.strip()``.  A v1 row that matched no finding
    therefore got an id no reader of the corpus could ever compute -- the same
    defect ``harvest`` fixed on its side of the pipeline.
    """

    DECL = "teorth:example «Exercise 1.2.1 (countable union)» at Foo.lean:12"
    ROW = {"item": "Exercise  1.2.1", "kind": "CONTENT", "verdict": "REFUTED",
           "severity": "low"}

    def row(self) -> dict:
        return dict(self.ROW, unfaithful_decls=[self.DECL])

    def out(self) -> dict:
        return migrate.map_v1_row(self.row(), repo="teorth", source_id=SOURCE_ID,
                                  run_id="batch1", findings=[])

    def test_the_id_is_the_one_the_corpus_hashes(self):
        want = hashing.finding_id("batch1", SOURCE_ID, "Exercise 1.2.1", "CONTENT", [self.DECL])
        self.assertEqual(self.out()["finding_id"], want)

    def test_it_is_not_the_id_of_the_matching_form(self):
        from ledger import compat

        squashed = compat.normalise_decl(self.DECL)
        self.assertNotEqual(squashed, self.DECL, "the fixture must carry interior whitespace")
        wrong = hashing.finding_id("batch1", SOURCE_ID, "Exercise 1.2.1", "CONTENT", [squashed])
        self.assertNotEqual(self.out()["finding_id"], wrong)

    def test_the_row_still_carries_the_matching_form(self):
        """Routing and matching compare on it: only the hash reads the label."""
        from ledger import compat

        self.assertEqual(self.out()["unfaithful_decls"],
                         [compat.normalise_decl(self.DECL)])

    def test_a_bare_declaration_is_prefixed_in_both_forms(self):
        row = dict(self.ROW, decls=["some  helper"])
        out = migrate.map_v1_row(row, repo="adam", source_id=SOURCE_ID, run_id="batch1",
                                 findings=[])
        self.assertEqual(out["unfaithful_decls"], ["adam:somehelper"])
        want = hashing.finding_id("batch1", SOURCE_ID, "Exercise 1.2.1", "CONTENT",
                                  ["adam:some  helper"])
        self.assertEqual(out["finding_id"], want)

    def test_a_matched_finding_still_wins_over_the_recompute(self):
        finding = {"finding_id": "f" * 64, "item": "Exercise 1.2.1", "kind": "CONTENT",
                   "source_id": SOURCE_ID, "unfaithful_decls": [self.DECL]}
        out = migrate.map_v1_row(self.row(), repo="teorth", source_id=SOURCE_ID,
                                 run_id="batch1", findings=[finding])
        self.assertEqual(out["finding_id"], "f" * 64)

    def test_the_module_names_no_machine_local_path(self):
        """Its docstring cited a home directory, which the hygiene rule forbids."""
        body = (SKILL / "scripts" / "ledger" / "migrate.py").read_text(encoding="utf-8")
        # the needles are BUILT rather than written out, so a tree-wide sweep for
        # them does not count this assertion as an occurrence
        for needle in ("/" + "Users/", "~/Desk" + "top"):
            self.assertNotIn(needle, body, needle)


# --------------------------------------------------------------------------- #
# H8 security-safety#3 — something can stage the bypass opt-in
# --------------------------------------------------------------------------- #
class TestSandboxBypassCanBeStaged(unittest.TestCase):
    """``run.yaml`` recorded whether the staged wrapper asks for the bypass, and
    nothing could make it ask: the opt-in existed only as a variable an operator
    exported by hand on the host, which the record cannot see."""

    def build(self, **over) -> pathlib.Path:
        root = scratch(self)
        plan = mtr.TendRootPlan(
            local=root / "tend-root", tend_root="/srv/r1-tend", entrypoint="/srv/r1",
            gate_dir="/srv/r1-gate", prompts_dir="/srv/r1-tend/prompts")
        mtr.make_tend_root(plan, presets_mod.get("attack-hunt"), run_id="r1",
                           gate_config={"sources": {SOURCE_ID: {}}}, **over)
        return root

    def wrapper(self, root: pathlib.Path) -> str:
        return (root / "tend-root" / "bin" / "worker-agent.sh").read_text(encoding="utf-8")

    def test_the_default_wrapper_does_not_ask_for_it(self):
        self.assertNotIn("CF_ALLOW_SANDBOX_BYPASS", self.wrapper(self.build()))

    def test_the_flag_stages_it_into_every_wrapper(self):
        root = self.build(allow_sandbox_bypass=True)
        for role in ("worker", "reviewer"):
            body = (root / "tend-root" / "bin" / f"{role}-agent.sh").read_text(encoding="utf-8")
            self.assertIn("export CF_ALLOW_SANDBOX_BYPASS=1", body, role)

    def test_the_record_run_yaml_carries_becomes_true(self):
        from run import manifest

        facts = manifest.agent_permissions(self.build(allow_sandbox_bypass=True))
        self.assertTrue(facts["permission_bypass_in_wrapper"])
        self.assertIn("--allow-sandbox-bypass", facts["permission_bypass"])
        off = manifest.agent_permissions(self.build())
        self.assertFalse(off["permission_bypass_in_wrapper"])
        self.assertIn("not requested in the staged wrapper", off["permission_bypass"])
        # the hint names a command that stages it, not a variable to export by hand
        self.assertIn("cf plan-run --allow-sandbox-bypass", off["default"])

    def test_plan_run_has_the_flag_and_defaults_it_off(self):
        import argparse

        from cfcmd import plan_run

        parser = argparse.ArgumentParser()
        plan_run.register(parser.add_subparsers(dest="cmd"))
        base = ["plan-run", "--preset", "attack-hunt"]
        self.assertFalse(parser.parse_args(base).allow_sandbox_bypass)
        self.assertTrue(parser.parse_args(base + ["--allow-sandbox-bypass"])
                        .allow_sandbox_bypass)

    def test_the_bridge_header_no_longer_says_nothing_stages_it(self):
        body = (SKILL / "scripts" / "run" / "bridge-agentbox.sh").read_text(encoding="utf-8")
        self.assertNotIn("nothing stages it today", body)
        self.assertIn("--allow-sandbox-bypass", body)

    def test_the_record_describes_two_states_and_not_three(self):
        """The rewritten docstring asserted a THREE-state record in its first
        sentence and named two states in its last; the dict backs the two."""
        from run import manifest

        doc = " ".join((manifest.agent_permissions.__doc__ or "").split())
        self.assertNotIn("THREE-state", doc)
        self.assertIn("two states this can distinguish", doc)
        facts = manifest.agent_permissions(self.build())
        self.assertIsInstance(facts["permission_bypass_in_wrapper"], bool)


class TestTheRunPlanNamesTheBypass(unittest.TestCase):
    """The bypass is asked for at PLAN time; `cf run` is a later command.

    ``run.yaml`` and the manifest's ``permission_bypass*`` fields recorded it, but
    the printed plan named tasks, budget, disk, transfer and warnings and nothing
    about agent permissions -- so an operator driving ``cf run --execute`` against a
    root planned days earlier by someone else never saw that the agents will run
    with approvals and the sandbox off.  The only warning was the one `cf plan-run`
    printed at plan time, into a terminal that is long gone.
    """

    HOST = "examplebox"

    def workspace(self, *, bypass: bool) -> pathlib.Path:
        import hashlib

        root = scratch(self)
        repo = root / "repos" / "ours"
        repo.mkdir(parents=True)
        sha = hashlib.sha256(BOOK.encode("utf-8")).hexdigest()
        (root / "workspace.yaml").write_text(
            "version: 2\n"
            "workspace:\n  name: demo\n"
            "companions:\n"
            "  ours:\n"
            f"    repo: {repo}\n"
            "    adapter: docstring_tags\n"
            f"    sources: [{SOURCE_ID}]\n"
            f"    anchor_source: {SOURCE_ID}\n"
            "sources:\n"
            f"  {SOURCE_ID}:\n"
            f'    sha256: "{sha}"\n'
            "    title: A Book\n"
            '    edition: "2nd edition"\n'
            f"    offset: {OFFSET}\n"
            "    role: audited\n"
            f"hosts:\n  allowlist: [{self.HOST}]\n  remote_root: /srv/audit\n"
            f"  {self.HOST}:\n    ssh: operator@{self.HOST}\n"
            "budget:\n  max_cost_usd: 25\n", encoding="utf-8")
        sdir = root / "sources" / SOURCE_ID
        sdir.mkdir(parents=True)
        (sdir / "text.txt").write_text(BOOK, encoding="utf-8")

        preset = presets_mod.get("attack-hunt")
        run_dir = root / "runs" / "demo"
        (run_dir / "tasks").mkdir(parents=True)
        (run_dir / "tasks" / "t1.yaml").write_text(
            "schema_version: 1\nid: t1\ntitle: \"attack: " + ITEM + "\"\n"
            f"item: {ITEM}\ndescription: |\n  attack\n", encoding="utf-8")
        (run_dir / "run.yaml").write_text(
            "run_id: demo\n"
            "mode: attack\n"
            f"preset: {preset.name}\n"
            "companions: [ours]\n"
            "repos: [ours]\n"
            "source_access: text\n"
            f"host:\n  kind: remote\n  name: {self.HOST}\n"
            "sources:\n"
            f"  - id: {SOURCE_ID}\n"
            f'    sha256: "{sha}"\n'
            f"    offset: {OFFSET}\n"
            "gate:\n"
            f"  config: {run_dir / 'gate.json'}\n"
            "  out_of_tree: true\n"
            "tasks:\n  count: 1\n"
            "remote:\n  entrypoint: demo\n  tend_root: demo-tend\n", encoding="utf-8")

        plan = mtr.TendRootPlan(
            local=run_dir / "tend-root", tend_root="/srv/audit/demo-tend",
            entrypoint="/srv/audit/demo", gate_dir="/srv/audit/demo-gate",
            prompts_dir="/srv/audit/demo-tend/prompts")
        mtr.make_tend_root(plan, preset, run_id="demo",
                           gate_config={"sources": {SOURCE_ID: {}}},
                           allow_sandbox_bypass=bypass)
        return root

    def plan_output(self, *, bypass: bool) -> str:
        """The dry-run plan `cf run` prints, stdout and stderr together."""
        import contextlib
        import io

        import cf as cf_main

        root = self.workspace(bypass=bypass)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cf_main.main(["run", "demo", "--host", self.HOST, "--i-have-consent",
                               "--workspace", str(root)])
        self.assertEqual(rc, 0, out.getvalue() + err.getvalue())
        return out.getvalue() + err.getvalue()

    def test_the_dry_run_plan_names_the_staged_bypass(self):
        text = self.plan_output(bypass=True)
        self.assertIn("--allow-sandbox-bypass", text)
        self.assertIn("CF_ALLOW_SANDBOX_BYPASS=1", text)

    def test_a_default_root_says_nothing_about_it(self):
        """Deny-by-default is the quiet case: a warning on every run is noise."""
        self.assertNotIn("CF_ALLOW_SANDBOX_BYPASS", self.plan_output(bypass=False))


# --------------------------------------------------------------------------- #
# H9 prompts-workflows#5 — cf run refuses a root that names another model
# --------------------------------------------------------------------------- #
class TestRunRefusesAModelMismatch(unittest.TestCase):
    """``run.yaml``'s ``model`` is what a report attributes the findings to; the
    staged ``.tend/worker-agent.yaml``'s ``model_name`` is what runs.  The reader
    existed and nothing compared them."""

    def root_for(self, model: str | None) -> pathlib.Path:
        root = scratch(self)
        plan = mtr.TendRootPlan(
            local=root / "tend-root", tend_root="/srv/r1-tend", entrypoint="/srv/r1",
            gate_dir="/srv/r1-gate", prompts_dir="/srv/r1-tend/prompts")
        mtr.make_tend_root(plan, presets_mod.get("attack-hunt"), run_id="r1",
                           gate_config={"sources": {SOURCE_ID: {}}}, model=model)
        return root

    def test_agreement_is_silent(self):
        from cfcmd import run as run_cmd

        root = self.root_for("claude-sonnet-5")
        self.assertEqual(
            models_mod.tend_agent_model(root / "tend-root", role="worker"), "claude-sonnet-5")
        self.assertIsNone(run_cmd._check_model(root, {"model": "claude-sonnet-5"}, execute=False))

    def test_a_dry_run_prints_the_mismatch(self):
        from cfcmd import run as run_cmd

        root = self.root_for("claude-sonnet-5")
        message = run_cmd._check_model(root, {"model": "gpt-5.6-sol"}, execute=False)
        self.assertIsNotNone(message)
        self.assertIn("gpt-5.6-sol", message)
        self.assertIn("claude-sonnet-5", message)

    def test_execute_refuses_and_names_the_command_that_settles_it(self):
        from cfcmd import run as run_cmd

        root = self.root_for("claude-sonnet-5")
        with self.assertRaises(CfError) as cm:
            run_cmd._check_model(root, {"model": "gpt-5.6-sol", "preset": "attack-hunt",
                                        "run_id": "r1"}, execute=True)
        self.assertIn("refusing to launch", cm.exception.message)
        self.assertIn("cf plan-run", cm.exception.hint or "")
        self.assertIn("--force", cm.exception.hint or "")

    def test_a_record_with_no_model_is_not_a_mismatch(self):
        """`model: null` is the honest "the bridge's own default" case."""
        from cfcmd import run as run_cmd

        root = self.root_for(None)
        self.assertIsNone(run_cmd._check_model(root, {"model": None}, execute=True))

    def test_the_readers_docstring_no_longer_says_it_is_unwired(self):
        doc = models_mod.tend_agent_model.__doc__ or ""
        self.assertNotIn("not wired yet", doc)
        self.assertIn("_check_model", doc)


# --------------------------------------------------------------------------- #
# H10 — finding_id's docstring says which key joins two runs
# --------------------------------------------------------------------------- #
class TestFindingIdDocstringIsTrue(unittest.TestCase):
    """It claimed the id is "deterministic across runs of the same defect, so
    union/agreed/adjudication link by id".  ``run_id`` is its first part."""

    def test_the_same_defect_in_two_runs_gets_two_ids(self):
        args = (SOURCE_ID, "Exercise 1.2.1", "CONTENT", ["ours:mono"])
        self.assertNotEqual(hashing.finding_id("run-a", *args),
                            hashing.finding_id("run-b", *args))

    def test_the_run_independent_key_is_the_one_that_joins(self):
        from harvest import tally

        args = (SOURCE_ID, "Exercise 1.2.1", "CONTENT", ["ours:mono"])
        self.assertEqual(tally.defect_key(*args), tally.defect_key(*args))
        self.assertEqual(tally.defect_key(*args), hashing.finding_id("", *args))

    def test_the_docstring_says_so(self):
        doc = " ".join((hashing.finding_id.__doc__ or "").split())
        self.assertNotIn("Deterministic across runs of the same defect", doc)
        self.assertIn("defect_key", doc)
        self.assertIn("run_id`` is the first part", doc)

    def test_the_frozen_definition_did_not_move(self):
        """The docstring changed; the bytes it documents did not."""
        self.assertEqual(
            hashing.finding_id("run-a", SOURCE_ID, "Exercise  1.2.1", "CONTENT",
                               ["b:two", "a:one"]),
            hashing.sha256_text("\n".join(
                ["run-a", SOURCE_ID, "Exercise 1.2.1", "CONTENT", "a:one", "b:two"])))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
