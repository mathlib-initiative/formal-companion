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
from cflib.workspace import from_args
from cflib.errors import CfError
from cflib.jsonio import write_json
from cflib.yamlio import read_yaml

from cost import fleet as cost_fleet
from cost import pricing
from harvest import gate_corpus as gate_mod
from harvest import harvest as harvest_mod
from harvest import harvest_journal, record as record_mod
from recheck import pin as pin_mod
from run import manifest


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "harvest", help="rsync a run's findings into corpus/<run-id> with a completeness receipt")
    p.add_argument("run_id")
    p.add_argument("--from", dest="source", help="host:/path/to/findings, or a local directory")
    p.add_argument("--journal", help="recover a killed local Workflow run from its wf_* directory")
    p.add_argument("--queue", help="with --journal: the queue args, to list what is left")
    # The escape from the unlinked-challenge refusal belongs in the command that
    # also writes the receipt, runs the gate and recomputes the tally; sending
    # the operator to the standalone script gets them a corpus with none of them.
    p.add_argument("--allow-unlinked-challenges", action="store_true",
                   help="with --journal: recover the items even when no challenge ruling "
                        "attaches to one (the orphans are counted in the journal summary)")
    p.add_argument("--verify-sha", action="store_true",
                   help="also compare source checksums (catches truncated transfers)")
    # The run's cost (issue #11): the orchestrator sees no token, the agents'
    # own transcripts carry every one.  A local path only: the transcripts hold
    # prompts and tool output, so what crosses machines is the `cf cost --export`
    # file, never the transcripts themselves.
    p.add_argument("--transcripts", default=None, metavar="PATH",
                   help="price the run from the agents' Claude Code transcripts or Codex "
                        "rollout logs under PATH, or from a usage export written by "
                        "`cf cost --export`; the priced totals land in RECEIPT.json")
    p.add_argument("--transcripts-model", default=None, metavar="ID",
                   help="with --transcripts: the model id for a log that names none")
    p.add_argument("--execute", action="store_true", help="required when the source is remote")
    p.add_argument("--i-have-consent", action="store_true",
                   help="required with a remote --from: this moves data between two machines")
    gate = p.add_mutually_exclusive_group()
    gate.add_argument("--gate", dest="gate", action="store_true", default=True,
                      help="(the default) run the findings gate over what landed; a failure "
                           "fails the receipt")
    gate.add_argument("--no-gate", dest="gate", action="store_false",
                      help="land the findings without validating them against the source text "
                           "(the receipt then records gate: null, and `cf tally` says so)")
    p.add_argument("--gate-config", default=None,
                   help="the gate.json to use (default: runs/<id>/gate.local.json)")
    p.add_argument("--allow-edition-mismatch", action="store_true",
                   help="accept findings judged against another edition (they are refused by default)")
    # NOT `--source`: that dest already belongs to `--from` above, and argparse
    # would silently make the two the same option.
    p.add_argument("--source-id", dest="source_id", default=None,
                   help="with --journal: the source_id the recovered findings read "
                        "(default: the run's single source)")
    p.add_argument("--no-tally", action="store_true", help="skip recomputing the tally")


