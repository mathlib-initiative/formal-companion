"""Render, time and merge the library probe on the run host.

New code for docs/proposals/wikipedia-locate.md §2.3 (universes and library
indexes). It is the host half of a library index: `scripts/corpus/probe_host.sh`
calls it to render `templates/corpus/probe.lean.tmpl`, to run one probe shard
under a timer, and to merge the shard files into one deterministic
`index.jsonl` plus a `manifest.json`.

Standard library only, and it imports nothing from `cflib`: it is copied to a
run host where `cflib` is not on the path, the same reason
`gate/verify_findings.py` stands alone (CLAUDE.md §4). The shard hash is
defined here and in the probe template, and the merge re-derives every
declaration's shard so a template that drifts from this file is refused.

Subcommands::

    probe_merge.py render    --template T --library ID --roots R [--prefixes P] [--src-dirs D] --out FILE
    probe_merge.py run-shard --timing FILE -- <command ...>
    probe_merge.py merge     --work DIR --shard-count N --checkout DIR --out-dir DIR --template T
                             --probe FILE --parallel P --started ISO
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import resource
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

#: The schema the probe writes; ``autoform-library-index/v0`` is its base shape.
SCHEMA = "formal-companion-library-index/v0"
#: A name component the probe writes as it is: a plain ASCII identifier.
_PLAIN_COMPONENT = re.compile(r"[A-Za-z_][A-Za-z0-9_'!?]*")
#: a component as Lean PRINTS one unescaped: Lean leaves any identifier plain, Unicode
#: letters included (``Foo.ö``), so a name read back from the probe is parsed this loosely
_PRINTED_COMPONENT = re.compile(r"[^.«»\s\x00-\x1f\x7f]+")
#: what may stand between « and »: anything but the guillemets and control characters, so
#: an escaped component can neither close early nor carry a newline into the rendered probe
_ESCAPED_BODY = re.compile(r"[^«»\x00-\x1f\x7f]+")
_LIBRARY_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_PLACEHOLDERS = ("@@IMPORTS@@", "@@LIBRARY_ID@@", "@@ROOTS@@", "@@PREFIXES@@", "@@SRC_DIRS@@")
KINDS = ("abbrev", "axiom", "class", "def", "inductive", "instance", "opaque", "structure", "theorem")
PROOF_STATUSES = ("axiom", "definition", "proved", "statement_only", "wanted")


class ProbeError(RuntimeError):
    """The probe output cannot become an index; the message says why."""


def fnv1a64(text: str) -> int:
    """FNV-1a, 64 bit, over UTF-8: the probe template's `CfLibraryProbe.fnv1a64`."""
    h = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def shard_of(name: str, count: int) -> int:
    """The shard a declaration name belongs to."""
    return fnv1a64(name) % count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def split_list(value: str) -> list[str]:
    """A comma/space/newline separated list, or ``@FILE`` naming one item per line.

    A separator inside ``«…»`` is part of the item: an escaped module name may
    hold a space or a comma (``Foo.«a, b»``) and is still one item.
    """
    if value.startswith("@"):
        value = Path(value[1:]).read_text(encoding="utf-8")
    items: list[str] = []
    current: list[str] = []
    quoted = False
    for ch in value:
        if quoted:
            current.append(ch)
            quoted = ch != "»"
        elif ch == "«":
            current.append(ch)
            quoted = True
        elif ch.isspace() or ch == ",":
            if current:
                items.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        items.append("".join(current))
    return items


# ---------------------------------------------------------------------------
# module names
# ---------------------------------------------------------------------------
#
# Lake names a module after its file: ``FormalConjectures/ErdosProblems/1.lean`` is
# the module whose last component is the string ``1``, and a directory
# ``Arxiv/0805.3777/`` is ONE component holding a dot.  Neither is an identifier, so
# Lean writes them escaped -- ``FormalConjectures.ErdosProblems.«1»``,
# ``FormalConjectures.Arxiv.«0805.3777».TensorRank`` -- and that is the only spelling
# an ``import`` line can parse.  These four functions are the one place the drivers
# turn a file into a module name and a module name back into its components.  An
# escaped spelling and a plain one of the same components are the same module
# (``Foo.«ö»`` is ``Foo.ö``), which is why names are compared by
# :func:`module_components`, never as strings.


