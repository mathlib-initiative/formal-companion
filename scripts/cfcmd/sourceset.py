"""cf sourceset -- create, fill, fetch, render and inspect a sourceset of web sources.

New code, implementing stage L1 of docs/proposals/wikipedia-locate.md section 3
(sources: enumerate the corpus, adopt consumer pins and bytes, fetch the rest
once, render) over the sourceset layout of references/locate-documents.md
section 7:

    cf sourceset create wpm --family enwiki
    cf sourceset enumerate-wikiproject wpm --project Mathematics --execute
    cf sourceset adopt wpm --from pins.jsonl --consumer wikilean
    cf sourceset fetch wpm --limit 200 --execute
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
from sources.sourceset import Sourceset

#: how many fetched sources a version records at most, so a killed fetch loses
#: at most this many manifest updates (the bytes on disk are reused next time)
DEFAULT_CHECKPOINT = 200


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "sourceset", help="create, enumerate, adopt, fetch, render and inspect a sourceset "
                          "of web sources (locate)")
    sub = p.add_subparsers(dest="sourceset_command", metavar="<subcommand>")

    create = sub.add_parser("create", help="a new sourceset: manifest-1 with no sources")
    create.add_argument("sourceset_id")
    create.add_argument("--family", default="enwiki",
                        help="the web-source family in the registry (default: enwiki)")

    enum = sub.add_parser("enumerate-wikiproject",
                          help="add a WikiProject's articles at their latest revisions")
    enum.add_argument("sourceset_id")
    enum.add_argument("--project", required=True, help="the WikiProject, e.g. Mathematics")
    enum.add_argument("--class", dest="classes", action="append", default=[], metavar="C",
                      help="keep only this assessment class (repeatable; case-insensitive)")
    enum.add_argument("--importance", action="append", default=[], metavar="I",
                      help="keep only this importance (repeatable; top, high, mid, low, ...)")
    enum.add_argument("--limit", type=int, default=None, help="add at most N pages")
    _network_arguments(enum)

    adopt = sub.add_parser("adopt", help="adopt a consumer's pinned revisions and their bytes")
    adopt.add_argument("sourceset_id")
    adopt.add_argument("--from", dest="pins", required=True, metavar="ROWS",
                       help="JSON lines {pageid, revision, title?, wikidata?, bytes: <path>}; "
                            "a relative bytes path is relative to this file")
    adopt.add_argument("--consumer", default="consumer",
                       help="who pinned them, recorded in adopted_from")

    fetch = sub.add_parser("fetch", help="fetch each source's parse output once")
    fetch.add_argument("sourceset_id")
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

def _transport(args):
    """FixtureTransport with --fixtures, CurlTransport with --execute, else None (dry run)."""
    if getattr(args, "fixtures", None):
        return wikinet.FixtureTransport(Path(args.fixtures).expanduser())
    if getattr(args, "execute", False):
        return wikinet.CurlTransport()
    return None


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_source(ss: Sourceset, entry: dict, *, raw: bytes | None = None,
                  body: str | None = None) -> None:
    """The source directory's raw bytes, canonical body and source.json."""
    from cflib.jsonio import atomic_write

    directory = ss.source_dir(entry["source_id"])
    if raw is not None:
        _write_bytes(directory / "source.raw.html", raw)
    if body is not None:
        atomic_write(directory / "body.html", body)
    write_json(directory / "source.json", entry, sort_keys=True)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# create

def _create(workspace, args) -> int:
    ss = Sourceset.create(workspace.paths, args.sourceset_id, args.family, workspace=workspace)
    manifest = ss.latest()
    log.ok(f"created sourceset {ss.id} ({args.family}): {manifest.path} "
           f"(sha {manifest.sha256[:12]})")
    return 0


# ---------------------------------------------------------------------------
# enumerate-wikiproject

def _enumerate(workspace, args) -> int:
    ss = Sourceset.open(workspace.paths, args.sourceset_id)
    base = ss.latest()
    fam = websource.family(base.family, workspace)
    transport = _transport(args)
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
    if args.limit is not None:
        pages = pages[:max(0, args.limit)]
    infos = wikinet.page_infos(transport, fam.site, [p["pageid"] for p in pages])
    entries = {e["source_id"]: e for e in base}
    by_page: dict[int, list[int]] = {}
    for e in base:
        by_page.setdefault(e["pageid"], []).append(e["revision"])
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
        "new_revisions": len(newer), "missing": len(missing)})
    log.info(f"WikiProject {args.project}: {len(pages)} page(s) listed, {added} added, "
             f"{skipped} already present at their latest revision")
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

