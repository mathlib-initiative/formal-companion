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

import re
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
#: a campaign id names a directory here and on the host and is written into a
#: shell script: nothing a shell would expand; and it is sealed into every record set
#: of its shards, whose consumer admits at most 128 characters
#: (``cflib.vocab.RECORDSET_TOKEN``, of which this is a narrower spelling)
CAMPAIGN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
#: the host-side tools render copies beside the sequencer
HOST_TOOLS = (("run", "watchdog.py"), ("cost", "usage.py"), ("run", "canary_scan.py"))


def check_campaign_id(campaign_id: str) -> str:
    if not CAMPAIGN_ID.match(str(campaign_id or "")):
        raise CfError(f"campaign id {campaign_id!r} is not letters, digits, '.', '_' and '-' "
                      f"(at most 128, starting with a letter or digit)",
                      hint="the id names a directory on the host and is written into the "
                           "sequencer script; pick a plain one, e.g. wpm-tier-a")
    return campaign_id


def check_host_paths(value: str | None, what: str, *, several: bool = False) -> str | None:
    """*value* when each of its paths is one the sequencer can use on the host, else a refusal.

    Render writes these paths shell-quoted into the sequencer, so nothing
    expands them there: a path is absolute, or ``~`` / ``~/...``, which the
    sequencer itself reads against the host's ``$HOME``.  A relative path would
    be relative to wherever the sequencer was started, and a ``$HOME/...``
    would stay those characters.  *several* reads *value* as a colon-separated
    list.
    """
    if not value:
        return value
    for part in (str(value).split(":") if several else [str(value)]):
        if part and not (part.startswith("/") or part == "~" or part.startswith("~/")):
            raise CfError(f"{what}: {part!r} is not an absolute host path",
                          hint="write it as /absolute/path or ~/path: the sequencer reads a "
                               "leading ~/ against the host's $HOME and expands nothing else, "
                               "since every rendered path is quoted")
    return value


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


def _stamp(workspace, campaign_id: str, run_id: str) -> None:
    """Record ``locate.campaign: <id>`` in the shard's run.yaml (the export seals it)."""
    path = workspace.paths.run_yaml(run_id)
    data = _run_doc(workspace, run_id)
    block = data.get("locate") if isinstance(data.get("locate"), dict) else {}
    if block.get("campaign") == campaign_id:
        return
    block["campaign"] = campaign_id
    data["locate"] = block
    write_yaml(path, data)


def _pilot(workspace, pilot: str) -> str:
    """A run that passed its smoke test (run.yaml ``run.smoke_verified``), or a refusal."""
    data = _run_doc(workspace, pilot)
    block = data.get("run") if isinstance(data.get("run"), dict) else {}
    if not block.get("smoke_verified"):
        raise CfError(f"pilot {pilot!r} records no passed smoke test (run.smoke_verified)",
                      hint="the sequencer launches every shard at full concurrency, so the "
                           "pilot must have gone worker -> gate -> reviewer -> merge first: "
                           f"`cf run {pilot} --host <host> --i-have-consent --stage raise "
                           "--smoke-verified --execute`")
    return pilot


