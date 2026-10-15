"""`overlay.config.json` has three readers in this repo, and they must agree.

Section 3 of `docs/overlay.md` is one rule set written down three times: in
`scripts/overlay/config.py`, which `cf overlay` uses; in
`templates/overlay/runtime/schema/overlay-config-v1.schema.json`, which a
companion hands an offline validator; and in
`templates/overlay/runtime/tools/check_generated.py`, which a companion's CI
runs with the standard library alone and so cannot import either of the other
two.  Three copies of a rule set drift, and the drift is invisible until a
companion's CI and `cf overlay check` disagree about the same file.

So this module holds them in step two ways: the four pattern strings must be
the same string in all three, and a table of configurations -- the shipped
example, the minimum, and one deliberate breach per rule -- must be accepted or
refused by all three alike.  `templates/overlay/runtime/tools/serve.py` is a
fourth reader, outside the three-way table because it is deliberately laxer in
two documented places; what is checked of it is the direction: nothing
`config.py` accepts may be a config `serve.py` refuses.  `build_site.py` reads
the same file before it publishes and is held to the same direction.

The last class holds the strings the annotation readers retype: the overlay's
extension key, the annotation file layout and the bundle's schema.

Everything here is synthetic: a source id, a page title and a file name that
belong to no edition and no companion.
"""
from __future__ import annotations

# Pin HOME before anything builds a workspace: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import importlib.util
import json
import os
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
from cflib.errors import CfError                                      # noqa: E402
from formal_companion_annotations.schemacheck import Validator        # noqa: E402
from overlay import config as overlay_config                          # noqa: E402
from overlay import sync as overlay_sync                              # noqa: E402

RUNTIME = SKILL / "templates" / "overlay" / "runtime"
SCHEMA_PATH = RUNTIME / "schema" / "overlay-config-v1.schema.json"
EXAMPLE_PATH = SKILL / "templates" / "overlay" / "overlay.config.json.example"


def load_tool(name: str):
    """Import a runtime tool by path, the way a companion runs it."""
    path = RUNTIME / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"overlay_{name}", path)
    if spec is None or spec.loader is None:                  # pragma: no cover
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK_GENERATED = load_tool("check_generated")
SERVE = load_tool("serve")
BUILD_SITE = load_tool("build_site")
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Validator(SCHEMA)


def minimal() -> dict:
    """The smallest configuration section 3 accepts."""
    return {
        "schema": "formal-companion/overlay-config",
        "schema_version": 1,
        "source_id": "notes-pdf",
        "title": "Elementary Sets: Lecture Notes (synthetic)",
        "pdf": {"file_name": "notes.pdf"},
        "site": {},
    }


def without(key: str) -> dict:
    value = minimal()
    value.pop(key)
    return value


def with_(path: str, replacement) -> dict:
    """``minimal()`` with one dotted path set (a two-level path at most)."""
    value = minimal()
    head, _, tail = path.partition(".")
    if tail:
        value.setdefault(head, {})[tail] = replacement
    else:
        value[head] = replacement
    return value


