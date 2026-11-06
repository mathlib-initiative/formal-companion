"""`cf informalize apply <id> --run <run>`: a harvested phase's files into the repository.

New code, for docs/proposals/informalize.md section 8.5 (component 5b of its section 11).
`cf harvest` brings an informalize phase's own files home under `corpus/<run>/tree/` and
says which of them the run WROTE against the base commit's bytes; this puts those back
into the output repository, commits them, and writes the phase's stage receipt -- the
file `cf plan-informalize` and `cf informalize status` read to know the phase is done
(a draft phase planned after a by-hand copy was refused for want of `stages/I4.json`).

Every refusal comes before anything is written:

* the run is an informalize run of this id, and it is harvested (`corpus/<run>/RECEIPT.json`);
* that harvest is complete, and its `informalize` block names no `unowned` path -- a path
  the entrypoint changed that no task owns is a gate escape or an edit on the box, and
  nothing of that run goes into the repository;
* `tree/` is what the receipt records: every path it lists is there with its sha256, and
  no file beside them;
* every path the run wrote is under `informal/`; `findings/` and the task files stay in
  `corpus/<run>/`;
* the repository's HEAD is the run's `base_commit`, else the phase must be planned again
  (its contributions were written against text that has moved), and none of the paths
  being written has an uncommitted change of the maintainer's -- both only for a run that
  writes a file: a phase that writes none has nothing a moved HEAD could invalidate, and
  refusing it would leave a receipt nobody can ever write.

A run whose files are already in HEAD is re-applied as a no-op, so the command may be
re-run (CLAUDE.md section 4, idempotence).  An attack run writes nothing under
`informal/`: its apply writes only the receipt and makes no commit.  After the items
phase it runs `check-items`, and records what it found.

The receipt is `stages/I3.json` (items), `stages/I4.json` (conventions) and
`stages/I5-<phase>-<run>.json` (the writing phases), section 3's table.  Its `runs` are
the entries `pipeline.stage_receipt` writes for a fleet (`run_id`, `run_yaml_sha256`,
`receipt_sha256`), each with the dollars that run cost, and `cost_usd` their sum, which
refuses a fleet receipt with no cost block -- `--unpriced` is the deliberate exception,
recorded in the receipt's `checks`.  Its paths are relative to `informalize/<id>/`
(section 5.7), so the harvest receipt it read, which is not under it, is named from
there: `../../corpus/<run>/RECEIPT.json`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cflib.errors import CfError
from cflib.hashing import sha256_file
from cflib.jsonio import read_json
from cflib.paths import WorkspacePaths
from cflib.yamlio import read_yaml
from harvest import informal_files
from harvest.harvest import RECEIPT_NAME
from pipeline.stage_receipt import fleet_run_entry, sum_fleets

from informalize import approve, config, plan, stages
from informalize.outrepo import OutputRepo

#: the stage a phase's receipt belongs to (section 3's table); the writing phases get one
#: receipt per run, `I5-<phase>-<run>`, since `draft`, `attack`, `judge` and `edit` all run
#: more than once
PHASE_STAGE = {"items": "I3", "conventions": "I4"}
#: the one phase whose fleet changes no file of the output repository (section 8.2)
NO_FILES_PHASE = "attack"
#: what a run may put back: the output repository's text, never a finding or a task file
WRITABLE_PREFIX = "informal/"


def stage_of(phase: str, run_id: str) -> str:
    """The receipt this phase's apply writes: `I3`, `I4`, or `I5-<phase>-<run>`."""
    return PHASE_STAGE.get(phase) or f"I5-{phase}-{run_id}"


@dataclass
class Applied:
    """What one apply did, for the command to print."""

    run_id: str
    phase: str
    stage: str
    files: list[str] = field(default_factory=list)
    commit: str | None = None
    receipt: Path | None = None
    cost_usd: float = 0.0
    already: bool = False
    check_items: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# what has been applied
# --------------------------------------------------------------------------- #

def receipt_runs(receipt: dict | None) -> list[dict]:
    """The `{run_id, ...}` entries of a stage receipt's `runs`, oldest first."""
    out = []
    for entry in (receipt or {}).get("runs") or []:
        if isinstance(entry, dict) and entry.get("run_id"):
            out.append(entry)
    return out


def applied_runs(paths: WorkspacePaths, informalize_id: str) -> dict[str, str]:
    """`{run_id: stage}` over every stage receipt of this id that records a run."""
    out: dict[str, str] = {}
    directory = paths.informalize_stages_dir(informalize_id)
    for path in sorted(directory.glob("I*.json")) if directory.is_dir() else []:
        doc = read_json(path)
        if not isinstance(doc, dict):
            continue
        for entry in receipt_runs(doc):
            out[str(entry["run_id"])] = str(doc.get("stage") or path.stem)
    return out


