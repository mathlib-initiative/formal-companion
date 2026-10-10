"""Offline commands for single annotations: validate and scan."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from . import __version__, companion, discovery

__all__ = ["main"]


def _write_json(text: str) -> None:
    """Write UTF-8 JSON with LF line ends, whatever the terminal's encoding."""
    binary = getattr(sys.stdout, "buffer", None)
    if binary is None:
        # Embedders and tests may supply a text-only stream such as StringIO.
        sys.stdout.write(text)
    else:
        binary.write(text.encode("utf-8"))


def _validate(args: argparse.Namespace) -> int:
    """Report every input, including after unreadable files; status 2 wins."""
    status = 0
    for path in args.documents:
        item_status = 0
        warnings: list[dict[str, str]] = []
        problems: list[dict[str, str]] = []
        applied: tuple[Path, ...] = ()
        try:
            text = path.read_bytes().decode("utf-8")
            document = companion.loads(text)
            failures: list[companion.Problem] = []
            if path.name == discovery.DEFAULTS:
                failures = [companion.Problem("DEFAULTS", "$", "a defaults file is not an annotation")]
            elif isinstance(document, dict):
                document, applied, failures = discovery.inherit(document, path)
            if failures:
                problems = [asdict(problem) for problem in failures]
                item_status = 1 if all(problem.code == "DEFAULTS" for problem in failures) else 2
            else:
                report = companion.check(document)
                problems = [asdict(problem) for problem in report.problems]
                warnings = [asdict(warning) for warning in report.warnings]
                item_status = 1 if problems else 0
        except (OSError, ValueError, RecursionError) as exc:
            item_status = 2
            code = "IO" if isinstance(exc, (OSError, UnicodeError)) else "JSON"
            problems = [{"code": code, "path": "$", "message": str(exc)}]
        status = max(status, item_status)
        if args.format == "json":
            _write_json(json.dumps({"document": str(path), "valid": not problems, "status": item_status,
                                   "defaults": [str(file) for file in applied],
                                   "problems": problems, "warnings": warnings}, ensure_ascii=True, allow_nan=False) + "\n")
        else:
            for problem in problems:
                print(f"{path}: {problem['path']}: {problem['message']} [{problem['code']}]",
                      file=sys.stderr if item_status == 2 else sys.stdout)
            if not problems:
                print(f"{path}: ok (1 annotation, {document['source']['type']})")
    return status


def _scan(args: argparse.Namespace) -> int:
    collection = discovery.collect(args.paths, include=args.include, exclude=args.exclude)
    operational = {"IO", "JSON", "NO_ANNOTATIONS", "SYMLINK", "PATH"}
    status = (2 if any(problem.code in operational for problem in collection.problems)
              else 1 if collection.problems else 0)
    if args.format == "json":
        result = {"valid": not collection.problems, "status": status,
                  "files": [str(path) for path in collection.files],
                  "records": [{"annotation": record.annotation,
                               "origin": {"path": str(record.path), "pointer": record.pointer,
                                          "line": record.line, "base_uri": record.base_uri,
                                          "defaults": [str(file) for file in record.defaults]}}
                              for record in collection.records],
                  "problems": [asdict(problem) for problem in collection.problems]}
        _write_json(companion.dumps(result))
    else:
        for problem in collection.problems:
            print(f"{problem.path}: {problem.message} [{problem.code}]")
        print(f"{len(collection.records)} annotations from {len(collection.files)} files; "
              f"{len(collection.problems)} problems")
    return status


def main(argv: list[str] | None = None) -> int:
    """Run an offline command; return 0 on success, 1 on invalid data, 2 on error."""
    parser = argparse.ArgumentParser(prog="literature-links",
                                     description="Validate and scan Literature Links annotations.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validation = subparsers.add_parser("validate", help="validate single annotations")
    validation.add_argument("documents", nargs="+", type=Path)
    validation.add_argument("--format", choices=("text", "json"), default="text", help="JSON emits one report per input")
    scanning = subparsers.add_parser("scan", help="read annotations from JSON and JSONL files")
    scanning.add_argument("paths", nargs="+", type=Path)
    scanning.add_argument("--include", action="append", help="select root-relative file patterns (repeatable)")
    scanning.add_argument("--exclude", action="append", default=[], help="exclude root-relative file patterns (repeatable)")
    scanning.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    if args.format == "text" and getattr(sys.stdout, "errors", None) == "strict" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    try:
        if args.command == "validate":
            return _validate(args)
        return _scan(args)
    except (OSError, ValueError, RecursionError) as exc:
        if args.command == "scan" and args.format == "json":
            _write_json(companion.dumps({"valid": False, "status": 2, "files": [], "records": [],
                                        "problems": [{"code": "IO", "path": "$", "message": str(exc)}]}))
            return 2
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
