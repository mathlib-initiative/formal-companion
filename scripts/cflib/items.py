"""The strict book-item grammar: one parser for every stage.

Ported from the normaliser in the v1 campaign's ``compare.py`` (``norm()``) and
the ITEM regexes of its two extractors, ``extract_ours.py`` and
``extract_teorth.py``.  None of the three are in this repo; what they did is
written down in ``docs/design/stage_map.md`` §1.1, and these are the defects the
design records fixed:

* whitespace tolerance -- ``compare.norm`` used a single-space regex and
  silently dropped ``Exercise    1.3.19`` (docs/design/stage_map.md Stage 1/3);
* a reject list -- ``norm`` fell through to ``return s``, so junk such as
  ``Corollary (Lemma 1.2.5 + Exercise 1.2.4)`` became a first-class key
  (the strict grammar and its reject list; docs/design/stage_map.md S1.1);
* sub-parts -- ``(i)``/``(iv)``/``(3)`` are parsed out instead of being
  glued into the key or thrown away by the title regex;
* trailing code leak -- teorth rows like ``Definition 1.3.8 (Unsigned
  measurable function) def UnsignedMeasurable {d`` keep the item and report
  the leak instead of inventing a new item;
* In-text items -- every qualifier word ("definition", "convention",
  "proof of Lemma 1.1.2(ii)") is accepted, the printed page wins over any
  cross-reference, and page ranges collapse to their first page.

The canonical id is ``"<Kind> <n>[.<n>...]"`` or ``"In-text p.<n>"``; the
sub-part is carried separately so ``Exercise 1.1.6(i)`` and
``Exercise 1.1.6(ii)`` pair on the same book item but stay distinguishable.

How many numeric segments a label may carry is the SOURCE's numbering, not a
constant: ``Theorem 7``, ``Theorem 3.4`` and ``Lemma 2.3.4.1`` are all real
book numbering, and ``cflib.inference.infer_numbering`` reads a source's own
headings to find out which one it uses, and ``cf sources set <id>
numbering=<min>,<max>`` is the override for a book whose deeper labels are too
few to be inferred from.  Callers that know the source pass it
in; ``DEFAULT_NUMBERING`` is what a caller that does not know gets, and it is
the two-or-three-segment grammar this parser has always applied.  A label
DEEPER than the numbering allows is rejected (``too_many_segments``), never
truncated -- truncating ``Theorem 2.3.4.5`` to ``Theorem 2.3.4`` merged every
item of a section onto one audit key with nothing on the reject list.

Two functions normalise a label and they are not interchangeable: ``clean()``
is the ID transformation (whitespace only, hashed by ``finding_id``, frozen),
and ``join_key()`` is the KEY two corpus generations join on (the strict
grammar, applied to in-text labels).  See the section above ``is_in_text``.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .errors import ItemGrammarError

#: Kinds of the Tao measure-theory book; every source overrides them with its own
#: ``sources.<id>.kinds``, which discovery INFERS from the source's headings.
DEFAULT_KINDS: tuple[str, ...] = (
    "Definition", "Lemma", "Theorem", "Proposition", "Corollary",
    "Exercise", "Example", "Remark", "Strategy", "Claim",
)

IN_TEXT = "In-text"

#: Why a raw string was rejected (the reject list the gate reports).
REJECT_EMPTY = "empty"
REJECT_UNKNOWN_KIND = "unknown_kind"
REJECT_NO_NUMBER = "no_number"
REJECT_IN_TEXT_NO_TARGET = "in_text_no_target"
REJECT_UNPARSEABLE = "unparseable"
#: the label carries more numeric segments than the source's numbering has
REJECT_TOO_MANY_SEGMENTS = "too_many_segments"

#: the deepest numbering any source may declare (``Lemma 2.3.4.1``)
MAX_NUMBER_SEGMENTS = 4

#: ``(min, max)`` numeric segments for a caller that does not know the source.
#: Two-or-three is the grammar every existing corpus was keyed with, so this is
#: what keeps a re-parse of the baseline byte-identical; a prepared source
#: carries its own ``numbering`` and passes it in.
DEFAULT_NUMBERING: tuple[int, int] = (2, 3)

_DASHES = {"‐": "-", "‑": "-", "‒": "-", "–": "-",
           "—": "-", "―": "-", "−": "-"}
_SUB_RE = re.compile(r"^(?:[ivxl]{1,6}|\d{1,2}|[a-h])$", re.I)
_PAGE_RE = re.compile(r"(?:pp?\.|pages?\s*|p\s+)\s*(\d+)", re.I)
_IN_TEXT_RE = re.compile(r"^in[-\s]?text\b", re.I)
#: the whole dotted run, however deep -- the depth is judged, never truncated
_SEGMENTS_RE = re.compile(r"^(\d+(?:\.\d+)*)")


def numbering_range(numbering=None) -> tuple[int, int]:
    """``(min, max)`` numeric segments a label of this source may carry.

    Accepts what a source records: ``None`` (use :data:`DEFAULT_NUMBERING`), a
    ``(min, max)`` pair, a single int (exactly that many), or the mapping
    ``items.json``/``meta.json`` store (``{"min_segments": …, "max_segments":
    …}``).  Unusable values -- out of range, non-numeric, an EMPTY mapping --
    fall back rather than raise, because this reads a number off disk that a
    human may have edited.

    The empty mapping is the case that matters: ``items.json`` stores
    ``numbering: {}`` whenever the preparing host had no ``cflib`` to infer
    with, and in exactly that case the table was built with
    :data:`DEFAULT_NUMBERING`.  Collapsing ``{}`` onto its ``min_segments``
    would tell a caller that reads the stored value the source is numbered
    ``(2, 2)``, and every three-segment label in the baseline corpus would come
    back ``too_many_segments`` -- a mass reject out of a missing default.
    """
    if not numbering:
        return DEFAULT_NUMBERING
    try:
        if isinstance(numbering, int):
            lo = hi = numbering
        elif isinstance(numbering, dict):
            lo = int(numbering.get("min_segments") or DEFAULT_NUMBERING[0])
            hi = int(numbering.get("max_segments") or lo)
        else:
            seq = tuple(numbering)
            lo = int(seq[0])
            hi = int(seq[1]) if len(seq) > 1 else lo
        lo = max(1, min(int(lo), MAX_NUMBER_SEGMENTS))
        hi = max(lo, min(int(hi), MAX_NUMBER_SEGMENTS))
    except (TypeError, ValueError):
        return DEFAULT_NUMBERING
    return lo, hi


@dataclass(frozen=True)
class Item:
    """A parsed book item id."""

    id: str                     # canonical id without the sub-part
    kind: str                   # one of the SOURCE's kinds, or "In-text"
    chapter: int | None = None
    section: int | None = None
    number: int | None = None
    levels: tuple[int, ...] = ()  # every numeric segment, however many
    sub: str = ""               # "" or "(iv)" / "(3)" / "(i)(a)"
    page: int | None = None     # printed page, for In-text items
    qualifier: str = ""         # "definition", "proof of Lemma 1.1.2(ii)", ...
    title: str = ""             # parenthetical title, e.g. "Boolean closure"
    trailing: str = ""          # junk after the id (code leak); '' when clean
    raw: str = ""

    @property
    def full(self) -> str:
        """Canonical id including the sub-part: ``Exercise 1.1.6(i)``."""
        return self.id + self.sub

    @property
    def in_text(self) -> bool:
        return self.kind == IN_TEXT

    @property
    def numbered(self) -> bool:
        return self.chapter is not None

    @property
    def number_levels(self) -> tuple[int, ...]:
        """Every numeric segment, for an Item built here or by hand."""
        if self.levels:
            return self.levels
        return tuple(n for n in (self.chapter, self.section, self.number) if n is not None)

    def slug(self, style: str = "underscore") -> str:
        return slug(self, style=style)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "full": self.full, "kind": self.kind,
            "chapter": self.chapter, "section": self.section, "number": self.number,
            "levels": list(self.number_levels),
            "sub": self.sub, "page": self.page, "qualifier": self.qualifier,
            "title": self.title, "trailing": self.trailing, "raw": self.raw,
        }


@dataclass(frozen=True)
class Reject:
    """A raw item string that the grammar refuses, with the reason."""

    raw: str
    reason: str
    context: dict = field(default_factory=dict)


def clean(raw: str) -> str:
    """NFKC, dash and whitespace normalisation; strips wrapping backticks.

    ``Exercise    1.3.19`` and ``Exercise\\t1.3.19`` both become
    ``Exercise 1.3.19``.
    """
    s = unicodedata.normalize("NFKC", raw or "")
    for bad, good in _DASHES.items():
        s = s.replace(bad, good)
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip("`").strip()
    return s.rstrip(" .,;:").strip()


def _canonical_kind(word: str, kinds: Sequence[str]) -> str | None:
    low = word.lower()
    for k in kinds:
        if k.lower() == low:
            return k
    return None


def _take_parens(text: str) -> tuple[str, str]:
    """Split a leading balanced parenthetical off ``text``.

    Returns (inner, rest); ("", text) when text does not start with '('.
    """
    if not text.startswith("("):
        return "", text
    depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[1:i], text[i + 1:].strip()
    return "", text  # unbalanced: treat as trailing junk


def _parse_subs(text: str) -> tuple[str, str]:
    """Consume leading ``(i)``/``(3)``/``(a)`` groups; returns (sub, rest)."""
    subs = []
    rest = text.strip()
    while True:
        inner, after = _take_parens(rest)
        if inner and _SUB_RE.match(inner.strip()):
            tok = inner.strip()
            subs.append(f"({tok.lower() if not tok.isdigit() else tok})")
            rest = after
            continue
        break
    return "".join(subs), rest


def try_parse(raw: str, kinds: Sequence[str] = DEFAULT_KINDS,
              *, numbering=None) -> tuple[Item | None, Reject | None]:
    """Parse a raw item string. Returns (Item, None) or (None, Reject).

    ``numbering`` is the SOURCE's numbering (see :func:`numbering_range`): a
    label with fewer segments than it allows is ``no_number``, and one with more
    is ``too_many_segments``.  The second is the case that used to be silent:
    the old two-or-three-segment regex matched the first three segments of
    ``Theorem 2.3.4.5`` and dropped ``.5`` into ``trailing``, so nineteen
    distinct theorems became one key and nothing was reported anywhere.
    """
    s = clean(raw)
    if not s:
        return None, Reject(raw, REJECT_EMPTY)

    if _IN_TEXT_RE.match(s):
        return _parse_in_text(raw, s, kinds, numbering=numbering)

    m = re.match(r"^([A-Za-z][A-Za-z.\-]*)\s*(.*)$", s, re.S)
    if not m:
        return None, Reject(raw, REJECT_UNPARSEABLE)
    kind = _canonical_kind(m.group(1).rstrip("."), kinds)
    if kind is None:
        return None, Reject(raw, REJECT_UNKNOWN_KIND, {"word": m.group(1), "kinds": list(kinds)})

    rest = m.group(2).strip()
    low, high = numbering_range(numbering)
    num = _SEGMENTS_RE.match(rest)
    levels = tuple(int(x) for x in num.group(1).split(".")) if num else ()
    if len(levels) < low:
        return None, Reject(raw, REJECT_NO_NUMBER,
                            {"kind": kind, "rest": rest[:60], "min_segments": low})
    if len(levels) > high:
        return None, Reject(raw, REJECT_TOO_MANY_SEGMENTS,
                            {"kind": kind, "number": num.group(1),
                             "min_segments": low, "max_segments": high,
                             "segments": len(levels)})
    chapter = levels[0]
    section = levels[1] if len(levels) > 1 else None
    number = levels[2] if len(levels) > 2 else None
    rest = rest[num.end():].strip()
    sub, rest = _parse_subs(rest)
    title, rest = _take_parens(rest)
    if title and _SUB_RE.match(title.strip()):  # a second sub after the title slot
        sub += f"({title.strip().lower()})"
        title = ""
    ident = kind + " " + ".".join(str(n) for n in levels)
    return Item(id=ident, kind=kind, chapter=chapter, section=section, number=number,
                levels=levels, sub=sub, title=title.strip(), trailing=rest.strip(),
                raw=raw), None


def _parse_in_text(raw: str, s: str, kinds: Sequence[str], *, numbering=None):
    """In-text items: the printed page wins; else a named cross-reference."""
    body = _IN_TEXT_RE.sub("", s).strip().lstrip(",").strip()
    page_m = _PAGE_RE.search(s)
    qualifier = re.split(r",|\(|\bbook\b|\bpp?\.", body, maxsplit=1)[0].strip()
    if page_m:
        page = int(page_m.group(1))
        return Item(id=f"{IN_TEXT} p.{page}", kind=IN_TEXT, page=page,
                    qualifier=qualifier, raw=raw), None
    ref = _find_reference(body, kinds, numbering=numbering)
    if ref is not None and ref.numbered:
        return Item(id=f"{IN_TEXT} {ref.full}", kind=IN_TEXT, chapter=ref.chapter,
                    section=ref.section, number=ref.number, levels=ref.levels,
                    qualifier=qualifier, raw=raw), None
    return None, Reject(raw, REJECT_IN_TEXT_NO_TARGET, {"body": body[:60]})


def _mention_re(kinds: Sequence[str], numbering=None) -> "re.Pattern[str]":
    """Where a label of this source's grammar may START inside running prose.

    The pattern follows the source's own numbering, so a book numbered
    ``Theorem 7`` is found as readily as one numbered ``Lemma 1.2.13``.  It only
    LOCATES a candidate: :func:`try_parse` is what decides, so a run the pattern
    finds but the grammar refuses is still a reject and never an item.
    """
    low, _high = numbering_range(numbering)
    digits = r"\d+" + (r"(?:\.\d+)" * (low - 1) if low > 1 else "")
    return re.compile(r"\b(" + "|".join(re.escape(k) for k in kinds) + r")\s+" + digits, re.I)


def find_items(text: str, kinds: Sequence[str] = DEFAULT_KINDS, *,
               numbering=None, limit: int = 0,
               rejects: "list[Reject] | None" = None) -> list[Item]:
    """Every numbered item NAMED anywhere in a blob of prose, in reading order.

    This is the one way to ask "does this paragraph claim a book item?" -- the
    docstring cross-check in ``adapters.attribution_table`` asks it of every
    Lean docstring in a companion, and it must ask it with the grammar and the
    kind list of the SOURCE rather than with a private regex (CLAUDE.md S4:
    one item grammar).  ``adapters.itemtag.find_tag`` answers a narrower
    question -- which item is this docstring's OWN tag -- and requires the tag
    to lead or to be backticked; a docstring that names its exercise in the
    middle of a sentence is invisible to it and visible here.

    ``limit`` counts MENTIONS, not accepted items: only the first ``limit``
    mentions are read, so ``limit=1`` asks "what does the LEADING mention
    claim" -- which is what a caller that wants "what does this declaration
    claim" means.  A mention the grammar refuses is a reject, exactly as in
    :func:`_find_reference`: it is appended to ``rejects`` when the caller
    passes a list (the caller must report it, CLAUDE.md S4), and it is never an
    invitation to reach for the next mention along.  ``Theorem 2.3.4.5
    generalises Lemma 1.2.3`` under a two-to-three-segment numbering claims
    nothing; it does not claim the lemma it cross-references.
    """
    out: list[Item] = []
    body = text or ""
    for seen, m in enumerate(_mention_re(kinds, numbering).finditer(body), start=1):
        item, reject = try_parse(body[m.start():], kinds, numbering=numbering)
        if item is not None and item.numbered:
            out.append(item)
        elif reject is not None and rejects is not None:
            rejects.append(reject)
        if limit and seen >= limit:
            break
    return out


def _find_reference(body: str, kinds: Sequence[str], *, numbering=None) -> Item | None:
    """Find a numbered item named anywhere inside an In-text qualifier.

    ``In-text definition, Lemma 1.2.13(iii)`` and ``In-text step in the proof
    of Lemma 1.2.13(ii)`` both point at a numbered item with no printed page.
    The FIRST candidate decides: a qualifier whose leading mention the grammar
    refuses is a reject, not an invitation to reach for the next mention along.
    """
    m = _mention_re(kinds, numbering).search(body)
    if not m:
        return None
    ref, _ = try_parse(body[m.start():], kinds, numbering=numbering)
    return ref


def parse(raw: str, kinds: Sequence[str] = DEFAULT_KINDS, *, numbering=None) -> Item:
    """Parse or raise ItemGrammarError."""
    item, reject = try_parse(raw, kinds, numbering=numbering)
    if item is None:
        assert reject is not None
        hint = ("fix the tag in the companion repo, or add the kind to "
                "workspace.yaml sources.<id>.kinds")
        if reject.reason == REJECT_TOO_MANY_SEGMENTS:
            # `cf sources refresh --force` would NOT settle this: it re-runs
            # infer_numbering over the same text and reaches the same depth.
            # The only thing that can say "the book really numbers this deep"
            # is the override, which stamps `numbering_method: manual`.
            hint = (f"this source's numbering has at most "
                    f"{reject.context.get('max_segments')} numeric segments; if the book really "
                    f"numbers this deep: cf sources set <id> numbering="
                    f"{reject.context.get('min_segments')},{reject.context.get('segments')}")
        raise ItemGrammarError(raw, reject.reason, hint=hint)
    return item


def normalise(raw: str, kinds: Sequence[str] = DEFAULT_KINDS, with_sub: bool = False,
              *, numbering=None) -> str:
    """Canonical id string (``with_sub`` keeps ``(iv)``). Raises on junk."""
    item = parse(raw, kinds, numbering=numbering)
    return item.full if with_sub else item.id


def parse_all(raws: Iterable[str], kinds: Sequence[str] = DEFAULT_KINDS,
              *, numbering=None) -> tuple[list[Item], list[Reject]]:
    """Bulk parse: (items, rejects). Callers report the reject list, never drop it."""
    items, rejects = [], []
    for raw in raws:
        item, reject = try_parse(raw, kinds, numbering=numbering)
        (items if item is not None else rejects).append(item if item is not None else reject)
    return items, rejects


# --------------------------------------------------------------------------
# the canonical key two corpus GENERATIONS join on
# --------------------------------------------------------------------------
#
# ``clean()`` is the id transformation: it is what ``finding_id`` hashes, it is
# duplicated byte-for-byte in five places (cflib/hashing.py, gate/
# verify_findings.py, sources/compat.py, ledger/compat.py, detect/_compat.py)
# and it MUST NOT change, because changing it re-keys every finding in the
# 2,672-file baseline corpus.  It is also whitespace-only, which is why the
# defect below survived it.
#
# Book items that carry no number are labelled by whoever wrote the label, and
# two generations of the corpus spell them differently:
#
#     v1 corpus (findings*/):  "In-text, book p. 71"
#                              "In-text convention, book p. 29 (Exercise 1.2.5: ...)"
#                              "In-text, Definition 1.4.35"
#     today's extraction:      "In-text p.71"
#                              "In-text p.29"
#                              "In-text Definition 1.4.35"
#
# Exact-match overlap between the two label sets is ZERO, so a new run cannot be
# joined to the baseline on any in-text item -- `union --new-only` calls every
# re-found in-text defect new, the agreed-across-runs report finds no agreement,
# and a regression check scores a re-found defect (the live case: the p.71
# defect on ``RiemannIntegrableOn.complexLebesgueIntegralOn_Icc``) as a miss.
#
# The fix is a SECOND function, not a change to ``clean()``: joining wants the
# canonical item, hashing wants the label as written.  ``join_key`` is the
# canonical item and nothing hashes it.
#
#     numbered labels     -> ``clean(raw)``, byte-identical to today
#     page-keyed in-text  -> ``In-text p.<n>``       (one spelling for both)
#     anchored in-text    -> ``In-text <Kind> <c>.<s>.<n>[(sub)]``
#
# An anchored in-text item is NOT the numbered item it names: ``In-text
# Definition 1.4.35`` is the unnumbered material next to Definition 1.4.35, so
# the two keep separate keys.  A descriptive prefix ("convention", "proof of
# ..."), a trailing parenthetical and a page RANGE never split a key, and a
# printed page always wins over a cross-reference the label happens to mention
# -- so ``In-text, book p. 51 / Definition 1.3.6`` is the p.51 item.
#
# An in-text label the grammar refuses keeps the cleaned label as its own key
# (it is never guessed onto some other item) and comes back with a Reject, so
# the caller reports it instead of dropping it.


def is_in_text(raw: str) -> bool:
    """True when *raw* labels an unnumbered ("in-text") book item."""
    return bool(_IN_TEXT_RE.match(clean(raw)))


def try_join_key(raw: str, kinds: Sequence[str] = DEFAULT_KINDS,
                 *, numbering=None) -> tuple[str, Reject | None]:
    """The key to join *raw* on, and the Reject to report (``None`` when clean).

    The sub-part is KEPT: ``In-text Lemma 1.2.13(ii)`` and ``In-text Lemma
    1.2.13(iii)`` are two anchors, spelled as two items by both corpus
    generations, and collapsing them would merge two items the corpus
    distinguishes.  (A numbered ``Exercise 1.6.27(iii)`` is different: it is a
    part of an item the book itself numbers, and the tally collapses it.)
    """
    s = clean(raw)
    if not s:
        return "", Reject(raw, REJECT_EMPTY)
    if not _IN_TEXT_RE.match(s):
        return s, None                      # numbered/other labels: untouched
    item, reject = _parse_in_text(raw, s, kinds, numbering=numbering)
    if item is None:
        return s, reject                    # reported, never guessed onto an item
    return item.full, None


def join_key(raw: str, kinds: Sequence[str] = DEFAULT_KINDS, *, numbering=None) -> str:
    """:func:`try_join_key`, raising ItemGrammarError on an unjoinable label."""
    key, reject = try_join_key(raw, kinds, numbering=numbering)
    if reject is not None:
        raise ItemGrammarError(raw, reject.reason,
                               hint="an in-text label needs a printed page ('book p. 71') "
                                    "or a named numbered item ('In-text Definition 1.4.35')")
    return key


def join_keys(raws: Iterable[str], kinds: Sequence[str] = DEFAULT_KINDS,
              *, numbering=None) -> tuple[dict[str, str], list[Reject]]:
    """Bulk :func:`try_join_key`: ``({raw: key}, [Reject, ...])``.

    The rejects are the report; the keys still contain every input, so a caller
    that joins on the result loses no row.
    """
    keys: dict[str, str] = {}
    rejects: list[Reject] = []
    for raw in raws:
        text = str(raw)
        if text in keys:
            continue
        key, reject = try_join_key(text, kinds, numbering=numbering)
        keys[text] = key
        if reject is not None:
            rejects.append(reject)
    return keys, rejects


_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8,
          "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15}


def sub_rank(sub: str) -> tuple:
    """Order sub-parts: (i) < (ii) < (iii); (1) < (2); '' first."""
    toks = re.findall(r"\(([^)]*)\)", sub)
    out = []
    for t in toks:
        t = t.strip().lower()
        if t.isdigit():
            out.append((0, int(t)))
        elif t in _ROMAN:
            out.append((0, _ROMAN[t]))
        else:
            out.append((1, t))
    return tuple(out)


def sort_key(item: "Item | str", kinds: Sequence[str] = DEFAULT_KINDS, *, numbering=None) -> tuple:
    """Stable book order: numbered items by their segments, then In-text by page.

    The tuple keeps its six slots and its element types: ``plan/make_tasks.py``
    sorts parseable and unparseable items into one list and supplies a literal
    ``(9, 0, 0, 0, (), item)`` for the second, so a slot that changed shape
    would raise there rather than mis-sort.  Segments past the third ride in the
    sub-part slot, which orders tuples already.
    """
    obj = item if isinstance(item, Item) else parse(item, kinds, numbering=numbering)
    if obj.numbered:
        levels = obj.number_levels
        return (0, levels[0], levels[1] if len(levels) > 1 else -1,
                levels[2] if len(levels) > 2 else -1,
                tuple((0, n) for n in levels[3:]) + sub_rank(obj.sub), obj.kind)
    return (1, obj.page or 0, 0, 0, (), obj.kind)


def slug(item: "Item | str", style: str = "underscore") -> str:
    """Task/finding id slug.

    ``underscore`` reproduces the tend task ids (``corollary-1_3_14``),
    ``dash`` the attack-run finding filenames (``corollary-1-3-14``).
    """
    obj = item if isinstance(item, Item) else parse(item)
    sep = "_" if style == "underscore" else "-"
    if obj.kind == IN_TEXT:
        base = (f"in-text-p{obj.page}" if obj.page is not None
                else re.sub(r"[^a-z0-9]+", "-", obj.id.lower().replace(".", sep)).strip("-"))
    elif obj.numbered:
        base = (obj.kind.lower().replace(" ", "-") + "-"
                + sep.join(str(n) for n in obj.number_levels))
    else:
        base = re.sub(r"[^a-z0-9]+", "-", obj.id.lower()).strip("-")
    if obj.sub:
        base += "-" + "-".join(t.strip().lower() for t in re.findall(r"\(([^)]*)\)", obj.sub))
    return base


def slug_from_raw(raw: str) -> str:
    """The baseline's raw-derived slug (``in-text-book-p-10``), for convert_v1."""
    s = re.sub(r"[^a-z0-9]+", "-", clean(raw).lower()).strip("-")
    return s


