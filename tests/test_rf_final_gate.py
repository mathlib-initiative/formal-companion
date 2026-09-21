"""Final review-fix regressions for the findings gate (stdlib unittest).

One class per defect the adversarial review of the whole branch found in
``scripts/gate/``, ``scripts/cflib/gitsafe.py`` and the ledger reader the gate is
supposed to agree with:

* the pins stopped the index-refresh hook but not git's CONTENT filters, so a
  worker could still hand the gate a program to run from under ``.git/`` and be
  reported clean -- and the same route was open on a companion repository;
* one nonsense word colliding with an operator token name turned the operator
  pass off for the whole quote, which is all a flipped inequality needed;
* defect rows nested under ``result`` were validated by nothing and counted by
  the ledger, the bypass the top-level walk was written to close;
* a malformed ``gate.json`` and a missing ``decl_index`` exited 1 -- the code
  every prompt defines as "your findings were rejected, fix them";
* a quote spliced across a page break bound to no page, and an empty page list
  turns the item-page bind off;
* a typographic dash at a quote's edge was read as a minus sign the book lacks;
* the unbound note told the operator the item names no printed page when it does
  and the run only lacks the offset;
* the tend README claimed a guarantee the shipped configuration does not give.

The synthetic book below is a public-domain paraphrase: no source text lives in
this repo (CLAUDE.md S1).  Every test here runs without the private baselines.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))

from cflib import gitsafe  # noqa: E402
from gate import verify_findings as vf  # noqa: E402
from plan import presets as presets_mod  # noqa: E402

TMPDIR = os.environ.get("CF_TMPDIR") or None
SOURCE_ID = "a-book-2nd-edition"
SOURCE_SHA = "d" * 64

#: Seven pages with a running head on each, the item on page 1 and an unrelated
#: exercise six pages away, so a quote can be placed and mis-placed.
RUNNING_HEAD = "4.1. Outer measures and the elementary sets"
PAGE_1 = (
    "Exercise 4.1.2 (Monotonicity). Let E and F be elementary sets. "
    "Show that if E is contained in F then m(E) <= m(F), and that the "
    "infimum over all such covers is attained.\n"
)
PAGE_7 = (
    "Exercise 4.9.9 (Far from the rest). Show that a bounded set of finite "
    "outer measure need not be measurable in the Jordan sense.\n"
)
#: pages 2 to 6 carry ordinary prose; page 6's last sentence is the one a
#: straddling quote is spliced onto
_FILLER = [
    "We record the consequences of the previous paragraph and keep them "
    "for later use in section %d.\n" % n
    for n in range(2, 7)
]
PAGES = [PAGE_1, *_FILLER, PAGE_7]
BOOK = "".join(f"{RUNNING_HEAD}\n\n{body}\f" for body in PAGES)

ITEM = "Exercise 4.1.2"
QUOTE_1 = ("Show that if E is contained in F then m(E) <= m(F), and that the "
           "infimum over all such covers is attained")
#: the tail of page 6, the head of page 7 and page 7's first sentence: a window
#: that is in the book but on no single page
STRADDLE_QUOTE = ("for later use in section 6. " + RUNNING_HEAD + " " +
                  PAGE_7.split("\n")[0])

BOGUS_ROW = {
    "concept": "invented",
    "kind": "NOT_A_KIND",
    "repos_involved": ["not-a-repo"],
    "faithful": "nope",
    "unfaithful_decls": ["not-a-repo:Nonexistent.decl"],
    "severity": "catastrophic",
}


def gate_cfg(book: pathlib.Path, **over) -> vf.GateConfig:
    """A GateConfig for the ``attack-vac`` preset with ``book`` as its source."""
    data = presets_mod.gate_config(
        presets_mod.get("attack-vac"), run_id="test-run",
        sources={SOURCE_ID: {"text": str(book), "sha256": SOURCE_SHA,
                             **over.pop("source", {})}},
        repos=["ours"])
    data.update(over)
    return vf.GateConfig(data)


def doc_for(quote: str = QUOTE_1, *, item: str = ITEM, **over) -> dict:
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


class BookTestCase(unittest.TestCase):
    """A written-out source text, so the gate loads it exactly as it does a run's."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMPDIR)
        self.root = pathlib.Path(self.tmp.name)
        self.book_path = self.root / "text.txt"
        self.book_path.write_text(BOOK, encoding="utf-8")
        self.cfg = gate_cfg(self.book_path)
        self.books, errs = vf.load_sources(self.cfg)
        self.assertEqual(errs, [])
        self.book = self.books[SOURCE_ID]

    def tearDown(self):
        self.tmp.cleanup()

    def errs(self, doc, cfg=None):
        return vf.validate_doc(doc, "x.json", cfg or self.cfg, self.books)


