"""The library's builds on the operator's machine: `cf informalize build-command`,
`fetch-library` and `verso-command`.

New code, for docs/proposals/informalize.md section 6.1.  The pilot built its library by a
hand-written script on the host and dumped it with a Lean file that named its library,
its paths and its commit; here the host runs locate's drivers as they are, through
`scripts/informalize/library_host.sh`, and this module does the two ends on the operator's
machine:

* **build-command** writes `informalize/<id>/library/to-host/` -- a byte copy of every
  driver the host runs (:data:`HOST_FILES` and the canary), the one-line library list
  `library.tsv`, and `build-command.sh`, the lines that copy both to the host and start the
  job there detached -- and prints those lines.  It runs nothing: building a third party's
  library on a shared host is the operator's act, after the box owner's consent.
* **fetch-library** asks the host whether the job wrote `library/host/DONE`, copies
  `library/host/` back into `library/.incoming/` and stops there unless DONE holds 0, then
  copies `deps.json` and the index, checks them (:func:`check_fetched`, which also holds
  `to-host/` to what the job recorded it ran), unpacks the source archive there, and only
  then puts each in its place under `library/`, so a fetch that fails a check leaves the
  previous one as it was.
  It writes the library's license into `informalize.yaml` when the license file names one,
  the commit's time into `informal/informalize.json` (committed), and the I1 receipt.
* **verso-command** is the same shape for the overlay's hovers (section 10): it writes
  `informalize/<id>/verso/to-host/` -- the drivers of the Verso build
  (:data:`VERSO_HOST_FILES`, the overlay's own `verso_extract.py` among them), the same
  one-line `library.tsv`, and `verso-command.sh` -- and prints the lines that copy them and
  the output repository's annotation bundle to the host, start the job there detached, and
  bring `pdf-overlay/verso.json` back to be committed.  It runs nothing either.

ssh and rsync run only under `fetch-library --execute --i-have-consent`, against a host of
`hosts.allowlist` (`run.manifest.resolve_host`), the one network exception CLAUDE.md
section 3 allows this mode.  Lean never runs here.
"""
from __future__ import annotations

import copy
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from cflib.errors import CfError
from cflib.hashing import sha256_file
from cflib.paths import skill_root
from cfcmd.corpus import DRIVERS as CORPUS_DRIVERS
from corpus import ingest, universe_host
from corpus.universe import lean_version
from overlay import sync as overlay_sync
from run import manifest

from informalize import config, library_host, runtime, stages, verso_host
from informalize.outrepo import OutputRepo

#: what build-command copies to the host besides the canary, beside each other there as in
#: this checkout: the drivers `cf corpus probe-command` names, the dependency dump, and I1's
#: own pair
HOST_FILES = (*CORPUS_DRIVERS, "scripts/detect/dump_deps.sh", "scripts/detect/DumpDeps.lean.tmpl",
              "scripts/informalize/library_host.sh", "scripts/informalize/library_host.py")
#: the acceptance canary's templates, copied whole
CANARY_DIR = "templates/corpus/canary"
HOST_SCRIPT = "scripts/informalize/library_host.sh"
LIBRARY_LIST = "library.tsv"
COMMAND_FILE = "build-command.sh"
#: what verso-command copies to the host: the universe line's parser, the Verso build's own
#: pair, and the overlay's extractor with the module it imports
VERSO_HOST_FILES = ("scripts/corpus/universe_host.py", "scripts/corpus/probe_merge.py",
                    "scripts/informalize/verso_host.sh",
                    "scripts/informalize/verso_host.py",
                    "templates/overlay/runtime/tools/verso_extract.py",
                    "templates/overlay/runtime/tools/overlay_annotations.py")
VERSO_HOST_SCRIPT = "scripts/informalize/verso_host.sh"
VERSO_COMMAND_FILE = "verso-command.sh"
#: the output repository's annotation bundle, which the host reads, and the file the Verso
#: build is for (docs/overlay.md section 5)
BUNDLE = "pdf-overlay/annotations.bundle.json"
VERSO_FILE = "pdf-overlay/verso.json"
#: a released Lean version, the only kind whose Verso tag `default_verso_rev` may assume
RELEASE_VERSION = re.compile(r"\d+\.\d+\.\d+(-rc\d+)?\Z")
DEFAULT_MIN_FREE_GB = 60
DEFAULT_LEAN_THREADS = 16
#: the license files library_host.sh copies from the checkout's root, in its order
LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md", "LICENCE.txt",
                 "COPYING", "COPYING.md", "COPYING.txt", "LICENSE-APACHE", "LICENSE-MIT")
#: what fetch-library puts in place under library/, each from library/.incoming/
FETCHED = ("host", "index", "deps.json", "src")
INCOMING = ".incoming"

# ---------------------------------------------------------------------------
# license files
# ---------------------------------------------------------------------------

#: ``(SPDX id, phrases every copy holds, phrases none holds)``, each phrase in the form
#: :func:`normalise_license` gives a text.  A license file is that SPDX id when exactly one
#: row matches it.  A family whose text does not say which version applies (the GPL's
#: "or later") is not in the table: the maintainer names it with `init --library-license`.
LICENSE_FINGERPRINTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("Apache-2.0", ("apache license", "version 2 0"), ()),
    ("MIT", ("permission is hereby granted free of charge to any person obtaining a copy",
             "the above copyright notice and this permission notice shall be included"), ()),
    ("BSD-3-Clause", ("redistribution and use in source and binary forms",
                      "neither the name of"), ()),
    ("BSD-2-Clause", ("redistribution and use in source and binary forms",),
     ("neither the name of",)),
    ("ISC", ("permission to use copy modify and or distribute this software for any purpose "
             "with or without fee is hereby granted",), ()),
    ("MPL-2.0", ("mozilla public license version 2 0",), ()),
    ("Unlicense", ("this is free and unencumbered software released into the public domain",),
     ()),
    ("CC0-1.0", ("cc0 1 0 universal",), ()),
    ("CC-BY-4.0", ("attribution 4 0 international",),
     ("sharealike", "noncommercial", "noderivatives")),
    ("CC-BY-SA-4.0", ("attribution sharealike 4 0 international",), ("noncommercial",)),
)


