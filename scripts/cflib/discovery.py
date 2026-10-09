"""Discovery: read the repo, find its sources, fingerprint the bytes.

docs/design/archive/multi-source-model.md R2 / S4.  `cf onboard <repo>` types nothing:
this module reads a companion repository -- and NEVER writes to it -- and
answers four questions the user used to answer with flags.

    detect_adapter(repo)          which convention is this repo?
    discover_sources(repo)        what sources does it name?      (S1-S7)
    locate_pdf(claim, ...)        where are those bytes on THIS machine?
    choose_anchor_source(...)     whose pagination do its anchors follow?

Three rules keep it honest:

1. **A truncated sha is a candidate, never an identity.**  ``64498329…`` in a
   CLAUDE.md resolves only through a registry alias or a real fingerprint;
   unresolved prefixes are reported, not adopted.
2. **A contradicting sha is never adopted.**  A file that looks right but
   fingerprints to another edition becomes a warning, because that is exactly
   how batch-1's anchor findings were judged against the 2011 draft.
3. **Nothing is downloaded** unless ``--download`` was passed AND the workspace
   policy allows it AND the registry's licence note says the author publishes
   the file.  Nothing is ever redistributed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .errors import CfError, DiscoveryError
from .hashing import sha256_file, sha256_text
from .log import detail, warn
from .registry import Registry, RegistryEntry, _norm_isbn, _norm_title, source_id_for
from .yamlio import load_yaml

#: the seven repo signals, in the order S1..S7 of the design
SIGNALS = ("formalization_yaml", "ledger_source", "official_source_json",
           "graph_json", "prose", "filesystem", "citation")

MIN_ANCHOR_AGREEMENT = 0.80
MIN_ANCHOR_MARGIN = 0.05
MIN_ADAPTER_CONFIDENCE = 0.5

#: :attr:`SourceClaim.discovered_by`, and the value `cf onboard` writes into
#: ``sources.<id>.discovered.pdf_found_in`` for such a source.  Named here, with
#: the field, because both the adapter that sets it and the command that reads it
#: have to spell it the same way.
DISCOVERED_BY_ADAPTER = "adapter"
DISCOVERED_BY_MANUAL = "manual"

#: a sha we cannot find is worth saying once per candidate, not 450 times: past
#: this many `edition_mismatch` lines the search stops reporting and just keeps
#: looking.
MAX_MISMATCH_WARNINGS = 8

#: prose files S5 reads (repo-relative; docs/**/*.md is added at scan time)
PROSE_FILES = ("CLAUDE.md", "AGENTS.md", "README.md")

_SHA_FULL_RE = re.compile(r"\b([0-9a-f]{64})\b")
_SHA_PREFIX_RE = re.compile(r"\b([0-9a-f]{8,63})\s*(?:…|\.\.\.)")
_URL_RE = re.compile(r"https?://[^\s)>\"'\]]+")
_PDF_PATH_RE = re.compile(r"[`\"'(]([^`\"'()\s]+\.pdf)[`\"')]", re.I)
_ISBN_RE = re.compile(r"ISBN[\s:]*([0-9][0-9\-xX]{8,})", re.I)
_OFFSET_RE = re.compile(
    r"(?:PDF|pdf)\s*page\s*=\s*(?:book|printed)\s*page\s*\+\s*(\d+)"
    r"|(?:book|printed)\s*page\s*=\s*(?:PDF|pdf)\s*page\s*-\s*(\d+)")
#: applied to a paragraph that has ALREADY been flattened to one line (see
#: _anchor_hint): the sentence that says which edition the anchors follow is
#: line-wrapped in both target repos, so a newline-sensitive scan misses it.
_ANCHOR_SENT_RE = re.compile(r"[^.]*\banchors?\b[^.]*\.", re.I)
_EDITION_WORD_RE = re.compile(
    r"\b(published|draft|preprint|official|revised|corrected|final|preliminary)\b", re.I)


# --------------------------------------------------------------------------
# dataclasses
# --------------------------------------------------------------------------

@dataclass
class SourceClaim:
    """One repo signal's claim that a source exists.

    Claims are merged (:func:`merge_claims`) into candidate sources; each keeps
    the file it came from so `cf onboard` can print WHERE every inference came
    from and `cf status` can say what nobody has checked.
    """

    signal: str
    path: str = ""
    title: str = ""
    authors: tuple[str, ...] = ()
    edition: str = ""
    isbn: str = ""
    sha256: str = ""
    sha256_prefix: str = ""
    bytes: int | None = None
    pdf_hints: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    book_page_url: str = ""
    licence: str = ""
    offset_hint: int | None = None
    anchor_hint: str = ""
    #: item kinds the COMPANION's own tags use for this source (the grammar half
    #: of R2).  Only an adapter can fill this -- it is the one thing about a
    #: source that is read from the companion rather than from the source -- and
    #: it is what `cf sources` has to fall back on when the PDF is not on this
    #: machine and :func:`cflib.inference.infer_kinds` has no text to read.
    kinds: tuple[str, ...] = ()
    #: A source that IS files on this machine rather than a book to be located:
    #: the absolute paths whose CONCATENATED bytes are the source, in reading
    #: order.  A leanblueprint's manuscript is its own ``.tex`` tree and
    #: `cf onboard --source <file>` names one outright, so neither has a PDF to
    #: search for: :func:`resolve_claims` builds the body from these bytes and
    #: never calls :func:`locate_pdf`, which is also why the ``pdf_hints``
    #: boundary does not apply to them -- the repo is not choosing the path, the
    #: adapter's own glob or the operator is.
    files: tuple[str, ...] = ()
    #: ``pdf`` / ``text`` and ``formfeed`` / ``none`` for such a source, read off
    #: the bytes by `cf onboard` (``sources/locator.py`` owns both rules).
    source_format: str = ""
    pagination: str = ""
    #: how the ADAPTER locates an item in this source (``number``, ``label``).
    #: Provenance, recorded under ``discovered``: it is what the adapter keys its
    #: rows on, never a fact about the source's own pages.
    locator: str = ""
    #: who named this source: :data:`DISCOVERED_BY_ADAPTER` (the companion's own
    #: manuscript) or :data:`DISCOVERED_BY_MANUAL` (``cf onboard --source``).
    #: Empty for a book the seven signals described.
    discovered_by: str = ""
    confidence: float = 0.0
    sources: tuple[str, ...] = ()          # every file that contributed

    def __post_init__(self) -> None:
        if not self.sources and self.path:
            self.sources = (self.path,)
        self.sha256 = (self.sha256 or "").strip().lower()
        self.sha256_prefix = (self.sha256_prefix or "").strip().lower()

    # -- merging -------------------------------------------------------
    def merge(self, other: "SourceClaim") -> "SourceClaim":
        """Union the evidence; the higher-confidence claim wins each scalar."""
        hi, lo = (self, other) if self.confidence >= other.confidence else (other, self)

        def pick(field_name: str):
            return getattr(hi, field_name) or getattr(lo, field_name)

        return SourceClaim(
            signal=hi.signal,
            path=hi.path or lo.path,
            title=pick("title"),
            authors=hi.authors or lo.authors,
            edition=pick("edition"),
            isbn=pick("isbn"),
            sha256=pick("sha256"),
            sha256_prefix=pick("sha256_prefix"),
            bytes=hi.bytes if hi.bytes is not None else lo.bytes,
            pdf_hints=_uniq(self.pdf_hints + other.pdf_hints),
            urls=_uniq(self.urls + other.urls),
            book_page_url=pick("book_page_url"),
            licence=pick("licence"),
            offset_hint=hi.offset_hint if hi.offset_hint is not None else lo.offset_hint,
            anchor_hint=pick("anchor_hint"),
            # kinds are unioned, never picked: two signals that each see part of
            # the grammar (an adapter's tags, a sibling claim's) both saw it.
            kinds=_uniq(hi.kinds + lo.kinds),
            # a claim that names its own bytes keeps them whichever side it was
            # on: a corroborating prose claim with the higher confidence must not
            # turn a manuscript back into a book nobody can find.
            files=hi.files or lo.files,
            source_format=pick("source_format"),
            pagination=pick("pagination"),
            locator=pick("locator"),
            discovered_by=pick("discovered_by"),
            confidence=max(self.confidence, other.confidence),
            sources=_uniq(self.sources + other.sources),
        )

    def matches(self, other: "SourceClaim") -> bool:
        """Do two claims describe the same source?

        Full sha first, then a resolvable prefix, then (last resort) the same
        normalised title AND a compatible edition token -- never the title alone,
        because two editions of one book share it.

        One more join, and it is the common case for a repo nobody has
        catalogued: one signal names the WORK (``formalization.yaml`` gives a
        title and no file) while another names the FILE (the filesystem scan and
        the prose give ``sources/<name>.pdf`` and no title).  Every claim here
        came from the same repository, so when the filename echoes the title they
        are one source -- and without this branch they became two source ids for
        one PDF, which `Workspace.validate` then rejected forever.
        """
        if self.sha256 and other.sha256:
            return self.sha256 == other.sha256
        if self.sha256 and other.sha256_prefix:
            return self.sha256.startswith(other.sha256_prefix)
        if other.sha256 and self.sha256_prefix:
            return other.sha256.startswith(self.sha256_prefix)
        if self.sha256_prefix and other.sha256_prefix:
            a, b = self.sha256_prefix, other.sha256_prefix
            return a.startswith(b) or b.startswith(a)
        if self.sha256 or other.sha256:
            # one side is pinned to bytes and the other names none: only a
            # shared pdf hint can join them
            return bool(set(_basenames(self.pdf_hints)) & set(_basenames(other.pdf_hints)))
        if self.title and other.title and _norm_title(self.title) == _norm_title(other.title):
            return _edition_compatible(self.edition, other.edition)
        if self.title and not self.pdf_hints and other.pdf_hints and not other.title:
            return _name_echoes_title(other.pdf_hints, self.title)
        if other.title and not other.pdf_hints and self.pdf_hints and not self.title:
            return _name_echoes_title(self.pdf_hints, other.title)
        return bool(set(_basenames(self.pdf_hints)) & set(_basenames(other.pdf_hints)))


@dataclass
class LocatedPdf:
    """A PDF found on this machine, with its identity."""

    path: Path
    sha256: str
    bytes: int
    pages: int | None = None
    text_sha256: str | None = None
    where: str = "repo_declared"
    rank: int = 0
    header: str = ""


@dataclass
class DiscoveryResult:
    """Everything `cf onboard` learned, ready to register and to print."""

    repo: Path
    name: str
    adapter: str
    adapter_confidence: float = 0.0
    adapter_evidence: dict = field(default_factory=dict)
    claims: list[SourceClaim] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    #: which claim each source id was built from. Matching a claim to a body by
    #: the FILE they share cannot do this: two sources declared in one
    #: formalization.yaml share every signal path, and the overlay row for one of
    #: them was written from the other one's title.
    claims_by_source: dict[str, SourceClaim] = field(default_factory=dict)
    anchor_source: str = ""
    anchor_confidence: float = 0.0
    anchor_evidence: dict = field(default_factory=dict)
    bundled_sources: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def source_ids(self) -> list[str]:
        return [s["id"] for s in self.sources]

    def audited_ids(self) -> list[str]:
        return [s["id"] for s in self.sources if s.get("role", "audited") == "audited"]

    def get(self, source_id: str) -> dict | None:
        return next((s for s in self.sources if s["id"] == source_id), None)

    def render(self) -> str:
        """The block `cf onboard` prints (design S7.2): every inference + where it came from."""
        out: list[str] = []
        out.append(f"repo      : {self.repo}")
        out.append(f"adapter   : {self.adapter:<34s} confidence {self.adapter_confidence:.2f}"
                   + (f"   ({self.adapter_evidence.get('reason')})"
                      if self.adapter_evidence.get("reason") else ""))
        out.append(f"name      : {self.name}")
        out.append("")
        out.append("sources discovered")
        for body in self.sources:
            sha = body.get("sha256")
            role = body.get("role", "audited")
            head = (f"  {body['id']:<30s} sha {sha[:8] + '…' if sha else '(not located)':<10s}"
                    f" registry: {body.get('registry', 'local')}"
                    f" ({'confirmed' if body.get('confirmed') else 'UNCONFIRMED'})")
            if role != "audited":
                head += f"   role: {role}"
            out.append(head)
            if body.get("title"):
                authors = ", ".join(body.get("authors") or [])
                out.append(f"      title    {body['title']}" + (f" -- {authors}" if authors else ""))
            if body.get("edition"):
                out.append(f"      edition  {body['edition']}")
            frm = (body.get("discovered") or {}).get("from") or []
            if frm:
                out.append(f"      from     {', '.join(frm)}")
            manuscript = body.get("manuscript_files") or []
            if body.get("pdf"):
                # `--source` makes a text manuscript land on this line routinely,
                # and calling one "pdf" is the same wrong word this module already
                # took out of its refusals ("no source file", `cf sources add
                # <source>`): a source is not a PDF.
                label = "source" if body.get("source_format") == "text" else "pdf"
                out.append(f"      {label:<8s} {body['pdf']}")
                out.append(f"               found in: {(body.get('discovered') or {}).get('pdf_found_in')}")
            elif manuscript:
                # not "NOT FOUND": these bytes are in hand, they are simply not
                # one file yet -- `cf onboard` concatenates them in this order.
                out.append(f"      text     {len(manuscript)} file(s), concatenated in this order")
                for path in manuscript[:8]:
                    out.append(f"               {path}")
            else:
                out.append("      pdf      NOT FOUND on this machine "
                           "(text, items and the quote gate cannot be prepared)")
            if body.get("pagination"):
                out.append(f"      pages    {body['pagination']}"
                           + ("   (located by section and line, not by printed page)"
                              if body["pagination"] == "none" else ""))
            if body.get("offset") is not None:
                out.append(f"      offset   {body['offset']:<34d} confidence "
                           f"{float(body.get('offset_confidence') or 0):.3f}"
                           f"  ({body.get('offset_method')})")
            if body.get("kinds"):
                out.append("      kinds    " + " ".join(body["kinds"]))
            if body.get("errata_url"):
                out.append(f"      errata   {body['errata_url']}")
                out.append(f"               confidence {float(body.get('errata_confidence') or 0):.2f}")
            for sibling in body.get("same_work") or []:
                out.append(f"      same work as {sibling}")
        out.append("")
        out.append("anchor edition")
        if self.anchor_source:
            out.append(f"  {self.anchor_source:<44s} confidence {self.anchor_confidence:.2f}")
            table = (self.anchor_evidence or {}).get("agreement") or {}
            for sid, row in sorted(table.items()):
                out.append(f"      anchor agreement  {sid:<34s} {row.get('agreement', 0):.2f} "
                           f"({row.get('ok', 0)} ok / {row.get('mismatched', 0)} mismatched)")
            if (self.anchor_evidence or {}).get("reason"):
                out.append(f"      reason: {self.anchor_evidence['reason']}")
            if (self.anchor_evidence or {}).get("hint"):
                out.append(f"      corroborated by prose: \"{self.anchor_evidence['hint'][:110]}\"")
        else:
            out.append("  (undecided)")
        for sid in self.bundled_sources:
            out.append(f"  the repo BUNDLES {sid} but does not anchor to it -> bundled_sources")
        if self.unresolved:
            out.append("")
            out.append("unresolved")
            for item in self.unresolved:
                out.append(f"  {item}")
        if self.warnings:
            out.append("")
            for w in self.warnings:
                out.append(f"warning: {w}")
        return "\n".join(out)


# --------------------------------------------------------------------------
# S4.2 adapter detection
# --------------------------------------------------------------------------

def _adapters_api():
    """The real ``adapters.api`` module, or None when there is none to import.

    Imported lazily and never at module scope: nothing in cflib may depend on
    ``scripts/adapters/`` being importable (``cflib.adapters_api``), and this is
    the one place in discovery that reads it.

    The dance around ``sys.modules`` is for a caller that has REPLACED the
    top-level ``adapters`` module with a stand-in exposing only ``load()`` --
    which is how the suite onboards a repo without running a real parser.  A
    stand-in has no ``__path__``, so importing the submodule through it fails;
    detection is core behaviour (`cf onboard` cannot even choose a parser without
    it), so the real package is imported beside the stand-in and the stand-in is
    handed straight back.

    The stand-in is tested for FIRST, before the ``adapters.api`` shortcut, and
    ``registry()`` is called while the real package is still in ``sys.modules``.
    Both halves matter: any process that has imported the package at all leaves
    ``adapters.api`` cached, so the shortcut used to return early and the dance
    never ran; and ``registry()`` imports the shipped adapter modules on first
    call, which needs a parent with a ``__path__``.  Getting either wrong makes
    detection raise ``ImportError: cannot import name 'attribution_table' from
    'adapters'`` for every test class that onboards behind a stand-in, and makes
    a green suite a matter of which test file imported the adapters first.
    """
    import importlib
    import sys

    stand_in = sys.modules.get("adapters")
    if stand_in is not None and not hasattr(stand_in, "__path__"):
        del sys.modules["adapters"]
        try:
            mod = importlib.import_module("adapters.api")
            mod.registry()
            return mod
        except Exception:                                 # pragma: no cover - adapters absent
            return None
        finally:
            sys.modules["adapters"] = stand_in
    mod = sys.modules.get("adapters.api")
    if mod is not None and hasattr(mod, "registry"):
        return mod
    try:
        return importlib.import_module("adapters.api")
    except Exception:                                     # pragma: no cover - adapters absent
        return None


def _adapter_classes() -> dict:
    """``{kind: Adapter class}``, or ``{}`` when the adapters package is absent."""
    api = _adapters_api()
    if api is None:                                       # pragma: no cover - adapters absent
        return {}
    try:
        return dict(api.registry())
    except Exception:                                     # pragma: no cover - broken adapter
        return {}


def detect_scores(repo: Path) -> dict[str, tuple[float, dict]]:
    """``{kind: (score, evidence)}`` -- design S4.2, one entry per REGISTERED kind.

    The heuristic that recognises a convention lives on that convention's
    adapter (``adapters.api.Adapter.detect``); this is the ranking loop over
    them, so adding a convention is adding a file rather than editing the core
    as well.  Only kinds an adapter actually implements can appear, which is the
    behaviour change: a repo carrying a ``source-formalization-ledger.json`` used
    to score a flat 0.9 for the unimplemented ``ledger_nodes``, win on the
    maximum, and be refused at onboarding even when ``docstring_tags`` would
    have read it.
    """
    repo = Path(repo)
    api = _adapters_api()
    if api is None:                                       # pragma: no cover - adapters absent
        warn("the adapters package is not importable: no convention can be detected")
        return {}
    return {kind: (score, evidence)
            for kind, (score, evidence) in api.detect_all(repo).items() if score > 0}


def detect_report(repo: str | Path) -> list[str]:
    """Why every kind scored what it did -- the lines a refusal prints.

    Onboarding is the front door, and "cannot tell what kind of companion this
    is" told the user only that it had failed: not what was looked for, not what
    was found, not which convention was nearly matched.  These lines are the
    ranked scores followed by one "looked for / found" line per kind, each
    naming the override that would settle it.

    A detector that RAISED is printed too.  ``detect_all`` records the exception
    under ``evidence["error"]`` with score 0, and a report that filtered on
    ``score > 0`` swallowed exactly the reason the user is reading it for: the
    repo was called unrecognisable while the crash that made it so went
    unmentioned in the one place that exists to say why.
    """
    repo = Path(repo)
    api = _adapters_api()
    if api is None:                                       # pragma: no cover - adapters absent
        return ["  the adapters package is not importable; no convention could be measured"]

    out: list[str] = []
    measured = api.detect_all(repo)
    scored = {k: v for k, v in measured.items() if v[0] > 0}
    if scored:
        out.append("  scores:")
        for kind, (score, evidence) in sorted(scored.items(), key=lambda kv: (-kv[1][0], kv[0])):
            out.append(f"    {kind:<20s} {score:.2f}  {evidence.get('reason', '')}".rstrip())
    failed = {k: v[1]["error"] for k, v in sorted(measured.items()) if v[1].get("error")}
    if failed:
        out.append("  failed:")
        for kind, error in failed.items():
            out.append(f"    {kind:<20s} could not be measured: {error}")
    out.append("  looked for:")
    for kind, lines in sorted(api.explain_all(repo).items()):
        for line in lines or [f"{kind}: nothing this convention keys on"]:
            out.append(f"    {line}")
    from .vocab import ADAPTER_KINDS

    declared = sorted(set(ADAPTER_KINDS) - set(api.registry()))
    if declared:
        out.append(f"  declared in the vocabulary but not implemented in v1: {', '.join(declared)} "
                   "(references/onboarding-contract.md)")
    return out


def detect_adapter(repo: str | Path) -> tuple[str, float, dict]:
    """Which convention this repo follows. Refuses a tie or a weak best guess."""
    repo = Path(repo)
    scores = detect_scores(repo)
    kinds = sorted(_adapter_classes()) or sorted(scores)
    flags = " | ".join(f"--adapter {k}" for k in kinds) or "--adapter <kind>"
    if not scores:
        raise DiscoveryError(
            "\n".join([f"cannot tell what kind of companion {repo} is"] + detect_report(repo)),
            hint=f"pass one of `{flags}` to `cf onboard <repo>`; "
                 "see references/onboarding-contract.md for what each kind needs")
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[0]))
    (kind, (score, evidence)) = ranked[0]
    if len(ranked) > 1 and abs(ranked[1][1][0] - score) < 1e-9:
        raise DiscoveryError(
            "\n".join([f"{repo}: {kind} and {ranked[1][0]} both score {score:.2f}"]
                      + detect_report(repo)),
            hint=f"pass `--adapter {kind}` or `--adapter {ranked[1][0]}` to say which "
                 "convention to read")
    if score < MIN_ADAPTER_CONFIDENCE:
        raise DiscoveryError(
            "\n".join([f"{repo}: best adapter guess {kind} scores only {score:.2f}, "
                       f"below the {MIN_ADAPTER_CONFIDENCE} a guess has to clear"]
                      + detect_report(repo)),
            hint=f"pass `--adapter {kind}` to read it as that convention anyway, "
                 f"or one of `{flags}`")
    evidence = dict(evidence)
    evidence["scores"] = {k: v[0] for k, v in scores.items()}
    return kind, score, evidence


#: never descended into: a Lake project's dependency tree is ~10,000 Mathlib
#: files, and walking it turns "read the repo" into a minute of IO.
PRUNED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv",
                         ".mypy_cache", ".pytest_cache", "build", "packages"})


def _walk(repo: Path, suffix: str, *, limit: int = 4000) -> list[Path]:
    """Files under ``repo`` ending in ``suffix``, pruning build and vendor trees.

    ``.lake/official-pdf`` and ``.lake/*.pdf`` ARE visited (measure_theory caches
    the official PDF there); ``.lake/packages`` and ``.lake/build`` are not.
    """
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames
                             if d not in PRUNED_DIRS
                             and not (here.name == ".lake" and d in {"packages", "build"}))
        for name in sorted(filenames):
            if name.endswith(suffix):
                out.append(here / name)
                if len(out) >= limit:
                    return out
    return out


def lean_files(repo: Path, limit: int = 400) -> list[Path]:
    """The repo's own .lean files, sorted (the score must not depend on readdir order).

    Public because an adapter's :meth:`detect` needs it and the walk (which
    prunes ``.lake/packages`` and the other vendor trees) belongs here, with the
    rest of "read the repo", rather than being copied into every convention.
    """
    out = [p for p in _walk(repo, ".lean", limit=limit * 4) if ".lake" not in p.parts]
    return sorted(out)[:limit]


def tex_files(repo: Path, subdir: str = "blueprint", limit: int = 400) -> list[Path]:
    """The repo's ``<subdir>/**/*.tex``, sorted; empty when there is no such tree."""
    root = Path(repo) / subdir
    if not root.is_dir():
        return []
    return sorted(p for p in _walk(root, ".tex", limit=limit))[:limit]


# --------------------------------------------------------------------------
# S4.3 repo signals
# --------------------------------------------------------------------------

def discover_sources(repo: str | Path, *, adapter: str | None = None) -> list[SourceClaim]:
    """Every source claim the repo makes. ALL signals run for every adapter kind.

    A repo may carry a signal that "belongs" to another kind -- tao-measure-lean
    has both a mathlib-initiative ``formalization.yaml`` and an Autoform
    ``graph.json`` -- so the adapter kind only ranks the claims, it never selects
    which files are read.
    """
    repo = Path(repo)
    claims: list[SourceClaim] = []
    claims += _s1_formalization_yaml(repo)
    claims += _s2_ledger_source(repo)
    claims += _s3_official_source(repo)
    claims += _s4_graph_json(repo)
    claims += _s5_prose(repo)
    claims += _s6_filesystem(repo)
    claims += _s7_citations(repo)
    if adapter:
        # The signals a convention owns are declared ONCE, on its adapter class
        # (``PRIMARY_SIGNALS``), which is also what ranks the claims in
        # ``Adapter.discover_sources``.  A second hand-written table here is how
        # the two drifted apart: this one gave attribution_table no bonus for the
        # `citation` signal its own class lists.
        cls = _adapter_classes().get(adapter)
        bonus = tuple(getattr(cls, "PRIMARY_SIGNALS", ()) or ()) if cls is not None else ()
        for claim in claims:
            if claim.signal in bonus:
                claim.confidence = min(1.0, claim.confidence + 0.05)
    return claims


def discover_sources_with_adapter(repo: str | Path, *,
                                  adapter: str | None = None) -> list[SourceClaim]:
    """Every claim the repo makes, INCLUDING the ones only the adapter can make.

    ``CLAUDE.md`` S6 promises every adapter a ``discover_sources(repo)`` and
    `cf onboard` never called it: it called the module-level
    :func:`discover_sources` instead, so a convention whose source is not a
    published book could not contribute the one claim that would unblock it.  A
    leanblueprint project is the whole of that case -- its manuscript IS its
    ``.tex`` tree, so the seven signals find no title, no ISBN, no PDF and no
    sha, and every blueprint repo stopped at "no source could be discovered".

    The adapter may only ADD a claim here.  Its own ``discover_sources`` returns
    these same seven signals (re-ranked, and enriched with the item kinds its
    tags use), and adopting that reading would make the prepared item table of
    every already-onboarded companion depend on the adapter's view of the
    companion rather than on the source's own headings -- which is the opposite
    of S4's "nothing about a source is typed in", and would move figures this
    repo pins.  So a claim cflib's signals already produced is kept exactly as
    cflib produced it, and only a genuinely new claim is appended.
    """
    repo = Path(repo)
    claims = discover_sources(repo, adapter=adapter)
    seen = {_claim_key(c) for c in claims}
    for claim in _adapter_claims(repo, adapter):
        key = _claim_key(claim)
        if key in seen:
            continue
        seen.add(key)
        claims.append(claim)
    return claims


def _claim_key(claim: SourceClaim) -> tuple:
    """What makes two claims the same claim, for the merge above."""
    return (claim.signal, claim.path, claim.sha256, claim.sha256_prefix,
            claim.title, tuple(claim.files), tuple(claim.pdf_hints))


def _adapter_claims(repo: Path, adapter: str | None) -> list[SourceClaim]:
    """``Adapter.discover_sources(repo)`` for one kind, or nothing.

    The adapter is instantiated with the minimum a discovery pass needs (its
    kind and the repo), because no ``adapter.yaml`` exists yet the first time
    `cf onboard` runs -- writing one is a later step of the same command.  A kind
    whose config check needs more than that, or whose reader raises, is reported
    and skipped: source discovery must not be lost because one convention could
    not measure a repo it does not claim.

    EVERY kind is asked, including the ones that add nothing.  The base
    implementation re-reads the repo's own tags (``observed_kinds``), which costs
    about a second on a companion of a hundred files -- measured on a once-per-
    companion command that already spends several seconds running ``pdftotext``.
    The alternative is to ask only the kinds that override the method, which means
    inspecting a class to decide whether to call its contract: get that wrong and
    a future convention is silently back behind the wall this function exists to
    remove, which is the more expensive mistake.
    """
    if not adapter:
        return []
    cls = _adapter_classes().get(adapter)
    if cls is None:
        return []
    try:
        instance = cls({"adapter": adapter, "name": repo.name, "repo": str(repo)})
        return list(instance.discover_sources(repo))
    except Exception as exc:                              # noqa: BLE001 - see the docstring
        detail(f"{adapter}: discover_sources could not run "
               f"({exc.__class__.__name__}: {exc}); using the repo signals only")
        return []


def _rel(repo: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:                                    # pragma: no cover
        return str(path)


def _s1_formalization_yaml(repo: Path) -> list[SourceClaim]:
    """S1 -- mathlib-initiative metadata: title, authors, ISBN and URLs."""
    path = repo / "formalization.yaml"
    if not path.exists():
        return []
    try:
        data = load_yaml(path.read_text(encoding="utf-8"), where=str(path))
    except CfError:                                       # pragma: no cover
        return []
    out: list[SourceClaim] = []
    for entry in ((data or {}).get("sources") or []):
        if not isinstance(entry, dict):
            continue
        free = str(entry.get("id") or "")
        isbn = _ISBN_RE.search(free)
        urls = tuple(_URL_RE.findall(free))
        book_page = next((u for u in urls if not u.lower().endswith(".pdf")), "")
        out.append(SourceClaim(
            signal="formalization_yaml", path="formalization.yaml#sources",
            title=str(entry.get("title") or ""),
            authors=tuple(str(a) for a in (entry.get("authors") or [])),
            isbn=(isbn.group(1) if isbn else ""),
            urls=urls, book_page_url=book_page,
            licence=str(entry.get("license") or entry.get("licence") or ""),
            confidence=0.8))
    return out


def _s2_ledger_source(repo: Path) -> list[SourceClaim]:
    """S2 -- a formalization ledger's ``source`` block: the sha is authoritative."""
    path = repo / "source-formalization-ledger.json"
    data = _read_json(path)
    src = (data or {}).get("source")
    if not isinstance(src, dict):
        return []
    hints = tuple(h for h in [str(src.get("path") or "")] if h)
    return [SourceClaim(
        signal="ledger_source", path="source-formalization-ledger.json#source",
        sha256=str(src.get("sha256") or ""), edition=str(src.get("revision") or ""),
        title=_title_from_revision(str(src.get("revision") or "")),
        pdf_hints=hints, confidence=0.95)]


