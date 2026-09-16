"""cf run -- send a planned run to the server (or stage the local fallback).

New code.  The procedure is the one in scratchpad/ffmain/tao-diff-run/RUNBOOK.md,
turned into a command that cannot skip its own safety steps:

    cf run <run-id> --host ffmain --i-have-consent            # prints the plan
    cf run <run-id> --host ffmain --i-have-consent --execute  # up to the smoke test
    cf run <run-id> --host ffmain --i-have-consent --stage raise --smoke-verified --execute
    cf run <run-id> --status                                  # this run's progress
    cf run <run-id> --local                                   # <100 items, Workflow fleet

Three refusals are the point of the command:

* a host that is not in ``campaign.yaml hosts.allowlist``;
* a run without ``--i-have-consent`` (recorded in run.yaml with who and when),
  because this rsyncs private Lean sources and copyrighted book text to a box
  the operator does not own;
* raising concurrency before a human watched one task go worker -> gate ->
  reviewer -> merge (``--smoke-verified``), and before a spend ceiling exists.

The default is a DRY RUN that prints the exact commands.  Nothing reaches the
network without ``--execute``.
"""
from __future__ import annotations

import getpass
from pathlib import Path

from cflib import log
from cflib import state as state_mod
from cflib.campaign import from_args
from cflib.errors import CfError
from cflib.yamlio import read_yaml, write_yaml

from run import fleet_local, manifest, remote, status as run_status

DEFAULT_WORKERS = 6
DEFAULT_REVIEWERS = 4


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "run", help="transfer a planned run to a host and drive tend (dry run by default)")
    p.add_argument("run_id")
    p.add_argument("--host", help="a host named in campaign.yaml hosts.allowlist")
    p.add_argument("--i-have-consent", action="store_true",
                   help="the owner of that host agreed to this run; recorded in run.yaml")
    p.add_argument("--include-sources", action="store_true",
                   help="also transfer the companion repos (read-only)")
    p.add_argument("--include-book", action="store_true",
                   help="also transfer the book text/images (COPYRIGHTED)")
    p.add_argument("--max-cost", type=float, help="spend ceiling for this run")
    p.add_argument("--no-budget-cap", action="store_true",
                   help="acknowledge running without a ceiling")
    p.add_argument("--stage", default="smoke",
                   choices=("preflight", "transfer", "verify", "smoke", "raise", "monitor", "stop"),
                   help="how far to go (default: smoke)")
    p.add_argument("--smoke-verified", action="store_true",
                   help="a human watched one task through the pipeline; required for --stage raise")
    p.add_argument("--workers", type=int, help="worker limit when raising (default from run.yaml)")
    p.add_argument("--reviewers", type=int, help="reviewer limit when raising")
    p.add_argument("--execute", action="store_true", help="actually run the commands")
    p.add_argument("--status", action="store_true", dest="show_status",
                   help="report this run's progress instead of planning it")
    p.add_argument("--local", action="store_true",
                   help="stage the local Workflow fleet instead (<100 items)")
    p.add_argument("--force", action="store_true", help="ignore the local fleet size limit")
    p.add_argument("--journal", help="with --status: a Workflow directory to count")


def run(args) -> int:
    campaign = from_args(args)
    paths = campaign.paths
    run_yaml_path = paths.run_yaml(args.run_id)
    if not run_yaml_path.exists():
        raise CfError(f"no run.yaml for run {args.run_id!r} ({run_yaml_path})",
                      hint=f"plan it first: cf plan-run --mode <mode> --run-id {args.run_id}")
    data = read_yaml(run_yaml_path)
    if not isinstance(data, dict):
        raise CfError(f"{run_yaml_path} is not a YAML mapping")

    if args.show_status:
        return _status(campaign, args, data)
    planned_host = data.get("host") if isinstance(data.get("host"), dict) else {}
    if args.local or (not args.host and planned_host.get("kind") == "local"):
        # A run planned with `--host local` carries this machine's absolute paths;
        # it can only be run by the local Workflow fleet.
        return _local(campaign, args, data)
    return _server(campaign, args, data, run_yaml_path)


# ---------------------------------------------------------------------------
# status


