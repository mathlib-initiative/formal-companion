"""Stage I2: `cf informalize scope`, the theorem's dependency closure inside its library.

New code, for docs/proposals/informalize.md sections 5.3 and 6.2.  The pilot took its
chain from an import walk narrowed by name heuristics, which it called "a heuristic, not
compiler-checked"; this is the exact closure over DumpDeps' constants
(`scripts/detect/DumpDeps.lean.tmpl`): from the theorem, every `type_deps` and
`value_deps` edge between constants of the library's own modules (`repo_constants`).

Two files describe one constant differently.  DumpDeps lists every constant, generated
ones too (constructors, recursors, projections, matchers, auxiliary lemmas, private
names in their mangled form); the probe's index (`templates/corpus/probe.lean.tmpl`)
records the declarations a reader writes, by their user-facing names.  A constant the
index does not record, or records only as private, is walked through but not listed, and
costs nothing in a member's `depth`.  Every listed member's proof status is re-decided
from the probe's own fields by `corpus.ingest.decide` (`cflib.vocab.decide_proof_status`),
never copied, and two public records of one name are refused, as `cf corpus ingest` does.
A name DumpDeps lists twice, a constant two modules realized alike, is one constant
(:func:`constants`), and the receipt counts the repeated rows.

What it refuses: an I1 that is missing or whose files moved since; a dump of another
module or prefix; a theorem missing from either file, in another module, or resting on a
non-standard axiom (`sorryAx` included); empty `value_deps` for the theorem, or for every
theorem of the library's modules -- the proofs' bodies were not loaded (a module-system
library the dump could not read), and the scope would silently shrink to the closure
through types; and a member whose source file or lines `library/src/` does not hold,
since the drafters and the cards read the statement there.

It writes `scope.json` (workspace) and `informal/declarations.json` (output repository,
committed), both deterministic, and the I2 receipt.

`declarations.json` also carries where a reader sees each definition: a member's own
`source_url`, its line in the library at the pinned commit, and `references`, the constants
from outside the library that the members' types name, each with the module DumpDeps found
it in and, for a library the Lean doc site holds, its documentation URL.  The conventions
task links from `00-conventions.tex` with these (`STYLE.md`, "The conventions section"):
the probe indexes the library's own modules alone, so the dump's `referenced` table is the
only place the module of `IsCompact` or `PseudoMetricSpace` is recorded.
"""
from __future__ import annotations

import json
import re
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from annotations.export.common import repo_id_from_url
from cflib import log
from cflib.errors import CfError
from cflib.hashing import sha256_file
from cflib.jsonio import write_json
from corpus import ingest

from informalize import config, itemtable, library, stages
from informalize.approve import DECLARATIONS
from informalize.outrepo import OutputRepo

SCOPE_SCHEMA = "formal-companion/informalize-scope"
DECLARATIONS_SCHEMA = "formal-companion/informalize-declarations"
SCHEMA_VERSION = 1
#: a member's fields in scope.json, and an entry's in declarations.json (section 5.3)
MEMBER_KEYS = ("name", "module", "path", "line", "kind", "depth", "reach", "proof_status")
ENTRY_KEYS = ("name", "module", "path", "line", "start_line", "end_line", "kind", "signature",
              "source_url")
#: a row of declarations.json's `references`: a constant from outside the library that a
#: member's type names, and where its definition is documented
DOC_REFERENCE_KEYS = ("name", "module", "kind", "doc_url")
#: where the libraries the Lean toolchain and Mathlib ship are documented: a module's page is
#: the module path with `/` for `.`, and a declaration's anchor its full name
DOC_SITE = "https://leanprover-community.github.io/mathlib4_docs/"
#: the roots that site documents; a constant from any other library gets its module and no URL
DOC_SITE_ROOTS = ("Mathlib", "Init", "Std", "Lean", "Batteries", "Aesop", "Qq", "Plausible",
                  "ImportGraph", "LeanSearchClient", "Cli")
#: how many names a refusal lists
_SHOWN = 5


@dataclass
class Closure:
    """The walk from the theorem: each walked constant's depth, and which were reached
    through types alone."""

    depth: dict[str, int]
    type_only: set[str]


