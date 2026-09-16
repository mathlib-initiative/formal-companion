"""Cheap-tier proxy detection: does a book Definition have a native definition?

Ported from the def-coverage cell of transcript b174aa13 (recovered/
b174aa13_L6099_Bash.txt), which produced
/Users/jack/Desktop/tao-differential/def-coverage.ours.json (32 stated,
4 bridge-only, 0 missing), and from the uncovered-items cell (L6145).

adam-detector/README.md:75-79 argues this is the query worth asking first:

    For a Mathlib-first repo (where proxies are a deliberate convention, not a
    bug), the more useful query is not "is there a proxy" but "is there a book
    *definition* item that no declaration states -- only bridges consume?" That
    is the content-losing case, and it is computable from an item->decl table
    alone, with no dependency dump.

Three classes per book Definition item:

``stated``       a def/structure/abbrev/instance/inductive/class states it;
``bridge_only``  the companion tags only theorems to it -- the concept is
                 consumed through bridges but never written down;
``missing``      no declaration is tagged to the item at all.

On top of that, definitional *bodies* are scanned for the proxy shape --
``elementaryMeasure E := (volume E).toNNReal``, a book object defined as
Mathlib's rendition of the same-stage concept (adam.NOTES.md:16-19) -- which
needs no Lean environment because the row already carries the body.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Sequence

from detect import findings as find_rows
from detect._compat import (
    DEFAULT_KINDS,
    item_base,
    parse_item,
    read_json,
    sort_key,
)
from detect.stages import StageTable

#: Lean declaration keywords that *state* a concept (vs assert something about it).
DEFINITIONAL_KEYWORDS = ("def", "abbrev", "structure", "class", "inductive", "instance")

#: The baseline's signature test (recovered L6099 ``DEFKIND``) plus ``instance``
#: and the remaining Lean modifiers. Applied to ``statement`` when a row has no
#: explicit ``kind``.
DEF_SIGNATURE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)*"
    r"(?:private |protected |noncomputable |nonrec |unsafe |partial |scoped |local )*"
    r"(" + "|".join(DEFINITIONAL_KEYWORDS) + r")\b"
)

#: row ``kind`` values (adapters emit these) that count as definitional
DEFINITIONAL_KINDS = {"def", "abbrev", "structure", "class", "inductive", "instance", "opaque"}


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------
def load_items(source, kind: str | None = None, chapter: int | None = None,
               kinds: Sequence[str] = DEFAULT_KINDS) -> list[str]:
    """Read ``items.json`` and return canonical item ids, in book order.

    Tolerant of the shapes Stage 1 may write: a list of strings, a list of
    objects (``item``/``id``/``label``), ``{"items": [...]}``, or a mapping of
    id -> record. Unparseable entries are dropped (they are reported by the
    book stage, not here); ``kind`` filters to e.g. ``"Definition"`` and
    ``chapter`` to the chapter the stage table covers.
    """
    data = read_json(source) if isinstance(source, (str, Path)) else source
    if data is None:
        return []
    if isinstance(data, dict):
        data = data.get("items", data)
    raw_ids: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            raw_ids.append(_item_string(value) or str(key))
    else:
        for entry in data:
            text = _item_string(entry)
            if text:
                raw_ids.append(text)
    seen: dict[str, None] = {}
    for raw in raw_ids:
        parsed = parse_item(raw, kinds)
        if parsed is None:
            continue
        if kind is not None and parsed.kind != kind:
            continue
        if chapter is not None and parsed.chapter != chapter:
            continue
        seen.setdefault(parsed.base, None)
    return sorted(seen, key=sort_key)


def _item_string(entry) -> str | None:
    """Pull the item id out of whatever shape ``items.json`` used."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("item", "id", "label", "full", "raw", "heading"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def index_by_item(rows: Iterable[dict], kinds: Sequence[str] = DEFAULT_KINDS) -> dict[str, list[dict]]:
    """Group statement rows by their base item id (sub-parts collapse)."""
    out: dict[str, list[dict]] = {}
    for row in rows:
        base = item_base(row.get("item") or row.get("item_raw") or "", kinds)
        if base:
            out.setdefault(base, []).append(row)
    return out


