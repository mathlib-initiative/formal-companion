"""Thin adapter over ``scripts/cflib`` for the sources/ and pair/ stages.

Ported from: nothing directly -- this is the glue the recovered cells never had
(they inlined ``/Users/jack/Desktop/tao-differential`` and the ``+16`` offset,
e.g. recovered/b174aa13_L5589_Bash.txt, b174aa13_L6099_Bash.txt).

Everything here RESOLVES to ``cflib`` (the shared library: workspace.yaml
loading, state.json, paths, jsonl io, sha256, the strict item grammar).  There
is no fallback half, on purpose: this module is only importable with
``scripts/`` on ``sys.path`` -- which is what ``scripts/cf.py`` and every test
do -- and ``cflib`` lives in that same package root, so cflib is importable
whenever this module is.  A ``try``/``except`` around those imports could
therefore only fire when cflib itself raises AT IMPORT TIME, and it used to
answer an honest bug inside cflib by silently swapping in a second, divergent
item grammar (it kept only the first sub-part of ``Theorem 1.1.1 (i)(a)`` and
truncated ``Theorem 2.3.4.1``, so findings stopped joining to their corpus rows
instead of failing loudly).  An ImportError here is an ImportError.

Nothing in this package ships to a run host: ``scripts/run/manifest.py``
transfers the bridges plus ``scripts/gate/verify_findings.py`` and
``check_changed_files.py``, and those two are the only modules that inline what
they need.

The item grammar is cflib's ``items.try_parse``; :class:`ItemId` is a tiny
view over it (``key`` = the canonical id, ``sub`` = ``(iv)``) that keeps the
sources/pair code readable.  The grammar is per SOURCE: the kind list comes from
``sources.<id>.kinds``, so every entry point here takes one.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from cflib import hashing as _cf_hashing
from cflib import items as _cf_items
from cflib import jsonio as _cf_jsonio
from cflib import paths as _cf_paths
from cflib import state as _cf_state
from cflib import workspace as _cf_workspace
from cflib import yamlio as _cf_yamlio
from cflib.vocab import (  # noqa: F401  (re-exported: the one vocabulary)
    DEFINITIONAL_KEYWORDS,
    DEFINITIONAL_KINDS,
    PAGE_ANCHOR,
)

#: default item kinds; workspace.yaml (``sources.<id>.kinds``) overrides per source
DEFAULT_KINDS: tuple[str, ...] = tuple(_cf_items.DEFAULT_KINDS)


def utcnow() -> str:
    """ISO-8601 UTC timestamp stamped into every artifact."""
    return _cf_state.now()


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------


def write_text(path, text: str) -> None:
    """Write a text file atomically."""
    _cf_jsonio.atomic_write(str(path), text)


def write_json(path, payload: Any, indent: int = 1) -> None:
    """Write one JSON document atomically (indent=1 matches the originals)."""
    _cf_jsonio.write_json(str(path), payload, indent=indent)


def read_json(path, default: Any = None) -> Any:
    """Read one JSON document; ``default`` when the file is absent.

    Not ``cflib.jsonio.read_json``: that one REFUSES a missing file unless a
    default is passed, and these callers read optional artifacts (a meta.json
    that stage A has not written yet) where absent and empty mean the same.
    """
    if not os.path.exists(str(path)):
        return default
    with open(str(path), "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_jsonl(path, skip_meta: bool = True) -> list[dict]:
    """Read a .jsonl file, skipping blanks and (by default) a ``_meta`` row.

    ``skip_meta`` defaults the other way from ``cflib.jsonio.read_jsonl``: the
    sources and pair stages read row tables, never the routed-findings header.
    """
    if not os.path.exists(str(path)):
        return []
    return _cf_jsonio.read_jsonl(str(path), skip_meta=skip_meta)


def write_jsonl(path, rows: Iterable[dict], meta: dict | None = None) -> int:
    """Write rows (optionally behind a ``_meta`` header) atomically."""
    rows = list(rows)
    _cf_jsonio.write_jsonl(str(path), rows, meta=meta)
    return len(rows)


# --------------------------------------------------------------------------
# hashing
# --------------------------------------------------------------------------


def sha256_file(path) -> str:
    """sha256 of a file's bytes."""
    return _cf_hashing.sha256_file(str(path))


def sha256_text(text: str) -> str:
    """sha256 of a unicode string (utf-8)."""
    return _cf_hashing.sha256_text(text)


def finding_id(run_id: str, source_id: str, item: str, kind: str,
               unfaithful_decls: Sequence[str]) -> str:
    """``sha256(run_id, source_id, clean(item), kind, sorted unfaithful_decls)``.

    :func:`cflib.hashing.finding_id` is the single definition; the gate inlines
    the identical algorithm (it runs on a server with no cflib), and the test
    suite asserts the two agree, so a PAGE_ANCHOR row carries the same id the
    gate, the ledger and ``route`` compute.
    """
    decls = sorted({str(d).strip() for d in (unfaithful_decls or []) if str(d).strip()})
    return _cf_hashing.finding_id(run_id, source_id, item, kind, decls)


