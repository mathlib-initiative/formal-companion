"""Bring an informalize phase's own files home beside its findings.

New code, for docs/proposals/informalize.md section 8.5 (component 5b of its
section 11).  The findings rsync carries ``*.json`` documents out of the
entrypoint's ``findings/`` and nothing else, and most of what an informalize
phase writes is not a document there: the item table, the conventions files and
the section ``.tex`` files are paths of the output repository that the fleet
merged into the entrypoint's ``main``.  Without them a harvested phase holds the
attackers' and the judges' documents and not a line of the text they are about.

So the harvest reads the run's own record of what each task owns (``run.yaml``
``informalize.paths``, what the planner wrote and the gate enforces), asks the
host for the sha256 of each of those paths that is there, copies them into
``corpus/<run>/tree/<path>``, and compares the two digests -- the reconciliation
the findings get, for the files: a path the host listed and that did not land is
MISSING, and one that landed with another sha256 was changed in flight.  The
receipt carries the block, and is not complete while either list holds a name.

``findings/`` paths are left out: they are the findings, and they land where the
findings land.  A path the host does not have is recorded as absent, never an
error -- a task may own a file it did not change only in a phase whose base
commit has none (``items-review.md`` before the items task writes it).

``base_files`` is the gate's record of the base commit's bytes
(``gate.json``'s), so the block can say which of the landed files the run
actually CHANGED: that list is what a later `cf informalize apply` copies into
the output repository.  A landed path those pins do not name -- one the base
commit does not hold, and ``items-review.md``, which they never name -- counts
as written by the run rather than as unchanged, so nothing the fleet wrote is
left behind by a file the harvest could not tell apart.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from cflib import hashing
from cflib.errors import CfError

#: where the files land under ``corpus/<run>/``.  A directory, so the receipt's
#: findings listing (``harvest/files.py``) never counts ``informal/items.json``
#: as a finding; it is left out there by name.
TREE = "tree"
#: the phases whose tasks write a document under ``findings/`` (section 8.2);
#: the others' only outputs are files of ``informal/``
FINDINGS_PHASES = ("attack", "judge", "edit")
#: ask the host for the sha256 of each named path that is there, in one call: a
#: path that is absent prints nothing and is no error (the `if` keeps the loop's
#: exit status 0), and the names are the plan's own, quoted for the host's shell
REMOTE_SHAS = 'cd {path} && for f in {names}; do if test -f "$f"; then shasum -a 256 "$f"; fi; done'


def block_of(run: Mapping | None) -> dict:
    """``run.yaml``'s ``informalize:`` block, or ``{}``."""
    block = (run or {}).get("informalize")
    return block if isinstance(block, dict) else {}


def phase_of(run: Mapping | None) -> str:
    return str(block_of(run).get("phase") or "")


def writes_findings(run: Mapping | None) -> bool:
    """Does this phase's fleet write documents under ``findings/`` at all?"""
    return phase_of(run) in FINDINGS_PHASES


def owned_paths(run: Mapping | None) -> list[str]:
    """Every path of the output repository this run's tasks may change, sorted.

    The planner's record (``informalize.paths``, task -> its owned paths), which
    is the same list the gate holds each contribution to, so the harvest brings
    home what the run was allowed to write and nothing else.  A path that is not
    a plain relative one inside the entrypoint is left out rather than fetched:
    the list is the plan's own, and nothing that reads a file from a host should
    take ``..`` from a document.
    """
    out: set[str] = set()
    for paths in (block_of(run).get("paths") or {}).values():
        for path in paths or []:
            rel = str(path)
            parts = rel.split("/")
            if not rel or rel.startswith("findings/") or "\\" in rel:
                continue
            if any(part in ("", ".", "..") for part in parts):
                continue
            out.add(rel)
    return sorted(out)


def entrypoint_of(source: Mapping, run: Mapping | None) -> str:
    """The entrypoint directory beside the findings directory *source* names.

    A phase that writes findings is harvested from ``<entrypoint>/findings``, so
    the entrypoint is its parent; one that writes none has no findings directory
    on the host at all and is harvested from the entrypoint itself.
    """
    path = str(source.get("path") or "").rstrip("/")
    if writes_findings(run) or path.rsplit("/", 1)[-1] == "findings":
        return path.rsplit("/", 1)[0] if "/" in path else "."
    return path


def _rsync_argv(source: Mapping, entrypoint: str, rel: str, dest: Path) -> list[str]:
    """A plain copy of one file the host was asked about first (its sha256)."""
    remote = f"{entrypoint}/{rel}"
    if source.get("kind") == "remote":
        remote = f"{source['host']}:{remote}"
    return ["rsync", "-a", "--no-links", remote, str(dest)]


def list_commands(source: Mapping, entrypoint: str, paths: Sequence[str],
                  dest: Path) -> list[dict]:
    """The commands a remote fetch runs, for the dry run to print."""
    if source.get("kind") != "remote" or not paths:
        return []
    names = " ".join(shlex.quote(p) for p in paths)
    ls = ["ssh", source["host"], REMOTE_SHAS.format(path=shlex.quote(entrypoint), names=names)]
    out = [{"title": f"the sha256 of each of the {len(paths)} path(s) this run's tasks own "
                     f"(the expectation)", "argv": ls,
            "shell": " ".join(shlex.quote(a) for a in ls)}]
    for rel in paths:
        argv = _rsync_argv(source, entrypoint, rel, Path(dest) / rel)
        out.append({"title": f"the run's {rel}", "argv": argv,
                    "shell": " ".join(shlex.quote(a) for a in argv)})
    return out