def escape_component(component: str) -> str:
    """One name component as an ``import`` line may spell it: plain, or ``«escaped»``.

    Refused (``ProbeError``): an empty component, and one holding ``«``, ``»`` or a
    control character, which no escape can carry.
    """
    if _PLAIN_COMPONENT.fullmatch(component):
        return component
    if not component or not _ESCAPED_BODY.fullmatch(component):
        raise ProbeError(f"the name component {component!r} cannot be written as a Lean module "
                         f"name (it is empty, or holds «, » or a control character)")
    return f"«{component}»"


def module_name(components: "list[str] | tuple[str, ...]") -> str:
    """The dotted, escaped name of a module given by its components."""
    if not components:
        raise ProbeError("a module name needs at least one component")
    return ".".join(escape_component(c) for c in components)


def module_components(name: str, *, strict: bool = False) -> "tuple[str, ...] | None":
    """The components of a dotted module name, plain or ``«escaped»``; None when malformed.

    ``Foo.«0805.3777».Bar`` is ``("Foo", "0805.3777", "Bar")``.  Malformed: an empty
    component, an unclosed ``«``, text after a ``»`` before the next dot, an escaped
    component holding a control character, and a plain one holding whitespace or a
    control character -- or, with ``strict``, a plain one that is not an ASCII
    identifier: the spelling this file WRITES into an ``import`` line.  Without it a
    plain component is read as Lean prints one (``Foo.ö``, which Lean leaves unescaped).
    """
    plain = _PLAIN_COMPONENT if strict else _PRINTED_COMPONENT
    parts: list[str] = []
    i, n = 0, len(name)
    while True:
        if i < n and name[i] == "«":
            close = name.find("»", i + 1)
            if close < 0:
                return None
            body = name[i + 1:close]
            if not _ESCAPED_BODY.fullmatch(body):
                return None
            parts.append(body)
            i = close + 1
        else:
            dot = name.find(".", i)
            end = n if dot < 0 else dot
            piece = name[i:end]
            if not plain.fullmatch(piece):
                return None
            parts.append(piece)
            i = end
        if i == n:
            return tuple(parts)
        if name[i] != ".":
            return None
        i += 1
        if i == n:
            return None


def module_of_file(relative: "Path | str") -> str:
    """The module a ``.lean`` file is, given its path relative to the source directory."""
    parts = Path(relative).with_suffix("").parts
    return module_name(list(parts))


def module_file(name: str) -> str:
    """The ``.lean`` path a module name is read from, relative to its source directory."""
    components = module_components(name)
    if components is None:
        raise ProbeError(f"{name!r} is not a module name")
    return "/".join(components) + ".lean"


def module_ok(name: str) -> bool:
    """A name this file may write into an ``import`` line: every component plain ASCII or escaped."""
    return module_components(name, strict=True) is not None


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def render(template: str, library: str, roots: list[str], prefixes: list[str],
           src_dirs: list[str]) -> str:
    """The probe for one library: the template with its five fields filled."""
    if not _LIBRARY_ID.fullmatch(library):
        raise ProbeError(f"library id {library!r} is not [a-z0-9][a-z0-9._-]*")
    roots = sorted(set(roots))
    prefixes = sorted(set(prefixes or roots))
    if not roots:
        raise ProbeError("no root module to import")
    for module in roots + prefixes:
        # plain or «escaped» components only: the name is written into an `import` line,
        # and no component can close its escape early or carry a newline there
        if not module_ok(module):
            raise ProbeError(f"{module!r} is not a module name the probe can import")
    for directory in src_dirs:
        parts = directory.split("/")
        if directory.startswith("/") or ".." in parts or not directory:
            raise ProbeError(f"source directory {directory!r} must be relative and inside the package")
    for field in _PLACEHOLDERS:
        if template.count(field) != 1:
            raise ProbeError(f"the template must hold {field} exactly once")

    def literal(lines: list[str]) -> str:
        return json.dumps("\n".join(lines), ensure_ascii=False)

    text = (template
            .replace("@@IMPORTS@@", "\n".join(f"import {module}" for module in roots))
            .replace("@@LIBRARY_ID@@", json.dumps(library))
            .replace("@@ROOTS@@", literal(roots))
            .replace("@@PREFIXES@@", literal(prefixes))
            .replace("@@SRC_DIRS@@", literal(src_dirs or ["."])))
    if "@@" in text:
        raise ProbeError("a placeholder is left in the rendered probe")
    return text


