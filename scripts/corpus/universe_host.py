"""Plan, check and record a universe build on the run host.

New code for docs/proposals/wikipedia-locate.md §2.3 (universes) and §2.4
(library indexes). It is the Python half of
`scripts/corpus/build_universe_host.sh`, which fetches, builds and indexes
every library of a universe list on the run host; the bash driver does the
process work (git, lake, nice/ionice, the disk floor) and calls this file for
everything that parses or writes structured data: the universe list itself,
Lake root globs, the Mathlib-family packages of a lake manifest, the hard-link
donor registry, the "is this index finished" test, the manifest of a
*published* index (one a library publishes itself, such as ATLAS's
`autoform-library-index.jsonl`), per-library state and the status table.

Standard library only, and it imports nothing from `cflib`: like
`scripts/corpus/probe_merge.py` it is copied to a run host where `cflib` is not
on the path (CLAUDE.md §4, "the gate runs out of tree").

The universe list is TSV, one library per line, `#` comments and blank lines
ignored::

    id <TAB> repo-url <TAB> ref <TAB> roots <TAB> kind [<TAB> options]

`ref` is a 40-hex commit, a branch or a tag. `roots` are Lake globs separated
by commas (`Mod`, `Mod.+` for the submodules of `Mod`, `Mod.*` for both).
`kind` is `build` (clone, fetch caches, build, probe) or `published-index`
(clone, copy the index the library publishes, write a manifest). `options` are
`KEY=VALUE` pairs separated by `;`; the keys are `OPTION_DEFAULTS` below.

Subcommands (all print to stdout; failures go to stderr with exit status 2)::

    ids        --universe F                     library ids in file order (checks the whole file)
    entry      --universe F --id ID             shell assignments for one library (the driver evals them)
    expand-roots --checkout D --roots SPEC [--src-dir S] --out F --prefixes-out F
    family     --checkout D                     mathlib | self | none: how the package relates to Mathlib
    find-donor --checkout D --donors F          a registered checkout holding identical Mathlib-family packages
    add-donor  --checkout D --donors F --label L
    index-ok   --universe F --id ID --commit C --out-dir D [--roots-file F --prefixes-file F]
               exit 0 finished and current, 1 absent or unfinished, 2 finished for another spec,
               4 cannot decide without the expanded roots
    write-entry --universe F --id ID --commit C --out-dir D [KEY=VALUE ...]
    published-manifest --index F --out F --universe U --id ID --commit C --tag-commit T
               --source S [--blob-sha B] --checkout D --started ISO
    state      --file F KEY=VALUE ...           merge keys into a per-library state file
    summary    --universe F --state-dir D       the status table, TSV
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

#: Packages that come with Mathlib at a given Mathlib commit and dominate a workspace's disk.
FAMILY = ("mathlib", "batteries", "aesop", "Qq", "proofwidgets", "plausible",
          "LeanSearchClient", "importGraph", "Cli")
KINDS = ("build", "published-index")
#: Every option a universe line may set, with the value used when it does not.
OPTION_DEFAULTS = {
    "SHARD_COUNT": "8",           # probe shards
    "MAX_PARALLEL": "",           # probe shards at once (probe_host.sh default: min(SHARD_COUNT, 24))
    "BUILD_TARGETS": "",          # lake build targets (default: the roots without glob suffixes)
    "MODULE_PREFIXES": "",        # modules that are the library's own (default: from the roots)
    "SRC_DIR": ".",               # where the roots' globs are expanded, relative to the checkout
    "SPARSE": "",                 # cone sparse-checkout directories (comma separated)
    "INDEX_FILE": "autoform-library-index.jsonl",   # published-index: the file the library publishes
    "BUILD_TIMEOUT": "14400",     # seconds for `lake build`
    "PROBE_TIMEOUT": "7200",      # seconds for probe_host.sh
    "LINK_FAMILY": "1",           # 0: never hard-link Mathlib-family packages into this library
}
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_SHA = re.compile(r"[0-9a-f]{40}")
_REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}")
_URL = re.compile(r"https://[A-Za-z0-9.-]+/[A-Za-z0-9._/-]+")
_MODULE = r"[A-Za-z_][A-Za-z0-9_'!?]*(?:\.[A-Za-z_][A-Za-z0-9_'!?]*)*"
_MODULE_RE = re.compile(_MODULE)
_GLOB = re.compile(rf"({_MODULE})(\.\+|\.\*)?")
_OPTION_VALUE = re.compile(r"[A-Za-z0-9._,/@:+*-]*")


class UniverseError(RuntimeError):
    """The universe list, a checkout or an index is not what the driver needs; says why."""


def norm_url(url: str) -> str:
    """A repository URL without a trailing slash or `.git`, for comparisons only."""
    url = url.strip().rstrip("/")
    return url[:-4] if url.endswith(".git") else url


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, data: dict) -> None:
    """Atomic: a reader sees the old file or the new one, never half of one."""
    staged = path.with_name(path.name + f".tmp{os.getpid()}")
    staged.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staged, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# the universe list
# ---------------------------------------------------------------------------

class Entry:
    """One line of a universe list, validated."""

    def __init__(self, line_no: int, fields: list[str]):
        where = f"line {line_no}"
        if len(fields) not in (5, 6):
            raise UniverseError(f"{where}: {len(fields)} tab-separated fields; want 5 or 6")
        self.id, self.url, self.ref, self.roots_spec, self.kind = (f.strip() for f in fields[:5])
        if not _ID.fullmatch(self.id):
            raise UniverseError(f"{where}: id {self.id!r} is not [a-z0-9][a-z0-9._-]*")
        if not _URL.fullmatch(self.url):
            raise UniverseError(f"{where}: {self.url!r} is not an https repository URL")
        if not (_SHA.fullmatch(self.ref) or (_REF.fullmatch(self.ref) and ".." not in self.ref)):
            raise UniverseError(f"{where}: ref {self.ref!r} is not a commit, branch or tag name")
        if self.kind not in KINDS:
            raise UniverseError(f"{where}: kind {self.kind!r} is not one of {', '.join(KINDS)}")
        self.globs = [g for g in self.roots_spec.split(",") if g]
        if not self.globs:
            raise UniverseError(f"{where}: no roots")
        for glob in self.globs:
            if not _GLOB.fullmatch(glob):
                raise UniverseError(f"{where}: root {glob!r} is not a Lake module glob")
        self.options = dict(OPTION_DEFAULTS)
        raw = fields[5].strip() if len(fields) == 6 else ""
        for pair in (p for p in raw.split(";") if p.strip()):
            key, sep, value = pair.strip().partition("=")
            if not sep or key not in OPTION_DEFAULTS:
                raise UniverseError(f"{where}: option {pair!r} is not KEY=VALUE with KEY one of "
                                    f"{', '.join(OPTION_DEFAULTS)}")
            if not _OPTION_VALUE.fullmatch(value):
                raise UniverseError(f"{where}: option {key} has characters outside [A-Za-z0-9._,/@:+*-]")
            self.options[key] = value
        for key in ("SHARD_COUNT", "BUILD_TIMEOUT", "PROBE_TIMEOUT"):
            if not self.options[key].isdigit() or int(self.options[key]) < 1:
                raise UniverseError(f"{where}: {key} must be a positive integer")
        if self.options["MAX_PARALLEL"] and not self.options["MAX_PARALLEL"].isdigit():
            raise UniverseError(f"{where}: MAX_PARALLEL must be a positive integer")
        if self.options["LINK_FAMILY"] not in ("0", "1"):
            raise UniverseError(f"{where}: LINK_FAMILY is 0 or 1")
        for key in ("BUILD_TARGETS", "MODULE_PREFIXES"):
            for item in (i for i in self.options[key].split(",") if i):
                if not _MODULE_RE.fullmatch(item):
                    raise UniverseError(f"{where}: {key} item {item!r} is not a name")
        for key in ("SRC_DIR", "INDEX_FILE", *self.options["SPARSE"].split(",")):
            path = self.options.get(key, key)
            if path and (path.startswith("/") or ".." in path.split("/")):
                raise UniverseError(f"{where}: {path!r} must be a relative path inside the checkout")

    @property
    def bases(self) -> list[str]:
        """The roots' module names with any glob suffix removed, in order, without repeats."""
        out: list[str] = []
        for glob in self.globs:
            base = _GLOB.fullmatch(glob).group(1)
            if base not in out:
                out.append(base)
        return out

    def spec(self, commit: str) -> dict:
        """What makes two indexes of this library the same index (shard counts do not)."""
        spec = {"id": self.id, "url": norm_url(self.url), "commit": commit, "kind": self.kind,
                "roots": self.globs, "src_dir": self.options["SRC_DIR"],
                "module_prefixes": self.options["MODULE_PREFIXES"]}
        if self.kind == "published-index":
            spec["index_file"] = self.options["INDEX_FILE"]
        return spec


