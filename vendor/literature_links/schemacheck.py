"""A small JSON Schema (draft 2020-12) evaluator.

It keeps the package free of dependencies. It supports ``ASSERTION_KEYWORDS``
(``$ref`` only as a local ``#/...`` pointer), ignores ``ANNOTATION_KEYWORDS``
and refuses any other keyword. When ``jsonschema`` is installed, the tests
check that the two agree.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

# Only these names are public.
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


class SchemaError(ValueError):
    """The schema itself uses something this evaluator does not implement."""


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


def _child(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", str(key)):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=False)}]"


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
                        self._regex(name)
                    self._check_schema(sub, f"{where}/{key}/{name}")
            elif key in _SUBSCHEMA_LISTS:
                for i, sub in enumerate(value):
                    self._check_schema(sub, f"{where}/{key}/{i}")
            elif key in _SUBSCHEMA_ONE:
                self._check_schema(value, f"{where}/{key}")
            elif key == "$ref":
                self._resolve(value)
            elif key == "pattern":
                self._regex(value)

    def _regex(self, pattern: str) -> re.Pattern:
        compiled = self._patterns.get(pattern)
        if compiled is None:
            compiled = self._patterns[pattern] = re.compile(pattern)
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
        if _type_ok(inst, "number"):
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
                for i in range(len(inst)):
                    if any(_json_equal(inst[i], inst[j]) for j in range(i)):
                        yield _child(path, i), "duplicates an earlier entry"
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
                        yield _child(path, key), "is not an allowed key here"
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