def _adopt(workspace, args) -> int:
    ss = Sourceset.open(workspace.paths, args.sourceset_id)
    base = ss.latest()
    fam = websource.family(base.family, workspace)
    pins_path = Path(args.pins).expanduser()
    if not pins_path.is_file():
        raise CfError(f"no pins file at {pins_path}")
    pins_sha = _sha(pins_path.read_bytes())
    entries = {e["source_id"]: dict(e) for e in base}
    by_page: dict[int, list[int]] = {}
    for e in base:
        by_page.setdefault(e["pageid"], []).append(e["revision"])
    writes: list[tuple[dict, bytes, str]] = []
    adopted, already, notes = 0, 0, []
    for number, row in enumerate(iter_jsonl(pins_path), 1):
        where = f"{pins_path.name}:{number}"
        if not isinstance(row, dict):
            raise CfError(f"{where}: a pin is a JSON object")
        try:
            pageid, revision = row["pageid"], row["revision"]
            bytes_path = Path(str(row["bytes"])).expanduser()
        except KeyError as exc:
            raise CfError(f"{where}: a pin needs pageid, revision and bytes ({exc} is missing)")
        if not bytes_path.is_absolute():
            bytes_path = pins_path.parent / bytes_path
        if not bytes_path.is_file():
            raise CfError(f"{where}: no bytes at {bytes_path}")
        raw = bytes_path.read_bytes()
        text, parse = render_html.parse_response_text(raw)
        if parse and parse.get("revid") not in (None, revision):
            raise CfError(f"{where}: the bytes are the parse of revision {parse.get('revid')}, "
                          f"the pin says {revision}")
        body = render_html.canonical_body(raw)
        sid = fam.source_id(pageid, revision)
        raw_sha = _sha(raw)
        html_sha = render_html.sha256_text(body)
        current = entries.get(sid)
        if current is not None and current.get("raw_sha256"):
            if current["raw_sha256"] == raw_sha:
                already += 1
                continue
            raise CfError(f"{where}: {sid} already holds other bytes (raw sha "
                          f"{current['raw_sha256'][:12]}..., the consumer's are "
                          f"{raw_sha[:12]}...)",
                          hint="a source's bytes never change; a different rendering of the "
                               "same revision cannot be adopted into this sourceset")
        adopted_from = {"consumer": args.consumer, "pins_sha256": pins_sha}
        if current is not None:
            entry = dict(current)
            entry["raw_sha256"] = raw_sha
            entry["html_sha256"] = html_sha
            entry.setdefault("adopted_from", adopted_from)
            for key in ("title", "wikidata"):
                if entry.get(key) is None and row.get(key):
                    entry[key] = row[key]
        else:
            if pageid in by_page:
                notes.append(f"page {pageid}: the consumer pins revision {revision}; the "
                             f"sourceset also holds revision(s) "
                             f"{', '.join(str(r) for r in sorted(by_page[pageid]))}; both are "
                             f"kept and a run should read the adopted one")
            entry = websource.entry(fam, pageid, revision, title=row.get("title"),
                                    wikidata=row.get("wikidata"),
                                    assessment=row.get("assessment"),
                                    origin=websource.ORIGIN_ADOPTED, adopted_from=adopted_from)
            entry["raw_sha256"] = raw_sha
            entry["html_sha256"] = html_sha
            by_page.setdefault(pageid, []).append(revision)
        problems = websource.entry_problems(entry)
        if problems:
            raise CfError(f"{where}: " + "; ".join(problems))
        entries[sid] = entry
        writes.append((entry, raw, body))
        adopted += 1
    for entry, raw, body in writes:
        _write_source(ss, entry, raw=raw, body=body)
    manifest = ss.write_version(base, entries.values())
    ss.refresh_state(command="adopt", details={"consumer": args.consumer,
                                               "pins_sha256": pins_sha, "adopted": adopted,
                                               "already": already})
    for note in notes:
        log.info(f"  {note}")
    log.ok(f"adopted {adopted} revision(s) from {args.consumer} ({already} already held "
           f"these bytes); sourceset {ss.id} version {manifest.version} "
           f"(sha {manifest.sha256[:12]})")
    return 0


