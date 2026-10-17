"""Ingest one library's probe index into a universe: a normalised, re-decided index.

New code for docs/proposals/wikipedia-locate.md sections 2.4 and 2.5, writing
the normalised index references/locate-documents.md section 8 fixes
(``corpora/<universe>/index/<library>/decls.jsonl`` + ``manifest.json``).

**Streamed.**  A Mathlib index is hundreds of megabytes of JSON lines, so
nothing here loads a file whole: the probe's ``index.jsonl`` is read line by
line twice (module paths first, then declarations), each declaration is
normalised as it passes, and the output is written as it goes.  The probe
writes declarations sorted by name; the order is verified while streaming,
and an input that is not sorted falls back to an external merge sort through
temporary chunk files, so the output is sorted by name whatever arrived.  Two
declarations with one name in one library are refused (the exact table keys
on the name).

**Every proof status is re-decided** with ``cflib.vocab.decide_proof_status``,
never copied from the probe, because the probe the host ran decides in another
order: ``axiom`` (by kind) first, then ``wanted``, ``statement_only``,
``definition`` for data and ``proved`` otherwise; it has no ``axiom_dependent``,
emits no statement flag, and an ``isProp`` that failed falls through to
``proved``.  The inputs are what the probe emits:

* ``wanted`` -- the probe found a wrapper (``wrapper`` is not null);
* the closure -- ``nonstandard_axioms``: every axiom the declaration reaches
  except ``propext``, ``Classical.choice`` and ``Quot.sound``, ``sorryAx``
  included.  An axiom declaration's closure contains the axiom itself (the
  probe's node contributes it), so a non-standard axiom is ``axiom_dependent``
  -- ``collectAxioms`` semantics;
* ``is_prop`` -- the probe's own data/proof split: ``proved`` means the type is
  a proposition, ``definition`` that it is data; theorems are propositions and
  inductive types are data by kind; of the three standard axioms only
  ``Classical.choice`` is data.  When the probe's ``isProp`` call failed the
  answer is unknown, and a declaration whose status would turn on it is given
  the conservative ``statement_only`` -- it can make no passage ``formalized``
  -- with ``is_prop_unknown`` in its ``flags``, counted in the manifest.

**The statement flag** (a ``Prop``-valued definition, section 2.4) is a
fallback: the probe emits no such field, so it is derived from the kind (a
``def``, ``abbrev`` or ``opaque``) and the printed type, whose final codomain
-- after the last top-level ``→`` -- is ``Prop``.  A probe field computed from
the type itself (``isPropFormerType``) would be exact; that is an open
question for the template, which is not changed here.

**Tags** (``cflib.vocab.TAG_KINDS``): ``@[wikidata Q…]``, ``@[stacks …]`` and
``@[kerodon …]`` attributes in the declaration's source span, when the library
checkout is given -- read as plain files under the checkout, a path that leaves
it refused, nothing in it ever run -- and Wikipedia URLs in the docstring.

The acceptance canary (section 2.4) is ingested by :func:`check_canary`: its
four declarations must be classified as expected before any library on its
toolchain is ingested, and :func:`ingest` refuses a library whose toolchain
has no passed canary in ``universe.yaml``.
"""
from __future__ import annotations

import heapq
import json
import os
import re
import shutil
import tempfile
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Iterator

from cflib import state as state_mod
from cflib import vocab
from cflib.errors import CfError
from cflib.hashing import sha256_file

from .universe import Universe, lean_version

INDEX_SCHEMA = "cf-library-index/1"
#: the probe's own schema (scripts/corpus/probe_merge.py) and the published base it extends
PROBE_SCHEMA = "formal-companion-library-index/v0"
PUBLISHED_SCHEMA = "autoform-library-index/v0"
#: the probe statuses ingest understands (scripts/corpus/probe_merge.py PROOF_STATUSES)
PROBE_STATUSES = frozenset({"axiom", "definition", "proved", "statement_only", "wanted"})
#: An ``autoform-library-index/v0`` status word, for a published record with no
#: closure, and the proof status it means.  Anything else is refused.
PUBLISHED_STATUSES = {
    "proved": vocab.PROOF_PROVED, "definition": vocab.PROOF_DEFINITION,
    "wanted": vocab.PROOF_WANTED, "statement_only": vocab.PROOF_STATEMENT_ONLY,
    "sorry": vocab.PROOF_STATEMENT_ONLY, "axiom_dependent": vocab.PROOF_AXIOM_DEPENDENT,
}
DATA_KINDS = frozenset({"inductive", "structure", "class"})
DEFINITION_KINDS = frozenset({"def", "abbrev", "opaque"})
#: records per in-memory chunk when the input has to be sorted
CHUNK_RECORDS = 50_000
#: source files kept open at once while tags are read from a checkout
_FILE_CACHE = 256

