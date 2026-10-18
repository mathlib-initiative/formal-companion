"""Locate L1: the web-source renderer, web-source identity, the Wikipedia client, sourcesets.

New code, for docs/proposals/wikipedia-locate.md sections 2.1, 2.2 and 3 (L1)
in the formats of references/locate-documents.md section 7.  Every page here is
synthetic (``Widget lemma``, page 1001, revision 2002), the recorded API
responses live in tests/data/locate/wiki/, and nothing touches the network:
``sources.wikinet.FixtureTransport`` replays them and ``CurlTransport`` is driven
through a fake runner.  The milestone L-M0 round trip is here too: a synthetic
page rendered, a quote cut from its layer found exactly once, and its passage
id stable.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main  # noqa: E402
from cflib import passages, registry, vocab  # noqa: E402
from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path before the format)
from cflib.errors import CfError  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from gate import locate_text  # noqa: E402
from sources import render_html as rh  # noqa: E402
from sources import websource, wikinet  # noqa: E402
from sources.sourceset import Sourceset, SourcesetError  # noqa: E402

FIXTURES = HERE / "data" / "locate" / "wiki"
SITE = "https://en.wikipedia.org"


def _scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def fixture_body(revision: int) -> str:
    data = json.loads((FIXTURES / f"parse-{revision}.json").read_text(encoding="utf-8"))
    return data["json"]["parse"]["text"]


def math(tex: str, *, block: bool = False, image: bool = True, mathml: bool = True) -> str:
    """A MediaWiki-shaped formula: MathML (with alttext and annotation) and a fallback image."""
    kind = "block" if block else "inline"
    alt = "{\\displaystyle " + tex + "}"
    inner = ""
    if mathml:
        inner += (f'<span class="mwe-math-mathml-{kind} mwe-math-mathml-a11y" style="display: none;">'
                  f'<math xmlns="http://www.w3.org/1998/Math/MathML" alttext="{alt}"><semantics>'
                  f'<mrow><mi>{tex[0]}</mi></mrow><annotation encoding="application/x-tex">{alt}'
                  f'</annotation></semantics></math></span>')
    if image:
        inner += f'<img src="x.svg" class="mwe-math-fallback-image-{kind}" alt="{alt}">'
    return f'<span class="mwe-math-element mwe-math-element-{kind}">{inner}</span>'


PAGE = (
    '<div class="mw-parser-output"><style>.x{color:red}</style>\n'
    '<p>In <a href="/wiki/W">widget theory</a>, the <b>widget lemma</b> says every finite '
    f'widget {math("G")} has a spanning gadget.<sup class="reference"><a>[1]</a></sup>\n</p>\n'
    '<div class="mw-heading mw-heading2"><h2 id="Statement">Statement</h2></div>\n'
    f'<div class="math_theorem"><strong class="theorem-name">Theorem</strong>&#160;&mdash; Every '
    f'finite widget {math("G")} of order {math("n")} has a spanning gadget.</div>\n'
    f'<dl><dd>{math("|S| = n", block=True)}</dd></dl>\n'
    '<h2><span class="mw-headline" id="Proof_sketch">Proof sketch</span>'
    '<span class="mw-editsection">[edit]</span></h2>\n'
    '<table class="wikitable"><tr><th>a</th><td><p>Cell with <b>bold</b> words in a table.</p>'
    '</td></tr></table>\n'
    '<p>Split the widget along its spine and count both halves carefully.</p>\n'
    '<div class="reflist"><ol class="references"><li>A fictional book.</li></ol></div></div>')


# ---------------------------------------------------------------------------
# the renderer
# ---------------------------------------------------------------------------

class TestCanonicalBody(unittest.TestCase):

    def test_a_formatversion_2_response_gives_its_text(self):
        raw = json.dumps({"parse": {"revid": 2002, "text": "<p>a<!-- b -->c</p>"}}).encode()
        self.assertEqual(rh.canonical_body(raw), "<p>ac</p>")

    def test_a_formatversion_1_response_and_plain_html(self):
        raw = json.dumps({"parse": {"text": {"*": "<p>x</p>"}}}).encode()
        self.assertEqual(rh.canonical_body(raw), "<p>x</p>")
        self.assertEqual(rh.canonical_body(b"<p>y<!--\nz-->w</p>"), "<p>yw</p>")
        self.assertEqual(rh.canonical_body("<p>q<!-- open".encode()), "<p>q")
        self.assertEqual(rh.canonical_body(b'{"not": "a parse"}'), '{"not": "a parse"}')

    def test_bytes_that_are_not_utf8_are_refused(self):
        with self.assertRaises(rh.RenderError):
            rh.canonical_body(b"\xff\xfe<p>x</p>")


class TestTheLayer(unittest.TestCase):

    def test_it_is_text_content_with_white_space_collapsed(self):
        r = rh.render("<p>  a\t\n b\f\rc&nbsp;d &amp; e\x0bf\u2009g\u202fh\u3000i </p>"
                      "<style>s { }</style><script>var s = 1;</script>"
                      "<template><p>t</p></template>")
        self.assertEqual(r.layer, "a b c d & e f g h i")

    def test_the_white_space_set_is_the_passage_normalisers(self):
        from formal_companion_annotations import ids
        self.assertEqual(rh.WHITE_SPACE, frozenset(ids._UNICODE_WHITE_SPACE))
        self.assertEqual(len(rh.WHITE_SPACE), 25)

    def test_markup_inside_formulas_and_citations_is_in_it(self):
        r = rh.render(PAGE)
        self.assertIn("G{\\displaystyle G}", r.layer)
        self.assertIn("[1]", r.layer)
        self.assertNotIn(".x{color:red}", r.layer)       # style text is no part of it
        self.assertIn("[edit]", r.layer)
        self.assertIn("A fictional book.", r.layer)

    def test_the_shas_are_of_the_utf8(self):
        r = rh.render(PAGE)
        self.assertEqual(r.layer_sha256, rh.sha256_text(r.layer))
        self.assertEqual(r.record()["renderer"], "web-html/2")
        self.assertEqual(rh.RENDERER, vocab.RENDERER_WEB_HTML)


class TestFormulas(unittest.TestCase):

    def test_spans_alttexts_and_the_head(self):
        r = rh.render(PAGE)
        self.assertEqual(len(r.formulas), 4)
        for formula in r.formulas:
            a, b = formula["span"]
            self.assertEqual(r.layer[a:b].strip(), r.layer[a:b])
            self.assertTrue(r.layer[a:b].endswith(formula["alttext"]))
        spans = [f["span"] for f in r.formulas]
        self.assertEqual(spans, sorted(spans))
        self.assertIn("finite widget ⟦G⟧ of order ⟦n⟧", r.head)
        self.assertIn("⟦|S| = n⟧", r.head)
        self.assertEqual(r.head, locate_text.HeadMap(r.layer, r.formulas).head)

    def test_the_alttext_falls_back_to_the_annotation_then_the_image(self):
        bare = ('<p>x <math><semantics><mi>y</mi><annotation encoding="application/x-tex">'
                'y^2</annotation></semantics></math> z</p>')
        self.assertEqual(rh.render(bare).formulas[0]["alttext"], "y^2")
        native = '<p>x <math alttext="{\\displaystyle k}"><mi>k</mi></math> z</p>'
        self.assertEqual(rh.render(native).formulas[0]["alttext"], "{\\displaystyle k}")
        image_only = f'<p>x {math("q", mathml=False)} z</p>'
        r = rh.render(image_only)
        self.assertEqual(r.formulas, [])
        self.assertEqual(r.counts["formulas_without_text"], 1)
        self.assertEqual(r.layer, "x z")

    def test_a_formula_without_alttext_is_text(self):
        r = rh.render('<p>x <span class="mwe-math-element">plain</span> z</p>')
        self.assertEqual(r.formulas, [])
        self.assertEqual(r.head, "x plain z")

    def test_two_renderings_of_one_formula_have_one_head(self):
        a = rh.render(f"<p>Let {math('G')} be finite and good.</p>")
        b = rh.render("<p>Let <math alttext='{\\displaystyle  G }'><mi>G</mi></math> be finite "
                      "and good.</p>")
        self.assertEqual(a.head, b.head)
        self.assertNotEqual(a.layer, b.layer)


class TestSectionsBlocksFinders(unittest.TestCase):

    def setUp(self):
        self.r = rh.render(PAGE)

    def test_sections_tile_the_layer_in_both_markups(self):
        s = self.r.sections
        self.assertEqual([x["path"] for x in s], [[], ["Statement"], ["Proof sketch"]])
        self.assertEqual([x["fragment"] for x in s], [None, "Statement", "Proof_sketch"])
        self.assertEqual(s[0]["span"][0], 0)
        self.assertEqual(s[-1]["span"][1], len(self.r.layer))
        for a, b in zip(s, s[1:]):
            self.assertEqual(a["span"][1], b["span"][0])
        self.assertTrue(self.r.layer[s[2]["span"][0]:].startswith("Proof sketch"))

    def test_nested_headings_carry_their_path(self):
        r = rh.render("<p>lead text here</p><h2 id='A'>A</h2><p>x</p><h3 id='B'>B</h3><p>y</p>"
                      "<h2 id='C'>C</h2><p>z</p>")
        self.assertEqual([x["path"] for x in r.sections], [[], ["A"], ["A", "B"], ["C"]])
        self.assertEqual([x["level"] for x in r.sections], [0, 2, 3, 2])

    def test_blocks(self):
        kinds = [(p["block"], p["math"]) for p in self.r.paragraphs]
        self.assertIn(("theorem_box", True), kinds)
        self.assertIn(("math", True), kinds)
        self.assertIn(("dd", True), kinds)
        lead = self.r.paragraphs[0]
        self.assertEqual((lead["block"], lead["formulas"], lead["section"]), ("p", 1, []))
        # the reference list is not a block; the plain proof paragraph is, without math
        texts = [self.r.layer[slice(*p["span"])] for p in self.r.paragraphs]
        self.assertNotIn("A fictional book.", texts)
        proof = next(p for p, t in zip(self.r.paragraphs, texts) if t.startswith("Split"))
        self.assertFalse(proof["math"])

    def test_the_three_finders(self):
        f = {(x["finder"], x["term"]) for x in self.r.finders}
        self.assertEqual(f, {("bold_definition", "widget lemma"), ("lead", None),
                             ("theorem_box", "Theorem")})
        for row in self.r.finders:
            self.assertEqual(row["start"], row["span"][0])
        # the bold term in a table cell is not a definition
        self.assertFalse(any(x["term"] == "bold" for x in self.r.finders))

    def test_a_finder_start_that_is_no_word_start_is_dropped_and_counted(self):
        r = rh.render("<div>tail</div><p><b>glued</b> to the word before it here</p>")
        self.assertEqual(r.layer, "tailglued to the word before it here")
        self.assertEqual(r.finders, [])
        self.assertEqual(r.counts["finders_dropped"], 2)

    def test_the_lead_finder_skips_short_and_table_paragraphs(self):
        r = rh.render("<table><tr><td><p>one two three four five six</p></td></tr></table>\n"
                      "<p>too short</p>\n<p>this one has enough words in it</p>\n<h2>H</h2>\n"
                      "<p>after the heading there are words</p>")
        lead = [x for x in r.finders if x["finder"] == "lead"]
        self.assertEqual(len(lead), 1)
        self.assertTrue(r.layer[lead[0]["start"]:].startswith("this one"))

    def test_finder_starts_key_passages_in_both_implementations(self):
        hm = self.r.head_map()
        starts = sorted({hm.to_head(x["start"]) for x in self.r.finders})
        theirs = passages.assign_passage_ids(self.r.head, starts)
        mine = locate_text.assign_ids(self.r.head, starts)
        self.assertEqual(mine, [(h.start, h.hex, h.head_words) for h in theirs])


class TestTheReadingCopy(unittest.TestCase):

    def setUp(self):
        self.r = rh.render(PAGE)

    def test_what_it_keeps_and_drops(self):
        t = self.r.text
        self.assertIn("finite widget $G$ has a spanning gadget.", t)
        self.assertIn("## Statement", t)
        self.assertIn("## Proof sketch\n", t)
        self.assertIn("\n$|S| = n$\n", t)
        for gone in ("[1]", "[edit]", "color:red", "A fictional book", "displaystyle"):
            self.assertNotIn(gone, t)
        self.assertIn("a | Cell with bold words in a table.", t)
        self.assertTrue(t.endswith("\n") and not t.endswith("\n\n"))

    def test_the_map(self):
        r = self.r
        for t0, t1, l0, l1, kind in r.segments:
            if kind == rh.SEGMENT_TEXT:
                self.assertEqual(r.text[t0:t1], r.layer[l0:l1])
            else:
                self.assertEqual(kind, rh.SEGMENT_FORMULA)
                self.assertTrue(r.text[t0:t1].startswith("$"))
        at = r.text.index("spanning gadget.")
        self.assertEqual(r.layer[r.text_to_layer(at):].split(".")[0], "spanning gadget")
        self.assertEqual(r.layer_to_text(r.text_to_layer(at)), at)
        self.assertIsNone(r.text_to_layer(r.text.index("## Statement")))
        formula = r.formulas[0]["span"]
        self.assertEqual(r.text[r.layer_to_text(formula[0])], "$")
        self.assertIsNone(r.layer_to_text(formula[0] + 1))
        self.assertIsNone(r.layer_to_text(r.layer.index("[1]")))
        self.assertEqual(r.layer_map()["schema"], "cf-layer-map/2")


    def test_the_map_reads_back_from_its_file(self):
        out = _scratch(self)
        rh.write_rendering(out, self.r)
        m = rh.LayerMap.read(out)
        for i in range(len(self.r.text)):
            self.assertEqual(m.text_to_layer(i), self.r.text_to_layer(i))
        for i in range(len(self.r.layer)):
            self.assertEqual(m.layer_to_text(i), self.r.layer_to_text(i))


class TestRobustness(unittest.TestCase):

    def test_malformed_markup_parses_and_is_deterministic(self):
        html = ("<div><p>One <b>term</b> here<p>Two <i>open <b>bold</i> mixed</b> "
                "<ul><li>a<li>b</ul><table><tr><td>x<td>y<tr><td>z</table><br/>end<span/>"
                "<link rel=x><meta x=y></div></span></p><img alt=q>")
        a, b = rh.render(html), rh.render(html)
        self.assertEqual(a.files(), b.files())
        self.assertEqual(a.layer, "One term hereTwo open bold mixed abxyzend")
        self.assertIn("- a\n\n- b", a.text)
        self.assertIn("x | y", a.text)

    def test_the_fixture_pages_render(self):
        for revision in (2002, 2003, 2004):
            with self.subTest(revision=revision):
                r = rh.render(fixture_body(revision))
                self.assertTrue(r.layer)
                locate_text.HeadMap(r.layer, r.formulas)
        widget = rh.render(fixture_body(2002))
        self.assertEqual({x["finder"] for x in widget.finders},
                         {"bold_definition", "lead", "theorem_box"})
        self.assertNotIn("NewPP", widget.layer)

    def test_a_body_must_be_text(self):
        with self.assertRaises(rh.RenderError):
            rh.render(b"<p>x</p>")


class TestTheFiles(unittest.TestCase):

    def test_write_rendering_writes_the_contract_files(self):
        out = _scratch(self)
        r = rh.render(PAGE)
        record = rh.write_rendering(out, r)
        self.assertEqual(sorted(p.name for p in out.iterdir()), sorted(rh.RENDER_FILES))
        self.assertEqual(record["layer_sha256"],
                         websource_sha(out / "layer.txt"))
        self.assertEqual(record["html_sha256"], websource_sha(out / "body.html"))
        self.assertEqual(record["head_sha256"], websource_sha(out / "head.txt"))
        self.assertEqual(json.loads((out / "render.json").read_text()), record)
        layer, formulas = rh.read_layer(out)
        self.assertEqual((layer, formulas), (r.layer, r.formulas))
        self.assertEqual(rh.head_map(out).head, (out / "head.txt").read_text(encoding="utf-8"))
        before = {p.name: p.stat().st_mtime_ns for p in out.iterdir()}
        rh.write_rendering(out, rh.render(PAGE))
        self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in out.iterdir()})


def websource_sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestTheMilestoneRoundTrip(unittest.TestCase):
    """L-M0: a synthetic page round-trips prepare -> layer quote -> passage id."""

    def test_a_quote_cut_from_the_layer_keys_one_passage(self):
        out = _scratch(self)
        raw = (FIXTURES / "parse-2002.json").read_bytes()
        data = json.loads(raw)["json"]
        body = rh.canonical_body(json.dumps(data).encode("utf-8"))
        r = rh.render(body)
        rh.write_rendering(out, r)
        box = next(x for x in r.finders if x["finder"] == "theorem_box")
        layer, formulas = rh.read_layer(out)
        hm = locate_text.HeadMap(layer, formulas)
        words = layer[box["start"]:].split(" ")[:12]
        exact = " ".join(words)
        prefix = layer[max(0, box["start"] - 20):box["start"]]
        self.assertEqual(locate_text.find_quote(layer, exact, prefix=prefix), [box["start"]])
        starts = sorted({hm.to_head(x["start"]) for x in r.finders})
        ids = {s: (h, n) for s, h, n in locate_text.assign_ids(hm.head, starts)}
        hexdigit, head_words = ids[hm.to_head(box["start"])]
        self.assertEqual(locate_text.hex_at(hm.head, hm.to_head(box["start"]), head_words),
                         hexdigit)
        again = rh.render(body)
        self.assertEqual(again.head, r.head)
        self.assertRegex(f"In-text #{hexdigit}", r"^In-text #[0-9a-f]{8}$")


#: a page in the shape MediaWiki's parse output really has (TemplateStyles
#: blocks and their deduplicated links, a hatnote, an anchor template inside a
#: bold term, a page script, a template element, NBSP, thin, narrow and
#: ideographic spaces, a {{math}} formula, a MathML one, citation markers);
#: every word of it is made up
MW_PAGE = (
    '<div class="mw-content-ltr mw-parser-output" lang="en" dir="ltr">'
    '<style data-mw-deduplicate="TemplateStyles:r100">.mw-parser-output .hatnote'
    '{font-style:italic}.mw-parser-output div.hatnote{padding-left:1.6em}</style>'
    '<div role="note" class="hatnote navigation-not-searchable">For other uses, see '
    '<a href="/wiki/Widget_(disambiguation)">Widget (disambiguation)</a>.</div>\n'
    '<p class="mw-empty-elt">\n</p>\n'
    '<p>In <a href="/wiki/W">widget theory</a>, a <b><style data-mw-deduplicate='
    '"TemplateStyles:r200">.mw-parser-output .vanchor>:target~.vanchor-text'
    '{background-color:#b1d2ff}</style><span class="vanchor"><span id="Widget_group">'
    '</span><span class="vanchor-text">widget&#160;group</span></span></b> is a group '
    '<span class="texhtml"><i>G</i>&#8201;=&#8201;<i>H</i></span> with a spanning&#8239;'
    'gadget.<sup id="cite_ref-1" class="reference"><a href="#cite_note-1">&#91;1&#93;</a>'
    '</sup>\n</p>\n'
    '<script>(RLQ=window.RLQ||[]).push(function(){mw.config.set({"wgBackendResponseTime"'
    ':12});});</script>\n'
    '<link rel="mw-deduplicated-inline-style" href="mw-data:TemplateStyles:r100">'
    '<template><p>A widget that is never shown.</p></template>\n'
    '<h2 id="Statement">Statement</h2>\n'
    f'<p>Every widget group {math("G")} of order&#12288;{math("n")} has a spanning gadget '
    '&amp; a&nbsp;spine.</p>\n'
    '<table class="infobox" style="width:22em"><tbody><tr><th>Field</th>'
    '<td>Widget&#160;theory</td></tr></tbody></table>\n'
    '<div class="reflist"><style data-mw-deduplicate="TemplateStyles:r300">'
    '.mw-parser-output .reflist{margin-bottom:0.5em}</style><ol class="references">'
    '<li id="cite_note-1">A made-up book.</li></ol></div></div>')

#: what MW_PAGE's layer is, written out by hand from the Literature Links rule
MW_PAGE_LAYER = (
    "For other uses, see Widget (disambiguation). In widget theory, a widget group is a "
    "group G = H with a spanning gadget.[1] Statement Every widget group G{\\displaystyle G}"
    " of order n{\\displaystyle n} has a spanning gadget & a spine. FieldWidget theory "
    "A made-up book.")


def literature_links_layer(html: str) -> str:
    """An independent statement of the Literature Links text layer, for the tests only.

    A scan of the markup, sharing nothing with the renderer: comments and tags
    are skipped, the content of ``script``, ``style`` and ``template`` too,
    ``&...;`` references are decoded, and each run of ``White_Space`` becomes
    one space, the ends stripped.
    """
    import html as htmlmod
    from formal_companion_annotations import ids
    white = set(ids._UNICODE_WHITE_SPACE)
    out: list[str] = []
    i, n = 0, len(html)
    lower = html.lower()
    while i < n:
        if html.startswith("<!--", i):
            close = html.find("-->", i + 4)
            i = n if close < 0 else close + 3
            continue
        tag = re.match(r"<(/?)([A-Za-z][A-Za-z0-9:-]*)", html[i:i + 64])
        if tag:
            end = html.find(">", i)
            end = n if end < 0 else end + 1
            name = tag.group(2).lower()
            if not tag.group(1) and name in ("script", "style", "template"):
                close = lower.find("</" + name, end)
                gt = html.find(">", close) if close >= 0 else -1
                i = n if gt < 0 else gt + 1
            else:
                i = end
            continue
        if html[i] == "&":
            semi = html.find(";", i, min(i + 16, n))
            if semi > 0 and htmlmod.unescape(html[i:semi + 1]) != html[i:semi + 1]:
                out.append(htmlmod.unescape(html[i:semi + 1]))
                i = semi + 1
                continue
        out.append(html[i])
        i += 1
    text = "".join(" " if ch in white else ch for ch in "".join(out))
    return " ".join(part for part in text.split(" ") if part)


class TestTheLayerIsTheLiteratureLinksLayer(unittest.TestCase):
    """S1: the layer is the consumer's text layer -- no style, script or template text,
    every White_Space run one space -- on pages shaped like MediaWiki's real output."""

    def test_a_real_shaped_page(self):
        r = rh.render(rh.canonical_body(MW_PAGE.encode("utf-8")))
        self.assertEqual(r.layer, MW_PAGE_LAYER)
        self.assertEqual(r.layer, literature_links_layer(MW_PAGE))
        for css in (".mw-parser-output", "font-style", "RLQ", "never shown", "\xa0",
                    " ", " ", "　"):
            self.assertNotIn(css, r.layer)

    def test_every_page_the_suite_renders_agrees_with_the_independent_rule(self):
        pages = {"PAGE": PAGE, "MW_PAGE": MW_PAGE,
                 **{f"parse-{rev}": fixture_body(rev) for rev in (2002, 2003, 2004)},
                 "inline style in a heading": '<h2 id="X">Case <style>.t{x:y}</style>'
                                              '<span class="texhtml">n</span></h2><p>a</p>',
                 "script between words": "<p>one<script>two()</script> three</p>",
                 "spaces of every kind": "<p>a b c d e f g"
                                         " h\x85i\x0bj</p>"}
        for name, html in pages.items():
            with self.subTest(page=name):
                body = rh.canonical_body(html.encode("utf-8"))
                self.assertEqual(rh.render(body).layer, literature_links_layer(body))

    def test_a_bold_term_holding_a_style_is_the_term_and_its_head_has_no_css(self):
        r = rh.render(rh.canonical_body(MW_PAGE.encode("utf-8")))
        bold = next(f for f in r.finders if f["finder"] == "bold_definition")
        self.assertEqual(bold["term"], "widget group")
        hm = r.head_map()
        head = locate_text.head_at(hm.head, hm.to_head(bold["start"]), 12)
        self.assertEqual(head[:4], ["In", "widget", "theory,", "a"])
        self.assertFalse(any("{" in w or "mw-parser" in w for w in head))

    def test_the_reading_copy_maps_onto_the_new_layer(self):
        r = rh.render(rh.canonical_body(MW_PAGE.encode("utf-8")))
        for t0, t1, l0, l1, kind in r.segments:
            if kind == rh.SEGMENT_TEXT:
                self.assertEqual(r.text[t0:t1], r.layer[l0:l1])
        self.assertNotIn(".mw-parser-output", r.text)
        self.assertIn("a widget group is a group G = H with a spanning gadget.", r.text)


