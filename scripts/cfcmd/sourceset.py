"""cf sourceset -- create, fill, fetch, render and inspect a sourceset of web sources.

New code, implementing stage L1 of docs/proposals/wikipedia-locate.md section 3
(sources: enumerate the corpus, adopt consumer pins and bytes, fetch the rest
once, render) over the sourceset layout of references/locate-documents.md
section 7:

    cf sourceset create wpm --family enwiki
    cf sourceset create wpm2 --from wpm          # the same sources and bytes, no layers
    cf sourceset enumerate-wikiproject wpm --project Mathematics --execute
    cf sourceset adopt wpm --from pins.jsonl --consumer wikilean [--execute]
    cf sourceset fetch wpm --limit 200 --execute
    cf sourceset fetch wpm --select pilot.txt --execute   # only the sources it names
    cf sourceset render wpm
    cf sourceset status wpm --verify

Nothing reaches the network without ``--execute`` (``--fixtures <dir>`` replays
recorded responses instead): without either, ``enumerate-wikiproject`` and
``fetch`` print the first request they would make and stop.  Every write is a
new manifest version, append-only; every command refreshes the sourceset's
``state.json``.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from cflib import log
from cflib.errors import CfError
from cflib.jsonio import iter_jsonl, write_json
from cflib.workspace import from_args
from sources import render_html, websource, wikinet
from sources.sourceset import Sourceset, is_adopted

#: how many fetched sources a version records at most, so a killed fetch loses
#: at most this many manifest updates (the bytes on disk are reused next time)
DEFAULT_CHECKPOINT = 200
#: a fetch stops after this many sources in a row that Wikipedia refused or
#: rate-limited, instead of sending the rest of the list into the limiter
MAX_UNAVAILABLE = 3


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "sourceset", help="create, enumerate, adopt, fetch, render and inspect a sourceset "
                          "of web sources (locate)")
    sub = p.add_subparsers(dest="sourceset_command", metavar="<subcommand>")

    create = sub.add_parser("create", help="a new sourceset: manifest-1 with no sources")
    create.add_argument("sourceset_id")
    create.add_argument("--family", default=None,
                        help="the web-source family in the registry (default: enwiki, or the "
                             "family of --from)")
    create.add_argument("--from", dest="seed", default=None, metavar="SOURCESET",
                        help="seed it with every source of another sourceset -- identity, "
                             "metadata and bytes, but no layer -- so `render` renders them "
                             "under the current renderer (a recorded layer never changes)")
    create.add_argument("--from-version", dest="seed_version", type=int, default=None,
                        metavar="N", help="seed from version N of --from (default: its latest)")

    enum = sub.add_parser("enumerate-wikiproject",
                          help="add a WikiProject's articles at their latest revisions")
    enum.add_argument("sourceset_id")
    enum.add_argument("--project", required=True, help="the WikiProject, e.g. Mathematics")
    enum.add_argument("--class", dest="classes", action="append", default=[], metavar="C",
                      help="keep only this assessment class (repeatable; case-insensitive)")
    enum.add_argument("--importance", action="append", default=[], metavar="I",
                      help="keep only this importance (repeatable; top, high, mid, low, ...)")
    enum.add_argument("--limit", type=int, default=None, help="add at most N pages")
    enum.add_argument("--include-adopted", action="store_true",
                      help="also add the latest revision of a page a consumer pinned (by "
                           "default such a page keeps only its adopted revision)")
    _network_arguments(enum)

    adopt = sub.add_parser("adopt", help="adopt a consumer's pinned revisions (and their bytes)")
    adopt.add_argument("sourceset_id")
    adopt.add_argument("--from", dest="pins", required=True, metavar="ROWS",
                       help="JSON lines {revision, pageid | title, wikidata?, assessment?, "
                            "bytes?: <path>}; a relative bytes path is relative to this file; "
                            "a pin with no bytes is fetched later, one with no pageid has its "
                            "page resolved from its revision (--execute)")
    adopt.add_argument("--consumer", default="consumer",
                       help="who pinned them, recorded in adopted_from")
    adopt.add_argument("--only-known-pages", action="store_true",
                       help="skip (and list) a pin whose page has no other source in this "
                            "sourceset, e.g. a page outside the enumerated WikiProject")
    _network_arguments(adopt)

    fetch = sub.add_parser("fetch", help="fetch each source's parse output once")
    fetch.add_argument("sourceset_id")
    fetch.add_argument("--source", dest="sources", action="append", default=[],
                       metavar="SOURCE_ID", help="fetch only this source (repeatable)")
    fetch.add_argument("--select", default=None, metavar="FILE",
                       help="fetch only the sources FILE names: a source id or a page id per "
                            "line ('#' starts a comment), or a JSON list of them; a page id "
                            "names the source a run plans for that page")
    fetch.add_argument("--limit", type=int, default=None, help="fetch at most N sources")
    fetch.add_argument("--checkpoint", type=int, default=DEFAULT_CHECKPOINT,
                       help=f"write a version every N fetched sources (default "
                            f"{DEFAULT_CHECKPOINT})")
    _network_arguments(fetch)

    render = sub.add_parser("render", help="render fetched sources into the layer and its views")
    render.add_argument("sourceset_id")
    render.add_argument("--source", dest="sources", action="append", default=[],
                        metavar="SOURCE_ID", help="render only this source (repeatable)")

    status = sub.add_parser("status", help="versions, counts and (with --verify) the shas on disk")
    status.add_argument("sourceset_id")
    status.add_argument("--json", action="store_true", dest="as_json")
    status.add_argument("--verify", action="store_true",
                        help="check every source's raw, body and layer sha against its files")


def _network_arguments(parser) -> None:
    parser.add_argument("--execute", action="store_true",
                        help="really make the requests (curl to the family's site)")
    parser.add_argument("--fixtures", default=None, metavar="DIR",
                        help="replay recorded responses from DIR instead of the network")


def run(args) -> int:
    command = getattr(args, "sourceset_command", None)
    if not command:
        raise CfError("cf sourceset needs a subcommand",
                      hint="create | enumerate-wikiproject | adopt | fetch | render | status")
    workspace = from_args(args)
    return {
        "create": _create, "enumerate-wikiproject": _enumerate, "adopt": _adopt,
        "fetch": _fetch, "render": _render, "status": _status,
    }[command](workspace, args)


# ---------------------------------------------------------------------------
# helpers

def _transport(args, family_id: str, workspace):
    """FixtureTransport with --fixtures, CurlTransport with --execute, else None (dry run).

    Before a real request the family is checked for network use: confirmed, and
    its site the shipped one (``websource.family(network=True)``).
    """
    if getattr(args, "fixtures", None):
        return wikinet.FixtureTransport(Path(args.fixtures).expanduser())
    if getattr(args, "execute", False):
        websource.family(family_id, workspace, network=True)
        return wikinet.CurlTransport()
    return None


def _create_bytes(path: Path, data: bytes) -> bool:
    """Create ``path`` holding ``data``; False, writing nothing, when it already exists.

    The raw bytes a manifest records are never replaced: two writers racing for
    one source both end up recording whichever bytes reached the disk first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _write_source(ss: Sourceset, entry: dict, *, body: str | None = None) -> None:
    """The source directory's canonical body (exact UTF-8 bytes) and source.json."""
    directory = ss.source_dir(entry["source_id"])
    if body is not None:
        render_html.write_bytes(directory / "body.html", body.encode("utf-8"))
    write_json(directory / "source.json", entry, sort_keys=True)


