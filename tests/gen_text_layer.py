"""Generate tests/data/locate/text-layer.json from ``$WIKILEAN``'s own text layer.

New code, for references/locate-documents.md section 7 ("the layer"): the
Literature Links text layer has ONE definition, ``$WIKILEAN``'s -- the rule its
``site/anchor_resolve.py`` module docstring states and its ``text_layer``
implements -- and ``scripts/sources/render_html.py`` (``web-html/3``) is a
conforming implementation.  The conformance fixture pins the two together
without vendoring a line of ``$WIKILEAN``'s code: each case below is a synthetic,
MediaWiki-shaped HTML body (no text of any real article; CLAUDE.md section 1),
and the fixture records the layer ``$WIKILEAN``'s real function gives for it,
stamped with the commit and the sha256 of the module that gave it.
``tests/test_locate_text_layer.py`` replays every case through this repository's
renderer.

Where the two layers differ the case says so: its ``divergence`` is the reason,
its ``departs`` names the side (or sides) that is not the HTML5 parser there,
and the fixture marks it ``known_divergence`` and records this renderer's layer
beside ``$WIKILEAN``'s.  Most are where ``$WIKILEAN``'s regular-expression
scanner reads bytes differently from the HTML5 tokenizer, which this renderer
follows; a few are where this renderer's tree builder is not HTML5's, on markup
MediaWiki's parser output does not carry (a ``<template>`` element), which a
renderer may follow only under a new id.  The generator refuses to write a
fixture in which an unmarked case differs or a marked one no longer does, so a
regeneration from a newer ``$WIKILEAN`` commit, or under a new renderer id, says
exactly which divergences it closed or opened.

Run it against a read-only ``$WIKILEAN`` checkout::

    python3 tests/gen_text_layer.py --wikilean <checkout> [--check]

``text_layer`` runs in a separate ``python3 -I -S`` process whose working
directory is a fresh temporary one: the module's bytes are read here, hashed,
and executed there from the bytes, so nothing is imported from inside the
checkout and nothing (no bytecode) is written into it.  The commit is read with
``git rev-parse`` (read-only), and the working file must be that commit's blob.
``--check`` compares the result with the committed fixture instead of writing it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FIXTURE = HERE / "data" / "locate" / "text-layer.json"
SCHEMA = "cf-text-layer-conformance/1"
#: the module and function of ``$WIKILEAN`` that define the layer
MODULE = "site/anchor_resolve.py"
FUNCTION = "text_layer"
#: the placeholder this repository writes for the consumer's repository (CLAUDE.md section 1)
REPOSITORY = "$WIKILEAN"
#: the sides a known divergence names as not following the HTML5 parser
PRODUCER_SIDE = REPOSITORY
THIS_SIDE = "formal-companion"


@dataclass(frozen=True)
class Case:
    name: str
    why: str
    html: str
    #: why ``$WIKILEAN``'s layer is not this renderer's here; None: they must agree
    divergence: str | None = None
    #: which side is not the HTML5 parser where they diverge: ``$WIKILEAN``'s
    #: scanner, this renderer, or both
    departs: tuple[str, ...] = (PRODUCER_SIDE,)


# --------------------------------------------------------------------------
# MediaWiki-shaped building blocks (every page, title and number is made up)
# --------------------------------------------------------------------------

def imath(tex: str) -> str:
    """An inline formula as MediaWiki's Math extension writes it: MathML, annotation, image."""
    alt = "{\\displaystyle " + tex + "}"
    return ('<span class="mwe-math-element mwe-math-element-inline"><span class="mwe-math-mathml-inline '
            'mwe-math-mathml-a11y" style="display: none;"><math xmlns="http://www.w3.org/1998/Math/MathML" '
            f'alttext="{alt}"><semantics><mrow class="MJX-TeXAtom-ORD"><mstyle displaystyle="true" '
            f'scriptlevel="0"><mi>{tex}</mi></mstyle></mrow><annotation encoding="application/x-tex">{alt}'
            '</annotation></semantics></math></span><img src="/media/math/render/svg/0123abcd" '
            'class="mwe-math-fallback-image-inline mw-invert skin-invert" aria-hidden="true" '
            f'style="vertical-align: -0.338ex; width:2.4ex; height:2.1ex;" alt="{alt}" /></span>')


