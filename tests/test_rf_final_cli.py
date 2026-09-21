"""Review fixes for the CLI's scoping, rollback and printed hints (stdlib unittest).

One class per finding, and every case failed before the change it guards:

* ``cf sources add`` bound the companion and saved the workspace BEFORE it
  prepared the source, so a document the tool refused with exit 2 -- "almost
  certainly not the document this companion formalises" -- stayed in the audited
  scope, kept its registry overlay row and its prepared directory, and with
  ``--anchor`` became the companion's anchor edition.  A ``cf plan-run`` that had
  worked a moment earlier then failed blaming a source count.
* a differential ``cf plan-run`` staged EVERY companion in the workspace rather
  than the ones audited against the run's source: the worker prompt asserted that
  a companion of another book was one of "N INDEPENDENT formalizations" of this
  one, ``gate.json``'s ``repos`` accepted findings naming their declarations, the
  foreign statement tables were staged into ``data/``, and every companion's
  ``state.json`` recorded the run.  In a workspace whose companions span several
  sources no invocation planned a correctly scoped differential at all: both
  ``--source`` and the no-flag inference refused.
* the decl-index warning named the repos the index DOES cover instead of the ones
  it is missing, on a stream of its own, so under ``-q`` the only line the
  operator saw sent them to the healthy repos.
* ``cf adjudicate`` printed ``python3 scripts/cf.py adjudicate ...`` as its next
  step -- a path relative to the SKILL checkout, printed to an operator standing
  in the WORKSPACE -- one step after ``cf route`` had printed the spelling that
  works; three commands printed the same relative path out of ``--help``, because
  they hand their module docstring to argparse as ``description=``; and ``cf init``
  was the one hint printer in the skill that passed ``--workspace``, which left the
  operator outside the workspace so that ``cf onboard``'s own bare next-steps
  exited 2 when pasted.
* ``cfcmd.detect_proxies`` accepted a ``pdf_page`` of ``0``, a bool or a negative
  where ``plan.presets.item_pages`` drops them, which binds every quote of that
  item to page 0 instead of falling back to the gate's heading search.
* ``cf recheck`` wrote a ``state.json`` section that was not in
  ``cflib.state.SECTIONS``, so ``cf status --companion <name>`` dropped it.

Nothing here reads a private baseline, a companion repo or the network: the
book, the companions and the findings are synthetic (CLAUDE.md S1).

Run:  python3 -m unittest tests.test_rf_final_cli -v
"""

from __future__ import annotations

# Pin HOME before anything builds a workspace: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
# See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import ast
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                                   # noqa: E402
from cflib import log, state as state_mod                              # noqa: E402
from cflib.errors import CfError                                       # noqa: E402
from cflib.workspace import Workspace                                  # noqa: E402
from cflib.yamlio import read_yaml                                     # noqa: E402

TMPDIR = os.environ.get("CF_TMPDIR") or None

#: a synthetic booklet and a second, unrelated one -- no real edition is named
BOOK_ID = "a-booklet-first-printing"
BOOK_SHA = "a" * 64
OTHER_ID = "another-booklet-first-printing"
OTHER_SHA = "b" * 64

MINI_BOOK = (
    "Exercise 1.2.4 (Boolean closure). Show that the union of two elementary "
    "sets is elementary.\n\x0c"
    "Lemma 1.2.5 (Monotonicity). Show that the measure of a subset is no larger.\n"
)

CONVENTIONS = """# House conventions -- {name}

confirmed_by: the repo owner
confirmed_at: 2026-01-01

## 1. Total functions and junk values

- the measure of a non-measurable set is `0` by junk value.

## 2. Standing type and structure conventions

- unsigned quantities live in `NNReal`.

## 3. Naming and namespacing

- declarations live under `Booklet`.

## 4. Statement shape

- hypotheses are explicit.
"""


def _tmp(case: unittest.TestCase, prefix: str) -> pathlib.Path:
    """A throwaway directory; realpath because cflib resolves the paths it builds."""
    root = pathlib.Path(os.path.realpath(tempfile.mkdtemp(prefix=prefix, dir=TMPDIR)))
    case.addCleanup(shutil.rmtree, root, ignore_errors=True)
    return root


def _quiet(fn, *args, **kwargs):
    """Run a CLI entry point with its output captured."""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*args, **kwargs)


def _say(fn, *args, **kwargs) -> tuple:
    """(return value, everything printed) -- both streams, in one string."""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        out = fn(*args, **kwargs)
    return out, buf.getvalue()


def cf(*argv, cwd=None, env=None):
    """Run the CLI the way a user does, from *cwd*. Never touches the network."""
    environ = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environ.pop("CF_WORKSPACE", None)
    if env:
        environ.update(env)
    return subprocess.run([sys.executable, str(SCRIPTS / "cf.py"), *argv],
                          capture_output=True, text=True, cwd=cwd, env=environ)


