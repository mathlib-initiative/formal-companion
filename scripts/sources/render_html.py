"""Render one web source's canonical body into the views the locate stages read.

New code, implementing docs/proposals/wikipedia-locate.md section 2.1 ("Two text
views of one document") and section 2.2's head text, in the file shapes of
references/locate-documents.md section 7.  Standard library only: the body is
parsed with ``html.parser`` into a small tree, and every view is computed from
that one tree in one pass, so they cannot disagree about which characters a
passage covers.

**The renderer is frozen as** ``web-html/2`` (``cflib.vocab.RENDERER_WEB_HTML``):
a source's ``layer_sha256`` is pinned by every run, gate and score document, so
any change to the rule below that can move one character of any view -- the
layout of a view's file included -- is a new renderer id, never an edit of this
one; ``tests/test_locate_sources.py`` pins every view of a synthetic page to the
id.  ``web-html/1`` kept ``style`` and ``script`` text in the layer and
collapsed ASCII whitespace only, so it was no port of the consumer's text layer
(docs/proposals/wikipedia-locate.md section 2.1) and its layers matched none;
references/locate-documents.md section 7 lists what ``/2`` changed.  The rule,
in full:

* **The canonical body** (:func:`canonical_body`): the bytes are decoded as
  UTF-8 with one leading byte-order mark dropped (the WHATWG *UTF-8 decode*); a
  ``formatversion=2`` ``action=parse`` JSON response gives its ``parse.text``
  (``parse.text["*"]`` of a ``formatversion=1`` one), and anything that is no
  such response is the body itself.  Every HTML comment is then removed exactly
  as the HTML tokenizer delimits one in the data state (:func:`strip_comments`):
  from ``<!--`` to the first ``-->`` or ``--!>``, an abrupt ``<!-->`` or
  ``<!--->`` being a whole (empty) comment and an unterminated one running to
  the end, while a ``<!--`` inside a tag (an attribute value) or inside the raw
  text of ``script style xmp iframe noembed noframes textarea title`` is text and
  stays.  A body holding a lone surrogate is refused.  Its sha256 is
  ``html_sha256``, the source's ``source_sha``.
* **The tree.**  Tags are lower-cased by the parser; character references are
  decoded except inside ``script`` and ``style``, whose content is raw text.
  The void elements (``area base br col embed hr img input link meta param
  source track wbr``) never hold content.  A self-closing flag is honoured on a
  MathML or SVG element (``<mspace/>`` holds nothing) and ignored on an HTML one,
  as the HTML5 tree builder does: an element is MathML or SVG when it is a
  ``math`` or ``svg``, or its parent is one and it is no HTML integration point's
  child (SVG ``foreignObject desc title``; a MathML ``annotation-xml`` whose
  ``encoding`` is ``text/html`` or ``application/xhtml+xml``; a MathML ``mi mo mn
  ms mtext``, except for an ``mglyph`` or ``malignmark``) and no HTML element
  that breaks out of foreign content (``b``, ``div``, ``p``, ``span`` ... ).  An end tag closes the nearest open element
  of its name and everything opened inside it; one with no open element is
  ignored.  A ``p`` is closed by the start of a block (``address article aside
  blockquote center details dir div dl fieldset figcaption figure footer form
  h1-h6 header hr main menu nav ol p pre section table ul``) when no ``table``,
  ``td``, ``th``, ``caption``, ``button`` or ``object`` lies between; an ``li``
  by the next ``li`` of its list, a ``dt``/``dd`` by the next ``dt``/``dd`` of
  its ``dl``, a ``tr`` by the next ``tr`` of its table section, a ``td``/``th``
  by the next cell or row, an ``option`` by the next ``option``.  One newline
  (LF, CR or CRLF) right after a ``pre``, ``listing`` or ``textarea`` start tag is
  not text, as in the HTML5 tree builder.  A body that nests elements more than
  :data:`MAX_DEPTH` deep is refused.
* **The Literature Links text layer** (``layer.txt``) is the tree's DOM
  ``textContent`` without the ``script``, ``style`` and ``template`` elements
  -- every other text node in document order, MathML token text, the TeX
  annotation and citation markers all included -- with each maximal run of
  ``White_Space`` (:data:`WHITE_SPACE`, the 25 code points of the Unicode
  property: U+00A0, U+2009, U+202F and U+3000 among them) made one U+0020 and
  the ends stripped.  No space is added at element boundaries, and there is no
  other Unicode normalisation.  This is the Literature Links rule for HTML, the
  one the consumer's ``text_layer`` implements.
* **An element's layer span** is half-open, from its first non-whitespace text
  character to just after its last; an element with no such character has no
  span.  Its **kept span** is the same over the text outside the dropped regions
  listed under the reading copy: a block or a finder passage is measured by its
  kept span, so a citation marker at the start of a paragraph is never the start
  of a passage, and a paragraph that holds only dropped text is no block.
* **Formulas** (``formulas.json``): every element whose class tokens include
  ``mwe-math-element``, and every ``math`` element outside one, is a formula; its
  span is its layer span and its alttext is the first descendant ``math``
  element's ``alttext``, else the text of an ``annotation`` whose ``encoding`` is
  ``application/x-tex``, else the ``alt`` of an ``img`` whose class names
  ``mwe-math-fallback-image``.  A formula with no alttext is ordinary text; one
  with no span (an image only) contributes nothing to the layer, is counted in
  ``render.json`` as ``formulas_without_text``, and is written to the reading
  copy as added characters.  Formulas never nest.
* **The head text** (``head.txt``) is ``gate.locate_text.HeadMap`` over the layer
  and the formulas: each span replaced by ``⟦normalized alttext⟧``.
* **Sections** (``sections.json``): every ``h1``-``h6`` starts one, at the layer
  position of its first text; its title is its text outside the dropped regions
  listed under the reading copy (``.mw-editsection``, a ``sup.reference``, an
  inline ``style`` ...), ``White_Space``-collapsed; its fragment is its own ``id``, else the ``id`` of a
  ``.mw-headline`` inside it, else null; its path is the titles of the open
  headings of lower level, then its own.  The lead (path ``[]``, fragment null)
  comes first, and the spans tile the layer.
* **Blocks** (``paragraphs.json``): every ``p``, ``li``, ``dd``, ``dt`` and
  ``blockquote``, every theorem box (class token ``math_theorem`` or
  ``math-theorem``) and every display formula (a formula whose class names
  ``mwe-math-element-block``, a ``div`` formula, or a ``math`` with
  ``display="block"``) that has a kept span and lies outside the dropped regions
  below.  Blocks may nest (an ``li`` holding a ``p`` is two entries).
  ``formulas`` counts the formulas starting inside; ``math`` is true when there
  is one, or when a deterministic finder's passage starts inside.
* **The deterministic finders** (``finders.json``, ``cflib.vocab.
  DETERMINISTIC_FINDERS``): ``theorem_box`` -- each theorem box, ``term`` its
  ``.theorem-name`` (or ``.math_theorem_name``) text, else its first ``b`` or
  ``strong`` (a term is always kept text, ``White_Space``-collapsed); ``bold_definition`` -- each ``p`` with a ``b`` inside and no
  ``table``, ``caption`` or heading around it, ``term`` the first ``b``'s text;
  ``lead`` -- the first ``p`` of the lead section with at least
  :data:`LEAD_MIN_WORDS` words of kept text and no ``table`` around it.  A
  passage starts at its element's kept span start and must be a word start of
  the head text
  (``locate_text.check_start`` at ``HeadMap.to_head``); one that is not is
  dropped and counted in ``finders_dropped``.
* **The reading copy** (``text.txt``) walks the same tree without the dropped
  regions -- ``style``, ``script``, ``template``, ``sup.reference``, ``ol.references``,
  ``.reflist``, ``.mw-references-wrap``, ``.mw-editsection`` -- writing each
  formula as ``$<normalized alttext>$``, each heading as a line of ``#`` (one per
  level) and its title, ``- `` before a list item, `` | `` between table cells
  (one space each side, whatever whitespace the cells hold), a line break for
  ``br``, a blank line between blocks (inside a table cell a block -- a display
  formula too -- is only a space: a row is one line), and whitespace collapsed
  within a block (``White_Space``, as in the layer).  ``layer_map.json`` (``cf-layer-map/2``) maps it back: in each
  segment ``[t0, t1, l0, l1, kind]``, ``text[t0:t1]`` is ``layer[l0:l1]``,
  character by character when ``kind`` is 0 (text) and only as a whole when it
  is 1 (a formula, whatever the two lengths are).  Characters the copy adds
  (heading marks, bullets, separators, breaks, an image-only formula) are in no
  segment.

Offsets everywhere are code-point offsets.  Nothing here fetches anything:
``scripts/sources/wikinet.py`` is the one module that does.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from bisect import bisect_right
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:  # pragma: no cover - imported through cf.py normally
    sys.path.insert(0, str(_SCRIPTS))

from gate import locate_text  # noqa: E402

RENDERER = "web-html/2"
#: the lead finder skips a lead paragraph shorter than this (a stub's empty line)
LEAD_MIN_WORDS = 5

#: the files a rendering writes into a source's directory, in the order written
RENDER_FILES = ("body.html", "layer.txt", "formulas.json", "head.txt", "text.txt",
                "layer_map.json", "sections.json", "paragraphs.json", "finders.json",
                "render.json")

#: the HTML tokenizer's whitespace (where a tag ends, a tag name stops)
_HTML_WS = "\t\n\f\r "
#: the 25 code points with the Unicode ``White_Space`` property: what the layer,
#: the reading copy, titles and terms collapse (the Literature Links rule; it is
#: the vendored passage normaliser's set, tests/test_locate_sources.py checks)
WHITE_SPACE = frozenset(chr(code) for code in (
    *range(0x09, 0x0E), 0x20, 0x85, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029,
    0x202F, 0x205F, 0x3000))
_WS = WHITE_SPACE
#: elements whose text is no part of the layer (the Literature Links rule)
_SKIPPED = frozenset({"script", "style", "template"})
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                   "meta", "param", "source", "track", "wbr", "keygen"})
_CLOSES_P = frozenset({"address", "article", "aside", "blockquote", "center", "details",
                       "dir", "div", "dl", "fieldset", "figcaption", "figure", "footer",
                       "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main",
                       "menu", "nav", "ol", "p", "pre", "section", "table", "ul"})
_P_SCOPE_BOUNDARY = frozenset({"table", "td", "th", "caption", "button", "object", "html",
                               "template", "marquee", "applet"})
_HEADINGS = {f"h{n}": n for n in range(1, 7)}
_COPY_BLOCKS = frozenset({"p", "div", "li", "dd", "dt", "dl", "ul", "ol", "table", "tr",
                          "tbody", "thead", "tfoot", "blockquote", "pre", "figure",
                          "figcaption", "caption", "section", "center", "hr", "h1", "h2",
                          "h3", "h4", "h5", "h6", "address", "article", "aside", "details",
                          "footer", "header", "main", "nav", "form", "fieldset"})
_BLOCK_TAGS = frozenset({"p", "li", "dd", "dt", "blockquote"})
_THEOREM_CLASSES = frozenset({"math_theorem", "math-theorem"})
#: elements whose content the tokenizer reads as raw text (or RCDATA): a ``<!--``
#: there is text, and so is anything that looks like a tag
_RAW_TEXT = frozenset({"script", "style", "xmp", "iframe", "noembed", "noframes",
                       "textarea", "title"})
#: the HTML5 tree builder drops one newline right after these start tags
_EAT_LF = frozenset({"pre", "listing", "textarea"})
#: deeper nesting than this is refused, deterministically and well inside Python's
#: recursion limit (the walk takes two frames a level)
MAX_DEPTH = 256
#: the layer map's schema, and its segment kinds
LAYER_MAP_SCHEMA = "cf-layer-map/2"
SEGMENT_TEXT = 0
SEGMENT_FORMULA = 1
_TAG_NAME = re.compile(r"[^\t\n\f\r />]*")


class RenderError(ValueError):
    """A body or a response the renderer cannot turn into a source."""


# --------------------------------------------------------------------------
# the canonical body
# --------------------------------------------------------------------------

def _tag_end(html: str, i: int) -> int:
    """Just past the ``>`` closing the tag that starts at ``i`` (or the end of the text).

    The tokenizer's attribute states, reduced to what decides where a tag ends:
    a ``"`` or ``'`` opens a quoted value only right after an ``=`` (and the
    whitespace after it); inside one, ``>`` is text.
    """
    n = len(html)
    j = i + 1
    expecting_value = False
    while j < n:
        ch = html[j]
        if ch == ">":
            return j + 1
        if ch == "=":
            expecting_value = True
            j += 1
            continue
        if ch in _HTML_WS:
            j += 1
            continue
        if expecting_value and ch in "\"'":
            close = html.find(ch, j + 1)
            if close < 0:
                return n
            j = close + 1
            expecting_value = False
            continue
        expecting_value = False
        j += 1
    return n


def strip_comments(html: str) -> str:
    """Every HTML comment removed, delimited as the tokenizer delimits it.

    Scans the data state: a ``<!--`` opens a comment closed by the first ``-->``
    or ``--!>`` (``<!-->`` and ``<!--->`` close at once, and an unterminated one
    runs to the end); a tag is skipped whole, attribute values included; the
    content of a raw-text element (``_RAW_TEXT``) runs to its end tag.  So a
    ``<!--`` in an attribute or a stylesheet is text and stays.
    """
    out: list[str] = []
    n = len(html)
    i = 0
    while i < n:
        j = html.find("<", i)
        if j < 0:
            out.append(html[i:])
            break
        out.append(html[i:j])
        if html.startswith("<!--", j):
            k = j + 4
            if html.startswith(">", k):
                i = k + 1
                continue
            if html.startswith("->", k):
                i = k + 2
                continue
            ends = [e + len(m) for m in ("-->", "--!>") for e in [html.find(m, k)] if e >= 0]
            i = min(ends) if ends else n
            continue
        nxt = html[j + 1:j + 2]
        if nxt.isascii() and nxt.isalpha():
            k = _tag_end(html, j)
            out.append(html[j:k])
            name = _TAG_NAME.match(html, j + 1).group(0).lower()
            if name in _RAW_TEXT and not html[j:k].endswith("/>"):
                close = re.compile(r"</" + re.escape(name) + r"(?=[\t\n\f\r />])", re.I)
                m = close.search(html, k)
                end = m.start() if m else n
                out.append(html[k:end])
                i = end
            else:
                i = k
            continue
        if nxt == "/" and html[j + 2:j + 3].isascii() and html[j + 2:j + 3].isalpha():
            k = _tag_end(html, j)
            out.append(html[j:k])
            i = k
            continue
        out.append("<")
        i = j + 1
    return "".join(out)


def decode(raw: bytes) -> str:
    """UTF-8, one leading byte-order mark dropped (the WHATWG UTF-8 decode); else refused."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RenderError(f"the bytes are not UTF-8 ({exc})") from None
    return text[1:] if text.startswith("\ufeff") else text


