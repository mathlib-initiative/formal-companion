"""Offline commands: validate files and scan files and folders."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

from . import __version__, companion, discovery
from .companion import Problem

__all__ = ["main"]

_PROG = "literature-links"

# Problems that are not about the data: they exit 2, the others 1.
_OPERATIONAL = {"IO", "JSON", "NO_ANNOTATIONS", "SYMLINK", "PATH"}

_EXIT_STATUS = ("exit status: 0 when every annotation is valid, 1 for an invalid annotation, file shape or "
                "defaults file, 2 for a read, write, JSON, path or symlink failure, no annotations, or a usage error")


_ROOT = "the companion's root folder: defaults files above it do not apply, and a file outside it is a problem"


def _write_json(text: str) -> None:
    """Write UTF-8 JSON with LF line ends, whatever the terminal's encoding."""
    if sys.stdout is None:
        return  # stdout was closed at launch: write nothing, as print() does
    binary = getattr(sys.stdout, "buffer", None)
    if binary is None:
        # Embedders and tests may supply a text-only stream such as StringIO.
        sys.stdout.write(text)
    else:
        binary.write(text.encode("utf-8"))


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _text(value: Any) -> str:
    """``str(value)`` with each unpaired surrogate, as in a file name that is not UTF-8, written as an escape."""
    return str(value).encode("utf-8", "backslashreplace").decode("utf-8")


def _report(problems: list[Problem]) -> list[dict[str, str]]:
    """Problems for a JSON report, which must hold no unpaired surrogate."""
    return [{key: _text(value) for key, value in asdict(problem).items()} for problem in problems]


def _status(problems: list[Problem]) -> int:
    return 2 if any(problem.code in _OPERATIONAL for problem in problems) else 1 if problems else 0


def _check_row(value: Any, line: int | None, path: Path, chain: list[tuple[Path, dict[str, Any]]] | None,
               reading: list[Problem]) -> tuple[dict[str, Any] | None, tuple[Path, ...], list[Problem]]:
    """The merged annotation or None, the defaults files applied, and the problems."""
    shape = discovery._shape(value, line)
    if shape:
        return None, (), [Problem("SHAPE", "$", shape)]
    merged, applied, failures = discovery._inherit(value, path, chain, reading)
    if merged is None:
        return None, (), failures
    return merged, applied, companion.check(merged).problems


def _check_file(path: Path, root: Path | None) -> tuple[list[Problem], tuple[Path, ...], list[list[str]]]:
    """Check each annotation in ``path``: the problems, the defaults files applied and each valid one's end types."""
    mode = path.lstat().st_mode  # checked before reading, as scan checks a file named explicitly
    if stat.S_ISLNK(mode):
        return [Problem("SYMLINK", "$", "explicit inputs must not be symlinks")], (), []
    if not stat.S_ISREG(mode):
        return [Problem("PATH", "$", "input must be a regular file")], (), []
    if path.name == discovery.DEFAULTS:
        return [Problem("DEFAULTS", "$", "a defaults file is not an annotation")], (), []
    resolved = path.resolve()
    reading: list[Problem] = []
    defaults = discovery._Defaults(reading, root)
    if defaults.folders(resolved) is None:
        return reading, (), []  # outside the root, before reading
    rows = discovery._rows(path, path.read_bytes().decode("utf-8"))
    if not rows:
        return [Problem("NO_ANNOTATIONS", "$", "the file holds no annotations")], (), []
    problems: list[Problem] = []
    reported: set[Problem] = set()
    applied: tuple[Path, ...] = ()
    types: list[list[str]] = []
    chain = defaults.chain(resolved)
    for line, row in rows:
        try:
            merged, files, found = _check_row(companion.loads(row), line, resolved, chain, reading)
        except companion.LoadError as exc:
            merged, files, found = None, (), [Problem("JSON", "$", str(exc))]
        applied = files or applied
        if merged is not None and not found:
            types.append([end["type"] for side in ("subject", "object") for end in merged[side]])
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
    root = None if args.root is None else args.root.resolve()
    for path in args.documents:
        applied: tuple[Path, ...] = ()
        types: list[list[str]] = []
        try:
            found, applied, types = _check_file(path, root)
        except (OSError, ValueError, RecursionError) as exc:
            code = "IO" if isinstance(exc, (OSError, UnicodeError)) else "JSON"
            found = [Problem(code, "$", str(exc))]
        item_status = _status(found)
        status = max(status, item_status)
        problems = [asdict(problem) for problem in found]
        if args.format == "json":
            _write_json(json.dumps({"document": _text(path), "valid": not problems, "status": item_status,
                                   "defaults": [_text(file) for file in applied],
                                   "problems": _report(found), "warnings": []},
                                  ensure_ascii=True, allow_nan=False) + "\n")
        else:
            note = f" (with defaults from {', '.join(map(str, applied))})" if applied else ""
            for problem in problems:
                print(f"{path}: {problem['path']}: {problem['message']}{note} [{problem['code']}]",
                      file=sys.stderr if item_status == 2 else sys.stdout)
            if not problems:
                names = dict.fromkeys(name for ends in types for name in ends)
                print(f"{path}: ok ({_plural(len(types), 'annotation')}, {', '.join(names)})")
    return status