def dmath(tex: str) -> str:
    """A displayed formula in its ``dl``/``dd``, as MediaWiki writes a ``:<math>`` line."""
    alt = "{\\displaystyle " + tex + "}"
    return ('<dl><dd><span class="mwe-math-element mwe-math-element-block"><span class="mwe-math-mathml-display '
            'mwe-math-mathml-a11y" style="display: none;"><math display="block" xmlns="http://www.w3.org/1998/'
            f'Math/MathML" alttext="{alt}"><semantics><mrow class="MJX-TeXAtom-ORD"><mstyle displaystyle="true" '
            f'scriptlevel="0"><mi>F</mi><mo>=</mo><mi>W</mi></mstyle></mrow><annotation encoding="application/x-tex">'
            f'{alt}</annotation></semantics></math></span><img src="/media/math/render/svg/4567cdef" '
            'class="mwe-math-fallback-image-display mw-invert skin-invert" aria-hidden="true" '
            f'style="width:9ex; height:2.8ex;" alt="{alt}" /></span></dd></dl>')


def heading(level: int, ident: str, title: str) -> str:
    """A heading in the current ``mw-heading`` markup, with its edit link."""
    return (f'<div class="mw-heading mw-heading{level}"><h{level} id="{ident}">{title}</h{level}>'
            '<span class="mw-editsection"><span class="mw-editsection-bracket">[</span><a href="/w/index.php?'
            f'title=Flange&amp;action=edit&amp;section=1" title="Edit section: {title}"><span>edit</span></a>'
            '<span class="mw-editsection-bracket">]</span></span></div>\n')


def legacy_heading(level: int, ident: str, title: str) -> str:
    """A heading in the older ``mw-headline`` markup, with its edit link."""
    return (f'<h{level}><span class="mw-headline" id="{ident}">{title}</span><span class="mw-editsection">'
            '<span class="mw-editsection-bracket">[</span><a href="/w/index.php?title=Flange&amp;action=edit'
            f'&amp;section=1" title="Edit section: {title}">edit</a><span class="mw-editsection-bracket">]</span>'
            f'</span></h{level}>\n')


HATNOTE_STYLE = ('<style data-mw-deduplicate="TemplateStyles:r1000001">.mw-parser-output .hatnote{font-style:italic}'
                 '.mw-parser-output div.hatnote{padding-left:1.6em;margin-bottom:0.5em}'
                 '@media print{body.ns-0 .mw-parser-output .hatnote{display:none!important}}</style>')
REFLIST_STYLE = ('<style data-mw-deduplicate="TemplateStyles:r1000002">.mw-parser-output .reflist{margin-bottom:0.5em;'
                 'list-style-type:decimal}</style>')
CS1_STYLE = ('<style data-mw-deduplicate="TemplateStyles:r1000003">.mw-parser-output cite.citation{font-style:inherit;'
             'word-wrap:break-word}.mw-parser-output .citation q{quotes:"\\"""\\"""\'""\'"}'
             '.mw-parser-output .id-lock-free a{background:url("/x/Lock-green.svg")right 0.1em center/9px no-repeat}'
             '</style>')
LIMIT_REPORT = ('\n<!-- \nNewPP limit report\nParsed by mw-api-ext.example\nCached time: 20260101000000\n'
                'Cache expiry: 2592000\nCPU time usage: 0.101 seconds\n-->\n'
                '<!--\nTransclusion expansion time report (%,ms,calls,template)\n100.00%   10.000      1 -total\n-->\n')
ESI = '<!--esi <esi:include src="/esitest-0000/content" /> -->'
REF = ('<sup id="cite_ref-1" class="reference"><a href="#cite_note-1"><span class="cite-bracket">&#91;</span>1'
       '<span class="cite-bracket">&#93;</span></a></sup>')
THEOREM = ('<div class="math_theorem" style="margin:1em 2em;"><strong class="theorem-name">Flange theorem'
           '</strong>&#160;&mdash;&#32;Every finite widget ' + imath('W') + ' has a flange.</div>\n')
#: the 25 White_Space code points, one between each pair of words
ALL_WHITE_SPACE = "".join(chr(c) for c in (
    *range(0x09, 0x0E), 0x20, 0x85, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F,
    0x205F, 0x3000))


def wrap(inner: str) -> str:
    """``parse.text``'s outer element."""
    return '<div class="mw-content-ltr mw-parser-output" lang="en" dir="ltr">' + inner + '</div>'


