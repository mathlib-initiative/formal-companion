"""Derive the M1 calibration's truth table from a companion's annotation document.

New code for milestone M1 of docs/proposals/pdf-to-companion.md, section 5.2,
after the review of pull request 16: the calibration needs only where each
numbered item and passage of the edition starts, so the companion's document
(Lean signatures, snippets, verdicts, book-text anchors) never enters this
repository (CLAUDE.md section 1).  What the table holds, and all it may hold:

* ``numbered``: ``{kind, number, page}`` per numbered item of the source, with
  ``page`` the first physical page any of its locations shows (``null`` when
  it has none);
* ``passages``: ``{page, near}`` per passage, ``near`` the ids of the numbered
  items it sits beside;
* a header naming the table's format, the companion and the edition's sha256.

These are facts about the edition, like the registry's page maps; no quote,
title, declaration or verdict.  :func:`check_table` refuses any other field
and any value that could carry text, and :func:`write_truth` writes nothing
it has not passed.  A numbered item whose kind the producer supplied
(``kind_source: producer``) is a label the book never prints; it is left out
and counted, and so is one whose kind or number is not a word and a dotted
number, or a ``near`` id that names no numbered item; the script prints each.

Run by the maintainer, by path, on a document that stays outside the tree::

    python3 scripts/pipeline/derive_truth.py <document.json[.gz]> --companion NAME \\
        [--source-id tao-measure-gsm126-published] [--out tests/data/m1/ followed by the companion name and .truth.json]
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import write_json  # noqa: E402
from cflib.registry import Registry  # noqa: E402
from pipeline import recall  # noqa: E402

FORMAT = "cf-m1-truth/1"
DEFAULT_SOURCE = "tao-measure-gsm126-published"
DATA_DIR = _SCRIPTS.parent / "tests" / "data" / "m1"

TOP_KEYS = frozenset({"format", "companion", "source_sha256", "numbered", "passages"})
NUMBERED_KEYS = frozenset({"kind", "number", "page"})
PASSAGE_KEYS = frozenset({"page", "near"})

_KIND = re.compile(r"^[A-Z][A-Za-z]{0,30}$")
_NUMBER = re.compile(r"^\d{1,3}(?:\.\d{1,3}){0,3}[a-z]?$")
_ITEM_ID = re.compile(r"^[a-z][a-z-]{0,30}-\d{1,3}(?:\.\d{1,3}){0,3}[a-z]?$")
_COMPANION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def load_document(path: str | Path) -> dict:
    """An annotation document, plain or gzipped JSON."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def derive(doc: Mapping[str, Any], sha256: str, companion: str) -> tuple[dict, dict]:
    """``(table, counts)``: the truth table of ``doc`` for the source with ``sha256``."""
    if recall._source(dict(doc), sha256) is None:
        raise CfError(f"the document has no source with sha256 {sha256[:12]}...",
                      hint="pass --source-id (or --sha256) of the edition the document annotates")
    numbered, producer, unshaped, near_dropped = [], 0, [], 0
    for row in recall.numbered_rows(dict(doc), sha256):
        if row["producer_kind"]:
            producer += 1
            continue
        if not (_KIND.match(str(row["kind"])) and _NUMBER.match(str(row["number"]))):
            unshaped.append(row["id"])
            continue
        numbered.append({"kind": row["kind"], "number": row["number"],
                         "page": row["pages"][0] if row["pages"] else None})
    passages = []
    for r in recall.passage_truth(dict(doc), sha256, 10 ** 9):
        near = [i for i in r["near"] if _ITEM_ID.match(i)]
        near_dropped += len(r["near"]) - len(near)
        passages.append({"page": r["page"], "near": near})
    numbered.sort(key=lambda r: (r["page"] is None, r["page"] or 0, r["kind"], r["number"]))
    passages.sort(key=lambda r: (r["page"], r["near"]))
    table = {"format": FORMAT, "companion": companion, "source_sha256": sha256,
             "numbered": numbered, "passages": passages}
    return table, {"numbered": len(numbered), "passages": len(passages),
                   "left_out_producer_kind": producer, "left_out_unshaped": unshaped,
                   "near_ids_dropped": near_dropped}