@unittest.skipUnless(os.environ.get("CF_WIKILEAN_LAYER"),
                     "set CF_WIKILEAN_LAYER to a directory of <name>.body.html + "
                     "<name>.layer.txt pairs from $WIKILEAN's text_layer")
class TestParityWithTheConsumersTextLayer(unittest.TestCase):
    """The layer is a port of $WIKILEAN's site/anchor_resolve.text_layer; this pins both."""

    def test_every_pair_agrees(self):
        directory = Path(os.environ["CF_WIKILEAN_LAYER"]).expanduser()
        pairs = sorted(directory.glob("*.body.html"))
        self.assertTrue(pairs, f"no *.body.html in {directory}")
        for body in pairs:
            expected = body.with_name(body.name[:-len(".body.html")] + ".layer.txt")
            with self.subTest(page=body.name):
                self.assertEqual(rh.render(rh.canonical_body(body.read_bytes())).layer,
                                 expected.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# web-source identity
# ---------------------------------------------------------------------------

class TestWebSourceIdentity(unittest.TestCase):

    def setUp(self):
        self.fam = websource.family("enwiki")

    def test_the_fixed_parameters(self):
        self.assertEqual(websource.parse_params(2002)["oldid"], "2002")
        self.assertNotIn("oldid", websource.PARSE_PARAMS)
        self.assertEqual(websource.PARSE_PARAMS["formatversion"], "2")
        blob = json.dumps(websource.PARSE_PARAMS, sort_keys=True, separators=(",", ":"))
        import hashlib
        self.assertEqual(websource.params_sha256(), hashlib.sha256(blob.encode()).hexdigest())
        self.assertEqual(websource.fetch_block()["api"], "action=parse")
        with self.assertRaises(CfError):
            websource.parse_params(0)

    def test_an_entry_and_its_problems(self):
        e = websource.entry(self.fam, 1001, 2002, title="Widget lemma", wikidata="Q99999001",
                            assessment={"class": "B", "importance": "high"})
        self.assertEqual(e["source_id"], "enwiki-1001-2002")
        self.assertEqual(e["licence_class"], "open_share_alike")
        self.assertEqual(tuple(e), websource.ENTRY_FIELDS)
        self.assertEqual(websource.entry_problems(e), [])
        for key, value, needle in (("source_id", "enwiki-1001-2003", "does not say"),
                                   ("raw_sha256", "abc", "64 lowercase hex"),
                                   ("origin", "guessed", "origin"),
                                   ("wikidata", "99", "not a QID"),
                                   ("renderer", "web-html/1", "no layer_sha256")):
            bad = dict(e, **{key: value})
            with self.subTest(key=key):
                self.assertTrue(any(needle in p for p in websource.entry_problems(bad)),
                                websource.entry_problems(bad))
        with self.assertRaises(CfError):
            websource.entry(self.fam, 1001, 2002, colour="red")

    def test_the_permalink_is_built_from_ids_not_the_title(self):
        url = websource.revision_url(self.fam, 1001, 2002)
        self.assertIn("oldid=2002", url)
        self.assertIn("1001", url)
        self.assertEqual(websource.api_url(self.fam), "https://en.wikipedia.org/w/api.php")

    def test_an_unknown_family_is_refused(self):
        with self.assertRaises(CfError):
            websource.family("nowiki")


# ---------------------------------------------------------------------------
# the Wikipedia client
# ---------------------------------------------------------------------------

class TestTheRecordedTransport(unittest.TestCase):

    def setUp(self):
        self.t = wikinet.FixtureTransport(FIXTURES)

    def test_parse_and_its_checks(self):
        body = wikinet.fetch_parse(self.t, SITE, 2002)
        self.assertIn(b"widget lemma", body)
        with self.assertRaises(wikinet.WikiError):
            wikinet.fetch_parse(self.t, SITE, 9999)

    def test_projectpages_follows_the_continuation_and_keeps_articles(self):
        pages = list(wikinet.project_pages(self.t, SITE, "Mathematics"))
        self.assertEqual([p["pageid"] for p in pages], [1001, 1002, 1003])
        self.assertEqual(pages[0]["assessment"], {"class": "B", "importance": "high"})
        self.assertEqual(len(self.t.requests), 2)

    def test_page_infos(self):
        infos = wikinet.page_infos(self.t, SITE, [1003, 1001, 1002])
        self.assertEqual(infos[1001], {"revision": 2002, "title": "Widget lemma",
                                       "wikidata": "Q99999001", "redirect": False,
                                       "disambiguation": False})
        self.assertIsNone(infos[1003]["wikidata"])

    def test_maxlag_does_not_key_a_recording(self):
        q = {"action": "parse", "oldid": "1"}
        self.assertEqual(wikinet.request_key(q), wikinet.request_key({**q, "maxlag": "5"}))
        self.assertEqual(wikinet.canonical_query({"b": "2", "a": "1"}), "a=1&b=2")


class _Fake:
    """A transport that answers from a function and counts."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[dict] = []

    def get(self, site, params):
        self.calls.append(dict(params))
        return json.dumps(self.answer(params)).encode()


class TestTheClientLogic(unittest.TestCase):

    def test_page_infos_go_fifty_a_request(self):
        def answer(params):
            ids = [int(x) for x in params["pageids"].split("|")]
            return {"query": {"pages": [{"pageid": i, "revisions": [{"revid": i + 1}]}
                                        for i in ids if i != 7] + [{"pageid": 7, "missing": True}]}}
        fake = _Fake(answer)
        infos = wikinet.page_infos(fake, SITE, range(1, 121))
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(len(infos), 119)
        self.assertNotIn(7, infos)

    def test_an_api_error_and_a_wrong_revision_are_refused(self):
        with self.assertRaises(wikinet.WikiError):
            wikinet.fetch_parse(_Fake(lambda p: {"error": {"code": "nosuchrevid"}}), SITE, 5)
        with self.assertRaises(wikinet.WikiError):
            wikinet.fetch_parse(_Fake(lambda p: {"parse": {"revid": 6, "text": "x"}}), SITE, 5)

    def test_a_repeating_continuation_is_refused(self):
        fake = _Fake(lambda p: {"continue": {"wppcontinue": "1"},
                                "query": {"projects": {"P": []}}})
        with self.assertRaises(wikinet.WikiError):
            list(wikinet.project_pages(fake, SITE, "P"))


class _Run:
    """A fake subprocess.run for curl: answers from a script of (exit, status, body)."""

    def __init__(self, script):
        self.script = list(script)
        self.argvs: list[list[str]] = []

    def __call__(self, argv, capture_output=True, text=True):
        self.argvs.append(list(argv))
        code, status, body = self.script.pop(0)
        Path(argv[argv.index("-o") + 1]).write_bytes(body)

        class P:
            returncode = code
            stdout = status
            stderr = "" if code == 0 else "curl: (22) failed"
        return P()


class TestCurlTransport(unittest.TestCase):

    def transport(self, script):
        sleeps: list[float] = []
        now = [0.0]

        def sleep(s):
            sleeps.append(s)
            now[0] += s

        run = _Run(script)
        t = wikinet.CurlTransport(runner=run, sleep=sleep, clock=lambda: now[0], exe="curl")
        return t, run, sleeps

    def test_the_argv_is_bounded_and_names_nobody(self):
        t, _run, _s = self.transport([])
        argv = t.argv("https://en.wikipedia.org/w/api.php?x=1", "/tmp/out")
        for flag in ("--fail", "--proto", "=https", "--proto-redir", "--max-filesize",
                     "--max-time", "-A"):
            self.assertIn(flag, argv)
        ua = argv[argv.index("-A") + 1]
        self.assertTrue(ua.startswith("formal-companion-locate/"))
        self.assertNotIn("@", ua)

    def test_a_503_then_a_lag_then_success_with_politeness(self):
        ok = json.dumps({"parse": {"revid": 3, "text": "<p>x</p>"}}).encode()
        lag = json.dumps({"error": {"code": "maxlag"}}).encode()
        t, run, sleeps = self.transport([(22, "503", b""), (0, "200", lag), (0, "200", ok)])
        self.assertEqual(wikinet.fetch_parse(t, SITE, 3), ok)
        self.assertEqual(len(run.argvs), 3)
        self.assertIn("maxlag=5", run.argvs[0][-1])
        self.assertTrue(all(s > 0 for s in sleeps))

    def test_a_404_is_not_retried(self):
        t, run, _s = self.transport([(22, "404", b""), (0, "200", b"{}")])
        with self.assertRaises(wikinet.WikiError):
            wikinet.fetch_parse(t, SITE, 3)
        self.assertEqual(len(run.argvs), 1)

    def test_it_waits_between_requests(self):
        ok = json.dumps({"parse": {"revid": 3, "text": "x"}}).encode()
        t, _run, sleeps = self.transport([(0, "200", ok), (0, "200", ok)])
        wikinet.fetch_parse(t, SITE, 3)
        wikinet.fetch_parse(t, SITE, 3)
        self.assertEqual(sleeps, [wikinet.MIN_INTERVAL])

    def test_only_https_sites(self):
        with self.assertRaises(wikinet.WikiError):
            wikinet.request_url("http://en.wikipedia.org", {"a": "1"})


class TestOneModuleTalksToWikipedia(unittest.TestCase):
    """CLAUDE.md section 3: the Wikipedia fetcher is one named exception, in one module."""

    #: anything that can open a connection or run curl
    NETWORK = re.compile(
        r"\burllib\.request\b|from\s+urllib\s+import\s+[^\n]*\brequest\b|\bhttp\.client\b|"
        r"from\s+http\s+import\s+[^\n]*\bclient\b|^\s*(?:import|from)\s+(?:requests|httpx|urllib3)\b|"
        r"\bsocket\b|[\"']curl[\"']|shutil\.which\(\s*[\"']curl", re.M)
    #: anything that names Wikipedia or builds a request to a web-source family
    WIKI = re.compile(r"wikipedia\.org|wikidata\.org|api\.php|\bapi_url\(|\brequest_url\(|"
                      r"\bwikinet\b|\bwebsource\b|\.site\b")

    def test_no_other_module_fetches_from_wikipedia(self):
        offenders = []
        for path in sorted(SCRIPTS.rglob("*.py")):
            rel = path.relative_to(SCRIPTS).as_posix()
            if rel == "sources/wikinet.py":
                continue
            text = path.read_text(encoding="utf-8")
            if self.WIKI.search(text) and self.NETWORK.search(text):
                offenders.append(rel)
            if "CurlTransport(" in text and rel != "cfcmd/sourceset.py":
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_the_check_sees_the_spellings_it_is_written_for(self):
        for sample in ("from urllib import request\nrequest.urlopen(websource.api_url(f))",
                       "import urllib.request\nurllib.request.urlopen(fam.site)",
                       "subprocess.run(['curl', wikinet.request_url(s, p)])",
                       "import requests\nrequests.get(websource.api_url(fam))"):
            with self.subTest(sample=sample):
                self.assertTrue(self.WIKI.search(sample) and self.NETWORK.search(sample))


# ---------------------------------------------------------------------------
# review fixes: the renderer
# ---------------------------------------------------------------------------

class TestReviewFixesRenderer(unittest.TestCase):
    """Regression tests for the adversarial review of the renderer (ids in the names)."""

    def test_00_a_formula_maps_only_as_a_whole_whatever_its_lengths(self):
        for html in ('<p>x <math alttext="a"><mi>a</mi><mo>+</mo><mi>b</mi></math> y</p>',
                     '<p>Let <math alttext="x^{2}"><semantics><msup><mi>x</mi><mn>2</mn>'
                     '</msup><annotation encoding="application/x-tex">x^{2}</annotation>'
                     '</semantics></math> be.</p>'):
            with self.subTest(html=html):
                r = rh.render(html)
                (a, b), = [f["span"] for f in r.formulas]
                seg = next(s for s in r.segments if s[2] == a)
                self.assertEqual(seg[4], rh.SEGMENT_FORMULA)
                self.assertEqual(r.text[r.layer_to_text(a)], "$")
                for inside in range(a + 1, b):
                    self.assertIsNone(r.layer_to_text(inside))
                self.assertIsNone(r.text_to_layer(seg[0] + 1))
                m = rh.LayerMap(r.segments)
                self.assertIsNone(m.layer_to_text(a + 1))

    def test_00_an_old_layer_map_is_refused(self):
        out = _scratch(self)
        (out / "layer_map.json").write_text('{"schema": "cf-layer-map/1", "segments": []}')
        with self.assertRaises(rh.RenderError):
            rh.LayerMap.read(out)

    def test_01_blocks_and_finders_skip_dropped_text(self):
        css = ('<style data-mw-deduplicate="TemplateStyles:r1">.mw-parser-output .texhtml'
               '{font-family:"Nimbus Roman No9 L","Times New Roman",Times,serif}</style>')
        # web-html/2: style text is no part of the layer, so it glues nothing
        styled = rh.render(f'<div><p>{css}<span class="texhtml"><i>G</i></span> is called a '
                           f'<b>widget group</b> when it has a spanning gadget.</p></div>')
        self.assertTrue(styled.layer.startswith("G is called"))
        self.assertEqual({x["finder"] for x in styled.finders}, {"bold_definition", "lead"})
        # a citation marker is in the layer but outside the kept text: glued, dropped
        cite = '<sup class="reference"><a href="#c1">[1]</a></sup>'
        glued = rh.render(f'<div><p>{cite}<span class="texhtml"><i>G</i></span> is called a '
                          f'<b>widget group</b> when it has a spanning gadget.</p></div>')
        self.assertEqual(glued.finders, [])            # "[1]G" is no word start
        self.assertEqual(glued.counts["finders_dropped"], 2)
        (block,) = glued.paragraphs
        self.assertTrue(glued.layer[block["span"][0]:].startswith("G is called"))
        spaced = rh.render(f'<p>{cite} <i>G</i> is called a <b>widget group</b> when it has '
                           f'a spanning gadget.</p>')
        starts = {x["finder"]: x["start"] for x in spaced.finders}
        self.assertEqual(set(starts), {"bold_definition", "lead"})
        for start in starts.values():
            self.assertTrue(spaced.layer[start:].startswith("G is called"))
            self.assertIsNotNone(spaced.layer_to_text(start))
        only_cite = rh.render(f'<p>{cite}</p>\n<p>A <b>widget</b> is a small gadget with '
                              f'five parts.</p>')
        self.assertEqual(len(only_cite.paragraphs), 1)
        lead = next(x for x in only_cite.finders if x["finder"] == "lead")
        self.assertTrue(only_cite.layer[lead["start"]:].startswith("A widget"))
        only_css = rh.render(f'<p>{css}</p>\n<p>A <b>widget</b> is a small gadget.</p>')
        self.assertEqual(len(only_css.paragraphs), 1)

    def test_02_cells_get_one_separator_whatever_their_whitespace(self):
        self.assertEqual(rh.render("<table><tr>\n<td>a\n</td>\n<td>b\n</td></tr></table>").text,
                         "a | b\n")
        self.assertEqual(rh.render('<table class="wikitable">\n<tbody><tr>\n<th>a\n</th>\n'
                                   '<th>b\n</th></tr>\n<tr>\n<td>c\n</td>\n<td>d\n</td></tr>'
                                   '</tbody></table>').text, "a | b\n\nc | d\n")
        self.assertEqual(rh.render("<table><tr><td><p>x</p></td><td>y</td></tr></table>").text,
                         "x | y\n")

    def test_03_a_display_formula_in_a_cell_stays_on_the_row(self):
        html = ('<table><tr><td>x <span class="mwe-math-element mwe-math-element-block">'
                '<math alttext="a=b" display="block"><mi>a</mi></math></span> y</td>'
                '<td>z</td></tr></table>')
        self.assertEqual(rh.render(html).text, "x $a=b$ y | z\n")

    def test_04_an_image_only_formula_is_in_the_reading_copy(self):
        r = rh.render('<p>Let <span class="mwe-math-element"><img class="mwe-math-fallback-'
                      'image-inline" alt="{\\displaystyle G}"></span> be a finite widget group '
                      'here.</p>')
        self.assertEqual(r.text, "Let $G$ be a finite widget group here.\n")
        self.assertEqual(r.layer, "Let be a finite widget group here.")
        at = r.text.index("$G$")
        self.assertIsNone(r.text_to_layer(at))
        self.assertEqual(r.counts["formulas_without_text"], 1)

    def test_05_a_title_is_its_text_outside_the_dropped_regions(self):
        r = rh.render('<h2 id="History">History<sup class="reference"><a>[1]</a></sup></h2>'
                      '<h2 id="X">Case <style>.texhtml{font-family:serif}</style>'
                      '<span class="texhtml">n</span></h2>')
        self.assertEqual([s["path"] for s in r.sections[1:]], [["History"], ["Case n"]])
        self.assertIn("History[1]", r.layer)

    def test_06_the_newline_after_pre_listing_textarea_is_not_text(self):
        self.assertEqual(rh.render("<p>See:</p><pre>\ncode here</pre>").layer, "See:code here")
        self.assertEqual(rh.render("<p>a</p><textarea>\nb</textarea>").layer, "ab")
        self.assertEqual(rh.render("<p>a</p><listing>\nb</listing>").layer, "ab")
        self.assertEqual(rh.render("<p>a</p><pre>\r\nb</pre>").layer, "ab")
        self.assertEqual(rh.render("<p>See:</p><pre>\n\ncode</pre>").layer, "See: code")
        self.assertEqual(rh.render("<p>a</p><pre><b>\nb</b></pre>").layer, "a b")

    def test_07_comments_are_removed_as_the_tokenizer_delimits_them(self):
        cases = {
            "<style>.a::before{content:'<!--'}</style><p>The widget lemma states a fact.</p>":
                "<style>.a::before{content:'<!--'}</style><p>The widget lemma states a fact.</p>",
            "<p>x<span title='<!-- y'>z</span> rest</p>":
                "<p>x<span title='<!-- y'>z</span> rest</p>",
            "<p>a<!-->b-->c</p>": "<p>ab-->c</p>",
            "<p>a<!--->b-->c</p>": "<p>ab-->c</p>",
            "<p>a<!-- x --!>b</p>": "<p>ab</p>",
            "<p>good<!-- real comment -->text</p>": "<p>goodtext</p>",
            "<p>q<!-- open": "<p>q",
            "<script>if (a<!--b) {}</script><p>s</p>": "<script>if (a<!--b) {}</script><p>s</p>",
            "<p>a<!-- one --><b>b</b><!-- two\n lines -->c</p>": "<p>a<b>b</b>c</p>",
            "<p>1 < 2 <!-- c --></p>": "<p>1 < 2 </p>",
        }
        for html, body in cases.items():
            with self.subTest(html=html):
                self.assertEqual(rh.canonical_body(html.encode()), body)
        self.assertEqual(rh.render(rh.canonical_body(
            b"<style>.a::before{content:'<!--'}</style><p>The widget lemma.</p>")).layer,
            "The widget lemma.")

    def test_08_a_byte_order_mark_is_dropped(self):
        raw = b'\xef\xbb\xbf{"parse": {"revid": 2002, "text": "<p>Widget lemma text</p>"}}'
        self.assertEqual(rh.canonical_body(raw), "<p>Widget lemma text</p>")
        self.assertEqual(rh.parse_response_text(raw)[1]["revid"], 2002)
        self.assertEqual(rh.canonical_body(b"\xef\xbb\xbf<p>x</p>"), "<p>x</p>")

    def test_09_a_body_with_a_cr_keeps_its_bytes_and_its_sha(self):
        out = _scratch(self)
        body = rh.canonical_body(b"<p>a\r\nb</p>")
        rh.write_bytes(out / "body.html", body.encode("utf-8"))
        r = rh.render_directory(out)
        self.assertEqual(r.html_sha256, websource_sha(out / "body.html"))
        self.assertEqual(json.loads((out / "render.json").read_text())["html_sha256"],
                         websource_sha(out / "body.html"))
        (out / "text.txt").write_bytes(r.text.replace("\n", "\r\n").encode("utf-8"))
        rh.write_rendering(out, r)
        self.assertEqual((out / "text.txt").read_bytes(), r.text.encode("utf-8"))

    def test_10_deep_nesting_is_refused_not_a_crash(self):
        for html in ("<div>" * 500 + "<p>text <b>x</b></p>" + "</div>" * 500,
                     "<p>" + "<span>" * 500 + "x"):
            with self.subTest(n=len(html)), self.assertRaises(rh.RenderError):
                rh.render(html)
        ok = "<div>" * 200 + "<p>text</p>" + "</div>" * 200
        self.assertEqual(rh.render(ok).layer, "text")

    def test_17_a_lone_surrogate_is_refused(self):
        with self.assertRaises(rh.RenderError):
            rh.canonical_body(b'{"parse": {"revid": 2002, "text": "<p>x\\ud800y</p>"}}')
        with self.assertRaises(rh.RenderError):
            rh.render("<p>x\ud800y</p>")


# ---------------------------------------------------------------------------
# review fixes: the client and the identity
# ---------------------------------------------------------------------------

class _HeaderRun(_Run):
    """A fake curl that also writes response headers to -D."""

    def __init__(self, script):
        super().__init__([(c, s, b) for c, s, b, _h in script])
        self.headers = [h for _c, _s, _b, h in script]

    def __call__(self, argv, capture_output=True, text=True):
        headers = self.headers.pop(0)
        if "-D" in argv:
            Path(argv[argv.index("-D") + 1]).write_bytes(headers)
        return super().__call__(argv, capture_output=capture_output, text=text)


class TestReviewFixesClient(unittest.TestCase):

    def transport(self, run):
        sleeps: list[float] = []
        now = [0.0]

        def sleep(s):
            sleeps.append(s)
            now[0] += s

        return wikinet.CurlTransport(runner=run, sleep=sleep, clock=lambda: now[0],
                                     exe="curl"), sleeps

    def test_16_retry_after_is_honoured_and_maxlag_waits_five_seconds(self):
        ok = json.dumps({"parse": {"revid": 3, "text": "x"}}).encode()
        lag = json.dumps({"error": {"code": "maxlag"}}).encode()
        run = _HeaderRun([(22, "429", b"", b"HTTP/1.1 429 Too Many\r\nRetry-After: 30\r\n\r\n"),
                          (0, "200", lag, b"HTTP/1.1 200 OK\r\n\r\n"),
                          (0, "200", ok, b"HTTP/1.1 200 OK\r\n\r\n")])
        t, sleeps = self.transport(run)
        self.assertEqual(wikinet.fetch_parse(t, SITE, 3), ok)
        # the 429 asked for 30 s; the lag waits at least MAXLAG_WAIT, above the 4 s backoff
        self.assertEqual(sleeps[:2], [30.0, wikinet.MAXLAG_WAIT])
        self.assertIn("-D", run.argvs[0])
        huge = _HeaderRun([(22, "503", b"", b"HTTP/1.1 503\r\nRetry-After: 9999\r\n\r\n"),
                           (0, "200", ok, b"")])
        t, sleeps = self.transport(huge)
        wikinet.fetch_parse(t, SITE, 3)
        self.assertEqual(sleeps[0], wikinet.MAX_RETRY_WAIT)

    def test_16_exhausted_retries_and_a_403_are_unavailable(self):
        t, _s = self.transport(_Run([(22, "429", b"")] * 4))
        with self.assertRaises(wikinet.WikiUnavailable):
            wikinet.fetch_parse(t, SITE, 3)
        run = _Run([(22, "403", b""), (0, "200", b"{}")])
        t, _s = self.transport(run)
        with self.assertRaises(wikinet.WikiUnavailable):
            wikinet.fetch_parse(t, SITE, 3)
        self.assertEqual(len(run.argvs), 1)

    def test_20_a_missing_curl_is_a_named_refusal(self):
        def missing(argv, capture_output=True, text=True):
            raise FileNotFoundError(2, "No such file or directory", "curl")

        t = wikinet.CurlTransport(runner=missing, sleep=lambda s: None, exe="curl")
        with self.assertRaisesRegex(wikinet.WikiError, "curl could not be run"):
            wikinet.fetch_parse(t, SITE, 3)

    def test_13_check_parse_is_the_fetch_check(self):
        good = json.dumps({"parse": {"revid": 3, "text": "<p>x</p>"}}).encode()
        self.assertEqual(wikinet.check_parse(good, 3)["revid"], 3)
        for bad in (b"<p>html</p>", json.dumps({"parse": {"revid": 4, "text": "x"}}).encode(),
                    json.dumps({"parse": {"revid": 3}}).encode()):
            with self.subTest(bad=bad), self.assertRaises(wikinet.WikiError):
                wikinet.check_parse(bad, 3)

    def test_17_19_the_entry_checks(self):
        fam = websource.family("enwiki")
        with self.assertRaises(CfError):
            websource.entry(fam, 1001, 2002, assessment="B")
        e = websource.entry(fam, 1001, 2002, title="Widget")
        self.assertTrue(any("surrogate" in p
                            for p in websource.entry_problems(dict(e, title="x\ud800"))))
        self.assertIs(websource.ORIGINS, vocab.SOURCE_ORIGINS)
        self.assertEqual(vocab.SOURCE_ORIGINS, {"enumerated", "adopted"})


# ---------------------------------------------------------------------------
# sourcesets
# ---------------------------------------------------------------------------

def _workspace(case) -> Path:
    root = _scratch(case) / "ws"
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        case.assertEqual(cf_main.main(["init", str(root), "--no-git", "-q"]), 0)
    return root


def cf(root: Path, *argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cf_main.main(["--workspace", str(root), *argv])
        except CfError as exc:
            return 2, f"{out.getvalue()}{err.getvalue()}error: {exc}"
    return code, out.getvalue() + err.getvalue()


def _entry(n: int, **fields) -> dict:
    e = websource.entry(websource.family("enwiki"), 1000 + n, 2000 + n, title=f"Page {n}")
    e.update(fields)
    return e


class TestTheStateFileHasOneWriter(unittest.TestCase):
    """Wave 1 #83: ``Sourceset.record_state`` is the state file's one writer, per key."""

    def setUp(self):
        self.root = _workspace(self)
        self.paths = WorkspacePaths(self.root)
        self.ss = Sourceset.create(self.paths, "wpm", "enwiki")

    def state(self) -> dict:
        return json.loads(self.paths.sourceset_state_json("wpm").read_text(encoding="utf-8"))

    def test_a_keyed_row_is_merged_and_its_siblings_kept(self):
        self.ss.record_state("runs", {"planned": 3, "preset": "locate"}, key="r1")
        self.ss.record_state("runs", {"planned": 5}, key="r2")
        self.ss.record_state("runs", {"harvested": 2}, key="r1")
        runs = self.state()["runs"]
        self.assertEqual({k: {f: v for f, v in row.items() if f != "at"}
                          for k, row in runs.items()},
                         {"r1": {"planned": 3, "preset": "locate", "harvested": 2},
                          "r2": {"planned": 5}})
        self.assertTrue(all(row.get("at") for row in runs.values()))

    def test_the_header_is_kept_and_never_the_other_writers(self):
        self.ss.refresh_state(command="status")
        self.ss.record_state("exports", {"records": 4}, key="set-1")
        state = self.state()
        self.assertEqual((state["schema"], state["id"]), ("cf-sourceset-state/1", "wpm"))
        self.assertNotIn("version", state)
        self.assertNotIn("sourceset", state)
        self.assertIn("counts", state)                  # refresh_state's section survives
        self.ss.refresh_state(command="status")
        self.assertIn("set-1", self.state()["exports"])  # and survives it in turn

    def test_a_whole_section_without_a_key(self):
        self.ss.record_state("tally", {"a": 1})
        self.ss.record_state("tally", {"b": 2})
        self.assertEqual(self.state()["tally"], {"b": 2})

    def test_refusals(self):
        for section in ("schema", "id", "counts", "manifest"):
            with self.subTest(section=section), self.assertRaises(SourcesetError):
                self.ss.record_state(section, {"x": 1})
        with self.assertRaises(SourcesetError):
            self.ss.record_state("notes", ["a list"])
        state = self.state()
        state["odd"] = [1, 2]
        self.paths.sourceset_state_json("wpm").write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(SourcesetError):
            self.ss.record_state("odd", {"y": 2}, key="k")
        with self.assertRaises(SourcesetError):
            Sourceset(self.paths, "nope").record_state("runs", {"x": 1}, key="r")


class TestSourcesetManifests(unittest.TestCase):

    def setUp(self):
        self.root = _workspace(self)
        self.paths = WorkspacePaths(self.root)
        self.ss = Sourceset.create(self.paths, "wpm", "enwiki")

    def test_create_writes_a_header_only_version_one(self):
        m = self.ss.latest()
        self.assertEqual((m.version, len(m), m.family), (1, 0, "enwiki"))
        line = self.paths.sourceset_manifest("wpm", 1).read_text().splitlines()
        self.assertEqual(len(line), 1)
        self.assertEqual(json.loads(line[0])["schema"], "cf-sourceset/1")
        with self.assertRaises(SourcesetError):
            Sourceset.create(self.paths, "wpm", "enwiki")
        with self.assertRaises(CfError):
            Sourceset.create(self.paths, "other", "nowiki")
        self.assertEqual(self.ss.read_state()["counts"]["sources"], 0)

    def test_versions_chain_and_are_canonical(self):
        m2 = self.ss.write_version(self.ss.latest(), [_entry(2), _entry(1)])
        self.assertEqual(m2.version, 2)
        self.assertEqual(m2.header["previous_sha256"], self.ss.load(1).sha256)
        lines = m2.path.read_text().splitlines()
        self.assertEqual([json.loads(x)["source_id"] for x in lines[1:]],
                         ["enwiki-1001-2001", "enwiki-1002-2002"])
        self.assertEqual(lines[1], json.dumps(json.loads(lines[1]), sort_keys=True,
                                              separators=(",", ":"), ensure_ascii=False))
        self.assertIs(self.ss.write_version(m2, list(m2)), m2)       # unchanged: no version
        filled = dict(m2.get("enwiki-1001-2001"), raw_sha256="a" * 64)
        m3 = self.ss.write_version(m2, [filled, m2.get("enwiki-1002-2002")])
        self.assertEqual(m3.get("enwiki-1001-2001")["raw_sha256"], "a" * 64)
        self.assertEqual(self.ss.pinned(2, m2.sha256).version, 2)
        with self.assertRaises(SourcesetError):
            self.ss.pinned(2, m3.sha256)

    def test_append_only_refusals(self):
        m2 = self.ss.write_version(self.ss.latest(), [_entry(1, raw_sha256="a" * 64), _entry(2)])
        with self.assertRaisesRegex(SourcesetError, "never drops"):
            self.ss.write_version(m2, [m2.get("enwiki-1001-2001")])
        changed = dict(m2.get("enwiki-1001-2001"), raw_sha256="b" * 64)
        with self.assertRaisesRegex(SourcesetError, "raw_sha256"):
            self.ss.write_version(m2, [changed, m2.get("enwiki-1002-2002")])
        retitled = dict(m2.get("enwiki-1002-2002"), title="Moved")
        with self.assertRaisesRegex(SourcesetError, "title"):
            self.ss.write_version(m2, [m2.get("enwiki-1001-2001"), retitled])

    def test_a_concurrent_writer_and_a_rewritten_version_are_refused(self):
        m1 = self.ss.latest()
        self.ss.write_version(m1, [_entry(1)])
        with self.assertRaisesRegex(SourcesetError, "this command read version 1"):
            self.ss.write_version(m1, [_entry(2)])
        path1 = self.paths.sourceset_manifest("wpm", 1)
        path1.write_text(path1.read_text().replace('"sources":0', '"sources":0 '))
        with self.assertRaisesRegex(SourcesetError, "rewritten"):
            self.ss.load(2)

    def test_a_malformed_manifest_is_refused(self):
        m2 = self.ss.write_version(self.ss.latest(), [_entry(1), _entry(2)])
        lines = m2.path.read_text().splitlines()
        m2.path.write_text("\n".join([lines[0], lines[2], lines[1]]) + "\n")
        with self.assertRaisesRegex(SourcesetError, "out of order"):
            self.ss.load(2)

    def test_lazy_validation_reads_the_files_it_is_asked_about(self):
        directory = self.ss.source_dir("enwiki-1001-2001")
        directory.mkdir(parents=True)
        (directory / "source.raw.html").write_bytes(b"raw")
        entry = _entry(1, raw_sha256=websource_sha(directory / "source.raw.html"),
                       html_sha256="c" * 64)
        problems = self.ss.source_problems(entry)
        self.assertEqual(len(problems), 1)
        self.assertIn("body.html is missing", problems[0])


class TestTheSourcesetCommand(unittest.TestCase):

    def setUp(self):
        self.root = _workspace(self)
        self.paths = WorkspacePaths(self.root)
        code, out = cf(self.root, "sourceset", "create", "wpm")
        self.assertEqual(code, 0, out)
        self.ss = Sourceset.open(self.paths, "wpm")

    def enumerate(self, *extra) -> tuple[int, str]:
        return cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project",
                  "Mathematics", "--fixtures", str(FIXTURES), *extra)

    def test_the_dry_runs_touch_nothing(self):
        code, out = cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project",
                       "Mathematics")
        self.assertEqual(code, 0, out)
        self.assertIn("dry run", out)
        self.assertIn("list=projectpages", out)
        self.assertEqual(self.ss.versions(), [1])
        code, out = cf(self.root, "sourceset", "fetch", "wpm")
        self.assertEqual((code, "dry run" in out), (0, True))

    def test_enumerate_fetch_render_status(self):
        code, out = self.enumerate()
        self.assertEqual(code, 0, out)
        m = self.ss.latest()
        self.assertEqual(sorted(m.entries), ["enwiki-1001-2002", "enwiki-1002-2003",
                                             "enwiki-1003-2004"])
        widget = m.get("enwiki-1001-2002")
        self.assertEqual((widget["wikidata"], widget["assessment"], widget["origin"]),
                         ("Q99999001", {"class": "B", "importance": "high"}, "enumerated"))
        code, out = self.enumerate()
        self.assertEqual(code, 0, out)
        self.assertIn("3 already present", out)
        self.assertEqual(self.ss.latest().version, m.version)

        code, out = cf(self.root, "sourceset", "fetch", "wpm", "--fixtures", str(FIXTURES))
        self.assertEqual(code, 0, out)
        m = self.ss.latest()
        for e in m:
            self.assertEqual(self.ss.source_problems(e), [])
            self.assertEqual(e["fetch"], websource.fetch_block())
            d = self.ss.source_dir(e["source_id"])
            self.assertEqual(json.loads((d / "source.json").read_text()), e)
        code, out = cf(self.root, "sourceset", "render", "wpm")
        self.assertEqual(code, 0, out)
        m = self.ss.latest()
        for e in m:
            self.assertEqual(e["renderer"], rh.RENDERER)
            self.assertEqual(self.ss.source_problems(e), [])
            self.assertTrue((self.ss.source_dir(e["source_id"]) / "finders.json").is_file())
        code, out = cf(self.root, "sourceset", "render", "wpm")
        self.assertEqual(code, 0, out)
        self.assertIn("3 unchanged", out)

        code, out = cf(self.root, "sourceset", "status", "wpm", "--json", "--verify")
        self.assertEqual(code, 0, out)
        report = json.loads(out)
        self.assertEqual(report["counts"]["rendered"], 3)
        self.assertEqual(report["manifest"]["manifest_sha256"], m.sha256)
        state = self.ss.read_state()
        self.assertEqual(state["manifest"], {"version": m.version, "sha256": m.sha256})
        self.assertEqual(set(state["commands"]), {"create", "enumerate-wikiproject", "fetch",
                                                  "render", "status"})
        # a body changed on disk is caught by --verify
        (self.ss.source_dir("enwiki-1003-2004") / "body.html").write_text("tampered")
        code, out = cf(self.root, "sourceset", "status", "wpm", "--verify")
        self.assertEqual(code, 1, out)
        self.assertIn("body.html has sha", out)

    def test_filters_and_limits(self):
        code, out = self.enumerate("--importance", "HIGH", "--importance", "mid", "--limit", "1")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(self.ss.latest().entries), ["enwiki-1001-2002"])

    def test_a_new_revision_is_a_new_source_beside_the_old(self):
        self.enumerate()
        newer = _scratch(self)
        for p in FIXTURES.glob("*.json"):
            shutil.copy(p, newer / p.name)
        info = json.loads((newer / "pageinfo-1001-1002-1003.json").read_text())
        info["json"]["query"]["pages"][0]["revisions"][0]["revid"] = 2010
        (newer / "pageinfo-1001-1002-1003.json").write_text(json.dumps(info))
        code, out = cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project",
                       "Mathematics", "--fixtures", str(newer))
        self.assertEqual(code, 0, out)
        self.assertIn("new revision of a present page", out)
        self.assertIn("enwiki-1001-2010", self.ss.latest().entries)
        self.assertIn("enwiki-1001-2002", self.ss.latest().entries)

    def test_fetch_is_once_even_after_a_crash(self):
        self.enumerate("--limit", "1")
        d = self.ss.source_dir("enwiki-1001-2002")
        d.mkdir(parents=True)
        recorded = json.loads((FIXTURES / "parse-2002.json").read_text())["json"]
        (d / "source.raw.html").write_bytes(json.dumps(recorded).encode())
        empty = _scratch(self)          # no recordings: a request would be refused
        code, out = cf(self.root, "sourceset", "fetch", "wpm", "--fixtures", str(empty))
        self.assertEqual(code, 0, out)
        self.assertIn("reused 1", out)
        self.assertEqual(self.ss.source_problems(self.ss.latest().get("enwiki-1001-2002")), [])

    def test_a_render_that_would_change_a_recorded_layer_is_refused(self):
        self.enumerate("--limit", "1")
        cf(self.root, "sourceset", "fetch", "wpm", "--fixtures", str(FIXTURES))
        cf(self.root, "sourceset", "render", "wpm")
        layer = self.ss.source_dir("enwiki-1001-2002") / "layer.txt"
        before = layer.read_bytes()
        real = rh.render

        def drifted(body):
            r = real(body)
            r.layer = r.layer + " drift"
            return r

        with mock.patch.object(rh, "render", drifted):
            code, out = cf(self.root, "sourceset", "render", "wpm")
        self.assertEqual(code, 1, out)
        self.assertIn("refused", out)
        self.assertEqual(layer.read_bytes(), before)

    def test_adopt_a_consumers_pins(self):
        self.enumerate()
        cf(self.root, "sourceset", "fetch", "wpm", "--fixtures", str(FIXTURES))
        pins = _scratch(self)
        body = {"parse": {"revid": 2011, "text": "<p>The <b>widget lemma</b> as the consumer "
                                                 "cached it, at a later revision.</p>"}}
        (pins / "1001-2011.json").write_text(json.dumps(body))
        (pins / "pins.jsonl").write_text(json.dumps(
            {"pageid": 1001, "revision": 2011, "title": "Widget lemma", "wikidata": "Q99999001",
             "bytes": "1001-2011.json"}) + "\n")
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins / "pins.jsonl"),
                       "--consumer", "wikilean")
        self.assertEqual(code, 0, out)
        self.assertIn("both are kept", out)
        e = self.ss.latest().get("enwiki-1001-2011")
        self.assertEqual((e["origin"], e["adopted_from"]["consumer"]), ("adopted", "wikilean"))
        self.assertEqual(self.ss.source_problems(e), [])
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins / "pins.jsonl"))
        self.assertEqual(code, 0, out)
        self.assertIn("1 already held", out)
        # other bytes for a fetched source, and a revision mismatch, are refused
        (pins / "other.json").write_text(json.dumps({"parse": {"revid": 2002, "text": "<p>x</p>"}}))
        (pins / "bad.jsonl").write_text(json.dumps(
            {"pageid": 1001, "revision": 2002, "bytes": "other.json"}) + "\n")
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins / "bad.jsonl"))
        self.assertEqual(code, 2, out)
        self.assertIn("already holds other bytes", out)
        (pins / "wrong.jsonl").write_text(json.dumps(
            {"pageid": 1001, "revision": 2020, "bytes": "other.json"}) + "\n")
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins / "wrong.jsonl"))
        self.assertEqual(code, 2, out)
        self.assertIn("the parse of revision 2002", out)
        code, out = cf(self.root, "sourceset", "render", "wpm", "--source", "enwiki-1001-2011")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ss.latest().get("enwiki-1001-2011")["renderer"], rh.RENDERER)

    def test_an_unknown_sourceset_is_refused_with_a_hint(self):
        code, out = cf(self.root, "sourceset", "status", "nope")
        self.assertEqual(code, 2)
        self.assertIn("no sourceset", out)


