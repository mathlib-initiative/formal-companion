"""``cf detect-proxies <name> [--lean]`` -- stage 8, proxy detection.

New code (the CLI wrapper); the analysis lives in ``scripts/detect/`` and is
ported from /Users/jack/Desktop/tao-differential/adam-detector/ (README.md,
analyze2.py, DumpDeps.lean) and from transcript b174aa13 L6099/L6145.

Two tiers:

``cheap`` (always)
    ``statements.jsonl`` + the book's ``items.json`` -> ``pairs/def-coverage.<name>.json``
    (stated / bridge_only / missing) and PROXY rows for definition bodies that
    are the library's object under a book name.

``lean`` (``--lean``)
    a ``deps.json`` dump -> ORDER_VIOLATION and SAME_STAGE_PRIMITIVE hits.
    This command **never runs lake**: when the dump is missing it prints the
    ``dump_deps.sh`` command line and stops, because producing it needs a built
    ``.lake`` that the skill must not build or modify.

Outputs, all inside the campaign directory::

    pairs/def-coverage.<name>.json
    detect/<name>/findings.cheap.jsonl
    detect/<name>/report_keydefs.json
    detect/<name>/report_broad_defs.json      (--broad, when the stage table maps the repo's modules)
    detect/<name>/findings.lean.jsonl
    companions/<name>/state.json              (counts merged in)

Exit codes: 0 fine, 1 the unclassified-modules gate refused the run (Stage 8:
"``unclassified_modules`` must be empty"), 2 a user error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:  # pragma: no cover - import side effect
    sys.path.insert(0, str(_SCRIPTS))

from cflib import campaign as cf_campaign  # noqa: E402
from cflib import state as cf_state  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_jsonl, write_json, write_jsonl  # noqa: E402
from cflib.log import info, ok, warn  # noqa: E402
from cflib.yamlio import read_yaml  # noqa: E402
from detect import analyze_stages, def_coverage  # noqa: E402
from detect import findings as find_rows  # noqa: E402
from detect._compat import DEFAULT_KINDS, item_base, read_json  # noqa: E402
from detect.deps import DepsIndex  # noqa: E402
from detect.stages import StageTable  # noqa: E402

COMMAND = "detect-proxies"


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------
def register(subparsers) -> object:
    """Add the ``detect-proxies`` subcommand (cf.py discovers this)."""
    ap = subparsers.add_parser(
        COMMAND,
        help="classify book Definitions as native / bridge-only / missing and find proxies",
        description=__doc__,
    )
    ap.add_argument("name", help="companion name, as in companions/<name>/")
    ap.add_argument("--lean", action="store_true", help="also run the deps.json tier")
    ap.add_argument("--deps", help="deps.json path (default: <campaign>/detect/<name>/deps.json)")
    ap.add_argument("--book", help="stage-table id (default: campaign.yaml book.id)")
    ap.add_argument("--stages", help="explicit stage table json (overrides --book)")
    ap.add_argument("--statements", help="statements.jsonl override")
    ap.add_argument("--items", help="items.json override")
    ap.add_argument("--run-id", help="run id stamped into findings (default: detect-<name>-<tier>)")
    ap.add_argument("--all-decls", action="store_true", help="do not restrict the Lean tier to key rows")
    ap.add_argument("--allow-unclassified", action="store_true",
                    help="do not fail when the stage table leaves modules unclassified")
    ap.add_argument("--broad", action="store_true", help="also write the uncurated broad scan")
    ap.add_argument("--render-dump-deps", action="store_true",
                    help="render DumpDeps.lean outside the repo and print the command to run, then stop")
    ap.add_argument("--no-findings", action="store_true", help="write reports but no finding rows")
    ap.add_argument("--json", action="store_true", dest="as_json", help="print the summary as JSON")
    ap.set_defaults(func=run)
    return ap


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------
def run(args) -> int:
    """Run the cheap tier, then the Lean tier when ``--lean`` is passed."""
    campaign = cf_campaign.from_args(args)
    paths = campaign.paths
    name = args.name
    kinds = campaign.kinds
    book_sha = campaign.book_sha
    entry = _companion_entry(campaign, name)
    adapter = _read_yaml(paths.adapter_yaml(name))
    detect_dir = paths.detect_companion_dir(name)

    if args.render_dump_deps:
        return _render_dump_deps(detect_dir, name, entry, adapter, args)

    statements = Path(args.statements) if args.statements else paths.statements_jsonl(name)
    if not statements.exists():
        raise CfError(
            f"no statement table for {name!r}: {statements}",
            hint=f"run `cf extract --companion {name}` first",
        )
    items_path = Path(args.items) if args.items else paths.items_json
    if not items_path.exists():
        raise CfError(
            f"no items.json for book {campaign.book_id!r}: {items_path}",
            hint=f"run `cf book {campaign.book_id}` first, or pass --items",
        )

    stages = _load_stages(args, paths.detect_dir, args.book or campaign.book_id)
    rows = read_jsonl(statements, skip_meta=True)

    summary: dict = {
        "companion": name,
        "book": campaign.book_id,
        "book_sha": book_sha,
        "statements": str(statements),
        "items": str(items_path),
    }
    chapter = _audited_chapter(campaign, stages)
    summary["cheap"] = _run_cheap(
        args, paths, detect_dir, name, rows, items_path, stages, kinds, book_sha,
        chapter=chapter,
    )
    exit_code = 0
    if args.lean:
        summary["lean"], exit_code = _run_lean(
            args, detect_dir, name, rows, stages, kinds, book_sha, entry, adapter
        )

    _write_state(campaign, name, summary)
    if args.as_json:
        print(json.dumps(summary, indent=1))
    return exit_code


def _companion_entry(campaign, name: str) -> dict:
    """The companion's campaign.yaml record; ``{}`` when it is not listed."""
    try:
        return dict(campaign.companion(name))
    except CfError:
        return {}


