"""Line ranges of a TeX source on the rows of its PDF: `annotations.export.texplace`.

New code, testing scripts/annotations/export/texplace.py (docs/exporters.md
section 9).  The manuscript and its rows are tests/_roadmap_fixture.py's, which
are synthetic; nothing here runs poppler, so every rule is tested on every
machine: the rows are handed to `Layout` as `pdftotext -bbox-layout` would give
them, and the ink is a function of the test's own.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _roadmap_fixture as fx                                      # noqa: E402
from annotations.export import texplace                            # noqa: E402
from annotations.export.texplace import Extent, Layout, Placer, Source  # noqa: E402


def top(index: int) -> float:
    return fx.row_top(index)


def bottom(index: int) -> float:
    return fx.row_top(index) + fx.HEIGHT


class FakeInk:
    """Ink where the test says: ``weight(y)`` pixels in the row at ``y`` points."""

    def __init__(self, weight, step: float = 0.25):
        self._weight, self._step = weight, step

    def weight(self, page: int, y: float) -> int:
        return self._weight(y)

    def step(self, page: int) -> float:
        return self._step


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.source = Source(fx.SOURCE)

    def test_detex_keeps_every_line_and_drops_what_prints_no_word(self):
        plain = texplace.detex(fx.SOURCE).split("\n")
        self.assertEqual(len(plain), len(fx.SOURCE.split("\n")))
        self.assertEqual(texplace.words_of(plain[2]), ["coloured", "pebbles"])   # the label's key is gone
        self.assertEqual(texplace.words_of(plain[7]), ["heaps"])                 # the environment's name too
        self.assertEqual(texplace.words_of(plain[15]), [])                       # a displayed formula
        self.assertEqual(texplace.words_of(plain[20]), [])                       # a comment

    def test_detex_blanks_inline_mathematics_and_keeps_an_accented_letter(self):
        plain = texplace.detex("Let $x \\in X$ be a point of Poincar\\'e's sphere, see \\cite[p.~3]{key}.\n")
        self.assertEqual(texplace.words_of(plain), ["let", "be", "point", "of", "poincare", "sphere", "see"])

    def test_words_are_compared_without_marks_or_case(self):
        self.assertEqual(texplace.words_of("Néron–Severi, ﬁnite; a b"), ["neron", "severi", "finite"])

    def test_every_word_knows_its_line(self):
        at = self.source.first_token_at(13)
        self.assertEqual(self.source.tokens[at], "weigh")
        self.assertEqual(self.source.token_lines[at], 13)
        self.assertEqual(self.source.first_token_at(15), self.source.first_token_at(18))

    def test_content_counts_the_lines_that_print(self):
        self.assertEqual(self.source.content(15, 17), 1)     # `\[`, the formula, `\]`
        self.assertEqual(self.source.content(11, 12), 0)     # an environment closing and one opening
        self.assertEqual(self.source.content(20, 21), 0)     # `\end{proof}` and a comment
        self.assertEqual(self.source.content(4, 6), 3)
        self.assertEqual(self.source.content(9, 3), 0)

    def test_a_label_gives_its_environment_and_a_sections_label_its_line(self):
        self.assertEqual(self.source.label_span("lem:heaps"), (8, 11))
        self.assertEqual(self.source.label_span("sec:pebbles"), (3, 3))
        self.assertIsNone(self.source.label_span("lem:absent"))

    def test_a_label_written_twice_is_no_locator(self):
        twice = Source(fx.SOURCE + "\\label{lem:heaps}\n")
        self.assertIsNone(twice.label_span("lem:heaps"))

    def test_the_section_of_a_line(self):
        self.assertEqual(self.source.section_at(2), "")
        self.assertEqual(self.source.section_at(9), "1")
        appendix = Source("\\section{One}\nx\n\\appendix\n\\section{Extra}\ny\n")
        self.assertEqual(appendix.section_at(2), "1")
        self.assertEqual(appendix.section_at(5), "")


class LayoutTests(unittest.TestCase):
    def test_the_rows_are_the_lines_without_the_folio(self):
        layout = Layout(fx.layout_pages())
        self.assertEqual(len(layout.rows), 11)
        self.assertEqual(layout.rows[-1].text, fx.ROWS[10])
        self.assertEqual((layout.left, layout.right), (100.0, 478.0))
        self.assertEqual(layout.page_rows, [list(range(11))])
        first = [w for w in layout.words if w.row == 5][:2]
        self.assertEqual([(w.token, w.x) for w in first], [("proof", 100.0), ("weigh", 142.0)])

    def test_lines_that_overlap_are_one_row(self):
        formula = (200.0, 212.0, [(150.0, 250.0, "E = mc")])
        number = (202.0, 211.0, [(460.0, 478.0, "(3)")])
        layout = Layout([(612.0, 792.0, [fx.line_of(180.0, "Before the display"), formula, number,
                                         fx.line_of(230.0, "after the display")])])
        self.assertEqual([row.text for row in layout.rows], ["Before the display", "E = mc (3)",
                                                             "after the display"])
        self.assertEqual((layout.rows[1].top, layout.rows[1].bottom), (200.0, 212.0))

    def test_running_heads_and_folios_are_not_rows(self):
        pages = []
        for number in range(5):
            pages.append((612.0, 792.0, [
                fx.line_of(60.0, f"Pebble Studies {number + 11}"),
                fx.line_of(100.0, f"The text of page {number} begins here"),
                fx.line_of(114.0, "and it ends with the word did" if number == 4 else "and it goes on"),
                fx.line_of(128.0, "did") if number == 4 else fx.line_of(700.0, ["iv", "v", "12", "13"][number]),
            ]))
        layout = Layout(pages)
        self.assertEqual([len(rows) for rows in layout.page_rows], [2, 2, 2, 2, 3])
        self.assertEqual(layout.rows[-1].text, "did")        # a word, not a roman numeral
        self.assertEqual(layout.rows[0].text, "The text of page 0 begins here")

    def test_from_bbox_reads_pages_lines_and_words(self):
        xml = "\n".join([
            '<page width="612.000000" height="792.000000">',
            '  <flow><block xMin="100" yMin="100" xMax="300" yMax="124">',
            '    <line xMin="100.000000" yMin="100.000000" xMax="220.000000" yMax="110.000000">',
            '      <word xMin="100.000000" yMin="100.000000" xMax="130.000000" yMax="110.000000">Heaps</word>',
            '      <word xMin="136.000000" yMin="100.000000" xMax="220.000000" yMax="110.000000">&amp; pebbles</word>',
            '    </line>',
            '    <line xMin="100.000000" yMin="114.000000" xMax="160.000000" yMax="124.000000">',
            '      <word xMin="100.000000" yMin="114.000000" xMax="160.000000" yMax="124.000000">again</word>',
            '    </line>',
            '  </block></flow>',
            '</page>',
        ])
        layout = Layout.from_bbox(xml)
        self.assertEqual(layout.sizes, [(612.0, 792.0)])
        self.assertEqual([row.text for row in layout.rows], ["Heaps & pebbles", "again"])
        self.assertEqual([w.token for w in layout.words], ["heaps", "pebbles", "again"])

    def test_rows_of_splits_an_extent_at_a_page_break(self):
        pages = [(612.0, 792.0, [fx.line_of(100.0 + 14 * i, f"first page row {w}") for i, w in
                                 enumerate(["alpha", "beta", "gamma"])]),
                 (612.0, 792.0, [fx.line_of(100.0 + 14 * i, f"second page row {w}") for i, w in
                                 enumerate(["delta", "epsilon"])])]
        layout = Layout(pages)
        self.assertEqual(layout.rows_of(1, 3), [(1, 1, 2), (2, 0, 0)])
        self.assertEqual(layout.rows_of(4, 4), [(2, 1, 1)])


class PlaceTests(unittest.TestCase):
    def setUp(self):
        self.layout = Layout(fx.layout_pages())
        self.placer = Placer(Source(fx.SOURCE), self.layout)

    def test_a_paragraph_and_an_environment(self):
        self.assertEqual(self.placer.place(4, 6), Extent(1, 2, "words"))
        self.assertEqual(self.placer.place(8, 11), Extent(3, 4, "words"))

    def test_a_shared_row_goes_to_the_range_that_has_more_of_it(self):
        # line 13 ends, and line 14 starts, in the right half of row 5
        self.assertEqual(self.placer.place(13, 13), Extent(5, 5, "words"))
        self.assertEqual(self.placer.place(14, 14), Extent(6, 6, "words"))
        # line 22 ends, and line 23 starts, in the left half of row 10
        self.assertEqual(self.placer.place(22, 22), Extent(9, 9, "words"))
        self.assertEqual(self.placer.place(23, 23), Extent(10, 10, "words"))

    def test_a_range_of_one_row_keeps_it_though_its_neighbour_does_too(self):
        # row 4 is all of line 10 and nothing else; row 3 holds lines 8-9, and line 9 alone has no other row
        self.assertEqual(self.placer.place(9, 9), Extent(3, 3, "words"))
        self.assertEqual(self.placer.place(10, 10), Extent(4, 4, "words"))

    def test_a_displayed_formula_goes_to_the_range_whose_lines_it_is(self):
        self.assertEqual(self.placer.place(13, 16), Extent(5, 7, "words"))
        self.assertEqual(self.placer.place(18, 19), Extent(8, 8, "words"))
        self.assertEqual(self.placer.place(13, 14), Extent(5, 6, "words"))
        self.assertEqual(self.placer.place(15, 19), Extent(7, 8, "words"))

    def test_a_range_of_mathematics_alone_is_placed_between_its_neighbours(self):
        self.assertEqual(self.placer.place(15, 17), Extent(7, 7, "neighbours"))

    def test_neighbouring_ranges_never_share_a_row_they_can_avoid(self):
        cuts = [(4, 6), (8, 11), (12, 14), (15, 17), (18, 19), (22, 23)]
        extents = [self.placer.place(a, b) for a, b in cuts]
        self.assertEqual([(e.first, e.last) for e in extents],
                         [(1, 2), (3, 4), (5, 6), (7, 7), (8, 8), (9, 10)])

    def test_lines_outside_the_file_are_not_placed(self):
        self.assertIsNone(self.placer.place(0, 3))
        self.assertIsNone(self.placer.place(23, 40))
        self.assertIsNone(self.placer.place(9, 8))

    def test_agreement_is_the_share_of_the_words_found(self):
        self.assertIsNone(self.placer.agreement)
        self.placer.place(4, 6)
        self.assertEqual(self.placer.agreement, 1.0)

    def test_a_pdf_of_other_words_matches_nothing(self):
        other = Layout(fx.layout_pages(["An unrelated page about harbours and tides",
                                        "with nothing of the manuscript on it at all"]))
        placer = Placer(Source(fx.SOURCE), other)
        self.assertIsNone(placer.place(4, 6))
        self.assertEqual(placer.agreement, 0.0)


class BoxTests(unittest.TestCase):
    def setUp(self):
        self.layout = Layout(fx.layout_pages())
        self.extents = {"a": [Extent(1, 2, "words")], "b": [Extent(1, 2, "words")],
                        "c": [Extent(1, 4, "words")], "d": [Extent(4, 5, "words")]}

    def test_edges_are_halfway_between_rows(self):
        found, report = texplace.boxes(self.layout, {"a": self.extents["a"]})
        (box,) = found["a"]
        self.assertEqual((box.page, box.first, box.last, box.key), (1, 1, 2, "p001-r001-002"))
        x, y, width, height = box.rect
        self.assertAlmostEqual(y * 792, (bottom(0) + top(1)) / 2, places=2)
        self.assertAlmostEqual((y + height) * 792, (bottom(2) + top(3)) / 2, places=2)
        self.assertAlmostEqual(x * 612, 100.0 - texplace.PAD_PT, places=2)
        self.assertAlmostEqual((x + width) * 612, 478.0 + texplace.PAD_PT, places=2)
        self.assertEqual(report, {"boxes": 1, "edges_checked": False, "edges_on_ink": [], "crossing": []})

    def test_a_pages_first_and_last_rows_get_a_lead(self):
        found, _ = texplace.boxes(self.layout, {"all": [Extent(0, 10, "words")]})
        _, y, _, height = found["all"][0].rect
        self.assertAlmostEqual(y * 792, top(0) - texplace.LEAD_PT, places=2)
        self.assertAlmostEqual((y + height) * 792, bottom(10) + texplace.LEAD_PT, places=2)

    def test_ranges_with_the_same_rows_share_one_box(self):
        found, report = texplace.boxes(self.layout, self.extents)
        self.assertIs(found["a"][0], found["b"][0])
        self.assertEqual(report["boxes"], 3)

    def test_a_box_that_encloses_another_is_drawn_wider(self):
        found, _ = texplace.boxes(self.layout, self.extents)
        inner, outer = found["a"][0].rect, found["c"][0].rect
        self.assertAlmostEqual((inner[0] - outer[0]) * 612, texplace.NEST_PT, places=2)
        self.assertAlmostEqual((outer[0] + outer[2] - inner[0] - inner[2]) * 612, texplace.NEST_PT, places=2)

    def test_boxes_that_overlap_without_nesting_are_reported(self):
        _, report = texplace.boxes(self.layout, self.extents)
        self.assertEqual(report["crossing"], ["p001-r001-004 and p001-r004-005"])

    def test_an_edge_on_ink_moves_to_the_nearest_blank_row(self):
        edge = (bottom(2) + top(3)) / 2                       # 140.0
        ink = FakeInk(lambda y: 4 if abs(y - edge) < 0.6 else 0)
        found, report = texplace.boxes(self.layout, {"a": self.extents["a"]}, ink)
        _, y, _, height = found["a"][0].rect
        self.assertAlmostEqual((y + height) * 792, edge - 0.75, places=2)
        self.assertEqual((report["edges_checked"], report["edges_on_ink"]), (True, []))

    def test_an_edge_with_no_blank_row_in_reach_takes_the_least_ink_and_is_named(self):
        edge = (bottom(2) + top(3)) / 2
        ink = FakeInk(lambda y: 0 if abs(y - edge) > 10 else (1 if abs(y - (edge + 1.0)) < 0.05 else 5))
        found, report = texplace.boxes(self.layout, {"a": self.extents["a"]}, ink)
        _, y, _, height = found["a"][0].rect
        self.assertAlmostEqual((y + height) * 792, edge + 1.0, places=2)
        self.assertEqual(report["edges_on_ink"], ["p001-r001-002"])
        self.assertEqual(found["a"][0].on_ink, 1)

    def test_an_enclosing_box_is_moved_off_the_inner_ones_edge_where_the_page_is_blank(self):
        found, _ = texplace.boxes(self.layout, self.extents, FakeInk(lambda y: 0))
        inner, outer = found["a"][0].rect, found["c"][0].rect
        self.assertAlmostEqual((inner[1] - outer[1]) * 792, texplace.NUDGE_PT, places=2)

    def test_an_extent_over_a_page_break_is_a_box_on_each_page(self):
        pages = [(612.0, 792.0, [fx.line_of(100.0 + 14 * i, f"first page row {w}") for i, w in
                                 enumerate(["alpha", "beta", "gamma"])]),
                 (612.0, 792.0, [fx.line_of(100.0 + 14 * i, f"second page row {w}") for i, w in
                                 enumerate(["delta", "epsilon"])])]
        layout = Layout(pages)
        found, report = texplace.boxes(layout, {"x": [Extent(1, 3, "words")]})
        self.assertEqual([box.key for box in found["x"]], ["p001-r001-002", "p002-r000-000"])
        first, second = found["x"]
        self.assertAlmostEqual((first.rect[1] + first.rect[3]) * 792, 100.0 + 28 + 10 + texplace.LEAD_PT, places=2)
        self.assertAlmostEqual(second.rect[1] * 792, 100.0 - texplace.LEAD_PT, places=2)
        self.assertEqual(report["boxes"], 2)


if __name__ == "__main__":
    unittest.main()
