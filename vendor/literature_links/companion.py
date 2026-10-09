"""Validate one annotation, and read and write strict JSON."""
from __future__ import annotations

import copy
import functools
import json
import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from .schemacheck import Validator

__all__ = ["SCHEMA_PATH", "LoadError", "Problem", "Report", "check", "dumps", "load_schema", "loads", "merge",
           "rebase", "validate"]

_HERE = Path(__file__).resolve().parent
_PACKAGE_SCHEMA = _HERE / "schema" / "companion.schema.json"
_SOURCE_CHECKOUT = _HERE.parent.name == "src" and (_HERE.parent.parent / "pyproject.toml").is_file()
SCHEMA_PATH = (_HERE.parent.parent / "schema" / "companion.schema.json"
               if not _PACKAGE_SCHEMA.is_file() and _SOURCE_CHECKOUT else _PACKAGE_SCHEMA)


class LoadError(ValueError):
    """The input is not an unambiguous, finite JSON value."""


@dataclass(frozen=True)
class Problem:
    """A stable diagnostic code, where it was found, and a readable explanation."""

    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class Report:
    """Validation result. Any problem makes the annotation invalid. ``warnings`` is always empty."""

    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)


def _child(path: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}[{json.dumps(key)}]"


def _json_problems(value: Any) -> list[Problem]:
    """Reject values JSON cannot represent, even inside opaque payloads."""
    problems: list[Problem] = []
    active: set[int] = set()

    def walk(node: Any, path: str) -> None:
        if isinstance(node, str):
            if any(0xD800 <= ord(char) <= 0xDFFF for char in node):
                problems.append(Problem("JSON", path, "strings must not contain unpaired Unicode surrogates"))
            return
        if node is None or isinstance(node, (bool, int)):
            return
        if isinstance(node, float):
            if not math.isfinite(node):
                problems.append(Problem("JSON", path, "numbers must be finite"))
            return
        if not isinstance(node, (dict, list)):
            problems.append(Problem("JSON", path, f"{type(node).__name__} is not a JSON value"))
            return
        identity = id(node)
        if identity in active:
            problems.append(Problem("JSON", path, "cyclic containers are not JSON"))
            return
        active.add(identity)
        if isinstance(node, dict):
            for key, item in node.items():
                if not isinstance(key, str):
                    problems.append(Problem("JSON", path, "object keys must be strings"))
                    continue
                walk(key, path + " (object key)")
                walk(item, _child(path, key))
        else:
            for index, item in enumerate(node):
                walk(item, _child(path, index))
        active.remove(identity)

    try:
        walk(value, "$")
    except RecursionError:
        problems.append(Problem("JSON", "$", "JSON nesting exceeds this implementation's recursion limit"))
    return problems


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LoadError(f"duplicate key {key!r} in one object")
        result[key] = value
    return result


def _constant(name: str) -> Any:
    raise LoadError(f"{name} is not JSON")


def _float(text: str) -> float:
    value = float(text)
    # float() turns a tiny nonzero number such as 1e-400 into 0.0. Reject it.
    # Check only the digits before the exponent, so a huge exponent is never parsed.
    if value == 0.0 and any(char in "123456789" for char in text.lower().partition("e")[0]):
        raise LoadError("nonzero JSON number is below this implementation's floating-point range")
    return value


def loads(text: str) -> Any:
    """Parse JSON. Reject NaN, Infinity, duplicate keys, unpaired surrogates, overflow and nonzero underflow.

    Integers stay exact and other numbers become floats. This does not validate;
    use :func:`check`.
    """
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, LoadError):
            raise
        raise LoadError(f"not JSON: {exc}") from exc
    problems = _json_problems(value)
    if problems:
        raise LoadError("; ".join(map(str, problems)))
    return value


def dumps(value: Any) -> str:
    """Write JSON, keeping key order, with two-space indentation and a final newline.

    Raise :class:`LoadError` for values JSON cannot hold.
    """
    problems = _json_problems(value)
    if problems:
        raise LoadError("; ".join(map(str, problems)))
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    except (ValueError, RecursionError) as exc:
        raise LoadError(f"cannot write JSON: {exc}") from exc


