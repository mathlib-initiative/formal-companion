"""Run a `cf export` convention and write its Literature Links shards into a companion.

New code, implementing the `cf overlay export` row of section 6 of the overlay
contract (`docs/overlay.md`).  The exporters write a validated annotation
document; this module adds what they deliberately refuse to do, because
`cf export` writes nothing under the checkout it reads:

1. export to a temporary file OUTSIDE the companion and check the document
   with the vendored `formal_companion_annotations` checker;
2. convert it into Literature Links records, one shard per book section
   (`overlay.shards`), and check every shard with the vendored
   `literature_links` scan;
3. only then replace the shards in `<companion>/annotations/` and record them
   in `pdf-overlay/GENERATED.json`, with the export, its commit and the
   sha256 of every curation input it read, so the companion's CI can tell a
   stale export without the skill.

A failed export, conversion or scan leaves the companion untouched.  The
writes follow rule 5: `annotations/` and `pdf-overlay/GENERATED.json`, through
`sync.annotations_dir` and `sync.overlay_dir`, and no git in the companion.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
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
    shards: dict[str, int]
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


def _replace_shards(directory: Path, files: dict[str, bytes]) -> list[str]:
    """Put exactly ``files`` in the shard directory; return the stale shards removed.

    Everything already there must be a shard (a regular ``<name>.jsonl``):
    anything else is somebody's file, and the export stops before writing.
    """
    stale = []
    if directory.is_dir():
        for entry in sorted(os.scandir(directory), key=lambda e: e.name):
            mode = entry.stat(follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode) or not overlay_shards.SHARD_FILE.fullmatch(entry.name):
                raise CfError(f"{entry.path} is not a shard",
                              hint=f"{directory.name}/ holds only the exported <section>.jsonl shards; "
                                   "move it elsewhere and re-run")
            if entry.name not in files:
                stale.append(entry.name)
    directory.mkdir(exist_ok=True)
    for name, raw in sorted(files.items()):
        path = directory / name
        if not (path.is_file() and path.read_bytes() == raw):
            overlay_sync.write_file(path, raw)
    for name in stale:
        (directory / name).unlink()
    return stale


def export(companion: str | Path, *, convention: str, commit: str, source_id: str | None = None,
           extra: Sequence[str] = (),
           runner: Callable[[Sequence[str]], subprocess.CompletedProcess] = run_cf_export,
           ) -> ExportResult:
    """Write the companion's shards from `cf export <convention>` and record them."""
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
    files = overlay_shards.shard_bytes(conversion)
    scanned = overlay_shards.scan_problems(files)
    if scanned:
        raise CfError("the shards do not pass the Literature Links scan, so nothing was written",
                      hint="; ".join(scanned[:5]))

    inputs = _inputs(Path(companion).expanduser().resolve(), report, doc)
    if not inputs:
        raise CfError("the export names no curation file it read, so nothing was written",
                      hint="GENERATED.json must record the inputs, or CI cannot tell a stale export")
    deleted = _replace_shards(directory, files)
    entries, problems = overlay_sync.shard_entries(companion)
    if problems:                                             # pragma: no cover - scanned above
        raise CfError(problems[0])
    overlay_sync.record_annotations(companion, {
        "directory": overlay_sync.ANNOTATIONS_DIRNAME,
        "export": {"commit": (doc.get("companion") or {}).get("commit") or commit,
                   "convention": convention, "source_id": source["id"]},
        "extension": overlay_shards.NAMESPACE,
        "inputs": inputs,
        "shards": entries,
    })
    return ExportResult(directory=directory,
                        shards={name: raw.count(b"\n") for name, raw in files.items()},
                        deleted=deleted, counts=dict(conversion.counts))
