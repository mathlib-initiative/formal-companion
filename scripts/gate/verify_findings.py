#!/usr/bin/env python3
"""The mechanical findings gate (schema v2), driven by ``gate.json``.

Ported from ``ffmain:~/tao-diff-run/scripts/verify_findings.py`` (md5 ca976440,
the three differential entrypoints) and ``ffmain:~/tao-attack-vac/scripts/
verify_findings.py`` (md5 f4cc21a8, the two attack entrypoints). Those two files
differ only in the ``KINDS``/``VERDICTS`` constants; everything they hard-wired —
book path, repo list, kind and verdict sets — is now read from ``gate.json``.

Three things changed on purpose, per the design critique:

1. **It runs out of tree.** The original lived at ``scripts/verify_findings.py``
   *inside the worktree the worker commits to*, and both the validation and the
   pre-merge validation ran ``python3 scripts/verify_findings.py`` from that
   worktree — a worker that committed an edited gate passed both. This file is
   copied to ``<tend-root>/gate/`` and invoked by absolute path.
2. **Per-kind rules.** CONTENT/VACUITY need a quote and (in attack mode) a
   witness object; SUSPECTED_ERRATA needs a quote; COVERAGE needs neither;
   PROXY needs bridge + closure evidence; PAGE_ANCHOR is mechanical-only.
3. **Source binding.** Every findings file carries ``source_id`` and
   ``source_sha``, and both must be one this run pinned; batch 1's anchor
   findings were judged against the 2011 draft.  ``gate.json`` carries a MAP of
   sources and the document's ``source_id`` chooses which text its quotes are
   matched against, so a run spanning two editions cannot launder a quote from
   one as a quote from the other.

Deliberately stdlib-only and self-contained (no ``cflib`` import): it is rsynced
to a server and must run there with nothing installed.

Exit status (errors on stderr):

* 0  every file validated;
* 1  the findings were REJECTED — fix them;
* 3  the gate itself could not run (no gate.json, unreadable source text, a
  ``--source-text`` that names no source of this gate, a missing base ref for the
  changed-files check).  A worker must answer this by stopping and reporting
  blocked, never by editing its findings: an infrastructure failure used to be
  indistinguishable from "your quotes are wrong", and the plausible response to a
  gate that cannot be satisfied is to delete findings until it passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import unicodedata
from typing import Any, Mapping

#: Files that live beside findings in a corpus directory and are NOT findings.
#: ``cf harvest`` and ``cf import-corpus`` write RECEIPT.json into the findings
#: directory, so a directory-mode re-gate of a landed corpus used to report
#: eight fabricated failures against it.  Duplicated from
#: ``scripts/harvest/_compat.py`` (NON_FINDING_FILES) on purpose: this file runs
#: on a server with no cflib and no harvest package on the path.  Keep in sync.
NON_FINDING_FILES = {
    "RECEIPT.json", "run.yaml", "run.json", "manifest.json",
    "tally.json", "journal-harvest.json", "gate.json", "gate.local.json",
    "queue_remaining.json",
}

#: The exit vocabulary.  ``EXIT_GATE_BROKEN`` is the one that matters: the same
#: command string is handed to every worker and reviewer with "run the validation
#: gate yourself and fix anything it reports", so a mis-rsynced gate or a missing
#: source text must not read to them as an audit verdict.  Anything but 1 means
#: the gate is broken -- stop and report blocked, do not change the findings.
EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_GATE_BROKEN = 3

#: Every key a findings document may carry its defect rows under.  ``harvest/
#: tally.py`` (FINDING_LISTS) and ``ledger/findings.py`` count BOTH of these, so
#: the gate must validate both: a file whose rows sat under ``findings`` was
#: gated on ``disagreements`` alone and counted on the union, which is a complete
#: bypass of the quote gate, the kind and verdict vocabularies, the witness
#: requirement and the repo allowlist, reachable by one key name.  Duplicated
#: rather than imported for the same reason as NON_FINDING_FILES above; the
#: symmetry is enforced by a test.
DEFECT_LISTS = ("disagreements", "findings")

MIN_QUOTE_CHARS = 25
#: the copyright ceiling, the floor's missing other half.  SKILL.md hard rule 6
#: permits "at most a sentence or two of a source in any artifact" and the gate
#: could only ever refuse a quote for being too SHORT.  Measured over the 3,019
#: quotes of the baseline corpus: median 176 raw characters, 90th percentile 329,
#: longest 2,620.  Over ``LONG_QUOTE_CHARS`` the gate RECORDS the quote as
#: over-long (36 baseline quotes, 1.2%, would be named); over ``MAX_QUOTE_CHARS``
#: -- roughly one page of that source, and longer than anything in the baseline --
#: it refuses, because that is a page of the book rather than a passage.  Both are
#: overridable per run from gate.json for a source whose licence note allows more.
LONG_QUOTE_CHARS = 600
MAX_QUOTE_CHARS = 3000
WINDOW_FRACTION = 0.6
MIN_WINDOW_WORDS = 6
#: how many pages the matched text may occur on before the gate refuses it: a
#: running head or a table-of-contents line recurs, a passage does not.  Per run
#: from gate.json (``max_match_pages``) like the two quote-length keys, because a
#: source may have a short sentence it legitimately repeats; 0 turns the refusal
#: off, exactly as ``max_quote_chars: 0`` does.
MAX_MATCH_PAGES = 5
#: how far the page a quote matched on may sit from the page its item is stated
#: on.  An item's statement runs over a page break and pdftotext puts the odd
#: heading on the neighbouring page, so the bind is a window, not an equality:
#: measured over the 2,672-file baseline corpus WITH the source's own page_offset
#: (16), +-2 pages holds for 2,969 of the 2,985 bindable quotes, and the 16
#: outside include the ones quoting a wholly different section (58, 73 and 107
#: pages away), which is the defect the bind exists to catch.  State the offset
#: whenever this figure is quoted: without it only 2,750 of the same quotes can
#: be placed at all, though the same 16 are still outside the window.
PAGE_BIND_WINDOW = 2
#: how many unmatched words the gate prints back when a quote matched only in part
REMAINDER_WORDS = 12
#: how many binding notes the gate prints before summarising the rest: gating the
#: 2,672-file baseline corpus in one go would otherwise print a page per file
MAX_NOTES_PRINTED = 12

DEFAULT_KIND_RULES: dict[str, dict[str, Any]] = {
    "CONTENT": {"quote": True, "witness": True, "decls": True},
    "VACUITY": {"quote": True, "witness": True, "decls": True},
    "SUSPECTED_ERRATA": {"quote": True, "witness": False, "decls": False},
    "COVERAGE": {"quote": False, "witness": False, "decls": False},
    "PROXY": {"quote": False, "witness": False, "decls": True, "bridge": True, "closure": True},
    "PAGE_ANCHOR": {
        "quote": False,
        "witness": False,
        "decls": True,
        "pages": True,
        "mechanical_only": True,
    },
}


# --------------------------------------------------------------------------- #
# quote normalisation — ported verbatim (norm L22-27, quote_ok L29-39)
# --------------------------------------------------------------------------- #
#: mathematical operators the operator pass keeps.  Flipping any of these is the
#: defect class this audit exists to find, and the word pass below deletes them
#: all, so they are checked separately.  The NEGATED and variant glyphs are here
#: for the same reason as the plain ones: with ``\u2209`` missing from this table,
#: doctoring ``x \u2208 E`` into ``x \u2209 E`` normalised to the same token stream as the
#: book and the gate certified it as verbatim.
_OPERATOR_TOKENS: dict[str, str] = {
    "\u2264": "ople", "\u2265": "opge", "<": "oplt", ">": "opgt",
    "=": "opeq", "\u2260": "opne", "+": "opplus", "-": "opminus",
    "\u2208": "opin", "\u2282": "opsub", "\u2286": "opsub",
    "\u222a": "opcup", "\u2229": "opcap", "\u2192": "oparr", "\u00ac": "opnot",
    "\u2209": "opnotin", "\u220c": "opnotni",
    "\u2283": "opsup", "\u2287": "opsup",
    "\u2284": "opnotsub", "\u2285": "opnotsup",
    "\u228a": "opsubne", "\u228b": "opsupne",
    "\u2270": "opnotle", "\u2271": "opnotge", "\u2262": "opnotequiv",
    "\u2200": "opforall", "\u2203": "opexists", "\u2205": "opempty",
}
#: ASCII spellings pdftotext and Lean sources use for the same operators, and the
#: typographic variants of ``\u2264``/``\u2265`` (a quote spelled with ``\u2a7d`` must not be able to
#: mean something else from the book's ``\u2264``).
_OPERATOR_DIGRAPHS: tuple[tuple[str, str], ...] = (
    ("<=", "\u2264"), (">=", "\u2265"), ("!=", "\u2260"),
    ("/=", "\u2260"), ("->", "\u2192"),
    ("\u2266", "\u2264"), ("\u2a7d", "\u2264"),
    ("\u2267", "\u2265"), ("\u2a7e", "\u2265"),
)
#: the token names above, for splitting a normalised stream back into words and
#: operators, and one representative glyph per name for the error messages.
_OPERATOR_NAMES: frozenset[str] = frozenset(_OPERATOR_TOKENS.values())
_OPERATOR_GLYPH: dict[str, str] = {}
for _glyph, _name in _OPERATOR_TOKENS.items():
    _OPERATOR_GLYPH.setdefault(_name, _glyph)


def _fold(s: str) -> str:
    """The shared prefix of both normalisations: NFKC, ligatures, one dash."""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\ufb01", "fi").replace("\ufb02", "fl")
    s = s.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"[\u2212\u2013\u2014]", "-", s)


def norm(s: str) -> str:
    """Normalise text for quote comparison: pdftotext mangles mathematics.

    LOSSY BY DESIGN and therefore blind by design: every non-alphanumeric — so
    every ``=``, ``\u2264``, ``\u2208``, ``\u222a`` — becomes a space, because a
    pdftotext extraction spells the same formula a dozen ways.  A quote whose
    operators have been flipped is word-identical to the real one under this
    normalisation, which is why :func:`norm_operators` exists and why
    ``check_operators`` is on for every non-legacy preset.
    """
    s = _fold(s)
    s = re.sub(r"[^0-9A-Za-z]+", " ", s)  # compare on words, not symbols
    return re.sub(r"\s+", " ", s).strip().lower()


def norm_operators(s: str) -> str:
    """Like :func:`norm`, but the operators survive as words.

    ``a \u2264 b`` becomes ``a ople b``, so an operator keeps its position in the
    token stream and a flipped ``=`` \u2192 ``\u2260`` no longer matches the book.
    """
    s = _fold(s)
    for ascii_form, unicode_form in _OPERATOR_DIGRAPHS:
        s = s.replace(ascii_form, unicode_form)
    for ch, token in _OPERATOR_TOKENS.items():
        s = s.replace(ch, " " + token + " ")
    s = re.sub(r"[^0-9A-Za-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def has_operators(q: str) -> bool:
    """True when the raw quote contains one of the operators worth checking.

    NOT a gate on the operator pass, and it must never become one again: a quote
    with the book's ``≤`` *deleted* carries no operator, so skipping the pass for
    it certified exactly the doctoring the pass exists to catch.  The pass now
    runs whenever the book was loaded with an operator normalisation.
    """
    folded = _fold(q)
    return any(ch in folded for ch in _OPERATOR_TOKENS) or any(
        a in folded for a, _ in _OPERATOR_DIGRAPHS
    )


def _split_operators(stream: list[str]) -> tuple[list[str], list[list[str]]]:
    """Split a :func:`norm_operators` token stream into words and operators.

    Returns ``(words, gaps)`` where ``gaps[j]`` holds the operator tokens that
    sit immediately before ``words[j]`` and ``gaps[-1]`` the trailing ones.  The
    words are exactly :func:`norm`'s tokens (both normalisations delete the same
    characters; only the operators differ), which is what lets the operator pass
    be anchored to the window the word pass matched.
    """
    words: list[str] = []
    gaps: list[list[str]] = [[]]
    for token in stream:
        if token in _OPERATOR_NAMES:
            gaps[-1].append(token)
        else:
            words.append(token)
            gaps.append([])
    return words, gaps


def _with_operators(words: list[str], gaps: list[list[str]], start: int, length: int,
                    *, edges: bool = False) -> str:
    """``words[start:start+length]`` spelled with the operators between them.

    ``edges`` also takes the operators BEFORE the first word and AFTER the last.
    Only the whole-quote (verbatim) window may ask for them: there the edges are
    the quote's own boundary, so an operator prepended or appended to an otherwise
    verbatim quote (``m(E) <= m(F) ≠``) has to occur in the book too.  Inside a
    partial window the edge operators belong to the unmatched remainder, which has
    no book span to compare against, so they stay out.
    """
    out: list[str] = []
    if edges:
        out.extend(gaps[start])
    for j in range(start, start + length):
        if j > start:
            out.extend(gaps[j])
        out.append(words[j])
    if edges:
        out.extend(gaps[start + length])
    return " ".join(out)


def _glyphs(tokens: list[str]) -> str:
    """Operator tokens as glyphs, for an error a human can act on."""
    return " ".join(_OPERATOR_GLYPH.get(t, t) for t in tokens) or "(none)"


def _book_operators(ops: str, words: list[str]) -> list[str] | None:
    """The operators the book carries inside its first occurrence of ``words``.

    Only used to word the refusal: the pass itself is a containment test.  The
    pattern lets any operator token sit between two words, so the span found is
    the book's spelling of the same words.
    """
    if not words:
        return None
    pattern = r"(?:\s+op[a-z]+)*\s+".join(re.escape(w) for w in words)
    m = re.search(pattern, ops)
    if m is None:
        return None
    return [t for t in m.group(0).split() if t in _OPERATOR_NAMES]


def _operator_problem(bk: "BookText", q: str, words: list[str], start: int,
                      length: int) -> str:
    """``""`` when the matched window carries the book's operators, else why not.

    The window the word pass matched, spelled WITH the quote's own operators,
    must occur in the book's operator normalisation.  Anchoring it to that window
    is the whole point: the old pass ran an independent 50%-window search over the
    operator stream, so any six operator-free tokens anywhere in the quote
    satisfied it and a single flipped ``≤`` → ``≥`` passed as *verbatim*.
    A verbatim match is checked WITH its edges, so an operator prepended or
    appended to an otherwise verbatim quote is refused too; a partial window is
    not, because its edge operators sit in the unmatched remainder.
    Measured against the baseline corpus's 3,019 quotes: 5 fail this rule
    (pdftotext artifacts in the imported 2011-draft corpus -- a doubled hyphen, a
    sign the extraction dropped), against 2 under the old one, and the edges add
    none of those 5 even though 117 of the quotes carry a boundary operator.  The
    two ``*-legacy`` presets keep ``check_operators`` off for exactly that reason.
    """
    qwords, gaps = _split_operators(norm_operators(q).split())
    if qwords != words:
        # The two normalisations disagreed about the word stream, so the window
        # cannot be anchored.  Refusing here would reject a real quote over a
        # normalisation bug; the word pass still applies.
        return ""
    whole = start == 0 and length == len(qwords)
    if _with_operators(qwords, gaps, start, length, edges=whole) in bk.ops:
        return ""
    carried = [t for j in range(start + 1, start + length) for t in gaps[j]]
    if whole:
        carried = gaps[start] + carried + gaps[start + length]
    book = _book_operators(bk.ops, words[start:start + length])
    if book is None:
        return ("words match the book but their OPERATORS could not be located there "
                "at all (the quote splices two passages together, or its "
                f"mathematics was rewritten): quote has {_glyphs(carried)}")
    if len(book) > len(carried):
        what = "an OPERATOR the book carries has been DROPPED"
    elif len(book) < len(carried):
        what = "the quote carries an OPERATOR the book does not"
    else:
        what = ("an OPERATOR has been CHANGED -- an inequality, equality, "
                "quantifier or set operator is not the book's")
    return f"words match the book but {what}: book has {_glyphs(book)}, quote has {_glyphs(carried)}"


class BookText:
    """The pinned book text in the normalisations the quote gate compares against.

    ``words`` is the lossy :func:`norm` form (the historical one).  ``ops`` is
    the :func:`norm_operators` form, or ``None`` when the caller handed in a
    plain string — in which case the operator pass is skipped and says so.
    ``pages`` is the SAME text as one :func:`norm` form per page, so a match can
    be bound to a page; ``None`` means the caller handed in a flat string and no
    quote can be bound, which the gate records rather than passing over.
    """

    def __init__(self, words: str, ops: str | None = None,
                 pages: list[str] | None = None):
        self.words = words
        self.ops = ops
        self.pages = pages
        #: item label -> the pages carrying it.  A corpus gate asks for the same
        #: label once per findings file per run directory, and the search is over
        #: every page of the source.
        self._label_pages: dict[str, list[int]] = {}

    @classmethod
    def from_text(cls, raw: str, *, operators: bool = True) -> "BookText":
        """Load one source's pinned text, page by page.

        ``pdftotext`` writes a form feed between pages and ``norm`` deletes it,
        so the flat string the word pass searches cannot say WHERE a quote
        matched — which is why a section running head, repeated on every page of
        the section, passed the gate as an item's passage.  Splitting first and
        joining the pages costs nothing and is byte-identical to ``norm(raw)``
        (a form feed always separates two tokens).
        """
        pages = [norm(page) for page in raw.split("\f")]
        return cls(" ".join(p for p in pages if p),
                   norm_operators(raw) if operators else None, pages)

    @classmethod
    def coerce(cls, book: "BookText | str") -> "BookText":
        return book if isinstance(book, BookText) else cls(str(book))

    def pages_matching(self, needle: str) -> list[int]:
        """The 1-based PDF pages carrying ``needle``, or ``[]`` when unknown."""
        if self.pages is None or not needle:
            return []
        return [n for n, page in enumerate(self.pages, 1) if needle in page]

    def pages_with_label(self, needle: str) -> list[int]:
        """The 1-based PDF pages where an item label occurs, cached.

        Not :meth:`pages_matching`: a label must not match the longer number it
        is a prefix of, or ``Exercise 1.3.19`` would be found wherever
        ``Exercise 1.3.190`` is.
        """
        if self.pages is None or not needle:
            return []
        if needle not in self._label_pages:
            pattern = re.compile(r"(?<![0-9])" + re.escape(needle) + r"(?![0-9])")
            self._label_pages[needle] = [n for n, page in enumerate(self.pages, 1)
                                         if pattern.search(page)]
        return self._label_pages[needle]


def _window_match(book: str, words: list[str], fraction: float) -> int:
    """Index of the first >=``fraction`` contiguous window found, else -1."""
    k = max(MIN_WINDOW_WORDS, int(len(words) * fraction))
    for i in range(0, len(words) - k + 1):
        if " ".join(words[i : i + k]) in book:
            return i
    return -1


def unmatched_words(book: str, words: list[str], start: int, length: int) -> list[str]:
    """The quote words OUTSIDE the window that matched — what nobody verified."""
    return words[:start] + words[start + length :]


class QuoteMatch:
    """What the quote gate found for one quote.

    ``pages`` are the PDF pages the matched window occurs on — the evidence the
    page binds in :class:`PageBind` need, and empty when the book was handed in
    as a flat string or the match straddles a page break.
    """

    def __init__(self, ok: bool, why: str, *, needle: str = "",
                 pages: list[int] | None = None, chars: int = 0):
        self.ok = ok
        self.why = why
        self.needle = needle
        self.pages = pages or []
        self.chars = chars

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.ok


def quote_match(book: "BookText | str", q: str, *,
                max_chars: int = MAX_QUOTE_CHARS,
                max_pages: int = MAX_MATCH_PAGES) -> QuoteMatch:
    """Locate ``q`` in the book text: verbatim, or a 60% window.

    Four passes:

    1. the **length** floor and ceiling: below 25 normalised characters nothing
       can be verified, and above ``max_chars`` the "quote" is a page of the book
       rather than a passage (SKILL.md hard rule 6: at most a sentence or two).
    2. the **word** pass (historical): operators deleted, a contiguous window of
       >=60% of the quote's words must occur in the book.  When only a window
       matched, the reason string names the words that were NOT verified —
       previously a quote that was 70% genuine and 30% invented passed with the
       gate saying nothing at all about the invented 30%.
    3. the **operator** pass: when the book was loaded with an operator
       normalisation, the matched window spelled with the quote's own operators
       must occur in it, so a flipped, dropped or added operator is refused.
    4. the **page** pass: a window that occurs on more than ``max_pages`` pages
       (:data:`MAX_MATCH_PAGES`, per run from gate.json; 0 = off) is a running
       head or a contents line, not a passage.  Measured over the baseline
       corpus: the most a real quote recurs is 5 pages, while this source's own
       permission line is on all 265 and its section running heads on 9 to 25.
    """
    bk = BookText.coerce(book)
    n = norm(q)
    chars = len(q or "")
    if len(n) < MIN_QUOTE_CHARS:
        return QuoteMatch(False, f"quote too short to verify "
                                 f"(need >={MIN_QUOTE_CHARS} normalised chars)", chars=chars)
    if max_chars and chars > max_chars:
        return QuoteMatch(False, f"quote is {chars} characters, over the {max_chars}-character "
                                 f"ceiling — quote the passage, not the page", chars=chars)
    w = n.split()
    if n in bk.words:
        needle, start, length = n, 0, len(w)
        why = "verbatim"
    else:
        i = _window_match(bk.words, w, WINDOW_FRACTION)
        if i < 0:
            return QuoteMatch(False, "NOT FOUND in the book text", chars=chars)
        k = max(MIN_WINDOW_WORDS, int(len(w) * WINDOW_FRACTION))
        needle, start, length = " ".join(w[i : i + k]), i, k
        rest = unmatched_words(bk.words, w, i, k)
        shown = " ".join(rest[:REMAINDER_WORDS]) + ("…" if len(rest) > REMAINDER_WORDS else "")
        why = (
            f"partial ({k}/{len(w)} words matched as one window; pdftotext mangling "
            f"tolerated). UNVERIFIED: {shown!r}"
        )
    if bk.ops is not None:
        problem = _operator_problem(bk, q, w, start, length)
        if problem:
            return QuoteMatch(False, problem, needle=needle, chars=chars)
    pages = bk.pages_matching(needle)
    if max_pages and len(pages) > max_pages:
        return QuoteMatch(
            False,
            f"matched text occurs on {len(pages)} pages ({pages[0]}, {pages[1]}, … , "
            f"{pages[-1]}) — a running head or a contents line, not a passage",
            needle=needle, pages=pages, chars=chars)
    return QuoteMatch(True, why, needle=needle, pages=pages, chars=chars)


def quote_ok(book: "BookText | str", q: str) -> tuple[bool, str]:
    """``(ok, why)`` for one quote — :func:`quote_match` without the evidence."""
    m = quote_match(book, q)
    return m.ok, m.why


#: characters pdftotext and LaTeX produce for a hyphen (mirrors cflib.items._DASHES)
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"


def clean_item(raw: str) -> str:
    """Canonicalise an item label before hashing it.

    Byte-for-byte the same transformation as ``cflib.items.clean`` (NFKC, one
    dash, non-breaking space to space, whitespace collapsed, wrapping backticks
    and trailing punctuation stripped).  Duplicated here rather than imported
    because this file is rsynced to a run host with no ``cflib`` on the path;
    ``tests/test_plan_gate.py`` asserts the two stay identical.

    Without it ``Exercise    1.3.19`` and ``Exercise 1.3.19`` -- the whitespace
    variant the baseline lost -- would hash to different finding ids.
    """
    s = unicodedata.normalize("NFKC", raw or "")
    for bad in _DASHES:
        s = s.replace(bad, "-")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip("`").strip()
    return s.rstrip(" .,;:").strip()


def finding_id(run_id: str, source_id: str, item: str, kind: str,
               unfaithful_decls: list[str]) -> str:
    """Stable content address of a finding — the ONE definition.

    ``sha256("\\n".join([run_id, source_id, clean_item(item), kind, *sorted(decls)]))`` —
    the critique rejected the original ``concept``-based id because ``concept``
    is LLM-written prose, so the same defect got a different id in every run.

    This is inlined from :func:`cflib.hashing.finding_id`; the two MUST produce
    identical digests, or the detector's ids and the gate's live in different
    namespaces and no ledger row can ever match a finding.  The gate cannot
    import ``cflib`` (it runs on a server with nothing installed), so equality
    is enforced by a test instead of by sharing code.
    """
    decls = sorted({str(d).strip() for d in (unfaithful_decls or []) if str(d).strip()})
    payload = "\n".join([str(run_id or ""), str(source_id or ""), clean_item(str(item or "")),
                         str(kind or ""), *decls])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# gate configuration
# --------------------------------------------------------------------------- #
class GateConfig:
    """The parsed ``gate.json``; every former hard-wired constant lives here."""

    def __init__(self, data: dict[str, Any], path: pathlib.Path | None = None):
        self.path = path
        self.data = data
        self.mode: str = data.get("mode", "differential")
        self.preset: str = data.get("preset", "")
        self.run_id: str = data.get("run_id", "")
        self.schema_version: int = int(data.get("schema_version", 2))
        # A MAP of sources, not one book: a run may span more than one source,
        # and the text a quote is checked against is chosen by the document's
        # own source_id.  One concatenated text would let a quote from the 2011
        # draft pass as the published book's, which is the exact defect the
        # source gate exists to stop.
        self._sources: dict[str, dict[str, Any]] = {}
        for sid, body in (data.get("sources") or {}).items():
            body = body if isinstance(body, dict) else {"text": body}
            self._sources[str(sid)] = {
                "source_id": str(sid),
                "text": (pathlib.Path(body["text"]).expanduser()
                         if body.get("text") else None),
                "sha256": body.get("sha256"),
                "text_sha256": body.get("text_sha256"),
                "images_dir": body.get("images_dir"),
                "page_offset": int(body.get("page_offset") or 0),
                # optional ``{item: page OF THE PINNED TEXT}`` -- items.json's
                # ``pdf_page``, never its printed ``page``, because the gate
                # compares it against the pages it split the text into and the two
                # differ by the source's offset.  The gate falls back to the item's
                # own heading in the pinned text when this is absent, so nothing
                # has to ship beside gate.json, and that fallback is what leaves 26
                # of the baseline's 3,019 quotes unplaceable.  ``presets.
                # source_block`` passes the map through and ``presets.item_pages``
                # builds it; `cf plan-run`, `cf pair` and `cf harvest` fill it from
                # the source's own items.json, and a corpus imported against a
                # source this workspace never prepared still binds by the heading.
                "item_pages": dict(body.get("item_pages") or {}),
            }
        self.repos: list[str] = list(data.get("repos") or [])
        self.kinds: set[str] = set(data.get("kinds") or [])
        self.verdicts: set[str] = set(data.get("verdicts") or [])
        self.severities: set[str] = set(data.get("severities") or ["high", "medium", "low"])
        self.witness_required: bool = bool(data.get("witness_required", False))
        self.require_book_located: bool = bool(data.get("require_book_located", True))
        # Off only for the `mechanical` preset: pair/anchor_check.py and
        # detect/ produce findings without ever reading the book's prose, so
        # demanding a verbatim passage quote of them is demanding a fabrication.
        self.require_passage_quote: bool = bool(data.get("require_passage_quote", True))
        self.require_source_sha: bool = bool(data.get("require_source_sha", True))
        self.verify_text_sha: bool = bool(data.get("verify_text_sha", False))
        self.require_finding_id: bool = bool(data.get("require_finding_id", False))
        # The operator pass is on everywhere except the two *-legacy presets that
        # validate the imported 2011-draft corpus, whose quotes carry pdftotext
        # artifacts predating the check (5 of its 3,019 quotes fail the anchored
        # pass, 2 failed the old independent window search).
        self.check_operators: bool = bool(data.get("check_operators", True))
        # Bind every quote to the page its own item is stated on.  Off for the
        # two *-legacy presets for the same reason as the operator pass: measured
        # with the source's own page_offset (16), 16 of the 2,985 bindable quotes
        # of the imported corpus sit more than PAGE_BIND_WINDOW pages from their
        # item, and those rows are already landed.  A new run has no such history,
        # so it is on.
        self.bind_item_page: bool = bool(data.get("bind_item_page", True))
        # The copyright ceiling, per run: a source whose registry licence note
        # permits more may raise it, and 0 turns the refusal off.
        self.long_quote_chars: int = int(data.get("long_quote_chars", LONG_QUOTE_CHARS))
        self.max_quote_chars: int = int(data.get("max_quote_chars", MAX_QUOTE_CHARS))
        # The page-recurrence refusal, per run for the same reason: a source with a
        # short, legitimately repeated sentence needs a documented way out, and 0
        # turns the rule off.
        self.max_match_pages: int = int(data.get("max_match_pages", MAX_MATCH_PAGES))
        self.findings_dir: str = data.get("findings_dir", "findings")
        self.decl_index: str | None = data.get("decl_index")
        rules = dict(DEFAULT_KIND_RULES)
        for kind, override in (data.get("kind_rules") or {}).items():
            merged = dict(rules.get(kind, {}))
            merged.update(override)
            rules[kind] = merged
        self.kind_rules = rules

    @classmethod
    def load(cls, path: pathlib.Path) -> "GateConfig":
        return cls(json.loads(path.read_text(encoding="utf-8")), path)

    def rule(self, kind: str) -> dict[str, Any]:
        return self.kind_rules.get(kind, {"quote": True, "witness": True, "decls": True})

    # --- sources -----------------------------------------------------------
    def source_ids(self) -> list[str]:
        """Every source_id this gate can check a document against."""
        return sorted(self._sources)

    def source(self, source_id: str) -> dict[str, Any]:
        """One ``sources.<id>`` block, or ``{}`` when the gate does not know it.

        Returning ``{}`` rather than raising is deliberate: an unknown source is
        a GATE FAILURE reported against the document that named it, not a crash
        that loses the other 2,671 files' results.
        """
        return self._sources.get(str(source_id), {})

    def only_source(self) -> str:
        """The single source_id when there is exactly one, else ``""``.

        The one place a document may omit ``source_id``: a single-source gate
        knows what it meant.  With two or more sources an unstamped document is
        refused, because guessing is how a quote gets checked against the wrong
        edition.
        """
        return self.source_ids()[0] if len(self._sources) == 1 else ""


def _load_decl_index(spec: str) -> set[str]:
    """Load the full declaration index used to reject phantom names.

    Accepts a JSON list of ``repo:decl`` strings, a JSON object
    ``{repo: [decl, ...]}``, or JSONL rows carrying ``repo`` and ``decl``.
    """
    path = pathlib.Path(spec).expanduser()
    text = path.read_text(encoding="utf-8")
    out: set[str] = set()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            out.add(f"{row['repo']}:{row['decl']}")
        return out
    if isinstance(data, dict):
        for repo, decls in data.items():
            for d in decls:
                out.add(f"{repo}:{d}")
    else:
        for entry in data:
            if isinstance(entry, str):
                out.add(entry)
            else:
                out.add(f"{entry['repo']}:{entry['decl']}")
    return out


# --------------------------------------------------------------------------- #
# binding a quote to the item it is supposed to evidence
# --------------------------------------------------------------------------- #
#: a numbered item label inside a NORMALISED item string (`exercise 2 3 4`)
_ITEM_LABEL = re.compile(r"(?:^|\s)([a-z][a-z]{2,})\s+(\d+(?:\s+\d+){0,2})(?![0-9])")
#: an item that names a printed page instead of a numbered label
#: (`In-text, book p. 71`, `In-text, book pp. 46-47`)
_ITEM_PRINTED = re.compile(r"(?:^|\s)pp?\s*(\d{1,4})(?:\s|$)")


class PageBind:
    """Where a document's item is stated, and whether its quotes are there.

    Nothing used to bind a quote to its item: ``validate_doc`` checked that the
    quote occurred SOMEWHERE in the pinned text and ``item`` was read for nothing
    but the finding id, so a finding about ``Definition 1.6.1`` passed on a
    sentence from another section.  The item's page comes from, in order,
    ``gate.json``'s own ``sources.<id>.item_pages`` map, the printed page the
    label itself names, and the item's heading in the pinned text.

    When none of those yields a page the state is UNBOUND: recorded and printed
    by the gate, never a silent pass and never a mass failure.  Measured over the
    baseline corpus, 262 of its 3,019 quotes cannot be placed at all when the run
    does not know the source's page offset (230 whose item names a printed page
    the gate cannot convert without it, 32 whose numbered label occurs nowhere in
    the pinned text); with the registry's offset of 16, 26 remain.
    """

    def __init__(self, cfg: "GateConfig", source_id: str, book: "BookText",
                 item: Any, *, window: int = PAGE_BIND_WINDOW):
        self.item = clean_item(str(item or ""))
        self.window = window
        self.on = bool(cfg.bind_item_page)
        self.pages, self.how = self._resolve(cfg.source(source_id), book)

    def _resolve(self, block: dict[str, Any], book: "BookText") -> tuple[list[int], str]:
        if not self.on:
            return [], "the item-page bind is off for this preset"
        if not self.item:
            return [], "the document names no item"
        mapped = (block.get("item_pages") or {})
        for label, page in mapped.items():
            if clean_item(str(label)) == self.item:
                pages = page if isinstance(page, list) else [page]
                return [int(p) for p in pages], "gate.json item_pages"
        printed = _ITEM_PRINTED.search(norm(self.item))
        offset = int(block.get("page_offset") or 0)
        if printed and offset:
            # `page_offset: 0` is how gate.json spells "this run does not know the
            # offset" (presets.source_block defaults to it), and binding a printed
            # page as if the offset were 0 would reject every In-text item of a
            # source that is offset -- a false refusal destroys real findings.
            return ([int(printed.group(1)) + offset],
                    "the printed page the item names, plus the source's page_offset")
        if book.pages is None:
            return [], "the source was handed in as flat text, which carries no pages"
        label = _ITEM_LABEL.search(norm(self.item))
        if not label:
            return [], f"{self.item!r} is neither a numbered label nor a printed page"
        pages = book.pages_with_label(f"{label.group(1)} {label.group(2)}")
        if not pages:
            return [], f"{self.item!r} occurs nowhere in the pinned text"
        return pages, "the item's own heading in the pinned text"

    def problem(self, where: str, m: "QuoteMatch") -> str:
        """``""`` unless the quote matched too far from where the item is stated."""
        if not self.pages or not m.pages:
            return ""
        delta = min(abs(page - known) for page in m.pages for known in self.pages)
        if delta <= self.window:
            return ""
        return (f"{where} matched on page(s) {m.pages}, {delta} pages from "
                f"{self.item!r} on page(s) {self.pages} ({self.how}) — this quote is "
                f"not the item's passage")

    def note(self, name: str, where: str, m: "QuoteMatch") -> str:
        """The visible record when a quote could NOT be bound to a page."""
        if not self.on:
            return ""
        if not self.pages:
            return f"page unbound: {name}: {self.how}"
        if not m.pages:
            return (f"page unbound: {name}: {where} matches no single page "
                    f"(it straddles a page break)")
        return ""


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _witness_ok(witness: Any) -> bool:
    """Presence/non-empty only — judging the witness is the reviewer's job."""
    if not isinstance(witness, dict):
        return False
    object_ = str(witness.get("object") or "").strip()
    why = str(witness.get("why") or "").strip()
    return bool(object_) and bool(why)