# ---------------------------------------------------------------------------
# fetch

def _fetch(workspace, args) -> int:
    ss = Sourceset.open(workspace.paths, args.sourceset_id)
    base = ss.latest()
    fam = websource.family(base.family, workspace)
    todo = [e for e in base if not e.get("raw_sha256")]
    if args.limit is not None:
        todo = todo[:max(0, args.limit)]
    transport = _transport(args)
    if transport is None:
        log.info(f"dry run: {len(todo)} source(s) of {ss.id} have no bytes yet")
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
    manifest = base
    for e in todo:
        sid = e["source_id"]
        raw_path = ss.source_dir(sid) / "source.raw.html"
        try:
            if raw_path.is_file():
                raw = raw_path.read_bytes()       # fetch once: an earlier run's bytes
                reused += 1
            else:
                raw = wikinet.fetch_parse(transport, fam.site, e["revision"])
                fetched += 1
            body = render_html.canonical_body(raw)
        except (CfError, render_html.RenderError) as exc:
            failed.append(f"{sid}: {exc}")
            continue
        entry = dict(e)
        entry["raw_sha256"] = _sha(raw)
        entry["html_sha256"] = render_html.sha256_text(body)
        entry["fetch"] = websource.fetch_block()
        _write_source(ss, entry, raw=None if raw_path.is_file() else raw, body=body)
        entries[sid] = entry
        pending += 1
        if pending >= checkpoint:
            manifest = ss.write_version(manifest, entries.values())
            pending = 0
    manifest = ss.write_version(manifest, entries.values())
    ss.refresh_state(command="fetch", details={"fetched": fetched, "reused_on_disk": reused,
                                               "failed": len(failed)})
    log.info(f"fetched {fetched} source(s), reused {reused} already on disk, "
             f"{len(failed)} failed")
    for line in failed[:20]:
        log.warn(f"  {line}")
    log.ok(f"sourceset {ss.id}: version {manifest.version} (sha {manifest.sha256[:12]})")
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
    for e in base:
        sid = e["source_id"]
        if wanted and sid not in wanted:
            continue
        if not e.get("html_sha256"):
            waiting += 1
            continue
        directory = ss.source_dir(sid)
        body_path = directory / "body.html"
        try:
            body = body_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            refused.append(f"{sid}: body.html cannot be read ({exc})")
            continue
        if render_html.sha256_text(body) != e["html_sha256"]:
            refused.append(f"{sid}: body.html is not the body the manifest records")
            continue
        rendering = render_html.render(body)
        if e.get("layer_sha256"):
            if e["layer_sha256"] == rendering.layer_sha256 and \
                    e.get("renderer") == render_html.RENDERER:
                render_html.write_rendering(directory, rendering)   # restore missing views
                unchanged += 1
                continue
            refused.append(f"{sid}: the layer would become {rendering.layer_sha256[:12]}... "
                           f"under {render_html.RENDERER}, the manifest records "
                           f"{e['layer_sha256'][:12]}... under {e.get('renderer')}")
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
    state = ss.refresh_state(command="status", details={
        "verified": bool(args.verify), "problems": len(problems)})
    report = {"id": ss.id, "family": manifest.family, "versions": ss.versions(),
              "manifest": manifest.pin(), "counts": state["counts"],
              "verified": bool(args.verify), "problems": problems}
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        counts = state["counts"]
        log.info(f"sourceset {ss.id} ({manifest.family}): version {manifest.version} of "
                 f"{len(report['versions'])}, sha {manifest.sha256}")
        log.info(f"  {counts['sources']} source(s) over {counts['pages']} page(s): "
                 f"{counts['enumerated']} enumerated, {counts['adopted']} adopted, "
                 f"{counts['fetched']} fetched, {counts['rendered']} rendered")
        log.info("  by class: " + ", ".join(f"{k} {v}" for k, v in counts["by_class"].items()))
        log.info("  by importance: " + ", ".join(
            f"{k} {v}" for k, v in counts["by_importance"].items()))
        if args.verify:
            if problems:
                log.warn(f"{len(problems)} problem(s) on disk:")
                for line in problems[:50]:
                    log.warn(f"  {line}")
            else:
                log.ok("every recorded sha matches its file")
    return 1 if problems else 0
