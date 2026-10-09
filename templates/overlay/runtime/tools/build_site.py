#!/usr/bin/env python3
"""Copy a generated overlay into a built site and link it from the site index.

Ported from the hand-written combined-site builder of the first companion
overlay this template generalises: that script pinned one toolchain, one
package manifest, one file inventory, one link text and one set of validators,
and refused to run against anything else. Everything specific now comes from
`pdf-overlay/overlay.config.json` and `GENERATED.json`, both found from this
file's own path; the inventory is whatever `cf overlay sync` copied under
`viewer/` and `vendor/`.

`docs/overlay.md` section 5 is the contract -- copy the viewer, the config,
`GENERATED.json` and the annotation bundle into `<output_root>/<mount>/` and
the Literature Links annotation files into
`<output_root>/<annotations directory>/<section>/`. Their relative target
references are relative to this published site layout (`../../<page>` from a
section folder is a page of `<output_root>`), not to the repository, where
they resolve to nothing; replace a previous copy atomically; insert or replace one marked
link block in the index; write `receipt.json`. It never runs the build:
`serve.py --build` does that first.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import html
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import secrets
import shutil
import stat
import sys
from typing import Any, NoReturn

# The siblings are imported from the companion's tree: never leave bytecode there.
sys.dont_write_bytecode = True
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import overlay_annotations  # noqa: E402  (beside this file in pdf-overlay/tools/)


CONFIG_NAME = "overlay.config.json"
GENERATED_NAME = "GENERATED.json"
CONFIG_SCHEMA = "formal-companion/overlay-config"
RECEIPT_SCHEMA = "formal-companion/overlay-site-receipt"
RECEIPT_NAME = "receipt.json"
#: The directories of the generated runtime a site needs, in copy order.
RUNTIME_ROOTS = ("viewer", "vendor")
#: The files the viewer reads at run time beside the bundle: the config, and the record.
INSTANCE_FILES = (CONFIG_NAME, GENERATED_NAME)
LINK_START = "<!-- formal-companion-overlay-link -->"
LINK_END = "<!-- /formal-companion-overlay-link -->"
LINK_CLASS = "formal-companion-overlay-link"
BODY_END = re.compile(r"</body\s*>", re.IGNORECASE)
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*\Z")

CONFIG_KEYS = {
    "schema", "schema_version", "source_id", "title", "lede", "back_link",
    "display", "storage_key", "pdf", "site", "build", "serve",
}
REQUIRED_CONFIG_KEYS = {"schema", "schema_version", "source_id", "title", "pdf", "site"}
PDF_KEYS = {"file_name", "cache_dir", "route"}
SITE_KEYS = {"output_root", "index", "mount", "link_text"}
BUILD_KEYS = {"commands"}
SERVE_KEYS = {"host", "port"}
BACK_LINK_KEYS = {"href", "text"}
DISPLAY_KEYS = {"strip_prefix"}

DEFAULT_CACHE_DIR = ".lake/overlay-pdf"
DEFAULT_OUTPUT_ROOT = ".lake/build/literate-html"
DEFAULT_INDEX = "index.html"
DEFAULT_MOUNT = "pdf-overlay"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


class BuildError(RuntimeError):
    """A fail-closed integration error: the tool stops and says what to fix."""


@dataclass(frozen=True)
class Config:
    """The fields of `overlay.config.json` this tool needs (section 3)."""

    source_id: str
    title: str
    output_root: str
    index: str
    mount: str
    link_text: str


def fail(message: str) -> NoReturn:
    raise BuildError(message)


def refuse_windows() -> None:
    """One line, not a traceback: the tool needs POSIX file semantics."""
    if os.name == "nt":
        fail("this tool needs a POSIX system (Linux, macOS, WSL); Windows is not supported")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


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

    try:
        return json.loads(text, object_pairs_hook=unique_pairs, parse_constant=bad_constant)
    except BuildError:
        raise
    except json.JSONDecodeError as error:
        fail(f"{label}: invalid JSON: {error}")


def lstat_mode(path: Path, label: str) -> int:
    try:
        return path.lstat().st_mode
    except OSError as error:
        fail(f"{label}: cannot inspect {path}: {error}")


def require_regular(path: Path, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        fail(f"{label}: {path} does not exist")
    except OSError as error:                                 # pragma: no cover
        fail(f"{label}: cannot inspect {path}: {error}")
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        fail(f"{label}: expected an ordinary nonsymlink file: {path}")
    return path


def require_directory(path: Path, label: str, *, hint: str = "") -> Path:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        fail(f"{label}: {path} does not exist{hint}")
    except OSError as error:                                 # pragma: no cover
        fail(f"{label}: cannot inspect {path}: {error}")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        fail(f"{label}: expected an ordinary nonsymlink directory: {path}")
    return path


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


def load_config(overlay: Path) -> Config:
    """Read and check `overlay.config.json` against section 3 of the contract.

    Every rule section 3 states is checked here, not only the keys this tool
    uses: a site built from a config `serve.py` would refuse is a site nobody
    can serve.
    """
    path = overlay / CONFIG_NAME
    value = strict_json(require_regular(path, CONFIG_NAME).read_bytes(), CONFIG_NAME)
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
        exact_keys(value["display"], DISPLAY_KEYS, set(), f"{CONFIG_NAME}.display")

    pdf = exact_keys(value["pdf"], PDF_KEYS, {"file_name"}, f"{CONFIG_NAME}.pdf")
    file_name = nonempty_string(pdf["file_name"], f"{CONFIG_NAME}.pdf.file_name")
    if not FILE_NAME.fullmatch(file_name):
        fail(f"{CONFIG_NAME}.pdf.file_name: expected one plain file name: {file_name!r}")
    relative_subpath(pdf.get("cache_dir", DEFAULT_CACHE_DIR), f"{CONFIG_NAME}.pdf.cache_dir")
    route = nonempty_string(pdf.get("route", f"/__overlay_pdf__/{file_name}"),
                            f"{CONFIG_NAME}.pdf.route")
    if not route.startswith("/__") or ".." in route or route.endswith("/"):
        fail(f"{CONFIG_NAME}.pdf.route: must start with /__, name a file and hold no ..")

    site = exact_keys(value["site"], SITE_KEYS, set(), f"{CONFIG_NAME}.site")
    output_root = relative_subpath(site.get("output_root", DEFAULT_OUTPUT_ROOT),
                                   f"{CONFIG_NAME}.site.output_root")
    index = relative_subpath(site.get("index", DEFAULT_INDEX), f"{CONFIG_NAME}.site.index")
    mount = relative_subpath(site.get("mount", DEFAULT_MOUNT), f"{CONFIG_NAME}.site.mount")
    link_text = nonempty_string(site.get("link_text", title), f"{CONFIG_NAME}.site.link_text")

    build = exact_keys(value.get("build", {}), BUILD_KEYS, set(), f"{CONFIG_NAME}.build")
    commands = build.get("commands", [])
    if not isinstance(commands, list):
        fail(f"{CONFIG_NAME}.build.commands: expected an array of argv arrays")
    for position, command in enumerate(commands):
        if not isinstance(command, list) or not command or not all(
            isinstance(word, str) and word for word in command
        ):
            fail(f"{CONFIG_NAME}.build.commands[{position}]: expected a nonempty argv array")

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
        output_root=output_root,
        index=index,
        mount=mount,
        link_text=link_text,
    )


def collect_annotations(overlay: Path, config: Config) -> tuple[str, str, dict[str, bytes]]:
    """``(directory, bundle path, {path in the directory: bytes})``: the files the bundle holds.

    The bundle must have its recorded sha256 and hold exactly the files on
    disk; anything else is refused rather than published: the viewer checks
    the same sha256 and would refuse it later.
    """
    try:
        block = overlay_annotations.annotations_block(overlay)
        entries = overlay_annotations.read_bundle(overlay, block)
        overlay_annotations.pdf_source(overlay, config.source_id)
    except overlay_annotations.AnnotationError as error:
        fail(str(error))
    files, problems = overlay_annotations.disk_files(overlay.parent, block["directory"])
    problems += overlay_annotations.bundle_problems(entries, files)
    if problems:
        fail(f"{problems[0]}; re-run `cf overlay sync`")
    if not files:
        fail(f"{GENERATED_NAME} records no annotation file; `cf overlay export` writes them")
    prefix = block["directory"] + "/"
    return block["directory"], block["bundle"]["path"], {p[len(prefix):]: raw for p, raw in files.items()}


# ---------------------------------------------------------------------------
# what is copied
# ---------------------------------------------------------------------------

def collect_runtime(overlay: Path, bundle: str) -> dict[str, bytes]:
    """{mount-relative path: bytes} for the runtime, the two instance files and the bundle."""
    contents: dict[str, bytes] = {}
    for root_name in RUNTIME_ROOTS:
        root = require_directory(overlay / root_name, f"runtime directory {root_name}")
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError as error:                         # pragma: no cover
                fail(f"cannot inspect {directory}: {error}")
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(overlay).as_posix()
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    fail(f"the generated runtime contains a symlink: {relative}")
                if stat.S_ISDIR(mode):
                    pending.append(path)
                elif stat.S_ISREG(mode):
                    try:
                        contents[relative] = path.read_bytes()
                    except OSError as error:                 # pragma: no cover
                        fail(f"cannot read {relative}: {error}")
                else:
                    fail(f"the generated runtime contains a non-regular file: {relative}")
    for name in (*INSTANCE_FILES, bundle):
        contents[name] = require_regular(overlay / name, name).read_bytes()
    if not any(name.startswith("viewer/") for name in contents):
        fail("the generated runtime holds no viewer file to publish")
    return contents


def link_href(config: Config) -> str:
    """The viewer's href as the site index must spell it."""
    target = posixpath.join(config.mount, "viewer", "index.html")
    start = posixpath.dirname(config.index) or "."
    return posixpath.relpath(target, start)


