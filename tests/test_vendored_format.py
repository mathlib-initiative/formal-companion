"""The vendored annotation format is exactly the release its manifest names.

New code, for the switchover to formal-companion-annotations (CLAUDE.md
sections 2 and 4).  ``vendor/formal_companion_annotations/`` is one tagged
release of the format's repository, and
``vendor/formal_companion_annotations.manifest.json`` seals it: the upstream,
the tag, the commit, the version and the sha256 of every file.  Any vendored
byte that differs from the manifest, a file added or removed, fails here; so
does a module that imports the format without the ``cflib.vendored``
bootstrap, and a value the skill writes into a document that the format's own
vocabulary does not admit.  The format owns its vocabulary
(``formal_companion_annotations.vocab``); ``cflib.vocab`` keeps the skill's own
sets for its stages and maps onto the format's values where a document is
written, so the test here is containment, not equality.  A change to the
format starts upstream (a release, or a release candidate tagged ``vX.Y.ZrcN``),
then ``scripts/annotations/sync_format.py``, then our copy.
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

#: The skill's sets whose values reach a document, and the format's set each must sit inside:
#: a verdict's ``verdict``, ``severity`` and ``status`` (the viewer layer the exporters copy),
#: a source's ``role`` and a declaration's ``sorry`` class.
WRITTEN_INTO_DOCUMENTS = (
    ("VIEWER_VERDICTS", "ANNOTATION_VERDICTS"),
    ("VIEWER_SEVERITIES", "ANNOTATION_SEVERITIES"),
    ("VIEWER_STATUS", "ANNOTATION_VERDICT_STATUSES"),
    ("SOURCE_ROLES", "ANNOTATION_SOURCE_ROLES"),
    ("SORRY_CLASSES", "ANNOTATION_SORRY_CLASSES"),
)


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
        self.assertRegex(m["tag"], r"^v[0-9]+\.[0-9]+\.[0-9]+(?:rc[1-9][0-9]*)?$")
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


#: Modules copied to a run host, where ``cflib`` is not importable: each puts
#: ``vendor/`` on ``sys.path`` itself (the copy beside it in a gate directory, else
#: the checkout's) before it imports the format, which is the bootstrap's job done
#: without the bootstrap.
HOST_BOOTSTRAPPED = {
    "scripts/gate/locate_text.py":
        "the gate's head-text helper; references/locate-documents.md section 9",
}


class TestEveryImportGoesThroughTheBootstrap(unittest.TestCase):

    def test_the_bootstrap_comes_before_the_format(self):
        """A module that imports the format first would find it only by luck of import order."""
        offenders = []
        for path in sorted([*SCRIPTS.rglob("*.py"), *(SKILL / "tests").glob("*.py")]):
            if path.relative_to(SKILL).as_posix() in HOST_BOOTSTRAPPED:
                continue
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


    def test_a_host_module_puts_vendor_on_the_path_before_the_format(self):
        for rel in sorted(HOST_BOOTSTRAPPED):
            with self.subTest(module=rel):
                text = (SKILL / rel).read_text(encoding="utf-8")
                self.assertNotIn("from cflib", text, "a host module cannot import cflib")
                self.assertLess(text.index("sys.path.insert"),
                                text.index("from formal_companion_annotations"))


class TestEveryValueTheSkillWritesIsTheFormats(unittest.TestCase):
    """The format owns its vocabulary; whatever the skill copies into a document must be in it."""

    def test_every_written_set_is_inside_the_formats(self):
        for ours, theirs in WRITTEN_INTO_DOCUMENTS:
            with self.subTest(set=ours):
                self.assertLessEqual(set(getattr(vocab, ours)), set(getattr(fmt, theirs)),
                                     f"cflib.vocab.{ours} writes a value {theirs} does not admit")

    def test_every_resolution_state_the_skill_writes_is_the_formats(self):
        # a verdict's resolution.state: the static resolutions, the prefixed ones and ``open``
        written = {"open"} | vocab.STATIC_RESOLUTIONS | {p.rstrip(":") for p in vocab.RESOLUTION_PREFIXES}
        self.assertLessEqual(written, fmt.ANNOTATION_RESOLUTION_STATES)

    def test_the_ledger_vocabulary_is_not_the_formats(self):
        """The ledger verdicts and finding kinds left the format: they go under ``x_`` keys."""
        for name in ("LEDGER_VERDICTS", "FINDING_KINDS", "VIEWER_VERDICTS", "VIEWER_STATUS",
                     "VIEWER_SEVERITIES", "SOURCE_ROLES", "SORRY_CLASSES"):
            self.assertFalse(hasattr(fmt, name), f"the format grew a {name} again; map to it instead")

    def test_the_exporters_write_the_major_version_the_package_reads(self):
        self.assertEqual(fmt.ANNOTATION_SCHEMA, "formal-companion/annotations")
        self.assertEqual(fmt.ANNOTATION_SCHEMA_MAJOR, 1)
        self.assertEqual(manifest()["version"].split(".")[0], str(fmt.ANNOTATION_SCHEMA_MAJOR))
        self.assertEqual(set(fmt.ANNOTATION_PROFILE_VERSIONS), {"audit", "graph", "history", "site"})


if __name__ == "__main__":
    unittest.main()
