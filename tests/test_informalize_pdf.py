"""`cf informalize pdf-command | fetch-pdf | overlay | check-site`: stages I6 and I7.

New code, for docs/proposals/informalize.md section 11, components 6 and 7 (its sections
9 and 10).  No TeX and no host here: the canonical PDF is a small invented file, the
markers and the build's receipt are written by hand into a fake host folder, and ssh and
rsync are replaced by a local copy, as tests/test_informalize_library.py does for stage
I1.  Four groups:

* `canonical_pdf.sh`'s own argument handling, which runs no build (the script end to end
  is tests/e2e/informalize_synthetic/driver.py, which has TeX and poppler);
* `pdf-command`: what it writes, what it prints, and each refusal -- a dirty repository, a
  repository with no commit, a harvested run nobody applied, a runtime that is not this
  checkout's;
* `fetch-pdf`: the dry run, the consent, a job that has not finished or failed, each check
  of what came back, the files it lands, the commit it makes, the I6 receipt, and that a
  second fetch over its own markers commit changes nothing;
* stage I7's pieces: the generated config and landing page (whose overlay link block the
  site build must write back unchanged), the workflows, the `.gitignore` lines, the one
  value of the export's record that a re-run keeps rather than committing again, and the
  refusals of `overlay` and `check-site` -- among them the `pdf-overlay/verso.json` that
  is not one, since `overlay` commits it.
"""
from __future__ import annotations