def composite() -> str:
    """A whole MediaWiki-shaped page: everything above, in a page's order."""
    return wrap(
        '<div class="shortdescription nomobile noexcerpt noprint searchaux" style="display:none">Made-up '
        'notion in widget theory</div>\n'
        + HATNOTE_STYLE + '<div role="note" class="hatnote navigation-not-searchable">Not to be confused with '
        '<a href="/wiki/Gadget" title="Gadget">Gadget</a>.</div>\n'
        '<p class="mw-empty-elt">\n</p>\n'
        '<p>In <a href="/wiki/Widget_theory" title="Widget theory">widget theory</a>, a <b>flange</b> of a widget '
        + imath('W') + ' is a subset closed under the widget operation.' + REF + ' The flange of '
        'order&#160;<i>n</i> is written <span class="texhtml"><i>F</i><sub><i>n</i></sub></span>.\n</p>\n'
        '<meta property="mw:PageProp/toc" />\n'
        + heading(2, 'Definition', 'Definition') + '<p>Let ' + imath('W') + ' be a widget. Then:\n</p>\n'
        + dmath('F=W') + THEOREM
        + heading(2, 'References', 'References') + REFLIST_STYLE + '<div class="reflist">\n<div '
        'class="mw-references-wrap"><ol class="references">\n<li id="cite_note-1"><span class="mw-cite-backlink">'
        '<b><a href="#cite_ref-1">^</a></b></span> <span class="reference-text">' + CS1_STYLE
        + '<cite id="CITEREFExample2001" class="citation book cs1">Example, A. (2001). <i>Widgets</i>. Synthetic '
        'Press. p.&#160;12.</cite></span>\n</li>\n</ol></div></div>' + LIMIT_REPORT + ESI)


# --------------------------------------------------------------------------
# the cases
# --------------------------------------------------------------------------

_TOKENIZER = "this renderer follows the HTML5 tokenizer"
_TEMPLATE_SCOPE = ("MediaWiki's parser output carries no <template> element, so no page reaches this, "
                   "and following HTML5 here moves layers: a new renderer id, never an edit of "
                   "this one")

