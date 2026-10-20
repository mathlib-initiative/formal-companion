"""``cf plan-locate`` -- plan one locate run from a sourceset and a universe.

New code, implementing docs/proposals/wikipedia-locate.md section 3 (the locate
planner: "sourceset-scoped, not companion-scoped") with the three presets
``locate-inventory``, ``locate`` and ``locate-challenge``:

    cf plan-locate --preset locate-inventory --sourceset wpm --universe u1 --run-id inv-1
    cf plan-locate --preset locate --sourceset wpm --universe u1 --inventory-run inv-1
    cf plan-locate --preset locate-challenge --sourceset wpm --universe u1 --challenge-of loc-1

Writes ``runs/<run-id>/`` in the layout of references/locate-documents.md
section 9: ``run.yaml`` (with its ``locate`` block: the sourceset pin, the
universe and its two databases, the arm, the skill commit, the sha of every
file staged for the gate and the bridge, and ``config_shas``, the same
configuration free of the run's own id and paths), ``tasks/*.yaml``,
``prompts/``, ``gate.json`` (the run host's paths) and ``gate.local.json``
(this machine's), a challenge run's ``challenge-plan.json``, and a tend root
whose ``gate/`` holds the task manifests, the gate and its helpers and the
vendored packages.  The counts land in the sourceset's own ``state.json``,
through its one writer.

The workers search the TOOLS database: the full ``corpus.sqlite`` by default,
the G1-blind ``corpus.blind.sqlite`` with ``--blind``, a planted one for the P
tier with ``--tools-corpus-db``; the gate always checks against the full one.
An inventory or locate plan refuses a source with no candidates file, or with
one retrieved from another kind of database than its workers search; a
challenge plan refuses a tools database other than the one its challenged
runs' workers searched (the same kind, and the same file for a planted one).

Nothing here contacts a host or calls a model; planning is local and
deterministic, and re-planning the same inputs rewrites the same tasks,
manifests and gate configuration.  A harvested challenge run is never re-planned
into a plan that changes a record's planned value: its verdicts answer the plan
they were planned under (references/locate-documents.md section 9).
"""
from __future__ import annotations

import datetime as _dt
from fnmatch import fnmatchcase
from pathlib import Path

from cflib import log, vocab
from cflib.errors import CfError
from cflib.hashing import sha256_file, sha256_text
from cflib.jsonio import read_json, write_json
from cflib.paths import templates_dir
from cflib.workspace import from_args
from cflib.yamlio import read_yaml, write_yaml

from locate import export as lexport, inputs, plan as lplan, presets as lp
from locate import prompts as lprompts, state as ss_state, tend_root as ltr
from plan import models as models_mod
from run import manifest as manifest_mod

