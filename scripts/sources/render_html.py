"""Render one web source's canonical body into the views the locate stages read.

New code, implementing docs/proposals/wikipedia-locate.md section 2.1 ("Two text
views of one document") and section 2.2's head text, in the file shapes of
references/locate-documents.md section 7.  Standard library only: the body is
parsed with ``html.parser`` into a small tree, and every view is computed from
that one tree in one pass, so they cannot disagree about which characters a
passage covers.

**The renderer is frozen as** ``web-html/1`` (``cflib.vocab.RENDERER_WEB_HTML``):
a source's ``layer_sha256`` is pinned by every run, gate and score document, so
any change to the rule below that can move one character of any view is a new
renderer id, never an edit of this one.  The rule, in full:

* **The canonical body** (:func:`canonical_body`) is the ``parse.text`` of a
  ``formatversion=2`` ``action=parse`` JSON response (``parse.text["*"]`` of a
  ``formatversion=1`` one; bytes that are no such response are the body itself),
  decoded as UTF-8, with every HTML comment ``<!-- ... -->`` removed (an
  unterminated one runs to the end, as an HTML parser reads it).  Its sha256 is
  ``html_sha256``, the source's ``source_sha``.
* **The tree.**  Tags are lower-cased by the parser; character references are
  decoded except inside ``script`` and ``style``, whose content is raw text.
  The void elements (``area base br col embed hr img input link meta param
  source track wbr``) never hold content; a self-closing flag on any other tag
  is ignored, as HTML5 ignores it.  An end tag closes the nearest open element
  of its name and everything opened inside it; one with no open element is
  ignored.  A ``p`` is closed by the start of a block (``address article aside
  blockquote center details dir div dl fieldset figcaption figure footer form
  h1-h6 header hr main menu nav ol p pre section table ul``) when no ``table``,
  ``td``, ``th``, ``caption``, ``button`` or ``object`` lies between; an ``li``
  by the next ``li`` of its list, a ``dt``/``dd`` by the next ``dt``/``dd`` of
  its ``dl``, a ``tr`` by the next ``tr`` of its table section, a ``td``/``th``
  by the next cell or row, an ``option`` by the next ``option``.
* **The Literature Links text layer** (``layer.txt``) is the tree's DOM
  ``textContent`` -- every text node in document order, ``style`` and ``script``
  text, MathML token text, the TeX annotation and citation markers all included
  -- after the WHATWG *strip and collapse ASCII whitespace*: each maximal run of
  U+0009, U+000A, U+000C, U+000D and U+0020 becomes one U+0020, and the ends are
  stripped.  No Unicode normalisation, so U+00A0 stays U+00A0.
* **An element's layer span** is half-open, from its first non-whitespace text
  character to just after its last; an element with no such character has no
  span.
* **Formulas** (``formulas.json``): every element whose class tokens include
  ``mwe-math-element``, and every ``math`` element outside one, is a formula; its
  span is its layer span and its alttext is the first descendant ``math``
  element's ``alttext``, else the text of an ``annotation`` whose ``encoding`` is
  ``application/x-tex``, else the ``alt`` of an ``img`` whose class names
  ``mwe-math-fallback-image``.  A formula with no alttext is ordinary text; one
  with no span (an image only) contributes nothing to the layer and is counted in
  ``render.json`` as ``formulas_without_text``.  Formulas never nest.
* **The head text** (``head.txt``) is ``gate.locate_text.HeadMap`` over the layer
  and the formulas: each span replaced by ``⟦normalized alttext⟧``.
* **Sections** (``sections.json``): every ``h1``-``h6`` starts one, at the layer
  position of its first text; its title is its text without ``.mw-editsection``,
  whitespace-collapsed; its fragment is its own ``id``, else the ``id`` of a
  ``.mw-headline`` inside it, else null; its path is the titles of the open
  headings of lower level, then its own.  The lead (path ``[]``, fragment null)
  comes first, and the spans tile the layer.
* **Blocks** (``paragraphs.json``): every ``p``, ``li``, ``dd``, ``dt`` and
  ``blockquote``, every theorem box (class token ``math_theorem`` or
  ``math-theorem``) and every display formula (a formula whose class names
  ``mwe-math-element-block``, a ``div`` formula, or a ``math`` with
  ``display="block"``) that has a span and lies outside the dropped regions
  below.  Blocks may nest (an ``li`` holding a ``p`` is two entries).
  ``formulas`` counts the formulas starting inside; ``math`` is true when there
  is one, or when a deterministic finder's passage starts inside.
* **The deterministic finders** (``finders.json``, ``cflib.vocab.
  DETERMINISTIC_FINDERS``): ``theorem_box`` -- each theorem box, ``term`` its
  ``.theorem-name`` (or ``.math_theorem_name``) text, else its first ``b`` or
  ``strong``; ``bold_definition`` -- each ``p`` with a ``b`` inside and no
  ``table``, ``caption`` or heading around it, ``term`` the first ``b``'s text;
  ``lead`` -- the first ``p`` of the lead section with at least
  :data:`LEAD_MIN_WORDS` words and no ``table`` around it.  A passage starts at
  its element's layer span start and must be a word start of the head text
  (``locate_text.check_start`` at ``HeadMap.to_head``); one that is not is
  dropped and counted in ``finders_dropped``.
* **The reading copy** (``text.txt``) walks the same tree without the dropped
  regions -- ``style``, ``script``, ``sup.reference``, ``ol.references``,
  ``.reflist``, ``.mw-references-wrap``, ``.mw-editsection`` -- writing each
  formula as ``$<normalized alttext>$``, each heading as a line of ``#`` (one per
  level) and its title, ``- `` before a list item, `` | `` between table cells,
  a line break for ``br``, a blank line between blocks (inside a table cell a
  block is only a space: a row is one line), and whitespace collapsed within a
  block.  ``layer_map.json`` maps it back: in each segment
  ``[t0, t1, l0, l1]``, ``text[t0:t1]`` is ``layer[l0:l1]``, character by
  character when the lengths are equal and only as a whole for a formula.
  Characters the copy adds (heading marks, bullets, separators, breaks) are in
  no segment.

Offsets everywhere are code-point offsets.  Nothing here fetches anything:
``scripts/sources/wikinet.py`` is the one module that does.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from bisect import bisect_right
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:  # pragma: no cover - imported through cf.py normally
    sys.path.insert(0, str(_SCRIPTS))

from gate import locate_text  # noqa: E402

RENDERER = "web-html/1"
#: the lead finder skips a lead paragraph shorter than this (a stub's empty line)
LEAD_MIN_WORDS = 5

#: the files a rendering writes into a source's directory, in the order written
RENDER_FILES = ("body.html", "layer.txt", "formulas.json", "head.txt", "text.txt",
                "layer_map.json", "sections.json", "paragraphs.json", "finders.json",
                "render.json")

_WS = "\t\n\f\r "
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
_COMMENT = re.compile(r"<!--.*?-->", re.S)


class RenderError(ValueError):
    """A body or a response the renderer cannot turn into a source."""


# --------------------------------------------------------------------------
# the canonical body
# --------------------------------------------------------------------------

def strip_comments(html: str) -> str:
    """Every ``<!-- ... -->`` removed; an unterminated comment runs to the end."""
    text = _COMMENT.sub("", html)
    at = text.find("<!--")
    return text[:at] if at >= 0 else text


def parse_response_text(raw: bytes) -> tuple[str | None, dict]:
    """``(parse.text, parse)`` of an ``action=parse`` JSON response, or ``(None, {})``.

    ``formatversion=2`` puts the HTML at ``parse.text``; ``formatversion=1`` at
    ``parse.text["*"]``.  Anything else -- not JSON, not a parse response -- is
    ``(None, {})``, and the caller treats the bytes as a body.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, {}
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
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RenderError(f"the bytes are neither a parse response nor UTF-8 HTML "
                              f"({exc})") from None
    return strip_comments(text)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# the tree
