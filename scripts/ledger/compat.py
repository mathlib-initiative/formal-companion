"""cflib adapter for the ledger / route / report stages.

The shared library is ``scripts/cflib`` (vocabulary, paths, workspace, state,
json io, hashing).  This module is the thin translation layer those three stages
import, so that a rename in cflib is a one-file change here rather than a
rewrite of five modules.  Everything it exports resolves to cflib, with no
fallback: the module is only importable with ``scripts/`` on ``sys.path``, and
cflib lives in that same package root, so cflib is importable whenever this
module is.  The ``try``/``except Exception`` that used to wrap those imports
could only fire when cflib itself raised AT IMPORT TIME, and it then answered an
honest bug inside cflib with a second ``WorkspacePaths`` that was missing 35 of
its 64 accessors -- an AttributeError at the write site instead of the real
error.  An ImportError here is an ImportError.  (The same applies to
``sources/compat.py`` and ``pair/compat.py``; the only modules that really do
run without cflib are ``scripts/gate/verify_findings.py`` and
``check_changed_files.py``, which ``cf run`` transfers to the host and which
inline what they need.)

Delete this file once every call site has been switched to cflib directly.

Two deliberate choices worth knowing about:

* ``finding_id`` is ``cflib.hashing.finding_id``, the ONE definition.  It used
  to prefer ``gate.verify_findings.finding_id`` because the two disagreed on the
  join byte (newline vs NUL/'|') and a ledger row must link to the id its corpus
  file carries; they now agree byte for byte (the gate inlines the same
  algorithm because it must run with no cflib on the path) and the test suite
  asserts it, so the preference is gone.
* ``LedgerPaths`` wraps ``cflib.paths.WorkspacePaths`` and adds the short
  accessors this stage uses (``accepted``/``refuted``/``routed_findings``);
  every cflib accessor still passes through untouched.  The pairing accessors
  take a ``source_id`` now (``pairs/<source_id>/…``), because one workspace
  pairs each of its sources separately.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

# --------------------------------------------------------------------------
# cflib
# --------------------------------------------------------------------------

from cflib import hashing as _hashing, items as _cf_items, jsonio as _jsonio
from cflib import state as _state, vocab as _vocab
from cflib.workspace import Workspace as _Workspace
from cflib.paths import WorkspacePaths as _WorkspacePaths
from cflib.yamlio import read_yaml as _read_yaml

# --------------------------------------------------------------------------
# vocabulary (cflib.vocab is the single source; these are its names)
# --------------------------------------------------------------------------

FINDING_KINDS = frozenset(_vocab.FINDING_KINDS)
MECHANICAL_KINDS = frozenset(_vocab.MECHANICAL_ONLY_KINDS)
SEVERITIES = frozenset(_vocab.SEVERITY_SET)
LEDGER_VERDICTS = frozenset(_vocab.LEDGER_VERDICTS)
VERDICTS_REQUIRING_ARGUMENT = frozenset(_vocab.VERDICTS_REQUIRING_ARGUMENT)
#: `cf recheck` statuses, sorted so an error message lists them the same way twice
RECHECK_STATUSES = tuple(sorted(_vocab.RECHECK_STATUS_SET))

#: verdicts that keep a finding open against the companion / that close it
OPEN_VERDICTS = frozenset({"CONFIRMED_CONTENT", "ATTRIBUTION_GAP", "UNSURE"})
CLOSED_VERDICTS = frozenset(LEDGER_VERDICTS - OPEN_VERDICTS)

#: routed `_meta.status` values
STATUS_UNCHALLENGED = _vocab.UNCHALLENGED
STATUS_CHALLENGED = _vocab.CHALLENGED

#: ledger verdict -> viewer verdict. Stage 13 (publish-verdicts) is out of v1
#: scope; the map is re-exported here so that stage does not invent a second one.
VIEWER_VERDICT = dict(_vocab.LEDGER_TO_VIEWER)


def resolution_ok(value: Any) -> bool:
    """True when *value* is a legal ``resolution`` token (None counts as open)."""
    if value in (None, ""):
        return True
    try:
        _vocab.check_resolution(str(value))
    except Exception:  # noqa: BLE001 - check_resolution names the legal tokens
        return False
    return True


# --------------------------------------------------------------------------
# ids
# --------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def normalise_item(item: Any) -> str:
    """Collapse whitespace in a book item label (NFKC, non-breaking spaces).

    Used for MATCHING only. The baseline lost ``"Exercise    1.3.19"`` to a
    single-space regex; ids are still computed from the label as written, so
    that they agree with the corpus.
    """
    if item is None:
        return ""
    s = unicodedata.normalize("NFKC", str(item)).replace(" ", " ")
    return _WS.sub(" ", s).strip()


def item_join_key(item: Any) -> str:
    """The canonical key a book item is MATCHED on.

    ``normalise_item`` above is whitespace-only, which is right for a label that
    is about to be written down or hashed and wrong for a label that is about to
    be looked up.  Items with no number are labelled by whoever wrote the label,
    and the corpus has two generations of spelling for them --
    ``"In-text, book p. 71"`` (v1 findings) and ``"In-text p.71"`` (extraction).
    Matching on the whitespace form, every in-text row in one generation misses
    every in-text row in the other: the routed file loses the anchor row, the
    adjudication lens finds no peer statements, and ``match_finding`` files the
    row as unlinked.

    Numbered labels come back byte-identical to ``normalise_item``; only in-text
    labels move, onto ``cflib.items.join_key``.  Nothing here is hashed --
    ``finding_id`` still goes through ``cflib.items.clean`` on the label as
    written -- so no id changes.

    The kinds are the default list: a ledger row does not carry its source's
    kinds, and threading them here would mean threading a source through every
    matcher.  A source with a different kind list therefore keeps an ANCHORED
    in-text label ("In-text Widget 2.3.4") as written -- the behaviour before
    this function existed, never a guess -- while page-keyed labels, which is
    what the grammar reads without kinds, canonicalise for every source.
    """
    text = normalise_item(item)
    if not text:
        return text
    key, _reject = _cf_items.try_join_key(text)
    return key


def normalise_decl(decl: Any) -> str:
    """Normalise a ``repo:decl`` id (strip whitespace, keep the prefix)."""
    if decl is None:
        return ""
    return _WS.sub("", str(decl)).strip()


def decl_repo(decl: Any) -> str:
    """The repo prefix of a ``repo:decl`` id, or '' when unprefixed."""
    s = normalise_decl(decl)
    return s.split(":", 1)[0] if ":" in s else ""


def finding_id(run_id: str, source_id: str, item: str, kind: str,
               unfaithful_decls: Iterable[str]) -> str:
    """``sha256(run_id, source_id, clean(item), kind, sorted(unfaithful_decls))``.

    Delegates to :func:`cflib.hashing.finding_id`, the single definition.
    """
    decls = sorted({str(d).strip() for d in (unfaithful_decls or []) if str(d).strip()})
    return _hashing.finding_id(str(run_id or ""), str(source_id or ""), str(item or ""),
                               str(kind or ""), decls)


def sha256_file(path: str) -> str:
    """sha256 of a file's bytes."""
    return _hashing.sha256_file(path)


