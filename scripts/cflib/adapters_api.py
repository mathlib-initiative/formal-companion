"""The adapter interface: how a companion repo becomes a statement table.

New code. It formalises the Stage 0/2 contract of docs/design/stage_map.md so the
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
from typing import Any, ClassVar, Iterator, Mapping

from . import gitsafe
from .errors import AdapterError
from .hashing import sha256_json

#: Row schema v2 (docs/design/archive/multi-source-model.md S6.2). Required on every row.
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
    "provenance",      # which artifact OF the companion the row was read from
                       # (cflib.vocab.ROW_PROVENANCE): a corpus built from an
                       # export is not the repository, and a COVERAGE finding
                       # has to know the difference.
)

#: Keys `dry_run` must return; extra keys are printed as-is by `cf onboard`.
DRY_RUN_REQUIRED_KEYS: tuple[str, ...] = ("declarations", "tagged", "items")

#: The optional ``dry_run`` key carrying an adapter's cross-check of the artifact
#: it read against the sources that artifact claims to describe.
CROSS_CHECK_KEY = "lean_scan"

#: How many omissions a single warning names before it says "and N more".  All
#: of them are in the report either way; this only bounds the terminal.
CROSS_CHECK_WARN_LIMIT = 25


def unattributed_rows(name: str, report: "Mapping[str, Any] | None") -> list[dict]:
    """The omissions a cross-check could not key on an item, as rows for
    ``companions/<name>/unattributed.jsonl``.

    These are the ones a corpus search cannot find by construction: their
    docstring names no book item (typically they sit under a block header that
    names its item only in prose), so no row can be keyed on one.  The only way
    a worker meets them is by being handed the list, so `cf plan-run` stages
    this file beside the statement tables and the prompts point at it.  Each
    row keeps the file, line and the signals that claimed it; ``repo`` is the
    companion so a merged read stays attributable.

    The file means the same thing under every policy: omissions that name NO
    item.  An omission that does name one is unioned into the table under
    ``union`` and only named in the warning under ``report``; it is never
    written here, because the prompts describe every row of this file as a
    declaration its index attributes to no book item.
    """
    scan = dict(report or {}).get(CROSS_CHECK_KEY) or {}
    return [{"repo": name, **hit}
            for hit in (scan.get("omissions") or [])
            if not hit.get("unioned") and hit.get("item") is None]


def cross_check_warning(name: str, report: "Mapping[str, Any] | None"
                        ) -> tuple[str, list[str]] | None:
    """``(header, lines)`` for :func:`cflib.log.warn_block`, or None.

    An adapter whose corpus is built from an EXPORT of a companion rather than
    from the companion can be wrong in a way no schema check sees: the export
    omits a declaration the repository carries, the corpus has no row for it,
    and a worker who greps the corpus concludes the repository does not state
    it.  So the disagreement is never silent.  Under ``union`` and ``report``
    the command that ran the adapter names the omitted declarations (the first
    :data:`CROSS_CHECK_WARN_LIMIT` on the terminal, all of them in the report);
    under ``off`` no scan runs, so there is nothing to name, and the warning
    says instead that the table was not cross-checked at all.  None only when a
    check ran and found nothing, or when the adapter has no cross-check.
    """
    scan = dict(report or {}).get(CROSS_CHECK_KEY) or {}
    policy = str(scan.get("policy") or "")
    if policy == "off":
        return (f"{name}: lean_scan.policy is off -- the attribution table was NOT "
                "cross-checked against the Lean sources",
                ["  declarations the export omits are neither unioned, listed nor named; do not "
                 "read absence",
                 "  from this corpus as absence from the repository: a COVERAGE finding has to "
                 "grep the Lean."])
    omissions = list(scan.get("omissions") or [])
    refused = list(scan.get("refused_labels") or [])
    if not omissions and not refused:
        return None
    unioned = sum(1 for hit in omissions if hit.get("unioned"))
    if policy == "union":
        what = f"{unioned} unioned into the table as provenance `lean_scan`"
    elif policy == "report":
        what = f"{len(omissions) - unioned} NOT added to the table (lean_scan.policy: report)"
    else:
        what = f"lean_scan.policy: {policy}"
    listed = sum(1 for hit in omissions if not hit.get("unioned") and hit.get("item") is None)
    private = sum(1 for hit in omissions if hit.get("private"))
    header = (f"{name}: the attribution table omits {len(omissions)} declaration(s) that "
              f"the Lean sources claim for the book -- {what}" if omissions else
              f"{name}: the attribution table omits nothing the Lean sources claim, but the "
              f"item grammar refused {len(refused)} leading docstring label(s)")
    lines = []
    for hit in omissions[:CROSS_CHECK_WARN_LIMIT]:
        flags = "".join([" [private]" if hit.get("private") else "",
                         "" if hit.get("unioned") else " [not in the table, not in the corpus]"])
        lines.append(f"  {hit.get('decl')}  {hit.get('file')}:{hit.get('line')}"
                     f"  ({'; '.join(hit.get('signals') or [])}){flags}")
    if len(omissions) > CROSS_CHECK_WARN_LIMIT:
        lines.append(f"  … and {len(omissions) - CROSS_CHECK_WARN_LIMIT} more (all of them are in "
                     "the dry-run report)")
    if listed:
        lines.append(f"  {listed} of these name no book item in their docstring, so there is "
                     "no item to file them under; they are listed in unattributed.jsonl,")
        lines.append("  which `cf plan-run` stages beside the statement tables for every worker "
                     "to read.")
    if refused:
        lines.append(f"  {len(refused)} leading docstring label(s) the item grammar refused, so "
                     "no item was keyed from them:")
        for hit in refused[:5]:
            lines.append(f"    {hit.get('decl')}  {hit.get('file')}:{hit.get('line')}  "
                         f"`{hit.get('label')}` ({hit.get('reason')})")
    if private:
        lines.append(f"  {private} of the omitted declaration(s) are private: an export may leave "
                     "those out by convention,")
        lines.append("  so an omission is not by itself a defect to report upstream (SKILL.md hard "
                     "rule 5).")
    lines.append("  do not read absence from this corpus as absence from the repository: a "
                 "COVERAGE finding has to grep the Lean.")
    return header, lines


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

    # -- discovery (docs/design/archive/multi-source-model.md S4.2, S4.3) ------
    @classmethod
    def detect(cls, repo: str | Path) -> tuple[float, dict]:
        """How sure are we that ``repo`` follows THIS convention, and why?

        ``cf onboard`` takes no --adapter in the common case: the kinds score
        themselves and the best one wins (a tie, or a best below 0.5, is a
        refusal asking for the flag).  :func:`cflib.discovery.detect_scores`
        calls this on every class in ``adapters.api.registry()``, so the
        heuristic for a convention -- its globs, its floors, its score constants
        -- lives on that convention's adapter and a new convention is a new
        file.  The default is 0.0: a kind that cannot recognise itself is never
        chosen automatically, only through ``--adapter``.

        The evidence dict is printed by `cf onboard` and stored in state.json.
        ``reason`` is the one line a human reads; everything else is the
        measurement behind it.  A key the adapter names here can also be written
        into the generated adapter.yaml, which is how a per-repo spelling of the
        convention (``tag.require_backticks``) stops being a hard-coded default.
        """
        return 0.0, {}

    @classmethod
    def explain(cls, repo: str | Path) -> list[str]:
        """Why this kind scored what it did -- the near-miss lines of a refusal.

        One "looked for / found" line per signal this convention keys on, for a
        repo it did NOT claim: ``pdf-overlay/official-source.json: absent``,
        ``docstring tags: 0/102 documented files (0 backticked, 86 bare -- set
        tag.require_backticks: false)``.  Without them the only thing an
        unrecognised repo is told is that it was not recognised, and the user's
        next move is to read the source.  Naming the override that would settle
        it is the point: a line that only counts is half a diagnosis.
        """
        return []

    #: The repo signals (:data:`cflib.discovery.SIGNALS`) this convention reads
    #: as its own.  They are ranked first by :meth:`discover_sources`, and they
    #: are the ones a subclass is responsible for enriching -- for
    #: ``docstring_tags`` the mathlib-initiative ``formalization.yaml`` and the
    #: Autoform ``graph.json``, for ``attribution_table`` the ledger's source
    #: block, ``official-source.json`` and the README's book-page link.
    PRIMARY_SIGNALS: ClassVar[tuple[str, ...]] = ()

    def discover_sources(self, repo: str | Path) -> list:
        """Source claims from the signals THIS kind knows about (S4.3).

        The base implementation runs every repo signal, ranked for this kind:
        design S4.3 is explicit that ALL signals run for EVERY adapter kind,
        because a repo may carry a signal that "belongs" to another convention
        (tao-measure-lean has both a ``formalization.yaml`` and an Autoform
        ``graph.json``).  The kind only orders the claims -- its own signals
        first, then by confidence -- so a subclass never has to re-implement a
        parser cflib.discovery already owns.

        A subclass calls ``super().discover_sources(repo)`` and then adds what
        only its convention can say: chiefly the item kinds its own tags use,
        which no signal over the SOURCE can supply when the PDF is not on this
        machine (:attr:`cflib.discovery.SourceClaim.kinds`).

        `cf onboard` calls this and merges what it ADDS into the seven repo
        signals (:func:`cflib.discovery.discover_sources_with_adapter`), so a
        convention whose source no signal can see has a way in.  A convention
        that IS its own manuscript says so by naming the files: a claim carrying
        :attr:`cflib.discovery.SourceClaim.files` -- absolute paths, in reading
        order -- is a source whose bytes are already in hand, and onboarding
        concatenates exactly that sequence, fingerprints it and registers it
        unconfirmed like any other discovered source, rather than searching for a
        PDF that does not exist.  A claim that repeats a signal cflib already
        read is dropped in favour of cflib's: an adapter may add a source, never
        restate one, because every later stage's item table is built from the
        source's own text and not from the companion's reading of it.
        """
        from .discovery import discover_sources
        claims = list(discover_sources(Path(repo), adapter=self.kind))
        primary = set(self.PRIMARY_SIGNALS)
        claims.sort(key=lambda c: (c.signal not in primary, -c.confidence, c.path))
        return claims

    def observed_kinds(self, repo: str | Path) -> tuple[str, ...]:
        """Item kinds THIS repo's tags actually use, in descending frequency.

        Empty by default; a subclass that can read its own tags overrides it.
        """
        return ()

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

    def default_source(self, sources: "Mapping[str, str] | None" = None) -> str:
        """The source_id an unmapped row belongs to.

        ``adapter.yaml``'s ``sources.default`` when it names a source the caller
        actually bound (or when nothing was bound at all, as in a stand-alone
        regression run); otherwise the single bound source.  A companion bound to
        several sources with no usable default is an ERROR rather than a guess:
        attributing a row to the wrong edition means judging a finding against
        the wrong pages, which is the failure this whole model exists to stop.
        """
        bound = dict(sources or {})
        declared = str((self.config.get("sources") or {}).get("default") or "")
        if declared and (not bound or declared in bound):
            return declared
        if len(bound) == 1:
            return next(iter(bound))
        if declared:
            raise AdapterError(
                f"{self.name}: sources.default is {declared!r}, which is not one of the "
                f"sources this companion is bound to ({', '.join(sorted(bound)) or 'none'})",
                hint="re-run `cf onboard <repo> --force` so adapter.yaml names the anchor "
                     "edition again, or fix sources.default by hand")
        raise AdapterError(
            f"{self.name}: cannot tell which source these rows belong to "
            f"(bound: {', '.join(sorted(bound)) or 'none'})",
            hint="set sources.default in adapter.yaml, or add sources.map rules")

    def bind_default_source(self, source_id: str) -> None:
        """Pin which source unmapped rows belong to (the companion's anchor).

        `cf onboard` decides the anchor edition MECHANICALLY and may re-decide it
        after extracting, so the anchor the workspace holds is fresher than the
        one written into adapter.yaml; `cf extract` pins the workspace's.  It
        also changes :meth:`key_inputs`, which is correct: the same repo read
        against another edition is another table and must not replay this one.
        """
        cfg = self.config.setdefault("sources", {})
        if not isinstance(cfg, dict):
            raise AdapterError(f"{self.name}: adapter.yaml `sources:` must be a mapping")
        cfg["default"] = str(source_id)

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
        companion is bound to.  It is the ONLY signature: a row that does not
        say which source it is about cannot be paired, gated or tallied, so
        there is no spelling of this call that omits the sources.
        """

    # -- idempotence ---------------------------------------------------
    def key_inputs(self) -> dict:
        """Everything that changes the extraction result.

        Subclasses extend this: ledger-backed adapters MUST add their export
        shas (the design critique: "the extract key must include ledger sha
        and target-refinement sha for ledger repos"), otherwise `cf extract`
        replays a stale table after the repo moves on.  The bound source shas
        belong in the key too: the same repo extracted against another edition
        is a different table.
        """
        return {
            "adapter_kind": self.kind,
            "config": self.config,
            "repo_commit": self.repo_commit(self.config.get("repo")),
            "source_shas": self.bound_source_shas(),
        }

    def bound_source_shas(self) -> dict:
        """``{source_id: source_sha}`` for every source this companion reads.

        Part of the extract key: the same repo extracted against another edition
        is a different table, and replaying the old one is exactly how batch-1's
        anchor findings came to be judged against the 2011 draft.
        """
        return {}

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
            # gitsafe, not a bare `git -C`: a companion repo is read as data, and
            # even `rev-parse` must not become an execve for `core.fsmonitor`.
            out = gitsafe.run(repo, ["rev-parse", "HEAD"], timeout=30)
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
    return loader(adapter_yaml, workspace=workspace)
