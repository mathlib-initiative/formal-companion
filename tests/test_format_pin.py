"""The annotation format is pinned in one place: the vendored release.

New code, implementing the format-pin rule of `docs/overlay.md` ("Upgrading the
format") and CLAUDE.md section 2.  formal-companion consumes the annotation
format as one vendored release sealed by
`vendor/formal_companion_annotations.manifest.json`; the format's own
repository moves independently, and its next major renames the schema
identity.  So nothing outside `vendor/` may carry a second copy of that
identity, and the generated overlay must read it from the schema it ships.

Three things are held here:

* no file under `templates/overlay/runtime/` or `scripts/` spells the
  identity, or any `<word>/annotations` schema id, as a literal;
* `cf overlay sync` copies the vendored package's own schema file to
  `pdf-overlay/schema/annotations.schema.json` and stamps the release in
  `GENERATED.json`'s `format` block;
* the three runtime readers take the accepted identity and version from that
  schema file -- replace it with a synthetic format and they accept a
  synthetic document and refuse the vendored one.

Everything here is synthetic: a source id, a page title and a document about a
book nobody published.
"""
from __future__ import annotations

# Pin HOME before anything builds a workspace: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from formal_companion_annotations import validate as V                # noqa: E402
from formal_companion_annotations import vocab as fmt                 # noqa: E402
from overlay import sync as overlay_sync                              # noqa: E402

RUNTIME = SKILL / "templates" / "overlay" / "runtime"
MANIFEST = SKILL / "vendor" / "formal_companion_annotations.manifest.json"
SCHEMA_RELATIVE = overlay_sync.FORMAT_SCHEMA_RELATIVE

#: A schema identity written as a literal: `"<word>/annotations"` in quotes.
#: The real one is the vendored `ANNOTATION_SCHEMA`; this catches a stale copy
#: of any spelling, including one the format's next major introduces.
IDENTITY_LITERAL = re.compile(r"""['"][A-Za-z0-9_.-]+/annotations['"]""")

SOURCE_ID = "notes-pdf"
SOURCE_TITLE = "Elementary Sets: Lecture Notes (synthetic)"
SOURCE_SHA = "a1b2c3d4" * 8

#: The synthetic format the runtime readers are pointed at: an identity and a
#: major that no release of the real format has ever carried.
OTHER_SCHEMA = "example/annotations"
OTHER_VERSION_PATTERN = r"^7\.(0|[1-9][0-9]*)$"
OTHER_VERSION = "7.2"


def synthetic_schema() -> dict:
    """A JSON Schema that states a format identity and nothing else."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "schema": {"const": OTHER_SCHEMA},
            "schema_version": {"pattern": OTHER_VERSION_PATTERN, "type": "string"},
        },
        "type": "object",
    }


def document(schema: str, schema_version: str) -> dict:
    """The smallest document the three runtime readers all look at."""
    return {
        "schema": schema,
        "schema_version": schema_version,
        "companion": {"name": "notes-companion"},
        "sources": [{
            "id": SOURCE_ID,
            "kind": "pdf",
            "title": SOURCE_TITLE,
            "sha256": SOURCE_SHA,
            "physical_page_count": 12,
            "url": "https://notes.example.invalid/notes.pdf",
        }],
        "items": [],
        "declarations": [],
        "links": [],
        "provenance": [],
    }


def load_tool(name: str):
    """Import a runtime tool by path, the way a companion runs it."""
    path = RUNTIME / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"format_pin_{name}", path)
    if spec is None or spec.loader is None:                  # pragma: no cover
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK_GENERATED = load_tool("check_generated")
SERVE = load_tool("serve")
BUILD = load_tool("build_site")


def text_files(root: Path):
    """Every readable text file under *root*, skipping the vendored trees."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        parts = path.relative_to(root).parts
        if "vendor" in parts or "__pycache__" in parts:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):                # pragma: no cover
            continue


# ---------------------------------------------------------------------------
# (a) no second copy of the identity
# ---------------------------------------------------------------------------

