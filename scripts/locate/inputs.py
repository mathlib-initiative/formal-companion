"""The planner's views of the other locate packages' artifacts, through their owners.

New code, reading references/locate-documents.md section 7 (sourcesets and
rendered web sources), section 8 (universes and ``corpus.sqlite``) and
section 10 (harvested documents) for the candidates, the planner, the
outcomes and the export.  Nothing here parses one of those formats itself:

* a sourceset manifest is ``sources.sourceset.Sourceset``'s (:func:`manifest`,
  :func:`pinned_manifest`), with its own validation, append-only chain (a
  version written as a ``cf-sourceset-delta/1`` delta is replayed onto the
  snapshot before it) and ``preferred()`` one-source-per-page rule; :func:`rendered` is the planner's
  one extra rule (a source is plannable once it has a body, a layer and a
  renderer);
* a rendered source's views are read through :class:`SourceView`, which takes
  the layer and formulas from ``sources.render_html.read_layer`` and refuses a
  missing view instead of reading it as empty;
* a universe is ``corpus.universe.Universe.load``'s, wrapped in the small
  :class:`Universe` view the planner passes around (its sha COMPUTED, and the
  recorded one refused when it differs);
* ``corpus.sqlite`` is opened with ``corpus.query.open_db`` and pinned to the
  universe with ``corpus.query.check_universe`` (:func:`open_corpus`): a run's
  identity is the universe sha the database was built for, never the file's
  bytes, which a rebuild changes;
* a harvested run is listed with ``harvest.files.iter_finding_files`` and its
  receipt read with ``harvest.harvest.read_receipt``.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cflib import hashing, vocab
from cflib.errors import CfError
from cflib.jsonio import read_json
from cflib.paths import WorkspacePaths

from corpus import query
from corpus.universe import Universe as CorpusUniverse
from harvest import files as harvest_files
from harvest.harvest import read_receipt
from sources import render_html
from sources.sourceset import Manifest, Sourceset

#: the database kinds a run's TOOLS database can be (references/locate-documents.md
#: section 9), defined in ``cflib.vocab`` (CLAUDE.md section 4: one vocabulary)
TOOLS_FULL = vocab.TOOLS_DB_FULL
TOOLS_BLIND = vocab.TOOLS_DB_BLIND
TOOLS_PLANTED = vocab.TOOLS_DB_PLANTED
TOOLS_KINDS = vocab.TOOLS_DB_KINDS


# --------------------------------------------------------------------------
# sourcesets (section 7)
# --------------------------------------------------------------------------

def manifest(paths: WorkspacePaths, sourceset_id: str, version: int | None = None) -> Manifest:
    """Version ``version`` of a sourceset (the newest by default), validated by its owner."""
    ss = Sourceset.open(paths, sourceset_id)
    return ss.latest() if version is None else ss.load(int(version))


def pinned_manifest(paths: WorkspacePaths, pin: dict) -> Manifest:
    """The manifest a run pinned (``{id, version, manifest_sha256}``), refused if rewritten."""
    return Sourceset.open(paths, str(pin["id"])).pinned(int(pin["version"]),
                                                        str(pin["manifest_sha256"]))


def rendered(entry: dict | None) -> bool:
    """Is a manifest entry plannable: a body, a layer and the renderer that made it?"""
    return bool(entry and entry.get("html_sha256") and entry.get("layer_sha256")
                and entry.get("renderer"))


def check_source(paths: WorkspacePaths, sourceset_id: str, entry: dict) -> None:
    """Refuse a source whose files on disk are not the ones its entry pins.

    A source an older renderer recorded cannot be re-rendered where it is (a
    recorded layer never changes, references/locate-documents.md section 7), so
    its refusal names the step that settles it -- a new sourceset seeded from
    this one's bytes -- instead of a render the sourceset would refuse.
    """
    problems = Sourceset(paths, sourceset_id).source_problems(entry)
    if problems:
        recorded = entry.get("renderer")
        if recorded and recorded != render_html.RENDERER:
            hint = (f"{entry['source_id']} was rendered by {recorded}, and a recorded layer "
                    f"never changes: render it under {render_html.RENDERER} in a new sourceset "
                    f"seeded from this one's bytes -- `cf sourceset create <new id> --from "
                    f"{sourceset_id}`, then `cf sourceset render <new id>`")
        else:
            hint = (f"re-render it: `cf sourceset render {sourceset_id} --source "
                    f"{entry['source_id']}`")
        raise CfError("; ".join(problems), hint=hint)


class SourceView:
    """The rendered views of one web source (section 7's table), each one required."""

    def __init__(self, directory: Path, source_id: str, sourceset_id: str = ""):
        self.dir = Path(directory)
        self.source_id = source_id
        self.sourceset_id = sourceset_id

    def _missing(self, name: str) -> CfError:
        hint = (f"`cf sourceset render {self.sourceset_id} --source {self.source_id}` restores "
                f"a view whose layer is unchanged") if self.sourceset_id else None
        return CfError(f"{self.source_id}: {name} is missing from {self.dir}", hint=hint)

    def json(self, name: str) -> Any:
        path = self.dir / name
        if not path.is_file():
            raise self._missing(name)
        return read_json(path)

    def layer_and_formulas(self) -> tuple[str, list[dict]]:
        for name in ("layer.txt", "formulas.json"):
            if not (self.dir / name).is_file():
                raise self._missing(name)
        return render_html.read_layer(self.dir)

    def layer(self) -> str:
        return self.layer_and_formulas()[0]

    def formulas(self) -> list[dict]:
        return list(self.layer_and_formulas()[1])

    def finders(self) -> list[dict]:
        return list(self.json("finders.json"))

    def paragraphs(self) -> list[dict]:
        return list(self.json("paragraphs.json"))

    def sections(self) -> list[dict]:
        return list(self.json("sections.json"))

    def layer_map(self) -> list[list[int]]:
        """``layer_map.json``'s segments (``sources.render_html.LayerMap``)."""
        if not (self.dir / "layer_map.json").is_file():
            raise self._missing("layer_map.json")
        return render_html.LayerMap.read(self.dir).segments


def source_view(paths: WorkspacePaths, sourceset_id: str, source_id: str) -> SourceView:
    return SourceView(paths.sourceset_source_dir(sourceset_id, source_id), source_id,
                      sourceset_id)


# --------------------------------------------------------------------------
# universes (section 8)
# --------------------------------------------------------------------------

@dataclass
class Universe:
    """The planner's view of a ``corpus.universe.Universe``: ids, commits, the computed sha."""

    id: str
    sha256: str
    libraries: dict[str, dict]
    excluded: list[dict]
    owner: CorpusUniverse

    def library(self, library_id: str) -> dict:
        try:
            return self.libraries[library_id]
        except KeyError:
            raise CfError(f"library {library_id!r} is not in universe {self.id}",
                          hint="libraries: " + ", ".join(sorted(self.libraries))) from None

    def commit(self, library_id: str) -> str:
        return str(self.library(library_id)["commit"])

    def pin(self) -> dict:
        return {"id": self.id, "sha256": self.sha256}

    def gate_libraries(self) -> dict:
        return {lid: {"repo": str(lib.get("repo") or ""), "commit": str(lib["commit"])}
                for lid, lib in sorted(self.libraries.items())}

    def mathlib_library(self) -> dict | None:
        return self.owner.mathlib_library()


def load_universe(paths: WorkspacePaths, universe_id: str) -> Universe:
    """``corpus.universe.Universe.load``, its recorded sha checked against the computed one."""
    path = paths.universe_yaml(universe_id)
    owner = CorpusUniverse.load(path)
    if owner.id != universe_id:
        raise CfError(f"{path} is universe {owner.id!r}, not {universe_id!r}")
    computed = owner.sha256()
    recorded = str(owner.data.get("universe_sha256") or "")
    if recorded != computed:
        raise CfError(f"{path}: universe_sha256 {recorded[:12] or '(none)'} is not the sha its "
                      f"libraries hash to ({computed[:12]})",
                      hint="a re-probe or an edited library changes the universe; write it "
                           "through `cf corpus` rather than editing the sha")
    if not owner.libraries:
        raise CfError(f"{path}: universe {universe_id} has no libraries")
    return Universe(id=owner.id, sha256=computed,
                    libraries={str(lib["id"]): dict(lib) for lib in owner.libraries},
                    excluded=owner.excluded, owner=owner)


# --------------------------------------------------------------------------
# corpus.sqlite (section 8)
# --------------------------------------------------------------------------

def open_corpus(path: Path, universe: Universe, *, blind: bool | None = None
                ) -> sqlite3.Connection:
    """A read-only connection to a database built for ``universe`` as it is now.

    ``corpus.query.check_universe``: the database's ``meta.universe_sha256`` must
    be the universe's computed sha (and, with ``blind`` given, the database must
    or must not be the G1-blind one).  The file's bytes are not compared -- a
    rebuild of an unchanged universe is the same database.
    """
    if not Path(path).is_file():
        raise CfError(f"no corpus database at {path}",
                      hint=f"build it with `cf corpus build-db {universe.id}"
                           + (" --blind`" if blind else "`"))
    conn = query.open_db(path)
    try:
        query.check_universe(conn, universe.owner, blind=blind)
    except CfError:
        conn.close()
        raise
    return conn


def database_record(path: Path, kind: str) -> dict:
    """``{path, sha256, kind}``: a database as a run records it."""
    return {"path": str(path), "sha256": hashing.sha256_file(path), "kind": kind}


def candidates_subdir(db: dict) -> str | None:
    """The candidates directory of a TOOLS database kind: None for the full one's own."""
    if db["kind"] == TOOLS_BLIND:
        return TOOLS_BLIND
    if db["kind"] == TOOLS_PLANTED:
        return f"{TOOLS_PLANTED}-{db['sha256'][:16]}"
    return None


def candidates_dir(paths: WorkspacePaths, sourceset_id: str, universe_id: str,
                   db: dict) -> Path:
    """Where the candidates retrieved from ``db`` live (references/locate-documents.md section 5)."""
    sub = candidates_subdir(db)
    return (paths.candidates_kind_dir(sourceset_id, universe_id, sub) if sub
            else paths.candidates_dir(sourceset_id, universe_id))


def open_tools(db: dict, universe: Universe) -> sqlite3.Connection:
    """Open a TOOLS database record, checked as its kind: only ``blind`` is the blind build."""
    return open_corpus(Path(db["path"]), universe, blind=db["kind"] == TOOLS_BLIND)


def tools_database(paths: WorkspacePaths, universe: Universe, *, blind: bool = False,
                   planted: Path | None = None) -> dict:
    """``{path, sha256, kind}`` of the database a run's workers search (section 9).

    ``full`` is ``corpora/<u>/corpus.sqlite`` itself; ``blind`` (the G1-blind
    arm) is ``corpora/<u>/corpus.blind.sqlite``, refused when it was never
    built; ``planted`` (the P tier) is a file the operator names, which must be
    a full (not blind) database built for this universe.  Each is opened and
    pinned to the universe before it is recorded.
    """
    if blind and planted:
        raise CfError("--blind and --tools-corpus-db are two different arms",
                      hint="the G1-blind arm searches corpus.blind.sqlite; the P tier searches a "
                           "planted database")
    if planted:
        path, kind = Path(planted).resolve(), TOOLS_PLANTED
        if not path.is_file():
            raise CfError(f"no planted database at {planted}",
                          hint="`cf bench freeze --plant` writes one")
    elif blind:
        path, kind = paths.corpus_db_blind(universe.id), TOOLS_BLIND
        if not path.is_file():
            raise CfError(f"universe {universe.id} has no G1-blind database at {path}",
                          hint=f"build it with `cf corpus build-db {universe.id} --blind`")
    else:
        path, kind = paths.corpus_db(universe.id), TOOLS_FULL
    record = {"path": str(path), "kind": kind}
    open_tools(record, universe).close()
    return database_record(path, kind)


# --------------------------------------------------------------------------
# harvested runs (section 10)
# --------------------------------------------------------------------------

def harvested_documents(run_dir: Path) -> list[tuple[str, dict]]:
    """``[(relative path, document)]`` of a harvested run: ``<task-id>/<source-id>.json``.

    The listing is ``harvest.files.iter_finding_files``' -- the receipt's own --
    so the tally and the receipt agree on which files are documents.  A document
    that is not a JSON object is refused by name, as the receipt marks it
    malformed.
    """
    root = Path(run_dir)
    out: list[tuple[str, dict]] = []
    for path in harvest_files.iter_finding_files(root):
        rel = harvest_files.finding_name(path, root)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CfError(f"{rel} in {root} is not readable JSON ({exc})") from None
        if not isinstance(doc, dict):
            raise CfError(f"{rel} in {root} is not a JSON object")
        out.append((rel, doc))
    return out


def receipt(run_dir: Path) -> dict | None:
    got = read_receipt(Path(run_dir))
    return got if isinstance(got, dict) else None


def require_gated(run_dir: Path, run_id: str, what: str) -> dict:
    """The receipt of a harvested run that is complete and passed its gate, or refuse."""
    got = receipt(run_dir)
    if got is None:
        raise CfError(f"{what} {run_id!r} has no RECEIPT.json under {run_dir}",
                      hint=f"harvest it first: cf harvest {run_id}")
    if not got.get("complete"):
        raise CfError(f"{what} {run_id!r}: the harvest receipt is not complete",
                      hint=f"re-run the missing tasks, then cf harvest {run_id} again")
    gate = got.get("gate")
    if not isinstance(gate, dict) or not gate.get("ok"):
        raise CfError(f"{what} {run_id!r} was not gated, or its gate failed",
                      hint="a document is frozen only once the gate passed it")
    return got