NAME = "plan-locate"
#: staged files that are the run's own rather than its configuration's: left out of
#: ``locate.config_shas.gate_files``, with any file naming a run path, since their bytes
#: differ between two runs of one configuration and would refuse every A/A replicate
RUN_SPECIFIC_STAGED = ("gate/gate.json", "gate/tasks/*", "bin/*-agent.sh")


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
                   help="plan only this source (repeatable; default: every rendered source "
                        "of a concept article)")
    p.add_argument("--include-flagged", action="store_true",
                   help="locate-inventory: also plan the pages flagged as no concept article "
                        "(redirect, disambiguation, list, biography; left out by default)")
    p.add_argument("--inventory-run", default=None,
                   help="locate: the harvested, gated inventory run whose passages are frozen")
    p.add_argument("--challenge-of", dest="challenge_of", action="append", default=[],
                   help="locate-challenge: a harvested locate run to challenge (repeatable)")
    p.add_argument("--per-task", type=int, default=vocab.MAX_PASSAGES_PER_LOCATE_TASK,
                   help=f"locate: passages per task (1..{vocab.MAX_PASSAGES_PER_LOCATE_TASK})")
    p.add_argument("--subject-only", action="store_true",
                   help="locate: the article-level tier, one subject passage per article")
    p.add_argument("--blind", action="store_true",
                   help="the G1-blind arm: the workers search corpus.blind.sqlite and the "
                        "gate's curated-hit rule is off (the candidates files must have been "
                        "written with `cf locate candidates --blind`)")
    p.add_argument("--tools-corpus-db", type=Path, default=None, metavar="FILE",
                   help="the P tier: the workers search this planted database (built for the "
                        "same universe); the gate still checks against the full corpus.sqlite")
    p.add_argument("--seed", type=int, default=0,
                   help="locate-challenge: the stratified sample's seed (default 0)")
    p.add_argument("--sample-rate", type=float, default=vocab.CHALLENGE_SAMPLE_RATE,
                   help="locate-challenge: the fraction of each scope, supporting-prominence "
                        "and weak-relation stratum re-judged; a subject passage's prominence "
                        f"is always challenged (default {vocab.CHALLENGE_SAMPLE_RATE})")
    p.add_argument("--strong-sample-rate", type=float, default=vocab.CHALLENGE_STRONG_SAMPLE_RATE,
                   help="locate-challenge: the fraction of a supporting passage's exactMatch, "
                        "closeMatch and broadMatch locations re-judged, per relation, beside "
                        "the one that sets its status, which is always challenged (default "
                        f"{vocab.CHALLENGE_STRONG_SAMPLE_RATE})")
    p.add_argument("--absence-sample-rate", type=float,
                   default=vocab.CHALLENGE_ABSENCE_SAMPLE_RATE,
                   help="locate-challenge: the fraction of the supporting passages' absences "
                        f"hunted (default {vocab.CHALLENGE_ABSENCE_SAMPLE_RATE})")
    p.add_argument("--max-open-tasks", type=int, default=None,
                   help="chain depends_on so tend holds at most N open tasks (waves)")
    p.add_argument("--workers", type=int, default=None,
                   help="concurrent worker agents (default: workspace.yaml "
                        "defaults.max_concurrent_workers, else the preset's)")
    p.add_argument("--reviewers", type=int, default=None,
                   help="concurrent reviewer agents (default: workspace.yaml "
                        "defaults.max_concurrent_reviewers, else the preset's)")
    p.add_argument("--run-id", default=None, help="default: <preset>-<YYYY-MM-DD>")
    p.add_argument("--host", default=lplan.LOCAL,
                   help="run host; must be in workspace.yaml hosts.allowlist")
    p.add_argument("--model", default=None, help="model id to record and pass to the bridge")
    p.add_argument("--max-cost", type=float, default=None,
                   help="USD spend cap recorded in run.yaml as budget.max_cost (`cf run` "
                        "refuses to send a run without one; the workspace default is not one)")
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
    if args.preset != vocab.LOCATE_INVENTORY and args.include_flagged:
        raise CfError("--include-flagged belongs to the locate-inventory preset",
                      hint="a locate run plans the sources its frozen inventory holds")
    if args.preset == vocab.LOCATE_CHALLENGE and not args.challenge_of:
        raise CfError("a challenge judges harvested locate runs",
                      hint="pass --challenge-of <run-id> (repeatable)")
    if args.preset != vocab.LOCATE_CHALLENGE and args.challenge_of:
        raise CfError("--challenge-of belongs to the locate-challenge preset")
    if args.blind and args.tools_corpus_db:
        raise CfError("--blind and --tools-corpus-db are two different arms",
                      hint="the G1-blind arm searches corpus.blind.sqlite; the P tier searches a "
                           "planted database")
    if args.preset == vocab.LOCATE_CHALLENGE and args.sources:
        raise CfError("--source does not narrow a challenge: it judges every required record "
                      "of the --challenge-of runs",
                      hint="challenge fewer locate runs per plan, or lower the sample rates")
    if args.max_open_tasks is not None and args.max_open_tasks < 1:
        raise CfError(f"--max-open-tasks must be at least 1, not {args.max_open_tasks}",
                      hint="omit it to leave tend's open tasks unbounded")


