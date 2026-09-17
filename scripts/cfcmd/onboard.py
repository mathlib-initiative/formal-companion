"""cf onboard <repo> -- discover, register, prepare, extract. One command.

docs/design/multi-source-model.md R2 / S4 / S7.2.  The user rejected typing
sources, editions, page offsets and errata URLs, so onboarding reads them off
the repository instead:

    discover  detect the adapter, read the repo's seven signals, locate and
              FINGERPRINT the PDF bytes, infer the offset and the item kinds,
              find the errata page, decide which edition the anchors follow
    register  workspace.yaml + the registry overlay (unconfirmed sources are
              written as `confirmed: false` and surfaced by `cf status`)
    prepare   sources/<id>/{text.txt, pages/, items.json, meta.json, errata.*}
    extract   companions/<name>/statements.jsonl, every row carrying its source

Nothing is written to the companion repo, ever.  Every inference is printed with
its confidence and the file it came from, and a weak inference is a REFUSAL with
the one command that settles it -- never a default.

    cf onboard ~/Desktop/tao-measure-lean
    cf onboard ~/Desktop/measure_theory --name adam
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from cflib import discovery, inference, log, state as state_mod, vocab
from cflib.adapters_api import DRY_RUN_REQUIRED_KEYS
from cflib.errors import AdapterError, CfError, DiscoveryError, SourceError
from cflib.jsonio import write_jsonl
from cflib.log import format_table
from cflib.registry import Registry, entry_from_claim
from cflib.templates import copy_template, render_template
from cflib.workspace import ROLE_AUDITED, ROLE_REFERENCE, from_args
from cflib.jsonio import atomic_write

#: Sensible starting points per adapter kind; edit adapter.yaml afterwards.
DEFAULTS = {
    "docstring_tags": {"glob": "**/*.lean", "anchor": "docstring", "granularity": "item"},
    "attribution_table": {"glob": "pdf-overlay/data/*.json", "anchor": "ledger", "granularity": "item"},
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
    p.add_argument("--no-extract", action="store_true", help="stop after preparing the sources")
    p.add_argument("--force", action="store_true", help="rewrite adapter.yaml and re-prepare")


def _check_adapter_implemented(kind: str) -> None:
    """Refuse an adapter kind the vocabulary names but no code implements.

    ``references/onboarding-contract.md`` says ``cf onboard --adapter
    ledger_nodes`` "refuses with a pointer to this paragraph".  It did not: the
    AdapterError was swallowed into a warning, the command returned 0 having
    written adapter.yaml, conventions.md, state.json AND a workspace.yaml entry,
    and the next ``cf extract`` then aborted for the WHOLE workspace on a
    companion nothing could parse.  This runs before anything is written.
    """
    known = set(vocab.IMPLEMENTED_ADAPTER_KINDS)
    if kind in known:
        return
    raise CfError(
        f"adapter kind {kind!r} is declared in the vocabulary but not implemented in v1",
        hint=("implemented kinds: " + ", ".join(sorted(known)) +
              ". See references/onboarding-contract.md for what ledger_nodes still needs "
              "(an item-level curation pass) and what `custom` would have to provide."),
    )


def run(args) -> int:
    workspace = from_args(args)
    paths = workspace.paths
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        raise CfError(f"{repo} is not a directory",
                      hint="cf onboard takes the PATH to a companion repo: "
                           "`cf onboard ~/Desktop/tao-measure-lean`")
    if repo == workspace.root or workspace.root in repo.parents:
        raise CfError("the companion repo must live outside the workspace directory")
    name = args.name or _default_name(repo)

    # ---- discover ----------------------------------------------------
    kind, adapter_confidence, adapter_evidence = _detect(repo, args.adapter)
    _check_adapter_implemented(kind)
    registry = Registry.load(workspace)
    claims = discovery.discover_sources(repo, adapter=kind)
    result = discovery.resolve_claims(claims, repo=repo, workspace=workspace,
                                      registry=registry, download=args.download)
    result.name = name
    result.adapter = kind
    result.adapter_confidence = adapter_confidence
    result.adapter_evidence = adapter_evidence
    if not result.sources:
        raise DiscoveryError(
            f"{repo}: no source could be discovered",
            hint="the repo names no book, ships no PDF and quotes no sha. Add one by hand: "
                 f"`cf sources add <pdf> --for {name}`")

    _fold_offset_hints(result)
    log.info(result.render())
    if args.dry_run:
        log.info("")
        log.info("--dry-run: nothing written")
        return 0

    # ---- register ----------------------------------------------------
    for body in result.sources:
        _register_source(workspace, registry, body, result)
    workspace.save()

    # ---- prepare -----------------------------------------------------
    log.info("")
    for body in result.sources:
        _prepare_source(workspace, body["id"], force=args.force, no_errata=args.no_errata)

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
    adapter_yaml = _write_adapter_yaml(workspace, name, repo, kind, commit, audited, anchor, args)
    conventions, written = copy_template("conventions.md.example", paths.conventions_md(name),
                                         {"name": name})
    log.ok(f"wrote {conventions}") if written else log.info(f"{conventions} exists; left untouched")

    # ---- extract -----------------------------------------------------
    counts, problem = _dry_run(adapter_yaml, repo, workspace, anchor)
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


def _detect(repo: Path, override: str | None) -> tuple[str, float, dict]:
    if override:
        return override, 1.0, {"reason": "--adapter"}
    return discovery.detect_adapter(repo)


def _fold_offset_hints(result: discovery.DiscoveryResult) -> None:
    """Keep the repo's declared offset on the source body for prepare() to check."""
    for body in result.sources:
        hint = body.pop("offset_hint", None)
        body.pop("anchor_hint", None)
        if hint is not None and body.get("offset") in (None, hint):
            body["declared_offset"] = hint


