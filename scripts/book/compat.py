"""Thin adapter over ``scripts/cflib`` for the book/ and pair/ stages.

Ported from: nothing directly -- this is the glue the recovered cells never had
(they inlined ``/Users/jack/Desktop/tao-differential`` and the ``+16`` offset,
e.g. recovered/b174aa13_L5589_Bash.txt, b174aa13_L6099_Bash.txt).

Everything here PREFERS ``cflib`` (the shared library: campaign.yaml loading,
state.json, paths, jsonl io, sha256, the strict item grammar) and keeps a small
fallback so the book and pair stages stay runnable and testable on their own --
``python3 -m unittest tests.test_book_pair`` must not need a campaign directory.
The fallbacks mirror cflib's API names, so callers do not care which is in use.

The item grammar is cflib's ``items.try_parse``; :class:`ItemId` is a tiny
view over it (``key`` = the canonical id, ``sub`` = ``(iv)``) that keeps the
book/pair code readable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

try:  # the shared library, when the CLI implementer's modules are present
    from cflib import items as _cf_items  # type: ignore
except Exception:  # pragma: no cover - exercised only before cflib lands
    _cf_items = None
try:
    from cflib import hashing as _cf_hashing  # type: ignore
except Exception:  # pragma: no cover
    _cf_hashing = None
try:
    from cflib import jsonio as _cf_jsonio  # type: ignore
except Exception:  # pragma: no cover
    _cf_jsonio = None
try:
    from cflib import yamlio as _cf_yamlio  # type: ignore
except Exception:  # pragma: no cover
    _cf_yamlio = None
try:
    from cflib import paths as _cf_paths  # type: ignore
except Exception:  # pragma: no cover
    _cf_paths = None
try:
    from cflib import campaign as _cf_campaign  # type: ignore
except Exception:  # pragma: no cover
    _cf_campaign = None
try:
    from cflib import state as _cf_state  # type: ignore
except Exception:  # pragma: no cover
    _cf_state = None
try:
    from cflib import vocab as _cf_vocab  # type: ignore
except Exception:  # pragma: no cover
    _cf_vocab = None


def _clean_item_fallback(raw: str) -> str:
    """Stand-alone copy of ``cflib.items.clean`` (see :func:`finding_id`)."""
    s = unicodedata.normalize("NFKC", raw or "")
    for bad in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212":
        s = s.replace(bad, "-")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip().strip("`").strip()
    return s.rstrip(" .,;:").strip()

#: finding kind emitted by pair/anchor_check.py -- mechanical only, never an LLM
PAGE_ANCHOR = getattr(_cf_vocab, "PAGE_ANCHOR", "PAGE_ANCHOR")

#: default item kinds; campaign.yaml (``book.kinds``) overrides per book
DEFAULT_KINDS: tuple[str, ...] = tuple(
    getattr(
        _cf_items,
        "DEFAULT_KINDS",
        (
            "Definition", "Lemma", "Theorem", "Proposition", "Corollary",
            "Exercise", "Example", "Remark", "Strategy", "Claim",
        ),
    )
)


def utcnow() -> str:
    """ISO-8601 UTC timestamp stamped into every artifact."""
    if _cf_state is not None and hasattr(_cf_state, "now"):
        return _cf_state.now()
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------


def write_text(path, text: str) -> None:
    """Write a text file atomically."""
    if _cf_jsonio is not None:
        _cf_jsonio.atomic_write(str(path), text)
        return
    path = str(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = "%s.tmp" % path
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_json(path, payload: Any, indent: int = 1) -> None:
    """Write one JSON document atomically (indent=1 matches the originals)."""
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=indent) + "\n")


def read_json(path, default: Any = None) -> Any:
    """Read one JSON document; ``default`` when the file is absent."""
    if not os.path.exists(str(path)):
        return default
    with open(str(path), "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_jsonl(path, skip_meta: bool = True) -> list[dict]:
    """Read a .jsonl file, skipping blanks and (by default) a ``_meta`` row."""
    if _cf_jsonio is not None and os.path.exists(str(path)):
        return _cf_jsonio.read_jsonl(str(path), skip_meta=skip_meta)
    rows: list[dict] = []
    if not os.path.exists(str(path)):
        return rows
    with open(str(path), "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("%s:%d: %s" % (path, n, exc)) from exc
            if skip_meta and isinstance(row, dict) and "_meta" in row and len(row) == 1:
                continue
            rows.append(row)
    return rows


def write_jsonl(path, rows: Iterable[dict], meta: dict | None = None) -> int:
    """Write rows (optionally behind a ``_meta`` header) atomically."""
    rows = list(rows)
    if _cf_jsonio is not None:
        _cf_jsonio.write_jsonl(str(path), rows, meta=meta)
        return len(rows)
    parts = []
    if meta is not None:
        parts.append(json.dumps({"_meta": meta}, ensure_ascii=False))
    parts += [json.dumps(r, ensure_ascii=False) for r in rows]
    write_text(path, "\n".join(parts) + ("\n" if parts else ""))
    return len(rows)


# --------------------------------------------------------------------------
# hashing
# --------------------------------------------------------------------------


def sha256_file(path) -> str:
    """sha256 of a file's bytes."""
    if _cf_hashing is not None:
        return _cf_hashing.sha256_file(str(path))
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    """sha256 of a unicode string (utf-8)."""
    if _cf_hashing is not None:
        return _cf_hashing.sha256_text(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def finding_id(run_id: str, item: str, kind: str, unfaithful_decls: Sequence[str]) -> str:
    """``sha256(run_id, clean(item), kind, sorted unfaithful_decls)`` (critique.json).

    :func:`cflib.hashing.finding_id` is the single definition; the gate inlines
    the identical algorithm (it runs on a server with no cflib), and the test
    suite asserts the two agree, so a PAGE_ANCHOR row carries the same id the
    gate, the ledger and ``route`` compute.
    """
    decls = sorted({str(d).strip() for d in (unfaithful_decls or []) if str(d).strip()})
    if _cf_hashing is not None and hasattr(_cf_hashing, "finding_id"):
        return _cf_hashing.finding_id(run_id, item, kind, decls)
    payload = "\n".join(
        [str(run_id or ""), _clean_item_fallback(str(item or "")), str(kind or ""), *decls]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# yaml / campaign / paths / state
# --------------------------------------------------------------------------


def load_yaml(path) -> Any:
    """Load a YAML file (cflib's reader, else PyYAML)."""
    if _cf_yamlio is not None:
        return _cf_yamlio.read_yaml(str(path))
    import yaml  # type: ignore

    with open(str(path), "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh.read())


class _FallbackPaths:
    """Mirror of ``cflib.paths.CampaignPaths`` (only what book/pair need)."""

    def __init__(self, root, book_id: str = ""):
        self.root = Path(str(root)).resolve()
        self.book_id = book_id

    @property
    def campaign_yaml(self):
        return self.root / "campaign.yaml"

    @property
    def book_dir(self):
        return self.root / "book" / self.book_id

    @property
    def items_json(self):
        return self.book_dir / "items.json"

    @property
    def book_json(self):
        return self.book_dir / "book.json"

    @property
    def companions_dir(self):
        return self.root / "companions"

    def companion_dir(self, name: str):
        return self.companions_dir / name

    def statements_jsonl(self, name: str):
        return self.companion_dir(name) / "statements.jsonl"

    def state_json(self, name: str):
        return self.companion_dir(name) / "state.json"

    @property
    def pairs_dir(self):
        return self.root / "pairs"

    @property
    def book_txt(self):
        return self.book_dir / "book.txt"

    @property
    def corpus_dir(self):
        return self.root / "corpus"

    def corpus_run_dir(self, run_id: str):
        return self.corpus_dir / run_id

    def list_companions(self) -> list[str]:
        if not self.companions_dir.is_dir():
            return []
        return sorted(p.name for p in self.companions_dir.iterdir() if p.is_dir())


def campaign_paths(root, book_id: str = ""):
    """``CampaignPaths`` for a campaign root (cflib's when available)."""
    if _cf_paths is not None:
        return _cf_paths.CampaignPaths(Path(str(root)), book_id)
    return _FallbackPaths(root, book_id)


class _FallbackCampaign:
    """Mirror of ``cflib.campaign.Campaign`` for standalone use."""

    def __init__(self, root, data: dict):
        self.root = Path(str(root)).resolve()
        self.data = data or {}

    @property
    def paths(self):
        return campaign_paths(self.root, self.book_id)

    @property
    def book(self) -> dict:
        book = self.data.get("book")
        return dict(book) if isinstance(book, dict) else {}

    @property
    def book_id(self) -> str:
        return str(self.book.get("id") or "")

    @property
    def book_sha(self):
        return self.book.get("sha256") or None

    @property
    def offset(self) -> int:
        return int(self.book.get("offset") or 0)

    @property
    def errata_url(self):
        return self.book.get("errata_url") or None

    @property
    def pdf_path(self):
        p = self.book.get("pdf")
        return Path(str(p)).expanduser() if p else None

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(str(k) for k in (self.book.get("kinds") or DEFAULT_KINDS))

    def companion_names(self) -> list[str]:
        comps = self.data.get("companions")
        names: list[str] = []
        if isinstance(comps, list):
            names = [str(c.get("name")) for c in comps if isinstance(c, dict) and c.get("name")]
        elif isinstance(comps, dict):
            names = [str(k) for k in comps]
        for name in self.paths.list_companions():
            if name not in names:
                names.append(name)
        return names

    def require_book_sha(self, sha, where: str) -> None:
        if self.book_sha and sha != self.book_sha:
            raise ValueError("%s: book_sha %r != campaign book.sha256 %r" % (where, sha, self.book_sha))


def load_campaign(start=None):
    """Load the campaign at/above ``start`` (or $CF_CAMPAIGN, or cwd)."""
    if _cf_campaign is not None:
        return _cf_campaign.Campaign.load(start)
    root = _find_campaign_root(start)
    return _FallbackCampaign(root, load_yaml(os.path.join(str(root), "campaign.yaml")) or {})


def _find_campaign_root(start=None) -> Path:
    cur = Path(str(start or os.environ.get("CF_CAMPAIGN") or os.getcwd())).expanduser().resolve()
    for cand in [cur] + list(cur.parents):
        if (cand / "campaign.yaml").exists():
            return cand
    raise FileNotFoundError(
        "no campaign.yaml at or above %s (pass --campaign or set CF_CAMPAIGN)" % cur
    )


def state_merge(campaign, name: str, section: str, values: dict, top: dict | None = None) -> None:
    """Merge ``values`` into ``state.json[section]`` (and ``top`` at the root)."""
    if _cf_state is not None:
        with _cf_state.update(campaign, name) as st:
            _cf_state.merge_section(st, section, values)
            st.update(top or {})
        return
    path = str(campaign.paths.state_json(name))
    st = read_json(path, default=None) or {"name": name}
    body = dict(st.get(section) or {})
    body.update(values)
    body["at"] = utcnow()
    st[section] = body
    st.update(top or {})
    st["updated_at"] = utcnow()
    write_json(path, st, indent=2)


# --------------------------------------------------------------------------
# item grammar (cflib.items, with a small local fallback)
# --------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_SUBPART = re.compile(r"^\(\s*(?:(?P<roman>[ivxlcdm]+)|(?P<num>\d{1,3})|(?P<alpha>[a-z]))\s*\)$", re.I)


def normalise_item(item: Any) -> str:
    """Collapse whitespace / NBSP / dashes in a raw book item id.

    ``"Exercise\\u00a0   1.3.19."`` -> ``"Exercise 1.3.19"``.
    """
    if item is None:
        return ""
    if _cf_items is not None:
        return _cf_items.clean(str(item))
    s = unicodedata.normalize("NFKC", str(item)).replace(" ", " ")
    s = s.replace("–", "-").replace("—", "-")
    return _WS.sub(" ", s).strip().rstrip(" .,;:").strip("`").strip()


class ItemId:
    """A parsed book item: ``key`` is what N codebases pair on.

    A view over ``cflib.items.Item``: ``key`` = ``Item.id`` ("Exercise 1.3.19",
    "In-text p.23"), ``sub`` = ``Item.sub`` ("(iv)"), ``number`` the dotted
    number as a string, ``parts`` its integer tuple.
    """

    __slots__ = ("raw", "key", "kind", "number", "parts", "sub", "title", "page", "trailing")

    def __init__(self, raw, key, kind, number, parts, sub="", title="", page=None, trailing=""):
        self.raw = raw
        self.key = key
        self.kind = kind
        self.number = number
        self.parts = parts
        self.sub = sub
        self.title = title
        self.page = page
        self.trailing = trailing

    @property
    def chapter(self):
        return self.parts[0] if self.parts else None

    @property
    def section(self):
        return self.parts[1] if len(self.parts) > 1 else None

    @property
    def index(self):
        return self.parts[2] if len(self.parts) > 2 else None

    @property
    def is_intext(self) -> bool:
        return self.kind == "In-text"

    def as_dict(self) -> dict:
        return {
            "item": self.key, "kind": self.kind, "number": self.number, "sub": self.sub,
            "title": self.title, "chapter": self.chapter, "section": self.section,
            "index": self.index,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return "ItemId(%r, sub=%r)" % (self.key, self.sub)


def _from_cf_item(raw, obj) -> ItemId:
    parts = tuple(
        x for x in (obj.chapter, obj.section, obj.number) if x is not None
    )
    number = ".".join(str(x) for x in parts)
    return ItemId(
        str(raw), obj.id, obj.kind, number, parts, obj.sub, obj.title,
        getattr(obj, "page", None), getattr(obj, "trailing", ""),
    )


def _canonical_kind(word: str, kinds: Sequence[str]) -> str | None:
    low = word.lower()
    for k in kinds:
        if k.lower() == low:
            return k
    return None


def parse_item(raw: Any, kinds: Sequence[str] = DEFAULT_KINDS) -> ItemId | None:
    """Strict item grammar, whitespace-tolerant; ``None`` when it does not match.

    Callers put every ``None`` on the reject list instead of inventing a key
    (``compare.norm`` happily keyed ``Corollary (Lemma 1.2.5 + Exercise 1.2.4)``).
    """
    return parse_item_reject(raw, kinds)[0]


def parse_item_reject(raw: Any, kinds: Sequence[str] = DEFAULT_KINDS):
    """``(ItemId | None, reason)``: the reason names WHY a label was rejected.

    ``unknown_kind`` is the actionable one -- it means a companion tags a kind
    the campaign does not list (``Strategy 2.1.1``), which is a campaign.yaml
    fix, not junk.
    """
    if _cf_items is not None:
        obj, reject = _cf_items.try_parse(str(raw or ""), tuple(kinds))
        if obj is not None:
            return _from_cf_item(raw, obj), ""
        return None, getattr(reject, "reason", "unparseable")
    obj = _fallback_parse_item(raw, kinds)
    return (obj, "") if obj is not None else (None, "unparseable")


def _fallback_parse_item(raw: Any, kinds: Sequence[str]) -> ItemId | None:
    s = normalise_item(raw)
    if not s:
        return None
    m = re.match(r"^(?P<kind>[A-Za-z-]+)[ ]+(?P<num>\d+(?:\.\d+){1,2})(?P<rest>.*)$", s)
    if m:
        kind = _canonical_kind(m.group("kind"), kinds)
        if kind is None:
            return None
        number = m.group("num")
        parts = tuple(int(x) for x in number.split("."))
        rest = m.group("rest").strip()
        sub, title = "", ""
        while rest.startswith("(") and ")" in rest:
            paren = rest[: rest.index(")") + 1]
            sm = _SUBPART.match(paren)
            if sm and not sub:
                sub = "(%s)" % (sm.group("roman") or sm.group("num") or sm.group("alpha")).lower()
            elif not title:
                title = paren.strip("()")
            rest = rest[len(paren):].strip()
        return ItemId(str(raw), "%s %s" % (kind, number), kind, number, parts, sub, title)
    if re.match(r"^in[- ]?text\b", s, re.I):
        m = re.search(r"(?:pp?\.|pages?)[ ]*(?P<p>\d+)", s, re.I)
        if m:
            page = int(m.group("p"))
            return ItemId(str(raw), "In-text p.%d" % page, "In-text", "", (), page=page)
        alt = "|".join(re.escape(k) for k in kinds)
        m = re.search(r"\b(?P<kind>" + alt + r")[ ]+(?P<num>\d+(?:\.\d+){1,2})", s)
        if m:
            kind = _canonical_kind(m.group("kind"), kinds) or m.group("kind")
            return ItemId(
                str(raw), "In-text %s %s" % (kind, m.group("num")), "In-text", m.group("num"),
                tuple(int(x) for x in m.group("num").split(".")),
            )
    return None


def item_sort_key(key: str) -> tuple:
    """Sort items by number, then label (compare.py's ``keyorder`` generalised).

    String-in / tuple-out, and total: it must never raise on a key that came
    from a repo tag, because sorting happens before the reject list is applied.
    """
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", key or "")
    nums = tuple(int(x) for x in m.groups() if x) if m else (99, 99, 99)
    nums = nums + (0,) * (3 - len(nums))
    return (nums, key or "")


_PAGE_NUM = re.compile(r"(\d+)\s*(?:[-–—]\s*(\d+))?")


def parse_pages(anchor: Any, *, span: int = 3, lo: int = 1, hi: int = 10000) -> list[int]:
    """Printed pages named by a page anchor, tolerantly.

    Handles ``23``, ``"23-24"``, ``"book p. 135"`` and ``None``.  The recovered
    queue builder used ``re.match(r'(\\d+)...')``, which silently dropped every
    ``book p. N`` anchor -- the whole attribution-table convention.  ``span``
    caps how far a range is expanded so a typo cannot fetch 200 pages.
    """
    if anchor is None:
        return []
    m = _PAGE_NUM.search(str(anchor))
    if not m:
        return []
    a = int(m.group(1))
    b = int(m.group(2) or a)
    if not (lo <= a <= hi):
        return []
    return list(range(a, min(b, a + span) + 1))