def _check_run_id(paths, args, run_id: str, preset) -> None:
    """Refuse a run id that names a run this plan reads, or a run of another preset.

    And a ``--run-id`` or ``--challenge-of`` id the export's run token cannot spell
    (``cflib.vocab.RUN_TOKEN_ID``: letters, digits and ``._-``, at most 64): a run in
    no campaign is sealed under ``run:<locate run>[+<challenge run>]``, and a run
    cannot be renamed once it has run, so the id is refused before anything is
    planned rather than after the whole run has been paid for.
    """
    for flag, rid in [("--run-id", run_id)] + [("--challenge-of", c) for c in args.challenge_of]:
        if not vocab.RUN_TOKEN_ID.fullmatch(str(rid or "")):
            raise CfError(f"{flag} {rid!r} cannot be spelled in a record set's run token",
                          hint="a run id is letters, digits and ._- (at most 64, a letter or "
                               "digit first): a run in no campaign is exported as "
                               "run:<locate run>[+<challenge run>] (references/"
                               "locate-documents.md section 6); plan the run under such an id")
    if run_id == args.inventory_run or run_id in args.challenge_of:
        raise CfError(f"run id {run_id} names a run this plan reads",
                      hint="give the new run its own --run-id")
    path = paths.run_yaml(run_id)
    if path.is_file():
        previous = read_yaml(path) or {}
        mode = (previous.get("locate") or {}).get("mode") if isinstance(previous, dict) else None
        if not isinstance(previous, dict) or previous.get("preset") != preset.name or \
                mode != preset.mode:
            raise CfError(f"run {run_id} exists and was planned as "
                          f"{previous.get('preset') if isinstance(previous, dict) else '?'!r}, "
                          f"not {preset.name!r}",
                          hint="re-planning keeps a run's preset; use a new --run-id")


