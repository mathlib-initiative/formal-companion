"""Turn a book quote into something shareable, and recognise one again.

New code.  The redacted form is the hash ``route._redact`` already computed, plus
the three facts a reader and :mod:`share.hydrate` need that it dropped:

    {"redacted": true, "redaction": "quote-v1",
     "chars": 176, "norm_chars": 174,
     "sha256": "<sha256 of the normalised quote>",
     "page": 34, "pages": [34],
     "first_words": "let a b be an interval",
     "last_words": "which is what we wanted"}

``sha256`` is over the NORMALISED quote -- whitespace collapsed, lowercased, and
nothing else -- the same normalisation ``scripts/route/route.py`` hashes, so a
row redacted by ``cf route --redact-quotes`` and a row redacted here carry the
same digest for the same sentence.  It is deliberately NOT the quote gate's
:func:`gate.verify_findings.norm`, which deletes every operator: the defect class
this audit exists to find is a flipped ``≤``, so a hash that cannot tell
``m(E) ≤ m(F)`` from ``m(E) ≥ m(F)`` would let :mod:`share.hydrate`
reinstate the wrong sentence and call it verified.

``first_words``/``last_words`` are a BUDGET, not a fixed six: at most a quarter
of the words from each end, and never an edge at or above :data:`PROSE_SPAN_MIN`
normalised characters, which is the floor this same module applies to a verbatim
run inside somebody's prose.  A flat six words from each end fails both tests on
a short sentence -- six and six of a fourteen-word sentence is twelve of its
words -- and a redaction that hands over most of the sentence, its exact length
and its digest is not a redaction.  A quote below :data:`MIN_WORDS_FOR_EDGES`
words carries no edges at all.

``book_quote`` is not the only field that carries the source.  A worker quotes
the book inside their own prose, and MEASURED over the baseline campaign's own
ledger and routed rows (``accepted``/``refuted``/``findings.adam.jsonl``, 25
rows, 59 non-empty prose fields) 10 of those fields carry a run of
:data:`PROSE_SPAN_MIN` normalised characters or more that occurs verbatim in the
book -- the longest 113 characters in a ``reason``, and one ``reason`` holding a
whole ``pdftotext`` page dump.  So :class:`SourceIndex` and
:func:`redact_prose_field` do to those runs what :func:`redact_quote` does to
``book_quote``: the span is replaced in place by a marker and its record is
appended to the row's ``redacted_spans``, from which ``cf quotes hydrate``
recovers it exactly like any other redacted quote.

:func:`redact_prose` walks EVERY string of a row, at any depth, and redacts by
default.  It used to iterate :data:`PROSE_FIELDS` at depth one, and that is a
key enumeration: of the 24 keys ``ledger/transcribe.py`` writes onto a v2 ledger
row exactly one (``reason``) was in the tuple, so ``summary``,
``refutation_text``, ``lens_disagreement``, ``recovery_argument`` and
``dissent[].argument`` carried the sentence that had just been redacted out of
``reason``, and a routed row's ``witness.why`` was never looked at.  A walk whose
default is redact scrubs the next prose key somebody adds instead of leaking it.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Iterable, Sequence

#: the shape of the record this module writes, so a reader can tell it from
#: ``route._redact``'s (which carries no page and no normalised length)
REDACTION = "quote-v1"
#: the MOST words of each end that travel with a redacted quote
EDGE_WORDS = 6
#: below this many words the edges ARE the quote, so they are withheld
MIN_WORDS_FOR_EDGES = 2 * EDGE_WORDS + 2

#: the prose fields of a finding or a ledger row: written by a worker in their
#: own words, and measurably carrying the source's words as well.  This is the
#: documented MINIMUM -- :func:`redact_prose` scans every string of a row -- and
#: it is what the measurement in :data:`PROSE_SPAN_MIN` was taken over.
PROSE_FIELDS = ("reason", "evidence", "finding_summary", "original_concept",
                "reasoning", "concept", "suggested_fix")
#: where a prose field's redacted spans are recorded, so they can be hydrated
SPANS_KEY = "redacted_spans"

#: keys whose value is a fact ABOUT the finding rather than somebody's prose: an
#: id, an enum, a hash, a page number, a declaration name or a file:line that
#: :func:`elide_row_paths` has already handled.  Everything else a row carries is
#: treated as prose, so the next key a writer adds is scrubbed rather than
#: leaked.  Keys ending ``_decls`` (``unfaithful_decls``, ``other_decls``) are
#: skipped by the same rule.
SKIP_KEYS = frozenset({
    "finding_id", "run_id", "source_id", "source_sha", "item", "kind", "verdict",
    "severity", "method", "schema", "at", "repo", "provenance", "source_file",
    "page", "confidence", "resolution", "decl_sites", "repo_commit",
    "decl_fingerprints",
})
#: subtrees :func:`redact_prose` must not descend into.  Both are redaction
#: RECORDS this module wrote: their ``first_words``/``last_words`` are edge words
#: by design (POLICY.md S4) and real ones measure above :data:`PROSE_SPAN_MIN`,
#: so a blanket walk would perforate the record, nest a record inside a record
#: and cost ``cf quotes hydrate`` the anchor it searches a page with.
SKIP_SUBTREES = frozenset({"book_quote", SPANS_KEY})

#: how long a verbatim run of the source has to be, in NORMALISED characters,
#: before a prose field's span is redacted.
#:
#: MEASURED over the baseline campaign's 59 non-empty prose fields: 10 of them
#: carry a run at or above 40 -- their longest runs are 40, 41, 41, 42, 42, 45,
#: 57, 84, 84 and 113 characters, and redacting each field until nothing is left
#: at or above the floor takes 13 substitutions, at most two in any one field.
#: The longest run BELOW the floor is 36 characters: "the lebesgue measurable
#: subsets of a", a term of art an auditor writing about that item produces anyway
#: rather than a sentence of the book.  40 is that floor, and it is a floor rather
#: than a guess because the gap either side of it is the gap between a phrase and
#: a sentence.
PROSE_SPAN_MIN = 40
#: how many spans one prose field may have redacted before the export refuses it.
#: The measured worst case is two; a field with nine separate verbatim runs is a
#: page dump the exporter should be told about rather than quietly perforated.
MAX_SPANS_PER_FIELD = 8


def normalise_quote(text: Any) -> str:
    """Collapse whitespace and lowercase -- the form the digest is taken over.

    Identical to the normalisation inlined in ``route._redact``
    (``" ".join(quote.split()).lower()``).  The two must stay identical or a row
    shared through ``cf route --redact-quotes`` and a row shared through
    ``cf share export`` stop being the same quote; ``tests/test_rf_share.py``
    pins them against each other.
    """
    return " ".join(str(text or "").split()).lower()


def quote_digest(text: Any) -> str:
    """sha256 of :func:`normalise_quote` of *text*."""
    return hashlib.sha256(normalise_quote(text).encode("utf-8")).hexdigest()


def normalise_with_offsets(raw: str) -> tuple[str, list[int]]:
    """:func:`normalise_quote` of *raw*, plus where each of its characters came from.

    ``offsets[k]`` is the index in *raw* of ``norm[k]``, so a span found in the
    normalised form can be cut out of -- or replaced inside -- the holder's own
    bytes.  The space that stands for a whitespace RUN is given the index of the
    last character of that run, which keeps the map monotone and means a replaced
    span never eats the whitespace before the token it starts at.

    :mod:`share.hydrate`'s page index is built from this same function, so the
    normalisation a digest is taken over and the normalisation a span is located
    in cannot drift apart.
    """
    raw = str(raw or "")
    parts: list[str] = []
    offsets: list[int] = []
    start = 0
    first = True
    n = len(raw)
    while start < n:
        while start < n and raw[start].isspace():
            start += 1
        if start >= n:
            break
        end = start
        while end < n and not raw[end].isspace():
            end += 1
        if not first:
            parts.append(" ")
            offsets.append(start - 1)       # inside the whitespace run before it
        first = False
        lowered = raw[start:end].lower()
        parts.append(lowered)
        if len(lowered) == end - start:
            offsets.extend(range(start, end))
        else:
            # A character whose lowercase has a different LENGTH (Turkish dotted I
            # and friends).  The digest is still exact -- this is the same
            # ``.lower()`` the record's normalisation uses -- and only the raw
            # span's edge can move by a character inside this one token.
            offsets.extend([start] * len(lowered))
        start = end
    return "".join(parts), offsets


class SourceIndex:
    """Blocks of a workspace's pinned text, for finding a verbatim run in prose.

    The question this answers, for every prose field of every exported row, is
    "does any run of at least :data:`PROSE_SPAN_MIN` characters of this field
    occur in the source?".  Asking it by sliding the field over the text costs
    about ten seconds for the baseline campaign's 25 rows, which is more than an
    export can pay, so the text is indexed once instead: every block of
    ``PROSE_SPAN_MIN // 2`` characters at a position that is a multiple of that
    block length.

    That is enough, and it is enough for a reason rather than by luck.  A shared
    run of length L >= 2B covers L - B + 1 > B consecutive text positions at which
    a block could start, so one of them IS a multiple of B: every run long enough
    to matter contains an indexed block, and looking each of the field's own blocks
    up in the index and extending it both ways finds the longest run there is.
    Measured on the same rows: 4 ms instead of 10.8 s, same 13 spans.
    """

    def __init__(self, texts: Iterable[str], *, minimum: int = PROSE_SPAN_MIN):
        self.minimum = max(2, int(minimum))
        self.block = self.minimum // 2
        self.texts: list[str] = []
        self.blocks: dict[str, list[tuple[int, int]]] = {}
        for raw in texts:
            norm = normalise_quote(raw)
            if len(norm) < self.block:
                continue
            which = len(self.texts)
            self.texts.append(norm)
            for at in range(0, len(norm) - self.block + 1, self.block):
                self.blocks.setdefault(norm[at:at + self.block], []).append((which, at))

    def __bool__(self) -> bool:
        return bool(self.texts)

    def longest_span(self, field: str) -> tuple[int, int] | None:
        """``(start, end)`` of the longest run of *field* (normalised) in the text.

        ``None`` when the longest run is shorter than :attr:`minimum`, which is
        the answer for 49 of the baseline's 59 prose fields.
        """
        if not self.texts or len(field) < self.minimum:
            return None
        block = self.block
        best = (0, 0, 0)
        for i in range(len(field) - block + 1):
            for which, j in self.blocks.get(field[i:i + block], ()):
                text = self.texts[which]
                a, b = i, j
                while a and b and field[a - 1] == text[b - 1]:
                    a -= 1
                    b -= 1
                e, f = i + block, j + block
                while e < len(field) and f < len(text) and field[e] == text[f]:
                    e += 1
                    f += 1
                if e - a > best[0]:
                    best = (e - a, a, e)
        return (best[1], best[2]) if best[0] >= self.minimum else None


def span_mark(record: dict) -> str:
    """What stands in a prose field where a verbatim run of the source was.

    It names the digest so a reader can pair the marker with its record in
    ``redacted_spans``, and says the length so the sentence's absence is visible
    rather than looking like the auditor wrote a short remark.
    """
    return ("[redacted: %d chars of the source, sha256 %s]"
            % (int(record.get("chars") or 0), str(record.get("sha256") or "")[:12]))


def redact_prose_field(value: Any, index: SourceIndex, *,
                       locate: Callable[[str], Sequence[int]] | None = None,
                       limit: int = MAX_SPANS_PER_FIELD) -> tuple[str, list[dict]]:
    """*value* with every verbatim run of the source replaced by a marker.

    Returns ``(text, records)``.  Each record is a :func:`redact_quote` record for
    the span it replaced, so the span is recoverable by exactly the machinery a
    redacted ``book_quote`` is: a digest over the normalised passage, its length,
    its page and its edge words.  *locate* is the caller's page finder (see
    :func:`share.hydrate.locate_pages`); without it the records carry no page and
    a hydration has to search the whole source.

    Raises :class:`ValueError` past *limit* substitutions in one field: a field
    with that many separate verbatim runs is a page dump, and the caller says so
    by name instead of handing over a perforated paragraph.
    """
    text = str(value or "")
    records: list[dict] = []
    if not index or not text.strip():
        return text, records
    while True:
        norm, offsets = normalise_with_offsets(text)
        span = index.longest_span(norm)
        if span is None:
            return text, records
        if len(records) >= limit:
            raise ValueError(
                "%d separate verbatim run(s) of the source, the longest %d characters"
                % (len(records) + 1, span[1] - span[0]))
        start, end = offsets[span[0]], offsets[span[1] - 1] + 1
        # A span may begin or end at the SPACE that stands for a whitespace run.
        # Leaving that whitespace outside the marker keeps the surrounding prose
        # spaced as the auditor wrote it, and makes the record's raw length the
        # passage's own rather than the passage plus a blank.
        start += len(text[start:end]) - len(text[start:end].lstrip())
        end -= len(text[start:end]) - len(text[start:end].rstrip())
        passage = text[start:end]
        record = redact_quote(passage, pages=list(locate(passage)) if locate else ())
        records.append(record)
        text = text[:start] + span_mark(record) + text[end:]


def edge_words(words: Sequence[str]) -> int:
    """How many words of each end a record may disclose (0 = none).

    Three bounds, in order, and the last two are what the flat six words missed:

    * below :data:`MIN_WORDS_FOR_EDGES` words nothing travels, because six words
      from each end of a twelve-word sentence is the sentence;
    * at most a QUARTER of the words from each end, so the two edges are at most
      half the quote whatever its length;
    * and no edge at or above :data:`PROSE_SPAN_MIN` characters, the floor this
      module already refuses a verbatim run of the source at.

    MEASURED over the 429 ``book_quote`` strings of the baseline corpus: the flat
    six words handed over 12 of a 14-word quote (86% of it, with its exact length
    and its digest beside them), its longest edge ran to 67 characters, and 149
    of the 429 carried an edge at or above the 40-character floor -- inside a
    record the same export labels ``redacted``.  Under the budget the worst case
    is half the words, the longest edge is 39 characters, none reaches the floor,
    70 quotes have an edge shortened by the character bound, and the same 39
    short quotes withhold their edges as before.  Hydration is unaffected: the
    digest is what verifies a passage and the first words only prune where the
    search starts (``share.hydrate._Page.windows``).
    """
    count = len(words)
    if count < MIN_WORDS_FOR_EDGES:
        return 0
    keep = min(EDGE_WORDS, count // 4)
    while keep and max(len(" ".join(words[:keep])),
                       len(" ".join(words[-keep:]))) >= PROSE_SPAN_MIN:
        keep -= 1
    return keep


def redact_quote(quote: Any, *, pages: Sequence[int] = ()) -> dict:
    """The shareable record for one book quote.

    ``pages`` are the 1-based PDF pages of the pinned text the quote was located
    on (see :func:`share.hydrate.locate_pages`); an empty list is recorded as
    ``page: None`` rather than guessed at, and ``cf quotes hydrate`` then has to
    search every page instead of a window.
    """
    raw = str(quote or "")
    norm = normalise_quote(raw)
    words = norm.split()
    page_list = [int(p) for p in pages]
    out: dict[str, Any] = {
        "redacted": True,
        "redaction": REDACTION,
        "chars": len(raw),
        "norm_chars": len(norm),
        "words": len(words),
        "sha256": hashlib.sha256(norm.encode("utf-8")).hexdigest(),
        "page": page_list[0] if page_list else None,
        "pages": page_list,
    }
    keep = edge_words(words)
    if keep:
        out["first_words"] = " ".join(words[:keep])
        out["last_words"] = " ".join(words[-keep:])
    elif len(words) < MIN_WORDS_FOR_EDGES:
        out["edges_withheld"] = (
            f"{len(words)} words: {EDGE_WORDS} from each end would be the whole quote"
        )
    else:
        out["edges_withheld"] = (
            f"{len(words)} words: one word from each end would already be a run of "
            f"{PROSE_SPAN_MIN}+ characters of the source"
        )
    return out


def is_redacted(value: Any) -> bool:
    """True for a redacted-quote record -- ours or ``route._redact``'s."""
    return isinstance(value, dict) and bool(value.get("redacted")) and bool(value.get("sha256"))


