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
  sha256 recorded for it;
* `pdf-overlay/` holds no other file, except the two instance files
  `GENERATED.json` names and `GENERATED.json` itself;
* `overlay.config.json` satisfies section 3 -- the same rules as the skill's
  `scripts/overlay/config.py` and as the `overlay-config-v1.schema.json` in
  the `schema/` directory beside this one.  The four patterns below are those
  files' patterns, and
  the skill's `tests/test_overlay_config.py` fails when one of the strings
  differs or when the three disagree on any config in its table;
* `annotations.json` parses, declares the annotation format's own `schema`,
  and carries the config's `source_id` as a `kind: pdf` source with a
  `sha256`.  The format's full checker is not here (it is a package this
  directory deliberately does not depend on); `cf overlay check` runs it.

Which documents count as "the annotation format" is not written down here.
`pdf-overlay/schema/annotations.schema.json` beside this directory is the
format's own JSON Schema, copied in by `cf overlay sync`, and its
``properties.schema.const`` and ``properties.schema_version.pattern`` are the
only statement of the accepted identity; `GENERATED.json`'s ``format`` block
records which release that schema came from.  A format upgrade is therefore a
new vendored release and a re-sync, and nothing in this file to edit.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any

CONFIG_NAME = "overlay.config.json"
DOCUMENT_NAME = "annotations.json"
GENERATED_NAME = "GENERATED.json"

CONFIG_SCHEMA = "formal-companion/overlay-config"
CONFIG_SCHEMA_VERSION = 1
GENERATED_SCHEMA = "formal-companion/overlay-generated"
GENERATED_SCHEMA_VERSION = 1
#: The annotation format's own schema, as `cf overlay sync` copied it out of
#: the vendored release.  Nothing here names the format's identity: it is read
#: from this file.
FORMAT_SCHEMA_PATH = ("schema", "annotations.schema.json")
#: The distribution `GENERATED.json`'s `format` block must name.
FORMAT_PACKAGE = "formal-companion-annotations"
#: The keys of that block, and the two the shipped schema settles.
FORMAT_KEYS = ("package", "version", "tag", "commit", "schema", "schema_version")

CONFIG_KEYS = {
    "schema", "schema_version", "source_id", "title", "lede", "back_link",
    "display", "storage_key", "pdf", "site", "build", "serve",
}
REQUIRED_CONFIG_KEYS = {"schema", "schema_version", "source_id", "title", "pdf", "site"}
PDF_KEYS = {"file_name", "cache_dir", "route"}
REQUIRED_PDF_KEYS = {"file_name"}
SITE_KEYS = {"output_root", "index", "mount", "link_text"}
BUILD_KEYS = {"commands"}
SERVE_KEYS = {"host", "port"}
BACK_LINK_KEYS = {"href", "text"}
DISPLAY_KEYS = {"strip_prefix"}

#: The four patterns, verbatim from the skill's scripts/overlay/config.py and
#: from the overlay-config-v1 schema in the directory beside this one.
FILE_NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9._-]*$"
ROUTE_PATTERN = r"^/__[A-Za-z0-9._~/-]*[A-Za-z0-9._~-]$"
RELATIVE_PATH_PATTERN = r"^[^/\x00-\x1f\\][^\x00-\x1f\\]*$"
RELATIVE_PATH_REFUSED = ("/$", "//", r"(^|/)\.{1,2}(/|$)")
LOOPBACK_PATTERN = (r"^(127\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
                    r"(\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])){2}|::1)$")
PORT_MIN = 1
PORT_MAX = 65535

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
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


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

    try:
        return json.loads(text, object_pairs_hook=unique_pairs, parse_constant=bad_constant)
    except json.JSONDecodeError as error:
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
# the document identity, read from the schema this directory ships
# ---------------------------------------------------------------------------

class DocumentFormat:
    """Which documents the shipped annotation schema accepts.

    `schema` is the schema's ``properties.schema.const`` and `version` is a
    compiled ``properties.schema_version.pattern``.  A schema file that states
    neither is a broken generated directory, not a licence to accept anything.
    """

    __slots__ = ("schema", "version")

    def __init__(self, schema: str, version: "re.Pattern[str]") -> None:
        self.schema = schema
        self.version = version


