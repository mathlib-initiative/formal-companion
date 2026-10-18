"""Locate campaigns: an ordered list of shard runs, a host sequencer, a status table.

New code, implementing docs/proposals/wikipedia-locate.md section 6 (Roots;
Cost and spend; ``cf campaign``) over references/locate-documents.md section
11.  A campaign does not plan anything: each shard is a locate run already
planned (``runs/<run-id>/run.yaml`` with a ``locate`` block), of at most
:data:`MAX_ROOT_TASKS` tasks and with a spend cap recorded in its own
``run.yaml``.  The campaign orders them, renders the host side --
``templates/campaign/sequencer.sh.tmpl`` plus copies of
``scripts/run/watchdog.py`` and ``scripts/cost/usage.py`` -- and reads the
shards' receipts back.  Nothing here contacts a host.
"""
from __future__ import annotations

import shlex
import shutil
from pathlib import Path
from typing import Any

from cflib import state as state_mod
from cflib import vocab
from cflib.errors import CfError
from cflib.hashing import sha256_file
from cflib.jsonio import read_json
from cflib.paths import skill_root, templates_dir
from cflib.yamlio import read_yaml, write_yaml

from . import manifest

#: a root holds at most this many tasks (proposal section 6, Roots)
MAX_ROOT_TASKS = vocab.MAX_TASKS_PER_ROOT
CAMPAIGN_VERSION = 1


def _run_doc(workspace, run_id: str) -> dict:
    path = workspace.paths.run_yaml(run_id)
    if not path.exists():
        raise CfError(f"shard {run_id!r} has no run.yaml ({path})",
                      hint="plan each shard first; a campaign orders runs, it plans none")
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise CfError(f"{path} is not a YAML mapping")
    return data


def _task_count(workspace, run_id: str, data: dict) -> int:
    return manifest.task_count(Path(workspace.root), run_id, data)


def shard_record(workspace, run_id: str, index: int, host_alias: str) -> dict:
    """One shard's row of campaign.yaml, every refusal of section 6 checked."""
    data = _run_doc(workspace, run_id)
    block = data.get("locate") if isinstance(data.get("locate"), dict) else None
    if block is None:
        raise CfError(f"shard {run_id!r} is not a locate run (its run.yaml has no locate block)",
                      hint="a campaign sequences locate shards; a faithfulness run goes through "
                           "`cf run` on its own")
    tasks = _task_count(workspace, run_id, data)
    if tasks > MAX_ROOT_TASKS:
        raise CfError(f"shard {run_id!r} holds {tasks} tasks; a root holds at most "
                      f"{MAX_ROOT_TASKS}", hint="re-plan it as several shards")
    budget = data.get("budget") if isinstance(data.get("budget"), dict) else {}
    cap = budget.get("max_cost")
    if not isinstance(cap, (int, float)) or isinstance(cap, bool) or cap <= 0:
        raise CfError(f"shard {run_id!r} records no spend cap (budget.max_cost in its run.yaml)",
                      hint="a recorded cap is a precondition of every locate shard: re-plan "
                           "it with a cap")
    planned = data.get("host") if isinstance(data.get("host"), dict) else {}
    if planned.get("name") and planned["name"] != host_alias:
        raise CfError(f"shard {run_id!r} was planned for host {planned['name']!r}, not "
                      f"{host_alias!r}", hint="one campaign runs on one host")
    concurrency = data.get("concurrency") if isinstance(data.get("concurrency"), dict) else {}
    return {"index": index, "run_id": run_id, "mode": block.get("mode") or data.get("mode"),
            "tasks": tasks, "max_cost": float(cap),
            "workers": int(concurrency.get("workers") or 6),
            "reviewers": int(concurrency.get("reviewers") or 4)}


