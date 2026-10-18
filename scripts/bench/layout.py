"""Every path inside one bench directory, and the small helpers every bench module shares.

New code, for references/locate-bench.md section 1 (the layout).  The bench
directory itself comes from ``cflib.paths.WorkspacePaths.bench_dir``; the names
under it are fixed here, in one place, the way ``cflib.paths`` fixes the
workspace's, and the one variable name a bench path carries (a score id, a
labeler mode) is checked with ``cflib.paths``' own id rule.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from cflib import hashing
from cflib.errors import CfError
from cflib.jsonio import atomic_write
from cflib.paths import WorkspacePaths, _safe_id

#: the schema ids of the files a bench writes (references/locate-bench.md)
BENCH_SCHEMA = "cf-bench/1"
FRAME_SCHEMA = "cf-bench-frame/1"
DRAW_SCHEMA = "cf-bench-draw/1"
CLUSTERS_SCHEMA = "cf-bench-clusters/1"
SPANS_SCHEMA = "cf-bench-spans/1"
HOST_SCHEMA = "cf-bench-host/1"
G1_RESOLVED_SCHEMA = "cf-bench-g1-resolved/1"
LABELS_SCHEMA = "cf-bench-labels/1"
CALIBRATION_SCHEMA = "cf-bench-calibration/1"
GOLD_SCHEMA = "cf-bench-gold/1"
SCORE_SCHEMA = "cf-bench-score/1"
LEDGER_SCHEMA = "cf-bench-ledger/1"
SPEND_SCHEMA = "cf-bench-spend/1"
ABLATION_SCHEMA = "cf-bench-ablation/1"
LABEL_ARGS_SCHEMA = "cf-locate-label-args-v1"
LABEL_RESULT_SCHEMA = "cf-locate-label-v1"

#: the tier files under gold/, by tier
LABEL_FILES = {"G1": "g1.jsonl", "G3": "g3.jsonl", "GX": "gx.jsonl", "G4": "g4.jsonl"}

_WORD = re.compile(r"\S+")


@dataclass(frozen=True)
class BenchLayout:
    """``benches/<id>/`` and everything in it."""

    root: Path
    bench_id: str

    @classmethod
    def of(cls, paths: WorkspacePaths, bench_id: str) -> "BenchLayout":
        return cls(paths.bench_dir(bench_id), bench_id)

    @property
    def bench_yaml(self) -> Path:
        return self.root / "bench.yaml"

    @property
    def frame_json(self) -> Path:
        return self.root / "frame.json"

    @property
    def draw_json(self) -> Path:
        return self.root / "draw.json"

    @property
    def clusters_json(self) -> Path:
        return self.root / "clusters.json"

    @property
    def spans_json(self) -> Path:
        return self.root / "spans.json"

    # -- what the run host may see: label-free ---------------------------
    @property
    def host_dir(self) -> Path:
        return self.root / "host"

    @property
    def host_manifest(self) -> Path:
        return self.host_dir / "bench-host.json"

    @property
    def host_canaries(self) -> Path:
        return self.host_dir / "canaries.txt"

    # -- gold: stays on the operator's machine (section 8.4) -------------
    @property
    def gold_dir(self) -> Path:
        return self.root / "gold"

    @property
    def g1_resolved(self) -> Path:
        return self.gold_dir / "g1-resolved.json"

    def label_file(self, tier: str) -> Path:
        if tier not in LABEL_FILES:
            raise CfError(f"no label file for tier {tier!r}",
                          hint="labels are kept for " + ", ".join(sorted(LABEL_FILES)))
        return self.gold_dir / LABEL_FILES[tier]

    @property
    def calibration_json(self) -> Path:
        return self.gold_dir / "calibration.json"

    @property
    def planted_dir(self) -> Path:
        return self.gold_dir / "planted"

    @property
    def ablation_json(self) -> Path:
        return self.planted_dir / "ablation.json"

    @property
    def planted_db(self) -> Path:
        return self.planted_dir / "corpus.sqlite"

    def gold_manifest(self, version: int) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise CfError(f"a gold manifest version is a positive integer, not {version!r}")
        return self.root / f"gold-manifest-{version}.json"

    def gold_manifests(self) -> list[tuple[int, Path]]:
        found = []
        if self.root.is_dir():
            for path in self.root.glob("gold-manifest-*.json"):
                number = path.name[len("gold-manifest-"):-len(".json")]
                if number.isdigit() and not number.startswith("0"):
                    found.append((int(number), path))
        return sorted(found)

    # -- the labeler -------------------------------------------------------
    @property
    def labeler_dir(self) -> Path:
        return self.root / "labeler"

    def label_args(self, mode: str) -> Path:
        return self.labeler_dir / f"args-{_safe_id(mode, 'labeler mode')}.json"

    def label_result_copy(self, mode: str, digest: str) -> Path:
        return (self.labeler_dir / "results"
                / f"{_safe_id(mode, 'labeler mode')}-{_safe_id(digest[:12], 'result sha')}.json")

    @property
    def spend_json(self) -> Path:
        return self.root / "spend.json"

    # -- scores, audits, the loop ledger ----------------------------------
    @property
    def scores_dir(self) -> Path:
        return self.root / "scores"

    def score_json(self, score_id: str) -> Path:
        return self.scores_dir / f"{_safe_id(score_id, 'score id')}.json"

    def audit_jsonl(self, score_id: str) -> Path:
        return self.root / "audits" / f"{_safe_id(score_id, 'score id')}.jsonl"

    @property
    def ledger_jsonl(self) -> Path:
        return self.root / "ledger.jsonl"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def now() -> str:
    """UTC, to the second, ``Z``-suffixed."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


