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
VERDICT line per skip verdict of the last round.  Under ``--rule 3`` (section 13.12) the layer
no passage and no skip holds is drawn block by block (:class:`GapPainter`): each gap
(``inventory_audit.gap_facts``) a GAP line of its own with its id ``r<k>-g<nnn>``, span, block,
words and section, in full to ``inventory_audit.GAP_BLOCK_MAX`` characters; the header gains
the gaps' legend and line, the HINTs ``commentary`` and the kind rule's reach over every formal
``other`` passage; the index gains ``gaps`` and each part the gaps it shows; ``--show <gap
id>`` prints one gap whole; and the diff gains its GAPS line and a GAP VERDICT line per gap
verdict of the last round.  Without ``--rule`` (rule 1) every output is the base commit's,
byte for byte, and rule 2's is 3b29518's.

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
#: rule 2's skip blocks: a math block (but the back matter's) in full to MATH_MAX characters
#: (its first and last MATH_SIDE past that), a run of LIST_RUN or more list items with no
#: math one line (each end LIST_SIDE characters), and past ia.SKIP_BLOCK_BYTES one clipped
#: line a block
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
    "block with its span inside the skip (a math block in full but in the back matter, a run "
    "of list items with no math as one … line); ⋯ for text no block holds; HINT.",
    "Skip HINTs: skip-rule (it covers math no passage holds in a proof, an example or a "
    "mostly skipped section: the stage gives it a skipped_statements verdict unless the audit "
    "says, with a reason, that the skip is right); in-proof; in-example; mostly-skipped (a "
    "section of it is mostly skipped); partial (it starts or ends inside a block); furniture "
    "(every block is in the lead or the back matter, or it holds no block and lies in the "
    "lead).",
]


