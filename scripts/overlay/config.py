"""Load and check a companion's ``overlay.config.json``.

New code, implementing section 3 of the overlay contract (`docs/overlay.md`).
The config is the one place a generated `pdf-overlay/` carries a value chosen
for a single companion, so there are four readers of the same twelve keys and
they have to agree.  Three of them live in this repository and are held in step
mechanically:

* this module, which `cf overlay init|check|export` use;
* `templates/overlay/runtime/schema/overlay-config-v1.schema.json`, the
  Draft 2020-12 schema a companion can hand any offline validator -- it repeats
  the four patterns defined here, and `tests/test_overlay_config.py` fails when
  one of the strings differs;
* `templates/overlay/runtime/tools/check_generated.py`, which a companion's CI
  runs with nothing but the standard library and the directory itself, so it
  carries its own copy of the same rules and the same patterns.

The fourth reader is `templates/overlay/runtime/tools/serve.py`, whose
``load_config`` this one follows key for key.  Where it differs it is
*stricter*, never laxer, so a config `cf overlay check` passes is one the
runtime tools accept.  The two tightenings: a relative path may hold no
backslash and no control character, and a loopback host is spelled `127.x.y.z`
or `::1` (serve.py takes any address ``ipaddress`` calls loopback, which
includes `0:0:0:0:0:0:0:1`).

The PDF's identity is deliberately not here: its URL and `sha256` come from the
`source` of the annotations whose overlay extension carries this
`source_id`, and `bytes`, `alternate_urls` and `physical_page_count` from that
extension.
"""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from cflib.errors import CfError

__all__ = [
    "BACK_LINK_KEYS", "BUILD_KEYS", "CONFIG_KEYS", "CONFIG_NAME", "CONFIG_SCHEMA",
    "CONFIG_SCHEMA_VERSION", "Config", "DEFAULT_BACK_LINK", "DEFAULT_CACHE_DIR",
    "DEFAULT_HOST", "DEFAULT_INDEX", "DEFAULT_MOUNT", "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_PORT", "DEFAULT_STORAGE_KEY", "DISPLAY_KEYS", "FILE_NAME_PATTERN",
    "LOOPBACK_PATTERN", "PDF_KEYS", "PORT_MAX", "PORT_MIN", "RELATIVE_PATH_PATTERN",
    "RELATIVE_PATH_REFUSED", "REQUIRED_CONFIG_KEYS", "REQUIRED_PDF_KEYS", "ROUTE_DIR",
    "ROUTE_PATTERN", "SCHEMA_FILE", "SERVE_KEYS", "SITE_KEYS", "load", "load_problems",
    "parse", "problems", "route_for", "strict_json",
]

#: The file, and the two values that identify its format.
CONFIG_NAME = "overlay.config.json"
CONFIG_SCHEMA = "formal-companion/overlay-config"
CONFIG_SCHEMA_VERSION = 1
#: The schema shipped beside the config, under `pdf-overlay/schema/`.
SCHEMA_FILE = "overlay-config-v1.schema.json"

CONFIG_KEYS = frozenset({
    "schema", "schema_version", "source_id", "title", "lede", "back_link",
    "display", "storage_key", "pdf", "site", "build", "serve",
})
REQUIRED_CONFIG_KEYS = frozenset({"schema", "schema_version", "source_id", "title",
                                  "pdf", "site"})
PDF_KEYS = frozenset({"file_name", "cache_dir", "route"})
REQUIRED_PDF_KEYS = frozenset({"file_name"})
SITE_KEYS = frozenset({"output_root", "index", "mount", "link_text"})
BUILD_KEYS = frozenset({"commands"})
SERVE_KEYS = frozenset({"host", "port"})
BACK_LINK_KEYS = frozenset({"href", "text"})
DISPLAY_KEYS = frozenset({"strip_prefix"})