def format_label() -> str:
    return "/".join(FORMAT_SCHEMA_PATH)


def document_format(overlay: Path) -> DocumentFormat:
    """Read `pdf-overlay/schema/annotations.schema.json`, or say why it cannot be."""
    label = format_label()
    value = strict_json(read_bytes(overlay.joinpath(*FORMAT_SCHEMA_PATH), label), label)
    if not isinstance(value, dict):
        raise Unreadable(f"{label}: expected a JSON Schema object")
    properties = value.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    schema = properties.get("schema")
    const = schema.get("const") if isinstance(schema, dict) else None
    if not isinstance(const, str) or not const:
        raise Unreadable(f"{label}: properties.schema has no const, so the accepted "
                         f"document identity cannot be read")
    version = properties.get("schema_version")
    pattern = version.get("pattern") if isinstance(version, dict) else None
    if not isinstance(pattern, str) or not pattern:
        raise Unreadable(f"{label}: properties.schema_version has no pattern, so the "
                         f"accepted document versions cannot be read")
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise Unreadable(f"{label}: properties.schema_version.pattern is not a regular "
                         f"expression: {error}") from error
    return DocumentFormat(const, compiled)


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
    if not _RELATIVE.search(value) or any(p.search(value) for p in _REFUSED) \
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

    file_name = ""
    if "pdf" in value:
        pdf = _keys(value["pdf"], PDF_KEYS, REQUIRED_PDF_KEYS, f"{label}.pdf", out)
        if "file_name" in pdf:
            file_name = _string(pdf["file_name"], f"{label}.pdf.file_name", out)
            if file_name and not _FILE_NAME.search(file_name):
                out.append(f"{label}.pdf.file_name: expected one plain file name: "
                           f"{file_name!r}")
                file_name = ""
        if "cache_dir" in pdf:
            _relative(pdf["cache_dir"], f"{label}.pdf.cache_dir", out)
        route = pdf.get("route", f"/__overlay_pdf__/{file_name}" if file_name else "")
        if not isinstance(route, str) or not route:
            out.append(f"{label}.pdf.route: expected a nonempty string")
        elif not _ROUTE.search(route) or ".." in route:
            out.append(f"{label}.pdf.route: must start with /__, name a file and "
                       f"hold no ..: {route!r}")

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
            if host and not (_LOOPBACK.search(host) and _is_loopback(host)):
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


def format_problems(record: Any, fmt: DocumentFormat | None) -> list[str]:
    """`GENERATED.json`'s `format` block, against the schema this directory ships.

    The block records which release of the annotation format generated this
    directory.  Its `version`, `tag` and `commit` cannot be checked from here
    -- there is no network and no package -- but the three that can are
    checked: the distribution's name, and the identity and version the shipped
    schema states.
    """
    out: list[str] = []
    block = record.get("format") if isinstance(record, dict) else None
    if not isinstance(block, dict):
        return [f"{GENERATED_NAME}.format: expected an object naming the annotation "
                f"format release this directory was generated against"]
    missing = sorted(set(FORMAT_KEYS) - set(block))
    if missing:
        out.append(f"{GENERATED_NAME}.format: missing keys {missing}")
    unknown = sorted(set(block) - set(FORMAT_KEYS))
    if unknown:
        out.append(f"{GENERATED_NAME}.format: unknown keys {unknown}")
    for key in FORMAT_KEYS:
        if key in block and (not isinstance(block[key], str) or not block[key]):
            out.append(f"{GENERATED_NAME}.format.{key}: expected a nonempty string")
    if isinstance(block.get("package"), str) and block["package"] != FORMAT_PACKAGE:
        out.append(f"{GENERATED_NAME}.format.package: expected {FORMAT_PACKAGE}")
    if fmt is None:
        return out
    if isinstance(block.get("schema"), str) and block["schema"] != fmt.schema:
        out.append(f"{GENERATED_NAME}.format.schema: {block['schema']!r} is not the "
                   f"identity {format_label()} states ({fmt.schema!r})")
    version = block.get("schema_version")
    if isinstance(version, str) and version and not fmt.version.fullmatch(version):
        out.append(f"{GENERATED_NAME}.format.schema_version: {version!r} does not match "
                   f"the pattern {format_label()} states ({fmt.version.pattern})")
    return out


