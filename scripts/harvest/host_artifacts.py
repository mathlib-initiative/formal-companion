"""Bring a locate run's host artifacts home beside its corpus.

New code, implementing references/locate-documents.md section 10 (what a
harvest lands) for docs/proposals/wikipedia-locate.md section 6 (cost and
spend).  The findings rsync carries ``*.json`` documents and nothing else, so
three files the bench and ``cf campaign status`` read stayed on the host:

* the gate's own log, ``<log_dir>/gate.jsonl`` -- ``log_dir`` is the host
  ``gate.json``'s, under the tend root -- one line per worker call;
* for a campaign shard, the sequencer's usage export
  ``<campaign dir>/usage-<run-id>.jsonl``, the only usage record left once it
  has pruned that shard's transcripts;
* for a campaign shard, the sequencer's canary scan
  ``<campaign dir>/canary-<run-id>.json``.

They land in ``runs/<run>/host/`` (``WorkspacePaths.run_host_dir``), never under
``corpus/<run>/``, whose tree is the receipt's manifest.  A file the host does
not have is recorded as absent, never an error: the findings are what make a
receipt complete, and a shard run with no ``CF_TRANSCRIPT_DIRS`` exports no
usage at all.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from cflib import hashing
from cflib.jsonio import read_json
from cflib.yamlio import read_yaml

#: the local names, in ``runs/<run>/host/``
GATE_LOG = "gate.jsonl"
USAGE = "usage.jsonl"
CANARY = "canary.json"


def campaign_shard(workspace, run_id: str, data: dict) -> tuple[str, str] | None:
    """``(campaign id, host campaign dir)`` for a shard, else None.

    The campaign comes from the shard's ``run.yaml`` (``locate.campaign``, which
    ``cf campaign plan`` stamps) and must list the run among its shards; the
    directory is the rendered sequencer's record, or
    ``<remote_root>/campaigns/<id>`` when it was never rendered.  The
    sequencer keys its files by run id, never by position.
    """
    block = data.get("locate") if isinstance(data.get("locate"), dict) else {}
    campaign_id = str(block.get("campaign") or "")
    if not campaign_id:
        return None
    path = workspace.paths.campaign_yaml(campaign_id)
    doc = read_yaml(path) if path.exists() else None
    if not isinstance(doc, dict):
        return None
    if not any(isinstance(s, dict) and s.get("run_id") == run_id for s in doc.get("shards") or []):
        return None
    sequencer = doc.get("sequencer") if isinstance(doc.get("sequencer"), dict) else {}
    host = data.get("host") if isinstance(data.get("host"), dict) else {}
    host_dir = str(sequencer.get("host_dir") or "")
    if not host_dir and host.get("remote_root"):
        host_dir = f"{str(host['remote_root']).rstrip('/')}/campaigns/{campaign_id}"
    return (campaign_id, host_dir) if host_dir else None


def remote_files(workspace, run_id: str, data: dict) -> list[dict]:
    """``[{name, remote}]``: the host paths of the artifacts this run can have."""
    out: list[dict] = []
    gate_json = Path(workspace.paths.gate_json(run_id))
    try:
        log_dir = str((read_json(gate_json, default=None) or {}).get("log_dir") or "")
    except Exception:  # noqa: BLE001 - an unreadable gate.json names no log
        log_dir = ""
    if log_dir:
        out.append({"name": GATE_LOG, "remote": f"{log_dir.rstrip('/')}/gate.jsonl"})
    shard = campaign_shard(workspace, run_id, data)
    if shard:
        campaign_id, host_dir = shard
        out.append({"name": USAGE, "remote": f"{host_dir}/usage-{run_id}.jsonl",
                    "campaign": campaign_id})
        out.append({"name": CANARY, "remote": f"{host_dir}/canary-{run_id}.json",
                    "campaign": campaign_id})
    return out


def commands(ssh_host: str, files: Sequence[dict], local_dir: Path) -> list[dict]:
    """The rsync each file takes, for the dry run to print."""
    out = []
    for f in files:
        argv = rsync_argv(ssh_host, f["remote"], Path(local_dir) / f["name"])
        out.append({"title": f"host artifact {f['name']}", "argv": argv,
                    "shell": " ".join(shlex.quote(a) for a in argv)})
    return out


def rsync_argv(ssh_host: str, remote: str, local: Path) -> list[str]:
    # --ignore-missing-args: an artifact the host never wrote is "absent", a
    # status, not a failed transfer
    return ["rsync", "-a", "--ignore-missing-args", f"{ssh_host}:{remote}", str(local)]


def fetch(ssh_host: str, files: Sequence[dict], local_dir: Path,
          runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict:
    """Fetch *files* into *local_dir*; ``{name: {remote, fetched, path?, sha256?, bytes?, reason?}}``.

    Each file goes to a scratch directory first and replaces the local copy only
    once it arrived, so a failed transfer leaves the previous harvest's copy as
    it was and says so.
    """
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    incoming = Path(tempfile.mkdtemp(prefix=".incoming-", dir=local_dir))
    try:
        for f in files:
            row = {"remote": f["remote"], "fetched": False}
            row.update({k: f[k] for k in ("campaign",) if k in f})
            target = incoming / f["name"]
            proc = runner(rsync_argv(ssh_host, f["remote"], target), text=True,
                          capture_output=True)
            if proc.returncode != 0:
                row["reason"] = (f"rsync exited {proc.returncode}: "
                                 f"{(proc.stderr or '').strip()[:300]}")
            elif not target.is_file():
                row["reason"] = "absent on the host"
            else:
                final = local_dir / f["name"]
                shutil.move(str(target), str(final))
                row.update(fetched=True, path=str(final), sha256=hashing.sha256_file(final),
                           bytes=final.stat().st_size)
            if not row["fetched"] and (local_dir / f["name"]).is_file():
                row["kept_previous"] = str(local_dir / f["name"])
            out[f["name"]] = row
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
    return out


def describe(block: dict) -> list[str]:
    """One line per artifact, for the harvest summary."""
    lines = []
    for name, row in sorted((block or {}).items()):
        if row.get("fetched"):
            lines.append(f"  host: {name} <- {row['remote']} ({row.get('bytes', 0)} bytes)")
        else:
            kept = " (the previous copy is kept)" if row.get("kept_previous") else ""
            lines.append(f"  host: {name} NOT fetched from {row['remote']}: "
                         f"{row.get('reason')}{kept}")
    return lines
