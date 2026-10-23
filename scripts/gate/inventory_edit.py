#!/usr/bin/env python3
"""Apply an audit round's edit operations to an inventory document -- every one citing a verdict.

New code, for references/locate-documents.md section 13 (``cf-inventory-audit/1``,
the ops file).  A worker repairing an audited inventory does not rewrite the
document by hand: it writes operations -- ``merge``, ``split``, ``set``, ``add``
and ``drop`` -- each naming the verdict it applies (``verdict``) or the verdict
whose neighbour's end or start it moves (``neighbour_of``), and this tool applies
them, in order, deterministically.  An operation that cites no verdict is
refused, so ``inventory_audit.py collateral`` can list every change that no
cited operation produced.  The tool computes no offsets: it edits quotes, ``until``
and the passage fields, and ``passage_ids.py --write`` places and re-keys the
passages afterwards, then the gate judges the result.

    inventory_edit.py --document findings/<task>/<source>.json --ops .tend/audit/r<k>.ops.json
                      [--base .tend/audit/r<k>.document.json] [--verdicts .tend/audit/r<k>.verdicts.json]
                      [--write]

Items name the passages of the base document (``--base``, the snapshot the round
reviewed; without it the document itself), so the whole ops file can be applied
again from the snapshot after every gate refusal.  ``--verdicts`` holds every
citation to a verdict the round asked for.  Without ``--write`` it prints what
it would do and writes nothing; with it, it writes the named document and
nothing else -- a file ``findings/<task>/<source>.json`` inside the working
directory, reached through no symlink.

The operations (each field one of ``inventory_audit.EDIT_FIELDS``):

* ``merge {keep, drop, fields?}`` -- ``drop`` (one item or a list) joins ``keep``:
  the merged passage starts at the earlier start (taking that passage's quote and
  finder) and ends where the one that ends later ended (its ``until``, none when
  it had none), carries ``keep``'s other fields, then ``fields``;
* ``split {item, at, new, earlier?}`` -- a new passage starts at the quote ``at``
  inside ``item``; it ends where ``item`` ended (``item``'s ``until``, unless
  ``new`` gives one), takes ``item``'s section, fragment and the finder
  ``model``, and ``new``'s fields (``statement``, ``passage_kind``, ``scope`` and
  ``prominence`` required); ``item`` loses its ``until`` (its default end now
  stops at the new start) unless ``earlier`` gives one, then takes ``earlier``;
* ``set {item, fields}`` -- the fields; ``null`` removes ``until``, ``shape`` and
  ``conjuncts`` and empties ``concept``; ``quote`` replaces the quote (moves the
  start); ``quote_end`` cuts the quote's ``exact`` after its first occurrence of
  those words (the end of the quote, the start kept), dropping its ``suffix``;
* ``add {after, quote, fields}`` -- a new passage from text no passage holds,
  placed after ``after`` (null: first), with ``after``'s section and fragment
  unless ``fields`` gives them;
* ``drop {item}`` -- the passage goes.

A passage scoped other than ``formal_math`` by an operation that does not say
otherwise becomes ``supporting`` and loses ``shape`` and ``conjuncts``, as the
gate requires (rule 10).  Exit 0 when every operation applied, 1 when one was
refused (each named, and nothing written), 2 when an input cannot be read or the
document path is refused, 3 when the gate beside it does not import.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import inventory_audit as ia  # noqa: E402

FINDINGS_DIR = "findings"
MODEL_FINDER = "model"
SUPPORTING = "supporting"
#: the keys an edit removes with ``null`` rather than storing a null
REMOVABLE = ("until", "shape", "conjuncts")


def named_items(ops: list) -> set[str]:
    """Every base item an operation names (its own passage, a merge's two, an add's anchor)."""
    out: set[str] = set()
    for op in ops:
        if not isinstance(op, dict):
            continue
        for key in ("item", "keep", "after"):
            if isinstance(op.get(key), str):
                out.add(op[key])
        drop = op.get("drop")
        out.update(d for d in (drop if isinstance(drop, list) else [drop]) if isinstance(d, str))
    return out


def _set_fields(passage: dict, fields: dict, where: str, problems: list[str]) -> None:
    """Apply ``fields`` to one passage (validated by ``inventory_audit.field_problems``)."""
    for key, value in fields.items():
        if key == "quote_end":
            quote = dict(passage.get("quote") or {})
            exact = str(quote.get("exact") or "")
            at = exact.find(value)
            if at < 0:
                problems.append(f"{where}: quote_end {value!r} is not in the quote "
                                f"{exact[:60]!r}")
                continue
            quote["exact"] = exact[:at + len(value)]
            quote["suffix"] = ""
            passage["quote"] = quote
        elif key == "quote":
            passage["quote"] = {"exact": value["exact"], "prefix": value.get("prefix", ""),
                                "suffix": value.get("suffix", "")}
        elif key in REMOVABLE and value is None:
            passage.pop(key, None)
        else:
            passage[key] = copy.deepcopy(value)
    if passage.get("scope") != ia.FORMAL and "scope" in fields:
        if "prominence" not in fields:
            passage["prominence"] = SUPPORTING
        for key in ("shape", "conjuncts"):
            if key not in fields:
                passage.pop(key, None)


def _new_passage(base: dict | None, fields: dict) -> dict:
    """A passage passage_ids.py has not placed yet: a quote, the fields, no span or item."""
    out = {"item": "", "head_words": 0, "span": None, "quote": {},
           "section": list(base.get("section") or []) if base else [],
           "passage_kind": None, "prominence": None, "scope": None, "statement": "",
           "concept": None, "finder": MODEL_FINDER}
    if base is not None and "fragment" in base:
        out["fragment"] = base.get("fragment")
    for key in ia.NEW_PASSAGE_PLACE:
        if key in fields:
            out[key] = copy.deepcopy(fields[key])
    return out


def _start(passage: dict, order: dict[int, int]) -> tuple:
    """Where a passage starts, for a merge: its span's start, else its place in the list."""
    span = ia.span_of(passage)
    return (0, span[0]) if span else (1, order.get(id(passage), 0))