def _on_disk_entry(ss: Sourceset, sid: str) -> dict:
    """The ``source.json`` a source directory holds, or {} when there is none."""
    try:
        data = json.loads((ss.source_dir(sid) / "source.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# create

def _create(workspace, args) -> int:
    if getattr(args, "seed", None):
        return _create_from(workspace, args)
    family = args.family or "enwiki"
    ss = Sourceset.create(workspace.paths, args.sourceset_id, family, workspace=workspace)
    manifest = ss.latest()
    log.ok(f"created sourceset {ss.id} ({family}): {manifest.path} "
           f"(sha {manifest.sha256[:12]})")
    return 0


def _link_or_copy(src: Path, dst: Path) -> None:
    """``dst`` made to hold ``src``'s bytes: a hard link where the filesystem allows one."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        render_html.write_bytes(dst, src.read_bytes())


def _create_from(workspace, args) -> int:
    """``create --from``: a new sourceset holding another one's sources with no layers.

    The way from one renderer to the next (references/locate-documents.md
    section 7): a recorded ``layer_sha256`` never changes, so a source rendered
    under an older renderer is re-rendered in a NEW sourceset, which keeps every
    identity, metadata field and recorded byte sha of the old one and starts
    with no layer.  The old sourceset is only read, so a run that pinned it still
    verifies.  Every recorded raw and body sha is checked against its file before
    anything is written; a mismatch refuses the whole seeding.
    """
    paths = workspace.paths
    old = Sourceset.open(paths, args.seed)
    base = old.load(args.seed_version) if args.seed_version else old.latest()
    if args.family and args.family != base.family:
        raise CfError(f"sourceset {old.id} is of family {base.family}, not {args.family}",
                      hint="drop --family: a seeded sourceset keeps its seed's family")
    if args.sourceset_id == old.id:
        raise CfError("a sourceset cannot be seeded from itself")
    problems: list[str] = []
    for e in base:
        problems.extend(old.source_problems(e, views=False))   # views are not carried over
    if problems:
        raise CfError(f"sourceset {old.id} version {base.version}: {len(problems)} recorded "
                      f"byte sha(s) do not match their files; nothing was created: "
                      + "; ".join(problems[:5]),
                      hint=f"`cf sourceset status {old.id} --verify` lists them")
    ss = Sourceset.create(paths, args.sourceset_id, base.family, workspace=workspace)
    entries = []
    with_bytes = 0
    for e in base:
        entry = dict(e)
        entry["layer_sha256"] = None
        entry["renderer"] = None
        src, dst = old.source_dir(e["source_id"]), ss.source_dir(e["source_id"])
        if e.get("raw_sha256"):
            _link_or_copy(src / "source.raw.html", dst / "source.raw.html")
        if e.get("html_sha256"):
            _link_or_copy(src / "body.html", dst / "body.html")
            with_bytes += 1
        if e.get("raw_sha256") or e.get("html_sha256"):
            write_json(dst / "source.json", entry, sort_keys=True)
        entries.append(entry)
    manifest = ss.write_version(ss.latest(), entries)
    ss.refresh_state(command="create", details={
        "from": base.pin(), "sources": len(entries), "with_bytes": with_bytes})
    log.ok(f"created sourceset {ss.id} ({base.family}) from {old.id} version {base.version}: "
           f"{len(entries)} source(s), {with_bytes} with their bytes, none rendered; version "
           f"{manifest.version} (sha {manifest.sha256[:12]})")
    log.info(f"next: cf sourceset render {ss.id}")
    return 0


# ---------------------------------------------------------------------------
# enumerate-wikiproject

def _enumerate(workspace, args) -> int:
    ss = Sourceset.open(workspace.paths, args.sourceset_id)
    base = ss.latest()
    fam = websource.family(base.family, workspace)
    transport = _transport(args, base.family, workspace)
    if transport is None:
        first = wikinet.request_url(fam.site, {
            "action": "query", "list": "projectpages", "wppprojects": args.project,
            "wppassessments": "1", "wpplimit": "500", "format": "json", "formatversion": "2"})
        log.info(f"dry run: would list WikiProject {args.project} on {fam.site}, then ask "
                 f"each page's latest revision and Wikidata item in batches of "
                 f"{wikinet.INFO_BATCH}. First request:")
        log.info(f"  {first}")
        log.info("pass --execute to make the requests, or --fixtures <dir> to replay "
                 "recorded ones")
        return 0
    classes = {c.lower() for c in args.classes}
    importances = {i.lower() for i in args.importance}
    pages = []
    for page in wikinet.project_pages(transport, fam.site, args.project):
        assessment = page["assessment"]
        if classes and str(assessment.get("class") or "").lower() not in classes:
            continue
        if importances and str(assessment.get("importance") or "").lower() not in importances:
            continue
        pages.append(page)
    pages.sort(key=lambda p: p["pageid"])
    # a page a consumer pinned keeps the revision it pinned (the proposal's
    # section 2.1): its latest revision is added only when asked for
    pinned = set() if args.include_adopted else base.adopted_pages()
    kept_pinned = [p["pageid"] for p in pages if p["pageid"] in pinned]
    adopted_listed = [p for p in pages if p["pageid"] in base.adopted_pages()]
    pages = [p for p in pages if p["pageid"] not in pinned]
    if args.limit is not None:
        pages = pages[:max(0, args.limit)]
    # a pinned page is asked about too: its adopted entries take the page's
    # assessment and QID when they have none (S4)
    infos = wikinet.page_infos(transport, fam.site,
                               [p["pageid"] for p in pages + adopted_listed])
    entries = {e["source_id"]: e for e in base}
    by_page: dict[int, list[int]] = {}
    for e in base:
        by_page.setdefault(e["pageid"], []).append(e["revision"])
    filled = 0
    for page in adopted_listed:
        info = infos.get(page["pageid"]) or {}
        assessment = page["assessment"] if any(page["assessment"].values()) else None
        meta = {"title": info.get("title") or page.get("title"),
                "wikidata": info.get("wikidata"), "assessment": assessment}
        for sid in [fam.source_id(page["pageid"], r) for r in by_page[page["pageid"]]]:
            entry = entries[sid]
            if not is_adopted(entry):
                continue
            missing_meta = {k: v for k, v in meta.items() if v and entry.get(k) is None}
            if missing_meta:
                entries[sid] = {**entry, **missing_meta}
                filled += 1
    added, skipped, newer, missing = 0, 0, [], []
    for page in pages:
        info = infos.get(page["pageid"])
        if info is None:
            missing.append(page["pageid"])
            continue
        sid = fam.source_id(page["pageid"], info["revision"])
        if sid in entries:
            skipped += 1
            continue
        if page["pageid"] in by_page:
            newer.append(f"page {page['pageid']}: revision {info['revision']} beside "
                         f"{', '.join(str(r) for r in sorted(by_page[page['pageid']]))}")
        entries[sid] = websource.entry(
            fam, page["pageid"], info["revision"], title=info.get("title") or page.get("title"),
            wikidata=info.get("wikidata"), assessment=page["assessment"],
            origin=websource.ORIGIN_ENUMERATED)
        added += 1
    manifest = ss.write_version(base, entries.values())
    ss.refresh_state(command="enumerate-wikiproject", details={
        "project": args.project, "listed": len(pages), "added": added, "skipped": skipped,
        "new_revisions": len(newer), "missing": len(missing),
        "pinned_by_a_consumer": len(kept_pinned), "adopted_metadata_filled": filled})
    log.info(f"WikiProject {args.project}: {len(pages)} page(s) listed, {added} added, "
             f"{skipped} already present at their latest revision")
    if kept_pinned:
        log.info(f"  {len(kept_pinned)} page(s) pinned by a consumer keep their adopted "
                 f"revision (--include-adopted adds their latest one too)")
    if filled:
        log.info(f"  {filled} adopted revision(s) took their page's assessment, QID or title")
    for line in newer:
        log.info(f"  new revision of a present page, kept beside it: {line}")
    if missing:
        log.warn(f"{len(missing)} page(s) had no revision in the page-info answer: "
                 + ", ".join(str(p) for p in missing[:20]))
    log.ok(f"sourceset {ss.id}: version {manifest.version} (sha {manifest.sha256[:12]}), "
           f"{len(manifest)} source(s)")
    return 0


# ---------------------------------------------------------------------------
# adopt

#: the metadata an adopted revision takes from its page's other sources when its
#: pin carries none (the page's assessment, its QID, its title)
INHERITED_FIELDS = ("title", "wikidata", "assessment")


def _page_metadata(entries: list[dict]) -> dict:
    """Each of :data:`INHERITED_FIELDS` from a page's newest source that has it.

    An enumerated source is preferred to an adopted one: the listing is where a
    page's assessment and QID come from.
    """
    out: dict = {}
    ranked = sorted(entries, key=lambda e: (e.get("origin") == websource.ORIGIN_ENUMERATED,
                                            e["revision"]), reverse=True)
    for name in INHERITED_FIELDS:
        for e in ranked:
            if e.get(name):
                out[name] = e[name]
                break
    return out


def _int_field(row: dict, name: str, where: str, *, required: bool) -> int | None:
    value = row.get(name)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CfError(f"{where}: {name} is a positive integer"
                      + (" (it is missing)" if value is None else f", not {value!r}"))
    return value


def _read_pins(pins_path: Path) -> list[dict]:
    """The pins file, checked row by row: ``{revision, pageid?, title?, bytes?, ...}``."""
    rows: list[dict] = []
    for number, row in enumerate(iter_jsonl(pins_path), 1):
        where = f"{pins_path.name}:{number}"
        if not isinstance(row, dict):
            raise CfError(f"{where}: a pin is a JSON object")
        revision = _int_field(row, "revision", where, required=True)
        pageid = _int_field(row, "pageid", where, required=False)
        if pageid is None and not row.get("title"):
            raise CfError(f"{where}: a pin names its revision and its page: a pageid or a "
                          f"title")
        if row.get("assessment") is not None and not isinstance(row["assessment"], dict):
            raise CfError(f"{where}: assessment is null or {{class, importance}}")
        bytes_path = None
        if row.get("bytes") is not None:
            bytes_path = Path(str(row["bytes"])).expanduser()
            if not bytes_path.is_absolute():
                bytes_path = pins_path.parent / bytes_path
            if not bytes_path.is_file():
                raise CfError(f"{where}: no bytes at {bytes_path}")
        rows.append({"where": where, "row": row, "revision": revision, "pageid": pageid,
                     "bytes": bytes_path})
    return rows


def _pin_bytes(pin: dict) -> tuple[bytes, str, dict]:
    """``(raw, canonical body, parse)`` of a pin's bytes, checked against the pin."""
    where, bytes_path = pin["where"], pin["bytes"]
    raw = bytes_path.read_bytes()
    text, parse = render_html.parse_response_text(raw)
    if text is None and render_html.response_json(raw) is not None:
        data = render_html.response_json(raw)
        error = data.get("error") if isinstance(data, dict) else None
        raise CfError(f"{where}: {bytes_path.name} is JSON but no action=parse response "
                      f"with a parse.text"
                      + (f" (the API error {error.get('code')!r})"
                         if isinstance(error, dict) else ""),
                      hint="adopt the response bytes exactly as the API returned them "
                           "(prop=text), or the HTML body itself")
    for key, want in (("revid", pin["revision"]), ("pageid", pin["pageid"])):
        got = parse.get(key) if parse else None
        if got is not None and got != want:
            label = "revision" if key == "revid" else "pageid"
            raise CfError(f"{where}: the bytes are the parse of {label} {got}, the pin "
                          f"says {want}")
    try:
        body = render_html.canonical_body(raw)
    except render_html.RenderError as exc:
        raise CfError(f"{where}: {bytes_path.name}: {exc}",
                      hint="adopt takes an action=parse JSON response or UTF-8 HTML; "
                           "re-encode the consumer's file as UTF-8") from None
    return raw, body, parse


def _adopt(workspace, args) -> int:
    """Adopt a consumer's pinned revisions, with their bytes when it holds them.

    A pin is ``{revision, pageid | title, bytes?, wikidata?, assessment?}``.  A pin
    with no page id has its page resolved from its revision (one request per
    fifty, under ``--execute`` or ``--fixtures``), never from its title, which
    is metadata.  A pin with no bytes is adopted with null shas, and ``fetch``
    then fetches exactly that revision.  A new revision takes the assessment,
    QID and title its pin does not carry from the page's other sources, so it
    stays in every frame its page is in.
    """
    ss = Sourceset.open(workspace.paths, args.sourceset_id)
    base = ss.latest()
    fam = websource.family(base.family, workspace)
    pins_path = Path(args.pins).expanduser()
    if not pins_path.is_file():
        raise CfError(f"no pins file at {pins_path}")
    pins_sha = _sha(pins_path.read_bytes())
    pins = _read_pins(pins_path)
    unresolved = sorted({p["revision"] for p in pins if p["pageid"] is None})
    resolved: dict[int, dict] = {}
    if unresolved:
        transport = _transport(args, base.family, workspace)
        if transport is None:
            raise CfError(f"{len(unresolved)} pin(s) name a title and no page id; their pages "
                          f"are resolved from their revisions on {fam.site} under --execute "
                          f"(or replayed with --fixtures)",
                          hint=f"pass --execute to make those requests ("
                               f"{-(-len(unresolved) // wikinet.INFO_BATCH)}, "
                               f"action=query&revids=), or --fixtures <dir> to replay them")
        resolved, bad = wikinet.revision_pages(transport, fam.site, unresolved)
        if bad:
            raise CfError(f"{len(bad)} pinned revision(s) are no revision of {fam.site}: "
                          + ", ".join(str(r) for r in sorted(bad)[:20]),
                          hint="remove those pins (the consumer pinned a deleted or "
                               "mistyped revision); nothing was adopted")
    entries = {e["source_id"]: dict(e) for e in base}
    by_page: dict[int, list[dict]] = {}
    for e in base:
        by_page.setdefault(e["pageid"], []).append(e)
    writes: list[tuple[dict, bytes, str]] = []
    adopted, already, inherited, notes = 0, 0, 0, []
    without_bytes, unknown_pages = 0, []
    adopted_from = {"consumer": args.consumer, "pins_sha256": pins_sha}
    for pin in pins:
        where, row, revision = pin["where"], pin["row"], pin["revision"]
        pageid = pin["pageid"]
        if pageid is None:
            page = resolved[revision]
            pageid = page["pageid"]
            if row.get("title") and page.get("title") and row["title"] != page["title"]:
                notes.append(f"{where}: the pin titles revision {revision} {row['title']!r}, "
                             f"its page {pageid} is now {page['title']!r}; the pin's title "
                             f"is kept as metadata")
        if pageid not in by_page and args.only_known_pages:
            unknown_pages.append(f"{where}: page {pageid} (revision {revision})")
            continue
        raw = body = None
        parse: dict = {}
        if pin["bytes"] is not None:
            raw, body, parse = _pin_bytes(dict(pin, pageid=pageid))
        title = row.get("title") or (parse.get("title") if parse else None)
        if parse and row.get("title") and parse.get("title") and \
                row["title"] != parse["title"]:
            notes.append(f"{where}: the pin titles page {pageid} {row['title']!r}, the "
                         f"response {parse['title']!r}; the pin's title is kept as metadata")
        sid = fam.source_id(pageid, revision)
        current = entries.get(sid)
        raw_sha = _sha(raw) if raw is not None else None
        if raw is not None and current is not None and current.get("raw_sha256"):
            if current["raw_sha256"] == raw_sha:
                already += 1
                continue
            raise CfError(f"{where}: {sid} already holds other bytes (raw sha "
                          f"{current['raw_sha256'][:12]}..., the consumer's are "
                          f"{raw_sha[:12]}...)",
                          hint="a source's bytes never change; a different rendering of the "
                               "same revision cannot be adopted into this sourceset")
        if raw is None and current is not None and is_adopted(current):
            already += 1                       # pinned before; its bytes come with fetch
            continue
        page_meta = _page_metadata([e for e in by_page.get(pageid, [])
                                    if e["source_id"] != sid])
        own = {"title": title, "wikidata": row.get("wikidata"),
               "assessment": row.get("assessment")}
        if current is not None:
            entry = dict(current)
            entry.setdefault("adopted_from", adopted_from)
            for name in INHERITED_FIELDS:
                if entry.get(name) is None and own[name]:
                    entry[name] = own[name]
        else:
            if pageid in by_page:
                notes.append(f"page {pageid}: the consumer pins revision {revision}; the "
                             f"sourceset also holds revision(s) "
                             f"{', '.join(str(e['revision']) for e in sorted(by_page[pageid], key=lambda e: e['revision']))}; "
                             f"both are kept and a run reads the adopted one")
            took = [name for name in INHERITED_FIELDS if not own[name] and page_meta.get(name)]
            inherited += bool(took)
            meta = {name: own[name] or page_meta.get(name) for name in INHERITED_FIELDS}
            entry = websource.entry(fam, pageid, revision, title=meta["title"],
                                    wikidata=meta["wikidata"], assessment=meta["assessment"],
                                    origin=websource.ORIGIN_ADOPTED, adopted_from=adopted_from)
        if raw is not None:
            entry["raw_sha256"] = raw_sha
            entry["html_sha256"] = render_html.sha256_text(body)
        else:
            without_bytes += 1
        problems = websource.entry_problems(entry)
        if problems:
            raise CfError(f"{where}: " + "; ".join(problems))
        entries[sid] = entry
        by_page.setdefault(pageid, [])
        by_page[pageid] = [e for e in by_page[pageid] if e["source_id"] != sid] + [entry]
        if raw is not None:
            writes.append((entry, raw, body))
        adopted += 1
    for entry, raw, body in writes:
        raw_path = ss.source_dir(entry["source_id"]) / "source.raw.html"
        if not _create_bytes(raw_path, raw) and raw_path.read_bytes() != raw:
            raise CfError(f"{entry['source_id']}: source.raw.html on disk holds other bytes "
                          f"than the consumer's (another writer, or an interrupted fetch); "
                          f"nothing more was adopted",
                          hint="`cf sourceset status --verify` shows the source; if no "
                               "version records those bytes, delete the file and re-run")
        _write_source(ss, entry, body=body)
    manifest = ss.write_version(base, entries.values())
    ss.refresh_state(command="adopt", details={
        "consumer": args.consumer, "pins_sha256": pins_sha, "adopted": adopted,
        "already": already, "without_bytes": without_bytes, "inherited_metadata": inherited,
        "resolved_by_revision": len(resolved), "skipped_unknown_pages": len(unknown_pages)})
    for note in notes[:50]:
        log.info(f"  {note}")
    if len(notes) > 50:
        log.info(f"  (+{len(notes) - 50} more notes)")
    for line in unknown_pages[:20]:
        log.info(f"  skipped, no other source of its page here: {line}")
    log.ok(f"adopted {adopted} revision(s) from {args.consumer} ({already} already held), "
           f"{without_bytes} without bytes (fetch them with `cf sourceset fetch`), "
           f"{inherited} taking their page's metadata; sourceset {ss.id} version "
           f"{manifest.version} (sha {manifest.sha256[:12]})")
    return 0


# ---------------------------------------------------------------------------
# fetch

def _selection(base, args) -> list[str] | None:
    """The source ids ``--source`` and ``--select`` name, in their order; None for all.

    A selection line is a source id, or a page id (all digits), which names the
    source a run plans for that page (``Manifest.preferred()``).  Anything the
    manifest does not hold is refused by name, before any request.
    """
    names: list[str] = list(getattr(args, "sources", None) or [])
    select = getattr(args, "select", None)
    if select:
        path = Path(select).expanduser()
        if not path.is_file():
            raise CfError(f"no selection file at {path}")
        text = path.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, list):
            names.extend(str(x) for x in data)
        elif data is not None:
            raise CfError(f"{path}: a JSON selection is a list of source ids or page ids")
        else:
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    names.append(line)
    if not names and not select:
        return None
    preferred = base.preferred()
    wanted: list[str] = []
    unknown: list[str] = []
    for name in names:
        sid = preferred.get(int(name)) if name.isdigit() else name
        if sid is None or sid not in base.entries:
            unknown.append(name)
        elif sid not in wanted:
            wanted.append(sid)
    if unknown:
        raise CfError(f"sourceset {base.sourceset_id} version {base.version} holds no source "
                      f"or page {', '.join(unknown[:10])}"
                      + (f" (+{len(unknown) - 10} more)" if len(unknown) > 10 else ""),
                      hint="a selection names source ids (<family>-<pageid>-<revision>) or "
                           "page ids of this sourceset")
    return wanted


def _fetch(workspace, args) -> int:
    ss = Sourceset.open(workspace.paths, args.sourceset_id)
    base = ss.latest()
    fam = websource.family(base.family, workspace)
    selected = _selection(base, args)
    if selected is not None:
        # what was asked for by name is fetched, a consumer's pin or not
        todo = [base.get(sid) for sid in selected if not base.get(sid).get("raw_sha256")]
        pinned: list[dict] = []
    else:
        # a page a consumer pinned is read at its adopted revision (the proposal's
        # 2.1): its other revisions are not fetched -- the adopted one is, if its
        # bytes are not in yet
        adopted_pages = {e["pageid"] for e in base if is_adopted(e)}
        todo = [e for e in base if not e.get("raw_sha256")]
        pinned = [e for e in todo if not is_adopted(e) and e["pageid"] in adopted_pages]
        todo = [e for e in todo if e not in pinned]
    if args.limit is not None:
        todo = todo[:max(0, args.limit)]
    transport = _transport(args, base.family, workspace)
    if transport is None:
        log.info(f"dry run: {len(todo)} source(s) of {ss.id} have no bytes yet"
                 + (f" of the {len(selected)} selected" if selected is not None else ""))
        if todo:
            first = wikinet.request_url(fam.site, websource.parse_params(todo[0]["revision"]))
            log.info(f"  first request: {first}")
        log.info("pass --execute to fetch them, or --fixtures <dir> to replay recorded "
                 "responses")
        return 0
    checkpoint = max(1, int(args.checkpoint or DEFAULT_CHECKPOINT))
    entries = {e["source_id"]: dict(e) for e in base}
    fetched, reused, failed = 0, 0, []
    pending = 0
    unavailable = 0
    manifest = base
    stopped = None
    for e in todo:
        sid = e["source_id"]
        raw_path = ss.source_dir(sid) / "source.raw.html"
        try:
            if raw_path.is_file():
                # fetch once: an earlier run's bytes, checked as fetched bytes are;
                # an adopt's source.json has no fetch block, a fetch's has one
                on_disk = _on_disk_entry(ss, sid)
                if on_disk.get("adopted_from") and not on_disk.get("fetch"):
                    raise CfError("an interrupted adopt left these bytes; re-run "
                                  "`cf sourceset adopt` with the consumer's pins")
                wikinet.check_parse(raw_path.read_bytes(), e["revision"])
                reused += 1
            else:
                fresh = wikinet.fetch_parse(transport, fam.site, e["revision"])
                render_html.canonical_body(fresh)   # bytes it cannot read are never kept
                if _create_bytes(raw_path, fresh):
                    fetched += 1
                else:                       # another writer got there first: theirs stand
                    wikinet.check_parse(raw_path.read_bytes(), e["revision"])
                    reused += 1
            unavailable = 0
            raw = raw_path.read_bytes()
            body = render_html.canonical_body(raw)
            entry = dict(e)
            entry["raw_sha256"] = _sha(raw)
            entry["html_sha256"] = render_html.sha256_text(body)
            entry["fetch"] = websource.fetch_block()
        except wikinet.WikiUnavailable as exc:
            failed.append(f"{sid}: {exc}")
            unavailable += 1
            if unavailable >= MAX_UNAVAILABLE:
                stopped = exc
                break
            continue
        except (CfError, render_html.RenderError) as exc:
            failed.append(f"{sid}: {exc}")
            continue
        _write_source(ss, entry, body=body)
        entries[sid] = entry
        pending += 1
        if pending >= checkpoint:
            manifest = ss.write_version(manifest, entries.values())
            pending = 0
    manifest = ss.write_version(manifest, entries.values())
    ss.refresh_state(command="fetch", details={
        "fetched": fetched, "reused_on_disk": reused, "failed": len(failed),
        "pinned_by_a_consumer": len(pinned), "stopped": bool(stopped),
        **({"selected": len(selected)} if selected is not None else {})})
    log.info(f"fetched {fetched} source(s), reused {reused} already on disk, "
             f"{len(failed)} failed"
             + (f", {len(pinned)} left to their consumer's adopted revision" if pinned else ""))
    for line in failed[:20]:
        log.warn(f"  {line}")
    log.ok(f"sourceset {ss.id}: version {manifest.version} (sha {manifest.sha256[:12]})")
    if stopped is not None:
        raise CfError(f"Wikipedia is refusing or rate-limiting requests: stopped after "
                      f"{MAX_UNAVAILABLE} sources in a row failed ({stopped})",
                      hint="re-run `cf sourceset fetch --execute` later; what was fetched "
                           "is recorded in the version above")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# render

def _render(workspace, args) -> int:
    ss = Sourceset.open(workspace.paths, args.sourceset_id)
    base = ss.latest()
    wanted = set(args.sources)
    unknown = sorted(wanted - set(base.entries))
    if unknown:
        raise CfError(f"sourceset {ss.id} has no source {', '.join(unknown)}")
    entries = {e["source_id"]: dict(e) for e in base}
    rendered, unchanged, refused, waiting = 0, 0, [], 0
    older_renderer = 0
    for e in base:
        sid = e["source_id"]
        if wanted and sid not in wanted:
            continue
        if not e.get("html_sha256"):
            waiting += 1
            continue
        directory = ss.source_dir(sid)
        try:
            body = render_html.read_body(directory / "body.html")
        except (OSError, render_html.RenderError) as exc:
            refused.append(f"{sid}: body.html cannot be read ({exc})")
            continue
        if render_html.sha256_text(body) != e["html_sha256"]:
            refused.append(f"{sid}: body.html is not the body the manifest records")
            continue
        try:
            rendering = render_html.render(body)
        except render_html.RenderError as exc:
            refused.append(f"{sid}: {exc}")
            continue
        if e.get("layer_sha256"):
            if e["layer_sha256"] == rendering.layer_sha256 and \
                    e.get("renderer") == render_html.RENDERER:
                render_html.write_rendering(directory, rendering)   # restore missing views
                unchanged += 1
                continue
            refused.append(f"{sid}: the layer would become {rendering.layer_sha256[:12]}... "
                           f"under {render_html.RENDERER}, the manifest records "
                           f"{e['layer_sha256'][:12]}... under {e.get('renderer')}")
            if e.get("renderer") != render_html.RENDERER:
                older_renderer += 1
            continue
        render_html.write_rendering(directory, rendering)
        entry = dict(e)
        entry["layer_sha256"] = rendering.layer_sha256
        entry["renderer"] = render_html.RENDERER
        write_json(directory / "source.json", entry, sort_keys=True)
        entries[sid] = entry
        rendered += 1
    manifest = ss.write_version(base, entries.values())
    ss.refresh_state(command="render", details={"rendered": rendered, "unchanged": unchanged,
                                                "refused": len(refused),
                                                "renderer": render_html.RENDERER})
    log.info(f"rendered {rendered} source(s), {unchanged} unchanged, {waiting} waiting for "
             f"bytes, {len(refused)} refused")
    for line in refused[:20]:
        log.warn(f"  {line}")
    if older_renderer:
        log.warn(f"{older_renderer} source(s) were rendered by an older renderer, and a "
                 f"recorded layer never changes: render them in a new sourceset seeded from "
                 f"this one's bytes -- cf sourceset create <new id> --from {ss.id}, then "
                 f"cf sourceset render <new id>")
    log.ok(f"sourceset {ss.id}: version {manifest.version} (sha {manifest.sha256[:12]})")
    return 1 if refused else 0


# ---------------------------------------------------------------------------
# status

def _status(workspace, args) -> int:
    ss = Sourceset.open(workspace.paths, args.sourceset_id)
    manifest = ss.latest()
    problems: list[str] = []
    if args.verify:
        for e in manifest:
            problems.extend(ss.source_problems(e))
        # a verification is worth a row of the history; a plain status writes nothing
        counts = ss.refresh_state(command="status", details={
            "verified": True, "problems": len(problems)})["counts"]
    else:
        counts = ss.counts(manifest)
    report = {"id": ss.id, "family": manifest.family, "versions": ss.versions(),
              "manifest": manifest.pin(), "counts": counts,
              "verified": bool(args.verify), "problems": problems}
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        log.info(f"sourceset {ss.id} ({manifest.family}): version {manifest.version} of "
                 f"{len(report['versions'])}, sha {manifest.sha256}")
        log.info(f"  {counts['sources']} source(s) over {counts['pages']} page(s): "
                 f"{counts['enumerated']} enumerated, {counts['adopted']} adopted, "
                 f"{counts['fetched']} fetched, {counts['rendered']} rendered")
        log.info("  pages by class: " + ", ".join(
            f"{k} {v}" for k, v in counts["by_class"].items()))
        log.info("  pages by importance: " + ", ".join(
            f"{k} {v}" for k, v in counts["by_importance"].items()))
        if args.verify:
            if problems:
                log.warn(f"{len(problems)} problem(s) on disk:")
                for line in problems[:50]:
                    log.warn(f"  {line}")
            else:
                log.ok("every recorded sha matches its file")
    return 1 if problems else 0
