"""The statement cut's inventory side: the end rule, the blocks it reads, the cut rules.

New code, for docs/proposals/statement-cut.md section 3 (the "cut" package of its
section 6.2): ``locate_text.passage_end`` under ``span_end_rule`` 2 and the
definitions of section 3.1 it rests on (``locate_text.Blocks``), the units the
receipt counts (``locate_text.units``), ``passage_ids.py``'s two passes and the
rule it reads from the ``gate.json`` beside it, and the cut rules the inventory
prompt and the bench labeler's inventory mode carry word for word with
references/vocabulary.md section 1.11.  Every layer is rendered by the
producer, ``sources.render_html``, from a synthetic page about widgets and
gadgets, and every cut case -- connectives inside a sentence, lists, chains,
displays, proofs, nested containment, a passage that would cross a paragraph --
is ended under the new rule AND under rule 1, which every run planned before
the proposal's flip names and which must end it exactly as it always did.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import vocab  # noqa: E402
from locate import passage_ids  # noqa: E402
from locate import records as locate_records  # noqa: E402
from sources import render_html  # noqa: E402

#: the module passage_ids imports (by its plain name, as on the run host)
lt = passage_ids.lt

VOCABULARY_MD = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")
PROPOSAL = (SKILL / "docs" / "proposals" / "statement-cut.md").read_text(encoding="utf-8")
LABELER = (SKILL / "workflows" / "locate-label.workflow.js").read_text(encoding="utf-8")
TEMPLATE = (SKILL / "templates" / "prompts" / "locate-inventory.md.tmpl").read_text(
    encoding="utf-8")


def inline(tex: str) -> str:
    """An inline formula as MediaWiki's MathML output wraps one."""
    alt = "{\\displaystyle " + tex + "}"
    return (f'<span class="mwe-math-element"><span class="mwe-math-mathml-inline">'
            f'<math alttext="{alt}"><semantics><mrow><mi>{tex}</mi></mrow>'
            f'<annotation encoding="application/x-tex">{alt}</annotation></semantics></math>'
            f'</span></span>')


def display(tex: str) -> str:
    """A display formula (``mwe-math-element-block``), which the renderer lists as a block."""
    alt = "{\\displaystyle " + tex + "}"
    return (f'<span class="mwe-math-element mwe-math-element-block">'
            f'<span class="mwe-math-mathml-display"><math display="block" alttext="{alt}">'
            f'<semantics><mrow><mi>{tex}</mi></mrow>'
            f'<annotation encoding="application/x-tex">{alt}</annotation></semantics></math>'
            f'</span></span>')


def dd(tex: str) -> str:
    """A display on its own indented line, the way articles set one."""
    return "<dl><dd>" + display(tex) + "</dd></dl>"


class Page:
    """One synthetic page, rendered, with its blocks and a way to end passages on it."""

    def __init__(self, *html: str):
        body = '<div class="mw-parser-output">' + "\n".join(html) + "</div>"
        r = render_html.render(body)
        self.layer, self.formulas, self.paragraphs = r.layer, r.formulas, r.paragraphs
        self.blocks = lt.Blocks(self.layer, self.paragraphs, self.formulas)

    def at(self, text: str) -> int:
        """The layer offset of ``text``, which occurs exactly once."""
        first = self.layer.find(text)
        assert first >= 0 and self.layer.find(text, first + 1) < 0, text
        return first

    def after(self, text: str) -> int:
        return self.at(text) + len(text)

    def end(self, start: int, *, rule: int, starts=(), until=None, exact: str | None = None):
        later = sorted(s for s in starts if s > start)
        return lt.passage_end(self.layer, self.paragraphs, start,
                              exact if exact is not None else self.layer[start:start + 8],
                              until, rule=rule, next_start=later[0] if later else None,
                              formulas=self.formulas)

    def cut(self, *heads: str, rule: int) -> list[tuple[int, int]]:
        """The spans of passages starting at each head, ended by ``rule`` with no ``until``."""
        starts = [self.at(h) for h in heads]
        return [(s, self.end(s, rule=rule, starts=starts)) for s in starts]

    def text(self, span) -> str:
        return self.layer[span[0]:span[1]]


def nested(spans) -> int:
    """How many pairs of the spans share a character (containment or overlap)."""
    spans = sorted(spans)
    return sum(1 for i, (a, b) in enumerate(spans) for c, d in spans[i + 1:] if c < b)


# --------------------------------------------------------------------------- #
# the synthetic pages, one per cut case
# --------------------------------------------------------------------------- #

CONNECTIVES = ("<p>A widget is finite when it has finitely many sprockets; every finite widget "
               "has a spanning gadget, since gadgets meet every sprocket.</p>")
LISTS = ("<p>The following are equivalent for a widget:</p>"
         "<ul><li>the widget is finite;</li><li>the widget is bounded.</li></ul>\n"
         "<p>Two facts hold of gadgets:</p>"
         "<ul><li>Every gadget spans a widget.</li><li>Every gadget is finite.</li></ul>")
CHAIN = ("<p>For each finite widget W we have</p>\n" + dd("|W| = |G| \\cdot k,\\,") + "\n"
         + dd("k = |S|.") + "\n" + dd("x = y") + "\n<p>where S is the set of sprockets.</p>")
#: narration, a statement opened by its hypothesis, a statement after "so", and narration again
PROOF = ("<p>Proof. The argument goes by induction on the sprockets. Suppose that W is a finite "
         "widget. Then W has a spanning gadget, so every sprocket of W is met. This completes "
         "the proof.</p>")
PARAGRAPHS = "<p>Every widget is a gadget.</p>\n<p>Every gadget is finite.</p>"
BOX = ('<div class="math_theorem"><p>Theorem. Every widget spans.</p>\n'
       "<p>Moreover every gadget is unique.</p></div>")
#: a theorem box holding one paragraph shares that paragraph's span
LEMMA_BOX = ('<div class="math_theorem"><p>Lemma. Each widget has the gadget</p></div>\n'
             + dd("g = h."))
PLAIN_INTRO = "<p>Each sprocket has the cog</p>\n" + dd("c = d.")


