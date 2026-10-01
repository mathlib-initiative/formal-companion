"""`cf overlay`: generate a companion's pdf-overlay/, and keep it honest.

Sections 5 and 6 of `docs/overlay.md`, against throwaway companion repositories
(`git init` in a temporary directory) and a synthetic annotation document built
here and written in the format's canonical form.  Nothing real is read: no
companion checkout, no PDF, no network, and nothing is written outside the
temporary directory.

Three things are worth saying about what is checked, because each is a rule
that has no other guard:

* `sync` is a byte-for-byte copy, so the test that matters is that
  `GENERATED.json` lists exactly the files under `templates/overlay/runtime/`
  and that a second run writes nothing -- a template that quietly took a
  substitution would fail both.
* rule 5 is the one exception to "never write to a companion repo", so the
  refusals are tested as behaviour: a symlinked `pdf-overlay`, a symlink
  inside it, and a file in it that no manifest listed.
* `check_generated.py` is run as a companion's CI runs it -- a child process,
  from the copy under `pdf-overlay/tools/`, with nothing but the standard
  library -- on a fresh sync and after a single byte changes.
"""
from __future__ import annotations

# Pin HOME before anything builds a workspace: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import hashlib
import io
import json
import os
import shutil
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

import cf as cf_main                                                  # noqa: E402
from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from cflib.errors import CfError                                      # noqa: E402
from formal_companion_annotations import validate as V                # noqa: E402
from overlay import config as overlay_config                          # noqa: E402
from overlay import export as overlay_export                          # noqa: E402
from overlay import sync as overlay_sync                              # noqa: E402

RUNTIME = SKILL / "templates" / "overlay" / "runtime"
VENDORED_SCHEMA = (SKILL / "vendor" / "formal_companion_annotations" / "schema"
                   / "annotations-v1.schema.json")

SOURCE_ID = "notes-pdf"
SOURCE_TITLE = "Elementary Sets: Lecture Notes (synthetic)"
#: A synthetic digest: 64 hex digits that are nobody's edition.
SOURCE_SHA = "a1b2c3d4" * 8
DECL = "Notes.Box.volume_nonneg"


