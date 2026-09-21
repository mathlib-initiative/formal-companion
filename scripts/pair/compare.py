"""N-way statement differential for ONE source: pairs.jsonl, coverage.md, ...

Ported from: the v1 campaign's ``compare.py`` (the three-way Tao differential:
``norm()``, the coverage table, the pairs builder), which is not in this repo.
Generalised per docs/design/stage_map.md Stage 3 and
docs/design/archive/multi-source-model.md S6:

* N companions read from ``<workspace>/companions/<name>/statements.jsonl``
  instead of that script's hard-coded ``('ours','teorth','adam')``;
* ONE SOURCE AT A TIME.  A workspace holds M sources, and a differential is
  possible exactly where two companions' source lists overlap (R1) -- so every
  artifact written here carries ``source_id`` and ``source_sha`` and lands under
  ``pairs/<source_id>/``.  Pairing a row tagged against one edition with a row
  tagged against another is precisely the confusion this model exists to
  prevent: 22 of 40 batch-1 differential findings were pagination noise from two
  editions of one book being treated as one.
* the kind list comes from that source's own headings (``items.json``) plus
  whatever ``sources.<id>.kinds`` adds, never a hard-coded Tao list;
* the item key comes from the STRICT grammar with a reject list -- the original
  ``norm()`` fell through to ``return s, ''`` and happily keyed junk like
  ``Corollary (Lemma 1.2.5 + Exercise 1.2.4)``;
* the run is reconciled against the source's ``items.json`` and the result is
  written to ``pairs/<source_id>/reconciliation.json`` for ``plan/make_tasks.py``
  to gate on (the design critique: the 411-vs-410 drop happened in the task
  generator, so the reconciliation belongs to it -- this stage reports the
  numbers).
"""

from __future__ import annotations

import os
from typing import Mapping, Sequence

from .compat import (
    DEFAULT_KINDS,
    item_sort_key,
    normalise_item,
    parse_item,
    parse_item_reject,
    utcnow,
    write_json,
    write_jsonl,
    write_text,
)

#: row fields carried into pairs.jsonl (v1 and v2 statement rows both work)
CARRY = (
    "decl", "file", "line", "statement", "body", "sorry", "sorry_class",
    "page_anchor", "anchor_source", "kind", "key", "anonymous",
)


def row_item(row: Mapping) -> str:
    """The label a statement row is keyed on.

    A v2 row carries both the adapter's canonical ``item`` and the verbatim
    ``item_raw`` tag; the canonical one wins, so this stage never re-derives a
    key the adapter already resolved.  A v1 row has only ``item`` (the raw tag),
    which parses to the same key.
    """
    return normalise_item(row.get("item") or row.get("item_raw") or "")


def row_item_raw(row: Mapping) -> str:
    """The verbatim tag as written in the repo (for reject lists and prompts)."""
    return normalise_item(row.get("item_raw") or row.get("item") or "")


def rows_for_source(rows: Sequence[Mapping], source_id: str | None) -> list[dict]:
    """The rows of ONE source (R4: every extracted row carries ``source_id``).

    Two shapes exist on disk and both have to work:

    * a table whose rows carry ``source_id`` is filtered strictly -- an unstamped
      row in a MIXED table belongs to no known source and is dropped;
    * a table where NO row carries one is a legacy single-source table (the
      tao-differential baseline is one) and passes through whole.  Filtering it
      to nothing would produce a silently empty differential, which is the exact
      class of quiet zero this skill exists to refuse.
    """
    rows = [dict(r) for r in rows]
    if not source_id:
        return rows
    if not any(r.get("source_id") for r in rows):
        return rows
    return [r for r in rows if str(r.get("source_id") or "") == source_id]


