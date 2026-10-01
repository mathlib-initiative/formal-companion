"""cf review-tags -- check a document's key links against the known defect shapes.

New code, implementing the tag-review half of docs/proposals/pdf-to-companion.md
section 5.4 (M3); the checks are ``annotations.review_tags``.  Read-only over an
annotation document (plain or gzip-compressed), no workspace, no companion
checkout: everything it needs is in the document.

    cf review-tags <document.json[.gz]>
    cf review-tags <document.json> --json
    cf review-tags <document.json> --hide-pattern '\\bIn-text proof' --keep-pattern '…'

Exit 0 whatever it finds: the findings are the report.  A document that does
not load, or that is not an annotation document, or a --keep-pattern or
--hide-pattern that is not a regex, exits 2.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from annotations import review_tags
from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import validate as V
from formal_companion_annotations import vocab
from annotations.export.common import read_bytes
from cflib import log
from cflib.errors import CfError


def register(subparsers) -> None:
    p = subparsers.add_parser("review-tags",
                              help="report the known attribution defects a document's key links carry")
    p.add_argument("document", type=Path, help="an annotation document (.json or .json.gz)")
    p.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    p.add_argument("--limit", type=int, default=10, help="examples printed per check (default 10)")
    p.add_argument("--keep-pattern", default=review_tags.KEEP_PATTERN,
                   help="regex of the book anchors the site's fold script keeps a declaration for")
    p.add_argument("--hide-pattern", default=review_tags.HIDE_PATTERN,
                   help="regex of the tag spelling the fold script hides as proof plumbing")


def load_document(path: Path) -> dict:
    """The document, strictly parsed; a CfError when it is not one."""
    if not path.is_file():
        raise CfError(f"{path} is not a file")
    try:
        doc = V.loads(read_bytes(path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, V.LoadError) as exc:
        raise CfError(f"{path}: cannot read: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != vocab.ANNOTATION_SCHEMA:
        raise CfError(f"{path} is not a formal-companion annotation document",
                      hint=f"the root carries schema: {vocab.ANNOTATION_SCHEMA}")
    return doc


def run(args) -> int:
    for flag, pattern in (("--keep-pattern", args.keep_pattern), ("--hide-pattern", args.hide_pattern)):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise CfError(f"{flag} {pattern!r} is not a regular expression: {exc}",
                          hint="pass a Python regex; quote it for the shell") from exc
    doc = load_document(args.document)
    findings = review_tags.review(doc, keep_pattern=args.keep_pattern, hide_pattern=args.hide_pattern)
    if args.as_json:
        print(json.dumps(findings, indent=2, ensure_ascii=False))
        return 0
    log.info(f"{args.document}: {doc['companion']['name']} at {doc['companion']['commit'][:12]}")
    for line in review_tags.summary_lines(findings, limit=max(0, args.limit)):
        log.info(line)
    return 0