def harvested_runs(paths: WorkspacePaths, informalize_id: str) -> list[str]:
    """Every harvested informalize run of this id, oldest plan first."""
    runs = paths.runs_dir
    found: list[tuple[str, str]] = []
    for path in sorted(runs.glob("*/run.yaml")) if runs.is_dir() else []:
        doc = read_yaml(path)
        block = doc.get("informalize") if isinstance(doc, dict) else None
        if not isinstance(block, dict) or block.get("id") != informalize_id:
            continue
        run_id = path.parent.name
        if paths.receipt_json(run_id).is_file():
            found.append((str(doc.get("created") or ""), run_id))
    return [run_id for _, run_id in sorted(found)]


def unapplied(paths: WorkspacePaths, informalize_id: str) -> list[str]:
    """The harvested runs of this id whose files are not in a stage receipt yet."""
    done = applied_runs(paths, informalize_id)
    return [run_id for run_id in harvested_runs(paths, informalize_id) if run_id not in done]


# --------------------------------------------------------------------------- #
# the checks
# --------------------------------------------------------------------------- #

def _block(paths: WorkspacePaths, run_id: str) -> tuple[dict, dict]:
    """The harvest receipt and its `informalize` block, or a refusal."""
    receipt = read_json(paths.receipt_json(run_id))
    if not isinstance(receipt, dict):
        raise CfError(f"{paths.receipt_json(run_id)} is not a harvest receipt",
                      hint=f"cf harvest {run_id} --execute --i-have-consent writes it")
    block = receipt.get("informalize")
    if not isinstance(block, dict):
        raise CfError(f"the harvest of {run_id} recorded no informalize block",
                      hint=f"harvest it again: cf harvest {run_id} --execute --i-have-consent")
    return receipt, block


def check_harvest(run_id: str, receipt: dict, block: dict) -> None:
    """Refuse a harvest that is not complete, or that saw a path no task owns."""
    unowned = list(block.get("unowned") or [])
    if unowned:
        raise CfError(f"the harvest of {run_id} found {len(unowned)} path(s) the entrypoint "
                      f"changed that no task owns: {', '.join(unowned[:8])}",
                      hint="something other than a task wrote in the entrypoint (a gate "
                           "escape, an edit on the box, a merge that skipped the pre-merge "
                           "check); nothing of this run is applied until that is understood")
    if not receipt.get("complete") or not block.get("ok", True):
        raise CfError(f"the harvest of {run_id} is not complete, so what came home is not "
                      f"what the run wrote",
                      hint=f"fix the transfer and run `cf harvest {run_id}` again")


def check_tree(run_id: str, tree: Path, block: dict) -> list[str]:
    """`tree/` against what the receipt records; the paths the run wrote, sorted.

    Every recorded path is there with its recorded sha256, nothing else is, and each
    written path is under `informal/`: `findings/` and the task files stay in the corpus.
    """
    recorded = block.get("paths") if isinstance(block.get("paths"), dict) else {}
    for rel, row in sorted(recorded.items()):
        path = tree / rel
        want = str((row or {}).get("sha256") or "")
        if path.is_symlink() or not path.is_file():
            raise CfError(f"{tree}/{rel} is not there, and the harvest of {run_id} recorded it",
                          hint=f"harvest it again: cf harvest {run_id}")
        if sha256_file(path) != want:
            raise CfError(f"{tree}/{rel} is not the file the harvest of {run_id} recorded",
                          hint=f"corpus/{run_id}/tree/ was edited since the harvest; "
                               f"run `cf harvest {run_id}` again")
    if tree.is_dir():
        here = {p.relative_to(tree).as_posix() for p in tree.rglob("*")
                if p.is_file() or p.is_symlink()}
        extra = sorted(here - set(recorded))
        if extra:
            raise CfError(f"{tree} holds {len(extra)} file(s) the harvest of {run_id} does not "
                          f"record: {', '.join(extra[:8])}",
                          hint="the corpus is the harvest's own record of what came home; "
                               f"remove what does not belong there, or harvest {run_id} again")
    written = sorted(rel for rel, row in recorded.items() if (row or {}).get("changed"))
    outside = [rel for rel in written if not rel.startswith(WRITABLE_PREFIX)]
    if outside:
        raise CfError(f"run {run_id} wrote {len(outside)} path(s) outside informal/: "
                      f"{', '.join(outside[:8])}",
                      hint="apply copies in the changed paths under informal/ and only "
                           "those; findings/ and the task files stay in the corpus")
    return written


def check_head(repo: OutputRepo, run_id: str, phase: str, base_commit: str,
               files: list[str]) -> None:
    """Refuse a repository that has moved since the run was planned, or a dirty target."""
    head = repo.head()
    if head != base_commit:
        raise CfError(f"the output repository is at {(head or 'no commit')[:12]}, not the base "
                      f"commit {base_commit[:12]} run {run_id} was planned from",
                      hint=f"its contributions were written against text that has moved: plan "
                           f"the phase again, `cf plan-informalize {repo.id} --phase {phase}`")
    dirty = repo.changes(files) if files else []
    if dirty:
        raise CfError(f"{len(dirty)} path(s) this apply writes have uncommitted changes: "
                      f"{', '.join(sorted(dirty)[:8])}",
                      hint="commit or remove them first; apply never overwrites an edit of "
                           "yours without a record of it")


