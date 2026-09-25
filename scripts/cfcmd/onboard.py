"""cf onboard <repo> -- discover, register, prepare, extract. One command.

docs/design/archive/multi-source-model.md R2 / S4 / S7.2.  The user rejected typing
sources, editions, page offsets and errata URLs, so onboarding reads them off
the repository instead:

    discover  detect the adapter, read the repo's seven signals AND whatever the
              adapter itself claims, locate and FINGERPRINT the source bytes,
              infer the offset and the item kinds, find the errata page, decide
              which edition the anchors follow
    register  workspace.yaml + the registry overlay (unconfirmed sources are
              written as `confirmed: false` and surfaced by `cf status`)
    prepare   sources/<id>/{text.txt, pages/, items.json, meta.json, errata.*}
    extract   companions/<name>/statements.jsonl, every row carrying its source

Nothing is written to the companion repo, ever.  Every inference is printed with
its confidence and the file it came from, and a weak inference is a REFUSAL with
the one command that settles it -- never a default.

A source is a published edition, a manuscript the companion itself wrote (a
leanblueprint's .tex tree, claimed by its adapter and concatenated here), or a
file the operator names with ``--source`` because the repo names none.  All three
are fingerprinted, registered unconfirmed and prepared the same way: only the
LOCATION is ever given, never a fact about the source.

    cf onboard <repo>
    cf onboard <repo> --name adam
    cf onboard <repo> --source <the book's PDF or manuscript>
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from pathlib import Path

from cflib import discovery, gitsafe, inference, log, state as state_mod, vocab
from cflib.adapters_api import DRY_RUN_REQUIRED_KEYS
from cflib.errors import AdapterError, CfError, DiscoveryError, SourceError
from cflib.jsonio import write_jsonl
from cflib.log import format_table
from cflib.registry import OVERLAY_FILE, Registry, entry_from_claim
from cflib.templates import copy_template, render_template
from cflib.workspace import ROLE_AUDITED, ROLE_REFERENCE, derived_roots_for, from_args
from cflib.jsonio import atomic_write

#: Sensible starting points per adapter kind; edit adapter.yaml afterwards.
DEFAULTS = {
    "docstring_tags": {"glob": "**/*.lean", "anchor": "docstring", "granularity": "item"},
    "attribution_table": {"glob": "pdf-overlay/data/*.json", "anchor": "ledger", "granularity": "item"},
    # A blueprint has no printed pages to anchor to: its manuscript IS the .tex.
    "blueprint_tex": {"glob": "blueprint/**/*.tex", "anchor": "none", "granularity": "item"},
    "ledger_nodes": {"glob": "source-formalization-ledger.json", "anchor": "ledger", "granularity": "node"},
    "custom": {"glob": "", "anchor": "none", "granularity": "item"},
}


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "onboard", help="discover a companion repo's sources, prepare them and extract")
    p.add_argument("repo", help="path to the companion repo (never written to)")
    p.add_argument("--name", help="short companion name (default: the directory name)")
    p.add_argument("--adapter", choices=sorted(vocab.ADAPTER_KINDS),
                   help="override the detected adapter kind")
    p.add_argument("--anchor-source", metavar="ID",
                   help="override which edition this repo's page anchors follow")
    p.add_argument("--source", metavar="FILE", action="append", default=[],
                   help="name a source the repo does not: a PDF, or a manuscript that is "
                        "already text. Repeatable. Its LOCATION is all this gives -- the sha, "
                        "the page offset, the item kinds and the numbering are still read off "
                        "the file")
    p.add_argument("--download", action="store_true",
                   help="allow fetching a source PDF from the author's own site when the "
                        "registry licence note permits it (never redistributed)")
    p.add_argument("--dry-run", action="store_true",
                   help="print what discovery found and write nothing")
    p.add_argument("--owner", default="external",
                   help="who may change this repo (a session id, a person, or 'external')")
    p.add_argument("--write-policy", default="read_only", choices=list(vocab.WRITE_POLICIES),
                   help="v1 is findings-only; peers stay read_only")
    p.add_argument("--no-errata", action="store_true", help="skip the errata fetch")
    p.add_argument("--fetch-errata", action="store_true",
                   help="allow fetching an errata URL the COMPANION REPO supplies "
                        "(off by default: the page ends up in worker prompts)")
    p.add_argument("--no-extract", action="store_true", help="stop after preparing the sources")
    p.add_argument("--force", action="store_true", help="rewrite adapter.yaml and re-prepare")


def _check_adapter_implemented(kind: str, repo: Path | None = None) -> None:
    """Refuse an adapter kind the vocabulary names but no code implements.

    ``references/onboarding-contract.md`` says ``cf onboard --adapter
    ledger_nodes`` "refuses with a pointer to this paragraph".  It did not: the
    AdapterError was swallowed into a warning, the command returned 0 having
    written adapter.yaml, conventions.md, state.json AND a workspace.yaml entry,
    and the next ``cf extract`` then aborted for the WHOLE workspace on a
    companion nothing could parse.  This runs before anything is written.

    The hint names the implemented kind that scored best on THIS repo, with the
    flag that selects it: being told that ledger_nodes is unimplemented while
    docstring_tags would have read the repo, and not being told the second half,
    is the refusal a user has to read the source to get past.
    """
    known = set(vocab.IMPLEMENTED_ADAPTER_KINDS)
    if kind in known:
        return
    runner_up = ""
    if repo is not None:
        scores = discovery.detect_scores(repo)
        if scores:
            best = max(sorted(scores), key=lambda k: scores[k][0])
            runner_up = (f" This repo scores {scores[best][0]:.2f} for {best}: "
                         f"`cf onboard {repo} --adapter {best}` reads it as that convention.")
    raise CfError(
        f"adapter kind {kind!r} is declared in the vocabulary but not implemented in v1",
        hint=("implemented kinds: " + ", ".join(sorted(known)) + "." + runner_up +
              " See references/onboarding-contract.md for what ledger_nodes still needs "
              "(an item-level curation pass) and what `custom` would have to provide."),
    )


def run(args) -> int:
    workspace = from_args(args)
    paths = workspace.paths
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        raise CfError(f"{repo} is not a directory",
                      hint="cf onboard takes the PATH to a companion repo: "
                           "`cf onboard <path to the checkout>`")
    if repo == workspace.root or workspace.root in repo.parents:
        raise CfError("the companion repo must live outside the workspace directory")
    name = args.name or _default_name(repo)

    # ---- discover ----------------------------------------------------
    kind, adapter_confidence, adapter_evidence = _detect(repo, args.adapter)
    _check_adapter_implemented(kind, repo)
    registry = Registry.load(workspace)
    # The ADAPTER's claims too, not only the module-level signals: CLAUDE.md S6
    # promises every convention a `discover_sources`, and a convention whose
    # source is its own manuscript has no other way to say so.
    claims = discovery.discover_sources_with_adapter(repo, adapter=kind)
    claims += [_named_source_claim(raw) for raw in (getattr(args, "source", None) or [])]
    claims += _sources_only_the_operator_named(workspace, repo, name)
    _read_named_sources(claims)
    #: The repo being onboarded is not in `workspace.companions` yet -- `upsert_companion`
    #: runs further down -- so the search roots DERIVED from it would only exist from the
    #: second run on, and the identical command would then discover a different set of
    #: PDFs each time: run 1 left the anchor edition with no text (no quote gate, no
    #: anchor check), run 2 found it in the sibling repo and prepared it.  Deriving them
    #: here makes run 1 and run 2 search the same set.
    result = discovery.resolve_claims(
        claims, repo=repo, workspace=workspace, registry=registry, download=args.download,
        library_paths=list(dict.fromkeys(
            list(workspace.library_paths) + derived_roots_for(repo))))
    result.name = name
    result.adapter = kind
    result.adapter_confidence = adapter_confidence
    result.adapter_evidence = adapter_evidence
    if not result.sources:
        raise DiscoveryError(
            f"{repo}: no source could be discovered",
            # NOT `cf sources add --for <companion>`: that takes a companion this
            # workspace already has, and this refusal is exactly the case where
            # there is none -- so the hint used to name a command that answers
            # "unknown companion". `--source` names the file on the way in.
            hint="the repo names no book, ships no PDF and quotes no sha. Name the source "
                 f"yourself: `cf onboard {repo} --source <file>` (a PDF, or the manuscript "
                 "itself; repeatable). Only its location is given -- the sha, the offset, the "
                 "kinds and the numbering are still read off the file")

    _fold_offset_hints(result)
    log.info(result.render())
    if args.dry_run:
        log.info("")
        log.info("--dry-run: nothing written")
        return 0

    # ---- register ----------------------------------------------------
    # PREPARE BEFORE SAVING, the shape `cf sources add` uses. `_prepare_source`
    # is where the wrong-document refusals live (0 numbered items, a kind list
    # belonging to another book), and `cf onboard <repo> --source <the wrong
    # file>` reaches every one of them -- so writing workspace.yaml first left a
    # command that exits 2 with a source row, a registry overlay row and a
    # prepared directory to its name, in a directory that is a git repo, and
    # only `cf sources remove <id>` took them away again.
    registration = SourceRegistration(workspace)
    for body in result.sources:
        _assemble_manuscript(workspace, body, repo)
        _register_source(workspace, registry, body, result)

    # ---- prepare -----------------------------------------------------
    log.info("")
    try:
        for body in result.sources:
            _prepare_source(workspace, body["id"], force=args.force, no_errata=args.no_errata,
                            declared_offset=body.get("declared_offset"),
                            fetch_errata_ok=_errata_allowed(workspace, args),
                            onboarding=repo)
    except CfError:
        _retract_registration(registration, result, name)
        raise
    workspace.save()

    # ---- the anchor edition (needs prepared items) --------------------
    audited, bundled, anchor, anchor_conf, anchor_evidence = _decide_sources(
        workspace, repo, name, result, args)
    result.anchor_source = anchor
    result.anchor_confidence = anchor_conf
    result.anchor_evidence = anchor_evidence
    result.bundled_sources = bundled
    for body in result.sources:
        if body["id"] in bundled and body["id"] not in audited:
            workspace.upsert_source(body["id"], role=ROLE_REFERENCE)
    commit = _commit(repo)
    workspace.upsert_companion(
        name,
        repo=str(repo), adapter=kind, sources=list(audited), anchor_source=anchor,
        bundled_sources=list(bundled), owner=args.owner, write_policy=args.write_policy,
        commit=commit,
        discovered={
            "at": state_mod.now(),
            "adapter_confidence": round(float(adapter_confidence), 4),
            "anchor_confidence": round(float(anchor_conf), 4),
            "signals": discovery.detail_signals(result.claims),
        })
    workspace.save()

    # ---- adapter.yaml, conventions.md --------------------------------
    adapter_yaml = _write_adapter_yaml(workspace, name, repo, kind, commit, audited, anchor, args,
                                       evidence=adapter_evidence)
    conventions, written = copy_template("conventions.md.example", paths.conventions_md(name),
                                         {"name": name})
    log.ok(f"wrote {conventions}") if written else log.info(f"{conventions} exists; left untouched")

    # ---- extract -----------------------------------------------------
    counts, problem = _dry_run(adapter_yaml, repo, workspace, audited, anchor)
    rows: list = []
    if not args.no_extract:
        rows = _extract(workspace, name, adapter_yaml, repo, audited, anchor)

    # The anchor decision above had only a previous run's rows to score. Now that
    # this run's rows exist, let the MECHANICAL check have the last word (S4.6):
    # a prose sentence saying which edition the anchors follow can go stale, and
    # the pages cannot.
    anchor, anchor_conf, anchor_evidence = _confirm_anchor(
        workspace, repo, name, result, args, rows, anchor, anchor_conf, anchor_evidence)
    result.anchor_source, result.anchor_confidence = anchor, anchor_conf
    result.anchor_evidence = anchor_evidence
    workspace.upsert_companion(name, anchor_source=anchor, discovered={
        **(workspace.companion(name).discovered or {}),
        "anchor_confidence": round(float(anchor_conf), 4)})
    workspace.save()
    _print_anchor(result)

    with state_mod.update(workspace, name) as st:
        st["name"] = name
        st["repo"] = str(repo)
        st["commit"] = commit
        st["anchor_source"] = anchor
        adapter_state = {
            "kind": kind,
            "yaml": str(adapter_yaml.relative_to(workspace.root)),
            "granularity": DEFAULTS[kind]["granularity"],
            "write_policy": args.write_policy,
            "owner": args.owner,
            "confidence": round(float(adapter_confidence), 4),
        }
        if counts is not None:
            adapter_state["dry_run"] = counts
        st["adapter"] = {**(st.get("adapter") or {}), **adapter_state}
        for sid in audited:
            state_mod.source_section(st, sid, {"sha": workspace.source(sid).sha256})
        state_mod.note(st, "onboard", adapter=kind, commit=(commit or "")[:7],
                       sources=len(audited))
    log.ok(f"wrote {paths.state_json(name)}")

    _report(name, repo, commit, counts, problem, conventions, len(rows), workspace, audited)
    if counts is not None and not counts.get("tagged"):
        raise CfError(f"{name}: the adapter matched 0 tagged declarations",
                      hint="fix adapter.yaml (glob, tag pattern) and re-run `cf onboard --force`")
    return 0


# ---------------------------------------------------------------------------
# discovery helpers
# ---------------------------------------------------------------------------

def _default_name(repo: Path) -> str:
    """The directory name, normalised: ``measure_theory`` -> ``measure-theory``."""
    return repo.name.strip().lower().replace("_", "-").replace(" ", "-")


def _retract_registration(registration: "SourceRegistration", result, name: str) -> None:
    """Undo what this onboard registered, and say by name what was undone.

    `_prepare_source` prints ``ok   prepared  sources/<id>/ ...`` before it runs
    the wrong-document refusals, and that line is true right up until this
    deletes the directory it names: retract it by name rather than leaving the
    operator with an ``ok`` and an ``error`` that contradict each other.  A
    source the workspace already held is not this command's to remove, so it is
    named with the command that does remove it, as `cf sources add` does.
    """
    dropped = registration.retract(body["id"] for body in result.sources)
    for source_id, removed in sorted(dropped.items()):
        log.warn(f"{source_id} was not added; the workspace is unchanged"
                 + (f" (sources/{source_id}/ was removed again)" if removed else ""))
    # Only for a source the OPERATOR named: one the repo itself declared and this
    # workspace already held is not something they were offered a choice about,
    # and `cf sources remove` on it would drop it from the companions that are
    # audited against it.
    kept = sorted(body["id"] for body in result.sources
                  if body["id"] in registration.source_ids
                  and (body.get("discovered") or {}).get("pdf_found_in") == "manual")
    for source_id in kept:
        log.warn_block(
            f"{source_id} was already in this workspace and stays; {name} was not onboarded",
            [f"  drop it with:  cf sources remove {source_id}"])


def _park_unlisted_sources(workspace, result) -> None:
    """Demote every just-registered source no companion lists to `reference`.

    `role: audited` means "some companion is audited against this"; until the
    companion entry is written that is not true, and `Workspace.validate` says
    so.  A source parked here comes back as audited on the next successful
    onboard, and `cf sources remove <id>` drops it for good.

    Nothing calls this today.  It is the REPAIR for a workspace an older version
    parked such a source into, kept (with its test) for that reason.
    Onboarding no longer makes one: it saves workspace.yaml only after every
    source has been prepared, and a refusal before that goes through
    :func:`_retract_registration` instead, which leaves the file untouched.
    """
    for body in result.sources:
        source_id = body["id"]
        if source_id in workspace.sources and not workspace.companions_of(source_id):
            workspace.upsert_source(source_id, role=ROLE_REFERENCE)
    workspace.save()


def _errata_allowed(workspace, args) -> bool:
    """May a repo-supplied errata URL be fetched? ``--fetch-errata`` or the policy."""
    if getattr(args, "fetch_errata", False):
        return True
    return bool((workspace.policy or {}).get("allow_errata_fetch"))


def _detect(repo: Path, override: str | None) -> tuple[str, float, dict]:
    """The adapter kind, its confidence, and the evidence adapter.yaml is built from.

    ``--adapter`` settles WHICH convention, not HOW the repo spells it: the
    chosen kind is still measured, so the per-repo options detection found (the
    bare-versus-backticked tag spelling) reach the generated adapter.yaml on the
    manual path too.  Without that, `cf onboard <repo> --adapter docstring_tags`
    on a bare-label companion wrote an adapter.yaml demanding backticks and
    extracted one row in two thousand docstrings.
    """
    if override:
        evidence = {"reason": "--adapter"}
        scores = discovery.detect_scores(repo)
        if override in scores:
            evidence = {**scores[override][1], "reason": "--adapter",
                        "detected_score": scores[override][0]}
        return override, 1.0, evidence
    return discovery.detect_adapter(repo)


def _fold_offset_hints(result: discovery.DiscoveryResult) -> None:
    """Keep the repo's declared offset on the source body for prepare() to check.

    Kept whatever the registry already says.  Dropping it when the two disagreed
    threw away the one case the cross-check exists for: a repo declaring "PDF
    page = book page + 9" over a source the registry (and the running heads) put
    at 2 is a repo talking about a different edition, and it has to be said out
    loud rather than quietly resolved in the registry's favour.
    """
    for body in result.sources:
        hint = body.pop("offset_hint", None)
        body.pop("anchor_hint", None)
        if hint is not None:
            body["declared_offset"] = hint


def _accepted_suffixes() -> str:
    """What ``--source`` takes, read off ``locator.TEXT_SUFFIXES`` not retyped.

    `cf sources add`'s help does the same, and for the same reason: a message
    that advertised fewer formats than the code accepts is how a suffix with no
    heading pattern got in unnoticed.
    """
    from sources.locator import text_suffixes_help
    return "a PDF, or the manuscript itself (%s)" % text_suffixes_help()


def _named_source_claim(raw: str) -> discovery.SourceClaim:
    """The claim behind one ``cf onboard --source <file>``.

    The operator names the LOCATION and nothing else.  Every fact about the
    source -- its sha, its page offset, its item kinds, its numbering depth, its
    completeness -- is still read off the file by `prepare`, exactly as
    `cf sources add` reads them, so this is not the "typed-in source" S4 forbids:
    it is the same override, moved to the one command that needs it before a
    companion exists.
    """
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise SourceError(f"--source {raw}: not a file",
                          hint="give the path to " + _accepted_suffixes())
    return discovery.SourceClaim(
        signal="filesystem", path=str(path), files=(str(path),),
        title=path.stem.replace("-", " ").replace("_", " "),
        discovered_by=discovery.DISCOVERED_BY_MANUAL, confidence=1.0, sources=(str(path),))


def _sources_only_the_operator_named(workspace, repo: Path, name: str) -> list:
    """Re-claim the sources of THIS companion that no signal can discover.

    Discovery re-reads the repo on every run, and a source the repo does not name
    is invisible to all seven signals -- so a second `cf onboard <repo>` with no
    flags refused with "no source could be discovered" for a companion whose
    source workspace.yaml listed two lines away.  Re-onboarding is the documented
    way to refresh a companion; it must not need a flag the operator has to
    remember from the first run.

    Only ``discovered.pdf_found_in: manual`` rows are re-claimed, and only when
    the companion entry of this name already names THIS repo.  Every other row is
    something a signal will find again on its own, and re-asserting one from the
    workspace would let a stale row outrank what the repo says today -- which is
    the whole reason discovery re-reads the repo rather than trusting itself.

    The FACTS are still read off the file, exactly as on the first run: this
    contributes the location the operator gave, nothing more, so a source whose
    bytes changed in the meantime is re-fingerprinted rather than re-asserted.
    """
    entry = workspace.companions.get(name)
    if entry is None or Path(entry.repo).expanduser().resolve() != repo:
        return []
    rows = workspace.data.get("sources") or {}
    claims = []
    for source_id in entry.sources:
        body = rows.get(source_id) or {}
        if ((body.get("discovered") or {}).get("pdf_found_in")
                != discovery.DISCOVERED_BY_MANUAL):
            continue
        recorded = str(body.get("pdf") or "")
        if not recorded or not Path(recorded).is_file():
            log.warn(f"{source_id}: {recorded or '(no file recorded)'} is not on this machine "
                     f"any more, so this run cannot re-read it; name it again with "
                     f"`cf onboard {repo} --source <file>`")
            continue
        claims.append(discovery.SourceClaim(
            signal="filesystem", path=recorded, files=(recorded,),
            title=body.get("title") or source_id, sources=(recorded,),
            discovered_by=discovery.DISCOVERED_BY_MANUAL, confidence=1.0))
    return claims


def _read_named_sources(claims) -> None:
    """Fingerprint every claim that NAMES its files instead of describing a book.

    Two kinds of claim arrive with the bytes already in hand: an adapter's own
    manuscript (``blueprint_tex``) and an operator's ``--source``.  Neither has a
    PDF to locate, so the facts a search would have established are read here,
    once, before anything is registered or printed: the sha256 over the
    CONCATENATED bytes in the order the claim gives, the byte count, the format
    and -- from ``sources/locator.py``, which owns both rules -- whether the text
    has printed pages at all.

    The concatenation is the point for a manuscript spread over chapter files: a
    sha over the master alone would not move when a chapter was edited, and every
    row stamped with it would claim to have been read from bytes it was not.
    """
    from sources.locator import FORMAT_PDF, PAGINATION_FORMFEED, detect_pagination, source_format
    for claim in claims:
        if not claim.files:
            continue
        try:
            fmt = source_format(claim.files[0])
        except ValueError as exc:
            raise SourceError(str(exc), hint="give " + _accepted_suffixes()) from exc
        blob = _concatenated(claim.files)
        claim.sha256 = _sha256_bytes(blob)
        claim.bytes = len(blob)
        claim.source_format = fmt
        claim.pagination = (PAGINATION_FORMFEED if fmt == FORMAT_PDF
                            else detect_pagination(blob.decode("utf-8", errors="ignore")))


def _concatenated(files) -> bytes:
    """The bytes of these files, in this order, with nothing between them."""
    return b"".join(Path(p).read_bytes() for p in files)


def _sha256_bytes(blob: bytes) -> str:
    """sha256 of raw bytes.

    ``cflib.hashing`` fingerprints a file or a str; a manuscript assembled from
    several files is neither until it is written, and hashing the decoded text
    would fingerprint the decode rather than the bytes.
    """
    return hashlib.sha256(blob).hexdigest()


def _assemble_manuscript(workspace, body: dict, repo: Path | None = None) -> None:
    """Write a multi-file manuscript into the workspace as one source file.

    A source has to BE a file for `prepare` to read it, fingerprint it and answer
    the source gate, and a blueprint's manuscript is a tree.  The concatenation
    the claim's sha covers is written once into the workspace's own
    ``sources/<id>/`` directory -- never into the companion repo, which is read
    as data -- and the workspace row points at it from then on, so
    `cf sources refresh <id>` re-prepares the same bytes and a later
    `cf onboard` re-derives them.

    The sha is re-checked here rather than trusted: the files were read a moment
    ago to make the claim, and a tree that changed in between would otherwise be
    registered under a sha that describes neither version.
    """
    files = body.pop("manuscript_files", None)
    if not files:
        return
    blob = _concatenated(files)
    if _sha256_bytes(blob) != body.get("sha256"):
        raise SourceError(
            f"{body['id']}: the manuscript changed while it was being onboarded "
            f"({len(files)} files)",
            hint=f"re-run `cf onboard {repo or '<repo>'}`")
    out = workspace.paths.source_dir(body["id"]) / f"manuscript{Path(files[0]).suffix}"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(out)
    body["pdf"] = str(out)
    log.ok(f"assembled {out.name}   {len(files)} manuscript file(s), {len(blob)} bytes "
           f"(the sha covers them in this order)")


class SourceRegistration:
    """What the workspace held before a command registered a source.

    `cf sources add` and `cf onboard` both have to write the source row -- and,
    for a source nobody has catalogued, the registry overlay row -- BEFORE
    :func:`_prepare_source` can read the file and say whether it is the document
    at all.  A refusal there must leave neither behind: it is exactly the case
    where the operator names a different file and runs the command again, and a
    refusal that keeps the row, the overlay entry and a prepared directory is
    then nagged about by `cf status` for good, under a name nothing tells them
    is recoverable.

    Snapshot before the first ``upsert_source``/``append_overlay``; call
    :meth:`retract` from the refusal's handler.  The overlay is put back from its
    BYTES rather than re-dumped, because a re-dump normalises the comment header
    `cf init` wrote and leaves a refused command with a diff to its name.
    """

    def __init__(self, workspace):
        self.workspace = workspace
        self.source_ids = set(workspace.data.get("sources") or {})
        self.overlay_path = workspace.root / OVERLAY_FILE
        self.overlay_bytes = (self.overlay_path.read_bytes()
                              if self.overlay_path.exists() else None)
        sources_dir = workspace.paths.sources_dir
        self.prepared = ({p.name for p in sources_dir.iterdir()}
                         if sources_dir.is_dir() else set())

    def retract(self, source_ids) -> dict[str, bool]:
        """Undo the rows, overlay entries and directories *source_ids* added.

        Returns ``{source_id: a prepared directory was removed}`` for the ids
        this call created.  A source the workspace already held is not this
        call's to remove, so it is left alone and reported by the caller.  The
        same goes for a DIRECTORY that existed before this call: it is left in
        place even though prepare may have rewritten it, because deleting a
        directory this call did not create is the worse mistake.
        """
        dropped: dict[str, bool] = {}
        for source_id in source_ids:
            if source_id in self.source_ids:
                continue
            self.workspace.remove_source(source_id)
            directory = self.workspace.paths.source_dir(source_id)
            removed = source_id not in self.prepared and directory.exists()
            if removed:
                shutil.rmtree(directory, ignore_errors=True)
            dropped[source_id] = removed
        if dropped:
            if self.overlay_bytes is None:
                self.overlay_path.unlink(missing_ok=True)
            else:
                self.overlay_path.write_bytes(self.overlay_bytes)
        return dropped


def _register_source(workspace, registry: Registry, body: dict, result) -> None:
    """workspace.yaml + (for an unknown source) the registry overlay."""
    source_id = body["id"]
    fields = {k: v for k, v in body.items() if k not in {"id", "declared_offset"}}
    fields.setdefault("role", ROLE_AUDITED)
    if body.get("registry") == "local":
        claim = result.claims_by_source.get(source_id)
        # `pdf=` is the point: discovery has already located and fingerprinted
        # this file, and an overlay row written without the sha it just computed
        # cannot be matched by CONTENT on the next workspace -- which is both
        # what R3 asks of the registry and what keeps the next discovery from
        # splitting one PDF into two candidates again.
        entry = entry_from_claim(source_id, claim or _empty_claim(body), pdf=_located_of(body))
        path = registry.append_overlay(workspace, entry)
        fields["registry"] = "overlay"
        fields["confirmed"] = False
        log.warn(f"{source_id}: nobody has catalogued this source; appended to {path.name} "
                 f"with confirmed: false")
    workspace.upsert_source(source_id, **fields)


def _located_of(body: dict):
    """The `LocatedPdf` discovery already built, rebuilt from the source body."""
    if not body.get("pdf") or not body.get("sha256"):
        return None
    return discovery.LocatedPdf(
        path=Path(str(body["pdf"])), sha256=str(body["sha256"]),
        bytes=int(body.get("bytes") or 0), pages=body.get("pages"),
        text_sha256=body.get("text_sha256"),
        where=(body.get("discovered") or {}).get("pdf_found_in", "repo_declared"))


def _empty_claim(body: dict) -> discovery.SourceClaim:
    return discovery.SourceClaim(signal="filesystem", path="", title=body.get("title", ""),
                                 edition=body.get("edition", ""))


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def _prepare_source(workspace, source_id: str, *, force: bool, no_errata: bool,
                    declared_offset: int | None = None,
                    fetch_errata_ok: bool = False,
                    onboarding: Path | None = None) -> None:
    """Run STAGES-A's prepare() + errata fetch for one source.

    ``declared_offset`` is the offset the repo's own prose declares, handed
    straight in (never round-tripped through workspace.yaml, which is where it
    used to be dropped): :func:`cflib.inference.infer_offset` REFUSES when the
    running heads say something else.  ``fetch_errata_ok`` allows the one
    fetch a repo-supplied URL would otherwise not get.

    ``onboarding`` is the repo `cf onboard` is working on, and it is here for the
    REFUSALS below: this runs before the companion entry is written, so a hint
    that said ``cf sources add <source> --for <companion>`` named a command that
    answers "unknown companion" every time it was printed from here.  With the
    repo in hand the same refusal can name ``cf onboard <repo> --source <file>``,
    which works.  `cf sources add` / `cf sources refresh` pass nothing and keep
    the `--for` spelling, which is right for a companion that already exists.
    """
    ref = workspace.source(source_id)
    out_dir = workspace.paths.source_dir(source_id)
    if ref.pdf is None or not Path(ref.pdf).exists():
        log.warn_block(
            f"{source_id}: no source file on this machine -- text, items, the quote gate "
            f"and the anchor check cannot run",
            [f"         once you have it: cf sources add <source> --for <companion>"])
        # the errata is derived from bytes of its own, so a stale render is
        # still fixable here: nothing below this needs the source file.
        _report_offline_errata(workspace, source_id,
                               _rerender_stale_errata(workspace, source_id))
        return
    from sources import prepare_source

    # BEFORE prepare() and before the network question: an errata.md this
    # checkout's converter would not produce is re-derived from the bytes
    # already on disk, which needs neither the source text (a refusal below
    # must not leave it stale) nor the network (`--no-errata` means "do not go
    # out", not "keep a render known to be stale").
    errata_meta = _rerender_stale_errata(workspace, source_id)

    body = (workspace.data.get("sources") or {}).get(source_id) or {}
    payload = prepare_source.prepare(
        str(ref.pdf), str(out_dir),
        source_id=source_id,
        # an override a human set stays an override; the repo's declaration is a
        # cross-check the running heads have to agree with.
        offset=(ref.offset if ref.offset_method in {"manual", "declared"} and ref.offset_confidence
                else None),
        declared=declared_offset,
        kinds=list(ref.kinds) if ref.kinds and ref.registry != "local" else None,
        # the numbering depth is inferred like the kinds are, and overridden the
        # same way: `cf sources set <id> numbering=3,4` for a book whose deeper
        # labels are too few for the inference to see.
        segments=body.get("numbering") or None,
        edition=ref.edition,
        expect_sha=ref.sha256 or "",
        force=force,
    )
    confidence = float(payload.get("offset_confidence") or 0.0)
    if confidence < inference.MIN_OFFSET_CONFIDENCE:
        raise SourceError(
            f"could not infer the page offset for {source_id} "
            f"(best guess {payload['offset']}, confidence {confidence:.2f} over "
            f"{payload.get('offset_voting_pages', 0)} pages with a running head)",
            hint=f"set it explicitly: cf sources set {source_id} offset={payload['offset']}")
    workspace.upsert_source(
        source_id,
        offset=payload["offset"], offset_confidence=confidence,
        offset_method=payload["offset_method"], pages=payload["pages"],
        bytes=payload.get("text_bytes") and Path(ref.pdf).stat().st_size,
        text_sha256=payload["text_sha256"], kinds=list(payload["kinds"]))
    log.ok(f"prepared  sources/{source_id}/   text {payload['pages']} pages, "
           f"{payload['items']} items, offset {payload['offset']} "
           f"(confidence {confidence:.3f}, {payload['offset_method']})")
    _refuse_an_empty_source(source_id, ref, payload, workspace, onboarding=onboarding)
    _teach_the_overlay(workspace, source_id, sha256=ref.sha256, bytes=ref.bytes,
                       pages=payload["pages"], offset=payload["offset"],
                       offset_confidence=confidence, offset_method=payload["offset_method"],
                       kinds=list(payload["kinds"]))

    if no_errata:
        _report_offline_errata(workspace, source_id, errata_meta)
        return
    fetched = _fetch_errata(workspace, source_id, out_dir, offset=payload["offset"],
                            fetch_errata_ok=fetch_errata_ok)
    # ONE warning block per source, about whichever render is now on disk: the
    # fetched one, or (no URL, not fetched, fetch failed) the one it kept.
    _report_errata_maths(source_id, fetched if fetched is not None else errata_meta)


def _fetch_errata(workspace, source_id: str, out_dir, *, offset,
                  fetch_errata_ok: bool) -> dict | None:
    """The network half of :func:`_prepare_source`: errata.json after a fetch.

    ``None`` when nothing was fetched -- no URL, a repo-supplied URL nobody
    confirmed, or a failed fetch -- each of which it has already said.
    """
    ref = workspace.source(source_id)
    entry = Registry.load(workspace).get(source_id)
    url, conf, method = inference.infer_errata_url([], entry)
    if not url:
        url, conf, method = (ref.errata_url, ref.errata_confidence or 0.5, "workspace")
    if not url:
        log.warn(f"{source_id}: no errata page found (a real erratum has been missed before)")
        return None
    provenance = _errata_provenance(entry, ref, method)
    if provenance == "repo" and not fetch_errata_ok:
        # The URL came out of the COMPANION's own files. Fetching it is an
        # outbound GET this machine did not choose, and the page it returns is
        # shipped to the fleet and handed to workers as authoritative errata --
        # so a repo would be choosing both. It waits for a human.
        log.warn_block(
            f"{source_id}: the errata URL {url} came from the companion repo "
            f"({method}); NOT fetched",
            [f"         confirm it, then re-run:  cf sources set {source_id} "
             f"errata_url={url}",
             f"         or onboard with --fetch-errata (workspace policy "
             f"allow_errata_fetch: true does it once and for all)"])
        workspace.upsert_source(source_id, book_page_url=url, errata_method="repo_unconfirmed")
        return None
    from sources import fetch_errata
    try:
        meta = fetch_errata.fetch(url, str(out_dir), source_id=source_id,
                                  source_sha=ref.sha256 or "", offset=offset,
                                  provenance=provenance)
    except Exception as exc:                        # noqa: BLE001 - offline is not fatal
        log.warn(f"{source_id}: errata fetch failed ({exc}); sources/{source_id}/errata.md is stale")
        return None
    raw = workspace.paths.source_errata_raw(source_id)
    confirmed = inference.confirm_errata(raw.read_text(encoding="utf-8", errors="ignore")
                                         if raw.exists() else "")
    if not confirmed:
        conf = conf / 2
        log.warn(f"{source_id}: {url} does not contain the word 'errata' "
                 f"(errata_url_unconfirmed)")
    workspace.upsert_source(source_id, errata_url=url, errata_confidence=round(conf, 3),
                            errata_method=provenance)
    _teach_the_overlay(workspace, source_id, errata_url=url)
    log.ok(f"errata    {url}  HTTP {meta['http_status']}  "
           f"{meta['entries']} entries  ({method}, confidence {conf:.2f}, url from {provenance})")
    return meta


def _rerender_stale_errata(workspace, source_id: str) -> dict:
    """Re-derive errata.md when THIS checkout's converter has moved on.

    The errata file is derived data -- ``errata.raw.html`` is the fetch -- so a
    converter fix has to reach the sources already ingested, and it cannot wait
    for the page to change: the bug that motivated the version (every formula
    deleted, the prose left reading as English) lives in files whose page never
    changes again.  Offline, idempotent, and a no-op for a source whose
    errata.md this converter already wrote.

    Returns the errata record as it now stands (``{}`` when it could not be
    read); the CALLER reports its suspect entries, once, so a run that goes on
    to fetch does not print the same block twice.  A damaged errata.json is a
    warning, never a traceback: it used to be a file `--no-errata` never read.
    """
    from sources import fetch_errata
    out_dir = workspace.paths.source_dir(source_id)
    try:
        meta = fetch_errata.rerender(str(out_dir))
    except (OSError, ValueError) as exc:          # ValueError: a damaged errata.json
        log.warn(f"{source_id}: could not re-render errata.md ({exc}); "
                 f"a fetch rewrites sources/{source_id}/errata.json: "
                 f"cf sources refresh {source_id}")
        return {}
    if meta.get("changed"):
        was = meta.get("previous_renderer_version")
        log.ok(f"errata    re-rendered sources/{source_id}/errata.md from the kept HTML "
               f"(converter v{was if was is not None else 1} -> v{fetch_errata.RENDERER_VERSION}, "
               f"{meta.get('math_recovered') or 0} formulas recovered)")
        if meta.get("raw_sha_drift"):
            log.warn(f"{source_id}: errata.raw.html no longer hashes to what errata.json "
                     f"recorded; the re-render used the BYTES on disk")
    return meta


def _report_offline_errata(workspace, source_id: str, meta: dict) -> None:
    """What a run that will not fetch says about the errata it leaves in place.

    Its suspect entries, and -- when the render is stale and there is no kept
    HTML to re-derive it from -- that only a fetch can fix it, because the
    offline re-render this run just attempted had nothing to work from.
    """
    from sources import fetch_errata
    if (meta.get("reason") == "no errata.raw.html"
            and workspace.paths.source_errata_md(source_id).exists()
            and fetch_errata.is_stale_render(meta.get("renderer_version"))):
        log.warn(f"{source_id}: errata.md was rendered by an older converter and no "
                 f"errata.raw.html was kept to re-derive it from -- re-fetch it: "
                 f"cf sources refresh {source_id}")
    _report_errata_maths(source_id, meta)


def _report_errata_maths(source_id: str, meta: dict) -> None:
    """Say, loudly and on one stream, which entries may have lost content.

    An erratum whose symbols were dropped reads as grammatical English with the
    substance removed ("In Corollary 3.2.4(b), should be , and the same"), and a
    worker handed one cannot tell.  `cf` does not refuse the page over it -- the
    other entries are real, and the raw HTML is right there -- but it never
    passes one off silently, which is what used to happen.
    """
    suspect = meta.get("suspect") or []
    if not suspect:
        return
    lines = [f"         {s['entry'][:140]}  [{', '.join(s['reasons'])}]"
             for s in suspect[:5]]
    if len(suspect) > 5:
        lines.append(f"         ... and {len(suspect) - 5} more (errata.json `suspect`)")
    lines.append(f"         read sources/{source_id}/errata.raw.html for these before "
                 f"treating them as the author's correction")
    log.warn_block(
        f"{source_id}: {meta.get('suspect_entries', len(suspect))} errata entries may have "
        f"lost content in conversion "
        f"({meta.get('math_unrecovered') or 0} formulas were not recoverable from the page)",
        lines)


def _teach_the_overlay(workspace, source_id: str, **learned) -> None:
    """Record on the overlay row what reading the source actually taught us.

    R3 makes the registry the memory of what is known about a source.  The row
    `cf onboard` / `cf sources add` appends is written BEFORE the text is read,
    so it knows no offset, no kinds and (for onboard) not even the sha; this puts
    them back.  A no-op for a source that came from the skill's own registry.
    """
    if workspace.source(source_id).registry != "overlay":
        return
    registry = Registry.load(workspace)
    path = registry.update_overlay(workspace, source_id, **learned)
    if path is not None:
        log.detail(f"registry  {path.name}: {source_id} now remembers "
                   f"offset {learned.get('offset')}, {len(learned.get('kinds') or [])} kinds")


def _name_the_source(onboarding: Path | None) -> str:
    """The command that names a different source, for the refusals below.

    Two contexts, two spellings, and the wrong one is a dead end: during
    `cf onboard` no companion entry exists yet, so only ``--source`` can be
    followed; from `cf sources refresh` the companion is already there and
    ``cf sources add --for`` is the command that exists.
    """
    if onboarding is not None:
        return f"cf onboard {onboarding} --source <file>"
    return "cf sources add <source> --for <companion>"


def _self_authored(workspace, source_id: str) -> bool:
    """Did the ADAPTER claim this source out of the companion's own tree?

    A manuscript the companion wrote has no printed-book headings for the item
    grammar to number, and there is no other document its bytes could be mistaken
    for -- they are the ones the adapter's own glob just matched.  That is the one
    case the two "this is the wrong document" refusals below do not cover, and it
    is read off the row rather than guessed: a file an OPERATOR named is somebody
    else's book, and every one of those checks still applies to it.
    """
    body = ((workspace.data.get("sources") or {}).get(source_id) or {}) if workspace else {}
    return ((body.get("discovered") or {}).get("pdf_found_in")
            == discovery.DISCOVERED_BY_ADAPTER)


def _refuse_an_empty_source(source_id: str, ref, payload: dict, workspace=None, *,
                            onboarding: Path | None = None) -> None:
    """A source table that is not a source table -- empty, or almost empty.

    `locate_pdf` can only rank the candidates it finds; the text is the last
    check, and it is a decisive one.  A document whose own headings number
    nothing, or none of the kinds the repo tags, cannot be paired, cannot answer
    a quote gate and cannot carry a page anchor -- so adopting it would mean
    auditing a companion against the wrong document in silence.

    ``items == 0`` used to be the whole guard, and it is the case that almost
    never happens: a real book's prose always numbers SOMETHING.  A 404-page book
    whose numbering puts the kind after the number prepared 24 items -- 0.09 of
    the slots its own sections number -- and was adopted without a word, with
    `kind_counts` reading `Now: 5, See: 3` on disk the whole time.  Two more
    signals are therefore refusals now, both already computed and both with a
    named settlement:

    * every declared kind missing from the text (`kinds_not_in_text` /
      `kinds_not_inferred`): the kind list belongs to another book;
    * `item_completeness` below `MIN_ITEM_COMPLETENESS`: the grammar read prose,
      not headings.

    All three ask the same question -- "is this the document this companion
    formalises?" -- and all three are answered before it is asked for a source the
    adapter claimed out of the companion's own tree (:func:`_self_authored`),
    whose empty item table is reported instead.
    """
    kind_warnings = payload.get("kind_warnings") or []
    kinds = list(payload.get("kinds") or [])
    if _self_authored(workspace, source_id):
        # Not a refusal, but not silence either: a reader of `cf status` has to be
        # able to see that this source's item table is empty ON PURPOSE.
        if not payload.get("items"):
            log.info(f"         {source_id}: 0 numbered items in the manuscript's own prose, "
                     f"as expected -- its items are the tagged environments the adapter reads")
        return
    if not payload.get("items"):
        raise SourceError(
            f"{source_id}: {ref.pdf} has 0 numbered items in its text "
            f"({payload.get('pages')} pages, kinds {', '.join(kinds) or 'none'})"
            + (f"; {kind_warnings[0]}" if kind_warnings else ""),
            hint=f"this is almost certainly not the document this companion formalises. "
                 f"Name the right source: {_name_the_source(onboarding)}")
    body = ((workspace.data.get("sources") or {}).get(source_id) or {}) if workspace else {}
    if body.get("completeness_ok"):
        return
    counts = payload.get("kind_counts") or {}
    seen = ", ".join(f"{k}: {n}" for k, n in list(counts.items())[:3]) or "nothing"
    absent: list[str] = []
    for warning in kind_warnings:
        if warning.startswith("kinds_not_in_text:"):
            absent = [k.strip() for k in warning.split(":", 1)[1].split(",") if k.strip()]
    if "kinds_not_inferred" in (payload.get("warnings") or []) or (
            kinds and len(absent) >= len(kinds)):
        raise SourceError(
            f"{source_id}: not one of the {len(kinds)} kinds this source declares "
            f"({', '.join(kinds)}) heads a numbered item in its text; what heads them is {seen}",
            hint=f"that kind list is another book's. Read the right one off this text: "
                 f"cf sources set {source_id} kinds=<comma-separated> (or "
                 f"cf sources set {source_id} completeness_ok=true if the table really is "
                 f"this small)")
    measured = payload.get("item_completeness")
    if measured is None:
        return                        # prepared before this check existed; not re-judged
    if float(measured) < inference.MIN_ITEM_COMPLETENESS:
        detail = payload.get("item_completeness_detail") or {}
        raise SourceError(
            f"{source_id}: {payload['items']} items is {float(measured):.3f} of the "
            f"{detail.get('expected', 0)} slots this source's own numbering counts "
            f"(below {inference.MIN_ITEM_COMPLETENESS}); what heads its numbered lines is {seen}",
            hint=f"the grammar read prose, not headings -- usually the kind list or the "
                 f"numbering: cf sources set {source_id} kinds=<comma-separated>, or "
                 f"cf sources set {source_id} completeness_ok=true if this really is the "
                 f"whole table")


def _errata_provenance(entry, ref, method: str) -> str:
    """Who chose this errata URL: ``registry``, ``manual`` or ``repo``.

    Only a CONFIRMED registry entry (the curated file the skill ships, or an
    overlay row a human has vouched for) and a URL a human typed with
    `cf sources set` are fetched without being asked.  Everything else -- a book
    page scraped out of ``formalization.yaml``, a README link, the parent
    directory of a download URL the repo names -- is the repository's choice of
    an outbound request and of text that lands in agent prompts.
    """
    if entry is not None and getattr(entry, "errata_url", "") and getattr(entry, "confirmed", False):
        return "registry"
    if ref.errata_method == "manual":
        return "manual"
    if ref.confirmed and ref.errata_url and method == "workspace":
        return "manual"
    return "repo"


# ---------------------------------------------------------------------------
# which sources, and which one the anchors follow
# ---------------------------------------------------------------------------

def _decide_sources(workspace, repo: Path, name: str, result, args):
    """(audited ids, bundled ids, anchor id, confidence, evidence).

    A companion has a LIST of sources (R1), and the list may hold two genuinely
    different books.  Only EDITIONS OF ONE WORK are rivals for the anchor, so the
    discovered ids are clustered by ``same_work`` first and
    :func:`discovery.choose_anchor_source` runs inside each cluster.  Scoring all
    of them together asked which of "An Introduction to Measure Theory" and
    "Elementary Notes on Sets" the page anchors follow, a question with no
    answer, and on a first onboard (no rows to score) it refused outright -- so a
    two-book companion had no flagless path at all.
    """
    ids = result.source_ids()
    hints = [c.anchor_hint for c in result.claims if c.anchor_hint]
    rows = _existing_rows(workspace, name)
    clusters = _same_work_clusters(result, ids)
    #: With no rows there is nothing MECHANICAL to decide with, so a pick here is
    #: provisional and `_confirm_anchor` settles it once this run has extracted.
    provisional = not rows
    if args.anchor_source:
        if args.anchor_source not in ids:
            raise CfError(f"--anchor-source {args.anchor_source} is not one of the discovered "
                          f"sources ({', '.join(ids)})")
        anchor, conf, evidence = args.anchor_source, 1.0, {"reason": "--anchor-source"}
        chosen = [anchor] + [_elect(repo, rows, c, workspace, hints, provisional=provisional)[0]
                             for c in clusters if anchor not in c]
    else:
        elected = [_elect(repo, rows, cluster, workspace, hints, provisional=provisional)
                   for cluster in clusters]
        anchor, conf, evidence = elected[0]
        chosen = [sid for sid, _c, _e in elected]
    audited = [sid for sid in ids if sid in chosen]
    bundled = [sid for sid in ids if sid not in audited]
    if len(audited) > 1:
        log.info(f"{name} formalises {len(audited)} different works: "
                 f"{', '.join(audited)} (anchor: {anchor})")
    return audited, bundled, anchor, conf, evidence


def _elect(repo: Path, rows, cluster: list[str], workspace, hints, *, provisional: bool):
    """The one edition of ONE work a companion anchors to, or a refusal.

    Both anchor call sites come through here, and an UNDECIDABLE anchor is a
    refusal at both -- the CLAUDE.md rule that below ``MIN_ANCHOR_AGREEMENT`` the
    command stops and names the flag that settles it.  It used to be a warning at
    one site and a refusal at the other, so the identical command exited 0 with no
    statements.jsonl to score and 2 once one existed, and re-running `cf onboard`
    after editing conventions.md -- which onboard's own "next:" block tells you to
    do -- broke a workspace that had worked a minute earlier.

    ``provisional`` is the difference between undecidable and not-yet-decided.
    Before extraction there are no page anchors to score, so `choose_anchor_source`
    can only fall back to the prose sentence and scores it 0.60 whatever the
    editions look like -- refusing there refused EVERY first onboard of a
    two-edition work, flags or not, and left a two-book companion with no flagless
    path at all.  A provisional pick therefore warns and is kept; the refusal lands
    at :func:`_confirm_anchor`, where a mechanical decision was really attempted.
    Idempotence holds either way: run 1 refuses after extraction, and run 2 scores
    the same rows at the pre-extraction site and refuses identically.
    """
    sid, conf, evidence = discovery.choose_anchor_source(
        repo, rows, cluster, workspace=workspace, hints=hints)
    if conf < discovery.MIN_ANCHOR_AGREEMENT:
        reason = (evidence or {}).get("reason", "no evidence")
        if provisional:
            log.warn("the anchor edition could not be confirmed mechanically: "
                     f"{sid} scores {conf:.2f} ({reason}) with no page anchors extracted yet")
            log.warn(f"keeping {sid} (confidence {conf:.2f}); settle it with "
                     f"`cf onboard {repo} --anchor-source <id>`")
            return sid, conf, evidence
        raise DiscoveryError(
            f"cannot decide which edition {repo.name} anchors to: "
            f"{sid} scores only {conf:.2f} ({reason}), "
            f"below the {discovery.MIN_ANCHOR_AGREEMENT:.2f} a mechanical decision needs",
            hint=f"settle it with `cf onboard {repo} --anchor-source <id>`, or prepare the "
                 "missing edition's PDF first (`cf sources add <pdf> --for <companion>`) so "
                 "the page anchors can be scored")
    return sid, conf, evidence


def _same_work_clusters(result, ids: list[str]) -> list[list[str]]:
    """Group the discovered ids by ``same_work``: one cluster per WORK.

    Two editions of one book are rivals (one is the pagination the repo follows,
    the other is prepared only so the page map can be checked); two different
    books are not rivals at all, they are two entries on the companion's list.
    """
    clusters: list[list[str]] = []
    for sid in ids:
        body = result.get(sid) or {}
        siblings = set(body.get("same_work") or [])
        for cluster in clusters:
            others = set(cluster)
            back = set()
            for other in cluster:
                back |= set((result.get(other) or {}).get("same_work") or [])
            if siblings & others or sid in back:
                cluster.append(sid)
                break
        else:
            clusters.append([sid])
    return clusters


def _confirm_anchor(workspace, repo, name, result, args, rows, anchor, conf, evidence):
    """Re-decide the anchor edition against the rows we just extracted.

    EVERY cluster of two or more editions is re-elected, not only the one the
    anchor landed in: `_decide_sources` ran before any rows existed, so each of
    its picks was a prose guess, and the anchor's own cluster may well be a single
    candidate while another work's editions are the undecided ones.  Re-electing
    only the anchor's cluster let `_elect`'s refusal be skipped entirely there.
    The anchor itself is only re-pinned from its own cluster -- the rivals for the
    anchor are the other editions of ITS work, never another book the same
    companion also formalises.

    No try/except here on purpose: `_elect` refuses an undecidable anchor, and
    catching that refusal is exactly what made the same command exit 0 before any
    rows existed and 2 afterwards.
    """
    if args.anchor_source or not rows or len(result.source_ids()) < 2:
        return anchor, conf, evidence
    hints = [c.anchor_hint for c in result.claims if c.anchor_hint]
    chosen = set(result.source_ids()) - set(result.bundled_sources or ())
    out = (anchor, conf, evidence)
    for cluster in _same_work_clusters(result, result.source_ids()):
        if len(cluster) < 2:
            continue
        winner, winner_conf, winner_evidence = _elect(
            repo, rows, cluster, workspace, hints, provisional=False)
        if anchor not in cluster:
            if winner not in chosen:
                log.warn(f"the anchors agree with {winner}, not the edition picked for that work "
                         f"({', '.join(sid for sid in cluster if sid in chosen) or 'none'}): "
                         f"re-run `cf onboard {repo} --anchor-source {winner}` to re-pin it")
            continue
        if winner != anchor:
            log.warn(f"the anchors agree with {winner}, not {anchor}: re-pinning the anchor "
                     f"edition and re-run `cf extract --companion {name}`")
        out = (winner, winner_conf, winner_evidence)
    return out


def _print_anchor(result) -> None:
    log.info("")
    log.info("anchor edition")
    log.info(f"  {result.anchor_source:<44s} confidence {result.anchor_confidence:.2f}")
    for sid, row in sorted((result.anchor_evidence or {}).get("agreement", {}).items()):
        log.info(f"      anchor agreement  {sid:<34s} {row.get('agreement', 0):.2f} "
                 f"({row.get('ok', 0)} ok / {row.get('mismatched', 0)} mismatched)")
    if (result.anchor_evidence or {}).get("reason"):
        log.info(f"      reason: {result.anchor_evidence['reason']}")
    if (result.anchor_evidence or {}).get("hint"):
        log.info(f"      corroborated by prose: \"{result.anchor_evidence['hint'][:110]}\"")
    for sid in result.bundled_sources:
        log.info(f"  the repo BUNDLES {sid} but does not anchor to it -> bundled_sources "
                 "(prepared as a reference, audited by nobody)")


def _existing_rows(workspace, name: str) -> list[dict]:
    """statements.jsonl from a previous run, for the mechanical anchor check."""
    from cflib.jsonio import read_jsonl
    path = workspace.paths.statements_jsonl(name)
    return read_jsonl(path) if path.exists() else []


# ---------------------------------------------------------------------------
# adapter.yaml, dry run, extraction
# ---------------------------------------------------------------------------

def _detected_options(kind: str, evidence: dict) -> dict:
    """The adapter.yaml values detection MEASURED, rather than defaulted.

    ``tag.require_backticks`` is the whole of it today, and it is the reason the
    second convention this skill names -- a repo that writes the book label bare
    at the start of the docstring -- could not be onboarded by either path: the
    template hard-coded the backticked spelling, so `cf extract` found one
    backticked tag among 2,371 docstrings.  Nothing about a source or a repo is
    typed in (CLAUDE.md S4): a spelling the detector can read is written down
    with what it read.
    """
    if kind != "docstring_tags":
        return {"require_backticks": "true"}
    detected = evidence.get("require_backticks")
    if detected is None:
        return {"require_backticks": "true"}
    bare, backticked = evidence.get("tagged_bare"), evidence.get("tagged_backticked")
    note = ""
    if bare is not None and backticked is not None:
        note = f"   # detected: {backticked} backticked, {bare} bare"
    return {"require_backticks": ("true" if detected else "false") + note}


def _write_adapter_yaml(workspace, name: str, repo: Path, kind: str, commit, sources,
                        anchor: str, args, evidence: dict | None = None) -> Path:
    paths = workspace.paths
    adapter_yaml = paths.adapter_yaml(name)
    paths.companion_dir(name).mkdir(parents=True, exist_ok=True)
    if adapter_yaml.exists() and not args.force:
        log.info(f"{adapter_yaml} exists; left untouched (use --force to rewrite)")
        return adapter_yaml
    defaults = DEFAULTS[kind]
    text = render_template("adapter.yaml.example", {
        "name": name,
        "repo": str(repo),
        "commit": commit or "null",
        "adapter": kind,
        "granularity": defaults["granularity"],
        "glob": _yaml_str(defaults["glob"]),
        "anchor": defaults["anchor"],
        "write_policy": args.write_policy,
        "owner": _yaml_str(args.owner),
        "source_default": anchor,
        **_detected_options(kind, dict(evidence or {})),
        # Commented-out placeholders: with more than one source bound and no map,
        # the attribution reads each source's items.json per row (adapters/api.py
        # SourceBinding._source_holding), so an uncommented rule is an OVERRIDE
        # of that, not the only thing standing between a row and the anchor.
        "source_map": "\n".join(
            [f"    # a rule here overrides the per-row items.json lookup:"]
            + [f"    # - {{source: {sid}, files: [\"...\"]}}" for sid in sources]),
    }, allow_missing=True)
    atomic_write(adapter_yaml, text)
    log.ok(f"wrote {adapter_yaml}")
    return adapter_yaml


def _yaml_str(value: str) -> str:
    return '"' + str(value).replace('"', '\\"') + '"' if value else '""'


def _commit(repo: Path) -> str | None:
    try:
        res = gitsafe.run(repo, ["rev-parse", "HEAD"], timeout=30)
    except (OSError, subprocess.SubprocessError):           # pragma: no cover
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def _bind(workspace, adapter_yaml: Path, sources, anchor: str):
    """Load the companion's adapter, bound to the sources it is audited against.

    The adapter is handed the WHOLE workspace and the ``{source_id: sha}`` map:
    an item is the pair (source_id, item), so the adapter resolves the kinds,
    the page offset and the sha per source rather than being handed one source's
    parameters and told to assume.  The anchor is pinned separately because
    onboarding re-decides it against the rows it is about to extract, which makes
    it fresher than the ``sources.default`` already written into adapter.yaml.
    """
    from cflib.adapters_api import load as load_adapter
    adapter = load_adapter(adapter_yaml, workspace=workspace)
    if anchor:
        adapter.bind_default_source(anchor)
    return adapter, {sid: workspace.source(sid).sha256 for sid in (sources or [anchor]) if sid}


def _dry_run(adapter_yaml: Path, repo: Path, workspace, sources, anchor: str):
    """Run the adapter's parser without writing a statement table."""
    try:
        adapter, shas = _bind(workspace, adapter_yaml, sources, anchor)
        counts = dict(adapter.dry_run(repo, sources=shas))
    except AdapterError as exc:
        log.warn(str(exc))
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - a parser bug must not lose the files we wrote
        log.warn(f"dry run failed: {exc.__class__.__name__}: {exc}")
        return None, f"{exc.__class__.__name__}: {exc}"
    missing = [k for k in DRY_RUN_REQUIRED_KEYS if k not in counts]
    if missing:
        log.warn(f"adapter dry_run() omitted {', '.join(missing)}")
    _print_discovered_sources(counts)
    return counts, None


