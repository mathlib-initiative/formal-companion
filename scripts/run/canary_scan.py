#!/usr/bin/env python3
"""Scan a shard's transcripts for the bench's label canaries, on the run host.

New code, implementing docs/proposals/wikipedia-locate.md section 8.4 (a
transcript holding a label canary fails the run) for the campaign sequencer of
section 6 (references/locate-documents.md section 11).  The sequencer prunes a
shard's transcripts once their usage is exported, so the canary check the bench
needs has to run on the host first; this writes the report ``cf bench score
--canary-report`` reads, and ``cf harvest`` brings it home as
``runs/<run>/host/canary.json``.

    canary_scan.py --canaries <canaries.txt> --out <canary-<run>.json> -- <file>...

The canaries file holds one string per line (the bench's ``host/canaries.txt``).
A ``.gz`` transcript is read decompressed.  The report is ``{"scanned": N,
"hits": [<file name>, ...], "canaries": K}``.  Exit 0 when nothing matched, 1
when a transcript holds a canary, 2 when the canaries or a transcript cannot be
read -- the sequencer keeps every transcript on anything but 0.  Standard
library only: the host has no cflib.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import zlib
from pathlib import Path


def read_canaries(path: Path) -> list[str]:
    """The non-empty lines of the canaries file."""
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def holds(path: Path, canaries: list[str]) -> bool:
    """Does the transcript at ``path`` contain any canary?"""
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return any(c in text for c in canaries)


def scan(canaries: list[str], files: list[Path]) -> dict:
    hits = [p.name for p in files if holds(p, canaries)]
    return {"scanned": len(files), "hits": sorted(hits), "canaries": len(canaries)}


def write(out: Path, report: dict) -> None:
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="canary_scan.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--canaries", required=True, help="one canary string per line")
    ap.add_argument("--out", required=True, help="the report to write")
    ap.add_argument("files", nargs="*", help="the shard's transcripts")
    args = ap.parse_args(argv)
    try:
        canaries = read_canaries(Path(args.canaries))
        if not canaries:
            raise ValueError(f"{args.canaries} holds no canary")
        report = scan(canaries, [Path(f) for f in args.files])
    except (OSError, ValueError, EOFError, zlib.error) as exc:
        print(f"canary_scan: {exc}", file=sys.stderr)
        return 2
    write(Path(args.out), report)
    print(f"canary_scan: {report['scanned']} transcript(s) scanned, "
          f"{len(report['hits'])} holding a canary")
    return 1 if report["hits"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
