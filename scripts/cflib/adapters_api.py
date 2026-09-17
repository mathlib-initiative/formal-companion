"""The adapter interface: how a companion repo becomes a statement table.

New code. It formalises the Stage 0/2 contract of design/stage_map.md so the
three shipped adapters (``docstring_tags`` for tao-measure-lean and
teorth/analysis, ``attribution_table`` for measure_theory's overlay data,
``ledger_nodes`` for its source-formalization ledger) and any future ``custom``
script are interchangeable to `cf onboard` / `cf extract`.

Implementations live in ``scripts/adapters/`` and subclass :class:`Adapter`;
``scripts/adapters/__init__.py`` exposes ``load(adapter_yaml)`` returning an
instance.  Nothing in cflib imports the adapters package at module scope, so
the core CLI works before the adapters land.
"""
from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Iterator

from .errors import AdapterError
from .hashing import sha256_json

#: Row schema v2 (docs/design/multi-source-model.md S6.2). Required on every row.
#: An item is the pair ``(source_id, item)``: the same label means different
#: things in different sources, so a row that does not say which source it is
#: about cannot be paired, gated or tallied.
ROW_REQUIRED_FIELDS: tuple[str, ...] = (
    "item",        # canonical id from cflib.items.normalise(), per-source grammar
    "item_raw",    # the tag exactly as it appears in the repo
    "source_id",   # which source this row is attributed to
    "source_sha",  # sha256 of that source's PDF: the edition it was built against
    "decl",        # fully qualified declaration name (namespace tracked)
    "file",        # repo-relative path
    "line",        # 1-based
    "statement",   # the signature, comments stripped
    "repo",        # companion name
)

#: Optional but standardised: adapters fill what their repo can supply.
ROW_OPTIONAL_FIELDS: tuple[str, ...] = (
    "sub",             # "(iv)" when the tag names a sub-part
    "kind",            # Lean kind: theorem/def/instance/...
    "body",            # defs/abbrevs/instances only (proxy detection)
    "sorry",           # bool
    "sorry_class",     # exercise | gap | none
    "page_anchor",     # int printed page, or null
    "anchor_source",   # own | crossref | none
    "key",             # bool: a key declaration for the item
    "anonymous",       # bool: synthetic id example@<file>:<line>
    "namespace",
    "granularity",     # item | node (ledger repos before curation)
)

#: Keys `dry_run` must return; extra keys are printed as-is by `cf onboard`.
DRY_RUN_REQUIRED_KEYS: tuple[str, ...] = ("declarations", "tagged", "items")