def normalise_license(text: str) -> str:
    """Lowercase words separated by single spaces: punctuation and line breaks do not count."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.lower()).split())


def license_of(text: str) -> str | None:
    """The SPDX id of a license text by :data:`LICENSE_FINGERPRINTS`, or None (none, or more
    than one, matches)."""
    flat = normalise_license(text)
    found = [spdx for spdx, need, never in LICENSE_FINGERPRINTS
             if all(p in flat for p in need) and not any(p in flat for p in never)]
    return found[0] if len(found) == 1 else None


# ---------------------------------------------------------------------------
# the host folder
# ---------------------------------------------------------------------------

_HOME_ROOT = re.compile(r"\A(?:~|\$HOME)((?:/[A-Za-z0-9._-]+)+)/?\Z")
_ABSOLUTE_ROOT = re.compile(r"\A((?:/[A-Za-z0-9._-]+)+)/?\Z")


class RemoteRoot:
    """The workspace's `remote_root` for a host: one folder, spelled for the host's shell
    and for rsync.  `~/x` and `$HOME/x` are the operator's home on the host; anything but
    plain path characters is refused, so nothing printed needs quoting there."""

    def __init__(self, root: str):
        text = str(root).strip()
        home = _HOME_ROOT.match(text)
        absolute = None if home else _ABSOLUTE_ROOT.match(text)
        rest = (home or absolute).group(1) if (home or absolute) else None
        if rest is None or any(part in (".", "..") for part in rest.split("/")):
            raise CfError(f"remote_root {root!r} is not a folder this command can name on the "
                          "host",
                          hint="set hosts.<host>.remote_root in workspace.yaml to one folder for "
                               "this informalize id, e.g. ~/informalize-<id>: letters, digits, "
                               ". _ - and /, under ~ or absolute")
        self.text = text
        self.home = home is not None
        self.rest = rest.strip("/")

    def shell(self, relative: str = "") -> str:
        """The path in a command the host's shell reads (`"$HOME"/x/...` or `/x/...`)."""
        tail = "/".join(p for p in (self.rest, relative) if p)
        return f'"$HOME"/{tail}' if self.home else f"/{tail}"

    def rsync(self, relative: str = "") -> str:
        """The path after `<host>:` for rsync, which reads one under the home as relative."""
        tail = "/".join(p for p in (self.rest, relative) if p)
        return tail if self.home else f"/{tail}"


def host_folder(workspace, host_name: str, informalize_id: str) -> tuple[dict, RemoteRoot]:
    """The host's connection facts (`run.manifest.resolve_host`) and this id's folder on it.

    The folder is `hosts.<host>.remote_root` as that host's own entry sets it, and its last
    part is `informalize-<id>`.  The workspace-wide `hosts.remote_root` and the default (the
    operator's home) are never used, so the folder is never the home, another run's root or
    another informalize id's (section 2)."""
    host = manifest.resolve_host(workspace, host_name)
    hosts = workspace.hosts if isinstance(workspace.hosts, dict) else {}
    entry = hosts.get(host_name)
    if not isinstance(entry, dict):
        entries = hosts.get("entries")
        entry = entries.get(host_name) if isinstance(entries, dict) else None
    own = (entry.get("remote_root") or entry.get("root")) if isinstance(entry, dict) else None
    want = f"informalize-{informalize_id}"
    hint = (f"set hosts.{host_name}.remote_root in workspace.yaml to this id's own folder, "
            f"e.g. ~/{want}")
    if not own:
        raise CfError(f"workspace.yaml sets no remote_root for {host_name} itself",
                      hint=hint + "; the workspace-wide hosts.remote_root and the default (your "
                                  "home on the host) are not used")
    remote = RemoteRoot(str(own))
    if remote.rest.rsplit("/", 1)[-1] != want:
        raise CfError(f"hosts.{host_name}.remote_root {own!r} is not the folder of "
                      f"{informalize_id}: its last part must be {want}",
                      hint=hint + ", never the home, another run's root or another id's")
    return host, remote


def library_id(cfg: dict) -> str:
    """The library's id in the universe line: `<id>-lib` (section 5.1)."""
    return f"{cfg['id']}-lib"


def universe_fields(cfg: dict) -> list[str]:
    """`library.tsv`'s one line, as the universe driver reads it, refused here when it would
    refuse it there."""
    library = cfg["library"]
    options = (f"BUILD_TARGETS={library['module']};MODULE_PREFIXES={library['prefix']};"
               f"LIBRARY_CACHE={1 if library['library_cache'] else 0}")
    fields = [library_id(cfg), library["url"], library["commit"], library["module"], "build",
              options]
    try:
        universe_host.Entry(1, fields)
    except universe_host.UniverseError as exc:
        raise CfError(f"the host driver would refuse this library: {exc}",
                      hint="the universe driver reads library.url as https://<host>/<path> of "
                           "letters, digits and . _ / -; edit informalize.yaml") from None
    return fields


