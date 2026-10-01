"""Declarations from the companion's own Lake build (``.ilean`` + literate JSON), checked fresh by Lake's hashes.

Per file, falls back to :mod:`adapters.leanparse` when no verified build data exists; never runs Lean or Lake.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
from collections import Counter
from pathlib import Path

from . import leanparse
from .leanparse import DEFAULT_DECL_KINDS, DEFINITION_KINDS, Decl

__all__ = ["Build", "parse_file", "of_text", "tally", "fingerprint"]

ENV = "CF_LEAN_SOURCE"

_M = 0xC6A4A7935BD1E995
_MASK = (1 << 64) - 1

#: words that may precede a declaration keyword in its header
_HEADER_WORDS = frozenset(leanparse._MODIFIERS) | {"public", "meta"}
#: commands that prefix another as ``<cmd> ... in``
_IN_PREFIXES = frozenset({"open", "include", "omit", "set_option", "variable"})


def _murmur64a(data: bytes, seed: int) -> int:
    """MurmurHash64A, Lean's ``lean_string_hash``."""
    M, MASK = _M, _MASK
    n = len(data)
    h = (seed ^ ((n * M) & MASK)) & MASK
    end = n - n % 8
    if sys.byteorder == "little":
        words = memoryview(data)[:end].cast("Q")
    else:  # pragma: no cover - big-endian hosts
        words = [w for (w,) in struct.iter_unpack("<Q", data[:end])]
    for k in words:
        k = (k * M) & MASK
        h = (((k ^ (k >> 47)) * M ^ h) * M) & MASK
    if n > end:
        h = ((h ^ int.from_bytes(data[end:], "little")) * M) & MASK
    h ^= h >> 47
    h = (h * M) & MASK
    return h ^ (h >> 47)


def of_text(text: str) -> str:
    """Lake's ``Hash.ofText`` as the 16-digit hex its traces write."""
    k = (_murmur64a(text.replace("\r\n", "\n").encode("utf-8"), 11) * _M) & _MASK
    k = (k ^ (k >> 47)) ^ _M
    return "%016x" % (((1723 ^ k) * _M) & _MASK)


# -- a small Lean lexer for headers ------------------------------------------------

_WORD_RE = re.compile(r"(?:«[^»]*»|[^\s()\[\]{}⟨⟩,:;\"«»@`])+")


def _comment_end(s: str, i: int) -> int:
    """Offset after the ``-/`` closing the (nested) comment opened at ``i``."""
    depth, n = 0, len(s)
    while i < n:
        if s.startswith("/-", i):
            depth += 1
            i += 2
        elif s.startswith("-/", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return n


def _string_end(s: str, i: int) -> int:
    i += 1
    while i < len(s) and s[i] != '"':
        i += 2 if s[i] == "\\" else 1
    return min(i + 1, len(s))


def _group_end(s: str, i: int, open_: str, close: str) -> int:
    """Offset after the ``close`` balancing the ``open_`` at ``i`` (strings skipped)."""
    depth, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '"':
            i = _string_end(s, i)
            continue
        if c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _tokens(s: str, i: int, stop: int):
    """``(kind, start, end)``: doc | attr | group | word | punct; comments are skipped."""
    while i < stop:
        c = s[i]
        if c.isspace():
            i += 1
        elif s.startswith("--", i):
            j = s.find("\n", i)
            i = stop if j < 0 else j
        elif s.startswith("/-", i):
            j = _comment_end(s, i)
            if s.startswith("/--", i):
                yield "doc", i, j
            i = j
        elif s.startswith("@[", i):
            j = _group_end(s, i + 1, "[", "]")
            yield "attr", i, j
            i = j
        elif c == "(":
            j = _group_end(s, i, "(", ")")
            yield "group", i, j
            i = j
        elif c == '"':
            i = _string_end(s, i)
        else:
            m = _WORD_RE.match(s, i)
            if m and m.end() > i:
                yield "word", i, m.end()
                i = m.end()
            else:
                yield "punct", i, i + 1
                i += 1


def _split_name(name: str) -> list[str]:
    return re.findall(r"(?:«[^»]*»|[^.«»])+", name)


def _name_parts(name: str) -> list[str]:
    parts = re.findall(r"«[^»]*»|[^.«»]+", name)
    return [p[1:-1] if p.startswith("«") else p for p in parts]


# -- one source file's text ----------------------------------------------------------

class _Src:
    def __init__(self, text: str):
        self.text = text
        self.lines = text.split("\n")
        self.starts = [0]
        for ln in self.lines[:-1]:
            self.starts.append(self.starts[-1] + len(ln) + 1)

    def at_utf16(self, line: int, col: int) -> int:
        """Offset of an LSP position (UTF-16 column); literate positions count code points instead."""
        ln = self.lines[line]
        if ln.isascii() or max(map(ord, ln)) <= 0xFFFF:
            return self.starts[line] + min(col, len(ln))
        units = 0
        for k, c in enumerate(ln):
            if units >= col:
                return self.starts[line] + k
            units += 2 if ord(c) > 0xFFFF else 1
        return self.starts[line] + len(ln)

    def at(self, line: int, col: int) -> int:
        return self.starts[line] + min(col, len(self.lines[line]))

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset) - 1

    def col_of(self, offset: int) -> int:
        return offset - self.starts[self.line_of(offset)]


