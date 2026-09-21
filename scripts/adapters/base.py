"""Statement-table row schema v2 and its mechanical validation.

New code; the field list is docs/design/stage_map.md Stage 2 ("Extract statement
tables"), which generalises the three row shapes that were produced by hand:

  ours.jsonl / teorth.jsonl  (extract_ours.py, extract_teorth.py)
      item, decl, file, line, statement, sorry, page_anchor, repo
  adam.jsonl                 (transcript 11323fe3 L3102)
      the same plus key, label, and a "book p. N" string anchor

v2 adds the fields the downstream gates need: the parsed item id and its
sub-part next to the verbatim tag, a fully qualified ``decl``, the Lean
keyword, definition bodies, the sorry classification, the printed page as an
int with the provenance of the anchor, and the pair ``source_id`` /
``source_sha`` so a row says WHICH source it is about and which bytes of it.

``source_id`` is not decoration.  An item is the pair ``(source_id, item)``
(docs/design/archive/multi-source-model.md S6.1): ``Exercise 1.3.19`` of one source is
not the same item as ``Exercise 1.3.19`` of another, and a row that does not
name its source cannot be paired, gated or tallied.  ``source_sha`` is the
sha256 of that source's PDF bytes, so a row can never be compared against a
different edition -- the mistake that had batch-1's anchor findings judged
against the 2011 draft.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

__all__ = [
    "ROW_FIELDS",
    "ANCHOR_SOURCES",
    "SORRY_CLASSES",
    "make_row",
    "validate_row",
    "validate_rows",
    "row_sort_key",
    "GRANULARITIES",
    "SchemaError",
]

#: Every key of a statement-table row, in the order they are written.
#: ``kind`` is the LEAN kind and ``item_kind`` the BOOK item kind, following
#: cflib.adapters_api.ROW_OPTIONAL_FIELDS; the nine fields that library marks
#: required (item, item_raw, source_id, source_sha, decl, file, line, statement,
#: repo) are all present here.
ROW_FIELDS: tuple[str, ...] = (
    "item_raw",        # the verbatim tag / label from the repo
    "item",            # canonical id: "Exercise 1.3.19" | "In-text p.29"
    "sub",             # "" | "(iv)" | "(3)"
    "item_kind",       # book item kind: Definition | Exercise | In-text | ...
    "source_id",       # which source this row is about; (source_id, item) is the item
    "decl",            # fully qualified name, or example@<file>:<line>
    "kind",            # Lean keyword: theorem | def | example | ...
    "namespace",       # enclosing namespace ("" when none)
    "file",            # repo-relative path
    "line",            # 1-based
    "statement",       # signature, comments stripped, whitespace collapsed
    "body",            # definition bodies only; null for theorems
    "sorry",           # bool
    "sorry_class",     # exercise | gap | none
    "page_anchor",     # printed page as int, or null
    "page_anchor_raw", # the matched text ("book pp. 25-26"), or null
    "anchor_source",   # own | crossref | none
    "repo",            # companion name, e.g. "ours"
    "key",             # is this a key/primary formalisation of the item?
    "anonymous",       # unnameable declaration (example) with a synthetic id
    "private",         # declared `private`
    "granularity",     # item | node (ledger repos before curation)
    "source_sha",      # sha256 of that source's PDF bytes
)

ANCHOR_SOURCES = frozenset({"own", "crossref", "none"})
SORRY_CLASSES = frozenset({"exercise", "gap", "none"})
GRANULARITIES = frozenset({"item", "node"})

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_DECL_RE = re.compile(r"^[^\s]+$")


class SchemaError(ValueError):
    """A statement row does not satisfy schema v2."""


def make_row(**kwargs: Any) -> dict:
    """Build a row with every schema field present and in canonical order."""
    unknown = set(kwargs) - set(ROW_FIELDS)
    if unknown:
        raise SchemaError(f"unknown row fields: {sorted(unknown)}")
    return {field: kwargs.get(field) for field in ROW_FIELDS}


def validate_row(
    row: Any,
    kinds: Sequence[str] | None = None,
    require_source_sha: bool = True,
    index: int | None = None,
) -> list[str]:
    """Return a list of human-readable problems with one row (empty = valid)."""
    where = f"row {index}" if index is not None else "row"
    if not isinstance(row, dict):
        return [f"{where}: not an object"]
    problems: list[str] = []
    missing = [f for f in ROW_FIELDS if f not in row]
    if missing:
        problems.append(f"{where}: missing fields {missing}")
    extra = [k for k in row if k not in ROW_FIELDS]
    if extra:
        problems.append(f"{where}: unknown fields {extra}")

    def need(field: str, ok: bool, what: str) -> None:
        if not ok:
            problems.append(f"{where}: {field}={row.get(field)!r} is not {what}")

    need("item", isinstance(row.get("item"), str) and bool(row.get("item")), "a non-empty string")
    need("item_raw", isinstance(row.get("item_raw"), str) and bool(row.get("item_raw")),
         "a non-empty string")
    need("sub", isinstance(row.get("sub"), str), "a string ('' when absent)")
    need("decl", isinstance(row.get("decl"), str) and bool(_DECL_RE.match(str(row.get("decl", "")))),
         "a whitespace-free declaration id")
    need("item_kind", isinstance(row.get("item_kind"), str) and bool(row.get("item_kind")),
         "a non-empty string")
    # The item half of (source_id, item) is meaningless without the other half.
    need("source_id", isinstance(row.get("source_id"), str) and bool(row.get("source_id")),
         "a non-empty source_id")
    if kinds is not None and isinstance(row.get("item_kind"), str) and row["item_kind"] not in set(kinds):
        problems.append(f"{where}: item_kind={row['item_kind']!r} is not one of {sorted(set(kinds))}")
    need("kind", isinstance(row.get("kind"), str) and bool(row.get("kind")), "a non-empty string")
    need("namespace", isinstance(row.get("namespace"), str), "a string ('' when none)")
    need("granularity", row.get("granularity") in GRANULARITIES, f"one of {sorted(GRANULARITIES)}")
    need("file", isinstance(row.get("file"), str) and bool(row.get("file")), "a non-empty string")
    need("line", isinstance(row.get("line"), int) and not isinstance(row.get("line"), bool)
         and row.get("line", 0) > 0, "a positive integer")
    need("statement", isinstance(row.get("statement"), str) and bool(row.get("statement", "").strip()),
         "a non-empty string")
    need("body", row.get("body") is None or isinstance(row.get("body"), str), "a string or null")
    need("sorry", isinstance(row.get("sorry"), bool), "a boolean")
    need("sorry_class", row.get("sorry_class") in SORRY_CLASSES,
         f"one of {sorted(SORRY_CLASSES)}")
    page = row.get("page_anchor")
    need("page_anchor", page is None or (isinstance(page, int) and not isinstance(page, bool) and page > 0),
         "a positive integer or null")
    need("page_anchor_raw", row.get("page_anchor_raw") is None or isinstance(row.get("page_anchor_raw"), str),
         "a string or null")
    need("anchor_source", row.get("anchor_source") in ANCHOR_SOURCES,
         f"one of {sorted(ANCHOR_SOURCES)}")
    if row.get("anchor_source") == "own" and page is None:
        problems.append(f"{where}: anchor_source='own' but page_anchor is null")
    if row.get("anchor_source") in ("crossref", "none") and page is not None:
        problems.append(f"{where}: anchor_source={row.get('anchor_source')!r} must not carry a page")
    need("repo", isinstance(row.get("repo"), str) and bool(row.get("repo")), "a non-empty string")
    for flag in ("key", "anonymous", "private"):
        need(flag, isinstance(row.get(flag), bool), "a boolean")
    if row.get("sorry") is False and row.get("sorry_class") != "none":
        problems.append(f"{where}: sorry=False but sorry_class={row.get('sorry_class')!r}")
    if row.get("sorry") is True and row.get("sorry_class") == "none":
        problems.append(f"{where}: sorry=True but sorry_class='none'")
    sha = row.get("source_sha")
    if require_source_sha:
        need("source_sha", isinstance(sha, str) and bool(_SHA_RE.match(sha or "")),
             "a sha256 hex digest")
    elif sha is not None:
        need("source_sha", isinstance(sha, str) and bool(_SHA_RE.match(sha)),
             "a sha256 hex digest or null")
    return problems


def validate_rows(
    rows: Iterable[dict],
    kinds: Sequence[str] | None = None,
    require_source_sha: bool = True,
    max_problems: int = 50,
) -> list[str]:
    """Validate a whole table: per-row schema plus per-source uniqueness.

    Uniqueness is keyed on the SOURCE too: one declaration may legitimately
    formalise ``Exercise 1.3.19`` of two different sources, and calling that a
    duplicate would refuse a table the model is built to allow.
    """
    problems: list[str] = []
    seen: dict[tuple, int] = {}
    for i, row in enumerate(rows):
        problems.extend(validate_row(row, kinds=kinds, require_source_sha=require_source_sha,
                                     index=i))
        if isinstance(row, dict):
            key = (row.get("repo"), row.get("source_id"), row.get("item"),
                   row.get("sub"), row.get("decl"))
            if key in seen:
                problems.append(
                    f"row {i}: duplicate (repo, source_id, item, sub, decl) {key!r}, "
                    f"first seen at row {seen[key]}")
            else:
                seen[key] = i
        if len(problems) >= max_problems:
            problems.append(f"... stopped after {max_problems} problems")
            break
    return problems


def row_sort_key(row: dict) -> tuple:
    """Deterministic table order: book order is applied by the caller's parser.

    Falls back to (source_id, file, line, decl) so that two runs over the same
    tree always produce byte-identical statements.jsonl, and a table bound to
    two sources groups by source instead of interleaving them.
    """
    return (str(row.get("source_id") or ""), str(row.get("file") or ""),
            int(row.get("line") or 0), str(row.get("decl") or ""))