def _read_yaml(path: Path) -> dict:
    """adapter.yaml as a dict; ``{}`` when absent or unreadable."""
    if not path.exists():
        return {}
    try:
        return read_yaml(path) or {}
    except Exception as exc:  # pragma: no cover - malformed adapter
        warn(f"cannot read {path}: {exc}")
        return {}


def _load_stages(args, detect_dir: Path, book_id: str | None) -> StageTable:
    """A campaign-local stage table wins over the one shipped with the skill."""
    if args.stages:
        return StageTable.load(args.stages)
    if not book_id:
        raise CfError(
            "no book id: campaign.yaml has no book.id",
            hint="pass --book <id> or --stages <path to a stage table>",
        )
    return StageTable.for_book(book_id, extra_dirs=[detect_dir / "mathlib_stages"])


def _audited_chapter(campaign, stages):
    """The chapter def-coverage is scoped to.

    ``campaign.yaml book.chapters`` is the single source of scope, because
    ``cf pair`` writes the SAME ``pairs/def-coverage.<name>.json`` and reads it
    from there: when the two disagreed, an unscoped ``cf pair`` reported 32
    stated / 7 bridge-only / 1 missing for the same repo this command scored
    32 / 4 / 0, and the file meant whichever the last command ran.  The stage
    table's own chapter is the fallback for a campaign that predates the key.
    """
    chapters = (campaign.book or {}).get("chapters")
    if isinstance(chapters, list) and len(chapters) == 1:
        try:
            return int(chapters[0])
        except (TypeError, ValueError):
            pass
    if isinstance(chapters, list) and len(chapters) > 1:
        return None          # multi-chapter scope: do not narrow to one
    return stages.chapter


