"""cf export -- write a companion's attribution data as an annotation document.

New code, implementing docs/proposals/pdf-to-companion.md section 5.4 (M3):
the two prototype exporters of docs/annotations.md section 10 as one command
with a subcommand per companion convention.  It needs no workspace: it reads a
checkout at a pinned commit, plus the outside inputs a convention needs, and
writes the document in canonical form beside a report of what did not fit.

    cf export attribution-table <checkout> --commit <sha> --out <doc.json> \\
        [--data-dir pdf-overlay/data] [--ledger source-formalization-ledger.json] \\
        [--official-source pdf-overlay/official-source.json]
    cf export docstring-tags <checkout> --commit <sha> --pdf <the anchor edition> \\
        --xref <xref.json> --site-pages <pages.json> [--site <rendered site>] --out <doc.json>
    cf export docstring-tags <checkout> --commit <sha> --source-id <id> --source-title <title> \\
        --source-revision <edition stamp> --out <doc.json>        # no PDF, no site
    cf export site-pages <rendered site> --out <pages.json> [--source-id ID] [--title T] \\
        [--path P] [--rendered-at TIME] [--exclude GLOB]...

The document is validated before the command returns (schema, invariants,
canonical bytes, and the format's ``external-urls`` lint, which the exporters
keep their documents clean of); a problem the exporter recorded, or a document
that does not validate, exits 1 after writing, so the file is there to inspect.  The
checkout is read only: `cflib.gitsafe` pins every git call, and an --out or
--report under it is refused.  An input shape the exporters do not check for
(a KeyError, ValueError, IndexError or a failed tool) is a CfError, never a
traceback.  docs/exporters.md says what each exporter reads and loses.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import validate as V
from annotations.export import common
from cflib import log
from cflib.jsonio import atomic_write
from cflib.errors import CfError


def register(subparsers) -> None:
    p = subparsers.add_parser("export", help="write a companion's attribution data as an annotation document")
    sub = p.add_subparsers(dest="export_command", metavar="<convention>")
    at = sub.add_parser("attribution-table",
                        help="a PDF-overlay companion: geometry, target refinement, ledger, verdicts")
    _common_arguments(at)
    at.add_argument("--lean-build", type=Path, metavar="DIR",
                    help="a Lake build's lib/lean directory whose .ilean files name anonymous instances "
                         "(default: <checkout>/.lake/build/lib/lean)")
    at.add_argument("--data-dir", metavar="DIR",
                    help="the overlay data directory, relative to the checkout: the geometry batches, the "
                         "target refinement, the verdicts and the destinations (default: pdf-overlay/data)")
    at.add_argument("--ledger", metavar="FILE",
                    help="the source-formalization ledger, relative to the checkout "
                         "(default: source-formalization-ledger.json)")
    at.add_argument("--official-source", metavar="FILE",
                    help="the PDF's download record, relative to the checkout "
                         "(default: pdf-overlay/official-source.json)")
    dt = sub.add_parser("docstring-tags",
                        help="a docstring-tagged companion plus its literate site's index and page manifest")
    _common_arguments(dt)
    dt.add_argument("--pdf", type=Path, metavar="FILE",
                    help="the anchor edition's PDF (named through the registry by its sha256); without it the "
                         "edition is an unpinned source named by --source-id, --source-title and --source-revision")
    dt.add_argument("--xref", type=Path, metavar="FILE",
                    help="the site's cross-reference index, xref.json (or a .gz of it); with --site-pages")
    dt.add_argument("--site-pages", type=Path, metavar="FILE",
                    help="the site's page manifest: the HTML pages the producer pins, each with its sha256 "
                         "(docs/exporters.md section 2); without it the document has no site source")
    dt.add_argument("--site", type=Path, metavar="DIR",
                    help="the rendered site, to check every pinned page and every anchor; pages the manifest "
                         "leaves out are counted, not refused")
    dt.add_argument("--source-revision", metavar="STAMP",
                    help="without --pdf: the edition stamp that identifies the unpinned source (its revision)")
    dt.add_argument("--source-media-type", default="application/pdf", metavar="TYPE",
                    help="without --pdf: the unpinned source's IANA media type (default application/pdf)")
    dt.add_argument("--source-doi", metavar="DOI", help="without --pdf: the edition's DOI")
    dt.add_argument("--source-isbn", action="append", default=[], metavar="ISBN13",
                    help="without --pdf: an ISBN-13 of the edition (repeatable)")
    dt.add_argument("--adapter-config", type=Path, metavar="FILE",
                    help="the docstring_tags adapter config to read the tags with "
                         "(default: the shipped tao-measure-lean template)")
    sp = sub.add_parser("site-pages",
                        help="write a rendered literate site's page manifest, the file docstring-tags "
                             "--site-pages reads")
    sp.add_argument("site", type=Path, help="the rendered site directory (read only)")
    sp.add_argument("--out", type=Path, required=True, metavar="FILE", help="the manifest to write")
    sp.add_argument("--source-id", help="the site source's id (default when absent: <name>-site at export)")
    sp.add_argument("--title", help="the site source's title (default when absent: a generic one at export)")
    sp.add_argument("--path", help="the site's path under the companion's checkout (default: the directory's "
                                   "name)")
    sp.add_argument("--rendered-at", metavar="TIME",
                    help="the render time, RFC 3339 in UTC (default: the newest page's modification time)")
    sp.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="leave out the pages whose path under the site matches GLOB (fnmatch, `*` crosses `/`; "
                         "repeatable), e.g. '*/_lazy/*' for generated fragments")


def _common_arguments(p) -> None:
    p.add_argument("checkout", type=Path, help="a git checkout of the companion (read only)")
    p.add_argument("--commit", required=True, help="the commit the checkout must be at")
    p.add_argument("--out", type=Path, required=True, metavar="FILE", help="the document to write")
    p.add_argument("--report", type=Path, metavar="FILE",
                   help="the export report (default: <out> with .export-report.json)")
    p.add_argument("--name", help="companion.name (default: the last component of the origin remote)")
    p.add_argument("--repo-id", help="companion.repo as host/org/name (default: from the origin remote)")
    p.add_argument("--site-name", default="verso_literate",
                   help="the destination site kind (default: verso_literate)")
    p.add_argument("--source-id", help="the PDF source's id, when the registry does not know the bytes")
    p.add_argument("--source-title", help="the PDF source's title, when the registry does not know the bytes")
    p.add_argument("--source-edition", help="the PDF source's edition")
    p.add_argument("--page-offset", type=int, help="physical page minus printed page, when the registry "
                                                    "does not know the bytes")


def _report_path(args) -> Path:
    if args.report is not None:
        return args.report
    name = args.out.name
    for suffix in (".annotations.json", ".json"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return args.out.with_name(name + ".export-report.json")


def run(args) -> int:
    if not getattr(args, "export_command", None):
        raise CfError("say which convention to export", hint="cf export attribution-table … or "
                                                             "cf export docstring-tags …")
    if args.export_command == "site-pages":
        return _site_pages(args)
    checkout = args.checkout.expanduser()
    if not checkout.is_dir():
        raise CfError(f"{checkout} is not a directory")
    report_path = _report_path(args)
    for flag, path in (("--out", args.out), ("--report", report_path)):
        target = path.expanduser().resolve()
        if target == checkout.resolve() or checkout.resolve() in target.parents:
            raise CfError(f"{flag} {path} is inside the checkout {checkout}",
                          hint="the exporter reads the checkout and writes nothing under it; write elsewhere")
    options = {"name": args.name, "repo_id": args.repo_id, "site_name": args.site_name,
               "source_id": args.source_id, "source_title": args.source_title,
               "source_edition": args.source_edition, "page_offset": args.page_offset}
    try:
        if args.export_command == "attribution-table":
            from annotations.export import attribution_table
            doc, report = attribution_table.export(checkout, args.commit, lean_build=args.lean_build,
                                                   data_dir=args.data_dir, ledger=args.ledger,
                                                   official_source=args.official_source, **options)
        else:
            from annotations.export import docstring_tags
            for flag in ("pdf", "xref", "site_pages"):
                path = getattr(args, flag)
                if path is not None and not path.is_file():
                    raise CfError(f"--{flag.replace('_', '-')}: {path} is not a file")
            if args.site is not None and not args.site.is_dir():
                raise CfError(f"--site: {args.site} is not a directory")
            doc, report = docstring_tags.export(checkout, args.commit, pdf=args.pdf, xref=args.xref,
                                                site_pages=args.site_pages, site=args.site,
                                                adapter_config=args.adapter_config,
                                                source_revision=args.source_revision,
                                                source_media_type=args.source_media_type,
                                                source_doi=args.source_doi, source_isbn=tuple(args.source_isbn),
                                                **options)
    except common.ExportError as exc:
        raise CfError(exc.message, hint=exc.hint) from exc
    except RuntimeError as exc:
        raise CfError(str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        raise CfError(f"{' '.join(map(str, exc.cmd))} failed ({exc.returncode}): {stderr.strip()[:200]}",
                      hint="the export needs that tool to read its inputs") from exc
    except (KeyError, ValueError, IndexError) as exc:
        # a backstop: an input shape the exporter does not check for, reported instead of a traceback
        raise CfError(f"the {args.export_command} export stopped on an input it did not expect: "
                      f"{type(exc).__name__}: {exc}",
                      hint="the companion's files disagree with the convention the exporter reads "
                           "(docs/exporters.md); the value above names the record") from exc
    text = common.write_canonical(args.out, doc)
    common.write_report(report_path, report)
    checked = V.check(V.loads(text), text=text, canonical=True, lints=common.LINTS)
    counts = ", ".join(f"{len(doc.get(k, []))} {k}" for k in V.RECORD_ARRAYS if k in doc)
    profiles = ", ".join(sorted(doc.get("profiles", {}))) or "core only"
    log.info(f"wrote {args.out} ({counts}; profiles: {profiles})")
    log.info(f"report {report_path}: {len(report.lossy)} lossy and {len(report.bent)} bent buckets")
    status = 0
    for warning in checked.warnings:
        log.warn(f"{args.out}: warning: {warning} [{warning.code}]")
    if checked.problems:
        log.warn_block(f"{args.out}: the document does not validate ({len(checked.problems)} problems; "
                       f"lints: {', '.join(common.LINTS)})",
                       [f"    {p} [{p.code}]" for p in checked.problems[:20]])
        status = 1
    if report.problems:
        log.warn_block(f"the export recorded {len(report.problems)} problems",
                       [f"    {p}" for p in report.problems[:20]])
        status = 1
    if args.verbose:
        log.detail(json.dumps(report.counts, indent=1, ensure_ascii=False, default=str))
    return status


def _site_pages(args) -> int:
    from annotations.export import site_pages
    site = args.site.expanduser()
    target = args.out.expanduser().resolve()
    if site.is_dir() and site.resolve() in target.parents:
        raise CfError(f"--out {args.out} is inside the site {site}",
                      hint="the manifest describes the site's pages; write it elsewhere")
    try:
        pin = site_pages.manifest(site, source_id=args.source_id, title=args.title, path=args.path,
                                  rendered_at=args.rendered_at, exclude=tuple(args.exclude))
    except common.ExportError as exc:
        raise CfError(exc.message, hint=exc.hint) from exc
    atomic_write(args.out, json.dumps(pin, indent=1, ensure_ascii=False) + "\n")
    log.info(f"wrote {args.out} ({len(pin['pages'])} pages, site_sha256 {pin['site_sha256'][:12]}...)")
    return 0
