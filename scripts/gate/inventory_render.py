#!/usr/bin/env python3
"""Render an inventory document against its layer, for the audit: parts, one passage, a diff.

New code, for references/locate-documents.md section 13 (``cf-inventory-audit/1``,
the rendering).  An auditor, a skeptic and a checker read an inventory as text:
every passage in span order, its exact span on the layer, its statement and
conjuncts, the layer text no passage holds, and computed hints -- never the JSON.
This tool writes that text from the document and the pinned source, and refuses
(exit 3) a layer, ``formulas.json`` or ``paragraphs.json`` that is not what the
run's ``gate.json`` pins, with the gate's own words (``verify_locate.SourceView``).

    inventory_render.py --gate-config <gate>/gate.json --document findings/<task>/<source>.json
        [--out-dir .tend/audit --label r<k> [--previous <snapshot>] [--part-bytes N]]
        [--show <item> ... [--context N]]

* With neither ``--out-dir`` nor ``--show`` it prints the whole rendering.
* **Parts** (``--out-dir``, the bridge's mode): ``r<k>.render.part<i>.md``, each at most
  about ``--part-bytes`` (``inventory_audit.PART_BYTES``) and cut only between two
  passages (a passage longer than that is a part of its own), each opening with the
  rendering's header and the passages it holds; ``r<k>.render.json``, the index of the
  parts (each part's file, bytes, first and last passage number, items and formal
  items); and with ``--previous`` ``r<k>.diff.md``: every passage changed, added or
  removed since that snapshot, before and after.  The directory must be
  ``.tend/audit`` of the working directory.
* **One passage** (``--show``, an agent's mode, repeatable): the passage's lines with
  ``--context`` characters (default 300) of layer before and after it.

Every passage line is ``[n] item · span · scope · kind · prominence · section``, then
``TEXT`` (the span, a line break shown as ``⏎``), ``STATEMENT``, ``SHAPE`` and its
conjuncts on a compound statement, and ``HINT``: ``kind-other`` (a formal passage of
kind other), ``kind-rule`` (one the kind rule judges: in a proof or an example
section, so the merge adds a wrong_kind verdict unless the auditor says, with a
reason, that other is right), ``in-proof``, ``in-example``, ``connective-start``
(its first word is one of ``inventory_audit.CONNECTIVE_HINTS``) and ``widened`` (it
ends past its loose extent, where only statement-cut rule 3's wide bound lets it
run: a merge the checker judges).  ``GAP`` lines are layer text no passage holds,
cut to its first and last 200 characters past 400.

Exit 0, 2 when an input cannot be read, an item is not in the document or an
output path is refused, 3 when ``gate.json`` or a pinned file cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import inventory_audit as ia  # noqa: E402

LINE_MARK = "⏎"
GAP_MAX = 400
GAP_SIDE = 200
SKIPS_MAX = 3000
CONTEXT_DEFAULT = 300
CONTEXT_MAX = 5000
LABEL = re.compile(r"r[1-9][0-9]?\Z")

EXPLAIN = [
    "Each passage: [n] item · span · scope · kind · prominence · section, then TEXT (the span "
    f"on the layer; {LINE_MARK} marks a line break), STATEMENT, SHAPE and its conjuncts when "
    "compound, and HINT.",
    f"GAP lines are layer text no passage holds (past {GAP_MAX} characters, its first and last "
    f"{GAP_SIDE}).",
    "HINTs: kind-other (a formal passage of kind other); kind-rule (in a proof or an example "
    "section: the stage flags it wrong_kind unless the audit says, with a reason, that other "
    "is right); in-proof; in-example; connective-start (it starts at and, so, hence, thus, "
    "therefore, then or but); widened (it ends past its loose extent: only the wide bound of "
    "statement-cut rule 3 lets it run).",
]


def flat(text: str) -> str:
    """One line: each line break shown as the mark, no trailing whitespace."""
    return text.replace("\r\n", "\n").replace("\n", f" {LINE_MARK} ").rstrip()


def gap_line(text: str, label: str = "GAP") -> str | None:
    if not text.strip():
        return None
    if len(text) > GAP_MAX:
        text = text[:GAP_SIDE] + " … " + text[-GAP_SIDE:]
    return f"{label}: {flat(text)}"


def hints(passage: dict, layer: str, blocks) -> list[str]:
    """The computed hints of one passage (see the module docstring)."""
    out = []
    formal = ia.is_formal(passage)
    if formal and passage.get("passage_kind") == ia.KIND_OTHER:
        out.append("kind-other")
    if ia.kind_rule_applies(passage):
        out.append("kind-rule")
    if ia.in_proof(passage):
        out.append("in-proof")
    if ia.in_example(passage):
        out.append("in-example")
    span = ia.span_of(passage)
    if span:
        first = layer[span[0]:span[1]].split(None, 1)
        if first and first[0].rstrip(",;:").lower() in ia.CONNECTIVE_HINTS:
            out.append("connective-start")
        if formal and blocks is not None:
            compound = isinstance(passage.get("conjuncts"), list) and bool(passage["conjuncts"])
            loose = blocks.extent(span[0], compound=compound, loose=True)
            if loose is not None and span[1] > loose:
                out.append("widened")
    return out


def passage_lines(n: int, passage: dict, layer: str, blocks) -> list[str]:
    span = ia.span_of(passage) or (0, 0)
    section = " > ".join(ia.section_path(passage)) or "(lead)"
    lines = [f"[{n}] {passage.get('item')} · [{span[0]}, {span[1]}] · {passage.get('scope')} · "
             f"{passage.get('passage_kind')} · {passage.get('prominence')} · {section}",
             f"  TEXT: {flat(layer[span[0]:span[1]])}",
             f"  STATEMENT: {flat(str(passage.get('statement') or ''))}"]
    conjuncts = passage.get("conjuncts")
    if isinstance(conjuncts, list) and conjuncts:
        lines.append(f"  SHAPE {passage.get('shape')}: " + " | ".join(
            f"{c.get('id')}: {flat(str(c.get('text')))}" for c in conjuncts if isinstance(c, dict)))
    found = hints(passage, layer, blocks)
    if found:
        lines.append("  HINT: " + " ".join(found))
    return lines


def header(doc: dict) -> list[str]:
    rows = doc.get("passages") or []
    formal = sum(ia.is_formal(p) for p in rows)
    notes = flat(str(doc.get("notes") or "")).strip() or "(none)"
    return [f"# {doc.get('source_id')}: inventory, {len(rows)} passages ({formal} formal)", "",
            *EXPLAIN, f"Worker notes: {notes}", ""]


def entries(doc: dict, layer: str, blocks) -> tuple[list[tuple[int, dict, list[str]]], list[str]]:
    """``([(n, passage, lines)], tail)``: each passage's lines, the gap before it first, and
    the lines after the last passage (the gap to the end and the skips)."""
    out = []
    pos = 0
    for n, passage in enumerate(ia.ordered(doc), 1):
        span = ia.span_of(passage) or (pos, pos)
        lines = []
        if span[0] > pos:
            gap = gap_line(layer[pos:span[0]])
            if gap:
                lines.append(gap)
        lines.extend(passage_lines(n, passage, layer, blocks))
        out.append((n, passage, lines))
        pos = max(pos, span[1])
    tail = []
    if pos < len(layer):
        gap = gap_line(layer[pos:pos + GAP_MAX], "GAP (to end)")
        if gap:
            tail.append(gap)
    if doc.get("skips"):
        tail.extend(["", "SKIPS: " + json.dumps(doc["skips"], ensure_ascii=False)[:SKIPS_MAX]])
    return out, tail


def render(doc: dict, layer: str, blocks) -> str:
    rows, tail = entries(doc, layer, blocks)
    return "\n".join(header(doc) + [line for _n, _p, lines in rows for line in lines] + tail) \
        + "\n"


def nbytes(lines: list[str]) -> int:
    return sum(len(line.encode("utf-8")) + 1 for line in lines)


def parts(doc: dict, layer: str, blocks, part_bytes: int = ia.PART_BYTES) -> list[dict]:
    """The rendering cut between passages into parts of at most about ``part_bytes``."""
    rows, tail = entries(doc, layer, blocks)
    head = header(doc)
    groups: list[list[tuple[int, dict, list[str]]]] = [[]]
    size = nbytes(head)
    for row in rows:
        cost = nbytes(row[2])
        if groups[-1] and size + cost > part_bytes:
            groups.append([])
            size = nbytes(head)
        groups[-1].append(row)
        size += cost
    out = []
    total = len(rows)
    for i, group in enumerate(groups, 1):
        if not group:
            continue
        first, last = group[0][0], group[-1][0]
        lines = head + [f"Part {i} of {len(groups)}: passages [{first}] to [{last}] of {total}.",
                        ""] + [line for _n, _p, ls in group for line in ls]
        if i == len(groups):
            lines += tail
        text = "\n".join(lines) + "\n"
        out.append({"text": text, "first": first, "last": last,
                    "items": [str(p.get("item")) for _n, p, _l in group],
                    "formal": [str(p.get("item")) for _n, p, _l in group if ia.is_formal(p)]})
    return out


def show(doc: dict, layer: str, blocks, items: list[str], context: int) -> str:
    """The passages named, each with ``context`` characters of layer around it."""
    numbered = {str(p.get("item")): (n, p) for n, p in enumerate(ia.ordered(doc), 1)}
    out = []
    for item in items:
        if item not in numbered:
            raise ia.InputError(f"{item} is no passage of the document")
        n, passage = numbered[item]
        span = ia.span_of(passage) or (0, 0)
        lines = passage_lines(n, passage, layer, blocks)
        lines.insert(1, f"  BEFORE: {flat(layer[max(0, span[0] - context):span[0]])}")
        lines.insert(3, f"  AFTER: {flat(layer[span[1]:span[1] + context])}")
        out.extend(lines + [""])
    return "\n".join(out)


def _changed(a: dict, b: dict) -> list[str]:
    fields = [k for k in ia.CONTENT_FIELDS if a.get(k) != b.get(k)]
    if ia.span_of(a) != ia.span_of(b):
        fields.append("span")
    if a.get("item") != b.get("item"):
        fields.append("item")
    return fields


def _overlaps(passage: dict, among: list[dict]) -> list[str]:
    span = ia.span_of(passage)
    if not span:
        return []
    return [str(q.get("item")) for q in among
            if (s := ia.span_of(q)) and s[0] < span[1] and span[0] < s[1]]


def diff(previous: dict, doc: dict, layer: str, blocks) -> str:
    """Every passage changed, added or removed between ``previous`` and ``doc``.

    Passages are matched by item, then by start (an id re-keyed when a neighbour's head
    changed); an added passage names the earlier passages its span overlaps, a removed one
    the passages now holding its text.
    """
    before = {str(p.get("item")): (n, p) for n, p in enumerate(ia.ordered(previous), 1)}
    after = {str(p.get("item")): (n, p) for n, p in enumerate(ia.ordered(doc), 1)}
    pairs = [(before[i], after[i]) for i in after if i in before]
    lost = {i: v for i, v in before.items() if i not in after}
    new = {i: v for i, v in after.items() if i not in before}
    starts = {(ia.span_of(p) or (None,))[0]: i for i, (_n, p) in lost.items()}
    for item, (n, p) in sorted(new.items(), key=lambda kv: kv[1][0]):
        match = starts.get((ia.span_of(p) or (None,))[0])
        if match is not None and match in lost:
            pairs.append((lost.pop(match), (n, p)))
            del new[item]
    blocks_out: list[tuple[int, list[str]]] = []
    changed = 0
    for (nb, pb), (na, pa) in pairs:
        fields = _changed(pb, pa)
        if not fields:
            continue
        changed += 1
        lines = [f"CHANGED {pa.get('item')} (fields: {', '.join(fields)})", "  BEFORE:"]
        lines += ["    " + x for x in passage_lines(nb, pb, layer, blocks)]
        lines += ["  AFTER:"] + ["    " + x for x in passage_lines(na, pa, layer, blocks)]
        blocks_out.append((na, lines))
    prev_rows = [p for _n, p in before.values()]
    cur_rows = [p for _n, p in after.values()]
    for _item, (n, p) in new.items():
        held = _overlaps(p, prev_rows)
        lines = [f"ADDED {p.get('item')}" + (f" (inside {', '.join(held)} before)" if held else "")]
        lines += ["  AFTER:"] + ["    " + x for x in passage_lines(n, p, layer, blocks)]
        blocks_out.append((n, lines))
    for _item, (n, p) in lost.items():
        held = _overlaps(p, cur_rows)
        lines = [f"REMOVED {p.get('item')}"
                 + (f" (its text now in {', '.join(held)})" if held else "")]
        lines += ["  BEFORE:"] + ["    " + x for x in passage_lines(n, p, layer, blocks)]
        place = next((m for m, q in enumerate(ia.ordered(doc), 1)
                      if (ia.span_of(q) or (0,))[0] >= (ia.span_of(p) or (0,))[0]),
                     len(after) + 1)
        blocks_out.append((place, lines))
    head = [f"# {doc.get('source_id')}: {changed} changed, {len(new)} added, {len(lost)} removed "
            f"({len(before)} passages before, {len(after)} after)", "",
            "Each entry: the passage BEFORE and AFTER, rendered as in the rendering. Passages "
            "are matched by item, then by start.", ""]
    body = [line for _n, lines in sorted(blocks_out, key=lambda b: b[0]) for line in lines + [""]]
    return "\n".join(head + body)


def _out_dir(value: str) -> Path:
    probe = ia.confined(os.path.join(value, "probe"), ia.OUT_DIR)
    return probe.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gate-config", required=True, help="the run's gate.json")
    ap.add_argument("--document", required=True, help="the inventory document")
    ap.add_argument("--out-dir", help="write the parts here (.tend/audit)")
    ap.add_argument("--label", help="the round's label, r<k> (with --out-dir)")
    ap.add_argument("--previous", help="the snapshot to diff against (with --out-dir)")
    ap.add_argument("--part-bytes", type=int, default=ia.PART_BYTES)
    ap.add_argument("--show", action="append", metavar="ITEM", help="print one passage")
    ap.add_argument("--context", type=int, default=CONTEXT_DEFAULT)
    args = ap.parse_args(argv)
    try:
        if args.out_dir and args.show:
            raise ia.InputError("give --out-dir or --show, not both")
        if args.out_dir and not (args.label and LABEL.match(args.label)):
            raise ia.InputError("--out-dir needs --label r<k>")
        if (args.label or args.previous) and not args.out_dir:
            raise ia.InputError("--label and --previous go with --out-dir")
        if not 0 <= args.context <= CONTEXT_MAX:
            raise ia.InputError(f"--context is {args.context}, not 0..{CONTEXT_MAX}")
        if args.part_bytes < 1000:
            raise ia.InputError(f"--part-bytes is {args.part_bytes}, below 1000")
        doc = ia.read_document(args.document)
        sid = str(doc.get("source_id") or "")
        cfg, view = ia.load_source(args.gate_config, sid)
        pinned = str(cfg.sources[sid].get("layer_sha256") or "")
        if doc.get("layer_sha256") and doc["layer_sha256"] != pinned:
            raise ia.InputError(f"the document names layer {str(doc['layer_sha256'])[:12]}…, "
                                f"the run pinned {pinned[:12]}…")
        blocks = view.blocks()
        if args.show:
            print(show(doc, view.layer, blocks, args.show, args.context))
            return ia.EXIT_OK
        if not args.out_dir:
            sys.stdout.write(render(doc, view.layer, blocks))
            return ia.EXIT_OK
        out = _out_dir(args.out_dir)
        previous = ia.read_document(args.previous) if args.previous else None
        if previous is not None and previous.get("source_id") != sid:
            raise ia.InputError(f"--previous is of {previous.get('source_id')!r}, not {sid!r}")
    except ia.InputError as exc:
        print(f"inventory_render: {exc}", file=sys.stderr)
        return ia.EXIT_INPUT
    except ia.Broken as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return ia.EXIT_BROKEN
    out.mkdir(parents=True, exist_ok=True)
    index = {"schema": ia.AUDIT_SCHEMA, "kind": "render", "label": args.label,
             "source_id": sid, "document_sha256": ia.sha256_file(args.document),
             "layer_sha256": pinned, "passages": len(doc["passages"]),
             "formal": len(ia.formal_items(doc)), "part_bytes": args.part_bytes, "parts": [],
             "diff": None}
    for i, part in enumerate(parts(doc, view.layer, blocks, args.part_bytes), 1):
        name = f"{args.label}.render.part{i}.md"
        (out / name).write_text(part["text"], encoding="utf-8")
        index["parts"].append({"file": name, "bytes": len(part["text"].encode("utf-8")),
                               **{k: part[k] for k in ("first", "last", "items", "formal")}})
    if previous is not None:
        name = f"{args.label}.diff.md"
        (out / name).write_text(diff(previous, doc, view.layer, blocks), encoding="utf-8")
        index["diff"] = name
    ia.write_json(out / f"{args.label}.render.json", index)
    print(json.dumps({"parts": [p["file"] for p in index["parts"]], "diff": index["diff"],
                      "index": f"{args.label}.render.json"}))
    return ia.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
