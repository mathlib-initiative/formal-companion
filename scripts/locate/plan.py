"""Plan a locate run: tasks, task manifests, gate.json and run.yaml, from a sourceset.

New code, implementing docs/proposals/wikipedia-locate.md sections 3 (L3a, L3b,
L5), 5, 6 (Roots) and 7 in the layout of references/locate-documents.md
section 9.  A locate run is scoped to a SOURCESET pinned at one manifest
version and to one UNIVERSE, never to a companion:

* ``locate-inventory`` -- one task per rendered source, its deterministic
  finders' passages pre-filled in the task manifest;
* ``locate`` -- tasks of at most ``cflib.vocab.MAX_PASSAGES_PER_LOCATE_TASK``
  ``formal_math`` passages of a FROZEN inventory (the gated documents of an
  inventory run), one source per task; ``subject_only`` is the article-level
  tier, one subject passage per article bundled across sources;
* ``locate-challenge`` -- the batches ``locate.challenge_plan`` draws over
  harvested locate runs.

Planning is deterministic: the same sourceset version, universe, inventory,
seed and flags give the same task ids, the same manifests and the same gate
configuration, and a root never holds more than
``locate.presets.MAX_TASKS_PER_ROOT`` tasks (a larger plan is campaign shards,
references/locate-documents.md section 11).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from cflib import hashing, vocab
from cflib.errors import CfError
from cflib.paths import WorkspacePaths, skill_root, templates_dir

from plan.make_tasks import tend_priority
from plan.render import render_file

from . import challenge_plan as cplan, inputs, outcomes as outcomes_mod, presets as lp

GATE_SCHEMA = "cf-gate-locate/1"
GATE_SCRIPT = "verify_locate.py"
LOCAL = "local"
_HIGH_IMPORTANCE = {"top", "high"}


@dataclass
class TaskSpec:
    """One planned locate task: its id, its documents, and how tend shows it."""

    id: str
    mode: str
    title: str
    priority: str
    summary: str
    documents: list[dict]
    keys: list[str] = field(default_factory=list)

    def manifest(self, run_id: str) -> dict:
        return {"task_id": self.id, "mode": self.mode, "run_id": run_id,
                "documents": self.documents}

    @property
    def passages(self) -> int:
        return sum(len(d.get("passages") or d.get("records") or d.get("finders") or [])
                   for d in self.documents)


# --------------------------------------------------------------------------
# where things live on the run host
# --------------------------------------------------------------------------

def host_layout(paths: WorkspacePaths, *, host: str, run_id: str, sourceset_id: str,
                universe_id: str, inventory_run: str | None, remote_root: str = "",
                ssh: str = "") -> dict:
    """The run host's paths for every locate input, and the local mirror of each."""
    rd = paths.run_dir(run_id)
    local_tend = rd / "tend-root"
    local = {
        "kind": LOCAL, "remote_root": "", "ssh": "",
        "entrypoint": str(rd / "entrypoint"),
        "tend_root": str(local_tend),
        "gate_dir": str(local_tend / "gate"),
        "prompts_dir": str(local_tend / "prompts"),
        "sources_dir": str(paths.sourceset_sources_dir(sourceset_id)),
        "corpus_db": str(paths.corpus_db(universe_id)),
        "candidates_dir": str(paths.candidates_dir(sourceset_id, universe_id)),
        "inventory_dir": str(paths.corpus_run_dir(inventory_run)) if inventory_run else "",
        "tasks_dir": str(local_tend / "gate" / "tasks"),
        "log_dir": str(local_tend / "gate-log"),
        "orchestrator_db": str(local_tend / "orchestrator.sqlite"),
        "checkouts_dir": "",
    }
    if host == LOCAL:
        remote = dict(local)
    else:
        root = remote_root.rstrip("/")
        tend_root = f"{root}/{run_id}-tend"
        gate_dir = f"{root}/{run_id}-gate"
        remote = {
            "kind": "remote", "remote_root": root, "ssh": ssh or host,
            "entrypoint": f"{root}/{run_id}",
            "tend_root": tend_root,
            "gate_dir": gate_dir,
            "prompts_dir": f"{tend_root}/prompts",
            "sources_dir": f"{root}/sourcesets/{sourceset_id}/sources",
            "corpus_db": f"{root}/corpora/{universe_id}/corpus.sqlite",
            "candidates_dir": f"{root}/sourcesets/{sourceset_id}/candidates/{universe_id}",
            "inventory_dir": f"{root}/inventories/{inventory_run}" if inventory_run else "",
            "tasks_dir": f"{gate_dir}/tasks",
            "log_dir": f"{tend_root}/gate-log",
            "orchestrator_db": f"{tend_root}/orchestrator.sqlite",
            "checkouts_dir": f"{root}/corpora/{universe_id}/checkouts",
        }
    for layout in (local, remote):
        layout["gate_command"] = (f"python3 {layout['gate_dir']}/{GATE_SCRIPT} "
                                  f"--gate-config {layout['gate_dir']}/gate.json")
    return {"local": local, "host": remote}