def doc_source_id(doc: Any, cfg: GateConfig) -> str:
    """The source_id a document is judged against.

    A document says which source it read.  When the gate knows exactly one
    source an unstamped document inherits it (the single-source case cannot be
    ambiguous); with two or more, an unstamped document is refused by
    :func:`validate_doc` rather than guessed at.
    """
    if isinstance(doc, dict) and doc.get("source_id"):
        return str(doc["source_id"])
    return cfg.only_source()


def validate_doc(
    doc: Any,
    name: str,
    cfg: GateConfig,
    books: "Mapping[str, BookText] | BookText | str",
    decl_index: set[str] | None = None,
) -> list[str]:
    """Return the list of gate errors for one findings document.

    ``books`` is the ``{source_id: BookText}`` map :func:`load_sources` returns.
    A single ``BookText``/``str`` is still accepted for the one-source callers
    (``pair/anchor_check``, ``detect/``), and is then used for whatever source
    the document names.
    """
    errs: list[str] = []
    if not isinstance(doc, dict):
        return [f"{name}: top level is not a JSON object"]

    for key in ("schema_version", "item", "book_located", "matches", "disagreements"):
        if key not in doc:
            errs.append(f"{name}: missing required key {key!r}")
    if doc.get("schema_version") != cfg.schema_version:
        errs.append(
            f"{name}: schema_version {doc.get('schema_version')!r} "
            f"(expected {cfg.schema_version}; run convert_v1.py on a v1 corpus)"
        )

    # --- the SOURCE gate ---------------------------------------------------
    # An item is the pair (source_id, item): a document that does not say which
    # source it read cannot be checked against a text, and one that names a
    # source this run does not carry was judged against something nobody pinned.
    source_id = doc_source_id(doc, cfg)
    known = cfg.source_ids()
    if not source_id:
        errs.append(
            f"{name}: missing source_id and this gate carries {len(known)} sources "
            f"({', '.join(known) or 'none'}) — stamp the source the quotes came from"
        )
    elif known and source_id not in known:
        errs.append(
            f"{name}: source_id {source_id!r} is not one of this run's sources "
            f"({', '.join(known)})"
        )
    block = cfg.source(source_id)
    book = _book_for(books, source_id)
    if cfg.require_source_sha:
        want = block.get("sha256")
        if not doc.get("source_sha"):
            errs.append(f"{name}: missing source_sha (the pinned edition must be stamped)")
        elif want and doc["source_sha"] != want:
            errs.append(
                f"{name}: source_sha {doc['source_sha'][:12]}… is not "
                f"{source_id}'s {str(want)[:12]}… — wrong edition"
            )
    if cfg.require_book_located and not doc.get("book_located"):
        errs.append(
            f"{name}: book_located is false — locate the passage, or raise a COVERAGE finding"
        )
    bind = PageBind(cfg, source_id, book, doc.get("item"))
    passage = doc.get("book_passage_quote")
    if cfg.require_passage_quote or passage:
        found = quote_match(book, passage or "", max_chars=cfg.max_quote_chars,
                            max_pages=cfg.max_match_pages)
        if not found.ok:
            errs.append(f"{name}: book_passage_quote {found.why}")
        else:
            problem = bind.problem("book_passage_quote", found)
            if problem:
                errs.append(f"{name}: {problem}")

    for i, m in enumerate(doc.get("matches") or []):
        if not isinstance(m, dict):
            errs.append(f"{name}: matches[{i}] is not an object")
            continue
        if m.get("verdict") not in cfg.verdicts:
            errs.append(
                f"{name}: match[{i}] verdict {m.get('verdict')!r} not in {sorted(cfg.verdicts)}"
            )

    # --- every key that CARRIES defect rows, not just the one it is written under -
    # `harvest/tally.py` and `ledger/findings.py` count `disagreements` and
    # `findings`; the gate validated only the first, so a file whose rows sat
    # under `findings` was gated on an empty list and counted on the union --
    # kind vocabulary, verdict vocabulary, witness rule, repo allowlist and quote
    # gate all bypassed by one key name.
    run_id = doc.get("run_id") or cfg.run_id
    for key in DEFECT_LISTS:
        rows = doc.get(key)
        if rows is None:
            continue
        if not isinstance(rows, list):
            errs.append(f"{name}: {key!r} must be a list of defect rows, got {type(rows).__name__}")
            continue
        label = "disagreement" if key == "disagreements" else key
        for i, g in enumerate(rows):
            errs.extend(_validate_disagreement(g, i, name, cfg, book, decl_index, run_id, doc,
                                               source_id, bind=bind, key=label))
    # and the NEXT plausible key name fails loudly instead of silently: a
    # top-level array of objects carrying `kind` is a defect list, whatever it is
    # called.  No baseline file carries one (measured over all 2,672).
    for other, value in doc.items():
        if other in DEFECT_LISTS or other == "matches" or not isinstance(value, list):
            continue
        if any(isinstance(x, dict) and "kind" in x for x in value):
            errs.append(
                f"{name}: top-level {other!r} is an array of defect rows the gate does not "
                f"validate — the keys that are gated and counted are "
                f"{', '.join(repr(k) for k in DEFECT_LISTS)}"
            )
    return errs