# --------------------------------------------------------------------------

class _Node:
    __slots__ = ("tag", "attrs", "classes", "children", "parent", "start", "end")

    def __init__(self, tag: str, attrs: dict, parent: "_Node | None", start: int):
        self.tag = tag
        self.attrs = attrs
        self.classes = frozenset((attrs.get("class") or "").split())
        self.children: list = []          # _Node or (start, end) raw text ranges
        self.parent = parent
        self.start = start
        self.end = start

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

    # -- helpers -------------------------------------------------------
    def _close_to(self, index: int) -> None:
        """Pop the stack down to (and including) ``self.stack[index]``."""
        while len(self.stack) > index:
            node = self.stack.pop()
            node.end = self.length

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
    def handle_starttag(self, tag, attrs):
        self._implied_closes(tag)
        parent = self.stack[-1]
        node = _Node(tag, {k: (v if v is not None else "") for k, v in attrs},
                     parent, self.length)
        parent.children.append(node)
        if tag in _VOID:
            node.end = self.length
        else:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        # HTML5 ignores the self-closing flag on a non-void element
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self._close_to(i)
                return

    def handle_data(self, data):
        if not data:
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


def _texts(node: _Node) -> Iterable[tuple[int, int]]:
    """Every raw text range under ``node``, in document order."""
    for child in node.children:
        if isinstance(child, _Node):
            yield from _texts(child)
        else:
            yield child


