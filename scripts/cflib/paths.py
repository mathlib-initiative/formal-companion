"""Every path in a workspace directory, in one place.

New code. The layout is docs/design/archive/multi-source-model.md S6.2; no stage may
invent a path of its own, because `cf status`, the gate and harvest all have to
agree on where an artifact lives.

A workspace holds N companions and M sources, so the two directories that used
to be singular are now keyed:

    book/<book-id>/...   ->  sources/<source_id>/...   (text.txt, meta.json, ...)
    pairs/...            ->  pairs/<source_id>/...     (one pairing per source)

``WorkspacePaths`` therefore has no ``book_id`` field: a path that depends on a
source takes the ``source_id`` as an argument, which is what makes it impossible
to write an artifact without saying which source it is about.

CLAUDE.md S5.3 makes this module the safety boundary ("never build a path by
string concatenation"), so every accessor that takes an id validates it with
:func:`_safe_id`: ``self.sources_dir / source_id`` discards the workspace root
entirely for an absolute id, and normalises out of the workspace for ``..``.
Nothing typed in reaches these helpers today -- ids are slugified, or a directory
name -- but the whole design direction is that facts about a source are
discovered from repo content, so the pressure is toward repo-derived strings
arriving here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import CfError


def _safe_id(value: str, kind: str = "id") -> str:
    """Return *value* if it can only name something INSIDE the workspace.

    The refusals are what a joined id can do to a workspace path:

    * empty -- ``sources/`` itself, silently standing in for a source;
    * a path separator -- a subtree of the workspace, or with ``..`` anything
      above it;
    * absolute -- pathlib drops the left operand, so ``/etc/cron.d/evil``
      becomes the whole path;
    * ``.`` / ``..`` -- the workspace root and its parent.

    A separator is refused first, so ``..`` can only ever be the whole string:
    an interior dot (``smith-analysis-1.2``) is a legal name and stays legal.

    A non-string is refused BEFORE anything is coerced.  ``str(value)`` would have
    turned ``None`` into ``sources/None`` and ``7`` into ``sources/7``, where the
    unvalidated code raised ``TypeError`` outright: a hardening pass must not turn
    a loud programming error into a quietly wrong path (CLAUDE.md S4, "low
    confidence is a refusal").
    """
    if not isinstance(value, str):
        raise CfError(f"unsafe {kind} {value!r}: it is not a string",
                      hint="ids are strings: a discovered (slugified) id, or one typed with "
                           "--source/--name. A None here is a caller that lost its id")
    text = value
    if not text or not text.strip():
        problem = "it is empty"
    elif "/" in text or "\\" in text:
        problem = "it contains a path separator"
    elif "\x00" in text:
        problem = "it contains a NUL byte"
    elif text in (".", ".."):
        problem = "it names the workspace directory or its parent"
    elif Path(text).is_absolute():
        problem = "it is an absolute path"
    else:
        return text
    raise CfError(f"unsafe {kind} {value!r}: {problem}",
                  hint="ids name one directory or file inside the workspace; they are "
                       "discovered (slugified) or typed with --source/--name, never a path")


def skill_root() -> Path:
    """The formal-companion repo root (parent of scripts/)."""
    return Path(__file__).resolve().parents[2]


def templates_dir() -> Path:
    return skill_root() / "templates"


def references_dir() -> Path:
    return skill_root() / "references"


def workflows_dir() -> Path:
    return skill_root() / "workflows"


def registry_dir() -> Path:
    """The skill's own source registry directory (``registry/sources.yaml``)."""
    return skill_root() / "registry"


@dataclass(frozen=True)
class WorkspacePaths:
    """Resolved paths inside one workspace directory."""

    root: Path

    # -- top level -----------------------------------------------------
    @property
    def workspace_yaml(self) -> Path:
        return self.root / "workspace.yaml"

    @property
    def registry_yaml(self) -> Path:
        """The workspace's registry overlay (metadata only, never book text)."""
        return self.root / "registry.yaml"

    @property
    def gitignore(self) -> Path:
        return self.root / ".gitignore"

    # -- sources (gitignored: copyrighted) -----------------------------
    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    def source_dir(self, source_id: str) -> Path:
        return self.sources_dir / _safe_id(source_id, "source_id")

    def source_text(self, source_id: str) -> Path:
        return self.source_dir(source_id) / "text.txt"

    def source_meta(self, source_id: str) -> Path:
        return self.source_dir(source_id) / "meta.json"

    def source_items(self, source_id: str) -> Path:
        return self.source_dir(source_id) / "items.json"

    def source_pages_dir(self, source_id: str) -> Path:
        return self.source_dir(source_id) / "pages"

    def source_images_dir(self, source_id: str) -> Path:
        return self.source_dir(source_id) / "images"

    def source_errata_md(self, source_id: str) -> Path:
        return self.source_dir(source_id) / "errata.md"

    def source_errata_raw(self, source_id: str) -> Path:
        return self.source_dir(source_id) / "errata.raw.html"

    def source_errata_json(self, source_id: str) -> Path:
        return self.source_dir(source_id) / "errata.json"

    def list_sources(self) -> list[str]:
        """Source ids prepared on disk (a directory holding meta.json)."""
        if not self.sources_dir.is_dir():
            return []
        return sorted(p.name for p in self.sources_dir.iterdir()
                      if p.is_dir() and (p / "meta.json").exists())

    # -- companions ----------------------------------------------------
    @property
    def companions_dir(self) -> Path:
        return self.root / "companions"

    def companion_dir(self, name: str) -> Path:
        return self.companions_dir / _safe_id(name, "companion name")

    def adapter_yaml(self, name: str) -> Path:
        return self.companion_dir(name) / "adapter.yaml"

    def conventions_md(self, name: str) -> Path:
        return self.companion_dir(name) / "conventions.md"

    def statements_jsonl(self, name: str) -> Path:
        return self.companion_dir(name) / "statements.jsonl"

    def state_json(self, name: str) -> Path:
        return self.companion_dir(name) / "state.json"

    def unattributed_jsonl(self, name: str) -> Path:
        """Declarations the repo claims for the book that its table files under
        no item -- written by `cf extract` from an export-driven adapter's
        cross-check, staged into runs beside statements.jsonl, and absent when
        there are none (or when the adapter reads the Lean directly)."""
        return self.companion_dir(name) / "unattributed.jsonl"

    def lint_json(self, name: str) -> Path:
        return self.companion_dir(name) / "lint.json"

    def list_companions(self) -> list[str]:
        """Companion names present on disk (a directory with adapter.yaml)."""
        if not self.companions_dir.is_dir():
            return []
        return sorted(p.name for p in self.companions_dir.iterdir()
                      if p.is_dir() and (p / "adapter.yaml").exists())

    # -- pairing, one tree per source ----------------------------------
    @property
    def pairs_root(self) -> Path:
        return self.root / "pairs"

    def pairs_dir(self, source_id: str) -> Path:
        return self.pairs_root / _safe_id(source_id, "source_id")

    def pairs_jsonl(self, source_id: str) -> Path:
        return self.pairs_dir(source_id) / "pairs.jsonl"

    def coverage_md(self, source_id: str) -> Path:
        return self.pairs_dir(source_id) / "coverage.md"

    def index_json(self, source_id: str) -> Path:
        return self.pairs_dir(source_id) / "index.json"

    def reconciliation_json(self, source_id: str) -> Path:
        return self.pairs_dir(source_id) / "reconciliation.json"

    def rejected_json(self, source_id: str) -> Path:
        return self.pairs_dir(source_id) / "rejected.json"

    def queue_json(self, source_id: str) -> Path:
        return self.pairs_dir(source_id) / "queue.json"

    def targets_json(self, source_id: str, name: str) -> Path:
        return self.pairs_dir(source_id) / f"targets.{_safe_id(name, 'companion name')}.json"

    def def_coverage_json(self, source_id: str, name: str) -> Path:
        return self.pairs_dir(source_id) / f"def-coverage.{_safe_id(name, 'companion name')}.json"

    def uncovered_json(self, source_id: str, name: str) -> Path:
        return self.pairs_dir(source_id) / f"uncovered.{_safe_id(name, 'companion name')}.json"

    def anchor_check_json(self, source_id: str) -> Path:
        return self.pairs_dir(source_id) / "anchor-check.json"

    def list_paired_sources(self) -> list[str]:
        if not self.pairs_root.is_dir():
            return []
        return sorted(p.name for p in self.pairs_root.iterdir() if p.is_dir())

    # -- runs and corpus -----------------------------------------------
    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / _safe_id(run_id, "run_id")

    def run_yaml(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run.yaml"

    def gate_json(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "gate.json"

    def tasks_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "tasks"

    def prompts_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "prompts"

    def tend_root(self, run_id: str) -> Path:
        """Gitignored: tend logs and sessions quote the source verbatim."""
        return self.run_dir(run_id) / "tend-root"

    @property
    def corpus_dir(self) -> Path:
        return self.root / "corpus"

    def corpus_run_dir(self, run_id: str) -> Path:
        return self.corpus_dir / _safe_id(run_id, "run_id")

    def receipt_json(self, run_id: str) -> Path:
        return self.corpus_run_dir(run_id) / "RECEIPT.json"

    # -- pipelines (docs/proposals/pdf-to-companion.md section 4.1) ----
    @property
    def pipelines_dir(self) -> Path:
        return self.root / "pipelines"

    def pipeline_dir(self, pipeline_id: str) -> Path:
        return self.pipelines_dir / _safe_id(pipeline_id, "pipeline id")

    def pipeline_yaml(self, pipeline_id: str) -> Path:
        return self.pipeline_dir(pipeline_id) / "pipeline.yaml"

    def pipeline_doc(self, pipeline_id: str, version: int) -> Path:
        """The checkpointed annotation document doc v<version>."""
        return self.pipeline_dir(pipeline_id) / f"doc.v{int(version)}.json"

    def pipeline_repo(self, pipeline_id: str) -> Path:
        """The generated companion repository S0 creates (its own git repo, ignored here)."""
        return self.pipeline_dir(pipeline_id) / "repo"

    def stage_receipts_dir(self, pipeline_id: str) -> Path:
        return self.pipeline_dir(pipeline_id) / "stages"

    def stage_receipt_json(self, pipeline_id: str, stage: str) -> Path:
        return self.stage_receipts_dir(pipeline_id) / (
            f"{_safe_id(stage, 'stage')}.stage-receipt.json")

    # -- locate (docs/proposals/wikipedia-locate.md; the layout is
    # references/locate-documents.md section 7) -------------------------
    #
    # A web source does not live in sources/ or in workspace.yaml -- twenty
    # thousand of them fit neither -- but in a SOURCESET: append-only manifests
    # plus one directory per source.  The per-source directories sit under
    # sourcesets/<id>/sources/, so the workspace .gitignore's `sources/` line
    # keeps their page text out of git with no rule of its own.
    @property
    def sourcesets_dir(self) -> Path:
        return self.root / "sourcesets"

    def sourceset_dir(self, sourceset_id: str) -> Path:
        return self.sourcesets_dir / _safe_id(sourceset_id, "sourceset id")

    def sourceset_manifest(self, sourceset_id: str, version: int) -> Path:
        """``manifest-<n>.jsonl``: version ``n`` of the sourceset, never rewritten."""
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise CfError(f"a sourceset manifest version is a positive integer, not {version!r}")
        return self.sourceset_dir(sourceset_id) / f"manifest-{version}.jsonl"

    def sourceset_manifests(self, sourceset_id: str) -> list[tuple[int, Path]]:
        """``[(n, path)]`` of every manifest on disk, oldest first."""
        directory = self.sourceset_dir(sourceset_id)
        if not directory.is_dir():
            return []
        found = []
        for path in directory.glob("manifest-*.jsonl"):
            number = path.name[len("manifest-"):-len(".jsonl")]
            if number.isdigit() and not number.startswith("0"):
                found.append((int(number), path))
        return sorted(found)

    def sourceset_sources_dir(self, sourceset_id: str) -> Path:
        return self.sourceset_dir(sourceset_id) / "sources"

    def sourceset_source_dir(self, sourceset_id: str, source_id: str) -> Path:
        """One web source's files: the raw bytes, the body, the layer and its views."""
        return self.sourceset_sources_dir(sourceset_id) / _safe_id(source_id, "source_id")

    def sourceset_state_json(self, sourceset_id: str) -> Path:
        """The sourceset-level state file locate writes instead of companions/<n>/state.json."""
        return self.sourceset_dir(sourceset_id) / "state.json"

    def candidates_dir(self, sourceset_id: str, universe_id: str) -> Path:
        """L2: one candidates file per source, for one universe."""
        return (self.sourceset_dir(sourceset_id) / "candidates"
                / _safe_id(universe_id, "universe id"))

    def candidates_json(self, sourceset_id: str, universe_id: str, source_id: str) -> Path:
        return (self.candidates_dir(sourceset_id, universe_id)
                / f"{_safe_id(source_id, 'source_id')}.json")

    @property
    def corpora_dir(self) -> Path:
        """Universes of libraries.  Not ``corpus/``, which holds harvested findings."""
        return self.root / "corpora"

    @property
    def corpora(self) -> "CorporaPaths":
        """The universe layout under ``corpora/`` (:class:`CorporaPaths`)."""
        return CorporaPaths(self.corpora_dir)

    def universe_dir(self, universe_id: str) -> Path:
        return self.corpora.universe_dir(universe_id)

    def universe_yaml(self, universe_id: str) -> Path:
        return self.corpora.universe_yaml(universe_id)

    def universe_index_dir(self, universe_id: str, library_id: str) -> Path:
        return self.corpora.universe_index_dir(universe_id, library_id)

    def universe_conventions_md(self, universe_id: str, library_id: str) -> Path:
        return self.corpora.universe_conventions_md(universe_id, library_id)

    def corpus_db(self, universe_id: str) -> Path:
        """``corpus.sqlite``: derived from the indexes, rebuildable, opened read-only."""
        return self.corpora.corpus_db(universe_id)

    def corpus_db_blind(self, universe_id: str) -> Path:
        """``corpus.blind.sqlite``: the G1-blind database -- no tags, no docstring Wikipedia URLs."""
        return self.corpora.corpus_db_blind(universe_id)

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    def export_dir(self, set_id: str) -> Path:
        """One sealed record set (references/locate-documents.md section 6)."""
        return self.exports_dir / _safe_id(set_id, "record set id")

    def export_report_json(self, set_id: str) -> Path:
        """What one export refused and why: beside the set, never inside its seal."""
        return self.reports_dir / f"export.{_safe_id(set_id, 'record set id')}.json"

    @property
    def benches_dir(self) -> Path:
        return self.root / "benches"

    def bench_dir(self, bench_id: str) -> Path:
        return self.benches_dir / _safe_id(bench_id, "bench id")

    @property
    def campaigns_dir(self) -> Path:
        return self.root / "campaigns"

    def campaign_dir(self, campaign_id: str) -> Path:
        return self.campaigns_dir / _safe_id(campaign_id, "campaign id")

    def campaign_yaml(self, campaign_id: str) -> Path:
        return self.campaign_dir(campaign_id) / "campaign.yaml"

    def locate_outcomes_jsonl(self, run_id: str) -> Path:
        """One row per passage of a harvested locate run, with its derived status."""
        return self.reports_dir / f"locate-outcomes.{_safe_id(run_id, 'run_id')}.jsonl"

    def locate_tally_json(self, run_id: str) -> Path:
        return self.reports_dir / f"locate-tally.{_safe_id(run_id, 'run_id')}.json"

    # -- ledger, routing, detection ------------------------------------
    @property
    def ledger_dir(self) -> Path:
        return self.root / "ledger"

    def accepted_jsonl(self, name: str) -> Path:
        return self.ledger_dir / f"accepted.{_safe_id(name, 'companion name')}.jsonl"

    def refuted_jsonl(self, name: str) -> Path:
        return self.ledger_dir / f"refuted.{_safe_id(name, 'companion name')}.jsonl"

    def adjudicated_md(self, name: str) -> Path:
        return self.ledger_dir / f"adjudicated.{_safe_id(name, 'companion name')}.md"

    @property
    def routed_dir(self) -> Path:
        return self.ledger_dir / "routed"

    def routed_jsonl(self, name: str) -> Path:
        return self.routed_dir / f"findings.{_safe_id(name, 'companion name')}.jsonl"

    @property
    def detect_dir(self) -> Path:
        return self.root / "detect"

    def detect_companion_dir(self, name: str) -> Path:
        return self.detect_dir / _safe_id(name, "companion name")

    # -- fixes, reviews, reports (v1: reports only) --------------------
    @property
    def fixes_dir(self) -> Path:
        return self.root / "fixes"

    @property
    def reviews_dir(self) -> Path:
        return self.root / "reviews"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    def tally_json(self, run_id: str) -> Path:
        return self.reports_dir / f"tally.{_safe_id(run_id, 'run_id')}.json"

    def union_json(self, name: str) -> Path:
        return self.reports_dir / f"union.{_safe_id(name, 'companion name')}.json"

    def pin_json(self, run_id: str) -> Path:
        """The companion revision a run's findings were judged against.

        A report and not a corpus file: writing it into ``corpus/<run-id>/`` would
        make the findings gate read it as a finding, and the corpus is the set of
        files whose shas the receipt manifests.
        """
        return self.reports_dir / f"pin.{_safe_id(run_id, 'run_id')}.json"

    def recheck_json(self, name: str) -> Path:
        """One companion's latest ``cf recheck`` result."""
        return self.reports_dir / f"recheck.{_safe_id(name, 'companion name')}.json"

    def score_json(self, run_id: str) -> Path:
        return self.reports_dir / f"score.{_safe_id(run_id, 'run_id')}.json"

    @property
    def agreed_high_json(self) -> Path:
        return self.reports_dir / "agreed_high.json"

    # -- helpers -------------------------------------------------------
    def ensure_skeleton(self) -> list[Path]:
        """Create the directories a fresh workspace needs. Idempotent."""
        made = []
        dirs = [self.sources_dir, self.companions_dir, self.pairs_root, self.runs_dir,
                self.corpus_dir, self.ledger_dir, self.routed_dir, self.detect_dir,
                self.reports_dir, self.fixes_dir, self.reviews_dir]
        for d in dirs:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                made.append(d)
        return made

    def list_runs(self) -> list[str]:
        if not self.runs_dir.is_dir():
            return []
        return sorted(p.name for p in self.runs_dir.iterdir()
                      if p.is_dir() and (p / "run.yaml").exists())


@dataclass(frozen=True)
class CorporaPaths:
    """The universe layout under one directory: a workspace's ``corpora/``, or any other.

    The one definition of where a universe's files live, which
    ``WorkspacePaths`` delegates to for its ``corpora/``.  ``cf corpus --base
    <dir>`` addresses a universe kept outside any workspace -- on the run host,
    ``<dir>/<universe>/universe.yaml`` beside ``index/`` and ``corpus.sqlite`` --
    through this class alone, so both places hold one layout
    (references/locate-documents.md section 8).
    """

    base: Path

    def universe_dir(self, universe_id: str) -> Path:
        return self.base / _safe_id(universe_id, "universe id")

    def universe_yaml(self, universe_id: str) -> Path:
        return self.universe_dir(universe_id) / "universe.yaml"

    def universe_index_dir(self, universe_id: str, library_id: str) -> Path:
        return self.universe_dir(universe_id) / "index" / _safe_id(library_id, "library id")

    def universe_conventions_md(self, universe_id: str, library_id: str) -> Path:
        return (self.universe_dir(universe_id) / "conventions"
                / f"{_safe_id(library_id, 'library id')}.md")

    def corpus_db(self, universe_id: str) -> Path:
        """``corpus.sqlite``: derived from the indexes, rebuildable, opened read-only."""
        return self.universe_dir(universe_id) / "corpus.sqlite"

    def corpus_db_blind(self, universe_id: str) -> Path:
        """``corpus.blind.sqlite``: the G1-blind database -- no tags, no docstring Wikipedia URLs."""
        return self.universe_dir(universe_id) / "corpus.blind.sqlite"
