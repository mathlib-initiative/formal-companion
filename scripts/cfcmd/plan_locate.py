"""``cf plan-locate`` -- plan one locate run from a sourceset and a universe.

New code, implementing docs/proposals/wikipedia-locate.md section 3 (the locate
planner: "sourceset-scoped, not companion-scoped") with the three presets
``locate-inventory``, ``locate`` and ``locate-challenge``:

    cf plan-locate --preset locate-inventory --sourceset wpm --universe u1 --run-id inv-1
    cf plan-locate --preset locate --sourceset wpm --universe u1 --inventory-run inv-1
    cf plan-locate --preset locate-challenge --sourceset wpm --universe u1 --challenge-of loc-1

Writes ``runs/<run-id>/`` in the layout of references/locate-documents.md
section 9: ``run.yaml`` (with its ``locate`` block: the sourceset pin, the
universe and ``corpus.sqlite`` shas), ``tasks/*.yaml``, ``prompts/``,
``gate.json`` (the run host's paths) and ``gate.local.json`` (this machine's),
a challenge run's ``challenge-plan.json``, and a tend root whose ``gate/``
holds the task manifests, the gate's helpers and the vendored packages.  The
counts land in the sourceset's own ``state.json``.

Nothing here contacts a host or calls a model; planning is local and
deterministic, and re-planning the same inputs rewrites the same tasks,
manifests and gate configuration.
"""
from __future__ import annotations

import datetime as _dt

from cflib import log, vocab
from cflib.errors import CfError
from cflib.hashing import sha256_text
from cflib.jsonio import write_json
from cflib.workspace import from_args
from cflib.yamlio import read_yaml, write_yaml

from locate import inputs, plan as lplan, presets as lp, prompts as lprompts, state as ss_state
from locate import tend_root as ltr
from plan import models as models_mod
from run import manifest as manifest_mod

NAME = "plan-locate"


def register(subparsers) -> None:
    p = subparsers.add_parser(NAME, help="plan a locate run: tasks, manifests, gate.json, tend root",
                              description=__doc__)
    p.add_argument("--preset", required=True, choices=sorted(lp.LOCATE_PRESETS),
                   help="locate-inventory (L3a), locate (L3b) or locate-challenge (L5)")
    p.add_argument("--sourceset", required=True, help="the sourceset id")
    p.add_argument("--manifest-version", type=int, default=None,
                   help="the sourceset version to pin (default: the newest)")
    p.add_argument("--universe", required=True, help="the universe id")
    p.add_argument("--source", dest="sources", action="append", default=[],
                   help="plan only this source (repeatable; default: every rendered source)")
    p.add_argument("--inventory-run", default=None,
                   help="locate: the harvested, gated inventory run whose passages are frozen")
    p.add_argument("--challenge-of", dest="challenge_of", action="append", default=[],
                   help="locate-challenge: a harvested locate run to challenge (repeatable)")
    p.add_argument("--per-task", type=int, default=vocab.MAX_PASSAGES_PER_LOCATE_TASK,
                   help=f"locate: passages per task (1..{vocab.MAX_PASSAGES_PER_LOCATE_TASK})")
    p.add_argument("--subject-only", action="store_true",
                   help="locate: the article-level tier, one subject passage per article")
    p.add_argument("--blind", action="store_true",
                   help="the G1-blind arm: the gate's curated-hit rule off (the candidates "
                        "files must have been written with `cf locate candidates --blind`)")
    p.add_argument("--seed", type=int, default=0,
                   help="locate-challenge: the stratified sample's seed (default 0)")
    p.add_argument("--sample-rate", type=float, default=0.2,
                   help="locate-challenge: the fraction of each stratum re-judged (default 0.2)")
    p.add_argument("--max-open-tasks", type=int, default=None,
                   help="chain depends_on so tend holds at most N open tasks (waves)")
    p.add_argument("--run-id", default=None, help="default: <preset>-<YYYY-MM-DD>")
    p.add_argument("--host", default=lplan.LOCAL,
                   help="run host; must be in workspace.yaml hosts.allowlist")
    p.add_argument("--model", default=None, help="model id to record and pass to the bridge")
    p.add_argument("--max-cost", type=float, default=None,
                   help="USD spend cap recorded in run.yaml (required before the run is sent)")
    p.add_argument("--i-have-consent", action="store_true",
                   help="the host's owner agreed to this run; recorded in run.yaml")
    p.add_argument("--allow-sandbox-bypass", action="store_true",
                   help="stage CF_ALLOW_SANDBOX_BYPASS=1: the agents run with approvals off")