def response_json(raw: bytes):
    """The JSON value the bytes hold (byte-order mark tolerated), or None when they hold none."""
    try:
        return json.loads(decode(raw))
    except (RenderError, ValueError):
        return None


def parse_response_text(raw: bytes) -> tuple[str | None, dict]:
    """``(parse.text, parse)`` of an ``action=parse`` JSON response, or ``(None, {})``.

    ``formatversion=2`` puts the HTML at ``parse.text``; ``formatversion=1`` at
    ``parse.text["*"]``.  Anything else -- not JSON, not a parse response -- is
    ``(None, {})``, and the caller treats the bytes as a body.
    """
    data = response_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("parse"), dict):
        return None, {}
    parse = data["parse"]
    text = parse.get("text")
    if isinstance(text, dict):
        text = text.get("*")
    return (text, parse) if isinstance(text, str) else (None, {})


def canonical_body(raw: bytes) -> str:
    """The canonical body of fetched or adopted bytes (see the module docstring)."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    text, _parse = parse_response_text(raw)
    if text is None:
        try:
            text = decode(raw)
        except RenderError as exc:
            raise RenderError(f"the bytes are neither a parse response nor UTF-8 HTML "
                              f"({exc})") from None
    _encodable(text)
    return strip_comments(text)


def _encodable(text: str) -> str:
    """``text``, refused when it holds a lone surrogate (it would have no UTF-8 bytes)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RenderError(f"the body holds a lone surrogate ({exc.reason} at {exc.start})",
                          ) from None
    return text


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# the tree
# --------------------------------------------------------------------------