# --------------------------------------------------------------------------
# selecting sources
# --------------------------------------------------------------------------

def _document_pin(entry: dict) -> dict:
    return {"source_id": entry["source_id"], "source_sha": entry["html_sha256"],
            "layer_sha256": entry["layer_sha256"], "renderer": entry["renderer"]}


def select_sources(manifest: inputs.Manifest, wanted: Iterable[str]) -> tuple[list[str], list[str]]:
    """``(rendered source ids to plan, unrendered ones skipped)``; a named one must be rendered."""
    wanted = list(wanted)
    unknown = [s for s in wanted if s not in manifest.entries]
    if unknown:
        raise CfError(f"not in sourceset {manifest.sourceset} version {manifest.version}: "
                      + ", ".join(unknown[:5]),
                      hint="`--source` names a source id of the pinned manifest")
    if wanted:
        bare = [s for s in wanted if not manifest.rendered(s)]
        if bare:
            raise CfError(f"not rendered in version {manifest.version}: " + ", ".join(bare[:5]),
                          hint="the sources stage renders a source before it can be planned")
        return sorted(dict.fromkeys(wanted)), []
    ids = sorted(manifest.entries)
    return ([s for s in ids if manifest.rendered(s)],
            [s for s in ids if not manifest.rendered(s)])


def _priority(entry: dict, *, hot: bool = False) -> str:
    importance = str((entry.get("assessment") or {}).get("importance") or "").lower()
    return tend_priority("high" if hot or importance in _HIGH_IMPORTANCE else "default")


# --------------------------------------------------------------------------
# the three task sets
# --------------------------------------------------------------------------

def inventory_tasks(paths: WorkspacePaths, manifest: inputs.Manifest,
                    source_ids: list[str]) -> list[TaskSpec]:
    """One task per source, the deterministic finders' passages in its manifest."""
    tasks = []
    for sid in source_ids:
        entry = manifest.entries[sid]
        view = inputs.source_view(paths, manifest.sourceset, sid)
        finders = sorted(view.finders(), key=lambda f: (int(f.get("start", 0)),
                                                        str(f.get("finder"))))
        for f in finders:
            if f.get("finder") not in vocab.DETERMINISTIC_FINDERS:
                raise CfError(f"{sid}: finders.json names finder {f.get('finder')!r}, which is "
                              "not a deterministic finder")
        doc = {**_document_pin(entry), "finders": finders}
        tasks.append(TaskSpec(id=f"inv-{sid}", mode=vocab.LOCATE_INVENTORY,
                              title=f"inventory: {sid}", priority=_priority(entry),
                              summary=f"inventory of {sid} ({len(finders)} finder passage(s))",
                              documents=[doc]))
    return tasks


def frozen_inventory(paths: WorkspacePaths, inventory_run: str, manifest: inputs.Manifest
                     ) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """``({source_id: [formal_math passages by span]}, {source_id: why it has none})``."""
    frozen = outcomes_mod.inventory(paths, inventory_run, require_gate=True)
    by_source: dict[str, list[dict]] = {}
    left_out: dict[str, str] = {sid: f"unresolved inventory ({u.get('reason', '')})"
                                for sid, u in frozen["unresolved"].items()}
    for (sid, _item), passage in sorted(frozen["passages"].items()):
        entry = manifest.entries.get(sid)
        if entry is None:
            left_out[sid] = "not in the pinned sourceset manifest"
            continue
        if passage.get("source_sha") != entry.get("html_sha256") or \
                passage.get("layer_sha256") != entry.get("layer_sha256"):
            left_out[sid] = "the inventory was made against another rendering of the source"
            continue
        if passage.get("scope") not in vocab.LOCATED_SCOPES:
            continue
        by_source.setdefault(sid, []).append(passage)
    for sid in by_source:
        by_source[sid].sort(key=lambda p: (int((p.get("span") or [0])[0]), p["item"]))
        left_out.pop(sid, None)
    return by_source, left_out


def _passage_row(p: dict) -> dict:
    return {"item": p["item"], "span": list(p["span"]), "head_words": p.get("head_words"),
            "prominence": p.get("prominence"), "passage_kind": p.get("passage_kind")}