def canonical(obj: Any) -> str:
    """The one JSON spelling a sha is taken over: sorted keys, no spaces, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha_of(obj: Any) -> str:
    return hashing.sha256_json(obj)


def write_canonical_jsonl(path: Path, rows: Iterable[dict]) -> Path:
    """One canonical JSON object per line, atomically."""
    return atomic_write(path, "".join(canonical(row) + "\n" for row in rows))


def read_jsonl_rows(path: Path) -> list[dict]:
    """Every object of a JSONL file; a line that is not an object is an error naming it."""
    rows = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CfError(f"{path}:{number}: not JSON ({exc.msg})") from exc
            if not isinstance(row, dict):
                raise CfError(f"{path}:{number}: not a JSON object")
            rows.append(row)
    return rows


def word_spans(text: str, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    """``[(a, b)]`` of every maximal non-whitespace run of ``text[start:end]``, in text offsets."""
    end = len(text) if end is None else end
    return [(start + m.start(), start + m.end()) for m in _WORD.finditer(text[start:end])]


#: a curated list key: a Wikidata QID, or the 1000+ project's QID with one letter
#: (``Q4724004A``, ``Q180345X``) for several theorems of one Wikidata item
CURATED_KEY = re.compile(r"(Q[1-9][0-9]*)([A-Z])?\Z")


def curated_qid(key) -> str | None:
    """The Wikidata QID a curated key names (its letter dropped), or None for anything else."""
    found = CURATED_KEY.match(str(key or "").strip())
    return found.group(1) if found else None


#: the join's floor: the whole layer words two spans must share (section 8.2), and so
#: the fewest a gold span may hold -- a shorter one could never join any run passage
MIN_OVERLAP_WORDS = 4


def overlap_words(layer: str, one: tuple[int, int], two: tuple[int, int]) -> int:
    """How many layer words lie wholly inside both spans.

    A word is a maximal run of non-whitespace of the layer (the layer is
    whitespace-collapsed, so a word cannot straddle a collapsed run).  A word cut
    by either span's edge does not count: the overlap is the words both spans
    hold whole, so two spans that touch at one character share nothing.
    """
    a = max(int(one[0]), int(two[0]))
    b = min(int(one[1]), int(two[1]))
    if b <= a:
        return 0
    count = 0
    for wa, wb in _words_of(layer, a, b):
        if wa >= a and wb <= b:
            count += 1
    return count


def _words_of(layer: str, a: int, b: int) -> list[tuple[int, int]]:
    """The words of ``layer`` that intersect ``[a, b)``, with their full extents."""
    lo = a
    while lo > 0 and not layer[lo - 1].isspace():
        lo -= 1
    hi = b
    while hi < len(layer) and not layer[hi].isspace():
        hi += 1
    return word_spans(layer, lo, hi)