# --------------------------------------------------------------------------- #
# FR#12 — the content filters are a program the repository names
# --------------------------------------------------------------------------- #
class FilterDriverTestCase(unittest.TestCase):
    """Helpers for arming a repository with a content filter of its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMPDIR)
        self.root = pathlib.Path(self.tmp.name)
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd: pathlib.Path | None = None):
        subprocess.run(["git", *args], cwd=str(cwd or self.repo), check=True,
                       env=self.env, capture_output=True, text=True)

    def arm_filter(self, repo: pathlib.Path, pattern: str) -> pathlib.Path:
        """Point ``filter.x.clean`` at a script and apply it through .git/."""
        proof = repo / ".git" / "proof-filter.txt"
        script = repo / ".git" / "f.sh"
        script.write_text("#!/bin/sh\necho ran > %s\ncat\n" % proof, encoding="utf-8")
        script.chmod(0o755)
        self.git("config", "filter.x.clean", str(script), cwd=repo)
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text(pattern, encoding="utf-8")
        proof.unlink(missing_ok=True)
        return proof


class TestTheGateRunsNoFilterTheWorkerChose(FilterDriverTestCase):
    """The pins stopped ``core.fsmonitor``; they did not stop a content filter.

    ``changed_entries`` diffs the WORKING TREE against the base, which has to
    read that tree's content, so ``filter.x.clean`` in ``.git/config`` plus
    ``*.json filter=x`` in ``.git/info/attributes`` -- both invisible to the
    changed-files check -- made the gate execute the worker's program and then
    report the contribution clean.  Verified before the fix: the proof file below
    was written and the gate printed its OK line with exit 0.
    """

    def setUp(self):
        super().setUp()
        self.repo = self.root / "worktree"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main", ".")
        (self.repo / "findings").mkdir()
        (self.repo / "tasks").mkdir()
        (self.repo / "findings" / "alice.json").write_text(
            json.dumps({"schema_version": 2, "disagreements": [{"kind": "CONTENT"}]}),
            encoding="utf-8")
        (self.repo / "tasks" / "alice.yaml").write_text("id: alice\nstatus: complete\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "alice's finding lands")
        self.git("checkout", "-q", "-b", "carol")

    def write_own_pair(self):
        (self.repo / "findings" / "carol.json").write_text("{}\n")
        (self.repo / "tasks" / "carol.yaml").write_text("id: carol\n")

    def bare_diff_fires(self, proof: pathlib.Path) -> bool:
        """Whether the unsafe spelling runs it -- the exposure, for comparison."""
        subprocess.run(["git", "diff", "--name-status", "main"], cwd=str(self.repo),
                       capture_output=True, text=True, check=False, env=self.env)
        fired = proof.exists()
        proof.unlink(missing_ok=True)
        return fired

    def test_no_content_filter_of_the_worktree_is_executed(self):
        proof = self.arm_filter(self.repo, "*.json filter=x\n")
        self.write_own_pair()
        unsafe_ran = self.bare_diff_fires(proof)
        paths, errs = vf.changed_entries(self.repo, "main")
        self.assertEqual(errs, [])
        self.assertEqual(set(paths), {"findings/carol.json", "tasks/carol.yaml"})
        self.assertFalse(proof.exists(), "the gate executed a program the worker chose")
        if not unsafe_ran:                                   # pragma: no cover - git build
            self.skipTest("this git does not run a clean filter on `diff --name-status`")

    def test_the_pins_name_every_driver_the_repo_defines(self):
        self.arm_filter(self.repo, "*.json filter=x\n")
        self.git("config", "filter.y.process", "/bin/false")
        pins = vf._filter_pins(self.repo)
        self.assertIn("filter.x.clean=", pins)
        self.assertIn("filter.y.process=", pins)
        self.assertEqual(pins.count("-c"), len(pins) // 2)
        # the gate ships without cflib, so its copy and gitsafe's must agree
        self.assertEqual(pins, gitsafe.filter_pins(self.repo))

    def test_an_uncommitted_rewrite_of_a_neighbours_pair_is_still_refused(self):
        """What a tree-to-tree diff would have cost: the case the rule exists for.

        Diffing ``base..HEAD`` plus the index runs no filter either, but it cannot
        see an UNSTAGED rewrite of a tracked file -- which is exactly where a
        worker emptying a neighbour's landed findings would leave the edit.
        """
        self.arm_filter(self.repo, "*.json filter=x\n")
        self.write_own_pair()
        (self.repo / "findings" / "alice.json").write_text(
            json.dumps({"schema_version": 2, "disagreements": []}) + "\n", encoding="utf-8")
        script = str(SKILL / "scripts" / "gate" / "check_changed_files.py")
        proc = subprocess.run(
            [sys.executable, script, "--base", "main", "--root", str(self.repo)],
            capture_output=True, text=True, env={**self.env, "TEND_TASK_ID": "carol"})
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("findings/alice.json", proc.stderr)
        self.assertFalse((self.repo / ".git" / "proof-filter.txt").exists())


class TestGitsafeRunsNoFilterTheCompanionChose(FilterDriverTestCase):
    """The same hole on a companion repository, which is read-only data.

    ``gitsafe.run(repo, ["status", "--porcelain"])`` -- the call
    ``scripts/recheck/pin.py`` makes -- runs ``filter.<n>.clean`` over any file
    whose index entry is stat-stale, so a tree delivered with its ``.git/``
    intact executed its own program on a read.
    """

    def setUp(self):
        super().setUp()
        self.repo = self.root / "companion"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main", ".")
        (self.repo / "a.lean").write_text("theorem a : True := trivial\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "the companion's own commit")

    def make_stat_stale(self) -> None:
        """Same size, newer mtime: git must re-read the content to decide."""
        path = self.repo / "a.lean"
        data = path.read_bytes()
        path.write_bytes(data)
        stamp = os.stat(path).st_mtime + 5
        os.utime(path, (stamp, stamp))

    def test_a_companions_clean_filter_does_not_run_on_a_read(self):
        proof = self.arm_filter(self.repo, "*.lean filter=x\n")
        self.make_stat_stale()
        subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"],
                       capture_output=True, text=True, check=False, env=self.env)
        unsafe_ran = proof.exists()
        proof.unlink(missing_ok=True)
        self.make_stat_stale()
        gitsafe.run(self.repo, ["status", "--porcelain"])
        self.assertFalse(proof.exists(), "the companion's own program ran on a read")
        if not unsafe_ran:                                   # pragma: no cover - git build
            self.skipTest("this git does not run a clean filter on `status --porcelain`")

    def test_a_driver_is_pinned_only_once_it_is_named_and_never_raises(self):
        self.assertNotIn("filter.x.clean=", gitsafe.filter_pins(self.repo))
        self.arm_filter(self.repo, "*.lean filter=x\n")
        pins = gitsafe.filter_pins(self.repo)
        self.assertEqual(pins[pins.index("filter.x.clean=") - 1], "-c")
        # a path that is no repo at all is no pins, not an exception: the
        # caller's own command is what reports that there is nothing to read
        self.assertEqual(gitsafe.filter_pins(self.root / "nowhere"), [])
        self.assertEqual(gitsafe.argv(self.root / "nowhere", "rev-parse", "HEAD")[-2:],
                         ["rev-parse", "HEAD"])

    def test_the_boundary_comments_do_not_claim_the_wider_closure(self):
        """Both files said the fsmonitor pin closes the class; it closes that hook."""
        for text in (gitsafe.__doc__ or "", gitsafe.filter_pins.__doc__ or ""):
            self.assertNotIn("verified: this alone stops it", text)
        pins_comment = (SKILL / "scripts" / "gate" / "verify_findings.py").read_text(
            encoding="utf-8")
        self.assertIn("NOT git's content filters", pins_comment)

    def test_the_header_admits_the_pins_reach_the_users_own_config(self):
        """The header's "only what the audited repository controls" is not all of it.

        A driver is blanked by the NAME an attribute gives it, wherever it was
        defined, so the header has to say so too: a reader who stops there would
        not expect a globally configured git-lfs to be neutralised.
        """
        from unittest import mock

        header = " ".join((gitsafe.__doc__ or "").split())
        self.assertIn("in every scope, the user's own included", header)
        self.assertIn("git-lfs", header)
        # the behaviour the clause describes, with a HOME of our own so nothing
        # outside the temporary directory is read or written
        home = self.root / "home"
        home.mkdir()
        (home / ".gitconfig").write_text('[filter "lfs"]\n\tclean = /bin/false\n',
                                         encoding="utf-8")
        with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home)}):
            self.assertIn("filter.lfs.clean=", gitsafe.filter_pins(self.repo))


# --------------------------------------------------------------------------- #
# FR#13 — the operator pass must not be switchable off by a word
# --------------------------------------------------------------------------- #
class TestATokenWordCannotTurnTheOperatorPassOff(BookTestCase):
    """``_split_operators`` classifies a token by NAME, so a quote carrying the
    plain word ``ople`` produced a word stream one token shorter than ``norm``'s
    and the anchor guard silently skipped the operator pass for the whole quote.
    A flipped inequality was then certified, and the OK line still said
    "operators checked".
    """

    def test_the_real_quote_still_passes_verbatim(self):
        self.assertEqual(vf.quote_ok(self.book, QUOTE_1), (True, "verbatim"))
        self.assertEqual(self.errs(doc_for()), [])

    def test_a_token_word_in_the_quote_cannot_buy_a_flipped_inequality_a_pass(self):
        flip = QUOTE_1.replace("<=", ">=")
        self.assertFalse(vf.quote_ok(self.book, flip)[0])
        for pad in ("ople", "opeq", "opin"):
            for q in (flip + " " + pad, pad + " " + flip):
                with self.subTest(q=q[-24:]):
                    ok, why = vf.quote_ok(self.book, q)
                    self.assertFalse(ok, "sentinel word disabled the operator pass: " + why)
                    self.assertNotEqual(self.errs(doc_for(q)), [])

    def test_the_refusal_says_what_to_do_about_it(self):
        ok, why = vf.quote_ok(self.book, QUOTE_1 + " ople")
        self.assertFalse(ok)
        self.assertIn("collides with an operator token name", why)
        self.assertIn("rewrite the quote", why)


# --------------------------------------------------------------------------- #
# FR#14 — every key the LEDGER reads rows out of is a key the gate validates
# --------------------------------------------------------------------------- #
class TestNestedDefectRowsAreValidatedOrNotCounted(BookTestCase):
    """The catch-all inspected top-level ARRAYS, and the ledger unwrapped a
    top-level ``result`` DICT before reading its ``disagreements``: one file gave
    the gate 0 problems, the ledger 1 row and the tally 0 rows, with the quote
    gate, the vocabularies, the witness rule and the repo allowlist all skipped.
    """

    def corpus_dir(self, doc, name: str = "exercise-4-1-2.json") -> pathlib.Path:
        out = self.root / "corpus"
        out.mkdir(exist_ok=True)
        (out / name).write_text(json.dumps(doc), encoding="utf-8")
        return out

    def test_a_row_the_ledger_unwraps_is_refused_by_the_gate(self):
        from ledger import findings as ledger_findings

        doc = doc_for(disagreements=[], result={"item": ITEM,
                                                "disagreements": [dict(BOGUS_ROW)]})
        corpus = self.corpus_dir(doc)
        self.assertEqual(len(ledger_findings.load_findings([str(corpus)])), 0,
                         "the ledger counted a row out of a corpus item's 'result'")
        self.assertTrue(any("'result'" in e for e in self.errs(doc)), self.errs(doc))

    def test_the_stray_key_walk_covers_both_shapes(self):
        for stray in ({"defects": [dict(BOGUS_ROW)]},
                      {"payload": {"findings": [dict(BOGUS_ROW)]}}):
            with self.subTest(stray=sorted(stray)):
                errs = self.errs(doc_for(**stray))
                self.assertTrue(any(repr(sorted(stray)[0]) in e for e in errs), errs)

    def test_an_ordinary_document_with_other_keys_still_passes(self):
        """The walk must not refuse the shapes real documents carry."""
        doc = doc_for()
        doc["_meta"] = {"challenges": [{"id": 1, "answered": True}]}
        doc["gate"] = {"preset": "attack-vac", "ok": True}
        doc["notes"] = ["nothing to see"]
        self.assertEqual(self.errs(doc), [])

    def test_the_killed_fleets_harvest_is_still_read_under_its_own_name(self):
        """The unwrap is not deleted, it is narrowed to the shape it is for."""
        from ledger import findings as ledger_findings

        row = {"item": ITEM, "kind": "CONTENT", "unfaithful_decls": ["ours:mono"],
               "severity": "high", "repos_involved": ["ours"]}
        partial = self.root / "harvest.partial.json"
        partial.write_text(json.dumps({"result": {"findings": [row]}}), encoding="utf-8")
        self.assertEqual(len(ledger_findings.load_findings([str(partial)])), 1)
        renamed = self.root / "exercise-4-1-2.json"
        renamed.write_text(partial.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(len(ledger_findings.load_findings([str(renamed)])), 0)


# --------------------------------------------------------------------------- #
# FR#15 — an infrastructure fault is exit 3, never "your findings were rejected"
# --------------------------------------------------------------------------- #
class TestTheGateCouldNotRunIsNotARejection(BookTestCase):
    """``GateConfig.load`` and ``_load_decl_index`` were the two unguarded calls
    in ``main``, so a truncated gate.json and a missing index exited 1 with a
    traceback -- the code ``templates/prompts/worker-system.md.tmpl`` defines as
    "your findings were rejected: fix them", next to a sentence promising that a
    missing index is an infrastructure fault.
    """

    def gate_dir(self, doc) -> list[str]:
        (self.root / "findings").mkdir(exist_ok=True)
        (self.root / "findings" / "exercise-4-1-2.json").write_text(
            json.dumps(doc), encoding="utf-8")
        cfgp = self.root / "gate.json"
        cfgp.write_text(json.dumps(self.cfg.data), encoding="utf-8")
        return ["--gate-config", str(cfgp), "--root", str(self.root), "--quiet"]

    def test_a_valid_config_is_still_a_clean_run(self):
        self.assertEqual(vf.main(self.gate_dir(doc_for())), vf.EXIT_OK)

    def test_a_malformed_gate_config_is_not_a_rejected_finding(self):
        argv = self.gate_dir(doc_for())
        bad = self.root / "truncated.json"
        bad.write_text('{ "sources": ', encoding="utf-8")
        argv[1] = str(bad)
        self.assertEqual(vf.main(argv), vf.EXIT_GATE_BROKEN)
        notmap = self.root / "notmap.json"
        notmap.write_text("[]", encoding="utf-8")
        argv[1] = str(notmap)
        self.assertEqual(vf.main(argv), vf.EXIT_GATE_BROKEN)

    def test_a_missing_or_unreadable_decl_index_is_not_a_rejected_finding(self):
        for body in (None, "not json at all\n"):
            with self.subTest(body=body):
                idx = self.root / "decl-index.json"
                if body is None:
                    idx.unlink(missing_ok=True)
                else:
                    idx.write_text(body, encoding="utf-8")
                data = dict(self.cfg.data, decl_index=str(idx))
                cfgp = self.root / "gate-idx.json"
                cfgp.write_text(json.dumps(data), encoding="utf-8")
                argv = self.gate_dir(doc_for())
                argv[1] = str(cfgp)
                self.assertEqual(vf.main(argv), vf.EXIT_GATE_BROKEN)

    def test_the_three_infrastructure_messages_read_alike(self):
        source = (SKILL / "scripts" / "gate" / "verify_findings.py").read_text(
            encoding="utf-8")
        self.assertEqual(source.count("this is not a finding of yours"), 3)


# --------------------------------------------------------------------------- #
# FR#31 — a match that straddles a page break is still bound to its item
# --------------------------------------------------------------------------- #
class TestAQuoteSplicedAcrossAPageBreakIsStillBound(BookTestCase):
    """``pages_matching`` reported only whole-page hits, so a window crossing a
    form feed got ``[]`` -- and an empty page list short-circuits
    ``PageBind.problem``.  A quote from six pages away passed by being spliced
    onto the tail of the preceding page, with only a "straddles a page break"
    note and exit 0.
    """

    def test_the_straddling_quote_names_both_pages_and_is_refused(self):
        m = vf.quote_match(self.book, STRADDLE_QUOTE)
        self.assertEqual(m.pages, [6, 7])
        errs = self.errs(doc_for(STRADDLE_QUOTE, item=ITEM))
        self.assertTrue(any("not the item's passage" in e for e in errs), errs)

    def test_an_ordinary_quote_is_reported_on_its_own_page_only(self):
        """The pair search runs only when the whole-page search found nothing,
        or every quote would be counted on its neighbours too and the recurrence
        ceiling would fire on ordinary passages."""
        self.assertEqual(vf.quote_match(self.book, QUOTE_1).pages, [1])
        self.assertEqual(self.book.pages_matching(vf.norm(PAGE_7.split("(")[0])), [7])
        self.assertFalse(vf.quote_ok(self.book, RUNNING_HEAD)[0])

    def test_a_blank_page_at_the_seam_does_not_hide_the_match(self):
        """``from_text`` joins the non-empty pages, so the pairs must be of those."""
        raw = BOOK.replace("\f" + RUNNING_HEAD + "\n\n" + PAGE_7, "\f\f" + RUNNING_HEAD
                           + "\n\n" + PAGE_7)
        book = vf.BookText.from_text(raw)
        self.assertEqual(vf.quote_match(book, STRADDLE_QUOTE).pages, [6, 8])


# --------------------------------------------------------------------------- #
# FR#46 — a dash at the quote's edge is punctuation, not a minus sign
# --------------------------------------------------------------------------- #
class TestATypographicDashAtTheEdgeIsNotAnOperator(BookTestCase):
    """The verbatim window is compared WITH its edges, ``_fold`` maps em/en dash
    and the minus sign to ``-`` and the operator table reads ``-`` as
    ``opminus``, so an em dash introducing a quotation or a trailing pdftotext
    hyphen refused a correct finding and blamed the mathematics for it.
    """

    def test_a_boundary_dash_does_not_refuse_a_verbatim_quote(self):
        for d in ("— " + QUOTE_1, QUOTE_1 + " –", QUOTE_1 + " -",
                  "− " + QUOTE_1, QUOTE_1 + " — "):
            with self.subTest(quote=d[:6] + "..." + d[-6:]):
                ok, why = vf.quote_ok(self.book, d)
                self.assertTrue(ok, why)
                self.assertEqual(why, "verbatim")

    def test_a_sign_written_against_its_operand_is_still_an_operator(self):
        """What forbids the wider fix: dropping ``opminus`` from the edges
        certifies a flipped sign as verbatim."""
        book = vf.BookText.from_text(
            "Lemma 4.2.1. In that case we have 1 <= m(F) for every set F that "
            "covers it, and the infimum is attained.\n\f")
        kept = "we have 1 <= m(F) for every set F that covers it, and the infimum is attained"
        self.assertTrue(vf.quote_ok(book, kept)[0])
        self.assertFalse(vf.quote_ok(book, "-1 <= m(F) for every set F that covers "
                                           "it, and the infimum is attained")[0])

    def test_an_interior_operator_is_untouched_by_the_strip(self):
        flip = "— " + QUOTE_1.replace("<=", ">=")
        ok, why = vf.quote_ok(self.book, flip)
        self.assertFalse(ok)
        self.assertIn("OPERATOR has been CHANGED", why)


# --------------------------------------------------------------------------- #
# FR#47 — the unbound note names the missing page_offset, not the item
# --------------------------------------------------------------------------- #
class TestTheUnboundNoteSaysWhyTheBindFailed(BookTestCase):
    """``_resolve`` matched the printed page first but used it only with a
    non-zero offset, then fell through and reported the LABEL search's message.
    The note is what ``cf harvest --gate`` records in the receipt, so the recorded
    reason denied that the item names a printed page at all -- 230 of the
    baseline's quotes -- and sent the reader to the item instead of to gate.json.
    """

    def test_an_in_text_item_without_an_offset_names_the_missing_key(self):
        bind = vf.PageBind(self.cfg, SOURCE_ID, self.book, "In-text, book p. 3")
        self.assertEqual(bind.pages, [])
        self.assertIn("names printed page 3", bind.how)
        self.assertIn("page_offset", bind.how)

    def test_an_item_matching_both_patterns_still_binds_by_its_heading(self):
        """Why the branch stays AFTER the label search: an early return here
        unbinds items this gate places today, which moves the pinned figures."""
        both = vf.PageBind(self.cfg, SOURCE_ID, self.book,
                           "In-text convention, book p. 29 (Exercise 4.1.2: a thing)")
        self.assertEqual((both.pages, both.how),
                         ([1], "the item's own heading in the pinned text"))

    def test_an_item_naming_no_page_at_all_keeps_the_original_message(self):
        bind = vf.PageBind(self.cfg, SOURCE_ID, self.book,
                           "In-text, first paragraph of the chapter")
        self.assertEqual(bind.pages, [])
        self.assertIn("neither a numbered label nor a printed page", bind.how)

    def test_the_offset_route_is_unchanged(self):
        cfg = gate_cfg(self.book_path, source={"page_offset": 2})
        bind = vf.PageBind(cfg, SOURCE_ID, self.book, "In-text, book p. 3")
        self.assertEqual(bind.pages, [5])
        self.assertIn("page_offset", bind.how)


# --------------------------------------------------------------------------- #
# FR#30 — the tend README claimed a guarantee the shipped config does not give
# --------------------------------------------------------------------------- #
class TestTheReadmeDoesNotOverstateTheOwnershipRule(unittest.TestCase):
    """``templates/tend/config.yaml.tmpl`` passes no ``--task-id``, so the weak
    single-stem rule is the default path -- and there a change set that touches
    only a neighbour's pair passes.  The README said such a contribution "is
    still refused by name", contradicting ``references/runbook-server.md`` §4 on
    a data-loss path.  The rule itself is a recorded decision and does not move.
    """

    def test_the_lone_rewrite_of_a_neighbours_pair_is_not_caught(self):
        self.assertEqual(vf.check_changed({"findings/alice.json": "M",
                                           "tasks/alice.yaml": "M"}), [])

    def test_the_readme_scopes_the_claim_the_way_the_runbook_does(self):
        readme = (SKILL / "templates" / "tend" / "README.md.tmpl").read_text(encoding="utf-8")
        runbook = (SKILL / "references" / "runbook-server.md").read_text(encoding="utf-8")
        self.assertIn("cannot tell whose the one stem is", readme)
        self.assertIn("is not caught", runbook)
        self.assertNotIn("rewrites or empties a neighbour's landed pair is still refused",
                         readme.replace("\n", " "))

    def test_the_readme_sends_the_operator_to_the_section_that_says_it(self):
        """A pointer to the wrong section is a pointer to nothing.

        The README defers the scoping to the runbook instead of repeating it, so
        the section it names has to be the one carrying "is not caught" -- and
        the same one ``SKILL.md`` already names, or the two shipped documents
        disagree about where the operator should look.
        """
        readme = (SKILL / "templates" / "tend" / "README.md.tmpl").read_text(encoding="utf-8")
        runbook = (SKILL / "references" / "runbook-server.md").read_text(encoding="utf-8")
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        cited = re.findall(r"`references/runbook-server\.md` §(\d+)", readme)
        self.assertEqual(len(cited), 1, "expected exactly one runbook citation: %r" % (cited,))
        # the sections, split on the runbook's own "## <n>. " headings
        bodies = {}
        current = None
        for line in runbook.splitlines():
            head = re.match(r"## (\d+)\. ", line)
            if head:
                current = head.group(1)
                bodies[current] = []
            elif current:
                bodies[current].append(line)
        body = " ".join(bodies.get(cited[0], []))
        self.assertIn("is not caught", body,
                      "README sends the operator to §%s, which does not carry the sentence"
                      % cited[0])
        self.assertIn("`references/runbook-server.md` §%s" % cited[0], skill,
                      "README and SKILL.md cite different sections for the same fact")

    def test_the_docstring_says_which_change_sets_the_stem_count_refuses(self):
        doc = " ".join((vf.check_changed.__doc__ or "").split())
        self.assertIn("A lone stem is never refused here", doc)
        self.assertNotIn("one that rewrites, empties, deletes or renames a path "
                         "that was already on the base branch is still refused", doc)


if __name__ == "__main__":
    unittest.main()