def _status(campaign, args, data: dict) -> int:
    paths = campaign.paths
    planned = run_status.planned_status(paths, args.run_id)
    corpus = run_status.corpus_status(paths, args.run_id)
    journal = None
    if args.journal:
        journal = run_status.journal_counts(Path(args.journal))
    log.info(run_status.render(args.run_id, planned, corpus, journal))

    # `--status` is read-only and answers from the campaign directory, so a run
    # planned for `local` (or for a host the campaign never allowlisted) must
    # still print its status instead of failing the allowlist check.
    host_name = args.host or planned.get("host")
    if host_name in (None, "", "local"):
        return 0
    try:
        host = manifest.resolve_host(campaign, host_name)
    except CfError as exc:
        log.warn(f"no remote status: {exc}")
        return 0

    names = manifest.remote_names(data, args.run_id)
    steps = run_status.remote_steps(host, args.run_id, names)
    log.info("")
    log.info(f"# remote status on {host_name}"
             + ("" if args.execute else "  (add --execute to run these)"))
    for step in steps:
        log.info(f"# {step['title']}\n{step['shell']}")
    if args.execute:
        remote.execute([{**s, "stage": "monitor"} for s in steps], ["monitor"], dry_run=False)
    return 0


# ---------------------------------------------------------------------------
# local fallback


#: how much of conventions.md travels into the workflow prompt (it is inlined,
#: not read from disk: a Workflow script has no filesystem access)
CONVENTIONS_CHARS = 4000


def _conventions_text(paths, name: str) -> str:
    """The companion's conventions.md, inlined for the workflow prompt."""
    path = paths.conventions_md(name)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) > CONVENTIONS_CHARS:
        text = text[:CONVENTIONS_CHARS].rstrip() + "\n[...truncated; full text: " + str(path) + "]"
    return text


def _local_repos(campaign, data: dict) -> list[dict]:
    """``args.repos``: {name, repo, statements, conventions} per companion."""
    paths = campaign.paths
    names = [str(r) for r in (data.get("repos") or [])] or campaign.companion_names()
    out = []
    for name in names:
        entry = {}
        try:
            entry = campaign.companion(name)
        except CfError:
            pass
        out.append({
            "name": name,
            "repo": str(entry.get("repo") or entry.get("path") or ""),
            "statements": str(paths.statements_jsonl(name)),
            "conventions": _conventions_text(paths, name),
        })
    return out


def _local_item_meta(campaign, data: dict) -> tuple[dict[str, dict], str | None]:
    """``{item: {repos, pdf_pages}}`` from the same universe ``plan-run`` used.

    ``pairs/queue.json`` rows carry ``repos`` outright; ``pairs/targets.<name>.json``
    rows carry ``peers`` and the file records whose targets they are, so the owning
    companion has to be put back.  Without this every item reached the workflow with
    ``repos: []`` and the prompt could not say which repositories formalise it.
    """
    import json

    paths = campaign.paths
    companion = data.get("companion")
    sources: list[Path] = []
    if str(data.get("mode") or "").startswith("attack") and companion:
        sources.append(paths.targets_json(str(companion)))
    else:
        sources.append(paths.queue_json)
        sources += [paths.targets_json(n) for n in campaign.companion_names()]

    attack = str(data.get("mode") or "").startswith("attack")
    meta: dict[str, dict] = {}
    for src in sources:
        if not Path(src).exists():
            continue
        doc = json.loads(Path(src).read_text(encoding="utf-8"))
        rows = doc.get("items") if isinstance(doc, dict) else doc
        owner = str(doc.get("companion") or "") if isinstance(doc, dict) else ""
        for row in rows or []:
            if not isinstance(row, dict) or not row.get("item"):
                continue
            slot = meta.setdefault(str(row["item"]), {"repos": [], "pdf_pages": []})
            # An attack audits ONE companion: naming its peers here would put
            # repositories in the prompt that the run is not allowed to judge.
            found = ([str(companion)] if attack
                     else [owner] + list(row.get("repos") or []) + list(row.get("peers") or []))
            for repo in found:
                if repo and repo not in slot["repos"]:
                    slot["repos"].append(str(repo))
            for page in row.get("pdf_pages") or []:
                if page not in slot["pdf_pages"]:
                    slot["pdf_pages"].append(page)
    pairs = paths.pairs_jsonl
    return meta, (str(pairs) if pairs.exists() else None)