def load_universe(path: Path) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[str] = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        entry = Entry(line_no, line.split("\t"))
        if entry.id in seen:
            raise UniverseError(f"line {line_no}: library id {entry.id} appears twice")
        seen.add(entry.id)
        entries.append(entry)
    if not entries:
        raise UniverseError(f"{path} lists no library")
    return entries


def get_entry(path: Path, lib_id: str) -> Entry:
    for entry in load_universe(path):
        if entry.id == lib_id:
            return entry
    raise UniverseError(f"{path} has no library {lib_id!r}")


def shell_assignments(entry: Entry) -> str:
    """The entry as `NAME=value` lines a POSIX shell can eval safely."""
    values = {"LIB_ID": entry.id, "LIB_URL": entry.url, "LIB_REF": entry.ref,
              "LIB_REF_IS_SHA": "1" if _SHA.fullmatch(entry.ref) else "0",
              "LIB_ROOTS": entry.roots_spec, "LIB_KIND": entry.kind,
              "LIB_BASES": ",".join(entry.bases)}
    values.update({f"OPT_{key}": value for key, value in entry.options.items()})
    return "".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items())


# ---------------------------------------------------------------------------
# roots
# ---------------------------------------------------------------------------

def expand_roots(checkout: Path, globs: list[str], src_dir: str) -> tuple[list[str], list[str]]:
    """Lake globs to module names (sorted), and the smallest prefix set that covers them."""
    src = checkout / src_dir
    modules: set[str] = set()
    for glob in globs:
        match = _GLOB.fullmatch(glob)
        base, suffix = match.group(1), match.group(2)
        base_file = src.joinpath(*base.split(".")).with_suffix(".lean")
        if suffix in (None, ".*"):
            if base_file.is_file():
                modules.add(base)
            elif suffix is None:
                raise UniverseError(f"root module {base} has no source file {base_file}")
        if suffix in (".+", ".*"):
            base_dir = src.joinpath(*base.split("."))
            found = 0
            for path in sorted(base_dir.rglob("*.lean")) if base_dir.is_dir() else []:
                name = ".".join(path.relative_to(src).with_suffix("").parts)
                if not _MODULE_RE.fullmatch(name):
                    print(f"universe_host: skipping {path.relative_to(checkout)}: not a module name",
                          file=sys.stderr)
                    continue
                modules.add(name)
                found += 1
            if not found and suffix == ".+":
                raise UniverseError(f"glob {glob} matches no module under {base_dir}")
    if not modules:
        raise UniverseError("the roots expand to no module")
    bases = sorted({_GLOB.fullmatch(g).group(1) for g in globs}, key=lambda b: (b.count("."), b))
    prefixes: list[str] = []
    for base in bases:
        if not any(base == p or base.startswith(p + ".") for p in prefixes):
            prefixes.append(base)
    return sorted(modules), sorted(prefixes)