def constants(deps: dict) -> dict[str, dict]:
    """DumpDeps' `repo_constants` by name, each with its two dependency lists.

    DumpDeps writes a row for every module that holds a constant, and a constant Lean
    realizes on demand (`congr_simp`, `eq_1`, `eq_def`) can be held by two modules that both
    realized it; Lean keeps one copy at import (the probe indexes it where Lean kept it).
    Such rows are one constant: one row is kept, with the union of their `value_deps`.  Rows
    of one name whose `type_deps` differ are refused: no environment holds two such
    constants."""
    out: dict[str, dict] = {}
    clash: list[str] = []
    for row in deps["repo_constants"]:
        name = row.get("name") if isinstance(row, dict) else None
        lists = [row.get(k) for k in ("type_deps", "value_deps")] if isinstance(row, dict) else []
        if not isinstance(name, str) or not all(
                isinstance(v, list) and all(isinstance(d, str) for d in v) for v in lists):
            raise CfError(f"deps.json: a constant that is not a name with two dependency lists: "
                          f"{str(row)[:120]}", hint="fetch the library again (stage I1)")
        seen = out.get(name)
        if seen is None:
            out[name] = row
        elif set(seen["type_deps"]) != set(row["type_deps"]):
            clash.append(name)
        else:
            out[name] = dict(seen, value_deps=list(dict.fromkeys([*seen["value_deps"],
                                                                   *row["value_deps"]])))
    if clash:
        raise CfError(f"deps.json lists {len(set(clash))} constant(s) twice with different "
                      "types: " + ", ".join(sorted(set(clash))[:_SHOWN]),
                      hint="two modules cannot both hold a constant of one name and two types; "
                           "fetch the library again (stage I1)")
    return out


def walk(graph: dict[str, dict], start: str, listed: set[str]) -> Closure:
    """Every constant of `graph` the start reaches through `type_deps` and `value_deps`.

    `depth` is the fewest listed constants on a path from the start (the start is 0): a
    step into a constant that is walked through but not listed costs nothing, so an
    auxiliary lemma between a theorem and what it uses puts no extra level between them.
    `type_only` is what the start reaches through `type_deps` alone."""
    depth = {start: 0}
    queue: deque[str] = deque([start])
    while queue:
        name = queue.popleft()
        row = graph[name]
        for dep in (*row["type_deps"], *row["value_deps"]):
            if dep not in graph:
                continue
            step = 1 if dep in listed else 0
            if dep not in depth or depth[name] + step < depth[dep]:
                depth[dep] = depth[name] + step
                if step:
                    queue.append(dep)
                else:
                    queue.appendleft(dep)
    type_only = {start}
    stack = [start]
    while stack:
        for dep in graph[stack.pop()]["type_deps"]:
            if dep in graph and dep not in type_only:
                type_only.add(dep)
                stack.append(dep)
    return Closure(depth=depth, type_only=type_only)


def public_records(index: Path) -> dict[str, dict]:
    """The index's public declarations by name; two of one name are refused."""
    out: dict[str, dict] = {}
    twice: list[str] = []
    for record in library.index_records(index, "declaration"):
        name = record.get("name")
        if record.get("private") or not isinstance(name, str):
            continue
        if name in out:
            twice.append(name)
        out[name] = record
    if twice:
        raise CfError(f"the index holds two public records of {len(set(twice))} name(s): "
                      + ", ".join(sorted(set(twice))[:_SHOWN]),
                      hint="the exact closure keys on the name; re-probe the library (I1)")
    return out


def doc_url(module: Any, name: str) -> str:
    """Where a constant of another library is documented, or `""` for a library the doc site
    does not hold.  `Mathlib.Topology.Basic` and `IsCompact` ->
    `.../Mathlib/Topology/Basic.html#IsCompact`."""
    if not isinstance(module, str) or not module:
        return ""
    if module.split(".")[0] not in DOC_SITE_ROOTS:
        return ""
    return f"{DOC_SITE}{module.replace('.', '/')}.html#{name}"


def source_url(url: Any, commit: str, path: Any, line: Any) -> str:
    """A declaration's line in the library's own source at the pinned commit, as a web URL.

    The destination is the annotation cards' (`annotations.export.informal_markers`), built
    from `informalize.yaml`'s `library.url` through the one reader of a remote URL this repo
    has, `annotations.export.common.repo_id_from_url` (`docs/exporters.md`): a URL it refuses
    -- one with no `<org>/<name>` to it -- gets no link rather than a link to nowhere."""
    if not isinstance(url, str) or not re.match(config.URL_PATTERN, url):
        return ""
    if not isinstance(path, str) or not isinstance(line, int) or isinstance(line, bool):
        return ""
    repo_id = repo_id_from_url(url)
    if not repo_id:
        return ""
    return f"https://{repo_id}/blob/{commit}/{path}#L{line}"


