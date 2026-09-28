"""The vendored annotation format is exactly the release its manifest names.

New code, for the switchover to formal-companion-annotations (CLAUDE.md
sections 2 and 4).  ``vendor/formal_companion_annotations/`` is one tagged
release of the format's repository, and
``vendor/formal_companion_annotations.manifest.json`` seals it: the upstream,
the tag, the commit, the version and the sha256 of every file.  Any vendored
byte that differs from the manifest, a file added or removed, fails here; so
does a module that imports the format without the ``cflib.vendored``
bootstrap, and a shared set whose copy in ``cflib.vocab`` is not the format's.
A change to the format starts upstream (a release), then
``scripts/annotations/sync_format.py``, then our copy.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vendored, vocab                                  # noqa: E402
import formal_companion_annotations as fmt_package                  # noqa: E402
from formal_companion_annotations import vocab as fmt               # noqa: E402

VENDOR = SKILL / "vendor"
PACKAGE = VENDOR / "formal_companion_annotations"
MANIFEST = VENDOR / "formal_companion_annotations.manifest.json"
UPSTREAM = "https://github.com/mathlib-initiative/formal-companion-annotations"

SHARED = ("VIEWER_VERDICTS", "VIEWER_SEVERITIES", "VIEWER_STATUS", "LEDGER_VERDICTS",
          "FINDING_KINDS", "SOURCE_ROLES", "SORRY_CLASSES", "STATIC_RESOLUTIONS",
          "RESOLUTION_PREFIXES")


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def on_disk() -> dict[str, str]:
    out = {}
    for path in sorted(PACKAGE.rglob("*")):
        rel = path.relative_to(PACKAGE)
        if path.is_file() and "__pycache__" not in rel.parts and path.suffix != ".pyc":
            out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class TestTheVendoredBytesAreTheRelease(unittest.TestCase):

    def test_the_manifest_names_a_release(self):
        m = manifest()
        self.assertEqual(set(m), {"upstream", "tag", "commit", "version", "source", "license", "files"})
        self.assertEqual(m["upstream"], UPSTREAM)
        self.assertRegex(m["tag"], r"^v[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertEqual(m["tag"], f"v{m['version']}")
        self.assertRegex(m["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(m["source"], "src/formal_companion_annotations")

    def test_no_vendored_byte_drifts_from_the_manifest(self):
        self.assertEqual(on_disk(), manifest()["files"],
                         "vendor/ is not the release its manifest names; rerun "
                         "scripts/annotations/sync_format.py instead of editing it")

    def test_the_vendored_version_is_the_manifests(self):
        self.assertEqual(fmt_package.__version__, manifest()["version"])

    def test_the_bootstrap_imports_the_vendored_copy(self):
        self.assertEqual(vendored.VENDOR, VENDOR)
        self.assertIn(str(VENDOR), sys.path)
        self.assertEqual(Path(fmt_package.__file__).resolve().parent, PACKAGE.resolve())


class TestEveryImportGoesThroughTheBootstrap(unittest.TestCase):

    def test_the_bootstrap_comes_before_the_format(self):
        """A module that imports the format first would find it only by luck of import order."""
        offenders = []
        for path in sorted([*SCRIPTS.rglob("*.py"), *(SKILL / "tests").glob("*.py")]):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = sorted((n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))),
                             key=lambda n: (n.lineno, n.col_offset))
            booted = False
            for node in imports:
                if isinstance(node, ast.ImportFrom) and node.module == "cflib" \
                        and any(a.name == "vendored" for a in node.names):
                    booted = True
                names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                if any(n.split(".")[0] == "formal_companion_annotations" for n in names) and not booted:
                    offenders.append(f"{path.relative_to(SKILL)}:{node.lineno}")
        self.assertEqual(offenders, [], "import `from cflib import vendored` first")


class TestTheFormatAgrees(unittest.TestCase):
    """The format owns nine sets that cflib.vocab also holds; our copies must equal its."""

    def test_our_copy_of_every_shared_set_is_the_formats(self):
        for name in SHARED:
            with self.subTest(set=name):
                self.assertEqual(getattr(vocab, name), getattr(fmt, name))

    def test_the_formats_resolution_states_are_ours_plus_open(self):
        self.assertEqual({"open"} | vocab.STATIC_RESOLUTIONS
                         | {p.rstrip(":") for p in vocab.RESOLUTION_PREFIXES},
                         fmt.ANNOTATION_RESOLUTION_STATES)

    def test_the_exporters_write_the_major_version_the_package_reads(self):
        self.assertEqual(fmt.ANNOTATION_SCHEMA, "lean-companion/annotations")
        self.assertEqual(fmt.ANNOTATION_SCHEMA_MAJOR, 1)
        self.assertEqual(manifest()["version"].split(".")[0], str(fmt.ANNOTATION_SCHEMA_MAJOR))


if __name__ == "__main__":
    unittest.main()
