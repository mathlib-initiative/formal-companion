"""Plan a locate run: tasks, task manifests, gate.json and run.yaml, from a sourceset.

New code, implementing docs/proposals/wikipedia-locate.md sections 3 (L3a, L3b,
L5), 5, 6 (Roots) and 7 in the layout of references/locate-documents.md
section 9.  A locate run is scoped to a SOURCESET pinned at one manifest
version and to one UNIVERSE, never to a companion:

* ``locate-inventory`` -- one task per rendered source of a concept article
  (a redirect, disambiguation page, list or biography is left out and named,
  unless asked for), its deterministic finders' passages pre-filled in the
  task manifest;
* ``locate`` -- tasks of at most ``cflib.vocab.MAX_PASSAGES_PER_LOCATE_TASK``
  ``formal_math`` passages of a FROZEN inventory (the gated documents of an
  inventory run), one source per task; ``subject_only`` is the article-level
  tier, one subject passage per article bundled across sources;
* ``locate-challenge`` -- the batches ``locate.challenge_plan`` draws over
  harvested locate runs.

Planning is deterministic: the same sourceset version, universe, inventory,
seed and flags give the same task ids, the same manifests and the same gate
configuration, and a root never holds more than
``cflib.vocab.MAX_TASKS_PER_ROOT`` tasks (a larger plan is campaign shards,
references/locate-documents.md section 11).

The sourceset is read through its owner (``sources.sourceset``): one source per
page, the one ``Manifest.preferred()`` names (an adopted revision wins), and a
source whose files on disk are not the ones its entry pins is refused at plan
time.  Two databases are named (section 9): ``corpus_db``, the full
``corpus.sqlite`` the gate checks every location in, and ``tools_corpus_db``,
the one the workers' two host tools search -- the same file, the G1-blind
build, or a planted one for the P tier.
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

from sources import render_html, websource
from sources.sourceset import Manifest

from . import challenge_plan as cplan, inputs, outcomes as outcomes_mod

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
        """Passages (records, for a challenge) the task owes.

        An inventory document's pre-filled rows are one per (finder, start), and
        two finders may share a start; the worker writes one passage per start,
        so that is what is counted.
        """
        if self.mode == vocab.LOCATE_INVENTORY:
            return sum(len({int(f.get("start", 0)) for f in d.get("finders") or []})
                       for d in self.documents)
        return sum(len(d.get("passages") or d.get("records") or []) for d in self.documents)


# --------------------------------------------------------------------------
# where things live on the run host
# --------------------------------------------------------------------------

def _host_tools_path(root: str, universe_id: str, tools_db: dict) -> str:
    """Where the tools database lands on a remote host (content-addressed when planted)."""
    if tools_db["kind"] == inputs.TOOLS_BLIND:
        return f"{root}/corpora/{universe_id}/corpus.blind.sqlite"
    if tools_db["kind"] == inputs.TOOLS_PLANTED:
        return f"{root}/corpora/{universe_id}/planted-{tools_db['sha256'][:16]}.sqlite"
    return f"{root}/corpora/{universe_id}/corpus.sqlite"


def host_layout(paths: WorkspacePaths, *, host: str, run_id: str, sourceset_id: str,
                universe_id: str, tools_db: dict, remote_root: str = "",
                ssh: str = "") -> dict:
    """The run host's paths for every locate input, and the local mirror of each.

    ``checkouts_dir`` stays empty: no stage creates, transfers or verifies
    library checkouts on a run host, so the challenge prompt names none.  The
    local layout's ``log_dir`` is the run directory's ``gate-local-log/``, so a
    gate run on this machine (the harvest's re-gate, a hand check) never writes
    into the log the workers' gate calls fill.
    """
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
        "tools_corpus_db": str(tools_db["path"]),
        "candidates_dir": str(inputs.candidates_dir(paths, sourceset_id, universe_id, tools_db)),
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
        sub = inputs.candidates_subdir(tools_db)
        gate_dir = f"{root}/{run_id}-gate"
        remote = {
            "kind": "remote", "remote_root": root, "ssh": ssh or host,
            "entrypoint": f"{root}/{run_id}",
            "tend_root": tend_root,
            "gate_dir": gate_dir,
            "prompts_dir": f"{tend_root}/prompts",
            "sources_dir": f"{root}/sourcesets/{sourceset_id}/sources",
            "corpus_db": f"{root}/corpora/{universe_id}/corpus.sqlite",
            "tools_corpus_db": _host_tools_path(root, universe_id, tools_db),
            "candidates_dir": (f"{root}/sourcesets/{sourceset_id}/candidates/{universe_id}"
                               + (f"/{sub}" if sub else "")),
            "tasks_dir": f"{gate_dir}/tasks",
            "log_dir": f"{tend_root}/gate-log",
            "orchestrator_db": f"{tend_root}/orchestrator.sqlite",
            "checkouts_dir": "",
        }
    local["log_dir"] = str(rd / "gate-local-log")
    for layout in (local, remote):
        layout["gate_command"] = (f"python3 {layout['gate_dir']}/{GATE_SCRIPT} "
                                  f"--gate-config {layout['gate_dir']}/gate.json")
    return {"local": local, "host": remote}


# --------------------------------------------------------------------------
# selecting sources
# --------------------------------------------------------------------------

def _document_pin(entry: dict) -> dict:
    inputs.check_renderer(entry)          # another renderer's source is pinned nowhere
    return {"source_id": entry["source_id"], "source_sha": entry["html_sha256"],
            "layer_sha256": entry["layer_sha256"], "renderer": entry["renderer"]}


def select_sources(manifest: Manifest, wanted: Iterable[str], *,
                   include_flagged: bool = False, refused: dict[str, dict] | None = None
                   ) -> tuple[list[str], list[str], dict[str, str]]:
    """``(source ids to plan, unrendered ones, {flagged one: why})``: one source per page.

    The page's source is the one ``Manifest.preferred()`` names; with no
    ``--source`` every page's is planned once it is rendered, unless its page is
    no concept article (``sources.websource.concept_page``: a redirect, a
    disambiguation page, a list or a biography, by the flags the entry recorded
    or, where it recorded none, by its own assessment class -- field defect S5:
    a fleet over a sourceset of null flags planned every stub and list as a paid
    task).  Those are left out and named, or planned with ``include_flagged``.
    A named source must be its page's preferred one, and rendered; it is
    planned whatever its flags say.  ``refused`` is the sourceset's standing
    parity refusals (:func:`inputs.parity_refusals`): a named one is refused as
    such, with the step that settles it, never with a render that cannot.
    """
    wanted = list(dict.fromkeys(wanted))
    unknown = [s for s in wanted if s not in manifest.entries]
    if unknown:
        raise CfError(f"not in sourceset {manifest.sourceset_id} version {manifest.version}: "
                      + ", ".join(unknown[:5]),
                      hint="`--source` names a source id of the pinned manifest")
    preferred = manifest.preferred()
    if wanted:
        other = [(s, preferred[manifest.entries[s]["pageid"]]) for s in wanted
                 if preferred[manifest.entries[s]["pageid"]] != s]
        if other:
            raise CfError("not the source its page plans: " + ", ".join(
                f"{s} (page {manifest.entries[s]['pageid']} plans {p})" for s, p in other[:5]),
                hint="a page plans one source: an adopted revision, else its highest "
                     "(`Manifest.preferred()`); name that one")
        bare = [s for s in wanted if not inputs.rendered(manifest.entries[s])]
        standing = {s: (refused or {})[s] for s in bare if s in (refused or {})}
        if standing:
            raise CfError(f"render refused, in version {manifest.version}, because the layer is "
                          f"not the producer's: " + inputs.parity_refused_text(standing),
                          hint=inputs.parity_remedy(manifest))
        if bare:
            raise CfError(f"not rendered in version {manifest.version}: " + ", ".join(bare[:5]),
                          hint=f"render it: `cf sourceset render {manifest.sourceset_id} "
                               f"--source {bare[0]}`")
        return sorted(wanted), [], {}
    ids = sorted(preferred.values())
    flagged: dict[str, str] = {}
    if not include_flagged:
        for sid in ids:
            flags = websource.effective_flags(manifest.entries[sid])
            true = sorted(name for name, value in flags.items() if value)
            if true:
                flagged[sid] = f"no concept article ({', '.join(true)})"
    ids = [s for s in ids if s not in flagged]
    return ([s for s in ids if inputs.rendered(manifest.entries[s])],
            [s for s in ids if not inputs.rendered(manifest.entries[s])], flagged)


def undetermined_biographies(manifest: Manifest, source_ids: Iterable[str]) -> list[str]:
    """The planned sources whose page nobody asked Wikidata about (``biography`` null)."""
    return [s for s in source_ids
            if websource.effective_flags(manifest.entries[s]).get(vocab.PAGE_FLAG_BIOGRAPHY)
            is None]


def _priority(entry: dict, *, hot: bool = False) -> str:
    importance = str((entry.get("assessment") or {}).get("importance") or "").lower()
    return tend_priority("high" if hot or importance in _HIGH_IMPORTANCE else "default")


# --------------------------------------------------------------------------
# the three task sets
# --------------------------------------------------------------------------

def inventory_tasks(paths: WorkspacePaths, manifest: Manifest,
                    source_ids: list[str]) -> list[TaskSpec]:
    """One task per source, the deterministic finders' passages in its manifest.

    The rows are ``finders.json``'s, one per (finder, start), as the gate's rule 8
    reads them; the worker writes one passage per START (two finders may share
    one), which is what the summary counts.
    """
    tasks = []
    for sid in source_ids:
        entry = manifest.entries[sid]
        inputs.check_source(paths, manifest.sourceset_id, entry)
        view = inputs.source_view(paths, manifest.sourceset_id, sid)
        finders = sorted(view.finders(), key=lambda f: (int(f.get("start", 0)),
                                                        str(f.get("finder"))))
        for f in finders:
            if f.get("finder") not in vocab.DETERMINISTIC_FINDERS:
                raise CfError(f"{sid}: finders.json names finder {f.get('finder')!r}, which is "
                              "not a deterministic finder")
        starts = len({int(f.get("start", 0)) for f in finders})
        doc = {**_document_pin(entry), "finders": finders}
        tasks.append(TaskSpec(id=f"inv-{sid}", mode=vocab.LOCATE_INVENTORY,
                              title=f"inventory: {sid}", priority=_priority(entry),
                              summary=f"inventory of {sid} ({starts} finder passage(s))",
                              documents=[doc]))
    return tasks


def text_span(segments: list[list[int]], span: list[int] | None) -> list[int] | None:
    """A layer span's reading-copy span (``text.txt`` offsets), through ``layer_map.json``.

    The first segment ending after the start and the last starting before the end
    bound it; a formula maps as a whole.  None when the span maps to nothing.
    """
    if not span:
        return None
    a, b = int(span[0]), int(span[1])
    start = end = None
    for t0, t1, l0, l1, kind in segments:
        if l1 <= a or l0 >= b:
            continue
        if start is None:
            start = t0 + max(0, a - l0) if kind == render_html.SEGMENT_TEXT else t0
        end = t0 + (min(b, l1) - l0) if kind == render_html.SEGMENT_TEXT else t1
    return [start, end] if start is not None and end is not None else None


class _TextSpans:
    """Reading-copy spans for many passages, each source's layer map read once."""

    def __init__(self, paths: WorkspacePaths, sourceset_id: str):
        self.paths, self.sourceset_id = paths, sourceset_id
        self.maps: dict[str, list[list[int]]] = {}

    def __call__(self, source_id: str, span: list[int] | None) -> list[int] | None:
        if source_id not in self.maps:
            self.maps[source_id] = inputs.source_view(self.paths, self.sourceset_id,
                                                      source_id).layer_map()
        return text_span(self.maps[source_id], span)


def frozen_inventory(paths: WorkspacePaths, inventory_run: str, manifest: Manifest
                     ) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """``({source_id: [formal_math passages by span]}, {source_id: why it has none})``.

    Every source the inventory covered at this rendering is a key, one whose
    passages were all scoped out with ``[]``: the plan records it as skipped and
    the outcomes still carry its scoped-out passages.  Each passage gains its
    reading-copy ``text_span``.
    """
    frozen = outcomes_mod.inventory(paths, inventory_run, require_gate=True)
    preferred = set(manifest.preferred().values())
    spans = _TextSpans(paths, manifest.sourceset_id)
    by_source: dict[str, list[dict]] = {}
    inventoried: set[str] = set()
    left_out: dict[str, str] = {sid: f"unresolved inventory ({u.get('reason', '')})"
                                for sid, u in frozen["unresolved"].items()}
    for (sid, _item), passage in sorted(frozen["passages"].items()):
        entry = manifest.entries.get(sid)
        if entry is None:
            left_out[sid] = "not in the pinned sourceset manifest"
            continue
        if sid not in preferred:
            left_out[sid] = (f"page {entry['pageid']} plans "
                             f"{manifest.preferred()[entry['pageid']]} in this version")
            continue
        if passage.get("source_sha") != entry.get("html_sha256") or \
                passage.get("layer_sha256") != entry.get("layer_sha256"):
            left_out[sid] = "the inventory was made against another rendering of the source"
            continue
        inventoried.add(sid)
        if passage.get("scope") not in vocab.LOCATED_SCOPES:
            continue
        by_source.setdefault(sid, []).append({**passage,
                                              "text_span": spans(sid, passage.get("span"))})
    for sid in sorted(inventoried - set(left_out)):
        by_source.setdefault(sid, [])
    for sid in by_source:
        by_source[sid].sort(key=lambda p: (int((p.get("span") or [0])[0]), p["item"]))
        left_out.pop(sid, None)
    return by_source, left_out


def _passage_row(p: dict) -> dict:
    """A frozen passage as the worker finds it: its id and layer span, and where to read
    it -- the reading-copy ``text_span``, the ``section`` and the inventory's ``quote``
    (already capped by the inventory's gate; the layer holds it anyway)."""
    return {"item": p["item"], "span": list(p["span"]), "head_words": p.get("head_words"),
            "prominence": p.get("prominence"), "passage_kind": p.get("passage_kind"),
            "text_span": p.get("text_span"), "section": p.get("section"),
            "quote": p.get("quote")}


def _curated_rows(curated: dict[str, list[dict]] | None, sid: str) -> list[dict]:
    return [{"library": str(h["library"]), "declaration": str(h["declaration"])}
            for h in (curated or {}).get(sid) or []]


def _curated_chunk(chunks: list[list[dict]]) -> int:
    """The one chunk of a source that carries its curated hits: the subject's, else the first."""
    for k, chunk in enumerate(chunks):
        if any(p.get("prominence") == vocab.PROMINENCE_SUBJECT for p in chunk):
            return k
    return 0


def locate_tasks(manifest: Manifest, frozen: dict[str, list[dict]],
                 source_ids: list[str], *, per_task: int, subject_only: bool,
                 curated: dict[str, list[dict]] | None = None
                 ) -> tuple[list[TaskSpec], dict[str, str]]:
    """Tasks of at most ``per_task`` passages; ``subject_only`` bundles one per article.

    Each document carries ``curated: [{library, declaration}]``, the curated hits
    of its source's candidates file that THIS task answers for: all of them go to
    the one task holding the source's subject passage (the first of its tasks
    when it has none), and every other task of the source gets ``[]`` -- so a
    source split over several tasks owes each hit once, not once per task.
    """
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
            docs = [{**_document_pin(manifest.entries[sid]), "passages": [_passage_row(p)],
                     "curated": _curated_rows(curated, sid)}
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
        chunks = [passages[k:k + per_task] for k in range(0, len(passages), per_task)]
        owner = _curated_chunk(chunks)
        for k, chunk in enumerate(chunks):
            hot = any(p.get("prominence") in vocab.REFUTE_BY_DEFAULT_PROMINENCE for p in chunk)
            number = k + 1
            tasks.append(TaskSpec(id=f"loc-{sid}-{number}", mode=vocab.LOCATE,
                                  title=f"locate: {sid} part {number}",
                                  priority=_priority(entry, hot=hot),
                                  summary=f"{len(chunk)} passage(s) of {sid}",
                                  documents=[{**_document_pin(entry),
                                              "passages": [_passage_row(p) for p in chunk],
                                              "curated": (_curated_rows(curated, sid)
                                                          if k == owner else [])}]))
    return tasks, skipped


def challenge_tasks(manifest: Manifest, built: dict,
                    paths: WorkspacePaths | None = None) -> list[TaskSpec]:
    """One task per batch of the challenge plan, its records grouped by source."""
    tasks = []
    spans = _TextSpans(paths, manifest.sourceset_id) if paths is not None else None
    keep = ("key", "target", "item", "span", "text_span", "section", "quote", "location_key",
            "library", "declaration", "relation", "rank", "outcome", "prominence", "scope",
            "passage_kind", "run_id", "parts", "note")
    for number, batch in enumerate(built["batches"], 1):
        docs: list[dict] = []
        for rec in batch:
            entry = manifest.entries.get(rec["source_id"])
            if not inputs.rendered(entry):
                raise CfError(f"challenged source {rec['source_id']} is not a rendered source of "
                              f"sourceset {manifest.sourceset_id} version {manifest.version}")
            if not docs or docs[-1]["source_id"] != rec["source_id"]:
                docs.append({**_document_pin(entry), "records": []})
            if spans is not None:
                rec = {**rec, "text_span": spans(rec["source_id"], rec.get("span"))}
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


def check_task_dir(tasks: list[TaskSpec], out_dir: Path) -> None:
    """Refuse, before anything is written, a task directory holding another plan's tasks."""
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise CfError("two planned tasks share an id")
    stale = ({p.name for p in out_dir.glob("*.yaml")} - {f"{i}.yaml" for i in ids}
             if out_dir.is_dir() else set())
    if stale:
        raise CfError(f"{len(stale)} stale task file(s) in {out_dir}: {sorted(stale)[:5]}",
                      hint="this run directory was planned from other inputs; use a new run id")


def write_tasks(tasks: list[TaskSpec], out_dir: Path, *, layout: dict,
                max_open_tasks: int | None) -> list[Path]:
    """``tasks/<id>.yaml`` for each task; refuses stale task files of another plan."""
    if max_open_tasks is not None and max_open_tasks < 1:
        raise CfError(f"--max-open-tasks must be at least 1, not {max_open_tasks}")
    check_task_dir(tasks, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = [t.id for t in tasks]
    written = []
    for i, spec in enumerate(tasks):
        depends = [ids[i - max_open_tasks]] if max_open_tasks and i >= max_open_tasks else []
        path = out_dir / f"{spec.id}.yaml"
        text = task_yaml(spec, layout=layout, depends_on=depends)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def vendor_shas() -> dict[str, str]:
    """sha256 of each vendored package's manifest: what the gate verifies at start."""
    vendor = skill_root() / "vendor"
    return {name: hashing.sha256_file(vendor / f"{name}.manifest.json")
            for name in ("literature_links", "formal_companion_annotations")}


def gate_config(*, preset_mode: str, preset_name: str, run_id: str, layout: dict,
                universe: inputs.Universe, corpus_db_sha256: str, tools_db: dict,
                manifest: Manifest, source_ids: list[str], blind: bool,
                inventory_run: str | None, challenged_runs: list[str]) -> dict:
    """``gate.json`` of references/locate-documents.md section 9, for one layout."""
    for sid in source_ids:                # another renderer's source is pinned nowhere
        inputs.check_renderer(manifest.entries[sid], manifest.sourceset_id)
    return {
        "schema": GATE_SCHEMA,
        "mode": preset_mode,
        "preset": preset_name,
        "run_id": run_id,
        "kinds": sorted(vocab.kinds_for_mode(preset_mode)),
        "universe": {"id": universe.id, "sha256": universe.sha256,
                     "libraries": universe.gate_libraries()},
        "corpus_db": {"path": layout["corpus_db"], "sha256": corpus_db_sha256},
        "tools_corpus_db": {"path": layout["tools_corpus_db"], "sha256": tools_db["sha256"],
                            "kind": tools_db["kind"]},
        "sourceset": {**manifest.pin(), "sources_dir": layout["sources_dir"]},
        "sources": {sid: {"html_sha256": manifest.entries[sid]["html_sha256"],
                          "layer_sha256": manifest.entries[sid]["layer_sha256"],
                          "renderer": manifest.entries[sid]["renderer"]}
                    for sid in sorted(source_ids)},
        "candidates_dir": layout["candidates_dir"],
        "blind": bool(blind),
        "inventory": {"run_id": inventory_run} if inventory_run else None,
        "challenged_runs": list(challenged_runs),
        "findings_dir": "findings",
        "tasks_dir": layout["tasks_dir"],
        "log_dir": layout["log_dir"],
        "orchestrator_db": layout["orchestrator_db"],
        "max_locations": vocab.MAX_LOCATIONS_PER_PASSAGE,
        "min_absence_queries": vocab.MIN_ABSENCE_QUERIES,
        "vendor": vendor_shas(),
    }


#: ``runs/<run>/planned.json``: every task manifest of the plan, kept beside run.yaml
PLANNED_FILE = "planned.json"
PLANNED_SCHEMA = "cf-locate-planned/1"


def planned_document(run_id: str, tasks: list[TaskSpec]) -> dict:
    """What the outcomes read the planned passages from (the tend root may be gone)."""
    return {"schema": PLANNED_SCHEMA, "run_id": run_id,
            "tasks": [t.manifest(run_id) for t in tasks]}


def check_root_size(tasks: list[TaskSpec], mode: str = vocab.LOCATE) -> None:
    if len(tasks) > vocab.MAX_TASKS_PER_ROOT:
        hint = ("split the challenged runs over several plans (fewer --challenge-of each, "
                "each its own --run-id), or lower --sample-rate"
                if mode == vocab.LOCATE_CHALLENGE else
                "plan the sources in campaign shards of at most "
                f"{vocab.MAX_TASKS_PER_ROOT} tasks each (references/locate-documents.md "
                "section 11): name each shard's sources with --source and give each its own "
                "--run-id")
        raise CfError(
            f"{len(tasks)} tasks is over the {vocab.MAX_TASKS_PER_ROOT} one tend root may hold",
            hint=hint)
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
