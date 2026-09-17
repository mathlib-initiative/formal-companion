"""cf sources -- list, show, add, set and refresh the workspace's sources.

docs/design/multi-source-model.md S7.  `cf onboard` is the path; these are the
exceptions:

    cf sources list                       one row per source
    cf sources show <id>                  the full record and its provenance
    cf sources add <pdf> --for <name>     add one by hand (fingerprint + prepare)
    cf sources set <id> offset=16 ...     override a field discovery got wrong
    cf sources refresh [<id>]             re-prepare text / items / errata

``cf sources set`` marks what it touches: an overridden offset gets
``offset_method: manual``, an overridden scope gets ``scope_method: manual``, and
the override is recorded in the source's ``meta.json`` -- so a number a human
typed is never mistaken later for one the text supports.

(This command replaces ``cf book``: there is no single book any more.)
"""
from __future__ import annotations

import json
from pathlib import Path

from cflib import inference, log
from cflib.errors import CfError, SourceError
from cflib.hashing import short
from cflib.jsonio import read_json, write_json
from cflib.log import format_table
from cflib.registry import Registry, entry_from_claim, source_id_for
from cflib.workspace import ROLE_AUDITED, ROLE_REFERENCE, from_args

LIST_COLUMNS = ["source", "sha", "edition", "offset", "conf", "kinds", "items",
                "errata", "companions", "role", "confirmed"]

#: ``cf sources set`` keys, and how to read the value off the command line.
SETTABLE = {
    "offset": int,
    "errata_url": str,
    "download_url": str,
    "book_page_url": str,
    "title": str,
    "edition": str,
    "isbn": str,
    "sha256": str,
    "role": str,
    "confirmed": lambda v: str(v).lower() in {"1", "true", "yes", "on"},
    "kinds": lambda v: [k.strip() for k in v.split(",") if k.strip()],
    "chapters": lambda v: [int(c.strip()) for c in v.split(",") if c.strip()],
    "pdf": str,
}


def register(subparsers) -> None:
    p = subparsers.add_parser("sources", help="list, show, add, set and refresh sources")
    sub = p.add_subparsers(dest="sources_command", metavar="<subcommand>")

    sub.add_parser("list", help="one row per source")

    show = sub.add_parser("show", help="the full record of one source")
    show.add_argument("source_id")
    show.add_argument("--json", action="store_true", dest="as_json")

    add = sub.add_parser("add", help="add a source by hand (the exception, not the path)")
    add.add_argument("pdf", help="path to the PDF (never copied into the skill repo)")
    add.add_argument("--for", dest="companion", required=True,
                     help="the companion this source is audited against")
    add.add_argument("--anchor", action="store_true",
                     help="this companion's page anchors follow THIS edition")
    add.add_argument("--role", default=ROLE_AUDITED, choices=[ROLE_AUDITED, ROLE_REFERENCE])
    add.add_argument("--title", default="", help="only needed when no registry entry matches")
    add.add_argument("--edition", default="", help="only needed when no registry entry matches")
    add.add_argument("--no-prepare", action="store_true")

    st = sub.add_parser("set", help="override a discovered field")
    st.add_argument("source_id")
    st.add_argument("assignments", nargs="+", metavar="key=value",
                    help="offset=16 errata_url=... chapters=1,2 kinds=A,B confirmed=true")

    refresh = sub.add_parser("refresh", help="re-prepare text, page map, items and errata")
    refresh.add_argument("source_id", nargs="?", help="default: every source")
    refresh.add_argument("--force", action="store_true")
    refresh.add_argument("--images", action="store_true", help="also render page images (local only)")
    refresh.add_argument("--image-pages", default="", help="PDF pages to render, e.g. 20-40,55")
    refresh.add_argument("--dpi", type=int, default=150)
    refresh.add_argument("--no-errata", action="store_true")


def run(args) -> int:
    workspace = from_args(args)
    command = getattr(args, "sources_command", None) or "list"
    return {
        "list": _list, "show": _show, "add": _add, "set": _set, "refresh": _refresh,
    }[command](workspace, args)


# ---------------------------------------------------------------------------

def _list(workspace, args) -> int:
    sources = workspace.sources
    if not sources:
        log.info("no sources yet:")
        log.info("  cf onboard <path to a companion repo>")
        return 0
    rows = []
    for sid, ref in sources.items():
        items = _item_count(workspace, sid)
        rows.append([
            sid,
            short(ref.sha256, 8) if ref.sha256 else "-",
            (ref.edition or "")[:34],
            ref.offset,
            f"{ref.offset_confidence:.2f}",
            len(ref.kinds),
            items if items is not None else "-",
            "yes" if workspace.paths.source_errata_md(sid).exists() else "no",
            ",".join(workspace.companions_of(sid)) or "-",
            ref.role,
            "yes" if ref.confirmed else "NO",
        ])
    log.info(format_table(LIST_COLUMNS, rows))
    shared = workspace.shared_sources()
    log.info("")
    if shared:
        for sid, names in shared.items():
            log.info(f"differential possible on {sid}: {', '.join(names)}")
    else:
        log.info("no source is audited by two companions yet, so no differential is possible")
    return 0


