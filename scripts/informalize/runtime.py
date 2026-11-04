"""The LaTeX runtime in an output repository: `cf informalize sync` and `sync --check`.

New code, for docs/proposals/informalize.md sections 5.2 and 7.1, after the overlay's own
sync (`scripts/overlay/sync.py`, docs/overlay.md section 6).  Three kinds of file in
`informal/` are the command's:

* every file under `templates/informalize/runtime/`, copied byte for byte with nothing
  substituted (never a `__pycache__/` or `*.pyc`), and `tools/` is the runtime's alone;
* `informalize.json`, the runtime's configuration, written from `informalize.yaml` (and
  I1's `source_date_epoch`, kept while the library commit is the same), never by an
  agent; it is also the stamp section 1.3 speaks of;
* `GENERATED.json`, which lists every runtime file with its sha256 and the generator
  commit, so `sync --check` (and a person) can tell a copy that was edited or left behind.

A second sync is a no-op.  A file a previous `GENERATED.json` listed and the templates no
longer have is deleted, when it is one the runtime could have held (under `tools/`, or a
top-level name that is none of :data:`NOT_RUNTIME`); a record that lists another file is
refused.  A file under `informal/tools/` that no record lists is refused, not overwritten
or blessed (Python's `__pycache__/` aside, which the output repository ignores).  The
configuration written is first read back by the runtime's own `fccommon.load_config`, so
`cf` never writes one the tools would refuse.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from cflib import __version__
from cflib.errors import CfError
from cflib.hashing import sha256_json
from cflib.paths import templates_dir
from overlay.sync import digest, generator_commit

from informalize.outrepo import STAMP, OutputRepo

INFORMAL = "informal"
CONFIG_NAME = "informalize.json"
GENERATED_NAME = "GENERATED.json"
RUNTIME_SCHEMA = "formal-companion/informalize-runtime"
GENERATED_SCHEMA = "formal-companion/informalize-generated"
SCHEMA_VERSION = 1
GENERATOR_NAME = "formal-companion"
#: the main file `init` renders from templates/informalize/project/main.tex
MAIN = "main.tex"
#: every TeX file the marker package, the main template and the notation template load,
#: which build_pdf.sh checks with kpsewhich before the first pass (section 5.2)
TEX_PACKAGES = ["amsmath.sty", "amssymb.sty", "amsthm.sty", "keyval.sty", "zref-savepos.sty",
                "zref-abspage.sty", "lmodern.sty", "t1lmr.fd", "geometry.sty", "xcolor.sty",
                "hyperref.sty"]
#: the directory of informal/ that only the runtime writes
TOOLS = "tools"
#: the top-level files of informal/ that are not the runtime's: the stamp and the record,
#: what the project templates render, and what the stages write (section 4); sync never
#: deletes one, whatever a record lists
NOT_RUNTIME = ("informalize.json", "GENERATED.json", "main.tex", "notation.sty",
               "STYLE-project.md", "declarations.json", "items.json", "items-review.md",
               "sections.tex", "hypotheses.json", "markers.json")

assert STAMP == f"{INFORMAL}/{CONFIG_NAME}"


def runtime_root() -> Path:
    return templates_dir() / "informalize" / "runtime"


def template_files() -> dict[str, bytes]:
    """``{path relative to informal/: bytes}`` of the runtime, refusing a link."""
    root = runtime_root()
    out: dict[str, bytes] = {}
    for parent, names, files in os.walk(root):
        names[:] = sorted(n for n in names if n != "__pycache__")
        for name in sorted(files):
            path = Path(parent) / name
            if path.suffix == ".pyc":
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise CfError(f"{path} is not an ordinary file",
                              hint="the runtime is copied byte for byte; a link cannot be")
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    if not out:                                              # pragma: no cover
        raise CfError(f"{root} holds no files", hint="this is not a formal-companion checkout")
    return out


#: the module name the runtime's fccommon.py is imported under here
FCCOMMON_MODULE = "informalize_runtime_fccommon"
#: and its fcitems.py, the rules of items.json and declarations.json (section 5.4)
FCITEMS_MODULE = "informalize_runtime_fcitems"


def _tool_module(name: str, file_name: str, siblings: dict | None = None):
    """A runtime tool imported once from this checkout, without bytecode, its siblings as
    given and `sys.path` as it was.

    The flag is set around the import, since a module writes its `.pyc` while it is
    compiled, before its own first line runs; the module is registered before it runs, as
    `dataclass` looks its module up by name; a tool puts its own directory on `sys.path` to
    import a sibling, so `siblings` (``{import name: module}``) are registered under their
    import names first, when nothing holds the name, and `sys.path` is put back after.
    """
    if name in sys.modules:
        return sys.modules[name]
    path = runtime_root() / TOOLS / file_name
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:                  # pragma: no cover
        raise CfError(f"{path} cannot be imported", hint="this is not a formal-companion checkout")
    module = importlib.util.module_from_spec(spec)
    saved, saved_path = sys.dont_write_bytecode, list(sys.path)
    sys.dont_write_bytecode = True
    sys.modules[name] = module
    for alias, sibling in (siblings or {}).items():
        sys.modules.setdefault(alias, sibling)
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    finally:
        sys.dont_write_bytecode = saved
        sys.path[:] = saved_path
    return module


def fccommon():
    """The runtime's own `tools/fccommon.py`.

    Its `load_config` is the rule a written `informalize.json` must pass and its
    `DEFAULT_BOXES` the figures it holds, so neither is restated here.
    """
    return _tool_module(FCCOMMON_MODULE, "fccommon.py")


def fcitems():
    """The runtime's own `tools/fcitems.py`: the rules of items.json and declarations.json
    and the keyword check, which `informalize.itemtable` and the gate both use."""
    return _tool_module(FCITEMS_MODULE, "fcitems.py", {"fccommon": fccommon()})


def config_document(cfg: dict, previous: dict | None) -> dict:
    """`informal/informalize.json` for the settings `cfg` (section 5.2).

    `source_date_epoch` is I1's (the library commit's committer time): kept from the
    previous file while it names the same commit, else null until I1 runs again.
    """
    library = cfg["library"]
    epoch = None
    if isinstance(previous, dict) and isinstance(previous.get("library"), dict) \
            and previous["library"].get("commit") == library["commit"]:
        epoch = previous.get("source_date_epoch")
    return {
        "schema": RUNTIME_SCHEMA, "schema_version": SCHEMA_VERSION,
        "id": cfg["id"], "job": cfg["text"]["job"], "main": MAIN,
        "library": {k: library[k] for k in ("url", "commit", "module", "theorem", "prefix")},
        "source_date_epoch": epoch,
        "boxes": dict(fccommon().DEFAULT_BOXES),
        "tex_packages": list(TEX_PACKAGES),
    }


def record_source_date_epoch(repo: OutputRepo, cfg: dict, epoch: int) -> bool:
    """Write `informalize.json` with I1's `source_date_epoch`, the time of the commit `cfg`
    names (`cf informalize fetch-library`); False when it already held it."""
    document = config_document(cfg, _json(repo.read_bytes(STAMP)))
    document["source_date_epoch"] = epoch
    return repo.write(STAMP, config_bytes(document))


def config_bytes(document: dict) -> bytes:
    """The bytes of a configuration, after the runtime's own loader accepted it."""
    raw = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tool = fccommon()
    with tempfile.TemporaryDirectory(prefix="cf-informalize-") as tmp:
        Path(tmp, CONFIG_NAME).write_bytes(raw)
        try:
            tool.load_config(Path(tmp))
        except tool.ToolError as exc:                        # pragma: no cover - a bug here
            raise CfError(f"the runtime would refuse this {CONFIG_NAME}: {exc}") from exc
    return raw


