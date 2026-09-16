"""Every path in a campaign directory, in one place.

New code. The layout is design/architecture.md S2; no stage may invent a path
of its own, because `cf status`, the gate and harvest all have to agree on
where an artifact lives.
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


@dataclass(frozen=True)
class CampaignPaths:
    """Resolved paths inside one campaign directory."""

    root: Path
    book_id: str = ""

    # -- top level -----------------------------------------------------
    @property
    def campaign_yaml(self) -> Path:
        return self.root / "campaign.yaml"

    @property
    def gitignore(self) -> Path:
        return self.root / ".gitignore"

    # -- book (gitignored: copyrighted) --------------------------------
    @property
    def book_dir(self) -> Path:
        return self.root / "book" / self.book_id

    @property
    def book_txt(self) -> Path:
        return self.book_dir / "book.txt"

    @property
    def book_json(self) -> Path:
        return self.book_dir / "book.json"

    @property
    def items_json(self) -> Path:
        return self.book_dir / "items.json"

    @property
    def pages_dir(self) -> Path:
        return self.book_dir / "pages"

    @property
    def images_dir(self) -> Path:
        return self.book_dir / "images"

    @property
    def errata_md(self) -> Path:
        return self.book_dir / "errata.md"

    @property
    def errata_raw(self) -> Path:
        return self.book_dir / "errata.raw.html"

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

    # -- pairing -------------------------------------------------------
    @property
    def pairs_dir(self) -> Path:
        return self.root / "pairs"

    @property
    def pairs_jsonl(self) -> Path:
        return self.pairs_dir / "pairs.jsonl"

    @property
    def coverage_md(self) -> Path:
        return self.pairs_dir / "coverage.md"

    @property
    def queue_json(self) -> Path:
        return self.pairs_dir / "queue.json"

    def targets_json(self, name: str) -> Path:
        return self.pairs_dir / f"targets.{name}.json"

    def def_coverage_json(self, name: str) -> Path:
        return self.pairs_dir / f"def-coverage.{name}.json"

    def uncovered_json(self, name: str) -> Path:
        return self.pairs_dir / f"uncovered.{name}.json"

    @property
    def anchor_check_json(self) -> Path:
        return self.pairs_dir / "anchor-check.json"

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
        """Gitignored: tend logs and sessions quote the book verbatim."""
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
        """Create the directories a fresh campaign needs. Idempotent."""
        made = []
        dirs = [self.book_dir, self.companions_dir, self.pairs_dir, self.runs_dir,
                self.corpus_dir, self.ledger_dir, self.routed_dir, self.detect_dir,
                self.reports_dir, self.fixes_dir, self.reviews_dir]
        for d in dirs:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                made.append(d)
        return made

    def list_companions(self) -> list[str]:
        """Companion names present on disk (a directory with adapter.yaml)."""
        if not self.companions_dir.is_dir():
            return []
        return sorted(p.name for p in self.companions_dir.iterdir()
                      if p.is_dir() and (p / "adapter.yaml").exists())

    def list_runs(self) -> list[str]:
        if not self.runs_dir.is_dir():
            return []
        return sorted(p.name for p in self.runs_dir.iterdir()
                      if p.is_dir() and (p / "run.yaml").exists())
