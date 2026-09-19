"""Corpus documents -> one row per (source, repo, item, kind), for scoring.

New code.  ``harvest/tally.py`` already reads the corpus, but it reads it for
COUNTING: a defect that names no declaration lands in its ``_unattributed``
bucket, which is the right answer for a tally and the wrong one for a score --
the mechanical COVERAGE row for a definition nobody tagged names no declaration
by construction (``detect/findings.py``: "``decls`` is empty for a wholly
missing item -- there is nothing to name"), and it is precisely the row an
answer key expects against that companion.

So this reader resolves the repo from the declarations when there are any and
from ``repos_involved`` when there are not, and otherwise agrees with the tally
exactly: the item goes through ``harvest.tally.collapse_to_book_item``, which is
``cflib.items.join_key`` for the labels that reach it, so a score and a tally of
the same corpus join on the same key.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping, Sequence

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:  # pragma: no cover - import side effect
    sys.path.insert(0, str(_SCRIPTS))

from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from harvest._compat import iter_finding_files  # noqa: E402
from harvest.tally import FINDING_LISTS, collapse_to_book_item  # noqa: E402


def kinds_for(kinds, source_id: str) -> Sequence[str] | None:
    """``kinds`` may be one list or ``{source_id: kinds}``; pick this source's."""
    if isinstance(kinds, Mapping):
        return kinds.get(source_id) or kinds.get("") or None
    return kinds


def _defects(payload: dict) -> list[dict]:
    """Every defect row in a findings document, whatever key it used."""
    out: list[dict] = []
    for key in FINDING_LISTS:
        value = payload.get(key)
        if isinstance(value, list):
            out.extend(d for d in value if isinstance(d, dict))
    return out


def _repos(defect: dict, document: dict) -> dict[str, list[str]]:
    """{repo: [decls]} for one defect: the declarations first, the claim second.

    A defect names its repos through ``unfaithful_decls`` (``ours:IsInterval``),
    which is what Stage 9 routes on.  A mechanical COVERAGE row has none -- the
    companion states nothing, so there is nothing to name -- and then
    ``repos_involved`` is the only record of whose gap it is.
    """
    out: dict[str, list[str]] = {}
    for decl in defect.get("unfaithful_decls") or []:
        text = str(decl).strip()
        if not text or ":" not in text:
            continue
        repo, _sep, _name = text.partition(":")
        out.setdefault(repo, []).append(text)
    if out:
        return out
    for repo in (defect.get("repos_involved") or document.get("repos_involved") or []):
        if str(repo).strip():
            out.setdefault(str(repo).strip(), [])
    return out


def rows_from_dir(directory: str | Path, *, run_id: str = "", kinds=None) -> list[dict]:
    """Every finding row in one corpus directory, one per (defect, repo)."""
    d = Path(directory)
    if not d.is_dir():
        raise CfError(f"no corpus directory at {d}",
                      hint="pass --run <run-id> for a run in this workspace, or --corpus <dir>")
    run = run_id or d.name
    rows: list[dict] = []
    for path in iter_finding_files(d):
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            # The same refusal harvest/tally.py makes: a findings file that
            # cannot be read makes every number a lower bound, and a score
            # would report the miss as a recall failure of the fleet.
            raise CfError(
                f"{path.name} is not a findings object (it is "
                f"{'null' if payload is None else type(payload).__name__})",
                hint=f"fix or re-run that task, then `cf score --run {run}` again")
        source_id = str(payload.get("source_id") or "")
        item_raw = str(payload.get("item") or path.stem)
        item = collapse_to_book_item(item_raw, kinds_for(kinds, source_id))
        for defect in _defects(payload):
            row_source = str(defect.get("source_id") or source_id)
            kind = str(defect.get("kind") or "").strip()
            by_repo = _repos(defect, payload)
            for repo, decls in sorted(by_repo.items()):
                rows.append({
                    "run": run,
                    "file": path.name,
                    "source_id": row_source,
                    "repo": repo,
                    "item": item,
                    "item_raw": item_raw,
                    "kind": kind,
                    "severity": defect.get("severity"),
                    "decls": sorted(decls),
                    "finding_id": str(defect.get("finding_id") or ""),
                    "concept": defect.get("concept"),
                })
    return rows


def rows_from_dirs(dirs: Mapping[str, str | Path], *, kinds=None) -> list[dict]:
    """Rows for several corpus directories; ``dirs`` maps run id -> directory."""
    out: list[dict] = []
    for run_id, directory in dirs.items():
        out.extend(rows_from_dir(directory, run_id=run_id, kinds=kinds))
    return out