def _print_discovered_sources(counts: dict) -> None:
    """What the ADAPTER says the repo's sources are, next to what discovery said.

    The two readings come from the same files but through different eyes: a
    disagreement here is the cheapest place to see a repo that names an edition
    it does not tag.
    """
    claims = counts.get("sources") or []
    if not claims:
        return
    log.info("")
    log.info(f"the adapter reads {len(claims)} source signal(s) in this repo:")
    for claim in claims[:8]:
        sha = claim.get("sha256") or claim.get("sha256_prefix", "")
        head = f"  {claim.get('signal', '?'):<20s} {claim.get('path', '')}"
        log.info(head + (f"   sha {sha[:8]}…" if sha else ""))
        if claim.get("title"):
            log.detail(f"      title  {claim['title']}")
        if claim.get("kinds"):
            log.detail(f"      kinds  {' '.join(claim['kinds'])}   (from this repo's own tags)")
    for sid, rows in (counts.get("rows_by_source") or {}).items():
        log.info(f"  rows attributed to {sid}: {rows}")


def _extract(workspace, name: str, adapter_yaml: Path, repo: Path, sources, anchor: str) -> list:
    """statements.jsonl, every row stamped with its source_id and source_sha."""
    try:
        adapter, shas = _bind(workspace, adapter_yaml, sources, anchor)
        rows = [dict(row) for row in adapter.extract(repo, sources=shas)]
    except (AdapterError, CfError) as exc:
        # A companion whose adapter cannot parse it must not lose the workspace
        # entry, the prepared sources and the errata already written above: the
        # files stay, the table does not, and the message says which.
        log.warn(f"{name}: extraction failed: {exc}")
        return []
    except Exception as exc:  # noqa: BLE001 - an adapter bug is reported, not raised through
        log.warn(f"{name}: extraction failed: {exc.__class__.__name__}: {exc}")
        log.detail(traceback.format_exc())
        return []
    _report_unattributed(name, adapter, sources, anchor)
    problems = _report_schema_problems(workspace, name, rows, anchor)
    path = workspace.paths.statements_jsonl(name)
    meta = {"companion": name, "repo": str(repo), "sources": list(sources),
            "anchor_source": anchor, "rows": len(rows), "at": state_mod.now()}
    if problems:
        # `cf extract` refuses to write a table that fails the schema gate, and
        # there must be ONE answer to "is this companion's table valid". The rows
        # are still returned, because the anchor edition is scored from them and
        # they never leave this process; they land beside the table with a
        # .rejected suffix so the 142-duplicate-key case can be read.
        rejected = path.parent / (path.name + ".rejected")
        write_jsonl(rejected, rows, meta={**meta, "rejected": problems[:10]})
        log.warn_block(
            f"{name}: statements.jsonl failed schema v2 validation "
            f"({len(problems)} problems); nothing was written to "
            f"{path.relative_to(workspace.root)}",
            [f"         the rows are at {rejected.relative_to(workspace.root)} -- fix "
             f"adapter.yaml (glob, tag pattern, sources.map) and re-run "
             f"`cf onboard {repo} --force`"])
        return rows
    write_jsonl(path, rows, meta=meta)
    log.ok(f"extracted {path.relative_to(workspace.root)}   {len(rows)} rows")
    with state_mod.update(workspace, name) as st:
        state_mod.set_section(st, "extract", {
            "rows": len(rows),
            "items": len({(r.get("source_id"), r.get("item")) for r in rows}),
            "source_ids": sorted({str(r.get("source_id") or "") for r in rows} - {""}),
        })
        for sid in sorted({str(r.get("source_id") or "") for r in rows} - {""}):
            per_source = [r for r in rows if r.get("source_id") == sid]
            state_mod.source_section(st, sid, {"sha": workspace.source(sid).sha256,
                                               "rows": len(per_source),
                                               "items": len({r.get("item") for r in per_source})})
    for sid in sorted({str(r.get("source_id") or "") for r in rows} - {""}):
        _infer_scope(workspace, sid, rows)
    return rows