def reference_table(graph: dict[str, dict], members: list[dict], referenced: Any) -> list[dict]:
    """The constants from outside the library that the members' *types* name, each with the
    module the dump found it in and its documentation URL, sorted by name.

    DumpDeps' `referenced` table (`scripts/detect/DumpDeps.lean.tmpl`) is the only place a
    module is recorded for a constant the probe does not index: the probe indexes the
    library's own modules alone.  Types, not values: what a statement can mention is what the
    conventions have to explain (`00-conventions.tex`), and a proof term names hundreds of
    constants no reader of the text meets.  A generated or private name (a component starting
    with `_`) is left out, and so is a constant no module holds."""
    modules: dict[str, dict] = {}
    for row in referenced if isinstance(referenced, list) else []:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            modules[row["name"]] = row
    out: list[dict] = []
    for name in sorted({dep for member in members for dep in graph[member["name"]]["type_deps"]
                        if dep not in graph}):
        row = modules.get(name)
        module = row.get("module") if isinstance(row, dict) else None
        if not isinstance(module, str) or module == "<none>" \
                or any(part.startswith("_") for part in name.split(".")):
            continue
        kind = row.get("kind") if isinstance(row.get("kind"), str) else ""
        out.append({"name": name, "module": module, "kind": kind,
                    "doc_url": doc_url(module, name)})
    return out


def compute(cfg: dict, deps: Any, records: dict[str, dict], source: itemtable.SourceTree
            ) -> tuple[list[dict], list[dict], list[dict], dict]:
    """``(members, declarations, references, counts)``: section 6.2 on a dump, an index and a
    source tree."""
    found = library.deps_problems(deps, cfg)
    if found:
        raise CfError(found[0], hint="fetch the library again, or correct informalize.yaml "
                                     "and run stage I1 again")
    theorem, module = cfg["library"]["theorem"], cfg["library"]["module"]
    graph = constants(deps)
    if theorem not in graph:
        raise CfError(f"{theorem} is not among the constants deps.json dumped",
                      hint=f"check library.theorem and library.module ({module}) in "
                           "informalize.yaml")
    record = records.get(theorem)
    if record is None:
        raise CfError(f"{theorem} is not a public declaration of the index",
                      hint="the probe records the declarations of the modules under the prefix")
    if record.get("module") != module:
        raise CfError(f"{theorem} is declared in {record.get('module')}, not in "
                      f"{module}, which informalize.yaml names",
                      hint="correct library.module and run stage I1 again")
    axioms = sorted(record.get("nonstandard_axioms") or [])
    if axioms:
        raise CfError(f"{theorem} rests on {', '.join(axioms)}: an unproved chain is not "
                      "informalized", hint="pick a commit at which the theorem is proved")
    unloaded = library.bodies_problem({"repo_constants": list(graph.values())}, theorem)
    if unloaded:
        raise CfError(unloaded, hint="the dump could not read the modules' bodies (a "
                                     "module-system library?); the scope would shrink to the "
                                     "closure through types")
    closure = walk(graph, theorem, set(records))
    names = sorted(n for n in closure.depth if n in records)
    members, entries, problems, unsigned = [], [], [], []
    for name in names:
        rec = records[name]
        try:
            ingest.check_consistent(rec)
            status, _statement, _flags = ingest.decide(rec)
        except CfError as exc:
            raise CfError(f"the index's record of {name}: {exc.message}", hint=exc.hint) from None
        lines = source.lines(rec.get("path")) if isinstance(rec.get("path"), str) else None
        end = rec.get("end_line")
        if lines is None:
            problems.append(f"{name}: its file {rec.get('path')!r} is not in library/src/")
        elif not isinstance(end, int) or end > len(lines):
            problems.append(f"{name}: {rec.get('path')} has {len(lines)} lines, the index "
                            f"puts it at {rec.get('start_line')}..{end}")
        signature = rec.get("signature")
        if not isinstance(signature, str):
            unsigned.append(name)
        members.append({"name": name, "module": rec.get("module"), "path": rec.get("path"),
                        "line": rec.get("line"), "kind": rec.get("kind"),
                        "depth": closure.depth[name],
                        "reach": "type" if name in closure.type_only else "value",
                        "proof_status": status})
        entries.append({"name": name, "module": rec.get("module"), "path": rec.get("path"),
                        "line": rec.get("line"), "start_line": rec.get("start_line"),
                        "end_line": rec.get("end_line"), "kind": rec.get("kind"),
                        "signature": signature if isinstance(signature, str) else "",
                        "source_url": source_url(cfg["library"].get("url"),
                                                 cfg["library"]["commit"], rec.get("path"),
                                                 rec.get("line"))})
    if problems:
        raise CfError(f"{len(problems)} member(s) of the scope are not where library/src/ says: "
                      + problems[0], hint="; ".join(problems[1:_SHOWN]) or
                      "the index and the source archive are not one tree; fetch the library "
                      "again (stage I1)")
    counts = {
        "members": len(members),
        "walked_through": len(closure.depth) - len(members),
        "repeated_rows": len(deps["repo_constants"]) - len(graph),
        "type_only": sum(1 for m in members if m["reach"] == "type"),
        "max_depth": max(m["depth"] for m in members),
        "by_kind": dict(sorted(Counter(m["kind"] for m in members).items())),
        "by_proof_status": dict(sorted(Counter(m["proof_status"] for m in members).items())),
        "unsigned": unsigned,
    }
    references = reference_table(graph, members, deps.get("referenced"))
    counts["references"] = len(references)
    return members, entries, references, counts