class Adapter(ABC):
    """Turn a companion repo into statement rows.

    Subclasses set ``kind`` and implement :meth:`dry_run`, :meth:`extract` and
    (usually) :meth:`key_inputs`.
    """

    kind: ClassVar[str] = ""

    def __init__(self, config: dict[str, Any], name: str | None = None,
                 workspace: Any | None = None):
        self.config = dict(config or {})
        self.name = name or self.config.get("name") or self.config.get("repo_name") or ""
        self.workspace = workspace

    # -- discovery (docs/design/multi-source-model.md S4.2, S4.3) ------
    @classmethod
    def detect(cls, repo: str | Path) -> tuple[float, dict]:
        """How sure are we that ``repo`` follows THIS convention, and why?

        ``cf onboard`` takes no --adapter in the common case: the kinds score
        themselves and the best one wins (a tie, or a best below 0.5, is a
        refusal asking for the flag).  Until each adapter implements this, the
        shipped heuristics live in :func:`cflib.discovery.detect_scores`.
        """
        from .discovery import detect_scores
        score, evidence = detect_scores(Path(repo)).get(cls.kind, (0.0, {}))
        return score, evidence

    def discover_sources(self, repo: str | Path) -> list:
        """Source claims from the signals THIS kind knows about (S4.3).

        The base implementation runs the generic signals (prose, filesystem,
        citation); a subclass adds the structured ones it understands and calls
        ``super().discover_sources(repo)``.
        """
        from .discovery import discover_sources
        return discover_sources(Path(repo), adapter=self.kind)

    def source_for(self, *, file: Path, tag: str, decl: str) -> str:
        """Which source a row belongs to.

        ``adapter.yaml sources.default`` when the companion has one source;
        otherwise the first ``sources.map`` rule matching by ``files:`` glob or
        ``tag_prefix:``.  An unattributable row is an error naming the file and
        the tag -- never a silent default, because a row attributed to the wrong
        edition is a finding judged against the wrong pages.
        """
        cfg = self.config.get("sources") or {}
        for rule in cfg.get("map") or []:
            globs = rule.get("files") or []
            prefix = rule.get("tag_prefix")
            if prefix and str(tag).startswith(prefix):
                return str(rule["source"])
            for pattern in globs:
                if Path(file).match(pattern):
                    return str(rule["source"])
        default = cfg.get("default")
        if default:
            return str(default)
        raise AdapterError(
            f"{self.name}: cannot tell which source {decl} ({file}) belongs to",
            hint="set sources.default in adapter.yaml, or add a sources.map rule")

    # -- required ------------------------------------------------------
    @abstractmethod
    def dry_run(self, repo: str | Path) -> dict:
        """Parse without writing anything; return counts.

        Must include ``declarations``, ``tagged`` and ``items``; should include
        ``untagged``, ``private``, ``sorry`` and a ``rejects`` list of
        ``{"raw": ..., "reason": ...}`` from the item grammar.  `cf onboard`
        prints these and stores them in state.json; onboarding fails when
        ``tagged`` is 0.
        """

    @abstractmethod
    def extract(self, repo: str | Path | None = None, *,
                sources: Any | None = None) -> Iterator[dict]:
        """Yield statement rows (row schema v2), each stamped with its source.

        ``sources`` maps ``source_id -> source_sha`` for every source this
        companion is bound to.  (The shipped adapters still take the older
        ``extract(repo, book_sha)``; `cf onboard` calls whichever they expose and
        the ADAPTERS package unifies them.)
        """

    # -- idempotence ---------------------------------------------------
    def key_inputs(self) -> dict:
        """Everything that changes the extraction result.

        Subclasses extend this: ledger-backed adapters MUST add their export
        shas (design/critique.json: "the extract key must include ledger sha
        and target-refinement sha for ledger repos"), otherwise `cf extract`
        replays a stale table after the repo moves on.  The bound source shas
        belong in the key too: the same repo extracted against another edition
        is a different table.
        """
        return {
            "adapter_kind": self.kind,
            "config": self.config,
            "repo_commit": self.repo_commit(self.config.get("repo")),
            "source_shas": getattr(self.workspace, "source_sha", None)
                           or (self.config.get("sources") or {}).get("default"),
        }

    def key(self) -> str:
        """Idempotence key: unchanged key means `cf extract` is a no-op."""
        return "sha256:" + sha256_json(self.key_inputs())

    # -- helpers -------------------------------------------------------
    @staticmethod
    def repo_commit(repo: str | Path | None) -> str | None:
        """HEAD of a git repo, or None when it is not one."""
        if not repo:
            return None
        try:
            out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() or None if out.returncode == 0 else None

    @staticmethod
    def file_sha(path: str | Path) -> str | None:
        from .hashing import sha256_file
        p = Path(path)
        return sha256_file(p) if p.exists() else None

    def validate_row(self, row: dict) -> dict:
        """Check a row against the required fields; raises AdapterError."""
        missing = [f for f in ROW_REQUIRED_FIELDS if row.get(f) in (None, "")]
        if missing:
            raise AdapterError(f"{self.kind}: row missing {', '.join(missing)}: {row!r}")
        return row


def load(adapter_yaml: str | Path, workspace: Any | None = None) -> Adapter:
    """Instantiate the adapter described by ``adapter_yaml``.

    Delegates to ``scripts/adapters/__init__.py``'s ``load``; raises a
    readable error while that package is still being written.
    """
    try:
        import adapters as adapters_pkg  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on install state
        raise AdapterError(
            "the adapters package is not available",
            hint="scripts/adapters/ must exist and expose load(adapter_yaml); "
                 "use --skip-dry-run to onboard without running the parser") from exc
    loader = getattr(adapters_pkg, "load", None)
    if loader is None:  # pragma: no cover
        raise AdapterError("scripts/adapters/__init__.py does not expose load(adapter_yaml)")
    if workspace is None:
        return loader(adapter_yaml)
    try:
        return loader(adapter_yaml, workspace=workspace)
    except TypeError:
        # the ADAPTERS package has not been migrated yet: its loader still spells
        # the keyword `campaign`. One call, one fallback, and it goes away with it.
        return loader(adapter_yaml, campaign=workspace)