def generated_document(files: dict[str, bytes], commit: str | None) -> dict:
    generator = {"name": GENERATOR_NAME, "version": __version__}
    if commit:
        generator["commit"] = commit
    return {
        "schema": GENERATED_SCHEMA, "schema_version": SCHEMA_VERSION,
        "generator": generator,
        "instance_files": [CONFIG_NAME],
        "files": [{"path": path, "sha256": digest(raw)} for path, raw in sorted(files.items())],
    }


def _json(raw: bytes | None) -> dict | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _listed(record: dict | None) -> dict[str, object] | None:
    files = record.get("files") if isinstance(record, dict) else None
    if not isinstance(files, list):
        return None
    return {row["path"]: row.get("sha256") for row in files
            if isinstance(row, dict) and isinstance(row.get("path"), str)}


def _scan(directory: Path) -> tuple[dict[str, str], list[str]]:
    """``{path relative to informal/: sha256}`` of the files under `directory`, and what
    is not an ordinary file."""
    found: dict[str, str] = {}
    odd: list[str] = []
    informal = directory if directory.name == INFORMAL else directory.parent
    if not directory.is_dir():
        return found, odd
    for parent, names, files in os.walk(directory):
        names[:] = sorted(n for n in names if n != "__pycache__")
        for name in names:
            path = Path(parent) / name
            if path.is_symlink():
                odd.append(f"{path.relative_to(informal).as_posix()}: a symlinked directory")
        for name in sorted(files):
            if name.endswith(".pyc"):
                continue
            path = Path(parent) / name
            relative = path.relative_to(informal).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                odd.append(f"{relative}: a symlink")
            elif not stat.S_ISREG(mode):
                odd.append(f"{relative}: not an ordinary file")
            else:
                found[relative] = digest(path.read_bytes())
    return found, odd


