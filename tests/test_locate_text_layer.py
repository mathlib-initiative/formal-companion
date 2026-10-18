"""The Literature Links text layer: ``web-html/3`` against ``$WIKILEAN``'s own function.

New code, for references/locate-documents.md section 7 (the layer, and its
parity with the consumer's).  ``$WIKILEAN``'s rule is the one definition of the
layer, and ``scripts/sources/render_html.py`` is a conforming implementation;
the proof is tests/data/locate/text-layer.json, whose ``layer`` values
``$WIKILEAN``'s real ``text_layer`` generated (tests/gen_text_layer.py, stamped
with its commit and the module's sha256).  This module replays every case:

* a case without ``known_divergence`` must give ``$WIKILEAN``'s layer, character
  for character;
* a known divergence must give the layer recorded beside it, and still differ
  from ``$WIKILEAN``'s, so a stale mark cannot hide a change of either side;
  ``departs`` names the side that is not the HTML5 parser there, and the cases
  where this renderer is the one are pinned by name.

It replaces the env-gated parity test that ran only when ``CF_WIKILEAN_LAYER``
named a directory of pairs, and never ran: the fixture is in the tree.  Nothing
here needs ``$WIKILEAN``: regenerating the fixture does.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
for _path in (str(SCRIPTS), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import gen_text_layer as gen  # noqa: E402
from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from cflib import vocab  # noqa: E402
from formal_companion_annotations import ids as fca_ids  # noqa: E402
from sources import render_html as rh  # noqa: E402

FIXTURE = HERE / "data" / "locate" / "text-layer.json"
#: the cases where web-html/3 itself is not the HTML5 parser: a template is no
#: scope boundary for its tree builder, and html.parser drops a CDATA section
#: in MathML (markup MediaWiki's parser output does not carry)
THIS_RENDERERS_DEPARTURES = frozenset({
    "template-in-list-item", "template-in-definition-list", "template-in-table-cell",
    "template-and-stray-end-tags", "cdata-in-mathml"})


def load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def layer(html: str) -> str:
    """The layer as adopt then render computes it: the canonical body, rendered."""
    return rh.render(rh.canonical_body(html.encode("utf-8"))).layer


class TestTheFixture(unittest.TestCase):

    def setUp(self):
        self.doc = load()

    def test_it_is_stamped_with_the_producer_and_names_nobody(self):
        p = self.doc["producer"]
        self.assertEqual(self.doc["_schema"], gen.SCHEMA)
        self.assertEqual(p["repository"], "$WIKILEAN")
        self.assertRegex(p["commit"], r"\A[0-9a-f]{40}\Z")
        self.assertRegex(p["module_sha256"], r"\A[0-9a-f]{64}\Z")
        self.assertEqual((p["module"], p["function"]), ("site/anchor_resolve.py", "text_layer"))
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertTrue(text.isascii(), "the fixture is written ASCII-only (escapes, no "
                                        "invisible characters)")
        self.assertNotRegex(text, r"github\.com|/home/|/Users/")

    def test_it_is_of_the_current_renderer(self):
        """A fixture of another renderer id says nothing about this one's layer."""
        self.assertEqual(self.doc["renderer"], rh.RENDERER)
        self.assertEqual(self.doc["renderer"], vocab.RENDERER_WEB_HTML)
        self.assertEqual(self.doc["renderer"], "web-html/3")

    def test_the_generator_holds_the_same_cases(self):
        """The fixture is regenerable: its cases are the generator's, in its order."""
        mine = [(c.name, c.why, c.html, c.divergence, list(c.departs) if c.divergence else None)
                for c in gen.CASES]
        theirs = [(c["name"], c["why"], c["html"],
                   (c.get("known_divergence") or {}).get("reason"),
                   (c.get("known_divergence") or {}).get("departs")) for c in self.doc["cases"]]
        self.assertEqual(theirs, mine)
        self.assertEqual(len({name for name, *_ in mine}), len(mine))

    def test_the_cases_exercise_every_part_of_the_rule(self):
        conforming = [c["html"] for c in self.doc["cases"] if "known_divergence" not in c]
        joined = "".join(conforming)
        for marker in ("<style", "<script", "<template", "<math", "mw-editsection", "&#160;",
                       'class="reference"', "<br"):
            self.assertIn(marker, joined)
        for ch in rh.WHITE_SPACE:
            self.assertIn(ch, joined, f"no conforming case holds U+{ord(ch):04X}")

    def test_each_divergence_names_the_side_that_departs(self):
        sides = {gen.PRODUCER_SIDE, gen.THIS_SIDE}
        ours = set()
        for case in self.doc["cases"]:
            mark = case.get("known_divergence")
            if mark is None:
                continue
            with self.subTest(case=case["name"]):
                self.assertTrue(mark["departs"])
                self.assertLessEqual(set(mark["departs"]), sides)
                self.assertTrue(mark["reason"].strip())
                if gen.THIS_SIDE in mark["departs"]:
                    ours.add(case["name"])
                    self.assertIn("MediaWiki", mark["reason"],
                                  "a departure of this renderer says why no page reaches it")
        self.assertEqual(ours, THIS_RENDERERS_DEPARTURES)

    def test_a_self_closing_skipped_element_in_foreign_content_is_recorded(self):
        """HTML5 honours ``<style/>`` and ``<script/>`` in SVG and MathML; the scanner does not."""
        names = {c["name"]: c for c in self.doc["cases"]}
        for name, html_marker in (("svg-self-closing-style", "<svg><style/>"),
                                  ("mathml-self-closing-script", "<script/>")):
            with self.subTest(case=name):
                case = names[name]
                self.assertIn(html_marker, case["html"])
                self.assertEqual(case["known_divergence"]["departs"], [gen.PRODUCER_SIDE])


