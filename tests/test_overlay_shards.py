"""The annotation document → Literature Links shards mapping (`scripts/overlay/shards.py`).

docs/overlay.md section 7, record by record, on a synthetic document: what a
link, a region, a quad region, an item that links nothing, a destination, a
verdict and the source become, which shard each record goes to, and that
every shard passes the vendored `literature_links` scan.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import json
import sys
import unittest
from fractions import Fraction
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from literature_links import companion                               # noqa: E402
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
                     "page_size": {"width_pt": 600, "height_pt": 800}},
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
            location("remark-1.2.4@p002", ["remark-1.2.4", "equation-1.1"],
                     selector(2, quads=[{"x0": 0.1, "y0": 0.5, "x1": 0.9, "y1": 0.5,
                                         "x2": 0.9, "y2": 0.6, "x3": 0.1, "y3": 0.6},
                                        {"x0": 0.1, "y0": 0.6, "x1": 0.4, "y1": 0.6,
                                         "x2": 0.4, "y2": 0.7, "x3": 0.1, "y3": 0.7}])),
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
        "links": [
            {"id": "theorem-1.2.3/attributed/Notes.Box.part", "item": "theorem-1.2.3",
             "declaration": "Notes.Box.part", "role": "attributed"},
            {"id": "theorem-1.2.3/key/Notes.Box.mono", "item": "theorem-1.2.3",
             "declaration": "Notes.Box.mono", "role": "key"},
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
                      "resolution": {"state": "fixed", "pr": 3},
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
            "uri": "../Notes/Box/index.html#Notes___Box___mono%C2%ABx%C2%BB",
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
                                            "resolution": {"state": "fixed", "pr": 3}}])
        self.assertEqual(ext["declaration_verdict"], {"verdict": "faithful", "status": "fixed", "note": "repaired"})

    def test_a_fallback_destination_has_no_fragment_and_keeps_its_label(self):
        [record] = self.records["theorem-1.2.3/attributed/Notes.Box.part"]
        self.assertEqual(record["target"]["uri"], "../Notes/Box/index.html")
        self.assertEqual(record["extensions"][NS]["destination"],
                         {"resolution": "fallback", "fallback_label": "module page"})
        self.assertEqual(record["extensions"][NS]["snippet"]["truncated"], True)

    def test_an_https_destination_is_kept_whole(self):
        [record] = self.records["lemma-2.1/key/Notes.Box.ext"]
        self.assertEqual(record["target"]["uri"], "https://docs.example.invalid/Notes/Box.html#Notes.Box.ext")

    def test_a_declaration_with_no_rendered_page_links_its_source_file(self):
        [record] = self.records["passage-p5/support/Notes.Box.help"]
        self.assertEqual(record["target"]["uri"], "../Notes/Box.lean")
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
        self.assertEqual(record["extensions"][NS]["pages"], {"basis": "physical", "first": 5})

    def test_an_item_that_links_nothing_is_one_target_less_record_per_region(self):
        [record] = self.records["exercise-1.3.1"]
        self.assertNotIn("target", record)
        self.assertEqual(record["status"], {"value": "not_formalized", "pin": {
            "type": "repository", "url": "https://git.example.invalid/owner/notes", "commit": "1" * 40}})
        self.assertEqual(record["label"], "Exercise 1.3.1(ii)")
        self.assertEqual(record["extensions"][NS]["clusters"], ["c:13"])
        [equation] = self.records["equation-1.1"]
        self.assertNotIn("status", equation)
        self.assertEqual(equation["extensions"][NS]["region"], "remark-1.2.4@p002")
        self.assertEqual(equation["label"], "Equation 1.1")

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


class TestTheShards(unittest.TestCase):
    def test_records_go_to_their_section(self):
        conversion = shards.convert(document())
        self.assertEqual({name: sorted({r["extensions"][NS]["item"] for r in records})
                          for name, records in conversion.shards.items()},
                         {"1.1": ["equation-1.1"], "1.2": ["remark-1.2.4", "theorem-1.2.3"],
                          "1.3": ["exercise-1.3.1"], "2": ["lemma-2.1"], "other": ["passage-p5"]})

    def test_section_of(self):
        clusters = {"c": {"locator": "Section 4.2, Lemma 4.2.1"}, "d": {"locator": "pp. 3-4"}}
        for item, section in (({"number": "1.2.7"}, "1.2"), ({"number": "1.2.7", "clusters": ["c"]}, "1.2"),
                              ({"number": "1.1", "clusters": ["c"]}, "4.2"), ({"section": "Section 3.1"}, "3.1"),
                              ({"number": "3.4"}, "3"), ({"clusters": ["d"]}, "other"), ({}, "other")):
            with self.subTest(item=item):
                self.assertEqual(shards.section_of(item, clusters), section)

    def test_lines_are_canonical_sorted_and_end_with_a_newline(self):
        files = shards.shard_bytes(shards.convert(document()))
        self.assertEqual(sorted(files), ["1.1.jsonl", "1.2.jsonl", "1.3.jsonl", "2.jsonl", "other.jsonl"])
        text = files["1.2.jsonl"].decode("utf-8")
        self.assertTrue(text.endswith("\n"))
        lines = text.splitlines()
        for line in lines:
            self.assertEqual(line, json.dumps(json.loads(line), sort_keys=True, separators=(",", ":"),
                                              ensure_ascii=False))
        ids = [json.loads(line)["id"] for line in lines]
        self.assertEqual(ids, ["theorem-1.2.3/key/Notes.Box.mono", "theorem-1.2.3/attributed/Notes.Box.part",
                               "remark-1.2.4/attributed/Notes.Box.part"],
                         "by page, then position, then role")

    def test_the_bytes_are_deterministic(self):
        doc = document()
        shuffled = copy.deepcopy(doc)
        for key in ("links", "locations", "declarations", "destinations"):
            shuffled[key].reverse()
        self.assertEqual(shards.shard_bytes(shards.convert(doc)), shards.shard_bytes(shards.convert(shuffled)))

    def test_every_shard_passes_the_scan_and_a_broken_one_does_not(self):
        files = shards.shard_bytes(shards.convert(document()))
        self.assertEqual(shards.scan_problems(files), [])
        files["1.2.jsonl"] += b'{"source": {"type": "pdf", "uri": "x"}, "location": {"page": 1, "rect": [0.5, 0, 0.6, 1]}}\n'
        self.assertTrue(any("PDF_RECT" in line for line in shards.scan_problems(files)))


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