def _validate_disagreement(
    g: Any,
    i: int,
    name: str,
    cfg: GateConfig,
    book: "BookText | str",
    decl_index: set[str] | None,
    run_id: str,
    doc: dict[str, Any],
    source_id: str = "",
    *,
    bind: "PageBind | None" = None,
    key: str = "disagreement",
) -> list[str]:
    errs: list[str] = []
    where = f"{name}: {key}[{i}]"
    if not isinstance(g, dict):
        return [f"{where} is not an object"]

    for required in ("concept", "kind", "repos_involved", "faithful", "reasoning", "severity"):
        if required not in g:
            errs.append(f"{where}: missing required key {required!r}")

    kind = g.get("kind")
    if kind not in cfg.kinds:
        errs.append(f"{where}.kind {kind!r} not in {sorted(cfg.kinds)}")
        return errs  # without a known kind the per-kind rules are meaningless
    rule = cfg.rule(kind)

    if g.get("severity") not in cfg.severities:
        errs.append(f"{where}.severity {g.get('severity')!r} not in {sorted(cfg.severities)}")

    faithful = g.get("faithful")
    if not isinstance(faithful, list):
        errs.append(f"{where}.faithful must be a list of repos (schema v2), got {faithful!r}")
    else:
        for repo in faithful:
            if cfg.repos and repo not in cfg.repos:
                errs.append(f"{where}.faithful entry {repo!r} is not a known repo {cfg.repos}")

    decls = g.get("unfaithful_decls") or []
    if not isinstance(decls, list):
        errs.append(f"{where}.unfaithful_decls must be a list")
        decls = []
    if rule.get("decls") and not decls:
        errs.append(f"{where}: kind {kind} must name the unfaithful declaration(s)")
    prefixes = "|".join(re.escape(r) for r in cfg.repos) or r"[A-Za-z0-9_.-]+"
    for u in decls:
        if not isinstance(u, str) or not re.match(rf"^({prefixes}):\S", u):
            errs.append(f"{where}: unfaithful_decls entry {u!r} must be 'repo:decl'")
        elif decl_index is not None and u not in decl_index:
            errs.append(f"{where}: unfaithful_decls entry {u!r} names no declaration in the index")

    if rule.get("quote", True) or g.get("book_quote"):
        optional = "" if rule.get("quote", True) else f" (optional for {kind}, but must be real)"
        found = quote_match(book, g.get("book_quote", ""), max_chars=cfg.max_quote_chars,
                            max_pages=cfg.max_match_pages)
        if not found.ok:
            errs.append(f"{where}.book_quote {found.why}{optional}")
        elif bind is not None:
            problem = bind.problem(f"{key}[{i}].book_quote", found)
            if problem:
                errs.append(f"{name}: {problem}")

    if cfg.witness_required and rule.get("witness"):
        if not _witness_ok(g.get("witness")):
            errs.append(
                f"{where}: kind {kind} needs a witness object "
                '{"object": ..., "why": ...} with both fields non-empty'
            )

    if rule.get("bridge"):
        if g.get("bridge") not in {"proved", "fiat", "none"}:
            errs.append(f"{where}: kind {kind} needs bridge ∈ proved|fiat|none")
    if rule.get("closure"):
        if not g.get("closure"):
            errs.append(f"{where}: kind {kind} needs closure evidence (the dependency path)")
    if rule.get("pages"):
        page = g.get("page")
        if not isinstance(page, dict) or "claimed" not in page or "actual" not in page:
            errs.append(f"{where}: kind {kind} needs page {{claimed, actual}}")
    if rule.get("mechanical_only") and g.get("source") != "anchor_check":
        errs.append(
            f"{where}: kind {kind} is mechanical-only — it may only come from anchor_check.py"
        )

    # A finding_id that is PRESENT is always recomputed and compared, on every
    # preset.  `require_finding_id` is off for the five runnable presets because
    # nothing tells a worker how to compute a sha256 and no helper ships with the
    # gate, so demanding the field would fail every task for a field no prompt
    # asks for -- but that left the comparison dead on a real fleet, and a run
    # that wrote `"finding_id": "00"*32` on every row had it stored verbatim.
    # Measured over the imported baseline: 347 of 347 defect rows carry an id
    # after convert_v1 and every one matches, so nothing landed is refused.
    got = g.get("finding_id")
    if cfg.require_finding_id or got:
        want = finding_id(run_id, source_id, doc.get("item", ""),
                          str(kind), [d for d in decls if isinstance(d, str)])
        if not got:
            # naming the expected id is what makes the requirement answerable:
            # there is no other way to learn it from inside a worktree.
            errs.append(f"{where}: missing finding_id (this row's is {want})")
        elif not isinstance(got, str):
            # Reading the field on every preset means its TYPE is the worker's
            # too, and `got[:12]` on a JSON number or object is a traceback out
            # of `cf harvest`, `cf pair` and the tend validation command where
            # the gate owes a named refusal instead.
            errs.append(f"{where}: finding_id must be the 64-character hex string "
                        f"sha256(run_id, source_id, item, kind, sorted decls), got {got!r}")
        elif got != want:
            errs.append(f"{where}: finding_id {got[:12]}… != computed {want[:12]}…")
    return errs