#: Where an attribution-table companion keeps its official-source record, current first.
OFFICIAL_SOURCE_PATHS = ("attribution/official-source.json", "pdf-overlay/official-source.json")


def _s3_official_source(repo: Path) -> list[SourceClaim]:
    """S3 -- ``attribution/official-source.json`` (or ``pdf-overlay/``): sha, download URL, book page."""
    rel = next((r for r in OFFICIAL_SOURCE_PATHS if (repo / r).is_file()), OFFICIAL_SOURCE_PATHS[0])
    data = _read_json(repo / rel)
    if not isinstance(data, dict):
        return []
    urls = tuple(u for u in [data.get("canonical_pdf_url"), data.get("final_pdf_url")] if u)
    book_page = str(data.get("legacy_page_url") or data.get("canonical_page_url") or "")
    return [SourceClaim(
        signal="official_source_json", path=rel,
        sha256=str(data.get("sha256") or ""),
        bytes=(int(data["bytes"]) if data.get("bytes") is not None else None),
        urls=urls, book_page_url=book_page,
        pdf_hints=tuple(Path(u).name for u in urls),
        confidence=0.95)]


def _s4_graph_json(repo: Path) -> list[SourceClaim]:
    """S4 -- Autoform ``graph.json`` metadata: file, title and a page-offset note."""
    data = _read_json(repo / "graph.json")
    entries = ((data or {}).get("metadata") or {}).get("sources") or []
    out: list[SourceClaim] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        note = str(entry.get("page_offset_note") or "")
        out.append(SourceClaim(
            signal="graph_json", path="graph.json#metadata.sources",
            title=str(entry.get("title") or ""),
            pdf_hints=tuple(h for h in [str(entry.get("file") or "")] if h),
            offset_hint=_offset_from(note),
            confidence=0.7))
    return out