class TestTheIdentityIsNowhereButVendor(unittest.TestCase):
    """A literal here is a place a format bump would silently miss."""

    def offenders(self, root: Path) -> list[str]:
        found = []
        for path, body in text_files(root):
            for number, line in enumerate(body.splitlines(), start=1):
                if fmt.ANNOTATION_SCHEMA in line or IDENTITY_LITERAL.search(line):
                    found.append(f"{path.relative_to(SKILL)}:{number}: {line.strip()}")
        return found

    def test_the_generated_runtime_names_no_schema_identity(self):
        self.assertEqual(
            self.offenders(RUNTIME), [],
            "the generated runtime reads its format from "
            f"{SCHEMA_RELATIVE}; it may not carry the identity as a literal")

    def test_the_scripts_name_no_schema_identity(self):
        self.assertEqual(
            self.offenders(SCRIPTS), [],
            "use formal_companion_annotations.vocab.ANNOTATION_SCHEMA")

    def test_the_pattern_would_catch_a_literal(self):
        """A test that matches nothing would pass against any regression."""
        self.assertTrue(IDENTITY_LITERAL.search(f'"{fmt.ANNOTATION_SCHEMA}"'))
        self.assertTrue(IDENTITY_LITERAL.search(f"'{OTHER_SCHEMA}'"))
        self.assertIsNone(IDENTITY_LITERAL.search('"../schema/annotations.schema.json"'))

    def test_the_templates_ship_no_copy_of_the_format_schema(self):
        """The copy was deleted: `sync` takes it from the vendored release."""
        self.assertEqual(sorted(p.name for p in (RUNTIME / "schema").iterdir()),
                         ["overlay-config-v1.schema.json"])
        self.assertNotIn(SCHEMA_RELATIVE, overlay_sync.template_files())


# ---------------------------------------------------------------------------
# (b) what a sync writes
# ---------------------------------------------------------------------------

