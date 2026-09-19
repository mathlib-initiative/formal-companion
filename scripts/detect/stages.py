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

**Two halves, two homes.** A shipped table holds only what is true of the WORK
-- ``module_stages``, ``mathlib_objects``, ``stage_beyond_book``, the chapter
scope.  What is true of one COMPANION -- ``repo_module_stages`` (that repo's own
module layout) and ``native_tokens`` (that repo's own constructions) -- lives in
the workspace at ``companions/<name>/stage-overlay.json``, beside
``conventions.md``, and is applied with :meth:`StageTable.with_companion_overlay`.
The shipped file used to carry both, which is how a published copy of this skill
would have shipped one private repository's module names.

**A missing table is not a dead end.** :meth:`StageTable.for_source` with
``required=False`` returns an *unavailable* table instead of raising: the
def-coverage half of ``cf detect-proxies`` needs no stage table at all, so the
detector runs and the body-proxy scan reports that it was skipped and why.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Iterable, Sequence

from detect._compat import CfError, parse_item, read_json

#: shipped tables live next to this module
STAGES_DIR = Path(__file__).resolve().parent / "mathlib_stages"

#: keys that describe the WORK; a companion overlay may not claim them
SOURCE_LEVEL_KEYS = ("source_id", "source_ids", "chapter", "chapters", "default_stage",
                     "stage_beyond_book", "module_stages", "mathlib_objects")

#: keys that describe ONE COMPANION; these are what an overlay carries
COMPANION_LEVEL_KEYS = ("repo_module_stages", "native_tokens")


class StageTable:
    """Module-prefix -> stage table plus the source's object and native vocabularies."""

    def __init__(self, data: dict, path: Path | None = None, *,
                 available: bool = True, unavailable_reason: str = "",
                 overlay_path: Path | None = None):
        self.data = data
        self.path = path
        #: False when no table could be found: the table-free detectors still run
        self.available = available
        #: why there is no table, and the command that settles it
        self.unavailable_reason = unavailable_reason
        #: where the companion-level half came from, when one was applied
        self.overlay_path = overlay_path
        self.source_id: str = str(data.get("source_id") or data.get("book") or "unknown")
        #: every source_id this one table serves (editions of the same work)
        self.source_ids: tuple[str, ...] = tuple(
            str(s) for s in (data.get("source_ids") or [self.source_id]))
        self.chapter: int = int(data.get("chapter", 1))
        #: the chapters whose items have a stage.  ``()`` means "every chapter":
        #: the scope belongs to the workspace (``sources.<id>.chapters``), and a
        #: table that names none must not silently narrow a multi-chapter audit
        #: to its own chapter.
        self.chapters: tuple[int, ...] = tuple(
            int(c) for c in data.get("chapters", (self.chapter,)))
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
    def absent(cls, source_id: str, reason: str) -> "StageTable":
        """A stand-in for "there is no table": empty, and it says why.

        Every query answers "nothing is classified" rather than raising, so a
        caller that needs no table (def-coverage) runs unchanged and a caller
        that does (the body-proxy scan, the Lean tier) checks ``available`` and
        reports the skip instead of dying on an attribute.
        """
        return cls({"source_id": source_id, "module_stages": [], "mathlib_objects": {}},
                   path=None, available=False, unavailable_reason=reason)

    @classmethod
    def for_source(cls, source_id: str, extra_dirs: Sequence[str | Path] = (),
                   *, required: bool = True) -> "StageTable":
        """Find the table for ``source_id`` in the workspace's detect dir or the shipped ones.

        A file named ``<source_id>.json`` wins; failing that, any shipped table
        that LISTS this id under ``source_ids`` is used, so a sibling edition of
        the same work does not need its own copy of a table that would be
        identical.

        ``required=False`` returns :meth:`absent` instead of raising, for the
        callers whose work does not depend on a table.
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
        message = f"no stage table for source {source_id!r}"
        hint = (f"write one with `cf detect-proxies <companion> --source {source_id} "
                f"--init-stage-table`, pass --stages <path>, or add {source_id!r} to an "
                f"existing table's `source_ids` (shipped: {', '.join(known) or 'none'})")
        if not required:
            return cls.absent(source_id, f"{message} -- {hint}")
        raise CfError(message, hint=hint)

    # -- the two halves ---------------------------------------------------
    def with_companion_overlay(self, overlay: "dict | str | Path | None") -> "StageTable":
        """Apply ONE companion's half of the table: its modules and its vocabulary.

        ``repo_module_stages`` and ``native_tokens`` are facts about a single
        repository, not about the work, so they live in the workspace at
        ``companions/<name>/stage-overlay.json`` rather than in the shipped
        table.  An overlay that tries to restate a source-level key is refused:
        it would let one companion's file quietly re-stage the book for every
        other companion audited against the same source.
        """
        if overlay is None:
            return self
        path = None
        if isinstance(overlay, (str, Path)):
            path = Path(overlay)
            data = read_json(path)
            if data is None:
                raise CfError(f"companion stage overlay not found: {path}",
                              hint="write one from templates/detect/stage-overlay.json.tmpl")
        else:
            data = dict(overlay)
        claimed = [k for k in SOURCE_LEVEL_KEYS if k in data and k not in ("source_id",)]
        if claimed:
            raise CfError(
                f"companion stage overlay sets source-level key(s): {', '.join(claimed)}",
                hint=f"those belong in the source's stage table ({self.path or 'the shipped one'}); "
                     f"an overlay carries only {', '.join(COMPANION_LEVEL_KEYS)}")
        merged = copy.deepcopy(self.data)
        for key in COMPANION_LEVEL_KEYS:
            if key in data:
                merged[key] = data[key]
        return StageTable(merged, path=self.path, available=self.available,
                          unavailable_reason=self.unavailable_reason, overlay_path=path)

    def scoped_to_chapters(self, chapters: Iterable[int]) -> "StageTable":
        """Narrow (or widen) the chapters whose items have a stage.

        The audited scope is ``workspace.yaml sources.<id>.chapters``, not a
        property of the table: a table written for Chapter 1 is correct about
        §1.k for a run that audits Chapters 1 and 2, and returning ``None`` for
        every Chapter-2 item would silently suppress every hit in it.  An empty
        list means "do not narrow".
        """
        wanted = [int(c) for c in chapters]
        merged = copy.deepcopy(self.data)
        merged["chapters"] = wanted
        if wanted:
            merged["chapter"] = wanted[0]
        return StageTable(merged, path=self.path, available=self.available,
                          unavailable_reason=self.unavailable_reason,
                          overlay_path=self.overlay_path)

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
        artifacts. Items outside :attr:`chapters` return ``None``; see
        :meth:`scoped_to_chapters` for where that scope comes from.
        """
        parsed = parse_item(item) if kinds is None else parse_item(item, kinds)
        if parsed is None:
            return None
        if self.chapters and parsed.chapter not in self.chapters:
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
