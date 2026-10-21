#!/usr/bin/env python3
"""Fill an inventory document's spans and passage ids from its quotes -- stdlib only.

New code, for docs/proposals/wikipedia-locate.md section 2.2 and
references/locate-documents.md section 2 ("``item`` must equal the passage id
of ``span``; the gate recomputes it").  A worker cannot count code points in a
layer or hash a head, and must not try: it writes each passage's quote, and
this tool -- copied into the gate directory beside ``locate_text.py``, run by
absolute path -- places every quote on the source's ``layer.txt`` (exactly
once, with its prefix and suffix), converts the start to the head text, keys
ALL of the document's passages together with the frozen head rule (an id
depends on the other passages of the source), and writes ``span``,
``head_words`` and ``item`` back.  It decides nothing else, and it refuses
what the gate would refuse: a quote that is not on the layer exactly once, a
start inside a word or a formula, two passages with one head.

A passage ends at ``until`` when the passage gives one (the end of the first
occurrence of that string after the start).  Without one it ends by the
span-end rule its run names (``locate_text.passage_end``, the rule gate rule 2
re-computes): under version 1 at the end of the paragraph of
``paragraphs.json`` it starts in, else at the end of the quote; under version
2, the statement cut's (docs/proposals/statement-cut.md section 3.2), at the
earlier of its extent -- its block, and the displays its last sentence
introduces -- and the next passage's start, so the tool places every passage
first and ends each in a second pass.  ``until`` stays in the document, so the
gate can re-compute the end from it and refuse any other: an offset edited by
hand, or counted in UTF-16 units.  Everything else a passage carries -- its
``conjuncts`` and ``shape`` among them -- is written back as it was.

The version is the ``span_end_rule`` of the ``gate.json`` beside this file (a
staged gate directory always holds one; one without the key is version 1);
with no ``gate.json`` beside it, ``--span-end-rule`` (default 1) names it, and
the two disagreeing is an input error.

    passage_ids.py --source-dir <sources>/<source-id> --document findings/<task>/<source>.json [--write]

Exit 0 when every passage was placed, 1 when one was not (each named on
stderr), 2 when the inputs cannot be read -- a source directory without its
``layer.txt``, ``formulas.json`` or ``paragraphs.json`` among them, as the gate
refuses one, or a span-end rule this tool does not know.
"""
from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parent / "gate"):
    if (_candidate / "locate_text.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

import locate_text as lt  # noqa: E402

#: the item spelling ``cflib.items.passage_item`` writes
ITEM_PREFIX = "In-text #"


def place(doc: dict, layer: str, formulas: list, paragraphs: list, *,
          rule: int = 1) -> list[str]:
    """Fill the document's passages in place; return the problems, one per passage.

    Two passes: every quote is placed first, then each passage is ended by
    span-end rule ``rule`` -- version 2 reads the next passage's start, so it
    needs them all.  A passage that cannot be placed names its problem in
    document order, as it always did.
    """
    problems: dict[int, str] = {}
    head_map = lt.HeadMap(layer, formulas)
    found: list[tuple[int, dict, int, int, str]] = []   # (index, passage, start, head start, exact)
    for index, passage in enumerate(doc.get("passages") or []):
        where = f"passages[{index}]"
        quote = passage.get("quote") if isinstance(passage.get("quote"), dict) else {}
        exact = str(quote.get("exact") or "")
        hits = lt.find_quote(layer, exact, quote.get("prefix") or "", quote.get("suffix") or "")
        if len(hits) != 1:
            problems[index] = (f"{where}: the quote occurs {len(hits)} times on the layer "
                               f"({exact[:40]!r}); it must occur exactly once with its "
                               f"prefix and suffix")
            continue
        start = hits[0]
        try:
            head_start = lt.check_start(head_map.head, head_map.to_head(start))
        except lt.HeadError as exc:
            problems[index] = f"{where}: {exc}"
            continue
        found.append((index, passage, start, head_start, exact))
    table = lt.Blocks(layer, paragraphs, formulas) if rule == lt.SPAN_END_STATEMENT_CUT \
        else paragraphs
    starts = sorted({start for _i, _p, start, _h, _e in found})
    placed: list[tuple[dict, int]] = []
    for index, passage, start, head_start, exact in found:
        after = bisect_right(starts, start)
        try:
            end = lt.passage_end(layer, table, start, exact, passage.get("until"), rule=rule,
                                 next_start=starts[after] if after < len(starts) else None,
                                 formulas=formulas)
        except lt.HeadError as exc:
            problems[index] = f"passages[{index}]: {exc}"
            continue
        passage["span"] = [start, end]
        placed.append((passage, head_start))
    if problems:
        return [problems[i] for i in sorted(problems)]
    try:
        ids = lt.assign_ids(head_map.head, [h for _p, h in placed])
    except lt.HeadError as exc:
        return [str(exc)]
    for (passage, _h), (_start, digest, words) in zip(placed, ids):
        passage["item"] = ITEM_PREFIX + digest
        passage["head_words"] = words
    return []


def end_rule(flag: int | None, here: Path = _HERE) -> int:
    """The span-end rule this run names: the ``gate.json`` beside the tool, else the flag.

    A staged gate directory holds its run's ``gate.json``; its ``span_end_rule``
    (absent: a run planned before the rule, ended by version 1) is the run's.
    Without one -- the tool run from a checkout -- the flag says it, default 1.
    A version this tool does not know, or a flag that disagrees with the
    ``gate.json``, is a ``ValueError``: the inputs are wrong, not a passage.
    """
    gate = here / "gate.json"
    if not gate.is_file():
        version = 1 if flag is None else flag
        if version not in lt.SPAN_END_RULES:
            raise ValueError(f"--span-end-rule {version} is not one of {lt.SPAN_END_RULES}")
        return version
    try:
        data = json.loads(gate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"the gate.json beside this tool is unreadable ({exc})") from None
    if not isinstance(data, dict):
        raise ValueError("the gate.json beside this tool is not a JSON object")
    version = data.get("span_end_rule")
    if version is None:
        version = 0
    if isinstance(version, bool) or not isinstance(version, int) \
            or (version or 1) not in lt.SPAN_END_RULES:
        raise ValueError(f"the gate.json beside this tool names span_end_rule {version!r}, "
                         f"and this tool knows {lt.SPAN_END_RULES}")
    version = version or 1
    if flag is not None and flag != version:
        raise ValueError(f"--span-end-rule {flag} disagrees with the gate.json beside this "
                         f"tool, which names {version}: the run's gate.json decides")
    return version


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-dir", required=True, help="the rendered source's directory")
    ap.add_argument("--document", required=True, help="the inventory document to fill")
    ap.add_argument("--write", action="store_true", help="write the filled document back")
    ap.add_argument("--span-end-rule", type=int, default=None,
                    help="the span-end rule to end passages by when no gate.json is beside "
                         "this tool (default 1; a staged gate directory's gate.json decides)")
    args = ap.parse_args(argv)
    source = Path(args.source_dir)
    try:
        rule = end_rule(args.span_end_rule)
    except ValueError as exc:
        print(f"passage_ids: {exc}", file=sys.stderr)
        return 2
    try:
        layer = (source / "layer.txt").read_text(encoding="utf-8")
        formulas = json.loads((source / "formulas.json").read_text(encoding="utf-8"))
        paragraphs = json.loads((source / "paragraphs.json").read_text(encoding="utf-8"))
        doc = json.loads(Path(args.document).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"passage_ids: cannot read the inputs ({exc})", file=sys.stderr)
        return 2
    if not isinstance(doc, dict):
        print("passage_ids: the document is not a JSON object", file=sys.stderr)
        return 2
    try:
        problems = place(doc, layer, formulas, paragraphs, rule=rule)
    except lt.HeadError as exc:
        print(f"passage_ids: the source's formulas cannot be read ({exc})", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(f"passage_ids: {problem}", file=sys.stderr)
        return 1
    if args.write:
        Path(args.document).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                                       encoding="utf-8")
    for passage in doc.get("passages") or []:
        print(json.dumps({"item": passage["item"], "span": passage["span"],
                          "head_words": passage["head_words"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
