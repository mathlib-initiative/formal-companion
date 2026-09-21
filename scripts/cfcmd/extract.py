"""cf extract -- Stage 2: companion repos -> companions/<name>/statements.jsonl.

New code.  The originals were one-off cells that wrote a table for one repo
(``extract_ours.py`` / ``extract_teorth.py``, transcript b174aa13 lines 5547
and 5564; the ``adam.jsonl`` generator, transcript 11323fe3 line 3102); this
command runs whichever adapter a companion declares, validates every row
against schema v2 and records the counts in state.json.

Idempotence (docs/design/stage_map.md S4): the table is keyed on
``adapter.key()``, which hashes the adapter kind, its config, the repo commit,
the SOURCE SHAS it is bound to *and* the content the adapter reads -- the
globbed Lean sources, or the ledger / target-refinement / snippet / geometry
exports of a table-driven companion.  An unchanged key is a no-op; ``--force``
re-extracts anyway.

    cf extract                      # every companion in workspace.yaml
    cf extract --companion ours     # one
    cf extract --dry-run            # counts only, writes nothing

Every row carries the pair ``(source_id, source_sha)``: a companion is audited
against a LIST of sources (docs/design/archive/multi-source-model.md R1/R4), the same
item label means different things in different sources, and a table whose rows
do not say which source they are about cannot be paired, gated or tallied.

Gate: the table is written only when every row validates (schema v2, the item
kinds of the source it is attributed to, that source's sha256).  Rows whose tag
the strict item grammar refuses are never invented into item ids; they are
counted and listed under ``extract.rejects`` in state.json.
"""

from __future__ import annotations

from pathlib import Path

from cflib import log, state as state_mod
from cflib.adapters_api import load as load_adapter
from cflib.errors import CfError
from cflib.jsonio import write_jsonl
from cflib.workspace import from_args

from adapters.base import row_sort_key, validate_rows


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "extract", help="run each companion's adapter into statements.jsonl")
    p.add_argument("--companion", action="append", dest="companions", metavar="NAME",
                   help="only this companion (repeatable); default: all")
    p.add_argument("--repo", help="parse this tree instead of adapter.yaml's repo "
                                  "(a worktree pinned at another commit)")
    p.add_argument("--force", action="store_true",
                   help="re-extract even when the adapter key is unchanged")
    p.add_argument("--dry-run", action="store_true",
                   help="report the parse without writing statements.jsonl")
    p.add_argument("--allow-missing-source-sha", action="store_true",
                   help="extract before a source's PDF has been fingerprinted "
                        "(rows carry source_sha: null and no later stage will accept them)")


def run(args) -> int:
    workspace = from_args(args)
    names = _select(workspace, getattr(args, "companions", None))

    summary: list[tuple] = []
    changed = 0
    failures: list[tuple[str, Exception]] = []
    for name in names:
        # One unparseable companion must not take the stage down: `cf extract`
        # used to let the error propagate, so a single broken adapter.yaml
        # aborted the whole command with exit 2 and wrote nothing at all for the
        # healthy companions.
        try:
            result = _extract_one(workspace, name, args)
        except Exception as exc:  # noqa: BLE001 - deliberate: see the comment above
            failures.append((name, exc))
            label = "" if isinstance(exc, CfError) else f"{exc.__class__.__name__}: "
            log.warn(f"{name}: {label}{exc}")
            summary.append((name, "FAILED", "-", "-", "-", "-", "-"))
            continue
        summary.append((name, result["status"],
                        ", ".join(result.get("source_ids") or []) or "-",
                        result.get("rows", "-"),
                        result.get("items", "-"), result.get("sorry", "-"),
                        result.get("untagged", "-")))
        changed += 1 if result["status"] == "written" else 0

    log.info(log.format_table(
        ["companion", "status", "sources", "rows", "items", "sorry", "untagged"], summary))
    if not args.dry_run:
        log.ok(f"{changed} of {len(names)} companion tables rewritten "
               f"({len(names) - changed - len(failures)} already up to date"
               + (f", {len(failures)} FAILED" if failures else "") + ")")
    if failures:
        log.warn(f"{len(failures)} companion(s) failed: "
                 + ", ".join(name for name, _ in failures))
        for name, exc in failures:
            hint = getattr(exc, "hint", "") or ""
            log.detail(f"  {name}: {exc}" + (f"\n    hint: {hint}" if hint else ""))
        return 1
    return 0


