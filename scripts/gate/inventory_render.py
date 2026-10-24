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
        [--show <item> ... [--context N]] [--rule 2 [--previous-verdicts <verdicts>]]

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

Under ``--rule 2`` (references/locate-documents.md section 13.11, audit rule 2) every
skip is a SKIP block in place (its id ``r<k>-s<nnn>``, span, blocks, math paragraphs and
those no passage holds, sections, REASON, a ``¶`` line per block, HINT: one of
``inventory_audit.SKIP_HINTS``) instead of the closing ``SKIPS:`` line; the header gains
the skips' line and the ATTENTION line of the mostly skipped sections; the index gains
``rule`` and ``skips`` and each part the skips it shows; ``--show <skip id>`` prints one
skip whole; and with ``--previous-verdicts`` the diff gains its SKIPS section and a SKIP
VERDICT line per skip verdict of the last round.  Without ``--rule`` (rule 1) every output
is the base commit's, byte for byte.

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
#: rule 2's skip blocks: a math block in full to MATH_MAX characters (its first and last
#: MATH_SIDE past that), a run of LIST_RUN or more list items with no math one line (each
#: end LIST_SIDE characters), and past ia.SKIP_BLOCK_BYTES one clipped line a block
MATH_MAX = 1500
MATH_SIDE = 700
LIST_RUN = 3
LIST_SIDE = 60
CAPPED_SIDE = 100

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