@dataclass
class ScopeResult:
    members: int
    counts: dict
    commit: str | None
    scope_path: Path
    declarations_sha256: str


def scope(workspace, informalize_id: str) -> ScopeResult:
    """`cf informalize scope`: read I1's files, write scope.json, declarations.json and the
    I2 receipt."""
    paths = workspace.paths
    cfg = config.load(paths, informalize_id, sessions=config.session_limit(workspace))
    receipt = library.current_receipt(workspace, informalize_id)
    repo = OutputRepo(paths, informalize_id)
    repo.require_stamp()
    home = paths.informalize_dir(informalize_id)
    lib = paths.informalize_library(informalize_id)
    if receipt["checks"].get("commit") != cfg["library"]["commit"]:
        raise CfError(f"stage I1 fetched commit {str(receipt['checks'].get('commit'))[:12]}, "
                      f"informalize.yaml names {cfg['library']['commit'][:12]}",
                      hint="run stage I1 again for the commit informalize.yaml names")
    index, deps_path = lib / "index" / "index.jsonl", lib / "deps.json"
    try:
        deps = json.loads(deps_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CfError(f"{deps_path} is not readable JSON ({exc})",
                      hint="fetch the library again (stage I1)") from None
    members, entries, references, counts = compute(
        cfg, deps, public_records(index),
        itemtable.SourceTree(paths.informalize_library_src(informalize_id)))
    library_block = {"commit": cfg["library"]["commit"], "module": cfg["library"]["module"],
                     "prefix": cfg["library"]["prefix"]}
    scope_doc = {"schema": SCOPE_SCHEMA, "schema_version": SCHEMA_VERSION, "id": informalize_id,
                 "theorem": cfg["library"]["theorem"], "library": library_block,
                 "index_sha256": sha256_file(index), "deps_sha256": sha256_file(deps_path),
                 "counts": counts, "members": members}
    declarations = {"schema": DECLARATIONS_SCHEMA, "schema_version": SCHEMA_VERSION,
                    "library": {"commit": cfg["library"]["commit"],
                                "theorem": cfg["library"]["theorem"],
                                "url": cfg["library"].get("url") or ""},
                    "declarations": entries, "references": references}
    found = itemtable.declarations_problems(declarations)
    if found:
        raise CfError(f"the scope would write a declarations.json the item table refuses: "
                      f"{found[0]}", hint="; ".join(found[1:4]) or None)
    scope_path = paths.informalize_scope(informalize_id)
    write_json(scope_path, scope_doc)
    raw = (json.dumps(declarations, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    repo.write(DECLARATIONS, raw)
    commit = repo.commit([DECLARATIONS],
                         f"Record the scope of {cfg['library']['theorem']}: "
                         f"{counts['members']} declarations (I2)")
    declarations_sha = sha256_file(repo.root / DECLARATIONS)
    _warn_table(repo, declarations_sha)
    stages.write_receipt(
        paths, informalize_id, "I2",
        inputs={k: sha256_file(home / k) for k in ("library/index/index.jsonl",
                                                    "library/deps.json", "library/host/src.tar")},
        outputs={"scope.json": sha256_file(scope_path), f"repo/{DECLARATIONS}": declarations_sha},
        checks={"theorem": cfg["library"]["theorem"], **counts})
    return ScopeResult(members=counts["members"], counts=counts, commit=commit,
                       scope_path=scope_path, declarations_sha256=declarations_sha)


def _warn_table(repo: OutputRepo, declarations_sha: str) -> None:
    """Say so when an item table pins another scope: check-items refuses it.

    The pin is over the whole file, so a re-run that changes only what a member is described
    by -- `source_url` and `references` arrived this way -- re-keys it while every fact the
    table was drafted against stands.  The remedy is the same either way, and it is the
    maintainer's: set `library.declarations_sha256` to the sha below, run `check-items`, and
    approve the table again (section 5.3)."""
    raw = repo.read_bytes("informal/items.json")
    if raw is None:
        return
    try:
        pinned = (json.loads(raw).get("library") or {}).get("declarations_sha256")
    except (ValueError, AttributeError):
        return
    if pinned and pinned != declarations_sha:
        log.warn("informal/items.json pins another declarations.json; `cf informalize "
                 "check-items` refuses it until its library.declarations_sha256 is "
                 f"{declarations_sha}, and the table is approved again")