def _check_mode_flags(args) -> None:
    if args.preset == vocab.LOCATE and not args.inventory_run:
        raise CfError("a locate run reads a frozen inventory",
                      hint="pass --inventory-run <the harvested locate-inventory run>")
    if args.preset != vocab.LOCATE and (args.inventory_run or args.subject_only):
        raise CfError("--inventory-run and --subject-only belong to the locate preset")
    if args.preset == vocab.LOCATE_CHALLENGE and not args.challenge_of:
        raise CfError("a challenge judges harvested locate runs",
                      hint="pass --challenge-of <run-id> (repeatable)")
    if args.preset != vocab.LOCATE_CHALLENGE and args.challenge_of:
        raise CfError("--challenge-of belongs to the locate-challenge preset")


def _host(ws, host: str) -> dict:
    if host == lplan.LOCAL:
        return {"remote_root": "", "ssh": ""}
    resolved = manifest_mod.resolve_host(ws, host)
    return {"remote_root": resolved["remote_root"], "ssh": resolved["ssh"]}


def run(args) -> int:
    ws = from_args(args)
    paths = ws.paths
    _check_mode_flags(args)
    preset = lp.get(args.preset)
    run_id = args.run_id or f"{preset.name}-{_dt.date.today().isoformat()}"
    manifest = inputs.load_manifest(paths, args.sourceset, args.manifest_version)
    universe = inputs.load_universe(paths, args.universe)
    db_path = paths.corpus_db(args.universe)
    with inputs.CorpusDB(db_path) as db:
        db_sha = db.sha256()
        if db.meta.get("universe_sha256") not in (None, universe.sha256):
            raise CfError(f"corpus.sqlite of {universe.id} was built for another universe",
                          hint="rebuild it after the universe changed")
    host = _host(ws, args.host)
    layouts = lplan.host_layout(paths, host=args.host, run_id=run_id,
                                sourceset_id=args.sourceset, universe_id=args.universe,
                                inventory_run=args.inventory_run, **host)
    warnings: list[str] = []

    skipped: dict[str, str] = {}
    challenge_doc = None
    if preset.mode == vocab.LOCATE_INVENTORY:
        source_ids, unrendered = lplan.select_sources(manifest, args.sources)
        skipped.update({s: "not rendered" for s in unrendered})
        tasks = lplan.inventory_tasks(paths, manifest, source_ids)
    elif preset.mode == vocab.LOCATE:
        inv_run = outcomes_pin_check(paths, args.inventory_run, manifest, universe)
        frozen, left_out = lplan.frozen_inventory(paths, inv_run, manifest)
        skipped.update(left_out)
        wanted = list(args.sources) or sorted(frozen)
        unknown = [s for s in wanted if s not in manifest.entries]
        if unknown:
            raise CfError(f"not in sourceset {manifest.sourceset} version {manifest.version}: "
                          + ", ".join(unknown[:5]))
        source_ids = sorted(dict.fromkeys(wanted))
        tasks, more = lplan.locate_tasks(manifest, frozen, source_ids, per_task=args.per_task,
                                         subject_only=args.subject_only)
        skipped.update(more)
    else:
        for run in args.challenge_of:
            outcomes_pin_check(paths, run, manifest, universe)
        built = lplan.challenge_build(paths, list(args.challenge_of), seed=args.seed,
                                      sample_rate=args.sample_rate)
        tasks = lplan.challenge_tasks(manifest, built)
        challenge_doc = lplan.challenge_document(run_id, built, tasks, seed=args.seed,
                                                 sample_rate=args.sample_rate,
                                                 locate_runs=list(args.challenge_of))
        source_ids = sorted({d["source_id"] for t in tasks for d in t.documents})
    lplan.check_root_size(tasks)
    if args.blind:
        blind_check(paths, args.sourceset, args.universe, source_ids)
    missing_candidates = [s for s in source_ids
                          if not paths.candidates_json(args.sourceset, args.universe, s).is_file()]
    if missing_candidates:
        warnings.append(f"{len(missing_candidates)} source(s) have no candidates file for "
                        f"{args.universe} (`cf locate candidates` writes them); the gate's "
                        "curated-hit rule reads them")

    model, model_source = models_mod.resolve(requested=args.model, preset=preset, workspace=ws)
    rd = paths.run_dir(run_id)
    gate_remote = lplan.gate_config(
        preset_mode=preset.mode, preset_name=preset.name, run_id=run_id,
        layout=layouts["host"], universe=universe, corpus_db_sha256=db_sha, manifest=manifest,
        source_ids=source_ids, blind=args.blind, inventory_run=args.inventory_run,
        challenged_runs=list(args.challenge_of))
    gate_local = lplan.gate_config(
        preset_mode=preset.mode, preset_name=preset.name, run_id=run_id,
        layout=layouts["local"], universe=universe, corpus_db_sha256=db_sha, manifest=manifest,
        source_ids=source_ids, blind=args.blind, inventory_run=args.inventory_run,
        challenged_runs=list(args.challenge_of))
    rd.mkdir(parents=True, exist_ok=True)
    write_json(paths.gate_json(run_id), gate_remote, sort_keys=True)
    write_json(rd / "gate.local.json", gate_local, sort_keys=True)
    if challenge_doc is not None:
        write_json(rd / "challenge-plan.json", challenge_doc, sort_keys=True)

    lplan.write_tasks(tasks, paths.tasks_dir(run_id), layout=layouts["host"],
                      max_open_tasks=args.max_open_tasks)
    ctx = lprompts.build_context(preset, run_id=run_id, host=layouts["host"],
                                 universe=universe, per_task=args.per_task)
    prompts = lprompts.render_prompts(preset, ctx, paths.prompts_dir(run_id))
    agent = {k: v for k, v in {"agent_model": model,
                               "reviewer_agent_model": ws.model("reviewer") or model,
                               "agentbox_profile": ws.hosts.get("agentbox_profile")}.items() if v}
    built_root = ltr.stage(
        layout_host=layouts["host"], layout_local=layouts["local"], preset=preset,
        run_id=run_id, gate_config=gate_remote, prompts=prompts, tasks=tasks, model=model,
        agent=agent, allow_sandbox_bypass=bool(args.allow_sandbox_bypass),
        context={"sourceset_pin": f"{manifest.sourceset} v{manifest.version} "
                                  f"{manifest.sha256[:12]}",
                 "universe_pin": f"{universe.id} {universe.sha256[:12]}"})
    warnings.extend(built_root["warnings"])
    if args.max_cost is None and ws.budget.get("max_cost_usd") is None:
        warnings.append("no spend cap is recorded: a locate run is not sent to a host without "
                        "one (pass --max-cost)")
    summary = lplan.summarise(tasks)
    previous = read_yaml(paths.run_yaml(run_id)) if paths.run_yaml(run_id).exists() else {}
    consent = bool(args.i_have_consent)
    run_doc = {
        "version": 1,
        "run_id": run_id,
        "created": (previous or {}).get("created") or _now(),
        "preset": preset.name,
        "mode": preset.mode,
        "companion": None,
        "repos": [],
        "sources": [],
        "source_access": preset.book_access,
        "host": {"name": args.host, "kind": layouts["host"]["kind"],
                 "remote_root": layouts["host"]["remote_root"],
                 "ssh": layouts["host"]["ssh"] or None},
        "remote": {"entrypoint": run_id, "tend_root": f"{run_id}-tend", "gate": f"{run_id}-gate",
                   "sourceset": f"sourcesets/{args.sourceset}/sources",
                   "corpus": f"corpora/{args.universe}",
                   "candidates": f"sourcesets/{args.sourceset}/candidates/{args.universe}",
                   "inventory": (f"inventories/{args.inventory_run}"
                                 if args.inventory_run else None)},
        "consent": {"given": consent, "flag": "--i-have-consent",
                    "at": _now() if consent else None,
                    "note": "cf run refuses to start a remote run without this"},
        "bridge": preset.bridge,
        "agents": {"allow_sandbox_bypass": bool(args.allow_sandbox_bypass),
                   "flag": "--allow-sandbox-bypass", "env": "CF_ALLOW_SANDBOX_BYPASS"},
        "model": model,
        "model_source": model_source,
        "models": {"worker": ws.model("worker"), "reviewer": ws.model("reviewer")},
        "gate": {"command": layouts["host"]["gate_command"],
                 "config": str(paths.gate_json(run_id)),
                 "local_config": str(rd / "gate.local.json"), "out_of_tree": True,
                 "script": lplan.GATE_SCRIPT, "kinds": list(preset.kinds)},
        "tasks": {**summary, "max_per_task": (args.per_task if preset.mode == vocab.LOCATE
                                              else lp.shape(preset.name).max_per_task),
                  "max_open": args.max_open_tasks,
                  "max_per_root": lp.MAX_TASKS_PER_ROOT},
        "prompts": {name: sha256_text(text) for name, text in sorted(prompts.items())},
        "concurrency": {"workers": preset.max_workers, "reviewers": preset.max_reviewers},
        "budget": {"currency": "USD",
                   "max_cost": args.max_cost if args.max_cost is not None
                   else ws.budget.get("max_cost_usd"),
                   "billing_path": ws.budget.get("billing_path")},
        "locate": {
            "mode": preset.mode,
            "sourceset": manifest.pin(),
            "universe": {"id": universe.id, "sha256": universe.sha256,
                         "corpus_db_sha256": db_sha},
            "inventory_run": args.inventory_run,
            "challenged_runs": list(args.challenge_of),
            "arm": vocab.BENCH_ARM_BLIND if args.blind else vocab.BENCH_ARM_ASSISTED,
            "per_task": args.per_task if preset.mode == vocab.LOCATE else None,
            "subject_only": bool(args.subject_only),
            "seed": args.seed if preset.mode == vocab.LOCATE_CHALLENGE else None,
            "sample_rate": args.sample_rate if preset.mode == vocab.LOCATE_CHALLENGE else None,
            "documents": summary["documents"],
            "passages": summary["passages"],
            "source_ids": source_ids,
            "skipped": dict(sorted(skipped.items())),
        },
        "warnings": warnings,
    }
    write_yaml(paths.run_yaml(run_id), run_doc, header=f"# run.yaml -- {run_id} (locate)\n")
    with ss_state.update(paths, args.sourceset) as st:
        ss_state.record(st, "runs", run_id, {
            "preset": preset.name, "manifest": manifest.pin(), "universe": universe.pin(),
            "tasks": summary["count"], "documents": summary["documents"],
            "passages": summary["passages"], "host": args.host, "status": "planned",
            "skipped": len(skipped)})

    print(f"planned locate run {run_id}: {summary['count']} task(s) [{preset.name}] -> {rd}")
    print(f"  sourceset: {manifest.sourceset} v{manifest.version} ({manifest.sha256[:12]}), "
          f"{len(source_ids)} source(s), {summary['passages']} passage(s)/record(s)")
    print(f"  universe : {universe.id} ({universe.sha256[:12]}), corpus.sqlite {db_sha[:12]}")
    print(f"  gate     : {layouts['host']['gate_command']}")
    print(f"  model    : {model or '(bridge default)'} (from {model_source})")
    if skipped:
        print(f"  skipped  : {len(skipped)} source(s) — listed in run.yaml locate.skipped")
    if not consent and args.host != lplan.LOCAL:
        print("  consent  : NOT given — `cf run` will refuse until --i-have-consent is recorded")
    for w in warnings:
        log.warn(w)
    return 0


