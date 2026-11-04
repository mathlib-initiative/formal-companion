"""Plan one informalize phase as a tend run: tasks, manifests, prompts, data, gate and tend root.

New code, for docs/proposals/informalize.md sections 8.1 and 8.2 (component 5a of its
section 11), on the pattern of the locate planner (`scripts/locate/plan.py`,
`scripts/locate/tend_root.py`): `cf plan-run` is scoped to a companion and a source, and a
phase is scoped to an informalize id and its output repository.

One phase is one run, planned from the output repository's HEAD (its `base_commit`) and,
for `judge` and `edit`, from the harvest of the run before it (`--from-run`):

* `items` -- one task, `items`: `informal/items.json` and `informal/items-review.md`;
* `conventions` -- one task, `conventions`: the four conventions files;
* `draft` -- `draft-NN` per section, its section file;
* `attack` -- `attack-lean-NN` and `attack-tex-NN` per section, each one document;
* `judge` -- `judge-NN` per section whose attack documents hold a finding: its section
  file and its ruling;
* `edit` -- one task, `edit`: the conventions files, the section files the judge run's
  requests name, its answers and a proposal for the item table.

`runs/<run-id>/` then holds `run.yaml` (its `informalize:` block: the id, phase, sections,
base commit, the run it reads, each task's owned paths, the library's commit and the
sha256 of its source), `repo/` (the output repository's files at the base commit, which
`cf run` makes the entrypoint's first commit beside `tasks/`), `tasks/`, `prompts/`,
`data/` (`scope.json`, and the attack or judge run's documents), `gate.json` and
`gate.local.json`, and `tend-root/`, whose `gate/` holds `verify_informal.py`,
`verify_findings.py`, `tex/` (the runtime of this checkout), `declarations.json`,
`hypotheses.json`, `data/` and the task manifests, every one pinned by sha256 in
`gate.json`.  Nothing here contacts a host or calls a model, and planning the same inputs
again writes the same files.
"""
from __future__ import annotations

import datetime as _dt
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cflib import gitsafe, hashing, vocab
from cflib.errors import CfError
from cflib.jsonio import read_json, write_json
from cflib.paths import WorkspacePaths, skill_root, templates_dir
from cflib.yamlio import read_yaml

from locate.prompts import gate_exits
from locate.tend_root import reviewer_git
from plan.make_tend_root import BRIDGE_HELPERS, TendRootPlan, make_tend_root
from plan.presets import Preset
from plan.render import render_file
from plan.render_prompts import finish_sentence

from informalize import approve, bootstrap, itemtable, stages
from informalize.outrepo import OutputRepo

GATE_SCRIPT = "verify_informal.py"
GATE_SCHEMA = "cf-gate-informalize/1"
TASK_SCHEMA = "cf-informalize-task/1"
RUN_MODE = "informalize"
LOCAL = "local"
BRIDGE = "agentbox-claude"
MERGE_TARGET = "main"
#: (source in the skill, name in the gate directory); the runtime goes in as `tex/`
GATE_FILES = (("scripts/gate/verify_informal.py", GATE_SCRIPT),
              ("scripts/gate/verify_findings.py", "verify_findings.py"))
#: the conventions task's files, which every section reads (section 8.2)
CONVENTION_FILES = ("informal/notation.sty", "informal/sections/00-conventions.tex",
                    "informal/STYLE-project.md", "informal/hypotheses.json")
CONVENTION_READS = ("informal/notation.sty", "informal/hypotheses.json",
                    "informal/sections/00-conventions.tex")
ITEMS = "informal/items.json"
DECLARATIONS = "informal/declarations.json"
HYPOTHESES = "informal/hypotheses.json"
#: the bridge's observable-state fallback (section 8.1): a phase's own documents, or a glob
#: that matches nothing, so a worker that wrote no result file is needs_review
FINDINGS_GLOB = {"attack": "findings/${TEND_TASK_ID:-*}/*.json",
                 "judge": "findings/${TEND_TASK_ID:-*}/*.json",
                 "edit": "findings/edit/*.json"}
NO_RESULT_GLOB = "findings/.no-result-file/*.json"
#: what a worker or reviewer checks with the gate's --findings call, per phase
FINDINGS_PATH = {"items": ITEMS, "conventions": HYPOTHESES,
                 "attack": "findings/$TEND_TASK_ID/attack.json",
                 "judge": "findings/$TEND_TASK_ID/ruling.json",
                 "edit": "findings/edit/edit.json"}
PROMPT_FILES = ("worker-system.md", "worker.md", "reviewer-system.md", "reviewer.md")
#: which run a phase reads with --from-run
READS = {"judge": "attack", "edit": "judge"}


def preset(phase: str) -> Preset:
    """A phase as a `plan.presets.Preset`, so `make_tend_root` renders its root."""
    return Preset(name=f"informalize-{phase}", mode=RUN_MODE, kinds=(), verdicts=(),
                  witness_required=False, book_access="none", bridge=BRIDGE, model=None,
                  task_style=RUN_MODE, slug_style="dash", max_workers=6, max_reviewers=4,
                  note=f"informalize phase {phase} (docs/proposals/informalize.md section 8.2)")


def prompt_templates(phase: str) -> dict[str, str]:
    return {"worker-system.md": "informalize-worker-system.md.tmpl",
            "worker.md": f"informalize-{phase}.md.tmpl",
            "reviewer-system.md": "informalize-reviewer-system.md.tmpl",
            "reviewer.md": "informalize-reviewer.md.tmpl"}


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #

@dataclass
class Task:
    """One planned task: its id, what it owns and must write, and what it reads."""

    id: str
    title: str
    summary: str
    paths: list[str]
    required: list[str]
    section: str | None = None
    section_file: str | None = None
    direction: str | None = None
    items: list[str] = field(default_factory=list)
    attacks: list[str] = field(default_factory=list)
    rulings: list[str] = field(default_factory=list)

    def manifest(self, run_id: str, phase: str) -> dict:
        return {"schema": TASK_SCHEMA, "task_id": self.id, "run_id": run_id, "phase": phase,
                "section": self.section, "section_file": self.section_file,
                "direction": self.direction, "paths": list(self.paths),
                "required": list(self.required), "items": list(self.items),
                "attacks": list(self.attacks), "rulings": list(self.rulings)}


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def nn(number: str) -> str:
    return f"{int(number):02d}"


