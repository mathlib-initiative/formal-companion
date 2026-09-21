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

import contextlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
#: a folio-only page, which is what pdftotext writes for a page carrying nothing
#: but its number -- the short page an attacker puts at the seam
FOLIO_PAGE = "42\n"
#: the same splice with that page in the middle of it: three pages, not two
SPLICED_QUOTE = ("for later use in section 6. " + FOLIO_PAGE.strip() + " " +
                 RUNNING_HEAD + " " + PAGE_7.split("\n")[0])


def book_with_a_folio_page() -> "vf.BookText":
    """:data:`BOOK` with a folio-only page inserted before the last one."""
    return vf.BookText.from_text(BOOK.replace(
        "\f" + RUNNING_HEAD + "\n\n" + PAGE_7,
        "\f" + FOLIO_PAGE + "\f" + RUNNING_HEAD + "\n\n" + PAGE_7))

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

    def arm_filter(self, repo: pathlib.Path, pattern: str, *,
                   driver: str = "x") -> pathlib.Path:
        """Point ``filter.<driver>.clean`` at a script and apply it through .git/.

        ``driver`` is the name the repository chooses, and a name carrying an
        ``=`` is the one a ``-c`` blank cannot express: see
        :func:`cflib.gitsafe.filter_env`.
        """
        proof = repo / ".git" / "proof-filter.txt"
        script = repo / ".git" / "f.sh"
        script.write_text("#!/bin/sh\necho ran > %s\ncat\n" % proof, encoding="utf-8")
        script.chmod(0o755)
        self.git("config", f"filter.{driver}.clean", str(script), cwd=repo)
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text(pattern, encoding="utf-8")
        proof.unlink(missing_ok=True)
        return proof

    def a_git_that_ignores_the_config_environment(self) -> pathlib.Path:
        """A ``git`` shim that drops ``GIT_CONFIG_*``: a git older than 2.31.

        Those variables are how a whole key is blanked, and the git that predates
        them ignores them in SILENCE -- no error, no exit code, the driver simply
        runs.  Debian 11 ships 2.30.2 and Ubuntu 20.04 2.25.1; nothing in this
        repo checks the run host's git version (``cf doctor`` tests that git is on
        PATH), so the ``-c`` blanks are the floor under the pairs, and this shim
        is what stands on it.  Returns the directory to put in front of ``PATH``.
        """
        real = shutil.which("git")
        if real is None:                                     # pragma: no cover
            self.skipTest("git is not installed")
        bin_dir = self.root / "git-before-2.31"
        bin_dir.mkdir(exist_ok=True)
        shim = bin_dir / "git"
        shim.write_text(
            "#!/bin/sh\n"
            "unset GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS\n"
            "i=0\n"
            "while [ $i -lt 64 ]; do\n"
            "  unset GIT_CONFIG_KEY_$i GIT_CONFIG_VALUE_$i\n"
            "  i=$((i + 1))\n"
            "done\n"
            'exec "%s" "$@"\n' % real, encoding="utf-8")
        shim.chmod(0o755)
        return bin_dir


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

    def make_stat_stale(self) -> None:
        """Same bytes, newer mtime on the LANDED file: git must re-read it.

        Which is when the clean filter runs.  Without this the arming is inert on
        a fresh checkout and the test skips itself, proving nothing.
        """
        path = self.repo / "findings" / "alice.json"
        path.write_bytes(path.read_bytes())
        stamp = os.stat(path).st_mtime + 5
        os.utime(path, (stamp, stamp))

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
        # without the stale stat the arming fires only when git happens to think
        # the landed file is racily clean, and the test skips itself at random
        self.make_stat_stale()
        unsafe_ran = self.bare_diff_fires(proof)
        self.make_stat_stale()
        paths, errs = vf.changed_entries(self.repo, "main")
        self.assertEqual(errs, [])
        self.assertEqual(set(paths), {"findings/carol.json", "tasks/carol.yaml"})
        self.assertFalse(proof.exists(), "the gate executed a program the worker chose")
        if not unsafe_ran:                                   # pragma: no cover - git build
            self.skipTest("this git does not run a clean filter on `diff --name-status`")

    def test_a_driver_whose_name_carries_an_equals_sign_is_blanked_too(self):
        """``-c`` splits at the first ``=``, so the driver name defeated the blank.

        ``-c 'filter.a=b.clean='`` sets ``filter.a`` to ``b.clean=`` and leaves
        the driver the worktree called ``a=b`` armed; the gate ran it and printed
        its OK line.  A name like this one is reached by ``GIT_CONFIG_KEY_i``,
        which carries the whole key; the ``-c`` blanks stay beside the pairs for
        the names they can express and for the git that predates the pairs.
        """
        proof = self.arm_filter(self.repo, "*.json filter=a=b\n", driver="a=b")
        self.write_own_pair()
        self.make_stat_stale()
        unsafe_ran = self.bare_diff_fires(proof)
        self.make_stat_stale()
        paths, errs = vf.changed_entries(self.repo, "main")
        self.assertEqual(errs, [])
        self.assertEqual(set(paths), {"findings/carol.json", "tasks/carol.yaml"})
        self.assertFalse(proof.exists(), "the gate executed a program the worker chose")
        # and through the shipped argv, which is what the rendered tend config
        # runs: the same contribution, reported clean while the payload ran
        self.make_stat_stale()
        script = str(SKILL / "scripts" / "gate" / "check_changed_files.py")
        proc = subprocess.run(
            [sys.executable, script, "--base", "main", "--root", str(self.repo)],
            capture_output=True, text=True, env={**self.env, "TEND_TASK_ID": "carol"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(proof.exists(), "the shipped argv executed the worker's program")
        if not unsafe_ran:                                   # pragma: no cover - git build
            self.skipTest("this git does not run a clean filter on `diff --name-status`")

    def test_the_blanks_name_every_driver_the_repo_defines(self):
        self.arm_filter(self.repo, "*.json filter=x\n")
        self.git("config", "filter.y.process", "/bin/false")
        self.git("config", "filter.a=b.clean", "/bin/false")
        pinned = vf._filter_env(self.repo)
        keys = {v for k, v in pinned.items() if k.startswith("GIT_CONFIG_KEY_")}
        self.assertLessEqual({"filter.x.clean", "filter.y.process", "filter.a=b.clean"}, keys)
        self.assertEqual(int(pinned["GIT_CONFIG_COUNT"]), len(keys))
        # every one of them is blanked, and the count covers exactly the pairs
        for i in range(int(pinned["GIT_CONFIG_COUNT"])):
            self.assertIn(f"GIT_CONFIG_KEY_{i}", pinned)
            self.assertEqual(pinned[f"GIT_CONFIG_VALUE_{i}"], "")
        # the gate ships without cflib, so its copy and gitsafe's must agree
        self.assertEqual(pinned, gitsafe.filter_env(self.repo))
        # and the OTHER channel is still there: `-c` is what a git older than
        # 2.31 reads, and the run host's version is nobody's to assume
        args = gitsafe.argv(self.repo, "status")
        self.assertEqual(args[args.index("filter.x.clean=") - 1], "-c")
        self.assertIn("filter.y.process=", args)
        self.assertNotIn("filter.a=b.clean=", args)   # `-c` cannot express it
        self.assertEqual(vf._filter_pin_args(vf._filter_names(self.repo)),
                         gitsafe.filter_pin_args(gitsafe.filter_names(self.repo)))

    def test_the_blanks_reach_a_git_that_ignores_the_environment_pairs(self):
        """The env pairs are 2.31 and later; the run host's git is not pinned.

        Moving the blanks OUT of ``-c`` to reach an ``=``-named driver dropped the
        floor below where it was before that attack was known: on a run host with
        Debian 11's git the worktree's ``filter.x.clean`` ran again, silently.
        Both channels go on every call now, and this is the one an old git reads.
        """
        from unittest import mock

        proof = self.arm_filter(self.repo, "*.json filter=x\n")
        self.write_own_pair()
        self.make_stat_stale()
        unsafe_ran = self.bare_diff_fires(proof)
        bin_dir = self.a_git_that_ignores_the_config_environment()
        self.make_stat_stale()
        with mock.patch.dict(os.environ,
                             {"PATH": "%s:%s" % (bin_dir, os.environ.get("PATH", ""))}):
            paths, errs = vf.changed_entries(self.repo, "main")
        self.assertEqual(errs, [])
        self.assertEqual(set(paths), {"findings/carol.json", "tasks/carol.yaml"})
        self.assertFalse(proof.exists(),
                         "with GIT_CONFIG_* ignored, nothing blanked the driver")
        if not unsafe_ran:                                   # pragma: no cover - git build
            self.skipTest("this git does not run a clean filter on `diff --name-status`")

    def test_the_blanks_extend_a_count_the_callers_environment_already_carries(self):
        """A ``GIT_CONFIG_*`` the operator set is extended, never overwritten."""
        from unittest import mock

        self.arm_filter(self.repo, "*.json filter=x\n")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1",
                                          "GIT_CONFIG_KEY_0": "user.name",
                                          "GIT_CONFIG_VALUE_0": "operator"}):
            pinned = vf._filter_env(self.repo)
            self.assertEqual(pinned, gitsafe.filter_env(self.repo))
        count = int(pinned["GIT_CONFIG_COUNT"])
        named = [pinned[f"GIT_CONFIG_KEY_{i}"] for i in range(1, count)]
        self.assertNotIn("GIT_CONFIG_KEY_0", pinned)        # the operator's own pair
        self.assertIn("filter.x.clean", named)
        self.assertEqual(count, 1 + len(named))

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

    def test_a_companions_equals_named_filter_does_not_run_on_a_read_either(self):
        """The name the companion chose is the one ``-c`` could not blank."""
        proof = self.arm_filter(self.repo, "*.lean filter=a=b\n", driver="a=b")
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

    def test_an_old_gits_read_is_blanked_by_the_c_half(self):
        """The same floor on the companion side, and one ``git config`` for both.

        ``gitsafe`` ships to the same run host as the gate, so the env pairs alone
        would leave a companion's driver armed there.  ``run()`` discovers the
        names once and spells them into both channels from that one list.
        """
        from unittest import mock

        proof = self.arm_filter(self.repo, "*.lean filter=x\n")
        self.make_stat_stale()
        subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"],
                       capture_output=True, text=True, check=False, env=self.env)
        unsafe_ran = proof.exists()
        proof.unlink(missing_ok=True)
        bin_dir = self.a_git_that_ignores_the_config_environment()
        self.make_stat_stale()
        with mock.patch.dict(os.environ,
                             {"PATH": "%s:%s" % (bin_dir, os.environ.get("PATH", ""))}):
            gitsafe.run(self.repo, ["status", "--porcelain"])
        self.assertFalse(proof.exists(),
                         "with GIT_CONFIG_* ignored, nothing blanked the driver")
        self.assertEqual(gitsafe.filter_pin_args(["filter.x.clean"]),
                         ["-c", "filter.x.clean="])
        self.assertEqual(gitsafe.filter_pin_args(["filter.a=b.clean"]), [])
        if not unsafe_ran:                                   # pragma: no cover - git build
            self.skipTest("this git does not run a clean filter on `status --porcelain`")

    def test_a_driver_is_pinned_only_once_it_is_named_and_never_raises(self):
        self.assertNotIn("filter.x.clean", gitsafe.filter_names(self.repo))
        self.arm_filter(self.repo, "*.lean filter=x\n")
        pinned = gitsafe.filter_env(self.repo)
        i = gitsafe.filter_names(self.repo).index("filter.x.clean")
        self.assertEqual(pinned[f"GIT_CONFIG_KEY_{i}"], "filter.x.clean")
        self.assertEqual(pinned[f"GIT_CONFIG_VALUE_{i}"], "")
        # a path that is no repo at all is no pins, not an exception: the
        # caller's own command is what reports that there is nothing to read
        self.assertEqual(gitsafe.filter_env(self.root / "nowhere"), {})
        self.assertEqual(gitsafe.argv(self.root / "nowhere", "rev-parse", "HEAD")[-2:],
                         ["rev-parse", "HEAD"])

    def test_the_boundary_comments_do_not_claim_the_wider_closure(self):
        """Both files said the fsmonitor pin closes the class; it closes that hook."""
        for text in (gitsafe.__doc__ or "", gitsafe.filter_env.__doc__ or ""):
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
            self.assertIn("filter.lfs.clean", gitsafe.filter_names(self.repo))
            self.assertIn("filter.lfs.clean", gitsafe.filter_env(self.repo).values())


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

    def test_a_source_text_that_exists_but_cannot_be_read_is_not_a_rejection(self):
        """``exists()`` is true for a directory and for a file this user may not
        read -- a partial rsync makes the first, a wrong run-host user the second.

        The prompts name "an unreadable source text" as an infrastructure fault
        in the same breath as the missing config and the missing index, and the
        module docstring reserves exit 3 for it; only the MISSING file arrived
        that way, the unreadable one came out as exit 1 and a traceback.
        """
        argv = self.gate_dir(doc_for())
        unreadable = self.root / "asdir"
        unreadable.mkdir()
        cases = [("a directory", unreadable)]
        blocked = self.root / "blocked.txt"
        if os.name == "posix" and os.geteuid() != 0:
            blocked.write_text(BOOK, encoding="utf-8")
            blocked.chmod(0o000)
            cases.append(("mode 000", blocked))
        for what, path in cases:
            with self.subTest(source=what):
                data = dict(self.cfg.data)
                data["sources"] = {SOURCE_ID: dict(data["sources"][SOURCE_ID],
                                                   text=str(path))}
                cfgp = self.root / "gate-unreadable.json"
                cfgp.write_text(json.dumps(data), encoding="utf-8")
                argv[1] = str(cfgp)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(vf.main(argv), vf.EXIT_GATE_BROKEN)
                self.assertIn("could not be read", err.getvalue())
                self.assertIn("this is not a finding of yours", err.getvalue())
        if blocked.exists():
            blocked.chmod(0o600)        # before the temporary directory goes

    def test_a_findings_dir_of_the_wrong_type_is_not_a_rejected_finding(self):
        """The same class one function further on: the key is joined onto
        ``--root``, so a number raised a TypeError after the load guard had
        already returned."""
        argv = self.gate_dir(doc_for())
        cfgp = self.root / "gate-dir.json"
        cfgp.write_text(json.dumps(dict(self.cfg.data, findings_dir=3)), encoding="utf-8")
        argv[1] = str(cfgp)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(vf.main(argv), vf.EXIT_GATE_BROKEN)
        self.assertIn("findings_dir must be a string", err.getvalue())

    def test_the_infrastructure_messages_read_alike(self):
        """The three the fix wave added, plus every way the source text fails to
        arrive: missing, unreadable, unconfigured, or not the pinned extraction."""
        source = (SKILL / "scripts" / "gate" / "verify_findings.py").read_text(
            encoding="utf-8")
        self.assertEqual(source.count("this is not a finding of yours"), 4)


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

    def test_a_short_page_at_the_seam_does_not_buy_the_splice_a_pass_either(self):
        """Pairs left the same attack open one page wider, and cheaply.

        A folio-only page between the two halves makes the splice span THREE
        pages, which the pair search reported as ``[]`` -- and an empty page list
        is exactly what turns the bind off.  Two hundred characters against a
        3,000-character ceiling then bought a quote from six pages away a
        "straddles a page break" note and exit 0.
        """
        book = book_with_a_folio_page()
        m = vf.quote_match(book, SPLICED_QUOTE)
        self.assertLess(len(SPLICED_QUOTE), vf.MAX_QUOTE_CHARS)
        self.assertEqual(m.pages, [6, 7, 8])
        errs = vf.validate_doc(doc_for(SPLICED_QUOTE, item=ITEM), "x.json", self.cfg,
                               {SOURCE_ID: book})
        self.assertTrue(any("not the item's passage" in e for e in errs), errs)
        self.assertEqual(vf.binding_notes(doc_for(SPLICED_QUOTE), "x.json", self.cfg, book), [])

    def test_the_run_search_reports_the_narrowest_run_that_holds_the_quote(self):
        """The widening stops at the first width that matches, so a quote inside
        one page is still one page and the recurrence ceiling counts it once."""
        book = book_with_a_folio_page()
        self.assertEqual(vf.quote_match(book, QUOTE_1).pages, [1])
        self.assertEqual(vf.quote_match(book, STRADDLE_QUOTE.split(RUNNING_HEAD)[0]
                                        + FOLIO_PAGE.strip()).pages, [6, 7])
        # the running head is still on every page, and still refused for it
        self.assertFalse(vf.quote_ok(book, RUNNING_HEAD)[0])


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

    def test_a_space_between_the_sign_and_its_operand_does_not_launder_it(self):
        """The whitespace condition reads the spacing the QUOTE chose.

        The quote is written by the agent, so the attacker picks it: the glued
        ``-1 ≤ …`` was refused as a sign the book lacks and the same flip with one
        space in it was stripped back to the book's own words and certified
        verbatim.  Both spellings, side by side, and both edges.
        """
        book = vf.BookText.from_text(
            "Lemma 4.2.1. In that case we have 1 <= m(F) for every set F that "
            "covers it, and the infimum is attained.\n\f")
        flipped = "%s1 <= m(F) for every set F that covers it, and the infimum is attained"
        for sign in ("-", "- ", "-  ", "− ", "— ", "— — "):
            with self.subTest(sign=sign):
                ok, why = vf.quote_ok(book, flipped % sign)
                self.assertFalse(ok, "a sign flip was laundered by its spacing: " + why)
                self.assertIn("OPERATOR", why)
        # and the trailing edge, where the operand is the last thing in the quote
        tail = "we have 1 <= m(F) for every set F that covers it, and the infimum is attained"
        self.assertTrue(vf.quote_ok(book, tail)[0])
        for sign in (" -", " —"):
            with self.subTest(sign=sign):
                self.assertFalse(vf.quote_ok(
                    book, "In that case we have 1" + sign)[0])

    def test_a_dash_beside_a_word_is_still_punctuation_at_either_edge(self):
        """The tightening is about numbers only: the case FR#46 fixed stands."""
        for d in ("— " + QUOTE_1, QUOTE_1 + " –", QUOTE_1 + " -",
                  "− " + QUOTE_1, QUOTE_1 + " — "):
            with self.subTest(quote=d[:6] + "..." + d[-6:]):
                self.assertEqual(vf.quote_ok(self.book, d), (True, "verbatim"))

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

    def test_the_relaxation_is_per_stem_not_per_path(self):
        """The unit check under the git one below: one added findings file per
        task, beside the task file the runbook committed before the run."""
        batch = {"findings/bob.json": "A", "tasks/bob.yaml": "M",
                 "findings/carol.json": "A", "tasks/carol.yaml": "M"}
        self.assertEqual(vf.check_changed(batch), [])
        landed = dict(batch, **{"findings/carol.json": "M"})
        problems = vf.check_changed(landed)
        self.assertTrue(any("findings/carol.json" in p for p in problems), problems)
        self.assertFalse(any("findings/bob.json" in p for p in problems), problems)

    def test_the_docstring_says_which_change_sets_the_stem_count_refuses(self):
        doc = " ".join((vf.check_changed.__doc__ or "").split())
        self.assertIn("A lone stem is never refused here", doc)
        self.assertNotIn("one that rewrites, empties, deletes or renames a path "
                         "that was already on the base branch is still refused", doc)