def already_applied(repo: OutputRepo, tree: Path, written: list[str],
                    stage_receipt: dict | None) -> bool:
    """Is this run's text already committed, byte for byte, with its receipt written?

    An apply may be re-run (CLAUDE.md section 4): the second one finds HEAD past the base
    commit, which is exactly what "plan again" refuses, so it is answered here first.
    """
    if stage_receipt is None:
        return False
    for rel in written:
        if repo.read_bytes(rel) != (tree / rel).read_bytes() or not repo.committed(rel):
            return False
    return True


# --------------------------------------------------------------------------- #
# applying
# --------------------------------------------------------------------------- #

def _cost(paths: WorkspacePaths, run_id: str, *, unpriced: bool) -> dict:
    """This run's receipt entry: `{run_id, run_yaml_sha256, receipt_sha256, cost_usd}`.

    The dollars are `pipeline.stage_receipt.sum_fleets`', which refuses a fleet receipt
    with no cost block or an unpriced model: a phase receipt whose dollars quietly left a
    fleet out is the cost line no report should carry (section 5.7).  `--unpriced` is the
    deliberate exception, and the receipt records that it was taken.
    """
    if unpriced:
        return {**fleet_run_entry(paths, run_id), "cost_usd": None}
    summed = sum_fleets(paths, [run_id])
    return {**summed["fleet_runs"][0], "cost_usd": summed["cost_usd"]}


def _runs(previous: dict | None, entry: dict) -> tuple[list[dict], float]:
    """This run's entry beside the ones the stage receipt already holds, and their sum."""
    rows = [row for row in receipt_runs(previous) if row.get("run_id") != entry["run_id"]]
    rows.append(entry)
    total = sum(float(row.get("cost_usd") or 0.0) for row in rows)
    return rows, round(total, 6)


def apply_run(workspace, informalize_id: str, run_id: str, *, unpriced: bool = False) -> Applied:
    """Put a harvested phase's files into the output repository and write its receipt."""
    paths = workspace.paths
    config.load(paths, informalize_id, sessions=config.session_limit(workspace))
    repo = OutputRepo(paths, informalize_id)
    repo.require_stamp()
    block = plan.harvested_run(paths, run_id, informalize_id, flag="--run")
    phase = str(block.get("phase") or "")
    base_commit = str(block.get("base_commit") or "")
    receipt, informal = _block(paths, run_id)
    check_harvest(run_id, receipt, informal)
    tree = Path(paths.corpus_run_dir(run_id)) / informal_files.TREE
    written = check_tree(run_id, tree, informal)
    if phase == NO_FILES_PHASE and written:
        raise CfError(f"the {phase} run {run_id} wrote {len(written)} file(s) of informal/: "
                      f"{', '.join(written[:8])}",
                      hint="an attack task owns its findings document alone (section 8.2); "
                           "its apply writes only the receipt")
    stage = stage_of(phase, run_id)
    previous = stages.read_receipt(paths, informalize_id, stage)
    result = Applied(run_id=run_id, phase=phase, stage=stage, files=written)
    if already_applied(repo, tree, written, previous):
        result.already = True
        result.commit = repo.head()
        return result
    if written:
        # A phase that wrote nothing (an attack run) puts no text into the repository, so
        # a HEAD past the base commit invalidates nothing of it; refusing it would wedge
        # the receipt -- and `cf informalize status`, which names the apply of every
        # unapplied run -- behind a re-plan that would throw the findings away.
        check_head(repo, run_id, phase, base_commit, written)
    entry = _cost(paths, run_id, unpriced=unpriced)

    # ---- every refusal is above this line: now write ----------------------------------
    for rel in written:
        repo.write(rel, (tree / rel).read_bytes())
    if written:
        # `commit` returns None when none of the paths differed from HEAD, which is a
        # phase that wrote the bytes already there: the receipt still names a commit
        result.commit = repo.commit(written, f"Informalize {phase} ({run_id}): "
                                             f"{len(written)} files") or repo.head()
    else:
        result.commit = repo.head()
    if phase == "items":
        try:
            result.check_items, _ = approve.check_items(workspace, informalize_id)
        except CfError as exc:                 # the table is in: say why it cannot be checked
            result.check_items = [exc.message]
    rows, total = _runs(previous, entry)
    result.cost_usd = total
    checks = {"phase": phase, "base_commit": base_commit, "commit": result.commit,
              "files": len(written), "harvest_complete": True,
              "cost_priced": not unpriced}
    if phase == "items":
        checks["check_items"] = "ok" if not result.check_items else len(result.check_items)
    result.receipt = stages.write_receipt(
        paths, informalize_id, stage,
        inputs={f"../../corpus/{run_id}/{RECEIPT_NAME}": sha256_file(paths.receipt_json(run_id))},
        outputs={f"repo/{rel}": sha256_file(repo.root / rel) for rel in written},
        checks=checks, runs=rows, cost_usd=total)
    return result
