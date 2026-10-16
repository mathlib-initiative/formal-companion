"""The annotation document → Literature Links 2.0 annotation files mapping (`scripts/overlay/shards.py`).

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
    """The records by item id, and by link id (``<item>/<role>/<declaration>``) for a record with a link."""
    out: dict[str, list[dict]] = {}
    for record in conversion.records:
        out.setdefault(record["id"], []).append(record)
        if record.get("relation") != "noMatch":
            for end in record["object"]:
                key = f"{record['id']}/{end['role']}/{end['extensions'][NS]['declaration']['id']}"
                out.setdefault(key, []).append(record)
    return out


def ext(node: dict) -> dict:
    return node["extensions"][NS]


class TestTheMapping(unittest.TestCase):
    def setUp(self):
        self.conversion = shards.convert(document())
        self.records = by_id(self.conversion)

    def test_nothing_was_left_behind(self):
        self.assertEqual(self.conversion.problems, [])
        self.assertEqual(self.conversion.counts["regions"], 5)
        self.assertEqual(self.conversion.counts["records"], 7)

    def test_a_link_is_one_record_with_the_pdf_regions_as_subject_and_the_declaration_as_object(self):
        [record] = self.records["theorem-1.2.3/key/Notes.Box.mono"]
        self.assertEqual(record["subject"], [{
            "type": "pdf", "uri": "https://notes.example.invalid/notes.pdf", "sha256": SHA,
            "citation": {"id": "notes-pdf", "type": "book", "title": "Notes (synthetic)", "edition": "second",
                         "URL": "https://notes.example.invalid/notes.pdf",
                         "accessed": {"date-parts": [[2026, 1, 2]], "raw": "2026-01-02T00:00:00Z"},
                         "ISBN": "9780000000002", "DOI": "10.0000/x"},
            "identifiers": {"isbn-2": "9780000000019"},
            "location": {"page": 2, "rect": [0.1, 0.2, 0.5, 0.15]},
            "title": "Theorem 1.2.3 (Boxes)",
            "extensions": {NS: {"source_id": "notes-pdf", "bytes": 1234, "physical_page_count": 9,
                                "alternate_urls": ["https://mirror.example.invalid/notes.pdf"],
                                "region": "theorem-1.2.3@p002",
                                "signoff": {"method": "agent_visual", "verifier": "agent:one"}}}}])
        [end] = record["object"]
        self.assertEqual({k: v for k, v in end.items() if k != "extensions"}, {
            "type": "lean", "uri": "https://git.example.invalid/owner/notes", "version": "1" * 40,
            "location": {"declaration": "Notes.Box.mono", "module": "Notes.Box"}, "role": "key"})
        self.assertEqual(ext(end), {
            "declaration": {"id": "Notes.Box.mono", "kind": "theorem"},
            "snippet": {"text": "theorem mono : True", "line": 10},
            "destination": {"resolution": "exact", "fragment": "Notes___Box___mono«x»"},
            "declaration_verdict": {"verdict": "faithful", "status": "fixed", "note": "repaired"}})
        self.assertEqual((record["id"], record["kind"], record["status"]), ("theorem-1.2.3", "theorem", "formalized"))
        self.assertNotIn("relation", record, "a link with no relation token claims none")
        self.assertEqual(ext(record), {
            "locator": {"physical": [2], "printed": [1]},
            "provenance": {"actor": "agent", "method": "curation", "confirmed_by_human": False},
            "verdicts": [{"verdict": "deviation", "severity": "low", "status": "fixed",
                          "summary": "closed boxes only", "evidence": ["Notes/Box.lean:10"],
                          "resolution": {"state": "fixed", "pr": 3},
                          "provenance": {"actor": "agent", "method": "adjudication", "confirmed_by_human": False,
                                         "note": "two readings and a reconciler"}}]})

    def test_the_records_about_one_item_share_its_id_and_its_regions(self):
        records = self.records["theorem-1.2.3"]
        self.assertEqual(sorted(r["object"][0]["role"] for r in records), ["attributed", "key"])
        self.assertEqual(records[0]["subject"], records[1]["subject"])

    def test_a_page_the_module_gives_keeps_only_its_resolution(self):
        [record] = self.records["theorem-1.2.3/attributed/Notes.Box.part"]
        self.assertEqual(ext(record["object"][0])["destination"],
                         {"resolution": "fallback", "fallback_label": "module page"})
        self.assertEqual(ext(record["object"][0])["snippet"]["truncated"], True)
        self.assertEqual((shards.page_of("Notes.Box"), shards.verso_fragment("Notes.Box.part")),
                         ("Notes/Box/index.html", "Notes___Box___part"))

    def test_an_https_destination_keeps_its_page_and_anchor(self):
        [record] = self.records["lemma-2.1/key/Notes.Box.ext"]
        self.assertEqual(ext(record["object"][0])["destination"],
                         {"resolution": "exact", "href": "https://docs.example.invalid/Notes/Box.html",
                          "fragment": "Notes.Box.ext"})

    def test_a_declaration_with_no_rendered_page_has_no_destination(self):
        [record] = self.records["passage-p5/support/Notes.Box.help"]
        [end] = record["object"]
        self.assertEqual(end["location"], {"declaration": "Notes.Box.help", "module": "Notes.Box"})
        self.assertNotIn("destination", ext(end))
        self.assertEqual(ext(end)["declaration"]["private"], True)
        self.assertEqual(self.conversion.counts["declarations_without_a_rendered_page"], 1)

    def test_an_anonymous_declaration_is_its_module_and_the_line_it_starts_on(self):
        doc = document()
        doc["declarations"].append({"id": "example@Notes/Box.lean:30", "kind": "example", "module": "Notes.Box",
                                    "file": "Notes/Box.lean", "line": 30, "anonymous": True})
        doc["declarations"].append({"id": "example:lost", "kind": "example", "module": "Notes.Box",
                                    "file": "Notes/Box.lean", "anonymous": True, "unlocated": True})
        doc["links"] += [{"id": "lemma-2.1/example/example@Notes/Box.lean:30", "item": "lemma-2.1",
                          "declaration": "example@Notes/Box.lean:30", "role": "example"},
                         {"id": "lemma-2.1/example/example:lost", "item": "lemma-2.1",
                          "declaration": "example:lost", "role": "example"}]
        conversion = shards.convert(doc)
        records = by_id(conversion)
        [located] = records["lemma-2.1/example/example@Notes/Box.lean:30"]
        self.assertEqual(located["object"][0]["location"], {"module": "Notes.Box", "lines": [30]})
        self.assertEqual(ext(located["object"][0])["declaration"], {"id": "example@Notes/Box.lean:30",
                                                                    "kind": "example", "anonymous": True})
        [lost] = records["lemma-2.1/example/example:lost"]
        self.assertEqual(lost["object"][0]["location"],
                         {"profile": shards.UNLOCATED_PROFILE, "module": "Notes.Box", "id": "example:lost"})
        self.assertEqual(conversion.counts["unlocated_anonymous_declarations"], 1)
        self.assertEqual(shards.scan_problems(shards.file_bytes(conversion)), [])

    def test_an_orphaned_link_is_not_exported(self):
        self.assertNotIn("lemma-2.1/key/Notes.Box.orphan", self.records)

    def test_a_relation_token_becomes_the_relation_and_one_it_does_not_name_alone_stays(self):
        [record] = self.records["remark-1.2.4/attributed/Notes.Box.part"]
        self.assertEqual(record["relation"], "hasPart")
        self.assertNotIn("relation", ext(record))
        for token, relation, kept in (("exact", "exactMatch", False), ("generalization", "broadMatch", False),
                                      ("special_case", "narrowMatch", False), ("related", "relatedMatch", True),
                                      ("invocation", "relatedMatch", True), ("uses", "relatedMatch", True),
                                      ("states", None, True)):
            doc = document()
            doc["links"][2]["relation"] = token
            [record] = by_id(shards.convert(doc))["remark-1.2.4/attributed/Notes.Box.part"]
            with self.subTest(token=token):
                self.assertEqual(record.get("relation"), relation)
                self.assertEqual(ext(record).get("relation"), token if kept else None)

    def test_a_quad_region_is_its_bounding_rect_and_keeps_its_quads(self):
        [record] = self.records["remark-1.2.4/attributed/Notes.Box.part"]
        [end] = record["subject"]
        self.assertEqual(end["location"], {"page": 2, "rect": [0.1, 0.5, 0.8, 0.2]})
        self.assertEqual(ext(end)["quads"],
                         [[0.1, 0.5, 0.9, 0.5, 0.9, 0.6, 0.1, 0.6], [0.1, 0.6, 0.4, 0.6, 0.4, 0.7, 0.1, 0.7]])
        self.assertEqual((record["status"], record["note"]), ("partial", "only the closed case"))

    def test_a_pt_region_from_the_bottom_left_becomes_top_left_fractions(self):
        [record] = self.records["passage-p5/support/Notes.Box.help"]
        self.assertEqual(record["subject"][0]["location"], {"page": 5, "rect": [0.1, 0.8, 0.4, 0.1]})
        self.assertEqual((record["kind"], record["subject"][0]["title"]), ("passage", "A remark on unions"))
        self.assertEqual(ext(record)["locator"], {"physical": [5], "printed": [4]})
        self.assertNotIn("provenance", ext(record), "the link names no provenance")

    def test_an_item_that_links_nothing_is_one_record(self):
        [record] = self.records["exercise-1.3.1"]
        self.assertEqual((record["status"], record["relation"]), ("not_formalized", "noMatch"))
        self.assertEqual(record["object"], [{"type": "lean", "uri": "https://git.example.invalid/owner/notes",
                                             "version": "1" * 40}],
                         "a checked absence: the repository at the commit")
        self.assertEqual(record["subject"][0]["title"], "Exercise 1.3.1(ii)")
        self.assertEqual(ext(record)["clusters"], ["c:13"])
        self.assertEqual(ext(record)["locator"], {"text": "Section 1.3, Exercise 1.3.1", "physical": [3], "printed": [2]})
        [equation] = self.records["equation-1.1"]
        self.assertEqual(equation["object"], [], "unlinked, and no claim that nothing matches")
        self.assertNotIn("status", equation)
        self.assertNotIn("relation", equation)
        self.assertEqual(ext(equation["subject"][0])["region"], "remark-1.2.4@p002")
        self.assertEqual(equation["subject"][0]["title"], "Equation 1.1")
        self.assertEqual((self.conversion.counts["records_linking_nothing"],
                          self.conversion.counts["checked_absences"]), (2, 1))

    def test_status_is_a_recommended_value_and_another_class_stays_in_the_extension(self):
        doc = document()
        classes = {"passage-p5": "support_only", "lemma-2.1": "example_only"}
        for item in doc["items"]:
            item["coverage"] = classes.get(item["id"], item.get("coverage"))
        records = by_id(shards.convert(doc))
        [support] = records["passage-p5"]
        self.assertEqual((support["status"], ext(support)["coverage"]), ("not_formalized", "support_only"))
        self.assertNotIn("relation", support, "a support link claims no correspondence")
        self.assertTrue(records["lemma-2.1"])
        for record in records["lemma-2.1"]:
            self.assertNotIn("status", record, "no recommended status names the class")
            self.assertEqual(ext(record)["coverage"], "example_only")
        for item in ("theorem-1.2.3", "remark-1.2.4", "exercise-1.3.1", "equation-1.1"):
            for record in self.records[item]:
                self.assertNotIn("coverage", ext(record), f"{item}: its status names its class")

    def test_a_region_keeps_the_order_its_curation_names_its_items_in(self):
        for record in self.records["remark-1.2.4"] + self.records["equation-1.1"]:
            self.assertEqual(ext(record["subject"][0])["region_items"], ["remark-1.2.4", "equation-1.1"])
        doc = document()
        doc["locations"][1]["x_item_order"] = ["remark-1.2.4", "lemma-2.1"]
        [equation] = by_id(shards.convert(doc))["equation-1.1"]
        self.assertEqual(ext(equation["subject"][0])["region_items"], ["equation-1.1", "remark-1.2.4"],
                         "an order that names other items is ignored")

    def test_an_item_on_several_pages_is_one_record_per_link_with_every_region(self):
        doc = document()
        doc["locations"].append(location("theorem-1.2.3@p004", ["theorem-1.2.3"],
                                         selector(4, rect={"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2})))
        conversion = shards.convert(doc)
        [record] = by_id(conversion)["theorem-1.2.3/key/Notes.Box.mono"]
        self.assertEqual([ext(end)["region"] for end in record["subject"]],
                         ["theorem-1.2.3@p002", "theorem-1.2.3@p004"])
        self.assertEqual(ext(record)["locator"], {"physical": [2, 4], "printed": [1, 3]})
        self.assertEqual(conversion.counts["records"], 7)

    def test_a_rect_that_reaches_the_edge_stays_on_the_page_in_exact_arithmetic(self):
        [record] = self.records["exercise-1.3.1"]
        x, y, w, h = record["subject"][0]["location"]["rect"]
        self.assertLessEqual(Fraction(repr(x)) + Fraction(repr(w)), 1)
        self.assertEqual(companion.validate(record), [])

    def test_a_location_on_another_source_is_not_exported(self):
        regions = {ext(end)["region"] for r in self.conversion.records for end in r["subject"]}
        self.assertNotIn("elsewhere", regions)

    def test_every_record_validates(self):
        for path, record in self.conversion.files.items():
            with self.subTest(path=path):
                self.assertEqual(companion.validate(record), [])


class TestTheFiles(unittest.TestCase):
    def test_records_go_to_their_section(self):
        conversion = shards.convert(document())
        self.assertEqual({name: sorted({r["id"] for r in records})
                          for name, records in conversion.sections.items()},
                         {"1.1": ["equation-1.1"], "1.2": ["remark-1.2.4", "theorem-1.2.3"],
                          "1.3": ["exercise-1.3.1"], "other": ["lemma-2.1", "passage-p5"]})

    def test_section_of(self):
        clusters = {"c": {"locator": "Section 4.2, Lemma 4.2.1"}, "d": {"locator": "pp. 3-4"},
                    "e": {"locator": "Section １.２"}}
        for item, section in (({"number": "1.2.7"}, "1.2"), ({"number": "1.2.7", "clusters": ["c"]}, "1.2"),
                              ({"number": "1.1", "clusters": ["c"]}, "4.2"), ({"section": "Section 3.1"}, "3.1"),
                              ({"number": "3.4"}, "other"), ({"clusters": ["d"]}, "other"), ({}, "other"),
                              ({"section": "٣.٤"}, "other"), ({"clusters": ["e"]}, "other")):
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

    def test_the_defaults_hold_an_end_template_for_each_side(self):
        files = shards.file_bytes(shards.convert(document()))
        top = json.loads(files["_defaults.json"])
        self.assertEqual({k: top["subject"][k] for k in ("type", "uri", "sha256")},
                         {"type": "pdf", "uri": "https://notes.example.invalid/notes.pdf", "sha256": SHA})
        self.assertEqual(top["subject"]["citation"]["id"], "notes-pdf")
        self.assertEqual(top["object"], {"type": "lean", "uri": "https://git.example.invalid/owner/notes",
                                         "version": "1" * 40})
        self.assertEqual(json.loads(files["1.3/exercise-1.3.1.json"]).get("object", "left out"), "left out",
                         "a checked absence's object is the template alone")
        self.assertEqual(json.loads(files["1.1/equation-1.1.json"])["object"], [])
        item = json.loads(files["1.2/theorem-1.2.3/_defaults.json"])
        self.assertEqual((item["id"], item["subject"]["title"]), ("theorem-1.2.3", "Theorem 1.2.3 (Boxes)"))
        self.assertNotIn("subject", json.loads(files["1.2/theorem-1.2.3/key__Notes.Box.mono.json"]))

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
        files["1.2/broken.json"] = (b'{"subject": [{"type": "pdf", "uri": "x",'
                                    b' "location": {"page": 1, "rect": [0.5, 0, 0.6, 1]}}], "object": []}\n')
        self.assertTrue(any("1.2/broken.json" in line and "PDF_RECT" in line for line in shards.scan_problems(files)))


def named(*rows) -> list[dict]:
    """Records with just what a file name is made of: ``(item, role, declaration)``."""
    out = []
    for item, role, decl in rows:
        ends = [{"role": role, "extensions": {NS: {"declaration": {"id": decl}}}}] if role else []
        out.append({"id": item, "object": ends})
    return out


def suffix(key: str) -> str:
    return "-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10] + ".json"


class TestFileNames(unittest.TestCase):
    def test_a_name_is_the_item_role_and_declaration(self):
        self.assertEqual(shards.file_names(named(("theorem-1.2.3", "key", "Notes.Box.mono"),
                                                 ("exercise-1.3.1", None, None))),
                         ["theorem-1.2.3__key__Notes.Box.mono.json", "exercise-1.3.1.json"])

    def test_every_character_outside_the_safe_set_becomes_a_dash(self):
        self.assertEqual(shards.file_names(named(("thm 1.2/3", "key", "Notes.σ_finite'"),
                                                 (".hidden", None, None))),
                         ["thm-1.2-3__key__Notes.-_finite-.json", "-hidden.json"])
        for name in shards.file_names(named(("a:b", "key", "x/y\\z «w»"))):
            self.assertRegex(name, shards.ANNOTATION_FILE)

    def test_a_long_name_is_truncated_and_hashed_from_the_full_key(self):
        decl = "Notes." + "veryLongDeclarationName" * 8
        key = f"theorem-1.2.3__key__{decl}"
        [name] = shards.file_names(named(("theorem-1.2.3", "key", decl)))
        self.assertEqual(len(name), shards.MAX_NAME)
        self.assertEqual(name, key[:shards.MAX_NAME - len(suffix(key))] + suffix(key))
        self.assertRegex(name, shards.ANNOTATION_FILE)
        counts = collections.Counter()
        shards.file_names(named(("theorem-1.2.3", "key", decl), ("t", "key", "short")), counts)
        self.assertEqual((counts["files_truncated"], counts["files_hashed"]), (1, 1))
        [exact] = shards.file_names(named(("x" * (shards.MAX_NAME - 5), None, None)))
        self.assertEqual(exact, "x" * (shards.MAX_NAME - 5) + ".json", "exactly the limit is not truncated")

    def test_two_records_with_one_name_both_take_the_hash(self):
        self.assertEqual(shards.file_names(named(("a b", None, None), ("a/b", None, None),
                                                 ("c", None, None))),
                         ["a-b" + suffix("a b"), "a-b" + suffix("a/b"), "c.json"])

    def test_names_that_differ_only_in_case_both_take_the_hash(self):
        self.assertEqual(shards.file_names(named(("Lemma-1", None, None), ("lemma-1", None, None))),
                         ["Lemma-1" + suffix("Lemma-1"), "lemma-1" + suffix("lemma-1")])

    def test_a_hashed_name_that_meets_a_plain_one_hashes_that_one_too(self):
        clash = "a-b" + suffix("a b")[:-len(".json")]
        names = shards.file_names(named(("a b", None, None), ("a/b", None, None),
                                        (clash, None, None)))
        self.assertEqual(len({n.lower() for n in names}), 3)
        self.assertEqual(names[2], clash + suffix(clash))

    def test_a_windows_device_name_is_hashed(self):
        self.assertEqual(shards.file_names(named(("CON", None, None), ("nul.1", None, None),
                                                 ("console", None, None))),
                         ["CON" + suffix("CON"), "nul.1" + suffix("nul.1"), "console.json"])

    def test_no_file_is_named_like_folder_defaults(self):
        self.assertEqual(shards.file_names(named(("_defaults", None, None), ("_Defaults", "key", "D")),
                                           in_item=False),
                         ["_defaults" + suffix("_defaults"), "_Defaults__key__D.json"])
        self.assertEqual(shards.layout(named(("_defaults.json", "key", "D"), ("_defaults.json", "key", "E")))
                         [0].split("/")[0], "_defaults.json" + suffix("_defaults.json")[:-len(".json")])

    def test_one_item_in_a_folder_named_like_a_file_beside_it_hashes_both(self):
        paths = shards.layout(named(("t", "key", "D"), ("t", "key", "E"), ("T", None, None)))
        self.assertEqual(paths, ["t/key__D.json", "t/key__E.json", "T.json"])
        paths = shards.layout(named(("t.json", "key", "D"), ("t.json", "key", "E"), ("t", None, None)))
        self.assertEqual(paths[2], "t" + suffix("t"))
        self.assertTrue(paths[0].startswith("t.json" + suffix("t.json")[:-len(".json")] + "/"))

    def test_names_do_not_depend_on_the_record_order_but_the_records(self):
        rows = [("a b", None, None), ("a/b", None, None), ("Z", "key", "D")]
        forward = dict(zip([r[0] for r in rows], shards.file_names(named(*rows))))
        backward = dict(zip([r[0] for r in reversed(rows)], shards.file_names(named(*reversed(rows)))))
        self.assertEqual(forward, backward)


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

    def test_a_local_checkout_is_the_companion_root_relative_to_each_file(self):
        doc = document()
        doc["companion"]["repo"] = "local/notes"
        conversion = shards.convert(doc)
        self.assertEqual(conversion.files["1.2/theorem-1.2.3/key__Notes.Box.mono.json"]["object"][0]["uri"], "../../../")
        self.assertEqual(conversion.files["other/lemma-2.1__key__Notes.Box.ext.json"]["object"][0]["uri"], "../../")
        files = shards.file_bytes(conversion)
        self.assertEqual(json.loads(files["_defaults.json"])["object"]["uri"], "../")
        self.assertEqual(shards.scan_problems(files), [])

    def test_a_link_to_a_declaration_twice_in_one_role_is_a_problem(self):
        doc = document()
        doc["links"].append(dict(doc["links"][1], id="theorem-1.2.3/key/again"))
        self.assertTrue(any("share the key" in p for p in shards.convert(doc).problems))


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