def host_files() -> list[str]:
    """Every file build-command copies to the host, relative to this checkout."""
    root = skill_root()
    out = list(HOST_FILES)
    for parent, names, files in os.walk(root / CANARY_DIR):
        names[:] = sorted(n for n in names if n != "__pycache__")
        out += [(Path(parent) / f).relative_to(root).as_posix() for f in sorted(files)
                if not f.endswith(".pyc")]
    for relative in out:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise CfError(f"{path} is not an ordinary file",
                          hint="this is not a whole formal-companion checkout")
    return sorted(set(out))


def _check_home(workspace, informalize_id: str, extra: tuple[Path, ...] = ()) -> Path:
    """`informalize/<id>/library/`, refusing one that is a link or lives elsewhere, and a
    link where this module writes a directory of its own (`extra`: another command's)."""
    paths = workspace.paths
    home = paths.informalize_dir(informalize_id)
    library = paths.informalize_library(informalize_id)
    top = os.path.realpath(paths.root)
    for path in (home, library, paths.informalize_library_to_host(informalize_id),
                 library / INCOMING, *extra):
        if path.is_symlink():
            raise CfError(f"{path} is a symlink", hint="it is written in place; remove the link")
    if not os.path.realpath(home).startswith(top + os.sep):
        raise CfError(f"{home} resolves outside the workspace {paths.root}")
    return library


def _quote_local(path: Path) -> str:
    return shlex.quote(str(path))


@dataclass
class BuildCommand:
    """What build-command or verso-command wrote, and the lines it prints."""

    to_host: Path
    lines: list[str]
    files: dict[str, str] = field(default_factory=dict)


def _write_to_host(to_host: Path, files: list[str], fields: list[str]) -> dict[str, str]:
    """`<to-host>/drivers/` from this checkout and the one-line `library.tsv` beside it, as
    both build-command and verso-command send them, with the sha256 of each.  The drivers
    are rewritten whole every time, so a file this checkout dropped does not linger."""
    drivers = to_host / "drivers"
    if drivers.exists():
        shutil.rmtree(drivers)
    drivers.mkdir(parents=True)
    root = skill_root()
    written: dict[str, str] = {}
    for relative in files:
        target = drivers / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / relative).read_bytes())
        os.chmod(target, 0o644)
        written[f"drivers/{relative}"] = sha256_file(target)
    (to_host / LIBRARY_LIST).write_text("\t".join(fields) + "\n", encoding="utf-8")
    written[LIBRARY_LIST] = sha256_file(to_host / LIBRARY_LIST)
    return written


def build_command(workspace, informalize_id: str, host_name: str, *,
                  min_free_gb: int = DEFAULT_MIN_FREE_GB,
                  lean_threads: int = DEFAULT_LEAN_THREADS) -> BuildCommand:
    """Write `library/to-host/` and return the lines that run I1 on the host."""
    cfg = config.load(workspace.paths, informalize_id, sessions=config.session_limit(workspace))
    if isinstance(min_free_gb, bool) or not isinstance(min_free_gb, int) or min_free_gb < 0:
        raise CfError(f"--min-free-gb {min_free_gb!r} is not a whole number of GiB")
    if isinstance(lean_threads, bool) or not isinstance(lean_threads, int) or lean_threads < 1:
        raise CfError(f"--lean-threads {lean_threads!r} is not a positive number")
    host, remote = host_folder(workspace, host_name, informalize_id)
    fields = universe_fields(cfg)
    _check_home(workspace, informalize_id)
    to_host = workspace.paths.informalize_library_to_host(informalize_id)
    written = _write_to_host(to_host, host_files(), fields)
    lines = _command_lines(cfg, host, remote, to_host, min_free_gb, lean_threads)
    script = "#!/bin/sh\nset -eu\n" + "\n".join(lines) + "\n"
    (to_host / COMMAND_FILE).write_text(script, encoding="utf-8")
    return BuildCommand(to_host=to_host, lines=lines, files=written)


def _command_lines(cfg: dict, host: dict, remote: RemoteRoot, to_host: Path, min_free_gb: int,
                   lean_threads: int) -> list[str]:
    library = cfg["library"]
    ssh = shlex.quote(host["ssh"])
    start = (f"cd {remote.shell()} && mkdir -p library/host && setsid -f env "
             f'ATLAS_ROOT="$PWD/library" MIN_FREE_GB={min_free_gb} LEAN_THREADS={lean_threads} '
             f"bash drivers/{HOST_SCRIPT} {LIBRARY_LIST} >> library/host/library_host.log 2>&1 "
             "< /dev/null")
    watch = (f"tail -n 20 {remote.shell('library/host/library_host.log')}; "
             f"cat {remote.shell('library/host/DONE')}")
    lines = [
        f"# Stage I1 of {cfg['id']} (docs/proposals/informalize.md section 6.1): on {host['name']},",
        f"# build {library['module']} of {library['url']} at {library['commit']},",
        "# probe the modules it imports under " + library["prefix"] + ", check the probe's canary "
        "on its toolchain",
        "# and dump their dependencies. Written by `cf informalize build-command`, which runs",
        "# nothing: run these lines on this machine, as the operator, once the box owner has",
        f"# agreed. They copy the drivers and the library list to {remote.text} and start the",
        "# job there, detached; the library is cloned and built on the host, never here.",
    ]
    if library["library_cache"]:
        lines.append("# library.library_cache is true: the host also runs the library's own "
                     "scripts/lake-cache-get.sh, which may download and run code.")
    lines += [
        f"ssh -n {ssh} {shlex.quote('mkdir -p ' + remote.shell('library/host'))}",
        f"rsync -a --delete {_quote_local(to_host / 'drivers')}/ "
        f"{ssh}:{remote.rsync('drivers')}/",
        f"rsync -a {_quote_local(to_host / LIBRARY_LIST)} {ssh}:{remote.rsync(LIBRARY_LIST)}",
        f"ssh -n {ssh} {shlex.quote(start)}",
        "# The job writes library/host/DONE, its exit status, last. To watch it:",
        f"#   ssh -n {ssh} {shlex.quote(watch)}",
        "# When DONE holds 0, bring it back:",
        f"#   cf informalize fetch-library {cfg['id']} --host {host['name']} --execute "
        "--i-have-consent",
        "# Once it is fetched the build may go; the index and the dump stay:",
        f"#   ssh -n {ssh} "
        + shlex.quote("rm -rf " + remote.shell(f"library/libs/{library_id(cfg)}/.lake")),
    ]
    return lines


