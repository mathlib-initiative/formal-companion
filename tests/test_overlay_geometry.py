"""The generic geometry producer: synthetic pages, synthetic items, no book.

Tests ``scripts/overlay/geometry.py`` against section 7 of the overlay contract
(`docs/overlay.md`, seeded as the overlay template's SPEC.md).  The input is
``tests/fixtures/overlay/notes-bbox-layout.xhtml``: a hand-written
``pdftotext -bbox-layout`` document of three pages of "Elementary Sets: Lecture
Notes (synthetic)", carrying a section heading, items of six kinds, two items
that run across a page break, two proof boundaries, a display set past the
text column, a paragraph set off by a gap and an indent, a running head that
reads like a section heading, and a folio on every page (CONTRIBUTING.md
invariant 4: synthetic examples only).  Nothing here reads a PDF: ``pdftotext``
and ``pdftoppm`` are never run, and the crop renderer is a stand-in.
"""
from __future__ import annotations

# The first import, before anything reads HOME (see tests/_hermetic.py).
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from formal_companion_annotations import validate as V              # noqa: E402
from formal_companion_annotations import vocab                      # noqa: E402
from cflib.errors import CfError                                    # noqa: E402
from overlay import geometry                                        # noqa: E402

FIXTURE = HERE / "fixtures" / "overlay" / "notes-bbox-layout.xhtml"
SOURCE_ID = "notes-pdf"
VERIFIER = "agent:reviewer-1"
KINDS = ("Definition", "Theorem", "Lemma", "Exercise", "Remark", "Example")
#: The fixture is an extract of physical pages 4 to 6.
FIRST, LAST = 4, 6
#: The fixture's page box, in points.
WIDTH_PT, HEIGHT_PT = 612.0, 792.0


def layout(*pages: list[tuple[float, float, float, str]]) -> str:
    """A minimal ``-bbox-layout`` document from ``(yMin, xMin, xMax, text)`` lines."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<html><body><doc>"]
    for lines in pages:
        out.append(f'<page width="{WIDTH_PT:f}" height="{HEIGHT_PT:f}"><flow>')
        for (y, x0, x1, text) in lines:
            box = f'xMin="{x0:f}" yMin="{y:f}" xMax="{x1:f}" yMax="{y + 12:f}"'
            words = "".join(f"<word {box}>{w}</word>" for w in text.split(" "))
            out.append(f"<block {box}><line {box}>{words}</line></block>")
        out.append("</flow></page>")
    out.append("</doc></body></html>")
    return "\n".join(out)


def fragment(**overrides) -> dict:
    """The fixture's fragment, with the usual arguments."""
    kwargs = dict(first=FIRST, last=LAST, source_id=SOURCE_ID, kinds=KINDS,
                  verifier_id=VERIFIER, method="agent_visual", section="2.1")
    kwargs.update(overrides)
    xhtml = kwargs.pop("xhtml", None)
    if xhtml is None:
        xhtml = FIXTURE.read_text(encoding="utf-8")
    return geometry.build_fragment(xhtml, **kwargs)


def by_id(records) -> dict:
    return {r["id"]: r for r in records}


def regions_of(frag: dict, item_id: str) -> list[dict]:
    """Every PdfRegionSelector of one item's locations, in location order."""
    out = []
    for location in frag["locations"]:
        if location["items"] != [item_id]:
            continue
        for alternative in location["target"]["selector"]:
            inner = alternative.get("refinedBy")
            if inner is not None and inner["type"] == "PdfRegionSelector":
                out.append(dict(inner, x_location=location["id"]))
    return out


def covers(rect: dict, y_pt_top: float, y_pt_bottom: float) -> bool:
    """Does the rect span these two point offsets from the top of the page?"""
    return rect["y0"] <= y_pt_top / HEIGHT_PT and rect["y1"] >= y_pt_bottom / HEIGHT_PT