def load_schema() -> dict[str, Any]:
    """Read the bundled schema."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@functools.cache
def _validator(path: Path) -> Validator:
    # Keyed by path so a swapped SCHEMA_PATH is still read.
    return Validator(load_schema())


def check(document: Any) -> Report:
    """Check one annotation without changing it.

    Problem codes are ``JSON`` (not a JSON value), ``SCHEMA``, ``PDF_RECT`` and
    ``LATEX_LINES``.
    """
    report = Report(problems=_json_problems(document))
    if report.problems:
        return report
    validator = _validator(SCHEMA_PATH)
    try:
        report.problems.extend(Problem("SCHEMA", path, message)
                               for path, message in validator.errors(document))
    except (ValueError, RecursionError) as exc:
        report.problems.append(Problem("SCHEMA", "$", f"cannot validate JSON: {exc}"))
    if report.problems:
        return report

    mode = document["source"]["type"]
    location = document.get("location")
    if location is None or "profile" in location:
        return report
    if mode == "pdf" and "rect" in location:
        x, y, width, height = location["rect"]
        if width <= 0 or height <= 0:
            report.problems.append(Problem("PDF_RECT", "$.location.rect",
                                           "width and height must be positive"))
        # Exact decimal arithmetic avoids accepting .9 + .10000000000000002
        # because a binary floating-point sum happens to round to 1.
        outside = (Fraction(str(x)) + Fraction(str(width)) > 1
                   or Fraction(str(y)) + Fraction(str(height)) > 1)
        if outside:
            report.problems.append(Problem("PDF_RECT", "$.location.rect",
                                           "rectangle must stay within the page"))
    elif mode == "latex" and "lines" in location:
        start, end = location["lines"]
        if end < start:
            report.problems.append(Problem("LATEX_LINES", "$.location.lines",
                                           "end must be greater than or equal to start"))
    return report


def validate(document: Any) -> list[str]:
    """Return each problem as a ``path: message`` string. An empty list means valid."""
    return [str(problem) for problem in check(document).problems]


def merge(inherited: dict[str, Any], written: dict[str, Any]) -> dict[str, Any]:
    """Merge ``written`` over ``inherited`` (docs/companion.md, "Folder defaults").

    Objects merge member by member at any depth, and any other written value
    replaces the inherited one. A ``null`` removes the inherited member; where
    nothing is inherited it is kept. Neither argument is changed.
    """
    result = copy.deepcopy(inherited)
    _merge_into(result, written)
    return result


def _merge_into(result: dict[str, Any], written: dict[str, Any]) -> None:
    for key, value in written.items():
        if key in result and value is None:
            del result[key]
        elif key in result and isinstance(value, dict) and isinstance(result[key], dict):
            _merge_into(result[key], value)
        else:
            result[key] = copy.deepcopy(value)


_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")


def _rebased(reference: str, base: str) -> str:
    if _SCHEME.match(reference) or reference.startswith("/"):
        return reference
    if reference.startswith(("#", "?")):
        return base + reference
    return base[:base.rfind("/") + 1] + reference


def rebase(value: dict[str, Any], base: str) -> dict[str, Any]:
    """Copy ``value`` with its relative references resolved against ``base``.

    ``base`` is a relative reference to the file that holds ``value``, written
    from the file that will hold the result, such as ``"../_defaults.json"``.
    Only the references that docs/companion.md lists under "References" change.
    """
    result = copy.deepcopy(value)

    def fix(holder: Any, key: str) -> None:
        if isinstance(holder, dict) and isinstance(holder.get(key), str):
            holder[key] = _rebased(holder[key], base)

    fix(result, "$schema")
    fix(result, "target")
    fix(result.get("location"), "profile")
    for name in ("source", "target"):
        fix(result.get(name), "uri")
    for name in ("source", "target", "status"):
        holder = result.get(name)
        if isinstance(holder, dict):
            fix(holder.get("pin"), "url")
    return result