def _show(workspace, args) -> int:
    ref = workspace.source(args.source_id)
    meta = read_json(workspace.paths.source_meta(ref.id), default={}) or {}
    if getattr(args, "as_json", False):
        body = dict((workspace.data.get("sources") or {}).get(ref.id) or {})
        print(json.dumps({"id": ref.id, "source": body, "meta": meta,
                          "companions": workspace.companions_of(ref.id)},
                         indent=2, ensure_ascii=False))
        return 0
    log.info(f"source    : {ref.id}")
    log.info(f"title     : {ref.title}" + (f" -- {', '.join(ref.authors)}" if ref.authors else ""))
    log.info(f"edition   : {ref.edition}")
    log.info(f"sha256    : {ref.sha256 or '(not located)'}"
             + (f"   {ref.bytes} bytes" if ref.bytes else ""))
    log.info(f"pdf       : {ref.pdf or '(not on this machine)'}")
    log.info(f"offset    : {ref.offset}   confidence {ref.offset_confidence:.4f} "
             f"({ref.offset_method})   printed = pdf_page - {ref.offset}")
    log.info(f"kinds     : {', '.join(ref.kinds)}")
    log.info(f"chapters  : {list(ref.chapters) or '(whole source)'}   ({ref.scope_method})")
    log.info(f"errata    : {ref.errata_url or '(none found)'}   "
             f"confidence {ref.errata_confidence:.2f}")
    log.info(f"licence   : {ref.licence.get('note') or '(unrecorded)'}")
    log.info(f"            fetch: {ref.licence.get('fetch', 'none')}   redistribute: false")
    log.info(f"role      : {ref.role}    registry: {ref.registry}    "
             f"confirmed: {'yes' if ref.confirmed else 'NO'}")
    log.info(f"companions: {', '.join(workspace.companions_of(ref.id)) or '(none)'}")
    for other in ref.same_work:
        shift = ref.shift_to(other)
        log.info(f"same work : {other}" + (f"   printed pages shift by {shift:+d} by default"
                                           if shift is not None else ""))
        note = (ref.page_map_to.get(other) or {}).get("note")
        if note:
            log.info(f"            {note.strip()}")
    if ref.discovered:
        log.info(f"discovered: {ref.discovered.get('at', '')}  "
                 f"pdf found in: {ref.discovered.get('pdf_found_in')}")
        for src in ref.discovered.get("from") or []:
            log.info(f"            from {src}")
    prepared = workspace.paths.source_dir(ref.id)
    log.info("")
    log.info(f"prepared  : {prepared}")
    if meta:
        log.info(f"            text {meta.get('pages')} pages, {meta.get('items')} items, "
                 f"text sha {short(meta.get('text_sha256') or '', 8)}")
        for warning in meta.get("warnings") or []:
            log.warn(f"            {warning}")
    else:
        log.warn(f"            not prepared -- run `cf sources refresh {ref.id}`")
    if not ref.confirmed:
        log.info("")
        log.warn("nobody has checked this source. Confirm it with:")
        log.info(f"  cf sources set {ref.id} confirmed=true")
        log.info("  or promote it into the skill's registry/sources.yaml")
    return 0


