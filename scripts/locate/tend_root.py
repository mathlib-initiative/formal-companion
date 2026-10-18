"""Stage a locate run's tend root: the shared machinery plus the locate gate directory.

New code, for docs/proposals/wikipedia-locate.md sections 4 and 6 and the
layout of references/locate-documents.md section 9.  The root itself --
``config.yaml``, ``.tend/``, the wrappers in ``bin/`` with the skill's bridge,
``prompts/`` -- is ``plan.make_tend_root``'s, rendered with the locate config
(``templates/tend/config-locate.yaml.tmpl``, whose validation commands run the
locate gate with ``--changed-files``) and these wrapper variables, which the
bridge reads:

* ``CF_EXTRA_TOOLS`` (the worker's wrapper only) -- the allowlist entries of the
  two read-only corpus tools, each with ``--db <the run's tools database>``
  INSIDE the allowed prefix, and of the passage-id tool in an inventory run.  The
  prefix alone does not hold a database (argparse keeps the LAST ``--db``): the
  tools refuse a repeated or abbreviated ``--db`` and, in the gate directory,
  any database but the one ``gate.json`` pins (``corpus_search.check_db``);
* ``CF_REVIEWER_TOOLS`` (the reviewer's wrapper only) -- the bridge's read-only
  default plus the gate itself, which the reviewer prompt has it run;
* ``CF_TASK_MANIFESTS`` -- ``<gate>/tasks``, where the bridge finds the task
  manifest it writes the ``UNRESOLVED`` document from at the attempt cap;
* ``CF_ATTEMPT_CAP`` -- ``cflib.vocab.LOCATE_ATTEMPT_CAP``, the genuine reviewer
  rejections a task may take, and ``CF_MERGE_TARGET`` -- the branch the bridge
  reads a capped task's change set against, as the gate does;
* ``CF_FINDINGS_GLOB`` -- the task's own nested documents, for the bridge's
  observable-state fallback.

``bin/`` carries the bridge with every helper (``plan.make_tend_root.
BIN_HELPERS``) and, for a locate run, ``watchdog.py``.  The gate directory,
outside every worktree, then gets what the locate gate and the worker's tools
read (``GATE_FILES``, each one required): the gate itself and the findings gate
it imports (``verify_findings.py``, whose hardened change-set reader it uses),
``locate_text.py``, ``passage_ids.py``, ``locate_records.py``
(``scripts/locate/records.py``), the corpus tool pair (``corpus_show.py``
imports ``corpus_search.py``), ``vendor/`` with both vendored packages and
their manifests, and ``tasks/<task-id>.json``.  That directory is the whole of
what runs on the host: every module any of those files imports is either the
standard library or another file of the directory, so ``python3 -I
<gate>/verify_locate.py`` runs from it with nothing else on the path
(references/locate-documents.md section 9).  A file of ``GATE_FILES`` missing
from this checkout is a planning error before anything is written, not a
warning: a gate directory without ``verify_findings.py`` once made every gate
call die with ``ModuleNotFoundError`` and Python's exit 1, which the worker
prompt reads as a verdict on the document (field defect P2).  :func:`stage`
returns the sha256 of every file it staged into ``gate/`` and ``bin/`` (the
task manifests aside), which ``run.yaml`` pins as ``locate.gate_files``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from cflib import hashing, vocab
from cflib.errors import CfError
from cflib.paths import skill_root

from plan.make_tend_root import BRIDGE_HELPERS, TendRootPlan, make_tend_root
from plan.presets import Preset

from .plan import GATE_SCRIPT, TaskSpec

#: (source in the skill, name in the gate directory): references/locate-documents.md
#: section 9's gate directory.  Every row is required: the gate imports
#: ``locate_text``, ``verify_findings`` and ``locate_records``, the id tool imports
#: ``locate_text``, and the prompts render the other three.
GATE_FILES = (
    ("scripts/gate/verify_locate.py", GATE_SCRIPT),
    ("scripts/gate/verify_findings.py", "verify_findings.py"),
    ("scripts/gate/locate_text.py", "locate_text.py"),
    ("scripts/locate/passage_ids.py", "passage_ids.py"),
    ("scripts/locate/records.py", "locate_records.py"),
    ("scripts/corpus/corpus_search.py", "corpus_search.py"),
    ("scripts/corpus/corpus_show.py", "corpus_show.py"),
)
#: what a locate run's ``bin/`` adds to every run's helpers
LOCATE_BIN_HELPERS = BRIDGE_HELPERS
VENDORED = ("literature_links", "formal_companion_annotations")


def wrapper_env(preset: Preset, host_gate_dir: str, host_tools_db: str,
                merge_target: str = "main") -> dict[str, dict[str, str]]:
    """``{role: {VAR: value}}`` the locate wrappers export (see the module docstring)."""
    tools = [f"Bash(python3 {host_gate_dir}/corpus_search.py --db {host_tools_db}:*)",
             f"Bash(python3 {host_gate_dir}/corpus_show.py --db {host_tools_db}:*)"]
    if preset.mode == vocab.LOCATE_INVENTORY:
        tools.append(f"Bash(python3 {host_gate_dir}/passage_ids.py:*)")
    common = {"CF_TASK_MANIFESTS": f"{host_gate_dir}/tasks",
              "CF_ATTEMPT_CAP": str(vocab.LOCATE_ATTEMPT_CAP),
              "CF_MERGE_TARGET": merge_target,
              "CF_FINDINGS_GLOB": "findings/${TEND_TASK_ID:-*}/*.json"}
    # the reviewer's job is the form and the gate's output: it runs the gate, and nothing else
    reviewer_tools = ("Read,Glob,Grep,Bash(git:*),"
                      f"Bash(python3 {host_gate_dir}/{GATE_SCRIPT}:*)")
    return {"worker": {**common, "CF_EXTRA_TOOLS": ",".join(tools)},
            "reviewer": {**common, "CF_REVIEWER_TOOLS": reviewer_tools}}


def _copy_tree(src: Path, dest: Path) -> list[Path]:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return sorted(p for p in dest.rglob("*") if p.is_file())


def stage(*, layout_host: dict, layout_local: dict, preset: Preset, run_id: str,
          gate_config: dict, prompts: dict[str, str], tasks: list[TaskSpec], model: str | None,
          agent: dict[str, Any] | None, allow_sandbox_bypass: bool,
          context: dict[str, Any]) -> dict[str, Any]:
    """Write the root and its locate gate directory; ``{"files", "warnings", "gate_files"}``."""
    root = skill_root()
    missing = [str(root / rel) for rel, _name in GATE_FILES if not (root / rel).is_file()]
    if missing:
        # before anything is written: a root whose gate cannot import must never exist
        raise CfError("gate file missing: " + ", ".join(missing),
                      hint="the locate gate directory cannot run without it; plan from a "
                           "complete formal-companion checkout")
    plan = TendRootPlan(local=Path(layout_local["tend_root"]),
                        tend_root=layout_host["tend_root"],
                        entrypoint=layout_host["entrypoint"],
                        gate_dir=layout_host["gate_dir"],
                        prompts_dir=layout_host["prompts_dir"],
                        gate_script=GATE_SCRIPT)
    built = make_tend_root(
        plan, preset, run_id=run_id, gate_config=gate_config, prompts=prompts, agent=agent,
        task_count=len(tasks), model=model, allow_sandbox_bypass=allow_sandbox_bypass,
        config_template="config-locate.yaml.tmpl", readme_template="README-locate.md.tmpl",
        gate_files=(), bin_helpers=LOCATE_BIN_HELPERS,
        extra_env=wrapper_env(preset, layout_host["gate_dir"], layout_host["tools_corpus_db"],
                              plan.merge_target_branch),
        context=context, generator="cf plan-locate")
    gate = plan.local / "gate"
    for rel, name in GATE_FILES:
        shutil.copy2(root / rel, gate / name)
        built["files"].append(str(gate / name))
    vendor_dest = gate / "vendor"
    vendor_dest.mkdir(exist_ok=True)
    for name in VENDORED:
        built["files"].extend(str(p) for p in _copy_tree(root / "vendor" / name, vendor_dest / name))
        shutil.copy2(root / "vendor" / f"{name}.manifest.json",
                     vendor_dest / f"{name}.manifest.json")
        built["files"].append(str(vendor_dest / f"{name}.manifest.json"))
    tasks_dir = gate / "tasks"
    if tasks_dir.exists():
        shutil.rmtree(tasks_dir)
    tasks_dir.mkdir()
    for spec in tasks:
        path = tasks_dir / f"{spec.id}.json"
        path.write_text(json.dumps(spec.manifest(run_id), indent=1, sort_keys=True,
                                   ensure_ascii=False) + "\n", encoding="utf-8")
        built["files"].append(str(path))
    built["gate_files"] = staged_shas(plan.local)
    return built


def staged_shas(tend_root: Path) -> dict[str, str]:
    """``{path under the tend root: sha256}`` of everything in ``gate/`` and ``bin/``.

    The task manifests (``gate/tasks/``) are left out: they are the plan's own
    output, one per task, and the tasks are pinned by their ids.
    """
    out: dict[str, str] = {}
    for top in ("gate", "bin"):
        base = Path(tend_root) / top
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            rel = path.relative_to(tend_root).as_posix()
            if rel.startswith("gate/tasks/") or "__pycache__" in path.parts:
                continue
            out[rel] = hashing.sha256_file(path)
    return out