def section_paths(table: dict) -> dict[str, str]:
    """``{section number: informal/sections/NN-<slug>.tex}``."""
    return {s["number"]: f"informal/sections/{itemtable.section_file(s)}.tex"
            for s in table.get("sections") or []}


def section_items(table: dict, number: str) -> list[str]:
    return [it["id"] for it in table.get("items") or []
            if it.get("section") == number and it.get("in_v1") is True]


def make_tasks(phase: str, table: dict | None, sections: list[str], *,
               attacks: dict[str, list[str]] | None = None, rulings: list[str] | None = None,
               request_files: list[str] | None = None) -> list[Task]:
    """The tasks of one phase (section 8.2), in a fixed order."""
    if phase == "items":
        return [Task("items", "Draft the item table",
                     "Choose the items, sections, parts and declarations of the text",
                     [ITEMS, "informal/items-review.md"], [ITEMS, "informal/items-review.md"])]
    if phase == "conventions":
        return [Task("conventions", "Fix the conventions",
                     "Write the notation, the standing hypotheses and the conventions section",
                     list(CONVENTION_FILES), [HYPOTHESES])]
    files = section_paths(table or {})
    titles = {s["number"]: s["title"] for s in (table or {}).get("sections") or []}
    out: list[Task] = []
    if phase == "edit":
        doc = "findings/edit/edit.json"
        owned = sorted(set(CONVENTION_FILES) | set(request_files or ()))
        return [Task("edit", "Apply the judges' requests",
                     "Apply or decline every request of the judge run, in the shared files",
                     owned + [doc, "findings/edit/items-proposal.json"], [doc],
                     rulings=list(rulings or []))]
    for number in sections:
        sf, items = files[number], section_items(table, number)
        head = f"section {number}, {titles[number]}"
        if phase == "draft":
            out.append(Task(f"draft-{nn(number)}", f"Draft {head}",
                            f"Write the statements of {len(items)} item(s)", [sf], [sf],
                            section=number, section_file=sf, items=items))
        elif phase == "attack":
            for word, direction in (("lean", "lean-first"), ("tex", "tex-first")):
                task = f"attack-{word}-{nn(number)}"
                doc = f"findings/{task}/attack.json"
                out.append(Task(task, f"Attack {head}, {direction}",
                                f"Compare {len(items)} item(s) with the Lean, {direction}",
                                [doc], [doc], section=number, section_file=sf,
                                direction=direction, items=items))
        elif phase == "judge":
            task = f"judge-{nn(number)}"
            doc = f"findings/{task}/ruling.json"
            out.append(Task(task, f"Judge {head}", "Rule on the attackers' findings and fix "
                                                   "the real ones", [sf, doc], [doc],
                            section=number, section_file=sf, items=items,
                            attacks=list((attacks or {}).get(number) or [])))
    return out


def quoted(text: str) -> str:
    """``text`` escaped for the inside of a YAML double-quoted scalar (JSON's escapes, which YAML
    reads): a task's title holds its section's title, LaTeX source, whose backslashes and quote
    marks are kept as written."""
    return json.dumps(text, ensure_ascii=False)[1:-1]


def task_yaml(task: Task, phase: str, layout: dict) -> str:
    """The tend task file of one task."""
    lines = [f"Phase `{phase}` of the informalize run. Your manifest is "
             f"`{layout['tasks_dir']}/{task.id}.json`.", "",
             "You may change these paths, and this file's `status:` line, nothing else:", ""]
    lines += [f"- `{p}`" + (" (required)" if p in task.required else "") for p in task.paths]
    if task.section:
        lines += ["", f"Section {task.section}: `{task.section_file}`, items "
                      f"{', '.join(task.items) or '(none)'}."]
    if task.attacks:
        lines += ["", "The attack documents you rule on: "
                  + ", ".join(f"`{layout['gate_dir']}/data/attacks/{a}.json`" for a in task.attacks)
                  + "."]
    if task.rulings:
        lines += ["", "The rulings whose requests you answer: "
                  + ", ".join(f"`{layout['gate_dir']}/data/rulings/{r}.json`" for r in task.rulings)
                  + "."]
    lines += ["", "Commit what you write, and set `status: complete` here when it is done."]
    ctx = {"task_id": task.id, "title": quoted(task.title), "priority": "high",
           "summary": quoted(task.summary),
           "description": "\n".join(("  " + line) if line.strip() else "" for line in lines)}
    text = render_file(templates_dir() / "tasks" / "informalize.yaml.tmpl", ctx)
    return text if text.endswith("\n") else text + "\n"


# --------------------------------------------------------------------------- #
# the output repository
# --------------------------------------------------------------------------- #

def tree_at(root: Path, commit: str) -> dict[str, tuple[str, bytes]]:
    """``{path: (mode, bytes)}`` of every file of ``commit``, read through `cflib.gitsafe`
    from git's objects (no filter runs); a link or a submodule is refused."""
    names = gitsafe.filter_names(root)
    blocked = gitsafe.unexpressible_filter(names)
    if blocked is not None:
        raise CfError(gitsafe.refusal(root, blocked))
    env = gitsafe.env(root)
    listed = subprocess.run(gitsafe.argv(root, "ls-tree", "-r", "-z", "--full-tree", commit),
                            capture_output=True, env=env, check=False)
    if listed.returncode != 0:
        raise CfError(f"git ls-tree {commit} failed in {root}")
    wanted: list[tuple[str, str, str]] = []
    for entry in listed.stdout.split(b"\0"):
        if not entry:
            continue
        meta, _, raw = entry.partition(b"\t")
        mode, kind, sha = meta.decode("ascii").split()
        path = raw.decode("utf-8", "surrogateescape")
        if kind != "blob" or mode not in ("100644", "100755"):
            raise CfError(f"the output repository holds {path} as a {kind} of mode {mode} at "
                          f"{commit[:12]}", hint="it holds files only; remove it and commit")
        wanted.append((path, mode, sha))
    out: dict[str, tuple[str, bytes]] = {}
    if not wanted:
        return out
    batch = subprocess.run(gitsafe.argv(root, "cat-file", "--batch"), capture_output=True,
                           env=env, check=False,
                           input="".join(f"{sha}\n" for _p, _m, sha in wanted).encode("ascii"))
    if batch.returncode != 0:
        raise CfError(f"git cat-file failed in {root}")
    data, at = batch.stdout, 0
    for path, mode, sha in wanted:
        end = data.index(b"\n", at)
        size = int(data[at:end].split()[2])
        out[path] = (mode, data[end + 1:end + 1 + size])
        at = end + 1 + size + 1
    return out