class TestReviewFixesSourcesets(unittest.TestCase):
    """Regression tests for the adversarial review of the sourceset code (ids in the names)."""

    def setUp(self):
        self.root = _workspace(self)
        self.paths = WorkspacePaths(self.root)
        code, out = cf(self.root, "sourceset", "create", "wpm")
        self.assertEqual(code, 0, out)
        self.ss = Sourceset.open(self.paths, "wpm")
        self.pins = _scratch(self)

    def pin(self, name: str, rows: list[dict], files: dict[str, bytes]) -> str:
        for fname, data in files.items():
            (self.pins / fname).write_bytes(data)
        path = self.pins / name
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return str(path)

    def adopt(self, rows, files, *extra):
        return cf(self.root, "sourceset", "adopt", "wpm", "--from",
                  self.pin("pins.jsonl", rows, files), *extra)

    def parse(self, revid=2002, pageid=1001, text="<p>The <b>widget lemma</b> holds.</p>",
              title="Widget lemma") -> bytes:
        return json.dumps({"parse": {"revid": revid, "pageid": pageid, "title": title,
                                     "text": text}}).encode()

    def test_08_24_json_that_is_no_parse_response_is_refused(self):
        bom = b"\xef\xbb\xbf" + self.parse(revid=9999)
        code, out = self.adopt([{"pageid": 1001, "revision": 2011, "bytes": "b.json"}],
                               {"b.json": bom})
        self.assertEqual(code, 2, out)
        self.assertIn("the parse of revision 9999", out)
        code, out = self.adopt([{"pageid": 1001, "revision": 2011, "bytes": "e.json"}],
                               {"e.json": b'{"error": {"code": "nosuchrevid"}}'})
        self.assertEqual(code, 2, out)
        self.assertIn("nosuchrevid", out)
        code, out = self.adopt([{"pageid": 1001, "revision": 2011, "bytes": "w.json"}],
                               {"w.json": b'{"parse": {"revid": 2011, "wikitext": "x"}}'})
        self.assertEqual(code, 2, out)
        self.assertEqual(self.ss.versions(), [1])

    def test_11_22_a_line_separator_in_a_value_does_not_wedge_the_sourceset(self):
        title = "Widget lemma and\u0085more"
        code, out = self.adopt([{"pageid": 1001, "revision": 2002, "title": title,
                                 "bytes": "b.json"}], {"b.json": self.parse()},
                               "--consumer", "wiki\u0085lean")
        self.assertEqual(code, 0, out)
        m = self.ss.latest()
        self.assertEqual(m.get("enwiki-1001-2002")["title"], title)
        self.assertEqual(m.get("enwiki-1001-2002")["adopted_from"]["consumer"], "wiki\u0085lean")
        self.assertEqual(len(m.path.read_bytes().split(b"\n")), 3)
        self.assertEqual(cf(self.root, "sourceset", "status", "wpm")[0], 0)

    def test_12_23_a_crlf_body_renders(self):
        code, out = self.adopt([{"pageid": 1001, "revision": 2002, "bytes": "b.html"}],
                               {"b.html": b"<p>Widget lemma.</p>\r\n<p>Every widget spans.</p>\r\n"})
        self.assertEqual(code, 0, out)
        code, out = cf(self.root, "sourceset", "render", "wpm")
        self.assertEqual(code, 0, out)
        self.assertIsNotNone(self.ss.latest().get("enwiki-1001-2002")["layer_sha256"])
        code, out = cf(self.root, "sourceset", "status", "wpm", "--verify")
        self.assertEqual(code, 0, out)

    def test_10_a_body_the_renderer_refuses_is_a_recorded_refusal(self):
        deep = "<div>" * 500 + "<p>x</p>" + "</div>" * 500
        self.adopt([{"pageid": 1001, "revision": 2002, "bytes": "a.html"},
                    {"pageid": 1002, "revision": 2003, "bytes": "b.html"}],
                   {"a.html": deep.encode(), "b.html": b"<p>A fine body.</p>"})
        code, out = cf(self.root, "sourceset", "render", "wpm")
        self.assertEqual(code, 1, out)
        self.assertIn("nests elements", out)
        m = self.ss.latest()
        self.assertIsNone(m.get("enwiki-1001-2002")["layer_sha256"])
        self.assertIsNotNone(m.get("enwiki-1002-2003")["layer_sha256"])

    def test_13_reused_bytes_are_checked_and_an_interrupted_adopt_is_not_fetched(self):
        cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project", "Mathematics",
           "--fixtures", str(FIXTURES), "--limit", "1")
        d = self.ss.source_dir("enwiki-1001-2002")
        d.mkdir(parents=True)
        (d / "source.raw.html").write_bytes(b"<p>consumer cached HTML</p>")
        (d / "source.json").write_text(json.dumps({"adopted_from": {"consumer": "wikilean"}}))
        empty = _scratch(self)
        code, out = cf(self.root, "sourceset", "fetch", "wpm", "--fixtures", str(empty))
        self.assertEqual(code, 1, out)
        self.assertIn("interrupted adopt", out)
        self.assertIsNone(self.ss.latest().get("enwiki-1001-2002")["fetch"])
        (d / "source.json").unlink()
        (d / "source.raw.html").write_bytes(self.parse(revid=2999))
        code, out = cf(self.root, "sourceset", "fetch", "wpm", "--fixtures", str(empty))
        self.assertEqual(code, 1, out)
        self.assertIn("answered for revision 2999", out)
        self.assertIsNone(self.ss.latest().get("enwiki-1001-2002")["raw_sha256"])

    def test_14_a_racing_writer_cannot_leave_a_sha_with_no_bytes(self):
        cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project", "Mathematics",
           "--fixtures", str(FIXTURES), "--limit", "1")
        from cfcmd import sourceset as cmd
        recorded = json.loads((FIXTURES / "parse-2002.json").read_text())["json"]
        theirs = json.dumps(dict(recorded, theirs=True)).encode()
        mine = json.dumps(recorded).encode()
        raw_path = self.ss.source_dir("enwiki-1001-2002") / "source.raw.html"

        class Racing:
            def get(self, site, params):
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(theirs)          # the other writer, mid-request
                return mine

        with mock.patch.object(cmd, "_transport", lambda *a: Racing()):
            code, out = cf(self.root, "sourceset", "fetch", "wpm", "--execute")
        self.assertEqual(code, 0, out)
        e = self.ss.latest().get("enwiki-1001-2002")
        self.assertEqual(e["raw_sha256"], websource_sha(raw_path))
        self.assertEqual(raw_path.read_bytes(), theirs)
        self.assertEqual(self.ss.source_problems(e), [])

    def test_15_a_pin_whose_bytes_name_another_page_is_refused(self):
        code, out = self.adopt([{"pageid": 1003, "revision": 2002, "title": "Sprocket",
                                 "bytes": "b.json"}], {"b.json": self.parse()})
        self.assertEqual(code, 2, out)
        self.assertIn("pageid 1001", out)
        code, out = self.adopt([{"pageid": 1001, "revision": 2002, "bytes": "b.json"}],
                               {"b.json": self.parse()})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ss.latest().get("enwiki-1001-2002")["title"], "Widget lemma")

    def test_16_a_fetch_stops_when_wikipedia_keeps_refusing(self):
        cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project", "Mathematics",
           "--fixtures", str(FIXTURES))
        from cfcmd import sourceset as cmd
        calls = []

        class Limited:
            def get(self, site, params):
                calls.append(params["oldid"])
                if params["oldid"] == "2002":
                    return json.dumps(json.loads((FIXTURES / "parse-2002.json").read_text())
                                      ["json"]).encode()
                raise wikinet.WikiUnavailable("rate-limited")

        with mock.patch.object(cmd, "_transport", lambda *a: Limited()), \
                mock.patch.object(cmd, "MAX_UNAVAILABLE", 2):
            code, out = cf(self.root, "sourceset", "fetch", "wpm", "--execute")
        self.assertEqual(code, 2, out)
        self.assertIn("stopped after 2", out)
        self.assertEqual(calls, ["2002", "2003", "2004"])
        self.assertIsNotNone(self.ss.latest().get("enwiki-1001-2002")["raw_sha256"])

    def test_17_27_malformed_pins_are_refusals_naming_the_line(self):
        cases = [
            ({"s.json": b'{"parse":{"revid":2002,"text":"<p>x\\ud800y</p>"}}'}, "s.json",
             None, "surrogate"),
            ({"l.html": "<p>café widget</p>".encode("latin-1")}, "l.html", None, "UTF-8"),
            ({"a.json": self.parse()}, "a.json", "B", "assessment"),
        ]
        for files, name, assessment, needle in cases:
            row = {"pageid": 1001, "revision": 2002, "bytes": name}
            if assessment is not None:
                row["assessment"] = assessment
            with self.subTest(case=needle):
                code, out = self.adopt([row], files)
                self.assertEqual(code, 2, out)
                self.assertIn("pins.jsonl:1", out)
                self.assertIn(needle, out)
        self.assertEqual(self.ss.versions(), [1])

    def test_17_a_fetched_body_with_a_surrogate_fails_that_source_only(self):
        cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project", "Mathematics",
           "--fixtures", str(FIXTURES))
        fx = _scratch(self)
        for p in FIXTURES.glob("*.json"):
            shutil.copy(p, fx / p.name)
        bad = json.loads((fx / "parse-2003.json").read_text())
        bad["body"] = ('{"parse": {"revid": 2003, "pageid": 1002, '
                       '"text": "<p>x\\ud800y</p>"}}')
        del bad["json"]
        (fx / "parse-2003.json").write_text(json.dumps(bad))
        code, out = cf(self.root, "sourceset", "fetch", "wpm", "--fixtures", str(fx))
        self.assertEqual(code, 1, out)
        self.assertIn("surrogate", out)
        m = self.ss.latest()
        self.assertIsNotNone(m.get("enwiki-1001-2002")["raw_sha256"])
        self.assertIsNone(m.get("enwiki-1002-2003")["raw_sha256"])
        self.assertFalse((self.ss.source_dir("enwiki-1002-2003") / "source.raw.html").exists())

    def overlay(self, body: str) -> None:
        with (self.root / "registry.yaml").open("a", encoding="utf-8") as fh:
            fh.write(body)

    def test_18_26_the_network_reaches_only_a_reviewed_site(self):
        self.overlay("families:\n"
                     "  testwiki:\n"
                     "    title: Test Wiki\n"
                     "    site: https://wiki.example.org\n"
                     "    licence_class: open_attribution\n"
                     "    licence: {spdx: CC-BY-4.0, note: CC BY 4.0 text, redistribute: false}\n"
                     "    attribution: '\"{title}\", Test Wiki, {url}, {licence}'\n"
                     "    revision_url: \"https://wiki.example.org/?oldid={revision}\"\n"
                     "  enwiki:\n"
                     "    site: https://mirror.example.net\n")
        code, out = cf(self.root, "sourceset", "create", "tw", "--family", "testwiki")
        self.assertEqual(code, 0, out)                      # offline use is the overlay's
        from cfcmd import sourceset as cmd
        built = []
        with mock.patch.object(cmd.wikinet, "CurlTransport", lambda *a, **k: built.append(1)):
            code, out = cf(self.root, "sourceset", "enumerate-wikiproject", "tw", "--project",
                           "P", "--execute")
            self.assertEqual(code, 2, out)
            self.assertIn("not confirmed", out)
            code, out = cf(self.root, "sourceset", "fetch", "wpm", "--execute")
            self.assertEqual(code, 2, out)
            self.assertIn("changes its site", out)
        self.assertEqual(built, [])

    def test_25_a_page_a_consumer_pinned_keeps_its_adopted_revision(self):
        code, out = self.adopt([{"pageid": 1001, "revision": 2011, "bytes": "b.json"}],
                               {"b.json": self.parse(revid=2011)})
        self.assertEqual(code, 0, out)
        code, out = cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project",
                       "Mathematics", "--fixtures", str(FIXTURES))
        self.assertEqual(code, 0, out)
        self.assertIn("pinned by a consumer", out)
        m = self.ss.latest()
        self.assertNotIn("enwiki-1001-2002", m.entries)
        self.assertEqual(m.preferred()[1001], "enwiki-1001-2011")
        code, out = cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project",
                       "Mathematics", "--fixtures", str(FIXTURES), "--include-adopted")
        self.assertEqual(code, 0, out)
        m = self.ss.latest()
        self.assertIn("enwiki-1001-2002", m.entries)
        self.assertEqual(m.preferred()[1001], "enwiki-1001-2011")   # still the adopted one
        code, out = cf(self.root, "sourceset", "fetch", "wpm", "--fixtures", str(FIXTURES))
        self.assertEqual(code, 0, out)
        self.assertIn("left to their consumer's adopted revision", out)
        m = self.ss.latest()
        self.assertIsNone(m.get("enwiki-1001-2002")["raw_sha256"])
        self.assertIsNotNone(m.get("enwiki-1002-2003")["raw_sha256"])
        self.assertEqual(m.preferred(), {1001: "enwiki-1001-2011", 1002: "enwiki-1002-2003",
                                         1003: "enwiki-1003-2004"})



