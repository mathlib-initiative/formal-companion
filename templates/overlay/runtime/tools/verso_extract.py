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

A companion whose site has no Verso pages (its cards link elsewhere) can
commit a `pdf-overlay/verso.json` cut from a Verso build of its project made
somewhere else: `python3 verso_extract.py --pages <literate-html> --bundle
<annotations.bundle.json> --out <verso.json> --commit <sha>`. There a card
is found by its Lean location instead of its link: its module's page
(`A/B/index.html` for `A.B`) and the anchor Verso gives its declaration (the
build's `xref.json`, else `verso_slug` of its name), or for a tagged example
its tag. It is filed under the same key, the same rules match the snippet,
no token keeps a link (those pages are not the viewer's site), and `build`
records the project commit, the build's Verso revision and its dependency
repository pins (`build_record`). Optional `--dependency-pages REPOSITORY=DIR`
adds that dependency's pages, with hover IDs remapped across independent
builds; a dependency card is kept only at its repository's own pinned commit.
Line-only ends match their exact excerpts in the module's code boxes; sibling
literate metadata, when present, confines the search to the enclosing declaration.
Repeated or stale excerpts remain plain rather than borrowing another proof's bindings.
`build_site.py` publishes it only for a site with no Verso pages, and only
when its cards pin that commit (`unpinned`), each statement checked again
against the bundle (`committed_problems`, `joined`). A card with a statement
there pins a full commit, not a release tag (`inexact_pins`).
"""
from __future__ import annotations

import argparse
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
from typing import Any, Callable, Iterator
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
#: The run kinds this file writes, as the viewer's `RUN_KINDS`.
RUN_KINDS = frozenset({"tx", "kw", "cm", "el", "const", "var", "sort", "lit"})
#: A Verso build's cross-reference table at its root, and its domain of constants.
XREF_NAME = "xref.json"
XREF_CONSTANTS = "VersoHtml.constant"
#: Verso's `asSlug` (`MultiVerso/Slug.lean`): ASCII letters, digits, "-" and "_" stay, Lean's whitespace is "-",
#: these characters are spelled out, and any other (each "." of a name) is "___".
SLUG_KEEP = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
SLUG_WHITESPACE = frozenset(" \t\r\n")
SLUG_NAMES = {"<": "_LT_", ">": "_GT_", ";": "_SEMI_", "‹": "_FLQ_", "›": "_FRQ_", "«": "_FLQQ_", "»": "_FLQQ_",
              "⟨": "_LANGLE_", "⟩": "_RANGLE_", "(": "_LPAR_", ")": "_RPAR_", "[": "_LSQ_", "]": "_RSQ_",
              "→": "_ARR_", "↦": "_MAPSTO_", "⊢": "_VDASH_"}
COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
#: The keys of a committed file; `build` is what `--pages` records about the build.
FILE_KEYS = frozenset({"schema", "schema_version", "statements", "hovers", "build"})


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


Locator = tuple[str, str, str]


def card_statements(entries: list[tuple[str, dict[str, Any]]],
                    locate: Callable[[dict[str, Any]], Locator | None] = card_locator,
                    ) -> dict[str, list[tuple[Locator, str]]]:
    """{target: [(locator, snippet text), ...]} in bundle order, each pair once: the target is the key
    the viewer looks the card up by (`card_target`), the locator where `locate` says its statement is."""
    found: dict[str, list[tuple[Locator, str]]] = {}
    for _path, annotation in entries:
        for end in annotation.get("object") or []:
            if not isinstance(end, dict):
                continue
            target, locator, text = card_target(end), locate(end), snippet_text(end)
            if target and locator and text and (locator, text) not in found.get(target, []):
                found.setdefault(target, []).append((locator, text))
    return found


def module_page(module: str) -> str | None:
    """A module's page in a Verso build, `A/B/index.html` for `A.B`, or None."""
    parts = module.split(".")
    if not module or not all(part and part not in {".", ".."} and "/" not in part and "\\" not in part
                             for part in parts):
        return None
    return "/".join(parts) + "/index.html"


def verso_slug(name: str) -> str:
    """The anchor Verso gives a name: `asSlug` of `MultiVerso/Slug.lean`, character by character."""
    return "".join(char if char in SLUG_KEEP else "-" if char in SLUG_WHITESPACE else SLUG_NAMES.get(char, "___")
                   for char in name)


def object_locator(end: dict[str, Any], ids: dict[tuple[str, str], str],
                   site: Site | None = None) -> Locator | None:
    """`(page, how, what)` of an object end on a Verso build's own pages, from its Lean location alone:
    its module's page and its declaration's anchor (`ids`, else `verso_slug` of its name), a
    tagged example's tag, or a range with its enclosing declaration when known."""
    location = end.get("location") if isinstance(end.get("location"), dict) else {}
    module = _trimmed(location.get("module"))
    page = module_page(module)
    if page is None:
        return None
    name = _trimmed(location.get("declaration"))
    ext = overlay_annotations.extension(end)
    declaration = ext.get("declaration") if isinstance(ext.get("declaration"), dict) else {}
    tag = declaration.get("id")
    if declaration.get("kind") == "example" and isinstance(tag, str) and EXAMPLE_ID.match(tag):
        return page, "tag", tag
    lines = location.get("lines")
    if isinstance(lines, list) and 1 <= len(lines) <= 2 and all(type(n) is int and n > 0 for n in lines):
        first, last = lines[0], lines[-1]
        if first <= last:
            owner = (range_owner(site, module, first, last) if site else None) or name
            return page, "range", ids.get((owner, module), verso_slug(owner)) if owner else ""
    if name:
        return page, "id", ids.get((name, module)) or verso_slug(name)
    return None


def range_owner(site: Site, module: str, first: int, last: int) -> str | None:
    """The declaration enclosing these lines in Verso's sibling literate data, when available.

    HTML alone can highlight a unique excerpt. Build metadata disambiguates
    repeated excerpts without borrowing another declaration's local bindings.
    """
    relative = module_page(module)
    if relative is None:
        return None
    if module not in site.range_owners:
        site.range_owners[module] = []
        path = Site(site.root.parent / "literate").resolve(relative.removesuffix("/index.html") + ".json")
        try:
            data = json.loads(path.read_bytes()) if path else {}
            commands = data.get("items", {}).get("items", []) if data.get("module") == module else []
            for command in commands:
                span, names = command.get("range"), command.get("defines")
                if isinstance(span, list) and len(span) == 2 and isinstance(names, list) and len(names) == 1 \
                        and isinstance(names[0], str) and names[0] \
                        and all(isinstance(p, dict) and type(p.get("line")) is int for p in span):
                    site.range_owners[module].append((span[0]["line"], span[1]["line"], names[0]))
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    owners = {name for start, stop, name in site.range_owners[module] if start <= first <= last <= stop}
    return owners.pop() if len(owners) == 1 else None


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
        self.declared: dict[tuple[str, str], bool] = {}
        self.range_owners: dict[str, list[tuple[int, int, str]]] = {}

    @staticmethod
    def parts(relative: str) -> tuple[str, ...] | None:
        """The components of a path relative to the root, or None when it is absolute or has a `..` or a NUL."""
        parts = PurePosixPath(relative).parts
        if not parts or relative.startswith("/") or "\\" in relative or "\0" in relative or ".." in parts:
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
        self.anchors: dict[str, int] = {}
        self.declarations: dict[str, int] = {}
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
        if not skip and block and values.get("id"):
            self.anchors[values["id"]] = self.length
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


def read_range_boxes(data: mmap.mmap | bytes) -> list[Box]:
    """All code boxes, excluding goal views and messages, parsed once for a module's ranges."""
    boxes: list[Box] = []
    start = 0
    while True:
        at = data.find(BOX_CLASS, start)
        if at < 0:
            return boxes
        start = at + len(BOX_CLASS)
        box = Box("", past_anchor=len(data))
        box.anchor_at = 0
        box = _parse_box(data, start, box)
        if box:
            box.declarations = {name: pos for name, pos in box.anchors.items()
                                if declared_at(data, id_position(data, name))}
            boxes.append(box)


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
                   hovers: dict[str, Any], *, links: bool = True) -> list[list[Any]] | None:
    """The snippet as runs carrying the box's tokens (with no link unless `links`), or None when it
    does not match."""
    # leansnippets.field_text labels a projection with a synthetic structure
    # header. Match only the real field against its own anchor; the label and
    # omission marker carry no borrowed token information.
    field = re.fullmatch(r"(structure ([^\s]+) where\n  )…(\n)([ \t]+.+)", snippet, re.DOTALL)
    if field and box.anchor_at is not None:
        owner = re.search(r"(?:class|structure)\s+" + re.escape(field[2]) + r"(?=\s|\(|\{|:)",
                          box.text[:box.anchor_at])
        runs = statement_runs(box, field[4], site, page, hovers, links=links) if owner else None
        if runs:
            return [["tx", field[1]], ["el", ELLIPSIS], ["tx", field[3]], *runs]
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
            return _runs(box, snippet, want, found[0], comments, site if links else None, page, hovers)
        if line == 0:
            break
        line = text.rfind("\n", 0, line - 1) + 1
    return None


def range_runs(boxes: list[Box], snippet: str, site: Site, page: str,
               hovers: dict[str, Any], *, fragment: str = "", links: bool = True) -> list[list[Any]] | None:
    """Match a proof excerpt uniquely, within its recorded declaration when one is known.

    Deduplicate matches reached through the same leading blank lines/comments.
    Missing, ambiguous and stale snippets remain plain; goal text is never a candidate.
    """
    stripped = snippet.rstrip()
    want = stripped[:-1] if stripped.endswith(ELLIPSIS) else snippet
    if ELLIPSIS in want or not want.strip():
        return None
    found: dict[tuple[int, int, int], tuple[Box, list[tuple[int, int]], list[tuple[int, int]]]] = {}
    for number, box in enumerate(boxes):
        text = box.text
        lower, upper = 0, len(text)
        if fragment:
            if fragment not in box.declarations:
                continue
            anchor = box.declarations[fragment]
            lower = text.rfind("\n", 0, anchor) + 1
            upper = min((text.rfind("\n", 0, at) + 1 for at in box.declarations.values() if at > anchor),
                        default=len(text))
        for at in [lower, *(lower + m.end() for m in re.finditer("\n", text[lower:upper]))]:
            comments: list[tuple[int, int]] = []
            match = align(text, at, want, comments)
            if match and match[1] <= upper:
                key = (number, match[0][0][1], match[1])
                found[key] = box, match[0], comments
                if len(found) > 1:
                    return None
    if not found:
        return None
    box, pairs, comments = next(iter(found.values()))
    return _runs(box, snippet, want, pairs, comments, site if links else None, page, hovers)


def _runs(box: Box, snippet: str, want: str, pairs: list[tuple[int, int]],
          comments: list[tuple[int, int]], site: Site | None, page: str,
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
                    binding, link(site, page, href) if href and site else None])
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
    """A token's link as `<page>#<fragment>` relative to the site root, when the fragment is a
    declaration's own anchor on that page (`Site.declares`)."""
    parts = urllib.parse.urlsplit(href)
    if parts.scheme or parts.netloc or parts.query or not parts.path or not parts.fragment or "\\" in href:
        return None
    path = urllib.parse.unquote(parts.path)
    fragment = urllib.parse.unquote(parts.fragment)
    joined = posixpath.normpath(path.lstrip("/") if path.startswith("/")
                                else posixpath.join(site.base(page), path))
    if joined in {".", ".."} or joined.startswith("../"):
        return None
    target = f"{joined}/index.html" if path.endswith("/") or site.is_directory(joined) else joined
    if not site.declares(target, fragment):
        return None
    return f"{urllib.parse.quote(target, safe='/')}#{urllib.parse.quote(fragment, safe=URI_COMPONENT_SAFE)}"


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
    ids = declaration_ids(site)

    def locate(end: dict[str, Any]) -> Locator | None:
        destination = card_locator(end)
        source = object_locator(end, ids, site)
        # Keep the rendered page's own path; only the source context is shared.
        return (destination[0], "range", source[2]) if destination and source and source[1] == "range" else destination

    return _extract(site, card_statements(entries, locate), links=True)


