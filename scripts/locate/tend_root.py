"""Stage a locate run's tend root: the shared machinery plus the locate gate directory.

New code, for docs/proposals/wikipedia-locate.md sections 4 and 6 and the
layout of references/locate-documents.md section 9.  The root itself --
``config.yaml``, ``.tend/``, the wrappers in ``bin/`` with the skill's bridge,
``prompts/`` -- is ``plan.make_tend_root``'s, rendered with the locate config
(``templates/tend/config-locate.yaml.tmpl``, whose validation commands run the
locate gate with ``--changed-files``) and these wrapper variables, which the
bridge reads:

* ``CF_EXTRA_TOOLS`` (the worker's wrapper only) -- the allowlist entries of the
  two read-only corpus tools, and of the passage-id tool in an inventory run;
* ``CF_TASK_MANIFESTS`` -- ``<gate>/tasks``, where the bridge finds the task
  manifest it writes the ``UNRESOLVED`` document from at the attempt cap;
* ``CF_ATTEMPT_CAP`` -- 3, the genuine reviewer rejections a task may take;
* ``CF_FINDINGS_GLOB`` -- the task's own nested documents, for the bridge's
  observable-state fallback.

The gate directory, outside every worktree, then gets what the locate gate and
the worker's tools read: the gate itself, ``locate_text.py`` and
``verify_findings.py`` (whose hardened change-set reader the gate imports),
``passage_ids.py``, ``locate_records.py`` (``scripts/locate/records.py``), the
two corpus tools, ``vendor/`` with both vendored packages and their manifests,
and ``tasks/<task-id>.json``.  That directory is the whole of what runs on the
host: every module any of those files imports is either the standard library
or another file of the directory, so ``python3 -I <gate>/verify_locate.py``
runs from it with nothing else on the path (references/locate-documents.md
section 9).  A file of :data:`GATE_FILES` missing from this checkout is a
refusal, not a warning: a gate directory without ``verify_findings.py`` once
made every gate call die with ``ModuleNotFoundError`` and Python's exit 1,
which the worker prompt reads as a verdict on the document (field defect P2).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from cflib import vocab
from cflib.errors import CfError
from cflib.paths import skill_root

from plan.make_tend_root import TendRootPlan, make_tend_root
from plan.presets import Preset

from .plan import GATE_SCRIPT, TaskSpec

#: (source in the skill as path parts, name in the gate directory).  Every one
#: is required: the gate imports ``locate_text`` and ``verify_findings``, the
#: id tool imports ``locate_text``, and the prompts render the other three.
GATE_FILES = (
    (("scripts", "gate", "verify_locate.py"), GATE_SCRIPT),
    (("scripts", "gate", "locate_text.py"), "locate_text.py"),
    (("scripts", "gate", "verify_findings.py"), "verify_findings.py"),
    (("scripts", "locate", "passage_ids.py"), "passage_ids.py"),
    (("scripts", "locate", "records.py"), "locate_records.py"),
    (("scripts", "corpus", "corpus_search.py"), "corpus_search.py"),
    (("scripts", "corpus", "corpus_show.py"), "corpus_show.py"),
)
VENDORED = ("literature_links", "formal_companion_annotations")


def wrapper_env(preset: Preset, host_gate_dir: str) -> dict[str, dict[str, str]]:
    """``{role: {VAR: value}}`` the locate wrappers export (see the module docstring)."""
    tools = [f"Bash(python3 {host_gate_dir}/corpus_search.py:*)",
             f"Bash(python3 {host_gate_dir}/corpus_show.py:*)"]
    if preset.mode == vocab.LOCATE_INVENTORY:
        tools.append(f"Bash(python3 {host_gate_dir}/passage_ids.py:*)")
    common = {"CF_TASK_MANIFESTS": f"{host_gate_dir}/tasks",
              "CF_ATTEMPT_CAP": "3",
              "CF_FINDINGS_GLOB": "findings/${TEND_TASK_ID:-*}/*.json"}
    return {"worker": {**common, "CF_EXTRA_TOOLS": ",".join(tools)}, "reviewer": dict(common)}


def _copy_tree(src: Path, dest: Path) -> list[Path]:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return sorted(p for p in dest.rglob("*") if p.is_file())


def stage(*, layout_host: dict, layout_local: dict, preset: Preset, run_id: str,
          gate_config: dict, prompts: dict[str, str], tasks: list[TaskSpec], model: str | None,
          agent: dict[str, Any] | None, allow_sandbox_bypass: bool,
          context: dict[str, Any]) -> dict[str, Any]:
    """Write the root and its locate gate directory; ``{"files", "warnings"}``."""
    root = skill_root()
    missing = [root.joinpath(*parts) for parts, _name in GATE_FILES
               if not root.joinpath(*parts).is_file()]
    if missing:
        raise CfError("this checkout cannot stage a locate gate that runs: "
                      + ", ".join(str(p) for p in missing) + " missing",
                      hint="plan from a complete formal-companion checkout")
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
        gate_files=(), extra_env=wrapper_env(preset, layout_host["gate_dir"]), context=context,
        generator="cf plan-locate")
    gate = plan.local / "gate"
    for parts, name in GATE_FILES:
        shutil.copy2(root.joinpath(*parts), gate / name)
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
    return built
