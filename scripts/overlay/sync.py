"""Write and verify a companion's generated `pdf-overlay/` directory.

New code, implementing sections 2, 5 and 6 of the overlay contract
(`docs/overlay.md`).  Three commands live here:

* **init** writes `pdf-overlay/overlay.config.json` from
  `templates/overlay/overlay.config.json.example` with the source id, the
  title and the cache file name filled in, and refuses to overwrite one.
* **sync** copies every file under `templates/overlay/runtime/` into
  `pdf-overlay/` byte for byte, adds the vendored Literature Links package as
  `pdf-overlay/tools/literature_links/`, deletes what a previous
  `GENERATED.json` listed and the templates no longer have, writes the bundle
  (every annotation file in `<companion>/annotations/` with its path, in file
  order, so the viewer reads them in one fetch) and writes `GENERATED.json`,
  which records the files' count and manifest sha256 and the bundle's sha256.
  It substitutes nothing (contract rule 2), which is why a second sync is a
  no-op and why `tools/check_generated.py` can verify the whole directory
  with the standard library alone.
* **check** is `sync --check` plus the config rules, the Literature Links scan
  of every annotation file, the curation inputs' hashes and the overlay's own
  rules.

Rule 5 of section 1 is the exception to "never write to a companion repo", and
it is narrow: these commands write under `<companion>/pdf-overlay/` and, for
`export`, `<companion>/annotations/`, and nowhere else; they refuse either
directory when it is a symlink or resolves outside the companion, refuse to
write through a symlinked component inside it, and run no git in the
companion at all.  The one git call is in THIS checkout, read-only through
`cflib.gitsafe`, to stamp the generator commit.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from cflib import __version__, gitsafe, vendored  # noqa: F401  (vendor/ on sys.path)
from cflib.errors import CfError
from cflib.paths import skill_root, templates_dir
from literature_links import discovery

from overlay import config as overlay_config
from overlay import shards as overlay_shards

__all__ = [
    "ANNOTATIONS_DIRNAME", "BUNDLE_NAME", "BUNDLE_SCHEMA", "FORMAT_PACKAGE", "GENERATED_NAME",
    "GENERATED_SCHEMA", "GENERATED_SCHEMA_VERSION", "GENERATOR_NAME", "INSTANCE_FILES",
    "LINKS_RELATIVE", "OVERLAY_DIRNAME", "SyncResult", "annotation_files", "annotations_dir",
    "bundle_bytes", "bundle_differences", "bundle_files", "check", "digest", "format_pin", "generated_document", "generated_files",
    "generator_commit", "init", "manifest_sha256", "overlay_dir", "overlay_problems",
    "record_annotations", "runtime_root", "sync", "sync_problems", "template_files",
]

#: The one directory a companion gets, and the file in it that is not copied.
OVERLAY_DIRNAME = "pdf-overlay"
GENERATED_NAME = "GENERATED.json"
INSTANCE_FILES = (overlay_config.CONFIG_NAME,)
#: The annotation directory, relative to the companion root (contract section 2).
ANNOTATIONS_DIRNAME = overlay_shards.DIRECTORY
#: The bundle `sync` writes beside `GENERATED.json`: every annotation file, for the viewer.
BUNDLE_NAME = "annotations.bundle.json"
BUNDLE_SCHEMA = "formal-companion/overlay-annotations"

GENERATED_SCHEMA = "formal-companion/overlay-generated"
GENERATED_SCHEMA_VERSION = 3
GENERATOR_NAME = "formal-companion"

#: The Literature Links release the overlay reads: the vendored package, shipped
#: whole under `pdf-overlay/tools/` so a companion's CI validates its annotations
#: with nothing but CPython.
FORMAT_PACKAGE = "literature-links"
LINKS_PACKAGE = "literature_links"
LINKS_RELATIVE = f"tools/{LINKS_PACKAGE}"
FORMAT_MANIFEST = f"{LINKS_PACKAGE}.manifest.json"

#: The example `cf overlay init` fills in.
CONFIG_EXAMPLE = "overlay/overlay.config.json.example"

_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_HTTPS = re.compile(r"https://[^\s\\]+\Z")


# ---------------------------------------------------------------------------
# the templates
# ---------------------------------------------------------------------------

def runtime_root() -> Path:
    """`templates/overlay/runtime/` in this checkout."""
    return templates_dir() / "overlay" / "runtime"


def _tree(root: Path, label: str) -> dict[str, bytes]:
    """{path relative to *root*: bytes}, refusing a link or a device node, bytecode left out."""
    out: dict[str, bytes] = {}
    for parent, names, files in os.walk(root):
        names[:] = sorted(n for n in names if n != "__pycache__")
        for name in sorted(files):
            path = Path(parent) / name
            if path.suffix == ".pyc":
                continue
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                raise CfError(f"{path} is not an ordinary file",
                              hint=f"{label} is copied verbatim; a link or a device node cannot be")
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def template_files() -> dict[str, bytes]:
    """{path relative to the runtime root: bytes}."""
    root = runtime_root()
    if not root.is_dir():                                    # pragma: no cover
        raise CfError(f"{root} is not a directory", hint="this is not a formal-companion checkout")
    out = _tree(root, "the runtime")
    if not out:                                              # pragma: no cover
        raise CfError(f"{root} holds no files")
    return out


def format_manifest() -> dict:
    """The seal on the vendored Literature Links package."""
    path = skill_root() / "vendor" / FORMAT_MANIFEST
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:  # pragma: no cover
        raise CfError(f"cannot read {path}: {exc}",
                      hint="`scripts/annotations/sync_format.py --package literature_links` writes it") from exc
    if not all(isinstance(value.get(k), str) and value[k] for k in ("version", "commit")):
        raise CfError(f"{path}: names no version or commit")  # pragma: no cover
    return value


def format_pin() -> dict:
    """`GENERATED.json`'s `format`: the Literature Links release shipped in `tools/`."""
    manifest = format_manifest()
    pin = {"package": FORMAT_PACKAGE, "version": manifest["version"], "commit": manifest["commit"]}
    if manifest.get("tag"):
        pin["tag"] = manifest["tag"]
    return pin