def sha256_text(text: str) -> str:
    """sha256 of a unicode string (utf-8)."""
    return _hashing.sha256_text(text)


# --------------------------------------------------------------------------
# json / jsonl io (atomic writes; cflib.jsonio when present)
# --------------------------------------------------------------------------


def read_json(path: str) -> Any:
    """Read one JSON document."""
    return _jsonio.read_json(path)


def read_jsonl(path: str) -> list[dict]:
    """Read a .jsonl file (``_meta`` rows included; blank lines skipped)."""
    return _jsonio.read_jsonl(path)


def is_meta_row(row: Any) -> bool:
    """True for the ``_meta`` header row every workspace .jsonl carries."""
    return isinstance(row, dict) and "_meta" in row


def data_rows(rows: Iterable[dict]) -> list[dict]:
    """Drop ``_meta`` header rows."""
    return [r for r in rows if isinstance(r, dict) and not is_meta_row(r)]


def meta_row(rows: Iterable[dict]) -> dict:
    """The ``_meta`` payload of a row list ({} when there is none)."""
    for r in rows:
        if is_meta_row(r):
            m = r["_meta"]
            return m if isinstance(m, dict) else {"note": m}
    return {}


def write_jsonl(path: str, rows: Iterable[dict], meta: dict | None = None) -> int:
    """Write rows (optionally behind a ``_meta`` header) atomically.

    Returns the number of DATA rows written.
    """
    rows = list(rows)
    _jsonio.write_jsonl(path, rows, meta=meta)
    return len(rows)