#: The acceptance canary's four declarations (templates/corpus/canary/), and what
#: each must be decided as before a toolchain's libraries are ingested.
CANARY = {
    "CfCanary.hiddenSorry": {"proof_status": vocab.PROOF_STATEMENT_ONLY, "statement": False},
    "CfCanary.wantedTheorem": {"proof_status": vocab.PROOF_WANTED, "statement": False},
    "CfCanary.propDef": {"proof_status": vocab.PROOF_DEFINITION, "statement": True},
    "CfCanary.axiomTheorem": {"proof_status": vocab.PROOF_AXIOM_DEPENDENT, "statement": False},
}

_TAG_ATTR = {
    "wikidata": re.compile(r"\bwikidata\s+(Q[1-9][0-9]*)\b"),
    "stacks": re.compile(r"\bstacks\s+([0-9A-Z]{4})\b"),
    "kerodon": re.compile(r"\bkerodon\s+([0-9A-Z]{4})\b"),
}
_ATTRIBUTES = re.compile(r"@\[(.*?)\]", re.S)
_WIKIPEDIA = re.compile(r"https?://[a-z]{2,12}(?:\.m)?\.wikipedia\.org/wiki/[^\s)>\]\"'`]+")


# --------------------------------------------------------------------------
# deciding one declaration
# --------------------------------------------------------------------------

def _top_level_codomain(signature: str) -> str:
    """The text after the last ``→`` or ``,`` outside brackets: a type's final codomain."""
    depth = 0
    cut = 0
    for i, ch in enumerate(signature):
        if ch in "([{⦃⟨":
            depth += 1
        elif ch in ")]}⦄⟩":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in "→,":
            cut = i + 1
    return signature[cut:].strip()


def statement_flag(kind: str, signature: str | None) -> bool:
    """A ``def``/``abbrev``/``opaque`` whose printed type ends in ``Prop`` (the fallback)."""
    if kind not in DEFINITION_KINDS or not signature:
        return False
    return _top_level_codomain(str(signature)) == "Prop"


def _is_prop_failed(errors) -> bool:
    return any(str(e).startswith("isProp") for e in (errors or []))


def decide(record: dict) -> tuple[str, bool, list[str]]:
    """``(proof_status, statement, flags)`` of one probe declaration, by section 2.4."""
    name = str(record.get("name") or "")
    kind = str(record.get("kind") or "")
    probe_status = str(record.get("proof_status") or "")
    closure = {str(a) for a in (record.get("nonstandard_axioms") or [])}
    flags: list[str] = []
    if kind == "axiom" and name not in vocab.STANDARD_AXIOMS:
        closure.add(name)
    if kind == "theorem":
        is_prop: bool | None = True
    elif kind in DATA_KINDS:
        is_prop = False
    elif kind == "axiom":
        is_prop = name != "Classical.choice"
    elif probe_status == "proved":
        is_prop = True
    elif probe_status == "definition":
        is_prop = False
    else:
        is_prop = None
    if _is_prop_failed(record.get("errors")) and kind not in ("theorem", "axiom") \
            and kind not in DATA_KINDS:
        is_prop = None
    wanted = record.get("wrapper") is not None or probe_status == "wanted"
    status = vocab.decide_proof_status(wanted=wanted, axioms=closure, is_prop=bool(is_prop))
    if is_prop is None and status in (vocab.PROOF_PROVED, vocab.PROOF_DEFINITION):
        status = vocab.PROOF_STATEMENT_ONLY
        flags.append("is_prop_unknown")
    statement = statement_flag(kind, record.get("signature")) and status != vocab.PROOF_PROVED
    return status, statement, flags


def _printable(record: dict) -> bool:
    if record.get("signature") is None:
        return False
    if "signature" in (record.get("truncated_fields") or []):
        return False
    return not any(str(e).startswith("signature") for e in (record.get("errors") or []))


# --------------------------------------------------------------------------
# tags
# --------------------------------------------------------------------------

class _Checkout:
    """Plain reads of a library checkout's files, never leaving it, a few kept open."""

    def __init__(self, root: Path | None):
        self.root = Path(root).resolve() if root else None
        self._lines: "OrderedDict[str, list[str] | None]" = OrderedDict()
        self.unreadable: set[str] = set()

    def lines(self, rel: str | None) -> list[str] | None:
        if self.root is None or not rel:
            return None
        if rel in self._lines:
            self._lines.move_to_end(rel)
            return self._lines[rel]
        candidate = (self.root / rel).resolve()
        got: list[str] | None
        try:
            candidate.relative_to(self.root)
        except ValueError:
            got = None                     # a path that leaves the checkout is not read
        else:
            try:
                got = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                got = None
        if got is None:
            self.unreadable.add(rel)
        self._lines[rel] = got
        if len(self._lines) > _FILE_CACHE:
            self._lines.popitem(last=False)
        return got