# ---------------------------------------------------------------------------
# verso-command: the Verso build the overlay's hovers are cut from
# ---------------------------------------------------------------------------

def verso_host_files() -> list[str]:
    """Every file verso-command copies to the host, relative to this checkout."""
    root = skill_root()
    for relative in VERSO_HOST_FILES:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise CfError(f"{path} is not an ordinary file",
                          hint="this is not a whole formal-companion checkout")
    return sorted(VERSO_HOST_FILES)


def default_verso_rev(paths, informalize_id: str) -> str:
    """The Verso revision of the library's toolchain: `v<version>` of `library/host/
    lean-toolchain`, which stage I1 brought back.  Verso is released once per Lean
    release, so the tag of the toolchain's version is the one that builds against it --
    but only for a released version, since no Verso tag answers to a nightly or a
    pr-release and `vnightly-2026-01-01` would be printed as if one did."""
    path = paths.informalize_library(informalize_id) / "host" / "lean-toolchain"
    toolchain = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    version = lean_version(toolchain)
    if not RELEASE_VERSION.match(version):
        said = f"{path} says {toolchain!r}, not a released version" if version else f"no {path}"
        raise CfError(f"nothing says which Verso revision to build: {said}",
                      hint="run stage I1 first (`cf informalize fetch-library`), or name the "
                           "revision with --verso-rev (the tag of the library's toolchain, "
                           "e.g. v4.36.0-rc2; a nightly or a pr-release has no tag of its "
                           "own, so name the commit Verso built against it)")
    return f"v{version}"


def verso_command(workspace, informalize_id: str, host_name: str, *, verso_rev: str | None = None,
                  min_free_gb: int = DEFAULT_MIN_FREE_GB,
                  lean_threads: int = DEFAULT_LEAN_THREADS) -> BuildCommand:
    """Write `verso/to-host/` and return the lines that build the library's literate HTML
    on the host and cut `pdf-overlay/verso.json` out of it."""
    cfg = config.load(workspace.paths, informalize_id, sessions=config.session_limit(workspace))
    if isinstance(min_free_gb, bool) or not isinstance(min_free_gb, int) or min_free_gb < 0:
        raise CfError(f"--min-free-gb {min_free_gb!r} is not a whole number of GiB")
    if isinstance(lean_threads, bool) or not isinstance(lean_threads, int) or lean_threads < 1:
        raise CfError(f"--lean-threads {lean_threads!r} is not a positive number")
    rev = verso_rev or default_verso_rev(workspace.paths, informalize_id)
    if not verso_host.REV.match(rev):
        raise CfError(f"--verso-rev {rev!r} is not a tag, a branch or a commit")
    host, remote = host_folder(workspace, host_name, informalize_id)
    fields = universe_fields(cfg)
    paths = workspace.paths
    verso_dir = paths.informalize_verso(informalize_id)
    to_host = paths.informalize_verso_to_host(informalize_id)
    _check_home(workspace, informalize_id, extra=(verso_dir, to_host))
    repo = OutputRepo(paths, informalize_id)
    repo.require_stamp()
    bundle = repo.target(BUNDLE)
    if bundle.is_symlink() or not bundle.is_file():
        raise CfError(f"the output repository has no {BUNDLE}",
                      hint="`cf overlay export --convention informal-markers` and `cf overlay "
                           "sync` write it (stage I7; `cf informalize overlay`, which will run "
                           "them, is not implemented yet -- docs/overlay.md); the Verso build "
                           "is cut to its cards")
    records = overlay_sync.bundle_records(bundle.read_bytes())
    if records is None:
        raise CfError(f"{BUNDLE} is not a readable annotation bundle",
                      hint="`cf overlay export --convention informal-markers` writes it; "
                           "`cf overlay check` says what is wrong with this one")
    cards = len(records)
    written = _write_to_host(to_host, verso_host_files(), fields)
    lines = _verso_lines(cfg, host, remote, to_host, bundle, rev, lean_threads, cards,
                         min_free_gb)
    script = "#!/bin/sh\nset -eu\n" + "\n".join(lines) + "\n"
    (to_host / VERSO_COMMAND_FILE).write_text(script, encoding="utf-8")
    return BuildCommand(to_host=to_host, lines=lines, files=written)


