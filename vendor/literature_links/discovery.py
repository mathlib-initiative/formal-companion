"""Read annotations from local JSON and JSONL files (docs/tools.md, "Discovery")."""
from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

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

    ``line`` is the one-based physical line in a JSONL file, and None in a
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
    # Wildcards match the complete POSIX path inside the walked folder, including '/'.
    # A leading '**/' also matches zero directories, so files directly in it.
    for pattern in patterns:
        while True:
            if fnmatch.fnmatchcase(relative, pattern):
                return True
            if not pattern.startswith("**/"):
                break
            pattern = pattern[3:]
    return False


_BROKEN = object()


def _same(folder: Path, root: Path) -> bool:
    try:
        return os.path.samefile(folder, root)
    except OSError:
        return False


class _Defaults:
    """Folder defaults files, each read once; problems go to ``problems``.

    With a resolved ``root``, no defaults file above it applies.
    """

    def __init__(self, problems: list[Problem], root: Path | None = None) -> None:
        self.problems = problems
        self.root = root
        self.files: dict[Path, Any] = {}
        self.walks: dict[Path, Sequence[Path] | None] = {}

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

    def folders(self, path: Path) -> Sequence[Path] | None:
        """The folders above the file ``path``, nearest first, up to the root; None if ``path`` is outside it."""
        if self.root is None:
            return path.parents
        if path not in self.walks:
            self.walks[path] = None
            for depth, folder in enumerate(path.parents):
                # By identity too: on a case-insensitive file system, Companion and companion are one folder.
                if folder == self.root or _same(folder, self.root):
                    self.walks[path] = path.parents[:depth + 1]
                    break
            else:
                self.problems.append(Problem("PATH", str(path), f"the file is outside the root {self.root}"))
        return self.walks[path]

    def chain(self, path: Path) -> list[tuple[Path, dict[str, Any]]] | None:
        """The defaults files covering the file ``path``, outermost first; None if one is broken or
        ``path`` is outside the root."""
        folders = self.folders(path)
        if folders is None:
            return None
        found = []
        for folder in folders:
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


_TOO_DEEP = "JSON nesting exceeds this implementation's recursion limit"


def _root(root: Path | str | None) -> Path | None:
    return None if root is None else Path(root).resolve()


def inherit(annotation: dict[str, Any], path: Path | str, *, root: Path | str | None = None
            ) -> tuple[dict[str, Any] | None, tuple[Path, ...], list[Problem]]:
    """Apply the folder defaults that cover the file ``path``, up to the folder ``root``, to ``annotation``.

    Return the merged annotation, the defaults files applied (outermost first)
    and the problems reading them. The annotation is None when ``path`` is
    outside ``root``, a defaults file cannot be used, or the annotation is too
    deep to merge. Relative references
    keep resolving against ``path``.
    """
    problems: list[Problem] = []
    path = Path(path).resolve()
    return _inherit(annotation, path, _Defaults(problems, _root(root)).chain(path), problems)


def _inherit(annotation: dict[str, Any], path: Path, chain: list[tuple[Path, dict[str, Any]]] | None,
             problems: list[Problem]) -> tuple[dict[str, Any] | None, tuple[Path, ...], list[Problem]]:
    """inherit() for a resolved ``path`` whose ``chain`` is already read, into ``problems``, so a file's rows read it
    once."""
    if chain is None:
        return None, (), list(problems)
    try:
        merged = _apply(annotation, path, chain)
    except RecursionError:
        return None, (), [Problem("JSON", "$", _TOO_DEEP)]
    return merged, tuple(file for file, _ in chain), list(problems)


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
            exclude: Iterable[str] = (), root: Path | str | None = None) -> Collection:
    """Read annotations from files and directories.

    Directories are walked for ``.json``, ``.jsonl`` and ``.ndjson`` files,
    ignoring case. A file named explicitly may have any suffix. ``include``
    replaces the suffix filter and ``exclude`` always wins. Patterns are
    case-sensitive shell wildcards on the path inside the walked folder, or on the file
    name for a file named explicitly. ``*`` also matches ``/``, and a leading
    ``**/`` also matches files directly in that folder. Symlinks are skipped while walking and
    reported when named explicitly. Each resolved path is read once, in sorted
    path order. Files named ``_defaults.json`` are folder defaults: they are
    checked and applied, never read as annotations. With ``root``, the
    companion's root folder, no defaults file above it applies, and a file
    outside it is a ``PATH`` problem.
    """
    if any(isinstance(value, str) for value in (paths, include, exclude)):
        raise TypeError("paths, include and exclude take lists, not one string")
    result = Collection()
    includes = None if include is None else tuple(include)
    excludes = tuple(exclude)
    selected: set[Path] = set()
    defaults = _Defaults(result.problems, _root(root))

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
        if defaults.folders(resolved) is None:
            return  # outside the root, a PATH problem
        selected.add(resolved)

    def walk(directory: Path, top: Path) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            result.problems.append(Problem("IO", str(directory), str(exc)))
            return
        for child in children:
            relative = child.relative_to(top).as_posix()
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
                walk(child, top)
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