# --------------------------------------------------------------------------
# yaml / workspace / paths / state
# --------------------------------------------------------------------------


def load_yaml(path) -> Any:
    """Load a YAML file (cflib's reader: PyYAML when present, its subset otherwise)."""
    return _cf_yamlio.read_yaml(str(path))


def workspace_paths(root):
    """``cflib.paths.WorkspacePaths`` for a workspace root."""
    return _cf_paths.WorkspacePaths(Path(str(root)))


def load_workspace(start=None):
    """Load the workspace at/above ``start`` (or $CF_WORKSPACE, or cwd)."""
    return _cf_workspace.Workspace.load(start)


def state_merge(workspace, name: str, section: str, values: dict, top: dict | None = None) -> None:
    """Merge ``values`` into ``state.json[section]`` (and ``top`` at the root)."""
    with _cf_state.update(workspace, name) as st:
        _cf_state.merge_section(st, section, values)
        st.update(top or {})


# --------------------------------------------------------------------------
# item grammar (cflib.items)
# --------------------------------------------------------------------------


def normalise_item(item: Any) -> str:
    """Collapse whitespace / NBSP / dashes in a raw source item id.

    ``"Exercise\\u00a0   1.3.19."`` -> ``"Exercise 1.3.19"``.
    """
    if item is None:
        return ""
    return _cf_items.clean(str(item))


class ItemId:
    """A parsed source item: ``key`` is what N codebases pair on.

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
    # `number_levels` carries EVERY numeric segment: rebuilding the number from
    # chapter/section/number alone would drop the fourth level of a book that
    # numbers `Lemma 2.3.4.1`, which is the truncation the grammar now refuses.
    parts = getattr(obj, "number_levels", None) or tuple(
        x for x in (obj.chapter, obj.section, obj.number) if x is not None
    )
    number = ".".join(str(x) for x in parts)
    return ItemId(
        str(raw), obj.id, obj.kind, number, parts, obj.sub, obj.title,
        getattr(obj, "page", None), getattr(obj, "trailing", ""),
    )


def parse_item(raw: Any, kinds: Sequence[str] = DEFAULT_KINDS) -> ItemId | None:
    """Strict item grammar, whitespace-tolerant; ``None`` when it does not match.

    Callers put every ``None`` on the reject list instead of inventing a key
    (``compare.norm`` happily keyed ``Corollary (Lemma 1.2.5 + Exercise 1.2.4)``).
    """
    return parse_item_reject(raw, kinds)[0]


def parse_item_reject(raw: Any, kinds: Sequence[str] = DEFAULT_KINDS):
    """``(ItemId | None, reason)``: the reason names WHY a label was rejected.

    ``unknown_kind`` is the actionable one -- it means a companion tags a kind
    the SOURCE does not list (``Strategy 2.1.1``), which is a
    ``cf sources set <id> kinds=...`` fix, not junk.
    """
    obj, reject = _cf_items.try_parse(str(raw or ""), tuple(kinds))
    if obj is not None:
        return _from_cf_item(raw, obj), ""
    return None, getattr(reject, "reason", "unparseable")


def item_sort_key(key: str) -> tuple:
    """Sort items by number, then label (compare.py's ``keyorder`` generalised).

    String-in / tuple-out, and total: it must never raise on a key that came
    from a repo tag, because sorting happens before the reject list is applied.

    The dotted run is taken WHOLE, so ``Theorem 2.3.4.10`` sorts after
    ``Theorem 2.3.4.9`` instead of falling back to the label and ordering them
    as strings.  A book that numbers ``Theorem 7`` has no dotted run at all, so a
    single number IMMEDIATELY after the kind counts too -- otherwise every item
    of such a source tied in the 99s and items.json came out in string order
    (``Theorem 10`` before ``Theorem 9``).  Both patterns leave ``In-text p.71``
    where it was, in the 99s with the other unnumbered labels: ``p.71`` has no
    leading digit and nothing but whitespace may sit between kind and number.
    """
    m = re.search(r"(\d+(?:\.\d+)+)", key or "") or _FLAT_NUM.match(key or "")
    nums = tuple(int(x) for x in m.group(1).split(".")) if m else (99, 99, 99)
    nums = nums + (0,) * max(0, 3 - len(nums))
    return (nums, key or "")


#: ``Theorem 7`` / ``Exercise 3(a)`` -- a source numbered per chapter, not per section
_FLAT_NUM = re.compile(r"^[A-Za-z][A-Za-z.\-]*[ \t]+(\d+)(?![\d.])")


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