def _add(workspace, args) -> int:
    from cflib import discovery
    pdf = Path(args.pdf).expanduser().resolve()
    if not pdf.is_file():
        raise CfError(f"{pdf} is not a file")
    companion = workspace.companion(args.companion)          # raises when unknown
    fp = discovery.fingerprint(pdf)
    registry = Registry.load(workspace)
    entry = registry.by_sha(fp["sha256"])
    existing = workspace.source_by_sha(fp["sha256"])
    if existing is not None:
        source_id = existing.id
        log.info(f"{pdf.name} is already source {source_id} ({short(fp['sha256'], 8)})")
    elif entry is not None:
        source_id = entry.id
        log.ok(f"{pdf.name} is a known source: {source_id} ({entry.edition})")
        fields = entry.to_source_fields()
        fields.update({"pdf": str(pdf), "bytes": fp["bytes"], "pages": fp["pages"],
                       "text_sha256": fp["text_sha256"], "role": args.role,
                       "discovered": {"at": _now(), "from": ["cf sources add"],
                                      "pdf_found_in": "manual"}})
        workspace.upsert_source(source_id, **fields)
    else:
        title = args.title or pdf.stem.replace("-", " ").replace("_", " ")
        if not args.title:
            log.warn(f"no registry entry for {short(fp['sha256'], 8)}; using --title/--edition "
                     "(pass them to get a stable id)")
        source_id = source_id_for(title, args.edition, (), sha256=fp["sha256"],
                                  taken=set(workspace.sources))
        claim = discovery.SourceClaim(signal="filesystem", path=str(pdf), title=title,
                                      edition=args.edition, sha256=fp["sha256"])
        located = discovery.LocatedPdf(path=pdf, sha256=fp["sha256"], bytes=fp["bytes"],
                                       pages=fp["pages"], text_sha256=fp["text_sha256"],
                                       where="manual", rank=0)
        path = registry.append_overlay(workspace, entry_from_claim(source_id, claim, pdf=located))
        log.warn(f"new source {source_id}: appended to {path.name} with confirmed: false")
        workspace.upsert_source(
            source_id, sha256=fp["sha256"], title=title, edition=args.edition or "unspecified",
            bytes=fp["bytes"], pages=fp["pages"], text_sha256=fp["text_sha256"], pdf=str(pdf),
            role=args.role, confirmed=False, registry="overlay",
            licence={"note": "", "fetch": "none", "redistribute": False},
            discovered={"at": _now(), "from": ["cf sources add"], "pdf_found_in": "manual"})

    sources = list(companion.sources)
    if source_id not in sources and args.role == ROLE_AUDITED:
        sources.append(source_id)
    bundled = list(companion.bundled_sources)
    if args.role == ROLE_REFERENCE and source_id not in bundled:
        bundled.append(source_id)
    anchor = source_id if args.anchor else companion.anchor_source
    workspace.upsert_companion(args.companion, sources=sources, bundled_sources=bundled,
                               anchor_source=anchor)
    workspace.save()
    log.ok(f"{args.companion}: sources = {', '.join(sources)}   anchor = {anchor}")
    if not args.no_prepare:
        from cfcmd.onboard import _prepare_source
        _prepare_source(workspace, source_id, force=False, no_errata=False)
        workspace.save()
    return 0


def _set(workspace, args) -> int:
    ref = workspace.source(args.source_id)
    changes: dict = {}
    for assignment in args.assignments:
        if "=" not in assignment:
            raise CfError(f"expected key=value, got {assignment!r}",
                          hint="settable: " + ", ".join(sorted(SETTABLE)))
        key, _, raw = assignment.partition("=")
        key = key.strip()
        if key not in SETTABLE:
            raise CfError(f"{key!r} is not settable",
                          hint="settable: " + ", ".join(sorted(SETTABLE)))
        try:
            changes[key] = SETTABLE[key](raw.strip())
        except ValueError as exc:
            raise CfError(f"{assignment}: {exc}") from exc
    # a human typing a number must never be mistaken for a number the text supports
    if "offset" in changes:
        changes["offset_method"] = "manual"
        changes["offset_confidence"] = 1.0
    if "chapters" in changes:
        changes["scope_method"] = "manual"
    if "role" in changes and changes["role"] not in {ROLE_AUDITED, ROLE_REFERENCE}:
        raise CfError(f"role must be {ROLE_AUDITED} or {ROLE_REFERENCE}")
    workspace.upsert_source(ref.id, **changes)
    workspace.save()
    for key, value in changes.items():
        log.ok(f"{ref.id}.{key} = {value}")
    meta_path = workspace.paths.source_meta(ref.id)
    if meta_path.exists():
        meta = read_json(meta_path, default={}) or {}
        meta.setdefault("overrides", []).append({"at": _now(), "changes": changes})
        write_json(meta_path, meta)
        log.detail(f"recorded the override in {meta_path}")
    if "offset" in changes or "kinds" in changes:
        log.info(f"re-prepare so items.json follows: cf sources refresh {ref.id} --force")
    return 0


def _refresh(workspace, args) -> int:
    from cfcmd.onboard import _prepare_source
    ids = [args.source_id] if args.source_id else workspace.source_ids()
    if not ids:
        raise SourceError("this workspace has no sources",
                          hint="run `cf onboard <repo>`")
    for source_id in ids:
        ref = workspace.source(source_id)
        _prepare_source(workspace, source_id, force=args.force, no_errata=args.no_errata)
        if args.images and ref.pdf:
            from sources import render_pages
            meta = read_json(workspace.paths.source_meta(source_id), default={}) or {}
            pages = _parse_pages(args.image_pages) or list(range(1, int(meta.get("pages") or 0) + 1))
            manifest = render_pages.render(str(ref.pdf), str(workspace.paths.source_dir(source_id)),
                                           pages, dpi=args.dpi, force=args.force)
            log.ok(f"images: {len(manifest['written'])} rendered, {len(manifest['present'])} present "
                   "(local only, gitignored)")
    workspace.save()
    return 0


# ---------------------------------------------------------------------------

def _item_count(workspace, source_id: str):
    data = read_json(workspace.paths.source_items(source_id), default={}) or {}
    return data.get("count")


def _parse_pages(spec: str) -> list[int]:
    pages: list[int] = []
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            pages.extend(range(int(a), int(b) + 1))
        else:
            pages.append(int(chunk))
    return pages


def _now() -> str:
    from cflib.state import now
    return now()
