"""cf extract -- Stage 2: companion repos -> companions/<name>/statements.jsonl.

New code.  The originals were one-off cells that wrote a table for one repo
(``extract_ours.py`` / ``extract_teorth.py``, transcript b174aa13 lines 5547
and 5564; the ``adam.jsonl`` generator, transcript 11323fe3 line 3102); this
command runs whichever adapter a companion declares, validates every row
against schema v2 and records the counts in state.json.

Idempotence (design/architecture.md S6): the table is keyed on
``adapter.key()``, which hashes the adapter kind, its config, the repo commit
*and* the content the adapter reads -- the globbed Lean sources, or the
ledger / target-refinement / snippet / geometry exports of a table-driven
companion.  An unchanged key is a no-op; ``--force`` re-extracts anyway.

    cf extract                      # every companion in campaign.yaml
    cf extract --companion ours     # one
    cf extract --dry-run            # counts only, writes nothing

Gate: the table is written only when every row validates (schema v2, the
campaign's item kinds, the campaign's book_sha).  Rows whose tag the strict
item grammar refuses are never invented into item ids; they are counted and
listed under ``extract.rejects`` in state.json.
"""

from __future__ import annotations

from pathlib import Path

from cflib import log, state as state_mod
from cflib.adapters_api import load as load_adapter
from cflib.campaign import from_args
from cflib.errors import CfError
from cflib.jsonio import write_jsonl

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
    p.add_argument("--allow-missing-book-sha", action="store_true",
                   help="extract before `cf book` has pinned the edition (rows carry book_sha: null)")


def run(args) -> int:
    campaign = from_args(args)
    names = _select(campaign, getattr(args, "companions", None))
    book_sha = campaign.book_sha
    if not book_sha and not args.allow_missing_book_sha:
        raise CfError(
            "campaign.yaml has no book.sha256, so rows cannot carry the edition they were read against",
            hint="run `cf book <id> --pdf <path>` first, or pass --allow-missing-book-sha")

    summary: list[tuple] = []
    changed = 0
    failures: list[tuple[str, Exception]] = []
    for name in names:
        # One unparseable companion must not take the stage down: `cf extract`
        # used to let the error propagate, so a single broken adapter.yaml
        # aborted the whole command with exit 2 and wrote nothing at all for the
        # healthy companions.
        try:
            result = _extract_one(campaign, name, book_sha, args)
        except Exception as exc:  # noqa: BLE001 - deliberate: see the comment above
            failures.append((name, exc))
            label = "" if isinstance(exc, CfError) else f"{exc.__class__.__name__}: "
            log.warn(f"{name}: {label}{exc}")
            summary.append((name, "FAILED", "-", "-", "-", "-"))
            continue
        summary.append((name, result["status"], result.get("rows", "-"),
                        result.get("items", "-"), result.get("sorry", "-"),
                        result.get("untagged", "-")))
        changed += 1 if result["status"] == "written" else 0

    log.info(log.format_table(
        ["companion", "status", "rows", "items", "sorry", "untagged"], summary))
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


def _select(campaign, wanted) -> list[str]:
    """Companion names to extract, in campaign.yaml order."""
    known = campaign.companion_names()
    if not known:
        raise CfError("no companions are onboarded",
                      hint="run `cf onboard <name> --repo <path> --adapter <kind>` first")
    if not wanted:
        return known
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise CfError(f"unknown companion(s): {', '.join(unknown)}",
                      hint=f"known: {', '.join(known)}")
    return [n for n in known if n in set(wanted)]


def _extract_one(campaign, name: str, book_sha: str | None, args) -> dict:
    paths = campaign.paths
    adapter_yaml = paths.adapter_yaml(name)
    if not adapter_yaml.exists():
        raise CfError(f"{name}: {adapter_yaml} does not exist",
                      hint=f"run `cf onboard {name} --repo <path> --adapter <kind>`")
    adapter = load_adapter(adapter_yaml, campaign=campaign)
    repo = Path(args.repo).expanduser() if args.repo else adapter.resolve_repo()
    out = paths.statements_jsonl(name)

    key = adapter.key()
    previous = (state_mod.read_state(campaign, name, missing_ok=True).get("extract") or {})
    fresh = (previous.get("key") == key and out.exists() and not args.force and not args.repo)
    if fresh:
        log.detail(f"{name}: up to date ({previous.get('rows')} rows, key {key[7:19]})")
        return {"status": "up-to-date", **{k: previous.get(k) for k in
                                           ("rows", "items", "sorry", "untagged")}}

    rows = list(adapter.extract(repo, book_sha))
    problems = validate_rows(rows, kinds=campaign.kinds + ("In-text",),
                             require_book_sha=bool(book_sha))
    if problems:
        raise CfError(f"{name}: statements.jsonl failed schema v2 validation "
                      f"({len(problems)} problems); nothing was written\n  " +
                      "\n  ".join(problems[:10]),
                      hint="fix the adapter config (glob, tag pattern, kinds) and re-run")
    rows.sort(key=row_sort_key)

    report = adapter.dry_run(repo)
    counts = _counts(rows, report, key, repo, adapter)
    if args.dry_run:
        log.info(f"{name}: {counts['rows']} rows / {counts['items']} items "
                 f"(dry run; {out} not written)")
        return {"status": "dry-run", **counts}

    write_jsonl(out, rows)
    log.ok(f"{name}: wrote {out} ({counts['rows']} rows, {counts['items']} items)")
    with state_mod.update(campaign, name) as st:
        st["repo"] = str(repo)
        st["commit"] = counts["commit"]
        st["book_sha"] = book_sha
        if counts.get("ledger_sha"):
            st["ledger_sha"] = counts["ledger_sha"]
            st["stale_since_commit"] = counts["commit"] if counts.get("ledger_stale") else None
        state_mod.set_section(st, "extract", counts)
        state_mod.note(st, "extract", rows=counts["rows"], items=counts["items"])
    return {"status": "written", **counts}


def _counts(rows: list[dict], report: dict, key: str, repo: Path, adapter) -> dict:
    """The extract section of state.json: counts, key and staleness."""
    counts = {
        "key": key,
        "adapter": adapter.kind,
        "repo": str(repo),
        "commit": report.get("commit"),
        "rows": len(rows),
        "items": len({r["item"] for r in rows}),
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