def index_tables(
    tables: Mapping[str, Sequence[Mapping]],
    *,
    source_id: str,
    source_sha: str = "",
    kinds: Sequence[str] = DEFAULT_KINDS,
) -> dict:
    """Group every companion's rows by source item key.

    Returns ``{"index": {key: {repo: [row...]}}, "rejected": [...],
    "intext": {key: {repo: [...]}}}``.  Rows whose label does not match the
    strict grammar go on the reject list with their repo/decl, never into a key.
    Every kept row is stamped with the source it was compared against.
    """
    index: dict[str, dict[str, list[dict]]] = {}
    intext: dict[str, dict[str, list[dict]]] = {}
    rejected: list[dict] = []
    for repo, rows in tables.items():
        for row in rows:
            raw = row_item_raw(row)
            parsed, reason = parse_item_reject(row_item(row), kinds)
            if parsed is None and raw != row_item(row):
                parsed, reason = parse_item_reject(raw, kinds)
            if parsed is None:
                rejected.append(
                    {"source_id": source_id, "repo": repo, "item_raw": raw, "reason": reason,
                     "decl": row.get("decl"), "file": row.get("file"), "line": row.get("line")}
                )
                continue
            out = {k: row[k] for k in CARRY if k in row}
            out["source_id"] = source_id
            out["source_sha"] = source_sha or str(row.get("source_sha") or "")
            out["sub"] = row.get("sub") or parsed.sub
            out["title"] = row.get("title") or parsed.title
            out["item_raw"] = raw
            bucket = intext if parsed.is_intext else index
            bucket.setdefault(parsed.key, {}).setdefault(repo, []).append(out)
    return {"index": index, "intext": intext, "rejected": rejected}


def build_pairs(
    index: Mapping[str, Mapping[str, list]],
    repos: Sequence[str],
    kinds: Sequence[str] = DEFAULT_KINDS,
    *,
    source_id: str = "",
    source_sha: str = "",
) -> list[dict]:
    """Items formalised by >= 2 companions OF THIS SOURCE: the differential queue."""
    pairs = []
    for key in sorted(index, key=item_sort_key):
        present = [r for r in repos if index[key].get(r)]
        if len(present) < 2:
            continue
        parsed = parse_item(key, kinds)
        row = {
            "source_id": source_id,
            "source_sha": source_sha,
            "item": key,
            "kind": parsed.kind if parsed else key.split()[0],
            "number": parsed.number if parsed else "",
            "repos": present,
        }
        for repo in present:
            row[repo] = index[key][repo]
        pairs.append(row)
    return pairs


def coverage_markdown(
    index: Mapping[str, Mapping[str, list]],
    repos: Sequence[str],
    *,
    book_items: Mapping[str, dict] | None = None,
    source_id: str = "",
    source_sha: str = "",
) -> str:
    """The per-item coverage table (compare.py L30-45, plus the source's page)."""
    head = ["# Coverage by source item", "",
            "source: `%s`" % (source_id or "(unnamed)"), "",
            "repos loaded: %s" % ", ".join(repos), ""]
    if source_sha:
        head.append("source sha256: `%s`" % source_sha)
        head.append("")
    cols = ["item"] + list(repos) + (["printed p."] if book_items else [])
    head.append("| " + " | ".join(cols) + " |")
    head.append("|" + "---|" * len(cols))
    for key in sorted(index, key=item_sort_key):
        cells = [str(len(index[key][r])) if index[key].get(r) else "—" for r in repos]
        if book_items is not None:
            entry = book_items.get(key)
            cells.append(str(entry["page"]) if entry else "not in this source")
        head.append("| %s | %s |" % (key, " | ".join(cells)))
    return "\n".join(head) + "\n"


def reconcile(
    index: Mapping[str, Mapping[str, list]],
    repos: Sequence[str],
    *,
    book_items: Mapping[str, dict] | None,
    pairs: Sequence[Mapping],
    rejected: Sequence[Mapping],
    intext: Mapping[str, Mapping[str, list]],
    source_id: str = "",
    source_sha: str = "",
    chapters: Sequence[int] | None = None,
) -> dict:
    """Reconcile the item universe against THIS source's ``items.json``.

    The numbers ``plan/make_tasks.py`` must agree with: ``items.json`` ∩
    any-repo is the task universe for this source, and anything a repo claims
    that the source does not contain is a data defect, not a task.
    """
    keys = set(index)
    in_book = set(book_items or {})
    # ``chapters`` narrows the TASK universe (Tao v1 audits Chapter 1) but never
    # the "is this a real source item?" test: a Chapter-2 tag is out of scope,
    # not junk.
    in_scope = (
        {k for k in in_book if book_items[k].get("chapter") in set(chapters)}
        if (chapters and book_items)
        else set(in_book)
    )
    out = {
        "created": utcnow(),
        "source_id": source_id,
        "source_sha": source_sha,
        "repos": list(repos),
        "items_in_any_repo": len(keys),
        "pairs": len(pairs),
        "rejected_rows": len(rejected),
        "rejected_by_reason": {},
        "intext_keys": len(intext),
        "single_source": {r: sum(1 for k in keys if list(index[k]) == [r]) for r in repos},
        "rows_per_repo": {r: sum(len(index[k].get(r, [])) for k in keys) for r in repos},
    }
    if book_items is not None:
        matched = in_scope & keys
        out.update(
            {
                "chapters": list(chapters) if chapters else None,
                "items_in_book": len(in_book),
                "items_in_scope": len(in_scope),
                "items_in_scope_and_any_repo": len(matched),
                "items_in_scope_uncovered": sorted(in_scope - keys, key=item_sort_key),
                "items_not_in_book": sorted(keys - in_book, key=item_sort_key),
                "items_out_of_scope": sorted((keys & in_book) - in_scope, key=item_sort_key),
            }
        )
        # What plan/make_tasks must reconcile against.  The NAMES, not only the
        # count: a count-only check can say "382 where 383 were expected" but
        # never which item went missing, and naming it is the whole point of the
        # gate (Exercise 1.3.19, lost to a single-space regex).
        out["task_universe"] = len(matched)
        out["task_universe_items"] = sorted(matched, key=item_sort_key)
    return out