def partial_quotes(doc: Any, name: str, book: "BookText | str", *,
                   max_chars: int = MAX_QUOTE_CHARS,
                   max_pages: int = MAX_MATCH_PAGES) -> list[str]:
    """Quotes in ``doc`` that matched only as a window — what nobody verified.

    The gate prints these on success.  A quote that is 70% genuine and 30%
    invented passes the word pass; refusing it outright would reject 287 of the
    3,019 real quotes in the baseline corpus, in 281 of its 2,672 files (286 with
    the operator pass on), so the gate names the unverified remainder instead of
    staying silent about it.
    """
    out: list[str] = []
    if not isinstance(doc, dict):
        return out
    for where, q in _quotes_of(doc):
        found = quote_match(book, q, max_chars=max_chars, max_pages=max_pages)
        if found.ok and found.why.startswith("partial"):
            out.append(f"{name}: {where} {found.why}")
    return out


def _quotes_of(doc: dict[str, Any]) -> list[tuple[str, str]]:
    """``(where, quote)`` for every quote in a document, under whichever key."""
    todo = [("book_passage_quote", doc.get("book_passage_quote"))]
    for key in DEFECT_LISTS:
        label = "disagreement" if key == "disagreements" else key
        for i, g in enumerate(doc.get(key) or []):
            if isinstance(g, dict) and g.get("book_quote"):
                todo.append((f"{label}[{i}].book_quote", g["book_quote"]))
    return [(where, q) for where, q in todo if q]