class _Entry:
    """A declaration found in the build, before its body is cut."""

    __slots__ = ("kind", "name", "full", "kw", "doc", "start", "mods", "private", "origin", "ns", "secs",
                 "doc_line", "doc_text")

    def __init__(self, kind, name, full, kw, doc, start, mods, private, origin="lean"):
        self.kind, self.name, self.full, self.kw, self.doc = kind, name, full, kw, doc
        self.start, self.mods, self.private, self.origin = start, mods, private, origin
        self.ns: str | None = None
        self.secs: tuple[str, ...] | None = None
        self.doc_line: int | None = None
        self.doc_text: str | None = None


def _keyword(src: _Src, toks: list, kinds: tuple[str, ...]):
    """``(kind, keyword start, modifiers)`` when the header's words end with a declaration keyword."""
    words = [t for t in toks if t[0] in ("word", "group", "punct")]
    if (len(words) >= 2 and words[-1][0] == "group" and src.text[words[-2][1]:words[-2][2]] == "instance"
            and src.text[words[-1][1] + 1:words[-1][2]].lstrip().startswith("priority")):
        words.pop()
    if not words or words[-1][0] != "word":
        return None
    kw = src.text[words[-1][1]:words[-1][2]]
    start = words[-1][1]
    words.pop()
    if kw == "inductive" and words and src.text[words[-1][1]:words[-1][2]] == "class" and "class" in kinds:
        kw, start = "class", words.pop()[1]
    head = [src.text[a:b] if k == "word" else None for k, a, b in words]
    if kw not in kinds or not all(w in _HEADER_WORDS for w in head):
        return None
    return kw, start, tuple(w for w in head if w in leanparse._MODIFIERS)


def _doc(src: _Src, tok) -> tuple[int, str]:
    """``(1-based line, text)`` of a ``/-- -/`` token, normalised as leanparse does."""
    text = re.sub(r"^\s*/--", "", src.text[tok[1]:tok[2]])
    return src.line_of(tok[1]) + 1, re.sub(r"-/\s*$", "", text).strip()


# -- the build -------------------------------------------------------------------------

class _Unusable(Exception):
    """Build data that cannot be used for a file; the message is the reason."""


def _load_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _Unusable(f"no {what}") from None
    except (OSError, UnicodeDecodeError, ValueError):
        raise _Unusable(f"unreadable {what}") from None


