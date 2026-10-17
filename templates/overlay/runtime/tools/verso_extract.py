"""Cut each card's statement out of the built Verso site, with its hovers.

New code for `docs/overlay.md` sections 4 and 5: `build_site.py` calls
`extract` on the built site and publishes what it returns as
`<mount>/verso.json`, which the viewer reads to tint a key card's statement
the way the rendered page does and to show a hover for each identifier in it.

A card qualifies when its end's rendered page is a page of the site with an
anchor (the same page and fragment the viewer's `cardHref` builds), or when
it is a tagged example on its module's page: an example has no anchor, so
its box is the one whose code holds its tag comment (`` `example:<slug>` ``),
and its `example` keyword after the tag stands for the anchor. That page's
`div.code-box` holding the anchor is parsed with `html.parser`, from the
box's start only, and its code text is flattened in reading order:
`.tactic-state` and `.hover-info` subtrees and inputs are skipped, and a
line break separates two `code.block` elements. The card's snippet is matched
against that text from the anchor's line or up to `LINES_ABOVE` lines above
it: whitespace runs match whitespace runs and comments are skipped on both
sides (the literate page moves docstrings and top-level comments into prose),
a trailing "…" is kept as the cut mark, and an internal one, a mismatch or a
match that does not cover the anchor means no statement. Nothing is guessed.

The statement is the snippet's own text, so the plain rendering and this one
show the same characters: `[kind, text, hover, binding, href]` runs whose
texts joined are the snippet exactly. A run takes its kind, hover id and link
from the Verso token its characters matched; whitespace and comments the
snippet has and the page does not stay plain. A binding is a small number,
shared by the runs of one statement that Verso binds together (a variable and
its uses, a bracket and its partner), kept only when two runs share it. A
link is kept only when its target page is a file of the site and holds the
anchor it names, and that anchor is a declaration's own: the name after a
declaration keyword, or a field or constructor of a declaration the page
declares (a Verso page also gives an id to some other uses of a name). Only
the hover entries the statements use are copied from
the site's hover table (`DOCS_NAME`), each split into its signature and its
docstring, both plain text; the viewer renders them with `textContent`.
"""
from __future__ import annotations

import codecs
import html
from html.parser import HTMLParser
import json
import mmap
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import sys
from typing import Any
import urllib.parse

sys.dont_write_bytecode = True
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import overlay_annotations  # noqa: E402  (beside this file in pdf-overlay/tools/)

SCHEMA = "formal-companion/overlay-verso"
SCHEMA_VERSION = 1
#: Where `build_site.py` publishes the result, relative to the mount.
FILE_NAME = "verso.json"
#: Verso's table of hover contents, at the site root: `{id: "<code>signature</code>..."}`.
DOCS_NAME = "-verso-docs.json"
#: How many lines above the declaration's name its statement may start (attributes, modifiers).
LINES_ABOVE = 6
ELLIPSIS = "…"
#: The keyword that stands for a tagged example's anchor.
EXAMPLE_KEYWORD = "example"
#: Verso's token classes, by the kind a run carries; any other token is plain text.
KINDS = (("keyword", "kw"), ("const", "const"), ("level-var", "var"), ("var", "var"),
         ("sort", "sort"), ("literal", "lit"), ("comment", "cm"))
#: Subtrees of a code box that are not its code.
SKIP_CLASSES = frozenset({"tactic-state", "hover-info"})
VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                       "source", "track", "wbr"})
CHUNK = 1 << 16
#: How Verso marks a code box, opens a goal view, and any span tag.
BOX_CLASS = b'class="code-box'
TACTIC_STATE = b'<span class="tactic-state">'
#: How a comment's opening delimiter token and a code element of a code box start.
COMMENT_TOKEN = b'<span class="comment delimiter'
CODE_BLOCK = b'<code class="hl lean block"'
SPAN_TAG = re.compile(rb"<(/?)span\b[^>]*>")
WHITESPACE = " \t\r\n"
HOVER_ID = re.compile(r"[0-9]{1,9}\Z")
BASE_TAG = re.compile(rb"<base\s[^>]*href=\"([^\"]*)\"", re.IGNORECASE)
#: The characters `encodeURIComponent` leaves alone.
URI_COMPONENT_SAFE = "-_.!~*'()"
#: An example's declared id: its tag, which its tag comment holds in backticks.
EXAMPLE_ID = re.compile(r"example:[A-Za-z0-9][A-Za-z0-9._-]*\Z")
#: The keywords a declaration's own anchor follows: the anchored name is the one they declare.
DECLARING = frozenset({b"theorem", b"lemma", b"def", b"abbrev", b"structure", b"class", b"inductive",
                       b"instance", b"opaque", b"axiom"})