@contextlib.contextmanager
def quiet():
    """Swallow the CLI's own output; the assertions read return values."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def cf(*argv: str) -> int:
    """Run the CLI in-process; a CfError becomes its exit code, as `cli()` does."""
    try:
        return int(cf_main.main(list(argv)) or 0)
    except CfError as exc:
        return int(getattr(exc, "exit_code", 2) or 2)


def runtime_paths() -> set[str]:
    """Every file under `templates/overlay/runtime/`, relative and posix."""
    found = set()
    for parent, _names, files in os.walk(RUNTIME):
        for name in files:
            found.add((Path(parent) / name).relative_to(RUNTIME).as_posix())
    return found


def document(*, page_count: int = 12, region_page: int = 7,
             href: str = "Notes/Basic/index.html",
             source_id: str = SOURCE_ID, **source_over) -> dict:
    """A small, valid annotation document about a source nobody published."""
    source = {
        "id": source_id,
        "kind": "pdf",
        "title": SOURCE_TITLE,
        "sha256": SOURCE_SHA,
        "page_size": {"height_pt": 792, "width_pt": 612},
    }
    if page_count is not None:
        source["physical_page_count"] = page_count
    source.update(source_over)
    region = {
        "type": "PdfRegionSelector",
        "page": region_page,
        "coordinate_space": {"origin": "top_left", "page_box": "CropBox",
                             "units": "normalized", "x_axis": "right",
                             "y_axis": "down"},
        "rect": {"x0": 0.15, "x1": 0.85, "y0": 0.2, "y1": 0.35},
    }
    return {
        "schema": "lean-companion/annotations",
        "schema_version": "1.0",
        "companion": {
            "commit": "0" * 40,
            "generated_at": "2026-09-26T12:00:00Z",
            "generator": "tests/test_overlay_cli.py",
            "name": "notes-companion",
            "repo": "git.example.org/example/notes-companion",
        },
        "declarations": [{
            "anonymous": False, "file": "Notes/Basic.lean", "id": DECL,
            "id_source": "name", "kind": "theorem", "module": "Notes.Basic",
            "name": DECL, "namespace": "Notes.Box", "private": False,
        }],
        "destinations": [{
            "declaration": DECL, "fragment": "Notes___Box___volume_nonneg",
            "href": href, "id": f"dest:{DECL}", "resolution": "exact",
            "site": "verso_literate",
        }],
        "items": [{
            "form": "numbered", "id": "lemma-1.2", "id_source": "label",
            "kind": "Lemma", "number": "1.2", "source": source_id,
        }],
        "links": [{
            "declaration": DECL, "id": f"lemma-1.2/key/{DECL}", "item": "lemma-1.2",
            "provenance": "prov-1", "role": "key",
        }],
        "locations": [{
            "id": f"lemma-1.2@p{region_page:03d}",
            "items": ["lemma-1.2"],
            "pages": {"basis": "physical", "first": region_page},
            "provenance": "prov-1",
            "target": {"selector": [region], "source": source_id},
        }],
        "provenance": [{
            "actor": "tool", "at": "2026-09-26", "confirmed_by_human": False,
            "id": "prov-1", "method": "pipeline",
        }],
        "sources": [source],
    }


def canonical(doc: dict) -> str:
    return V.canonical_dumps(V.canonicalize(doc))


class OverlayCase(unittest.TestCase):
    """A throwaway companion repository with nothing in it but git."""

    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="cf-overlay-cli-",
                                     dir=os.environ.get("CF_TEST_TMP") or None))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.root = root.resolve()
        self.companion = self.root / "companion"
        self.companion.mkdir()
        subprocess.run(["git", "init", "-q", str(self.companion)], check=True,
                       capture_output=True)
        self.overlay = self.companion / "pdf-overlay"

    # -- helpers ---------------------------------------------------------
    def init(self, **over) -> int:
        argv = ["overlay", "init", str(self.companion), "--source-id", SOURCE_ID,
                "--title", "Synthetic notes", "--file-name", "notes.pdf"]
        argv += over.pop("extra", [])
        with quiet():
            return cf(*argv)

    def sync(self, *flags: str) -> int:
        with quiet():
            return cf("overlay", "sync", str(self.companion), *flags)

    def check(self) -> tuple[int, str]:
        with quiet() as (out, err):
            code = cf("overlay", "check", str(self.companion))
        return code, out.getvalue() + err.getvalue()

    def write_document(self, doc: dict | None = None) -> Path:
        path = self.overlay / "annotations.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical(document() if doc is None else doc), encoding="utf-8")
        return path

    def ready(self, doc: dict | None = None) -> None:
        """init + a document + sync: the state every later command assumes."""
        self.assertEqual(self.init(), 0)
        self.write_document(doc)
        self.assertEqual(self.sync(), 0)

    def tool(self, name: str, *argv: str) -> subprocess.CompletedProcess:
        """Run one copied runtime tool the way a companion's CI does."""
        return subprocess.run(
            [sys.executable, str(self.overlay / "tools" / f"{name}.py"), *argv],
            cwd=str(self.companion), capture_output=True, text=True, timeout=300)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

