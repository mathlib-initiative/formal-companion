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
  documents; 3 the gate could not run (no, unreadable or malformed gate.json,
  a vendored package that is not the pinned release, a layer missing or not
  the pinned bytes, a formula list or paragraph table missing or malformed, an
  unreadable corpus.sqlite, a missing task-manifest directory, a change set
  git cannot read safely, a gate directory missing one of its own modules, a
  crash of the gate itself).
  Every infrastructure failure arrives as 3, never as the interpreter's 1 out
  of a traceback, because the worker's prompt says 1 means "your documents
  are wrong"; an exit 1 always comes with a ``VALIDATION FAILED`` list.
  ``--self-check`` validates nothing and says whether the gate can run, which
  is what ``cf run``'s verify stage asks of the host, under ``python3 -I``.

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
   ``gate.json``; a coverage claim sits on a located passage and names at
   least two of its ``hasPart`` locations, with a note in the worker's own
   words, and no challenge document confirms one while refuting a part;
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
   never from the document): an inventory keeps a ``subject`` passage scoped
   ``formal_math`` and scopes no ``subject`` passage out; a locate document
   leaves no hit handed to its task unhandled -- each is located with a
   relation that counts (a ``relatedMatch`` does not) or rejected with a
   reason -- and an ``accepted`` candidate is one of its passage's locations;
   off in the blind arm;
5. an absence lists at least three distinct queries, and the document names
   the frozen universe;
6. at most five locations per passage with distinct ranks 1..5, one per
   declaration;
7. the changed files are the task's own: ``findings/<task-id>/<source-id>.json``
   for the sources its manifest names and ``tasks/<task-id>.yaml``, whose
   change is its ``status:`` line; the task id comes from
   ``--task-id``/``TEND_TASK_ID``, else from tend's ``orchestrator.sqlite`` (an
   ASSUMED schema, looked up defensively; verify it on the host), else the
   single-stem rule (one task per change set; ``--pre-merge``, the batched
   pre-merge check, admits several tasks' new documents) -- and the last line
   says which;
8. an inventory drops no deterministic-finder passage of its task without a
   skip covering its span, every ``math`` paragraph is covered by a passage or
   a skip, and no skip holds the whole span of a passage the inventory keeps.

One JSON line per call is appended to ``gate.json``'s ``log_dir`` when that is
writable (the watchdog and the bench read it); a log that cannot be written
never fails the gate.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import pathlib
import re
import sqlite3
import stat
import sys
import time
import urllib.parse
from collections import Counter
from typing import Any, Callable, Iterable

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
#: the vendored packages: beside this file in a gate directory, else the checkout's
VENDOR = HERE / "vendor" if (HERE / "vendor").is_dir() else HERE.parent.parent / "vendor"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

#: What went wrong importing the gate's siblings (references/locate-documents.md
#: section 9: the gate directory holds locate_text.py, verify_findings.py and
#: locate_records.py beside this file).  A missing or broken one is an
#: infrastructure failure: ``main`` turns it into exit 3 with ONE ``FAIL`` line,
#: never the interpreter's exit 1 out of a traceback -- the code a worker is told
#: means "your documents are wrong".
IMPORT_ERRORS: list[str] = []
try:
    import locate_text as lt  # noqa: E402
except Exception as _exc:  # noqa: BLE001 - any failure to import is the same failure
    lt = None  # type: ignore[assignment]
    IMPORT_ERRORS.append(f"locate_text.py ({_exc.__class__.__name__}: {_exc})")
try:
    import verify_findings as vf  # noqa: E402
except Exception as _exc:  # noqa: BLE001
    vf = None  # type: ignore[assignment]
    IMPORT_ERRORS.append(f"verify_findings.py ({_exc.__class__.__name__}: {_exc})")


def _load_records():
    """The export's record builder: ``locate_records.py`` staged here, else the checkout's.

    The gate directory carries ``scripts/locate/records.py`` as
    ``locate_records.py``; the harvest's in-process gate runs from the skill
    checkout, where the same file sits at ``scripts/locate/records.py``.
    """
    try:
        import locate_records  # noqa: PLC0415
        return locate_records
    except ModuleNotFoundError as exc:
        if exc.name != "locate_records":
            raise
    path = HERE.parent / "locate" / "records.py"
    if not path.is_file():
        raise ModuleNotFoundError("no locate_records.py beside the gate, and no checkout "
                                  "scripts/locate/records.py")
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("locate_records", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["locate_records"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


try:
    lr = _load_records()
except Exception as _exc:  # noqa: BLE001
    lr = None
    IMPORT_ERRORS.append(f"locate_records.py ({_exc.__class__.__name__}: {_exc})")

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_GATE_BROKEN = 3

#: what a gate directory must hold beside this file, besides gate.json, tasks/
#: and vendor/ (scripts/locate/tend_root.py stages them; a test pins the two).
#: A missing or broken one is in IMPORT_ERRORS above: exit 3, never a verdict.
GATE_MODULES = ("locate_text.py", "verify_findings.py", "locate_records.py")

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
#: how strong a relation is, strongest first: a challenge's ``downgrade`` names a
#: relation of a HIGHER number than the record's (references/locate-documents.md
#: section 4); cflib.vocab.RELATION_STRENGTH, inlined and pinned by a test
RELATION_STRENGTH = {"exactMatch": 0, "closeMatch": 1, "broadMatch": 2, "narrowMatch": 3,
                     "hasPart": 3, "relatedMatch": 4}
RELATED_MATCH = "relatedMatch"
CLOSE_MATCH = "closeMatch"
NO_MATCH = "noMatch"
LOCATE_OUTCOMES = frozenset({"located", "absent", "unresolved"})
FINDERS = frozenset({"theorem_box", "bold_definition", "lead", "model"})
DETERMINISTIC_FINDERS = frozenset({"theorem_box", "bold_definition", "lead"})
CONFIDENCES = frozenset({"high", "medium", "low"})
CANDIDATE_VERDICTS = frozenset({"accepted", "rejected"})
CHALLENGE_TARGETS = frozenset({"location", "absence", "unresolved", "scope", "prominence",
                               "coverage"})
CHALLENGE_ACTIONS = frozenset({"confirm", "uphold", "downgrade", "refute", "overturn"})
#: which actions a target admits (references/locate-documents.md section 4)
CHALLENGE_COMBOS = {
    "location": frozenset({"confirm", "downgrade", "refute"}),
    "absence": frozenset({"uphold", "overturn"}),
    "unresolved": frozenset({"uphold", "overturn"}),
    "scope": frozenset({"uphold", "overturn"}),
    "prominence": frozenset({"uphold", "overturn"}),
    # a passage's coverage claim (cflib.vocab.COVERAGE_ACTIONS): refuted by default
    "coverage": frozenset({"confirm", "refute"}),
}
UNRESOLVED_BY = frozenset({"worker", "attempt_cap"})
HAS_PART = "hasPart"
#: a coverage claim's keys and its least number of parts (cflib.vocab.MIN_COVERAGE_PARTS;
#: references/locate-documents.md section 3)
COVERAGE_KEYS = frozenset({"complete", "parts", "note"})
MIN_COVERAGE_PARTS = 2
UNRESOLVED_ATTEMPT_CAP = "attempt_cap"
#: cflib.vocab.LOCATE_ATTEMPT_CAP: the genuine reviewer rejections at which the
#: bridge ends a task UNRESOLVED (scripts/run/attempt_cap.py)
LOCATE_ATTEMPT_CAP = 3
#: the reason ``attempt_cap.cap_reason`` writes; a test pins the two together
ATTEMPT_CAP_REASON = re.compile(r"attempt_cap: ([0-9]+) genuine reviewer rejections; the bridge "
                                r"ended the task without another agent call\Z")
MAX_LOCATIONS_PER_PASSAGE = 5
MIN_ABSENCE_QUERIES = 3
LOCATE_EXTENSION = "org.mathlib-initiative.formal-companion.locate"

#: the one spelling of a passage item a stage writes (``items.passage_item``)
ITEM = re.compile(r"In-text #[0-9a-f]{8}\Z")
QID = re.compile(r"Q[1-9][0-9]*\Z")
#: keys no worker document may carry anywhere: the proof status is joined from
#: the index at export, and the commit is the universe's
FORBIDDEN_KEYS = ("proof_status", "commit", "version")
NOTES_MAX = 2000
#: problems kept (and printed) per gate call; the rest are counted by rule
MAX_PROBLEMS = 200
#: the largest task file rule 7 reads to compare it with the merge base
MAX_TASK_FILE_BYTES = 1024 * 1024
#: defaults gate.json may override
MAX_QUOTE_CHARS = 1500
MAX_CONTEXT_CHARS = 200
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
#: tend's validation runs this from the worktree; a harvested corpus keeps the
#: task directories at its top
DEFAULT_FINDINGS_DIR = "findings"
NON_DOCUMENT_FILES = frozenset(vf.NON_FINDING_FILES) if vf is not None else frozenset()
VENDORED_PACKAGES = ("literature_links", "formal_companion_annotations")


class GateBroken(Exception):
    """The gate cannot run: an infrastructure failure, never a verdict on the documents."""


def ro_uri(path: pathlib.Path, *, immutable: bool = True) -> str:
    """A read-only SQLite URI for ``path``, with the path percent-quoted.

    Unquoted, a ``?`` or ``#`` in a directory name cuts the query string off --
    SQLite then opens the truncated path READ-WRITE and creates an empty file --
    and a ``%xx`` decodes to another path.  ``immutable=1`` (the default) is for
    a database nobody writes while the gate reads it; tend's live
    ``orchestrator.sqlite`` is opened without it (:func:`_open_orchestrator`).
    """
    return ("file:" + urllib.parse.quote(str(pathlib.Path(path).resolve()))
            + ("?mode=ro&immutable=1" if immutable else "?mode=ro"))


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


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text[:24]} is not a finite JSON number")
    return value


#: deeper than this a value is refused before anything walks it; the formats
#: nest about six levels
MAX_JSON_DEPTH = 64


def _check_tree(value: Any) -> None:
    """Refuse a value nested deeper than :data:`MAX_JSON_DEPTH` or holding a lone surrogate.

    Iterative, so no worker document can exhaust the interpreter's stack; a
    lone surrogate (a ``\\ud800`` escape) is not UTF-8 text, and every writer
    downstream encodes UTF-8.
    """
    stack: list[tuple[Any, int, str]] = [(value, 1, "$")]
    while stack:
        node, depth, where = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"nested deeper than {MAX_JSON_DEPTH} levels at {where}")
        if isinstance(node, dict):
            for key, child in node.items():
                _check_text(key, f"a key under {where}")
                stack.append((child, depth + 1, f"{where}.{key[:40]}"))
        elif isinstance(node, list):
            stack.extend((child, depth + 1, f"{where}[{i}]") for i, child in enumerate(node))
        elif isinstance(node, str):
            _check_text(node, where)


def _check_text(text: str, where: str) -> None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{where} holds a lone surrogate (not UTF-8 text)") from None


def load_json_strict(text: str) -> Any:
    """JSON a second reader reads the same way: no duplicate key, no NaN/Infinity and
    no number that overflows to one, no lone surrogate, at most :data:`MAX_JSON_DEPTH`
    levels deep.  Any of them is a ``ValueError``."""
    try:
        value = json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_no_constant,
                           parse_float=_finite_float)
    except RecursionError:
        raise ValueError(f"nested deeper than {MAX_JSON_DEPTH} levels") from None
    _check_tree(value)
    return value