class TestTheBlocks(unittest.TestCase):
    """Section 3.1: block, next, display block, introduces, chain, list run, extent."""

    def test_block_is_the_outermost_and_entries_with_one_span_are_one_block(self):
        page = Page(BOX, LEMMA_BOX)
        box = page.blocks.block_at(page.at("Moreover"))
        self.assertTrue(box.theorem_box)
        self.assertEqual(box.span, (page.at("Theorem."), page.after("gadget is unique.")))
        lemma = page.blocks.block_at(page.at("Lemma."))
        self.assertEqual(sorted(lemma.kinds), ["p", "theorem_box"])
        self.assertTrue(lemma.theorem_box)
        # the display after the box and its dd share a span, so they are one block too
        shown = page.blocks.block_at(page.at("g = h.{"))
        self.assertEqual(sorted(shown.kinds), ["dd", "math"])
        # block(x) is paragraph_end's choice: rule 1's end is the same block's end
        for text in ("Moreover", "Lemma.", "g = h.{"):
            with self.subTest(text=text):
                self.assertEqual(lt.paragraph_end(page.paragraphs, page.at(text)),
                                 page.blocks.block_at(page.at(text)).end)

    def test_no_block_holds_text_outside_every_block(self):
        page = Page("Loose words outside any paragraph.", PARAGRAPHS)
        self.assertIsNone(page.blocks.block_at(page.at("Loose")))
        self.assertIsNone(page.blocks.extent(page.at("Loose")))

    def test_next_is_the_following_block_across_whitespace_only(self):
        page = Page(PARAGRAPHS, '<h2 id="More">More</h2>', "<p>Every cog turns.</p>")
        first = page.blocks.block_at(page.at("Every widget"))
        second = page.blocks.block_at(page.at("Every gadget"))
        self.assertIs(page.blocks.next(first), second)
        # a heading's text lies between: there is no next block
        self.assertIsNone(page.blocks.next(second))

    def test_a_display_block_holds_a_formula_and_no_letter(self):
        page = Page(CHAIN, "<dl><dd>" + inline("x") + " where the widget is finite</dd></dl>",
                    "<p>The cog " + inline("c") + " turns.</p>")
        self.assertTrue(page.blocks.is_display(page.blocks.block_at(page.at("k = |S|.{"))))
        self.assertFalse(page.blocks.is_display(page.blocks.block_at(page.at("where the widget"))))
        self.assertFalse(page.blocks.is_display(page.blocks.block_at(page.at("The cog"))))
        self.assertFalse(page.blocks.is_display(page.blocks.block_at(page.at("For each"))))

    def test_introduces_reads_a_trailing_formula_by_its_alttext(self):
        page = Page(CHAIN, PARAGRAPHS)
        b = page.blocks
        self.assertTrue(b.introduces(b.block_at(page.at("For each"))))        # "... we have"
        first = b.block_at(page.at("|W| = |G| \\cdot k,\\,{"))
        self.assertTrue(b.introduces(first))                                  # "k,\," runs on
        self.assertFalse(b.introduces(b.block_at(page.at("k = |S|.{"))))     # "|S|." stops
        self.assertFalse(b.introduces(b.block_at(page.at("Every widget"))))  # a full stop

    def test_the_formula_stop(self):
        for alt, want in (("{\\displaystyle a=b.\\,}", "."), ("{\\displaystyle a=b,\\;}", ","),
                          ("{\\displaystyle a=b\\quad}", "b"),
                          ("{\\displaystyle a=b.\\qquad~}", "."),
                          ("{\\displaystyle \\left\\{x\\right.}", "x"),
                          ("{\\displaystyle \\,}", ""), ("{\\displaystyle a = b!}", "!"),
                          # what TeX writes after the last character is set aside too
                          ("{\\displaystyle \\begin{aligned}a&=b.\\end{aligned}}", "."),
                          ("{\\displaystyle \\begin{alignedat}{2}a&=b,\\end{alignedat}}", ","),
                          ("{\\displaystyle a=b{\\text{.}}}", "."),
                          ("{\\displaystyle a=b. \\\\}", "."),
                          ("{\\displaystyle \\{x\\}}", "}"),     # an escaped brace is the end
                          ("{\\displaystyle x^{n!}}", "!"), ("{\\displaystyle \\frac{1}{n!}}", "!")):
            with self.subTest(alt=alt):
                self.assertEqual(lt.formula_stop(alt), want)

    def test_a_formulas_factorial_is_no_full_stop(self):
        """``x^{n!}`` ends with "!", which in TeX is a factorial: it ends no sentence and
        no chain (``FORMULA_STOPS``), so the display after it still joins."""
        self.assertEqual(lt.FORMULA_STOPS, lt.SENTENCE_STOPS - {"!"})
        page = Page("<p>For each widget the count is</p>" + dd("c = \\frac{1}{n!}") + "\n"
                    + dd("d = e."), "<p>The count " + inline("x^{n!}") + " Widgets turn.</p>")
        b = page.blocks
        self.assertTrue(b.introduces(b.block_at(page.at("c = \\frac{1}{n!}{"))))
        self.assertEqual(b.extent(page.at("For each")), b.block_at(page.at("d = e.{")).end)
        span = (page.at("The count"), page.after("Widgets turn."))
        self.assertEqual(len(lt.units(page.layer, b, None, span)["sentences"]), 1)
        # a full stop in the text after it still ends the sentence
        page = Page("<p>The count is " + inline("n!") + ". Widgets turn.</p>")
        span = (page.at("The count"), page.after("Widgets turn."))
        self.assertEqual(len(lt.units(page.layer, page.blocks, None, span)["sentences"]), 2)

    def test_closing_marks_after_a_full_stop_are_read_through(self):
        page = Page("<p>Every widget is finite. (See the notes.)</p>" + dd("c = d."),
                    "<p>Every gadget has a cog (when finite)</p>" + dd("e = f."),
                    "<p>Every cog has a count (" + inline("n!") + ")</p>" + dd("g = h."))
        b = page.blocks
        cases = (("Every widget", ".", False), ("Every gadget", ")", True),
                 ("Every cog", ")", True))
        for head, last, runs_on in cases:
            with self.subTest(head=head):
                block = b.block_at(page.at(head))
                self.assertEqual(b.last_character(block.start, block.end), last)
                self.assertIs(b.introduces(block), runs_on)
        self.assertEqual(b.extent(page.at("Every widget")), page.after("(See the notes.)"))

    def test_the_chain_is_the_displays_the_last_sentence_introduces(self):
        page = Page(CHAIN)
        b = page.blocks
        chain = b.chain(b.block_at(page.at("For each")))
        self.assertEqual([page.text(x.span)[:10] for x in chain],
                         ["For each f", "|W| = |G| ", "k = |S|.{\\"])
        # "x = y" follows a display that ended its sentence: not introduced
        self.assertNotIn(b.block_at(page.at("x = y{")), chain)

    def test_a_chain_stops_at_a_list_item(self):
        """A list item is a unit of its own: a list of display items is one passage per item
        (or a compound statement over them), never displays a sentence introduces."""
        page = Page("<p>For each widget we have</p><ul><li>" + inline("a = b,") + "</li><li>"
                    + inline("c = d,") + "</li><li>" + inline("e = f") + "</li></ul>")
        b = page.blocks
        intro = b.block_at(page.at("For each"))
        self.assertTrue(b.introduces(intro))
        self.assertEqual(b.chain(intro), [intro])
        first = page.at("a = b,{")
        self.assertTrue(b.is_display(b.block_at(first)))
        self.assertEqual(b.extent(first), b.block_at(first).end)
        self.assertEqual(b.extent(first, compound=True), b.block_at(page.at("e = f{")).end)

    def test_the_extent_is_an_upper_bound_in_a_container(self):
        """The proposal's section 3.1: block(x) is the outermost block, so inside a
        blockquote, a dd or a list item that holds other blocks a passage no later start
        ends runs to the container's end, and `until` is what ends it at its paragraph;
        two lists with nothing between them are one run.  Pinned as documented."""
        two = "<p>Every widget is finite.</p><p>Every gadget is round.</p>"
        for html in ("<blockquote>" + two + "</blockquote>", "<dl><dd>" + two + "</dd></dl>"):
            with self.subTest(html=html[:12]):
                page = Page(html)
                start = page.at("Every widget")
                self.assertEqual(page.end(start, rule=2), page.after("gadget is round."))
                self.assertEqual(page.end(start, rule=2, starts=[page.at("Every gadget")]),
                                 page.after("widget is finite."))
                self.assertLess(page.end(start, rule=2, until="is finite."),
                                page.blocks.extent(start))
        page = Page("<ul><li>Every widget is finite.<ul><li>every gadget is round;</li>"
                    "<li>every cog turns.</li></ul></li><li>Every sprocket spins.</li></ul>")
        b = page.blocks
        self.assertEqual(page.end(page.at("Every widget"), rule=2), page.after("cog turns."))
        nested_item = page.at("every gadget")
        self.assertEqual(b.extent(nested_item), page.after("cog turns."))
        self.assertEqual(b.extent(nested_item, compound=True), page.after("sprocket spins."))
        page = Page("<ul><li>a widget is finite;</li></ul><ol><li>a gadget is round.</li></ol>")
        run = page.blocks.list_run(page.blocks.block_at(page.at("a widget")))
        self.assertEqual([page.text(x.span) for x in run],
                         ["a widget is finite;", "a gadget is round."])

    def test_a_list_run_is_the_items_of_one_list(self):
        page = Page(LISTS)
        b = page.blocks
        run = b.list_run(b.block_at(page.at("the widget is finite")))
        self.assertEqual([page.text(x.span) for x in run],
                         ["the widget is finite;", "the widget is bounded."])
        self.assertEqual(b.list_run(b.block_at(page.at("The following"))),
                         [b.block_at(page.at("The following"))])

    def test_the_extent(self):
        page = Page(LISTS, CHAIN, BOX, LEMMA_BOX, PLAIN_INTRO)
        b = page.blocks
        first_item = page.at("the widget is finite")
        self.assertEqual(b.extent(first_item), page.after("the widget is finite;"))
        self.assertEqual(b.extent(first_item, compound=True), page.after("the widget is bounded."))
        # a lead-in is no list item, and runs into no list
        self.assertEqual(b.extent(page.at("The following"), compound=True),
                         page.after("equivalent for a widget:"))
        self.assertEqual(b.extent(page.at("For each")), b.block_at(page.at("k = |S|.{")).end)
        self.assertEqual(b.extent(page.at("Moreover")), page.after("gadget is unique."))
        # a theorem box ends where it does, even when its last sentence runs on
        self.assertEqual(b.extent(page.at("Lemma.")), page.after("has the gadget"))
        self.assertEqual(b.extent(page.at("Each sprocket")), b.block_at(page.at("c = d.{")).end)
        self.assertEqual(lt.extent(page.layer, page.paragraphs, page.formulas,
                                   page.at("Each sprocket")),
                         b.extent(page.at("Each sprocket")))