def generated_files() -> dict[str, bytes]:
    """Every file `sync` writes under `pdf-overlay/`: the runtime and the Literature Links package."""
    files = template_files()
    if any(p.startswith(LINKS_RELATIVE + "/") for p in files):  # pragma: no cover
        raise CfError(f"{runtime_root()} carries {LINKS_RELATIVE}/",
                      hint="the package is copied from vendor/, not from the templates")
    for rel, raw in _tree(Path(vendored.VENDOR) / LINKS_PACKAGE, "the vendored package").items():
        files[f"{LINKS_RELATIVE}/{rel}"] = raw
    return files


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def generator_commit() -> str | None:
    """This checkout's HEAD, read through `cflib.gitsafe`, or None outside a git checkout."""
    try:
        done = gitsafe.run(skill_root(), ["rev-parse", "HEAD"], timeout=30)
    except (OSError, subprocess.SubprocessError):            # pragma: no cover
        return None
    if done.returncode != 0:
        return None
    found = done.stdout.strip()
    return found if _COMMIT.fullmatch(found) else None


def empty_annotations() -> dict:
    return {"count": 0, "directory": ANNOTATIONS_DIRNAME, "extension": overlay_shards.NAMESPACE,
            "manifest_sha256": manifest_sha256({})}


def generated_document(files: dict[str, bytes], *, commit: str | None,
                       annotations: dict | None = None) -> dict:
    """`GENERATED.json` exactly as section 6 shows it."""
    generator = {"name": GENERATOR_NAME, "version": __version__}
    if commit:
        generator["commit"] = commit
    return {
        "schema": GENERATED_SCHEMA,
        "schema_version": GENERATED_SCHEMA_VERSION,
        "generator": generator,
        "format": format_pin(),
        "instance_files": list(INSTANCE_FILES),
        "files": [{"path": path, "sha256": digest(raw)} for path, raw in sorted(files.items())],
        "annotations": annotations if annotations is not None else empty_annotations(),
    }


# ---------------------------------------------------------------------------
# rule 5: where a write may land
# ---------------------------------------------------------------------------