def _local(campaign, args, data: dict) -> int:
    paths = campaign.paths
    run_dir = paths.run_dir(args.run_id)
    item_meta, pairs = _local_item_meta(campaign, data)
    gate_block = data.get("gate") if isinstance(data.get("gate"), dict) else {}
    gate_config = gate_block.get("local_config") or str(run_dir / "gate.local.json")
    scratch = run_dir / "self-gate"
    scratch.mkdir(parents=True, exist_ok=True)
    gate_script = _skill_root() / "scripts" / "gate" / "verify_findings.py"
    book = data.get("book") if isinstance(data.get("book"), dict) else {}
    fleet = fleet_local.build(
        run_dir,
        args.run_id,
        campaign={
            "book_id": campaign.book_id,
            "book_title": str(campaign.book.get("title") or campaign.book_id),
            "book_sha": campaign.book_sha,
            "book_layout_txt": str(paths.book_txt),
            "book_pages_dir": str(paths.pages_dir),
            "pdf_page_offset": campaign.offset,
            "errata": str(paths.errata_md) if paths.errata_md.exists() else "",
        },
        repos=_local_repos(campaign, data),
        mode=data.get("mode"),
        gate={
            # The local fleet gates on THIS machine, so the command points at the
            # skill's own gate and the run's gate.local.json, never the host copy.
            "command": (f"python3 {gate_script} --gate-config {gate_config} "
                        "--findings " + str(scratch) + "/${TASK_ID}.json"),
            "kinds": list(gate_block.get("kinds") or []),
            "witness_required": bool(gate_block.get("witness_required")),
            "quote_min_chars": 25,
            "scratch_dir": str(scratch),
        },
        pairs=pairs,
        item_meta=item_meta,
        force=args.force,
        max_items=int(campaign.default("local_fleet_max_items",
                                       fleet_local.DEFAULT_MAX_ITEMS)),
    )
    if book.get("sha256") and campaign.book_sha:
        campaign.require_book_sha(str(book["sha256"]), f"runs/{args.run_id}/run.yaml")
    path = fleet_local.write(run_dir, fleet)
    missing = fleet_local.missing_keys(fleet)
    if missing:
        log.warn("fleet args are missing keys the workflow reads: " + ", ".join(missing[:8]))
    log.info(fleet_local.instructions(
        path, fleet["count"], run_id=args.run_id, gate_config=str(gate_config),
        corpus_dir=str(paths.corpus_run_dir(args.run_id))))
    return 0


# ---------------------------------------------------------------------------
# server


def _server(campaign, args, data: dict, run_yaml_path: Path) -> int:
    if not args.host:
        raise CfError("--host is required (or use --local / --status)",
                      hint="allowlisted hosts: "
                           + (", ".join(campaign.host_allowlist) or "(none in campaign.yaml)"))
    if not args.i_have_consent:
        raise CfError(
            "refusing to plan a server run without --i-have-consent",
            hint="this transfers private Lean sources and (with --include-book) copyrighted "
                 "book text to a machine you do not own; the box owner must agree first")
    # The allowlist is the hard rule, so it is checked FIRST: a host the campaign
    # never authorised must be refused as such, not as a planning mismatch.
    host = manifest.resolve_host(campaign, args.host)
    manifest.check_planned_host(data, args.host)
    names = manifest.remote_names(data, args.run_id)
    planned_consent = manifest.planned_consent(data)
    if planned_consent.get("given"):
        log.detail(f"run.yaml already records consent at {planned_consent.get('at')}")

    sha = manifest.book_sha(campaign, data)
    if sha and campaign.book_sha:
        campaign.require_book_sha(sha, f"runs/{args.run_id}/run.yaml")

    entries, warnings = manifest.build_manifest(
        campaign_dir=Path(campaign.root), skill_dir=_skill_root(), run_id=args.run_id,
        run=data, host=host, include_sources=args.include_sources,
        include_book=args.include_book, companions=manifest.companion_repos(campaign))
    warnings += manifest.gate_config_warnings(Path(campaign.root), args.run_id, host, data)

    tasks = manifest.task_count(Path(campaign.root), args.run_id, data)
    budget = manifest.check_budget_cap(campaign, data, args.max_cost, args.no_budget_cap)
    disk = manifest.disk_budget(entries, tasks, host)
    concurrency = _concurrency(campaign, data, args)

    steps = remote.plan_commands(host=host, run_id=args.run_id, run=data, entries=entries,
                                 names=names, budget=budget, concurrency=concurrency)
    stages = remote.stages_upto(args.stage)
    if args.stage == "raise" and not args.smoke_verified:
        raise CfError(
            "--stage raise needs --smoke-verified",
            hint="watch ONE task go worker -> gate -> reviewer -> merge at limits 1/1 first; "
                 "the model ids in run.yaml may never have been exercised on that host")

    _print_plan(args, host, entries, warnings, tasks, budget, disk, concurrency)
    log.info("")
    log.info(remote.render(steps, stages))

    executed = []
    if args.execute:
        missing = [e for e in entries if e["required"] and not e["exists"]]
        if missing:
            raise CfError("refusing to execute with missing components: "
                          + ", ".join(e["name"] for e in missing))
        log.info("")
        executed = remote.execute(steps, stages, dry_run=False)

    _record(run_yaml_path, data, args=args, host=host, entries=entries, budget=budget,
            disk=disk, concurrency=concurrency, names=names, stages=stages, executed=executed)
    _record_state(campaign, args.run_id, data, tasks, host, budget)

    if not args.execute:
        log.info("")
        log.info("dry run: nothing was transferred or started. Add --execute to run the block above.")
    return 0