def is_definitional(row: dict) -> bool:
    """Does this row *state* a concept?

    Prefers the adapter's ``kind`` field; falls back to the signature regex
    that produced the baseline table, so v1 rows (which carry only
    ``statement``) classify identically.
    """
    kind = (row.get("kind") or "").strip().lower()
    if kind:
        return kind in DEFINITIONAL_KINDS
    return bool(DEF_SIGNATURE.match(row.get("statement") or ""))


# --------------------------------------------------------------------------
# the coverage classification
# --------------------------------------------------------------------------
def compute_def_coverage(
    definition_items: Sequence[str],
    rows: Iterable[dict],
    kinds: Sequence[str] = DEFAULT_KINDS,
) -> dict:
    """Classify every book Definition item as stated / bridge_only / missing.

    Returns the baseline's three lists plus ``bridge_only_detail`` (item -> the
    declarations that do cite it), which is what an adjudicator needs to tell a
    lost definition from a deliberate Mathlib-first convention.
    """
    by_item = index_by_item(rows, kinds)
    stated: list[str] = []
    bridge_only: list[str] = []
    missing: list[str] = []
    detail: dict[str, list[str]] = {}
    for item in definition_items:
        item_rows = by_item.get(item, [])
        if not item_rows:
            missing.append(item)
            continue
        if any(is_definitional(r) for r in item_rows):
            stated.append(item)
        else:
            bridge_only.append(item)
            detail[item] = [str(r.get("decl")) for r in item_rows][:8]
    return {
        "missing": missing,
        "bridge_only": bridge_only,
        "stated": stated,
        "bridge_only_detail": detail,
    }


def uncovered_items(
    all_items: Sequence[str],
    rows: Iterable[dict],
    kinds: Sequence[str] = DEFAULT_KINDS,
) -> list[str]:
    """Numbered book items with no declaration in this companion (L6145).

    Reported as COVERAGE, never queued: for tao-measure-lean all 28 were
    Remarks (design/decisions.json, "Item scope").
    """
    tagged = set(index_by_item(rows, kinds))
    return sorted(set(all_items) - tagged, key=sort_key)


