"""cf status -- what this workspace knows, read from state.json only.

New code (design/architecture.md S4).  `cf status` prints counts that were
written by the command that produced them; it never re-derives a number by
reading findings, because every counting error in the audit so far ran toward
"cleaner".  A number that is missing here means the stage has not run, not
that it found nothing.

It also prints the SOURCES table and the "unconfirmed sources" block: discovery
writes what it learned about a source nobody had catalogued with
``confirmed: false``, and the only place that would otherwise be visible is a
YAML file nobody reads.

    cf status
    cf status --companion ours
    cf status --source tao-measure-gsm126-published
    cf status --json
"""
from __future__ import annotations

import json

from cflib import log, state as state_mod
from cflib.errors import CfError, StateError
from cflib.hashing import short
from cflib.jsonio import read_json
from cflib.log import format_table
from cflib.workspace import from_args

COLUMNS = ["companion", "commit", "adapter", "sources", "anchor", "rows", "items",
           "single_source", "runs", "findings", "confirmed", "refuted", "routed",
           "stale", "updated"]

SOURCE_COLUMNS = ["source", "sha", "offset", "conf", "items", "errata", "companions",
                  "role", "confirmed"]


def register(subparsers) -> None:
    p = subparsers.add_parser("status", help="print the sources table and every companion's state")
    p.add_argument("--companion", help="show one companion in full")
    p.add_argument("--source", help="show one source's row only")
    p.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    p.add_argument("--runs", action="store_true", help="also list runs and corpus sizes")


def run(args) -> int:
    workspace = from_args(args)
    names = [args.companion] if args.companion else workspace.companion_names()

    states: dict[str, dict] = {}
    missing: list[str] = []
    for name in names:
        try:
            states[name] = state_mod.read_state(workspace, name)
        except StateError:
            missing.append(name)

    if args.as_json:
        print(json.dumps({
            "workspace": _workspace_facts(workspace),
            "sources": _source_facts(workspace),
            "companions": states,
            "not_onboarded": missing,
            "runs": _run_facts(workspace),
        }, indent=2, ensure_ascii=False))
        return 0

    facts = _workspace_facts(workspace)
    log.info(f"workspace : {workspace.root}   ({facts['name']})")
    sources = _source_facts(workspace)
    if args.source:
        sources = [s for s in sources if s["id"] == args.source]
        if not sources:
            raise CfError(f"unknown source {args.source!r}",
                          hint=f"known: {', '.join(workspace.source_ids()) or '(none)'}")
    if not sources:
        log.info("sources   : none yet")
        log.info("")
        log.info("next:  cf onboard <path to a companion repo>")
    else:
        log.info("")
        log.info(format_table(SOURCE_COLUMNS, [
            [s["id"], short(s["sha256"], 8) if s["sha256"] else "-", s["offset"],
             f"{s['offset_confidence']:.2f}", s["items"] if s["items"] is not None else "-",
             _yn(s["errata"]), ",".join(s["companions"]) or "-", s["role"],
             "yes" if s["confirmed"] else "NO"]
            for s in sources]))
        for s in sources:
            if not s["prepared"]:
                log.warn(f"{s['id']}: not prepared (no text.txt) -- the quote gate cannot run. "
                         f"cf sources refresh {s['id']}")
            elif not s["sha256"]:
                log.warn(f"{s['id']}: no sha256 -- the source gate cannot run")
            if s["text_drift"]:
                log.warn(f"{s['id']}: the prepared text no longer hashes to meta.json's "
                         "text_sha256 (poppler changed?) -- "
                         f"cf sources refresh {s['id']} --force")
        shared = workspace.shared_sources()
        log.info("")
        for sid, companions in shared.items():
            log.info(f"differential possible on {sid}: {', '.join(companions)}")
        if not shared and len(workspace.companions) > 1:
            log.info("no two companions share a source, so no differential is possible")

    if args.companion and args.companion not in states:
        raise CfError(f"companion {args.companion!r} has no state.json",
                      hint=f"run `cf onboard <repo> --name {args.companion}`"
                           + (f"; known: {', '.join(workspace.companion_names())}"
                              if workspace.companion_names() else ""))
    if not states:
        log.info("")
        log.info("no companions onboarded yet:")
        log.info("  cf onboard <path to a companion repo>")
        _print_unconfirmed(workspace)
        return 0

    rows = []
    for name in names:
        st = states.get(name)
        if st is None:
            rows.append([name] + ["-"] * (len(COLUMNS) - 2) + ["not onboarded"])
            continue
        row = state_mod.summary_row(st)
        # summary_row counts the sources the last EXTRACT touched. The workspace
        # is what the companion is BOUND to, and the two differ the moment a
        # source is added and nothing has re-extracted yet -- printing only the
        # first contradicts the source table directly above, which already lists
        # this companion against the new source.
        bound = len(workspace.sources_of(name))
        done = row.get("sources") or 0
        row["sources"] = str(bound) if bound == done else f"{bound} ({done} extracted)"
        rows.append([row.get(c) for c in COLUMNS])
    log.info("")
    log.info(format_table(COLUMNS, rows))

    for name in names:
        bound = set(workspace.sources_of(name))
        done = set((states.get(name) or {}).get("sources") or {})
        missing = sorted(bound - done)
        if missing:
            log.warn(f"{name}: bound to {', '.join(missing)} but no rows extracted against "
                     f"{'it' if len(missing) == 1 else 'them'} -- run "
                     f"`cf extract --companion {name}`")

    for name in names:
        st = states.get(name)
        if st is None:
            continue
        for sid, body in (st.get("sources") or {}).items():
            want = (workspace.sources.get(sid).sha256 if sid in workspace.sources else None)
            if body.get("sha") and want and body["sha"] != want:
                log.warn(f"{name}: state.json sources.{sid}.sha {short(body['sha'])} != workspace "
                         f"{short(want)} -- re-run `cf extract --companion {name}`")
        if st.get("stale_since_commit"):
            log.warn(f"{name}: statement table is stale since {short(st['stale_since_commit'], 7)}")
        routed = st.get("routed") or {}
        if routed.get("status") == "UNCHALLENGED":
            log.warn(f"{name}: routed findings are UNCHALLENGED -- do not report them upstream "
                     "(run `cf adjudicate` first)")

    _print_unconfirmed(workspace)
    if args.companion:
        _print_detail(states.get(args.companion) or {}, args.companion)
    if args.runs or args.verbose:
        _print_runs(workspace)
    return 0


