"""Source "stage" tables: which section of a source first develops a concept.

Ported from /Users/jack/Desktop/tao-differential/adam-detector/analyze2.py
(``MATHLIB_STAGE`` L51-120 and ``stage_of_mathlib_module`` L121-124, ``MOD_SEC``
L40-47, ``MATHLIB_MT`` L279-282, ``NATIVE`` L283-284). The tables themselves are
*source-specific, not repo-specific* (adam-detector/README.md:64-68), so they
ship as data in ``detect/mathlib_stages/<source_id>.json`` and the code here is
generic.  One file may serve several ``source_ids``: a stage is which SECTION
first develops a concept, and two editions of one work share that even when
their pagination differs.

A **stage** is an integer: 0 = foundations (fair game at any point in the book),
k = the book section (e.g. §1.k) where the concept is first developed, and
``stage_beyond_book`` (8 for Tao's measure theory book) = later than anything the
book covers. ``None`` means *unclassified*: a module in the subject's own
namespace that the table does not place. Unclassified modules are reported and
make the run fail unless ``--allow-unclassified`` is passed, because an
unclassified module silently suppresses hits (analyze2.py L172-173).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from detect._compat import CfError, parse_item, read_json

#: shipped tables live next to this module
STAGES_DIR = Path(__file__).resolve().parent / "mathlib_stages"


class StageTable:
    """Module-prefix -> stage table plus the source's object and native vocabularies."""

    def __init__(self, data: dict, path: Path | None = None):
        self.data = data
        self.path = path
        self.source_id: str = str(data.get("source_id") or data.get("book") or "unknown")
        #: every source_id this one table serves (editions of the same work)
        self.source_ids: tuple[str, ...] = tuple(
            str(s) for s in (data.get("source_ids") or [self.source_id]))
        self.chapter: int = int(data.get("chapter", 1))
        self.default_stage: int = int(data.get("default_stage", 0))
        self.stage_beyond_book: int | None = data.get("stage_beyond_book")
        self.module_stages: list[tuple[str, int | None]] = [
            (str(prefix), None if stage is None else int(stage))
            for prefix, stage in data.get("module_stages", [])
        ]
        self.repo_module_stages: list[tuple[str, int | None]] = [
            (str(prefix), None if stage is None else int(stage))
            for prefix, stage in data.get("repo_module_stages", [])
        ]
        #: fully qualified foreign constant -> {"label", "module", "stage"}
        self.objects: dict[str, dict] = dict(data.get("mathlib_objects", {}))
        #: identifier fragments the source's own formalisation uses
        self.native_tokens: list[str] = list(data.get("native_tokens", []))

    # -- loading ----------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "StageTable":
        """Load a stage table from an explicit path."""
        p = Path(path)
        data = read_json(p)
        if data is None:
            raise CfError(
                f"stage table not found: {p}",
                hint="ship one at scripts/detect/mathlib_stages/<source_id>.json "
                     "(see tao-measure-gsm126-published.json)",
            )
        return cls(data, path=p)

    @classmethod
    def for_source(cls, source_id: str, extra_dirs: Sequence[str | Path] = ()) -> "StageTable":
        """Find the table for ``source_id`` in the workspace's detect dir or the shipped ones.

        A file named ``<source_id>.json`` wins; failing that, any shipped table
        that LISTS this id under ``source_ids`` is used, so a sibling edition of
        the same work does not need its own copy of a table that would be
        identical.
        """
        for directory in [*extra_dirs, STAGES_DIR]:
            candidate = Path(directory) / f"{source_id}.json"
            if candidate.exists():
                return cls.load(candidate)
        for directory in [*extra_dirs, STAGES_DIR]:
            d = Path(directory)
            if not d.is_dir():
                continue
            for candidate in sorted(d.glob("*.json")):
                data = read_json(candidate)
                if isinstance(data, dict) and source_id in (data.get("source_ids") or []):
                    return cls(data, path=candidate)
        known = sorted(p.stem for p in STAGES_DIR.glob("*.json"))
        raise CfError(
            f"no stage table for source {source_id!r}",
            hint=f"write scripts/detect/mathlib_stages/{source_id}.json, or add {source_id!r} to "
                 f"an existing table's `source_ids` (shipped: {', '.join(known) or 'none'})",
        )

    # -- queries ----------------------------------------------------------
    def stage_of_module(self, module: str) -> int | None:
        """Stage of a foreign (Mathlib) module. First matching prefix wins.

        Ported from analyze2.stage_of_mathlib_module (L121-124), including its
        first-match-wins order: the shipped table keeps the original ordering,
        so a later, more specific prefix stays shadowed exactly as it was when
        the baseline reports were produced.
        """
        for prefix, stage in self.module_stages:
            if module == prefix or module.startswith(prefix + "."):
                return stage
        return self.default_stage

    def stage_of_repo_module(self, module: str) -> int | None:
        """Stage of one of the companion's own modules (analyze2.sec_of_module).

        Only used by the optional broad scan; empty for companions whose module
        layout has not been mapped.
        """
        for prefix, stage in self.repo_module_stages:
            if module == prefix or module.startswith(prefix + "."):
                return stage
        return None

    def stage_of_item(self, item: str, kinds: Iterable[str] | None = None) -> int | None:
        """Stage of a source item, by parsing its id.

        ``Definition 1.2.2`` -> 2. This is the substring parse
        adam-detector/README.md:70-73 recommends in place of that repo's
        label -> source_ids -> page fallback chain, and it needs no overlay
        artifacts. Items outside the table's chapter return ``None``.
        """
        parsed = parse_item(item) if kinds is None else parse_item(item, kinds)
        if parsed is None or parsed.chapter != self.chapter:
            return None
        return parsed.section

    def object_stage(self, constant: str) -> int | None:
        """Stage of a named foreign constant from the source's object vocabulary."""
        entry = self.objects.get(constant)
        if entry is None:
            return None
        stage = entry.get("stage")
        return None if stage is None else int(stage)

    def objects_by_label(self) -> dict[str, dict]:
        """``{"volume": {"constant": ..., "stage": ...}, ...}`` for text scans."""
        out: dict[str, dict] = {}
        for constant, entry in self.objects.items():
            label = str(entry.get("label") or constant.rsplit(".", 1)[-1])
            out.setdefault(label, {"constant": constant, **entry})
        return out
