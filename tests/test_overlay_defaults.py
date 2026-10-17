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

PDF = {"type": "pdf", "uri": "https://example.invalid/notes.pdf", "sha256": "0" * 64,
       "citation": {"id": "notes", "type": "book", "title": "Notes"}}
PROJECT = {"type": "lean", "uri": "https://example.invalid/repo", "version": "1" * 40}


def record(rid: str, declaration: str | None = None, *, page: int = 1, project: dict = PROJECT, **extra) -> dict:
    out = {"id": rid.split("/")[0], "kind": "theorem",
           "subject": [dict(PDF, location={"page": page, "rect": [0.1, 0.2, 0.3, 0.4]}, title=rid.split("/")[0])],
           "object": [], "extensions": {"x": {"item": rid.split("/")[0]}}, **extra}
    if declaration is not None:
        out["object"] = [dict(project, location={"declaration": declaration}, role="key")]
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
    "s/one.json": record("one", "One.one"),
    "s/two/a.json": record("two/a", "Two.a", status="formalized"),
    "s/two/b.json": record("two/b", "Two.b", status="formalized"),
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

    def test_an_end_template_is_what_the_ends_of_the_commonest_document_share(self):
        ends = [dict(PDF, location={"page": 1}, title="a"), dict(PDF, location={"page": 1}, title="b"),
                {"type": "pdf", "uri": "other.pdf"}]
        self.assertEqual(defaults.template_for(ends), dict(PDF, location={"page": 1}))
        self.assertIsNone(defaults.template_for(ends[:1] + ends[2:]), "no document holds a strict majority")
        self.assertIsNone(defaults.template_for(ends[:1]), "one end is no template")
        ends = [dict(PDF, location={"page": 1, "rect": [0, 0, 1, 1]}), dict(PDF, location={"page": 1})]
        self.assertNotIn("location", defaults.template_for(ends), "a location is shared only whole")

    def test_an_end_patch_writes_no_uri_and_a_whole_location(self):
        template = dict(PDF, location={"page": 1, "rect": [0, 0, 1, 1]})
        end = dict(PDF, location={"page": 2}, citation=dict(PDF["citation"], title="Other"))
        self.assertEqual(defaults.end_patch(template, end), {"location": {"page": 2}, "citation": {"title": "Other"}})
        self.assertEqual(defaults.end_patch(template, PDF), {"location": None})
        self.assertEqual(companion.merge({"subject": template}, {"subject": [defaults.end_patch(template, end)]}),
                         {"subject": [end]})

    def test_patch_is_the_smallest_merge_patch(self):
        inherited = {"a": 1, "o": {"x": 1, "y": 1}, "gone": {"z": 1}}
        written = defaults.patch(inherited, {"a": 1, "o": {"x": 2, "y": 1}, "new": [1]})
        self.assertEqual(written, {"o": {"x": 2}, "gone": None, "new": [1]})
        self.assertEqual(companion.merge(inherited, written), {"a": 1, "o": {"x": 2, "y": 1}, "new": [1]})


