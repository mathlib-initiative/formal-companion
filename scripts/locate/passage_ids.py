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
occurrence of that string after the start), else at the end of the paragraph
of ``paragraphs.json`` it starts in, else at the end of the quote.

    passage_ids.py --source-dir <sources>/<source-id> --document findings/<task>/<source>.json [--write]

Exit 0 when every passage was placed, 1 when one was not (each named on
stderr), 2 when the inputs cannot be read -- a source directory without its
``layer.txt``, ``formulas.json`` or ``paragraphs.json`` among them, as the gate
refuses one.
"""
from __future__ import annotations

import argparse
import json
import sys
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


def _paragraph_end(paragraphs: list, start: int) -> int | None:
    for para in paragraphs:
        try:
            a, b = (int(x) for x in para["span"])
        except (KeyError, TypeError, ValueError):
            continue
        if a <= start < b:
            return b
    return None


def place(doc: dict, layer: str, formulas: list, paragraphs: list) -> list[str]:
    """Fill the document's passages in place; return the problems, one per passage."""
    problems: list[str] = []
    head_map = lt.HeadMap(layer, formulas)
    placed: list[tuple[dict, int]] = []
    for index, passage in enumerate(doc.get("passages") or []):
        where = f"passages[{index}]"
        quote = passage.get("quote") if isinstance(passage.get("quote"), dict) else {}
        exact = str(quote.get("exact") or "")
        hits = lt.find_quote(layer, exact, quote.get("prefix") or "", quote.get("suffix") or "")
        if len(hits) != 1:
            problems.append(f"{where}: the quote occurs {len(hits)} times on the layer "
                            f"({exact[:40]!r}); it must occur exactly once with its "
                            f"prefix and suffix")
            continue
        start = hits[0]
        try:
            head_start = lt.check_start(head_map.head, head_map.to_head(start))
        except lt.HeadError as exc:
            problems.append(f"{where}: {exc}")
            continue
        end = start + len(exact)
        until = passage.get("until")
        if until:
            at = layer.find(str(until), start)
            if at < 0:
                problems.append(f"{where}: `until` {str(until)[:40]!r} is not on the layer "
                                f"after the passage's start")
                continue
            end = max(end, at + len(str(until)))
        else:
            end = max(end, _paragraph_end(paragraphs, start) or end)
        passage["span"] = [start, end]
        placed.append((passage, head_start))
    if problems:
        return problems
    try:
        ids = lt.assign_ids(head_map.head, [h for _p, h in placed])
    except lt.HeadError as exc:
        return [str(exc)]
    for (passage, _h), (_start, digest, words) in zip(placed, ids):
        passage["item"] = ITEM_PREFIX + digest
        passage["head_words"] = words
        passage.pop("until", None)
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-dir", required=True, help="the rendered source's directory")
    ap.add_argument("--document", required=True, help="the inventory document to fill")
    ap.add_argument("--write", action="store_true", help="write the filled document back")
    args = ap.parse_args(argv)
    source = Path(args.source_dir)
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
        problems = place(doc, layer, formulas, paragraphs)
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