def _one_of(value: Any, allowed) -> bool:
    """``value in allowed`` for a worker-written value that may be a list or an object."""
    return isinstance(value, str) and value in allowed


def _positive_int(data: dict, key: str, default: int) -> int:
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GateBroken(f"gate.json {key} is {value!r}, not a positive integer")
    return value


def _path_field(value: Any, key: str) -> pathlib.Path:
    if not isinstance(value, str) or not value:
        raise GateBroken(f"gate.json {key} is {value!r}, not a path")
    return pathlib.Path(value).expanduser()


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
        kinds = data.get("kinds")
        if kinds is not None and not (isinstance(kinds, list)
                                      and all(isinstance(k, str) for k in kinds)):
            raise GateBroken(f"gate.json kinds is {kinds!r}, not a list of kinds")
        self.kinds = frozenset(kinds or KINDS_BY_MODE[self.mode])
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
        self.corpus_db = (_path_field(corpus["path"], "corpus_db.path") if corpus.get("path")
                          else None)
        self.corpus_db_sha = str(corpus.get("sha256") or "")
        sourceset = data.get("sourceset") if isinstance(data.get("sourceset"), dict) else {}
        if not sourceset.get("sources_dir"):
            raise GateBroken("gate.json sourceset has no sources_dir")
        self.sourceset = sourceset
        self.sources_dir = _path_field(sourceset["sources_dir"], "sourceset.sources_dir")
        sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}
        if not sources:
            raise GateBroken("gate.json pins no sources (the gate fails closed)")
        self.sources: dict[str, dict] = {str(k): dict(v) for k, v in sources.items()
                                         if isinstance(v, dict)}
        self.candidates_dir = (_path_field(data["candidates_dir"], "candidates_dir")
                               if data.get("candidates_dir") else None)
        self.blind = bool(data.get("blind"))
        if not data.get("tasks_dir"):
            raise GateBroken("gate.json names no tasks_dir (the task manifests)")
        self.tasks_dir = _path_field(data["tasks_dir"], "tasks_dir")
        self.log_dir = _path_field(data["log_dir"], "log_dir") if data.get("log_dir") else None
        self.orchestrator_db = (_path_field(data["orchestrator_db"], "orchestrator_db")
                                if data.get("orchestrator_db") else None)
        self.max_locations = _positive_int(data, "max_locations", MAX_LOCATIONS_PER_PASSAGE)
        self.min_absence_queries = _positive_int(data, "min_absence_queries", MIN_ABSENCE_QUERIES)
        self.max_quote_chars = _positive_int(data, "max_quote_chars", MAX_QUOTE_CHARS)
        self.max_context_chars = _positive_int(data, "max_context_chars", MAX_CONTEXT_CHARS)
        self.max_document_bytes = _positive_int(data, "max_document_bytes", MAX_DOCUMENT_BYTES)
        self.vendor = data.get("vendor") if isinstance(data.get("vendor"), dict) else {}

    @classmethod
    def load(cls, path: pathlib.Path) -> "GateConfig":
        try:
            data = load_json_strict(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateBroken(f"gate config at {path} is unreadable ({exc})") from None
        try:
            return cls(data, path)
        except (TypeError, ValueError, AttributeError, KeyError) as exc:
            # a value of the wrong shape is a broken gate.json (exit 3), never a
            # traceback the worker would read as exit 1
            raise GateBroken(f"gate config at {path} has a malformed value "
                             f"({exc.__class__.__name__}: {exc})") from None


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
        if not isinstance(formulas, list) or not all(isinstance(f, dict) for f in formulas):
            raise GateBroken(f"{source_id}: formulas.json is not a list of formula entries")
        try:
            self.map = lt.HeadMap(self.layer, formulas)
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
            if not isinstance(data, list) or not all(isinstance(p, dict) for p in data):
                raise GateBroken(f"{self.source_id}: paragraphs.json is not a list of block "
                                 f"entries")
            self._paragraphs = data
        return self._paragraphs


class Context:
    """Everything one gate call loads lazily: sources, candidates, manifests, the corpus."""

    def __init__(self, cfg: GateConfig, *, check_counter: bool = False):
        self.cfg = cfg
        #: read the attempt counter for a ``by: attempt_cap`` document (tend's
        #: validation runs beside it; a harvested corpus has none)
        self.check_counter = check_counter
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
                    self._db = sqlite3.connect(ro_uri(self.cfg.corpus_db), uri=True)
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
    """``(rule, message)`` pairs, printed as ``[rule N] <where>: <message>``.

    At most :data:`MAX_PROBLEMS` are kept; the rest are only counted, by rule, so
    a document that is wrong a million times over costs a million counts, not a
    million lines on stderr.
    """

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []
        self.more: dict[str, int] = {}

    def add(self, rule: int | str, where: str, message: str) -> None:
        if len(self.items) >= MAX_PROBLEMS:
            self.more[str(rule)] = self.more.get(str(rule), 0) + 1
            return
        self.items.append((str(rule), f"{where}: {message}"))

    def extend(self, other: "Problems") -> None:
        for rule, message in other.items:
            if len(self.items) >= MAX_PROBLEMS:
                self.more[rule] = self.more.get(rule, 0) + 1
            else:
                self.items.append((rule, message))
        for rule, count in other.more.items():
            self.more[rule] = self.more.get(rule, 0) + count

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items) + sum(self.more.values())

    def lines(self) -> list[str]:
        out = [f"[rule {rule}] {message}" for rule, message in self.items]
        if self.more:
            out.append(f"... and {sum(self.more.values())} more problem(s) not listed ("
                       + ", ".join(f"rule {r}: {n}" for r, n in sorted(self.more.items()))
                       + f"); the first {MAX_PROBLEMS} are above")
        return out

    def by_rule(self) -> dict[str, int]:
        out: dict[str, int] = dict(self.more)
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


def _union_length(spans: list[tuple[int, int]]) -> int:
    """How many layer characters the spans cover together."""
    total, reach = 0, -1
    for a, b in sorted(spans):
        if b <= reach:
            continue
        total += b - max(a, reach)
        reach = b
    return total