def _scan(args: argparse.Namespace) -> int:
    try:
        collection = discovery.collect(args.paths, include=args.include, exclude=args.exclude, root=args.root)
    except (OSError, ValueError, RecursionError) as exc:
        # A failure that is not about one input, such as an unreadable schema.
        if args.format == "json":
            _write_json(companion.dumps({"valid": False, "status": 2, "files": [], "records": [],
                                        "problems": [{"code": "IO", "path": "$", "message": str(exc)}]}))
        else:
            print(f"{_PROG}: {exc}", file=sys.stderr)
        return 2
    status = _status(collection.problems)
    if args.format == "json":
        result = {"valid": not collection.problems, "status": status,
                  "files": [_text(path) for path in collection.files],
                  "records": [{"annotation": record.annotation,
                               "origin": {"path": _text(record.path), "line": record.line, "base_uri": record.base_uri,
                                          "defaults": [_text(file) for file in record.defaults]}}
                              for record in collection.records],
                  "problems": _report(collection.problems)}
        _write_json(companion.dumps(result))
    else:
        for problem in collection.problems:
            # A problem with the whole run, such as finding no annotations, has the path "$".
            where = "" if problem.path == "$" else f"{problem.path}: "
            print(f"{where}{problem.message} [{problem.code}]")
        print(f"{_plural(len(collection.records), 'annotation')} from {_plural(len(collection.files), 'file')}; "
              f"{_plural(len(collection.problems), 'problem')}")
    return status


def main(argv: list[str] | None = None) -> int:
    """Run an offline command; return 0 on success, 1 on invalid data, 2 on error."""
    # No abbreviations: a prefix that works today would break when an option is added.
    parser = argparse.ArgumentParser(prog=_PROG, allow_abbrev=False, epilog=_EXIT_STATUS,
                                     description="Validate and scan Literature Links annotations.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validation = subparsers.add_parser("validate", allow_abbrev=False, epilog=_EXIT_STATUS,
                                       help="check JSON files, one annotation each, and JSONL files, one per line")
    validation.add_argument("documents", nargs="+", type=Path, metavar="FILE",
                            help="a .json file holds one annotation, a .jsonl or .ndjson file one per line")
    validation.add_argument("--root", type=Path, metavar="DIR", help=_ROOT)
    validation.add_argument("--format", choices=("text", "json"), default="text",
                            help="json writes a report on one line for each file")
    scanning = subparsers.add_parser("scan", allow_abbrev=False, epilog=_EXIT_STATUS,
                                     help="read annotations from JSON and JSONL files and folders")
    scanning.add_argument("paths", nargs="+", type=Path, metavar="PATH",
                          help="a file, or a folder to walk for .json, .jsonl and .ndjson files")
    scanning.add_argument("--include", action="append", metavar="PATTERN",
                          help="select files by pattern on the path inside the walked folder instead of suffix "
                               "(repeatable)")
    scanning.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                          help="leave out files by pattern on the path inside the walked folder; wins over "
                               "--include (repeatable)")
    scanning.add_argument("--root", type=Path, metavar="DIR", help=_ROOT)
    scanning.add_argument("--format", choices=("text", "json"), default="text",
                          help="json writes one report with the files, records and problems")
    args = parser.parse_args(argv)
    if args.root is not None and not args.root.is_dir():
        (validation if args.command == "validate" else scanning).error(f"--root {args.root} is not a folder")
    if args.format == "text" and getattr(sys.stdout, "errors", None) == "strict" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    try:
        status = _validate(args) if args.command == "validate" else _scan(args)
        if sys.stdout is not None:
            sys.stdout.flush()
        return status
    except (OSError, ValueError) as exc:
        # The commands report read failures themselves, so this is a failure to write, such as a full disk.
        # A reader that stops early, as `| head` does, needs no message.
        if not isinstance(exc, BrokenPipeError):
            print(f"{_PROG}: {exc}", file=sys.stderr)
        # Python flushes stdout at exit: send what is left to the null device, so that this cannot fail again.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass  # a stream without a file descriptor, such as StringIO
        return 2


if __name__ == "__main__":
    sys.exit(main())
