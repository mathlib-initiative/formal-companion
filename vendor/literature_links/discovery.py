"""Read annotations from local JSON and JSONL files (docs/tools.md, "Discovery")."""
from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import companion
from .companion import Problem

__all__ = ["DEFAULTS", "Collection", "Problem", "Record", "collect", "inherit"]

# The file name of folder defaults (docs/companion.md, "Folder defaults").
DEFAULTS = "_defaults.json"

# Files read one annotation per line; other files hold one JSON value.
_LINES = (".jsonl", ".ndjson")

# The SHAPE message for a JSON file that holds an array.
_ONE_PER_FILE = "a JSON file holds one annotation, an object, not an array"


@dataclass
class Record:
    """An annotation and its origin.

    ``line`` is the one-based physical line of a JSONL record, and None in a
    JSON file, which holds one annotation. ``annotation`` has its folder
    defaults applied; ``defaults`` lists their files, outermost first.
    """

    annotation: dict[str, Any]
    path: Path
    _: KW_ONLY
    line: int | None = None
    defaults: tuple[Path, ...] = ()

    @property
    def base_uri(self) -> str:
        """The file URI that relative references resolve against."""
        # ``path`` is already resolved. Resolving again could follow a symlink made since.
        return self.path.absolute().as_uri()


@dataclass
class Collection:
    """Records and files in sorted path order, plus all problems.

    Valid records are kept when other files fail, so check ``problems`` first.
    """

    records: list[Record] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)


def _matches(relative: str, patterns: tuple[str, ...]) -> bool:
    # Wildcards match the complete root-relative POSIX path, including '/'.
    # A leading '**/' also matches zero directories, including root files.
    for pattern in patterns:
        while True:
            if fnmatch.fnmatchcase(relative, pattern):
                return True
            if not pattern.startswith("**/"):
                break
            pattern = pattern[3:]
    return False


_BROKEN = object()


class _Defaults:
    """Folder defaults files, each read once; problems go to ``problems``."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        self.files: dict[Path, Any] = {}

    def read(self, path: Path) -> Any:
        """Return the object in ``path``, None when there is no file, or _BROKEN."""
        if path in self.files:
            return self.files[path]
        value: Any = _BROKEN
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            value = None
        except OSError as exc:
            self.problems.append(Problem("PATH", str(path), str(exc)))
        else:
            if not self._spelled(path):
                value = None
            elif stat.S_ISLNK(mode):
                self.problems.append(Problem("SYMLINK", str(path), "defaults files must not be symlinks"))
            elif not stat.S_ISREG(mode):
                self.problems.append(Problem("PATH", str(path), "a defaults file must be a regular file"))
            else:
                try:
                    value = companion.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError) as exc:
                    self.problems.append(Problem("IO", str(path), str(exc)))
                    value = _BROKEN
                except companion.LoadError as exc:
                    self.problems.append(Problem("JSON", str(path), str(exc)))
                    value = _BROKEN
                else:
                    if not isinstance(value, dict):
                        self.problems.append(Problem("DEFAULTS", str(path),
                                                     "a defaults file must hold one JSON object"))
                        value = _BROKEN
        self.files[path] = value
        return value

    @staticmethod
    def _spelled(path: Path) -> bool:
        """Whether the folder's entry is spelled exactly; lstat ignores case on some file systems."""
        try:
            return path.name in os.listdir(path.parent)
        except OSError:
            return True  # a folder that can be searched but not listed

    def chain(self, path: Path) -> list[tuple[Path, dict[str, Any]]] | None:
        """The defaults files covering the file ``path``, outermost first; None if one is broken."""
        found = []
        for folder in path.parents:
            value = self.read(folder / DEFAULTS)
            if value is _BROKEN:
                return None
            if value is not None:
                found.append((folder / DEFAULTS, value))
        return found[::-1]


