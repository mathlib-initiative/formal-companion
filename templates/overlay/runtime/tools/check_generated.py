#!/usr/bin/env python3
"""Verify that this `pdf-overlay/` is the directory formal-companion generated.

Section 5 of the overlay contract (`docs/overlay.md` in the formal-companion
skill).  A companion's CI runs

    python3 pdf-overlay/tools/check_generated.py

and needs nothing but CPython 3.11 and this directory: no network, no package,
not even the skill.  That is the whole point of `sync` copying the runtime byte
for byte -- a generated directory can be checked against its own manifest.

It reports, one line per problem, and exits 1 when there is one:

* every file `GENERATED.json` lists exists, is an ordinary file, and has the
  sha256 recorded for it, and `pdf-overlay/` holds nothing else but
  `GENERATED.json`, the bundle, the instance file `overlay.config.json` and
  the companion's own `verso.json`, if it commits one: that one must have the
  form `tools/verso_extract.py --pages` writes and be of a build of a commit
  the bundle's cards pin, with no statement whose card pins a release tag
  rather than a full commit (a statement that no longer joins to its card's
  snippet is not a problem: `build_site.py` drops it);
* `GENERATED.json`'s `format` block names the Literature Links release whose
  package `cf overlay sync` shipped in `tools/literature_links/`;
* `overlay.config.json` satisfies section 3 -- the same rules as the skill's
  `scripts/overlay/config.py` and as the `overlay-config-v1.schema.json` in
  the `schema/` directory beside this one.  The five patterns and the license
  list below are those files', and the skill's `tests/test_overlay_config.py`
  fails when one of the strings differs or when the three disagree on any
  config in its table;
* the annotation directory `GENERATED.json`'s `annotations` block names
  holds only `<section>/[<item>/]<name>.json` files and their
  `_defaults.json`, each as a recorded export writes it, as many annotations
  as it records and every file with its manifest sha256; the shipped
  `literature_links` scan reads them with no problem; the bundle beside it
  has its recorded sha256 and holds exactly the records that scan reads,
  defaults applied (each one it does not hold is named); at least one record
  is of the PDF the config's `source_id` names;
* every curation input the export read still has the sha256 recorded for it,
  so a companion that changed its curation and did not re-export fails here.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any

CONFIG_NAME = "overlay.config.json"
GENERATED_NAME = "GENERATED.json"

CONFIG_SCHEMA = "formal-companion/overlay-config"
CONFIG_SCHEMA_VERSION = 1
GENERATED_SCHEMA = "formal-companion/overlay-generated"
GENERATED_SCHEMA_VERSION = 3
#: The distribution `GENERATED.json`'s `format` block must name, and where
#: `cf overlay sync` ships its package.
FORMAT_PACKAGE = "literature-links"
FORMAT_KEYS = ("package", "version", "commit")
FORMAT_OPTIONAL_KEYS = ("tag", "branch")
LINKS_INIT = ("tools", "literature_links", "__init__.py")

CONFIG_KEYS = {
    "schema", "schema_version", "source_id", "title", "lede", "back_link",
    "display", "storage_key", "pdf", "site", "build", "serve",
}
REQUIRED_CONFIG_KEYS = {"schema", "schema_version", "source_id", "title", "pdf", "site"}
PDF_KEYS = {"file_name", "cache_dir", "route", "publish"}
REQUIRED_PDF_KEYS = {"file_name"}
PUBLISH_KEYS = {"license"}
SITE_KEYS = {"output_root", "index", "mount", "link_text"}
BUILD_KEYS = {"commands"}
SERVE_KEYS = {"host", "port"}
BACK_LINK_KEYS = {"href", "text"}
DISPLAY_KEYS = {"strip_prefix", "minimal"}

#: The five patterns and the license list, verbatim from the skill's
#: scripts/overlay/config.py and from the overlay-config-v1 schema in the
#: directory beside this one.
FILE_NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9._-]*$"
ROUTE_PATTERN = r"^/__[A-Za-z0-9._~/-]*[A-Za-z0-9._~-]$"
RELATIVE_PATH_PATTERN = r"^[^/\x00-\x1f\\][^\x00-\x1f\\]*$"
RELATIVE_PATH_REFUSED = ("/$", "//", r"(^|/)\.{1,2}(/|$)")
LOOPBACK_PATTERN = (r"^(127\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
                    r"(\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])){2}|::1)$")
PORT_MIN = 1
PORT_MAX = 65535
OPEN_LICENSES = ("Apache-2.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0", "MIT")
PUBLISHED_FILE_NAME_PATTERN = r"\.pdf$"

DEFAULT_CACHE_DIR = ".lake/overlay-pdf"
DEFAULT_OUTPUT_ROOT = ".lake/build/literate-html"
DEFAULT_INDEX = "index.html"
DEFAULT_MOUNT = "pdf-overlay"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

_FILE_NAME = re.compile(FILE_NAME_PATTERN)
_ROUTE = re.compile(ROUTE_PATTERN)
_RELATIVE = re.compile(RELATIVE_PATH_PATTERN)
_REFUSED = tuple(re.compile(p) for p in RELATIVE_PATH_REFUSED)
_LOOPBACK = re.compile(LOOPBACK_PATTERN)
_PUBLISHED_FILE_NAME = re.compile(PUBLISHED_FILE_NAME_PATTERN)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.M)

# The siblings are imported from the companion's tree: never leave bytecode there.
sys.dont_write_bytecode = True
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import overlay_annotations  # noqa: E402  (beside this file in pdf-overlay/tools/)
import verso_extract  # noqa: E402  (beside this file in pdf-overlay/tools/)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

class Unreadable(Exception):
    """A file this tool cannot even parse; its message is the one problem."""


def strict_json(raw: bytes, label: str) -> Any:
    """JSON with no duplicate keys and no `NaN`/`Infinity`."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Unreadable(f"{label}: not UTF-8: {error}") from error

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise Unreadable(f"{label}: duplicate JSON key {key!r}")
            out[key] = value
        return out

    def bad_constant(token: str) -> Any:
        raise Unreadable(f"{label}: non-standard JSON constant {token}")

    def finite(token: str) -> float:
        value = float(token)
        if not math.isfinite(value):
            raise Unreadable(f"{label}: number out of range: {token[:40]}")
        return value

    try:
        return json.loads(text, object_pairs_hook=unique_pairs, parse_constant=bad_constant, parse_float=finite)
    except (ValueError, RecursionError) as error:
        # ValueError is a JSONDecodeError or an integer of more digits than
        # `int` converts; RecursionError, arrays or objects nested past the stack.
        raise Unreadable(f"{label}: invalid JSON: {error}") from error