# --------------------------------------------------------------------------
# the proxy shape, from definition bodies
# --------------------------------------------------------------------------
def strip_comments(src: str) -> str:
    """Remove ``/- -/`` blocks and ``--`` line comments.

    Ported from ``extract_ours.py`` (recovered b174aa13_L5547) so a book object
    named only inside a docstring or a ``-- TODO`` note is not read as a body
    dependency.
    """
    out: list[str] = []
    depth = 0
    i = 0
    n = len(src or "")
    while i < n:
        if src.startswith("/-", i):
            depth += 1
            i += 2
            continue
        if src.startswith("-/", i) and depth:
            depth -= 1
            i += 2
            continue
        if depth:
            i += 1
            continue
        if src.startswith("--", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def _token_pattern(label: str) -> re.Pattern:
    """Match a Lean identifier (optionally namespace-qualified) exactly."""
    return re.compile(r"(?<![A-Za-z0-9_'])(?:[A-Za-z_][\w.']*\.)?" + re.escape(label) + r"(?![A-Za-z0-9_'])")


def _qualified_pattern(constant: str) -> re.Pattern:
    """Match only the *qualified* spellings of a library constant.

    Used when the companion defines a declaration of the same short name --
    tao-measure-lean has its own ``volume`` (the book's box volume, S1.1), so a
    bare ``volume`` in one of its bodies is its own definition, not Mathlib's.
    Bare occurrences are then not evidence, and only ``MeasureSpace.volume`` /
    ``MeasureTheory.MeasureSpace.volume`` count.
    """
    parts = constant.split(".")
    suffixes = [".".join(parts[i:]) for i in range(len(parts) - 1)]  # drop the bare name
    alternatives = "|".join(re.escape(s) for s in suffixes) or re.escape(constant)
    return re.compile(r"(?<![A-Za-z0-9_'.])(?:" + alternatives + r")(?![A-Za-z0-9_'])")


def own_declaration_names(rows: Iterable[dict]) -> set[str]:
    """Short names of everything this companion declares (for shadow checks)."""
    names = set()
    for row in rows:
        decl = str(row.get("decl") or "")
        if decl and not decl.startswith("example@"):
            names.add(decl.rsplit(".", 1)[-1])
    return names


def shadowed_object_labels(rows: Iterable[dict], stages: StageTable) -> list[str]:
    """Library object names the companion also defines itself.

    Reported rather than silently handled: a shadowed name narrows the body
    scan to qualified occurrences, so the operator should know the scan for
    that object is weaker than for the others.
    """
    own = own_declaration_names(rows)
    return sorted(label for label in stages.objects_by_label() if label in own)


def definition_term(body: str) -> str:
    """The defining term of a declaration: everything after the first ``:=``.

    ``def elementaryMeasure (E : ElementarySet d) : NNReal := (volume E).toNNReal``
    -> ``(volume E).toNNReal``. Falls back to the whole body for ``structure``
    and ``where``-style definitions, which have no ``:=``.
    """
    text = strip_comments(body or "")
    head, sep, tail = text.partition(":=")
    return (tail if sep else text).strip()


def body_proxy_candidates(
    rows: Iterable[dict],
    stages: StageTable,
    kinds: Sequence[str] = DEFAULT_KINDS,
) -> list[dict]:
    """Definitional rows whose body is a same-or-later-stage foreign object.

    The ``elementaryMeasure := (volume E).toNNReal`` shape: the book asks for an
    intrinsic definition and the repo hands back Mathlib's object, so every
    theorem about it is true by construction rather than by the book's
    argument. Only same-or-later stage objects count -- reaching back to an
    earlier section's concept is ordinary mathematics.

    Needs ``body`` on the row (v2 schema, definitional rows only); rows without
    one are skipped and counted by the caller.
    """
    rows = list(rows)
    objects = stages.objects_by_label()
    own = own_declaration_names(rows)
    # A name the companion defines itself is its own: only qualified spellings
    # of the library constant count (tao-measure-lean's `volume` is the book's
    # box volume, not Mathlib's measure).
    patterns = {
        label: (_qualified_pattern(entry.get("constant", label)) if label in own else _token_pattern(label))
        for label, entry in objects.items()
    }
    native = {token: _token_pattern(token) for token in stages.native_tokens}
    theorem_rows = [r for r in rows if not is_definitional(r)]
    out: list[dict] = []
    for row in rows:
        if not is_definitional(row):
            continue
        body = row.get("body")
        if not body:
            continue
        parsed = parse_item(row.get("item") or "", kinds)
        if parsed is None:
            continue
        item_stage = stages.stage_of_item(parsed.base, kinds)
        if item_stage is None:
            continue
        term = definition_term(body)
        if not term:
            continue
        hits = []
        for label, pattern in patterns.items():
            entry = objects[label]
            stage = entry.get("stage")
            if stage is None or int(stage) < item_stage:
                continue
            if pattern.search(term):
                hits.append({"object": entry.get("constant", label), "label": label, "stage": int(stage)})
        if not hits:
            continue
        native_used = sorted(t for t, pattern in native.items() if pattern.search(term))
        decl = str(row.get("decl"))
        bridges = _row_bridges(theorem_rows, decl, [h["label"] for h in hits], patterns)
        out.append(
            {
                "item": parsed.base,
                "decl": decl,
                "file": row.get("file"),
                "line": row.get("line"),
                "item_stage": item_stage,
                "hits": sorted(hits, key=lambda h: (-h["stage"], h["label"])),
                "native_tokens_in_body": native_used,
                "term": " ".join(term.split())[:240],
                "bridge": _classify_row_bridges(bridges),
                "bridge_decls": bridges,
            }
        )
    return out


def _row_bridges(theorem_rows: Sequence[dict], decl: str, labels: Sequence[str],
                 patterns: dict[str, re.Pattern]) -> list[dict]:
    """Theorem rows whose statement names both the definition and the object.

    Uses the same (possibly qualification-requiring) patterns as the body scan,
    so a shadowed name does not invent a bridge either.
    """
    if not decl:
        return []
    decl_short = decl.rsplit(".", 1)[-1]
    decl_pattern = _token_pattern(decl_short)
    label_patterns = [(label, patterns[label]) for label in labels if label in patterns]
    found: list[dict] = []
    for row in theorem_rows:
        statement = strip_comments(row.get("statement") or "")
        if not decl_pattern.search(statement):
            continue
        relates = [label for label, pattern in label_patterns if pattern.search(statement)]
        if not relates:
            continue
        found.append(
            {
                "decl": str(row.get("decl")),
                "sorry": bool(row.get("sorry")),
                "relates": relates,
                "private": str(row.get("decl", "")).startswith("_private."),
            }
        )
    found.sort(key=lambda b: (b["private"], b["sorry"], b["decl"]))
    return found[:5]


def _classify_row_bridges(bridges: Sequence[dict]) -> str:
    """``proved`` | ``fiat`` | ``none`` from statement-table rows alone."""
    if not bridges:
        return "none"
    if any(not b["sorry"] for b in bridges):
        return "proved"
    return "fiat"


# --------------------------------------------------------------------------
# finding rows
# --------------------------------------------------------------------------
def build_finding_rows(
    coverage: dict,
    candidates: Sequence[dict],
    *,
    companion: str,
    run_id: str,
    book_sha: str | None = None,
    items_pages: dict[str, int] | None = None,
) -> list[dict]:
    """COVERAGE rows for missing/bridge-only definitions, PROXY rows for bodies."""
    pages = items_pages or {}
    out: list[dict] = []
    for item in coverage.get("missing", []):
        out.append(
            find_rows.coverage_row(
                run_id=run_id,
                companion=companion,
                item=item,
                rule="DEFINITION_NOT_STATED",
                severity="medium",
                reasoning=(
                    f"The book states {item} but {companion} tags no declaration to it: the concept is "
                    "neither defined nor used under the book's name."
                ),
                evidence={"printed_page": pages.get(item)},
                book_sha=book_sha,
            )
        )
    detail = coverage.get("bridge_only_detail", {})
    for item in coverage.get("bridge_only", []):
        decls = detail.get(item, [])
        out.append(
            find_rows.coverage_row(
                run_id=run_id,
                companion=companion,
                item=item,
                rule="DEFINITION_BRIDGE_ONLY",
                severity="low",
                reasoning=(
                    f"{item} is a book Definition, but every declaration {companion} tags to it is a theorem "
                    f"({', '.join(decls) or 'none named'}): the concept is consumed through bridges and never "
                    "written down natively. Check whether the defining clause is preserved."
                ),
                decls=decls,
                evidence={"printed_page": pages.get(item), "citing_decls": decls},
                book_sha=book_sha,
            )
        )
    for cand in candidates:
        top = cand["hits"][0]
        out.append(
            find_rows.proxy_row(
                run_id=run_id,
                companion=companion,
                item=cand["item"],
                decl=cand["decl"],
                rule="BODY_PROXY",
                severity="medium" if cand["bridge"] != "proved" else "low",
                concept=f"{cand['item']} is defined as the library object {top['label']}",
                reasoning=(
                    f"{cand['decl']} is the definitional declaration for {cand['item']} (book stage "
                    f"{cand['item_stage']}), and its defining term is the library's {top['label']} "
                    f"(stage {top['stage']}): {cand['term']}. A statement about it is then true by "
                    "construction rather than by the book's argument."
                    + (
                        ""
                        if cand["bridge"] == "none"
                        else f" Bridge: {cand['bridge']} ({', '.join(b['decl'] for b in cand['bridge_decls'])})."
                    )
                ),
                bridge=cand["bridge"],
                bridge_decls=cand["bridge_decls"],
                evidence={
                    "item_stage": cand["item_stage"],
                    "objects": cand["hits"],
                    "term": cand["term"],
                    "native_tokens_in_body": cand["native_tokens_in_body"],
                    "file": cand.get("file"),
                    "line": cand.get("line"),
                },
                book_sha=book_sha,
            )
        )
    return out