def _verso_lines(cfg: dict, host: dict, remote: RemoteRoot, to_host: Path, bundle: Path,
                 rev: str, lean_threads: int, cards: int, min_free_gb: int) -> list[str]:
    library = cfg["library"]
    ssh = shlex.quote(host["ssh"])
    repo = bundle.parent.parent
    bundle_name = PurePosixPath(BUNDLE).name
    start = (f"cd {remote.shell()} && mkdir -p verso/host && setsid -f env "
             f'ATLAS_ROOT="$PWD/library" VERSO_ROOT="$PWD/verso" VERSO_REV={shlex.quote(rev)} '
             f"MIN_FREE_GB={min_free_gb} "
             f"LEAN_THREADS={lean_threads} bash verso/drivers/{VERSO_HOST_SCRIPT} "
             f"verso/{LIBRARY_LIST} verso/{bundle_name} >> verso/host/verso_host.log 2>&1 "
             "< /dev/null")
    watch = (f"tail -n 20 {remote.shell('verso/host/verso_host.log')}; "
             f"cat {remote.shell('verso/host/DONE')}")
    lines = [
        f"# The overlay's hovers for {cfg['id']} (docs/proposals/informalize.md section 10):",
        f"# on {host['name']}, build the literate HTML of {library['module']} of "
        f"{library['url']}",
        f"# at {library['commit']} with Verso {rev}, and cut the",
        f"# statement of each of the {cards} cards of the output repository's annotation "
        "bundle out of",
        f"# it, with its hovers, as {VERSO_FILE} (docs/overlay.md section 5).",
        "# Written by `cf informalize verso-command`, which runs nothing: run these lines on",
        "# this machine, as the operator, once the box owner has agreed. They copy the "
        "drivers and",
        f"# the bundle (the output repository's own file, published with its site) to "
        f"{remote.text},",
        "# and start the job there, detached. The library is built on the host, never here,",
        f"# in a copy of stage I1's checkout under {remote.text}/verso: the Verso require "
        "edits",
        "# the lakefile, and I1's tree is left as it is. That copy is a second whole built",
        f"# library (tens of GiB); the job stops above the {min_free_gb}G floor rather than "
        "fill the",
        "# disk, and the last line below takes the copy away again.",
        f"ssh -n {ssh} {shlex.quote('mkdir -p ' + remote.shell('verso/host'))}",
        f"rsync -a --delete {_quote_local(to_host / 'drivers')}/ "
        f"{ssh}:{remote.rsync('verso/drivers')}/",
        f"rsync -a {_quote_local(to_host / LIBRARY_LIST)} "
        f"{ssh}:{remote.rsync('verso/' + LIBRARY_LIST)}",
        f"rsync -a {_quote_local(bundle)} {ssh}:{remote.rsync('verso/' + bundle_name)}",
        f"ssh -n {ssh} {shlex.quote(start)}",
        "# The job writes verso/host/DONE, its exit status, last. To watch it:",
        f"#   ssh -n {ssh} {shlex.quote(watch)}",
        "# When DONE holds 0, bring the file back, commit it, and build the site again:",
        f"#   rsync -a {ssh}:{remote.rsync('verso/host/verso.json')} "
        f"{_quote_local(repo / VERSO_FILE)}",
        f"#   git -C {_quote_local(repo)} add {VERSO_FILE} && git -C {_quote_local(repo)} "
        f"commit -m 'Add the Verso hovers of {library['commit'][:12]}'",
        f"#   (cd {_quote_local(repo)} && python3 pdf-overlay/tools/serve.py --build-only)",
        "# Once it is fetched the Verso build may go; nothing else reads it:",
        f"#   ssh -n {ssh} "
        + shlex.quote("rm -rf " + remote.shell(f"verso/libs/{library_id(cfg)}")),
    ]
    return lines


# ---------------------------------------------------------------------------
# fetch-library
# ---------------------------------------------------------------------------