def _apply(annotation: dict[str, Any], path: Path,
           chain: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    if not chain:
        return annotation
    merged: dict[str, Any] = {}
    for file, value in chain:
        depth = len(path.parent.relative_to(file.parent).parts)
        merged = companion.merge(merged, companion.rebase(value, "../" * depth + DEFAULTS), defaults=True)
    return companion.merge(merged, annotation)


def inherit(annotation: dict[str, Any], path: Path | str) -> tuple[dict[str, Any] | None, tuple[Path, ...],
                                                                     list[Problem]]:
    """Apply the folder defaults that cover the file ``path`` to ``annotation``.

    Return the merged annotation, the defaults files applied (outermost first)
    and the problems reading them. The annotation is None when a defaults file
    cannot be used or the record is too deep to merge. Relative references
    keep resolving against ``path``.
    """
    problems: list[Problem] = []
    chain = _Defaults(problems).chain(Path(path).resolve())
    if chain is None:
        return None, (), problems
    try:
        merged = _apply(annotation, Path(path).resolve(), chain)
    except RecursionError:
        return None, (), [Problem("JSON", "$", _TOO_DEEP)]
    return merged, tuple(file for file, _ in chain), problems


_TOO_DEEP = "JSON nesting exceeds this implementation's recursion limit"


def _rows(path: Path, text: str) -> list[tuple[int | None, str]]:
    """The JSON texts in a file: each nonblank JSONL line with its number, or the whole file."""
    if path.suffix.lower() not in _LINES:
        return [(None, text)]
    # CRLF, CR and LF end lines. splitlines() would also split strings at U+2028.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [(line, row) for line, row in enumerate(lines, 1) if row.strip(" \t")]


def _shape(value: Any, line: int | None) -> str | None:
    """Why ``value``, a JSON file or the JSONL row at ``line``, is not an annotation; None if it may be."""
    if isinstance(value, dict):
        return None
    return _ONE_PER_FILE if line is None and isinstance(value, list) else "each annotation must be a JSON object"


def _read(path: Path, result: Collection, defaults: _Defaults) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        result.problems.append(Problem("IO", str(path), str(exc)))
        return
    for line, row in _rows(path, text):
        origin = str(path) if line is None else f"{path} (line {line})"
        try:
            value = companion.loads(row)
        except companion.LoadError as exc:
            result.problems.append(Problem("JSON", origin, str(exc)))
            continue
        shape = _shape(value, line)
        if shape:
            result.problems.append(Problem("SHAPE", origin, shape))
            continue
        chain = defaults.chain(path)
        if chain is None:
            continue
        try:
            value = _apply(value, path, chain)
        except RecursionError:
            result.problems.append(Problem("JSON", origin, _TOO_DEEP))
            continue
        applied = tuple(file for file, _ in chain)
        findings = companion.check(value).problems
        if findings:
            note = f" (with defaults from {', '.join(map(str, applied))})" if applied else ""
            for finding in findings:
                result.problems.append(Problem(finding.code, f"{origin} {finding.path}", finding.message + note))
            continue
        result.records.append(Record(value, path, line=line, defaults=applied))


def collect(paths: Iterable[Path | str], *, include: Iterable[str] | None = None,
            exclude: Iterable[str] = ()) -> Collection:
    """Read annotations from files and directories.

    Directories are walked for ``.json``, ``.jsonl`` and ``.ndjson`` files,
    ignoring case. A file named explicitly may have any suffix. ``include``
    replaces the suffix filter and ``exclude`` always wins. Patterns are
    case-sensitive shell wildcards on the root-relative path, or on the file
    name for a file named explicitly. ``*`` also matches ``/``, and a leading
    ``**/`` also matches root files. Symlinks are skipped while walking and
    reported when named explicitly. Each resolved path is read once, in sorted
    path order. Files named ``_defaults.json`` are folder defaults: they are
    checked and applied, never read as annotations.
    """
    if any(isinstance(value, str) for value in (paths, include, exclude)):
        raise TypeError("paths, include and exclude take lists, not one string")
    result = Collection()
    includes = None if include is None else tuple(include)
    excludes = tuple(exclude)
    selected: set[Path] = set()
    defaults = _Defaults(result.problems)

    def select(path: Path, relative: str, *, explicit: bool = False) -> None:
        if _matches(relative, excludes):
            return
        if path.name == DEFAULTS:
            defaults.read(path.parent.resolve() / DEFAULTS)
            return
        if includes is not None:
            if not _matches(relative, includes):
                return
        elif not explicit and path.suffix.lower() not in (".json", *_LINES):
            return
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            result.problems.append(Problem("PATH", str(path), str(exc)))
            return
        selected.add(resolved)

    def walk(directory: Path, root: Path) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            result.problems.append(Problem("IO", str(directory), str(exc)))
            return
        for child in children:
            relative = child.relative_to(root).as_posix()
            if _matches(relative, excludes):
                continue
            try:
                mode = child.lstat().st_mode
            except OSError as exc:
                result.problems.append(Problem("PATH", str(child), str(exc)))
                continue
            if stat.S_ISLNK(mode):
                continue
            if stat.S_ISDIR(mode):
                walk(child, root)
            elif stat.S_ISREG(mode):
                select(child, relative)
            elif ((includes is None and child.suffix.lower() in (".json", *_LINES))
                  or (includes is not None and _matches(relative, includes))):
                result.problems.append(Problem("PATH", str(child),
                                               "selected input is not a regular file"))

    roots = sorted({Path(path).absolute() for path in paths}, key=Path.as_posix)
    for path in roots:
        try:
            if path.is_symlink():
                result.problems.append(Problem("SYMLINK", str(path),
                                               "explicit inputs must not be symlinks"))
                continue
            mode = path.lstat().st_mode
        except (OSError, ValueError) as exc:
            result.problems.append(Problem("PATH", str(path), str(exc)))
            continue
        if stat.S_ISDIR(mode):
            walk(path, path)
        elif stat.S_ISREG(mode):
            select(path, path.name, explicit=True)
        else:
            result.problems.append(Problem("PATH", str(path),
                                           "input must be a regular file or directory"))

    # as_posix, so that Windows reads files in the same order as everyone else.
    result.files = sorted(selected, key=Path.as_posix)
    for path in result.files:
        _read(path, result, defaults)
    if not result.records and not result.problems:
        result.problems.append(Problem("NO_ANNOTATIONS", "$", "no annotations were found in the selected inputs"))
    return result
