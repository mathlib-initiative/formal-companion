"""cf sources -- list, show, add, set and refresh the workspace's sources.

docs/design/multi-source-model.md S7.  `cf onboard` is the path; these are the
exceptions:

    cf sources list                       one row per source
    cf sources show <id>                  the full record and its provenance
    cf sources add <source> --for <name>  add one by hand (fingerprint + prepare)
    cf sources set <id> offset=16 ...     override a field discovery got wrong
    cf sources remove <id>                drop one that should not be here
    cf sources refresh [<id>]             re-prepare text / items / errata

``cf sources set`` marks what it touches: an overridden offset gets
``offset_method: manual``, an overridden scope gets ``scope_method: manual``, an
overridden numbering depth gets ``numbering_method: manual``, an accepted small
table gets ``completeness_method: manual``, and the override is recorded in the
source's ``meta.json`` -- so a number a human typed is never mistaken later for
one the text supports.

A source is a PDF or a manuscript that is already text (``.tex``, ``.md``,
``.rst``, ``.html``, a ``pdftotext`` dump): see ``scripts/sources/locator.py``.
A text with no page breaks is UNPAGINATED, and the page-dependent parts of this
command (``offset=``) refuse on it by name rather than invent a page.
``--images`` refuses on any manuscript, paginated or not: page images come from
the PDF, and form feeds in a text dump are not one.

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
from sources.locator import text_suffixes_help

LIST_COLUMNS =["source", "sha", "edition", "offset", "conf", "kinds", "items",
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
    # the escape hatch for the item-completeness refusal: a table that really is
    # this small (a booklet, one chapter of a reader) stays adoptable, and says
    # who vouched for it -- `completeness_method: manual`.
    "completeness_ok": lambda v: str(v).lower() in {"1", "true", "yes", "on"},
    # the escape hatch for `too_many_segments`: the numbering is inferred from
    # the source's own headings, so a book whose four-level labels are too few
    # to clear MIN_DEPTH_SHARE drops them as rejects and re-inferring cannot
    # change that.  `numbering=3,4` says the book really numbers this deep.
    "numbering": lambda v: _numbering_value(v),
    "kinds": lambda v: [k.strip() for k in v.split(",") if k.strip()],
    "chapters": lambda v: [int(c.strip()) for c in v.split(",") if c.strip()],
    "pdf": str,
}

#: the deepest numbering the item grammar carries (`cflib.items.MAX_NUMBER_SEGMENTS`)
MAX_NUMBERING = 4


def _numbering_value(raw: str) -> list[int]:
    """``"3,4"`` -> ``[3, 4]``; ``"4"`` -> ``[4, 4]``. Raises on anything else.

    Typed by a human, so it is checked here rather than clamped on the way in:
    a silently clamped depth would put the same headings back on the reject list
    and leave the user reading the same refusal.
    """
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    if not 1 <= len(parts) <= 2 or not all(p.isdigit() for p in parts):
        raise ValueError("expected <min>,<max> numeric segments, e.g. 3,4")
    low = int(parts[0])
    high = int(parts[-1])
    if not 1 <= low <= high <= MAX_NUMBERING:
        raise ValueError(f"numeric segments must run 1..{MAX_NUMBERING} with min <= max")
    return [low, high]


def register(subparsers) -> None:
    p = subparsers.add_parser("sources", help="list, show, add, set and refresh sources")
    sub = p.add_subparsers(dest="sources_command", metavar="<subcommand>")

    sub.add_parser("list", help="one row per source")

    show = sub.add_parser("show", help="the full record of one source")
    show.add_argument("source_id")
    show.add_argument("--json", action="store_true", dest="as_json")

    add = sub.add_parser("add", help="add a source by hand (the exception, not the path)")
    # the accepted set is read off `locator.TEXT_SUFFIXES` rather than retyped:
    # a help string that advertised fewer formats than the code accepts is how a
    # suffix with no heading pattern got in unnoticed.
    add.add_argument("source", help="path to the source: a PDF, or a manuscript that is "
                                    "already text (%s) -- never copied into the skill repo"
                                    % text_suffixes_help())
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
                    help="offset=16 errata_url=... chapters=1,2 kinds=A,B numbering=3,4 "
                         "confirmed=true completeness_ok=true")

    rm = sub.add_parser("remove", help="drop a source from this workspace")
    rm.add_argument("source_id")
    rm.add_argument("--keep-files", action="store_true",
                    help="leave sources/<id>/ on disk (the default deletes the prepared text)")

    refresh = sub.add_parser("refresh", help="re-prepare text, page map, items and errata")
    refresh.add_argument("source_id", nargs="?", help="default: every source")
    refresh.add_argument("--force", action="store_true")
    refresh.add_argument("--images", action="store_true",
                         help="also render page images (local only; needs the PDF itself, so it "
                              "refuses on a text manuscript)")
    refresh.add_argument("--image-pages", default="", help="PDF pages to render, e.g. 20-40,55")
    refresh.add_argument("--dpi", type=int, default=150)
    refresh.add_argument("--no-errata", action="store_true")


def run(args) -> int:
    command = getattr(args, "sources_command", None) or "list"
    # `remove` is the repair path: it must load a workspace whose own validator
    # rejects it, because that is precisely when a source has to come out.
    workspace = from_args(args, validate=(command != "remove"))
    return {
        "list": _list, "show": _show, "add": _add, "set": _set, "remove": _remove,
        "refresh": _refresh,
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
        # an unpaginated source has no printed page, so `offset 0  conf 1.00`
        # would state the same relation `cf sources show` refuses to print.
        paginated = _is_paginated(workspace, sid)
        rows.append([
            sid,
            short(ref.sha256, 8) if ref.sha256 else "-",
            (ref.edition or "")[:34],
            ref.offset if paginated else "-",
            f"{ref.offset_confidence:.2f}" if paginated else "-",
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
    log.info(f"file      : {ref.pdf or '(not on this machine)'}")
    # an unpaginated source has no printed page, so printing the rule would state
    # a relation that does not hold for it (`printed = pdf_page - 0`).
    if (meta.get("pagination") or "formfeed") == "none":
        log.info(f"offset    : (none)   this source is unpaginated; items are located by "
                 f"{meta.get('locator', 'section')}")
    else:
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
        numbering = meta.get("numbering") or {}
        if numbering:
            log.info(f"            numbering {numbering.get('min_segments')}-"
                     f"{numbering.get('max_segments')} numeric segments "
                     f"({numbering.get('method')}, confidence "
                     f"{float(numbering.get('confidence') or 0):.2f})")
        if meta.get("item_completeness") is not None:
            log.info(f"            completeness {float(meta['item_completeness']):.3f} of the "
                     f"numbering's own slots   locator {meta.get('locator')}   "
                     f"pagination {meta.get('pagination')}")
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
    pdf = Path(args.source).expanduser().resolve()
    if not pdf.is_file():
        raise CfError(f"{pdf} is not a file")
    companion = workspace.companion(args.companion)          # raises when unknown
    fp = _fingerprint(pdf)
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
                       "source_format": fp["source_format"], "pagination": fp["pagination"],
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
            source_format=fp["source_format"], pagination=fp["pagination"],
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
        # a PDF a human named by hand is as confirmed as a source gets, so its
        # errata URL may be fetched -- but only when the URL itself is confirmed
        # too, which `_errata_provenance` decides.
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
        # an unpaginated source has no printed page for an offset to shift: the
        # override is refused by name rather than stored where nothing reads it.
        _require_pagination(workspace, ref.id, "a page offset")
        changes["offset_method"] = "manual"
        changes["offset_confidence"] = 1.0
    if "chapters" in changes:
        changes["scope_method"] = "manual"
    if "completeness_ok" in changes:
        changes["completeness_method"] = "manual"
    if "numbering" in changes:
        changes["numbering_method"] = "manual"
    # the same rule for the errata URL: a URL a human typed is CONFIRMED, and
    # only a confirmed URL is fetched without being asked (a repo-supplied one
    # is an outbound request the repo chose, and text that reaches prompts).
    if "errata_url" in changes:
        changes["errata_method"] = "manual"
        changes["errata_confidence"] = 1.0
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
    if {"offset", "kinds", "numbering"} & set(changes):
        log.info(f"re-prepare so items.json follows: cf sources refresh {ref.id}")
    return 0


def _remove(workspace, args) -> int:
    """Drop a source from workspace.yaml, the overlay and (by default) the disk.

    The recovery command.  A workspace that names one PDF under two source ids
    could not be loaded at all, so there was no way to undo it but to hand-edit
    two YAML files; this undoes it, and says what it took out.
    """
    source_id = args.source_id
    table = (workspace.data.get("sources") or {})
    if source_id not in table:
        raise SourceError(f"unknown source {source_id!r}",
                          hint="known: " + (", ".join(sorted(table)) or "(none)"))
    body = dict(table[source_id] or {})
    users = [name for name, comp in (workspace.data.get("companions") or {}).items()
             if isinstance(comp, dict)
             and source_id in ((comp.get("sources") or []) + (comp.get("bundled_sources") or []))]
    orphaned = workspace.remove_source(source_id)
    repos = {name: str((workspace.data.get("companions") or {}).get(name, {}).get("repo") or "")
             for name in orphaned}
    for name in orphaned:
        # a companion with no source cannot be audited, and a workspace that
        # holds one cannot be loaded -- so the repair takes it out too, and says
        # the one command that brings it back.
        (workspace.data.get("companions") or {}).pop(name, None)
    workspace.save()
    log.ok(f"removed sources.{source_id}"
           + (f" (sha {short(str(body.get('sha256') or ''), 8)})" if body.get("sha256") else ""))
    for name in users:
        if name not in orphaned:
            log.warn(f"{name} no longer lists {source_id}; re-run "
                     f"`cf onboard <repo> --force` if that companion needs another source")
    for name in orphaned:
        log.warn(f"{name} had no other source, so it was removed too")
        log.info(f"  bring it back with:  cf onboard {repos.get(name) or '<repo>'}")
    _drop_overlay_row(workspace, source_id)
    prepared = workspace.paths.source_dir(source_id)
    if prepared.exists() and not args.keep_files:
        import shutil
        shutil.rmtree(prepared, ignore_errors=True)
        log.ok(f"removed {prepared}")
    elif prepared.exists():
        log.info(f"{prepared} left on disk (--keep-files)")
    return 0


def _drop_overlay_row(workspace, source_id: str) -> None:
    """Take the id out of the workspace's registry overlay, if it is there."""
    from cflib.registry import OVERLAY_FILE, REGISTRY_VERSION
    from cflib.jsonio import atomic_write
    from cflib.yamlio import dump_yaml, read_yaml
    path = workspace.root / OVERLAY_FILE
    if not path.exists():
        return
    data = read_yaml(path)
    if not isinstance(data, dict) or source_id not in (data.get("sources") or {}):
        return
    data["sources"].pop(source_id, None)
    data.setdefault("version", REGISTRY_VERSION)
    header = (
        "# registry.yaml -- this WORKSPACE's overlay on the skill's source registry.\n"
        "# Written by `cf onboard` / `cf sources add`, always with confirmed: false.\n"
        "# Metadata only -- no source text, ever. Promote an entry by copying it into\n"
        "# the skill's registry/sources.yaml (confirmed: true) and deleting it here.\n"
    )
    atomic_write(path, header + dump_yaml(data))
    log.ok(f"removed {source_id} from {path.name}")


