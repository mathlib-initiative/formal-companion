"""Every path in a workspace directory, in one place.

New code. The layout is docs/design/multi-source-model.md S6.2; no stage may
invent a path of its own, because `cf status`, the gate and harvest all have to
agree on where an artifact lives.

A workspace holds N companions and M sources, so the two directories that used
to be singular are now keyed:

    book/<book-id>/...   ->  sources/<source_id>/...   (text.txt, meta.json, ...)
    pairs/...            ->  pairs/<source_id>/...     (one pairing per source)

``WorkspacePaths`` therefore has no ``book_id`` field: a path that depends on a
source takes the ``source_id`` as an argument, which is what makes it impossible
to write an artifact without saying which source it is about.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def skill_root() -> Path:
    """The companion-faithfulness repo root (parent of scripts/)."""
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
        return self.sources_dir / source_id

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
        return self.companions_dir / name

    def adapter_yaml(self, name: str) -> Path:
        return self.companion_dir(name) / "adapter.yaml"

    def conventions_md(self, name: str) -> Path:
        return self.companion_dir(name) / "conventions.md"

    def statements_jsonl(self, name: str) -> Path:
        return self.companion_dir(name) / "statements.jsonl"

    def state_json(self, name: str) -> Path:
        return self.companion_dir(name) / "state.json"

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
        return self.pairs_root / source_id

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
        return self.pairs_dir(source_id) / f"targets.{name}.json"

    def def_coverage_json(self, source_id: str, name: str) -> Path:
        return self.pairs_dir(source_id) / f"def-coverage.{name}.json"

    def uncovered_json(self, source_id: str, name: str) -> Path:
        return self.pairs_dir(source_id) / f"uncovered.{name}.json"

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
        return self.runs_dir / run_id

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
        return self.corpus_dir / run_id

    def receipt_json(self, run_id: str) -> Path:
        return self.corpus_run_dir(run_id) / "RECEIPT.json"

    # -- ledger, routing, detection ------------------------------------
    @property
    def ledger_dir(self) -> Path:
        return self.root / "ledger"

    def accepted_jsonl(self, name: str) -> Path:
        return self.ledger_dir / f"accepted.{name}.jsonl"

    def refuted_jsonl(self, name: str) -> Path:
        return self.ledger_dir / f"refuted.{name}.jsonl"

    def adjudicated_md(self, name: str) -> Path:
        return self.ledger_dir / f"adjudicated.{name}.md"

    @property
    def routed_dir(self) -> Path:
        return self.ledger_dir / "routed"

    def routed_jsonl(self, name: str) -> Path:
        return self.routed_dir / f"findings.{name}.jsonl"

    @property
    def detect_dir(self) -> Path:
        return self.root / "detect"

    def detect_companion_dir(self, name: str) -> Path:
        return self.detect_dir / name

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
        return self.reports_dir / f"tally.{run_id}.json"

    def union_json(self, name: str) -> Path:
        return self.reports_dir / f"union.{name}.json"

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
