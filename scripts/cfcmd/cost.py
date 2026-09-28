"""cf cost -- price a fleet's tokens from the agents' own logs, by model id.

New code for milestone M0a (docs/proposals/pdf-to-companion.md section 5.1),
after issue #11.  Wraps ``cost/usage.py`` (the readers) and ``cost/pricing.py``
(the price table).

    cf cost <transcripts dir>                # priced totals by model
    cf cost usage.jsonl.gz --json            # a usage export, as JSON
    cf cost <rollouts dir> --model <id>      # Codex logs that name no model
    cf cost <transcripts dir> --export usage.jsonl   # rows only, no text

The price table is this workspace's ``budget.prices`` when the command runs in
one, the file named by ``--prices`` when given, and the shipped default (the
Opus 5 list prices issue #11 records) outside a workspace; the output says
which.  A model the table does not list is printed as UNPRICED and never
priced at a guess.

``--export`` is the host-side half: the transcripts hold prompts, paths and
tool output that must not leave the host, so this writes one ``{model,
usage}`` row per counted response and nothing else, which `cf harvest
--transcripts` then reads on the workspace machine.
"""
from __future__ import annotations

import json

from cflib import log
from cflib.errors import WorkspaceError
from cflib.workspace import Workspace, find_workspace_root
from cost import fleet as fleet_mod
from cost import pricing
from cost import usage as usage_mod


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "cost", help="price a fleet's tokens from the agents' transcripts or a usage export, "
                     "by model id")
    p.add_argument("path", help="a directory of Claude Code transcripts or Codex rollout logs, "
                               "one log file, or a usage export (.jsonl / .jsonl.gz)")
    p.add_argument("--model", default=None,
                   help="the model id for a log that names none (an older Codex rollout); "
                        "a log that names one keeps its own")
    p.add_argument("--prices", default=None, metavar="FILE",
                   help="a YAML or JSON price table (default: this workspace's budget.prices, "
                        "else the shipped default)")
    p.add_argument("--export", default=None, metavar="FILE",
                   help="write one {model, usage} row per counted response to FILE instead "
                        "of pricing; no text leaves the machine")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="print the cost block as JSON (the shape RECEIPT.json carries)")


def price_table(args) -> tuple[dict, str]:
    """(table, where it came from) for a command's arguments."""
    explicit = getattr(args, "prices", None)
    if explicit:
        return fleet_mod.load_table(explicit), f"{pricing.SOURCE_FILE} {explicit}"
    try:
        root = find_workspace_root(getattr(args, "workspace", None))
    except WorkspaceError:
        return dict(pricing.DEFAULT_PRICE_TABLE), pricing.SOURCE_DEFAULT
    workspace = Workspace.load(root)
    try:
        return pricing.from_workspace_data(workspace.data)
    except ValueError as exc:
        raise WorkspaceError(str(exc),
                             hint="one entry per model id with input, output, cache_write, "
                                  "cache_read (US dollars per million tokens) and a source note")


def run(args) -> int:
    if args.export:
        totals = usage_mod.export_rows(args.path, args.export, model=args.model)
        log.info(f"wrote {totals.message_count} usage row(s) from {totals.transcript_count} "
                 f"file(s) -> {args.export}")
        for line in usage_mod.render_tokens(totals.tokens):
            log.info(line)
        return 0
    table, source = price_table(args)
    block = fleet_mod.cost_block(args.path, table=table, table_source=source, model=args.model)
    if args.as_json:
        print(json.dumps(block, indent=1, sort_keys=True))
    else:
        for line in fleet_mod.describe(block, indent=""):
            log.info(line)
    if block["unpriced"]:
        log.warn(f"UNPRICED model(s) {', '.join(block['unpriced'])}: their tokens are counted "
                 f"and priced nowhere, so {pricing.format_usd(block['usd'])} is a lower bound. "
                 f"Add them to workspace.yaml budget.prices with a source note.")
    if source == pricing.SOURCE_DEFAULT:
        log.detail("  no workspace here: priced with the shipped default table")
    return 0