def minimal_document(frag: dict, *, page_count: int = 12) -> dict:
    """``frag`` wrapped in the smallest document the format accepts."""
    return {
        "schema": vocab.ANNOTATION_SCHEMA,
        "schema_version": vocab.ANNOTATION_SCHEMA_VERSION,
        "companion": {"name": "notes-companion", "repo": "example.invalid/org/notes-companion",
                      "commit": "0" * 40, "generated_at": "2026-01-01T00:00:00Z",
                      "generator": "overlay geometry (test)"},
        "sources": [{"id": SOURCE_ID, "kind": "pdf", "role": "audited",
                     "title": "Elementary Sets: Lecture Notes (synthetic)",
                     "sha256": "ab" * 32, "physical_page_count": page_count,
                     "printed_page_offset": 0,
                     "page_size": {"width_pt": WIDTH_PT, "height_pt": HEIGHT_PT}}],
        "declarations": [], "links": [],
        "items": frag["items"], "locations": frag["locations"],
        "provenance": frag["provenance"],
    }


class StubRenderer:
    """A :class:`geometry.CropRenderer` stand-in: no PDF, no pdftoppm, no files."""

    def __init__(self, available: bool = True):
        self.available = available
        self.calls: list[tuple] = []

    def render(self, page, rect, size_pt, stem):
        self.calls.append((page, dict(rect), size_pt, stem))
        if not self.available:
            return None
        return "%064x" % (page * 1000 + len(self.calls))


# ---------------------------------------------------------------------------
# the text layer
# ---------------------------------------------------------------------------