#: (name, config, is it valid).  One entry per rule of section 3, in both
#: directions where the boundary matters.
TABLE: tuple[tuple[str, dict, bool], ...] = (
    ("the shipped example", json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")), True),
    ("the minimum", minimal(), True),
    ("every optional key", {
        **minimal(),
        "lede": "One sentence under the heading.",
        "back_link": {"href": "../../index.html", "text": "Back to the site"},
        "display": {"strip_prefix": "Notes.", "minimal": True},
        "storage_key": "an-overlay-page-key",
        "pdf": {"file_name": "notes.pdf", "cache_dir": ".build/overlay-pdf",
                "route": "/__overlay_pdf__/notes.pdf"},
        "site": {"output_root": ".build/site", "index": "index.html",
                 "mount": "pdf-overlay", "link_text": "Source overlay"},
        "build": {"commands": [["make", "site"]]},
        "serve": {"host": "127.0.0.1", "port": 8111},
    }, True),
    ("an empty strip_prefix", with_("display.strip_prefix", ""), True),
    ("a minimal overlay", with_("display.minimal", True), True),
    ("minimal explicitly off", with_("display.minimal", False), True),
    ("no build commands at all", with_("build.commands", []), True),
    ("the IPv6 loopback", with_("serve.host", "::1"), True),
    ("another 127 address", with_("serve.host", "127.9.9.9"), True),
    ("port 1", with_("serve.port", 1), True),
    ("port 65535", with_("serve.port", 65535), True),
    ("a dotted cache dir", with_("pdf.cache_dir", ".lake/overlay-pdf"), True),
    ("a one-segment output root", with_("site.output_root", "build"), True),

    ("not an object", [], False),
    ("no schema", without("schema"), False),
    ("the wrong schema", with_("schema", "formal-companion/annotations"), False),
    ("no schema_version", without("schema_version"), False),
    ("schema_version 2", with_("schema_version", 2), False),
    ("schema_version as a string", with_("schema_version", "1"), False),
    ("schema_version true", with_("schema_version", True), False),
    ("no source_id", without("source_id"), False),
    ("an empty source_id", with_("source_id", ""), False),
    ("a source_id that is not a string", with_("source_id", 7), False),
    ("no title", without("title"), False),
    ("an empty title", with_("title", ""), False),
    ("no pdf", without("pdf"), False),
    ("no site", without("site"), False),
    ("an unknown top-level key", {**minimal(), "theme": "dark"}, False),
    ("an unknown pdf key", with_("pdf", {"file_name": "notes.pdf", "colour": "red"}), False),
    ("an unknown site key", with_("site", {"root": "build"}), False),
    ("an unknown serve key", with_("serve", {"host": "127.0.0.1", "tls": True}), False),
    ("an unknown build key", with_("build", {"env": {}}), False),
    ("an unknown display key", with_("display", {"hide": "Notes."}), False),
    ("minimal as a string", with_("display.minimal", "yes"), False),
    ("minimal as a number", with_("display.minimal", 1), False),
    ("a back_link with no text", with_("back_link", {"href": "../../index.html"}), False),
    ("a back_link with an extra key", with_(
        "back_link", {"href": "../../index.html", "text": "Back", "rel": "up"}), False),
    ("an empty lede", with_("lede", ""), False),
    ("an empty storage_key", with_("storage_key", ""), False),
    ("no pdf.file_name", with_("pdf", {"cache_dir": ".lake/overlay-pdf"}), False),
    ("a file name with a directory", with_("pdf.file_name", "pdfs/notes.pdf"), False),
    ("a file name with a leading dot", with_("pdf.file_name", ".notes.pdf"), False),
    ("a file name with a space", with_("pdf.file_name", "my notes.pdf"), False),
    ("a route that does not start with /__", with_("pdf.route", "/pdf/notes.pdf"), False),
    ("a route with ..", with_("pdf.route", "/__overlay_pdf__/../notes.pdf"), False),
    ("a route that ends in a slash", with_("pdf.route", "/__overlay_pdf__/"), False),
    ("an absolute cache dir", with_("pdf.cache_dir", "/var/tmp/overlay"), False),
    ("a cache dir that climbs out", with_("pdf.cache_dir", "../overlay-pdf"), False),
    ("a cache dir with a dot segment", with_("pdf.cache_dir", "build/./overlay"), False),
    ("a cache dir with an empty segment", with_("pdf.cache_dir", "build//overlay"), False),
    ("a cache dir with a trailing slash", with_("pdf.cache_dir", "build/overlay/"), False),
    ("a cache dir with a backslash", with_("pdf.cache_dir", "build\\overlay"), False),
    ("an empty cache dir", with_("pdf.cache_dir", ""), False),
    ("an absolute output root", with_("site.output_root", "/srv/site"), False),
    ("an index that climbs out", with_("site.index", "../index.html"), False),
    ("an empty link text", with_("site.link_text", ""), False),
    ("0.0.0.0", with_("serve.host", "0.0.0.0"), False),
    ("a routable address", with_("serve.host", "192.0.2.10"), False),
    ("localhost by name", with_("serve.host", "localhost"), False),
    ("port 0", with_("serve.port", 0), False),
    ("port 65536", with_("serve.port", 65536), False),
    ("a port as a string", with_("serve.port", "8000"), False),
    ("a port that is true", with_("serve.port", True), False),
    ("build commands that are not arrays", with_("build.commands", ["make site"]), False),
    ("an empty argv", with_("build.commands", [[]]), False),
    ("an argv word that is not a string", with_("build.commands", [["make", 1]]), False),
)


class TestTheThreeReadersCarryTheSamePatterns(unittest.TestCase):
    """A pattern retyped in three files is a pattern that drifts in one."""

    def test_the_schema_repeats_config_pys_four_patterns(self):
        relative = SCHEMA["$defs"]["relative_path"]
        pdf = SCHEMA["properties"]["pdf"]["properties"]
        serve = SCHEMA["properties"]["serve"]["properties"]
        self.assertEqual(pdf["file_name"]["pattern"], overlay_config.FILE_NAME_PATTERN)
        self.assertEqual(pdf["route"]["pattern"], overlay_config.ROUTE_PATTERN)
        self.assertEqual(relative["pattern"], overlay_config.RELATIVE_PATH_PATTERN)
        self.assertEqual(tuple(entry["pattern"] for entry in relative["not"]["anyOf"]),
                         overlay_config.RELATIVE_PATH_REFUSED)
        self.assertEqual(serve["host"]["pattern"], overlay_config.LOOPBACK_PATTERN)

    def test_check_generated_repeats_them_too(self):
        for name in ("FILE_NAME_PATTERN", "ROUTE_PATTERN", "RELATIVE_PATH_PATTERN",
                     "RELATIVE_PATH_REFUSED", "LOOPBACK_PATTERN"):
            with self.subTest(pattern=name):
                self.assertEqual(getattr(CHECK_GENERATED, name),
                                 getattr(overlay_config, name))

    def test_the_three_carry_the_same_key_sets(self):
        for name in ("CONFIG_KEYS", "REQUIRED_CONFIG_KEYS", "PDF_KEYS", "REQUIRED_PDF_KEYS",
                     "SITE_KEYS", "BUILD_KEYS", "SERVE_KEYS", "BACK_LINK_KEYS",
                     "DISPLAY_KEYS"):
            with self.subTest(keys=name):
                self.assertEqual(set(getattr(CHECK_GENERATED, name)),
                                 set(getattr(overlay_config, name)))

    def test_the_schema_carries_the_same_key_sets(self):
        self.assertEqual(set(SCHEMA["properties"]), set(overlay_config.CONFIG_KEYS))
        self.assertEqual(set(SCHEMA["required"]), set(overlay_config.REQUIRED_CONFIG_KEYS))
        for key, names in (("pdf", overlay_config.PDF_KEYS),
                           ("site", overlay_config.SITE_KEYS),
                           ("build", overlay_config.BUILD_KEYS),
                           ("serve", overlay_config.SERVE_KEYS),
                           ("back_link", overlay_config.BACK_LINK_KEYS),
                           ("display", overlay_config.DISPLAY_KEYS)):
            with self.subTest(block=key):
                self.assertEqual(set(SCHEMA["properties"][key]["properties"]), set(names))
                self.assertIs(SCHEMA["properties"][key]["additionalProperties"], False)

    def test_the_schema_uses_only_keywords_a_stdlib_validator_implements(self):
        """Constructing the Validator is the check: it refuses an unknown keyword."""
        self.assertTrue(VALIDATOR.is_valid(minimal()))


class TestTheThreeAgreeOnEveryConfigInTheTable(unittest.TestCase):
    """The table is the contract; three implementations answer it the same way."""

    def test_config_py_agrees_with_the_table(self):
        for name, value, valid in TABLE:
            with self.subTest(config=name):
                found = overlay_config.problems(copy.deepcopy(value))
                self.assertEqual(not found, valid, f"{name}: {found}")

    def test_check_generated_agrees_with_the_table(self):
        for name, value, valid in TABLE:
            with self.subTest(config=name):
                found = CHECK_GENERATED.config_problems(copy.deepcopy(value))
                self.assertEqual(not found, valid, f"{name}: {found}")

    def test_the_schema_agrees_with_the_table(self):
        for name, value, valid in TABLE:
            with self.subTest(config=name):
                errors = VALIDATOR.errors(copy.deepcopy(value))
                self.assertEqual(not errors, valid, f"{name}: {errors}")

    def test_the_table_measures_both_directions(self):
        """A table of refusals only would pass against a reader that refuses all."""
        self.assertGreaterEqual(sum(1 for _n, _v, ok in TABLE if ok), 8)
        self.assertGreaterEqual(sum(1 for _n, _v, ok in TABLE if not ok), 20)

    def test_serve_py_accepts_everything_config_py_accepts(self):
        """The direction that matters.

        `config.py` is deliberately stricter than `serve.py` in two places, so
        the two are not equal; what must hold is that `cf overlay check` never
        passes a config the tool a reader actually runs would then refuse.
        """
        root = Path(tempfile.mkdtemp(prefix="cf-overlay-config-",
                                     dir=os.environ.get("CF_TEST_TMP") or None))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for name, value, valid in TABLE:
            if not valid:
                continue
            with self.subTest(config=name):
                (root / "overlay.config.json").write_text(
                    json.dumps(value), encoding="utf-8")
                SERVE.load_config(root)          # raises OverlayError on a refusal

    def test_build_site_py_accepts_everything_config_py_accepts(self):
        """`build_site.py` reads the same file before it publishes, so the same holds."""
        root = Path(tempfile.mkdtemp(prefix="cf-overlay-config-",
                                     dir=os.environ.get("CF_TEST_TMP") or None))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for name, value, valid in TABLE:
            if not valid:
                continue
            with self.subTest(config=name):
                (root / "overlay.config.json").write_text(
                    json.dumps(value), encoding="utf-8")
                BUILD_SITE.load_config(root)     # raises on a refusal


class TestTheDefaultsAreTheOnesSectionThreeStates(unittest.TestCase):
    def test_the_minimum_resolves_to_the_documented_defaults(self):
        config = overlay_config.parse(minimal())
        self.assertEqual(config.cache_dir, overlay_config.DEFAULT_CACHE_DIR)
        self.assertEqual(config.route, "/__overlay_pdf__/notes.pdf")
        self.assertEqual(config.output_root, overlay_config.DEFAULT_OUTPUT_ROOT)
        self.assertEqual(config.index, overlay_config.DEFAULT_INDEX)
        self.assertEqual(config.mount, overlay_config.DEFAULT_MOUNT)
        self.assertEqual(config.host, overlay_config.DEFAULT_HOST)
        self.assertEqual(config.port, overlay_config.DEFAULT_PORT)
        self.assertEqual(config.storage_key, overlay_config.DEFAULT_STORAGE_KEY)
        self.assertEqual((config.back_link_href, config.back_link_text), ("", ""),
                         "no back_link is no back link, not a default one")

    def test_minimal_is_off_unless_the_config_says_so(self):
        self.assertIs(overlay_config.parse(minimal()).minimal, False)
        self.assertIs(overlay_config.parse(with_("display.minimal", True)).minimal, True)

    def test_the_link_text_defaults_to_the_title(self):
        config = overlay_config.parse(minimal())
        self.assertEqual(config.link_text, config.title)

    def test_the_build_commands_default_to_nothing(self):
        """A generic overlay knows no build tool; the example's `lake` lines are
        an example a Lean companion edits, not a default."""
        self.assertEqual(overlay_config.parse(minimal()).build_commands, ())

    def test_the_defaults_match_the_viewers_own(self):
        viewer = (RUNTIME / "viewer" / "viewer-core.js").read_text(encoding="utf-8")
        self.assertIn(f'DEFAULT_STORAGE_KEY = "{overlay_config.DEFAULT_STORAGE_KEY}"', viewer)
        self.assertNotIn("DEFAULT_BACK_LINK", viewer)
        self.assertIn(f'CONFIG_SCHEMA = "{overlay_config.CONFIG_SCHEMA}"', viewer)


class TestTheReadersAgreeOnTheAnnotations(unittest.TestCase):
    """The extension key, the file layout and the bundle schema, written once per reader.

    The producer (`scripts/overlay/shards.py` and `sync.py`), the runtime
    tools (`tools/overlay_annotations.py`) and the viewer each spell them,
    because none of them may import another; a key that drifts is a viewer
    that finds no item, region or card in any record.
    """

    def sources(self) -> tuple[str, str]:
        return ((RUNTIME / "tools" / "overlay_annotations.py").read_text(encoding="utf-8"),
                (RUNTIME / "viewer" / "viewer-core.js").read_text(encoding="utf-8"))

    def test_the_namespace_and_the_bundle_schema_are_one_string_each(self):
        from overlay import shards as overlay_shards
        tools, viewer = self.sources()
        for reader in (tools, viewer):
            self.assertIn(f'NAMESPACE = "{overlay_shards.NAMESPACE}"', reader)
            self.assertIn(f'BUNDLE_SCHEMA = "{overlay_sync.BUNDLE_SCHEMA}"', reader)

    def test_the_section_and_file_patterns_are_one_pattern_each(self):
        from overlay import shards as overlay_shards
        tools, viewer = self.sources()
        for pattern in (overlay_shards.SECTION_DIR.pattern, overlay_shards.ITEM_DIR.pattern,
                        overlay_shards.ANNOTATION_FILE.pattern):
            self.assertIn(pattern, tools)
            self.assertIn("/^" + pattern.removesuffix("\\Z") + "$/", viewer)

    def test_the_tools_check_files_against_the_form_the_export_writes(self):
        from overlay import defaults as overlay_defaults
        tools = load_tool("overlay_annotations")
        self.assertEqual((tools.TOP, tools.DEFAULTS), (overlay_defaults.TOP, overlay_defaults.DEFAULTS))
        value = {"extensions": {"x": {"b": [1, "é"], "type": "t", "a": [{"type": "q", "z": None}, []]}}, "zz": {},
                 "location": {"rect": [0.24, 0.47, 0.5, 0.16], "page": 1}, "id": "i", "$schema": "s", "kind": "k"}
        self.assertEqual(tools.file_bytes(value), overlay_defaults.dumps(value))

    def test_build_site_publishes_what_the_viewer_reads(self):
        self.assertEqual(set(BUILD_SITE.INSTANCE_FILES),
                         {overlay_config.CONFIG_NAME, overlay_sync.GENERATED_NAME})


class TestProblemsAreReportedWhole(unittest.TestCase):
    def test_every_problem_is_listed_not_only_the_first(self):
        broken = {**minimal(), "title": "", "serve": {"port": 0}}
        found = overlay_config.problems(broken)
        self.assertGreaterEqual(len(found), 2, found)
        self.assertTrue(any("title" in line for line in found), found)
        self.assertTrue(any("serve.port" in line for line in found), found)

    def test_parse_raises_a_cferror_naming_the_first_problem(self):
        with self.assertRaises(CfError) as caught:
            overlay_config.parse(without("title"))
        self.assertIn("title", caught.exception.message)

    def test_a_problem_names_the_path_it_is_at(self):
        found = overlay_config.problems(with_("pdf.route", "/nope"))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("overlay.config.json.pdf.route:"), found[0])


class TestReadingTheFile(unittest.TestCase):
    def test_strict_json_refuses_a_duplicate_key(self):
        raw = b'{"schema": "a", "schema": "b"}'
        with self.assertRaises(CfError):
            overlay_config.strict_json(raw, "overlay.config.json")

    def test_strict_json_refuses_nan(self):
        with self.assertRaises(CfError):
            overlay_config.strict_json(b'{"port": NaN}', "overlay.config.json")

    def test_load_problems_reports_a_missing_file_as_one_line(self):
        found = overlay_config.load_problems(SKILL / "no-such-overlay.config.json")
        self.assertEqual(len(found), 1, found)
        self.assertIn("cannot read", found[0])

    def test_the_example_is_what_init_writes_from(self):
        example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(overlay_config.problems(copy.deepcopy(example)), [])
        self.assertEqual(example["schema"], overlay_config.CONFIG_SCHEMA)
        self.assertEqual(example["pdf"]["route"],
                         overlay_config.route_for(example["pdf"]["file_name"]))


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