def binding_notes(doc: Any, name: str, cfg: GateConfig,
                  book: "BookText | str") -> list[str]:
    """What the gate could not settle about this document, said out loud.

    Two states that are neither a pass nor a failure, and both used to be
    invisible: a quote whose item could not be located on a page (so the page
    bind did not run), and a quote longer than the copyright rule allows but
    inside the refusal ceiling.  The gate prints these with the partial-window
    notes and ``cf harvest --gate`` records them in the receipt.
    """
    out: list[str] = []
    if not isinstance(doc, dict):
        return out
    bk = BookText.coerce(book)
    bind = PageBind(cfg, doc_source_id(doc, cfg), bk, doc.get("item"))
    said_unbound = False
    for where, q in _quotes_of(doc):
        found = quote_match(bk, q, max_chars=cfg.max_quote_chars,
                            max_pages=cfg.max_match_pages)
        if cfg.long_quote_chars and found.chars > cfg.long_quote_chars:
            out.append(f"over-long quote: {name}: {where} is {found.chars} characters "
                       f"(at most a sentence or two, {cfg.long_quote_chars})")
        if not found.ok:
            continue
        note = bind.note(name, where, found)
        if not note:
            continue
        if bind.pages:
            out.append(note)          # this one quote straddles a page break
        elif not said_unbound:
            out.append(note)          # the item itself is unplaceable: say it once
            said_unbound = True
    return out