CASES: tuple[Case, ...] = (
    # ---- the rule, on MediaWiki's own shapes -------------------------------
    Case("plain-paragraph", "a paragraph with a link and a bold term",
         wrap('<p>In <a href="/wiki/Widget_theory" title="Widget theory">widget theory</a>, a <b>flange</b> '
              'is a subset.\n</p>')),
    Case("hidden-text-counts", "a display:none short description and an empty paragraph: hidden text is text",
         wrap('<div class="shortdescription nomobile noexcerpt noprint searchaux" style="display:none">'
              'Made-up notion</div>\n<p class="mw-empty-elt">\n</p>\n<p>Body text.\n</p>')),
    Case("inline-math", "MathML token text and the TeX annotation count; the fallback image's alt does not",
         wrap('<p>A widget ' + imath('W') + ' has a flange.\n</p>')),
    Case("display-math", "a displayed formula in its dl/dd",
         wrap('<p>Define:\n</p>\n' + dmath('F=W'))),
    Case("citation-marker", "a citation marker, its brackets written as character references",
         wrap('<p>A claim.' + REF + ' More.\n</p>')),
    Case("comments", "the limit report and an ESI comment are comments, not text",
         wrap('<p>Text.\n</p>' + LIMIT_REPORT + ESI)),
    Case("heading", "an mw-heading heading with its edit link: the link's text counts",
         wrap('<p>Lead.\n</p>\n' + heading(2, 'Definition', 'Definition') + '<p>Body.\n</p>')),
    Case("legacy-heading", "an h2 > span.mw-headline heading with its edit link",
         wrap('<p>Lead.\n</p>\n' + legacy_heading(2, 'Definition', 'Definition') + '<p>Body.\n</p>')),
    Case("templatestyles-hatnote", "a TemplateStyles style before a hatnote: style text is no text",
         wrap(HATNOTE_STYLE + '<div role="note" class="hatnote navigation-not-searchable">For the related '
              'notion, see <a href="/wiki/Gadget" title="Gadget">Gadget</a>.</div>\n<p>Body.\n</p>')),
    Case("templatestyles-in-heading", "a TemplateStyles style inside a heading (an anchor template)",
         wrap('<div class="mw-heading mw-heading3"><h3 id="Flange_order"><style data-mw-deduplicate='
              '"TemplateStyles:r1000004">.mw-parser-output .vanchor>:target~.vanchor-text{background-color:'
              '#ebf4ff}</style><span class="vanchor"><span id="Order"></span><span class="vanchor-text">Flange '
              'order</span></span></h3></div>\n<p>Body.\n</p>')),
    Case("templatestyles-inline", "a TemplateStyles style inside a paragraph, as a {{math}}-style template writes it",
         wrap('<p>The order is <style data-mw-deduplicate="TemplateStyles:r1000005">.mw-parser-output '
              '.texhtml{font-family:serif}</style><span class="texhtml"><i>n</i></span> here.\n</p>')),
    Case("templatestyles-in-references", "CS1's style inside a reference list",
         wrap(heading(2, 'References', 'References') + REFLIST_STYLE + '<div class="reflist">\n<div '
              'class="mw-references-wrap"><ol class="references">\n<li id="cite_note-1"><span '
              'class="mw-cite-backlink"><b><a href="#cite_ref-1">^</a></b></span> <span class="reference-text">'
              + CS1_STYLE + '<cite id="CITEREFExample2001" class="citation book cs1">Example, A. (2001). '
              '<i>Widgets</i>. Synthetic Press. p.&#160;12.</cite></span>\n</li>\n</ol></div></div>')),
    Case("nbsp-numeric", "&#160;, as MediaWiki writes a no-break space: White_Space, so one space",
         wrap('<p>The flange of order&#160;<i>n</i> has p.&#160;12 entries.\n</p>')),
    Case("nbsp-named", "&nbsp;", wrap('<p>Order&nbsp;3 only.\n</p>')),
    Case("nbsp-literal", "a literal U+00A0", wrap('<p>Order\u00a03 only.\n</p>')),
    Case("thin-space", "U+2009 literal and as &thinsp;", wrap('<p>Value 1\u20092 and 3&thinsp;4.\n</p>')),
    Case("narrow-nbsp", "U+202F", wrap('<p>Value 10\u202f000.\n</p>')),
    Case("texhtml", "a {{math}}-style span with no-break spaces",
         wrap('<p>It is <span class="texhtml"><i>x</i><sup>2</sup>&#160;+&#160;1</span>.\n</p>')),
    Case("not-white-space", "U+200B, U+2060 and U+00AD have no White_Space property and stay",
         wrap('<p>long\u200bword join\u2060ed soft\u00adhyphen and &zwnj;x.\n</p>')),
    Case("every-white-space", "each of the 25 White_Space code points, between two words",
         wrap('<p>' + "".join(f"w{i}{ch}" for i, ch in enumerate(ALL_WHITE_SPACE)) + 'end</p>')),
    Case("white-space-entities", "&ensp;, &emsp;, &Tab;, &NewLine; and &#13; decode to White_Space",
         wrap('<p>a&ensp;b&emsp;c&Tab;d&NewLine;e&#13;f\n</p>')),
    Case("no-space-at-element-boundaries", "cells, blocks and a br join with no space inserted",
         wrap('<table class="wikitable"><tbody><tr><th>Field</th><td>Widget</td><td>theory</td></tr></tbody>'
              '</table><div>tail</div><p>head one<br />two</p>')),
    Case("infobox", "table cells with whitespace between them",
         wrap('<table class="infobox"><tbody><tr><th scope="row">Field</th><td>Widget theory</td></tr>'
              '</tbody></table>\n<p>Body.\n</p>')),
    Case("theorem-box", "a theorem box with &#160;&mdash;&#32; after its name", wrap(THEOREM)),
    Case("lists", "nested lists and a definition list",
         wrap('<ul><li>one\n<ul><li>one point one</li></ul></li><li>two</li></ul>\n<ol><li>first</li></ol>\n'
              '<dl><dt>Term</dt>\n<dd>its definition</dd></dl>')),
    Case("figure", "a figure with its caption",
         wrap('<figure class="mw-default-size" typeof="mw:File/Thumb"><a href="/wiki/File:W.svg" '
              'class="mw-file-description"><img src="/upload/w.svg" decoding="async" width="220" height="200" '
              'class="mw-file-element" /></a><figcaption>A widget.</figcaption></figure>\n<p>Body.\n</p>')),
    Case("void-elements", "link and meta elements hold no text",
         wrap('<link rel="mw-deduplicated-inline-style" href="mw-data:TemplateStyles:r1000001" />'
              '<meta property="mw:PageProp/toc" />\n<p>Body.\n</p>')),
    Case("character-references", "named, decimal and hexadecimal references with their ';'",
         wrap('<p>R&amp;D, a &lt; b &gt; c, &quot;q&quot;, &#8212; &#x2014; &mdash; &AMP; &foo; and A & B.\n</p>')),
    Case("escaped-markup-in-text", "markup written as text: '<' before a digit, escaped tags",
         wrap('<p>a <3 b and &lt;b&gt;not bold&lt;/b&gt;.\n</p>')),
    Case("non-bmp", "characters outside the BMP are one code point each",
         wrap('<p>The field \U0001d53d<sub>2</sub> and \U0001f600.\n</p>')),
    Case("line-endings", "CR, CRLF and form feeds are White_Space",
         wrap('<p>one\r\ntwo\rthree\x0cfour\n</p>')),
    Case("script", "a script element's text is no text",
         wrap('<p>A</p><script>var hidden = 1; if (a < b) { x = "</p>"; }</script><p>B</p>')),
    Case("template", "a template element's content is no text",
         wrap('<p>A</p><template><p>tpl</p><h2>Hidden</h2></template><p>B</p>')),
    Case("style-case-and-raw-text", "an upper-case STYLE, and a style whose CSS holds '<!--' and markup",
         wrap('<p>a<STYLE>.x{}</STYLE>b<style><!-- .y{content:"<b>"} --></style>c</p>')),
    Case("style-unclosed", "a style never closed runs to the end",
         wrap('<p>a</p><style>.x{color:red}')),
    Case("code-comma-slashes", "', //' in text (the resolve path never rewrites it)",
         wrap('<p>Call <code>f(a, //b)</code> now.\n</p>')),
    Case("composite", "a whole MediaWiki-shaped page", composite()),

    # ---- where $WIKILEAN's scanner is not the HTML5 tokenizer ---------------
    Case("pre-leading-newline", "a pre whose content starts with a newline",
         wrap('<p>Example:</p><pre>\nx = 1\n</pre>'),
         "the HTML5 tree builder drops one newline right after a pre start tag, so the layer is "
         "'Example:x = 1'; $WIKILEAN's scanner keeps it as White_Space, giving 'Example: x = 1'; "
         + _TOKENIZER),
    Case("pre-leading-crlf", "a pre whose content starts with CRLF",
         wrap('<p>Example:</p><pre>\r\nx = 1</pre>'),
         "the input stream turns CRLF into one newline, which the tree builder then drops after pre; "
         "$WIKILEAN's scanner keeps it as White_Space; " + _TOKENIZER),
    Case("textarea-leading-newline", "a textarea whose content starts with a newline",
         wrap('<p>Field:</p><textarea>\nvalue</textarea>'),
         "the HTML5 tree builder drops one newline right after a textarea start tag; $WIKILEAN's "
         "scanner keeps it as White_Space; " + _TOKENIZER),
    Case("listing-leading-newline", "a listing whose content starts with a newline",
         wrap('<p>x</p><listing>\ny</listing>'),
         "the HTML5 tree builder drops one newline right after a listing start tag, as after pre, so "
         "the layer is 'xy'; $WIKILEAN's scanner keeps it as White_Space, giving 'x y'; " + _TOKENIZER),
    Case("reference-without-semicolon", "character references with no ';'",
         wrap('<p>R&amp D and &copy2016 and order&#160n.</p>'),
         "HTML5 decodes '&amp', '&copy' and '&#160' with no ';' (a parse error, still decoded); "
         "$WIKILEAN's scanner decodes only '&...;' and keeps the rest as text; " + _TOKENIZER),
    Case("reference-longer-than-16", "a named reference longer than sixteen characters",
         wrap('<p>&CounterClockwiseContourIntegral; x</p>'),
         "HTML5 knows every named reference; $WIKILEAN's scanner looks for the ';' within sixteen "
         "characters of the '&' and keeps a longer one as text; " + _TOKENIZER),
    Case("abrupt-comments", "the comments '<!-->' and '<!-- --!>'",
         wrap('<p>A<!-->B<!-- c --!>C</p>'),
         "HTML5 closes '<!-->' at once and a comment at '--!>'; $WIKILEAN's scanner closes a comment "
         "only at a '-->' after its '<!--', so it reads the rest of the page as one comment; "
         + _TOKENIZER),
    Case("abrupt-comment-dash", "the empty comment '<!--->'",
         wrap('<p>a<!--->b-->c</p>'),
         "HTML5 closes '<!--->' at once (an abrupt closing of an empty comment), so 'b-->c' is text; "
         "$WIKILEAN's scanner closes a comment only at a '-->' after its '<!--', the one after 'b'; "
         + _TOKENIZER),
    Case("gt-in-quoted-attribute", "a '>' inside a quoted attribute value",
         wrap('<p><span title="a>b">x</span> y</p>'),
         "HTML5 reads a quoted attribute value to its closing quote; $WIKILEAN's scanner ends a tag at "
         "its first '>', so the rest of the value becomes text; " + _TOKENIZER),
    Case("comment-in-attribute", "a '<!-- -->' inside a quoted attribute value",
         wrap('<p><span title="<!-- x -->">a</span> b</p>'),
         "HTML5 reads the attribute value as a value; $WIKILEAN's scanner ends the tag at the '>' of "
         "the '-->' inside it; " + _TOKENIZER),
    Case("cdata-in-html", "a CDATA section in HTML content",
         wrap('<p>a<![CDATA[x]]>b</p>'),
         "in HTML content HTML5 reads '<![CDATA[x]]>' as a bogus comment, no text; $WIKILEAN's "
         "scanner keeps it as text; " + _TOKENIZER),
    Case("markup-declarations", "a doctype, a declaration and a processing instruction in the body",
         wrap('<p>a<!DOCTYPE html>b<!x>c<?php x ?>d</p>'),
         "HTML5 reads each as a doctype or bogus comment, no text; $WIKILEAN's scanner keeps them as "
         "text; " + _TOKENIZER),
    Case("bogus-end-tags", "the end tags '</>' and '</ x>'",
         wrap('<p>a</>b</ x>c</p>'),
         "HTML5 ignores '</>' and reads '</ x>' as a bogus comment; $WIKILEAN's scanner keeps both as "
         "text; " + _TOKENIZER),
    Case("rcdata-and-rawtext", "markup inside textarea, title and xmp",
         wrap('<p>a<textarea><b>t</b></textarea>b<title>&amp;<i>u</i></title>c<xmp><b>x</b></xmp>d</p>'),
         "HTML5 reads the content of textarea and title as text with references decoded, and that of "
         "xmp as raw text, so the markup inside is text; $WIKILEAN's scanner reads it as tags; "
         + _TOKENIZER),
    Case("skipped-end-tag-prefix", "'</styles>' inside a style, '</templates>' inside a template",
         wrap('<p>a<style>.x{}</styles>b</style>c<template>d</templates>e</template>f</p>'),
         "HTML5 ends a style's raw text only at '</style' followed by whitespace, '/' or '>', and "
         "'</templates>' is the end tag of another element, ignored, so 'b' and 'e' are skipped "
         "content and the layer is 'acf'; $WIKILEAN's scanner ends a skipped element at the first "
         "'</style' or '</template' even as the prefix of a longer name, giving 'abcef'; "
         + _TOKENIZER),
    Case("skipped-name-prefix", "a start tag whose name only begins with 'style'",
         wrap('<p>a<style.x>b</style.x>c</p>'),
         "HTML5 reads a tag name up to whitespace, '/' or '>', so '<style.x>' is an unknown element "
         "whose text counts ('abc'); $WIKILEAN's scanner reads a tag name as [A-Za-z][A-Za-z0-9:-]*, "
         "'style', and skips to the next '</style' ('ac'); " + _TOKENIZER),
    Case("nested-template", "a template inside a template",
         wrap('<p>A<template>a<template>b</template>c</template>d</p>'),
         "in HTML5 everything inside the outer template is its content, so the layer is 'Ad'; "
         "$WIKILEAN's scanner skips to the first '</template' and keeps 'c'; " + _TOKENIZER),
    Case("cdata-in-mathml", "a CDATA section inside MathML",
         wrap('<p><math><mi><![CDATA[x]]></mi></math>y</p>'),
         "here NEITHER follows HTML5, which makes a CDATA section in foreign content text ('xy'): "
         "$WIKILEAN's scanner keeps the markup as text, and html.parser reads every CDATA section as "
         "a declaration, which this renderer drops ('y'); MediaWiki's math output holds no CDATA",
         departs=(PRODUCER_SIDE, THIS_SIDE)),
    Case("svg-self-closing-style", "a self-closing style inside SVG",
         wrap('<p>alpha <svg><style/>beta</svg> gamma words here</p>'),
         "HTML5 honours the self-closing flag on an SVG element, so '<style/>' is an empty SVG style and "
         "'beta' is text ('alpha beta gamma words here'); $WIKILEAN's scanner reads it as a style start "
         "tag and skips to the next '</style', of which there is none, so nothing after it is text "
         "('alpha'); MediaWiki's math output holds no self-closing style or script; " + _TOKENIZER),
    Case("mathml-self-closing-script", "a self-closing script inside MathML",
         wrap('<p>alpha <math><mi>x</mi><script/>y</math> gamma</p>'),
         "HTML5 honours the self-closing flag on a MathML element, so '<script/>' is empty and 'y' and "
         "' gamma' are text ('alpha xy gamma'); $WIKILEAN's scanner reads it as a script start tag and "
         "skips to the next '</script', of which there is none ('alpha x'); MediaWiki's math output "
         "holds no self-closing style or script; " + _TOKENIZER),

    # ---- where this renderer's tree builder is not HTML5's ------------------
    # A template is a scope boundary in HTML5: no tag inside one closes an
    # element outside it.  web-html/3's tree builder does not make it one, so a
    # tag inside a template can close an element around it, the template with
    # it, and what follows leaks into the layer.  $WIKILEAN's scanner skips the
    # template's content and so gives HTML5's layer.
    Case("template-in-list-item", "an li start tag inside a template closes no list item outside it",
         wrap('<ul><li>a<template><li>b</li></template>c</li></ul>'),
         "the li start tag inside the template closes the li outside it, and the template with it, so "
         "'b' and 'c' are text here ('abc'); HTML5 and $WIKILEAN give 'ac'; " + _TEMPLATE_SCOPE,
         departs=(THIS_SIDE,)),
    Case("template-in-definition-list", "a dd start tag inside a template closes no dd outside it",
         wrap('<dl><dd>a<template><dd>b</template>c</dd></dl>'),
         "the dd start tag inside the template closes the dd outside it, and the template with it "
         "('abc'); HTML5 and $WIKILEAN give 'ac'; " + _TEMPLATE_SCOPE,
         departs=(THIS_SIDE,)),
    Case("template-in-table-cell", "td and tr start tags inside a template close no cell or row outside it",
         wrap('<table><tbody><tr><td>a<template><td>b</template>c</td></tr><tr><td>d<template><tr><td>e'
              '</template>f</td></tr></tbody></table>'),
         "the td and tr start tags inside the templates close the cell and the row outside them, and "
         "the templates with them ('abcdef'); HTML5 and $WIKILEAN give 'acdf'; " + _TEMPLATE_SCOPE,
         departs=(THIS_SIDE,)),
    Case("template-and-stray-end-tags", "an end tag inside a template closes nothing outside it",
         wrap('<p>a<template>b</p>c</template>d</p><div><template><p>x</div>y</template>z</div>'),
         "the '</p>' and '</div>' inside the templates close the p and the div outside them, and the "
         "templates with them ('acdyz'); HTML5 and $WIKILEAN give 'adz'; " + _TEMPLATE_SCOPE,
         departs=(THIS_SIDE,)),
)