def _companion_subdir(companion: str | Path, name: str) -> Path:
    root = Path(companion).expanduser()
    if not root.is_dir():
        raise CfError(f"{root} is not a directory",
                      hint="`cf overlay <command> <companion>` takes the companion's checkout")
    root = root.resolve()
    target = root / name
    if target.is_symlink():
        raise CfError(f"{target} is a symlink",
                      hint=f"{name}/ is written in place; a link could put the write anywhere. "
                           "Remove it and re-run.")
    if target.exists():
        if not target.is_dir():
            raise CfError(f"{target} is not a directory")
        if Path(os.path.realpath(target)) != target:
            raise CfError(f"{target} resolves outside {root}",
                          hint=f"{name}/ must be a real directory of the companion")
    return target


def overlay_dir(companion: str | Path) -> Path:
    """`<companion>/pdf-overlay`, or a refusal naming what is wrong with it."""
    return _companion_subdir(companion, OVERLAY_DIRNAME)


def annotations_dir(companion: str | Path) -> Path:
    """`<companion>/annotations`, the annotation directory, under the same refusals."""
    return _companion_subdir(companion, ANNOTATIONS_DIRNAME)


def _safe_relative(relative: str, label: str) -> PurePosixPath:
    """A path a generated file may take, or a refusal."""
    if not relative or relative != relative.strip():
        raise CfError(f"{label}: {relative!r} is not a path inside {OVERLAY_DIRNAME}/")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or str(pure) != relative \
            or any(part in {"", ".", ".."} for part in pure.parts) or "\\" in relative:
        raise CfError(f"{label}: {relative!r} is not a path inside {OVERLAY_DIRNAME}/",
                      hint="every generated path is relative, normalised and has no `..`")
    return pure


def _resolve_inside(overlay: Path, relative: str, label: str) -> Path:
    """`overlay/<relative>`, with no symlinked component on the way."""
    pure = _safe_relative(relative, label)
    current = overlay
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise CfError(f"{label}: {current} is a symlink",
                          hint="nothing under pdf-overlay/ is written through a link")
    return current


def write_file(path: Path, raw: bytes) -> None:
    """Atomically replace *path*, creating its directories, following no link."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(temporary, 0o644)          # mkstemp makes it 0600; these files are the repository's
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:                                      # pragma: no cover
            pass
        raise


def _scan(overlay: Path) -> tuple[dict[str, str], list[str]]:
    """{relative path: sha256} under *overlay*, and what is not an ordinary file."""
    found: dict[str, str] = {}
    odd: list[str] = []
    if not overlay.is_dir():
        return found, odd
    for parent, names, files in os.walk(overlay):
        names.sort()
        for name in names:
            path = Path(parent) / name
            if path.is_symlink():
                odd.append(f"{path.relative_to(overlay).as_posix()}: a symlinked directory")
        for name in sorted(files):
            path = Path(parent) / name
            relative = path.relative_to(overlay).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                odd.append(f"{relative}: a symlink")
            elif not stat.S_ISREG(mode):
                odd.append(f"{relative}: not an ordinary file")
            else:
                found[relative] = digest(path.read_bytes())
    return found, sorted(odd)


def _record(path: Path) -> dict | None:
    """A `GENERATED.json` as a dict, or None when it cannot be read."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _listed(record: dict | None) -> dict[str, object] | None:
    """{path: sha256} of a `GENERATED.json`'s `files`, or None when it cannot be read."""
    files = record.get("files") if record is not None else None
    if not isinstance(files, list):
        return None
    return {row["path"]: row.get("sha256") for row in files
            if isinstance(row, dict) and isinstance(row.get("path"), str)}


def _previous(overlay: Path) -> dict | None:
    path = overlay / GENERATED_NAME
    if not path.is_file() or path.is_symlink():
        return None
    return _record(path)


def _prune(overlay: Path, path: Path) -> None:
    """Remove the directories a deleted file leaves empty, never *overlay*."""
    parent = path.parent
    while parent != overlay and overlay in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def _dump(document: dict) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# the annotation files and the bundle
# ---------------------------------------------------------------------------

