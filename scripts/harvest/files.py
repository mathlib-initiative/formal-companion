"""Which files of a corpus directory are findings documents, and how they are named.

New code, moved out of ``harvest/_compat.py`` when the listing became
recursive for locate (docs/proposals/wikipedia-locate.md section 3: a locate
run lands ``<task-id>/<source-id>.json``; references/locate-documents.md
section 10).  Standard library only, so every reader -- the receipt, the tally,
the score rows -- shares this one definition, and ``scripts/gate/
verify_findings.py`` carries a copy of :data:`NON_FINDING_FILES` that a test
keeps equal to it.
"""
from __future__ import annotations

import os
from pathlib import Path

#: files that live beside findings in a corpus directory and are not findings.
#: ``scripts/gate/verify_findings.py`` carries an identical literal (it runs on
#: a server with nothing on the path) -- keep the two in sync, or a re-gate of a
#: harvested corpus fails on the receipt this module wrote.
NON_FINDING_FILES = {"RECEIPT.json", "run.yaml", "run.json", "manifest.json",
                     "tally.json", "journal-harvest.json", "gate.json",
                     "gate.local.json", "queue_remaining.json"}


def is_finding_name(rel: str) -> bool:
    """Is this posix path, relative to a corpus directory, a findings document?

    ``*.json``, not one of :data:`NON_FINDING_FILES` (by basename, at any depth),
    and no path component hidden behind a dot.
    """
    parts = [p for p in str(rel).split("/") if p]
    return (bool(parts) and parts[-1].endswith(".json")
            and parts[-1] not in NON_FINDING_FILES
            and not any(p.startswith(".") for p in parts))


def finding_name(path: str | Path, directory: str | Path) -> str:
    """A findings file's name in a receipt: its posix path relative to the corpus."""
    return Path(path).relative_to(Path(directory)).as_posix()


def iter_finding_files(directory: str | Path) -> list[Path]:
    """Sorted findings in *directory* and below, excluding receipts and manifests.

    RECURSIVE, sorted by relative path, and regular files only (a symlink is
    left out).  A flat faithfulness corpus has no subdirectory holding JSON, so
    its listing is the one the flat version gave, in the same order.
    """
    d = Path(directory)
    if not d.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for here, dirs, files in os.walk(d):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for name in files:
            path = Path(here) / name
            rel = finding_name(path, d)
            # never a symlink: the remote listing (`find -type f`) leaves links
            # out, and following one here counted a document twice, or a file
            # from outside the corpus once
            if is_finding_name(rel) and path.is_file() and not path.is_symlink():
                found.append((rel, path))
    return [path for _rel, path in sorted(found)]