def _s5_prose(repo: Path) -> list[SourceClaim]:
    """S5 -- CLAUDE.md / AGENTS.md / README.md / docs/**/*.md.

    Prose is where a repo says the things no schema has a field for: that two
    editions exist, that both satisfy ``PDF page = book page + 16``, and which
    one the anchors follow.  A truncated sha here becomes ``sha256_prefix``; it
    is NEVER written as an identity.
    """
    files = [repo / name for name in PROSE_FILES]
    docs = repo / "docs"
    if docs.is_dir():
        files += sorted(docs.rglob("*.md"))[:40]
    out: list[SourceClaim] = []
    for path in files:
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:                                   # pragma: no cover
            continue
        rel = _rel(repo, path)
        shas = _SHA_FULL_RE.findall(text)
        prefixes = [p for p in _SHA_PREFIX_RE.findall(text) if p not in shas]
        offset_hint = _offset_from(text)
        anchor_hint = _anchor_hint(text)
        urls = tuple(_uniq(tuple(_URL_RE.findall(text))))[:12]
        pdf_hints = tuple(_uniq(tuple(_PDF_PATH_RE.findall(text))))[:12]
        for sha in shas:
            out.append(SourceClaim(signal="prose", path=f"{rel}#sha256", sha256=sha,
                                   urls=urls, pdf_hints=pdf_hints, offset_hint=offset_hint,
                                   anchor_hint=anchor_hint, confidence=0.5,
                                   edition=_edition_near(text, sha)))
        for prefix in prefixes:
            out.append(SourceClaim(signal="prose", path=f"{rel}#sha256_prefix",
                                   sha256_prefix=prefix, urls=urls, pdf_hints=pdf_hints,
                                   offset_hint=offset_hint, anchor_hint=anchor_hint,
                                   confidence=0.5, edition=_edition_near(text, prefix)))
        if not shas and not prefixes and (offset_hint is not None or pdf_hints):
            out.append(SourceClaim(signal="prose", path=rel, urls=urls, pdf_hints=pdf_hints,
                                   offset_hint=offset_hint, anchor_hint=anchor_hint,
                                   confidence=0.4))
    return out


