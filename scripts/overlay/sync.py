"""Write and verify a companion's generated `pdf-overlay/` directory.

New code, implementing sections 2, 5 and 6 of the overlay contract
(`docs/overlay.md`).  Three commands live here:

* **init** writes `pdf-overlay/overlay.config.json` from
  `templates/overlay/overlay.config.json.example` with the source id, the
  title and the cache file name filled in, and refuses to overwrite one.
* **sync** copies every file under `templates/overlay/runtime/` into
  `pdf-overlay/` byte for byte, deletes what a previous `GENERATED.json`
  listed and the templates no longer have, and writes `GENERATED.json`.  It
  substitutes nothing: a template that needs a per-companion value reads it at
  run time (contract rule 2), which is why a second sync is a no-op and why
  `tools/check_generated.py` can verify the whole directory with the standard
  library alone.
* **check** is `sync --check` plus the config rules, the annotation format's
  own checker (schema, invariants, canonical bytes) and the three overlay
  rules the viewer depends on.

Rule 5 of section 1 is the exception to "never write to a companion repo", and
it is narrow: these commands write under `<companion>/pdf-overlay/` and nowhere
else, they refuse a `pdf-overlay` that is a symlink or resolves outside the
companion, they refuse to write through a symlinked component inside it, and
they run no git in the companion at all.  The one git call is in THIS checkout,
read-only through `cflib.gitsafe`, to stamp the generator commit.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from cflib import __version__, gitsafe, vendored  # noqa: F401  (vendor/ on sys.path)
from cflib.errors import CfError
from cflib.paths import skill_root, templates_dir
from formal_companion_annotations import validate as V
from formal_companion_annotations import vocab

from overlay import config as overlay_config

__all__ = [
    "DOCUMENT_NAME", "GENERATED_NAME", "GENERATED_SCHEMA", "GENERATED_SCHEMA_VERSION",
    "GENERATOR_NAME", "INSTANCE_FILES", "OVERLAY_DIRNAME", "SyncResult", "check",
    "digest", "document_problems", "generated_document", "generator_commit", "init",
    "overlay_dir", "overlay_problems", "runtime_root", "safe_https_url",
    "safe_site_path", "sync", "sync_problems", "template_files",
]

#: The one directory a companion gets, and the three files that are not copied.
OVERLAY_DIRNAME = "pdf-overlay"
GENERATED_NAME = "GENERATED.json"
DOCUMENT_NAME = "annotations.json"
#: Sorted, because `GENERATED.json` carries the list verbatim.
INSTANCE_FILES = (DOCUMENT_NAME, overlay_config.CONFIG_NAME)

GENERATED_SCHEMA = "formal-companion/overlay-generated"
GENERATED_SCHEMA_VERSION = 1
GENERATOR_NAME = "formal-companion"

#: The example `cf overlay init` fills in.
CONFIG_EXAMPLE = "overlay/overlay.config.json.example"

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
#: The viewer's own two href rules, as `viewer-core.js` spells them.  A scheme
#: needs no pattern of its own here: a relative site path may hold no colon.
_HTTPS = re.compile(r"https://[^\s#\\]+\Z")
_DOTDOT = re.compile(r"(^|/)\.\.(/|$)")


# ---------------------------------------------------------------------------
# the templates
# ---------------------------------------------------------------------------

def runtime_root() -> Path:
    """`templates/overlay/runtime/` in this checkout."""
    return templates_dir() / "overlay" / "runtime"


def template_files() -> dict[str, bytes]:
    """{path relative to the runtime root: bytes}, refusing anything odd.

    A symlink or a device node in the templates would be copied into somebody
    else's repository, so it stops the command instead.
    """
    root = runtime_root()
    if not root.is_dir():                                    # pragma: no cover
        raise CfError(f"{root} is not a directory",
                      hint="this is not a formal-companion checkout")
    out: dict[str, bytes] = {}
    for parent, names, files in os.walk(root):
        names.sort()
        for name in sorted(files):
            path = Path(parent) / name
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                raise CfError(f"{path} is not an ordinary file",
                              hint="the runtime is copied verbatim; a link or a "
                                   "device node cannot be")
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    if not out:                                              # pragma: no cover
        raise CfError(f"{root} holds no files")
    return out


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def generator_commit() -> str | None:
    """This checkout's HEAD, read through `cflib.gitsafe`, or None.

    None when the skill is not a git checkout (a tarball install), which is a
    fact about the generator, not a failure: `GENERATED.json` then carries no
    `commit` and `check_generated.py` does not ask for one.
    """
    try:
        done = gitsafe.run(skill_root(), ["rev-parse", "HEAD"], timeout=30)
    except (OSError, subprocess.SubprocessError):            # pragma: no cover
        return None
    if done.returncode != 0:
        return None
    found = done.stdout.strip()
    return found if _COMMIT.fullmatch(found) else None


def generated_document(files: dict[str, bytes], *, commit: str | None) -> dict:
    """`GENERATED.json` exactly as section 6 shows it."""
    generator = {"name": GENERATOR_NAME, "version": __version__}
    if commit:
        generator["commit"] = commit
    return {
        "schema": GENERATED_SCHEMA,
        "schema_version": GENERATED_SCHEMA_VERSION,
        "generator": generator,
        "instance_files": list(INSTANCE_FILES),
        "files": [{"path": path, "sha256": digest(raw)}
                  for path, raw in sorted(files.items())],
    }


# ---------------------------------------------------------------------------
# rule 5: where a write may land
# ---------------------------------------------------------------------------

def overlay_dir(companion: str | Path) -> Path:
    """`<companion>/pdf-overlay`, or a refusal naming what is wrong with it.

    The whole of the write exception rests on this: the directory is resolved
    against the companion's own resolved path, a symlink there is refused
    rather than followed, and nothing outside it is ever opened for writing.
    """
    root = Path(companion).expanduser()
    if not root.is_dir():
        raise CfError(f"{root} is not a directory",
                      hint="`cf overlay <command> <companion>` takes the companion's checkout")
    root = root.resolve()
    target = root / OVERLAY_DIRNAME
    if target.is_symlink():
        raise CfError(f"{target} is a symlink",
                      hint="pdf-overlay/ is generated in place; a link could put the "
                           "write anywhere. Remove it and re-run.")
    if target.exists():
        if not target.is_dir():
            raise CfError(f"{target} is not a directory")
        if Path(os.path.realpath(target)) != target:
            raise CfError(f"{target} resolves outside {root}",
                          hint="pdf-overlay/ must be a real directory of the companion")
    return target


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


def _write(path: Path, raw: bytes) -> None:
    """Atomically replace *path*, creating its directories, following no link."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(parent), prefix=f".{path.name}.",
                                         suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:                                      # pragma: no cover
            pass
        raise


