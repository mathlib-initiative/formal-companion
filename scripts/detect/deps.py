"""Index over a ``deps.json`` dump produced by ``DumpDeps.lean``.

Ported from /Users/jack/Desktop/tao-differential/adam-detector/analyze2.py:
``closure`` L132-147, ``direct_deps`` L127-131, ``path_to`` L148-151, the
statement closure L241-252 / L285-295 and the generated-name filter L213.

``deps.json`` shape (see detect/DumpDeps.lean.tmpl)::

    {"repo_constants": [{"name", "module", "kind", "instance", "structure",
                         "projection", "type_deps": [...], "value_deps": [...]}],
     "referenced":     [{"name", "module", "kind"}]}

The file is ~3.6 MB for measure_theory and is *never* committed: it is written
outside the companion tree and lives in the campaign's ``detect/<name>/``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from detect._compat import CfError, read_json

#: kinds that introduce data rather than assert something
DEF_KINDS = {"def", "inductive", "ctor", "rec", "opaque", "structure", "abbrev", "instance"}

#: Lean's generated auxiliary declarations (analyze2.py L213)
_GENERATED = re.compile(
    r"\._(proof|eq|sunfold|unfold|cstage|closed|spec|match|rarg|below|brecOn|binductionOn|"
    r"ibelow|casesOn|recOn|noConfusion|sizeOf|inj|mk\.inj|ctorIdx|ofNat|toCtorIdx)"
)

#: the constant Lean inserts for an unproved theorem
SORRY_AX = "sorryAx"


def is_generated(name: str) -> bool:
    """True for compiler-generated auxiliary constants (equations, matchers...)."""
    return bool(_GENERATED.search(name)) or ".proof_" in name or "match_" in name or "._" in name


class DepsIndex:
    """Constant-level dependency graph of one companion's own modules."""

    def __init__(self, data: dict, source: Path | None = None):
        self.source = source
        self.repo: dict[str, dict] = {c["name"]: c for c in data.get("repo_constants", [])}
        self._refmod: dict[str, str] = {c["name"]: c.get("module", "?") for c in data.get("referenced", [])}
        self._refkind: dict[str, str] = {c["name"]: c.get("kind", "?") for c in data.get("referenced", [])}

    @classmethod
    def load(cls, path: str | Path) -> "DepsIndex":
        """Load a dump; raises :class:`CfError` with the regeneration hint."""
        p = Path(path)
        data = read_json(p)
        if data is None:
            raise CfError(
                f"deps.json not found: {p}",
                hint="run scripts/detect/dump_deps.sh from the companion root (needs a built .lake)",
            )
        if "repo_constants" not in data:
            raise CfError(f"{p} is not a DumpDeps.lean dump (no 'repo_constants' key)")
        return cls(data, source=p)

    # -- lookups ----------------------------------------------------------
    def is_repo(self, name: str) -> bool:
        """Is this constant one of the companion's own?"""
        return name in self.repo

    def kind_of(self, name: str) -> str:
        """``def`` / ``theorem`` / ``inductive`` / ... for repo or referenced constants."""
        entry = self.repo.get(name)
        if entry is not None:
            return entry.get("kind", "?")
        return self._refkind.get(name, "?")

    def module_of(self, name: str) -> str:
        """Module a constant lives in (``"?"`` when the dump does not say)."""
        entry = self.repo.get(name)
        if entry is not None:
            return entry.get("module", "?")
        return self._refmod.get(name, "?")

    def is_definitional(self, name: str) -> bool:
        """True for defs/structures/inductives -- the things that *state* a concept."""
        return self.kind_of(name) in DEF_KINDS

    def uses_sorry(self, name: str) -> bool:
        """True when the proof term of ``name`` goes through ``sorryAx``."""
        entry = self.repo.get(name)
        if entry is None:
            return False
        return SORRY_AX in entry.get("value_deps", []) or SORRY_AX in entry.get("type_deps", [])

    def direct_deps(self, name: str, follow_value: bool) -> list[str]:
        """Direct dependencies (analyze2.direct_deps L127-131)."""
        entry = self.repo[name]
        out = list(entry.get("type_deps", []))
        if follow_value:
            out += list(entry.get("value_deps", []))
        return out

    def direct_set(self, name: str) -> set[str]:
        """Type + value dependencies of one constant, as a set."""
        entry = self.repo.get(name, {})
        return set(entry.get("type_deps", [])) | set(entry.get("value_deps", []))

    # -- closures ---------------------------------------------------------
    def closure(self, root: str, root_follow_value: bool = True) -> tuple[dict[str, str], set[str], dict[str, str]]:
        """Definitional closure of ``root`` (analyze2.closure L132-147, verbatim logic).

        Follows repo defs/ctors/inductives/recs through type+value, and repo
        theorems through type only. Returns ``(external -> first repo constant
        that reached it, repo constants seen, parent map)``.
        """
        if root not in self.repo:
            raise KeyError(root)
        seen = {root}
        ext: dict[str, str] = {}
        parent: dict[str, str] = {}
        stack: list[tuple[str, bool]] = [(root, root_follow_value)]
        while stack:
            name, follow_value = stack.pop()
            for dep in self.direct_deps(name, follow_value):
                if dep in seen:
                    continue
                if self.is_repo(dep):
                    seen.add(dep)
                    parent[dep] = name
                    stack.append((dep, self.repo[dep].get("kind") != "theorem"))
                else:
                    ext.setdefault(dep, name)
        return ext, seen, parent

    def statement_closure(self, root: str) -> tuple[set[str], set[str]]:
        """What a *statement* mentions (analyze2 L241-252 / stmt_closure L285-295).

        Type dependencies, unfolding the repo's own definitions through their
        values but never unfolding a repo theorem. Returns
        ``(external constants, repo constants)``.
        """
        if root not in self.repo:
            raise KeyError(root)
        seen = {root}
        ext: set[str] = set()
        repo_names: set[str] = set()
        stack = list(self.repo[root].get("type_deps", []))
        while stack:
            dep = stack.pop()
            if dep in seen:
                continue
            seen.add(dep)
            if self.is_repo(dep):
                repo_names.add(dep)
                stack += self.repo[dep].get("type_deps", [])
                if self.repo[dep].get("kind") != "theorem":
                    stack += self.repo[dep].get("value_deps", [])
            else:
                ext.add(dep)
        return ext, repo_names

    @staticmethod
    def path_to(name: str, parent: dict[str, str], root: str) -> list[str]:
        """Walk the parent map back to ``root`` (analyze2.path_to L148-151)."""
        path = [name]
        while path[-1] != root and path[-1] in parent:
            path.append(parent[path[-1]])
        return list(reversed(path))

    # -- bridges ----------------------------------------------------------
    def bridge_theorems(self, decl: str, foreign: Iterable[str], limit: int = 5) -> list[dict]:
        """Repo theorems whose *statement* mentions both ``decl`` and a foreign constant.

        This is the mechanical part of the ``bridge`` field: a proxy with a
        proved bridge lemma (``elementaryMeasure_eq_toNNReal_volume``) is a very
        different finding from one with none. Public theorems sort first, then
        the most focused statements (fewest type dependencies).
        """
        wanted = {f for f in foreign if f}
        found: list[dict] = []
        for name, entry in self.repo.items():
            if entry.get("kind") != "theorem" or is_generated(name):
                continue
            tdeps = entry.get("type_deps", [])
            if decl not in tdeps:
                continue
            hits = sorted(wanted.intersection(tdeps))
            if not hits:
                continue
            found.append(
                {
                    "decl": name,
                    "private": name.startswith("_private."),
                    "sorry": self.uses_sorry(name),
                    "relates": hits,
                    "_width": len(tdeps),
                }
            )
        found.sort(key=lambda b: (b["private"], b["sorry"], b["_width"], b["decl"]))
        for b in found:
            b.pop("_width", None)
        return found[:limit]

    @staticmethod
    def classify_bridge(bridges: list[dict]) -> str:
        """``proved`` | ``fiat`` | ``none`` from the bridge theorems found.

        ``fiat`` means the only bridges are asserted rather than proved (a
        ``sorry``/axiom), so the identification of the book's object with
        Mathlib's is unverified.
        """
        if not bridges:
            return "none"
        if any(not b["sorry"] for b in bridges):
            return "proved"
        return "fiat"