def _s6_filesystem(repo: Path) -> list[SourceClaim]:
    """S6 -- PDFs actually sitting in the repo (or in its caches)."""
    seen: list[Path] = []
    for pattern in ("sources/*.pdf", ".lake/official-pdf/*.pdf", "pdf-overlay/vendor/**/*.pdf"):
        seen += sorted(repo.glob(pattern))
    for path in _walk(repo, ".pdf", limit=200):
        if path not in seen:
            seen.append(path)
        if len(seen) > 40:                                # pragma: no cover - a huge repo
            break
    out = []
    for path in seen:
        out.append(SourceClaim(signal="filesystem", path=_rel(repo, path),
                               pdf_hints=(_rel(repo, path),), confidence=0.6))
    return out


def _s7_citations(repo: Path) -> list[SourceClaim]:
    """S7 -- a link in the README / lakefile / formalization.yaml naming the book."""
    out: list[SourceClaim] = []
    readme = repo / "README.md"
    if readme.exists():
        head = "\n".join(readme.read_text(encoding="utf-8", errors="ignore").splitlines()[:120])
        urls = [u.rstrip(">;,.") for u in _URL_RE.findall(head)]
        page_urls = [u for u in urls if not u.lower().endswith(".pdf")]
        pdf_urls = [u for u in urls if u.lower().endswith(".pdf")]
        if page_urls or pdf_urls:
            out.append(SourceClaim(signal="citation", path="README.md#links",
                                   urls=tuple(_uniq(tuple(pdf_urls + page_urls)))[:8],
                                   book_page_url=(page_urls[0] if page_urls else ""),
                                   pdf_hints=tuple(Path(u).name for u in pdf_urls)[:4],
                                   confidence=0.4))
    return out


