"""Lean-tier proxy detection: development-order analysis over ``deps.json``.

A rewrite (not an adaptation) of
/Users/jack/Desktop/tao-differential/adam-detector/analyze2.py, as
adam-detector/README.md:63-73 requires: the closure logic and the two rules are
kept verbatim in spirit, but the item -> declaration mapping now comes from the
campaign's ``statements.jsonl`` instead of one repo's PDF-overlay artifacts
(``target-refinement.json`` + ``declaration-snippets.json`` + four
``geometry-batch-*.json``), and the section of an item is a substring parse of
its id instead of that repo's label -> source_ids -> page fallback chain.

The two rules (analyze2.py L165-196):

``ORDER_VIOLATION``
    a key definition whose *definitional closure* reaches a library constant
    belonging to a **later** book stage than the item it realises -- the book's
    development order is short-circuited.

``SAME_STAGE_PRIMITIVE``
    a key definition whose **direct** dependencies are the library's rendition
    of the *same* section's concept -- the ``elementaryMeasure := volume``
    shape, where the book's own construction is replaced by the thing it was
    supposed to construct.

``MATHLIB_STAGE`` lives in ``detect/mathlib_stages/<book>.json`` (shipped for
tao-measure from analyze2.py L51-120). Modules in the subject's namespace that
the table does not classify are reported and make the run fail unless
``--allow-unclassified`` is passed: an unclassified module silently suppresses
every hit that goes through it (analyze2.py L172-173).
"""
from __future__ import annotations

import argparse
import collections
from pathlib import Path
from typing import Iterable, Sequence

from detect import findings as find_rows
from detect._compat import (
    DEFAULT_KINDS,
    item_base,
    parse_item,
    read_jsonl,
    sort_key,
    write_json,
)
from detect.deps import DepsIndex, is_generated
from detect.stages import StageTable

RULE_ORDER_VIOLATION = "ORDER_VIOLATION"
RULE_SAME_STAGE_PRIMITIVE = "SAME_STAGE_PRIMITIVE"


# --------------------------------------------------------------------------
# item -> key definitional declarations
# --------------------------------------------------------------------------
def key_declarations(
    rows: Iterable[dict],
    deps: DepsIndex,
    key_only: bool = True,
    kinds: Sequence[str] = DEFAULT_KINDS,
) -> tuple[list[tuple[str, str]], list[str]]:
    """``[(item, decl)]`` for the definitional declarations of each book item.

    Replaces analyze2.py L155-162, which read ``key_formalization_ids`` out of
    measure_theory's ``target-refinement.json``. Here the source is the
    statement table: a row is *key* when it says so (``key: true``) and
    definitional when the dependency dump says its constant is not a theorem --
    the dump, not the row text, is ground truth about the kind.

    Returns the pairs (order preserved, duplicates dropped) and the list of
    declarations the statement table names but the dump does not contain, which
    means the table and the built environment are out of step.
    """
    rows = list(rows)
    has_key_field = any("key" in r for r in rows)
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    unknown: list[str] = []
    for row in rows:
        decl = str(row.get("decl") or "")
        if not decl:
            continue
        item = item_base(row.get("item") or row.get("item_raw") or "", kinds)
        if not item:
            continue
        if key_only and has_key_field and not row.get("key", False):
            continue
        if not deps.is_repo(decl):
            if decl not in unknown:
                unknown.append(decl)
            continue
        if deps.kind_of(decl) == "theorem" or is_generated(decl):
            continue
        pair = (item, decl)
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(pair)
    return pairs, unknown


# --------------------------------------------------------------------------
# the two rules
# --------------------------------------------------------------------------
def classify_hits(
    deps: DepsIndex,
    stages: StageTable,
    decl: str,
    item_stage: int,
    unclassified: collections.Counter,
) -> tuple[list[tuple], list[tuple], dict[str, str]]:
    """Split a declaration's closure into later-stage and same-stage hits.

    Ported from analyze2.analyze (L165-178). Returns ``(later, same, parent)``
    where a hit is ``(stage, constant, module, reached_via)``. Stage-0
    (foundations) constants are ignored; unclassified modules are counted.
    """
    ext, _seen, parent = deps.closure(decl, root_follow_value=True)
    hits: list[tuple] = []
    for constant, via in ext.items():
        module = deps.module_of(constant)
        stage = stages.stage_of_module(module)
        if stage is None:
            unclassified[module] += 1
            continue
        if stage == 0:
            continue
        hits.append((stage, constant, module, via))
    later = [h for h in hits if h[0] > item_stage]
    same = [h for h in hits if h[0] == item_stage]
    return later, same, parent