def _run(argv: list[str]) -> subprocess.CompletedProcess:
    """One ssh or rsync call (the tests replace this)."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=7200)


def fetch_commands(cfg: dict, host: dict, remote: RemoteRoot, incoming: Path) -> list[list[str]]:
    """The ssh test of DONE, then the three copies, in the order fetch-library runs them."""
    ssh = host["ssh"]
    index = f"library/index/{library_id(cfg)}@{cfg['library']['commit'][:12]}/"
    return [
        ["ssh", "-n", ssh, f"test -f {remote.shell('library/host/DONE')}"],
        ["rsync", "-a", "--no-links", "--delete", f"{ssh}:{remote.rsync('library/host/')}",
         f"{incoming / 'host'}/"],
        ["rsync", "-a", "--no-links", f"{ssh}:{remote.rsync('library/deps.json')}",
         str(incoming / "deps.json")],
        ["rsync", "-a", "--no-links", "--delete", "--exclude=work/",
         f"{ssh}:{remote.rsync(index)}", f"{incoming / 'index'}/"],
    ]


def _text(path: Path, what: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise CfError(f"no {what} ({path})",
                      hint="the host job did not write it; read library/.incoming/host/"
                           "library_host.log, or run the job again")
    return path.read_text(encoding="utf-8", errors="replace")


def _json(path: Path, what: str) -> Any:
    try:
        return json.loads(_text(path, what))
    except json.JSONDecodeError as exc:
        raise CfError(f"{what} ({path}) is not JSON: {exc.msg}") from None


def index_records(index: Path, kind: str) -> Iterator[dict]:
    """The records of one kind (`module`, `declaration`) of a probe's `index.jsonl`, in
    file order."""
    with open(index, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CfError(f"{index.name} line {line_no} is not JSON ({exc.msg})") from None
            if isinstance(record, dict) and record.get("record") == kind:
                yield record


def unpack_source(archive: Path, dest: Path) -> dict:
    """Unpack `git archive`'s tar into `dest`, read-only: plain files and directories only.

    An absolute path, a `..` component or a member that is neither a file, a directory nor
    a link is refused; a link is never made, and is listed instead (a module behind one has
    no file, which I2 then refuses)."""
    if dest.exists():
        _remove(dest)
    dest.mkdir(parents=True)
    files, links = 0, []
    try:
        with tarfile.open(archive, "r:") as tar:
            for member in tar:
                name = member.name
                pure = PurePosixPath(name)
                if not name or "\\" in name or pure.is_absolute() or ".." in pure.parts:
                    raise CfError(f"src.tar holds {name!r}, a path outside the archive",
                                  hint="git archive writes none; fetch the library again")
                parts = [p for p in pure.parts if p != "."]
                if not parts:
                    continue
                target = dest.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.issym() or member.islnk():
                    links.append("/".join(parts))
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = tar.extractfile(member)
                    if stream is None:                          # pragma: no cover
                        raise CfError(f"src.tar member {name!r} cannot be read")
                    with stream, open(target, "xb") as out:
                        shutil.copyfileobj(stream, out)
                    os.chmod(target, 0o444)
                    files += 1
                else:
                    raise CfError(f"src.tar holds {name!r}, which is not a file, a directory "
                                  "or a link")
    except (tarfile.TarError, FileExistsError) as exc:
        raise CfError(f"src.tar does not unpack: {exc}",
                      hint="fetch the library again; the host writes it with git archive") from None
    return {"files": files, "links_skipped": sorted(links)}


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def check_done(host: Path) -> None:
    """Refuse a fetched `host/` whose job did not end with 0, naming its log."""
    done = _text(host / "DONE", "host/DONE").strip()
    if done != "0":
        raise CfError(f"the host job ended with status {done!r}, not 0",
                      hint=f"read {host / 'library_host.log'} (the step that failed, and the log "
                           "it names), then run the printed lines of build-command again")


def drivers_problems(host: Path, to_host: Path) -> list[str]:
    """Is `to-host/` what the host job ran?  `host/drivers.json` holds the sha256 of every
    driver and of the library list as the job read them (`library_host.py digest`)."""
    ran = _json(host / "drivers.json", "host/drivers.json")
    if not isinstance(ran, dict) or not all(isinstance(v, str) for v in ran.values()):
        return ["host/drivers.json is not a map of file to sha256"]
    if not (to_host / LIBRARY_LIST).is_file():
        return [f"there is no {to_host / LIBRARY_LIST}: build-command was not run in this "
                "workspace, so nothing says what the host job should have run"]
    here = library_host.digest(to_host / "drivers", to_host / LIBRARY_LIST)
    differ = sorted(k for k in ran.keys() | here.keys() if ran.get(k) != here.get(k))
    if differ:
        return [f"library/to-host/ is not what the host job ran: {len(differ)} file(s) differ ("
                + ", ".join(differ[:5]) + "); build-command was run again since, from "
                "another checkout or with other settings: run its printed lines again"]
    return []


def check_fetched(incoming: Path, cfg: dict, to_host: Path) -> dict:
    """Every check section 6.1 makes of a fetched library, on `library/.incoming/` (whose
    `host/src.tar` it unpacks into `src/`); the facts the I1 receipt records.

    Refused: a job that did not end with 0; drivers or a library list other than
    `to_host`'s; a checkout whose HEAD, or whose driver state, is not the commit; an index
    that is not whole or not the library's at the commit and toolchain; a canary that
    failed or ran on another toolchain, or in a form the library's modules do not call for;
    a dump of another module or prefix, or one without the proofs' bodies; a theorem the
    index does not hold; an index whose module files the archive does not hold; and license
    files that name no license, more than one, or not a manual one.
    """
    library = cfg["library"]
    commit, module, theorem = library["commit"], library["module"], library["theorem"]
    lib = library_id(cfg)
    host = incoming / "host"
    check_done(host)
    facts: dict[str, Any] = {"done": 0}
    problems: list[str] = drivers_problems(host, to_host)
    head = _text(host / "commit", "host/commit").strip()
    if head != commit:
        problems.append(f"the host checkout's HEAD is {head[:12]!r}, not the commit "
                        f"{commit[:12]}")
    state = _json(host / "state" / f"{lib}.json", f"the driver's state host/state/{lib}.json")
    state = state if isinstance(state, dict) else {}
    if state.get("stage") != "indexed" or state.get("commit") != commit:
        problems.append(f"the driver's state says stage {state.get('stage')!r} at commit "
                        f"{str(state.get('commit'))[:12]!r}, not indexed at {commit[:12]}")
    facts["commit"] = commit
    toolchain = _text(host / "lean-toolchain", "host/lean-toolchain").strip()
    facts["toolchain"] = toolchain
    try:
        epoch = int(_text(host / "commit-time", "host/commit-time").strip())
    except ValueError:
        epoch = -1
    if epoch < 0:
        problems.append("host/commit-time is not the commit's time in seconds")
    facts["source_date_epoch"] = epoch

    index = ingest.check_index(incoming / "index", library_id=lib, commit=commit,
                               toolchain=toolchain)
    facts["index"] = {"sha256": index["index_sha256"], "declarations": index["declarations"],
                      "modules": index["modules"], "lean_version": index["lean_version"]}

    canary = ingest.canary_cases(host / "canary")
    form = _text(host / "canary-form", "host/canary-form").strip()
    facts["canary"] = {"passed": canary["passed"], "lean_version": canary["lean_version"],
                       "form": form,
                       "cases": {name: case["ok"] for name, case in canary["cases"].items()}}
    if not canary["passed"]:
        failed = [name for name, case in canary["cases"].items() if not case["ok"]]
        problems.append(f"the canary failed on Lean {canary['lean_version']}: "
                        + ", ".join(failed) + " decided otherwise than the probe must")
    if canary["lean_version"] != lean_version(toolchain):
        problems.append(f"the canary ran on Lean {canary['lean_version']}, the library is on "
                        f"{lean_version(toolchain)}")

    deps = _json(incoming / "deps.json", "deps.json")
    found = deps_problems(deps, cfg)
    problems += found or [p for p in [bodies_problem(deps, theorem)] if p]
    deps = deps if isinstance(deps, dict) else {}
    facts["deps"] = {"root_module": deps.get("root_module"),
                     "module_prefix": deps.get("module_prefix"),
                     "repo_constants": len(deps.get("repo_constants") or [])}

    records = [r for r in index_records(incoming / "index" / "index.jsonl", "declaration")
               if r.get("name") == theorem and not r.get("private")]
    if len(records) != 1:
        problems.append(f"the index holds {len(records)} public records of {theorem}, not one")
    else:
        record = records[0]
        facts["theorem"] = {"name": theorem, "module": record.get("module"),
                            "kind": record.get("kind"),
                            "nonstandard_axioms": sorted(record.get("nonstandard_axioms") or [])}

    source = incoming / "src"
    facts["source"] = {"src_tar_sha256": sha256_file(host / "src.tar"),
                       **unpack_source(host / "src.tar", source)}
    module_paths = sorted({m["path"] for m in index_records(incoming / "index" / "index.jsonl",
                                                            "module")
                           if isinstance(m.get("path"), str)})
    missing = [p for p in module_paths if not _plain_file(source, p)]
    if missing:
        problems.append(f"{len(missing)} module file(s) the index names are not in src.tar: "
                        + ", ".join(missing[:5]))
    texts = [(source / p).read_text(encoding="utf-8", errors="replace")
             for p in module_paths if p not in missing]
    if form != library_host.library_form(texts):
        opening = sum(library_host.module_form(text) == "module" for text in texts)
        problems.append(f"the canary ran in the {form} form, but "
                        + (f"{opening} of the {len(module_paths)} module files the index names "
                           "open" if opening else
                           f"none of the {len(module_paths)} module files the index names opens")
                        + " with `module`")
    try:
        theorem_file = library_host.module_source(source, module)
    except library_host.HostError as exc:
        problems.append(str(exc))
    else:
        if not _plain_file(source, theorem_file.relative_to(source).as_posix()):
            problems.append(f"src.tar holds no {theorem_file.relative_to(source)}, the source "
                            f"of {module}")

    license_block, license_problem = _license(host / "license", cfg)
    facts["license"] = license_block
    if license_problem:
        problems.append(license_problem)
    if problems:
        raise CfError(f"the fetched library fails {len(problems)} check(s): {problems[0]}",
                      hint="; ".join(problems[1:5]) or
                           f"the fetched files stay in {incoming} for a look")
    return facts


def deps_problems(deps: Any, cfg: dict) -> list[str]:
    """Is `deps.json` DumpDeps' dump of the module and the prefix `cfg` names?  No
    `--package` is passed to it, so its package is the root module."""
    if not isinstance(deps, dict) or not isinstance(deps.get("repo_constants"), list):
        return ["deps.json holds no repo_constants list"]
    library = cfg["library"]
    named = {k: deps.get(k) for k in ("root_module", "module_prefix", "package")}
    want = {"root_module": library["module"], "module_prefix": library["prefix"],
            "package": library["module"]}
    if named != want:
        return [f"deps.json is the dump of root module {named['root_module']!r} under prefix "
                f"{named['module_prefix']!r} (package {named['package']!r}), but "
                f"informalize.yaml names {library['module']} under {library['prefix']}"]
    return []


def bodies_problem(deps: dict, theorem: str) -> str | None:
    """Did the dump read the proofs' bodies?  Not when the theorem's `value_deps` are empty,
    or those of every theorem of the library's modules (a module-system library the dump
    could not read): the scope would silently shrink to the closure through types.  Checked
    by fetch-library, before anything is replaced, and by scope."""
    rows = [r for r in deps.get("repo_constants") or [] if isinstance(r, dict)]
    own = [r for r in rows if r.get("name") == theorem]
    if own and not any(r.get("value_deps") for r in own):
        return (f"deps.json records no value dependency of {theorem}: its proof's body was not "
                "loaded")
    theorems = [r for r in rows if r.get("kind") == "theorem"]
    if theorems and not any(r.get("value_deps") for r in theorems):
        return (f"deps.json records no value dependency for any of the library's "
                f"{len(theorems)} theorems: the proofs' bodies were not loaded")
    return None


def _plain_file(root: Path, relative: str) -> bool:
    """Is `relative` an ordinary file under `root`, reached through no link?"""
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        return False
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            return False
    return current.is_file()


def _license(directory: Path, cfg: dict) -> tuple[dict, str | None]:
    """The library's license from its license files, against a manual one.

    Files that name two licenses (a dual-licensed library's LICENSE-APACHE and LICENSE-MIT)
    name none on their own; a manual license that is one of them stands, and the receipt
    records every license the files name."""
    library = cfg["library"]
    files: dict[str, str | None] = {}
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if path.is_file() and not path.is_symlink():
                files[path.name] = license_of(path.read_text(encoding="utf-8", errors="replace"))
    named = sorted({spdx for spdx in files.values() if spdx})
    manual = library.get("license_method") == "manual"
    block = {"spdx": None, "method": "manual" if manual else "license file", "named": named,
             "files": files}
    if manual:
        block["spdx"] = library.get("license")
        if named and library.get("license") not in named:
            return block, (f"the license files name {', '.join(named)}, informalize.yaml says "
                           f"{library.get('license')} (set by hand); one of them is wrong")
        return block, None
    if len(named) == 1:
        block["spdx"] = named[0]
        return block, None
    why = ("there is no license file at the library's root" if not files else
           f"its license files name {', '.join(named)}, so pick the one this companion "
           "follows" if named else
           f"no row of the license table matches {', '.join(files)}")
    return block, (f"the library's license is not known: {why}; name it with "
                   f"`cf informalize init {cfg['id']} --library-license <SPDX id> --force` "
                   "and the flags it was made with")


@dataclass
class FetchResult:
    """What fetch-library did."""

    commands: list[list[str]]
    facts: dict = field(default_factory=dict)
    receipt: Path | None = None
    license_written: bool = False
    commit: str | None = None


def fetch_library(workspace, informalize_id: str, host_name: str, *, execute: bool,
                  consent: bool,
                  runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None
                  ) -> FetchResult:
    """Fetch I1 from the host, check it, put it in place, and write the I1 receipt.

    Without `execute` it only says what it would run."""
    paths = workspace.paths
    cfg = config.load(paths, informalize_id, sessions=config.session_limit(workspace))
    host, remote = host_folder(workspace, host_name, informalize_id)
    library = _check_home(workspace, informalize_id)
    incoming = library / INCOMING
    commands = fetch_commands(cfg, host, remote, incoming)
    result = FetchResult(commands=commands)
    if not execute:
        return result
    if not consent:
        raise CfError("refusing to fetch from the host without --i-have-consent",
                      hint="the library's build and its source come from a host you were allowed "
                           "to use; say so with --i-have-consent (recorded in the I1 receipt)")
    repo = OutputRepo(paths, informalize_id)
    repo.require_stamp()
    run = runner or _run
    probe, *copies = commands
    asked = run(probe)
    if asked.returncode == 1:
        raise CfError(f"{host['name']} has no {remote.shell('library/host/DONE')} yet: the job "
                      "is running, or never started",
                      hint=f"`cf informalize build-command {informalize_id} --host "
                           f"{host['name']}` prints the lines that start it and the one that "
                           "watches it")
    if asked.returncode != 0:
        raise CfError(f"could not ask {host['name']} for DONE (ssh exited {asked.returncode}): "
                      f"{(asked.stderr or '').strip()[:200]}")
    if incoming.exists():
        _remove(incoming)
    incoming.mkdir(parents=True)
    for number, argv in enumerate(copies):
        done = run(argv)
        if done.returncode != 0:
            raise CfError(f"{argv[0]} exited {done.returncode}: "
                          f"{(done.stderr or done.stdout or '').strip()[:300]}",
                          hint=f"the command was: {' '.join(shlex.quote(a) for a in argv)}")
        if number == 0:
            # host/ came first: a job that failed left no dump or index to copy
            check_done(incoming / "host")
    facts = check_fetched(incoming, cfg, paths.informalize_library_to_host(informalize_id))
    for name in FETCHED:
        target = library / name
        if target.exists() or target.is_symlink():
            _remove(target)
        os.replace(incoming / name, target)
    _remove(incoming)
    result.facts = facts
    result.license_written = _record_license(paths, cfg, facts["license"]["spdx"])
    cfg = config.load(paths, informalize_id, sessions=config.session_limit(workspace))
    if runtime.record_source_date_epoch(repo, cfg, facts["source_date_epoch"]):
        result.commit = repo.commit([runtime.STAMP],
                                    "Record the library commit's time for the PDF build (I1)")
    facts["consent"] = {"i_have_consent": True, "operator": getpass.getuser(),
                        "host": host["name"]}
    result.receipt = _write_receipt(workspace, informalize_id, facts)
    return result


def _record_license(paths, cfg: dict, spdx: str | None) -> bool:
    """Write the discovered license into informalize.yaml; True when it changed."""
    library = cfg["library"]
    if spdx is None or library.get("license") == spdx:
        return False
    doc = copy.deepcopy(cfg)
    doc["library"]["license"] = spdx
    doc["library"].pop("license_method", None)
    config.write(paths.informalize_yaml(cfg["id"]), doc)
    return True


def _write_receipt(workspace, informalize_id: str, facts: dict) -> Path:
    """The I1 receipt: its inputs are informalize.yaml and what the host job ran, as it
    recorded it in `host/drivers.json` (equal to `to-host/`'s, which fetch checked)."""
    home = workspace.paths.informalize_dir(informalize_id)
    to_host = workspace.paths.informalize_library_to_host(informalize_id)
    inputs = {"informalize.yaml": sha256_file(home / "informalize.yaml")}
    ran = json.loads((workspace.paths.informalize_library(informalize_id) / "host"
                      / "drivers.json").read_text(encoding="utf-8"))
    prefix = to_host.relative_to(home).as_posix()
    inputs.update({f"{prefix}/{name}": sha for name, sha in sorted(ran.items())})
    outputs = {}
    for relative in ("library/index/index.jsonl", "library/index/manifest.json",
                     "library/deps.json", "library/host/src.tar", "library/host/lean-toolchain",
                     "library/host/lake-manifest.json", "library/host/canary/index.jsonl",
                     f"repo/{runtime.STAMP}"):
        outputs[relative] = sha256_file(home / relative)
    return stages.write_receipt(workspace.paths, informalize_id, "I1", inputs=inputs,
                                outputs=outputs, checks=facts)


def current_receipt(workspace, informalize_id: str) -> dict:
    """The I1 receipt, refused when it is missing or a file it pins has changed since."""
    paths = workspace.paths
    receipt = stages.read_receipt(paths, informalize_id, "I1")
    if receipt is None:
        raise CfError(f"stage I1 has not run for {informalize_id}: no stages/I1.json",
                      hint=f"`cf informalize build-command {informalize_id} --host <host>`, "
                           f"then `cf informalize fetch-library {informalize_id} --host <host> "
                           "--execute --i-have-consent`")
    home = paths.informalize_dir(informalize_id)
    moved = stages.stale_pins(home, {k: v for k, v in receipt["outputs"].items()
                                     if k.startswith("library/")})
    if moved:
        raise CfError(f"the library changed since stage I1 fetched it: {moved[0]}",
                      hint=f"`cf informalize fetch-library {informalize_id} --host <host> "
                           "--execute --i-have-consent` fetches and checks it again")
    return receipt
