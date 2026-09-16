"""cf harvest -- bring a run's findings home and prove the corpus is whole.

New code.  Wraps ``harvest/harvest.py`` (rsync + RECEIPT) and
``harvest/harvest_journal.py`` (recovery from a killed local Workflow run), then
recomputes the tally from what actually landed.

    cf harvest <run-id> --execute                 # from the host in run.yaml
    cf harvest <run-id> --from /path/to/findings  # a local directory
    cf harvest <run-id> --journal ~/.claude/.../wf_abc --queue queue.json

The command exits non-zero when the receipt is incomplete.  That is the whole
point: ``findings-hunt/`` once held 762 files where the server had 763, and the
missing one was a real CONTENT finding, so every count computed from that corpus
was quietly wrong.
"""
from __future__ import annotations

from pathlib import Path

from cflib import log
from cflib import state as state_mod
from cflib.campaign import from_args
from cflib.errors import CfError
from cflib.jsonio import write_json
from cflib.yamlio import read_yaml

from harvest import harvest as harvest_mod
from harvest import harvest_journal, tally as tally_mod
from run import manifest


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "harvest", help="rsync a run's findings into corpus/<run-id> with a completeness receipt")
    p.add_argument("run_id")
    p.add_argument("--from", dest="source", help="host:/path/to/findings, or a local directory")
    p.add_argument("--journal", help="recover a killed local Workflow run from its wf_* directory")
    p.add_argument("--queue", help="with --journal: the queue args, to list what is left")
    p.add_argument("--verify-sha", action="store_true",
                   help="also compare source checksums (catches truncated transfers)")
    p.add_argument("--execute", action="store_true", help="required when the source is remote")
    p.add_argument("--no-tally", action="store_true", help="skip recomputing the tally")


def run(args) -> int:
    campaign = from_args(args)
    paths = campaign.paths
    dest = Path(paths.corpus_run_dir(args.run_id))
    run_yaml_path = Path(paths.run_yaml(args.run_id))
    data = read_yaml(run_yaml_path) if run_yaml_path.exists() else {}
    data = data if isinstance(data, dict) else {}

    if args.journal:
        receipt = _from_journal(campaign, args, dest)
    else:
        receipt = _from_findings(campaign, args, data, dest, run_yaml_path)

    log.info(harvest_mod.describe(receipt))
    if not receipt["complete"]:
        log.warn("harvest INCOMPLETE: the corpus is not the whole corpus, so every number "
                 "derived from it is a lower bound. Re-run the missing tasks, then `cf harvest` again.")

    counts = {} if args.no_tally else _tally(campaign, args.run_id, dest)
    _record_state(campaign, args.run_id, data, receipt, counts)
    return 0 if receipt["complete"] else 1


def _from_findings(campaign, args, data: dict, dest: Path, run_yaml_path: Path) -> dict:
    source = harvest_mod.parse_source(args.source) if args.source else _default_source(campaign, args, data)
    if source["kind"] == "remote" and not args.execute:
        log.info(f"# dry run: harvesting {source['host']}:{source['path']} -> {dest}")
        for step in harvest_mod.list_commands(source, dest):
            log.info(f"# {step['title']}\n{step['shell']}")
        raise CfError("remote harvest needs --execute",
                      hint="the commands above are exactly what it will run")
    return harvest_mod.harvest(
        run_id=args.run_id, source=source, dest=dest,
        book_sha=manifest.book_sha(campaign, data),
        run_yaml_src=run_yaml_path if run_yaml_path.exists() else None,
        verify_sha=args.verify_sha, execute=True)


def _default_source(campaign, args, data: dict) -> dict:
    """The findings directory on the host run.yaml recorded."""
    block = data.get("run") if isinstance(data.get("run"), dict) else {}
    planned = data.get("host") if isinstance(data.get("host"), dict) else {}
    host_name = block.get("host") or planned.get("name")
    if not host_name or host_name == "local":
        raise CfError(f"run.yaml for {args.run_id!r} records no host",
                      hint="pass --from <host:/path> or --from <local dir>")
    host = manifest.resolve_host(campaign, host_name)
    names = manifest.remote_names(data, args.run_id)
    return {"kind": "remote", "host": host["ssh"],
            "path": f"{host['remote_root']}/{names['entrypoint']}/findings"}


def _from_journal(campaign, args, dest: Path) -> dict:
    """Recover a local Workflow run, then receipt what was recovered."""
    workflow_dir = Path(args.journal)
    items, challenge_rows = harvest_journal.read_journal(workflow_dir)
    challenges = harvest_journal.link_challenges(items, challenge_rows)
    written = harvest_journal.write_corpus(items, challenges, dest, args.run_id)
    summary = harvest_journal.summarise(items, challenges)
    if args.queue:
        todo = harvest_journal.remaining(Path(args.queue), items)
        summary["remaining"] = len(todo)
        write_json(dest / "queue_remaining.json", todo, indent=1)
        log.info(f"{len(todo)} queue items still to do -> {dest / 'queue_remaining.json'}")
    log.info("journal: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    receipt = harvest_mod.build_receipt(
        run_id=args.run_id, source={"kind": "journal", "path": str(workflow_dir)},
        expected=written, dest=dest, book_sha=campaign.book_sha or "")
    # A journal harvest is complete only against ITSELF: the run died, so the
    # expected set is what survived, not what was asked for.
    receipt["expected_from"] = "workflow journal (a killed run: this is what survived)"
    write_json(dest / harvest_mod.RECEIPT_NAME, receipt, indent=1, sort_keys=True)
    return receipt


def _tally(campaign, run_id: str, dest: Path) -> dict:
    """Recompute this run's tally from the corpus and write reports/tally.<run>.json."""
    result = tally_mod.tally({run_id: dest}, kinds=campaign.kinds, book_sha=campaign.book_sha or "")
    out = Path(campaign.paths.tally_json(run_id))
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, result, indent=1, sort_keys=True)
    log.info("")
    log.info(tally_mod.render(result))
    log.info(f"-> {out}")
    return result


def _companions(data: dict) -> list[str]:
    """The companions a run covers (plan-run writes `companion`, a string)."""
    for key in ("companions", "companion", "repos"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return [value]
        if isinstance(value, list) and value:
            return [str(v) for v in value]
    return []


def _record_state(campaign, run_id: str, data: dict, receipt: dict, counts: dict) -> None:
    """Record findings/receipt counts against every companion the run covers."""
    names = _companions(data) or campaign.companion_names()
    repos = (counts.get("repos") or {}) if counts else {}
    for name in names:
        try:
            with state_mod.update(campaign, str(name)) as state:
                values = {
                    "findings": receipt["actual"]["count"],
                    "expected": receipt["expected"]["count"],
                    "receipt_ok": receipt["complete"],
                    "harvested_at": receipt["harvested_at"],
                }
                state_mod.record_run(state, run_id, values)
                if str(name) in repos:
                    state_mod.merge_section(state, "tally", tally_mod.state_counts(repos[str(name)]))
                state_mod.note(state, "harvest", run=run_id, complete=receipt["complete"])
        except Exception as exc:
            log.detail(f"state.json for {name}: {exc}")