def tags_of(record: dict, path: str | None, checkout: _Checkout) -> list[dict]:
    """The declaration's tags: attributes in its source span, Wikipedia URLs in its docstring."""
    found: set[tuple[str, str]] = set()
    lines = checkout.lines(path)
    start, name_line = record.get("start_line"), record.get("line")
    if lines and isinstance(start, int) and isinstance(name_line, int) and 1 <= start <= name_line:
        span = "\n".join(lines[start - 1:name_line])
        for attrs in _ATTRIBUTES.findall(span):
            for tag, pattern in _TAG_ATTR.items():
                for value in pattern.findall(attrs):
                    found.add((tag, value))
    for url in _WIKIPEDIA.findall(str(record.get("docstring") or "")):
        found.add(("wikipedia", url.rstrip(".,;:")))
    return [{"tag": tag, "value": value} for tag, value in sorted(found)]


# --------------------------------------------------------------------------
# reading a probe index
# --------------------------------------------------------------------------

def _records(path: Path) -> Iterator[tuple[int, dict]]:
    """``(line number, record)`` of every non-blank line, refusing a line that is not an object."""
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CfError(f"{path.name} line {line_no} is not JSON ({exc.msg})") from None
            if not isinstance(record, dict):
                raise CfError(f"{path.name} line {line_no} is not a JSON object")
            yield line_no, record


def _index_file(source: Path) -> Path:
    if source.is_file():
        return source
    for name in ("index.jsonl", "autoform-library-index.jsonl"):
        if (source / name).is_file():
            return source / name
    raise CfError(f"no index.jsonl in {source}",
                  hint="--from names the probe's output directory (index.jsonl + manifest.json)")


def read_header(index: Path) -> dict:
    for _line_no, record in _records(index):
        if record.get("record") != "header":
            raise CfError(f"{index.name} does not start with a header record")
        if record.get("schema") not in (PROBE_SCHEMA, PUBLISHED_SCHEMA):
            raise CfError(f"{index.name} has schema {record.get('schema')!r}, not "
                          f"{PROBE_SCHEMA} or {PUBLISHED_SCHEMA}")
        return record
    raise CfError(f"{index.name} is empty")


def _module_paths(index: Path) -> dict[str, str | None]:
    paths: dict[str, str | None] = {}
    for _line_no, record in _records(index):
        if record.get("record") == "module":
            paths[str(record.get("name"))] = record.get("path") or record.get("source_file")
    return paths


def normalise(record: dict, module_paths: dict, checkout: _Checkout, *,
              published: bool) -> dict:
    """One probe (or published) declaration as a ``cf-library-index/1`` record."""
    name = str(record.get("name") or "")
    if not name:
        raise CfError("a declaration record has no name")
    kind = str(record.get("kind") or "")
    probe_status = str(record.get("proof_status") or "")
    if published and record.get("nonstandard_axioms") is None:
        # an autoform-library-index/v0 record carries its library's own status and no
        # closure to re-decide it from: take the status only when it is a word that
        # means one of ours, flagged, and refuse any other
        probe_status = str(record.get("proof_status") or record.get("status") or "")
        status = PUBLISHED_STATUSES.get(probe_status)
        if status is None:
            raise CfError(f"{name}: published status {probe_status!r} is not one of "
                          f"{', '.join(sorted(PUBLISHED_STATUSES))}",
                          hint="a status ingest cannot map is refused, never guessed")
        statement = statement_flag(kind, record.get("signature")) and status != vocab.PROOF_PROVED
        flags = ["published_status"]
    else:
        if probe_status not in PROBE_STATUSES:
            raise CfError(f"{name}: probe status {probe_status!r} is not one of "
                          f"{', '.join(sorted(PROBE_STATUSES))}")
        status, statement, flags = decide(record)
    module = record.get("module")
    path = record.get("path") or module_paths.get(str(module))
    return {
        "name": name, "module": module, "path": path,
        "line": record.get("line"), "start_line": record.get("start_line"),
        "end_line": record.get("end_line"), "kind": kind,
        "proof_status": status, "statement": statement, "printable": _printable(record),
        "signature": record.get("signature"), "docstring": record.get("docstring"),
        "axioms": sorted({str(a) for a in (record.get("nonstandard_axioms") or [])}
                         | ({name} if kind == "axiom" and name not in vocab.STANDARD_AXIOMS
                            else set())),
        "tags": tags_of(record, path, checkout),
        "deprecated": record.get("deprecated"),
        "probe_proof_status": probe_status or None,
        "flags": sorted(set(flags)),
    }


