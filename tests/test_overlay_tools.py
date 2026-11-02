"""The generic overlay runtime tools: acquire, verify, serve, publish.

`templates/overlay/runtime/tools/serve.py` and `build_site.py` are copied
verbatim into a companion's `pdf-overlay/`, so everything about one companion
has to reach them through `overlay.config.json` and the Literature Links
annotations in the bundle `GENERATED.json` records. These tests hold the
behaviour section 5 of `docs/overlay.md` asks for, against a synthetic
annotation file, a synthetic
configuration and a synthetic PDF in a temporary directory: no network (the
URL opener is injected), no real build tool (a fake one on PATH, reached
through a symlink), and nothing written outside the temporary directory.
"""
from __future__ import annotations

# The first import, before anything reads HOME: it pins HOME at a temporary
# directory for `unittest discover`, which loads no conftest.py.  The second
# spelling is for `python3 -m unittest tests.<module>`.  See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from overlay import shards as overlay_shards                          # noqa: E402
from overlay import sync as overlay_sync                              # noqa: E402

TOOLS = SKILL / "templates" / "overlay" / "runtime" / "tools"
NAMESPACE = overlay_shards.NAMESPACE
FILE = "annotations/1.1/lemma-1.1.1.json"
BUNDLE = overlay_sync.BUNDLE_NAME


def load_tool(name: str):
    """Import a runtime tool by path, the way a companion runs it."""
    path = TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"overlay_{name}", path)
    if spec is None or spec.loader is None:                  # pragma: no cover
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SERVE = load_tool("serve")
BUILD = load_tool("build_site")
CHECK = load_tool("check_generated")

#: A synthetic PDF: the signature and the terminal marker the tools check, and
#: nothing of anybody's book.
FIXTURE_PDF = (b"%PDF-1.7\n"
               b"synthetic overlay fixture; not a line of any source\n"
               b"%%EOF\n")
SOURCE_ID = "notes-pdf"
SOURCE_TITLE = "Elementary Sets: Lecture Notes (synthetic)"
PDF_URL = "https://notes.example.test/notes.pdf"
MIRROR_URL = "https://mirror.example.test/notes.pdf"
INDEX_HTML = ("<!doctype html>\n<html>\n  <head><title>site</title></head>\n"
              "  <body>\n    <main>built site</main>\n  </body>\n</html>\n")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@contextlib.contextmanager
def quiet():
    """The tools print progress lines; a test does not need them."""
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        yield stream


class FakeResponse:
    """One HTTPS response, as `urllib`'s opener would hand it over."""

    def __init__(self, raw: bytes, *, content_type: str = "application/pdf",
                 final_url: str = PDF_URL, status: int = 200,
                 content_length: int | None = -1, fail_after: int | None = None) -> None:
        self.raw = raw
        self.offset = 0
        self.final_url = final_url
        self.status = status
        self.fail_after = fail_after
        self.headers = Message()
        if content_type:
            self.headers["Content-Type"] = content_type
        if content_length is None:
            pass
        else:
            self.headers["Content-Length"] = str(
                len(raw) if content_length == -1 else content_length)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.final_url

    def read(self, size: int) -> bytes:
        if self.fail_after is not None and self.offset >= self.fail_after:
            raise OSError("synthetic interrupted read")
        if self.fail_after is not None:
            size = min(size, self.fail_after - self.offset)
        block = self.raw[self.offset:self.offset + size]
        self.offset += len(block)
        return block


class FakeOpener:
    """An opener that answers per URL, so alternates can be driven apart."""

    def __init__(self, answers: dict[str, object] | None = None, *,
                 response: object | None = None, error: BaseException | None = None) -> None:
        self.answers = answers or {}
        self.response = response
        self.error = error
        self.calls: list[tuple[str, float]] = []

    def open(self, request, timeout):                        # type: ignore[no-untyped-def]
        self.calls.append((request.full_url, timeout))
        self.request = request
        answer = self.answers.get(request.full_url, self.response)
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            if self.error is not None:
                raise self.error
            raise urllib.error.URLError("synthetic: no answer for this URL")
        return answer


FAKE_BUILD_TOOL = """#!/bin/sh
printf '%s\\n' "ran $*" >> "$OVERLAY_TEST_LOG"
mkdir -p .lake/build/literate-html
{
  printf '%s\\n' '<!doctype html>'
  printf '%s\\n' '<html>'
  printf '%s\\n' '  <body>'
  printf '%s\\n' '    <main>built site</main>'
  printf '%s\\n' '  </body>'
  printf '%s\\n' '</html>'
} > .lake/build/literate-html/index.html
"""