# --------------------------------------------------------------------------
# cheap tier
# --------------------------------------------------------------------------
def _run_cheap(args, paths, detect_dir, name, rows, items_path, stages, kinds, book_sha,
               *, chapter=None) -> dict:
    """def-coverage + body-proxy candidates; writes pairs/def-coverage.<name>.json."""
    definitions = def_coverage.load_items(items_path, kind="Definition", chapter=chapter, kinds=kinds)
    if not definitions:
        warn(f"items.json names no Definition items for chapter {chapter}")
    coverage = def_coverage.compute_def_coverage(definitions, rows, kinds=kinds)
    candidates = def_coverage.body_proxy_candidates(rows, stages, kinds=kinds)
    shadowed = def_coverage.shadowed_object_labels(rows, stages)
    bodies = sum(1 for r in rows if r.get("body") and def_coverage.is_definitional(r))

    # pairs/def-coverage.<name>.json is written by `cf pair` as well (the same
    # artifact is listed under Stage 3 and Stage 8 of design/stage_map.md), so
    # this is a superset of pair/def_coverage.py's schema -- `bridge_only_decls`
    # and the descriptive keys are kept so either writer leaves a file both
    # readers accept. TODO(cf): collapse the two implementations into this one.
    out = {
        "book_sha": book_sha,
        "kind": "Definition",
        "chapter": chapter,
        "keywords": list(def_coverage.DEFINITIONAL_KEYWORDS),
        "book_items": len(definitions),
        "missing": coverage["missing"],
        "bridge_only": coverage["bridge_only"],
        "bridge_only_decls": coverage["bridge_only_detail"],
        "bridge_only_detail": coverage["bridge_only_detail"],
        "stated": coverage["stated"],
        "proxy_candidates": candidates,
        "_meta": {
            "companion": name,
            "book": stages.book,
            "book_sha": book_sha,
            "definitions": len(definitions),
            "definitional_rows_with_body": bodies,
            "shadowed_objects": shadowed,
            "generated_by": "cf detect-proxies (cheap tier)",
        },
    }
    coverage_path = paths.def_coverage_json(name)
    write_json(coverage_path, out)

    counts = {
        "definitions": len(definitions),
        "stated": len(coverage["stated"]),
        "bridge_only": len(coverage["bridge_only"]),
        "missing": len(coverage["missing"]),
        "proxy_candidates": len(candidates),
        "definitional_rows_with_body": bodies,
    }
    info(
        f"{name}: {counts['definitions']} book Definitions -- {counts['stated']} stated, "
        f"{counts['bridge_only']} bridge-only, {counts['missing']} missing; "
        f"{counts['proxy_candidates']} body proxy candidate(s)"
    )
    if bodies == 0 and rows:
        warn("no definitional row carries a `body`: the body-proxy scan saw nothing "
             "(v1 statement tables have no body field)")
    if shadowed:
        warn(f"{name} defines its own {', '.join(shadowed)}: only namespace-qualified "
             "occurrences of the library constant count as evidence for those")

    rows_out: list[dict] = []
    if not args.no_findings:
        run_id = args.run_id or find_rows.default_run_id(name, "cheap")
        rows_out = def_coverage.build_finding_rows(
            coverage, candidates, companion=name, run_id=run_id,
            book_sha=book_sha, items_pages=_items_pages(items_path, kinds),
        )
        path = detect_dir / "findings.cheap.jsonl"
        write_jsonl(
            path, rows_out,
            meta=find_rows.meta(
                run_id=run_id, companion=name, tier="cheap", book_sha=book_sha,
                counts=counts, inputs={"items": str(items_path), "stage_table": str(stages.source)},
            ),
        )
        ok(f"wrote {path} ({len(rows_out)} rows)")
    return {"counts": counts, "coverage_file": str(coverage_path), "findings": len(rows_out)}


def _items_pages(items_source, kinds) -> dict[str, int]:
    """``{item id: printed page}`` when items.json carries pages.

    ``book/prepare_book.py`` puts the pages under ``occurrences`` (an item can
    be named on several pages, and the first occurrence is the heading -- the
    cross-reference trap); older/hand-made tables put one page on the row.
    """
    data = read_json(items_source) if isinstance(items_source, (str, Path)) else items_source
    if isinstance(data, dict):
        data = data.get("items", data)
    entries = data.values() if isinstance(data, dict) else (data or [])
    out: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        base = item_base(entry.get("item") or entry.get("id") or entry.get("label") or "",
                         kinds or DEFAULT_KINDS)
        if not base:
            continue
        page = _first_page(entry)
        if page is not None:
            out[base] = page
    return out