def _check_harvested_challenge(paths, run_id: str, doc: dict) -> None:
    """Refuse re-planning a harvested challenge run into a plan its verdicts did not answer.

    The outcomes read a challenge run's verdicts against its ``challenge-plan.json``
    (``locate.outcomes.challenge_values``): a record the new plan requires and no
    harvested verdict judged would be owed, so the export refuses its passage, and a
    verdict on a record it no longer requires would be ignored.  A planner change can
    do that to a whole stratum -- every subject passage's prominence, once it became a
    required record -- so a re-plan that keeps each record's planned value passes, and
    one that changes any is refused before anything is written: the new plan is a new
    challenge run.
    """
    old_path = paths.challenge_plan_json(run_id)
    if not (paths.receipt_json(run_id).is_file() and old_path.is_file()):
        return

    def planned(plan: dict) -> dict:
        return {r.get("key"): r.get("planned") for r in plan.get("records") or []
                if isinstance(r, dict)}
    before, after = planned(read_json(old_path)), planned(doc)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    if changed:
        newly = sum(after.get(k) == vocab.CHALLENGE_REQUIRED for k in changed)
        raise CfError(f"challenge run {run_id} is harvested, and this plan changes the planned "
                      f"value of {len(changed)} of its record(s), {newly} of them newly "
                      f"required: its verdicts answer the plan they were planned under, so a "
                      f"newly required record would be owed and its passage refused at export",
                      hint="plan the new challenge under a new --run-id, and export with it")


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
    _check_run_id(paths, args, run_id, preset)
    manifest = inputs.manifest(paths, args.sourceset, args.manifest_version)
    universe = inputs.load_universe(paths, args.universe)
    db_path = paths.corpus_db(args.universe)
    inputs.open_corpus(db_path, universe, blind=False).close()
    db_sha = sha256_file(db_path)
    tools_db = inputs.tools_database(paths, universe, blind=args.blind,
                                     planted=args.tools_corpus_db)
    arm = vocab.BENCH_ARM_BLIND if args.blind else vocab.BENCH_ARM_ASSISTED
    host = _host(ws, args.host)
    layouts = lplan.host_layout(paths, host=args.host, run_id=run_id,
                                sourceset_id=args.sourceset, universe_id=args.universe,
                                tools_db=tools_db, **host)
    warnings: list[str] = []

    skipped: dict[str, str] = {}
    challenge_doc = None
    if preset.mode == vocab.LOCATE_INVENTORY:
        refused = inputs.parity_refusals(paths, args.sourceset, manifest)
        source_ids, unrendered, flagged = lplan.select_sources(
            manifest, args.sources, include_flagged=args.include_flagged, refused=refused)
        # a standing parity refusal is no render away (only a corrected delivery settles
        # it), so it is named apart from a source a render would settle
        skipped.update({s: (vocab.LOCATE_SKIP_PARITY_REFUSED if s in refused
                            else vocab.LOCATE_SKIP_NOT_RENDERED) for s in unrendered})
        skipped.update(flagged)
        if flagged:
            warnings.append(f"{len(flagged)} source(s) are no concept article and are not "
                            f"planned (run.yaml locate.skipped names each; --include-flagged "
                            f"plans them)")
        unasked = lplan.undetermined_biographies(manifest, source_ids)
        if unasked:
            warnings.append(f"{len(unasked)} planned source(s) have no biography flag yet, so a "
                            f"biography among them is planned as a concept article: `cf "
                            f"sourceset flag-biographies {args.sourceset} --execute` asks "
                            f"Wikidata, then re-plan")
        candidates(paths, args.sourceset, universe, tools_db, source_ids, blind=args.blind)
        tasks = lplan.inventory_tasks(paths, manifest, source_ids)
    elif preset.mode == vocab.LOCATE:
        inv_run = outcomes_pin_check(paths, args.inventory_run, manifest, universe, arm)
        frozen, left_out = lplan.frozen_inventory(paths, inv_run, manifest)
        skipped.update(left_out)
        wanted = list(args.sources) or sorted(frozen)
        unknown = [s for s in wanted if s not in manifest.entries]
        if unknown:
            raise CfError(f"not in sourceset {manifest.sourceset_id} version {manifest.version}: "
                          + ", ".join(unknown[:5]))
        source_ids = sorted(dict.fromkeys(wanted))
        docs = candidates(paths, args.sourceset, universe, tools_db,
                          [s for s in source_ids if frozen.get(s)], blind=args.blind)
        curated = {sid: list(doc.get("curated") or []) for sid, doc in docs.items()}
        tasks, more = lplan.locate_tasks(manifest, frozen, source_ids, per_task=args.per_task,
                                         subject_only=args.subject_only, curated=curated)
        skipped.update(more)
    else:
        for run in args.challenge_of:
            outcomes_pin_check(paths, run, manifest, universe, arm)
            tools_pin_check(paths, run, tools_db)
        built = lplan.challenge_build(paths, list(args.challenge_of), seed=args.seed,
                                      sample_rate=args.sample_rate,
                                      strong_sample_rate=args.strong_sample_rate,
                                      absence_sample_rate=args.absence_sample_rate)
        tasks = lplan.challenge_tasks(manifest, built, paths)
        challenge_doc = lplan.challenge_document(run_id, built, tasks, seed=args.seed,
                                                 sample_rate=args.sample_rate,
                                                 strong_sample_rate=args.strong_sample_rate,
                                                 absence_sample_rate=args.absence_sample_rate,
                                                 locate_runs=list(args.challenge_of))
        source_ids = sorted({d["source_id"] for t in tasks for d in t.documents})
    if preset.mode == vocab.LOCATE_CHALLENGE and not tasks and challenge_doc["records"]:
        # every record is not_required or sampled_out: the plan IS the challenge's result,
        # and it needs no task, no harvest and no gate
        warnings.append(f"no record of {', '.join(args.challenge_of)} is required at sample "
                        f"rates {args.sample_rate} (weak), {args.strong_sample_rate} (strong) "
                        f"and {args.absence_sample_rate} (absences): every one is not_required "
                        "or sampled_out, there is no challenge task to run, and the plan needs "
                        "no harvest")
    else:
        lplan.check_root_size(tasks, preset.mode)
    if challenge_doc is not None:
        _check_harvested_challenge(paths, run_id, challenge_doc)
    # every refusal is above this line: nothing of a refused re-plan is written
    lplan.check_task_dir(tasks, paths.tasks_dir(run_id))

    model, model_source = models_mod.resolve(requested=args.model, preset=preset, workspace=ws)
    limits = lp.concurrency(preset, ws, workers=args.workers, reviewers=args.reviewers)
    warnings.extend(limits["warnings"])
    rd = paths.run_dir(run_id)
    gate_remote, gate_local = (lplan.gate_config(
        preset_mode=preset.mode, preset_name=preset.name, run_id=run_id,
        layout=layouts[side], universe=universe, corpus_db_sha256=db_sha, tools_db=tools_db,
        manifest=manifest, source_ids=source_ids, blind=args.blind,
        inventory_run=args.inventory_run, challenged_runs=list(args.challenge_of))
        for side in ("host", "local"))
    rd.mkdir(parents=True, exist_ok=True)
    write_json(paths.gate_json(run_id), gate_remote, sort_keys=True)
    write_json(rd / "gate.local.json", gate_local, sort_keys=True)
    if challenge_doc is not None:
        write_json(rd / "challenge-plan.json", challenge_doc, sort_keys=True)
    # the task manifests outside the tend root, which a workspace does not commit
    write_json(rd / lplan.PLANNED_FILE, lplan.planned_document(run_id, tasks), sort_keys=True)

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
        context={"sourceset_pin": f"{manifest.sourceset_id} v{manifest.version} "
                                  f"{manifest.sha256[:12]}",
                 "universe_pin": f"{universe.id} {universe.sha256[:12]}",
                 # config.yaml's max_concurrent_* are the run's limits, not the preset's
                 "max_workers": limits["workers"], "max_reviewers": limits["reviewers"]})
    warnings.extend(built_root["warnings"])
    if args.max_cost is None:
        warnings.append("no spend cap is recorded (budget.max_cost): `cf run` refuses to send "
                        "this run until --max-cost is given here or to `cf run`; the "
                        "workspace default is recorded only as budget.workspace_max_cost_usd")
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
                   "candidates": f"sourcesets/{args.sourceset}/candidates/{args.universe}"},
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
                  "max_per_root": vocab.MAX_TASKS_PER_ROOT},
        "prompts": {name: sha256_text(text) for name, text in sorted(prompts.items())},
        "concurrency": {"workers": limits["workers"], "reviewers": limits["reviewers"],
                        "source": limits["source"]},
        "budget": {"currency": "USD",
                   "max_cost": args.max_cost,
                   "workspace_max_cost_usd": ws.budget.get("max_cost_usd"),
                   "billing_path": ws.budget.get("billing_path")},
        "locate": {
            "mode": preset.mode,
            "sourceset": manifest.pin(),
            "universe": {"id": universe.id, "sha256": universe.sha256,
                         "corpus_db_sha256": db_sha,
                         "tools_corpus_db": {"kind": tools_db["kind"],
                                             "sha256": tools_db["sha256"]}},
            "inventory_run": args.inventory_run,
            "challenged_runs": list(args.challenge_of),
            "arm": arm,
            "skill_commit": lexport.skill_commit(),
            "gate_files": built_root["gate_files"],
            "config_shas": config_shas(
                preset, built_root["gate_files"], Path(layouts["local"]["tend_root"]),
                run_paths=[layouts["host"]["tend_root"], layouts["host"]["gate_dir"],
                           layouts["host"]["entrypoint"], str(rd)],
                agents=built_root["agents"]),
            "per_task": args.per_task if preset.mode == vocab.LOCATE else None,
            "subject_only": bool(args.subject_only),
            "seed": args.seed if preset.mode == vocab.LOCATE_CHALLENGE else None,
            "sample_rate": args.sample_rate if preset.mode == vocab.LOCATE_CHALLENGE else None,
            "strong_sample_rate": (args.strong_sample_rate
                                   if preset.mode == vocab.LOCATE_CHALLENGE else None),
            "absence_sample_rate": (args.absence_sample_rate
                                    if preset.mode == vocab.LOCATE_CHALLENGE else None),
            "documents": summary["documents"],
            "passages": summary["passages"],
            "source_ids": source_ids,
            "skipped": dict(sorted(skipped.items())),
        },
        "warnings": warnings,
    }
    # `cf campaign plan` stamped this shard; a re-plan (of the same preset:
    # _check_run_id) keeps the stamp, so the export still seals it
    # under its campaign rather than as `run:<id>` (export.campaign_token), the
    # harvest still fetches the campaign's host artifacts, and `cf campaign
    # render` can compare the shard with campaign.yaml again
    stamp = ((previous or {}).get("locate") or {}).get("campaign")
    if stamp:
        run_doc["locate"]["campaign"] = stamp
    write_yaml(paths.run_yaml(run_id), run_doc, header=f"# run.yaml -- {run_id} (locate)\n")
    ss_state.record(paths, args.sourceset, "runs", run_id, {
        "preset": preset.name, "manifest": manifest.pin(), "universe": universe.pin(),
        "arm": arm, "tasks": summary["count"], "documents": summary["documents"],
        "passages": summary["passages"], "host": args.host, "status": "planned",
        "skipped": len(skipped)})

    print(f"planned locate run {run_id}: {summary['count']} task(s) [{preset.name}] -> {rd}")
    print(f"  sourceset: {manifest.sourceset_id} v{manifest.version} ({manifest.sha256[:12]}), "
          f"{len(source_ids)} source(s), {summary['passages']} passage(s)/record(s)")
    print(f"  universe : {universe.id} ({universe.sha256[:12]}), corpus.sqlite {db_sha[:12]}; "
          f"tools search the {tools_db['kind']} database {tools_db['sha256'][:12]}")
    print(f"  gate     : {layouts['host']['gate_command']}")
    print(f"  model    : {model or '(bridge default)'} (from {model_source})")
    print(f"  limits   : {limits['workers']} workers ({limits['source']['workers']}), "
          f"{limits['reviewers']} reviewers ({limits['source']['reviewers']})")
    if skipped:
        print(f"  skipped  : {len(skipped)} source(s) — listed in run.yaml locate.skipped")
    if not consent and args.host != lplan.LOCAL:
        print("  consent  : NOT given — `cf run` will refuse until --i-have-consent is recorded")
    for w in warnings:
        log.warn(w)
    return 0


