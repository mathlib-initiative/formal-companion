"""Run a `cf export` convention and write its Literature Links annotation files into a companion.

New code, implementing the `cf overlay export` row of section 6 of the overlay
contract (`docs/overlay.md`).  The exporters write a validated annotation
document; this module adds what they deliberately refuse to do, because
`cf export` writes nothing under the checkout it reads:

1. export to a temporary file OUTSIDE the companion and check the document
   with the vendored `formal_companion_annotations` checker;
2. convert it into Literature Links records, one file per annotation in one
   folder per book section and one folder per item with several, with folder
   defaults (`overlay.shards`, `overlay.defaults`), and check every file with
   the vendored `literature_links` scan;
3. only then record the files (their count and manifest sha256) in
   `pdf-overlay/GENERATED.json`, with the export, its commit and the sha256 of
   every curation input it read, so the companion's CI can tell a stale export
   without the skill, and replace the files in `<companion>/annotations/`.

A failed export, conversion or scan leaves the companion untouched.  The
writes follow rule 5: `annotations/` and `pdf-overlay/GENERATED.json`, through
`sync.annotations_dir` and `sync.overlay_dir`, and no git in the companion.
"""
from __future__ import annotations

import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

from cflib import log, vendored  # noqa: F401  (vendor/ on sys.path)
from cflib.errors import CfError
from cflib.paths import skill_root
from formal_companion_annotations import validate as V
from formal_companion_annotations import vocab

from overlay import config as overlay_config
from overlay import shards as overlay_shards
from overlay import sync as overlay_sync

__all__ = ["CONVENTIONS", "ExportResult", "export", "run_cf_export"]

#: The `cf export` subcommands that write an annotation document.  `site-pages`
#: writes a page manifest, which is an INPUT to one of these, not a document;
#: `tests/test_overlay_cli.py` pins this set against the live `cf export` tree.
CONVENTIONS = ("attribution-table", "docstring-tags")

#: What a failed export prints back, at most.
TAIL = 600


@dataclass
class ExportResult:
    """What one `cf overlay export` wrote."""

    directory: Path
    #: ``{section: number of files}``.
    sections: dict[str, int]
    deleted: list[str]
    counts: dict[str, int]


def run_cf_export(argv: Sequence[str]) -> subprocess.CompletedProcess:
    """Run `cf export …` in a child process of this checkout.

    A child, not an in-process call: the exporters read an untrusted checkout
    and the command is the user-facing one, so its exit status and its output
    are what this module reports, unchanged.
    """
    command = [sys.executable, str(skill_root() / "scripts" / "cf.py"), *argv]
    return subprocess.run(command, capture_output=True, text=True, timeout=3600)


def _configured_source(overlay: Path) -> str | None:
    path = overlay / overlay_config.CONFIG_NAME
    if not path.is_file():
        return None
    try:
        return overlay_config.load(path).source_id
    except CfError:
        return None


def _inputs(root: Path, report: dict, doc: dict) -> list[dict]:
    """``{path, sha256}`` of every curation input the export read, as the checkout has it now.

    The exporter's report names them (`counts.inputs`); a convention whose
    report does not falls back to its document's provenance inputs and graph.
    Only regular files inside the companion are recorded.
    """
    named = [row.get("path") for row in (report.get("counts") or {}).get("inputs") or []]
    if not named:
        named = [row.get("path") for p in doc.get("provenance") or [] for row in p.get("inputs") or []]
        named.append(((doc.get("companion") or {}).get("graph") or {}).get("path"))
    out = []
    for rel in sorted({p for p in named if isinstance(p, str)}):
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts:
            continue
        path = root.joinpath(*pure.parts)
        if path.is_file() and not path.is_symlink() and Path(os.path.realpath(path)).is_relative_to(root):
            out.append({"path": rel, "sha256": overlay_sync.digest(path.read_bytes())})
    return out


def _legacy_shards(overlay: Path) -> dict[str, str]:
    """``{name: sha256}`` of the ``<section>.jsonl`` shards a previous export recorded.

    The layout before one file per annotation: an export that finds them,
    unchanged, removes them like any file it no longer writes.
    """
    record = overlay_sync._previous(overlay) or {}                # noqa: SLF001 - same package
    block = record.get("annotations") if isinstance(record.get("annotations"), dict) else {}
    out = {}
    for row in block.get("shards") or []:
        path = PurePosixPath(str(row.get("path"))) if isinstance(row, dict) else None
        if path is not None and path.parent == PurePosixPath(overlay_sync.ANNOTATIONS_DIRNAME) \
                and path.suffix == ".jsonl" and isinstance(row.get("sha256"), str):
            out[path.name] = row["sha256"]
    return out


def _replace_files(directory: Path, files: dict[str, bytes], legacy: dict[str, str],
                   record: Callable[[], object]) -> list[str]:
    """Put exactly ``files`` in the directory; return what was removed.

    Everything already there must fit the layout (`overlay.shards.walk`), or
    be a legacy shard at the sha256 its export recorded: anything else is
    somebody's file, and the export stops before writing.  Stale files and
    the folders left empty go.  ``record`` runs before the first removal.
    """
    found, folders, odd = overlay_shards.walk(directory)
    stale: list[str] = []
    for relative in odd:
        path = directory / relative
        if "/" not in relative and path.is_file() and not path.is_symlink() \
                and legacy.get(relative) == overlay_sync.digest(path.read_bytes()):
            stale.append(relative)
            continue
        raise CfError(f"{directory.name}/{relative} is not an exported annotation file or folder",
                      hint=f"{directory.name}/ holds only the exported <section>/[<item>/]<name>.json files "
                           f"and their {overlay_shards.DEFAULTS}; move it elsewhere and re-run")
    stale += [relative for relative in found if relative not in files]
    record()
    # Stale files go first: on a case-insensitive filesystem a stale
    # `foo.json` and a new `Foo.json` are one directory entry.
    for relative in stale:
        directory.joinpath(*PurePosixPath(relative).parts).unlink()
    for relative in folders:
        path = directory.joinpath(*PurePosixPath(relative).parts)
        if not any(path.iterdir()):
            path.rmdir()
    directory.mkdir(exist_ok=True)
    for relative, raw in sorted(files.items()):
        path = directory.joinpath(*PurePosixPath(relative).parts)
        if not (path.is_file() and path.read_bytes() == raw):
            overlay_sync.write_file(path, raw)
    return stale