def _dumps(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _write_sorted(records: Iterator[dict], out: Path, work: Path) -> int:
    """Write ``records`` to ``out`` sorted by name; refuse a repeated name.  Returns the count.

    Sorted input is written straight through.  At the first record out of order
    everything so far becomes chunk 0 and the rest is chunked, sorted and merged.
    """
    count = 0
    last: str | None = None
    chunks: list[Path] = []
    buffer: list[tuple[str, str]] = []
    handle = open(out, "w", encoding="utf-8", newline="\n")
    try:
        for record in records:
            name = record["name"]
            line = _dumps(record)
            count += 1
            if not chunks:
                if last is None or name > last:
                    handle.write(line + "\n")
                    last = name
                    continue
                if name == last:
                    raise CfError(f"two declarations named {name} in one library",
                                  hint="the exact table keys on (library, commit, name)")
                handle.close()
                first = work / "chunk-0.jsonl"
                os.replace(out, first)
                chunks.append(first)
                handle = open(out, "w", encoding="utf-8", newline="\n")
            buffer.append((name, line))
            if len(buffer) >= CHUNK_RECORDS:
                chunks.append(_spill(buffer, work, len(chunks)))
                buffer = []
        if chunks:
            if buffer:
                chunks.append(_spill(buffer, work, len(chunks)))
            files = [open(p, encoding="utf-8") for p in chunks]
            try:
                stream = heapq.merge(*(((_name_of(line), line) for line in f) for f in files))
                previous = None
                for name, line in stream:
                    if name == previous:
                        raise CfError(f"two declarations named {name} in one library",
                                      hint="the exact table keys on (library, commit, name)")
                    handle.write(line if line.endswith("\n") else line + "\n")
                    previous = name
            finally:
                for f in files:
                    f.close()
    finally:
        handle.close()
    return count


def _name_of(line: str) -> str:
    return json.loads(line)["name"]


def _spill(buffer: list[tuple[str, str]], work: Path, index: int) -> Path:
    path = work / f"chunk-{index}.jsonl"
    buffer.sort(key=lambda pair: pair[0])
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for _name, line in buffer:
            handle.write(line + "\n")
    return path


# --------------------------------------------------------------------------
# the two entry points
# --------------------------------------------------------------------------

def _source_manifest(source: Path) -> tuple[dict, str | None]:
    path = (source / "manifest.json") if source.is_dir() else source.with_name("manifest.json")
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CfError(f"{path} is not readable JSON ({exc})") from None
    return (data if isinstance(data, dict) else {}), sha256_file(path)


def _declarations(index: Path, *, published: bool,
                  checkout: _Checkout) -> Iterator[dict]:
    module_paths = _module_paths(index)
    for line_no, record in _records(index):
        if line_no == 1 or record.get("record") != "declaration":
            continue
        try:
            yield normalise(record, module_paths, checkout, published=published)
        except CfError as exc:
            raise CfError(f"{index.name} line {line_no}: {exc}", hint=exc.hint) from None


def ingest(universe: Universe, library_id: str, source: Path, out_dir: Path, *,
           checkout: Path | None = None) -> dict:
    """Ingest one library's index into ``out_dir`` and pin it in ``universe``; the manifest.

    Refused: a library the universe does not list (or excludes), a probe index
    for another library or commit, a probe index on a toolchain whose canary
    has not passed, and any record ingest cannot decide.  ``universe`` is
    changed in memory; the caller saves it.
    """
    lib = universe.library(library_id)
    source = Path(source)
    index = _index_file(source)
    header = read_header(index)
    published = header.get("schema") == PUBLISHED_SCHEMA
    manifest_in, manifest_sha = _source_manifest(source)
    for key in ("library",):
        value = manifest_in.get(key) or header.get(key)
        if value and value != library_id and not published:
            raise CfError(f"the index at {source} is for library {value!r}, not {library_id!r}")
    commit = manifest_in.get("commit") or header.get("commit")
    if commit and commit != lib.get("commit"):
        raise CfError(f"the index at {source} was probed at {str(commit)[:12]}, but universe "
                      f"{universe.id} pins {library_id} at {str(lib.get('commit'))[:12]}",
                      hint="probe the pinned commit; a universe is frozen")
    if not commit and not published:
        raise CfError(f"the index at {source} names no commit",
                      hint="probe_merge.py records it in manifest.json; ingest the whole "
                           "output directory")
    toolchain = header.get("toolchain") or lib.get("toolchain")
    if not published:
        if lean_version(toolchain) != lean_version(lib.get("toolchain")):
            raise CfError(f"the index was probed on Lean {lean_version(toolchain)}, but "
                          f"{library_id} is pinned to {lib.get('toolchain')}")
        if not universe.canary_passed(str(toolchain)):
            raise CfError(f"no passed acceptance canary for Lean {lean_version(toolchain)} in "
                          f"universe {universe.id}",
                          hint="build and probe templates/corpus/canary/ on that toolchain, then "
                               "`cf corpus ingest <universe> --canary --from <its index>` "
                               "(docs/proposals/wikipedia-locate.md section 2.4)")
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    reader = _Checkout(checkout)
    work = Path(tempfile.mkdtemp(prefix=f".{library_id}-", dir=out_dir.parent))
    try:
        staged = work / "out"
        staged.mkdir()
        decls_path = staged / "decls.jsonl"
        counts = {"proof_status": Counter(), "kind": Counter(), "flags": Counter(),
                  "tags": Counter(), "statement": 0, "unprintable": 0}

        def counted() -> Iterator[dict]:
            for record in _declarations(index, published=published, checkout=reader):
                counts["proof_status"][record["proof_status"]] += 1
                counts["kind"][record["kind"]] += 1
                counts["flags"].update(record["flags"])
                counts["tags"].update(t["tag"] for t in record["tags"])
                counts["statement"] += int(record["statement"])
                counts["unprintable"] += int(not record["printable"])
                yield record

        total = _write_sorted(counted(), decls_path, work)
        digest = sha256_file(decls_path)
        manifest = {
            "schema": INDEX_SCHEMA, "library": library_id, "universe": universe.id,
            "commit": lib.get("commit"), "toolchain": lib.get("toolchain"),
            "lean_version": lean_version(toolchain), "declarations": total,
            "sha256": digest,
            "by_proof_status": dict(sorted(counts["proof_status"].items())),
            "by_kind": dict(sorted(counts["kind"].items())),
            "flagged": dict(sorted(counts["flags"].items())),
            "statement_definitions": counts["statement"],
            "unprintable": counts["unprintable"],
            "tags": dict(sorted(counts["tags"].items())),
            "tags_read_from_checkout": checkout is not None,
            "checkout_files_unreadable": len(reader.unreadable),
            "source": {"schema": header.get("schema"), "index_sha256": sha256_file(index),
                       "manifest_sha256": manifest_sha,
                       "probe_revision": header.get("probe_revision"),
                       "template_sha256": manifest_in.get("template_sha256")},
            "ingested": state_mod.now(),
        }
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True)
                                              + "\n", encoding="utf-8")
        if out_dir.exists():
            shutil.rmtree(out_dir)
        os.replace(staged, out_dir)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    universe.set_index(library_id, path=f"index/{library_id}/", sha256=digest,
                       declarations=total)
    return manifest


