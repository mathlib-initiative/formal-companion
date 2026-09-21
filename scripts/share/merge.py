"""Take another workspace's ledger rows into this one, without losing a verdict.

New code.  Two people auditing one companion produce two ledgers, and until now
nothing in the skill could put them together: the workspace was a repo with no
remote and there was no merge, import-ledger or sync command at all.  The rule
this module implements is deliberately dull, because a merge that guesses is
worse than no merge:

* **same finding, same verdict** -- nothing happens.  Not "the newer one wins":
  the two rows say the same thing, so the file does not change and a second
  import of the same bundle is a no-op (which is how idempotence is tested).
* **same finding, different verdicts** -- BOTH rows are kept and both are marked
  ``conflict: true`` with ``conflict_verdicts`` naming the two.  A disagreement
  between two adjudicators is the most interesting row in the file; overwriting
  one of them, in either direction, would destroy the only evidence that the
  question is contested.
* **the same source id with a different sha256** -- the import is REFUSED, naming
  both shas.  The rows were judged against another edition's pagination and
  wording (batch 1's anchor findings were judged against the 2011 draft), so
  merging them would silently mix two books (CLAUDE.md S4, "stamp the source").

Rows are joined by ``finding_id`` first and by the run-independent DEFECT KEY
second, because ``run_id`` is the first part of a finding id: the same defect
found by two people's runs has two ids and only the defect key links them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

from cflib import log
from cflib.errors import CfError
from cflib.jsonio import read_jsonl, write_jsonl
from harvest.tally import defect_key  # the run-independent key; see the module note
from ledger import compat

from . import bundle as bundle_mod

#: what an imported row records about where it came from.  Both values are
#: deterministic (a name and a content digest, never a timestamp), so importing
#: the same bundle twice produces byte-identical rows -- which is what makes the
#: second import a no-op rather than a duplicate.
SHARED_FROM = "shared_from"
SHARED_BUNDLE = "shared_bundle"


def row_defect_key(row: dict) -> str:
    """The run-independent key for a ledger row: (source, item, kind, decls)."""
    return defect_key(str(row.get("source_id") or ""), str(row.get("item") or ""),
                      str(row.get("kind") or ""),
                      [str(d) for d in (row.get("unfaithful_decls") or []) if d])


def _data_rows(path: Path) -> tuple[dict | None, list[dict]]:
    """``(meta, rows)`` for a ledger .jsonl, meta being its ``_meta`` row if any."""
    raw = read_jsonl(path) if Path(path).exists() else []
    meta = next((r["_meta"] for r in raw if isinstance(r, dict) and "_meta" in r), None)
    return meta, [r for r in raw if not (isinstance(r, dict) and "_meta" in r)]


def check_sources(workspace: Any, rows: Iterable[dict], manifest: dict) -> dict[str, str]:
    """Every source the incoming rows cite must be THIS workspace's edition.

    Returns ``{source_id: sha256}`` for the sources actually cited.  Refuses on a
    sha mismatch and on a source this workspace does not pin at all: a row whose
    edition cannot be checked is exactly the row the source gate exists to stop,
    and accepting it would put two editions' pagination in one ledger.
    """
    declared = {str(k): str(v) for k, v in (manifest.get("sources") or {}).items()}
    known = workspace.sources
    cited: dict[str, str] = {}
    for row in rows:
        source_id = str(row.get("source_id") or "")
        if not source_id:
            raise CfError(
                f"a shared ledger row for {row.get('item') or '(no item)'} carries no source_id",
                hint="every row is stamped with the edition it was judged against; re-export "
                     "the bundle from a workspace whose ledger validates (`cf adjudicate "
                     "--validate`)")
        sha = str(row.get("source_sha") or declared.get(source_id) or "")
        if not sha:
            raise CfError(
                f"a shared ledger row for {row.get('item') or '(no item)'} does not say which "
                f"edition of {source_id} it was judged against",
                hint="source_id AND source_sha are stamped on every artifact that judges a "
                     "source (CLAUDE.md S4); re-export the bundle from a workspace whose "
                     "ledger validates (`cf adjudicate --validate`)")
        if source_id not in known:
            raise CfError(
                f"the bundle's rows cite source {source_id!r}, which this workspace does not "
                "have",
                hint=f"prepare the same edition first: `cf onboard <repo>` or `cf sources add "
                     f"<source>` (the bundle says its sha256 is {sha or 'unrecorded'})")
        mine = str(known[source_id].sha256 or "")
        if sha and mine and sha != mine:
            raise CfError(
                f"refusing to merge: source {source_id} is sha256 {sha} in the bundle and "
                f"{mine} in this workspace",
                hint="those are two editions of the same work; the rows were judged against "
                     "the bundle's pagination and wording. Pin the same edition (`cf sources "
                     "set %s sha256=...` after preparing it) or keep the campaigns apart"
                     % source_id)
        cited[source_id] = mine or sha
    return cited


def _conflict_marks(row: dict, verdicts: Sequence[str]) -> dict:
    """*row* marked as contested by the verdicts in *verdicts* (idempotent)."""
    out = dict(row)
    out["conflict"] = True
    out["conflict_verdicts"] = sorted({str(v) for v in verdicts if v})
    return out


def merge_rows(existing: list[dict], incoming: list[dict], *, origin: str,
               bundle_id: str) -> dict:
    """Merge *incoming* into *existing*; returns the new list and what happened.

    ``existing`` is not reordered and no row in it is replaced by an incoming one:
    the only change a merge may make to a row already here is to mark it
    ``conflict``.  New rows are appended in the bundle's own order, so the result
    is a function of (existing file, bundle) and a re-import changes nothing.
    """
    out = [dict(r) for r in existing]
    added: list[dict] = []
    unchanged = 0
    conflicts: list[dict] = []

    def matches(row: dict) -> list[int]:
        fid = str(row.get("finding_id") or "")
        by_id = [i for i, r in enumerate(out) if str(r.get("finding_id") or "") == fid]
        if by_id:
            return by_id
        key = row_defect_key(row)
        return [i for i, r in enumerate(out) if row_defect_key(r) == key]

    for row in incoming:
        row = dict(row)
        row[SHARED_FROM] = origin
        row[SHARED_BUNDLE] = bundle_id
        verdict = str(row.get("verdict") or "")
        found = matches(row)
        same = [i for i in found if str(out[i].get("verdict") or "") == verdict]
        if same:
            unchanged += 1
            continue
        if found:
            verdicts = sorted({verdict} | {str(out[i].get("verdict") or "") for i in found})
            for i in found:
                out[i] = _conflict_marks(out[i], verdicts)
            row = _conflict_marks(row, verdicts)
            conflicts.append({
                "finding_id": str(row.get("finding_id") or ""),
                "item": str(row.get("item") or ""),
                "verdicts": verdicts,
                "joined_by": ("finding_id" if any(
                    str(out[i].get("finding_id") or "") == str(row.get("finding_id") or "")
                    for i in found) else "defect_key"),
            })
        out.append(row)
        added.append(row)
    return {"rows": out, "added": len(added), "unchanged": unchanged, "conflicts": conflicts}


def _check_verdicts(rows: Iterable[dict], where: str) -> None:
    """Refuse a shared row whose verdict is not in the ledger vocabulary."""
    legal = set(compat.OPEN_VERDICTS) | set(compat.CLOSED_VERDICTS)
    for row in rows:
        verdict = str(row.get("verdict") or "")
        if verdict not in legal:
            raise CfError(
                f"{where}: verdict {verdict or '(empty)'} is not one this skill knows",
                hint="legal verdicts are " + ", ".join(sorted(legal)) +
                     " (references/vocabulary.md); the bundle was written against another "
                     "vocabulary version")
        if not str(row.get("finding_id") or ""):
            raise CfError(
                f"{where}: a row carries no finding_id, so nothing can be joined to it",
                hint="re-export the bundle from a workspace whose ledger validates "
                     "(`cf adjudicate --validate`)")


def merge_bundle(workspace: Any, bundle: Path, manifest: dict, *,
                 dry_run: bool = False) -> dict:
    """Merge every ledger file of *bundle* into *workspace*.  Returns the report."""
    bundle = Path(bundle)
    origin = str(manifest.get("workspace") or "(unnamed workspace)")
    bundle_id = str(manifest.get("bundle_id") or "")[:12]
    paths = workspace.paths
    files = bundle_mod.bundle_ledger_files(bundle)
    if not files:
        raise CfError(f"{bundle} carries no ledger rows",
                      hint="a bundle with nothing to merge is still worth reading: "
                           "`cf quotes hydrate --bundle <bundle>` reads its findings")
    report = {"bundle_id": bundle_id, "workspace": origin, "added": 0, "unchanged": 0,
              "conflicts": [], "files": [], "sources": {}, "dry_run": bool(dry_run)}
    all_incoming: list[dict] = []
    per_file: list[tuple[Path, str, list[dict]]] = []
    for path in files:
        _, rows = _data_rows(path)
        _check_verdicts(rows, f"{path.name}")
        companion = bundle_mod.companion_of(path)
        if not companion:
            raise CfError(f"{path.name} does not name a companion",
                          hint="a bundle's ledger files are accepted.<companion>.jsonl and "
                               "refuted.<companion>.jsonl")
        per_file.append((path, companion, rows))
        all_incoming.extend(rows)
    report["sources"] = check_sources(workspace, all_incoming, manifest)

    known = set(workspace.companion_names())
    for path, companion, rows in per_file:
        if companion not in known:
            log.warn(f"{companion} is not a companion of this workspace: its rows are merged "
                     f"into ledger/{path.name}, and `cf adjudicate --validate` will report "
                     f"them until `cf onboard` adds the repo")
        dest = (paths.accepted_jsonl(companion) if path.name.startswith("accepted.")
                else paths.refuted_jsonl(companion))
        meta, existing = _data_rows(dest)
        merged = merge_rows(existing, rows, origin=origin, bundle_id=bundle_id)
        report["added"] += merged["added"]
        report["unchanged"] += merged["unchanged"]
        for conflict in merged["conflicts"]:
            report["conflicts"].append(dict(conflict, companion=companion, file=dest.name))
        report["files"].append({
            "file": str(dest), "companion": companion, "added": merged["added"],
            "unchanged": merged["unchanged"], "conflicts": len(merged["conflicts"]),
            "rows": len(merged["rows"]),
        })
        # Nothing added means nothing changed: a conflict always appends the row it
        # conflicts with, so there is no case where only the marks moved.
        if not dry_run and merged["added"]:
            write_jsonl(dest, merged["rows"], meta=meta)
    return report