def link_block(href: str, text: str, indent: str = "    ") -> str:
    return (f"{indent}{LINK_START}\n"
            f'{indent}<p class="{LINK_CLASS}"><a href="{html.escape(href, quote=True)}">'
            f"{html.escape(text)}</a></p>\n"
            f"{indent}{LINK_END}")


def linked_index(raw: bytes, href: str, text: str) -> bytes:
    """The index with exactly one link block, inserted or replaced in place.

    Replacing and inserting end in the same bytes, so a second run over its own
    output changes nothing: the block always starts on a line of its own, behind
    whatever indentation the insertion point already sat behind.
    """
    try:
        document = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"the site index is not UTF-8: {error}")
    starts, ends = document.count(LINK_START), document.count(LINK_END)
    if starts > 1 or ends > 1:
        fail("the site index carries more than one overlay link marker")
    if bool(starts) != bool(ends):
        fail("the site index carries an unclosed overlay link marker")
    if starts == 1:
        if document.index(LINK_START) > document.index(LINK_END):
            fail("the site index carries the overlay link markers in the wrong order")
        head, _, rest = document.partition(LINK_START)
        _, _, tail = rest.partition(LINK_END)
    else:
        closings = list(BODY_END.finditer(document))
        at = closings[-1].start() if closings else len(document)
        head, tail = document[:at], document[at:]
    line_start = head.rfind("\n") + 1
    trailing = head[line_start:]
    if trailing.strip():
        indent, prefix = "", head + "\n"
    else:
        indent, prefix = trailing, head[:line_start]
    suffix = tail if starts == 1 else "\n" + indent + tail
    return (prefix + link_block(href, text, indent) + suffix).encode("utf-8")


