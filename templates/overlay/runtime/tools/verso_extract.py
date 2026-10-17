"""Cut each card's statement out of the built Verso site, with its hovers.

New code for `docs/overlay.md` sections 4 and 5: `build_site.py` calls
`extract` on the built site and publishes what it returns as
`<mount>/verso.json`, which the viewer reads to tint a key card's statement
the way the rendered page does and to show a hover for each identifier in it.

A card qualifies when its end's rendered page is a page of the site with an
anchor (the same page and fragment the viewer's `cardHref` builds). That
page's `div.code-box` holding the anchor is parsed with `html.parser`, from
the box's start only, and its code text is flattened in reading order:
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
anchor it names. Only the hover entries the statements use are copied from
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
SPAN_TAG = re.compile(rb"<(/?)span\b[^>]*>")
WHITESPACE = " \t\r\n"
HOVER_ID = re.compile(r"[0-9]{1,9}\Z")
BASE_TAG = re.compile(rb"<base\s[^>]*href=\"([^\"]*)\"", re.IGNORECASE)
#: The characters `encodeURIComponent` leaves alone.
URI_COMPONENT_SAFE = "-_.!~*'()"


# ---------------------------------------------------------------------------
# which cards qualify
# ---------------------------------------------------------------------------

def _trimmed(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def card_target(end: dict[str, Any]) -> str | None:
    """`<page>#<fragment>` of an object end whose rendered page has an anchor, or None.

    The viewer's `cardTarget` in `viewer-core.js`, written again: the page is
    the destination's own `href`, else the module's page; the fragment is the
    destination's own, else the declaration's name with each "." as "___",
    and only an `exact` destination has one.
    """
    ext = overlay_annotations.extension(end)
    destination = ext.get("destination")
    if not isinstance(destination, dict) or destination.get("resolution") != "exact":
        return None
    location = end.get("location") if isinstance(end.get("location"), dict) else {}
    if isinstance(destination.get("href"), str):
        page = destination["href"]
    else:
        module = _trimmed(location.get("module"))
        page = "/".join(module.split(".")) + "/index.html" if module else ""
    if isinstance(destination.get("fragment"), str):
        fragment = destination["fragment"]
    else:
        fragment = "___".join(_trimmed(location.get("declaration")).split("."))
    return f"{page}#{fragment}" if page and fragment else None


def snippet_text(end: dict[str, Any]) -> str | None:
    snippet = overlay_annotations.extension(end).get("snippet")
    value = snippet.get("text") if isinstance(snippet, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def card_statements(entries: list[tuple[str, dict[str, Any]]]) -> dict[str, list[str]]:
    """{target: [snippet text, ...]} in bundle order, each text once per target."""
    found: dict[str, list[str]] = {}
    for _path, annotation in entries:
        for end in annotation.get("object") or []:
            if not isinstance(end, dict):
                continue
            target, text = card_target(end), snippet_text(end)
            if target and text and text not in found.setdefault(target, []):
                found[target].append(text)
    return {target: texts for target, texts in found.items() if texts}


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

    def resolve(self, relative: str) -> Path | None:
        """A regular file under the root, every component a plain directory."""
        parts = PurePosixPath(relative).parts
        if not parts:
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
        path = self.root.joinpath(*PurePosixPath(relative).parts)
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


# ---------------------------------------------------------------------------
# one code box
# ---------------------------------------------------------------------------

class Box(HTMLParser):
    """The code text of one `div.code-box`, its tokens, and where the anchor's text starts."""

    def __init__(self, anchor: str, *, past_anchor: int) -> None:
        super().__init__(convert_charrefs=True)
        self.anchor = anchor
        self.past_anchor = past_anchor
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
        if not skip and block and self.anchor_at is None and values.get("id") == self.anchor:
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
    """The box that holds the element with this id, parsed from its start, or None.

    A goal view (`TACTIC_STATE`, most of a page's bytes) is passed over by
    counting its spans, not parsed; the parser would skip its content anyway.
    """
    at = id_position(data, fragment)
    if at < 0:
        return None
    mark = data.rfind(BOX_CLASS, 0, at)
    start = data.rfind(b"<div", 0, mark) if mark >= 0 else -1
    if start < 0 or b">" in data[start:mark]:
        return None
    box = Box(fragment, past_anchor=room)
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
    if not site.has_anchor(target, fragment):
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
        for target, texts in targets.items():
            page_ref, _, fragment = target.partition("#")
            page = site_file(page_ref)
            data = site.page(page) if page else None
            if data is None:
                continue
            for text in texts:
                box = read_box(data, fragment, room=2 * len(text) + 1024)
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
                   "targets": sum(len(v) for v in targets.values())}


def dumps(value: dict[str, Any]) -> bytes:
    """One deterministic, compact line of UTF-8 JSON."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")
