"""Bring a run's findings home, with a receipt that proves the corpus is whole.

New code.  The original was one line of the v1 campaign's runbook ("Findings land
as findings/<item>.json on main of the corpus repo") plus an rsync typed by
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
from typing import Mapping, Sequence

if __package__ in (None, ""):  # `python3 scripts/harvest/harvest.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvest._compat import (NON_FINDING_FILES, CfError, finding_name, hashing,
                             is_finding_name, iso_now, iter_finding_files, read_json,
                             vocab, write_json)

RECEIPT_NAME = "RECEIPT.json"
#: how many missing names an error message spells out before truncating
MAX_NAMED = 20
#: The remote listing and checksums, RECURSIVE: a locate run's findings are
#: ``findings/<task-id>/<source-id>.json`` (docs/proposals/wikipedia-locate.md
#: section 3), so ``ls -1`` over the findings directory listed task directories
#: and never a document.  NUL-separated, so no name is split; a flat
#: faithfulness directory lists exactly what ``ls -1`` listed.
REMOTE_LIST = "cd {path} && find . -type f -name '*.json' -print0"
REMOTE_SHAS = "cd {path} && find . -type f -name '*.json' -print0 | xargs -0 shasum -a 256"


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
    ls = ["ssh", source["host"], REMOTE_LIST.format(path=shlex.quote(source["path"]))]
    rsync = ["rsync", "-a", "--no-links", "--prune-empty-dirs", "--include=*.json",
             "--include=*/", "--exclude=*", remote, str(dest) + "/"]
    shas = ["ssh", source["host"], REMOTE_SHAS.format(path=shlex.quote(source["path"]))]
    return [
        {"title": "expected set (the source listing IS the expectation)", "argv": ls,
         "shell": " ".join(shlex.quote(a) for a in ls)},
        {"title": "transfer findings", "argv": rsync,
         "shell": " ".join(shlex.quote(a) for a in rsync)},
        {"title": "source checksums (--verify-sha)", "argv": shas,
         "shell": " ".join(shlex.quote(a) for a in shas)},
    ]


def source_names(source: dict) -> list[str]:
    """The expected findings file names, read from the source itself.

    The exclusion list is the one ``iter_finding_files`` uses at the
    DESTINATION.  It has to be: the expected set is compared against that
    listing, and when only one side filtered, a re-harvest (or an import of a
    landed corpus, which carries its own RECEIPT.json and manifest.json) reported
    those names as MISSING -- files that were on disk in both places.
    """
    if source["kind"] == "remote":
        out = _run(["ssh", source["host"], REMOTE_LIST.format(path=shlex.quote(source["path"]))],
                   "remote listing")
        names = [_strip_dot(n) for n in out.split("\0") if n.strip()]
    else:
        directory = Path(source["path"])
        if not directory.is_dir():
            raise CfError(f"source directory does not exist: {directory}")
        names = [finding_name(p, directory) for p in iter_finding_files(directory)]
    return sorted({n for n in names if is_finding_name(n)})


def _strip_dot(name: str) -> str:
    """``./t-1/a.json`` -> ``t-1/a.json``: a `find .` path as a receipt name."""
    name = name.strip("\n")
    return name[2:] if name.startswith("./") else name


def source_shas(source: dict) -> dict[str, str]:
    """{name: sha256} at the SOURCE (``--verify-sha``; remote uses shasum -a 256)."""
    if source["kind"] == "remote":
        out = _run(["ssh", source["host"], REMOTE_SHAS.format(path=shlex.quote(source["path"]))],
                   "remote checksums")
        shas = {}
        # split on "\n" only (splitlines also splits a name at U+2028 or \r), and
        # read shasum's escaped form: a name holding a backslash or a newline is
        # printed as "\<digest>  <name with \\ and \n>"
        for line in out.split("\n"):
            escaped = line.startswith("\\")
            digest, _, name = (line[1:] if escaped else line).partition(" ")
            name = name[1:] if name.startswith((" ", "*")) else name
            if escaped:
                name = name.replace("\\\\", "\0").replace("\\n", "\n").replace("\0", "\\")
            name = _strip_dot(name)
            if len(digest) == 64 and name and name != "-":
                shas[name] = digest
        return shas
    directory = Path(source["path"])
    return {finding_name(p, directory): hashing.sha256_file(p)
            for p in iter_finding_files(directory)}


def transfer(source: dict, dest: Path) -> None:
    """Copy the findings to *dest*; rsync for remote, a plain copy for local."""
    dest.mkdir(parents=True, exist_ok=True)
    if source["kind"] == "remote":
        # --no-links: the expected set is `find -type f`, so a link would land
        # as an extra that the readers then follow
        argv = ["rsync", "-a", "--no-links", "--prune-empty-dirs", "--include=*.json",
                "--include=*/", "--exclude=*", f"{source['host']}:{source['path']}/",
                str(dest) + "/"]
        _run(argv, "rsync")
        return
    root = Path(source["path"])
    for path in iter_finding_files(root):
        target = dest / finding_name(path, root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def source_audit(dest: Path, expected: Mapping[str, str]) -> dict:
    """Which harvested findings were judged against which source (was ``edition_audit``).

    The source gate belongs to the run's gate, but a corpus can also arrive from
    an older run: batch-1 findings were written against the 2011 draft, and their
    page anchors are wrong for the published edition.

    ``expected`` is ``{source_id: sha256}`` for the sources the run reads.  Three
    ways a file can fail, and they are reported separately because they need
    different fixes:

    * it names a ``source_id`` the run does not carry -- it belongs to another
      run entirely (``unknown_source``);
    * it names a known source with the WRONG sha -- another edition of the same
      work (``mismatched``);
    * it names no source at all -- that is what v1 findings look like before
      ``convert_v1.py`` stamps them (``no_source``, recorded, not fatal).

    A mismatch or an unknown source **fails the receipt** (SKILL.md hard rule 3:
    "a mismatch is refused, not warned about") unless the caller passes
    ``--allow-edition-mismatch``; an import of an older run says which edition it
    was judged against with ``--source-sha`` instead.
    """
    expected = {str(k): str(v) for k, v in dict(expected or {}).items() if v}
    if not expected:
        return {"sources": {}, "checked": False}
    matched, missing, mismatched, unknown = 0, [], [], []
    seen: dict[str, int] = {}
    for path in iter_finding_files(dest):
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            continue  # not silence: malformed_files() puts it on the receipt
        meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
        sid = str(payload.get("source_id") or meta.get("source_id") or "")
        sha = payload.get("source_sha") or meta.get("source_sha") \
            or payload.get("book_sha") or meta.get("book_sha")
        name = finding_name(path, dest)
        if not sha and not sid:
            missing.append(name)
            continue
        if sid and sid not in expected:
            unknown.append(name)
            continue
        want = expected.get(sid) if sid else None
        if want is None:
            # No source_id but a sha: accept it against ANY of the run's sources,
            # which is the most a v1-shaped file can be checked for.
            if str(sha) in set(expected.values()):
                matched += 1
                continue
            mismatched.append(name)
            continue
        if str(sha) != want:
            mismatched.append(name)
            continue
        matched += 1
        seen[sid] = seen.get(sid, 0) + 1
    return {"sources": expected, "checked": True, "matched": matched,
            "by_source": dict(sorted(seen.items())),
            "no_source": missing, "mismatched": mismatched, "unknown_source": unknown}


def malformed_files(dest: Path) -> list[str]:
    """Findings files in *dest* that cannot be read as a findings document.

    A file that is valid JSON but not an object (``null``, a bare list) was
    dropped without a word by both readers of the corpus, so the corpus was
    quietly a file short -- the failure the whole receipt exists to prevent, in
    the same "cleaner" direction.  The gate calls such a file an error; so does
    the receipt, and ``complete`` is false while one is there.
    """
    bad: list[str] = []
    for path in iter_finding_files(dest):
        try:
            payload = read_json(path, default=None)
        except CfError:  # invalid JSON: read_json already refuses it
            bad.append(finding_name(path, dest))
            continue
        if not isinstance(payload, dict):
            bad.append(finding_name(path, dest))
    return sorted(bad)


#: the ``schema`` of every locate document
LOCATE_SCHEMA = vocab.LOCATE_DOCUMENT_SCHEMA


def locate_counts(dest: Path) -> dict | None:
    """The locate documents in *dest*, counted by mode, or None when there are none.

    UNRESOLVED is counted on its own and never as "not formalized" (the
    proposal's section 2.6), and the documents the bridge wrote at the attempt
    cap (``unresolved.by: attempt_cap``) are counted apart from the workers' own:
    a shard whose receipt is mostly attempt-capped is a shard to re-plan, not a
    shard that found nothing (docs/proposals/wikipedia-locate.md section 6).
    """
    out = {"documents": 0, "by_mode": {}, "unresolved_documents": 0,
           "attempt_capped_documents": 0, "passages": 0, "unresolved_passages": 0,
           "verdicts": 0, "tasks": 0}
    tasks: set[str] = set()
    for path in iter_finding_files(dest):
        try:
            payload = read_json(path, default=None)
        except CfError:
            continue
        if not isinstance(payload, dict) or payload.get("schema") != LOCATE_SCHEMA:
            continue
        mode = str(payload.get("mode") or "?")
        out["documents"] += 1
        out["by_mode"][mode] = out["by_mode"].get(mode, 0) + 1
        tasks.add(str(payload.get("task_id") or Path(path).parent.name))
        block = payload.get("unresolved")
        if isinstance(block, dict):
            out["unresolved_documents"] += 1
            if block.get("by") == vocab.UNRESOLVED_ATTEMPT_CAP:
                out["attempt_capped_documents"] += 1
        passages = payload.get("passages") if isinstance(payload.get("passages"), list) else []
        out["passages"] += len(passages)
        out["unresolved_passages"] += sum(1 for p in passages if isinstance(p, dict)
                                          and p.get("outcome") == "unresolved")
        if isinstance(payload.get("verdicts"), list):
            out["verdicts"] += len(payload["verdicts"])
    if not out["documents"]:
        return None
    out["tasks"] = len(tasks)
    out["by_mode"] = dict(sorted(out["by_mode"].items()))
    return out


def build_receipt(*, run_id: str, source: dict, expected: Sequence[str], dest: Path,
                  sources: Mapping[str, str] | None = None, previous: dict | None = None,
                  source_manifest: dict[str, str] | None = None,
                  run_yaml: str | None = None, allow_edition_mismatch: bool = False,
                  gate: dict | None = None, cost: dict | None = None,
                  host_artifacts: dict | None = None, audit: dict | None = None) -> dict:
    """Reconcile the destination against the expected set.

    ``expected`` is the REMOTE listing, never a number someone remembered.
    ``sources`` is ``{source_id: sha256}`` for the sources this run reads, and
    it is recorded on the receipt so a later reader can tell which editions the
    corpus was judged against without re-reading 2,672 files.  The receipt is
    complete only when

    * nothing is missing,
    * every file matches the remote byte for byte (when checksums were collected),
    * every file that landed can be read as a findings document,
    * no finding was judged against another edition or an unknown source (unless
      ``allow_edition_mismatch``), and
    * the schema/quote gate passed, when one was run (``gate``).

    ``cost`` is the block ``cost.fleet.cost_block`` computed from the agents'
    transcripts (issue #11), or None when the harvest was given none; it never
    affects ``complete``, because a run's findings are whole or not regardless
    of whether anyone priced them.  ``host_artifacts`` is what
    ``harvest.host_artifacts.fetch`` brought home beside the corpus (a locate
    run's gate log, usage export and canary scan); an EXPECTED one that did
    not arrive (the gate log) makes the receipt incomplete and is named in
    ``host_artifacts_missing``, the others never affect ``complete``.
    ``audit`` is an audited inventory run's block (``harvest.audit_summary.
    summarise``, references/locate-documents.md section 13.10), recorded as given
    and only when given; it never affects ``complete``.
    """
    actual_paths = iter_finding_files(dest)
    actual = sorted(finding_name(p, dest) for p in actual_paths)
    expected_set, actual_set = set(expected), set(actual)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    manifest = hashing.manifest(actual_paths, root=dest)
    mismatched = sorted(
        name for name, sha in (source_manifest or {}).items()
        if name in manifest and manifest[name] != sha
    )
    # Checksums asked for and a landed file with none from the source: it was
    # never compared, so the receipt cannot say it was (fail closed).
    sha_unverified = (sorted(set(manifest) - set(source_manifest or {}))
                      if source_manifest is not None else [])
    malformed = malformed_files(dest)
    sources = {str(k): str(v) for k, v in dict(sources or {}).items()}
    edition = source_audit(dest, sources)
    edition_mismatch = list(edition.get("mismatched") or []) + list(
        edition.get("unknown_source") or [])
    edition["allowed_mismatch"] = bool(allow_edition_mismatch)
    edition["ok"] = not edition_mismatch or bool(allow_edition_mismatch)
    gate_ok = True if gate is None else bool(gate.get("ok"))
    from harvest import host_artifacts as host_mod   # local, as describe() imports it

    artifacts_missing = host_mod.missing(host_artifacts) if host_artifacts is not None else []
    receipt = {
        "run_id": run_id,
        "harvested_at": iso_now(),
        "sources": sources,
        "source": source,
        "destination": str(dest),
        "expected": {"count": len(expected_set), "names": sorted(expected_set)},
        "actual": {"count": len(actual_set)},
        "missing": missing,
        "extra": extra,
        "sha_mismatched": mismatched,
        "sha_verified": bool(source_manifest) and not sha_unverified,
        "sha_unverified": sha_unverified,
        "malformed": malformed,
        "complete": (not missing and not mismatched and not sha_unverified and not malformed
                     and edition["ok"] and gate_ok and not artifacts_missing),
        "manifest_algo": "sha256",
        "manifest": manifest,
        "run_yaml": run_yaml or "",
        "run_yaml_sha": (hashing.sha256_file(Path(dest) / run_yaml) if run_yaml
                         and (Path(dest) / run_yaml).exists() else ""),
        "edition": edition,
        "by_source": edition.get("by_source") or {},
        "gate": gate,
        "cost": cost,
    }
    locate = locate_counts(dest)
    if locate:
        receipt["locate"] = locate
    if host_artifacts is not None:
        receipt["host_artifacts"] = host_artifacts
        receipt["host_artifacts_missing"] = artifacts_missing
    if audit is not None:
        receipt["audit"] = audit
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


def ungated_runs(runs: Mapping[str, str | Path]) -> list[str]:
    """Which of *runs* landed with no schema/quote gate behind them.

    ``RECEIPT.json``'s ``gate`` block is the only record that the findings were
    validated against the source text, and nothing downstream used to read it:
    a corpus harvested without a gate fed the tally, the adjudication and the
    report with quotes never checked against ``text.txt``, and every number
    looked the same as a gated one.  A run with no receipt at all counts as
    ungated -- it is a directory nobody receipted.
    """
    out = []
    for run_id, directory in dict(runs or {}).items():
        receipt = read_receipt(Path(directory))
        if not isinstance(receipt, dict) or not (receipt.get("gate") or {}):
            out.append(str(run_id))
    return sorted(out)


def gate_rejected(receipt: Mapping | None) -> bool:
    """Did a gate RUN over this receipt's corpus and REJECT it?

    :func:`ungated_runs` answers "was a gate run at all"; this answers "did it
    reject".  The two are different states and only one of them was ever read:
    a run whose gate found a fabricated quote still leaves its rows in the
    corpus, and every reader that tested the gate BLOCK rather than its ``ok``
    counted those rows as if the gate had passed them.  One definition, because
    the tally and the report disagreeing about it is what made a rejected run
    invisible to `cf tally` while `cf report` named it.
    """
    gate = receipt.get("gate") if isinstance(receipt, dict) else None
    return bool(isinstance(gate, dict) and gate and not gate.get("ok"))


def gate_failed_runs(runs: Mapping[str, str | Path]) -> list[str]:
    """Which of *runs* carry a RECEIPT.json whose gate did not pass.

    A rejected run's rows are still in the corpus and are still counted, so the
    count has to say so.
    """
    return sorted(str(run_id) for run_id, directory in dict(runs or {}).items()
                  if gate_rejected(read_receipt(Path(directory))))


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
    if receipt.get("sha_unverified"):
        lines.append(f"  SHA UNVERIFIED {len(receipt['sha_unverified'])}: "
                     f"{', '.join(receipt['sha_unverified'][:MAX_NAMED])} -- the source "
                     f"listed no checksum for them, so they were never compared")
    if receipt.get("malformed"):
        lines.append(f"  MALFORMED {len(receipt['malformed'])}: "
                     f"{', '.join(receipt['malformed'][:MAX_NAMED])} -- valid JSON, but not a "
                     f"findings document, so nothing in them is counted")
    edition = receipt.get("edition") or {}
    if edition.get("checked"):
        for sid, n in sorted((edition.get("by_source") or {}).items()):
            lines.append(f"  source {sid}: {n} finding(s)")
        if edition.get("mismatched"):
            verdict = "allowed" if edition.get("allowed_mismatch") else "REFUSED"
            lines.append(f"  EDITION: {len(edition['mismatched'])} findings carry a different "
                         f"source_sha -- they were judged against another edition ({verdict};"
                         " --allow-edition-mismatch, or import with --source-sha)")
        if edition.get("unknown_source"):
            verdict = "allowed" if edition.get("allowed_mismatch") else "REFUSED"
            lines.append(f"  SOURCE: {len(edition['unknown_source'])} findings name a source this "
                         f"run does not carry ({verdict}; they belong to another run)")
        if edition.get("no_source"):
            lines.append(f"  source: {len(edition['no_source'])} findings carry no source_id "
                         f"(v1 findings; convert_v1.py stamps them)")
    locate = receipt.get("locate")
    if isinstance(locate, dict):
        modes = ", ".join(f"{m} {n}" for m, n in sorted((locate.get("by_mode") or {}).items()))
        lines.append(f"  locate: {locate.get('documents')} document(s) over {locate.get('tasks')} "
                     f"task(s) ({modes}); UNRESOLVED {locate.get('unresolved_documents')} "
                     f"document(s), {locate.get('attempt_capped_documents')} at the attempt cap, "
                     f"{locate.get('unresolved_passages')} of {locate.get('passages')} passage(s) "
                     f"-- counted on their own, never as not formalized")
    gate = receipt.get("gate")
    if gate:
        lines.append(f"  gate: {'PASSED' if gate.get('ok') else 'FAILED'} "
                     f"({gate.get('preset') or gate.get('config') or 'schema+quote'})")
        for problem in (gate.get("problems") or [])[:MAX_NAMED]:
            lines.append(f"    - {problem}")
    cost = receipt.get("cost")
    if isinstance(cost, dict):
        from cost import fleet as cost_fleet  # local: cost.fleet reads receipts through this module

        lines.extend(cost_fleet.describe(cost))
    if isinstance(receipt.get("host_artifacts"), dict):
        from harvest import host_artifacts as host_mod

        lines.extend(host_mod.describe(receipt["host_artifacts"]))
    if isinstance(receipt.get("audit"), dict):
        from harvest import audit_summary

        lines.extend(audit_summary.describe(receipt["audit"]))
    prev = receipt.get("previous")
    if prev:
        lines.append(f"  previous harvest: {prev.get('actual')} files at {prev.get('harvested_at')}"
                     f" (+{len(prev.get('gained') or [])} / -{len(prev.get('lost') or [])})")
    # The corpus is the calibration baseline, so it wants to be committed -- but the
    # workspace repo is the operator's (it holds source quotes and has no remote), so the
    # command never commits on their behalf.
    lines.append(f"  commit it yourself:  git -C {Path(receipt['destination']).parents[1]} add "
                 f"corpus/{receipt['run_id']} && git -C {Path(receipt['destination']).parents[1]} "
                 f"commit -m 'corpus: {receipt['run_id']}'")
    return "\n".join(lines)


def harvest(*, run_id: str, source: dict, dest: Path, sources: Mapping[str, str] | None = None,
            run_yaml_src: Path | None = None, verify_sha: bool = False,
            execute: bool = True, allow_edition_mismatch: bool = False,
            gate_runner=None, cost: dict | None = None,
            host_artifacts: dict | None = None, audit_runner=None) -> dict:
    """Transfer, reconcile and write RECEIPT.json. Returns the receipt.

    ``execute=False`` only makes sense for a remote source: it prints the
    commands and returns a receipt marked ``planned``.  ``cost`` is the priced
    block of the run's transcripts, recorded on the receipt as given.
    ``audit_runner``, an audited run's, is called on the landed corpus like
    ``gate_runner`` and its block recorded as the receipt's ``audit``.
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

    gate = gate_runner(dest) if gate_runner else None
    audit = audit_runner(dest) if audit_runner else None
    receipt = build_receipt(run_id=run_id, source=source, expected=expected, dest=dest,
                            sources=sources, previous=previous, source_manifest=shas,
                            run_yaml=run_yaml_name,
                            allow_edition_mismatch=allow_edition_mismatch, gate=gate,
                            cost=cost, host_artifacts=host_artifacts, audit=audit)
    write_json(dest / RECEIPT_NAME, receipt, indent=1, sort_keys=True)
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    parser.add_argument("--from", dest="source", required=True,
                        help="host:/path/to/findings or a local directory")
    parser.add_argument("--dest", required=True, help="corpus/<run-id> directory")
    # its own dest: `--from` already owns `source`, and argparse would make the
    # two one option
    parser.add_argument("--source", dest="source_specs", action="append", default=[],
                        metavar="ID=SHA256",
                        help="a source this run reads (repeatable); the receipt records them "
                             "and a finding stamped with any other is refused")
    parser.add_argument("--run-yaml", default=None)
    parser.add_argument("--verify-sha", action="store_true")
    parser.add_argument("--execute", action="store_true", help="required for a remote source")
    args = parser.parse_args(argv)
    source = parse_source(args.source)
    if source["kind"] == "remote" and not args.execute:
        for step in list_commands(source, Path(args.dest)):
            print(f"# {step['title']}\n{step['shell']}")
        return 0
    sources = {}
    for spec in args.source_specs:
        sid, _, sha = str(spec).partition("=")
        if not (sid and sha):
            parser.error(f"--source {spec!r} is not ID=SHA256")
        sources[sid] = sha
    receipt = harvest(run_id=args.run_id, source=source, dest=Path(args.dest),
                      sources=sources,
                      run_yaml_src=Path(args.run_yaml) if args.run_yaml else None,
                      verify_sha=args.verify_sha, execute=True)
    print(describe(receipt))
    return 0 if receipt["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