# ---------------------------------------------------------------------------
# Mathlib-family packages and hard-link donors
# ---------------------------------------------------------------------------

def read_manifest(checkout: Path) -> dict:
    path = checkout / "lake-manifest.json"
    if not path.is_file():
        raise UniverseError(f"{checkout} has no lake-manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def family_of(checkout: Path) -> dict[str, tuple[str, str]]:
    """{name: (rev, url)} for the Mathlib-family git packages of a manifest that requires Mathlib."""
    manifest = read_manifest(checkout)
    packages = {p.get("name"): p for p in manifest.get("packages", []) if p.get("type") == "git"}
    if "mathlib" not in packages:
        return {}
    return {name: (packages[name]["rev"], packages[name]["url"]) for name in FAMILY if name in packages}


def relation_to_mathlib(checkout: Path) -> str:
    manifest = read_manifest(checkout)
    if str(manifest.get("name", "")).strip("«»") == "mathlib":
        return "self"
    return "mathlib" if family_of(checkout) else "none"


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


def _git(directory: Path, *args: str) -> str:
    out = subprocess.run(["git", *_GIT_SAFE_CONFIG, "-C", str(directory), *args], capture_output=True,
                         text=True, env={**os.environ, **_GIT_SAFE_ENV})
    return out.stdout.strip() if out.returncode == 0 else ""


def family_key(family: dict[str, tuple[str, str]]) -> str:
    return ";".join(f"{name}={rev}@{norm_url(url)}" for name, (rev, url) in sorted(family.items()))


def _packages_dir(checkout: Path) -> Path:
    packages_dir = read_manifest(checkout).get("packagesDir", ".lake/packages")
    if packages_dir != ".lake/packages":
        raise UniverseError(f"{checkout} keeps packages in {packages_dir!r}; only .lake/packages is handled")
    return checkout / ".lake" / "packages"


def verify_family_on_disk(checkout: Path, family: dict[str, tuple[str, str]]) -> str:
    """Empty when every family package is checked out at its manifest rev and URL; else why not."""
    packages = _packages_dir(checkout)
    for name, (rev, url) in family.items():
        directory = packages / name
        if not (directory / ".git").exists():
            return f"{directory} is not a git checkout"
        head = _git(directory, "rev-parse", "HEAD")
        if head != rev:
            return f"{directory} is at {head[:12] or '?'}, the manifest pins {rev[:12]}"
        origin = _git(directory, "remote", "get-url", "origin")
        if origin != url:
            return f"{directory} fetches from {origin!r}, the manifest says {url!r}"
    if "mathlib" in family and not (packages / "mathlib/.lake/build/lib/lean/Mathlib.olean").is_file():
        return f"{packages / 'mathlib'} has no built Mathlib.olean"
    return ""


def read_donors(path: Path) -> list[tuple[str, str, str]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) >= 3 and not line.startswith("#"):
            rows.append((fields[0], fields[1], fields[2]))
    return rows


def find_donor(checkout: Path, donors: Path) -> tuple[str, list[str]]:
    """A registered checkout whose family packages equal this checkout's, re-verified on disk."""
    family = family_of(checkout)
    if not family:
        raise UniverseError("the package does not require Mathlib; nothing to link")
    packages = _packages_dir(checkout)
    present = [name for name in family if (packages / name).exists()]
    if present:
        raise UniverseError(f"{', '.join(present)} already present under {packages}; not linking")
    key = family_key(family)
    for donor_key, donor_checkout, label in read_donors(donors):
        donor = Path(donor_checkout)
        if donor_key != key or donor.resolve() == checkout.resolve():
            continue
        if not donor.is_dir():
            continue
        why = verify_family_on_disk(donor, family)
        if why:
            print(f"universe_host: donor {label} rejected: {why}", file=sys.stderr)
            continue
        return str(donor), sorted(family)
    raise UniverseError(f"no registered donor holds this family ({len(family)} packages, "
                        f"mathlib {family['mathlib'][0][:12]})")


def add_donor(checkout: Path, donors: Path, label: str) -> str:
    family = family_of(checkout)
    if not family:
        raise UniverseError("the package does not require Mathlib; not a donor")
    why = verify_family_on_disk(checkout, family)
    if why:
        raise UniverseError(f"not registering {label} as a donor: {why}")
    key = family_key(family)
    rows = read_donors(donors)
    if any(row[0] == key and Path(row[1]).resolve() == checkout.resolve() for row in rows):
        return "already registered"
    donors.parent.mkdir(parents=True, exist_ok=True)
    with open(donors, "a", encoding="utf-8") as handle:
        handle.write(f"{key}\t{checkout.resolve()}\t{label}\t{utc_now()}\n")
    return f"registered (mathlib {family['mathlib'][0][:12]})"


# ---------------------------------------------------------------------------
# finished indexes
# ---------------------------------------------------------------------------

SIDECAR = "universe-entry.json"


def index_ok(entry: Entry, commit: str, out_dir: Path, roots_file: Path | None,
             prefixes_file: Path | None) -> tuple[int, str]:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        return 1, "no manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return 1, f"manifest.json unreadable ({error})"
    index_name = "index.jsonl" if entry.kind == "build" else entry.options["INDEX_FILE"]
    index = out_dir / index_name
    if not index.is_file() or index.stat().st_size != manifest.get("index_bytes"):
        return 1, f"{index_name} missing or not the size manifest.json records"
    if manifest.get("library") != entry.id:
        return 2, f"manifest.json is for library {manifest.get('library')!r}"
    sidecar = out_dir / SIDECAR
    if sidecar.is_file():
        recorded = json.loads(sidecar.read_text(encoding="utf-8")).get("spec")
        if recorded == entry.spec(commit):
            return 0, "finished"
        return 2, "finished for a different universe entry"
    if entry.kind != "build" or manifest.get("commit") != commit:
        return 2, "finished by something other than this driver"
    if roots_file is None or prefixes_file is None:
        return 4, "an index from before this driver; needs the expanded roots"
    roots = sorted(l for l in roots_file.read_text(encoding="utf-8").splitlines() if l)
    prefixes = sorted(l for l in prefixes_file.read_text(encoding="utf-8").splitlines() if l)
    if manifest.get("roots") == roots and manifest.get("module_prefixes") == prefixes:
        return 0, "finished (adopted)"
    return 2, (f"finished for roots {manifest.get('roots')} / prefixes "
               f"{manifest.get('module_prefixes')}, not {roots[:5]} / {prefixes}")


def write_entry(entry: Entry, commit: str, out_dir: Path, extra: dict) -> None:
    write_json(out_dir / SIDECAR, {"spec": entry.spec(commit), "written_at": utc_now(), **extra})


# ---------------------------------------------------------------------------
# published indexes
# ---------------------------------------------------------------------------

def published_manifest(index: Path, entry: Entry, commit: str, tag_commit: str, source: str,
                       blob_sha: str, checkout: Path, started: str) -> dict:
    """Count and check an `autoform-library-index/v0` file a library publishes itself."""
    bases = entry.bases
    records: Counter = Counter()
    by_kind: Counter = Counter()
    by_status: Counter = Counter()
    by_root: Counter = Counter()
    header: dict | None = None
    outside: list[str] = []
    missing_source = 0
    with open(index, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise UniverseError(f"{index.name} line {line_no}: not JSON ({error.msg})") from None
            kind = record.get("record")
            records[kind] += 1
            if line_no == 1:
                if kind != "header":
                    raise UniverseError(f"{index.name} does not start with a header record")
                header = record
                continue
            name = str(record.get("module" if kind == "declaration" else "name", ""))
            if kind in ("module", "declaration"):
                root = next((b for b in bases if name == b or name.startswith(b + ".")), None)
                if root is None:
                    outside.append(name)
                else:
                    by_root[f"{root}:{kind}"] += 1
            if kind == "module":
                source_file = str(record.get("source_file") or "")
                if source_file.startswith("v1/") or not source_file:
                    outside.append(f"{name} ({source_file or 'no source_file'})")
                elif not (checkout / source_file).is_file():
                    missing_source += 1
            elif kind == "declaration":
                by_kind[str(record.get("kind"))] += 1
                by_status[str(record.get("status"))] += 1
    if header is None:
        raise UniverseError(f"{index.name} is empty")
    if header.get("schema") != "autoform-library-index/v0":
        raise UniverseError(f"{index.name} has schema {header.get('schema')!r}, not autoform-library-index/v0")
    if outside:
        raise UniverseError(f"{len(outside)} records lie outside the roots {bases}: {outside[:5]}")
    return {
        "schema": "formal-companion-library-index/v0/manifest",
        "kind": "published-index",
        "index_schema": header.get("schema"),
        "library": entry.id,
        "repo": norm_url(entry.url),
        "ref": entry.ref,
        "commit": commit,
        "commit_date": _git(checkout, "log", "-1", "--format=%cI", commit) or None,
        "tag_commit": tag_commit,
        "checkout_head": _git(checkout, "rev-parse", "HEAD") or None,
        "toolchain": header.get("lean_toolchain"),
        "generator": header.get("generator"),
        "package": header.get("package"),
        "source_dirs": header.get("source_dirs"),
        "dependencies": header.get("dependencies"),
        "roots": entry.globs,
        "index_file": index.name,
        "index_source": source,
        "index_blob_sha": blob_sha or None,
        "index_sha256": sha256_file(index),
        "index_bytes": index.stat().st_size,
        "records": dict(sorted((str(k), v) for k, v in records.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "by_status": dict(sorted(by_status.items())),
        "by_root": dict(sorted(by_root.items())),
        "modules_without_source_in_checkout": missing_source,
        "started_at": started,
        "finished_at": utc_now(),
    }


# ---------------------------------------------------------------------------
# state and summary
# ---------------------------------------------------------------------------

def update_state(path: Path, pairs: list[str]) -> None:
    state = {}
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise UniverseError(f"state pair {pair!r} is not KEY=VALUE")
        state[key] = int(value) if re.fullmatch(r"-?[0-9]{1,15}", value) else value
    state["updated_at"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, state)


def summary(entries: list[Entry], state_dir: Path) -> str:
    columns = ("id", "kind", "commit", "stage", "seconds", "declarations", "by_status",
               "linked_from", "disk_delta_gb", "note")
    lines = ["\t".join(columns)]
    stages: Counter = Counter()
    for entry in entries:
        path = state_dir / f"{entry.id}.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        stage = str(state.get("stage", "queued"))
        stages[stage] += 1
        row = [entry.id, entry.kind, str(state.get("commit", ""))[:12], stage,
               str(state.get("wall_seconds", "")), str(state.get("declarations", "")),
               str(state.get("by_status", "")), str(state.get("linked_from", "")),
               str(state.get("disk_delta_gb", "")),
               str(state.get("error", "") or state.get("note", ""))[:160]]
        lines.append("\t".join(row))
    lines.append("# " + ", ".join(f"{k} {v}" for k, v in sorted(stages.items())))
    return "\n".join(lines) + "\n"


def manifest_brief(manifest_path: Path) -> list[str]:
    """`declarations=N by_status=a:1,b:2` for the driver's state file."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    statuses = manifest.get("by_proof_status") or manifest.get("by_status") or {}
    return [f"declarations={manifest.get('records', {}).get('declaration', 0)}",
            "by_status=" + ",".join(f"{k}:{v}" for k, v in sorted(statuses.items())),
            f"index_sha256={manifest.get('index_sha256', '')}"]


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("ids"); p.add_argument("--universe", required=True, type=Path)
    p = sub.add_parser("entry"); p.add_argument("--universe", required=True, type=Path)
    p.add_argument("--id", required=True)
    p = sub.add_parser("expand-roots")
    p.add_argument("--checkout", required=True, type=Path); p.add_argument("--roots", required=True)
    p.add_argument("--src-dir", default="."); p.add_argument("--out", required=True, type=Path)
    p.add_argument("--prefixes-out", required=True, type=Path)
    p = sub.add_parser("family"); p.add_argument("--checkout", required=True, type=Path)
    for name in ("find-donor", "add-donor"):
        p = sub.add_parser(name)
        p.add_argument("--checkout", required=True, type=Path)
        p.add_argument("--donors", required=True, type=Path)
        p.add_argument("--label", default="")
    for name in ("index-ok", "write-entry"):
        p = sub.add_parser(name)
        p.add_argument("--universe", required=True, type=Path); p.add_argument("--id", required=True)
        p.add_argument("--commit", required=True); p.add_argument("--out-dir", required=True, type=Path)
        p.add_argument("--roots-file", type=Path); p.add_argument("--prefixes-file", type=Path)
        p.add_argument("pairs", nargs="*")
    p = sub.add_parser("published-manifest")
    for flag in ("--index", "--out", "--universe", "--checkout"):
        p.add_argument(flag, required=True, type=Path)
    for flag in ("--id", "--commit", "--tag-commit", "--source", "--started"):
        p.add_argument(flag, required=True)
    p.add_argument("--blob-sha", default="")
    p = sub.add_parser("manifest-brief"); p.add_argument("--manifest", required=True, type=Path)
    p = sub.add_parser("state"); p.add_argument("--file", required=True, type=Path)
    p.add_argument("pairs", nargs="*")
    p = sub.add_parser("summary"); p.add_argument("--universe", required=True, type=Path)
    p.add_argument("--state-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "ids":
            print("\n".join(e.id for e in load_universe(args.universe)))
        elif args.command == "entry":
            sys.stdout.write(shell_assignments(get_entry(args.universe, args.id)))
        elif args.command == "expand-roots":
            globs = [g for g in args.roots.split(",") if g]
            for glob in globs:
                if not _GLOB.fullmatch(glob):
                    raise UniverseError(f"{glob!r} is not a Lake module glob")
            modules, prefixes = expand_roots(args.checkout, globs, args.src_dir)
            args.out.write_text("\n".join(modules) + "\n", encoding="utf-8")
            args.prefixes_out.write_text("\n".join(prefixes) + "\n", encoding="utf-8")
            print(f"{len(modules)} modules, prefixes {','.join(prefixes)}")
        elif args.command == "family":
            print(relation_to_mathlib(args.checkout))
        elif args.command == "find-donor":
            donor, names = find_donor(args.checkout, args.donors)
            print(donor)
            print("\n".join(names))
        elif args.command == "add-donor":
            print(add_donor(args.checkout, args.donors, args.label or str(args.checkout)))
        elif args.command == "index-ok":
            entry = get_entry(args.universe, args.id)
            code, why = index_ok(entry, args.commit, args.out_dir, args.roots_file, args.prefixes_file)
            print(why)
            return code
        elif args.command == "write-entry":
            extra = dict(pair.partition("=")[::2] for pair in args.pairs)
            write_entry(get_entry(args.universe, args.id), args.commit, args.out_dir, extra)
        elif args.command == "published-manifest":
            entry = get_entry(args.universe, args.id)
            manifest = published_manifest(args.index, entry, args.commit, args.tag_commit,
                                          args.source, args.blob_sha, args.checkout, args.started)
            write_json(args.out, manifest)
            print(json.dumps({k: manifest[k] for k in ("library", "commit", "records", "by_status")},
                             sort_keys=True))
        elif args.command == "manifest-brief":
            print("\n".join(manifest_brief(args.manifest)))
        elif args.command == "state":
            update_state(args.file, args.pairs)
        elif args.command == "summary":
            sys.stdout.write(summary(load_universe(args.universe), args.state_dir))
        return 0
    except (UniverseError, OSError, json.JSONDecodeError, KeyError) as error:
        print(f"universe_host: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