class _SpanIndex:
    """Non-empty half-open layer spans, sorted once, for rule 8's three questions.

    Each skip, finder and math paragraph asks one question of the kept passages
    or of the skips; asked by scanning, a document of n passages and n skips cost
    O(n^2), so a 4 MB document could hold the gate for minutes.  Sorted by start,
    with the furthest end of every prefix and the nearest end of every suffix,
    each question is one bisection.
    """

    def __init__(self, spans: Iterable[tuple[int, int]]) -> None:
        self.spans = sorted(set(spans))
        self.starts = [a for a, _b in self.spans]
        #: index of the span reaching furthest among spans[:i + 1]
        self._furthest: list[int] = []
        best = -1
        for i, (_a, b) in enumerate(self.spans):
            if best < 0 or b > self.spans[best][1]:
                best = i
            self._furthest.append(best)
        #: index of the span ending first among spans[i:]
        self._first_end = [0] * len(self.spans)
        best = -1
        for i in range(len(self.spans) - 1, -1, -1):
            if best < 0 or self.spans[i][1] <= self.spans[best][1]:
                best = i
            self._first_end[i] = best

    def overlapping(self, span: tuple[int, int]) -> tuple[int, int] | None:
        """A span sharing a character with ``span``, else None."""
        i = bisect.bisect_left(self.starts, span[1])           # spans[:i] start before its end
        if i == 0:
            return None
        hit = self.spans[self._furthest[i - 1]]
        return hit if hit[1] > span[0] else None

    def covering(self, span: tuple[int, int]) -> tuple[int, int] | None:
        """A span holding the whole of ``span``, else None."""
        i = bisect.bisect_right(self.starts, span[0])          # spans[:i] start at or before it
        if i == 0:
            return None
        hit = self.spans[self._furthest[i - 1]]
        return hit if hit[1] >= span[1] else None

    def inside(self, span: tuple[int, int]) -> tuple[int, int] | None:
        """A span lying wholly inside ``span``, else None."""
        i = bisect.bisect_left(self.starts, span[0])           # spans[i:] start at or after it
        if i == len(self.spans):
            return None
        hit = self.spans[self._first_end[i]]
        return hit if hit[1] <= span[1] else None


# --------------------------------------------------------------------------- #
# the preview records (rule 1): what the export will build, checked here
# --------------------------------------------------------------------------- #

#: what the gate stands in for when it builds a record the worker's document
#: implies: the challenge, the location key and the joined proof status are the
#: harvest's and the export's to fill, never the worker's
_PLACEHOLDER_SHA = "0" * 64
_WEB_SOURCE_ID = re.compile(r"[a-z][a-z0-9]{1,23}-([1-9][0-9]*)-([1-9][0-9]*)\Z")


def capped_quote(layer: str, span: tuple[int, int]) -> dict:
    """The export's quote of a passage (``locate_records.cap_quote``); RecordError when none."""
    return lr.cap_quote(layer, span[0], span[1])


def _preview_source(cfg: GateConfig, source_id: str) -> dict:
    pin = cfg.sources.get(source_id) or {}
    match = _WEB_SOURCE_ID.match(source_id)
    return {"source_id": source_id, "pageid": int(match.group(1)) if match else 0,
            "revision": match.group(2) if match else "0",
            "title": pin.get("title") or source_id,
            "html_sha256": pin.get("html_sha256") or _PLACEHOLDER_SHA,
            "renderer": pin.get("renderer") or "", "uri": pin.get("uri") or source_id,
            "container_title": pin.get("container_title") or "",
            "licence": pin.get("licence") or ""}


def _preview_universe(cfg: GateConfig) -> dict:
    return {"id": cfg.universe_id or "inventory", "sha256": cfg.universe_sha or _PLACEHOLDER_SHA,
            "libraries": [{"id": lib, **body} for lib, body in sorted(cfg.libraries.items())]}


def _own_words(exc: Exception, where: str, item: str) -> str:
    """Rule 1's message for a free text the export refuses as the source verbatim.

    The export's message starts with the item (``locate_records._free_text``);
    a ``where`` that already ends with it (a locate document's passage) names it
    once, while a challenge verdict's ``verdicts[i]`` keeps it.
    """
    message, lead = str(exc), f"{item}: "
    if where.endswith(f": {item}") and message.startswith(lead):
        message = message[len(lead):]
    return f"{message}; the export refuses it: say it in your own words"


def check_records(cfg: GateConfig, problems: Problems, where: str, *, source_id: str,
                  layer: str, span: tuple[int, int], quote: dict, item: str,
                  passage_kind: Any, outcome: str | None, locations: list[dict],
                  receipt: dict | None = None) -> None:
    """Rule 1: build the records this passage implies with the EXPORT'S builder, and check them.

    ``locate_records`` (``scripts/locate/records.py``, staged in the gate
    directory) is the code ``cf export literature-links`` runs, so a document
    this passes is one the export can turn into records the vendored checker
    accepts.  That includes the export's cap on free text: every location's note
    goes through ``locate_records.check_note``, and an absence's receipt is built
    by ``locate_records.receipt_document`` -- the export's own two calls -- so a
    note, a query or a candidate's reason that carries more than the quote cap of
    the source verbatim is refused here, while the worker can still rewrite it.
    What the worker does not control -- the challenge value, the location key,
    the proof status joined from the index, the source's citation fields when
    ``gate.json`` does not carry them -- is a placeholder.
    """
    source = _preview_source(cfg, source_id)
    universe = _preview_universe(cfg)
    passage = {"item": item, "source_id": source_id, "challenge": "not_required",
               "passage_kind": passage_kind if passage_kind in PASSAGE_KINDS else "other",
               "absence": {"challenge": "not_required"}}
    span_sha = lr.sha256_text(layer[span[0]:span[1]])
    verbatim = False
    for loc in locations:
        try:
            lr.check_note(item, loc.get("note") if isinstance(loc.get("note"), str) else "",
                          layer)
        except lr.RecordError as exc:
            problems.add(1, where, _own_words(exc, where, item))
            verbatim = True
    reference = {"sha256": _PLACEHOLDER_SHA, "queries": 0, "candidates": 0}
    if outcome == "absent":
        try:
            _digest, _text, reference = lr.receipt_document(source_id, item, receipt or {},
                                                            universe, layer)
        except lr.RecordError as exc:
            problems.add(1, where, _own_words(exc, where, item))
            verbatim = True
    if verbatim:
        return
    records: list[dict] = []
    try:
        for loc in locations:
            body = cfg.libraries.get(str(loc.get("library") or ""))
            if body is None:
                continue                      # rule 3 names the library already
            location = {"declaration": str(loc.get("declaration") or ""),
                        "relation": str(loc.get("relation") or ""),
                        "proof_status": "proved", "location_key": _PLACEHOLDER_SHA,
                        "challenge": "not_required",
                        "confidence": loc.get("confidence") if isinstance(loc.get("confidence"), str) else None,
                        "note": loc.get("note") if isinstance(loc.get("note"), str) else None}
            if isinstance(loc.get("rank"), int) and not isinstance(loc.get("rank"), bool):
                location["rank"] = loc["rank"]
            records.append(lr.location_record(source, passage, location,
                                              {"id": str(loc["library"]), **body}, universe, quote,
                                              span_sha256=span_sha))
        if outcome == "absent" and universe["libraries"]:
            records.append(lr.absence_record(source, passage, universe, quote,
                                             span_sha256=span_sha, receipt=reference))
        elif not locations:
            records.append(lr.unresolved_record(source, passage, universe, quote,
                                                span_sha256=span_sha))
    except (lr.RecordError, KeyError, TypeError, ValueError) as exc:
        problems.add(1, where, f"the record this implies cannot be built: {exc}")
        return
    for record in records:
        for problem in lr.check(record):
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


def attempts_counter(cfg: GateConfig, task_id: str) -> pathlib.Path:
    """``<tend-root>/attempts/<task-id>.json``: the bridge's counter of genuine rejections.

    The bridge keeps it under its own root, the tend root its wrappers export as
    ``CF_BRIDGE_ROOT`` (the directory above ``bin/``).  ``gate.json`` names that
    directory through ``orchestrator_db``, which ``plan.host_layout`` puts at
    ``<tend root>/orchestrator.sqlite`` in both layouts: ``runs/<run>/tend-root/``
    on this machine, where the gate directory lies inside it, and
    ``<root>/<run>-tend/`` on a run host, where the gate directory is its sibling
    ``<root>/<run>-gate/`` -- so the counter is never looked for from the gate
    directory (references/locate-documents.md sections 1 and 9).  A ``gate.json``
    with no ``orchestrator_db`` cannot say where the counter is: that is a gate
    the planner did not write, and a broken one (exit 3).
    """
    if cfg.orchestrator_db is None:
        raise GateBroken("gate.json names no orchestrator_db, so the tend root holding the "
                         "bridge's attempts/ counter is unknown; a by: attempt_cap document "
                         "cannot be checked")
    return cfg.orchestrator_db.parent / "attempts" / f"{task_id}.json"