def annotation_files(companion: str | Path) -> tuple[dict[str, bytes], list[str]]:
    """``(files, problems)``: ``{path relative to the companion: bytes}`` of every annotation file.

    The directory holds ``<section>/<name>.json`` files and nothing else:
    anything else (a file outside a section folder, a nested folder, a link,
    a name the export would not write) is a problem, and so is anything the
    vendored ``literature_links`` scan reports.
    """
    directory = annotations_dir(companion)
    files: dict[str, bytes] = {}
    problems: list[str] = []
    if not directory.is_dir():
        return files, problems
    for entry in sorted(os.scandir(directory), key=lambda e: e.name):
        relative = f"{ANNOTATIONS_DIRNAME}/{entry.name}"
        if not entry.is_dir(follow_symlinks=False) or not overlay_shards.SECTION_DIR.fullmatch(entry.name):
            problems.append(f"{relative}: not a section folder; {ANNOTATIONS_DIRNAME}/ holds only "
                            "<section>/<name>.json files")
            continue
        for child in sorted(os.scandir(entry.path), key=lambda e: e.name):
            if not stat.S_ISREG(child.stat(follow_symlinks=False).st_mode) \
                    or not overlay_shards.ANNOTATION_FILE.fullmatch(child.name):
                problems.append(f"{relative}/{child.name}: not an annotation file")
                continue
            files[f"{relative}/{child.name}"] = Path(child.path).read_bytes()
    if files:
        root = str(directory.parent) + os.sep
        problems += [f"{p.path.replace(root, '')}: {p.message} [{p.code}]"
                     for p in discovery.collect([directory]).problems]
    return dict(sorted(files.items())), problems


def manifest_sha256(files: dict[str, bytes]) -> str:
    """The sha256 of ``<sha256>  <path>`` lines sorted by path: what `sha256sum` prints for the files."""
    return digest("".join(f"{digest(raw)}  {path}\n" for path, raw in sorted(files.items())).encode("utf-8"))


def bundle_bytes(files: dict[str, bytes]) -> bytes:
    """Every annotation file with its path, in file order: one line per annotation."""
    rows = [json.dumps({"annotation": json.loads(raw), "path": path}, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) for path, raw in sorted(files.items())]
    head = json.dumps({"schema": BUNDLE_SCHEMA, "schema_version": 1, "directory": ANNOTATIONS_DIRNAME,
                       "count": len(rows)}, separators=(",", ":"))[:-1]
    return (head + ',"annotations":[\n' + ",\n".join(rows) + "\n]}\n").encode("utf-8")


def bundle_files(raw: bytes) -> dict[str, bytes] | None:
    """``{path: bytes}`` of the annotation files a bundle holds, or None when it is not one."""
    try:
        rows = json.loads(raw.decode("utf-8"))["annotations"]
        return {row["path"]: overlay_shards.annotation_bytes(row["annotation"]) for row in rows}
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def bundle_differences(held: dict[str, bytes] | None, files: dict[str, bytes]) -> list[str]:
    """Each annotation file the bundle does not hold exactly, by path; empty when it holds them all."""
    if held is None:
        return [f"{BUNDLE_NAME} is not a readable bundle; run `cf overlay sync`"]
    out = [f"{path}: not in {BUNDLE_NAME}" for path in sorted(set(files) - set(held))]
    out += [f"{path}: in {BUNDLE_NAME} but not on disk" for path in sorted(set(held) - set(files))]
    out += [f"{path}: differs from {BUNDLE_NAME}" for path in sorted(set(held) & set(files))
            if held[path] != files[path]]
    return out


def _write_record(overlay: Path, document: dict) -> None:
    target = overlay / GENERATED_NAME
    if target.is_symlink():
        raise CfError(f"{target} is a symlink", hint="remove it and re-run")
    write_file(target, _dump(document))


def record_annotations(companion: str | Path, annotations: dict) -> Path:
    """Write ``annotations`` as `GENERATED.json`'s `annotations` block, keeping the rest.

    `cf overlay export` calls it with the export, its inputs and the files it
    wrote.  Before any sync there is no runtime yet, and the record lists no
    file until `cf overlay sync` copies them; a record of an older version
    keeps its file list, so the next sync still knows what it wrote.
    """
    overlay = overlay_dir(companion)
    previous = _previous(overlay)
    if previous is not None and previous.get("schema") == GENERATED_SCHEMA:
        document = dict(previous, annotations=annotations)
    else:
        document = generated_document({}, commit=generator_commit(), annotations=annotations)
    overlay.mkdir(exist_ok=True)
    _write_record(overlay, document)
    return overlay / GENERATED_NAME