def write_json(path: str, payload: Any) -> None:
    """Write one JSON document atomically."""
    _jsonio.write_json(path, payload, indent=1, sort_keys=True)


def write_text(path: str, text: str) -> None:
    """Write a text file atomically."""
    _jsonio.atomic_write(path, text)


def load_yaml(path: str) -> Any:
    """Load a YAML file (cflib's reader: PyYAML when present, its subset otherwise)."""
    return _read_yaml(path)


# --------------------------------------------------------------------------
# workspace paths and state
# --------------------------------------------------------------------------


class LedgerPaths:
    """``cflib.paths.WorkspacePaths`` plus the short names this stage uses.

    Unknown attributes fall through to the wrapped object, so cflib accessors
    (``statements_jsonl``, ``run_yaml``, ``tally_json`` ...) keep working.
    """

    def __init__(self, inner: Any, root: str):
        self._inner = inner
        self.root = str(root)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    # -- the names ledger/route/report call ------------------------------
    def companion(self, name: str) -> str:
        return str(self._inner.companion_dir(name))

    def statements(self, name: str) -> str:
        return str(self._inner.statements_jsonl(name))

    def conventions(self, name: str) -> str:
        return str(self._inner.conventions_md(name))

    def state(self, name: str) -> str:
        return str(self._inner.state_json(name))

    def corpus(self, run_id: str = "") -> str:
        return str(self._inner.corpus_run_dir(run_id) if run_id else self._inner.corpus_dir)

    def run(self, run_id: str) -> str:
        return str(self._inner.run_dir(run_id))

    @property
    def ledger(self) -> str:
        return str(self._inner.ledger_dir)

    @property
    def routed(self) -> str:
        return str(self._inner.routed_dir)

    @property
    def reports(self) -> str:
        return str(self._inner.reports_dir)

    def pairs(self, source_id: str = "") -> str:
        """``pairs/`` or ``pairs/<source_id>/`` -- one pairing per source."""
        if not source_id:
            return str(self._inner.pairs_root)
        return str(self._inner.pairs_dir(source_id))

    def anchor_check(self, source_id: str) -> str:
        return str(self._inner.anchor_check_json(source_id))

    def list_paired_sources(self) -> list:
        return list(self._inner.list_paired_sources())

    @property
    def runs(self) -> str:
        return str(self._inner.runs_dir)

    def accepted(self, name: str) -> str:
        return str(self._inner.accepted_jsonl(name))

    def refuted(self, name: str) -> str:
        return str(self._inner.refuted_jsonl(name))

    def adjudicated_md(self, name: str) -> str:
        return str(self._inner.adjudicated_md(name))

    def routed_findings(self, name: str) -> str:
        return str(self._inner.routed_jsonl(name))

    def args_file(self, kind: str, name: str) -> str:
        """Where `cf adjudicate --emit-args` writes a workflow's args."""
        return os.path.join(self.ledger, "args", "%s.%s.json" % (kind, name))


def workspace_paths(root: str) -> LedgerPaths:
    """Paths inside a workspace directory."""
    return LedgerPaths(_WorkspacePaths(root=Path(root)), root)