class Companion:
    """A synthetic companion with a generated `pdf-overlay/` directory."""

    def __init__(self, base: Path) -> None:
        self.root = base / "companion"
        self.overlay = self.root / "pdf-overlay"
        (self.overlay / "tools").mkdir(parents=True)
        (self.overlay / "viewer").mkdir()
        (self.overlay / "vendor" / "pdfjs").mkdir(parents=True)
        (self.root / "annotations").mkdir()
        (self.overlay / "viewer" / "index.html").write_text(
            "<!doctype html><title>overlay</title>\n", encoding="utf-8")
        (self.overlay / "viewer" / "viewer.js").write_text(
            "export const overlay = true;\n", encoding="utf-8")
        (self.overlay / "vendor" / "pdfjs" / "LICENSE").write_text(
            "fixture licence\n", encoding="utf-8")
        for name in ("serve", "build_site", "overlay_annotations", "verso_extract"):
            shutil.copy2(TOOLS / f"{name}.py", self.overlay / "tools" / f"{name}.py")
        shutil.copytree(SKILL / "vendor" / "literature_links", self.overlay / "tools" / "literature_links",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.log = base / "commands.log"
        self.bin = base / "bin"
        self.bin.mkdir()
        self.write_config()
        self.write_document()

    # -- the two data files ------------------------------------------------
    def config_value(self, **overrides) -> dict:
        value = {
            "schema": "formal-companion/overlay-config",
            "schema_version": 1,
            "source_id": SOURCE_ID,
            "title": "Synthetic overlay",
            "pdf": {"file_name": "notes.pdf", "cache_dir": ".lake/overlay-pdf",
                    "route": "/__overlay_pdf__/notes.pdf"},
            "site": {"output_root": ".lake/build/literate-html", "index": "index.html",
                     "mount": "pdf-overlay", "link_text": "Open the overlay"},
            "build": {"commands": [["lake", "build"]]},
            "serve": {"host": "127.0.0.1", "port": 8000},
        }
        value.update(overrides)
        return value

    def write_config(self, **overrides) -> Path:
        path = self.overlay / "overlay.config.json"
        path.write_text(json.dumps(self.config_value(**overrides), indent=2) + "\n",
                        encoding="utf-8")
        return path

    def publish(self, license: str = "CC-BY-4.0") -> Path:
        """The config with its PDF published under ``license`` (section 3's `pdf.publish`)."""
        pdf = dict(self.config_value()["pdf"], publish={"license": license})
        return self.write_config(pdf=pdf)

    def write_raw_config(self, text: str) -> Path:
        path = self.overlay / "overlay.config.json"
        path.write_text(text, encoding="utf-8")
        return path

    def record_value(self, **source_overrides) -> dict:
        """One Literature Links record of the PDF; overrides use the old source spellings."""
        over = {"id": SOURCE_ID, "kind": "pdf", "title": SOURCE_TITLE, "sha256": digest(FIXTURE_PDF),
                "bytes": len(FIXTURE_PDF), "url": PDF_URL, "physical_page_count": 4}
        over.update(source_overrides)
        ext = {k: over[k] for k in ("bytes", "physical_page_count", "alternate_urls")
               if over.get(k) is not None}
        ext.update(source_id=over["id"], region="lemma-1.1.1@p001")
        citation = {"id": over["id"], "type": "book", "title": over["title"]}
        if over["url"] is not None:
            citation["URL"] = over["url"]
        location = {"page": 1, "rect": [0.1, 0.1, 0.5, 0.2]} if over["kind"] == "pdf" else {"fragment": "lemma"}
        return {
            "id": "lemma-1.1.1",
            "subject": [{"type": over["kind"], "uri": over["url"] or "notes.pdf", "sha256": over["sha256"],
                         "citation": citation, "location": location, "title": "Lemma 1.1.1",
                         "extensions": {NAMESPACE: ext}}],
            "object": [],
        }

    def write_document(self, objects: list | None = None, **source_overrides) -> Path:
        """The annotation file, the bundle and the `GENERATED.json` that records them."""
        path = self.root / FILE
        path.parent.mkdir(parents=True, exist_ok=True)

        def value() -> dict:
            return dict(self.record_value(**source_overrides), **({"object": objects} if objects else {}))

        path.write_bytes(overlay_shards.annotation_bytes(value()))
        files = {FILE: path.read_bytes()}
        bundle = overlay_sync.bundle_bytes({FILE: value()})
        (self.overlay / BUNDLE).write_bytes(bundle)
        record = {"annotations": {"directory": "annotations", "count": 1,
                                  "manifest_sha256": overlay_sync.manifest_sha256(files),
                                  "bundle": {"path": BUNDLE, "sha256": digest(bundle)}}}
        (self.overlay / "GENERATED.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return path

    # -- the cache and the site -------------------------------------------
    @property
    def cache_path(self) -> Path:
        return self.root / ".lake" / "overlay-pdf" / "notes.pdf"

    def write_cache(self, raw: bytes = FIXTURE_PDF) -> Path:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_bytes(raw)
        return self.cache_path

    @property
    def site(self) -> Path:
        return self.root / ".lake" / "build" / "literate-html"

    def write_site(self, index: str = INDEX_HTML) -> Path:
        self.site.mkdir(parents=True, exist_ok=True)
        (self.site / "index.html").write_text(index, encoding="utf-8")
        return self.site

    # -- a build tool on PATH, reached through a symlink -------------------
    def fake_build_tool(self, name: str = "lake") -> Path:
        real = self.bin / f"{name}-real"
        real.write_text(FAKE_BUILD_TOOL, encoding="utf-8")
        real.chmod(0o755)
        link = self.bin / name
        link.symlink_to(real)
        return link

    def environment(self) -> dict[str, str]:
        return {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "OVERLAY_TEST_LOG": str(self.log)}

    # -- running them ------------------------------------------------------
    def config(self):
        return SERVE.load_config(self.overlay)

    def record(self):
        return SERVE.load_source_record(self.overlay, self.config())

    def run_tool(self, name: str, *arguments: str, env: dict[str, str] | None = None):
        environment = dict(os.environ)
        environment.update(self.environment())
        environment.update(env or {})
        return subprocess.run(
            [sys.executable, "-B", str(self.overlay / "tools" / f"{name}.py"), *arguments],
            cwd=self.root, env=environment, check=False, capture_output=True, text=True,
            timeout=120)


class OverlayToolsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="overlay-tools-test-")
        self.base = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.companion = Companion(self.base)


# ---------------------------------------------------------------------------
# the two data files are the only per-companion input
# ---------------------------------------------------------------------------

class TestTheConfigIsTheContract(OverlayToolsTestCase):
    """Section 3: required keys, defaults, and refusals that name the key."""

    def test_defaults_fill_in_everything_but_the_required_keys(self):
        self.companion.write_raw_config(json.dumps({
            "schema": "formal-companion/overlay-config",
            "schema_version": 1,
            "source_id": SOURCE_ID,
            "title": "Synthetic overlay",
            "pdf": {"file_name": "notes.pdf"},
            "site": {},
        }))
        config = self.companion.config()
        self.assertEqual(config.cache_dir, SERVE.DEFAULT_CACHE_DIR)
        self.assertEqual(config.route, "/__overlay_pdf__/notes.pdf")
        self.assertEqual(config.output_root, SERVE.DEFAULT_OUTPUT_ROOT)
        self.assertEqual((config.index, config.mount), ("index.html", "pdf-overlay"))
        self.assertEqual(config.link_text, "Synthetic overlay")
        self.assertEqual((config.host, config.port), ("127.0.0.1", 8000))
        self.assertEqual(config.build_commands, ())
        self.assertEqual(config.route_prefix, "/__overlay_pdf__/")

    def test_an_unknown_key_is_an_error_in_both_tools(self):
        self.companion.write_config(nickname="x")
        for module, error in ((SERVE, SERVE.OverlayError), (BUILD, BUILD.BuildError)):
            with self.subTest(tool=module.__name__):
                with self.assertRaisesRegex(error, r"unknown keys \['nickname'\]"):
                    module.load_config(self.companion.overlay)

    def test_display_minimal_must_be_true_or_false_in_both_tools(self):
        self.companion.write_config(display={"minimal": "yes"})
        for module, error in ((SERVE, SERVE.OverlayError), (BUILD, BUILD.BuildError)):
            with self.subTest(tool=module.__name__):
                with self.assertRaisesRegex(error, r"display\.minimal: expected true or false"):
                    module.load_config(self.companion.overlay)

    def test_a_missing_required_key_names_itself(self):
        value = self.companion.config_value()
        del value["source_id"]
        self.companion.write_raw_config(json.dumps(value))
        with self.assertRaisesRegex(SERVE.OverlayError, r"missing keys \['source_id'\]"):
            self.companion.config()

    def test_the_pdf_route_must_be_reserved_and_traversal_free(self):
        for route in ("/pdf/notes.pdf", "/__overlay_pdf__/../notes.pdf",
                      "/__overlay_pdf__/", ""):
            with self.subTest(route=route):
                self.companion.write_config(
                    pdf={"file_name": "notes.pdf", "route": route})
                with self.assertRaises(SERVE.OverlayError):
                    self.companion.config()

    def test_a_published_pdf_moves_the_route_beside_the_config_in_serve(self):
        self.companion.publish("CC-BY-SA-4.0")
        config = self.companion.config()
        self.assertEqual(config.publish_license, "CC-BY-SA-4.0")
        self.assertEqual(config.published_path, "pdf-overlay/notes.pdf")
        self.assertEqual(config.route, "/pdf-overlay/notes.pdf")
        self.assertEqual(config.route_prefix, "/__", "the mount's other files stay servable")
        built = BUILD.load_config(self.companion.overlay)
        self.assertEqual((built.publish_license, built.file_name, built.cache_dir),
                         ("CC-BY-SA-4.0", "notes.pdf", ".lake/overlay-pdf"))

    def test_with_no_publish_nothing_about_the_route_changes(self):
        config = self.companion.config()
        self.assertEqual((config.publish_license, config.published_path), ("", ""))
        self.assertEqual((config.route, config.route_prefix),
                         ("/__overlay_pdf__/notes.pdf", "/__overlay_pdf__/"))
        self.assertEqual(BUILD.load_config(self.companion.overlay).publish_license, "")

    def test_publishing_needs_an_open_license_and_a_pdf_file_name_in_both_tools(self):
        cases = (
            ({"file_name": "notes.pdf", "publish": {}}, r"missing keys \['license'\]"),
            ({"file_name": "notes.pdf", "publish": {"license": "CC-BY-NC-4.0"}},
             r"pdf\.publish\.license: expected one of Apache-2\.0, .*'CC-BY-NC-4\.0'"),
            ({"file_name": "notes.pdf", "publish": {"license": None}}, r"pdf\.publish\.license"),
            ({"file_name": "notes.pdf", "publish": "MIT"}, r"pdf\.publish: expected an object"),
            ({"file_name": "notes.djvu", "publish": {"license": "MIT"}},
             r"pdf\.file_name: a published PDF's file name ends in \.pdf"),
        )
        for pdf, message in cases:
            self.companion.write_config(pdf=pdf)
            for module, error in ((SERVE, SERVE.OverlayError), (BUILD, BUILD.BuildError)):
                with self.subTest(pdf=pdf, tool=module.__name__):
                    with self.assertRaisesRegex(error, message):
                        module.load_config(self.companion.overlay)

    def test_paths_must_stay_inside_the_companion(self):
        cases = (
            {"pdf": {"file_name": "notes.pdf", "cache_dir": "/tmp/cache"}},
            {"pdf": {"file_name": "notes.pdf", "cache_dir": "../cache"}},
            {"site": {"output_root": "/var/www"}},
            {"site": {"output_root": "../site"}},
            {"site": {"mount": "../mount"}},
            {"pdf": {"file_name": "../notes.pdf"}},
            {"pdf": {"file_name": "sub/notes.pdf"}},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.companion.write_config(**overrides)
                with self.assertRaises(SERVE.OverlayError):
                    self.companion.config()
                with self.assertRaises(BUILD.BuildError):
                    BUILD.load_config(self.companion.overlay)

    def test_the_serve_host_must_be_a_loopback_address(self):
        for host in ("0.0.0.0", "192.0.2.7", "localhost"):
            with self.subTest(host=host):
                self.companion.write_config(serve={"host": host, "port": 8000})
                with self.assertRaisesRegex(SERVE.OverlayError, "loopback"):
                    self.companion.config()
        self.companion.write_config(serve={"host": "::1", "port": 1024})
        self.assertEqual(self.companion.config().host, "::1")

    def test_a_duplicate_json_key_is_refused_before_anything_is_read(self):
        self.companion.write_raw_config(
            '{"schema": "formal-companion/overlay-config", "schema": "x"}')
        with self.assertRaisesRegex(SERVE.OverlayError, "duplicate JSON key"):
            self.companion.config()

    def test_json_python_cannot_load_is_invalid_json_to_build_site_and_check_generated(self):
        # neither is a JSONDecodeError: an integer of more digits than `int`
        # converts raises ValueError, and arrays nested past the stack RecursionError
        for case, text in (("long integer", '{"schema": ' + "1" * 5000 + "}"),
                           ("deep nesting", "[" * 200000 + "]" * 200000)):
            self.companion.write_raw_config(text)
            with self.subTest(case=case, tool="build_site"):
                with self.assertRaisesRegex(BUILD.BuildError, r"^overlay\.config\.json: invalid JSON: "):
                    BUILD.load_config(self.companion.overlay)
            with self.subTest(case=case, tool="check_generated"):
                self.assertRegex("\n".join(CHECK.problems(self.companion.overlay)),
                                 r"(?m)^overlay\.config\.json: invalid JSON: ")

    def test_every_strict_reader_refuses_what_json_cannot_hold(self):
        # the four runtime readers agree: a crash of `json` is invalid JSON, and a
        # number too large for a float would be Infinity, which none of them takes
        annotations = load_tool("overlay_annotations")
        readers = (("serve", lambda raw: SERVE.strict_json(raw, "x.json"), SERVE.OverlayError),
                   ("build_site", lambda raw: BUILD.strict_json(raw, "x.json"), BUILD.BuildError),
                   ("check_generated", lambda raw: CHECK.strict_json(raw, "x.json"), CHECK.Unreadable),
                   ("overlay_annotations", lambda raw: annotations.strict_json(raw.decode(), "x.json"),
                    annotations.AnnotationError))
        cases = (("long integer", '{"n": ' + "1" * 5000 + "}", "invalid JSON"),
                 ("deep nesting", "[" * 200000 + "]" * 200000, "invalid JSON"),
                 ("exponent", '{"n": 1e400}', "number out of range: 1e400"),
                 ("long decimal", '{"n": ' + "9" * 400 + ".5}", "number out of range"))
        for name, read, error in readers:
            for case, text, message in cases:
                with self.subTest(tool=name, case=case):
                    with self.assertRaisesRegex(error, "^x\\.json: " + message):
                        read(text.encode())
            with self.subTest(tool=name, case="a finite number"):
                self.assertEqual(read(b'{"n": 2.5e3}'), {"n": 2500.0})


class TestThePdfIdentityComesFromTheDocument(OverlayToolsTestCase):
    """Section 3: sha256, bytes, url and alternate_urls are the record's."""

    def test_the_record_gives_the_identity_and_the_urls_in_order(self):
        self.companion.write_document(alternate_urls=[MIRROR_URL, PDF_URL])
        record = self.companion.record()
        self.assertEqual(record.sha256, digest(FIXTURE_PDF))
        self.assertEqual(record.bytes, len(FIXTURE_PDF))
        self.assertEqual(record.urls, (PDF_URL, MIRROR_URL))
        self.assertEqual(record.title, SOURCE_TITLE)

    def test_annotations_without_the_configured_source_are_refused(self):
        for override in ({"id": "other-pdf"}, {"kind": "html"}):
            with self.subTest(**override):
                self.companion.write_document(**override)
                with self.assertRaisesRegex(SERVE.OverlayError,
                                            f"no annotation is a record of the PDF '{SOURCE_ID}'"):
                    self.companion.record()

    def test_a_source_that_pins_no_hash_is_refused(self):
        self.companion.write_document(sha256="NOTAHASH")
        with self.assertRaisesRegex(SERVE.OverlayError, "no lowercase sha256"):
            self.companion.record()

    def test_a_plain_http_url_in_the_record_is_refused(self):
        self.companion.write_document(url="http://notes.example.test/notes.pdf")
        with self.assertRaisesRegex(SERVE.OverlayError, "only https is fetched"):
            self.companion.record()
        self.companion.write_document(alternate_urls=["http://mirror.example.test/n.pdf"])
        with self.assertRaisesRegex(SERVE.OverlayError, "only https is fetched"):
            self.companion.record()

    def test_a_url_with_credentials_or_a_fragment_is_refused(self):
        for url in ("https://user:pw@notes.example.test/notes.pdf",
                    "https://notes.example.test/notes.pdf#page=2",
                    "https://notes.example.test:8443/notes.pdf"):
            with self.subTest(url=url):
                self.companion.write_document(url=url)
                with self.assertRaises(SERVE.OverlayError):
                    self.companion.record()

    def test_a_generated_record_without_the_bundle_is_refused(self):
        (self.companion.overlay / "GENERATED.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(SERVE.OverlayError, "GENERATED.json.annotations"):
            self.companion.record()
        self.companion.write_document()
        (self.companion.overlay / BUNDLE).write_bytes(b"{}\n")
        with self.assertRaisesRegex(SERVE.OverlayError, f"{BUNDLE}: sha256"):
            self.companion.record()


# ---------------------------------------------------------------------------
# acquire, verify, cache, revalidate, refresh
# ---------------------------------------------------------------------------

class TestAcquisition(OverlayToolsTestCase):
    """Section 5: HTTPS only, verify, install atomically, revalidate warm."""

    def token(self, digit: str = "1"):
        return lambda: digit * 16

    def test_a_cold_run_fetches_verifies_and_installs_atomically(self):
        opener = FakeOpener({PDF_URL: FakeResponse(FIXTURE_PDF)})
        with quiet() as out:
            state, actual = SERVE.cache_state(
                self.companion.root, self.companion.config(), self.companion.record(),
                opener=opener, token_factory=self.token())
        self.assertEqual((state, actual), ("acquired", digest(FIXTURE_PDF)))
        self.assertEqual(self.companion.cache_path.read_bytes(), FIXTURE_PDF)
        self.assertEqual(list(self.companion.cache_path.parent.glob("*.tmp")), [])
        self.assertEqual(opener.calls[0][0], PDF_URL)
        self.assertEqual(opener.calls[0][1], SERVE.DOWNLOAD_TIMEOUT)
        self.assertEqual(opener.request.get_method(), "GET")
        self.assertEqual(opener.request.get_header("Accept"), "application/pdf")
        self.assertEqual(out.getvalue(), "")

    def test_a_warm_cache_is_revalidated_and_never_touches_the_network(self):
        cache = self.companion.write_cache()
        inode = cache.stat().st_ino
        opener = FakeOpener(error=AssertionError("a warm cache attempted a fetch"))
        with quiet():
            state, actual = SERVE.cache_state(
                self.companion.root, self.companion.config(), self.companion.record(),
                opener=opener)
        self.assertEqual((state, actual), ("reused", digest(FIXTURE_PDF)))
        self.assertEqual(opener.calls, [])
        self.assertEqual(cache.stat().st_ino, inode)

    def test_a_cached_file_that_fails_revalidation_is_refetched(self):
        cache = self.companion.write_cache(b"%PDF-1.7\nwrong bytes\n%%EOF\n")
        stale = cache.stat().st_ino
        opener = FakeOpener({PDF_URL: FakeResponse(FIXTURE_PDF)})
        with quiet() as out:
            state, actual = SERVE.cache_state(
                self.companion.root, self.companion.config(), self.companion.record(),
                opener=opener, token_factory=self.token("2"))
        self.assertEqual((state, actual), ("refreshed", digest(FIXTURE_PDF)))
        self.assertIn("cache=stale", out.getvalue())
        self.assertEqual(cache.read_bytes(), FIXTURE_PDF)
        self.assertNotEqual(cache.stat().st_ino, stale)

    def test_refresh_pdf_reacquires_bytes_that_already_verify(self):
        cache = self.companion.write_cache()
        inode = cache.stat().st_ino
        opener = FakeOpener({PDF_URL: FakeResponse(FIXTURE_PDF)})
        with quiet():
            state, _ = SERVE.cache_state(
                self.companion.root, self.companion.config(), self.companion.record(),
                refresh=True, opener=opener, token_factory=self.token("3"))
        self.assertEqual(state, "refreshed")
        self.assertEqual(len(opener.calls), 1)
        self.assertNotEqual(cache.stat().st_ino, inode)

    def test_a_hash_mismatch_installs_nothing_and_leaves_no_temporary(self):
        other = b"%PDF-1.7\nnot the bytes the record pins\n%%EOF\n"
        self.companion.write_document(bytes=len(other))
        opener = FakeOpener({PDF_URL: FakeResponse(other)})
        with self.assertRaisesRegex(SERVE.OverlayError, "sha256 is"):
            SERVE.acquire_pdf(self.companion.write_cache(b"x").parent / "notes.pdf",
                              self.companion.record(), opener=opener,
                              token_factory=self.token("4"))
        self.assertEqual(self.companion.cache_path.read_bytes(), b"x")
        self.assertEqual(list(self.companion.cache_path.parent.glob("*.tmp")), [])

    def test_a_wrong_size_status_or_media_type_is_refused(self):
        self.companion.cache_path.parent.mkdir(parents=True)
        cases = {
            "short": FakeResponse(FIXTURE_PDF[:-4], content_length=None),
            "long": FakeResponse(FIXTURE_PDF + b"extra", content_length=None),
            "status": FakeResponse(FIXTURE_PDF, status=500),
            "media": FakeResponse(FIXTURE_PDF, content_type="text/html"),
            "length": FakeResponse(FIXTURE_PDF, content_length=99),
            "interrupted": FakeResponse(FIXTURE_PDF, fail_after=20),
            "plain_http_final": FakeResponse(FIXTURE_PDF,
                                             final_url="http://notes.example.test/n.pdf"),
        }
        for index, (name, response) in enumerate(sorted(cases.items())):
            with self.subTest(case=name):
                with self.assertRaises(SERVE.OverlayError):
                    SERVE.acquire_pdf(self.companion.cache_path, self.companion.record(),
                                      opener=FakeOpener({PDF_URL: response}),
                                      token_factory=lambda i=index: f"{i:016x}")
                self.assertFalse(self.companion.cache_path.exists())
                self.assertEqual(
                    list(self.companion.cache_path.parent.glob("*.tmp")), [])

    def test_an_alternate_url_is_tried_when_the_first_one_fails(self):
        self.companion.write_document(alternate_urls=[MIRROR_URL])
        self.companion.cache_path.parent.mkdir(parents=True)
        opener = FakeOpener({PDF_URL: urllib.error.URLError("synthetic DNS failure"),
                             MIRROR_URL: FakeResponse(FIXTURE_PDF, final_url=MIRROR_URL)})
        actual = SERVE.acquire_pdf(self.companion.cache_path, self.companion.record(),
                                   opener=opener, token_factory=self.token("5"))
        self.assertEqual(actual, digest(FIXTURE_PDF))
        self.assertEqual([call[0] for call in opener.calls], [PDF_URL, MIRROR_URL])

    def test_when_every_url_fails_the_message_names_each_one(self):
        self.companion.write_document(alternate_urls=[MIRROR_URL])
        self.companion.cache_path.parent.mkdir(parents=True)
        opener = FakeOpener({PDF_URL: urllib.error.URLError("first is down"),
                             MIRROR_URL: TimeoutError("second timed out")})
        with self.assertRaises(SERVE.OverlayError) as caught:
            SERVE.acquire_pdf(self.companion.cache_path, self.companion.record(),
                              opener=opener, token_factory=self.token("6"))
        message = str(caught.exception)
        self.assertIn(PDF_URL, message)
        self.assertIn(MIRROR_URL, message)
        self.assertEqual(message.count("\n"), 0, "one STOP line, not a traceback")

    def test_a_record_with_no_url_says_where_to_put_the_file(self):
        self.companion.write_document(url=None)
        self.companion.cache_path.parent.mkdir(parents=True)
        with self.assertRaisesRegex(SERVE.OverlayError, "names no https url"):
            SERVE.acquire_pdf(self.companion.cache_path, self.companion.record())

    def test_a_symlinked_cache_file_is_refused(self):
        target = self.base / "elsewhere.pdf"
        target.write_bytes(FIXTURE_PDF)
        self.companion.cache_path.parent.mkdir(parents=True)
        self.companion.cache_path.symlink_to(target)
        with self.assertRaisesRegex(SERVE.OverlayError, "nonsymlink"):
            SERVE.cache_state(self.companion.root, self.companion.config(),
                              self.companion.record())

    def test_a_stale_acquisition_temporary_is_removed_on_the_next_run(self):
        cache = self.companion.write_cache()
        stale = cache.parent / ".notes.pdf.1234.abcdefabcdefabcd.tmp"
        stale.write_bytes(b"partial")
        with quiet():
            state, _ = SERVE.cache_state(
                self.companion.root, self.companion.config(), self.companion.record(),
                opener=FakeOpener(error=AssertionError("no fetch")))
        self.assertEqual(state, "reused")
        self.assertFalse(stale.exists())

    def test_bytes_that_are_not_a_pdf_at_all_are_refused(self):
        payload = b"<html>an error page</html>\n"
        self.companion.write_document(sha256=digest(payload), bytes=len(payload))
        self.companion.cache_path.parent.mkdir(parents=True)
        with self.assertRaisesRegex(SERVE.OverlayError, "%PDF-"):
            SERVE.acquire_pdf(self.companion.cache_path, self.companion.record(),
                              opener=FakeOpener({PDF_URL: FakeResponse(payload)}),
                              token_factory=self.token("7"))


class TestRedirectsOnlyToHttps(OverlayToolsTestCase):
    """Section 1 rule 6: the redirect policy is kept, the sealed chain is not."""

    def handler(self, start: str = PDF_URL):
        return SERVE.HttpsOnlyRedirectHandler(start)

    def test_an_https_redirect_is_followed(self):
        request = urllib.request.Request(PDF_URL)
        handler = self.handler()
        with mock.patch.object(urllib.request.HTTPRedirectHandler, "redirect_request",
                               return_value="followed") as parent:
            self.assertEqual(
                handler.redirect_request(request, None, 302, "", {}, MIRROR_URL),
                "followed")
        self.assertEqual(parent.call_args[0][-1], MIRROR_URL)
        self.assertEqual(handler.seen, [PDF_URL, MIRROR_URL])

    def test_a_redirect_to_plain_http_is_refused(self):
        request = urllib.request.Request(PDF_URL)
        with self.assertRaisesRegex(SERVE.OverlayError, "only https is fetched"):
            self.handler().redirect_request(
                request, None, 302, "", {}, "http://mirror.example.test/notes.pdf")

    def test_a_redirect_loop_and_too_many_hops_are_refused(self):
        request = urllib.request.Request(PDF_URL)
        with self.assertRaisesRegex(SERVE.OverlayError, "loop"):
            self.handler().redirect_request(request, None, 302, "", {}, PDF_URL)
        handler = self.handler()
        with mock.patch.object(urllib.request.HTTPRedirectHandler, "redirect_request",
                               side_effect=lambda *a: a[0]):
            for hop in range(handler.max_hops):
                handler.redirect_request(request, None, 302, "", {},
                                         f"https://mirror.example.test/{hop}.pdf")
            with self.assertRaisesRegex(SERVE.OverlayError, "limit"):
                handler.redirect_request(request, None, 302, "", {},
                                         "https://mirror.example.test/last.pdf")


# ---------------------------------------------------------------------------
# the optional build
# ---------------------------------------------------------------------------

class TestTheConfiguredBuild(OverlayToolsTestCase):
    """Section 5: every argv in `build.commands`, no shell, then build_site."""

    def test_build_runs_each_configured_argv_and_then_build_site(self):
        self.companion.write_config(
            build={"commands": [["lake", "build"], ["lake", "query", ":site"]]})
        self.companion.fake_build_tool()
        calls: list[tuple[str, ...]] = []

        def runner(command, root, **kwargs):
            calls.append(tuple(command))
            return SERVE.ProcessResult(0, "", "")

        with mock.patch.dict(os.environ, self.companion.environment()), quiet():
            executed = SERVE.run_build(self.companion.overlay, self.companion.root,
                                       self.companion.config(), runner=runner)
        self.assertEqual(calls, executed)
        self.assertEqual([command[1:] for command in calls[:2]],
                         [("build",), ("query", ":site")])
        self.assertEqual(calls[0][0], str((self.companion.bin / "lake-real").resolve()))
        self.assertEqual(calls[-1][1:],
                         ("-B", str(self.companion.overlay / "tools" / "build_site.py")))

    def test_a_symlinked_executable_is_accepted_through_realpath(self):
        link = self.companion.fake_build_tool()
        self.assertTrue(link.is_symlink())
        with mock.patch.dict(os.environ, self.companion.environment()):
            resolved = SERVE.resolve_executable("lake")
        self.assertEqual(resolved, str(link.resolve()))
        self.assertFalse(Path(resolved).is_symlink())

    def test_a_missing_or_unexecutable_build_command_is_refused(self):
        (self.companion.bin / "notexec").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.companion.bin / "notexec").chmod(0o644)
        with mock.patch.dict(os.environ, self.companion.environment()):
            with self.assertRaisesRegex(SERVE.OverlayError, "unavailable"):
                SERVE.resolve_executable("no-such-build-tool")
            with self.assertRaisesRegex(SERVE.OverlayError, "unavailable"):
                SERVE.resolve_executable("notexec")

    def test_a_build_command_that_fails_keeps_its_exit_status(self):
        with self.assertRaises(SERVE.ChildFailure) as caught:
            SERVE.run_process((sys.executable, "-c", "raise SystemExit(7)"),
                              self.companion.root)
        self.assertEqual(caught.exception.returncode, 7)
        self.assertEqual(caught.exception.command[1:], ("-c", "raise SystemExit(7)"))

    def test_the_build_runs_with_no_shell(self):
        source = (TOOLS / "serve.py").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)


# ---------------------------------------------------------------------------
# build_site.py
# ---------------------------------------------------------------------------

class TestBuildSite(OverlayToolsTestCase):
    """Section 5: copy, replace atomically, link once, write the receipt."""

    def published(self) -> dict[str, str]:
        mount = self.companion.site / "pdf-overlay"
        return {path.relative_to(mount).as_posix(): digest(path.read_bytes())
                for path in sorted(mount.rglob("*")) if path.is_file()}

    def test_it_publishes_the_runtime_the_two_instance_files_and_the_bundle(self):
        self.companion.write_site()
        summary = BUILD.build_site(self.companion.overlay)
        published = self.published()
        self.assertEqual(
            sorted(published),
            ["GENERATED.json", BUNDLE, "overlay.config.json", "receipt.json",
             "vendor/pdfjs/LICENSE", "verso.json", "viewer/index.html", "viewer/viewer.js"])
        self.assertEqual((self.companion.site / FILE).read_bytes(),
                         (self.companion.root / FILE).read_bytes(),
                         "the annotations sit at the site root as they sit at the repository root")
        self.assertEqual(summary["annotation_files"], 1)
        self.assertEqual(published["viewer/viewer.js"],
                         digest((self.companion.overlay / "viewer/viewer.js").read_bytes()))
        self.assertEqual(summary["mount"], "pdf-overlay")
        self.assertEqual(summary["link_href"], "pdf-overlay/viewer/index.html")

    def test_the_receipt_carries_the_sha256_of_every_file_it_wrote(self):
        self.companion.write_site()
        BUILD.build_site(self.companion.overlay)
        mount = self.companion.site / "pdf-overlay"
        receipt = json.loads((mount / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], BUILD.RECEIPT_SCHEMA)
        self.assertEqual(receipt["source_id"], SOURCE_ID)
        rows = {row["path"]: row["sha256"] for row in receipt["files"]}
        self.assertEqual(sorted(rows), ["GENERATED.json", BUNDLE, "overlay.config.json",
                                        "vendor/pdfjs/LICENSE", "verso.json", "viewer/index.html",
                                        "viewer/viewer.js"])
        self.assertEqual(receipt["annotations"], {"directory": "annotations", "files": [{
            "bytes": len((self.companion.root / FILE).read_bytes()), "path": "1.1/lemma-1.1.1.json",
            "sha256": digest((self.companion.root / FILE).read_bytes())}]})
        for relative, expected in rows.items():
            with self.subTest(path=relative):
                self.assertEqual(digest((mount / relative).read_bytes()), expected)
        index = self.companion.site / "index.html"
        self.assertEqual(receipt["index"]["sha256"], digest(index.read_bytes()))
        self.assertEqual(receipt["index"]["path"], "index.html")
        self.assertNotIn(str(self.companion.root),
                         (mount / "receipt.json").read_text(encoding="utf-8"))

    def test_a_second_run_is_byte_identical(self):
        self.companion.write_site()
        first = BUILD.build_site(self.companion.overlay)
        published = self.published()
        index = (self.companion.site / "index.html").read_bytes()
        second = BUILD.build_site(self.companion.overlay)
        self.assertEqual(first, second)
        self.assertEqual(published, self.published())
        self.assertEqual(index, (self.companion.site / "index.html").read_bytes())

    def test_a_stale_previous_copy_is_replaced_and_never_stops_the_run(self):
        self.companion.write_site()
        mount = self.companion.site / "pdf-overlay"
        (mount / "viewer").mkdir(parents=True)
        (mount / "viewer" / "gone.js").write_text("stale\n", encoding="utf-8")
        (mount / "receipt.json").write_text("{}\n", encoding="utf-8")
        BUILD.build_site(self.companion.overlay)
        self.assertNotIn("viewer/gone.js", self.published())
        self.assertNotEqual((mount / "receipt.json").read_text(encoding="utf-8"), "{}\n")
        self.assertEqual(list(self.companion.site.glob(".pdf-overlay.*")), [])

    def test_without_publish_no_pdf_reaches_the_site_even_with_one_cached(self):
        self.companion.write_cache()
        self.companion.write_site()
        BUILD.build_site(self.companion.overlay)
        self.assertNotIn("notes.pdf", self.published())
        self.assertEqual(list(self.companion.site.rglob("*.pdf")), [])

    def test_a_published_pdf_is_verified_and_placed_beside_the_config(self):
        self.companion.publish()
        self.companion.write_cache()
        self.companion.write_site()
        summary = BUILD.build_site(self.companion.overlay)
        published = self.published()
        self.assertEqual(published["notes.pdf"], digest(FIXTURE_PDF))
        self.assertEqual(published["overlay.config.json"],
                         digest((self.companion.overlay / "overlay.config.json").read_bytes()),
                         "the PDF sits beside the config the viewer fetches it next to")
        self.assertEqual([path.relative_to(self.companion.site).as_posix()
                          for path in self.companion.site.rglob("*.pdf")], ["pdf-overlay/notes.pdf"])
        receipt = json.loads((self.companion.site / "pdf-overlay" / "receipt.json").read_text(encoding="utf-8"))
        self.assertIn({"bytes": len(FIXTURE_PDF), "path": "notes.pdf", "sha256": digest(FIXTURE_PDF)},
                      receipt["files"])
        self.assertEqual(summary["files"], len(published) - 1, "every file but the receipt")
        SERVE.assert_site_servable(self.companion.site, self.companion.config().published_path)
        self.assertEqual(BUILD.build_site(self.companion.overlay), summary, "a second run is the same")

    def test_a_published_pdf_that_is_missing_or_does_not_verify_is_refused(self):
        self.companion.publish()
        self.companion.write_site()
        with self.assertRaisesRegex(BUILD.BuildError,
                                    r"pdf\.publish: \.lake/overlay-pdf/notes\.pdf does not exist; "
                                    r"`python3 pdf-overlay/tools/serve\.py --build-only` fetches"):
            BUILD.build_site(self.companion.overlay)
        for raw, message in ((FIXTURE_PDF[:-1] + b"X", r"sha256 is [0-9a-f]{64}; the records' PDF end says"),
                             (FIXTURE_PDF + b"\n", r"\d+ bytes; the records' PDF end says \d+"),
                             (b"plain" + FIXTURE_PDF[5:], r"not a PDF")):
            with self.subTest(message=message):
                self.companion.write_cache(raw)
                with self.assertRaisesRegex(BUILD.BuildError, message):
                    BUILD.build_site(self.companion.overlay)
        self.assertFalse((self.companion.site / "pdf-overlay").exists(), "nothing was published")
        self.companion.write_cache()
        self.companion.cache_path.unlink()
        self.companion.cache_path.symlink_to(self.base / "elsewhere.pdf")
        (self.base / "elsewhere.pdf").write_bytes(FIXTURE_PDF)
        with self.assertRaisesRegex(BUILD.BuildError, "ordinary nonsymlink file"):
            BUILD.build_site(self.companion.overlay)

    def test_a_file_the_bundle_does_not_hold_is_not_published(self):
        self.companion.write_site()
        (self.companion.root / FILE).write_bytes(overlay_shards.annotation_bytes(
            dict(self.companion.record_value(), note="Lemma 9.9.9")))
        with self.assertRaisesRegex(BUILD.BuildError, f"{FILE}: differs from the bundle"):
            BUILD.build_site(self.companion.overlay)
        self.assertFalse((self.companion.site / "pdf-overlay").exists())
        self.companion.write_document()
        (self.companion.root / "annotations" / "1.1" / "notes.txt").write_text("mine\n", encoding="utf-8")
        with self.assertRaisesRegex(BUILD.BuildError, "annotations/1.1/notes.txt: not an annotation file"):
            BUILD.build_site(self.companion.overlay)

    def test_a_file_that_holds_an_array_is_not_published(self):
        self.companion.write_site()
        record = self.companion.record_value()
        (self.companion.root / FILE).write_text(json.dumps([record, record]), encoding="utf-8")
        with self.assertRaisesRegex(BUILD.BuildError, f"{FILE}: a JSON file holds one annotation, an object, "
                                                      "not an array"):
            BUILD.build_site(self.companion.overlay)
        self.assertFalse((self.companion.site / "pdf-overlay").exists())

    def test_a_site_directory_that_is_not_an_annotation_directory_is_not_replaced(self):
        for extra in ("page.html", "1.1/page.html"):
            with self.subTest(extra=extra):
                self.companion.write_site()
                shutil.rmtree(self.companion.site / "annotations", ignore_errors=True)
                (self.companion.site / "annotations" / "1.1").mkdir(parents=True)
                (self.companion.site / "annotations" / extra).write_text("built\n", encoding="utf-8")
                with self.assertRaisesRegex(BUILD.BuildError, "may hold only"):
                    BUILD.build_site(self.companion.overlay)
                self.assertTrue((self.companion.site / "annotations" / extra).is_file())

    def test_the_link_block_is_inserted_once_and_then_replaced_in_place(self):
        self.companion.write_site()
        BUILD.build_site(self.companion.overlay)
        index = (self.companion.site / "index.html").read_text(encoding="utf-8")
        self.assertEqual(index.count(BUILD.LINK_START), 1)
        self.assertEqual(index.count(BUILD.LINK_END), 1)
        self.assertIn('href="pdf-overlay/viewer/index.html"', index)
        self.assertIn(">Open the overlay<", index)
        self.assertIn("<main>built site</main>", index)
        self.assertLess(index.index(BUILD.LINK_START), index.index("</body>"))

        site = self.companion.config_value()["site"]
        site["link_text"] = "A different label"
        self.companion.write_config(site=site)
        BUILD.build_site(self.companion.overlay)
        index = (self.companion.site / "index.html").read_text(encoding="utf-8")
        self.assertEqual(index.count(BUILD.LINK_START), 1)
        self.assertIn(">A different label<", index)
        self.assertNotIn("Open the overlay", index)

    def test_the_link_text_and_href_reach_the_index_escaped(self):
        site = self.companion.config_value()["site"]
        site["link_text"] = 'Notes & <script>"alert"</script>'
        self.companion.write_config(site=site)
        self.companion.write_site()
        BUILD.build_site(self.companion.overlay)
        index = (self.companion.site / "index.html").read_text(encoding="utf-8")
        self.assertIn("Notes &amp; &lt;script&gt;", index)
        self.assertNotIn("<script>", index)

    def test_the_block_is_idempotent_for_an_index_with_no_body_tag(self):
        raw = b"<main>no body element</main>\n"
        once = BUILD.linked_index(raw, "m/viewer/index.html", "Label")
        self.assertEqual(BUILD.linked_index(once, "m/viewer/index.html", "Label"), once)
        self.assertEqual(once.count(BUILD.LINK_START.encode()), 1)
        inline = b"<html><body><main>x</main></body></html>"
        once = BUILD.linked_index(inline, "m/viewer/index.html", "Label")
        self.assertEqual(BUILD.linked_index(once, "m/viewer/index.html", "Label"), once)

    def test_a_duplicated_or_unclosed_marker_is_refused(self):
        for index in (f"<body>{BUILD.LINK_START}{BUILD.LINK_END}{BUILD.LINK_START}"
                      f"{BUILD.LINK_END}</body>",
                      f"<body>{BUILD.LINK_START}</body>",
                      f"<body>{BUILD.LINK_END}x{BUILD.LINK_START}</body>"):
            with self.subTest(index=index[:40]):
                with self.assertRaises(BUILD.BuildError):
                    BUILD.linked_index(index.encode("utf-8"), "m/v/index.html", "L")

    def test_a_nested_index_gets_a_relative_href(self):
        site = self.companion.config_value()["site"]
        site["index"] = "pages/index.html"
        self.companion.write_config(site=site)
        (self.companion.site / "pages").mkdir(parents=True)
        (self.companion.site / "pages" / "index.html").write_text(
            INDEX_HTML, encoding="utf-8")
        summary = BUILD.build_site(self.companion.overlay)
        self.assertEqual(summary["link_href"], "../pdf-overlay/viewer/index.html")
        index = (self.companion.site / "pages" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="../pdf-overlay/viewer/index.html"', index)

    def test_an_absent_site_or_index_says_to_build_first(self):
        with self.assertRaisesRegex(BUILD.BuildError, "build the site first"):
            BUILD.build_site(self.companion.overlay)
        self.companion.site.mkdir(parents=True)
        with self.assertRaisesRegex(BUILD.BuildError, "site index"):
            BUILD.build_site(self.companion.overlay)

    def test_a_symlinked_output_root_or_mount_is_an_output_escape(self):
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("unchanged", encoding="utf-8")
        self.companion.write_site()
        (self.companion.site / "pdf-overlay").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(BUILD.BuildError, "output escape"):
            BUILD.build_site(self.companion.overlay)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ["sentinel"])

    def test_a_symlink_inside_the_generated_runtime_is_refused(self):
        self.companion.write_site()
        target = self.companion.overlay / "viewer" / "viewer.js"
        moved = self.base / "viewer.js.real"
        target.rename(moved)
        target.symlink_to(moved)
        with self.assertRaisesRegex(BUILD.BuildError, "symlink"):
            BUILD.build_site(self.companion.overlay)

    def test_verso_json_holds_the_statements_the_sites_pages_highlight(self):
        """Section 5: an empty file for a site with no such page, and the receipt records it."""
        site = self.companion.write_site()
        self.assertEqual(BUILD.build_site(self.companion.overlay)["verso"],
                         {"source": "none", "statements": 0, "targets": 0})
        mount = site / "pdf-overlay"
        empty = json.loads((mount / "verso.json").read_text(encoding="utf-8"))
        self.assertEqual(empty, {"hovers": {}, "schema": "formal-companion/overlay-verso", "schema_version": 1,
                                 "statements": {}})
        snippet = "def volume : Nat := 2"
        self.companion.write_document(objects=[{
            "type": "lean", "uri": "https://git.example.test/notes", "version": "0" * 40, "role": "key",
            "location": {"declaration": "Notes.Box.volume", "module": "Notes.Box"},
            "extensions": {NAMESPACE: {"destination": {"resolution": "exact"},
                                       "snippet": {"line": 3, "text": snippet}}}}])
        page = site / "Notes" / "Box" / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text(
            '<html><head><base href="../../"></head><body><div class="code-box">'
            '<code class="hl lean block"><span class="keyword token">def</span> '
            '<span class="const token" data-verso-hover="7" id="Notes___Box___volume">volume</span> : '
            '<span class="const token" data-verso-hover="8">Nat</span> := '
            '<span class="literal number token">2</span></code></div></body></html>\n', encoding="utf-8")
        (site / "-verso-docs.json").write_text(json.dumps({"7": "<code>Notes.Box.volume : Nat</code>"}),
                                               encoding="utf-8")
        summary = BUILD.build_site(self.companion.overlay)
        self.assertEqual(summary["verso"], {"source": "site", "statements": 1, "targets": 1})
        verso = json.loads((mount / "verso.json").read_text(encoding="utf-8"))
        self.assertEqual(verso["statements"], {"Notes/Box/index.html#Notes___Box___volume": [[
            ["kw", "def"], ["tx", " "], ["const", "volume", 7], ["tx", " : "], ["const", "Nat"], ["tx", " := "],
            ["lit", "2"]]]})
        self.assertEqual(verso["hovers"], {"7": {"signature": "Notes.Box.volume : Nat"}})
        receipt = json.loads((mount / "receipt.json").read_text(encoding="utf-8"))
        rows = {row["path"]: row["sha256"] for row in receipt["files"]}
        self.assertEqual(rows["verso.json"], digest((mount / "verso.json").read_bytes()))
        self.assertEqual(receipt["verso"], summary["verso"])

    # -- a committed verso.json, cut from a Verso build elsewhere ------------
    REMOTE = "https://git.example.test/notes/blob/main/Notes/Box.lean"
    #: The commit the committed file's build is of, and the one the cards pin.
    BUILT = "1" * 40

    def remote_end(self, name: str, snippet: str, line: int, version: str = BUILT) -> dict:
        return {"type": "lean", "uri": "https://git.example.test/notes", "version": version, "role": "key",
                "location": {"declaration": f"Notes.Box.{name}", "module": "Notes.Box"},
                "extensions": {NAMESPACE: {"destination": {"resolution": "exact", "href": self.REMOTE,
                                                           "fragment": f"L{line}"},
                                           "snippet": {"line": line, "text": snippet}}}}

    def write_committed(self, value: dict | None = None) -> bytes:
        """What `verso_extract.py --pages` writes for `volume` and `area`, as a companion commits it."""
        value = value or {
            "schema": "formal-companion/overlay-verso", "schema_version": 1,
            "build": {"commit": self.BUILT, "verso": {"inputRev": "v0.0.0-test", "rev": "2" * 40}},
            "hovers": {"7": {"signature": "Notes.Box.volume : Nat"}, "8": {"signature": "Notes.Box.area : Nat"}},
            "statements": {
                f"{self.REMOTE}#L3": [[["kw", "def"], ["tx", " "], ["const", "volume", 7], ["tx", " := "], ["lit", "2"]]],
                f"{self.REMOTE}#L5": [[["kw", "def"], ["tx", " "], ["const", "area", 8], ["tx", " := "], ["lit", "1"]]]}}
        raw = BUILD.verso_extract.dumps_lines(value)
        (self.companion.overlay / "verso.json").write_bytes(raw)
        return raw

    def test_a_site_with_no_verso_pages_publishes_the_committed_verso_json(self):
        site = self.companion.write_site()
        self.companion.write_document(objects=[self.remote_end("volume", "def volume := 2", 3),
                                               self.remote_end("area", "def area := 1", 5)])
        raw = self.write_committed()
        summary = BUILD.build_site(self.companion.overlay)
        self.assertEqual(summary["verso"], {"source": "committed", "sha256": digest(raw), "commit": self.BUILT,
                                            "dropped": 0, "statements": 2, "targets": 2})
        mount = site / "pdf-overlay"
        published = (mount / "verso.json").read_bytes()
        self.assertEqual(json.loads(published), json.loads(raw), "every statement still joins to its card")
        self.assertEqual(published.count(b"\n"), 1, "published in the compact form")
        receipt = json.loads((mount / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["verso"], summary["verso"])
        self.assertEqual({row["path"]: row["sha256"] for row in receipt["files"]}["verso.json"], digest(published))
        self.assertEqual(BUILD.build_site(self.companion.overlay), summary, "a second run is the same")

    def test_a_committed_statement_whose_snippet_changed_is_dropped_alone(self):
        site = self.companion.write_site()
        self.companion.write_document(objects=[self.remote_end("volume", "def volume := 3", 3),
                                               self.remote_end("area", "def area := 1", 5)])
        self.write_committed()
        summary = BUILD.build_site(self.companion.overlay)
        self.assertEqual((summary["verso"]["statements"], summary["verso"]["dropped"]), (1, 1))
        published = json.loads((site / "pdf-overlay" / "verso.json").read_text(encoding="utf-8"))
        self.assertEqual(list(published["statements"]), [f"{self.REMOTE}#L5"])
        self.assertEqual(published["hovers"], {"8": {"signature": "Notes.Box.area : Nat"}})
        self.assertEqual(published["build"]["commit"], self.BUILT)

    def test_a_committed_file_of_a_build_no_card_pins_is_refused(self):
        site = self.companion.write_site()
        self.companion.write_document(objects=[self.remote_end("volume", "def volume := 2", 3, "0" * 40),
                                               self.remote_end("area", "def area := 1", 5, "0" * 40)])
        self.write_committed()
        with self.assertRaisesRegex(BUILD.BuildError, f"verso.json: cut from a build of {self.BUILT}, "
                                                      f"but the cards pin {'0' * 40}"):
            BUILD.build_site(self.companion.overlay)
        self.assertFalse((site / "pdf-overlay").exists())
        self.companion.write_document(objects=[self.remote_end("volume", "def volume := 2", 3),
                                               self.remote_end("area", "def area := 1", 5, "0" * 40)])
        summary = BUILD.build_site(self.companion.overlay)
        self.assertEqual((summary["verso"]["statements"], summary["verso"]["dropped"]), (1, 1),
                         "a card that pins another commit keeps no statement of this build")
        published = json.loads((site / "pdf-overlay" / "verso.json").read_text(encoding="utf-8"))
        self.assertEqual(list(published["statements"]), [f"{self.REMOTE}#L3"])

    def test_a_committed_statement_whose_card_pins_a_tag_is_refused_not_dropped(self):
        site = self.companion.write_site()
        self.write_committed()
        for name, versions in (("only some", (self.BUILT, "v1.0.0")), ("all", ("v1.0.0", "v1.0.0"))):
            with self.subTest(name):
                self.companion.write_document(objects=[self.remote_end("volume", "def volume := 2", 3, versions[0]),
                                                       self.remote_end("area", "def area := 1", 5, versions[1])])
                with self.assertRaisesRegex(BUILD.BuildError, r"pdf-overlay/verso.json: .*'v1\.0\.0'.*annotations/"
                                                              r".*git rev-parse") as refused:
                    BUILD.build_site(self.companion.overlay)
                self.assertNotIn("from a build of that commit", str(refused.exception))
                self.assertFalse((site / "pdf-overlay").exists(), "nothing published")
        self.companion.write_document(objects=[self.remote_end("volume", "def volume := 2", 3),
                                               self.remote_end("area", "def area := 1", 5)])
        summary = BUILD.build_site(self.companion.overlay)
        self.assertEqual((summary["verso"]["statements"], summary["verso"]["dropped"]), (2, 0),
                         "given the commit, every statement is back")

    def test_a_tag_is_nothing_to_a_site_that_reads_no_committed_file(self):
        site = self.companion.write_site()
        self.companion.write_document(objects=[self.remote_end("volume", "def volume := 2", 3, "v1.0.0")])
        self.assertEqual(BUILD.build_site(self.companion.overlay)["verso"],
                         {"source": "none", "statements": 0, "targets": 1}, "no committed file, no pin to hold")
        self.write_committed()
        (site / "-verso-docs.json").write_text("{}", encoding="utf-8")
        self.assertEqual(BUILD.build_site(self.companion.overlay)["verso"],
                         {"source": "site", "statements": 0, "targets": 1}, "its own pages, whatever the pin says")

    def test_a_site_with_verso_pages_extracts_its_own_and_ignores_the_committed_file(self):
        site = self.companion.write_site()
        self.companion.write_document(objects=[self.remote_end("volume", "def volume := 2", 3)])
        self.write_committed()
        (site / "-verso-docs.json").write_text("{}", encoding="utf-8")
        summary = BUILD.build_site(self.companion.overlay)
        self.assertEqual(summary["verso"], {"source": "site", "statements": 0, "targets": 1})
        published = json.loads((site / "pdf-overlay" / "verso.json").read_text(encoding="utf-8"))
        self.assertEqual(published["statements"], {})

    def test_a_site_whose_pages_give_a_statement_ignores_the_committed_file_with_no_hover_table(self):
        site = self.companion.write_site()
        local = {"type": "lean", "uri": "https://git.example.test/notes", "version": self.BUILT, "role": "key",
                 "location": {"declaration": "Notes.Box.volume", "module": "Notes.Box"},
                 "extensions": {NAMESPACE: {"destination": {"resolution": "exact"},
                                            "snippet": {"line": 3, "text": "def volume := 2"}}}}
        self.companion.write_document(objects=[local, self.remote_end("volume", "def volume := 2", 3)])
        self.write_committed()
        page = site / "Notes" / "Box" / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text(
            '<html><head><base href="../../"></head><body><div class="code-box">'
            '<code class="hl lean block"><span class="keyword token">def</span> '
            '<span class="const token" id="Notes___Box___volume">volume</span> := '
            '<span class="literal number token">2</span></code></div></body></html>\n', encoding="utf-8")
        summary = BUILD.build_site(self.companion.overlay)
        self.assertEqual(summary["verso"], {"source": "site", "statements": 1, "targets": 2})
        published = json.loads((site / "pdf-overlay" / "verso.json").read_text(encoding="utf-8"))
        self.assertEqual(list(published["statements"]), ["Notes/Box/index.html#Notes___Box___volume"])

    def test_a_committed_file_that_is_not_one_pages_writes_is_refused(self):
        self.companion.write_site()
        self.companion.write_document(objects=[self.remote_end("volume", "def volume := 2", 3)])
        linked = {"schema": "formal-companion/overlay-verso", "schema_version": 1, "hovers": {},
                  "statements": {f"{self.REMOTE}#L3": [[["kw", "def"], ["tx", " "],
                                                         ["const", "volume", None, None, "Notes/Box/index.html#v"]]]}}
        for value, words in ((linked, "carries a link"), ({**linked, "schema_version": 2}, "expected schema"),
                             ({"schema": "x"}, "expected schema")):
            with self.subTest(words=words):
                self.write_committed(value)
                with self.assertRaisesRegex(BUILD.BuildError, f"pdf-overlay/verso.json: .*{words}"):
                    BUILD.build_site(self.companion.overlay)
                self.assertFalse((self.companion.site / "pdf-overlay").exists())
        (self.companion.overlay / "verso.json").write_bytes(b'{"schema": 1, "schema": 2}')
        with self.assertRaisesRegex(BUILD.BuildError, "duplicate JSON key"):
            BUILD.build_site(self.companion.overlay)
        real = self.base / "verso.json"
        (self.companion.overlay / "verso.json").rename(real)
        (self.companion.overlay / "verso.json").symlink_to(real)
        with self.assertRaisesRegex(BUILD.BuildError, "nonsymlink"):
            BUILD.build_site(self.companion.overlay)

    def test_it_runs_no_build_tool_of_its_own(self):
        source = (TOOLS / "build_site.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("shell=True", source)


# ---------------------------------------------------------------------------
# the loopback server
# ---------------------------------------------------------------------------

class TestTheLoopbackServer(OverlayToolsTestCase):
    """Section 5: the site, the PDF at one route, no traversal, no listings."""

    def serving(self, pdf_bytes: bytes = FIXTURE_PDF):
        # the handler's access log is for an operator watching one request at a
        # time; here it would interleave with the test runner's own output.
        silence = mock.patch.object(SERVE.OverlayRequestHandler, "log_message",
                                    lambda *arguments: None)
        silence.start()
        self.addCleanup(silence.stop)
        config = self.companion.config()
        site = self.companion.write_site()
        (site / "page").mkdir(exist_ok=True)
        (site / "page" / "deep.html").write_text("<p>deep</p>", encoding="utf-8")
        self.companion.write_cache(pdf_bytes)
        pdf = SERVE.open_validated_pdf(self.companion.cache_path, self.companion.record())
        server = SERVE.reserve_server(site, config, 0)
        server.pdf = pdf
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.01})
        thread.start()

        def stop():
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            pdf.close()

        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_address[1]}", config

    def test_the_pdf_is_served_at_its_route_and_nowhere_else(self):
        base, config = self.serving()
        with urllib.request.urlopen(base + config.route, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "application/pdf")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.read(), FIXTURE_PDF)
        head = urllib.request.Request(base + config.route, method="HEAD")
        with urllib.request.urlopen(head, timeout=10) as response:
            self.assertEqual(int(response.headers["Content-Length"]), len(FIXTURE_PDF))
            self.assertEqual(response.read(), b"")
        for path in (config.route + "?x=1", config.route + ".near",
                     "/__overlay_pdf__/other.pdf", "/__other__/notes.pdf"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as bad:
                urllib.request.urlopen(base + path, timeout=10)
            self.assertEqual(bad.exception.code, 404)

    def test_the_site_is_served_but_a_traversal_is_not(self):
        base, _ = self.serving()
        with urllib.request.urlopen(base + "/", timeout=10) as response:
            self.assertIn(b"built site", response.read())
        with urllib.request.urlopen(base + "/page/deep.html", timeout=10) as response:
            self.assertIn(b"deep", response.read())
        for path in ("/../companion/pdf-overlay/overlay.config.json",
                     "/page/../../../outside.txt",
                     "/%2e%2e/%2e%2e/outside.txt"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as bad:
                urllib.request.urlopen(base + path, timeout=10)
            self.assertIn(bad.exception.code, (400, 404))

    def test_a_directory_listing_is_never_rendered(self):
        base, _ = self.serving()
        with self.assertRaises(urllib.error.HTTPError) as bad:
            urllib.request.urlopen(base + "/page/", timeout=10)
        self.assertEqual(bad.exception.code, 404)

    def test_a_pdf_left_in_the_site_is_neither_served_nor_accepted(self):
        base, _ = self.serving()
        leak = self.companion.site / "leak.pdf"
        leak.write_bytes(FIXTURE_PDF)
        with self.assertRaises(urllib.error.HTTPError) as bad:
            urllib.request.urlopen(base + "/leak.pdf", timeout=10)
        self.assertEqual(bad.exception.code, 404)
        with self.assertRaisesRegex(SERVE.OverlayError, "contains a PDF"):
            SERVE.assert_site_servable(self.companion.site)
        leak.unlink()
        SERVE.assert_site_servable(self.companion.site)
        (self.companion.site / "escape").symlink_to(self.base)
        with self.assertRaisesRegex(SERVE.OverlayError, "symlink"):
            SERVE.assert_site_servable(self.companion.site)

    def test_a_published_pdf_is_served_beside_the_config_and_the_mount_still_is(self):
        self.companion.publish()
        base, config = self.serving()
        mount = self.companion.site / "pdf-overlay"
        (mount / "viewer").mkdir(parents=True)
        (mount / "viewer" / "index.html").write_text("<p>viewer</p>", encoding="utf-8")
        (mount / "notes.pdf").write_bytes(FIXTURE_PDF)
        with urllib.request.urlopen(base + "/pdf-overlay/notes.pdf", timeout=10) as response:
            self.assertEqual(response.headers.get_content_type(), "application/pdf")
            self.assertEqual(response.read(), FIXTURE_PDF)
        with urllib.request.urlopen(base + "/pdf-overlay/viewer/index.html", timeout=10) as response:
            self.assertIn(b"viewer", response.read())
        for path in ("/__overlay_pdf__/notes.pdf", "/pdf-overlay/notes.pdf?x=1", "/pdf-overlay/other.pdf"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as bad:
                urllib.request.urlopen(base + path, timeout=10)
            self.assertEqual(bad.exception.code, 404)
            bad.exception.close()
        SERVE.assert_site_servable(self.companion.site, config.published_path)
        with self.assertRaisesRegex(SERVE.OverlayError, "contains a PDF"):
            SERVE.assert_site_servable(self.companion.site)
        (self.companion.site / "leak.pdf").write_bytes(FIXTURE_PDF)
        with self.assertRaisesRegex(SERVE.OverlayError, "contains a PDF.*leak\\.pdf"):
            SERVE.assert_site_servable(self.companion.site, config.published_path)

    def test_a_busy_port_is_reported_and_nothing_else_runs(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        self.addCleanup(listener.close)
        self.companion.write_site()
        with self.assertRaisesRegex(SERVE.OverlayError, "already in use"):
            SERVE.reserve_server(self.companion.site, self.companion.config(), port)


# ---------------------------------------------------------------------------
# the two tools as a companion runs them
# ---------------------------------------------------------------------------

class TestTheToolsFromTheirOwnPath(OverlayToolsTestCase):
    """Section 5: run from the companion root, located from the script."""

    def test_build_only_acquires_nothing_builds_and_stops_before_serving(self):
        self.companion.write_cache()
        self.companion.fake_build_tool()
        result = self.companion.run_tool("serve", "--build-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cache=reused", result.stdout)
        self.assertIn("PASS overlay build complete", result.stdout)
        self.assertEqual(self.companion.log.read_text(encoding="utf-8"), "ran build\n")
        index = (self.companion.site / "index.html").read_text(encoding="utf-8")
        self.assertEqual(index.count(BUILD.LINK_START), 1)
        self.assertTrue((self.companion.site / "pdf-overlay" / "receipt.json").is_file())
        self.assertEqual(list(self.companion.site.rglob("*.pdf")), [])

    def test_build_only_publishes_an_openly_licensed_pdf_with_the_site(self):
        self.companion.publish("CC-BY-4.0")
        self.companion.write_cache()
        self.companion.fake_build_tool()
        result = self.companion.run_tool("serve", "--build-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS overlay build complete; the PDF is published at pdf-overlay/notes.pdf "
                      "under CC-BY-4.0", result.stdout)
        self.assertEqual((self.companion.site / "pdf-overlay" / "notes.pdf").read_bytes(), FIXTURE_PDF)
        self.assertEqual(list(self.companion.site.rglob("*.pdf")),
                         [self.companion.site / "pdf-overlay" / "notes.pdf"])

    def test_a_tool_run_from_another_directory_still_finds_its_overlay(self):
        self.companion.write_cache()
        self.companion.write_site()
        result = subprocess.run(
            [sys.executable, "-B",
             str(self.companion.overlay / "tools" / "build_site.py"), "--print-receipt"],
            cwd=self.base, check=False, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(BUILD.RECEIPT_SCHEMA, result.stdout)
        self.assertIn('"mount": "pdf-overlay"', result.stdout)

    def test_a_bad_config_stops_with_one_line_and_exit_1(self):
        self.companion.write_config(nickname="x")
        for tool in ("serve", "build_site"):
            with self.subTest(tool=tool):
                result = self.companion.run_tool(tool)
                self.assertEqual(result.returncode, 1)
                lines = [line for line in result.stderr.splitlines() if line.strip()]
                self.assertEqual(len(lines), 1, result.stderr)
                self.assertTrue(lines[0].startswith("STOP "), lines[0])
                self.assertIn("nickname", lines[0])

    def test_both_tools_answer_help_with_no_configuration_at_all(self):
        for tool, flag in (("serve", "--refresh-pdf"), ("build_site", "--print-receipt")):
            with self.subTest(tool=tool):
                result = self.companion.run_tool(tool, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(flag, result.stdout)
        self.assertNotIn('add_argument("--host"',
                         (TOOLS / "serve.py").read_text(encoding="utf-8"))

    def test_both_tools_refuse_windows_with_one_line(self):
        with mock.patch.object(os, "name", "nt"):
            with self.assertRaisesRegex(SERVE.OverlayError, "POSIX") as caught:
                SERVE.refuse_windows()
            self.assertEqual(str(caught.exception).count("\n"), 0)
            with self.assertRaisesRegex(BUILD.BuildError, "POSIX") as caught:
                BUILD.refuse_windows()
            self.assertEqual(str(caught.exception).count("\n"), 0)
            with self.assertRaisesRegex(BUILD.BuildError, "POSIX"):
                BUILD.build_site(self.companion.overlay)


class TestNothingInTheToolsNamesOneCompanion(OverlayToolsTestCase):
    """Section 1 rule 1: no book, edition, hash, URL or namespace is in here."""

    def sources(self) -> dict[str, str]:
        return {name: (TOOLS / f"{name}.py").read_text(encoding="utf-8")
                for name in ("serve", "build_site", "overlay_annotations", "verso_extract")}

    def test_no_tool_carries_a_hash_a_url_or_a_byte_count_of_its_own(self):
        for name, source in self.sources().items():
            with self.subTest(tool=name):
                self.assertEqual(re.findall(r"\b[0-9a-f]{40,}\b", source), [])
                # a literal host, not the loopback template the server prints
                self.assertEqual(re.findall(r"https?://[A-Za-z0-9][\w.-]*", source), [])
                self.assertEqual(re.findall(r"\b\d[\d_]{5,}\b", source), [])

    def test_no_tool_names_a_toolchain_a_manifest_or_a_book(self):
        for name, source in self.sources().items():
            with self.subTest(tool=name):
                for token in ("lean-toolchain", "lake-manifest", "toolchain_sha256",
                              "overlay-manifest", "site-manifest", "measure",
                              "chapter", "Chapter"):
                    if (name, token) == ("verso_extract", "lake-manifest"):
                        # it reads a build's own manifest to record its Verso release, and pins none
                        record = source.partition("def build_record(")[2].partition("\ndef ")[0]
                        self.assertEqual(source.count(token), record.count(token), "only in build_record")
                        continue
                    self.assertNotIn(token, source)

    def test_the_per_companion_values_are_read_not_written(self):
        for name in ("serve", "build_site"):
            with self.subTest(tool=name):
                self.assertIn("overlay.config.json", self.sources()[name])
                self.assertIn("overlay_annotations.", self.sources()[name])
        self.assertIn("GENERATED.json", self.sources()["overlay_annotations"])


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main(verbosity=2)
