"""cf status -- what this campaign knows, read from state.json only.

New code (design/architecture.md S4).  `cf status` prints counts that were
written by the command that produced them; it never re-derives a number by
reading findings, because every counting error in the audit so far ran toward
"cleaner".  A number that is missing here means the stage has not run, not
that it found nothing.

    cf status
    cf status --companion ours
    cf status --json
"""
from __future__ import annotations

import json

from cflib import log, state as state_mod
from cflib.campaign import from_args
from cflib.errors import CfError, StateError
from cflib.hashing import short
from cflib.jsonio import read_json
from cflib.log import format_table

COLUMNS = ["companion", "commit", "adapter", "rows", "items", "single_source",
           "runs", "findings", "confirmed", "refuted", "routed", "stale", "updated"]


def register(subparsers) -> None:
    p = subparsers.add_parser("status", help="print every companion's state.json as a table")
    p.add_argument("--companion", help="show one companion in full")
    p.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    p.add_argument("--runs", action="store_true", help="also list runs and corpus sizes")


def run(args) -> int:
    campaign = from_args(args)
    paths = campaign.paths
    names = [args.companion] if args.companion else campaign.companion_names()

    states: dict[str, dict] = {}
    missing: list[str] = []
    for name in names:
        try:
            states[name] = state_mod.read_state(campaign, name)
        except StateError:
            missing.append(name)

    if args.as_json:
        print(json.dumps({
            "campaign": _campaign_facts(campaign),
            "companions": states,
            "not_onboarded": missing,
            "runs": _run_facts(campaign),
        }, indent=2, ensure_ascii=False))
        return 0

    facts = _campaign_facts(campaign)
    log.info(f"campaign : {campaign.root}")
    log.info(f"book     : {facts['book_id']}  "
             + (f"sha {short(facts['book_sha'])}  " if facts["book_sha"] else "(edition NOT pinned)  ")
             + f"offset {facts['offset']}")
    log.info(f"prepared : book.txt {_yn(facts['book_txt'])}   items.json "
             + (f"{facts['items']} items" if facts["items"] is not None else "no")
             + f"   errata {_yn(facts['errata'])}")
    if not facts["book_sha"]:
        log.warn("no book.sha256: the edition gate and the quote gate cannot run")

    if args.companion and args.companion not in states:
        raise CfError(f"companion {args.companion!r} has no state.json",
                      hint=f"run `cf onboard {args.companion} --repo <path> --adapter <kind>`"
                           + (f"; known: {', '.join(campaign.companion_names())}"
                              if campaign.companion_names() else ""))
    if not states:
        log.info("")
        log.info("no companions onboarded yet:")
        log.info("  cf onboard <name> --repo <path> --adapter docstring_tags")
        return 0

    rows = []
    for name in names:
        st = states.get(name)
        if st is None:
            rows.append([name] + ["-"] * (len(COLUMNS) - 2) + ["not onboarded"])
            continue
        summary = state_mod.summary_row(st)
        rows.append([summary.get(c) for c in COLUMNS])
    log.info("")
    log.info(format_table(COLUMNS, rows))

    for name in names:
        st = states.get(name)
        if st is None:
            continue
        if st.get("book_sha") and facts["book_sha"] and st["book_sha"] != facts["book_sha"]:
            log.warn(f"{name}: state.json book_sha {short(st['book_sha'])} != campaign "
                     f"{short(facts['book_sha'])} -- re-run `cf extract --companion {name}`")
        if st.get("stale_since_commit"):
            log.warn(f"{name}: statement table is stale since {short(st['stale_since_commit'], 7)}")
        routed = st.get("routed") or {}
        if routed.get("status") == "UNCHALLENGED":
            log.warn(f"{name}: routed findings are UNCHALLENGED -- do not report them upstream "
                     "(run `cf adjudicate` first)")

    if args.companion:
        _print_detail(states.get(args.companion) or {}, args.companion)
    if args.runs or args.verbose:
        _print_runs(campaign)
    return 0


# ---------------------------------------------------------------------------


def _yn(value) -> str:
    return "yes" if value else "no"


def _campaign_facts(campaign) -> dict:
    paths = campaign.paths
    items = None
    if paths.items_json.exists():
        data = read_json(paths.items_json, default={})
        items = len(data.get("items", data) if isinstance(data, dict) else data)
    return {
        "root": str(campaign.root),
        "book_id": campaign.book_id,
        "book_sha": campaign.book_sha,
        "offset": campaign.offset,
        "book_txt": paths.book_txt.exists(),
        "items": items,
        "errata": paths.errata_md.exists(),
        "companions": campaign.companion_names(),
        "hosts_allowlist": campaign.host_allowlist,
    }


def _run_facts(campaign) -> list[dict]:
    paths = campaign.paths
    out = []
    for run_id in paths.list_runs():
        corpus = paths.corpus_run_dir(run_id)
        receipt = paths.receipt_json(run_id)
        out.append({
            "run_id": run_id,
            "corpus_files": len(list(corpus.glob("*.json"))) - (1 if receipt.exists() else 0)
                            if corpus.is_dir() else 0,
            "receipt": receipt.exists(),
        })
    for corpus in sorted(paths.corpus_dir.glob("*")) if paths.corpus_dir.is_dir() else []:
        if corpus.is_dir() and corpus.name not in {r["run_id"] for r in out}:
            out.append({"run_id": corpus.name,
                        "corpus_files": len(list(corpus.glob("*.json"))),
                        "receipt": (corpus / "RECEIPT.json").exists(),
                        "planned": False})
    return out


def _print_runs(campaign) -> None:
    runs = _run_facts(campaign)
    if not runs:
        return
    log.info("")
    log.info(format_table(["run", "corpus files", "receipt"],
                          [[r["run_id"], r["corpus_files"], _yn(r["receipt"])] for r in runs]))


def _print_detail(state: dict, name: str) -> None:
    if not state:
        raise CfError(f"no state.json for companion {name!r}",
                      hint=f"run `cf onboard {name} --repo <path> --adapter <kind>`")
    log.info("")
    log.info(f"--- {name} ---")
    for section in state_mod.SECTIONS:
        body = state.get(section)
        if not body:
            continue
        log.info(f"{section}:")
        for key, value in body.items():
            if isinstance(value, dict):
                value = json.dumps(value, ensure_ascii=False)
            log.info(f"    {key}: {value}")
    history = state.get("history") or []
    if history:
        log.info("history:")
        for entry in history[-5:]:
            extra = {k: v for k, v in entry.items() if k not in {"at", "event"}}
            log.info(f"    {entry['at']}  {entry['event']}  {extra if extra else ''}".rstrip())