# ---------------------------------------------------------------------------
# field fixes: defects found running the stage against real data (synthetic
# reductions; the defect ids are in the names)
# ---------------------------------------------------------------------------

class _FieldCase(unittest.TestCase):

    def setUp(self):
        self.root = _workspace(self)
        self.paths = WorkspacePaths(self.root)
        code, out = cf(self.root, "sourceset", "create", "wpm")
        self.assertEqual(code, 0, out)
        self.ss = Sourceset.open(self.paths, "wpm")

    def enumerate(self, *extra, fixtures: Path = FIXTURES) -> tuple[int, str]:
        return cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project",
                  "Mathematics", "--fixtures", str(fixtures), *extra)

    def fetch(self, *extra, fixtures: Path = FIXTURES) -> tuple[int, str]:
        return cf(self.root, "sourceset", "fetch", "wpm", "--fixtures", str(fixtures), *extra)


class TestFieldS1TheRendererBumpPath(_FieldCase):

    def test_s1_a_layer_of_the_older_renderer_is_re_rendered_in_a_seeded_sourceset(self):
        self.enumerate()
        self.fetch()
        code, out = cf(self.root, "sourceset", "render", "wpm")
        self.assertEqual(code, 0, out)
        base = self.ss.latest()
        older = self.ss.source_dir("enwiki-1001-2002")
        # what the field held: a sourceset whose layers were recorded under web-html/1
        code, out = cf(self.root, "sourceset", "create", "old")
        self.assertEqual(code, 0, out)
        old_ss = Sourceset.open(self.paths, "old")
        rows = []
        for e in base:
            row = dict(e, renderer="web-html/1", layer_sha256="0" * 64)
            src, dst = self.ss.source_dir(e["source_id"]), old_ss.source_dir(e["source_id"])
            dst.mkdir(parents=True)
            for name in ("source.raw.html", "body.html"):
                shutil.copy(src / name, dst / name)
            rows.append(row)
        old_ss.write_version(old_ss.latest(), rows)
        code, out = cf(self.root, "sourceset", "render", "old")
        self.assertEqual(code, 1, out)
        self.assertIn("rendered by an older renderer", out)
        self.assertIn("--from old", out)
        # the planner and L2 read a source through locate.inputs.check_source: an older
        # renderer's source is refused with the same step, not a render the set refuses
        from cflib.errors import CfError as _CfError
        from locate import inputs as locate_inputs
        with self.assertRaises(_CfError) as ctx:
            locate_inputs.check_source(self.paths, "old", old_ss.latest().get("enwiki-1001-2002"))
        self.assertIn("cf sourceset create <new id> --from old", ctx.exception.hint)
        self.assertIn("web-html/1", ctx.exception.hint)
        self.assertNotIn("cf sourceset render old", ctx.exception.hint)
        # the path: a new sourceset seeded with the old one's bytes, rendered anew
        code, out = cf(self.root, "sourceset", "create", "new", "--from", "old")
        self.assertEqual(code, 0, out)
        new_ss = Sourceset.open(self.paths, "new")
        seeded = new_ss.latest()
        self.assertEqual(sorted(seeded.entries), sorted(e["source_id"] for e in base))
        for e in seeded:
            self.assertIsNone(e["layer_sha256"])
            self.assertIsNone(e["renderer"])
            self.assertEqual(e["html_sha256"], base.get(e["source_id"])["html_sha256"])
            self.assertEqual(e["assessment"], base.get(e["source_id"])["assessment"])
        code, out = cf(self.root, "sourceset", "render", "new")
        self.assertEqual(code, 0, out)
        for e in new_ss.latest():
            self.assertEqual(e["renderer"], rh.RENDERER)
            self.assertEqual(e["layer_sha256"], base.get(e["source_id"])["layer_sha256"])
        self.assertEqual(old_ss.latest().get("enwiki-1001-2002")["renderer"], "web-html/1")
        self.assertEqual(new_ss.read_state()["commands"]["create"]["from"]["id"], "old")
        self.assertTrue(older.is_dir())

    def test_s1_seeding_refuses_bytes_that_do_not_match_and_a_family_change(self):
        self.enumerate("--limit", "1")
        self.fetch()
        (self.ss.source_dir("enwiki-1001-2002") / "body.html").write_text("tampered")
        code, out = cf(self.root, "sourceset", "create", "new", "--from", "wpm")
        self.assertEqual(code, 2, out)
        self.assertIn("do not match", out)
        self.assertFalse(self.paths.sourceset_manifests("new"))
        code, out = cf(self.root, "sourceset", "create", "new", "--from", "wpm",
                       "--family", "dewiki")
        self.assertEqual(code, 2, out)