def _first_page(entry: dict) -> int | None:
    """The item's own printed page, preferring the first heading occurrence."""
    for occ in entry.get("occurrences") or []:
        if isinstance(occ, dict):
            for key in ("page", "printed_page", "pdf_page"):
                if isinstance(occ.get(key), int):
                    return occ[key]
    for key in ("printed_page", "page", "book_page", "pdf_page"):
        if isinstance(entry.get(key), int):
            return entry[key]
    return None


# --------------------------------------------------------------------------
# lean tier
# --------------------------------------------------------------------------
def _run_lean(args, detect_dir, name, rows, stages, kinds, book_sha, entry, adapter) -> tuple[dict, int]:
    """ORDER_VIOLATION / SAME_STAGE_PRIMITIVE over deps.json. Never runs lake."""
    deps_path = Path(args.deps) if args.deps else detect_dir / "deps.json"
    if not deps_path.exists():
        raise CfError(
            f"no dependency dump at {deps_path}",
            hint=_dump_hint(name, entry, adapter, deps_path),
        )
    deps = DepsIndex.load(deps_path)
    report = analyze_stages.analyze(deps, stages, rows, key_only=not args.all_decls, kinds=kinds)
    report["companion"] = name
    report["book_sha"] = book_sha
    keydefs_path = detect_dir / "report_keydefs.json"
    write_json(keydefs_path, report)

    info(
        f"{name}: {report['key_declarations']} key definitional declaration(s), "
        f"{len(report['order_violations'])} order violation(s), "
        f"{len(report['same_stage_primitives'])} same-stage primitive(s)"
    )
    if report["decls_not_in_deps"]:
        warn(
            f"{len(report['decls_not_in_deps'])} declaration(s) in statements.jsonl are absent from "
            f"{deps_path.name} (the table and the built environment are out of step): "
            + ", ".join(report["decls_not_in_deps"][:5])
        )

    broad: list[dict] = []
    if args.broad:
        broad = analyze_stages.broad_scan(deps, stages)
        write_json(detect_dir / "report_broad_defs.json", broad)
        if not broad and not stages.repo_module_stages:
            warn("the stage table has no repo_module_stages for this companion; broad scan skipped")

    rows_out: list[dict] = []
    counts = {
        "order_violations": len(report["order_violations"]),
        "same_stage_primitives": len(report["same_stage_primitives"]),
    }
    if not args.no_findings:
        run_id = args.run_id or find_rows.default_run_id(name, "lean")
        rows_out = analyze_stages.build_finding_rows(
            report, deps, stages, companion=name, run_id=run_id, book_sha=book_sha
        )
        path = detect_dir / "findings.lean.jsonl"
        write_jsonl(
            path, rows_out,
            meta=find_rows.meta(
                run_id=run_id, companion=name, tier="lean", book_sha=book_sha,
                counts=counts, inputs={"deps": str(deps_path), "stage_table": str(stages.source)},
            ),
        )
        ok(f"wrote {path} ({len(rows_out)} rows)")

    exit_code = _report_unclassified(report, stages, args.allow_unclassified)
    return (
        {
            "counts": {
                "key_declarations": report["key_declarations"],
                "analysed": report["analysed"],
                **counts,
                "broad": len(broad),
                "unclassified_modules": len(report["unclassified_modules"]),
                "decls_not_in_deps": len(report["decls_not_in_deps"]),
            },
            "report": str(keydefs_path),
            "findings": len(rows_out),
            "deps": str(deps_path),
        },
        exit_code,
    )