class SourceInfo:
    """One source as the ledger/route/report stages need it.

    A plain record rather than ``cflib.workspace.SourceRef`` so that a workspace
    with no readable workspace.yaml -- which :func:`load_workspace` still routes
    and reports on -- produces the same shape as one with.
    """

    __slots__ = ("id", "sha256", "title", "edition", "offset", "kinds", "errata_url",
                 "chapters", "role", "confirmed")

    def __init__(self, source_id: str, data: dict | None = None):
        data = data or {}
        self.id = str(source_id)
        self.sha256 = str(data.get("sha256") or "")
        self.title = str(data.get("title") or "")
        self.edition = str(data.get("edition") or "")
        self.offset = int(data.get("offset") or 0)
        self.kinds = tuple(str(k) for k in (data.get("kinds") or ()))
        self.errata_url = str(data.get("errata_url") or "")
        self.chapters = tuple(int(c) for c in (data.get("chapters") or ()))
        self.role = str(data.get("role") or "audited")
        self.confirmed = bool(data.get("confirmed", False))

    @classmethod
    def from_ref(cls, ref: Any) -> "SourceInfo":
        return cls(ref.id, {
            "sha256": ref.sha256, "title": ref.title, "edition": ref.edition,
            "offset": ref.offset, "kinds": ref.kinds, "errata_url": ref.errata_url,
            "chapters": ref.chapters, "role": ref.role, "confirmed": ref.confirmed,
        })

    def to_dict(self) -> dict:
        return {"source_id": self.id, "source_sha": self.sha256, "title": self.title,
                "edition": self.edition, "offset": self.offset, "kinds": list(self.kinds),
                "errata_url": self.errata_url, "chapters": list(self.chapters),
                "role": self.role, "confirmed": self.confirmed}

    def short(self) -> str:
        return f"{self.id} ({self.sha256[:8]})" if self.sha256 else self.id


class WorkspaceInfo:
    """What ledger/route/report need from workspace.yaml.

    Wraps ``cflib.workspace.Workspace`` when the file is there and valid; falls
    back to an empty configuration so that a scratch directory (a test, or a
    workspace mid-``cf init``) can still be routed and reported on.

    There is no ``book``: a workspace holds a LIST of sources, and every method
    that used to answer "the book's sha / offset / kinds" now takes a
    ``source_id``.  ``require_source_sha`` is the source gate at this boundary.
    """

    def __init__(self, root: str, data: dict | None = None, workspace: Any = None):
        self.root = os.path.abspath(root)
        self._workspace = workspace
        self.data = data if data is not None else (getattr(workspace, "data", {}) or {})

    # -- sources ---------------------------------------------------------
    @property
    def sources(self) -> dict:
        """``{source_id: SourceInfo}`` in workspace.yaml order."""
        if self._workspace is not None:
            return {sid: SourceInfo.from_ref(ref)
                    for sid, ref in self._workspace.sources.items()}
        out: dict[str, SourceInfo] = {}
        for sid, body in (self.data.get("sources") or {}).items():
            out[str(sid)] = SourceInfo(str(sid), body if isinstance(body, dict) else {})
        return out

    def source_ids(self) -> list:
        return list(self.sources)

    def source(self, source_id: str) -> SourceInfo:
        """One source, or an EMPTY record for an unknown id.

        Empty rather than raising: routing a stray corpus must still produce a
        report that says the id is unknown, which is more use than a traceback.
        ``require_source_sha`` is where an unknown source is refused.
        """
        return self.sources.get(str(source_id)) or SourceInfo(str(source_id))

    def source_sha(self, source_id: str) -> str:
        return self.source(source_id).sha256

    def source_shas(self) -> dict:
        return {sid: src.sha256 for sid, src in self.sources.items() if src.sha256}

    def offset(self, source_id: str) -> int:
        return self.source(source_id).offset

    def kinds(self, source_id: str) -> tuple:
        return self.source(source_id).kinds

    def errata_url(self, source_id: str) -> str:
        return self.source(source_id).errata_url

    def require_source_sha(self, source_id: str, sha: str | None, where: str) -> None:
        """The source gate: refuse an artifact stamped with another edition."""
        want = self.source_sha(source_id)
        if not want:
            raise ValueError(
                "%s names source %r, which this workspace does not have "
                "(known: %s)" % (where, source_id, ", ".join(self.source_ids()) or "none"))
        if sha and str(sha) != want:
            raise ValueError(
                "%s: source_sha %s… is not %s's %s… — wrong edition"
                % (where, str(sha)[:12], source_id, want[:12]))

    @property
    def title(self) -> str:
        """A human name for the workspace (never one source's title)."""
        return str(self.data.get("name") or os.path.basename(self.root))

    # -- companions ------------------------------------------------------
    @property
    def paths(self) -> LedgerPaths:
        return workspace_paths(self.root)

    def companion_names(self) -> list:
        if self._workspace is not None:
            return list(self._workspace.companion_names())
        names = [str(n) for n in (self.data.get("companions") or {})]
        for name in self.paths.list_companions():
            if name not in names:
                names.append(name)
        return names

    def companion(self, name: str) -> dict:
        """One companion as a plain dict (repo, adapter, sources, anchor_source)."""
        if self._workspace is not None:
            try:
                ref = self._workspace.companion(name)
            except Exception:  # noqa: BLE001 - unknown companion -> empty record
                return {"name": name}
            return {"name": ref.name, "repo": str(ref.repo or ""), "adapter": ref.adapter,
                    "sources": list(ref.sources), "anchor_source": ref.anchor_source,
                    "owner": ref.owner, "write_policy": ref.write_policy,
                    "commit": ref.commit}
        body = (self.data.get("companions") or {}).get(name)
        if isinstance(body, dict):
            return {"name": name, **body}
        return {"name": name}

    def sources_of(self, companion: str) -> list:
        """The source ids one companion is audited against."""
        if self._workspace is not None:
            try:
                return list(self._workspace.sources_of(companion))
            except Exception:  # noqa: BLE001
                return []
        listed = self.companion(companion).get("sources") or []
        return [str(s) for s in listed]

    def anchor_source(self, companion: str) -> str:
        """The source whose PAGINATION a companion's anchors follow."""
        if self._workspace is not None:
            try:
                return str(self._workspace.anchor_source(companion))
            except Exception:  # noqa: BLE001
                return ""
        entry = self.companion(companion)
        ids = self.sources_of(companion)
        return str(entry.get("anchor_source") or (ids[0] if ids else ""))

    @property
    def policy(self) -> dict:
        return self.data.get("policy") or {}

    @property
    def models(self) -> dict:
        return self.data.get("models") or {}