def analyze(
    deps: DepsIndex,
    stages: StageTable,
    rows: Iterable[dict],
    *,
    key_only: bool = True,
    kinds: Sequence[str] = DEFAULT_KINDS,
    max_hits: int = 40,
) -> dict:
    """Run both rules over every key definitional declaration.

    The report has the same three top-level keys as the original
    ``report_keydefs.json`` (``order_violations``, ``same_stage_primitives``,
    ``unclassified_modules``) plus bookkeeping the original printed instead of
    recording.
    """
    pairs, unknown_decls = key_declarations(rows, deps, key_only=key_only, kinds=kinds)
    unclassified: collections.Counter = collections.Counter()
    order_violations: list[dict] = []
    same_stage: list[dict] = []
    without_stage: list[str] = []
    analysed = 0

    for item, decl in pairs:
        item_stage = stages.stage_of_item(item, kinds)
        if item_stage is None:
            if item not in without_stage:
                without_stage.append(item)
            continue
        analysed += 1
        later, same, parent = classify_hits(deps, stages, decl, item_stage, unclassified)
        parsed = parse_item(item, kinds)
        base = {
            "item": item,
            "item_kind": parsed.kind if parsed else None,
            "stage": item_stage,
            "decl": decl,
            "kind": deps.kind_of(decl),
            "module": deps.module_of(decl),
        }
        if later:
            hits = sorted({(s, c, m) for s, c, m, _ in later}, key=lambda x: (-x[0], x[1]))[:max_hits]
            first = later[0]
            order_violations.append(
                {
                    **base,
                    "max_stage": max(h[0] for h in hits),
                    "later_hits": [list(h) for h in hits],
                    "example_path": deps.path_to(first[3], parent, decl) + [first[1]],
                }
            )
        # same-stage primitives that are *direct* deps of the definition itself
        direct = deps.direct_set(decl)
        direct_same = sorted({(s, c, m) for s, c, m, via in same if via == decl and c in direct})
        if direct_same:
            same_stage.append({**base, "direct_same_stage": [list(h) for h in direct_same]})

    order_violations.sort(key=lambda v: (sort_key(v["item"]), v["decl"]))
    same_stage.sort(key=lambda v: (sort_key(v["item"]), v["decl"]))
    return {
        "order_violations": order_violations,
        "same_stage_primitives": same_stage,
        "unclassified_modules": dict(unclassified.most_common()),
        "key_declarations": len(pairs),
        "analysed": analysed,
        "items_without_stage": without_stage,
        "decls_not_in_deps": unknown_decls,
        "book": stages.book,
        "stage_table": str(stages.source) if stages.source else None,
        "deps": str(deps.source) if deps.source else None,
    }


# --------------------------------------------------------------------------
# optional broad scan (analyze2.py L209-233)
# --------------------------------------------------------------------------
def broad_scan(deps: DepsIndex, stages: StageTable, max_hits: int = 12) -> list[dict]:
    """Every repo definition whose closure reaches a later-stage library module.

    Uncurated counterpart of ORDER_VIOLATION: the stage of the *declaration*
    comes from its own module (``repo_module_stages``) rather than from a book
    item, so it covers declarations no item is tagged to. Returns ``[]`` when
    the stage table has no module map for this companion.
    """
    if not stages.repo_module_stages:
        return []
    out: list[dict] = []
    for name, const in deps.repo.items():
        if const.get("kind") == "theorem" or is_generated(name):
            continue
        stage = stages.stage_of_repo_module(const.get("module", ""))
        if stage is None:
            continue
        ext, _seen, parent = deps.closure(name, True)
        later: dict[str, tuple[int, str]] = {}
        for constant, via in ext.items():
            hit_stage = stages.stage_of_module(deps.module_of(constant))
            if hit_stage and hit_stage > stage:
                later[constant] = (hit_stage, via)
        if not later:
            continue
        top = max(s for s, _ in later.values())
        via_top = next(via for _c, (s, via) in later.items() if s == top)
        out.append(
            {
                "decl": name,
                "module": const.get("module"),
                "kind": const.get("kind"),
                "stage": stage,
                "max_stage": top,
                "hits": [list(h) for h in sorted({(s, c) for c, (s, _v) in later.items()},
                                                 key=lambda x: (-x[0], x[1]))[:max_hits]],
                "path": deps.path_to(via_top, parent, name),
            }
        )
    out.sort(key=lambda b: (b["stage"], -b["max_stage"], b["decl"]))
    return out


# --------------------------------------------------------------------------
# finding rows
# --------------------------------------------------------------------------
def _severity(rule: str, hit: dict, stages: StageTable) -> str:
    """Severity heuristic, documented so adjudication can argue with it.

    An order violation that reaches past the end of the book is the strongest
    structural claim; a same-stage primitive under a *Definition* item is the
    content-losing case, while under an Exercise or Theorem item it is usually
    the ambient convention (the §1.2 criteria stated against ``volume``).
    """
    if rule == RULE_ORDER_VIOLATION:
        beyond = stages.stage_beyond_book
        if beyond is not None and hit.get("max_stage") is not None and hit["max_stage"] >= int(beyond):
            return "high"
        return "medium"
    return "medium" if hit.get("item_kind") == "Definition" else "low"