EXPLAIN_SKIPS = [
    "SKIP blocks are the document's skips, in place, each named r<k>-s<nnn> (this round's "
    "numbering, in span order): SKIP id · span · chars · its blocks, their math paragraphs and "
    "how many of those no passage holds · its sections; REASON (the worker's); one ¶ line per "
    "block with its span inside the skip (a math block in full, a run of list items with no "
    "math as one … line); ⋯ for text no block holds; HINT.",
    "Skip HINTs: skip-rule (it covers math no passage holds in a proof, an example or a "
    "mostly skipped section: the stage gives it a skipped_statements verdict unless the audit "
    "says, with a reason, that the skip is right); in-proof; in-example; mostly-skipped (a "
    "section of it is mostly skipped); partial (it starts or ends inside a block); furniture "
    "(every block is in the lead or the back matter).",
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


def header(doc: dict, skips: dict | None = None) -> list[str]:
    """The rendering's header; under rule 2 (``skips``, :func:`skip_summary`) also the
    skips' line and, when a section is mostly skipped, the ATTENTION line."""
    rows = doc.get("passages") or []
    formal = sum(ia.is_formal(p) for p in rows)
    notes = flat(str(doc.get("notes") or "")).strip() or "(none)"
    if skips is None:
        return [f"# {doc.get('source_id')}: inventory, {len(rows)} passages ({formal} formal)",
                "", *EXPLAIN, f"Worker notes: {notes}", ""]
    return [f"# {doc.get('source_id')}: inventory, {len(rows)} passages ({formal} formal)", "",
            *EXPLAIN, *EXPLAIN_SKIPS, f"Worker notes: {notes}", *skips["lines"], ""]


def skip_summary(doc: dict, facts: list[dict], paragraphs: list[dict]) -> dict:
    """Rule 2's header lines: how many skips, the math they leave unheld, and the mostly
    skipped sections (the soft threshold, ``inventory_audit.mostly_skipped``)."""
    report = ia.skip_report(doc, paragraphs)
    if not facts:
        lines = ["Skips: none."]
    else:
        ids = facts[0]["id"] if len(facts) == 1 else f"{facts[0]['id']} to {facts[-1]['id']}"
        outside = ia.skipped_math_outside(report)
        lines = [f"Skips: {len(facts)} ({ids}); {report['math_paragraphs']} math paragraph(s) "
                 f"lie under them and no passage, {outside} of them outside the lead and the "
                 f"back matter."]
    if report["mostly_skipped"]:
        lines.append("ATTENTION: mostly skipped sections: " + "; ".join(
            f"{key} {report['by_section'][key]['skipped']} of {report['by_section'][key]['math']}"
            for key in report["mostly_skipped"]))
    return {"lines": lines, "report": report}


def clip(text: str, cap: int, side: int) -> str:
    return text if len(text) <= cap else text[:side] + " … " + text[-side:]


def skip_lines(fact: dict, layer: str, *, cap: bool = True) -> list[str]:
    """One skip's block (rule 2): its header, REASON, a line per block in reading order,
    ⋯ for text no block holds, and HINT.  Past ``ia.SKIP_BLOCK_BYTES`` (``cap``) every
    further block is one line of its first and last characters and the block ends by
    naming ``--show``."""
    a, b = fact["span"]
    blocks = fact["blocks"]
    lines = [f"SKIP {fact['id']} · [{a}, {b}] · {b - a} chars · {len(blocks)} block(s), "
             f"{fact['math']} math paragraph(s), {fact['unheld']} held by no passage · "
             + ("; ".join(fact["sections"]) or "no block"),
             f"  REASON: {flat(fact['reason'])}"]
    pieces: list[tuple[str, object]] = []
    cursor = a
    for block in blocks:
        x, y = max(a, block["span"][0]), min(b, block["span"][1])
        if x > cursor and layer[cursor:x].strip():
            pieces.append(("text", (cursor, x)))
        pieces.append(("block", block))
        cursor = max(cursor, y)
    if cursor < b and layer[cursor:b].strip():
        pieces.append(("text", (cursor, b)))
    size = nbytes(lines)
    capped = clipped = False
    i = 0
    while i < len(pieces):
        kind, value = pieces[i]
        clipped = clipped or capped
        run = []
        while kind == "block" and i + len(run) < len(pieces):
            k2, v2 = pieces[i + len(run)]
            if k2 != "block" or v2["block"] != "li" or v2["math"]:
                break
            run.append(v2)
        if len(run) >= LIST_RUN:
            x, y = max(a, run[0]["span"][0]), min(b, run[-1]["span"][1])
            first = flat(layer[max(a, run[0]["span"][0]):min(b, run[0]["span"][1])])
            last = flat(layer[max(a, run[-1]["span"][0]):min(b, run[-1]["span"][1])])
            line = (f"  … [{x}, {y}] {len(run)} list items, no math: "
                    f"{clip(first, 2 * LIST_SIDE, LIST_SIDE)} … {clip(last, 2 * LIST_SIDE, LIST_SIDE)}")
            i += len(run)
        elif kind == "text":
            x, y = value
            text = flat(layer[x:y])
            line = "  ⋯ " + (clip(text, 2 * CAPPED_SIDE, CAPPED_SIDE) if capped
                             else clip(text, GAP_MAX, GAP_SIDE))
            i += 1
        else:
            block = value
            x, y = max(a, block["span"][0]), min(b, block["span"][1])
            where = f"[{x}, {y}]" + ("" if [x, y] == block["span"]
                                     else f" (block [{block['span'][0]}, {block['span'][1]}])")
            text = flat(layer[x:y])
            if capped:
                text = clip(text, 2 * CAPPED_SIDE, CAPPED_SIDE)
            elif block["math"]:
                text = clip(text, MATH_MAX, MATH_SIDE)
            else:
                text = clip(text, GAP_MAX, GAP_SIDE)
            mark = (" · math" + ("" if block["held"] else " · held by no passage")
                    if block["math"] else "")
            line = f"  ¶ {where} {block['block'] or 'block'}{mark}: {text}"
            i += 1
        lines.append(line)
        size += len(line.encode("utf-8")) + 1
        if cap and not capped and size > ia.SKIP_BLOCK_BYTES:
            capped = True
    if clipped:
        lines.append(f"  … the rest in full: --show {fact['id']}")
    hints = ia.skip_hints(fact)
    if hints:
        lines.append("  HINT: " + " ".join(hints))
    return lines


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


def entries2(doc: dict, layer: str, blocks, facts: list[dict]
             ) -> tuple[list[tuple[int, dict, list[str]]], list[str], dict[str, int | None]]:
    """Rule 2's :func:`entries`: each skip as a block in place, in the gap before the next
    passage (its text split around it), and ``{skip id: the passage number whose lines
    show it, or None for the tail}``.  A skip whose text lies inside kept passages (a finder
    nested in one) is one entry of the closing ``SKIPS inside kept passages`` line."""
    shown: dict[str, int | None] = {}

    def gap_and_skips(lo: int, hi: int, owner: int | None, last: bool = False) -> list[str]:
        lines, cur = [], lo
        for fact in facts:
            a, b = fact["span"]
            if fact["id"] in shown or fact["inside"] or a >= hi or b <= lo:
                continue
            shown[fact["id"]] = owner
            if a > cur:
                gap = gap_line(layer[cur:a])
                if gap:
                    lines.append(gap)
            lines.extend(skip_lines(fact, layer))
            cur = max(cur, b)
        if cur < hi:
            gap = (gap_line(layer[cur:cur + GAP_MAX], "GAP (to end)") if last
                   else gap_line(layer[cur:hi]))
            if gap:
                lines.append(gap)
        return lines

    out = []
    pos = 0
    for n, passage in enumerate(ia.ordered(doc), 1):
        span = ia.span_of(passage) or (pos, pos)
        lines = gap_and_skips(pos, span[0], n) if span[0] > pos else []
        lines.extend(passage_lines(n, passage, layer, blocks))
        out.append((n, passage, lines))
        pos = max(pos, span[1])
    tail = gap_and_skips(pos, len(layer), None, last=True) if pos < len(layer) else []
    inner = [f for f in facts if f["id"] not in shown]
    if inner:
        tail += ["", "SKIPS inside kept passages: " + "; ".join(
            f"{f['id']} [{f['span'][0]}, {f['span'][1]}] {flat(f['reason'])}" for f in inner)]
        for f in inner:
            shown[f["id"]] = None
    return out, tail, shown


def render(doc: dict, layer: str, blocks, *, rule: int = ia.DEFAULT_RULE,
           paragraphs: list[dict] | None = None, label: str = "r1") -> str:
    if rule >= 2:
        facts = ia.skip_facts(doc, paragraphs or [], int(label[1:]))
        rows, tail, _shown = entries2(doc, layer, blocks, facts)
        head = header(doc, skip_summary(doc, facts, paragraphs or []))
        return "\n".join(head + [line for _n, _p, lines in rows for line in lines] + tail) \
            + "\n"
    rows, tail = entries(doc, layer, blocks)
    return "\n".join(header(doc) + [line for _n, _p, lines in rows for line in lines] + tail) \
        + "\n"


def nbytes(lines: list[str]) -> int:
    return sum(len(line.encode("utf-8")) + 1 for line in lines)


def parts(doc: dict, layer: str, blocks, part_bytes: int = ia.PART_BYTES, *,
          rule: int = ia.DEFAULT_RULE, paragraphs: list[dict] | None = None,
          label: str = "r1") -> list[dict]:
    """The rendering cut between passages into parts of at most about ``part_bytes``; under
    rule 2 each part also names the skips it shows (``skips``), the tail's in the last."""
    shown: dict[str, int | None] = {}
    if rule >= 2:
        facts = ia.skip_facts(doc, paragraphs or [], int(label[1:]))
        rows, tail, shown = entries2(doc, layer, blocks, facts)
        head = header(doc, skip_summary(doc, facts, paragraphs or []))
    else:
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
        if rule >= 2:
            numbers = {n for n, _p, _l in group}
            out[-1]["skips"] = [sid for sid, n in shown.items()
                                if n in numbers or (n is None and i == len(groups))]
    if rule >= 2 and not out:
        # a document with no passage is never rendered by the stage; its skips still have a part
        out.append({"text": "\n".join(head + tail) + "\n", "first": 0, "last": 0, "items": [],
                    "formal": [], "skips": list(shown)})
    return out


def show(doc: dict, layer: str, blocks, items: list[str], context: int, *,
         rule: int = ia.DEFAULT_RULE, paragraphs: list[dict] | None = None) -> str:
    """The passages named, each with ``context`` characters of layer around it; under rule 2
    a skip id (``r<k>-s<nnn>``, the document's skips in span order) prints that skip's
    block whole, with the layer around it."""
    numbered = {str(p.get("item")): (n, p) for n, p in enumerate(ia.ordered(doc), 1)}
    out = []
    for item in items:
        if rule >= 2 and ia.skip_number(item):
            facts = {f["id"]: f for f in ia.skip_facts(doc, paragraphs or [],
                                                       ia.skip_number(item)[0])}
            if item not in facts:
                raise ia.InputError(f"{item} is no skip of the document")
            fact = facts[item]
            a, b = fact["span"]
            lines = skip_lines(fact, layer, cap=False)
            lines.insert(1, f"  BEFORE: {flat(layer[max(0, a - context):a])}")
            at = len(lines) - (1 if lines[-1].startswith("  HINT:") else 0)
            lines.insert(at, f"  AFTER: {flat(layer[b:b + context])}")
            out.extend(lines + [""])
            continue
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


def skip_diff(previous: dict, doc: dict, layer: str, paragraphs: list[dict], *,
              before_round: int, after_round: int, verdicts: dict | None) -> list[str]:
    """Rule 2's part of the diff: every skip of the snapshot that changed, before and after
    (matched by span and reason, then a lost and a new one that overlap), and a line per skip
    verdict of the last round with the counts the checker reads mechanically."""
    old = ia.skip_facts(previous, paragraphs, before_round)
    new = ia.skip_facts(doc, paragraphs, after_round)

    def key(f: dict) -> tuple:
        return (tuple(f["span"]), f["reason"])

    pool: dict[tuple, list[dict]] = {}
    for f in new:
        pool.setdefault(key(f), []).append(f)
    lost = []
    for f in old:
        if pool.get(key(f)):
            pool[key(f)].pop(0)
        else:
            lost.append(f)
    added = [f for fs in pool.values() for f in fs]
    added.sort(key=lambda f: f["span"])
    lines = [f"SKIPS: {len(old)} before, {len(new)} after"]
    for f in added:
        a, b = f["span"]
        q = next((g for g in lost if g["span"][0] < b and a < g["span"][1]), None)
        if q is not None:
            lost.remove(q)
            lines += [f"CHANGED SKIP {q['id']} -> {f['id']}",
                      f"  BEFORE: [{q['span'][0]}, {q['span'][1]}] · {flat(q['reason'])}",
                      f"  AFTER: [{a}, {b}] · {flat(f['reason'])}"]
        else:
            lines += [f"ADDED SKIP {f['id']}: [{a}, {b}] · {flat(f['reason'])}"]
    for q in lost:
        lines += [f"REMOVED SKIP {q['id']}: [{q['span'][0]}, {q['span'][1]}] · "
                  f"{flat(q['reason'])}"]
    kept = [s for p in doc.get("passages") or [] if (s := ia.span_of(p))]
    under_now = {tuple(p["span"]) for p in ia.math_under_skips(doc, paragraphs)}
    under_then = {tuple(p["span"]) for p in ia.math_under_skips(previous, paragraphs)}
    for v in (verdicts or {}).get("verdicts") or []:
        if v.get("verdict") != ia.SKIP_VERDICT or not v.get("span"):
            continue
        a, b = v["span"]

        def inside(spans) -> int:
            return sum(1 for x, y in spans if x < b and a < y)

        statements = v.get("statements") or []
        held = 0
        for st in statements:
            at = ia.find_folded(layer, a, b, st.get("at") or "")
            held += at is not None and any(x <= at < y for x, y in kept)
        remain = [f for f in new if f["span"][0] < b and a < f["span"][1]]
        rest = ("the skip is gone" if not remain else
                f"{len(remain)} skip(s) remain in its span: " + ", ".join(
                    f"{f['id']} [{f['span'][0]}, {f['span'][1]}]" for f in remain))
        named = v.get("skip") or ", ".join(v.get("skips") or []) or "no skip"
        lines.append(f"SKIP VERDICT {v['id']} on {named} [{a}, {b}]: {inside(under_then)} math "
                     f"paragraph(s) unheld before, {inside(under_now)} now; {held} of its "
                     f"{len(statements)} statement(s) now start inside a passage; {rest}.")
    return lines


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
    ap.add_argument("--show", action="append", metavar="ITEM",
                    help="print one passage (rule 2: or one skip, by its id)")
    ap.add_argument("--context", type=int, default=CONTEXT_DEFAULT)
    ap.add_argument("--rule", type=int, default=ia.DEFAULT_RULE,
                    help="the audit rule the run names (2 renders the skips; default 1)")
    ap.add_argument("--previous-verdicts",
                    help="rule 2, with --previous: the last round's verdicts, whose skip "
                         "verdicts the diff counts")
    args = ap.parse_args(argv)
    try:
        rule = ia.known_rule(args.rule)
        if args.previous_verdicts and not (rule >= 2 and args.previous):
            raise ia.InputError("--previous-verdicts goes with --rule 2 and --previous")
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
        paragraphs = view.paragraphs() if rule >= 2 else None
        if args.show:
            print(show(doc, view.layer, blocks, args.show, args.context, rule=rule,
                       paragraphs=paragraphs))
            return ia.EXIT_OK
        if not args.out_dir:
            sys.stdout.write(render(doc, view.layer, blocks, rule=rule, paragraphs=paragraphs))
            return ia.EXIT_OK
        out = _out_dir(args.out_dir)
        previous = ia.read_document(args.previous) if args.previous else None
        if previous is not None and previous.get("source_id") != sid:
            raise ia.InputError(f"--previous is of {previous.get('source_id')!r}, not {sid!r}")
        verdicts = (ia.read_json(args.previous_verdicts, "previous verdicts")
                    if args.previous_verdicts else None)
    except ia.InputError as exc:
        print(f"inventory_render: {exc}", file=sys.stderr)
        return ia.EXIT_INPUT
    except ia.Broken as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return ia.EXIT_BROKEN
    out.mkdir(parents=True, exist_ok=True)
    index = {"schema": ia.AUDIT_SCHEMAS[rule], "kind": "render", "label": args.label,
             "source_id": sid, "document_sha256": ia.sha256_file(args.document),
             "layer_sha256": pinned, "passages": len(doc["passages"]),
             "formal": len(ia.formal_items(doc)), "part_bytes": args.part_bytes, "parts": [],
             "diff": None}
    k = int(args.label[1:])
    keys = ("first", "last", "items", "formal") + (("skips",) if rule >= 2 else ())
    cut = parts(doc, view.layer, blocks, args.part_bytes, rule=rule, paragraphs=paragraphs,
                label=args.label)
    for i, part in enumerate(cut, 1):
        name = f"{args.label}.render.part{i}.md"
        (out / name).write_text(part["text"], encoding="utf-8")
        index["parts"].append({"file": name, "bytes": len(part["text"].encode("utf-8")),
                               **{key: part[key] for key in keys}})
    if rule >= 2:
        owner = {sid_: i for i, part in enumerate(cut, 1) for sid_ in part["skips"]}
        index["rule"] = rule
        index["skips"] = [{"id": f["id"], "span": f["span"], "reason": f["reason"],
                           "part": owner.get(f["id"]), "blocks": len(f["blocks"]),
                           "math": f["math"], "unheld": f["unheld"],
                           "unheld_outside": f["unheld_outside"], "furniture": f["furniture"],
                           "rule": f["rule"], "sections": f["sections"], "inside": f["inside"]}
                          for f in ia.skip_facts(doc, paragraphs, k)]
    if previous is not None:
        name = f"{args.label}.diff.md"
        text = diff(previous, doc, view.layer, blocks)
        if rule >= 2:
            text += "\n".join(skip_diff(previous, doc, view.layer, paragraphs,
                                         before_round=max(1, k - 1), after_round=k,
                                         verdicts=verdicts)) + "\n"
        (out / name).write_text(text, encoding="utf-8")
        index["diff"] = name
    ia.write_json(out / f"{args.label}.render.json", index)
    print(json.dumps({"parts": [p["file"] for p in index["parts"]], "diff": index["diff"],
                      "index": f"{args.label}.render.json"}))
    return ia.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