def _now() -> str:
    from cflib import state as state_mod

    return state_mod.now()


def outcomes_pin_check(paths, run_id: str, manifest: inputs.Manifest,
                       universe: inputs.Universe) -> str:
    """A run this plan builds on pins this sourceset and this universe, or it is refused."""
    from locate import outcomes as outcomes_mod

    run = outcomes_mod.run_yaml(paths, run_id)
    block = run["locate"]
    if (block.get("sourceset") or {}).get("id") != manifest.sourceset:
        raise CfError(f"run {run_id} pins sourceset {(block.get('sourceset') or {}).get('id')!r}, "
                      f"not {manifest.sourceset!r}")
    if (block.get("universe") or {}).get("id") != universe.id and block.get("mode") != \
            vocab.LOCATE_INVENTORY:
        raise CfError(f"run {run_id} was located in universe "
                      f"{(block.get('universe') or {}).get('id')!r}, not {universe.id!r}")
    return run_id


def blind_check(paths, sourceset_id: str, universe_id: str, source_ids: list[str]) -> None:
    """A blind plan reads candidates files that are blind, or the arm is not blind."""
    from cflib.jsonio import read_json

    for sid in source_ids:
        path = paths.candidates_json(sourceset_id, universe_id, sid)
        if path.is_file() and not read_json(path).get("blind"):
            raise CfError(f"{sid}'s candidates file is not the blind arm's",
                          hint="re-run `cf locate candidates --blind` for this sourceset and "
                               "universe before planning the blind arm")
