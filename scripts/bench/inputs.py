"""Read what the other locate packages write -- through the packages that own it.

New code.  The bench reads four producers' artifacts (references/locate-documents.md
sections 7 to 10) and re-implements none of their readers, so a format has one
definition:

* a sourceset: ``sources.sourceset.Sourceset`` (its manifests, append-only and
  chained) and the rendered files of a source directory;
* a universe: ``corpus.universe.Universe`` and ``corpus.sqlite`` through
  ``corpus.query`` -- opened with ``open_db`` (read-only, immutable, the path
  quoted) and checked with ``check_universe``, so gold is never labelled against
  a database that describes another state of the universe it stamps;
* a harvested locate run: ``locate.outcomes`` (``run_yaml``, ``task_manifests``,
  the frozen ``inventory``), ``locate.inputs`` (``harvested_documents`` and the
  gated-receipt rule ``require_gated``), and the receipt bytes, whose sha the
  score pins;
* the host artifacts ``cf harvest`` brings home under ``runs/<run>/host/``: the
  gate log and the canary scan.

Everything here only reads.  A file in the wrong shape is a ``CfError`` naming it,
never a silently empty answer -- an empty answer would score as a run that found
nothing.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from cflib.errors import CfError
from cflib.jsonio import read_json
from cflib.paths import WorkspacePaths

from corpus import query
from corpus.universe import Universe
from locate import inputs as locate_inputs
from locate import outcomes as locate_outcomes
from sources.sourceset import Manifest, Sourceset

from .layout import read_jsonl_rows

#: the host artifacts ``cf harvest`` fetches into ``runs/<run>/host/``
#: (references/locate-documents.md section 9)
HOST_GATE_LOG = "gate.jsonl"
HOST_CANARY = "canary.json"


# --------------------------------------------------------------------------
# sourcesets
# --------------------------------------------------------------------------

def load_manifest(paths: WorkspacePaths, sourceset: str, version: int | None = None) -> Manifest:
    """The pinned (or the latest) manifest of a sourceset, read by its owner."""
    ss = Sourceset.open(paths, sourceset)
    return ss.latest() if version is None else ss.load(version)


def pinned_manifest(paths: WorkspacePaths, pin: dict) -> Manifest:
    """The manifest a bench pinned, refused when its bytes are not the pinned ones."""
    return Sourceset.open(paths, str(pin["id"])).pinned(int(pin["version"]),
                                                        str(pin["manifest_sha256"]))


def source_dir(paths: WorkspacePaths, sourceset: str, source_id: str) -> Path:
    return paths.sourceset_source_dir(sourceset, source_id)


def read_layer(paths: WorkspacePaths, sourceset: str, source_id: str,
               layer_sha256: str | None = None) -> str:
    """``layer.txt``, refused when it is not the pinned layer."""
    path = source_dir(paths, sourceset, source_id) / "layer.txt"
    if not path.is_file():
        raise CfError(f"{source_id}: no layer.txt at {path}",
                      hint="render the source first (the sourceset's render step)")
    raw = path.read_bytes()
    if layer_sha256 and hashlib.sha256(raw).hexdigest() != layer_sha256:
        raise CfError(f"{source_id}: layer.txt is not the pinned layer {layer_sha256[:12]}…",
                      hint="the bench pins the sourceset manifest; re-render or re-pin")
    return raw.decode("utf-8")


def read_source_json(paths: WorkspacePaths, sourceset: str, source_id: str, name: str,
                     required: bool = True) -> Any:
    path = source_dir(paths, sourceset, source_id) / name
    if not path.is_file():
        if required:
            raise CfError(f"{source_id}: no {name} at {path}",
                          hint="render the source first (the sourceset's render step)")
        return None
    return read_json(path)


# --------------------------------------------------------------------------
# universes and corpus.sqlite
# --------------------------------------------------------------------------

def load_universe(paths: WorkspacePaths, universe_id: str) -> Universe:
    """``corpus.universe.Universe.load``: the universe's one validating reader."""
    return Universe.load(paths.universe_yaml(universe_id))


def libraries(universe: Universe) -> dict[str, dict]:
    """``{library id: row}`` of a loaded universe."""
    return {str(lib["id"]): lib for lib in universe.libraries}


def universe_pin(universe: Universe) -> dict:
    """What gold stamps: the id and the RECOMPUTED sha, never the recorded one."""
    return {"id": universe.id, "sha256": universe.sha256()}


