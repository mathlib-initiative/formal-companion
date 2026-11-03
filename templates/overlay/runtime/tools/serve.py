#!/usr/bin/env python3
"""Acquire, verify and loopback-serve the source PDF a generated overlay needs.

Ported from the hand-written PDF workflow of the first companion overlay this
template generalises: that script carried one book's sha256, byte count, URLs,
cache file name and route as constants, pinned one toolchain and one package
manifest, and ran one companion's build steps. Everything specific now comes
from `pdf-overlay/overlay.config.json` and the PDF subject ends of the
Literature Links annotations in the bundle `GENERATED.json` records (read
through `overlay_annotations.py`), both found from this file's own path.

`docs/overlay.md` section 5 is the contract; section 1 rule 4 is why
the PDF is cached under an ignored directory and served only on loopback at one
route, and section 1 rule 6 is why the acquisition hardening (HTTPS only,
redirects only to HTTPS, atomic install, no directory listings, no traversal)
is kept while the refusal of a symlinked executable is not. A config whose
`pdf.publish` names an open license (rule 4's exception) moves that route to
`/<mount>/<file_name>`, where `build_site.py` publishes the same verified bytes.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import functools
import hashlib
import http.server
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Iterator, NoReturn
import urllib.error
import urllib.parse
import urllib.request

# The siblings are imported from the companion's tree: never leave bytecode there.
sys.dont_write_bytecode = True
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import overlay_annotations  # noqa: E402  (beside this file in pdf-overlay/tools/)

try:                      # POSIX only; Windows is refused with one line below
    import fcntl
except ImportError:                                          # pragma: no cover
    fcntl = None                                             # type: ignore[assignment]


CONFIG_NAME = "overlay.config.json"
CONFIG_SCHEMA = "formal-companion/overlay-config"
BUILD_SITE_TOOL = "build_site.py"
LOCK_NAME = ".overlay-serve.lock"
USER_AGENT = "formal-companion-overlay/1"
DOWNLOAD_TIMEOUT = 30.0
CHUNK_SIZE = 128 * 1024
#: The ceiling on a download whose source record carries no `bytes`.
MAX_PDF_BYTES = 512 * 1024 * 1024
#: Media types a mirror may label the same bytes with; the sha256 is the gate.
PDF_MEDIA_TYPES = {"application/pdf", "application/octet-stream", "binary/octet-stream"}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ROUTE = re.compile(r"/__[A-Za-z0-9._~/-]*\Z")
FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*\Z")
#: `pdf.publish.license`: the open licenses under which the PDF may be published
#: with the site (section 3).
OPEN_LICENSES = ("Apache-2.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0", "MIT")
PUBLISHED_FILE_NAME = re.compile(r"\.pdf\Z")

CONFIG_KEYS = {
    "schema", "schema_version", "source_id", "title", "lede", "back_link",
    "display", "storage_key", "pdf", "site", "build", "serve",
}
REQUIRED_CONFIG_KEYS = {"schema", "schema_version", "source_id", "title", "pdf", "site"}
PDF_KEYS = {"file_name", "cache_dir", "route", "publish"}
PUBLISH_KEYS = {"license"}
SITE_KEYS = {"output_root", "index", "mount", "link_text"}
BUILD_KEYS = {"commands"}
SERVE_KEYS = {"host", "port"}
BACK_LINK_KEYS = {"href", "text"}
DISPLAY_KEYS = {"strip_prefix", "minimal"}

DEFAULT_CACHE_DIR = ".lake/overlay-pdf"
DEFAULT_OUTPUT_ROOT = ".lake/build/literate-html"
DEFAULT_INDEX = "index.html"
DEFAULT_MOUNT = "pdf-overlay"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


class OverlayError(RuntimeError):
    """A fail-closed error: the tool stops and says what to fix."""


class ChildFailure(OverlayError):
    """A build command that returned a status the tool does not accept."""

    def __init__(self, command: tuple[str, ...], returncode: int) -> None:
        super().__init__(f"build command exited {returncode}: {' '.join(command)}")
        self.command = command
        self.returncode = returncode


class Interrupted(BaseException):
    """A handled termination signal."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@dataclass(frozen=True)
class Config:
    """`overlay.config.json` as section 3 of the contract defines it."""

    source_id: str
    title: str
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
    publish_license: str = ""

    @property
    def published_path(self) -> str:
        """`<mount>/<file_name>` when the PDF is published with the site, else ""."""
        return f"{self.mount}/{self.file_name}" if self.publish_license else ""

    @property
    def route_prefix(self) -> str:
        """Requests under it that are not the route are refused; a published PDF's
        route is a file of the mount, so only `/__` is reserved then."""
        if self.publish_license:
            return "/__"
        return self.route.rsplit("/", 1)[0] + "/"


