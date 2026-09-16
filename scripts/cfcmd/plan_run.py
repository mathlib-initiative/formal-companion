"""``cf plan-run`` -- plan one fleet run: tasks, prompts, gate.json, tend root.

    python3 scripts/cf.py plan-run --preset attack-hunt --companion ours \\
        --host ffmain --model claude-sonnet-5 --i-have-consent

Writes ``<campaign>/runs/<run-id>/``:

    run.yaml        preset, mode, host, consent, models, counts, prompt shas
    tasks/*.yaml    one tend task per item (title carries the evidence class)
    prompts/*.md    the five rendered prompts
    data/*.jsonl    the statement tables the workers read
    gate.json       kinds/verdicts/witness/book_sha, with the RUN HOST's paths
    gate.local.json the same gate with local paths, for harvest-time re-checks
    tend-root/      config.yaml, .tend/*.yaml, bin/ wrappers, gate/ (out of tree)

Nothing here contacts a host: planning is local, deterministic and idempotent
(re-running with the same ``--run-id`` rewrites byte-identical files), and
``run.yaml`` records the sha of every rendered prompt so a resumed run can prove
the prompt did not change under it.

Ported from the five orchestration roots retrieved from ffmain (see
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

from cflib import campaign as campaign_mod
from cflib import log
from cflib import state as state_mod
from cflib.errors import CfError
from cflib.hashing import sha256_text
from cflib.yamlio import write_yaml
from plan import decl_index
from plan import make_tasks as mt
from plan import presets as presets_mod
from plan import render_prompts as rp
from plan.make_tend_root import TendRootPlan, make_tend_root

NAME = "plan-run"
HELP = "plan a fleet run: tend tasks, prompts, gate.json and a tend root"

LOCAL = "local"


def register(subparsers):
    """cf.py discovery hook."""
    p = subparsers.add_parser(NAME, help=HELP, description=__doc__)
    p.add_argument("--campaign", help="campaign directory (default: $CF_CAMPAIGN or cwd upward)")
    p.add_argument("--preset", required=True, help="one of: " + ", ".join(sorted(presets_mod.PRESETS)))
    p.add_argument("--host", default=LOCAL, help="run host; must be in campaign.yaml hosts.allowlist")
    p.add_argument("--model", default=None, help="model id to record and pass to the bridge")
    p.add_argument("--companion", help="the audited companion (required for attack presets)")
    p.add_argument("--run-id", help="default: <preset>-<YYYY-MM-DD>")
    p.add_argument("--chapters", default=None, help="comma-separated chapters for the differential universe")
    p.add_argument("--max-open-tasks", type=int, default=None,
                   help="chain depends_on so tend holds at most N worktrees at once")
    p.add_argument("--max-cost", type=float, default=None, help="USD ceiling recorded in run.yaml")
    p.add_argument("--i-have-consent", action="store_true",
                   help="the owner of --host has agreed to receive private sources and book text")
    p.add_argument("--force", action="store_true", help="replan into an existing run directory")
    p.add_argument("--allow-unreconciled", action="store_true",
                   help="plan a differential run although pairs/reconciliation.json is missing "
                        "or predates the task-universe gate (nothing then checks the task count)")
    p.set_defaults(func=run)
    return p


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _host_paths(camp, host: str, run_id: str, book_id: str) -> dict:
    """Resolve where this run's pieces live on the run host."""
    if host == LOCAL:
        p = camp.paths
        rd = p.run_dir(run_id)
        return {
            "kind": LOCAL,
            "remote_root": "",
            "entrypoint": str(rd / "entrypoint"),
            "tend_root": str(rd / "tend-root"),
            "gate_dir": str(rd / "tend-root" / "gate"),
            "prompts_dir": str(rd / "tend-root" / "prompts"),
            "book_text": str(p.book_txt),
            "images_dir": str(p.images_dir),
            "errata": str(p.errata_md),
            "sources": {c["name"]: str(c.get("repo", "")) for c in camp.companions},
        }
    camp.check_host(host)
    entry = camp.hosts.get(host) or {}
    if not isinstance(entry, dict):
        entry = {}
    root = str(entry.get("remote_root") or camp.hosts.get("remote_root")
               or f"/home/{os.environ.get('USER', 'jack')}").rstrip("/")
    return {
        "kind": "remote",
        "remote_root": root,
        "ssh": str(entry.get("ssh") or host),
        "entrypoint": f"{root}/{run_id}",
        "tend_root": f"{root}/{run_id}-tend",
        "gate_dir": f"{root}/{run_id}-gate",
        "prompts_dir": f"{root}/{run_id}-tend/prompts",
        "book_text": f"{root}/book/{book_id}/book.txt",
        "images_dir": f"{root}/book/{book_id}/images",
        "errata": f"{root}/book/{book_id}/errata.md",
        "sources": {c["name"]: f"{root}/sources/{c['name']}" for c in camp.companions},
    }


