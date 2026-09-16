"""N-way statement differential: pairs.jsonl, coverage.md, reconciliation.json.

Ported from: /Users/jack/Desktop/tao-differential/compare.py (the three-way Tao
differential; ``norm()`` L13-20, the coverage table L30-45, the pairs builder
L46-52).  Generalised per design/stage_map.md Stage 3:

* N companions read from ``<campaign>/companions/<name>/statements.jsonl``
  instead of the hard-coded ``('ours','teorth','adam')`` at L22;
* the kind list and the printed-page offset come from campaign.yaml / book.json;
* the item key comes from the STRICT grammar with a reject list -- the original
  ``norm()`` fell through to ``return s, ''`` and happily keyed junk like
  ``Corollary (Lemma 1.2.5 + Exercise 1.2.4)``;
* the run is reconciled against the book's ``items.json`` and the result is
  written to ``pairs/reconciliation.json`` for ``plan/make_tasks.py`` to gate on
  (design/critique.json: the 411-vs-410 drop happened in the task generator, so
  the reconciliation belongs to it -- this stage reports the numbers).
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
    """The raw item label of a statement row (v2 ``item_raw`` wins over v1 ``item``)."""
    return normalise_item(row.get("item_raw") or row.get("item") or "")


def index_tables(
    tables: Mapping[str, Sequence[Mapping]],
    kinds: Sequence[str] = DEFAULT_KINDS,
) -> dict:
    """Group every companion's rows by book item key.

    Returns ``{"index": {key: {repo: [row...]}}, "rejected": [...],
    "intext": {key: {repo: [...]}}}``.  Rows whose label does not match the
    strict grammar go on the reject list with their repo/decl, never into a key.
    """
    index: dict[str, dict[str, list[dict]]] = {}
    intext: dict[str, dict[str, list[dict]]] = {}
    rejected: list[dict] = []
    for repo, rows in tables.items():
        for row in rows:
            raw = row_item(row)
            parsed, reason = parse_item_reject(raw, kinds)
            if parsed is None:
                rejected.append(
                    {"repo": repo, "item_raw": raw, "reason": reason, "decl": row.get("decl"),
                     "file": row.get("file"), "line": row.get("line")}
                )
                continue
            out = {k: row[k] for k in CARRY if k in row}
            out["sub"] = row.get("sub") or parsed.sub
            out["title"] = row.get("title") or parsed.title
            out["item_raw"] = raw
            bucket = intext if parsed.is_intext else index
            bucket.setdefault(parsed.key, {}).setdefault(repo, []).append(out)
    return {"index": index, "intext": intext, "rejected": rejected}


def build_pairs(index: Mapping[str, Mapping[str, list]], repos: Sequence[str], kinds) -> list[dict]:
    """Items formalised by >= 2 companions: the differential queue."""
    pairs = []
    for key in sorted(index, key=item_sort_key):
        present = [r for r in repos if index[key].get(r)]
        if len(present) < 2:
            continue
        parsed = parse_item(key, kinds)
        row = {
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
    book_sha: str = "",
) -> str:
    """The per-item coverage table (compare.py L30-45, plus a book column)."""
    head = ["# Coverage by book item", "", "repos loaded: %s" % ", ".join(repos), ""]
    if book_sha:
        head.append("book sha256: `%s`" % book_sha)
        head.append("")
    cols = ["item"] + list(repos) + (["book p."] if book_items else [])
    head.append("| " + " | ".join(cols) + " |")
    head.append("|" + "---|" * len(cols))
    for key in sorted(index, key=item_sort_key):
        cells = [str(len(index[key][r])) if index[key].get(r) else "—" for r in repos]
        if book_items is not None:
            entry = book_items.get(key)
            cells.append(str(entry["page"]) if entry else "not in book")
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
    book_sha: str = "",
    chapters: Sequence[int] | None = None,
) -> dict:
    """Reconcile the item universe against the book's ``items.json``.

    The numbers ``plan/make_tasks.py`` must agree with: ``items.json`` ∩
    any-repo is the task universe, and anything a repo claims that the book does
    not contain is a data defect, not a task.
    """
    keys = set(index)
    in_book = set(book_items or {})
    # ``chapters`` narrows the TASK universe (Tao v1 audits Chapter 1) but never
    # the "is this a real book item?" test: a Chapter-2 tag is out of scope, not junk.
    in_scope = (
        {k for k in in_book if book_items[k].get("chapter") in set(chapters)}
        if (chapters and book_items)
        else set(in_book)
    )
    out = {
        "created": utcnow(),
        "book_sha": book_sha,
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
        # what plan/make_tasks must reconcile its task count against
        out["task_universe"] = len(matched)
    return out


def compare(
    tables: Mapping[str, Sequence[Mapping]],
    out_dir: str,
    *,
    kinds: Sequence[str] = DEFAULT_KINDS,
    book_items: Mapping[str, dict] | None = None,
    book_sha: str = "",
    chapters: Sequence[int] | None = None,
    write: bool = True,
) -> dict:
    """Run the differential and write ``pairs/`` artifacts; returns a summary."""
    repos = list(tables)
    grouped = index_tables(tables, kinds)
    index, intext, rejected = grouped["index"], grouped["intext"], grouped["rejected"]
    pairs = build_pairs(index, repos, kinds)
    reasons: dict[str, int] = {}
    for row in rejected:
        reasons[row.get("reason", "")] = reasons.get(row.get("reason", ""), 0) + 1
    recon = reconcile(
        index, repos, book_items=book_items, pairs=pairs, rejected=rejected,
        intext=intext, book_sha=book_sha, chapters=chapters,
    )
    recon["rejected_by_reason"] = reasons
    if write:
        os.makedirs(out_dir, exist_ok=True)
        meta = {"book_sha": book_sha, "repos": repos, "created": utcnow(),
                "source": "pair/compare.py"}
        write_jsonl(os.path.join(out_dir, "pairs.jsonl"), pairs, meta=meta)
        write_text(
            os.path.join(out_dir, "coverage.md"),
            coverage_markdown(index, repos, book_items=book_items, book_sha=book_sha),
        )
        write_json(os.path.join(out_dir, "reconciliation.json"), recon)
        reasons: dict[str, int] = {}
        for row in rejected:
            reasons[row.get("reason", "")] = reasons.get(row.get("reason", ""), 0) + 1
        write_json(
            os.path.join(out_dir, "rejected.json"),
            {"created": utcnow(), "count": len(rejected), "by_reason": reasons,
             "note": ("labels that failed the strict item grammar; unknown_kind usually means "
                      "campaign.yaml book.kinds is missing a kind the companion tags"),
             "rows": list(rejected)},
        )
        write_json(
            os.path.join(out_dir, "index.json"),
            {
                "created": utcnow(),
                "book_sha": book_sha,
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
        "repos": repos,
        "index": index,
        "intext": intext,
        "rejected": rejected,
        "pairs": pairs,
        "reconciliation": recon,
    }


def load_tables(paths: Mapping[str, str]) -> dict[str, list[dict]]:
    """Read ``{name: statements.jsonl path}`` into ``{name: rows}``."""
    from .compat import read_jsonl

    return {name: read_jsonl(path) for name, path in paths.items() if os.path.exists(path)}