@dataclass(frozen=True)
class SourceRecord:
    """The PDF identity, from the `source` of the annotations."""

    id: str
    title: str
    sha256: str
    bytes: int | None
    urls: tuple[str, ...]


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stderr: str
    stdout: str


@dataclass
class ValidatedPdf:
    """An open descriptor on bytes that were verified after they were opened."""

    fd: int
    path: Path
    sha256: str
    size: int

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def fail(message: str) -> NoReturn:
    raise OverlayError(message)


def refuse_windows() -> None:
    """One line, not a traceback: the tool needs POSIX file and process calls."""
    if os.name == "nt" or fcntl is None:
        fail("this tool needs a POSIX system (Linux, macOS, WSL); Windows is not supported")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw: bytes, label: str) -> Any:
    """JSON with no duplicate keys and no NaN/Infinity."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"{label}: not UTF-8: {error}")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                fail(f"{label}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def bad_constant(token: str) -> NoReturn:
        fail(f"{label}: non-standard JSON constant {token}")

    def finite(token: str) -> float:
        value = float(token)
        if not math.isfinite(value):
            fail(f"{label}: number out of range: {token[:40]}")
        return value

    try:
        return json.loads(text, object_pairs_hook=unique_pairs, parse_constant=bad_constant, parse_float=finite)
    except OverlayError:
        raise
    except (ValueError, RecursionError) as error:
        # ValueError is a JSONDecodeError or an integer of more digits than
        # `int` converts; RecursionError, arrays or objects nested past the stack.
        fail(f"{label}: invalid JSON: {error}")


def lstat_mode(path: Path, label: str) -> int:
    try:
        return path.lstat().st_mode
    except OSError as error:
        fail(f"{label}: cannot inspect {path}: {error}")


def require_regular(path: Path, label: str) -> Path:
    mode = lstat_mode(path, label)
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        fail(f"{label}: expected an ordinary nonsymlink file: {path}")
    return path


def require_directory(path: Path, label: str, *, hint: str = "") -> Path:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        fail(f"{label}: {path} does not exist{hint}")
    except OSError as error:
        fail(f"{label}: cannot inspect {path}: {error}")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        fail(f"{label}: expected an ordinary nonsymlink directory: {path}")
    return path


def checked_bytes(path: Path, label: str) -> bytes:
    require_regular(path, label)
    try:
        return path.read_bytes()
    except OSError as error:
        fail(f"{label}: cannot read {path}: {error}")


def relative_subpath(value: Any, label: str) -> str:
    """A nonempty, normalised, traversal-free POSIX path inside the companion."""
    if not isinstance(value, str) or not value:
        fail(f"{label}: expected a nonempty relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or value != str(pure) or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        fail(f"{label}: path must be relative, normalised and traversal-free: {value!r}")
    return value


def resolve_relative(root: Path, relative: str) -> Path:
    return root.joinpath(*PurePosixPath(relative).parts)


def ensure_directory(root: Path, relative: str) -> Path:
    """Create every component of `relative` under `root`, refusing a symlink."""
    relative_subpath(relative, "directory")
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o755)
            except FileExistsError:                          # pragma: no cover
                pass
            except OSError as error:
                fail(f"cannot create {current}: {error}")
            mode = lstat_mode(current, "directory")
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            fail(f"not an ordinary directory: {current}")
    return current


def exact_keys(value: Any, allowed: set[str], required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label}: expected an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        fail(f"{label}: unknown keys {unknown}")
    missing = sorted(required - set(value))
    if missing:
        fail(f"{label}: missing keys {missing}")
    return value


def nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{label}: expected a nonempty string")
    return value


def validate_https_url(value: Any, label: str) -> str:
    """HTTPS, no credentials, no fragment, no port other than 443."""
    if not isinstance(value, str) or not value:
        fail(f"{label}: expected one HTTPS URL")
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        fail(f"{label}: URL carries whitespace or a control character")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        fail(f"{label}: invalid URL: {error}")
    if parsed.scheme != "https":
        fail(f"{label}: only https is fetched: {value}")
    if not parsed.hostname:
        fail(f"{label}: URL names no host: {value}")
    if parsed.username is not None or parsed.password is not None:
        fail(f"{label}: URL carries credentials")
    if parsed.fragment:
        fail(f"{label}: URL carries a fragment")
    if port not in {None, 443}:
        fail(f"{label}: URL names a port other than 443: {value}")
    return value


def argv_list(value: Any, label: str) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        fail(f"{label}: expected an array of argv arrays")
    commands: list[tuple[str, ...]] = []
    for index, command in enumerate(value):
        where = f"{label}[{index}]"
        if not isinstance(command, list) or not command:
            fail(f"{where}: expected a nonempty argv array")
        argv = []
        for position, word in enumerate(command):
            word = nonempty_string(word, f"{where}[{position}]")
            if "\0" in word:
                fail(f"{where}[{position}]: argument carries a NUL byte")
            argv.append(word)
        commands.append(tuple(argv))
    return tuple(commands)


def load_config(overlay: Path) -> Config:
    """Read and check `overlay.config.json` against section 3 of the contract."""
    path = overlay / CONFIG_NAME
    value = strict_json(checked_bytes(path, CONFIG_NAME), CONFIG_NAME)
    value = exact_keys(value, CONFIG_KEYS, REQUIRED_CONFIG_KEYS, CONFIG_NAME)
    if value["schema"] != CONFIG_SCHEMA:
        fail(f"{CONFIG_NAME}.schema: expected {CONFIG_SCHEMA}")
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        fail(f"{CONFIG_NAME}.schema_version: expected 1")
    source_id = nonempty_string(value["source_id"], f"{CONFIG_NAME}.source_id")
    title = nonempty_string(value["title"], f"{CONFIG_NAME}.title")
    if "lede" in value:
        nonempty_string(value["lede"], f"{CONFIG_NAME}.lede")
    if "storage_key" in value:
        nonempty_string(value["storage_key"], f"{CONFIG_NAME}.storage_key")
    if "back_link" in value:
        back = exact_keys(value["back_link"], BACK_LINK_KEYS, BACK_LINK_KEYS,
                          f"{CONFIG_NAME}.back_link")
        nonempty_string(back["href"], f"{CONFIG_NAME}.back_link.href")
        nonempty_string(back["text"], f"{CONFIG_NAME}.back_link.text")
    if "display" in value:
        display = exact_keys(value["display"], DISPLAY_KEYS, set(), f"{CONFIG_NAME}.display")
        if "minimal" in display and not isinstance(display["minimal"], bool):
            fail(f"{CONFIG_NAME}.display.minimal: expected true or false")

    pdf = exact_keys(value["pdf"], PDF_KEYS, {"file_name"}, f"{CONFIG_NAME}.pdf")
    file_name = nonempty_string(pdf["file_name"], f"{CONFIG_NAME}.pdf.file_name")
    if not FILE_NAME.fullmatch(file_name):
        fail(f"{CONFIG_NAME}.pdf.file_name: expected one plain file name: {file_name!r}")
    cache_dir = relative_subpath(pdf.get("cache_dir", DEFAULT_CACHE_DIR),
                                 f"{CONFIG_NAME}.pdf.cache_dir")
    route = pdf.get("route", f"/__overlay_pdf__/{file_name}")
    nonempty_string(route, f"{CONFIG_NAME}.pdf.route")
    if not route.startswith("/__") or ".." in route or not ROUTE.fullmatch(route) \
            or route.endswith("/"):
        fail(f"{CONFIG_NAME}.pdf.route: must start with /__, name a file and hold no ..")
    publish_license = publish_license_of(pdf, file_name)

    site = exact_keys(value["site"], SITE_KEYS, set(), f"{CONFIG_NAME}.site")
    output_root = relative_subpath(site.get("output_root", DEFAULT_OUTPUT_ROOT),
                                   f"{CONFIG_NAME}.site.output_root")
    index = relative_subpath(site.get("index", DEFAULT_INDEX), f"{CONFIG_NAME}.site.index")
    mount = relative_subpath(site.get("mount", DEFAULT_MOUNT), f"{CONFIG_NAME}.site.mount")
    link_text = nonempty_string(site.get("link_text", title), f"{CONFIG_NAME}.site.link_text")
    if publish_license:
        # The viewer fetches a published PDF from beside the config, `../<file_name>`.
        route = urllib.parse.quote(f"/{mount}/{file_name}")

    build = exact_keys(value.get("build", {}), BUILD_KEYS, set(), f"{CONFIG_NAME}.build")
    commands = argv_list(build.get("commands", []), f"{CONFIG_NAME}.build.commands")

    serve = exact_keys(value.get("serve", {}), SERVE_KEYS, set(), f"{CONFIG_NAME}.serve")
    host = nonempty_string(serve.get("host", DEFAULT_HOST), f"{CONFIG_NAME}.serve.host")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        fail(f"{CONFIG_NAME}.serve.host: expected a loopback address, got {host!r}")
    if not loopback:
        fail(f"{CONFIG_NAME}.serve.host: {host} is not a loopback address")
    port = serve.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        fail(f"{CONFIG_NAME}.serve.port: expected a port in 1..65535")

    return Config(
        source_id=source_id,
        title=title,
        file_name=file_name,
        cache_dir=cache_dir,
        route=route,
        output_root=output_root,
        index=index,
        mount=mount,
        link_text=link_text,
        build_commands=commands,
        host=host,
        port=port,
        publish_license=publish_license,
    )


def publish_license_of(pdf: dict[str, Any], file_name: str) -> str:
    """`pdf.publish.license`, or "" when the config does not publish the PDF."""
    if "publish" not in pdf:
        return ""
    publish = exact_keys(pdf["publish"], PUBLISH_KEYS, PUBLISH_KEYS, f"{CONFIG_NAME}.pdf.publish")
    found = publish["license"]
    if not isinstance(found, str) or found not in OPEN_LICENSES:
        fail(f"{CONFIG_NAME}.pdf.publish.license: expected one of {', '.join(OPEN_LICENSES)}, "
             f"got {found!r}")
    if not PUBLISHED_FILE_NAME.search(file_name):
        fail(f"{CONFIG_NAME}.pdf.file_name: a published PDF's file name ends in .pdf: {file_name!r}")
    return found


def load_source_record(overlay: Path, config: Config) -> SourceRecord:
    """The PDF identity: a subject end of the records whose PDF the config names.

    The end's `uri` and its citation's `URL` are the official addresses, then
    the overlay extension's `alternate_urls`; `sha256` is the end's, `bytes`
    the extension's and the title the citation's.
    """
    try:
        source = overlay_annotations.pdf_source(overlay, config.source_id)
    except overlay_annotations.AnnotationError as error:
        fail(str(error))
    label = f"source {config.source_id}"
    citation = source.get("citation") if isinstance(source.get("citation"), dict) else {}
    ext = overlay_annotations.extension(source)
    digest = source.get("sha256")
    if not isinstance(digest, str) or not HEX64.fullmatch(digest):
        fail(f"{label}: the end carries no lowercase sha256")
    size = ext.get("bytes")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        fail(f"{label}: bytes is not a nonnegative integer")
    if isinstance(size, int) and size > MAX_PDF_BYTES:
        fail(f"{label}: declares more than {MAX_PDF_BYTES} bytes")
    alternates = ext.get("alternate_urls", [])
    if not isinstance(alternates, list):
        fail(f"{label}: alternate_urls is not an array")
    urls: list[str] = []
    for where, candidate in [("uri", source.get("uri")), ("citation.URL", citation.get("URL")),
                             *((f"alternate_urls[{n}]", u) for n, u in enumerate(alternates))]:
        if where in ("uri", "citation.URL") and (candidate is None or (
                isinstance(candidate, str) and not urllib.parse.urlsplit(candidate).scheme)):
            continue                 # absent, or a relative reference: nothing to fetch
        url = validate_https_url(candidate, f"{label}.{where}")
        if url not in urls:
            urls.append(url)
    return SourceRecord(
        id=config.source_id,
        title=nonempty_string(citation.get("title"), f"{label}.citation.title"),
        sha256=digest,
        bytes=size,
        urls=tuple(urls),
    )


# ---------------------------------------------------------------------------
# the cached PDF
# ---------------------------------------------------------------------------

def open_flags(read_only: bool) -> int:
    flags = os.O_RDONLY if read_only else os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def validate_pdf_fd(fd: int, record: SourceRecord, label: str) -> str:
    """Verify the bytes behind an open descriptor, not behind a path."""
    try:
        info = os.fstat(fd)
    except OSError as error:
        fail(f"{label}: cannot fstat: {error}")
    if not stat.S_ISREG(info.st_mode):
        fail(f"{label}: not an ordinary file")
    size = info.st_size
    if record.bytes is not None and size != record.bytes:
        fail(f"{label}: expected {record.bytes} bytes, got {size}")
    if size > MAX_PDF_BYTES:
        fail(f"{label}: larger than the {MAX_PDF_BYTES}-byte ceiling")
    if os.pread(fd, 5, 0) != b"%PDF-":
        fail(f"{label}: does not begin with a %PDF- signature")
    tail = os.pread(fd, min(1024, size), max(0, size - min(1024, size)))
    if b"%%EOF" not in tail:
        fail(f"{label}: terminal %%EOF marker is absent")
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        block = os.pread(fd, min(CHUNK_SIZE, size - offset), offset)
        if not block:
            fail(f"{label}: short read at byte {offset}")
        digest.update(block)
        offset += len(block)
    actual = digest.hexdigest()
    if actual != record.sha256:
        fail(f"{label}: sha256 is {actual}; the source record says {record.sha256}")
    return actual


def open_validated_pdf(path: Path, record: SourceRecord,
                       label: str = "PDF cache") -> ValidatedPdf:
    require_regular(path, label)
    try:
        fd = os.open(path, open_flags(True))
    except OSError as error:
        fail(f"{label}: cannot open {path}: {error}")
    try:
        actual = validate_pdf_fd(fd, record, label)
        return ValidatedPdf(fd=fd, path=path, sha256=actual, size=os.fstat(fd).st_size)
    except BaseException:
        os.close(fd)
        raise


def validate_pdf_path(path: Path, record: SourceRecord, label: str = "PDF cache") -> str:
    opened = open_validated_pdf(path, record, label)
    try:
        return opened.sha256
    finally:
        opened.close()


def fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as error:
        fail(f"cannot fsync directory {path}: {error}")


def temp_pattern(file_name: str) -> re.Pattern[str]:
    return re.compile(re.escape(f".{file_name}") + r"\.[1-9][0-9]*\.[0-9a-f]{16}\.tmp\Z")


def cleanup_stale_temporaries(cache_dir: Path, file_name: str) -> None:
    pattern = temp_pattern(file_name)
    for entry in sorted(os.scandir(cache_dir), key=lambda item: item.name):
        if not pattern.fullmatch(entry.name):
            continue
        if not stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
            fail(f"unsafe stale acquisition temporary: {entry.path}")
        try:
            os.unlink(entry.path)
        except OSError as error:                             # pragma: no cover
            fail(f"cannot remove stale acquisition temporary {entry.path}: {error}")


class HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to an HTTPS URL, never twice to the same one."""

    max_hops = 5

    def __init__(self, start: str) -> None:
        super().__init__()
        self.seen = [start]

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        target = urllib.parse.urljoin(req.full_url, newurl)
        validate_https_url(target, "redirect target")
        if len(self.seen) > self.max_hops:
            fail("download redirect limit exceeded")
        if target in self.seen:
            fail(f"download redirect loop: {target}")
        self.seen.append(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


def response_media_type(headers: Any) -> str:
    if hasattr(headers, "get_content_type"):
        return str(headers.get_content_type()).casefold()
    return str(headers.get("Content-Type", "")).split(";", 1)[0].strip().casefold()


def create_temp_path(cache_dir: Path, file_name: str,
                     token_factory: Callable[[], str]) -> Path:
    token = token_factory()
    if not re.fullmatch(r"[0-9a-f]{16}", token):
        fail("temporary token provider did not return 16 lowercase hex digits")
    return cache_dir / f".{file_name}.{os.getpid()}.{token}.tmp"


def download_to(temporary: Path, url: str, record: SourceRecord, opener: Any) -> None:
    """Stream one URL into `temporary`, refusing anything but the exact bytes."""
    try:
        fd = os.open(temporary, open_flags(False), 0o600)
    except OSError as error:
        fail(f"cannot create acquisition temporary {temporary}: {error}")
    ceiling = record.bytes if record.bytes is not None else MAX_PDF_BYTES
    try:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/pdf", "User-Agent": USER_AGENT},
            method="GET",
        )
        try:
            with opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response, \
                    os.fdopen(fd, "wb", closefd=True) as stream:
                fd = -1
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                if status != 200:
                    fail(f"request returned HTTP {status}")
                final_url = response.geturl()
                validate_https_url(final_url, "final response URL")
                media_type = response_media_type(response.headers)
                if media_type and media_type not in PDF_MEDIA_TYPES:
                    fail(f"response media type is {media_type}, not a PDF")
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        declared = int(length)
                    except (TypeError, ValueError):
                        fail(f"invalid Content-Length {length!r}")
                    if declared > ceiling:
                        fail(f"Content-Length is {declared}; expected at most {ceiling}")
                    if record.bytes is not None and declared != record.bytes:
                        fail(f"Content-Length is {declared}; the source record "
                             f"says {record.bytes}")
                total = 0
                while True:
                    block = response.read(CHUNK_SIZE)
                    if not block:
                        break
                    total += len(block)
                    if total > ceiling:
                        fail(f"response exceeds the expected {ceiling} bytes")
                    stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
                if record.bytes is not None and total != record.bytes:
                    fail(f"response is {total} bytes; the source record says {record.bytes}")
        except OverlayError:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as error:
            fail(f"HTTPS acquisition failed: {error}")
    finally:
        if fd >= 0:
            os.close(fd)