#: One plain file name: no directory, no leading dot, no space.
FILE_NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9._-]*$"
#: `pdf.route`: it starts with `/__`, so it can never collide with a page of the
#: built site, and it names a file, so it does not end in `/`.  `..` is refused
#: separately: one pattern that allows `.` and forbids `..` is unreadable.
ROUTE_PATTERN = r"^/__[A-Za-z0-9._~/-]*[A-Za-z0-9._~-]$"
#: A path inside the companion: relative, no backslash, no control character.
RELATIVE_PATH_PATTERN = r"^[^/\x00-\x1f\\][^\x00-\x1f\\]*$"
#: ...and none of these three shapes, which is how the schema states it too: a
#: trailing slash, an empty segment, and a `.` or `..` segment anywhere.
RELATIVE_PATH_REFUSED = ("/$", "//", r"(^|/)\.{1,2}(/|$)")
#: `serve.host`: 127.0.0.0/8 or `::1`, the two spellings anybody writes.
LOOPBACK_PATTERN = (r"^(127\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
                    r"(\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])){2}|::1)$")
PORT_MIN = 1
PORT_MAX = 65535

#: The defaults section 3 states.  `site.link_text` defaults to the title and
#: `pdf.route` to :data:`ROUTE_DIR` plus the file name, so neither is a constant.
DEFAULT_CACHE_DIR = ".lake/overlay-pdf"
DEFAULT_OUTPUT_ROOT = ".lake/build/literate-html"
DEFAULT_INDEX = "index.html"
DEFAULT_MOUNT = "pdf-overlay"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
#: The viewer's own `DEFAULT_STORAGE_KEY` and `DEFAULT_BACK_LINK` (viewer-core.js).
DEFAULT_STORAGE_KEY = "formal-companion-overlay-page"
DEFAULT_BACK_LINK = {"href": "../../index.html", "text": "Back to the site"}
#: The directory half of the default `pdf.route`.
ROUTE_DIR = "/__overlay_pdf__"

_FILE_NAME = re.compile(FILE_NAME_PATTERN)
_ROUTE = re.compile(ROUTE_PATTERN)
_RELATIVE = re.compile(RELATIVE_PATH_PATTERN)
_REFUSED = tuple(re.compile(p) for p in RELATIVE_PATH_REFUSED)
_LOOPBACK = re.compile(LOOPBACK_PATTERN)


def route_for(file_name: str) -> str:
    """The default `pdf.route` for a cache file name."""
    return f"{ROUTE_DIR}/{file_name}"


@dataclass(frozen=True)
class Config:
    """`overlay.config.json` with every default filled in."""

    source_id: str
    title: str
    lede: str
    back_link_href: str
    back_link_text: str
    strip_prefix: str
    storage_key: str
    file_name: str
    cache_dir: str
    route: str
    output_root: str
    index: str
    mount: str
    link_text: str
    build_commands: tuple[tuple[str, ...], ...]
    host: str
    port: int


# ---------------------------------------------------------------------------
# reading the bytes
# ---------------------------------------------------------------------------