def _report_unattributed(name: str, adapter, sources, anchor: str) -> None:
    """Say which rows no source could claim when the companion has more than one.

    With one source there is nothing to decide.  With two, the attribution reads
    each candidate's items.json; a tag neither of them numbers falls to the
    anchor, and that is a guess, so it is printed rather than hidden.
    """
    binding = getattr(adapter, "last_binding", None)
    if binding is None or len(list(sources or ())) < 2:
        return
    for tags, why in ((list(binding.ambiguous or []), "are numbered by MORE THAN ONE of"),
                      (list(binding.unattributed or []), "match no item in any of")):
        if not tags:
            continue
        log.warn_block(
            f"{name}: {len(tags)} tag(s) {why} {', '.join(sources)}; "
            f"attributed to the anchor {anchor}",
            [f"    {tag!r}" for tag in sorted(set(tags))[:5]]
            + [f"    add a sources.map rule in {name}/adapter.yaml to say which source "
               f"they belong to"])


def _report_schema_problems(workspace, name: str, rows, anchor: str) -> list[str]:
    """Apply `cf extract`'s schema gate here, and say what it caught.

    Onboarding needs the rows before the gate runs, because the anchor edition is
    decided from them and an empty table decides nothing -- but it must not
    PUBLISH a table `cf extract` would refuse, or the two writers of
    companions/<name>/statements.jsonl disagree about what a valid table is.  So
    the problems are reported here and the caller keeps the table out of the
    companion directory: a companion whose `glob:` reaches a generated mirror of
    its own modules produces two rows per declaration, and a workspace holding
    that table is a workspace whose next stage reads 142 duplicate keys.
    """
    if not rows:
        return []
    cap = 10
    try:
        from adapters.base import validate_rows
        kinds = (tuple(workspace.source(anchor).kinds) + ("In-text",)) if anchor else None
        problems = validate_rows(rows, kinds=kinds, require_source_sha=False, max_problems=cap)
    except Exception as exc:  # noqa: BLE001 - a reporting aid must never be fatal
        log.detail(f"schema check skipped: {exc.__class__.__name__}: {exc}")
        return []
    if not problems:
        return []
    # validate_rows stops at `cap`, so this is a floor, not a total: saying "10"
    # of a table with 142 collisions would read as "nearly clean".
    at_least = "at least " if len(problems) > cap else ""
    lines = [f"    {problem}" for problem in problems[:5]]
    if any("duplicate" in p for p in problems):
        lines.append("    two files define the same declaration: narrow `glob:` in "
                     f"{workspace.paths.adapter_yaml(name)} to the companion's own modules "
                     "(a generated mirror of them is inside the glob), then "
                     f"`cf extract --companion {name} --force`")
    log.warn_block(f"{name}: {at_least}{min(len(problems), cap)} of these rows will NOT pass "
                   f"`cf extract`'s schema gate:", lines)
    return problems