def run(args) -> int:
    workspace = from_args(args)
    paths = workspace.paths
    dest = Path(paths.corpus_run_dir(args.run_id))
    run_yaml_path = Path(paths.run_yaml(args.run_id))
    data = read_yaml(run_yaml_path) if run_yaml_path.exists() else {}
    data = data if isinstance(data, dict) else {}

    cost = _cost_block(workspace, args)
    if args.journal:
        receipt = _from_journal(workspace, args, dest, data, cost=cost)
    else:
        receipt = _from_findings(workspace, args, data, dest, run_yaml_path, cost=cost)

    log.info(harvest_mod.describe(receipt))
    empty = cost_fleet.empty_warning(cost) if cost else None
    if empty:
        log.warn(f"NO RESPONSES priced for run {args.run_id}: {empty}. Check --transcripts; "
                 f"a stage receipt refuses this cost block.")
    if cost and cost.get("unpriced"):
        log.warn(f"UNPRICED model(s) {', '.join(cost['unpriced'])}: their tokens are in the "
                 f"receipt and priced nowhere, so {pricing.format_usd(cost['usd'])} is a lower "
                 f"bound. Add them to workspace.yaml budget.prices and re-run the harvest.")
    if receipt["missing"] or receipt["sha_mismatched"]:
        log.warn("harvest INCOMPLETE: the corpus is not the whole corpus, so every number "
                 "derived from it is a lower bound. Re-run the missing tasks, then `cf harvest` again.")
    if not ((receipt.get("gate") or {"ok": True}).get("ok", True)):
        log.warn("harvest REFUSED the gate: the findings above do not validate against "
                 "gate.local.json. Fix or re-run them; the receipt is not complete.")
    if not receipt.get("gate"):
        # the trust boundary, said out loud: nothing downstream re-checks a quote
        log.warn(f"UNGATED run {args.run_id}: RECEIPT.json carries no gate block, so no quote "
                 "in this corpus was checked against the source text and no witness was "
                 "checked at all. Every number computed from it is unvalidated. Re-run "
                 f"`cf harvest {args.run_id} --gate-config <file>` when you have one.")
    if not (receipt.get("edition") or {}).get("ok", True):
        log.warn("harvest REFUSED the edition: findings judged against another edition are in "
                 "this corpus. Re-run them against the pinned edition, or pass "
                 "--allow-edition-mismatch and say so in the report.")

    if is_locate(data):
        # A locate corpus is no companion's: it pins no companion revision, the
        # faithfulness tally has no row to count in it, and its counts belong to
        # the sourceset, which the locate harvest records.
        log.info("locate run: the faithfulness tally, the companion pin and the companions' "
                 "state.json are not written for it; the receipt's locate block carries its "
                 "counts")
        return 0 if receipt["complete"] else 1
    pin = _pin(workspace, args.run_id, data, dest)
    counts = ({} if args.no_tally
              else record_mod.write_run_tally(workspace, args.run_id, dest))
    record_mod.record_run_state(workspace, args.run_id, data, receipt, counts, pin)
    return 0 if receipt["complete"] else 1


def is_locate(data: dict) -> bool:
    """Is this run.yaml a locate run's (references/locate-documents.md section 9)?"""
    from cflib import vocab

    block = data.get("locate") if isinstance(data, dict) else None
    return isinstance(block, dict) or str((data or {}).get("mode") or "") in vocab.LOCATE_MODES


def locate_source_shas(workspace, run_id: str) -> dict[str, str]:
    """``{source_id: html_sha256}`` a locate run pinned, from its gate.local.json.

    A web source lives in a sourceset, never in workspace.yaml, so the edition
    audit of the receipt reads the run's own pin instead of the workspace's
    sources table.
    """
    path = Path(workspace.paths.run_dir(run_id)) / "gate.local.json"
    try:
        import json

        sources = json.loads(path.read_text(encoding="utf-8")).get("sources") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(sid): str(pin.get("html_sha256")) for sid, pin in sorted(sources.items())
            if isinstance(pin, dict) and pin.get("html_sha256")}


def _cost_block(workspace, args) -> dict | None:
    """The receipt's ``cost`` block from ``--transcripts``, or None without the flag.

    Priced with this workspace's ``budget.prices``; a workspace written before
    that key existed is priced with the shipped default table, and the block
    says so in ``price_table_source``.
    """
    path = getattr(args, "transcripts", None)
    if not path:
        return None
    try:
        table, source = pricing.from_workspace_data(workspace.data)
    except ValueError as exc:
        raise CfError(str(exc),
                      hint="one entry per model id with input, output, cache_write, cache_read "
                           "(US dollars per million tokens) and a source note")
    if source == pricing.SOURCE_DEFAULT:
        log.detail("  cost: workspace.yaml has no budget.prices; priced with the shipped "
                   "default table")
    return cost_fleet.cost_block(path, table=table, table_source=source,
                                 model=getattr(args, "transcripts_model", None))


