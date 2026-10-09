"""A small JSON Schema (draft 2020-12) evaluator for the keywords this package uses.

This package is standard library only, so the ``jsonschema`` package cannot be
assumed; this module evaluates the subset of draft 2020-12 that the shipped
schema is written in, and it REFUSES a schema that uses any other keyword, so
the schema cannot quietly rely on a rule nothing here enforces.
``tests/test_annotations_schema.py`` checks that the real ``jsonschema``
package, when it is installed, agrees with this evaluator on every shipped
example.

Supported: ``$ref`` (local ``#/$defs/...`` only), ``type``, ``enum``, ``const``,
``properties``, ``required``, ``additionalProperties``, ``patternProperties``,
``items``, ``minItems``, ``maxItems``, ``uniqueItems``, ``minLength``,
``maxLength``, ``pattern``, ``minimum``, ``maximum``, ``allOf``, ``anyOf``,
``oneOf``, ``not``, ``if``/``then``/``else``.  Annotation keywords
(``$schema``, ``$id``, ``$defs``, ``title``, ``description``) are accepted and
ignored.

Regular expressions.  JSON Schema patterns are ECMA-262 regular expressions,
and a Python checker evaluates them with :mod:`re`.  The two dialects differ
on ``\\d`` (Python matches every Unicode digit), ``\\s``/``\\S``/``\\w``/``\\b``
(different whitespace and word sets), ``.`` (which line terminators it skips)
and ``$`` (Python also matches before a final newline).  So this module
accepts only the dialect-neutral subset: explicit classes such as ``[0-9]``
and, for "no whitespace", :data:`_NO_WHITESPACE`; no ``\\d \\w \\s \\S \\b \\B
\\A \\Z``, no ``\\p{..}``, no ``.`` outside a class, no named groups or inline
flags, no empty class ``[]``.  A schema whose pattern uses one of them is
refused with :class:`SchemaError`, and every unescaped ``$`` outside a class
is evaluated as ECMA-262 does, as the end of the string.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

#: The stable public API of this module (README.md "From Python"; docs/annotations.md
#: section 11.6): exactly these names.  Anything else, underscored or not, is internal
#: and may change in any release.
__all__ = [
    "SchemaError", "Validator",
]

ANNOTATION_KEYWORDS = frozenset({"$schema", "$id", "$defs", "title", "description"})
ASSERTION_KEYWORDS = frozenset({
    "$ref", "type", "enum", "const", "properties", "required", "additionalProperties",
    "patternProperties", "items", "minItems", "maxItems", "uniqueItems", "minLength",
    "maxLength", "pattern", "minimum", "maximum", "allOf", "anyOf", "oneOf", "not",
    "if", "then", "else",
})
_SUBSCHEMA_MAPS = ("properties", "patternProperties", "$defs")
_SUBSCHEMA_LISTS = ("allOf", "anyOf", "oneOf")
_SUBSCHEMA_ONE = ("additionalProperties", "items", "not", "if", "then", "else")

#: The whitespace a "no whitespace" pattern excludes, as the inside of a
#: character class: every C0 control and DEL, NEL, and the code points ECMA-262
#: and Python agree or disagree to call whitespace (the Unicode White_Space
#: property plus the byte-order mark U+FEFF).  Spelled with ``\\uXXXX`` escapes,
#: which both dialects read.  ``[^...]`` of it (:data:`_NO_WHITESPACE`) is what
#: the schema writes instead of ``\\S``.
_WHITESPACE_RANGES = ((0x0000, 0x0020), (0x007F, 0x007F), (0x0085, 0x0085), (0x00A0, 0x00A0),
                      (0x1680, 0x1680), (0x2000, 0x200A), (0x2028, 0x2029), (0x202F, 0x202F),
                      (0x205F, 0x205F), (0x3000, 0x3000), (0xFEFF, 0xFEFF))
_WHITESPACE = "".join("\\u%04x" % a if a == b else "\\u%04x-\\u%04x" % (a, b) for a, b in _WHITESPACE_RANGES)
_NO_WHITESPACE = "[^" + _WHITESPACE + "]"
#: Escapes whose meaning differs between the dialects (or exist in one only).
_FORBIDDEN_ESCAPES = frozenset("dDwWsSbBAZpP")
_HEX = re.compile(r"[0-9A-Fa-f]+")
#: The message of an ``additionalProperties: false`` failure: an unknown key of a
#: closed object.  The checker turns exactly these into warnings in forward mode
#: (a document that declares a newer minor than it knows).
_UNKNOWN_KEY = "is not an allowed key here"


class SchemaError(ValueError):
    """The schema itself uses something this evaluator does not implement."""


def _translate(pattern: str, where: str) -> str:
    """``pattern`` for :mod:`re`, refused unless it means the same under ECMA-262.

    Rejects the dialect-dependent constructs (module docstring) and rewrites
    every unescaped ``$`` outside a character class to ``\\Z``, Python's
    end-of-string anchor, because ECMA-262's ``$`` never matches before a final
    newline.
    """
    out: list[str] = []
    i, n, in_class = 0, len(pattern), False
    while i < n:
        c = pattern[i]
        if c == "\\":
            if i + 1 >= n:
                raise SchemaError(f"{where}: pattern {pattern!r} ends in a backslash")
            e = pattern[i + 1]
            if e in _FORBIDDEN_ESCAPES:
                raise SchemaError(f"{where}: pattern {pattern!r} uses \\{e}, which ECMA-262 and Python "
                                  f"read differently; write an explicit class such as [0-9] or "
                                  f"{_NO_WHITESPACE}")
            if e in "ux":
                width = 4 if e == "u" else 2
                digits = pattern[i + 2:i + 2 + width]
                if len(digits) != width or not _HEX.fullmatch(digits):
                    raise SchemaError(f"{where}: pattern {pattern!r}: \\{e} takes exactly {width} hex digits")
                out.append(pattern[i:i + 2 + width])
                i += 2 + width
                continue
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
            out.append(c)
            i += 1
            continue
        if c == "[":
            j = i + 2 if pattern.startswith("[^", i) else i + 1
            if j < n and pattern[j] == "]":
                raise SchemaError(f"{where}: pattern {pattern!r} has an empty class, which ECMA-262 and "
                                  f"Python read differently")
            in_class = True
        elif c == "(" and pattern.startswith("(?", i):
            follow = pattern[i + 2:i + 4]
            if not (follow[:1] in (":", "=", "!") or follow in ("<=", "<!")):
                raise SchemaError(f"{where}: pattern {pattern!r} uses a named group, an inline flag or a "
                                  f"comment, which are not dialect-neutral")
        elif c == ".":
            raise SchemaError(f"{where}: pattern {pattern!r} uses '.', which skips different line terminators "
                              f"in ECMA-262 and Python; write an explicit class")
        elif c == "$":
            out.append(r"\Z")
            i += 1
            continue
        out.append(c)
        i += 1
    if in_class:
        raise SchemaError(f"{where}: pattern {pattern!r} has an unclosed class")
    return "".join(out)


def _type_ok(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if isinstance(value, bool):
        return False
    if name == "integer":
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if name == "number":
        return isinstance(value, (int, float))
    raise SchemaError(f"unknown type {name!r}")


def _json_equal(a: Any, b: Any) -> bool:
    """Equality in the JSON data model: 1 == 1.0, but True is not 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _hashable(value: Any) -> Any:
    """A hashable key that is equal for two values exactly when :func:`_json_equal` says so.

    Lets ``uniqueItems`` run in linear time (a set of keys) instead of comparing
    every pair.  Numbers keep Python's exact int/float equality (``1`` and
    ``1.0`` hash alike); booleans, strings, null, arrays and objects are tagged
    so a boolean never equals a number.
    """
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, (int, float)):
        return ("n", value)
    if isinstance(value, str):
        return ("s", value)
    if value is None:
        return ("z",)
    if isinstance(value, list):
        return ("a", tuple(_hashable(v) for v in value))
    if isinstance(value, dict):
        return ("o", tuple(sorted((str(k), _hashable(v)) for k, v in value.items())))
    return ("x", id(value))