class TestInit(OverlayCase):
    def test_it_writes_a_config_the_rules_accept(self):
        self.assertEqual(self.init(), 0)
        config = overlay_config.load(self.overlay / "overlay.config.json")
        self.assertEqual(config.source_id, SOURCE_ID)
        self.assertEqual(config.title, "Synthetic notes")
        self.assertEqual(config.file_name, "notes.pdf")
        self.assertEqual(config.route, "/__overlay_pdf__/notes.pdf")

    def test_it_refuses_to_overwrite(self):
        self.assertEqual(self.init(), 0)
        before = (self.overlay / "overlay.config.json").read_bytes()
        edited = before.replace(b'"Synthetic notes"', b'"Edited by hand"')
        (self.overlay / "overlay.config.json").write_bytes(edited)
        self.assertNotEqual(self.init(), 0)
        self.assertEqual((self.overlay / "overlay.config.json").read_bytes(), edited,
                         "a refusal must leave the companion's own edit alone")

    def test_force_overwrites(self):
        self.assertEqual(self.init(), 0)
        (self.overlay / "overlay.config.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.init(extra=["--force"]), 0)
        self.assertEqual(overlay_config.load(
            self.overlay / "overlay.config.json").source_id, SOURCE_ID)

    def test_the_file_name_defaults_to_the_source_id(self):
        with quiet():
            self.assertEqual(cf("overlay", "init", str(self.companion),
                                "--source-id", SOURCE_ID, "--title", "Synthetic notes"), 0)
        config = overlay_config.load(self.overlay / "overlay.config.json")
        self.assertEqual(config.file_name, f"{SOURCE_ID}.pdf")
        self.assertEqual(config.route, f"/__overlay_pdf__/{SOURCE_ID}.pdf")

    def test_it_refuses_a_file_name_that_is_not_one(self):
        with quiet():
            self.assertNotEqual(cf("overlay", "init", str(self.companion),
                                   "--source-id", SOURCE_ID, "--title", "T",
                                   "--file-name", "pdfs/notes.pdf"), 0)
        self.assertFalse(self.overlay.exists())

    def test_it_refuses_a_companion_that_is_not_a_directory(self):
        with quiet():
            self.assertNotEqual(cf("overlay", "init", str(self.root / "nowhere"),
                                   "--source-id", SOURCE_ID, "--title", "T"), 0)


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

class TestSync(OverlayCase):
    def test_it_writes_exactly_the_runtime_plus_generated(self):
        self.ready()
        written = {p.relative_to(self.overlay).as_posix()
                   for p in self.overlay.rglob("*") if p.is_file()}
        self.assertEqual(written,
                         runtime_paths() | {"GENERATED.json", "overlay.config.json",
                                            "annotations.json"})

    def test_every_copy_is_byte_identical_to_its_template(self):
        self.ready()
        for relative in sorted(runtime_paths()):
            with self.subTest(file=relative):
                self.assertEqual((self.overlay / relative).read_bytes(),
                                 (RUNTIME / relative).read_bytes())

    def test_generated_json_is_the_record_section_six_shows(self):
        self.ready()
        record = json.loads((self.overlay / "GENERATED.json").read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], "formal-companion/overlay-generated")
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["generator"]["name"], "formal-companion")
        self.assertEqual(record["instance_files"],
                         ["annotations.json", "overlay.config.json"])
        listed = [row["path"] for row in record["files"]]
        self.assertEqual(listed, sorted(listed), "files is sorted by path")
        self.assertEqual(set(listed), runtime_paths())
        self.assertNotIn("GENERATED.json", listed)
        for row in record["files"]:
            self.assertEqual(
                row["sha256"],
                hashlib.sha256((self.overlay / row["path"]).read_bytes()).hexdigest())

    def test_the_generator_commit_is_this_checkouts_head(self):
        self.ready()
        record = json.loads((self.overlay / "GENERATED.json").read_text(encoding="utf-8"))
        commit = overlay_sync.generator_commit()
        if commit is None:                                   # pragma: no cover
            self.assertNotIn("commit", record["generator"])
        else:
            self.assertEqual(record["generator"]["commit"], commit)

    def test_a_second_sync_is_a_no_op(self):
        self.ready()
        before = {p: p.stat().st_mtime_ns for p in self.overlay.rglob("*") if p.is_file()}
        result = overlay_sync.sync(self.companion)
        self.assertEqual(result.written, [])
        self.assertEqual(result.deleted, [])
        self.assertFalse(result.changed)
        after = {p: p.stat().st_mtime_ns for p in self.overlay.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "a no-op sync rewrote a file")

    def test_it_removes_a_file_a_previous_manifest_listed(self):
        self.ready()
        stale = self.overlay / "viewer" / "retired.js"
        stale.write_text("// a template that is gone\n", encoding="utf-8")
        record = json.loads((self.overlay / "GENERATED.json").read_text(encoding="utf-8"))
        record["files"].append({"path": "viewer/retired.js",
                                "sha256": hashlib.sha256(stale.read_bytes()).hexdigest()})
        record["files"].sort(key=lambda row: row["path"])
        (self.overlay / "GENERATED.json").write_text(json.dumps(record, indent=2) + "\n",
                                                     encoding="utf-8")
        result = overlay_sync.sync(self.companion)
        self.assertEqual(result.deleted, ["viewer/retired.js"])
        self.assertFalse(stale.exists())
        self.assertEqual(self.sync("--check"), 0)

    def test_it_prunes_a_directory_a_deletion_empties(self):
        self.ready()
        stale = self.overlay / "legacy" / "data" / "batch.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}\n", encoding="utf-8")
        record = json.loads((self.overlay / "GENERATED.json").read_text(encoding="utf-8"))
        record["files"].append({"path": "legacy/data/batch.json",
                                "sha256": hashlib.sha256(stale.read_bytes()).hexdigest()})
        record["files"].sort(key=lambda row: row["path"])
        (self.overlay / "GENERATED.json").write_text(json.dumps(record, indent=2) + "\n",
                                                     encoding="utf-8")
        overlay_sync.sync(self.companion)
        self.assertFalse((self.overlay / "legacy").exists())

    def test_it_refuses_a_file_no_manifest_listed(self):
        self.ready()
        (self.overlay / "notes-of-my-own.txt").write_text("mine\n", encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn("notes-of-my-own.txt", caught.exception.message)
        self.assertTrue((self.overlay / "notes-of-my-own.txt").is_file(),
                        "a refusal deletes nothing")

    def test_it_refuses_a_symlinked_pdf_overlay(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.overlay.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn("symlink", caught.exception.message)
        self.assertEqual(list(elsewhere.iterdir()), [], "nothing was written through it")

    def test_it_refuses_a_symlink_inside_the_directory(self):
        self.ready()
        (self.overlay / "viewer" / "viewer.css").unlink()
        (self.overlay / "viewer" / "viewer.css").symlink_to(self.root / "captured.css")
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn("symlink", caught.exception.message)
        self.assertFalse((self.root / "captured.css").exists(),
                         "nothing was written through the link")

    def test_it_restores_a_tampered_file(self):
        self.ready()
        css = self.overlay / "viewer" / "viewer.css"
        css.write_text(css.read_text(encoding="utf-8") + "\n/* tampered */\n",
                       encoding="utf-8")
        self.assertEqual(self.sync("--check"), 1)
        result = overlay_sync.sync(self.companion)
        self.assertIn("viewer/viewer.css", result.written)
        self.assertEqual(css.read_bytes(), (RUNTIME / "viewer" / "viewer.css").read_bytes())

    def test_it_writes_nothing_outside_pdf_overlay(self):
        before = {p for p in self.companion.rglob("*") if ".git" not in p.parts}
        self.ready()
        after = {p for p in self.companion.rglob("*")
                 if ".git" not in p.parts and self.overlay not in p.parents
                 and p != self.overlay}
        self.assertEqual(before, after)

    def test_the_shipped_format_schema_is_the_vendored_one_byte_for_byte(self):
        self.assertEqual((RUNTIME / "schema" / "annotations-v1.schema.json").read_bytes(),
                         VENDORED_SCHEMA.read_bytes())


class TestSyncCheck(OverlayCase):
    def test_it_passes_on_a_fresh_sync(self):
        self.ready()
        self.assertEqual(overlay_sync.sync_problems(self.companion), [])
        self.assertEqual(self.sync("--check"), 0)

    def test_it_reports_a_missing_file(self):
        self.ready()
        (self.overlay / "viewer" / "viewer.js").unlink()
        self.assertIn("missing: viewer/viewer.js",
                      overlay_sync.sync_problems(self.companion))
        self.assertEqual(self.sync("--check"), 1)

    def test_it_reports_a_changed_file(self):
        self.ready()
        css = self.overlay / "viewer" / "viewer.css"
        css.write_text(css.read_text(encoding="utf-8") + "/* x */\n", encoding="utf-8")
        self.assertIn("changed: viewer/viewer.css",
                      overlay_sync.sync_problems(self.companion))

    def test_it_reports_an_extra_file(self):
        self.ready()
        (self.overlay / "viewer" / "extra.js").write_text("//\n", encoding="utf-8")
        self.assertIn("extra: viewer/extra.js",
                      overlay_sync.sync_problems(self.companion))

    def test_it_reports_a_manifest_that_lists_what_the_templates_do_not_have(self):
        self.ready()
        record = json.loads((self.overlay / "GENERATED.json").read_text(encoding="utf-8"))
        record["files"].append({"path": "viewer/retired.js", "sha256": "0" * 64})
        record["files"].sort(key=lambda row: row["path"])
        (self.overlay / "GENERATED.json").write_text(json.dumps(record, indent=2) + "\n",
                                                     encoding="utf-8")
        problems = overlay_sync.sync_problems(self.companion)
        self.assertTrue(any("viewer/retired.js" in line for line in problems), problems)

    def test_it_reports_a_missing_manifest(self):
        self.ready()
        (self.overlay / "GENERATED.json").unlink()
        self.assertIn("missing: GENERATED.json",
                      overlay_sync.sync_problems(self.companion))

    def test_it_writes_nothing(self):
        self.ready()
        before = {p: p.read_bytes() for p in self.overlay.rglob("*") if p.is_file()}
        self.assertEqual(self.sync("--check"), 0)
        after = {p: p.read_bytes() for p in self.overlay.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

class TestCheck(OverlayCase):
    def test_it_accepts_a_generated_overlay_with_a_valid_document(self):
        self.ready()
        code, said = self.check()
        self.assertEqual(code, 0, said)

    def test_it_reports_a_document_that_is_not_canonical(self):
        self.ready()
        path = self.overlay / "annotations.json"
        path.write_text(json.dumps(document(), indent=4) + "\n", encoding="utf-8")
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("canonical", said)

    def test_it_reports_a_region_past_the_page_count(self):
        self.ready(document(page_count=5, region_page=7))
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("physical pages", said)

    def test_the_overlay_rule_names_the_region_itself(self):
        """The format's checker catches this first in a document it can read; the
        contract still asks `check` to hold the rule, so it is tested directly."""
        problems = overlay_sync.overlay_problems(
            document(page_count=5, region_page=7), SOURCE_ID)
        self.assertTrue(any("page 7" in line and "5 pages" in line for line in problems),
                        problems)

    def test_it_reports_an_href_the_viewer_will_not_link_to(self):
        self.ready(document(href="Notes\\Basic\\index.html"))
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("will not link to", said)

    def test_it_accepts_an_https_destination(self):
        self.ready(document(href="https://docs.example.org/Notes/Basic/index.html"))
        code, said = self.check()
        self.assertEqual(code, 0, said)

    def test_it_reports_a_source_the_config_does_not_name(self):
        self.ready(document(source_id="other-pdf"))
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn(SOURCE_ID, said)

    def test_it_reports_a_source_with_no_physical_page_count(self):
        self.ready(document(page_count=None))
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("physical_page_count", said)

    def test_it_reports_a_broken_config(self):
        self.ready()
        (self.overlay / "overlay.config.json").write_text(
            json.dumps({"schema": "formal-companion/overlay-config"}), encoding="utf-8")
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("missing keys", said)

    def test_it_reports_a_missing_document(self):
        self.assertEqual(self.init(), 0)
        self.assertEqual(self.sync(), 0)
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("annotations.json", said)

    def test_it_reports_a_directory_that_was_never_synced(self):
        self.assertEqual(self.init(), 0)
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("missing:", said)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

class _Exporter:
    """A stand-in for the `cf export` child: it records argv and writes a document."""

    def __init__(self, doc: dict | None = None, *, returncode: int = 0,
                 text: str | None = None):
        self.doc = doc
        self.returncode = returncode
        self.text = text
        self.argv: list[str] = []

    def __call__(self, argv):
        self.argv = list(argv)
        if self.returncode == 0:
            out = argv[argv.index("--out") + 1]
            Path(out).write_text(
                self.text if self.text is not None
                else canonical(self.doc if self.doc is not None else document()),
                encoding="utf-8")
        return subprocess.CompletedProcess(list(argv), self.returncode, "", "no data here")


class TestExport(OverlayCase):
    def test_it_writes_the_document_the_exporter_produced(self):
        self.assertEqual(self.init(), 0)
        self.assertEqual(self.sync(), 0)
        exporter = _Exporter()
        written = overlay_export.export(self.companion, convention="attribution-table",
                                        commit="b" * 40, runner=exporter)
        self.assertEqual(written, self.overlay / "annotations.json")
        self.assertEqual(written.read_text(encoding="utf-8"), canonical(document()))
        code, said = self.check()
        self.assertEqual(code, 0, said)

    def test_it_builds_the_cf_export_command_line(self):
        self.assertEqual(self.init(), 0)
        exporter = _Exporter()
        overlay_export.export(self.companion, convention="attribution-table",
                              commit="b" * 40, extra=["--data-dir", "overlay/data"],
                              runner=exporter)
        self.assertEqual(exporter.argv[:3],
                         ["export", "attribution-table", str(self.companion)])
        self.assertIn("--commit", exporter.argv)
        self.assertEqual(exporter.argv[-2:], ["--data-dir", "overlay/data"])
        out = Path(exporter.argv[exporter.argv.index("--out") + 1])
        self.assertNotIn(self.companion, out.parents,
                         "cf export refuses an --out inside the checkout")

    def test_a_failed_export_leaves_the_old_document_alone(self):
        self.ready()
        before = (self.overlay / "annotations.json").read_bytes()
        with self.assertRaises(CfError) as caught:
            overlay_export.export(self.companion, convention="attribution-table",
                                  commit="b" * 40, runner=_Exporter(returncode=2))
        self.assertIn("exited 2", caught.exception.message)
        self.assertEqual((self.overlay / "annotations.json").read_bytes(), before)

    def test_a_document_that_does_not_validate_is_not_written(self):
        self.ready()
        before = (self.overlay / "annotations.json").read_bytes()
        broken = document()
        broken["sources"][0].pop("sha256")
        with self.assertRaises(CfError) as caught:
            overlay_export.export(self.companion, convention="attribution-table",
                                  commit="b" * 40, runner=_Exporter(broken))
        self.assertIn("does not validate", caught.exception.message)
        self.assertEqual((self.overlay / "annotations.json").read_bytes(), before)

    def test_a_document_that_is_not_canonical_is_not_written(self):
        self.ready()
        before = (self.overlay / "annotations.json").read_bytes()
        with self.assertRaises(CfError):
            overlay_export.export(self.companion, convention="attribution-table",
                                  commit="b" * 40,
                                  runner=_Exporter(text=json.dumps(document(), indent=4)))
        self.assertEqual((self.overlay / "annotations.json").read_bytes(), before)

    def test_it_refuses_a_convention_that_writes_no_document(self):
        self.assertEqual(self.init(), 0)
        with self.assertRaises(CfError) as caught:
            overlay_export.export(self.companion, convention="site-pages",
                                  commit="b" * 40, runner=_Exporter())
        self.assertIn("site-pages", caught.exception.message)

    def test_the_conventions_are_the_cf_export_subcommands_that_write_a_document(self):
        """Pinned against the live parser: a new exporter must be listed here."""
        import argparse

        parser = argparse.ArgumentParser(prog="cf")
        subparsers = parser.add_subparsers(dest="command")
        cf_main.discover_commands(subparsers)
        export = subparsers.choices["export"]
        children: set[str] = set()
        for action in export._actions:                       # noqa: SLF001 - no public API
            if isinstance(action, argparse._SubParsersAction):   # noqa: SLF001
                children |= set(action.choices)
        self.assertEqual(children - {"site-pages"}, set(overlay_export.CONVENTIONS))

    def test_the_real_child_reports_its_own_failure(self):
        """No stub: `cf export` really runs, against a companion with no data."""
        self.assertEqual(self.init(), 0)
        with self.assertRaises(CfError) as caught:
            overlay_export.export(self.companion, convention="attribution-table",
                                  commit="b" * 40)
        self.assertIn("exited", caught.exception.message)


# ---------------------------------------------------------------------------
# check_generated.py, as a companion's CI runs it
# ---------------------------------------------------------------------------

class TestCheckGenerated(OverlayCase):
    def test_it_passes_on_a_fresh_sync(self):
        self.ready()
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_it_fails_after_one_byte_changes(self):
        self.ready()
        css = self.overlay / "viewer" / "viewer.css"
        css.write_bytes(css.read_bytes() + b" ")
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("viewer/viewer.css", done.stderr)

    def test_it_fails_on_a_file_the_manifest_does_not_list(self):
        self.ready()
        (self.overlay / "viewer" / "extra.js").write_text("//\n", encoding="utf-8")
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("viewer/extra.js", done.stderr)

    def test_it_fails_on_a_missing_file(self):
        self.ready()
        (self.overlay / "THIRD_PARTY.md").unlink()
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("THIRD_PARTY.md", done.stderr)

    def test_it_fails_on_a_config_section_three_refuses(self):
        self.ready()
        (self.overlay / "overlay.config.json").write_text(
            json.dumps({**json.loads(
                (self.overlay / "overlay.config.json").read_text(encoding="utf-8")),
                "serve": {"host": "0.0.0.0"}}), encoding="utf-8")
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("loopback", done.stderr)

    def test_it_fails_on_a_document_that_names_another_source(self):
        self.ready(document(source_id="other-pdf"))
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn(SOURCE_ID, done.stderr)

    def test_it_needs_only_the_directory_it_sits_in(self):
        """It is run from the companion root with no arguments and no PYTHONPATH."""
        self.ready()
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        done = subprocess.run(
            [sys.executable, "pdf-overlay/tools/check_generated.py"],
            cwd=str(self.companion), capture_output=True, text=True, timeout=300, env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_build_site_runs_against_a_freshly_synced_overlay(self):
        """The other tool a reader runs, on the directory `sync` just wrote."""
        self.ready()
        config = overlay_config.load(self.overlay / "overlay.config.json")
        site = self.companion.joinpath(*config.output_root.split("/"))
        site.mkdir(parents=True)
        index = site / config.index
        index.write_text("<!doctype html><html><body><p>site</p></body></html>\n",
                         encoding="utf-8")
        for run in (1, 2):
            done = self.tool("build_site")
            self.assertEqual(done.returncode, 0,
                             f"run {run}: {done.stdout + done.stderr}")
        self.assertEqual(index.read_text(encoding="utf-8").count(
            "<!-- formal-companion-overlay-link -->"), 1)
        self.assertTrue((site / config.mount / "receipt.json").is_file())

    def test_serve_help_runs_from_the_copy(self):
        self.ready()
        done = self.tool("serve", "--help")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


# ---------------------------------------------------------------------------
# the command itself
# ---------------------------------------------------------------------------

class TestTheCommand(OverlayCase):
    def test_cf_overlay_with_no_subcommand_refuses(self):
        with quiet():
            self.assertNotEqual(cf("overlay"), 0)

    def test_the_producers_are_mounted_with_their_own_flags(self):
        import argparse

        parser = argparse.ArgumentParser(prog="cf")
        subparsers = parser.add_subparsers(dest="command")
        cf_main.discover_commands(subparsers)
        overlay = subparsers.choices["overlay"]
        children = {}
        for action in overlay._actions:                      # noqa: SLF001 - no public API
            if isinstance(action, argparse._SubParsersAction):   # noqa: SLF001
                children = dict(action.choices)
        self.assertEqual(set(children),
                         {"init", "sync", "check", "export", "geometry", "destinations"})
        flags = {opt for act in children["geometry"]._actions   # noqa: SLF001
                 for opt in act.option_strings}
        for flag in ("--pdf", "--bbox-xhtml", "--first", "--last", "--kinds", "--section",
                     "--source-id", "--verifier-id", "--method", "--out"):
            self.assertIn(flag, flags)

    def test_a_producer_argv_is_rebuilt_from_the_namespace(self):
        from cfcmd import overlay as cfcmd_overlay

        parser = cfcmd_overlay.DESTINATIONS_PARSER
        args = parser.parse_args(["--site", "site", "--doc", "d.json", "--out", "o.json"])
        self.assertEqual(sorted(cfcmd_overlay.producer_argv(parser, args)),
                         sorted(["--doc", "d.json", "--out", "o.json", "--site", "site"]))

    def test_overlay_dir_refuses_a_pdf_overlay_that_is_a_file(self):
        self.overlay.write_text("not a directory\n", encoding="utf-8")
        with self.assertRaises(CfError):
            overlay_sync.overlay_dir(self.companion)

    def test_no_git_is_run_in_the_companion(self):
        """Rule 5: the generator commit comes from THIS checkout, not the companion."""
        self.ready()
        record = json.loads((self.overlay / "GENERATED.json").read_text(encoding="utf-8"))
        head = subprocess.run(["git", "-C", str(self.companion), "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        self.assertNotEqual(head.returncode, 0,
                            "the fixture companion has no commit, so a commit here "
                            "could only have come from this checkout")
        self.assertNotEqual(record["generator"].get("commit"), head.stdout.strip())


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