EXPLAIN_GAPS = [
    "GAP lines (rule 3) are drawn block by block. A GAP r<k>-g<nnn> line is a gap, named in "
    f"this round's numbering in span order: a run of {ia.GAP_MIN_WORDS} or more words of one "
    "block of the body that no passage and no skip holds -- GAP id · span · block · words · "
    f"section: its text, in full to {ia.GAP_BLOCK_MAX} characters (its first and last "
    f"{ia.GAP_BLOCK_SIDE} past that). A GAP [a, b] line is a shorter run of a body block, in "
    "full; a GAP line with no span is text no block of the body holds (a heading, the back "
    f"matter), past {GAP_MAX} characters its first and last {GAP_SIDE}.",
    "Rule 3's HINTs: kind-rule marks every formal passage of kind other; commentary (advisory, "
    "never a verdict) marks a formal passage that reads as commentary on a method (an "
    "efficiency word, no bound), an instruction (it starts compute, proceed, repeat, for each "
    "of them or to illustrate) or a meta remark (need not be, may be used for, follows from "
    "this proof, one additional fact): judge whether it asserts anything (C6, PD5).",
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


def hints(passage: dict, layer: str, blocks, rule: int = ia.DEFAULT_RULE) -> list[str]:
    """The computed hints of one passage (see the module docstring); under rule 3 the kind
    rule's reach is every formal ``other`` passage and ``commentary`` may follow."""
    out = []
    formal = ia.is_formal(passage)
    if formal and passage.get("passage_kind") == ia.KIND_OTHER:
        out.append("kind-other")
    if ia.kind_rule_applies(passage, rule):
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
        if formal and rule >= 3 and ia.commentary_hint(layer[span[0]:span[1]]):
            out.append("commentary")
    return out


def passage_lines(n: int, passage: dict, layer: str, blocks,
                  rule: int = ia.DEFAULT_RULE) -> list[str]:
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
    found = hints(passage, layer, blocks, rule)
    if found:
        lines.append("  HINT: " + " ".join(found))
    return lines


def header(doc: dict, skips: dict | None = None, gaps: list[str] | None = None) -> list[str]:
    """The rendering's header; under rule 2 (``skips``, :func:`skip_summary`) also the
    skips' line, the SKIP blocks' legend when the document has a skip (every part repeats
    the header, so a document with none pays for no legend) and, when a section is mostly
    skipped, the ATTENTION line; under rule 3 (``gaps``, :func:`gap_summary`) the gaps'
    legend and line too."""
    rows = doc.get("passages") or []
    formal = sum(ia.is_formal(p) for p in rows)
    notes = flat(str(doc.get("notes") or "")).strip() or "(none)"
    if skips is None:
        return [f"# {doc.get('source_id')}: inventory, {len(rows)} passages ({formal} formal)",
                "", *EXPLAIN, f"Worker notes: {notes}", ""]
    legend = EXPLAIN_SKIPS if skips["report"]["skips"] else []
    if gaps is not None:
        legend = [*legend, *EXPLAIN_GAPS]
    return [f"# {doc.get('source_id')}: inventory, {len(rows)} passages ({formal} formal)", "",
            *EXPLAIN, *legend, f"Worker notes: {notes}", *skips["lines"], *(gaps or []), ""]


def gap_summary(facts: list[dict]) -> list[str]:
    """Rule 3's header line: how many gaps the document leaves, and their words."""
    if not facts:
        return ["Gaps: none."]
    ids = facts[0]["id"] if len(facts) == 1 else f"{facts[0]['id']} to {facts[-1]['id']}"
    return [f"Gaps: {len(facts)} ({ids}), {sum(f['words'] for f in facts)} word(s): runs of "
            f"{ia.GAP_MIN_WORDS} or more words of the body that no passage and no skip holds; "
            f"judge each once."]


def gap_line3(fact: dict, layer: str, *, cap: bool = True) -> str:
    """One gap's line (rule 3): its id, span, block, words and section, and its text."""
    a, b = fact["span"]
    text = layer[a:b]
    if cap:
        text = clip(text, ia.GAP_BLOCK_MAX, ia.GAP_BLOCK_SIDE)
    section = (fact["section"] if isinstance(fact["section"], str)
               else ia.section_key(fact["section"]))
    return (f"GAP {fact['id']} · [{a}, {b}] · {fact['block'] or 'block'} · {fact['words']} "
            f"word(s) · {section}: {flat(text)}")


class GapPainter:
    """Rule 3's GAP lines over the layer no passage and no skip holds, block by block
    (:func:`inventory_audit.own_text`), each gap of ``facts`` as its own line, and which
    passage's lines show each gap (``shown``: the passage number, or None for the tail)."""

    def __init__(self, layer: str, paragraphs: list[dict], facts: list[dict]):
        self.layer = layer
        self.pieces = ia.own_text(paragraphs)
        self.facts = facts
        self.shown: dict[str, int | None] = {}

    def lines(self, lo: int, hi: int, owner: int | None, *, last: bool = False) -> list[str]:
        layer = self.layer
        segs: list[tuple[int, int, dict | None]] = []
        cursor = lo
        for piece in self.pieces:
            x, y = max(lo, piece["span"][0]), min(hi, piece["span"][1])
            if x >= y:
                continue
            if x > cursor:
                segs.append((cursor, x, None))
            segs.append((x, y, piece))
            cursor = max(cursor, y)
        if cursor < hi:
            segs.append((cursor, hi, None))
        out: list[str] = []
        run: list[int] | None = None

        def flush(final: bool) -> None:
            if run is not None:
                line = gap_line(layer[run[0]:run[1]], "GAP (to end)" if final else "GAP")
                if line:
                    out.append(line)

        for n, (a, b, piece) in enumerate(segs):
            if piece is None or ia.is_back_matter(piece["section"]):
                run = [run[0], b] if run is not None else [a, b]
                continue
            flush(False)
            run = None
            text = layer[a:b]
            if not text.strip():
                continue
            fact = next((f for f in self.facts if a <= f["span"][0] and f["span"][1] <= b
                         and f["id"] not in self.shown), None)
            if fact is not None:
                out.append(gap_line3(fact, layer))
                self.shown[fact["id"]] = owner
                continue
            x = a + len(text) - len(text.lstrip())
            y = b - (len(text) - len(text.rstrip()))
            out.append(f"GAP [{x}, {y}] {piece['block'] or 'block'}: "
                       f"{flat(clip(layer[x:y], ia.GAP_BLOCK_MAX, ia.GAP_BLOCK_SIDE))}")
        flush(last)
        return out

    def leftover(self) -> list[str]:
        """Any gap no line drew (none, as the gaps are cut from the same pieces): drawn at the
        end, the tail's."""
        out = []
        for fact in self.facts:
            if fact["id"] not in self.shown:
                out.append(gap_line3(fact, self.layer))
                self.shown[fact["id"]] = None
        return out


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


def _back_matter(section: list[str]) -> bool:
    """A block of the back matter: furniture, not the lead (whose math may state the
    subject, and is shown in full)."""
    return bool(section) and ia.is_furniture(section)


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
            elif block["math"] and not _back_matter(block["section"]):
                text = clip(text, MATH_MAX, MATH_SIDE)
            else:
                # prose, and a math block of the back matter (a bibliography entry with a
                # formula in its title): clipped as a GAP line is
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


def entries2(doc: dict, layer: str, blocks, facts: list[dict],
             painter: "GapPainter | None" = None, rule: int = 2
             ) -> tuple[list[tuple[int, dict, list[str]]], list[str], dict[str, int | None]]:
    """Rule 2's :func:`entries`: each skip as a block in place, in the gap before the next
    passage (its text split around it), and ``{skip id: the passage number whose lines
    show it, or None for the tail}``.  A skip whose text lies inside kept passages (a finder
    nested in one) is one entry of the closing ``SKIPS inside kept passages`` line.  Under
    rule 3 (``painter``) the text around the skips is drawn block by block, each gap a line of
    its own (:class:`GapPainter`, whose ``shown`` says which passage's lines hold each)."""
    shown: dict[str, int | None] = {}

    def gap_text(lo: int, hi: int, owner: int | None, last: bool) -> list[str]:
        if painter is not None:
            return painter.lines(lo, hi, owner, last=last)
        gap = (gap_line(layer[lo:lo + GAP_MAX], "GAP (to end)") if last
               else gap_line(layer[lo:hi]))
        return [gap] if gap else []

    def gap_and_skips(lo: int, hi: int, owner: int | None, last: bool = False) -> list[str]:
        lines, cur = [], lo
        for fact in facts:
            a, b = fact["span"]
            if fact["id"] in shown or fact["inside"] or a >= hi or b <= lo:
                continue
            shown[fact["id"]] = owner
            if a > cur:
                lines.extend(gap_text(cur, a, owner, False))
            lines.extend(skip_lines(fact, layer))
            cur = max(cur, b)
        if cur < hi:
            lines.extend(gap_text(cur, hi, owner, last))
        return lines

    out = []
    pos = 0
    for n, passage in enumerate(ia.ordered(doc), 1):
        span = ia.span_of(passage) or (pos, pos)
        lines = gap_and_skips(pos, span[0], n) if span[0] > pos else []
        lines.extend(passage_lines(n, passage, layer, blocks, rule))
        out.append((n, passage, lines))
        pos = max(pos, span[1])
    tail = gap_and_skips(pos, len(layer), None, last=True) if pos < len(layer) else []
    if painter is not None:
        tail += painter.leftover()
    inner = [f for f in facts if f["id"] not in shown]
    if inner:
        tail += ["", "SKIPS inside kept passages: " + "; ".join(
            f"{f['id']} [{f['span'][0]}, {f['span'][1]}] {flat(f['reason'])}" for f in inner)]
        for f in inner:
            shown[f["id"]] = None
    return out, tail, shown


def _rule2_rows(doc: dict, layer: str, blocks, rule: int, paragraphs: list[dict] | None,
               label: str):
    """``(rows, tail, shown skips, header, painter)`` of a rule-2 or rule-3 rendering."""
    k = int(label[1:])
    facts = ia.skip_facts(doc, paragraphs or [], k)
    painter = None
    gaps = None
    if rule >= 3:
        painter = GapPainter(layer, paragraphs or [], ia.gap_facts(doc, paragraphs or [],
                                                                   layer, k))
        gaps = gap_summary(painter.facts)
    rows, tail, shown = entries2(doc, layer, blocks, facts, painter, rule)
    head = header(doc, skip_summary(doc, facts, paragraphs or []), gaps)
    return rows, tail, shown, head, painter


def render(doc: dict, layer: str, blocks, *, rule: int = ia.DEFAULT_RULE,
           paragraphs: list[dict] | None = None, label: str = "r1") -> str:
    if rule >= 2:
        rows, tail, _shown, head, _painter = _rule2_rows(doc, layer, blocks, rule, paragraphs,
                                                         label)
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
    rule 2 each part also names the skips it shows (``skips``), the tail's in the last, and
    under rule 3 the gaps (``gaps``) the same way."""
    shown: dict[str, int | None] = {}
    painter = None
    if rule >= 2:
        rows, tail, shown, head, painter = _rule2_rows(doc, layer, blocks, rule, paragraphs,
                                                       label)
    else:
        rows, tail = entries(doc, layer, blocks)
        head = header(doc)
    groups: list[list[tuple[int, dict, list[str]]]] = [[]]
    size = nbytes(head)
    # under rule 2 the tail (the gap to the end and the skips after the last passage, each
    # SKIP block up to ia.SKIP_BLOCK_BYTES) rides with the last passage, so it counts
    # toward the last part as a passage's gap and skips count toward theirs; rule 1's tail
    # is the base commit's, one capped line, and its parts are the base commit's
    tail_cost = nbytes(tail) if rule >= 2 else 0
    for j, row in enumerate(rows):
        cost = nbytes(row[2]) + (tail_cost if j == len(rows) - 1 else 0)
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
        if painter is not None:
            out[-1]["gaps"] = [gid for gid, n in painter.shown.items()
                               if n in numbers or (n is None and i == len(groups))]
    if rule >= 2 and not out:
        # a document with no passage is never rendered by the stage; its skips still have a part
        out.append({"text": "\n".join(head + tail) + "\n", "first": 0, "last": 0, "items": [],
                    "formal": [], "skips": list(shown)})
        if painter is not None:
            out[-1]["gaps"] = list(painter.shown)
    return out


def show(doc: dict, layer: str, blocks, items: list[str], context: int, *,
         rule: int = ia.DEFAULT_RULE, paragraphs: list[dict] | None = None) -> str:
    """The passages named, each with ``context`` characters of layer around it; under rule 2
    a skip id (``r<k>-s<nnn>``, the document's skips in span order) prints that skip's
    block whole, with the layer around it."""
    numbered = {str(p.get("item")): (n, p) for n, p in enumerate(ia.ordered(doc), 1)}
    out = []
    for item in items:
        if rule >= 3 and ia.gap_number(item):
            gaps = {f["id"]: f for f in ia.gap_facts(doc, paragraphs or [], layer,
                                                     ia.gap_number(item)[0])}
            if item not in gaps:
                raise ia.InputError(f"{item} is no gap of the document")
            fact = gaps[item]
            a, b = fact["span"]
            out.extend([gap_line3(fact, layer, cap=False),
                        f"  BEFORE: {flat(layer[max(0, a - context):a])}",
                        f"  AFTER: {flat(layer[b:b + context])}", ""])
            continue
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
        lines = passage_lines(n, passage, layer, blocks, rule)
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


def diff(previous: dict, doc: dict, layer: str, blocks, rule: int = ia.DEFAULT_RULE) -> str:
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
        lines += ["    " + x for x in passage_lines(nb, pb, layer, blocks, rule)]
        lines += ["  AFTER:"] + ["    " + x for x in passage_lines(na, pa, layer, blocks, rule)]
        blocks_out.append((na, lines))
    prev_rows = [p for _n, p in before.values()]
    cur_rows = [p for _n, p in after.values()]
    for _item, (n, p) in new.items():
        held = _overlaps(p, prev_rows)
        lines = [f"ADDED {p.get('item')}" + (f" (inside {', '.join(held)} before)" if held else "")]
        lines += ["  AFTER:"] + ["    " + x for x in passage_lines(n, p, layer, blocks, rule)]
        blocks_out.append((n, lines))
    for _item, (n, p) in lost.items():
        held = _overlaps(p, cur_rows)
        lines = [f"REMOVED {p.get('item')}"
                 + (f" (its text now in {', '.join(held)})" if held else "")]
        lines += ["  BEFORE:"] + ["    " + x for x in passage_lines(n, p, layer, blocks, rule)]
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
    # the math entries, as the skip report counts them (two entries of one span are two)
    under_now = [p["span"] for p in ia.math_under_skips(doc, paragraphs)]
    under_then = [p["span"] for p in ia.math_under_skips(previous, paragraphs)]
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


def gap_diff(previous: dict, doc: dict, layer: str, paragraphs: list[dict], *,
             before_round: int, after_round: int, verdicts: dict | None) -> list[str]:
    """Rule 3's part of the diff: the gaps before and after, and a line per gap verdict of the
    last round with the counts the checker reads mechanically -- the words held by no passage
    and no skip in its span before and now, and how many of its statements now start inside a
    passage."""
    old = ia.gap_facts(previous, paragraphs, layer, before_round)
    new = ia.gap_facts(doc, paragraphs, layer, after_round)
    lines = [f"GAPS: {len(old)} before ({sum(f['words'] for f in old)} word(s)), {len(new)} "
             f"after ({sum(f['words'] for f in new)} word(s))"]
    kept = [s for p in doc.get("passages") or [] if (s := ia.span_of(p))]
    for v in (verdicts or {}).get("verdicts") or []:
        if v.get("verdict") != ia.GAP_VERDICT or not v.get("span"):
            continue
        a, b = v["span"]

        def words(facts) -> int:
            return sum(f["words"] for f in facts if f["span"][0] < b and a < f["span"][1])

        statements = v.get("statements") or []
        held = 0
        for st in statements:
            at = ia.find_folded(layer, a, b, st.get("at") or "")
            held += at is not None and any(x <= at < y for x, y in kept)
        named = v.get("gap") or ", ".join(v.get("gaps") or []) or "no gap"
        lines.append(f"GAP VERDICT {v['id']} on {named} [{a}, {b}]: {words(old)} word(s) held "
                     f"by no passage and no skip before, {words(new)} now; {held} of its "
                     f"{len(statements)} statement(s) now start inside a passage.")
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
                    help="the audit rule the run names (2 renders the skips, 3 the gaps "
                         "too; default 1)")
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
    keys = ("first", "last", "items", "formal") + (("skips",) if rule >= 2 else ()) \
        + (("gaps",) if rule >= 3 else ())
    cut = parts(doc, view.layer, blocks, args.part_bytes, rule=rule, paragraphs=paragraphs,
                label=args.label)
    for i, part in enumerate(cut, 1):
        name = f"{args.label}.render.part{i}.md"
        (out / name).write_text(part["text"], encoding="utf-8")
        index["parts"].append({"file": name, "bytes": len(part["text"].encode("utf-8")),
                               **{key: part[key] for key in keys}})
    if rule >= 3:
        gap_owner = {gid: i for i, part in enumerate(cut, 1) for gid in part["gaps"]}
        index["gaps"] = [{"id": f["id"], "span": f["span"], "part": gap_owner.get(f["id"]),
                          "words": f["words"], "block": f["block"],
                          "section": ia.section_key(f["section"]), "whole": f["whole"]}
                         for f in ia.gap_facts(doc, paragraphs, view.layer, k)]
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
        text = diff(previous, doc, view.layer, blocks, rule)
        if rule >= 2:
            text += "\n".join(skip_diff(previous, doc, view.layer, paragraphs,
                                         before_round=max(1, k - 1), after_round=k,
                                         verdicts=verdicts)) + "\n"
        if rule >= 3:
            text += "\n".join(gap_diff(previous, doc, view.layer, paragraphs,
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