class TestTheEndRule(unittest.TestCase):
    """Section 3.2: each cut case ended by version 2, and by version 1 as before."""

    def test_connectives_inside_a_sentence(self):
        """The separator before the next start -- the space, the connective, the comma or
        semicolon -- is in neither passage."""
        page = Page(CONNECTIVES)
        heads = ("A widget is finite", "every finite widget", "gadgets meet every")
        new = page.cut(*heads, rule=2)
        self.assertEqual([page.text(s) for s in new],
                         ["A widget is finite when it has finitely many sprockets",
                          "every finite widget has a spanning gadget",
                          "gadgets meet every sprocket."])
        self.assertEqual(nested(new), 0)
        old = page.cut(*heads, rule=1)
        self.assertEqual({e for _s, e in old}, {page.after("every sprocket.")})
        self.assertEqual(nested(old), 3)
        # `until` ends a piece before its connective, under either rule, and under rule 2
        # it is the end the next start gives: one span, one passage_key, whether or not
        # the reason after "since" is cut as a passage of its own
        start = page.at("every finite widget")
        for rule in (1, 2):
            with self.subTest(rule=rule):
                self.assertEqual(page.end(start, rule=rule, starts=[page.at("gadgets meet")],
                                          until="spanning gadget"),
                                 page.after("spanning gadget"))
        self.assertEqual(page.end(start, rule=2, starts=[page.at("gadgets meet")]),
                         page.end(start, rule=2, until="spanning gadget"))

    def test_the_separator(self):
        """What separator_start sets aside, and what it leaves: a bare "and" is no
        connective, a connective is a whole word, a sentence's full stop stays, and nothing
        inside a formula is set aside."""
        cases = (
            ("<p>Every widget is finite, and every gadget is round.</p>", "Every widget is finite"),
            ("<p>Every widget is finite and every gadget is round.</p>",
             "Every widget is finite and"),
            ("<p>Every widget is finite; moreover, every gadget is round.</p>",
             "Every widget is finite"),
            ("<p>Every widget is finite, so hence every gadget is round.</p>",
             "Every widget is finite"),
            ("<p>Every widget spans also every gadget is round.</p>", "Every widget spans also"),
            ("<p>Every widget is finite. Then every gadget is round.</p>",
             "Every widget is finite."),
        )
        for html, want in cases:
            with self.subTest(want=want):
                page = Page(html)
                spans = page.cut("Every widget", "every gadget", rule=2)
                self.assertEqual(page.text(spans[0]), want)
                self.assertEqual(page.text(spans[1]), "every gadget is round.")
        # a formula ending the first piece is kept whole, its own comma with it
        page = Page("<p>Every widget has " + inline("a = b,") + " so every gadget is round.</p>")
        first = page.cut("Every widget", "every gadget", rule=2)[0]
        formula = page.blocks.formula_at(first[1] - 1)
        self.assertIsNotNone(formula)
        self.assertEqual(formula[1], first[1])
        # never back to the passage's own start: a connective it starts with stays
        self.assertEqual(page.blocks.separator_start(page.at("so every"), page.at("every gadget")),
                         page.at("so every") + len("so"))

    def test_lists(self):
        page = Page(LISTS)
        facts = page.cut("Two facts", "Every gadget spans", "Every gadget is finite", rule=2)
        self.assertEqual([page.text(s) for s in facts],
                         ["Two facts hold of gadgets:", "Every gadget spans a widget.",
                          "Every gadget is finite."])
        # rule 1 ends a list item at its own end too
        self.assertEqual(page.cut("Two facts", "Every gadget spans", "Every gadget is finite",
                                  rule=1), facts)
        # a compound statement over a list runs over its items by its `until`
        start = page.at("the widget is finite")
        for rule in (1, 2):
            with self.subTest(rule=rule):
                self.assertEqual(page.end(start, rule=rule, until="the widget is bounded."),
                                 page.after("the widget is bounded."))
                self.assertEqual(page.end(start, rule=rule), page.after("the widget is finite;"))

    def test_chains_and_displays(self):
        page = Page(CHAIN)
        start = page.at("For each")
        # version 2: the sentence with the displays it introduces, and no further
        self.assertEqual(page.end(start, rule=2), page.blocks.block_at(page.at("k = |S|.{")).end)
        # version 1: the paragraph only, so the displays were nobody's
        self.assertEqual(page.end(start, rule=1), page.after("we have"))
        # a passage that starts at the next sentence stops the chain where it starts
        where = page.at("where S")
        self.assertEqual(page.end(start, rule=2, starts=[page.at("x = y{")]),
                         page.blocks.block_at(page.at("k = |S|.{")).end)
        self.assertEqual(page.end(where, rule=2), page.after("set of sprockets."))
        # no end falls inside a formula
        for s in (start, where, page.at("x = y{")):
            end = page.end(s, rule=2)
            f = page.blocks.formula_at(end - 1)
            self.assertTrue(f is None or f[1] == end)

    def test_proofs(self):
        """PD5 and the cut rules: the narration is an expository passage, the statement opens
        with its hypothesis, the one after "so" is its own, and the closing narration is left
        out, so the last statement ends by its `until`."""
        page = Page(PROOF)
        heads = ("Proof. The argument", "Suppose that W", "every sprocket of W")
        until = {"every sprocket of W": "of W is met."}
        starts = [page.at(h) for h in heads]

        def spans(rule):
            return [(s, page.end(s, rule=rule, starts=starts, until=until.get(h)))
                    for h, s in zip(heads, starts)]

        self.assertEqual([page.text(s) for s in spans(2)],
                         ["Proof. The argument goes by induction on the sprockets.",
                          "Suppose that W is a finite widget. Then W has a spanning gadget",
                          "every sprocket of W is met."])
        self.assertEqual(nested(spans(2)), 0)
        old = spans(1)
        self.assertEqual([e for _s, e in old][:2], [page.after("the proof.")] * 2)
        self.assertEqual(nested(old), 3)
        # a proof as one passage -- the whole paragraph from "Proof." -- holds all of them
        self.assertEqual(page.text(old[0]), page.text(page.blocks.block_at(starts[0]).span))

    def test_nested_containment_is_gone_and_was_there(self):
        """FM9: under rule 1 a passage with no `until` runs to its paragraph's end over every
        later passage of the paragraph; rule 2 ends it at the next one's start."""
        page = Page(CONNECTIVES, PROOF, LISTS, CHAIN)
        heads = ("A widget is finite", "every finite widget", "gadgets meet every",
                 "Proof. The argument", "Suppose that W", "every sprocket of W", "This completes",
                 "The following", "the widget is finite", "the widget is bounded",
                 "For each", "where S")
        old, new = page.cut(*heads, rule=1), page.cut(*heads, rule=2)
        self.assertEqual(nested(old), 9)
        self.assertEqual(nested(new), 0)
        # the same starts, and only the ends that ran over a later passage moved
        self.assertEqual([s for s, _e in old], [s for s, _e in new])

    def test_no_passage_crosses_into_the_next_paragraph(self):
        page = Page(PARAGRAPHS, BOX, LEMMA_BOX, PLAIN_INTRO)
        start = page.at("Every widget is")
        for rule in (1, 2):
            with self.subTest(rule=rule):
                self.assertEqual(page.end(start, rule=rule), page.after("is a gadget."))
        # inside a theorem box a passage runs over its paragraphs, to the next passage
        box = page.at("Theorem.")
        self.assertEqual(page.end(box, rule=2), page.after("gadget is unique."))
        # ... less the space between its paragraphs
        self.assertEqual(page.end(box, rule=2, starts=[page.at("Moreover")]),
                         page.after("Every widget spans."))
        self.assertEqual(page.end(box, rule=1), page.after("gadget is unique."))
        # a box of one paragraph does not run into the display after it; a paragraph does
        self.assertEqual(page.end(page.at("Lemma."), rule=2), page.after("has the gadget"))
        self.assertEqual(page.end(page.at("Each sprocket"), rule=2),
                         page.blocks.block_at(page.at("c = d.{")).end)
        self.assertEqual(page.end(page.at("Each sprocket"), rule=1), page.after("has the cog"))

    def test_outside_every_block_a_passage_ends_at_its_quote(self):
        page = Page("Loose words outside any paragraph.", PARAGRAPHS)
        start = page.at("Loose")
        for rule in (1, 2):
            with self.subTest(rule=rule):
                self.assertEqual(page.end(start, rule=rule, exact="Loose words"),
                                 page.after("Loose words"))

    def test_never_before_the_quote_ends(self):
        page = Page(CONNECTIVES)
        start = page.at("A widget")
        quote = "A widget is finite when it has finitely many sprockets; every finite"
        self.assertEqual(page.end(start, rule=2, starts=[page.at("every finite")], exact=quote),
                         start + len(quote))

    def test_what_version_2_refuses(self):
        page = Page(CONNECTIVES)
        start = page.at("every finite")
        with self.assertRaises(ValueError):
            lt.passage_end(page.layer, page.paragraphs, start, "every", rule=3,
                           formulas=page.formulas)
        with self.assertRaises(TypeError):
            lt.passage_end(page.layer, page.paragraphs, start, "every", rule=2)
        for bad in (start, start - 1, True, "12"):
            with self.subTest(next_start=bad):
                with self.assertRaises(ValueError):
                    lt.passage_end(page.layer, page.paragraphs, start, "every", rule=2,
                                   next_start=bad, formulas=page.formulas)
        # an `until` that is not on the layer is the HeadError both rules give
        for rule in (1, 2):
            with self.assertRaises(lt.HeadError):
                lt.passage_end(page.layer, page.paragraphs, start, "every", "no such words",
                               rule=rule, formulas=page.formulas)

    def test_version_1_is_the_function_it_was(self):
        """Every run planned before the flip names version 1: it reads no next start and no
        formulas, takes a Blocks or the raw table alike, and is the default."""
        page = Page(CONNECTIVES, PROOF, CHAIN, BOX)
        for text in ("A widget", "every finite", "Suppose that W", "For each", "Moreover"):
            start = page.at(text)
            want = max(start + 5, lt.paragraph_end(page.paragraphs, start))
            with self.subTest(text=text):
                self.assertEqual(lt.passage_end(page.layer, page.paragraphs, start, "x" * 5), want)
                self.assertEqual(lt.passage_end(page.layer, page.blocks, start, "x" * 5, rule=1,
                                                next_start=start + 1), want)


