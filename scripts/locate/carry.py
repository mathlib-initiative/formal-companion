"""The carry-over map: what a re-cut does to each passage of the old cut of a page.

New code, implementing docs/proposals/statement-cut.md section 4 (the revision-free
keys of section 4.1, the map of section 4.2, the document of section 4.3 and what
carries, section 4.4) in the shape references/locate-documents.md section 12 fixes.
The maintainer's answer of 2026-10-21 makes a re-cut a routine, versioned operation:
a verdict, a gold label or a known answer carries automatically wherever its
passage's text is unchanged, and only the changed passages are asked again.

**The map** is computed from spans and span text only -- never from an item, a
statement, a model or a guess -- between two inventory runs' cuts of one page, at
one revision or two:

1. every passage of both cuts is keyed (``cflib.hashing.passage_key`` of the page
   and the sha256 of its span's text); two passages of one cut with one key refuse
   the page, since no carry could tell them apart;
2. an old passage whose key a new passage has is ``unchanged``;
3. any other is placed on the new layer: at its own span when both cuts read one
   layer (the same ``layer_sha256``), else at the one occurrence of its text in the
   new layer; a text the new layer does not hold exactly once is ``changed``;
4. with M the new passages that share a character with the placed span: none is
   ``removed``; two or more is ``split``; one that starts where it starts is
   ``end_moved``; one that starts before it and holds the whole of it is
   ``merged``; one otherwise is ``reshaped``;
5. a new passage that no old passage went into (no old key is its key, and it
   shares no character with a placed old passage) is ``new``.

:func:`map_page` is that function over two :class:`Cut` values and is all a
consumer's port needs (``tests/data/locate/carry-parity.json`` holds the shared
cases); :func:`build` reads two harvested inventory runs through their owners
(``locate.inputs``: the gated documents, the pinned sourceset, each source's
layer, whose sha must be the one the document was made against) and writes
nothing; :func:`write` puts the document at ``reports/locate-carry/<from>/<to>.json``;
:func:`carry_rows` gives each row of a JSON-lines file keyed by ``passage_key`` its
fate.  The document holds no time and no path, so a re-run writes the same bytes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from cflib import hashing, vocab
from cflib.errors import CfError
from cflib.jsonio import atomic_write, read_json, write_json
from cflib.paths import WorkspacePaths, skill_root

from . import inputs
from . import outcomes as outcomes_mod

CARRY_SCHEMA = "cf-locate-carry/1"


class CarryRefused(Exception):
    """A page the map cannot be computed for; its reason goes into ``refused``."""


@dataclass
class Cut:
    """One inventory's cut of one page: its layer and its passages.

    ``passages`` are the inventory document's rows as written (every scope), each
    with at least ``item`` and ``span``; ``scope`` and ``conjuncts`` are read when
    present.
    """

    pageid: int
    source_id: str
    layer: str
    layer_sha256: str
    passages: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class _Keyed:
    key: str
    item: str
    span: tuple[int, int]
    scope: str | None
    conjuncts: str

    def ref(self) -> dict:
        return {"passage_key": self.key, "item": self.item, "span": list(self.span)}


def conjuncts_sha256(passage: dict) -> str:
    """The sha256 of a passage's canonical conjunct list (``[]`` for a simple statement)."""
    conjuncts = passage.get("conjuncts")
    return hashing.sha256_json(conjuncts if isinstance(conjuncts, list) else [])


def _span(value: Any, layer: str) -> tuple[int, int] | None:
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(x) is int for x in value) and 0 <= value[0] < value[1] <= len(layer)):
        return value[0], value[1]
    return None


def _keyed(cut: Cut, which: str) -> list[_Keyed]:
    """Every passage of a cut with its key, ordered by span; refuse what cannot be keyed."""
    out: list[_Keyed] = []
    seen: dict[str, str] = {}
    for row in cut.passages:
        item = str(row.get("item") or "")
        span = _span(row.get("span"), cut.layer)
        if not item or span is None:
            raise CarryRefused(f"passage {item or '(no item)'} of the {which} cut has no span "
                               f"inside its layer ({row.get('span')!r})")
        key = hashing.passage_key(cut.pageid, hashing.span_sha256(cut.layer, list(span)))
        if key in seen:
            raise CarryRefused(f"two passages of the {which} cut share one passage_key "
                               f"({seen[key]} and {item}: the same text)")
        seen[key] = item
        out.append(_Keyed(key, item, span, row.get("scope"), conjuncts_sha256(row)))
    return sorted(out, key=lambda p: (p.span, p.item))


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _occurrences(layer: str, text: str, limit: int = 2) -> list[int]:
    """Where ``text`` starts in ``layer``, overlapping matches counted, up to ``limit``."""
    found: list[int] = []
    at = layer.find(text)
    while at != -1 and len(found) < limit:
        found.append(at)
        at = layer.find(text, at + 1)
    return found