# ---------------------------------------------------------------------------
# run-shard
# ---------------------------------------------------------------------------

def run_shard(timing: Path, command: list[str]) -> int:
    """Run one shard, then record its exit code, wall time and peak RSS."""
    started = time.monotonic()
    code = subprocess.call(command)
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    timing.write_text(json.dumps({
        "exit": code,
        "wall_seconds": round(time.monotonic() - started, 3),
        # Linux reports kilobytes: the largest descendant (lake, then lean) that was reaped.
        "max_rss_kb": usage.ru_maxrss,
        "user_seconds": round(usage.ru_utime, 3),
        "system_seconds": round(usage.ru_stime, 3),
    }, sort_keys=True) + "\n", encoding="utf-8")
    return code


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def _strict(line: str, where: str) -> dict:
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ProbeError(f"{where}: repeated key {key!r}")
            out[key] = value
        return out

    try:
        record = json.loads(line, object_pairs_hook=unique)
    except json.JSONDecodeError as error:
        raise ProbeError(f"{where}: not JSON ({error.msg})") from None
    if type(record) is not dict or type(record.get("record")) is not str:
        raise ProbeError(f"{where}: not a probe record")
    return record


def read_shard(path: Path, index: int, count: int) -> tuple[dict, list[dict], list[dict], dict]:
    """One shard file: its header, modules, declarations and end record, checked."""
    if not path.is_file():
        raise ProbeError(f"shard {index} wrote no output ({path.name})")
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        raise ProbeError(f"shard {index} output does not end with a newline; it was cut short")
    lines = text[:-1].split("\n")
    records = [_strict(line, f"shard {index} line {n}") for n, line in enumerate(lines, 1)]
    if len(records) < 2 or records[0]["record"] != "header" or records[-1]["record"] != "shard_end":
        raise ProbeError(f"shard {index} lacks its header or its shard_end record; refusing a partial shard")
    header, end = records[0], records[-1]
    for record in (header, end):
        if record.get("shard_index") != index or record.get("shard_count") != count:
            raise ProbeError(f"shard {index} file says it is shard "
                             f"{record.get('shard_index')}/{record.get('shard_count')}")
    modules = [r for r in records[1:-1] if r["record"] == "module"]
    declarations = [r for r in records[1:-1] if r["record"] == "declaration"]
    other = [r["record"] for r in records[1:-1] if r["record"] not in ("module", "declaration")]
    if other:
        raise ProbeError(f"shard {index} holds unknown records: {sorted(set(other))}")
    if index != 0 and modules:
        raise ProbeError(f"shard {index} emitted module records; only shard 0 may")
    if end.get("declarations") != len(declarations):
        raise ProbeError(f"shard {index} says it emitted {end.get('declarations')} declarations "
                         f"but holds {len(declarations)}")
    for declaration in declarations:
        if shard_of(declaration["name"], count) != index:
            raise ProbeError(f"{declaration['name']} is in shard {index} but hashes to "
                             f"{shard_of(declaration['name'], count)}: the template and "
                             "probe_merge.py disagree on the shard hash")
    return header, modules, declarations, end


#: ``cflib.gitsafe.SAFE_CONFIG`` and ``SAFE_ENV``, inlined because this file runs on
#: the run host with no ``cflib`` on the path (the reason ``gate/verify_findings.py``
#: inlines them too).  A library checkout is a third party's tree: these stop git
#: from running an fsmonitor hook, a hooks path, an external diff, a pager, an ssh
#: command or an askpass program it names.  ``tests/test_locate_corpus.py`` pins both
#: equal to gitsafe's.
_GIT_SAFE_CONFIG: tuple[str, ...] = (
    "-c", "core.fsmonitor=",
    "-c", "core.hooksPath=/dev/null",
    "-c", "diff.external=",
    "-c", "core.pager=cat",
    "-c", "core.sshCommand=",
    "-c", "core.askPass=",
)
_GIT_SAFE_ENV: dict[str, str] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}