def plan(workspace, campaign_id: str, shards: list[str], *, host: str,
         p90_cost_per_task: float | None = None, p90_gate_failure_rate: float | None = None,
         multiple: float | None = None, min_tasks: int | None = None, pilot: str | None = None,
         transcript_dirs: str | None = None, canaries: str | None = None) -> tuple[Path, dict]:
    """Write ``campaigns/<id>/campaign.yaml`` and stamp each shard's run.yaml.

    Idempotent for the same arguments, and a re-plan reads every shard's
    run.yaml again (its cap, task count and limits) while keeping what it is
    not given: the watchdog's thresholds (``multiple`` 2 and ``min_tasks`` 5
    the first time), the pilot, the transcript directories and the canaries.
    A shard already stamped with another campaign is refused, and so is a
    re-plan of a rendered campaign that does not keep its shards, in order, as
    a prefix of the new list.
    """
    check_campaign_id(campaign_id)
    if not shards:
        raise CfError("a campaign needs at least one shard", hint="pass --shard <run-id>, in order")
    if len(set(shards)) != len(shards):
        raise CfError("a shard is named twice", hint="each run is one shard, once")
    check_host_paths(transcript_dirs, "--transcript-dirs", several=True)
    check_host_paths(canaries, "--canaries")
    path = workspace.paths.campaign_yaml(campaign_id)
    previous = read_yaml(path) if path.exists() else {}
    previous = previous if isinstance(previous, dict) else {}
    watch = previous.get("watchdog") if isinstance(previous.get("watchdog"), dict) else {}

    def kept(value, key: str, default):
        return value if value is not None else (watch[key] if watch.get(key) is not None
                                                else default)

    p90_cost_per_task = kept(p90_cost_per_task, "p90_cost_per_task", None)
    p90_gate_failure_rate = kept(p90_gate_failure_rate, "p90_gate_failure_rate", None)
    multiple, min_tasks = kept(multiple, "multiple", 2.0), kept(min_tasks, "min_tasks", 5)
    if multiple <= 1:
        raise CfError(f"--multiple {multiple} would drain at or below the pilot's p90",
                      hint="the watchdog drains at a MULTIPLE of the p90, e.g. 2")
    workspace.check_host(host)
    rows = [shard_record(workspace, run_id, i, host) for i, run_id in enumerate(shards, 1)]
    for run_id in shards:
        stamped = (_run_doc(workspace, run_id).get("locate") or {}).get("campaign")
        if stamped and stamped != campaign_id:
            raise CfError(f"shard {run_id!r} belongs to campaign {stamped!r}",
                          hint="a shard runs in one campaign; plan another run for this one")
    before = [str(s.get("run_id")) for s in previous.get("shards") or [] if isinstance(s, dict)]
    if previous.get("sequencer") and before != list(shards)[:len(before)]:
        raise CfError(f"campaign {campaign_id!r} was rendered with shards {before}; a re-plan "
                      f"may only append to them", hint="plan a new campaign id for another order")
    if pilot:
        _pilot(workspace, pilot)
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
        "pilot": pilot or previous.get("pilot"),
        "transcript_dirs": transcript_dirs if transcript_dirs is not None
        else previous.get("transcript_dirs"),
        "canaries": canaries if canaries is not None else previous.get("canaries"),
        "sequencer": previous.get("sequencer"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(path, doc, header=f"# campaign.yaml -- {campaign_id}\n")
    for run_id in shards:
        _stamp(workspace, campaign_id, run_id)
    return path, doc


def load(workspace, campaign_id: str) -> dict:
    check_campaign_id(campaign_id)
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


def _unchanged(workspace, campaign_id: str, host: str, shard: dict) -> dict:
    """The shard as its run.yaml has it now, refused when that is not what was planned.

    The sequencer runs each shard with campaign.yaml's cap and limits, and the
    harvest finds a shard's usage export and canary report by its stamp, so a
    shard re-planned since ``cf campaign plan`` (another cap, more tasks, a
    stamp it lost) is refused here rather than run on stale figures.
    """
    run_id = str(shard["run_id"])
    fresh = shard_record(workspace, run_id, int(shard["index"]), host)
    changed = [f"{key} {shard.get(key)!r} -> {fresh[key]!r}"
               for key in ("tasks", "max_cost", "workers", "reviewers")
               if fresh[key] != shard.get(key)]
    stamp = (_run_doc(workspace, run_id).get("locate") or {}).get("campaign")
    if stamp != campaign_id:
        changed.append(f"locate.campaign {stamp!r}, not {campaign_id!r}")
    if changed:
        raise CfError(f"shard {run_id!r} differs from campaign {campaign_id!r}'s record of it: "
                      + "; ".join(changed),
                      hint=f"a re-planned shard is planned into the campaign again: re-run "
                           f"`cf campaign plan {campaign_id}` with the same --shard list (it "
                           "keeps the thresholds it is not given), then render")
    return fresh


def render(workspace, campaign_id: str) -> dict:
    """Write ``campaigns/<id>/host/``: the sequencer and the stdlib tools it runs."""
    from plan.render import render_file

    doc = load(workspace, campaign_id)
    host = manifest.resolve_host(workspace, str(doc["host"]))
    if not doc.get("pilot"):
        raise CfError(f"campaign {campaign_id!r} records no smoke-tested pilot",
                      hint="the sequencer launches every shard at full concurrency without a "
                           "smoke test of its own: re-plan with `cf campaign plan "
                           f"{campaign_id} --shard <run-id> --host {doc['host']} --pilot <run-id>`")
    _pilot(workspace, str(doc["pilot"]))
    root = host["remote_root"]
    lines = []
    for shard in doc["shards"]:
        fresh = _unchanged(workspace, campaign_id, str(doc["host"]), shard)
        names = manifest.remote_names(_run_doc(workspace, fresh["run_id"]), fresh["run_id"])
        args = [str(fresh["index"]), fresh["run_id"], f"{root}/{names['tend_root']}",
                f"{root}/{names['entrypoint']}", f"{root}/{names['gate']}",
                f"{float(fresh['max_cost']):g}", str(fresh["workers"]), str(fresh["reviewers"])]
        lines.append("run_shard " + " ".join(shlex.quote(a) for a in args) + " || exit $?")
    if doc.get("transcript_dirs"):
        transcript_dirs = str(check_host_paths(doc["transcript_dirs"], "campaign.yaml "
                                               "transcript_dirs", several=True))
    else:
        transcript_dirs = str(check_host_paths(host.get("transcript_dirs") or "",
                                               f"workspace.yaml hosts.{doc['host']}."
                                               "transcript_dirs", several=True))
    check_host_paths(doc.get("canaries"), "campaign.yaml canaries")
    ctx = {"campaign_id": campaign_id, "host": doc["host"],
           "tend_cmd_quoted": shlex.quote(host.get("tend_cmd") or ""),
           "state_dir_quoted": shlex.quote(f"{root}/campaigns/{campaign_id}"),
           "transcript_dirs_quoted": shlex.quote(transcript_dirs),
           "canaries_quoted": shlex.quote(str(doc.get("canaries") or "")),
           "multiple": doc["watchdog"].get("multiple"),
           "watchdog_flags": _watchdog_flags(doc.get("watchdog") or {}),
           "shard_ids": " ".join(shlex.quote(str(s["run_id"])) for s in doc["shards"]),
           "shard_lines": "\n".join(lines)}
    text = render_file(templates_dir() / "campaign" / "sequencer.sh.tmpl", ctx)
    out = workspace.paths.campaign_dir(campaign_id) / "host"
    out.mkdir(parents=True, exist_ok=True)
    sequencer = out / "sequencer.sh"
    sequencer.write_text(text, encoding="utf-8")
    sequencer.chmod(0o755)
    for package, name in HOST_TOOLS:
        shutil.copy2(skill_root() / "scripts" / package / name, out / name)
    record = {"path": str(sequencer), "sha256": sha256_file(sequencer),
              "host_dir": f"{root}/campaigns/{campaign_id}"}
    doc["sequencer"] = record
    write_yaml(workspace.paths.campaign_yaml(campaign_id), doc,
               header=f"# campaign.yaml -- {campaign_id}\n")
    script = shlex.quote(f"{record['host_dir']}/sequencer.sh")
    # rsync makes only the last directory of its destination, and nothing else
    # makes <remote root>/campaigns on a host's first campaign
    mkdir = "mkdir -p " + shlex.quote(record["host_dir"])
    return {"dir": str(out), "sequencer": record, "transcript_dirs": transcript_dirs,
            "canaries": str(doc.get("canaries") or ""),
            "transfer": " ".join(["ssh", shlex.quote(host["ssh"]), shlex.quote(mkdir), "&&",
                                  "rsync", "-a", shlex.quote(str(out) + "/"),
                                  shlex.quote(f"{host['ssh']}:{record['host_dir']}/")]),
            "start": " ".join(["ssh", shlex.quote(host["ssh"]),
                               shlex.quote(f"nohup {script} </dev/null >/dev/null 2>&1 &")])}


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
        # three states, never two: a receipt with no gate block was never gated
        # (`cf harvest --no-gate`), which is not a gate that failed
        gate = (receipt or {}).get("gate")
        gated = isinstance(gate, dict) and bool(gate)
        row = {"index": shard["index"], "run_id": shard["run_id"], "mode": shard.get("mode"),
               "tasks": shard["tasks"], "max_cost": shard["max_cost"],
               "harvested": receipt is not None,
               "complete": bool((receipt or {}).get("complete")),
               "gated": gated, "gate_ok": bool(gated and gate.get("ok")),
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