class Build:
    """A companion's Lake build, read as data; one per repository."""

    def __init__(self, lib: Path, literate: Path | None):
        self.lib = lib
        self.literate = literate
        #: rel -> (origin, fallback reason, literate reason)
        self.files: dict[str, tuple[str, str | None, str | None]] = {}
        self._cache: dict[tuple, tuple] = {}
        self._hashes: dict[tuple, str] = {}

    @classmethod
    def find(cls, checkout: str | Path, lib_dir: str | Path | None = None) -> "Build | None":
        """The checkout's build (``lib_dir``: its ``lib/lean``), or None without one or with CF_LEAN_SOURCE=text."""
        if os.environ.get(ENV, "").strip().lower() == "text":
            return None
        lib = Path(lib_dir) if lib_dir is not None else Path(checkout) / ".lake" / "build" / "lib" / "lean"
        if not lib.is_dir():
            return None
        literate = lib.parent.parent / "literate" if lib.parts[-2:] == ("lib", "lean") else None
        return cls(lib, literate)

    # -- verification ------------------------------------------------------------
    def _hash_file(self, path: Path) -> tuple[str, str]:
        """``(text, of_text(text))``, the hash cached by the file's size and mtime."""
        text = path.read_text(encoding="utf-8")
        st = path.stat()
        key = (str(path), st.st_size, st.st_mtime_ns)
        if key not in self._hashes:
            self._hashes[key] = of_text(text)
        return text, self._hashes[key]

    def _trace(self, rel: str) -> tuple[Path, dict, str]:
        """``(module path stem, trace, source hash)`` for the module built from ``rel``."""
        suffix = "/" + Path(rel).as_posix()
        found = False
        for stem in _stems(self.lib, rel):
            path = stem.with_name(stem.name + ".trace")
            if not path.is_file():
                continue
            found = True
            trace = _load_json(path, "trace")
            try:
                hashes = [h for label, h in trace["inputs"]
                          if isinstance(h, str) and isinstance(label, str) and label.endswith(suffix)]
            except (TypeError, ValueError, KeyError):
                raise _Unusable("unexpected trace format") from None
            if len(hashes) == 1:
                return stem, trace, hashes[0]
        raise _Unusable("trace names another source" if found else "no build data")

    def _verify(self, rel: str, h_text: str):
        stem, trace, h_src = self._trace(rel)
        if h_text != h_src.lower():
            raise _Unusable("source changed since the build")
        try:
            out_i, out_o = trace["outputs"]["i"], trace["outputs"]["o"][0]
            assert isinstance(out_i, str) and out_i.endswith(".ilean") and isinstance(out_o, str)
        except (TypeError, KeyError, IndexError, AssertionError):
            raise _Unusable("unexpected trace format") from None
        ilean_path = stem.with_name(stem.name + ".ilean")
        try:
            ilean_text, ilean_hash = self._hash_file(ilean_path)
        except FileNotFoundError:
            raise _Unusable("no .ilean") from None
        except (OSError, UnicodeDecodeError):
            raise _Unusable("unreadable .ilean") from None
        if ilean_hash != out_i[:-len(".ilean")].lower():
            raise _Unusable(".ilean does not match its trace")
        try:
            ilean = json.loads(ilean_text)
            module = ilean["module"]
            decls = [(str(n), [int(x) for x in r]) for n, r in ilean["decls"].items()]
            assert isinstance(module, str) and all(len(r) == 8 for _n, r in decls)
        except (ValueError, TypeError, KeyError, AttributeError, AssertionError):
            raise _Unusable("unexpected .ilean format") from None
        items, lit_reason = None, None
        try:
            items = self._literate(stem, module, out_o.split(".", 1)[0].lower())
        except _Unusable as exc:
            lit_reason = str(exc)
        return module, decls, items, lit_reason

    def _literate(self, stem: Path, module: str, olean_hash: str) -> list[tuple[str, tuple, tuple, list]]:
        if self.literate is None:
            raise _Unusable("no literate")
        base = self.literate.joinpath(*stem.relative_to(self.lib).parts)
        path = base.with_name(base.name + ".json")
        if not path.is_file():
            raise _Unusable("no literate")
        trace = _load_json(base.with_name(base.name + ".json.trace"), "literate trace")
        try:
            olean = [h for label, h in trace["inputs"] if label == f"{module}:olean"]
            same = len(olean) == 1 and str(olean[0][0][1]).lower() == olean_hash
        except (ValueError, TypeError, KeyError, IndexError):
            raise _Unusable("unexpected literate format") from None
        if not same:   # checked before hashing the (large) JSON
            raise _Unusable("literate built from another .olean")
        try:
            text, h = self._hash_file(path)
            want = base.with_name(base.name + ".json.hash").read_text(encoding="utf-8").strip().lower()
        except FileNotFoundError:
            raise _Unusable("no literate") from None
        except (OSError, UnicodeDecodeError):
            raise _Unusable("unreadable literate") from None
        if h != want:
            raise _Unusable("literate does not match its hash")
        try:
            out = []
            for it in json.loads(text)["items"]["items"]:
                if it["range"] is None:      # the header of a module without imports
                    continue
                a, b = it["range"]
                out.append((str(it["kind"]), (int(a["line"]) - 1, int(a["column"])),
                            (int(b["line"]) - 1, int(b["column"])), [str(d) for d in it["defines"]]))
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise _Unusable("unexpected literate format") from None
        return out

    # -- declarations ------------------------------------------------------------
    def decls(self, path: Path, rel: str, text: str, kinds: tuple[str, ...],
              max_body_lines: int) -> list[Decl] | None:
        """The file's declarations from the build, or None (recorded with the reason) to fall back."""
        try:
            key = (rel, of_text(text))
            got = self._cache.get(key)
            if got is None:
                got = self._cache[key] = self._verify(*key)
            module, ilean, items, lit_reason = got
            out = _build_decls(path, rel, text, kinds, max_body_lines, module, ilean, items)
        except _Unusable as exc:
            self.files[rel] = ("text", str(exc), None)
            return None
        except Exception as exc:  # noqa: BLE001 - build data must never break a reader
            self.files[rel] = ("text", f"unexpected build data ({exc.__class__.__name__})", None)
            return None
        self.files[rel] = ("lean", None, lit_reason)
        return out

    def tally(self) -> dict:
        """Files by origin, why each text file fell back, and why a verified file had no literate data."""
        out: dict = dict(Counter(o for o, _r, _l in self.files.values()))
        reasons = Counter(r for _o, r, _l in self.files.values() if r)
        if reasons:
            out["fallback"] = dict(sorted(reasons.items()))
        lit = Counter(lr for o, _r, lr in self.files.values() if o == "lean" and lr)
        if lit:
            out["without_literate"] = dict(sorted(lit.items()))
        return out