class TestUnits(unittest.TestCase):
    """Section 3.3: the sentences and displays the receipt counts per passage."""

    def sentences(self, page: Page, span) -> list[str]:
        return [page.text(s) for s in lt.units(page.layer, page.paragraphs, page.formulas,
                                               span)["sentences"]]

    def test_sentences(self):
        page = Page("<p>A widget is finite, cf. Theorem 2 of the notes. It has 3.5 cogs on "
                    "average. so it turns. " + inline("G") + " is a gadget. We have "
                    + inline("a = b.") + " Then every widget spans.</p>")
        span = (page.at("A widget"), page.after("widget spans."))
        self.assertEqual(self.sentences(page, span),
                         ["A widget is finite, cf. Theorem 2 of the notes.",
                          "It has 3.5 cogs on average. so it turns.",
                          page.text((page.at("G{"), page.after("is a gadget."))),
                          page.text((page.at("We have"), page.at(" Then"))),
                          "Then every widget spans."])

    def test_glued_paragraphs_of_a_box_and_a_trailing_space(self):
        page = Page(BOX, CONNECTIVES)
        box = (page.at("Theorem."), page.after("gadget is unique."))
        self.assertEqual(self.sentences(page, box),
                         ["Theorem.", "Every widget spans.", "Moreover every gadget is unique."])
        # a span that keeps the separator's space counts no whitespace into its sentence
        first = (page.at("A widget is finite"), page.at("every finite widget"))
        self.assertEqual(page.text(first)[-2:], "; ")
        self.assertEqual(self.sentences(page, first),
                         ["A widget is finite when it has finitely many sprockets;"])
        cut = page.cut("A widget is finite", "every finite widget", rule=2)[0]
        self.assertEqual(self.sentences(page, cut),
                         ["A widget is finite when it has finitely many sprockets"])

    def test_displays_are_the_math_blocks_inside(self):
        page = Page(CHAIN)
        span = (page.at("For each"), page.end(page.at("For each"), rule=2))
        counted = lt.units(page.layer, page.blocks, None, span)
        self.assertEqual(len(counted["displays"]), 2)
        self.assertEqual(len(counted["sentences"]), 1)
        whole = (page.at("For each"), page.after("set of sprockets."))
        self.assertEqual(len(lt.units(page.layer, page.blocks, None, whole)["displays"]), 3)


