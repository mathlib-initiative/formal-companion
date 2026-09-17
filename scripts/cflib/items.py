"""The strict book-item grammar: one parser for every stage.

Ported from the normaliser in /Users/jack/Desktop/tao-differential/compare.py
(``norm()``, lines 13-20) and the ITEM regexes of the recovered extractors
(scratchpad/recovered/b174aa13_L5547_Bash.txt ``extract_ours.py`` and
b174aa13_L5564 ``extract_teorth.py``), with the defects the design records
fixed:

* whitespace tolerance -- ``compare.norm`` used a single-space regex and
  silently dropped ``Exercise    1.3.19`` (design/stage_map.md Stage 1/3);
* a reject list -- ``norm`` fell through to ``return s``, so junk such as
  ``Corollary (Lemma 1.2.5 + Exercise 1.2.4)`` became a first-class key
  (design/architecture.md Stage 3 "strict grammar + reject list");
* sub-parts -- ``(i)``/``(iv)``/``(3)`` are parsed out instead of being
  glued into the key or thrown away by the title regex;
* trailing code leak -- teorth rows like ``Definition 1.3.8 (Unsigned
  measurable function) def UnsignedMeasurable {d`` keep the item and report
  the leak instead of inventing a new item;
* In-text items -- every qualifier word ("definition", "convention",
  "proof of Lemma 1.1.2(ii)") is accepted, the printed page wins over any
  cross-reference, and page ranges collapse to their first page.

The canonical id is ``"<Kind> <c>.<s>.<n>"`` or ``"In-text p.<n>"``; the
sub-part is carried separately so ``Exercise 1.1.6(i)`` and
``Exercise 1.1.6(ii)`` pair on the same book item but stay distinguishable.
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

_DASHES = {"‐": "-", "‑": "-", "‒": "-", "–": "-",
           "—": "-", "―": "-", "−": "-"}
_SUB_RE = re.compile(r"^(?:[ivxl]{1,6}|\d{1,2}|[a-h])$", re.I)
_NUM_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_PAGE_RE = re.compile(r"(?:pp?\.|pages?\s*|p\s+)\s*(\d+)", re.I)
_IN_TEXT_RE = re.compile(r"^in[-\s]?text\b", re.I)


@dataclass(frozen=True)
class Item:
    """A parsed book item id."""

    id: str                     # canonical id without the sub-part
    kind: str                   # one of the SOURCE's kinds, or "In-text"
    chapter: int | None = None
    section: int | None = None
    number: int | None = None
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

    def slug(self, style: str = "underscore") -> str:
        return slug(self, style=style)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "full": self.full, "kind": self.kind,
            "chapter": self.chapter, "section": self.section, "number": self.number,
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


def try_parse(raw: str, kinds: Sequence[str] = DEFAULT_KINDS) -> tuple[Item | None, Reject | None]:
    """Parse a raw item string. Returns (Item, None) or (None, Reject)."""
    s = clean(raw)
    if not s:
        return None, Reject(raw, REJECT_EMPTY)

    if _IN_TEXT_RE.match(s):
        return _parse_in_text(raw, s, kinds)

    m = re.match(r"^([A-Za-z][A-Za-z.\-]*)\s*(.*)$", s, re.S)
    if not m:
        return None, Reject(raw, REJECT_UNPARSEABLE)
    kind = _canonical_kind(m.group(1).rstrip("."), kinds)
    if kind is None:
        return None, Reject(raw, REJECT_UNKNOWN_KIND, {"word": m.group(1), "kinds": list(kinds)})

    rest = m.group(2).strip()
    num = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", rest)
    if not num:
        return None, Reject(raw, REJECT_NO_NUMBER, {"kind": kind, "rest": rest[:60]})
    chapter, section, number = (int(num.group(1)), int(num.group(2)),
                                int(num.group(3)) if num.group(3) else None)
    rest = rest[num.end():].strip()
    sub, rest = _parse_subs(rest)
    title, rest = _take_parens(rest)
    if title and _SUB_RE.match(title.strip()):  # a second sub after the title slot
        sub += f"({title.strip().lower()})"
        title = ""
    ident = f"{kind} {chapter}.{section}" + (f".{number}" if number is not None else "")
    return Item(id=ident, kind=kind, chapter=chapter, section=section, number=number,
                sub=sub, title=title.strip(), trailing=rest.strip(), raw=raw), None


def _parse_in_text(raw: str, s: str, kinds: Sequence[str]):
    """In-text items: the printed page wins; else a named cross-reference."""
    body = _IN_TEXT_RE.sub("", s).strip().lstrip(",").strip()
    page_m = _PAGE_RE.search(s)
    qualifier = re.split(r",|\(|\bbook\b|\bpp?\.", body, maxsplit=1)[0].strip()
    if page_m:
        page = int(page_m.group(1))
        return Item(id=f"{IN_TEXT} p.{page}", kind=IN_TEXT, page=page,
                    qualifier=qualifier, raw=raw), None
    ref = _find_reference(body, kinds)
    if ref is not None and ref.numbered:
        return Item(id=f"{IN_TEXT} {ref.full}", kind=IN_TEXT, chapter=ref.chapter,
                    section=ref.section, number=ref.number, qualifier=qualifier,
                    raw=raw), None
    return None, Reject(raw, REJECT_IN_TEXT_NO_TARGET, {"body": body[:60]})


def _find_reference(body: str, kinds: Sequence[str]) -> Item | None:
    """Find a numbered item named anywhere inside an In-text qualifier.

    ``In-text definition, Lemma 1.2.13(iii)`` and ``In-text step in the proof
    of Lemma 1.2.13(ii)`` both point at a numbered item with no printed page.
    """
    pattern = r"\b(" + "|".join(re.escape(k) for k in kinds) + r")\s+\d+\.\d+"
    m = re.search(pattern, body, re.I)
    if not m:
        return None
    ref, _ = try_parse(body[m.start():], kinds)
    return ref


def parse(raw: str, kinds: Sequence[str] = DEFAULT_KINDS) -> Item:
    """Parse or raise ItemGrammarError."""
    item, reject = try_parse(raw, kinds)
    if item is None:
        assert reject is not None
        raise ItemGrammarError(raw, reject.reason,
                               hint="fix the tag in the companion repo, or add the kind to "
                                    "workspace.yaml sources.<id>.kinds")
    return item


def normalise(raw: str, kinds: Sequence[str] = DEFAULT_KINDS, with_sub: bool = False) -> str:
    """Canonical id string (``with_sub`` keeps ``(iv)``). Raises on junk."""
    item = parse(raw, kinds)
    return item.full if with_sub else item.id


def parse_all(raws: Iterable[str], kinds: Sequence[str] = DEFAULT_KINDS) -> tuple[list[Item], list[Reject]]:
    """Bulk parse: (items, rejects). Callers report the reject list, never drop it."""
    items, rejects = [], []
    for raw in raws:
        item, reject = try_parse(raw, kinds)
        (items if item is not None else rejects).append(item if item is not None else reject)
    return items, rejects


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


def sort_key(item: "Item | str", kinds: Sequence[str] = DEFAULT_KINDS) -> tuple:
    """Stable book order: numbered items by c.s.n, then In-text by page."""
    obj = item if isinstance(item, Item) else parse(item, kinds)
    if obj.numbered:
        return (0, obj.chapter or 0, obj.section or 0, obj.number if obj.number is not None else -1,
                sub_rank(obj.sub), obj.kind)
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
        nums = [obj.chapter, obj.section] + ([obj.number] if obj.number is not None else [])
        base = obj.kind.lower().replace(" ", "-") + "-" + sep.join(str(n) for n in nums)
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

    An item is the pair ``(source_id, item)`` (docs/design/multi-source-model.md
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