def _read_json(path: Path, what: str):
    if not path.exists():
        raise CfError(f"missing {what}: {path}", hint="run `cf pair` first")
    return json.loads(path.read_text(encoding="utf-8"))


def _queue_rows(data) -> list[dict]:
    """Unwrap a pairs/ artifact into its row list.

    ``make_queue`` writes ``queue.json`` and ``targets.<name>.json`` as
    ``{created, book_sha, count, ..., items: [...]}``; older hand-built files
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


def _stage_data(camp, rd: Path, companions: list[str]) -> list[str]:
    """Copy the statement tables into ``runs/<id>/data/`` (what cf run rsyncs)."""
    ddir = rd / "data"
    ddir.mkdir(parents=True, exist_ok=True)
    staged = []
    for name in companions:
        src = camp.paths.statements_jsonl(name)
        if src.exists():
            shutil.copy2(src, ddir / f"{name}.jsonl")
            staged.append(f"{name}.jsonl")
    if camp.paths.pairs_jsonl.exists():
        shutil.copy2(camp.paths.pairs_jsonl, ddir / "pairs.jsonl")
        staged.append("pairs.jsonl")
    return staged


def _task_universe(p, args) -> tuple[list[str] | None, int | None]:
    """The reconciled item set a differential run's tasks must equal.

    ``pairs/reconciliation.json`` is written by ``cf pair``; an absent file or
    an absent ``task_universe`` used to make the check silently vanish -- no
    warning, and the banner still claimed "universe: queue.json + targets.*.json
    (items ∩ any-repo)".  An unreconciled plan is exactly the 382-vs-383 state
    this gate exists to prevent, so it is refused instead.
    """
    allow = bool(getattr(args, "allow_unreconciled", False))
    recon_path = p.pairs_dir / "reconciliation.json"
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


def _companion_rows(camp, host_paths: dict) -> list[dict]:
    rows = []
    for c in camp.companions:
        name = str(c["name"])
        rows.append(
            {
                "name": name,
                "path": host_paths["sources"].get(name, str(c.get("repo", ""))),
                "files": c.get("glob") or "",
                "conventions_md": camp.paths.conventions_md(name),
            }
        )
    return rows


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #
def run(args) -> int:
    camp = campaign_mod.from_args(args)
    preset = presets_mod.get(args.preset)
    if preset.bridge == "none":
        raise CfError(
            f"preset {preset.name!r} is a gate-only preset",
            hint="runnable presets: " + ", ".join(presets_mod.RUNNABLE),
        )
    run_id = args.run_id or f"{preset.name}-{_dt.date.today().isoformat()}"
    p = camp.paths
    rd = p.run_dir(run_id)
    if rd.exists() and not args.force and any(rd.iterdir()):
        print(f"replanning {rd} (idempotent; pass --force to allow stale files to be replaced)")

    book_sha = camp.book_sha
    if not book_sha:
        raise CfError("campaign.yaml has no book.sha256", hint="run `cf book <id> --pdf <path>`")
    host = _host_paths(camp, args.host, run_id, camp.book_id)

    companion = args.companion
    if preset.is_attack and not companion:
        raise CfError("an attack run audits ONE companion", hint="pass --companion <name>")
    if companion:
        camp.companion(companion)  # raises when unknown

    # ---- entries -----------------------------------------------------------
    chapters = [int(c) for c in args.chapters.split(",")] if args.chapters else None
    if chapters is None:
        chapters = [int(c) for c in (camp.book.get("chapters") or [])] or None
    kinds = camp.kinds
    if preset.is_attack:
        targets = _queue_rows(
            _read_json(p.targets_json(companion), f"targets.{companion}.json")
        )
        entries = mt.target_entries(targets, companion, offset=camp.offset)
        # The ITEM SET, not its length: comparing len(entries) to len(targets)
        # is tautological (entries is built from targets), so it could only ever
        # catch a drop inside make_tasks, never one between the companion's item
        # set and targets.<name>.json.
        expected_items = sorted({str(r.get("item")) for r in targets if r.get("item")})
        expected_n = len(expected_items)
        universe = f"targets.{companion}.json"
    else:
        queue = _queue_rows(_read_json(p.queue_json, "queue.json"))
        targets_by = {}
        for name in camp.companion_names():
            tp = p.targets_json(name)
            if tp.exists():
                targets_by[name] = _queue_rows(
                    json.loads(tp.read_text(encoding="utf-8"))
                )
        entries = mt.differential_entries(
            queue, targets_by, offset=camp.offset, chapters=chapters, kinds=kinds
        )
        expected_items, expected_n = _task_universe(p, args)
        universe = "queue.json + targets.*.json (items ∩ any-repo)"

    # ---- prompts -----------------------------------------------------------
    gate_command = (
        f"python3 {host['gate_dir']}/verify_findings.py --gate-config {host['gate_dir']}/gate.json"
    )
    ctx = rp.build_context(
        preset,
        run_id=run_id,
        book_citation=str(camp.book.get("title") or camp.book_id),
        book_text=host["book_text"],
        book_sha=book_sha,
        page_offset=camp.offset,
        companions=_companion_rows(camp, host),
        gate_command=gate_command,
        errata_path=host["errata"],
        images_dir=host["images_dir"] if preset.needs_images else "",
        primary=companion or "",
    )
    prompts = rp.render_prompts(preset, ctx, p.prompts_dir(run_id))

    # ---- tasks -------------------------------------------------------------
    tasks = mt.make_tasks(
        entries,
        preset,
        {
            "run_id": run_id,
            "book_sha": book_sha,
            "gate_command": gate_command,
            "companion": companion or (camp.companion_names()[0] if camp.companion_names() else ""),
            "total_functions_sentence": ctx["total_functions_sentence"],
        },
        kinds=kinds,
        max_open_tasks=args.max_open_tasks,
        # critique.json moves `tasks == items ∩ any-repo` into make_tasks, and
        # reconcile(expected_items=...) NAMES what is missing or unexpected.
        expected_items=expected_items,
    )
    if expected_n is not None and len(tasks) != expected_n:
        raise CfError(
            f"{len(tasks)} tasks but the universe says {expected_n} ({universe})",
            hint="re-run `cf pair`; a silently shorter run is exactly what this gate exists to stop",
        )
    mt.write_tasks(tasks, p.tasks_dir(run_id))
    staged = _stage_data(camp, rd, camp.companion_names())

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
        book_text=host["book_text"],
        book_sha=book_sha,
        repos=repos,
        images_dir=host["images_dir"] if preset.needs_images else None,
        page_offset=camp.offset,
        decl_index=remote_index,
    )
    gate_local = dict(gate_remote)
    gate_local["book"] = dict(gate_remote["book"])
    gate_local["book"]["text"] = str(p.book_txt)
    gate_local["book"]["images_dir"] = str(p.images_dir) if preset.needs_images else None
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
    agent = {k: v for k, v in {
        "agent_model": camp.model("worker"),
        "reviewer_agent_model": camp.model("reviewer"),
        "agentbox_profile": camp.hosts.get("agentbox_profile"),
    }.items() if v}
    built = make_tend_root(
        plan, preset, run_id=run_id, gate_config=gate_remote,
        prompts=prompts, agent=agent, task_count=len(tasks),
        decl_index_src=index_path if remote_index else None,
    )

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
        "book": {
            "id": camp.book_id,
            "sha256": book_sha,
            "offset": camp.offset,
            "access": preset.book_access,
            "text": host["book_text"],
            "images_dir": host["images_dir"] if preset.needs_images else None,
        },
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
            "book": "book",
            "sources": "sources",
        },
        "consent": {
            "given": consent_given,
            "flag": "--i-have-consent",
            "at": state_mod.now() if consent_given else None,
            "note": "cf run refuses to start a remote run without this",
        },
        "bridge": preset.bridge,
        "model": args.model or preset.model or camp.model("worker"),
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
                "planned_tasks": len(tasks),
                "host": args.host,
                "gate": "out-of-tree",
                "status": "planned",
            })

    print(f"planned run {run_id}: {len(tasks)} task(s) [{preset.name}] -> {rd}")
    print(f"  universe : {universe}" + (f" (expected {expected_n})" if expected_n else ""))
    print(f"  gate     : {gate_command}")
    print(f"  tend root: {plan.tend_root} (entrypoint {plan.entrypoint})")
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
