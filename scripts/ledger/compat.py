"""cflib adapter for the ledger / route / report stages.

The shared library is ``scripts/cflib`` (vocabulary, paths, campaign, state,
json io, hashing).  This module is the thin translation layer those three stages
import, so that a rename in cflib is a one-file change here rather than a
rewrite of five modules.  Everything it exports resolves to cflib when cflib is
importable; the small fallbacks exist only so the stage can be exercised
stand-alone (the same pattern as ``book/compat.py`` and ``pair/compat.py``).

Delete this file once every call site has been switched to cflib directly.

Two deliberate choices worth knowing about:

* ``finding_id`` is ``cflib.hashing.finding_id``, the ONE definition.  It used
  to prefer ``gate.verify_findings.finding_id`` because the two disagreed on the
  join byte (newline vs NUL/'|') and a ledger row must link to the id its corpus
  file carries; they now agree byte for byte (the gate inlines the same
  algorithm because it must run with no cflib on the path) and the test suite
  asserts it, so the preference is gone.
* ``LedgerPaths`` wraps ``cflib.paths.CampaignPaths`` and adds the short
  accessors this stage uses (``accepted``/``refuted``/``routed_findings``);
  every cflib accessor still passes through untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from typing import Any, Callable, Iterable, Iterator

# --------------------------------------------------------------------------
# cflib
# --------------------------------------------------------------------------

try:
    from cflib import hashing as _hashing, jsonio as _jsonio, state as _state, vocab as _vocab
    from cflib.campaign import Campaign as _Campaign
    from cflib.paths import CampaignPaths as _CampaignPaths
    from cflib.yamlio import read_yaml as _read_yaml

    _HAVE_CFLIB = True
except Exception:  # pragma: no cover - stand-alone use only
    _hashing = _jsonio = _state = _vocab = None  # type: ignore[assignment]
    _Campaign = _CampaignPaths = None  # type: ignore[assignment]
    _read_yaml = None  # type: ignore[assignment]
    _HAVE_CFLIB = False


def _clean_item_fallback(raw: str) -> str:
    """Stand-alone copy of ``cflib.items.clean`` (see finding_id below)."""
    s = unicodedata.normalize("NFKC", raw or "")
    for bad in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212":
        s = s.replace(bad, "-")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip().strip("`").strip()
    return s.rstrip(" .,;:").strip()


# --------------------------------------------------------------------------
# vocabulary (cflib.vocab is the single source; these are its names)
# --------------------------------------------------------------------------

_FALLBACK = {
    "FINDING_KINDS": ("CONTENT", "VACUITY", "PROXY", "SUSPECTED_ERRATA", "COVERAGE", "PAGE_ANCHOR"),
    "MECHANICAL_ONLY_KINDS": ("PAGE_ANCHOR",),
    "SEVERITY_SET": ("low", "medium", "high"),
    "LEDGER_VERDICTS": ("CONFIRMED_CONTENT", "ATTRIBUTION_GAP", "CONVENTION", "ERRATA",
                        "REFUTED", "UNSURE"),
    "VERDICTS_REQUIRING_ARGUMENT": ("CONVENTION", "ERRATA"),
}


def _vocab_set(name: str) -> frozenset:
    if _vocab is not None and hasattr(_vocab, name):
        return frozenset(getattr(_vocab, name))
    return frozenset(_FALLBACK[name])


FINDING_KINDS = _vocab_set("FINDING_KINDS")
MECHANICAL_KINDS = _vocab_set("MECHANICAL_ONLY_KINDS")
SEVERITIES = _vocab_set("SEVERITY_SET")
LEDGER_VERDICTS = _vocab_set("LEDGER_VERDICTS")
VERDICTS_REQUIRING_ARGUMENT = _vocab_set("VERDICTS_REQUIRING_ARGUMENT")

#: verdicts that keep a finding open against the companion / that close it
OPEN_VERDICTS = frozenset({"CONFIRMED_CONTENT", "ATTRIBUTION_GAP", "UNSURE"})
CLOSED_VERDICTS = frozenset(LEDGER_VERDICTS - OPEN_VERDICTS)

#: routed `_meta.status` values
STATUS_UNCHALLENGED = getattr(_vocab, "UNCHALLENGED", "UNCHALLENGED") if _vocab else "UNCHALLENGED"
STATUS_CHALLENGED = getattr(_vocab, "CHALLENGED", "CHALLENGED") if _vocab else "CHALLENGED"

#: ledger verdict -> viewer verdict. Stage 13 (publish-verdicts) is out of v1
#: scope; the map is re-exported here so that stage does not invent a second one.
VIEWER_VERDICT = dict(getattr(_vocab, "LEDGER_TO_VIEWER", {})) if _vocab else {}

_RESOLUTION_RE = re.compile(
    r"^(fixed:PR#\d+|in_progress:[A-Za-z0-9._/#+-]+|refuted|argued_correct|out_of_scope)$"
)


def resolution_ok(value: Any) -> bool:
    """True when *value* is a legal ``resolution`` token (None counts as open)."""
    if value in (None, ""):
        return True
    text = str(value)
    if _vocab is not None and hasattr(_vocab, "check_resolution"):
        try:
            _vocab.check_resolution(text)
            return True
        except Exception:
            return False
    return bool(_RESOLUTION_RE.match(text))


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

    Delegates to :func:`cflib.hashing.finding_id`, the single definition; the
    fallback below inlines the identical algorithm for stand-alone use.
    """
    decls = sorted({str(d).strip() for d in (unfaithful_decls or []) if str(d).strip()})
    if _hashing is not None:
        return _hashing.finding_id(str(run_id or ""), str(source_id or ""), str(item or ""),
                                   str(kind or ""), decls)
    payload = "\n".join(
        [str(run_id or ""), str(source_id or ""), _clean_item_fallback(str(item or "")),
         str(kind or ""), *decls]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    """sha256 of a file's bytes."""
    if _hashing is not None:
        return _hashing.sha256_file(path)
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    """sha256 of a unicode string (utf-8)."""
    if _hashing is not None:
        return _hashing.sha256_text(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# json / jsonl io (atomic writes; cflib.jsonio when present)
# --------------------------------------------------------------------------


def read_json(path: str) -> Any:
    """Read one JSON document."""
    if _jsonio is not None:
        return _jsonio.read_json(path)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_jsonl(path: str) -> list[dict]:
    """Read a .jsonl file (``_meta`` rows included; blank lines skipped)."""
    if _jsonio is not None:
        return _jsonio.read_jsonl(path)
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError("%s:%d: %s" % (path, n, exc)) from exc
    return rows


def is_meta_row(row: Any) -> bool:
    """True for the ``_meta`` header row every campaign .jsonl carries."""
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
    if _jsonio is not None:
        _jsonio.write_jsonl(path, rows, meta=meta)
        return len(rows)
    os.makedirs(os.path.dirname(os.path.abspath(str(path))) or ".", exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        if meta is not None:
            fh.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, str(path))
    return len(rows)


def write_json(path: str, payload: Any) -> None:
    """Write one JSON document atomically."""
    if _jsonio is not None:
        _jsonio.write_json(path, payload, indent=1, sort_keys=True)
        return
    os.makedirs(os.path.dirname(os.path.abspath(str(path))) or ".", exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, str(path))


def write_text(path: str, text: str) -> None:
    """Write a text file atomically."""
    if _jsonio is not None:
        _jsonio.atomic_write(path, text)
        return
    os.makedirs(os.path.dirname(os.path.abspath(str(path))) or ".", exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, str(path))


def load_yaml(path: str) -> Any:
    """Load a YAML file (PyYAML when present, cflib's subset reader otherwise)."""
    if _read_yaml is not None:
        return _read_yaml(path)
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("no YAML reader available for %s" % path) from exc
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------
# campaign paths and state
# --------------------------------------------------------------------------


class LedgerPaths:
    """``cflib.paths.CampaignPaths`` plus the short names this stage uses.

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

    @property
    def pairs(self) -> str:
        return str(self._inner.pairs_dir)

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


class _PlainPaths:
    """Fallback CampaignPaths for stand-alone use (cflib absent)."""

    def __init__(self, root: str, book_id: str = ""):
        self.root = os.path.abspath(root)
        self.book_id = book_id

    def companion_dir(self, name): return os.path.join(self.root, "companions", name)
    def statements_jsonl(self, name): return os.path.join(self.companion_dir(name), "statements.jsonl")
    def conventions_md(self, name): return os.path.join(self.companion_dir(name), "conventions.md")
    def state_json(self, name): return os.path.join(self.companion_dir(name), "state.json")
    def corpus_run_dir(self, run_id): return os.path.join(self.root, "corpus", run_id)
    def run_dir(self, run_id): return os.path.join(self.root, "runs", run_id)
    def accepted_jsonl(self, name): return os.path.join(self.root, "ledger", "accepted.%s.jsonl" % name)
    def refuted_jsonl(self, name): return os.path.join(self.root, "ledger", "refuted.%s.jsonl" % name)
    def adjudicated_md(self, name): return os.path.join(self.root, "ledger", "adjudicated.%s.md" % name)
    def routed_jsonl(self, name): return os.path.join(self.root, "ledger", "routed", "findings.%s.jsonl" % name)
    def book_dir_for(self, book_id): return os.path.join(self.root, "book", book_id)

    @property
    def corpus_dir(self): return os.path.join(self.root, "corpus")
    @property
    def runs_dir(self): return os.path.join(self.root, "runs")
    @property
    def ledger_dir(self): return os.path.join(self.root, "ledger")
    @property
    def routed_dir(self): return os.path.join(self.ledger_dir, "routed")
    @property
    def reports_dir(self): return os.path.join(self.root, "reports")
    @property
    def pairs_dir(self): return os.path.join(self.root, "pairs")
    @property
    def anchor_check_json(self): return os.path.join(self.pairs_dir, "anchor-check.json")
    @property
    def book_dir(self): return os.path.join(self.root, "book", self.book_id)
    @property
    def companions_dir(self): return os.path.join(self.root, "companions")

    def list_companions(self) -> list[str]:
        d = self.companions_dir
        if not os.path.isdir(d):
            return []
        return sorted(n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n)))


def campaign_paths(root: str, book_id: str = "") -> LedgerPaths:
    """Paths inside a campaign directory."""
    if _CampaignPaths is not None:
        from pathlib import Path

        return LedgerPaths(_CampaignPaths(root=Path(root), book_id=book_id), root)
    return LedgerPaths(_PlainPaths(root, book_id), root)


class CampaignInfo:
    """What ledger/route/report need from campaign.yaml.

    Wraps ``cflib.campaign.Campaign`` when the file is there and valid; falls
    back to an empty configuration so that a scratch directory (a test, or a
    campaign mid-``cf init``) can still be routed and reported on.
    """

    def __init__(self, root: str, data: dict | None = None, campaign: Any = None):
        self.root = os.path.abspath(root)
        self._campaign = campaign
        self.data = data if data is not None else (getattr(campaign, "data", {}) or {})

    # -- book ------------------------------------------------------------
    @property
    def book(self) -> dict:
        return self.data.get("book") or {}

    @property
    def book_id(self) -> str:
        if self._campaign is not None:
            return self._campaign.book_id
        return str(self.book.get("id") or "")

    @property
    def book_sha(self) -> str:
        if self._campaign is not None:
            return str(self._campaign.book_sha or "")
        return str(self.book.get("sha256") or "")

    @property
    def offset(self) -> int:
        if self._campaign is not None:
            return int(self._campaign.offset or 0)
        return int(self.book.get("offset") or 0)

    @property
    def errata_url(self) -> str:
        return str(self.book.get("errata_url") or "")

    @property
    def title(self) -> str:
        return str(self.book.get("title") or self.data.get("name") or self.book_id or
                   os.path.basename(self.root))

    # -- companions ------------------------------------------------------
    @property
    def paths(self) -> LedgerPaths:
        return campaign_paths(self.root, self.book_id)

    def companion_names(self) -> list[str]:
        if self._campaign is not None:
            return list(self._campaign.companion_names())
        names = [str(c.get("name")) for c in (self.data.get("companions") or [])
                 if isinstance(c, dict) and c.get("name")]
        for name in self.paths.list_companions():
            if name not in names:
                names.append(name)
        return names

    def companion(self, name: str) -> dict:
        for c in self.data.get("companions") or []:
            if isinstance(c, dict) and c.get("name") == name:
                return c
        return {"name": name}

    @property
    def policy(self) -> dict:
        return self.data.get("policy") or {}

    @property
    def models(self) -> dict:
        return self.data.get("models") or {}


def load_campaign(root: str | None) -> CampaignInfo:
    """Load a campaign; a directory without campaign.yaml yields empty config.

    An EXPLICIT root is taken literally: unlike ``cflib.campaign.find_campaign_root``
    this does not walk up to an ancestor's campaign.yaml, because a caller that
    names a directory means that directory (a scratch campaign inside another
    one must not inherit it).
    """
    explicit = bool(root)
    if _Campaign is not None and (not explicit or os.path.exists(os.path.join(str(root), "campaign.yaml"))):
        try:
            camp = _Campaign.load(root)
            return CampaignInfo(str(camp.root), camp.data, camp)
        except Exception:
            pass
    root = root or os.environ.get("CF_CAMPAIGN") or os.getcwd()
    path = os.path.join(root, "campaign.yaml")
    data: dict = {}
    if os.path.exists(path):
        try:
            loaded = load_yaml(path)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    return CampaignInfo(root, data)


def read_state(paths: Any, companion: str) -> dict:
    """Read companions/<name>/state.json ({} when absent or unreadable)."""
    if _state is not None:
        try:
            return _state.read_state(paths, companion, missing_ok=True)
        except Exception:
            return {}
    path = paths.state(companion)
    if not os.path.exists(path):
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}


def update_state(paths: Any, companion: str, mutate: Callable[[dict], None]) -> dict:
    """Read-modify-write companions/<name>/state.json; returns the new state."""
    if _state is not None:
        with _state.update(paths, companion) as st:
            mutate(st)
            return st
    state = read_state(paths, companion) or {"name": companion}
    mutate(state)
    write_json(paths.state(companion), state)
    return state


def state_section(state: dict, section: str, values: dict) -> dict:
    """Set one state.json section, stamped (cflib.state.set_section)."""
    if _state is not None:
        return _state.set_section(state, section, values)
    body = dict(values)
    state[section] = body
    return body


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
