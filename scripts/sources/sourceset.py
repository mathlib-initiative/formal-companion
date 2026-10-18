"""Sourcesets: append-only, versioned manifests of web sources, plus their files.

New code, implementing docs/proposals/wikipedia-locate.md section 2.1
("Sourcesets") in the layout of references/locate-documents.md section 7.
Twenty thousand sources fit neither ``workspace.yaml`` -- which every command
validates -- nor a single rewritten file, so a sourceset is:

* ``manifest-<n>.jsonl``: a header line, then one canonical JSON entry per source
  sorted by ``source_id``.  A version is written once and never again; version
  n+1 holds every source of version n with every non-null field unchanged
  (:func:`append_only_problems`), names version n's sha as
  ``previous_sha256``, and is written only if no other writer got there first
  (:meth:`Sourceset.write_version`).  A run pins ``(id, version, sha256)``.
* ``sources/<source-id>/``: the raw bytes, the canonical body and the rendered
  views of one source, validated lazily against the shas its entry records
  (:meth:`Sourceset.source_problems`) -- never all twenty thousand at load.
* ``state.json``: the sourceset-level state file locate writes instead of
  ``companions/<name>/state.json``: the latest version and its counts, plus a
  section per command that wrote one (:meth:`Sourceset.record_state`).

Nothing here touches the network (``sources.wikinet`` does) or renders
(``sources.render_html`` does).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from cflib import registry as registry_mod
from cflib import state as state_mod
from cflib import vocab
from cflib.errors import CfError
from cflib.jsonio import read_json, write_json
from cflib.paths import WorkspacePaths

from . import websource

SCHEMA = "cf-sourceset/1"
STATE_SCHEMA = "cf-sourceset-state/1"
#: keys of state.json that are no section: the header, and what refresh_state owns
_STATE_RESERVED = frozenset({"schema", "id", "family", "manifest", "counts", "updated_at"})
#: a source directory's files whose shas an entry records
SHA_FILES = {"raw_sha256": "source.raw.html", "html_sha256": "body.html",
             "layer_sha256": "layer.txt"}


class SourcesetError(CfError):
    """A sourceset that is missing, malformed, or would stop being append-only."""


#: characters some line splitters break on that ``json.dumps(ensure_ascii=False)``
#: leaves raw; a manifest line escapes them so every reader sees one line per entry
_LINE_BREAKERS = {"\u0085": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029"}


def canonical_line(obj: dict) -> str:
    """One manifest line: sorted keys, no spaces, UTF-8 as is except U+0085, U+2028, U+2029."""
    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for raw, escaped in _LINE_BREAKERS.items():
        line = line.replace(raw, escaped)
    return line


def split_lines(text: str) -> list[str]:
    """A manifest's lines: split on LF only, the one separator a manifest is written with."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Manifest:
    """One version of a sourceset, as read from disk."""

    sourceset_id: str
    version: int
    path: Path | None
    sha256: str | None
    header: dict
    entries: dict[str, dict] = field(default_factory=dict)

    @property
    def family(self) -> str:
        return str(self.header.get("family") or "")

    def get(self, source_id: str) -> dict | None:
        return self.entries.get(source_id)

    def __iter__(self):
        return iter(self.entries[k] for k in sorted(self.entries))

    def __len__(self) -> int:
        return len(self.entries)

    def pin(self) -> dict:
        """What a run records: ``{id, version, manifest_sha256}``."""
        return {"id": self.sourceset_id, "version": self.version,
                "manifest_sha256": self.sha256}

    def preferred(self) -> dict[int, str]:
        """``{pageid: source_id}``: the one source per page a run plans.

        The proposal's section 2.1: a revision a consumer pinned is adopted
        INSTEAD of one the sourceset chose, so a page with an adopted source plans
        it (the highest adopted revision when there are several), and any other
        page plans its highest enumerated revision.
        """
        best: dict[int, tuple[int, int, str]] = {}
        for entry in self.entries.values():
            adopted = 1 if is_adopted(entry) else 0
            key = (adopted, entry["revision"], entry["source_id"])
            if entry["pageid"] not in best or key > best[entry["pageid"]]:
                best[entry["pageid"]] = key
        return {pageid: key[2] for pageid, key in sorted(best.items())}

    def adopted_pages(self) -> set[int]:
        """Page ids with an adopted source: the pages a consumer pinned."""
        return {e["pageid"] for e in self.entries.values() if is_adopted(e)}