def _annotations_for_sync(companion: str | Path, previous: dict | None) -> tuple[dict, bytes]:
    """The `annotations` block a sync writes, and the bundle: the files on disk, the export's facts kept.

    Files `cf overlay export` wrote are its output: a set that changed since
    is refused here rather than recorded, so a hand edit cannot be blessed by
    a sync and then pass the companion's CI.
    """
    old = (previous or {}).get("annotations")
    old = old if isinstance(old, dict) else {}
    files, problems = annotation_files(companion)
    if problems:
        raise CfError(problems[0], hint="; ".join(problems[1:4]) or
                      "fix or re-export the annotations; `cf overlay export` writes them")
    block = {k: v for k, v in old.items() if k in ("export", "inputs")}
    if "export" in block and (old.get("count"), old.get("manifest_sha256")) != (len(files), manifest_sha256(files)):
        # The last bundle names the file when it holds what the export wrote.
        bundle = overlay_dir(companion) / BUNDLE_NAME
        held = bundle_files(bundle.read_bytes()) if bundle.is_file() else None
        named = bundle_differences(held, files) \
            if held is not None and manifest_sha256(held) == old.get("manifest_sha256") else []
        raise CfError(f"{named[0] if named else ANNOTATIONS_DIRNAME + '/'}: not what `cf overlay export` wrote",
                      hint="re-run `cf overlay export`; exported annotations are not edited by hand")
    bundle = bundle_bytes(files)
    block.update(count=len(files), manifest_sha256=manifest_sha256(files),
                 bundle={"path": BUNDLE_NAME, "sha256": digest(bundle)})
    return dict(sorted({**empty_annotations(), **block}.items())), bundle


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def init(companion: str | Path, *, source_id: str, title: str,
         file_name: str | None = None, force: bool = False) -> Path:
    """Write `pdf-overlay/overlay.config.json` from the shipped example."""
    overlay = overlay_dir(companion)
    path = overlay / overlay_config.CONFIG_NAME
    if path.is_symlink():
        raise CfError(f"{path} is a symlink", hint="remove it and re-run")
    if path.exists() and not force:
        raise CfError(f"{path} already exists",
                      hint="it is edited by the companion after `init`; pass --force to "
                           "overwrite it with the template again", exit_code=1)
    name = file_name or f"{source_id}.pdf"
    if not re.fullmatch(overlay_config.FILE_NAME_PATTERN, name):
        raise CfError(f"{name!r} is not one plain file name",
                      hint="pass --file-name, e.g. --file-name notes.pdf")
    example = templates_dir() / CONFIG_EXAMPLE
    try:
        value = json.loads(example.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:           # pragma: no cover
        raise CfError(f"cannot read {example}: {exc}") from exc
    value["source_id"] = source_id
    value["title"] = title
    value["pdf"]["file_name"] = name
    value["pdf"]["route"] = overlay_config.route_for(name)
    overlay_config.parse(value)          # never write a config `check` would refuse
    write_file(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return path


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    """What one `cf overlay sync` did."""

    overlay: Path
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.written or self.deleted)


def sync(companion: str | Path) -> SyncResult:
    """Copy the runtime into `pdf-overlay/`, write the bundle, and write `GENERATED.json`."""
    overlay = overlay_dir(companion)
    generated = generated_files()
    record = _previous(overlay)
    previous = sorted(_listed(record) or {})
    present, odd = _scan(overlay)
    if odd:
        raise CfError(f"{overlay} holds something that is not an ordinary file: {odd[0]}",
                      hint="; ".join(odd[1:]) or "remove it and re-run")

    known = set(generated) | set(previous) | set(INSTANCE_FILES) | {GENERATED_NAME, BUNDLE_NAME}
    unexpected = sorted(set(present) - known)
    if unexpected:
        rest = (f"the other {len(unexpected) - 1}: {', '.join(unexpected[1:])}"
                if len(unexpected) > 1 else
                "pdf-overlay/ is generated; keep your own files outside it")
        raise CfError(f"{overlay} holds a file no previous GENERATED.json listed: "
                      f"{unexpected[0]}", hint=rest)
    annotations, bundle = _annotations_for_sync(companion, record)

    result = SyncResult(overlay=overlay)
    for relative in sorted(set(previous) - set(generated) - set(INSTANCE_FILES)):
        if relative in (GENERATED_NAME, BUNDLE_NAME) or relative not in present:
            continue
        path = _resolve_inside(overlay, relative, f"the previous {GENERATED_NAME}")
        path.unlink()
        _prune(overlay, path)
        result.deleted.append(relative)

    for relative, raw in sorted(generated.items()):
        path = _resolve_inside(overlay, relative, "the runtime")
        if present.get(relative) == digest(raw):
            result.unchanged.append(relative)
            continue
        write_file(path, raw)
        result.written.append(relative)
    if present.get(BUNDLE_NAME) == digest(bundle):
        result.unchanged.append(BUNDLE_NAME)
    else:
        write_file(_resolve_inside(overlay, BUNDLE_NAME, "the bundle"), bundle)
        result.written.append(BUNDLE_NAME)

    document = generated_document(generated, commit=generator_commit(), annotations=annotations)
    target = overlay / GENERATED_NAME
    if target.is_file() and not target.is_symlink() and target.read_bytes() == _dump(document):
        result.unchanged.append(GENERATED_NAME)
    else:
        _write_record(overlay, document)
        result.written.append(GENERATED_NAME)
    return result


def sync_problems(companion: str | Path) -> list[str]:
    """`sync --check`: every missing, changed or extra file and annotation file, read-only."""
    overlay = overlay_dir(companion)
    if not overlay.is_dir():
        return [f"{OVERLAY_DIRNAME}/ does not exist; run `cf overlay sync`"]
    expected = {path: digest(raw) for path, raw in generated_files().items()}
    present, odd = _scan(overlay)
    out = [f"odd: {line}" for line in odd]
    for relative in sorted(set(expected) - set(present)):
        out.append(f"missing: {relative}")
    for relative in sorted(set(expected) & set(present)):
        if expected[relative] != present[relative]:
            out.append(f"changed: {relative}")
    for relative in sorted(set(present) - set(expected) - set(INSTANCE_FILES) - {GENERATED_NAME, BUNDLE_NAME}):
        out.append(f"extra: {relative}")

    path = overlay / GENERATED_NAME
    if not path.is_file():
        return out + [f"missing: {GENERATED_NAME}"]
    record = _record(path)
    listed = _listed(record)
    if listed is None or record is None:
        return out + [f"{GENERATED_NAME} is not a readable generation record"]
    if record.get("schema_version") != GENERATED_SCHEMA_VERSION:
        out.append(f"{GENERATED_NAME}.schema_version: expected {GENERATED_SCHEMA_VERSION}; "
                   "run `cf overlay sync`")
    pin = format_pin()
    if record.get("format") != pin:
        out.append(f"{GENERATED_NAME}.format: expected the vendored release "
                   f"{json.dumps(pin, sort_keys=True)}, got "
                   f"{json.dumps(record.get('format'), sort_keys=True, default=str)}")
    for relative in sorted(set(expected) - set(listed)):
        out.append(f"{GENERATED_NAME} does not list: {relative}")
    for relative in sorted(set(listed) - set(expected)):
        out.append(f"{GENERATED_NAME} lists a file the templates no longer have: {relative}")
    for relative in sorted(set(expected) & set(listed)):
        if listed[relative] != expected[relative]:
            out.append(f"{GENERATED_NAME} records the wrong sha256 for {relative}")

    block = record.get("annotations") if isinstance(record.get("annotations"), dict) else {}
    files, _problems = annotation_files(companion)
    if (block.get("count"), block.get("manifest_sha256")) != (len(files), manifest_sha256(files)):
        out.append(f"changed annotations: {ANNOTATIONS_DIRNAME}/ holds {len(files)} files, not the "
                   f"{block.get('count')} {GENERATED_NAME} records by manifest sha256")
    bundle = overlay / BUNDLE_NAME
    recorded = (block.get("bundle") or {}).get("sha256") if isinstance(block.get("bundle"), dict) else None
    if not bundle.is_file():
        out.append(f"missing: {BUNDLE_NAME}")
    else:
        raw = bundle.read_bytes()
        if digest(raw) != recorded:
            out.append(f"changed: {BUNDLE_NAME}")
        out += bundle_differences(bundle_files(raw), files)
    return out


# ---------------------------------------------------------------------------
# check: the config, the annotations, the inputs and the overlay's own rules
# ---------------------------------------------------------------------------

def target_problems(path: str, record: dict) -> list[str]:
    """A target the viewer can open: an `https:` URL, or a relative reference that stays in the site."""
    target = record.get("target")
    uri = target.get("uri") if isinstance(target, dict) else target
    if uri is None or _HTTPS.fullmatch(uri):
        return []
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", uri) or uri.startswith(("/", "\\")):
        return [f"{path}: {record.get('id')!r}: target {uri!r} is neither https: nor a relative reference"]
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(path), uri.split("#", 1)[0]))
    if joined == ".." or joined.startswith("../"):
        return [f"{path}: {record.get('id')!r}: target {uri!r} climbs out of the site"]
    return []