def tally(build: Build | None) -> dict:
    """:meth:`Build.tally`, or why no build was read."""
    if build is not None:
        return build.tally()
    forced = os.environ.get(ENV, "").strip().lower() == "text"
    return {"build": f"not read ({ENV}=text)" if forced else "none"}


def _stems(lib: Path, rel: str):
    """Candidate module paths under ``lib`` for source ``rel`` (its source directory may be a subdirectory)."""
    parts = Path(rel).with_suffix("").parts
    return [lib.joinpath(*parts[k:]) for k in range(len(parts))]


def fingerprint(build: Build | None, paths, root: Path) -> str:
    """A digest of the build records that decide each file's origin (for extraction keys)."""
    if build is None:
        return "text"
    h = hashlib.sha256()
    for p in sorted(paths):
        rel = Path(p).relative_to(root).as_posix()
        h.update(rel.encode() + b"\0")
        for stem in _stems(build.lib, rel):
            files = [stem.with_name(stem.name + ".trace"), stem.with_name(stem.name + ".ilean")]
            if build.literate is not None:
                lit = build.literate.joinpath(*stem.relative_to(build.lib).parts)
                files += [lit.with_name(lit.name + ".json.hash"), lit.with_name(lit.name + ".json.trace")]
            for f in files:
                try:
                    h.update(hashlib.sha256(f.read_bytes()).digest())
                except OSError:
                    h.update(b"-")
    return h.hexdigest()


def _scope_name(src: _Src, start: int, stop: int, word: str) -> str | None:
    """The name a ``namespace``/``section``/``end`` command in ``[start, stop)`` gives ("" for none), else None."""
    seen = None
    for k, a, b in _tokens(src.text, start, stop):
        w = src.text[a:b] if k == "word" else None
        if seen is not None:
            return w or ""
        if k == "attr" or w in _HEADER_WORDS:
            continue
        if w != word:
            return None
        seen = w
    return "" if seen is not None else None


def _bounds(src: _Src, items: list) -> list[tuple[int, int]]:
    """Each literate item's ``[start, stop)`` offsets; a range end past the next item's start is cut there."""
    out = []
    for n, (_kind, a, b, _defines) in enumerate(items):
        start = src.at(*a)
        nxt = src.at(*items[n + 1][1]) if n + 1 < len(items) else len(src.text)
        stop = src.at(*b)
        out.append((start, min(stop, nxt) if stop > start else nxt))
    return out