def _fate(placed: tuple[int, int], into: list[_Keyed]) -> str:
    if not into:
        return vocab.CARRY_REMOVED
    if len(into) > 1:
        return vocab.CARRY_SPLIT
    start, end = into[0].span
    if start == placed[0]:
        return vocab.CARRY_END_MOVED
    if start < placed[0] and end >= placed[1]:
        return vocab.CARRY_MERGED
    return vocab.CARRY_RESHAPED


def _counts(fates: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for fate in fates:
        out[fate] = out.get(fate, 0) + 1
    return dict(sorted(out.items()))


def map_page(old: Cut, new: Cut) -> dict:
    """The map of one page between two cuts (section 4.2 of the proposal).

    Returns the page's entry of a ``cf-locate-carry/1`` document (its ``pageid``,
    each cut's ``source_id``, ``layer_sha256`` and ``cut_id``, ``same_layer``, the old
    passages' fates, the new passages and the counts); raises :class:`CarryRefused`
    when either cut cannot be keyed.
    """
    if old.pageid != new.pageid:
        raise CarryRefused(f"the old cut is of page {old.pageid} and the new one of page "
                           f"{new.pageid}")
    olds, news = _keyed(old, "old"), _keyed(new, "new")
    by_key = {p.key: p for p in news}
    same_layer = old.layer_sha256 == new.layer_sha256
    entered: set[str] = set()
    entries = []
    for o in olds:
        twin = by_key.get(o.key)
        if twin is not None:
            fate, into = vocab.CARRY_UNCHANGED, [twin]
        else:
            if same_layer:
                placed: tuple[int, int] | None = o.span
            else:
                text = old.layer[o.span[0]:o.span[1]]
                hits = _occurrences(new.layer, text)
                placed = (hits[0], hits[0] + len(text)) if len(hits) == 1 else None
            if placed is None:
                fate, into = vocab.CARRY_CHANGED, []
            else:
                into = [n for n in news if _overlaps(n.span, placed)]
                fate = _fate(placed, into)
        entered.update(n.key for n in into)
        entries.append({
            "passage_key": o.key, "item": o.item, "span": list(o.span), "scope": o.scope,
            "fate": fate, "into": [n.ref() for n in into],
            # nothing went in: nothing to compare (a removed or changed passage)
            "scope_changed": any(n.scope != o.scope for n in into) if into else None,
            "conjuncts_changed": any(n.conjuncts != o.conjuncts for n in into) if into else None})
    fresh = [{**n.ref(), "scope": n.scope, "fate": vocab.CARRY_NEW}
             for n in news if n.key not in entered]
    return {
        "pageid": old.pageid,
        "old": {"source_id": old.source_id, "layer_sha256": old.layer_sha256,
                "cut_id": hashing.cut_id(old.pageid, old.layer_sha256,
                                         [list(p.span) for p in olds])},
        "new": {"source_id": new.source_id, "layer_sha256": new.layer_sha256,
                "cut_id": hashing.cut_id(new.pageid, new.layer_sha256,
                                         [list(p.span) for p in news])},
        "same_layer": same_layer, "passages": entries, "new_passages": fresh,
        "counts": _counts([e["fate"] for e in entries] + [vocab.CARRY_NEW] * len(fresh))}


# --------------------------------------------------------------------------
# two harvested inventory runs
# --------------------------------------------------------------------------

def _rule(gate: Any, key: str) -> int:
    """A version a gate.json names (0 when it names none, as a run planned before it)."""
    value = gate.get(key) if isinstance(gate, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


@dataclass
class _Side:
    run_id: str
    rules: dict
    manifest: Any
    #: {pageid: [(source_id, document)]}
    pages: dict[int, list[tuple[str, dict]]]
    layers: dict[str, str] = field(default_factory=dict)

    def layer(self, paths: WorkspacePaths, sid: str) -> str:
        if sid not in self.layers:
            self.layers[sid] = inputs.source_view(paths, self.manifest.sourceset_id, sid).layer()
        return self.layers[sid]


def _side(paths: WorkspacePaths, run_id: str) -> _Side:
    """A gated inventory run: its documents by page, its sourceset pin and its rules."""
    run = outcomes_mod.run_yaml(paths, run_id)
    locate = run["locate"]
    if locate.get("mode") != vocab.LOCATE_INVENTORY:
        raise CfError(f"run {run_id!r} is a {locate.get('mode')!r} run, not an inventory",
                      hint="a carry-over map is between two cuts: two locate-inventory runs")
    pin = locate.get("sourceset")
    if not isinstance(pin, dict):
        raise CfError(f"inventory run {run_id!r} pins no sourceset in its run.yaml")
    manifest = inputs.pinned_manifest(paths, pin)
    run_dir = paths.corpus_run_dir(run_id)
    inputs.require_gated(run_dir, run_id, "inventory run")
    gate_path = paths.gate_json(run_id)
    gate = read_json(gate_path) if gate_path.is_file() else {}
    pages: dict[int, list[tuple[str, dict]]] = {}
    seen: dict[str, str] = {}
    for rel, doc in inputs.harvested_documents(run_dir):
        if doc.get("mode") != vocab.LOCATE_INVENTORY:
            raise CfError(f"{rel} in inventory run {run_id} is a {doc.get('mode')!r} document")
        sid = str(doc.get("source_id") or "")
        if not sid or not rel.endswith(f"/{sid}.json"):
            raise CfError(f"{rel} in inventory run {run_id}: a document lives at "
                          f"<task-id>/<its source_id>.json")
        if sid in seen:
            raise CfError(f"inventory run {run_id} holds two documents for {sid}: "
                          f"{seen[sid]} and {rel}")
        seen[sid] = rel
        entry = manifest.get(sid)
        if entry is None:
            raise CfError(f"{rel}: {sid} is not in the sourceset manifest inventory run "
                          f"{run_id} pinned")
        pages.setdefault(int(entry["pageid"]), []).append((sid, doc))
    return _Side(run_id, {"span_end_rule": _rule(gate, "span_end_rule"),
                          "statement_cut_rule": _rule(gate, "statement_cut_rule")},
                 manifest, pages)


def _cut(paths: WorkspacePaths, side: _Side, pageid: int, which: str) -> Cut:
    docs = side.pages[pageid]
    if len(docs) != 1:
        raise CarryRefused(f"the {which} inventory ({side.run_id}) holds {len(docs)} sources of "
                           f"the page: " + ", ".join(sorted(s for s, _ in docs)))
    sid, doc = docs[0]
    if isinstance(doc.get("unresolved"), dict):
        raise CarryRefused(f"the {which} inventory ({side.run_id}) left {sid} unresolved "
                           f"({doc['unresolved'].get('reason', '')})")
    try:
        layer = side.layer(paths, sid)
    except CfError as exc:
        raise CarryRefused(f"the {which} layer of {sid} cannot be read: {exc}") from None
    sha = hashing.sha256_text(layer)
    if doc.get("layer_sha256") != sha:
        # stamp the source: spans are offsets into ONE layer, so a document made
        # against another is never read against this one
        raise CarryRefused(f"the {which} inventory's document for {sid} was made against layer "
                           f"{str(doc.get('layer_sha256'))[:12]}…, and the sourceset's layer "
                           f"is {sha[:12]}…")
    rows = doc.get("passages")
    return Cut(pageid, sid, layer, sha, [dict(r) for r in rows or [] if isinstance(r, dict)])


def _pages_of(sides: tuple[_Side, _Side], source_ids: Iterable[str]) -> set[int]:
    wanted: set[int] = set()
    for sid in source_ids:
        found = {pageid for side in sides for pageid, docs in side.pages.items()
                 if any(s == sid for s, _ in docs)}
        if not found:
            raise CfError(f"neither {sides[0].run_id} nor {sides[1].run_id} inventoried {sid}",
                          hint="name a source id one of the two runs holds a document for")
        wanted |= found
    return wanted


def build(paths: WorkspacePaths, from_run: str, to_run: str, *,
          source_ids: Iterable[str] = ()) -> dict:
    """The ``cf-locate-carry/1`` document between two inventory runs; writes nothing.

    Every page both runs hold a document for, or only the pages of ``source_ids``;
    a page only one run holds is listed in ``unpaired``, and a page the map cannot
    be computed for in ``refused`` with the reason.
    """
    old, new = _side(paths, from_run), _side(paths, to_run)
    named = sorted(set(source_ids))
    considered = (_pages_of((old, new), named) if named
                  else set(old.pages) | set(new.pages))
    pages, refused = [], []
    totals: dict[str, int] = {}
    for pageid in sorted(considered & set(old.pages) & set(new.pages)):
        try:
            page = map_page(_cut(paths, old, pageid, "old"), _cut(paths, new, pageid, "new"))
        except CarryRefused as exc:
            refused.append({"pageid": pageid, "reason": str(exc)})
            continue
        pages.append(page)
        for fate, n in page["counts"].items():
            totals[fate] = totals.get(fate, 0) + n
    return {"schema": CARRY_SCHEMA,
            "old": {"inventory_run": from_run, **old.rules},
            "new": {"inventory_run": to_run, **new.rules},
            "sources": named or None,
            "pages": pages, "refused": refused,
            "unpaired": {"old": sorted(considered & set(old.pages) - set(new.pages)),
                         "new": sorted(considered & set(new.pages) - set(old.pages))},
            "counts": {"pages": len(pages), **dict(sorted(totals.items()))}}


def write(paths: WorkspacePaths, doc: dict) -> Path:
    """``reports/locate-carry/<from>/<to>.json``, sorted keys: the same map, the same bytes."""
    path = paths.locate_carry_json(doc["old"]["inventory_run"], doc["new"]["inventory_run"])
    return write_json(path, doc, sort_keys=True)


# --------------------------------------------------------------------------
# rows keyed by passage_key: known answers, a verdict export
# --------------------------------------------------------------------------

def carries(entry: dict) -> bool:
    """Does a pair verdict, label or known answer on this old passage carry (section 4.4)?

    Its passage is ``unchanged``, it was and still is ``formal_math``, and its
    conjuncts are the same: the conservative reading of "for anything that read the
    conjuncts", since a row does not say whether it read them.
    """
    return (entry.get("fate") == vocab.CARRY_UNCHANGED
            and entry.get("scope") == vocab.SCOPE_FORMAL_MATH
            and entry.get("scope_changed") is False and entry.get("conjuncts_changed") is False)


def by_passage_key(doc: dict) -> dict[str, dict]:
    """``{old passage_key: its map entry}`` over every mapped page."""
    return {e["passage_key"]: e for page in doc.get("pages") or [] for e in page["passages"]}


def read_rows(path: Path) -> list[dict]:
    """A JSON-lines file of objects, each with a 64-hex ``passage_key``; refuse by line."""
    rows = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CfError(f"cannot read --rows {path}: {exc}") from None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CfError(f"{path} line {number} is not JSON ({exc.msg})") from None
        key = row.get("passage_key") if isinstance(row, dict) else None
        if not (isinstance(key, str) and len(key) == 64
                and all(c in "0123456789abcdef" for c in key)):
            raise CfError(f"{path} line {number} has no passage_key (a 64-hex sha256)",
                          hint="--rows carries rows keyed by cflib.hashing.passage_key; the "
                               "map's passages list each old passage's key beside its item")
        rows.append(row)
    return rows


def carry_rows(doc: dict, rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Each row with ``carry`` (its old passage's fate, or null) and ``carried``, in order."""
    index = by_passage_key(doc)
    out, counts = [], {"rows": 0, "carried": 0, "asked_again": 0, "unmatched": 0}
    for row in rows:
        entry = index.get(row["passage_key"])
        if entry is None:
            carry, carried = None, False
            counts["unmatched"] += 1
        else:
            carry = {k: entry[k] for k in ("fate", "into", "scope_changed", "conjuncts_changed")}
            carried = carries(entry)
        counts["rows"] += 1
        counts["carried" if carried else "asked_again"] += 1
        out.append({**row, "carry": carry, "carried": carried})
    return out, counts


def check_rows_out(path: Path, *, source: Path) -> Path:
    """Where the carried rows may go: never into this checkout, never over ``--rows``."""
    target = Path(path).resolve()
    root = skill_root().resolve()
    if target == root or root in target.parents:
        raise CfError(f"refusing to write rows inside the skill repo ({root})",
                      hint="the skill repo holds code, templates and docs only (CLAUDE.md "
                           "section 1); pass --rows-out outside it")
    if Path(source).resolve() == target:
        raise CfError("--rows-out names the --rows file itself",
                      hint="the rows are copied, never rewritten in place: name another file")
    return target


def write_rows(path: Path, rows: list[dict], *, source: Path) -> Path:
    """The carried rows, one canonical JSON object a line (:func:`check_rows_out`)."""
    text = "".join(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                   + "\n" for r in rows)
    return atomic_write(check_rows_out(path, source=source), text)
