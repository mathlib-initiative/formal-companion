"""``cf campaign`` -- sequence locate shards on one host, with a spend watchdog.

New code, implementing docs/proposals/wikipedia-locate.md section 6 over
references/locate-documents.md section 11; the logic is ``run/campaign.py``.

    cf campaign plan wpm-tier-a --shard wpm-a-1 --shard wpm-a-2 --host examplebox \\
        --pilot wpm-pilot --p90-cost-per-task 4.1 --p90-gate-failure-rate 0.12 --multiple 2
    cf campaign render wpm-tier-a      # campaigns/<id>/host/: sequencer.sh + its tools
    cf campaign status wpm-tier-a      # per shard: tasks, receipt, UNRESOLVED, cost

``plan`` orders shards that are already planned locate runs (each at most 500
tasks with a spend cap in its own run.yaml), stamps ``locate.campaign`` into
each, and records the watchdog's thresholds and the smoke-tested pilot;
``render`` writes the host-side sequencer, which starts shard k+1 when shard k
ends, runs the model-free watchdog beside each shard, scans, exports and prunes
the transcripts each shard wrote, and stops the campaign when a shard's last
watchdog record gives a reason to drain; ``status`` reads the shards'
receipts.  Nothing here contacts the host: ``render`` prints the transfer and
start commands for the operator, after each shard went through ``cf run``'s
transfer and verify stages and nothing more (a smoke-tested shard leaves its
own tend run going outside the sequencer).
"""
from __future__ import annotations

import json

from cflib import log
from cflib.log import format_table
from cflib.workspace import from_args
from run import campaign as campaign_mod


def register(subparsers) -> None:
    p = subparsers.add_parser("campaign",
                              help="sequence locate shards on one host with a spend watchdog")
    sub = p.add_subparsers(dest="campaign_command", metavar="<subcommand>")
    pl = sub.add_parser("plan", help="order already-planned locate runs as the campaign's shards")
    pl.add_argument("campaign_id")
    pl.add_argument("--shard", action="append", default=[], required=True, metavar="RUN_ID",
                    help="a planned locate run, in the order it runs (repeatable)")
    pl.add_argument("--host", required=True, help="the allowlisted host every shard was planned for")
    pl.add_argument("--p90-cost-per-task", type=float, default=None,
                    help="the pilot's p90 cost per merged task, in USD")
    pl.add_argument("--p90-gate-failure-rate", type=float, default=None,
                    help="the pilot's p90 fraction of gate calls that failed")
    pl.add_argument("--multiple", type=float, default=2.0,
                    help="drain at this multiple of either p90 (default 2)")
    pl.add_argument("--min-tasks", type=int, default=5,
                    help="merged tasks before the cost-per-task rule fires (default 5)")
    pl.add_argument("--pilot", default=None, metavar="RUN_ID",
                    help="the run whose smoke test (run.smoke_verified) stands for every "
                         "shard's; render refuses without one")
    pl.add_argument("--transcript-dirs", default=None, metavar="DIRS",
                    help="colon-separated host directories the agents' transcripts land in "
                         "(default: workspace.yaml hosts.<host>.transcript_dirs)")
    pl.add_argument("--canaries", default=None, metavar="HOST_PATH",
                    help="the bench's canaries.txt on the host: each shard's transcripts are "
                         "scanned for it before they are pruned")
    rd = sub.add_parser("render", help="write the host-side sequencer and the tools it runs")
    rd.add_argument("campaign_id")
    st = sub.add_parser("status", help="per shard: tasks, receipt, UNRESOLVED documents, cost")
    st.add_argument("campaign_id")
    st.add_argument("--json", action="store_true", dest="as_json")


def run(args) -> int:
    workspace = from_args(args)
    command = getattr(args, "campaign_command", None)
    if command == "plan":
        path, doc = campaign_mod.plan(
            workspace, args.campaign_id, list(args.shard), host=args.host,
            p90_cost_per_task=args.p90_cost_per_task,
            p90_gate_failure_rate=args.p90_gate_failure_rate, multiple=args.multiple,
            min_tasks=args.min_tasks, pilot=args.pilot, transcript_dirs=args.transcript_dirs,
            canaries=args.canaries)
        log.info(f"campaign {args.campaign_id}: {len(doc['shards'])} shard(s), {doc['tasks']} "
                 f"task(s), caps summing to ${doc['max_cost']:.2f} -> {path}")
        if doc["watchdog"]["p90_cost_per_task"] is None or doc["watchdog"]["p90_gate_failure_rate"] is None:
            log.warn("no pilot p90 for the cost per task or the gate-failure rate: the watchdog "
                     "drains on the spend cap alone until the pilot measures them")
        return 0
    if command == "render":
        out = campaign_mod.render(workspace, args.campaign_id)
        log.info(f"rendered {out['sequencer']['path']} (sha256 {out['sequencer']['sha256'][:12]})")
        if not out["transcript_dirs"]:
            log.warn("no transcript directories are known (cf campaign plan --transcript-dirs, or "
                     "workspace.yaml hosts.<host>.transcript_dirs): the sequencer exports no "
                     "usage and scans no transcript unless CF_TRANSCRIPT_DIRS is set on the host")
        log.info("send it, after every shard passed `cf run <shard> --stage verify` (transfer "
                 "and verify only: a shard smoke-tested with cf run keeps its own tend run "
                 "going, which the sequencer refuses):")
        log.info(f"  {out['transfer']}")
        log.info(f"  {out['start']}")
        return 0
    if command == "status":
        result = campaign_mod.status(workspace, args.campaign_id)
        if args.as_json:
            print(json.dumps(result, indent=1, sort_keys=True))
            return 0
        rows = [{"shard": r["index"], "run": r["run_id"], "mode": r["mode"], "tasks": r["tasks"],
                 "cap": f"{r['max_cost']:g}", "receipt": ("complete" if r["complete"] else
                                                          "INCOMPLETE" if r["harvested"] else "-"),
                 "gate": ("-" if not r["harvested"] else "UNGATED" if not r["gated"]
                          else "ok" if r["gate_ok"] else "FAILED"),
                 "documents": r["documents"], "unresolved": r["unresolved_documents"],
                 "capped": r["attempt_capped"],
                 "usd": "-" if r["usd"] is None else f"{r['usd']:.2f}"}
                for r in result["shards"]]
        headers = ["shard", "run", "mode", "tasks", "cap", "receipt", "gate", "documents",
                   "unresolved", "capped", "usd"]
        log.info(format_table(headers, [[row[h] for h in headers] for row in rows]))
        t = result["totals"]
        rate = "-" if t["unresolved_rate"] is None else f"{100 * t['unresolved_rate']:.1f}%"
        log.info(f"{t['harvested']}/{len(result['shards'])} shard(s) harvested; "
                 f"{t['documents']} document(s), UNRESOLVED {t['unresolved_documents']} ({rate}, "
                 f"{t['attempt_capped']} at the attempt cap); ${t['usd']:.2f} priced")
        return 0
    log.error("cf campaign needs a subcommand: plan, render or status")
    return 2
