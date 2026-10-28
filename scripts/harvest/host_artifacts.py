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
  ``<campaign dir>/canary-<run-id>.json``;
* the bridge's kept agent envelopes, ``<tend root>/usage/*.json`` (one per
  agent call, scripts/run/agent_call.py), a directory: what prices a run
  that has no transcripts and no usage export (``cost.fleet.envelope_cost_block``);
* for an audited inventory run (``run.yaml`` ``locate.audit``,
  references/locate-documents.md section 13.10), the turn machine's
  ``<tend root>/audit/`` (its ``*.json``: each task's state and round files),
  a directory, which ``harvest.audit_summary`` reads for the receipt's
  ``audit`` block;
* for an unaudited locate run, the bridge's turn records
  (``scripts/run/review_record.py``, references/locate-documents.md sections 9 and
  10): ``<tend root>/contracts/`` (one ``*.json`` per agent call, a directory),
  ``<tend root>/needs-review.jsonl`` (every review of a contribution its worker
  marked ``needs_review``) and ``<tend root>/tamper.jsonl`` (every reviewer call
  whose change the guard undid), which ``harvest.turn_records`` reads for the
  receipt's ``needs_review``, ``worker_documents``, ``merged_not_worker`` and
  ``reviewer_tampered``.

They land in ``runs/<run>/host/`` (``WorkspacePaths.run_host_dir``), never under
``corpus/<run>/``, whose tree is the receipt's manifest.

**Expected or not.**  The gate log is EXPECTED: every task the host merged ran
the gate, which appends a line per call, so a remote locate run without one
lost the record of every gate call; a harvest that cannot bring it home is
INCOMPLETE (the receipt names it in ``host_artifacts_missing``).  So is an
audited run's ``audit/``: every task the host merged was reviewed by the turn
machine, which writes its state there, and without it no document's audit is
known.  So is ``contracts/`` for a run whose ``run.yaml`` pins the bridge's
``bin/review_record.py`` (``locate.gate_files``): that bridge records every worker
call there, and without it no merged document can be checked against its worker's
commit.  The two ledgers are not: a run with no ``needs_review`` and no tampering
writes neither.  The usage
export and the canary scan are not: a shard run with no ``CF_TRANSCRIPT_DIRS``
exports no usage at all, and a file the host does not have is recorded as
absent, never an error.

**How a file is fetched.**  The host is asked first whether the file is there
(``ssh <host> test -f <path>``: 0 there, 1 absent, anything else a failure to
ask), and only a file that is there is copied, by a plain ``rsync -a`` that any
rsync runs.  The copy used to pass ``--ignore-missing-args`` (rsync 3.1) so an
absent file was no error; macOS's ``/usr/bin/rsync`` 2.6.9 rejects the option,
so the gate log was never fetched while the harvest still said COMPLETE (field
defect H1).
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
from harvest import audit_summary

#: the local names, in ``runs/<run>/host/``
GATE_LOG = "gate.jsonl"
USAGE = "usage.jsonl"
CANARY = "canary.json"
#: a directory: the agent envelopes ``agent_call.py`` kept in the tend root's ``usage/``
ENVELOPES = "envelopes"
#: a directory: an audited run's ``<tend root>/audit/`` (``run/audit_turn.py``'s records)
AUDIT = "audit"
#: an unaudited locate run's turn records (``run/review_record.py``): the contracts (a
#: directory), the needs_review ledger and the tamper ledger
CONTRACTS = "contracts"
NEEDS_REVIEW = "needs-review.jsonl"
TAMPER = "tamper.jsonl"
#: the staged helper whose pin in ``run.yaml`` ``locate.gate_files`` says the run's
#: bridge records contracts
RECORDER_PIN = "bin/review_record.py"


def records_turns(data: dict) -> bool:
    """Does this run's staged bridge keep turn records (``RECORDER_PIN`` in its pins)?"""
    block = data.get("locate") if isinstance(data.get("locate"), dict) else {}
    pins = block.get("gate_files") if isinstance(block.get("gate_files"), dict) else {}
    return RECORDER_PIN in pins


def host_tend_root(data: dict, log_dir: str = "") -> str:
    """The run's tend root on the host, from ``run.yaml``, else the gate log's parent; '' if neither.

    ``cf plan-locate`` records ``host.remote_root`` and ``remote.tend_root``
    (relative to it); the host ``gate.json``'s ``log_dir`` is ``<tend root>/gate-log``.
    """
    host = data.get("host") if isinstance(data.get("host"), dict) else {}
    remote = data.get("remote") if isinstance(data.get("remote"), dict) else {}
    root, tend = str(host.get("remote_root") or "").rstrip("/"), str(remote.get("tend_root") or "")
    if root and tend:
        return tend.rstrip("/") if tend.startswith("/") else f"{root}/{tend.strip('/')}"
    log_dir = log_dir.rstrip("/")
    if log_dir.endswith("/gate-log"):
        return log_dir[: -len("/gate-log")]
    return ""


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
        out.append({"name": GATE_LOG, "remote": f"{log_dir.rstrip('/')}/gate.jsonl",
                    "expected": True})
    shard = campaign_shard(workspace, run_id, data)
    if shard:
        campaign_id, host_dir = shard
        out.append({"name": USAGE, "remote": f"{host_dir}/usage-{run_id}.jsonl",
                    "campaign": campaign_id, "expected": False})
        out.append({"name": CANARY, "remote": f"{host_dir}/canary-{run_id}.json",
                    "campaign": campaign_id, "expected": False})
    tend_root = host_tend_root(data, log_dir)
    if tend_root:
        out.append({"name": ENVELOPES, "remote": f"{tend_root}/usage", "dir": True,
                    "expected": False})
    if tend_root and audit_summary.plan_of(data):
        out.append({"name": AUDIT, "remote": f"{tend_root}/audit", "dir": True,
                    "expected": True})
    elif tend_root:
        # an unaudited run's turn records; an audited run's turns are the turn machine's
        out.append({"name": CONTRACTS, "remote": f"{tend_root}/{CONTRACTS}", "dir": True,
                    "expected": records_turns(data)})
        out.append({"name": NEEDS_REVIEW, "remote": f"{tend_root}/{NEEDS_REVIEW}",
                    "expected": False})
        out.append({"name": TAMPER, "remote": f"{tend_root}/{TAMPER}", "expected": False})
    return out


def commands(ssh_host: str, files: Sequence[dict], local_dir: Path) -> list[dict]:
    """The two steps each file takes (ask the host, then copy), for the dry run to print."""
    out = []
    for f in files:
        directory = bool(f.get("dir"))
        for title, argv in ((f"host artifact {f['name']}: is it there",
                             exists_argv(ssh_host, f["remote"], directory=directory)),
                            (f"host artifact {f['name']}",
                             rsync_argv(ssh_host, f["remote"], Path(local_dir) / f["name"],
                                        directory=directory))):
            out.append({"title": title, "argv": argv,
                        "shell": " ".join(shlex.quote(a) for a in argv)})
    return out


def exists_argv(ssh_host: str, remote: str, *, directory: bool = False) -> list[str]:
    """``ssh <host> test -f|-d <remote>``: exit 0 it is there, 1 it is not."""
    return ["ssh", ssh_host, f"test {'-d' if directory else '-f'} {shlex.quote(remote)}"]


def rsync_argv(ssh_host: str, remote: str, local: Path, *, directory: bool = False) -> list[str]:
    """A plain copy of what the host was asked about first (:func:`exists_argv`).

    No ``--ignore-missing-args``: rsync 2.6.9, the one macOS ships, rejects it.
    A directory (the envelopes, an audit's records, the contracts) brings its ``*.json``
    files and nothing else.
    """
    if directory:
        return ["rsync", "-a", "--no-links", "--prune-empty-dirs", "--include=*.json",
                "--include=*/", "--exclude=*", f"{ssh_host}:{remote.rstrip('/')}/",
                str(local) + "/"]
    return ["rsync", "-a", f"{ssh_host}:{remote}", str(local)]


def _dir_digest(directory: Path) -> tuple[str, int, int]:
    """``(sha256 over the sorted 'path<TAB>sha256' lines, files, bytes)`` of a fetched directory."""
    lines, count, size = [], 0, 0
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        lines.append(f"{path.relative_to(directory).as_posix()}\t{hashing.sha256_file(path)}")
        count += 1
        size += path.stat().st_size
    return hashing.sha256_text("\n".join(lines)), count, size


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
            row = {"remote": f["remote"], "fetched": False,
                   "expected": bool(f.get("expected"))}
            row.update({k: f[k] for k in ("campaign",) if k in f})
            target = incoming / f["name"]
            directory = bool(f.get("dir"))
            asked = runner(exists_argv(ssh_host, f["remote"], directory=directory), text=True,
                           capture_output=True)
            proc = None
            if asked.returncode == 1:
                row["reason"] = "absent on the host"
            elif asked.returncode != 0:
                row["reason"] = (f"could not ask the host whether it is there (ssh exited "
                                 f"{asked.returncode}: {(asked.stderr or '').strip()[:300]})")
            else:
                if directory:
                    target.mkdir(parents=True, exist_ok=True)
                proc = runner(rsync_argv(ssh_host, f["remote"], target, directory=directory),
                              text=True, capture_output=True)
            if proc is None:
                pass
            elif proc.returncode != 0:
                row["reason"] = (f"rsync exited {proc.returncode}: "
                                 f"{(proc.stderr or '').strip()[:300]}")
            elif directory:
                final = local_dir / f["name"]
                if final.is_dir() and not final.is_symlink():
                    shutil.rmtree(final)
                shutil.move(str(target), str(final))
                digest, count, size = _dir_digest(final)
                row.update(fetched=True, path=str(final), sha256=digest, files=count,
                           bytes=size)
            elif not target.is_file():
                row["reason"] = "the host has it, and rsync reported success with no file"
            else:
                final = local_dir / f["name"]
                shutil.move(str(target), str(final))
                row.update(fetched=True, path=str(final), sha256=hashing.sha256_file(final),
                           bytes=final.stat().st_size)
            if not row["fetched"] and (local_dir / f["name"]).exists():
                row["kept_previous"] = str(local_dir / f["name"])
            out[f["name"]] = row
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
    return out


def missing(block: dict | None) -> list[str]:
    """The EXPECTED artifacts this harvest did not bring home: each makes it incomplete.

    A previous harvest's copy does not stand in for one: it may predate the
    run's last gate call.
    """
    return sorted(name for name, row in (block or {}).items()
                  if isinstance(row, dict) and row.get("expected") and not row.get("fetched"))


def describe(block: dict) -> list[str]:
    """One line per artifact, for the harvest summary."""
    lines = []
    for name, row in sorted((block or {}).items()):
        if row.get("fetched"):
            files = f"{row['files']} file(s), " if "files" in row else ""
            lines.append(f"  host: {name} <- {row['remote']} ({files}{row.get('bytes', 0)} bytes)")
        else:
            kept = " (the previous copy is kept)" if row.get("kept_previous") else ""
            label = "MISSING (expected; the harvest is INCOMPLETE)" if row.get("expected") \
                else "NOT fetched"
            lines.append(f"  host: {name} {label} from {row['remote']}: "
                         f"{row.get('reason')}{kept}")
    return lines