def _tree(html: str):
    """``(root, raw)`` of the renderer's tree, and a compact dump of it for assertions."""
    builder = rh._TreeBuilder()
    builder.feed(html)
    root, raw = builder.finish()

    def dump(node):
        out = []
        for child in node.children:
            if isinstance(child, tuple):
                out.append(raw[child[0]:child[1]])
            else:
                out.append((child.tag, child.ns, dump(child)))
        return out
    return dump(root)


class TestFieldS14SelfClosingInForeignContent(unittest.TestCase):

    def test_s14_a_mathml_or_svg_self_closing_tag_holds_nothing(self):
        tree = _tree('<math alttext="a b"><mrow><mi>a</mi><mspace width="1em"/><mi>b</mi>'
                     '</mrow></math>')
        (_math, ns, (mrow,)), = tree
        self.assertEqual(ns, "mathml")
        self.assertEqual(mrow, ("mrow", "mathml", [("mi", "mathml", ["a"]),
                                                    ("mspace", "mathml", []),
                                                    ("mi", "mathml", ["b"])]))
        svg = _tree('<svg><rect/><text>t</text></svg>')
        self.assertEqual(svg, [("svg", "svg", [("rect", "svg", []), ("text", "svg", ["t"])])])

    def test_s14_html_keeps_ignoring_the_flag_and_integration_points_are_html(self):
        self.assertEqual(_tree("<p>x<span/>y</p>"), [("p", "html", ["x", ("span", "html",
                                                                         ["y"])])])
        tree = _tree('<svg><foreignObject><span/>z</foreignObject></svg>')
        self.assertEqual(tree, [("svg", "svg", [("foreignobject", "svg",
                                                  [("span", "html", ["z"])])])])
        tree = _tree('<math><mtext><span/>w</mtext><mi><mglyph/>v</mi></math>')
        self.assertEqual(tree, [("math", "mathml", [
            ("mtext", "mathml", [("span", "html", ["w"])]),
            ("mi", "mathml", [("mglyph", "mathml", []), "v"])])])
        tree = _tree('<math><mrow><div/>u</mrow></math>')    # a breakout tag is HTML
        self.assertEqual(tree, [("math", "mathml", [("mrow", "mathml",
                                                      [("div", "html", ["u"])])])])

    def test_s14_the_views_of_a_formula_with_an_mspace_are_unchanged(self):
        r = rh.render('<p>Let <span class="mwe-math-element"><math alttext="{\\displaystyle '
                      'a\\,b}"><semantics><mrow><mi>a</mi><mspace width="0.167em"/><mi>b</mi>'
                      '</mrow><annotation encoding="application/x-tex">{\\displaystyle a\\,b}'
                      '</annotation></semantics></math></span> be given.</p>')
        self.assertEqual(r.layer, "Let ab{\\displaystyle a\\,b} be given.")
        self.assertEqual(r.text, "Let $a\\,b$ be given.\n")
        self.assertEqual([f["alttext"] for f in r.formulas], ["{\\displaystyle a\\,b}"])


