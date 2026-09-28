"""The text layer of a PDF, for locating headings and verifying quotes.

New code, ported from the docstring-tags prototype exporter of
docs/annotations.md section 10.  ``pdftotext`` gives one text per physical
page; every match is made under the text layer the document declares
(``text_layer``: NFKC, whitespace collapsed, case kept, docs/annotations.md
section 4.6), and a ``TextQuoteSelector`` is written only for a run that occurs
exactly once on its page, so a consumer can re-anchor it mechanically.

:class:`PdfText` takes the page texts directly, so a test can hand it strings;
:meth:`PdfText.extract` runs ``pdftotext`` (and ``pdfinfo`` for the page box).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

#: a prose run shorter than this is not a usable anchor
QUOTE_MIN_CHARS = 12
#: prefix and suffix length on a TextQuoteSelector
CONTEXT_CHARS = 32

_QUOTE_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                             "–": "-", "—": "-", "−": "-", " ": " "})


def normalise_with_map(text: str) -> tuple[str, list[int]]:
    """NFKC per character, whitespace collapsed, quotes and dashes folded; with an index map back."""
    out: list[str] = []
    back: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        norm = unicodedata.normalize("NFKC", ch).translate(_QUOTE_FOLD)
        for c in norm:
            if c.isspace():
                if prev_space:
                    continue
                out.append(" ")
                back.append(i)
                prev_space = True
            else:
                out.append(c)
                back.append(i)
                prev_space = False
    return "".join(out), back


def collapse_ws(text: str) -> str:
    """The declared text layer: NFKC, each run of whitespace one space, ends stripped."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def quoted_runs(doc: str | None) -> list[str]:
    """Prose runs of the double-quoted passages of a docstring, longest first.

    Backticked Lean inside a quote is the companion's rendering of the book's
    formula, never the book's text, so it splits the quote into runs.
    """
    runs: list[str] = []
    for q in re.findall(r"[\"“]([^\"”]{%d,})[\"”]" % QUOTE_MIN_CHARS, doc or ""):
        for part in re.split(r"`[^`]*`|…|\.\.\.", q):
            part = re.sub(r"\s+", " ", part).strip(" ,;:")
            if len(part) >= QUOTE_MIN_CHARS and len(re.findall(r"[A-Za-z]{2,}", part)) >= 2:
                runs.append(part)
    return sorted(set(runs), key=lambda s: (-len(s), s))


class PdfText:
    """The page texts of one PDF and the matchers over them."""

    def __init__(self, pages: list[str], version: str = "unknown",
                 page_size_pt: tuple[float, float] | None = None):
        self.pages = pages                                   # index 0 = physical page 1
        self.norm = [normalise_with_map(p) for p in pages]
        self.version = version
        self.page_size_pt = page_size_pt
        self.last_miss: str | None = None

    @classmethod
    def extract(cls, path: Path) -> "PdfText":
        """Run ``pdftotext`` over ``path``; read the page box from ``pdfinfo`` when it is on PATH."""
        if shutil.which("pdftotext") is None:
            raise RuntimeError("pdftotext is not on PATH (install poppler)")
        raw = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], check=True,
                             capture_output=True).stdout.decode("utf-8")
        pages = raw.split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        ver = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True)
        m = re.search(r"pdftotext version (\S+)", ver.stdout + ver.stderr)
        version = m.group(1) if m else "unknown"
        size = None
        if shutil.which("pdfinfo") is not None:
            info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True)
            sm = re.search(r"Page size:\s+([\d.]+) x ([\d.]+) pts", info.stdout)
            if sm:
                size = (float(sm.group(1)), float(sm.group(2)))
        return cls(pages, version, size)

    def page_count(self) -> int:
        return len(self.pages)

    def headings(self, kind: str, number: str) -> list[tuple[int, int, int]]:
        """``(physical page, offset, length)`` of every line that OPENS with ``<kind> <number>``
        followed by a title parenthesis or a full stop: the item's own heading shape."""
        pat = re.compile(rf"(?m)^{re.escape(kind)}\s+{re.escape(number)}(?=\s+\(|\.(?:\s|$))")
        return [(i + 1, m.start(), m.end() - m.start()) for i, page in enumerate(self.pages)
                for m in pat.finditer(page)]

    def bare_headings(self, number: str) -> list[tuple[int, int, int]]:
        """Lines opening with ``<number>. `` and no kind word (how a book prints a bare strategy)."""
        pat = re.compile(rf"(?m)^{re.escape(number)}\.(?=\s)")
        return [(i + 1, m.start(), m.end() - m.start()) for i, page in enumerate(self.pages)
                for m in pat.finditer(page)]

    def heading_title(self, physical: int, end: int) -> str | None:
        """The parenthesised title that follows a heading label, if any (balanced, at most 160 characters)."""
        page = self.pages[physical - 1]
        i = end
        while i < len(page) and page[i] in " \t\n":
            i += 1
        if i >= len(page) or page[i] != "(":
            return None
        depth = 0
        for j in range(i, min(len(page), i + 200)):
            if page[j] == "(":
                depth += 1
            elif page[j] == ")":
                depth -= 1
                if depth == 0:
                    title = collapse_ws(page[i + 1:j])
                    return title if 0 < len(title) <= 160 else None
        return None

    def quote_at(self, physical: int, start: int, length: int) -> dict:
        """TextQuoteSelector keys for a raw span of one page, in the declared text layer."""
        page = self.pages[physical - 1]
        exact = collapse_ws(page[start:start + length])
        prefix = collapse_ws(page[max(0, start - 3 * CONTEXT_CHARS):start])[-CONTEXT_CHARS:]
        suffix = collapse_ws(page[start + length:start + length + 3 * CONTEXT_CHARS])[:CONTEXT_CHARS]
        out = {"type": "TextQuoteSelector", "exact": exact}
        if prefix:
            out["prefix"] = prefix + (" " if page[start - 1:start].isspace() else "")
        if suffix:
            out["suffix"] = (" " if page[start + length:start + length + 1].isspace() else "") + suffix
        return out

    def find_prose(self, physical: int, candidate: str) -> dict | None:
        """A TextQuoteSelector when ``candidate`` occurs exactly once on the page, else None.

        ``last_miss`` says why not: ``absent`` or ``ambiguous``.
        """
        if not (1 <= physical <= len(self.pages)):
            return None
        needle, _ = normalise_with_map(candidate)
        needle = needle.strip()
        if len(needle) < QUOTE_MIN_CHARS:
            return None
        hay, back = self.norm[physical - 1]
        first = hay.find(needle)
        if first < 0 or hay.find(needle, first + 1) >= 0:
            self.last_miss = "absent" if first < 0 else "ambiguous"
            return None
        raw_start = back[first]
        raw_end = back[first + len(needle) - 1] + 1
        return self.quote_at(physical, raw_start, raw_end - raw_start)

    def first_verified(self, physicals, runs) -> tuple[int, dict] | None:
        """The first (page, quote) over the candidate pages and runs that verifies."""
        for p in physicals:
            for run in runs:
                q = self.find_prose(p, run)
                if q:
                    return p, q
        return None