#: an element's namespace, as far as the tree needs it (where a self-closing flag counts)
_NS_HTML, _NS_MATHML, _NS_SVG = "html", "mathml", "svg"
#: MathML's text integration points: a start tag inside one is HTML, but these two
_MATHML_TEXT_POINTS = frozenset({"mi", "mo", "mn", "ms", "mtext"})
_MATHML_TEXT_POINT_FOREIGN = frozenset({"mglyph", "malignmark"})
#: SVG's HTML integration points (html.parser lower-cases ``foreignObject``)
_SVG_HTML_POINTS = frozenset({"foreignobject", "desc", "title"})
#: the HTML start tags that break out of foreign content (the HTML5 tree builder)
_BREAKOUT = frozenset({"b", "big", "blockquote", "body", "br", "center", "code", "dd", "div",
                       "dl", "dt", "em", "embed", "h1", "h2", "h3", "h4", "h5", "h6", "head",
                       "hr", "i", "img", "li", "listing", "menu", "meta", "nobr", "ol", "p",
                       "pre", "ruby", "s", "small", "span", "strong", "strike", "sub", "sup",
                       "table", "tt", "u", "ul", "var"})


def _namespace(tag: str, attrs: dict, parent: "_Node") -> str:
    """The namespace a start tag of ``tag`` gets under ``parent`` (see the module docstring)."""
    pns = parent.ns
    if pns == _NS_MATHML:
        if parent.tag in _MATHML_TEXT_POINTS and tag not in _MATHML_TEXT_POINT_FOREIGN:
            pns = _NS_HTML
        elif parent.tag == "annotation-xml":
            encoding = parent.attrs.get("encoding", "").lower()
            if tag == "svg":
                return _NS_SVG
            if encoding in ("text/html", "application/xhtml+xml"):
                pns = _NS_HTML
    elif pns == _NS_SVG and parent.tag in _SVG_HTML_POINTS:
        pns = _NS_HTML
    if pns == _NS_HTML:
        return {"math": _NS_MATHML, "svg": _NS_SVG}.get(tag, _NS_HTML)
    if tag in _BREAKOUT or (tag == "font" and set(attrs) & {"color", "face", "size"}):
        return _NS_HTML
    return pns