def compare(
    tables: Mapping[str, Sequence[Mapping]],
    out_dir: str,
    *,
    source_id: str,
    source_sha: str = "",
    kinds: Sequence[str] = DEFAULT_KINDS,
    book_items: Mapping[str, dict] | None = None,
    chapters: Sequence[int] | None = None,
    write: bool = True,
) -> dict:
    """Run one source's differential into ``pairs/<source_id>/``; returns a summary."""
    repos = list(tables)
    grouped = index_tables(tables, source_id=source_id, source_sha=source_sha, kinds=kinds)
    index, intext, rejected = grouped["index"], grouped["intext"], grouped["rejected"]
    pairs = build_pairs(index, repos, kinds, source_id=source_id, source_sha=source_sha)
    reasons: dict[str, int] = {}
    for row in rejected:
        reasons[row.get("reason", "")] = reasons.get(row.get("reason", ""), 0) + 1
    recon = reconcile(
        index, repos, book_items=book_items, pairs=pairs, rejected=rejected,
        intext=intext, source_id=source_id, source_sha=source_sha, chapters=chapters,
    )
    recon["rejected_by_reason"] = reasons
    if write:
        os.makedirs(out_dir, exist_ok=True)
        meta = {"source_id": source_id, "source_sha": source_sha, "repos": repos,
                "created": utcnow(), "source": "pair/compare.py"}
        write_jsonl(os.path.join(out_dir, "pairs.jsonl"), pairs, meta=meta)
        write_text(
            os.path.join(out_dir, "coverage.md"),
            coverage_markdown(index, repos, book_items=book_items,
                              source_id=source_id, source_sha=source_sha),
        )
        write_json(os.path.join(out_dir, "reconciliation.json"), recon)
        write_json(
            os.path.join(out_dir, "rejected.json"),
            {"created": utcnow(), "source_id": source_id, "source_sha": source_sha,
             "count": len(rejected), "by_reason": reasons,
             "note": ("labels that failed the strict item grammar; unknown_kind usually means "
                      "sources.%(id)s.kinds is missing a kind the companion tags "
                      "(`cf sources set %(id)s kinds=...`)" % {"id": source_id or "<id>"}),
             "rows": list(rejected)},
        )
        write_json(
            os.path.join(out_dir, "index.json"),
            {
                "created": utcnow(),
                "source_id": source_id,
                "source_sha": source_sha,
                "repos": repos,
                "items": {
                    k: {r: [x.get("decl") for x in v] for r, v in sorted(index[k].items())}
                    for k in sorted(index, key=item_sort_key)
                },
                "intext": {
                    k: {r: [x.get("decl") for x in v] for r, v in sorted(intext[k].items())}
                    for k in sorted(intext, key=item_sort_key)
                },
            },
        )
    return {
        "source_id": source_id,
        "source_sha": source_sha,
        "repos": repos,
        "index": index,
        "intext": intext,
        "rejected": rejected,
        "pairs": pairs,
        "reconciliation": recon,
    }


def load_tables(
    paths: Mapping[str, str], *, source_id: str | None = None
) -> dict[str, list[dict]]:
    """Read ``{name: statements.jsonl path}`` into ``{name: rows}``.

    With ``source_id`` only that source's rows are kept (:func:`rows_for_source`),
    so a companion audited against two sources contributes each row to exactly
    one differential.
    """
    from .compat import read_jsonl

    out: dict[str, list[dict]] = {}
    for name, path in paths.items():
        if not os.path.exists(path):
            continue
        out[name] = rows_for_source(read_jsonl(path), source_id)
    return out