class TestConformance(unittest.TestCase):
    """The replay: web-html/3 is $WIKILEAN's layer on every case not marked."""

    def test_every_conforming_case_is_wikileans_layer(self):
        cases = [c for c in load()["cases"] if "known_divergence" not in c]
        self.assertGreaterEqual(len(cases), 30)
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertEqual(layer(case["html"]), case["layer"])

    def test_every_known_divergence_is_the_one_recorded(self):
        cases = [c for c in load()["cases"] if "known_divergence" in c]
        self.assertTrue(cases)
        for case in cases:
            with self.subTest(case=case["name"]):
                ours = layer(case["html"])
                self.assertEqual(ours, case["known_divergence"]["formal_companion_layer"])
                self.assertNotEqual(ours, case["layer"], "no longer a divergence: regenerate "
                                                         "the fixture and drop the mark")

    def test_the_whitespace_and_the_skipped_elements_are_wikileans(self):
        producer = load()["producer"]
        self.assertEqual(rh.WHITE_SPACE, frozenset(chr(c) for c in producer["white_space"]))
        self.assertEqual(rh._SKIPPED, frozenset(producer["skip_elements"]))
        # and the Unicode White_Space property the vendored id normaliser writes out
        self.assertEqual(rh.WHITE_SPACE, frozenset(fca_ids._UNICODE_WHITE_SPACE))
        self.assertEqual(len(rh.WHITE_SPACE), 25)

    def test_nothing_inside_a_template_is_a_view(self):
        r = rh.render('<p>Lead paragraph with words.</p><template><h2>Hidden</h2><p><b>gone</b> '
                      'here</p><span class="mwe-math-element"><img class="mwe-math-fallback-image" '
                      'alt="q"></span></template><p>After.</p>')
        self.assertEqual(r.layer, "Lead paragraph with words.After.")
        self.assertEqual(len(r.sections), 1)
        self.assertEqual(r.counts["formulas_without_text"], 0)
        self.assertFalse(any(f["term"] == "gone" for f in r.finders))
        self.assertNotIn("Hidden", r.text)
        self.assertNotRegex(r.text, re.escape("$q$"))


if __name__ == "__main__":
    unittest.main()