class TestTheFiles(unittest.TestCase):
    def setUp(self):
        self.files = defaults.files(RECORDS, FOLDERS)

    def test_the_reader_reads_back_every_record(self):
        self.assertEqual(read(self.files), RECORDS)

    def test_defaults_and_end_templates_only_where_asked_and_where_records_share_something(self):
        self.assertEqual([p for p in self.files if p.endswith(defaults.DEFAULTS)], ["_defaults.json",
                                                                                 "s/two/_defaults.json"])
        self.assertEqual([p for p in defaults.files({"s/x/a.json": RECORDS["s/one.json"]}, ["", "s/x"])],
                         ["s/x/a.json"], "no defaults file for a single record")
        top = json.loads(self.files["_defaults.json"])
        self.assertEqual(top["subject"], dict(PDF, location={"page": 1, "rect": [0.1, 0.2, 0.3, 0.4]}))
        self.assertEqual(top["object"], dict(PROJECT, role="key"))
        self.assertEqual(json.loads(self.files["s/two/_defaults.json"]),
                         {"id": "two", "status": "formalized", "subject": {"title": "two"},
                          "extensions": {"x": {"item": "two"}}})

    def test_an_end_is_written_as_its_patch_over_the_template_and_a_side_that_is_the_template_left_out(self):
        self.assertEqual(json.loads(self.files["s/one.json"]),
                         {"id": "one", "subject": [{"title": "one"}],
                          "object": [{"location": {"declaration": "One.one"}}], "extensions": {"x": {"item": "one"}}})
        self.assertEqual(json.loads(self.files["s/two/c.json"]), {"object": []})

    def test_an_end_that_would_cancel_a_template_member_is_written_whole(self):
        records = {f"s/{n}.json": record(f"r{n}", f"D.{n}", project=dict(PROJECT, extensions={"x": {"k": 1}}))
                   for n in range(3)}
        records["s/3.json"] = record("r3", "D.3")
        files = defaults.files(records, [""])
        self.assertEqual(json.loads(files["_defaults.json"])["object"]["extensions"], {"x": {"k": 1}})
        self.assertEqual(json.loads(files["s/3.json"])["object"], records["s/3.json"]["object"],
                         "an end that would write extensions: null names its document instead")
        self.assertEqual(read(files), records)

    def test_a_record_lacking_a_member_most_records_have_writes_null(self):
        records = {f"s/{n}.json": record(f"r{n}", f"D.{n}", extensions={"x": {"a": {"k": 1}, "b": n}})
                   for n in range(3)}
        records["s/3.json"] = record("r3", "D.3", extensions={"x": {"b": 3}})
        files = defaults.files(records, [""])
        self.assertEqual(json.loads(files["_defaults.json"])["extensions"], {"x": {"a": {"k": 1}}})
        self.assertEqual(json.loads(files["s/3.json"])["extensions"], {"x": {"a": None, "b": 3}})
        self.assertEqual(read(files), records)

    def test_a_relative_reference_is_shared_by_meaning(self):
        local = {"type": "lean", "uri": "../../", "version": "1" * 40}
        records = {"s/a.json": record("a", "A.a", project=local),
                   "s/b/c.json": record("b", "B.c", project=dict(local, uri="../../../")),
                   "s/b/d.json": record("b", "B.d", project=dict(local, uri="../../../"))}
        files = defaults.files(records, ["", "s/b"])
        self.assertEqual(json.loads(files["_defaults.json"])["object"]["uri"], "../")
        self.assertNotIn("uri", json.loads(files["s/a.json"])["object"][0])
        for path, value in read(files).items():
            self.assertEqual(resolved(value["object"][0]["uri"], path), resolved(records[path]["object"][0]["uri"], path))
        same_spelling = {"s/a.json": record("a", "A.a", project=local),
                         "s/b/c.json": record("b", "B.c", project=local), "s/b/d.json": record("b", "B.d", project=local)}
        files = defaults.files(same_spelling, ["", "s/b"])
        self.assertEqual(json.loads(files["s/a.json"])["object"][0]["uri"], "../../",
                         "the two in s/b/ are another folder, the template's; this end names its own")
        for path, value in read(files).items():
            self.assertEqual(resolved(value["object"][0]["uri"], path),
                             resolved(same_spelling[path]["object"][0]["uri"], path))

    def test_any_relative_reference_is_moved(self):
        moved = defaults.moved({"subject": [{"type": "pdf", "uri": "../notes.pdf"}],
                                "object": [{"type": "lean", "uri": "../../", "location": {"profile": "#here"}}]},
                               "s/a.json", "s/t/b.json")
        self.assertEqual(moved, {"subject": [{"type": "pdf", "uri": "../../notes.pdf"}],
                                 "object": [{"type": "lean", "uri": "../../../", "location": {"profile": "../a.json#here"}}]})

    def test_a_file_that_would_read_back_wrong_is_refused(self):
        real = defaults.dumps
        with mock.patch.object(defaults, "dumps", lambda value: real({**value, "note": "not the record"})):
            with self.assertRaisesRegex(ValueError, "do not read back.*differs"):
                defaults.files(RECORDS, FOLDERS)


class TestTheBytes(unittest.TestCase):
    def test_keys_in_order_scalar_arrays_on_one_line_and_strict_utf8_json(self):
        value = {"extensions": {"x": {"b": 1, "type": "t", "a": [[1, 2], [3]]}}, "zz": 1, "aa": "é",
                 "subject": [{"title": "T", "location": {"rect": [0.24, 0.47, 0.5, 0.16], "page": 1}, "type": "p",
                              "citation": {"title": "C", "type": "book"}}],
                 "id": "i", "$schema": "s", "relation": "noMatch", "object": None, "kind": "k", "e": [], "o": {}}
        raw = defaults.dumps(value)
        self.assertEqual(raw.decode("utf-8"), """{
  "$schema": "s",
  "id": "i",
  "kind": "k",
  "relation": "noMatch",
  "subject": [
    {
      "type": "p",
      "location": {
        "page": 1,
        "rect": [0.24, 0.47, 0.5, 0.16]
      },
      "title": "T",
      "citation": {
        "type": "book",
        "title": "C"
      }
    }
  ],
  "object": null,
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