def _from_findings(workspace, args, data: dict, dest: Path, run_yaml_path: Path,
                   cost: dict | None = None) -> dict:
    if args.source:
        source = harvest_mod.parse_source(args.source)
        # `--from host:/path` used to skip the allowlist entirely -- only the
        # DEFAULT source went through manifest.resolve_host -- so `cf harvest`
        # would ssh to any host named on the command line and pull whatever JSON
        # it found straight into the corpus every tally and ledger is built from.
        if source["kind"] == "remote":
            _check_remote_source(workspace, args, source)
    else:
        source = _default_source(workspace, args, data)
    if source["kind"] == "remote" and not args.execute:
        log.info(f"# dry run: harvesting {source['host']}:{source['path']} -> {dest}")
        for step in harvest_mod.list_commands(source, dest):
            log.info(f"# {step['title']}\n{step['shell']}")
        raise CfError("remote harvest needs --execute",
                      hint="the commands above are exactly what it will run")
    locate = is_locate(data)
    return harvest_mod.harvest(
        run_id=args.run_id, source=source, dest=dest,
        sources=(locate_source_shas(workspace, args.run_id) if locate
                 else manifest.source_shas(workspace, data)),
        run_yaml_src=run_yaml_path if run_yaml_path.exists() else None,
        verify_sha=args.verify_sha, execute=True,
        allow_edition_mismatch=args.allow_edition_mismatch,
        gate_runner=gate_runner(workspace, args, locate=locate), cost=cost)


def _check_remote_source(workspace, args, source: dict) -> None:
    """A remote ``--from`` obeys the same allowlist and consent rule as ``cf run``."""
    ssh = str(source.get("host") or "")
    # hosts.allowlist names workspace aliases; resolve_host maps alias -> ssh name,
    # so accept either spelling and refuse anything neither matches.
    for alias in workspace.host_allowlist:
        try:
            host = manifest.resolve_host(workspace, alias)
        except CfError:
            continue
        if ssh in (alias, host.get("ssh")):
            break
    else:
        raise CfError(
            f"host {ssh!r} is not in hosts.allowlist",
            hint="add it to workspace.yaml hosts.allowlist; harvest ssh/rsyncs from that box "
                 "and lands whatever it finds in the corpus every number is computed from")
    if not args.i_have_consent:
        raise CfError(
            "refusing a remote harvest without --i-have-consent",
            hint="this moves data from a machine you may not own onto this one")


def gate_runner(workspace, args, *, preset: str = "", sources=None, repos=None,
                locate: bool = False):
    """A callable harvest() invokes on the landed corpus, or None with ``--no-gate``.

    ``--gate-config`` always wins.  Otherwise a run this workspace PLANNED has a
    ``runs/<id>/gate.local.json`` and that is used.  A run it did not plan -- an
    imported v1 corpus -- has none, and the two presets written for imported
    corpora (``differential-legacy``, ``attack-legacy``) are gate-only, so
    ``cf plan-run`` refuses to write one for them either.  When the caller says
    which preset and which sources the import was attributed to, the config is
    built from those and written next to the corpus it judged, so the receipt
    names a file a human can re-run.

    The gate runs BY DEFAULT.  references/runbook-server.md already describes
    that design ("the worker self-gates; the gate runs post-hoc at harvest"), and
    it was a flag nobody had to pass, so a corpus could be tallied, adjudicated
    and reported with no quote ever checked against ``text.txt``.  What a default
    must not do is turn a harvest into a hard failure when no config can be
    built at all -- an import of a foreign directory is the case -- so that path
    warns loudly, leaves ``gate: null`` on the receipt, and lets the harvest
    finish; ``cf tally`` reports every ungated run it counts.
    """
    if not getattr(args, "gate", True):
        return None
    explicit = getattr(args, "gate_config", None)
    planned = Path(workspace.paths.run_dir(args.run_id)) / "gate.local.json"
    config = Path(explicit).expanduser() if explicit else planned
    if locate:
        # A locate run is gated by gate/verify_locate.py over the whole landed
        # corpus, with the gate.local.json its planner wrote: the same rules the
        # host ran per task, run once more over what actually arrived.
        def _run_locate_gate(dest: Path) -> dict:
            result = gate_mod.gate_locate_corpus(dest, config)
            log.info(gate_mod.render(result))
            return result

        return _run_locate_gate

    def _run_gate(dest: Path) -> dict | None:
        cfg = config
        if not explicit and not cfg.exists():
            try:
                cfg = write_local_gate(workspace, dest, run_id=args.run_id,
                                       preset=preset, sources=sources, repos=repos)
            except CfError as exc:
                log.warn(f"UNGATED: {exc.message} -- landing these findings without checking "
                         f"a single quote against the source text. The receipt records "
                         f"gate: null and `cf tally` repeats it. "
                         f"{exc.hint or ''}".strip())
                return None
        result = gate_mod.gate_corpus(dest, cfg)
        if not result.get("files") and result.get("ok"):
            # The rule itself lives in `gate_corpus`, so every caller inherits it;
            # this is the backstop for a gate result that reached here from
            # somewhere else still claiming ok on nothing.  `files: 0,
            # problems: [], ok: True` is how an empty corpus used to receipt as
            # complete.
            result = dict(result)
            result["ok"] = False
            result["problems"] = list(result.get("problems") or []) + [
                f"{dest}: the gate found no findings to validate (an empty corpus is not a "
                f"clean one)"]
        log.info(gate_mod.render(result))
        return result

    return _run_gate