def _scan(overlay: Path) -> tuple[dict[str, str], list[str]]:
    """{relative path: sha256} under *overlay*, and what is not an ordinary file.

    ``os.walk`` does not follow a symlinked directory, so one is reported here
    and never descended into.
    """
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


def _previous(overlay: Path) -> list[str]:
    """The paths the last `GENERATED.json` listed, or an empty list.

    An unreadable record is read as "nothing was generated here", which makes
    `sync` refuse the files it finds rather than delete them.
    """
    path = overlay / GENERATED_NAME
    if not path.is_file() or path.is_symlink():
        return []
    return sorted(_listed(path) or {})


def _prune(overlay: Path, path: Path) -> None:
    """Remove the directories a deleted file leaves empty, never *overlay*."""
    parent = path.parent
    while parent != overlay and overlay in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


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
    _write(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
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
    """Copy the runtime into `pdf-overlay/` and write `GENERATED.json`."""
    overlay = overlay_dir(companion)
    templates = template_files()
    previous = _previous(overlay)
    present, odd = _scan(overlay)
    if odd:
        raise CfError(f"{overlay} holds something that is not an ordinary file: {odd[0]}",
                      hint="; ".join(odd[1:]) or "remove it and re-run")

    known = set(templates) | set(previous) | set(INSTANCE_FILES) | {GENERATED_NAME}
    unexpected = sorted(set(present) - known)
    if unexpected:
        rest = (f"the other {len(unexpected) - 1}: {', '.join(unexpected[1:])}"
                if len(unexpected) > 1 else
                "pdf-overlay/ is generated; keep your own files outside it")
        raise CfError(f"{overlay} holds a file no previous GENERATED.json listed: "
                      f"{unexpected[0]}", hint=rest)

    result = SyncResult(overlay=overlay)
    for relative in sorted(set(previous) - set(templates) - set(INSTANCE_FILES)):
        if relative == GENERATED_NAME or relative not in present:
            continue
        path = _resolve_inside(overlay, relative, f"the previous {GENERATED_NAME}")
        path.unlink()
        _prune(overlay, path)
        result.deleted.append(relative)

    for relative, raw in sorted(templates.items()):
        path = _resolve_inside(overlay, relative, "the runtime")
        if present.get(relative) == digest(raw):
            result.unchanged.append(relative)
            continue
        _write(path, raw)
        result.written.append(relative)

    document = generated_document(templates, commit=generator_commit())
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    target = overlay / GENERATED_NAME
    if target.is_symlink():
        raise CfError(f"{target} is a symlink", hint="remove it and re-run")
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        _write(target, text.encode("utf-8"))
        result.written.append(GENERATED_NAME)
    else:
        result.unchanged.append(GENERATED_NAME)
    return result


def sync_problems(companion: str | Path) -> list[str]:
    """`sync --check`: every missing, changed or extra file, read-only."""
    overlay = overlay_dir(companion)
    if not overlay.is_dir():
        return [f"{OVERLAY_DIRNAME}/ does not exist; run `cf overlay sync`"]
    templates = template_files()
    expected = {path: digest(raw) for path, raw in templates.items()}
    present, odd = _scan(overlay)
    out = [f"odd: {line}" for line in odd]
    for relative in sorted(set(expected) - set(present)):
        out.append(f"missing: {relative}")
    for relative in sorted(set(expected) & set(present)):
        if expected[relative] != present[relative]:
            out.append(f"changed: {relative}")
    for relative in sorted(set(present) - set(expected) - set(INSTANCE_FILES)
                           - {GENERATED_NAME}):
        out.append(f"extra: {relative}")

    path = overlay / GENERATED_NAME
    if not path.is_file():
        out.append(f"missing: {GENERATED_NAME}")
        return out
    listed = _listed(path)
    if listed is None:
        out.append(f"{GENERATED_NAME} is not a readable generation record")
        return out
    for relative in sorted(set(expected) - set(listed)):
        out.append(f"{GENERATED_NAME} does not list: {relative}")
    for relative in sorted(set(listed) - set(expected)):
        out.append(f"{GENERATED_NAME} lists a file the templates no longer have: "
                   f"{relative}")
    for relative in sorted(set(expected) & set(listed)):
        if listed[relative] != expected[relative]:
            out.append(f"{GENERATED_NAME} records the wrong sha256 for {relative}")
    return out


def _listed(path: Path) -> dict[str, object] | None:
    """{path: sha256} from a `GENERATED.json`, or None when it cannot be read."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    files = value.get("files") if isinstance(value, dict) else None
    if not isinstance(files, list):
        return None
    return {row["path"]: row.get("sha256") for row in files
            if isinstance(row, dict) and isinstance(row.get("path"), str)}


# ---------------------------------------------------------------------------
# check: the config, the document and the three overlay rules
# ---------------------------------------------------------------------------

def safe_https_url(value: object) -> str | None:
    """The viewer's `safeHttpsUrl`: an `https:` URL with no fragment of its own."""
    return value if isinstance(value, str) and _HTTPS.fullmatch(value) else None


def safe_site_path(value: object) -> str | None:
    """The viewer's `safeSitePath`: the relative-href grammar of format section 3.6."""
    if not isinstance(value, str) or not value:
        return None
    if re.search(r"[\s#:]", value) or "\\" in value:
        return None
    if value.startswith("/") or _DOTDOT.search(value):
        return None
    return value


def _selectors(node: object):
    """Every selector in a `target.selector` tree, refinements and ends included."""
    if isinstance(node, list):
        for item in node:
            yield from _selectors(item)
    elif isinstance(node, dict):
        yield node
        for key in ("refinedBy", "startSelector", "endSelector"):
            if key in node:
                yield from _selectors(node[key])


def overlay_problems(doc: dict, source_id: str) -> list[str]:
    """The three rules the viewer depends on, beyond the format's own checker."""
    out: list[str] = []
    sources = doc.get("sources") if isinstance(doc.get("sources"), list) else []
    named = [row for row in sources if isinstance(row, dict) and row.get("id") == source_id]
    if not named:
        return [f"{DOCUMENT_NAME}: no source {source_id!r}, which "
                f"{overlay_config.CONFIG_NAME} names"]
    source = named[0]
    if source.get("kind") != "pdf":
        out.append(f"{DOCUMENT_NAME}: source {source_id!r} is kind "
                   f"{source.get('kind')!r}, not pdf")
    if not isinstance(source.get("sha256"), str) or not _HEX64.fullmatch(source["sha256"]):
        out.append(f"{DOCUMENT_NAME}: source {source_id!r} carries no lowercase sha256")
    pages = source.get("physical_page_count")
    if not isinstance(pages, int) or isinstance(pages, bool) or pages < 1:
        out.append(f"{DOCUMENT_NAME}: source {source_id!r} carries no "
                   f"physical_page_count, which the viewer pages with")
        pages = None

    for location in doc.get("locations") or []:
        if not isinstance(location, dict):
            continue
        target = location.get("target")
        if not isinstance(target, dict) or target.get("source") != source_id:
            continue
        for selector in _selectors(target.get("selector")):
            if selector.get("type") != "PdfRegionSelector":
                continue
            page = selector.get("page")
            if pages is not None and isinstance(page, int) and not 1 <= page <= pages:
                out.append(f"{DOCUMENT_NAME}: location {location.get('id')!r} has a "
                           f"region on page {page}, past the source's {pages} pages")

    for destination in doc.get("destinations") or []:
        if not isinstance(destination, dict):
            continue
        href = destination.get("href")
        if safe_https_url(href) is None and safe_site_path(href) is None:
            out.append(f"{DOCUMENT_NAME}: destination {destination.get('id')!r} has an "
                       f"href the viewer will not link to: {href!r}")
    return out


def document_problems(overlay: Path, source_id: str) -> list[str]:
    """The format's own checker, canonical bytes included, then the overlay rules."""
    path = overlay / DOCUMENT_NAME
    if not path.is_file() or path.is_symlink():
        return [f"{DOCUMENT_NAME}: not an ordinary file in {overlay}; "
                f"`cf overlay export` writes it"]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{DOCUMENT_NAME}: cannot read it: {exc}"]
    try:
        doc = V.loads(text)
    except V.LoadError as exc:
        return [f"{DOCUMENT_NAME}: {exc}"]
    if not isinstance(doc, dict):
        return [f"{DOCUMENT_NAME}: expected a JSON object"]
    if doc.get("schema") != vocab.ANNOTATION_SCHEMA:
        return [f"{DOCUMENT_NAME}.schema: expected {vocab.ANNOTATION_SCHEMA}, got "
                f"{doc.get('schema')!r}"]
    report = V.check(doc, text=text, canonical=True)
    out = [f"{DOCUMENT_NAME}: {problem}" for problem in report.problems]
    if out:
        return out
    return overlay_problems(doc, source_id)


def check(companion: str | Path) -> list[str]:
    """Every problem `cf overlay check` reports, one line each."""
    overlay = overlay_dir(companion)
    out = sync_problems(companion)
    config_path = overlay / overlay_config.CONFIG_NAME
    found = overlay_config.load_problems(config_path)
    out += found
    if found:
        return out                   # without a source_id there is nothing to check against
    source_id = overlay_config.load(config_path).source_id
    return out + document_problems(overlay, source_id)