def _is_formula_root(node: _Node, inside: bool) -> bool:
    if inside:
        return False
    return "mwe-math-element" in node.classes or node.tag == "math"


def _is_display(node: _Node) -> bool:
    return ("mwe-math-element-block" in node.classes or node.tag == "div"
            or (node.tag == "math" and node.attrs.get("display", "").lower() == "block"))


def _dropped(node: _Node) -> bool:
    """Is this element a region the reading copy and the block list leave out?"""
    if node.tag in ("style", "script"):
        return True
    classes = node.classes
    if node.tag == "sup" and "reference" in classes:
        return True
    if node.tag == "ol" and "references" in classes:
        return True
    return bool(classes & {"reflist", "mw-references-wrap", "mw-editsection"})


def _is_theorem_box(node: _Node) -> bool:
    return bool(node.classes & _THEOREM_CLASSES)


def _ascii_collapse(text: str) -> str:
    """Strip and collapse ASCII whitespace only (the layer's rule, for titles and terms)."""
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
        return {"schema": "cf-layer-map/1", "segments": [list(s) for s in self.segments]}

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
        self.atomic.append([t0, self.length, span[0], span[1]])
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
                run = [t, t + 1, at, at + 1]
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
        data = json.loads((Path(directory) / "layer_map.json").read_text(encoding="utf-8"))
        return cls(data["segments"])

    def text_to_layer(self, offset: int) -> int | None:
        i = bisect_right(self._t, offset) - 1
        return _lookup(self.segments, i, offset, 0)

    def layer_to_text(self, offset: int) -> int | None:
        i = bisect_right(self._l, offset) - 1
        return _lookup(self.segments, i, offset, 2)


def _lookup(segments, i: int, offset: int, side: int) -> int | None:
    if i < 0:
        return None
    seg = segments[i]
    a, b = seg[side], seg[side + 1]
    other = 2 - side
    if not a <= offset < b:
        return None
    if seg[1] - seg[0] == seg[3] - seg[2]:
        return seg[other] + (offset - a)
    return seg[other] if offset == a else None


def text_to_layer(segments: list[list[int]], offset: int) -> int | None:
    """The layer offset of a reading-copy offset, or None where the copy added the character."""
    i = bisect_right([s[0] for s in segments], offset) - 1
    if i < 0:
        return None
    t0, t1, l0, l1 = segments[i]
    if not t0 <= offset < t1:
        return None
    if t1 - t0 == l1 - l0:
        return l0 + (offset - t0)
    return l0 if offset == t0 else None