class TestPassageIds(unittest.TestCase):
    """``passage_ids.py``: every passage placed before any is ended, by the run's rule."""

    def doc(self, page: Page, heads, **extra) -> dict:
        rows = []
        for head in heads:
            row = {"item": "", "head_words": 0, "span": None,
                   "quote": {"exact": head, "prefix": "", "suffix": ""},
                   "passage_kind": "theorem", "prominence": "supporting",
                   "scope": "formal_math", "statement": "a restatement", "finder": "model"}
            row.update(extra.get(head, {}))
            rows.append(row)
        return {"passages": rows}

    def test_both_rules_and_the_conjuncts_left_as_written(self):
        page = Page(CONNECTIVES)
        conjuncts = [{"id": "c1", "text": "a finite widget has a spanning gadget"},
                     {"id": "c2", "text": "gadgets meet every sprocket"}]
        heads = ("gadgets meet every", "A widget is finite", "every finite widget")
        for rule, ends in ((1, [page.after("every sprocket.")] * 3),
                           (2, [page.after("every sprocket."), page.after("many sprockets"),
                                page.after("spanning gadget")])):
            doc = self.doc(page, heads, **{"every finite widget": {
                "conjuncts": conjuncts, "shape": "conclusions"}})
            with self.subTest(rule=rule):
                self.assertEqual(passage_ids.place(doc, page.layer, page.formulas,
                                                   page.paragraphs, rule=rule), [])
                self.assertEqual([p["span"][1] for p in doc["passages"]], ends)
                self.assertEqual(doc["passages"][2]["conjuncts"], conjuncts)
                self.assertEqual(doc["passages"][2]["shape"], "conclusions")
                self.assertTrue(all(p["item"].startswith("In-text #") for p in doc["passages"]))
        # the default is version 1: what every run planned before the flip names
        doc = self.doc(page, heads)
        self.assertEqual(passage_ids.place(doc, page.layer, page.formulas, page.paragraphs), [])
        self.assertEqual({p["span"][1] for p in doc["passages"]}, {page.after("every sprocket.")})

    def test_problems_stay_in_document_order(self):
        page = Page(CONNECTIVES)
        doc = self.doc(page, ("A widget is finite", "not on the layer", "every finite widget"),
                       **{"A widget is finite": {"until": "words that are nowhere"}})
        for rule in (1, 2):
            with self.subTest(rule=rule):
                problems = passage_ids.place(json.loads(json.dumps(doc)), page.layer,
                                             page.formulas, page.paragraphs, rule=rule)
                self.assertEqual(len(problems), 2)
                self.assertTrue(problems[0].startswith("passages[0]: `until`"))
                self.assertTrue(problems[1].startswith("passages[1]: the quote occurs 0 times"))