def _report_unclassified(report: dict, stages: StageTable, allow: bool) -> int:
    """Stage-8 gate: an unclassified module hides every hit that goes through it."""
    unclassified = report["unclassified_modules"]
    if not unclassified:
        return 0
    listing = ", ".join(f"{m} ({n})" for m, n in list(unclassified.items())[:10])
    table = stages.source.name if stages.source else "the stage table"
    message = f"{len(unclassified)} module(s) are not classified by {table}: {listing}"
    if allow:
        warn(message + " -- continuing because --allow-unclassified was passed")
        return 0
    warn(message)
    warn(
        "an unclassified module silently suppresses every hit through it; add it to "
        f"module_stages in {stages.source} or re-run with --allow-unclassified"
    )
    return 1


# --------------------------------------------------------------------------
# DumpDeps.lean rendering (the one step that needs Lean; we never run it)
# --------------------------------------------------------------------------
def _lean_params(name: str, entry: dict, adapter: dict) -> dict:
    """Package / root module / module prefix for DumpDeps, from the adapter."""
    source = {**entry, **adapter}
    lean = source.get("lean") if isinstance(source.get("lean"), dict) else {}
    merged = {**source, **lean}
    return {
        "repo": merged.get("repo") or merged.get("repo_path") or f"<path to {name}>",
        "package": merged.get("package") or merged.get("lean_package") or "<Package>",
        "root_module": merged.get("root_module") or merged.get("module") or "<RootModule>",
        "module_prefix": merged.get("module_prefix") or merged.get("namespace") or "<Namespace>",
    }


def _dump_script() -> Path:
    return Path(__file__).resolve().parent.parent / "detect" / "dump_deps.sh"


def _dump_hint(name: str, entry: dict, adapter: dict, deps_path: Path) -> str:
    """The exact command that produces deps.json."""
    p = _lean_params(name, entry, adapter)
    return (
        f"{_dump_script()} --repo {p['repo']} --out {deps_path} --package {p['package']} "
        f"--root-module {p['root_module']} --module-prefix {p['module_prefix']}  "
        "(needs a built .lake in the companion; cf never builds or modifies it)"
    )


def _render_dump_deps(detect_dir: Path, name: str, entry: dict, adapter: dict, args) -> int:
    """Render the Lean template outside the repo tree and print the command line."""
    p = _lean_params(name, entry, adapter)
    deps_path = Path(args.deps) if args.deps else detect_dir / "deps.json"
    template = Path(__file__).resolve().parent.parent / "detect" / "DumpDeps.lean.tmpl"
    text = template.read_text(encoding="utf-8")
    for key, value in [("package", p["package"]), ("root_module", p["root_module"]),
                       ("module_prefix", p["module_prefix"]), ("output_path", str(deps_path))]:
        text = text.replace("{{" + key + "}}", str(value))
    out = detect_dir / "DumpDeps.lean"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    ok(f"rendered {out}")
    info(f"cd {p['repo']} && PATH=/usr/bin:$PATH timeout 900 lake env lean --run {out}")
    info(f"then: cf detect-proxies {name} --lean")
    if "<" in f"{p['package']}{p['root_module']}{p['module_prefix']}":
        warn("adapter.yaml does not name package / root_module / module_prefix; fill the placeholders in by hand")
    return 0


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------
def _write_state(campaign, name: str, summary: dict) -> None:
    """Merge the detector's counts into companions/<name>/state.json."""
    if not campaign.paths.companion_dir(name).exists():
        return
    cheap = summary.get("cheap", {}).get("counts", {})
    lean = summary.get("lean")
    with cf_state.update(campaign, name) as st:
        cf_state.merge_section(st, "coverage", {
            "definitions": cheap.get("definitions"),
            "def_stated": cheap.get("stated"),
            "def_bridge_only": cheap.get("bridge_only"),
            "def_missing": cheap.get("missing"),
        })
        detect = {"cheap": {**cheap, "findings": summary.get("cheap", {}).get("findings")}}
        if lean:
            detect["lean"] = {**lean.get("counts", {}), "findings": lean.get("findings")}
        cf_state.merge_section(st, "detect", detect)
        cf_state.note(st, "detect-proxies", tier="lean" if lean else "cheap",
                      proxy_candidates=cheap.get("proxy_candidates"))