def _scopes(src: _Src, items: list, bounds: list) -> list[tuple[str, tuple[str, ...]]]:
    """``(namespace, named sections)`` in force at each literate item."""
    stack: list[tuple[str, str | None]] = []   # (namespace component or "", section name or None)
    out = []
    for (kind, _a, _b, _defines), (start, stop) in zip(items, bounds):
        out.append((".".join(c for c, _s in stack if c), tuple(s for _c, s in stack if s)))
        last = kind.rsplit(".", 1)[-1].lower()
        if last == "namespace":
            name = _scope_name(src, start, stop, "namespace") or ""
            stack += [(c, None) for c in name.split(".") if c]
        elif last.endswith("section"):
            name = _scope_name(src, start, stop, "section") or ""
            stack += [("", None)] * name.count(".") + [("", name or None)]
        elif last == "end":
            name = _scope_name(src, start, stop, "end") or ""
            del stack[max(0, len(stack) - (name.count(".") + 1)):]
    return out


def _example_head(src: _Src, start: int, stop: int) -> list:
    """Tokens of the command at ``start`` up to its keyword, past any ``open ... in`` style prefixes."""
    head: list = []
    skipping = False
    for t in _tokens(src.text, start, stop):
        w = src.text[t[1]:t[2]] if t[0] == "word" else None
        if skipping:
            skipping = w != "in"
            continue
        if not head and w in _IN_PREFIXES:
            skipping = True
            continue
        head.append(t)
        if not (t[0] == "attr" or w in _HEADER_WORDS or (t[0] == "doc" and len(head) == 1)):
            break
    return head


def _build_decls(path: Path, rel: str, text: str, kinds: tuple[str, ...], max_body_lines: int,
                 module: str, ilean: list, items: list | None) -> list[Decl]:
    src = _Src(text)
    entries: list[_Entry] = []
    mangled = f"_private.{module}.0."
    for full, (l0, c0, _l1, _c1, s0, sc0, s1, sc1) in ilean:
        if max(l0, s0, s1) >= len(src.lines):
            raise _Unusable(".ilean positions outside the source")
        start, sel_a, sel_b = src.at_utf16(l0, c0), src.at_utf16(s0, sc0), src.at_utf16(s1, sc1)
        private = full.startswith("_private.")
        if private:
            full = full[len(mangled):] if full.startswith(mangled) else re.sub(r"^_private\..*?\.0\.", "", full)
        written = text[sel_a:sel_b]
        toks = list(_tokens(text, start, sel_a))
        if written == "instance":
            got = _keyword(src, toks + [("word", sel_a, sel_b)], kinds)
            if got is None or got[0] != "instance":
                continue
            name = None
        else:
            want = _name_parts(written[len("_root_."):] if written.startswith("_root_.") else written)
            if not want or _name_parts(full)[-len(want):] != want:
                continue
            got = _keyword(src, toks, kinds)
            if got is None:
                continue
            name = written
        doc = toks[0] if toks and toks[0][0] == "doc" else None
        entries.append(_Entry(got[0], name, full, got[1], doc, start, got[2], private))
    text_decls: list[Decl] = []
    if items is not None:
        bounds = _bounds(src, items)
        for (_kind, _a, _b, defines), (start, stop) in zip(items, bounds):
            if defines:
                continue
            head = _example_head(src, start, stop)
            got = _keyword(src, head, kinds) if head else None
            if got is None or got[0] != "example":
                continue
            doc = head[0] if head[0][0] == "doc" else None
            entries.append(_Entry("example", None, None, got[1], doc, head[0][1], got[2], "private" in got[2]))
        scopes = _scopes(src, items, bounds)
        starts = [a for a, _b in bounds]
        for e in entries:
            k = bisect.bisect_right(starts, e.kw) - 1
            e.ns, e.secs = scopes[k] if k >= 0 else ("", ())
    else:
        text_decls = _text_pass(path, rel, text, kinds, max_body_lines)
        by_line = {d.line: d for d in text_decls}
        for e in entries:
            d = by_line.get(src.line_of(e.kw) + 1)
            derived = None
            if e.full is not None and not (e.name or "").startswith("_root_."):
                derived = ".".join(_split_name(e.full)[:-len(_split_name(e.name)) if e.name else -1])
            # leanparse's block tracking can be wrong (e.g. after `mutual ... end`); Lean's name is not
            e.ns = d.namespace if d is not None and derived in (None, d.namespace) else (derived or "")
            e.secs = d.sections if d is not None else ()
    for e in entries:
        e.doc_line, e.doc_text = _doc(src, e.doc) if e.doc is not None else (None, None)
    for d in text_decls:
        if d.anonymous and d.kind == "example":
            kw = src.at(d.line - 1, 0)
            e = _Entry("example", None, None, kw, None, src.at(d.doc_line - 1, 0) if d.doc_line else kw,
                       d.modifiers, d.private, "text")
            e.ns, e.secs, e.doc_line, e.doc_text = d.namespace, d.sections, d.doc_line, d.doc
            entries.append(e)
    entries.sort(key=lambda e: e.kw)
    return _cut(src, rel, entries, max_body_lines)