# ---------------------------------------------------------------------------


def _select(workspace, wanted) -> list[str]:
    """Companion names to extract, in workspace.yaml order."""
    known = workspace.companion_names()
    if not known:
        raise CfError("no companions are onboarded",
                      hint="run `cf onboard <path-to-repo>` first")
    if not wanted:
        return known
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise CfError(f"unknown companion(s): {', '.join(unknown)}",
                      hint=f"known: {', '.join(known)}")
    return [n for n in known if n in set(wanted)]


def _bind_sources(workspace, name: str, args) -> tuple[dict, str]:
    """``({source_id: source_sha}, anchor_source)`` for one companion.

    A source with no sha256 has not been fingerprinted, so a row stamped with it
    would claim an edition nobody has identified.  That is refused here unless
    --allow-missing-source-sha says otherwise, because the alternative is a
    finding judged against pages from another printing.
    """
    companion = workspace.companion(name)
    anchor = workspace.anchor_source(name)
    shas: dict[str, str | None] = {}
    missing: list[str] = []
    for sid in companion.sources or ((anchor,) if anchor else ()):
        sha = workspace.source(sid).sha256
        if not sha:
            missing.append(sid)
        shas[sid] = sha
    if missing and not getattr(args, "allow_missing_source_sha", False):
        raise CfError(
            f"{name}: source(s) {', '.join(missing)} have no sha256, so its rows cannot "
            f"carry the edition they were read against",
            hint=f"run `cf sources refresh {missing[0]}` with the PDF reachable, or pass "
                 "--allow-missing-source-sha")
    return shas, anchor


def _extract_one(workspace, name: str, args) -> dict:
    paths = workspace.paths
    adapter_yaml = paths.adapter_yaml(name)
    if not adapter_yaml.exists():
        raise CfError(f"{name}: {adapter_yaml} does not exist",
                      hint=f"run `cf onboard <path-to-repo> --name {name}`")
    shas, anchor = _bind_sources(workspace, name, args)
    adapter = load_adapter(adapter_yaml, workspace=workspace)
    if anchor:
        # The workspace's anchor is fresher than adapter.yaml's sources.default:
        # `cf onboard` re-decides it against the extracted rows.
        adapter.bind_default_source(anchor)
    repo = Path(args.repo).expanduser() if args.repo else adapter.resolve_repo()
    out = paths.statements_jsonl(name)

    key = adapter.key()
    previous = (state_mod.read_state(workspace, name, missing_ok=True).get("extract") or {})
    fresh = (previous.get("key") == key and out.exists() and not args.force and not args.repo)
    if fresh:
        log.detail(f"{name}: up to date ({previous.get('rows')} rows, key {key[7:19]})")
        return {"status": "up-to-date", "source_ids": list(shas),
                **{k: previous.get(k) for k in ("rows", "items", "sorry", "untagged")}}

    rows = list(adapter.extract(repo, sources=shas))
    problems = validate_rows(rows, kinds=_kinds_of(workspace, shas, anchor),
                             require_source_sha=all(shas.values()) and bool(shas))
    problems += _source_problems(rows, shas, name)
    if problems:
        raise CfError(f"{name}: statements.jsonl failed schema v2 validation "
                      f"({len(problems)} problems); nothing was written\n  " +
                      "\n  ".join(problems[:10]),
                      hint="fix the adapter config (glob, tag pattern, sources.map) and re-run")
    rows.sort(key=row_sort_key)

    report = adapter.dry_run(repo, sources=shas)
    counts = _counts(rows, report, key, repo, adapter)
    if args.dry_run:
        log.info(f"{name}: {counts['rows']} rows / {counts['items']} items "
                 f"(dry run; {out} not written)")
        return {"status": "dry-run", **counts}

    write_jsonl(out, rows)
    log.ok(f"{name}: wrote {out} ({counts['rows']} rows, {counts['items']} items "
           f"over {', '.join(counts['source_ids'])})")
    with state_mod.update(workspace, name) as st:
        st["repo"] = str(repo)
        st["commit"] = counts["commit"]
        st["anchor_source"] = anchor or st.get("anchor_source")
        if counts.get("ledger_sha"):
            st["ledger_sha"] = counts["ledger_sha"]
            st["stale_since_commit"] = counts["commit"] if counts.get("ledger_stale") else None
        state_mod.set_section(st, "extract", counts)
        for sid in counts["source_ids"]:
            per_source = [r for r in rows if r.get("source_id") == sid]
            state_mod.source_section(st, sid, {
                "sha": shas.get(sid),
                "rows": len(per_source),
                "items": len({r["item"] for r in per_source}),
            })
        state_mod.note(st, "extract", rows=counts["rows"], items=counts["items"],
                       sources=len(counts["source_ids"]))
    return {"status": "written", **counts}