# --------------------------------------------------------------------------
# S4.3 claim merging
# --------------------------------------------------------------------------

def merge_claims(claims: Sequence[SourceClaim], registry: Registry) -> list[SourceClaim]:
    """Fold claims into one per candidate source.

    Claims join when they share a full sha, a resolvable sha prefix, a registry
    hit, or (last resort) a normalised title AND a compatible edition token.
    """
    identifying = [c for c in claims if _identifies(c)]
    corroborating = [c for c in claims if not _identifies(c)]
    resolved: list[tuple[SourceClaim, str | None]] = []
    for claim in identifying:
        resolved.append((claim, _registry_id(claim, registry)))
    groups: list[tuple[SourceClaim, str | None]] = []
    for claim, reg_id in resolved:
        for i, (existing, existing_id) in enumerate(groups):
            if reg_id and existing_id and reg_id == existing_id:
                groups[i] = (existing.merge(claim), existing_id)
                break
            if reg_id and existing_id and reg_id != existing_id:
                continue
            if existing.matches(claim):
                groups[i] = (existing.merge(claim), existing_id or reg_id)
                break
        else:
            groups.append((claim, reg_id))
    # A claim that names no edition -- a book-page link, an offset sentence -- is
    # CORROBORATION for every candidate, not a source of its own: the errata page
    # and the printed-page rule are properties of the WORK, and both Tao editions
    # share them. Folding them in is what keeps S5/S7 from inventing a source.
    for extra in corroborating:
        for i, (existing, existing_id) in enumerate(groups):
            groups[i] = (existing.merge(extra), existing_id)
    return [claim for claim, _ in sorted(groups, key=lambda pair: -pair[0].confidence)]


def _identifies(claim: SourceClaim) -> bool:
    """Does this claim name a particular edition (rather than just the work)?"""
    return bool(claim.sha256 or claim.sha256_prefix or claim.title or claim.pdf_hints)


def _registry_id(claim: SourceClaim, registry: Registry) -> str | None:
    if claim.sha256:
        hit = registry.by_sha(claim.sha256)
        if hit:
            return hit.id
    if claim.sha256_prefix:
        hits = registry.by_sha_prefix(claim.sha256_prefix)
        if len(hits) == 1:
            return hits[0].id
    for url in claim.urls:
        hit = registry.by_url(url)
        if hit:
            return hit.id
    if claim.isbn:
        isbn_hits = [e for e in registry.entries.values()
                     if e.isbn and _norm_isbn(e.isbn) == _norm_isbn(claim.isbn)]
        if len(isbn_hits) == 1:
            return isbn_hits[0].id
    for hint in claim.pdf_hints:
        hits = registry.by_filename(Path(hint).name)
        if len(hits) == 1:
            return hits[0].id
    if claim.title:
        hits = registry.by_title(claim.title, edition=claim.edition, isbn=claim.isbn)
        if len(hits) == 1:
            return hits[0].id
    return None


# --------------------------------------------------------------------------
# S4.4 locating the PDF / S4.5 fingerprinting
# --------------------------------------------------------------------------

def _workspace_root(workspace: Any) -> Path:
    """The workspace directory, from a Workspace OR a bare path.

    NOT ``getattr(workspace, "root", workspace)``: a ``pathlib.Path`` HAS a
    ``.root`` and it is ``"/"``, so that spelling searches (and downloads into)
    the filesystem root.
    """
    return Path(workspace if isinstance(workspace, (str, Path)) else workspace.root)


def search_order(claim: SourceClaim, *, repo: Path, workspace: Any | None,
                 library_paths: Sequence[str] = (),
                 rejected: list[str] | None = None) -> list[tuple[Path, str, int]]:
    """Candidate PDFs, strictly ordered: repo-declared, repo cache, workspace, library.

    The order is also a BOUNDARY.  ``claim.pdf_hints`` are strings read out of a
    companion repo's ledger, graph.json, prose and README, and an absolute path,
    a ``~`` path or a ``../../..`` path in one of them used to be followed
    wherever it went -- so a repo could name any PDF on the machine and have its
    full text extracted into the workspace.  A hint is now kept only when it
    lands inside the repo itself, the workspace's own ``sources/`` store, or one
    of the ``library_paths`` the USER configured; anything else is reported
    through *rejected* and never opened.
    """
    out: list[tuple[Path, str, int]] = []
    seen: set[Path] = set()
    repo_root = _resolve(Path(repo))
    store = _resolve(_workspace_root(workspace) / "sources") if workspace is not None else None
    #: hints outside the repo and the store: allowed only if a library path holds
    #: them, which is decided once the library is expanded (step 4).
    deferred: list[tuple[Path, str, int, str]] = []

    def add(path: Path, where: str, rank: int, hint: str = "") -> None:
        resolved = _resolve(path)
        if resolved is None or resolved in seen or not resolved.is_file():
            return
        if _within(resolved, repo_root) or (store is not None and _within(resolved, store)):
            seen.add(resolved)
            out.append((resolved, where, rank))
            return
        deferred.append((resolved, where, rank, hint or str(path)))

    # 1. paths the repo names
    for hint in claim.pdf_hints:
        raw = str(hint)
        if not raw.lower().endswith(".pdf"):
            continue
        add(repo / raw, "repo_declared", 0, raw)
        if raw.startswith("~") or os.path.isabs(raw):
            add(Path(raw), "repo_declared", 0, raw)
        # the same BASENAME somewhere else in the repo is the repo's cache, not a
        # path the repo names: measure_theory's ledger says
        # `sources/gsm-126-tao5-measure-book.pdf`, which does not exist, and the
        # bytes actually sit in `.lake/official-pdf/`. Saying `repo_declared`
        # there would hide that the declared path is stale.
        base = Path(raw).name
        for found in _walk(repo, base, limit=5):
            add(found, "repo_cache", 1, raw)

    # 2. the repo's own caches
    for pattern in (".lake/official-pdf/*.pdf", "sources/*.pdf", "pdf-overlay/vendor/**/*.pdf"):
        for found in sorted(repo.glob(pattern)):
            add(found, "repo_cache", 1)

    # 3. the workspace's store
    if workspace is not None:
        store_dir = _workspace_root(workspace) / "sources"
        for pattern in ("*/*.pdf", "*.pdf"):
            for found in sorted(store_dir.glob(pattern)) if store_dir.is_dir() else []:
                add(found, "workspace_store", 2)

    # 4. library_paths from workspace.yaml -- and the only roots outside the repo
    #    and the store that a repo-declared path may resolve into.
    library: list[Path] = []
    for pattern in library_paths or ():
        for found in _expand_library(pattern):
            resolved = _resolve(found)
            if resolved is not None:
                library.append(resolved)
    in_library = set(library)
    said: set[Path] = set()
    for resolved, where, rank, hint in deferred:
        if resolved in in_library:
            if resolved not in seen:
                seen.add(resolved)
                out.append((resolved, where, rank))
            continue
        if rejected is not None and resolved not in said:
            said.add(resolved)
            named = hint if str(resolved) == str(hint) else f"{hint} -> {resolved}"
            rejected.append(
                f"the repo names {named}, outside the repo, the workspace's sources/ store "
                f"and every library_path -- not opened "
                f"(`cf sources add {resolved} --for <companion>` to use it anyway)")
    for found in library:
        # already resolved, already inside a root the user configured: appended
        # directly rather than through add(), whose job is the boundary check.
        if found not in seen and found.is_file():
            seen.add(found)
            out.append((found, "library_path", 3))
    return out


def _resolve(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError):                       # pragma: no cover
        return None


def _within(path: Path, root: Path | None) -> bool:
    """Is *path* inside *root*? (both already resolved)"""
    if root is None:
        return False
    return path == root or root in path.parents


#: expansions are cached per process: onboarding two sources must not walk a
#: library root twice, and `cf onboard` already walks it once too often.
_LIBRARY_CACHE: dict[str, list[Path]] = {}

#: how far below a library pattern's FIXED prefix the search may descend, and how
#: many files it may look at. A library path is a convenience; it must never turn
#: `cf onboard` into a scan of the user's whole home directory.
LIBRARY_MAX_DEPTH = 4
LIBRARY_MAX_FILES = 5000


def _fnmatch_segment(name: str, pattern: str) -> bool:
    from fnmatch import fnmatchcase
    return fnmatchcase(name, pattern)