def _source_item_pages(workspace, source_id: str) -> dict:
    """``{item: pdf_page}`` for one source, or ``{}`` when it is not prepared.

    An imported corpus is gated against a source this workspace may only have
    partly prepared, so a missing or unreadable items.json leaves the gate with
    its heading-search fallback rather than failing the harvest.
    """
    from plan import presets as presets_mod
    from sources.prepare_source import items_index, load_items

    path = workspace.paths.source_items(source_id)
    if not path.exists():
        return {}
    try:
        return presets_mod.item_pages(items_index(load_items(str(path.parent))))
    except (OSError, ValueError, FileNotFoundError) as exc:
        log.detail(f"  gate: {path} is unreadable ({exc}); quotes bind by heading search")
        return {}


def write_local_gate(workspace, dest: Path, *, run_id: str, preset: str, sources,
                     repos=None) -> Path:
    """Write a gate.local.json for a corpus nothing planned, and return its path.

    Every path in it is LOCAL: the source text this workspace prepared, and its
    own page images when the preset reads them.  ``gate.local.json`` is already
    in ``NON_FINDING_FILES``, so leaving it in the corpus directory does not make
    it look like a finding.
    """
    from plan import presets as presets_mod

    if not preset:
        raise CfError(
            f"the gate has no config for run {run_id!r}",
            hint="`cf plan-run` writes runs/<id>/gate.local.json for a run it planned; "
                 "for an imported corpus pass --preset (so the gate knows which rules "
                 "to apply) or --gate-config <file>")
    obj = presets_mod.get(preset)
    blocks = {}
    for source_id in sorted(sources or {}):
        source = workspace.source(source_id)
        images = workspace.paths.source_images_dir(source_id)
        blocks[source_id] = {
            "text": str(workspace.paths.source_text(source_id)),
            "sha256": (sources or {}).get(source_id) or source.sha256 or "",
            "text_sha256": source.text_sha256,
            "images_dir": str(images) if obj.needs_images and images.is_dir() else None,
            "page_offset": source.offset,
            # PINNED-TEXT pages (items.json `pdf_page`), which is what the gate
            # binds a quote to; without them it falls back to searching the text
            # for the item's own heading.
            "item_pages": _source_item_pages(workspace, source_id),
        }
    # The quote gate compares every quote with the source TEXT.  A workspace that
    # has not prepared one cannot have a gate built for it, and a config naming a
    # file that is not there would fail every row for a reason that is about this
    # machine and not about the findings -- so this is the "no config can be
    # built" case, which warns and records gate: null (the caller catches it).
    if blocks and not any(Path(b["text"]).exists() for b in blocks.values()):
        raise CfError(
            f"no prepared source text for {', '.join(blocks)}, so the quote gate has nothing "
            f"to check {run_id!r} against",
            hint="`cf sources refresh <source-id>` prepares sources/<id>/text.txt (or "
                 "`cf onboard <repo>` for a source this workspace has never seen); or pass "
                 "--gate-config <file>, or --no-gate to land the findings unvalidated")
    # The repos of the CORPUS, not of the workspace: an imported corpus names the
    # repos it was judged over, and a baseline run's `ours`/`teorth`/`adam` are not
    # this workspace's companions.  Gating it against the workspace's names would
    # reject every row for naming a repo the gate had not been told about.
    config = presets_mod.gate_config(
        obj, run_id=run_id, sources=blocks,
        repos=list(repos) if repos else list(workspace.companion_names()))
    path = Path(dest) / "gate.local.json"
    write_json(path, config, indent=2)
    log.detail(f"  gate: built {preset} config for {', '.join(blocks) or '(no source)'} "
               f"-> {path}")
    return path


