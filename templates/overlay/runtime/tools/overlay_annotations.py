"""Read the Literature Links annotations a generated overlay shows.

Section 2 of the overlay contract (`docs/overlay.md` in the formal-companion
skill).  The annotations are JSON files, one annotation per file, in one
folder per book section under the directory `GENERATED.json`'s `annotations`
block names (relative to the companion root).  The block records their count
and manifest sha256, and the sha256 of the bundle `cf overlay sync` writes
beside it: every annotation file with its path, in file order, which is what
the viewer reads, in one fetch, because a browser cannot list a directory.
`serve.py`, `build_site.py` and `check_generated.py` read them through this
module, with the standard library alone.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

GENERATED_NAME = "GENERATED.json"
#: The extension key the overlay's records carry its own facts under.
NAMESPACE = "org.mathlib-initiative.formal-companion.overlay"
BUNDLE_SCHEMA = "formal-companion/overlay-annotations"
#: A section folder, and an annotation file in it.
SECTION_DIR = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*\Z")
ANNOTATION_FILE = re.compile(r"[0-9A-Za-z_-][0-9A-Za-z._-]{0,114}\.json\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
#: The bundle sits beside `GENERATED.json`.
BUNDLE_FILE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*\.json\Z")


class AnnotationError(Exception):
    """The annotations or their record cannot be read; the message says which."""


def strict_json(text: str, label: str) -> Any:
    """JSON with no duplicate keys and no `NaN`/`Infinity`."""
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise AnnotationError(f"{label}: duplicate JSON key {key!r}")
            out[key] = value
        return out

    def constant(token: str) -> Any:
        raise AnnotationError(f"{label}: non-standard JSON constant {token}")

    try:
        return json.loads(text, object_pairs_hook=unique, parse_constant=constant)
    except json.JSONDecodeError as error:
        raise AnnotationError(f"{label}: invalid JSON: {error}") from error


def safe_relative(value: Any) -> bool:
    """A normalised, traversal-free relative POSIX path."""
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    pure = PurePosixPath(value)
    return (not pure.is_absolute() and str(pure) == value
            and not any(part in {"", ".", ".."} for part in pure.parts))


def read_regular(path: Path, label: str) -> bytes:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise AnnotationError(f"{label}: {path} does not exist") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise AnnotationError(f"{label}: expected an ordinary nonsymlink file")
    return path.read_bytes()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def annotation_bytes(annotation: Any) -> bytes:
    """An annotation file's exact bytes: sorted keys, two-space indent, UTF-8, a final newline."""
    return (json.dumps(annotation, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
            + "\n").encode("utf-8")


def manifest_sha256(files: dict[str, bytes]) -> str:
    """The sha256 of ``<sha256>  <path>`` lines sorted by path: what `sha256sum` prints for the files."""
    return sha256("".join(f"{sha256(raw)}  {path}\n" for path, raw in sorted(files.items())).encode("utf-8"))


def annotations_block(overlay: Path) -> dict[str, Any]:
    """`GENERATED.json`'s `annotations` block, its paths and hashes checked."""
    try:
        record = strict_json(read_regular(overlay / GENERATED_NAME, GENERATED_NAME).decode("utf-8"),
                             GENERATED_NAME)
    except UnicodeDecodeError as error:
        raise AnnotationError(f"{GENERATED_NAME}: not UTF-8") from error
    block = record.get("annotations") if isinstance(record, dict) else None
    label = f"{GENERATED_NAME}.annotations"
    if not isinstance(block, dict):
        raise AnnotationError(f"{label}: expected an object; `cf overlay export` and `cf overlay sync` write it")
    if not safe_relative(block.get("directory")):
        raise AnnotationError(f"{label}.directory: expected a relative path")
    count = block.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise AnnotationError(f"{label}.count: expected the number of annotation files")
    if not isinstance(block.get("manifest_sha256"), str) or not HEX64.match(block["manifest_sha256"]):
        raise AnnotationError(f"{label}.manifest_sha256: expected a lowercase sha256")
    bundle = block.get("bundle")
    if not (isinstance(bundle, dict) and isinstance(bundle.get("path"), str) and BUNDLE_FILE.match(bundle["path"])
            and isinstance(bundle.get("sha256"), str) and HEX64.match(bundle["sha256"])):
        raise AnnotationError(f"{label}.bundle: expected {{path, sha256}}; `cf overlay sync` writes it")
    return block


def annotation_path(value: Any, directory: str) -> bool:
    """``<directory>/<section>/<name>.json``."""
    if not safe_relative(value) or not value.startswith(directory + "/"):
        return False
    parts = PurePosixPath(value[len(directory) + 1:]).parts
    return len(parts) == 2 and bool(SECTION_DIR.match(parts[0])) and bool(ANNOTATION_FILE.match(parts[1]))


def read_bundle(overlay: Path, block: dict[str, Any] | None = None) -> list[tuple[str, dict[str, Any]]]:
    """``[(path, annotation)]`` from the bundle, in file order, at its recorded sha256."""
    block = annotations_block(overlay) if block is None else block
    path = block["bundle"]["path"]
    raw = read_regular(overlay / path, path)
    if sha256(raw) != block["bundle"]["sha256"]:
        raise AnnotationError(f"{path}: sha256 {sha256(raw)}, but {GENERATED_NAME} records "
                              f"{block['bundle']['sha256']}; run `cf overlay sync`")
    try:
        value = strict_json(raw.decode("utf-8"), path)
    except UnicodeDecodeError as error:
        raise AnnotationError(f"{path}: not UTF-8") from error
    rows = value.get("annotations") if isinstance(value, dict) else None
    if not isinstance(value, dict) or value.get("schema") != BUNDLE_SCHEMA or not isinstance(rows, list) \
            or value.get("directory") != block["directory"]:
        raise AnnotationError(f"{path}: expected a {BUNDLE_SCHEMA} bundle of {block['directory']}/")
    out: list[tuple[str, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        where = row.get("path") if isinstance(row, dict) else None
        if not (annotation_path(where, block["directory"]) and isinstance(row.get("annotation"), dict)):
            raise AnnotationError(f"{path}: annotations[{index}] is not {{path, annotation}}")
        if out and where <= out[-1][0]:
            raise AnnotationError(f"{path}: annotations[{index}] is out of file order")
        out.append((where, row["annotation"]))
    if len(out) != value.get("count") or len(out) != block["count"]:
        raise AnnotationError(f"{path}: {len(out)} annotations, but {GENERATED_NAME} records {block['count']}")
    return out


def disk_files(root: Path, directory: str) -> tuple[dict[str, bytes], list[str]]:
    """``({path: bytes}, problems)``: the annotation files under ``root/directory``, and anything else there."""
    base = root.joinpath(*PurePosixPath(directory).parts)
    files: dict[str, bytes] = {}
    problems: list[str] = []
    for parent, names, children in os.walk(base):
        names.sort()
        here = PurePosixPath(directory, Path(parent).relative_to(base).as_posix())
        for name in list(names):
            if os.path.islink(os.path.join(parent, name)) or here != PurePosixPath(directory) \
                    or not SECTION_DIR.match(name):
                problems.append(f"{here}/{name}: not a section folder; {directory}/ holds only "
                                f"<section>/<name>.json annotation files")
                names.remove(name)
        for name in sorted(children):
            path = Path(parent) / name
            if here == PurePosixPath(directory) or not ANNOTATION_FILE.match(name) \
                    or not stat.S_ISREG(path.lstat().st_mode):
                problems.append(f"{here}/{name}: not an annotation file; {directory}/ holds only "
                                f"<section>/<name>.json annotation files")
                continue
            files[f"{here}/{name}"] = path.read_bytes()
    return dict(sorted(files.items())), problems


def bundle_problems(entries: list[tuple[str, dict[str, Any]]], files: dict[str, bytes]) -> list[str]:
    """Each annotation file the bundle does not hold exactly, named."""
    held = {path: annotation_bytes(annotation) for path, annotation in entries}
    out = [f"{path}: not in the bundle; run `cf overlay sync`" for path in sorted(set(files) - set(held))]
    out += [f"{path}: missing, but the bundle holds it" for path in sorted(set(held) - set(files))]
    out += [f"{path}: differs from the bundle" for path in sorted(set(held) & set(files))
            if held[path] != files[path]]
    return out


def extension(node: Any) -> dict[str, Any]:
    """The overlay's own extension object of a record, source or target, or {}."""
    value = node.get("extensions", {}).get(NAMESPACE) if isinstance(node, dict) else None
    return value if isinstance(value, dict) else {}


def pdf_source(overlay: Path, source_id: str) -> dict[str, Any]:
    """The `source` of the first annotation whose PDF the config's `source_id` names."""
    for _path, annotation in read_bundle(overlay):
        source = annotation.get("source")
        if isinstance(source, dict) and source.get("type") == "pdf" \
                and extension(source).get("source_id") == source_id:
            return source
    raise AnnotationError(f"no annotation is a record of the PDF {source_id!r}, which the config's "
                          f"source_id names; `cf overlay export` writes them")