def redact_row(row: dict, *, pages: Sequence[int] = ()) -> dict:
    """A copy of *row* whose ``book_quote`` is a redacted record.

    A row that carries no quote, or one already redacted, is returned unchanged:
    re-redacting a record would hash the record itself.
    """
    quote = row.get("book_quote")
    if is_redacted(quote) or not str(quote or "").strip():
        return dict(row)
    out = dict(row)
    out["book_quote"] = redact_quote(quote, pages=pages)
    return out


def _skipped(key: str) -> bool:
    """Is *key* structural (an id, an enum, a declaration) rather than prose?"""
    return key in SKIP_KEYS or key in SKIP_SUBTREES or key.endswith("_decls")


def redact_prose(row: dict, index: SourceIndex, *,
                 locate: Callable[[str], Sequence[int]] | None = None) -> dict:
    """A copy of *row* whose prose carries no verbatim run of the source.

    EVERY string of the row is scanned, at any depth, except the structural keys
    (:data:`SKIP_KEYS`) and the two redaction-record subtrees
    (:data:`SKIP_SUBTREES`).  The default is to redact, because the version of
    this function that iterated :data:`PROSE_FIELDS` at depth one scanned one of
    a v2 ledger row's five free-text keys and none of a routed row's
    ``witness.why`` -- and because a field that IS in the tuple goes unscanned
    the moment a writer makes it a list, which the baseline's own ``evidence``
    already is.

    The records go into :data:`SPANS_KEY` as ``{"field": ..., "quote": {...}}``,
    where ``field`` is the JSON path the span came from (``witness.why``,
    ``dissent[0].argument``), so the row says what was taken out of it and
    ``cf quotes hydrate`` can put each one back and name where it belongs.  A row
    with nothing to redact is returned with no such key at all: a bundle whose
    rows all carry an empty ``redacted_spans`` would read as though something had
    been withheld everywhere.

    Raises :class:`ValueError` naming the path when one field is a page dump
    (see :func:`redact_prose_field`); the caller adds the row.
    """
    if not index:
        return dict(row)
    spans: list[dict] = list(row.get(SPANS_KEY) or [])

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, str):
            if not value.strip():
                return value
            try:
                text, records = redact_prose_field(value, index, locate=locate)
            except ValueError as exc:
                raise ValueError(f"{path}: {exc}") from None
            spans.extend({"field": path, "quote": record} for record in records)
            return text
        if isinstance(value, dict):
            return {k: (v if _skipped(str(k))
                        else walk(v, f"{path}.{k}" if path else str(k)))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, f"{path}[{n}]") for n, v in enumerate(value)]
        return value

    out = walk(dict(row), "")
    if spans:
        out[SPANS_KEY] = spans
    return out