def _runtime_present(repo: OutputRepo, names: set[str]) -> tuple[dict[str, str], list[str]]:
    """The runtime's files as they are in informal/: every template path, and all of tools/."""
    informal = repo.root / INFORMAL
    found, odd = _scan(informal / TOOLS)
    for relative in sorted(names):
        path = informal / relative
        if relative.startswith(TOOLS + "/"):
            continue
        if path.is_symlink():
            odd.append(f"{relative}: a symlink")
        elif path.is_file():
            found[relative] = digest(path.read_bytes())
    return found, odd


@dataclass
class SyncResult:
    """What one sync did, by path relative to the repository."""

    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def touched(self) -> list[str]:
        return sorted(self.written + self.deleted)

    @property
    def owned(self) -> list[str]:
        """Every path of the runtime, written or not, and what it deleted: what a commit
        takes, so a sync that stopped before its commit is committed by the next one."""
        return sorted(set(self.written + self.unchanged + self.deleted))


def sync(repo: OutputRepo, cfg: dict) -> SyncResult:
    """Copy the runtime into informal/, and write informalize.json and GENERATED.json."""
    templates = template_files()
    record = _json(repo.read_bytes(f"{INFORMAL}/{GENERATED_NAME}"))
    previous = set(_listed(record) or {})
    present, odd = _runtime_present(repo, set(templates) | previous)
    if odd:
        raise CfError(f"{repo.root / INFORMAL} holds something that is not an ordinary file: "
                      f"{odd[0]}", hint="; ".join(odd[1:]) or "remove it and re-run")
    unknown = sorted(p for p in present if p.startswith(TOOLS + "/")
                     and p not in templates and p not in previous)
    if unknown:
        raise CfError(f"{INFORMAL}/{unknown[0]} is a file no {GENERATED_NAME} listed",
                      hint=f"{INFORMAL}/{TOOLS}/ is the runtime's; keep your own files outside it"
                           + (f" (and {', '.join(unknown[1:])})" if unknown[1:] else ""))
    foreign = sorted(p for p in previous - set(templates)
                     if p in present and not _runtime_shaped(p))
    if foreign:
        raise CfError(f"{INFORMAL}/{GENERATED_NAME} lists {INFORMAL}/{foreign[0]}, which is not "
                      "the runtime's to delete",
                      hint=f"remove it from {GENERATED_NAME} and re-run"
                           + (f" (and {', '.join(foreign[1:])})" if foreign[1:] else ""))
    result = SyncResult()
    for relative in sorted(previous - set(templates)):
        if relative in present:
            repo.remove(f"{INFORMAL}/{relative}")
            result.deleted.append(f"{INFORMAL}/{relative}")
    for relative, raw in sorted(templates.items()):
        target = f"{INFORMAL}/{relative}"
        (result.written if repo.write(target, raw) else result.unchanged).append(target)
    old = _json(repo.read_bytes(STAMP))
    target = STAMP
    (result.written if repo.write(target, config_bytes(config_document(cfg, old)))
     else result.unchanged).append(target)
    document = generated_document(templates, generator_commit())
    target = f"{INFORMAL}/{GENERATED_NAME}"
    raw = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if record is not None and _same_files(record, document) and repo.read_bytes(target) is not None:
        result.unchanged.append(target)        # only the generator commit would move
    elif repo.write(target, raw):
        result.written.append(target)
    else:
        result.unchanged.append(target)
    return result