def config_shas(preset, gate_files: dict[str, str], tend_root: Path, *,
                run_paths: list[str], agents: dict) -> dict[str, dict]:
    """``locate.config_shas``: the configuration a run was planned with, free of its id.

    ``prompts`` is the sha256 of each ``templates/prompts/`` file the plan
    rendered (the rendered prompts embed the run's own paths, and
    ``run.yaml``'s ``prompts`` keeps their shas); ``gate_files`` is
    ``locate.gate_files`` less what is the run's own -- ``gate/gate.json``, the
    task manifests, the ``bin/*-agent.sh`` wrappers, and any staged file whose
    bytes name one of ``run_paths``; ``agents`` is what those wrappers run with
    that names no run (``locate.tend_root.agent_settings``: the agent host, the
    model, the sandbox bypass, the agentbox profile, the attempt cap and the
    merge target), as the plan decided it: the wrappers' own shas name the run, so
    without this map a run whose agents ran with the sandbox bypass would pass for
    a replicate of one whose agents did not.  Two plans of one configuration that
    differ only in run id record equal maps, which is what the bench's A/A
    check compares (references/locate-documents.md section 9).
    """
    tdir = templates_dir() / "prompts"
    prompts = {name: sha256_file(tdir / name)
               for name in sorted(set(lprompts.template_files(preset).values()))}
    needles = [p.encode("utf-8") for p in run_paths if p]
    kept: dict[str, str] = {}
    for rel, sha in sorted(gate_files.items()):
        if any(fnmatchcase(rel, pattern) for pattern in RUN_SPECIFIC_STAGED):
            continue
        data = (Path(tend_root) / rel).read_bytes()
        if any(needle in data for needle in needles):
            continue
        kept[rel] = sha
    return {"prompts": prompts, "gate_files": kept, "agents": dict(sorted(agents.items()))}