def layer_to_text(segments: list[list[int]], offset: int) -> int | None:
    """The reading-copy offset of a layer offset, or None where the copy has no character."""
    i = bisect_right([s[2] for s in segments], offset) - 1
    if i < 0:
        return None
    t0, t1, l0, l1 = segments[i]
    if not l0 <= offset < l1:
        return None
    if t1 - t0 == l1 - l0:
        return t0 + (offset - l0)
    return t0 if offset == l0 else None


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

    def _element(self, node: _Node, *, in_formula: bool, dropped: bool, in_table: bool,
                 in_heading: bool, in_cell: bool) -> None:
        c = self.c
        tag = node.tag
        span = c.span(node.start, node.end)
        drop_here = dropped or _dropped(node)
        # formulas: always in the layer; the copy gets the TeX when not dropped
        if _is_formula_root(node, in_formula):
            alt = _alttext(node, c.raw)
            if alt is not None:
                if span is None:
                    self.no_text_formulas += 1
                else:
                    self.formulas.append({"span": [span[0], span[1]], "alttext": alt})
                    self.formula_nodes[id(node)] = span
                    display = _is_display(node)
                    if not drop_here:
                        if display:
                            self.blocks.append((node, "math", span, False))
                            self.copy.block_break()
                        self.copy.atom("$" + locate_text.normalize_alttext(alt) + "$", span)
                        if display:
                            self.copy.block_break()
                    return
        heading = _HEADINGS.get(tag)
        if heading is not None and not in_formula:
            self.headings.append((node, heading))
        if not drop_here and span is not None:
            if tag in _BLOCK_TAGS:
                self.blocks.append((node, tag, span, in_table))
            if _is_theorem_box(node):
                self.blocks.append((node, "theorem_box", span, in_table))
                self.boxes.append((node, span))
            if tag == "p":
                self.paras.append((node, span, in_table, in_heading))
        copy = self.copy
        # inside a table cell a block is only a word break: a cell is one line
        breaks = (tag in _COPY_BLOCKS or _is_theorem_box(node)) and not in_cell
        if not drop_here:
            if tag == "br":
                copy.line_break()
            elif tag in ("td", "th"):
                if copy.line_has_text:
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
    parts: list[str] = []

    def walk(n: _Node) -> None:
        for child in n.children:
            if isinstance(child, tuple):
                parts.append(raw[child[0]:child[1]])
            elif not _dropped(child):
                walk(child)

    walk(node)
    return _ascii_collapse("".join(parts))


def _heading_fragment(node: _Node) -> str | None:
    if node.attrs.get("id"):
        return node.attrs["id"]
    for el in node.iter():
        if el is not node and "mw-headline" in el.classes and el.attrs.get("id"):
            return el.attrs["id"]
    return None


def _iter_kept(node: _Node):
    """Every descendant element outside a dropped region, in document order."""
    for child in node.children:
        if isinstance(child, _Node) and not _dropped(child):
            yield child
            yield from _iter_kept(child)


def _first_text(node: _Node, raw: str, predicate) -> str | None:
    for el in _iter_kept(node):
        if predicate(el):
            text = _ascii_collapse("".join(raw[a:b] for a, b in _texts(el)))
            if text:
                return text
    return None


def render(body: str) -> Rendering:
    """Every view of one canonical body.  Deterministic: the same body, the same bytes."""
    if not isinstance(body, str):
        raise RenderError("the body is text: decode it (canonical_body) first")
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
        if len(layer[span[0]:span[1]].split(" ")) >= LEAD_MIN_WORDS:
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

    Each file is written atomically; an unchanged file is left alone, so a
    re-render of the same body changes nothing on disk.
    """
    from cflib.jsonio import atomic_write

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in rendering.files().items():
        path = directory / name
        try:
            if path.read_text(encoding="utf-8") == content:
                continue
        except (OSError, UnicodeDecodeError):
            pass
        atomic_write(path, content)
    return rendering.record()


def render_directory(directory: str | Path) -> Rendering:
    """Render the ``body.html`` of a source directory and write every view beside it."""
    directory = Path(directory)
    body_path = directory / "body.html"
    try:
        body = body_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RenderError(f"{body_path} is missing: fetch or adopt the source first") from None
    rendering = render(body)
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