class _Node:
    __slots__ = ("tag", "attrs", "classes", "children", "parent", "start", "end", "ns")

    def __init__(self, tag: str, attrs: dict, parent: "_Node | None", start: int,
                 ns: str = _NS_HTML):
        self.tag = tag
        self.attrs = attrs
        self.classes = frozenset((attrs.get("class") or "").split())
        self.children: list = []          # _Node or (start, end) raw text ranges
        self.parent = parent
        self.start = start
        self.end = start
        self.ns = ns

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def iter(self):
        """This node and every descendant element, in document order."""
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(c for c in reversed(node.children) if isinstance(c, _Node))


class _TreeBuilder(HTMLParser):
    """``html.parser`` events into a :class:`_Node` tree plus the raw text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pieces: list[str] = []
        self.length = 0
        self.root = _Node("#root", {}, None, 0)
        self.stack = [self.root]
        self.eat_lf = False
        #: how many ``script``/``style``/``template`` elements are open: their text
        #: is no part of the layer, so it never enters the raw text
        self.skipping = 0

    # -- helpers -------------------------------------------------------
    def _close_to(self, index: int) -> None:
        """Pop the stack down to (and including) ``self.stack[index]``."""
        while len(self.stack) > index:
            node = self.stack.pop()
            node.end = self.length
            if node.tag in _SKIPPED:
                self.skipping -= 1

    def _find(self, names, boundary) -> int:
        """Index of the nearest open element named in ``names`` above ``boundary``."""
        for i in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[i].tag
            if tag in names:
                return i
            if tag in boundary:
                return -1
        return -1

    def _implied_closes(self, tag: str) -> None:
        if tag in _CLOSES_P:
            i = self._find({"p"}, _P_SCOPE_BOUNDARY)
            if i > 0:
                self._close_to(i)
        if tag == "li":
            i = self._find({"li"}, {"ul", "ol", "menu", "table", "td", "th"})
            if i > 0:
                self._close_to(i)
        elif tag in ("dt", "dd"):
            i = self._find({"dt", "dd"}, {"dl", "table", "td", "th"})
            if i > 0:
                self._close_to(i)
        elif tag == "tr":
            i = self._find({"tr"}, {"table", "tbody", "thead", "tfoot"})
            if i > 0:
                self._close_to(i)
        elif tag in ("td", "th"):
            i = self._find({"td", "th"}, {"tr", "table"})
            if i > 0:
                self._close_to(i)
        elif tag in ("tbody", "thead", "tfoot"):
            i = self._find({"tr", "td", "th", "tbody", "thead", "tfoot"}, {"table"})
            if i > 0:
                self._close_to(i)
        elif tag == "option":
            i = self._find({"option"}, {"select", "datalist"})
            if i > 0:
                self._close_to(i)

    # -- events --------------------------------------------------------
    def handle_starttag(self, tag, attrs, *, self_closing: bool = False):
        self.eat_lf = False
        self._implied_closes(tag)
        parent = self.stack[-1]
        attrs = {k: (v if v is not None else "") for k, v in attrs}
        node = _Node(tag, attrs, parent, self.length, _namespace(tag, attrs, parent))
        parent.children.append(node)
        if tag in _VOID or (self_closing and node.ns != _NS_HTML):
            node.end = self.length
        else:
            if len(self.stack) > MAX_DEPTH:
                raise RenderError(f"the body nests elements more than {MAX_DEPTH} deep "
                                  f"(at a {tag}); repair it before rendering")
            self.stack.append(node)
            self.eat_lf = tag in _EAT_LF
            if tag in _SKIPPED:
                self.skipping += 1

    def handle_startendtag(self, tag, attrs):
        # HTML5 honours the self-closing flag in MathML and SVG, ignores it on HTML
        self.handle_starttag(tag, attrs, self_closing=True)

    def handle_endtag(self, tag):
        self.eat_lf = False
        if tag in _VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self._close_to(i)
                return

    def handle_comment(self, data):
        self.eat_lf = False

    def handle_decl(self, decl):
        self.eat_lf = False

    def handle_pi(self, data):
        self.eat_lf = False

    def handle_data(self, data):
        if self.eat_lf:
            self.eat_lf = False
            for newline in ("\r\n", "\r", "\n"):
                if data.startswith(newline):
                    data = data[len(newline):]
                    break
        if not data or self.skipping:
            return
        start = self.length
        self.pieces.append(data)
        self.length += len(data)
        self.stack[-1].children.append((start, self.length))

    def finish(self) -> tuple[_Node, str]:
        self.close()
        self._close_to(1)
        self.root.end = self.length
        return self.root, "".join(self.pieces)


# --------------------------------------------------------------------------
# the layer
# --------------------------------------------------------------------------

class _Collapse:
    """The WHATWG strip-and-collapse of the raw text, with its offset map."""

    def __init__(self, raw: str):
        out: list[str] = []
        emit = [-1] * len(raw)
        pending = False
        n = 0
        for i, ch in enumerate(raw):
            if ch in _WS:
                pending = True
                continue
            if pending and n:
                out.append(" ")
                n += 1
            pending = False
            emit[i] = n
            out.append(ch)
            n += 1
        self.raw = raw
        self.layer = "".join(out)
        self.emit = emit
        size = len(raw)
        nxt = [size] * (size + 1)
        for i in range(size - 1, -1, -1):
            nxt[i] = i if emit[i] >= 0 else nxt[i + 1]
        prv = [-1] * (size + 1)
        last = -1
        for i in range(size + 1):
            prv[i] = last
            if i < size and emit[i] >= 0:
                last = i
        self.nxt = nxt
        self.prv = prv

    def span(self, start: int, end: int) -> tuple[int, int] | None:
        """The layer span of raw ``[start, end)``: first to last non-whitespace character."""
        j = self.nxt[start]
        if j >= end:
            return None
        k = self.prv[end]
        return self.emit[j], self.emit[k] + 1

    def position(self, raw_offset: int) -> int:
        """The layer offset of the first text at or after a raw offset."""
        j = self.nxt[raw_offset]
        return self.emit[j] if j < len(self.raw) else len(self.layer)


def _alttext(node: _Node, raw: str) -> str | None:
    """A formula's alttext: math@alttext, else the x-tex annotation, else the image alt."""
    for el in node.iter():
        if el.tag == "math" and el.attrs.get("alttext"):
            return el.attrs["alttext"]
    for el in node.iter():
        if el.tag == "annotation" and el.attrs.get("encoding", "").lower() == "application/x-tex":
            text = "".join(raw[a:b] for a, b in _texts(el))
            if text.strip():
                return text
    for el in node.iter():
        if el.tag == "img" and any("mwe-math-fallback-image" in c for c in el.classes) \
                and el.attrs.get("alt"):
            return el.attrs["alt"]
    return None


