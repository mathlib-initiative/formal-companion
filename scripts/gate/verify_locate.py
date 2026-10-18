#!/usr/bin/env python3
"""The out-of-tree locate gate: rules 1-8 over one task's own documents.

New code, implementing docs/proposals/wikipedia-locate.md section 4 (gates,
locate mode) over the formats of references/locate-documents.md sections 1-4
and 7-9.  It is the locate counterpart of ``verify_findings.py`` and follows
its contract:

* **out of tree** -- copied into ``<tend-root>/gate/`` with ``gate.json``, the
  task manifests (``gate/tasks/<task-id>.json``), ``locate_text.py``,
  ``verify_findings.py`` (whose hardened changed-file reader it reuses) and
  ``vendor/``, and invoked by absolute path, never from the worktree a worker
  can edit;
* **stdlib only, no cflib** -- the run host has neither; the vocabulary it
  needs is inlined below and ``tests/test_verify_locate.py`` pins it to
  ``cflib.vocab``;
* **the exit vocabulary** -- 0 every document validated; 1 REJECTED, fix the
  documents; 3 the gate could not run (no or unreadable gate.json, a vendored
  package that is not the pinned release, a layer, formula list or paragraph
  table missing or not the pinned bytes, an unreadable corpus.sqlite, a
  missing task-manifest directory, a change set git cannot read safely).
  Every infrastructure failure arrives as 3, never as the interpreter's 1 out
  of a traceback, because the worker's prompt says 1 means "your documents
  are wrong".

**Only the task's own documents.**  The v1 gate validated every findings file
of the root on every call, which made it O(N) per call.  This one validates
the documents this contribution changed (``--changed-files``) or the files
named with ``--findings``, and loads only the sources, candidates files and
task manifests those documents name.  Without either, it validates
``<root>/<findings-dir>/*/*.json`` (a harvested corpus: ``--findings-dir .``).

**The rules** (the proposal's section 4; the precise forms are written in
references/locate-documents.md sections 2-4 and 9):

1. the schema of the document for its mode, with the header pins; every
   record the document implies passes the vendored ``literature_links.check``;
   both vendored packages match their manifests and the manifests the shas in
   ``gate.json``;
2. every quote matches exactly once on the source's layer, at its passage's
   span start and inside the span, and its ``In-text #<hex>`` is the hash of
   that start's head (an inventory's ids recomputed over all its passages; a
   locate document's passages are exactly the task's frozen ones); the
   document's ``source_sha``, ``layer_sha256`` and ``renderer`` are the pinned
   source's, and ``layer.txt`` hashes to the pin;
3. every declaration a document names as found exists in ``corpus.sqlite``'s
   exact table at that library and the universe's commit; ``proof_status``,
   ``commit`` and ``version`` are refused wherever a worker writes them;
4. with L2 curated hits for the article (read here, from the candidates file,
   never from the document), no passage ends absent, no ``subject`` passage is
   scoped out and an inventory keeps a ``formal_math`` passage, unless every
   hit is located or rejected with a reason -- off in the blind arm;
5. an absence lists at least three distinct queries, and the document names
   the frozen universe;
6. at most five locations per passage with distinct ranks 1..5;
7. the changed files are the task's own: ``findings/<task-id>/<source-id>.json``
   for the sources its manifest names and ``tasks/<task-id>.yaml``; the task
   id comes from ``--task-id``/``TEND_TASK_ID``, else from tend's
   ``orchestrator.sqlite`` (an ASSUMED schema, looked up defensively; verify it
   on the host), else the single-stem rule -- and the last line says which;
8. an inventory drops no deterministic-finder passage of its task without a
   skip covering its span, and every ``math`` paragraph is covered by a
   passage or a skip.

One JSON line per call is appended to ``gate.json``'s ``log_dir`` when that is
writable (the watchdog and the bench read it); a log that cannot be written
never fails the gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
from typing import Any, Iterable

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
#: the vendored packages: beside this file in a gate directory, else the checkout's
VENDOR = HERE / "vendor" if (HERE / "vendor").is_dir() else HERE.parent.parent / "vendor"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import locate_text as lt  # noqa: E402
import verify_findings as vf  # noqa: E402

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_GATE_BROKEN = 3

GATE_SCHEMA = "cf-gate-locate/1"
DOCUMENT_SCHEMA = "cf-locate/1"

# --------------------------------------------------------------------------- #
# the vocabulary, inlined from cflib.vocab (no cflib on the host).  Keep in
# step; tests/test_verify_locate.py asserts every set equals cflib's.
# --------------------------------------------------------------------------- #
LOCATE_INVENTORY = "locate-inventory"
LOCATE = "locate"
LOCATE_CHALLENGE = "locate-challenge"
LOCATE_MODES = frozenset({LOCATE_INVENTORY, LOCATE, LOCATE_CHALLENGE})
KINDS_BY_MODE = {
    LOCATE_INVENTORY: frozenset({"PASSAGE", "UNRESOLVED"}),
    LOCATE: frozenset({"LOCATION", "ABSENCE", "UNRESOLVED"}),
    LOCATE_CHALLENGE: frozenset({"LOCATION", "ABSENCE", "UNRESOLVED"}),
}
PASSAGE_KINDS = frozenset({"definition", "theorem", "conjecture", "example", "construction",
                           "notation", "other"})
PROMINENCE = frozenset({"subject", "central", "supporting"})
SCOPES = frozenset({"formal_math", "expository", "non_math"})
LOCATED_SCOPES = frozenset({"formal_math"})
LOCATE_RELATIONS = frozenset({"exactMatch", "closeMatch", "broadMatch", "narrowMatch",
                              "hasPart", "relatedMatch"})
RELATED_MATCH = "relatedMatch"
CLOSE_MATCH = "closeMatch"
NO_MATCH = "noMatch"
LOCATE_OUTCOMES = frozenset({"located", "absent", "unresolved"})
FINDERS = frozenset({"theorem_box", "bold_definition", "lead", "model"})
DETERMINISTIC_FINDERS = frozenset({"theorem_box", "bold_definition", "lead"})
CONFIDENCES = frozenset({"high", "medium", "low"})
CANDIDATE_VERDICTS = frozenset({"accepted", "rejected"})
CHALLENGE_TARGETS = frozenset({"location", "absence", "unresolved", "scope", "prominence"})
CHALLENGE_ACTIONS = frozenset({"confirm", "uphold", "downgrade", "refute", "overturn"})
#: which actions a target admits (references/locate-documents.md section 4)
CHALLENGE_COMBOS = {
    "location": frozenset({"confirm", "downgrade", "refute"}),
    "absence": frozenset({"uphold", "overturn"}),
    "unresolved": frozenset({"uphold", "overturn"}),
    "scope": frozenset({"uphold", "overturn"}),
    "prominence": frozenset({"uphold", "overturn"}),
}
UNRESOLVED_BY = frozenset({"worker", "attempt_cap"})
MAX_LOCATIONS_PER_PASSAGE = 5
MIN_ABSENCE_QUERIES = 3
LOCATE_EXTENSION = "org.mathlib-initiative.formal-companion.locate"
EXPORT_QUOTE_WORDS = 12

#: the one spelling of a passage item a stage writes (``items.passage_item``)
ITEM = re.compile(r"In-text #[0-9a-f]{8}\Z")
QID = re.compile(r"Q[1-9][0-9]*\Z")
#: keys no worker document may carry anywhere: the proof status is joined from
#: the index at export, and the commit is the universe's
FORBIDDEN_KEYS = ("proof_status", "commit", "version")
NOTES_MAX = 2000
#: defaults gate.json may override
MAX_QUOTE_CHARS = 1500
MAX_CONTEXT_CHARS = 200
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
#: tend's validation runs this from the worktree; a harvested corpus keeps the
#: task directories at its top
DEFAULT_FINDINGS_DIR = "findings"
NON_DOCUMENT_FILES = frozenset(vf.NON_FINDING_FILES)
VENDORED_PACKAGES = ("literature_links", "formal_companion_annotations")


class GateBroken(Exception):
    """The gate cannot run: an infrastructure failure, never a verdict on the documents."""


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #

def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def load_json_strict(text: str) -> Any:
    """JSON with no duplicate key and no NaN/Infinity: a value two readers read one way."""
    return json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_no_constant)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_name(value: Any) -> bool:
    """An id that names one file inside a directory and nothing else."""
    return (isinstance(value, str) and bool(value.strip()) and "/" not in value
            and "\\" not in value and "\x00" not in value and value not in (".", "..")
            and not value.startswith("."))


class GateConfig:
    """``gate.json`` for a locate run (``cf-gate-locate/1``), validated on load."""

    def __init__(self, data: Any, path: pathlib.Path | None = None):
        if not isinstance(data, dict):
            raise GateBroken("gate.json is not a JSON object")
        if data.get("schema") != GATE_SCHEMA:
            raise GateBroken(f"gate.json schema is {data.get('schema')!r}, not {GATE_SCHEMA!r} "
                             f"(this is the locate gate; a faithfulness run uses verify_findings.py)")
        self.data = data
        self.path = path
        self.mode = str(data.get("mode") or "")
        if self.mode not in LOCATE_MODES:
            raise GateBroken(f"gate.json mode {self.mode!r} is not a locate mode")
        self.preset = str(data.get("preset") or self.mode)
        self.run_id = str(data.get("run_id") or "")
        if not self.run_id:
            raise GateBroken("gate.json has no run_id")
        self.kinds = frozenset(data.get("kinds") or KINDS_BY_MODE[self.mode])
        universe = data.get("universe") if isinstance(data.get("universe"), dict) else {}
        self.universe_id = str(universe.get("id") or "")
        self.universe_sha = str(universe.get("sha256") or "")
        libraries = universe.get("libraries") if isinstance(universe.get("libraries"), dict) else {}
        self.libraries: dict[str, dict] = {}
        for lib, body in sorted(libraries.items()):
            if not isinstance(body, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(body.get("commit") or "")):
                raise GateBroken(f"gate.json universe library {lib!r} has no 40-hex commit")
            self.libraries[str(lib)] = {"repo": str(body.get("repo") or ""),
                                        "commit": str(body["commit"])}
        if self.mode != LOCATE_INVENTORY and not (self.universe_id and self.universe_sha
                                                  and self.libraries):
            raise GateBroken("gate.json has no frozen universe (id, sha256, libraries); a "
                             f"{self.mode} run cannot be gated without one")
        corpus = data.get("corpus_db") if isinstance(data.get("corpus_db"), dict) else {}
        self.corpus_db = pathlib.Path(corpus["path"]).expanduser() if corpus.get("path") else None
        self.corpus_db_sha = str(corpus.get("sha256") or "")
        sourceset = data.get("sourceset") if isinstance(data.get("sourceset"), dict) else {}
        if not sourceset.get("sources_dir"):
            raise GateBroken("gate.json sourceset has no sources_dir")
        self.sourceset = sourceset
        self.sources_dir = pathlib.Path(sourceset["sources_dir"]).expanduser()
        sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}
        if not sources:
            raise GateBroken("gate.json pins no sources (the gate fails closed)")
        self.sources: dict[str, dict] = {str(k): dict(v) for k, v in sources.items()
                                         if isinstance(v, dict)}
        self.candidates_dir = (pathlib.Path(data["candidates_dir"]).expanduser()
                               if data.get("candidates_dir") else None)
        self.blind = bool(data.get("blind"))
        if not data.get("tasks_dir"):
            raise GateBroken("gate.json names no tasks_dir (the task manifests)")
        self.tasks_dir = pathlib.Path(data["tasks_dir"]).expanduser()
        self.log_dir = pathlib.Path(data["log_dir"]).expanduser() if data.get("log_dir") else None
        self.orchestrator_db = (pathlib.Path(data["orchestrator_db"]).expanduser()
                                if data.get("orchestrator_db") else None)
        self.max_locations = int(data.get("max_locations") or MAX_LOCATIONS_PER_PASSAGE)
        self.min_absence_queries = int(data.get("min_absence_queries") or MIN_ABSENCE_QUERIES)
        self.max_quote_chars = int(data.get("max_quote_chars") or MAX_QUOTE_CHARS)
        self.max_context_chars = int(data.get("max_context_chars") or MAX_CONTEXT_CHARS)
        self.max_document_bytes = int(data.get("max_document_bytes") or MAX_DOCUMENT_BYTES)
        self.vendor = data.get("vendor") if isinstance(data.get("vendor"), dict) else {}

    @classmethod
    def load(cls, path: pathlib.Path) -> "GateConfig":
        try:
            data = load_json_strict(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateBroken(f"gate config at {path} is unreadable ({exc})") from None
        return cls(data, path)


def verify_vendor(cfg: GateConfig, vendor_dir: pathlib.Path = VENDOR) -> None:
    """Both vendored packages are the pinned releases, byte for byte; else :class:`GateBroken`.

    The manifest of each is hashed and compared with ``gate.json``'s ``vendor``
    pin, and every file the manifest lists is hashed and compared with the
    manifest: a gate whose checker was edited on the host is not a gate.
    """
    for package in VENDORED_PACKAGES:
        manifest_path = vendor_dir / f"{package}.manifest.json"
        want = str(cfg.vendor.get(package) or "")
        if not want:
            raise GateBroken(f"gate.json pins no manifest sha for the vendored {package}")
        try:
            raw = manifest_path.read_bytes()
        except OSError as exc:
            raise GateBroken(f"the vendored {package} manifest is missing ({exc})") from None
        got = _sha256_bytes(raw)
        if got != want:
            raise GateBroken(f"the vendored {package} manifest is {got[:12]}…, gate.json pins "
                             f"{want[:12]}…: not the release this run was planned with")
        try:
            files = json.loads(raw.decode("utf-8")).get("files") or {}
        except (ValueError, AttributeError):
            raise GateBroken(f"the vendored {package} manifest does not parse") from None
        for rel, sha in sorted(files.items()):
            path = vendor_dir / package / rel
            try:
                actual = _sha256_bytes(path.read_bytes())
            except OSError:
                raise GateBroken(f"vendored file {package}/{rel} is missing") from None
            if actual != sha:
                raise GateBroken(f"vendored file {package}/{rel} is not the manifest's bytes "
                                 f"(edited on this host?)")


# --------------------------------------------------------------------------- #
# per-call caches: only what the documents name is loaded
# --------------------------------------------------------------------------- #

class SourceView:
    """One pinned source's layer, head map and tables, loaded on first use."""

    def __init__(self, cfg: GateConfig, source_id: str):
        self.source_id = source_id
        self.pin = cfg.sources.get(source_id)
        if self.pin is None:
            raise KeyError(source_id)
        self.dir = cfg.sources_dir / source_id
        layer_path = self.dir / "layer.txt"
        try:
            raw = layer_path.read_bytes()
        except OSError as exc:
            raise GateBroken(f"{source_id}: layer.txt is missing at {layer_path} ({exc})") from None
        want = str(self.pin.get("layer_sha256") or "")
        got = _sha256_bytes(raw)
        if not want or got != want:
            raise GateBroken(f"{source_id}: layer.txt on this host is {got[:12]}…, the run pinned "
                             f"{want[:12] or 'nothing'}… (not the rendering this run was planned on)")
        self.layer = raw.decode("utf-8")
        formulas_path = self.dir / "formulas.json"
        try:
            formulas = load_json_strict(formulas_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateBroken(f"{source_id}: formulas.json is unreadable ({exc})") from None
        try:
            self.map = lt.HeadMap(self.layer, formulas if isinstance(formulas, list) else [])
        except lt.HeadError as exc:
            raise GateBroken(f"{source_id}: formulas.json does not fit the layer ({exc})") from None
        self._paragraphs: list[dict] | None = None

    @property
    def head(self) -> str:
        return self.map.head

    def paragraphs(self) -> list[dict]:
        if self._paragraphs is None:
            path = self.dir / "paragraphs.json"
            try:
                data = load_json_strict(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise GateBroken(f"{self.source_id}: paragraphs.json is unreadable ({exc})") from None
            self._paragraphs = [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []
        return self._paragraphs


class Context:
    """Everything one gate call loads lazily: sources, candidates, manifests, the corpus."""

    def __init__(self, cfg: GateConfig):
        self.cfg = cfg
        self._sources: dict[str, SourceView] = {}
        self._candidates: dict[str, dict | None] = {}
        self._manifests: dict[str, dict | None] = {}
        self._db: sqlite3.Connection | None = None
        self._exists: dict[tuple[str, str, str], bool] = {}

    def source(self, source_id: str) -> SourceView | None:
        if source_id not in self.cfg.sources or not _safe_name(source_id):
            return None
        if source_id not in self._sources:
            self._sources[source_id] = SourceView(self.cfg, source_id)
        return self._sources[source_id]

    def manifest(self, task_id: str) -> dict | None:
        """``gate/tasks/<task-id>.json``, or None when there is no such task."""
        if task_id in self._manifests:
            return self._manifests[task_id]
        if not self.cfg.tasks_dir.is_dir():
            raise GateBroken(f"the task-manifest directory {self.cfg.tasks_dir} is missing")
        found = None
        if _safe_name(task_id):
            path = self.cfg.tasks_dir / f"{task_id}.json"
            if path.is_file():
                try:
                    data = load_json_strict(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise GateBroken(f"task manifest {path} is unreadable ({exc})") from None
                if not isinstance(data, dict) or data.get("task_id") != task_id:
                    raise GateBroken(f"task manifest {path} is not task {task_id!r}'s")
                found = data
        self._manifests[task_id] = found
        return found

    def candidates(self, source_id: str) -> dict | None:
        """The L2 candidates file for a source, or None in the blind arm / with no L2."""
        if self.cfg.blind or self.cfg.candidates_dir is None:
            return None
        if source_id not in self._candidates:
            path = self.cfg.candidates_dir / f"{source_id}.json"
            try:
                data = load_json_strict(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise GateBroken(f"{source_id}: no candidates file at {path}: gate rule 4 reads "
                                 f"L2 itself and fails closed without it") from None
            except (OSError, ValueError) as exc:
                raise GateBroken(f"{source_id}: candidates file {path} is unreadable ({exc})") from None
            if not isinstance(data, dict) or data.get("source_id") != source_id:
                raise GateBroken(f"{path} is not {source_id}'s candidates file")
            if data.get("universe") not in (None, self.cfg.universe_id) or (
                    data.get("universe_sha256") not in (None, self.cfg.universe_sha)):
                raise GateBroken(f"{path} was built for universe {data.get('universe')!r}, not "
                                 f"this run's {self.cfg.universe_id!r}")
            self._candidates[source_id] = data
        return self._candidates[source_id]

    def exists(self, library: str, name: str) -> bool:
        """Is ``library:name`` in the exact table at the universe's commit?"""
        lib = self.cfg.libraries.get(library)
        if lib is None:
            return False
        key = (library, lib["commit"], name)
        if key not in self._exists:
            if self._db is None:
                if self.cfg.corpus_db is None:
                    raise GateBroken("gate.json names no corpus_db; rule 3 fails closed")
                if not self.cfg.corpus_db.is_file():
                    raise GateBroken(f"corpus.sqlite is missing at {self.cfg.corpus_db}")
                try:
                    uri = f"file:{self.cfg.corpus_db.as_posix()}?mode=ro&immutable=1"
                    self._db = sqlite3.connect(uri, uri=True)
                except sqlite3.Error as exc:
                    raise GateBroken(f"corpus.sqlite at {self.cfg.corpus_db} will not open "
                                     f"({exc})") from None
            try:
                row = self._db.execute(
                    "SELECT 1 FROM decls WHERE library = ? AND commit_sha = ? AND name = ? LIMIT 1",
                    key).fetchone()
            except sqlite3.Error as exc:
                raise GateBroken(f"corpus.sqlite is not the cf-corpus/1 schema ({exc})") from None
            self._exists[key] = row is not None
        return self._exists[key]

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None


# --------------------------------------------------------------------------- #
# problems
# --------------------------------------------------------------------------- #

class Problems:
    """``(rule, message)`` pairs, printed as ``[rule N] <where>: <message>``."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def add(self, rule: int | str, where: str, message: str) -> None:
        self.items.append((str(rule), f"{where}: {message}"))

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def lines(self) -> list[str]:
        return [f"[rule {rule}] {message}" for rule, message in self.items]

    def by_rule(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for rule, _ in self.items:
            out[rule] = out.get(rule, 0) + 1
        return dict(sorted(out.items()))


def _forbidden_keys(node: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_KEYS:
                found.append(f"{path}.{key}")
            found.extend(_forbidden_keys(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_forbidden_keys(value, f"{path}[{i}]"))
    return found


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _span(value: Any, length: int) -> tuple[int, int] | None:
    if (isinstance(value, list) and len(value) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in value)
            and 0 <= value[0] < value[1] <= length):
        return value[0], value[1]
    return None


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


# --------------------------------------------------------------------------- #
# the preview records (rule 1): what the export will build, checked here
# --------------------------------------------------------------------------- #

_LL = None


def _literature_links():
    global _LL
    if _LL is None:
        try:
            from literature_links import companion as ll  # noqa: PLC0415 - vendored, after the pin check
        except ImportError as exc:
            raise GateBroken(f"the vendored literature_links package will not import ({exc})") from None
        _LL = ll
    return _LL


def _head_quote(layer: str, span: tuple[int, int]) -> str:
    words = layer[span[0]:span[1]].split(" ")
    return " ".join(words[:EXPORT_QUOTE_WORDS]).strip() or layer[span[0]:span[1]]


def _subject(source_id: str, quote: dict) -> dict:
    location = {"quote": {k: v for k, v in quote.items() if k in ("exact", "prefix", "suffix")
                          and isinstance(v, str) and (k == "exact" or v)}}
    return {"type": "html", "uri": source_id, "location": location}


def _lean_end(cfg: GateConfig, library: str, declaration: Any) -> dict:
    lib = cfg.libraries.get(library, {})
    end: dict[str, Any] = {"type": "lean", "uri": lib.get("repo") or library,
                           "version": lib.get("commit") or "unknown"}
    if declaration is not None:
        end["location"] = {"declaration": declaration}
        end["role"] = "key"
    return end


def _preview_records(cfg: GateConfig, mode: str, source_id: str, item: str,
                     quote: dict, passage_kind: str | None, outcome: str | None,
                     locations: list[dict]) -> list[dict]:
    """The Literature Links records one passage of a document implies.

    A private, minimal builder: the export's *scripts/locate/records.py* (the
    planner package) is the real one, and integration points this check at it.
    It carries what a worker controls -- quote, declaration names, relations --
    and stands in for what it does not (the uri, the citation, the status).
    """
    base: dict[str, Any] = {"id": f"{source_id}::{item}", "subject": [_subject(source_id, quote)]}
    if passage_kind:
        base["kind"] = passage_kind
    records: list[dict] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        record = dict(base, object=[_lean_end(cfg, str(location.get("library") or ""),
                                              location.get("declaration"))],
                      relation=location.get("relation"))
        ext = {k: location[k] for k in ("rank", "confidence") if k in location}
        ext["item"] = item
        record["extensions"] = {LOCATE_EXTENSION: ext}
        if location.get("note"):
            record["note"] = location.get("note")
        records.append(record)
    if outcome == "absent":
        records.append(dict(base, relation=NO_MATCH, status="not_formalized",
                            object=[_lean_end(cfg, lib, None) for lib in sorted(cfg.libraries)]))
    if outcome in ("unresolved", None) and not locations:
        records.append(dict(base, object=[]))
    return records


def _check_records(cfg: GateConfig, problems: Problems, where: str, records: list[dict]) -> None:
    ll = _literature_links()
    for record in records:
        for problem in ll.check(record).problems:
            problems.add(1, where, f"the record this implies fails the Literature Links "
                                   f"checker: {problem}")


# --------------------------------------------------------------------------- #
# the documents
# --------------------------------------------------------------------------- #

def _header(doc: dict, name: str, cfg: GateConfig, task_id: str, manifest: dict | None,
            problems: Problems) -> str:
    """Rule 1's header half and rule 2's pins; returns the source id ('' when unusable)."""
    if doc.get("schema") != DOCUMENT_SCHEMA:
        problems.add(1, name, f"schema is {doc.get('schema')!r}, not {DOCUMENT_SCHEMA!r}")
    if doc.get("mode") != cfg.mode:
        problems.add(1, name, f"mode is {doc.get('mode')!r}, this run is {cfg.mode!r}")
    if doc.get("run_id") != cfg.run_id:
        problems.add(1, name, f"run_id is {doc.get('run_id')!r}, this run is {cfg.run_id!r}")
    if doc.get("task_id") != task_id:
        problems.add(1, name, f"task_id is {doc.get('task_id')!r}, but the document sits in "
                              f"task {task_id!r}'s directory")
    source_id = doc.get("source_id")
    if not _safe_name(source_id):
        problems.add(1, name, f"source_id {source_id!r} is not a source id")
        return ""
    if manifest is not None:
        named = [d.get("source_id") for d in manifest.get("documents") or [] if isinstance(d, dict)]
        if source_id not in named:
            problems.add(1, name, f"source {source_id} is not one of task {task_id}'s documents "
                                  f"({', '.join(map(str, named)) or 'none'})")
    pin = cfg.sources.get(source_id)
    if pin is None:
        problems.add(2, name, f"source {source_id} is not pinned by this run")
        return ""
    for key, want_key in (("source_sha", "html_sha256"), ("layer_sha256", "layer_sha256"),
                          ("renderer", "renderer")):
        if doc.get(key) != pin.get(want_key):
            problems.add(2, name, f"{key} is {str(doc.get(key))[:16]!r}, the pinned sourceset "
                                  f"says {str(pin.get(want_key))[:16]!r}")
    if cfg.mode == LOCATE_INVENTORY:
        for key in ("universe", "universe_sha256"):
            if key in doc:
                problems.add(1, name, f"an inventory document carries no {key}")
    else:
        if doc.get("universe") != cfg.universe_id or doc.get("universe_sha256") != cfg.universe_sha:
            problems.add(5, name, f"universe {doc.get('universe')!r} @ "
                                  f"{str(doc.get('universe_sha256'))[:12]!r} is not the frozen "
                                  f"universe {cfg.universe_id} @ {cfg.universe_sha[:12]}")
    for path in _forbidden_keys(doc):
        problems.add(3, name, f"{path} is not a worker's to write (proof_status is joined from "
                              f"the index at export; the commit is the universe's)")
    return source_id


def _unresolved_block(doc: dict, name: str, problems: Problems) -> bool:
    block = doc.get("unresolved")
    if block is None:
        return False
    if not isinstance(block, dict) or not _nonempty_str(block.get("reason")) \
            or block.get("by") not in UNRESOLVED_BY:
        problems.add(1, name, "unresolved must be {reason: <text>, by: worker|attempt_cap}")
    return True


def _check_location(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                    location: Any) -> None:
    if not isinstance(location, dict):
        problems.add(1, where, "a location is an object")
        return
    library = location.get("library")
    declaration = location.get("declaration")
    if library not in cfg.libraries:
        problems.add(3, where, f"library {library!r} is not in universe {cfg.universe_id} "
                               f"({', '.join(sorted(cfg.libraries))})")
    elif not _nonempty_str(declaration):
        problems.add(1, where, "a location names a declaration")
    elif not ctx.exists(library, declaration):
        problems.add(3, where, f"{library}:{declaration} is not in corpus.sqlite at "
                               f"{cfg.libraries[library]['commit'][:12]} (a phantom declaration)")
    if location.get("relation") not in LOCATE_RELATIONS:
        problems.add(1, where, f"relation {location.get('relation')!r} is not a locate relation")
    if location.get("relation") == CLOSE_MATCH and not _nonempty_str(location.get("note")):
        problems.add(6, where, "closeMatch needs a note naming the convention difference")
    rank = location.get("rank")
    if not (isinstance(rank, int) and not isinstance(rank, bool)
            and 1 <= rank <= cfg.max_locations):
        problems.add(6, where, f"rank {rank!r} is not 1..{cfg.max_locations}")
    if location.get("confidence") not in CONFIDENCES:
        problems.add(1, where, f"confidence {location.get('confidence')!r} is not "
                               f"{'/'.join(sorted(CONFIDENCES))}")
    if not _nonempty_str(location.get("witness")):
        problems.add(1, where, "a location carries a witness")
    if "note" in location and not isinstance(location.get("note"), str):
        problems.add(1, where, "note is a string")


def _check_locations(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                     locations: Any) -> list[dict]:
    if not isinstance(locations, list):
        problems.add(1, where, "locations is a list")
        return []
    if len(locations) > cfg.max_locations:
        problems.add(6, where, f"{len(locations)} locations; at most {cfg.max_locations} per passage")
    ranks = [loc.get("rank") for loc in locations if isinstance(loc, dict)]
    if len(ranks) != len(set(map(repr, ranks))):
        problems.add(6, where, f"ranks {ranks} are not distinct")
    for i, location in enumerate(locations):
        _check_location(cfg, ctx, problems, f"{where}.locations[{i}]", location)
    return [loc for loc in locations if isinstance(loc, dict)]


def _check_receipt(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                   receipt: Any) -> dict:
    if not isinstance(receipt, dict):
        problems.add(1, where, "receipt is {queries: [...], candidates: [...]}")
        return {"queries": [], "candidates": []}
    queries = receipt.get("queries")
    candidates = receipt.get("candidates")
    if not isinstance(queries, list) or not all(isinstance(q, str) for q in queries):
        problems.add(1, where, "receipt.queries is a list of strings")
        queries = []
    if not isinstance(candidates, list):
        problems.add(1, where, "receipt.candidates is a list")
        candidates = []
    for i, cand in enumerate(candidates):
        at = f"{where}.receipt.candidates[{i}]"
        if not isinstance(cand, dict):
            problems.add(1, at, "a candidate is an object")
            continue
        if cand.get("verdict") not in CANDIDATE_VERDICTS:
            problems.add(1, at, f"verdict {cand.get('verdict')!r} is not accepted/rejected")
        if cand.get("verdict") == "rejected" and not _nonempty_str(cand.get("reason")):
            problems.add(1, at, "a rejected candidate carries its reason")
        if not _nonempty_str(cand.get("declaration")) or not _nonempty_str(cand.get("library")):
            problems.add(1, at, "a candidate names its library and declaration")
        elif cand.get("verdict") == "accepted":
            if cand["library"] not in cfg.libraries:
                problems.add(3, at, f"library {cand['library']!r} is not in the universe")
            elif not ctx.exists(cand["library"], cand["declaration"]):
                problems.add(3, at, f"accepted candidate {cand['library']}:{cand['declaration']} "
                                    f"is not in corpus.sqlite (a phantom declaration)")
    return {"queries": queries, "candidates": [c for c in candidates if isinstance(c, dict)]}


def _curated(ctx: Context, source_id: str) -> list[tuple[str, str]]:
    data = ctx.candidates(source_id)
    if not data:
        return []
    out = []
    for hit in data.get("curated") or []:
        if isinstance(hit, dict) and _nonempty_str(hit.get("library")) \
                and _nonempty_str(hit.get("declaration")):
            out.append((hit["library"], hit["declaration"]))
    return sorted(set(out))


def check_inventory(doc: dict, name: str, cfg: GateConfig, ctx: Context, source_id: str,
                    manifest_doc: dict, problems: Problems, unresolved: bool) -> dict:
    """Rules 1, 2, 4 and 8 for a ``locate-inventory`` document."""
    stats = {"passages": 0, "unresolved": unresolved}
    passages = doc.get("passages")
    skips = doc.get("skips", [])
    if not isinstance(passages, list):
        problems.add(1, name, "passages is a list")
        return stats
    if not isinstance(skips, list):
        problems.add(1, name, "skips is a list")
        skips = []
    if "notes" in doc and (not isinstance(doc["notes"], str) or len(doc["notes"]) > NOTES_MAX):
        problems.add(1, name, f"notes is a string of at most {NOTES_MAX} characters")
    if unresolved:
        if passages:
            problems.add(1, name, "an unresolved inventory carries no passages")
        return stats
    source = ctx.source(source_id)
    if source is None:
        return stats
    layer = source.layer
    stats["passages"] = len(passages)
    starts: list[int] = []
    rows: list[tuple[int, dict, int]] = []         # (index, passage, head start)
    spans: list[tuple[int, int]] = []
    span_starts: set[int] = set()
    seen_items: set[str] = set()
    for i, p in enumerate(passages):
        where = f"{name}: passages[{i}]"
        if not isinstance(p, dict):
            problems.add(1, where, "a passage is an object")
            continue
        item = p.get("item")
        if not isinstance(item, str) or not ITEM.match(item):
            problems.add(1, where, f"item {item!r} is not `In-text #<8 hex>` as the stages write it")
        elif item in seen_items:
            problems.add(2, where, f"{item} is given twice")
        else:
            seen_items.add(item)
        for key, allowed in (("passage_kind", PASSAGE_KINDS), ("prominence", PROMINENCE),
                             ("scope", SCOPES), ("finder", FINDERS)):
            if p.get(key) not in allowed:
                problems.add(1, where, f"{key} {p.get(key)!r} is not one of "
                                       f"{', '.join(sorted(allowed))}")
        if "kind" in p and p["kind"] != "PASSAGE":
            problems.add(1, where, "an inventory row's kind, when written, is PASSAGE")
        if not _nonempty_str(p.get("statement")):
            problems.add(1, where, "statement restates the passage")
        if p.get("concept") is not None and not (isinstance(p["concept"], str)
                                                 and QID.match(p["concept"])):
            problems.add(1, where, "concept is null or a Wikidata QID")
        section = p.get("section", [])
        if not isinstance(section, list) or not all(isinstance(s, str) for s in section):
            problems.add(1, where, "section is a list of headings")
        if p.get("fragment") is not None and not isinstance(p.get("fragment"), str):
            problems.add(1, where, "fragment is null or a string")
        head_words = p.get("head_words")
        if not (isinstance(head_words, int) and not isinstance(head_words, bool)
                and 1 <= head_words <= lt.MAX_HEAD_WORDS):
            problems.add(1, where, f"head_words {head_words!r} is not 1..{lt.MAX_HEAD_WORDS}")
        span = _span(p.get("span"), len(layer))
        if span is None:
            problems.add(2, where, f"span {p.get('span')!r} is not [start, end] inside the layer "
                                   f"(0..{len(layer)})")
            continue
        spans.append(span)
        span_starts.add(span[0])
        quote = p.get("quote")
        if not isinstance(quote, dict) or not _nonempty_str(quote.get("exact")):
            problems.add(1, where, "quote is {exact, prefix, suffix} with a non-empty exact")
            continue
        exact = quote["exact"]
        prefix, suffix = quote.get("prefix", ""), quote.get("suffix", "")
        if not isinstance(prefix, str) or not isinstance(suffix, str):
            problems.add(1, where, "prefix and suffix are strings")
            continue
        if len(exact) > cfg.max_quote_chars or max(len(prefix), len(suffix)) > cfg.max_context_chars:
            problems.add(2, where, f"the quote is over the cap ({cfg.max_quote_chars} characters of "
                                   f"exact, {cfg.max_context_chars} of each context)")
            continue
        found = lt.find_quote(layer, exact, prefix, suffix)
        if len(found) != 1:
            problems.add(2, where, f"the quote matches the layer {len(found)} times; it must match "
                                   f"exactly once with its prefix and suffix")
            continue
        if found[0] != span[0] or span[0] + len(exact) > span[1]:
            problems.add(2, where, f"the quote starts at {found[0]} and ends at "
                                   f"{found[0] + len(exact)}; its passage is {list(span)}: the quote "
                                   f"starts the passage and lies inside it")
            continue
        try:
            head_start = source.map.to_head(span[0])
        except lt.HeadError as exc:
            problems.add(2, where, f"the passage cannot start there ({exc})")
            continue
        starts.append(head_start)
        rows.append((i, p, head_start))
        _check_records(cfg, problems, where,
                       _preview_records(cfg, LOCATE_INVENTORY, source_id, str(item), quote,
                                        p.get("passage_kind"), None, []))
    if rows and len(starts) == len(rows):
        try:
            ids = lt.assign_ids(source.head, starts)
        except lt.HeadError as exc:
            problems.add(2, name, f"the passage table cannot be keyed: {exc}")
        else:
            for (i, p, _start), (_s, digest, words) in zip(rows, ids):
                if p.get("item") != f"In-text #{digest}" or p.get("head_words") != words:
                    problems.add(2, f"{name}: passages[{i}]",
                                 f"the passage at {p['span'][0]} is In-text #{digest} with a "
                                 f"{words}-word head, not {p.get('item')!r} with "
                                 f"{p.get('head_words')!r}")
    skip_spans: list[tuple[int, int]] = []
    for i, skip in enumerate(skips):
        at = f"{name}: skips[{i}]"
        span = _span(skip.get("span") if isinstance(skip, dict) else None, len(layer))
        if span is None:
            problems.add(1, at, "a skip is {span: [start, end] inside the layer, reason}")
            continue
        if not _nonempty_str(skip.get("reason")):
            problems.add(1, at, "a skip carries its reason")
        skip_spans.append(span)
    # rule 8: the deterministic finders' passages and the coverage floor
    for j, finder in enumerate(manifest_doc.get("finders") or []):
        if not isinstance(finder, dict):
            continue
        fspan = _span(finder.get("span"), len(layer))
        if fspan is None:
            raise GateBroken(f"task manifest finder {j} of {source_id} has no span in the layer")
        start = finder.get("start", fspan[0])
        kept = start in span_starts
        skipped = any(s[0] <= fspan[0] and fspan[1] <= s[1] for s in skip_spans)
        if not (kept or skipped):
            problems.add(8, name, f"the {finder.get('finder')} passage at {list(fspan)} was "
                                  f"dropped with no skip covering its span")
    finder_starts = {(f.get("finder"), f.get("start", (f.get("span") or [None])[0]))
                     for f in manifest_doc.get("finders") or [] if isinstance(f, dict)}
    for i, p, _h in rows:
        if p.get("finder") in DETERMINISTIC_FINDERS and (p.get("finder"), p["span"][0]) not in finder_starts:
            problems.add(8, f"{name}: passages[{i}]",
                         f"finder {p.get('finder')} is claimed, but no {p.get('finder')} passage of "
                         f"the task starts at {p['span'][0]}; a passage the enumerator found is "
                         f"`model`")
    covering = spans + skip_spans
    for k, paragraph in enumerate(source.paragraphs()):
        if not paragraph.get("math"):
            continue
        pspan = _span(paragraph.get("span"), len(layer))
        if pspan is None:
            raise GateBroken(f"{source_id}: paragraphs.json entry {k} has no span in the layer")
        if not any(_overlaps(pspan, c) for c in covering):
            problems.add(8, name, f"the math paragraph at {list(pspan)} (section "
                                  f"{paragraph.get('section') or []}) is covered by no passage and "
                                  f"no skip (the coverage floor)")
    # rule 4, inventory side
    curated = _curated(ctx, source_id)
    if curated:
        if not any(isinstance(p, dict) and p.get("scope") in LOCATED_SCOPES for p in passages):
            problems.add(4, name, f"L2 holds {len(curated)} curated hit(s) for this article "
                                  f"({_hits(curated)}), so the inventory keeps a formal_math passage")
        for i, p in enumerate(passages):
            if isinstance(p, dict) and p.get("prominence") == "subject" \
                    and p.get("scope") not in LOCATED_SCOPES:
                problems.add(4, f"{name}: passages[{i}]",
                             f"the subject passage is scoped {p.get('scope')} while L2 holds a "
                             f"curated hit for the article ({_hits(curated)})")
    return stats


def _hits(curated: list[tuple[str, str]], n: int = 3) -> str:
    shown = ", ".join(f"{lib}:{decl}" for lib, decl in curated[:n])
    return shown + (f" and {len(curated) - n} more" if len(curated) > n else "")


def check_locate(doc: dict, name: str, cfg: GateConfig, ctx: Context, source_id: str,
                 manifest_doc: dict, problems: Problems, unresolved: bool) -> dict:
    """Rules 1-6 for a ``locate`` document."""
    stats = {"passages": 0, "unresolved_passages": 0, "unresolved": unresolved}
    passages = doc.get("passages")
    if not isinstance(passages, list):
        problems.add(1, name, "passages is a list")
        return stats
    source = ctx.source(source_id)
    if source is None:
        return stats
    frozen: dict[str, dict] = {}
    for p in manifest_doc.get("passages") or []:
        if not isinstance(p, dict) or not isinstance(p.get("item"), str):
            continue
        span = _span(p.get("span"), len(source.layer))
        try:
            if span is None:
                raise lt.HeadError(f"span {p.get('span')!r} is not in the layer")
            digest = lt.hex_at(source.head, source.map.to_head(span[0]), int(p.get("head_words") or 0))
        except (lt.HeadError, ValueError) as exc:
            raise GateBroken(f"task manifest passage {p['item']} of {source_id} does not key on "
                             f"the pinned layer ({exc}): the frozen inventory is inconsistent") from None
        if p["item"] != f"In-text #{digest}":
            raise GateBroken(f"task manifest passage {p['item']} of {source_id} hashes to "
                             f"In-text #{digest} on the pinned layer: the frozen inventory is "
                             f"inconsistent")
        frozen[p["item"]] = dict(p, span=span)
    written: dict[str, int] = {}
    stats["passages"] = len(passages)
    handled: set[tuple[str, str]] = set()
    absent_where: list[str] = []
    for i, p in enumerate(passages):
        where = f"{name}: passages[{i}]"
        if not isinstance(p, dict):
            problems.add(1, where, "a passage is an object")
            continue
        item = p.get("item")
        if item not in frozen:
            problems.add(2, where, f"item {item!r} is not one of this task's passages")
            continue
        if item in written:
            problems.add(2, where, f"{item} is given twice")
            continue
        written[item] = i
        where = f"{name}: {item}"
        outcome = p.get("outcome")
        if outcome not in LOCATE_OUTCOMES:
            problems.add(1, where, f"outcome {outcome!r} is not located/absent/unresolved")
            continue
        if unresolved and outcome != "unresolved":
            problems.add(1, where, "an unresolved document leaves every passage unresolved")
        locations = _check_locations(cfg, ctx, problems, where, p.get("locations"))
        receipt = _check_receipt(cfg, ctx, problems, where, p.get("receipt"))
        counting = [loc for loc in locations if loc.get("relation") != RELATED_MATCH]
        if outcome == "located" and not counting:
            problems.add(1, where, "located needs a location whose relation counts; with only "
                                   "relatedMatch the passage is absent, beside them")
        if outcome == "absent":
            if counting:
                problems.add(1, where, "an absence may sit beside relatedMatch locations only")
            distinct = {" ".join(q.split()).casefold() for q in receipt["queries"] if q.strip()}
            if len(distinct) < cfg.min_absence_queries:
                problems.add(5, where, f"an absence lists {len(distinct)} distinct quer"
                                       f"{'y' if len(distinct) == 1 else 'ies'}; at least "
                                       f"{cfg.min_absence_queries}")
            absent_where.append(where)
        if outcome == "unresolved":
            stats["unresolved_passages"] += 1
            if locations:
                problems.add(1, where, "an unresolved passage has no locations")
            if not _nonempty_str(p.get("reason")) and not unresolved:
                problems.add(1, where, "an unresolved passage says why (reason)")
        for loc in locations:
            handled.add((str(loc.get("library")), str(loc.get("declaration"))))
        for cand in receipt["candidates"]:
            if cand.get("verdict") == "accepted" or _nonempty_str(cand.get("reason")):
                handled.add((str(cand.get("library")), str(cand.get("declaration"))))
        span = frozen[item]["span"]
        quote = {"exact": _head_quote(source.layer, span)}
        _check_records(cfg, problems, where,
                       _preview_records(cfg, LOCATE, source_id, item, quote,
                                        frozen[item].get("passage_kind"), outcome, locations))
    missing = sorted(set(frozen) - set(written))
    if missing:
        problems.add(2, name, f"the task's passages {', '.join(missing)} have no outcome")
    # rule 4: every curated hit handed to the task is located or rejected with a reason
    if not unresolved:
        unhandled = [hit for hit in _curated(ctx, source_id) if hit not in handled]
        if unhandled:
            problems.add(4, name, f"curated L2 hit(s) {_hits(unhandled)} are neither located nor "
                                  f"rejected with a reason in any receipt"
                                  + (f", and {len(absent_where)} passage(s) end absent"
                                     if absent_where else ""))
    return stats


def check_challenge(doc: dict, name: str, cfg: GateConfig, ctx: Context, source_id: str,
                    manifest_doc: dict, problems: Problems, unresolved: bool) -> dict:
    """Rules 1, 3 and 6 for a ``locate-challenge`` document."""
    stats = {"verdicts": 0, "unresolved": unresolved}
    verdicts = doc.get("verdicts")
    if not isinstance(verdicts, list):
        problems.add(1, name, "verdicts is a list")
        return stats
    if unresolved:
        if verdicts:
            problems.add(1, name, "an unresolved challenge carries no verdicts")
        return stats
    records: dict[tuple, dict] = {}
    for r in manifest_doc.get("records") or []:
        if isinstance(r, dict):
            lkey = r.get("location_key") if r.get("target") == "location" else None
            records[(r.get("item"), r.get("target"), lkey)] = r
    stats["verdicts"] = len(verdicts)
    judged: set[tuple] = set()
    source = ctx.source(source_id)
    for i, v in enumerate(verdicts):
        where = f"{name}: verdicts[{i}]"
        if not isinstance(v, dict):
            problems.add(1, where, "a verdict is an object")
            continue
        target, action = v.get("target"), v.get("action")
        if target not in CHALLENGE_TARGETS:
            problems.add(1, where, f"target {target!r} is not a challenge target")
            continue
        if action not in CHALLENGE_COMBOS[target]:
            problems.add(1, where, f"action {action!r} does not apply to a {target} "
                                   f"({'/'.join(sorted(CHALLENGE_COMBOS[target]))})")
        key = (v.get("item"), target, v.get("location_key") if target == "location" else None)
        if key not in records:
            problems.add(1, where, f"{v.get('item')!r} {target}"
                                   + (f" {str(v.get('location_key'))[:12]}" if target == "location" else "")
                                   + " is not one of the records this task judges")
            continue
        if key in judged:
            problems.add(1, where, "this record is judged twice")
        judged.add(key)
        if not _nonempty_str(v.get("witness")):
            problems.add(1, where, "a verdict carries a witness")
        if action == "downgrade" and v.get("relation") not in LOCATE_RELATIONS:
            problems.add(1, where, "a downgrade names the weaker relation")
        if action == "overturn" and target == "prominence" and v.get("prominence") not in PROMINENCE:
            problems.add(1, where, "a prominence overturn names the new prominence")
        if action == "overturn" and target in ("absence", "unresolved", "scope"):
            locations = _check_locations(cfg, ctx, problems, where, v.get("locations"))
            if target != "scope" and not [loc for loc in locations
                                          if loc.get("relation") != RELATED_MATCH]:
                problems.add(1, where, "overturning an absence or an unresolved outcome gives a "
                                       "location whose relation counts")
            if source is not None and locations:
                rec = records[key]
                span = _span(rec.get("span"), len(source.layer))
                quote = {"exact": _head_quote(source.layer, span)} if span else {"exact": str(v.get("item"))}
                _check_records(cfg, problems, where,
                               _preview_records(cfg, LOCATE_CHALLENGE, source_id, str(v.get("item")),
                                                quote, rec.get("passage_kind"), "located", locations))
    owed = [k for k in records if k not in judged]
    if owed:
        problems.add(1, name, f"{len(owed)} record(s) of this task have no verdict "
                              f"({', '.join(str(k[0]) + ' ' + str(k[1]) for k in owed[:3])})")
    return stats


CHECKS = {LOCATE_INVENTORY: check_inventory, LOCATE: check_locate,
          LOCATE_CHALLENGE: check_challenge}


def validate_document(path: pathlib.Path, name: str, task_id: str, cfg: GateConfig,
                      ctx: Context, problems: Problems) -> dict:
    """Every rule but 7 over one document; returns its counts for the receipt and the log."""
    stats: dict[str, Any] = {"task_id": task_id, "mode": cfg.mode}
    try:
        raw = path.read_bytes()
    except OSError as exc:
        problems.add(1, name, f"cannot be read ({exc})")
        return stats
    if len(raw) > cfg.max_document_bytes:
        problems.add(1, name, f"{len(raw)} bytes; a document is at most {cfg.max_document_bytes}")
        return stats
    try:
        doc = load_json_strict(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        problems.add(1, name, f"not valid, unambiguous JSON ({exc})")
        return stats
    if not isinstance(doc, dict):
        problems.add(1, name, "a document is a JSON object")
        return stats
    manifest = ctx.manifest(task_id)
    if manifest is None:
        problems.add(7, name, f"task {task_id!r} has no manifest in this run's gate: not a task "
                              f"of run {cfg.run_id}")
        return stats
    if manifest.get("mode") not in (None, cfg.mode) or manifest.get("run_id") not in (None, cfg.run_id):
        raise GateBroken(f"task manifest {task_id} is for {manifest.get('mode')}/"
                         f"{manifest.get('run_id')}, not this gate's {cfg.mode}/{cfg.run_id}")
    source_id = _header(doc, name, cfg, task_id, manifest, problems)
    unresolved = _unresolved_block(doc, name, problems)
    stats.update({"source_id": source_id, "unresolved": unresolved,
                  "attempt_capped": bool(unresolved and isinstance(doc.get("unresolved"), dict)
                                         and doc["unresolved"].get("by") == "attempt_cap")})
    if not source_id:
        return stats
    manifest_doc = next((d for d in manifest.get("documents") or []
                         if isinstance(d, dict) and d.get("source_id") == source_id), None)
    if manifest_doc is None:
        return stats
    stats.update(CHECKS[cfg.mode](doc, name, cfg, ctx, source_id, manifest_doc, problems, unresolved))
    return stats


# --------------------------------------------------------------------------- #
# rule 7: the change set, and whose task it is
# --------------------------------------------------------------------------- #

_PATH_COLUMN = re.compile(r"(?:^|_)(?:worktree(?:_path|_dir)?|path|cwd|directory)$", re.I)
_TASK_COLUMN = re.compile(r"task(?:_?id)?$", re.I)


def _open_orchestrator(db_path: pathlib.Path) -> sqlite3.Connection:
    """A read-only connection to tend's LIVE database that sees its committed writes.

    tend keeps ``orchestrator.sqlite`` in WAL mode, and a row it has committed
    sits in ``-wal`` until a checkpoint copies it into the main file.  An
    ``immutable=1`` open reads the main file alone, so it missed every worktree
    tend had created since the last checkpoint -- on a live run, nearly all of
    them, and the gate fell back to the single-stem rule (field defect G1).
    ``mode=ro`` reads the WAL through the shared-memory index, takes no write
    lock and never blocks the writer.  ``immutable=1`` stays only as the
    fallback for a database nobody is writing whose directory this user cannot
    write (no ``-shm`` can be made there), where it is also correct.
    """
    uri = f"file:{db_path.as_posix()}"
    try:
        con = sqlite3.connect(f"{uri}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        con = None
    if con is not None:
        try:
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            return con
        except sqlite3.Error:
            con.close()
    return sqlite3.connect(f"{uri}?mode=ro&immutable=1", uri=True)


def task_from_orchestrator(db_path: pathlib.Path | None, worktree: pathlib.Path) -> tuple[str, str]:
    """``(task id, how)`` recovered from tend's ``orchestrator.sqlite``, or ``('', why not)``.

    tend's schema is NOT known to this repository, so this is a defensive
    lookup, never a parse of a documented shape: every table with a
    worktree-path-like column and a task-id-like column is asked for rows whose
    path is this worktree, and the answer counts only when exactly one task id
    comes back.  A recorded path is this worktree when it is the same spelling
    or resolves to the same directory: tend records the path it was configured
    with, while the gate is handed the resolved working directory, and the two
    differ wherever a component is a symlink (``/var`` -> ``/private/var`` on
    macOS, a symlinked home on a host).  The database is opened read-only
    through :func:`_open_orchestrator`, so the lookup can neither write it nor
    wait on tend's lock, and it sees what tend committed to its WAL.  The schema
    is an ASSUMPTION to verify on the host after every tend upgrade
    (references/runbook-server.md names the same check for ``TEND_TASK_ID``).
    """
    if db_path is None or not db_path.is_file():
        return "", "no orchestrator.sqlite"
    resolved = str(worktree.resolve())
    candidates = {str(worktree), resolved}
    suffix = "/" + worktree.resolve().name
    found: set[str] = set()
    try:
        con = _open_orchestrator(db_path)
    except sqlite3.Error as exc:
        return "", f"orchestrator.sqlite will not open ({exc})"
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for table in tables:
            columns = [r[1] for r in con.execute(f'PRAGMA table_info("{table.replace(chr(34), "")}")')]
            paths = [c for c in columns if _PATH_COLUMN.search(c)]
            tasks = [c for c in columns if _TASK_COLUMN.fullmatch(c)]
            for pcol in paths:
                for tcol in tasks:
                    p = f'"{pcol.replace(chr(34), "")}"'
                    # The rows spelled like this worktree, or ending in its name
                    # (substr, not LIKE: a worktree name may hold `_` or `%`);
                    # which of the latter is this worktree is decided by resolving.
                    q = (f'SELECT DISTINCT {p}, "{tcol.replace(chr(34), "")}" FROM '
                         f'"{table.replace(chr(34), "")}" WHERE {p} IN '
                         f'({",".join("?" * len(candidates))}) '
                         f"OR substr(rtrim({p}, '/'), ?) = ?")
                    for path, value in con.execute(q, [*sorted(candidates), -len(suffix), suffix]):
                        if value in (None, "") or not isinstance(path, str):
                            continue
                        if path in candidates or os.path.realpath(path) == resolved:
                            found.add(str(value))
    except sqlite3.Error as exc:
        return "", f"orchestrator.sqlite could not be read ({exc})"
    finally:
        con.close()
    if len(found) == 1:
        return found.pop(), "orchestrator.sqlite"
    if found:
        return "", f"orchestrator.sqlite names {len(found)} tasks for this worktree"
    return "", "orchestrator.sqlite names no task for this worktree"


def _classify(path: str) -> tuple[str, str, str] | None:
    """``('findings', task, source)`` / ``('task', task, '')`` / None for anything else."""
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "findings" and parts[2].endswith(".json") \
            and _safe_name(parts[1]) and _safe_name(parts[2][:-len(".json")]):
        return "findings", parts[1], parts[2][:-len(".json")]
    if len(parts) == 2 and parts[0] == "tasks" and parts[1].endswith((".yaml", ".yml")):
        stem = parts[1].rsplit(".", 1)[0]
        if _safe_name(stem):
            return "task", stem, ""
    return None


def check_changed(entries: dict[str, str], ctx: Context, *, task_id: str = "",
                  allow_empty: bool = False) -> Problems:
    """Rule 7: only the task's own documents and task file, nothing removed.

    With no task id the single-stem rule of ``verify_findings.check_changed``
    stands in, addition-aware per task for a batched pre-merge change set: a
    several-task set passes only when every one of its documents is an addition.
    """
    problems = Problems()
    written: dict[str, list[tuple[str, str]]] = {}
    tasks: set[str] = set()
    seen: set[str] = set()
    for path in sorted(entries):
        status = entries[path]
        kind = _classify(path)
        if kind is None:
            problems.add(7, path, "is outside findings/<task-id>/<source-id>.json and "
                                  "tasks/<task-id>.yaml")
            continue
        what, stem, source = kind
        seen.add(stem)
        if status in ("D", "R"):
            problems.add(7, path, "was deleted or renamed away; a contribution writes its own "
                                  "documents and removes nothing")
            continue
        if task_id and stem != task_id:
            problems.add(7, path, f"belongs to task {stem!r}, not to this worker's {task_id!r}")
            continue
        if what == "task":
            tasks.add(stem)
            continue
        written.setdefault(stem, []).append((source, status))
        manifest = ctx.manifest(stem)
        if manifest is None:
            problems.add(7, path, f"task {stem!r} is not a task of run {ctx.cfg.run_id}")
            continue
        named = {d.get("source_id") for d in manifest.get("documents") or [] if isinstance(d, dict)}
        if source not in named:
            problems.add(7, path, f"source {source} is not one of task {stem}'s documents")
    for stem in sorted(set(written) - tasks):
        problems.add(7, f"findings/{stem}/", f"has no tasks/{stem}.yaml in the same change set")
    if not task_id and len(seen) > 1:
        landed = sorted(stem for stem, docs in written.items() if any(s != "A" for _src, s in docs))
        landed += sorted(stem for stem in seen - set(written))
        if landed:
            problems.add(7, "change set", f"belongs to {len(seen)} tasks ({', '.join(sorted(seen))}) "
                                          f"and no task id says which is this worker's; "
                                          f"{', '.join(sorted(set(landed)))} added no new document — "
                                          f"pass --task-id <id> or export TEND_TASK_ID")
    if not written and not allow_empty:
        problems.add(7, "change set", "no document was written")
    return problems


def ownership_line(task_id: str, how: str) -> str:
    if task_id and how == "orchestrator.sqlite":
        return (f"ownership checked against task {task_id} (task id recovered from "
                f"orchestrator.sqlite: an assumed schema, verify on the host)")
    if task_id:
        return f"ownership checked against task {task_id}"
    return ("ownership: single-stem rule (no task id); pass --task-id or export TEND_TASK_ID "
            "for the full check")


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #

def _documents_in(directory: pathlib.Path) -> list[tuple[pathlib.Path, str, str]]:
    """``[(path, display name, task id)]`` for ``<directory>/<task>/<source>.json``."""
    out = []
    if not directory.is_dir():
        return out
    for task_dir in sorted(directory.iterdir()):
        if not task_dir.is_dir() or not _safe_name(task_dir.name):
            continue
        for path in sorted(task_dir.glob("*.json")):
            if path.name in NON_DOCUMENT_FILES or path.name.startswith("."):
                continue
            out.append((path, f"{task_dir.name}/{path.name}", task_dir.name))
    return out


def write_log(cfg: GateConfig, record: dict) -> None:
    """Append one line to the gate log; any failure to write is silent by design."""
    if cfg.log_dir is None:
        return
    try:
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(cfg.log_dir / "gate.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            try:
                import fcntl  # noqa: PLC0415 - POSIX only
                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        return


def run_gate(cfg: GateConfig, *, root: pathlib.Path, findings: list[pathlib.Path] | None = None,
             findings_dir: str = DEFAULT_FINDINGS_DIR, changed_files: bool = False,
             base: str = "main", task_id: str = "", allow_empty: bool = False,
             check_vendor: bool = True) -> dict:
    """Run the gate in-process; ``{exit, problems, broken, documents, ownership, ...}``.

    ``cf harvest`` calls this over a landed corpus; ``main`` is the CLI around it.
    Raises nothing: an infrastructure failure is ``exit: 3`` with ``broken`` set.
    """
    result: dict[str, Any] = {"exit": EXIT_OK, "problems": [], "broken": [], "documents": 0,
                              "by_rule": {}, "stats": [], "ownership": "", "task_id": task_id,
                              "mode": cfg.mode, "run_id": cfg.run_id}
    ctx = Context(cfg)
    problems = Problems()
    how = "--task-id" if task_id else ""
    try:
        if check_vendor:
            verify_vendor(cfg)
        if changed_files and not task_id:
            task_id, how = task_from_orchestrator(cfg.orchestrator_db, root)
            if not task_id:
                result["ownership_note"] = how
        docs: list[tuple[pathlib.Path, str, str]] = []
        if changed_files:
            entries, errs = vf.changed_entries(root, base)
            if errs:
                raise GateBroken("; ".join(errs))
            changed = check_changed(entries, ctx, task_id=task_id, allow_empty=allow_empty)
            problems.items.extend(changed.items)
            if not findings:
                for path, status in sorted(entries.items()):
                    kind = _classify(path)
                    if kind and kind[0] == "findings" and status not in ("D", "R") \
                            and (not task_id or kind[1] == task_id):
                        docs.append((root / path, path, kind[1]))
            result["ownership"] = ownership_line(task_id, how)
        if findings:
            for f in findings:
                f = pathlib.Path(f)
                docs.append((f, f"{f.parent.name}/{f.name}", f.parent.name))
        elif not changed_files:
            docs = _documents_in(root / findings_dir)
            if not docs:
                problems.add(1, str(root / findings_dir), "the gate found no locate documents to "
                                                         "validate (an empty set is not a clean one)")
        for path, name, doc_task in docs:
            if task_id and doc_task != task_id:
                problems.add(7, name, f"is task {doc_task!r}'s document, not {task_id!r}'s")
                continue
            result["stats"].append(validate_document(path, name, doc_task, cfg, ctx, problems))
        result["documents"] = len(docs)
    except GateBroken as exc:
        result["broken"].append(str(exc))
        result["exit"] = EXIT_GATE_BROKEN
    finally:
        ctx.close()
    result["problems"] = problems.lines()
    result["by_rule"] = problems.by_rule()
    result["task_id"] = task_id
    if result["exit"] == EXIT_OK and problems:
        result["exit"] = EXIT_REJECTED
    write_log(cfg, {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "run_id": cfg.run_id, "mode": cfg.mode, "task_id": task_id,
                    "ok": result["exit"] == EXIT_OK, "exit": result["exit"],
                    "documents": result["documents"], "by_rule": result["by_rule"],
                    "broken": result["broken"][:3],
                    "reasons": [p[:300] for p in result["problems"][:20]]})
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gate-config", default=str(HERE / "gate.json"),
                    help="the locate gate.json (cf-gate-locate/1; default: next to this script)")
    ap.add_argument("--root", default=".", help="the worktree (or harvested corpus) root")
    ap.add_argument("--findings-dir", default=DEFAULT_FINDINGS_DIR,
                    help="with neither --findings nor --changed-files: the directory of "
                         "<task-id>/<source-id>.json documents under --root (a harvested corpus: '.')")
    ap.add_argument("--findings", action="append", default=[],
                    help="validate this document only (repeatable); its task is its directory")
    ap.add_argument("--changed-files", action="store_true",
                    help="validate the documents this contribution changed and refuse any other "
                         "path (rule 7)")
    ap.add_argument("--base", default="main", help="the branch the change set is read against")
    ap.add_argument("--task-id", default=os.environ.get("TEND_TASK_ID", ""),
                    help="the worker's own task id (default: $TEND_TASK_ID, else "
                         "orchestrator.sqlite, else the single-stem rule)")
    ap.add_argument("--allow-empty", action="store_true",
                    help="with --changed-files: tolerate a change set with no document")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    gate_path = pathlib.Path(args.gate_config).expanduser()
    try:
        if not gate_path.is_file():
            raise GateBroken(f"gate config missing at {gate_path}")
        cfg = GateConfig.load(gate_path)
    except GateBroken as exc:
        print(f"FAIL: {exc} — the gate could not run: this is not a finding of yours",
              file=sys.stderr)
        print("ownership: not checked (the gate could not run)")
        return EXIT_GATE_BROKEN
    result = run_gate(cfg, root=pathlib.Path(args.root).resolve(),
                      findings=[pathlib.Path(f) for f in args.findings],
                      findings_dir=args.findings_dir, changed_files=args.changed_files,
                      base=args.base, task_id=args.task_id, allow_empty=args.allow_empty)
    for broken in result["broken"]:
        print(f"FAIL: {broken} — the gate could not run: this is not a finding of yours",
              file=sys.stderr)
    if result["problems"]:
        print(f"VALIDATION FAILED ({len(result['problems'])} problem(s)):", file=sys.stderr)
        for line in result["problems"]:
            print("  - " + line, file=sys.stderr)
    elif result["exit"] == EXIT_OK and not args.quiet:
        unresolved = sum(1 for s in result["stats"] if s.get("unresolved"))
        print(f"OK: {result['documents']} locate document(s) validated against gate "
              f"{cfg.preset} ({cfg.mode}); every quote matched the pinned layer once and every "
              f"declaration is in the frozen universe {cfg.universe_id or '(none: inventory)'}"
              + (f"; {unresolved} unresolved" if unresolved else ""))
    # The LAST line says which ownership rule judged the contribution.
    if result["ownership"]:
        print(f"  changed files: {result['ownership']}")
    else:
        print("ownership: not checked (no --changed-files: these documents were validated, the "
              "change set was not)")
    return int(result["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
