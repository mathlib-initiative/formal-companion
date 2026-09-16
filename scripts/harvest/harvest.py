"""Bring a run's findings home, with a receipt that proves the corpus is whole.

New code.  The original was one line of RUNBOOK.md ("Findings land as
findings/<item>.json on main of ~/Desktop/tao-diff-run") plus an rsync typed by
hand, and it is exactly where the audit lost a finding: ``findings-hunt/`` held
762 files locally where the server had 763, and the missing
``exercise-1-1-17.json`` was a real CONTENT finding.  Every downstream number is
a function of the corpus, so a corpus that is quietly a file short makes every
count quietly wrong -- and all three counting errors ran toward "cleaner".

So the harvest is not a copy, it is a reconciliation:

1. list the SOURCE (remote ``ls``) -- that list is the expected set, by NAME;
2. transfer;
3. list the DESTINATION and diff the two sets;
4. write ``RECEIPT.json`` with expected/actual counts, the missing names, a
   sha256 manifest of what landed, and a copy of run.yaml;
5. refuse to mark the harvest complete on any mismatch.

Re-running is safe and is the normal way to close a gap: the receipt records the
previous count and the delta.
"""
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):  # `python3 scripts/harvest/harvest.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvest._compat import (CfError, hashing, iso_now, iter_finding_files,
                             read_json, write_json)

RECEIPT_NAME = "RECEIPT.json"
#: how many missing names an error message spells out before truncating
MAX_NAMED = 20


def parse_source(spec: str) -> dict:
    """``host:/path`` -> remote source, anything else -> a local directory.

    A local path that happens to contain a colon wins if it exists on disk, so
    ``/tmp/a:b`` is not mistaken for a host.
    """
    if Path(spec).exists():
        return {"kind": "local", "path": str(Path(spec).resolve())}
    if ":" in spec:
        host, _, path = spec.partition(":")
        if host and path:
            return {"kind": "remote", "host": host, "path": path.rstrip("/")}
    return {"kind": "local", "path": str(Path(spec).resolve())}


def _run(argv: Sequence[str], what: str) -> str:
    proc = subprocess.run(list(argv), text=True, capture_output=True)
    if proc.returncode != 0:
        raise CfError(f"{what} failed ({proc.returncode}): {proc.stderr.strip()[:300]}",
                      hint="command was: " + " ".join(shlex.quote(a) for a in argv))
    return proc.stdout


def list_commands(source: dict, dest: Path) -> list[dict]:
    """The exact commands a remote harvest runs, for the dry run to print."""
    if source["kind"] != "remote":
        return []
    remote = f"{source['host']}:{source['path']}/"
    ls = ["ssh", source["host"], f"ls -1 {shlex.quote(source['path'])}"]
    rsync = ["rsync", "-a", "--prune-empty-dirs", "--include=*.json", "--include=*/",
             "--exclude=*", remote, str(dest) + "/"]
    shas = ["ssh", source["host"],
            f"cd {shlex.quote(source['path'])} && shasum -a 256 *.json"]
    return [
        {"title": "expected set (the source listing IS the expectation)", "argv": ls,
         "shell": " ".join(shlex.quote(a) for a in ls)},
        {"title": "transfer findings", "argv": rsync,
         "shell": " ".join(shlex.quote(a) for a in rsync)},
        {"title": "source checksums (--verify-sha)", "argv": shas,
         "shell": " ".join(shlex.quote(a) for a in shas)},
    ]


def source_names(source: dict) -> list[str]:
    """The expected findings file names, read from the source itself."""
    if source["kind"] == "remote":
        out = _run(["ssh", source["host"], f"ls -1 {shlex.quote(source['path'])}"],
                   "remote listing")
        names = [line.strip() for line in out.splitlines() if line.strip()]
    else:
        directory = Path(source["path"])
        if not directory.is_dir():
            raise CfError(f"source directory does not exist: {directory}")
        names = [p.name for p in sorted(directory.iterdir()) if p.is_file()]
    return sorted(n for n in names if n.endswith(".json") and not n.startswith("."))