def _register_source(workspace, registry: Registry, body: dict, result) -> None:
    """workspace.yaml + (for an unknown source) the registry overlay."""
    source_id = body["id"]
    fields = {k: v for k, v in body.items() if k not in {"id", "declared_offset"}}
    fields.setdefault("role", ROLE_AUDITED)
    if body.get("registry") == "local":
        claim = next((c for c in result.claims if source_id in _ids_of(c, result)), None)
        entry = entry_from_claim(source_id, claim or _empty_claim(body))
        path = registry.append_overlay(workspace, entry)
        fields["registry"] = "overlay"
        fields["confirmed"] = False
        log.warn(f"{source_id}: nobody has catalogued this source; appended to {path.name} "
                 f"with confirmed: false")
    workspace.upsert_source(source_id, **fields)


def _ids_of(claim, result) -> list[str]:
    return [b["id"] for b in result.sources if set(b.get("discovered", {}).get("from") or [])
            & set(claim.sources)]


def _empty_claim(body: dict) -> discovery.SourceClaim:
    return discovery.SourceClaim(signal="filesystem", path="", title=body.get("title", ""),
                                 edition=body.get("edition", ""))


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def _prepare_source(workspace, source_id: str, *, force: bool, no_errata: bool) -> None:
    """Run STAGES-A's prepare() + errata fetch for one source."""
    ref = workspace.source(source_id)
    out_dir = workspace.paths.source_dir(source_id)
    if ref.pdf is None or not Path(ref.pdf).exists():
        log.warn(f"{source_id}: no PDF on this machine -- text, items, the quote gate and the "
                 f"anchor check cannot run")
        log.info(f"         once you have it: cf sources add <pdf> --for <companion>")
        return
    from sources import prepare_source

    declared = (workspace.data.get("sources") or {}).get(source_id, {}).get("declared_offset")
    payload = prepare_source.prepare(
        str(ref.pdf), str(out_dir),
        source_id=source_id,
        offset=ref.offset if ref.offset_method in {"manual", "declared"} and ref.offset_confidence
        else (declared if declared is not None else None),
        kinds=list(ref.kinds) if ref.kinds and ref.registry != "local" else None,
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
    (workspace.data.get("sources") or {}).get(source_id, {}).pop("declared_offset", None)
    log.ok(f"prepared  sources/{source_id}/   text {payload['pages']} pages, "
           f"{payload['items']} items, offset {payload['offset']} "
           f"(confidence {confidence:.3f}, {payload['offset_method']})")

    if no_errata:
        return
    url, conf, method = inference.infer_errata_url([], Registry.load(workspace).get(source_id))
    if not url:
        url, conf, method = (ref.errata_url, ref.errata_confidence or 0.5, "workspace")
    if not url:
        log.warn(f"{source_id}: no errata page found (a real erratum has been missed before)")
        return
    from sources import fetch_errata
    try:
        meta = fetch_errata.fetch(url, str(out_dir), source_id=source_id,
                                  source_sha=ref.sha256 or "", offset=payload["offset"])
    except Exception as exc:                        # noqa: BLE001 - offline is not fatal
        log.warn(f"{source_id}: errata fetch failed ({exc}); sources/{source_id}/errata.md is stale")
        return
    raw = workspace.paths.source_errata_raw(source_id)
    confirmed = inference.confirm_errata(raw.read_text(encoding="utf-8", errors="ignore")
                                         if raw.exists() else "")
    if not confirmed:
        conf = conf / 2
        log.warn(f"{source_id}: {url} does not contain the word 'errata' "
                 f"(errata_url_unconfirmed)")
    workspace.upsert_source(source_id, errata_url=url, errata_confidence=round(conf, 3))
    log.ok(f"errata    {url}  HTTP {meta['http_status']}  "
           f"{meta['entries']} entries  ({method}, confidence {conf:.2f})")


# ---------------------------------------------------------------------------
# which sources, and which one the anchors follow
# ---------------------------------------------------------------------------

def _decide_sources(workspace, repo: Path, name: str, result, args):
    """(audited ids, bundled ids, anchor id, confidence, evidence)."""
    ids = result.source_ids()
    hints = [c.anchor_hint for c in result.claims if c.anchor_hint]
    rows = _existing_rows(workspace, name)
    if args.anchor_source:
        if args.anchor_source not in ids:
            raise CfError(f"--anchor-source {args.anchor_source} is not one of the discovered "
                          f"sources ({', '.join(ids)})")
        anchor, conf, evidence = args.anchor_source, 1.0, {"reason": "--anchor-source"}
    else:
        anchor, conf, evidence = discovery.choose_anchor_source(
            repo, rows, ids, workspace=workspace, hints=hints)
    audited = [anchor] + [sid for sid in ids if sid != anchor and _is_audited(result, sid, anchor)]
    bundled = [sid for sid in ids if sid not in audited]
    return audited, bundled, anchor, conf, evidence


def _confirm_anchor(workspace, repo, name, result, args, rows, anchor, conf, evidence):
    """Re-decide the anchor edition against the rows we just extracted."""
    if args.anchor_source or not rows or len(result.source_ids()) < 2:
        return anchor, conf, evidence
    hints = [c.anchor_hint for c in result.claims if c.anchor_hint]
    try:
        winner, winner_conf, winner_evidence = discovery.choose_anchor_source(
            repo, rows, result.source_ids(), workspace=workspace, hints=hints)
    except DiscoveryError as exc:
        log.warn(f"the anchor edition could not be confirmed mechanically: {exc.message}")
        log.warn(f"keeping {anchor} (confidence {conf:.2f}); settle it with "
                 f"`cf onboard {repo} --anchor-source <id>`")
        return anchor, conf, evidence
    if winner != anchor:
        log.warn(f"the anchors agree with {winner}, not {anchor}: re-pinning the anchor edition "
                 f"and re-run `cf extract --companion {name}`")
    return winner, winner_conf, winner_evidence


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


def _is_audited(result, source_id: str, anchor: str) -> bool:
    """A sibling edition of the anchor is a REFERENCE, not a second audit source.

    ``same_work`` is the test: two editions of one book are not two sources to
    audit against -- one of them is the pagination the repo follows and the other
    is prepared only so the page map between them can be checked.
    """
    body = result.get(source_id) or {}
    anchor_body = result.get(anchor) or {}
    siblings = set(body.get("same_work") or []) | set(anchor_body.get("same_work") or [])
    return not (source_id in siblings or anchor in (body.get("same_work") or []))


def _existing_rows(workspace, name: str) -> list[dict]:
    """statements.jsonl from a previous run, for the mechanical anchor check."""
    from cflib.jsonio import read_jsonl
    path = workspace.paths.statements_jsonl(name)
    return read_jsonl(path) if path.exists() else []


# ---------------------------------------------------------------------------
# adapter.yaml, dry run, extraction
# ---------------------------------------------------------------------------

def _write_adapter_yaml(workspace, name: str, repo: Path, kind: str, commit, sources,
                        anchor: str, args) -> Path:
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
        "source_map": "\n".join(f"    # - {{source: {sid}, files: [\"...\"]}}" for sid in sources),
    }, allow_missing=True)
    atomic_write(adapter_yaml, text)
    log.ok(f"wrote {adapter_yaml}")
    return adapter_yaml


