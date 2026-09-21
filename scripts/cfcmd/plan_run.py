"""``cf plan-run`` -- plan one fleet run: tasks, prompts, gate.json, tend root.

    python3 scripts/cf.py plan-run --preset attack-hunt --companion ours \\
        --host examplebox --model claude-sonnet-5 --i-have-consent

Writes ``<workspace>/runs/<run-id>/``:

    run.yaml        preset, mode, host, consent, the model AND where it came
                    from, whether any conventions list was unconfirmed, the
                    chapter narrowing, counts, prompt shas
    tasks/*.yaml    one tend task per item (title carries the evidence class)
    prompts/*.md    the five rendered prompts
    data/*.jsonl    the statement tables the workers read
    gate.json       kinds/verdicts/witness/sources, with the RUN HOST's paths
    gate.local.json the same gate with local paths, for harvest-time re-checks
    tend-root/      config.yaml, .tend/*.yaml, bin/ wrappers, gate/ (out of tree)

Nothing here contacts a host: planning is local, deterministic and idempotent
(re-running with the same ``--run-id`` rewrites byte-identical files), and
``run.yaml`` records the sha of every rendered prompt so a resumed run can prove
the prompt did not change under it.

Ported from the five orchestration roots of the v1 campaign (see
templates/PRESETS.md) — the generator that produced their tasks and configs was
never committed.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys

if __package__ in (None, ""):  # direct execution
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from cflib import workspace as workspace_mod
from cflib import log
from cflib import state as state_mod
from cflib.errors import CfError
from cflib.hashing import sha256_text
from cflib.yamlio import write_yaml
from plan import decl_index
from plan import make_tasks as mt
from plan import models as models_mod
from plan import presets as presets_mod
from plan import render_prompts as rp
from plan.make_tend_root import TendRootPlan, make_tend_root
from run import manifest as manifest_mod
from recheck import pin as pin_mod

NAME = "plan-run"
HELP = "plan a fleet run: tend tasks, prompts, gate.json and a tend root"

LOCAL = "local"


def register(subparsers):
    """cf.py discovery hook."""
    p = subparsers.add_parser(NAME, help=HELP, description=__doc__)
    p.add_argument("--workspace", help="workspace directory (default: $CF_WORKSPACE or cwd upward)")
    p.add_argument("--preset", required=True,
                   help="runnable: " + ", ".join(presets_mod.RUNNABLE)
                        + " (gate-only, cannot be planned: "
                        + ", ".join(f"{n} — {why}" for n, why in presets_mod.GATE_ONLY.items())
                        + ")")
    p.add_argument("--host", default=LOCAL, help="run host; must be in workspace.yaml hosts.allowlist")
    p.add_argument("--model", default=None, help="model id to record and pass to the bridge")
    p.add_argument("--companion", help="the audited companion (required for attack presets)")
    p.add_argument("--source", dest="source_id", default=None,
                   help="the source this run audits; required only when the selected "
                        "companions share more than one")
    p.add_argument("--run-id", help="default: <preset>-<YYYY-MM-DD>")
    p.add_argument("--chapters", default=None,
                   help="comma-separated chapters to narrow the task universe to "
                        "(both differential and attack presets; refused for a preset or a "
                        "source that has no such chapter)")
    p.add_argument("--max-open-tasks", type=int, default=None,
                   help="chain depends_on so tend holds at most N worktrees at once")
    p.add_argument("--max-cost", type=float, default=None, help="USD ceiling recorded in run.yaml")
    p.add_argument("--i-have-consent", action="store_true",
                   help="the owner of --host has agreed to receive private sources and book text")
    p.add_argument("--force", action="store_true", help="replan into an existing run directory")
    p.add_argument("--conventions-unconfirmed", action="store_true",
                   help="plan although a companion's conventions.md carries no "
                        "`confirmed_by:` stamp or no filled section (recorded in run.yaml)")
    p.add_argument("--allow-sandbox-bypass", action="store_true",
                   help="stage CF_ALLOW_SANDBOX_BYPASS=1 into the generated bin/*.sh "
                        "wrappers, so the agents run with approvals and the sandbox OFF "
                        "(recorded in run.yaml; default is the bridge's deny-by-default)")
    p.add_argument("--allow-unreconciled", action="store_true",
                   help="plan a differential run although pairs/reconciliation.json is missing "
                        "or predates the task-universe gate (nothing then checks the task count)")
    p.set_defaults(func=run)
    return p


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _host_paths(ws, host: str, run_id: str, source_id: str) -> dict:
    """Resolve where this run's pieces live on the run host.

    ``repos`` is the companion Lean; ``source_text``/``images_dir``/``errata``
    are THIS run's source.  The two live under different remote roots
    (``repos/<name>/`` and ``sources/<source_id>/``) so a companion named after
    a source cannot land on top of the text the gate checks quotes against.
    """
    if host == LOCAL:
        p = ws.paths
        rd = p.run_dir(run_id)
        return {
            "kind": LOCAL,
            "remote_root": "",
            "entrypoint": str(rd / "entrypoint"),
            "tend_root": str(rd / "tend-root"),
            "gate_dir": str(rd / "tend-root" / "gate"),
            "prompts_dir": str(rd / "tend-root" / "prompts"),
            "source_text": str(p.source_text(source_id)),
            "pages_dir": str(p.source_pages_dir(source_id)),
            "images_dir": str(p.source_images_dir(source_id)),
            "errata": str(p.source_errata_md(source_id)),
            "repos": {name: str(ref.repo or "") for name, ref in ws.companions.items()},
        }
    ws.check_host(host)
    entry = ws.hosts.get(host) or {}
    if not isinstance(entry, dict):
        entry = {}
    root = str(entry.get("remote_root") or ws.hosts.get("remote_root")
               or manifest_mod.default_remote_root()).rstrip("/")
    return {
        "kind": "remote",
        "remote_root": root,
        "ssh": str(entry.get("ssh") or host),
        "entrypoint": f"{root}/{run_id}",
        "tend_root": f"{root}/{run_id}-tend",
        "gate_dir": f"{root}/{run_id}-gate",
        "prompts_dir": f"{root}/{run_id}-tend/prompts",
        "source_text": f"{root}/sources/{source_id}/text.txt",
        "pages_dir": f"{root}/sources/{source_id}/pages",
        "images_dir": f"{root}/sources/{source_id}/images",
        "errata": f"{root}/sources/{source_id}/errata.md",
        "repos": {name: f"{root}/repos/{name}" for name in ws.companions},
    }


def _read_json(path: Path, what: str):
    if not path.exists():
        raise CfError(f"missing {what}: {path}", hint="run `cf pair` first")
    return json.loads(path.read_text(encoding="utf-8"))


def _queue_rows(data) -> list[dict]:
    """Unwrap a pairs/ artifact into its row list.

    ``make_queue`` writes ``queue.json`` and ``targets.<name>.json`` as
    ``{created, source_sha, count, ..., items: [...]}``; older hand-built files
    are a bare list.  Both shapes are accepted, and ``len()`` of the unwrapped
    list is the task count (``len()`` of the wrapper is its key count, which
    silently made ``expected_n`` 10 instead of 519).
    """
    if isinstance(data, dict):
        for key in ("items", "targets", "queue", "rows"):
            if isinstance(data.get(key), list):
                return list(data[key])
        return []
    return list(data or [])


def _stage_data(ws, rd: Path, companions: list[str], source_id: str) -> list[str]:
    """Copy the statement tables into ``runs/<id>/data/`` (what cf run rsyncs).

    The pairs table staged is THIS source's (``pairs/<source_id>/pairs.jsonl``):
    a worker grepping one merged table would read another edition's rows for the
    same item label.
    """
    ddir = rd / "data"
    ddir.mkdir(parents=True, exist_ok=True)
    staged = []
    for name in companions:
        src = ws.paths.statements_jsonl(name)
        if src.exists():
            shutil.copy2(src, ddir / f"{name}.jsonl")
            staged.append(f"{name}.jsonl")
    pairs = ws.paths.pairs_jsonl(source_id)
    if pairs.exists():
        shutil.copy2(pairs, ddir / "pairs.jsonl")
        staged.append("pairs.jsonl")
    return staged


def _task_universe(p, args, source_id: str) -> tuple[list[str] | None, int | None]:
    """The reconciled item set a differential run's tasks must equal.

    ``pairs/reconciliation.json`` is written by ``cf pair``; an absent file or
    an absent ``task_universe`` used to make the check silently vanish -- no
    warning, and the banner still claimed "universe: queue.json + targets.*.json
    (items ∩ any-repo)".  An unreconciled plan is exactly the 382-vs-383 state
    this gate exists to prevent, so it is refused instead.
    """
    allow = bool(getattr(args, "allow_unreconciled", False))
    recon_path = p.reconciliation_json(source_id)
    if not recon_path.exists():
        if allow:
            return None, None
        raise CfError(
            f"no {recon_path}: the task universe is unknown",
            hint="run `cf pair` first (it writes the reconciliation), or pass "
                 "--allow-unreconciled to plan a run nothing checks the size of",
        )
    recon = json.loads(recon_path.read_text(encoding="utf-8"))
    items = recon.get("task_universe_items")
    count = recon.get("task_universe")
    if count is None:
        if allow:
            return None, None
        raise CfError(
            f"{recon_path} has no `task_universe`",
            hint="it predates the reconciliation gate; re-run `cf pair`, or pass "
                 "--allow-unreconciled",
        )
    if items is None:
        # an older reconciliation: the count is all we have, so keep the count check
        print(f"warn {recon_path} has no `task_universe_items`; falling back to a "
              "count-only check (re-run `cf pair` to name a dropped item)")
        return None, int(count)
    names = sorted({str(i) for i in items})
    if len(names) != int(count):
        raise CfError(
            f"{recon_path}: task_universe {count} != {len(names)} distinct "
            "task_universe_items",
            hint="the reconciliation is malformed; re-run `cf pair`",
        )
    return names, len(names)


def _companion_rows(ws, host_paths: dict, names: list[str] | None = None) -> list[dict]:
    rows = []
    for name, ref in ws.companions.items():
        if names is not None and name not in names:
            continue
        rows.append(
            {
                "name": name,
                "path": host_paths["repos"].get(name, str(ref.repo or "")),
                "files": "",
                "conventions_md": ws.paths.conventions_md(name),
            }
        )
    return rows


def _select_source(ws, companions: list[str], requested: str | None) -> str:
    """Which source this run audits.

    A run is scoped to ONE source: the gate picks a text by source_id, and a
    task set spanning two editions cannot be reconciled against one universe.
    ``--source`` is required only when the selected companions share more than
    one -- otherwise there is nothing to choose and asking would be noise.
    """
    if requested:
        ws.source(requested)  # raises when unknown
        for name in companions:
            listed = ws.sources_of(name)
            if listed and requested not in listed:
                raise CfError(
                    f"companion {name!r} is not audited against source {requested!r}",
                    hint="its sources are: " + (", ".join(listed) or "(none)"))
        return requested
    shared: list[str] | None = None
    for name in companions:
        listed = ws.sources_of(name)
        shared = list(listed) if shared is None else [s for s in shared if s in listed]
    candidates = shared if shared is not None else ws.source_ids()
    candidates = [s for s in candidates if s]
    if not candidates:
        raise CfError(
            "the selected companions share no source",
            hint="a run audits one source; `cf sources list` shows which companion has which, "
                 "and `cf onboard` sets them")
    if len(candidates) > 1:
        raise CfError(
            f"the selected companions share {len(candidates)} sources "
            f"({', '.join(candidates)})",
            hint="a run is scoped to one source; pass --source <id>")
    return candidates[0]


def _check_confirmed(ws, source) -> None:
    """``policy.require_confirmed_sources`` -- read here, or it means nothing.

    Discovery appends what it learned about a new source with ``confirmed:
    false`` and surfaces it in ``cf status`` (R3).  A workspace that sets this
    key is saying "no run may be planned against a source a human has not
    signed off", and the one place to enforce it is the moment a run picks its
    source: everything downstream is stamped with it.
    """
    if source.confirmed or not ws.policy.get("require_confirmed_sources"):
        return
    raise CfError(
        f"source {source.id!r} is not confirmed, and this workspace sets "
        "policy.require_confirmed_sources",
        hint=f"check what discovery inferred with `cf sources show {source.id}`, then "
             f"`cf sources set {source.id} confirmed=true` -- or set the policy key to false",
    )


def _chapters(args, camp, preset, source_id: str) -> tuple[list[int] | None, list[int] | None]:
    """The chapter filter for this plan, and the EXPLICIT half of it.

    ``--chapters`` used to be read by the differential branch only: an attack
    plan accepted it, ignored it and planned the full universe, and a
    differential plan narrowed its entries but not the universe the task count
    is reconciled against, so the plan failed its own gate.  Both halves are
    fixed here — the same list reaches the entries and the universe — and a
    preset that cannot narrow refuses the flag by name instead of dropping it.

    The DERIVED default (the source's own ``chapters``) stays entry-only: it is
    the scope ``cf pair`` already reconciled against, so re-filtering the
    universe with it could only disagree with a pinned count.
    """
    raw = getattr(args, "chapters", None)
    explicit: list[int] | None = None
    if raw:
        if not preset.narrows_by_chapter:
            raise CfError(
                f"preset {preset.name!r} does not narrow its task universe by chapter",
                hint="drop --chapters; presets that consume it: "
                     + ", ".join(n for n in presets_mod.RUNNABLE
                                 if presets_mod.get(n).narrows_by_chapter))
        try:
            explicit = [int(c) for c in str(raw).split(",") if str(c).strip()]
        except ValueError:
            raise CfError(
                f"--chapters {raw!r} is not a comma-separated list of chapter numbers",
                hint="e.g. --chapters 1 or --chapters 1,2") from None
        if not explicit:
            raise CfError("--chapters was given no chapter", hint="e.g. --chapters 1")
        known = [int(c) for c in camp.chapters(source_id)]
        unknown = [c for c in explicit if known and c not in known]
        if unknown:
            raise CfError(
                f"source {source_id!r} has no chapter "
                + ", ".join(str(c) for c in unknown),
                hint="its chapters are " + ", ".join(str(c) for c in known)
                     + f"; `cf sources set {source_id} chapters=…` changes the audited scope")
    chapters = explicit or ([int(c) for c in camp.chapters(source_id)] or None)
    return chapters, explicit


def _local_fleet_limit(camp) -> int:
    """The ceiling `cf run --local` enforces, from this workspace's own defaults."""
    try:
        from run import fleet_local
        fallback = int(fleet_local.DEFAULT_MAX_ITEMS)
    except Exception:  # pragma: no cover - the runner is in this package
        fallback = 100
    return int(camp.default("local_fleet_max_items", fallback) or fallback)


def _source_examples(camp, source_id: str) -> list[str]:
    """This source's own illustrations of a content delta, if a human wrote any.

    ``sources.<id>.prompt_examples`` in workspace.yaml: prose for the prompt, not
    a discovered fact, which is why it is read straight off the workspace document
    and has no inference path.  Absent -- the normal case -- the prompt uses
    book-neutral shapes instead of another book's objects (`ℝ` vs `ℝᵈ` taught a
    topology companion to look for dimension hypotheses).
    """
    body = (camp.data.get("sources") or {}).get(source_id) or {}
    raw = body.get("prompt_examples") if isinstance(body, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    return [str(e) for e in (raw or []) if str(e).strip()]


def _check_conventions(rows: list[dict], override: bool) -> list[str]:
    """Refuse a run whose companions have no CONFIRMED conventions list.

    ``conventions.md`` is what the CONVENTION-vs-CONTENT judgement turns on, and
    an unconfirmed one renders the worst prompt in the set: "a first pass was
    wrong about 40% of the time by over-calling content" three lines above "treat
    every delta as content until the owner confirms one". Those cannot both be
    followed, and the actionable one maximises the failure the rest of the design
    spends its budget suppressing -- so the run stops here instead of burning a
    fleet and an adjudication pass to produce noise.

    Unconfirmed means: no ``confirmed_by:`` front-matter, or every section that
    feeds a CONVENTION_ONLY bullet (1-4) is empty once the shipped ``(example)``
    bullets are dropped (which is the DEFAULT state, because `cf onboard` copies
    the template in).  Section 7's known-and-accepted deltas are read by a prompt
    too, but they refute findings rather than licensing them, so a file carrying
    only those still leaves the judgement the prompt turns on unwritten.
    Returns the names planned with the override, for run.yaml.
    """
    unconfirmed: list[tuple[str, str]] = []
    for row in rows:
        status = rp.conventions_status(row.get("conventions_md"))
        if not status["ok"]:
            unconfirmed.append((str(row["name"]), status["reason"]))
    if not unconfirmed:
        return []
    listed = "; ".join(f"{name} ({why})" for name, why in unconfirmed)
    if not override:
        raise CfError(
            f"{len(unconfirmed)} companion(s) have no confirmed conventions.md: {listed}",
            hint="fill companions/<name>/conventions.md and stamp `confirmed_by:` "
                 "(templates/conventions.md.example is the contract), or pass "
                 "--conventions-unconfirmed to plan anyway -- it is recorded in run.yaml "
                 "so the report can say the findings rest on an unconfirmed list")
    print(f"warn planning with unconfirmed conventions: {listed}"
          " -- every finding that rests on a convention is lower confidence")
    return [name for name, _ in unconfirmed]


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def _item_pages(p, source_id: str) -> dict[str, int]:
    """``{item: pdf_page}`` for this run's gate.json, or ``{}`` if unprepared.

    The gate's first and best route from an item to the page its quotes must be
    on.  Absent it the gate searches the pinned text for the item's own heading,
    which is what leaves 26 of the baseline's 3,019 quotes unplaceable, so this
    is worth reading items.json for -- but a source that has none is planned
    anyway with the fallback, because the map is an optimisation of the bind and
    not a precondition of it.
    """
    from sources.prepare_source import items_index, load_items

    path = p.source_items(source_id)
    if not path.exists():
        return {}
    try:
        return presets_mod.item_pages(items_index(load_items(str(path.parent))))
    except (OSError, ValueError, FileNotFoundError) as exc:
        log.detail(f"item_pages: {path} is unreadable ({exc}); the gate will bind by heading")
        return {}


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #
def run(args) -> int:
    camp = workspace_mod.from_args(args)
    preset = presets_mod.get(args.preset)
    if preset.bridge == "none":
        # Not dead surface: these are the gates for corpora this tool did not
        # plan, so the refusal says what each one IS for rather than only that it
        # cannot be planned.
        purpose = presets_mod.GATE_ONLY.get(preset.name, "gate-only")
        raise CfError(
            f"preset {preset.name!r} is a gate-only preset ({purpose})",
            hint="runnable presets: " + ", ".join(presets_mod.RUNNABLE),
        )
    run_id = args.run_id or f"{preset.name}-{_dt.date.today().isoformat()}"
    p = camp.paths
    rd = p.run_dir(run_id)
    if rd.exists() and not args.force and any(rd.iterdir()):
        print(f"replanning {rd} (idempotent; pass --force to allow stale files to be replaced)")

    companion = args.companion
    if preset.is_attack and not companion:
        raise CfError("an attack run audits ONE companion", hint="pass --companion <name>")
    if companion:
        camp.companion(companion)  # raises when unknown
    names = [companion] if companion else camp.companion_names()
    if not names:
        raise CfError("this workspace has no companions",
                      hint="run `cf onboard <repo>` first")

    source_id = _select_source(camp, names, args.source_id)
    source = camp.source(source_id)
    _check_confirmed(camp, source)
    source_sha = source.sha256
    if not source_sha:
        raise CfError(
            f"source {source_id!r} has no sha256",
            hint="`cf sources show %s` says what is known about it; re-run "
                 "`cf onboard <repo>` or `cf sources add <pdf>` so the bytes are "
                 "fingerprinted -- a run cannot stamp findings with an edition nobody pinned"
            % source_id)
    host = _host_paths(camp, args.host, run_id, source_id)
    model, model_source = models_mod.resolve(requested=args.model, preset=preset, workspace=camp)
    model_warning = models_mod.unknown_warning(model, camp)

    # ---- entries -----------------------------------------------------------
    chapters, chapters_explicit = _chapters(args, camp, preset, source_id)
    kinds = camp.kinds(source_id)
    if preset.is_attack:
        targets = _queue_rows(
            _read_json(p.targets_json(source_id, companion),
                       f"pairs/{source_id}/targets.{companion}.json")
        )
        entries = mt.target_entries(targets, companion, source_id=source_id,
                                    offset=source.offset)
        # The ITEM SET, not its length: comparing len(entries) to len(targets)
        # is tautological (entries is built from targets), so it could only ever
        # catch a drop inside make_tasks, never one between the companion's item
        # set and targets.<name>.json.
        expected_items = sorted({str(r.get("item")) for r in targets if r.get("item")})
        if chapters_explicit:
            # The narrowing the flag promises, on the branch that used to ignore
            # it — applied to the entries AND to what they are reconciled against.
            entries = [e for e in entries
                       if mt.in_chapters(e.item, chapters_explicit, kinds)]
            expected_items = [i for i in expected_items
                              if mt.in_chapters(i, chapters_explicit, kinds)]
            if not entries:
                raise CfError(
                    f"no target of {companion!r} is in chapter "
                    + ", ".join(str(c) for c in chapters_explicit),
                    hint=f"`cf pair` wrote pairs/{source_id}/targets.{companion}.json; "
                         "drop --chapters or name a chapter it covers")
        expected_n = len(expected_items)
        universe = f"pairs/{source_id}/targets.{companion}.json"
    else:
        queue = _queue_rows(_read_json(p.queue_json(source_id),
                                       f"pairs/{source_id}/queue.json"))
        targets_by = {}
        for name in camp.companion_names():
            tp = p.targets_json(source_id, name)
            if tp.exists():
                targets_by[name] = _queue_rows(
                    json.loads(tp.read_text(encoding="utf-8"))
                )
        entries = mt.differential_entries(
            queue, targets_by, source_id=source_id, offset=source.offset,
            chapters=chapters, kinds=kinds
        )
        expected_items, expected_n = _task_universe(p, args, source_id)
        universe = f"pairs/{source_id}/: queue.json + targets.*.json (items ∩ any-repo)"
        if chapters_explicit and expected_n is not None:
            if expected_items is None:
                raise CfError(
                    f"{p.reconciliation_json(source_id)} has no `task_universe_items`, so "
                    "a --chapters run cannot be reconciled against it",
                    hint="re-run `cf pair` (it writes the item list), drop --chapters, or "
                         "pass --allow-unreconciled to plan a run nothing checks the size of")
            expected_items = [i for i in expected_items
                              if mt.in_chapters(i, chapters_explicit, kinds)]
            expected_n = len(expected_items)
            universe += " ∩ chapters " + ",".join(str(c) for c in chapters_explicit)
            if not expected_n:
                raise CfError(
                    "the reconciled universe has no item in chapter "
                    + ", ".join(str(c) for c in chapters_explicit),
                    hint=f"`cf sources show {source_id}` prints the chapters this source "
                         "covers; drop --chapters or name one of them")

    # ---- prompts -----------------------------------------------------------
    gate_command = (
        f"python3 {host['gate_dir']}/verify_findings.py --gate-config {host['gate_dir']}/gate.json"
    )
    companion_rows = _companion_rows(camp, host)
    unconfirmed = _check_conventions(companion_rows, bool(args.conventions_unconfirmed))
    ctx = rp.build_context(
        preset,
        run_id=run_id,
        source_id=source_id,
        book_citation=source.title or source_id,
        book_text=host["source_text"],
        source_sha=source_sha,
        source_edition=source.edition,
        page_offset=source.offset,
        companions=companion_rows,
        gate_command=gate_command,
        errata_path=host["errata"],
        images_dir=host["images_dir"] if preset.needs_images else "",
        primary=companion or "",
        # the calibration sentences come from THIS workspace's adjudicated rows
        # when it has any, and say they are imported when it has none
        ledger_dir=p.ledger_dir,
        examples=_source_examples(camp, source_id),
    )
    prompts = rp.render_prompts(preset, ctx, p.prompts_dir(run_id))

    # ---- tasks -------------------------------------------------------------
    tasks = mt.make_tasks(
        entries,
        preset,
        {
            "run_id": run_id,
            "source_id": source_id,
            "source_sha": source_sha,
            "gate_command": gate_command,
            "companion": companion or (camp.companion_names()[0] if camp.companion_names() else ""),
            "total_functions_sentence": ctx["total_functions_sentence"],
        },
        kinds=kinds,
        max_open_tasks=args.max_open_tasks,
        # the design critique moves `tasks == items ∩ any-repo` into make_tasks, and
        # reconcile(expected_items=...) NAMES what is missing or unexpected.
        expected_items=expected_items,
    )
    if expected_n is not None and len(tasks) != expected_n:
        raise CfError(
            f"{len(tasks)} tasks but the universe says {expected_n} ({universe})",
            hint="re-run `cf pair`; a silently shorter run is exactly what this gate exists to stop",
        )
    mt.write_tasks(tasks, p.tasks_dir(run_id))
    staged = _stage_data(camp, rd, camp.companion_names(), source_id)

    # ---- gate.json (host paths) and gate.local.json ------------------------
    repos = [companion] if preset.is_attack else camp.companion_names()
    # The phantom-declaration half of the schema gate: it was implemented in
    # verify_findings.py and exposed by presets.gate_config, but nothing ever
    # built an index, so every gate.json carried "decl_index": null and the
    # check was dead. It must be the FULL declaration set (a real finding names
    # private helpers), which decl_index.build() takes from deps.json or the
    # Lean sources, never from statements.jsonl unless there is nothing else.
    index_path, index = decl_index.write(camp, run_id, repos)
    if decl_index.usable(index, repos):
        remote_index = f"{host['gate_dir']}/decl-index.json"
        log.detail(f"decl-index: " + ", ".join(
            f"{k}={v}" for k, v in sorted(index["_meta"]["counts"].items())))
    else:
        remote_index = None
        print("warn the declaration index does not cover every repo of this run "
              f"({', '.join(decl_index.usable_repos(index)) or 'none'}); the gate's "
              "phantom-name check is OFF -- a partial index would reject real findings")
    gate_remote = presets_mod.gate_config(
        preset,
        run_id=run_id,
        sources={source_id: {
            "text": host["source_text"],
            "sha256": source_sha,
            "text_sha256": source.text_sha256,
            "images_dir": host["images_dir"] if preset.needs_images else None,
            "page_offset": source.offset,
            "item_pages": _item_pages(p, source_id),
        }},
        repos=repos,
        decl_index=remote_index,
    )
    gate_local = dict(gate_remote)
    gate_local["sources"] = {
        source_id: dict(gate_remote["sources"][source_id],
                        text=str(p.source_text(source_id)),
                        images_dir=(str(p.source_images_dir(source_id))
                                    if preset.needs_images else None))
    }
    gate_local["decl_index"] = str(index_path) if remote_index else None
    p.gate_json(run_id).write_text(json.dumps(gate_remote, indent=2) + "\n", encoding="utf-8")
    (rd / "gate.local.json").write_text(json.dumps(gate_local, indent=2) + "\n", encoding="utf-8")

    # ---- tend root ---------------------------------------------------------
    plan = TendRootPlan(
        local=p.tend_root(run_id),
        tend_root=host["tend_root"],
        entrypoint=host["entrypoint"],
        gate_dir=host["gate_dir"],
        prompts_dir=host["prompts_dir"],
    )
    # The RESOLVED model reaches the agents: the tend-agent yaml's model_name and
    # CF_MODEL for the bridges. `--model` used to change run.yaml and nothing
    # else, so the record named a tier the fleet may never have been given.
    agent = {k: v for k, v in {
        "agent_model": model,
        "reviewer_agent_model": camp.model("reviewer") or model,
        "agentbox_profile": camp.hosts.get("agentbox_profile"),
    }.items() if v}
    allow_bypass = bool(getattr(args, "allow_sandbox_bypass", False))
    built = make_tend_root(
        plan, preset, run_id=run_id, gate_config=gate_remote,
        prompts=prompts, agent=agent, task_count=len(tasks), model=model,
        allow_sandbox_bypass=allow_bypass,
        decl_index_src=index_path if remote_index else None,
    )
    if allow_bypass:
        print("warn --allow-sandbox-bypass: the staged wrappers run the agents with "
              "approvals and the sandbox OFF on the run host")
    if model_warning:
        built["warnings"].append(model_warning)

    # ---- the revision pin --------------------------------------------------
    # `.companions.get`, not `.companion()`: companion_names() also returns a
    # companion that exists only on disk, and a missing pin is a pin that says so.
    pins = {name: pin_mod.repo_pin(getattr(camp.companions.get(name), "repo", None))
            for name in repos}
    for name, pin in sorted(pins.items()):
        if pin.get("dirty"):
            built["warnings"].append(
                f"{name}: the checkout is dirty, so commit "
                f"{str(pin['repo_commit'])[:12]} does not hold the statements this run "
                f"audits; every finding from it re-checks as unpinned. Commit or stash "
                f"in the companion, then re-run `cf plan-run`")
        elif not pin.get("repo_commit"):
            built["warnings"].append(
                f"{name}: no git checkout to pin ({pin.get('method')}), so its findings "
                f"cannot be re-checked against a revision later")

    # ---- run.yaml ----------------------------------------------------------
    entrypoint_bytes = _dir_bytes(p.tasks_dir(run_id)) + _dir_bytes(rd / "data")
    concurrent = args.max_open_tasks or len(tasks)
    consent_given = bool(args.i_have_consent)
    run_doc = {
        "version": 1,
        "run_id": run_id,
        "created": state_mod.now(),
        "preset": preset.name,
        "mode": "attack" if preset.is_attack else "differential",
        "attack_label": preset.attack_label or None,
        "companion": companion,
        "repos": repos,
        # WHICH REVISION of each companion this run audits. A finding is a claim
        # about a Lean statement at a revision; without it, six months later a
        # live finding and one already fixed upstream read identically, and
        # `cf recheck` has nothing to compare against. Read here rather than at
        # harvest because here is where the statements the workers will read are
        # staged -- and read through cflib.gitsafe, because a companion tree is
        # data. `dirty` is part of the pin: a working tree that does not match
        # its commit is not pinned by it.
        "companion_pins": pins,
        # A LIST, because `cf run` decides what leaves this machine from it and
        # `cf harvest` checks the landed corpus against it. One source today;
        # the shape does not assume it.
        "sources": [{
            "id": source_id,
            "sha256": source_sha,
            "title": source.title,
            "edition": source.edition,
            "offset": source.offset,
            "text": host["source_text"],
            "pages_dir": host["pages_dir"],
            "images_dir": host["images_dir"] if preset.needs_images else None,
            "errata": host["errata"],
        }],
        "source_access": preset.book_access,
        "host": {
            "name": args.host,
            "kind": host["kind"],
            "remote_root": host["remote_root"],
            "ssh": host.get("ssh"),
        },
        "remote": {
            "entrypoint": run_id,
            "tend_root": f"{run_id}-tend",
            "gate": f"{run_id}-gate",
            "sources": "sources",
            "repos": "repos",
        },
        # An unconfirmed conventions list is the single biggest source of false
        # positives, so a run planned over one says so in its own record and the
        # report can mark every convention-dependent finding lower confidence.
        "conventions": {
            "unconfirmed": unconfirmed,
            "override": "--conventions-unconfirmed" if unconfirmed else None,
        },
        "consent": {
            "given": consent_given,
            "flag": "--i-have-consent",
            "at": state_mod.now() if consent_given else None,
            "note": "cf run refuses to start a remote run without this",
        },
        "bridge": preset.bridge,
        # WHAT THE AGENTS MAY DO, decided here because this is what writes the
        # wrappers: false means the bridge's deny-by-default reached the host.
        # `cf run` re-reads the staged wrappers rather than trusting this line.
        "agents": {
            "allow_sandbox_bypass": allow_bypass,
            "flag": "--allow-sandbox-bypass",
            "env": "CF_ALLOW_SANDBOX_BYPASS",
        },
        "model": model,
        # WHERE the model came from, so the record is falsifiable: a run that
        # says `model_source: --model` was asked for that tier; one that says
        # `workspace.models.worker` inherited it.
        "model_source": model_source,
        "models": {"worker": camp.model("worker"), "reviewer": camp.model("reviewer")},
        "gate": {
            "command": gate_command,
            "config": str(p.gate_json(run_id)),
            "local_config": str(rd / "gate.local.json"),
            "out_of_tree": True,
            "kinds": list(preset.kinds),
            "verdicts": list(preset.verdicts),
            "witness_required": preset.witness_required,
        },
        "tasks": {
            "count": len(tasks),
            "universe": universe,
            "expected": expected_n,
            # the narrowing this plan was asked for, so a count can be explained
            # without re-deriving it: null means "the source's whole scope"
            "chapters": chapters_explicit,
            "differential": sum(1 for t in tasks if t.kind == "DIFFERENTIAL"),
            "single_source": sum(1 for t in tasks if t.kind == "SINGLE-SOURCE"),
            "attack": sum(1 for t in tasks if t.kind in ("hunt", "vac")),
            "max_open": args.max_open_tasks,
        },
        "data": staged,
        "prompts": {name: sha256_text(text) for name, text in sorted(prompts.items())},
        "concurrency": {"workers": preset.max_workers, "reviewers": preset.max_reviewers},
        "budget": {
            "currency": "USD",
            "max_cost": args.max_cost if args.max_cost is not None
            else camp.budget.get("max_cost_usd"),
            "billing_path": camp.budget.get("billing_path"),
        },
        "disk": {
            "entrypoint_bytes": entrypoint_bytes,
            "concurrent_worktrees": concurrent,
            "estimated_worktree_bytes": entrypoint_bytes * concurrent,
            "note": "tend creates a worktree per READY task; --max-open-tasks chains depends_on",
        },
        "warnings": built["warnings"],
    }
    write_yaml(p.run_yaml(run_id), run_doc, header=f"# run.yaml -- {run_id}\n")

    for name in (repos if preset.is_attack else camp.companion_names()):
        with state_mod.update(camp, name) as st:
            state_mod.record_run(st, run_id, {
                "preset": preset.name,
                "source_id": source_id,
                "source_sha": source_sha,
                "planned_tasks": len(tasks),
                "host": args.host,
                "gate": "out-of-tree",
                "status": "planned",
            })

    print(f"planned run {run_id}: {len(tasks)} task(s) [{preset.name}] -> {rd}")
    print(f"  source   : {source_id} ({source_sha[:8]}), offset {source.offset}")
    print(f"  universe : {universe}" + (f" (expected {expected_n})" if expected_n else ""))
    print(f"  gate     : {gate_command}")
    print(f"  model    : {model or '(bridge default)'} (from {model_source})")
    print(f"  tend root: {plan.tend_root} (entrypoint {plan.entrypoint})")
    # The size warning belongs HERE, not one command later: the limit lives in the
    # workspace.yaml this command just read, and a plan with no host can only be
    # executed locally. `cf run --local` used to be the first place a user heard
    # that a 506-task plan does not fit.
    local_limit = _local_fleet_limit(camp)
    if args.host == LOCAL and len(tasks) > local_limit:
        print(f"  note     : {len(tasks)} items is above local_fleet_max_items "
              f"({local_limit}), so `cf run {run_id} --local` will refuse. Either plan it "
              f"for a server (--host <alias> --i-have-consent), narrow it (--chapters), "
              f"raise local_fleet_max_items in workspace.yaml, or accept a resume with "
              f"`cf run {run_id} --local --force`")
    if not consent_given and args.host != LOCAL:
        print("  consent  : NOT given — `cf run` will refuse until --i-have-consent is recorded")
    for w in built["warnings"]:
        print(f"  warning  : {w}")
    return 0


def main(argv=None) -> int:
    """Standalone entry point: ``python3 scripts/cfcmd/plan_run.py --preset …``."""
    import argparse

    parser = argparse.ArgumentParser(prog="cf")
    sub = parser.add_subparsers(dest="command", required=True)
    register(sub)
    args = parser.parse_args([NAME] + list(sys.argv[1:] if argv is None else argv))
    return run(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