# --------------------------------------------------------------------------
# running $WIKILEAN's function, out of its tree
# --------------------------------------------------------------------------

_CHILD = r'''
import json, sys
request = json.load(sys.stdin)
namespace = {"__name__": "anchor_resolve"}
exec(compile(request["source"], request["module"], "exec"), namespace)
text_layer = namespace[request["function"]]
json.dump({"layers": [text_layer(html)[0] for html in request["cases"]],
           "white_space": sorted(ord(ch) for ch in namespace["WHITE_SPACE"]),
           "skip_elements": sorted(namespace["_SKIP_ELEMENTS"])}, sys.stdout)
'''


def _git(checkout: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True,
                          timeout=60)
    if done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in the checkout: {done.stderr.strip()}")
    return done.stdout.strip()


def producer_stamp(checkout: Path, commit: str | None = None) -> tuple[dict, str]:
    """``(stamp, module source)``; refused when the working file is not the commit's blob."""
    path = checkout / MODULE
    data = path.read_bytes()
    commit = commit or _git(checkout, "rev-parse", "HEAD")
    blob = _git(checkout, "rev-parse", f"{commit}:{MODULE}")
    mine = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    if blob != mine:
        raise SystemExit(f"{MODULE} in the checkout is not the one commit {commit} holds; "
                         "generate from a clean checkout")
    stamp = {"repository": REPOSITORY, "commit": commit, "module": MODULE,
             "module_sha256": hashlib.sha256(data).hexdigest(), "function": FUNCTION}
    return stamp, data.decode("utf-8")