class TestTheRuleBesideTheTool(unittest.TestCase):
    """The tool ends passages by the span_end_rule of the gate.json beside it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-cut-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.gate = self.tmp / "gate"
        self.gate.mkdir()
        shutil.copy2(SCRIPTS / "locate" / "passage_ids.py", self.gate / "passage_ids.py")
        shutil.copy2(SCRIPTS / "gate" / "locate_text.py", self.gate / "locate_text.py")
        shutil.copytree(SKILL / "vendor" / "formal_companion_annotations",
                        self.gate / "vendor" / "formal_companion_annotations")
        self.page = Page(CONNECTIVES)
        source = self.tmp / "source"
        source.mkdir()
        (source / "layer.txt").write_text(self.page.layer, encoding="utf-8")
        (source / "formulas.json").write_text(json.dumps(self.page.formulas), encoding="utf-8")
        (source / "paragraphs.json").write_text(json.dumps(self.page.paragraphs),
                                                encoding="utf-8")
        self.source = source

    def run_tool(self, gate: dict | None, *flags: str, where: Path | None = None):
        if gate is not None:
            (self.gate / "gate.json").write_text(json.dumps(gate), encoding="utf-8")
        doc = {"passages": [
            {"item": "", "head_words": 0, "span": None,
             "quote": {"exact": head, "prefix": "", "suffix": ""}, "statement": "s",
             **({"conjuncts": [{"id": "c1", "text": "one"}, {"id": "c2", "text": "two"}],
                 "shape": "iff"} if head.startswith("every") else {})}
            for head in ("A widget is finite", "every finite widget", "gadgets meet every")]}
        path = self.tmp / "doc.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        tool = (where or self.gate) / "passage_ids.py"
        proc = subprocess.run([sys.executable, "-I", str(tool), "--source-dir", str(self.source),
                               "--document", str(path), "--write", *flags],
                              capture_output=True, text=True, cwd=str(self.tmp))
        written = json.loads(path.read_text(encoding="utf-8"))
        return proc, written

    def ends(self, written: dict) -> list[int]:
        return [p["span"][1] for p in written["passages"]]

    def test_the_gate_json_decides(self):
        new = [self.page.after("many sprockets"), self.page.after("spanning gadget"),
               self.page.after("every sprocket.")]
        old = [self.page.after("every sprocket.")] * 3
        for gate, want in (({"span_end_rule": 2}, new), ({"span_end_rule": 1}, old),
                           ({}, old), ({"span_end_rule": None}, old)):
            with self.subTest(gate=gate):
                proc, written = self.run_tool(gate)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self.ends(written), want)
                self.assertEqual(written["passages"][1]["shape"], "iff")
                self.assertEqual(len(written["passages"][1]["conjuncts"]), 2)
        proc, written = self.run_tool({"span_end_rule": 2}, "--span-end-rule", "2")
        self.assertEqual((proc.returncode, self.ends(written)), (0, new))

    def test_a_flag_that_disagrees_or_a_rule_it_does_not_know_is_an_input_error(self):
        for gate, flags in (({"span_end_rule": 2}, ("--span-end-rule", "1")),
                            ({}, ("--span-end-rule", "2")),
                            ({"span_end_rule": 3}, ()), ({"span_end_rule": True}, ()),
                            ({"span_end_rule": "2"}, ())):
            with self.subTest(gate=gate, flags=flags):
                proc, written = self.run_tool(gate, *flags)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIsNone(written["passages"][0]["span"])

    def test_with_no_gate_json_the_flag_says_it(self):
        proc, written = self.run_tool(None, where=SCRIPTS / "locate")
        self.assertEqual((proc.returncode, self.ends(written)),
                         (0, [self.page.after("every sprocket.")] * 3))
        proc, written = self.run_tool(None, "--span-end-rule", "2", where=SCRIPTS / "locate")
        self.assertEqual(self.ends(written)[:2], [self.page.after("many sprockets"),
                                                  self.page.after("spanning gadget")])
        proc, _written = self.run_tool(None, "--span-end-rule", "4", where=SCRIPTS / "locate")
        self.assertEqual(proc.returncode, 2)


class TestTheConstantsAreTheSkills(unittest.TestCase):
    """The host-side file restates what it cannot import; these pin each copy."""

    def test_the_versions(self):
        self.assertEqual(lt.SPAN_END_STATEMENT_CUT, vocab.SPAN_END_STATEMENT_CUT)
        self.assertEqual(lt.SPAN_END_RULES, (1, vocab.SPAN_END_STATEMENT_CUT))
        self.assertIn(vocab.SPAN_END_RULE, lt.SPAN_END_RULES)

    def test_the_abbreviations_are_the_records(self):
        self.assertEqual(lt.ABBREVIATIONS, locate_records._ABBREVIATIONS)

    def test_the_cut_connectives_are_the_vocabularys(self):
        """The end rule's separator sets aside the connectives the cut rules name."""
        self.assertEqual(lt.CUT_CONNECTIVES, vocab.CUT_CONNECTIVES)
        for connective in lt.CUT_CONNECTIVES:
            word = connective.lstrip(",; ")
            with self.subTest(connective=connective):
                if word:
                    self.assertTrue(word.isalpha() and word == word.lower())
                    self.assertTrue(set(connective[:-len(word)].strip()) <= lt.SEPARATOR_MARKS)
                else:
                    self.assertIn(connective, lt.SEPARATOR_MARKS)

    def test_the_block_names_are_the_renderers(self):
        self.assertLessEqual(set(lt.LIST_BLOCKS), render_html._BLOCK_TAGS)
        page = Page(BOX, CHAIN)
        kinds = {p["block"] for p in page.paragraphs}
        self.assertIn(lt.BLOCK_THEOREM_BOX, kinds)
        self.assertIn(lt.BLOCK_MATH, kinds)

    def test_a_blocks_of_either_module_name_is_one(self):
        from gate import locate_text as skill_lt
        page = Page(CHAIN)
        theirs = skill_lt.Blocks(page.layer, page.paragraphs, page.formulas)
        start = page.at("For each")
        self.assertEqual(lt.passage_end(page.layer, theirs, start, "For", rule=2),
                         page.blocks.block_at(page.at("k = |S|.{")).end)