def _texts(node: _Node, *, kept: bool = False) -> Iterable[tuple[int, int]]:
    """Every raw text range under ``node`` in document order (``kept``: outside dropped regions)."""
    stack = [iter(node.children)]
    while stack:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
        elif isinstance(child, _Node):
            if not (kept and _dropped(child)):
                stack.append(iter(child.children))
        else:
            yield child


def _kept_span(node: _Node, collapse: "_Collapse") -> tuple[int, int] | None:
    """The layer span of the node's text outside the dropped regions, or None."""
    first = last = None
    for a, b in _texts(node, kept=True):
        j = collapse.nxt[a]
        if j < b:
            if first is None:
                first = j
            last = collapse.prv[b]
    if first is None:
        return None
    return collapse.emit[first], collapse.emit[last] + 1


def _kept_text(node: _Node, raw: str) -> str:
    return _collapse_ws("".join(raw[a:b] for a, b in _texts(node, kept=True)))


def _is_formula_root(node: _Node, inside: bool) -> bool:
    if inside:
        return False
    return "mwe-math-element" in node.classes or node.tag == "math"


def _is_display(node: _Node) -> bool:
    return ("mwe-math-element-block" in node.classes or node.tag == "div"
            or (node.tag == "math" and node.attrs.get("display", "").lower() == "block"))


def _dropped(node: _Node) -> bool:
    """Is this element a region the reading copy and the block list leave out?"""
    if node.tag in _SKIPPED:
        return True
    classes = node.classes
    if node.tag == "sup" and "reference" in classes:
        return True
    if node.tag == "ol" and "references" in classes:
        return True
    return bool(classes & {"reflist", "mw-references-wrap", "mw-editsection"})


def _is_theorem_box(node: _Node) -> bool:
    return bool(node.classes & _THEOREM_CLASSES)


def _collapse_ws(text: str) -> str:
    """Strip and collapse ``White_Space`` (the layer's rule, for titles and terms)."""
    out: list[str] = []
    pending = False
    for ch in text:
        if ch in _WS:
            pending = True
            continue
        if pending and out:
            out.append(" ")
        pending = False
        out.append(ch)
    return "".join(out)


# --------------------------------------------------------------------------
# the rendering
# --------------------------------------------------------------------------

@dataclass
class Rendering:
    """Every view of one canonical body (references/locate-documents.md section 7)."""

    body: str
    layer: str
    formulas: list[dict]
    head: str
    text: str
    segments: list[list[int]]
    sections: list[dict]
    paragraphs: list[dict]
    finders: list[dict]
    counts: dict = field(default_factory=dict)

    @property
    def html_sha256(self) -> str:
        return sha256_text(self.body)

    @property
    def layer_sha256(self) -> str:
        return sha256_text(self.layer)

    @property
    def head_sha256(self) -> str:
        return sha256_text(self.head)

    @property
    def text_sha256(self) -> str:
        return sha256_text(self.text)

    def record(self) -> dict:
        """``render.json``."""
        return {"renderer": RENDERER, "html_sha256": self.html_sha256,
                "layer_sha256": self.layer_sha256, "head_sha256": self.head_sha256,
                "text_sha256": self.text_sha256, "counts": dict(self.counts)}

    def layer_map(self) -> dict:
        return {"schema": LAYER_MAP_SCHEMA, "segments": [list(s) for s in self.segments]}

    def head_map(self) -> locate_text.HeadMap:
        return locate_text.HeadMap(self.layer, self.formulas)

    def text_to_layer(self, offset: int) -> int | None:
        return text_to_layer(self.segments, offset)

    def layer_to_text(self, offset: int) -> int | None:
        return layer_to_text(self.segments, offset)

    def files(self) -> dict[str, str]:
        """``{file name: content}`` for every file :func:`write_rendering` writes."""
        return {
            "body.html": self.body,
            "layer.txt": self.layer,
            "formulas.json": _dump(self.formulas),
            "head.txt": self.head,
            "text.txt": self.text,
            "layer_map.json": _dump(self.layer_map()),
            "sections.json": _dump(self.sections),
            "paragraphs.json": _dump(self.paragraphs),
            "finders.json": _dump(self.finders),
            "render.json": _dump(self.record()),
        }


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