def extract_pages(pages_root: Path, entries: list[tuple[str, dict[str, Any]]], *,
                  commit: str | None = None,
                  dependency_pages: dict[str, Path] | None = None) -> tuple[dict[str, Any], dict[str, int]]:
    """Extract another build's pages, with optional pages of dependencies its manifest pins.

    Each dependency is named by its repository URI. Hover IDs belong to one
    page build, so merging remaps them before combining the statements.
    """
    site = Site(pages_root)
    ids = declaration_ids(site)
    value, counts = _extract(site, card_statements(entries, lambda end: object_locator(end, ids, site)), links=False)
    build = build_record(site.root, commit)
    if build:
        value["build"] = build
    for repo, root in sorted((dependency_pages or {}).items()):
        if repo not in build.get("dependencies", {}):
            raise overlay_annotations.AnnotationError(f"dependency pages for {repo} have no pinned repository "
                                                      "in the primary build's package manifest")
        selected = [(path, dict(annotation, object=[end for end in annotation.get("object", [])
                                                    if isinstance(end, dict) and end.get("uri") == repo]))
                    for path, annotation in entries]
        site = Site(root)
        ids = declaration_ids(site)
        extra, _ = _extract(site, card_statements(selected, lambda end: object_locator(end, ids, site)), links=False)
        merge_statements(value, extra)
    counts["statements"] = sum(len(v) for v in value["statements"].values())
    return value, counts