def _runtime_shaped(relative: str) -> bool:
    """Could the runtime have held `relative` (a path inside informal/)?"""
    return relative.startswith(TOOLS + "/") \
        or ("/" not in relative and relative not in NOT_RUNTIME)


def _same_files(record: dict, document: dict) -> bool:
    """Do two records list the same files at the same sha256, of one schema version?"""
    return record.get("schema") == document["schema"] \
        and record.get("schema_version") == document["schema_version"] \
        and _listed(record) == _listed(document) \
        and record.get("instance_files") == document["instance_files"]


def problems(repo: OutputRepo, cfg: dict) -> list[str]:
    """`sync --check`: every missing, changed or extra runtime file, read-only."""
    informal = repo.root / INFORMAL
    if not informal.is_dir():
        return [f"{INFORMAL}/ does not exist; run `cf informalize sync {repo.id}`"]
    templates = {path: digest(raw) for path, raw in template_files().items()}
    record = _json(repo.read_bytes(f"{INFORMAL}/{GENERATED_NAME}"))
    listed = _listed(record)
    present, odd = _runtime_present(repo, set(templates) | set(listed or {}))
    out = [f"odd: {line}" for line in odd]
    out += [f"missing: {INFORMAL}/{p}" for p in sorted(set(templates) - set(present))]
    out += [f"changed: {INFORMAL}/{p}" for p in sorted(set(templates) & set(present))
            if templates[p] != present[p]]
    extra = {p for p in present if p not in templates
             and (p.startswith(TOOLS + "/") or p in (listed or {}))}
    out += [f"extra: {INFORMAL}/{p}" for p in sorted(extra)]
    if record is None:
        out.append(f"missing or unreadable: {INFORMAL}/{GENERATED_NAME}")
    else:
        if record.get("schema") != GENERATED_SCHEMA \
                or record.get("schema_version") != SCHEMA_VERSION:
            out.append(f"{INFORMAL}/{GENERATED_NAME} is not a {GENERATED_SCHEMA} record of "
                       f"version {SCHEMA_VERSION}")
        listed = listed or {}
        out += [f"{GENERATED_NAME} does not list: {p}" for p in sorted(set(templates) - set(listed))]
        out += [f"{GENERATED_NAME} lists a file the templates no longer have: {p}"
                for p in sorted(set(listed) - set(templates))]
        out += [f"{GENERATED_NAME} records the wrong sha256 for {p}"
                for p in sorted(set(templates) & set(listed)) if listed[p] != templates[p]]
    current = _json(repo.read_bytes(STAMP))
    if current is None:
        out.append(f"missing or unreadable: {STAMP}")
    elif sha256_json(current) != sha256_json(config_document(cfg, current)):
        out.append(f"{STAMP} is not what informalize.yaml gives")
    return out