def _cut_line(lines: list[str], depths: list[int], i: int, end: int) -> int:
    """leanparse's early stop: a depth-0 ``end``/``namespace``/``/-!`` line between ``i`` and ``end``."""
    for j in range(i + 1, end):
        if depths[j] == 0 and (leanparse._END_RE.match(lines[j]) or leanparse._NAMESPACE_RE.match(lines[j])
                               or leanparse._SECTION_DOC_RE.match(lines[j])):
            return j
    return end


def _cut(src: _Src, rel: str, entries: list[_Entry], max_body_lines: int) -> list[Decl]:
    """Decls with leanparse's body rule over the merged declaration starts."""
    lines = src.lines
    depths = leanparse.comment_depths(lines)
    out: list[Decl] = []
    for n, e in enumerate(entries):
        i = src.line_of(e.kw)
        begin = src.starts[i]
        if n and src.line_of(entries[n - 1].kw) == i and src.line_of(e.start) == i:
            begin = e.start                          # a second declaration on one line starts at its own text
        if n + 1 < len(entries) and src.line_of(entries[n + 1].start) == i:
            stop = entries[n + 1].start
        else:
            nxt = entries[n + 1] if n + 1 < len(entries) else None
            end = len(lines) if nxt is None else (nxt.doc_line - 1 if nxt.doc_line else src.line_of(nxt.kw))
            end = _cut_line(lines, depths, i, end)
            stop = len(src.text) if end >= len(lines) else (src.starts[end] - 1 if end > i else begin)
        body = src.text[begin:max(begin, stop)]
        line = i + 1
        anonymous = e.name is None
        out.append(Decl(
            file=rel, line=line, kind=e.kind, name=e.name,
            full_name=f"{e.kind}@{rel}:{line}" if anonymous else e.full,
            namespace=e.ns or "", sections=tuple(e.secs or ()), doc=e.doc_text, doc_line=e.doc_line, body=body,
            def_body=leanparse._body_text(body, max_body_lines) if e.kind in DEFINITION_KINDS else None,
            private=e.private, protected="protected" in e.mods, anonymous=anonymous,
            modifiers=e.mods, origin=e.origin,
        ))
    return out


def _text_pass(path: Path, rel: str, text: str, kinds: tuple[str, ...], max_body_lines: int) -> list[Decl]:
    """leanparse over exactly ``text`` (the file is re-read to confirm it is still that text)."""
    decls = leanparse.parse_file(path, rel=rel, decl_kinds=kinds, max_body_lines=max_body_lines)
    try:
        same = Path(path).read_text(encoding="utf-8") == text
    except (OSError, UnicodeDecodeError):
        same = False
    if same:
        return decls
    with tempfile.TemporaryDirectory(prefix="cf-leanbuild-") as tmp:
        f = Path(tmp) / Path(rel).name
        f.write_text(text, encoding="utf-8")
        return leanparse.parse_file(f, rel=rel, decl_kinds=kinds, max_body_lines=max_body_lines)


def parse_file(path: str | Path, rel: str | None = None,
               decl_kinds: tuple[str, ...] | list[str] = DEFAULT_DECL_KINDS, max_body_lines: int = 40,
               build: Build | None = None, text: str | None = None) -> list[Decl]:
    """:func:`adapters.leanparse.parse_file`, from ``build`` when it verifies this file's text."""
    p = Path(path)
    rel = rel if rel is not None else p.name
    kinds = tuple(decl_kinds)
    if build is None and text is None:
        return leanparse.parse_file(p, rel=rel, decl_kinds=kinds, max_body_lines=max_body_lines)
    if text is None:
        text = p.read_text(encoding="utf-8")
    if build is not None:
        got = build.decls(p, rel, text, kinds, max_body_lines)
        if got is not None:
            return got
    return _text_pass(p, rel, text, kinds, max_body_lines)