class TestFieldS13FormulasWrittenAsHtml(unittest.TestCase):

    def test_s13_a_block_holding_only_a_texhtml_formula_is_math(self):
        # a partition of an interval, indented, written with {{math}} (no TeX at all)
        r = rh.render('<p>A partition of an interval is a finite sequence of numbers of '
                      'the form</p>\n<dl><dd><span class="texhtml"><i>a</i> = <i>x</i><sub>0'
                      '</sub> &lt; <i>x</i><sub>1</sub> &lt; ... &lt; <i>x</i><sub><i>n</i>'
                      '</sub> = <i>b</i></span></dd></dl>\n<h2 id="R">Refinement</h2>\n'
                      '<p>Given <span class="texhtml mvar" style="font-style:italic;">P</span>'
                      ', a refinement adds points.</p>\n<p>History of the widget idea, told '
                      'without any formula at all.</p>')
        blocks = {r.layer[p["span"][0]:p["span"][0] + 12]: p for p in r.paragraphs}
        dd = blocks["a = x0 < x1 "]
        self.assertEqual((dd["block"], dd["formulas"], dd["texhtml"], dd["math"]),
                         ("dd", 0, 1, True))
        mvar = blocks["Given P, a r"]
        self.assertEqual((mvar["texhtml"], mvar["math"]), (1, True))
        prose = blocks["History of t"]
        self.assertEqual((prose["texhtml"], prose["math"]), (0, False))
        self.assertEqual(r.counts["texhtml"], 2)
        self.assertEqual(r.formulas, [])               # no TeX: no formulas.json formula
        self.assertNotIn("$", r.text)

    def test_s13_nested_texhtml_counts_once_and_mathml_still_counts_as_formulas(self):
        r = rh.render(f'<p>Both <span class="texhtml"><span class="texhtml">x</span> + 1</span>'
                      f' and {math("y")} appear.</p>')
        (p,) = r.paragraphs
        self.assertEqual((p["formulas"], p["texhtml"], p["math"]), (1, 1, True))


class TestFieldBoldLabelsAreNoDefinitions(unittest.TestCase):
    """The check found bold_definition passages whose bold was only a label."""

    def finder_terms(self, html: str) -> list:
        r = rh.render('<p>The widget article opens with this lead paragraph here.</p>\n'
                      '<h2 id="S">Section</h2>\n' + html)
        return [f["term"] for f in r.finders if f["finder"] == "bold_definition"]

    def test_a_label_only_bold_makes_no_passage(self):
        for label in ("Proof", "Proof.", "Example.", "Definition:", "Example 2:", "Case 1:",
                      "Proof of the lemma.", "Theorem (Widget lemma).", "Solution:",
                      "Base case:", "Remark 3.1."):
            with self.subTest(label=label):
                self.assertEqual(self.finder_terms(
                    f"<p><b>{label}</b> Split the widget along its spine.</p>"), [])

    def test_a_symbol_bold_makes_no_passage(self):
        for symbol in ("R", "M", "1'", "2'", "(i)", "v"):
            with self.subTest(symbol=symbol):
                self.assertEqual(self.finder_terms(
                    f"<p>Let <b>{symbol}</b> be a widget ring.</p>"), [])

    def test_the_first_bold_that_names_a_term_is_the_term(self):
        self.assertEqual(self.finder_terms(
            "<p><b>Definition:</b> A <b>widget group</b> is a group with a gadget.</p>"),
            ["widget group"])
        self.assertEqual(self.finder_terms(
            "<p>A <b>solution</b> of a widget equation is a value that satisfies it.</p>"),
            ["solution"])
        self.assertEqual(self.finder_terms("<p>The <b>Pi</b> gadget.</p>"), ["Pi"])

    def test_every_label_is_in_the_vocabulary_document(self):
        text = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")
        for label in sorted(vocab.BOLD_LABELS | vocab.BOLD_LABELS_PUNCTUATED):
            with self.subTest(label=label):
                self.assertIn(f"`{label}`", text)


#: the page every view of which is pinned to the renderer id: the real-shaped
#: page, the suite's page, a fixture page, and one of each field case
GOLDEN_EXTRA = (
    '<h2 id="More">More</h2>\n'
    '<p><b>Proof.</b> Split the widget; let <b>R</b> be its ring.</p>\n'
    '<dl><dd><span class="texhtml"><i>a</i> = <i>x</i><sub>0</sub> &lt; <i>b</i></span></dd>'
    '</dl>\n<p>Spaced <span class="mwe-math-element"><math alttext="{\\displaystyle a\\,b}">'
    '<mrow><mi>a</mi><mspace width="0.167em"/><mi>b</mi></mrow></math></span> out.</p>\n')

#: sha256 of every file write_rendering writes for the golden page, per renderer id
GOLDEN_VIEWS = {
    "web-html/2": {
        "body.html": "adf883de75208f77f7c558b176b5c833cc2ed1d164ee81ed95111227eff5bb8f",
        "finders.json": "dae197e64e3d8a4b9c9a384ef0dc46baf3182ff033e3da9899db771bdccdb7da",
        "formulas.json": "8539aec3aeaafa945c013ed34c78008fd8fa805a2014c55292c0ce398eb7eb97",
        "head.txt": "1838f6844084223bffbd0be45a176f327587984f1cd7372531901bda093df2fb",
        "layer.txt": "9a95ca0666dcf41f40c93868ac1c407ce27bcdcd3ececeaee1dad8fdd3494909",
        "layer_map.json": "214d1b6b84b9e0584a238f518ca34a29909150feea142a23be4a822c311461bb",
        "paragraphs.json": "bc3b030c9c6396f3ac3156d2c3ab37ae740a1cfe748afae78450c1183dcb9ddf",
        "render.json": "017ac93754ddca9b914dacc856d13124573e4fd558c9e374e9415dddc0c78dc1",
        "sections.json": "b70c3527079235f0e5dbcd659cd57fcdc0575f8db837734270caa6f01d664f07",
        "text.txt": "3e0a85699ad46f101f46a793a43edd7726846719d3caf8b36af8e88cfcbf63aa",
    },
}


def golden_rendering():
    return rh.render(rh.canonical_body(
        (MW_PAGE + PAGE + fixture_body(2004) + GOLDEN_EXTRA).encode("utf-8")))


class TestFieldL1EveryViewIsPinnedToTheRendererId(_FieldCase):
    """L1: a view's format changed (cf-layer-map/1 to /2) under one renderer id."""

    def test_l1_every_view_of_the_golden_page_is_the_renderer_ids(self):
        self.assertIn(rh.RENDERER, GOLDEN_VIEWS,
                      "a new renderer id: add its row of view shas to GOLDEN_VIEWS")
        r = golden_rendering()
        for name, content in sorted(r.files().items()):
            with self.subTest(view=name):
                self.assertEqual(
                    hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    GOLDEN_VIEWS[rh.RENDERER].get(name),
                    f"{name} changed under {rh.RENDERER}: a change to any view, or to the "
                    f"layout of its file, is a new renderer id -- bump render_html.RENDERER "
                    f"and cflib.vocab.RENDERER_WEB_HTML and add a GOLDEN_VIEWS row")
        self.assertEqual(set(r.files()), set(rh.RENDER_FILES))

    def test_l1_a_map_another_renderer_wrote_is_refused(self):
        out = _scratch(self)
        rh.write_rendering(out, golden_rendering())
        self.assertTrue(rh.LayerMap.read(out).segments)
        path = out / "layer_map.json"
        good = path.read_text(encoding="utf-8")
        data = json.loads(good)
        for renderer in ("web-html/1", None):
            with self.subTest(renderer=renderer):
                changed = dict(data, renderer=renderer) if renderer else \
                    {k: v for k, v in data.items() if k != "renderer"}
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(rh.RenderError, "renderer"):
                    rh.LayerMap.read(out)
        path.write_text(good, encoding="utf-8")
        record = json.loads((out / "render.json").read_text(encoding="utf-8"))
        (out / "render.json").write_text(json.dumps(dict(record, renderer="web-html/1")),
                                         encoding="utf-8")
        with self.assertRaisesRegex(rh.RenderError, "render.json names renderer"):
            rh.LayerMap.read(out)

    def test_l1_status_verify_names_a_view_of_another_renderer(self):
        self.enumerate("--limit", "1")
        self.fetch()
        cf(self.root, "sourceset", "render", "wpm")
        code, out = cf(self.root, "sourceset", "status", "wpm", "--verify")
        self.assertEqual(code, 0, out)
        d = self.ss.source_dir("enwiki-1001-2002")
        data = json.loads((d / "layer_map.json").read_text(encoding="utf-8"))
        (d / "layer_map.json").write_text(json.dumps(dict(data, renderer="web-html/1")),
                                          encoding="utf-8")
        code, out = cf(self.root, "sourceset", "status", "wpm", "--verify")
        self.assertEqual(code, 1, out)
        self.assertIn("layer_map.json says renderer 'web-html/1'", out)


class TestFieldS11ManifestDeltas(unittest.TestCase):
    """S11: every version was a full copy -- ~150 copies of ten megabytes for one fetch."""

    def setUp(self):
        self.root = _workspace(self)
        self.paths = WorkspacePaths(self.root)
        self.ss = Sourceset.create(self.paths, "wpm", "enwiki")
        self.base = self.ss.write_version(self.ss.latest(), [_entry(n) for n in range(1, 41)])

    def test_s11_a_small_change_is_a_delta_that_reads_back_whole(self):
        self.assertFalse(self.base.is_delta)              # 40 new of 40: a snapshot
        filled = dict(self.base.get("enwiki-1003-2003"), raw_sha256="a" * 64)
        m = self.ss.write_version(self.base, [*(e for e in self.base
                                                if e["source_id"] != filled["source_id"]),
                                               filled])
        self.assertTrue(m.is_delta)
        lines = m.path.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        header = json.loads(lines[0])
        self.assertEqual((header["schema"], header["sources"], header["changed"]),
                         ("cf-sourceset-delta/1", 40, 1))
        self.assertEqual(header["previous_sha256"], self.base.sha256)
        again = self.ss.load(m.version)
        self.assertEqual(len(again), 40)
        self.assertEqual(again.get("enwiki-1003-2003")["raw_sha256"], "a" * 64)
        self.assertEqual(again.sha256, websource_sha(m.path))
        self.assertEqual(self.ss.pinned(m.version, m.sha256).entries, again.entries)
        # the planner's and the bench's readers see the same version
        from bench import inputs as bench_inputs
        from locate import inputs as locate_inputs
        for reader in (locate_inputs.manifest, bench_inputs.load_manifest):
            with self.subTest(reader=reader.__module__):
                read = reader(self.paths, "wpm", m.version)
                self.assertEqual((read.sha256, read.entries), (m.sha256, again.entries))

    def test_s11_a_whole_fetch_writes_tens_of_versions_not_tens_of_copies(self):
        m = self.base
        for n in range(1, 41):
            sid = f"enwiki-{1000 + n}-{2000 + n}"
            m = self.ss.write_version(m, [dict(e, raw_sha256="b" * 64) if e["source_id"] == sid
                                          else e for e in m])
        snapshot = self.paths.sourceset_manifest("wpm", 2).stat().st_size
        total = sum(p.stat().st_size for _n, p in self.paths.sourceset_manifests("wpm"))
        self.assertLess(total, 3 * snapshot)             # was 41 snapshots
        self.assertTrue(all(e["raw_sha256"] == "b" * 64 for e in self.ss.latest()))

    def test_s11_a_long_chain_ends_in_a_snapshot(self):
        from sources import sourceset as sourceset_mod
        with mock.patch.object(sourceset_mod, "MAX_DELTA_CHAIN", 2):
            m = self.base
            kinds = []
            for n in range(1, 6):
                sid = f"enwiki-{1000 + n}-{2000 + n}"
                m = self.ss.write_version(m, [dict(e, raw_sha256="c" * 64)
                                              if e["source_id"] == sid else e for e in m])
                kinds.append(m.is_delta)
            self.assertEqual(kinds, [True, True, False, True, True])
            self.assertEqual(self.ss.load(m.version).chain, 2)

    def test_s11_a_broken_chain_is_refused(self):
        m = self.base
        for n in (1, 2):
            sid = f"enwiki-{1000 + n}-{2000 + n}"
            m = self.ss.write_version(m, [dict(e, raw_sha256="d" * 64) if e["source_id"] == sid
                                          else e for e in m])
        snapshot = self.paths.sourceset_manifest("wpm", 2)
        middle = self.paths.sourceset_manifest("wpm", 3)
        text = middle.read_text()
        # a delta that changes a recorded value is refused
        lines = text.splitlines()
        row = json.loads(lines[1])
        changed_path = self.paths.sourceset_manifest("wpm", 4)
        last = changed_path.read_text().splitlines()
        bad = dict(json.loads(last[1]), source_id=row["source_id"], pageid=row["pageid"],
                   revision=row["revision"], title=row["title"], raw_sha256="e" * 64)
        changed_path.write_text(last[0] + "\n" + sourceset_canonical(bad) + "\n")
        with self.assertRaisesRegex(SourcesetError, "never changes"):
            self.ss.load(4)
        # a rewritten snapshot breaks every delta after it
        snapshot.write_text(snapshot.read_text().replace('"sources":40', '"sources":40 '))
        with self.assertRaisesRegex(SourcesetError, "rewritten"):
            self.ss.load(3)
        middle.unlink()
        with self.assertRaisesRegex(SourcesetError, "not on disk"):
            self.ss.load(4)