def _refresh(workspace, args) -> int:
    from cfcmd.onboard import _prepare_source
    ids = [args.source_id] if args.source_id else workspace.source_ids()
    if not ids:
        raise SourceError("this workspace has no sources",
                          hint="run `cf onboard <repo>`")
    for source_id in ids:
        ref = workspace.source(source_id)
        if args.images:
            # pdftoppm renders PAGES *of a PDF*, and the two conditions are not
            # the same one: a pdftotext dump kept as .txt has form feeds, so it
            # is paginated, and handing its bytes to pdftoppm raised
            # "Couldn't read xref table" as a traceback.
            _require_pagination(workspace, source_id, "page images (--images)")
            _require_pdf(workspace, source_id, "page images (--images)")
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

def _require_pagination(workspace, source_id: str, feature: str) -> None:
    """Refuse a page-dependent feature on an unpaginated source.

    The prepared ``meta.json`` is what knows -- it read the text -- and the
    workspace row is the fallback for a source `cf sources add` recorded but
    nothing has prepared yet.
    """
    from sources.locator import require_pagination
    meta = read_json(workspace.paths.source_meta(source_id), default={}) or {}
    body = (workspace.data.get("sources") or {}).get(source_id) or {}
    pagination = meta.get("pagination") or body.get("pagination")
    require_pagination({"pagination": pagination} if pagination else {}, feature,
                       source_id=source_id)