def _child(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", str(key)):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=not str(key).isprintable())}]"


class Validator:
    """Evaluate one schema.  ``errors(instance)`` yields ``(path, message)``."""

    def __init__(self, schema: dict) -> None:
        self.root = schema
        self._patterns: dict[str, re.Pattern] = {}
        self._check_schema(schema, "#")

    # -- schema self-check -------------------------------------------------
    def _check_schema(self, node: Any, where: str) -> None:
        if isinstance(node, bool):
            return
        if not isinstance(node, dict):
            raise SchemaError(f"{where}: a schema must be an object or a boolean")
        for key, value in node.items():
            if key in ANNOTATION_KEYWORDS - {"$defs"}:
                continue
            if key not in ASSERTION_KEYWORDS and key not in _SUBSCHEMA_MAPS:
                raise SchemaError(f"{where}: keyword {key!r} is not implemented by schemacheck")
            if key in _SUBSCHEMA_MAPS:
                for name, sub in value.items():
                    if key == "patternProperties":
                        self._regex(name, f"{where}/{key}")
                    self._check_schema(sub, f"{where}/{key}/{name}")
            elif key in _SUBSCHEMA_LISTS:
                for i, sub in enumerate(value):
                    self._check_schema(sub, f"{where}/{key}/{i}")
            elif key in _SUBSCHEMA_ONE:
                self._check_schema(value, f"{where}/{key}")
            elif key == "$ref":
                self._resolve(value)
            elif key == "pattern":
                self._regex(value, f"{where}/pattern")

    def _regex(self, pattern: str, where: str = "#") -> re.Pattern:
        compiled = self._patterns.get(pattern)
        if compiled is None:
            if not isinstance(pattern, str):
                raise SchemaError(f"{where}: a pattern is a string")
            try:
                compiled = re.compile(_translate(pattern, where))
            except re.error as exc:
                raise SchemaError(f"{where}: pattern {pattern!r} does not compile: {exc}") from exc
            self._patterns[pattern] = compiled
        return compiled

    def _resolve(self, ref: str) -> Any:
        if not ref.startswith("#/"):
            raise SchemaError(f"only local $ref is supported, got {ref!r}")
        node: Any = self.root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                raise SchemaError(f"$ref {ref!r} resolves nowhere")
            node = node[part]
        return node

    # -- evaluation --------------------------------------------------------
    def is_valid(self, instance: Any, schema: Any = None) -> bool:
        return next(self._eval(instance, self.root if schema is None else schema, "$"), None) is None

    def errors(self, instance: Any) -> list[tuple[str, str]]:
        return list(self._eval(instance, self.root, "$"))

    def _eval(self, inst: Any, schema: Any, path: str) -> Iterator[tuple[str, str]]:
        if schema is True:
            return
        if schema is False:
            yield path, "no value is allowed here"
            return
        if "$ref" in schema:
            yield from self._eval(inst, self._resolve(schema["$ref"]), path)
        if "type" in schema:
            names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_type_ok(inst, n) for n in names):
                yield path, f"must be of type {' or '.join(names)}"
                return
        if "const" in schema and not _json_equal(inst, schema["const"]):
            yield path, f"must be {json.dumps(schema['const'], ensure_ascii=False)}"
        if "enum" in schema and not any(_json_equal(inst, e) for e in schema["enum"]):
            allowed = ", ".join(json.dumps(e, ensure_ascii=False) for e in schema["enum"])
            yield path, f"{json.dumps(inst, ensure_ascii=False)} is not one of {allowed}"
        if isinstance(inst, str):
            if "minLength" in schema and len(inst) < schema["minLength"]:
                yield path, f"must be at least {schema['minLength']} character(s) long"
            if "maxLength" in schema and len(inst) > schema["maxLength"]:
                yield path, f"must be at most {schema['maxLength']} characters long"
            if "pattern" in schema and not self._regex(schema["pattern"]).search(inst):
                yield path, f"{json.dumps(inst, ensure_ascii=False)} does not match {schema['pattern']}"
        if _type_ok(inst, "number") and not isinstance(inst, bool):
            if "minimum" in schema and inst < schema["minimum"]:
                yield path, f"must be >= {schema['minimum']}"
            if "maximum" in schema and inst > schema["maximum"]:
                yield path, f"must be <= {schema['maximum']}"
        if isinstance(inst, list):
            if "minItems" in schema and len(inst) < schema["minItems"]:
                yield path, f"must have at least {schema['minItems']} item(s)"
            if "maxItems" in schema and len(inst) > schema["maxItems"]:
                yield path, f"must have at most {schema['maxItems']} item(s)"
            if schema.get("uniqueItems"):
                seen: set = set()
                for i, value in enumerate(inst):
                    key = _hashable(value)
                    if key in seen:
                        yield _child(path, i), "duplicates an earlier entry"
                    seen.add(key)
            if "items" in schema:
                for i, value in enumerate(inst):
                    yield from self._eval(value, schema["items"], _child(path, i))
        if isinstance(inst, dict):
            for key in schema.get("required", ()):
                if key not in inst:
                    yield path, f"is missing required key {key!r}"
            props = schema.get("properties", {})
            pats = schema.get("patternProperties", {})
            for key, value in inst.items():
                matched = False
                if key in props:
                    matched = True
                    yield from self._eval(value, props[key], _child(path, key))
                for pat, sub in pats.items():
                    if self._regex(pat).search(key):
                        matched = True
                        yield from self._eval(value, sub, _child(path, key))
                if not matched and "additionalProperties" in schema:
                    extra = schema["additionalProperties"]
                    if extra is False:
                        yield _child(path, key), _UNKNOWN_KEY
                    else:
                        yield from self._eval(value, extra, _child(path, key))
        for sub in schema.get("allOf", ()):
            yield from self._eval(inst, sub, path)
        if "anyOf" in schema and not any(self.is_valid(inst, s) for s in schema["anyOf"]):
            yield path, "matches none of the allowed alternatives (anyOf)"
        if "oneOf" in schema:
            hits = sum(self.is_valid(inst, s) for s in schema["oneOf"])
            if hits != 1:
                yield path, f"must match exactly one alternative (oneOf), matched {hits}"
        if "not" in schema and self.is_valid(inst, schema["not"]):
            yield path, "matches a shape that is not allowed here (not)"
        if "if" in schema:
            if self.is_valid(inst, schema["if"]):
                if "then" in schema:
                    yield from self._eval(inst, schema["then"], path)
            elif "else" in schema:
                yield from self._eval(inst, schema["else"], path)