# --------------------------------------------------------------------------- #
# the cut rules, word for word in the three places that carry them
# --------------------------------------------------------------------------- #

def cut_rule_lines() -> list[str]:
    """The cut rules as references/vocabulary.md section 1.11 writes them."""
    start = VOCABULARY_MD.index("**How the inventory cuts**")
    block = VOCABULARY_MD[start:VOCABULARY_MD.index("A stated reason is cut by PD9", start)]
    return [" ".join(line.split()) for line in block.splitlines() if line.startswith("- **")]


def pd9_line() -> str:
    """PD9 in the words the proposal's section 3.4 quotes."""
    match = re.search(r"^> \*\*PD9\*\* — (.*?)(?=\n\n)", PROPOSAL, re.M | re.S)
    return "- **PD9** — " + " ".join(match.group(1).replace("\n> ", " ").split())


def rendered_inventory() -> str:
    """The inventory worker prompt as the planner renders it."""
    from locate import presets as lpresets, prompts as lprompts

    class _Universe:
        id, sha256, libraries = "u1", "0" * 64, {}

    host = {"gate_dir": "<gate>", "tools_corpus_db": "<db>", "gate_command": "<gate cmd>",
            "sources_dir": "<sources>", "candidates_dir": "<candidates>", "tasks_dir": "<tasks>"}
    preset = lpresets.get(vocab.LOCATE_INVENTORY)
    ctx = lprompts.build_context(preset, run_id="r1", host=host, universe=_Universe(),
                                 per_task=8)
    return lprompts.render_prompts(preset, ctx)["worker.md"]


def labeler_cut_rules() -> str:
    start = LABELER.index("const CUT_RULES = `")
    end = LABELER.index("`\n", start + len("const CUT_RULES = `"))
    return " ".join(LABELER[start:end].replace("\\`", "`").split())