def _kinds_of(workspace, shas, anchor: str) -> tuple[str, ...]:
    """The item kinds every bound source allows, plus In-text.

    The grammar is per source, and ``validate_rows`` checks one list; the union
    is the only sound choice when a table spans two sources, and the per-source
    check belongs to the adapter that parsed the tag.
    """
    kinds: list[str] = []
    for sid in list(shas) or ([anchor] if anchor else []):
        for kind in workspace.kinds(sid):
            if kind not in kinds:
                kinds.append(kind)
    if "In-text" not in kinds:
        kinds.append("In-text")
    return tuple(kinds)


def _source_problems(rows, shas, name: str) -> list[str]:
    """Rows attributed to a source this companion is not bound to, or mis-stamped.

    This is the edition gate at the table's own boundary: a row whose
    ``source_sha`` is not the sha of the source it names has been read against
    other bytes than it claims, and no later stage can notice.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        sid = str(row.get("source_id") or "")
        if sid and sid not in shas and sid not in seen:
            seen.add(sid)
            problems.append(
                f"row {i}: source_id {sid!r} is not one of {name}'s sources "
                f"({', '.join(shas) or 'none'})")
        expected = shas.get(sid)
        if expected and row.get("source_sha") and row["source_sha"] != expected:
            problems.append(
                f"row {i}: source_sha {str(row['source_sha'])[:8]} is not "
                f"sources.{sid}.sha256 {expected[:8]}")
            break
    return problems


def _counts(rows: list[dict], report: dict, key: str, repo: Path, adapter) -> dict:
    """The extract section of state.json: counts, key and staleness."""
    counts = {
        "key": key,
        "adapter": adapter.kind,
        "repo": str(repo),
        "commit": report.get("commit"),
        # sorted, not set-ordered: state.json is committed and diffed.
        "source_ids": sorted({str(r.get("source_id") or "") for r in rows} - {""}),
        "rows_by_source": report.get("rows_by_source"),
        "rows": len(rows),
        # an item is the pair (source_id, item): counting the labels alone would
        # merge two sources' Exercise 1.3.19 into one.
        "items": len({(r.get("source_id"), r["item"]) for r in rows}),
        "declarations": report.get("declarations"),
        "tagged": report.get("tagged"),
        "untagged": report.get("untagged"),
        "private": report.get("private"),
        "anonymous": sum(1 for r in rows if r.get("anonymous")),
        "sorry": sum(1 for r in rows if r.get("sorry")),
        "sorry_gap": sum(1 for r in rows if r.get("sorry_class") == "gap"),
        "sorry_exercise": sum(1 for r in rows if r.get("sorry_class") == "exercise"),
        "anchors": report.get("anchors") or {
            "own": sum(1 for r in rows if r.get("anchor_source") == "own"),
            "crossref": sum(1 for r in rows if r.get("anchor_source") == "crossref"),
            "none": sum(1 for r in rows if r.get("anchor_source") == "none"),
        },
        "item_kinds": report.get("item_kinds"),
        "rejected": report.get("rejected", report.get("unnumbered_labels", 0)),
        "rejects": report.get("rejects") or [],
    }
    for extra in ("ledger_sha", "ledger_sha_in_refinement", "ledger_stale",
                  "uncurated_items", "missing_snippet"):
        if report.get(extra) is not None:
            counts[extra] = report[extra]
    return {k: v for k, v in counts.items() if v is not None}
