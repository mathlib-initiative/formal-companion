"""Offline commands: validate files and scan files and folders."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any

from . import __version__, companion, discovery
from .companion import Problem

__all__ = ["main"]

# Problems that are not about the data: they exit 2, the others 1.
_OPERATIONAL = {"IO", "JSON", "NO_ANNOTATIONS", "SYMLINK", "PATH"}


def _write_json(text: str) -> None:
    """Write UTF-8 JSON with LF line ends, whatever the terminal's encoding."""
    binary = getattr(sys.stdout, "buffer", None)
    if binary is None:
        # Embedders and tests may supply a text-only stream such as StringIO.
        sys.stdout.write(text)
    else:
        binary.write(text.encode("utf-8"))


def _status(problems: list[Problem]) -> int:
    return 2 if any(problem.code in _OPERATIONAL for problem in problems) else 1 if problems else 0


def _check_row(value: Any, line: int | None, path: Path) -> tuple[dict[str, Any] | None, tuple[Path, ...],
                                                                  list[Problem]]:
    """The merged annotation or None, the defaults files applied, and the problems."""
    shape = discovery._shape(value, line)
    if shape:
        return None, (), [Problem("SHAPE", "$", shape)]
    merged, applied, failures = discovery.inherit(value, path)
    if merged is None:
        return None, (), failures
    return merged, applied, companion.check(merged).problems


def _check_file(path: Path) -> tuple[list[Problem], tuple[Path, ...], list[str]]:
    """Check each annotation in ``path``: the problems, the defaults files applied and the source types."""
    if path.name == discovery.DEFAULTS:
        return [Problem("DEFAULTS", "$", "a defaults file is not an annotation")], (), []
    rows = discovery._rows(path, path.read_bytes().decode("utf-8"))
    if not rows:
        return [Problem("NO_ANNOTATIONS", "$", "the file holds no annotations")], (), []
    problems: list[Problem] = []
    reported: set[Problem] = set()
    applied: tuple[Path, ...] = ()
    types: list[str] = []
    for line, row in rows:
        try:
            merged, files, found = _check_row(companion.loads(row), line, path)
        except companion.LoadError as exc:
            merged, files, found = None, (), [Problem("JSON", "$", str(exc))]
        applied = files or applied
        if merged is not None and not found:
            types.append(merged["source"]["type"])
        for problem in found:
            if not problem.path.startswith("$"):
                # A defaults file's problem names that file: report it once, not for every row.
                if problem in reported:
                    continue
                reported.add(problem)
            elif line is not None:
                problem = Problem(problem.code, f"line {line} {problem.path}", problem.message)
            problems.append(problem)
    return problems, applied, types


def _validate(args: argparse.Namespace) -> int:
    """Report every input, including after unreadable files; status 2 wins."""
    status = 0
    for path in args.documents:
        applied: tuple[Path, ...] = ()
        types: list[str] = []
        try:
            found, applied, types = _check_file(path)
        except (OSError, ValueError, RecursionError) as exc:
            code = "IO" if isinstance(exc, (OSError, UnicodeError)) else "JSON"
            found = [Problem(code, "$", str(exc))]
        item_status = _status(found)
        status = max(status, item_status)
        problems = [asdict(problem) for problem in found]
        if args.format == "json":
            _write_json(json.dumps({"document": str(path), "valid": not problems, "status": item_status,
                                   "defaults": [str(file) for file in applied],
                                   "problems": problems, "warnings": []}, ensure_ascii=True, allow_nan=False) + "\n")
        else:
            note = f" (with defaults from {', '.join(map(str, applied))})" if applied else ""
            for problem in problems:
                print(f"{path}: {problem['path']}: {problem['message']}{note} [{problem['code']}]",
                      file=sys.stderr if item_status == 2 else sys.stdout)
            if not problems:
                count = f"{len(types)} annotation{'s' if len(types) != 1 else ''}"
                print(f"{path}: ok ({count}, {', '.join(dict.fromkeys(types))})")
    return status


def _scan(args: argparse.Namespace) -> int:
    collection = discovery.collect(args.paths, include=args.include, exclude=args.exclude)
    status = _status(collection.problems)
    if args.format == "json":
        result = {"valid": not collection.problems, "status": status,
                  "files": [str(path) for path in collection.files],
                  "records": [{"annotation": record.annotation,
                               "origin": {"path": str(record.path), "line": record.line, "base_uri": record.base_uri,
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
    # No abbreviations: a prefix that works today would break when an option is added.
    parser = argparse.ArgumentParser(prog="literature-links", allow_abbrev=False,
                                     description="Validate and scan Literature Links annotations.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validation = subparsers.add_parser("validate", allow_abbrev=False,
                                       help="check JSON files, one annotation each, and JSONL files, one per line")
    validation.add_argument("documents", nargs="+", type=Path)
    validation.add_argument("--format", choices=("text", "json"), default="text", help="JSON emits one report per input")
    scanning = subparsers.add_parser("scan", allow_abbrev=False, help="read annotations from JSON and JSONL files")
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