class TestTheCutRulesAreOneText(unittest.TestCase):
    """Section 3.4: the inventory prompt and the labeler cut by the vocabulary's words."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = rendered_inventory()
        cls.flat = " ".join(cls.prompt.split())

    def test_nine_rules_and_pd9(self):
        lines = cut_rule_lines()
        self.assertEqual([re.match(r"- \*\*(.*?)\*\*", line).group(1) for line in lines],
                         ["The test.", "Units.", "Displays.", "Inside a sentence.", "Lists.",
                          "Proofs.", "Context.", "Conjuncts.", "The subject."])
        self.assertIn("is a passage of its own only when X is a declarative", pd9_line())

    def test_the_inventory_prompt_says_them_word_for_word(self):
        for line in cut_rule_lines() + [pd9_line()]:
            with self.subTest(rule=line[:30]):
                self.assertIn(line, self.flat)
        self.assertNotRegex(self.prompt, r"\{\{")

    def test_the_labeler_says_them_word_for_word(self):
        rules = labeler_cut_rules()
        for line in cut_rule_lines() + [pd9_line()]:
            with self.subTest(rule=line[:30]):
                self.assertIn(line, rules)
        # both inventory prompts of the labeler read them, and no other mode does
        self.assertEqual(LABELER.count("${CUT_RULES}"), 2)
        inventory = LABELER[LABELER.index("async function inventory"):]
        self.assertEqual(inventory.count("${CUT_RULES}"), 2)

    def test_the_vocabulary_states_what_the_prompt_renders(self):
        lines = {re.match(r"- \*\*(.*?)\*\*", line).group(1): line for line in cut_rule_lines()}
        connectives = ", ".join(f'"{c}"' for c in vocab.CUT_CONNECTIVES)
        self.assertIn(f"at one of {connectives} (", lines["Inside a sentence."])
        self.assertIn(f"{vocab.MIN_CONJUNCTS} to {vocab.MAX_CONJUNCTS} of them",
                      lines["Conjuncts."])
        shapes = [m for m in re.findall(r"`([a-z_]+)` \(", lines["Conjuncts."])]
        self.assertEqual(tuple(shapes), vocab.COMPOUND_SHAPE_ORDER)
        self.assertIn("`existence_uniqueness` has exactly two", lines["Conjuncts."])
        self.assertEqual(vocab.SHAPE_CONJUNCT_COUNTS, {"existence_uniqueness": 2})
        # the template renders the bounds and connectives from cflib.vocab
        self.assertIn("{{cut_connectives}}", TEMPLATE)
        self.assertIn("{{min_conjuncts}} to {{max_conjuncts}}", TEMPLATE)
        self.assertIn("{{compound_shapes}}", TEMPLATE)

    def test_the_document_bullets_hold_the_gates_numbers(self):
        self.assertIn(f"one line of at most {vocab.MAX_CLAIM_CHARS} characters in your own "
                      f"words, never more than twelve consecutive words of the article, alone "
                      f"or read one after the other", self.flat)
        self.assertEqual(vocab.EXPORT_QUOTE_WORDS, 12)
        self.assertIn("`shape` is one of `existence_uniqueness`, `iff`, `cases`, `conclusions`, "
                      "`equations`", self.flat)
        self.assertIn('"shape": "existence_uniqueness"', self.prompt)

    def test_the_end_paragraph_is_the_runs_end_rule(self):
        """The prompt says where the run's own passage_ids.py ends a passage: span-end rule
        2, which every plan made since the flip names, and what rule 10 refuses.  Version
        1's paragraph, which a plan made before the flip rendered, went with its slot
        (``test_locate_judgment.TestThePromptsAreTheFlipsOnly``)."""
        self.assertEqual(vocab.SPAN_END_RULE, vocab.SPAN_END_STATEMENT_CUT)
        prompt = self.prompt
        flat = self.flat
        self.assertIn("**Where a passage ends**: at the earlier of the end of its paragraph or "
                      "list item", flat)
        self.assertIn("less what separates the two (the space, a connective of the cut rules "
                      "above, a comma or semicolon)", flat)
        self.assertIn("So two passages never overlap", flat)
        self.assertIn("A quotation, an indented block, or a list item that holds paragraphs or a "
                      "list of its own, counts as one paragraph here", flat)
        self.assertIn("when narration you leave out follows it.\n- A single word", prompt)
        self.assertIn("The gate holds the cut's mechanical part", flat)
        self.assertIn("The next passage's start ends each one", flat)
        for text in ("**Where a passage ends**: where its paragraph or list item ends",
                     "So give `until` to every passage that ends before its paragraph does",
                     "End each where it ends (`until`, below) when its paragraph goes on"):
            with self.subTest(text=text):
                self.assertNotIn(text, flat)
        # the paragraph glues no bullet to the next
        self.assertNotRegex(prompt, r"\{\{")
        self.assertNotRegex(prompt, r"\S- \*\*|[.:]- [A-Z]")

    def test_the_labelers_fields_are_the_vocabularys(self):
        shapes = re.search(r"const SHAPES = \[(.*?)\]", LABELER).group(1)
        self.assertEqual(tuple(re.findall(r"'([a-z_]+)'", shapes)), vocab.COMPOUND_SHAPE_ORDER)
        fields = LABELER[LABELER.index("const CUT_FIELDS"):LABELER.index("const ENUM_SCHEMA")]
        self.assertIn(f"minItems: {vocab.MIN_CONJUNCTS}, maxItems: {vocab.MAX_CONJUNCTS}", fields)
        self.assertIn("required: ['id', 'text'],\n    additionalProperties: false,", fields)
        self.assertEqual(LABELER.count("...CUT_FIELDS"), 2)
        # both inventory schemas require every passage's `statement`
        self.assertEqual(LABELER.count("required: ['quote', 'passage_kind', 'scope', 'statement'"),
                         2)

    def test_the_labelers_bundle_says_the_vocabularys_relations(self):
        """The flip verifier: the label schema's ``bundle`` description names the relations
        a bundle's parts bear in words, and nothing held them to ``BUNDLE_RELATIONS``, which
        the transcriber enforces (``bench.gold.gold_bundle``); a part's enum is
        ``CONJUNCT_RELATIONS``."""
        conjunct = re.search(r"const CONJUNCT_RELATIONS = \[(.*?)\]", LABELER).group(1)
        self.assertEqual(set(re.findall(r"'(\w+)'", conjunct)), set(vocab.CONJUNCT_RELATIONS))
        schema = LABELER[LABELER.index("const LABEL_SCHEMA"):LABELER.index("const CUT_FIELDS")]
        description = re.search(r"bundle: \{.*?description: \"(.*?)\" \}", schema, re.S).group(1)
        strong = sorted(vocab.BUNDLE_RELATIONS, key=vocab.RELATION_STRENGTH.__getitem__)
        self.assertIn(f"each with a conjunct_relation of {', '.join(strong[:-1])} or "
                      f"{strong[-1]},", description)
        named = {w for w in re.findall(r"\b(\w+Match|hasPart)\b", description)}
        self.assertEqual(named, set(vocab.BUNDLE_RELATIONS) | {vocab.HAS_PART})

    def test_the_lists_rule_asks_for_nothing_the_labeler_cannot_give(self):
        """The labeler's passages have no `until` (gold is anchored on its quote), so the
        shared Lists rule names none; the prompt's end paragraph says when to give one."""
        lines = {re.match(r"- \*\*(.*?)\*\*", line).group(1): line for line in cut_rule_lines()}
        self.assertNotIn("until", lines["Lists."])
        self.assertNotIn("until", re.search(r"CUT_FIELDS = \{(.*?)\n\}", LABELER, re.S).group(1))
        self.assertIn("one quote per statement", labeler_cut_rules())

    def test_what_the_prompt_kept(self):
        """The PD5 lines and the scope line other tests hold the labeler to stay."""
        for needle in ("**Only definitions, theorems, equations and declarative statements about "
                       "mathematical objects are `formal_math` passages** (the maintainer's PD5)",
                       "write it as a passage scoped `expository`, which is never located and "
                       "gets no status, and otherwise leave it out",
                       "`formal_math` — mathematics a library could state · `expository` — "
                       "history, motivation, applications told about mathematics, and proof "
                       "narration and commentary (PD5) · `non_math` — anything else",
                       "Finders that share a start are one passage"):
            with self.subTest(needle=needle[:30]):
                self.assertIn(needle, self.flat)

    def test_the_examples_are_synthetic(self):
        for text, name in ((self.prompt, "prompt"), (labeler_cut_rules(), "labeler"),
                           (" ".join(cut_rule_lines()), "vocabulary")):
            with self.subTest(text=name):
                self.assertEqual(re.findall(r"#([0-9a-f]{8})\b", text), [])
                self.assertEqual(re.findall(r"\bQ[1-9][0-9]{3,}\b", text), [])
        # the context rule's example is the widget one, never an article's sentence
        lines = {re.match(r"- \*\*(.*?)\*\*", line).group(1): line for line in cut_rule_lines()}
        self.assertIn('("Let W be a finite widget.")', lines["Context."])
        self.assertIn('("for a finite widget W, …")', lines["Context."])


if __name__ == "__main__":
    unittest.main()