def sourceset_canonical(obj: dict) -> str:
    from sources.sourceset import canonical_line
    return canonical_line(obj)


class TestFieldS9S10S12TheStateAndTheStatus(_FieldCase):

    def adopt_older_revision(self):
        pins = _scratch(self)
        body = {"parse": {"revid": 1990, "text": "<p>The <b>widget lemma</b> as a consumer "
                                                 "pinned it, at an older revision.</p>"}}
        (pins / "b.json").write_text(json.dumps(body))
        (pins / "pins.jsonl").write_text(json.dumps(
            {"pageid": 1001, "revision": 1990, "bytes": "b.json",
             "assessment": {"class": "B", "importance": "high"}}) + "\n")
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from",
                       str(pins / "pins.jsonl"), "--consumer", "wikilean")
        self.assertEqual(code, 0, out)

    def test_s9_status_counts_pages_once_whatever_their_revisions(self):
        self.enumerate()
        self.adopt_older_revision()
        counts = self.ss.read_state()["counts"]
        self.assertEqual((counts["sources"], counts["pages"]), (4, 3))
        self.assertEqual(sum(counts["by_class"].values()), 3)
        self.assertEqual(sum(counts["by_importance"].values()), 3)
        self.assertEqual(counts["by_importance"].get("high"), 1)
        code, out = cf(self.root, "sourceset", "status", "wpm")
        self.assertIn("pages by class", out)

    def test_s10_the_history_keeps_every_run_and_a_plain_status_writes_nothing(self):
        self.enumerate("--limit", "1")
        self.fetch()
        cf(self.root, "sourceset", "render", "wpm")
        written = self.ss.read_state()
        code, out = cf(self.root, "sourceset", "render", "wpm")      # a no-op re-run
        self.assertEqual(code, 0, out)
        state = self.ss.read_state()
        runs = [(h["command"], h["wrote_version"]) for h in state["history"]]
        self.assertEqual(runs, [("create", True), ("enumerate-wikiproject", True),
                                ("fetch", True), ("render", True), ("render", False)])
        rendered = [h for h in state["history"] if h["command"] == "render"]
        self.assertEqual((rendered[0]["rendered"], rendered[1]["rendered"]), (1, 0))
        self.assertEqual(rendered[0]["version"], written["manifest"]["version"])
        before = self.paths.sourceset_state_json("wpm").read_bytes()
        code, out = cf(self.root, "sourceset", "status", "wpm")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.paths.sourceset_state_json("wpm").read_bytes(), before)
        code, out = cf(self.root, "sourceset", "status", "wpm", "--verify")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ss.read_state()["history"][-1]["command"], "status")
        with self.assertRaises(SourcesetError):
            self.ss.record_state("history", {"x": 1})

    def test_s10_the_history_is_bounded(self):
        from sources import sourceset as sourceset_mod
        with mock.patch.object(sourceset_mod, "MAX_HISTORY", 3):
            for _ in range(5):
                self.ss.refresh_state(command="probe")
        state = self.ss.read_state()
        self.assertEqual(len(state["history"]), 3)
        self.assertEqual(state["history_dropped"], 3)      # create + 5 probes - 3 kept

    def test_s12_cf_status_shows_the_sourcesets(self):
        self.enumerate()
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cf_main.main(["--workspace", str(self.root), "status"])
        self.assertEqual(code, 0, out.getvalue())
        text = out.getvalue()
        self.assertIn("sourceset", text)
        self.assertIn("wpm", text)
        self.assertIn("enumerate-wikiproject", text)
        self.assertNotIn("next:  cf onboard", text)
        out = io.StringIO()
        with redirect_stdout(out):
            cf_main.main(["--workspace", str(self.root), "status", "--json"])
        (row,) = json.loads(out.getvalue())["sourcesets"]
        self.assertEqual((row["id"], row["counts"]["sources"], row["counts"]["pages"]),
                         ("wpm", 3, 3))
        self.assertEqual(row["version"], row["latest_on_disk"])


class TestFieldS2FetchASelection(_FieldCase):
    """S2: a pilot of 80 articles meant fetching all 30k sources, or a script."""

    def test_s2_only_the_selected_sources_are_fetched(self):
        self.enumerate()
        code, out = self.fetch("--source", "enwiki-1002-2003")
        self.assertEqual(code, 0, out)
        m = self.ss.latest()
        self.assertEqual({e["source_id"] for e in m if e["raw_sha256"]}, {"enwiki-1002-2003"})
        self.assertEqual(self.ss.read_state()["commands"]["fetch"]["selected"], 1)
        pick = _scratch(self) / "pick.txt"
        pick.write_text("# the pilot\n1003   # a page id: the source a run plans\n"
                        "enwiki-1002-2003\n\n")
        code, out = cf(self.root, "sourceset", "fetch", "wpm", "--select", str(pick))
        self.assertEqual(code, 0, out)                       # the dry run counts what is left
        self.assertIn("1 source(s) of wpm have no bytes yet of the 2 selected", out)
        code, out = self.fetch("--select", str(pick))
        self.assertEqual(code, 0, out)
        m = self.ss.latest()
        self.assertEqual({e["source_id"] for e in m if e["raw_sha256"]},
                         {"enwiki-1002-2003", "enwiki-1003-2004"})
        self.assertIsNone(m.get("enwiki-1001-2002")["raw_sha256"])
        as_json = _scratch(self) / "pick.json"
        as_json.write_text(json.dumps(["enwiki-1001-2002"]))
        code, out = self.fetch("--select", str(as_json))
        self.assertEqual(code, 0, out)
        self.assertIsNotNone(self.ss.latest().get("enwiki-1001-2002")["raw_sha256"])

    def test_s2_a_selection_naming_nothing_held_is_refused_before_any_request(self):
        self.enumerate()
        pick = _scratch(self) / "pick.txt"
        pick.write_text("enwiki-9999-1\n4242\n")
        empty = _scratch(self)
        code, out = cf(self.root, "sourceset", "fetch", "wpm", "--select", str(pick),
                       "--fixtures", str(empty))
        self.assertEqual(code, 2, out)
        self.assertIn("holds no source or page enwiki-9999-1, 4242", out)
        self.assertEqual(self.ss.latest().version, 2)


def _record(directory: Path, name: str, query: dict, answer: dict) -> None:
    """One recorded API answer in FixtureTransport's format."""
    (directory / f"{name}.json").write_text(json.dumps({"query": query, "json": answer}),
                                            encoding="utf-8")


def _fixtures_with(case, extra: dict[str, tuple[dict, dict]]) -> Path:
    """The suite's recordings plus ``extra`` ``{name: (query, answer)}``."""
    directory = _scratch(case)
    for p in FIXTURES.glob("*.json"):
        shutil.copy(p, directory / p.name)
    for name, (query, answer) in extra.items():
        _record(directory, name, query, answer)
    return directory


def _revids_query(*revisions: int) -> dict:
    return {"action": "query", "revids": "|".join(str(r) for r in sorted(revisions)),
            "prop": "revisions", "rvprop": "ids", "format": "json", "formatversion": "2"}


class TestFieldS3S4AdoptWithoutBytes(_FieldCase):
    """S3: a consumer pins title + revision and holds no parse bytes; S4: an adopted
    revision lost its page's assessment and QID."""

    def pins(self, rows) -> Path:
        path = _scratch(self) / "pins.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path

    def test_s3_s4_a_pin_without_bytes_is_fetched_at_exactly_its_revision(self):
        self.enumerate()
        pins = self.pins([{"pageid": 1001, "revision": 1990, "title": "Widget lemma"}])
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins),
                       "--consumer", "wikilean")
        self.assertEqual(code, 0, out)
        self.assertIn("1 without bytes", out)
        e = self.ss.latest().get("enwiki-1001-1990")
        self.assertEqual((e["origin"], e["raw_sha256"], e["adopted_from"]["consumer"]),
                         ("adopted", None, "wikilean"))
        # S4: the page's assessment and QID, which the pin did not carry
        self.assertEqual((e["assessment"], e["wikidata"]),
                         ({"class": "B", "importance": "high"}, "Q99999001"))
        self.assertFalse(self.ss.source_dir("enwiki-1001-1990").exists())
        self.assertEqual(self.ss.latest().preferred()[1001], "enwiki-1001-1990")
        # the fetch fetches oldid 1990 and leaves the page's enumerated revision alone
        answer = json.loads((FIXTURES / "parse-2002.json").read_text())["json"]
        answer["parse"]["revid"] = 1990
        fixtures = _fixtures_with(self, {"parse-1990": (websource.parse_params(1990), answer)})
        code, out = self.fetch(fixtures=fixtures)
        self.assertEqual(code, 0, out)
        m = self.ss.latest()
        self.assertIsNotNone(m.get("enwiki-1001-1990")["raw_sha256"])
        self.assertIsNone(m.get("enwiki-1001-2002")["raw_sha256"])
        self.assertIn("left to their consumer's adopted revision", out)
        self.assertEqual(self.ss.source_problems(m.get("enwiki-1001-1990")), [])
        # adopting the same pins again adds nothing
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins))
        self.assertEqual(code, 0, out)
        self.assertIn("1 already held", out)

    def test_s3_a_title_pin_has_its_page_resolved_from_its_revision(self):
        self.enumerate()
        pins = self.pins([{"title": "Widget lemma (moved)", "revision": 1990},
                          {"title": "Gadget theorem", "revision": 2003}])
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins))
        self.assertEqual(code, 2, out)                          # no --execute, no fixtures
        self.assertIn("--execute", out)
        answer = {"query": {"pages": [
            {"pageid": 1001, "ns": 0, "title": "Widget lemma", "revisions": [{"revid": 1990}]},
            {"pageid": 1002, "ns": 0, "title": "Gadget theorem",
             "revisions": [{"revid": 2003}]}]}}
        # revision 2003 is the enumerated one, so the sourceset names its page already:
        # only revision 1990 is asked
        fixtures = _fixtures_with(self, {"revids": (_revids_query(1990), answer)})
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins),
                       "--fixtures", str(fixtures))
        self.assertEqual(code, 0, out)
        self.assertIn("its page 1001 is now 'Widget lemma'", out)
        m = self.ss.latest()
        moved = m.get("enwiki-1001-1990")
        self.assertEqual((moved["title"], moved["wikidata"]), ("Widget lemma (moved)",
                                                               "Q99999001"))
        same = m.get("enwiki-1002-2003")                         # the enumerated revision
        self.assertEqual((same["origin"], same["adopted_from"]["consumer"]),
                         ("enumerated", "consumer"))
        adopt = self.ss.read_state()["commands"]["adopt"]
        self.assertEqual((adopt["resolved_by_revision"], adopt["resolved_from_sourceset"]), (1, 1))

    def test_a_re_run_of_title_pins_asks_nothing(self):
        """The same title pins again: every one is held, so no request and exit 0, with or
        without --execute (CLAUDE.md section 4: every command may be re-run)."""
        self.enumerate()
        pins = self.pins([{"title": "Widget lemma (moved)", "revision": 1990}])
        answer = {"query": {"pages": [
            {"pageid": 1001, "ns": 0, "title": "Widget lemma", "revisions": [{"revid": 1990}]}]}}
        fixtures = _fixtures_with(self, {"revids": (_revids_query(1990), answer)})
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins),
                       "--fixtures", str(fixtures))
        self.assertEqual(code, 0, out)
        version = self.ss.latest().version
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins))
        self.assertEqual(code, 0, out)
        self.assertIn("1 already held", out)
        self.assertEqual(self.ss.latest().version, version)
        empty = _scratch(self)                     # a replay that holds no answer at all
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins),
                       "--fixtures", str(empty))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ss.read_state()["commands"]["adopt"]["resolved_by_revision"], 0)

    def test_s3_a_revision_that_does_not_exist_is_refused(self):
        self.enumerate()
        pins = self.pins([{"title": "Widget lemma", "revision": 77}])
        answer = {"query": {"badrevids": {"77": {"revid": 77, "missing": True}}}}
        fixtures = _fixtures_with(self, {"revids": (_revids_query(77), answer)})
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins),
                       "--fixtures", str(fixtures))
        self.assertEqual(code, 2, out)
        self.assertIn("no revision of", out)
        self.assertNotIn("enwiki-1001-77", self.ss.latest().entries)

    def test_s3_a_page_outside_the_sourceset_can_be_left_out(self):
        self.enumerate()
        pins = self.pins([{"pageid": 4242, "revision": 9}, {"pageid": 1003, "revision": 2004}])
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins),
                       "--only-known-pages")
        self.assertEqual(code, 0, out)
        self.assertIn("skipped, no other source of its page here", out)
        self.assertNotIn("enwiki-4242-9", self.ss.latest().entries)
        self.assertTrue(self.ss.latest().get("enwiki-1003-2004").get("adopted_from"))

    def test_s3_a_pin_names_its_page(self):
        pins = self.pins([{"revision": 5}])
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins))
        self.assertEqual(code, 2, out)
        self.assertIn("a pageid or a title", out)


class TestFieldS4AnEnumerationFillsAnAdoptedRevision(_FieldCase):

    def test_s4_a_revision_adopted_before_the_listing_takes_its_metadata_from_it(self):
        pins = _scratch(self) / "pins.jsonl"
        pins.write_text(json.dumps({"pageid": 1001, "revision": 1990}) + "\n")
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(pins))
        self.assertEqual(code, 0, out)
        e = self.ss.latest().get("enwiki-1001-1990")
        self.assertEqual((e["assessment"], e["wikidata"], e["title"]), (None, None, None))
        code, out = self.enumerate()
        self.assertEqual(code, 0, out)
        self.assertIn("1 adopted revision(s) took their page's", out)
        e = self.ss.latest().get("enwiki-1001-1990")
        self.assertEqual((e["assessment"], e["wikidata"], e["title"]),
                         ({"class": "B", "importance": "high"}, "Q99999001", "Widget lemma"))
        self.assertNotIn("enwiki-1001-2002", self.ss.latest().entries)


def _listing_query(**cont) -> dict:
    return {"action": "query", "list": "projectpages", "wppprojects": "Mathematics",
            "wppassessments": "1", "wpplimit": "500", "format": "json", "formatversion": "2",
            **cont}


def _row(pageid, title, cls, importance=None, ns=0):
    return {"pageid": pageid, "ns": ns, "title": title,
            "assessment": {"class": cls, "importance": importance or "Low"}}


#: a synthetic WikiProject whose listing holds every kind of page the field met
FLAG_LISTING = [
    (_listing_query(), {"continue": {"continue": "-||", "wppcontinue": "1103"},
                        "query": {"projects": {"Mathematics": [
                            _row(1101, "Widget group", "B", "High"),
                            _row(1102, "Widget (disambiguation)", "Disambig", "NA"),
                            _row(5101, "Talk:Widget group", "B", "High", ns=1),
                            _row(5102, "Category:Widgets", "Category", "NA", ns=14),
                            _row(5103, "Template:Widget", "Template", "NA", ns=10)]}}}),
    (_listing_query(**{"continue": "-||", "wppcontinue": "1103"}),
     {"query": {"projects": {"Mathematics": [
         _row(1103, "List of widgets", "List", "Low"),
         _row(1104, "Old widget name", "Redirect", "NA"),
         _row(1105, "Ada Widgetson", "Start", "Low"),
         _row(1106, "Widget theorem", "Start", "Mid")]}}}),
]
FLAG_IDS = "1101|1102|1103|1104|1105|1106"