class _Copy:
    """The reading copy and its map, built in document order."""

    def __init__(self, collapse: _Collapse):
        self.c = collapse
        self.parts: list[str] = []
        self.length = 0
        self.tmap: list[int | None] = []      # per text character: its layer offset
        self.atomic: list[list[int]] = []
        self.pending_space = False
        self.last_layer = -10                 # layer offset of the last mapped character
        self.line_has_text = False

    def _append(self, text: str, mapped=None) -> None:
        self.parts.append(text)
        self.length += len(text)
        if mapped is None:
            self.tmap.extend([None] * len(text))
        else:
            self.tmap.extend(mapped)

    def _tail(self, n: int) -> str:
        out = ""
        for part in reversed(self.parts):
            out = part + out
            if len(out) >= n:
                break
        return out[-n:]

    def _flush_space(self, next_layer: int | None) -> None:
        if self.pending_space and self.line_has_text and not self._tail(1).isspace():
            layer = self.c.layer
            prev = self.last_layer
            if (next_layer is not None and next_layer == prev + 2
                    and 0 <= prev + 1 < len(layer) and layer[prev + 1] == " "):
                self._append(" ", [prev + 1])
            else:
                self._append(" ")
        self.pending_space = False

    def text(self, start: int, end: int) -> None:
        raw, emit = self.c.raw, self.c.emit
        for i in range(start, end):
            ch = raw[i]
            if ch in _WS:
                self.pending_space = True
                continue
            at = emit[i]
            self._flush_space(at)
            self._append(ch, [at])
            self.last_layer = at
            self.line_has_text = True

    def literal(self, text: str) -> None:
        """Unmapped characters the copy adds (bullets, separators)."""
        self._flush_space(None)
        self._append(text)
        self.line_has_text = True

    def atom(self, text: str, span: tuple[int, int]) -> None:
        self._flush_space(span[0])
        t0 = self.length
        self._append(text)
        self.atomic.append([t0, self.length, span[0], span[1], SEGMENT_FORMULA])
        self.last_layer = span[1] - 1
        self.line_has_text = True

    def line_break(self) -> None:
        self.pending_space = False
        if self.line_has_text:
            self._append("\n")
            self.line_has_text = False

    def block_break(self) -> None:
        self.pending_space = False
        if self.length == 0:
            return
        tail = self._tail(2)
        if tail == "\n\n":
            return
        self._append("\n" if tail.endswith("\n") else "\n\n")
        self.line_has_text = False

    def finish(self) -> tuple[str, list[list[int]]]:
        text = "".join(self.parts)
        cut = len(text.rstrip("\n"))
        text = text[:cut] + ("\n" if cut else "")
        tmap = self.tmap[:cut] + ([None] if cut else [])
        segments: list[list[int]] = []
        run: list[int] | None = None
        for t, at in enumerate(tmap):
            if at is not None and run is not None and at == run[3] and t == run[1]:
                run[1] += 1
                run[3] += 1
                continue
            if run is not None:
                segments.append(run)
                run = None
            if at is not None:
                run = [t, t + 1, at, at + 1, SEGMENT_TEXT]
        if run is not None:
            segments.append(run)
        segments.extend(s for s in self.atomic if s[1] <= cut)
        segments.sort()
        return text, segments


class LayerMap:
    """``layer_map.json``'s segments with their search keys built once, for many lookups."""

    def __init__(self, segments: list[list[int]]):
        self.segments = [list(s) for s in segments]
        self._t = [s[0] for s in self.segments]
        self._l = [s[2] for s in self.segments]

    @classmethod
    def read(cls, directory: str | Path) -> "LayerMap":
        path = Path(directory) / "layer_map.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != LAYER_MAP_SCHEMA:
            raise RenderError(f"{path} is not a {LAYER_MAP_SCHEMA} layer map; re-render it")
        return cls(data["segments"])

    def text_to_layer(self, offset: int) -> int | None:
        i = bisect_right(self._t, offset) - 1
        return _lookup(self.segments, i, offset, 0)

    def layer_to_text(self, offset: int) -> int | None:
        i = bisect_right(self._l, offset) - 1
        return _lookup(self.segments, i, offset, 2)


def _lookup(segments, i: int, offset: int, side: int) -> int | None:
    """Map ``offset`` through segment ``i`` from ``side`` (0: text, 2: layer) to the other."""
    if i < 0:
        return None
    seg = segments[i]
    a, b = seg[side], seg[side + 1]
    other = 2 - side
    if not a <= offset < b:
        return None
    if seg[4] == SEGMENT_TEXT:
        return seg[other] + (offset - a)
    return seg[other] if offset == a else None       # a formula maps only as a whole


def text_to_layer(segments: list[list[int]], offset: int) -> int | None:
    """The layer offset of a reading-copy offset, or None where the copy added the character."""
    return _lookup(segments, bisect_right([s[0] for s in segments], offset) - 1, offset, 0)


def layer_to_text(segments: list[list[int]], offset: int) -> int | None:
    """The reading-copy offset of a layer offset, or None where the copy has no character."""
    return _lookup(segments, bisect_right([s[2] for s in segments], offset) - 1, offset, 2)