class SyncedCompanion(unittest.TestCase):
    """A throwaway companion with a freshly generated `pdf-overlay/`."""

    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="cf-format-pin-",
                                     dir=os.environ.get("CF_TEST_TMP") or None))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.root = root.resolve()
        self.companion = self.root / "companion"
        self.companion.mkdir()
        self.overlay = self.companion / "pdf-overlay"
        self.overlay.mkdir()
        (self.overlay / "overlay.config.json").write_text(json.dumps({
            "schema": "formal-companion/overlay-config",
            "schema_version": 1,
            "source_id": SOURCE_ID,
            "title": "Synthetic notes",
            "pdf": {"file_name": "notes.pdf"},
            "site": {},
        }, indent=2) + "\n", encoding="utf-8")
        self.write_document(fmt.ANNOTATION_SCHEMA, fmt.ANNOTATION_SCHEMA_VERSION)
        overlay_sync.sync(self.companion)

    def write_document(self, schema: str, schema_version: str) -> None:
        (self.overlay / "annotations.json").write_text(
            json.dumps(document(schema, schema_version), indent=2) + "\n",
            encoding="utf-8")

    def write_schema(self, value: dict) -> None:
        (self.overlay / Path(SCHEMA_RELATIVE)).write_text(
            json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def record(self) -> dict:
        return json.loads((self.overlay / "GENERATED.json").read_text(encoding="utf-8"))


class TestSyncShipsTheVendoredSchema(SyncedCompanion):

    def test_the_shipped_schema_is_the_vendored_file_byte_for_byte(self):
        self.assertEqual((self.overlay / Path(SCHEMA_RELATIVE)).read_bytes(),
                         Path(V.SCHEMA_PATH).read_bytes())

    def test_generated_json_lists_it_with_its_sha256(self):
        listed = {row["path"]: row["sha256"] for row in self.record()["files"]}
        self.assertIn(SCHEMA_RELATIVE, listed)
        self.assertEqual(listed[SCHEMA_RELATIVE],
                         overlay_sync.digest(Path(V.SCHEMA_PATH).read_bytes()))

    def test_the_format_block_is_the_manifest_plus_vocab(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(self.record()["format"], {
            "package": "formal-companion-annotations",
            "version": manifest["version"],
            "tag": manifest["tag"],
            "commit": manifest["commit"],
            "schema": fmt.ANNOTATION_SCHEMA,
            "schema_version": fmt.ANNOTATION_SCHEMA_VERSION,
        })

    def test_sync_check_and_check_generated_pass_on_it(self):
        self.assertEqual(overlay_sync.sync_problems(self.companion), [])
        self.assertEqual(CHECK_GENERATED.problems(self.overlay), [])

    def test_sync_check_reports_a_format_block_that_is_not_the_pin(self):
        record = self.record()
        record["format"] = {**record["format"], "tag": "v0.0.1"}
        (self.overlay / "GENERATED.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8")
        found = overlay_sync.sync_problems(self.companion)
        self.assertTrue(any("GENERATED.json.format" in line for line in found), found)

    def test_check_generated_reports_a_missing_format_block(self):
        record = self.record()
        record.pop("format")
        (self.overlay / "GENERATED.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8")
        found = CHECK_GENERATED.problems(self.overlay)
        self.assertTrue(any("GENERATED.json.format" in line for line in found), found)

    def test_check_generated_reports_a_format_block_the_schema_contradicts(self):
        """The one cross-check a companion can make with no package at all."""
        record = self.record()
        record["format"] = {**record["format"], "schema": OTHER_SCHEMA}
        found = CHECK_GENERATED.format_problems(
            record, CHECK_GENERATED.document_format(self.overlay))
        self.assertTrue(any("format.schema" in line for line in found), found)

    def test_check_generated_reports_a_schema_file_with_no_const(self):
        self.write_schema({"properties": {"schema_version": {"pattern": "^7\\.0$"}}})
        found = CHECK_GENERATED.problems(self.overlay)
        self.assertTrue(any("properties.schema has no const" in line for line in found),
                        found)


# ---------------------------------------------------------------------------
# (c) the readers follow the schema file
# ---------------------------------------------------------------------------

class TestTheRuntimeReadersFollowTheShippedSchema(SyncedCompanion):
    """Point the three Python readers at a format nobody has published.

    They are called directly rather than through `main`: the sha256 half of
    `check_generated.py` would stop first on a schema file that no longer
    matches `GENERATED.json`, and this is a test about the document check.
    """

    def setUp(self) -> None:
        super().setUp()
        self.write_schema(synthetic_schema())

    # -- check_generated.py ------------------------------------------------
    def test_check_generated_accepts_the_synthetic_format(self):
        self.write_document(OTHER_SCHEMA, OTHER_VERSION)
        fmt_read = CHECK_GENERATED.document_format(self.overlay)
        self.assertEqual(fmt_read.schema, OTHER_SCHEMA)
        self.assertEqual(fmt_read.version.pattern, OTHER_VERSION_PATTERN)
        self.assertEqual(
            CHECK_GENERATED.document_problems(self.overlay, SOURCE_ID, fmt_read), [])

    def test_check_generated_refuses_the_vendored_identity(self):
        self.write_document(fmt.ANNOTATION_SCHEMA, fmt.ANNOTATION_SCHEMA_VERSION)
        found = CHECK_GENERATED.document_problems(
            self.overlay, SOURCE_ID, CHECK_GENERATED.document_format(self.overlay))
        self.assertTrue(any("annotations.json.schema:" in line for line in found), found)
        self.assertTrue(any("schema_version" in line for line in found), found)

    def test_check_generated_refuses_a_version_outside_the_pattern(self):
        self.write_document(OTHER_SCHEMA, "8.0")
        found = CHECK_GENERATED.document_problems(
            self.overlay, SOURCE_ID, CHECK_GENERATED.document_format(self.overlay))
        self.assertTrue(any("schema_version" in line for line in found), found)

    # -- serve.py ----------------------------------------------------------
    def test_serve_accepts_the_synthetic_format(self):
        self.write_document(OTHER_SCHEMA, OTHER_VERSION)
        record = SERVE.load_source_record(self.overlay, SERVE.load_config(self.overlay))
        self.assertEqual(record.id, SOURCE_ID)
        self.assertEqual(record.sha256, SOURCE_SHA)

    def test_serve_refuses_the_vendored_identity(self):
        self.write_document(fmt.ANNOTATION_SCHEMA, fmt.ANNOTATION_SCHEMA_VERSION)
        with self.assertRaisesRegex(SERVE.OverlayError, "annotations.json.schema"):
            SERVE.load_source_record(self.overlay, SERVE.load_config(self.overlay))

    def test_serve_refuses_a_schema_file_that_is_not_there(self):
        (self.overlay / Path(SCHEMA_RELATIVE)).unlink()
        with self.assertRaisesRegex(SERVE.OverlayError, "annotations.schema.json"):
            SERVE.load_source_record(self.overlay, SERVE.load_config(self.overlay))

    # -- build_site.py -----------------------------------------------------
    def test_build_site_accepts_the_synthetic_format(self):
        self.write_document(OTHER_SCHEMA, OTHER_VERSION)
        raw = BUILD.check_document(self.overlay, BUILD.load_config(self.overlay))
        self.assertEqual(json.loads(raw)["schema"], OTHER_SCHEMA)

    def test_build_site_refuses_the_vendored_identity(self):
        self.write_document(fmt.ANNOTATION_SCHEMA, fmt.ANNOTATION_SCHEMA_VERSION)
        with self.assertRaisesRegex(BUILD.BuildError, "annotations.json.schema"):
            BUILD.check_document(self.overlay, BUILD.load_config(self.overlay))

    def test_build_site_publishes_the_schema_into_the_site_mount(self):
        """The viewer fetches it from the mount, so it has to be copied there."""
        self.write_document(OTHER_SCHEMA, OTHER_VERSION)
        site = self.companion / ".lake" / "build" / "literate-html"
        site.mkdir(parents=True)
        (site / "index.html").write_text(
            "<!doctype html><html><body><p>site</p></body></html>\n", encoding="utf-8")
        contents = BUILD.collect_runtime(self.overlay)
        self.assertIn(SCHEMA_RELATIVE, contents)
        self.assertEqual(json.loads(contents[SCHEMA_RELATIVE])["properties"]["schema"],
                         {"const": OTHER_SCHEMA})


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