def _info_query(ids=FLAG_IDS, **cont) -> dict:
    return {**wikinet.PAGE_INFO_PARAMS, "pageids": ids, **cont}


#: the page infos, the second half of one page's answer in a continuation (S8)
FLAG_INFOS = [
    (_info_query(), {"continue": {"continue": "||", "rvcontinue": "1105|3105"},
                     "query": {"pages": [
                         {"pageid": 1101, "ns": 0, "title": "Widget group",
                          "revisions": [{"revid": 3101}],
                          "pageprops": {"wikibase_item": "Q99999101"}},
                         {"pageid": 1102, "ns": 0, "title": "Widget (disambiguation)",
                          "revisions": [{"revid": 3102}],
                          "pageprops": {"disambiguation": ""}},
                         {"pageid": 1103, "ns": 0, "title": "List of widgets",
                          "revisions": [{"revid": 3103}]},
                         {"pageid": 1104, "ns": 0, "title": "Old widget name",
                          "redirect": True, "revisions": [{"revid": 3104}]},
                         {"pageid": 1105, "ns": 0, "title": "Ada Widgetson",
                          "pageprops": {"wikibase_item": "Q99999105"}},
                         {"pageid": 1106, "ns": 0, "title": "Widget theorem", "redirect": True,
                          "revisions": [{"revid": 3106}]}]}}),
    (_info_query(**{"continue": "||", "rvcontinue": "1105|3105"}),
     {"query": {"pages": [{"pageid": 1105, "ns": 0, "title": "Ada Widgetson",
                           "revisions": [{"revid": 3105}]}]}}),
]


def _flag_fixtures(case, *, listing=True, infos=True, first_answer_only=False) -> Path:
    directory = _scratch(case)
    recorded = []
    if listing:
        recorded += FLAG_LISTING[:1] if first_answer_only else FLAG_LISTING
    if infos:
        recorded += FLAG_INFOS
    for n, (query, answer) in enumerate(recorded):
        _record(directory, f"r{n}", query, answer)
    return directory


class TestFieldS5S6S8FlagsAndCounts(_FieldCase):
    """S5: redirects, disambiguation pages, lists and biographies went in unflagged;
    S6: the listing's other rows were dropped uncounted; S8: page info ignored
    ``continue``."""

    def test_s5_s6_s8_an_enumeration_flags_counts_and_follows_continuations(self):
        code, out = self.enumerate(fixtures=_flag_fixtures(self))
        self.assertEqual(code, 0, out)
        self.assertIn("3 row(s) of the listing are no article", out)
        self.assertIn("by namespace: 1 1, 10 1, 14 1", out)
        m = self.ss.latest()
        flags = {e["pageid"]: e["flags"] for e in m}
        self.assertEqual(sorted(flags), [1101, 1102, 1103, 1104, 1105, 1106])   # S8: 1105 too
        self.assertEqual(m.get("enwiki-1105-3105")["wikidata"], "Q99999105")
        self.assertEqual(flags[1101], {"redirect": False, "disambiguation": False,
                                       "list": False, "biography": None})
        self.assertTrue(flags[1102]["disambiguation"])            # pageprops and class
        self.assertTrue(flags[1103]["list"])
        self.assertTrue(flags[1104]["redirect"])                  # info and class
        self.assertTrue(flags[1106]["redirect"])                  # info alone
        self.assertEqual([p for p, f in flags.items() if not any(f.values())], [1101, 1105])
        state = self.ss.read_state()["commands"]["enumerate-wikiproject"]
        self.assertEqual(state["non_article_rows"],
                         {"total": 3, "by_namespace": {"1": 1, "10": 1, "14": 1}})
        self.assertEqual(state["flagged"], {"redirect": 2, "disambiguation": 1, "list": 1})
        # a plain fetch leaves the redirect and disambiguation stubs out
        code, out = cf(self.root, "sourceset", "fetch", "wpm")
        self.assertIn("dry run: 3 source(s) of wpm have no bytes yet", out)
        code, out = cf(self.root, "sourceset", "fetch", "wpm", "--include-flagged")
        self.assertIn("dry run: 6 source(s)", out)

    def test_s5_the_biography_step_asks_wikidata(self):
        self.enumerate(fixtures=_flag_fixtures(self))
        code, out = cf(self.root, "sourceset", "flag-biographies", "wpm")
        self.assertEqual(code, 0, out)
        self.assertIn("2 Wikidata item(s): 1 query(ies)", out)
        query = ("SELECT ?item WHERE { VALUES ?item { wd:Q99999101 wd:Q99999105 } "
                 "?item wdt:P31 wd:Q5 . }")
        answers = _scratch(self)
        _record(answers, "p31", {"query": query, "format": "json"},
                {"results": {"bindings": [
                    {"item": {"type": "uri",
                              "value": "http://www.wikidata.org/entity/Q99999105"}}]}})
        code, out = cf(self.root, "sourceset", "flag-biographies", "wpm", "--fixtures",
                       str(answers))
        self.assertEqual(code, 0, out)
        m = self.ss.latest()
        self.assertIs(m.get("enwiki-1105-3105")["flags"]["biography"], True)
        self.assertIs(m.get("enwiki-1101-3101")["flags"]["biography"], False)
        self.assertIsNone(m.get("enwiki-1103-3103")["flags"]["biography"])   # no QID
        code, out = cf(self.root, "sourceset", "flag-biographies", "wpm", "--fixtures",
                       str(_scratch(self)))
        self.assertEqual(code, 0, out)                             # nothing left to ask
        # a determined flag never changes
        e = dict(m.get("enwiki-1105-3105"))
        e["flags"] = dict(e["flags"], biography=False)
        with self.assertRaisesRegex(SourcesetError, "flag biography"):
            self.ss.write_version(m, [e if x["source_id"] == e["source_id"] else x for x in m])

    def test_s5_a_sourceset_enumerated_before_flags_takes_them_at_the_next_listing(self):
        fam = websource.family("enwiki")
        old = websource.entry(fam, 1104, 3104, title="Old widget name",
                              assessment={"class": "Redirect", "importance": "na"})
        self.ss.write_version(self.ss.latest(), [old])
        code, out = self.enumerate(fixtures=_flag_fixtures(self))
        self.assertEqual(code, 0, out)
        e = self.ss.latest().get("enwiki-1104-3104")
        self.assertTrue(e["flags"]["redirect"])
        self.assertEqual(self.ss.read_state()["commands"]["enumerate-wikiproject"]
                         ["flags_filled"], 1)

    def test_s6_the_client_counts_what_it_skips(self):
        t = wikinet.FixtureTransport(_flag_fixtures(self, infos=False))
        skipped: dict = {}
        pages = list(wikinet.project_pages(t, SITE, "Mathematics", skipped=skipped))
        self.assertEqual(len(pages), 6)
        self.assertEqual(skipped, {1: 1, 14: 1, 10: 1})

    def test_s8_a_repeating_page_info_continuation_is_refused(self):
        fake = _Fake(lambda p: {"continue": {"rvcontinue": "1"},
                                "query": {"pages": [{"pageid": 1, "revisions": []}]}})
        with self.assertRaisesRegex(wikinet.WikiError, "repeats"):
            wikinet.page_infos(fake, SITE, [1])


class TestFieldS7EnumerationCheckpoints(_FieldCase):
    """S7: 666 requests and twelve minutes, all or nothing."""

    def test_s7_a_failed_page_info_request_resumes_without_listing_again(self):
        code, out = self.enumerate(fixtures=_flag_fixtures(self, infos=False))
        self.assertEqual(code, 2, out)
        self.assertIn("are kept in", out)
        checkpoint = self.paths.sourceset_enumerate_checkpoint("wpm")
        data = json.loads(checkpoint.read_text())
        self.assertTrue(data["listing"]["done"])
        self.assertEqual(len(data["listing"]["pages"]), 6)
        self.assertEqual(self.ss.versions(), [1])                  # nothing half-written
        # the listing is not recorded here: a request for it would be refused
        code, out = self.enumerate(fixtures=_flag_fixtures(self, listing=False))
        self.assertEqual(code, 0, out)
        self.assertIn("resuming the enumeration", out)
        self.assertEqual(len(self.ss.latest()), 6)
        self.assertFalse(checkpoint.exists())
        self.assertTrue(self.ss.read_state()["commands"]["enumerate-wikiproject"]["resumed"])

    def test_s7_a_listing_cut_off_resumes_from_its_continuation(self):
        code, out = self.enumerate(fixtures=_flag_fixtures(self, infos=False,
                                                           first_answer_only=True))
        self.assertEqual(code, 2, out)
        data = json.loads(self.paths.sourceset_enumerate_checkpoint("wpm").read_text())
        self.assertEqual(data["listing"]["continue"], {"continue": "-||", "wppcontinue": "1103"})
        resume = _scratch(self)
        for n, (query, answer) in enumerate(FLAG_LISTING[1:] + FLAG_INFOS):
            _record(resume, f"r{n}", query, answer)
        code, out = self.enumerate(fixtures=resume)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.ss.latest()), 6)
        self.assertIn("3 row(s) of the listing are no article", out)

    def test_s7_a_checkpoint_of_another_project_is_refused_until_restarted(self):
        self.enumerate(fixtures=_flag_fixtures(self, infos=False))
        code, out = cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project",
                       "Physics", "--fixtures", str(_scratch(self)))
        self.assertEqual(code, 2, out)
        self.assertIn("not 'Physics'", out)
        code, out = self.enumerate("--restart", fixtures=_flag_fixtures(self))
        self.assertEqual(code, 0, out)
        self.assertNotIn("resuming", out)
        self.assertFalse(self.paths.sourceset_enumerate_checkpoint("wpm").exists())


class TestCreateFromIsReRunnable(_FieldCase):
    """``create --from`` wrote manifest-1 first and linked every source after it, so an
    interruption left a sourceset that every re-run refused as existing, with an empty
    manifest and half its directories linked (CLAUDE.md section 4: every command may be
    re-run)."""

    def test_an_interrupted_seeding_leaves_nothing_and_a_re_run_finishes(self):
        from cfcmd import sourceset as cmd
        self.enumerate()
        self.fetch()
        real, calls = cmd._link_or_copy, []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError("interrupted (simulated)")
            real(src, dst)

        with mock.patch.object(cmd, "_link_or_copy", flaky):
            with self.assertRaises(OSError):
                cf(self.root, "sourceset", "create", "new", "--from", "wpm")
        self.assertEqual(self.paths.sourceset_manifests("new"), [])
        self.assertFalse(self.paths.sourceset_dir("new").exists())
        code, out = cf(self.root, "sourceset", "status", "new")
        self.assertEqual(code, 2, out)                      # no such sourceset, not a broken one
        code, out = cf(self.root, "sourceset", "create", "new", "--from", "wpm")
        self.assertEqual(code, 0, out)
        self.assertIn("discarding .seeding-new/", out)
        seeded = Sourceset.open(self.paths, "new").latest()
        self.assertEqual(sorted(seeded.entries), sorted(self.ss.latest().entries))
        self.assertEqual([p.name for p in self.paths.sourcesets_dir.iterdir()
                          if p.name.startswith(".")], [])
        for e in seeded:
            self.assertEqual(Sourceset.open(self.paths, "new").source_problems(e, views=False),
                             [])
        code, out = cf(self.root, "sourceset", "create", "new", "--from", "wpm")
        self.assertEqual(code, 2, out)
        self.assertIn("already exists", out)

    def test_the_leftover_of_the_earlier_code_is_named(self):
        code, out = cf(self.root, "sourceset", "create", "new")      # an empty manifest-1
        self.assertEqual(code, 0, out)
        self.enumerate()
        with self.assertRaises(CfError) as ctx:
            cf_main.main(["--workspace", str(self.root), "sourceset", "create", "new",
                          "--from", "wpm"])
        self.assertIn("already exists", str(ctx.exception))
        self.assertIn("only an empty manifest-1", ctx.exception.hint)
        self.assertIn(str(self.paths.sourceset_dir("new")), ctx.exception.hint)


class TestFieldS5NullFlagsAreReadOffTheAssessment(_FieldCase):
    """S5, as the integrate migration left it: ``create --from`` a sourceset enumerated
    before the flags existed carries ``flags: null`` on every entry, and a plain fetch
    kept only an entry whose flag said redirect or disambiguation -- so it fetched every
    stub its assessment class (Redirect, Disambig) already named."""

    def seed(self) -> Sourceset:
        code, out = cf(self.root, "sourceset", "create", "old")
        self.assertEqual(code, 0, out)
        old = Sourceset.open(self.paths, "old")
        fam = websource.family("enwiki")
        rows = [websource.entry(fam, 1000 + n, 2000 + n, title=title,
                                assessment={"class": cls, "importance": "NA"})
                for n, (title, cls) in enumerate((
                    ("Widget group", "B"), ("Old widget name", "Redirect"),
                    ("Widget (disambiguation)", "Disambig"), ("List of widgets", "List"),
                    ("Gadget theorem", "Start")), 1)]
        self.assertTrue(all("flags" not in r for r in rows))
        old.write_version(old.latest(), rows)
        code, out = cf(self.root, "sourceset", "create", "new", "--from", "old")
        self.assertEqual(code, 0, out)
        new = Sourceset.open(self.paths, "new")
        self.assertTrue(all(not e.get("flags") for e in new.latest()))
        return new

    def test_a_plain_fetch_leaves_out_what_the_class_names(self):
        self.seed()
        code, out = cf(self.root, "sourceset", "fetch", "new")
        self.assertEqual(code, 0, out)
        self.assertIn("dry run: 3 source(s) of new have no bytes yet", out)
        self.assertIn("2 redirect or disambiguation page(s) left out", out)
        self.assertIn("carry no page flags", out)
        self.assertIn("enumerate-wikiproject new", out)
        code, out = cf(self.root, "sourceset", "fetch", "new", "--include-flagged")
        self.assertIn("dry run: 5 source(s)", out)

    def test_the_effective_flags(self):
        entries = {e["title"]: e for e in self.seed().latest()}
        self.assertFalse(websource.no_article(entries["Widget group"]))
        self.assertTrue(websource.no_article(entries["Old widget name"]))
        self.assertTrue(websource.no_article(entries["Widget (disambiguation)"]))
        self.assertFalse(websource.no_article(entries["List of widgets"]))   # a list is fetched
        self.assertFalse(websource.concept_page(entries["List of widgets"]))  # and not planned
        self.assertTrue(websource.concept_page(entries["Gadget theorem"]))
        # a flag the entry determined is kept as recorded, whatever its class says
        recorded = dict(entries["Old widget name"], flags={"redirect": False})
        self.assertFalse(websource.effective_flags(recorded)["redirect"])
        self.assertIsNone(websource.effective_flags(entries["Gadget theorem"])["biography"])


class TestFieldS5TheWikidataRequest(unittest.TestCase):

    transport = TestCurlTransport.transport

    def test_s5_the_query_service_gets_one_fixed_get_with_no_maxlag(self):
        body = json.dumps({"results": {"bindings": [
            {"item": {"value": "http://www.wikidata.org/entity/Q99999105"}}]}}).encode()
        t, run, _s = self.transport([(0, "200", body)])
        self.assertEqual(wikinet.wikidata_humans(t, ["Q99999105", "Q99999101"]), {"Q99999105"})
        (argv,) = run.argvs
        url = argv[-1]
        self.assertTrue(url.startswith("https://query.wikidata.org/sparql?"), url)
        self.assertNotIn("maxlag", url)
        self.assertIn("wdt%3AP31+wd%3AQ5", url)
        self.assertIn(wikinet.USER_AGENT, argv)

    def test_s5_only_qids_are_asked_and_only_the_two_endpoints_exist(self):
        with self.assertRaises(wikinet.WikiError):
            wikinet.wikidata_humans(_Fake(lambda p: {}), ["Q1 } DELETE {"])
        with self.assertRaises(wikinet.WikiError):
            wikinet.request_url(SITE, {"a": "1"}, "/w/index.php")


if __name__ == "__main__":
    unittest.main()