def _default_source(workspace, args, data: dict) -> dict:
    """The findings directory on the host run.yaml recorded."""
    block = data.get("run") if isinstance(data.get("run"), dict) else {}
    planned = data.get("host") if isinstance(data.get("host"), dict) else {}
    host_name = block.get("host") or planned.get("name")
    if not host_name or host_name == "local":
        raise CfError(f"run.yaml for {args.run_id!r} records no host",
                      hint="pass --from <host:/path> or --from <local dir>")
    host = manifest.resolve_host(workspace, host_name)
    names = manifest.remote_names(data, args.run_id)
    return {"kind": "remote", "host": host["ssh"],
            "path": f"{host['remote_root']}/{names['entrypoint']}/findings"}


def _from_journal(workspace, args, dest: Path, data: dict | None = None,
                  cost: dict | None = None) -> dict:
    """Recover a local Workflow run, then receipt what was recovered."""
    workflow_dir = Path(args.journal)
    sources = manifest.source_shas(workspace, data or {})
    source_id = getattr(args, "source_id", None) or ""
    if not source_id and len(sources) == 1:
        source_id = next(iter(sources))
    source_sha = sources.get(source_id, "")
    items, challenge_rows = harvest_journal.read_journal(workflow_dir)
    challenges = harvest_journal.link_challenges(
        items, challenge_rows,
        strict=not getattr(args, "allow_unlinked_challenges", False))
    written = harvest_journal.write_corpus(items, challenges, dest, args.run_id,
                                           source_id=source_id, source_sha=source_sha)
    # The rows travel with the counts: without them a PARTIAL loss -- a refuter
    # that dropped disagreement_id, an item whose name the label truncated --
    # prints a plausible `challenge_results=N` and says nothing about the M
    # rulings that were read off disk and discarded.
    summary = harvest_journal.summarise(items, challenges, challenge_rows)
    if args.queue:
        todo = harvest_journal.remaining(Path(args.queue), items)
        summary["remaining"] = len(todo)
        write_json(dest / "queue_remaining.json", todo, indent=1)
        log.info(f"{len(todo)} queue items still to do -> {dest / 'queue_remaining.json'}")
    log.info("journal: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    runner = gate_runner(workspace, args)
    receipt = harvest_mod.build_receipt(
        run_id=args.run_id, source={"kind": "journal", "path": str(workflow_dir)},
        expected=written, dest=dest, sources=sources,
        allow_edition_mismatch=args.allow_edition_mismatch,
        gate=runner(dest) if runner else None, cost=cost)
    # A journal harvest is complete only against ITSELF: the run died, so the
    # expected set is what survived, not what was asked for.
    receipt["expected_from"] = "workflow journal (a killed run: this is what survived)"
    write_json(dest / harvest_mod.RECEIPT_NAME, receipt, indent=1, sort_keys=True)
    return receipt


def _pin(workspace, run_id: str, data: dict, dest: Path) -> dict:
    """Stamp the companion revision this corpus was judged against.

    A finding is a claim about a Lean statement at a revision, and nothing in the
    corpus says which one: the commit comes from ``run.yaml`` (written at plan
    time) and the per-declaration fingerprints from the statement table extracted
    at that same commit -- never from the worker, for the same reason
    ``finding_id`` is recomputed here rather than read.

    A corpus nobody planned (an imported v1 directory) has no ``run.yaml`` with a
    pin, so every declaration lands in ``unpinned`` and `cf recheck` says so.
    That is the whole reason the pinned regression figures cannot move: a legacy
    import is reported as unpinned, not fingerprinted against today's HEAD.
    """
    decls = pin_mod.corpus_decls(dest)
    doc = pin_mod.build(workspace, run_id, data, decls)
    out = Path(workspace.paths.pin_json(run_id))
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, doc, indent=1, sort_keys=True)
    unpinned = len(doc["unpinned"])
    log.info(f"pin: {len(doc['decls'])} declaration(s) fingerprinted, {unpinned} unpinned "
             f"-> {out}")
    if unpinned:
        notes = sorted({str(c.get("note") or "") for c in doc["companions"].values()
                        if not c.get("fingerprinted")} - {""})
        log.warn(f"UNPINNED: {unpinned} declaration(s) in this corpus carry no revision "
                 f"fingerprint, so `cf recheck` cannot tell a live finding from one already "
                 f"fixed upstream" + (f" ({'; '.join(notes)})" if notes else "") +
                 ". Re-extract at the planned commit and re-harvest to pin them.")
    return doc