def _skill_root() -> Path:
    from cflib.paths import skill_root

    return skill_root()


def _concurrency(campaign, data: dict, args) -> dict:
    """run.yaml wins, then campaign defaults, then the audit's 6/4."""
    block = data.get("concurrency") if isinstance(data.get("concurrency"), dict) else {}
    return {
        "workers": int(args.workers or block.get("workers")
                       or campaign.default("max_concurrent_workers", DEFAULT_WORKERS)),
        "reviewers": int(args.reviewers or block.get("reviewers")
                         or campaign.default("max_concurrent_reviewers", DEFAULT_REVIEWERS)),
    }


def _print_plan(args, host, entries, warnings, tasks, budget, disk, concurrency) -> None:
    log.info(f"run {args.run_id} -> {host['name']} ({host['ssh']}:{host['remote_root']})")
    log.info(f"  tasks: {tasks}   concurrency: {concurrency['workers']} workers / "
             f"{concurrency['reviewers']} reviewers")
    log.info("  budget: " + (f"{budget['max_cost']} {budget['currency']}"
                              if budget["max_cost"] is not None else "NONE (acknowledged)"))
    log.info(f"  disk:   {disk['human']}")
    if disk["over_budget"]:
        log.warn("disk budget exceeded: " + disk["advice"])
    log.info("  transfer:")
    for entry in entries:
        mark = " " if entry["exists"] else "!"
        log.info(f"   {mark} {entry['name']:<14} {entry['files']:>5} files  "
                 f"{entry['bytes']:>10} B  -> {entry['dest']}")
    if not args.include_sources:
        log.info("     (companion sources NOT transferred; --include-sources)")
    if not args.include_book:
        log.info("     (book text NOT transferred; --include-book)")
    for warning in warnings:
        log.warn(warning)


def _record(run_yaml_path: Path, data: dict, *, args, host, entries, budget, disk,
            concurrency, names, stages, executed) -> None:
    """Write everything this command decided back into run.yaml."""
    block = data.get("run") if isinstance(data.get("run"), dict) else {}
    block.update({
        "planned_at": state_mod.now(),
        "host": host["name"],
        "ssh": host["ssh"],
        "remote_root": host["remote_root"],
        "remote_names": names,
        "consent": {"i_have_consent": True, "operator": getpass.getuser(),
                    "recorded_at": state_mod.now()},
        "include_sources": bool(args.include_sources),
        "include_book": bool(args.include_book),
        "book_access": manifest.book_access(data),
        "budget": budget,
        "disk_budget": disk,
        "concurrency": concurrency,
        "gate_out_of_tree": f"{host['remote_root']}/{names['gate']}",
        "transfer": [{k: e[k] for k in ("name", "src", "dest", "files", "bytes", "exists")}
                     for e in entries],
        "stages_planned": list(stages),
        "smoke_verified": bool(args.smoke_verified) or bool(block.get("smoke_verified")),
    })
    if executed:
        block.setdefault("executed", []).extend(executed)
    data["run"] = block
    write_yaml(run_yaml_path, data)


def _companions(data: dict) -> list[str]:
    """The companions a run covers (plan-run writes `companion`; older run.yaml `companions`)."""
    for key in ("companions", "companion", "repos"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return [value]
        if isinstance(value, list) and value:
            return [str(v) for v in value]
    return []


def _record_state(campaign, run_id: str, data: dict, tasks: int, host, budget) -> None:
    """Record the run against every companion it covers."""
    names = _companions(data)
    for name in names:
        try:
            with state_mod.update(campaign, str(name)) as state:
                state_mod.record_run(state, run_id, {
                    "mode": data.get("mode"),
                    "tasks": tasks,
                    "host": host["name"],
                    "max_cost": budget.get("max_cost"),
                    "planned_at": state_mod.now(),
                })
                state_mod.note(state, "run planned", run=run_id, host=host["name"])
        except Exception as exc:  # a companion without state.json must not fail the plan
            log.detail(f"state.json for {name}: {exc}")