#: the content-filter hooks gitsafe blanks, and the git that reads GIT_CONFIG_* pairs
_FILTER_HOOKS = ("clean", "process", "smudge")
_CONFIG_ENV_SINCE = (2, 31)


def _git_env() -> dict[str, str]:
    return {**os.environ, **_GIT_SAFE_ENV}


def _git_version() -> "tuple[int, ...] | None":
    try:
        got = subprocess.run(["git", *_GIT_SAFE_CONFIG, "--version"], capture_output=True,
                             text=True, check=False, env=_git_env())
    except OSError:
        return None
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", got.stdout or "")
    if got.returncode != 0 or not found:
        return None
    return tuple(int(part) for part in found.groups() if part is not None)


def _filter_names(checkout: Path) -> list[str]:
    """``cflib.gitsafe.filter_names``: every content-filter key the checkout resolves."""
    try:
        got = subprocess.run(["git", *_GIT_SAFE_CONFIG, "-C", str(checkout), "config", "--list",
                              "--name-only"], capture_output=True, text=True, check=False,
                             env=_git_env())
    except OSError:
        return []
    names: list[str] = []
    for line in (got.stdout or "").splitlines():
        key = line.strip()
        if key.startswith("filter.") and key.rsplit(".", 1)[-1] in _FILTER_HOOKS:
            if key not in names:
                names.append(key)
    return names


def _filter_pin_args(names: list[str]) -> list[str]:
    """``cflib.gitsafe.filter_pin_args``: ``-c <key>=`` for each name ``-c`` can express."""
    return [arg for key in names if "=" not in key for arg in ("-c", key + "=")]


def _filter_pairs(names: list[str], have: dict[str, str]) -> dict[str, str]:
    """``cflib.gitsafe``'s environment pairs blanking every name, ``=`` names included."""
    if not names:
        return {}
    try:
        start = max(0, int(have.get("GIT_CONFIG_COUNT") or 0))
    except (TypeError, ValueError):
        start = 0
    out = {"GIT_CONFIG_COUNT": str(start + len(names))}
    for i, key in enumerate(names, start):
        out[f"GIT_CONFIG_KEY_{i}"] = key
        out[f"GIT_CONFIG_VALUE_{i}"] = ""
    return out


def _git(checkout: Path, *args: str) -> "str | None":
    """One read-only git command in a library checkout, as ``cflib.gitsafe.run`` makes it.

    The fixed pins, plus a blank for every content filter the checkout names
    (``git status`` compares working-tree content, which runs a
    ``filter.<n>.clean`` the checkout's own ``.git/config`` chose).  A driver
    whose name holds an ``=`` on a git too old to read the environment pairs has
    no blank at all, so the read is not made.  None when it failed or was not
    made: the caller records "unknown", never "clean".
    """
    names = _filter_names(checkout)
    if any("=" in key for key in names):
        version = _git_version()
        if version is None or version < _CONFIG_ENV_SINCE:
            return None
    environ = _git_env()
    environ.update(_filter_pairs(names, environ))
    try:
        out = subprocess.run(["git", *_GIT_SAFE_CONFIG, *_filter_pin_args(names), "-C",
                              str(checkout), *args], capture_output=True, text=True,
                             env=environ)
    except OSError:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _dirty(checkout: Path) -> "bool | None":
    """Tracked files modified in the probed checkout: True, False, or None when unknown."""
    got = _git(checkout, "status", "--porcelain", "--untracked-files=no")
    return None if got is None else bool(got)


