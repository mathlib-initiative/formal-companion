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
from cflib.workspace import from_args
from cflib.errors import CfError
from cflib.jsonio import write_json
from cflib.yamlio import read_yaml

from harvest import gate_corpus as gate_mod
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
    # The escape from the unlinked-challenge refusal belongs in the command that
    # also writes the receipt, runs the gate and recomputes the tally; sending
    # the operator to the standalone script gets them a corpus with none of them.
    p.add_argument("--allow-unlinked-challenges", action="store_true",
                   help="with --journal: recover the items even when no challenge ruling "
                        "attaches to one (the orphans are counted in the journal summary)")
    p.add_argument("--verify-sha", action="store_true",
                   help="also compare source checksums (catches truncated transfers)")
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

    if args.journal:
        receipt = _from_journal(workspace, args, dest, data)
    else:
        receipt = _from_findings(workspace, args, data, dest, run_yaml_path)

    log.info(harvest_mod.describe(receipt))
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

    counts = {} if args.no_tally else _tally(workspace, args.run_id, dest)
    _record_state(workspace, args.run_id, data, receipt, counts)
    return 0 if receipt["complete"] else 1


def _from_findings(workspace, args, data: dict, dest: Path, run_yaml_path: Path) -> dict:
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
    return harvest_mod.harvest(
        run_id=args.run_id, source=source, dest=dest,
        sources=manifest.source_shas(workspace, data),
        run_yaml_src=run_yaml_path if run_yaml_path.exists() else None,
        verify_sha=args.verify_sha, execute=True,
        allow_edition_mismatch=args.allow_edition_mismatch,
        gate_runner=gate_runner(workspace, args))


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


def gate_runner(workspace, args, *, preset: str = "", sources=None, repos=None):
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
        if not result.get("files"):
            # A gate asked to validate a directory and given nothing to validate
            # has not passed anything: `files: 0, problems: [], ok: True` is how
            # an empty corpus used to receipt as complete.
            result = dict(result)
            result["ok"] = False
            result["problems"] = list(result.get("problems") or []) + [
                f"{dest}: the gate found no findings to validate (an empty corpus is not a "
                f"clean one)"]
        log.info(gate_mod.render(result))
        return result

    return _run_gate


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


def _from_journal(workspace, args, dest: Path, data: dict | None = None) -> dict:
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
        gate=runner(dest) if runner else None)
    # A journal harvest is complete only against ITSELF: the run died, so the
    # expected set is what survived, not what was asked for.
    receipt["expected_from"] = "workflow journal (a killed run: this is what survived)"
    write_json(dest / harvest_mod.RECEIPT_NAME, receipt, indent=1, sort_keys=True)
    return receipt


def _tally(workspace, run_id: str, dest: Path) -> dict:
    """Recompute this run's tally from the corpus and write reports/tally.<run>.json.

    The kinds map is per source: the grammar that decides which labels collapse
    onto one item belongs to the source, not to the workspace.
    """
    kinds = {sid: list(workspace.kinds(sid)) for sid in workspace.source_ids()}
    result = tally_mod.tally({run_id: dest}, kinds=kinds)
    out = Path(workspace.paths.tally_json(run_id))
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


def _record_state(workspace, run_id: str, data: dict, receipt: dict, counts: dict) -> None:
    """Record findings/receipt counts against every companion the run covers."""
    names = _companions(data) or workspace.companion_names()
    repos = (counts.get("repos") or {}) if counts else {}
    for name in names:
        try:
            with state_mod.update(workspace, str(name)) as state:
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