def _counted_rejections(cfg: GateConfig, task_id: str) -> int:
    counter = attempts_counter(cfg, task_id)
    try:
        data = json.loads(counter.read_text(encoding="utf-8"))
        count = data.get("rejections") if isinstance(data, dict) else 0
        return count if isinstance(count, int) and not isinstance(count, bool) else 0
    except (OSError, ValueError):
        return 0


def _unresolved_block(doc: dict, name: str, problems: Problems, cfg: GateConfig, task_id: str,
                      *, check_counter: bool) -> bool:
    """Is the document unresolved?  Its block's form, and whose it is, are checked here.

    ``by: attempt_cap`` is the bridge's (``scripts/run/attempt_cap.py``), never a
    worker's: its reason is the text the bridge writes, at a count of at least
    :data:`LOCATE_ATTEMPT_CAP`, and with ``check_counter`` (tend's validation,
    which runs beside the counter) the task's counter is at the cap too.
    """
    block = doc.get("unresolved")
    if block is None:
        return False
    if not isinstance(block, dict) or not _nonempty_str(block.get("reason")) \
            or not _one_of(block.get("by"), UNRESOLVED_BY):
        problems.add(1, name, "unresolved must be {reason: <text>, by: worker|attempt_cap}")
        return True
    if block["by"] == UNRESOLVED_ATTEMPT_CAP:
        said = ATTEMPT_CAP_REASON.match(block["reason"])
        if said is None or int(said.group(1)) < LOCATE_ATTEMPT_CAP:
            problems.add(1, name, "unresolved.by attempt_cap is written by the bridge at the "
                                  "attempt cap, never by a worker: a worker that cannot finish "
                                  "writes by: worker with its reason")
        elif check_counter:
            counted = _counted_rejections(cfg, task_id)
            if counted < LOCATE_ATTEMPT_CAP:
                problems.add(1, name, f"unresolved.by attempt_cap, but task {task_id} has "
                                      f"{counted} counted rejection(s), under the cap of "
                                      f"{LOCATE_ATTEMPT_CAP}: a worker that cannot finish "
                                      f"writes by: worker with its reason")
    return True


def _check_location(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                    location: Any) -> None:
    if not isinstance(location, dict):
        problems.add(1, where, "a location is an object")
        return
    library = location.get("library")
    declaration = location.get("declaration")
    if not _one_of(library, cfg.libraries):
        problems.add(3, where, f"library {library!r} is not in universe {cfg.universe_id} "
                               f"({', '.join(sorted(cfg.libraries))})")
    elif not _nonempty_str(declaration):
        problems.add(1, where, "a location names a declaration")
    elif not ctx.exists(library, declaration):
        problems.add(3, where, f"{library}:{declaration} is not in corpus.sqlite at "
                               f"{cfg.libraries[library]['commit'][:12]} (a phantom declaration)")
    if not _one_of(location.get("relation"), LOCATE_RELATIONS):
        problems.add(1, where, f"relation {location.get('relation')!r} is not a locate relation")
    if location.get("relation") == CLOSE_MATCH and not _nonempty_str(location.get("note")):
        problems.add(6, where, "closeMatch needs a note naming the convention difference")
    rank = location.get("rank")
    if not (isinstance(rank, int) and not isinstance(rank, bool)
            and 1 <= rank <= cfg.max_locations):
        problems.add(6, where, f"rank {rank!r} is not 1..{cfg.max_locations}")
    if not _one_of(location.get("confidence"), CONFIDENCES):
        problems.add(1, where, f"confidence {location.get('confidence')!r} is not "
                               f"{'/'.join(sorted(CONFIDENCES))}")
    if not _nonempty_str(location.get("witness")):
        problems.add(1, where, "a location carries a witness")
    if "note" in location and not isinstance(location.get("note"), str):
        problems.add(1, where, "note is a string")


def _check_locations(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                     locations: Any) -> list[dict] | None:
    """The passage's locations, checked; None when there are more than the cap.

    Over the cap the passage is refused (rule 6) and nothing else of it is read:
    each location costs a check and a record, and a document within the byte cap
    can hold a million of them.
    """
    if not isinstance(locations, list):
        problems.add(1, where, "locations is a list")
        return []
    if len(locations) > cfg.max_locations:
        problems.add(6, where, f"{len(locations)} locations; at most {cfg.max_locations} per "
                               f"passage (none of them was checked)")
        return None
    ranks = [loc.get("rank") for loc in locations if isinstance(loc, dict)]
    if len(ranks) != len(set(map(repr, ranks))):
        problems.add(6, where, f"ranks {ranks} are not distinct")
    # one location per declaration: two would share one location_key, and the
    # challenge plan refuses a key it sees twice
    named = Counter(f"{loc.get('library')}:{loc.get('declaration')}" for loc in locations
                    if isinstance(loc, dict))
    repeated = sorted(n for n, count in named.items() if count > 1)
    if repeated:
        problems.add(6, where, f"{', '.join(repeated)} is located more than once; one location "
                               f"per declaration")
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
        if not _one_of(cand.get("verdict"), CANDIDATE_VERDICTS):
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


def _rank_of(location: dict) -> int | None:
    rank = location.get("rank")
    return rank if isinstance(rank, int) and not isinstance(rank, bool) else None