def overlay_problems(companion: str | Path, source_id: str) -> list[str]:
    """The overlay's rules beyond Literature Links: one PDF the config names, its pages, the targets."""
    root = Path(companion).expanduser().resolve()
    out: list[str] = []
    sources: dict[str, dict] = {}
    files, _problems = annotation_files(companion)
    for path, raw in files.items():
        record = json.loads(raw)
        source = record.get("source") or {}
        ext = (source.get("extensions") or {}).get(overlay_shards.NAMESPACE) or {}
        if source.get("type") != "pdf" or ext.get("source_id") != source_id:
            continue
        sources.setdefault(json.dumps(source, sort_keys=True), source)
        count = ext.get("physical_page_count")
        page = (record.get("location") or {}).get("page")
        if isinstance(count, int) and isinstance(page, int) and page > count:
            out.append(f"{path}: {record.get('id')!r} is on page {page}, past the "
                       f"source's {count} pages")
        out += target_problems(path, record)
    if not sources:
        out.append(f"no annotation is a record of the PDF {source_id!r}, which "
                   f"{overlay_config.CONFIG_NAME} names")
    elif len(sources) > 1:
        out.append(f"the records of {source_id!r} carry {len(sources)} different sources; "
                   "the overlay shows one PDF")
    elif not re.fullmatch(r"[0-9a-f]{64}", str((next(iter(sources.values())).get("pin") or {}).get("sha256"))):
        out.append(f"the source {source_id!r} pins no sha256, which the viewer verifies the PDF by")
    return out