# --------------------------------------------------------------------------- #
# changed-files check (kept in this file on purpose: only verify_findings.py and
# gate.json are transferred to the run host, so the check must travel with them)
# --------------------------------------------------------------------------- #
#: The git config and environment pins, COPIED from ``cflib.gitsafe`` (SAFE_CONFIG
#: / SAFE_ENV) rather than imported, for the same reason ``finding_id`` is inlined
#: here: only this file and ``gate.json`` are transferred to the run host, where
#: there is no ``cflib``.  They are not optional decoration.  ``--root`` is the
#: worktree the worker OWNS, and several git commands refresh the index first,
#: which runs ``core.fsmonitor`` -- a program named in that worktree's own
#: ``.git/config``.  A worker with ``Bash(git:*)`` (the tightest documented tool
#: allowlist) could therefore make the gate execute its code, from under ``.git/``
#: where the changed-files check cannot see it: verified, both ``git diff`` and
#: ``git ls-files`` fired the hook bare and neither does with these pins.  Keep in
#: step with gitsafe; ``tests/test_rf_gate.py`` asserts they are identical.
_GIT_SAFE_CONFIG: tuple[str, ...] = (
    "-c", "core.fsmonitor=",
    "-c", "core.hooksPath=/dev/null",
    "-c", "diff.external=",
    "-c", "core.pager=cat",
    "-c", "core.sshCommand=",
    "-c", "core.askPass=",
)
_GIT_SAFE_ENV: dict[str, str] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(args: list[str], cwd: pathlib.Path) -> tuple[int, str]:
    """One read-only git command in ``cwd``, with the boundary pinned."""
    import subprocess

    proc = subprocess.run(["git", *_GIT_SAFE_CONFIG, *args], cwd=str(cwd),
                          capture_output=True, text=True, check=False,
                          env={**os.environ, **_GIT_SAFE_ENV})
    return proc.returncode, (proc.stdout or "")


