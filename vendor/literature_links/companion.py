"""Validate one annotation, and read and write strict JSON."""
from __future__ import annotations

import copy
import functools
import json
import math
import re
from dataclasses import dataclass, field
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

# The two sides of an annotation, each a list of ends.
_SIDES = ("subject", "object")


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
    if isinstance(text, str) and text.startswith("\ufeff"):
        # Clearer than the json module's advice to decode as utf-8-sig.
        raise LoadError("not JSON: the text starts with a byte order mark")
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
    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema


# Where the schema keeps a pattern for a rule of the specification, and the rule in words.
_PATTERN_RULES = (
    (("$defs", "reference", "pattern"),
     "is not a reference: percent-encode White_Space, controls, invisible characters and any bare %"),
    (("$defs", "sha256", "pattern"), "is not 64 lowercase hex digits"),
    (("$defs", "html_location", "properties", "fragment", "pattern"),
     "is not a fragment: it is empty, starts with # or has White_Space or a control character; use css for such an ID"),
)


@functools.cache
def _validator(path: Path) -> tuple[Validator, dict[str, str]]:
    """A validator for the schema at ``path``, and its patterns' rules in words."""
    # Keyed by path, so a swapped SCHEMA_PATH is read.
    schema = json.loads(path.read_text(encoding="utf-8"))
    rules = {}
    for keys, words in _PATTERN_RULES:
        node = schema
        for key in keys:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, str):
            rules[node] = words
    return Validator(schema), rules


def check(document: Any) -> Report:
    """Check one annotation without changing it.

    Problem codes are ``JSON`` (not a JSON value), ``SCHEMA``, ``PDF_RECT`` and
    ``LINES``. The last two are checked in each end, subject first, whose
    location has no ``profile``, and only when the schema finds nothing.
    """
    report = Report(problems=_json_problems(document))
    if report.problems:
        return report
    validator, rules = _validator(SCHEMA_PATH)
    try:
        for path, message in validator.errors(document):
            # Say which rule a value breaks, not which regular expression it fails.
            value, _, pattern = message.rpartition(" does not match ")
            if value and pattern in rules:
                if value == '""' and report.problems and report.problems[-1].path == path:
                    continue  # minLength has already said that the value is empty
                message = f"{value} {rules[pattern]}"
            report.problems.append(Problem("SCHEMA", path, message))
    except (ValueError, RecursionError) as exc:
        report.problems.append(Problem("SCHEMA", "$", f"cannot validate JSON: {exc}"))
    if report.problems:
        return report

    for side in _SIDES:
        for index, end in enumerate(document[side]):
            location = end.get("location")
            if location is None or "profile" in location:
                continue
            path = f"$.{side}[{index}].location"
            if end["type"] == "pdf" and "rect" in location:
                x, y, width, height = location["rect"]
                if width <= 0 or height <= 0:
                    report.problems.append(Problem("PDF_RECT", path + ".rect",
                                                   "width and height must be positive"))
                # Double sums, as the specification says: every language computes the same answer.
                if float(x) + float(width) > 1 or float(y) + float(height) > 1:
                    report.problems.append(Problem("PDF_RECT", path + ".rect",
                                                   "rectangle must stay within the page"))
            elif end["type"] in ("latex", "lean") and len(location.get("lines", ())) == 2:
                start, last = location["lines"]
                if last < start:
                    report.problems.append(Problem("LINES", path + ".lines",
                                                   "end must be greater than or equal to start"))
    return report


def validate(document: Any) -> list[str]:
    """Return each problem as a ``path: message`` string. An empty list means valid."""
    return [str(problem) for problem in check(document).problems]


def merge(inherited: dict[str, Any], written: dict[str, Any], *, defaults: bool = False) -> dict[str, Any]:
    """Merge ``written`` over ``inherited`` (docs/companion.md, "Folder defaults").

    Objects merge member by member at any depth, and any other written value
    replaces the inherited one. A ``null`` removes the inherited member; where
    nothing is inherited it is kept.

    One rule covers ends. Where ``inherited`` holds an end template, an object,
    as its ``subject`` or ``object``, an object that ``written`` holds there
    merges over it. If ``written`` holds a list there, each object in the list
    without a ``uri`` member merges over the template, and other items are kept
    as written. A ``location`` is never merged: one written over the
    template's replaces it whole. ``written`` is an annotation, so a side it
    leaves out that is still a template becomes a list holding the template
    alone. With ``defaults=True``, ``written`` is a defaults file instead, and
    templates stay objects. Neither argument is changed.
    """
    result = copy.deepcopy(inherited)
    for key, value in written.items():
        template = result.get(key) if key in _SIDES else None
        if isinstance(template, dict) and isinstance(value, list):
            result[key] = [_merged_end(template, item) if isinstance(item, dict) and "uri" not in item
                           else copy.deepcopy(item) for item in value]
        elif isinstance(template, dict) and isinstance(value, dict):
            result[key] = _merged_end(template, value)
        else:
            _merge_member(result, key, value)
    if not defaults:
        for key in _SIDES:
            if key not in written and isinstance(result.get(key), dict):
                result[key] = [result[key]]
    return result


def _merged_end(template: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(template)
    for key, value in end.items():
        if key == "location" and value is not None:
            result[key] = copy.deepcopy(value)
        else:
            _merge_member(result, key, value)
    return result


def _merge_member(result: dict[str, Any], key: str, value: Any) -> None:
    if key in result and value is None:
        del result[key]
    elif key in result and isinstance(value, dict) and isinstance(result[key], dict):
        for inner, item in value.items():
            _merge_member(result[key], inner, item)
    else:
        result[key] = copy.deepcopy(value)


_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")


def _rebased(reference: str, base: str) -> str:
    # An empty reference is invalid and must stay so.
    if not reference or _SCHEME.match(reference) or reference.startswith("/"):
        return reference
    base = base.partition("#")[0]
    if reference.startswith("#"):
        return base + reference
    base = base.partition("?")[0]
    if reference.startswith("?"):
        return base + reference
    return base[:base.rfind("/") + 1] + reference


def rebase(value: dict[str, Any], base: str) -> dict[str, Any]:
    """Copy ``value`` with its relative references resolved against ``base``.

    ``base`` is a relative reference to the file that holds ``value``, written
    from the file that will hold the result, such as ``"../_defaults.json"``.
    Only the references that docs/companion.md lists under "References" change:
    ``$schema``, and each end's ``uri`` and ``location.profile``, where a side
    is an end template or a list of ends. ``citation`` and ``extensions`` are
    copied unchanged.
    """
    result = copy.deepcopy(value)

    def fix(holder: Any, key: str) -> None:
        if isinstance(holder, dict) and isinstance(holder.get(key), str):
            holder[key] = _rebased(holder[key], base)

    fix(result, "$schema")
    for side in _SIDES:
        ends = result.get(side)
        for end in ends if isinstance(ends, list) else [ends]:
            if isinstance(end, dict):
                fix(end, "uri")
                fix(end.get("location"), "profile")
    return result