def merge_statements(value: dict[str, Any], extra: dict[str, Any]) -> None:
    """Add statements from another page build, remapping its independent hover IDs."""
    numbers = {json.dumps(entry, sort_keys=True): key for key, entry in value["hovers"].items()}
    next_id = max((int(key) for key in value["hovers"]), default=-1) + 1
    remap = {}
    for key, entry in extra["hovers"].items():
        identity = json.dumps(entry, sort_keys=True)
        if identity not in numbers:
            numbers[identity] = str(next_id)
            value["hovers"][str(next_id)] = entry
            next_id += 1
        remap[key] = int(numbers[identity])
    for target, alternatives in extra["statements"].items():
        for runs in alternatives:
            copied = [list(run) for run in runs]
            for run in copied:
                if len(run) > 2 and run[2] is not None:
                    run[2] = remap[str(run[2])]
            if copied not in value["statements"].get(target, []):
                value["statements"].setdefault(target, []).append(copied)


def _extract(site: Site, targets: dict[str, list[tuple[Locator, str]]], *,
             links: bool) -> tuple[dict[str, Any], dict[str, int]]:
    table = hover_table(site)
    statements: dict[str, list[list[list[Any]]]] = {}
    used: set[int] = set()
    range_boxes: dict[str, list[Box]] = {}
    try:
        for target, pairs in targets.items():
            for (page_ref, how, what), text in pairs:
                page = site_file(page_ref)
                data = site.page(page) if page else None
                if data is None:
                    continue
                if how == "range":
                    if page not in range_boxes:
                        range_boxes[page] = read_range_boxes(data)
                    runs = range_runs(range_boxes[page], text, site, page, table, fragment=what, links=links)
                else:
                    room = len(text) + 256
                    box = read_box(data, what, room) if how == "id" else read_tagged_box(data, what, room)
                    runs = statement_runs(box, text, site, page, table, links=links) if box else None
                if runs is None or runs in statements.get(target, []):
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
                   "targets": sum(len(pairs) for pairs in targets.values())}