def strict_json(raw: bytes, label: str) -> Any:
    """JSON with no duplicate keys and no `NaN`/`Infinity`, as the tools read it."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CfError(f"{label}: not UTF-8: {exc}") from exc

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise CfError(f"{label}: duplicate JSON key {key!r}")
            out[key] = value
        return out

    def bad_constant(token: str) -> Any:
        raise CfError(f"{label}: non-standard JSON constant {token}")

    try:
        return json.loads(text, object_pairs_hook=unique_pairs, parse_constant=bad_constant)
    except json.JSONDecodeError as exc:
        raise CfError(f"{label}: invalid JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# the rules of section 3
# ---------------------------------------------------------------------------

def _keys(value: Any, allowed: frozenset[str], required: frozenset[str],
          label: str, out: list[str]) -> dict[str, Any]:
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


def _relative(value: Any, label: str, out: list[str], fallback: str) -> str:
    """A nonempty, normalised, traversal-free path inside the companion."""
    if not isinstance(value, str) or not value:
        out.append(f"{label}: expected a nonempty relative path")
        return fallback
    if not _RELATIVE.search(value) or any(p.search(value) for p in _REFUSED) \
            or str(PurePosixPath(value)) != value:
        out.append(f"{label}: path must be relative, normalised and traversal-free: "
                   f"{value!r}")
        return fallback
    return value


def _argv_list(value: Any, label: str, out: list[str]) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        out.append(f"{label}: expected an array of argv arrays")
        return ()
    commands: list[tuple[str, ...]] = []
    for index, command in enumerate(value):
        where = f"{label}[{index}]"
        if not isinstance(command, list) or not command:
            out.append(f"{where}: expected a nonempty argv array")
            continue
        argv: list[str] = []
        for position, word in enumerate(command):
            word = _string(word, f"{where}[{position}]", out)
            if "\0" in word:
                out.append(f"{where}[{position}]: argument carries a NUL byte")
                word = ""
            argv.append(word)
        commands.append(tuple(argv))
    return tuple(commands)


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:                                       # pragma: no cover
        return False                                         # the pattern caught it first


def _check(value: Any, label: str) -> tuple[list[str], Config]:
    """One pass: every problem, and the config the values resolve to.

    The resolved config is meaningless when the list is non-empty; `parse`
    raises there, and `problems` throws it away.
    """
    out: list[str] = []
    if not isinstance(value, dict):
        out.append(f"{label}: expected an object")
        value = {}
    else:
        _keys(value, CONFIG_KEYS, REQUIRED_CONFIG_KEYS, label, out)

    if "schema" in value and value["schema"] != CONFIG_SCHEMA:
        out.append(f"{label}.schema: expected {CONFIG_SCHEMA}")
    if "schema_version" in value:
        version = value["schema_version"]
        if isinstance(version, bool) or version != CONFIG_SCHEMA_VERSION:
            out.append(f"{label}.schema_version: expected {CONFIG_SCHEMA_VERSION}")
    source_id = (_string(value["source_id"], f"{label}.source_id", out)
                 if "source_id" in value else "")
    title = _string(value["title"], f"{label}.title", out) if "title" in value else ""
    lede = _string(value["lede"], f"{label}.lede", out) if "lede" in value else ""
    storage_key = (_string(value["storage_key"], f"{label}.storage_key", out)
                   if "storage_key" in value else DEFAULT_STORAGE_KEY)

    back_href, back_text = DEFAULT_BACK_LINK["href"], DEFAULT_BACK_LINK["text"]
    if "back_link" in value:
        back = _keys(value["back_link"], BACK_LINK_KEYS, BACK_LINK_KEYS,
                     f"{label}.back_link", out)
        back_href = _string(back.get("href"), f"{label}.back_link.href", out)
        back_text = _string(back.get("text"), f"{label}.back_link.text", out)

    strip_prefix = ""
    if "display" in value:
        display = _keys(value["display"], DISPLAY_KEYS, frozenset(), f"{label}.display", out)
        if "strip_prefix" in display:
            strip_prefix = _string(display["strip_prefix"], f"{label}.display.strip_prefix",
                                   out, allow_empty=True)

    file_name, cache_dir, route = "", DEFAULT_CACHE_DIR, ""
    if "pdf" in value:
        pdf = _keys(value["pdf"], PDF_KEYS, REQUIRED_PDF_KEYS, f"{label}.pdf", out)
        if "file_name" in pdf:
            file_name = _string(pdf["file_name"], f"{label}.pdf.file_name", out)
            if file_name and not _FILE_NAME.search(file_name):
                out.append(f"{label}.pdf.file_name: expected one plain file name: "
                           f"{file_name!r}")
                file_name = ""
        if "cache_dir" in pdf:
            cache_dir = _relative(pdf["cache_dir"], f"{label}.pdf.cache_dir", out,
                                  DEFAULT_CACHE_DIR)
        route = pdf.get("route", route_for(file_name) if file_name else "")
        if not isinstance(route, str) or not route:
            out.append(f"{label}.pdf.route: expected a nonempty string")
            route = ""
        elif not _ROUTE.search(route) or ".." in route:
            out.append(f"{label}.pdf.route: must start with /__, name a file and "
                       f"hold no ..: {route!r}")
            route = ""

    output_root, index = DEFAULT_OUTPUT_ROOT, DEFAULT_INDEX
    mount, link_text = DEFAULT_MOUNT, title
    if "site" in value:
        site = _keys(value["site"], SITE_KEYS, frozenset(), f"{label}.site", out)
        if "output_root" in site:
            output_root = _relative(site["output_root"], f"{label}.site.output_root", out,
                                    DEFAULT_OUTPUT_ROOT)
        if "index" in site:
            index = _relative(site["index"], f"{label}.site.index", out, DEFAULT_INDEX)
        if "mount" in site:
            mount = _relative(site["mount"], f"{label}.site.mount", out, DEFAULT_MOUNT)
        if "link_text" in site:
            link_text = _string(site["link_text"], f"{label}.site.link_text", out)

    commands: tuple[tuple[str, ...], ...] = ()
    if "build" in value:
        build = _keys(value["build"], BUILD_KEYS, frozenset(), f"{label}.build", out)
        if "commands" in build:
            commands = _argv_list(build["commands"], f"{label}.build.commands", out)

    host, port = DEFAULT_HOST, DEFAULT_PORT
    if "serve" in value:
        serve = _keys(value["serve"], SERVE_KEYS, frozenset(), f"{label}.serve", out)
        if "host" in serve:
            host = _string(serve["host"], f"{label}.serve.host", out)
            if host and not (_LOOPBACK.search(host) and _is_loopback(host)):
                out.append(f"{label}.serve.host: expected a loopback address "
                           f"(127.x.y.z or ::1), got {host!r}")
                host = DEFAULT_HOST
        if "port" in serve:
            candidate = serve["port"]
            if isinstance(candidate, int) and not isinstance(candidate, bool) \
                    and PORT_MIN <= candidate <= PORT_MAX:
                port = candidate
            else:
                out.append(f"{label}.serve.port: expected a port in "
                           f"{PORT_MIN}..{PORT_MAX}")

    return out, Config(
        source_id=source_id, title=title, lede=lede, back_link_href=back_href,
        back_link_text=back_text, strip_prefix=strip_prefix, storage_key=storage_key,
        file_name=file_name, cache_dir=cache_dir, route=route, output_root=output_root,
        index=index, mount=mount, link_text=link_text or title,
        build_commands=commands, host=host, port=port,
    )


def problems(value: Any, *, label: str = CONFIG_NAME) -> list[str]:
    """Every way *value* breaks section 3, as ``<path>: <message>`` lines.

    Empty when the config is valid.  Every problem is reported, not only the
    first: somebody repairing a generated file by hand should see the whole list.
    """
    return _check(value, label)[0]


def parse(value: Any, *, label: str = CONFIG_NAME) -> Config:
    """The config with its defaults filled in, or :class:`CfError`."""
    found, resolved = _check(value, label)
    if found:
        raise CfError(found[0], hint="; ".join(found[1:]) if len(found) > 1 else
                      f"section 3 of docs/overlay.md and "
                      f"templates/overlay/runtime/schema/{SCHEMA_FILE} state the rules")
    return resolved


def load(path: str | Path) -> Config:
    """Read and check `overlay.config.json` at *path*."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CfError(f"cannot read {path}: {exc}",
                      hint="`cf overlay init <companion>` writes it") from exc
    return parse(strict_json(raw, CONFIG_NAME))


def load_problems(path: str | Path) -> list[str]:
    """:func:`problems` for a file, with a read or parse failure as one line."""
    try:
        return problems(strict_json(Path(path).read_bytes(), CONFIG_NAME))
    except OSError as exc:
        return [f"{CONFIG_NAME}: cannot read {path}: {exc}"]
    except CfError as exc:
        return [exc.message]
