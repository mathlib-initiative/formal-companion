"""Folder defaults for the overlay's annotation files (`scripts/overlay/defaults.py`), on synthetic records."""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from literature_links import companion, discovery                    # noqa: E402
from overlay import defaults                                          # noqa: E402

SOURCE = {"type": "pdf", "uri": "https://example.invalid/notes.pdf", "pin": {"type": "book", "title": "Notes"}}
PIN = {"type": "repository", "url": "https://example.invalid/repo", "commit": "1" * 40}


def record(rid: str, uri: str | None = None, **extra) -> dict:
    out = {"id": rid, "kind": "theorem", "source": SOURCE, "location": {"page": 1, "rect": [0.1, 0.2, 0.3, 0.4]},
           "extensions": {"x": {"item": rid.split("/")[0]}}, **extra}
    if uri is not None:
        out["target"] = {"uri": uri, "pin": dict(PIN, path=rid + ".lean")}
    return out


def read(files: dict[str, bytes]) -> dict[str, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        for name, raw in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(raw)
        found = discovery.collect([root])
        assert not found.problems, found.problems
        return {r.path.relative_to(root).as_posix(): r.annotation for r in found.records}


def resolved(uri: str, path: str) -> str:
    return urllib.parse.urljoin("https://site.invalid/a/b/c/annotations/" + path, uri)


RECORDS = {
    "s/one.json": record("one", "../../Site/One.html#one"),
    "s/two/a.json": record("two/a", "../../../Site/Two.html#a", status="formalized"),
    "s/two/b.json": record("two/b", "../../../Site/Two.html#b", status="formalized"),
    "s/two/c.json": record("two/c", None, status="formalized"),
}
FOLDERS = ["", "s/two"]


class TestFactoring(unittest.TestCase):
    def test_common_takes_equal_members_and_recurses_on_a_majority_of_objects(self):
        values = [{"a": 1, "o": {"x": 1, "y": 1}, "s": 1}, {"a": 1, "o": {"x": 1, "y": 2}, "s": 2},
                  {"a": 1, "s": "o"}]
        self.assertEqual(defaults.common(values), {"a": 1, "o": {"x": 1}})
        self.assertEqual(defaults.common([{"o": {"x": 1}}, {"o": 3}, {"o": {"x": 1}}]), {},
                         "a value that is not an object stops the recursion")
        self.assertEqual(defaults.common([{"o": {"x": 1}}, {}, {"p": 1}, {"o": {"x": 1}}]), {},
                         "half is not a strict majority")

    def test_subtract_leaves_out_what_is_inherited(self):
        self.assertEqual(defaults.subtract({"a": 1, "o": {"x": 1, "y": 2}, "b": 2}, {"a": 1, "o": {"x": 1}, "b": 3}),
                         {"o": {"y": 2}, "b": 2})

    def test_patch_is_the_smallest_merge_patch(self):
        inherited = {"a": 1, "o": {"x": 1, "y": 1}, "gone": {"z": 1}}
        written = defaults.patch(inherited, {"a": 1, "o": {"x": 2, "y": 1}, "new": [1]})
        self.assertEqual(written, {"o": {"x": 2}, "gone": None, "new": [1]})
        self.assertEqual(companion.merge(inherited, written), {"a": 1, "o": {"x": 2, "y": 1}, "new": [1]})


class TestTheFiles(unittest.TestCase):
    def setUp(self):
        self.files = defaults.files(RECORDS, FOLDERS)

    def test_the_reader_reads_back_every_record(self):
        back = read(self.files)
        self.assertEqual(set(back), set(RECORDS))
        for path, value in RECORDS.items():
            with self.subTest(path=path):
                self.assertEqual({k: v for k, v in back[path].items() if k != "target"},
                                 {k: v for k, v in value.items() if k != "target"})
                if "target" in value:
                    self.assertEqual(resolved(back[path]["target"]["uri"], path),
                                     resolved(value["target"]["uri"], path))
                else:
                    self.assertNotIn("target", back[path])

    def test_defaults_only_where_asked_and_where_records_share_something(self):
        self.assertEqual([p for p in self.files if p.endswith(defaults.DEFAULTS)], ["_defaults.json",
                                                                                 "s/two/_defaults.json"])
        self.assertEqual([p for p in defaults.files({"s/x/a.json": RECORDS["s/one.json"]}, ["", "s/x"])],
                         ["s/x/a.json"], "no defaults file for a single record")
        top = json.loads(self.files["_defaults.json"])
        self.assertEqual(top["source"], SOURCE)
        self.assertEqual(top["target"], {"pin": PIN})
        self.assertEqual(json.loads(self.files["s/two/_defaults.json"]),
                         {"status": "formalized", "extensions": {"x": {"item": "two"}}})

    def test_a_record_lacking_an_object_most_records_have_writes_null(self):
        self.assertEqual(json.loads(self.files["s/two/c.json"]), {"id": "two/c", "target": None})

    def test_a_reference_shared_by_meaning_is_factored_and_one_shared_by_spelling_is_not(self):
        same_meaning = {"s/a.json": record("a", "../../Site/X.html"), "s/b/c.json": record("b", "../../../Site/X.html"),
                        "s/b/d.json": record("b", "../../../Site/X.html")}
        files = defaults.files(same_meaning, ["", "s/b"])
        self.assertEqual(json.loads(files["_defaults.json"])["target"]["uri"], "../Site/X.html")
        self.assertNotIn("uri", json.loads(files["s/a.json"]).get("target", {}))
        same_spelling = {"s/a.json": record("a", "../../Site/X.html"), "s/b/c.json": record("b", "../../Site/X.html"),
                         "s/b/d.json": record("b", "../../Site/X.html")}
        files = defaults.files(same_spelling, ["", "s/b"])
        self.assertNotIn("uri", json.loads(files["_defaults.json"])["target"])
        self.assertEqual(json.loads(files["s/b/_defaults.json"])["target"]["uri"], "../../Site/X.html")
        self.assertEqual(json.loads(files["s/a.json"])["target"]["uri"], "../../Site/X.html")
        for path, value in read(files).items():
            self.assertEqual(resolved(value["target"]["uri"], path), resolved(same_spelling[path]["target"]["uri"], path))

    def test_any_relative_reference_is_moved_not_only_the_target(self):
        moved = defaults.moved({"source": {"type": "pdf", "uri": "../notes.pdf"}, "target": "#here"}, "s/a.json",
                               "s/t/b.json")
        self.assertEqual(moved, {"source": {"type": "pdf", "uri": "../../notes.pdf"}, "target": "../a.json#here"})

    def test_a_file_that_would_read_back_wrong_is_refused(self):
        with mock.patch.object(defaults, "patch", lambda inherited, value: {"id": value["id"]}):
            with self.assertRaisesRegex(ValueError, "do not read back"):
                defaults.files(RECORDS, FOLDERS)


class TestTheBytes(unittest.TestCase):
    def test_keys_in_order_scalar_arrays_on_one_line_and_strict_utf8_json(self):
        value = {"extensions": {"x": {"b": 1, "type": "t", "a": [[1, 2], [3]]}}, "zz": 1, "aa": "é",
                 "location": {"rect": [0.24, 0.47, 0.5, 0.16], "page": 1, "type": "p"}, "id": "i", "$schema": "s",
                 "target": None, "kind": "k", "e": [], "o": {}}
        raw = defaults.dumps(value)
        self.assertEqual(raw.decode("utf-8"), """{
  "$schema": "s",
  "id": "i",
  "kind": "k",
  "location": {
    "type": "p",
    "page": 1,
    "rect": [0.24, 0.47, 0.5, 0.16]
  },
  "target": null,
  "extensions": {
    "x": {
      "type": "t",
      "a": [
        [1, 2],
        [3]
      ],
      "b": 1
    }
  },
  "aa": "é",
  "e": [],
  "o": {},
  "zz": 1
}
""")
        self.assertEqual(companion.loads(raw.decode("utf-8")), value)


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