def is_adopted(entry: dict) -> bool:
    """Did a consumer pin this source -- adopted outright, or its bytes adopted later?"""
    return entry.get("origin") == vocab.SOURCE_ORIGIN_ADOPTED or bool(entry.get("adopted_from"))


def append_only_problems(base: Manifest, entries: dict[str, dict]) -> list[str]:
    """Why ``entries`` cannot follow ``base``: a dropped source or a changed value."""
    out: list[str] = []
    for sid, old in sorted(base.entries.items()):
        new = entries.get(sid)
        if new is None:
            out.append(f"{sid}: version {base.version} holds it and the new version does not "
                       f"(a sourceset never drops a source)")
            continue
        for key in sorted(set(old) | set(new)):
            was = old.get(key)
            if was is not None and new.get(key) != was:
                out.append(f"{sid}: {key} was {was!r} in version {base.version} and would "
                           f"become {new.get(key)!r} (a recorded value never changes)")
    return out


def _write_new(path: Path, text: str) -> None:
    """Create ``path`` with ``text`` atomically, refusing when it already exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise SourcesetError(
                f"{path.name} appeared while this command ran: another writer added a "
                f"version first", hint="re-run the command; it builds on the newest version")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class Sourceset:
    """One sourceset of a workspace."""

    def __init__(self, paths: WorkspacePaths, sourceset_id: str):
        self.paths = paths
        self.id = sourceset_id
        self.dir = paths.sourceset_dir(sourceset_id)

    # -- creation and loading ------------------------------------------
    @classmethod
    def create(cls, paths: WorkspacePaths, sourceset_id: str, family: str,
               *, workspace=None, now: str | None = None) -> "Sourceset":
        """A new sourceset: ``manifest-1.jsonl`` with the header and no sources."""
        websource.family(family, workspace)          # refuses an unknown family
        ss = cls(paths, sourceset_id)
        if paths.sourceset_manifests(sourceset_id):
            raise SourcesetError(f"sourceset {sourceset_id!r} already exists at {ss.dir}",
                                 hint=f"`cf sourceset status {sourceset_id}` shows it")
        header = {"schema": SCHEMA, "id": sourceset_id, "version": 1, "family": family,
                  "previous_sha256": None, "created": now or state_mod.now(), "sources": 0}
        _write_new(paths.sourceset_manifest(sourceset_id, 1), canonical_line(header) + "\n")
        ss.refresh_state(command="create")
        return ss

    @classmethod
    def open(cls, paths: WorkspacePaths, sourceset_id: str) -> "Sourceset":
        ss = cls(paths, sourceset_id)
        if not paths.sourceset_manifests(sourceset_id):
            raise SourcesetError(f"no sourceset {sourceset_id!r} in this workspace",
                                 hint=f"create it: cf sourceset create {sourceset_id}")
        return ss

    def versions(self) -> list[int]:
        return [n for n, _path in self.paths.sourceset_manifests(self.id)]

    def latest(self) -> Manifest:
        versions = self.versions()
        if not versions:
            raise SourcesetError(f"sourceset {self.id!r} has no manifest")
        return self.load(versions[-1])

    def load(self, version: int) -> Manifest:
        """Read and check one version: header, order, entries, and the chain to the one before."""
        path = self.paths.sourceset_manifest(self.id, version)
        if not path.is_file():
            raise SourcesetError(f"sourceset {self.id!r} has no version {version}",
                                 hint=f"versions on disk: {self.versions()}")
        raw = path.read_bytes()
        lines = split_lines(raw.decode("utf-8"))
        if not lines:
            raise SourcesetError(f"{path} is empty")
        try:
            header = json.loads(lines[0])
            rows = [json.loads(line) for line in lines[1:] if line.strip()]
        except ValueError as exc:
            raise SourcesetError(f"{path}: not JSON lines ({exc})") from None
        problems: list[str] = []
        if not isinstance(header, dict) or header.get("schema") != SCHEMA:
            problems.append(f"line 1 is not a {SCHEMA} header")
        elif header.get("id") != self.id or header.get("version") != version:
            problems.append(f"the header says sourceset {header.get('id')!r} version "
                            f"{header.get('version')!r}")
        entries: dict[str, dict] = {}
        previous = None
        for number, row in enumerate(rows, 2):
            for problem in websource.entry_problems(row):
                problems.append(f"line {number}: {problem}")
            sid = row.get("source_id") if isinstance(row, dict) else None
            if not isinstance(sid, str):
                continue
            if previous is not None and sid <= previous:
                problems.append(f"line {number}: {sid} is out of order or repeated")
            previous = sid
            if isinstance(header, dict) and row.get("family") != header.get("family"):
                problems.append(f"line {number}: family {row.get('family')!r} in a "
                                f"{header.get('family')!r} sourceset")
            entries[sid] = row
        if isinstance(header, dict) and header.get("sources") != len(entries):
            problems.append(f"the header counts {header.get('sources')!r} sources, the file "
                            f"holds {len(entries)}")
        if problems:
            raise SourcesetError(f"{path} is not a valid manifest: " + "; ".join(problems[:5])
                                 + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""))
        sha = hashlib.sha256(raw).hexdigest()
        if version > 1:
            prev_path = self.paths.sourceset_manifest(self.id, version - 1)
            if prev_path.is_file() and header.get("previous_sha256") != sha256_file(prev_path):
                raise SourcesetError(f"{path}: previous_sha256 is not the sha of version "
                                     f"{version - 1} (a version was rewritten)")
        return Manifest(self.id, version, path, sha, header, entries)

    def pinned(self, version: int, manifest_sha256: str) -> Manifest:
        """The version a run pinned, refused when its bytes are not the pinned ones."""
        manifest = self.load(version)
        if manifest.sha256 != manifest_sha256:
            raise SourcesetError(f"sourceset {self.id!r} version {version} has sha "
                                 f"{manifest.sha256[:12]}..., the pin says "
                                 f"{str(manifest_sha256)[:12]}...")
        return manifest

    # -- writing -------------------------------------------------------
    def write_version(self, base: Manifest, entries: Iterable[dict], *,
                      now: str | None = None) -> Manifest:
        """Write version ``base.version + 1`` holding ``entries``; unchanged returns ``base``.

        Refused when the result would not be append-only, when an entry is
        malformed or belongs to another family, and when a newer version than
        ``base`` is already on disk (another writer: re-run on the newest).
        """
        latest = self.versions()[-1]
        if latest != base.version:
            raise SourcesetError(f"sourceset {self.id!r} is at version {latest}, but this "
                                 f"command read version {base.version}",
                                 hint="another writer added a version; re-run the command")
        new: dict[str, dict] = {}
        problems: list[str] = []
        for row in entries:
            sid = row.get("source_id")
            problems.extend(websource.entry_problems(row))
            if row.get("family") != base.family:
                problems.append(f"{sid}: family {row.get('family')!r} in a {base.family!r} "
                                f"sourceset")
            if sid in new and new[sid] != row:
                problems.append(f"{sid}: given twice")
            new[sid] = dict(row)
        problems.extend(append_only_problems(base, new))
        if problems:
            raise SourcesetError(f"sourceset {self.id!r}: the new version is refused: "
                                 + "; ".join(problems[:5])
                                 + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""))
        if new == base.entries:
            return base
        version = base.version + 1
        header = {"schema": SCHEMA, "id": self.id, "version": version, "family": base.family,
                  "previous_sha256": base.sha256, "created": now or state_mod.now(),
                  "sources": len(new)}
        rows = [header] + [new[k] for k in sorted(new)]
        text = "".join(canonical_line(x) + "\n" for x in rows)
        # read it back as load() will, BEFORE the version exists: once linked it is
        # permanent, and one unreadable version wedges every later command
        try:
            if [json.loads(line) for line in split_lines(text)] != rows:
                raise ValueError("the lines do not read back as the entries")
        except ValueError as exc:
            raise SourcesetError(f"sourceset {self.id!r}: version {version} would not read "
                                 f"back ({exc}); nothing was written") from None
        _write_new(self.paths.sourceset_manifest(self.id, version), text)
        return self.load(version)

    # -- sources -------------------------------------------------------
    def source_dir(self, source_id: str) -> Path:
        return self.paths.sourceset_source_dir(self.id, source_id)

    def source_problems(self, entry: dict) -> list[str]:
        """What on disk disagrees with one entry's shas (checked only when asked)."""
        sid = entry.get("source_id")
        directory = self.source_dir(sid)
        out: list[str] = []
        for key, name in SHA_FILES.items():
            want = entry.get(key)
            if want is None:
                continue
            path = directory / name
            if not path.is_file():
                out.append(f"{sid}: {name} is missing, the manifest records its {key}")
                continue
            got = sha256_file(path)
            if got != want:
                out.append(f"{sid}: {name} has sha {got[:12]}..., the manifest records "
                           f"{want[:12]}...")
        return out

    # -- state ---------------------------------------------------------
    def counts(self, manifest: Manifest) -> dict:
        entries = list(manifest)
        return {
            "sources": len(entries),
            vocab.SOURCE_ORIGIN_ENUMERATED: sum(
                1 for e in entries if e["origin"] == vocab.SOURCE_ORIGIN_ENUMERATED),
            vocab.SOURCE_ORIGIN_ADOPTED: sum(1 for e in entries if is_adopted(e)),
            "fetched": sum(1 for e in entries if e.get("raw_sha256")),
            "rendered": sum(1 for e in entries if e.get("layer_sha256")),
            "pages": len({e["pageid"] for e in entries}),
            "by_class": dict(sorted(Counter(
                str((e.get("assessment") or {}).get("class") or "none")
                for e in entries).items())),
            "by_importance": dict(sorted(Counter(
                str((e.get("assessment") or {}).get("importance") or "none")
                for e in entries).items())),
        }

    def read_state(self) -> dict:
        path = self.paths.sourceset_state_json(self.id)
        data = read_json(path, default={}) if path.exists() else {}
        return data if isinstance(data, dict) else {}

    def record_state(self, section: str, payload: dict, key: str | None = None) -> dict:
        """Read-modify-write one section of ``state.json``; every other section is kept.

        This class is the file's one writer (references/locate-documents.md
        section 7): the sourceset commands, the planner, the outcomes and the
        export all record through here, so the file always carries its
        ``schema`` and ``id`` header and nothing else's.  With no ``key`` the
        section becomes ``payload``; with one, ``state[section][key]`` becomes
        ``{**what it was, **payload, "at": <now>}`` -- one run's, one command's
        or one export's row merged, its siblings kept.  The header keys and the
        counts ``refresh_state`` owns are not a section anyone else may write.
        """
        if not self.paths.sourceset_manifests(self.id):
            raise SourcesetError(f"no sourceset {self.id!r} in this workspace",
                                 hint="create it with `cf sourceset create <id>`")
        if section in _STATE_RESERVED:
            raise SourcesetError(f"{section!r} is a header of the sourceset state, not a section",
                                 hint=f"the reserved keys are {', '.join(sorted(_STATE_RESERVED))}")
        if not isinstance(payload, dict):
            raise SourcesetError(f"a state section is a mapping, not {type(payload).__name__}")
        state = self.read_state()
        state.setdefault("schema", STATE_SCHEMA)
        state["id"] = self.id
        now = state_mod.now()
        if key is None:
            state[section] = payload
        else:
            block = state.get(section)
            if block is None:
                block = {}
            elif not isinstance(block, dict):
                raise SourcesetError(f"state section {section!r} is not a mapping; it cannot "
                                     f"hold a row per key")
            existing = block.get(key) if isinstance(block.get(key), dict) else {}
            block[str(key)] = {**existing, **payload, "at": now}
            state[section] = block
        state["updated_at"] = now
        write_json(self.paths.sourceset_state_json(self.id), state, sort_keys=True)
        return state

    def refresh_state(self, *, command: str, details: dict | None = None) -> dict:
        """Write the latest version's pin and counts, and what ``command`` did."""
        manifest = self.latest()
        state = self.read_state()
        state.setdefault("schema", STATE_SCHEMA)
        state["id"] = self.id
        state["family"] = manifest.family
        state["manifest"] = {"version": manifest.version, "sha256": manifest.sha256}
        state["counts"] = self.counts(manifest)
        commands = state.get("commands") if isinstance(state.get("commands"), dict) else {}
        commands[command] = {"at": state_mod.now(), "version": manifest.version,
                             **(details or {})}
        state["commands"] = commands
        state["updated_at"] = state_mod.now()
        write_json(self.paths.sourceset_state_json(self.id), state, sort_keys=True)
        return state


def family_of(manifest: Manifest, workspace=None) -> registry_mod.SourceFamily:
    return websource.family(manifest.family, workspace)