def inputs_problems(companion: str | Path, record: dict) -> list[str]:
    """Every curation input the export recorded still has its recorded sha256; an export records one."""
    root = Path(companion).expanduser().resolve()
    block = record.get("annotations") or {}
    if "export" in block and not block.get("inputs"):
        return [f"{GENERATED_NAME}.annotations.inputs: the export records no curation input, so a stale "
                "export cannot be told; re-run `cf overlay export`"]
    out = []
    for row in block.get("inputs") or []:
        path = root / row["path"]
        if not path.is_file():
            out.append(f"{row['path']}: gone since the annotations were exported from it; "
                       "re-run `cf overlay export`")
        elif digest(path.read_bytes()) != row["sha256"]:
            out.append(f"{row['path']}: changed since the annotations were exported from it; "
                       "re-run `cf overlay export` and `cf overlay sync`")
    return out


def check(companion: str | Path) -> list[str]:
    """Every problem `cf overlay check` reports, one line each."""
    overlay = overlay_dir(companion)
    out = sync_problems(companion)
    files, problems = annotation_files(companion)
    out += problems
    if not files:
        out.append(f"{ANNOTATIONS_DIRNAME}/ holds no annotation file; `cf overlay export` writes them")
    out += inputs_problems(companion, _previous(overlay) or {})
    config_path = overlay / overlay_config.CONFIG_NAME
    found = overlay_config.load_problems(config_path)
    out += found
    if found or problems or not files:
        return out
    return out + overlay_problems(companion, overlay_config.load(config_path).source_id)