def is_verso_site(site_root: Path) -> bool:
    """Whether a built site is a Verso build: its root holds Verso's hover table."""
    return Site(site_root).resolve(DOCS_NAME) is not None


# ---------------------------------------------------------------------------
# a build elsewhere: its anchors, its record, and the file a companion commits
# ---------------------------------------------------------------------------

def declaration_ids(site: Site) -> dict[tuple[str, str], str]:
    """{(declaration, module): anchor} from the build's cross-reference table, for each constant it
    files on its module's page under one anchor: a private declaration's anchor carries the private
    prefix its name does not show. Empty when there is no table that reads."""
    path = site.resolve(XREF_NAME)
    try:
        value = json.loads(path.read_bytes().decode("utf-8")) if path is not None else None
    except (OSError, UnicodeDecodeError, ValueError):
        value = None
    domain = value.get(XREF_CONSTANTS) if isinstance(value, dict) else None
    contents = domain.get("contents") if isinstance(domain, dict) else None
    found: dict[tuple[str, str], set[str]] = {}
    for name, rows in (contents.items() if isinstance(contents, dict) else ()):
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict) or not isinstance(row.get("data"), dict):
                continue
            user, module = row["data"].get("userName") or name, row["data"].get("module")
            anchor, address = row.get("id"), row.get("address")
            if all(isinstance(item, str) and item for item in (user, module, anchor, address)) \
                    and site_file(address.lstrip("/")) == module_page(module):
                found.setdefault((user, module), set()).add(anchor)
    return {key: anchors.pop() for key, anchors in found.items() if len(anchors) == 1}