def locate_tasks(manifest: inputs.Manifest, frozen: dict[str, list[dict]],
                 source_ids: list[str], *, per_task: int, subject_only: bool
                 ) -> tuple[list[TaskSpec], dict[str, str]]:
    """Tasks of at most ``per_task`` passages; ``subject_only`` bundles one per article."""
    if not 1 <= per_task <= vocab.MAX_PASSAGES_PER_LOCATE_TASK:
        raise CfError(f"--per-task must be 1..{vocab.MAX_PASSAGES_PER_LOCATE_TASK}, "
                      f"not {per_task}")
    skipped: dict[str, str] = {}
    tasks: list[TaskSpec] = []
    if subject_only:
        picks: list[tuple[str, dict]] = []
        for sid in source_ids:
            subjects = [p for p in frozen.get(sid, [])
                        if p.get("prominence") == vocab.PROMINENCE_SUBJECT]
            if not subjects:
                skipped[sid] = "no formal_math subject passage in the inventory"
                continue
            picks.append((sid, subjects[0]))
        for k in range(0, len(picks), per_task):
            chunk = picks[k:k + per_task]
            docs = [{**_document_pin(manifest.entries[sid]), "passages": [_passage_row(p)]}
                    for sid, p in chunk]
            number = k // per_task + 1
            tasks.append(TaskSpec(id=f"loc-subject-{number:04d}", mode=vocab.LOCATE,
                                  title=f"locate: subjects {number:04d}",
                                  priority=tend_priority("high"),
                                  summary=f"the subject passage of {len(chunk)} article(s)",
                                  documents=docs))
        return tasks, skipped
    for sid in source_ids:
        passages = frozen.get(sid, [])
        if not passages:
            skipped[sid] = "no formal_math passage in the inventory"
            continue
        entry = manifest.entries[sid]
        for k in range(0, len(passages), per_task):
            chunk = passages[k:k + per_task]
            hot = any(p.get("prominence") in vocab.REFUTE_BY_DEFAULT_PROMINENCE for p in chunk)
            number = k // per_task + 1
            tasks.append(TaskSpec(id=f"loc-{sid}-{number}", mode=vocab.LOCATE,
                                  title=f"locate: {sid} part {number}",
                                  priority=_priority(entry, hot=hot),
                                  summary=f"{len(chunk)} passage(s) of {sid}",
                                  documents=[{**_document_pin(entry),
                                              "passages": [_passage_row(p) for p in chunk]}]))
    return tasks, skipped


def challenge_tasks(manifest: inputs.Manifest, built: dict) -> list[TaskSpec]:
    """One task per batch of the challenge plan, its records grouped by source."""
    tasks = []
    keep = ("key", "target", "item", "span", "location_key", "library", "declaration",
            "relation", "rank", "outcome", "prominence", "scope", "passage_kind", "run_id")
    for number, batch in enumerate(built["batches"], 1):
        docs: list[dict] = []
        for rec in batch:
            entry = manifest.entries.get(rec["source_id"])
            if entry is None or not manifest.rendered(rec["source_id"]):
                raise CfError(f"challenged source {rec['source_id']} is not a rendered source of "
                              f"sourceset {manifest.sourceset} version {manifest.version}")
            if not docs or docs[-1]["source_id"] != rec["source_id"]:
                docs.append({**_document_pin(entry), "records": []})
            docs[-1]["records"].append({k: rec.get(k) for k in keep if rec.get(k) is not None})
        tid = f"chl-{number:04d}"
        tasks.append(TaskSpec(id=tid, mode=vocab.LOCATE_CHALLENGE, title=f"challenge: {tid}",
                              priority=tend_priority("high"),
                              summary=f"{len(batch)} record(s) of {len(docs)} source(s)",
                              documents=docs, keys=[r["key"] for r in batch]))
    return tasks


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _indent(text: str, spaces: int = 2) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else "" for line in text.rstrip("\n").splitlines())


def task_yaml(spec: TaskSpec, *, layout: dict, depends_on: list[str]) -> str:
    """The tend task file: tend's task shape, the documents named, the gate command."""
    what = {vocab.LOCATE_INVENTORY: "the passage table (inventory) of",
            vocab.LOCATE: "the locations of the manifest's passages of",
            vocab.LOCATE_CHALLENGE: "per-record challenge verdicts for"}[spec.mode]
    lines = [f"Write {what} each source below, one document per source:", ""]
    for doc in spec.documents:
        sid = doc["source_id"]
        lines.append(f"- `findings/{spec.id}/{sid}.json` — source `{sid}`; read "
                     f"`{layout['sources_dir']}/{sid}/text.txt`")
    lines += ["",
              f"The task manifest `{layout['tasks_dir']}/{spec.id}.json` lists what is yours. "
              "Write only those documents and this task file, and set this task's "
              "`status: complete` when the gate passes each document:",
              "",
              f"`{layout['gate_command']} --findings findings/{spec.id}/<source-id>.json`"]
    ctx = {"task_id": spec.id, "title": spec.title, "priority": spec.priority,
           "depends_on": "[" + ", ".join(depends_on) + "]", "summary": spec.summary,
           "description": _indent("\n".join(lines))}
    text = render_file(templates_dir() / "tasks" / "locate.yaml.tmpl", ctx)
    return text if text.endswith("\n") else text + "\n"