def changed_entries(root: pathlib.Path, base: str) -> tuple[dict[str, str], list[str]]:
    """``{path: status}`` for this contribution against ``base``, plus untracked work.

    The statuses are git's own: ``A`` added, ``M`` modified, ``D`` deleted, ``R``
    renamed (recorded against the old path, with the new one as an addition).
    ``git diff --name-only`` erased them, so the check could not tell a
    contribution that added its own pair from one that also deleted another
    worker's landed findings -- which is the failure ``harvest.py`` exists
    because of, reachable at the merge gate and silent in the direction of
    "cleaner".
    """
    code, _ = _git(["rev-parse", "--verify", "--quiet", base], root)
    if code != 0:
        return {}, [f"base ref {base!r} does not exist in {root} (the check fails closed)"]
    errs: list[str] = []
    entries: dict[str, str] = {}
    code, out = _git(["diff", "--name-status", base], root)
    if code != 0:
        errs.append(f"git diff against {base} failed")
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0].strip()[:1]
        if status == "R" and len(parts) >= 3:
            entries[parts[1]] = "R"
            entries[parts[2]] = "A"
        elif status and len(parts) >= 2 and parts[1].strip():
            entries[parts[1]] = status
        else:
            errs.append(f"could not parse `git diff --name-status` line {line!r} "
                        f"(the check fails closed)")
    _code, out = _git(["ls-files", "--others", "--exclude-standard"], root)
    for path in out.splitlines():
        if path.strip():
            entries.setdefault(path, "A")
    return entries, errs


def changed_paths(root: pathlib.Path, base: str) -> tuple[set[str], list[str]]:
    """The paths alone — :func:`changed_entries` without the statuses."""
    entries, errs = changed_entries(root, base)
    return set(entries), errs


def check_changed(paths: "set[str] | dict[str, str]", *, allow_empty: bool = False,
                  task_id: str = "") -> list[str]:
    """Only ``findings/<id>.json`` and ``tasks/<id>.yaml`` may change, in pairs.

    This is the other half of moving the gate out of tree: the original run let
    a worker commit anything in its worktree, including the gate itself.

    ``paths`` may be the ``{path: status}`` map :func:`changed_entries` returns,
    and then every deletion and rename is refused as well — a contribution adds
    its own pair and removes nothing.  ``task_id`` (the worker's own, from
    ``--task-id`` or ``TEND_TASK_ID``) requires every touched stem to be that
    task's, which is what catches emptying a neighbour's findings file.

    With no ``task_id`` the SINGLE-STEM rule stands in for it: a contribution may
    touch the pair of exactly one stem, so two stems are refused by name.  It is
    weaker than the ownership rule — it cannot tell whose stem the one stem is —
    but it is the half that works when nobody knows the task id, and whether tend
    exports ``TEND_TASK_ID`` into a validation command's environment has never
    been verified against a running orchestrator.

    The count is ADDITION-AWARE, because the tend config runs this same argv for
    the per-contribution ``validation_commands`` and for the
    ``pre_merge_validation_commands`` of a ``batched_merge: true`` root: a batched
    pre-merge change set carries one findings/task pair per batched task, so a
    bare stem count would refuse the whole batch and no flag could relax it.  A
    change set in which every touched path is newly ADDED is therefore allowed to
    carry several stems; one that rewrites, empties, deletes or renames a path
    that was already on the base branch is still refused by name, which is the
    case the rule exists for.  A caller that passes a bare set of paths knows no
    statuses, so it keeps the strict count.
    """
    statuses = dict(paths) if isinstance(paths, dict) else {}
    problems: list[str] = []
    written: set[str] = set()
    tasks: set[str] = set()
    seen: set[str] = set()
    for p in sorted(set(paths)):
        status = statuses.get(p, "")
        parts = p.split("/")
        if len(parts) == 2 and parts[0] == "findings" and parts[1].endswith(".json"):
            stem = parts[1][: -len(".json")]
            if status not in ("D", "R"):
                written.add(stem)
        elif len(parts) == 2 and parts[0] == "tasks" and parts[1].endswith((".yaml", ".yml")):
            stem = parts[1].rsplit(".", 1)[0]
            tasks.add(stem)
        else:
            problems.append(f"changed path {p!r} is outside findings/<id>.json and tasks/<id>.yaml")
            continue
        seen.add(stem)
        if status in ("D", "R"):
            gone = "deleted" if status == "D" else "renamed away"
            problems.append(
                f"changed path {p!r} was {gone} — a contribution writes its own findings and "
                f"task file and removes nothing; another worker's landed findings are not "
                f"yours to drop"
            )
        if task_id and stem != task_id:
            problems.append(
                f"changed path {p!r} belongs to task {stem!r}, not to this worker's {task_id!r} "
                f"— change only findings/{task_id}.json and tasks/{task_id}.yaml"
            )
    for stem in sorted(written - tasks):
        problems.append(f"findings/{stem}.json has no tasks/{stem}.yaml in the same change set")
    if not task_id and len(seen) > 1:
        # Only a path that was already on the base branch makes a second stem a
        # refusal: see the ADDITION-AWARE paragraph above — a batched pre-merge
        # change set is many stems of pure additions, and refusing it would stop
        # every batched merge.
        touched = sorted(p for p in set(paths) if statuses.get(p, "") != "A")
        if touched:
            problems.append(
                f"changed paths belong to {len(seen)} tasks ({', '.join(sorted(seen))}) and no "
                f"task id says which stem is this worker's, and this contribution did not add "
                f"{', '.join(touched)} — a contribution writes one findings/<id>.json and one "
                f"tasks/<id>.yaml; pass --task-id <id> (or export TEND_TASK_ID) to check "
                f"ownership instead of counting stems"
            )
    if not written and not allow_empty:
        problems.append("no findings file was written")
    return problems