def acquire_pdf(
    cache_path: Path,
    record: SourceRecord,
    *,
    opener: Any | None = None,
    token_factory: Callable[[], str] = lambda: secrets.token_hex(8),
) -> str:
    """Fetch, verify and atomically install the PDF; try every URL in turn."""
    if not record.urls:
        fail(f"source {record.id} names no https url, so the PDF cannot be fetched; "
             f"place the verified file at {cache_path} yourself")
    cache_dir = cache_path.parent
    problems: list[str] = []
    for url in record.urls:
        temporary = create_temp_path(cache_dir, cache_path.name, token_factory)
        try:
            handler = HttpsOnlyRedirectHandler(url)
            current = urllib.request.build_opener(handler) if opener is None else opener
            try:
                download_to(temporary, url, record, current)
                validate_pdf_path(temporary, record, "downloaded PDF")
            except OverlayError as error:
                problems.append(f"{url}: {error}")
                continue
            try:
                os.chmod(temporary, 0o644)
                os.replace(temporary, cache_path)
            except OSError as error:
                fail(f"cannot atomically install the verified PDF cache: {error}")
            fsync_directory(cache_dir)
            return validate_pdf_path(cache_path, record)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:                                  # pragma: no cover
                pass
    fail("no URL in the source record produced the expected bytes: " + "; ".join(problems))