def check_canary(universe: Universe, source: Path) -> dict:
    """Ingest a canary probe index and record whether its toolchain passes.

    The four declarations of templates/corpus/canary/ must each be decided as
    :data:`CANARY` says.  A missing one fails the canary: the probe that cannot
    see a declaration cannot classify it.  ``universe`` is changed in memory.
    """
    source = Path(source)
    index = _index_file(source)
    header = read_header(index)
    if header.get("schema") != PROBE_SCHEMA:
        raise CfError(f"a canary is a probe index ({PROBE_SCHEMA}), not {header.get('schema')!r}")
    toolchain = str(header.get("toolchain") or "")
    if not toolchain:
        raise CfError(f"{index.name}'s header names no toolchain")
    got: dict[str, dict] = {}
    reader = _Checkout(None)
    for record in _declarations(index, published=False, checkout=reader):
        if record["name"] in CANARY:
            got[record["name"]] = record
    cases = {}
    for name, want in CANARY.items():
        record = got.get(name)
        result = ({"proof_status": record["proof_status"], "statement": record["statement"]}
                  if record else None)
        cases[name] = {"expected": want, "got": result, "ok": result == want}
    passed = all(case["ok"] for case in cases.values())
    universe.record_canary(toolchain=toolchain, passed=passed, cases=cases,
                           index_sha256=sha256_file(index), checked=state_mod.now())
    return {"toolchain": toolchain, "lean_version": lean_version(toolchain), "passed": passed,
            "cases": cases}