def _infer_scope(workspace, source_id: str, rows) -> None:
    """The audited scope is what the companions TAG, not a flag (S5.3)."""
    items_path = workspace.paths.source_items(source_id)
    if not items_path.exists() or not rows:
        return
    from cflib.jsonio import read_json
    scope = inference.infer_scope(rows, read_json(items_path, default={}), source_id=source_id)
    if not scope["chapters"]:
        return
    ref = workspace.source(source_id)
    chapters = sorted(set(ref.chapters) | set(scope["chapters"]))
    workspace.upsert_source(source_id, chapters=chapters, scope_method=scope["method"])
    workspace.save()
    log.ok(f"scope     chapters {chapters}   from the items the companions actually tag")


def _report(name, repo, commit, counts, problem, conventions, rows, workspace, sources) -> None:
    log.info("")
    log.info(f"companion : {name}")
    log.info(f"repo      : {repo}" + (f" @ {commit[:7]}" if commit else " (not a git repo)"))
    log.info(f"sources   : {', '.join(sources)}")
    if counts:
        rejects = counts.get("rejects") or []
        table = [(k, v) for k, v in counts.items() if not isinstance(v, (list, dict))]
        log.info("")
        log.info(format_table(["count", "value"], table))
        if rejects:
            log.info("")
            log.warn_block(
                f"{len(rejects)} item tags rejected by the strict grammar; first few:",
                [f"    {r.get('raw')!r}  ({r.get('reason')})" for r in rejects[:5]])
    elif problem and problem != "skipped":
        log.warn(f"no counts recorded: {problem}")
    unconfirmed = [s.id for s in workspace.unconfirmed_sources() if s.id in sources]
    log.info("")
    log.info("next:")
    log.info(f"  edit {conventions} and have the repo owner confirm it")
    for sid in unconfirmed:
        log.info(f"  cf sources show {sid}     # nobody has checked this source yet")
    log.info("  cf onboard <the other companion repo>   # then `cf pair`")