def source_shas(source: dict) -> dict[str, str]:
    """{name: sha256} at the SOURCE (``--verify-sha``; remote uses shasum -a 256)."""
    if source["kind"] == "remote":
        out = _run(["ssh", source["host"],
                    f"cd {shlex.quote(source['path'])} && shasum -a 256 *.json"],
                   "remote checksums")
        shas = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                shas[Path(parts[-1]).name] = parts[0]
        return shas
    directory = Path(source["path"])
    return {p.name: hashing.sha256_file(p) for p in sorted(directory.iterdir())
            if p.is_file() and p.suffix == ".json"}


def transfer(source: dict, dest: Path) -> None:
    """Copy the findings to *dest*; rsync for remote, a plain copy for local."""
    dest.mkdir(parents=True, exist_ok=True)
    if source["kind"] == "remote":
        argv = ["rsync", "-a", "--prune-empty-dirs", "--include=*.json", "--include=*/",
                "--exclude=*", f"{source['host']}:{source['path']}/", str(dest) + "/"]
        _run(argv, "rsync")
        return
    for path in sorted(Path(source["path"]).iterdir()):
        if path.is_file() and path.suffix == ".json" and not path.name.startswith("."):
            shutil.copy2(path, dest / path.name)


def build_receipt(*, run_id: str, source: dict, expected: Sequence[str], dest: Path,
                  book_sha: str = "", previous: dict | None = None,
                  source_manifest: dict[str, str] | None = None,
                  run_yaml: str | None = None) -> dict:
    """Reconcile the destination against the expected set.

    ``expected`` is the SOURCE listing, never a number someone remembered.  The
    receipt is complete only when nothing is missing and (when checksums were
    collected) every file matches the source byte for byte.
    """
    actual_paths = iter_finding_files(dest)
    actual = sorted(p.name for p in actual_paths)
    expected_set, actual_set = set(expected), set(actual)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    manifest = hashing.manifest(actual_paths, root=dest)
    mismatched = sorted(
        name for name, sha in (source_manifest or {}).items()
        if name in manifest and manifest[name] != sha
    )
    receipt = {
        "run_id": run_id,
        "harvested_at": iso_now(),
        "book_sha": book_sha,
        "source": source,
        "destination": str(dest),
        "expected": {"count": len(expected_set), "names": sorted(expected_set)},
        "actual": {"count": len(actual_set)},
        "missing": missing,
        "extra": extra,
        "sha_mismatched": mismatched,
        "sha_verified": bool(source_manifest),
        "complete": not missing and not mismatched,
        "manifest_algo": "sha256",
        "manifest": manifest,
        "run_yaml": run_yaml or "",
        "run_yaml_sha": (hashing.sha256_file(Path(dest) / run_yaml) if run_yaml
                         and (Path(dest) / run_yaml).exists() else ""),
    }
    if previous:
        receipt["previous"] = {
            "harvested_at": previous.get("harvested_at"),
            "actual": (previous.get("actual") or {}).get("count"),
            "complete": previous.get("complete"),
            "gained": sorted(actual_set - set((previous.get("manifest") or {}).keys())),
            "lost": sorted(set((previous.get("manifest") or {}).keys()) - actual_set),
        }
    return receipt


def read_receipt(dest: Path) -> dict | None:
    """The previous RECEIPT.json in *dest*, when there is one."""
    path = Path(dest) / RECEIPT_NAME
    if not path.exists():
        return None
    try:
        return read_json(path, default=None)
    except Exception:
        return None