def receipt_value(config: Config, contents: dict[str, bytes], href: str,
                  index_raw: bytes, directory: str, annotations: dict[str, bytes]) -> dict[str, Any]:
    """Every file this run wrote, by sha256: the contract's `receipt.json`."""
    return {
        "annotations": {
            "directory": directory,
            "files": [{"bytes": len(raw), "path": name, "sha256": sha256(raw)}
                      for name, raw in sorted(annotations.items())],
        },
        "files": [
            {"bytes": len(raw), "path": path, "sha256": sha256(raw)}
            for path, raw in sorted(contents.items())
        ],
        "index": {
            "bytes": len(index_raw),
            "href": href,
            "marker": LINK_START,
            "path": config.index,
            "sha256": sha256(index_raw),
        },
        "mount": config.mount,
        "schema": RECEIPT_SCHEMA,
        "schema_version": 1,
        "source_id": config.source_id,
    }


# ---------------------------------------------------------------------------
# writing it out
# ---------------------------------------------------------------------------

def inspect_output_component(path: Path, label: str) -> int | None:
    """The mode of an existing component, refusing a symlink; None when absent."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as error:                                 # pragma: no cover
        fail(f"{label}: cannot inspect {path}: {error}")
    if stat.S_ISLNK(mode):
        fail(f"output escape: symlink component forbidden: {path}")
    return mode


def ensure_output_directory(root: Path, parts: tuple[str, ...]) -> Path:
    current = root
    for part in parts:
        current /= part
        mode = inspect_output_component(current, "output directory")
        if mode is None:
            try:
                current.mkdir(mode=0o755)
            except OSError as error:                         # pragma: no cover
                fail(f"cannot create output directory {current}: {error}")
        elif not stat.S_ISDIR(mode):
            fail(f"output escape: not an ordinary directory: {current}")
    return current


def write_atomic(path: Path, raw: bytes) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    fd = -1
    try:
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(temporary, flags, 0o644)
        except OSError as error:                             # pragma: no cover
            fail(f"cannot create {temporary}: {error}")
        with os.fdopen(fd, "wb", closefd=True) as stream:
            fd = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        except OSError as error:                             # pragma: no cover
            fail(f"cannot replace {path}: {error}")
    finally:
        if fd >= 0:                                          # pragma: no cover
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:                                      # pragma: no cover
            pass


def write_tree(staging: Path, contents: dict[str, bytes]) -> None:
    for relative, raw in sorted(contents.items()):
        parts = PurePosixPath(relative).parts
        parent = ensure_output_directory(staging, parts[:-1])
        write_atomic(parent / parts[-1], raw)


def staging_pattern(leaf: str) -> re.Pattern[str]:
    return re.compile(re.escape(f".{leaf}") + r"\.[1-9][0-9]*\.[0-9a-f]{16}\.(?:new|old)\Z")


def clear_stale_staging(parent: Path, leaf: str) -> None:
    """A crashed run leaves a `.<mount>.<pid>.<token>.new` tree; it is ours."""
    pattern = staging_pattern(leaf)
    for entry in sorted(os.scandir(parent), key=lambda item: item.name):
        if not pattern.fullmatch(entry.name):
            continue
        mode = entry.stat(follow_symlinks=False).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            fail(f"output escape: unsafe staging entry {entry.path}")
        try:
            shutil.rmtree(entry.path)
        except OSError as error:                             # pragma: no cover
            fail(f"cannot remove the stale staging tree {entry.path}: {error}")


def only_annotations(target: Path) -> None:
    """Refuse to replace a directory of the site that is not an annotation directory."""
    for entry in os.scandir(target):
        mode = entry.stat(follow_symlinks=False).st_mode
        if not (stat.S_ISDIR(mode) and overlay_annotations.SECTION_DIR.match(entry.name)):
            fail(f"the built site already has {target}, which holds {entry.name}; the annotations "
                 f"are published there, so it may hold only <section>/<name>.json files")
        for child in os.scandir(entry.path):
            if not (stat.S_ISREG(child.stat(follow_symlinks=False).st_mode)
                    and overlay_annotations.ANNOTATION_FILE.match(child.name)):
                fail(f"the built site already has {target}, which holds {entry.name}/{child.name}; the "
                     f"annotations are published there, so it may hold only <section>/<name>.json files")


def install_directory(output_root: Path, relative: str, contents: dict[str, bytes],
                      token: str, *, guard=None) -> None:
    """Stage ``contents`` beside ``<output_root>/<relative>``, then swap it in with two renames.

    A previous copy is replaced whatever state it is in, after ``guard`` (when
    given) has accepted it: the mount is output this tool owns, so it is never
    a reason to stop; the annotation directory is checked to be one.
    """
    parts = PurePosixPath(relative).parts
    parent = ensure_output_directory(output_root, parts[:-1])
    leaf = parts[-1]
    clear_stale_staging(parent, leaf)
    staging = parent / f".{leaf}.{os.getpid()}.{token}.new"
    retired = parent / f".{leaf}.{os.getpid()}.{token}.old"
    try:
        staging.mkdir(mode=0o755)
    except OSError as error:                                 # pragma: no cover
        fail(f"cannot create the staging directory {staging}: {error}")
    installed = False
    try:
        write_tree(staging, contents)
        target = parent / leaf
        mode = inspect_output_component(target, relative)
        if mode is not None and not stat.S_ISDIR(mode):
            fail(f"output escape: {relative} is not a directory: {target}")
        if mode is not None:
            if guard is not None:
                guard(target)
            try:
                os.rename(target, retired)
            except OSError as error:                         # pragma: no cover
                fail(f"cannot set the previous copy of {relative} aside: {error}")
        try:
            os.rename(staging, target)
        except OSError as error:                             # pragma: no cover
            if mode is not None:
                os.rename(retired, target)
            fail(f"cannot install {relative}: {error}")
        installed = True
    finally:
        if not installed:
            shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(retired, ignore_errors=True)


def build_site(overlay: Path, *, token_factory=lambda: secrets.token_hex(8)) -> dict[str, Any]:
    """Publish the overlay into the configured site and link it from the index."""
    refuse_windows()
    overlay = Path(os.path.abspath(overlay))
    require_directory(overlay, "overlay directory")
    root = overlay.parent
    config = load_config(overlay)
    directory, bundle, annotations = collect_annotations(overlay, config)
    contents = collect_runtime(overlay, bundle)
    if PurePosixPath(directory).parts[0] == PurePosixPath(config.mount).parts[0]:
        fail(f"the annotation directory {directory} and the mount {config.mount} would overlap")

    output_parts = PurePosixPath(config.output_root).parts
    current = root
    for part in output_parts:
        current /= part
        mode = inspect_output_component(current, "site output root")
        if mode is None:
            fail(f"site output root: {current} does not exist; build the site first")
        if not stat.S_ISDIR(mode):
            fail(f"site output root: not an ordinary directory: {current}")
    output_root = current

    index_parts = PurePosixPath(config.index).parts
    current = output_root
    for part in index_parts[:-1]:
        current /= part
        mode = inspect_output_component(current, "site index")
        if mode is None or not stat.S_ISDIR(mode):
            fail(f"site index: {current} is not a directory of the built site")
    index_path = require_regular(current / index_parts[-1], "site index")

    href = link_href(config)
    final_index = linked_index(index_path.read_bytes(), href, config.link_text)
    receipt_raw = canonical_json(receipt_value(config, contents, href, final_index, directory, annotations))
    token = token_factory()
    if not re.fullmatch(r"[0-9a-f]{16}", token):
        fail("temporary token provider did not return 16 lowercase hex digits")
    install_directory(output_root, directory, annotations, token, guard=only_annotations)
    install_directory(output_root, config.mount, {**contents, RECEIPT_NAME: receipt_raw}, token)
    write_atomic(index_path, final_index)

    mount_root = output_root.joinpath(*PurePosixPath(config.mount).parts)
    annotation_root = output_root.joinpath(*PurePosixPath(directory).parts)
    for root, files in ((mount_root, contents), (annotation_root, annotations)):
        for relative, expected in files.items():
            written = root.joinpath(*PurePosixPath(relative).parts)
            if written.read_bytes() != expected:             # pragma: no cover
                fail(f"post-integration verification failed: {relative}")
    if (mount_root / RECEIPT_NAME).read_bytes() != receipt_raw:  # pragma: no cover
        fail("post-integration verification failed: the receipt")
    if index_path.read_bytes() != final_index:               # pragma: no cover
        fail("post-integration verification failed: the site index")
    return {
        "annotations": directory,
        "files": len(contents),
        "index": config.index,
        "annotation_files": len(annotations),
        "link_href": href,
        "mount": config.mount,
        "output_root": config.output_root,
        "receipt_sha256": sha256(receipt_raw),
    }


def overlay_from_script() -> Path:
    """`pdf-overlay/` from this file's own path, never from the cwd."""
    script = Path(os.path.abspath(__file__))
    require_regular(script, "overlay build tool")
    return script.parent.parent


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("--print-receipt", action="store_true",
                        help="print the receipt this run wrote")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        overlay = overlay_from_script()
        summary = build_site(overlay)
        sys.stdout.write(canonical_json(summary).decode("utf-8"))
        if arguments.print_receipt:
            receipt = (overlay.parent
                       .joinpath(*PurePosixPath(summary["output_root"]).parts)
                       .joinpath(*PurePosixPath(summary["mount"]).parts) / RECEIPT_NAME)
            sys.stdout.write(receipt.read_text(encoding="utf-8"))
    except BuildError as error:
        print(f"STOP {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