def slug_variants(item: "Item | str", raw: str | None = None) -> list[str]:
    """Every filename spelling a baseline corpus may have used for this item."""
    out = [slug(item, "underscore"), slug(item, "dash")]
    if raw:
        out.append(slug_from_raw(raw))
    obj = item if isinstance(item, Item) else parse(item)
    out.append(slug_from_raw(obj.full))
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


# --------------------------------------------------------------------------
# namespacing: an item is the pair (source_id, item)
# --------------------------------------------------------------------------

#: The ONE separator between a source and its item in a single string.
QUALIFIED_SEP = "::"


def qualified_item(source_id: str, item: "Item | str") -> str:
    """``"<source_id>::Exercise 1.3.19"`` -- an item id that cannot collide.

    An item is the pair ``(source_id, item)`` (docs/design/archive/multi-source-model.md
    S6.1): ``Exercise 1.3.19`` means one thing in one source and something else
    in another, so a bare item string is NEVER used as a cross-source key.  Rows
    carry two fields; a task id, a log line or a finding payload -- anywhere only
    one string fits -- carries this.
    """
    label = item.full if isinstance(item, Item) else clean(str(item))
    sid = str(source_id or "").strip()
    if not sid:
        raise ItemGrammarError(str(item), "no source_id",
                               hint="every item is (source_id, item); pass the source it is from")
    return f"{sid}{QUALIFIED_SEP}{label}"


def split_qualified(qualified: str) -> tuple[str, str]:
    """Inverse of :func:`qualified_item`. Raises on a bare item string."""
    text = str(qualified or "")
    if QUALIFIED_SEP not in text:
        raise ItemGrammarError(text, "not a qualified item",
                               hint=f"expected <source_id>{QUALIFIED_SEP}<item>; a bare item has no "
                                    "source and cannot be looked up")
    source_id, _, item = text.partition(QUALIFIED_SEP)
    return source_id.strip(), clean(item)


def source_slug(source_id: str) -> str:
    """A filename-safe spelling of a source id (it is already a slug)."""
    return re.sub(r"[^a-z0-9]+", "-", str(source_id or "").lower()).strip("-")


def qualified_slug(source_id: str, item: "Item | str", style: str = "dash") -> str:
    """``tao-measure-gsm126-published--exercise-1-3-19`` -- a per-source filename."""
    return source_slug(source_id) + "--" + slug(item, style=style)