def load_workspace(root: str | None) -> WorkspaceInfo:
    """Load a workspace; a directory without workspace.yaml yields empty config.

    An EXPLICIT root is taken literally: unlike ``cflib.workspace.find_workspace_root``
    this does not walk up to an ancestor's workspace.yaml, because a caller that
    names a directory means that directory (a scratch workspace inside another
    one must not inherit it).
    """
    explicit = bool(root)
    if not explicit or os.path.exists(os.path.join(str(root), "workspace.yaml")):
        try:
            ws = _Workspace.load(root)
            return WorkspaceInfo(str(ws.root), ws.data, ws)
        except Exception:
            pass
    root = root or os.environ.get("CF_WORKSPACE") or os.getcwd()
    path = os.path.join(root, "workspace.yaml")
    data: dict = {}
    if os.path.exists(path):
        try:
            loaded = load_yaml(path)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    return WorkspaceInfo(root, data)


def read_state(paths: Any, companion: str) -> dict:
    """Read companions/<name>/state.json ({} when absent or unreadable)."""
    try:
        return _state.read_state(paths, companion, missing_ok=True)
    except Exception:  # noqa: BLE001 - a half-written state.json must not stop a report
        return {}


def update_state(paths: Any, companion: str, mutate: Callable[[dict], None]) -> dict:
    """Read-modify-write companions/<name>/state.json; returns the new state."""
    with _state.update(paths, companion) as st:
        mutate(st)
        return st


def state_section(state: dict, section: str, values: dict) -> dict:
    """Set one state.json section, stamped (cflib.state.set_section)."""
    return _state.set_section(state, section, values)


def iter_json_files(directory: str) -> Iterator[str]:
    """Yield *.json paths in *directory*, sorted, skipping receipts/manifests."""
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        if name in ("RECEIPT.json", "run.yaml.json", "manifest.json", "index.json"):
            continue
        yield os.path.join(directory, name)