def plan(workspace, campaign_id: str, shards: list[str], *, host: str,
         p90_cost_per_task: float | None = None, p90_gate_failure_rate: float | None = None,
         multiple: float = 2.0, min_tasks: int = 5) -> tuple[Path, dict]:
    """Write ``campaigns/<id>/campaign.yaml``; idempotent for the same arguments."""
    if not shards:
        raise CfError("a campaign needs at least one shard", hint="pass --shard <run-id>, in order")
    if len(set(shards)) != len(shards):
        raise CfError("a shard is named twice", hint="each run is one shard, once")
    if multiple <= 1:
        raise CfError(f"--multiple {multiple} would drain at or below the pilot's p90",
                      hint="the watchdog drains at a MULTIPLE of the p90, e.g. 2")
    workspace.check_host(host)
    rows = [shard_record(workspace, run_id, i, host) for i, run_id in enumerate(shards, 1)]
    path = workspace.paths.campaign_yaml(campaign_id)
    previous = read_yaml(path) if path.exists() else {}
    doc = {
        "version": CAMPAIGN_VERSION,
        "id": campaign_id,
        "created": (previous or {}).get("created") or state_mod.now(),
        "host": host,
        "shards": rows,
        "tasks": sum(r["tasks"] for r in rows),
        "max_cost": round(sum(r["max_cost"] for r in rows), 2),
        "watchdog": {"p90_cost_per_task": p90_cost_per_task,
                     "p90_gate_failure_rate": p90_gate_failure_rate,
                     "multiple": multiple, "min_tasks": min_tasks},
        "sequencer": (previous or {}).get("sequencer"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(path, doc, header=f"# campaign.yaml -- {campaign_id}\n")
    return path, doc


def load(workspace, campaign_id: str) -> dict:
    path = workspace.paths.campaign_yaml(campaign_id)
    if not path.exists():
        raise CfError(f"no campaign {campaign_id!r} ({path})",
                      hint=f"plan it: cf campaign plan {campaign_id} --shard <run-id> --host <h>")
    data = read_yaml(path)
    if not isinstance(data, dict) or not data.get("shards"):
        raise CfError(f"{path} lists no shards")
    return data


def _watchdog_flags(watch: dict) -> str:
    flags = []
    for key, flag in (("p90_cost_per_task", "--p90-cost-per-task"),
                      ("p90_gate_failure_rate", "--p90-gate-failure-rate"),
                      ("multiple", "--multiple"), ("min_tasks", "--min-tasks")):
        if watch.get(key) is not None:
            flags += [flag, str(watch[key])]
    return " ".join(shlex.quote(f) for f in flags)


def render(workspace, campaign_id: str) -> dict:
    """Write ``campaigns/<id>/host/``: the sequencer and the two stdlib tools it runs."""
    from plan.render import render_file

    doc = load(workspace, campaign_id)
    host = manifest.resolve_host(workspace, str(doc["host"]))
    root = host["remote_root"]
    lines = []
    for shard in doc["shards"]:
        data = _run_doc(workspace, shard["run_id"])
        names = manifest.remote_names(data, shard["run_id"])
        args = [str(shard["index"]), shard["run_id"], f"{root}/{names['tend_root']}",
                f"{root}/{names['entrypoint']}", f"{float(shard['max_cost']):g}",
                str(shard["workers"]), str(shard["reviewers"])]
        lines.append("run_shard " + " ".join(shlex.quote(a) for a in args) + " || exit $?")
    ctx = {"campaign_id": campaign_id, "host": doc["host"],
           "tend_cmd_quoted": shlex.quote(host.get("tend_cmd") or ""),
           "state_dir": f"{root}/campaigns/{campaign_id}",
           "multiple": doc["watchdog"].get("multiple"),
           "watchdog_flags": _watchdog_flags(doc.get("watchdog") or {}),
           "shard_lines": "\n".join(lines)}
    text = render_file(templates_dir() / "campaign" / "sequencer.sh.tmpl", ctx)
    out = workspace.paths.campaign_dir(campaign_id) / "host"
    out.mkdir(parents=True, exist_ok=True)
    sequencer = out / "sequencer.sh"
    sequencer.write_text(text, encoding="utf-8")
    sequencer.chmod(0o755)
    for src in (skill_root() / "scripts" / "run" / "watchdog.py",
                skill_root() / "scripts" / "cost" / "usage.py"):
        shutil.copy2(src, out / src.name)
    record = {"path": str(sequencer), "sha256": sha256_file(sequencer),
              "host_dir": f"{root}/campaigns/{campaign_id}"}
    doc["sequencer"] = record
    write_yaml(workspace.paths.campaign_yaml(campaign_id), doc,
               header=f"# campaign.yaml -- {campaign_id}\n")
    return {"dir": str(out), "sequencer": record,
            "transfer": " ".join(["rsync", "-a", shlex.quote(str(out) + "/"),
                                  shlex.quote(f"{host['ssh']}:{record['host_dir']}/")]),
            "start": " ".join(["ssh", shlex.quote(host["ssh"]),
                               shlex.quote(f"nohup {record['host_dir']}/sequencer.sh "
                                           f"</dev/null >/dev/null 2>&1 &")])}


def status(workspace, campaign_id: str) -> dict:
    """Per shard: planned tasks, the harvest receipt, its locate counts and its cost."""
    doc = load(workspace, campaign_id)
    rows: list[dict[str, Any]] = []
    totals = {"tasks": 0, "documents": 0, "unresolved_documents": 0, "attempt_capped": 0,
              "usd": 0.0, "harvested": 0}
    for shard in doc["shards"]:
        receipt_path = workspace.paths.receipt_json(shard["run_id"])
        receipt = read_json(receipt_path, default=None) if receipt_path.exists() else None
        locate = (receipt or {}).get("locate") or {}
        cost = (receipt or {}).get("cost") or {}
        row = {"index": shard["index"], "run_id": shard["run_id"], "mode": shard.get("mode"),
               "tasks": shard["tasks"], "max_cost": shard["max_cost"],
               "harvested": receipt is not None,
               "complete": bool((receipt or {}).get("complete")),
               "gate_ok": bool(((receipt or {}).get("gate") or {}).get("ok")),
               "documents": locate.get("documents", 0),
               "unresolved_documents": locate.get("unresolved_documents", 0),
               "attempt_capped": locate.get("attempt_capped_documents", 0),
               "usd": cost.get("usd")}
        rows.append(row)
        totals["tasks"] += shard["tasks"]
        totals["documents"] += row["documents"]
        totals["unresolved_documents"] += row["unresolved_documents"]
        totals["attempt_capped"] += row["attempt_capped"]
        totals["usd"] += float(row["usd"] or 0)
        totals["harvested"] += 1 if receipt is not None else 0
    totals["usd"] = round(totals["usd"], 2)
    totals["unresolved_rate"] = (round(totals["unresolved_documents"] / totals["documents"], 4)
                                 if totals["documents"] else None)
    return {"id": campaign_id, "host": doc["host"], "shards": rows, "totals": totals,
            "max_cost": doc.get("max_cost"), "sequencer": doc.get("sequencer")}