class TestTheTextLayer(unittest.TestCase):

    def test_parse_bbox_layout_reads_the_page_box_the_lines_and_their_words(self):
        pages = geometry.parse_bbox_layout(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(pages), 3)
        self.assertEqual((pages[0].width_pt, pages[0].height_pt), (WIDTH_PT, HEIGHT_PT))
        first = pages[0].lines[0]
        self.assertEqual(first.text, "2.1. Elementary sets")
        self.assertAlmostEqual(first.y0, 108 / HEIGHT_PT)
        self.assertAlmostEqual(first.y1, 120 / HEIGHT_PT)
        self.assertAlmostEqual(first.x0, 108 / WIDTH_PT)
        self.assertAlmostEqual(first.x1, 260 / WIDTH_PT)
        # the words of a line are joined with one space, and the folio survives
        self.assertEqual(pages[0].lines[-1].text, "4")
        self.assertEqual([len(p.lines) for p in pages], [14, 13, 8])

    def test_parse_bbox_layout_refuses_something_that_is_not_a_layout(self):
        with self.assertRaises(CfError) as caught:
            geometry.parse_bbox_layout("Definition 2.1.1. A set is elementary.\n")
        self.assertIn("-bbox-layout", str(caught.exception.hint))

    def test_number_pages_maps_an_extract_onto_the_pages_it_was_asked_for(self):
        pages = geometry.parse_bbox_layout(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(sorted(geometry.number_pages(pages, 4, 6)), [4, 5, 6])
        # the whole document instead of an extract: counted from page 1 and sliced
        whole = geometry.number_pages(pages, 2, 3)
        self.assertEqual(sorted(whole), [2, 3])
        self.assertEqual(whole[2].lines[0].text, "2.1. Elementary sets")
        self.assertEqual(whole[3].lines[1].text,
                         "and the finite union of boxes is elementary by definition.")
        # an extract is counted from --first wherever the range sits
        self.assertEqual(sorted(geometry.number_pages(pages, 7, 9)), [7, 8, 9])
        # neither an extract of that range nor a document long enough to hold it
        with self.assertRaises(CfError) as caught:
            geometry.number_pages(pages, 1, 5)
        self.assertIn("3 page(s)", str(caught.exception))

    def test_a_running_head_and_a_folio_are_measured_away_from_the_body(self):
        pages = geometry.parse_bbox_layout(FIXTURE.read_text(encoding="utf-8"))
        band = geometry.body_lines(pages[1])
        self.assertEqual(len(pages[1].lines) - len(band), 2)
        self.assertNotIn("2.1. Elementary sets", [ln.text for ln in band])
        self.assertNotIn("5", [ln.text for ln in band])
        self.assertAlmostEqual(band[0].y0, 108 / HEIGHT_PT)
        self.assertAlmostEqual(band[-1].y1, 372 / HEIGHT_PT)
        # a page with no furniture keeps every line it has
        kept = geometry.body_lines(pages[0])
        self.assertEqual(len(kept), len(pages[0].lines) - 1)   # the folio only
        self.assertEqual(kept[0].text, "2.1. Elementary sets")


# ---------------------------------------------------------------------------
# items
# ---------------------------------------------------------------------------

class TestItems(unittest.TestCase):

    def test_every_numbered_heading_of_the_section_becomes_one_item(self):
        items = by_id(fragment()["items"])
        self.assertEqual(sorted(items), ["definition-2.1.1", "example-2.1.6", "exercise-2.1.3",
                                         "lemma-2.1.5", "remark-2.1.4", "theorem-2.1.2"])
        definition = items["definition-2.1.1"]
        self.assertEqual(definition["kind"], "Definition")
        self.assertEqual(definition["number"], "2.1.1")
        self.assertEqual(definition["label"], "Definition 2.1.1 (Elementary set)")
        self.assertEqual(definition["title"], "Elementary set")
        self.assertEqual(definition["id_source"], "label")
        self.assertEqual(definition["form"], "numbered")
        self.assertEqual(definition["source"], SOURCE_ID)
        self.assertEqual(definition["section"], "2.1")
        # a heading with no parenthesis carries no title
        self.assertNotIn("title", items["exercise-2.1.3"])
        self.assertEqual(items["exercise-2.1.3"]["label"], "Exercise 2.1.3")

    def test_the_section_prefix_keeps_the_next_sections_items_out(self):
        self.assertIn("definition-2.2.1", by_id(fragment(section=None)["items"]))
        self.assertNotIn("definition-2.2.1", by_id(fragment()["items"]))
        # and without a section nothing claims one
        self.assertNotIn("section", by_id(fragment(section=None)["items"])["definition-2.2.1"])

    def test_a_heading_the_item_grammar_refuses_is_reported_and_never_an_item(self):
        xhtml = layout([
            (108, 108, 504, "Theorem 2.3.4.5. A label deeper than this source numbers."),
            (126, 108, 400, "It is reported, not truncated onto another item."),
            (162, 108, 504, "Lemma 2.3.5. A label the grammar accepts."),
            (180, 108, 400, "So this one is an item."),
            (216, 126, 480, "A paragraph closes the lemma off here."),
            (252, 108, 300, "Tail line, so the page can be measured."),
        ])
        rejects = []
        frag = fragment(xhtml=xhtml, first=1, last=1, section=None,
                        kinds=("Theorem", "Lemma"), rejects=rejects)
        self.assertEqual([r["id"] for r in frag["items"]], ["lemma-2.3.5"])
        self.assertEqual([(r.reason, r.context.get("segments")) for r in rejects],
                         [("too_many_segments", 4)])

    def test_two_headings_that_would_share_an_item_id_are_refused(self):
        xhtml = layout([
            (108, 108, 504, "Lemma 2.3.5. A label the grammar accepts."),
            (126, 108, 400, "Its statement."),
            (162, 108, 504, "Lemma 2.3.5. The same label, printed twice."),
            (180, 108, 400, "Its statement again."),
            (216, 126, 480, "A paragraph closes it off."),
            (252, 108, 300, "Tail line, so the page can be measured."),
        ])
        with self.assertRaises(CfError) as caught:
            fragment(xhtml=xhtml, first=1, last=1, section=None, kinds=("Lemma",))
        self.assertIn("lemma-2.3.5", str(caught.exception))


# ---------------------------------------------------------------------------
# where an item ends
# ---------------------------------------------------------------------------

class TestWhereAnItemEnds(unittest.TestCase):

    def test_an_item_ends_before_a_paragraph_set_off_by_a_gap_and_an_indent(self):
        rect = regions_of(fragment(), "definition-2.1.1")[0]["rect"]
        self.assertTrue(covers(rect, 136, 166), rect)          # both of its own lines
        self.assertLess(rect["y1"], 190 / HEIGHT_PT)           # not the paragraph's
        # the exact box: the column's left edge and the widest line inside, each
        # padded by XPAD, and the trim at the bottom of the last line it keeps
        self.assertEqual(rect, {"x0": 0.172471, "y0": 0.167717,
                                "x1": 0.827529, "y1": 0.213596})

    def test_an_item_ends_at_the_next_numbered_heading(self):
        rect = regions_of(fragment(), "remark-2.1.4")[0]["rect"]
        self.assertTrue(covers(rect, 162, 192), rect)
        self.assertLess(rect["y1"], 216 / HEIGHT_PT)           # its trailing paragraph
        self.assertLess(rect["y1"], 252 / HEIGHT_PT)           # the next heading

    def test_a_proof_line_ends_a_proof_kind_exactly_and_no_other_kind(self):
        frag = fragment()
        theorem = regions_of(frag, "theorem-2.1.2")[0]["rect"]
        # the display line is indented and set off by a gap, and the proof rule
        # is exact: the region keeps it and stops at "Proof."
        self.assertTrue(covers(theorem, 244, 284), theorem)
        self.assertLess(theorem["y1"], 290 / HEIGHT_PT)
        example = regions_of(frag, "example-2.1.6")
        self.assertEqual([r["page"] for r in example], [5, 6])
        # Example is not a proof kind, so the prose line opening "Proof." on the
        # next page ends nothing: the continuation runs past it to the section heading
        self.assertTrue(covers(example[1]["rect"], 108, 156), example[1])
        self.assertLess(example[1]["rect"]["y1"], 180 / HEIGHT_PT)

    def test_a_proof_kind_the_caller_names_is_the_one_that_is_used(self):
        # with Example made a proof kind, its continuation stops at the prose
        # "Proof." line it used to run past
        frag = fragment(proof_kinds=("Example",))
        example = regions_of(frag, "example-2.1.6")
        self.assertLess(example[1]["rect"]["y1"], 126 / HEIGHT_PT)
        # and Theorem, no longer a proof kind, no longer ends at its proof line,
        # so the paragraph rule reaches the indented display line first: that the
        # display survives in the test above is the proof rule being exact
        theorem = regions_of(frag, "theorem-2.1.2")[0]["rect"]
        self.assertLess(theorem["y1"], 272 / HEIGHT_PT)

    def test_an_item_that_runs_off_the_page_continues_at_the_top_of_the_next(self):
        regions = regions_of(fragment(), "exercise-2.1.3")
        self.assertEqual([r["page"] for r in regions], [4, 5])
        self.assertEqual([r["x_location"] for r in regions],
                         ["exercise-2.1.3@p004", "exercise-2.1.3@p005"])
        self.assertTrue(covers(regions[0]["rect"], 344, 420), regions[0])
        # the continuation starts at the top of the page's TEXT, below the
        # running head (which ends at 48pt), and ends at the next heading
        self.assertGreater(regions[1]["rect"]["y0"], 48 / HEIGHT_PT)
        self.assertAlmostEqual(regions[1]["rect"]["y0"], 108 / HEIGHT_PT - geometry.GAP,
                               places=6)
        self.assertTrue(covers(regions[1]["rect"], 108, 138), regions[1])
        self.assertLess(regions[1]["rect"]["y1"], 162 / HEIGHT_PT)

    def test_a_section_heading_ends_an_item_and_is_never_one(self):
        frag = fragment(section=None)
        self.assertNotIn("2.1", [r["number"] for r in frag["items"]])
        self.assertEqual([r["kind"] for r in frag["items"] if r["number"] == "2.2.1"],
                         ["Definition"])
        # the last item of the section ends above the next section's heading
        last = regions_of(frag, "example-2.1.6")[-1]["rect"]
        self.assertLess(last["y1"], 180 / HEIGHT_PT)


# ---------------------------------------------------------------------------
# the records
# ---------------------------------------------------------------------------

class TestTheRecords(unittest.TestCase):

    def test_a_region_is_a_normalised_top_left_rect_of_the_crop_box(self):
        region = regions_of(fragment(), "definition-2.1.1")[0]
        self.assertEqual(region["coordinate_space"],
                         {"origin": "top_left", "units": "normalized", "x_axis": "right",
                          "y_axis": "down", "page_box": "CropBox"})
        self.assertEqual(sorted(region["rect"]), ["x0", "x1", "y0", "y1"])
        for value in region["rect"].values():
            self.assertTrue(0.0 <= value <= 1.0, region)
        other = regions_of(fragment(page_box="MediaBox"), "definition-2.1.1")[0]
        self.assertEqual(other["coordinate_space"]["page_box"], "MediaBox")
        with self.assertRaises(CfError):
            fragment(page_box="WholePage")

    def test_the_margin_around_a_region_never_leaves_the_page_box(self):
        # a heading on the first line and text to the very bottom: the padded
        # box would run outside [0, 1], which normalised units may not do
        lines = [(2 + 18 * i, 108, 504, f"body line {i} of a full page") for i in range(43)]
        lines[0] = (2, 108, 504, "Lemma 1.2. A statement that fills the page.")
        lines.append((780, 108, 504, "the last line of the page"))
        frag = fragment(xhtml=layout(lines), first=1, last=1, section=None,
                        kinds=("Lemma",), numbering=(2, 2))
        rect = regions_of(frag, "lemma-1.2")[0]["rect"]
        self.assertEqual((rect["y0"], rect["y1"]), (0.0, 1.0))
        self.assertEqual(V.validate(V.canonicalize(minimal_document(frag))), [])

    def test_a_display_past_the_text_column_widens_its_region(self):
        frag = fragment()
        narrow = regions_of(frag, "definition-2.1.1")[0]["rect"]
        wide = regions_of(frag, "theorem-2.1.2")[0]["rect"]
        self.assertEqual(narrow["x0"], wide["x0"])             # one left edge
        self.assertEqual(narrow["x1"], 0.827529)               # the column, padded
        self.assertEqual(wide["x1"], 0.886353)                 # the display, padded
        self.assertLess(wide["x1"], geometry.X_MAX)

    def test_each_location_carries_a_page_fragment_and_a_highlight_fallback(self):
        location = by_id(fragment()["locations"])["definition-2.1.1@p004"]
        self.assertEqual(location["items"], ["definition-2.1.1"])
        self.assertEqual(location["pages"], {"first": 4, "basis": "physical"})
        self.assertEqual(location["target"]["source"], SOURCE_ID)
        alternatives = location["target"]["selector"]
        self.assertEqual([a["conformsTo"] for a in alternatives],
                         [vocab.CONFORMS_TO_PDF] * 2)
        self.assertEqual(alternatives[0]["value"], "page=4")
        self.assertEqual(alternatives[0]["refinedBy"]["type"], "PdfRegionSelector")
        # the fallback is the same box in points: left,right,top,bottom
        self.assertEqual(alternatives[1]["value"], "page=4&highlight=105,507,132,170")
        self.assertNotIn("refinedBy", alternatives[1])

    def test_the_signoff_records_the_method_the_caller_names_and_nothing_else(self):
        for method in geometry.SIGNOFF_METHODS:
            frag = fragment(method=method)
            signoffs = [loc["signoff"] for loc in frag["locations"]]
            self.assertEqual({s["method"] for s in signoffs}, {method})
            self.assertEqual({s["verifier"] for s in signoffs}, {VERIFIER})
            self.assertNotIn("evidence_sha256", signoffs[0])
            self.assertIs(frag["provenance"][0]["confirmed_by_human"],
                          method == "manual_visual")
        with self.assertRaises(CfError) as caught:
            fragment(method="visual")
        self.assertIn("no default", str(caught.exception.hint))
        with self.assertRaises(CfError):
            fragment(verifier_id="  ")

    def test_evidence_is_recorded_only_when_a_crop_was_rendered(self):
        renderer = StubRenderer()
        frag = fragment(evidence=renderer)
        signoff = by_id(frag["locations"])["definition-2.1.1@p004"]["signoff"]
        self.assertEqual(signoff["evidence_id"], "crop:definition-2.1.1@p004")
        self.assertRegex(signoff["evidence_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(renderer.calls), len(frag["locations"]))
        page, rect, size_pt, stem = renderer.calls[0]
        self.assertEqual((page, size_pt), (4, (WIDTH_PT, HEIGHT_PT)))
        self.assertEqual(stem, "004-definition-2_1_1-01")
        self.assertEqual(rect, regions_of(frag, "definition-2.1.1")[0]["rect"])
        # a renderer that writes nothing leaves the sign-off without evidence
        quiet = fragment(evidence=StubRenderer(available=False))
        self.assertEqual(by_id(quiet["locations"])["definition-2.1.1@p004"]["signoff"],
                         {"method": "agent_visual", "verifier": VERIFIER})

    def test_one_provenance_record_names_the_tool_the_source_and_the_signoff(self):
        record = fragment()["provenance"][0]
        self.assertEqual(record["id"], geometry.PROVENANCE_ID)
        self.assertEqual(record["actor"], "tool")
        self.assertEqual(record["method"], "layout_extraction")
        self.assertEqual(record["inputs"], [{"source": SOURCE_ID}])
        self.assertEqual(record["agents"], [VERIFIER])
        self.assertIn("pages 4-6", record["note"])
        self.assertIn("agent_visual", record["note"])
        self.assertNotIn("version", record["note"])
        versioned = fragment(extractor_version="24.02.0")["provenance"][0]
        self.assertIn("pdftotext 24.02.0", versioned["note"])

    def test_the_fragment_validates_inside_a_minimal_document(self):
        doc = V.canonicalize(minimal_document(fragment()))
        text = V.canonical_dumps(doc)
        report = V.check(doc, text=text, canonical=True)
        self.assertEqual(report.problems, [])
        self.assertEqual(report.warnings, [])
        self.assertEqual(len(doc["locations"]), 8)

    def test_a_region_past_the_sources_page_count_is_what_the_checker_refuses(self):
        # the producer stamps the physical page it was given; the document's own
        # source record is what says how many pages there are
        doc = V.canonicalize(minimal_document(fragment(), page_count=5))
        problems = V.validate(doc)
        self.assertTrue(any("page=6" in p or "page 6" in p for p in problems), problems)


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

class TestTheCommand(unittest.TestCase):

    def run_main(self, argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = geometry.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_main_writes_a_canonical_fragment_from_a_saved_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "fragment.json"
            code, out, err = self.run_main([
                "--bbox-xhtml", str(FIXTURE), "--first", str(FIRST), "--last", str(LAST),
                "--kinds", ",".join(KINDS), "--section", "2.1", "--source-id", SOURCE_ID,
                "--verifier-id", VERIFIER, "--method", "manual_visual",
                "--out", str(target)])
            self.assertEqual((code, err), (0, ""))
            self.assertIn("6 items, 8 regions", out)
            text = target.read_text(encoding="utf-8")
            frag = json.loads(text)
        self.assertEqual(text, V.canonical_dumps(V.canonicalize(frag)))
        self.assertEqual([r["id"] for r in frag["items"]][:2],
                         ["definition-2.1.1", "example-2.1.6"])
        self.assertEqual(frag["provenance"][0]["confirmed_by_human"], True)

    def test_main_refuses_the_pdf_when_pdftotext_is_not_on_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(geometry, "resolve_tool", return_value=None):
                code, _out, err = self.run_main([
                    "--pdf", str(FIXTURE), "--first", "1", "--last", "1",
                    "--kinds", "Lemma", "--source-id", SOURCE_ID,
                    "--verifier-id", VERIFIER, "--method", "agent_visual",
                    "--out", str(Path(tmp) / "fragment.json")])
        self.assertEqual(code, 2)
        self.assertIn("pdftotext is not on PATH", err)
        self.assertIn("--bbox-xhtml", err)

    def test_main_refuses_a_source_the_registry_knows_no_kinds_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _out, err = self.run_main([
                "--bbox-xhtml", str(FIXTURE), "--first", str(FIRST), "--last", str(LAST),
                "--source-id", "no-such-source", "--verifier-id", VERIFIER,
                "--method", "agent_visual", "--out", str(Path(tmp) / "fragment.json")])
        self.assertEqual(code, 2)
        self.assertIn("no item kinds", err)
        self.assertIn("--kinds", err)

    def test_main_refuses_an_evidence_dir_with_no_pdf_to_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _out, err = self.run_main([
                "--bbox-xhtml", str(FIXTURE), "--first", str(FIRST), "--last", str(LAST),
                "--kinds", "Lemma", "--source-id", SOURCE_ID, "--verifier-id", VERIFIER,
                "--method", "agent_visual", "--evidence-dir", str(Path(tmp) / "crops"),
                "--out", str(Path(tmp) / "fragment.json")])
            self.assertFalse((Path(tmp) / "crops").exists())
        self.assertEqual(code, 2)
        self.assertIn("--evidence-dir", err)

    def test_resolve_tool_follows_a_link_and_refuses_what_is_not_an_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real-tool"
            real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            real.chmod(0o755)
            (root / "linked-tool").symlink_to(real)
            (root / "plain-tool").write_text("not executable\n", encoding="utf-8")
            with unittest.mock.patch.dict("os.environ", {"PATH": str(root)}):
                self.assertEqual(geometry.resolve_tool("linked-tool"), str(real))
                self.assertIsNone(geometry.resolve_tool("plain-tool"))
                self.assertIsNone(geometry.resolve_tool("absent-tool"))


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
