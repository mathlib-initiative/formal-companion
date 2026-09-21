"""Build the FULL declaration index the schema gate checks `repo:decl` against.

New code. The design put "``repo:decl`` ids against ... the declaration set
(new: reject phantom names, which produced 5 of 6 'unsure' ledger rows)" into the
schema gate, and the design critique re-scoped it to a *full* declaration index.
``scripts/gate/verify_findings.py`` implements the check and ``plan/presets.py``
exposes ``decl_index`` -- but ``cf plan-run`` never passed one, so every
generated ``gate.json`` carried ``"decl_index": null`` and the check was dead.

It MUST be the full declaration set, not ``statements.jsonl``: a real finding
often names a private helper or an untagged lemma, and an index built from the
tagged rows alone would reject it. So the index is, in order of preference:

1. ``detect/<name>/deps.json`` -- every constant the built environment knows
   (the DumpDeps dump), which is the closest thing to ground truth;
2. every ``.lean`` file under the companion repo, parsed with
   ``adapters/leanparse.py`` (no build needed, ``.lake`` excluded);
``companions/<name>/statements.jsonl`` is deliberately NOT a source: it holds the
TAGGED rows only, so an index built from it would reject every finding that names
an untagged lemma or a private helper -- the opposite of the defect this check
exists for.  When neither source is available the index is left empty for that
companion and the gate's phantom-name check stays OFF, with a warning, rather
than being switched on against a set that is known to be incomplete.

The index is written as ``{repo: [decl, ...]}``, one of the three shapes
``verify_findings._load_decl_index`` accepts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from cflib import log
from cflib.errors import CfError

#: directories never walked when parsing a companion for declarations
SKIP_DIRS = {".lake", ".git", "build", "lake-packages", ".elan", "__pycache__"}
#: refuse an index this small: it means the walk found nothing real
MIN_DECLS = 1


def _from_deps(path: Path) -> list[str]:
    """Declaration names from a DumpDeps dump (the built environment)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return sorted({str(c["name"]) for c in data.get("repo_constants", []) if c.get("name")})


def _from_lean(repo: Path, globs: Sequence[str] = ("**/*.lean",)) -> list[str]:
    """Every declaration name in every .lean file under *repo* (no build)."""
    from adapters.leanparse import parse_file

    names: set[str] = set()
    for pattern in globs:
        for path in sorted(repo.glob(pattern)):
            if not path.is_file() or set(path.parts) & SKIP_DIRS:
                continue
            try:
                decls = parse_file(path, rel=str(path.relative_to(repo)))
            except Exception:  # noqa: BLE001 - one unparseable file must not kill the index
                continue
            for decl in decls:
                if decl.full_name and not decl.anonymous:
                    names.add(decl.full_name)
                if decl.name:
                    names.add(decl.name)
    return sorted(names)


def _from_statements(path: Path, source_id: str | None = None) -> list[str]:
    """Declaration names from statements.jsonl -- TAGGED ROWS ONLY.

    Never used to build a gate index (see the module docstring): it is here for
    callers that want to know which declarations carry a source tag.  With
    ``source_id`` only the rows tagged against THAT source are counted: a
    companion audited against two sources has two disjoint tagged sets, and
    merging them would answer "does this declaration carry a tag" for the wrong
    source.
    """
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not row.get("decl"):
            continue
        if source_id and str(row.get("source_id") or "") != source_id:
            continue
        names.add(str(row["decl"]))
    return sorted(names)


def for_companion(paths, name: str, repo: Path | None) -> tuple[list[str], str]:
    """``(declaration names, which ORIGIN produced them)`` for one companion.

    "Origin" here is deps.json / lean-source / none -- it is not a
    ``source_id``.  The declaration set is a fact about the Lean, not about the
    book, so it is the same whichever source a run audits.
    """
    deps = Path(paths.detect_companion_dir(name)) / "deps.json"
    if deps.exists():
        return _from_deps(deps), "deps.json"
    if repo is not None and Path(repo).is_dir():
        names = _from_lean(Path(repo))
        if names:
            return names, "lean-source"
    return [], "none (no deps.json and no readable Lean source)"


def build(workspace, repos: Iterable[str]) -> dict:
    """``{repo: [decl, ...]}`` plus a ``_meta`` block naming each origin."""
    paths = workspace.paths
    index: dict[str, list[str]] = {}
    origins: dict[str, str] = {}
    for name in repos:
        try:
            repo = workspace.companion(name).repo
        except CfError:
            repo = None
        names, origin = for_companion(paths, name, Path(repo).expanduser() if repo else None)
        index[name] = names
        origins[name] = origin
        if len(names) < MIN_DECLS:
            log.warn(f"decl-index: no declarations found for {name} ({origin}); "
                     "the gate's phantom-name check stays OFF rather than running against "
                     "an incomplete set. Run `cf detect-proxies --lean` (deps.json) or point "
                     "workspace.yaml at a readable repo.")
    index["_meta"] = {  # type: ignore[assignment]
        "origins": origins,
        "counts": {k: len(v) for k, v in index.items() if k != "_meta"},
        "note": ("the FULL declaration set, never statements.jsonl: findings name private "
                 "helpers and untagged lemmas, and an index of tagged rows alone would "
                 "reject real findings"),
    }
    return index


def usable_repos(index: dict) -> list[str]:
    """Repos this index can actually vouch for."""
    return sorted(k for k, v in index.items() if k != "_meta" and v)


def write(workspace, run_id: str, repos: Iterable[str]) -> tuple[Path, dict]:
    """Write ``runs/<run-id>/decl-index.json``; returns (path, index)."""
    index = build(workspace, repos)
    path = Path(workspace.paths.run_dir(run_id)) / "decl-index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path, index


def usable(index: dict, repos: Sequence[str] | None = None) -> bool:
    """True only when EVERY repo in the run contributed declarations.

    A partial index is worse than none: the gate would reject every finding that
    names a declaration of the repo it could not read.
    """
    names = list(repos) if repos is not None else [k for k in index if k != "_meta"]
    return bool(names) and all(index.get(n) for n in names)