def _require_pdf(workspace, source_id: str, feature: str) -> None:
    """Refuse a feature that needs the PDF itself on a text manuscript.

    Same three places to ask as the pagination guard, in the same order: the
    prepared ``meta.json`` read the file, the workspace row recorded what
    `cf sources add` was given, and the path on disk is the last word for a
    source neither of them describes.
    """
    from sources.locator import require_pdf
    meta = read_json(workspace.paths.source_meta(source_id), default={}) or {}
    body = (workspace.data.get("sources") or {}).get(source_id) or {}
    fmt = meta.get("source_format") or body.get("source_format") or body.get("pdf") or ""
    require_pdf(fmt, feature, source_id=source_id)


def _fingerprint(path: Path) -> dict:
    """``discovery.fingerprint`` for a PDF; the same shape for a text manuscript.

    A text source's identity is the sha256 of its BYTES, exactly as a PDF's is,
    and ``text_sha256`` is the same poppler tripwire -- for a manuscript the two
    differ only by the utf-8 decode, which is the honest answer: nothing was
    extracted.  ``pages`` counts ``\\f`` page breaks (a ``pdftotext`` dump keeps
    them) and is ``None`` when there are none, which is what makes the source
    unpaginated.
    """
    from cflib import discovery
    from cflib.hashing import sha256_file, sha256_text
    from sources.locator import FORMAT_PDF, PAGINATION_FORMFEED, PAGINATION_NONE
    from sources.locator import detect_pagination, source_format
    try:
        fmt = source_format(str(path))
    except ValueError as exc:
        raise CfError(str(exc),
                      hint="give the PDF, or the manuscript itself (%s)"
                           % text_suffixes_help()) from exc
    if fmt == FORMAT_PDF:
        data = discovery.fingerprint(path)
        data["source_format"] = FORMAT_PDF
        data["pagination"] = PAGINATION_FORMFEED
        return data
    text = path.read_text(encoding="utf-8", errors="ignore")
    pagination = detect_pagination(text)
    pages = text.count("\f") if pagination == PAGINATION_FORMFEED else None
    return {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size,
            "pages": pages, "text_sha256": sha256_text(text), "header": text[:8],
            "text": text, "source_format": fmt,
            "pagination": pagination or PAGINATION_NONE}


def _item_count(workspace, source_id: str):
    data = read_json(workspace.paths.source_items(source_id), default={}) or {}
    return data.get("count")


def _is_paginated(workspace, source_id: str) -> bool:
    """Does this source have printed pages? (prepared meta first, then the row)."""
    from sources.locator import is_paginated
    meta = read_json(workspace.paths.source_meta(source_id), default={}) or {}
    body = (workspace.data.get("sources") or {}).get(source_id) or {}
    pagination = meta.get("pagination") or body.get("pagination")
    return is_paginated({"pagination": pagination} if pagination else {})


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