#: an absolute POSIX path of three components or more, written inside a row.
#:
#: Not a guess: the baseline campaign's own ``findings.adam.jsonl`` carries one
#: absolute checkout path (``<home>/<checkout>/MeasureTheory/Tao/Integration/
#: UnsignedMeasurability.lean:324``) inside a ``reasoning`` field, because a worker
#: cites the file it read.  RULES and CLAUDE.md S1 forbid a path in anything
#: shareable, and this is the one command that writes outside the workspace.
#:
#: The two lookbehinds keep a URL out of it: at the second slash of ``https://``
#: the preceding two characters are ``:/``, so an errata URL -- which IS metadata a
#: bundle carries -- is left alone, while ``(/a/b/c.lean)`` in prose is not.
_LOCAL_PATH = re.compile(r"(?<![\w.~])(?<!:/)/(?:[\w.@+~%-]+/){2,}[\w.@+~%-]+")


def elide_local_paths(text: Any) -> tuple[str, int]:
    """*text* with each absolute path cut down to its last component.

    Returns ``(text, count)``.  The file name and any ``:line`` after it survive,
    because that is the evidence the row is making; the directories above it are a
    fact about the exporter's laptop and nothing else.
    """
    raw = str(text or "")
    count = 0

    def one(match: re.Match) -> str:
        nonlocal count
        count += 1
        return ".../" + match.group(0).rsplit("/", 1)[-1]

    return _LOCAL_PATH.sub(one, raw), count


def elide_row_paths(row: dict) -> tuple[dict, int]:
    """A copy of *row* with every absolute path in it elided, at any depth."""
    count = 0

    def walk(value: Any) -> Any:
        nonlocal count
        if isinstance(value, str):
            text, found = elide_local_paths(value)
            count += found
            return text
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(dict(row)), count


def prose_spans(row: dict) -> list[dict]:
    """``[{"field", "quote"}]`` for the prose spans redacted out of *row*."""
    return [s for s in (row.get(SPANS_KEY) or [])
            if isinstance(s, dict) and is_redacted(s.get("quote"))]


def quote_of(row: dict) -> str:
    """The row's verbatim quote, or ``""`` when it carries none or a record."""
    quote = row.get("book_quote")
    return "" if is_redacted(quote) else str(quote or "")


def count_quotes(rows: Iterable[dict]) -> dict[str, int]:
    """``{verbatim, redacted, none}`` over *rows* -- what a manifest reports."""
    counts = {"verbatim": 0, "redacted": 0, "none": 0}
    for row in rows:
        quote = row.get("book_quote")
        if is_redacted(quote):
            counts["redacted"] += 1
        elif str(quote or "").strip():
            counts["verbatim"] += 1
        else:
            counts["none"] += 1
    return counts