# Pin HOME before anything builds a workspace: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
# See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
for entry in (str(HERE), str(SKILL / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import _informalize_doc as doc                                # noqa: E402
import cf as cf_main                                          # noqa: E402
from _fakehost import FakeHost                                # noqa: E402
from cflib.errors import CfError                              # noqa: E402
from cflib.hashing import sha256_file                         # noqa: E402
from cflib.paths import WorkspacePaths                        # noqa: E402
from cflib.workspace import Workspace                         # noqa: E402
from informalize import overlay as ioverlay                   # noqa: E402
from informalize import pdf as ipdf                           # noqa: E402
from informalize import stages                                # noqa: E402
from informalize.approve import AGENT_ENVIRONMENT             # noqa: E402
from informalize.outrepo import GIT_LOCATION_ENV, OutputRepo  # noqa: E402

ID = "example-thm"
JOB = doc.JOB
URL = doc.SLOTS["library_url"]
MODULE = "ExampleLib.Topology.Glue.Basic"
HOST = "testbox"
SSH = "tester@testbox"
CANONICAL = SKILL / "templates" / "informalize" / "runtime" / "tools" / "canonical_pdf.sh"
#: a file with a PDF's signature: nothing here opens it, and the checks are by sha256
FAKE_PDF = b"%PDF-1.5\n% an invented canonical PDF\ntrailer\n%%EOF\n"
SHEETS = ("page-01.png", "page-01.svg")


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-informalize-pdf-", dir=os.environ.get("CF_TEST_TMP")))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def cf(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cf_main.cli(list(argv))
        except SystemExit as exc:                            # argparse
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# canonical_pdf.sh, without a build
# ---------------------------------------------------------------------------

@unittest.skipUnless(shutil.which("bash"), "the runtime's shell tools need bash")
class TestTheHostScriptReadsItsArguments(unittest.TestCase):
    """What it refuses before it builds anything: section 9's exit statuses."""

    def run_it(self, *argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(CANONICAL), *argv], capture_output=True, text=True,
                              cwd=str(cwd) if cwd else None)

    def test_it_needs_a_work_folder(self):
        done = self.run_it()
        self.assertEqual(done.returncode, 2)
        self.assertIn("usage", done.stderr)

    def test_a_source_commit_is_forty_hex(self):
        done = self.run_it("--source-commit", "nope", "/tmp/unused-by-this-test")
        self.assertEqual(done.returncode, 2)
        self.assertIn("40-hex", done.stderr)

    def test_it_is_run_from_the_output_repositorys_root(self):
        here = scratch(self)
        done = self.run_it(str(here / "work"), cwd=here)
        self.assertEqual(done.returncode, 2)
        self.assertIn("informal/", done.stderr)

    def test_help_prints_the_contract(self):
        done = self.run_it("--help")
        self.assertEqual(done.returncode, 0)
        for needle in ("canonical_pdf.sh", "pdf-receipt.json", "poppler"):
            with self.subTest(needle=needle):
                self.assertIn(needle, done.stdout)


# ---------------------------------------------------------------------------
# a workspace with an output repository and a fake host folder
# ---------------------------------------------------------------------------

class _Workspace(unittest.TestCase):
    """One informalize id whose repository holds an item table and a conventions file."""

    def setUp(self):
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in AGENT_ENVIRONMENT + GIT_LOCATION_ENV:
            os.environ.pop(name, None)
        base = scratch(self)
        self.root = base / "ws"
        self.host = base / f"informalize-{ID}"
        self.host.mkdir()
        code, out, err = cf("init", str(self.root), "--no-git", "-q")
        self.assertEqual(code, 0, out + err)
        workspace = Workspace.load(self.root, validate=False)
        workspace.data["hosts"] = {"allowlist": [HOST],
                                   HOST: {"ssh": SSH, "remote_root": str(self.host)}}
        workspace.save()
        self.paths = WorkspacePaths(self.root)
        self.repo = self.paths.informalize_repo(ID)
        self.cf("informalize", "init", ID, "--library", URL, "--commit", doc.COMMIT,
                "--module", MODULE, "--theorem", "ExampleLib.glue_theorem",
                "--prefix", "ExampleLib", "--text-license", "Apache-2.0",
                "--title", doc.SLOTS["title"], "--author", "A. Maintainer")
        self.write_json(self.repo / "informal" / "declarations.json", doc.DECLARATIONS)
        self.git("add", "informal/declarations.json")
        self.git("commit", "-qm", "declarations")

    def workspace(self) -> Workspace:
        return Workspace.load(self.root)

    def cf(self, *argv: str, code: int = 0) -> str:
        got, out, err = cf("--workspace", str(self.root), *argv)
        self.assertEqual(got, code, out + err)
        return out + err

    def git(self, *args: str) -> str:
        # an identity of its own: HOME is a temporary directory (tests/_hermetic.py), so
        # the machine's git names nobody
        identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        return subprocess.run(["git", "-C", str(self.repo), *identity, *args],
                              capture_output=True, text=True, check=True).stdout

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    @staticmethod
    def write_json(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    # -- the fake host folder ---------------------------------------------
    def plan(self) -> str:
        """`pdf-command`, and the commit it planned."""
        head = self.head()
        self.cf("informalize", "pdf-command", ID, "--host", HOST)
        return head

    def write_host(self, head: str, *, done: str = "0", pdf: bytes = FAKE_PDF,
                   receipt: dict | None = None, markers: dict | None = None,
                   sheets: tuple[str, ...] = SHEETS) -> Path:
        """The host folder after a job that built the tree pdf-command wrote."""
        work = self.host / "pdf" / head[:12]
        build = work / "a"
        (build / "proof-sheets").mkdir(parents=True, exist_ok=True)
        (build / f"{JOB}.pdf").write_bytes(pdf)
        import hashlib

        sha = hashlib.sha256(pdf).hexdigest()
        self.write_json(build / "markers.json", markers if markers is not None else {
            "schema": "formal-companion/informalize-markers", "schema_version": 1,
            "job": JOB, "source_commit": head, "pdf_sha256": sha, "pages": 1,
            "page_size_pt": [612.0, 792.0], "entries": []})
        for name in sheets:
            (build / "proof-sheets" / name).write_bytes(b"not really an image\n")
        self.write_json(work / "pdf-receipt.json", receipt if receipt is not None else {
            "schema": "formal-companion/informalize-pdf", "schema_version": 1, "job": JOB,
            "source_commit": head, "source_date_epoch": 1700000000,
            "pdf": {"sha256": sha, "bytes": len(pdf), "pages": 1},
            "page_size_pt": [612.0, 792.0], "second_build_sha256": sha,
            "markers_sha256": sha256_file(build / "markers.json"),
            "proof_sheets": list(sheets), "pdftex": "pdfTeX x", "latexmk": "Latexmk x",
            "poppler": "pdftoppm x", "inputs": {"src/main.tex": sha},
            "checks": {name: "ok" for name in ipdf.CHECKS}})
        (work / "DONE").write_text(done + "\n", encoding="utf-8")
        return work

    def fetch(self, **kwargs):
        return ipdf.fetch_pdf(self.workspace(), ID, HOST, execute=True, consent=True,
                              runner=FakeHost(SSH), **kwargs)


# ---------------------------------------------------------------------------
# pdf-command
# ---------------------------------------------------------------------------

class TestPdfCommand(_Workspace):

    def test_it_writes_the_tree_and_prints_the_lines(self):
        head = self.head()
        out = self.cf("informalize", "pdf-command", ID, "--host", HOST)
        to_host = self.paths.informalize_pdf_to_host(ID, head[:12])
        self.assertTrue((to_host / "src" / "informal" / "main.tex").is_file())
        self.assertTrue((to_host / "src" / "informal" / "tools" / "canonical_pdf.sh").is_file())
        self.assertTrue((to_host / ipdf.COMMAND_FILE).is_file())
        work = f"{self.host}/pdf/{head[:12]}"
        self.assertIn(f"mkdir -p {work}/src", out)
        self.assertIn(f"{SSH}:{work}/src/", out)
        self.assertIn(f"bash {ipdf.HOST_SCRIPT} --source-commit {head}", out)
        self.assertIn("setsid -f", out)
        self.assertIn(f"cf informalize fetch-pdf {ID} --host {HOST}", out)
        # it ran nothing: the tree is here and nothing is on the host
        self.assertFalse((self.host / "pdf").exists())

    def test_the_tree_is_the_commit_not_the_worktree(self):
        head = self.plan()
        self.assertFalse((self.paths.informalize_pdf_to_host(ID, head[:12]) / "src"
                          / "informal" / "stray.tex").exists())
        (self.repo / "informal" / "stray.tex").write_text("x\n", encoding="utf-8")
        out = self.cf("informalize", "pdf-command", ID, "--host", HOST, code=2)
        self.assertIn("uncommitted change", out)

    def test_a_runtime_that_is_not_this_checkouts_is_refused(self):
        (self.repo / "informal" / "tools" / "build_pdf.sh").write_text("# edited\n",
                                                                       encoding="utf-8")
        self.git("commit", "-qam", "edit the runtime")
        out = self.cf("informalize", "pdf-command", ID, "--host", HOST, code=2)
        self.assertIn("runtime", out)

    def test_a_harvested_run_nobody_applied_comes_first(self):
        run = f"{ID}-draft-1"
        plan = self.paths.run_dir(run)
        plan.mkdir(parents=True, exist_ok=True)
        (plan / "run.yaml").write_text(
            f"created: '2026-01-01T00:00:00Z'\ninformalize:\n  id: {ID}\n  phase: draft\n",
            encoding="utf-8")
        self.write_json(self.paths.receipt_json(run),
                        {"run_id": run, "informalize": {"id": ID, "phase": "draft"}})
        out = self.cf("informalize", "pdf-command", ID, "--host", HOST, code=2)
        self.assertIn(run, out)

    def test_the_host_folder_is_this_ids_own(self):
        workspace = Workspace.load(self.root, validate=False)
        workspace.data["hosts"][HOST]["remote_root"] = "/home/tester"
        workspace.save()
        out = self.cf("informalize", "pdf-command", ID, "--host", HOST, code=2)
        self.assertIn(f"informalize-{ID}", out)


# ---------------------------------------------------------------------------
# fetch-pdf
# ---------------------------------------------------------------------------

class TestFetchPdf(_Workspace):

    def test_a_dry_run_runs_nothing(self):
        head = self.plan()
        out = self.cf("informalize", "fetch-pdf", ID, "--host", HOST)
        self.assertIn("dry run", out)
        self.assertIn(f"test -f {self.host}/pdf/{head[:12]}/DONE", out)
        self.assertFalse(self.paths.informalize_pdf(ID, head[:12]).joinpath(f"{JOB}.pdf")
                         .exists())

    def test_executing_needs_consent(self):
        self.plan()
        out = self.cf("informalize", "fetch-pdf", ID, "--host", HOST, "--execute", code=2)
        self.assertIn("--i-have-consent", out)

    def test_a_pdf_nobody_planned_is_refused(self):
        with self.assertRaises(CfError) as caught:
            self.fetch()
        self.assertIn("plans a canonical PDF", caught.exception.message)

    def test_a_job_that_has_not_finished_is_refused(self):
        self.plan()
        with self.assertRaises(CfError) as caught:
            self.fetch()
        self.assertIn("DONE", caught.exception.message)

    def test_a_job_that_failed_is_refused_with_its_reason(self):
        head = self.plan()
        self.write_host(head, done="4")
        with self.assertRaises(CfError) as caught:
            self.fetch()
        self.assertIn("poppler", caught.exception.message)

    def test_it_fetches_checks_commits_and_records(self):
        head = self.plan()
        work = self.write_host(head)
        result = self.fetch()
        here = self.paths.informalize_pdf(ID, head[:12])
        for relative in (f"{JOB}.pdf", "markers.json", "pdf-receipt.json",
                         f"proof-sheets/{SHEETS[0]}"):
            with self.subTest(file=relative):
                self.assertTrue((here / relative).is_file())
        self.assertFalse((here / ".incoming").exists())
        self.assertEqual((self.repo / "informal" / "markers.json").read_bytes(),
                         (work / "a" / "markers.json").read_bytes())
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertTrue(result.committed)
        receipt = stages.read_receipt(self.paths, ID, "I6")
        self.assertEqual(receipt["checks"]["source_commit"], head)
        self.assertEqual(receipt["checks"]["pdf"]["bytes"], len(FAKE_PDF))
        self.assertTrue(receipt["checks"]["consent"]["i_have_consent"])
        self.assertIn("repo/informal/markers.json", receipt["outputs"])
        self.assertIn(f"pdf/{head[:12]}/proof-sheets/{SHEETS[0]}", receipt["outputs"])

    def test_a_second_fetch_over_its_own_markers_commit_changes_nothing(self):
        head = self.plan()
        self.write_host(head)
        self.fetch()
        after = self.head()
        self.assertNotEqual(after, head)                     # the markers commit
        self.assertEqual(ipdf.built_commit(OutputRepo(self.paths, ID), self.paths, ID), head)
        self.fetch()
        self.assertEqual(self.head(), after)

    def test_a_commit_with_more_than_the_markers_in_it_is_refused(self):
        head = self.plan()
        self.write_host(head)
        self.fetch()
        (self.repo / "informal" / "main.tex").write_text("% edited\n", encoding="utf-8")
        self.git("commit", "-qam", "the maintainer's own edit")
        with self.assertRaises(CfError) as caught:
            self.fetch()
        self.assertIn("plans a canonical PDF", caught.exception.message)

    def test_a_receipt_of_another_commit_is_refused(self):
        head = self.plan()
        work = self.write_host(head)
        receipt = json.loads((work / "pdf-receipt.json").read_text(encoding="utf-8"))
        self.write_json(work / "pdf-receipt.json", dict(receipt, source_commit="0" * 40))
        with self.assertRaises(CfError) as caught:
            self.fetch()
        self.assertIn("HEAD", caught.exception.message)

    def test_markers_of_another_pdf_are_refused(self):
        head = self.plan()
        work = self.write_host(head)
        markers = json.loads((work / "a" / "markers.json").read_text(encoding="utf-8"))
        self.write_json(work / "a" / "markers.json", dict(markers, pdf_sha256="a" * 64))
        with self.assertRaises(CfError) as caught:
            self.fetch()
        self.assertIn("markers.json", caught.exception.message)

    def test_a_check_the_host_did_not_pass_is_refused(self):
        head = self.plan()
        work = self.write_host(head)
        receipt = json.loads((work / "pdf-receipt.json").read_text(encoding="utf-8"))
        receipt["checks"]["proof_sheets"] = "failed"
        self.write_json(work / "pdf-receipt.json", receipt)
        with self.assertRaises(CfError) as caught:
            self.fetch()
        self.assertIn("proof_sheets", caught.exception.message)

    def test_a_proof_sheet_that_did_not_come_back_is_refused(self):
        head = self.plan()
        work = self.write_host(head)
        (work / "a" / "proof-sheets" / SHEETS[0]).unlink()
        with self.assertRaises(CfError) as caught:
            self.fetch()
        self.assertIn(SHEETS[0], caught.exception.message)

    def test_the_proof_sheets_approval_pins_what_it_wrote(self):
        head = self.plan()
        self.write_host(head)
        self.fetch()
        out = self.cf("informalize", "approve", ID, "proof-sheets", "--by", "A. Maintainer")
        self.assertIn("proof-sheets", out)
        approval = json.loads(self.paths.informalize_approval(ID, "proof-sheets")
                              .read_text(encoding="utf-8"))
        self.assertIn(f"pdf/{head[:12]}/{JOB}.pdf", approval["pins"])
        self.assertIn("stages/I6.json", approval["pins"])
        # a PDF that moved voids it
        self.paths.informalize_pdf(ID, head[:12]).joinpath(f"{JOB}.pdf").write_bytes(b"%PDF-\n")
        out = self.cf("informalize", "status", ID)
        self.assertIn("stale", out)


# ---------------------------------------------------------------------------
# stage I7's pieces
# ---------------------------------------------------------------------------

class TestTheGeneratedOverlayFiles(unittest.TestCase):
    """What `cf informalize overlay` writes before it exports anything."""

    def settings(self) -> dict:
        return {"id": ID, "text": {"title": "A & B, after ExampleLib", "job": JOB,
                                   "license": "Apache-2.0", "author": "A. Maintainer"},
                "library": {"url": URL, "commit": doc.COMMIT, "module": MODULE,
                            "theorem": "ExampleLib.glue_theorem", "prefix": "ExampleLib"}}

    def test_the_landing_page_is_what_the_site_build_writes_back(self):
        """The whole point of generating it: the build's injected link changes nothing."""
        raw = ioverlay.index_html(self.settings())
        tool = ioverlay.build_site_tool()
        again = tool.linked_index(raw, ioverlay.viewer_href(), ioverlay.LINK_TEXT)
        self.assertEqual(again, raw)
        self.assertIn(b"formal-companion-overlay-link", raw)

    def test_the_landing_page_escapes_what_it_prints(self):
        raw = ioverlay.index_html(self.settings()).decode("utf-8")
        self.assertIn("A &amp; B, after ExampleLib", raw)
        self.assertNotIn("A & B", raw)

    def test_the_two_hrefs_point_at_each_other(self):
        self.assertEqual(ioverlay.viewer_href(), "pdf-overlay/viewer/index.html")
        self.assertEqual(ioverlay.back_href(), "../../index.html")

    def test_the_workflows_are_copied_whole(self):
        files = ioverlay.workflow_files()
        self.assertEqual(sorted(files), [".github/workflows/check.yml",
                                         ".github/workflows/pages.yml"])
        pages = files[".github/workflows/pages.yml"].decode("utf-8")
        self.assertIn("upload-pages-artifact", pages)
        self.assertIn("serve.py --build-only", pages)
        # a Pages artifact, never a branch deploy (section 10)
        self.assertNotIn("gh-pages", pages)
        self.assertIn("check_generated.py",
                      files[".github/workflows/check.yml"].decode("utf-8"))

    def test_the_gitignore_lines_are_added_once(self):
        self.assertEqual(ioverlay.ignore_bytes(None).decode("utf-8").splitlines(),
                         list(ioverlay.IGNORE_LINES))
        already = ("\n".join(ioverlay.IGNORE_LINES) + "\n").encode("utf-8")
        self.assertEqual(ioverlay.ignore_bytes(already), already)
        kept = ioverlay.ignore_bytes(b"own-notes/\n").decode("utf-8").splitlines()
        self.assertEqual(kept[0], "own-notes/")
        self.assertEqual(sorted(kept[1:]), sorted(ioverlay.IGNORE_LINES))


class TestTheExportCommitSettles(unittest.TestCase):
    """`GENERATED.json`'s own commit moves when it is committed, so a re-run keeps it."""

    @staticmethod
    def record(commit: str, *, count: int = 2, sha: str = "a" * 64) -> bytes:
        document = {"schema": "formal-companion/overlay-generated", "schema_version": 3,
                    "annotations": {"count": count, "manifest_sha256": sha,
                                    "export": {"commit": commit, "convention": "informal-markers",
                                               "source_id": ID},
                                    "inputs": [{"path": "informal/items.json", "sha256": sha}]}}
        return (json.dumps(document, indent=2) + "\n").encode("utf-8")

    def test_only_the_commit_differs_so_the_old_record_is_kept(self):
        before = self.record("b" * 40)
        kept = ioverlay.keep_export_commit(before, self.record("c" * 40))
        self.assertEqual(kept, before)

    def test_anything_else_differs_and_the_new_record_stands(self):
        before = self.record("b" * 40)
        for other in (self.record("c" * 40, count=3),
                      self.record("c" * 40, sha="d" * 64),
                      b'{"schema": "formal-companion/overlay-generated"}\n',
                      b"not json\n"):
            with self.subTest(other=other[:40]):
                self.assertIsNone(ioverlay.keep_export_commit(before, other))

    def test_the_first_export_has_nothing_to_keep(self):
        self.assertIsNone(ioverlay.keep_export_commit(None, self.record("c" * 40)))
        # the same commit again: nothing to put back either
        same = self.record("b" * 40)
        self.assertIsNone(ioverlay.keep_export_commit(same, same))


class TestTheCardAndRegionChecks(unittest.TestCase):
    """What `check-site` says of a card with no declaration or no destination."""

    @staticmethod
    def record(**kwargs) -> dict:
        end = {"location": {"declaration": "ExampleLib.glue_theorem"},
               "extensions": {"org.mathlib-initiative.formal-companion.overlay": {
                   "destination": {"href": "https://example.org/blob/x.lean"}}}}
        end.update(kwargs)
        return {"id": "theorem-1.2", "object": [end]}

    def check(self, records: dict[str, dict]) -> list[str]:
        repo = mock.Mock()
        from overlay import sync as overlay_sync

        repo.read_bytes.return_value = overlay_sync.bundle_bytes(records)
        return ioverlay._card_problems(repo)                 # noqa: SLF001 - the unit

    def test_a_card_with_both_passes(self):
        self.assertEqual(self.check({"annotations/1/a.json": self.record()}), [])

    def test_a_card_with_no_end_is_named(self):
        found = self.check({"annotations/1/a.json": {"id": "theorem-1.2", "object": []}})
        self.assertEqual(len(found), 1)
        self.assertIn("no declaration", found[0])

    def test_an_end_without_a_destination_is_named(self):
        found = self.check({"annotations/1/a.json": self.record(extensions={})})
        self.assertEqual(len(found), 1)
        self.assertIn("no destination", found[0])

    def test_a_region_off_the_page_is_named(self):
        repo = mock.Mock()
        annotations = mock.Mock()
        annotations.records = {"annotations/1/a.json": {
            "id": "theorem-1.2",
            "subject": [{"type": "pdf", "location": {"page": 9, "rect": [0, 0, 1, 1]}}]}}
        with mock.patch("overlay.sync.read_annotations", return_value=annotations):
            found = ioverlay._region_problems(repo, 3)       # noqa: SLF001 - the unit
        self.assertEqual(len(found), 1)
        self.assertIn("page 9", found[0])


class TestTheI7Refusals(_Workspace):

    def test_overlay_wants_the_proof_sheets_approval(self):
        out = self.cf("informalize", "overlay", ID, "--pdf-url",
                      "https://example.org/x.pdf", code=2)
        self.assertIn("proof-sheets approval is absent", out)

    def test_overlay_wants_an_https_release_url(self):
        head = self.plan()
        self.write_host(head)
        self.fetch()
        out = self.cf("informalize", "overlay", ID, "--pdf-url", "ftp://example.org/x.pdf",
                      code=2)
        self.assertIn("https", out)

    def test_check_site_wants_an_overlay(self):
        head = self.plan()
        self.write_host(head)
        self.fetch()
        out = self.cf("informalize", "check-site", ID, code=2)
        self.assertIn("overlay.config.json", out)

    def test_the_verso_file_the_maintainer_brings_back_is_not_a_dirty_tree(self):
        """It is untracked until this command commits it, and it is this command's own."""
        head = self.plan()
        self.write_host(head)
        self.fetch()
        verso = self.repo / ioverlay.VERSO
        verso.parent.mkdir(parents=True, exist_ok=True)
        verso.write_text('{"schema": "formal-companion/overlay-verso"}\n', encoding="utf-8")
        # it gets past the clean check, to the approval that really is missing
        out = self.cf("informalize", "overlay", ID, "--pdf-url",
                      "https://example.org/x.pdf", code=2)
        self.assertIn("proof-sheets approval is absent", out)
        self.assertNotIn("uncommitted change", out)
        # anything else of the maintainer's is still work in the tree
        (self.repo / "informal" / "notes.md").write_text("mine\n", encoding="utf-8")
        out = self.cf("informalize", "overlay", ID, "--pdf-url",
                      "https://example.org/x.pdf", code=2)
        self.assertIn("uncommitted change", out)
        self.assertIn("informal/notes.md", out)


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
