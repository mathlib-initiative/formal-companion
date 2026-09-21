"""def-coverage.<name>.json and uncovered.<name>.json (the cheap proxy check).

Ported from: the v1 campaign's def-coverage cell (Definition coverage: which book
Definitions a companion states with a real ``def``/``structure`` versus only
theorems about someone else's notion -- the "bridge-only" case that hides a
proxy) and its uncovered-items cell (numbered items with no declaration at all).
Neither cell is in this repo.

Generalised per docs/design/stage_map.md Stage 3/8:

* the item universe comes from ``sources/<source_id>/items.json``
  (whitespace-tolerant headings) instead of re-grepping the book text with a
  single-space regex;
* the kind whose coverage is measured, the chapter filter and the definitional
  keyword list are parameters;
* results carry ``source_id`` + ``source_sha`` and land under
  ``pairs/<source_id>/``, so a companion audited against two sources gets two
  coverage files instead of one that silently mixes them;
* they are shaped so ``missing`` / ``bridge_only`` can be emitted as COVERAGE
  and PROXY findings by the detect stage.

The declaration classifier is NOT here: ``cf detect-proxies`` writes the same
``pairs/<source_id>/def-coverage.<name>.json`` and owns the same three keys, so
both commands call ``detect.def_coverage.is_definitional``.  This module keeps
the item universe, the merge and the file layout; it does not decide what a
definitional declaration is.

Baseline (tao-measure-lean @ 2026-09-04, Tao Ch.1): 36 Definitions = 32 stated,
4 bridge-only, 0 missing; 29 numbered items uncovered (28 Remarks + Ex 1.6.18).
"""

from __future__ import annotations

import os
import re
from typing import Mapping, Sequence

from detect.def_coverage import def_signature, is_definitional

from .compat import (
    DEFINITIONAL_KEYWORDS,
    item_sort_key,
    parse_item,
    read_json,
    utcnow,
    write_json,
)
from .compare import row_item

#: a declaration that STATES a notion (as opposed to proving something about it).
#: ONE definition, in ``cflib.vocab``: ``detect/def_coverage.py`` writes the same
#: file and carried its own list, which had ``instance`` and this one did not --
#: so the 32/4/0 agreement between the two commands was luck, not structure.
DEF_KEYWORDS = DEFINITIONAL_KEYWORDS


def def_regex(keywords: Sequence[str] = DEF_KEYWORDS) -> re.Pattern:
    """Regex matching a definitional declaration's signature (L6099 DEFKIND).

    ``detect.def_coverage.def_signature`` IS the regex; this name is kept
    because ``def_coverage()`` below takes a ``keywords`` parameter.
    """
    return def_signature(keywords)


def _book_items(items_json: Mapping, *, kind: str | None, kinds, chapter: int | None) -> list[str]:
    wanted = {kind} if kind else set(kinds or [])
    out = []
    for row in items_json.get("items", []):
        if wanted and row.get("kind") not in wanted:
            continue
        if chapter is not None and row.get("chapter") != chapter:
            continue
        out.append(row["item"])
    return sorted(out, key=item_sort_key)


def _rows_by_item(rows: Sequence[Mapping], kinds) -> dict[str, list[Mapping]]:
    by: dict[str, list[Mapping]] = {}
    for row in rows:
        parsed = parse_item(row_item(row), kinds)
        if parsed is not None:
            by.setdefault(parsed.key, []).append(row)
    return by


def _is_definitional(row: Mapping, pattern: re.Pattern) -> bool:
    """A row states its notion: by its adapter ``kind``, else by its signature.

    This IS ``detect/def_coverage.is_definitional`` -- not "the same test as".
    It used to be a second implementation whose regex allowed at most one
    attribute group and did not list ``local ``, and the two writers of
    ``def-coverage.<name>.json`` then disagreed about rows whose adapter left
    ``kind`` empty.
    """
    return is_definitional(row, pattern)


def def_coverage(
    items_json: Mapping,
    rows: Sequence[Mapping],
    *,
    source_id: str = "",
    kind: str = "Definition",
    chapter: int | None = None,
    keywords: Sequence[str] = DEF_KEYWORDS,
    kinds: Sequence[str] | None = None,
) -> dict:
    """Split ONE source's Definitions into stated / bridge-only / missing."""
    source_id = source_id or str(items_json.get("source_id") or "")
    kinds = list(kinds or items_json.get("kinds") or [])
    defs = _book_items(items_json, kind=kind, kinds=None, chapter=chapter)
    by = _rows_by_item(rows, kinds)
    pattern = def_regex(keywords)
    missing, bridge_only, stated = [], [], []
    bridge_decls: dict[str, list[str]] = {}
    for item in defs:
        rs = by.get(item, [])
        if not rs:
            missing.append(item)
        elif any(_is_definitional(r, pattern) for r in rs):
            stated.append(item)
        else:
            bridge_only.append(item)
            bridge_decls[item] = [r.get("decl") for r in rs][:5]
    return {
        "created": utcnow(),
        "source_id": source_id,
        "source_sha": items_json.get("source_sha", ""),
        "kind": kind,
        "chapter": chapter,
        "keywords": list(keywords),
        "book_items": len(defs),
        "missing": missing,
        "bridge_only": bridge_only,
        "bridge_only_decls": bridge_decls,
        "stated": stated,
    }


def uncovered(
    items_json: Mapping,
    rows: Sequence[Mapping],
    *,
    source_id: str = "",
    kinds: Sequence[str] | None = None,
    chapter: int | None = None,
) -> dict:
    """Numbered items of ONE source with no declaration in this companion at all."""
    source_id = source_id or str(items_json.get("source_id") or "")
    kind_list = list(kinds or items_json.get("kinds") or [])
    universe = _book_items(items_json, kind=None, kinds=kind_list, chapter=chapter)
    have = set(_rows_by_item(rows, kind_list))
    missing = [i for i in universe if i not in have]
    by_kind: dict[str, list[str]] = {}
    for item in missing:
        by_kind.setdefault(item.split()[0], []).append(item)
    return {
        "created": utcnow(),
        "source_id": source_id,
        "source_sha": items_json.get("source_sha", ""),
        "chapter": chapter,
        "kinds": kind_list,
        "book_items": len(universe),
        "count": len(missing),
        "by_kind": {k: len(v) for k, v in sorted(by_kind.items())},
        "items": missing,
    }


def write_coverage(
    out_dir: str, name: str, coverage: Mapping, uncov: Mapping
) -> dict:
    """Write ``pairs/<source_id>/{def-coverage,uncovered}.<name>.json``.

    MERGES rather than overwrites: ``cf detect-proxies`` writes the same file
    with a superset of the keys (``_meta``, ``bridge_only_detail``,
    ``proxy_candidates``), and a later ``cf pair`` used to drop them -- 26 real
    proxy candidates for ``adam`` vanished simply because the two commands ran
    in the other order.  Keys this stage owns are replaced; keys it does not are
    preserved exactly as the detector left them.
    """
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "def-coverage.%s.json" % name)
    merged = dict(read_json(path) or {}) if os.path.exists(path) else {}
    merged.update(dict(coverage))
    merged["written_by"] = "cf pair"
    write_json(path, merged)
    write_json(os.path.join(out_dir, "uncovered.%s.json" % name), dict(uncov))
    return {
        "def_stated": len(coverage.get("stated", [])),
        "def_bridge_only": len(coverage.get("bridge_only", [])),
        "def_missing": len(coverage.get("missing", [])),
        "uncovered": uncov.get("count", 0),
    }