def _end(passage: dict, order: dict[int, int]) -> tuple:
    """Where a passage ends, for a merge: its span's end, else its place in the list."""
    span = ia.span_of(passage)
    return (0, span[1]) if span else (1, order.get(id(passage), 0))


def apply(doc: dict, ops: list, *, verdict_ids: set[str] | None = None
          ) -> tuple[dict, list[str], list[str]]:
    """``(document, log, problems)``: ``ops`` applied to a copy of ``doc``, in order.

    Items name ``doc``'s passages; a passage an earlier operation dropped or merged away
    cannot be named again.  Any problem leaves the result unusable: the caller writes
    nothing.
    """
    out = copy.deepcopy(doc)
    passages: list[dict] = out.setdefault("passages", [])
    order = {id(p): n for n, p in enumerate(passages)}
    items = {str(p.get("item")): p for p in passages if p.get("item")}
    gone: set[str] = set()
    log: list[str] = []
    problems: list[str] = []

    def find(item: Any, where: str) -> dict | None:
        if item in gone:
            problems.append(f"{where}: {item} was dropped or merged away by an earlier operation")
            return None
        passage = items.get(item)
        if passage is None:
            problems.append(f"{where}: {item} is no passage of the base document")
        return passage

    for i, op in enumerate(ops):
        where = f"ops[{i}]"
        shape = ia.op_problems(op, where, verdict_ids=verdict_ids)
        if shape:
            problems.extend(shape)
            continue
        name = op["op"]
        cite = op.get("verdict") or f"neighbour of {op.get('neighbour_of')}"
        before = len(problems)
        if name == "merge":
            keep = find(op["keep"], where)
            drops = op["drop"] if isinstance(op["drop"], list) else [op["drop"]]
            absorbed = [find(d, where) for d in drops]
            if keep is None or any(a is None for a in absorbed) or len(problems) > before:
                continue
            if any(a is keep for a in absorbed) or \
                    len({id(a) for a in absorbed}) != len(absorbed):
                # op_problems refuses a repeated item; this guards one passage named twice
                problems.append(f"{where}: merge names one passage twice")
                continue
            for item, d in zip(drops, absorbed):
                # the merged passage's provisional span orders the next drop against it
                # (passage_ids.py writes the real one)
                if _start(d, order) < _start(keep, order):
                    keep["quote"] = copy.deepcopy(d.get("quote"))
                    keep["finder"] = d.get("finder")
                if _end(d, order) > _end(keep, order):
                    if "until" in d:
                        keep["until"] = d["until"]
                    else:
                        keep.pop("until", None)
                if ia.span_of(d) and ia.span_of(keep):
                    keep["span"] = [min(keep["span"][0], d["span"][0]),
                                    max(keep["span"][1], d["span"][1])]
                passages.remove(d)
                gone.add(item)
            _set_fields(keep, op.get("fields") or {}, where, problems)
            log.append(f"op {i + 1} merge ({cite}): {op['keep']} absorbs {', '.join(drops)}")
        elif name == "split":
            p = find(op["item"], where)
            if p is None:
                continue
            new = _new_passage(p, op["new"])
            new["quote"] = {"exact": op["at"]["exact"], "prefix": op["at"].get("prefix", ""),
                            "suffix": op["at"].get("suffix", "")}
            if "until" in p:
                new["until"] = p["until"]
            _set_fields(new, {k: v for k, v in op["new"].items()
                              if k not in ia.NEW_PASSAGE_PLACE}, where, problems)
            p.pop("until", None)
            _set_fields(p, op.get("earlier") or {}, where, problems)
            passages.insert(passages.index(p) + 1, new)
            order[id(new)] = order.get(id(p), 0)
            log.append(f"op {i + 1} split ({cite}): {op['item']} at {op['at']['exact'][:40]!r}")
        elif name == "set":
            p = find(op["item"], where)
            if p is None:
                continue
            _set_fields(p, op["fields"], where, problems)
            log.append(f"op {i + 1} set ({cite}): {op['item']} "
                       f"{', '.join(sorted(op['fields']))}")
        elif name == "add":
            anchor = None if op["after"] is None else find(op["after"], where)
            if op["after"] is not None and anchor is None:
                continue
            new = _new_passage(anchor, op["fields"])
            new["quote"] = {"exact": op["quote"]["exact"],
                            "prefix": op["quote"].get("prefix", ""),
                            "suffix": op["quote"].get("suffix", "")}
            _set_fields(new, {k: v for k, v in op["fields"].items()
                              if k not in ia.NEW_PASSAGE_PLACE}, where, problems)
            passages.insert(passages.index(anchor) + 1 if anchor is not None else 0, new)
            order[id(new)] = order.get(id(anchor), -1) if anchor is not None else -1
            log.append(f"op {i + 1} add ({cite}): after {op['after']} "
                       f"{op['quote']['exact'][:40]!r}")
        else:
            p = find(op["item"], where)
            if p is None:
                continue
            passages.remove(p)
            gone.add(op["item"])
            log.append(f"op {i + 1} drop ({cite}): {op['item']}")
    return out, log, problems