class _Walker:
    """One pass over the tree: formulas, sections, blocks, finders, the reading copy."""

    def __init__(self, root: _Node, collapse: _Collapse):
        self.root = root
        self.c = collapse
        self.copy = _Copy(collapse)
        self.formulas: list[dict] = []
        self.formula_nodes: dict[int, tuple[int, int]] = {}
        self.no_text_formulas = 0
        self.headings: list[tuple[_Node, int]] = []
        self.blocks: list[tuple[_Node, str, tuple[int, int], bool]] = []
        self.boxes: list[tuple[_Node, tuple[int, int]]] = []
        self.paras: list[tuple[_Node, tuple[int, int], bool, bool]] = []  # node, span, in table, in heading

    def run(self) -> None:
        self._visit(self.root, in_formula=False, dropped=False, in_table=False,
                    in_heading=False, in_cell=False)

    def _visit(self, node: _Node, *, in_formula: bool, dropped: bool, in_table: bool,
               in_heading: bool, in_cell: bool) -> None:
        for child in node.children:
            if isinstance(child, tuple):
                if not dropped:
                    self.copy.text(*child)
                continue
            self._element(child, in_formula=in_formula, dropped=dropped, in_table=in_table,
                          in_heading=in_heading, in_cell=in_cell)

    def _formula_break(self, display: bool, in_cell: bool) -> None:
        """Around a display formula: a block break, or inside a table cell a space."""
        if not display:
            return
        if in_cell:
            self.copy.pending_space = True
        else:
            self.copy.block_break()

    def _element(self, node: _Node, *, in_formula: bool, dropped: bool, in_table: bool,
                 in_heading: bool, in_cell: bool) -> None:
        c = self.c
        tag = node.tag
        if tag in _SKIPPED:
            return          # no text, no formula, no heading, no block: nothing of it is read
        span = c.span(node.start, node.end)
        drop_here = dropped or _dropped(node)
        # formulas: always in the layer; the copy gets the TeX when not dropped
        if _is_formula_root(node, in_formula):
            alt = _alttext(node, c.raw)
            if alt is not None:
                display = _is_display(node)
                tex = "$" + locate_text.normalize_alttext(alt) + "$"
                if span is None:
                    self.no_text_formulas += 1
                else:
                    self.formulas.append({"span": [span[0], span[1]], "alttext": alt})
                    self.formula_nodes[id(node)] = span
                if not drop_here:
                    if display and span is not None:
                        self.blocks.append((node, "math", span, False))
                    self._formula_break(display, in_cell)
                    if span is None:
                        self.copy.literal(tex)      # an image only: added characters
                    else:
                        self.copy.atom(tex, span)
                    self._formula_break(display, in_cell)
                return
        heading = _HEADINGS.get(tag)
        if heading is not None and not in_formula:
            self.headings.append((node, heading))
        kept = (_kept_span(node, c)
                if not drop_here and (tag in _BLOCK_TAGS or _is_theorem_box(node)) else None)
        if kept is not None:
            if tag in _BLOCK_TAGS:
                self.blocks.append((node, tag, kept, in_table))
            if _is_theorem_box(node):
                self.blocks.append((node, "theorem_box", kept, in_table))
                self.boxes.append((node, kept))
            if tag == "p":
                self.paras.append((node, kept, in_table, in_heading))
        copy = self.copy
        # inside a table cell a block is only a word break: a cell is one line
        breaks = (tag in _COPY_BLOCKS or _is_theorem_box(node)) and not in_cell
        if not drop_here:
            if tag == "br":
                copy.line_break()
            elif tag in ("td", "th"):
                if copy.line_has_text:
                    copy.pending_space = False     # the separator brings its own spaces
                    copy.literal(" | ")
            elif breaks:
                copy.block_break()
            elif tag in _COPY_BLOCKS and in_cell:
                copy.pending_space = True
            if heading is not None and not in_cell:
                copy.literal("#" * heading + " ")
            if tag == "li" and not in_cell:
                copy.literal("- ")
        self._visit(node, in_formula=in_formula or _is_formula_root(node, in_formula),
                    dropped=drop_here,
                    in_table=in_table or tag in ("table", "caption"),
                    in_heading=in_heading or heading is not None,
                    in_cell=in_cell or tag in ("td", "th", "caption"))
        if not drop_here and breaks:
            copy.block_break()
        elif not drop_here and tag in _COPY_BLOCKS and in_cell:
            copy.pending_space = True


def _heading_title(node: _Node, raw: str) -> str:
    return _kept_text(node, raw)


def _heading_fragment(node: _Node) -> str | None:
    if node.attrs.get("id"):
        return node.attrs["id"]
    for el in node.iter():
        if el is not node and "mw-headline" in el.classes and el.attrs.get("id"):
            return el.attrs["id"]
    return None


def _iter_kept(node: _Node):
    """Every descendant element outside a dropped region, in document order."""
    stack = [iter(node.children)]
    while stack:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
        elif isinstance(child, _Node) and not _dropped(child):
            yield child
            stack.append(iter(child.children))


def _first_text(node: _Node, raw: str, predicate) -> str | None:
    for el in _iter_kept(node):
        if predicate(el):
            text = _collapse_ws("".join(raw[a:b] for a, b in _texts(el, kept=True)))
            if text:
                return text
    return None