def generated_problems(overlay: Path, record: Any,
                       fmt: DocumentFormat | None = None) -> tuple[list[str], list[str]]:
    """Problems with the manifest and the files, and the instance file names."""
    out: list[str] = []
    if not isinstance(record, dict):
        return [f"{GENERATED_NAME}: expected an object"], list(
            (DOCUMENT_NAME, CONFIG_NAME))
    if record.get("schema") != GENERATED_SCHEMA:
        out.append(f"{GENERATED_NAME}.schema: expected {GENERATED_SCHEMA}")
    if record.get("schema_version") != GENERATED_SCHEMA_VERSION:
        out.append(f"{GENERATED_NAME}.schema_version: expected "
                   f"{GENERATED_SCHEMA_VERSION}")
    generator = record.get("generator")
    if not isinstance(generator, dict) or not generator.get("name"):
        out.append(f"{GENERATED_NAME}.generator: expected an object naming the generator")
    out += format_problems(record, fmt)

    instance = record.get("instance_files")
    if not isinstance(instance, list) or not all(isinstance(n, str) for n in instance):
        out.append(f"{GENERATED_NAME}.instance_files: expected an array of file names")
        instance = [DOCUMENT_NAME, CONFIG_NAME]

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

    allowed = set(listed) | set(instance) | {GENERATED_NAME}
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


def document_problems(overlay: Path, source_id: str, fmt: DocumentFormat) -> list[str]:
    """`annotations.json` parses, is the format, and carries the config's PDF source.

    "Is the format" means what *fmt* says, which is what the shipped schema
    says: this tool holds no identity of its own to compare against.
    """
    try:
        value = strict_json(read_bytes(overlay / DOCUMENT_NAME, DOCUMENT_NAME),
                            DOCUMENT_NAME)
    except Unreadable as error:
        return [str(error)]
    if not isinstance(value, dict):
        return [f"{DOCUMENT_NAME}: expected an object"]
    out: list[str] = []
    if value.get("schema") != fmt.schema:
        out.append(f"{DOCUMENT_NAME}.schema: expected {fmt.schema}, which "
                   f"{format_label()} states")
    version = value.get("schema_version")
    if not isinstance(version, str) or not fmt.version.fullmatch(version):
        out.append(f"{DOCUMENT_NAME}.schema_version: expected a version matching "
                   f"{fmt.version.pattern}, which {format_label()} states")
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        return out + [f"{DOCUMENT_NAME}.sources: expected a nonempty array"]
    named = [row for row in sources if isinstance(row, dict) and row.get("id") == source_id]
    if not named:
        return out + [f"{DOCUMENT_NAME}: no source {source_id!r}, which "
                      f"{CONFIG_NAME} names"]
    source = named[0]
    if source.get("kind") != "pdf":
        out.append(f"{DOCUMENT_NAME}: source {source_id!r} is kind "
                   f"{source.get('kind')!r}, not pdf")
    digest = source.get("sha256")
    if not isinstance(digest, str) or not _HEX64.search(digest):
        out.append(f"{DOCUMENT_NAME}: source {source_id!r} carries no lowercase sha256")
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
    schema_problem: list[str] = []
    try:
        fmt: DocumentFormat | None = document_format(overlay)
    except Unreadable as error:
        fmt, schema_problem = None, [str(error)]
    out, _instance = generated_problems(overlay, record, fmt)
    out += schema_problem
    try:
        config = strict_json(read_bytes(overlay / CONFIG_NAME, CONFIG_NAME), CONFIG_NAME)
    except Unreadable as error:
        return out + [str(error)]
    found = config_problems(config)
    out += found
    if not found and fmt is not None:
        out += document_problems(overlay, config["source_id"], fmt)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_generated.py",
        description="Verify that pdf-overlay/ is the directory formal-companion "
                    "generated, and that its two instance files fit it.")
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