def write_tasks(tasks: list[TaskSpec], out_dir: Path, *, layout: dict,
                max_open_tasks: int | None) -> list[Path]:
    """``tasks/<id>.yaml`` for each task; refuses stale task files of another plan."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise CfError("two planned tasks share an id")
    written = []
    for i, spec in enumerate(tasks):
        depends = [ids[i - max_open_tasks]] if max_open_tasks and i >= max_open_tasks else []
        path = out_dir / f"{spec.id}.yaml"
        text = task_yaml(spec, layout=layout, depends_on=depends)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
        written.append(path)
    stale = {p.name for p in out_dir.glob("*.yaml")} - {p.name for p in written}
    if stale:
        raise CfError(f"{len(stale)} stale task file(s) in {out_dir}: {sorted(stale)[:5]}",
                      hint="this run directory was planned from other inputs; use a new run id")
    return written


def vendor_shas() -> dict[str, str]:
    """sha256 of each vendored package's manifest: what the gate verifies at start."""
    vendor = skill_root() / "vendor"
    return {name: hashing.sha256_file(vendor / f"{name}.manifest.json")
            for name in ("literature_links", "formal_companion_annotations")}


def gate_config(*, preset_mode: str, preset_name: str, run_id: str, layout: dict,
                universe: inputs.Universe, corpus_db_sha256: str, manifest: inputs.Manifest,
                source_ids: list[str], blind: bool, inventory_run: str | None,
                challenged_runs: list[str]) -> dict:
    """``gate.json`` of references/locate-documents.md section 9, for one layout."""
    return {
        "schema": GATE_SCHEMA,
        "mode": preset_mode,
        "preset": preset_name,
        "run_id": run_id,
        "kinds": sorted(vocab.kinds_for_mode(preset_mode)),
        "universe": {"id": universe.id, "sha256": universe.sha256,
                     "libraries": universe.gate_libraries()},
        "corpus_db": {"path": layout["corpus_db"], "sha256": corpus_db_sha256},
        "sourceset": {**manifest.pin(), "sources_dir": layout["sources_dir"]},
        "sources": {sid: {"html_sha256": manifest.entries[sid]["html_sha256"],
                          "layer_sha256": manifest.entries[sid]["layer_sha256"],
                          "renderer": manifest.entries[sid]["renderer"]}
                    for sid in sorted(source_ids)},
        "candidates_dir": layout["candidates_dir"],
        "blind": bool(blind),
        "inventory": ({"run_id": inventory_run, "dir": layout["inventory_dir"]}
                      if inventory_run else None),
        "challenged_runs": list(challenged_runs),
        "findings_dir": "findings",
        "tasks_dir": layout["tasks_dir"],
        "log_dir": layout["log_dir"],
        "orchestrator_db": layout["orchestrator_db"],
        "max_locations": vocab.MAX_LOCATIONS_PER_PASSAGE,
        "min_absence_queries": vocab.MIN_ABSENCE_QUERIES,
        "vendor": vendor_shas(),
    }


def check_root_size(tasks: list[TaskSpec]) -> None:
    if len(tasks) > lp.MAX_TASKS_PER_ROOT:
        raise CfError(
            f"{len(tasks)} tasks is over the {lp.MAX_TASKS_PER_ROOT} one tend root may hold",
            hint="plan the sources in campaign shards of at most "
                 f"{lp.MAX_TASKS_PER_ROOT} tasks each (references/locate-documents.md "
                 "section 11): name each shard's sources with --source and give each its own "
                 "--run-id")
    if not tasks:
        raise CfError("nothing to plan: no task was made from these inputs",
                      hint="check the sourceset is rendered, the inventory run is harvested "
                           "and gated, or the challenged runs hold records")


def challenge_build(paths: WorkspacePaths, locate_runs: list[str], *, seed: int,
                    sample_rate: float) -> dict:
    return cplan.plan(paths, locate_runs, seed=seed, sample_rate=sample_rate)


def challenge_document(run_id: str, built: dict, tasks: list[TaskSpec], *, seed: int,
                       sample_rate: float, locate_runs: list[str]) -> dict:
    task_of = {key: t.id for t in tasks for key in t.keys}
    return cplan.plan_document(run_id, built, seed=seed, sample_rate=sample_rate,
                               task_of=task_of, challenged_runs=locate_runs)


def summarise(tasks: list[TaskSpec]) -> dict[str, Any]:
    return {"count": len(tasks),
            "documents": sum(len(t.documents) for t in tasks),
            "passages": sum(t.passages for t in tasks)}
