"""Adapter: a machine-readable item -> declaration attribution table.

Ported from the ``adam.jsonl`` generator,
  scratchpad/recovered/11323fe3_L3102_Bash.txt
which joined measure_theory's overlay artifacts:

    pdf-overlay/data/geometry-batch-{a,b,c,d}.json   region -> item label + PDF page
    pdf-overlay/data/target-refinement.json          region -> formalization ids
                                                     (+ key_formalization_ids)
    pdf-overlay/data/declaration-snippets.json       id -> file, line, statement
    source-formalization-ledger.json                 id -> kind, module, file

Generalisations over the original cell (design/stage_map.md Stage 2):

* the printed/PDF page offset is configuration, not the literal ``16``;
* item labels go through the strict grammar (``cflib.items``) instead of a
  three-component regex, so ``Exercise 1.1.6(i)`` keeps its sub-part and a
  label the grammar refuses is reported rather than silently relabelled;
* ``statement`` is the signature and ``body`` the definition body, instead of
  one blob;
* ``key()`` includes the ledger and target-refinement shas
  (design/critique.json: "the extract key must include ledger sha and
  target-refinement sha for ledger repos"), and the dry run reports when the
  refinement was curated against a *different* ledger than the one on disk —
  the "ledger-node repos go stale after fixes" failure mode.

Coverage is bounded by curation: the refinement covers the source nodes a
curator agent has been through, so items with no curated entry are simply
absent, and declarations that exist only as anonymous ``example``s are absent
by construction.  ``dry_run`` reports both numbers so the gap is visible.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Iterator, Mapping

from cflib import items as cf_items
from cflib.hashing import sha256_file

from . import leanparse
from .api import Adapter, AdapterError, register
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
                counts["sorry"] += 1 if has_sorry else 0
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
                ))
        rows.sort(key=lambda r: (r["source_id"], r["item"], r["sub"], not r["key"], r["decl"]))
        counts["rows"] = len(rows)
        counts["declarations"] = len({r["decl"] for r in rows})
        counts["tagged"] = len(rows)
        return rows, counts, missing, rejects

    def _ledger_kinds(self, repo: Path) -> dict[str, str]:
        path = self._ledger_path(repo)
        if path is None:
            return {}
        ledger = json.loads(path.read_text(encoding="utf-8"))
        return {n["id"]: str(n.get("kind") or "")
                for n in ledger.get("formalization_nodes", []) if n.get("id")}

    # -- interface ---------------------------------------------------------
    def extract(self, repo: str | Path | None = None, *,
                sources: "Mapping[str, str] | None" = None) -> Iterator[dict]:
        rows, _counts, _missing, _rejects = self._rows(self.resolve_repo(repo),
                                                       self.bind(sources))
        yield from rows

    def dry_run(self, repo: str | Path | None = None, *,
                sources: "Mapping[str, str] | None" = None) -> dict:
        path = self.resolve_repo(repo)
        binding = self.bind(sources)
        rows, counts, missing, rejects = self._rows(path, binding)
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
            "uncurated_items": counts["items"] - len({r["item"] for r in rows}),
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
        return fp
