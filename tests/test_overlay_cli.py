"""`cf overlay`: generate a companion's pdf-overlay/, feed it annotations, and keep both honest.

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
  inside them, a file no manifest listed, a file in the annotation directory
  that is not an annotation file) are behaviour, tested as such;
* `check_generated.py` runs as a companion's CI runs it -- a child process,
  from the copy under `pdf-overlay/tools/`, standard library only -- and fails
  on a tampered file, a tampered annotation file or bundle and a changed
  curation input, naming the file.
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
from unittest import mock

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
from overlay import defaults as overlay_defaults                      # noqa: E402
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
#: The one annotation file the synthetic document exports.
FILE = f"annotations/1.2/lemma-1.2.3__key__{DECL}.json"
BUNDLE = "annotations.bundle.json"
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


def manifest(companion: Path, paths) -> str:
    """What `sha256sum <paths> | sha256sum` prints, from the companion root."""
    lines = "".join(f"{hashlib.sha256((companion / p).read_bytes()).hexdigest()}  {p}\n" for p in sorted(paths))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


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
                 text: str | None = None, inputs: tuple[str, ...] = (CURATION,), counts: dict | None = None):
        self.doc = doc
        self.returncode = returncode
        self.text = text
        self.inputs = inputs
        self.counts = counts or {}
        self.argv: list[str] = []

    def __call__(self, argv):
        self.argv = list(argv)
        if self.returncode == 0:
            Path(argv[argv.index("--out") + 1]).write_text(
                self.text if self.text is not None
                else canonical(self.doc if self.doc is not None else document()),
                encoding="utf-8")
            Path(argv[argv.index("--report") + 1]).write_text(json.dumps(
                {"counts": {"inputs": [{"path": p} for p in self.inputs], **self.counts}}), encoding="utf-8")
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
        self.annotations = self.companion / "annotations"

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

    def annotation(self) -> Path:
        return self.companion / FILE

    def reseal(self) -> None:
        """Re-record the files and rewrite the bundle as they are now, as a hand edit of both would."""
        found = overlay_sync.read_annotations(self.companion)
        bundle = overlay_sync.bundle_bytes(found.records)
        (self.overlay / BUNDLE).write_bytes(bundle)
        record = self.record()
        record["annotations"].update(count=overlay_sync.annotation_count(found.files),
                                     manifest_sha256=overlay_sync.manifest_sha256(found.files),
                                     bundle={"path": BUNDLE, "sha256": hashlib.sha256(bundle).hexdigest()})
        self.write_record(record)

    def two_records(self) -> dict:
        """A second declaration linked to the lemma, and a lemma of 1.1 in its region: an item folder and defaults."""
        doc = document()
        doc["declarations"].append(dict(doc["declarations"][0], id=DECL + "_two", name=DECL + "_two"))
        doc["links"].append(dict(doc["links"][0], id=f"lemma-1.2.3/key/{DECL}_two", declaration=DECL + "_two"))
        doc["items"].append({"form": "numbered", "id": "lemma-1.1.1", "id_source": "label", "kind": "Lemma",
                             "number": "1.1.1", "source": SOURCE_ID})
        doc["locations"][0]["items"] = ["lemma-1.1.1", "lemma-1.2.3"]
        return doc

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
    def test_it_writes_one_file_per_annotation_in_its_section_folder_and_records_the_export(self):
        self.assertEqual(self.init(), 0)
        result = self.export()
        self.assertEqual(result.sections, {"1.2": 1})
        self.assertEqual(tree(self.annotations), {FILE.removeprefix("annotations/")})
        block = self.record()["annotations"]
        self.assertEqual(block["directory"], "annotations")
        self.assertEqual(block["extension"], overlay_shards.NAMESPACE)
        self.assertEqual(block["export"], {"commit": "0" * 40, "convention": "attribution-table",
                                           "source_id": SOURCE_ID})
        self.assertEqual(block["inputs"], [{
            "path": CURATION,
            "sha256": hashlib.sha256((self.companion / CURATION).read_bytes()).hexdigest()}])
        self.assertEqual((block["count"], block["manifest_sha256"]), (1, manifest(self.companion, [FILE])))
        self.assertNotIn("bundle", block, "sync writes the bundle")
        raw = self.annotation().read_bytes()
        self.assertEqual(raw, overlay_defaults.dumps(json.loads(raw)))
        self.assertIn('"uri": "../../Notes/Basic/index.html#Notes___Box___volume_nonneg"', raw.decode("utf-8"))
        self.assertEqual(overlay_shards.scan_problems({"1.2/x.json": raw}), [])

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

    def test_it_relays_the_warnings_of_the_export_report(self):
        self.assertEqual(self.init(), 0)
        counts = {"ignored_inputs": ["pdf-overlay/data/declaration-snippets.json"],
                  "snippets_unlocated": ["Notes.gone"]}
        self.assertEqual(self.export().warnings, [])
        real = overlay_export.export
        with mock.patch.object(overlay_export, "export",
                               lambda *a, **k: real(*a, runner=_Exporter(counts=counts), **k)), \
                quiet() as (_out, err):
            code = cf("overlay", "export", str(self.companion), "--convention", "attribution-table",
                      "--commit", "b" * 40)
        self.assertEqual(code, 0)
        self.assertIn("warn pdf-overlay/data/declaration-snippets.json is not read", err.getvalue())
        self.assertIn("warn no snippet for 1 of the ids the refinement lists", err.getvalue())
        self.assertIn("Notes.gone", err.getvalue())

    def test_an_input_outside_the_companion_is_not_recorded(self):
        self.assertEqual(self.init(), 0)
        self.export(inputs=(CURATION, "../elsewhere.json", "/etc/hosts", "missing.json"))
        self.assertEqual([row["path"] for row in self.record()["annotations"]["inputs"]], [CURATION])

    def test_an_export_that_names_no_input_writes_nothing(self):
        self.assertEqual(self.init(), 0)
        with self.assertRaises(CfError) as caught:
            self.export(inputs=())
        self.assertIn("names no curation file", caught.exception.message)
        self.assertFalse(self.annotations.exists())

    def test_a_second_export_removes_the_files_it_no_longer_writes(self):
        self.ready()
        (self.annotations / "9.9").mkdir()
        (self.annotations / "9.9" / "old.json").write_bytes(self.annotation().read_bytes())
        (self.annotations / "1.2" / "stale.json").write_bytes(self.annotation().read_bytes())
        result = self.export()
        self.assertEqual(result.deleted, ["1.2/stale.json", "9.9/old.json"])
        self.assertEqual(tree(self.annotations), {FILE.removeprefix("annotations/")})
        self.assertFalse((self.annotations / "9.9").exists(), "an emptied section folder goes too")

    def test_an_item_with_two_records_is_a_folder_with_its_defaults_and_a_stale_folder_goes(self):
        self.ready()
        result = self.export(self.two_records())
        self.assertEqual(result.deleted, [FILE.removeprefix("annotations/")])
        self.assertEqual(tree(self.annotations), {"_defaults.json", "1.1/lemma-1.1.1.json",
                                                  "1.2/lemma-1.2.3/_defaults.json",
                                                  f"1.2/lemma-1.2.3/key__{DECL}.json",
                                                  f"1.2/lemma-1.2.3/key__{DECL}_two.json"})
        block = self.record()["annotations"]
        self.assertEqual((block["count"], block["manifest_sha256"]),
                         (3, manifest(self.companion, [f"annotations/{p}" for p in tree(self.annotations)])))
        (self.annotations / "1.3" / "empty").mkdir(parents=True)
        result = self.export()
        self.assertEqual(result.deleted, ["1.1/lemma-1.1.1.json", "1.2/lemma-1.2.3/_defaults.json",
                                          f"1.2/lemma-1.2.3/key__{DECL}.json",
                                          f"1.2/lemma-1.2.3/key__{DECL}_two.json", "_defaults.json"])
        self.assertEqual(tree(self.annotations), {FILE.removeprefix("annotations/")})
        self.assertEqual(sorted(p.name for p in self.annotations.iterdir()), ["1.2"],
                         "the emptied item folder and an empty folder go")
        self.assertFalse((self.annotations / "1.2" / "lemma-1.2.3").exists())

    def test_a_second_export_keeps_a_file_renamed_only_in_case(self):
        """On APFS or NTFS the stale and the new name are one entry: removing the stale one must not lose it."""
        self.ready()
        renamed = DECL.replace("volume", "Volume")
        result = self.export(json.loads(json.dumps(document()).replace(DECL, renamed)))
        self.assertEqual(result.deleted, [FILE.removeprefix("annotations/")])
        self.assertEqual({entry.name for entry in os.scandir(self.annotations / "1.2")},
                         {f"lemma-1.2.3__key__{renamed}.json"})
        files, _ = overlay_sync.annotation_files(self.companion)
        self.assertEqual(len(files), 1)
        block = self.record()["annotations"]
        self.assertEqual((block["count"], block["manifest_sha256"]), (1, overlay_sync.manifest_sha256(files)))
        self.assertIn(renamed, next(iter(files.values())).decode("utf-8"))

    def test_it_records_the_export_before_it_writes_so_sync_refuses_what_a_failed_write_left(self):
        self.assertEqual(self.init(), 0)
        real = overlay_sync.write_file

        def lost(path, raw):
            if self.annotations not in path.parents:
                real(path, raw)

        with mock.patch.object(overlay_sync, "write_file", lost):
            with self.assertRaises(CfError) as caught:
                self.export()
        self.assertIn("not the ones the export wrote", caught.exception.message)
        self.assertIn(FILE, caught.exception.hint)
        block = self.record()["annotations"]
        self.assertEqual((block["export"]["convention"], block["count"]), ("attribution-table", 1))
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn("not what `cf overlay export` wrote", caught.exception.message)

    def test_an_export_that_dies_writing_a_file_has_recorded_itself_so_sync_refuses_what_it_left(self):
        self.assertEqual(self.init(), 0)
        real = overlay_sync.write_file

        def full(path, raw):
            if self.annotations in path.parents:
                raise OSError("no space left on device")
            real(path, raw)

        with mock.patch.object(overlay_sync, "write_file", full):
            with self.assertRaises(OSError):
                self.export()
        block = self.record()["annotations"]
        self.assertEqual((block["export"]["convention"], block["count"]), ("attribution-table", 1))
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn("not what `cf overlay export` wrote", caught.exception.message)

    def test_an_export_that_dies_removing_a_file_has_recorded_itself_so_sync_refuses_what_it_left(self):
        self.assertEqual(self.init(), 0)
        stale = self.annotations / "9.9" / "old.json"
        stale.parent.mkdir(parents=True)
        stale.write_text(json.dumps(overlay_shards.convert(document()).records[0]), encoding="utf-8")
        with mock.patch.object(Path, "unlink", side_effect=OSError("died")):
            with self.assertRaises(OSError):
                self.export()
        self.assertIn("export", self.record()["annotations"])
        self.assertTrue(stale.is_file())
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn("not what `cf overlay export` wrote", caught.exception.message)

    def test_it_refuses_anything_in_the_annotation_directory_that_is_not_an_annotation_file(self):
        for extra in ("README.md", "1.2/notes.txt", "1.2/sub/deeper/x.json", "1.2.jsonl", "1.2/.hidden.json",
                      "1.2/_defaults.json"):
            with self.subTest(extra=extra):
                if self.overlay.exists():
                    shutil.rmtree(self.overlay)
                    shutil.rmtree(self.annotations)
                self.ready()
                path = self.annotations / extra
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("mine\n", encoding="utf-8")
                before = self.annotation().read_bytes()
                with self.assertRaises(CfError) as caught:
                    self.export(document(region_page=8))
                self.assertIn(extra.removesuffix("/x.json"), caught.exception.message)
                self.assertEqual(self.annotation().read_bytes(), before, "a refusal writes nothing")
                self.assertTrue(path.is_file(), "nothing else is ever deleted")

    def test_it_removes_the_shards_a_previous_export_recorded_and_nothing_else(self):
        """The layout before one file per annotation: `<section>.jsonl` shards listed in GENERATED.json."""
        self.assertEqual(self.init(), 0)
        self.annotations.mkdir()
        old = self.annotations / "1.2.jsonl"
        old.write_text('{"old": 1}\n', encoding="utf-8")
        (self.overlay / "GENERATED.json").write_text(json.dumps({
            "schema": "formal-companion/overlay-generated", "schema_version": 2, "files": [],
            "annotations": {"directory": "annotations", "shards": [
                {"path": "annotations/1.2.jsonl", "records": 1,
                 "sha256": hashlib.sha256(old.read_bytes()).hexdigest()}]}}), encoding="utf-8")
        old.write_text('{"old": 2}\n', encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            self.export()
        self.assertIn("1.2.jsonl", caught.exception.message, "a shard edited since is somebody's file")
        old.write_text('{"old": 1}\n', encoding="utf-8")
        result = self.export()
        self.assertEqual(result.deleted, ["1.2.jsonl"])
        self.assertEqual(tree(self.annotations), {FILE.removeprefix("annotations/")})
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.record()["schema_version"], 3)

    def test_it_refuses_folder_defaults_above_the_annotation_directory_before_it_writes(self):
        self.ready()
        before = self.annotation().read_bytes(), self.record()
        for text in ('{"note": "from above"}\n', "[]\n"):
            with self.subTest(text=text):
                (self.companion / "_defaults.json").write_text(text, encoding="utf-8")
                with self.assertRaises(CfError) as caught:
                    self.export(document(region_page=8))
                self.assertIn("_defaults.json: ", caught.exception.message)
                self.assertEqual((self.annotation().read_bytes(), self.record()), before)

    def test_it_refuses_a_symlinked_annotation_directory(self):
        self.assertEqual(self.init(), 0)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.annotations.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(CfError) as caught:
            self.export()
        self.assertIn("symlink", caught.exception.message)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_it_refuses_a_link_inside_the_annotation_directory_and_removes_nothing_through_it(self):
        self.ready()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "x.json").write_bytes(self.annotation().read_bytes())
        (self.annotations / "1.2" / "evil").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(CfError) as caught:
            self.export(document(region_page=8))
        self.assertIn("annotations/1.2/evil", caught.exception.message)
        self.assertTrue((outside / "x.json").is_file())
        self.assertIn("annotations/1.2/evil: not an annotation file", self.tool("check_generated").stderr)

    def test_a_failed_export_leaves_the_files_alone(self):
        self.ready()
        before = self.annotation().read_bytes()
        with self.assertRaises(CfError) as caught:
            self.export(returncode=2)
        self.assertIn("exited 2", caught.exception.message)
        self.assertEqual(self.annotation().read_bytes(), before)

    def test_a_document_that_does_not_validate_writes_nothing(self):
        self.ready()
        before = self.annotation().read_bytes()
        broken = document()
        broken["sources"][0].pop("sha256")
        with self.assertRaises(CfError) as caught:
            self.export(broken)
        self.assertIn("does not validate", caught.exception.message)
        self.assertEqual(self.annotation().read_bytes(), before)
        with self.assertRaises(CfError):
            self.export(text=json.dumps(document(), indent=4))
        self.assertEqual(self.annotation().read_bytes(), before)

    def test_a_record_that_cannot_be_converted_writes_nothing(self):
        self.ready()
        before = self.annotation().read_bytes()
        doc = document()
        doc["locations"][0]["target"]["selector"][0]["coordinate_space"]["page_box"] = "TrimBox"
        with self.assertRaises(CfError) as caught:
            self.export(doc)
        self.assertIn("TrimBox", caught.exception.hint)
        self.assertEqual(self.annotation().read_bytes(), before)

    def test_a_folder_the_layout_refuses_stops_the_export_before_it_writes(self):
        self.ready()
        before = self.annotation().read_bytes(), self.record()
        with mock.patch.object(overlay_shards, "section_of", lambda item, clusters: "٣.٤"):
            with self.assertRaises(CfError) as caught:
                self.export(document(region_page=8))
        self.assertIn("٣.٤: ", caught.exception.hint)
        self.assertEqual((self.annotation().read_bytes(), self.record()), before)
        self.assertEqual(tree(self.annotations), {FILE.removeprefix("annotations/")})

    def test_it_refuses_a_document_without_the_configured_source(self):
        self.assertEqual(self.init(), 0)
        with self.assertRaises(CfError) as caught:
            self.export(document(source_id="other-pdf"))
        self.assertIn(SOURCE_ID, caught.exception.message)
        self.assertFalse(self.annotations.exists())

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
                         generated_paths() | {"GENERATED.json", "overlay.config.json", BUNDLE})

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
                         ("formal-companion/overlay-generated", 3))
        self.assertEqual(record["generator"]["name"], "formal-companion")
        self.assertEqual(record["instance_files"], ["overlay.config.json"])
        listed = [row["path"] for row in record["files"]]
        self.assertEqual(listed, sorted(listed))
        self.assertEqual(set(listed), generated_paths())
        for row in record["files"]:
            self.assertEqual(row["sha256"],
                             hashlib.sha256((self.overlay / row["path"]).read_bytes()).hexdigest())
        sealed = json.loads(MANIFEST.read_text(encoding="utf-8"))
        expected = {"package": "literature-links", "version": sealed["version"], "commit": sealed["commit"]}
        if "tag" in sealed:                                  # pragma: no cover - a tagged release
            expected["tag"] = sealed["tag"]
        self.assertEqual(record["format"], expected)
        block = record["annotations"]
        self.assertEqual((block["count"], block["manifest_sha256"]), (1, manifest(self.companion, [FILE])))
        self.assertEqual(block["bundle"], {
            "path": BUNDLE, "sha256": hashlib.sha256((self.overlay / BUNDLE).read_bytes()).hexdigest()})

    def test_the_bundle_holds_every_record_as_the_reader_reads_it_in_file_order(self):
        self.ready(self.two_records())
        found = overlay_sync.read_annotations(self.companion)
        self.assertEqual(list(found.files), [
            "annotations/1.1/lemma-1.1.1.json", "annotations/1.2/lemma-1.2.3/_defaults.json",
            f"annotations/1.2/lemma-1.2.3/key__{DECL}.json", f"annotations/1.2/lemma-1.2.3/key__{DECL}_two.json",
            "annotations/_defaults.json"])
        raw = (self.overlay / BUNDLE).read_bytes()
        bundle = json.loads(raw)
        self.assertEqual((bundle["schema"], bundle["directory"], bundle["count"]),
                         ("formal-companion/overlay-annotations", "annotations", 3))
        self.assertEqual([(row["path"], row["annotation"]) for row in bundle["annotations"]],
                         list(found.records.items()))
        self.assertEqual(list(found.records), [p for p in found.files if not p.endswith("_defaults.json")])
        row = bundle["annotations"][1]["annotation"]
        self.assertEqual(row["source"]["extensions"][overlay_shards.NAMESPACE]["source_id"], SOURCE_ID)
        self.assertEqual(row["target"]["uri"], "../../../Notes/Basic/index.html#Notes___Box___volume_nonneg",
                         "relative to its own file, one folder deeper")
        self.assertEqual(raw.count(b"\n"), 2 + 3, "one line per annotation")
        self.assertEqual(self.tool("check_generated").returncode, 0)

    def test_a_second_sync_is_a_no_op(self):
        self.ready()
        before = {p: p.stat().st_mtime_ns for p in self.overlay.rglob("*") if p.is_file()}
        result = overlay_sync.sync(self.companion)
        self.assertEqual((result.written, result.deleted), ([], []))
        after = {p: p.stat().st_mtime_ns for p in self.overlay.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "a no-op sync rewrote a file")

    def test_sync_before_export_records_no_annotation(self):
        self.assertEqual(self.init(), 0)
        self.assertEqual(self.sync(), 0)
        bundle = (self.overlay / BUNDLE).read_bytes()
        self.assertEqual(self.record()["annotations"], {
            "bundle": {"path": BUNDLE, "sha256": hashlib.sha256(bundle).hexdigest()}, "count": 0,
            "directory": "annotations", "extension": overlay_shards.NAMESPACE,
            "manifest_sha256": hashlib.sha256(b"").hexdigest()})
        self.assertEqual(json.loads(bundle)["annotations"], [])

    def test_it_records_hand_written_annotations_when_nothing_exported_them(self):
        self.assertEqual(self.init(), 0)
        record = overlay_shards.convert(document()).records[0]
        self.annotation().parent.mkdir(parents=True)
        self.annotation().write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(self.sync(), 0)
        block = self.record()["annotations"]
        self.assertNotIn("export", block)
        self.assertEqual(block["count"], 1)
        self.assertEqual(self.sync("--check"), 0)
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_it_refuses_an_exported_file_edited_by_hand_and_names_it(self):
        self.ready()
        self.annotation().write_bytes(self.annotation().read_bytes().replace(b"Lemma 1.2.3", b"Lemma 1.2.4"))
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn(FILE, caught.exception.message)
        self.assertIn("cf overlay export", caught.exception.hint)

    def test_it_refuses_a_file_the_scan_refuses(self):
        self.assertEqual(self.init(), 0)
        self.annotation().parent.mkdir(parents=True)
        self.annotation().write_text('{"source": {"type": "pdf"}}\n', encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn(FILE, caught.exception.message)

    def test_it_refuses_a_file_that_holds_an_array(self):
        self.assertEqual(self.init(), 0)
        record = overlay_shards.convert(document()).records[0]
        self.annotation().parent.mkdir(parents=True)
        self.annotation().with_name("one.json").write_text(json.dumps(record), encoding="utf-8")
        for held in ([record, dict(record, id="second")], [record], []):
            with self.subTest(held=len(held)):
                self.annotation().write_text(json.dumps(held), encoding="utf-8")
                with self.assertRaises(CfError) as caught:
                    overlay_sync.sync(self.companion)
                self.assertEqual(f"{FILE}: a JSON file holds one annotation, an object, not an array [SHAPE]",
                                 caught.exception.message)

    def test_it_refuses_a_file_in_the_annotation_directory_that_is_not_an_annotation_file(self):
        self.ready()
        (self.annotations / "notes.txt").write_text("mine\n", encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            overlay_sync.sync(self.companion)
        self.assertIn("annotations/notes.txt", caught.exception.message)

    def test_it_refuses_folder_defaults_above_the_annotation_directory(self):
        self.assertEqual(self.init(), 0)
        for exported in (False, True):
            if exported:
                self.export()
            for stray in (self.companion / "_defaults.json", self.root / "_defaults.json"):
                with self.subTest(exported=exported, above=stray.parent.name):
                    stray.write_text('{"note": "from above"}\n', encoding="utf-8")
                    try:
                        with self.assertRaises(CfError) as caught:
                            overlay_sync.sync(self.companion)
                    finally:
                        stray.unlink()
                    self.assertIn("_defaults.json: folder defaults outside annotations/", caught.exception.message)
        self.assertEqual(self.sync(), 0)

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

    def test_it_names_a_changed_missing_or_extra_file_and_a_changed_bundle(self):
        self.ready()
        self.annotation().write_bytes(self.annotation().read_bytes() + b"\n")
        problems = overlay_sync.sync_problems(self.companion)
        self.assertIn(f"{FILE}: not as `cf overlay export` writes it", problems)
        self.assertTrue(any(line.startswith("changed annotations:") for line in problems), problems)
        self.annotation().write_bytes(self.annotation().read_bytes()[:-1].replace(b"Lemma 1.2.3", b"Lemma 1.2.4"))
        self.assertIn(f"{FILE}: differs from {BUNDLE}", overlay_sync.sync_problems(self.companion))
        (self.annotations / "2.1").mkdir()
        (self.annotations / "2.1" / "x.json").write_bytes(self.annotation().read_bytes())
        self.assertIn(f"annotations/2.1/x.json: not in {BUNDLE}", overlay_sync.sync_problems(self.companion))
        self.annotation().unlink()
        self.assertIn(f"{FILE}: in {BUNDLE} but not on disk", overlay_sync.sync_problems(self.companion))
        (self.overlay / BUNDLE).write_bytes(b"{}\n")
        problems = overlay_sync.sync_problems(self.companion)
        self.assertIn(f"changed: {BUNDLE}", problems)
        self.assertIn(f"{BUNDLE} is not a readable bundle; run `cf overlay sync`", problems)
        (self.overlay / BUNDLE).unlink()
        self.assertIn(f"missing: {BUNDLE}", overlay_sync.sync_problems(self.companion))

    def test_it_names_what_sync_refuses_in_the_annotations_and_check_names_it_once(self):
        self.ready()
        (self.companion / "_defaults.json").write_text("{}\n", encoding="utf-8")
        line = "_defaults.json: folder defaults outside annotations/ apply to its annotations; remove it"
        self.assertIn(line, overlay_sync.sync_problems(self.companion))
        self.assertEqual(self.sync("--check"), 1)
        self.assertEqual(self.check()[1].count(line), 1)

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
    def test_it_accepts_a_generated_overlay_with_current_annotations(self):
        self.ready()
        code, said = self.check()
        self.assertEqual(code, 0, said)

    def test_it_reports_a_curation_input_that_changed_since_the_export(self):
        self.ready()
        (self.companion / CURATION).write_text('{"curated": false}\n', encoding="utf-8")
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn(f"{CURATION}: changed since the annotations were exported", said)
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

    def test_it_reports_no_annotations(self):
        self.assertEqual(self.init(), 0)
        self.assertEqual(self.sync(), 0)
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn("holds no annotation file", said)

    def test_it_reports_annotations_of_a_source_the_config_does_not_name(self):
        self.hand_written(lambda r: r["source"]["extensions"][overlay_shards.NAMESPACE].update(
            source_id="other-pdf"))
        code, said = self.check()
        self.assertEqual(code, 1)
        self.assertIn(f"no annotation is a record of the PDF '{SOURCE_ID}'", said)

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
        """Hand-written annotation files (nothing exported them), synced, one file per mutation."""
        if not self.overlay.exists():
            self.assertEqual(self.init(), 0)
        if self.annotations.exists():
            shutil.rmtree(self.annotations)
        (self.annotations / "1.2").mkdir(parents=True)
        for n, mutate in enumerate(mutations):
            record = json.loads(json.dumps(overlay_shards.convert(document()).records[0]))
            mutate(record)
            (self.annotations / "1.2" / f"r{n}.json").write_bytes(overlay_shards.annotation_bytes(record))
        self.assertEqual(self.sync(), 0)
        return overlay_sync.overlay_problems(self.companion, SOURCE_ID)

    def test_the_overlay_rules_hold_the_page_count(self):
        problems = self.hand_written(lambda r: r["location"].update(page=40))
        self.assertTrue(any("page 40" in line and "12 pages" in line for line in problems), problems)

    def test_the_overlay_rules_hold_the_targets(self):
        for uri, word in (("../../../outside/index.html", "climbs out"), ("javascript:alert(1)", "neither"),
                          ("http://plain.example.invalid/x", "neither")):
            with self.subTest(uri=uri):
                problems = self.hand_written(lambda r, uri=uri: r["target"].update(uri=uri))
                self.assertTrue(any(word in line for line in problems), problems)
        self.assertEqual(self.hand_written(lambda r: r["target"].update(uri="https://docs.example.invalid/x")),
                         [])
        problems = self.hand_written(lambda r: r.update(target="javascript:alert(1)"))
        self.assertTrue(any("neither" in line for line in problems), problems)
        self.assertEqual(self.hand_written(lambda r: r.update(target="https://docs.example.invalid/x")), [])

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

    def test_it_names_a_tampered_annotation_file(self):
        self.ready()
        self.annotation().write_bytes(self.annotation().read_bytes().replace(b"Lemma 1.2.3", b"Lemma 1.2.4"))
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"{FILE}: differs from the bundle", done.stderr)
        self.assertIn("annotations/: 1 annotations in 1 files with manifest sha256", done.stderr)

    def test_it_names_a_tampered_defaults_file_and_the_records_below_it(self):
        self.ready(self.two_records())
        self.assertEqual(self.tool("check_generated").returncode, 0)
        defaults = self.annotations / "1.2" / "lemma-1.2.3" / "_defaults.json"
        defaults.write_bytes(defaults.read_bytes().replace(b"Lemma 1.2.3", b"Lemma 1.2.4"))
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("annotations/: 3 annotations in 5 files with manifest sha256", done.stderr)
        for name in (DECL, DECL + "_two"):
            self.assertIn(f"annotations/1.2/lemma-1.2.3/key__{name}.json: differs from the bundle", done.stderr)
        defaults.write_bytes(defaults.read_bytes().replace(b"Lemma 1.2.4", b"Lemma 1.2.3") + b"\n")
        done = self.tool("check_generated")
        self.assertIn("annotations/1.2/lemma-1.2.3/_defaults.json: not as `cf overlay export` writes it", done.stderr)

    def test_it_names_folder_defaults_above_the_annotation_directory(self):
        self.ready()
        (self.companion / "_defaults.json").write_text('{"note": "from above"}\n', encoding="utf-8")
        self.reseal()
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn("_defaults.json: folder defaults outside annotations/", done.stderr)

    def test_it_names_a_missing_and_a_stale_annotation_file(self):
        self.ready()
        stale = self.annotations / "9.9" / "old.json"
        stale.parent.mkdir()
        stale.write_bytes(self.annotation().read_bytes())
        self.annotation().unlink()
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"{FILE}: missing, but the bundle holds it", done.stderr)
        self.assertIn("annotations/9.9/old.json: not in the bundle", done.stderr)

    def test_it_fails_on_a_bundle_that_is_not_the_files(self):
        self.ready()
        bundle = self.overlay / BUNDLE
        bundle.write_bytes(bundle.read_bytes().replace(b"Lemma 1.2.3", b"Lemma 1.2.4"))
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"{BUNDLE}: sha256", done.stderr)
        record = self.record()
        record["annotations"]["bundle"]["sha256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
        self.write_record(record)
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"{FILE}: differs from the bundle", done.stderr)
        self.assertNotIn("manifest sha256", done.stderr, "the files themselves are what GENERATED.json records")

    def test_it_fails_on_a_file_the_scan_refuses(self):
        self.ready()
        self.annotation().write_text('{"source": {"type": "pdf"}}\n', encoding="utf-8")
        self.reseal()
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"{FILE}: ", done.stderr)
        self.assertIn("[SCHEMA]", done.stderr)
        self.assertNotIn("read from it", done.stderr)

    def test_it_fails_on_an_extra_file_that_is_not_an_annotation_file(self):
        for extra in ("notes.txt", "1.2/notes.txt", "1.2/sub/deeper/x.json", "1.2.jsonl", "1.2/_defaults.json"):
            with self.subTest(extra=extra):
                if not self.overlay.exists():
                    self.ready()
                path = self.annotations / extra
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("mine\n", encoding="utf-8")
                done = self.tool("check_generated")
                self.assertEqual(done.returncode, 1)
                self.assertIn(f"annotations/{extra.removesuffix('/x.json')}: not a", done.stderr)
                path.unlink()
                if path.parent.name == "deeper":
                    shutil.rmtree(path.parent.parent)
        self.assertEqual(self.tool("check_generated").returncode, 0)

    def test_it_fails_on_a_stale_export(self):
        self.ready()
        (self.companion / CURATION).write_text('{"curated": "again"}\n', encoding="utf-8")
        done = self.tool("check_generated")
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"{CURATION}: changed since the annotations were exported", done.stderr)

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

    def test_it_fails_on_annotations_that_name_another_source(self):
        self.assertEqual(self.init(), 0)
        self.annotation().parent.mkdir(parents=True)
        self.annotation().write_bytes(overlay_shards.file_bytes(
            overlay_shards.convert(document(source_id="other-pdf")))[FILE.removeprefix("annotations/")])
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

    def test_build_site_publishes_the_annotations_at_the_site_root(self):
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
        self.assertEqual((site / FILE).read_bytes(), self.annotation().read_bytes())
        for name in ("GENERATED.json", BUNDLE):
            self.assertEqual((site / config.mount / name).read_bytes(), (self.overlay / name).read_bytes())
        receipt = json.loads((site / config.mount / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual([row["path"] for row in receipt["annotations"]["files"]],
                         [FILE.removeprefix("annotations/")])
        self.assertEqual(list(self.overlay.rglob("__pycache__")), [])

    def test_build_site_publishes_the_folder_defaults_with_the_annotations(self):
        self.ready(self.two_records())
        config = overlay_config.load(self.overlay / "overlay.config.json")
        site = self.companion.joinpath(*config.output_root.split("/"))
        site.mkdir(parents=True)
        (site / config.index).write_text("<!doctype html><html><body><p>site</p></body></html>\n", encoding="utf-8")
        done = self.tool("build_site")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual({p: (site / "annotations" / p).read_bytes() for p in tree(site / "annotations")},
                         {p: (self.annotations / p).read_bytes() for p in tree(self.annotations)})

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

    def test_the_subcommands_are_init_sync_check_and_export(self):
        import argparse

        parser = argparse.ArgumentParser(prog="cf")
        subparsers = parser.add_subparsers(dest="command")
        cf_main.discover_commands(subparsers)
        overlay = subparsers.choices["overlay"]
        children = {}
        for action in overlay._actions:                      # noqa: SLF001 - no public API
            if isinstance(action, argparse._SubParsersAction):   # noqa: SLF001
                children = dict(action.choices)
        self.assertEqual(set(children), {"init", "sync", "check", "export"})

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