def _match_parts(parts: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    """Glob-match a path's segments, where ``**`` spans any number of segments."""
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        if not rest:
            return True
        for i in range(len(parts) + 1):
            if _match_parts(parts[i:], rest):
                return True
        return False
    if not parts:
        return False
    return _fnmatch_segment(parts[0], head) and _match_parts(parts[1:], rest)


def _expand_library(pattern: str, max_files: int = LIBRARY_MAX_FILES,
                    max_depth: int = LIBRARY_MAX_DEPTH) -> list[Path]:
    """Expand one ``library_paths`` entry to PDFs, depth-capped and count-capped.

    ``~/books/**/sources`` walks ``~/books`` to at most four levels, pruning
    build and vendor trees, and collects the PDFs in every directory that
    matches.
    """
    raw = os.path.expanduser(str(pattern))
    cached = _LIBRARY_CACHE.get(raw)
    if cached is not None:
        return cached
    parts = Path(raw).parts
    fixed: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        fixed.append(part)
    tail = tuple(parts[len(fixed):])
    root = Path(*fixed) if fixed else Path("/")
    out: list[Path] = []
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            rel = here.relative_to(root)
            depth = len(rel.parts)
            dirnames[:] = [] if depth >= max_depth else sorted(
                d for d in dirnames if d not in PRUNED_DIRS)
            if tail and not _match_parts(rel.parts, tail):
                continue
            for name in sorted(filenames):
                if name.lower().endswith(".pdf"):
                    out.append(here / name)
                    if len(out) >= max_files:
                        _LIBRARY_CACHE[raw] = out
                        return out
    _LIBRARY_CACHE[raw] = out
    return out


def fingerprint(pdf: str | Path) -> dict:
    """Identity (sha256) plus the poppler tripwire (text_sha256).

    ``sha256`` is the identity of the bytes.  ``text_sha256`` is NOT an identity:
    it catches the day poppler changes under us, because every quote-gate offset
    is derived from the extracted text and a silent change there would move every
    page without moving a single sha.
    """
    path = Path(pdf)
    data = {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size,
            "pages": None, "text_sha256": None, "header": ""}
    with open(path, "rb") as fh:
        data["header"] = fh.read(8).decode("latin-1", "replace")
    text = _pdftotext(path)
    if text is not None:
        pages = text.split("\f")
        if pages and pages[-1] == "":
            pages.pop()
        data["pages"] = len(pages)
        data["text_sha256"] = sha256_text(text)
        data["text"] = text
    return data


def _pdftotext(pdf: Path) -> str | None:
    """``pdftotext -layout`` to stdout; None when poppler is not installed."""
    import shutil
    exe = shutil.which("pdftotext")
    if exe is None:
        return None
    proc = subprocess.run([exe, "-layout", str(pdf), "-"], capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "ignore")


#: how far a candidate's NAME is from what we are looking for.
MATCH_ALIAS = 1        # a filename the registry records for this source
MATCH_TITLE = 2        # two or more title tokens in the filename
MATCH_NONE = 3         # no relation to the title at all

#: search_order ranks: 0 repo_declared, 1 repo_cache, 2 workspace_store, 3 library_path
RANK_CONVENIENCE = 2   # at this rank and beyond, the file was not named by anybody


def locate_pdf(claim: SourceClaim, *, repo: Path, workspace: Any | None,
               registry: Registry, library_paths: Sequence[str] = (),
               download: bool = False, entry: RegistryEntry | None = None,
               warnings: list[str] | None = None) -> LocatedPdf | None:
    """The first candidate whose bytes match what we know (or, absent a known sha, the first that reads).

    A candidate whose sha CONTRADICTS a known sha is never adopted: it is
    reported as ``edition_mismatch``, because adopting it is precisely the
    mistake the audit exists to catch.

    With NO sha to check against -- the new-source case -- the NAME has to carry
    the identification, and it is a rule, not a sort key: a file nobody named,
    found by walking a ``library_paths`` convenience directory, is adopted only
    when its filename is a registry alias or shares two title tokens.  Without
    that floor the first readable PDF in ``~/Downloads`` became the audited
    source (observed: a paper on the sunflower lemma adopted as "An Introduction
    to Measure Theory"), and every quote gate afterwards checked the companion
    against the wrong document.

    The search also STOPS at the first candidate it accepts.  The list is
    already ordered, so everything after it is worse -- and fingerprinting means
    sha256 plus a full ``pdftotext`` run, which turned onboarding a repo whose
    PDF sat at rank 0 into a scan of the whole of ``~/Downloads``.
    """
    warnings = warnings if warnings is not None else []
    known_sha = claim.sha256 or (entry.sha256 if entry else "") or ""
    known_prefix = claim.sha256_prefix or ""
    alias_names = {str(f).lower() for f in ((entry.aliases.get("filenames") if entry else []) or [])}
    title_tokens = _tokens(f"{claim.title} {claim.edition} {entry.title if entry else ''}")

    candidates = search_order(claim, repo=repo, workspace=workspace,
                              library_paths=library_paths, rejected=warnings)
    ranked: list[tuple[tuple, Path, str, int]] = []
    for path, where, rank in candidates:
        name = path.name.lower()
        if name in alias_names:
            match_rank = MATCH_ALIAS
        elif len(set(_tokens(name)) & title_tokens) >= 2:
            match_rank = MATCH_TITLE
        else:
            match_rank = MATCH_NONE
        ranked.append(((rank, match_rank, str(path)), path, where, rank))
    ranked.sort(key=lambda row: row[0])

    blind = not known_sha and not known_prefix
    unrelated: list[Path] = []
    fallback: LocatedPdf | None = None
    for key, path, where, rank in ranked:
        match_rank = key[1]
        if blind and match_rank == MATCH_NONE and rank >= RANK_CONVENIENCE:
            # nothing pins these bytes and nothing connects this name to the
            # title: a library path is a convenience, not a claim.
            unrelated.append(path)
            continue
        try:
            fp = fingerprint(path)
        except OSError:                                   # pragma: no cover
            continue
        sha = fp["sha256"]
        if known_sha:
            if sha == known_sha:
                return _located(fp, where, rank)
            if len(warnings) < MAX_MISMATCH_WARNINGS:
                warnings.append(f"edition_mismatch: {path} is {sha[:8]}, expected {known_sha[:8]}")
            continue
        if known_prefix:
            if sha.startswith(known_prefix):
                return _located(fp, where, rank)
            continue
        fallback = _located(fp, where, rank)
        break
    if fallback is not None:
        return fallback
    if unrelated:
        warnings.append(
            f"no PDF matching {claim.title or claim.path!r}: "
            f"{len(unrelated)} file(s) under the library paths match neither a registry "
            f"filename nor the title (first: {unrelated[0].name})")

    if download and entry is not None:
        fetched = _download(entry, workspace=workspace, registry=registry, warnings=warnings)
        if fetched is not None:
            return fetched
    return None


def _located(fp: Mapping, where: str, rank: int) -> LocatedPdf:
    return LocatedPdf(path=Path(fp["path"]), sha256=fp["sha256"], bytes=fp["bytes"],
                      pages=fp.get("pages"), text_sha256=fp.get("text_sha256"),
                      where=where, rank=rank, header=fp.get("header", ""))


def _download(entry: RegistryEntry, *, workspace: Any, registry: Registry,
              warnings: list[str]) -> LocatedPdf | None:
    """Fetch the author's own copy -- only when the licence note permits it.

    The fetch is recorded (url, status, bytes, sha) and the file lands in the
    workspace's gitignored sources/ store.  Nothing is ever redistributed.
    """
    if workspace is None:
        return None
    if not entry.may_fetch():
        warnings.append(f"download refused: {entry.id} licence.fetch is "
                        f"{entry.licence.get('fetch', 'none')!r} / no download_url")
        return None
    policy = getattr(workspace, "policy", {}) or {}
    if policy.get("allow_download") is False:
        warnings.append(f"download refused: workspace policy.allow_download is false ({entry.id})")
        return None
    dest_dir = _workspace_root(workspace) / "sources" / entry.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(entry.download_url).name
    cmd = ["curl", "-L", "--fail", "--silent", "--show-error",
           "-w", "%{http_code}", "-o", str(dest), entry.download_url]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not dest.exists():
        warnings.append(f"download failed ({entry.download_url}): {proc.stderr.strip()[:200]}")
        return None
    fp = fingerprint(dest)
    if entry.sha256 and fp["sha256"] != entry.sha256:
        warnings.append(f"download sha {fp['sha256'][:8]} != registry {entry.sha256[:8]}: "
                        f"kept at {dest}, NOT adopted")
        return None
    located = _located(fp, "download", 4)
    located.header = f"http {proc.stdout.strip()}"
    return located


# --------------------------------------------------------------------------
# S4.6 the anchor edition
# --------------------------------------------------------------------------

def choose_anchor_source(repo: Path, rows: Sequence[Mapping], candidates: Sequence[str], *,
                         workspace: Any, hints: Sequence[str] = ()) -> tuple[str, float, dict]:
    """Which edition's pagination the repo's page anchors follow.

    1. one candidate -> it is the anchor;
    2. otherwise the MECHANICAL check decides: score the companion's extracted
       page_anchor rows against each candidate's items.json and require
       ``agreement >= 0.80`` with a margin of ``0.05``;
    3. a prose sentence ("anchors follow the PUBLISHED pagination") only
       CORROBORATES (+0.05); when it disagrees with the mechanical winner the
       whole decision is refused, quoting both.
    """
    candidates = [c for c in candidates if c]
    if not candidates:
        raise DiscoveryError(f"{repo}: no candidate source to anchor to")
    if len(candidates) == 1:
        conf = 1.0
        evidence = {"reason": "single_candidate", "agreement": {}}
        if hints:
            evidence["hint"] = hints[0]
        return candidates[0], conf, evidence

    table = _anchor_agreement(candidates, rows, workspace=workspace)
    scored = sorted(table.items(), key=lambda kv: -kv[1]["agreement"])
    hint_pick, hint_text = _anchor_hint_pick(hints, candidates)

    if not scored or scored[0][1]["ok"] + scored[0][1]["mismatched"] == 0:
        if hint_pick:
            return hint_pick, 0.6, {"reason": "prose_hint_only", "hint": hint_text,
                                    "agreement": table}
        raise DiscoveryError(
            f"cannot decide which edition {repo.name} anchors to: no page anchors to score",
            hint="re-run with `cf onboard <repo> --anchor-source <id>`")
    winner, best = scored[0]
    runner_up = scored[1][1]["agreement"] if len(scored) > 1 else 0.0
    evidence = {"reason": "anchor_agreement", "agreement": table}
    if best["agreement"] < MIN_ANCHOR_AGREEMENT or best["agreement"] - runner_up < MIN_ANCHOR_MARGIN:
        lines = "\n".join(
            f"  {sid:<30s} agreement {row['agreement']:.2f} "
            f"({row['ok']} ok / {row['mismatched']} mismatched)" for sid, row in scored)
        raise DiscoveryError(
            f"cannot decide which edition {repo.name} anchors to\n{lines}",
            hint="re-run with `cf onboard <repo> --anchor-source <id>`, or check whether the "
                 "companion's anchors are internally consistent at all (`cf pair`)")
    confidence = best["agreement"]
    if hint_pick and hint_pick != winner:
        raise DiscoveryError(
            f"{repo.name}: the prose says {hint_pick} but the anchors agree with {winner}\n"
            f"  prose: \"{hint_text[:160]}\"\n"
            + "\n".join(f"  {sid:<30s} agreement {row['agreement']:.2f}"
                        for sid, row in scored),
            hint="settle it with `cf onboard <repo> --anchor-source <id>` after checking which "
                 "sentence is stale")
    if hint_pick == winner:
        confidence = min(1.0, confidence + 0.05)
        evidence["hint"] = hint_text
    return winner, round(confidence, 4), evidence


def _anchor_agreement(candidates: Sequence[str], rows: Sequence[Mapping],
                      *, workspace: Any) -> dict[str, dict]:
    """How well the companion's own page anchors agree with each candidate edition.

    The scoring lives in :func:`pair.anchor_check.check_edition` and is imported,
    not reimplemented: this module and `cf pair` must not be able to disagree
    about which edition a repo is anchored to.  There is no fallback -- an
    inline second copy behind ``except Exception`` would silently answer for a
    broken ``check_edition`` and decide the anchor edition by a different rule
    than the one `cf pair` then checks against.
    """
    from pair.anchor_check import check_edition

    items_by_source: dict[str, dict] = {}
    paths = getattr(workspace, "paths", None)
    for sid in candidates:
        if paths is None:
            continue
        path = paths.source_items(sid)
        if path.exists():
            items_by_source[sid] = json.loads(path.read_text(encoding="utf-8"))
    if not items_by_source:
        return {sid: {"ok": 0, "mismatched": 0, "agreement": 0.0} for sid in candidates}
    return check_edition(items_by_source, rows)


def _anchor_hint_pick(hints: Sequence[str], candidates: Sequence[str]) -> tuple[str, str]:
    """Which candidate a prose sentence points at, if exactly one."""
    for hint in hints or ():
        words = {w.lower() for w in _EDITION_WORD_RE.findall(hint)}
        if not words:
            continue
        matched = [sid for sid in candidates if words & set(_tokens(sid))]
        if len(matched) == 1:
            return matched[0], hint
    return "", ""


# --------------------------------------------------------------------------
# S4.1 the pipeline
# --------------------------------------------------------------------------

def resolve_claims(claims: Sequence[SourceClaim], *, repo: Path, workspace: Any,
                   registry: Registry, download: bool = False,
                   library_paths: Sequence[str] | None = None) -> DiscoveryResult:
    """Registry lookup -> locate the PDF -> fingerprint -> a workspace source body."""
    repo = Path(repo)
    if library_paths is None:
        library_paths = getattr(workspace, "library_paths", []) or []
    merged = merge_claims(claims, registry)
    #: A location an OPERATOR named is the last word to be asked, not the first.
    #: `cf onboard --source` exists for the case where nothing else answered, so
    #: when its bytes turn out to be a source the repo itself catalogues, the fold
    #: below has to run the repo's way round: the repo's name for the work
    #: survives (S3's derivation step prefers a declared title to a filename stem)
    #: and the flag only adds its path to the evidence.  `sort` is stable, so
    #: nothing else moves.
    merged.sort(key=lambda c: c.discovered_by == DISCOVERED_BY_MANUAL)
    result = DiscoveryResult(repo=repo, name=repo.name, adapter="")
    taken: set[str] = set(getattr(workspace, "sources", {}) or {})
    #: the fingerprint is the identity the whole model is built on, so two claims
    #: that end up on the same bytes are ONE source however differently they were
    #: worded. Without this, `cf onboard` could write a workspace.yaml its own
    #: loader rejects ("the same bytes are one source") and wedge the directory.
    by_sha: dict[str, dict] = {}
    for claim in merged:
        if _is_noise(claim):
            continue
        entry = _entry_for(claim, registry)
        # A claim that NAMES its files has nothing to search for: the bytes are
        # already in hand (an adapter's own manuscript, or `cf onboard --source`),
        # and a locate_pdf pass over them would rank a file against the filename
        # it already is.
        located = None if claim.files else locate_pdf(
            claim, repo=repo, workspace=workspace, registry=registry,
            library_paths=library_paths, download=download, entry=entry,
            warnings=result.warnings)
        if located is not None and entry is None:
            entry = registry.by_sha(located.sha256)
        if entry is None and located is None and not claim.files \
                and claim.signal in ("prose", "citation"):
            # Prose is evidence, not authority (rule 1). A sha quoted in a README
            # that matches no registry entry and no local PDF is reported, never
            # adopted: in measure_theory's README that string is the sha of
            # lake-manifest.json, and adopting it would invent a source.
            named = claim.sha256 or (claim.sha256_prefix + "…") or claim.title or claim.path
            result.unresolved.append(
                f"{named} from {claim.path} matched no registry entry and no local PDF")
            continue
        if entry is None and located is None and not claim.title and not claim.sha256:
            result.unresolved.append(f"{claim.path} named no source we could resolve")
            continue
        # Keyed on whichever sha the claim RESOLVED to, not on `located` alone: a
        # claim that named its own files has no located PDF and its sha was read
        # off those bytes, so keying on `located` let a `--source` file whose
        # bytes the repo's own signals also found become a SECOND id for one sha
        # -- the state workspace.yaml's loader refuses, reached by a flag rather
        # than by a bug. A sha somebody merely QUOTED is deliberately not a key
        # here: those bytes are not in hand, so folding a located claim into such
        # a body would keep the fingerprint and drop the file.
        sha = located.sha256 if located is not None else (claim.sha256 if claim.files else "")
        if sha and sha in by_sha:
            _fold_into(by_sha[sha], claim, entry, located, result, taken)
            result.claims.append(claim)
            continue

        source_id = _source_id(claim, entry, located, taken)
        taken.add(source_id)
        body = _source_body(source_id, claim, entry, located, result)
        result.claims.append(claim)
        result.claims_by_source[source_id] = claim
        result.sources.append(body)
        if sha:
            by_sha[sha] = body
    result.sources.sort(key=lambda b: (b.get("role") != "audited", b["id"]))
    return result


def _fold_into(body: dict, claim: SourceClaim, entry: RegistryEntry | None,
               located: LocatedPdf | None, result: DiscoveryResult, taken: set[str]) -> None:
    """Merge a second claim on the SAME bytes into the source already accepted.

    The claims were worded differently enough that :func:`merge_claims` kept
    them apart, and only the fingerprint showed they were one source.  The
    evidence is unioned so ``discovered.from`` still names every file that
    pointed here; a registry entry found on the second pass upgrades a source
    that had been named from a filename, because a catalogued id is the stable
    one (S3: sha -> registry alias -> derivation).

    ``located`` is None for a claim that NAMED its files -- an adapter's own
    manuscript, or `cf onboard --source` -- whose sha was read off those bytes
    rather than found by a search.  Such a claim reaches here whenever the
    operator named a file the repo already declares, so nothing below may
    dereference it.
    """
    where = located.where if located is not None else (claim.discovered_by or "none")
    sha8 = (located.sha256 if located is not None else claim.sha256 or "")[:8]
    discovered = body.setdefault("discovered", {"from": [], "pdf_found_in": where,
                                                "signal": claim.signal})
    discovered["from"] = _uniq(tuple(discovered.get("from") or ()) + tuple(claim.sources))
    discovered["from"] = list(discovered["from"])
    for key, value in (("title", claim.title), ("edition", claim.edition),
                       ("isbn", claim.isbn), ("book_page_url", claim.book_page_url)):
        if value and not body.get(key):
            body[key] = value
    if claim.kinds and not body.get("kinds"):
        body["kinds"] = list(claim.kinds)
    if body.get("offset_hint") is None and claim.offset_hint is not None:
        body["offset_hint"] = claim.offset_hint
    if not body.get("anchor_hint") and claim.anchor_hint:
        body["anchor_hint"] = claim.anchor_hint
    stored = result.claims_by_source.get(body["id"])
    result.claims_by_source[body["id"]] = stored.merge(claim) if stored is not None else claim
    if entry is not None and body.get("registry") == "local" and entry.id != body["id"]:
        old_id = body["id"]
        taken.discard(old_id)
        taken.add(entry.id)
        body.update(entry.to_source_fields())
        body["id"] = entry.id
        body["registry"] = entry.origin
        result.claims_by_source[entry.id] = result.claims_by_source.pop(old_id)
        detail(f"{old_id} and {entry.id} are the same bytes ({sha8}); "
               f"keeping the catalogued id {entry.id}")
    else:
        detail(f"{body['id']}: a second claim ({claim.signal} {claim.path}) names the same "
               f"bytes {sha8}; folded in")


def _is_noise(claim: SourceClaim) -> bool:
    """Drop a claim that names nothing identifying at all."""
    return not any([claim.sha256, claim.sha256_prefix, claim.title, claim.pdf_hints,
                    claim.urls, claim.book_page_url, claim.files])


def _entry_for(claim: SourceClaim, registry: Registry) -> RegistryEntry | None:
    rid = _registry_id(claim, registry)
    return registry.get(rid) if rid else None


def _source_id(claim: SourceClaim, entry: RegistryEntry | None,
               located: LocatedPdf | None, taken: set[str]) -> str:
    """sha -> registry alias -> derivation, in that order (the stability rule)."""
    if entry is not None:
        return entry.id
    title = claim.title or (Path(claim.pdf_hints[0]).stem.replace("-", " ")
                            if claim.pdf_hints else "source")
    return source_id_for(title, claim.edition, claim.authors,
                         sha256=(located.sha256 if located else claim.sha256 or None),
                         taken=taken)


def _source_body(source_id: str, claim: SourceClaim, entry: RegistryEntry | None,
                 located: LocatedPdf | None, result: DiscoveryResult) -> dict:
    body: dict[str, Any] = {"id": source_id}
    if entry is not None:
        body.update(entry.to_source_fields())
    body.setdefault("title", claim.title)
    body.setdefault("authors", list(claim.authors))
    body.setdefault("edition", claim.edition)
    if not body.get("title"):
        body["title"] = claim.title or source_id
    if not body.get("edition"):
        body["edition"] = claim.edition or "unspecified"
    if claim.isbn and not body.get("isbn"):
        body["isbn"] = claim.isbn
    if claim.book_page_url and not body.get("book_page_url"):
        body["book_page_url"] = claim.book_page_url
    if claim.kinds and not body.get("kinds"):
        # An adapter saw the grammar in the companion's own tags. It is weaker
        # evidence than the source's headings (cflib.inference.infer_kinds runs
        # over the text in prepare and overwrites this), but it is the only
        # evidence there is when the PDF is not on this machine.
        body["kinds"] = list(claim.kinds)
    if claim.licence and not (body.get("licence") or {}).get("note"):
        body["licence"] = {"note": claim.licence, "fetch": "none", "redistribute": False}
    body.setdefault("licence", {"note": "", "fetch": "none", "redistribute": False})
    body.setdefault("confirmed", False)
    body.setdefault("registry", entry.origin if entry else "local")
    body.setdefault("role", "audited")
    if located is not None:
        if body.get("sha256") and body["sha256"] != located.sha256:   # pragma: no cover
            result.warnings.append(
                f"{source_id}: located {located.path} is {located.sha256[:8]} but the registry "
                f"pins {body['sha256'][:8]}; keeping the registry sha")
        else:
            body["sha256"] = located.sha256
            body["bytes"] = located.bytes
            if located.pages:
                body["pages"] = located.pages
            body["text_sha256"] = located.text_sha256
        body["pdf"] = str(located.path)
    elif claim.files:
        # The bytes are the source.  One file IS the source file; several are
        # concatenated in this order by `cf onboard`, which is also what the sha
        # covers -- so `manuscript_files` is the order, not merely the set.
        body["sha256"] = claim.sha256
        if claim.bytes is not None:
            body["bytes"] = claim.bytes
        if claim.source_format:
            body["source_format"] = claim.source_format
        if claim.pagination:
            body["pagination"] = claim.pagination
        if len(claim.files) == 1:
            body["pdf"] = str(claim.files[0])
        else:
            body["manuscript_files"] = list(claim.files)
    elif claim.sha256:
        body["sha256"] = claim.sha256
    body["offset_hint"] = claim.offset_hint
    body["anchor_hint"] = claim.anchor_hint
    body["discovered"] = {
        "from": list(claim.sources),
        "pdf_found_in": (located.where if located is not None
                         else (claim.discovered_by or "none") if claim.files else "none"),
        "signal": claim.signal,
    }
    if claim.locator:
        body["discovered"]["locator"] = claim.locator
    return body


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warn(f"{path}: {exc}")
        return None


def _uniq(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(str(value))
    return tuple(out)


def _basenames(hints: Iterable[str]) -> list[str]:
    return [Path(str(h)).name.lower() for h in hints if str(h).lower().endswith(".pdf")]


#: words a filename and a title can share without meaning anything
_NAME_NOISE = frozenset({"the", "and", "for", "pdf", "book", "final", "copy", "new", "old"})


def _name_echoes_title(hints: Iterable[str], title: str) -> bool:
    """Does one of these filenames share a meaningful word with *title*?

    The guard on the work/file join above.  ``elementary-notes.pdf`` echoes
    "Elementary Notes on Sets"; ``sunflower-rao.pdf`` does not, and a repo that
    declares one book while committing an unrelated paper must end up with two
    claims, not one.
    """
    wanted = _tokens(title) - _NAME_NOISE
    if not wanted:
        return False
    for hint in hints:
        name = Path(str(hint)).stem
        if (_tokens(name) - _NAME_NOISE) & wanted:
            return True
    return False


def _edition_key_tokens(edition: str) -> set[str]:
    """Years and edition words -- the tokens that DISTINGUISH two editions."""
    text = str(edition or "").lower()
    return ({w.lower() for w in _EDITION_WORD_RE.findall(text)}
            | set(re.findall(r"\b(?:19|20)\d{2}\b", text)))


def _edition_compatible(a: str, b: str) -> bool:
    """True unless the two edition labels name DIFFERENT editions."""
    ta, tb = _edition_key_tokens(a), _edition_key_tokens(b)
    if not ta or not tb:
        return True
    return bool(ta & tb)


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", str(text).lower()) if len(t) > 2}


def _offset_from(text: str) -> int | None:
    m = _OFFSET_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def _anchor_hint(text: str) -> str:
    """The sentence naming WHICH edition the repo's page anchors follow.

    Markdown wraps prose, and both target repos wrap this exact sentence across
    two source lines ("... anchors in docstrings follow the / PUBLISHED
    pagination"), so each blank-line-separated paragraph is flattened to a single
    line before the sentence is cut out.  Corroboration only: S4.6 lets this
    raise the mechanical confidence by 0.05 and refuse on a contradiction, never
    decide on its own.
    """
    for para in re.split(r"\n\s*\n", text or ""):
        flat = re.sub(r"\s+", " ", para.replace("*", "")).strip()
        for sentence in _ANCHOR_SENT_RE.findall(flat):
            if _EDITION_WORD_RE.search(sentence):
                return sentence.strip()[:400]
    return ""


def _edition_near(text: str, needle: str, window: int = 260) -> str:
    """The edition words in the sentence around a sha mentioned in prose."""
    idx = text.find(needle)
    if idx < 0:
        return ""
    chunk = text[max(0, idx - window): idx + window]
    words = _EDITION_WORD_RE.findall(chunk)
    years = re.findall(r"\b(19|20)\d{2}-\d{2}-\d{2}\b", chunk)
    full_years = re.findall(r"\b((?:19|20)\d{2})(?:-\d{2}-\d{2})?\b", chunk)
    parts = list(dict.fromkeys([w.lower() for w in words]))
    if full_years:
        parts.insert(0, full_years[0])
    del years
    return " ".join(parts[:3])


def _title_from_revision(revision: str) -> str:
    """``Terence Tao, An Introduction to Measure Theory, author's ...`` -> the title."""
    parts = [p.strip() for p in str(revision or "").split(",")]
    for part in parts[1:]:
        if len(part.split()) >= 2 and part[:1].isupper():
            return part
    return ""


def detail_signals(claims: Sequence[SourceClaim]) -> list[str]:
    """Every file a claim came from, for ``companions.<name>.discovered.signals``."""
    out: list[str] = []
    for claim in claims:
        for src in claim.sources:
            if src not in out:
                out.append(src)
    detail(f"discovery signals: {', '.join(out)}")
    return out
