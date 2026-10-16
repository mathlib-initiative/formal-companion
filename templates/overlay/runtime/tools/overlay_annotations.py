"""Read the Literature Links annotations a generated overlay shows.

Section 2 of the overlay contract (`docs/overlay.md` in the formal-companion
skill).  The annotations are JSON files, one annotation per file, in one
folder per book section and one folder per item with several, under the
directory `GENERATED.json`'s `annotations` block names (relative to the
companion root), with Literature Links folder defaults (`_defaults.json`) at
the top and in item folders.  The block records the annotation count, the
manifest sha256 of every file, and the sha256 of the bundle `cf overlay sync`
writes beside it: every annotation as the shipped `literature_links` reader
reads it, defaults applied, with its file's path, in file order, which is what
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
#: A section folder, an item folder in it, an annotation file in either, and folder defaults.
SECTION_DIR = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*\Z")
ITEM_DIR = re.compile(r"[0-9A-Za-z_-][0-9A-Za-z._-]{0,119}\Z")
ANNOTATION_FILE = re.compile(r"[0-9A-Za-z_-][0-9A-Za-z._-]{0,114}\.json\Z")
DEFAULTS = "_defaults.json"
#: The order `cf overlay export` writes top-level members in, the rest sorted; an end's members in
#: :data:`END` order; elsewhere `type` first.
TOP = ("$schema", "id", "kind", "status", "relation", "junk", "note", "subject", "object", "extensions")
END = ("type", "uri", "version", "location", "title", "role", "sha256", "citation", "identifiers", "extensions")
SIDES = ("subject", "object")
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


def file_bytes(value: Any) -> bytes:
    """A file as `cf overlay export` writes it: :data:`TOP` and :data:`END` order, two-space indent, scalar
    arrays on one line."""
    def dump(item: Any, indent: str, first: tuple[str, ...]) -> str:
        inner = indent + "  "
        if isinstance(item, dict) and item:
            keys = [k for k in first if k in item] + sorted(k for k in item if k not in first)
            return "{\n" + ",\n".join(f"{inner}{scalar(k)}: "
                                       f"{dump(item[k], inner, END if first is TOP and k in SIDES else ('type',))}"
                                       for k in keys) + "\n" + indent + "}"
        if isinstance(item, list) and item:
            if not any(isinstance(x, (dict, list)) for x in item):
                return "[" + ", ".join(map(scalar, item)) + "]"
            return "[\n" + ",\n".join(inner + dump(x, inner, first) for x in item) + "\n" + indent + "]"
        return scalar(item)

    def scalar(item: Any) -> str:
        return json.dumps(item, ensure_ascii=False, allow_nan=False)

    return (dump(value, "", TOP) + "\n").encode("utf-8")


def form_problems(files: dict[str, bytes]) -> list[str]:
    """Each file that is not byte for byte what `cf overlay export` writes for its JSON, named."""
    out = []
    for path, raw in files.items():
        try:
            value = strict_json(raw.decode("utf-8"), path)
        except (UnicodeDecodeError, AnnotationError):
            continue                                         # the scan reports it
        if file_bytes(value) != raw:
            out.append(f"{path}: not as `cf overlay export` writes it; re-run it")
    return out


def canonical(annotation: Any) -> bytes:
    """An annotation as one comparable string: sorted keys, UTF-8."""
    return json.dumps(annotation, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")


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
    """``<directory>/<section>/<name>.json`` or ``<directory>/<section>/<item>/<name>.json``."""
    if not safe_relative(value) or not value.startswith(directory + "/"):
        return False
    parts = PurePosixPath(value[len(directory) + 1:]).parts
    return len(parts) in (2, 3) and bool(SECTION_DIR.match(parts[0])) and all(map(ITEM_DIR.match, parts[1:-1])) \
        and bool(ANNOTATION_FILE.match(parts[-1])) and parts[-1] != DEFAULTS


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


def walk(base: Path) -> tuple[dict[str, Path], list[str]]:
    """``(files, odd)`` under an annotation directory, by path relative to it.

    The layout: `_defaults.json` and section folders at the top, annotation
    files and item folders in a section, `_defaults.json` and annotation files
    in an item folder.  Anything else, a link included, is odd.
    """
    files: dict[str, Path] = {}
    odd: list[str] = []

    def visit(path: Path, prefix: str, depth: int) -> None:
        for entry in sorted(os.scandir(path), key=lambda e: e.name):
            relative = prefix + entry.name
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode) and depth < 2 and (SECTION_DIR, ITEM_DIR)[depth].match(entry.name):
                visit(Path(entry.path), relative + "/", depth + 1)
            elif stat.S_ISREG(mode) and (depth != 1 if entry.name == DEFAULTS
                                         else depth > 0 and ANNOTATION_FILE.match(entry.name)):
                files[relative] = Path(entry.path)
            else:
                odd.append(relative)

    visit(base, "", 0)
    return dict(sorted(files.items())), odd


def disk_files(root: Path, directory: str) -> tuple[dict[str, bytes], list[str]]:
    """``({path: bytes}, problems)``: every file under ``root/directory``, and anything the layout has no place for."""
    found, odd = walk(root.joinpath(*PurePosixPath(directory).parts))
    problems = [f"{directory}/{relative}: not an annotation file; {directory}/ holds only "
                f"<section>/[<item>/]<name>.json files and their {DEFAULTS}" for relative in odd]
    return {f"{directory}/{relative}": path.read_bytes() for relative, path in found.items()}, problems


def annotation_count(files: dict[str, bytes]) -> int:
    """How many of ``files`` are annotation files, not folder defaults."""
    return sum(PurePosixPath(path).name != DEFAULTS for path in files)


def read_records(root: Path, directory: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """``({path: annotation}, problems)``: what the shipped `literature_links` reads, folder defaults applied."""
    try:
        from literature_links import discovery
    except ImportError as error:                             # pragma: no cover
        return {}, [f"tools/literature_links: cannot import it: {error}"]
    base = root.resolve().joinpath(*PurePosixPath(directory).parts)
    collection = discovery.collect([base])
    problems = [f"{p.path.replace(str(root.resolve()) + os.sep, '')}: {p.message} [{p.code}]"
                for p in collection.problems]
    problems += [f"{str(path).replace(str(root.resolve()) + os.sep, '')}: folder defaults outside {directory}/ apply "
                 f"to its annotations; remove it" for path in discovery.inherit({}, base)[1]]
    return {f"{directory}/{r.path.relative_to(base).as_posix()}": r.annotation for r in collection.records}, problems


def bundle_problems(entries: list[tuple[str, dict[str, Any]]], records: dict[str, dict[str, Any]]) -> list[str]:
    """Each annotation the bundle does not hold exactly as the reader reads it, named."""
    held = {path: canonical(annotation) for path, annotation in entries}
    out = [f"{path}: not in the bundle; run `cf overlay sync`" for path in sorted(set(records) - set(held))]
    out += [f"{path}: missing, but the bundle holds it" for path in sorted(set(held) - set(records))]
    out += [f"{path}: differs from the bundle" for path in sorted(set(held) & set(records))
            if held[path] != canonical(records[path])]
    return out


def extension(node: Any) -> dict[str, Any]:
    """The overlay's own extension object of a record or an end, or {}."""
    value = node.get("extensions", {}).get(NAMESPACE) if isinstance(node, dict) else None
    return value if isinstance(value, dict) else {}


def pdf_ends(annotation: Any, source_id: str) -> list[dict[str, Any]]:
    """The annotation's subject ends that are regions of the PDF the config's `source_id` names."""
    ends = annotation.get("subject") if isinstance(annotation, dict) else None
    return [end for end in ends if isinstance(end, dict) and end.get("type") == "pdf"
            and extension(end).get("source_id") == source_id] if isinstance(ends, list) else []


def pdf_source(overlay: Path, source_id: str) -> dict[str, Any]:
    """The first subject end, in the bundle, of the PDF the config's `source_id` names."""
    for _path, annotation in read_bundle(overlay):
        for end in pdf_ends(annotation, source_id):
            return end
    raise AnnotationError(f"no annotation is a record of the PDF {source_id!r}, which the config's "
                          f"source_id names; `cf overlay export` writes them")