def ownership_line(task_id: str) -> str:
    """Which ownership rule judged a passing contribution, for the OK line.

    Both gate entry points print it, and neither may imply the other's rule ran:
    with a task id every touched stem had to be that task's, and without one only
    the single-stem count did — and that count lets a batched pre-merge change set
    of pure additions through, so it is the weaker half in two ways, not one.
    """
    if task_id:
        return f"ownership checked against task {task_id}"
    return ("ownership: single-stem rule (no task id); pass --task-id or export "
            "TEND_TASK_ID for the full check")


def _load_one(cfg: GateConfig, block: dict[str, Any]) -> tuple[BookText, list[str]]:
    """Read and normalise one source's text; returns (BookText, errors)."""
    sid = block.get("source_id") or "?"
    empty = BookText("")
    path = block.get("text")
    if path is None:
        return empty, [f"gate.json sources.{sid} has no text path"]
    if not path.exists():
        return empty, [f"{sid}: source text missing at {path} (the gate fails closed)"]
    raw = path.read_bytes()
    errs: list[str] = []
    want = block.get("text_sha256")
    if cfg.verify_text_sha and want:
        got = hashlib.sha256(raw).hexdigest()
        if got != want:
            errs.append(
                f"{sid}: source text sha256 {got[:12]}… != gate.json "
                f"sources.{sid}.text_sha256 {str(want)[:12]}… — wrong extraction"
            )
    text = raw.decode("utf-8", errors="ignore")
    return BookText.from_text(text, operators=cfg.check_operators), errs


def load_sources(cfg: GateConfig) -> tuple[dict[str, BookText], list[str]]:
    """``({source_id: BookText}, errors)`` for every source in ``gate.json``.

    Every text is read up front so a document's quotes are matched against the
    edition it names and no other.  A gate with no sources at all is an error,
    not an empty pass: a gate that cannot find a text would otherwise approve
    every quote in the run.
    """
    books: dict[str, BookText] = {}
    errs: list[str] = []
    ids = cfg.source_ids()
    if not ids:
        return books, ["gate.json has no `sources` block (the gate fails closed)"]
    for sid in ids:
        book, problems = _load_one(cfg, cfg.source(sid))
        books[sid] = book
        errs.extend(problems)
    return books, errs


def _book_for(books: "Mapping[str, BookText] | BookText | str", source_id: str) -> BookText:
    """The text one document is checked against.

    A missing entry yields an EMPTY text, so every quote in a document naming an
    unknown source fails — the gate fails closed, and the "unknown source_id"
    error above says why.
    """
    if isinstance(books, (BookText, str)):
        return BookText.coerce(books)
    got = books.get(source_id) if hasattr(books, "get") else None
    return BookText.coerce(got) if got is not None else BookText("")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--gate-config",
        default=str(pathlib.Path(__file__).resolve().parent / "gate.json"),
        help="gate.json written by cf plan-run (default: next to this script)",
    )
    ap.add_argument("--root", default=".", help="worktree root holding findings/ (default: cwd)")
    ap.add_argument(
        "--findings-dir",
        default=None,
        help="subdirectory of --root holding the findings (default: gate.json findings_dir). "
             "Use '.' to gate a harvested corpus, where the files sit directly in corpus/<run-id>/",
    )
    ap.add_argument("--findings", action="append", default=[], help="validate this file only (repeatable)")
    ap.add_argument("--source-text", default=None, metavar="ID=PATH",
                    action="append", dest="source_text",
                    help="override gate.json sources.<ID>.text (repeatable). "
                         "With a single-source gate a bare PATH is accepted.")
    ap.add_argument(
        "--changed-files",
        action="store_true",
        help="also refuse a contribution that touched anything but findings/<id>.json "
             "and tasks/<id>.yaml (the out-of-tree gate's other half)",
    )
    ap.add_argument("--base", default="main", help="branch the changed-files check compares against")
    ap.add_argument(
        "--task-id",
        default=os.environ.get("TEND_TASK_ID", ""),
        help="with --changed-files: the worker's own task id, so a contribution that "
             "touches another task's findings/<id>.json or tasks/<id>.yaml is refused "
             "(default: $TEND_TASK_ID). Without one only the single-stem rule runs: "
             "two stems are refused, but nothing can tell whose the one stem is",
    )
    ap.add_argument(
        "--allow-empty",
        action="store_true",
        help="with --changed-files: tolerate a change set with no findings file",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    gate_path = pathlib.Path(args.gate_config).expanduser()
    if not gate_path.exists():
        print(f"FAIL: gate config missing at {gate_path} (the gate could not run: "
              f"this is not a finding of yours)", file=sys.stderr)
        return EXIT_GATE_BROKEN
    cfg = GateConfig.load(gate_path)
    for spec in args.source_text or []:
        sid, sep, path = str(spec).partition("=")
        if not sep:
            sid, path = cfg.only_source(), sid
        if not sid or not cfg.source(sid):
            print(f"FAIL: --source-text {spec!r} names no source of this gate "
                  f"({', '.join(cfg.source_ids()) or 'none'})", file=sys.stderr)
            return EXIT_GATE_BROKEN
        cfg.source(sid)["text"] = pathlib.Path(path).expanduser()
    if args.findings_dir:
        cfg.findings_dir = args.findings_dir

    books, errs = load_sources(cfg)
    if errs:
        for e in errs:
            print(f"FAIL: {e}", file=sys.stderr)
        return EXIT_GATE_BROKEN

    decl_index = _load_decl_index(cfg.decl_index) if cfg.decl_index else None

    root = pathlib.Path(args.root).resolve()
    problems: list[str] = []
    broken: list[str] = []
    if args.changed_files:
        entries, cerrs = changed_entries(root, args.base)
        broken.extend(cerrs)
        problems.extend(cerrs)
        problems.extend(check_changed(entries, allow_empty=args.allow_empty,
                                      task_id=args.task_id))

    if args.findings:
        files = [pathlib.Path(f) for f in args.findings]
    else:
        files = [
            f for f in sorted((root / cfg.findings_dir).glob("*.json"))
            if f.name not in NON_FINDING_FILES and not f.name.startswith(".")
        ]
    if not files and not problems:
        if not args.quiet:
            print("no findings files here; nothing to validate")
        return EXIT_OK

    partial: list[str] = []
    notes: list[str] = []
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - any parse failure is a gate failure
            problems.append(f"{f.name}: not valid JSON ({exc})")
            continue
        problems.extend(validate_doc(doc, f.name, cfg, books, decl_index))
        book = _book_for(books, doc_source_id(doc, cfg))
        partial.extend(partial_quotes(doc, f.name, book, max_chars=cfg.max_quote_chars,
                                      max_pages=cfg.max_match_pages))
        notes.extend(binding_notes(doc, f.name, cfg, book))

    if problems:
        print(f"VALIDATION FAILED ({len(problems)} problem(s)):", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        # A change set the check could not even read is an infrastructure failure
        # wearing a validation failure's clothes.
        return EXIT_GATE_BROKEN if broken else EXIT_REJECTED
    if not args.quiet:
        for note in partial:
            print("  ~ " + note)
        for note in notes[:MAX_NOTES_PRINTED]:
            print("  ? " + note)
        if len(notes) > MAX_NOTES_PRINTED:
            print(f"  ? ... +{len(notes) - MAX_NOTES_PRINTED} more")
        ops = "operators checked" if cfg.check_operators else "OPERATORS NOT CHECKED (legacy preset)"
        unbound = sum(1 for n in notes if n.startswith("page unbound:"))
        bind = (f"{unbound} quote(s) not bound to an item page" if cfg.bind_item_page
                else "ITEM PAGES NOT BOUND (legacy preset)")
        print(
            f"OK: {len(files)} findings file(s) validated against gate {cfg.preset or cfg.mode}; "
            f"every book quote matched the text ({len(partial)} only in part, listed above; "
            f"{ops}; {bind})"
        )
        # The ownership rule is the half of the changed-files check that catches
        # emptying a neighbour's findings file, and without a task id only the
        # weaker single-stem rule ran: say which one judged this contribution,
        # because this is the invocation the tend config uses.
        if args.changed_files:
            print(f"  changed files: {ownership_line(args.task_id)}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