def write_files(files: dict[str, tuple[str, bytes]], dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    for rel, (mode, data) in sorted(files.items()):
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if mode == "100755":
            path.chmod(0o755)


def _json_at(files: dict[str, tuple[str, bytes]], rel: str, what: str) -> dict:
    if rel not in files:
        raise CfError(f"the output repository holds no {rel} at its HEAD", hint=what)
    import json

    try:
        doc = json.loads(files[rel][1].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CfError(f"{rel} does not parse ({exc})") from None
    if not isinstance(doc, dict):
        raise CfError(f"{rel} is not an object")
    return doc


# --------------------------------------------------------------------------- #
# what each phase needs before it is planned
# --------------------------------------------------------------------------- #

def check_ready(workspace, informalize_id: str, phase: str, repo: OutputRepo) -> None:
    """Refuse a phase whose inputs are not there (section 8.1)."""
    paths = workspace.paths
    if not paths.informalize_scope(informalize_id).is_file():
        raise CfError("stage I2 has not run: there is no scope.json",
                      hint=f"cf informalize scope {informalize_id}")
    if not paths.informalize_library_src(informalize_id).is_dir():
        raise CfError("stage I1 has not run: there is no library/src/",
                      hint=f"cf informalize fetch-library {informalize_id} --host H --execute "
                           "--i-have-consent")
    changed = repo.changes()
    if changed:
        raise CfError(f"the output repository is not clean ({len(changed)} changed path(s), "
                      f"{changed[0]} first)", hint="a phase is planned from a commit: commit or "
                                                   "remove the changes")
    drift = bootstrap.sync_problems(workspace, informalize_id)
    if drift:
        raise CfError(f"the output repository's runtime is not this checkout's: {drift[0]}",
                      hint=f"cf informalize sync {informalize_id}: the agents build with the "
                           "tools the gate checks with")
    if phase == "items":
        return
    stamp = repo.read_bytes("informal/informalize.json") or b"{}"
    import json

    if json.loads(stamp.decode("utf-8")).get("source_date_epoch") is None:
        raise CfError("informal/informalize.json has no source_date_epoch, which every build "
                      "needs", hint=f"cf informalize fetch-library {informalize_id} --host H "
                                    "--execute --i-have-consent writes the library commit's time")
    for gate in ("items", "references"):
        state, stale = approve.gate_state(workspace, informalize_id, gate)
        if state != "current":
            raise CfError(f"the {gate} approval is {state}" + (f" ({stale[0]})" if stale else ""),
                          hint=f"the maintainer runs `cf informalize approve {informalize_id} "
                               f"{gate} --by <name>` (section 13)")
    if phase != "conventions" and stages.read_receipt(paths, informalize_id, "I4") is None:
        raise CfError("the conventions phase has not been applied (no stages/I4.json)",
                      hint="plan, run, harvest and apply `--phase conventions` first")


def harvested_run(paths: WorkspacePaths, run_id: str, informalize_id: str, phase: str) -> dict:
    """The `informalize:` block of a harvested run of ``phase`` of this id, or a refusal."""
    path = paths.run_yaml(run_id)
    doc = read_yaml(path) if path.is_file() else None
    block = (doc or {}).get("informalize") if isinstance(doc, dict) else None
    if not isinstance(block, dict) or block.get("id") != informalize_id:
        raise CfError(f"run {run_id} is no informalize run of {informalize_id}",
                      hint=f"--from-run names the harvested {phase} run this phase reads")
    if block.get("phase") != phase:
        raise CfError(f"run {run_id} is a {block.get('phase')} run, not a {phase} run")
    if not paths.receipt_json(run_id).is_file():
        raise CfError(f"run {run_id} is not harvested (no {paths.receipt_json(run_id)})",
                      hint=f"cf harvest {run_id} --execute --i-have-consent")
    return block


def corpus_document(paths: WorkspacePaths, run_id: str, task: str, name: str) -> dict | None:
    """``corpus/<run>/<task>/<name>``, a harvested task document, or None."""
    path = paths.corpus_run_dir(run_id) / task / name
    if not path.is_file():
        return None
    doc = read_json(path)
    return doc if isinstance(doc, dict) else None


def touched_sections(paths: WorkspacePaths, informalize_id: str, repo: OutputRepo,
                     numbers: list[str], files: dict[str, str]) -> list[str]:
    """Every section whose file, or a conventions file every section reads, changed since
    the base commit of the last attack run that covered it (or that no attack run covered)."""
    last: dict[str, tuple[str, str]] = {}
    runs = paths.runs_dir
    for path in sorted(runs.glob("*/run.yaml")) if runs.is_dir() else []:
        doc = read_yaml(path)
        block = doc.get("informalize") if isinstance(doc, dict) else None
        if not isinstance(block, dict) or block.get("id") != informalize_id \
                or block.get("phase") != "attack" \
                or not paths.receipt_json(path.parent.name).is_file():
            continue
        stamp = str(doc.get("created") or "")
        for number in block.get("sections") or []:
            if number not in last or stamp > last[number][0]:
                last[number] = (stamp, str(block.get("base_commit") or ""))
    out: list[str] = []
    for number in numbers:
        if number not in last:
            out.append(number)
            continue
        base = last[number][1]
        status = subprocess.run(gitsafe.argv(repo.root, "diff", "--quiet", base, "HEAD", "--",
                                             files[number], *CONVENTION_READS),
                                env=gitsafe.env(repo.root), capture_output=True, check=False)
        if status.returncode != 0:
            out.append(number)
    return out


def choose_sections(spec: str | None, phase: str, table: dict, *, paths: WorkspacePaths,
                    informalize_id: str, repo: OutputRepo, files: dict[str, tuple[str, bytes]],
                    with_findings: list[str] | None = None) -> list[str]:
    """The sections a draft, attack or judge run plans (`--sections`)."""
    every = [s["number"] for s in table.get("sections") or [] if section_items(table, s["number"])]
    where = section_paths(table)
    if phase not in ("draft", "attack", "judge"):
        if spec:
            raise CfError(f"--sections belongs to the draft, attack and judge phases, not {phase}")
        return []
    if spec in (None, "", "all"):
        chosen = list(with_findings) if (phase == "judge" and spec in (None, "")) else every
    elif spec == "touched":
        if phase != "attack":
            raise CfError("--sections touched belongs to the attack phase")
        chosen = touched_sections(paths, informalize_id, repo, every, where)
    else:
        wanted = [str(int(n)) for n in spec.split(",") if n.strip().isdigit()]
        if not wanted or len(wanted) != len([n for n in spec.split(",") if n.strip()]):
            raise CfError(f"--sections {spec!r} is all, touched or a comma list of numbers")
        unknown = sorted(set(wanted) - set(every), key=int)
        if unknown:
            raise CfError(f"section(s) {', '.join(unknown)} hold no in_v1 item of the table")
        chosen = [n for n in every if n in wanted]
    for number in chosen:
        text = files.get(where[number], ("", b""))[1]
        if phase == "draft" and text.strip():
            raise CfError(f"section {number} is drafted already ({where[number]})",
                          hint="a drafted section changes through attack, judge and edit, "
                               "never a second draft")
        if phase in ("attack", "judge") and not text.strip():
            raise CfError(f"section {number} is not drafted ({where[number]} is empty)",
                          hint="plan --phase draft first")
    if not chosen:
        raise CfError(f"no section to plan for the {phase} phase",
                      hint="every chosen section is done" if spec == "touched" else
                      "check --sections and the table")
    return chosen


# --------------------------------------------------------------------------- #
# where things are
# --------------------------------------------------------------------------- #

def library_dir(informalize_id: str, library_commit: str) -> str:
    """The library's source on the host, under the remote root: one folder per id and commit,
    so a run of another id, or of this one at another commit, never replaces it."""
    return f"library-src/{informalize_id}-{library_commit[:12]}"


def host_layout(paths: WorkspacePaths, informalize_id: str, run_id: str, *, host: str,
                library_commit: str, remote_root: str = "", ssh: str = "") -> dict[str, dict]:
    """The run host's paths and this machine's mirror of each."""
    rd = paths.run_dir(run_id)
    tend = rd / "tend-root"
    local = {"kind": LOCAL, "remote_root": "", "ssh": "",
             "entrypoint": str(rd / "entrypoint"), "tend_root": str(tend),
             "gate_dir": str(tend / "gate"), "prompts_dir": str(tend / "prompts"),
             "tasks_dir": str(tend / "gate" / "tasks"),
             "library_src": str(paths.informalize_library_src(informalize_id)),
             "log_dir": str(rd / "gate-local-log"),
             "orchestrator_db": str(tend / "orchestrator.sqlite"),
             "attempts_dir": str(tend / "attempts")}
    if host == LOCAL:
        remote = dict(local, log_dir=str(tend / "gate-log"))
    else:
        root = remote_root.rstrip("/")
        tend_r, gate = f"{root}/{run_id}-tend", f"{root}/{run_id}-gate"
        remote = {"kind": "remote", "remote_root": root, "ssh": ssh or host,
                  "entrypoint": f"{root}/{run_id}", "tend_root": tend_r, "gate_dir": gate,
                  "prompts_dir": f"{tend_r}/prompts", "tasks_dir": f"{gate}/tasks",
                  "library_src": f"{root}/{library_dir(informalize_id, library_commit)}",
                  "log_dir": f"{tend_r}/gate-log",
                  "orchestrator_db": f"{tend_r}/orchestrator.sqlite",
                  "attempts_dir": f"{tend_r}/attempts"}
    for side in (local, remote):
        side["gate_command"] = (f"python3 {side['gate_dir']}/{GATE_SCRIPT} "
                                f"--gate-config {side['gate_dir']}/gate.json")
    return {"local": local, "host": remote}


def wrapper_env(phase: str, gate_dir: str) -> dict[str, dict[str, str]]:
    """``{role: {VAR: value}}`` the two wrappers export (section 8.1)."""
    gate_command = f"python3 {gate_dir}/{GATE_SCRIPT} --gate-config {gate_dir}/gate.json"
    common = {"CF_TASK_MANIFESTS": f"{gate_dir}/tasks", "CF_MERGE_TARGET": MERGE_TARGET,
              "CF_ATTEMPT_CAP": str(vocab.INFORMALIZE_ATTEMPT_CAP),
              "CF_FINDINGS_GLOB": FINDINGS_GLOB.get(phase, NO_RESULT_GLOB)}
    worker = [f"Bash(bash {gate_dir}/tex/tools/build_pdf.sh:*)",
              f"Bash(python3 {gate_dir}/tex/tools/check_items.py:*)"]
    reviewer = ["Read", "Glob", "Grep", *reviewer_git(), f"Bash({gate_command} --findings:*)"]
    return {"worker": {**common, "CF_EXTRA_TOOLS": ",".join(worker)},
            "reviewer": {**common, "CF_REVIEWER_TOOLS": ",".join(reviewer)}}


# --------------------------------------------------------------------------- #
# staging
# --------------------------------------------------------------------------- #

def _copy_runtime(dest: Path) -> list[Path]:
    src = templates_dir() / "informalize" / "runtime"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return sorted(p for p in dest.rglob("*") if p.is_file())


def stage_gate(gate: Path, *, tasks: list[Task], run_id: str, phase: str,
               files: dict[str, tuple[str, bytes]], data: dict[str, dict]) -> dict[str, str]:
    """Write the gate directory but gate.json; ``{path under gate/: sha256}`` of every file."""
    root = skill_root()
    missing = [str(root / rel) for rel, _ in GATE_FILES if not (root / rel).is_file()]
    if missing:
        raise CfError("gate file missing: " + ", ".join(missing),
                      hint="plan from a complete formal-companion checkout")
    if gate.exists():
        shutil.rmtree(gate)
    gate.mkdir(parents=True)
    for rel, name in GATE_FILES:
        shutil.copy2(root / rel, gate / name)
    _copy_runtime(gate / "tex")
    (gate / "declarations.json").write_bytes(files[DECLARATIONS][1])
    if HYPOTHESES in files:
        (gate / "hypotheses.json").write_bytes(files[HYPOTHESES][1])
    for rel, doc in sorted(data.items()):
        write_json(gate / "data" / rel, doc, sort_keys=True)
    for task in tasks:
        write_json(gate / "tasks" / f"{task.id}.json", task.manifest(run_id, phase), sort_keys=True)
    return {p.relative_to(gate).as_posix(): hashing.sha256_file(p)
            for p in sorted(gate.rglob("*")) if p.is_file() and p.name != "gate.json"
            and "__pycache__" not in p.parts}


def gate_config(side: dict, *, informalize_id: str, run_id: str, phase: str, base_commit: str,
                attack_base_commit: str | None, library: dict, pins: dict[str, str],
                base_files: dict[str, str], tex_packages: list[str]) -> dict:
    """`gate.json` for one side (the host's paths, or this machine's)."""
    return {"schema": GATE_SCHEMA, "id": informalize_id, "run_id": run_id, "phase": phase,
            "base_commit": base_commit, "attack_base_commit": attack_base_commit,
            "library": {"commit": library["commit"], "src": side["library_src"],
                        "license": library.get("license")},
            "base_files": dict(sorted(base_files.items())),
            "files": dict(sorted(pins.items())),
            "log_dir": side["log_dir"], "orchestrator_db": side["orchestrator_db"],
            "attempts_dir": side["attempts_dir"],
            "attempt_cap": vocab.INFORMALIZE_ATTEMPT_CAP,
            "build_timeout": vocab.INFORMALIZE_BUILD_TIMEOUT,
            "copy_run_words": vocab.INFORMALIZE_COPY_RUN_WORDS,
            "tex_packages": list(tex_packages)}


def finish_block() -> str:
    """The worker's one way to finish on the agentbox bridge."""
    shape = ('{"schema_version": 1, "status": "completed", "summary": "one or two sentences", '
             '"files_changed": ["<each path you changed>", "tasks/<task id>.yaml"]}')
    return ("## How to finish (required)\n"
            "Your stdout is not read, and there is no `final_result` tool on this bridge. Finish "
            "by writing **`.tend/agent_result.json`** in the worktree, last, after committing:\n\n"
            f"{shape}\n\n"
            "`status` is `completed` (written, committed, and your checks pass), `needs_review` "
            "(written, but you are unsure), or `blocked` (you could not do the work, or a gate "
            "or a tool was broken: say why in `summary`).")


def prompt_context(*, phase: str, run_id: str, informalize_id: str, cfg: dict, side: dict,
                   base_commit: str, attack_base_commit: str | None,
                   declarations_sha256: str) -> dict[str, Any]:
    library = cfg["library"]
    reference = (cfg.get("reference") or {}).get("work") or ""
    license_ = library.get("license") or "none recorded, so adapt nothing"
    p = preset(phase)
    return {
        "run_id": run_id, "informalize_id": informalize_id, "phase": phase,
        "theorem": library["theorem"], "library_url": library["url"],
        "library_commit": library["commit"], "library_license": license_,
        "reference_work": reference, "base_commit": base_commit,
        "attack_base_commit": attack_base_commit or base_commit,
        "declarations_sha256": declarations_sha256,
        "gate_command": side["gate_command"], "gate_dir": side["gate_dir"],
        "tasks_dir": side["tasks_dir"], "library_src": side["library_src"],
        "build_command": f"bash {side['gate_dir']}/tex/tools/build_pdf.sh",
        "check_command": f"python3 {side['gate_dir']}/tex/tools/check_items.py",
        "merge_target_branch": MERGE_TARGET,
        "copy_run_words": vocab.INFORMALIZE_COPY_RUN_WORDS,
        "finish_sentence": finish_sentence(p), "finish_block": finish_block(),
        "gate_exits": gate_exits("worker"), "gate_exits_reviewer": gate_exits("reviewer"),
        "has_findings_check": phase in FINDINGS_PATH,
        "findings_path": FINDINGS_PATH.get(phase, ""),
        "is_writing": phase in ("conventions", "draft", "judge", "edit"),
    }


def render_prompts(phase: str, ctx: dict[str, Any]) -> dict[str, str]:
    tdir = templates_dir() / "prompts"
    return {name: render_file(tdir / template, ctx)
            for name, template in prompt_templates(phase).items()}


def next_run_id(paths: WorkspacePaths, informalize_id: str, phase: str) -> str:
    """`<id>-<phase>-<n>`, `n` one more than the phase's last planned run."""
    prefix = f"{informalize_id}-{phase}-"
    numbers = [int(p.parent.name[len(prefix):]) for p in paths.runs_dir.glob(f"{prefix}*/run.yaml")
               if p.parent.name[len(prefix):].isdigit()] if paths.runs_dir.is_dir() else []
    return f"{prefix}{max(numbers, default=0) + 1}"


def check_run_id(paths: WorkspacePaths, run_id: str, informalize_id: str, phase: str,
                 from_run: str | None) -> None:
    if not vocab.RUN_TOKEN_ID.fullmatch(run_id or ""):
        raise CfError(f"--run-id {run_id!r} is not letters, digits and ._- (at most 64)")
    if run_id == from_run:
        raise CfError(f"run id {run_id} names the run this plan reads")
    path = paths.run_yaml(run_id)
    if path.is_file():
        doc = read_yaml(path)
        block = doc.get("informalize") if isinstance(doc, dict) else None
        if not isinstance(block, dict) or block.get("id") != informalize_id \
                or block.get("phase") != phase:
            raise CfError(f"run {run_id} exists and is not a {phase} run of {informalize_id}",
                          hint="use a new --run-id")
        if paths.receipt_json(run_id).is_file():
            raise CfError(f"run {run_id} is harvested; a harvested run is not planned again",
                          hint="use a new --run-id")


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #

@dataclass
class Planned:
    """What `plan` wrote, for the command to print."""

    run_id: str
    run_dir: Path
    phase: str
    tasks: list[Task]
    sections: list[str]
    base_commit: str
    model: str | None
    model_source: str
    workers: int
    reviewers: int
    max_cost: float
    agent_timeout: str
    gate_command: str
    warnings: list[str]


def _phase_inputs(workspace, informalize_id: str, phase: str, from_run: str | None,
                  table: dict | None) -> tuple[dict[str, dict], dict]:
    """``(data documents by path under data/, facts)`` a judge or edit phase reads."""
    paths = workspace.paths
    wanted = READS.get(phase)
    if wanted is None:
        if from_run:
            raise CfError(f"--from-run belongs to the judge and edit phases, not {phase}")
        return {}, {}
    if not from_run:
        raise CfError(f"the {phase} phase reads a harvested {wanted} run",
                      hint=f"pass --from-run <the {wanted} run>")
    block = harvested_run(paths, from_run, informalize_id, wanted)
    tasks = sorted((block.get("paths") or {}).keys())
    data: dict[str, dict] = {}
    if phase == "judge":
        attacks: dict[str, list[str]] = {}
        with_findings: list[str] = []
        for task in tasks:
            doc = corpus_document(paths, from_run, task, "attack.json")
            if doc is None:
                continue
            data[f"attacks/{task}.json"] = doc
            number = str(doc.get("section") or "")
            attacks.setdefault(number, []).append(task)
            if doc.get("findings") and number not in with_findings:
                with_findings.append(number)
        return data, {"attacks": attacks, "with_findings": with_findings,
                      "attack_base_commit": str(block.get("base_commit") or "")}
    requests: list[dict] = []
    rulings: list[str] = []
    fixed: list[str] = []
    for task in tasks:
        doc = corpus_document(paths, from_run, task, "ruling.json")
        if doc is None:
            continue
        data[f"rulings/{task}.json"] = doc
        rulings.append(task)
        requests += [r for r in doc.get("requests") or [] if isinstance(r, dict)]
        actions = [r.get("action") for r in doc.get("rulings") or [] if isinstance(r, dict)]
        if "fixed" in actions:
            fixed.append(str(doc.get("section") or ""))
    if not requests:
        raise CfError(f"judge run {from_run} made no request: there is nothing for an editor",
                      hint="plan --phase attack --sections touched next")
    sections = set(section_paths(table or {}).values())
    files = sorted({str(r.get("file")) for r in requests if str(r.get("file")) in sections})
    return data, {"rulings": rulings, "request_files": files, "fixed_sections": fixed,
                  "judge_base_commit": str(block.get("base_commit") or "")}


def check_edit_ready(informalize_id: str, repo: OutputRepo, table: dict,
                     files: dict[str, tuple[str, bytes]], from_run: str, facts: dict) -> None:
    """Refuse an edit run the whole document's build cannot pass, or that would undo its judge
    run (section 8.1): every section with an `in_v1` item drafted, and the judge run applied
    (its base commit an ancestor of HEAD, and each section file a judge fixed changed since)."""
    where = section_paths(table)
    empty = [n for n in where if section_items(table, n)
             and not files.get(where[n], ("", b""))[1].strip()]
    if empty:
        raise CfError(f"section(s) {', '.join(empty)} are not drafted, and the edit phase builds "
                      f"the whole document", hint=f"plan --phase draft --sections "
                                                  f"{','.join(empty)} first")
    base = facts["judge_base_commit"]
    ancestor = subprocess.run(gitsafe.argv(repo.root, "merge-base", "--is-ancestor", base, "HEAD"),
                              env=gitsafe.env(repo.root), capture_output=True, check=False)
    old = tree_at(repo.root, base) if ancestor.returncode == 0 else {}
    unapplied = [where[n] for n in facts["fixed_sections"] if n in where
                 and old.get(where[n]) == files.get(where[n])]
    if ancestor.returncode != 0 or unapplied:
        raise CfError(f"judge run {from_run} is not applied: " + (
                      f"{unapplied[0]} is the text its judge fixed" if unapplied else
                      f"HEAD does not descend from its base commit {base[:12]}"),
                      hint=f"apply it first (`cf informalize apply {informalize_id} --run "
                           f"{from_run}`, component 5b, not implemented yet): the editor works on "
                           "the judges' fixes")


def plan(workspace, informalize_id: str, phase: str, *, sections: str | None = None,
         from_run: str | None = None, workers: int | None = None, reviewers: int | None = None,
         agent_timeout: str | None = None, max_cost: float | None = None,
         model: str | None = None, host: str = LOCAL, run_id: str | None = None,
         consent: bool = False) -> Planned:
    """Plan one phase (section 8.1); every refusal comes before anything is written."""
    from informalize import config
    from locate.export import skill_commit
    from plan import models as models_mod
    from plan.make_tend_root import check_agent_timeout
    from run import manifest as manifest_mod

    if phase not in vocab.INFORMALIZE_PHASES:
        raise CfError(f"--phase {phase!r} is not one of {', '.join(vocab.INFORMALIZE_PHASES)}")
    paths = workspace.paths
    cfg = config.load(paths, informalize_id, sessions=config.session_limit(workspace))
    repo = OutputRepo(paths, informalize_id)
    repo.require_stamp()
    check_ready(workspace, informalize_id, phase, repo)
    base_commit = repo.head()
    if base_commit is None:
        raise CfError("the output repository has no commit")
    files = tree_at(repo.root, base_commit)
    if DECLARATIONS not in files:
        raise CfError(f"the output repository holds no {DECLARATIONS}",
                      hint=f"cf informalize scope {informalize_id}")
    table = _json_at(files, ITEMS, "the items phase writes it") if phase != "items" else None
    data, facts = _phase_inputs(workspace, informalize_id, phase, from_run, table)
    chosen = choose_sections(sections, phase, table or {}, paths=paths,
                             informalize_id=informalize_id, repo=repo, files=files,
                             with_findings=facts.get("with_findings"))
    attack_base = facts.get("attack_base_commit")
    if phase == "judge":
        missing = [n for n in chosen if not facts["attacks"].get(n)]
        if missing:
            raise CfError(f"section(s) {', '.join(missing)} have no attack document in "
                          f"{from_run}", hint="judge the sections the attack run covered")
        old = tree_at(repo.root, attack_base)
        where = section_paths(table)
        moved = [where[n] for n in chosen if old.get(where[n]) != files.get(where[n])]
        if moved:
            raise CfError(f"{moved[0]} differs from the attack run's base commit "
                          f"{attack_base[:12]}: the findings quote another text",
                          hint="attack the section again (--phase attack --sections touched)")
    if phase == "edit":
        check_edit_ready(informalize_id, repo, table, files, from_run, facts)
    tasks = make_tasks(phase, table, chosen, attacks=facts.get("attacks"),
                       rulings=facts.get("rulings"), request_files=facts.get("request_files"))
    run_id = run_id or next_run_id(paths, informalize_id, phase)
    check_run_id(paths, run_id, informalize_id, phase, from_run)

    caps = cfg.get("caps") or {}
    workers = workers if workers is not None else int(caps.get("workers") or 6)
    reviewers = reviewers if reviewers is not None else int(caps.get("reviewers") or 4)
    for name, value in (("--workers", workers), ("--reviewers", reviewers)):
        if isinstance(value, bool) or value < 1:
            raise CfError(f"{name} {value} is not a whole number >= 1")
    limit = config.session_limit(workspace)
    if workers + reviewers > limit:
        raise CfError(f"{workers} workers + {reviewers} reviewers is over the {limit} sessions "
                      f"the account allows", hint="lower --workers or --reviewers")
    max_cost = max_cost if max_cost is not None else caps.get("max_cost_per_phase")
    if max_cost is None or float(max_cost) <= 0:
        raise CfError("no spend cap: a phase is planned with one",
                      hint="pass --max-cost USD, or set caps.max_cost_per_phase in "
                           "informalize.yaml (the watchdog drains the run at it)")
    agent_timeout = check_agent_timeout(agent_timeout or caps.get("agent_timeout") or "2h")
    p = preset(phase)
    chosen_model, model_source = models_mod.resolve(requested=model, preset=p, workspace=workspace)
    if host == LOCAL:
        resolved = {"remote_root": "", "ssh": ""}
    else:
        resolved = manifest_mod.resolve_host(workspace, host)
    layouts = host_layout(paths, informalize_id, run_id, host=host,
                          library_commit=cfg["library"]["commit"],
                          remote_root=resolved["remote_root"], ssh=resolved["ssh"])
    warnings: list[str] = []
    if host != LOCAL and not consent:
        warnings.append("consent is not recorded: `cf run` refuses this run until it is "
                        "planned with --i-have-consent")

    # ---- every refusal is above this line: now write -----------------------------------
    rd = paths.run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)
    write_files(files, rd / "repo")
    scope = read_json(paths.informalize_scope(informalize_id))
    data_docs = {"scope.json": scope, **data}
    data_dir = rd / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    for rel, doc in sorted(data_docs.items()):
        write_json(data_dir / rel, doc, sort_keys=True)
    tasks_dir = paths.tasks_dir(run_id)
    if tasks_dir.exists():
        shutil.rmtree(tasks_dir)
    tasks_dir.mkdir(parents=True)
    for task in tasks:
        (tasks_dir / f"{task.id}.yaml").write_text(task_yaml(task, phase, layouts["host"]),
                                                   encoding="utf-8")
    gate_local = Path(layouts["local"]["gate_dir"])
    pins = stage_gate(gate_local, tasks=tasks, run_id=run_id, phase=phase, files=files,
                      data=data_docs)
    base_files = {rel: _sha256(blob) for rel, (_m, blob) in files.items()
                  if rel == ITEMS or rel in CONVENTION_FILES
                  or (rel.startswith("informal/sections/") and rel.endswith(".tex"))}
    runtime_json = _json_at(files, "informal/informalize.json", "cf informalize sync writes it")
    library = dict(cfg["library"])
    gates = {side: gate_config(layouts[side], informalize_id=informalize_id, run_id=run_id,
                               phase=phase, base_commit=base_commit,
                               attack_base_commit=attack_base, library=library, pins=pins,
                               base_files=base_files,
                               tex_packages=list(runtime_json.get("tex_packages") or []))
             for side in ("host", "local")}
    write_json(paths.gate_json(run_id), gates["host"], sort_keys=True)
    write_json(rd / "gate.local.json", gates["local"], sort_keys=True)
    declarations_sha = _sha256(files[DECLARATIONS][1])
    prompts = render_prompts(phase, prompt_context(
        phase=phase, run_id=run_id, informalize_id=informalize_id, cfg=cfg,
        side=layouts["host"], base_commit=base_commit, attack_base_commit=attack_base,
        declarations_sha256=declarations_sha))
    pdir = paths.prompts_dir(run_id)
    pdir.mkdir(parents=True, exist_ok=True)
    for name, text in prompts.items():
        (pdir / name).write_text(text, encoding="utf-8")
    agent = {k: v for k, v in {"agent_model": chosen_model,
                               "reviewer_agent_model": workspace.model("reviewer") or chosen_model,
                               "agentbox_profile": workspace.hosts.get("agentbox_profile")}.items()
             if v}
    host_side = layouts["host"]
    root_plan = TendRootPlan(local=Path(layouts["local"]["tend_root"]),
                             tend_root=host_side["tend_root"], entrypoint=host_side["entrypoint"],
                             gate_dir=host_side["gate_dir"], prompts_dir=host_side["prompts_dir"],
                             merge_target_branch=MERGE_TARGET, gate_script=GATE_SCRIPT)
    built = make_tend_root(
        root_plan, p, run_id=run_id, gate_config=gates["host"], prompts=prompts, agent=agent,
        task_count=len(tasks), model=chosen_model, agent_timeout=agent_timeout,
        config_template="config-informalize.yaml.tmpl",
        readme_template="README-informalize.md.tmpl", gate_files=(),
        bin_helpers=BRIDGE_HELPERS, extra_env=wrapper_env(phase, host_side["gate_dir"]),
        context={"phase": phase, "informalize_id": informalize_id, "base_commit": base_commit,
                 "library_src": host_side["library_src"], "max_workers": workers,
                 "max_reviewers": reviewers}, generator="cf plan-informalize")
    warnings += built["warnings"]
    staged = {f"gate/{rel}": sha for rel, sha in pins.items()}
    staged["gate/gate.json"] = hashing.sha256_file(gate_local / "gate.json")
    for path in sorted((Path(layouts["local"]["tend_root"]) / "bin").glob("*")):
        staged[f"bin/{path.name}"] = hashing.sha256_file(path)
    write_run_yaml(workspace, run_id=run_id, informalize_id=informalize_id, phase=phase,
                   tasks=tasks, chosen=chosen, base_commit=base_commit, from_run=from_run,
                   attack_base=attack_base, library=library, host=host, layouts=layouts,
                   consent=consent, model=chosen_model, model_source=model_source,
                   prompts=prompts, workers=workers, reviewers=reviewers, max_cost=max_cost,
                   agent_timeout=agent_timeout, staged=staged, warnings=warnings,
                   skill=skill_commit(), agents=built["agent_settings"])
    return Planned(run_id=run_id, run_dir=rd, phase=phase, tasks=tasks, sections=chosen,
                   base_commit=base_commit, model=chosen_model, model_source=model_source,
                   workers=workers, reviewers=reviewers, max_cost=float(max_cost),
                   agent_timeout=agent_timeout, gate_command=host_side["gate_command"],
                   warnings=warnings)


def write_run_yaml(workspace, *, run_id: str, informalize_id: str, phase: str,
                   tasks: list[Task], chosen: list[str], base_commit: str, from_run: str | None,
                   attack_base: str | None, library: dict, host: str, layouts: dict,
                   consent: bool, model: str | None, model_source: str, prompts: dict,
                   workers: int, reviewers: int, max_cost: float, agent_timeout: str,
                   staged: dict, warnings: list[str], skill: str | None, agents: dict) -> Path:
    """`run.yaml` with its `informalize:` block (section 8.1)."""
    from cflib.yamlio import write_yaml
    from run import manifest as manifest_mod

    paths = workspace.paths
    previous = read_yaml(paths.run_yaml(run_id)) if paths.run_yaml(run_id).is_file() else {}
    src = paths.informalize_library_src(informalize_id)
    host_side = layouts["host"]
    doc = {
        "version": 1, "run_id": run_id,
        "created": (previous or {}).get("created") or now(),
        "preset": preset(phase).name, "mode": RUN_MODE,
        "companion": None, "repos": [], "sources": [], "source_access": "none",
        "host": {"name": host, "kind": host_side["kind"],
                 "remote_root": host_side["remote_root"], "ssh": host_side["ssh"] or None},
        "remote": {"entrypoint": run_id, "tend_root": f"{run_id}-tend",
                   "gate": f"{run_id}-gate",
                   "library_src": library_dir(informalize_id, library["commit"])},
        "consent": {"given": bool(consent), "flag": "--i-have-consent",
                    "at": now() if consent else None,
                    "note": "cf run refuses to start a remote run without this"},
        "bridge": BRIDGE,
        "agents": {"allow_sandbox_bypass": False, "flag": "--allow-sandbox-bypass",
                   "env": "CF_ALLOW_SANDBOX_BYPASS"},
        "model": model, "model_source": model_source,
        "models": {"worker": workspace.model("worker"), "reviewer": workspace.model("reviewer")},
        "gate": {"command": host_side["gate_command"], "config": str(paths.gate_json(run_id)),
                 "local_config": str(paths.run_dir(run_id) / "gate.local.json"),
                 "out_of_tree": True, "script": GATE_SCRIPT},
        "tasks": {"count": len(tasks), "ids": [t.id for t in tasks]},
        "prompts": {name: hashing.sha256_text(text) for name, text in sorted(prompts.items())},
        "concurrency": {"workers": workers, "reviewers": reviewers},
        "budget": {"currency": "USD", "max_cost": float(max_cost),
                   "workspace_max_cost_usd": workspace.budget.get("max_cost_usd"),
                   "billing_path": workspace.budget.get("billing_path")},
        "informalize": {
            "id": informalize_id, "phase": phase, "sections": list(chosen),
            "base_commit": base_commit, "from_run": from_run,
            "attack_base_commit": attack_base,
            "paths": {t.id: list(t.paths) for t in tasks},
            "library": {"commit": library["commit"],
                        "src_sha256": manifest_mod.tree_digest(src)["sha256"]},
            "agent_timeout": agent_timeout, "skill_commit": skill,
            "gate_files": dict(sorted(staged.items())),
            "agents": dict(sorted(agents.items())),
        },
        "warnings": warnings,
    }
    return write_yaml(paths.run_yaml(run_id), doc,
                      header=f"# run.yaml -- {run_id} (informalize {phase})\n")