def _check_coverage(problems: Problems, where: str, coverage: Any, *, outcome: Any,
                    locations: list[dict], layer: str) -> None:
    """Rule 1: a passage's coverage claim (the maintainer's decision D1).

    ``{"complete": true, "parts": [<rank>, ...], "note": "<one sentence>"}`` on a
    LOCATED passage only: at least two distinct parts, each the rank of one of the
    passage's ``hasPart`` locations, and a note in the worker's own words -- at
    most ``NOTES_MAX`` characters, and no more of the source verbatim than the
    export's quote cap (``locate_records.verbatim_run``).  A claim upgrades the
    passage's derived status once a challenger confirms it, so a malformed one is
    refused here, while the worker can still fix it.
    """
    at = f"{where}: coverage"
    if outcome != "located":
        problems.add(1, at, f"a coverage claim belongs to a located passage; this one is "
                            f"{outcome}")
        return
    if not isinstance(coverage, dict):
        problems.add(1, at, "coverage is {complete: true, parts: [<ranks>], note: <one "
                            "sentence>}")
        return
    extra = sorted(set(coverage) - COVERAGE_KEYS)
    if extra:
        problems.add(1, at, f"carries {', '.join(map(repr, extra))}; a claim is complete, "
                            f"parts and note, nothing else")
    if coverage.get("complete") is not True:
        problems.add(1, at, "complete is true: a passage claims coverage only when its parts "
                            "together state every clause")
    parts = coverage.get("parts")
    if not isinstance(parts, list) or not all(isinstance(r, int) and not isinstance(r, bool)
                                              for r in parts):
        problems.add(1, at, "parts is a list of the ranks of this passage's hasPart locations")
    else:
        if len(set(parts)) != len(parts):
            problems.add(1, at, f"parts {parts} names a rank twice")
        if len(set(parts)) < MIN_COVERAGE_PARTS:
            problems.add(1, at, f"a coverage claim names at least {MIN_COVERAGE_PARTS} parts "
                                f"(one location that states the whole passage is an "
                                f"exactMatch, not a hasPart)")
        by_rank = {_rank_of(loc): loc for loc in locations if _rank_of(loc) is not None}
        for rank in sorted(set(parts)):
            loc = by_rank.get(rank)
            if loc is None:
                problems.add(1, at, f"part {rank} is the rank of no location of this passage")
            elif loc.get("relation") != HAS_PART:
                problems.add(1, at, f"part {rank} is a {loc.get('relation')} location; a "
                                    f"claim's parts are its hasPart locations")
    note = coverage.get("note")
    if not _nonempty_str(note):
        problems.add(1, at, "note says, in one sentence of your own, how the parts together "
                            "state every clause")
    elif len(note) > NOTES_MAX:
        problems.add(1, at, f"note is {len(note)} characters; at most {NOTES_MAX}")
    else:
        run = lr.verbatim_run(note, layer)
        if run is not None:
            problems.add(1, at, f"note carries {len(run.split())}+ words of the source verbatim "
                                f"({run[:40]!r}...), past the {lr.QUOTE_WORDS}-word quote cap: "
                                f"say it in your own words")


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
            if not _one_of(p.get(key), allowed):
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
        # Rule 1: the export quotes this passage with its own capped quote, so a
        # passage it cannot quote uniquely is refused NOW, while the worker can
        # still move the start, not after challenge at `cf export`.
        try:
            exported = capped_quote(layer, span)
        except lr.RecordError as exc:
            problems.add(1, where, f"the export cannot quote this passage ({exc}); start it "
                                   f"where its first words are distinctive")
            continue
        check_records(cfg, problems, where, source_id=source_id, layer=layer, span=span,
                      quote=exported, item=str(item), passage_kind=p.get("passage_kind"),
                      outcome=None, locations=[])
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
    kept_by = _SpanIndex(spans)
    skip_spans: list[tuple[int, int]] = []
    for i, skip in enumerate(skips):
        at = f"{name}: skips[{i}]"
        span = _span(skip.get("span") if isinstance(skip, dict) else None, len(layer))
        if span is None:
            problems.add(1, at, "a skip is {span: [start, end] inside the layer, reason}")
            continue
        if not _nonempty_str(skip.get("reason")):
            problems.add(1, at, "a skip carries its reason")
        # A skip says "no passage here": one that holds a whole kept passage
        # contradicts it, and a skip of the whole layer beside the one passage
        # rule 4 asks for would otherwise meet the coverage floor alone.  A skip
        # INSIDE a kept passage stays allowed: it is how a finder nested in one
        # (a bolded definition in a theorem box) is dropped.
        held = kept_by.inside(span)
        if held is not None:
            problems.add(8, at, f"the skip {list(span)} holds the whole kept passage at "
                                f"{list(held)}: a skip drops text no passage keeps")
        skip_spans.append(span)
    skipped_by = _SpanIndex(skip_spans)
    skipped_finders = 0
    # rule 8: the deterministic finders' passages and the coverage floor
    for j, finder in enumerate(manifest_doc.get("finders") or []):
        if not isinstance(finder, dict):
            continue
        fspan = _span(finder.get("span"), len(layer))
        if fspan is None:
            raise GateBroken(f"task manifest finder {j} of {source_id} has no span in the layer")
        start = finder.get("start", fspan[0])
        kept = start in span_starts
        skipped = skipped_by.covering(fspan) is not None
        if not (kept or skipped):
            problems.add(8, name, f"the {finder.get('finder')} passage at {list(fspan)} was "
                                  f"dropped with no skip covering its span")
        elif skipped and not kept:
            skipped_finders += 1
    finder_starts = {(f.get("finder"), f.get("start", (f.get("span") or [None])[0]))
                     for f in manifest_doc.get("finders") or [] if isinstance(f, dict)}
    for i, p, _h in rows:
        if _one_of(p.get("finder"), DETERMINISTIC_FINDERS) \
                and (p.get("finder"), p["span"][0]) not in finder_starts:
            problems.add(8, f"{name}: passages[{i}]",
                         f"finder {p.get('finder')} is claimed, but no {p.get('finder')} passage of "
                         f"the task starts at {p['span'][0]}; a passage the enumerator found is "
                         f"`model`")
    skipped_math = 0
    for k, paragraph in enumerate(source.paragraphs()):
        if not paragraph.get("math"):
            continue
        pspan = _span(paragraph.get("span"), len(layer))
        if pspan is None:
            raise GateBroken(f"{source_id}: paragraphs.json entry {k} has no span in the layer")
        if kept_by.overlapping(pspan) is not None:
            continue
        if skipped_by.overlapping(pspan) is not None:
            skipped_math += 1
            continue
        problems.add(8, name, f"the math paragraph at {list(pspan)} (section "
                              f"{paragraph.get('section') or []}) is covered by no passage and "
                              f"no skip (the coverage floor)")
    # what the floor's skips removed, for the harvest receipt (gate_corpus)
    stats["skipped"] = {"skips": len(skip_spans), "chars": _union_length(skip_spans),
                        "math_paragraphs": skipped_math, "finder_passages": skipped_finders}
    # rule 4, inventory side
    curated = _curated(ctx, source_id)
    if curated:
        if not any(isinstance(p, dict) and _one_of(p.get("scope"), LOCATED_SCOPES)
                   for p in passages):
            problems.add(4, name, f"L2 holds {len(curated)} curated hit(s) for this article "
                                  f"({_hits(curated)}), so the inventory keeps a formal_math passage")
        for i, p in enumerate(passages):
            if isinstance(p, dict) and p.get("prominence") == "subject" \
                    and not _one_of(p.get("scope"), LOCATED_SCOPES):
                problems.add(4, f"{name}: passages[{i}]",
                             f"the subject passage is scoped {p.get('scope')} while L2 holds a "
                             f"curated hit for the article ({_hits(curated)})")
        # The hits are for the article's OWN subject, so the inventory names a
        # subject passage it keeps in formal math; relabelling the subject
        # `central` must not turn the half above off.
        if not any(isinstance(p, dict) and p.get("prominence") == "subject"
                   and _one_of(p.get("scope"), LOCATED_SCOPES) for p in passages):
            problems.add(4, name, f"L2 holds {len(curated)} curated hit(s) for this article "
                                  f"({_hits(curated)}), so a passage of prominence subject is "
                                  f"kept, scoped formal_math")
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
        try:
            quote = capped_quote(source.layer, span)
        except lr.RecordError as exc:
            raise GateBroken(f"task manifest passage {p['item']} of {source_id} cannot be quoted "
                             f"by the export ({exc}): the frozen inventory predates the rule "
                             f"that refuses such a passage") from None
        frozen[p["item"]] = dict(p, span=span, quote=quote)
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
        if not isinstance(item, str) or item not in frozen:
            problems.add(2, where, f"item {item!r} is not one of this task's passages")
            continue
        if item in written:
            problems.add(2, where, f"{item} is given twice")
            continue
        written[item] = i
        where = f"{name}: {item}"
        outcome = p.get("outcome")
        if not _one_of(outcome, LOCATE_OUTCOMES):
            problems.add(1, where, f"outcome {outcome!r} is not located/absent/unresolved")
            continue
        if unresolved and outcome != "unresolved":
            problems.add(1, where, "an unresolved document leaves every passage unresolved")
        locations = _check_locations(cfg, ctx, problems, where, p.get("locations"))
        receipt = _check_receipt(cfg, ctx, problems, where, p.get("receipt"))
        if locations is None:
            continue                          # over the cap: refused, nothing more to read
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
        if "coverage" in p:
            _check_coverage(problems, where, p["coverage"], outcome=outcome,
                            locations=locations, layer=source.layer)
        # Rule 4's "located or rejected with a reason": a location whose relation
        # COUNTS locates a hit (a relatedMatch is "relevant but states something
        # else", so it discharges nothing on its own), and a rejection with a
        # reason rejects one.  An `accepted` candidate is one of the passage's
        # counting locations; accepted and not located is neither.
        located = {(str(loc.get("library")), str(loc.get("declaration"))) for loc in counting}
        handled.update(located)
        for cand in receipt["candidates"]:
            key = (str(cand.get("library")), str(cand.get("declaration")))
            if cand.get("verdict") == "accepted" and key not in located:
                problems.add(1, where, f"accepted candidate {key[0]}:{key[1]} is not one of this "
                                       f"passage's locations with a relation that counts: "
                                       f"locate it, or reject it with a reason")
            elif cand.get("verdict") == "rejected" and _nonempty_str(cand.get("reason")):
                handled.add(key)
        span = frozen[item]["span"]
        check_records(cfg, problems, where, source_id=source_id, layer=source.layer, span=span,
                      quote=frozen[item]["quote"], item=item,
                      passage_kind=frozen[item].get("passage_kind"), outcome=outcome,
                      locations=locations, receipt=receipt)
    missing = sorted(set(frozen) - set(written))
    if missing:
        problems.add(2, name, f"the task's passages {', '.join(missing)} have no outcome")
    # rule 4: every curated hit HANDED TO THIS TASK is located or rejected with a
    # reason.  The planner gives each of a source's curated hits to exactly one of
    # its tasks and names them in the manifest's `curated` list; the gate takes
    # that list intersected with the candidates file it reads itself, so a
    # manifest cannot add a hit, and a manifest that predates the key hands the
    # task every hit of the file.
    if not unresolved:
        hits = _curated(ctx, source_id)
        if "curated" in manifest_doc:
            named = {(str(e.get("library")), str(e.get("declaration")))
                     for e in manifest_doc.get("curated") or [] if isinstance(e, dict)}
            hits = [hit for hit in hits if hit in named]
        unhandled = [hit for hit in hits if hit not in handled]
        if unhandled:
            problems.add(4, name, f"curated L2 hit(s) {_hits(unhandled)} are neither located "
                                  f"(with a relation that counts: a relatedMatch is not) nor "
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
        if not _one_of(target, CHALLENGE_TARGETS):
            problems.add(1, where, f"target {target!r} is not a challenge target")
            continue
        if not isinstance(v.get("item"), str) or (
                target == "location" and not isinstance(v.get("location_key"), str)):
            problems.add(1, where, "a verdict names its record by item"
                                   + (" and location_key" if target == "location" else "")
                                   + " (strings)")
            continue
        if not _one_of(action, CHALLENGE_COMBOS[target]):
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
        if action == "downgrade":
            new, old = v.get("relation"), records[key].get("relation")
            if not _one_of(new, LOCATE_RELATIONS):
                problems.add(1, where, "a downgrade names the weaker relation")
            elif not _one_of(old, LOCATE_RELATIONS):
                raise GateBroken(f"challenge record {v.get('item')} of {source_id} carries no "
                                 f"relation, so no downgrade of it can be checked")
            elif RELATION_STRENGTH[new] <= RELATION_STRENGTH[old]:
                problems.add(1, where, f"a downgrade names a relation weaker than the record's "
                                       f"{old}; {new} is not")
            elif (new == CLOSE_MATCH and source is not None
                  and not str(records[key].get("note") or "").strip()):
                # the note the outcomes give the downgraded location (locate/outcomes.py
                # takes the location's own note first; a location record of a challenge
                # manifest carries none, so this is checked for every such downgrade)
                try:
                    lr.check_note(str(v.get("item")), lr.downgrade_note(v, source.layer),
                                  source.layer)
                except lr.RecordError as exc:
                    problems.add(1, where, _own_words(exc, where, str(v.get("item")))
                                 + " (a downgrade to closeMatch exports its note, else a "
                                   "sentence of its witness, as the location's note)")
        if action == "overturn" and target == "prominence" \
                and not _one_of(v.get("prominence"), PROMINENCE):
            problems.add(1, where, "a prominence overturn names the new prominence")
        if action == "overturn" and target in ("absence", "unresolved", "scope"):
            locations = _check_locations(cfg, ctx, problems, where, v.get("locations"))
            if locations is None:
                continue                      # over the cap: refused, nothing more to read
            if target != "scope" and not [loc for loc in locations
                                          if loc.get("relation") != RELATED_MATCH]:
                problems.add(1, where, "overturning an absence or an unresolved outcome gives a "
                                       "location whose relation counts")
            rec = records[key]
            span = _span(rec.get("span"), len(source.layer)) if source is not None else None
            if span is not None and locations:
                try:
                    quote = capped_quote(source.layer, span)
                except lr.RecordError as exc:
                    raise GateBroken(f"challenge record {v.get('item')} of {source_id} cannot be "
                                     f"quoted by the export ({exc})") from None
                check_records(cfg, problems, where, source_id=source_id, layer=source.layer,
                              span=span, quote=quote, item=str(v.get("item")),
                              passage_kind=rec.get("passage_kind"), outcome="located",
                              locations=locations)
    # one document may not confirm a coverage claim and refute or downgrade one of the
    # parts it names: the claim then names a location that is no longer a hasPart one
    changed = {v.get("location_key") for v in verdicts if isinstance(v, dict)
               and v.get("target") == "location" and v.get("action") in ("refute", "downgrade")}
    for i, v in enumerate(verdicts):
        if not (isinstance(v, dict) and v.get("target") == "coverage"
                and v.get("action") == "confirm"):
            continue
        rec = records.get((v.get("item"), "coverage", None)) or {}
        hit = sorted(str(part.get("location_key")) for part in rec.get("parts") or []
                     if isinstance(part, dict) and part.get("location_key") in changed)
        if hit:
            problems.add(1, f"{name}: verdicts[{i}]",
                         f"confirms the coverage claim of {v.get('item')} and refutes or "
                         f"downgrades its part(s) {', '.join(k[:12] for k in hit)}: a claim "
                         f"stands only while every part is a hasPart location; refute it")
    owed = [k for k in records if k not in judged]
    if owed:
        problems.add(1, name, f"{len(owed)} record(s) of this task have no verdict "
                              f"({', '.join(str(k[0]) + ' ' + str(k[1]) for k in owed[:3])})")
    return stats


CHECKS = {LOCATE_INVENTORY: check_inventory, LOCATE: check_locate,
          LOCATE_CHALLENGE: check_challenge}


def read_regular(path: pathlib.Path, limit: int, name: str, problems: Problems) -> bytes | None:
    """A document's bytes, read only from a REGULAR file and at most ``limit + 1`` of them.

    A committed symlink would be followed to any file of the host -- what merges
    is then a link, not the bytes the gate checked -- and a device such as
    ``/dev/zero`` would be read without end; both are refused before a byte is
    read, and the file is opened ``O_NOFOLLOW`` so it cannot be swapped between
    the check and the read.
    """
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            problems.add(1, name, "is not a regular file (a symlink, device or fifo is refused)")
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                problems.add(1, name, "is not a regular file")
                return None
            return fh.read(limit + 1)
    except OSError as exc:
        problems.add(1, name, f"cannot be read ({exc})")
        return None


def validate_document(path: pathlib.Path, name: str, task_id: str, cfg: GateConfig,
                      ctx: Context, problems: Problems) -> dict:
    """Every rule but 7 over one document; returns its counts for the receipt and the log."""
    stats: dict[str, Any] = {"task_id": task_id, "mode": cfg.mode}
    raw = read_regular(path, cfg.max_document_bytes, name, problems)
    if raw is None:
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
    unresolved = _unresolved_block(doc, name, problems, cfg, task_id,
                                   check_counter=ctx.check_counter)
    stats.update({"source_id": source_id, "unresolved": unresolved,
                  "attempt_capped": bool(unresolved and isinstance(doc.get("unresolved"), dict)
                                         and doc["unresolved"].get("by") == "attempt_cap")})
    if not source_id:
        return stats
    if path.name != f"{source_id}.json":
        problems.add(1, name, f"holds source {source_id}'s document; that lives at "
                              f"findings/{task_id}/{source_id}.json (one document per source, "
                              f"named for it)")
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


#: what the ownership line adds when orchestrator.sqlite could only be read immutably
#: while a ``-wal`` lay beside it
IMMUTABLE_NOTE = "read immutably: rows in its -wal may be missing"


def _open_orchestrator(db_path: pathlib.Path) -> tuple[sqlite3.Connection, str]:
    """``(connection, note)``: a read-only connection to tend's LIVE database.

    tend keeps ``orchestrator.sqlite`` in WAL mode, and a row it has committed
    sits in ``-wal`` until a checkpoint copies it into the main file.  An
    ``immutable=1`` open reads the main file alone, so it missed every worktree
    tend had created since the last checkpoint -- on a live run, nearly all of
    them, and the gate fell back to the single-stem rule (field defect G1).
    ``mode=ro`` reads the WAL through the shared-memory index, takes no write
    lock and never blocks the writer.

    ``immutable=1`` is the fallback only where it is complete or the only read
    there is: when no ``-wal`` lies beside the database (the main file holds
    every committed row), or when its directory cannot be written (no ``-shm``
    can be made there, so ``mode=ro`` cannot read the WAL at all) -- and then,
    with a ``-wal`` present, ``note`` is :data:`IMMUTABLE_NOTE`, which the
    ownership line carries.  Any other failure of the ``mode=ro`` read (a
    busy timeout, a damaged file) is raised: an immutable read there would
    silently miss the very rows G1 was about.
    """
    try:
        con = sqlite3.connect(ro_uri(db_path, immutable=False), uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        con, error = None, exc
    if con is not None:
        try:
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            return con, ""
        except sqlite3.Error as exc:
            con.close()
            error = exc
    wal = db_path.with_name(db_path.name + "-wal").exists()
    if wal and os.access(db_path.parent, os.W_OK):
        raise sqlite3.Error(f"it did not open read-only ({error}), and an immutable read "
                            f"would miss the rows in its -wal")
    return sqlite3.connect(ro_uri(db_path), uri=True), (IMMUTABLE_NOTE if wal else "")


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
        con, note = _open_orchestrator(db_path)
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
    read = f"orchestrator.sqlite ({note})" if note else "orchestrator.sqlite"
    if len(found) == 1:
        return found.pop(), read
    if found:
        return "", f"{read} names {len(found)} tasks for this worktree"
    return "", f"{read} names no task for this worktree"


def _classify(path: str) -> tuple[str, str, str] | None:
    """``('findings', task, source)`` / ``('task', task, '')`` / None for anything else.

    A task file is ``tasks/<task-id>.yaml``, the name the planner writes; a
    ``tasks/<task-id>.yml`` beside it would be a second task file, not this one.
    """
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "findings" and parts[2].endswith(".json") \
            and _safe_name(parts[1]) and _safe_name(parts[2][:-len(".json")]):
        return "findings", parts[1], parts[2][:-len(".json")]
    if len(parts) == 2 and parts[0] == "tasks" and parts[1].endswith(".yaml"):
        stem = parts[1][:-len(".yaml")]
        if _safe_name(stem):
            return "task", stem, ""
    return None


#: a task file's ``status:`` line, the one line a contribution changes
_STATUS_LINE = re.compile(r"status:")


def _task_file_change(before: str | None, path: pathlib.Path) -> str:
    """Why a task file's change is more than its status, or '' when it is only that.

    Every line but the ``status:`` ones must read as at the merge base: the id,
    the dependencies and the priority are the plan's, and a contribution that
    rewrote them would land them on the merge target, where tend reads tasks.
    """
    if before is None:
        return "cannot be read at the merge base"
    try:
        raw = path.read_bytes()[:MAX_TASK_FILE_BYTES + 1]
    except OSError as exc:
        return f"cannot be read ({exc})"
    if len(raw) > MAX_TASK_FILE_BYTES:
        return f"is over {MAX_TASK_FILE_BYTES} bytes"
    after = raw.decode("utf-8", errors="replace")

    def rest(text: str) -> list[str]:
        return [line.rstrip() for line in text.splitlines() if not _STATUS_LINE.match(line)]

    old, new = rest(before), rest(after)
    if old == new:
        return ""
    changed = next((n for o, n in zip(old, new) if o != n), None)
    if changed is None:
        changed = (new[len(old)] if len(new) > len(old) else f"(removed) {old[len(new)]}")
    return (f"changes more than its status line (first other change: {changed[:80]!r}); a "
            f"contribution sets `status:` and leaves the plan's lines as they are")


def check_changed(entries: dict[str, str], ctx: Context, *, task_id: str = "",
                  allow_empty: bool = False, root: pathlib.Path | None = None,
                  pre_merge: bool = False,
                  at_base: Callable[[str], str | None] | None = None) -> Problems:
    """Rule 7: only the task's own documents and task file, nothing removed.

    With no task id the single-stem rule of ``verify_findings.check_changed``
    stands in: one worker's change set is one task's.  Only ``pre_merge`` (the
    batched pre-merge command, ``--pre-merge``) admits several tasks, and then
    only when every one of their documents is an addition.  With ``root`` (the
    worktree) every path the change set writes must be a regular file there,
    and a task that wrote documents must hold one for every source its manifest
    names (rule 1: one document per source of the task).  The task file is the
    task's own ``tasks/<task-id>.yaml``, already on the merge target, and with
    ``at_base`` (its text at the merge base) a change to it is its status line
    and nothing else.
    """
    problems = Problems()
    written: dict[str, list[tuple[str, str]]] = {}
    #: the tasks whose task file the change set holds, accepted or not: a task
    #: file refused below has its own line, and "has no tasks/<id>.yaml" would
    #: be a second, false one
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
        if what == "task":
            tasks.add(stem)
        if status in ("D", "R"):
            problems.add(7, path, "was deleted or renamed away; a contribution writes its own "
                                  "documents and removes nothing")
            continue
        if task_id and stem != task_id:
            problems.add(7, path, f"belongs to task {stem!r}, not to this worker's {task_id!r}")
            continue
        if root is not None:
            try:
                regular = stat.S_ISREG(os.lstat(root / path).st_mode)
            except OSError:
                regular = False
            if not regular:
                problems.add(7, path, "is not a regular file in the worktree (a symlink is "
                                      "refused: what merges must be the bytes the gate read)")
                continue
        if what == "task":
            if status == "A":
                problems.add(7, path, "adds a task file: a task's file is on the merge target "
                                      "already, and a contribution only sets its status")
                continue
            if root is not None and at_base is not None:
                why = _task_file_change(at_base(path), root / path)
                if why:
                    problems.add(7, path, why)
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
    for stem in sorted(written):
        lacking = _lacking_documents(ctx, stem, {src for src, _st in written[stem]},
                                     root / "findings" / stem if root is not None else None)
        if lacking:
            problems.add(1, f"findings/{stem}/", f"the task's documents for {', '.join(lacking)} "
                                                 f"are missing: one document per source of the "
                                                 f"task manifest")
    if not task_id and len(seen) > 1 and not pre_merge:
        problems.add(7, "change set", f"belongs to {len(seen)} tasks ({', '.join(sorted(seen))}) "
                                      f"and no task id says which is this worker's: one "
                                      f"worker's contribution is one task's (only the batched "
                                      f"pre-merge check, --pre-merge, admits several)")
    elif not task_id and len(seen) > 1:
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


def _lacking_documents(ctx: Context, task_id: str, have: set[str],
                       directory: pathlib.Path | None) -> list[str]:
    """The sources task ``task_id``'s manifest names that have no document.

    ``have`` is what this call validates; ``directory`` (the task's findings
    directory, when known) supplies documents an earlier commit of the same task
    already holds.
    """
    manifest = ctx.manifest(task_id)
    if manifest is None:
        return []
    named = sorted({str(d.get("source_id")) for d in manifest.get("documents") or []
                    if isinstance(d, dict) and d.get("source_id")})
    return [sid for sid in named if sid not in have
            and not (directory is not None and (directory / f"{sid}.json").is_file())]


#: where a task id came from (:func:`ownership_line`, the gate log's ``task_id_from``)
TASK_ID_FROM_FLAG = "--task-id"
TASK_ID_FROM_ENV = "$TEND_TASK_ID"


def ownership_line(task_id: str, how: str) -> str:
    """The gate's last line: which rule judged the change set, and why when it is the weak one.

    ``how`` is where the task id came from (``--task-id``, ``$TEND_TASK_ID``,
    ``orchestrator.sqlite``, which may carry :data:`IMMUTABLE_NOTE`), or, with
    no task id, why the lookup found none -- said on the line, never dropped.
    An agent runs with ``TEND_TASK_ID`` in its environment, so a worker's or a
    reviewer's own call names its task that way; the line says so, and a
    task id on it is not by itself proof that a validation command's
    ``orchestrator.sqlite`` lookup works (field watch item W1).
    """
    if task_id and how.startswith("orchestrator.sqlite"):
        note = how[len("orchestrator.sqlite"):].strip().strip("()")
        return (f"ownership checked against task {task_id} (task id recovered from "
                f"orchestrator.sqlite: an assumed schema, verify on the host"
                + (f"; {note}" if note else "") + ")")
    if task_id and how in (TASK_ID_FROM_FLAG, TASK_ID_FROM_ENV):
        return f"ownership checked against task {task_id} (task id from {how})"
    if task_id:
        return f"ownership checked against task {task_id}"
    return ("ownership: single-stem rule (no task id); pass --task-id or export TEND_TASK_ID "
            "for the full check" + (f" ({how})" if how else ""))


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #

def _documents_in(directory: pathlib.Path) -> list[tuple[pathlib.Path, str, str]]:
    """``[(path, display name, task id)]`` for ``<directory>/<task>/<source>.json``."""
    out = []
    if not directory.is_dir():
        return out
    for task_dir in sorted(directory.iterdir()):
        if task_dir.is_symlink() or not task_dir.is_dir() or not _safe_name(task_dir.name):
            continue
        for path in sorted(task_dir.glob("*.json")):
            if path.name in NON_DOCUMENT_FILES or path.name.startswith("."):
                continue
            out.append((path, f"{task_dir.name}/{path.name}", task_dir.name))
    return out


def _strays(directory: pathlib.Path) -> list[str]:
    """JSON under ``directory`` that is not a ``<task>/<source>.json`` document.

    A locate corpus is exactly that layout; the receipt and every reader of a
    harvested corpus count JSON at any depth, so a file the gate never read
    would land as counted and "gated".  The listing is the corpus readers' own:
    hidden components skipped, the corpus's own receipt and copies excluded.
    """
    out: list[str] = []
    if not directory.is_dir():
        return out
    for here, dirs, files in os.walk(directory):
        rel_dir = pathlib.Path(here).relative_to(directory)
        depth = len(rel_dir.parts)
        for d in sorted(dirs):
            if not d.startswith(".") and (pathlib.Path(here) / d).is_symlink():
                out.append(f"{(rel_dir / d).as_posix()}/")
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if not f.endswith(".json") or f.startswith(".") or f in NON_DOCUMENT_FILES:
                continue
            if depth != 1 or not _safe_name(rel_dir.parts[0]):
                out.append((rel_dir / f).as_posix())
    return sorted(out)


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


def incomplete_message() -> str:
    return ("the gate directory is incomplete: " + "; ".join(IMPORT_ERRORS)
            + " (cf plan-locate stages verify_locate.py with locate_text.py, verify_findings.py, "
              "locate_records.py and vendor/ beside it)")


def _merge_base_reader(root: pathlib.Path, base: str) -> Callable[[str], str | None]:
    """``path -> its text at the merge base of base and HEAD`` (None when it has none).

    ``git cat-file blob`` runs no filter and no textconv; the git boundary is
    ``verify_findings._git``'s.  ``changed_entries`` has refused a history with
    no single merge base before this is asked.
    """
    code, out = vf._git(["merge-base", base, "HEAD"], root)
    merge_base = out.strip() if code == 0 else ""

    def read(path: str) -> str | None:
        if not merge_base:
            return None
        code, text = vf._git(["cat-file", "blob", f"{merge_base}:{path}"], root)
        return text if code == 0 else None

    return read


def run_gate(cfg: GateConfig, *, root: pathlib.Path, findings: list[pathlib.Path] | None = None,
             findings_dir: str = DEFAULT_FINDINGS_DIR, changed_files: bool = False,
             base: str = "main", task_id: str = "", allow_empty: bool = False,
             check_vendor: bool = True, task_id_from: str | None = None,
             pre_merge: bool = False) -> dict:
    """Run the gate in-process; ``{exit, problems, broken, documents, ownership, ...}``.

    ``cf harvest`` calls this over a landed corpus; ``main`` is the CLI around it.
    ``task_id_from`` says where a given ``task_id`` came from (``--task-id``,
    the default, or ``$TEND_TASK_ID``).  Raises nothing: an infrastructure
    failure is ``exit: 3`` with ``broken`` set.  Every call appends one line to
    the gate log with its verdict, the ``ownership`` line, ``task_id_from`` and
    whether it judged a change set (``changed_files``).  ``pre_merge`` is tend's
    batched pre-merge check (``--pre-merge``), the one call in which a change set
    of several tasks' new documents is admitted without a task id (rule 7).
    """
    result: dict[str, Any] = {"exit": EXIT_OK, "problems": [], "broken": [], "documents": 0,
                              "by_rule": {}, "stats": [], "ownership": "", "task_id": task_id,
                              "mode": cfg.mode, "run_id": cfg.run_id}
    if IMPORT_ERRORS:
        result.update(exit=EXIT_GATE_BROKEN, broken=[incomplete_message()])
        return result
    ctx = Context(cfg, check_counter=changed_files)
    problems = Problems()
    how = (task_id_from or TASK_ID_FROM_FLAG) if task_id else ""
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
            changed = check_changed(entries, ctx, task_id=task_id, allow_empty=allow_empty,
                                    root=root, pre_merge=pre_merge,
                                    at_base=_merge_base_reader(root, base))
            problems.extend(changed)
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
            for stray in _strays(root / findings_dir):
                problems.add(7, stray, "is not a <task-id>/<source-id>.json document; a locate "
                                       "corpus holds nothing else, and the gate did not read it")
            by_task: dict[str, set[str]] = {}
            for path, _name, doc_task in docs:
                by_task.setdefault(doc_task, set()).add(path.stem)
            for doc_task, have in sorted(by_task.items()):
                lacking = _lacking_documents(ctx, doc_task, have, None)
                if lacking:
                    problems.add(1, f"{doc_task}/", f"the task's documents for "
                                                    f"{', '.join(lacking)} are missing: one "
                                                    f"document per source of the task manifest")
        for path, name, doc_task in docs:
            if task_id and doc_task != task_id:
                problems.add(7, name, f"is task {doc_task!r}'s document, not {task_id!r}'s")
                continue
            try:
                result["stats"].append(validate_document(path, name, doc_task, cfg, ctx,
                                                         problems))
            except (TypeError, AttributeError, KeyError, ValueError, IndexError,
                    RecursionError) as exc:
                # the backstop of "raises nothing": a shape no check above
                # foresaw is the worker's document's fault, said as rule 1
                problems.add(1, name, f"the document's shape could not be checked "
                                      f"({exc.__class__.__name__}: {str(exc)[:200]})")
        result["documents"] = len(docs)
    except GateBroken as exc:
        result["broken"].append(str(exc))
        result["exit"] = EXIT_GATE_BROKEN
    finally:
        ctx.close()
    result["problems"] = problems.lines()
    result["by_rule"] = problems.by_rule()
    result["task_id"] = task_id
    if result["exit"] == EXIT_OK and len(problems):
        result["exit"] = EXIT_REJECTED
    result["task_id_from"] = how if task_id else None
    write_log(cfg, {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "run_id": cfg.run_id, "mode": cfg.mode, "task_id": task_id,
                    "task_id_from": result["task_id_from"],
                    "changed_files": bool(changed_files),
                    "ownership": result["ownership"] or None,
                    "ownership_note": result.get("ownership_note"),
                    "ok": result["exit"] == EXIT_OK, "exit": result["exit"],
                    "documents": result["documents"], "by_rule": result["by_rule"],
                    "broken": result["broken"][:3],
                    "reasons": [p[:300] for p in result["problems"][:20]]})
    return result


def self_check(cfg: GateConfig) -> str:
    """Everything this gate needs before a document reaches it; a summary, or :class:`GateBroken`.

    ``cf run``'s verify stage runs ``--self-check`` on the host, with
    ``python3 -I`` from the gate directory alone, before any agent starts: the
    gate's own modules imported (a missing or broken one -- ``locate_records``
    imports ``literature_links`` -- is exit 3 before this runs), both vendored
    packages the pinned releases, and the task manifests, the sources and
    ``corpus.sqlite`` where gate.json says they are.  A gate directory staged without one of its modules used to
    surface only as every worker's first validation failing (field defect P2).
    """
    verify_vendor(cfg)
    if not cfg.tasks_dir.is_dir():
        raise GateBroken(f"the task manifests directory {cfg.tasks_dir} is missing")
    manifests = sum(1 for p in cfg.tasks_dir.glob("*.json") if p.is_file())
    if not manifests:
        raise GateBroken(f"{cfg.tasks_dir} holds no task manifest")
    if not cfg.sources_dir.is_dir():
        raise GateBroken(f"the sources directory {cfg.sources_dir} is missing")
    if cfg.corpus_db is not None and not cfg.corpus_db.is_file():
        raise GateBroken(f"corpus.sqlite is missing at {cfg.corpus_db}")
    return (f"OK: gate self-check: {cfg.mode} run {cfg.run_id}, {manifests} task manifest(s), "
            f"{len(cfg.sources)} pinned source(s), the vendored packages are the pinned releases "
            f"and import from {VENDOR}")


def main(argv: list[str] | None = None) -> int:
    """The CLI; any failure of the gate itself is exit 3, never the interpreter's 1."""
    try:
        return _main(argv)
    except Exception as exc:  # noqa: BLE001 - a crash of the gate is not a verdict
        print(f"FAIL: the gate itself failed ({type(exc).__name__}: {exc}) — the gate could not "
              "run: this is not a finding of yours", file=sys.stderr)
        print("ownership: not checked (the gate could not run)")
        return EXIT_GATE_BROKEN


def _main(argv: list[str] | None = None) -> int:
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
    ap.add_argument("--task-id", default=None,
                    help="the worker's own task id (default: $TEND_TASK_ID, else "
                         "orchestrator.sqlite, else the single-stem rule)")
    ap.add_argument("--allow-empty", action="store_true",
                    help="with --changed-files: tolerate a change set with no document")
    ap.add_argument("--pre-merge", action="store_true",
                    help="with --changed-files: the batched pre-merge check, where a change "
                         "set of several tasks' new documents is admitted without a task id")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-check", action="store_true",
                    help="check that this gate can run (its modules, the vendored packages, the "
                         "task manifests, the sources, corpus.sqlite) and validate nothing")
    args = ap.parse_args(argv)

    if IMPORT_ERRORS:
        # one line, never a traceback: exit 1 would tell the worker to fix its documents
        print(f"FAIL: {incomplete_message()} — the gate could not run: this is not a finding "
              f"of yours", file=sys.stderr)
        print("ownership: not checked (the gate could not run)")
        return EXIT_GATE_BROKEN
    gate_path = pathlib.Path(args.gate_config).expanduser()
    try:
        if not gate_path.is_file():
            raise GateBroken(f"gate config missing at {gate_path}")
        cfg = GateConfig.load(gate_path)
        if args.self_check:
            print(self_check(cfg))
            print("ownership: not checked (--self-check validates no document)")
            return EXIT_OK
    except GateBroken as exc:
        print(f"FAIL: {exc} — the gate could not run: this is not a finding of yours",
              file=sys.stderr)
        print("ownership: not checked (the gate could not run)")
        return EXIT_GATE_BROKEN
    if args.task_id:
        task_id, task_id_from = args.task_id, TASK_ID_FROM_FLAG
    else:
        task_id = os.environ.get("TEND_TASK_ID", "")
        task_id_from = TASK_ID_FROM_ENV if task_id else None
    result = run_gate(cfg, root=pathlib.Path(args.root).resolve(),
                      findings=[pathlib.Path(f) for f in args.findings],
                      findings_dir=args.findings_dir, changed_files=args.changed_files,
                      base=args.base, task_id=task_id, allow_empty=args.allow_empty,
                      task_id_from=task_id_from, pre_merge=args.pre_merge)
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
    elif result["broken"]:
        print("ownership: not checked (the gate could not run)")
    else:
        print("ownership: not checked (no --changed-files: these documents were validated, the "
              "change set was not)")
    return int(result["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