def render(body: str) -> Rendering:
    """Every view of one canonical body.  Deterministic: the same body, the same bytes."""
    if not isinstance(body, str):
        raise RenderError("the body is text: decode it (canonical_body) first")
    _encodable(body)
    builder = _TreeBuilder()
    try:
        builder.feed(body)
        root, raw = builder.finish()
    except AssertionError as exc:  # html.parser's own refusals
        raise RenderError(f"the body does not parse as HTML ({exc})") from None
    collapse = _Collapse(raw)
    layer = collapse.layer
    walker = _Walker(root, collapse)
    walker.run()
    formulas = sorted(walker.formulas, key=lambda f: f["span"])
    head_map = locate_text.HeadMap(layer, formulas)
    head = head_map.head

    # sections: the lead, then one per heading, tiling the layer
    sections: list[dict] = []
    starts: list[int] = []
    open_titles: list[tuple[int, str]] = []
    heading_entries = []
    for node, level in walker.headings:
        title = _heading_title(node, raw)
        while open_titles and open_titles[-1][0] >= level:
            open_titles.pop()
        open_titles.append((level, title))
        heading_entries.append({"path": [t for _l, t in open_titles],
                                "fragment": _heading_fragment(node), "level": level,
                                "start": collapse.position(node.start)})
    bounds = [h["start"] for h in heading_entries] + [len(layer)]
    sections.append({"path": [], "fragment": None, "level": 0,
                     "span": [0, bounds[0] if heading_entries else len(layer)]})
    for i, entry in enumerate(heading_entries):
        sections.append({"path": entry["path"], "fragment": entry["fragment"],
                         "level": entry["level"], "span": [entry["start"], bounds[i + 1]]})
    starts = [s["span"][0] for s in sections]

    def section_of(offset: int) -> list[str]:
        i = bisect_right(starts, offset) - 1
        # an empty section shares its start with the next one; take the last
        return list(sections[max(i, 0)]["path"])

    lead_end = sections[0]["span"][1]

    # deterministic finders
    finder_rows: list[dict] = []
    dropped = 0

    def propose(finder: str, span: tuple[int, int], term: str | None) -> None:
        nonlocal dropped
        start = span[0]
        try:
            locate_text.check_start(head, head_map.to_head(start))
        except locate_text.HeadError:
            dropped += 1
            return
        finder_rows.append({"finder": finder, "start": start, "span": [span[0], span[1]],
                            "section": section_of(start), "term": term})

    for node, span in walker.boxes:
        term = (_first_text(node, raw, lambda el: bool(el.classes & {"theorem-name",
                                                                      "math_theorem_name"}))
                or _first_text(node, raw, lambda el: el.tag in ("b", "strong")))
        propose("theorem_box", span, term)
    for node, span, in_table, in_heading in walker.paras:
        if in_table or in_heading:
            continue
        term = _first_text(node, raw, lambda el: el.tag == "b")
        if term:
            propose("bold_definition", span, term)
    for node, span, in_table, _in_heading in walker.paras:
        if in_table or span[0] >= lead_end:
            continue
        if len(_kept_text(node, raw).split(" ")) >= LEAD_MIN_WORDS:
            propose("lead", span, None)
            break
    seen = set()
    finders = []
    for row in sorted(finder_rows, key=lambda r: (r["start"], r["finder"])):
        key = (row["finder"], row["start"])
        if key not in seen:
            seen.add(key)
            finders.append(row)
    finder_starts = sorted(r["start"] for r in finders)

    # blocks
    formula_starts = [f["span"][0] for f in formulas]
    paragraphs = []
    for node, kind, span, _in_table in walker.blocks:
        a, b = span
        n = bisect_right(formula_starts, b - 1) - bisect_right(formula_starts, a - 1)
        hit = bisect_right(finder_starts, b - 1) - bisect_right(finder_starts, a - 1)
        paragraphs.append({"span": [a, b], "section": section_of(a), "block": kind,
                           "formulas": n, "math": bool(n or hit)})
    paragraphs.sort(key=lambda p: (p["span"][0], -p["span"][1], p["block"]))

    text, segments = walker.copy.finish()
    counts = {
        "layer_chars": len(layer), "text_chars": len(text), "formulas": len(formulas),
        "formulas_without_text": walker.no_text_formulas, "sections": len(sections),
        "paragraphs": len(paragraphs),
        "math_paragraphs": sum(1 for p in paragraphs if p["math"]),
        "finders": {name: sum(1 for r in finders if r["finder"] == name)
                    for name in ("theorem_box", "bold_definition", "lead")},
        "finders_dropped": dropped,
    }
    return Rendering(body=body, layer=layer, formulas=formulas, head=head, text=text,
                     segments=segments, sections=sections, paragraphs=paragraphs,
                     finders=finders, counts=counts)


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------

def write_rendering(directory: str | Path, rendering: Rendering) -> dict:
    """Write every file of :data:`RENDER_FILES` into ``directory``; returns ``render.json``.

    Each file is written atomically as the exact UTF-8 bytes of its content (no
    newline translation, so a body holding a CR keeps the sha it was recorded
    with); an unchanged file is left alone, so a re-render of the same body
    changes nothing on disk.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in rendering.files().items():
        path = directory / name
        data = content.encode("utf-8")
        try:
            if path.read_bytes() == data:
                continue
        except OSError:
            pass
        write_bytes(path, data)
    return rendering.record()


def write_bytes(path: str | Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file in the same directory, then replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_body(path: str | Path) -> str:
    """A ``body.html`` as its exact text: UTF-8, no newline translation."""
    try:
        return Path(path).read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise RenderError(f"{path} is missing: fetch or adopt the source first") from None
    except UnicodeDecodeError as exc:
        raise RenderError(f"{path} is not UTF-8 ({exc})") from None


def render_directory(directory: str | Path) -> Rendering:
    """Render the ``body.html`` of a source directory and write every view beside it."""
    directory = Path(directory)
    rendering = render(read_body(directory / "body.html"))
    write_rendering(directory, rendering)
    return rendering


def read_layer(directory: str | Path) -> tuple[str, list[dict]]:
    """``(layer, formulas)`` of a rendered source directory."""
    directory = Path(directory)
    layer = (directory / "layer.txt").read_text(encoding="utf-8")
    formulas = json.loads((directory / "formulas.json").read_text(encoding="utf-8"))
    return layer, formulas


def head_map(directory: str | Path) -> locate_text.HeadMap:
    """The :class:`gate.locate_text.HeadMap` of a rendered source directory."""
    layer, formulas = read_layer(directory)
    return locate_text.HeadMap(layer, formulas)