def _yaml_str(value: str) -> str:
    return '"' + str(value).replace('"', '\\"') + '"' if value else '""'


def _commit(repo: Path) -> str | None:
    res = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else None


class _SourceView:
    """What an adapter needs to know about the ONE source it is extracting for.

    The shipped adapters still read ``kinds`` / ``offset`` / ``book_sha`` off the
    object they are handed.  A Workspace cannot answer those without being told
    WHICH source, so onboarding hands them this view of a single source instead.
    The ADAPTERS package replaces it with ``Adapter.extract(sources=...)``.
    """

    def __init__(self, workspace, source_id: str):
        self.workspace = workspace
        self.source_id = source_id
        ref = workspace.source(source_id)
        self.kinds = tuple(ref.kinds)
        self.offset = ref.offset
        self.book_sha = ref.sha256
        self.source_sha = ref.sha256
        self.root = workspace.root
        self.paths = workspace.paths


def _dry_run(adapter_yaml: Path, repo: Path, workspace, source_id: str):
    """Run the adapter's parser without writing a statement table."""
    try:
        from cflib.adapters_api import load as load_adapter
        adapter = load_adapter(adapter_yaml, workspace=_SourceView(workspace, source_id))
        counts = dict(adapter.dry_run(repo))
    except AdapterError as exc:
        log.warn(str(exc))
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - a parser bug must not lose the files we wrote
        log.warn(f"dry run failed: {exc.__class__.__name__}: {exc}")
        return None, f"{exc.__class__.__name__}: {exc}"
    missing = [k for k in DRY_RUN_REQUIRED_KEYS if k not in counts]
    if missing:
        log.warn(f"adapter dry_run() omitted {', '.join(missing)}")
    return counts, None


