"""cf run -- send a planned run to the server (or stage the local fallback).

New code.  The procedure is the one in the v1 campaign's runbook, written down
here as references/runbook-server.md and turned into a command that cannot skip
its own safety steps:

    cf run <run-id> --host examplebox --i-have-consent           # prints the plan
    cf run <run-id> --host examplebox --i-have-consent --execute # up to the smoke test
    cf run <run-id> --host examplebox --i-have-consent --stage raise --smoke-verified --execute
    cf run <run-id> --status                                  # this run's progress
    cf run <run-id> --local                                   # <100 items, Workflow fleet

Three refusals are the point of the command:

* a host that is not in ``workspace.yaml hosts.allowlist``;
* a run without ``--i-have-consent`` (recorded in run.yaml with who and when),
  because this rsyncs private Lean sources and copyrighted source text to a box
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
from cflib.workspace import from_args
from cflib.errors import CfError
from cflib.yamlio import read_yaml, write_yaml

from ledger import workflow_args
from run import fleet_local, manifest, remote, status as run_status

DEFAULT_WORKERS = 6
DEFAULT_REVIEWERS = 4
#: references/traps.md C3: past this many concurrent sessions the API returns
#: HTTP 409, per ACCOUNT, across workspaces. It was documentation only: `cf run`
#: accepted any --workers/--reviewers and planned the `tend-control limits` call
#: without a word (24 + 12 = 36 sessions planned, no warning).
DEFAULT_SESSION_CAP = 16


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "run", help="transfer a planned run to a host and drive tend (dry run by default)")
    p.add_argument("run_id")
    p.add_argument("--host", help="a host named in workspace.yaml hosts.allowlist")
    p.add_argument("--i-have-consent", action="store_true",
                   help="the owner of that host agreed to this run; recorded in run.yaml")
    p.add_argument("--include-sources", action="store_true",
                   help="also transfer the companion repos (read-only)")
    p.add_argument("--include-source-text", action="store_true", dest="include_source_text",
                   help="also transfer this run's source text/images (COPYRIGHTED)")
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
    p.add_argument("--over-session-cap", action="store_true",
                   help="plan more concurrent sessions than defaults.max_concurrent_sessions "
                        "(the account-wide limit; past it the API returns 409s)")
    p.add_argument("--execute", action="store_true", help="actually run the commands")
    p.add_argument("--status", action="store_true", dest="show_status",
                   help="report this run's progress instead of planning it")
    p.add_argument("--local", action="store_true",
                   help="stage the local Workflow fleet instead (<100 items)")
    p.add_argument("--force", action="store_true", help="ignore the local fleet size limit")
    p.add_argument("--journal", help="with --status: a Workflow directory to count")


def run(args) -> int:
    workspace = from_args(args)
    paths = workspace.paths
    run_yaml_path = paths.run_yaml(args.run_id)
    if not run_yaml_path.exists():
        raise CfError(f"no run.yaml for run {args.run_id!r} ({run_yaml_path})",
                      hint=f"plan it first: cf plan-run --mode <mode> --run-id {args.run_id}")
    data = read_yaml(run_yaml_path)
    if not isinstance(data, dict):
        raise CfError(f"{run_yaml_path} is not a YAML mapping")

    if args.show_status:
        return _status(workspace, args, data)
    planned_host = data.get("host") if isinstance(data.get("host"), dict) else {}
    if args.local or (not args.host and planned_host.get("kind") == "local"):
        # A run planned with `--host local` carries this machine's absolute paths;
        # it can only be run by the local Workflow fleet.
        return _local(workspace, args, data)
    return _server(workspace, args, data, run_yaml_path)


# ---------------------------------------------------------------------------
# status


def _status(workspace, args, data: dict) -> int:
    paths = workspace.paths
    planned = run_status.planned_status(paths, args.run_id)
    corpus = run_status.corpus_status(paths, args.run_id)
    journal = None
    if args.journal:
        journal = run_status.journal_counts(Path(args.journal))
    log.info(run_status.render(args.run_id, planned, corpus, journal))

    # `--status` is read-only and answers from the workspace directory, so a run
    # planned for `local` (or for a host the workspace never allowlisted) must
    # still print its status instead of failing the allowlist check.
    host_name = args.host or planned.get("host")
    if host_name in (None, "", "local"):
        return 0
    try:
        host = manifest.resolve_host(workspace, host_name)
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


def _local_repos(workspace, data: dict) -> list[dict]:
    """``args.repos``: {name, repo, statements, conventions} per companion."""
    paths = workspace.paths
    names = [str(r) for r in (data.get("repos") or [])] or workspace.companion_names()
    out = []
    for name in names:
        # `Workspace.companion` returns a CompanionRef, not a mapping: a
        # companion listed in run.yaml but absent from workspace.yaml still gets
        # a row, with an empty repo, rather than taking the command down.
        repo = ""
        try:
            repo = str(workspace.companion(name).repo or "")
        except CfError:
            pass
        out.append({
            "name": name,
            "repo": repo,
            "statements": str(paths.statements_jsonl(name)),
            "conventions": _conventions_text(paths, name),
        })
    return out


def _run_source(workspace, data: dict) -> str:
    """The source_id this run was planned for (run.yaml is the record)."""
    ids = manifest.run_source_ids(data)
    if ids:
        return ids[0]
    listed = workspace.source_ids()
    return listed[0] if len(listed) == 1 else ""


def _local_item_meta(workspace, data: dict) -> tuple[dict[str, dict], str | None]:
    """``{item: {repos, pdf_pages, source_id}}`` from the universe ``plan-run`` used.

    ``pairs/<source>/queue.json`` rows carry ``repos`` outright;
    ``pairs/<source>/targets.<name>.json`` rows carry ``peers`` and the file
    records whose targets they are, so the owning companion has to be put back.
    Without this every item reached the workflow with ``repos: []`` and the
    prompt could not say which repositories formalise it.
    """
    import json

    paths = workspace.paths
    companion = data.get("companion")
    source_id = _run_source(workspace, data)
    if not source_id:
        # `_local` refuses a run.yaml that names no source, with the flag that
        # settles it; that refusal must be what the user sees, not the path
        # helpers' "unsafe source_id ''" from the queue path built on the way.
        return {}, None
    files: list[Path] = []
    if str(data.get("mode") or "").startswith("attack") and companion:
        files.append(paths.targets_json(source_id, str(companion)))
    else:
        files.append(paths.queue_json(source_id))
        files += [paths.targets_json(source_id, n) for n in workspace.companion_names()]

    attack = str(data.get("mode") or "").startswith("attack")
    meta: dict[str, dict] = {}
    for src in files:
        if not Path(src).exists():
            continue
        doc = json.loads(Path(src).read_text(encoding="utf-8"))
        rows = doc.get("items") if isinstance(doc, dict) else doc
        owner = str(doc.get("companion") or "") if isinstance(doc, dict) else ""
        for row in rows or []:
            if not isinstance(row, dict) or not row.get("item"):
                continue
            slot = meta.setdefault(str(row["item"]),
                                   {"repos": [], "pdf_pages": [],
                                    "source_id": str(row.get("source_id") or source_id)})
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
    pairs = paths.pairs_jsonl(source_id)
    return meta, (str(pairs) if pairs.exists() else None)


def _local(workspace, args, data: dict) -> int:
    paths = workspace.paths
    run_dir = paths.run_dir(args.run_id)
    item_meta, pairs = _local_item_meta(workspace, data)
    gate_block = data.get("gate") if isinstance(data.get("gate"), dict) else {}
    gate_config = gate_block.get("local_config") or str(run_dir / "gate.local.json")
    scratch = run_dir / "self-gate"
    scratch.mkdir(parents=True, exist_ok=True)
    gate_script = _skill_root() / "scripts" / "gate" / "verify_findings.py"
    source_id = _run_source(workspace, data)
    if not source_id:
        raise CfError(f"runs/{args.run_id}/run.yaml names no source",
                      hint="re-plan with `cf plan-run --source <id>`")
    src = workspace.source(source_id)
    # The local fleet reads the errata off THIS machine, so `manifest`'s transfer
    # guard never sees it -- and the same unconfirmed page would be handed to the
    # workers as the document that decides whether a deviation is a correction.
    errata, errata_withheld = workflow_args.errata_for_prompt(
        paths.source_errata_md(source_id), paths.source_dir(source_id))
    if errata_withheld:
        log.warn(f"errata for {source_id} came from the companion repo and is unconfirmed: "
                 f"not given to the fleet (`cf sources set {source_id} errata_url=<url>` "
                 "to confirm it)")
    errata_path = Path(errata) if errata else None
    fleet = fleet_local.build(
        run_dir,
        args.run_id,
        source={
            "source_id": source_id,
            "title": src.title or source_id,
            "edition": src.edition,
            "source_sha": src.sha256,
            "text": str(paths.source_text(source_id)),
            "pages_dir": str(paths.source_pages_dir(source_id)),
            "page_offset": src.offset,
            "errata": str(errata_path) if errata_path and errata_path.exists() else "",
            "errata_withheld": errata_withheld,
        },
        repos=_local_repos(workspace, data),
        mode=data.get("mode"),
        gate={
            # The local fleet gates on THIS machine, so the command points at the
            # skill's own gate and the run's gate.local.json, never the host copy.
            "command": (f"python3 {gate_script} --gate-config {gate_config} "
                        "--findings " + str(scratch) + "/${TASK_ID}.json"),
            "config": str(gate_config),
            # The workflow builds the worker's output enums from these three, so
            # the fleet is constrained by the run's own gate: a hand-written copy
            # in the script drifted from the preset and every recovered file then
            # failed the gate the tool prints as the next step.
            "kinds": list(gate_block.get("kinds") or []),
            "verdicts": list(gate_block.get("verdicts") or []),
            "schema_version": int(gate_block.get("schema_version")
                                  or fleet_local.DEFAULT_SCHEMA_VERSION),
            "witness_required": bool(gate_block.get("witness_required")),
            "quote_min_chars": 25,
            "scratch_dir": str(scratch),
        },
        pairs=pairs,
        item_meta=item_meta,
        force=args.force,
        max_items=int(workspace.default("local_fleet_max_items",
                                       fleet_local.DEFAULT_MAX_ITEMS)),
    )
    # The SOURCE GATE: run.yaml records the sha the run was planned against, and
    # a workspace whose source has since been re-pinned must not be handed to a
    # fleet that will stamp findings with the old edition.
    for sid, sha in manifest.source_shas(workspace, data).items():
        workspace.require_source_sha(sid, sha, f"runs/{args.run_id}/run.yaml")
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


def _server(workspace, args, data: dict, run_yaml_path: Path) -> int:
    if not args.host:
        raise CfError("--host is required (or use --local / --status)",
                      hint="allowlisted hosts: "
                           + (", ".join(workspace.host_allowlist) or "(none in workspace.yaml)"))
    if not args.i_have_consent:
        raise CfError(
            "refusing to plan a server run without --i-have-consent",
            hint="this transfers private Lean sources and (with --include-source-text) copyrighted "
                 "source text to a machine you do not own; the box owner must agree first")
    # The allowlist is the hard rule, so it is checked FIRST: a host the workspace
    # never authorised must be refused as such, not as a planning mismatch.
    host = manifest.resolve_host(workspace, args.host)
    manifest.check_planned_host(data, args.host)
    names = manifest.remote_names(data, args.run_id)
    planned_consent = manifest.planned_consent(data)
    if planned_consent.get("given"):
        log.detail(f"run.yaml already records consent at {planned_consent.get('at')}")

    for sid, sha in manifest.source_shas(workspace, data).items():
        workspace.require_source_sha(sid, sha, f"runs/{args.run_id}/run.yaml")

    # `--stage monitor` is a separate invocation, and the companion-tree integrity
    # check hangs off the repo entries: run.yaml's record of what this run
    # transfers decides, not whether the flag was typed again.
    include_sources, notes = manifest.include_sources_for(
        data, requested=args.include_sources, stage=args.stage)

    entries, warnings = manifest.build_manifest(
        workspace_dir=Path(workspace.root), skill_dir=_skill_root(), run_id=args.run_id,
        run=data, host=host, include_sources=include_sources,
        include_texts=args.include_source_text,
        companions=manifest.companion_repos(workspace, data.get("repos")))
    warnings += notes
    warnings += manifest.gate_config_warnings(Path(workspace.root), args.run_id, host, data)
    # The bypass was asked for at PLAN time, possibly days earlier and by someone
    # else: only run.yaml and the manifest fields carried it, and the printed plan
    # said nothing, so an operator typing --execute could not see that the agents
    # will run with approvals and the sandbox off.
    permissions = manifest.agent_permissions(run_yaml_path.parent)
    if permissions["permission_bypass_in_wrapper"]:
        warnings.append(permissions["permission_bypass"])
    # The model the record claims and the model the root launches, before
    # anything is transferred: a dry run prints it, --execute refuses.
    mismatch = _check_model(run_yaml_path.parent, data, execute=bool(args.execute))
    if mismatch:
        warnings.append(mismatch)

    tasks = manifest.task_count(Path(workspace.root), args.run_id, data)
    budget = manifest.check_budget_cap(workspace, data, args.max_cost, args.no_budget_cap)
    disk = manifest.disk_budget(entries, tasks, host)
    concurrency = _concurrency(workspace, data, args)

    steps = remote.plan_commands(host=host, run_id=args.run_id, run=data, entries=entries,
                                 names=names, budget=budget, concurrency=concurrency)
    stages = remote.stages_upto(args.stage)
    if args.stage == "raise" and not args.smoke_verified:
        raise CfError(
            "--stage raise needs --smoke-verified",
            hint="watch ONE task go worker -> gate -> reviewer -> merge at limits 1/1 first; "
                 "the model ids in run.yaml may never have been exercised on that host")

    _print_plan(args, host, entries, warnings, tasks, budget, disk, concurrency,
                include_sources=include_sources)
    log.info("")
    log.info(remote.render(steps, stages))

    executed: list[dict] = []
    refused = None
    if args.execute:
        missing = [e for e in entries if e["required"] and not e["exists"]]
        if missing:
            raise CfError("refusing to execute with missing components: "
                          + ", ".join(e["name"] for e in missing))
        log.info("")
        try:
            remote.execute(steps, stages, dry_run=False, results=executed)
        except CfError as exc:
            # A refused companion-tree digest or tend version is the outcome most
            # worth a durable record, and it used to be the only one that wrote
            # nothing: the results list went out with the exception.
            refused = exc

    _record(run_yaml_path, data, args=args, host=host, entries=entries, budget=budget,
            disk=disk, concurrency=concurrency, names=names, stages=stages, executed=executed,
            include_sources=include_sources)
    _record_state(workspace, args.run_id, data, tasks, host, budget)
    if refused is not None:
        raise refused

    if not args.execute:
        log.info("")
        log.info("dry run: nothing was transferred or started. Add --execute to run the block above.")
    return 0


def _skill_root() -> Path:
    from cflib.paths import skill_root

    return skill_root()


def _check_model(run_dir: Path, data: dict, *, execute: bool) -> str | None:
    """Refuse (or, in a dry run, report) a tend root that names another model.

    ``run.yaml``'s ``model`` is what the report attributes the findings to, and
    the generated ``.tend/<role>-agent.yaml``'s ``model_name`` is what the
    tend-agent backend actually launches.  A root re-planned after ``--model``
    changed leaves the two disagreeing, and then the record names a tier the
    fleet may never have been given (references/method.md S10: "the yields belong
    to the models that produced them").  Nothing is compared when run.yaml records
    no model -- that is the honest "the bridge's own default" case, which the yaml
    fills with the tend-agent default, not with a contradiction.
    """
    from plan import models as models_mod

    recorded = str(data.get("model") or "")
    if not recorded:
        return None
    staged = models_mod.tend_agent_model(run_dir / "tend-root", role="worker")
    if not staged or staged == recorded:
        return None
    message = (f"run.yaml records model {recorded!r} but the staged tend root's "
               f".tend/worker-agent.yaml names {staged!r}")
    if execute:
        raise CfError(
            f"refusing to launch: {message}",
            hint=f"re-plan so the two agree: `cf plan-run --preset {data.get('preset')} "
                 f"--run-id {data.get('run_id')} --model {recorded} --force`")
    return message + " -- re-plan with `cf plan-run ... --force` before launching"


def _concurrency(workspace, data: dict, args) -> dict:
    """run.yaml wins, then workspace defaults, then the audit's 6/4.

    Also checks the account-wide session cap: workers and reviewers are separate
    sessions and the limit is per ACCOUNT, across workspaces, so the sum is what
    matters.  The workspace default (8 + 5 = 13) happens to sit under it, which
    is why nothing has bitten yet.
    """
    block = data.get("concurrency") if isinstance(data.get("concurrency"), dict) else {}
    workers = int(args.workers or block.get("workers")
                  or workspace.default("max_concurrent_workers", DEFAULT_WORKERS))
    reviewers = int(args.reviewers or block.get("reviewers")
                    or workspace.default("max_concurrent_reviewers", DEFAULT_REVIEWERS))
    cap = int(workspace.default("max_concurrent_sessions", DEFAULT_SESSION_CAP) or 0)
    out = {"workers": workers, "reviewers": reviewers,
           "sessions": workers + reviewers, "session_cap": cap}
    if cap and workers + reviewers > cap:
        if not getattr(args, "over_session_cap", False):
            raise CfError(
                f"{workers} workers + {reviewers} reviewers = {workers + reviewers} concurrent "
                f"sessions, over the account cap of {cap}",
                hint="the limit is per ACCOUNT and across workspaces; past it the API returns "
                     "409s and tend loses agents mid-run (references/traps.md C3). Lower "
                     "--workers/--reviewers, or pass --over-session-cap deliberately")
        log.warn(f"{workers + reviewers} concurrent sessions is over the account cap of {cap}; "
                 "--over-session-cap was passed, so expect 409s")
        out["over_session_cap"] = True
    return out


def _print_plan(args, host, entries, warnings, tasks, budget, disk, concurrency,
                *, include_sources: bool | None = None) -> None:
    log.info(f"run {args.run_id} -> {host['name']} ({host['ssh']}:{host['remote_root']})")
    log.info(f"  tasks: {tasks}   concurrency: {concurrency['workers']} workers / "
             f"{concurrency['reviewers']} reviewers "
             f"({concurrency.get('sessions')} sessions, cap {concurrency.get('session_cap')})")
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
    if not (args.include_sources if include_sources is None else include_sources):
        log.info("     (companion sources NOT transferred; --include-sources)")
    if not args.include_source_text:
        log.info("     (source text NOT transferred; --include-source-text)")
    for warning in warnings:
        log.warn(warning)


def _record(run_yaml_path: Path, data: dict, *, args, host, entries, budget, disk,
            concurrency, names, stages, executed, include_sources: bool | None = None) -> None:
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
        "include_sources": bool(args.include_sources if include_sources is None
                                else include_sources),
        "include_source_text": bool(args.include_source_text),
        # WHAT LEFT THIS MACHINE: the consent record names the exact source ids,
        # not "the book", because a workspace can hold two editions of one work
        # and this run transferred at most the ones it reads.
        "sources_transferred": (manifest.run_source_ids(data)
                                if args.include_source_text else []),
        "source_access": manifest.source_access(data),
        # WHAT THE AGENTS MAY DO: the bridge denies by default, and a run whose
        # wrapper opted out of approvals and the sandbox has to say so here.
        "agents": manifest.agent_permissions(run_yaml_path.parent),
        "budget": budget,
        "disk_budget": disk,
        "concurrency": concurrency,
        "gate_out_of_tree": f"{host['remote_root']}/{names['gate']}",
        # tree_sha256 is in the record because the monitor stage compares the host
        # against it: re-walking the local checkout in a later invocation would
        # compare against a tree the operator may have pulled since.
        "transfer": manifest.transfer_record(entries),
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


def _record_state(workspace, run_id: str, data: dict, tasks: int, host, budget) -> None:
    """Record the run against every companion it covers."""
    names = _companions(data)
    for name in names:
        try:
            with state_mod.update(workspace, str(name)) as state:
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