def build_finding_rows(
    report: dict,
    deps: DepsIndex,
    stages: StageTable,
    *,
    companion: str,
    run_id: str,
    book_sha: str | None = None,
) -> list[dict]:
    """Turn ORDER_VIOLATION / SAME_STAGE_PRIMITIVE hits into PROXY rows."""
    out: list[dict] = []
    for hit in report.get("order_violations", []):
        constants = [c for _s, c, _m in hit["later_hits"]]
        bridges = deps.bridge_theorems(hit["decl"], constants)
        top = hit["later_hits"][0]
        out.append(
            find_rows.proxy_row(
                run_id=run_id,
                companion=companion,
                item=hit["item"],
                decl=hit["decl"],
                rule=RULE_ORDER_VIOLATION,
                severity=_severity(RULE_ORDER_VIOLATION, hit, stages),
                concept=f"{hit['item']} is built on stage-{hit['max_stage']} material",
                reasoning=(
                    f"The definitional closure of {hit['decl']} (which realises {hit['item']}, book stage "
                    f"{hit['stage']}) reaches {top[1]} from {top[2]}, developed at stage {top[0]} -- later in "
                    f"the book than the item itself. Path: {' -> '.join(hit['example_path'])}."
                ),
                bridge=DepsIndex.classify_bridge(bridges),
                bridge_decls=bridges,
                evidence={
                    "item_stage": hit["stage"],
                    "max_stage": hit["max_stage"],
                    "hits": hit["later_hits"][:12],
                    "closure_path": hit["example_path"],
                    "module": hit["module"],
                },
                book_sha=book_sha,
                tier="lean",
            )
        )
    for hit in report.get("same_stage_primitives", []):
        constants = [c for _s, c, _m in hit["direct_same_stage"]]
        bridges = deps.bridge_theorems(hit["decl"], constants)
        names = ", ".join(constants[:4])
        out.append(
            find_rows.proxy_row(
                run_id=run_id,
                companion=companion,
                item=hit["item"],
                decl=hit["decl"],
                rule=RULE_SAME_STAGE_PRIMITIVE,
                severity=_severity(RULE_SAME_STAGE_PRIMITIVE, hit, stages),
                concept=f"{hit['item']} is stated through the library's own rendition of the same concept",
                reasoning=(
                    f"{hit['decl']} realises {hit['item']} (book stage {hit['stage']}), and its direct "
                    f"dependencies include the library's stage-{hit['stage']} rendition of that same concept "
                    f"({names}). The book's construction is replaced by the object it was meant to construct, "
                    "so statements about it can hold by construction."
                ),
                bridge=DepsIndex.classify_bridge(bridges),
                bridge_decls=bridges,
                evidence={
                    "item_stage": hit["stage"],
                    "direct_same_stage": hit["direct_same_stage"],
                    "module": hit["module"],
                },
                book_sha=book_sha,
                tier="lean",
            )
        )
    return out


# --------------------------------------------------------------------------
# low-level entry point (the CLI is `cf detect-proxies <name> --lean`)
# --------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    """Run the analysis over explicit paths; for debugging and tests."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deps", required=True, help="deps.json from DumpDeps.lean (outside the repo tree)")
    ap.add_argument("--statements", required=True, help="statements.jsonl for the companion")
    ap.add_argument("--book", help="stage table name, e.g. tao-measure")
    ap.add_argument("--stages", help="explicit path to a stage table json")
    ap.add_argument("--out", help="write the report json here")
    ap.add_argument("--all-decls", action="store_true", help="do not restrict to rows marked key")
    ap.add_argument("--allow-unclassified", action="store_true")
    args = ap.parse_args(argv)

    if not args.stages and not args.book:
        ap.error("pass --book or --stages")
    stages = StageTable.load(args.stages) if args.stages else StageTable.for_book(args.book)
    deps = DepsIndex.load(args.deps)
    rows = read_jsonl(args.statements, skip_meta=True)
    report = analyze(deps, stages, rows, key_only=not args.all_decls)
    if args.out:
        write_json(Path(args.out), report)
    print(
        f"key declarations: {report['key_declarations']}  analysed: {report['analysed']}  "
        f"order violations: {len(report['order_violations'])}  "
        f"same-stage primitives: {len(report['same_stage_primitives'])}"
    )
    if report["unclassified_modules"]:
        print("unclassified modules: " + ", ".join(report["unclassified_modules"]))
        if not args.allow_unclassified:
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