def _print_unconfirmed(workspace) -> None:
    """Discovery writes `confirmed: false`; this is where a human sees it."""
    unconfirmed = workspace.unconfirmed_sources()
    if not unconfirmed:
        return
    log.info("")
    log.warn("unconfirmed sources (discovered, nobody has checked them)")
    for ref in unconfirmed:
        origin = ", ".join((ref.discovered or {}).get("from") or []) or ref.registry
        log.info(f"  {ref.id}   sha {short(ref.sha256, 8) + '…' if ref.sha256 else '(none)'}"
                 f"   from {origin}")
        log.info(f"      confirm with:  cf sources show {ref.id}")
        log.info(f"                     cf sources set {ref.id} confirmed=true")
        log.info("      or promote it into the skill's registry/sources.yaml")


# ---------------------------------------------------------------------------


def _yn(value) -> str:
    return "yes" if value else "no"


def _workspace_facts(workspace) -> dict:
    return {
        "root": str(workspace.root),
        "name": workspace.name,
        "companions": workspace.companion_names(),
        "sources": workspace.source_ids(),
        "shared_sources": workspace.shared_sources(),
        "hosts_allowlist": workspace.host_allowlist,
    }


def _source_facts(workspace) -> list[dict]:
    """One row per source, read from workspace.yaml and the prepared meta.json."""
    paths = workspace.paths
    out = []
    for sid, ref in workspace.sources.items():
        items_json = read_json(paths.source_items(sid), default={}) or {}
        meta = read_json(paths.source_meta(sid), default={}) or {}
        text = paths.source_text(sid)
        drift = False
        if text.exists() and meta.get("text_sha256"):
            from cflib.hashing import sha256_text
            drift = sha256_text(text.read_text(encoding="utf-8", errors="ignore")) != meta["text_sha256"]
        out.append({
            "id": sid,
            "sha256": ref.sha256,
            "title": ref.title,
            "edition": ref.edition,
            "offset": ref.offset,
            "offset_confidence": ref.offset_confidence,
            "offset_method": ref.offset_method,
            "items": items_json.get("count"),
            "kinds": list(ref.kinds),
            "chapters": list(ref.chapters),
            "errata": paths.source_errata_md(sid).exists(),
            "errata_url": ref.errata_url,
            "prepared": text.exists(),
            "text_drift": drift,
            "companions": workspace.companions_of(sid),
            "role": ref.role,
            "confirmed": ref.confirmed,
            "registry": ref.registry,
            "pdf": str(ref.pdf) if ref.pdf else None,
        })
    return out


def _run_facts(workspace) -> list[dict]:
    paths = workspace.paths
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


def _print_runs(workspace) -> None:
    runs = _run_facts(workspace)
    if not runs:
        return
    log.info("")
    log.info(format_table(["run", "corpus files", "receipt"],
                          [[r["run_id"], r["corpus_files"], _yn(r["receipt"])] for r in runs]))


def _print_detail(state: dict, name: str) -> None:
    if not state:
        raise CfError(f"no state.json for companion {name!r}",
                      hint=f"run `cf onboard <repo> --name {name}`")
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
