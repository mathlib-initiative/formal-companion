"""The annotation document → Literature Links annotation files mapping (`scripts/overlay/shards.py`).

docs/overlay.md section 7, record by record, on a synthetic document: what a
link, a region, a quad region, an item that links nothing, a destination, a
verdict and the source become, which section folder, item folder and file each
record goes to, how a file is named, and that every file passes the vendored
`literature_links` scan.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import collections
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from literature_links import companion, discovery                    # noqa: E402
from overlay import shards                                           # noqa: E402

NS = shards.NAMESPACE
SHA = "c0ffee00" * 8
SPACE = {"origin": "top_left", "page_box": "CropBox", "units": "normalized", "x_axis": "right", "y_axis": "down"}


def selector(page, **geometry):
    return {"type": "PdfRegionSelector", "page": page, "coordinate_space": dict(SPACE), **geometry}


def location(lid, items, sel):
    return {"id": lid, "items": items, "target": {"source": "notes-pdf", "selector": [
        {"type": "FragmentSelector", "value": f"page={sel['page']}", "refinedBy": sel},
        {"type": "FragmentSelector", "value": f"page={sel['page']}&highlight=0,1,0,1"}]},
        "signoff": {"method": "agent_visual", "verifier": "agent:one"}}


def decl(did, **extra):
    return {"id": did, "name": did, "kind": "theorem", "module": "Notes.Box", "file": "Notes/Box.lean",
            "line": 10, "private": False, "anonymous": False, **extra}


def document() -> dict:
    return {
        "companion": {"commit": "1" * 40, "repo": "git.example.invalid/owner/notes"},
        "sources": [{"id": "notes-pdf", "kind": "pdf", "title": "Notes (synthetic)", "sha256": SHA,
                     "bytes": 1234, "url": "https://notes.example.invalid/notes.pdf",
                     "alternate_urls": ["https://mirror.example.invalid/notes.pdf"],
                     "physical_page_count": 9, "edition": "second", "retrieved_at": "2026-01-02T00:00:00Z",
                     "identifiers": {"isbn": ["9780000000002", "9780000000019"], "doi": "10.0000/x"},
                     "page_size": {"width_pt": 600, "height_pt": 800}, "printed_page_offset": 1},
                    {"id": "site", "kind": "web", "revision": "r1", "title": "a site"}],
        "items": [
            {"id": "theorem-1.2.3", "form": "numbered", "kind": "Theorem", "number": "1.2.3",
             "label": "Theorem 1.2.3 (Boxes)", "title": "Boxes", "source": "notes-pdf", "coverage": "formalized"},
            {"id": "remark-1.2.4", "form": "numbered", "kind": "Remark", "number": "1.2.4", "source": "notes-pdf",
             "coverage": "partial_only", "coverage_note": "only the closed case"},
            {"id": "exercise-1.3.1", "form": "numbered", "kind": "Exercise", "number": "1.3.1", "sub": "ii",
             "source": "notes-pdf", "coverage": "not_formalized", "coverage_note": "open",
             "clusters": ["c:13"]},
            {"id": "equation-1.1", "form": "numbered", "kind": "Equation", "number": "1.1",
             "source": "notes-pdf", "clusters": ["c:11"]},
            {"id": "passage-p5", "form": "passage", "kind": "passage", "title": "A remark on unions",
             "source": "notes-pdf", "pages": {"basis": "physical", "first": 5}},
            {"id": "lemma-2.1", "form": "numbered", "kind": "Lemma", "number": "2.1", "source": "notes-pdf"},
        ],
        "clusters": [{"id": "c:11", "locator": "Section 1.1, equation (1.1)"},
                     {"id": "c:13", "locator": "Section 1.3, Exercise 1.3.1"}],
        "locations": [
            location("theorem-1.2.3@p002", ["theorem-1.2.3"],
                     selector(2, rect={"x0": 0.1, "y0": 0.2, "x1": 0.6, "y1": 0.35})),
            dict(location("remark-1.2.4@p002", ["equation-1.1", "remark-1.2.4"],
                          selector(2, quads=[{"x0": 0.1, "y0": 0.5, "x1": 0.9, "y1": 0.5,
                                         "x2": 0.9, "y2": 0.6, "x3": 0.1, "y3": 0.6},
                                        {"x0": 0.1, "y0": 0.6, "x1": 0.4, "y1": 0.6,
                                         "x2": 0.4, "y2": 0.7, "x3": 0.1, "y3": 0.7}])),
                 x_item_order=["remark-1.2.4", "equation-1.1"]),
            location("exercise-1.3.1@p003", ["exercise-1.3.1"],
                     selector(3, rect={"x0": 0.7, "y0": 0.0, "x1": 1.0, "y1": 0.1})),
            location("passage-p5@p005", ["passage-p5"],
                     {**selector(5, rect={"x0": 60, "y0": 80, "x1": 300, "y1": 160}),
                      "coordinate_space": {**SPACE, "units": "pt", "origin": "bottom_left", "y_axis": "up"}}),
            location("lemma-2.1@p006", ["lemma-2.1"],
                     selector(6, rect={"x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2})),
            {"id": "elsewhere", "items": ["theorem-1.2.3"],
             "target": {"source": "site", "selector": [{"type": "SitePageSelector", "path": "a.html"}]}},
        ],
        "declarations": [decl("Notes.Box.mono"), decl("Notes.Box.part", kind="lemma"),
                         decl("Notes.Box.help", private=True, line=None), decl("Notes.Box.ext"),
                         decl("Notes.Box.orphan")],
        "provenance": [
            {"id": "prov-curation", "actor": "agent", "method": "curation", "confirmed_by_human": False,
             "note": "curated by one agent, checked by another", "x_method": "semantic_curation"},
            {"id": "prov-verdicts", "actor": "agent", "method": "adjudication", "confirmed_by_human": False,
             "note": "two readings and a reconciler"}],
        "links": [
            {"id": "theorem-1.2.3/attributed/Notes.Box.part", "item": "theorem-1.2.3",
             "declaration": "Notes.Box.part", "role": "attributed", "provenance": "prov-curation"},
            {"id": "theorem-1.2.3/key/Notes.Box.mono", "item": "theorem-1.2.3",
             "declaration": "Notes.Box.mono", "role": "key", "provenance": "prov-curation"},
            {"id": "remark-1.2.4/attributed/Notes.Box.part", "item": "remark-1.2.4",
             "declaration": "Notes.Box.part", "role": "attributed", "relation": "part"},
            {"id": "passage-p5/support/Notes.Box.help", "item": "passage-p5",
             "declaration": "Notes.Box.help", "role": "support"},
            {"id": "lemma-2.1/key/Notes.Box.ext", "item": "lemma-2.1", "declaration": "Notes.Box.ext",
             "role": "key"},
            {"id": "lemma-2.1/key/Notes.Box.orphan", "item": "lemma-2.1", "declaration": "Notes.Box.orphan",
             "role": "key", "orphaned": True},
        ],
        "snippets": [{"declaration": "Notes.Box.mono", "text": "theorem mono : True", "line": 10},
                     {"declaration": "Notes.Box.part", "text": "lemma part…", "line": 12, "truncated": True}],
        "destinations": [
            {"declaration": "Notes.Box.mono", "href": "Notes/Box/index.html", "resolution": "exact",
             "fragment": "Notes___Box___mono«x»"},
            {"declaration": "Notes.Box.part", "href": "Notes/Box/index.html", "resolution": "fallback",
             "fallback_label": "module page"},
            {"declaration": "Notes.Box.ext", "href": "https://docs.example.invalid/Notes/Box.html",
             "resolution": "exact", "fragment": "Notes.Box.ext"},
        ],
        "verdicts": [{"item": "theorem-1.2.3", "verdict": "deviation", "severity": "low", "status": "fixed",
                      "summary": "closed boxes only", "evidence": ["Notes/Box.lean:10"],
                      "resolution": {"state": "fixed", "pr": 3}, "provenance": "prov-verdicts",
                      "declarations": [{"declaration": "Notes.Box.mono", "verdict": "faithful",
                                        "note": "repaired"}]}],
    }


def by_id(conversion: shards.Conversion) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for record in conversion.records:
        out.setdefault(record["id"], []).append(record)
    return out


class TestTheMapping(unittest.TestCase):
    def setUp(self):
        self.conversion = shards.convert(document())
        self.records = by_id(self.conversion)

    def test_nothing_was_left_behind(self):
        self.assertEqual(self.conversion.problems, [])
        self.assertEqual(self.conversion.counts["regions"], 5)
        self.assertEqual(self.conversion.counts["records"], 7)

    def test_a_link_is_one_record_with_the_pdf_source_and_a_repository_pin(self):
        [record] = self.records["theorem-1.2.3/key/Notes.Box.mono"]
        self.assertEqual(record["source"], {
            "type": "pdf", "uri": "https://notes.example.invalid/notes.pdf",
            "pin": {"type": "book", "title": "Notes (synthetic)", "edition": "second", "sha256": SHA,
                    "url": "https://notes.example.invalid/notes.pdf", "retrieved": "2026-01-02T00:00:00Z",
                    "isbn": "9780000000002", "identifiers": {"doi": "10.0000/x", "isbn-2": "9780000000019"}},
            "extensions": {NS: {"source_id": "notes-pdf", "bytes": 1234, "physical_page_count": 9,
                                "alternate_urls": ["https://mirror.example.invalid/notes.pdf"]}}})
        self.assertEqual(record["location"], {"page": 2, "rect": [0.1, 0.2, 0.5, 0.15]})
        self.assertEqual(record["target"], {
            "uri": "../../../Notes/Box/index.html#Notes___Box___mono%C2%ABx%C2%BB",
            "pin": {"type": "repository", "url": "https://git.example.invalid/owner/notes", "commit": "1" * 40,
                    "path": "Notes/Box.lean", "line": 10, "module": "Notes.Box", "declaration": "Notes.Box.mono"}})
        self.assertEqual((record["kind"], record["label"], record["status"]),
                         ("theorem", "Theorem 1.2.3 (Boxes)", "formalized"))
        ext = record["extensions"][NS]
        self.assertEqual(ext["item"], "theorem-1.2.3")
        self.assertEqual(ext["region"], "theorem-1.2.3@p002")
        self.assertEqual(ext["role"], "key")
        self.assertEqual(ext["declaration"], {"id": "Notes.Box.mono", "kind": "theorem"})
        self.assertEqual(ext["snippet"], {"text": "theorem mono : True", "line": 10})
        self.assertEqual(ext["destination"], {"resolution": "exact"})
        self.assertEqual(ext["signoff"], {"method": "agent_visual", "verifier": "agent:one"})
        self.assertEqual(ext["verdicts"], [{"verdict": "deviation", "severity": "low", "status": "fixed",
                                            "summary": "closed boxes only", "evidence": ["Notes/Box.lean:10"],
                                            "resolution": {"state": "fixed", "pr": 3},
                                            "provenance": {"actor": "agent", "method": "adjudication",
                                                           "confirmed_by_human": False,
                                                           "note": "two readings and a reconciler"}}])
        self.assertEqual(ext["provenance"], {"actor": "agent", "method": "curation", "confirmed_by_human": False})
        self.assertEqual(ext["locator"], {"physical": [2], "printed": [1]})
        self.assertNotIn("region_items", ext)
        self.assertEqual(ext["declaration_verdict"], {"verdict": "faithful", "status": "fixed", "note": "repaired"})

    def test_a_fallback_destination_has_no_fragment_and_keeps_its_label(self):
        [record] = self.records["theorem-1.2.3/attributed/Notes.Box.part"]
        self.assertEqual(record["target"]["uri"], "../../../Notes/Box/index.html", "one folder deeper, in its item's")
        self.assertEqual(record["extensions"][NS]["destination"],
                         {"resolution": "fallback", "fallback_label": "module page"})
        self.assertEqual(record["extensions"][NS]["snippet"]["truncated"], True)

    def test_an_https_destination_is_kept_whole(self):
        [record] = self.records["lemma-2.1/key/Notes.Box.ext"]
        self.assertEqual(record["target"]["uri"], "https://docs.example.invalid/Notes/Box.html#Notes.Box.ext")

    def test_a_declaration_with_no_rendered_page_links_its_source_file(self):
        [record] = self.records["passage-p5/support/Notes.Box.help"]
        self.assertEqual(record["target"]["uri"], "../../Notes/Box.lean")
        self.assertNotIn("line", record["target"]["pin"])
        self.assertNotIn("destination", record["extensions"][NS])
        self.assertEqual(record["extensions"][NS]["declaration"]["private"], True)
        self.assertEqual(self.conversion.counts["targets_without_a_rendered_page"], 1)

    def test_an_orphaned_link_is_not_exported(self):
        self.assertNotIn("lemma-2.1/key/Notes.Box.orphan", self.records)

    def test_a_quad_region_is_its_bounding_rect_and_keeps_its_quads(self):
        [record] = self.records["remark-1.2.4/attributed/Notes.Box.part"]
        self.assertEqual(record["location"], {"page": 2, "rect": [0.1, 0.5, 0.8, 0.2]})
        self.assertEqual(record["extensions"][NS]["quads"],
                         [[0.1, 0.5, 0.9, 0.5, 0.9, 0.6, 0.1, 0.6], [0.1, 0.6, 0.4, 0.6, 0.4, 0.7, 0.1, 0.7]])
        self.assertEqual((record["status"], record["note"]), ("partial", "only the closed case"))
        self.assertEqual(record["extensions"][NS]["relation"], "part")

    def test_a_pt_region_from_the_bottom_left_becomes_top_left_fractions(self):
        [record] = self.records["passage-p5/support/Notes.Box.help"]
        self.assertEqual(record["location"], {"page": 5, "rect": [0.1, 0.8, 0.4, 0.1]})
        self.assertEqual((record["kind"], record["label"]), ("passage", "A remark on unions"))
        self.assertEqual(record["extensions"][NS]["locator"], {"physical": [5], "printed": [4]})
        self.assertNotIn("provenance", record["extensions"][NS], "the link names no provenance")

    def test_an_item_that_links_nothing_is_one_target_less_record_per_region(self):
        [record] = self.records["exercise-1.3.1"]
        self.assertNotIn("target", record)
        self.assertEqual(record["status"], {"value": "not_formalized", "pin": {
            "type": "repository", "url": "https://git.example.invalid/owner/notes", "commit": "1" * 40}})
        self.assertEqual(record["label"], "Exercise 1.3.1(ii)")
        self.assertEqual(record["extensions"][NS]["clusters"], ["c:13"])
        self.assertEqual(record["extensions"][NS]["locator"],
                         {"text": "Section 1.3, Exercise 1.3.1", "physical": [3], "printed": [2]})
        [equation] = self.records["equation-1.1"]
        self.assertNotIn("status", equation)
        self.assertEqual(equation["extensions"][NS]["region"], "remark-1.2.4@p002")
        self.assertEqual(equation["label"], "Equation 1.1")

    def test_a_region_keeps_the_order_its_curation_names_its_items_in(self):
        for record in self.records["remark-1.2.4/attributed/Notes.Box.part"] + self.records["equation-1.1"]:
            self.assertEqual(record["extensions"][NS]["region_items"], ["remark-1.2.4", "equation-1.1"])
        doc = document()
        doc["locations"][1]["x_item_order"] = ["remark-1.2.4", "lemma-2.1"]
        [equation] = by_id(shards.convert(doc))["equation-1.1"]
        self.assertEqual(equation["extensions"][NS]["region_items"], ["equation-1.1", "remark-1.2.4"],
                         "an order that names other items is ignored")

    def test_an_item_on_several_pages_names_them_all_on_every_record(self):
        doc = document()
        doc["locations"].append(location("theorem-1.2.3@p004", ["theorem-1.2.3"],
                                         selector(4, rect={"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2})))
        for record in by_id(shards.convert(doc))["theorem-1.2.3/key/Notes.Box.mono"]:
            self.assertEqual(record["extensions"][NS]["locator"], {"physical": [2, 4], "printed": [1, 3]})

    def test_a_rect_that_reaches_the_edge_stays_on_the_page_in_exact_arithmetic(self):
        [record] = self.records["exercise-1.3.1"]
        x, y, w, h = record["location"]["rect"]
        self.assertLessEqual(Fraction(repr(x)) + Fraction(repr(w)), 1)
        self.assertEqual(companion.validate(record), [])

    def test_a_location_on_another_source_is_not_exported(self):
        regions = {r["extensions"][NS]["region"] for r in self.conversion.records}
        self.assertNotIn("elsewhere", regions)

    def test_every_record_validates(self):
        for record in self.conversion.records:
            with self.subTest(record=record["id"]):
                self.assertEqual(companion.validate(record), [])


class TestTheFiles(unittest.TestCase):
    def test_records_go_to_their_section(self):
        conversion = shards.convert(document())
        self.assertEqual({name: sorted({r["extensions"][NS]["item"] for r in records})
                          for name, records in conversion.sections.items()},
                         {"1.1": ["equation-1.1"], "1.2": ["remark-1.2.4", "theorem-1.2.3"],
                          "1.3": ["exercise-1.3.1"], "other": ["lemma-2.1", "passage-p5"]})

    def test_section_of(self):
        clusters = {"c": {"locator": "Section 4.2, Lemma 4.2.1"}, "d": {"locator": "pp. 3-4"}}
        for item, section in (({"number": "1.2.7"}, "1.2"), ({"number": "1.2.7", "clusters": ["c"]}, "1.2"),
                              ({"number": "1.1", "clusters": ["c"]}, "4.2"), ({"section": "Section 3.1"}, "3.1"),
                              ({"number": "3.4"}, "other"), ({"clusters": ["d"]}, "other"), ({}, "other")):
            with self.subTest(item=item):
                self.assertEqual(shards.section_of(item, clusters), section)

    def test_an_item_with_several_records_gets_a_folder_and_one_record_a_file(self):
        conversion = shards.convert(document())
        self.assertEqual(list(conversion.files), [
            "1.1/equation-1.1.json",
            "1.2/remark-1.2.4__attributed__Notes.Box.part.json",
            "1.2/theorem-1.2.3/attributed__Notes.Box.part.json",
            "1.2/theorem-1.2.3/key__Notes.Box.mono.json",
            "1.3/exercise-1.3.1.json",
            "other/lemma-2.1__key__Notes.Box.ext.json",
            "other/passage-p5__support__Notes.Box.help.json"])
        self.assertEqual(conversion.records, list(conversion.files.values()))
        files = shards.file_bytes(conversion)
        self.assertEqual([path for path in files if path.endswith(shards.DEFAULTS)],
                         ["1.2/theorem-1.2.3/_defaults.json", "_defaults.json"],
                         "defaults at the top and in item folders only")
        self.assertEqual(set(files) - {"1.2/theorem-1.2.3/_defaults.json", "_defaults.json"}, set(conversion.files))
        self.assertIn("lemma part…".encode("utf-8"), files["1.2/theorem-1.2.3/attributed__Notes.Box.part.json"])

    def test_the_reader_reads_back_every_record_from_the_files(self):
        conversion = shards.convert(document())
        files = shards.file_bytes(conversion)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name, raw in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(raw)
            found = discovery.collect([root])
        self.assertEqual(found.problems, [])
        self.assertEqual({r.path.relative_to(root).as_posix(): r.annotation for r in found.records}, conversion.files)

    def test_the_bytes_are_deterministic(self):
        doc = document()
        shuffled = copy.deepcopy(doc)
        for key in ("links", "locations", "declarations", "destinations"):
            shuffled[key].reverse()
        self.assertEqual(shards.file_bytes(shards.convert(doc)), shards.file_bytes(shards.convert(shuffled)))

    def test_every_file_passes_the_scan_and_a_broken_one_does_not(self):
        files = shards.file_bytes(shards.convert(document()))
        self.assertEqual(shards.scan_problems(files), [])
        files["1.2/broken.json"] = (b'{"source": {"type": "pdf", "uri": "x"}, "target": null,'
                                    b' "location": {"page": 1, "rect": [0.5, 0, 0.6, 1]}}\n')
        self.assertTrue(any("1.2/broken.json" in line and "PDF_RECT" in line for line in shards.scan_problems(files)))


def named(*rows) -> list[dict]:
    """Records with just what a file name is made of: ``(item, role, declaration, region)``."""
    out = []
    for item, role, decl, region in rows:
        ext = {"item": item, "region": region or f"{item}@p001"}
        if role:
            ext.update(role=role, declaration={"id": decl})
        out.append({"extensions": {NS: ext}})
    return out


def suffix(key: str) -> str:
    return "-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10] + ".json"


class TestFileNames(unittest.TestCase):
    def test_a_name_is_the_item_role_and_declaration(self):
        self.assertEqual(shards.file_names(named(("theorem-1.2.3", "key", "Notes.Box.mono", None),
                                                 ("exercise-1.3.1", None, None, None))),
                         ["theorem-1.2.3__key__Notes.Box.mono.json", "exercise-1.3.1.json"])

    def test_every_character_outside_the_safe_set_becomes_a_dash(self):
        self.assertEqual(shards.file_names(named(("thm 1.2/3", "key", "Notes.σ_finite'", None),
                                                 (".hidden", None, None, None))),
                         ["thm-1.2-3__key__Notes.-_finite-.json", "-hidden.json"])
        for name in shards.file_names(named(("a:b", "key", "x/y\\z «w»", None))):
            self.assertRegex(name, shards.ANNOTATION_FILE)

    def test_a_long_name_is_truncated_and_hashed_from_the_full_key(self):
        decl = "Notes." + "veryLongDeclarationName" * 8
        key = f"theorem-1.2.3__key__{decl}"
        [name] = shards.file_names(named(("theorem-1.2.3", "key", decl, None)))
        self.assertEqual(len(name), shards.MAX_NAME)
        self.assertEqual(name, key[:shards.MAX_NAME - len(suffix(key))] + suffix(key))
        self.assertRegex(name, shards.ANNOTATION_FILE)
        counts = collections.Counter()
        shards.file_names(named(("theorem-1.2.3", "key", decl, None), ("t", "key", "short", None)), counts)
        self.assertEqual((counts["files_truncated"], counts["files_hashed"]), (1, 1))
        [exact] = shards.file_names(named(("x" * (shards.MAX_NAME - 5), None, None, None)))
        self.assertEqual(exact, "x" * (shards.MAX_NAME - 5) + ".json", "exactly the limit is not truncated")

    def test_two_records_with_one_name_both_take_the_hash(self):
        self.assertEqual(shards.file_names(named(("a b", None, None, None), ("a/b", None, None, None),
                                                 ("c", None, None, None))),
                         ["a-b" + suffix("a b"), "a-b" + suffix("a/b"), "c.json"])

    def test_names_that_differ_only_in_case_both_take_the_hash(self):
        self.assertEqual(shards.file_names(named(("Lemma-1", None, None, None), ("lemma-1", None, None, None))),
                         ["Lemma-1" + suffix("Lemma-1"), "lemma-1" + suffix("lemma-1")])

    def test_one_item_in_two_regions_hashes_each_record_with_its_region(self):
        names = shards.file_names(named(("t", "key", "D", "t@p002"), ("t", "key", "D", "t@p003")))
        self.assertEqual(names, ["t__key__D" + suffix("t__key__D@t@p002"), "t__key__D" + suffix("t__key__D@t@p003")])

    def test_a_hashed_name_that_meets_a_plain_one_hashes_that_one_too(self):
        clash = "a-b" + suffix("a b")[:-len(".json")]
        names = shards.file_names(named(("a b", None, None, None), ("a/b", None, None, None),
                                        (clash, None, None, None)))
        self.assertEqual(len({n.lower() for n in names}), 3)
        self.assertEqual(names[2], clash + suffix(clash))

    def test_a_windows_device_name_is_hashed(self):
        self.assertEqual(shards.file_names(named(("CON", None, None, None), ("nul.1", None, None, None),
                                                 ("console", None, None, None))),
                         ["CON" + suffix("CON"), "nul.1" + suffix("nul.1"), "console.json"])

    def test_no_file_is_named_like_folder_defaults(self):
        self.assertEqual(shards.file_names(named(("_defaults", None, None, None), ("_Defaults", "key", "D", None)),
                                           in_item=False),
                         ["_defaults" + suffix("_defaults"), "_Defaults__key__D.json"])
        self.assertEqual(shards.layout(named(("_defaults.json", "key", "D", "a"), ("_defaults.json", "key", "E", "a")))
                         [0].split("/")[0], "_defaults.json" + suffix("_defaults.json")[:-len(".json")])

    def test_one_item_in_a_folder_named_like_a_file_beside_it_hashes_both(self):
        paths = shards.layout(named(("t", "key", "D", None), ("t", "key", "E", None), ("T", None, None, None)))
        self.assertEqual(paths, ["t/key__D.json", "t/key__E.json", "T.json"])
        paths = shards.layout(named(("t.json", "key", "D", None), ("t.json", "key", "E", None), ("t", None, None, None)))
        self.assertEqual(paths[2], "t" + suffix("t"))
        self.assertTrue(paths[0].startswith("t.json" + suffix("t.json")[:-len(".json")] + "/"))

    def test_names_do_not_depend_on_the_record_order_but_the_records(self):
        rows = [("a b", None, None, None), ("a/b", None, None, None), ("Z", "key", "D", None)]
        forward = dict(zip([r[0] for r in rows], shards.file_names(named(*rows))))
        backward = dict(zip([r[0] for r in reversed(rows)], shards.file_names(named(*reversed(rows)))))
        self.assertEqual(forward, backward)

    def test_two_conversions_of_a_document_with_one_item_in_two_regions(self):
        doc = document()
        doc["locations"].append(location("theorem-1.2.3@p004", ["theorem-1.2.3"],
                                         selector(4, rect={"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2})))
        conversion = shards.convert(doc)
        self.assertEqual(conversion.problems, [])
        names = [p for p in conversion.files if p.startswith("1.2/theorem-1.2.3/key__")]
        self.assertEqual(names, sorted(
            "1.2/theorem-1.2.3/key__Notes.Box.mono" + suffix(f"key__Notes.Box.mono@{region}")
            for region in ("theorem-1.2.3@p002", "theorem-1.2.3@p004")))
        self.assertEqual(shards.scan_problems(shards.file_bytes(conversion)), [])


class TestRefusals(unittest.TestCase):
    def test_a_page_box_that_is_not_displayed_is_a_problem(self):
        doc = document()
        doc["locations"][0]["target"]["selector"][0]["refinedBy"]["coordinate_space"]["page_box"] = "TrimBox"
        conversion = shards.convert(doc)
        self.assertTrue(any("TrimBox" in p for p in conversion.problems), conversion.problems)

    def test_a_region_past_the_page_count_is_a_problem(self):
        doc = document()
        doc["sources"][0]["physical_page_count"] = 4
        self.assertTrue(any("past the source's 4 pages" in p for p in shards.convert(doc).problems))

    def test_an_unsafe_destination_is_a_problem_and_an_unlinked_card(self):
        doc = document()
        doc["destinations"][0]["href"] = "../outside.html"
        conversion = shards.convert(doc)
        self.assertTrue(any("../outside.html" in p for p in conversion.problems))

    def test_the_source_must_be_one_pdf_with_a_url_and_a_sha256(self):
        doc = document()
        with self.assertRaisesRegex(ValueError, "0 pdf sources"):
            shards.convert(doc, source_id="site")
        doc["sources"][0].pop("url")
        with self.assertRaisesRegex(ValueError, "no url"):
            shards.convert(doc)

    def test_a_local_checkout_has_no_repository_url(self):
        doc = document()
        doc["companion"]["repo"] = "local/notes"
        [record] = by_id(shards.convert(doc))["theorem-1.2.3/key/Notes.Box.mono"]
        self.assertNotIn("url", record["target"]["pin"])


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