def check_table(table: Any) -> list[str]:
    """Every way ``table`` holds more than the derived facts; empty when it holds only them."""
    if not isinstance(table, dict):
        return ["the table is not an object"]
    problems = [f"field {k!r} is not allowed" for k in sorted(set(table) - TOP_KEYS)]
    problems += [f"field {k!r} is missing" for k in sorted(TOP_KEYS - set(table))]
    if table.get("format") != FORMAT:
        problems.append(f"format must be {FORMAT}")
    if not (isinstance(table.get("companion"), str) and _COMPANION.match(table["companion"])):
        problems.append("companion must be a short name")
    if not (isinstance(table.get("source_sha256"), str) and _SHA.match(table["source_sha256"])):
        problems.append("source_sha256 must be 64 hex characters")
    for n, row in enumerate(table.get("numbered") or []):
        where = f"numbered[{n}]"
        if not isinstance(row, dict):
            problems.append(f"{where} is not an object")
            continue
        problems += [f"{where}: field {k!r} is not allowed" for k in sorted(set(row) - NUMBERED_KEYS)]
        if not (isinstance(row.get("kind"), str) and _KIND.match(row["kind"])):
            problems.append(f"{where}: kind must be one word")
        if not (isinstance(row.get("number"), str) and _NUMBER.match(row["number"])):
            problems.append(f"{where}: number must be a dotted number")
        if not (row.get("page") is None or _is_page(row["page"])):
            problems.append(f"{where}: page must be a positive integer or null")
    for n, row in enumerate(table.get("passages") or []):
        where = f"passages[{n}]"
        if not isinstance(row, dict):
            problems.append(f"{where} is not an object")
            continue
        problems += [f"{where}: field {k!r} is not allowed" for k in sorted(set(row) - PASSAGE_KEYS)]
        if not _is_page(row.get("page")):
            problems.append(f"{where}: page must be a positive integer")
        near = row.get("near")
        if not (isinstance(near, list) and all(isinstance(i, str) and _ITEM_ID.match(i)
                                               for i in near)):
            problems.append(f"{where}: near must be a list of numbered item ids")
    for key in ("numbered", "passages"):
        if not isinstance(table.get(key), list):
            problems.append(f"{key} must be a list")
    return problems


def _is_page(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def write_truth(path: str | Path, table: Mapping[str, Any]) -> Path:
    """Write ``table`` after :func:`check_table` passes it; refuse otherwise."""
    problems = check_table(dict(table))
    if problems:
        raise CfError("refusing to write the truth table: " + "; ".join(problems[:10]),
                      hint="the table holds {kind, number, page} and {page, near} only")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, dict(table), indent=1, sort_keys=True)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("document", help="the companion's annotation document (.json or .json.gz); "
                                     "it stays outside this repository")
    ap.add_argument("--companion", required=True, help="the companion's name, e.g. measure_theory")
    ap.add_argument("--source-id", default=DEFAULT_SOURCE,
                    help=f"the registry edition the table is about (default {DEFAULT_SOURCE})")
    ap.add_argument("--sha256", default=None, help="the edition's sha256, instead of --source-id")
    ap.add_argument("--out", default=None,
                    help="where to write (default tests/data/m1/<companion>.truth.json)")
    args = ap.parse_args(argv)
    try:
        sha = args.sha256
        if not sha:
            entry = Registry.load(None).get(args.source_id)
            if entry is None or not entry.sha256:
                raise CfError(f"the registry has no sha256 for {args.source_id}")
            sha = entry.sha256
        table, counts = derive(load_document(args.document), sha, args.companion)
        out = write_truth(args.out or DATA_DIR / f"{args.companion}.truth.json", table)
    except CfError as exc:
        print(f"error: {exc}" + (f"\nhint: {exc.hint}" if getattr(exc, "hint", None) else ""),
              file=sys.stderr)
        return 1
    print(f"{out}: {counts['numbered']} numbered items, {counts['passages']} passages")
    if counts["left_out_producer_kind"]:
        print(f"  left out: {counts['left_out_producer_kind']} producer-kind item(s)")
    if counts["left_out_unshaped"]:
        print(f"  left out: {len(counts['left_out_unshaped'])} item(s) whose kind or number is "
              f"not a word and a dotted number: {', '.join(counts['left_out_unshaped'][:10])}")
    if counts["near_ids_dropped"]:
        print(f"  dropped: {counts['near_ids_dropped']} near id(s) that name no numbered item")
    return 0


if __name__ == "__main__":
    sys.exit(main())