def open_checked(paths: WorkspacePaths, universe: Universe,
                 db: Path | None = None) -> sqlite3.Connection:
    """``corpus.sqlite`` of ``universe``, refused unless it describes the universe as it is now.

    The universe must record a built database (``corpus_db``; an ingest after
    the build clears it), and the database's ``meta.universe_sha256`` must be the
    universe's recomputed sha (``corpus.query.check_universe``).  A database
    built for another state of the universe would resolve gold against
    declarations the stamped universe does not hold.
    """
    if universe.corpus_db is None:
        raise CfError(f"universe {universe.id} records no built corpus.sqlite (an index was "
                      f"ingested after the last build)",
                      hint=f"`cf corpus build-db {universe.id}` rebuilds it")
    conn = query.open_db(db or paths.corpus_db(universe.id))
    try:
        query.check_universe(conn, universe, blind=False)
    except CfError:
        conn.close()
        raise
    return conn


def declaration(conn: sqlite3.Connection, library: str, commit: str, name: str) -> dict | None:
    """The exact-table row of one declaration at a commit (``corpus.query.declaration``), or None."""
    return query.declaration(conn, library, name, commit=commit)


def tagged(conn: sqlite3.Connection, tag: str, value: str, library_ids) -> list[dict]:
    """``corpus.query.tagged`` rows of these libraries."""
    wanted = set(library_ids)
    return [row for row in query.tagged(conn, tag, value) if row["library"] in wanted]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# runs (the planner's and the harvest's formats, through their owners)
# --------------------------------------------------------------------------

def run_yaml(paths: WorkspacePaths, run_id: str) -> dict:
    """A locate run's ``run.yaml`` (``locate.outcomes.run_yaml``: it must hold a locate block)."""
    return locate_outcomes.run_yaml(paths, run_id)


def receipt(paths: WorkspacePaths, run_id: str) -> tuple[dict, str]:
    """``(RECEIPT.json, its sha256)`` of a harvested run that is complete and passed its gate.

    The gate rule is the planner's (``locate.inputs.require_gated``): the export
    and plan-locate refuse an ungated run, and so does the bench.
    """
    got = locate_inputs.require_gated(paths.corpus_run_dir(run_id), run_id, "run")
    return got, sha256_file(paths.receipt_json(run_id))


def outcomes(paths: WorkspacePaths, run_id: str) -> list[dict]:
    """``reports/locate-outcomes.<run>.jsonl``: one row per passage after challenge."""
    path = paths.locate_outcomes_jsonl(run_id)
    if not path.is_file():
        raise CfError(f"run {run_id!r} has no passage outcomes at {path}",
                      hint="harvest the run and write its passage outcomes first (the locate tally)")
    return read_jsonl_rows(path)


def tally(paths: WorkspacePaths, run_id: str) -> dict:
    """``reports/locate-tally.<run>.json``: which challenge runs the outcomes were built with."""
    path = paths.locate_tally_json(run_id)
    if not path.is_file():
        raise CfError(f"run {run_id!r} has no locate tally at {path}",
                      hint="the outcome rows and the tally are written together by the locate tally")
    doc = read_json(path)
    if not isinstance(doc, dict):
        raise CfError(f"{path} is not a JSON object")
    return doc


def inventory(paths: WorkspacePaths, run_id: str) -> dict:
    """The frozen inventory of a gated inventory run (``locate.outcomes.inventory``)."""
    return locate_outcomes.inventory(paths, run_id, require_gate=True)


def locate_documents(paths: WorkspacePaths, run_id: str) -> list[tuple[str, dict]]:
    """``[(relative path, document)]`` of a harvested run (``locate.inputs.harvested_documents``)."""
    return locate_inputs.harvested_documents(paths.corpus_run_dir(run_id))


def task_manifests(paths: WorkspacePaths, run_id: str) -> list[dict]:
    """The task manifests a run was planned with (``locate.outcomes.task_manifests``)."""
    return locate_outcomes.task_manifests(paths, run_id)


# --------------------------------------------------------------------------
# what cf harvest brings home from the host
# --------------------------------------------------------------------------

def run_host_dir(paths: WorkspacePaths, run_id: str) -> Path:
    """``runs/<run>/host/``: where ``cf harvest`` lands the host's artifacts."""
    accessor = getattr(paths, "run_host_dir", None)
    return Path(accessor(run_id)) if callable(accessor) else paths.run_dir(run_id) / "host"


def gate_log(path: Path) -> list[dict]:
    """Every gate call recorded in a ``gate.jsonl`` (or every one under a directory)."""
    path = Path(path)
    files = [path] if path.is_file() else sorted(path.rglob("*.jsonl")) if path.is_dir() else []
    if not files:
        raise CfError(f"no gate log at {path}",
                      hint="cf harvest fetches the host's gate log into runs/<run>/host/gate.jsonl")
    rows: list[dict] = []
    for file in files:
        rows.extend(read_jsonl_rows(file))
    return rows


def canary_report(path: Path) -> dict:
    """A host-side canary scan: ``{"scanned": <files>, "hits": [<file names>]}``."""
    doc = read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("hits", []), list):
        raise CfError(f"{path} is not a canary scan ({{scanned, hits}})")
    return doc