def dependency_uri(value: Any) -> str | None:
    """A Lake HTTPS git URL in the repository form Literature Links uses."""
    if not isinstance(value, str) or not re.fullmatch(r"https://[^\s?#]+", value):
        return None
    try:
        if not urllib.parse.urlsplit(value).hostname:
            return None
    except ValueError:
        return None
    return value.rstrip("/").removesuffix(".git")


def matches_build(build: dict[str, Any], end: dict[str, Any]) -> bool:
    """The end pins its dependency's revision, or the primary project's commit."""
    expected = build.get("dependencies", {}).get(end.get("uri"), build["commit"])
    return end.get("version") == expected


def build_record(root: Path, commit: str | None) -> dict[str, Any]:
    """The project commit, Verso revision and dependency repository pins in lake-manifest.json.

    The manifest is read only for pages in the project's .lake/build/<directory>.
    """
    out: dict[str, Any] = {"commit": commit} if commit else {}
    if root.parent.name != "build" or root.parent.parent.name != ".lake":
        return out
    try:
        value = json.loads((root.parent.parent.parent / "lake-manifest.json").read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return out
    packages = value.get("packages") if isinstance(value, dict) else None
    for package in packages if isinstance(packages, list) else ():
        if isinstance(package, dict) and package.get("name") == "verso":
            verso = {key: package[key] for key in ("inputRev", "rev") if isinstance(package.get(key), str)}
            if verso:
                out["verso"] = verso
        elif isinstance(package, dict) and package.get("type") == "git":
            repo = dependency_uri(package.get("url"))
            rev = package.get("rev")
            if repo and _is_commit(rev):
                out.setdefault("dependencies", {})[repo] = rev
    return out


def bundle_cards(entries: list[tuple[str, dict[str, Any]]]) -> Iterator[tuple[str, dict[str, Any], str, str]]:
    """`(annotation's path, end, target, snippet)` of each object end with a key and a snippet."""
    for path, annotation in entries:
        for end in annotation.get("object") or []:
            target, text = (card_target(end), snippet_text(end)) if isinstance(end, dict) else (None, None)
            if target and text:
                yield path, end, target, text


def pinned(entries: list[tuple[str, dict[str, Any]]]) -> set[str]:
    """What the bundle's cards pin: the `version` of each object end with a key and a snippet."""
    return {end["version"] for _path, end, _target, _text in bundle_cards(entries)
            if isinstance(end.get("version"), str)}


def _is_commit(version: Any) -> bool:
    return isinstance(version, str) and COMMIT.match(version) is not None


def _named(items: list[str]) -> str:
    return ", ".join(items[:3]) + (f" and {len(items) - 3} more" if len(items) > 3 else "")


def inexact_pins(value: dict[str, Any], entries: list[tuple[str, dict[str, Any]]]) -> str | None:
    """Why the cards of a committed `verso.json` (one `committed_problems` passes) cannot be held to its
    build, and what settles it; None when they can.

    A statement is of one tree, and `joined` keeps it for a card that pins the
    project's commit or its dependency's recorded revision. Another commit is another tree. A release tag, which
    Literature Links allows, or no `version` says nothing either way, and only
    the project's git could tell, so a statement that would go for that reason
    is refused rather than dropped, and so is a file when no card pins a commit
    at all. A card with no statement here loses nothing to its pin: a
    declaration of another project may stay at its release.
    """
    commit = value["build"]["commit"]
    cards = list(bundle_cards(entries))
    lost = {(target, "".join(run[1] for run in runs))
            for target, alternatives in value["statements"].items() for runs in alternatives}
    lost -= {(target, text) for _path, end, target, text in cards if matches_build(value["build"], end)}
    inexact = [card for card in cards if not _is_commit(card[1].get("version"))]
    found = [card for card in inexact if card[2:] in lost] or (inexact if len(inexact) == len(cards) else [])
    if not found:
        return None
    versions = sorted({repr(end["version"]) if isinstance(end.get("version"), str) else "no version"
                       for _path, end, _target, _text in found})
    paths = list(dict.fromkeys(path for path, _end, _target, _text in found))
    return (f"these cards pin {_named(versions)}, not a full lowercase commit, so they cannot be held to this "
            f"build of {commit}: {_named(paths)}; pin each by a full commit (for a tag, "
            "`git rev-parse '<tag>^{commit}'` in its project), then run `cf overlay sync`")


def unpinned(value: dict[str, Any], entries: list[tuple[str, dict[str, Any]]]) -> str | None:
    """Why a committed `verso.json` (one `committed_problems` passes) is not of the build the cards pin,
    and what settles it: a pin that cannot be compared (`inexact_pins`), or a build's commit that no card
    pins, so its hovers would be another build's. None when it is."""
    inexact = inexact_pins(value, entries)
    if inexact:
        return inexact
    commit, commits = value["build"]["commit"], sorted(filter(_is_commit, pinned(entries)))
    if not commits or any(matches_build(value["build"], end)
                          for _path, end, _target, _text in bundle_cards(entries)):
        return None
    return (f"cut from a build of {commit}, but the cards pin {_named(commits)}; write it again with "
            "`verso_extract.py --pages` from a build of that commit")


def _run_problem(run: Any, hovers: dict[str, Any]) -> str | None:
    if not isinstance(run, list) or not 2 <= len(run) <= 5:
        return "a run is not [kind, text, hover, binding]"
    if len(run) == 5:
        return "a run carries a link, to a page that is not this site's"
    if not isinstance(run[0], str) or run[0] not in RUN_KINDS or not isinstance(run[1], str) or not run[1]:
        return f"a run is not a known kind with its text: {run[:2]!r}"
    hover = run[2] if len(run) > 2 else None
    if hover is not None and not (type(hover) is int and str(hover) in hovers):
        return f"a run's hover {hover!r} is not in hovers"
    binding = run[3] if len(run) > 3 else None
    if binding is not None and not (type(binding) is int and binding > 0):
        return f"a run's binding {binding!r} is not a positive integer"
    return None


def committed_problems(value: Any, limit: int = 10) -> list[str]:
    """What keeps a committed `verso.json` from being one `--pages` writes, at most `limit` lines: its
    schema, each hover entry, each statement's runs (none with a link), and its `build` record."""
    if not isinstance(value, dict):
        return ["expected an object"]
    out = [f"unknown key {key!r}" for key in sorted(set(value) - FILE_KEYS)]
    if value.get("schema") != SCHEMA or type(value.get("schema_version")) is not int \
            or value["schema_version"] != SCHEMA_VERSION:
        out.append(f"expected schema {SCHEMA} version {SCHEMA_VERSION}")
    hovers = value.get("hovers")
    if not isinstance(hovers, dict):
        out.append("hovers: expected an object")
        hovers = {}
    for key, entry in hovers.items():
        if not HOVER_ID.match(key) or not isinstance(entry, dict) or not entry \
                or not all(name in ("signature", "doc") and isinstance(text, str) and text
                           for name, text in entry.items()):
            out.append(f"hovers[{key!r}]: expected {{signature, doc}} as text")
    statements = value.get("statements")
    if not isinstance(statements, dict):
        out.append("statements: expected an object")
        statements = {}
    for target, alternatives in statements.items():
        if not target or not isinstance(alternatives, list) or not alternatives \
                or not all(isinstance(runs, list) and runs for runs in alternatives):
            out.append(f"statements[{target!r}]: expected a list of statements, each a list of runs")
            continue
        problem = next(filter(None, (_run_problem(run, hovers) for runs in alternatives for run in runs)), None)
        if problem:
            out.append(f"statements[{target!r}]: {problem}")
    build = value.get("build")
    if not isinstance(build, dict) or set(build) - {"commit", "verso", "dependencies"} \
            or not (isinstance(build.get("commit"), str) and COMMIT.match(build["commit"])) \
            or not isinstance(build.get("verso", {}), dict) \
            or not all(isinstance(text, str) for text in build.get("verso", {}).values()):
        out.append("build: expected {commit, verso}: the commit the pages were built from (--commit) and "
                   "the Verso package's revisions")
    if isinstance(build, dict) and "dependencies" in build:
        dependencies = build["dependencies"]
        if not isinstance(dependencies, dict) or not all(
                dependency_uri(repo) == repo and _is_commit(rev) for repo, rev in dependencies.items()):
            out.append("build.dependencies: expected repository HTTPS URIs mapped to full commits")
    return out[:limit] + ([f"and {len(out) - limit} more"] if len(out) > limit else [])


def joined(value: dict[str, Any], entries: list[tuple[str, dict[str, Any]]]) -> tuple[dict[str, Any], dict[str, int]]:
    """A committed `verso.json` (one `committed_problems` passes) cut to the bundle: a statement stays
    only where its runs' texts joined are the snippet of a card the viewer files under its target and
    that card pins its project's recorded commit, and a hover entry only where a statement left uses it.
    `(verso.json, {"targets", "statements", "dropped"})`."""
    cards: set[tuple[str, str]] = set()
    wanted: dict[str, set[str]] = {}
    for _path, end, target, text in bundle_cards(entries):
        cards.add((target, text))
        if matches_build(value["build"], end):
            wanted.setdefault(target, set()).add(text)
    statements: dict[str, list[list[list[Any]]]] = {}
    dropped = 0
    for target, alternatives in value["statements"].items():
        kept = [runs for runs in alternatives if "".join(run[1] for run in runs) in wanted.get(target, ())]
        dropped += len(alternatives) - len(kept)
        if kept:
            statements[target] = kept
    used = {str(run[2]) for alternatives in statements.values() for runs in alternatives for run in runs
            if len(run) > 2 and run[2] is not None}
    out = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "statements": statements, "build": value["build"],
           "hovers": {key: entry for key, entry in value["hovers"].items() if key in used}}
    return out, {"dropped": dropped, "statements": sum(len(v) for v in statements.values()), "targets": len(cards)}