KEYWORD_TOKEN = re.compile(rb'<span class="keyword token"[^>]*>([^<]*)</span>')
ANY_TAG = re.compile(rb"<[^>]*>")


# ---------------------------------------------------------------------------
# which cards qualify
# ---------------------------------------------------------------------------

def _trimmed(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def card_locator(end: dict[str, Any]) -> tuple[str, str, str] | None:
    """`(page, how, what)` of an object end's statement, or None: `how` is "id" and `what` the
    anchor of an exact destination, or `how` is "tag" and `what` the tag of an example.

    The viewer's `cardTarget` in `viewer-core.js`, written again: the page is
    the destination's own `href`, else the module's page; the fragment is the
    destination's own, else the declaration's name with each "." as "___",
    and only an `exact` destination has one. An example whose declared id is
    its tag (`example:<slug>`) has no anchor, and is found by that tag.
    """
    ext = overlay_annotations.extension(end)
    destination = ext.get("destination")
    if not isinstance(destination, dict):
        return None
    location = end.get("location") if isinstance(end.get("location"), dict) else {}
    if isinstance(destination.get("href"), str):
        page = destination["href"]
    else:
        module = _trimmed(location.get("module"))
        page = "/".join(module.split(".")) + "/index.html" if module else ""
    if not page:
        return None
    if destination.get("resolution") == "exact":
        if isinstance(destination.get("fragment"), str):
            fragment = destination["fragment"]
        else:
            fragment = "___".join(_trimmed(location.get("declaration")).split("."))
        return (page, "id", fragment) if fragment else None
    declaration = ext.get("declaration") if isinstance(ext.get("declaration"), dict) else {}
    tag = declaration.get("id")
    if declaration.get("kind") == "example" and isinstance(tag, str) and EXAMPLE_ID.match(tag):
        return page, "tag", tag
    return None


def card_target(end: dict[str, Any]) -> str | None:
    """`<page>#<fragment>`, or `<page>#<example tag>`: the key `verso.json` files the end's statement under."""
    found = card_locator(end)
    return f"{found[0]}#{found[2]}" if found else None


def snippet_text(end: dict[str, Any]) -> str | None:
    snippet = overlay_annotations.extension(end).get("snippet")
    value = snippet.get("text") if isinstance(snippet, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def card_statements(entries: list[tuple[str, dict[str, Any]]]) -> dict[str, tuple[tuple[str, str, str], list[str]]]:
    """{target: (locator, [snippet text, ...])} in bundle order, each text once per target."""
    found: dict[str, tuple[tuple[str, str, str], list[str]]] = {}
    for _path, annotation in entries:
        for end in annotation.get("object") or []:
            if not isinstance(end, dict):
                continue
            locator, text = card_locator(end), snippet_text(end)
            if locator and text:
                texts = found.setdefault(f"{locator[0]}#{locator[2]}", (locator, []))[1]
                if text not in texts:
                    texts.append(text)
    return found


def site_file(page: str) -> str | None:
    """The site-relative file a page reference names, or None when it leaves the site."""
    parts = urllib.parse.urlsplit(page)
    if parts.scheme or parts.netloc or parts.query or parts.fragment or "\\" in page:
        return None
    path = urllib.parse.unquote(parts.path)
    if not path or path.startswith("/"):
        return None
    normal = posixpath.normpath(path)
    if normal == ".." or normal.startswith("../") or normal == ".":
        return None
    return f"{normal}/index.html" if path.endswith("/") else normal


# ---------------------------------------------------------------------------
# the site's pages
# ---------------------------------------------------------------------------

class Site:
    """The built site's files, read lazily and only inside the site root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.realpath(root))
        self.pages: dict[str, mmap.mmap | bytes | None] = {}
        self.anchors: dict[tuple[str, str], bool] = {}
        self.declared: dict[tuple[str, str], bool] = {}

    @staticmethod
    def parts(relative: str) -> tuple[str, ...] | None:
        """The components of a path relative to the root, or None when it is absolute or has a `..`."""
        parts = PurePosixPath(relative).parts
        if not parts or relative.startswith("/") or "\\" in relative or ".." in parts:
            return None
        return parts

    def resolve(self, relative: str) -> Path | None:
        """A regular file under the root, every component a plain directory."""
        parts = self.parts(relative)
        if parts is None:
            return None
        current = self.root
        mode = 0
        for part in parts:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except OSError:
                return None
            if stat.S_ISLNK(mode):
                return None
        return current if stat.S_ISREG(mode) else None

    def is_directory(self, relative: str) -> bool:
        parts = self.parts(relative)
        if parts is None:
            return False
        path = self.root.joinpath(*parts)
        try:
            return stat.S_ISDIR(path.lstat().st_mode)
        except OSError:
            return False

    def page(self, relative: str) -> mmap.mmap | bytes | None:
        if relative not in self.pages:
            self.pages[relative] = None
            path = self.resolve(relative)
            if path is not None:
                try:
                    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    try:
                        size = os.fstat(fd).st_size
                        self.pages[relative] = mmap.mmap(fd, 0, access=mmap.ACCESS_READ) if size else b""
                    finally:
                        os.close(fd)
                except (OSError, ValueError):
                    self.pages[relative] = None
        return self.pages[relative]

    def close(self) -> None:
        for data in self.pages.values():
            if isinstance(data, mmap.mmap):
                data.close()
        self.pages.clear()

    def has_anchor(self, relative: str, fragment: str) -> bool:
        """Whether the page exists and an element on it has this id."""
        key = (relative, fragment)
        if key not in self.anchors:
            data = self.page(relative)
            self.anchors[key] = data is not None and (not fragment or id_position(data, fragment) >= 0)
        return self.anchors[key]

    def declares(self, relative: str, fragment: str) -> bool:
        """Whether the page's element with this id is a declaration's own name (`declared_at`), or the
        name of a field or constructor (`A___B___c`) of a declaration the page declares (`A___B`)."""
        key = (relative, fragment)
        if key not in self.declared:
            self.declared[key] = False
            data = self.page(relative)
            at = id_position(data, fragment) if data is not None and fragment else -1
            parent = fragment.rpartition("___")[0]
            self.declared[key] = at >= 0 and (declared_at(data, at) or bool(parent) and self.declares(relative, parent))
        return self.declared[key]

    def base(self, relative: str) -> str:
        """The page's directory, as its own `<base href>` moves it, relative to the root."""
        data = self.page(relative)
        directory = posixpath.dirname(relative)
        head = data[:65536] if data is not None else b""
        found = BASE_TAG.search(head)
        if not found:
            return directory
        base = html.unescape(found.group(1).decode("utf-8", "replace"))
        if urllib.parse.urlsplit(base).scheme or base.startswith("/"):
            return ""
        return posixpath.normpath(posixpath.join(directory, base))


def id_spellings(fragment: str) -> list[bytes]:
    """`id="<fragment>"` as an HTML writer may have escaped it."""
    core = fragment.replace("&", "&amp;").replace('"', "&quot;")
    spellings = {core, core.replace("<", "&lt;").replace(">", "&gt;")}
    for spelling in list(spellings):
        for apostrophe in ("&#39;", "&#x27;", "&apos;"):
            spellings.add(spelling.replace("'", apostrophe))
    return [f' id="{spelling}"'.encode("utf-8") for spelling in sorted(spellings)]


def id_position(data: mmap.mmap | bytes, fragment: str) -> int:
    """Where `id="<fragment>"` first appears, or -1."""
    found = [at for at in (data.find(needle) for needle in id_spellings(fragment)) if at >= 0]
    return min(found) if found else -1


def declared_at(data: mmap.mmap | bytes, at: int) -> bool:
    """Whether the element whose attribute starts at `at` comes right after a declaration keyword token,
    with nothing but tags and whitespace between them."""
    start = data.rfind(b"<", 0, at)
    window = bytes(data[max(0, start - 4096):start]) if start >= 0 else b""
    keyword = None
    for keyword in KEYWORD_TOKEN.finditer(window):
        pass
    if keyword is None or keyword.group(1).strip() not in DECLARING:
        return False
    between = ANY_TAG.sub(b"", window[keyword.end():])
    return not html.unescape(between.decode("utf-8", "replace")).strip()


# ---------------------------------------------------------------------------
# one code box
# ---------------------------------------------------------------------------

class Box(HTMLParser):
    """The code text of one `div.code-box`, its tokens, and where the anchor's text starts."""

    def __init__(self, anchor: str, *, past_anchor: int, tag: bool = False, loose: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.anchor = anchor
        self.past_anchor = past_anchor
        #: In tag mode `anchor` is a text the code holds (an example's tag comment), not an id, and the
        #: anchor is the first `example` keyword after it; `tail` is the text read last, `tag_at` the tag's.
        self.tag = tag
        self.tail = ""
        self.tag_at: int | None = None
        #: The token the last characters belong to, where its text starts, and its text so far.
        self.run: tuple[int, int, str] = (-1, 0, "")
        #: Read from inside the box: an end tag of no element open here closes nothing.
        self.loose = loose
        self.parts: list[str] = []
        self.length = 0
        #: One entry per character run: (start, end, token index or -1).
        self.segments: list[tuple[int, int, int]] = []
        #: (kind, hover, binding, href) per token element.
        self.tokens: list[tuple[str, str | None, str | None, str | None]] = []
        #: (tag, skip, in a code block, token index, href) per open element.
        self.stack: list[tuple[str, bool, bool, int, str | None]] = []
        self.anchor_at: int | None = None
        self.blocks = 0
        self.done = False
        self.failed = False

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def _append(self, value: str, token: int) -> None:
        if value and self.tag and self.anchor_at is None:
            last, start, text = self.run
            self.run = (token, start, text + value) if token >= 0 and token == last else (token, self.length, value)
            if self.tag_at is None:
                window = self.tail + value
                found = window.find(self.anchor)
                if found >= 0:
                    self.tag_at = self.length - len(self.tail) + found
                self.tail = window[-len(self.anchor):]
            elif token >= 0 and self.tokens[token][0] == "kw" and self.run[2] == EXAMPLE_KEYWORD:
                self.anchor_at = self.run[1]
        if value:
            self.parts.append(value)
            self.segments.append((self.length, self.length + len(value), token))
            self.length += len(value)
        if self.anchor_at is not None and self.length - self.anchor_at > self.past_anchor:
            self.done = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        values = {name: value or "" for name, value in attrs}
        classes = values.get("class", "").split()
        if not self.stack:
            if tag != "div" or "code-box" not in classes:
                self.failed = self.done = True
                return
            parent: tuple[str, bool, bool, int, str | None] = ("div", False, False, -1, None)
        else:
            parent = self.stack[-1]
        _tag, skip, block, token, href = parent
        skip = skip or tag == "input" or bool(SKIP_CLASSES.intersection(classes))
        if not skip and tag == "code" and "block" in classes:
            if self.blocks and not self.text.endswith("\n"):
                self._append("\n", -1)
            self.blocks += 1
            block = True
        if tag == "a":
            href = values.get("href") or None
        if not skip and "token" in classes:
            kind = next((short for name, short in KINDS if name in classes), "tx")
            self.tokens.append((kind, values.get("data-verso-hover") or None,
                                values.get("data-binding") or None, href))
            token = len(self.tokens) - 1
        if not self.tag and not skip and block and self.anchor_at is None and values.get("id") == self.anchor:
            self.anchor_at = self.length
        if tag not in VOID_TAGS:
            self.stack.append((tag, skip, block, token, href))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in VOID_TAGS:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.done or tag in VOID_TAGS:
            return
        if self.loose and all(entry[0] != tag for entry in self.stack):
            return
        while self.stack:
            if self.stack.pop()[0] == tag:
                break
        if not self.stack:
            self.done = True

    def handle_data(self, data: str) -> None:
        if self.done or not self.stack:
            return
        _tag, skip, block, token, _href = self.stack[-1]
        if block and not skip:
            self._append(data, token)


def subtree_end(data: mmap.mmap | bytes, start: int) -> int:
    """The end of the `span` element that starts at `start`."""
    depth = 0
    for found in SPAN_TAG.finditer(data, start):
        depth += -1 if found.group(1) else 1
        if depth == 0:
            return found.end()
    return len(data)


def read_box(data: mmap.mmap | bytes, fragment: str, room: int) -> Box | None:
    """The box that holds the element with this id, parsed from its start, or None."""
    at = id_position(data, fragment)
    return _parse_box(data, at, Box(fragment, past_anchor=room)) if at >= 0 else None


def read_tagged_box(data: mmap.mmap | bytes, tag: str, room: int) -> Box | None:
    """The box whose code holds this example's tag comment, its anchor the first `example` keyword
    after the tag; or None.

    The tag comment often ends the box of the declaration before it, whose
    proof may be megabytes, so when the comment is in a code element the box
    is read from the comment on, as that element's content, an end tag of an
    element opened before the comment passed over (`Box.loose`); failing
    that, from the box's start.
    """
    needle = f"`{tag}`".encode("utf-8")
    at = data.find(needle)
    while at >= 0:
        box = None
        comment = data.rfind(COMMENT_TOKEN, max(0, at - 4096), at)
        if comment >= 0 and in_code_block(data, comment):
            box = _parse_box(data, at, Box(f"`{tag}`", past_anchor=room, tag=True, loose=True), start=comment)
        box = box or _parse_box(data, at, Box(f"`{tag}`", past_anchor=room, tag=True))
        if box:
            return box
        at = data.find(needle, at + len(needle))
    return None


def in_code_block(data: mmap.mmap | bytes, at: int) -> bool:
    """Whether `at` is in a `code.block` element of a code box."""
    mark = data.rfind(BOX_CLASS, 0, at)
    code = data.rfind(CODE_BLOCK, mark, at) if mark >= 0 else -1
    return code >= 0 and data.find(b"</code>", code, at) < 0 and data.find(b"</div>", mark, at) < 0


def _parse_box(data: mmap.mmap | bytes, at: int, box: Box, start: int | None = None) -> Box | None:
    """`box` fed from the start of the `div.code-box` before `at`, or None when there is none or its
    anchor is not in it. With `start`, it is fed from there instead, as the content of a code box's
    code element (`in_code_block`).

    A goal view (`TACTIC_STATE`, most of a page's bytes) is passed over by
    counting its spans, not parsed; the parser would skip its content anyway.
    """
    if start is None:
        mark = data.rfind(BOX_CLASS, 0, at)
        start = data.rfind(b"<div", 0, mark) if mark >= 0 else -1
        if start < 0 or b">" in data[start:mark]:
            return None
    else:
        box.feed('<div class="code-box"><code class="hl lean block">')
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        while not box.done and start < len(data):
            goals = data.find(TACTIC_STATE, start, start + CHUNK)
            stop = goals if goals >= 0 else min(len(data), start + CHUNK)
            box.feed(decoder.decode(data[start:stop], final=stop >= len(data)))
            start = subtree_end(data, goals) if goals >= 0 else stop
    except UnicodeDecodeError:
        return None
    return None if box.failed or box.anchor_at is None else box


# ---------------------------------------------------------------------------
# matching the snippet
# ---------------------------------------------------------------------------

def _block_comment_end(text: str, at: int) -> int:
    """The end of the (nesting) `/- -/` comment that starts at `at`."""
    depth = 0
    while at < len(text):
        if text.startswith("/-", at):
            depth += 1
            at += 2
        elif text.startswith("-/", at):
            depth -= 1
            at += 2
            if depth == 0:
                return at
        else:
            at += 1
    return len(text)


def gap_end(text: str, at: int, comments: list[tuple[int, int]] | None = None) -> int:
    """The end of the whitespace and comments that start at `at`; comment spans are recorded."""
    while True:
        before = at
        while at < len(text) and text[at] in WHITESPACE:
            at += 1
        if text.startswith("/-", at):
            end = _block_comment_end(text, at)
        elif text.startswith("--", at):
            end = text.find("\n", at)
            end = len(text) if end < 0 else end
        else:
            end = at
        if end > at and comments is not None:
            comments.append((at, end))
        at = end
        if at == before:
            return at


def align(text: str, start: int, want: str,
          comments: list[tuple[int, int]]) -> tuple[list[tuple[int, int]], int] | None:
    """`want` matched in `text` from `start`: ([(want index, text index)] of every character
    that is neither whitespace nor a comment, end in text), or None.

    A gap (whitespace or a comment) on one side must be a gap on the other,
    except before the first character.
    """
    pairs: list[tuple[int, int]] = []
    i, j = start, 0
    while True:
        j2 = gap_end(want, j, comments)
        i2 = gap_end(text, i)
        if j2 >= len(want):
            return (pairs, pairs[-1][1] + 1) if pairs else None
        if pairs and (j2 > j) != (i2 > i):
            return None
        if i2 >= len(text) or text[i2] != want[j2]:
            return None
        pairs.append((j2, i2))
        i, j = i2 + 1, j2 + 1


def owners(box: Box) -> list[int]:
    """The token index of every character of the box's text, -1 for plain text."""
    out = [-1] * box.length
    for start, end, token in box.segments:
        if token >= 0:
            out[start:end] = [token] * (end - start)
    return out


def statement_runs(box: Box, snippet: str, site: Site, page: str,
                   hovers: dict[str, Any]) -> list[list[Any]] | None:
    """The snippet as runs carrying the box's tokens, or None when it does not match."""
    stripped = snippet.rstrip()
    elided = stripped.endswith(ELLIPSIS)
    want = stripped[:-1] if elided else snippet
    if ELLIPSIS in want or not want.strip():
        return None
    text = box.text
    assert box.anchor_at is not None
    line = text.rfind("\n", 0, box.anchor_at) + 1
    for _ in range(LINES_ABOVE + 1):
        comments: list[tuple[int, int]] = []
        found = align(text, line, want, comments)
        if found and found[0][0][1] <= box.anchor_at < found[1]:
            return _runs(box, snippet, want, found[0], comments, site, page, hovers)
        if line == 0:
            break
        line = text.rfind("\n", 0, line - 1) + 1
    return None


def _runs(box: Box, snippet: str, want: str, pairs: list[tuple[int, int]],
          comments: list[tuple[int, int]], site: Site, page: str,
          hovers: dict[str, Any]) -> list[list[Any]]:
    by_char = owners(box)
    token_of = [-1] * len(snippet)
    for j, i in pairs:
        token_of[j] = by_char[i]
    # A gap in the snippet takes the token that holds the whole of the page's gap opposite it
    # (a space inside a string literal); any other gap is plain.
    for (j0, i0), (j1, i1) in zip(pairs, pairs[1:]):
        held = set(by_char[i0 + 1:i1])
        if j1 > j0 + 1 and i1 > i0 + 1 and len(held) == 1 and -1 not in held:
            token_of[j0 + 1:j1] = [held.pop()] * (j1 - j0 - 1)
    in_comment = [False] * len(snippet)
    for start, end in comments:
        in_comment[start:end] = [True] * (end - start)
    runs: list[list[Any]] = []
    for at, char in enumerate(snippet):
        if at >= len(want):
            key: tuple[str, int] = ("el", -1) if char == ELLIPSIS else ("tx", -1)
        elif token_of[at] >= 0:
            key = ("token", token_of[at])
        else:
            key = ("cm", -1) if in_comment[at] else ("tx", -1)
        if runs and runs[-1][0] == key:
            runs[-1][1] += char
        else:
            runs.append([key, char])
    out: list[list[Any]] = []
    for (kind, token), value in runs:
        if kind != "token":
            out.append([kind, value])
            continue
        token_kind, hover, binding, href = box.tokens[token]
        out.append([token_kind, value,
                    int(hover) if hover and HOVER_ID.fullmatch(hover) and hover in hovers else None,
                    binding, link(site, page, href) if href else None])
    return _numbered(out)


def _numbered(runs: list[list[Any]]) -> list[list[Any]]:
    """Bindings as small numbers, kept only when two runs share one; plain runs merged, nulls trimmed."""
    counts: dict[str, int] = {}
    for run in runs:
        if len(run) > 3 and run[3]:
            counts[run[3]] = counts.get(run[3], 0) + 1
    numbers: dict[str, int] = {}
    out: list[list[Any]] = []
    for run in runs:
        if len(run) > 3:
            binding = run[3]
            run[3] = numbers.setdefault(binding, len(numbers) + 1) if binding and counts[binding] > 1 else None
            while len(run) > 2 and run[-1] is None:
                run.pop()
            if run[0] == "tx" and len(run) == 2 and out and out[-1][0] == "tx" and len(out[-1]) == 2:
                out[-1][1] += run[1]
                continue
        elif run[0] == "tx" and out and out[-1][0] == "tx" and len(out[-1]) == 2:
            out[-1][1] += run[1]
            continue
        out.append(run)
    return out


def link(site: Site, page: str, href: str) -> str | None:
    """A token's link as `<page>#<fragment>` relative to the site root, when its target exists."""
    parts = urllib.parse.urlsplit(href)
    if parts.scheme or parts.netloc or parts.query or not parts.path or "\\" in href:
        return None
    path = urllib.parse.unquote(parts.path)
    fragment = urllib.parse.unquote(parts.fragment)
    joined = posixpath.normpath(path.lstrip("/") if path.startswith("/")
                                else posixpath.join(site.base(page), path))
    if joined in {".", ".."} or joined.startswith("../"):
        return None
    target = f"{joined}/index.html" if path.endswith("/") or site.is_directory(joined) else joined
    if not (site.declares(target, fragment) if fragment else site.has_anchor(target, fragment)):
        return None
    quoted = urllib.parse.quote(target, safe="/")
    return f"{quoted}#{urllib.parse.quote(fragment, safe=URI_COMPONENT_SAFE)}" if fragment else quoted


# ---------------------------------------------------------------------------
# the hover table
# ---------------------------------------------------------------------------

class Entry(HTMLParser):
    """One hover entry: the text of its `code` elements, the docstring's apart."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.signature: list[str] = []
        self.doc: list[str] = []
        self.stack: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            return
        classes = (dict(attrs).get("class") or "").split()
        inherited = self.stack[-1] if self.stack else None
        self.stack.append("doc" if tag == "code" and "docstring" in classes
                          else "signature" if tag == "code" and inherited is None else inherited)

    def handle_endtag(self, tag: str) -> None:
        if tag not in VOID_TAGS and self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        where = self.stack[-1] if self.stack else None
        if where == "doc":
            self.doc.append(data)
        elif where == "signature":
            self.signature.append(data)


def hover_entry(value: Any) -> dict[str, str] | None:
    if not isinstance(value, str):
        return None
    entry = Entry()
    entry.feed(value)
    entry.close()
    out = {"signature": "".join(entry.signature).strip(), "doc": "".join(entry.doc).strip()}
    out = {key: text for key, text in out.items() if text}
    return out or None


def hover_table(site: Site) -> dict[str, Any]:
    """The site's hover table as `{id: html}`, or {} when it has none that reads."""
    path = site.resolve(DOCS_NAME)
    if path is None:
        return {}
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# the whole site
# ---------------------------------------------------------------------------

def extract(site_root: Path, entries: list[tuple[str, dict[str, Any]]]) -> tuple[dict[str, Any], dict[str, int]]:
    """`(verso.json, {"targets": n, "statements": m})` for the bundle's cards over the built site."""
    site = Site(site_root)
    table = hover_table(site)
    targets = card_statements(entries)
    statements: dict[str, list[list[list[Any]]]] = {}
    used: set[int] = set()
    try:
        for target, ((page_ref, how, what), texts) in targets.items():
            page = site_file(page_ref)
            data = site.page(page) if page else None
            if data is None:
                continue
            for text in texts:
                room = len(text) + 256
                box = read_box(data, what, room) if how == "id" else read_tagged_box(data, what, room)
                runs = statement_runs(box, text, site, page, table) if box else None
                if runs is None:
                    continue
                statements.setdefault(target, []).append(runs)
                used.update(run[2] for run in runs if len(run) > 2 and isinstance(run[2], int))
    finally:
        site.close()
    hovers = {}
    for number in sorted(used):
        entry = hover_entry(table.get(str(number)))
        if entry:
            hovers[str(number)] = entry
    for alternatives in statements.values():
        for runs in alternatives:
            for run in runs:
                if len(run) > 2 and isinstance(run[2], int) and str(run[2]) not in hovers:
                    run[2] = None
                    while len(run) > 2 and run[-1] is None:
                        run.pop()
    value = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "statements": statements, "hovers": hovers}
    return value, {"statements": sum(len(v) for v in statements.values()),
                   "targets": sum(len(texts) for _locator, texts in targets.values())}


def dumps(value: dict[str, Any]) -> bytes:
    """One deterministic, compact line of UTF-8 JSON."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")