def cache_state(
    root: Path,
    config: Config,
    record: SourceRecord,
    *,
    refresh: bool = False,
    opener: Any | None = None,
    token_factory: Callable[[], str] = lambda: secrets.token_hex(8),
) -> tuple[str, str]:
    """("reused" | "refreshed" | "acquired", sha256) for the cached PDF."""
    cache_dir = ensure_directory(root, config.cache_dir)
    cleanup_stale_temporaries(cache_dir, config.file_name)
    cache_path = cache_dir / config.file_name
    exists = cache_path.exists() or cache_path.is_symlink()
    if exists:
        mode = lstat_mode(cache_path, "PDF cache")
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            fail(f"PDF cache is not an ordinary nonsymlink file: {cache_path}")
        if not refresh:
            try:
                actual = validate_pdf_path(cache_path, record)
            except OverlayError as error:
                print(f"cache=stale reason={error}")
            else:
                return "reused", actual
    actual = acquire_pdf(cache_path, record, opener=opener, token_factory=token_factory)
    return ("refreshed" if exists else "acquired"), actual


@contextmanager
def overlay_lock(cache_dir: Path) -> Iterator[None]:
    """One acquisition at a time, so two runs cannot race on the cache."""
    lock_path = cache_dir / LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as error:
        fail(f"cannot open the overlay lock {lock_path}: {error}")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):           # pragma: no cover
            fail(f"the overlay lock is not an ordinary file: {lock_path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as error:                             # pragma: no cover
            fail(f"cannot acquire the overlay lock: {error}")
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# the optional build
# ---------------------------------------------------------------------------

def child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_NO_LAZY_FETCH"] = "1"
    return environment


def terminate_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    for sent, timeout in ((signal.SIGINT, 5.0), (signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(process.pid, sent)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + timeout
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if process.poll() is not None:
            return


def run_process(
    command: Iterable[str],
    root: Path,
    *,
    capture: bool = False,
    allowed: set[int] | None = None,
    environment: dict[str, str] | None = None,
) -> ProcessResult:
    """Run one argv with no shell, reaping its process group on interruption."""
    argv = tuple(str(item) for item in command)
    allowed = {0} if allowed is None else allowed
    try:
        process = subprocess.Popen(
            argv,
            cwd=root,
            env=child_environment() if environment is None else environment,
            start_new_session=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
        )
    except OSError as error:
        fail(f"cannot execute {argv[0]}: {error}")
    try:
        stdout, stderr = process.communicate()
    except BaseException:
        terminate_process_group(process)
        process.wait()
        raise
    result = ProcessResult(process.returncode, stderr or "", stdout or "")
    if result.returncode not in allowed:
        raise ChildFailure(argv, result.returncode)
    return result


def resolve_executable(name: str, *, path: str | None = None) -> str:
    """A tool on PATH, through realpath; a symlink is followed, not refused."""
    found = shutil.which(name, path=path)
    if found is None:
        fail(f"build command is unavailable: {name}")
    resolved = Path(os.path.realpath(found))
    mode = lstat_mode(resolved, f"{name} executable")
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or not os.access(resolved, os.X_OK):
        fail(f"{name}: expected a regular executable file, got {resolved}")
    return str(resolved)


def run_build(
    overlay: Path,
    root: Path,
    config: Config,
    *,
    runner: Callable[..., ProcessResult] = run_process,
) -> list[tuple[str, ...]]:
    """Every argv in `config.build.commands`, then `build_site.py`."""
    executed: list[tuple[str, ...]] = []
    for argv in config.build_commands:
        command = (resolve_executable(argv[0]), *argv[1:])
        print(f"build command={' '.join(argv)}")
        runner(command, root)
        executed.append(command)
    site_tool = require_regular(overlay / "tools" / BUILD_SITE_TOOL, BUILD_SITE_TOOL)
    command = (sys.executable, "-B", str(site_tool))
    print(f"build command={BUILD_SITE_TOOL}")
    runner(command, root)
    executed.append(command)
    return executed


# ---------------------------------------------------------------------------
# the loopback server
# ---------------------------------------------------------------------------

def assert_site_servable(site: Path, published: str = "") -> Path:
    """No symlink and no PDF under the served root, but the `published` one: rule 4 of section 1."""
    pending = [site]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:                             # pragma: no cover
            fail(f"cannot inspect the site at {directory}: {error}")
        for entry in entries:
            mode = entry.stat(follow_symlinks=False).st_mode
            path = Path(entry.path)
            if stat.S_ISLNK(mode):
                fail(f"the site contains a symlink: {path.relative_to(site)}")
            if entry.name.casefold().endswith(".pdf") \
                    and path.relative_to(site).as_posix() != published:
                fail(f"the site contains a PDF, which is served only at its route: "
                     f"{path.relative_to(site)}")
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif not stat.S_ISREG(mode):
                fail(f"the site contains a non-regular file: {path.relative_to(site)}")
    return site


class OverlayHTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = False

    def __init__(self, server_address, handler):             # type: ignore[no-untyped-def]
        self.pdf: ValidatedPdf | None = None
        self.route = ""
        self.route_prefix = ""
        self.download_name = ""
        super().__init__(server_address, handler)


class OverlayRequestHandler(http.server.SimpleHTTPRequestHandler):
    """The site, plus the cached PDF at exactly one route. No listings."""

    server: OverlayHTTPServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10.0)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def list_directory(self, path):                          # type: ignore[no-untyped-def]
        self.send_error(404, "directory listing disabled")
        return None

    def _request_parts(self) -> urllib.parse.SplitResult:
        return urllib.parse.urlsplit(self.path)

    def _is_pdf_route(self) -> bool:
        parsed = self._request_parts()
        # decoded, since a browser sends a mount's `+`, `(` or `@` as it is
        return (urllib.parse.unquote(parsed.path) == urllib.parse.unquote(self.server.route)
                and not parsed.query and not parsed.fragment)

    def _reserved_near_miss(self) -> bool:
        path = self._request_parts().path
        return path.startswith(self.server.route_prefix) or path.startswith("/__")

    def send_head(self):                                     # type: ignore[no-untyped-def]
        decoded = urllib.parse.unquote(self._request_parts().path)
        if ".." in PurePosixPath(decoded).parts:
            self.send_error(404, "path traversal refused")
            return None
        root = Path(self.directory)
        target = Path(self.translate_path(self.path))
        try:
            resolved = target.resolve()
            site = root.resolve()
        except OSError:                                      # pragma: no cover
            self.send_error(404, "unreadable path")
            return None
        if resolved != site and site not in resolved.parents:
            self.send_error(404, "path outside the served site")
            return None
        if resolved.is_file() and resolved.suffix.casefold() == ".pdf":
            self.send_error(404, "the PDF is served only at its route")
            return None
        return super().send_head()

    def _serve_pdf(self, *, head: bool) -> None:
        if self.headers.get("Range") is not None:
            self.send_error(416, "range requests are not supported")
            return
        pdf = self.server.pdf
        if pdf is None or pdf.fd < 0:
            self.send_error(503, "verified PDF unavailable")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(pdf.size))
        self.send_header(
            "Content-Disposition", f'inline; filename="{self.server.download_name}"')
        self.end_headers()
        if head:
            return
        offset = 0
        try:
            while offset < pdf.size:
                block = os.pread(pdf.fd, min(CHUNK_SIZE, pdf.size - offset), offset)
                if not block:                                # pragma: no cover
                    raise OSError(errno.EIO, "short PDF cache read")
                self.wfile.write(block)
                offset += len(block)
        except (BrokenPipeError, ConnectionResetError):       # pragma: no cover
            return

    def do_GET(self) -> None:
        if self._is_pdf_route():
            self._serve_pdf(head=False)
        elif self._reserved_near_miss():
            self.send_error(404, "unknown reserved route")
        else:
            super().do_GET()

    def do_HEAD(self) -> None:
        if self._is_pdf_route():
            self._serve_pdf(head=True)
        elif self._reserved_near_miss():
            self.send_error(404, "unknown reserved route")
        else:
            super().do_HEAD()


def reserve_server(site_root: Path, config: Config, port: int) -> OverlayHTTPServer:
    """Bind the loopback socket first, so a busy port stops the run at once."""
    handler = functools.partial(OverlayRequestHandler, directory=str(site_root))
    try:
        server = OverlayHTTPServer((config.host, port), handler)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            fail(f"http://{config.host}:{port} is already in use; stop the other "
                 f"server or pass --port")
        fail(f"cannot bind loopback http://{config.host}:{port}: {error}")
    server.route = config.route
    server.route_prefix = config.route_prefix
    server.download_name = config.file_name
    return server


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

def overlay_from_script() -> Path:
    """`pdf-overlay/` from this file's own path, never from the cwd."""
    script = Path(os.path.abspath(__file__))
    require_regular(script, "overlay serve tool")
    overlay = script.parent.parent
    require_directory(overlay, "overlay directory")
    return overlay


def positive_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be an integer")
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be in 1..65535")
    return port


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("--port", type=positive_port, default=None,
                        help="override the config's serve.port")
    result.add_argument("--refresh-pdf", action="store_true",
                        help="re-acquire the PDF even when the cached bytes verify")
    result.add_argument("--build", action="store_true",
                        help="run the config's build commands, then build_site.py")
    result.add_argument("--build-only", action="store_true",
                        help="build as --build does and stop before serving")
    return result


def install_signal_handlers() -> dict[int, Any]:
    previous: dict[int, Any] = {}

    def interrupted(signum, frame):                          # type: ignore[no-untyped-def]
        raise Interrupted(signum)

    for name in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is None:                                   # pragma: no cover
            continue
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupted)
    return previous