def dumps(value: dict[str, Any]) -> bytes:
    """One deterministic, compact line of UTF-8 JSON."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def dumps_lines(value: dict[str, Any]) -> bytes:
    """`dumps`, with each hover entry and each target's statements on a line of its own: the form a
    committed file is written in, so that a diff names the cards that changed."""
    def one(item: Any) -> str:
        return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    parts = []
    for key in sorted(value):
        item = value[key]
        if key in ("hovers", "statements") and isinstance(item, dict) and item:
            parts.append(one(key) + ":{\n" + ",\n".join(f"{one(name)}:{one(item[name])}" for name in sorted(item))
                         + "\n}")
        else:
            parts.append(f"{one(key)}:{one(item)}")
    return ("{" + ",\n".join(parts) + "}\n").encode("utf-8")


# ---------------------------------------------------------------------------
# the command, for a build elsewhere
# ---------------------------------------------------------------------------

def bundle_entries(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """`[(path, annotation)]` of an annotation bundle file, as `cf overlay sync` writes it."""
    label = path.name
    try:
        value = overlay_annotations.strict_json(overlay_annotations.read_regular(path, label).decode("utf-8"), label)
    except UnicodeDecodeError as error:
        raise overlay_annotations.AnnotationError(f"{label}: not UTF-8") from error
    rows = value.get("annotations") if isinstance(value, dict) else None
    if not isinstance(rows, list) or value.get("schema") != overlay_annotations.BUNDLE_SCHEMA \
            or not all(isinstance(row, dict) and isinstance(row.get("path"), str)
                       and isinstance(row.get("annotation"), dict) for row in rows):
        raise overlay_annotations.AnnotationError(f"{label}: expected a {overlay_annotations.BUNDLE_SCHEMA} bundle")
    return [(row["path"], row["annotation"]) for row in rows]


def write_out(path: Path, raw: bytes) -> None:
    """`raw` at `path`, through a temporary file beside it; never through a symlink."""
    if path.is_symlink() or path.exists() and not path.is_file():
        raise OSError(f"{path}: not an ordinary file")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(raw)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verso_extract.py",
        description="Cut each card's statement and its hovers out of a Verso build that is not the "
                    "overlay's site, for a committed pdf-overlay/verso.json.")
    parser.add_argument("--pages", required=True, type=Path, metavar="DIR",
                        help="the build's literate HTML root (its -verso-docs.json and module pages)")
    parser.add_argument("--dependency-pages", action="append", default=[], metavar="REPOSITORY=DIR",
                        help="pages of a dependency pinned in the primary build's package manifest; repeatable")
    parser.add_argument("--bundle", required=True, type=Path, metavar="FILE",
                        help="the companion's pdf-overlay/annotations.bundle.json")
    parser.add_argument("--out", required=True, type=Path, metavar="FILE", help="the verso.json to write")
    parser.add_argument("--commit", required=True, metavar="SHA",
                        help="the commit of the Lean project the pages were built from, which the cards pin")
    args = parser.parse_args(argv)
    if not COMMIT.match(args.commit):
        parser.error("--commit: expected a full lowercase commit sha")
    if not args.pages.is_dir():
        parser.error(f"--pages: {args.pages} is not a directory")
    dependencies = {}
    for item in args.dependency_pages:
        repo, separator, directory = item.partition("=")
        if not separator or dependency_uri(repo) != repo or not Path(directory).is_dir():
            parser.error(f"--dependency-pages: expected a repository HTTPS URI and a directory, got {item!r}")
        if repo in dependencies:
            parser.error(f"--dependency-pages: repeated repository {repo}")
        dependencies[repo] = Path(directory)
    try:
        entries = bundle_entries(args.bundle)
        value, counts = extract_pages(args.pages, entries, commit=args.commit, dependency_pages=dependencies)
        inexact = inexact_pins(value, entries)
        if inexact:
            raise overlay_annotations.AnnotationError(inexact)
        raw = dumps_lines(value)
        write_out(args.out, raw)
    except (overlay_annotations.AnnotationError, OSError) as error:
        print(f"STOP {error}", file=sys.stderr)
        return 1
    print(json.dumps({**counts, "bytes": len(raw), "hovers": len(value["hovers"])}, sort_keys=True))
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