# --------------------------------------------------------------------------- #
# NEW — the batched pre-merge set is findings ADDED beside a task file MODIFIED
# --------------------------------------------------------------------------- #
class TestABatchOfSeveralTasksMerges(unittest.TestCase):
    """The relaxation counted PATHS, and a real batch is not all additions.

    ``references/runbook-server.md`` §4 commits the tasks to the entrypoint's
    merge target before the run, the templates write them with ``status: open``,
    and the worker prompt tells the worker to set that file's status -- so each
    batched task contributes ``findings/<id>.json`` as ``A`` and
    ``tasks/<id>.yaml`` as ``M``.  Requiring every path to be an addition
    therefore refused every merge of two or more tasks on the shipped argv, which
    passes no task id, while both shipped documents said such a batch merges.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMPDIR)
        self.root = pathlib.Path(self.tmp.name) / "entrypoint"
        self.root.mkdir()
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
                    "TEND_TASK_ID": ""}
        (self.root / "findings").mkdir()
        (self.root / "tasks").mkdir()
        self.git("init", "-q", "-b", "main", ".")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=str(self.root), check=True,
                       env=self.env, capture_output=True, text=True)

    def a_run_of(self, ids: list[str], *, landed: tuple[str, ...] = ()) -> None:
        """The runbook's sequence: tasks (and any landed findings) on main first."""
        for task in ids:
            (self.root / "tasks" / f"{task}.yaml").write_text(
                f"id: {task}\nstatus: open\n", encoding="utf-8")
        for task in landed:
            (self.root / "findings" / f"{task}.json").write_text(
                json.dumps({"schema_version": 2, "disagreements": [{"kind": "CONTENT"}]}),
                encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "the run's tasks land on the merge target")
        self.git("checkout", "-q", "-b", "batch")

    def workers_write(self, ids: list[str]) -> None:
        """What each worker does in its own worktree, merged into one branch."""
        for task in ids:
            (self.root / "findings" / f"{task}.json").write_text(
                json.dumps({"schema_version": 2, "item": f"Exercise 4.1.{len(task)}",
                            "disagreements": []}), encoding="utf-8")
            (self.root / "tasks" / f"{task}.yaml").write_text(
                f"id: {task}\nstatus: complete\n", encoding="utf-8")

    def gate(self, *extra: str) -> subprocess.CompletedProcess:
        """The shipped pre-merge argv: no ``--task-id``, nothing to substitute."""
        script = str(SKILL / "scripts" / "gate" / "check_changed_files.py")
        return subprocess.run(
            [sys.executable, script, "--base", "main", "--root", str(self.root), *extra],
            capture_output=True, text=True, env=self.env)

    def test_a_batch_of_one_two_or_three_tasks_merges(self):
        for n in (1, 2, 3):
            with self.subTest(tasks=n):
                self.setUp()
                ids = ["bob", "carol", "dave"][:n]
                self.a_run_of(ids)
                self.workers_write(ids)
                proc = self.gate()
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn(f"{2 * n} allowed path(s)", proc.stdout)
                self.tearDown()

    def test_a_batch_that_rewrites_a_landed_findings_file_is_still_refused(self):
        """The data-loss case the rule exists for, inside a passing batch."""
        self.a_run_of(["bob", "carol", "dave"], landed=("dave",))
        self.workers_write(["bob", "carol", "dave"])
        proc = self.gate()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        refusal = [line for line in proc.stderr.splitlines() if line.startswith("  - ")]
        self.assertEqual(len(refusal), 1, proc.stderr)
        self.assertIn("findings/dave.json", refusal[0])
        self.assertNotIn("findings/bob.json", refusal[0])   # named, not counted
        self.assertIn("--task-id", refusal[0])

    def test_a_batch_that_drops_a_landed_findings_file_is_still_refused(self):
        self.a_run_of(["bob", "carol", "dave"], landed=("dave",))
        self.workers_write(["bob", "carol"])
        (self.root / "findings" / "dave.json").unlink()
        proc = self.gate("--allow-empty")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("yours to drop", proc.stderr)

    def test_a_batch_whose_task_file_moves_without_its_findings_is_refused(self):
        """A stem that touches only its task file is not a batched contribution."""
        self.a_run_of(["bob", "carol"])
        self.workers_write(["bob"])
        (self.root / "tasks" / "carol.yaml").write_text("id: carol\nstatus: blocked\n",
                                                        encoding="utf-8")
        proc = self.gate()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("findings/carol.json", proc.stderr)

    def test_the_two_documents_say_what_the_rule_does(self):
        """Both were rewritten one clause too early: they described the per-path
        rule, which refused the batch they promise merges."""
        readme = (SKILL / "templates" / "tend" / "README.md.tmpl").read_text(encoding="utf-8")
        runbook = (SKILL / "references" / "runbook-server.md").read_text(encoding="utf-8")
        for name, text in (("README.md.tmpl", readme), ("runbook-server.md", runbook)):
            with self.subTest(document=name):
                flat = " ".join(text.split())
                self.assertNotIn("only when one of the paths was already on", flat)
                self.assertIn("findings file was already on", flat)


if __name__ == "__main__":
    unittest.main()
