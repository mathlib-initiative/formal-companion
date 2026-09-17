"""The adapter base class and loader used by ``cf onboard`` / ``cf extract``.

The abstract contract lives in ``cflib.adapters_api`` (``dry_run(repo)``,
``extract(repo, sources={id: sha})``, ``key()``); this module adds what every
concrete adapter needs and keeps the config vocabulary of
``templates/adapter.yaml.example`` in one place:

    adapter:        docstring_tags | attribution_table | ledger_nodes | custom
    repo:           the companion repo (never written to)
    glob / exclude: which files the adapter reads
    tag:            {position, pattern, page_pattern}
    body_for_kinds: def-like kinds whose body is captured
    sorry:          {exercise_sorry, gap_marker}
    key_files:      extra files whose sha belongs in the extract key

Item kinds and the printed/PDF page offset come from the WORKSPACE's record of
the source being extracted (``sources.<id>.kinds`` / ``.offset``) when the loader
is given one, so no adapter hard-codes the Tao kind list or the ``+16`` offset.
Which source that is comes from :class:`SourceBinding`: ``adapter.yaml``'s
``sources.default`` and ``sources.map``, narrowed to the ids `cf extract` passes
in ``extract(repo, sources={id: sha})``.  An item is the pair
``(source_id, item)``, so every row carries both halves and the sha of the bytes
it was read against.

``key()`` (inherited) hashes ``key_inputs()``; every adapter here extends it
with the sha of the content it actually reads, so ``cf extract`` is a no-op
only when nothing it depends on changed -- including on a dirty worktree, and
including the ledger/target-refinement exports of a table-driven companion
(design/critique.json: "the extract key must include ledger sha and
target-refinement sha for ledger repos").
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from cflib.adapters_api import Adapter as CoreAdapter
from cflib.errors import AdapterError
from cflib.hashing import sha256_file, sha256_text

from . import yamlio

__all__ = ["Adapter", "AdapterError", "SourceBinding", "load", "from_config", "register",
           "registry", "sha_of_files"]

_REGISTRY: dict[str, type["Adapter"]] = {}


def register(cls: type["Adapter"]) -> type["Adapter"]:
    """Class decorator: make an adapter reachable by its ``kind``."""
    if not getattr(cls, "kind", ""):
        raise AdapterError(f"{cls.__name__} has no kind")
    _REGISTRY[cls.kind] = cls
    return cls


def registry() -> dict[str, type["Adapter"]]:
    """All known adapter kinds (imports the shipped adapters on first call)."""
    if not _REGISTRY:
        from . import attribution_table, docstring_tags  # noqa: F401
    return dict(_REGISTRY)


class Adapter(CoreAdapter):
    """A companion repository seen as a table of book statements."""

    #: adapter kind as written in adapter.yaml (``adapter:``)
    kind: str = ""

    def __init__(self, config: dict, name: str | None = None, workspace: Any | None = None,
                 source: str | None = None):
        super().__init__(config, name=name, workspace=workspace)
        self.source = source or f"adapter({self.kind})"
        self._claims_cache: dict[str, list] = {}
        if not self.name:
            raise AdapterError(f"{self.source}: 'name' is required (the companion's short name)")
        self.validate_config()

    # -- configuration -----------------------------------------------------
    def validate_config(self) -> None:
        """Raise ``AdapterError`` when the config cannot drive an extraction."""

    def get(self, path: str, default: Any = None) -> Any:
        """Dotted lookup into the config: ``self.get("tag.page_pattern")``."""
        node: Any = self.config
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    def require(self, field: str) -> Any:
        value = self.get(field)
        if value in (None, "", [], {}):
            raise AdapterError(f"{self.source}: '{field}' is required in adapter.yaml")
        return value

    # -- repo and files ----------------------------------------------------
    def resolve_repo(self, repo: str | Path | None = None) -> Path:
        """The repo to read: the argument, else ``repo:`` from adapter.yaml."""
        raw = repo if repo not in (None, "") else self.config.get("repo")
        if not raw:
            raise AdapterError(f"{self.source}: 'repo' is required in adapter.yaml")
        path = Path(str(raw)).expanduser()
        if not path.is_dir():
            raise AdapterError(f"{self.name}: repo not found: {path}")
        return path

    def globs(self, default: Sequence[str] = ()) -> list[str]:
        """``glob:`` as a list (a single string or a list are both accepted)."""
        raw = self.config.get("glob", self.config.get("globs"))
        if raw in (None, "", []):
            return list(default)
        return [str(raw)] if isinstance(raw, str) else [str(g) for g in raw]

    def excludes(self) -> list[str]:
        raw = self.config.get("exclude") or []
        return [str(raw)] if isinstance(raw, str) else [str(g) for g in raw]

    def matching_files(self, repo: Path, default_globs: Sequence[str] = ()) -> list[Path]:
        """Files under ``repo`` matching ``glob:``, minus ``exclude:`` and minus
        anything the repository itself ignores.

        The ignore filter is not a convenience.  A companion is audited AT A
        COMMIT -- `cf onboard` records one, and every finding is a claim about
        the committed repository -- so a file git ignores is not part of the
        thing under audit.  Reading it invents rows: tao-measure-lean keeps
        spliced working copies of its own modules in the git-ignored
        ``scratch/``, and the default ``**/*.lean`` glob parsed 142 declarations
        a second and third time from them, which is both a false audit surface
        and a duplicate-key failure in `cf extract`.
        """
        selected: dict[str, Path] = {}
        for pattern in self.globs(default_globs):
            for p in sorted(repo.glob(pattern)):
                if p.is_file():
                    selected[str(p)] = p
        for pattern in self.excludes():
            for p in repo.glob(pattern):
                selected.pop(str(p), None)
        for path in self.ignored_files(repo, list(selected.values())):
            selected.pop(str(path), None)
        return list(selected.values())

    @staticmethod
    def ignored_files(repo: Path, paths: Sequence[Path]) -> list[Path]:
        """The subset of *paths* that this repository's git ignores.

        Empty when *repo* is not a git checkout, when git is unavailable, or
        when the call fails for any reason: an adapter that cannot ask must
        read everything rather than silently audit nothing.
        """
        if not paths or not (repo / ".git").exists():
            return []
        rel = [str(Path(p).relative_to(repo)) for p in paths]
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo), "check-ignore", "--stdin"],
                input="\n".join(rel) + "\n",
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):       # pragma: no cover
            return []
        # 0 = some paths are ignored, 1 = none are; anything else is an error
        if proc.returncode not in (0, 1):                   # pragma: no cover
            return []
        return [repo / line for line in proc.stdout.splitlines() if line.strip()]

    # -- discovery (design S4.3) -------------------------------------------
    def discover_sources(self, repo: str | Path | None = None) -> list:
        """Source claims for this repo, this kind's own signals first.

        Subclasses add the item kinds their own tags reveal
        (:meth:`observed_kinds`) to the claims coming from
        :attr:`PRIMARY_SIGNALS`, which is the one fact about a source that can
        only be read off the COMPANION -- and the only kinds there are when the
        PDF is not on this machine for `cflib.inference.infer_kinds` to read.
        """
        path = self.resolve_repo(repo)
        cached = self._claims_cache.get(str(path))
        if cached is not None:
            return list(cached)
        claims = super().discover_sources(path)
        kinds = self.observed_kinds(path)
        if kinds:
            primary = [c for c in claims if c.signal in set(self.PRIMARY_SIGNALS)]
            # No claim from this convention's own signals: there is no better
            # place to hang the grammar than every candidate, and merging unions
            # them into the repo's source(s) anyway.
            for claim in (primary or claims):
                claim.kinds = tuple(dict.fromkeys(tuple(claim.kinds) + kinds))
        self._claims_cache[str(path)] = list(claims)
        return list(claims)

    def observed_kinds(self, repo: str | Path | None = None) -> tuple[str, ...]:
        """Item kinds THIS repo's tags use (empty when the kind cannot tell)."""
        return ()

    def source_report(self, repo: str | Path | None = None) -> list[dict]:
        """:meth:`discover_sources` flattened for ``dry_run`` and `cf onboard`.

        Never raises: a repo whose metadata cannot be read must still report its
        parse counts, and the reason is carried in the report rather than thrown.
        """
        try:
            claims = self.discover_sources(repo)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            return [{"signal": "error", "path": "",
                     "note": f"{exc.__class__.__name__}: {exc}"}]
        out: list[dict] = []
        for claim in claims:
            body = {
                "signal": claim.signal,
                "path": claim.path,
                "confidence": round(float(claim.confidence), 3),
            }
            for field_name in ("title", "edition", "isbn", "sha256", "sha256_prefix",
                               "book_page_url"):
                value = getattr(claim, field_name, "")
                if value:
                    body[field_name] = value
            for field_name in ("authors", "pdf_hints", "urls", "kinds"):
                value = getattr(claim, field_name, ())
                if value:
                    body[field_name] = list(value)
            if claim.offset_hint is not None:
                body["offset_hint"] = claim.offset_hint
            if claim.anchor_hint:
                body["anchor_hint"] = claim.anchor_hint[:200]
            out.append(body)
        return out

    # -- which source, and what it says ------------------------------------
    def bind(self, sources: "Mapping[str, str] | None" = None) -> "SourceBinding":
        """The row -> (source_id, source_sha) attributor for one extraction."""
        return SourceBinding(self, sources)

    def source_ref(self, source_id: str | None):
        """What the workspace records about one source, or None.

        Accepts a real :class:`cflib.workspace.Workspace` (``source(id)`` ->
        SourceRef) and any object that simply exposes ``kinds`` / ``offset`` /
        ``sha256``, which is what the regression tests and `cf extract --repo`
        hand in.
        """
        ws = self.workspace
        if ws is None:
            return None
        getter = getattr(ws, "source", None)
        if callable(getter):
            if not source_id:
                return None
            try:
                return getter(source_id)
            except Exception:                 # noqa: BLE001 - an unknown id is not fatal here
                return None
        return ws

    def item_kinds(self, source_id: str | None = None) -> tuple[str, ...]:
        """Item kinds of ONE source: the grammar is per source, never global."""
        ref = self.source_ref(source_id if source_id is not None else self._primary_source())
        source_kinds = getattr(ref, "kinds", None) if ref is not None else None
        kinds = list(source_kinds) if source_kinds else list(self.config.get("item_kinds") or [])
        if not kinds:
            from .itemtag import DEFAULT_KEYWORDS
            return DEFAULT_KEYWORDS
        if "In-text" not in kinds:
            kinds.append("In-text")
        return tuple(str(k) for k in kinds)

    def numbered_kinds(self, source_id: str | None = None) -> tuple[str, ...]:
        """Item kinds that carry a ``c.s.n`` number (everything but In-text)."""
        return tuple(k for k in self.item_kinds(source_id) if k != "In-text")

    def page_offset(self, source_id: str | None = None) -> int:
        """printed page = PDF page - offset (the bound source, else adapter.yaml)."""
        ref = self.source_ref(source_id if source_id is not None else self._primary_source())
        offset = getattr(ref, "offset", None) if ref is not None else None
        if offset is None:
            offset = self.config.get("page_offset")
        if offset is None:
            raise AdapterError(
                f"{self.source}: no page offset for {source_id or 'this companion'}",
                hint="set the source's offset in workspace.yaml (16 for the published GSM 126) "
                     "or page_offset in adapter.yaml")
        return int(offset)

    def source_sha(self, source_id: str | None = None) -> str | None:
        """sha256 of the bytes a row was read against, from the workspace."""
        ref = self.source_ref(source_id if source_id is not None else self._primary_source())
        if ref is None:
            return None
        for attr in ("sha256", "source_sha"):
            value = getattr(ref, attr, None)
            if value:
                return str(value)
        return None

    def _primary_source(self) -> str:
        """``sources.default`` -- the id used when a caller names none."""
        return str((self.config.get("sources") or {}).get("default") or "")

    def bound_source_shas(self) -> dict:
        """``{source_id: sha}`` for every source adapter.yaml names (the key)."""
        cfg = self.config.get("sources") or {}
        ids = [str(cfg.get("default"))] if cfg.get("default") else []
        for rule in cfg.get("map") or []:
            if isinstance(rule, dict) and rule.get("source"):
                ids.append(str(rule["source"]))
        return {sid: self.source_sha(sid) for sid in dict.fromkeys(ids)}

    # -- the interface -----------------------------------------------------
    def dry_run(self, repo: str | Path | None = None) -> dict:
        """Parse without writing: counts, rejects and a few sample rows."""
        raise NotImplementedError

    def extract(self, repo: str | Path | None = None, *,
                sources: "Mapping[str, str] | None" = None) -> Iterator[dict]:
        """Yield schema-v2 statement rows, each stamped with its source."""
        raise NotImplementedError

    def key_inputs(self) -> dict:
        """Everything that changes the extraction result."""
        repo = self.resolve_repo()
        inputs = {
            "adapter_kind": self.kind,
            "config": self.config,
            "repo": str(repo),
            "repo_commit": self.repo_commit(repo),
            # the bound editions: extracting the same tree against another
            # source is another table, and must not replay this one.
            "source_shas": self.bound_source_shas(),
        }
        extra = {str(f): self.file_sha(repo / str(f)) for f in (self.config.get("key_files") or [])}
        if extra:
            inputs["key_files"] = extra
        return inputs

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r} kind={self.kind!r}>"


class SourceBinding:
    """Which source each row of one extraction belongs to, and its sha.

    An item is the pair ``(source_id, item)`` (design S6.1): the same label
    means different things in different sources, so every row is attributed
    before it is written, never afterwards.  The rule, in order:

    1. a ``sources.map`` rule in adapter.yaml matching the row's ``tag_prefix``
       or its ``files:`` glob -- the row's tag names another source;
    2. otherwise the companion's default source, which is
       ``sources.default`` when `cf extract` bound it, else the single bound
       source.  `cf onboard` re-decides the anchor edition mechanically AFTER
       extracting, so the ids it binds are fresher than adapter.yaml's and win.

    The sha comes from the ``{source_id: source_sha}`` mapping the caller passed
    in, falling back to the workspace's record.  A row with no sha is still
    written when the caller allows it (``cf extract --allow-missing-source-sha``,
    the stand-alone regression runs), and the schema check is what refuses it
    otherwise.
    """

    def __init__(self, adapter: "Adapter", sources: "Mapping[str, str] | None" = None):
        self.adapter = adapter
        self.shas: dict[str, str | None] = {
            str(k): (str(v) if v else None) for k, v in dict(sources or {}).items()}
        cfg = adapter.config.get("sources") or {}
        if not isinstance(cfg, dict):
            raise AdapterError(f"{adapter.source}: adapter.yaml `sources:` must be a mapping")
        self.declared_default = str(cfg.get("default") or "")
        self.has_map = bool(cfg.get("map"))
        self.used: dict[str, str | None] = {}
        self._default: str | None = None
        self._sha_cache: dict[str, str | None] = {}

    @property
    def default(self) -> str:
        # computed lazily: a companion whose every row is mapped need not have a
        # default at all, and asking for one in __init__ would refuse it.
        if self._default is None:
            self._default = self.adapter.default_source(self.shas)
        return self._default

    def resolve(self, *, file: Any = "", tag: str = "", decl: str = "") -> tuple[str, str | None]:
        """``(source_id, source_sha)`` for one row."""
        source_id = ""
        if self.has_map:
            try:
                matched = self.adapter.source_for(file=Path(str(file)), tag=tag, decl=decl)
            except AdapterError:
                matched = ""
            # source_for falls back to sources.default when no rule matches; that
            # fallback is adapter.yaml's, and the binding's is fresher.
            if matched and matched != self.declared_default:
                source_id = matched
        source_id = source_id or self.default
        sha = self.sha(source_id)
        self.used[source_id] = sha
        return source_id, sha

    def sha(self, source_id: str) -> str | None:
        if source_id in self.shas:
            return self.shas[source_id]
        if source_id not in self._sha_cache:
            self._sha_cache[source_id] = self.adapter.source_sha(source_id)
        return self._sha_cache[source_id]

    def ids(self) -> list[str]:
        """Every source this binding can attribute a row to."""
        ids = set(self.shas) | set(self.used)
        if self.declared_default:
            ids.add(self.declared_default)
        for rule in (self.adapter.config.get("sources") or {}).get("map") or []:
            if isinstance(rule, dict) and rule.get("source"):
                ids.add(str(rule["source"]))
        return sorted(i for i in ids if i)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SourceBinding {self.ids()}>"


# -- loading ---------------------------------------------------------------

def from_config(config: dict, name: str | None = None, workspace: Any | None = None,
                source: str | None = None) -> Adapter:
    """Build an adapter from an already-parsed ``adapter.yaml`` mapping."""
    if not isinstance(config, dict):
        raise AdapterError(f"{source or 'adapter'}: adapter.yaml must be a mapping")
    kinds = registry()
    kind = str(config.get("adapter") or config.get("kind") or "").strip()
    if not kind:
        raise AdapterError(f"{source or 'adapter'}: 'adapter' is required",
                           hint=f"one of {', '.join(sorted(kinds))}")
    if kind not in kinds:
        raise AdapterError(
            f"{source or 'adapter'}: unknown adapter kind {kind!r}",
            hint=f"implemented kinds: {', '.join(sorted(kinds))}")
    return kinds[kind](config, name=name, workspace=workspace, source=source)


def load(adapter_yaml: str | Path, workspace: Any | None = None) -> Adapter:
    """Build an adapter from a companion's ``adapter.yaml``.

    ``cflib.adapters_api.load`` delegates here, so this is what every command
    ultimately calls.
    """
    path = Path(adapter_yaml).expanduser()
    if not path.is_file():
        raise AdapterError(f"adapter config not found: {path}",
                           hint="run `cf onboard <name> --repo <path> --adapter <kind>` first")
    config = yamlio.load_yaml(path)
    if not isinstance(config, dict):
        raise AdapterError(f"{path}: adapter.yaml must be a mapping")
    config.setdefault("name", path.parent.name)
    repo = config.get("repo")
    if repo:
        repo_path = Path(str(repo)).expanduser()
        config["repo"] = str(repo_path if repo_path.is_absolute()
                             else (path.parent / repo_path).resolve())
    return from_config(config, name=str(config.get("name")), workspace=workspace, source=str(path))


def sha_of_files(paths: Sequence[Path], root: Path) -> str:
    """One digest over (relative path, content sha) for a set of files.

    Content-keying the sources makes ``cf extract`` correct on a dirty
    worktree, where the commit alone would claim the table is up to date.
    """
    parts = []
    for p in sorted(paths):
        try:
            rel = str(p.relative_to(root))
        except ValueError:  # pragma: no cover - defensive
            rel = str(p)
        parts.append(f"{rel}:{sha256_file(p)}")
    return sha256_text("\n".join(parts))