def dumps(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def check_roots_indexed(roots: list[str], prefixes: list[str], modules: list[str]) -> None:
    """Every root inside the library's prefixes is one of the probe's module records.

    Names are compared by their components, so the probe's own spelling of a module
    (Lean escapes only what it must) and the driver's agree whatever each escaped.  A
    root missing here is a module the probe imported but did not index -- a prefix
    the probe parsed differently, say -- and the merge refuses it rather than write an
    index short of the roots it names.
    """
    have = {module_components(m) for m in modules}
    wanted = [module_components(p) for p in prefixes]
    if None in wanted:
        raise ProbeError(f"a module prefix of the probe header is not a module name: {prefixes}")
    missing = []
    for root in roots:
        parts = module_components(root)
        if parts is None:
            raise ProbeError(f"the probe header names the root {root!r}, which is not a module name")
        inside = any(parts[:len(p)] == p for p in wanted)
        if inside and parts not in have:
            missing.append(root)
    if missing:
        raise ProbeError(f"{len(missing)} root module(s) inside the prefixes {prefixes} have no "
                         f"module record: {missing[:5]}; the probe imported them but did not "
                         "index them")


def merge(work: Path, count: int, checkout: Path, out_dir: Path, template: Path, probe: Path,
          parallel: int, started: str) -> dict:
    """Merge the shards in ``work`` into ``out_dir``/index.jsonl and manifest.json."""
    shards = [read_shard(work / f"shard-{i}.jsonl", i, count) for i in range(count)]
    header0 = shards[0][0]
    for i, (header, *_rest) in enumerate(shards):
        for key in ("library", "toolchain", "lean_githash", "module_count", "probe_revision",
                    "roots", "module_prefixes", "schema"):
            if header.get(key) != header0.get(key):
                raise ProbeError(f"shard {i} header disagrees with shard 0 on {key}")
    if header0.get("schema") != SCHEMA:
        raise ProbeError(f"unknown probe schema {header0.get('schema')!r}")
    seen = {end["constants_seen"] for *_x, end in shards}
    if len(seen) != 1:
        raise ProbeError(f"the shards saw different constant counts: {sorted(seen)}")
    modules = sorted(shards[0][1], key=lambda r: r["name"])
    if len(modules) != header0["module_count"]:
        raise ProbeError(f"header counts {header0['module_count']} modules, shard 0 emitted {len(modules)}")
    module_names = {m["name"] for m in modules}
    check_roots_indexed(header0.get("roots") or [], header0.get("module_prefixes") or [],
                        [m["name"] for m in modules])
    declarations: dict[tuple[str, str], dict] = {}
    for _h, _m, decls, _e in shards:
        for record in decls:
            key = (record["name"], record["module"])
            if key in declarations:
                raise ProbeError(f"two records for {key[0]} in {key[1]}")
            if record["module"] not in module_names:
                raise ProbeError(f"{key[0]} names module {key[1]}, which has no module record")
            if record["kind"] not in KINDS or record["proof_status"] not in PROOF_STATUSES:
                raise ProbeError(f"{key[0]}: kind {record['kind']!r} / proof_status "
                                 f"{record['proof_status']!r} is outside the schema")
            declarations[key] = record

    commit = _git(checkout, "rev-parse", "HEAD")
    # The index bytes must not depend on how the run was split: the shard fields go to the
    # manifest only, so one shard and thirty-two give the same index.jsonl.
    header = {k: v for k, v in header0.items()
              if k not in ("shard_index", "shard_count", "shard_hash")}
    header.update({"commit": commit or None, "declaration_count": len(declarations),
                   "template_sha256": sha256_file(template)})
    out_dir.mkdir(parents=True, exist_ok=True)
    index = out_dir / "index.jsonl"
    staged = out_dir / "index.jsonl.tmp"
    with open(staged, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dumps(header) + "\n")
        for record in modules:
            handle.write(dumps(record) + "\n")
        for key in sorted(declarations):
            handle.write(dumps(declarations[key]) + "\n")
    os.replace(staged, index)

    decls = list(declarations.values())
    skipped: Counter = Counter()
    for *_x, end in shards:
        skipped.update(end["skipped"])
    axioms: Counter = Counter(a for d in decls for a in d["nonstandard_axioms"])
    timings = []
    for i, (_h, _m, _d, end) in enumerate(shards):
        timing_path = work / f"shard-{i}.timing.json"
        timing = json.loads(timing_path.read_text()) if timing_path.is_file() else {}
        timings.append({"shard": i, "declarations": end["declarations"],
                        "closure_nodes": end["closure_nodes"], "ms_total": end["ms_total"],
                        "ms_closure": end["ms_closure"], "ms_print": end["ms_print"],
                        "ms_other": end["ms_other"], **timing})
    toolchain_file = checkout / "lean-toolchain"
    lake_manifest = checkout / "lake-manifest.json"
    dependencies = []
    if lake_manifest.is_file():
        for package in json.loads(lake_manifest.read_text(encoding="utf-8")).get("packages", []):
            dependencies.append({"name": package.get("name"), "rev": package.get("rev"),
                                 "url": package.get("url")})
    manifest = {
        "schema": SCHEMA + "/manifest",
        "library": header0["library"],
        "commit": commit or None,
        "commit_date": _git(checkout, "log", "-1", "--format=%cI") or None,
        "dirty": _dirty(checkout),
        "toolchain": toolchain_file.read_text(encoding="utf-8").strip() if toolchain_file.is_file() else None,
        "lean_version": header0["toolchain"],
        "lean_githash": header0["lean_githash"],
        "dependencies": sorted(dependencies, key=lambda d: str(d["name"])),
        "roots": header0["roots"],
        "module_prefixes": header0["module_prefixes"],
        "records": {"header": 1, "module": len(modules), "declaration": len(decls)},
        "by_kind": dict(sorted(Counter(d["kind"] for d in decls).items())),
        "by_proof_status": dict(sorted(Counter(d["proof_status"] for d in decls).items())),
        "nonstandard_axioms": dict(sorted(axioms.items())),
        "private": sum(1 for d in decls if d["private"]),
        "deprecated": sum(1 for d in decls if d["deprecated"] is not None),
        "truncated": sum(1 for d in decls if d["truncated"]),
        "with_errors": sum(1 for d in decls if d["errors"]),
        "modules_without_path": sum(1 for m in modules if m["path"] is None),
        "constants_seen": seen.pop(),
        "skipped": dict(sorted(skipped.items())),
        "index_sha256": sha256_file(index),
        "index_bytes": index.stat().st_size,
        "template_sha256": sha256_file(template),
        "probe_sha256": sha256_file(probe),
        "shard_count": count,
        "max_parallel": parallel,
        "started_at": started,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "shards": timings,
    }
    manifest["wall_seconds"] = round(time.time() - _parse_iso(started), 1)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
    return manifest


def _parse_iso(stamp: str) -> float:
    import calendar
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("render")
    p.add_argument("--template", required=True, type=Path)
    p.add_argument("--library", required=True)
    p.add_argument("--roots", required=True)
    p.add_argument("--prefixes", default="")
    p.add_argument("--src-dirs", default=".")
    p.add_argument("--out", required=True, type=Path)
    p = sub.add_parser("run-shard")
    p.add_argument("--timing", required=True, type=Path)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    p = sub.add_parser("merge")
    p.add_argument("--work", required=True, type=Path)
    p.add_argument("--shard-count", required=True, type=int)
    p.add_argument("--checkout", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--template", required=True, type=Path)
    p.add_argument("--probe", required=True, type=Path)
    p.add_argument("--parallel", required=True, type=int)
    p.add_argument("--started", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            text = render(args.template.read_text(encoding="utf-8"), args.library,
                          split_list(args.roots), split_list(args.prefixes),
                          split_list(args.src_dirs))
            args.out.write_text(text, encoding="utf-8")
            return 0
        if args.command == "run-shard":
            command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
            if not command:
                raise ProbeError("run-shard needs a command after --")
            return run_shard(args.timing, command)
        manifest = merge(args.work, args.shard_count, args.checkout, args.out_dir, args.template,
                         args.probe, args.parallel, args.started)
        print(json.dumps({k: manifest[k] for k in ("library", "commit", "records", "by_kind",
                                                     "by_proof_status", "index_sha256",
                                                     "wall_seconds")}, sort_keys=True))
        return 0
    except (ProbeError, OSError) as error:
        print(f"probe_merge: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