def _now() -> str:
    from cflib import state as state_mod

    return state_mod.now()


def outcomes_pin_check(paths, run_id: str, manifest, universe: inputs.Universe,
                       arm: str) -> str:
    """A run this plan builds on pins this sourceset, this universe and this arm, or is refused.

    The arm is the bench's unit of comparison: a blind locate run over an
    assisted inventory (or a challenge of a run of the other arm) would be
    scored as one arm while half of it was the other.
    """
    from locate import outcomes as outcomes_mod

    run = outcomes_mod.run_yaml(paths, run_id)
    block = run["locate"]
    if (block.get("sourceset") or {}).get("id") != manifest.sourceset_id:
        raise CfError(f"run {run_id} pins sourceset {(block.get('sourceset') or {}).get('id')!r}, "
                      f"not {manifest.sourceset_id!r}")
    if (block.get("universe") or {}).get("id") != universe.id and block.get("mode") != \
            vocab.LOCATE_INVENTORY:
        raise CfError(f"run {run_id} was located in universe "
                      f"{(block.get('universe') or {}).get('id')!r}, not {universe.id!r}")
    if (block.get("arm") or vocab.BENCH_ARM_ASSISTED) != arm:
        raise CfError(f"run {run_id} is the {block.get('arm') or vocab.BENCH_ARM_ASSISTED} arm and "
                      f"this plan is the {arm} arm",
                      hint="plan every run of one arm with the same --blind (an inventory, its "
                           "locate runs and their challenges)")
    return run_id