def restore_signal_handlers(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def execute(arguments: argparse.Namespace, overlay: Path) -> int:
    refuse_windows()
    root = overlay.parent
    config = load_config(overlay)
    record = load_source_record(overlay, config)
    building = bool(arguments.build or arguments.build_only)
    serving = not arguments.build_only
    port = config.port if arguments.port is None else arguments.port
    site_root = resolve_relative(root, config.output_root)
    server: OverlayHTTPServer | None = None
    pdf: ValidatedPdf | None = None
    if serving:
        server = reserve_server(site_root, config, port)
    try:
        cache_dir = ensure_directory(root, config.cache_dir)
        with overlay_lock(cache_dir):
            declared = "unknown" if record.bytes is None else record.bytes
            print(f"source_id={record.id}")
            print(f"expected_sha256={record.sha256} expected_bytes={declared}")
            for url in record.urls:
                print(f"source_url={url}")
            state, actual = cache_state(
                root, config, record, refresh=arguments.refresh_pdf)
            print(f"cache={state} path={config.cache_dir}/{config.file_name}")
            print(f"actual_sha256={actual}")
            if building:
                run_build(overlay, root, config)
            if not serving:
                # the site goes up as it is, so hold it to what serving would
                assert_site_servable(site_root, config.published_path)
                where = (f"is published at {config.published_path} under {config.publish_license}"
                         if config.publish_license else "stayed out of the site")
                print(f"PASS overlay build complete; the PDF {where}")
                return 0
            assert server is not None
            require_directory(site_root, "site output root",
                              hint="; build the site first (--build)")
            assert_site_servable(site_root, config.published_path)
            pdf = open_validated_pdf(cache_dir / config.file_name, record)
            server.pdf = pdf
            base = f"http://{config.host}:{port}"
            print(f"served_root={base}/")
            print(f"served_viewer={base}/{config.mount}/viewer/index.html")
            print(f"served_pdf_route={base}{config.route}")
            print("Press Ctrl+C to stop the loopback-only server.")
            server.serve_forever(poll_interval=0.2)
            return 0
    finally:
        if server is not None:
            server.server_close()
        if pdf is not None:
            pdf.close()


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    previous = install_signal_handlers()
    try:
        try:
            return execute(arguments, overlay_from_script())
        except KeyboardInterrupt:
            print("STOP interrupted; children and sockets cleaned", file=sys.stderr)
            return 128 + signal.SIGINT
        except Interrupted as interrupted:
            print(f"STOP interrupted by signal {interrupted.signum}; children and "
                  f"sockets cleaned", file=sys.stderr)
            return 128 + interrupted.signum
        except ChildFailure as error:
            print(f"STOP {error}", file=sys.stderr)
            if 0 < error.returncode < 256:
                return error.returncode
            if error.returncode < 0 and -error.returncode < 128:
                return 128 - error.returncode
            return 1
        except OverlayError as error:
            print(f"STOP {error}", file=sys.stderr)
            return 1
    finally:
        restore_signal_handlers(previous)


if __name__ == "__main__":
    raise SystemExit(main())