def describe(receipt: dict) -> str:
    """One-screen summary of a receipt, complete or not."""
    lines = [
        f"run {receipt['run_id']}: expected {receipt['expected']['count']}, "
        f"got {receipt['actual']['count']} -> "
        + ("COMPLETE" if receipt["complete"] else "INCOMPLETE"),
        f"  source: {receipt['source'].get('host', 'local')}:{receipt['source']['path']}",
        f"  corpus: {receipt['destination']}",
    ]
    if receipt["missing"]:
        named = ", ".join(receipt["missing"][:MAX_NAMED])
        more = "" if len(receipt["missing"]) <= MAX_NAMED else f" (+{len(receipt['missing']) - MAX_NAMED} more)"
        lines.append(f"  MISSING {len(receipt['missing'])}: {named}{more}")
    if receipt["extra"]:
        lines.append(f"  extra {len(receipt['extra'])}: {', '.join(receipt['extra'][:MAX_NAMED])}")
    if receipt["sha_mismatched"]:
        lines.append(f"  SHA MISMATCH {len(receipt['sha_mismatched'])}: "
                     f"{', '.join(receipt['sha_mismatched'][:MAX_NAMED])}")
    prev = receipt.get("previous")
    if prev:
        lines.append(f"  previous harvest: {prev.get('actual')} files at {prev.get('harvested_at')}"
                     f" (+{len(prev.get('gained') or [])} / -{len(prev.get('lost') or [])})")
    # The corpus is the calibration baseline, so it wants to be committed -- but the
    # campaign repo is the operator's (it holds book quotes and has no remote), so the
    # command never commits on their behalf.
    lines.append(f"  commit it yourself:  git -C {Path(receipt['destination']).parents[1]} add "
                 f"corpus/{receipt['run_id']} && git -C {Path(receipt['destination']).parents[1]} "
                 f"commit -m 'corpus: {receipt['run_id']}'")
    return "\n".join(lines)


def harvest(*, run_id: str, source: dict, dest: Path, book_sha: str = "",
            run_yaml_src: Path | None = None, verify_sha: bool = False,
            execute: bool = True) -> dict:
    """Transfer, reconcile and write RECEIPT.json. Returns the receipt.

    ``execute=False`` only makes sense for a remote source: it prints the
    commands and returns a receipt marked ``planned``.
    """
    dest = Path(dest)
    if source["kind"] == "remote" and not execute:
        raise CfError("remote harvest needs --execute (it ssh/rsyncs to the host)")
    previous = read_receipt(dest)
    expected = source_names(source)
    if not expected:
        raise CfError(f"source holds no *.json findings: {source}",
                      hint="check the run actually merged findings before harvesting")
    shas = source_shas(source) if verify_sha else None
    transfer(source, dest)

    run_yaml_name = ""
    if run_yaml_src and Path(run_yaml_src).exists():
        shutil.copy2(run_yaml_src, dest / "run.yaml")
        run_yaml_name = "run.yaml"

    receipt = build_receipt(run_id=run_id, source=source, expected=expected, dest=dest,
                            book_sha=book_sha, previous=previous, source_manifest=shas,
                            run_yaml=run_yaml_name)
    write_json(dest / RECEIPT_NAME, receipt, indent=1, sort_keys=True)
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    parser.add_argument("--from", dest="source", required=True,
                        help="host:/path/to/findings or a local directory")
    parser.add_argument("--dest", required=True, help="corpus/<run-id> directory")
    parser.add_argument("--book-sha", default="")
    parser.add_argument("--run-yaml", default=None)
    parser.add_argument("--verify-sha", action="store_true")
    parser.add_argument("--execute", action="store_true", help="required for a remote source")
    args = parser.parse_args(argv)
    source = parse_source(args.source)
    if source["kind"] == "remote" and not args.execute:
        for step in list_commands(source, Path(args.dest)):
            print(f"# {step['title']}\n{step['shell']}")
        return 0
    receipt = harvest(run_id=args.run_id, source=source, dest=Path(args.dest),
                      book_sha=args.book_sha,
                      run_yaml_src=Path(args.run_yaml) if args.run_yaml else None,
                      verify_sha=args.verify_sha, execute=True)
    print(describe(receipt))
    return 0 if receipt["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
