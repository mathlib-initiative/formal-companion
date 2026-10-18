"""``universe.yaml``: a named, frozen set of Lean libraries at 40-hex commits.

New code for docs/proposals/wikipedia-locate.md section 2.3 (universes), in the
shape references/locate-documents.md section 8 fixes.  One file per universe,
``corpora/<universe>/universe.yaml``, written atomically through
``cflib.yamlio`` and read by every locate stage that names a library:

* ``libraries`` -- one row per library: the universe-local ``id`` a document
  names as ``library``, ``brain_lib``, ``repo``, ``commit``, ``toolchain``,
  ``roots``, ``licence``, ``conventions`` and, once ingested, ``index: {path,
  sha256, declarations}``;
* ``published_index`` on a library row -- the recorded acceptance of an index
  the library publishes itself, which the probe and its canary never checked;
* ``excluded`` -- ``{id, reason}`` for a library that failed to build or probe,
  so an absence honestly names the smaller universe;
* ``canaries`` -- one row per toolchain whose acceptance canary was ingested
  (section 2.4): no library on a toolchain without a passed canary is ingested;
* ``corpus_db`` -- ``corpus.sqlite``'s sha256, set by ``build-db`` and cleared
  by any ingest that changes an index;
* ``universe_sha256`` -- recomputed on every save: sha256 over
  ``<id>\\t<commit>\\t<index sha256>``, one line per library sorted by id,
  joined with ``\\n``.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Iterable

from cflib.errors import CfError
from cflib.hashing import sha256_text
from cflib.yamlio import read_yaml, write_yaml

#: the universe-local library id: what the host drivers' universe list accepts too
LIBRARY_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_REPO = re.compile(r"https://[A-Za-z0-9.-]+/[A-Za-z0-9._/-]+\Z")
#: an elan toolchain spelling (``leanprover/lean4:v4.34.0``,
#: ``leanprover/lean4-nightly:nightly-2026-10-01``) or a bare version: nothing that
#: needs quoting in the shell lines ``cf corpus probe-command`` prints
TOOLCHAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._+-]*)?\Z")
#: a root module glob, exactly as the host driver's universe list accepts one
#: (``universe_host._GLOB``: a module name, optionally ``.+`` or ``.*``)
_MODULE = r"[A-Za-z_][A-Za-z0-9_'!?]*(?:\.[A-Za-z_][A-Za-z0-9_'!?]*)*"
ROOT_GLOB = re.compile(rf"{_MODULE}(?:\.\+|\.\*)?\Z")
_TOOLCHAIN_PREFIX = "leanprover/lean4:"

#: the order the keys of one library row are written in
LIBRARY_KEYS = ("id", "brain_lib", "repo", "commit", "toolchain", "roots", "licence",
                "conventions", "index")


def universe_sha256(libraries: Iterable[dict]) -> str:
    """sha256 over ``<id>\\t<commit>\\t<index sha256>`` per library, sorted by id, ``\\n``-joined.

    A library not yet ingested contributes an empty index sha, so the value is
    defined at every stage; ``Universe.validate(require_indexes=True)`` is what
    refuses to build a database over such a universe.
    """
    lines = []
    for lib in sorted(libraries, key=lambda row: str(row.get("id") or "")):
        index = lib.get("index") if isinstance(lib.get("index"), dict) else {}
        lines.append(f"{lib.get('id') or ''}\t{lib.get('commit') or ''}\t"
                     f"{(index or {}).get('sha256') or ''}")
    return sha256_text("\n".join(lines))


def lean_version(toolchain: str | None) -> str:
    """``leanprover/lean4:v4.34.0-rc2`` or ``v4.34.0-rc2`` or ``4.34.0-rc2`` -> ``4.34.0-rc2``.

    The probe's header carries ``Lean.versionString`` and a lake checkout's
    ``lean-toolchain`` the elan spelling; a canary recorded under one must match
    a library declared under the other.
    """
    text = str(toolchain or "").strip()
    if text.startswith(_TOOLCHAIN_PREFIX):
        text = text[len(_TOOLCHAIN_PREFIX):]
    if text[:1] == "v" and text[1:2].isdigit():
        text = text[1:]
    return text


class Universe:
    """One ``universe.yaml``: load, change through the methods below, :meth:`save`."""

    def __init__(self, path: Path, data: dict[str, Any]):
        self.path = Path(path)
        self.data = data

    # -- construction ----------------------------------------------------
    @classmethod
    def create(cls, path: Path, universe_id: str, frozen: str | None = None) -> "Universe":
        """A new, empty universe; refused when ``path`` already holds one."""
        if not LIBRARY_ID.match(str(universe_id or "")):
            raise CfError(f"universe id {universe_id!r} is not [a-z0-9][a-z0-9._-]*",
                          hint="a short lowercase id such as u1")
        if Path(path).exists():
            raise CfError(f"{path} already exists",
                          hint="a universe is frozen once made; use another id for a new one")
        frozen = frozen or _dt.date.today().isoformat()
        if not _DATE.match(str(frozen)):
            raise CfError(f"--frozen {frozen!r} is not a YYYY-MM-DD date")
        data = {"id": universe_id, "frozen": str(frozen), "libraries": [], "excluded": [],
                "canaries": [], "corpus_db": None}
        return cls(Path(path), data)

    @classmethod
    def load(cls, path: Path) -> "Universe":
        path = Path(path)
        if not path.is_file():
            raise CfError(f"no universe at {path}",
                          hint="create it with `cf corpus init <universe>`")
        data = read_yaml(path)
        if not isinstance(data, dict):
            raise CfError(f"{path} is not a YAML mapping")
        data.setdefault("libraries", [])
        data.setdefault("excluded", [])
        data.setdefault("canaries", [])
        data.setdefault("corpus_db", None)
        if data.get("frozen") is not None:
            data["frozen"] = str(data["frozen"])
        universe = cls(path, data)
        problems = universe.validate()
        if problems:
            raise CfError(f"{path} is not a valid universe: " + "; ".join(problems[:5]),
                          hint="fix the file, or recreate the universe with `cf corpus init`")
        return universe

    # -- reading ---------------------------------------------------------
    @property
    def id(self) -> str:
        return str(self.data.get("id") or "")

    @property
    def frozen(self) -> str:
        return str(self.data.get("frozen") or "")

    @property
    def libraries(self) -> list[dict]:
        return list(self.data.get("libraries") or [])

    @property
    def excluded(self) -> list[dict]:
        return list(self.data.get("excluded") or [])

    @property
    def canaries(self) -> list[dict]:
        return list(self.data.get("canaries") or [])

    @property
    def corpus_db(self) -> dict | None:
        value = self.data.get("corpus_db")
        return value if isinstance(value, dict) else None

    def library_ids(self) -> list[str]:
        return [str(lib.get("id")) for lib in self.libraries]

    def library(self, library_id: str) -> dict:
        for lib in self.libraries:
            if lib.get("id") == library_id:
                return lib
        for row in self.excluded:
            if row.get("id") == library_id:
                raise CfError(f"library {library_id!r} is excluded from universe {self.id}: "
                              f"{row.get('reason')}")
        raise CfError(f"universe {self.id} has no library {library_id!r}",
                      hint="known: " + (", ".join(self.library_ids()) or "none") +
                           "; add it with `cf corpus add`")

    def sha256(self) -> str:
        return universe_sha256(self.libraries)

    def canary(self, toolchain: str) -> dict | None:
        """The canary row of a toolchain, matched on the Lean version, or None."""
        want = lean_version(toolchain)
        for row in self.canaries:
            if lean_version(row.get("toolchain")) == want:
                return row
        return None

    def canary_passed(self, toolchain: str) -> bool:
        row = self.canary(toolchain)
        return bool(row and row.get("passed") is True)

    # -- changing --------------------------------------------------------
    def add_library(self, *, library_id: str, repo: str, commit: str, toolchain: str,
                    roots: list[str], brain_lib: str | None = None,
                    licence: str | None = None, conventions: str | None = None) -> dict:
        """Add one library row; refused when the id exists with other facts or is excluded.

        Re-adding the same id with the same repo, commit and toolchain is a no-op,
        so the command is idempotent; changing a pinned commit is not -- a
        universe is frozen, and a new pin is a new universe.
        """
        row = {"id": library_id, "brain_lib": brain_lib or library_id, "repo": repo,
               "commit": commit, "toolchain": toolchain, "roots": list(roots),
               "licence": {"spdx": licence} if licence else None,
               "conventions": conventions, "index": None}
        problems = _library_problems(row)
        if problems:
            raise CfError(f"library {library_id!r}: " + "; ".join(problems))
        if any(r.get("id") == library_id for r in self.excluded):
            raise CfError(f"library {library_id!r} is excluded from universe {self.id}",
                          hint="an exclusion is a record; use a new universe to include it")
        for existing in self.libraries:
            if existing.get("id") != library_id:
                continue
            same = all(existing.get(k) == row[k] for k in ("repo", "commit", "toolchain"))
            if not same:
                raise CfError(f"library {library_id!r} is already pinned at "
                              f"{str(existing.get('commit'))[:12]} ({existing.get('repo')})",
                              hint="a universe is frozen: make a new universe for a new pin")
            return existing
        if brain_lib:
            for existing in self.libraries:
                if existing.get("brain_lib") == brain_lib and existing.get("repo") != repo:
                    raise CfError(f"brain_lib {brain_lib!r} already names {existing.get('id')}",
                                  hint="a brain_lib token is unique per repository (section 11.3)")
        self.data.setdefault("libraries", []).append(row)
        return row

    def exclude(self, library_id: str, reason: str) -> dict:
        """Record that a library is not in the universe, and why; drops its row if present."""
        if not LIBRARY_ID.match(str(library_id or "")):
            raise CfError(f"library id {library_id!r} is not [a-z0-9][a-z0-9._-]*")
        reason = " ".join(str(reason or "").split())
        if not reason:
            raise CfError("an exclusion needs a reason", hint="--exclude 'licence forbids ML use'")
        self.data["libraries"] = [lib for lib in self.libraries if lib.get("id") != library_id]
        rows = [r for r in self.excluded if r.get("id") != library_id]
        row = {"id": library_id, "reason": reason}
        rows.append(row)
        self.data["excluded"] = sorted(rows, key=lambda r: str(r.get("id")))
        self.clear_corpus_db()
        return row

    def set_index(self, library_id: str, *, path: str, sha256: str, declarations: int,
                  published: dict | None = None) -> None:
        """Pin a library's normalised index.

        ``published`` is the recorded acceptance of an index the library
        publishes itself (``{schema, reason, accepted}``): the probe did not
        produce it, so its canary says nothing about it, and the row says who
        took it on trust and why.  A probe index clears any earlier one.
        """
        lib = self.library(library_id)
        new = {"path": path, "sha256": sha256, "declarations": int(declarations)}
        if lib.get("index") != new:
            lib["index"] = new
            self.clear_corpus_db()
        if published:
            lib["published_index"] = dict(published)
        else:
            lib.pop("published_index", None)

    def record_canary(self, *, toolchain: str, passed: bool, cases: dict, index_sha256: str,
                      checked: str) -> dict:
        row = {"toolchain": toolchain, "lean_version": lean_version(toolchain),
               "passed": bool(passed), "checked": checked, "index_sha256": index_sha256,
               "cases": cases}
        rows = [r for r in self.canaries
                if lean_version(r.get("toolchain")) != lean_version(toolchain)]
        rows.append(row)
        self.data["canaries"] = sorted(rows, key=lambda r: str(r.get("lean_version")))
        return row

    def set_corpus_db(self, *, sha256: str, built: str, declarations: int) -> None:
        self.data["corpus_db"] = {"path": "corpus.sqlite", "sha256": sha256, "built": built,
                                  "declarations": int(declarations),
                                  "universe_sha256": self.sha256()}

    def clear_corpus_db(self) -> None:
        """An index changed: the database no longer describes this universe."""
        self.data["corpus_db"] = None

    # -- checking and writing ----------------------------------------------
    def validate(self, *, require_indexes: bool = False) -> list[str]:
        problems: list[str] = []
        if not LIBRARY_ID.match(self.id):
            problems.append(f"id {self.id!r} is not [a-z0-9][a-z0-9._-]*")
        if self.frozen and not _DATE.match(self.frozen):
            problems.append(f"frozen {self.frozen!r} is not a YYYY-MM-DD date")
        seen: set[str] = set()
        for lib in self.libraries:
            if not isinstance(lib, dict):
                problems.append(f"a library row is not a mapping: {lib!r}")
                continue
            lib_id = str(lib.get("id") or "")
            if lib_id in seen:
                problems.append(f"library {lib_id!r} is listed twice")
            seen.add(lib_id)
            problems.extend(f"library {lib_id!r}: {p}" for p in _library_problems(lib))
            index = lib.get("index")
            if index is not None:
                if not isinstance(index, dict) or not SHA256.match(str(index.get("sha256") or "")):
                    problems.append(f"library {lib_id!r}: index has no sha256")
            elif require_indexes:
                problems.append(f"library {lib_id!r} has no index; run `cf corpus ingest`")
        for row in self.excluded:
            if not isinstance(row, dict) or not row.get("reason"):
                problems.append(f"an exclusion carries no reason: {row!r}")
            elif row.get("id") in seen:
                problems.append(f"library {row.get('id')!r} is both listed and excluded")
        brain: dict[str, str] = {}
        for lib in self.libraries:
            token = lib.get("brain_lib")
            if token and token in brain and brain[token] != lib.get("repo"):
                problems.append(f"brain_lib {token!r} names two repositories")
            if token:
                brain[token] = str(lib.get("repo"))
        if require_indexes and not self.libraries:
            problems.append("the universe has no library")
        return problems

    def save(self) -> Path:
        problems = self.validate()
        if problems:
            raise CfError(f"refusing to write an invalid universe: " + "; ".join(problems[:5]))
        libraries = [{key: lib.get(key) for key in LIBRARY_KEYS}
                     | {k: v for k, v in lib.items() if k not in LIBRARY_KEYS}
                     for lib in self.libraries]
        out = {"id": self.id, "frozen": self.frozen,
               "libraries": sorted(libraries, key=lambda lib: str(lib.get("id"))),
               "excluded": self.excluded, "canaries": self.canaries,
               "corpus_db": self.corpus_db, "universe_sha256": universe_sha256(libraries)}
        self.data = out
        return write_yaml(self.path, out,
                          header=f"# universe.yaml -- {self.id} (references/locate-documents.md "
                                 f"section 8). Written by `cf corpus`; universe_sha256 is "
                                 f"recomputed on every write.\n")


def _library_problems(lib: dict) -> list[str]:
    problems: list[str] = []
    if not LIBRARY_ID.match(str(lib.get("id") or "")):
        problems.append(f"id {lib.get('id')!r} is not [a-z0-9][a-z0-9._-]*")
    if not _REPO.match(str(lib.get("repo") or "")):
        problems.append(f"repo {lib.get('repo')!r} is not an https repository URL")
    if not COMMIT.match(str(lib.get("commit") or "")):
        problems.append(f"commit {lib.get('commit')!r} is not 40 lowercase hex digits")
    if not TOOLCHAIN.match(str(lib.get("toolchain") or "")):
        problems.append(f"toolchain {lib.get('toolchain')!r} is not an elan toolchain such as "
                        f"leanprover/lean4:v4.34.0")
    roots = lib.get("roots")
    if not isinstance(roots, list) or not roots:
        problems.append("roots must be a non-empty list of module names")
    else:
        bad = [r for r in roots if not isinstance(r, str) or not ROOT_GLOB.match(r)]
        if bad:
            problems.append(f"roots {bad!r} are not Lake module globs (a module name, "
                            f"optionally .+ or .*), which the host driver refuses")
    return problems