def shell(command: str, cwd) -> subprocess.CompletedProcess:
    """Replay a printed hint verbatim, with the `cf` shim on PATH."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               PATH=os.pathsep.join([str(SKILL / "bin"), os.environ.get("PATH", "")]))
    env.pop("CF_WORKSPACE", None)
    return subprocess.run(command, shell=True, cwd=str(cwd), capture_output=True,
                          text=True, env=env)


def hints_in(res) -> list[str]:
    """Every printed `cf ...` / `cd ...` next step, in order, from BOTH streams.

    stdout alone is not the hint surface: a refusal's remediation goes out through
    ``log.warn``/``log.warn_block``, which write to stderr (`cf sources add` prints
    its `cf sources remove <id>` that way), so scraping one stream silently
    exempted every hint a command prints while it says no.
    """
    out = []
    for line in (res.stdout + res.stderr).splitlines():
        body = re.sub(r"^\s*(warn\s+)?(next|then|drop it with|bring it back with|"
                      r"once you have it):\s*", "", line).strip()
        if body.startswith("cf ") or body.startswith("cd "):
            out.append(body)
    return out


# ---------------------------------------------------------------------------
# FR#8 -- a refused source must not stay bound
# ---------------------------------------------------------------------------
class TestARefusedSourceIsNotLeftBound(unittest.TestCase):
    """`cf sources add` prepares first; a refusal leaves the workspace as it was."""

    def setUp(self):
        self.root = _tmp(self, "cf-rf-add-")
        self.ws = self.root / "ws"
        self.assertEqual(_quiet(cf_main.main, ["init", str(self.ws), "--no-git", "-q"]), 0)
        # a companion audited against one good source, written directly: this case
        # is about `cf sources add`, not about discovery.
        good = self.root / "booklet.md"
        good.write_text(MINI_BOOK, encoding="utf-8")
        ws = Workspace.load(self.ws, validate=False)
        ws.upsert_source(BOOK_ID, sha256=BOOK_SHA, title="A Booklet", edition="first printing",
                         pdf=str(good), role="audited", confirmed=True,
                         kinds=["Exercise", "Lemma"])
        repo = self.root / "repo"
        repo.mkdir()
        ws.upsert_companion("ours", repo=str(repo), adapter="docstring_tags",
                            sources=[BOOK_ID], anchor_source=BOOK_ID)
        ws.save()
        # prose that numbers nothing: the document the empty-source refusal is for
        self.bad = self.root / "appendix.md"
        self.bad.write_text("# Appendix\n\nNothing is numbered anywhere in here.\n",
                            encoding="utf-8")
        self.before = {
            name: (self.ws / name).read_bytes()
            for name in ("workspace.yaml", "registry.yaml")
            if (self.ws / name).exists()
        }

    def _add_the_bad_source(self, *extra: str) -> int:
        return _quiet(cf_main.cli, ["sources", "add", str(self.bad), "--for", "ours",
                                    "--title", "Appendix Manuscript", "--edition", "a1",
                                    "--workspace", str(self.ws), *extra])

    def test_a_refused_source_leaves_the_two_yaml_files_byte_identical(self):
        self.assertEqual(self._add_the_bad_source("--anchor"), 2)
        for name, body in self.before.items():
            with self.subTest(file=name):
                self.assertEqual((self.ws / name).read_bytes(), body,
                                 f"{name} changed although the add was refused")

    def test_a_refused_source_is_not_bound_prepared_or_made_the_anchor(self):
        self.assertEqual(self._add_the_bad_source("--anchor"), 2)
        ws = Workspace.load(self.ws, validate=False)
        companion = ws.data["companions"]["ours"]
        self.assertNotIn("appendix-manuscript", companion["sources"])
        self.assertEqual(companion["anchor_source"], BOOK_ID)
        self.assertNotIn("appendix-manuscript", ws.data.get("sources") or {})
        overlay = read_yaml(self.ws / "registry.yaml") if (self.ws / "registry.yaml").exists() else {}
        self.assertNotIn("appendix-manuscript", (overlay or {}).get("sources") or {})
        self.assertFalse(ws.paths.source_dir("appendix-manuscript").exists())

    def test_the_refusal_says_the_companion_is_unchanged(self):
        rc, said = _say(cf_main.cli, ["sources", "add", str(self.bad), "--for", "ours",
                                      "--title", "Appendix Manuscript", "--edition", "a1",
                                      "--workspace", str(self.ws)])
        self.assertEqual(rc, 2)
        self.assertIn("ours is unchanged", said)
        # `_prepare_source` says `ok prepared sources/<id>/ ...` before it runs the
        # refusals, and the rollback then deletes that directory: the warning has
        # to retract the earlier line, not merely contradict it.
        self.assertIn("ok   prepared  sources/appendix-manuscript/", said)
        self.assertIn("sources/appendix-manuscript/ was removed again", said)

    def _already_hold_the_bad_source(self) -> None:
        """Put the refused document in the workspace before the call under test.

        ``role: reference`` because the row has to leave a workspace that LOADS: an
        audited source no companion lists is refused by the validator, and a
        fixture that wrote one got its exit 2 from there rather than from the
        refusal under test.
        """
        ws = Workspace.load(self.ws, validate=False)
        from cflib.hashing import sha256_file

        ws.upsert_source("appendix-manuscript", sha256=sha256_file(self.bad),
                         title="Appendix Manuscript", edition="a1", pdf=str(self.bad),
                         role="reference", confirmed=False, registry="overlay")
        ws.save()
        self.assertEqual(Workspace.load(self.ws).validate(), [])

    def test_the_recovery_hint_the_refusal_prints_actually_removes_it(self):
        """A hint printed while saying NO is still a hint, and this one is on stderr.

        The rollback deliberately keeps a row the call did not create, so the only
        thing that takes it out is the command the refusal names -- which went out
        through ``warn_block``, a stream a stdout-only replay never reads.
        """
        self._already_hold_the_bad_source()
        res = cf("sources", "add", str(self.bad), "--for", "ours", "--title",
                 "Appendix Manuscript", "--edition", "a1", cwd=self.ws)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        offered = [h for h in hints_in(res) if h.startswith("cf sources remove")]
        self.assertEqual(offered, ["cf sources remove appendix-manuscript"],
                         res.stdout + res.stderr)
        replayed = shell(offered[0], cwd=self.ws)
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        after = Workspace.load(self.ws, validate=False)
        self.assertNotIn("appendix-manuscript", after.data.get("sources") or {})

    def test_a_source_the_workspace_already_held_is_not_removed_by_a_refusal(self):
        """The rollback undoes what THIS call created -- never a pre-existing row."""
        self._already_hold_the_bad_source()
        rc, said = _say(cf_main.cli, ["sources", "add", str(self.bad), "--for", "ours",
                                      "--title", "Appendix Manuscript", "--edition", "a1",
                                      "--workspace", str(self.ws)])
        self.assertEqual(rc, 2)
        self.assertIn("was already in this workspace and stays", said)
        after = Workspace.load(self.ws, validate=False)
        self.assertIn("appendix-manuscript", after.data.get("sources") or {},
                      "a row this call did not create must survive its refusal")
        self.assertNotIn("appendix-manuscript", after.data["companions"]["ours"]["sources"])

    def test_the_documented_next_command_still_works_after_the_refusal(self):
        """The verifier's aggravating fact: the refused add broke `cf plan-run`.

        A workspace whose companion is bound to two sources leaves the auto-pick
        path, so the NEXT command refused blaming a source count and never named
        the add.
        """
        self.assertEqual(self._add_the_bad_source("--anchor"), 2)
        ws = Workspace.load(self.ws, validate=False)
        self.assertEqual(ws.sources_of("ours"), [BOOK_ID])


# ---------------------------------------------------------------------------
# FR#11 -- a differential covers its own source's companions, and only those
# ---------------------------------------------------------------------------
class MultiSourceWorkspace(unittest.TestCase):
    """Three companions over two sources: ours+peer share one, outsider has its own.

    This is the repo's own canonical end state in miniature (``tests/e2e/
    multisource.sh`` step 13 finishes with four companions over three sources),
    and it is the shape no plan-run invocation used to handle.
    """

    def setUp(self):
        self.tmp = _tmp(self, "cf-rf-multi-")
        self.root = self.tmp / "ws"
        self.root.mkdir(parents=True)
        (self.root / "workspace.yaml").write_text(
            "version: 2\nworkspace:\n  name: mini\ncompanions: {}\nsources: {}\n",
            encoding="utf-8")
        ws = Workspace.load(self.root)
        p = ws.paths
        for sid in (BOOK_ID, OTHER_ID):
            p.source_dir(sid).mkdir(parents=True, exist_ok=True)
            p.source_text(sid).write_text(MINI_BOOK, encoding="utf-8")
            p.source_errata_md(sid).write_text("# errata\n", encoding="utf-8")

        # real (tiny) Lean repos: plan-run builds the gate's declaration index
        # from the companion sources, and an index missing a repo is not used
        self.repos = self.tmp / "repos"
        for name, decls in {"ours": ["elem_union"], "peer": ["union_elem"],
                            "outsider": ["gadget_one"]}.items():
            src = self.repos / name / "Src"
            src.mkdir(parents=True)
            (src / "A.lean").write_text(
                "".join(f"theorem {d} : True := trivial\n" for d in decls),
                encoding="utf-8")
            d = p.companion_dir(name)
            d.mkdir(parents=True)
            (d / "adapter.yaml").write_text(f"name: {name}\n", encoding="utf-8")
            (d / "conventions.md").write_text(CONVENTIONS.format(name=name), encoding="utf-8")
            (d / "statements.jsonl").write_text(
                json.dumps({"item": "Exercise 1.2.4", "decl": decls[0], "repo": name,
                            "source_id": BOOK_ID if name != "outsider" else OTHER_ID,
                            "source_sha": BOOK_SHA if name != "outsider" else OTHER_SHA})
                + "\n", encoding="utf-8")

        for sid, sha in ((BOOK_ID, BOOK_SHA), (OTHER_ID, OTHER_SHA)):
            ws.upsert_source(sid, sha256=sha, title="A Booklet", edition="first printing",
                             offset=0, offset_confidence=0.99,
                             offset_method="running_head_mode",
                             kinds=["Exercise", "Lemma"], chapters=[1],
                             role="audited", confirmed=True)
        for name, sid in (("ours", BOOK_ID), ("peer", BOOK_ID), ("outsider", OTHER_ID)):
            ws.upsert_companion(name, repo=str(self.repos / name), adapter="docstring_tags",
                                sources=[sid], anchor_source=sid, write_policy="read_only")
        ws.data["hosts"] = {"allowlist": []}
        ws.save()
        self.ws = Workspace.load(self.root)
        self.assertEqual(self.ws.validate(), [])
        for sid in (BOOK_ID, OTHER_ID):
            self._write_pairs(p, sid)

    def _write_pairs(self, p, source_id: str) -> None:
        names = ["ours", "peer"] if source_id == BOOK_ID else ["outsider"]
        p.pairs_dir(source_id).mkdir(parents=True, exist_ok=True)
        p.queue_json(source_id).write_text(json.dumps([
            {"item": "Exercise 1.2.4", "repos": names,
             "decls": {n: ["d_%s" % n] for n in names}, "book_page": 1},
        ]), encoding="utf-8")
        for name in names:
            p.targets_json(source_id, name).write_text(json.dumps([
                {"item": "Exercise 1.2.4", "decls": ["d_%s" % name],
                 "peers": [n for n in names if n != name],
                 "single_source": len(names) == 1, "book_page": 1, "section": "1.2"},
            ]), encoding="utf-8")
        p.reconciliation_json(source_id).write_text(json.dumps({
            "source_id": source_id,
            "source_sha": BOOK_SHA if source_id == BOOK_ID else OTHER_SHA,
            "task_universe": 1,
            "task_universe_items": ["Exercise 1.2.4"],
        }), encoding="utf-8")

    def plan(self, **over):
        from cfcmd import plan_run

        class A:
            workspace = str(self.root)
            preset = "differential-text"
            host = "local"
            model = None
            companion = None
            source_id = None
            run_id = "r1"
            chapters = None
            max_open_tasks = None
            max_cost = None
            i_have_consent = False
            force = False
            conventions_unconfirmed = False
            allow_sandbox_bypass = False
            allow_unreconciled = False

        for k, v in over.items():
            setattr(A, k, v)
        return plan_run.run(A())

    def run_doc(self, run_id: str = "r1") -> dict:
        return read_yaml(self.ws.paths.run_yaml(run_id))

    def gate_doc(self, run_id: str = "r1") -> dict:
        return json.loads(self.ws.paths.gate_json(run_id).read_text(encoding="utf-8"))


class TestADifferentialStagesOnlyItsOwnSourcesCompanions(MultiSourceWorkspace):
    def test_the_run_record_and_gate_name_only_this_sources_companions(self):
        self.assertEqual(_quiet(self.plan, companion="ours"), 0)
        self.assertEqual(self.run_doc()["repos"], ["ours", "peer"])
        self.assertEqual(self.gate_doc()["repos"], ["ours", "peer"])

    def test_only_this_sources_statement_tables_are_staged(self):
        self.assertEqual(_quiet(self.plan, companion="ours"), 0)
        staged = sorted(p.name for p in (self.ws.paths.run_dir("r1") / "data").iterdir())
        self.assertEqual(staged, ["ours.jsonl", "peer.jsonl"])
        self.assertEqual(sorted(self.run_doc()["data"]), ["ours.jsonl", "peer.jsonl"])

    def test_the_worker_prompt_does_not_claim_a_foreign_repo_formalises_this_book(self):
        self.assertEqual(_quiet(self.plan, companion="ours"), 0)
        prompt = (self.ws.paths.prompts_dir("r1") / "worker-system.md").read_text(
            encoding="utf-8")
        self.assertIn("2 INDEPENDENT", prompt)
        self.assertNotIn("outsider", prompt)

    def test_a_foreign_companion_records_no_run(self):
        self.assertEqual(_quiet(self.plan, companion="ours"), 0)
        theirs = self.ws.paths.companion_dir("outsider") / "state.json"
        runs = (json.loads(theirs.read_text(encoding="utf-8")).get("runs") or {}
                if theirs.exists() else {})
        self.assertNotIn("r1", runs,
                         "a companion of another source was recorded as planned into this run")
        for name in ("ours", "peer"):
            with self.subTest(companion=name):
                theirs = json.loads(
                    (self.ws.paths.companion_dir(name) / "state.json").read_text(
                        encoding="utf-8"))
                self.assertIn("r1", theirs.get("runs") or {})

    def test_the_conventions_gate_only_names_companions_of_this_run(self):
        """The operator-visible symptom: a refusal naming companions not in the run."""
        for d in ("ours", "peer", "outsider"):
            (self.ws.paths.companion_dir(d) / "conventions.md").write_text(
                "# House conventions\n\n## 1. Total functions\n\n- (example) drop me.\n",
                encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            _quiet(self.plan, companion="ours")
        message = str(caught.exception)
        self.assertIn("ours", message)
        self.assertIn("peer", message)
        self.assertNotIn("outsider", message)


class TestEveryDocumentedInvocationPlansTheRightDifferential(MultiSourceWorkspace):
    def test_source_narrows_instead_of_refusing(self):
        """`--source <id>` was the documented disambiguator and it dead-ended."""
        self.assertEqual(_quiet(self.plan, source_id=BOOK_ID), 0)
        self.assertEqual(self.run_doc()["repos"], ["ours", "peer"])

    def test_the_no_flag_inference_names_the_differentiable_sources(self):
        """It used to intersect over every companion and answer "share no source"."""
        self.ws.upsert_companion("peer", sources=[BOOK_ID, OTHER_ID])
        self.ws.upsert_companion("outsider", sources=[OTHER_ID])
        self.ws.save()
        with self.assertRaises(CfError) as caught:
            _quiet(self.plan)
        message = str(caught.exception)
        self.assertIn("2 differentiable sources", message)
        self.assertIn(BOOK_ID, message)
        self.assertIn(OTHER_ID, message)
        self.assertIn("--source", caught.exception.hint or "")

    def test_the_no_flag_path_picks_the_one_shared_source(self):
        self.assertEqual(_quiet(self.plan), 0)
        self.assertEqual(self.run_doc()["repos"], ["ours", "peer"])
        self.assertEqual(self.run_doc()["sources"][0]["id"], BOOK_ID)

    def test_a_source_no_companion_audits_is_refused_by_name(self):
        """`--source <a reference edition>` refuses instead of planning an empty run.

        The role matters, and it is the whole fixture: an AUDITED source no
        companion lists is refused one layer earlier, by the workspace validator
        ("role is audited but no companion lists it"), so a fixture that wrote
        ``role="audited"`` never reached this branch and passed byte-identically
        before the fix.  A REFERENCE edition nobody lists is a legal workspace --
        the bundled draft in ``tests/e2e/multisource.sh`` is one -- so it loads,
        and `plan-run` is the command that has to say no.
        """
        self.ws.upsert_source("a-ref-booklet", sha256="c" * 64, title="Third",
                              edition="x", role="reference", confirmed=True,
                              kinds=["Exercise"], chapters=[1])
        self.ws.save()
        self.assertEqual(Workspace.load(self.root).validate(), [],
                         "a reference edition nobody lists must still load")
        with self.assertRaises(CfError) as caught:
            _quiet(self.plan, source_id="a-ref-booklet")
        self.assertIn("no companion of this workspace is audited", str(caught.exception))
        self.assertIn("a-ref-booklet", str(caught.exception))
        self.assertIn("cf sources list", caught.exception.hint or "")

    def test_an_attack_run_still_audits_exactly_one_companion(self):
        self.assertEqual(
            _quiet(self.plan, preset="attack-hunt", companion="ours", source_id=BOOK_ID), 0)
        self.assertEqual(self.run_doc()["repos"], ["ours"])

    def test_naming_a_companion_still_refuses_a_source_it_does_not_audit(self):
        with self.assertRaises(CfError) as caught:
            _quiet(self.plan, companion="outsider", source_id=BOOK_ID)
        self.assertIn("not audited against", str(caught.exception))


# ---------------------------------------------------------------------------
# FR#29 -- the decl-index warning names what is MISSING
# ---------------------------------------------------------------------------
class TestTheDeclIndexWarningNamesTheMissingRepos(MultiSourceWorkspace):
    def setUp(self):
        super().setUp()
        # `peer`'s repo holds no Lean and no deps.json, so the index cannot vouch
        # for it -- and the gate's phantom-name check must stay OFF.
        shutil.rmtree(self.repos / "peer")
        (self.repos / "peer").mkdir(parents=True)

    def test_it_names_the_uncovered_repo_and_not_the_covered_one(self):
        rc, said = _say(self.plan, companion="ours")
        self.assertEqual(rc, 0)
        warning = [line for line in said.splitlines() if "does not cover" in line]
        self.assertEqual(len(warning), 1, said)
        self.assertIn("peer", warning[0])
        self.assertNotIn("ours", warning[0])

    def test_the_gate_still_fails_safe(self):
        self.assertEqual(_quiet(self.plan, companion="ours"), 0)
        self.assertIsNone(self.gate_doc()["decl_index"])

    def test_quiet_does_not_leave_the_misleading_line_as_the_only_one(self):
        """Under -q the bare print was ungated and the accurate warning was not."""
        log.set_level(0)
        self.addCleanup(log.set_level, 1)
        rc, said = _say(self.plan, companion="ours")
        self.assertEqual(rc, 0)
        self.assertNotIn("does not cover", said)


# ---------------------------------------------------------------------------
# FR#27 / FR#40 -- one spelling, and every printed hint runs from the workspace
# ---------------------------------------------------------------------------
class TestPrintedHintsRunFromTheWorkspace(unittest.TestCase):
    #: the literal that does not resolve from a workspace: there is no scripts/ there
    RELATIVE = "scripts/cf.py"
    #: the one non-hint the sweep below must not flag, with the reason it is not one.
    #: ``scripts/adapters/yamlio.py`` raises it when the adapters are imported WITHOUT
    #: ``cf.py``, which only happens from the repo checkout (CONTRIBUTING.md's
    #: reader), never from a workspace -- so it is read where the path resolves.
    NOT_A_HINT = ("adapters/yamlio.py",)

    def _routed_workspace(self) -> pathlib.Path:
        """A workspace with one unchallenged routed row, so `cf adjudicate` hints.

        The directory name carries a SPACE.  A hint is only pasteable if the path
        in it is quoted, and `cf route` writes one of these strings into
        ``ledger/routed/findings.<name>.jsonl`` and into the draft addressed to
        the repository's owner -- so on a workspace called `my audit` the command
        that shipped upstream answered `unrecognized arguments: audit/ws`.  A
        space-free fixture cannot see that.
        """
        root = _tmp(self, "cf-rf-hint ")
        repo = root / "repo"
        repo.mkdir()
        (root / "workspace.yaml").write_text(
            "version: 2\n"
            "workspace:\n  name: rf\n"
            "companions:\n"
            "  ours:\n"
            f"    repo: {repo}\n"
            "    adapter: attribution_table\n"
            f"    sources: [{BOOK_ID}]\n"
            f"    anchor_source: {BOOK_ID}\n"
            "sources:\n"
            f"  {BOOK_ID}:\n"
            f'    sha256: "{BOOK_SHA}"\n'
            "    title: A Booklet\n"
            '    edition: "first printing"\n'
            "    offset: 0\n"
            "    role: audited\n"
            "hosts:\n  allowlist: []\n", encoding="utf-8")
        ws = Workspace.load(root, validate=False)
        p = ws.paths
        p.source_dir(BOOK_ID).mkdir(parents=True, exist_ok=True)
        (p.source_dir(BOOK_ID) / "items.json").write_text(json.dumps({
            "source_id": BOOK_ID, "sha256": BOOK_SHA, "count": 1,
            "items": [{"item": "Exercise 1.2.4", "page": 1, "pdf_page": 1,
                       "chapter": 1, "kind": "Exercise", "line": 1}]}), encoding="utf-8")
        p.companion_dir("ours").mkdir(parents=True, exist_ok=True)
        (p.companion_dir("ours") / "statements.jsonl").write_text(json.dumps({
            "item": "Exercise 1.2.4", "item_raw": "Exercise 1.2.4", "decl": "Booklet.u",
            "file": "Src/A.lean", "line": 1, "statement": "theorem u : True",
            "repo": "ours", "source_id": BOOK_ID, "source_sha": BOOK_SHA}) + "\n",
            encoding="utf-8")
        corpus = root / "corpus" / "hunt"
        corpus.mkdir(parents=True)
        (corpus / "exercise-1-2-4.json").write_text(json.dumps({
            "item": "Exercise 1.2.4", "book_located": True, "source_id": BOOK_ID,
            "source_sha": BOOK_SHA, "matches": [], "coverage_notes": "",
            "disagreements": [{
                "kind": "CONTENT", "severity": "low", "concept": "the concept",
                "unfaithful_decls": ["ours:Booklet.u"], "repos_involved": ["ours"],
                "faithful": [], "reasoning": "the Lean statement drops a hypothesis",
                "suggested_fix": "add it", "book_quote": "Show that the union"}]}),
            encoding="utf-8")
        (corpus / "RECEIPT.json").write_text(json.dumps({
            "run_id": "hunt", "sources": {BOOK_ID: BOOK_SHA},
            "actual": {"count": 1}, "complete": True,
            "gate": {"ok": True, "checked": 1}}), encoding="utf-8")
        return root

    def test_cf_adjudicate_prints_a_hint_that_runs_from_the_workspace(self):
        root = self._routed_workspace()
        res = cf("adjudicate", cwd=root)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn(self.RELATIVE, res.stdout + res.stderr)
        hints = hints_in(res)
        self.assertTrue(hints, res.stdout)
        for hint in hints:
            with self.subTest(hint=hint):
                replayed = shell(hint, cwd=root)
                self.assertEqual(replayed.returncode, 0,
                                 f"{hint!r} -> rc {replayed.returncode}\n{replayed.stderr}")

    def test_the_emit_args_hint_runs_too(self):
        root = self._routed_workspace()
        res = cf("adjudicate", "--companion", "ours", "--emit-args", cwd=root)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn(self.RELATIVE, res.stdout + res.stderr)
        hints = hints_in(res)
        self.assertTrue(hints, res.stdout)
        for hint in hints:
            with self.subTest(hint=hint):
                # this one carries a `<result.json>` the operator fills in, so what
                # is replayed is the command with the placeholder dropped, which
                # leaves a plain `cf adjudicate --workspace <ws> --companion ours`.
                # rc 0 is the assertion: "it did not say No such file" passes for a
                # hint that resolves the workspace and then fails for some other
                # reason, which is most of what could go wrong here.
                replayed = shell(hint.split("--transcribe")[0].strip(), cwd=root)
                self.assertEqual(replayed.returncode, 0,
                                 f"{hint!r} -> rc {replayed.returncode}\n{replayed.stderr}")
                self.assertNotIn(self.RELATIVE, replayed.stdout + replayed.stderr)

    def test_cf_init_hands_over_a_shell_every_later_bare_hint_resolves_in(self):
        """FR#40: `cf init` was the one printer that passed --workspace.

        Pasting it left the operator outside the workspace, and `cf onboard`'s own
        next-steps -- bare, like the other hint sites -- then exited 2.
        """
        outside = _tmp(self, "cf-rf-init-")
        target = outside / "ws"
        res = cf("init", str(target), "--no-git", cwd=outside)
        self.assertEqual(res.returncode, 0, res.stderr)
        hints = hints_in(res)
        self.assertEqual(hints[0], f"cd {target}")
        self.assertNotIn("--workspace", " ".join(hints))
        # replay the chain: cd, then the bare hint, in the directory cd names
        replayed = shell("cf status", cwd=target)
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        self.assertNotIn("no workspace.yaml found", replayed.stderr)

    #: what makes a MODULE docstring printed output rather than a usage header
    PRINTS_ITS_DOCSTRING = ("description=__doc__", "epilog=__doc__")

    def test_no_printed_string_under_scripts_ships_a_relative_cf_py(self):
        """A static sweep over every string a command can print.

        Docstrings are exempt because they are read from the repo checkout
        (CONTRIBUTING.md's reader), with ONE exception that is not a detail: a
        module that hands its ``__doc__`` to argparse as ``description=`` or
        ``epilog=`` has turned that docstring into ``cf <command> --help`` output,
        read by an operator standing in the WORKSPACE.  `cf adjudicate --help`
        printed four ``python3 scripts/cf.py adjudicate ...`` lines that way, which
        is this finding's own defect on the one surface a docstring exemption
        hides.  So for those modules the module docstring is swept like any other
        string; their class and function docstrings stay exempt.
        """
        offenders = []
        for path in sorted(SCRIPTS.rglob("*.py")):
            if path.relative_to(SCRIPTS).as_posix() in self.NOT_A_HINT:
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            printed = any(marker in source for marker in self.PRINTS_ITS_DOCSTRING)
            exempt = {id(ast.get_docstring(n, clean=False)) for n in ast.walk(tree)
                      if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
            if not printed:
                exempt.add(id(ast.get_docstring(tree, clean=False)))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node.value) in exempt:
                    continue
                if self.RELATIVE in node.value:
                    offenders.append(f"{path.relative_to(SKILL)}:{node.lineno}")
        self.assertEqual(offenders, [], "a printed string ships a skill-relative cf.py")

    def test_no_workflow_script_saves_a_relative_cf_py_into_the_workspace(self):
        """The .js twins, which the Python sweep above cannot see.

        ``workflows/fleet-local.workflow.js`` builds the run's ``next`` block, which
        is saved into the workspace and read from there, and it had drifted from
        ``scripts/run/fleet_local.py`` -- the two printed different spellings of the
        same two steps.  A ``//`` comment line is the JS analogue of a docstring
        (read in the checkout, never printed), so only the rest is swept.
        """
        offenders = []
        for path in sorted((SKILL / "workflows").glob("*.js")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("//"):
                    continue
                if self.RELATIVE in line:
                    offenders.append(f"{path.relative_to(SKILL)}:{number}")
        self.assertEqual(offenders, [], "a workflow saves a skill-relative cf.py")

    def test_the_two_fleet_local_twins_print_the_same_two_steps(self):
        """The drift the sweep above would not have caught on its own."""
        from run import fleet_local

        text = fleet_local.instructions(pathlib.Path("/tmp/args.json"), 3, run_id="demo")
        self.assertIn("cf harvest demo --journal <workflow-dir> --gate", text)
        saved = [line.strip() for line
                 in (SKILL / "workflows" / "fleet-local.workflow.js").read_text(
                     encoding="utf-8").splitlines()
                 if line.strip().startswith(("harvest:", "requeue:"))]
        self.assertEqual(len(saved), 2, saved)
        self.assertTrue(saved[0].startswith("harvest: 'cf harvest '"), saved[0])
        self.assertTrue(saved[1].startswith("requeue: 'cf plan-run --preset "), saved[1])

    def test_one_helper_spells_every_cf_adjudicate_hint(self):
        """`cf route` and `cf adjudicate` print the same next step, one step apart.

        They used to disagree, and the fix put a second builder in ``cfcmd``
        alongside ``route.challenge_command``.  ``cflib.invocation`` is now the one
        builder; until ``scripts/route/`` can be touched, this pins the two against
        each other so they cannot drift again in silence.
        """
        from cflib import invocation
        from route import route as route_mod

        root = self._routed_workspace()
        self.assertEqual(
            route_mod.challenge_command(str(root), "ours"),
            invocation.adjudicate_command(root, "ours", "--challenge-only", "--emit-args"))
        # and with nothing to resolve the flag is left off rather than faked
        self.assertEqual(invocation.adjudicate_command("", "ours", "--emit-args"),
                         "cf adjudicate --companion ours --emit-args")
        self.assertEqual(route_mod.challenge_command("", "ours"),
                         invocation.adjudicate_command(
                             "", "ours", "--challenge-only", "--emit-args"))

    def test_the_local_fleet_prints_an_absolute_gate_script(self):
        """The second half of the same rule, in the function whose docstring states it."""
        from cflib.paths import skill_root
        from run import fleet_local

        text = fleet_local.instructions(pathlib.Path("/tmp/args.json"), 3, run_id="r1")
        self.assertIn("cf harvest r1 --journal <workflow-dir> --gate", text)
        self.assertIn(str(skill_root() / "scripts/gate/verify_findings.py"), text)
        self.assertNotIn("python3 scripts/", text)


# ---------------------------------------------------------------------------
# FR#33 -- detect_proxies drops a pdf_page the gate cannot use, like presets does
# ---------------------------------------------------------------------------
class TestDetectProxiesItemPagesSharesThePresetsValueTest(unittest.TestCase):
    KINDS = ["Definition", "Lemma", "Theorem", "Exercise", "Remark"]

    def test_unusable_pages_are_dropped_and_the_base_keying_is_kept(self):
        from cfcmd import detect_proxies

        self.assertEqual(
            detect_proxies._item_pdf_pages({"items": [
                {"item": "Lemma 1.2.5", "pdf_page": 0},        # dropped, like presets
                {"item": "Remark 1.3.1", "pdf_page": True},    # dropped, like presets
                {"item": "Theorem 1.1.9", "pdf_page": -3},     # dropped, like presets
                {"item": "In-text p.4"},                       # no pdf_page: dropped
                {"item": "Exercise 1.6.27(iii)", "pdf_page": 110},  # keyed by BASE, on purpose
                {"item": "Definition 1.2.2", "pdf_page": 34},
            ]}, self.KINDS),
            {"Exercise 1.6.27": 110, "Definition 1.2.2": 34})

    def test_the_two_builders_apply_the_same_value_test(self):
        from cfcmd import detect_proxies
        from plan import presets as presets_mod

        rows = {"Definition 1.2.2": {"pdf_page": 0},
                "Lemma 1.2.5": {"pdf_page": True},
                "Theorem 1.1.9": {"pdf_page": 34}}
        detected = detect_proxies._item_pdf_pages(
            [dict(row, item=item) for item, row in rows.items()], self.KINDS)
        self.assertEqual(sorted(detected), sorted(presets_mod.item_pages(rows)))


# ---------------------------------------------------------------------------
# FR#34 -- every section a command writes is a section `cf status` prints
# ---------------------------------------------------------------------------
class TestEverySectionWrittenIsASectionStatusKnows(unittest.TestCase):
    #: forwarders whose section name is a parameter, not a literal
    FORWARDERS = ("set_section", "merge_section", "state_section")

    def _written_sections(self) -> dict:
        found: dict = {}
        for path in sorted(SCRIPTS.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in self.FORWARDERS
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and isinstance(node.args[1].value, str)):
                    found.setdefault(node.args[1].value, []).append(
                        f"{path.relative_to(SKILL)}:{node.lineno}")
        return found

    def test_every_literal_section_name_is_in_SECTIONS(self):
        written = self._written_sections()
        self.assertIn("recheck", written, "cf recheck should still write its section")
        unknown = {k: v for k, v in written.items() if k not in state_mod.SECTIONS}
        self.assertEqual(unknown, {},
                         "a section cf status cannot print is a block it silently drops")

    def test_every_section_is_enumerated_in_the_layout_docstring(self):
        import cflib.state as module

        layout = module.__doc__ or ""
        for section in state_mod.SECTIONS:
            with self.subTest(section=section):
                self.assertIn(f'"{section}"', layout)

    def test_status_prints_a_recheck_block(self):
        from cfcmd import status

        _rc, said = _say(status._print_detail, {
            "version": 2, "name": "ours", "extract": {"rows": 10},
            "recheck": {"commit": "abc1234", "unchanged": 5, "changed": 2, "gone": 1},
        }, "ours")
        self.assertIn("recheck:", said)
        self.assertIn("unchanged: 5", said)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