def document_path(value: str) -> Path:
    """The document an edit may write: ``findings/<task>/<source>.json`` in the working
    directory, an existing regular file reached through no symlink."""
    cwd = Path.cwd().resolve()
    raw = Path(value)
    path = Path(os.path.normpath(raw if raw.is_absolute() else cwd / raw))
    try:
        rel = path.relative_to(cwd)
    except ValueError:
        raise ia.InputError(f"the document {value} is outside the working directory") from None
    if len(rel.parts) != 3 or rel.parts[0] != FINDINGS_DIR or rel.suffix != ".json":
        raise ia.InputError(f"the document {value} is not findings/<task>/<source>.json")
    node = cwd
    for part in rel.parts:
        node = node / part
        if node.is_symlink():
            raise ia.InputError(f"{node} is a symlink; this tool writes through none")
    if not path.is_file():
        raise ia.InputError(f"the document {value} is not a file")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--document", required=True, help="findings/<task>/<source>.json")
    ap.add_argument("--ops", required=True, help="the round's ops file")
    ap.add_argument("--base", help="the snapshot the items name (default: the document)")
    ap.add_argument("--verdicts", help="the round's verdicts file: every citation must be one")
    ap.add_argument("--write", action="store_true", help="write the document (default: dry run)")
    args = ap.parse_args(argv)
    try:
        path = document_path(args.document)
        doc = ia.read_document(path)
        base = ia.read_document(args.base) if args.base else doc
        for key in ("source_id", "task_id"):
            if base.get(key) != doc.get(key):
                raise ia.InputError(f"--base names {key} {base.get(key)!r}, the document "
                                    f"{doc.get(key)!r}")
        ops = ia.read_json(args.ops, "ops file")
        ids = None
        if args.verdicts:
            verdicts = ia.read_json(args.verdicts, "verdicts file")
            ids = {v.get("id") for v in verdicts.get("verdicts") or [] if isinstance(v, dict)}
    except ia.InputError as exc:
        print(f"inventory_edit: {exc}", file=sys.stderr)
        return ia.EXIT_INPUT
    try:
        problems = ia.validate_ops(ops, doc, verdict_ids=ids)
        result, log, more = (apply(base, ops["ops"], verdict_ids=ids) if not problems
                             else (None, [], []))
    except ImportError as exc:
        print(f"FAIL: the gate beside this tool does not import ({exc})", file=sys.stderr)
        return ia.EXIT_BROKEN
    problems.extend(more)
    if problems:
        for line in problems[:ia.MAX_PROBLEMS]:
            print(f"REFUSED: {line}")
        print("nothing written")
        return ia.EXIT_INVALID
    for line in log:
        print(line)
    print(f"{len(base['passages'])} passage(s) before, {len(result['passages'])} after")
    if args.write:
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        print(f"wrote {args.document}; run passage_ids.py --write on it next, then the gate")
    else:
        print("dry run: nothing written (add --write)")
    return ia.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
