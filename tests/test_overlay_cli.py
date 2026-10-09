"""`cf overlay`: generate a companion's pdf-overlay/, feed it shards, and keep both honest.

Sections 5 and 6 of `docs/overlay.md`, against throwaway companion repositories
(`git init` in a temporary directory) and a synthetic annotation document that
a stand-in for `cf export` hands over.  Nothing real is read: no companion
checkout, no PDF, no network, and nothing is written outside the temporary
directory.

What is checked, beside the commands' own behaviour:

* `sync` is a byte-for-byte copy: `GENERATED.json` lists exactly the runtime
  templates and the vendored Literature Links package, and a second run writes
  nothing;
* rule 5: the refusals (a symlinked `pdf-overlay` or `annotations`, a link
  inside them, a file no manifest listed, a file in the shard directory that
  is not a shard) are behaviour, tested as such;
* `check_generated.py` runs as a companion's CI runs it -- a child process,
  from the copy under `pdf-overlay/tools/`, standard library only -- and fails
  on a tampered file, a tampered shard and a changed curation input.
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
from formal_companion_annotations import vocab as fmt                 # noqa: E402
from overlay import config as overlay_config                          # noqa: E402
from overlay import export as overlay_export                          # noqa: E402
from overlay import shards as overlay_shards                          # noqa: E402
from overlay import sync as overlay_sync                              # noqa: E402

RUNTIME = SKILL / "templates" / "overlay" / "runtime"
LINKS = SKILL / "vendor" / "literature_links"
MANIFEST = SKILL / "vendor" / "literature_links.manifest.json"

SOURCE_ID = "notes-pdf"
SOURCE_TITLE = "Elementary Sets: Lecture Notes (synthetic)"
#: A synthetic digest: 64 hex digits that are nobody's edition.
SOURCE_SHA = "a1b2c3d4" * 8
DECL = "Notes.Box.volume_nonneg"
#: The companion's own curation file the stand-in export says it read.
CURATION = "curation/table.json"


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


def tree(root: Path) -> set[str]:
    return {(Path(p) / n).relative_to(root).as_posix()
            for p, _d, files in os.walk(root) for n in files
            if "__pycache__" not in Path(p).parts and not n.endswith(".pyc")}


def generated_paths() -> set[str]:
    """Everything `sync` writes: the templates and the vendored Literature Links package."""
    return tree(RUNTIME) | {f"tools/literature_links/{p}" for p in tree(LINKS)}


def document(*, page_count: int = 12, region_page: int = 7,
             href: str = "Notes/Basic/index.html",
             source_id: str = SOURCE_ID, **source_over) -> dict:
    """A small, valid annotation document about a source nobody published."""
    source = {
        "id": source_id,
        "kind": "pdf",
        "title": SOURCE_TITLE,
        "sha256": SOURCE_SHA,
        "url": "https://notes.example.invalid/notes.pdf",
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
        "schema": fmt.ANNOTATION_SCHEMA,
        "schema_version": fmt.ANNOTATION_SCHEMA_VERSION,
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
            "form": "numbered", "id": "lemma-1.2.3", "id_source": "label",
            "kind": "Lemma", "number": "1.2.3", "source": source_id,
        }],
        "links": [{
            "declaration": DECL, "id": f"lemma-1.2.3/key/{DECL}", "item": "lemma-1.2.3",
            "provenance": "prov-1", "role": "key",
        }],
        "locations": [{
            "id": f"lemma-1.2.3@p{region_page:03d}",
            "items": ["lemma-1.2.3"],
            "pages": {"basis": "physical", "first": region_page},
            "provenance": "prov-1",
            "target": {"selector": [region], "source": source_id},
        }],
        "provenance": [{
            "actor": "tool", "at": "2026-09-26", "confirmed_by_human": False,
            "id": "prov-1", "method": "pipeline",
        }],
        "profiles": {"site": fmt.ANNOTATION_PROFILE_VERSIONS["site"]},
        "sources": [source],
    }


def canonical(doc: dict) -> str:
    return V.canonical_dumps(V.canonicalize(doc))


class _Exporter:
    """A stand-in for the `cf export` child: it records argv, writes a document and a report."""

    def __init__(self, doc: dict | None = None, *, returncode: int = 0,
                 text: str | None = None, inputs: tuple[str, ...] = (CURATION,)):
        self.doc = doc
        self.returncode = returncode
        self.text = text
        self.inputs = inputs
        self.argv: list[str] = []

    def __call__(self, argv):
        self.argv = list(argv)
        if self.returncode == 0:
            Path(argv[argv.index("--out") + 1]).write_text(
                self.text if self.text is not None
                else canonical(self.doc if self.doc is not None else document()),
                encoding="utf-8")
            Path(argv[argv.index("--report") + 1]).write_text(json.dumps(
                {"counts": {"inputs": [{"path": p} for p in self.inputs]}}), encoding="utf-8")
        return subprocess.CompletedProcess(list(argv), self.returncode, "", "no data here")


class OverlayCase(unittest.TestCase):
    """A throwaway companion repository with one curation file in it."""

    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="cf-overlay-cli-",
                                     dir=os.environ.get("CF_TEST_TMP") or None))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.root = root.resolve()
        self.companion = self.root / "companion"
        self.companion.mkdir()
        subprocess.run(["git", "init", "-q", str(self.companion)], check=True,
                       capture_output=True)
        (self.companion / "curation").mkdir()
        (self.companion / CURATION).write_text('{"curated": true}\n', encoding="utf-8")
        self.overlay = self.companion / "pdf-overlay"
        self.shards = self.companion / "annotations"

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

    def export(self, doc: dict | None = None, **kwargs) -> overlay_export.ExportResult:
        with quiet():
            return overlay_export.export(self.companion, convention="attribution-table",
                                         commit="b" * 40, runner=_Exporter(doc, **kwargs))

    def ready(self, doc: dict | None = None) -> None:
        """init + export + sync: the state every later command assumes."""
        self.assertEqual(self.init(), 0)
        self.export(doc)
        self.assertEqual(self.sync(), 0)

    def record(self) -> dict:
        return json.loads((self.overlay / "GENERATED.json").read_text(encoding="utf-8"))

    def write_record(self, record: dict) -> None:
        (self.overlay / "GENERATED.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    def shard(self) -> Path:
        return self.shards / "1.2.jsonl"

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
# export
# ---------------------------------------------------------------------------

class TestExport(OverlayCase):
    def test_it_writes_one_shard_per_section_and_records_the_export(self):
        self.assertEqual(self.init(), 0)
        result = self.export()
        self.assertEqual(result.shards, {"1.2.jsonl": 1})
        self.assertEqual(tree(self.shards), {"1.2.jsonl"})
        block = self.record()["annotations"]
        self.assertEqual(block["directory"], "annotations")
        self.assertEqual(block["extension"], overlay_shards.NAMESPACE)
        self.assertEqual(block["export"], {"commit": "0" * 40, "convention": "attribution-table",
                                           "source_id": SOURCE_ID})
        self.assertEqual(block["inputs"], [{
            "path": CURATION,
            "sha256": hashlib.sha256((self.companion / CURATION).read_bytes()).hexdigest()}])
        self.assertEqual(block["shards"], [{
            "path": "annotations/1.2.jsonl", "records": 1,
            "sha256": hashlib.sha256(self.shard().read_bytes()).hexdigest()}])
        self.assertEqual(overlay_shards.scan_problems({"1.2.jsonl": self.shard().read_bytes()}), [])

    def test_it_builds_the_cf_export_command_line(self):
        self.assertEqual(self.init(), 0)
        exporter = _Exporter()
        with quiet():
            overlay_export.export(self.companion, convention="attribution-table",
                                  commit="b" * 40, extra=["--data-dir", "overlay/data"],
                                  runner=exporter)
        self.assertEqual(exporter.argv[:3], ["export", "attribution-table", str(self.companion)])
        self.assertEqual(exporter.argv[-2:], ["--data-dir", "overlay/data"])
        for flag in ("--out", "--report"):
            out = Path(exporter.argv[exporter.argv.index(flag) + 1])
            self.assertNotIn(self.companion, out.parents, "cf export refuses a path inside the checkout")

    def test_an_input_outside_the_companion_is_not_recorded(self):
        self.assertEqual(self.init(), 0)
        self.export(inputs=(CURATION, "../elsewhere.json", "/etc/hosts", "missing.json"))
        self.assertEqual([row["path"] for row in self.record()["annotations"]["inputs"]], [CURATION])

    def test_an_export_that_names_no_input_writes_nothing(self):
        self.assertEqual(self.init(), 0)
        with self.assertRaises(CfError) as caught:
            self.export(inputs=())
        self.assertIn("names no curation file", caught.exception.message)
        self.assertFalse(self.shards.exists())

    def test_a_second_export_removes_a_stale_shard(self):
        self.ready()
        (self.shards / "9.9.jsonl").write_bytes(self.shard().read_bytes())
        result = self.export()
        self.assertEqual(result.deleted, ["9.9.jsonl"])
        self.assertEqual(tree(self.shards), {"1.2.jsonl"})

    def test_it_refuses_a_file_in_the_shard_directory_that_is_not_a_shard(self):
        self.ready()
        (self.shards / "README.md").write_text("mine\n", encoding="utf-8")
        before = self.shard().read_bytes()
        with self.assertRaises(CfError) as caught:
            self.export(document(region_page=8))
        self.assertIn("README.md", caught.exception.message)
        self.assertEqual(self.shard().read_bytes(), before, "a refusal writes nothing")

    def test_it_refuses_a_symlinked_shard_directory(self):
        self.assertEqual(self.init(), 0)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.shards.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(CfError) as caught:
            self.export()
        self.assertIn("symlink", caught.exception.message)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_failed_export_leaves_the_shards_alone(self):
        self.ready()
        before = self.shard().read_bytes()
        with self.assertRaises(CfError) as caught:
            self.export(returncode=2)
        self.assertIn("exited 2", caught.exception.message)
        self.assertEqual(self.shard().read_bytes(), before)

    def test_a_document_that_does_not_validate_writes_nothing(self):
        self.ready()
        before = self.shard().read_bytes()
        broken = document()
        broken["sources"][0].pop("sha256")
        with self.assertRaises(CfError) as caught:
            self.export(broken)
        self.assertIn("does not validate", caught.exception.message)
        self.assertEqual(self.shard().read_bytes(), before)
        with self.assertRaises(CfError):
            self.export(text=json.dumps(document(), indent=4))
        self.assertEqual(self.shard().read_bytes(), before)

    def test_a_record_that_cannot_be_converted_writes_nothing(self):
        self.ready()
        before = self.shard().read_bytes()
        doc = document()
        doc["locations"][0]["target"]["selector"][0]["coordinate_space"]["page_box"] = "TrimBox"
        with self.assertRaises(CfError) as caught:
            self.export(doc)
        self.assertIn("TrimBox", caught.exception.hint)
        self.assertEqual(self.shard().read_bytes(), before)

    def test_it_refuses_a_document_without_the_configured_source(self):
        self.assertEqual(self.init(), 0)
        with self.assertRaises(CfError) as caught:
            self.export(document(source_id="other-pdf"))
        self.assertIn(SOURCE_ID, caught.exception.message)
        self.assertFalse(self.shards.exists())

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
        with self.assertRaises(CfError) as caught, quiet():
            overlay_export.export(self.companion, convention="attribution-table", commit="b" * 40)
        self.assertIn("exited", caught.exception.message)


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

class TestSync(OverlayCase):
    def test_it_writes_exactly_the_runtime_and_the_package(self):
        self.ready()
        self.assertEqual(tree(self.overlay),
                         generated_paths() | {"GENERATED.json", "overlay.config.json"})

    def test_every_copy_is_byte_identical(self):
        self.ready()
        for relative in sorted(tree(RUNTIME)):
            with self.subTest(file=relative):
                self.assertEqual((self.overlay / relative).read_bytes(), (RUNTIME / relative).read_bytes())
        for relative in sorted(tree(LINKS)):
            with self.subTest(file=relative):
                self.assertEqual((self.overlay / "tools" / "literature_links" / relative).read_bytes(),
                                 (LINKS / relative).read_bytes())

    def test_generated_json_is_the_record_section_six_shows(self):
        self.ready()
        record = self.record()
        self.assertEqual((record["schema"], record["schema_version"]),
                         ("formal-companion/overlay-generated", 2))
        self.assertEqual(record["generator"]["name"], "formal-companion")
        self.assertEqual(record["instance_files"], ["overlay.config.json"])
        listed = [row["path"] for row in record["files"]]
        self.assertEqual(listed, sorted(listed))
        self.assertEqual(set(listed), generated_paths())
        for row in record["files"]:
            self.assertEqual(row["sha256"],
                             hashlib.sha256((self.overlay / row["path"]).read_bytes()).hexdigest())
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        expected = {"package": "literature-links", "version": manifest["version"], "commit": manifest["commit"]}
        if "tag" in manifest:                                # pragma: no cover - a tagged release
            expected["tag"] = manifest["tag"]
        self.assertEqual(record["format"], expected)
        self.assertEqual([row["path"] for row in record["annotations"]["shards"]], ["annotations/1.2.jsonl"])

    def test_a_second_sync_is_a_no_op(self):
        self.ready()
        before = {p: p.stat().st_mtime_ns for p in self.overlay.rglob("*") if p.is_file()}
        result = overlay_sync.sync(self.companion)
        self.assertEqual((result.written, result.deleted), ([], []))
        after = {p: p.stat().st_mtime_ns for p in self.overlay.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "a no-op sync rewrote a file")

    def test_sync_before_export_records_no_shard(self):
        self.assertEqual(self.init(), 0)
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.record()["annotations"],
                         {"directory": "annotations", "extension": overlay_shards.NAMESPACE, "shards": []})

    def test_it_records_hand_written_shards_when_nothing_exported_them(self):
        self.assertEqual(self.init(), 0)
        self.shards.mkdir()
        conversion = overlay_shards.convert(document())
        self.shard().write_bytes(overlay_shards.shard_bytes(conversion)["1.2.jsonl"])
        self.assertEqual(self.sync(), 0)
        block = self.record()["annotations"]
        self.assertNotIn("export", block)
        self.assertEqual(block["shards"][0]["path"], "annotations/1.2.jsonl")

    def test_it_refuses_an_exported_shard_edited_by_hand(self):
        self.ready()
        self.shard().write_bytes(self.shard().read_bytes().replace(b"Lemma 1.2.3", b"Lemma 1.2.4"))
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn("annotations/1.2.jsonl", caught.exception.message)
        self.assertIn("cf overlay export", caught.exception.hint)

    def test_it_refuses_a_shard_the_scan_refuses(self):
        self.assertEqual(self.init(), 0)
        self.shards.mkdir()
        self.shard().write_text('{"source": {"type": "pdf"}}\n', encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn("annotations/1.2.jsonl", caught.exception.message)

    def test_it_removes_a_file_a_previous_manifest_listed(self):
        self.ready()
        stale = self.overlay / "legacy" / "data" / "batch.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}\n", encoding="utf-8")
        record = self.record()
        record["files"].append({"path": "legacy/data/batch.json",
                                "sha256": hashlib.sha256(stale.read_bytes()).hexdigest()})
        self.write_record(record)
        result = overlay_sync.sync(self.companion)
        self.assertEqual(result.deleted, ["legacy/data/batch.json"])
        self.assertFalse((self.overlay / "legacy").exists())
        self.assertEqual(self.sync("--check"), 0)

    def test_it_refuses_a_file_no_manifest_listed(self):
        self.ready()
        (self.overlay / "notes-of-my-own.txt").write_text("mine\n", encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn("notes-of-my-own.txt", caught.exception.message)
        self.assertTrue((self.overlay / "notes-of-my-own.txt").is_file(), "a refusal deletes nothing")

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
        self.assertFalse((self.root / "captured.css").exists())

    def test_it_restores_a_tampered_file(self):
        self.ready()
        css = self.overlay / "viewer" / "viewer.css"
        css.write_text(css.read_text(encoding="utf-8") + "\n/* tampered */\n", encoding="utf-8")
        self.assertEqual(self.sync("--check"), 1)
        self.assertIn("viewer/viewer.css", overlay_sync.sync(self.companion).written)
        self.assertEqual(css.read_bytes(), (RUNTIME / "viewer" / "viewer.css").read_bytes())

    def test_it_writes_only_under_pdf_overlay_and_annotations(self):
        before = tree(self.companion)
        self.ready()
        after = {p for p in tree(self.companion)
                 if not p.startswith(("pdf-overlay/", "annotations/"))}
        self.assertEqual(before, after)


class TestSyncCheck(OverlayCase):
    def test_it_passes_on_a_fresh_sync(self):
        self.ready()
        self.assertEqual(overlay_sync.sync_problems(self.companion), [])
        self.assertEqual(self.sync("--check"), 0)

    def test_it_reports_missing_changed_and_extra_files(self):
        self.ready()
        (self.overlay / "viewer" / "viewer.js").unlink()
        css = self.overlay / "viewer" / "viewer.css"
        css.write_text(css.read_text(encoding="utf-8") + "/* x */\n", encoding="utf-8")
        (self.overlay / "viewer" / "extra.js").write_text("//\n", encoding="utf-8")
        problems = overlay_sync.sync_problems(self.companion)
        for line in ("missing: viewer/viewer.js", "changed: viewer/viewer.css", "extra: viewer/extra.js"):
            self.assertIn(line, problems)

    def test_it_reports_a_changed_missing_or_unlisted_shard(self):
        self.ready()
        self.shard().write_bytes(self.shard().read_bytes() + b"\n")
        self.assertIn("changed shard: annotations/1.2.jsonl", overlay_sync.sync_problems(self.companion))
        (self.shards / "2.1.jsonl").write_bytes(self.shard().read_bytes())
        self.assertIn("GENERATED.json does not list the shard annotations/2.1.jsonl",
                      overlay_sync.sync_problems(self.companion))
        self.shard().unlink()
        self.assertIn("missing shard: annotations/1.2.jsonl", overlay_sync.sync_problems(self.companion))

    def test_it_reports_a_format_block_that_is_not_the_vendored_release(self):
        self.ready()
        record = self.record()
        record["format"]["commit"] = "f" * 40
        self.write_record(record)
        self.assertTrue(any("format" in line for line in overlay_sync.sync_problems(self.companion)))

    def test_it_writes_nothing_and_reports_a_missing_manifest(self):
        self.ready()
        before = {p: p.read_bytes() for p in self.overlay.rglob("*") if p.is_file()}
        self.assertEqual(self.sync("--check"), 0)
        self.assertEqual(before, {p: p.read_bytes() for p in self.overlay.rglob("*") if p.is_file()})
        (self.overlay / "GENERATED.json").unlink()
        self.assertIn("missing: GENERATED.json", overlay_sync.sync_problems(self.companion))


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

class TestCheck(OverlayCase):
    def test_it_accepts_a_generated_overlay_with_current_shards(self):
        self.ready()
        code, said = self.check()
        self.assertEqual(code, 0, said)

    def test_it_reports_a_curation_input_that_changed_since_the_export(self):
        self.ready()
        (self.companion / CURATION).write_text('{"curated": false}\n', encoding="utf-8")
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn(f"{CURATION}: changed since the shards were exported", said)
        (self.companion / CURATION).unlink()
        self.assertIn(f"{CURATION}: gone", self.check()[1])

    def test_it_reports_an_export_that_records_no_input(self):
        self.ready()
        record = self.record()
        record["annotations"]["inputs"] = []
        self.write_record(record)
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("annotations.inputs: the export records no curation input", said)

    def test_it_reports_no_shards(self):
        self.assertEqual(self.init(), 0)
        self.assertEqual(self.sync(), 0)
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("holds no shard", said)

    def test_it_reports_shards_of_a_source_the_config_does_not_name(self):
        self.hand_written(lambda r: r["source"]["extensions"][overlay_shards.NAMESPACE].update(
            source_id="other-pdf"))
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn(f"no shard holds a record of the PDF '{SOURCE_ID}'", said)

    def test_it_reports_a_broken_config(self):
        self.ready()
        (self.overlay / "overlay.config.json").write_text(
            json.dumps({"schema": "formal-companion/overlay-config"}), encoding="utf-8")
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("missing keys", said)

    def test_it_reports_a_directory_that_was_never_synced(self):
        self.assertEqual(self.init(), 0)
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("missing:", said)

    def hand_written(self, *mutations) -> list[str]:
        """Hand-written shards (nothing exported them), synced, one record per mutation."""
        if not self.overlay.exists():
            self.assertEqual(self.init(), 0)
        if self.shards.exists():
            shutil.rmtree(self.shards)
        self.shards.mkdir()
        lines = []
        for mutate in mutations:
            record = json.loads(json.dumps(overlay_shards.convert(document()).records[0]))
            mutate(record)
            lines.append(overlay_shards.line(record) + "\n")
        self.shard().write_text("".join(lines), encoding="utf-8")
        self.assertEqual(self.sync(), 0)
        return overlay_sync.overlay_problems(self.companion, SOURCE_ID)

    def test_the_overlay_rules_hold_the_page_count(self):
        problems = self.hand_written(lambda r: r["location"].update(page=40))
        self.assertTrue(any("page 40" in line and "12 pages" in line for line in problems), problems)

    def test_the_overlay_rules_hold_the_targets(self):
        for uri, word in (("../../outside/index.html", "climbs out"), ("javascript:alert(1)", "neither"),
                          ("http://plain.example.invalid/x", "neither")):
            with self.subTest(uri=uri):
                problems = self.hand_written(lambda r, uri=uri: r["target"].update(uri=uri))
                self.assertTrue(any(word in line for line in problems), problems)
        self.assertEqual(self.hand_written(lambda r: r["target"].update(uri="https://docs.example.invalid/x")),
                         [])

    def test_the_overlay_rules_hold_one_pdf(self):
        problems = self.hand_written(lambda r: None, lambda r: r["source"]["pin"].update(sha256="e" * 64))
        self.assertTrue(any("2 different sources" in line for line in problems), problems)


# ---------------------------------------------------------------------------
# check_generated.py, as a companion's CI runs it
# ---------------------------------------------------------------------------

class TestCheckGenerated(OverlayCase):
    def test_it_passes_on_a_fresh_sync_and_leaves_no_bytecode(self):
        self.ready()
        for _ in (1, 2):
            done = self.tool("check_generated")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(list(self.overlay.rglob("__pycache__")), [])

    def test_it_fails_after_one_byte_changes(self):
        self.ready()
        css = self.overlay / "viewer" / "viewer.css"
        css.write_bytes(css.read_bytes() + b" ")
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("viewer/viewer.css", done.stderr)

    def test_it_fails_on_extra_and_missing_files(self):
        self.ready()
        (self.overlay / "viewer" / "extra.js").write_text("//\n", encoding="utf-8")
        (self.overlay / "THIRD_PARTY.md").unlink()
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("viewer/extra.js", done.stderr)
        self.assertIn("THIRD_PARTY.md", done.stderr)

    def test_it_fails_on_a_config_section_three_refuses(self):
        self.ready()
        config = json.loads((self.overlay / "overlay.config.json").read_text(encoding="utf-8"))
        (self.overlay / "overlay.config.json").write_text(
            json.dumps({**config, "serve": {"host": "0.0.0.0"}}), encoding="utf-8")
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("loopback", done.stderr)

    def test_it_fails_on_a_tampered_shard(self):
        self.ready()
        self.shard().write_bytes(self.shard().read_bytes().replace(b"Lemma 1.2.3", b"Lemma 1.2.4"))
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("annotations/1.2.jsonl: sha256", done.stderr)

    def test_it_fails_on_a_shard_the_scan_refuses(self):
        self.ready()
        self.shard().write_text('{"source": {"type": "pdf"}}\n', encoding="utf-8")
        record = self.record()
        record["annotations"]["shards"][0]["sha256"] = hashlib.sha256(self.shard().read_bytes()).hexdigest()
        self.write_record(record)
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("[SCHEMA]", done.stderr)

    def test_it_fails_on_a_file_in_the_shard_directory_it_does_not_list(self):
        self.ready()
        (self.shards / "notes.txt").write_text("mine\n", encoding="utf-8")
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("annotations/notes.txt", done.stderr)

    def test_it_fails_on_a_stale_export(self):
        self.ready()
        (self.companion / CURATION).write_text('{"curated": "again"}\n', encoding="utf-8")
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"{CURATION}: changed since the shards were exported", done.stderr)

    def test_it_fails_on_an_export_that_records_no_input(self):
        self.ready()
        record = self.record()
        del record["annotations"]["inputs"]
        self.write_record(record)
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("annotations.inputs: the export records no curation input", done.stderr)

    def test_it_fails_on_a_format_block_the_shipped_package_does_not_match(self):
        self.ready()
        record = self.record()
        record["format"]["version"] = "0.0.1"
        self.write_record(record)
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("format.version", done.stderr)

    def test_it_fails_on_a_shard_set_that_names_another_source(self):
        self.assertEqual(self.init(), 0)
        self.shards.mkdir()
        self.shard().write_bytes(overlay_shards.shard_bytes(
            overlay_shards.convert(document(source_id="other-pdf")))["1.2.jsonl"])
        self.assertEqual(self.sync(), 0)
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn(SOURCE_ID, done.stderr)

    def test_it_needs_only_the_directory_it_sits_in(self):
        """It is run from the companion root with no arguments and no PYTHONPATH."""
        self.ready()
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        done = subprocess.run([sys.executable, "-I", "pdf-overlay/tools/check_generated.py"],
                              cwd=str(self.companion), capture_output=True, text=True, timeout=300, env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_build_site_publishes_the_shards_at_the_site_root(self):
        """The other tool a reader runs, on the directory `sync` just wrote."""
        self.ready()
        config = overlay_config.load(self.overlay / "overlay.config.json")
        site = self.companion.joinpath(*config.output_root.split("/"))
        site.mkdir(parents=True)
        index = site / config.index
        index.write_text("<!doctype html><html><body><p>site</p></body></html>\n", encoding="utf-8")
        for run in (1, 2):
            done = self.tool("build_site")
            self.assertEqual(done.returncode, 0, f"run {run}: {done.stdout + done.stderr}")
        self.assertEqual(index.read_text(encoding="utf-8").count("<!-- formal-companion-overlay-link -->"), 1)
        self.assertEqual((site / "annotations" / "1.2.jsonl").read_bytes(), self.shard().read_bytes())
        self.assertEqual((site / config.mount / "GENERATED.json").read_bytes(),
                         (self.overlay / "GENERATED.json").read_bytes())
        receipt = json.loads((site / config.mount / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual([row["path"] for row in receipt["annotations"]["files"]], ["1.2.jsonl"])
        self.assertEqual(list(self.overlay.rglob("__pycache__")), [])

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
        self.assertEqual(set(children), {"init", "sync", "check", "export", "geometry", "destinations"})
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
        head = subprocess.run(["git", "-C", str(self.companion), "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        self.assertNotEqual(head.returncode, 0, "the fixture companion has no commit")
        self.assertNotEqual(self.record()["generator"].get("commit"), head.stdout.strip())


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