def _remote_shas(source: Mapping, entrypoint: str, paths: Sequence[str]) -> dict[str, str]:
    """``{path: sha256}`` at the host, for the owned paths that are there.

    A line is read only when its name is one of the paths asked for, so a name
    ``shasum`` would print in its escaped form (one holding a backslash or a
    newline, which no planned path does) matches nothing and the path is
    reported absent rather than fetched under a name the host chose.
    """
    names = " ".join(shlex.quote(p) for p in paths)
    argv = ["ssh", source["host"], REMOTE_SHAS.format(path=shlex.quote(entrypoint), names=names)]
    proc = subprocess.run(argv, text=True, capture_output=True)
    if proc.returncode != 0:
        raise CfError(f"could not list the run's own files on the host "
                      f"({proc.returncode}): {proc.stderr.strip()[:300]}",
                      hint="command was: " + " ".join(shlex.quote(a) for a in argv))
    wanted, out = set(paths), {}
    for line in proc.stdout.split("\n"):
        digest, _, name = line.partition("  ")
        name = name.strip()
        if len(digest) == 64 and name in wanted:
            out[name] = digest
    return out


def _local_shas(entrypoint: str, paths: Sequence[str]) -> dict[str, str]:
    root = Path(entrypoint)
    return {rel: hashing.sha256_file(root / rel) for rel in paths
            if (root / rel).is_file() and not (root / rel).is_symlink()}


def fetch(source: Mapping, entrypoint: str, paths: Sequence[str], dest: Path, *,
          base_files: Mapping[str, str] | None = None) -> dict:
    """Copy the owned paths the entrypoint holds into *dest*, and reconcile.

    Returns the receipt's ``informalize`` block: what was expected (the host's
    own listing), what landed with its sha256 and bytes, which of those the run
    changed against ``base_files``, and the ``missing`` and ``sha_mismatched``
    names that make the harvest incomplete.
    """
    dest = Path(dest)
    paths = list(paths)
    block: dict = {"entrypoint": entrypoint, "tree": str(dest), "owned": paths,
                   "paths": {}, "expected": [], "absent": [], "missing": [],
                   "sha_mismatched": [], "changed": []}
    if not paths:
        block["ok"] = True
        return block
    expected = (_remote_shas(source, entrypoint, paths) if source.get("kind") == "remote"
                else _local_shas(entrypoint, paths))
    block["expected"] = sorted(expected)
    block["absent"] = [rel for rel in paths if rel not in expected]
    base = {str(k): str(v) for k, v in dict(base_files or {}).items()}
    for rel in sorted(expected):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.get("kind") == "remote":
            argv = _rsync_argv(source, entrypoint, rel, target)
            proc = subprocess.run(argv, text=True, capture_output=True)
            if proc.returncode != 0:
                raise CfError(f"could not copy {rel} from the host ({proc.returncode}): "
                              f"{proc.stderr.strip()[:300]}",
                              hint="command was: " + " ".join(shlex.quote(a) for a in argv))
        else:
            shutil.copy2(Path(entrypoint) / rel, target)
        if not target.is_file():
            block["missing"].append(rel)
            continue
        digest = hashing.sha256_file(target)
        row = {"sha256": digest, "bytes": target.stat().st_size}
        # A path the base commit's pins do not name is one the base commit does
        # not hold, or one the gate pins nothing for (``items-review.md``): it
        # counts as written by the run, which is the reading `apply` wants.
        row["changed"] = digest != base[rel] if rel in base else True
        if rel in base:
            row["base_sha256"] = base[rel]
        if row["changed"]:
            block["changed"].append(rel)
        block["paths"][rel] = row
        if digest != expected[rel]:
            block["sha_mismatched"].append(rel)
    block["ok"] = not block["missing"] and not block["sha_mismatched"]
    return block


def describe(block: Mapping | None) -> list[str]:
    """The harvest summary's lines for one of these blocks."""
    if not block:
        return []
    landed = len(block.get("paths") or {})
    changed = list(block.get("changed") or [])
    lines = [f"  informalize: {landed} of {len(block.get('owned') or [])} owned path(s) "
             f"landed in {block.get('tree')}, {len(changed)} written by the run"
             + (f" ({', '.join(changed[:8])})" if changed else "")]
    for key, what in (("missing", "MISSING (the host listed them and they did not land)"),
                      ("sha_mismatched", "SHA MISMATCH (not the bytes the host listed)")):
        names = list(block.get(key) or [])
        if names:
            lines.append(f"  informalize: {len(names)} {what}: {', '.join(names[:8])}")
    absent = list(block.get("absent") or [])
    if absent:
        lines.append(f"  informalize: {len(absent)} owned path(s) the entrypoint does not "
                     f"hold: {', '.join(absent[:8])}")
    return lines
