"""Load, validate and update workspace.yaml.

Replaces ``cflib/campaign.py`` (docs/design/archive/multi-source-model.md R1): the unit
of organisation is a COMPANION with a LIST OF SOURCES, and the directory a user
works in is a WORKSPACE holding N companions and M sources.

    workspace.yaml
      companions:  <name> -> {repo, adapter, sources[], anchor_source, ...}
      sources:     <source_id> -> {sha256, title, edition, offset, kinds, ...}

Two companions can be differentiated exactly where their source lists overlap
(:meth:`Workspace.shared_sources`), which is what makes "one companion per list
of sources" a real relation rather than a label: a differential is a fact about
the data, not a flag.

Nothing here is hard-coded -- a repo path, a host, a model, a page offset, an
errata URL and a library path are all named in workspace.yaml, and the source
fields in it were DISCOVERED (see cflib.discovery), not typed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .errors import CfError, SourceError, WorkspaceError
from .items import DEFAULT_KINDS
from .paths import WorkspacePaths
from .yamlio import dump_yaml, read_yaml
from .jsonio import atomic_write

WORKSPACE_FILE = "workspace.yaml"
WORKSPACE_VERSION = 2

#: `sources.<id>.role`: audited = a companion is checked against it; reference =
#: prepared only so page_map_to can be checked (the bundled-but-not-anchored
#: edition of docs/design/archive/multi-source-model.md S4.6).
ROLE_AUDITED = "audited"
ROLE_REFERENCE = "reference"

#: Comments re-emitted by :meth:`Workspace.save`.  workspace.yaml is committed and
#: hand-read; a YAML dump would otherwise silently delete the paragraphs that say
#: WHY a key exists, and those paragraphs are the only place some rules are
#: written down for a person.
_SECTION_COMMENTS: dict[str, str] = {
    "companions": (
        "# One entry per companion repo. `sources` is the list this companion is audited\n"
        "# against; `anchor_source` is the edition whose PAGINATION its page anchors follow,\n"
        "# which is NOT always an edition it ships (tao-measure-lean bundles the 2011 draft\n"
        "# and anchors to the published book). Everything here was discovered by\n"
        "# `cf onboard <repo>`; `discovered.signals` names the files it read.\n"
    ),
    "sources": (
        "# One entry per source, keyed by source_id and identified by sha256 of the PDF\n"
        "# BYTES. The PDF itself is never copied into the workspace or the skill repo.\n"
        "# offset: printed page = PDF page - offset. offset_confidence < 0.9 means a human\n"
        "# had to say (`cf sources set <id> offset=N`), never a guess.\n"
    ),
    "hosts": (
        "# `cf run` REFUSES any host that is not listed here, and requires --i-have-consent\n"
        "# (recorded in run.yaml). Running a fleet copies private Lean sources and\n"
        "# copyrighted source text to the host: the box owner must agree.\n"
    ),
    "models": (
        "# Never Fable for subagents: Fable-default fleets lost agents to the session limit\n"
        "# mid-run. Worker/reviewer ids must be confirmed by a 1/1 smoke test.\n"
    ),
    "defaults": (
        "# def-coverage -> attack presets -> differential -> adjudicate -> route -> report.\n"
        "# max_concurrent_sessions is a HARD account limit (references/traps.md C3): past 16\n"
        "# concurrent sessions the API returns 409s, per ACCOUNT, across workspaces.\n"
    ),
    "policy": (
        "# Nothing leaves the workspace while _meta.status is UNCHALLENGED. allow_download\n"
        "# false forbids `cf onboard --download` outright; require_confirmed_sources true\n"
        "# makes `cf plan-run` refuse a source nobody has reviewed.\n"
    ),
    "library_paths": (
        "# Where `cf onboard` may look for a PDF a repo names but does not commit. Searched\n"
        "# in order after the repo's own paths and caches and the workspace store; matched\n"
        "# by sha when one is known, by filename/title otherwise. This list is ALSO the\n"
        "# allowlist for an absolute path a companion repo names in its own files, so add to\n"
        "# it deliberately; the roots derived from this workspace are searched too -- its own\n"
        "# sources/ store, and <parent>/**/sources plus <parent>/**/.lake/official-pdf for the\n"
        "# parent of every onboarded repo, never the bare parent. `cf doctor` prints the set.\n"
    ),
}


def find_workspace_root(start: str | Path | None = None) -> Path:
    """Locate the workspace directory.

    Order: the explicit argument, ``$CF_WORKSPACE``, then the current directory
    and its ancestors (like git).  Raises WorkspaceError when nothing is found.
    """
    candidates: list[Path] = []
    if start:
        candidates.append(Path(start).expanduser())
    elif os.environ.get("CF_WORKSPACE"):
        candidates.append(Path(os.environ["CF_WORKSPACE"]).expanduser())
    else:
        candidates.append(Path.cwd())
    for cand in candidates:
        cand = cand.resolve()
        if (cand / WORKSPACE_FILE).exists():
            return cand
        for parent in cand.parents:
            if (parent / WORKSPACE_FILE).exists():
                return parent
    raise WorkspaceError(
        f"no {WORKSPACE_FILE} found from {candidates[0]}",
        hint="pass --workspace <dir>, set CF_WORKSPACE, or run `cf init <dir>`")


def _tuple(value, cast=str) -> tuple:
    if value in (None, "", {}):
        return ()
    if isinstance(value, (str, bytes)):
        return (cast(value),)
    return tuple(cast(v) for v in value)


@dataclass(frozen=True)
class SourceRef:
    """One source as ``workspace.yaml`` describes it."""

    id: str
    sha256: str | None = None
    title: str = ""
    authors: tuple[str, ...] = ()
    edition: str = ""
    isbn: str | None = None
    kinds: tuple[str, ...] = DEFAULT_KINDS
    offset: int = 0
    offset_confidence: float = 0.0
    offset_method: str = "manual"
    pages: int | None = None
    text_sha256: str | None = None
    bytes: int | None = None
    errata_url: str | None = None
    errata_confidence: float = 0.0
    #: where the errata URL came from: `registry` (a curated entry), `manual`
    #: (`cf sources set`), or `repo` (harvested from the companion's own files).
    #: Only the first two are fetched without an explicit opt-in.
    errata_method: str = "unknown"
    download_url: str | None = None
    book_page_url: str | None = None
    licence: dict = field(default_factory=dict)
    pdf: Path | None = None
    chapters: tuple[int, ...] = ()
    scope_method: str = "companion_tags"
    role: str = ROLE_AUDITED
    same_work: tuple[str, ...] = ()
    page_map_to: dict = field(default_factory=dict)
    confirmed: bool = False
    registry: str = "local"
    discovered: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, source_id: str, data: dict) -> "SourceRef":
        data = dict(data or {})
        pdf = data.get("pdf")
        return cls(
            id=source_id,
            sha256=(str(data["sha256"]).strip().lower() if data.get("sha256") else None),
            title=str(data.get("title") or ""),
            authors=_tuple(data.get("authors")),
            edition=str(data.get("edition") or ""),
            isbn=(str(data["isbn"]) if data.get("isbn") else None),
            kinds=_tuple(data.get("kinds")) or tuple(DEFAULT_KINDS),
            offset=int(data.get("offset") or 0),
            offset_confidence=float(data.get("offset_confidence") or 0.0),
            offset_method=str(data.get("offset_method") or "manual"),
            pages=(int(data["pages"]) if data.get("pages") is not None else None),
            text_sha256=(str(data["text_sha256"]) if data.get("text_sha256") else None),
            bytes=(int(data["bytes"]) if data.get("bytes") is not None else None),
            errata_url=(str(data["errata_url"]) if data.get("errata_url") else None),
            errata_confidence=float(data.get("errata_confidence") or 0.0),
            errata_method=str(data.get("errata_method") or "unknown"),
            download_url=(str(data["download_url"]) if data.get("download_url") else None),
            book_page_url=(str(data["book_page_url"]) if data.get("book_page_url") else None),
            licence=dict(data.get("licence") or {}),
            pdf=(Path(str(pdf)).expanduser() if pdf else None),
            chapters=_tuple(data.get("chapters"), int),
            scope_method=str(data.get("scope_method") or "companion_tags"),
            role=str(data.get("role") or ROLE_AUDITED),
            same_work=_tuple(data.get("same_work")),
            page_map_to=dict(data.get("page_map_to") or {}),
            confirmed=bool(data.get("confirmed")),
            registry=str(data.get("registry") or "local"),
            discovered=dict(data.get("discovered") or {}),
        )

    # -- display -------------------------------------------------------
    def short(self) -> str:
        """``tao-measure-gsm126-published (64498329)``."""
        return f"{self.id} ({self.sha256[:8]})" if self.sha256 else f"{self.id} (no sha)"

    # -- pagination ----------------------------------------------------
    def printed_page(self, pdf_page: int) -> int:
        """printed page = PDF page - offset."""
        return int(pdf_page) - self.offset

    def pdf_page(self, printed: int) -> int:
        """PDF page = printed page + offset."""
        return int(printed) + self.offset

    def shift_to(self, other_id: str, item: str | None = None) -> int | None:
        """Printed-page shift to a sibling edition, per ``page_map_to``.

        ``other_page = this_page + shift``.  ``None`` when no map is recorded.
        The exception shift applies to the items named in ``exceptions``; when
        that list is empty the map carries a prose ``note`` instead and only the
        default shift can be applied mechanically.
        """
        entry = (self.page_map_to or {}).get(other_id)
        if not isinstance(entry, dict):
            return None
        if item and item in (entry.get("exceptions") or []):
            return int(entry.get("exception_shift") or 0)
        return int(entry.get("default_shift") or 0)


@dataclass(frozen=True)
class CompanionRef:
    """One companion repo as ``workspace.yaml`` describes it."""

    name: str
    repo: Path
    adapter: str = ""
    sources: tuple[str, ...] = ()
    anchor_source: str = ""
    bundled_sources: tuple[str, ...] = ()
    owner: str = "external"
    write_policy: str = "read_only"
    commit: str | None = None
    discovered: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "CompanionRef":
        data = dict(data or {})
        sources = _tuple(data.get("sources"))
        return cls(
            name=name,
            repo=Path(str(data.get("repo") or "")).expanduser(),
            adapter=str(data.get("adapter") or ""),
            sources=sources,
            anchor_source=str(data.get("anchor_source") or (sources[0] if sources else "")),
            bundled_sources=_tuple(data.get("bundled_sources")),
            owner=str(data.get("owner") or "external"),
            write_policy=str(data.get("write_policy") or "read_only"),
            commit=(str(data["commit"]) if data.get("commit") else None),
            discovered=dict(data.get("discovered") or {}),
        )


class Workspace:
    """One workspace directory: N companions, M sources."""

    def __init__(self, root: Path, data: dict[str, Any]):
        self.root = root
        self.data = data

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"Workspace({self.root}, {len(self.sources)} sources, {len(self.companions)} companions)"

    # -- loading -------------------------------------------------------
    @classmethod
    def load(cls, start: str | Path | None = None, *, validate: bool = True) -> "Workspace":
        """Read workspace.yaml.

        ``validate=False`` is for the REPAIR path only (`cf sources remove`): a
        workspace whose file its own validator rejects cannot otherwise be
        loaded by any command, which is exactly when a human needs one.
        """
        root = find_workspace_root(start)
        data = read_yaml(root / WORKSPACE_FILE)
        if not isinstance(data, dict):
            raise WorkspaceError(f"{root / WORKSPACE_FILE}: expected a mapping at the top level")
        ws = cls(root=root, data=data)
        problems = ws.validate() if validate else []
        if problems:
            raise WorkspaceError(f"{root / WORKSPACE_FILE} is invalid: " + "; ".join(problems),
                                 hint="`cf sources remove <id>` drops a source that should never "
                                      "have been registered (it loads even a broken workspace)")
        return ws

    def save(self) -> Path:
        """Rewrite workspace.yaml, re-emitting the load-bearing section comments.

        Refuses to write a file its own loader would reject.  ``validate()`` used
        to run only in :meth:`load`, so a command could persist a contradiction
        -- two source ids on one sha256 was the real one -- and then every
        subsequent `cf` command in that directory died on reading it back, with
        no command able to load the workspace to repair it.  Only the STRUCTURAL
        rules are checked here: a save mid-command legitimately sees a source
        whose companion has not been written yet.
        """
        problems = self.validate(structural_only=True)
        if problems:
            raise WorkspaceError(
                f"refusing to write {self.paths.workspace_yaml}: " + "; ".join(problems),
                hint="nothing was written; this is a bug in the command that built this state")
        out = [f"# workspace.yaml -- N companions, M sources.\n"
               f"# Rewritten by cf on {date.today().isoformat()}; see\n"
               f"# docs/design/archive/multi-source-model.md for the schema.\n"]
        for key, value in self.data.items():
            comment = _SECTION_COMMENTS.get(key)
            out.append("\n")
            if comment:
                out.append(comment)
            out.append(dump_yaml({key: value}))
        return atomic_write(self.paths.workspace_yaml, "".join(out))

    # -- sections ------------------------------------------------------
    @property
    def paths(self) -> WorkspacePaths:
        return WorkspacePaths(root=self.root)

    @property
    def name(self) -> str:
        return str((self.data.get("workspace") or {}).get("name") or self.root.name)

    # -- sources -------------------------------------------------------
    @property
    def sources(self) -> dict[str, SourceRef]:
        raw = self.data.get("sources") or {}
        if not isinstance(raw, dict):
            raise WorkspaceError("workspace.yaml `sources:` must be a map keyed by source_id")
        return {sid: SourceRef.from_dict(sid, body) for sid, body in raw.items()}

    def source(self, source_id: str) -> SourceRef:
        known = self.sources
        if source_id not in known:
            raise SourceError(
                f"unknown source {source_id!r}",
                hint=f"known: {', '.join(sorted(known)) or '(none)'} -- "
                     "run `cf onboard <repo>` or `cf sources add <pdf>`")
        return known[source_id]

    def source_ids(self, *, role: str | None = None) -> list[str]:
        return [sid for sid, ref in self.sources.items() if role is None or ref.role == role]

    def source_by_sha(self, sha: str) -> SourceRef | None:
        sha = (sha or "").strip().lower()
        if not sha:
            return None
        for ref in self.sources.values():
            if ref.sha256 and ref.sha256 == sha:
                return ref
        return None

    def upsert_source(self, source_id: str, **fields) -> dict:
        """Add or update ``sources.<id>`` (does not save)."""
        table = self.data.setdefault("sources", {})
        if not isinstance(table, dict):
            raise WorkspaceError("workspace.yaml `sources:` must be a map")
        body = table.setdefault(source_id, {})
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, Path):
                value = str(value)
            elif isinstance(value, tuple):
                value = list(value)
            body[key] = value
        return body

    def remove_source(self, source_id: str) -> list[str]:
        """Drop ``sources.<id>`` and every reference to it; returns the companions
        left with no source at all (does not save).

        A companion with an empty source list is not a workspace this loader will
        read, and this is the REPAIR command: it must not leave behind the state
        it exists to undo.  The caller decides what to do with the names -- `cf
        sources remove` drops those companions and says how to bring them back.
        """
        (self.data.get("sources") or {}).pop(source_id, None)
        orphaned: list[str] = []
        for name, body in (self.data.get("companions") or {}).items():
            if not isinstance(body, dict):
                continue
            for key in ("sources", "bundled_sources"):
                if isinstance(body.get(key), list):
                    body[key] = [s for s in body[key] if s != source_id]
            if not body.get("sources"):
                orphaned.append(name)
            elif body.get("anchor_source") == source_id:
                body["anchor_source"] = body["sources"][0]
        return orphaned

    # -- companions ----------------------------------------------------
    @property
    def companions(self) -> dict[str, CompanionRef]:
        raw = self.data.get("companions") or {}
        if not isinstance(raw, dict):
            raise WorkspaceError(
                "workspace.yaml `companions:` must be a map keyed by companion name",
                hint="schema version 2 replaced the list with a map; see "
                     "docs/design/archive/multi-source-model.md S1.1")
        return {name: CompanionRef.from_dict(name, body) for name, body in raw.items()}

    def companion(self, name: str) -> CompanionRef:
        known = self.companions
        if name not in known:
            raise WorkspaceError(
                f"unknown companion {name!r}",
                hint=f"known: {', '.join(known) or '(none)'} -- run `cf onboard <repo>`")
        return known[name]

    def companion_names(self) -> list[str]:
        """Names from workspace.yaml, plus any directory on disk with an adapter."""
        listed = list(self.companions)
        for name in self.paths.list_companions():
            if name not in listed:
                listed.append(name)
        return listed

    def upsert_companion(self, name: str, **fields) -> dict:
        """Add or update ``companions.<name>`` (does not save)."""
        table = self.data.setdefault("companions", {})
        if not isinstance(table, dict):
            raise WorkspaceError("workspace.yaml `companions:` must be a map")
        body = table.setdefault(name, {})
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, Path):
                value = str(value)
            elif isinstance(value, tuple):
                value = list(value)
            body[key] = value
        return body

    # -- the multi-source joins (R1) -----------------------------------
    def sources_of(self, companion: str) -> list[str]:
        return list(self.companion(companion).sources)

    def companions_of(self, source_id: str) -> list[str]:
        return [name for name, comp in self.companions.items() if source_id in comp.sources]

    def shared_sources(self, min_companions: int = 2) -> dict[str, list[str]]:
        """``{source_id: [companion, ...]}`` for every source ``min_companions`` audit.

        A differential is possible exactly for these keys (R1): two companions
        can be compared only where their source lists overlap.
        """
        out: dict[str, list[str]] = {}
        for sid in self.sources:
            names = self.companions_of(sid)
            if len(names) >= min_companions:
                out[sid] = names
        return out

    def anchor_source(self, companion: str) -> str:
        comp = self.companion(companion)
        return comp.anchor_source or (comp.sources[0] if comp.sources else "")

    def kinds(self, source_id: str) -> tuple[str, ...]:
        """Item kinds of ONE source: the strict grammar is per source."""
        return self.source(source_id).kinds

    def offset(self, source_id: str) -> int:
        return self.source(source_id).offset

    def chapters(self, source_id: str) -> tuple[int, ...]:
        return self.source(source_id).chapters

    # -- gates ---------------------------------------------------------
    def require_source_sha(self, source_id: str, sha: str | None, where: str) -> None:
        """The source gate (was the edition gate): refuse another edition's bytes.

        Batch-1 anchor findings were judged against the 2011 draft; every artifact
        that quotes a source carries source_id AND source_sha, and a mismatch is
        refused here, never warned about.
        """
        ref = self.source(source_id)
        if not ref.sha256:
            raise SourceError(
                f"source {source_id} has no sha256",
                hint=f"run `cf sources refresh {source_id}` with the PDF reachable, "
                     f"or `cf sources set {source_id} sha256=<hex>`")
        if sha != ref.sha256:
            raise CfError(
                f"{where}: source_sha {sha!r} != workspace sources.{source_id}.sha256 "
                f"{ref.sha256!r}",
                hint="this artifact was produced against a different edition; re-run its stage")

    def check_host(self, host: str) -> str:
        """Refuse any host not listed in workspace.yaml hosts.allowlist.

        `cf run` rsyncs private Lean sources and copyrighted source text; the
        allowlist is the enforcement point.
        """
        if host not in self.host_allowlist:
            raise CfError(
                f"host {host!r} is not in hosts.allowlist",
                hint="add it to workspace.yaml hosts.allowlist; the owner of that box must "
                     "consent before private sources or source text are copied to it")
        return host

    def unconfirmed_sources(self) -> list[SourceRef]:
        """Sources discovery wrote that nobody has reviewed (`cf status` prints these)."""
        return [ref for ref in self.sources.values() if not ref.confirmed]

    # -- hosts, models, defaults, policy -------------------------------
    @property
    def hosts(self) -> dict:
        return self.data.get("hosts") or {}

    @property
    def host_allowlist(self) -> list[str]:
        return [str(h) for h in (self.hosts.get("allowlist") or [])]

    @property
    def models(self) -> dict:
        return self.data.get("models") or {}

    def model(self, role: str) -> str | None:
        return self.models.get(role)

    @property
    def defaults(self) -> dict:
        return self.data.get("defaults") or {}

    def default(self, key: str, fallback=None):
        val = self.defaults.get(key)
        return fallback if val is None else val

    @property
    def budget(self) -> dict:
        return self.data.get("budget") or {}

    @property
    def policy(self) -> dict:
        return self.data.get("policy") or {}

    @property
    def library_paths(self) -> list[str]:
        """Directories `cf onboard` may search for a PDF a repo names.

        The configured list plus :meth:`derived_library_paths`.  This is also the
        allowlist for an absolute path a companion repo names in its own files
        (:func:`cflib.discovery.search_order`), which is why the derived half is
        both DERIVED and kept to the two shapes of :data:`DERIVED_ROOT_SHAPES`.
        The home-directory globs it replaces let any audited repo point the
        extractor at anything under them, and only worked at all on a machine that
        keeps its checkouts there.
        """
        raw = self.data.get("library_paths")
        configured = list(DEFAULT_LIBRARY_PATHS) if raw is None else [str(p) for p in raw]
        out = list(configured)
        for path in self.derived_library_paths():
            if path not in out:
                out.append(path)
        return out

    def derived_library_paths(self) -> list[str]:
        """Search roots this workspace has already been told about.

        Its own ``sources/`` store, plus :func:`derived_roots_for` of every
        onboarded companion repo.  A repo path that is not a directory (a stale
        entry, a checkout that moved) is skipped rather than walked.

        Nothing here is a bare directory except the workspace's own store, and
        `cf init` / `cf doctor` print the effective set rather than leaving it in
        a file nobody opens.
        """
        out: list[str] = [str(self.root / "sources")]
        for ref in self.companions.values():
            for root in derived_roots_for(ref.repo):
                if root not in out:
                    out.append(root)
        return out

    # -- validation ----------------------------------------------------
    def validate(self, *, structural_only: bool = False) -> list[str]:
        """Structural problems with workspace.yaml (empty list when valid).

        ``structural_only`` keeps the rules that no half-finished command may
        break (the schema version, the shape of the two tables, one sha per
        source) and drops the cross-references between companions and sources,
        which a command legitimately completes over several saves.
        """
        problems: list[str] = []
        version = self.data.get("version")
        if version != WORKSPACE_VERSION:
            problems.append(f"version must be {WORKSPACE_VERSION} (found {version!r})")
        raw_sources = self.data.get("sources") or {}
        raw_companions = self.data.get("companions") or {}
        if not isinstance(raw_sources, dict):
            problems.append("sources must be a map keyed by source_id")
            raw_sources = {}
        if not isinstance(raw_companions, dict):
            problems.append("companions must be a map keyed by companion name")
            raw_companions = {}
        if not isinstance(self.hosts.get("allowlist", []), list):
            problems.append("hosts.allowlist must be a list")

        sources: dict[str, SourceRef] = {}
        for sid, body in raw_sources.items():
            try:
                sources[sid] = SourceRef.from_dict(sid, body)
            except (TypeError, ValueError) as exc:
                problems.append(f"sources.{sid}: {exc}")
        by_sha: dict[str, str] = {}
        for sid, ref in sources.items():
            if not ref.title:
                problems.append(f"sources.{sid}: title is required")
            if not ref.edition:
                problems.append(f"sources.{sid}: edition is required")
            if not isinstance((raw_sources.get(sid) or {}).get("offset", 0), int):
                problems.append(f"sources.{sid}: offset must be an int")
            if not 0.0 <= ref.offset_confidence <= 1.0:
                problems.append(f"sources.{sid}: offset_confidence must be in [0, 1]")
            if ref.sha256 is not None and not _is_sha256(ref.sha256):
                problems.append(f"sources.{sid}: sha256 must be 64 lowercase hex characters")
            if ref.role not in (ROLE_AUDITED, ROLE_REFERENCE):
                problems.append(f"sources.{sid}: role must be audited or reference")
            if ref.sha256:
                if ref.sha256 in by_sha:
                    problems.append(
                        f"sources.{sid} and sources.{by_sha[ref.sha256]} share sha256 "
                        f"{ref.sha256[:8]}: the same bytes are one source")
                by_sha[ref.sha256] = sid
        if structural_only:
            return problems

        companions: dict[str, CompanionRef] = {}
        for name, body in raw_companions.items():
            try:
                companions[name] = CompanionRef.from_dict(name, body)
            except (TypeError, ValueError) as exc:
                problems.append(f"companions.{name}: {exc}")
        from .vocab import IMPLEMENTED_ADAPTER_KINDS, WRITE_POLICY_SET
        for name, comp in companions.items():
            if not str(comp.repo):
                problems.append(f"companions.{name}: repo is required")
            elif not comp.repo.is_dir():
                problems.append(f"companions.{name}: repo {comp.repo} is not a directory")
            if comp.adapter not in IMPLEMENTED_ADAPTER_KINDS:
                problems.append(
                    f"companions.{name}: adapter {comp.adapter!r} is not implemented "
                    f"({', '.join(sorted(IMPLEMENTED_ADAPTER_KINDS))})")
            if not comp.sources:
                problems.append(f"companions.{name}: sources must list at least one source_id")
            for sid in comp.sources + comp.bundled_sources:
                if sid not in sources:
                    problems.append(f"companions.{name}: unknown source_id {sid!r}")
            if comp.sources and comp.anchor_source not in comp.sources:
                problems.append(
                    f"companions.{name}: anchor_source {comp.anchor_source!r} is not one of "
                    f"its sources ({', '.join(comp.sources)})")
            if comp.write_policy not in WRITE_POLICY_SET:
                problems.append(f"companions.{name}: write_policy {comp.write_policy!r} is invalid")

        # every `same_work` / `page_map_to` target must be a source we know, or a
        # registry entry (a sibling edition nobody has prepared here yet).
        known_ids = set(sources) | _registry_ids()
        for sid, ref in sources.items():
            for other in ref.same_work:
                if other not in known_ids:
                    problems.append(f"sources.{sid}: same_work names unknown source {other!r}")
            for other in ref.page_map_to:
                if other not in known_ids:
                    problems.append(f"sources.{sid}: page_map_to names unknown source {other!r}")
            audited_by = [n for n, c in companions.items() if sid in c.sources]
            if ref.role == ROLE_AUDITED and companions and not audited_by:
                problems.append(
                    f"sources.{sid}: role is audited but no companion lists it "
                    "(give it role: reference, or add it to a companion's sources)")
        return problems


#: Searched by `cf onboard` for a PDF a repo names but does not commit, when
#: workspace.yaml names no ``library_paths`` at all.  One home-relative entry and
#: nothing else: the rest of the search roots are derived from the workspace
#: (:meth:`Workspace.derived_library_paths`), because this list doubles as the
#: allowlist for absolute paths a companion repo names in its own files.
DEFAULT_LIBRARY_PATHS: tuple[str, ...] = (
    "~/Downloads",
)

#: The two places a companion's own PDF actually sits, relative to the directory
#: that holds the checkouts: a sibling repo's ``sources/`` directory, or its Lake
#: cache of the file the author publishes.  A derived root is ALWAYS one of these
#: shapes and never the bare parent, because ``library_paths`` doubles as the
#: allowlist for an absolute path a companion repo names in its own files
#: (:func:`cflib.discovery.search_order`): the bare parent of a checkout kept on
#: the desktop is the whole desktop, so onboarding one repo would swap a handful
#: of reachable PDFs for every PDF within ``LIBRARY_MAX_DEPTH`` of it.
DERIVED_ROOT_SHAPES: tuple[str, ...] = (
    "**/sources",
    "**/.lake/official-pdf",
)


def derived_roots_for(repo: str | Path) -> list[str]:
    """The ``library_paths`` entries onboarding *repo* adds.

    Onboarding ``<somewhere>/<companion>`` is how the user says ``<somewhere>``
    holds their companions, so a sibling repo that carries the PDF is reachable
    on any layout instead of only under one person's home-directory layout -- but
    only in the two shapes of :data:`DERIVED_ROOT_SHAPES`, which is strictly
    narrower than the home-directory globs this replaces.  A path that is not a
    directory (a stale entry, a checkout that moved) contributes nothing.
    """
    raw = str(repo or "")
    if not raw:
        return []
    path = Path(raw).expanduser()
    if not path.is_dir():
        return []
    parent = path.resolve().parent
    return [str(parent / shape) for shape in DERIVED_ROOT_SHAPES]


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _registry_ids() -> set[str]:
    """Ids the shipped/overlay registry knows (validation only; never raises)."""
    try:
        from .registry import Registry
        return set(Registry.load().entries)
    except Exception:  # pragma: no cover - a broken registry must not break loading
        return set()


def from_args(args, *, validate: bool = True) -> Workspace:
    """Load the workspace a cf command was pointed at.

    Every ``cfcmd`` module uses this: cf.py parses the global ``--workspace``
    option (defaulting to ``$CF_WORKSPACE`` or the current directory) and stores
    it on ``args.workspace``.  ``validate=False`` is the repair path -- see
    :meth:`Workspace.load`.
    """
    return Workspace.load(getattr(args, "workspace", None), validate=validate)
