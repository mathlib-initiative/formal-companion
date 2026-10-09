"""Adapter: a machine-readable item -> declaration attribution table.

Ported from the ``adam.jsonl`` generator of the v1 campaign (a one-off cell, not
in this repo), which joined measure_theory's overlay artifacts:

    pdf-overlay/data/geometry-batch-{a,b,c,d}.json   region -> item label + PDF page
    pdf-overlay/data/target-refinement.json          region -> formalization ids
                                                     (+ key_formalization_ids)
    pdf-overlay/data/declaration-snippets.json       id -> file, line, statement
    source-formalization-ledger.json                 id -> kind, module, file

Generalisations over the original cell (docs/design/stage_map.md Stage 2):

* the printed/PDF page offset is configuration, not the literal ``16``;
* item labels go through the strict grammar (``cflib.items``) instead of a
  three-component regex, so ``Exercise 1.1.6(i)`` keeps its sub-part and a
  label the grammar refuses is reported rather than silently relabelled;
* ``statement`` is the signature and ``body`` the definition body, instead of
  one blob;
* ``key()`` includes the ledger and target-refinement shas
  (the design critique: "the extract key must include ledger sha and
  target-refinement sha for ledger repos"), and the dry run reports when the
  refinement was curated against a *different* ledger than the one on disk —
  the "ledger-node repos go stale after fixes" failure mode.

Coverage is bounded by curation: the refinement covers the source nodes a
curator agent has been through, so items with no curated entry are simply
absent, and declarations that exist only as anonymous ``example``s are absent
by construction.  ``dry_run`` reports both numbers so the gap is visible.

**The cross-check** (docs/design/stage_map.md Stage 2) is what makes that gap
safe to publish.  This kind is the only one whose corpus is built from an
EXPORT of a companion rather than from the companion, and an export can omit
declarations the Lean sources carry -- named declarations inside a region the
convention files book statements under, and declarations whose docstring names
a book item.  A worker who greps a corpus built from such an export, finds
nothing and files a COVERAGE disagreement ("no named declaration of this
companion states X") is making a claim about the export, not the repository,
and it can be flatly false.  Absence from an export is not absence from a
repository.

:meth:`AttributionTableAdapter.lean_claims` reads the Lean sources for the two
signals the repo itself gives -- a docstring that names a book item, and
membership of a named ``section`` or ``namespace`` the convention files book
statements under -- and the omissions go two ways, because most of them cannot
be keyed on an item: typically only a few name one, and the rest sit under a
block header that names its item in prose with no number (``Companion 3: the
gadget hierarchy``), and guessing an item for them is the failure the grammar
exists to stop.  So an
omission that names an item is unioned into the table stamped ``provenance:
"lean_scan"``; one that names none is written to
``companions/<name>/unattributed.jsonl``, which `cf plan-run` stages beside the
statement tables and the prompts name as the list to read before any "no
counterpart exists" claim.  Under ``union`` and ``report`` `cf extract` / `cf
onboard` name the omissions in a warning, and under ``off`` they warn that no
cross-check ran: publishing a table that is short of its repository silently
would hide that.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from cflib import items as cf_items
from cflib.hashing import sha256_file

from . import leanbuild, leanparse
from .api import Adapter, AdapterError, register, sha_of_files
from .base import make_row
from .itemtag import kinds_from_labels

#: measure_theory's layout; overridable per companion in adapter.yaml.
DEFAULT_FILES = {
    "target_refinement": "pdf-overlay/data/target-refinement.json",
    "declaration_snippets": "pdf-overlay/data/declaration-snippets.json",
    "ledger": "source-formalization-ledger.json",
}
DEFAULT_GEOMETRY_GLOB = "pdf-overlay/data/geometry-batch-*.json"

BARE_SORRY = re.compile(r"\bsorry\b")

#: ledger ``kind`` -> the Lean keyword recorded in ``decl_kind``.
KIND_MAP = {
    "theorem": "theorem",
    "lemma": "theorem",
    "definition": "def",
    "def": "def",
    "abbreviation": "abbrev",
    "abbrev": "abbrev",
    "structure": "structure",
    "structure field": "projection",
    "structure projection": "projection",
    "projection": "projection",
    "instance": "instance",
    "inductive": "inductive",
    "anonymous example": "example",
    "module document": "module",
    "module documentation": "module",
    "check command": "check",
    "figure": "figure",
}
#: kinds whose body is content and is therefore captured
BODY_KINDS = frozenset({"def", "abbrev", "instance", "structure", "inductive"})

# -- the cross-check against the Lean sources (design Stage 2) ---------------
#: Where the cross-check reads declarations from.
DEFAULT_LEAN_GLOB = "**/*.lean"
#: And what it must never read.  This is not tidiness: ``**/*.lean`` over a
#: built Lake project walks ~10,000 files of Mathlib and would report every one
#: of its declarations as a declaration this companion's table omits.
DEFAULT_LEAN_EXCLUDE = (".lake/**/*.lean", "**/.lake/**/*.lean")
#: Named ``section`` or ``namespace`` blocks the convention files book
#: statements under, so membership is itself a claim on the book.  One repo
#: may spell the same block both ways -- ``section Companions`` in some files,
#: ``namespace Companions`` in others -- so both count.  Another repo names its
#: own word in adapter.yaml.
DEFAULT_CLAIM_REGIONS = ("Companions",)
#: union: publish the omission as a row AND warn.  report: warn only (the
#: omissions naming no item still go to unattributed.jsonl).  off: a deviation a
#: companion states in adapter.yaml, never one the code assumes, and it warns
#: that no check ran.  There is no value that passes quietly.  The same set as
#: cflib.vocab.CROSS_CHECK_POLICIES, in the order the refusal's hint prints;
#: tests/test_rf_adapters.py pins the two equal.
CROSS_CHECK_POLICIES = ("union", "report", "off")
DEFAULT_CROSS_CHECK_POLICY = "union"


def _refused_label(reject: "cf_items.Reject") -> str:
    """The refused mention itself (``Theorem 2.3.4.5``), not the prose after it."""
    ctx = reject.context or {}
    if ctx.get("kind") and ctx.get("number"):
        return f"{ctx['kind']} {ctx['number']}"
    return cf_items.clean(reject.raw)[:60]


@dataclass(frozen=True)
class LeanClaim:
    """A named declaration the Lean sources claim for the book.

    ``item`` is the book item its docstring names, or None when the only signal
    is :attr:`regions`.  A claim with no item is never unioned -- there is no
    key to file it under, and inventing one would be exactly the "let an
    unparseable id become a key" failure the item grammar exists to stop
    (CLAUDE.md S4) -- but it is not dropped either: it goes to the companion's
    ``unattributed.jsonl`` so a worker can read it.  Typically that is most of
    the omissions: item attribution is unrecoverable for a block whose header
    names its item in prose with no number.
    """

    decl: str
    file: str
    line: int
    kind: str
    private: bool
    regions: tuple[str, ...]        # the named section/namespace blocks that claimed it
    item: "cf_items.Item | None"
    signals: tuple[str, ...]
    parsed: "leanparse.Decl | None" = None

    def report(self, unioned: bool = False) -> dict:
        """The JSON shape ``dry_run`` publishes and `cf extract` warns with."""
        return {
            "decl": self.decl, "file": self.file, "line": self.line,
            "kind": self.kind, "private": self.private,
            "item": (self.item.full if self.item is not None else None),
            "signals": list(self.signals), "unioned": bool(unioned),
        }


@register
class AttributionTableAdapter(Adapter):
    """Companions that ship an item -> declaration map plus statement snippets."""

    kind = "attribution_table"

    #: The repo signals this convention owns (design S4.3): the ledger's
    #: ``source`` block (the sha is authoritative), ``official-source.json``
    #: (download URL, licence, book page) and the README's citation link, which
    #: is where measure_theory records the page the errata live on.  Ranked
    #: first by :meth:`discover_sources`; the rest still run.
    PRIMARY_SIGNALS = ("ledger_source", "official_source_json", "citation")

    # -- detection (design S4.2) -------------------------------------------
    #: The overlay layout is unambiguous -- a directory of region files NEXT TO
    #: a file naming the source they were measured against is not something a
    #: repo grows by accident -- so the score is a near-certainty and the
    #: constant lives with the adapter that reads those files.
    DETECT_SCORE = 0.98

    @classmethod
    def _overlay(cls, repo: str | Path) -> tuple[list, bool]:
        """``(pdf-overlay/data/*.json, official-source.json exists)``."""
        root = Path(repo)
        data = root / "pdf-overlay" / "data"
        files = sorted(data.glob("*.json")) if data.is_dir() else []
        return files, (root / "pdf-overlay" / "official-source.json").exists()

    @classmethod
    def detect(cls, repo: str | Path) -> tuple[float, dict]:
        files, official = cls._overlay(repo)
        if not (files and official):
            return 0.0, {}
        return cls.DETECT_SCORE, {
            "reason": f"{len(files)} pdf-overlay/data/*.json + official-source.json",
            "files": ["pdf-overlay/data", "pdf-overlay/official-source.json"],
        }

    @classmethod
    def explain(cls, repo: str | Path) -> list[str]:
        files, official = cls._overlay(repo)
        return [f"attribution table: pdf-overlay/data/*.json: {len(files)}; "
                f"pdf-overlay/official-source.json: {'present' if official else 'absent'}"]

    # -- config ------------------------------------------------------------
    def validate_config(self) -> None:
        if not self.config.get("repo"):
            raise AdapterError(f"{self.source}: 'repo' is required")
        repo = Path(str(self.config["repo"])).expanduser()
        for field in ("target_refinement", "declaration_snippets"):
            path = self._path(repo, field)
            if not path.is_file():
                raise AdapterError(
                    f"{self.source}: {field} not found at {path}",
                    hint=f"set {field}: <repo-relative path> in adapter.yaml")
        policy = self.cross_check_policy()
        if policy not in CROSS_CHECK_POLICIES:
            raise AdapterError(
                f"{self.source}: lean_scan.policy is {policy!r}",
                hint=f"one of {', '.join(CROSS_CHECK_POLICIES)}; `union` is the default and "
                     "there is no value that omits a declaration quietly")

    def _path(self, repo: Path, field: str) -> Path:
        return repo / str(self.config.get(field) or DEFAULT_FILES[field])

    def _geometry_files(self, repo: Path) -> list[Path]:
        raw = self.config.get("geometry_glob", self.config.get("geometry_globs"))
        patterns = [DEFAULT_GEOMETRY_GLOB] if not raw else (
            [str(raw)] if isinstance(raw, str) else [str(g) for g in raw])
        out: dict[str, Path] = {}
        for pattern in patterns:
            for p in sorted(repo.glob(pattern)):
                if p.is_file():
                    out[str(p)] = p
        return list(out.values())

    def _ledger_path(self, repo: Path) -> Path | None:
        rel = self.config.get("ledger", DEFAULT_FILES["ledger"])
        if not rel:
            return None
        path = repo / str(rel)
        return path if path.is_file() else None

    # -- joins -------------------------------------------------------------
    def _labels(self, repo: Path) -> list[str]:
        """Every overlay label, verbatim -- the repo's own item vocabulary."""
        out: list[str] = []
        for path in self._geometry_files(repo):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):      # pragma: no cover - broken export
                continue
            for entry in data.get("items", []):
                label = str(entry.get("label") or "").strip()
                if label:
                    out.append(label)
        return out

    def observed_kinds(self, repo: str | Path | None = None,
                       min_count: int = 2) -> tuple[str, ...]:
        """The item kinds this repo's overlay labels use.

        The geometry batches carry the label a human signed off against the page
        image, so they are the repo's statement of the source's grammar -- read
        with the same regex as a docstring tag, and with no kind list in the loop.
        """
        return kinds_from_labels(self._labels(self.resolve_repo(repo)), min_count=min_count)

    def _regions(self, repo: Path, source_id: str | None = None
                 ) -> tuple[dict[str, dict], Counter, list[dict]]:
        """region_id -> {item, sub, kind, label, page(printed)}; plus counters."""
        offset = self.page_offset(source_id)
        kinds = self.numbered_kinds(source_id)
        regions: dict[str, dict] = {}
        counts: Counter[str] = Counter()
        rejects: list[dict] = []
        for path in self._geometry_files(repo):
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data.get("items", []):
                pages = [r["page"] for r in entry.get("regions", []) if "page" in r]
                if not pages:
                    counts["regions_without_page"] += 1
                    continue
                printed = min(pages) - offset
                label = str(entry.get("label") or "").strip()
                item_id, sub, item_kind, reason = self._label_to_item(label, printed, kinds)
                if reason:
                    rejects.append({"raw": label, "reason": reason,
                                    "mapped_to": item_id, "page": printed})
                counts["items"] += 1
                counts["in_text_items" if item_kind == "In-text" else "numbered_items"] += 1
                for region in entry.get("regions", []):
                    regions[region["region_id"]] = {
                        "item": item_id, "sub": sub, "kind": item_kind,
                        "label": label, "page": printed,
                    }
        return regions, counts, rejects

    def _label_to_item(self, label: str, printed_page: int, kinds) -> tuple[str, str, str, str]:
        """Overlay label -> (item id, sub, kind, reject reason).

        A label the strict grammar refuses is prose: it becomes the In-text
        item of its printed page (the rule of the original generator), and the
        reason is reported so a mislabelled region is visible at onboarding.
        """
        item, reject = cf_items.try_parse(label, kinds)
        if item is not None and item.numbered:
            return item.id, item.sub, item.kind, ""
        return f"In-text p.{printed_page}", "", "In-text", (reject.reason if reject else "")

    def _rows(self, repo: Path, binding):
        # Every row of this convention comes from one export pair, so the source
        # is decided once: there is no per-row tag that could name another.
        source_id, source_sha = binding.resolve(file=self.config.get("target_refinement") or "",
                                                tag="", decl="<attribution table>")
        regions, counts, rejects = self._regions(repo, source_id)
        # A repo whose exercises ship with a permanent `sorry` classifies a
        # bare sorry as `exercise`; otherwise it is an unfinished `gap`.
        exercise_permanent = str(self.get("sorry.exercise_sorry", "permanent")).lower() != "none"
        refinement = json.loads(self._path(repo, "target_refinement").read_text(encoding="utf-8"))
        snippets = json.loads(
            self._path(repo, "declaration_snippets").read_text(encoding="utf-8")).get("snippets", {})
        ledger_kinds = self._ledger_kinds(repo)

        rows: list[dict] = []
        missing: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for entry in refinement.get("entries", []):
            region = regions.get(entry.get("region_id"))
            if region is None:
                counts["entries_without_region"] += 1
                missing.append({"region_id": entry.get("region_id"), "why": "no geometry region"})
                continue
            counts["entries"] += 1
            key_ids = set(entry.get("key_formalization_ids") or [])
            for decl in entry.get("formalization_ids") or []:
                dedup = (region["item"], region["sub"], decl)
                if dedup in seen:
                    counts["duplicate_attributions"] += 1
                    continue
                seen.add(dedup)
                snippet = snippets.get(decl)
                if not snippet or not str(snippet.get("text") or "").strip():
                    counts["missing_snippet"] += 1
                    missing.append({"decl": decl, "item": region["item"], "why": "no snippet"})
                    continue
                file = str(snippet.get("file") or "")
                line = int(snippet.get("line") or 0)
                if not file or line <= 0:
                    # A row with no source location cannot be reviewed or
                    # routed, so it is reported rather than written.
                    counts["missing_snippet"] += 1
                    missing.append({"decl": decl, "item": region["item"],
                                    "why": "snippet without file or line"})
                    continue
                text = leanparse.strip_comments(str(snippet["text"])).strip()
                ledger_kind = ledger_kinds.get(decl, "")
                decl_kind = KIND_MAP.get(ledger_kind.lower(), "unknown") if ledger_kind else "unknown"
                statement = leanparse.signature(text) or re.sub(r"\s+", " ", text).strip()
                has_sorry = bool(BARE_SORRY.search(text))
                sorry_class = ("none" if not has_sorry
                               else ("exercise" if exercise_permanent else "gap"))
                rows.append(make_row(
                    item_raw=region["label"] or region["item"],
                    item=region["item"],
                    sub=region["sub"],
                    item_kind=region["kind"],
                    source_id=source_id,
                    decl=decl,
                    kind=decl_kind,
                    namespace=decl.rsplit(".", 1)[0] if "." in decl else "",
                    file=file,
                    line=line,
                    statement=statement,
                    body=text if decl_kind in BODY_KINDS else None,
                    sorry=has_sorry,
                    sorry_class=sorry_class,
                    page_anchor=region["page"],
                    page_anchor_raw=f"book p. {region['page']}",
                    anchor_source="own",
                    repo=self.name,
                    key=decl in key_ids,
                    anonymous=ledger_kind == "anonymous example",
                    private=False,
                    granularity=str(self.config.get("granularity") or "item"),
                    source_sha=source_sha,
                    provenance="table",
                ))
        scan, scanned = self._cross_check(repo, regions, rows, source_id, source_sha,
                                          exercise_permanent)
        table_items = {r["item"] for r in rows}
        rows.extend(scanned)
        rows.sort(key=lambda r: (r["source_id"], r["item"], r["sub"], not r["key"], r["decl"]))
        # every number from the rows actually written: a unioned row can be a
        # sorry, and an item only the scan reached was still never curated.
        counts["sorry"] = sum(1 for r in rows if r["sorry"])
        counts["curated_items"] = len(table_items)
        counts["rows"] = len(rows)
        counts["declarations"] = len({r["decl"] for r in rows})
        counts["tagged"] = len(rows)
        return rows, counts, missing, rejects, scan

    def _cross_check(self, repo: Path, regions: Mapping[str, dict], table_rows: list[dict],
                     source_id: str, source_sha: str | None,
                     exercise_permanent: bool) -> tuple[dict, list[dict]]:
        """The table against the Lean sources: what does the export leave out?

        Returns ``(report, rows to union)``.  The comparison is by declaration
        NAME against the declarations the table actually produced rows for --
        not against the refinement's id list, which also holds ids whose snippet
        is missing and which therefore never reach the corpus a worker greps.
        """
        policy = self.cross_check_policy()
        report: dict = {"policy": policy, "regions": list(self.claim_regions())}
        if policy == "off":
            return report, []
        refused: list[dict] = []
        claims, counts = self.lean_claims(repo, source_id, refused=refused)
        in_table = {str(r["decl"]) for r in table_rows}
        # a label the grammar refused on a declaration the table already has is
        # the table's business; on one it lacks, it is why no item keyed it.
        refused = [r for r in refused if r["decl"] not in in_table]
        omitted = [c for c in claims if c.decl not in in_table]
        unionable = [c for c in omitted if c.item is not None]
        union = policy == "union"
        report.update({
            "files": counts["files"],
            "declarations": counts["declarations"],
            "claims": len(claims),
            "in_table": len(claims) - len(omitted),
            "omitted": len(omitted),
            "unioned": len(unionable) if union else 0,
            # omitted, but the docstring names no book item, so there is no key
            # to file them under: reported, never guessed onto an item.
            "unattributed": len(omitted) - len(unionable),
            "anonymous_claims": counts["anonymous_claims"],
            "lean_source": leanbuild.tally(self.lean_build(repo)),
            "omissions": [c.report(unioned=union and c.item is not None) for c in omitted],
            # leading docstring labels the item grammar refused (CLAUDE.md S4:
            # a reject is reported, never guessed onto the next mention)
            "refused_labels": refused[:50],
        })
        if not union:
            return report, []
        return report, self._scan_rows(unionable, regions, source_id, source_sha,
                                       exercise_permanent)

    def _ledger_kinds(self, repo: Path) -> dict[str, str]:
        path = self._ledger_path(repo)
        if path is None:
            return {}
        ledger = json.loads(path.read_text(encoding="utf-8"))
        return {n["id"]: str(n.get("kind") or "")
                for n in ledger.get("formalization_nodes", []) if n.get("id")}

    # -- the cross-check against the Lean sources --------------------------
    def cross_check_policy(self) -> str:
        """``union`` | ``report`` | ``off`` -- what an omission is allowed to do.

        YAML 1.1 reads an unquoted ``off`` as the boolean false, and both
        loaders (PyYAML and ``cflib.yamlio``'s fallback) do, so ``policy: off``
        written the way the template spells it arrives here as ``False``.  That
        is the one boolean with an unambiguous meaning, and it is read as
        ``off``; ``True`` (``on``/``yes``) names no policy and is refused.
        """
        raw = self.get("lean_scan.policy", DEFAULT_CROSS_CHECK_POLICY)
        if raw is False:
            return "off"
        if raw is None or raw == "":
            return DEFAULT_CROSS_CHECK_POLICY
        return str(raw).strip().lower()

    def claim_regions(self) -> tuple[str, ...]:
        """Names of the ``section``/``namespace`` blocks that claim the book.

        ``lean_scan.regions`` in adapter.yaml; ``sections`` is read as the same
        thing for a file written before the namespace spelling was found.
        """
        raw = self.get("lean_scan.regions", None)
        if raw in (None, "", [], ()):
            raw = self.get("lean_scan.sections", None)
        if raw in (None, "", [], ()):
            return DEFAULT_CLAIM_REGIONS
        return tuple(str(s) for s in ([raw] if isinstance(raw, str) else raw))

    def lean_files(self, repo: str | Path | None = None) -> list[Path]:
        """The Lean sources the cross-check reads: ``lean_scan.glob`` minus its
        excludes, minus anything this repository's git ignores.

        Not adapter.yaml's ``glob:``.  That one selects this convention's
        EXPORT (``pdf-overlay/data/*.json``), and pointing the cross-check at it
        would have it compare the table against itself.
        """
        path = self.resolve_repo(repo)
        raw = self.get("lean_scan.glob", DEFAULT_LEAN_GLOB)
        globs: Sequence[str] = [str(raw)] if isinstance(raw, str) else [str(g) for g in raw]
        skip = self.get("lean_scan.exclude", None)
        # A custom list ADDS to the Lake exclusion rather than replacing it: no
        # companion wants Mathlib's declarations reported as its own omissions.
        extra: Sequence[str] = ([] if skip in (None, "", [], ())
                                else ([str(skip)] if isinstance(skip, str)
                                      else [str(g) for g in skip]))
        excludes = list(DEFAULT_LEAN_EXCLUDE) + [g for g in extra if g not in DEFAULT_LEAN_EXCLUDE]
        return self.select_files(path, globs, excludes)

    def lean_claims(self, repo: str | Path | None = None, source_id: str | None = None,
                    refused: "list[dict] | None" = None) -> tuple[list[LeanClaim], Counter]:
        """Every NAMED declaration the Lean sources claim for the book.

        Two signals, both the repository's own and neither of them typed in:

        * a docstring that NAMES a book item, read with this source's kind list
          through :func:`cflib.items.find_items` -- the one item grammar, not a
          second regex here.  The LEADING mention wins, exactly as a docstring
          tag's does: ``Exercise 1.1.21(3) ... as in Definition 1.1.1`` claims
          the exercise and cross-references the definition.  A leading mention
          the grammar REFUSES claims nothing -- the next mention along is a
          cross-reference, not a fallback -- and it is appended to ``refused``
          (when the caller passes a list) so the refusal is reported rather
          than lost.
        * membership of a named ``section`` OR ``namespace`` this convention
          files book statements under (:meth:`claim_regions`).  Both spellings,
          because one repo may use both for the same block.

        Anonymous declarations are counted and never claimed.  The table's id
        for one is a curated slug (``example:BV-limits-...``), not a position,
        so there is no name to compare and every anonymous ``example`` would
        report as an omission, which would bury the named ones this is for.
        """
        path = self.resolve_repo(repo)
        kinds = self.numbered_kinds(source_id)
        wanted = set(self.claim_regions())
        claims: list[LeanClaim] = []
        counts: Counter[str] = Counter()
        build = self.lean_build(path)
        for lean in self.lean_files(path):
            rel = str(lean.relative_to(path))
            try:
                decls = leanbuild.parse_file(lean, rel, build=build)
            except (OSError, UnicodeDecodeError):   # pragma: no cover - unreadable source
                counts["unreadable_files"] += 1
                continue
            counts["files"] += 1
            for decl in decls:
                counts["declarations"] += 1
                in_sections = [s for s in decl.sections if s in wanted]
                in_namespaces = [s for s in decl.namespace.split(".") if s in wanted]
                rejects: list = []
                found = cf_items.find_items(decl.doc or "", kinds, limit=1, rejects=rejects)
                item = found[0] if found else None
                if rejects and refused is not None and not decl.anonymous:
                    refused.extend({"decl": decl.full_name, "file": rel, "line": decl.line,
                                    "label": _refused_label(r), "reason": r.reason}
                                   for r in rejects)
                if item is None and not (in_sections or in_namespaces):
                    continue
                if decl.anonymous:
                    counts["anonymous_claims"] += 1
                    continue
                signals = []
                if item is not None:
                    signals.append(f"docstring names {item.full}")
                if in_sections:
                    signals.append("section " + ", ".join(in_sections))
                if in_namespaces:
                    signals.append("namespace " + ", ".join(in_namespaces))
                claims.append(LeanClaim(
                    decl=decl.full_name, file=rel, line=decl.line, kind=decl.kind,
                    private=decl.private, regions=tuple(in_sections + in_namespaces),
                    item=item, signals=tuple(signals), parsed=decl))
        counts["claims"] = len(claims)
        return claims, counts

    def _scan_rows(self, claims: Sequence[LeanClaim], regions: Mapping[str, dict],
                   source_id: str, source_sha: str | None,
                   exercise_permanent: bool) -> list[dict]:
        """One row per omitted claim that carries a book item to file it under."""
        pages = self._item_pages(regions)
        rows: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for claim in claims:
            item = claim.item
            decl = claim.parsed
            if item is None or decl is None:
                continue
            # Two modules may each carry a same-named `private` declaration;
            # the table path dedupes the same way, and a duplicate key would
            # make `cf extract` refuse the whole table.  The omission list
            # still names both.
            dedup = (item.id, item.sub, claim.decl)
            if dedup in seen:
                continue
            seen.add(dedup)
            text = leanparse.strip_comments(decl.body).strip()
            has_sorry = bool(BARE_SORRY.search(text))
            page = pages.get(item.id)
            rows.append(make_row(
                # the docstring's own words for the item, whitespace collapsed
                item_raw=item.full,
                item=item.id,
                sub=item.sub,
                item_kind=item.kind,
                source_id=source_id,
                decl=claim.decl,
                kind=decl.kind,
                namespace=decl.namespace,
                file=claim.file,
                line=claim.line,
                statement=leanparse.signature(decl.body) or re.sub(r"\s+", " ", text).strip(),
                body=decl.def_body if decl.kind in BODY_KINDS else None,
                sorry=has_sorry,
                sorry_class=("none" if not has_sorry
                             else ("exercise" if exercise_permanent else "gap")),
                # the page is the OVERLAY's, for the item this declaration names:
                # the repo's own geometry, the same provenance the table rows
                # take theirs from.  An item the overlay never measured gets none
                # rather than a guessed one.
                page_anchor=page,
                page_anchor_raw=(f"book p. {page}" if page else None),
                anchor_source=("own" if page else "none"),
                repo=self.name,
                # the table decides what is KEY for an item, and the table has
                # never seen this declaration.
                key=False,
                anonymous=False,
                private=decl.private,
                granularity=str(self.config.get("granularity") or "item"),
                source_sha=source_sha,
                provenance="lean_scan",
            ))
        return rows

    @staticmethod
    def _item_pages(regions: Mapping[str, dict]) -> dict[str, int]:
        """item id -> the first printed page the overlay measured it on."""
        pages: dict[str, int] = {}
        for region in regions.values():
            item, page = region.get("item"), region.get("page")
            if item and isinstance(page, int) and page > 0:
                pages[item] = min(page, pages.get(item, page))
        return pages

    # -- interface ---------------------------------------------------------
    def extract(self, repo: str | Path | None = None, *,
                sources: "Mapping[str, str] | None" = None) -> Iterator[dict]:
        rows, *_rest = self._rows(self.resolve_repo(repo), self.bind(sources))
        yield from rows

    def dry_run(self, repo: str | Path | None = None, *,
                sources: "Mapping[str, str] | None" = None) -> dict:
        path = self.resolve_repo(repo)
        binding = self.bind(sources)
        rows, counts, missing, rejects, scan = self._rows(path, binding)
        fp = self.key_inputs()
        kinds = Counter(r["item_kind"] for r in rows)
        return {
            "adapter": self.kind,
            "name": self.name,
            "repo": str(path),
            "commit": fp.get("repo_commit"),
            "rows": len(rows),
            "declarations": counts["declarations"],
            "tagged": counts["tagged"],
            "items": len({r["item"] for r in rows}),
            "key_rows": sum(1 for r in rows if r["key"]),
            "geometry_items": counts["items"],
            "curated_entries": counts["entries"],
            "uncurated_items": counts["items"] - counts["curated_items"],
            "missing_snippet": counts["missing_snippet"],
            "entries_without_region": counts["entries_without_region"],
            "duplicate_attributions": counts["duplicate_attributions"],
            "sorry": counts["sorry"],
            "item_kinds": dict(sorted(kinds.items())),
            "ledger_sha": fp.get("ledger_sha"),
            "ledger_sha_in_refinement": fp.get("ledger_sha_in_refinement"),
            "ledger_stale": fp.get("ledger_stale"),
            # Labels the strict grammar refuses are prose, not errors: they
            # become the In-text item of their printed page. Reported so a
            # genuinely mislabelled region is visible at onboarding.
            "unnumbered_labels": len(rejects),
            "rejects": rejects[:50],
            "missing": missing[:50],
            # The table against the Lean sources it claims to describe. `cf
            # extract` and `cf onboard` turn `omissions` into a warning naming
            # every declaration: an export that omits a declaration the repo
            # carries is a defect in the companion's own table, and a corpus
            # that hides it produces COVERAGE findings that are simply false.
            "lean_scan": scan,
            "samples": rows[:3],
            # What this repo says its sources ARE (the ledger's sha, the
            # official-source record, the README's book page), and where the
            # rows were attributed. `cf onboard` prints both.
            "sources": self.source_report(path),
            "rows_by_source": {sid: sum(1 for r in rows if r["source_id"] == sid)
                               for sid in binding.ids()},
        }

    def key_inputs(self) -> dict:
        path = self.resolve_repo()
        fp = super().key_inputs()
        refinement_path = self._path(path, "target_refinement")
        fp["target_refinement_sha"] = sha256_file(refinement_path)
        fp["snippets_sha"] = sha256_file(self._path(path, "declaration_snippets"))
        fp["geometry_shas"] = {str(p.relative_to(path)): sha256_file(p)
                               for p in self._geometry_files(path)}
        ledger_path = self._ledger_path(path)
        if ledger_path is not None:
            fp["ledger_sha"] = sha256_file(ledger_path)
            declared = json.loads(refinement_path.read_text(encoding="utf-8")).get("ledger_sha256")
            fp["ledger_sha_in_refinement"] = declared
            fp["ledger_stale"] = bool(declared and declared != fp["ledger_sha"])
        # The cross-check READS the Lean sources, so they change the result: a
        # commit that adds a declaration to a `section Companions` block adds a
        # row and an omission warning, and without this `cf extract` would
        # replay the table that predates it at an unchanged export sha.
        if self.cross_check_policy() != "off":
            fp["lean_scan_sha"] = sha_of_files(self.lean_files(path), path)
            build = self.lean_build(path)
            if build is not None:   # without one, the key of the text parser's result is unchanged
                fp["lean_build"] = leanbuild.fingerprint(build, self.lean_files(path), path)
        return fp