def tools_pin_check(paths, run_id: str, tools_db: dict) -> None:
    """A challenge hunts in the tools database its challenged run's workers searched, or is refused.

    A challenge of a P run planned with the default (full)
    database would hunt with the very declarations the ablation removed, and
    its overturns would reach the outcomes the P tier scores as memory.  The
    kind must be the run's ``locate.universe.tools_corpus_db`` kind and, for a
    planted database, the very file (its sha, as the candidates are matched); a
    full or blind database is the universe's, which a rebuild leaves the same.
    """
    from locate import outcomes as outcomes_mod

    block = outcomes_mod.run_yaml(paths, run_id)["locate"]
    recorded = (block.get("universe") or {}).get("tools_corpus_db")
    recorded = recorded if isinstance(recorded, dict) else {}
    kind, sha = recorded.get("kind"), str(recorded.get("sha256") or "")
    if kind == tools_db["kind"] and (kind != inputs.TOOLS_PLANTED or sha == tools_db["sha256"]):
        return
    if kind not in inputs.TOOLS_KINDS:
        raise CfError(f"run {run_id} records no tools database (run.yaml "
                      f"locate.universe.tools_corpus_db), so a challenge cannot search the one "
                      f"its workers searched", hint=f"re-plan {run_id} with `cf plan-locate`")
    if kind == inputs.TOOLS_PLANTED:
        local = paths.run_dir(run_id) / "gate.local.json"
        where = ((read_json(local).get("tools_corpus_db") or {}).get("path")
                 if local.is_file() else None) or f"<the planted database {sha[:12]}>"
        fix = f"pass the same `--tools-corpus-db {where}`"
    elif kind == inputs.TOOLS_BLIND:
        fix = "pass `--blind`"
    else:
        fix = "leave out `--tools-corpus-db` and `--blind`"
    theirs = f"the {kind} database" + (f" {sha[:12]}" if kind == inputs.TOOLS_PLANTED else "")
    ours = f"the {tools_db['kind']} one" + (
        f" {tools_db['sha256'][:12]}" if tools_db["kind"] == inputs.TOOLS_PLANTED else "")
    raise CfError(f"run {run_id}'s workers searched {theirs}, and this challenge's hunters "
                  f"would search {ours}",
                  hint=f"{fix}: a challenge hunts with the tools its challenged runs had "
                       "(challenge runs of different databases in separate plans)")


def candidates(paths, sourceset_id: str, universe: inputs.Universe, tools_db: dict,
               source_ids: list[str], *, blind: bool) -> dict[str, dict]:
    """``{source_id: candidates document}`` of every planned source, or refuse.

    The gate's curated-hit rule reads each file and fails closed without one, so
    a missing file is refused here.  Each must have been written for this
    universe as it is now, from the same kind of database the workers will
    search (the very file, for a planted one), and in the same arm.
    """
    docs: dict[str, dict] = {}
    missing: list[str] = []
    fix = (f"`cf locate candidates --sourceset {sourceset_id} --universe {universe.id}"
           + (" --blind" if blind else "")
           + (f" --tools-corpus-db {tools_db['path']}"
              if tools_db["kind"] == inputs.TOOLS_PLANTED else "") + "`")
    for sid in source_ids:
        path = inputs.candidates_dir(paths, sourceset_id, universe.id, tools_db) / f"{sid}.json"
        if not path.is_file():
            missing.append(sid)
            continue
        doc = read_json(path)
        made = doc.get("corpus_db") if isinstance(doc.get("corpus_db"), dict) else {}
        if doc.get("universe_sha256") != universe.sha256:
            raise CfError(f"{sid}'s candidates file was written for another state of universe "
                          f"{universe.id}", hint=f"re-run {fix}")
        if made.get("kind") != tools_db["kind"] or (
                tools_db["kind"] == inputs.TOOLS_PLANTED
                and made.get("sha256") != tools_db["sha256"]):
            raise CfError(f"{sid}'s candidates were retrieved from the "
                          f"{made.get('kind') or 'unrecorded'} database, and this run's workers "
                          f"search the {tools_db['kind']} one", hint=f"re-run {fix}")
        if bool(doc.get("blind")) != bool(blind):
            raise CfError(f"{sid}'s candidates file is {'' if doc.get('blind') else 'not '}the "
                          f"blind arm's, and this plan is {'' if blind else 'not '}blind",
                          hint=f"re-run {fix}")
        docs[sid] = doc
    if missing:
        raise CfError(f"{len(missing)} planned source(s) have no candidates file from the "
                      f"{tools_db['kind']} database of universe {universe.id}: "
                      + ", ".join(missing[:5]),
                      hint=f"{fix} writes them; the gate's curated-hit rule reads them")
    return docs