def read_bytes(path: Path, label: str) -> bytes:
    mode = _lstat(path, label)
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise Unreadable(f"{label}: expected an ordinary nonsymlink file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise Unreadable(f"{label}: cannot read it: {error}") from error


def _lstat(path: Path, label: str) -> int:
    try:
        return path.lstat().st_mode
    except FileNotFoundError as error:
        raise Unreadable(f"{label}: {path} does not exist") from error
    except OSError as error:                                 # pragma: no cover
        raise Unreadable(f"{label}: cannot inspect {path}: {error}") from error


# ---------------------------------------------------------------------------
# section 3: the configuration
# ---------------------------------------------------------------------------

def _keys(value: Any, allowed: set[str], required: set[str], label: str,
          out: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        out.append(f"{label}: expected an object")
        return {}
    unknown = sorted(set(value) - allowed)
    if unknown:
        out.append(f"{label}: unknown keys {unknown}")
    missing = sorted(required - set(value))
    if missing:
        out.append(f"{label}: missing keys {missing}")
    return value


def _string(value: Any, label: str, out: list[str], *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        out.append(f"{label}: expected a nonempty string")
        return ""
    return value


def _relative(value: Any, label: str, out: list[str]) -> None:
    if not isinstance(value, str) or not value:
        out.append(f"{label}: expected a nonempty relative path")
        return
    if not _RELATIVE.fullmatch(value) or any(p.search(value) for p in _REFUSED) \
            or str(PurePosixPath(value)) != value:
        out.append(f"{label}: path must be relative, normalised and traversal-free: "
                   f"{value!r}")


def config_problems(value: Any, label: str = CONFIG_NAME) -> list[str]:
    """Every way *value* breaks section 3, as ``<path>: <message>`` lines."""
    out: list[str] = []
    if not isinstance(value, dict):
        return [f"{label}: expected an object"]
    _keys(value, CONFIG_KEYS, REQUIRED_CONFIG_KEYS, label, out)

    if "schema" in value and value["schema"] != CONFIG_SCHEMA:
        out.append(f"{label}.schema: expected {CONFIG_SCHEMA}")
    if "schema_version" in value:
        version = value["schema_version"]
        if isinstance(version, bool) or version != CONFIG_SCHEMA_VERSION:
            out.append(f"{label}.schema_version: expected {CONFIG_SCHEMA_VERSION}")
    if "source_id" in value:
        _string(value["source_id"], f"{label}.source_id", out)
    if "title" in value:
        _string(value["title"], f"{label}.title", out)
    if "lede" in value:
        _string(value["lede"], f"{label}.lede", out)
    if "storage_key" in value:
        _string(value["storage_key"], f"{label}.storage_key", out)
    if "back_link" in value:
        back = _keys(value["back_link"], BACK_LINK_KEYS, BACK_LINK_KEYS,
                     f"{label}.back_link", out)
        _string(back.get("href"), f"{label}.back_link.href", out)
        _string(back.get("text"), f"{label}.back_link.text", out)
    if "display" in value:
        display = _keys(value["display"], DISPLAY_KEYS, set(), f"{label}.display", out)
        if "strip_prefix" in display:
            _string(display["strip_prefix"], f"{label}.display.strip_prefix", out,
                    allow_empty=True)
        if "minimal" in display and not isinstance(display["minimal"], bool):
            out.append(f"{label}.display.minimal: expected true or false")

    file_name = ""
    if "pdf" in value:
        pdf = _keys(value["pdf"], PDF_KEYS, REQUIRED_PDF_KEYS, f"{label}.pdf", out)
        if "file_name" in pdf:
            file_name = _string(pdf["file_name"], f"{label}.pdf.file_name", out)
            if file_name and not _FILE_NAME.fullmatch(file_name):
                out.append(f"{label}.pdf.file_name: expected one plain file name: "
                           f"{file_name!r}")
                file_name = ""
        if "cache_dir" in pdf:
            _relative(pdf["cache_dir"], f"{label}.pdf.cache_dir", out)
        route = pdf.get("route", f"/__overlay_pdf__/{file_name}" if file_name else "")
        if not isinstance(route, str) or not route:
            out.append(f"{label}.pdf.route: expected a nonempty string")
        elif not _ROUTE.fullmatch(route) or ".." in route:
            out.append(f"{label}.pdf.route: must start with /__, name a file and "
                       f"hold no ..: {route!r}")
        if "publish" in pdf:
            publish = _keys(pdf["publish"], PUBLISH_KEYS, PUBLISH_KEYS,
                            f"{label}.pdf.publish", out)
            if file_name and not _PUBLISHED_FILE_NAME.search(file_name):
                out.append(f"{label}.pdf.file_name: a published PDF's file name ends in "
                           f".pdf: {file_name!r}")
            found = publish.get("license")
            if "license" in publish and not (isinstance(found, str) and found in OPEN_LICENSES):
                out.append(f"{label}.pdf.publish.license: expected one of "
                           f"{', '.join(OPEN_LICENSES)}, got {found!r}")

    if "site" in value:
        site = _keys(value["site"], SITE_KEYS, set(), f"{label}.site", out)
        for key in ("output_root", "index", "mount"):
            if key in site:
                _relative(site[key], f"{label}.site.{key}", out)
        if "link_text" in site:
            _string(site["link_text"], f"{label}.site.link_text", out)

    if "build" in value:
        build = _keys(value["build"], BUILD_KEYS, set(), f"{label}.build", out)
        commands = build.get("commands")
        if "commands" in build:
            if not isinstance(commands, list):
                out.append(f"{label}.build.commands: expected an array of argv arrays")
            else:
                for index, command in enumerate(commands):
                    where = f"{label}.build.commands[{index}]"
                    if not isinstance(command, list) or not command:
                        out.append(f"{where}: expected a nonempty argv array")
                        continue
                    for position, word in enumerate(command):
                        word = _string(word, f"{where}[{position}]", out)
                        if "\0" in word:
                            out.append(f"{where}[{position}]: argument carries a NUL byte")

    if "serve" in value:
        serve = _keys(value["serve"], SERVE_KEYS, set(), f"{label}.serve", out)
        if "host" in serve:
            host = _string(serve["host"], f"{label}.serve.host", out)
            if host and not (_LOOPBACK.fullmatch(host) and _is_loopback(host)):
                out.append(f"{label}.serve.host: expected a loopback address "
                           f"(127.x.y.z or ::1), got {host!r}")
        if "port" in serve:
            port = serve["port"]
            if not isinstance(port, int) or isinstance(port, bool) \
                    or not PORT_MIN <= port <= PORT_MAX:
                out.append(f"{label}.serve.port: expected a port in "
                           f"{PORT_MIN}..{PORT_MAX}")
    return out


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:                                       # pragma: no cover
        return False


# ---------------------------------------------------------------------------
# the generated directory
# ---------------------------------------------------------------------------

def _safe_relative(relative: str) -> bool:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return False
    pure = PurePosixPath(relative)
    return (not pure.is_absolute() and str(pure) == relative
            and not any(part in {"", ".", ".."} for part in pure.parts))


def format_problems(overlay: Path, record: Any) -> list[str]:
    """`GENERATED.json`'s `format` block, against the package shipped in `tools/`.

    The block names the Literature Links release this directory reads.  Its
    `commit` cannot be checked from here -- there is no network and no
    checkout -- but the package name and the shipped package's version can.
    """
    out: list[str] = []
    block = record.get("format") if isinstance(record, dict) else None
    if not isinstance(block, dict):
        return [f"{GENERATED_NAME}.format: expected an object naming the Literature Links "
                f"release this directory was generated against"]
    missing = sorted(set(FORMAT_KEYS) - set(block))
    if missing:
        out.append(f"{GENERATED_NAME}.format: missing keys {missing}")
    unknown = sorted(set(block) - set(FORMAT_KEYS) - set(FORMAT_OPTIONAL_KEYS))
    if unknown:
        out.append(f"{GENERATED_NAME}.format: unknown keys {unknown}")
    for key in (*FORMAT_KEYS, *FORMAT_OPTIONAL_KEYS):
        if key in block and (not isinstance(block[key], str) or not block[key]):
            out.append(f"{GENERATED_NAME}.format.{key}: expected a nonempty string")
    if isinstance(block.get("package"), str) and block["package"] != FORMAT_PACKAGE:
        out.append(f"{GENERATED_NAME}.format.package: expected {FORMAT_PACKAGE}")
    try:
        shipped = _VERSION.search(read_bytes(overlay.joinpath(*LINKS_INIT), "/".join(LINKS_INIT))
                                  .decode("utf-8"))
    except (Unreadable, UnicodeDecodeError) as error:
        return out + [str(error)]
    if shipped is None or shipped.group(1) != block.get("version"):
        out.append(f"{GENERATED_NAME}.format.version: {block.get('version')!r} is not the version of "
                   f"the shipped {'/'.join(LINKS_INIT[:2])} "
                   f"({shipped.group(1) if shipped else 'none'!r})")
    return out


def generated_problems(overlay: Path, record: Any) -> tuple[list[str], list[str]]:
    """Problems with the manifest and the files, and the instance file names."""
    out: list[str] = []
    if not isinstance(record, dict):
        return [f"{GENERATED_NAME}: expected an object"], [CONFIG_NAME]
    if record.get("schema") != GENERATED_SCHEMA:
        out.append(f"{GENERATED_NAME}.schema: expected {GENERATED_SCHEMA}")
    if record.get("schema_version") != GENERATED_SCHEMA_VERSION:
        out.append(f"{GENERATED_NAME}.schema_version: expected "
                   f"{GENERATED_SCHEMA_VERSION}")
    generator = record.get("generator")
    if not isinstance(generator, dict) or not generator.get("name"):
        out.append(f"{GENERATED_NAME}.generator: expected an object naming the generator")
    out += format_problems(overlay, record)

    instance = record.get("instance_files")
    if not isinstance(instance, list) or not all(isinstance(n, str) for n in instance):
        out.append(f"{GENERATED_NAME}.instance_files: expected an array of file names")
        instance = [CONFIG_NAME]

    listed: dict[str, str] = {}
    order: list[str] = []
    files = record.get("files")
    if not isinstance(files, list):
        out.append(f"{GENERATED_NAME}.files: expected an array of "
                   f"{{path, sha256}} entries")
        files = []
    for index, row in enumerate(files):
        where = f"{GENERATED_NAME}.files[{index}]"
        if not isinstance(row, dict) or not _safe_relative(row.get("path")) \
                or not isinstance(row.get("sha256"), str) \
                or not _HEX64.search(row["sha256"]):
            out.append(f"{where}: expected {{path, sha256}} with a relative path and "
                       f"a lowercase sha256")
            continue
        if row["path"] in listed:
            out.append(f"{where}: {row['path']} is listed twice")
        listed[row["path"]] = row["sha256"]
        order.append(row["path"])
    if order != sorted(order):
        out.append(f"{GENERATED_NAME}.files: expected the entries sorted by path")

    for relative, want in sorted(listed.items()):
        path = overlay.joinpath(*PurePosixPath(relative).parts)
        try:
            raw = read_bytes(path, relative)
        except Unreadable as error:
            out.append(str(error))
            continue
        found = hashlib.sha256(raw).hexdigest()
        if found != want:
            out.append(f"{relative}: sha256 {found}, but {GENERATED_NAME} records {want}")

    block = record.get("annotations")
    bundle = block.get("bundle") if isinstance(block, dict) else None
    allowed = set(listed) | set(instance) | {GENERATED_NAME, verso_extract.FILE_NAME}
    if isinstance(bundle, dict) and isinstance(bundle.get("path"), str) \
            and overlay_annotations.BUNDLE_FILE.match(bundle["path"]):
        allowed.add(bundle["path"])
    for relative, mode in sorted(_walk(overlay).items()):
        if relative in allowed:
            continue
        kind = "a symlink" if stat.S_ISLNK(mode) else "not generated"
        out.append(f"{relative}: {kind}; {GENERATED_NAME} lists no such file and it "
                   f"is not an instance file")
    return out, list(instance)


def _walk(overlay: Path) -> dict[str, int]:
    """{relative path: st_mode} for everything under *overlay* that is not a directory."""
    found: dict[str, int] = {}
    for parent, names, files in os.walk(overlay):
        names.sort()
        for name in names:
            path = Path(parent) / name
            if path.is_symlink():
                found[path.relative_to(overlay).as_posix()] = path.lstat().st_mode
        for name in sorted(files):
            path = Path(parent) / name
            found[path.relative_to(overlay).as_posix()] = path.lstat().st_mode
    return found


def annotations_problems(overlay: Path, source_id: str | None) -> list[str]:
    """The annotation files against `GENERATED.json` and the bundle, the shipped scan, the source, the inputs."""
    try:
        block = overlay_annotations.annotations_block(overlay)
    except overlay_annotations.AnnotationError as error:
        return [str(error)]
    root = overlay.parent
    directory = root.joinpath(*PurePosixPath(block["directory"]).parts)
    out: list[str] = []
    if not block["count"]:
        out.append(f"{GENERATED_NAME}.annotations records no annotation file; `cf overlay export` writes them")
    if directory.is_symlink() or not directory.is_dir():
        return out + [f"{block['directory']}/: not an ordinary directory"]
    files, layout = overlay_annotations.disk_files(root, block["directory"])
    out += layout + (overlay_annotations.form_problems(files) if "export" in block else [])
    found = overlay_annotations.manifest_sha256(files)
    count = overlay_annotations.annotation_count(files)
    if (count, found) != (block["count"], block["manifest_sha256"]):
        out.append(f"{block['directory']}/: {count} annotations in {len(files)} files with manifest sha256 "
                   f"{found}, but {GENERATED_NAME} records {block['count']} with {block['manifest_sha256']}")
    records, scanned = overlay_annotations.read_records(root, block["directory"]) if files else ({}, [])
    out += scanned
    try:
        entries = overlay_annotations.read_bundle(overlay, block)
    except overlay_annotations.AnnotationError as error:
        out.append(str(error))
    else:
        out += overlay_annotations.bundle_problems(entries, records)
    if files and source_id is not None and not any(overlay_annotations.pdf_ends(r, source_id)
                                                   for r in records.values()):
        out.append(f"no annotation is a record of the PDF {source_id!r}, which "
                   f"{CONFIG_NAME} names")
    out += inputs_problems(root, block.get("inputs"), exported="export" in block)
    return out


def inputs_problems(root: Path, inputs: Any, *, exported: bool = False) -> list[str]:
    """Each curation input the export recorded still hashes to its recorded sha256.

    An export must record at least one: without them a stale export passes.
    """
    if exported and not inputs:
        return [f"{GENERATED_NAME}.annotations.inputs: the export records no curation input, so a stale "
                f"export cannot be told; re-run `cf overlay export`"]
    if inputs is None:
        return []
    if not isinstance(inputs, list):
        return [f"{GENERATED_NAME}.annotations.inputs: expected an array of {{path, sha256}}"]
    out = []
    for index, row in enumerate(inputs):
        if not isinstance(row, dict) or not _safe_relative(row.get("path")) \
                or not isinstance(row.get("sha256"), str) or not _HEX64.search(row["sha256"]):
            out.append(f"{GENERATED_NAME}.annotations.inputs[{index}]: expected {{path, sha256}}")
            continue
        try:
            raw = read_bytes(root.joinpath(*PurePosixPath(row["path"]).parts), row["path"])
        except Unreadable as error:
            out.append(f"{error}; the annotations were exported from it: re-run `cf overlay export`")
            continue
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            out.append(f"{row['path']}: changed since the annotations were exported from it; "
                       f"re-run `cf overlay export` and `cf overlay sync`")
    return out


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

def overlay_root(argv_root: str | None = None) -> Path:
    """`pdf-overlay/`, found from this file rather than the working directory."""
    if argv_root is not None:
        return Path(argv_root).expanduser()
    return Path(__file__).resolve().parent.parent


def problems(overlay: Path) -> list[str]:
    """Every problem with this generated directory, one line each."""
    try:
        record = strict_json(read_bytes(overlay / GENERATED_NAME, GENERATED_NAME),
                             GENERATED_NAME)
    except Unreadable as error:
        return [str(error), f"{GENERATED_NAME} is what `cf overlay sync <companion>` "
                            f"writes; run it from the formal-companion checkout"]
    out, _instance = generated_problems(overlay, record)
    source_id = None
    try:
        config = strict_json(read_bytes(overlay / CONFIG_NAME, CONFIG_NAME), CONFIG_NAME)
    except Unreadable as error:
        out.append(str(error))
    else:
        found = config_problems(config)
        out += found
        if not found:
            source_id = config["source_id"]
    return out + verso_problems(overlay) + annotations_problems(overlay, source_id)


def verso_problems(overlay: Path) -> list[str]:
    """A committed `verso.json`, when there is one, against the form `verso_extract.py --pages` writes
    and the commit the bundle's cards pin (a bundle that does not read is `annotations_problems`')."""
    path = overlay / verso_extract.FILE_NAME
    if not path.is_symlink() and not path.exists():
        return []
    try:
        value = strict_json(read_bytes(path, verso_extract.FILE_NAME), verso_extract.FILE_NAME)
    except Unreadable as error:
        return [str(error)]
    found = verso_extract.committed_problems(value)
    if not found:
        try:
            stale = verso_extract.unpinned(value, overlay_annotations.read_bundle(overlay))
        except overlay_annotations.AnnotationError:
            stale = None
        found = [stale] if stale else []
    return [f"{verso_extract.FILE_NAME}: {line}" for line in found]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_generated.py",
        description="Verify that pdf-overlay/ is the directory formal-companion "
                    "generated, that its config fits it, and that the annotations it "
                    "records are current and valid.")
    parser.add_argument("--overlay", metavar="DIR",
                        help="the pdf-overlay directory (default: the one holding "
                             "this script)")
    args = parser.parse_args(argv)
    overlay = overlay_root(args.overlay)
    if not overlay.is_dir():
        print(f"{overlay} is not a directory", file=sys.stderr)
        return 1
    found = problems(overlay)
    for line in found:
        print(line, file=sys.stderr)
    if found:
        print(f"{overlay}: {len(found)} problem(s)", file=sys.stderr)
        return 1
    print(f"{overlay}: ok")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    sys.exit(main())
