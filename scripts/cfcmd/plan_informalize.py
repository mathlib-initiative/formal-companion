"""``cf plan-informalize`` -- plan one phase of an informalize id as a tend run.

New code, for docs/proposals/informalize.md section 8.1 (component 5a of its section 11):

    cf plan-informalize <id> --phase items --host H --workers 1 --reviewers 1 --max-cost 300 --i-have-consent
    cf plan-informalize <id> --phase draft --sections all --host H --max-cost 300 --i-have-consent
    cf plan-informalize <id> --phase judge --from-run <attack run> --host H --max-cost 300 --i-have-consent

It writes ``runs/<run-id>/`` (``informalize.plan``): ``run.yaml`` with its ``informalize:``
block, ``repo/`` (the output repository at its HEAD), ``tasks/``, ``prompts/``, ``data/``,
``gate.json`` and ``gate.local.json``, and a tend root whose ``gate/`` holds the informalize
gate with its runtime, pinned.  It refuses unless the output repository is clean and its
runtime is this checkout's, the scope is there, and, after the items phase, the items and
references approvals are current; after the conventions phase, that phase was applied.
Nothing here contacts a host or calls a model.
"""
from __future__ import annotations

from cflib import log, vocab
from cflib.workspace import from_args

from informalize import plan as iplan

NAME = "plan-informalize"


def register(subparsers) -> None:
    p = subparsers.add_parser(NAME, help="plan one informalize phase as a tend run: tasks, "
                                         "prompts, data, gate and tend root",
                              description=__doc__)
    p.add_argument("id", help="the informalize id")
    p.add_argument("--phase", required=True, choices=vocab.INFORMALIZE_PHASES,
                   help="the phase: items, conventions, draft, attack, judge or edit")
    p.add_argument("--sections", default=None,
                   help="draft, attack, judge: all (the default; for judge, every section "
                        "with a finding), a comma list of numbers, or (attack) touched: "
                        "every section changed since its last attack run")
    p.add_argument("--from-run", default=None,
                   help="the harvested run this phase reads: the attack run for judge, the "
                        "judge run for edit")
    p.add_argument("--workers", type=int, default=None,
                   help="concurrent workers (default: informalize.yaml caps.workers)")
    p.add_argument("--reviewers", type=int, default=None,
                   help="concurrent reviewers (default: informalize.yaml caps.reviewers)")
    p.add_argument("--agent-timeout", default=None, metavar="DURATION",
                   help="CF_AGENT_TIMEOUT in both wrappers (default: caps.agent_timeout, 2h); "
                        f"from {vocab.AGENT_TIMEOUT_MIN} to {vocab.AGENT_TIMEOUT_MAX}")
    p.add_argument("--max-cost", type=float, default=None,
                   help="USD spend cap of the run (default: caps.max_cost_per_phase); the "
                        "planner refuses without one")
    p.add_argument("--model", default=None,
                   help="the agents' model id (default: workspace.yaml models)")
    p.add_argument("--host", default=iplan.LOCAL,
                   help="run host; must be in workspace.yaml hosts.allowlist")
    p.add_argument("--run-id", default=None, help="default: <id>-<phase>-<n>")
    p.add_argument("--i-have-consent", action="store_true",
                   help="the host's owner agreed to this run; recorded in run.yaml")


def run(args) -> int:
    ws = from_args(args)
    planned = iplan.plan(ws, args.id, args.phase, sections=args.sections,
                         from_run=args.from_run, workers=args.workers,
                         reviewers=args.reviewers, agent_timeout=args.agent_timeout,
                         max_cost=args.max_cost, model=args.model, host=args.host,
                         run_id=args.run_id, consent=bool(args.i_have_consent))
    print(f"planned informalize run {planned.run_id}: {len(planned.tasks)} task(s), phase "
          f"{planned.phase} -> {planned.run_dir}")
    if planned.sections:
        print(f"  sections : {', '.join(planned.sections)}")
    print(f"  base     : {planned.base_commit}")
    print(f"  gate     : {planned.gate_command}")
    print(f"  model    : {planned.model or '(bridge default)'} (from {planned.model_source})")
    print(f"  limits   : {planned.workers} workers, {planned.reviewers} reviewers, "
          f"CF_AGENT_TIMEOUT={planned.agent_timeout}, max cost {planned.max_cost:g} USD")
    for warning in planned.warnings:
        log.warn(warning)
    return 0
