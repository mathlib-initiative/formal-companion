"""The adapter base class and loader used by ``cf onboard`` / ``cf extract``.

The abstract contract lives in ``cflib.adapters_api`` (``dry_run(repo)``,
``extract(repo, book_sha)``, ``key()``); this module adds what every concrete
adapter needs and keeps the config vocabulary of ``templates/adapter.yaml.example``
in one place:

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

``key()`` (inherited) hashes ``key_inputs()``; every adapter here extends it
with the sha of the content it actually reads, so ``cf extract`` is a no-op
only when nothing it depends on changed -- including on a dirty worktree, and
including the ledger/target-refinement exports of a table-driven companion
(design/critique.json: "the extract key must include ledger sha and
target-refinement sha for ledger repos").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Sequence

from cflib.adapters_api import Adapter as CoreAdapter
from cflib.errors import AdapterError
from cflib.hashing import sha256_file, sha256_text

from . import yamlio

__all__ = ["Adapter", "AdapterError", "load", "from_config", "register", "registry",
           "sha_of_files"]

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
        """Files under ``repo`` matching ``glob:`` and not matching ``exclude:``."""
        selected: dict[str, Path] = {}
        for pattern in self.globs(default_globs):
            for p in sorted(repo.glob(pattern)):
                if p.is_file():
                    selected[str(p)] = p
        for pattern in self.excludes():
            for p in repo.glob(pattern):
                selected.pop(str(p), None)
        return list(selected.values())

    # -- book parameters ---------------------------------------------------
    def item_kinds(self) -> tuple[str, ...]:
        """Item kinds: the bound source's list, else the adapter's own."""
        source_kinds = getattr(self.workspace, "kinds", None) if self.workspace else None
        kinds = list(source_kinds) if source_kinds else list(self.config.get("item_kinds") or [])
        if not kinds:
            from .itemtag import DEFAULT_KEYWORDS
            return DEFAULT_KEYWORDS
        if "In-text" not in kinds:
            kinds.append("In-text")
        return tuple(str(k) for k in kinds)

    def numbered_kinds(self) -> tuple[str, ...]:
        """Item kinds that carry a ``c.s.n`` number (everything but In-text)."""
        return tuple(k for k in self.item_kinds() if k != "In-text")

    def page_offset(self) -> int:
        """printed page = PDF page - offset (the bound source, else adapter.yaml)."""
        if self.workspace is not None and getattr(self.workspace, "offset", None) is not None:
            return int(self.workspace.offset)
        offset = self.config.get("page_offset")
        if offset is None:
            raise AdapterError(
                f"{self.source}: no page offset",
                hint="set the source's offset in workspace.yaml (16 for the published GSM 126) "
                     "or page_offset in adapter.yaml")
        return int(offset)

    def book_sha(self, book_sha: str | None = None) -> str | None:
        if book_sha:
            return book_sha
        return getattr(self.workspace, "book_sha", None) if self.workspace else None

    # -- the interface -----------------------------------------------------
    def dry_run(self, repo: str | Path | None = None) -> dict:
        """Parse without writing: counts, rejects and a few sample rows."""
        raise NotImplementedError

    def extract(self, repo: str | Path | None = None, book_sha: str | None = None) -> Iterator[dict]:
        """Yield schema-v2 statement rows for this companion."""
        raise NotImplementedError

    def key_inputs(self) -> dict:
        """Everything that changes the extraction result."""
        repo = self.resolve_repo()
        inputs = {
            "adapter_kind": self.kind,
            "config": self.config,
            "repo": str(repo),
            "repo_commit": self.repo_commit(repo),
        }
        extra = {str(f): self.file_sha(repo / str(f)) for f in (self.config.get("key_files") or [])}
        if extra:
            inputs["key_files"] = extra
        return inputs

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r} kind={self.kind!r}>"


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