def wikilean_layers(source: str, htmls: list[str]) -> dict:
    """Run ``text_layer`` over every body in a ``python3 -I -S`` process in a temporary directory."""
    request = json.dumps({"source": source, "module": MODULE, "function": FUNCTION,
                          "cases": htmls})
    with tempfile.TemporaryDirectory() as scratch:
        done = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", _CHILD], input=request,
                              capture_output=True, text=True, cwd=scratch, timeout=300)
    if done.returncode != 0:
        raise SystemExit(f"{MODULE}'s {FUNCTION} failed: {done.stderr.strip()[-2000:]}")
    return json.loads(done.stdout)


def our_layer(html: str) -> str:
    """This repository's layer of a body, as adopt then render computes it."""
    from sources import render_html

    return render_html.render(render_html.canonical_body(html.encode("utf-8"))).layer


def build(checkout: Path, commit: str | None = None) -> dict:
    """The fixture document; SystemExit naming every case that contradicts its mark."""
    from sources import render_html

    stamp, source = producer_stamp(checkout, commit)
    produced = wikilean_layers(source, [case.html for case in CASES])
    stamp["white_space"] = produced["white_space"]
    stamp["skip_elements"] = produced["skip_elements"]
    problems: list[str] = []
    cases: list[dict] = []
    for case, theirs in zip(CASES, produced["layers"]):
        ours = our_layer(case.html)
        row = {"name": case.name, "why": case.why, "html": case.html, "layer": theirs}
        if case.divergence is None:
            if ours != theirs:
                problems.append(f"{case.name}: the layers differ and the case is not marked "
                                f"(theirs {theirs[:60]!r}, ours {ours[:60]!r})")
        else:
            if ours == theirs:
                problems.append(f"{case.name}: marked known_divergence, but the layers agree now; "
                                "drop the mark")
            row["known_divergence"] = {"reason": case.divergence, "departs": list(case.departs),
                                       "formal_companion_layer": ours}
        cases.append(row)
    if problems:
        raise SystemExit("refusing to write the fixture:\n  " + "\n  ".join(problems))
    return {
        "_schema": SCHEMA,
        "_doc": [
            "The Literature Links text layer, one definition: the rule $WIKILEAN's "
            "site/anchor_resolve.py states in its module docstring and implements as text_layer.",
            "Every case is a synthetic MediaWiki-shaped body; 'layer' is what that function gave "
            "for 'html', run from the module whose commit and sha256 are under 'producer'.",
            "formal-companion's renderer (scripts/sources/render_html.py) must give 'layer' on "
            "every case without 'known_divergence'. A known divergence is where the two differ: "
            "'departs' names the side that is not the HTML5 parser there ($WIKILEAN's scanner, "
            "this renderer, or both), its reason says why, and 'formal_companion_layer' is the "
            "layer this repository gives there.",
            "Generated by tests/gen_text_layer.py; replayed by tests/test_locate_text_layer.py. "
            "Regenerate it from a newer commit with "
            "`python3 tests/gen_text_layer.py --wikilean <checkout>`.",
        ],
        "renderer": render_html.RENDERER,
        "producer": stamp,
        "cases": cases,
    }


def dump(document: dict) -> str:
    """The fixture's one spelling: ASCII only (no invisible characters in the file), one-space indent."""
    return json.dumps(document, ensure_ascii=True, indent=1) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wikilean", required=True, type=Path,
                        help="a read-only checkout of the consumer's repository")
    parser.add_argument("--commit", help="the commit to stamp (default: the checkout's HEAD)")
    parser.add_argument("--check", action="store_true",
                        help="compare with the committed fixture instead of writing it")
    args = parser.parse_args(argv)
    text = dump(build(args.wikilean.expanduser().resolve(), args.commit))
    if args.check:
        same = FIXTURE.is_file() and FIXTURE.read_text(encoding="utf-8") == text
        print("the fixture is current" if same else "the fixture differs from a regeneration")
        return 0 if same else 1
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(text, encoding="utf-8")
    divergent = sum(1 for case in CASES if case.divergence)
    print(f"wrote {FIXTURE.name}: {len(CASES)} cases, {divergent} known divergences")
    return 0


if __name__ == "__main__":
    sys.exit(main())
