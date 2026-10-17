"""The vendored Literature Links package is exactly the revision its manifest names.

New code (docs/overlay.md): ``vendor/literature_links/`` is the format the
generated overlay reads, sealed by ``vendor/literature_links.manifest.json``
and written by ``scripts/annotations/sync_format.py --package
literature_links``, which also takes a full commit id for an untagged release.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:                                                # pragma: no cover
    from tests import _hermetic  # noqa: F401

import ast
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

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vendored                                          # noqa: E402
import literature_links                                             # noqa: E402
from literature_links import companion, discovery                   # noqa: E402
from annotations import sync_format                                 # noqa: E402
from overlay import defaults as overlay_defaults                    # noqa: E402

VENDOR = SKILL / "vendor"
PACKAGE = VENDOR / "literature_links"
MANIFEST = VENDOR / "literature_links.manifest.json"


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class TestTheVendoredBytesAreTheRevision(unittest.TestCase):

    def test_the_manifest_names_a_revision(self):
        m = manifest()
        self.assertLessEqual({"upstream", "commit", "version", "source", "license", "files", "extra_files"}, set(m))
        self.assertLessEqual(set(m), {"upstream", "commit", "version", "source", "license", "files",
                                      "extra_files", "tag", "branch", "released"})
        self.assertEqual(m["upstream"], sync_format.PACKAGES["literature_links"].upstream)
        self.assertRegex(m["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(m["source"], "src/literature_links")
        self.assertEqual(m["extra_files"], ["schema/companion.schema.json"])
        if "tag" in m:
            self.assertEqual(m["tag"], f"v{m['version']}")
        self.assertEqual("branch" in m, m.get("released") is False, "an unreleased revision names its branch")
        self.assertFalse("tag" in m and "branch" in m)

    def test_no_vendored_byte_drifts_from_the_manifest(self):
        self.assertEqual(sync_format.vendored_files(PACKAGE), manifest()["files"],
                         "vendor/literature_links is not the revision its manifest names; rerun "
                         "scripts/annotations/sync_format.py --package literature_links")

    def test_the_vendored_version_is_the_manifests(self):
        self.assertEqual(literature_links.__version__, manifest()["version"])

    def test_the_contract_and_the_shipped_notice_name_the_revision(self):
        m = manifest()
        for path in ("docs/overlay.md", "templates/overlay/runtime/THIRD_PARTY.md"):
            text = (SKILL / path).read_text(encoding="utf-8")
            for value in (m["commit"], m["version"], m.get("tag", ""), m.get("branch", "")):
                self.assertIn(value, text, path)

    def test_the_written_order_names_every_field_of_the_schema(self):
        schema = companion.load_schema()
        self.assertEqual(set(overlay_defaults.TOP), set(schema["properties"]))
        self.assertEqual(set(overlay_defaults.END), set(schema["$defs"]["end"]["properties"]))

    def test_the_bootstrap_imports_the_vendored_copy_and_its_schema(self):
        self.assertEqual(vendored.VENDOR, VENDOR)
        self.assertEqual(Path(literature_links.__file__).resolve().parent, PACKAGE.resolve())
        self.assertEqual(Path(companion.SCHEMA_PATH).resolve(),
                         (PACKAGE / "schema" / "companion.schema.json").resolve())

    def test_it_validates_and_scans(self):
        good = {"subject": [{"type": "pdf", "uri": "book.pdf", "location": {"page": 2, "rect": [0.1, 0.2, 0.3, 0.4]}}],
                "object": []}
        self.assertEqual(companion.validate(good), [])
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "1.1.jsonl").write_text(json.dumps(good) + "\n", encoding="utf-8")
            found = discovery.collect([tmp])
        self.assertEqual((len(found.records), found.problems), (1, []))

    def test_a_json_file_holds_one_annotation(self):
        """The overlay counts one annotation per file because the reader refuses an array."""
        good = {"subject": [{"type": "pdf", "uri": "book.pdf"}], "object": [], "status": "formalized"}
        with tempfile.TemporaryDirectory() as tmp:
            for held in ([good], []):
                (Path(tmp) / "a.json").write_text(json.dumps(held), encoding="utf-8")
                found = discovery.collect([tmp])
                self.assertEqual(([r.annotation for r in found.records], [p.code for p in found.problems]),
                                 ([], ["SHAPE"]))

    def test_an_end_template_fills_the_ends_that_name_no_document(self):
        """The overlay's folder defaults rely on 2.0 end templates."""
        template = {"subject": {"type": "pdf", "uri": "https://example.invalid/b.pdf", "sha256": "0" * 64}}
        record = {"subject": [{"location": {"page": 1}}, {"type": "pdf", "uri": "c.pdf"}], "object": []}
        self.assertEqual(companion.merge(template, record)["subject"],
                         [dict(template["subject"], location={"page": 1}), {"type": "pdf", "uri": "c.pdf"}])
        self.assertEqual(companion.merge(template, {"object": []})["subject"], [template["subject"]])
        self.assertFalse(hasattr(companion, "normalize"))

    #: copied to a run host, where cflib is not importable: it puts vendor/ (beside
    #: it in a gate directory, else the checkout's) on sys.path itself first
    HOST_BOOTSTRAPPED = {"scripts/locate/records.py"}

    def test_every_import_goes_through_the_bootstrap(self):
        offenders = []
        for path in sorted([*SCRIPTS.rglob("*.py"), *(SKILL / "tests").glob("*.py")]):
            if path.relative_to(SKILL).as_posix() in self.HOST_BOOTSTRAPPED:
                text = path.read_text(encoding="utf-8")
                self.assertLess(text.index("sys.path.insert"), text.index("from literature_links"))
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
                if any(n.split(".")[0] == "literature_links" for n in names) and not booted:
                    offenders.append(f"{path.relative_to(SKILL)}:{node.lineno}")
        self.assertEqual(offenders, [], "import `from cflib import vendored` first")


GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.invalid",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e.invalid", GIT_CONFIG_NOSYSTEM="1")


class TestTheSyncScript(unittest.TestCase):
    """``sync_format.py`` against a synthetic upstream: a tag, a commit id, and refusals."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-sync-format-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "upstream"
        (self.repo / "src" / "literature_links").mkdir(parents=True)
        (self.repo / "schema").mkdir()
        (self.repo / "src" / "literature_links" / "__init__.py").write_text('__version__ = "2.1.0"\n')
        (self.repo / "src" / "literature_links" / "companion.py").write_text("X = 1\n")
        (self.repo / "schema" / "companion.schema.json").write_text('{"type": "object"}\n')
        (self.repo / "README.md").write_text("not vendored\n")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-qm", "one")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.vendor = self.tmp / "vendor"

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, env=GIT_ENV,
                              capture_output=True, text=True).stdout

    def sync(self, *argv: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return sync_format.main([str(self.repo), *argv, "--package", "literature_links"], vendor=self.vendor)

    def test_a_commit_id_vendors_the_package_and_its_schema_with_no_tag(self):
        self.assertEqual(self.sync(self.commit), 0)
        m = json.loads((self.vendor / "literature_links.manifest.json").read_text())
        self.assertNotIn("tag", m)
        self.assertEqual((m["commit"], m["version"]), (self.commit, "2.1.0"))
        self.assertEqual(sorted(m["files"]), ["__init__.py", "companion.py", "schema/companion.schema.json"])
        self.assertEqual(m["files"]["companion.py"], hashlib.sha256(b"X = 1\n").hexdigest())
        self.assertEqual(self.sync(self.commit, "--check"), 0)

    def test_a_commit_on_a_branch_is_an_unreleased_version(self):
        self.git("branch", "-M", "next")
        self.assertEqual(self.sync(self.commit, "--branch", "next"), 0)
        m = json.loads((self.vendor / "literature_links.manifest.json").read_text())
        self.assertEqual((m["commit"], m["version"], m["branch"], m["released"]), (self.commit, "2.1.0", "next", False))
        self.assertNotIn("tag", m)
        self.assertEqual(self.sync(self.commit, "--branch", "next", "--check"), 0)
        self.assertEqual(self.sync(self.commit, "--check"), 1, "the manifest names a branch the check does not")
        for branch in ("main", "no-such-branch"):
            with self.subTest(branch=branch), self.assertRaises(SystemExit):
                self.sync(self.commit, "--branch", branch)

    def test_a_commit_that_is_not_on_the_branch_is_refused(self):
        self.git("branch", "-M", "next")
        self.git("checkout", "-q", "-b", "other")
        (self.repo / "README.md").write_text("later\n")
        self.git("commit", "-qam", "two")
        later = self.git("rev-parse", "HEAD").strip()
        with self.assertRaises(SystemExit) as caught:
            self.sync(later, "--branch", "next")
        self.assertIn("not on next", str(caught.exception))
        self.git("tag", "-a", "v2.1.0", "-m", "release", self.commit)
        with self.assertRaises(SystemExit):
            self.sync("v2.1.0", "--branch", "next")

    def test_a_tag_vendors_the_same_bytes_and_names_the_tag(self):
        self.git("tag", "-a", "v2.1.0", "-m", "release")
        self.assertEqual(self.sync("v2.1.0"), 0)
        m = json.loads((self.vendor / "literature_links.manifest.json").read_text())
        self.assertEqual((m["tag"], m["commit"]), ("v2.1.0", self.commit))
        self.assertEqual(self.sync(self.commit, "--check"), 1)      # the manifest names a tag the check does not

    def test_check_fails_on_a_vendored_byte_that_drifts(self):
        self.sync(self.commit)
        (self.vendor / "literature_links" / "companion.py").write_text("X = 2\n")
        self.assertEqual(self.sync(self.commit, "--check"), 1)

    def test_refusals(self):
        self.git("tag", "v2.1.0")                                     # lightweight, not a release tag
        for revision in ("v2.1.0", self.commit[:12], "main", "0" * 40):
            with self.subTest(revision=revision), self.assertRaises(SystemExit):
                self.sync(revision)

    def test_a_tag_whose_version_is_not_the_packages_is_refused(self):
        self.git("tag", "-a", "v2.2.0", "-m", "wrong")
        with self.assertRaises(SystemExit) as caught:
            self.sync("v2.2.0")
        self.assertIn("2.1.0", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