def export(companion: str | Path, *, convention: str, commit: str, source_id: str | None = None,
           extra: Sequence[str] = (),
           runner: Callable[[Sequence[str]], subprocess.CompletedProcess] = run_cf_export,
           ) -> ExportResult:
    """Write the companion's annotation files from `cf export <convention>` and record them."""
    if convention not in CONVENTIONS:
        raise CfError(f"{convention!r} is not a convention that writes a document",
                      hint="--convention " + " | ".join(CONVENTIONS))
    overlay = overlay_sync.overlay_dir(companion)
    directory = overlay_sync.annotations_dir(companion)
    if source_id is None:
        source_id = _configured_source(overlay)

    workspace = Path(tempfile.mkdtemp(prefix="cf-overlay-export-"))
    try:
        written, report_path = workspace / "document.json", workspace / "report.json"
        argv = ["export", convention, str(Path(companion).expanduser()), "--commit", commit,
                "--out", str(written), "--report", str(report_path), *extra]
        log.detail("running cf " + " ".join(argv))
        done = runner(argv)
        if done.returncode != 0:
            output = ((done.stdout or "") + (done.stderr or "")).strip()
            raise CfError(f"cf {' '.join(argv[:2])} exited {done.returncode}",
                          hint=output[-TAIL:] or "it said nothing")
        if not written.is_file():
            raise CfError(f"cf {' '.join(argv[:2])} wrote no document", hint=f"expected it at {written}")
        text = written.read_text(encoding="utf-8")
        try:
            doc = V.loads(text)
        except V.LoadError as exc:
            raise CfError(f"the exported document is not readable JSON: {exc}") from exc
        if not isinstance(doc, dict) or doc.get("schema") != vocab.ANNOTATION_SCHEMA:
            raise CfError(f"the exported document does not declare schema {vocab.ANNOTATION_SCHEMA}")
        problems = V.check(doc, text=text, canonical=True).problems
        if problems:
            raise CfError("the exported document does not validate, so nothing was written",
                          hint="; ".join(map(str, problems[:5])))
        try:
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            report = {}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    try:
        conversion = overlay_shards.convert(doc, source_id=source_id, directory=overlay_sync.ANNOTATIONS_DIRNAME)
        source = overlay_shards.pdf_source(doc, source_id)
    except ValueError as exc:
        raise CfError(f"the document cannot be shown by the overlay: {exc}") from exc
    if conversion.problems:
        raise CfError(f"{len(conversion.problems)} records could not be converted, so nothing was written",
                      hint="; ".join(conversion.problems[:5]))
    try:
        files = overlay_shards.file_bytes(conversion)
    except ValueError as exc:
        raise CfError("the annotation files would not read back as the records, so nothing was written",
                      hint=str(exc)) from exc
    scanned = overlay_shards.scan_problems(files)
    if scanned:
        raise CfError("the annotations do not pass the Literature Links scan, so nothing was written",
                      hint="; ".join(scanned[:5]))

    inputs = _inputs(Path(companion).expanduser().resolve(), report, doc)
    if not inputs:
        raise CfError("the export names no curation file it read, so nothing was written",
                      hint="GENERATED.json must record the inputs, or CI cannot tell a stale export")
    above = overlay_sync.defaults_above(directory)
    if above:
        raise CfError(above[0], hint="; ".join(above[1:]) or "nothing was written")
    wanted = {f"{overlay_sync.ANNOTATIONS_DIRNAME}/{path}": raw for path, raw in files.items()}
    block = {
        "count": len(conversion.files),
        "directory": overlay_sync.ANNOTATIONS_DIRNAME,
        "export": {"commit": (doc.get("companion") or {}).get("commit") or commit,
                   "convention": convention, "source_id": source["id"]},
        "extension": overlay_shards.NAMESPACE,
        "inputs": inputs,
        "manifest_sha256": overlay_sync.manifest_sha256(wanted),
    }
    # Recorded before the first removal or write: an export that dies leaves a record its files do not match.
    deleted = _replace_files(directory, files, _legacy_shards(overlay),
                             lambda: overlay_sync.record_annotations(companion, block))
    found, problems = overlay_sync.annotation_files(companion)
    if problems:                                             # pragma: no cover - scanned above
        raise CfError(problems[0])
    if found != wanted:
        wrong = sorted(path for path in found.keys() | wanted.keys() if found.get(path) != wanted.get(path))
        raise CfError(f"{len(wrong)} annotation files on disk are not the ones the export wrote and "
                      "GENERATED.json records; re-run it", hint="; ".join(wrong[:5]))
    sections = collections.Counter(PurePosixPath(path).parts[0] for path in conversion.files)
    return ExportResult(directory=directory, sections=dict(sorted(sections.items())),
                        deleted=sorted(deleted), counts=dict(conversion.counts))