def _extract(workspace, name: str, adapter_yaml: Path, repo: Path, sources, anchor: str) -> list:
    """statements.jsonl, every row stamped with its source_id and source_sha."""
    try:
        from cflib.adapters_api import load as load_adapter
        view = _SourceView(workspace, anchor)
        adapter = load_adapter(adapter_yaml, workspace=view)
        try:
            produced = adapter.extract(repo, sources={anchor: view.source_sha})
        except TypeError:
            # the shipped adapters still take extract(repo, book_sha); the
            # ADAPTERS package unifies this on the S8 signature.
            produced = adapter.extract(repo, view.source_sha)
        rows = []
        for row in produced:
            row = dict(row)
            row["source_id"] = row.get("source_id") or anchor
            row["source_sha"] = row.pop("book_sha", None) or view.source_sha
            rows.append(row)
    except Exception as exc:  # noqa: BLE001
        log.warn(f"extraction failed: {exc.__class__.__name__}: {exc}")
        return []
    path = workspace.paths.statements_jsonl(name)
    write_jsonl(path, rows, meta={
        "companion": name, "repo": str(repo), "sources": list(sources),
        "anchor_source": anchor, "rows": len(rows), "at": state_mod.now(),
    })
    log.ok(f"extracted {path.relative_to(workspace.root)}   {len(rows)} rows")
    with state_mod.update(workspace, name) as st:
        state_mod.set_section(st, "extract", {"rows": len(rows),
                                              "items": len({r.get("item") for r in rows})})
        state_mod.source_section(st, anchor, {"sha": workspace.source(anchor).sha256,
                                              "rows": len(rows),
                                              "items": len({r.get("item") for r in rows})})
    _infer_scope(workspace, anchor, rows)
    return rows


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
            log.warn(f"{len(rejects)} item tags rejected by the strict grammar; first few:")
            for r in rejects[:5]:
                log.info(f"    {r.get('raw')!r}  ({r.get('reason')})")
    elif problem and problem != "skipped":
        log.warn(f"no counts recorded: {problem}")
    unconfirmed = [s.id for s in workspace.unconfirmed_sources() if s.id in sources]
    log.info("")
    log.info("next:")
    log.info(f"  edit {conventions} and have the repo owner confirm it")
    for sid in unconfirmed:
        log.info(f"  cf sources show {sid}     # nobody has checked this source yet")
    log.info("  cf onboard <the other companion repo>   # then `cf pair`")
